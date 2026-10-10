#!/usr/bin/env python3
"""#503 quant-proof item 7: the C1-NF shadow's event (live) V path against its const (replay) path, and the restart, on one exploration day.

  identity  Replay the exploration tape twice through Shadow and the real FeatureEngine, both with the wallet ledger. "const": each canonical
            PumpSwap row carries the pool's V0 as `virtual_quote_reserve` (as run_replay does). "event": the same rows carry V(t) = V0 as
            `virtual_quote_reserves` and no tip key (the identity day), so the event path has nothing to fall back to. The picks of the decision
            day must be equal and non-empty: md5 of the canonical pick lines. The md5 over every decision row's (T, pool, feature_hash) must be
            equal and non-empty as well. This ties DEC-026 item 14(a) (shadow against the frozen scorer, const mode) to the live mode.
  restart   DEC-026 item 14(b) in event mode on the same injected tape: the uninterrupted event run against a run restarted at
            --restart-at, whose engine is rebuilt from the --bootstrap-hours before it with decisions off (Shadow.resume_after_bootstrap, as
            run_live does). md5 of the picks with decision_T >= the restart (equal and non-empty), and every differing pick with whether the
            uninterrupted run's book held its mint at the restart (a restart drops the book: the expected difference class). The decision-row
            md5 from the restart is printed for both runs but does not decide the exit: a pool that opened before the bootstrap start is not in
            the restarted engine.

--ledger-root is REQUIRED: the #502 as-of root (tools/c1nf_wallet_ledger.py output, docs/runbooks/c1nf-wallet-ledger.md) holding
asof/asof-<--day>, built from exploration days only. Without it every decision row is wallet_ok=False (`no_wallet_ledger`), nothing is picked,
and the comparison is empty: job #505 at b48b5ba ran that way (n = 0 on both sides, md5 d41d8cd98f00b204e9800998ecf8427e = md5 of nothing).
The run refuses (exit 3) before reading anything when the root or asof-<--day> is missing.

Reads only the exploration tape (/data/mal/audit-1008/tape), the hunt-shared token table (V0 per pool) and the as-of ledger, behind
c1nf_shadow.refuse_replay (exploration hours only, forbidden path tokens). Picks and decision-row hashes only: every c1nf_outcome record is
dropped by the sink before it is kept (never written, never printed). With no --model, a constant stub model (pred 0.05) is used: the picks
are then stage 1 + cap + book + decision state, and each pick carries the sha256 of its float32 feature vector.

  python tools/c1nf_vmode_parity.py --day 2026-09-20 --ledger-root ROOT --out-json OUT.json     (one JSON line on stdout as well)
Run as one MiScusi job, memory-capped. Exit 0 = PASS (both pick md5 pairs equal and non-empty, identity decision rows equal and non-empty);
1 = FAIL (a difference, or an empty side: n = 0 is a FAIL, never a pass); 3 = refused.
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
from typing import Any, Callable, Iterable, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import c1nf_shadow as cs  # noqa: E402

COUNTERS = ("rows", "minutes", "decision_rows", "not_decidable", "stage1", "picks", "no_decision_state", "invalid_pick", "no_wallet_ledger",
            "row_errors", "feature_errors", "outcome_guard_pre_window", "v0_gap_late")
STUB_PRED = 0.05
EXIT_PASS, EXIT_FAIL = 0, 1


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


class DecisionTap:
    """md5 over every decision row's (T, pool, feature_hash): each features_at answer that Shadow._decide counts in `decision_rows` (not None),
    whatever its wallet_ok, stage 1 or pick. Lines are "T,pool,feature_hash\\n" (T in seconds, feature_hash = sha256 of the float32 vector as
    on a pick); the rows of one T are hashed sorted. With split_s, a second md5 covers the rows with T >= split_s."""

    def __init__(self, split_s: Optional[int] = None) -> None:
        self.split_s = split_s
        self._all, self._tail = hashlib.md5(), hashlib.md5()
        self.n = self.n_tail = 0
        self._T: Optional[int] = None
        self._buf: list[str] = []

    def wrap(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def features_at(pool: str, T: int, sd: Any = None) -> Any:
            res = fn(pool, T, sd)
            try:
                f = cs.unpack_features(res)
            except Exception:  # noqa: BLE001 - the shadow unpacks it again and counts feature_errors
                return res
            if f is not None:
                self.add(T, pool, cs.feature_hash(np.asarray(f.vec).astype(np.float32)))
            return res

        return features_at

    def add(self, T: int, pool: str, fh: str) -> None:
        if T != self._T:
            self.flush()
            self._T = T
        self._buf.append(f"{T},{pool},{fh}\n")

    def flush(self) -> None:
        if self._buf:
            b = "".join(sorted(self._buf)).encode()
            self._all.update(b)
            self.n += len(self._buf)
            if self.split_s is not None and self._T is not None and self._T >= self.split_s:
                self._tail.update(b)
                self.n_tail += len(self._buf)
        self._buf = []

    def summary(self) -> dict:
        self.flush()
        out: dict[str, Any] = {"n": self.n, "md5": self._all.hexdigest()}
        if self.split_s is not None:
            out["from_split"] = {"n": self.n_tail, "md5": self._tail.hexdigest()}
        return out


def canon_line(p: dict) -> str:
    return json.dumps(p, sort_keys=True, separators=(",", ":"), allow_nan=False)


def picks_md5(picks: Sequence[dict]) -> str:
    return hashlib.md5("".join(canon_line(p) + "\n" for p in picks).encode()).hexdigest()


def compare(a: Sequence[dict], b: Sequence[dict], from_ms: Optional[int] = None, held: Optional[Iterable[str]] = None) -> dict:
    """md5 of each side (decision_T_ms >= from_ms) and the differing picks, keyed (decision_T_ms, mint). `nonempty` is False when either side
    has no pick: an empty comparison proves nothing and is a FAIL in verdict(), even though its two md5s are equal."""
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
            "nonempty": len(a) > 0 and len(b) > 0, "n_diff": len(diff), "n_diff_mint_held": sum(r["mint_held_at_restart"] for r in rows),
            "diff": rows[:200]}


def compare_rows(a: dict, b: dict) -> dict:
    """Decision-row md5s ({"n", "md5"} each) side by side."""
    return {"a": a, "b": b, "equal": a["md5"] == b["md5"] and a["n"] == b["n"], "nonempty": a["n"] > 0 and b["n"] > 0}


def verdict(out: dict) -> tuple[int, list[str]]:
    """EXIT_PASS only when every pick comparison present is equal AND non-empty, and the identity decision rows are equal and non-empty."""
    fails: list[str] = []
    for k in ("identity", "restart"):
        c = out.get(k)
        if c is None:
            continue
        if not c["nonempty"]:
            fails.append(f"{k}: empty picks (n {c['a']['n']} / {c['b']['n']})")
        elif not c["equal"]:
            fails.append(f"{k}: picks differ (n_diff {c['n_diff']})")
    dr = (out.get("identity") or {}).get("decision_rows")
    if dr is not None and not (dr["nonempty"] and dr["equal"]):
        fails.append(f"identity: decision rows {'differ' if dr['nonempty'] else 'empty'} (n {dr['a']['n']} / {dr['b']['n']})")
    if not any(k in out for k in ("identity", "restart")):
        fails.append("nothing compared")
    return (EXIT_FAIL if fails else EXIT_PASS), fails


def run_rows(engine: Any, models: cs.ModelSet, batches: Iterable[Sequence[dict]], *, v_source: str, decide_from: Optional[int],
             decide_to: Optional[int], hour_sps: Optional[dict] = None, restart_s: Optional[int] = None, snap_s: Optional[int] = None) -> dict:
    """One replay pass. restart_s: decisions are off until the first row with block_time >= restart_s, then resume_after_bootstrap() (the
    live restart path). snap_s: the mints the book holds at that instant (re-entry not yet allowed) are returned as held_at_snap, and the
    decision-row md5 from snap_s on is kept as well."""
    tap = DecisionTap(split_s=snap_s)
    engine.features_at = tap.wrap(engine.features_at)
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
    rows = tap.summary()
    counters = {k: int(sh.c[k]) for k in COUNTERS}
    return {"picks": sink.of("c1nf_pick"), "held_at_snap": held, "counters": counters, "decision_rows": rows,
            "tap_matches_counter": rows["n"] == counters["decision_rows"], "universe": dict(sh.universe.counts),
            "engine_pool_rejects": health.get("pool_rejects"), "outcomes_dropped": sink.outcomes_dropped,
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


def load_pool_v(tokens: str) -> dict[str, float]:
    import pandas as pd  # the audit venv

    tk = pd.read_parquet(tokens, columns=["mint", "pool", "v0_lamports"])
    tk = tk[(tk.v0_lamports >= cs.V_LO) & (tk.v0_lamports <= cs.V_HI)]
    return dict(zip(tk.pool, tk.v0_lamports.astype(float)))


def check_ledger_root(root: Optional[str], day: str) -> Path:
    """The run needs decisions: refuse without the #502 as-of root, or when asof-<day> (the snapshot a decision on `day` reads) is missing."""
    if not root:
        raise cs.Refused("--ledger-root is required: without the as-of wallet ledger every decision row is wallet_ok=False and nothing is "
                         "picked (job #505: n = 0 on both sides)")
    snap = Path(root) / "asof" / f"asof-{day}"
    if not (snap / "MANIFEST.json").is_file():
        raise cs.Refused(f"{snap}/MANIFEST.json missing: build asof-{day} from exploration days (docs/runbooks/c1nf-wallet-ledger.md)")
    return Path(root)


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
    ap.add_argument("--ledger-root", default=None, help="REQUIRED: #502 as-of root with asof/asof-<day> (exploration days only)")
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
        cs.refuse_replay([args.tape, args.tokens] + [x for x in (args.ledger_root, args.out_json) if x], full + boot)
        check_ledger_root(args.ledger_root, args.day)
    except (cs.Refused, ValueError) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return cs.EXIT_REFUSED

    pool_v = load_pool_v(args.tokens)
    models, model_kind = build_models(args)
    sps = cs.hour_sps_from_tape(args.tape, full)
    dec_from, dec_to, R_s = int(d0.timestamp()), int(d1.timestamp()), int(R.timestamp())
    ledgers: dict[str, Any] = {}

    def engine(name: str, v_source: str) -> Any:
        ledgers[name] = cs.AsofDirLedger(args.ledger_root)
        return cs.build_engine(ledgers[name], v_source=v_source)

    out: dict[str, Any] = {"day": args.day, "tape_from": full[0], "restart_at": args.restart_at, "bootstrap_from": boot[0], "model": model_kind,
                           "ledger_root": str(args.ledger_root), "pools_v0": len(pool_v), "runs": {}}
    ev = None
    if args.what in ("all", "identity"):
        const = run_rows(engine("const", cs.V_SOURCE_REPLAY), models, cs.tape_rows(args.tape, full, pool_v, {}, event_v=False),
                         v_source=cs.V_SOURCE_REPLAY, decide_from=dec_from, decide_to=dec_to, hour_sps=sps)
        gc.collect()
    if args.what in ("all", "identity", "restart"):
        ev = run_rows(engine("event", cs.V_SOURCE_LIVE), models, cs.tape_rows(args.tape, full, pool_v, {}, event_v=True),
                      v_source=cs.V_SOURCE_LIVE, decide_from=dec_from, decide_to=dec_to, hour_sps=sps, snap_s=R_s)
        gc.collect()
    if args.what in ("all", "identity"):
        out["identity"] = compare(const["picks"], ev["picks"])
        out["identity"]["decision_rows"] = compare_rows(const["decision_rows"], {k: ev["decision_rows"][k] for k in ("n", "md5")})
        out["runs"]["const"] = {k: v for k, v in const.items() if k != "picks"}
        del const
    if args.what in ("all", "restart"):
        rs = run_rows(engine("event_restarted", cs.V_SOURCE_LIVE), models, cs.tape_rows(args.tape, boot, pool_v, {}, event_v=True),
                      v_source=cs.V_SOURCE_LIVE, decide_from=R_s, decide_to=dec_to, hour_sps=sps, restart_s=R_s)
        out["restart"] = compare(ev["picks"], rs["picks"], from_ms=R_s * 1000, held=ev["held_at_snap"])
        out["restart"]["held_at_restart"] = len(ev["held_at_snap"] or [])
        out["restart"]["decision_rows"] = compare_rows(ev["decision_rows"]["from_split"], rs["decision_rows"])  # reported, not in verdict()
        out["runs"]["event_restarted"] = {k: v for k, v in rs.items() if k != "picks"}
    out["runs"]["event"] = {k: v for k, v in ev.items() if k not in ("picks", "held_at_snap")}
    for name, led in ledgers.items():
        out["runs"].setdefault(name, {})["ledger_stale"] = int(getattr(led, "stale", 0))
    rc, fails = verdict(out)
    out["verdict"], out["fail_reasons"] = ("PASS" if rc == EXIT_PASS else "FAIL"), fails
    line = json.dumps(out, sort_keys=True)
    if args.out_json:
        Path(args.out_json).write_text(line + "\n")
    print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
