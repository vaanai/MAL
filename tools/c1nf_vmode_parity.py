#!/usr/bin/env python3
"""#503 quant-proof item 7: the C1-NF shadow's event (live) V path against its const (replay) path, and the restart, on one exploration day.

  identity  Replay the exploration tape twice through Shadow and the real FeatureEngine. "const": each canonical PumpSwap row carries the
            pool's V0 as `virtual_quote_reserve` (as run_replay does). "event": the same rows carry V(t) = V0 as `virtual_quote_reserves` and
            no tip key (the identity day), so the event path has nothing to fall back to. The picks of the decision day must be equal: md5 of
            the canonical pick lines. This ties DEC-026 item 14(a) (shadow against the frozen scorer, const mode) to the live mode.
  restart   DEC-026 item 14(b) in event mode on the same injected tape: the uninterrupted event run against a run restarted at
            --restart-at, whose engine is rebuilt from the --bootstrap-hours before it with decisions off (Shadow.resume_after_bootstrap, as
            run_live does). md5 of the picks with decision_T >= the restart, and every differing pick with whether the uninterrupted run's
            book held its mint at the restart (a restart drops the book: the expected difference class).

Reads only the exploration tape (/data/mal/audit-1008/tape) and the hunt-shared token table (V0 per pool), behind c1nf_shadow.refuse_replay
(exploration hours only, forbidden path tokens). Picks only: every c1nf_outcome record is dropped by the sink before it is kept (never
written, never printed). With no --model, a constant stub model (pred 0.05) is used: the picks are then stage 1 + cap + book + decision
state, and each pick carries the sha256 of its float32 feature vector, so equal md5s mean equal features as well.

  python tools/c1nf_vmode_parity.py --day 2026-09-20 --out-json OUT.json            (one JSON line on stdout as well)
Run as one MiScusi job, memory-capped. Exit 0 = both md5 pairs equal, 1 = a difference, 3 = refused.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import c1nf_shadow as cs  # noqa: E402

COUNTERS = ("rows", "minutes", "decision_rows", "stage1", "picks", "no_decision_state", "invalid_pick", "no_wallet_ledger", "row_errors",
            "feature_errors", "outcome_guard_pre_window", "v0_gap_late")
STUB_PRED = 0.05


class ConstModel:
    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(len(X), STUB_PRED)


class PicksSink(cs.MemorySink):
    """MemorySink that keeps no outcome: a c1nf_outcome record is counted and dropped before it is stored."""

    def __init__(self) -> None:
        super().__init__()
        self.outcomes_dropped = 0

    def write(self, rec: Any) -> None:
        if rec.get("type") == "c1nf_outcome":
            self.outcomes_dropped += 1
            return
        super().write(rec)


def canon_line(p: dict) -> str:
    return json.dumps(p, sort_keys=True, separators=(",", ":"), allow_nan=False)


def picks_md5(picks: Sequence[dict]) -> str:
    return hashlib.md5("".join(canon_line(p) + "\n" for p in picks).encode()).hexdigest()


def compare(a: Sequence[dict], b: Sequence[dict], from_ms: Optional[int] = None, held: Optional[Iterable[str]] = None) -> dict:
    """md5 of each side (decision_T_ms >= from_ms) and the differing picks, keyed (decision_T_ms, mint)."""
    if from_ms is not None:
        a = [p for p in a if p["decision_T_ms"] >= from_ms]
        b = [p for p in b if p["decision_T_ms"] >= from_ms]
    ka = {(p["decision_T_ms"], p["mint"]): canon_line(p) for p in a}
    kb = {(p["decision_T_ms"], p["mint"]): canon_line(p) for p in b}
    held_set = set(held or ())
    diff = sorted(k for k in set(ka) | set(kb) if ka.get(k) != kb.get(k))
    rows = [{"decision_T_ms": t, "mint": m, "side": "both" if (t, m) in ka and (t, m) in kb else ("a_only" if (t, m) in ka else "b_only"),
             "mint_held_at_restart": m in held_set} for t, m in diff]
    return {"a": {"n": len(a), "md5": picks_md5(a)}, "b": {"n": len(b), "md5": picks_md5(b)}, "equal": picks_md5(a) == picks_md5(b),
            "n_diff": len(diff), "n_diff_mint_held": sum(r["mint_held_at_restart"] for r in rows), "diff": rows[:200]}


def run_rows(engine: Any, models: cs.ModelSet, batches: Iterable[Sequence[dict]], *, v_source: str, decide_from: Optional[int],
             decide_to: Optional[int], hour_sps: Optional[dict] = None, restart_s: Optional[int] = None, snap_s: Optional[int] = None) -> dict:
    """One replay pass. restart_s: decisions are off until the first row with block_time >= restart_s, then resume_after_bootstrap() (the
    live restart path). snap_s: the mints the book holds at that instant (re-entry not yet allowed) are returned as held_at_snap."""
    sink = PicksSink()
    sh = cs.Shadow(engine, models, sink, replay=True, seal_start_ms=None, decide_from=decide_from, decide_to=decide_to, v_source=v_source)
    sh.clock.hour_sps.update(hour_sps or {})
    if restart_s is not None:
        sh.decide_enabled = False
    held: Optional[list[str]] = None
    t0 = time.monotonic()
    for batch in batches:
        for r in batch:
            bt = r.get("block_time")
            if isinstance(bt, int):
                if restart_s is not None and not sh.decide_enabled and bt >= restart_s:
                    sh.resume_after_bootstrap()
                if snap_s is not None and held is None and bt >= snap_s:
                    held = sorted(m for m, ex in sh.book.items() if snap_s < ex + cs.REENTRY_S)
            sh.feed(r, r["_k"])
    sh.finish("replay_end")
    health = engine.health() if hasattr(engine, "health") else {}
    return {"picks": sink.of("c1nf_pick"), "held_at_snap": held, "counters": {k: int(sh.c[k]) for k in COUNTERS},
            "universe": dict(sh.universe.counts), "engine_pool_rejects": health.get("pool_rejects"), "outcomes_dropped": sink.outcomes_dropped,
            "seconds": round(time.monotonic() - t0, 1)}


def _hour(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def _hours(first: datetime, end: datetime) -> list[str]:
    return cs.replay_hours(first.strftime("%Y-%m-%dT%H"), int((end - first).total_seconds() // 3600))


def build_models(args: argparse.Namespace) -> tuple[cs.ModelSet, str]:
    if args.model:
        return cs.ModelSet([{"from_day": "0000-00-00", "file": args.model, "sha256": args.model_sha256}], loader=cs._lgb_loader), "pinned"
    me = str(Path(__file__).resolve())
    sha = hashlib.sha256(Path(me).read_bytes()).hexdigest()
    return cs.ModelSet([{"from_day": "0000-00-00", "file": me, "sha256": sha}], loader=lambda p: ConstModel()), f"stub pred {STUB_PRED}"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--day", default="2026-09-20", help="decision day (UTC)")
    ap.add_argument("--tape-from", default="2026-09-18T23", help="first tape hour of the uninterrupted runs")
    ap.add_argument("--restart-at", default="2026-09-20T12", help="restart instant (whole hour) for item 14(b)")
    ap.add_argument("--bootstrap-hours", type=int, default=26)
    ap.add_argument("--tape", default=cs.TAPE_DIR)
    ap.add_argument("--tokens", default=cs.TOKENS_PARQUET)
    ap.add_argument("--model", default=None)
    ap.add_argument("--model-sha256", default=None)
    ap.add_argument("--ledger-root", default=None)
    ap.add_argument("--what", choices=("all", "identity", "restart"), default="all")
    ap.add_argument("--out-json", default=None)
    args = ap.parse_args(argv)
    try:
        d0 = datetime.strptime(args.day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        d1 = d0 + timedelta(days=1)
        R = _hour(args.restart_at)
        full = _hours(_hour(args.tape_from), d1)
        boot = _hours(R - timedelta(hours=args.bootstrap_hours), d1)
        if not (d0 <= R < d1):
            raise cs.Refused(f"--restart-at {args.restart_at} is not on --day {args.day}")
        if boot[0] < full[0]:
            raise cs.Refused(f"bootstrap start {boot[0]} is before --tape-from {full[0]}")
        cs.refuse_replay([args.tape, args.tokens] + ([args.out_json] if args.out_json else []), full + boot)
    except (cs.Refused, ValueError) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return cs.EXIT_REFUSED
    import pandas as pd  # the audit venv

    tk = pd.read_parquet(args.tokens, columns=["mint", "pool", "v0_lamports"])
    tk = tk[(tk.v0_lamports >= cs.V_LO) & (tk.v0_lamports <= cs.V_HI)]
    pool_v = dict(zip(tk.pool, tk.v0_lamports.astype(float)))
    models, model_kind = build_models(args)
    sps = cs.hour_sps_from_tape(args.tape, full)
    dec_from, dec_to, R_s = int(d0.timestamp()), int(d1.timestamp()), int(R.timestamp())

    def engine(v_source: str) -> Any:
        ledger = cs.AsofDirLedger(args.ledger_root) if args.ledger_root else None
        return cs.build_engine(ledger, v_source=v_source)

    out: dict[str, Any] = {"day": args.day, "tape_from": full[0], "restart_at": args.restart_at, "bootstrap_from": boot[0], "model": model_kind,
                           "ledger": bool(args.ledger_root), "pools_v0": len(pool_v), "runs": {}}
    ev = None
    if args.what in ("all", "identity"):
        const = run_rows(engine(cs.V_SOURCE_REPLAY), models, cs.tape_rows(args.tape, full, pool_v, {}, event_v=False),
                         v_source=cs.V_SOURCE_REPLAY, decide_from=dec_from, decide_to=dec_to, hour_sps=sps)
        gc.collect()
    if args.what in ("all", "identity", "restart"):
        ev = run_rows(engine(cs.V_SOURCE_LIVE), models, cs.tape_rows(args.tape, full, pool_v, {}, event_v=True),
                      v_source=cs.V_SOURCE_LIVE, decide_from=dec_from, decide_to=dec_to, hour_sps=sps, snap_s=R_s)
        gc.collect()
    if args.what in ("all", "identity"):
        out["identity"] = compare(const["picks"], ev["picks"])
        out["runs"]["const"] = {k: v for k, v in const.items() if k != "picks"}
        del const
    if args.what in ("all", "restart"):
        rs = run_rows(engine(cs.V_SOURCE_LIVE), models, cs.tape_rows(args.tape, boot, pool_v, {}, event_v=True),
                      v_source=cs.V_SOURCE_LIVE, decide_from=R_s, decide_to=dec_to, hour_sps=sps, restart_s=R_s)
        out["restart"] = compare(ev["picks"], rs["picks"], from_ms=R_s * 1000, held=ev["held_at_snap"])
        out["restart"]["held_at_restart"] = len(ev["held_at_snap"] or [])
        out["runs"]["event_restarted"] = {k: v for k, v in rs.items() if k != "picks"}
    out["runs"]["event"] = {k: v for k, v in ev.items() if k not in ("picks", "held_at_snap")}
    line = json.dumps(out, sort_keys=True)
    if args.out_json:
        Path(args.out_json).write_text(line + "\n")
    print(line)
    ok = all(out[k]["equal"] for k in ("identity", "restart") if k in out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
