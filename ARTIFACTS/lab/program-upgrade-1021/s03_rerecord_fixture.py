"""Re-record tools/fixtures/pump_structure_monitor/programdata.json (the three programdata headers, 45 bytes each, order pump, pumpswap, fees)
from public RPC, as the 10-14 re-pin did. Order = the monitor's `names` order (read from the existing fixture's program accounts)."""
import json
from common import REPO, LClient
c = LClient(max_calls=5)
accts = ["B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu", "6naEzKeUuFh1Jeeu51NXQgr5qkXgXtc9WKNct4xynVJc", "75Uu23mqWBb8LM8vDppqC1mQAnCcBuLXhVaDezVMQLRw"]
r = c.call("getMultipleAccounts", [accts, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 45}, "commitment": "finalized"}])
p = REPO / "tools/fixtures/pump_structure_monitor/programdata.json"
p.write_text(json.dumps(r, separators=(",", ":")), encoding="utf-8")
print(json.dumps(r)[:300])
