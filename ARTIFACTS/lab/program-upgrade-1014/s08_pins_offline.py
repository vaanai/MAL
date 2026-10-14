"""Step 8 (offline, no RPC): re-evaluate the 2026-10-14T07:11:10Z A3 record (the halted run, last line of daily.jsonl) against the
OLD pins (git HEAD~ of tools/pump_structure_pins.json via --old) and the NEW pins (the working tree file), with the monitor's own
compare_pins / compare_program_hashes. Not an A3 run and not the P2 run: nothing is appended anywhere."""
import json
import subprocess
import sys

from common import REPO, m, write_json

rec = json.loads(open("/data/mal/structure-monitor/daily.jsonl").read().splitlines()[-1])
old_txt = subprocess.run(["git", "-C", str(REPO), "show", "main:tools/pump_structure_pins.json"], capture_output=True, text=True, check=True).stdout
new = m.load_pins(REPO / "tools" / "pump_structure_pins.json")
import tempfile, pathlib

tmp = pathlib.Path(tempfile.mkdtemp()) / "old_pins.json"
tmp.write_text(old_txt)
old = m.load_pins(tmp)
out = {"record_run_utc": rec["run_utc"]}
for name, pins in (("old_pins", old), ("new_pins", new)):
    pc = m.compare_pins(rec, pins)
    ph = m.compare_program_hashes(rec, pins)
    out[name] = {"pins_check": {k: pc.get(k) for k in ("changed", "unpinned", "unread", "ok")}, "program_hashes": {k: ph.get(k) for k in ("changed", "unpinned", "unread", "ok")}}
    print(name, json.dumps(out[name]))
write_json("s08_pins_offline.json", out)
sys.exit(0)
