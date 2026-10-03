#!/usr/bin/env python3
"""EXP-012 forward latency/size sensitivity re-score (DEC-016 Amendment 3 (a) section 3).

    python3 -m tools.exp012_forward_sensitivity --walk-dir D --out-dir FWD_OUT --k-p50 N --k-p90 M \
        [--size-sol 0.5] [--priority-lamports 500000] [--tip-lamports 0] [--max-concurrent 3] [--result-dir R]

NOT part of the pre-registered read. It decides nothing about the FINAL verdict, makes no edge claim,
and cannot turn a FAIL into a PASS: it is run once, after the FINAL read, on the FINAL window's rows.

Order of operations (nothing about P&L is printed or written before step 2 passes):
  1. Seal. Refuses unless the FINAL read for the window is recorded: FINAL_READS.jsonl (the same
     external ledger `exp012_forward` uses) holds a marker for this out-dir, the out-dir's
     final_read.lock exists and matches the marker, and rows.jsonl still hashes to the lock.
     The pinned window [2026-10-06T00, 2026-10-16T00) is required unless --test-window.
  2. Reproduction. The window's sealed hours go through `exp012_forward.score_hours` (the forward
     scorer's own table build and scoring path, by import) at k=1, bound start, 0.5 SOL, 500,000
     lamports, no concurrency cap. Every FINAL row's `flat`, `press` (and `entered`) must match
     rows.jsonl byte for byte, keyed by (mint, mig_ms), with no extra or missing key. Any
     mismatch refuses (exit 2), names fields only, and writes nothing.
  3. Re-score. Same tape pass machinery (`exp012_score.load_rows` with a worker like
     `exp012_forward._tagged_worker`), for the two k values, over the FINAL `entered` set:
       - entry at slot + k, ENTRY_BOUND "end" (`score_one(entry_land_k=k, entry_bound="end")`);
       - every exit fill delayed k - 1 further slots (see "How the exit delay is applied");
       - size, priority and tip as given (tip is added to the per-side priority fee);
       - a position cap applied in decision order (mig_ms, mint).
  4. Gates. k(p50): the full promotion gate under both fail models (`exp011_score.compute_gate`).
     k(p90): mean > 0 and ex-top-3 > 0 under both fail models. Verdict SUPPORTS_LIVE only if both hold.

How the exit delay is applied: the frozen exit logic is not edited. During the re-score only, inside
the worker process and restored afterwards, `exploration_exits.ENTRY_LAND_K` is set to k (the trigger
print's exit lands at its slot + k, bound "start", exactly `_delayed`'s own rule), and `_state_at`
(the time-cap exit's state lookup) is wrapped so that for k > 1 the cap exit sees the later of the
frozen state and the last print before (that state's slot + k). At k = 1 nothing is changed.

Concurrency: a mint's decision time is mig_ms. A position occupies a slot from its decision time to its
exit time (the exit state's t_recv_ms, at least the landing time). Entered rows are walked in (mig_ms,
mint) order; a row that arrives with `max_concurrent` positions open is skipped and counted. A
no-fill row (status MISS) holds no position but is still subject to the cap check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import tools.exp011_score as e11
import tools.exploration_entry_model as eem
import tools.exploration_exits as ee
import tools.exp012_score as s12
import tools.exp012_forward as fw
import tools.forward_family as ff
from tools.latency_curve import _state_index

SCHEMA = "exp012_forward_sensitivity_v1"
LABEL = "forward simulated sensitivity re-score, not money made, not an edge claim"
SUPPORTS = "SUPPORTS_LIVE"
DOES_NOT_SUPPORT = "DOES_NOT_SUPPORT_LIVE"
RESULT_JSON = "sensitivity.json"
RESULT_MD = "sensitivity.md"
DETAIL_NAME = "rows_detail.jsonl"
REPRO_NAME = "reproduction.json"
LAMPORTS = fw.LAMPORTS
REPRO_K = 1
REPRO_BOUND = "start"
REPRO_SIZE_SOL = 0.5
REPRO_PRIORITY = 500_000
ENTRY_BOUND_SENS = "end"
DEFAULT_SIZE_SOL = 0.5
DEFAULT_PRIORITY = 500_000
DEFAULT_TIP = 0
DEFAULT_MAX_CONCURRENT = 3
CAP_MS = 30 * 60 * 1000  # the tp50_sl30 exit cap (exploration_exits), used only for a missing exit state


# --- seal (Amendment 2) ---------------------------------------------------------------------


def check_sealed(out_dir: Path, ledger: Path | None, clean_clock: datetime, read_end: datetime, test_window: bool) -> dict[str, Any]:
    """Refuse unless the FINAL read for this window is recorded in the external ledger and the lock. Returns the lock."""
    fw.check_window(clean_clock, read_end, test_window)
    cc_s, re_s = fw._wins(clean_clock, read_end)
    if ledger is None:
        raise fw.Refused(["a FINAL ledger is required (--final-ledger)"])
    lock_path = out_dir / fw.LOCK_NAME
    markers = [
        m
        for m in fw.ledger_markers(ledger)
        if m.get("final") and (m.get("clean_clock"), m.get("read_end")) == (cc_s, re_s)
        and bool(m.get("test_window")) == test_window
        and m.get("experiment", ff.PRIMARY_EXPERIMENT) == ff.PRIMARY_EXPERIMENT
        and m.get("out_dir") == str(out_dir.resolve())
    ]
    if not markers:
        raise fw.Refused([f"no FINAL read for [{cc_s}, {re_s}) (test_window={test_window}) from {out_dir} is recorded in {ledger}; the sensitivity re-score runs only after the FINAL read"])
    if not lock_path.is_file():
        raise fw.Refused([f"a FINAL marker exists but {lock_path} is missing"])
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if (lock.get("clean_clock"), lock.get("read_end"), bool(lock.get("test_window"))) != (cc_s, re_s, test_window):
        raise fw.Refused([f"{lock_path} was taken for a different window or test_window flag"])
    if not any(m.get("lock_sha256") == fw._sha256_file(lock_path) for m in markers):
        raise fw.Refused([f"{lock_path} does not match the lock sha256 in the FINAL ledger marker"])
    if fw._rows_sha256(out_dir) != lock.get("rows_sha256"):
        raise fw.Refused([f"{out_dir / fw.ROWS_NAME} no longer hashes to the FINAL lock's rows_sha256"])
    return lock


# --- reproduction ---------------------------------------------------------------------------


def _fl(x: Any) -> str:
    return json.dumps(float(x))


def compare_rows(stored: Sequence[dict[str, Any]], fresh: Sequence[dict[str, Any]]) -> list[str]:
    """Reasons (field names and counts only, never values) why `fresh` does not reproduce `stored`."""
    have = {fw.key_of(r): r for r in stored}
    got = {fw.key_of(r): r for r in fresh}
    out: list[str] = []
    missing, extra = sorted(set(have) - set(got)), sorted(set(got) - set(have))
    if missing:
        out.append(f"{len(missing)} FINAL row(s) not reproduced, first: mint {missing[0][0]} mig_ms {missing[0][1]}")
    if extra:
        out.append(f"{len(extra)} recomputed row(s) not in the FINAL rows, first: mint {extra[0][0]} mig_ms {extra[0][1]}")
    bad: dict[str, int] = {}
    first: dict[str, tuple[str, int]] = {}
    for k in sorted(set(have) & set(got)):
        a, b = have[k], got[k]
        for f in ("flat", "press"):
            if _fl(a[f]) != _fl(b[f]):
                bad[f] = bad.get(f, 0) + 1
                first.setdefault(f, k)
        if bool(a["entered"]) != bool(b["entered"]):
            bad["entered"] = bad.get("entered", 0) + 1
            first.setdefault("entered", k)
    for f, n in sorted(bad.items()):
        out.append(f"field {f!r} differs in {n} row(s), first: mint {first[f][0]} mig_ms {first[f][1]} (values withheld)")
    return out


def window_pool(runs: Sequence[dict[str, Any]], read_end: datetime) -> tuple[list[str], datetime]:
    last = fw.score_runs(runs)[-1:] or [None]
    if last[0] is None:
        raise fw.Refused(["runs.jsonl records no score run"])
    pf, to = last[0].get("pool_from"), last[0].get("to_exclusive")
    if not pf or not to:
        raise fw.Refused(["the last score run has no pool_from / to_exclusive"])
    if to < fw.hour_key(read_end + timedelta(hours=1)):
        raise fw.Refused([f"the last score run reached {to}, short of read end + 1 h"])
    return e11._hours_range(pf, to), fw.hour_dt(to)


# --- the re-score worker (tape pass) --------------------------------------------------------


@dataclass(frozen=True)
class SensHours(fw.ForwardHours):
    ks: tuple[int, ...] = (1,)
    size: int = 500_000_000
    priority: int = 500_000  # per side, tip included


class _Capture:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.fills: Any = None
        self.trigger_slot: int | None = None
        self.target: int | None = None
        self.state_idx: int | None = None
        self.landing_ms: int | None = None
        self.exit_idx: int | None = None
        self.hit = False
        self.p_press: float | None = None


def _detail(cap: _Capture, row: dict[str, Any]) -> dict[str, Any]:
    fills = cap.fills
    d: dict[str, Any] = {"entry_target_slot": cap.target, "entry_state_slot": None, "entry_spot_sol": None, "exit_state_slot": None, "exit_spot_sol": None, "exit_reason": "no_fill", "pressure_prob": None, "landing_ms": cap.landing_ms, "exit_ms": None}
    if fills is None or cap.state_idx is None or cap.state_idx < 0 or not row["filled"]:
        return d
    st = fills[cap.state_idx]
    d["entry_state_slot"], d["entry_spot_sol"] = int(st.slot), float(st.price_sol)
    d["pressure_prob"] = cap.p_press
    if cap.exit_idx is None:
        d["exit_reason"] = "unknown"
    elif cap.exit_idx < 0:
        d["exit_reason"] = "exit_state_missing"
        d["exit_ms"] = int((cap.landing_ms or 0) + CAP_MS)
    else:
        ex = fills[cap.exit_idx]
        d["exit_state_slot"], d["exit_spot_sol"] = int(ex.slot), float(ex.price_sol)
        d["exit_reason"] = "tpsl_trigger" if cap.hit else "time_cap"
        d["exit_ms"] = int(max(ex.t_recv_ms, cap.landing_ms or 0))
    return d


def _sens_worker(worker_id: int, home: list[str], buf: list[str], creator_hist: dict[str, list[int]], rows_out_path: Path | None, hours: SensHours) -> list[dict[str, Any]]:
    """`exp012_forward._tagged_worker`'s twin for the re-score. `eem.score_one` is wrapped to run the
    frozen `score_one` once per k with the trial terms and to tag each row with mig_ms, k and capture
    details; the hooks below only record or (for the exit delay) shift a state lookup. All restored."""
    orig_score_one = eem.score_one
    spec = [s for s in eem.build_specs() if s["id"] == e11.TARGET_SPEC_ID]
    if len(spec) != 1:
        raise SystemExit("tp50_sl30 spec not found")
    cap = _Capture()
    saved = {
        ("eem", "_fills_for"): eem._fills_for,
        ("eem", "_state_index"): eem._state_index,
        ("eem", "_slot_time"): eem._slot_time,
        ("eem", "mixed_net"): eem.mixed_net,
        ("ee", "_one_sell_close"): ee._one_sell_close,
        ("ee", "_delayed"): ee._delayed,
        ("ee", "_state_at"): ee._state_at,
        ("ee", "ENTRY_LAND_K"): ee.ENTRY_LAND_K,
    }
    cur = {"k": 1}

    def fills_for(*a: Any, **kw: Any) -> Any:
        res = saved[("eem", "_fills_for")](*a, **kw)
        cap.fills, cap.trigger_slot = res[0], res[1]
        return res

    def state_index(fills: Any, slot: int, bound: str) -> int:
        idx = saved[("eem", "_state_index")](fills, slot, bound)
        if cap.target is None:
            cap.target, cap.state_idx = slot, idx
        return idx

    def slot_time(*a: Any, **kw: Any) -> Any:
        res = saved[("eem", "_slot_time")](*a, **kw)
        if cap.landing_ms is None:
            cap.landing_ms = int(res)
        return res

    def mixed_net(net0: int, pri_sides: int, status: int, priority: int, p_fail: float) -> float:
        cap.p_press = float(p_fail)  # score_one calls this for the flat leg, then the pressure leg: the last call wins
        return saved[("eem", "mixed_net")](net0, pri_sides, status, priority, p_fail)

    def one_sell_close(fills: Any, state_idx: int, *a: Any, **kw: Any) -> Any:
        cap.exit_idx = state_idx
        return saved[("ee", "_one_sell_close")](fills, state_idx, *a, **kw)

    def delayed(*a: Any, **kw: Any) -> Any:
        cap.hit = True
        return saved[("ee", "_delayed")](*a, **kw)

    def state_at(fills: Any, t_ms: int) -> int:
        idx = saved[("ee", "_state_at")](fills, t_ms)
        k = cur["k"]
        if k <= 1 or idx < 0:
            return idx
        return max(idx, _state_index(fills, int(fills[idx].slot) + k, ee.ENTRY_BOUND))

    def tagged(mint_id: str, mint: Any, feat: Any, curve: Any, through_ms: int, creator_hist_: Any, **_k: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for k in hours.ks:
            cap.reset()
            cur["k"] = k
            ee.ENTRY_LAND_K = k
            rows = orig_score_one(mint_id, mint, feat, curve, through_ms, creator_hist_, specs=spec, size=hours.size, priority=hours.priority, entry_land_k=k, entry_bound=ENTRY_BOUND_SENS)
            for r in rows:
                r.pop("features", None)
                r["mig_ms"] = int(mint.mig_ms)
                r["entry_land_k"] = k
                r.update(_detail(cap, r))
                out.append(r)
        return out

    eem.score_one = tagged
    eem._fills_for, eem._state_index, eem._slot_time, eem.mixed_net = fills_for, state_index, slot_time, mixed_net
    ee._one_sell_close, ee._delayed, ee._state_at = one_sell_close, delayed, state_at
    try:
        return s12._run_worker(worker_id, home, buf, creator_hist, rows_out_path, hours)
    finally:
        eem.score_one = orig_score_one
        eem._fills_for, eem._state_index, eem._slot_time, eem.mixed_net = saved[("eem", "_fills_for")], saved[("eem", "_state_index")], saved[("eem", "_slot_time")], saved[("eem", "mixed_net")]
        ee._one_sell_close, ee._delayed, ee._state_at = saved[("ee", "_one_sell_close")], saved[("ee", "_delayed")], saved[("ee", "_state_at")]
        ee.ENTRY_LAND_K = saved[("ee", "ENTRY_LAND_K")]


def sensitivity_rows(walk_dir: Path, pool: Sequence[str], ks: Sequence[int], size_lamports: int, per_side_lamports: int, scratch: Path) -> list[dict[str, Any]]:
    hours = SensHours(str(walk_dir), frozenset(pool), None, tuple(ks), int(size_lamports), int(per_side_lamports))
    plan = fw.anchored_plan(pool, s12.MAX_HOME_HOURS, s12.BUFFER_HOURS)
    return s12.load_rows(hours, s12.MAX_WORKERS, s12.BUFFER_HOURS, s12.MAX_HOME_HOURS, scratch, pool_hours=list(pool), worker_fn=_sens_worker, plan=plan)


# --- concurrency cap and gates --------------------------------------------------------------


def apply_cap(rows: Sequence[dict[str, Any]], max_concurrent: int | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(kept, skipped), both in decision order (mig_ms, mint). `max_concurrent` None = no cap.
    A kept filled row holds a slot from mig_ms to its exit_ms; a row arriving with max_concurrent
    open slots is skipped, whatever its own outcome would have been."""
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    open_until: list[int] = []
    for r in sorted(rows, key=lambda x: (int(x["mig_ms"]), x["mint"])):
        t = int(r["mig_ms"])
        open_until = [e for e in open_until if e > t]
        if max_concurrent is not None and len(open_until) >= max_concurrent:
            skipped.append(r)
            continue
        kept.append(r)
        if r.get("filled"):
            ex = r.get("exit_ms")
            open_until.append(int(ex) if ex is not None else t + CAP_MS)
    return kept, skipped


def _leg_view(g: dict[str, Any]) -> dict[str, Any]:
    return fw._leg(g)


def book_legs(book: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not book:
        empty = {"n": 0, "mean_sol": None, "total_sol": None, "mean_ci90_sol": None, "total_ex_top3_sol": None, "days_positive": 0, "n_days": 0, "per_day": [], "blockers": ["n < 100", "no trades"], "clears_gate": False}
        return {"flat_15": dict(empty), "pressure_scale_1": dict(empty), "promote": False}
    g = e11.compute_gate(book)
    return {
        "flat_15": _leg_view(g),
        "pressure_scale_1": _leg_view(e11.compute_gate(fw._swap(book))),
        "promote": bool(g.get("promote")),
        "promote_flat_15": bool(g.get("promote_flat_15", False)),
        "promote_pressure_1": bool(g.get("promote_pressure_1", False)),
    }


def _pos(x: Any) -> bool:
    return x is not None and x > 0


def p90_pass(legs: dict[str, Any]) -> bool:
    return all(_pos(legs[m]["mean_sol"]) and _pos(legs[m]["total_ex_top3_sol"]) for m in ("flat_15", "pressure_scale_1"))


def decide(p50: dict[str, Any], p90: dict[str, Any]) -> dict[str, Any]:
    """The rule of DEC-016 Amendment 3 (a) 4: (i) full gate at k(p50), both models; (ii) at k(p90) mean > 0 and ex-top-3 > 0, both models."""
    i_ok = bool(p50["promote"])
    ii_ok = p90_pass(p90)
    return {"p50_full_gate": i_ok, "p90_mean_and_ex_top3_positive": ii_ok, "verdict": SUPPORTS if (i_ok and ii_ok) else DOES_NOT_SUPPORT}


# --- orchestration --------------------------------------------------------------------------


def _atomic_text(path: Path, text: str) -> None:
    fw.atomic_write(path, text.encode("utf-8"))


def reproduce(walk_dir: Path, out_dir: Path, artifact_dir: Path, pool: Sequence[str], to: datetime, clean_clock: datetime, read_end: datetime, scratch: Path) -> dict[str, Any]:
    """Re-run the FINAL window at k=1, start, 0.5 SOL, 500,000, no cap through the forward scorer's own path. Raises Refused on any mismatch."""
    rows, threshold = fw.score_hours(walk_dir, pool, artifact_dir, scratch)
    lo, hi = fw.ms(clean_clock), min(fw.ms(to), fw.ms(read_end))
    fresh = [fw.make_row(r, threshold) for r in rows if lo <= int(r["mig_ms"]) < hi]
    stored = fw.read_rows(out_dir / fw.ROWS_NAME)
    problems = compare_rows(stored, fresh)
    if problems:
        raise fw.Refused(["reproduction check failed, nothing written: " + "; ".join(problems[:5])])
    canon = "".join(f"{k[0]}\t{k[1]}\t{_fl(r['flat'])}\t{_fl(r['press'])}\n" for k, r in sorted((fw.key_of(r), r) for r in stored))
    return {"n_rows": len(stored), "n_entered": sum(1 for r in stored if r["entered"]), "flat_press_sha256": hashlib.sha256(canon.encode()).hexdigest(), "k": REPRO_K, "bound": REPRO_BOUND, "size_sol": REPRO_SIZE_SOL, "priority_lamports": REPRO_PRIORITY, "max_concurrent": None, "byte_identical": True}


def render_markdown(rep: dict[str, Any]) -> str:
    t = rep["trial_terms"]
    L = [f"# EXP-012 forward sensitivity: {rep['verdict']}", "", f"_{rep['label']}_", ""]
    if rep.get("test_window"):
        L += [f"**{fw.TEST_WINDOW_BANNER}**", ""]
    L += [
        f"Window [{rep['clean_clock']}, {rep['read_end']}). Reproduction check: byte-identical on {rep['reproduction']['n_rows']} rows.",
        f"Trial terms: size {t['size_sol']} SOL, priority {t['priority_lamports']} lamports per side, tip {t['tip_lamports']}, max concurrent {t['max_concurrent']}.",
        f"Rule: (i) full promotion gate at k(p50) = {rep['k_p50']}, both fail models: {'yes' if rep['rule']['p50_full_gate'] else 'NO'}; (ii) k(p90) = {rep['k_p90']} mean > 0 and ex-top-3 > 0, both models: {'yes' if rep['rule']['p90_mean_and_ex_top3_positive'] else 'NO'}.",
        "",
        "| book | k | n | skipped by cap | flat mean SOL | flat CI90 | flat ex-top-3 | press mean SOL | press CI90 | press ex-top-3 | days+/days |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in ("p50", "p90"):
        b = rep["books"][name]
        f, p = b["flat_15"], b["pressure_scale_1"]
        L.append(f"| {name} | {b['k']} | {f['n']} | {b['n_skipped_by_cap']} | {f['mean_sol']} | {f['mean_ci90_sol']} | {f['total_ex_top3_sol']} | {p['mean_sol']} | {p['mean_ci90_sol']} | {p['total_ex_top3_sol']} | {f['days_positive']}/{f['n_days']} |")
    L += ["", "Blockers at k(p50): flat " + str(rep["books"]["p50"]["flat_15"]["blockers"]) + "; pressure " + str(rep["books"]["p50"]["pressure_scale_1"]["blockers"]), ""]
    return "\n".join(L)


def run(
    walk_dir: Path,
    out_dir: Path,
    artifact_dir: Path,
    k_p50: int,
    k_p90: int,
    *,
    size_sol: float = DEFAULT_SIZE_SOL,
    priority_lamports: int = DEFAULT_PRIORITY,
    tip_lamports: int = DEFAULT_TIP,
    max_concurrent: int | None = DEFAULT_MAX_CONCURRENT,
    result_dir: Path | None = None,
    clean_clock: datetime | None = None,
    read_end: datetime | None = None,
    test_window: bool = False,
    final_ledger: Path | None = fw.DEFAULT_LEDGER,
    freeze_commit: str = fw.DEFAULT_FREEZE_COMMIT,
    frozen_manifest_md5: str | None = None,
) -> dict[str, Any]:
    cc = clean_clock if clean_clock is not None else fw.parse_clock(fw.PINNED_CLEAN_CLOCK)
    re_ = read_end if read_end is not None else fw.parse_clock(fw.PINNED_READ_END)
    if k_p50 < 1 or k_p90 < k_p50:
        raise fw.Refused([f"need 1 <= k_p50 <= k_p90, got {k_p50}, {k_p90}"])
    if size_sol <= 0 or priority_lamports < 0 or tip_lamports < 0 or (max_concurrent is not None and max_concurrent < 1):
        raise fw.Refused(["bad trial terms: size > 0, priority >= 0, tip >= 0, max concurrent >= 1"])
    result_dir = result_dir if result_dir is not None else out_dir / "sensitivity"
    check_sealed(out_dir, final_ledger, cc, re_, test_window)
    if (result_dir / RESULT_JSON).exists():
        raise fw.Refused([f"{result_dir / RESULT_JSON} exists: the re-score is computed once (DEC-016 Amendment 3 (a) 4); use a new --result-dir only for a new window"])
    errors = s12.check_frozen(artifact_dir, frozen_manifest_md5, freeze_commit)
    if errors:
        raise fw.Refused(errors)
    runs = fw.read_rows(out_dir / fw.RUNS_NAME)
    pool, to = window_pool(runs, re_)
    errors = fw.hour_problems(walk_dir, pool)
    if errors:
        raise fw.Refused(errors)

    with tempfile.TemporaryDirectory(prefix="exp012-sens-repro-") as td:
        repro = reproduce(walk_dir, out_dir, artifact_dir, pool, to, cc, re_, Path(td))
    # From here on, and only from here, P&L is computed in memory; nothing is printed until the end.
    result_dir.mkdir(parents=True, exist_ok=True)
    fw.atomic_write(result_dir / REPRO_NAME, (json.dumps({"schema": SCHEMA, **repro}, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    stored = fw.read_rows(out_dir / fw.ROWS_NAME)
    entered = {fw.key_of(r) for r in stored if r["entered"]}
    size = int(round(size_sol * LAMPORTS))
    ks = sorted({k_p50, k_p90})
    scratch = result_dir / "scratch"
    try:
        srows = sensitivity_rows(walk_dir, pool, ks, size, priority_lamports + tip_lamports, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    by_k: dict[int, dict[tuple[str, int], dict[str, Any]]] = {k: {} for k in ks}
    for r in srows:
        key = fw.key_of(r)
        if key in entered:
            by_k[int(r["entry_land_k"])][key] = r
    for k in ks:
        gone = sorted(entered - set(by_k[k]))
        if gone:
            raise fw.Refused([f"{len(gone)} FINAL entered mint(s) have no row at k={k} (exit past the tape?), first: mint {gone[0][0]} mig_ms {gone[0][1]}; no verdict"])
    books: dict[str, Any] = {}
    detail: list[dict[str, Any]] = []
    for name, k in (("p50", k_p50), ("p90", k_p90)):
        kept, skipped = apply_cap(list(by_k[k].values()), max_concurrent)
        legs = book_legs(kept)
        books[name] = {"k": k, "n_entered_final": len(entered), "n_skipped_by_cap": len(skipped), "n_filled": sum(1 for r in kept if r["filled"]), **legs}
        for r in kept + skipped:
            detail.append({"book": name, "k": k, "mint": r["mint"], "mig_ms": r["mig_ms"], "day": r["day"], "skipped_by_cap": r in skipped, **{f: r[f] for f in ("entry_target_slot", "entry_state_slot", "entry_spot_sol", "exit_state_slot", "exit_spot_sol", "exit_reason", "pressure_prob", "filled", "status", "flat", "press")}})
    rule = decide(books["p50"], books["p90"])
    rep = {
        "schema": SCHEMA,
        "label": LABEL,
        "verdict": rule["verdict"],
        "rule": rule,
        "test_window": bool(test_window),
        "clean_clock": fw._wins(cc, re_)[0],
        "read_end": fw._wins(cc, re_)[1],
        "k_p50": k_p50,
        "k_p90": k_p90,
        "trial_terms": {"size_sol": size_sol, "priority_lamports": priority_lamports, "tip_lamports": tip_lamports, "max_concurrent": max_concurrent},
        "entry_bound": ENTRY_BOUND_SENS,
        "reproduction": repro,
        "books": books,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if test_window:
        rep["window_note"] = fw.TEST_WINDOW_BANNER
    fw.atomic_write(result_dir / DETAIL_NAME, "".join(json.dumps(d, sort_keys=True) + "\n" for d in sorted(detail, key=lambda d: (d["book"], d["mig_ms"], d["mint"]))).encode("utf-8"))
    fw.atomic_write(result_dir / RESULT_JSON, (json.dumps(rep, indent=2, default=str) + "\n").encode("utf-8"))
    _atomic_text(result_dir / RESULT_MD, render_markdown(rep))
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--walk-dir", required=True)
    ap.add_argument("--out-dir", required=True, help="the forward scorer's out-dir (rows.jsonl, runs.jsonl, final_read.lock)")
    ap.add_argument("--result-dir", default=None, help="default: OUT/sensitivity")
    ap.add_argument("--artifact-dir", default=str(fw.DEFAULT_ARTIFACT_DIR))
    ap.add_argument("--k-p50", type=int, required=True)
    ap.add_argument("--k-p90", type=int, required=True)
    ap.add_argument("--size-sol", type=float, default=DEFAULT_SIZE_SOL)
    ap.add_argument("--priority-lamports", type=int, default=DEFAULT_PRIORITY)
    ap.add_argument("--tip-lamports", type=int, default=DEFAULT_TIP)
    ap.add_argument("--max-concurrent", type=int, default=DEFAULT_MAX_CONCURRENT)
    ap.add_argument("--clean-clock", default=None)
    ap.add_argument("--read-end", default=None)
    ap.add_argument("--test-window", action="store_true", help="allow a non-pinned window on synthetic fixtures; output is marked test_window")
    ap.add_argument("--final-ledger", default=str(fw.DEFAULT_LEDGER))
    ap.add_argument("--freeze-commit", default=fw.DEFAULT_FREEZE_COMMIT)
    ap.add_argument("--frozen-manifest-md5", default=None)
    a = ap.parse_args(argv)
    try:
        rep = run(
            Path(a.walk_dir), Path(a.out_dir), Path(a.artifact_dir), a.k_p50, a.k_p90,
            size_sol=a.size_sol, priority_lamports=a.priority_lamports, tip_lamports=a.tip_lamports, max_concurrent=a.max_concurrent,
            result_dir=Path(a.result_dir) if a.result_dir else None,
            clean_clock=fw.parse_clock(a.clean_clock) if a.clean_clock else None,
            read_end=fw.parse_clock(a.read_end) if a.read_end else None,
            test_window=a.test_window, final_ledger=Path(a.final_ledger), freeze_commit=a.freeze_commit, frozen_manifest_md5=a.frozen_manifest_md5,
        )
    except fw.Refused as exc:
        return fw._refuse(exc)
    print(f"VERDICT: {rep['verdict']} ({LABEL}){' [' + fw.TEST_WINDOW_BANNER + ']' if rep['test_window'] else ''}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
