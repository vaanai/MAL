"""Re-record tools/fixtures/pump_structure_monitor/programdata.json (the three programdata headers, 45 bytes each, order pump, pumpswap, fees)
from public RPC (finalized, dataSlice 0..45), as the 10-14 and 10-21 re-pins did (copy of program-upgrade-1021/s03_rerecord_fixture.py; QP ruling
on PR #1383 fix list item 1). Not hand-edited. The output must decode to [469055660, 458921517, 456132171]; an output note goes to out/s15_fixture.json."""
import json
from common import OUT, REPO, LClient
import tools.pump_structure_monitor as mon
c = LClient(max_calls=5)
accts = ["B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu", "6naEzKeUuFh1Jeeu51NXQgr5qkXgXtc9WKNct4xynVJc", "75Uu23mqWBb8LM8vDppqC1mQAnCcBuLXhVaDezVMQLRw"]
r = c.call("getMultipleAccounts", [accts, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 45}, "commitment": "finalized"}])
p = REPO / "tools/fixtures/pump_structure_monitor/programdata.json"
p.write_text(json.dumps(r, separators=(",", ":")), encoding="utf-8")
slots = [mon.decode_programdata_slot(mon._account_bytes(v)) for v in r["value"]]
note = {"context_slot": r["context"]["slot"], "decoded_deploy_slots": slots, "expected": [469055660, 458921517, 456132171], "ok": slots == [469055660, 458921517, 456132171], "rpc_attempts": c.total_calls}
(OUT / "s15_fixture.json").write_text(json.dumps(note, indent=1) + "\n")
print(json.dumps(note))
