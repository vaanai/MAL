#!/usr/bin/env python3
"""Re-record ONLY the GlobalConfig entry (values[2]) of tools/fixtures/pump_structure_monitor/config_accounts.json.
One fresh finalized read; asserts sha256 == the gc_diff.py result (a4c67d6d...). The file is edited at text level:
only the old base64 data string is replaced, so every other byte (other entries, context, formatting) is unchanged."""
import base64, hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gc_diff

EXPECT = "a4c67d6dc665973c16ae2b4c41400e0eb8d728414f358ea41ddd925d058ca011"  # FULL_NEW_SHA256 of gc_diff-output.txt
FX = gc_diff.REPO / "tools/fixtures/pump_structure_monitor/config_accounts.json"

txt = FX.read_text()
d = json.loads(txt)
ent = d["value"][2]
assert ent["owner"] == "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA" and ent["space"] == 949
old_b64 = ent["data"][0]
old = base64.b64decode(old_b64)
assert hashlib.sha256(old).hexdigest() == gc_diff.OLD_SHA, "entry 2 is not the old GlobalConfig"
r = gc_diff.fetch()["result"]
new = base64.b64decode(r["value"]["data"][0])
assert hashlib.sha256(new).hexdigest() == EXPECT, "fresh read sha != gc_diff result"
assert gc_diff.ranges(old, new) == [[57, 312]]
assert txt.count(old_b64) == 1
FX.write_text(txt.replace(old_b64, r["value"]["data"][0]))
print("re-recorded values[2] at slot", r["context"]["slot"], "sha256", EXPECT)
