"""Regenerate the pins with the monitor's own `--write-pins` path (the 10-14 precedent, s01_write_pins.py), write to scratch,
diff against tools/pump_structure_pins.json. The repo file is replaced only if the diff is limited to the pump and pumpswap
program entries (deploy_slot, deploy_utc, sha256) plus pinned_utc and pinned_at_slot."""
import json
import sys

from common import OUT, RAW, REPO, LClient, m

scratch = RAW / "pins_new.json"
c = LClient(max_calls=40, min_interval=0.6)
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
allowed = {"pinned_utc", "pinned_at_slot"} | {f"programs.{p}.{f}" for p in ("pump", "pumpswap") for f in ("deploy_slot", "deploy_utc", "sha256", "data_len")}
unexpected = sorted(set(diff) - allowed)
for k, (a, b) in diff.items():
    print(f"{k}: {a} -> {b}")
print("UNEXPECTED DIFFS:", unexpected)
(OUT / "s02_pins_diff.json").write_text(json.dumps({"diff": diff, "unexpected": unexpected, "rpc_attempts": c.total_calls}, indent=1, sort_keys=True) + "\n")
