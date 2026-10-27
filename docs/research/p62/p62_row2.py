import json
p = "docs/library/ideas.jsonl"
out = []
for l in open(p):
    if '"id": "p60-feeshare-commit-tag"' in l:
        r = json.loads(l)
        base = r["why_stopped"].split(" P62 step 1")[0]
        if base.startswith("Untested. "):
            base = base[len("Untested. "):]
        r["status"] = "parked"
        r["why_stopped"] = (
            "Desk-read idea; P62 (2026-10-27, PR #995) ran it on Aug/Sep exploration blocks and it is NOT_DECIDABLE (detector defect), not a KILL. "
            "Original kill-risk note: " + base + " "
            "P62 facts. The label is the fee-program SharingConfig PDA (seeds sharing-config + mint, program pfeeUx...); it is not in our tape. "
            "Today's state of 200 sampled coins: PDA exists 16 (8%), absent 184 (92%); 15 of the 16 have one shareholder, 16 of 16 admin_revoked. That is today's state, not causal. "
            "Timing check (p62_firsttx.py): the check was always true because 'Instruction: Create' also matches 'Instruction: CreateFeeSharingConfig', so the earlier claim '13 of 14 created in the create tx' was false. "
            "firsttx.out shows a tx logging CreateFeeSharingConfig 0 s after create for 2 coins, 1-2 s for 10, 31 s for 1 and 69 s for 1 (2 not found in the oldest 5 signatures). "
            "Step 2 (job #1707 at b6bec68e, N=300 per split vs 'up to 400' in the prereg; 286 discovery / 299 confirmation usable): the registered detector (CreateFeeSharingConfig in the create tx logs) found cfg=1 in 0 of 585; veto_share 1.0, AUC 0.5, placebo 0.5; bl_rate 0.7307692307692307 / 0.5919732441471572 (label too loose, 59-73%); bl_rate_cfg1 null (n_cfg1 = 0). "
            "Positive control (p62_poscontrol.py on the step-1 configured coins): the same detector returned 0 for 13 of 13 resolvable configured coins, including FcDNQrtL (0 s); 3 coins (incl. 2kQ1iKd9) could not be fetched from public RPC. So 'none found' says nothing about the world: the detector misses coins that do have a config (the config tx is not the create tx, or not visible in its logs; cause not diagnosed). "
            "The rule 'veto share above 50% also KILLs' was added by the P62 agent outside the brief.")
        r["revive_trigger"] = ("A detector that passes a positive control, i.e. finds the configs of the step-1 configured coins (configs created 0-69 s after create, known before a migration-time entry), "
                               "registered as a separate test with a stricter big-loser label; or a causal fee-share source (fee-program tx feed).")
        r["next_step"] = ("Find the creating tx of the SharingConfig for FcDNQrtL (0 s) and the other configured coins (getSignaturesForAddress on the PDA, oldest first, with a correct log match), "
                          "fix the detector, pass the positive control, then pre-register a new test with a stricter label. Public RPC, 0 Helius.")
        l = json.dumps(r, ensure_ascii=False) + "\n"
    out.append(l)
open(p, "w").writelines(out)
