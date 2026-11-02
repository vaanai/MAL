#!/usr/bin/env python3
"""HV8 C3: training dump. Design: docs/research/hv8/DESIGN.md sections 3, 4 and 7. Prints COUNTS only. 0 API calls, no outcome of any kind.

Replays the explore-0814 tape (arena-format hours written by tools/arena_clean_adapter.py) through HV7's own screen (`Hv7Runner.screen`, `_card`) with HV7's
FakeChain, state warmed from the block start, and takes the cards at every decision instant t_d = H:02:00Z for H in [2026-08-21T12, 2026-08-28T11) (167 instants).
Each card goes through hv8_rate.tape_view, so the view carries no RPC field. Output (one run into an EMPTY dir):
  prompts/batch-NN.jsonl  one rating request per line, per the C1 batch plan (first-rating batches, then retest batches); batch plan 21 + 3 (+ 1 re-run batch later)
  cards.jsonl             feature rows for the calibrator (card_sha, t_d, pool, f, risk, setup_type, setup_z); NO outcome
  manifest.json           counts, sha256 of every file, caps, batch plan, batch-order seed, playbook / template sha, git head, drop counts

HARD REFUSALS (raise Refused, write nothing): an input hour file at or after the window end (2026-08-28T12), or before the block start (fresh-0808 lookback);
any row whose t_recv_ms, event_ts or block_time is at or after the window end; a t_d outside the 167-instant grid; a path under fresh-08*, arena, meta-capture.
created_known: a card requires a graduation (`complete`) or `pool_create` event OBSERVED in the block, at or after the block start and strictly before t_d, for its
mint / pool. First print never sets it. Pools without one are dropped before the top-8 cut (counted; expected non-zero).

RUN CONDITION (QP, C3): the adapter must run with NO --lookback, and adapter_report.json must show preamble_rows 0, first_hour 2026-08-14T12 and last_hour
2026-08-28T11. Otherwise STOP and go back to QP (scripts/ops/hv8-offline.sh checks it). Preamble rows are stamped LATER than they happened, never earlier, but a
pre-block graduation stamped at the block start would count as "observed in block".
DISCLOSURE: the adapter writes no pool_create rows and `create` rows are ignored, so created_known is GRADUATION-ONLY on this tape (the pool_create path in Known is dead
on adapter output); PumpSwap pools that did not come from a pump.fun graduation are always dropped. The drop count is reported with this note.
RULE U2 IS OFF HERE, EXPLICITLY (`u2=False` in the runner config below): HV7's tip-source rule U2 (tools/hv7_tip_guard.py) admits only pools whose `pool_create` row is in the history, and
this tape has none by construction (see DISCLOSURE), so with U2 on every PumpSwap pool would be refused before HV8's own `Known` filter ran. The runner refuses u2=False for a tape under the
tip receiver copy. HV8's created_known is its own (graduation-only) rule, unchanged.
Nothing here says HV8 makes money. HV8 has 0 trades.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MAL_ARENA_TEST", "1")

from tools import hv4_prompts as P  # noqa: E402
from tools import hv7_rank as RK  # noqa: E402
from tools import hv7_runner as R  # noqa: E402
from tools import hv8_rate as H8  # noqa: E402
from tools.arena_state import ArenaState  # noqa: E402
from tools.arena_zst import open_zst  # noqa: E402
from tools.drill_hv6_item6 import FakeChain  # noqa: E402
from tools.hv7_p0_count import NoModel, NullEngine  # noqa: E402

OUT_ROOT = "/data2/mal/hv8-train"
DENY_PATH = ("fresh-08", "fresh-0828", "fresh-0808", "/data/mal/arena", "/data/mal/meta-capture", "meta-capture", "hv4", "q50", "probe")
HOUR_RE = re.compile(r"^tape-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(\.zst)?$")
HOUR = 3_600_000


class Refused(Exception):
    pass


def iso_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class Window:
    block_start_ms: int      # first tape hour (warm-up starts here)
    first_h_ms: int          # first decision hour H (t_d = H + 2 min)
    last_h_excl_ms: int      # decision hours H < this
    end_ms: int              # tape end, exclusive: hard refusal at or after

    def grid(self) -> list[int]:
        return [h + R.T_D_OFFSET_MS for h in range(self.first_h_ms, self.last_h_excl_ms, HOUR)]


DEFAULT = Window(iso_ms("2026-08-14T12:00:00Z"), iso_ms("2026-08-21T12:00:00Z"), iso_ms("2026-08-28T11:00:00Z"), iso_ms("2026-08-28T12:00:00Z"))


def iso_s(ms: int) -> str:
    """Second-resolution ISO ('2026-08-21T12:02:00Z'), the form hv8_calib.fold_of parses."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_td(t_d_ms: int, win: Window = DEFAULT) -> None:
    if t_d_ms not in set(win.grid()):
        raise Refused("t_d_not_on_the_training_grid")
    if t_d_ms >= win.end_ms:
        raise Refused("t_d_at_or_after_tape_end")


def check_path(path: str | os.PathLike) -> Path:
    low = os.path.realpath(os.fspath(path)).lower()
    if any(tok in low for tok in DENY_PATH):
        raise Refused("denied_path")
    return Path(os.fspath(path))


def _ms(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = int(v)
    return v * 1000 if v < 100_000_000_000 else v     # seconds or ms


def row_time_ms(row: dict) -> int:
    ts = [x for x in (_ms(row.get(k)) for k in ("t_recv_ms", "event_ts", "block_time")) if x is not None]
    return max(ts) if ts else 0


def guard_row(row: dict, win: Window = DEFAULT) -> None:
    if row_time_ms(row) >= win.end_ms:
        raise Refused("row_at_or_after_tape_end")


def hour_files(tape: Path, win: Window = DEFAULT) -> list[tuple[int, Path]]:
    """Every hour file of the dir, checked by NAME first (nothing is opened): one at or after the end, before the block start, or unparsable raises."""
    out = []
    for p in sorted(Path(tape).iterdir()):
        if not p.name.startswith("tape-"):
            continue
        m = HOUR_RE.match(p.name)
        if not m:
            raise Refused("unparsable_hour_file_name")
        h = iso_ms(m.group(1) + ":00:00Z")
        if h >= win.end_ms:
            raise Refused("hour_file_at_or_after_tape_end")
        if h < win.block_start_ms:
            raise Refused("hour_file_before_block_start")
        out.append((h, p))
    return out


def _lines(path: Path):
    if path.name.endswith(".zst"):
        r = open_zst(path)
        try:
            buf = b""
            while True:
                b = r.read(1 << 22)
                if not b:
                    break
                buf += b
                cut = buf.rfind(b"\n")
                if cut >= 0:
                    yield from buf[:cut].split(b"\n")
                    buf = buf[cut + 1:]
            if buf.strip():
                yield buf
        finally:
            r.close()
    else:
        with path.open("rb") as fh:
            yield from fh


def prescan_tail(files: list[tuple[int, Path]], win: Window = DEFAULT) -> int:
    """Open the last two hour files fully and refuse any row at or after the end (a mis-filed row). Returns rows scanned."""
    n = 0
    for h, p in files:
        if h < win.end_ms - 2 * HOUR:
            continue
        for ln in _lines(p):
            if ln.strip():
                try:
                    row = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    guard_row(row, win)
                    n += 1
    return n


class Known:
    """(pool_create is a dead path on adapter output: the adapter writes none.) created_known from OBSERVED events only: a `complete` row (by mint) or a `pool_create` row (by pool and by mint)."""

    def __init__(self, win: Window) -> None:
        self.win, self.mint_t, self.pool_t = win, {}, {}

    def observe(self, row: dict) -> None:
        typ, t = row.get("type"), row.get("t_recv_ms")
        if typ not in ("complete", "pool_create") or not isinstance(t, int) or isinstance(t, bool) or t < self.win.block_start_ms:
            return
        for d, k in ((self.mint_t, row.get("mint")), (self.pool_t, row.get("pool") if typ == "pool_create" else None)):
            if isinstance(k, str) and k and t < d.get(k, 1 << 62):
                d[k] = t

    def known(self, pool: str, mint: str | None, t_d: int) -> bool:
        ts = [self.pool_t.get(pool), self.mint_t.get(mint) if mint else None]
        return any(t is not None and t < t_d for t in ts)


def allow_roots(scratch: Path) -> None:
    """Process-local: HV7's path allowlist gets the scratch dir (the runner's decision log and the engine ledger live there)."""
    base = os.path.realpath(scratch)
    R.READ_ROOTS = tuple(dict.fromkeys(R.READ_ROOTS + (base,)))
    R.WRITE_ROOTS = tuple(dict.fromkeys(R.WRITE_ROOTS + (base,)))


def make_runner(tape: Path, scratch: Path, win: Window = DEFAULT, *, t_ds: list[int] | None = None, engine: Any = None, model: Any = None,
                guard: Any = None, cut_ms: int | None = None) -> tuple[Any, dict[str, Any]]:
    """The guarded HV7 runner over the tape (shared by the dump and the label tool). Every refusal fires here, before a row is applied.
    Defaults (NullEngine, NoModel) price nothing and ask nothing. cut_ms: rows with t_recv_ms at or after it are dropped on arrival (the tape "cut at t_d")."""
    tape, scratch = check_path(tape), check_path(scratch)
    files = hour_files(tape, win)
    t_ds = win.grid() if t_ds is None else list(t_ds)
    for t in t_ds:
        check_td(t, win)
    rows_scanned = prescan_tail(files, win)
    known = Known(win)
    root = scratch / "v7"
    allow_roots(scratch)
    run = R.Hv7Runner(R.Hv7Config(root=str(root), frozen_sha="hv8dump0000000", clock_start_ms=win.block_start_ms, phantom_filter=True, u2=False),
                      ArenaState(tape, start_ms=0), engine or NullEngine(), model or NoModel(), FakeChain(), guard or R.Hv7Guard(root))
    if model is None:
        run._ask = lambda rr, T: None       # type: ignore[method-assign]  # no model call of any kind
    keep = frozenset(p for _h, p in files)
    st = run.state
    st._hour_files = lambda orig=st._hour_files: [t for t in orig() if (t[1] or t[2]) in keep]     # type: ignore[method-assign]
    inner_ingest = st._ingest

    def ingest(data: bytes) -> None:       # every row read is checked on arrival, before it can queue
        n0 = len(st._buf)
        inner_ingest(data)
        new = list(st._buf)[n0:]
        for r in new:
            guard_row(r, win)
        if cut_ms is not None:
            for _ in new:
                st._buf.pop()
            st._buf.extend(r for r in new if r["t_recv_ms"] < cut_ms)

    st._ingest = ingest                    # type: ignore[method-assign]
    inner_apply = st.apply

    def apply(row: dict) -> None:
        guard_row(row, win)
        known.observe(row)
        inner_apply(row)

    st.apply = apply                       # type: ignore[method-assign]
    orig_card = run._card
    dropped = {"instances": 0, "pools": set()}

    def card(pool, T, snap, prints, feats):
        if not known.known(pool, snap.get("mint"), T):
            dropped["instances"] += 1
            dropped["pools"].add(pool)
            run._count("created_unknown_dropped")
            return None
        return orig_card(pool, T, snap, prints, feats)

    run._card = card                       # type: ignore[method-assign]
    return run, {"dropped": dropped, "tail_rows_scanned": rows_scanned, "t_ds": t_ds}


def collect(tape: Path, scratch: Path, win: Window = DEFAULT, *, t_ds: list[int] | None = None, cut_ms: int | None = None) -> dict[str, Any]:
    """The replay. Returns {sets: [(t_d, [cards in screen order])], counters, dropped_instances, dropped_pools, tail_rows_scanned, periods}."""
    run, info = make_runner(tape, scratch, win, t_ds=t_ds, cut_ms=cut_ms)
    dropped, t_ds, rows_scanned = info["dropped"], info["t_ds"], info["tail_rows_scanned"]
    sets = []
    for T in t_ds:
        run.poll_to(T)                     # rows up to T - 1 ms only; the first call replays the warm-up
        run.sim_now = max(run.sim_now, T)
        run.pending = None
        run.screen(T)
        if run.pending is not None:
            cards = [run.pending["cards"][i] for i in run.pending["scr_ids"]]
            assert RK.N_MIN <= len(cards) <= RK.N_MAX and all(c["as_of_ms"] == T for c in cards)
            sets.append((T, cards))
    return {"sets": sets, "counters": dict(sorted(run.counters.items())), "dropped_instances": dropped["instances"], "dropped_pools": len(dropped["pools"]),
            "tail_rows_scanned": rows_scanned, "periods": len(t_ds)}


def card_rows(sets) -> list[dict]:
    rows = []
    for t_d, cards in sets:
        iso = iso_s(t_d)
        for pos, c in enumerate(cards):
            v = H8.tape_view(c)
            rows.append({"card_sha": H8.view_sha256(c), "t_d": iso, "pos": pos, "n_in_set": len(cards), "pool": v["id"], "f": v["f"], "risk": v["risk"],
                         "setup_type": (v.get("setup") or {}).get("type"), "setup_z": (v.get("setup") or {}).get("z")})
    return rows


def _jl(objs) -> bytes:
    return "".join(json.dumps(o, sort_keys=True, separators=(",", ":")) + "\n" for o in objs).encode()


def repo_head(repo: str | os.PathLike | None = None) -> str:
    cwd = str(repo) if repo is not None else str(Path(__file__).resolve().parent.parent)
    try:
        if subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip():
            raise Refused("dirty_tree")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        raise Refused("git_head_unavailable") from None
    if len(head) != 40:
        raise Refused("git_head_unavailable")
    return head


def write_dump(res: dict[str, Any], out_dir: Path, *, head: str, win: Window = DEFAULT, out_root: str | None = OUT_ROOT) -> dict[str, Any]:
    out_dir = Path(os.path.abspath(out_dir))
    if out_root is not None:
        real, root = os.path.realpath(out_dir), os.path.realpath(out_root)
        if ".." in Path(os.fspath(out_dir)).parts or not real.startswith(root + os.sep):
            raise Refused("out_not_under_root")
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise Refused("out_not_empty")
    rows = card_rows(res["sets"])
    cards = {H8.view_sha256(c): c for _t, cs in res["sets"] for c in cs}
    first = [r["card_sha"] for r in rows]
    retest = H8.retest_select(first)
    caps = H8.call_caps(len(first))
    if len(first) + len(retest) > caps["cap"] or len(first) > 8 * len(win.grid()):
        raise Refused("call_cap")
    plan = H8.split_batches(first, retest)
    reqs = {(s, q): H8.build_rate_request(cards[s], seq=q) for s, q in [(s, 0) for s in first] + [(s, 1) for s in retest]}
    (out_dir / "prompts").mkdir(parents=True, exist_ok=True)
    files, batches = [], []
    for b in plan:
        seq = 0 if b["kind"] == "first" else 1
        body = _jl([{"batch": b["batch"], "kind": b["kind"], "card_sha": s, "seq": seq, "system": reqs[(s, seq)].system, "user": reqs[(s, seq)].user_text,
                     "request_sha256": reqs[(s, seq)].request_sha256()} for s in b["shas"]])
        name = f"prompts/batch-{b['batch']:02d}.jsonl"
        (out_dir / name).write_bytes(body)
        files.append({"file": name, "calls": len(b["shas"]), "sha256": hashlib.sha256(body).hexdigest()})
        batches.append({"batch": b["batch"], "kind": b["kind"], "n": len(b["shas"]), "shas": b["shas"]})
    cbody = _jl(rows)
    (out_dir / "cards.jsonl").write_bytes(cbody)
    pb, tp = H8.load_rate_playbook(), H8.load_rate_template()
    per_day: dict[str, int] = {}
    for t, _c in res["sets"]:
        per_day[iso_s(t)[:10]] = per_day.get(iso_s(t)[:10], 0) + 1
    manifest = {"hv8_dump": "manifest", "window": {"block_start": iso_s(win.block_start_ms), "first_h": iso_s(win.first_h_ms),
                                                    "last_h_excl": iso_s(win.last_h_excl_ms), "tape_end_excl": iso_s(win.end_ms)},
                "periods": res["periods"], "sets": len(res["sets"]), "cards": len(first), "retest": len(retest), "caps": caps, "sets_per_day": per_day,
                "batch_plan": {"first_batches": sum(1 for b in plan if b["kind"] == "first"), "retest_batches": sum(1 for b in plan if b["kind"] == "retest"),
                               "rerun_batch": 1, "max_per_batch": H8.BATCH_MAX, "order_seed": "sha256('hv8-batch|' + card_sha)"},
                "batches": batches, "files": files, "cards_sha256": hashlib.sha256(cbody).hexdigest(), "playbook_sha256": pb.sha256, "template_sha256": tp.sha256,
                "created_known": {"rule": "complete or pool_create observed in block, >= block start, < t_d", "graduation_only_on_adapter_tape": True, "dropped_instances": res["dropped_instances"],
                                  "dropped_pools": res["dropped_pools"]},
                "screen_counters": res["counters"], "api_calls": 0, "outcome_free": True, "git_head": head}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=1) + "\n")
    return {k: manifest[k] for k in ("sets", "cards", "retest", "caps", "batch_plan", "created_known")} | {"hv8_dump": "done", "api_calls": 0, "git_head": head}


def lookahead_instants(win: Window = DEFAULT) -> list[int]:
    """DESIGN section 4 no-lookahead check: the base instants are the first, the middle (index n // 2) and the last t_d of the grid."""
    g = win.grid()
    return [g[0], g[len(g) // 2], g[-1]]


def lookahead_pick(full_sets, win: Window = DEFAULT) -> tuple[list[int | None], list[dict[str, Any]]]:
    """Swap rule (fixed): a base instant with no set takes the NEXT later grid instant that has a set and is not already used; none left -> None (NOT_RUN)."""
    g, has, used = win.grid(), {t for t, cs in full_sets if cs}, set()
    picks, swaps = [], []
    for base in lookahead_instants(win):
        use = next((t for t in g if t >= base and t in has and t not in used), None)
        if use is not None:
            used.add(use)
        picks.append(use)
        if use != base:
            swaps.append({"base": iso_s(base), "used": iso_s(use) if use is not None else None})
    return picks, swaps


def lookahead_check(tape: Path, scratch: Path, full_sets, win: Window = DEFAULT) -> dict[str, Any]:
    """For 3 instants: rebuild the cards on the tape CUT at t_d (rows at or after t_d dropped on arrival) and compare card bytes and view_sha256 with the
    full-tape pass. PASS needs every instant to hold at least one compared card and to match; an instant without a set is swapped by lookahead_pick; if no
    instant can be found the result is NOT_RUN. Card shas only; no outcome."""
    full = {t: cs for t, cs in full_sets}
    picks, swaps = lookahead_pick(full_sets, win)
    inst, mism, cards_cmp = [], 0, 0
    for n, t in enumerate(picks):
        if t is None:
            continue
        cut = collect(tape, Path(scratch) / f"lookahead-{n}", win, t_ds=[t], cut_ms=t)["sets"]
        a, b = full.get(t, []), (cut[0][1] if cut else [])
        same = bool(a) and [H8.view_sha256(c) for c in a] == [H8.view_sha256(c) for c in b] and \
            [json.dumps(c, sort_keys=True) for c in a] == [json.dumps(c, sort_keys=True) for c in b]
        mism += 0 if same else 1
        cards_cmp += len(a)
        inst.append({"t_d": iso_s(t), "cards_full": len(a), "cards_cut": len(b), "match": same})
    verdict = "NOT_RUN" if any(t is None for t in picks) else ("FAIL" if mism else "PASS")
    return {"lookahead_check": verdict, "instants": inst, "swaps": swaps, "sets_compared": len(inst), "cards_compared": cards_cmp, "mismatches": mism}


def adapter_report_sha(tape: Path) -> str | None:
    p = Path(tape) / "adapter_report.json"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def check_count_record(count_path: Path, tape: Path, repo: str | os.PathLike | None = None) -> None:
    """The dump gate: the last count-only output must say PASS, name the CURRENT clean git head and the CURRENT adapter_report.json sha256."""
    try:
        rec = json.loads(Path(count_path).read_text())
    except (OSError, ValueError):
        raise Refused("no_count_record") from None
    if rec.get("lookahead_check") != "PASS":
        raise Refused("count_record_not_pass")
    if rec.get("git_head") != repo_head(repo):
        raise Refused("count_record_git_head_differs")
    cur = adapter_report_sha(tape)
    if cur is None or rec.get("adapter_report_sha256") != cur:
        raise Refused("count_record_adapter_report_differs")


def count_only(res: dict[str, Any]) -> dict[str, Any]:
    n = sum(len(c) for _t, c in res["sets"])
    return {"hv8_dump": "count_only", "periods": res["periods"], "sets": len(res["sets"]), "cards": n, "caps": H8.call_caps(n),
            "created_unknown_dropped_instances": res["dropped_instances"], "created_unknown_dropped_pools": res["dropped_pools"], "created_known_note": "graduation-only on adapter tape (no pool_create rows)",
            "screen_counters": res["counters"], "tail_rows_scanned": res["tail_rows_scanned"], "api_calls": 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV8 training dump (explore-0814 adapter output). Counts only; 0 API.")
    ap.add_argument("--tape", required=True, help="arena-format hours written by arena_clean_adapter from explore-0814 (never the live arena tape)")
    ap.add_argument("--scratch")
    ap.add_argument("--check-count", metavar="COUNT_JSON", help="the dump gate: refuse unless this count-only record is PASS at the current head and adapter report")
    ap.add_argument("--out", help="empty dir under /data2/mal/hv8-train/ (omit with --count-only)")
    ap.add_argument("--count-only", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.check_count:
            check_count_record(Path(a.check_count), Path(a.tape))
            print(json.dumps({"hv8_dump": "count_record_ok"}))
            return 0
        if not a.scratch:
            raise Refused("scratch_required")
        head = repo_head() if a.count_only else None             # a dirty tree refuses before any replay
        res = collect(Path(a.tape), Path(a.scratch))
        if a.count_only:
            out = count_only(res) | {"git_head": head, "adapter_report_sha256": adapter_report_sha(Path(a.tape))}
            out |= lookahead_check(Path(a.tape), Path(a.scratch), res["sets"])
            print(json.dumps(out, sort_keys=True, separators=(",", ":")))
            return 4 if out["lookahead_check"] != "PASS" else 0
        if not a.out:
            raise Refused("out_required")
        print(json.dumps(write_dump(res, Path(a.out), head=repo_head()), sort_keys=True, separators=(",", ":")))
    except Refused as e:
        print(json.dumps({"hv8_dump": "refused", "why": str(e)}), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - the class name only
        print(json.dumps({"hv8_dump": "error", "class": type(e).__name__}), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
