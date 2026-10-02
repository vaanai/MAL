"""EXP-013: pin the views of the one real screen (plan Amendment 5, section 7).

    python -m tools.exp013_pin_views --fast-dir A --oracle-insample-dir C --oracle-live-dir B \
        --candidate-view DIR [--candidate-view DIR ...] --out M.json

Refuses to run before tools.exp013_grad_screen.VIEW_CUTOFF. A candidate view is included only if its
VIEW.sha256 exists and was last written at or before the cutoff; others are listed under `excluded`
(not an error). Writes the view manifest `assert_view_manifest` expects (pools A/B/C and extra_views,
each with root, hours, view_sha256_file_sha256, view_sha256_mtime_utc) and `M.args.txt`: the
`--extra-fast-view` and DIR words for the table builder, one per line, in order. Hours and shas come
from the table builder's own functions. Reads no tape.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import tools.exp011_freeze as fz
import tools.exp013_grad_table as gtab
from tools.exp013_grad_screen import VIEW_CUTOFF, _utc_now
from tools.exp013_pool import load_extra_views

_FMT = "%Y-%m-%dT%H:%M:%SZ"
POOL_ARG = {"A": "fast_dir", "C": "oracle_insample_dir", "B": "oracle_live_dir"}


def _mtime_utc(path: Path, mtime_fn: Callable[[Path], float] | None) -> datetime:
    mt = mtime_fn(path) if mtime_fn else path.stat().st_mtime
    return datetime.fromtimestamp(mt, timezone.utc)


def pin_views(
    roots: dict[str, Path],
    candidates: Sequence[Path],
    *,
    now: datetime | None = None,
    cutoff: datetime = VIEW_CUTOFF,
    mtime_fn: Callable[[Path], float] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """roots: {"A": dir, "B": dir, "C": dir}. Returns (view manifest, builder args)."""
    now = now or _utc_now()
    if now < cutoff:
        raise SystemExit(f"refusing: {now.strftime(_FMT)} is before the view cutoff {cutoff.strftime(_FMT)}")
    included: list[Path] = []
    excluded: list[dict[str, str]] = []
    for c in candidates:
        sha = Path(c) / "VIEW.sha256"
        if not sha.is_file():
            excluded.append({"root": str(c), "reason": "VIEW.sha256 missing"})
            continue
        mt = _mtime_utc(sha, mtime_fn)
        if mt > cutoff:
            excluded.append({"root": str(c), "reason": f"VIEW.sha256 mtime {mt.strftime(_FMT)} is after {cutoff.strftime(_FMT)}"})
            continue
        included.append(Path(c))
    extra = load_extra_views(included, fz.DAYS_ALL) if included else []
    runs = gtab.pool_runs(extra)
    pools: dict[str, Any] = {}
    for tag in ("A", "B", "C"):
        root = Path(os.path.realpath(str(roots[tag])))
        sha = root / "VIEW.sha256"
        pools[tag] = {
            "root": str(root),
            "hours": [runs[tag][0][0], runs[tag][-1][-1]],
            "view_sha256_file_sha256": gtab._sha256_of_file(sha),
            "view_sha256_mtime_utc": _mtime_utc(sha, mtime_fn).strftime(_FMT),
        }
    extra_views = []
    for v in extra:
        d = v.describe()
        extra_views.append({
            "root": d["root"], "hours": d["hours"], "view_sha256_file_sha256": d["view_sha256_file_sha256"],
            "view_sha256_mtime_utc": _mtime_utc(v.root / "VIEW.sha256", mtime_fn).strftime(_FMT),
        })
    manifest = {"pinned_utc": now.strftime(_FMT), "cutoff_utc": cutoff.strftime(_FMT), "pools": pools, "extra_views": extra_views, "excluded": excluded}
    return manifest, [a for v in extra for a in ("--extra-fast-view", str(v.root))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast-dir", type=Path, required=True)
    ap.add_argument("--oracle-insample-dir", type=Path, required=True)
    ap.add_argument("--oracle-live-dir", type=Path, required=True)
    ap.add_argument("--candidate-view", type=Path, action="append", default=[])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    args_path = a.out.with_suffix(".args.txt")
    if a.out.exists() or args_path.exists():
        raise SystemExit(f"refusing: {a.out} (or its .args.txt) already exists")
    roots = {tag: getattr(a, name) for tag, name in POOL_ARG.items()}
    manifest, args = pin_views(roots, a.candidate_view)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    args_path.write_text("".join(f"{x}\n" for x in args), encoding="utf-8")
    print(f"pinned {len(manifest['extra_views'])} extra view(s), excluded {len(manifest['excluded'])}: {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
