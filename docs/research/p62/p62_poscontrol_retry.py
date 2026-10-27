"""Retry the positive-control coins whose RPC call failed in p62_poscontrol.py (same detector)."""
import json, subprocess, time

src = open("docs/research/p62/p62_step2.py").read().rsplit("\nmain()", 1)[0]
ns = {"__name__": "p62_step2_import"}
exec(compile(src, "p62_step2.py", "exec"), ns)
rpc_tx = ns["rpc_tx"]
rows = {r["mint"]: r for r in json.load(open("docs/research/p62/sample_pda_acct.json"))}
out = json.load(open("docs/research/p62/p62_poscontrol_output.json"))
for o in out:
    if o.get("rpc") != "fail":
        continue
    r = rows[o["mint"]]
    sig = None
    for l in subprocess.run(["zstd", "-dc", "/data/mal/clean-view/" + r["file"]], capture_output=True, text=True).stdout.splitlines():
        try:
            c = json.loads(l)
        except ValueError:
            continue
        if c.get("mint") == o["mint"]:
            sig = c["signature"]
    tx = rpc_tx(sig)
    if tx in ("ERR", None):
        print(o["mint"][:8], "still fails", "tx is None" if tx is None else "ERR")
        continue
    logs = tx.get("meta", {}).get("logMessages", [])
    o.pop("rpc")
    o["detector_cfg"] = int(any("CreateFeeSharingConfig" in x for x in logs))
    o["step1_delay_s"] = r["cfg_create"][0] if r.get("cfg_create") else None
    o["n_shareholders"] = r["acct"]["n_sh"]
    print(o)
json.dump(out, open("docs/research/p62/p62_poscontrol_output.json", "w"), indent=0)
print("detector=1:", sum(1 for o in out if o.get("detector_cfg") == 1), "detector=0:", sum(1 for o in out if o.get("detector_cfg") == 0),
      "unresolved:", sum(1 for o in out if "detector_cfg" not in o))
