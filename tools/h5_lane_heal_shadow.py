"""Entry point of the lane shadow WITH the A-R tip-replay heal (QP-RULING-1104-oc-feedfix s3.1, amended by the QP verdict on PR #1232).

`python -m tools.h5_lane_heal_shadow --lane DEC-035 --lane-heal DEC-035 --tip-dir ... <the usual shadow flags>`

tools/h5_lane_shadow.py is NOT edited (its blob is pinned by the h5-lane-oc, h5-lane-3 and h5-lane-syn records). This module strips `--lane-heal DEC-035`, refuses (exit 2)
without it, without `--tip-dir` or with any other reference, installs the Engine subclass of tools/h5_lane_heal.py (the module global `hs.Engine`, which run_live looks up
at call time) and returns tools.h5_lane_shadow.main(rest). Without this module the lane shadow is unchanged.
"""
from __future__ import annotations

import sys
from typing import Sequence

from tools import h5_lane_shadow as ls

LANE_HEAL_REF = "DEC-035"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    heal_ref = None
    rest: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--lane-heal" and i + 1 < len(args):
            heal_ref = args[i + 1]
            i += 2
        elif args[i].startswith("--lane-heal="):
            heal_ref = args[i].split("=", 1)[1]
            i += 1
        else:
            rest.append(args[i])
            i += 1
    if heal_ref != LANE_HEAL_REF:
        print(f"h5_lane_heal_shadow: needs --lane-heal {LANE_HEAL_REF} (got {heal_ref!r})", file=sys.stderr)
        return 2
    if not any(a == "--tip-dir" or a.startswith("--tip-dir=") for a in rest):
        print("h5_lane_heal_shadow: --lane-heal needs --tip-dir", file=sys.stderr)
        return 2
    from tools import h5_lane_heal
    h5_lane_heal.install()
    return ls.main(rest)


if __name__ == "__main__":
    sys.exit(main())
