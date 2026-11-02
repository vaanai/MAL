"""r4bt_price: the PRICER of the arena book r4-b-T (`r4bt-a1`; the r4-b-T DEC-031 amendment, PR #1187, number filled at merge; QP-RULING-1102-r4bt). PAPER ONLY.
No key, no network, no RPC.

It replays r4-b-T's hash-chained decision log (`entry` and `exit` rows of the arm `r4b-exp027`, the frozen host's rows) against the writer's DERIVED tape copy through
`tools.arena_paper.ArenaEngine`, and writes the book's ledger in its OWN root. It is the only process that joins r4-b-T's decisions to prices. It prints COUNTS ONLY
(no fill, guard, exit state, proceeds or P&L); the read is `tools/r4bt_read.py`.

THE STACK (Amendment 2 item 8). `StackConfig(rent=0, e1_floor_per_005=384022, strand_rule="both", gap_slots=150, gap_silence_ms=60000, k_rounding=<required>,
feed_delay_slots=<required>)`. QP-RULING-1102-r4bt Q5 pins k_rounding "ceil" and d from the follower's sampler; neither has a default here. Rent 0 decides; the read
prints the rent-charged leg.

LATENCY RUNS FROM THE DECISION INSTANT. Each entry or exit row is applied at its `ms`: the engine's call clock is the row's `ms`, every tape row with `t_recv_ms` at or
before it is already fed (rows received strictly after it are not; a test pins the order), and the decision slot passed to `buy()` / `sell()` is the engine's own highest
fed slot, never the row's `decision_slot` (sd(T)). No writer or transport lag is added (Q5 item 3).

GAP SOURCE (Q1-a). The section 1.4 span exclusions come from the PRICE PATH only: the derived copy's data_loss `gap` rows (fed through the tape), the engine's own
detection, a needed print without V, the host's `restart` exits (engine_restart), and the A3 stop instant (`a3_halt`: a trade whose decision-to-exit span contains the
registry's stop instant). Declog `gap` rows are NOT fed to the engine; they are COUNTED by `gap_kind`, with the derived copy's data_loss rows by prog:kind and the positions
open across each `incomplete_inputs` row, all report-only. There is no price-path gap-fill for this book (Q1-b).

EXITS. `restart` -> the engine_restart exclusion. Every other reason -> `sell()`. The pricer REFUSES unless every counted entry has an exit row (an entry without one would
be a restart orphan and make the read a final NOT_DECIDABLE): wait for the last exit.

CHECKS BEFORE THE FIRST LEDGER ROW (exit 2; exit 3 for untrusted inputs):
  * EXP-027's read shown written, and for route b the A2 C4 and C5 commits merged (`tools/r4bt_gate.exp027_read_written`);
  * no input under a closed root; the registry is r4-b-T's OWN file outside /data/mal/arena;
  * the engine and scorer sha256 equal the pins pasted from the registration record; the process runs a CLEAN checkout at the registered frozen_sha;
  * the registry line is r4bt-a1's (deciding stake 0.25, no models); stopped before the boundary refuses (A3 halt: the read is NOT_DECIDABLE);
  * the boundary file is `r4bt_attempts count` output with status "boundary", chain verified, for this clock; its `from_ms` (a confirmation file) selects entries in
    [from_ms, boundary); the entries selected here must equal its counted + excluded-hour attempts;
  * the declog: hash chain verified, EVERY closed day file's sha256 equals the pinned day-sha list (`r4bt_attempts day-sha` outputs in one file pinned by sha256; a
    missing or mismatched closed day refuses: the pricer prices decisions written live and never regenerates them), every `start` row carries the registered
    frozen_sha, every selected entry has a string `pool` (no fallback to meta.mint: Untrusted) and an exit row;
  * a ledger root that already holds this book (one pricer run per ledger; a run that fails before its ledger is opened may be re-run once into a fresh root).

V AUDIT (Q4). The priced trades whose entry or exit landing print (1.9 s leg) has V outside [17.5e9, 17.7e9] or a quote mint other than WSOL are COUNTED (`v_audit_n`);
their position ids go to a sidecar (`--v-audit-ids-out`); the read applies the rent-charged leg to them.

    python -m tools.r4bt_price --declog-root D --tape-dir T --ledger-root L --registry R --boundary-file B --boundary-sha256 H --day-sha-file F --day-sha-file-sha256 H \\
        --h-days 14 --exp027-record E --exp027-record-sha256 H --arena-paper-sha256 H --arena-score-sha256 H --feed-delay-slots d --k-rounding ceil \\
        [--v-audit-ids-out FILE] [--out FILE]

Exit codes: 0 priced (counts printed); 2 refused (nothing written); 3 the inputs cannot be trusted (broken chain, bad tape line, entry without a string pool).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import bisect
import hashlib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from tools import arena_stack as AST
from tools import r4bt_gate as G
from tools.arena_declog import DecisionLog, LogChainError
from tools.arena_paper import ArenaEngine, ArenaRefused, LedgerCorrupt, StackConfig, load_registry, process_code_state
from tools.arena_zst import ZstdReader

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
GUARD_PRE_MS = 3 * HOUR_MS  # the derived copy is read from clock - 3 h (the guard spot)
EXIT_POST_MS = 2 * HOUR_MS  # to the last exit + 2 h
DECIDING_STAKE = 0.25
PIN_KEYS = ("tools/arena_paper.py", "tools/arena_score.py")
TAPE_RE = re.compile(r"^tape-(\d{4}-\d\d-\d\dT\d\d)\.jsonl(\.zst)?$")
K_ROUNDINGS = ("ceil", "round")
V_LO, V_HI = 17.5e9, 17.7e9
WSOL = "So11111111111111111111111111111111111111112"
LAT = "1p9"


class Untrusted(RuntimeError):
    """The inputs cannot be trusted (broken chain, unparseable tape line, an entry without a string pool): nothing is priced."""


def pinned_config(*, feed_delay_slots: int, k_rounding: str) -> StackConfig:
    """Amendment 2 item 8's pin. d and k_rounding are quant-proof's (Q5): required, no default."""
    if not isinstance(feed_delay_slots, int) or isinstance(feed_delay_slots, bool) or feed_delay_slots < 0:
        raise G.Refused("--feed-delay-slots must be a whole number of slots >= 0 (quant-proof's pin, Q5)")
    if k_rounding not in K_ROUNDINGS:
        raise G.Refused(f"--k-rounding must be one of {K_ROUNDINGS} (quant-proof's pin, Q5: ceil)")
    return StackConfig(rent=0, e1_floor_per_005=384_022, strand_rule="both", k_rounding=k_rounding, feed_delay_slots=feed_delay_slots, gap_slots=150, gap_silence_ms=60_000)


def hour_name(ms: int) -> str:
    return G.iso(ms)[:13]


# ------------------------------------------------------------------------------------------------------------------------------ tape
def tape_hours(tape_dir: Path, lo_ms: int, hi_ms: int, now_ms: int) -> list[Path]:
    """The files of every hour in [floor(lo), floor(hi)], oldest first (`.jsonl` preferred over `.zst`). A missing hour, or an hour not closed at now_ms, refuses."""
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
        raise G.Refused(f"the derived copy lacks {len(missing)} hour(s), first {missing[0]}: it must be continuous from clock - 3 h to the last exit + 2 h")
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


def verify_day_shas(root: Path, arm: str, pinned_file: str | Path, pinned_sha256: str, now_ms: int) -> int:
    """Every CLOSED day file of the arm must be in the pinned list with an equal sha256, and every pinned day must exist (QP-RULING-1102-r4bt Q5 item 4). Returns the count."""
    p = G.check_path(pinned_file, "the day-sha file")
    if not G.SHA_RE.match(pinned_sha256 or "") or G.sha256_file(p) != pinned_sha256:
        raise G.Refused("the day-sha file sha256 differs from the pinned one")
    try:
        days = json.loads(p.read_bytes()).get("day_sha256")
    except ValueError:
        raise G.Refused("the day-sha file is not JSON") from None
    if not isinstance(days, dict) or not days or not all(G.SHA_RE.match(str(v)) for v in days.values()):
        raise G.Refused("the day-sha file needs a non-empty day_sha256 object of lowercase sha256 values")
    have = {f.stem: f for f in DecisionLog(root).files(arm)}
    for day, f in have.items():
        if G.utc_ms(day + "T00:00:00Z") + DAY_MS > now_ms:
            continue  # the open day is not pinned yet
        if day not in days:
            raise G.Refused(f"closed declog day {day} is not in the pinned day-sha list")
        if hashlib.sha256(f.read_bytes()).hexdigest() != days[day]:
            raise G.Refused(f"declog day {day}: sha256 differs from the notebooked value (a replay prices decisions as written, never altered rows)")
    missing = sorted(d for d in days if d not in have)
    if missing:
        raise G.Refused(f"pinned declog day {missing[0]} has no file ({len(missing)} missing)")
    return len(days)


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def check_start_rows(rows: Sequence[Mapping], frozen_sha: str) -> int:
    starts = [r for r in rows if r.get("kind") == "start"]
    if not starts:
        raise G.Refused("the declog has no `start` row: the decision host's code cannot be tied to the registered frozen_sha")
    bad = [r for r in starts if r.get("frozen_sha") != frozen_sha]
    if bad:
        raise G.Refused(f"{len(bad)} declog `start` row(s) carry frozen_sha {bad[0].get('frozen_sha')!r}, not the registered {frozen_sha}: the host ran other code")
    return len(starts)


def select_entries(rows: Sequence[Mapping], clock_ms: int, h_days: int, until_ms: int, from_ms: int | None = None) -> tuple[list[dict], dict]:
    """The entry rows r4bt_attempts counts: ms in [max(clock, from_ms), min(clock + H, until)), t_mark an int at or after the clock in seconds."""
    lo = max(clock_ms, from_ms or 0)
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
        elif ms < lo:
            skipped["before_from_ms"] += 1
        elif ms >= end:
            skipped["at_or_after_boundary_or_window"] += 1
        elif not _is_int(r.get("t_mark")) or r["t_mark"] < clock_ms // 1000:
            skipped["mark_before_clock"] += 1
        else:
            if not isinstance(r.get("pool"), str) or not r["pool"]:
                raise Untrusted("an entry row without a string pool (no fallback to meta.mint)")
            out.append(dict(r))
    return out, dict(skipped)


def declog_gap_counts(rows: Sequence[Mapping], selected: Sequence[Mapping], exits: Sequence[Mapping]) -> dict:
    """Report-only (Q1-a item 3): declog `gap` rows by gap_kind (kinds and counts, never spans), and the positions open across each `incomplete_inputs` row."""
    by_kind = Counter(str(r.get("gap_kind")) for r in rows if r.get("kind") == "gap")
    exit_ms = {x.get("order_id"): x["ms"] for x in exits}
    ii = [r["ms"] for r in rows if r.get("kind") == "gap" and r.get("gap_kind") == "incomplete_inputs" and _is_int(r.get("ms"))]
    across = sum(1 for m in ii for e in selected if e["ms"] <= m < exit_ms.get(e.get("order_id"), 1 << 62))
    return {"by_gap_kind": dict(sorted(by_kind.items())), "incomplete_inputs_rows": len(ii), "positions_open_across_incomplete_inputs": across}


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
    if b.get("from_ms") is not None and (not _is_int(b["from_ms"]) or b["from_ms"] < clock_ms):
        raise G.Refused("the boundary file's from_ms is not an epoch ms at or after the clock")
    return b


# ------------------------------------------------------------------------------------------------------------------------------ V audit
def first_print(prints: Sequence[tuple], slot: int):
    """The first print with slot >= `slot`; the last print when none (the engine derives its state from the last print)."""
    if not prints:
        return None
    i = bisect.bisect_left([p[0] for p in prints], slot)
    return prints[i] if i < len(prints) else prints[-1]


def v_audit(trades: Sequence[Mapping], prints: Mapping[str, list]) -> list[str]:
    """Position ids of priced trades (deciding stake) whose entry or exit landing print has V outside the band or a non-WSOL quote (QP-RULING-1102-r4bt Q4)."""
    bad = []
    for r in trades:
        pr = prints.get(r["pool"]) or []
        try:
            lag = AST.exit_lag_slots(r["stack"]["sps_exit"])
            slots = (int(r["calc"][LAT]["landing_slot"]) + 1, int(r["corrected_slot"]["exit"]) + int(lag) + 1)
        except (KeyError, TypeError, AssertionError):
            bad.append(r["position_id"])
            continue
        for s in slots:
            p = first_print(pr, s)
            if p is None or p[1] is None or not V_LO <= p[1] <= V_HI or p[2] != WSOL:
                bad.append(r["position_id"])
                break
    return sorted(set(bad))


# ------------------------------------------------------------------------------------------------------------------------------ the run
def price(*, declog_root: str | Path, tape_dir: str | Path, ledger_root: str | Path, registry: str | Path, boundary_file: str | Path, boundary_sha256: str,
          day_sha_file: str | Path, day_sha_file_sha256: str, h_days: int, exp027_record: str | None, exp027_record_sha256: str | None, pins: Mapping[str, str],
          feed_delay_slots: int, k_rounding: str, arm: str = G.ARM, bot_id: str = G.BOT_ID, now_ms: int | None = None,
          code_state_fn: Callable[[], tuple[str | None, bool | None]] | None = None, repo: str | Path | None = None, keep_bonding: bool = False,
          ancestor: Callable[[str], bool] | None = None, v_audit_ids_out: str | Path | None = None) -> dict:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    repo = Path(repo) if repo else Path(__file__).resolve().parent.parent
    # --- refusals, before anything is opened beyond the gate files
    G.exp027_read_written(exp027_record, exp027_record_sha256, now, ancestor=ancestor)
    droot = G.check_path(declog_root, "the declog root")
    troot = G.check_path(tape_dir, "the tape dir")
    lroot = G.check_path(ledger_root, "the ledger root")
    G.check_registry(registry)
    if set(pins) != set(PIN_KEYS):
        raise G.Refused(f"pins must name exactly {PIN_KEYS}")
    pin_hashes = G.check_pins(repo, pins)
    cfg = pinned_config(feed_delay_slots=feed_delay_slots, k_rounding=k_rounding)
    reg = load_registry(registry)
    e = reg.get(bot_id)
    if e is None:
        raise G.Refused(f"{bot_id} is not registered in this registry")
    if e.get("deciding_stake") != DECIDING_STAKE or e.get("model_ids") != []:
        raise G.Refused(f"{bot_id}: the registry line must carry deciding_stake 0.25 and model_ids [] (r4-b-T amendment, registration)")
    clock_ms = int(e["clock_start_ms"])
    csf = code_state_fn or (lambda: process_code_state(repo))
    head, dirty = csf()
    if head != e["frozen_sha"] or dirty is not False:
        raise G.Refused(f"the process runs code {head} (dirty {dirty}), not a clean checkout at the registered frozen_sha {e['frozen_sha']}: the scorer would refuse every row")
    if (lroot / bot_id).exists() and any((lroot / bot_id).iterdir()):
        raise G.Refused(f"{lroot / bot_id} already holds a ledger for {bot_id}: one pricer run per ledger root (the ledger is write-once)")
    b = load_boundary(boundary_file, boundary_sha256, clock_ms, arm)
    boundary_ms = int(b["boundary_ms"])
    stop_ms = e.get("stopped_at_ms")
    if stop_ms is not None and int(stop_ms) < boundary_ms:
        raise G.Refused(f"{bot_id} was stopped at {stop_ms}, before the boundary {boundary_ms}: an A3 halt before the boundary makes the read NOT_DECIDABLE")
    n_days = verify_day_shas(droot, arm, day_sha_file, day_sha_file_sha256, now)
    rows = load_declog(droot, arm)
    n_starts = check_start_rows(rows, e["frozen_sha"])
    entries, skipped = select_entries(rows, clock_ms, h_days, boundary_ms, b.get("from_ms"))
    expect = int(b.get("attempts_before_boundary", 0)) + int(b.get("excluded_hour_before_boundary", 0))
    if len(entries) != expect:
        raise G.Refused(f"entries before the boundary here ({len(entries)}) != the boundary file's counted + excluded-hour attempts ({expect}): the two counters disagree")
    orders = {r.get("order_id") for r in entries}
    exits = [dict(r) for r in rows if r.get("kind") == "exit" and r.get("order_id") in orders]
    unexited = orders - {x["order_id"] for x in exits}
    if unexited:
        raise G.Refused(f"{len(unexited)} counted entr(y/ies) have no exit row yet: pricing now would leave restart orphans and make the read a final NOT_DECIDABLE; wait for the last exit")
    last_ms = max([r["ms"] for r in entries + exits] + [clock_ms])
    lo_ms = clock_ms - GUARD_PRE_MS
    files = tape_hours(troot, lo_ms, last_ms + EXIT_POST_MS, now)
    events = sorted([{"_ms": r["ms"], "_seq": r.get("seq", 0), "k": "entry", "row": r} for r in entries]
                    + [{"_ms": r["ms"], "_seq": r.get("seq", 0), "k": "exit", "row": r} for r in exits], key=lambda x: (x["_ms"], x["_seq"]))
    entry_ms = {r["order_id"]: r["ms"] for r in entries}
    entry_pools = {r["pool"] for r in entries}
    prints: dict[str, list] = {p: [] for p in entry_pools}
    # --- the engine
    clock = [lo_ms]
    eng = ArenaEngine(ledger_root=str(lroot), registry_path=str(registry), cfg=cfg, sps_fn=None, now_fn=lambda: clock[0], code_state_fn=csf)
    eng.start_record(bot_id)
    by_order: dict[str, str] = {}
    c: Counter = Counter()
    tape_loss: Counter = Counter()

    def exclude(pid: str, ms: int, why: str, reason: str) -> None:
        pos = eng.positions[pid]
        pos.exit_ms, pos.exit_slot, pos.xs, pos.sps_exit, pos.reason = ms, eng.hw_slot, eng.hw_slot, pos.sps_entry, reason
        pos.exit_stamp_ms, pos.exit_call_ms = ms, ms
        eng._write_excluded(pos, why, None)
        del eng.positions[pid]
        c[why + "_exclusions"] += 1

    def apply(ev: Mapping[str, Any]) -> None:
        r = ev["row"]
        clock[0] = ev["_ms"]  # the call clock IS the decision instant
        if ev["k"] == "entry":
            try:
                by_order[r["order_id"]] = eng.buy(bot_id, r["pool"], r.get("stake_sol"), r["ms"], eng.hw_slot,
                                                  {"order_id": r["order_id"], "t_mark": r.get("t_mark"), "rank": r.get("rank"), "declog_seq": r.get("seq")})
                c["buys_open"] += 1
            except ArenaRefused as exc:
                c["buy_refused:" + str(exc).split(":")[0]] += 1
            return
        pid = by_order.get(r.get("order_id"))
        if pid is None or pid not in eng.positions:
            c["exit_without_open_position"] += 1
            return
        if r.get("reason") == "restart":
            exclude(pid, r["ms"], "engine_restart", "restart")
        elif stop_ms is not None and entry_ms[r["order_id"]] < int(stop_ms) <= r["ms"]:
            exclude(pid, r["ms"], "a3_halt", str(r.get("reason") or ""))  # the span contains the A3 stop instant: a section 1.4 exclusion in the 5% cap
        else:
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
                if row.get("type") == "gap" and (row.get("data_loss") or row.get("kind") in ("process_restart", "feed_restart", "feed_start")):
                    tape_loss[f"{row.get('prog')}:{row.get('kind')}"] += 1
                if row.get("type", "trade") == "trade" and row.get("prog") == "pumpswap" and row.get("pool") in prints and _is_int(row.get("slot")):
                    prints[row["pool"]].append((int(row["slot"]), row.get("virtual_quote_reserves"), row.get("quote_mint")))
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
    for p in prints.values():
        p.sort(key=lambda x: x[0])
    audit = v_audit([r for r in led if r["kind"] == "trade" and r["stake_sol"] == DECIDING_STAKE], prints)
    if v_audit_ids_out:
        Path(v_audit_ids_out).write_text(json.dumps({"bot_id": bot_id, "ids": audit}, sort_keys=True) + "\n")
    return {
        "tool": "r4bt_price", "bot_id": bot_id, "arm": arm, "clock_start_ms": clock_ms, "boundary_ms": boundary_ms, "from_ms": b.get("from_ms"),
        "boundary_file_sha256": boundary_sha256, "day_sha_file_sha256": day_sha_file_sha256, "declog_days_verified": n_days, "declog_start_rows": n_starts,
        "entries_selected": len(entries), "entries_not_selected": skipped, "exits_in_scope": len(exits), "tape_hours": len(files), "tape_first_hour": hour_name(lo_ms),
        "feed_delay_slots": feed_delay_slots, "k_rounding": k_rounding, "stack": cfg.as_dict(), "pins": pin_hashes, "frozen_sha": e["frozen_sha"],
        "stopped_at_ms": stop_ms, "open_positions_left": len(eng.positions), "ledger_rows_by_kind": dict(kinds), "counts": dict(sorted(c.items())),
        "declog_gaps": declog_gap_counts(rows, entries, exits), "tape_data_loss_rows_by_prog_kind": dict(sorted(tape_loss.items())), "v_audit_n": len(audit),
        "engine_counters": dict(sorted(eng.counters.items())), "engine_id": eng.engine_id,
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="r4-b-T pricer: counts only.")
    for a in ("declog-root", "tape-dir", "ledger-root", "registry", "boundary-file", "boundary-sha256", "day-sha-file", "day-sha-file-sha256", "arena-paper-sha256",
              "arena-score-sha256"):
        ap.add_argument("--" + a, required=True)
    ap.add_argument("--exp027-record", default=None)
    ap.add_argument("--exp027-record-sha256", default=None)
    ap.add_argument("--h-days", type=int, required=True)
    ap.add_argument("--feed-delay-slots", type=int, default=None, help="quant-proof's pin (Q5); no default")
    ap.add_argument("--k-rounding", default=None, help="quant-proof's pin (Q5: ceil); no default")
    ap.add_argument("--keep-bonding", action="store_true")
    ap.add_argument("--v-audit-ids-out", default=None, help="sidecar with the ids of the V-audit trades (refused if it exists)")
    ap.add_argument("--out", default=None, help="also write the counts JSON here (refused if it exists)")
    return ap


def main(argv: Sequence[str] | None = None, *, now_ms: int | None = None, code_state_fn: Callable[[], tuple[str | None, bool | None]] | None = None,
         ancestor: Callable[[str], bool] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if a.feed_delay_slots is None or a.k_rounding is None:
            raise G.Refused("--feed-delay-slots and --k-rounding are required (quant-proof's pins, Q5)")
        for o in (a.out, a.v_audit_ids_out):
            if o and Path(o).exists():
                raise G.Refused(f"{o} exists")
        res = price(declog_root=a.declog_root, tape_dir=a.tape_dir, ledger_root=a.ledger_root, registry=a.registry, boundary_file=a.boundary_file,
                    boundary_sha256=a.boundary_sha256, day_sha_file=a.day_sha_file, day_sha_file_sha256=a.day_sha_file_sha256, h_days=a.h_days,
                    exp027_record=a.exp027_record, exp027_record_sha256=a.exp027_record_sha256,
                    pins={"tools/arena_paper.py": a.arena_paper_sha256, "tools/arena_score.py": a.arena_score_sha256}, feed_delay_slots=a.feed_delay_slots,
                    k_rounding=a.k_rounding, now_ms=now_ms, code_state_fn=code_state_fn, keep_bonding=a.keep_bonding, ancestor=ancestor,
                    v_audit_ids_out=a.v_audit_ids_out)
    except G.Refused as e:
        print(f"r4bt_price: REFUSED: {e}", file=sys.stderr)
        return 2
    except Untrusted as e:
        print(f"r4bt_price: UNTRUSTED: {e}", file=sys.stderr)
        return 3
    text = json.dumps(res, sort_keys=True)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
