"""md5 decision-equivalence for the optional lane config key canary_max_ms (key ABSENT). Synthetic fixtures only (the repo's own test fixtures); no tape, ledger, declog or out/ dir is read.

  /data/mal/venv/bin/python ARTIFACTS/lab/canary-max-ms/equiv_canary.py <repo_root>   # once with the base worktree, once with the head worktree; compare the JSON

Legs: host decisions for v2, v3 (q 500/750/1000 M) and v4@1.3 on the fixture scenarios (decision md5 and whole-log md5, as ARTIFACTS/lab/floor-v4-rule/equiv_v2_v3.py), and the
executor's 14-day canary scenario (key absent): md5 of the would-send order ids plus the refusal count names. Names, md5s and counts only.
"""

import collections
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

os.environ["MAL_ARENA_TEST"] = "1"
import tools.test_arena_rule_l1 as T  # noqa: E402
import tools.arena_paper as AP  # noqa: E402
from tools import floor_lane_host as H  # noqa: E402

AP.shutil.disk_usage = lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500)
T.SHA = H.FROZEN_SHA
from tools.arena_declog import DecisionLog  # noqa: E402
from tools.test_floor_arena_executor import _host_log_scenarios  # noqa: E402
import tools.test_floor_arena_executor as TE  # noqa: E402
from tools.test_floor_lane_host_v3 import cooldown_rows, mid_vault_rows, run_rule  # noqa: E402
try:
    from tools.test_floor_v4_rule import _run_v4  # noqa: E402
except ModuleNotFoundError:  # a tree older than the v4 rule: the v4 arm is skipped and named so
    _run_v4 = None

assert H.__file__.startswith(str(root)), (H.__file__, root)
SCEN = {"three_pool": _host_log_scenarios, "cooldown": cooldown_rows, "mid_vault": lambda tmp: mid_vault_rows()}
out = {}
for arm, rid, q in [("v2", "l1-floor-v2", None), ("v3_q500", "l1-floor-v3", 500_000_000), ("v3_q750", "l1-floor-v3", 750_000_000), ("v3_q1000", "l1-floor-v3", 1_000_000_000), ("v4_tp1.3", "l1-floor-v4", None)]:
    for sn, fn in SCEN.items():
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            if rid == "l1-floor-v4" and _run_v4 is None:
                out[f"{arm}/{sn}"] = "skipped_tree_has_no_v4"
                continue
            if rid == "l1-floor-v4":
                _run_v4(tmp, fn(tmp), 1.3, hours=60)
            else:
                run_rule(tmp, fn(tmp), rid, q, hours=60)
            log = DecisionLog(str(tmp / "rules"))
            rows = log.rows(rid)
            out[f"{arm}/{sn}"] = {"decision_md5": H.decision_digest(tmp / "rules", bot_id=rid), "log_md5": log.md5(),
                                  "entries": sum(r["kind"] == "entry" for r in rows), "exits": sum(r["kind"] == "exit" for r in rows)}

# executor leg: the 14-day canary trip with the key absent (the test_floor_arena_executor fixture)
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    clk = TE.Clock()
    lg = TE.Log(tmp)
    lg.start(TE.H0)
    lg.entry(TE.H0 + 1000, "o1")
    br, port = TE.bridge(tmp, clk)
    TE.feed(br, clk)
    lg.entry(TE.H0 + 15 * 86_400_000, "o2", pool="Q")
    clk.t = TE.H0 + 15 * 86_400_000 + 50
    for r in br.read_new_rows():
        br.handle_row(r)
    ids = [w["order_id"] for w in port.would_send]
    out["executor/canary_14d_absent"] = {"would_send_md5": hashlib.md5(json.dumps(ids).encode()).hexdigest(), "n_would_send": len(ids),
                                         "refused_canary_expired": br.state.counts.get("refused_canary_expired"), "effective_ms": getattr(br.cfg, "canary_max_ms", TE.F.CANARY_MAX_MS)}
print(json.dumps(out, sort_keys=True, indent=1))
