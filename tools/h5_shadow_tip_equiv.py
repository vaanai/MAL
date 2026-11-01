#!/usr/bin/env python3
"""h5_shadow_tip_equiv: md5 DECISION-EQUIVALENCE harness for the tip input mode (tools/h5_shadow_tip.py) against the public-feed mode of tools/h5_shadow.py. PAPER ONLY.

THE CLAIM IT TESTS. The same PumpSwap rows give the same decisions whether the engine is fed by the S* public path (h5_shadow.replay's logic plus the clock move decode_notice
makes at an announcement, see run_public) or through the tip chain. TWO tip arms:
  `writer` (the one that counts; QP K1 on #1185): the rows are written in the FOLLOWER's file format (migrations-/trades-<hour>.jsonl, checkpoint.json, status.json with the pinned
    decoder blobs) into a fake tip dir, the PINNED tools/arena_tip_tape_writer.py runs on it (its reader, its adapter, zstd compaction of the closed hours) into a derived dir, and
    h5_shadow_tip.TipRowSource + TipDriver feed the same Engine from that dir. s0's announcement comes from a MIGRATION row per pool (placed at s0 - 1: the replay rows lack the pool's
    true creation slot and instant, so the replay's own announce point is used). The result records the code pins the writer ran with.
  `adapter` (unit level): the real tools.arena_tip_adapter row functions straight into an arena-layout tape.
After the STAMP keys are removed, every record (trigger, outcome, outcome_bx10, pool, strip, ... all types except the bookkeeping ones) must be equal, and so must one md5 per record
type and one over all of them. STAMP keys (the only keys allowed to differ): t_recv_ms, t_recv (its ISO form), t_detect_ms, detect_lag_ms, t_ms. In the tip arms the host wall is a
FIXED OFFSET after the row's t_recv_ms (`wall_offset_ms`, recorded in the output); the harness asserts every trigger's detect_lag_ms equals it (the stamp is the engine host's clock).

WHAT IT DOES NOT TEST (stated, not hidden). The public socket's own artefacts (failed-tx notices, arrival order, reconnect gaps) do not exist in a replay; gap rows are compared
by count only and the synthetic/exploration rows carry none. The tip tape lacks lp_fee / pool_quote_amount / protocol_fee / creator_fee; the exploration replay rows lack them too,
so both arms price with LP_FRAC (the live public feed carries them: that price-view difference is real and is NOT measured here; L1 design 1, #1180).

USE. As a library (tests, synthetic rows) or as a MiScusi job on EXPLORATION hours only (pre-2026-10-02T10, the h5_shadow replay's own refusals apply; never the tip tape, the
arena tape, a forward-* dir or a sealed hour):
  python3 -m tools.h5_shadow_tip_equiv --tape-dir /data/mal/audit-1008/tape --hours 2026-08-14T12,2026-08-14T13 --scratch-root /data2/mal/scratch --out OUT.json --min-triggers 30
`--min-triggers` (default 30, QP on #1185 (b): a >= 10% mismatch shows in 30 triggers with probability 0.958) is the floor PER ARM, to notebook with the hour list BEFORE the run; if the
span's expected triggers are below it QP rules on a longer span before the job. Exit 0 equal and floor met; 1 not equal; 4 floor not met (NOT a pass; no re-run on another span
without a QP note). Outputs: md5s, field names and structural counts only.
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
import subprocess
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
BOOKKEEPING = ("start", "stop", "hb", "tip_input_start", "tip_input_problem", "tip_input_resumed", "tip_input_halted", "tip_input_stats", "tip_input_stop")  # not decisions
EXIT_OK, EXIT_DIFF, EXIT_FLOOR = 0, 1, 4
MIN_TRIGGERS_DEFAULT = 30  # QP on #1185 (b): P(a >= 10% mismatch shows in 30 triggers) = 1 - 0.9**30 = 0.958; the hour list is notebooked before the job


def _hour(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def prepare_rows(rows: Iterable[dict]) -> list[dict]:
    """The rows both arms get: the tip tape's view (no fee keys), and a signature (replay rows have none; the adapter needs one, and a trigger record carries it)."""
    out = []
    for r in rows:
        r = {k: v for k, v in r.items() if k not in FEE_KEYS}
        r.setdefault("event_index", 0)  # replay_rows sets 0 on every row; the adapter needs an int
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
    """S* public-mode path: the logic of h5_shadow.replay (stamps = the rows' t_recv_ms) PLUS the clock move live decode_notice makes when a pool is announced: a CreatePool
    notice that carries no PumpSwap print calls Engine.advance(slot, recv_ms) (tools/h5_shadow.py decode_notice, `if n == 0: engine.advance`), and h5_shadow.replay does not.
    Without it a pool whose first print comes more than GAP_SLOTS (30) after the previous row has its slot_jump detected one slot later than in the tip arm and the two arms
    differ (reviewer B1 on #1185); the wrapper is the faithful side. tools/h5_shadow.py is not changed."""
    recs: list[dict] = []
    engine = make_engine(recs.append)
    announced: set[str] = set()
    for row in rows:
        pool = row["pool"]
        if pool not in announced and pool in meta and row["slot"] == meta[pool]["s0"]:  # only a pool's own first print is its announcement (replay's rule)
            announced.add(pool)
            engine.on_create_pool(pool, meta[pool]["mint"], h5.WSOL_MINT, row["slot"] - 1, row["t_recv_ms"])
            engine.request_class(meta[pool]["mint"], None)
            engine.advance(row["slot"] - 1, row["t_recv_ms"], None)
        engine.wall = lambda ms=row["t_recv_ms"]: ms
        engine.on_trade(row)
    engine.close_all("replay_end")
    return recs


def run_tip(tape_dir: Path, make_engine: Callable[[Callable[[dict], None]], h5.Engine], *, wall_offset_ms: int, roots: Sequence[str], start_ms: int = 0) -> tuple[list[dict], dict]:
    """Tip-mode path: TipRowSource over a tape dir (arena layout), TipDriver with the fixed wall offset, drained to the end, then close_all like replay()."""
    recs: list[dict] = []
    eng = make_engine(recs.append)
    src = tip.TipRowSource(tape_dir, start_ms=start_ms, roots=roots, problem_fn=lambda: None)
    src.start()
    drv = tip.TipDriver(eng, src, wall_offset_ms=wall_offset_ms)
    while drv.step(500):
        pass
    eng.close_all("replay_end")
    return recs, {"driver": dict(drv.counters), "source": src.counters()}


def follower_rows(rows: Sequence[dict], meta: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """(migrations, trades) in the FOLLOWER's file format (observe/trade_store walker rows plus `source: tip` and the singular V stamp), t_recv_ms unchanged. One migration row per pool
    (the CompletePumpAmmMigrationEvent tx that creates the pool): mint, pool, quote_mint WSOL, signature, at slot s0 - 1 with the pool's first print's t_recv_ms. WHAT THE REPLAY
    ROWS LACK: the pool's true creation slot and instant (the replay meta only has s0, mint and V), so the creation is placed at the replay's own announce point, s0 - 1."""
    migs: list[dict] = []
    trades: list[dict] = []
    announced: set[str] = set()
    for r in rows:
        pool = r["pool"]
        if pool not in announced and pool in meta and r["slot"] == meta[pool]["s0"]:
            announced.add(pool)
            migs.append({"type": "migration", "mint": meta[pool]["mint"], "pool": pool, "quote_mint": h5.WSOL_MINT, "slot": r["slot"] - 1, "signature": "create-" + pool, "event_index": 0,
                         "tx_index": 0, "block_time": r.get("event_ts"), "t_recv_ms": r["t_recv_ms"], "trader": "u", "source": "tip"})
        w = {**r, "venue": "pumpswap", "source": "tip", "virtual_quote_reserve": None, "block_time": r.get("event_ts")}
        if w.get("tx_index") is None:
            w.pop("tx_index", None)
        trades.append(w)
    return migs, trades


def build_follower_dir(rows: Sequence[dict], meta: dict[str, dict], tip_dir: Path, state_dir: Path) -> dict:
    """A fake tip dir + state dir as the follower leaves them (the qp-1165 arm-T pattern): migrations-/trades-<hour of t_recv_ms>.jsonl, checkpoint.json, status.json with the three
    pinned decoder blobs. The pinned reader, adapter and writer then run on it unchanged."""
    from tools import arena_tip_reader as tr

    tip_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    migs, trades = follower_rows(rows, meta)
    by_file: dict[str, list[bytes]] = collections.defaultdict(list)
    for kind, rs in (("migrations", migs), ("trades", trades)):
        for r in rs:
            by_file[f"{kind}-{_hour(r['t_recv_ms'])}.jsonl"].append((json.dumps(h5.clean(r), separators=(",", ":")) + "\n").encode())
    for name, lines in by_file.items():
        (tip_dir / name).write_bytes(b"".join(lines))
    last_slot = max(int(r["slot"]) for r in trades)
    last_t = max(int(r["t_recv_ms"]) for r in trades)
    (state_dir / "checkpoint.json").write_text(json.dumps({"last_done": last_slot, "last_t_recv_ms": last_t, "gaps": 0, "skipped": 0, "writing": None}))
    now = last_t + 1000
    (state_dir / "status.json").write_text(json.dumps({"ts_ms": now, "decoder_blobs": dict(tr.PINNED_BLOBS), "decoder_blobs_pinned": True, "trade_event_v": True}))
    return {"migrations": len(migs), "trades": len(trades), "files": len(by_file), "status_ts_ms": now, "first_t_ms": min(int(r["t_recv_ms"]) for r in trades), "last_t_ms": last_t,
            "last_slot": last_slot}


def run_writer(tip_dir: Path, state_dir: Path, out_dir: Path, info: dict, scratch_root: str) -> dict:
    """The PINNED writer (tools/arena_tip_tape_writer.Writer over arena_tip_adapter.ArenaTipSource over the pinned arena_tip_reader.TipReader) into `out_dir`, with zstd compaction of
    the hours that close. Returns the code pins it ran with and file counts. The clock is the status instant, then moved past the last hour so it closes."""
    from tools import arena_tip_adapter as ta_
    from tools import arena_tip_reader as tr
    from tools import arena_tip_tape_writer as aw

    clock = [info["status_ts_ms"]]
    hour0 = (info["first_t_ms"] // 3_600_000) * 3_600_000
    reader = tr.TipReader(tip_dir, state_dir, start_ms=hour0, catch_up_hours=0, min_hour="2026-08-01T00", lookback_h=None, fadvise=False)
    src = ta_.ArenaTipSource(reader, state_dir, now_ms=lambda: clock[0], io_hook=lambda: True, require_io_idle=False)
    w = aw.Writer(src, out_dir, start_ms=hour0, catch_up_hours=0, clock=lambda: clock[0], out_roots=[str(Path(scratch_root).resolve())], compress=True, snapshot_s=0.0, status_s=0.0)
    w.start()
    try:
        for _ in range(100_000):
            n = w.step()
            if n == 0 and src.caught_up:
                break
        end = (info["last_t_ms"] // 3_600_000 + 1) * 3_600_000 + 2 * aw.GRACE_MS_DEFAULT  # past the last hour's end + grace: it closes and is compacted
        clock[0] = end
        (state_dir / "checkpoint.json").write_text(json.dumps({"last_done": info["last_slot"], "last_t_recv_ms": end, "gaps": 0, "skipped": 0, "writing": None}))
        (state_dir / "status.json").write_text(json.dumps({"ts_ms": end, "decoder_blobs": dict(tr.PINNED_BLOBS), "decoder_blobs_pinned": True, "trade_event_v": True}))
        for _ in range(5):
            w.step()
    finally:
        w.close()
    names = sorted(p.name for p in out_dir.iterdir() if p.name.startswith("tape-"))
    pins = aw.code_pins()
    return {"pins": {k: (v if k == "decoder_blobs" else v[:8]) for k, v in pins.items()}, "tape_files": len(names), "zst_files": sum(n.endswith(".zst") for n in names),
            "jsonl_files": sum(n.endswith(".jsonl") for n in names), "writer_state": w.state, "writer_problem": w.problem}


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


def _tape_lines(out_dir: Path) -> Iterable[str]:
    """Every line of the derived dir's tape, open hours (.jsonl) and compacted ones (.jsonl.zst, through the zstd CLI)."""
    for p in sorted(out_dir.glob("tape-*.jsonl*")):
        text = p.read_text() if p.name.endswith(".jsonl") else subprocess.run(["zstd", "-dc", "-q", str(p)], check=True, capture_output=True, text=True).stdout
        yield from (ln for ln in text.splitlines() if ln.strip())


def run_equivalence(rows: Iterable[dict], meta: dict[str, dict], make_engine: Callable[[Callable[[dict], None]], h5.Engine], *, wall_offset_ms: int = 2500,
                    scratch_root: str, min_triggers: int = MIN_TRIGGERS_DEFAULT, arms: Sequence[str] = ("writer", "adapter")) -> dict:
    """Both tip arms against ONE public run. ARM `writer` (QP K1): follower-format fake tip dir -> the PINNED writer (reader, adapter, zstd compaction) -> a derived dir -> the tip source;
    s0 and the announcement come from MIGRATION rows. ARM `adapter` (unit level): the adapter's row functions straight into a tape. equal = every arm equal; the floor applies to every arm."""
    prep = prepare_rows(rows)
    root = str(Path(scratch_root).resolve())
    work = Path(tempfile.mkdtemp(dir=scratch_root, prefix="h5tipeq-"))
    results: dict[str, dict] = {}
    extra: dict[str, Any] = {}
    try:
        public = run_public(prep, meta, make_engine)
        if "adapter" in arms:
            tape = work / "adapter-tape"
            write_tape(tape, to_tip_rows(prep, meta))
            tipr, info = run_tip(tape, make_engine, wall_offset_ms=wall_offset_ms, roots=(root,))
            results["adapter"] = {**compare(public, tipr, wall_offset_ms=wall_offset_ms), "tip_info": info}
        if "writer" in arms:
            tipd, stated, outd = work / "follower-tip", work / "follower-state", work / "writer-out"
            built = build_follower_dir(prep, meta, tipd, stated)
            wr = run_writer(tipd, stated, outd, built, scratch_root)
            tipr, info = run_tip(outd, make_engine, wall_offset_ms=wall_offset_ms, roots=(root,))
            tape_types = collections.Counter(json.loads(ln).get("type") for ln in _tape_lines(outd))
            results["writer"] = {**compare(public, tipr, wall_offset_ms=wall_offset_ms), "tip_info": info, "built": built, "writer": wr, "tape_row_types": dict(tape_types)}
            extra["writer_pins"] = wr["pins"]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    trig = min(r["triggers"] for r in results.values()) if results else 0
    return {"schema": SCHEMA, "equal": bool(results) and all(r["equal"] for r in results.values()), "triggers": trig, "min_triggers": min_triggers, "floor_met": trig >= min_triggers,
            "arms": results, "wall_offset_ms": wall_offset_ms, "rows": len(prep), "stamp_keys": list(STAMP_KEYS), "fee_keys_removed": list(FEE_KEYS), **extra}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="md5 decision equivalence: tip input mode vs public-feed replay, same exploration rows (paper only)")
    ap.add_argument("--tape-dir", default=h5.TAPE_DIR)
    ap.add_argument("--work-dir", default=h5.WORK_DIR)
    ap.add_argument("--hours", required=True, help="comma-separated exploration tape hours")
    ap.add_argument("--scratch-root", required=True, help="a dir strictly under /data2/mal/scratch for the synthetic tip tape of the run")
    ap.add_argument("--wall-offset-ms", type=int, default=2500)
    ap.add_argument("--min-triggers", type=int, default=MIN_TRIGGERS_DEFAULT, help="the floor per arm (QP: 30), notebooked with the hour list before the run; exit 4 is not a PASS")
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
    res["outputs"] = "md5s, field names and structural counts only"
    text = json.dumps(res, indent=1, sort_keys=True)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return EXIT_OK if res["equal"] and res["floor_met"] else (EXIT_DIFF if not res["equal"] else EXIT_FLOOR)


if __name__ == "__main__":
    sys.exit(main())
