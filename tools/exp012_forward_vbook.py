#!/usr/bin/env python3
"""EXP-012 forward, book (B): the same entered set re-priced on PumpSwap vault + V (DEC-016 Amendment 4).

    python3 -m tools.exp012_forward_vbook --walk-dir D --final-out-dir O --final-ledger L \
        --vmap M --vmap-sha256 S --vmap-merge-meta M.merge.json --out-dir X [--runs-ledger R] [--test-window]

Book (A) is `tools/exp012_forward.py`, unchanged; its FINAL read is the EXP-012 verdict. This tool computes
(B) afterwards, from the same sealed hours, and never before:

  0. SEAL. It refuses unless the FINAL (A) read is recorded: a non-test FINAL marker for the pinned window in
     the ledger (`exp012_forward.ledger_markers`), for `--final-out-dir`, whose lock and rows.jsonl still hash
     to the marker. `--test-window` instead accepts only a `test_window` marker (fixture windows). No row is
     opened before this passes. The V map must hash to `--vmap-sha256`, and `--vmap-merge-meta` (the
     `OUT.merge.json` of `exp012_forward_vmap merge`, required unless --test-window) must carry
     `sha256.out` equal to it; its fields are embedded in the report as `vmap_merge`. `--test-window` is
     refused on a walk dir under /data/mal/blocks/. Every run is appended to VBOOK_RUNS.jsonl (beside
     the FINAL out dir, or `--runs-ledger`) and the report counts the prior runs.
  1. REPRODUCTION (section 1). A pass with frozen pricing (no V) must reproduce (A)'s per-row `flat` and
     `press` byte for byte, keyed by `exp012_forward.key_of`. Any difference refuses (exit 2) before (B).
  2. (B) at mcap_mode "v" (the rule) and "vault" (report only, section 4). The entered set must equal (A)'s,
     key by key; a difference refuses.
  3. NULL-V (section 3). Each row carries the PumpSwap pools its mint printed on and which of them have no V
     (null or absent in the map; never treated as V = 0). If any trade in the union of (B)'s top 3 entered
     trades by flat and its top 3 by pressure net (each leg drops its own top 3), or more than 1% of
     entered trades, touch such a pool, (B) is NOT_DECIDABLE. A row without its tags is refused (fail
     closed). A pool whose V is 0 is the chain's real value (vault-only pricing is right for it): it is
     reported (`zero_v_pools`, `n_entered_touching_zero_v`), never a blocker.
  4. The gate is `exp012_forward.build_report` on (B)'s rows, the same code path as (A)'s.

Pool-per-mint tracking is a superset: a mint's pools are those it printed on up to the moment it was scored,
which includes prints after the exit, so it over-reports contact (it can only add a mint to the null-V
set, never miss one).

Vault-mode (report only) disagreements, a vault entered-set mismatch, a NOT_DECIDABLE (B) and the Am.4 s5
validation are listed in the top-level `live_blockers`; none of them is decided here.

SINGLE USE (non-test windows). VBOOK_RUNS.jsonl is a claim ledger, like the sensitivity tool's. Everything that
does not read a V-priced result runs first and spends nothing: FINAL marker, merge-meta binding, vmap sha,
frozen checks, hour checks, and the frozen (no V) reproduction pass. Immediately before the first V-priced
pass a STARTED line is appended under flock (window, FINAL rows sha256, vmap sha256, merge-meta sha256, utc, git
commit); a STARTED line for the same non-test window already there refuses the run. After it, every exit appends
a terminal line: DONE (b_verdict, report sha256) or REFUSED_AFTER_READ (reason), including the entered-set
refusal and SIGTERM. The DONE line is written before the report files. `--test-window` runs are exempt from the
refusal (their lines are logged). `prior_vbook_runs` counts STARTED lines for the same window. A non-test
window always uses `<final-out-dir>/../VBOOK_RUNS.jsonl`; `--runs-ledger` is refused there. A STARTED line with no
terminal line (SIGKILL, OOM) also means the window is spent and (B) is NOT_DECIDABLE.

LP LAW (DEC-016 Amendment 5 section 7(a), (c), (d)). `lphist-entered` (first argument) runs after the FINAL (A) read: it takes the
pools every entered trade touched (frozen tape pass, so no V-priced result is opened), fetches their LP history over
[window start - 1 h, final fetch end] (key read inside Python, never printed, <= 5 rps), writes a read-only file + meta, and
appends a COMPLETED line to LPHIST_RUNS.jsonl beside the FINAL out dir. `--lphist FILE` (required unless --test-window) must be the
FIRST completed line for the window (later runs are never used); its sha256 goes into the STARTED line. Both subcommands take `--vmap-merge-meta` and `--final-fetch-map` (the FINAL fetch map: its .fetch.json and .detail.json must hash to the
merge meta's sha256.fetch / sha256.final_detail; the merged OUT carries neither); the lphist run records the merge-meta sha256 and vbook requires
it to match; lphist `t_to` must reach the final fetch's end; off the test window fetch_slot_max must be recorded; all before STARTED. The merge meta must carry
`lp_moves` (sidecars sha-checked) and `--snapshot` files must hash to it. Each entered trade is priced at V0 at its entry fill
slot (`pool_values`, `lp_min_rows`): same-slot events both ways, events inside the hold at entry-slot and exit-slot V0, the lower
P&L per leg; unresolved, unexplained or ambiguous pools are null-V. Sensitivity (d)(i) (final-map V0) goes to `live_blockers` if its
verdict differs; (d)(ii) pending = 0 is report only. Every V map used is written under OUT/vmaps/ with its sha256 in the report.

Outputs, in a new `--out-dir` (refused if it exists): vbook_report.json, vbook_report.md. Scratch row files
(P&L at rest) live in a temp directory next to it and are deleted.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import signal
import threading
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import tools.exp011_score as e11
import tools.exploration_entry_model as eem
import tools.exp012_forward as fw
import tools.exp012_forward_vmap as vm
import tools.exp012_score as s12
import tools.pumpswap_lp_history as lph
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
REAL_BLOCKS_PREFIX = "/data/mal/blocks/"
RUNS_LEDGER_NAME = "VBOOK_RUNS.jsonl"
SCHEMA_RUN = "exp012_forward_vbook_run_v1"
CUTOFF = "2026-10-16T00:00:00Z"  # a V fetch for the read must start at or after this (module constant only; tests patch it)
TAGS = ("pumpswap_pools", "no_v_pools", "zero_v_pools")
LP_TAGS = ("entry_slot", "exit_slot", "last_slot")  # optional on a row; required once --lphist is given
VALIDATION_REMINDER = "Am.4 s5 validation must also pass (`exp012_forward_vmap validate`)"


# --- the V-patched worker ---------------------------------------------------------------


def tracked_call(inner: Callable[..., Any], *args: Any, **kw: Any) -> Any:
    """Run the worker `inner`, with each row it scores also carrying the PumpSwap pools its mint printed on
    (`pumpswap_pools`), which of those have no V (`no_v_pools`) and which have V <= 0 (`zero_v_pools`; the name is kept, it means V <= 0: parse_virtual is signed, and the V0 = 0 pools carry a small negative V that the adapter prices on the vault). Runs inside the adapter's patch, so
    `eem.print_from_trade_row` is already the V wrapper; both wrappers here are restored on exit.

    LP tags (Amendment 5 s7(c)): `entry_slot` (slot of the tape row the entry fill is priced from, None if no entry fill),
    `exit_slot` (same for the exit fill, None if none) and `last_slot` (the last slot the mint printed in the worker's tape,
    the exit slot of a trade with no exit fill)."""
    import tools.exploration_exits as ee

    vmap, _mode = ad._cached_vmap()
    seen: dict[str, set[str]] = {}
    no_v: dict[str, set[str]] = {}
    zero_v: dict[str, set[str]] = {}
    last_slot: dict[str, int] = {}
    ctx: dict[str, Any] = {"cur": None, "specs": {}}
    inner_print, inner_score = eem.print_from_trade_row, eem.score_one
    inner_eval, inner_sell = eem.eval_spec, ee._one_sell_close

    def tracked_print(row: dict[str, Any]) -> Any:
        if row.get("venue") == "pumpswap":
            mint, pool = row.get("mint"), row.get("pool")
            pid = pool if isinstance(pool, str) else NO_POOL_FIELD
            seen.setdefault(mint, set()).add(pid)
            if not isinstance(pool, str) or vmap.get(pool) is None:
                no_v.setdefault(mint, set()).add(pid)
            elif vmap.get(pool) <= 0:  # V <= 0, not == 0: signed decode, same test as the adapter's vault-only branch
                zero_v.setdefault(mint, set()).add(pid)
        res = inner_print(row)
        if res is not None and isinstance(row.get("mint"), str):
            sl = int(res[1].slot)
            if sl > last_slot.get(row["mint"], -1):
                last_slot[row["mint"]] = sl
        return res

    def tracked_eval(spec: Any, fills: Any, idx: int, *a: Any, **k: Any) -> Any:
        rec = {"entry": fills[idx].slot if idx >= 0 else None, "exit": None}
        ctx["cur"] = ctx["specs"][spec["id"]] = rec
        return inner_eval(spec, fills, idx, *a, **k)

    def tracked_sell(fills: Any, state_idx: int, *a: Any, **k: Any) -> Any:
        if ctx["cur"] is not None and state_idx >= 0:
            ctx["cur"]["exit"] = fills[state_idx].slot
        return inner_sell(fills, state_idx, *a, **k)

    def tracked_score(mint_id: str, *a: Any, **k: Any) -> list[dict[str, Any]]:
        ctx["cur"], ctx["specs"] = None, {}
        rows = inner_score(mint_id, *a, **k)
        for r in rows:
            r["pumpswap_pools"] = sorted(seen.get(mint_id, ()))
            r["no_v_pools"] = sorted(no_v.get(mint_id, ()))
            r["zero_v_pools"] = sorted(zero_v.get(mint_id, ()))
            rec = ctx["specs"].get(r["spec"], {})
            r["entry_slot"], r["exit_slot"], r["last_slot"] = rec.get("entry"), rec.get("exit"), last_slot.get(mint_id)
        return rows

    eem.print_from_trade_row, eem.score_one, eem.eval_spec, ee._one_sell_close = tracked_print, tracked_score, tracked_eval, tracked_sell
    try:
        return inner(*args, **kw)
    finally:
        eem.print_from_trade_row, eem.score_one, eem.eval_spec, ee._one_sell_close = inner_print, inner_score, inner_eval, inner_sell


def _tracked_tagged(*args: Any, **kw: Any) -> Any:
    """`exp012_forward._tagged_worker` under `tracked_call`."""
    return tracked_call(fw._tagged_worker, *args, **kw)


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
    """Hash and count the map. `n_zero_v` counts pools with V <= 0 (signed V; the name is kept)."""
    from tools.pumpswap_virtual import load_map

    if not vmap.is_file():
        raise Refused([f"V map {vmap} does not exist"])
    got = fw._sha256_file(vmap)
    if got != want_sha.strip().lower():
        raise Refused([f"V map {vmap} sha256 {got} differs from --vmap-sha256 {want_sha}"])
    m = load_map(vmap)
    return {"path": str(vmap), "sha256": got, "n_pools": len(m), "n_null": sum(1 for v in m.values() if v is None), "n_zero_v": sum(1 for v in m.values() if v is not None and v <= 0)}  # V <= 0, see tracked_call


def check_merge_meta(meta: Path, vmap_sha: str) -> dict[str, Any]:
    """#374's `OUT.merge.json`: `sha256.out` must be the V map's sha256 (`--vmap-sha256`), and the map must come from a
    fresh fetch: `final_fetch.new` is true and `final_fetch.fetch_started_utc` is at or after `CUTOFF`. Returned whole,
    embedded as `vmap_merge`."""
    try:
        doc = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused([f"--vmap-merge-meta {meta} is unreadable: {type(exc).__name__}"])
    if isinstance(doc, dict) and (doc.get("dry_run") is True or meta.name.endswith(vm.DRYRUN_SUFFIX + ".merge.json")):
        raise Refused([f"--vmap-merge-meta {meta}: a dry-run merge record cannot be used for the FINAL"])
    out = doc.get("sha256", {}).get("out") if isinstance(doc, dict) and isinstance(doc.get("sha256"), dict) else None
    if out != vmap_sha.strip().lower():
        raise Refused([f"--vmap-merge-meta {meta}: sha256.out {out!r} does not equal --vmap-sha256 {vmap_sha}"])
    ff = doc.get("final_fetch")
    if not isinstance(ff, dict) or ff.get("new") is not True:
        raise Refused([f"--vmap-merge-meta {meta}: final_fetch.new is not true (the map was not fetched fresh for this read)"])
    started = ff.get("fetch_started_utc")
    try:
        t_start, t_cut = fw.parse_clock(started), fw.parse_clock(CUTOFF)
    except Exception:  # noqa: BLE001 -- any malformed value is a clean refusal
        raise Refused([f"--vmap-merge-meta {meta}: final_fetch.fetch_started_utc {started!r} is not a UTC timestamp"])
    if t_start < t_cut:
        raise Refused([f"--vmap-merge-meta {meta}: final_fetch.fetch_started_utc {started!r} is before {CUTOFF}"])
    return doc


def _same_window(m: dict[str, Any], cc_s: str, re_s: str, test_window: bool) -> bool:
    return (m.get("clean_clock"), m.get("read_end")) == (cc_s, re_s) and bool(m.get("test_window")) == test_window


def claim_window(path: Path, doc: dict[str, Any], test_window: bool) -> int:
    """Append the STARTED line under an exclusive flock. A non-test window that already has a STARTED line is refused
    (check and append are one critical section). Returns the count of earlier STARTED lines for this window."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.lseek(fd, 0, os.SEEK_SET)
        text = b"".join(iter(lambda: os.read(fd, 1 << 20), b"")).decode("utf-8")
        prior = []
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    prior.append(json.loads(line))
                except ValueError:
                    raise Refused([f"{path} line {n} is not valid JSON; repair it before any run"])
        started = [m for m in prior if m.get("state") == "STARTED" and _same_window(m, doc["clean_clock"], doc["read_end"], test_window)]
        if started and not test_window:
            raise Refused([f"(B) was already read for [{doc['clean_clock']}, {doc['read_end']}): {path} has a STARTED line (utc {started[0].get('utc_time')}); the V-priced book is read once per window"])
        os.write(fd, (json.dumps(doc, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
        return len(started)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def live_blockers(agreement: dict[str, Any], vault_bad: Sequence[str], null_v: dict[str, Any], b_verdict_if_decidable: str, extra: Sequence[str] = ()) -> list[str]:
    out = []
    if not agreement["agree_on_every_gate_condition"] and agreement["disagreements"]:
        out.append(f"vault mode and v mode disagree on gate condition(s): {sorted({(d['leg'], d['condition']) for d in agreement['disagreements']})}")
    if vault_bad:
        out.append("vault-mode entered set differs from (A)'s: " + "; ".join(vault_bad))
    if null_v["not_decidable"]:
        out.append("null-V rule: (B) is NOT_DECIDABLE")
    if b_verdict_if_decidable != "PASS":
        out.append("(B) at mcap_mode v does not PASS the gate")
    out.extend(extra)
    out.append(VALIDATION_REMINDER)
    return out


# --- checks -----------------------------------------------------------------------------


def window_rows(rows: Sequence[dict[str, Any]], threshold: float, lo: int, hi: int) -> list[dict[str, Any]]:
    """`make_row` rows in [lo, hi), as `run_score` builds them, plus the per-row V-contact tags."""
    out = []
    for r in rows:
        if lo <= int(r["mig_ms"]) < hi:
            row = fw.make_row(r, threshold)
            missing = [t for t in TAGS if t not in r]
            if missing:
                raise Refused([f"row mint {r['mint']} mig_ms {r['mig_ms']} has no {missing} tag(s): the V-contact tracking did not run; refusing (fail closed)"])
            for t in TAGS:
                row[t] = list(r[t])
            for t in LP_TAGS:
                if t in r:
                    row[t] = r[t]
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
    """Amendment 4 section 3. A pool with no readable V is never V = 0: if any trade in the union of (B)'s top 3 entered
    trades by flat and top 3 by press (each leg drops its own top 3), or more than 1% of entered trades, touch one, (B) is
    not decidable. A row without `no_v_pools` or `zero_v_pools` is refused (fail closed). V = 0 pools are
    reported, never a blocker."""
    entered = [r for r in b_rows if r["entered"]]
    for r in entered:
        absent = [t for t in ("no_v_pools", "zero_v_pools") if t not in r]
        if absent:
            raise Refused([f"entered row {r.get('mint')} has no {absent} tag(s); refusing (fail closed)"])
    touching = [r for r in entered if r["no_v_pools"]]
    zero = [r for r in entered if r["zero_v_pools"]]
    top_flat = sorted(entered, key=lambda r: r["flat"], reverse=True)[:TOP_N]
    top_press = sorted(entered, key=lambda r: r["press"], reverse=True)[:TOP_N]
    union: list[dict[str, Any]] = []
    for r in top_flat + top_press:
        if not any(r is u for u in union):
            union.append(r)
    top_hit = [r for r in union if r["no_v_pools"]]
    n = len(entered)
    over = len(touching) > NULL_V_SHARE_LIMIT * n  # exactly 1% is allowed
    return {
        "n_entered": n,
        "n_entered_touching_null_v": len(touching),
        "share_touching_null_v": (len(touching) / n) if n else None,
        "limit_share": NULL_V_SHARE_LIMIT,
        "over_limit": over,
        "top3": [{"mint": r["mint"], "flat_sol": r["flat_sol"], "no_v_pools": list(r["no_v_pools"])} for r in top_flat],
        "top3_by_press": [{"mint": r["mint"], "press_sol": r["press"] / 1e9, "no_v_pools": list(r["no_v_pools"])} for r in top_press],
        "top3_union_mints": [r["mint"] for r in union],
        "top3_touches_null_v": bool(top_hit),
        "not_decidable": bool(over or top_hit),
        "null_v_pool_ids": sorted({p for r in touching for p in r["no_v_pools"]}),
        "n_entered_touching_zero_v": len(zero),
        "n_top3_union_touching_zero_v": sum(1 for r in union if r["zero_v_pools"]),
        "zero_v_pool_ids": sorted({p for r in zero for p in r["zero_v_pools"]}),
        "zero_v_note": "V = 0 is the chain's real value for a native pool; vault-only pricing is right for it. Report only, not a blocker.",
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
    return fw.build_report([{k: v for k, v in r.items() if k not in TAGS and k not in LP_TAGS} for r in rows], runs)


# --- LP-law pricing (DEC-016 Amendment 5 section 7) ----------------------------------------

LPHIST_RUNS_NAME = "LPHIST_RUNS.jsonl"
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
SCHEMA_LPHIST_RUN = "exp012_forward_vbook_lphist_run_v1"
LPHIST_PAD_S = 3600  # the lphist range starts one hour before the window
NO_MERGE_META_BAD: dict[str, str] = {}


def anchor_span_slots(events: Sequence[dict[str, Any]], span: tuple[int, int]) -> tuple[int, int]:
    """A slot span (lo, hi) for `v0_at` from a fetch's unix span, using the same block-time classification as the merge
    (1 s of slack): events with block time before span[0] - 1 are before the read (slot < lo), events after span[1] + 1 are
    after it (slot > hi), the rest (and unknown block times) may be on either side."""
    ev = lph._order(events)
    t0, t1 = span
    lo = hi = None
    for e in ev:
        bt = e.get("block_time")
        before = bt is not None and bt < t0 - 1
        after = bt is not None and bt > t1 + 1
        if lo is None and not before:
            lo = e["slot"]
        if hi is None and after:
            hi = e["slot"] - 1
    top = ev[-1]["slot"] if ev else 0
    return (top + 1 if lo is None else lo), (top if hi is None else hi)


def pool_values(e: int, x: int, anchor_v0: int, pending: int, events: Sequence[dict[str, Any]], span: tuple[int, int]) -> tuple[str, Any, dict[str, bool]]:
    """Section 7(c) for one pool and one trade. ("ok", sorted stored-V candidates, flags) or ("unresolved", reason, {}).

    V0 at the entry fill slot `e` is the anchor V0 moved across the LP events by `v0_at` (inverted for later events, applied
    for earlier ones). An ambiguous placement of an event inside the anchor fetch's own span (`v0_at`'s anchor_ambiguous) makes
    the pool unresolved. Same-slot events (slot e, or slot x with a hold event) are priced both ways: the candidate
    set holds every cut. A hold event is an LP event with e < slot <= x (x = exit fill slot, or the last priced slot with no
    exit fill): then the candidates are the entry-slot values and the exit-slot values, and the caller takes the lower P&L
    of them per leg. Stored V = V0 - pending. k enters only through (e, x)."""
    if any(not isinstance(ev.get("slot"), int) for ev in events):
        return "unresolved", "event_slot_missing", {}
    lo_s, hi_s = getattr(span, "slot_min", None), getattr(span, "slot_max", None)
    sp = (lo_s, hi_s) if isinstance(lo_s, int) and isinstance(hi_s, int) else anchor_span_slots(events, (span[0], span[1]))
    x = max(x, e)
    hold = any(e < ev["slot"] <= x for ev in events)
    slots = (e, x) if hold else (e,)
    cands: set[int] = set()
    for s_ in slots:
        r = lph.v0_at(s_, anchor_v0, sp, events)
        if r is None:
            return "unresolved", "too_many_ambiguous_events", {}
        if not r["values"]:
            return "unresolved", "no_consistent_v0", {}
        if r["anchor_ambiguous"]:  # an event inside the anchor fetch's span: the placements disagree about this V0
            return "unresolved", "ambiguous_anchor_placement", {}
        cands |= r["values"]  # target_ambiguous (same-slot placements): every value is a candidate, the lower P&L is taken
    same = any(ev["slot"] == e for ev in events) or (hold and any(ev["slot"] == x for ev in events))
    return "ok", sorted(v - pending for v in cands), {"hold": hold, "same_slot": same}


class LpContext:
    """What the pricing needs: the lphist file, the anchors (final map or snapshot V0, pending, fetch span), the merge's
    unexplained and unresolved pools, and the final fetch's last context slot."""

    def __init__(self, lh: Any, anchors: dict[str, Any], static_bad: dict[str, str], fetch_slot_max: int | None):
        self.lh, self.anchors, self.static_bad, self.fetch_slot_max = lh, anchors, static_bad, fetch_slot_max

    def problem(self, pool: str) -> str | None:
        if pool in self.static_bad:
            return self.static_bad[pool]
        ent = self.lh.data.get(pool)
        if not isinstance(ent, dict):
            return "not_in_lphist"
        if not ent.get("resolved"):
            return f"lphist_unresolved:{ent.get('reason')}"
        if self.fetch_slot_max is not None:  # the history must be complete up to the final fetch
            sl = ent.get("supply_slot")
            if not isinstance(sl, int):
                return "lphist_supply_slot_missing"
            if sl < self.fetch_slot_max:
                return "supply_read_before_final_fetch_end"
        a = self.anchors.get(pool)
        if not isinstance(a, tuple):
            return a or "no_anchor"
        if not (isinstance(self.lh.t_from, int) and self.lh.t_from <= a[2][0] - 1):
            return "lphist_window_does_not_cover_anchor"
        return None


def bind_final_fetch(final_fetch_map: Path, meta: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The FINAL fetch map's `.fetch.json` and `.detail.json` (the merged OUT carries neither). Each must hash to the merge meta's
    `sha256.fetch` and `sha256.final_detail`, so a hand-copied file cannot switch off the supply-slot check. Returns (fetch doc, detail)."""
    sh = meta.get("sha256") if isinstance(meta.get("sha256"), dict) else {}
    fj, dj = vm.side(final_fetch_map, ".fetch.json"), vm.side(final_fetch_map, ".detail.json")
    for f, key in ((fj, "fetch"), (dj, "final_detail")):
        if not f.is_file():
            raise Refused([f"--final-fetch-map: {f} is missing"])
        if fw._sha256_file(f) != sh.get(key):
            raise Refused([f"--final-fetch-map: {f} does not hash to the merge meta's sha256.{key}"])
    return vm.load_json_dict(fj, "fetch file"), vm.load_detail(final_fetch_map)


def load_anchors(fdoc: dict[str, Any], final_detail: dict[str, Any], merged: dict[str, int | None], meta: dict[str, Any] | None, snapshots: Sequence[Path]) -> tuple[dict[str, Any], int | None]:
    """pool -> (V0, pending, fetch span) or a reason string; plus the final fetch's fetch_slot_max. A pool the merge filled
    from a snapshot is anchored at the LAST snapshot that has it (that snapshot's V0 and span); every other pool at the
    final fetch's detail and span. V0 is `vm._v0` (a short account without pending counters has V0 = its stored V)."""
    import tools.pumpswap_virtual as pv

    spans = (meta or {}).get("lp_moves", {}).get("fetch_spans_unix") if meta else None
    final_span = vm.span_of(fdoc) if fdoc else None  # carries fetch_slot_min/max when recorded
    if final_span is None and spans and spans[-1]:
        final_span = tuple(spans[-1])
    snaps: list[tuple[dict[str, int | None], dict[str, Any], tuple[int, int] | None]] = []
    for i, sp_path in enumerate(snapshots):
        try:
            sspan = vm.snapshot_span(sp_path)
        except Refused:
            sspan = None
        if sspan is None and spans and i < len(spans) and spans[i]:
            sspan = tuple(spans[i])
        snaps.append((pv.load_map(sp_path), vm.load_detail(sp_path), sspan))
    filled = set((meta or {}).get("filled_pools", []))
    out: dict[str, Any] = {}
    for p, v in merged.items():
        if v is None:
            continue
        if p in filled:
            src = next(((m, d, s) for m, d, s in reversed(snaps) if m.get(p) is not None), None)
            if src is None:
                out[p] = "filled_pool_has_no_snapshot"
                continue
            detail, span = src[1], src[2]
        else:
            detail, span = final_detail, final_span
        b = vm._v0(detail, p, v)
        if b is None:
            out[p] = "no_v_base"
        elif span is None:
            out[p] = "no_fetch_span"
        else:
            out[p] = (b, b - v, span)
    return out, fdoc.get("fetch_slot_max") if isinstance(fdoc.get("fetch_slot_max"), int) else None


def load_lp_moves(meta: dict[str, Any], meta_path: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """The merge's unexplained and unresolved pools from the sidecars named in meta `lp_moves`, each sha256-checked. A
    merge meta without `lp_moves` is refused. Ids stay in the returned dict and the files; counts only go to the report."""
    lm = meta.get("lp_moves")
    if not isinstance(lm, dict):
        raise Refused([f"--vmap-merge-meta {meta_path} has no lp_moves: it is not an LP-law merge; re-merge before (B)"])
    bad: dict[str, str] = {}
    for key, kind in (("unexplained", "merge_unexplained"), ("unresolved", "merge_unresolved")):
        name, want = lm.get(f"{key}_file"), lm.get(f"{key}_sha256")
        f = meta_path.parent / str(name)
        if not name or not f.is_file() or fw._sha256_file(f) != want:
            raise Refused([f"lp_moves.{key}_file {name!r} beside {meta_path} is missing or does not hash to lp_moves.{key}_sha256"])
        doc = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            for p, why in doc.items():
                bad[p] = f"{kind}:{why}"
        else:
            for p in doc:
                bad[p] = kind
    return bad, {k: lm.get(k) for k in ("n_explained", "n_unexplained", "n_unresolved", "ceiling", "unexplained_sha256", "unresolved_sha256")}


def first_lphist_run(ledger: Path, clean_clock: str, read_end: str, test_window: bool) -> dict[str, Any] | None:
    """The FIRST completed lphist-entered line for this window; later lines are never used."""
    if not ledger.is_file():
        return None
    for n, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            m = json.loads(line)
        except ValueError:
            raise Refused([f"{ledger} line {n} is not valid JSON"])
        if m.get("state") == "COMPLETED" and _same_window(m, clean_clock, read_end, test_window):
            return m
    return None


def build_lp_context(lphist: Path, ledger: Path, marker: dict[str, Any], test_window: bool, merged: dict[str, int | None], meta: dict[str, Any], meta_path: Path, snapshots: Sequence[Path], final_fetch_map: Path) -> tuple[LpContext, dict[str, Any]]:
    """Every check that reads no V-priced row; all refusals come before STARTED. `lphist` must be the FIRST completed lphist-entered
    run for the window, recorded against THIS merge meta (sha256), cover the window start - 1 h and the final fetch's end; the merge meta
    must carry lp_moves; the snapshots must hash to it; the final fetch map's `.fetch.json` and `.detail.json` must hash to it; off the test
    window the final fetch must record fetch_slot_max."""
    lh = vm.LpHistory(lphist)
    first = first_lphist_run(ledger, marker["clean_clock"], marker["read_end"], test_window)
    if first is None:
        raise Refused([f"{ledger} has no completed lphist-entered line for this window"])
    if first.get("sha256") != lh.sha256:
        raise Refused([f"--lphist {lphist} sha256 {lh.sha256} is not the first completed lphist run for this window ({first.get('sha256')}, utc {first.get('utc_time')}); later runs are never used"])
    meta_sha = fw._sha256_file(meta_path)
    if first.get("merge_meta_sha256") != meta_sha or lh.meta.get("merge_meta_sha256") != meta_sha:
        raise Refused(["the first lphist run was not recorded against this --vmap-merge-meta (merge_meta_sha256 differs)"])
    start = int(fw.parse_clock(marker["clean_clock"]).timestamp())
    if not (isinstance(lh.t_from, int) and lh.t_from <= start - LPHIST_PAD_S):
        raise Refused([f"--lphist range starts at {lh.t_from}, after the window start - 1 h ({start - LPHIST_PAD_S})"])
    static_bad, lp_info = load_lp_moves(meta, meta_path)
    sh = meta.get("sha256", {}) if isinstance(meta.get("sha256"), dict) else {}
    snaps = sorted(snapshots, key=lambda x: x.name)
    if [fw._sha256_file(s) for s in snaps] != list(sh.get("snapshots", [])):
        raise Refused(["--snapshot files (sorted by name) do not hash to the merge meta's sha256.snapshots"])
    if [fw._sha256_file(vm.side(s, ".detail.json")) for s in snaps] != list(sh.get("snapshot_details", [])):
        raise Refused(["--snapshot detail files do not hash to the merge meta's sha256.snapshot_details"])
    fdoc, final_detail = bind_final_fetch(final_fetch_map, meta)
    fspan = vm.span_of(fdoc)
    if fspan is None:
        raise Refused(["the final fetch record has no usable span"])
    if not (isinstance(lh.t_to, int) and lh.t_to >= fspan[1]):
        raise Refused([f"--lphist range ends at {lh.t_to}, before the final fetch's end ({fspan[1]})"])
    anchors, slot_max = load_anchors(fdoc, final_detail, merged, meta, snaps)
    if slot_max is None and not test_window:
        raise Refused(["the final fetch records no fetch_slot_max: the supply-slot check cannot run"])
    ctx = LpContext(lh, anchors, static_bad, slot_max)
    info = {"lphist_sha256": lh.sha256, "lphist_meta_sha256": lh.meta_sha256, "first_run_utc": first.get("utc_time"), "n_pools": lh.meta.get("n_pools"), "n_unresolved": lh.meta.get("n_unresolved"), "t_from_unix": lh.t_from, "t_to_unix": lh.t_to, "attempts": lh.meta.get("attempts"), "merge_lp_moves": lp_info, "merge_meta_sha256": meta_sha, "final_fetch_slot_max": slot_max}
    return ctx, info


def plan_lp(rows: Sequence[dict[str, Any]], ctx: LpContext, merged: dict[str, int | None], use_pending: bool = True) -> tuple[dict[str, set[int]], dict[tuple[str, int], dict[str, Any]]]:
    """(overrides, info). overrides: pool -> stored-V candidates that differ from the merged map's value. info: per ENTERED
    trade key -> {"bad": {pool: reason}, "hold", "same_slot", "changed": {pools}}. Every pumpswap pool the mint touched is
    examined (the touch definition of Am.5 s5); a pool with a problem (merge unexplained/unresolved, lphist unresolved,
    ambiguous anchor placement) is `bad`, which the caller turns into null-V."""
    overrides: dict[str, set[int]] = {}
    info: dict[tuple[str, int], dict[str, Any]] = {}
    for r in rows:
        if not r["entered"]:
            continue
        missing = [t for t in LP_TAGS if t not in r]
        if missing:
            raise Refused([f"entered row {r.get('mint')} has no {missing} tag(s): the LP tracking did not run; refusing (fail closed)"])
        rec: dict[str, Any] = {"bad": {}, "hold": False, "same_slot": False, "changed": set(), "cands": {}}
        e, x = r["entry_slot"], r["exit_slot"]
        if x is None:
            x = r["last_slot"]
        for pool in r["pumpswap_pools"]:
            if pool == NO_POOL_FIELD:
                continue
            if merged.get(pool) is None and pool not in ctx.static_bad:
                continue  # null in the map: the existing null-V tags already cover it
            why = ctx.problem(pool)
            if why:
                rec["bad"][pool] = why
                continue
            if e is None:
                continue
            v0, pend, span = ctx.anchors[pool]
            status, res, fl = pool_values(e, e if x is None else x, v0, pend if use_pending else 0, ctx.lh.data[pool]["events"], span)
            if status != "ok":
                rec["bad"][pool] = res
                continue
            rec["hold"] |= fl["hold"]
            rec["same_slot"] |= fl["same_slot"]
            if res != [merged[pool]]:  # candidates are THIS trade's own (its entry and exit slots), never another trade's
                overrides.setdefault(pool, set()).update(res)
                rec["changed"].add(pool)
                rec["cands"][pool] = list(res)
        info[fw.key_of(r)] = rec
    return overrides, info


def write_vmap_file(path: Path, vmap: dict[str, int | None]) -> str:
    """A deterministic V map file (no timestamp), readable by `pumpswap_virtual.load_map`; returns its sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"n_pools": len(vmap), "n_null": sum(1 for v in vmap.values() if v is None), "v": dict(sorted(vmap.items()))}
    path.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    return fw._sha256_file(path)


MAX_COMBOS = 64  # candidate combinations per trade; above it the trade's pools are null-V


def lp_min_rows(base_rows: Sequence[dict[str, Any]], plan: tuple[dict[str, set[int]], dict[tuple[str, int], dict[str, Any]]], merged: dict[str, int | None], run_pass: Callable[[Path, str], list[dict[str, Any]]], map_dir: Path, tag: str, max_combos: int = MAX_COMBOS) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Price every trade that has candidate V values at each of them and keep, per trade and per leg (`flat`, `press`
    separately), the LOWER P&L over the FULL PRODUCT of that trade's own candidates for its changed pools (<= max_combos,
    else the trade is null-V). `run_pass(map_path, pass_tag)` is the adapter scoring pass at a V map and bakes in k (entry and
    exit lag), so this is reusable at k(p50) and k(p90); it must not raise on an entered-set change. Candidates are per trade
    (`info[key]["cands"]`): trades that share a changed pool are put in different groups so no map has to give one pool two values;
    each group's passes cover its trades' products (mixed radix over the pools, wrapping).

    The entered set is (A)'s. A mint a candidate pass enters that the base pass did not is NOT added (counted in
    `n_would_add`). A trade the pass would drop, or whose entry or exit fill slot it would move, is listed in `shifted`
    (key -> reasons): its (e, x) classification no longer holds, and the caller treats its pools as null-V. Returns (rows, report)."""
    _unused, info = plan
    maps: list[dict[str, Any]] = []
    shifted: dict[tuple[str, int], set[str]] = {}
    sched: dict[tuple[str, int], tuple[list[str], int]] = {}
    for key, rec in sorted(info.items()):
        pools = sorted(rec["changed"])
        if not pools:
            continue
        n = 1
        for q in pools:
            n *= len(rec["cands"][q])
        if n > max_combos:
            shifted.setdefault(key, set()).add("too_many_candidate_combinations")
        else:
            sched[key] = (pools, n)
    groups: list[list[tuple[str, int]]] = []
    for key in sched:  # greedy: a group holds no two trades with a changed pool in common
        for g in groups:
            if not any(set(sched[key][0]) & set(sched[o][0]) for o in g):
                g.append(key)
                break
        else:
            groups.append([key])
    base_entered = {fw.key_of(r) for r in base_rows if r["entered"]}
    by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    would_add: set[tuple[str, int]] = set()
    seen: set[tuple] = set()
    for gi, g in enumerate(groups):
        for j in range(max(sched[k][1] for k in g)):
            m = dict(merged)
            sig = []
            for key in g:
                pools, n = sched[key]
                stride = 1
                for q in pools:
                    c = info[key]["cands"][q]
                    m[q] = c[((j % n) // stride) % len(c)]
                    sig.append((q, m[q]))
                    stride *= len(c)
            sig_t = tuple(sorted(sig))
            if sig_t in seen:
                continue
            seen.add(sig_t)
            path = map_dir / f"vmap-{tag}-{gi}-{j}.json"
            sha = write_vmap_file(path, m)
            rows_j = run_pass(path, f"{tag}{gi}-{j}")
            for r in rows_j:
                k = fw.key_of(r)
                if r["entered"] and k not in base_entered:
                    would_add.add(k)  # not added: the entered set is (A)'s
                if k in g:
                    by_key.setdefault(k, []).append(r)
            maps.append({"tag": f"{tag}-{gi}-{j}", "file": path.name, "sha256": sha, "n_overridden_pools": len({q for k in g for q in sched[k][0]})})
    out: list[dict[str, Any]] = []
    for r in base_rows:
        key = fw.key_of(r)
        if key in sched and by_key.get(key):
            rr = by_key[key]
            if any(not q["entered"] for q in rr):
                shifted.setdefault(key, set()).add("entry_dropped_in_candidate_pass")
            if any((q.get("entry_slot"), q.get("exit_slot")) != (r.get("entry_slot"), r.get("exit_slot")) for q in rr):
                shifted.setdefault(key, set()).add("entry_or_exit_slot_moved_with_v")
            flat, press = min(q["flat"] for q in rr), min(q["press"] for q in rr)
            r = {**r, "flat": flat, "press": press, "flat_sol": flat / fw.LAMPORTS, "press_sol": press / fw.LAMPORTS}
        out.append(r)
    return out, {"n_passes": len(maps), "n_groups": len(groups), "maps": maps, "shifted": shifted, "n_would_add": len(would_add)}


def with_lp_bad(rows: Sequence[dict[str, Any]], info: dict[tuple[str, int], dict[str, Any]], shifted: dict[tuple[str, int], str]) -> list[dict[str, Any]]:
    """Section 7(c), last rule: a trade on an unexplained or unresolved pool is a null-V trade (Amendment 4 section 3)."""
    out = []
    for r in rows:
        key = fw.key_of(r)
        rec = info.get(key)
        if r["entered"] and rec is not None:
            ids = set(rec["bad"]) | (set(rec["changed"]) if key in shifted else set())
            if ids:
                r = {**r, "no_v_pools": sorted(set(r["no_v_pools"]) | ids)}
        out.append(r)
    return out


def lp_counts(info: dict[tuple[str, int], dict[str, Any]], shifted: dict[tuple[str, int], Any], n_would_add: int = 0) -> dict[str, Any]:
    def n(pred: Callable[[dict[str, Any]], bool]) -> int:
        return sum(1 for r in info.values() if pred(r))

    def kind(prefix: str) -> Callable[[dict[str, Any]], bool]:
        return lambda r: any(w.startswith(prefix) for w in r["bad"].values())

    return {
        "n_entered": len(info),
        "n_entered_on_lp_moved_pools": n(lambda r: bool(r["changed"])),
        "n_entered_with_lp_event_inside_hold": n(lambda r: r["hold"]),
        "n_entered_same_slot_both_ways": n(lambda r: r["same_slot"]),
        "n_entered_touching_merge_unexplained": n(kind("merge_unexplained")),
        "n_entered_touching_merge_unresolved": n(kind("merge_unresolved")),
        "n_entered_touching_lphist_unresolved": n(lambda r: any(w.startswith(("lphist_unresolved", "not_in_lphist", "lphist_window", "supply_read")) for w in r["bad"].values())),
        "n_entered_touching_ambiguous_or_inconsistent": n(lambda r: any(w in ("ambiguous_anchor_placement", "no_consistent_v0", "too_many_ambiguous_events", "event_slot_missing") for w in r["bad"].values())),
        "n_entered_touching_any_unresolved": n(lambda r: bool(r["bad"])),
        "n_entered_slot_moved_in_candidate_pass": sum(1 for v in shifted.values() if "entry_or_exit_slot_moved_with_v" in v),
        "n_entered_dropped_in_candidate_pass": sum(1 for v in shifted.values() if "entry_dropped_in_candidate_pass" in v),
        "n_entered_over_combo_cap": sum(1 for v in shifted.values() if "too_many_candidate_combinations" in v),
        "n_entered_multi_pool_product": n(lambda r: len(r["changed"]) >= 2),
        "n_mints_candidate_pass_would_add_not_added": n_would_add,
        "same_slot_rule": "tape rows carry no usable transaction position against an LP event, so every same-slot event is priced both ways (lower P&L)",
    }


def book_summary(rows: Sequence[dict[str, Any]], runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    nv = null_v_assessment(rows)
    g = gate_block(rows, runs)
    return {"verdict": NOT_DECIDABLE if nv["not_decidable"] else g["verdict"], "gate_verdict": g["verdict"], "null_v_not_decidable": nv["not_decidable"], "n_entered": g["n_entered"], "flat_15": g["flat_15"], "pressure_scale_1": g["pressure_scale_1"]}


# --- lphist-entered ---------------------------------------------------------------------


def run_lphist_entered(walk_dir: Path, final_out_dir: Path, final_ledger: Path, vmap: Path, vmap_sha256: str, out: Path, artifact_dir: Path = fw.DEFAULT_ARTIFACT_DIR, freeze_commit: str | None = fw.DEFAULT_FREEZE_COMMIT, frozen_manifest_md5: str | None = None, test_window: bool = False, score_fn: ScoreFn = score_hours_v, vmap_merge_meta: Path | None = None, final_fetch_map: Path | None = None, rpc: Callable[[str, list], Any] | None = None, rps: float = 5.0, sleep: Callable[[float], None] | None = None) -> dict[str, Any]:
    """After the FINAL (A) read: the LP history of every pool an entered trade touched (Am.5 s5 touch: any pumpswap pool the
    mint printed on in the scoring worker's tape), over [window start - 1 h, the final fetch's end]. Reads pool fields and
    transaction logs only. The tape pass is FROZEN pricing (no V), so it shows nothing but (A), which is already read; no V-priced
    result is opened. Writes OUT (read-only) and OUT.meta.json, and appends a COMPLETED line to LPHIST_RUNS.jsonl beside the FINAL
    out dir. The RPC URL is built inside Python and never printed. Pool ids appear in files only."""
    import time

    import tools.pumpswap_virtual as pv

    if rps > pv.MAX_RPS:
        raise Refused([f"rps {rps} > {pv.MAX_RPS}: walkers share Helius"])
    if test_window and str(walk_dir.resolve()).startswith(REAL_BLOCKS_PREFIX):
        raise Refused([f"--test-window is refused on a walk dir under {REAL_BLOCKS_PREFIX}"])
    marker = find_final_marker(final_out_dir, final_ledger, test_window)
    cc, re_ = fw.parse_clock(marker["clean_clock"]), fw.parse_clock(marker["read_end"])
    fw.check_window(cc, re_, test_window)
    vinfo = check_vmap(vmap, vmap_sha256)
    if vmap_merge_meta is None or final_fetch_map is None:
        raise Refused(["lphist-entered needs --vmap-merge-meta and --final-fetch-map"])
    merge_doc = check_merge_meta(vmap_merge_meta, vmap_sha256)  # non-dry, new: true, after the cutoff
    merge_sha = fw._sha256_file(vmap_merge_meta)
    meta_path = vm.side(out, ".meta.json")
    for p in (out, meta_path):
        if p.exists():
            raise Refused([f"{p} exists; refusing to overwrite"])
    fdoc, _detail = bind_final_fetch(final_fetch_map, merge_doc)
    span = vm.span_of(fdoc)
    if span is None:
        raise Refused(["the final fetch record has no usable span: the lphist range ends at the final fetch's end"])
    t_from, t_to = int(cc.timestamp()) - LPHIST_PAD_S, span[1]
    run_start = datetime.now(timezone.utc)
    errors = s12.check_frozen(artifact_dir, frozen_manifest_md5, freeze_commit)
    if errors:
        raise Refused(errors)
    runs = fw.score_runs(fw.read_rows(final_out_dir / fw.RUNS_NAME))
    if not runs:
        raise Refused([f"{final_out_dir / fw.RUNS_NAME} records no score run"])
    last = runs[-1]
    pool = e11._hours_range(last["pool_from"], last["to_exclusive"])
    problems = fw.hour_problems(walk_dir, pool)
    if problems:
        raise Refused(problems)
    a_rows = fw.read_rows(final_out_dir / fw.ROWS_NAME)
    lo, hi = fw.ms(cc), min(fw.ms(fw.hour_dt(last["to_exclusive"])), fw.ms(re_))
    scratch = Path(tempfile.mkdtemp(prefix="lphist-scratch-", dir=str(out.resolve().parent)))
    try:
        rows, thr = score_fn(walk_dir, pool, artifact_dir, scratch / "frozen", vmap, "v", scratch / "counts", True)
        rows = window_rows(rows, thr, lo, hi)
        check_reproduction(a_rows, rows)
        pools = sorted({p for r in rows if r["entered"] for p in r["pumpswap_pools"] if p != NO_POOL_FIELD})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if rpc is None:
        from tools.pumpswap_simulate import Rpc

        rpc = Rpc(pv._rpc_url())
    attempts: list[dict[str, Any]] = []
    hist, calls = lph.fetch_lp_history(rpc, pools, t_from, t_to, rps=rps, attempts=attempts, sleep=sleep or time.sleep)
    pools_path = vm.side(out, ".pools.json")
    vm._write_new(pools_path, json.dumps(pools) + "\n", readonly=True)
    vm._write_new(out, json.dumps(hist, sort_keys=True) + "\n", readonly=True)
    n_unres = sum(1 for e in hist.values() if not e["resolved"])
    sha = fw._sha256_file(out)
    utc = _utc()
    meta = {"sha256": sha, "n_pools": len(hist), "n_unresolved": n_unres, "n_events": sum(len(e["events"]) for e in hist.values()), "calls": calls, "utc": utc, "t_from_unix": t_from, "t_to_unix": t_to, "pools_sha256": fw._sha256_file(pools_path), "vmap_sha256": vinfo["sha256"], "merge_meta_sha256": merge_sha, "run_start_utc": run_start.strftime(TS_FMT), "run_start_unix": int(run_start.timestamp()), "attempts": attempts, "kind": "entered_touch_pools", "reasons": {r: sum(1 for e in hist.values() if e["reason"] == r) for r in sorted({e["reason"] for e in hist.values() if e["reason"]})}}
    vm._write_new(meta_path, json.dumps(meta, indent=1, sort_keys=True) + "\n", readonly=True)
    line = {"schema": SCHEMA_LPHIST_RUN, "state": "COMPLETED", "clean_clock": marker["clean_clock"], "read_end": marker["read_end"], "test_window": bool(test_window), "sha256": sha, "meta_sha256": fw._sha256_file(meta_path), "merge_meta_sha256": merge_sha, "utc_time": utc, "n_pools": len(hist), "n_unresolved": n_unres, "attempts": attempts, "out": str(out.resolve())}
    fw.ledger_append(final_out_dir.resolve().parent / LPHIST_RUNS_NAME, line)
    return line


def lphist_entered_main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(prog="exp012_forward_vbook lphist-entered", description=run_lphist_entered.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--walk-dir", required=True)
    ap.add_argument("--final-out-dir", required=True)
    ap.add_argument("--final-ledger", required=True)
    ap.add_argument("--vmap", required=True)
    ap.add_argument("--vmap-sha256", required=True)
    ap.add_argument("--out", required=True, help="the lphist file; OUT.meta.json and OUT.pools.json are written beside it")
    ap.add_argument("--vmap-merge-meta", required=True, help="the merge's OUT.merge.json; its sha256 is recorded in the lphist meta and the LPHIST_RUNS line")
    ap.add_argument("--final-fetch-map", required=True, help="the FINAL fetch map; its .fetch.json and .detail.json must hash to the merge meta")
    ap.add_argument("--rps", type=float, default=5.0)
    ap.add_argument("--test-window", action="store_true")
    ap.add_argument("--artifact-dir", default=None)
    ap.add_argument("--freeze-commit", default=None)
    ap.add_argument("--frozen-manifest-md5", default=None)
    a = ap.parse_args(list(argv))
    try:
        line = run_lphist_entered(Path(a.walk_dir), Path(a.final_out_dir), Path(a.final_ledger), Path(a.vmap), a.vmap_sha256, Path(a.out), Path(a.artifact_dir) if a.artifact_dir else fw.DEFAULT_ARTIFACT_DIR, a.freeze_commit or fw.DEFAULT_FREEZE_COMMIT, a.frozen_manifest_md5, a.test_window, vmap_merge_meta=Path(a.vmap_merge_meta), final_fetch_map=Path(a.final_fetch_map), rps=a.rps)
    except Refused as exc:
        return _refuse(exc)
    except (SystemExit, Exception) as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}", file=sys.stderr)  # the type only: an RPC error message may hold the URL
        return 3
    print(f"lphist-entered: n_pools={line['n_pools']} n_unresolved={line['n_unresolved']} sha256={line['sha256']}", file=sys.stderr)
    return 0


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
    vmap_merge_meta: Path | None = None,
    runs_ledger: Path | None = None,
    lphist: Path | None = None,
    snapshots: Sequence[Path] = (),
    final_fetch_map: Path | None = None,
) -> dict[str, Any]:
    if test_window and str(walk_dir.resolve()).startswith(REAL_BLOCKS_PREFIX):
        raise Refused([f"--test-window is refused on a walk dir under {REAL_BLOCKS_PREFIX}"])
    if runs_ledger is not None and not test_window:
        raise Refused(["--runs-ledger is refused on a non-test window: the ledger is always <final-out-dir>/../VBOOK_RUNS.jsonl"])
    if vmap_merge_meta is None and not test_window:
        raise Refused(["--vmap-merge-meta (the V map merge's OUT.merge.json) is required unless --test-window"])
    marker = find_final_marker(final_out_dir, final_ledger, test_window)  # 0. before any row is opened
    cc, re_ = fw.parse_clock(marker["clean_clock"]), fw.parse_clock(marker["read_end"])
    fw.check_window(cc, re_, test_window)
    vinfo = check_vmap(vmap, vmap_sha256)
    merge_doc = check_merge_meta(vmap_merge_meta, vmap_sha256) if vmap_merge_meta is not None else None
    ledger_path = runs_ledger if runs_ledger is not None else final_out_dir.resolve().parent / RUNS_LEDGER_NAME
    if lphist is None and not test_window:
        raise Refused(["--lphist (the first completed lphist-entered run for this window) is required unless --test-window"])
    lp_ctx: LpContext | None = None
    lp_info: dict[str, Any] = {}
    if lphist is not None:  # no V-priced row is read here
        from tools.pumpswap_virtual import load_map

        if vmap_merge_meta is None or merge_doc is None or final_fetch_map is None:
            raise Refused(["--lphist needs --vmap-merge-meta and --final-fetch-map (the FINAL fetch map whose .fetch.json and .detail.json the merge meta binds)"])
        lp_ctx, lp_info = build_lp_context(lphist, final_out_dir.resolve().parent / LPHIST_RUNS_NAME, marker, test_window, load_map(vmap), merge_doc, vmap_merge_meta, snapshots, final_fetch_map)
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
    claimed = False
    done = False
    base = {"schema": SCHEMA_RUN, "clean_clock": marker["clean_clock"], "read_end": marker["read_end"], "test_window": bool(test_window), "final_rows_sha256": marker.get("rows_sha256"), "vmap_sha256": vinfo["sha256"], "merge_meta_sha256": fw._sha256_file(vmap_merge_meta) if vmap_merge_meta is not None else None, "lphist_sha256": lp_info.get("lphist_sha256"), "out_dir": str(out_dir.resolve())}
    old_handler = None
    try:
        def one(tag: str, mode: str, frozen: bool, vm_path: Path = vmap) -> tuple[list[dict[str, Any]], dict[str, Any], float]:
            rows, thr = score_fn(walk_dir, pool, artifact_dir, scratch_root / tag, vm_path, mode, scratch_root / f"counts-{tag}", frozen)
            return window_rows(rows, thr, lo, hi), read_counts(scratch_root / f"counts-{tag}"), thr

        # the frozen pass reveals nothing about (B) and (A) is already read: it spends nothing
        repro_rows, _c, thr = one("unpatched", "v", True)
        if last.get("threshold") is not None and thr != last["threshold"]:
            raise Refused([f"frozen threshold {thr!r} differs from the FINAL run's {last['threshold']!r}"])
        repro = check_reproduction(a_rows, repro_rows)  # before (B) is read
        commit = fw._git_commit()
        # the claim: immediately before the first V-priced pass
        n_prior = claim_window(ledger_path, {**base, "state": "STARTED", "utc_time": _utc(), "git_commit": commit}, test_window)
        claimed = True
        if threading.current_thread() is threading.main_thread():
            old_handler = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit("SIGTERM")))
        v_rows, v_counts, _ = one("v", "v", False)
        bad = check_entered_set(a_rows, v_rows, "mcap_mode v")
        if bad:
            raise Refused(bad)
        vault_rows, vault_counts, _ = one("vault", "vault", False)
        vault_bad = check_entered_set(a_rows, vault_rows, "mcap_mode vault")

        lp_rep: dict[str, Any] | None = None
        extra_blockers: list[str] = []
        map_bytes: dict[str, bytes] = {}
        b_rows = v_rows
        if lp_ctx is not None:
            from tools.pumpswap_virtual import load_map

            merged = load_map(vmap)
            map_dir = scratch_root / "vmaps"

            def run_pass(path: Path, tag: str) -> list[dict[str, Any]]:
                return one(tag, "v", False, path)[0]  # never refuses: an entered-set change is handled in lp_min_rows

            plan = plan_lp(v_rows, lp_ctx, merged, True)
            min_rows, minfo = lp_min_rows(v_rows, plan, merged, run_pass, map_dir, "c")
            b_rows = with_lp_bad(min_rows, plan[1], minfo["shifted"])
            primary = book_summary(b_rows, runs)
            sens = book_summary(with_lp_bad(v_rows, plan[1], {}), runs)  # (d)(i): LP-moved pools at their final-map V0, same null-V set
            if sens["verdict"] != primary["verdict"]:
                extra_blockers.append(f"sensitivity (d)(i): (B) with LP-moved pools at their final-map V0 is {sens['verdict']}, the primary (c) is {primary['verdict']}")
            plan0 = plan_lp(v_rows, lp_ctx, merged, False)  # (d)(ii): pending = 0, report only
            rows0, minfo0 = lp_min_rows(v_rows, plan0, merged, run_pass, map_dir, "p0")
            p0 = book_summary(with_lp_bad(rows0, plan0[1], minfo0["shifted"]), runs)
            for f in sorted(map_dir.glob("vmap-*.json")):
                map_bytes[f.name] = f.read_bytes()
            lp_rep = {
                "lphist": lp_info,
                "counts": lp_counts(plan[1], minfo["shifted"], minfo["n_would_add"]),
                "vmaps": [{**m, "kind": "primary_c"} for m in minfo["maps"]] + [{**m, "kind": "pending0_report_only"} for m in minfo0["maps"]],
                "primary_map_is_the_merged_final_map": vinfo["sha256"],
                "sensitivity_final_map_v0": {**{k: sens[k] for k in ("verdict", "gate_verdict", "n_entered")}, "differs_from_primary": sens["verdict"] != primary["verdict"], "flat_15": sens["flat_15"], "pressure_scale_1": sens["pressure_scale_1"]},
                "pending_zero_report_only": {**{k: p0[k] for k in ("verdict", "gate_verdict", "n_entered")}, "flat_15": p0["flat_15"], "pressure_scale_1": p0["pressure_scale_1"]},
                "pricing_note": "stored V = V0 at the entry fill slot - final-map pending; same-slot events both ways and events inside the hold: lower P&L per leg",
            }
        null_v = null_v_assessment(b_rows)
        b_gate = gate_block(b_rows, runs)
        vault_gate = gate_block(vault_rows, runs)
        agreement = compare_modes(b_gate, vault_gate)
        if vault_bad:
            agreement = {**agreement, "agree_on_every_gate_condition": False, "vault_entered_set_problems": vault_bad}
        a_gate = gate_block(a_rows, runs)
        verdict = NOT_DECIDABLE if null_v["not_decidable"] else b_gate["verdict"]
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
            "vmap_merge": merge_doc,
            "prior_vbook_runs": n_prior,
            "lp_pricing": lp_rep,
            "live_blockers": live_blockers(agreement, vault_bad, null_v, b_gate["verdict"], extra_blockers),
            "adapter_file_sha256": hashlib.sha256(adapter_file.read_bytes()).hexdigest(),
            "git_commit": commit,
            "live_support_note": "Amendment 4 section 2: live needs (A) PASS and (B) at mcap_mode v PASS; V-correction can only remove support. This report does not decide live.",
        }
        json_bytes = (json.dumps(rep, indent=2, default=str) + "\n").encode("utf-8")
        # the ledger line first: a crash can never leave a report without its entry
        fw.ledger_append(ledger_path, {**base, "state": "DONE", "utc_time": _utc(), "git_commit": commit, "b_verdict": verdict, "report_sha256": hashlib.sha256(json_bytes).hexdigest()})
        done = True
        out_dir.mkdir(parents=True)
        fw.atomic_write(out_dir / REPORT_JSON, json_bytes)
        (out_dir / "vmaps").mkdir(exist_ok=True)
        for name, data in sorted(map_bytes.items()):  # every V map used, whose sha256 the report lists
            fw.atomic_write(out_dir / "vmaps" / name, data)
        fw.atomic_write(out_dir / REPORT_MD, render_markdown(rep).encode("utf-8"))
        return rep
    except BaseException as exc:  # noqa: BLE001 -- after STARTED the window is spent whatever happens
        if claimed and not done:
            reason = "; ".join(exc.reasons) if isinstance(exc, Refused) else f"{type(exc).__name__}: {exc}"
            fw.ledger_append(ledger_path, {**base, "state": "REFUSED_AFTER_READ", "utc_time": _utc(), "reason": reason[:2000]})
        raise
    finally:
        if old_handler is not None:
            signal.signal(signal.SIGTERM, old_handler)
        shutil.rmtree(scratch_root, ignore_errors=True)


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
    L += [f"V = 0 pools: {n['n_entered_touching_zero_v']} entered trades touch one ({n['n_top3_union_touching_zero_v']} in the top-3 union); report only"]
    lp = rep.get("lp_pricing")
    if lp:
        c = lp["counts"]
        L += ["", "## LP-law pricing (Amendment 5 s7)", f"lphist sha256 {lp['lphist']['lphist_sha256']} (meta {lp['lphist']['lphist_meta_sha256']}); pools {lp['lphist']['n_pools']}, unresolved {lp['lphist']['n_unresolved']}"]
        L += [f"- {k}: {v}" for k, v in c.items() if k != "same_slot_rule"] + [f"- {c['same_slot_rule']}"]
        L += [f"sensitivity (final-map V0 for LP-moved pools): {lp['sensitivity_final_map_v0']['verdict']} (differs from primary: {lp['sensitivity_final_map_v0']['differs_from_primary']})", f"pending = 0 (report only): {lp['pending_zero_report_only']['verdict']}"]
        L += [f"V map {m['file']} ({m['kind']}) sha256 {m['sha256']}" for m in lp["vmaps"]]
    L += ["", "## Live blockers (none is decided here)"] + [f"- {x}" for x in rep["live_blockers"]]
    L += ["", f"prior vbook runs on this window: {rep['prior_vbook_runs']}"]
    L += ["", "## Adapter counts", f"{json.dumps(rep['adapter_counts'])}", "", f"V map {rep['vmap']['path']} sha256 {rep['vmap']['sha256']} ({rep['vmap']['n_pools']} pools, {rep['vmap']['n_null']} null)", "", f"Result label: {rep['label']}."]
    return "\n".join(L) + "\n"


def _refuse(exc: Refused) -> int:
    for r in exc.reasons:
        print(f"REFUSED: {r}", file=sys.stderr)
    return exc.code


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "lphist-entered":
        return lphist_entered_main(args[1:])
    argv = args
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--walk-dir", required=True)
    ap.add_argument("--final-out-dir", required=True, help="the FINAL (A) out dir; its rows are read only via exp012_forward.read_rows")
    ap.add_argument("--final-ledger", required=True)
    ap.add_argument("--vmap", required=True)
    ap.add_argument("--vmap-sha256", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--vmap-merge-meta", default=None, help="the V map merge's OUT.merge.json (sha256.out must equal --vmap-sha256); required unless --test-window")
    ap.add_argument("--runs-ledger", default=None, help="default: VBOOK_RUNS.jsonl beside the FINAL out dir")
    ap.add_argument("--lphist", default=None, help="the lphist-entered output; refused unless it is the FIRST completed run for the window in LPHIST_RUNS.jsonl; required unless --test-window")
    ap.add_argument("--final-fetch-map", default=None, help="the FINAL fetch map (the file given to `fetch --new`): its .fetch.json and .detail.json must hash to the merge meta's sha256.fetch and sha256.final_detail; required with --lphist")
    ap.add_argument("--snapshot", action="append", default=[], help="a V map snapshot named by the merge meta (repeatable; sha256-checked against it)")
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
            vmap_merge_meta=Path(a.vmap_merge_meta) if a.vmap_merge_meta else None, runs_ledger=Path(a.runs_ledger) if a.runs_ledger else None,
            lphist=Path(a.lphist) if a.lphist else None, snapshots=[Path(x) for x in a.snapshot], final_fetch_map=Path(a.final_fetch_map) if a.final_fetch_map else None,
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
