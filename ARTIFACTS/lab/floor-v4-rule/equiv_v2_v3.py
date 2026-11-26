"""md5 decision-equivalence legs (i) v2 and (ii) v3 for the l1-floor-v4 rule PR. Synthetic fixtures only (the repo's own test fixtures); no tape, ledger, declog or out/ dir is read.

  /data/mal/venv/bin/python ARTIFACTS/lab/floor-v4-rule/equiv_v2_v3.py <repo_root>   # run once with the base worktree, once with the head worktree; compare the JSON

Prints canonical JSON: for each scenario, the md5 of the entry/exit rows (H.decision_digest) and of the whole decision log file (DecisionLog.md5, start row and chain included),
and the row counts. Names, md5s and counts only.
"""

import collections
import json
import os
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

os.environ["MAL_ARENA_TEST"] = "1"  # the tests' autouse fixture, reproduced
import tools.test_arena_rule_l1 as T  # noqa: E402
import tools.arena_paper as AP  # noqa: E402
from tools import floor_lane_host as H  # noqa: E402

AP.shutil.disk_usage = lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500)
T.SHA = H.FROZEN_SHA
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.test_floor_arena_executor import _host_log_scenarios  # noqa: E402
from tools.test_floor_lane_host_v3 import cooldown_rows, mid_vault_rows, run_rule  # noqa: E402

assert H.__file__.startswith(str(root)), (H.__file__, root)
SCEN = {"three_pool": _host_log_scenarios, "cooldown": cooldown_rows}
ARMS = [("v2", "l1-floor-v2", None), ("v3_q500", "l1-floor-v3", 500_000_000), ("v3_q750", "l1-floor-v3", 750_000_000), ("v3_q1000", "l1-floor-v3", 1_000_000_000)]
out = {}
for arm, rid, q in ARMS:
    for sn, fn in list(SCEN.items()) + [("mid_vault", lambda tmp: mid_vault_rows())]:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            run_rule(tmp, fn(tmp), rid, q, hours=60)
            log = DecisionLog(str(tmp / "rules"))
            rows = log.rows(rid)
            out[f"{arm}/{sn}"] = {"decision_md5": H.decision_digest(tmp / "rules", bot_id=rid), "log_md5": log.md5(),
                                  "entries": sum(r["kind"] == "entry" for r in rows), "exits": sum(r["kind"] == "exit" for r in rows)}
print(json.dumps(out, sort_keys=True, indent=1))
