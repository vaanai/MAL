import json
p = "docs/library/ideas.jsonl"
out = []
for l in open(p):
    if '"id": "p60-feeshare-commit-tag"' in l:
        r = json.loads(l)
        w = r["why_stopped"]
        base = w.split("Original kill-risk note: ", 1)[1].split(" P62 facts.")[0]
        r["status"] = "explored-no-survivor"
        r["why_stopped"] = (
            "P62 (2026-10-27, PR #995) ran the in-create-tx version on Aug/Sep exploration blocks: KILL for that feature only. "
            "Original kill-risk note: " + base + " "
            "P62 facts. The label is the fee-program SharingConfig PDA (seeds sharing-config + mint, program pfeeUx...); it is not in our tape. "
            "Today's state of 200 sampled coins: PDA exists 16 (8%), absent 184 (92%); 15 of the 16 have one shareholder, 16 of 16 admin_revoked. That is today's state, not causal. "
            "Step 2 (job #1707 at b6bec68e, N=300 per split vs 'up to 400' in the prereg; 286 discovery / 299 confirmation usable): the registered detector (CreateFeeSharingConfig in the create tx logs) found cfg=1 in 0 of 585; veto_share 1.0, AUC 0.5, placebo 0.5; bl_rate 0.7307692307692307 / 0.5919732441471572 (label too loose, 59-73%); bl_rate_cfg1 null (n_cfg1 = 0). "
            "Positive control (a), p62_poscontrol_b.py: for the 16 step-1 configured coins the config transaction was found for 13 (oldest 5 PDA signatures, log 'Instruction: CreateFeeSharingConfig'); the committed detector returned 1 on all 13, and none of the 13 config signatures equals the coin's create signature (FcDNQrtL: create 2V9TF3h8..., config 48mKQ1BA..., 0 s later). Delays 0 s (1), 1-2 s (10), 31 s (1), 69 s (1). So the detector works, and the config is a separate transaction from the create transaction: a real configured coin is invisible to an in-create-tx test. The KILL stands for the in-create-tx feature: the registered detector found none in the pre-registered sample, and none can exist there. 3 coins had no config tx found (uJyBTLgd, 6CNTn9gT, AnHSooEv; RPC/window limits). "
            "An earlier claim '13 of 14 created in the create tx' was false (p62_firsttx.py matched 'Instruction: Create' inside 'Instruction: CreateFeeSharingConfig'). "
            "The rule 'veto share above 50% also KILLs' was added by the P62 agent outside the brief.")
        r["revive_trigger"] = ("A separately registered test of configs created 0-69 s after create (known before a migration-time entry), with a stricter big-loser label "
                               "and a detector that reads the config transaction (PDA signatures), or a causal fee-share source (fee-program tx feed).")
        r["next_step"] = ("If revived: pre-register a test where cfg = a CreateFeeSharingConfig tx on the coin's PDA with blockTime <= entry time, found via getSignaturesForAddress "
                          "(oldest first, correct log match), with a big-loser label that labels well under 59-73% of migrations. Public RPC, 0 Helius.")
        l = json.dumps(r, ensure_ascii=False) + "\n"
    out.append(l)
open(p, "w").writelines(out)
