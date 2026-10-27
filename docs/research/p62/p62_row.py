import json
p = "docs/library/ideas.jsonl"
out = []
for l in open(p):
    if '"id": "p60-feeshare-commit-tag"' in l:
        r = json.loads(l)
        r["why_stopped"] += (" P62 step 1 (2026-10-27, public RPC, 0 Helius): label = SharingConfig PDA (seeds sharing-config+mint, program pfeeUx...), "
                             "created inside the create tx (13 of 14 checked), NOT in our tape, needs one getTransaction of the create signature per coin (causal). "
                             "Sample of 200 Aug/Sep coins: PDA exists 16 (8%), absent 184 (92%); of the 16, 15 single shareholder, 16 admin_revoked. Not >95% constant, so not killed at step 1. "
                             "Step 2 pre-registered in docs/research/p62/PREREG.md; run is MiScusi job #1703; result in the PR.")
        l = json.dumps(r, ensure_ascii=False) + "\n"
    out.append(l)
open(p, "w").writelines(out)
