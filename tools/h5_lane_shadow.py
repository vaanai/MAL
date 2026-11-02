"""H5 shadow for the DEC-035 live-learning lane (v1 rule, Q* 40). Wrapper: tools/h5_shadow.py is NOT edited (its blob is pinned by arena / Q50 / C1-NF records).

The only difference from `python -m tools.h5_shadow`: the CAP-PICK oracle that run_live asks for pools with s0 >= 2026-10-16T01Z is replaced by
LaneOracle, which answers "not a pick" for every mint. EXP-022 / CAP-PICK was withdrawn 2026-10-15 and its exporter no longer runs; DEC-035 s7 makes the
lane's own live data unsealed. The substitution is recorded in the start record (seal.oracle = "lane_dec035_not_a_pick", seal.lane = "DEC-035").
Everything else is the stock detector: the H5 Look-2 seal still applies (declare --h5-look2-observed EXP-024-Am2 to write outcomes), same flags.

Required: `--lane DEC-035` (any other value is refused). Without this module the stock detector is unchanged (no code path here is imported by it).
"""
from __future__ import annotations

import sys
from typing import Any, Sequence

from tools import h5_shadow as hs

LANE_REF = "DEC-035"


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
    rest: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--lane" and i + 1 < len(args):
            ref = args[i + 1]
            i += 2
        elif args[i].startswith("--lane="):
            ref = args[i].split("=", 1)[1]
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
    hs.cap_pick_oracle_from_env = lane_oracle_from_env  # type: ignore[assignment]  # run_live looks this global up at call time
    return hs.main(rest)


if __name__ == "__main__":
    sys.exit(main())
