#!/usr/bin/env python3
"""h5_shadow_tip_equiv: md5 DECISION-EQUIVALENCE harness for the tip input mode (tools/h5_shadow_tip.py) against the public-feed mode of tools/h5_shadow.py. PAPER ONLY.

THE CLAIM IT TESTS. The same PumpSwap rows give the same decisions whether the engine is fed by the existing replay path (public mode: `h5_shadow.replay`, the S* code path with
the rows' own stamps) or through the tip chain: rows -> the REAL tools.arena_tip_adapter row functions (trade_row, pool_create_row: the tip tape's shape) -> an arena-layout
`tape-<hour>.jsonl` -> h5_shadow_tip.TipRowSource (ArenaState's file reader) -> h5_shadow_tip.TipDriver -> the same Engine. After the STAMP keys are removed, every record
(trigger, outcome, outcome_bx10, pool, strip, ... all types except the bookkeeping ones) must be equal, and so must one md5 per record type and one over all of them.
STAMP keys (the only keys allowed to differ): t_recv_ms, t_recv (its ISO form), t_detect_ms, detect_lag_ms, t_ms. In the tip arm the host wall is a FIXED OFFSET after the
row's t_recv_ms (`wall_offset_ms`, recorded in the output); the harness asserts every trigger's detect_lag_ms equals it (the stamp is the engine host's clock, not the row's).

WHAT IT DOES NOT TEST (stated, not hidden). The public socket's own artefacts (failed-tx notices, arrival order, reconnect gaps) do not exist in a replay; gap rows are compared
by count only and the synthetic/exploration rows carry none. The tip tape lacks lp_fee / pool_quote_amount / protocol_fee / creator_fee; the exploration replay rows lack them too,
so both arms price with LP_FRAC (the live public feed carries them: that price-view difference is real and is NOT measured here; L1 design 1, #1180).

USE. As a library (tests, synthetic rows) or as a MiScusi job on EXPLORATION hours only (pre-2026-10-02T10, the h5_shadow replay's own refusals apply; never the tip tape, the
arena tape, a forward-* dir or a sealed hour):
  python3 -m tools.h5_shadow_tip_equiv --tape-dir /data/mal/audit-1008/tape --hours 2026-08-14T12,2026-08-14T13 --scratch-root /data2/mal/scratch --out OUT.json --min-triggers N
`--min-triggers` is the floor to notebook BEFORE the run (a pass with fewer triggers proves nothing: exit 4). Exit 0 equal and floor met; 1 not equal; 4 floor not met.
"""

from __future__ import annotations

if __package__ in (None, ""):  # `python3 tools/h5_shadow_tip_equiv.py` from the repo root
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import collections
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from tools import arena_tip_adapter as ta
from tools import h5_shadow as h5
from tools import h5_shadow_tip as tip

SCHEMA = "h5_shadow_tip_equiv_v1"
STAMP_KEYS = ("t_recv_ms", "t_recv", "t_detect_ms", "detect_lag_ms", "t_ms")
FEE_KEYS = ("lp_fee", "pool_quote_amount", "protocol_fee", "creator_fee", "mint_source")  # what the tip tape cannot carry
BOOKKEEPING = ("start", "stop", "hb", "tip_input_start", "tip_input_problem", "tip_input_resumed", "tip_input_halted")  # not decisions
EXIT_OK, EXIT_DIFF, EXIT_FLOOR = 0, 1, 4


def _hour(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def prepare_rows(rows: Iterable[dict]) -> list[dict]:
    """The rows both arms get: the tip tape's view (no fee keys), and a signature (replay rows have none; the adapter needs one, and a trigger record carries it)."""
    out = []
    for r in rows:
        r = {k: v for k, v in r.items() if k not in FEE_KEYS}
        if not r.get("signature"):
            r["signature"] = str(r.get("replay_sig") or f"sig-{r['slot']}-{len(out)}")
        out.append(r)
    return out


def to_tip_rows(rows: Sequence[dict], meta: dict[str, dict]) -> list[dict]:
    """Arena-tape rows through the real adapter functions, in the replay's order; each pool's `pool_create` comes right before the pool's own first print (the replay's announce rule)."""
    out: list[dict] = []
    announced: set[str] = set()
    for r in rows:
        pool = r["pool"]
        if pool not in announced and pool in meta and r["slot"] == meta[pool]["s0"]:
            announced.add(pool)
            out.append(ta.pool_create_row({"slot": r["slot"] - 1, "t_recv_ms": r["t_recv_ms"], "pool": pool, "mint": meta[pool]["mint"], "quote_mint": h5.WSOL_MINT,
                                           "signature": "create-" + pool, "event_index": 0, "tx_index": 0, "block_time": r.get("event_ts")}))
        w = dict(r)
        w["venue"] = "pumpswap"
        if w.get("tx_index") is None:
            w.pop("tx_index", None)
        out.append(ta.trade_row(w))
    return out


def write_tape(tape_dir: Path, rows: Sequence[dict]) -> None:
    """The tip tape's line format (compact strict JSON, one file per UTC hour of t_recv_ms), as tools/arena_tip_tape_writer writes it."""
    tape_dir.mkdir(parents=True, exist_ok=True)
    by_hour: dict[str, list[bytes]] = collections.defaultdict(list)
    for r in rows:
        by_hour[_hour(r["t_recv_ms"])].append((json.dumps(h5.clean(r), separators=(",", ":"), allow_nan=False) + "\n").encode())
    for h, lines in by_hour.items():
        (tape_dir / f"tape-{h}.jsonl").write_bytes(b"".join(lines))


def strip_stamps(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: strip_stamps(v) for k, v in obj.items() if k not in STAMP_KEYS}
    if isinstance(obj, list):
        return [strip_stamps(v) for v in obj]
    return obj


def _canon(rec: dict) -> str:
    return json.dumps(h5.clean(strip_stamps(rec)), sort_keys=True, separators=(",", ":"))


def digests(records: Sequence[dict]) -> dict[str, Any]:
    by_type: dict[str, list[str]] = collections.defaultdict(list)
    for r in records:
        if r.get("type") not in BOOKKEEPING:
            by_type[str(r.get("type"))].append(_canon(r))
    md5 = lambda lines: hashlib.md5("\n".join(lines).encode()).hexdigest()  # noqa: E731 - a decision-equivalence fingerprint, not security
    allv = [ln for t in sorted(by_type) for ln in by_type[t]]
    return {"n": {t: len(v) for t, v in sorted(by_type.items())}, "md5": {t: md5(v) for t, v in sorted(by_type.items())}, "md5_all": md5(allv)}


def run_public(rows: Sequence[dict], meta: dict[str, dict], make_engine: Callable[[Callable[[dict], None]], h5.Engine]) -> list[dict]:
    """S* public-mode path: h5_shadow.replay (stamps = the rows' t_recv_ms)."""
    recs: list[dict] = []
    h5.replay(rows, meta, make_engine(recs.append))
    return recs


def run_tip(tape_dir: Path, make_engine: Callable[[Callable[[dict], None]], h5.Engine], *, wall_offset_ms: int, roots: Sequence[str], start_ms: int = 0) -> tuple[list[dict], dict]:
    """Tip-mode path: TipRowSource over the written tape, TipDriver with the fixed wall offset, drained to the end, then close_all like replay()."""
    recs: list[dict] = []
    eng = make_engine(recs.append)
    src = tip.TipRowSource(tape_dir, start_ms=start_ms, roots=roots, problem_fn=lambda: None)
    src.start()
    drv = tip.TipDriver(eng, src, wall_offset_ms=wall_offset_ms)
    while drv.step(500):
        pass
    eng.close_all("replay_end")
    return recs, {"driver": dict(drv.counters), "source": src.counters()}


def compare(public: Sequence[dict], tipr: Sequence[dict], *, wall_offset_ms: int) -> dict:
    dp, dt = digests(public), digests(tipr)
    bad_lag = [r.get("pool") for r in tipr if r.get("type") == "trigger" and r.get("detect_lag_ms") != wall_offset_ms]
    gaps = lambda recs: sum(1 for r in recs if r.get("type") == "gap")  # noqa: E731
    first = None
    for i, (a, b) in enumerate(zip([r for r in public if r.get("type") not in BOOKKEEPING], [r for r in tipr if r.get("type") not in BOOKKEEPING])):
        if _canon(a) != _canon(b):
            first = {"index": i, "public_type": a.get("type"), "tip_type": b.get("type")}
            break
    return {"equal": dp == dt and not bad_lag, "public": dp, "tip": dt, "detect_lag_offset_ok": not bad_lag, "wall_offset_ms": wall_offset_ms,
            "gap_records": {"public": gaps(public), "tip": gaps(tipr)}, "first_difference": first, "triggers": dt["n"].get("trigger", 0)}


def run_equivalence(rows: Iterable[dict], meta: dict[str, dict], make_engine: Callable[[Callable[[dict], None]], h5.Engine], *, wall_offset_ms: int = 2500,
                    scratch_root: str, min_triggers: int = 0) -> dict:
    prep = prepare_rows(rows)
    work = Path(tempfile.mkdtemp(dir=scratch_root, prefix="h5tipeq-"))
    try:
        tape = work / "tip"
        write_tape(tape, to_tip_rows(prep, meta))
        public = run_public(prep, meta, make_engine)
        tipr, info = run_tip(tape, make_engine, wall_offset_ms=wall_offset_ms, roots=(str(Path(scratch_root).resolve()),))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out = compare(public, tipr, wall_offset_ms=wall_offset_ms)
    out.update({"schema": SCHEMA, "min_triggers": min_triggers, "floor_met": out["triggers"] >= min_triggers, "tip_info": info, "rows": len(prep), "stamp_keys": list(STAMP_KEYS),
                "fee_keys_removed": list(FEE_KEYS)})
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="md5 decision equivalence: tip input mode vs public-feed replay, same exploration rows (paper only)")
    ap.add_argument("--tape-dir", default=h5.TAPE_DIR)
    ap.add_argument("--work-dir", default=h5.WORK_DIR)
    ap.add_argument("--hours", required=True, help="comma-separated exploration tape hours")
    ap.add_argument("--scratch-root", required=True, help="a dir strictly under /data2/mal/scratch for the synthetic tip tape of the run")
    ap.add_argument("--wall-offset-ms", type=int, default=2500)
    ap.add_argument("--min-triggers", type=int, required=True, help="the floor, notebooked before the run")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    hours = [h.strip() for h in a.hours.split(",") if h.strip()]
    try:
        h5.refuse_replay(a.tape_dir, hours)
        h5.refuse_replay(a.work_dir, [])
        tip.check_tip_input_dir(a.scratch_root, (tip.atp.tm.SCRATCH_ROOT,))  # a strict child of /data2/mal/scratch, an existing dir
    except (h5.Refused, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    rows, meta, sps_pool = h5.replay_rows(a.tape_dir, a.work_dir, hours)
    make_engine = lambda emit: h5.Engine(emit, boost_mode="auto", sps_fn=lambda p: sps_pool.get(p.pool), classifier=h5.PreEventClassifier(), bx10_enabled=False)  # noqa: E731
    res = run_equivalence(rows, meta, make_engine, wall_offset_ms=a.wall_offset_ms, scratch_root=a.scratch_root, min_triggers=a.min_triggers)
    res["hours"] = hours
    text = json.dumps(res, indent=1, sort_keys=True)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return EXIT_OK if res["equal"] and res["floor_met"] else (EXIT_DIFF if not res["equal"] else EXIT_FLOOR)


if __name__ == "__main__":
    sys.exit(main())
