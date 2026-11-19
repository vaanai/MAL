"""DEC-036 section 9 item 10 / CLAUDE.md: md5 decision-equivalence replay of the DEC-035-class DSL lane executor (tools/forge/dsl_lane_executor.py).

Not collected by pytest under tools/. Run it from a checkout root, once per tree, and compare the printed md5:

  REPLAY_OUT=/tmp/x.json /data/mal/venv/bin/python -m pytest <this file> -q -s -p no:cacheprovider

(it imports the DSL executor's own test helpers, which exist at origin/main). It replays one synthetic decision log through D.DslLaneBridge with a fake live port: two entries, a take-profit exit,
a lane-full refusal, a STOP-file refusal, a daily-stop refusal. The decision rows (refusal reasons, sends, sells, order statuses, ledger rows without random row ids, schema and code hash) are
written to $REPLAY_OUT; the printed DECISION_ROWS_MD5 line is their md5.
"""

import hashlib
import json
import os

from tools import floor_lane_executor as X
from tools.forge import dsl_lane_executor as D
from tools.test_floor_arena_executor import Clock, _env  # noqa: F401
from tools.test_floor_lane_executor import T0, other_bot_rows, other_cash
from tools.test_forge_dsl_lane_executor import Log, feed, mk, refusals, started


def test_replay(tmp_path):
    clk = Clock(T0)
    br, inner = mk(tmp_path, clk)
    lg = started(tmp_path)
    lg.entry(T0, "o1")
    lg.entry(T0 + 1000, "o2", pool="Q")
    feed(br, clk)
    inner.land_buy("MP")
    inner.land_buy("MQ")
    br._poll_ports()
    lg.exit(T0 + 20_000, "o1", reason="tp")
    feed(br, clk)
    inner.land_sell("MP")
    br._poll_ports()
    other_bot_rows(tmp_path, X.LANE_MAX_OPEN)
    lg.entry(T0 + 60_000, "o3", pool="R")
    feed(br, clk)
    (tmp_path / "lane" / "STOP").write_text("x")
    lg.entry(T0 + 90_000, "o4", pool="S")
    feed(br, clk)
    (tmp_path / "lane" / "STOP").unlink()
    other_cash(tmp_path, 100_000_000, snap_day=D.H.day_of(T0 + 120_000), now=T0 + 100_000)
    lg.entry(T0 + 120_000, "o5", pool="T")
    feed(br, clk)
    rows = [json.loads(x) for x in (tmp_path / "lane" / "ledger.jsonl").read_text().splitlines()]
    ledger = [{k: v for k, v in r.items() if k not in ("row_id", "schema", "code_sha256", "signature")} for r in rows]
    out = {"refusals": [r["why"] for r in refusals(br)], "buys": [(b["mint"], b["stake_lamports"]) for b in inner.buys], "sells": inner.sells,
           "orders": {k: (o["status"], o.get("refused")) for k, o in sorted(br.state.orders.items())}, "ledger": ledger}
    blob = json.dumps(out, sort_keys=True, default=str)
    if os.environ.get("REPLAY_OUT"):
        open(os.environ["REPLAY_OUT"], "w").write(blob)
    print("DECISION_ROWS_MD5", hashlib.md5(blob.encode()).hexdigest())
