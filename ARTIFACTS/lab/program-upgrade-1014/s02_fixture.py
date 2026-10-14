"""Step 2: re-record tools/fixtures/pump_structure_monitor/programdata.json from public RPC (one getMultipleAccounts,
the same call and slice as monitor.stage_programs), in the order pump, PumpSwap, fees. Compact JSON, no trailing newline,
as the earlier fixtures. Prints the decoded deploy slots; they must equal the pins."""
import json

from common import REPO, client, m, write_json

PROGRAMDATA = {
    "pump": "B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu",
    "pumpswap": "6naEzKeUuFh1Jeeu51NXQgr5qkXgXtc9WKNct4xynVJc",
    "fees": "75Uu23mqWBb8LM8vDppqC1mQAnCcBuLXhVaDezVMQLRw",
}
c = client(max_calls=5, cached=False)
res = c.call("getMultipleAccounts", [list(PROGRAMDATA.values()), {"encoding": "base64", "dataSlice": {"offset": 0, "length": 45}, "commitment": "finalized"}])
path = REPO / "tools" / "fixtures" / "pump_structure_monitor" / "programdata.json"
path.write_text(json.dumps(res, separators=(",", ":")), encoding="utf-8")
vals = res["value"]
slots = [m.decode_programdata_slot(m._account_bytes(v)) for v in vals]
pins = json.load(open(REPO / "tools" / "pump_structure_pins.json"))["programs"]
print("ctx slot", res["context"]["slot"], "decoded deploy slots", slots, "spaces", [v["space"] for v in vals])
print("equal pins:", slots == [pins["pump"]["deploy_slot"], pins["pumpswap"]["deploy_slot"], pins["fees"]["deploy_slot"]])
auth = [m.b58encode(__import__("base64").b64decode(v["data"][0])[13:45]) if __import__("base64").b64decode(v["data"][0])[12] else None for v in vals]
print("upgrade authority (header bytes 13..45):", auth)
write_json("s02_fixture.json", {"ctx_slot": res["context"]["slot"], "deploy_slots": slots, "spaces": [v["space"] for v in vals], "upgrade_authority": auth, "attempts": c.total_calls})
