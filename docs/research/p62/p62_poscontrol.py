"""P62 positive control. Runs the registered step-2 detector (rpc_tx from the committed
p62_step2.py, then the same any("CreateFeeSharingConfig" in log) test) on the step-1 coins whose
SharingConfig exists today. The create signature comes from the exploration creates tape, as in step 2."""
import json, subprocess, time

src = open("docs/research/p62/p62_step2.py").read().rsplit("\nmain()", 1)[0]
ns = {"__name__": "p62_step2_import"}
exec(compile(src, "p62_step2.py", "exec"), ns)
rpc_tx = ns["rpc_tx"]

rows = json.load(open("docs/research/p62/sample_pda_acct.json"))
out = []
for r in rows:
    if not r.get("acct"):
        continue
    sig = None
    p = "/data/mal/clean-view/" + r["file"]
    for l in subprocess.run(["zstd", "-dc", p], capture_output=True, text=True).stdout.splitlines():
        try:
            c = json.loads(l)
        except ValueError:
            continue
        if c.get("mint") == r["mint"]:
            sig = c["signature"]
    if sig is None:
        out.append({"mint": r["mint"], "found_sig": False})
        continue
    tx = rpc_tx(sig)
    time.sleep(0.15)
    if tx in ("ERR", None):
        out.append({"mint": r["mint"], "rpc": "fail"})
        continue
    logs = tx.get("meta", {}).get("logMessages", [])
    det = int(any("CreateFeeSharingConfig" in x for x in logs))
    fc = r.get("cfg_create")
    out.append({"mint": r["mint"], "detector_cfg": det, "step1_delay_s": fc[0] if fc else None,
                "n_shareholders": r["acct"]["n_sh"]})
    print(out[-1], flush=True)
json.dump(out, open("docs/research/p62/p62_poscontrol_output.json", "w"), indent=0)
print("configured step-1 coins", len(out), "detector=1:", sum(1 for o in out if o.get("detector_cfg") == 1),
      "detector=0:", sum(1 for o in out if o.get("detector_cfg") == 0))
