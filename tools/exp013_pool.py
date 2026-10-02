"""Expanded exploration pool: extra fast-format (getBlock walker) clean views.

Exploration tooling for the EXP-013 candidate refit. It lets the EXP-012 freeze
recipe (tools/exp011_freeze.py, tools/exp011_build_table.py) read additional
clean views next to the three built-in pools A / C / B. Nothing here changes
the recipe: the rows come from the same `run_worker_features` / `score_one`
(same 18 features, same exits, same execution), only the hour source differs.

An extra view is a directory with the fast layout
`{trades,creates,migrations}/<sub>-<YYYY-MM-DDTHH>.jsonl.zst` and a
`VIEW.sha256` (sha256sum format). Its hour list, and so its day list, is
derived from the file names in VIEW.sha256, never from a flag.

Guards (all refuse with SystemExit, before any data file is opened except
VIEW.sha256 itself):

  1. Forbidden locations, after `os.path.realpath`: the sealed EXP-012 / backup
     confirmation blocks and their clean copies and views, the EXP-011 walker
     directories, and the forward walk. Each file named in VIEW.sha256 is also
     realpath-checked, so a symlink out of an allowed view into a forbidden
     one is refused.
  2. VIEW.sha256 must exist and every listed file must hash to its listed
     value (tools.exp011_freeze.verify_view_sha256).
  3. Hour fence: every hour must lie in the exploration-expansion block
     [2026-08-14T12, 2026-08-28T12) (docs/HOLDOUT_LEDGER.md), which also
     excludes every owned or reserved confirmation block by time.
  4. Every hour needs both a trades and a creates file, and a view's hours
     must be contiguous (a hole would silently thin the tape).
  5. Overlap: a day that is already a built-in pool day (DAYS_ALL) is refused;
     two extra views may not share an HOUR (duplicate rows). Two extra views
     may share a DAY when their hours differ: walker blocks start at 12:00, so
     adjacent blocks split a UTC day, and the day is one LODO fold.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any, Sequence

# Real host locations that may never be an extra root, compared after realpath.
FORBIDDEN_REALPATH_PREFIXES: tuple[str, ...] = (
    "/data/mal/blocks",  # includes fresh-0903, fresh-0828, explore-0814 raw walkers, forward-1002, truth-*
    "/data/mal/blocks-clean",
    "/data/mal/clean-view/fresh-0828",
    "/data/mal/clean-view/fresh-0903",
    "/data/mal/clean-view/forward-1002",
    "/data/mal/exp012",
    os.path.realpath(str(Path(__file__).resolve().parents[1] / "ARTIFACTS" / "exp012" / "read")),  # EXP-012's read/ dir (realpath'd)
    "/var/lib/mal/backfill-fast-b",  # EXP-011 block (spent) walkers
    "/var/lib/mal/backfill-fast-c",
)

# Exploration-expansion block, docs/HOLDOUT_LEDGER.md (inclusive start, exclusive end).
EXPANSION_START = "2026-08-14T12"
EXPANSION_END = "2026-08-28T12"

POOL_TAG = "X"

_LINE = re.compile(r"^([0-9a-fA-F]{64})[ \t]+\*?(.+)$")
_NAME = re.compile(r"^(creates|trades|migrations)/(creates|trades|migrations)-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(?:\.zst|\.gz)?$")


def _is_forbidden(real: str) -> str | None:
    for bad in FORBIDDEN_REALPATH_PREFIXES:
        if real == bad or real.startswith(bad.rstrip("/") + "/"):
            return bad
    return None


def assert_extra_root_allowed(root: Path | str) -> Path:
    """realpath(root) must not be at or under a forbidden location. Returns the realpath."""
    real = os.path.realpath(str(root))
    bad = _is_forbidden(real)
    if bad is not None:
        raise SystemExit(f"--extra-fast-view {str(root)!r} resolves to {real!r}, inside a reserved/forbidden location ({bad}); the refit never reads it")
    return Path(real)


class ExtraView:
    """One verified extra view: root (realpath), hours, VIEW.sha256 sha256."""

    def __init__(self, root: Path, hours: list[str], view_sha256_file_sha256: str, n_files: int, files: dict[str, dict[str, str]] | None = None) -> None:
        self.root = root
        # hour -> {"trades": abs path, "creates": abs path}: exactly the paths VIEW.sha256 names.
        self.files = files if files is not None else {}
        self.hours = hours
        self.days = sorted({h[:10] for h in hours})
        self.view_sha256_file_sha256 = view_sha256_file_sha256
        self.n_files = n_files

    def describe(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "hours": [self.hours[0], self.hours[-1]],
            "n_hours": len(self.hours),
            "days": self.days,
            "n_files": self.n_files,
            "view_sha256_file_sha256": self.view_sha256_file_sha256,
        }


def _hour_range(a: str, b: str) -> list[str]:
    fmt = "%Y-%m-%dT%H"
    cur = datetime.strptime(a, fmt).replace(tzinfo=timezone.utc)
    end = datetime.strptime(b, fmt).replace(tzinfo=timezone.utc)
    out = []
    while cur <= end:
        out.append(cur.strftime(fmt))
        cur += timedelta(hours=1)
    return out


def parse_view_hours(root: Path) -> tuple[list[str], list[str], dict[str, dict[str, str]]]:
    """(sorted hours, listed relative paths, {hour: {"trades","creates"} -> abs path}) from `root/VIEW.sha256`. Refuses a
    listed file whose realpath is forbidden, an hour without both trades and
    creates, a non-contiguous hour list, or hours outside the expansion block."""
    view = root / "VIEW.sha256"
    if not view.is_file():
        raise SystemExit(f"--extra-fast-view: {view} not found (VIEW.sha256 is required)")
    subs: dict[str, set[str]] = {"trades": set(), "creates": set()}
    rels: list[str] = []
    hour_files: dict[str, dict[str, str]] = {}
    for lineno, line in enumerate(view.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        m = _LINE.match(line)
        if m is None:
            raise SystemExit(f"{view}:{lineno}: unparsable line")
        rel = os.path.normpath(m.group(2))
        nm = _NAME.match(rel)
        if nm is None or nm.group(1) != nm.group(2):
            raise SystemExit(f"{view}:{lineno}: not a fast-format hour file: {rel!r}")
        real = os.path.realpath(str(root / rel))
        bad = _is_forbidden(real)
        if bad is not None:
            raise SystemExit(f"{view}:{lineno}: {rel!r} resolves to {real!r}, inside a forbidden location ({bad})")
        rels.append(rel)
        if nm.group(1) in subs:
            subs[nm.group(1)].add(nm.group(3))
            hour_files.setdefault(nm.group(3), {})[nm.group(1)] = str(root / rel)
    hours = sorted(subs["trades"])
    if not hours:
        raise SystemExit(f"{view} lists no trades files")
    missing = sorted(set(hours) ^ subs["creates"])
    if missing:
        raise SystemExit(f"{view}: hours without both trades and creates files: {missing[:10]}")
    if hours != _hour_range(hours[0], hours[-1]):
        raise SystemExit(f"{view}: hour list is not contiguous ({hours[0]}..{hours[-1]}, {len(hours)} hours)")
    if hours[0] < EXPANSION_START or hours[-1] >= EXPANSION_END:
        raise SystemExit(f"{view}: hours {hours[0]}..{hours[-1]} fall outside the exploration-expansion block [{EXPANSION_START}, {EXPANSION_END})")
    return hours, rels, {h: hour_files[h] for h in hours}


_DATA_NAME = re.compile(r"^(creates|trades|migrations)-(\d{4}-\d{2}-\d{2}T\d{2})\.jsonl(?:\.zst|\.gz)?$")


def assert_no_unlisted_data_files(root: Path, rels: Sequence[str]) -> None:
    """Refuse if trades/ creates/ migrations/ under `root` holds a data file VIEW.sha256 does not
    list (e.g. a listed .zst beside an unlisted .jsonl or .gz): the loader must have only one
    possible reading of every hour."""
    listed = set(rels)
    extra: list[str] = []
    for sub in ("trades", "creates", "migrations"):
        d = root / sub
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            m = _DATA_NAME.match(entry.name)
            if m is not None and m.group(1) == sub and f"{sub}/{entry.name}" not in listed:
                extra.append(f"{sub}/{entry.name}")
    if extra:
        shown = ", ".join(extra[:10]) + (f" ... and {len(extra) - 10} more" if len(extra) > 10 else "")
        raise SystemExit(f"{root}: data file(s) not listed in VIEW.sha256: {shown}")


def union_gaps(views: Sequence["ExtraView"]) -> list[dict[str, Any]]:
    """Missing hour ranges between the first and last hour of the union of all extra-view hours."""
    hours = sorted({h for v in views for h in v.hours})
    if not hours:
        return []
    have = set(hours)
    gaps: list[dict[str, Any]] = []
    cur: list[str] = []
    for h in _hour_range(hours[0], hours[-1]):
        if h in have:
            if cur:
                gaps.append({"from": cur[0], "to": cur[-1], "n_hours": len(cur)})
                cur = []
        else:
            cur.append(h)
    return gaps


def load_extra_views(paths: Sequence[Path | str], builtin_days: Sequence[str], allow_gap: bool = False) -> list[ExtraView]:
    """Validate and verify every extra view (guards 1-9 in the module docstring)."""
    from tools.exp011_freeze import verify_view_sha256

    views: list[ExtraView] = []
    seen_roots: set[str] = set()
    for p in paths:
        root = assert_extra_root_allowed(p)
        if str(root) in seen_roots:
            raise SystemExit(f"--extra-fast-view {str(root)!r} given twice")
        seen_roots.add(str(root))
        hours, rels, hour_files = parse_view_hours(root)
        assert_no_unlisted_data_files(root, rels)
        n = verify_view_sha256(root)
        if n != len(rels):
            raise SystemExit(f"{root}: verified {n} files but VIEW.sha256 names {len(rels)}")
        sha = hashlib.sha256((root / "VIEW.sha256").read_bytes()).hexdigest()
        views.append(ExtraView(root, hours, sha, n, hour_files))
        print(f"EXP-013 extra view OK: {root} hours {hours[0]}..{hours[-1]} ({len(hours)}), {n} files verified", file=sys.stderr, flush=True)
    builtin = set(builtin_days)
    hour_owner: dict[str, str] = {}
    for v in views:
        clash = sorted(set(v.days) & builtin)
        if clash:
            raise SystemExit(f"{v.root}: day(s) {clash} already belong to a built-in pool; refusing the overlap")
        for h in v.hours:
            if h in hour_owner:
                raise SystemExit(f"{v.root}: hour {h} is also in {hour_owner[h]}; refusing the overlap")
            hour_owner[h] = str(v.root)
    gaps = union_gaps(views)
    if gaps:
        text = "; ".join(f"{g['from']}..{g['to']} ({g['n_hours']} h)" for g in gaps)
        if not allow_gap:
            raise SystemExit(f"the extra views leave missing hour range(s): {text}. Add the view that covers them, or pass --allow-gap to accept and record the gap(s)")
        print(f"EXP-013 extra views: --allow-gap accepted missing hour range(s): {text}", file=sys.stderr, flush=True)
    return views


# --- Loader -----------------------------------------------------------------


def _hour_info_x(key: str, *, hour_roots: dict[str, dict[str, str]]) -> dict[str, Any]:
    """`hour_roots`: hour -> {"trades": path, "creates": path}, exactly the files VIEW.sha256 names."""
    assert key in hour_roots, f"hour {key} is outside the extra pool fence"
    trade, create = Path(hour_roots[key]["trades"]), Path(hour_roots[key]["creates"])
    for f in (trade, create):
        if not f.is_file():
            raise SystemExit(f"listed file missing for whitelisted hour {key}: {f}")
    start_s = int(datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc).timestamp())
    return {"hour": key, "day": key[:10], "end": start_s + 3600, "trade": trade, "create": create}


def build_creator_history_x(hours: Sequence[str], hour_roots: dict[str, dict[str, str]]) -> dict[str, list[int]]:
    """Same as tools.exploration_entry_model.build_creator_history, over the extra hours."""
    from tools.latency_curve import _iter_trades

    hist: dict[str, list[int]] = {}
    for key in hours:
        path = _hour_info_x(key, hour_roots=hour_roots).get("create")
        if path is None:
            continue
        for row in _iter_trades(path):
            if row.get("type") != "create":
                continue
            creator, block = row.get("creator"), row.get("block_time")
            if isinstance(creator, str) and isinstance(block, int):
                hist.setdefault(creator, []).append(block * 1000)
    for times in hist.values():
        times.sort()
    return hist


def plan_extra(hours: Sequence[str], max_workers: int, buffer_hours: int, max_home_hours: int | None) -> list[tuple[int, list[str], list[str]]]:
    """chunk_plan over each contiguous run of hours, ids renumbered."""
    from tools.exploration_exits import chunk_plan

    runs: list[list[str]] = []
    for h in sorted(hours):
        if runs and _hour_range(runs[-1][-1], h)[1:] == [h]:
            runs[-1].append(h)
        else:
            runs.append([h])
    plan: list[tuple[int, list[str], list[str]]] = []
    for run in runs:
        for _i, home, buf in chunk_plan(run, max_workers, buffer_hours, max_home_hours):
            plan.append((len(plan), home, buf))
    return plan


def run_worker_x(worker_id: int, home: list[str], buf: list[str], creator_hist: dict[str, list[int]], rows_out_path: Path | None, hour_roots: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    from tools.exploration_entry_model import run_worker_features

    return run_worker_features(worker_id, home, buf, creator_hist, hour_info_fn=partial(_hour_info_x, hour_roots=hour_roots), rows_out_path=rows_out_path)


def run_all_features_x(
    views: Sequence[ExtraView], max_workers: int = 2, buffer_hours: int = 24, max_home_hours: int | None = 12, out_dir: Path | None = None
) -> list[dict[str, Any]]:
    from tools.exploration_entry_model import _rows_out_path, iter_rows_jsonl

    hour_roots = {h: v.files[h] for v in views for h in v.hours}
    hours = sorted(hour_roots)
    print(f"EXP-013 extra pool: {len(hours)} hours from {len(views)} view(s); building creator history...", file=sys.stderr, flush=True)
    creator_hist = build_creator_history_x(hours, hour_roots)
    plan = plan_extra(hours, max_workers, buffer_hours, max_home_hours)
    print(f"worker_plan_x={[(i, h[0], h[-1], len(b)) for i, h, b in plan]}", file=sys.stderr, flush=True)
    paths = [_rows_out_path(out_dir, POOL_TAG, i) for i, _h, _b in plan]
    rows: list[dict[str, Any]] = []
    if max_workers <= 1 or len(plan) <= 1:
        for (i, h, b), path in zip(plan, paths):
            rows.extend(run_worker_x(i, h, b, creator_hist, path, hour_roots))
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=min(max_workers, len(plan))) as pool:
            results = pool.starmap(run_worker_x, [(i, h, b, creator_hist, p, hour_roots) for (i, h, b), p in zip(plan, paths)])
        for part in results:
            rows.extend(part)
    if out_dir is not None:
        for path in paths:
            rows.extend(iter_rows_jsonl(path))
    return rows


def extra_manifest(views: Sequence[ExtraView], n_rows: int) -> dict[str, Any]:
    days = sorted({d for v in views for d in v.days})
    return {"views": [v.describe() for v in views], "days": days, "n_rows": n_rows, "pool_tag": POOL_TAG, "gaps": union_gaps(views)}


EDGE_ZONE_HOURS = 24  # the recipe's buffer_hours and its creator-history window

EDGE_CENSORING_STATEMENT = (
    "Pool X rows near the ends of each contiguous run of extra-view hours are censored. "
    "Left edge: there is no tape before the run's first hour (for the expansion block, none before "
    "2026-08-14T12), so creator_prior_mints_24h and the creator history are undercounted, and mints created "
    "before the run start are absent, for the first 24 hours. Right edge: the tape and the 24 h buffer stop "
    "at the run's last hour (for w1, 2026-08-28T11), so a mint created in the last 24 h that migrates after it, "
    "and a migrated mint whose exit walk (up to 60 min) runs past it, produce no row. Those dropped rows cannot "
    "be counted by the loader; what is counted below is the surviving rows on the edge days (row day = "
    "UTC day of the migration), which are the rows a censoring bias can touch. The right-edge survivors are "
    "biased toward fast migrations and early exits."
)


def edge_censoring(views: Sequence[ExtraView], rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Edge days of every contiguous run of extra-view hours, with the pool X row count on each.
    A zone is the first (left) or last (right) EDGE_ZONE_HOURS hours of a run."""
    hours = sorted({h for v in views for h in v.hours})
    runs: list[list[str]] = []
    for h in hours:
        if runs and _hour_range(runs[-1][-1], h)[1:] == [h]:
            runs[-1].append(h)
        else:
            runs.append([h])
    per_day: dict[str, int] = {}
    for r in rows:
        if r.get("pool", POOL_TAG) == POOL_TAG:
            per_day[r["day"]] = per_day.get(r["day"], 0) + 1
    edge_days: list[dict[str, Any]] = []
    for run in runs:
        for role, zone in (("left_censored", run[:EDGE_ZONE_HOURS]), ("right_truncated", run[-EDGE_ZONE_HOURS:])):
            by_day: dict[str, int] = {}
            for h in zone:
                by_day[h[:10]] = by_day.get(h[:10], 0) + 1
            for day, n_h in sorted(by_day.items()):
                edge_days.append({"run": [run[0], run[-1]], "role": role, "day": day, "zone_hours_on_day": n_h, "n_rows_on_day": per_day.get(day, 0)})
    return {
        "statement": EDGE_CENSORING_STATEMENT,
        "zone_hours": EDGE_ZONE_HOURS,
        "runs": [[r[0], r[-1]] for r in runs],
        "edge_days": edge_days,
        "n_rows_left_censored_days": sum(e["n_rows_on_day"] for e in edge_days if e["role"] == "left_censored"),
        "n_rows_right_truncated_days": sum(e["n_rows_on_day"] for e in edge_days if e["role"] == "right_truncated"),
        "note": "rows are counted per (zone, day) entry, so a day that is an edge day of two zones is counted in both totals",
    }


def add_extra_view_arg(ap: Any) -> None:
    from tools.exp011_freeze import add_extra_view_arg as _add

    _add(ap)


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str) + "\n"
