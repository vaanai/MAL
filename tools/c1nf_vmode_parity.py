#!/usr/bin/env python3
"""#503 quant-proof item 7: the C1-NF shadow's event (live) V path against its const (replay) path, and the restart, on one exploration day.

  identity  Replay the exploration tape twice through Shadow and the real FeatureEngine, both with the wallet ledger. "const": each canonical
            PumpSwap row carries the pool's V0 as `virtual_quote_reserve` (as run_replay does). "event": the same rows carry V(t) = V0 as
            `virtual_quote_reserves` and no tip key (the identity day), so the event path has nothing to fall back to. The picks of the decision
            day must be equal and non-empty: md5 of the canonical pick lines. The md5 over every decision row's (T, pool, feature_hash) must be
            equal and non-empty as well. This ties DEC-026 item 14(a) (shadow against the frozen scorer, const mode) to the live mode.
  restart   DEC-026 item 14(b) in event mode on the same injected tape: the uninterrupted event run against a run restarted at
            --restart-at, whose engine is rebuilt with decisions off (Shadow.resume_after_bootstrap, as run_live does). --restart-mode
            anchored (the fix, as run_live's bootstrap_plan): the rebuild replays from the uninterrupted run's first hour (the run's anchor).
            window (job #513's shape): from the --bootstrap-hours before the restart only. both: anchored decides, window is reported as
            `restart_window` (diagnostic). The restart passes when the picks with decision_T >= the restart are non-empty and every differing
            pick is on a mint the uninterrupted run's book held at the restart (a restart drops the book: the expected class), and the
            decision-row md5 from the restart is equal and non-empty. Each restart comparison carries `feature_diff`: which of the 107
            features differ on decision rows from the restart, on how many rows and pools, by group, and by cause class (the mint's create
            or the creator's first create / graduation before the rebuild's first hour). Names and counts only, never a value.
            Job #513 (window, 26 h): 223,998 decision rows on both sides, md5s differ, 260 non-held pick differences.
            --expire-s (default cs.EXPIRE_S, as run_live): engine.expire on the stream clock in every run (Shadow stream_expire_s); 0 = never.

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

    def __init__(self, split_s: Optional[int] = None, keep: bool = False) -> None:
        self.split_s = split_s
        self.keep = keep and split_s is not None
        self.vecs: dict[tuple[int, str], bytes] = {}        # keep: (T, pool) -> float32 vector bytes, T >= split_s (diagnostic, in memory)
        self.info: dict[str, tuple] = {}                     # keep: pool -> (mint create bt, creator's first create / graduation bt)
        self._eng: Any = None
        self._all, self._tail = hashlib.md5(), hashlib.md5()
        self.n = self.n_tail = 0
        self._T: Optional[int] = None
        self._buf: list[str] = []

    def wrap(self, fn: Callable[..., Any], engine: Any = None) -> Callable[..., Any]:
        self._eng = engine

        def features_at(pool: str, T: int, sd: Any = None) -> Any:
            res = fn(pool, T, sd)
            try:
                f = cs.unpack_features(res)
            except Exception:  # noqa: BLE001 - the shadow unpacks it again and counts feature_errors
                return res
            if f is not None:
                v32 = np.asarray(f.vec).astype(np.float32)
                self.add(T, pool, cs.feature_hash(v32))
                if self.keep and T >= self.split_s:
                    self.vecs[(int(T), pool)] = v32.tobytes()
                    if pool not in self.info:
                        self.info[pool] = pool_history(self._eng, f.mint)
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


def pool_history(eng: Any, mint: Optional[str]) -> tuple:
    """(create bt of the pool's mint or None, earliest create or graduation bt of its creator or None), from the engine's long-memory
    tables at the pool's first decision row from the split. Engine internals, read only; a stub without them gives (None, None)."""
    info = getattr(eng, "_info", {}).get(mint) if mint else None
    if info is None or not getattr(info, "has_create", False):
        return (None, None)
    cr = info.creator
    firsts = [a[0] for a in (getattr(eng, "_by_cr", {}).get(cr), getattr(eng, "_g_cr", {}).get(cr)) if a]
    return (int(info.cbt), int(min(firsts)) if firsts else None)


def feature_diff(a: dict, b: dict, a_info: dict, rebuild_from_s: int) -> dict:
    """Decision rows from the restart, uninterrupted (a) against restarted (b): which features differ (bitwise float32, NaN == NaN), on how
    many rows and distinct pools, by feature group, and by cause class of the pool in the uninterrupted engine: `mint_create_before_rebuild`
    (its mint's create is older than the rebuild's first hour, or was never seen), `creator_history_before_rebuild` (its creator created or
    graduated a mint before then), else `other`. Names and counts only."""
    from tools.c1nf_features import FEATURE_NAMES, feature_group

    by_f: dict[str, list] = {}
    cause: dict[str, int] = {}
    pools: set[str] = set()
    n_diff = 0
    for k in sorted(set(a) & set(b)):
        if a[k] == b[k]:
            continue
        va, vb = np.frombuffer(a[k], dtype=np.float32), np.frombuffer(b[k], dtype=np.float32)
        bad = np.flatnonzero(~((va == vb) | (np.isnan(va) & np.isnan(vb))))
        if not len(bad):
            continue
        n_diff += 1
        pools.add(k[1])
        for i in bad:
            e = by_f.setdefault(FEATURE_NAMES[i], [0, set()])
            e[0] += 1
            e[1].add(k[1])
        cbt, crf = a_info.get(k[1], (None, None))
        c = ("mint_create_before_rebuild" if (cbt is None or cbt < rebuild_from_s) else
             "creator_history_before_rebuild" if (crf is not None and crf < rebuild_from_s) else "other")
        cause[c] = cause.get(c, 0) + 1
    groups: dict[str, int] = {}
    for name in by_f:
        groups[feature_group(name)] = groups.get(feature_group(name), 0) + by_f[name][0]
    return {"rows_compared": len(set(a) & set(b)), "only_a": len(set(a) - set(b)), "only_b": len(set(b) - set(a)), "rows_differ": n_diff,
            "pools_differ": len(pools), "features_differ": sorted(by_f), "by_feature": {n: {"rows": e[0], "pools": len(e[1])} for n, e in sorted(by_f.items())},
            "by_group_rows": dict(sorted(groups.items())), "by_cause_rows": dict(sorted(cause.items())), "rebuild_from_s": rebuild_from_s}


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
    """EXIT_PASS only when the identity picks are equal AND non-empty with equal, non-empty decision rows, and the restart picks are non-empty
    with every difference on a mint held at the restart (n_diff == n_diff_mint_held) and, when present, equal non-empty decision rows from
    the restart. `restart_window` (diagnostic) never decides."""
    fails: list[str] = []
    for k in ("identity", "restart"):
        c = out.get(k)
        if c is None:
            continue
        if not c["nonempty"]:
            fails.append(f"{k}: empty picks (n {c['a']['n']} / {c['b']['n']})")
        elif k == "identity" and not c["equal"]:
            fails.append(f"{k}: picks differ (n_diff {c['n_diff']})")
        elif k == "restart" and c["n_diff"] != c["n_diff_mint_held"]:
            fails.append(f"{k}: picks differ off the held mints (n_diff {c['n_diff']}, held {c['n_diff_mint_held']})")
    dr = (out.get("identity") or {}).get("decision_rows")
    if dr is not None and not (dr["nonempty"] and dr["equal"]):
        fails.append(f"identity: decision rows {'differ' if dr['nonempty'] else 'empty'} (n {dr['a']['n']} / {dr['b']['n']})")
    rs = out.get("restart")
    if rs is not None and rs["nonempty"] and rs.get("decision_rows") is not None and not (rs["decision_rows"]["nonempty"] and rs["decision_rows"]["equal"]):
        rd = rs["decision_rows"]
        fails.append(f"restart: decision rows {'differ' if rd['nonempty'] else 'empty'} (n {rd['a']['n']} / {rd['b']['n']})")
    if not any(k in out for k in ("identity", "restart")):
        fails.append("nothing compared")
    return (EXIT_FAIL if fails else EXIT_PASS), fails


def run_rows(engine: Any, models: cs.ModelSet, batches: Iterable[Sequence[dict]], *, v_source: str, decide_from: Optional[int],
             decide_to: Optional[int], hour_sps: Optional[dict] = None, restart_s: Optional[int] = None, snap_s: Optional[int] = None,
             stream_expire_s: Optional[int] = None, keep_vectors: bool = False) -> dict:
    """One replay pass. restart_s: decisions are off until the first row with block_time >= restart_s, then resume_after_bootstrap() (the
    live restart path). snap_s: the mints the book holds at that instant (re-entry not yet allowed) are returned as held_at_snap, and the
    decision-row md5 from snap_s on is kept as well."""
    tap = DecisionTap(split_s=snap_s, keep=keep_vectors)
    engine.features_at = tap.wrap(engine.features_at, engine)
    sink = PicksSink()
    sh = cs.Shadow(engine, models, sink, replay=True, seal_start_ms=None, decide_from=decide_from, decide_to=decide_to, v_source=v_source,
                   stream_expire_s=stream_expire_s)
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
    counters = {k: int(sh.c[k]) for k in COUNTERS + ("stream_expires", "expire_errors")}
    vecs, info = tap.vecs, tap.info
    tap._eng = None
    return {"picks": sink.of("c1nf_pick"), "held_at_snap": held, "counters": counters, "decision_rows": rows, "vectors": vecs, "pool_info": info,
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
    ap.add_argument("--restart-mode", choices=("anchored", "window", "both"), default="anchored",
                    help="anchored: rebuild from --tape-from (run_live's anchor, the fix); window: the --bootstrap-hours only (#513); both: anchored decides")
    ap.add_argument("--expire-s", type=int, default=cs.EXPIRE_S, help="stream-clock engine.expire in every run, as run_live; 0 = never")
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

    exp_s = args.expire_s or None
    out: dict[str, Any] = {"day": args.day, "tape_from": full[0], "restart_at": args.restart_at, "bootstrap_from": boot[0], "model": model_kind,
                           "restart_mode": args.restart_mode, "stream_expire_s": exp_s,
                           "ledger_root": str(args.ledger_root), "pools_v0": len(pool_v), "runs": {}}
    ev = None
    if args.what in ("all", "identity"):
        const = run_rows(engine("const", cs.V_SOURCE_REPLAY), models, cs.tape_rows(args.tape, full, pool_v, {}, event_v=False),
                         v_source=cs.V_SOURCE_REPLAY, decide_from=dec_from, decide_to=dec_to, hour_sps=sps, stream_expire_s=exp_s)
        const.pop("vectors", None)
        gc.collect()
    if args.what in ("all", "identity", "restart"):
        ev = run_rows(engine("event", cs.V_SOURCE_LIVE), models, cs.tape_rows(args.tape, full, pool_v, {}, event_v=True),
                      v_source=cs.V_SOURCE_LIVE, decide_from=dec_from, decide_to=dec_to, hour_sps=sps, snap_s=R_s, stream_expire_s=exp_s,
                      keep_vectors=args.what in ("all", "restart"))
        gc.collect()
    if args.what in ("all", "identity"):
        out["identity"] = compare(const["picks"], ev["picks"])
        out["identity"]["decision_rows"] = compare_rows(const["decision_rows"], {k: ev["decision_rows"][k] for k in ("n", "md5")})
        out["runs"]["const"] = {k: v for k, v in const.items() if k not in ("picks", "pool_info")}
        del const
    if args.what in ("all", "restart"):
        modes = {"anchored": ["anchored"], "window": ["window"], "both": ["window", "anchored"]}[args.restart_mode]
        for mode in modes:
            hrs = full if mode == "anchored" else boot
            name = "event_restarted" if mode == "anchored" else "event_restarted_window"
            rs = run_rows(engine(name, cs.V_SOURCE_LIVE), models, cs.tape_rows(args.tape, hrs, pool_v, {}, event_v=True),
                          v_source=cs.V_SOURCE_LIVE, decide_from=R_s, decide_to=dec_to, hour_sps=sps, restart_s=R_s, stream_expire_s=exp_s,
                          keep_vectors=True)
            c = compare(ev["picks"], rs["picks"], from_ms=R_s * 1000, held=ev["held_at_snap"])
            c["mode"], c["rebuild_from"] = mode, hrs[0]
            c["held_at_restart"] = len(ev["held_at_snap"] or [])
            c["decision_rows"] = compare_rows(ev["decision_rows"]["from_split"], rs["decision_rows"])
            c["feature_diff"] = feature_diff(ev["vectors"], rs["vectors"], ev["pool_info"], int(_hour(hrs[0]).timestamp()))
            key = "restart" if (mode == "anchored" or args.restart_mode == "window") else "restart_window"
            out[key] = c
            out["runs"][name] = {k: v for k, v in rs.items() if k not in ("picks", "vectors", "pool_info")}
            del rs
            gc.collect()
    out["runs"]["event"] = {k: v for k, v in ev.items() if k not in ("picks", "held_at_snap", "vectors", "pool_info")}
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
