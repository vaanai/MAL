#!/usr/bin/env python3
"""damm_strand_list: COUNTS ONLY (no price). Lists the DBC pools in which a P14 D1/D2 cell (m4 m6 m10 ms30 ms50 ms70, rules frozen in tools/dbc_screen.py) has a position open at
curve completion (attempt `ended`), so the DAMM v2 fetch can take those pools first. Exploration hours [10-14T00, 10-17T00) only (tools.dbc_price refuses the holdout)."""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import sys

import numpy as np

from tools import dbc_screen as S

CELL_NAMES = ("m4", "m6", "m10", "ms30", "ms50", "ms70")


def main(argv: list[str] | None = None) -> int:
    out = (argv or sys.argv[1:])[0]
    D = S.load_exploration()
    res: dict = {"cells": {}, "pools": {}}
    names = D["pool_names"]
    for c in S.CELLS:
        if c.name not in CELL_NAMES:
            continue
        att, refused, st = S.cell_attempts(c, D)
        ended = [a for a in att if a["ended"]]
        res["cells"][c.name] = {"attempts": len(att), "ended": len(ended), "ended_by_day": {d: sum(1 for a in ended if a["date"] == d) for d in sorted({a["date"] for a in att})}}
        for a in ended:
            res["pools"].setdefault(names[a["pid"]], []).append({"cell": c.name, "bt": a["bt"], "date": a["date"]})
        print(c.name, res["cells"][c.name], flush=True)
    res["n_strand_pools"] = len(res["pools"])
    res["completed_pools_total"] = len(D["complete_slot"])
    json.dump(res, open(out, "w"), indent=1)
    print("strand pools", res["n_strand_pools"], "completed pools", res["completed_pools_total"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
