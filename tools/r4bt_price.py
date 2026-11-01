"""r4bt_price: the PRICER of the arena book r4-b-T (`r4bt-a1`; DEC-031 Amendment 25 DRAFT items 24 and 25, PR #1187). PAPER ONLY. No key, no network, no RPC.

It replays r4-b-T's hash-chained decision log (`entry`, `exit` and `gap` rows of the arm `r4b-exp027`, the frozen host's rows) against the Amendment 23 writer's DERIVED
tape copy through `tools.arena_paper.ArenaEngine`, and writes the book's ledger in its OWN root. It is the only process that joins r4-b-T's decisions to prices.
It prints COUNTS ONLY (no fill, guard, exit state, proceeds or P&L); the read is `tools/r4bt_read.py`.

THE STACK (Amendment 25 item 24, Amendment 2 item 8). `StackConfig(rent=0, e1_floor_per_005=384022, strand_rule="both", gap_slots=150, gap_silence_ms=60000,
k_rounding=<required>, feed_delay_slots=<required>)`. k_rounding and d are quant-proof's pins (Q5): there is NO default for either, and the tool refuses without them.
Rent 0 decides; the rent-charged leg is printed by the read (Amendment 2 item 7), not here.

LATENCY RUNS FROM THE DECISION INSTANT. Each entry or exit row is applied at its `ms` (the host's write instant): the engine's call clock is the row's `ms`, the
tape rows with `t_recv_ms` at or before it are already fed (rows received strictly after it are not), and the decision slot passed to `buy()` / `sell()` is the
engine's own highest fed slot, never the row's `decision_slot` (sd(T): item 24 "Never sd(T)"). The engine maps the write instant to the tape slot clock and adds d.

EXITS. `restart` -> the engine_restart exclusion (the frozen host's restart exit, Erratum 1 item 1; DEC-031 Amendment 6 item 4). Every other reason ("rank",
"gap", the time stop) -> `sell()`. An entry with no exit row stays open (the scorer counts it as a pending or orphan attempt). Declog `gap` rows whose `data_loss` is
not false enter the engine as data_loss gaps of prog `all`; the derived copy's own `gap` rows enter through the tape. That is the item 7 union.

FEED. Hours `tape-<UTC hour>.jsonl[.zst]` of the derived copy from clock - 3 h (the guard spot, item 25) to the hour holding the last exit + 2 h. Every hour in that
range must exist and be closed; a missing hour, an unparseable line or a broken decision-log chain REFUSES (exit 2 / 3). Rows are fed in file order; a decision row is
applied just before the first tape row received after it. Opened prints of idle bonding curves are dropped from memory after each hour (r4-b trades PumpSwap pools
only; the drop changes no PumpSwap price; `--keep-bonding` turns it off); `trim_history` runs each hour (the engine's own, price-neutral by its contract).

REFUSALS, all before the first ledger row (exit 2): EXP-027's read not shown written (`tools/r4bt_gate.exp027_read_written`, item 2 / 4); a closed root among the
inputs or outputs; an engine or scorer sha256 that differs from the pins pasted from the registration record; the process not running a CLEAN checkout at the
registered `frozen_sha` (the scorer would refuse the rows: Amendment 25 item 13, W route (2)); an unregistered or mis-registered `r4bt-a1` (stake 0.25, no models);
a boundary file that is not `r4bt_attempts count` output with status "boundary", chain verified, for this clock; a ledger root that already holds this book.
The pricer prices the entries decided before the boundary (and the exits of those entries, wherever they fall); the attempt set is fixed by the boundary, outcome-blind.
The boundary file's own counts must equal the entries selected here (kept + excluded-hour) or the run refuses: the two counters cannot disagree silently.

    python -m tools.r4bt_price --declog-root D --tape-dir T --ledger-root L --registry R --boundary-file B --boundary-sha256 H --h-days 14 \\
        --exp027-record E --exp027-record-sha256 H --arena-paper-sha256 H --arena-score-sha256 H --feed-delay-slots d --k-rounding {ceil,round} [--out FILE]

Exit codes: 0 priced (counts printed); 2 refused (nothing written); 3 the inputs cannot be trusted (broken chain, bad tape line).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from tools import r4bt_gate as G
from tools.arena_declog import DecisionLog, LogChainError
from tools.arena_paper import ArenaEngine, ArenaRefused, LedgerCorrupt, StackConfig, load_registry, process_code_state
from tools.arena_zst import ZstdReader

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
GUARD_PRE_MS = 3 * HOUR_MS  # the derived copy is read from clock - 3 h (Amendment 25 item 25)
EXIT_POST_MS = 2 * HOUR_MS  # to the last exit + 2 h
DECIDING_STAKE = 0.25
PIN_KEYS = ("tools/arena_paper.py", "tools/arena_score.py")
TAPE_RE = re.compile(r"^tape-(\d{4}-\d\d-\d\dT\d\d)\.jsonl(\.zst)?$")
K_ROUNDINGS = ("ceil", "round")


class Untrusted(RuntimeError):
    """The inputs cannot be trusted (broken chain, unparseable tape line): nothing is priced."""


def pinned_config(*, feed_delay_slots: int, k_rounding: str) -> StackConfig:
    """Amendment 2 item 8's pin for r4-b-T. d and k_rounding are quant-proof's (Q5): required, no default."""
    if not isinstance(feed_delay_slots, int) or isinstance(feed_delay_slots, bool) or feed_delay_slots < 0:
        raise G.Refused("--feed-delay-slots must be a whole number of slots >= 0 (quant-proof's pin, Q5)")
    if k_rounding not in K_ROUNDINGS:
        raise G.Refused(f"--k-rounding must be one of {K_ROUNDINGS} (quant-proof's pin, Q5)")
    return StackConfig(rent=0, e1_floor_per_005=384_022, strand_rule="both", k_rounding=k_rounding, feed_delay_slots=feed_delay_slots, gap_slots=150, gap_silence_ms=60_000)


def hour_ms(name: str) -> int:
    return G.utc_ms(name + ":00:00Z")


def hour_name(ms: int) -> str:
    return G.iso(ms)[:13]


# ------------------------------------------------------------------------------------------------------------------------------ tape
def tape_hours(tape_dir: Path, lo_ms: int, hi_ms: int, now_ms: int) -> list[Path]:
    """The files of every hour in [floor(lo), floor(hi)], oldest first (`.jsonl` preferred over `.zst`, like tools.arena_state). A missing hour, or an hour that is not
    closed at now_ms, is a refusal."""
    have: dict[str, Path] = {}
    for p in sorted(tape_dir.iterdir()):
        m = TAPE_RE.match(p.name)
        if m and (m.group(1) not in have or not p.name.endswith(".zst")):
            have[m.group(1)] = p
    out, missing = [], []
    h = lo_ms // HOUR_MS * HOUR_MS
    last = hi_ms // HOUR_MS * HOUR_MS
    if last + HOUR_MS > now_ms:
        raise G.Refused(f"the tape hour {hour_name(last)} is not closed at {G.iso(now_ms)}: the pricer waits for the last exit + 2 h")
    while h <= last:
        p = have.get(hour_name(h))
        (out.append(p) if p else missing.append(hour_name(h)))
        h += HOUR_MS
    if missing:
        raise G.Refused(f"the derived copy lacks {len(missing)} hour(s), first {missing[0]}: it must be continuous from clock - 3 h to the last exit + 2 h (Amendment 25 item 25)")
    return out


def iter_lines(path: Path) -> Iterator[bytes]:
    if path.name.endswith(".zst"):
        rd = ZstdReader(path)
        buf = b""
        try:
            while True:
                chunk = rd.read()
                if not chunk:
                    break
                buf += chunk
                *lines, buf = buf.split(b"\n")
                yield from lines
        finally:
            rd.close()
        if buf:
            yield buf
    else:
        with open(path, "rb") as fh:
            yield from fh.read().split(b"\n")


def iter_rows(path: Path) -> Iterator[dict]:
    for n, line in enumerate(iter_lines(path), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise Untrusted(f"unparseable tape line {n} in {path.name}") from None
        if not isinstance(row, dict):
            raise Untrusted(f"tape line {n} in {path.name} is not an object")
        yield row


# ------------------------------------------------------------------------------------------------------------------------------ decision log
def load_declog(root: Path, arm: str) -> list[dict]:
    log = DecisionLog(root)
    try:
        log.verify(arm)
    except LogChainError as e:
        raise Untrusted(f"decision log chain: {e}") from None
    return log.rows(arm)


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def select_entries(rows: Sequence[Mapping], clock_ms: int, h_days: int, until_ms: int) -> tuple[list[dict], dict]:
    """The entry rows r4bt_attempts counts, before the boundary: ms in [clock, min(clock + H, until)), t_mark an int at or after the clock in seconds."""
    end = min(clock_ms + h_days * DAY_MS, until_ms)
    out, skipped = [], Counter()
    for r in rows:
        if r.get("kind") != "entry":
            continue
        ms = r.get("ms")
        if not _is_int(ms):
            raise Untrusted("an entry row without an int ms")
        if ms < clock_ms:
            skipped["before_clock"] += 1
        elif ms >= end:
            skipped["at_or_after_boundary_or_window"] += 1
        elif not _is_int(r.get("t_mark")) or r["t_mark"] < clock_ms // 1000:
            skipped["mark_before_clock"] += 1
        else:
            out.append(dict(r))
    return out, dict(skipped)


def gap_rows(rows: Sequence[Mapping]) -> list[dict]:
    """Declog `gap` rows that are data loss (data_loss not false: None, a reject, counts as loss) as engine gap rows of prog `all` (item 7 (a))."""
    out = []
    for r in rows:
        if r.get("kind") != "gap" or r.get("data_loss") is False:
            continue
        ms, frm, to = r.get("ms"), r.get("from_ms"), r.get("to_ms")
        if not _is_int(ms):
            raise Untrusted("a gap row without an int ms")
        at = to if _is_int(to) else (r["instant_ms"] if _is_int(r.get("instant_ms")) else ms)
        silent = max(0, at - frm) if _is_int(frm) else 0
        out.append({"_ms": ms, "_seq": r.get("seq", 0), "row": {"type": "gap", "kind": str(r.get("gap_kind") or "declog_gap"), "data_loss": True, "prog": "all",
                                                              "t_recv_ms": at, "silent_ms": silent}})
    return out


# ------------------------------------------------------------------------------------------------------------------------------ boundary file
def load_boundary(path: str | Path, sha256: str, clock_ms: int, arm: str) -> dict:
    p = G.check_path(path, "the boundary file")
    if not G.SHA_RE.match(sha256 or ""):
        raise G.Refused("--boundary-sha256 is not a lowercase sha256")
    if G.sha256_file(p) != sha256:
        raise G.Refused("the boundary file sha256 differs from the pinned one")
    try:
        b = json.loads(p.read_bytes())
    except ValueError:
        raise G.Refused("the boundary file is not JSON") from None
    if b.get("tool") != "r4bt_attempts count" or b.get("arm") != arm or b.get("clock_start_ms") != clock_ms:
        raise G.Refused("the boundary file is not `r4bt_attempts count` output for this arm and clock")
    if b.get("status") != "boundary" or not _is_int(b.get("boundary_ms")) or b["boundary_ms"] % DAY_MS:
        raise G.Refused(f"the boundary file has status {b.get('status')!r}, not a 00:00Z boundary: nothing is priced")
    if b.get("chain_verified") is not True:
        raise G.Refused("the boundary file's hash chain was not verified (chain_verified is not true)")
    return b


# ------------------------------------------------------------------------------------------------------------------------------ the run
def price(*, declog_root: str | Path, tape_dir: str | Path, ledger_root: str | Path, registry: str | Path, boundary_file: str | Path, boundary_sha256: str,
          h_days: int, exp027_record: str | None, exp027_record_sha256: str | None, pins: Mapping[str, str], feed_delay_slots: int, k_rounding: str,
          arm: str = G.ARM, bot_id: str = G.BOT_ID, now_ms: int | None = None, code_state_fn: Callable[[], tuple[str | None, bool | None]] | None = None,
          repo: str | Path | None = None, keep_bonding: bool = False) -> dict:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    repo = Path(repo) if repo else Path(__file__).resolve().parent.parent
    # --- refusals, before anything is opened beyond the gate files
    G.exp027_read_written(exp027_record, exp027_record_sha256, now)
    droot = G.check_path(declog_root, "the declog root")
    troot = G.check_path(tape_dir, "the tape dir")
    lroot = G.check_path(ledger_root, "the ledger root")
    G.check_path(registry, "the registry")
    if set(pins) != set(PIN_KEYS):
        raise G.Refused(f"pins must name exactly {PIN_KEYS}")
    pin_hashes = G.check_pins(repo, pins)
    cfg = pinned_config(feed_delay_slots=feed_delay_slots, k_rounding=k_rounding)
    reg = load_registry(registry)
    e = reg.get(bot_id)
    if e is None:
        raise G.Refused(f"{bot_id} is not registered")
    if e.get("deciding_stake") != DECIDING_STAKE or e.get("model_ids") != [] or e.get("stopped_at_ms") is not None:
        raise G.Refused(f"{bot_id}: the registry line must carry deciding_stake 0.25, model_ids [] and no stop (Amendment 25 item 13)")
    clock_ms = int(e["clock_start_ms"])
    csf = code_state_fn or (lambda: process_code_state(repo))
    head, dirty = csf()
    if head != e["frozen_sha"] or dirty is not False:
        raise G.Refused(f"the process runs code {head} (dirty {dirty}), not a clean checkout at the registered frozen_sha {e['frozen_sha']}: the scorer would refuse every row")
    if (lroot / bot_id).exists() and any((lroot / bot_id).iterdir()):
        raise G.Refused(f"{lroot / bot_id} already holds a ledger for {bot_id}: one pricer run per ledger root (the ledger is write-once)")
    b = load_boundary(boundary_file, boundary_sha256, clock_ms, arm)
    boundary_ms = int(b["boundary_ms"])
    rows = load_declog(droot, arm)
    entries, skipped = select_entries(rows, clock_ms, h_days, boundary_ms)
    expect = int(b.get("attempts_before_boundary", 0)) + int(b.get("excluded_hour_before_boundary", 0))
    if len(entries) != expect:
        raise G.Refused(f"entries before the boundary here ({len(entries)}) != the boundary file's counted + excluded-hour attempts ({expect}): the two counters disagree")
    orders = {r.get("order_id") for r in entries}
    exits = [dict(r) for r in rows if r.get("kind") == "exit" and r.get("order_id") in orders]
    last_ms = max([r["ms"] for r in entries + exits] + [clock_ms])
    lo_ms = clock_ms - GUARD_PRE_MS
    files = tape_hours(troot, lo_ms, last_ms + EXIT_POST_MS, now)
    events = sorted([{"_ms": r["ms"], "_seq": r.get("seq", 0), "k": "entry", "row": r} for r in entries]
                    + [{"_ms": r["ms"], "_seq": r.get("seq", 0), "k": "exit", "row": r} for r in exits]
                    + [{"_ms": g["_ms"], "_seq": g["_seq"], "k": "gap", "row": g["row"]} for g in gap_rows(rows)], key=lambda x: (x["_ms"], x["_seq"]))
    # --- the engine
    clock = [lo_ms]
    eng = ArenaEngine(ledger_root=str(lroot), registry_path=str(registry), cfg=cfg, sps_fn=None, now_fn=lambda: clock[0], code_state_fn=csf)
    eng.start_record(bot_id)
    by_order: dict[str, str] = {}
    c: Counter = Counter()

    def apply(ev: Mapping[str, Any]) -> None:
        r = ev["row"]
        clock[0] = ev["_ms"]  # the call clock IS the decision instant
        if ev["k"] == "gap":
            eng.on_gap(r)
            c["declog_gap_rows_fed"] += 1
        elif ev["k"] == "entry":
            pool = r.get("pool") or (r.get("meta") or {}).get("mint")
            try:
                by_order[r["order_id"]] = eng.buy(bot_id, pool, r.get("stake_sol"), r["ms"], eng.hw_slot,
                                                  {"order_id": r["order_id"], "t_mark": r.get("t_mark"), "rank": r.get("rank"), "declog_seq": r.get("seq")})
                c["buys_open"] += 1
            except ArenaRefused as exc:
                c["buy_refused:" + str(exc).split(":")[0]] += 1
        else:
            pid = by_order.get(r.get("order_id"))
            if pid is None or pid not in eng.positions:
                c["exit_without_open_position"] += 1
                return
            if r.get("reason") == "restart":
                pos = eng.positions[pid]
                pos.exit_ms, pos.exit_slot, pos.xs, pos.sps_exit, pos.reason = r["ms"], eng.hw_slot, eng.hw_slot, pos.sps_entry, "restart"
                pos.exit_stamp_ms, pos.exit_call_ms = r["ms"], r["ms"]
                eng._write_excluded(pos, "engine_restart", None)
                del eng.positions[pid]
                c["restart_exclusions"] += 1
                return
            try:
                eng.sell(bot_id, pid, 1.0, r["ms"], eng.hw_slot, str(r.get("reason") or ""))
                c["sells_decided"] += 1
            except ArenaRefused as exc:
                c["sell_refused:" + str(exc).split(":")[0][:40]] += 1

    ev = list(events)
    i = 0
    try:
        for f in files:
            for row in iter_rows(f):
                t = row.get("t_recv_ms")
                while i < len(ev) and _is_int(t) and ev[i]["_ms"] < t:
                    apply(ev[i])
                    i += 1
                if _is_int(t):
                    clock[0] = max(clock[0], t)
                eng.on_row(row)
                c["tape_rows"] += 1
            eng.trim_history()
            if not keep_bonding:
                busy = {p.pool for p in eng.positions.values()}
                for k in [k for k, h in eng.pools.items() if h.venue == "pump_bonding" and k not in busy]:
                    del eng.pools[k]
        while i < len(ev):
            apply(ev[i])
            i += 1
        eng.flush()
    except LedgerCorrupt as exc:
        raise Untrusted(f"ledger write failed: {exc}") from None
    led = eng.ledger.read_bot(bot_id)
    kinds = Counter(r["kind"] for r in led)
    out = {
        "tool": "r4bt_price", "bot_id": bot_id, "arm": arm, "clock_start_ms": clock_ms, "boundary_ms": boundary_ms, "boundary_file_sha256": boundary_sha256,
        "entries_selected": len(entries), "entries_not_selected": skipped, "exits_in_scope": len(exits), "tape_hours": len(files), "tape_first_hour": hour_name(lo_ms),
        "feed_delay_slots": feed_delay_slots, "k_rounding": k_rounding, "stack": cfg.as_dict(), "pins": pin_hashes, "frozen_sha": e["frozen_sha"],
        "open_positions_left": len(eng.positions), "ledger_rows_by_kind": dict(kinds), "counts": dict(sorted(c.items())),
        "engine_counters": dict(sorted(eng.counters.items())), "engine_id": eng.engine_id,
    }
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="r4-b-T pricer: counts only.")
    for a in ("declog-root", "tape-dir", "ledger-root", "registry", "boundary-file", "boundary-sha256", "arena-paper-sha256", "arena-score-sha256"):
        ap.add_argument("--" + a, required=True)
    ap.add_argument("--exp027-record", default=None)
    ap.add_argument("--exp027-record-sha256", default=None)
    ap.add_argument("--h-days", type=int, required=True)
    ap.add_argument("--feed-delay-slots", type=int, default=None, help="quant-proof's pin (Q5); no default")
    ap.add_argument("--k-rounding", default=None, help="quant-proof's pin (Q5); no default")
    ap.add_argument("--keep-bonding", action="store_true")
    ap.add_argument("--out", default=None, help="also write the counts JSON here (refused if it exists)")
    return ap


def main(argv: Sequence[str] | None = None, *, now_ms: int | None = None, code_state_fn: Callable[[], tuple[str | None, bool | None]] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if a.feed_delay_slots is None or a.k_rounding is None:
            raise G.Refused("--feed-delay-slots and --k-rounding are required (quant-proof's pins, Q5)")
        res = price(declog_root=a.declog_root, tape_dir=a.tape_dir, ledger_root=a.ledger_root, registry=a.registry, boundary_file=a.boundary_file,
                    boundary_sha256=a.boundary_sha256, h_days=a.h_days, exp027_record=a.exp027_record, exp027_record_sha256=a.exp027_record_sha256,
                    pins={"tools/arena_paper.py": a.arena_paper_sha256, "tools/arena_score.py": a.arena_score_sha256}, feed_delay_slots=a.feed_delay_slots,
                    k_rounding=a.k_rounding, now_ms=now_ms, code_state_fn=code_state_fn, keep_bonding=a.keep_bonding)
    except G.Refused as e:
        print(f"r4bt_price: REFUSED: {e}", file=sys.stderr)
        return 2
    except Untrusted as e:
        print(f"r4bt_price: UNTRUSTED: {e}", file=sys.stderr)
        return 3
    text = json.dumps(res, sort_keys=True)
    if a.out:
        p = Path(a.out)
        if p.exists():
            print(f"r4bt_price: REFUSED: {p} exists", file=sys.stderr)
            return 2
        p.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
