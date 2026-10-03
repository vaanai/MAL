#!/usr/bin/env python3
"""EXP-012 forward latency/size sensitivity re-score (DEC-016 Amendment 3 (a) section 3).

    python3 -m tools.exp012_forward_sensitivity --walk-dir D --out-dir FWD_OUT --latency-summary S.json --latency-export E.jsonl \
        [--size-sol 0.5] [--priority-lamports 500000] [--tip-lamports 0] [--max-concurrent 3] [--result-dir R]

NOT part of the pre-registered read. It decides nothing about the FINAL verdict, makes no edge claim,
and cannot turn a FAIL into a PASS. It runs ONCE PER WINDOW, after the FINAL read, whatever the result dir.

Order of operations (no P&L is printed before step 3 passes):
  0. Inputs. --latency-n < 100 is NOT_DECIDABLE. k may be `inf` (a stale_recv drop): that part of the rule
     fails without scoring. Priority below 500,000 is refused (the pressure curve is not refit). On the real
     window --final-ledger must be the default FINAL_READS.jsonl; --test-window is refused on /data/mal/blocks/.
  1. Seal. Refuses unless the FINAL read for the window is recorded (ledger marker, final_read.lock, and
     rows.jsonl still hashing to the lock), rows.jsonl has no duplicate key, and the FINAL read was a PASS
     (the expression `exp012_forward.ledger_final` uses).
  2. Reproduction through `exp012_forward.score_hours` (the forward scorer's own path, by import) at k=1,
     bound start, 0.5 SOL, 500,000 lamports, no cap: `flat`, `press`, `entered` byte for byte, keyed by
     (mint, mig_ms). A mismatch refuses, names fields only, and writes nothing.
  3. The window is claimed: a STARTED marker is appended under flock to SENSITIVITY_RUNS.jsonl beside
     FINAL_READS.jsonl (window, k, trial terms, latency sha256, n, slot_ms). A later non-test run for the
     window is refused. Any refusal or crash after this point appends a terminal NOT_DECIDABLE marker.
  4. One tape pass scores the variants: the re-score worker at k=1, bound start, frozen terms (it must also
     be byte-identical to the FINAL rows, else NOT_DECIDABLE), then k(p50) and k(p90) over the FINAL
     `entered` set with entry at slot + k, bound end, every exit delayed k - 1 further slots, the trial's
     size, priority and tip, and a position cap in decision order.
  5. Gates. k(p50): the full promotion gate, both fail models (`exp011_score.compute_gate`). k(p90): mean > 0
     and ex-top-3 > 0, both models. Verdict SUPPORTS_LIVE only if both hold.

How the delay is applied: the frozen exit logic is not edited. In the worker process only, restored after,
`exploration_exits.ENTRY_LAND_K` is set to k and `ENTRY_BOUND` to the variant's bound (end for the re-score, so
entry and exit are treated alike): a trigger print's exit lands at its slot + k. `_state_at` (the time-cap
exit) is wrapped so that for k > 1 the cap exit is the later of the frozen state and the state at
deadline + (k-1) * slot_ms.

Concurrency: a mint's decision time is mig_ms. A filled position holds a slot from then until its exit_ms:
trigger exit, the `t_exit` that `_delayed` returns; time cap, landing_ms + cap_ms + (k-1) * slot_ms. Rows are
walked in (mig_ms, migration slot, mint) order; a row arriving with `max_concurrent` positions open is skipped
and counted. A no-fill row holds no slot but is still subject to the cap check.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
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
RUNS_LEDGER_NAME = "SENSITIVITY_RUNS.jsonl"  # beside FINAL_READS.jsonl, append-only, once per window
SCHEMA_RUN = "exp012_forward_sensitivity_run_v1"
MIN_LATENCY_N = 100  # DEC-016 Amendment 3 (a) 1
MIN_PRIORITY = 500_000  # the pressure curve is not refit below the frozen priority
REAL_BLOCKS_PREFIX = "/data/mal/blocks/"
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
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        raise fw.Refused([f"{lock_path} is not valid JSON"])
    if not isinstance(lock, dict):
        raise fw.Refused([f"{lock_path} is not a JSON object"])
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
        if "entered" in a and "entered" in b and bool(a["entered"]) != bool(b["entered"]):
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

# (label, k, bound, size lamports, per-side fee lamports). The bound is used for the entry and, equally, for every exit.
Variant = tuple[str, int, str, int, int]


@dataclass(frozen=True)
class SensHours(fw.ForwardHours):
    variants: tuple[Variant, ...] = ()
    slot_ms: float = 268.0


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
        self.hit_pr: Any = None
        self.t_exit: int | None = None
        self.p_press: float | None = None


def _detail(cap: _Capture, row: dict[str, Any], k: int, slot_ms: float, cap_ms: int) -> dict[str, Any]:
    fills = cap.fills
    d: dict[str, Any] = {"mig_slot": cap.trigger_slot, "entry_target_slot": cap.target, "entry_state_slot": None, "entry_spot_sol": None, "exit_state_slot": None, "exit_spot_sol": None, "exit_reason": "no_fill", "pressure_prob": None, "landing_ms": cap.landing_ms, "exit_ms": None}
    if fills is None or cap.state_idx is None or cap.state_idx < 0 or not row["filled"]:
        return d
    st = fills[cap.state_idx]
    d["entry_state_slot"], d["entry_spot_sol"] = int(st.slot), float(st.price_sol)
    d["pressure_prob"] = cap.p_press
    held_to_cap = int((cap.landing_ms or 0) + cap_ms + (k - 1) * slot_ms)
    if cap.exit_idx is None:
        d["exit_reason"] = "unknown"
    elif cap.exit_idx < 0:
        d["exit_reason"] = "exit_state_missing"
        d["exit_ms"] = int(cap.t_exit) if cap.hit_pr is not None and cap.t_exit is not None else held_to_cap
    else:
        ex = fills[cap.exit_idx]
        d["exit_state_slot"], d["exit_spot_sol"] = int(ex.slot), float(ex.price_sol)
        if cap.hit_pr is not None:
            # sign of the trigger print's return against the entry state's spot (tp >= +50%, sl <= -30%, so the sign decides)
            d["exit_reason"] = "tp" if cap.hit_pr.price_sol >= st.price_sol else "sl"
            d["exit_ms"] = int(cap.t_exit) if cap.t_exit is not None else int(ex.t_recv_ms)
        else:
            d["exit_reason"] = "time_cap"
            d["exit_ms"] = held_to_cap
    return d


_SCORE_ONE_KW = frozenset({"specs", "size", "priority", "entry_land_k", "entry_bound"})


def _sens_worker(worker_id: int, home: list[str], buf: list[str], creator_hist: dict[str, list[int]], rows_out_path: Path | None, hours: SensHours) -> list[dict[str, Any]]:
    """`exp012_forward._tagged_worker`'s twin for the re-score. `eem.score_one` is wrapped to run the
    frozen `score_one` once per variant and to tag each row with mig_ms, the variant label, k and capture
    details; the hooks below only record or (for the exit delay) shift a state lookup. All restored."""
    orig_score_one = eem.score_one
    spec = [s for s in eem.build_specs() if s["id"] == e11.TARGET_SPEC_ID]
    if len(spec) != 1:
        raise SystemExit("tp50_sl30 spec not found")
    cap_ms = int(spec[0]["cap_ms"])
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
        ("ee", "ENTRY_BOUND"): ee.ENTRY_BOUND,
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

    def delayed(fills: Any, pr: Any, *a: Any, **kw: Any) -> Any:
        res = saved[("ee", "_delayed")](fills, pr, *a, **kw)
        cap.hit_pr, cap.t_exit = pr, int(res[1])
        return res

    def state_at(fills: Any, t_ms: int) -> int:
        """The time-cap exit's state: the frozen one, or for k > 1 the later of it and the state at deadline + (k-1) slots."""
        idx = saved[("ee", "_state_at")](fills, t_ms)
        k = cur["k"]
        if k <= 1:
            return idx
        return max(idx, saved[("ee", "_state_at")](fills, t_ms + (k - 1) * hours.slot_ms))

    def tagged(mint_id: str, mint: Any, feat: Any, curve: Any, through_ms: int, creator_hist_: Any, **kw: Any) -> list[dict[str, Any]]:
        extra = sorted(set(kw) - _SCORE_ONE_KW)
        if extra or any(v is not None for v in kw.values()):
            raise RuntimeError(f"score_one was called with arguments this re-score does not model: {sorted(kw)}")
        out: list[dict[str, Any]] = []
        for label, k, bound, size, per_side in hours.variants:
            cap.reset()
            cur["k"] = k
            ee.ENTRY_LAND_K = k
            ee.ENTRY_BOUND = bound
            rows = orig_score_one(mint_id, mint, feat, curve, through_ms, creator_hist_, specs=spec, size=size, priority=per_side, entry_land_k=k, entry_bound=bound)
            for r in rows:
                r.pop("features", None)
                r["mig_ms"] = int(mint.mig_ms)
                r["variant"], r["entry_land_k"] = label, k
                r.update(_detail(cap, r, k, hours.slot_ms, cap_ms))
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
        ee.ENTRY_LAND_K, ee.ENTRY_BOUND = saved[("ee", "ENTRY_LAND_K")], saved[("ee", "ENTRY_BOUND")]


def sensitivity_rows(walk_dir: Path, pool: Sequence[str], variants: Sequence[Variant], slot_ms: float, scratch: Path) -> list[dict[str, Any]]:
    hours = SensHours(str(walk_dir), frozenset(pool), None, tuple(variants), float(slot_ms))
    plan = fw.anchored_plan(pool, s12.MAX_HOME_HOURS, s12.BUFFER_HOURS)
    return s12.load_rows(hours, s12.MAX_WORKERS, s12.BUFFER_HOURS, s12.MAX_HOME_HOURS, scratch, pool_hours=list(pool), worker_fn=_sens_worker, plan=plan)


# --- concurrency cap and gates --------------------------------------------------------------


def apply_cap(rows: Sequence[dict[str, Any]], max_concurrent: int | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(kept, skipped), both in decision order (mig_ms, migration slot, mint). `max_concurrent` None = no cap.
    A kept filled row holds a slot from mig_ms to its exit_ms; a row arriving with max_concurrent
    open slots is skipped, whatever its own outcome would have been."""
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    open_until: list[int] = []
    for r in sorted(rows, key=lambda x: (int(x["mig_ms"]), int(x.get("mig_slot") or 0), x["mint"])):
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


def not_scored_book(k: float) -> dict[str, Any]:
    """An infinite k (a stale_recv drop at or before that percentile) fails its part of the rule without scoring."""
    legs = book_legs([])
    return {"k": "inf", "scored": False, "n_entered_final": None, "n_skipped_by_cap": None, "n_filled": 0, **legs}


def _pos(x: Any) -> bool:
    return x is not None and x > 0


def p90_pass(legs: dict[str, Any]) -> bool:
    return all(_pos(legs[m]["mean_sol"]) and _pos(legs[m]["total_ex_top3_sol"]) for m in ("flat_15", "pressure_scale_1"))


def decide(p50: dict[str, Any], p90: dict[str, Any]) -> dict[str, Any]:
    """The rule of DEC-016 Amendment 3 (a) 4: (i) full gate at k(p50), both models; (ii) at k(p90) mean > 0 and ex-top-3 > 0, both models."""
    i_ok = bool(p50["promote"])
    ii_ok = p90_pass(p90)
    return {"p50_full_gate": i_ok, "p90_mean_and_ex_top3_positive": ii_ok, "verdict": SUPPORTS if (i_ok and ii_ok) else DOES_NOT_SUPPORT}


# --- the once-per-window ledger -------------------------------------------------------------


def runs_ledger_path(final_ledger: Path) -> Path:
    return final_ledger.parent / RUNS_LEDGER_NAME


def _ledger_read(fd: int) -> list[dict[str, Any]]:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        b = os.read(fd, 1 << 20)
        if not b:
            break
        chunks.append(b)
    text = b"".join(chunks).decode("utf-8")
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                raise fw.Refused([f"{RUNS_LEDGER_NAME} line {n} is not valid JSON; repair it before any run"])
    return out


def append_marker(path: Path, doc: dict[str, Any], *, claim_window: tuple[str, str] | None = None) -> None:
    """Append one marker under an exclusive flock. With `claim_window` (a non-test run's (clean_clock, read_end)),
    refuse if any non-test marker for that window is already there: the check and the append are one critical section."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if claim_window is not None:
            for m in _ledger_read(fd):
                if (m.get("clean_clock"), m.get("read_end")) == claim_window and not m.get("test_window"):
                    raise fw.Refused([f"the sensitivity re-score for [{claim_window[0]}, {claim_window[1]}) already has a marker in {path} (state {m.get('state')}); it runs once per window and a later run, with any result dir, is refused"])
        os.write(fd, (json.dumps(doc, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# --- orchestration --------------------------------------------------------------------------


def _atomic_text(path: Path, text: str) -> None:
    fw.atomic_write(path, text.encode("utf-8"))


def final_verdict(rows: Sequence[dict[str, Any]]) -> str:
    """The FINAL read's own verdict, recomputed from the locked rows exactly as `exp012_forward.ledger_final` does."""
    entered = [r for r in rows if r["entered"]]
    return "PASS" if entered and e11.compute_gate(entered).get("promote") else "FAIL"


def check_unique_keys(rows: Sequence[dict[str, Any]]) -> None:
    seen: set[tuple[str, int]] = set()
    for r in rows:
        k = fw.key_of(r)
        if k in seen:
            raise fw.Refused([f"{fw.ROWS_NAME} holds a duplicate (mint, mig_ms) key, first: mint {k[0]} mig_ms {k[1]}"])
        seen.add(k)


def reproduce(walk_dir: Path, stored: Sequence[dict[str, Any]], artifact_dir: Path, pool: Sequence[str], to: datetime, clean_clock: datetime, read_end: datetime, scratch: Path) -> dict[str, Any]:
    """Re-run the FINAL window at k=1, start, 0.5 SOL, 500,000, no cap through the forward scorer's own path. Raises Refused on any mismatch."""
    rows, threshold = fw.score_hours(walk_dir, pool, artifact_dir, scratch)
    lo, hi = fw.ms(clean_clock), min(fw.ms(to), fw.ms(read_end))
    fresh = [fw.make_row(r, threshold) for r in rows if lo <= int(r["mig_ms"]) < hi]
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
        f"Window [{rep['clean_clock']}, {rep['read_end']}). Reproduction check: byte-identical on {rep['reproduction']['n_rows']} rows (forward scorer path and the re-score worker at k=1, bound start).",
        f"Trial terms: size {t['size_sol']} SOL, priority {t['priority_lamports']} lamports per side, tip {t['tip_lamports']}, max concurrent {t['max_concurrent']}.",
        f"Latency inputs: n {rep['latency']['n']}, slot_ms {rep['latency']['slot_ms']}, export sha256 {rep['latency']['export_sha256']}.",
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


def _k_from_summary(v: Any, name: str) -> float:
    if v == "inf":
        return math.inf
    if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
        return v
    raise fw.Refused([f"latency summary: {name} must be an integer >= 1 or \"inf\", got {v!r}"])


def resolve_latency(test_window: bool, summary_path: Path | None, export_path: Path | None, k_p50: Any, k_p90: Any, n: Any, sha: Any, slot_ms: Any) -> dict[str, Any]:
    """The latency inputs. On the real window they come only from `--latency-summary` (the JSON printed by
    tools/exp012_runner_latency_export.py) checked against `--latency-export` (its sha256 must equal the
    summary's export_file_sha256); no free number is accepted next to them. Explicit numbers are for
    --test-window fixtures only."""
    explicit = [k_p50, k_p90, n, sha, slot_ms]
    if summary_path is None and export_path is None:
        if not test_window:
            raise fw.Refused(["the real window takes its latency inputs only from --latency-summary and --latency-export"])
        if any(x is None for x in explicit):
            raise fw.Refused(["--test-window without a latency summary needs --k-p50, --k-p90, --latency-n, --latency-export-sha256 and --slot-ms"])
        return {"k_p50": k_p50, "k_p90": k_p90, "n": n, "export_sha256": sha, "slot_ms": slot_ms, "summary_sha256": None}
    if summary_path is None or export_path is None:
        raise fw.Refused(["--latency-summary and --latency-export go together"])
    if any(x is not None for x in explicit):
        raise fw.Refused(["with --latency-summary no k, n, slot_ms or sha256 may be given on the command line: they come from the summary"])
    try:
        doc = json.loads(summary_path.read_text(encoding="utf-8"))
        export_sha = fw._sha256_file(export_path)
        summary_sha = fw._sha256_file(summary_path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise fw.Refused([f"cannot read the latency summary or export: {type(exc).__name__}"])
    if not isinstance(doc, dict):
        raise fw.Refused(["the latency summary is not a JSON object"])
    if doc.get("export_file_sha256") != export_sha:
        raise fw.Refused(["--latency-export does not hash to the summary's export_file_sha256"])
    if doc.get("verdict") != "OK":
        raise fw.Refused([f"NOT_DECIDABLE: the latency summary's verdict is {doc.get('verdict')!r}"])
    n_ = doc.get("n")
    sm = doc.get("slot_ms")
    if not isinstance(n_, int) or isinstance(n_, bool) or isinstance(sm, bool) or not isinstance(sm, (int, float)):
        raise fw.Refused(["latency summary: n and slot_ms must be numbers"])
    return {"k_p50": _k_from_summary(doc.get("k_p50"), "k_p50"), "k_p90": _k_from_summary(doc.get("k_p90"), "k_p90"), "n": n_, "export_sha256": export_sha, "slot_ms": float(sm), "summary_sha256": summary_sha}


def _kstr(k: float) -> Any:
    return "inf" if math.isinf(k) else int(k)


def run(
    walk_dir: Path,
    out_dir: Path,
    artifact_dir: Path,
    k_p50: float | None = None,
    k_p90: float | None = None,
    *,
    latency_summary: Path | None = None,
    latency_export: Path | None = None,
    latency_n: int | None = None,
    latency_export_sha256: str | None = None,
    slot_ms: float | None = None,
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
    # --- inputs (refusals here record nothing: nothing was computed)
    lat = resolve_latency(test_window, latency_summary, latency_export, k_p50, k_p90, latency_n, latency_export_sha256, slot_ms)
    k_p50, k_p90, latency_n, latency_export_sha256, slot_ms = lat["k_p50"], lat["k_p90"], lat["n"], lat["export_sha256"], lat["slot_ms"]
    if latency_n < MIN_LATENCY_N:
        raise fw.Refused([f"NOT_DECIDABLE: the latency export has n={latency_n} decisions, fewer than {MIN_LATENCY_N} (DEC-016 Amendment 3 (a) 1)"])
    if not re.fullmatch(r"[0-9a-f]{64}", latency_export_sha256 or ""):
        raise fw.Refused(["the latency export sha256 must be 64 hex chars"])
    if slot_ms <= 0:
        raise fw.Refused(["slot_ms must be positive"])
    if math.isnan(k_p50) or math.isnan(k_p90) or k_p50 < 1 or k_p90 < k_p50:
        raise fw.Refused([f"need 1 <= k_p50 <= k_p90, got {k_p50}, {k_p90}"])
    if size_sol <= 0 or tip_lamports < 0 or (max_concurrent is not None and max_concurrent < 1):
        raise fw.Refused(["bad trial terms: size > 0, tip >= 0, max concurrent >= 1"])
    if priority_lamports < MIN_PRIORITY:
        raise fw.Refused([f"priority {priority_lamports} < {MIN_PRIORITY}: the pressure curve is not refit, so a lower priority would be scored softer than the frozen model allows"])
    if test_window and str(walk_dir.resolve()).startswith(REAL_BLOCKS_PREFIX):
        raise fw.Refused([f"--test-window is refused on a walk dir under {REAL_BLOCKS_PREFIX}"])
    pinned = (fw._wins(cc, re_) == (fw.PINNED_CLEAN_CLOCK, fw.PINNED_READ_END)) and not test_window
    if pinned and (final_ledger is None or final_ledger.resolve() != fw.DEFAULT_LEDGER.resolve()):
        raise fw.Refused([f"on the real window --final-ledger must be {fw.DEFAULT_LEDGER}"])
    result_dir = result_dir if result_dir is not None else out_dir / "sensitivity"
    check_sealed(out_dir, final_ledger, cc, re_, test_window)
    assert final_ledger is not None
    ledger = runs_ledger_path(final_ledger)
    if (result_dir / RESULT_JSON).exists():
        raise fw.Refused([f"{result_dir / RESULT_JSON} exists: the re-score is computed once"])
    errors = s12.check_frozen(artifact_dir, frozen_manifest_md5, freeze_commit)
    if errors:
        raise fw.Refused(errors)
    runs = fw.read_rows(out_dir / fw.RUNS_NAME)
    pool, to = window_pool(runs, re_)
    errors = fw.hour_problems(walk_dir, pool)
    if errors:
        raise fw.Refused(errors)
    stored = fw.read_rows(out_dir / fw.ROWS_NAME)
    check_unique_keys(stored)
    if final_verdict(stored) != "PASS":
        raise fw.Refused(["the FINAL read was not a PASS: there is nothing for the sensitivity check to support; no re-score is run"])
    # --- reproduction through the forward scorer's own path (a refusal here records nothing and writes nothing)
    with tempfile.TemporaryDirectory(prefix="exp012-sens-repro-") as td:
        repro = reproduce(walk_dir, stored, artifact_dir, pool, to, cc, re_, Path(td))
    # --- from here the window is claimed: one run per window, whatever happens next
    cc_s, re_s = fw._wins(cc, re_)
    base = {"schema": SCHEMA_RUN, "clean_clock": cc_s, "read_end": re_s, "test_window": bool(test_window), "experiment": ff.PRIMARY_EXPERIMENT, "out_dir": str(out_dir.resolve()), "result_dir": str(result_dir.resolve())}
    terms = {"size_sol": size_sol, "priority_lamports": priority_lamports, "tip_lamports": tip_lamports, "max_concurrent": max_concurrent}
    start = {**base, "state": "STARTED", "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "k_p50": _kstr(k_p50), "k_p90": _kstr(k_p90), "trial_terms": terms, "latency_export_sha256": latency_export_sha256, "latency_summary_sha256": lat["summary_sha256"], "latency_n": latency_n, "slot_ms": slot_ms}
    append_marker(ledger, start, claim_window=None if test_window else (cc_s, re_s))
    try:
        rep = _rescore(walk_dir, out_dir, pool, stored, repro, result_dir, k_p50, k_p90, terms, latency_n, latency_export_sha256, slot_ms, test_window, cc_s, re_s, to, lat["summary_sha256"])
    except BaseException as exc:  # noqa: BLE001 -- terminal: the window is spent and cannot be re-run
        append_marker(ledger, {**base, "state": "NOT_DECIDABLE", "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "reason": type(exc).__name__})
        raise
    append_marker(ledger, {**base, "state": "DONE", "utc_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "verdict": rep["verdict"]})
    return rep


def _rescore(walk_dir: Path, out_dir: Path, pool: Sequence[str], stored: Sequence[dict[str, Any]], repro: dict[str, Any], result_dir: Path, k_p50: float, k_p90: float, terms: dict[str, Any], latency_n: int, sha: str, slot_ms: int, test_window: bool, cc_s: str, re_s: str, to: datetime, summary_sha256: str | None = None) -> dict[str, Any]:
    size = int(round(terms["size_sol"] * LAMPORTS))
    per_side = terms["priority_lamports"] + terms["tip_lamports"]
    finite = sorted({int(k) for k in (k_p50, k_p90) if not math.isinf(k)})
    variants: list[Variant] = [("repro", REPRO_K, REPRO_BOUND, int(round(REPRO_SIZE_SOL * LAMPORTS)), REPRO_PRIORITY)]
    variants += [(f"k{k}", k, ENTRY_BOUND_SENS, size, per_side) for k in finite]
    result_dir.mkdir(parents=True, exist_ok=True)
    scratch = result_dir / "scratch"
    try:
        srows = sensitivity_rows(walk_dir, pool, variants, slot_ms, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    # the re-score worker at k=1, bound start, frozen terms must also be byte-identical to the FINAL rows
    lo, hi = fw.ms(fw.parse_clock(cc_s)), min(fw.ms(to), fw.ms(fw.parse_clock(re_s)))
    got = [r for r in srows if r["variant"] == "repro" and lo <= int(r["mig_ms"]) < hi]
    problems = compare_rows(stored, got)
    if problems:
        raise fw.Refused(["re-score worker did not reproduce the FINAL rows at k=1, bound start, frozen terms; no verdict: " + "; ".join(problems[:5])])
    fw.atomic_write(result_dir / REPRO_NAME, (json.dumps({"schema": SCHEMA, **repro, "worker_byte_identical": True}, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    entered = {fw.key_of(r) for r in stored if r["entered"]}
    by_label: dict[str, dict[tuple[str, int], dict[str, Any]]] = {}
    for r in srows:
        if fw.key_of(r) in entered and r["variant"] != "repro":
            by_label.setdefault(r["variant"], {})[fw.key_of(r)] = r
    for label, rows_ in by_label.items():
        gone = sorted(entered - set(rows_))
        if gone:
            raise fw.Refused([f"{len(gone)} FINAL entered mint(s) have no row at {label} (exit past the tape?), first: mint {gone[0][0]} mig_ms {gone[0][1]}; no verdict"])
    books: dict[str, Any] = {}
    detail: list[dict[str, Any]] = []
    for name, k in (("p50", k_p50), ("p90", k_p90)):
        if math.isinf(k):
            books[name] = not_scored_book(k)
            continue
        kept, skipped = apply_cap(list(by_label[f"k{int(k)}"].values()), terms["max_concurrent"])
        skipped_keys = {fw.key_of(r) for r in skipped}
        legs = book_legs(kept)
        books[name] = {"k": int(k), "scored": True, "n_entered_final": len(entered), "n_skipped_by_cap": len(skipped), "n_filled": sum(1 for r in kept if r["filled"]), **legs}
        for r in kept + skipped:
            detail.append({"book": name, "k": int(k), "mint": r["mint"], "mig_ms": r["mig_ms"], "mig_slot": r["mig_slot"], "day": r["day"], "skipped_by_cap": fw.key_of(r) in skipped_keys, **{f: r[f] for f in ("entry_target_slot", "entry_state_slot", "entry_spot_sol", "exit_state_slot", "exit_spot_sol", "exit_reason", "pressure_prob", "filled", "status", "flat", "press")}})
    rule = decide(books["p50"], books["p90"])
    rep = {
        "schema": SCHEMA,
        "label": LABEL,
        "verdict": rule["verdict"],
        "rule": rule,
        "test_window": bool(test_window),
        "clean_clock": cc_s,
        "read_end": re_s,
        "k_p50": _kstr(k_p50),
        "k_p90": _kstr(k_p90),
        "trial_terms": terms,
        "latency": {"n": latency_n, "slot_ms": slot_ms, "export_sha256": sha, "summary_sha256": summary_sha256},
        "entry_bound": ENTRY_BOUND_SENS,
        "exit_bound": ENTRY_BOUND_SENS,
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


def _k_arg(text: str) -> float:
    return math.inf if text.strip().lower() in ("inf", "+inf", "infinity") else int(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--walk-dir", required=True)
    ap.add_argument("--out-dir", required=True, help="the forward scorer's out-dir (rows.jsonl, runs.jsonl, final_read.lock)")
    ap.add_argument("--result-dir", default=None, help="default: OUT/sensitivity (the run itself is once per window, whatever the directory)")
    ap.add_argument("--artifact-dir", default=str(fw.DEFAULT_ARTIFACT_DIR))
    ap.add_argument("--latency-summary", default=None, help="the JSON summary printed by tools/exp012_runner_latency_export.py (k, n and slot_ms come only from it)")
    ap.add_argument("--latency-export", default=None, help="the export file itself; its sha256 must equal the summary's export_file_sha256")
    ap.add_argument("--k-p50", type=_k_arg, default=None, help="--test-window fixtures only")
    ap.add_argument("--k-p90", type=_k_arg, default=None, help="--test-window fixtures only")
    ap.add_argument("--latency-n", type=int, default=None, help="--test-window fixtures only")
    ap.add_argument("--latency-export-sha256", default=None, help="--test-window fixtures only")
    ap.add_argument("--slot-ms", type=float, default=None, help="--test-window fixtures only")
    ap.add_argument("--size-sol", type=float, default=DEFAULT_SIZE_SOL)
    ap.add_argument("--priority-lamports", type=int, default=DEFAULT_PRIORITY, help=f"per side, at least {MIN_PRIORITY}")
    ap.add_argument("--tip-lamports", type=int, default=DEFAULT_TIP)
    ap.add_argument("--max-concurrent", type=int, default=DEFAULT_MAX_CONCURRENT)
    ap.add_argument("--clean-clock", default=None)
    ap.add_argument("--read-end", default=None)
    ap.add_argument("--test-window", action="store_true", help="allow a non-pinned window on synthetic fixtures; output is marked test_window")
    ap.add_argument("--final-ledger", default=str(fw.DEFAULT_LEDGER), help="on the real window this must be the default FINAL_READS.jsonl")
    ap.add_argument("--freeze-commit", default=fw.DEFAULT_FREEZE_COMMIT)
    ap.add_argument("--frozen-manifest-md5", default=None)
    a = ap.parse_args(argv)
    try:
        rep = run(
            Path(a.walk_dir), Path(a.out_dir), Path(a.artifact_dir), a.k_p50, a.k_p90,
            latency_summary=Path(a.latency_summary) if a.latency_summary else None, latency_export=Path(a.latency_export) if a.latency_export else None, latency_n=a.latency_n, latency_export_sha256=a.latency_export_sha256, slot_ms=a.slot_ms,
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
