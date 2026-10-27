"""P62 positive control (a): for each step-1 configured coin, find the tx among the oldest 5 PDA
signatures whose logs contain 'Instruction: CreateFeeSharingConfig', run the COMMITTED detector
(rpc_tx from p62_step2.py + any('CreateFeeSharingConfig' in log)) on that signature, and compare
that signature with the coin's create signature from the creates tape. Public RPC only."""
import json, subprocess, time

src = open("docs/research/p62/p62_step2.py").read().rsplit("\nmain()", 1)[0]
ns = {"__name__": "p62_step2_import"}
exec(compile(src, "p62_step2.py", "exec"), ns)
rpc_tx = ns["rpc_tx"]


def rpc(method, params):
    import urllib.request
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for i in range(6):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(ns["RPC"], body, {"Content-Type": "application/json"}), timeout=30))
            if "error" not in r:
                return r["result"]
        except Exception:
            pass
        time.sleep(1 + 2 * i)


rows = [r for r in json.load(open("docs/research/p62/sample_pda_acct.json")) if r.get("acct")]
import os, sys
OUTP = "docs/research/p62/p62_poscontrol_b_output.json"
out = json.load(open(OUTP)) if os.path.exists(OUTP) else []  # resume: skip coins already done
done = {o["mint"] for o in out}
for r in rows:
    if r["mint"] in done:
        continue
    create_sig = None
    for l in subprocess.run(["zstd", "-dc", "/data/mal/clean-view/" + r["file"]], capture_output=True, text=True).stdout.splitlines():
        try:
            c = json.loads(l)
        except ValueError:
            continue
        if c.get("mint") == r["mint"]:
            create_sig = c["signature"]
    sigs = rpc("getSignaturesForAddress", [r["pda"], {"limit": 1000}])
    o = {"mint": r["mint"], "create_sig": create_sig, "n_sigs": None if sigs is None else len(sigs), "cfg_sig": None}
    for s in (sigs or [])[::-1][:5]:
        t = rpc_tx(s["signature"])
        time.sleep(0.15)
        if t in ("ERR", None):
            continue
        logs = t.get("meta", {}).get("logMessages", [])
        if any("Instruction: CreateFeeSharingConfig" in x for x in logs):
            o["cfg_sig"] = s["signature"]
            o["cfg_delay_s"] = s["blockTime"] - r["create_ts"]
            o["detector_on_cfg_tx"] = int(any("CreateFeeSharingConfig" in x for x in logs))
            o["same_sig_as_create"] = s["signature"] == create_sig
            break
    out.append(o)
    json.dump(out, open("docs/research/p62/p62_poscontrol_b_output.json", "w"), indent=0)
    print({k: (v[:8] if isinstance(v, str) and len(v) > 40 else v) for k, v in o.items()}, flush=True)
json.dump(out, open("docs/research/p62/p62_poscontrol_b_output.json", "w"), indent=0)
f = [o for o in out if o["cfg_sig"]]
print("coins", len(out), "config tx found", len(f), "detector=1 on cfg tx", sum(o["detector_on_cfg_tx"] for o in f),
      "cfg tx == create tx", sum(o["same_sig_as_create"] for o in f))
