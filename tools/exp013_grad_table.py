#!/usr/bin/env python3
"""EXP-013 graduation-classifier table builder. EXPLORATION, NOT EVIDENCE.

One tape pass over the 9-day exploration pool (pools A / C / B, the clean views
the EXP-012 freeze read) plus any verified expansion views (pool X,
`--extra-fast-view`). For every mint it finds the first bonding print at
progress >= 0.80 (tools/exp013_grad_trigger.py), records the 22 features at that
print, and scores a curve buy at trigger slot + k for k in 1, 4, 8, held through
migration and sold at the PumpSwap state at migration + max(4, k) slots (30 minute cap,
-30% stop on the curve, 0.5 SOL, direct, flat 15% and pressure fail models).

Output, under `<out-root>/<run-id>/` (default out-root /data/mal/exp013-grad):
  table.jsonl, table.md5       one row per (mint, k) that was scored
  censored.jsonl               one record per (mint, k) that was not (reason)
  trigger_counts.json          triggers per day (every trigger, censored or not),
                               and per day x k: rows, fills, outcomes, censored
  manifest.json                input shas (each VIEW.sha256's own sha256, the
                               pinned values for A/C/B, the extra views), code
                               shas, settings, row counts
  scratch/                     per-worker streams (kept, small)

Guards, all before any data file is opened (VIEW.sha256 files excepted):
  1. Roots: realpath, then the forbidden prefixes (the sealed backup / EXP-012 /
     EXP-011 / forward blocks, their clean copies and views), via
     tools.exp012_latency_sensitivity.guarded_roots; all three roots and
     --verify-view are required, no defaults; VIEW.sha256 of every root is
     checked and pinned (tools.exp011_freeze.resolve_roots).
  2. Extra views: tools.exp013_pool.load_extra_views (forbidden realpaths,
     VIEW.sha256 verified, hour fence to the expansion block, contiguity, no
     overlap with the built-in days or each other).
  3. Hour fence (assert_hour_fence): every hour to be read is inside the pool
     whitelists or the extra views, and outside every owned/reserved block and
     the forward period.
  4. Settings: 2 workers at most, 24 h buffer, 12 h home chunks (the recipe's).
  5. Output: realpath outside the forbidden locations and outside ARTIFACTS/;
     the run directory must not already hold files.

Run (see the PR body for the full MiScusi command):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp013_grad_table --verify-view --run-id <id> \\
    --fast-dir ... --oracle-insample-dir ... --oracle-live-dir ... [--extra-fast-view DIR ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Sequence

import tools.exp011_freeze as fz
import tools.exp013_grad_trigger as gt
from tools.exp011_build_table import FORCED_BUFFER_HOURS, FORCED_MAX_HOME_HOURS, FORCED_MAX_WORKERS, _md5_of_file, _sha256_of_file
from tools.exp013_pool import _LINE, _is_forbidden, assert_no_unlisted_data_files
from tools.exp012_latency_sensitivity import EXTRA_FORBIDDEN_DIRS, EXTRA_FORBIDDEN_STR, guarded_roots
from tools.exploration_entry_model import _rows_out_path, build_creator_history
from tools.exploration_entry_model_b2 import build_creator_history_b
from tools.exploration_entry_model_b3 import build_creator_history_c
from tools.exploration_exits import POOL_HOURS as POOL_A_HOURS, _hour_info as _hour_info_a, chunk_plan
from tools.latency_curve import _iter_trades
from tools.oracle_insample_adapter import POOL_C_HOURS, _hour_info_c
from tools.oracle_live_adapter import POOL_B_HOURS, _hour_info_b, iter_trade_rows_sorted, load_creates_b

DEFAULT_OUT_ROOT = Path("/data/mal/exp013-grad")
SCHEMA = "exp013_grad_table_v1"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")

# Hours that no table build may read (UTC hour keys, inclusive start, exclusive end), from
# docs/HOLDOUT_LEDGER.md: backup block + EXP-012 holdout + EXP-011 block + EXP-009 block (all of
# [2026-08-28T12, 2026-09-18T23)), and everything from the forward-paper clean clock on, which
# includes the forward walk.
FORBIDDEN_HOUR_RANGES: tuple[tuple[str, str], ...] = (
    ("2026-08-28T12", "2026-09-18T23"),
    ("2026-09-28T00", "9999-12-31T23"),
)


# --- guards ---------------------------------------------------------------------


def assert_hour_fence(pools_hours: dict[str, Sequence[str]]) -> int:
    """Every hour to be read must be in a pool whitelist (already by construction) and outside
    every forbidden range. Also refuses an hour listed in two pools (duplicate rows). Returns the
    number of distinct hours."""
    seen: dict[str, str] = {}
    for tag, hours in pools_hours.items():
        for h in hours:
            for lo, hi in FORBIDDEN_HOUR_RANGES:
                if lo <= h < hi:
                    raise SystemExit(f"hour fence: pool {tag} hour {h} is inside the forbidden range [{lo}, {hi})")
            if h in seen and seen[h] != tag:
                raise SystemExit(f"hour fence: hour {h} is in both pool {seen[h]} and pool {tag}")
            seen[h] = tag
    return len(seen)


def assert_out_dir_allowed(out_root: Path | str, run_id: str) -> Path:
    if not _RUN_ID.match(run_id):
        raise SystemExit(f"--run-id {run_id!r} must match {_RUN_ID.pattern}")
    out = Path(os.path.realpath(str(Path(out_root) / run_id)))
    from tools.exp013_pool import _is_forbidden

    bad = _is_forbidden(str(out))
    if bad is None:
        for d in EXTRA_FORBIDDEN_DIRS:
            if str(out) == d or str(out).startswith(d.rstrip("/") + "/"):
                bad = d
        for s in EXTRA_FORBIDDEN_STR:
            if str(out).startswith(s):
                bad = s
    if bad is not None:
        raise SystemExit(f"output {str(out)!r} is inside a reserved/forbidden location ({bad})")
    fz.assert_out_not_frozen_dir(out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"output {str(out)!r} already holds files; use a new --run-id")
    return out


def assert_builtin_roots_allowed(args: argparse.Namespace) -> None:
    """Realpath of every given built-in root against the forbidden locations used for extra views
    (adds the forward walk, EXP-012 forward and read dirs, the forward family)."""
    for label in ("fast_dir", "oracle_insample_dir", "oracle_live_dir"):
        root = getattr(args, label, None)
        if root is None:
            continue
        real = os.path.realpath(str(root))
        bad = _is_forbidden(real)
        if bad is not None:
            raise SystemExit(f"--{label.replace('_', '-')} {str(root)!r} resolves to {real!r}, inside a reserved holdout location ({bad})")


def view_listed_rels(root: Path) -> list[str]:
    rels = []
    for line in (root / "VIEW.sha256").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        m = _LINE.match(line)
        if m is None:
            raise SystemExit(f"{root / 'VIEW.sha256'}: unparsable line")
        rels.append(os.path.normpath(m.group(2)))
    return rels


def assert_builtin_views_complete(roots: dict[str, Path]) -> None:
    """No data file under a built-in root may be missing from its VIEW.sha256 (a loader must
    have exactly one possible reading of every hour)."""
    for label, root in roots.items():
        assert_no_unlisted_data_files(root, view_listed_rels(root))


def assert_recipe_settings(max_workers: int, buffer_hours: int, max_home_hours: int) -> None:
    if max_workers > FORCED_MAX_WORKERS or buffer_hours != FORCED_BUFFER_HOURS or max_home_hours != FORCED_MAX_HOME_HOURS:
        raise SystemExit(
            f"settings are fixed: max_workers<={FORCED_MAX_WORKERS}, buffer_hours={FORCED_BUFFER_HOURS}, max_home_hours={FORCED_MAX_HOME_HOURS} "
            f"(got {max_workers}, {buffer_hours}, {max_home_hours})"
        )


# --- worker plumbing --------------------------------------------------------------


def _worker(spec: dict[str, Any]) -> dict[str, Any]:
    """Pickle-friendly wrapper (Pool.map takes one argument)."""
    out = gt.run_worker_grad(
        spec["worker_id"],
        spec["home"],
        spec["buf"],
        spec["creator_hist"],
        spec["rows_path"],
        spec["cens_path"],
        spec["hour_info_fn"],
        row_iter_fn=spec["row_iter_fn"],
        creates_override=spec["creates"],
        ks=spec["ks"],
        pool_tag=spec["tag"],
    )
    out["tag"] = spec["tag"]
    out.pop("rows", None)
    out.pop("censored", None)
    return out


def _home_window_ms(home: list[str], hour_info_fn: Any) -> tuple[int, int]:
    start = int(datetime.strptime(home[0], "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp()) * 1000
    return start, hour_info_fn(home[-1])["end"] * 1000


def plan_pools(roots: dict[str, Path], extra_views: Sequence[Any] | None, scratch: Path, ks: Sequence[int]) -> list[dict[str, Any]]:
    """One spec per chunk of every pool, in a fixed order (A, C, B, X)."""
    from tools import exp013_pool as xp

    specs: list[dict[str, Any]] = []
    b, m = FORCED_BUFFER_HOURS, FORCED_MAX_HOME_HOURS

    def add(tag: str, plan: Any, hour_info_fn: Any, hist: dict[str, list[int]], row_iter_fn: Any = _iter_trades, all_creates: Any = None) -> None:
        for i, home, buf in plan:
            creates = None
            if all_creates is not None:
                lo, hi = _home_window_ms(home, hour_info_fn)
                creates = {mid: mf for mid, mf in all_creates.items() if lo <= mf[0].block_ms < hi}
            specs.append(
                {
                    "tag": tag, "worker_id": i, "home": home, "buf": buf, "creator_hist": hist, "hour_info_fn": hour_info_fn, "row_iter_fn": row_iter_fn,
                    "creates": creates, "ks": tuple(ks), "rows_path": _rows_out_path(scratch, tag, i),
                    "cens_path": (scratch / f"pool{tag}-w{i}.censored.jsonl"),
                }
            )

    fast, ins, live = roots["fast"], roots["insample"], roots["live"]
    add("A", chunk_plan(POOL_A_HOURS, FORCED_MAX_WORKERS, b, m), partial(_hour_info_a, backfill=fast), build_creator_history(fast))
    add("C", chunk_plan(POOL_C_HOURS, FORCED_MAX_WORKERS, b, m), partial(_hour_info_c, root=ins), build_creator_history_c(ins))
    add(
        "B", chunk_plan(POOL_B_HOURS, FORCED_MAX_WORKERS, b, m), partial(_hour_info_b, root=live), build_creator_history_b(live),
        row_iter_fn=iter_trade_rows_sorted, all_creates=load_creates_b(live),
    )
    if extra_views:
        hour_roots = {h: v.files[h] for v in extra_views for h in v.hours}
        hours = sorted(hour_roots)
        add("X", xp.plan_extra(hours, FORCED_MAX_WORKERS, b, m), partial(xp._hour_info_x, hour_roots=hour_roots), xp.build_creator_history_x(hours, hour_roots))
    return specs


def pools_hours(extra_views: Sequence[Any] | None) -> dict[str, list[str]]:
    out = {"A": list(POOL_A_HOURS), "C": list(POOL_C_HOURS), "B": list(POOL_B_HOURS)}
    if extra_views:
        out["X"] = sorted({h for v in extra_views for h in v.hours})
    return out


# --- aggregation ----------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(rows: Sequence[dict[str, Any]], censored: Sequence[dict[str, Any]], triggers_by_day: dict[str, int]) -> dict[str, Any]:
    """Trigger counts per day (all triggers), and per day x k the scored rows, fills, outcomes,
    censored reasons. A trigger is `scored` at k when it has a row there, `censored` when it has
    a censored record there; the two add up to the day's triggers at every k."""
    days = sorted(set(triggers_by_day) | {r["day"] for r in rows} | {c["day"] for c in censored})
    by_day: dict[str, Any] = {}
    for d in days:
        entry: dict[str, Any] = {"triggers": triggers_by_day.get(d, 0), "by_k": {}}
        for k in sorted({r["entry_land_k"] for r in rows} | {c["entry_land_k"] for c in censored}):
            rk = [r for r in rows if r["day"] == d and r["entry_land_k"] == k]
            ck = [c for c in censored if c["day"] == d and c["entry_land_k"] == k]
            outcomes: dict[str, int] = {}
            for r in rk:
                outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
            reasons: dict[str, int] = {}
            for c in ck:
                reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1
            entry["by_k"][str(k)] = {
                "scored": len(rk), "filled": sum(1 for r in rk if r["filled"]), "label_press_pos": sum(r["label"] for r in rk),
                "outcomes": dict(sorted(outcomes.items())), "censored": len(ck), "censored_reasons": dict(sorted(reasons.items())),
            }
        by_day[d] = entry
    return {"triggers_total": sum(triggers_by_day.values()), "by_day": by_day}


def pool_runs(extra_views: Sequence[Any] | None) -> dict[str, list[tuple[str, str]]]:
    """Contiguous hour runs (first hour, last hour) per pool tag: A, C and B are one run each,
    X has one per contiguous stretch of the extra views."""
    from tools.exp013_pool import _hour_range

    out: dict[str, list[tuple[str, str]]] = {tag: [(h[0], h[-1])] for tag, h in pools_hours(None).items()}
    if extra_views:
        hours = pools_hours(extra_views)["X"]
        runs: list[list[str]] = []
        for h in hours:
            if runs and _hour_range(runs[-1][-1], h)[1:] == [h]:
                runs[-1].append(h)
            else:
                runs.append([h])
        out["X"] = [(r[0], r[-1]) for r in runs]
    return out


def _hour_ms(hour: str) -> int:
    return int(datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp()) * 1000


def edge_flags(tag: str, day: str, trigger_ms: int | None, runs: dict[str, list[tuple[str, str]]]) -> dict[str, Any]:
    """Amendment 3 edge flags. A day is a left (right) edge day when it is the first (last) day of
    a contiguous run of the pool's hours. A row also carries `secs_cap_s`: on a left edge day a
    mint was created at or after the run start, so `secs_since_create` cannot exceed the seconds
    from the run start to the trigger (an older mint is absent, not a long-lived row)."""
    left = right = False
    cap: float | None = None
    for first, last in runs.get(tag, []):
        if trigger_ms is not None and not (_hour_ms(first) <= trigger_ms < _hour_ms(last) + 3_600_000):
            continue
        if day == first[:10]:
            left = True
            if trigger_ms is not None:
                cap = (trigger_ms - _hour_ms(first)) / 1000.0
        if day == last[:10]:
            right = True
    return {"edge_left": left, "edge_right": right, "secs_cap_s": cap}


def edge_report(
    runs: dict[str, list[tuple[str, str]]],
    triggers: dict[str, dict[str, int]],
    untriggered: dict[str, dict[str, dict[str, int]]],
    rows: Sequence[dict[str, Any]],
    censored: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Per pool run, per edge day: triggers, rows scored, censored by reason, and the mints
    created that day that never triggered by the end of their chunk's tape (`absent_proxy_n`;
    `absent_proxy_near` = max progress seen >= 0.5). The right edge's absent mints (those that
    would have triggered after the pool end) are inside that count. It is an upper bound: a mint
    that would never have triggered is in it too."""
    out = []
    for tag, rr in runs.items():
        for first, last in rr:
            for role, day in (("left", first[:10]), ("right", last[:10])):
                rk = [r for r in rows if r.get("pool") == tag and r["day"] == day]
                ck = [c for c in censored if c.get("pool") == tag and c["day"] == day]
                reasons: dict[str, int] = {}
                for c in ck:
                    reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1
                u = untriggered.get(tag, {}).get(day, {"n": 0, "near": 0})
                out.append(
                    {
                        "pool": tag, "run": [first, last], "role": role, "day": day, "triggers": triggers.get(tag, {}).get(day, 0),
                        "rows_scored": len(rk), "censored": len(ck), "censored_reasons": dict(sorted(reasons.items())),
                        "absent_proxy_n": u["n"], "absent_proxy_near": u["near"],
                    }
                )
    return out


def _code_shas() -> dict[str, str]:
    here = Path(__file__).resolve().parent
    names = ("exp013_grad_trigger.py", "exp013_grad_table.py", "latency_curve.py", "paper_curve_math.py", "exploration_exits.py", "exploration_entry_model.py")
    return {n: _sha256_of_file(here / n) for n in names}


def _git_head() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def settings_doc(ks: Sequence[int]) -> dict[str, Any]:
    return {
        "trigger_progress": gt.TRIGGER_PROGRESS, "ks": list(ks), "primary_k": gt.PRIMARY_K, "stop_loss": gt.STOP_LOSS, "cap_ms": gt.CAP_MS,
        "migration_exit_slots": gt.MIG_EXIT_SLOTS, "size_lamports": gt.ENTRY_SIZE, "priority_lamports": gt.ENTRY_PRIORITY_LAMPORTS,
        "portal_ppm": gt.ENTRY_PORTAL_PPM, "flat_fail": gt.FLAT_FAIL, "pressure_intercept": gt._curve().intercept, "spec": gt.GRAD_SPEC_ID,
        "feature_names": gt.GRAD_FEATURE_NAMES, "max_workers": FORCED_MAX_WORKERS, "buffer_hours": FORCED_BUFFER_HOURS, "max_home_hours": FORCED_MAX_HOME_HOURS,
    }


def build_table(
    out_dir: Path,
    roots: dict[str, Path],
    extra_views: Sequence[Any] | None = None,
    max_workers: int = FORCED_MAX_WORKERS,
    ks: Sequence[int] = gt.KS,
    run_id: str = "",
    sha_of_roots: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """The heavy pass. Call only after the guards (main does)."""
    assert_recipe_settings(max_workers, FORCED_BUFFER_HOURS, FORCED_MAX_HOME_HOURS)
    n_hours = assert_hour_fence(pools_hours(extra_views))
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = out_dir / "scratch"
    scratch.mkdir(exist_ok=True)
    t0 = time.time()
    specs = plan_pools(roots, extra_views, scratch, ks)
    print(f"EXP-013 grad: {n_hours} hours, {len(specs)} chunks, max_workers={max_workers}", file=sys.stderr, flush=True)
    results: list[dict[str, Any]] = []
    if max_workers <= 1 or len(specs) <= 1:
        results = [_worker(s) for s in specs]
    else:
        with mp.get_context("spawn").Pool(processes=min(max_workers, len(specs))) as pool:
            results = pool.map(_worker, specs, chunksize=1)
    triggers_by_day: dict[str, int] = {}
    triggers_by_tag: dict[str, dict[str, int]] = {}
    untriggered: dict[str, dict[str, dict[str, int]]] = {}
    for r in results:
        for d, n in r["triggers_by_day"].items():
            triggers_by_day[d] = triggers_by_day.get(d, 0) + n
            triggers_by_tag.setdefault(r["tag"], {})[d] = triggers_by_tag.get(r["tag"], {}).get(d, 0) + n
        for d, u in r["untriggered_by_create_day"].items():
            cell = untriggered.setdefault(r["tag"], {}).setdefault(d, {"n": 0, "near": 0})
            cell["n"] += u["n"]
            cell["near"] += u["near"]
    runs = pool_runs(extra_views)
    rows: list[dict[str, Any]] = []
    censored: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for s in specs:
        for target, path in ((rows, s["rows_path"]), (censored, s["cens_path"])):
            for item in _read_jsonl(path):
                ident = (item["mint"], item["entry_land_k"])
                if ident in seen:  # detected as each item is appended
                    raise SystemExit(f"duplicate (mint, k) {ident} (pool {s['tag']}, chunk {s['worker_id']})")
                seen.add(ident)
                item.update(edge_flags(s["tag"], item["day"], item.get("trigger_ms"), runs))
                target.append(item)
    key = lambda r: (r["day"], r["mint"], r["entry_land_k"])  # noqa: E731
    rows.sort(key=key)
    censored.sort(key=key)
    table = out_dir / "table.jsonl"
    with table.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    md5 = _md5_of_file(table)
    table.with_suffix(".md5").write_text(md5 + "\n", encoding="utf-8")
    with (out_dir / "censored.jsonl").open("w", encoding="utf-8") as fh:
        for c in censored:
            fh.write(json.dumps(c) + "\n")
    counts = summarize(rows, censored, triggers_by_day)
    edges = edge_report(runs, triggers_by_tag, untriggered, rows, censored)
    counts["edge_days"] = edges
    (out_dir / "trigger_counts.json").write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema": SCHEMA, "run_id": run_id, "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "git_head": _git_head(),
        "code_sha256": _code_shas(), "settings": settings_doc(ks), "roots": {k: str(v) for k, v in roots.items()},
        "view_sha256_file_sha256": sha_of_roots or {},
        "pinned_view_sha256": {"A": fz.VIEW_PIN_BY_POOL["A"], "C": fz.VIEW_PIN_BY_POOL["C"], "B": fz.VIEW_PIN_BY_POOL["B"]},
        "extra_views": [v.describe() for v in (extra_views or [])],
        "n_hours": n_hours, "n_rows": len(rows), "n_censored": len(censored), "n_triggers": counts["triggers_total"],
        "table_md5": md5, "wall_s": time.time() - t0, "tape_through_ms_by_chunk": [{"pool": r["tag"], "tape_through_ms": r["tape_through_ms"]} for r in results],
        "edge_days": edges, "pool_runs": {t: [list(x) for x in v] for t, v in runs.items()},
        "n_gap_hours_in_chunks": sum(r.get("gap_hours", 0) for r in results),
        "label": "1{press > 0}; the primary row is k=4 (rows carry entry_land_k)",
        "censoring_note": (
            "A (mint, k) whose entry landing, stop sell, migration sell or 30-minute cap runs past the end of the tape read is in censored.jsonl with a reason, never in table.jsonl. "
            "Mints created before a pool's first hour are not seen (left edge), and a mint whose 80% print or exit falls after a chunk's 24 h buffer is censored or absent (right edge)."
        ),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"EXP-013 grad: wrote {table} rows={len(rows)} censored={len(censored)} triggers={counts['triggers_total']} md5={md5}", file=sys.stderr, flush=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    fz.add_root_args(ap)
    fz.add_extra_view_arg(ap)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--max-workers", type=int, default=FORCED_MAX_WORKERS)
    ap.add_argument("--dry-run", action="store_true", help="run every guard and print the plan, read no tape")
    args = ap.parse_args(argv)
    assert_recipe_settings(args.max_workers, FORCED_BUFFER_HOURS, FORCED_MAX_HOME_HOURS)
    out = assert_out_dir_allowed(args.out_root, args.run_id)
    assert_builtin_roots_allowed(args)
    roots = guarded_roots(args)
    assert_builtin_views_complete(roots)
    extra_views = None
    if args.extra_fast_view:
        from tools.exp013_pool import load_extra_views

        extra_views = load_extra_views(args.extra_fast_view, fz.DAYS_ALL, allow_gap=args.allow_gap)
    n_hours = assert_hour_fence(pools_hours(extra_views))
    shas = {k: (_sha256_of_file(r / "VIEW.sha256") if r is not None else None) for k, r in roots.items()}
    if args.dry_run:
        print(json.dumps({"out_dir": str(out), "n_hours": n_hours, "roots": {k: str(v) for k, v in roots.items()}, "view_sha256_file_sha256": shas, "extra_views": [v.describe() for v in (extra_views or [])]}, indent=2))
        return 0
    build_table(out, roots, extra_views, max_workers=args.max_workers, run_id=args.run_id, sha_of_roots=shas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
