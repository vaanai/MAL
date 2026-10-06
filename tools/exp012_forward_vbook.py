#!/usr/bin/env python3
"""EXP-012 forward, book (B): the same entered set re-priced on PumpSwap vault + V (DEC-016 Amendment 4).

    python3 -m tools.exp012_forward_vbook --walk-dir D --final-out-dir O --final-ledger L \
        --vmap M --vmap-sha256 S --out-dir X [--test-window]

Book (A) is `tools/exp012_forward.py`, unchanged; its FINAL read is the EXP-012 verdict. This tool computes
(B) afterwards, from the same sealed hours, and never before:

  0. SEAL. It refuses unless the FINAL (A) read is recorded: a non-test FINAL marker for the pinned window in
     the ledger (`exp012_forward.ledger_markers`), for `--final-out-dir`, whose lock and rows.jsonl still hash
     to the marker. `--test-window` instead accepts only a `test_window` marker (fixture windows). No row is
     opened before this passes. The V map must hash to `--vmap-sha256`.
  1. REPRODUCTION (section 1). A pass with frozen pricing (no V) must reproduce (A)'s per-row `flat` and
     `press` byte for byte, keyed by `exp012_forward.key_of`. Any difference refuses (exit 2) before (B).
  2. (B) at mcap_mode "v" (the rule) and "vault" (report only, section 4). The entered set must equal (A)'s,
     key by key; a difference refuses.
  3. NULL-V (section 3). Each row carries the PumpSwap pools its mint printed on and which of them have no V
     (null or absent in the map; never treated as V = 0). If any of (B)'s top 3 entered trades by flat, or
     more than 1% of entered trades, touch such a pool, (B) is NOT_DECIDABLE.
  4. The gate is `exp012_forward.build_report` on (B)'s rows, the same code path as (A)'s.

Pool-per-mint tracking is a superset: a mint's pools are those it printed on up to the moment it was scored
(including prints after the exit), so it can only over-report null-V contact, never miss it.

Outputs, in a new `--out-dir` (refused if it exists): vbook_report.json, vbook_report.md. Scratch row files
(P&L at rest) live in a temp directory next to it and are deleted.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

import tools.exp011_score as e11
import tools.exploration_entry_model as eem
import tools.exp012_forward as fw
import tools.exp012_score as s12
from tools import pumpswap_virtual_adapter as ad
from tools.exp012_forward import Refused

SCHEMA_REPORT = "exp012_forward_vbook_report_v1"
REPORT_JSON = "vbook_report.json"
REPORT_MD = "vbook_report.md"
NOT_DECIDABLE = "NOT_DECIDABLE"
NO_POOL_FIELD = "<no-pool-field>"
NULL_V_SHARE_LIMIT = 0.01  # more than 1% of entered trades
TOP_N = 3
CONDITIONS = ("min_n", "min_days", "mean_ci90", "drop_top3", "majority_days")
LABEL = "forward simulated paper re-priced on vault + V, not money made"


# --- the V-patched worker ---------------------------------------------------------------


def _tracked_tagged(*args: Any, **kw: Any) -> Any:
    """`exp012_forward._tagged_worker`, with each row also carrying the PumpSwap pools its mint printed on
    (`pumpswap_pools`) and which of those have no V (`no_v_pools`). Runs inside the adapter's patch, so
    `eem.print_from_trade_row` is already the V wrapper; both wrappers here are restored on exit."""
    vmap, _mode = ad._cached_vmap()
    seen: dict[str, set[str]] = {}
    no_v: dict[str, set[str]] = {}
    inner_print, inner_score = eem.print_from_trade_row, eem.score_one

    def tracked_print(row: dict[str, Any]) -> Any:
        if row.get("venue") == "pumpswap":
            mint, pool = row.get("mint"), row.get("pool")
            pid = pool if isinstance(pool, str) else NO_POOL_FIELD
            seen.setdefault(mint, set()).add(pid)
            if not isinstance(pool, str) or vmap.get(pool) is None:
                no_v.setdefault(mint, set()).add(pid)
        return inner_print(row)

    def tracked_score(mint_id: str, *a: Any, **k: Any) -> list[dict[str, Any]]:
        rows = inner_score(mint_id, *a, **k)
        for r in rows:
            r["pumpswap_pools"] = sorted(seen.get(mint_id, ()))
            r["no_v_pools"] = sorted(no_v.get(mint_id, ()))
        return rows

    eem.print_from_trade_row, eem.score_one = tracked_print, tracked_score
    try:
        return fw._tagged_worker(*args, **kw)
    finally:
        eem.print_from_trade_row, eem.score_one = inner_print, inner_score


def vbook_worker(*args: Any, **kw: Any) -> Any:
    """Picklable `worker_fn` for `s12.load_rows`: the forward tagged worker under the adapter's patch.
    Settings come from the adapter's environment variables."""
    return ad._run_patched(_tracked_tagged, "fv", *args, **kw)


@contextlib.contextmanager
def _adapter_env(vmap: Path, mcap_mode: str, counts_dir: Path, frozen: bool) -> Any:
    from tools.exp012_virtual_rescore import _set_env

    keys = (ad.ENV_MAP, ad.ENV_MCAP, ad.ENV_COUNTS, ad.ENV_FROZEN, ad.ENV_CAPTURE)
    saved = {k: os.environ.get(k) for k in keys}
    _set_env(str(vmap), mcap_mode, counts_dir, capture=False, frozen=frozen)
    ad._VMAP_CACHE.clear()  # the in-process cache must not serve another map or mode
    try:
        yield
    finally:
        ad._VMAP_CACHE.clear()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def score_hours_v(walk_dir: Path, pool: Sequence[str], artifact_dir: Path, scratch: Path, vmap: Path, mcap_mode: str, counts_dir: Path, frozen: bool) -> tuple[list[dict[str, Any]], float]:
    """`exp012_forward.score_hours` for the read's own exit, with `vbook_worker` as `worker_fn`.
    `frozen=True`: no V (the unpatched reproduction pass). Rows are scored by the frozen model."""
    if mcap_mode not in ("v", "vault"):
        raise Refused([f"mcap_mode must be 'v' or 'vault', got {mcap_mode!r}"])
    model, threshold, names = e11.load_frozen_spec(artifact_dir)
    plan = fw.anchored_plan(pool, s12.MAX_HOME_HOURS, s12.BUFFER_HOURS)
    hours = fw.ForwardHours(str(walk_dir), frozenset(pool))
    with _adapter_env(vmap, mcap_mode, counts_dir, frozen):
        rows = s12.load_rows(hours, s12.MAX_WORKERS, s12.BUFFER_HOURS, s12.MAX_HOME_HOURS, scratch, pool_hours=list(pool), worker_fn=vbook_worker, plan=plan)
    e11.score_rows(model, rows, names)
    return rows, threshold


def read_counts(counts_dir: Path) -> dict[str, Any]:
    tot = {"pumpswap_prints": 0, "corrected": 0, "no_v": 0, "bonding_untouched": 0}
    pools: set[str] = set()
    for f in sorted(counts_dir.glob("counts-*.json")):
        c = json.loads(f.read_text(encoding="utf-8"))
        for k in tot:
            tot[k] += int(c.get(k, 0))
        pools.update(c.get("no_v_pools", []))
    return {**tot, "n_no_v_pools": len(pools), "no_v_pools": sorted(pools)}


# --- seal -------------------------------------------------------------------------------


def find_final_marker(final_out_dir: Path, final_ledger: Path, test_window: bool) -> dict[str, Any]:
    """The FINAL (A) marker for `final_out_dir` in the ledger, with its lock and rows still intact. Refuses otherwise."""
    here = str(final_out_dir.resolve())
    mine = [
        m for m in fw.ledger_markers(final_ledger)
        if m.get("final") and m.get("experiment", fw.ff.PRIMARY_EXPERIMENT) == fw.ff.PRIMARY_EXPERIMENT and bool(m.get("test_window")) == test_window and m.get("out_dir") == here
    ]
    if not test_window:
        mine = [m for m in mine if (m.get("clean_clock"), m.get("read_end")) == (fw.PINNED_CLEAN_CLOCK, fw.PINNED_READ_END)]
    if not mine:
        raise Refused([f"no{' test-window' if test_window else ''} FINAL read of EXP-012 for {here} in {final_ledger}; (B) is computed only after (A)'s FINAL is recorded"])
    marker = mine[-1]
    lock, rows = final_out_dir / fw.LOCK_NAME, final_out_dir / fw.ROWS_NAME
    problems = []
    if not lock.is_file():
        problems.append(f"{lock} is missing")
    elif fw._sha256_file(lock) != marker.get("lock_sha256"):
        problems.append(f"{lock} differs from the lock sha256 in the FINAL marker")
    if not rows.is_file() or fw._sha256_file(rows) != marker.get("rows_sha256"):
        problems.append(f"{rows} does not hash to the rows_sha256 in the FINAL marker")
    if problems:
        raise Refused(problems)
    return marker


def check_vmap(vmap: Path, want_sha: str) -> dict[str, Any]:
    from tools.pumpswap_virtual import load_map

    if not vmap.is_file():
        raise Refused([f"V map {vmap} does not exist"])
    got = fw._sha256_file(vmap)
    if got != want_sha.strip().lower():
        raise Refused([f"V map {vmap} sha256 {got} differs from --vmap-sha256 {want_sha}"])
    m = load_map(vmap)
    return {"path": str(vmap), "sha256": got, "n_pools": len(m), "n_null": sum(1 for v in m.values() if v is None), "n_zero_v": sum(1 for v in m.values() if v == 0)}


# --- checks -----------------------------------------------------------------------------


def window_rows(rows: Sequence[dict[str, Any]], threshold: float, lo: int, hi: int) -> list[dict[str, Any]]:
    """`make_row` rows in [lo, hi), as `run_score` builds them, plus the per-row V-contact tags."""
    out = []
    for r in rows:
        if lo <= int(r["mig_ms"]) < hi:
            row = fw.make_row(r, threshold)
            row["pumpswap_pools"] = list(r.get("pumpswap_pools", []))
            row["no_v_pools"] = list(r.get("no_v_pools", []))
            out.append(row)
    return sorted(out, key=lambda x: (x["mig_ms"], x["mint"]))


def check_reproduction(a_rows: Sequence[dict[str, Any]], repro: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """(A)'s `flat` and `press` against the unpatched pass, byte for byte (the JSON text of each float)."""
    a = {fw.key_of(r): r for r in a_rows}
    b = {fw.key_of(r): r for r in repro}
    missing, extra = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    differ = [k for k in sorted(set(a) & set(b)) if any(json.dumps(a[k][f]) != json.dumps(b[k][f]) for f in ("flat", "press"))]
    res = {"n_a_rows": len(a), "n_reproduced_rows": len(b), "n_missing": len(missing), "n_extra": len(extra), "n_flat_press_differ": len(differ), "identical": not (missing or extra or differ)}
    if not res["identical"]:
        raise Refused([f"unpatched pass does not reproduce (A): {len(missing)} missing, {len(extra)} extra, {len(differ)} rows differ in flat/press; first mints {[k[0] for k in (differ + missing + extra)[:5]]}. (B) not computed"])
    return res


def entered_keys(rows: Sequence[dict[str, Any]]) -> set[tuple[str, int]]:
    return {fw.key_of(r) for r in rows if r["entered"]}


def check_entered_set(a_rows: Sequence[dict[str, Any]], b_rows: Sequence[dict[str, Any]], label: str) -> list[str]:
    a, b = entered_keys(a_rows), entered_keys(b_rows)
    if a == b:
        return []
    return [f"{label}: entered set differs from (A)'s: {len(a - b)} only in (A), {len(b - a)} only in (B); first mints {sorted(m for m, _ in (a ^ b))[:5]}"]


def null_v_assessment(b_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Amendment 4 section 3. A pool with no readable V is never V = 0: if any of (B)'s top 3 entered trades by flat,
    or more than 1% of entered trades, touch one, (B) is not decidable."""
    entered = [r for r in b_rows if r["entered"]]
    touching = [r for r in entered if r.get("no_v_pools")]
    top = sorted(entered, key=lambda r: r["flat"], reverse=True)[:TOP_N]
    top_hit = [r for r in top if r.get("no_v_pools")]
    n = len(entered)
    over = len(touching) > NULL_V_SHARE_LIMIT * n  # exactly 1% is allowed
    return {
        "n_entered": n,
        "n_entered_touching_null_v": len(touching),
        "share_touching_null_v": (len(touching) / n) if n else None,
        "limit_share": NULL_V_SHARE_LIMIT,
        "over_limit": over,
        "top3": [{"mint": r["mint"], "flat_sol": r["flat_sol"], "no_v_pools": list(r.get("no_v_pools", []))} for r in top],
        "top3_touches_null_v": bool(top_hit),
        "not_decidable": bool(over or top_hit),
        "null_v_pool_ids": sorted({p for r in touching for p in r["no_v_pools"]}),
    }


def compare_modes(v: dict[str, Any], vault: dict[str, Any]) -> dict[str, Any]:
    """Section 4: do vault and v modes agree on every gate condition, per leg?"""
    diffs = []
    for leg in ("flat_15", "pressure_scale_1"):
        bv, bw = set(v[leg]["blockers"]), set(vault[leg]["blockers"])
        for c in CONDITIONS:
            if (c in bv) != (c in bw):
                diffs.append({"leg": leg, "condition": c, "v_fails": c in bv, "vault_fails": c in bw})
        if v[leg]["clears_gate"] != vault[leg]["clears_gate"]:
            diffs.append({"leg": leg, "condition": "clears_gate", "v_fails": not v[leg]["clears_gate"], "vault_fails": not vault[leg]["clears_gate"]})
    return {"agree_on_every_gate_condition": not diffs, "disagreements": diffs, "note": "if they disagree, live is not supported until the tier rule is resolved on chain (Amendment 4 section 4)"}


def gate_block(rows: Sequence[dict[str, Any]], runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """(A)'s own `build_report` on these rows, without the per-run clocks that only describe (A)."""
    return fw.build_report([{k: v for k, v in r.items() if k not in ("pumpswap_pools", "no_v_pools")} for r in rows], runs)


# --- the run ----------------------------------------------------------------------------


ScoreFn = Callable[..., "tuple[list[dict[str, Any]], float]"]


def run_vbook(
    walk_dir: Path,
    final_out_dir: Path,
    final_ledger: Path,
    vmap: Path,
    vmap_sha256: str,
    out_dir: Path,
    artifact_dir: Path = fw.DEFAULT_ARTIFACT_DIR,
    freeze_commit: str | None = fw.DEFAULT_FREEZE_COMMIT,
    frozen_manifest_md5: str | None = None,
    test_window: bool = False,
    score_fn: ScoreFn = score_hours_v,
) -> dict[str, Any]:
    marker = find_final_marker(final_out_dir, final_ledger, test_window)  # 0. before any row is opened
    cc, re_ = fw.parse_clock(marker["clean_clock"]), fw.parse_clock(marker["read_end"])
    fw.check_window(cc, re_, test_window)
    vinfo = check_vmap(vmap, vmap_sha256)
    if out_dir.exists():
        raise Refused([f"{out_dir} exists; every run needs a new --out-dir"])
    errors = s12.check_frozen(artifact_dir, frozen_manifest_md5, freeze_commit)
    if errors:
        raise Refused(errors)
    all_runs = fw.read_rows(final_out_dir / fw.RUNS_NAME)
    runs = fw.score_runs(all_runs)
    if not runs:
        raise Refused([f"{final_out_dir / fw.RUNS_NAME} records no score run"])
    last = runs[-1]
    pool = e11._hours_range(last["pool_from"], last["to_exclusive"])
    problems = fw.hour_problems(walk_dir, pool)
    if problems:
        raise Refused(problems)
    a_rows = fw.read_rows(final_out_dir / fw.ROWS_NAME)
    lo, hi = fw.ms(cc), min(fw.ms(fw.hour_dt(last["to_exclusive"])), fw.ms(re_))

    scratch_root = Path(tempfile.mkdtemp(prefix="vbook-scratch-", dir=str(out_dir.resolve().parent)))
    try:
        def one(tag: str, mode: str, frozen: bool) -> tuple[list[dict[str, Any]], dict[str, Any], float]:
            rows, thr = score_fn(walk_dir, pool, artifact_dir, scratch_root / tag, vmap, mode, scratch_root / f"counts-{tag}", frozen)
            return window_rows(rows, thr, lo, hi), read_counts(scratch_root / f"counts-{tag}"), thr

        repro_rows, _c, thr = one("unpatched", "v", True)
        if last.get("threshold") is not None and thr != last["threshold"]:
            raise Refused([f"frozen threshold {thr!r} differs from the FINAL run's {last['threshold']!r}"])
        repro = check_reproduction(a_rows, repro_rows)  # 2. before (B) is read
        v_rows, v_counts, _ = one("v", "v", False)
        bad = check_entered_set(a_rows, v_rows, "mcap_mode v")
        if bad:
            raise Refused(bad)
        vault_rows, vault_counts, _ = one("vault", "vault", False)
        vault_bad = check_entered_set(a_rows, vault_rows, "mcap_mode vault")
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)

    null_v = null_v_assessment(v_rows)
    b_gate = gate_block(v_rows, runs)
    vault_gate = gate_block(vault_rows, runs)
    agreement = compare_modes(b_gate, vault_gate)
    if vault_bad:
        agreement = {**agreement, "agree_on_every_gate_condition": False, "vault_entered_set_problems": vault_bad}
    a_gate = gate_block(a_rows, runs)
    verdict = NOT_DECIDABLE if null_v["not_decidable"] else b_gate["verdict"]
    commit = fw._git_commit()
    adapter_file = Path(ad.__file__)
    rep: dict[str, Any] = {
        "schema": SCHEMA_REPORT,
        "label": LABEL,
        "test_window": test_window,
        "clean_clock": marker["clean_clock"],
        "read_end": marker["read_end"],
        "scored_through_exclusive": last["to_exclusive"],
        "threshold": thr,
        "a_reproduction": {**repro, "frozen_pricing_pass": "identical flat and press, keyed by (mint, mig_ms)"},
        "a_final_marker": {"rows_sha256": marker.get("rows_sha256"), "lock_sha256": marker.get("lock_sha256"), "utc_time": marker.get("utc_time")},
        "a_verdict_recomputed_from_stored_rows": a_gate["verdict"],
        "b_verdict": verdict,
        "b_verdict_if_decidable": b_gate["verdict"],
        "b_mcap_mode_v": {k: b_gate[k] for k in ("n_entered", "flat_15", "pressure_scale_1", "gate", "verdict")},
        "vault_mode_report_only": {k: vault_gate[k] for k in ("n_entered", "flat_15", "pressure_scale_1", "gate", "verdict")},
        "vault_vs_v": agreement,
        "null_v": null_v,
        "adapter_counts": {"mcap_mode_v": v_counts, "mcap_mode_vault": vault_counts},
        "vmap": vinfo,
        "adapter_file_sha256": hashlib.sha256(adapter_file.read_bytes()).hexdigest(),
        "git_commit": commit,
        "live_support_note": "Amendment 4 section 2: live needs (A) PASS and (B) at mcap_mode v PASS; V-correction can only remove support. This report does not decide live.",
    }
    out_dir.mkdir(parents=True)
    fw.atomic_write(out_dir / REPORT_JSON, (json.dumps(rep, indent=2, default=str) + "\n").encode("utf-8"))
    fw.atomic_write(out_dir / REPORT_MD, render_markdown(rep).encode("utf-8"))
    return rep


def render_markdown(rep: dict[str, Any]) -> str:
    L = ([fw.TEST_WINDOW_BANNER, ""] if rep.get("test_window") else []) + [f"# EXP-012 forward book (B): {rep['label']}", ""]
    L.append(f"(B) verdict: {rep['b_verdict']} (if decidable: {rep['b_verdict_if_decidable']}); (A) recomputed from stored rows: {rep['a_verdict_recomputed_from_stored_rows']}")
    L.append(f"window [{rep['clean_clock']}, {rep['read_end']}), scored through {rep['scored_through_exclusive']}, threshold {rep['threshold']!r}, git {rep['git_commit']}")
    ar = rep["a_reproduction"]
    L += ["", f"(A) reproduction (unpatched pass): identical={ar['identical']}, rows {ar['n_reproduced_rows']}/{ar['n_a_rows']}"]
    for key, title in (("b_mcap_mode_v", "(B) mcap_mode v"), ("vault_mode_report_only", "vault mode (report only)")):
        b = rep[key]
        L += ["", f"## {title}: n_entered={b['n_entered']}", "| model | n | mean SOL/trade | 90% CI of mean | total SOL | total ex top-3 SOL | days positive | clears gate | blockers |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, leg in (("flat 15%", b["flat_15"]), ("pressure x1", b["pressure_scale_1"])):
            L.append(f"| {name} | {leg['n']} | {leg['mean_sol']!r} | {leg['mean_ci90_sol']!r} | {leg['total_sol']!r} | {leg['total_ex_top3_sol']!r} | {leg['days_positive']}/{leg['n_days']} | {leg['clears_gate']} | {', '.join(leg['blockers'])} |")
    vv = rep["vault_vs_v"]
    L += ["", f"## Vault vs v: agree on every gate condition = {vv['agree_on_every_gate_condition']}", f"disagreements: {vv['disagreements']}"]
    n = rep["null_v"]
    L += ["", "## Null-V rule", f"entered {n['n_entered']}, touching a null or absent V pool {n['n_entered_touching_null_v']} (limit {n['limit_share']:.0%}, over limit {n['over_limit']}); top 3 touch null V: {n['top3_touches_null_v']}; NOT_DECIDABLE: {n['not_decidable']}", f"null-V pool ids touched by entered trades: {n['null_v_pool_ids']}"]
    L += ["", "## Adapter counts", f"{json.dumps(rep['adapter_counts'])}", "", f"V map {rep['vmap']['path']} sha256 {rep['vmap']['sha256']} ({rep['vmap']['n_pools']} pools, {rep['vmap']['n_null']} null)", "", f"Result label: {rep['label']}."]
    return "\n".join(L) + "\n"


def _refuse(exc: Refused) -> int:
    for r in exc.reasons:
        print(f"REFUSED: {r}", file=sys.stderr)
    return exc.code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--walk-dir", required=True)
    ap.add_argument("--final-out-dir", required=True, help="the FINAL (A) out dir; its rows are read only via exp012_forward.read_rows")
    ap.add_argument("--final-ledger", required=True)
    ap.add_argument("--vmap", required=True)
    ap.add_argument("--vmap-sha256", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--test-window", action="store_true", help="accept only a test_window FINAL marker (fixture windows)")
    ap.add_argument("--artifact-dir", default=None)
    ap.add_argument("--freeze-commit", default=None)
    ap.add_argument("--frozen-manifest-md5", default=None)
    a = ap.parse_args(argv)
    overridden = a.artifact_dir is not None
    try:
        rep = run_vbook(
            Path(a.walk_dir), Path(a.final_out_dir), Path(a.final_ledger), Path(a.vmap), a.vmap_sha256, Path(a.out_dir),
            Path(a.artifact_dir) if overridden else fw.DEFAULT_ARTIFACT_DIR, a.freeze_commit or fw.DEFAULT_FREEZE_COMMIT,
            a.frozen_manifest_md5, a.test_window,
        )
    except Refused as exc:
        return _refuse(exc)
    except (SystemExit, Exception) as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(f"(B) {rep['b_verdict']} ({LABEL}); vault agrees with v: {rep['vault_vs_v']['agree_on_every_gate_condition']}; wrote {a.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
