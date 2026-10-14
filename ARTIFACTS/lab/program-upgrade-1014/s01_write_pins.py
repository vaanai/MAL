"""Step 1: regenerate the pins with the monitor's own `--write-pins` path, then diff against tools/pump_structure_pins.json.

Runs `tools.pump_structure_monitor.main(["--write-pins", PATH, ...])` in process, with the monitor's RpcClient subclass
that logs each real attempt (the CLI builds the same RpcClient; only the logging transport differs). Writes to a scratch
path first; the repo pins file is replaced by hand only if the diff is limited to programs.pump + pinned_utc + pinned_at_slot.
"""
import json
import sys

from common import OUT, RAW, client, m, REPO, write_json

scratch = RAW / "pins_new.json"
c = client(max_calls=40, cached=False)
rc = m.main(["--write-pins", str(scratch), "--min-interval", "0.6"], client=c)
print("monitor rc", rc, "attempts", c.total_calls, "ok", c.ok_calls, "retries", c.retries, "by_method", dict(c.calls))
if rc != 0:
    sys.exit(rc)

old = json.load(open(REPO / "tools" / "pump_structure_pins.json"))
new = json.load(open(scratch))


def flat(d, pre=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, pre + k + "."))
        else:
            out[pre + k] = v
    return out


fo, fn = flat(old), flat(new)
diff = {k: (fo.get(k), fn.get(k)) for k in sorted(set(fo) | set(fn)) if fo.get(k) != fn.get(k)}
allowed = {"pinned_utc", "pinned_at_slot", "programs.pump.deploy_slot", "programs.pump.deploy_utc", "programs.pump.sha256", "programs.pump.data_len"}
unexpected = sorted(set(diff) - allowed)
for k, (a, b) in diff.items():
    print(f"{k}: {a} -> {b}")
print("data_len differs:", "programs.pump.data_len" in diff, "(task expects it unchanged: len 10485760)")
print("UNEXPECTED DIFFS:", unexpected)
write_json("s01_pins_diff.json", {"attempts": c.total_calls, "by_method": dict(c.calls), "diff": {k: list(v) for k, v in diff.items()}, "unexpected": unexpected})
sys.exit(3 if unexpected else 0)
