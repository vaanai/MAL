"""H5 shadow for the DEC-035 live-learning lane (v1 rule, Q* 40). Wrapper: tools/h5_shadow.py is NOT edited (its blob is pinned by arena / Q50 / C1-NF records).

The only difference from `python -m tools.h5_shadow`: the CAP-PICK oracle that run_live asks for pools with s0 >= 2026-10-16T01Z is replaced by
LaneOracle, which answers "not a pick" for every mint. EXP-022 / CAP-PICK was withdrawn 2026-10-15 and its exporter no longer runs; DEC-035 s7 makes the
lane's own live data unsealed. The substitution is recorded in the start record (seal.oracle = "lane_dec035_not_a_pick", seal.lane = "DEC-035").
Everything else is the stock detector: the H5 Look-2 seal still applies (declare --h5-look2-observed EXP-024-Am2 to write outcomes), same flags.

Required: `--lane DEC-035` (any other value is refused). Optional: `--lane-heal DEC-035` (needs --tip-dir; QP-RULING-1104-oc-feedfix s3.1): installs the tip-replay heal
Engine subclass of tools/h5_lane_heal.py; it adds `lane_gap_seq` to flagged gap rows and `lane_heal` to trigger records and changes nothing else. Off by default. Without this module the stock detector is unchanged (no code path here is imported by it).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Sequence

from tools import h5_shadow as hs

LANE_REF = "DEC-035"
LANE_HEAL_REF = "DEC-035"


def check_lane_out_dir(path: str | None) -> None:
    """The only accepted out dir is $HOME/data/h5-shadow-lane* (resolved, no '..'): never the book shadow's dir, which feeds the live executor."""
    if not path or ".." in Path(path).parts:
        raise ValueError("--out-dir is required and must not contain '..'")
    home = Path(os.environ.get("HOME") or Path.home())
    base = (home / "data").resolve()
    cand = Path(os.path.realpath(path))
    if cand.parent != base or not cand.name.startswith("h5-shadow-lane") or Path(path).parent.resolve() != base:
        raise ValueError(f"lane shadow out dir must be {home}/data/h5-shadow-lane* (got {path!r})")


class LaneOracle:
    """Three-state pick oracle that always says False (decided: not a pick)."""

    def __call__(self, mint: str) -> bool:
        return False


def lane_oracle_from_env(environ: dict[str, str] | None = None) -> tuple[LaneOracle, dict[str, Any]]:
    return LaneOracle(), {"oracle": "lane_dec035_not_a_pick", "lane": LANE_REF,
                          "why": "EXP-022 CAP-PICK withdrawn 2026-10-15; DEC-035 s7 lane data is unsealed; no CAP_PICK_* environment is read"}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    ref = None
    heal_ref = None
    rest: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--lane" and i + 1 < len(args):
            ref = args[i + 1]
            i += 2
        elif args[i].startswith("--lane="):
            ref = args[i].split("=", 1)[1]
            i += 1
        elif args[i] == "--lane-heal" and i + 1 < len(args):
            heal_ref = args[i + 1]
            i += 2
        elif args[i].startswith("--lane-heal="):
            heal_ref = args[i].split("=", 1)[1]
            i += 1
        else:
            rest.append(args[i])
            i += 1
    if ref != LANE_REF:
        print(f"h5_lane_shadow: needs --lane {LANE_REF} (got {ref!r})", file=sys.stderr)
        return 2
    if any(a == "--fresh-forward" or a.startswith("--fresh-forward=") for a in rest):
        print("h5_lane_shadow: --fresh-forward is a different mode (arena); refused", file=sys.stderr)
        return 2
    out = None
    for j, a in enumerate(rest):
        if a == "--out-dir" and j + 1 < len(rest):
            out = rest[j + 1]
        elif a.startswith("--out-dir="):
            out = a.split("=", 1)[1]
    try:
        check_lane_out_dir(out)
    except ValueError as exc:
        print(f"h5_lane_shadow: {exc}", file=sys.stderr)
        return 2
    if heal_ref is not None:  # A-R tip-replay heal (QP-RULING-1104-oc-feedfix s3.1): off unless this flag AND --tip-dir are given
        if heal_ref != LANE_HEAL_REF:
            print(f"h5_lane_shadow: --lane-heal needs {LANE_HEAL_REF} (got {heal_ref!r})", file=sys.stderr)
            return 2
        if not any(a == "--tip-dir" or a.startswith("--tip-dir=") for a in rest):
            print("h5_lane_shadow: --lane-heal needs --tip-dir", file=sys.stderr)
            return 2
        from tools import h5_lane_heal
        h5_lane_heal.install()  # replaces the module global hs.Engine (run_live looks it up at call time), as the oracle below
    hs.cap_pick_oracle_from_env = lane_oracle_from_env  # type: ignore[assignment]  # run_live looks this global up at call time
    return hs.main(rest)


if __name__ == "__main__":
    sys.exit(main())
