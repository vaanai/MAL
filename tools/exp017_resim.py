#!/usr/bin/env python3
"""EXP-017 re-simulation: the frozen-EXP-012-selected mints at stakes 0.10, 0.25 and 0.50 SOL (k = 6, exit lag 2, V pin 0909, haircut and fees applied
later by the screen, as for the 0.05 SOL cache). Feeds H4 (0.10 SOL) and C0 (0.25 / 0.5 SOL).

Design (the simpler of the two options): a SEALED sized cache. This step produces outcomes (net0 per cell) but is not a try and is not a read:
it writes `<out-dir>/sized_cache/v_P*.rows.jsonl` (same row schema as the EXP-015 cache), prints only counts and hashes, and nobody opens the
files. The screen consumes the directory only after a plan amendment pins its manifest sha256 (`SIZED_MANIFEST_SHA256 = <sha>` line in
EXP/EXP-017-batch1-cheap-screens-plan.md); the screen re-hashes the files first. The selection comes from the pinned EXP-015 cache's features
and the frozen model (outcome-blind: the cache is read with the net fields dropped at parse time).

  --precount   count-only: selected mints per source, combos, passes. Reads no net. Needs no tape.
  (full)       guards, then one V-mode tape pass per source (<= 4 workers), only the selected mints simulated.

Run (mal-research-0; one heavy job at a time; as a MiScusi job requesting <= 48 GB and 4 CPUs; under nice):
  nice -n 19 /data/mal/venv/bin/python -m tools.exp017_resim \\
    --p1-fast-dir /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 \\
    --p1-oracle-insample-dir /data/mal/clean-view/oracle-insample-2026-09-22_25 \\
    --p1-oracle-live-dir /data/mal/clean-view/oracle-live-2026-09-25_27 \\
    --p2-view-dir /data/mal/clean-view/explore-0814/w1 ... --p2-view-dir /data/mal/clean-view/explore-0814/w7 \\
    --p3-root /data/mal/blocks-clean/fresh-0903 \\
    --p4-view-dir /data/mal/clean-view/exp011-0909/b --p4-view-dir /data/mal/clean-view/exp011-0909/c \\
    --out-dir /data/mal/exp017-resim --max-workers 4
(the same view arguments as the EXP-015 screen; add --precount first.)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import tools.exp015_screen as e15
import tools.exp017_screen as x17

SIZES_SOL = (0.10, 0.25, 0.50)
COMBOS = x17.SIZED_COMBOS  # (6, 0.05, 2) first: the decision-equivalence proof against the EXP-015 cache
SIZED_DIR = "sized_cache"
SELECTED_FILE = "selected_mints.json"
MANIFEST_FILE = "SIZED.manifest.sha256"
WORKERS_CAP = 4  # the memory ceiling for research-0 (<= 48 GB) is kept by capping the tape workers


def selected_mints(scratch: str | Path, artifact_dir: Path | None = None) -> tuple[list[str], dict[str, int]]:
    """Outcome-blind: mints with frozen score >= THR90, from the pinned cache's features (net fields dropped at parse time)."""
    x17.check_manifests(scratch)
    x17.check_cache_heads(scratch)
    uni, _ = x17.load_universe(scratch, blind=True)
    scores = x17.frozen_scores(uni, artifact_dir)
    sel = [u["mint"] for u, s in zip(uni, scores) if s >= x17.THR90]
    by_src: dict[str, int] = {s: 0 for s in x17.SOURCES}
    for u, s in zip(uni, scores):
        by_src[u["source"]] += int(s >= x17.THR90)
    return sorted(sel), by_src


def _parser() -> Any:
    ap = e15._parser()
    ap.add_argument("--scratch", default=x17.DEFAULT_SCRATCH)
    ap.add_argument("--precount", action="store_true")
    return ap


def run_pass(args: Any, g: dict[str, Any], sel_path: Path, out_dir: Path, head: str, combos: Sequence[tuple[int, float, int]] = COMBOS, extra_meta: Mapping[str, Any] | None = None) -> dict[str, str]:
    """One V-mode pass per source with the env overrides read by exp015_screen.e15_v_patch. Returns {source: rows sha256}.
    `combos` defaults to the EXP-017 sizes (EXP-020 passes its own grid); the default behaviour is unchanged."""
    combos_ = tuple(combos)
    os.environ[e15.ENV_COMBOS] = json.dumps([[k, s, lag] for k, s, lag in combos_])
    os.environ[e15.ENV_SELECTED] = str(sel_path)
    work, cache = out_dir / "work", out_dir / SIZED_DIR
    mw = min(args.max_workers, WORKERS_CAP)
    shas: dict[str, str] = {}
    p1_roots = {"P1A": g["g1"]["roots"]["fast"], "P1C": g["g1"]["roots"]["insample"], "P1B": g["g1"]["roots"]["live"]}
    sel_sha = e15._file_sha256(sel_path)

    def done(tag: str, rows: list[dict[str, Any]], vmap_key: str) -> None:
        meta = {"tag": tag, "head": head, "combos": [list(c) for c in combos_], "selected_sha256": sel_sha, "vmap_sha256": g["vmap_sha256"][vmap_key], **(extra_meta or {})}
        shas[tag] = e15.write_cache(cache, "v", tag, rows, meta, {})
        print(f"EXP-017 resim {tag}: rows={len(rows)} sha256={shas[tag][:16]}", file=sys.stderr, flush=True)

    for tag in ("P1A", "P1C", "P1B"):
        done(tag, e15.run_p1_pass(tag, p1_roots[tag], "v", work, args.vmap_p1, mw), "P1")
    r2 = g["g2"]
    done("P2", e15.run_holdout_pass("P2", e15.bc.MultiViewHours(dict(r2["roots"])), r2["pool"], "v", work, args.vmap_p2, mw), "P2")
    done("P3", e15.run_holdout_pass("P3", e15.make_p3_hours(g["g3"]["walkers"]), e15.bc.hours_range(*e15.BLOCKS["P3"]), "v", work, args.vmap_p3, mw), "P3")
    r4 = g["g4"]
    done("P4", e15.run_holdout_pass("P4", e15.bc.MultiViewHours(dict(r4["roots"])), r4["pool"], "v", work, args.vmap_p4, mw), "P4")
    return shas


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out_dir: Path = args.out_dir
    try:
        sel, by_src = selected_mints(args.scratch, args.artifact_dir if args.artifact_dir != e15.DEFAULT_ARTIFACT_DIR else None)
        if args.precount:
            print(json.dumps({"mode": "precount", "outcome_blind": True, "n_selected": len(sel), "by_source": by_src, "combos": [list(c) for c in COMBOS],
                              "n_cells_to_simulate": len(sel) * len(COMBOS), "max_workers": min(args.max_workers, WORKERS_CAP)}, indent=2))
            return 0
        g = e15.run_guards(args)
        if g["g4"] is None:
            raise e15.Refused("--p4-view-dir is required: the sized cache covers all six sources")
        if (out_dir / SIZED_DIR).exists() or (out_dir / MANIFEST_FILE).exists():
            raise e15.Refused(f"{out_dir} already holds a sized cache: a second re-sim is refused")
        if args.guards_only:
            print(json.dumps({"guards": "ok", "n_selected": len(sel)}))
            return 0
        head = e15.git_state()["head"]
        e15.check_run_lock(out_dir)
        e15.take_lock(out_dir, head, "resim")
    except (e15.Refused, x17.Refused) as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    sel_path = out_dir / SELECTED_FILE
    sel_path.write_text(json.dumps(sel) + "\n", encoding="utf-8")
    status = "aborted"
    try:
        run_pass(args, g, sel_path, out_dir, head)
        for f in sorted((out_dir / SIZED_DIR).glob("v_P*")):
            os.chmod(f, 0o400)  # the seal is read-only files plus a convention: it relies on people not looking
        lines, sha = x17.manifest(out_dir / SIZED_DIR, x17.SIZED_PATTERNS)
        (out_dir / MANIFEST_FILE).write_text(sha + "\n", encoding="utf-8")
        print(json.dumps({"sized_manifest_sha256": sha, "n_files": len(lines), "note": "pin it with a plan amendment line; nets are never printed"}))
        status = "completed"
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"aborted: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        e15.write_record(out_dir, status, False, {"resim": status})


if __name__ == "__main__":
    raise SystemExit(main())
