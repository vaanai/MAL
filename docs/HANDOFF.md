# Manager handoff 2026-10-08 ~20:20Z (manager9 → next manager)

Replace this page at the next handoff; don't append. Read it first. Then read:
- **H5 (the live candidate):** `/data/mal/hunt-1008/h5-work/PLAN.md`, `/data/mal/hunt-1008/h5-flows/{RULE,REPORT,VERIFY}.md`, `/data/mal/hunt-1008/JUDGE.md`. The PRs are listed below.
- **EXP-022 CAP-PICK Part 1:** [EXP/EXP-022-cap-pick-part1-prereg.md](../EXP/EXP-022-cap-pick-part1-prereg.md) §0, §2.1, §3, §9, §12, §17, and **Amendment 1**.
- **Memory notes:** `owner-goal-october`, `h5-boostfloor`, `feedback-fan-out`, `exp022-cap-pick`, `openrouter-key`, `audit-2026-10-08`, `feedback-seal-outcome-counts`, `long-jobs-via-miscusi`.
- **MiScusi notebook, 10-08 evening:**
  - H5: n_aKkhDDVDD8NWzA, n_XAOeNSejKdoCqA, n_hZAyYYgXzwukSQ;
  - E0 and scorer: n__JG6lTs4tLlSjQ, n_XhHuUT1hSpMCkw, n_YFa8N7PGPyraOg;
  - hunts: n_cvvYNpFkW0uNOQ, n_qWUXcU6JLYp7cA, n_DJQLZZS9jA-TjA.

## Where things stand

### Owner mandate and decisions (10-08)
- **Goal.** $1000 of REAL income by 10-31, with $400 as the minimum. The owner says "7 SOL in the wallet" from a 1 SOL start.
  - Full autonomy. Fan out many parallel tests.
  - $0 extra budget is unchanged. The sniper route is closed.
- **H5 live, owner-approved.**
  - **Canary.** About 0.25 SOL. The owner sends it to the Helm-held probe wallet on fast-0 **when the manager asks**. The ask comes after the executor is reviewed and EXP-024 is merged.
  - **Gate override, H5 only.** The owner answered "Yes, full 1 SOL" in session to scaling to 1 SOL BEFORE the formal EXP-024 read passes. The conditions are that live execution matches the sim and that the shadow and canary results are not negative, at about 10-12..14. It goes into DEC-024 as `OWNER_OVERRIDE_CONFIRMED`.
  - **Every other book keeps the gate.**
- **OpenRouter.** The key is at `/var/lib/mal/openrouter/openrouter.env` (research-0) with a $3 cap. The runner hard-stops at $1.50. Never print it.
- **LAYA.** Optimize the after-graduation books into a consistent base strategy that runs while short-lived edges rotate.
- **Priority (owner, 10-08 ~20:30Z).** **H5 is the absolute priority.** Other work continues only if it does not slow H5 down.
  - After H5, take an **improvement / self-improving path**. Do NOT close failed lines after one frozen test.
  - Iterate them (the LLM trader, LAYA, the cascades, …) with propose → evaluate → learn loops on development data until there are solid options.
  - Freeze before any fresh read. Memory: `feedback-iterate-not-close`.
  - So the "closed" note on the LLM trader means only that its frozen v1 failed.

### STATE 10-09 ~07:35Z (NEWEST, read first; supersedes the sections below where they differ)

**H5 live canary: installed and funded, but HELD by an A3 halt.**
- **Helm's install.** Helm installed sha `5a281b2` on fast-0 and completed runbook steps 1–10.
  - Manifest: 24/24 match (manifest file sha256 `1c2780c6…551e`, PR #499 comment 6074979090).
  - Old probe: disabled; its drop-ins are in /root/disabled.
  - Watchdog timer: enabled. The owner confirmed the Discord test arrived.
  - Not yet done: `/etc/mal-h5/TIER` and `LIVE_OK` are absent, and the wallet-wide `/var/lib/mal-live/STOP` is still in place.
- **Funding.** The owner funded **0.298688847 SOL**, finalized 07:00Z (tx 5fTmqeg68pGQeYd7Acd5…).
  - `/data/mal/hunt-1008/h5-work/FUNDED_SOL` holds this number. The 12:17Z cron d6590929 reads it.
  - Daily check job #444: ALERTS=0.
  - Probe-state baseline is written (`state-live.json` e11cba1d…1839, dec020 absent).
- **Helm's go message.** Drafted at `scratchpad/helm-go-h5.md`. **Do not send it until the A3 halt is cleared.** It asks Helm to:
  - set `H5_WATCH_FUNDED_SOL=0.298688847`;
  - do Step 11: remove the wallet STOP, write TIER=T0, create LIVE_OK, start the unit.
- **Helm's open items, already answered in the draft:**
  - `probe_has_key` is a false positive on systemd 255. It is fixed on main as d562287 (#512) and takes effect at the next pinned reinstall.
  - Sudoers: wait.
  - Runbook deviations: accepted.
- **DEC-024 Amendment 1** (#513, 196dc9b; owner decision n_7Vi03b-5G31yGA):
  - lifts the "never before 10-10T00Z" bar;
  - adds EXP-024 Amendment 3 (declared observation for pools with s0 before 10-10T00);
  - adds the stop-probability table (zero-edge P(total stop) 0.643);
  - adds the md5 decision-equivalence proof: `75cb0b0c585bc2479137cae31330e73e` on both sides, 72/72, job #446 on 88eef14.
- **Go-live shadow.** MiScusi **#447** on main 88eef14, with `H5_LOOK2_OBSERVED=EXP-024-Am2`, started 07:16:53Z. Dry run #435 on 5a281b2 is still running.

**A3 HALT, 2026-10-09T07:11Z** (job #445; notebook n_ci2HqDs94LOtIg).
- **Cause.** `pins_changed`: the pump, PumpSwap and fees programs were redeployed at 2026-10-08T16:20Z (new deploy slots 454596459, 454596406, 454596501).
- **Other flags are clean.** boost_enabled=1, InitBoost 16/16, budget 17.586 SOL, 29 slices. The last slice after migrate has median **335 s** (it was 341.5).
- **What it blocks:**
  - DEC-024 §3: no canary send while A3 shows a halt.
  - EXP-024 P2: the last A3 run before 10-10T00Z must show no halt, otherwise H5 is withdrawn.
  - EXP-022 P2: the same, before 10-16T01.
- **Path to clear it.** Do these steps in order:
  1. Program-upgrade review. Agent `a4772b9fb1d96a4d5` is writing it to `/data/mal/hunt-1008/h5-work/PROGRAM-UPGRADE-2026-10-08.md`. It covers instruction/IDL, fees, BOOST and our code paths. Early evidence after the upgrade: the shadow decodes and fires, and 5 dry-run simulations had 0 errors.
  2. Re-pin PR. Run `tools/pump_structure_monitor --write-pins`, which writes `tools/pump_structure_pins.json`. Get **quant-proof** to rule on two things: the review, and whether a reviewed re-pin plus a clean run satisfies EXP-024 P2 / §11 and EXP-022 P2 / §11.
  3. Merge the re-pin, then run the monitor again as a MiScusi job **before 10-10T00Z**. It must be clean.
  4. Send Helm the go.
- **Do not use shadow outcomes in this review** (EXP-024 Amendment 3).
- **If fees or pricing changed:** rerun the DEC-024 §8 stop table and tell the owner. The September evidence predates the upgrade.

**Program-upgrade review DONE (07:45Z).** The review is in `/data/mal/hunt-1008/h5-work/PROGRAM-UPGRADE-2026-10-08.md`, with a copy in hunt-reports.
- **Re-pinning is safe for layouts.** These are unchanged: PumpSwap buy, sell and buy_exact_quote_in, the decoder event offsets, V, the fee configs, GlobalConfig and BOOST (budget 17.586, 29–30 slices). Last-slice timing was 332.5–346 s before the redeploy and 335–344 s after.
- **What the upgrade added:** multi-hop swaps, a curve-depth setting (pump Global gained 1 byte), and pump errors 6098–6108. Docs commits `8cda1fa` and `2293f9a` say "pump_amm and pump_fees IDLs are unchanged".
- **NEW RISK: synthetic migrations.** These are graduations whose big first buy happens via the v3 buys rather than a PumpSwap trade.
  - Before the redeploy: 0/61. After: 12/61, and 6/16 in the latest window.
  - The monitor's `synthetic_share_high` reads **0.316 against its 0.35 halt**; the review's own sample read 0.375.
  - If it fires, EXP-024 ends (EXP-024:328-331) and CAP-PICK is withdrawn (EXP-022:358).
  - Unmeasured: on synthetic pools H5's s0 (first PumpSwap print) may come later, which eats the 330 s exit margin.
- **When re-pinning, also pin the three program sha256s** (the `program_changed` rule is not evaluated today).
- **Quant-proof must rule** whether a reviewed re-pin cures today's pins_changed halt before 10-10T00Z. EXP-024:330 says "H5 is withdrawn"; P2 says "last A3 run before 10-10T00Z shows no halt".
- **Re-pin builder started:** agent `a4c8233b0ab378c99`, branch `claude/a3-repin-1009`. It does `--write-pins` with sha256s, writes the note `ARTIFACTS/lab/a3-repin-2026-10-09.md`, and runs a dry monitor check.
- **NEXT:**
  1. Builder: re-pin PR (`--write-pins`, including the sha256s) plus a short re-pin note citing the review.
  2. Quant-proof: the cure ruling, and the synthetic-share risk.
  3. Merge the re-pin.
  4. Run the monitor as a MiScusi job before 10-10T00Z.
  5. If it is clean, send Helm the go (`scratchpad/helm-go-h5.md`).
  6. Tell the owner the synthetic-share risk plainly.

**Merged today, on main:**
- #500, #508: handoff;
- #501: EXP-025 C1-NF Part 1, two looks (α 0.005/0.020), quant-proof OK;
- #505: forward-1002ev, DEC-016 Am.9, EXP-024 Am.1, EXP-012 Am.5;
- #507: EXP-025 Am.1, P7 on raw events;
- #484 + #499: executor and live unit (5a281b2);
- #477, #510: shadow (310b194, fc0816a);
- #511: EXP-024 Am.2, declared observation for the Look 2 window plus a shadow code guard (88eef14);
- #512: credential-check fix (d562287);
- #513: DEC-024 Am.1 (196dc9b).

**Open drafts:**
- #502 C1-NF ledger;
- #503 C1-NF shadow;
- #504 C1-NF executor;
- #506 C1-NF features;
- #509 CAP-PICK boolean pick oracle (needs review and wiring before 10-16T01; quant-proof call on live-vs-replay divergence);
- #471 T2 (B90 kept, report-only);
- #476 EXP-024 scorer port (forward mode, V from forward-1002ev, before 10-16).

**Running jobs:**
- shadow #447 (fast-0);
- dry run #435 (fast-0);
- forward-1002ev walk **#433** (research-0; cap 3.6M credits, owner OK n_zrsp9q0hvvecdQ);
- DEC-016 walk #382.

**Owner decisions today** (MiScusi notebook):
- Scale ladder n_xaHk-8t27C8qbw. Per tier:

  | Tier | Stake (SOL) | Max open | Trades/day | Daily stop | Total stop |
  |---|---|---|---|---|---|
  | T0 | 0.02 | 2 | 30 | 0.08 | 0.12 |
  | T1 | 0.10 | 3 | 40 | 0.40 | 0.60 |
  | T2 | 0.30 | 3 | 40 | 1.20 | 1.80 |

  - Steps up about every 25–50 trades.
  - The total stop is also capped at 35% of the wallet at tier start.
  - Helm writes `/etc/mal-h5/TIER`.
  - T2_IMPACT_OK is True (IMPACT.md: 0.30 is OK, 0.50 fails in thin pools).
- C1-NF two looks n_YStdR3WX1QDkkw.
- C1-NF small live canary approved (O3) n_hZaavyDyNcJcZg. It needs DEC-026 and a second wallet.
- Credits: 3M n_hXFJlpzcwrI2Eg, raised to 3.6M n_zrsp9q0hvvecdQ.
- Early start n_7Vi03b-5G31yGA.

**Before 2026-10-16:**
1. Pick oracle #509: review and quant-proof, then wire it into the executor and shadow.
2. Executor live `end_ms` is 10-16T00:30Z. Extend it in a reviewed config plus a reinstall, bundled with d562287.
3. DEC-024 §4 caps the canary at **14 days**, about 10-23. **Ask the owner** to extend it.
4. EXP-022 re-pin / P2.
5. Walk-2 submit, cron 01c21bbd at 10-16T00:23.
6. EXP-024 read tool #476.

**C1-NF:**
- parity task 3: shadow picks vs VERIFY's 419;
- pinned model file and sha;
- DEC-026;
- Helm creates a second wallet.

**Agent ids:**
- program review `a4772b9fb1d96a4d5`;
- QPs: `a9f352620793c1fac` (#511), `acb1c807c558f8ac4` (#513);
- Am.2 builder `a69356b24d85bf901`;
- cred-fix builder `a636054d9c3906e95`;
- pick oracle `a07c78ea9889c77f6`;
- shadow follow-up `abce3497d3889689d`.
- The earlier ids are listed below.

### STATE 10-09 ~05:30Z (older)
**Owner decisions today** (all in the MiScusi notebook):
- Small live trades ASAP at 0.02 SOL/trade. The owner funds ~0.25 SOL only when the manager asks, after Helm's hash check.
- **Scale ladder:** T0 0.02 → ~50 trades → T1 0.10 → ~25 trades → T2 0.30 → onward, 25–50 trades per step. No skipped steps.
  - Mechanism: code `TIERS` plus a root-owned `/etc/mal-h5/TIER` written by Helm.
  - T2 is capped at 0.30: IMPACT.md says 0.50 fails in thin pools.
- **C1-NF (hunt-4 lead)** is registered as EXP-025 with two looks: Look 1 α 0.005 at ~10-17, Look 2 α 0.020 at ~10-24.
  - Merged fcc7e99, with quant-proof OK on final head 8a50400.
  - A small live C1-NF canary is approved (O3). It needs DEC-026 and a separate wallet.
- **Helius:** ~3.0M credits approved, raised to 3.6M, for the `forward-1002ev` event-V re-walk (job #433). It is the V source for EXP-024 Look 1 and EXP-025.

**H5 PRs:**
- #477 shadow, d3b69d0.
- #484 executor: round 6 in progress on 1866327+. G1 is a MUST: the 35% cap must use a tier-start realized baseline.
- #499 live unit: 6c6728d; re-merge the executor after round 6.
- Reviews are in /data/mal/hunt-1008/h5-work/REVIEW-*.md. The last one is REVIEW-last-7c4db74-3dbe1de.md.

**H5 jobs (fast-0):**
- shadow #428 at 3dbe1de; restart on the final #477 head before live;
- dry run #432 at 7c4db74;
- 5 clean simulated round trips on the earlier head: sells at s0+330.4..330.8 s.

**Remaining H5 order:**
1. Executor round 6.
2. Short delta check.
3. Merge #477, then #484, then #499 (retarget it to main).
4. Restart the shadow and the dry run on main.
5. Helm installs per docs/runbooks/h5-executor.md, including TIER=T0.
6. Hash check.
7. Re-register the 12:17Z cron to scripts/mal-fast/h5-daily-check.py.
8. Ask the owner for SOL.
9. LIVE_OK.

**Before 10-16T01:** the CAP-PICK boolean pick oracle (`claude/cap-pick-oracle`). Without it H5 refuses every buy, and the shadow seals every pool, from 10-16T01 to 11-06.

**forward-1002ev:**
- PR #505 (94c40a7+) holds DEC-016 Am.9, EXP-024 Am.1 and EXP-012 Am.5. Its quant-proof is on pass 3.
- PR #507 is EXP-025 Amendment 1, P7 on raw events. Its quant-proof is in progress.
- **Both must merge before 10-10T00Z.**

**C1-NF build drafts:**
- #502 ledger;
- #506 features (parity exact in replay mode; drift in live mode);
- #503 shadow (no pinned model yet);
- #504 executor.
- Next: replay parity of picks against VERIFY, a pinned model, DEC-026, and Helm for a second wallet.

**Other:** CAP-PICK T2 B90 passed its kill rule and is kept report-only (#471, d71060f).

**Agent ids:**
- executor `ab84c2d698b6716d6`; shadow `accfdac84de54afb2`; unit `a16b48c5c2858494d`;
- fwd-ev `a5bf01d461057fc20`; EXP-025 Am.1 `adb91a0dd7cd60256`; pick oracle `a07c78ea9889c77f6`;
- QP #505 `a83a7cdd6faf84f12`; QP #507 `acca18609cea186a9`; QP #501 `a7dc7c15365d0e3ce`;
- C1-NF ledger `a6eb8ab30e1b223dd`; features `ab22d98e7232b34be`; shadow `a916d155cb4c28525`; executor `a323ae5912ea507c4`;
- impact QP `a4588d78c7c6df5b7`.

### H5 live path, 10-09 ~01:40Z (newest; supersedes the H5 rows below where they differ)
- **Owner, 10-08 ~23:50Z:** "start up some small trades now... headstart... run the simulation alongside". The manager agreed and pulled the start forward. The owner sends ~0.25 SOL **only when the manager says so**, after Helm's hash check (standing rule).
- **Target live start:** ~10-09 evening UTC. That is after the 200 ms slot switch (~14:30Z): no trading through the switch.
- **Reviews done.** Findings are in /data/mal/hunt-1008/h5-work/:
  - REVIEW-86a224b.md: 3 MUST-FIX, all fixed in 96b677b..ab0b1cd.
  - REVIEW-484-ab0b1cd.md: 1 MUST-FIX on the wall anchor, plus sell bookkeeping and the #477 holes. The round-3 fixes are in progress.
  - REVIEW-499-429e2fa.md: runbook stop-with-open-positions, plus about 10 SHOULD-FIX. Fixes in progress.
- **Dry-run findings on the real feed** (jobs #404–#419). All are fixed in code:
  - The Helius env is root-only for jobs, hence `--rpc-env`.
  - The probe precheck stats /var/lib/mal-live.
  - The BOOST halt was per pool. Over 111 pools the last slice is median 340.2 s, 27% < 335. Now it uses the UTC-day median with ≥30 pools.
  - The gap hold fired on redundant reconnects (173/265). Now it holds only on flags_pools.
  - Trigger quality: only 5 of 15 pv triggers pass the strict checks, because raw base_breaks/slot_regress are mostly reorders. They are being replaced by `base_breaks_unresolved` from #477.
  - s0 − announced_slot: p50 0, p90 1, p99 4440. Refuse > 2.
- **Feed.** The public mainnet-beta WS is the only usable free feed; publicnode was useless (job #412). It yields ~1.5–3 tradeable triggers per hour, enough for the 30/day canary. For scale-up, ask the owner/Helm for a Helius WS on the shadow.
- **Branches:**
  - executor `claude/h5-executor`: head 4f05e30 plus the round-3 fixes; builder `ab84c2d698b6716d6`.
  - shadow `claude/h5-shadow` (#477): round-3 fixes in progress; builder `accfdac84de54afb2`. The **shadow job #399 must be restarted on the new #477 head before live**, because the executor requires the new fields.
  - live unit `claude/h5-live-unit` (#499, base claude/h5-executor): unit, pinned installer, `/etc/mal-h5/LIVE_OK` (root 0644), h5_sell_and_close, h5-daily-check, runbook; builder `a16b48c5c2858494d`.
- **Dry run:** job #416 (ab0b1cd, public RPC, out ~/data/h5-exec-dry3 on fast-0). Re-run it on the final head.
- **Remaining order:**
  1. Fixes land.
  2. A short delta review.
  3. Merge #477, then #484, then #499 (retarget to main) on main.
  4. Restart shadow #399 on main.
  5. Dry run on main for a few hours: needs ≥3 simulated buys and sells with no errors.
  6. Set `end_ms` in the live config (reviewed).
  7. Helm installs the main sha per docs/runbooks/h5-executor.md, then the hash check.
  8. Update the 12:17Z cron (0bdc383b) to scripts/mal-fast/h5-daily-check.py.
  9. Ask the owner for the SOL.
  10. Helm creates LIVE_OK.
- **Before 10-16T01:** the picks exporter plus FINAL_WRITTEN. Without them the executor refuses every buy in the seal window, which is fail-closed.

### Hunt 4 (DONE 10-09 ~00:30Z)
- JUDGE-4 is at /data/mal/hunt-1008/JUDGE-4.md. No frozen rule passed.
- Lead **C1-NF** (frozen C1 + top-holder share ≤ 0.5, post hoc): +8.454/+7.479%, n 422, 17/21 days, BOOST-independent, ~20/day. Its adversarial VERIFY is running (agent `a2dc77e610bfc3d72`, out /data/mal/hunt-1008/c1nf-verify/). Kill rules are in JUDGE-4 §3.3.1.
- Second lead: uninformed-sell walk-forward, +2.13/+1.66%.

### H5 BOOST-floor: the one real candidate
- **Rule.** After a non-mayhem graduation, if a sell drains the pool to Q = real quote + V ≤ 40 SOL within 0–300 s of the first print while BOOST still has budget, buy at 1.3 s and sell at s0 + 330 s.
- **Sep confirmation,** read once, 21 days, n 1,124, 0.25 SOL:
  - flat +13.221% [CI90 +8.281], 17/21 days;
  - press +11.434% [+7.165];
  - the verifier reproduced it.
- **Decaying:** +26.7 → +11.7 → +5.4 → +6.8%.
- **October risks:**
  - BOOST's last slice comes earlier, median 337 s (n 8), so the 330 s exit sits near the cliff;
  - since 09-30, v2 trades keep fees in the vault, so per-print V is needed;
  - 200 ms slots from about 10-09T14:30Z.
- **Judge prior** that it is still positive in October: 0.35. Net if real, after haircuts: +1.4..+3.1% per attempt.
- **One BOOST switch-off kills H5, H4 and CAP-PICK together.**

### Other hunt results (exploration tape; JUDGE.md, JUDGE-3.md)
- **Dead:** H1 pre-graduation, H3 hours, H6 census, G2 momentum, G1 post-BOOST dip, L2, L3 primary, LAYA v0, and the LLM trader (Claude −7.57%; selection lift p 0.19).
- **Weak:** H4 mid-BOOST; L1 RULE B (+1.28% flat / +0.81% press; fails under rent and cost stress).
- **H2 wallets:** one wallet (4XWC649w…) carries it.
- **Leads, unverified:** the L1 pool-entry sub-book; L3's hourly top-10.
- **G4 survivors:** reported promising and was being verified when hunt-2 was stopped and resumed. Check hunt-2's results.

### Disk incident (10-08)
- Hunts wrote per-branch tape copies and research-0 hit 81%. Fixed by stopping hunts, Helm's cleanup (56 GB) and `.nobackup` on `/data/mal/hunt-1008` and `/data/mal/audit-1008/{tape,work,tmp}`.
- **Rules for all hunts:**
  - use the shared layer `/data/mal/hunt-shared` (job #393, 9.2 GB, zstd) and no tape copies;
  - a 5 GB budget each;
  - `systemd-run MemoryMax` with `nice 19 ionice -c3`;
  - at most 4 at a time per workflow.
- **Small reports** are copied to `/data/mal/hunt-reports` (backed up).
- **`rm -rf` is denied to this session.** Write a script and ask the owner or Helm to run it.

## In flight at handoff (all on research-0 unless noted)

**Workflows.** Their results land in files even if this session ends. Check them.

| Task id | What | Output |
| --- | --- | --- |
| w9v7nomhy (wf_84831245-793) | Hunt-2 (DONE) | /data/mal/hunt-1008/JUDGE-2.md (extracted from the journal); notebook "Hunt-2 judged".
  - **G4-FLOORDIP** is the 2nd candidate and BOOST-independent: +1.88% / +1.56%, 13/15 days. It needs about 31 concurrent positions, so only about 0.016 SOL/day at 1 SOL; a scaling candidate.
  - **Ingredients:** the G7 R' veto and the O4 regime overlay.
  - **Nine frozen v1s failed;** they go to the post-H5 iterate loops. |
| w9b4m533o (wf_0d1a560e-ebe) | Hunt-4 (DONE): LAYA-filter cascades, analog trader, 9 strategy-tree leaves | /data/mal/hunt-1008/JUDGE-4.md, STRATEGY-TREE.md |
| wo1wq4khh (wf_f304b5f5-877) | LAYA-opt (DONE) | /data/mal/hunt-1008/laya-opt/REPORT.md; backup in /data/mal/hunt-reports/laya-opt; notebook n_51ukuyOnjwHmOA.
  - The development path is smooth, but the October central is ≈ 0 after selection.
  - **r4-a** is a migration race (closed route).
  - **r4-b** (l3 hourly top-10, no race, October central about +0.57%) is the only background-book candidate.
  - **AFTER H5:** decide whether to pre-register r4-b for a forward-1002 read before 10-16 (l3 VERIFY, event-V precount). Iterate-path idea: stack the G7 R' veto and the O4 regime overlay. |

**Builder agents.** If one is gone, check its branch; resume it or start a fresh builder.

| Branch / PR | What | State |
| --- | --- | --- |
| #477 `claude/h5-shadow` | H5 live shadow detector (fast-0, public RPC, no keys) | Head **ff31026**, 86 tests. Replay matched the frozen triggers 72/72 on 09-20, and 11/11 again on hour 09-20T20 at ff31026. The reviewer's SHOULD-FIX 1–7 and the nits are applied: the seal hook `suppress_outcome` fails closed from 10-16T01; there is an exit strip; the sps fit is ready in about 8 s; gap records name each reconnect cause; reject records list both mints. Smoke #395 (old head) passed, with 3/3 CreatePool rejected as non-WSOL (maybe genuine non-WSOL pools; reject records will show) and 3 reconnects in 2 min. **Smoke #396 passed** (10 min, 3 sockets, ff31026): 7 pools tracked; 1 trigger, logged as both the `pv` and `fv` variants on one pool; 1 outcome and 1 strip; sps 0.2705 s (pre-200 ms); 298 MB peak. **42 gap records in 10 min:** public-RPC coverage is the risk, so check the gap causes in the long run's records. **LONG RUN STARTED: MiScusi job #399 on fast-0** (ff31026, `H5_SOCKETS=3`, resumable, 1.5 GB, 7-day limit; extend it before it runs out). Out `$HOME/data/h5-shadow` on fast-0.
  - EXP-024 is merged, so Look-1-window outcomes are declared-observed.
  - From 10-16T01 the stub oracle suppresses all outcomes (seal) until a real pick oracle exists.
  - Check daily: the gap-record rate, `unannounced_fresh`, the reject mints and the trigger count.
  - #477 itself is not merged yet: delta-review it and merge. |
| **#484** `claude/h5-executor` | H5 live executor on the probe_live / probe_executor core | Head **e436b28**, 132 new tests, plus the 299 probe tests still passing. Dry-run by default. It parses #477's trigger, gap, hb and pool records (pv variant).
  - **Limits:** 0.02 SOL stake, max 2 open, 30/day, stops 0.08 / 0.12.
  - **Sell:** prebuilt and timed to land at the exit slot. Set `exit_land_offset_s: 0.55` to match the frozen rule exactly. An emergency sell runs at 400 s. Token and WSOL accounts are closed for rent.
  - **Halt and seal:** live-halt rules are latched. The seal `pick_oracle` refuses every buy in the seal window until a boolean picks exporter exists (none yet). Live refuses until EXP-024 is on main.
  - **Live needs Helm:** a systemd unit with `LoadCredential` for the probe key, which is root-only (so live is NOT a MiScusi job; the dry run is), plus a root-run `sell-and-close` tool for abandoned positions.
  - **Manager:** LIVE_OK, the `end_ms` config, the `FINAL_WRITTEN` marker after the FINAL, and updating the 12:17Z probe-check cron (the PR body has the change).
  - **Next:** reviewer, then a dry run on fast-0 against the shadow output. |
| #478 (merged **9b82bc0**, 2026-10-08T20:25Z) | EXP-024 Part 1, DEC-023, DEC-024, DEC-021 Am.2, DEC-016 Am.7, EXP-012 Am.3, EXP-022 Am.2, ledger | **MERGED** with quant-proof OK on 8a20a82 (comment 6068403337). The EXP-024 deadline is met.
  - Live canary trades may start once the executor is reviewed and the Helm unit exists.
  - Declared observation covers Look 1's window [10-10T00, 10-16T00).
  - Pins: E0-H5 day 2026-09-20; monitor blob 1ca0a88c.
  - **Open:** the owner must state the 1 SOL stake, open cap and stops in writing in DEC-024 before any scale-up. DEC-024 needs ≥100 fills and ≥20 landed buys for the scale-up checks.
  - Cron 7b7a30bc (10-09 18:13Z) is now moot. |
| #476 `claude/h5-boostfloor-score` | H5 scorer port (32/32 cells and every trade reproduced) | Draft. Still needs a forward mode, V(t) pricing, the correction and the day-level t for Look 1 (by 10-16T00Z) |
| #479 `claude/cap-pick-exp022-mode` | `--exp022` mode in cap_pick_score: constants and `exp022_universe()` with walk2 and exploration adapters | Quant-proof OK-WITH-EDITS on 86355b3 (comment 6068166437). Edits done at **be7cdb2**, 229 tests:
  - the exploration source pins `--vmap` to `/data/mal/pumpswap-virtual/pool_v_0909.json`;
  - `--hour-sph-json` must be absent (tape-only hours; an unmeasurable hour refuses);
  - bad-reserves picks stay `status=attempt` with `priced=false`.
  **Next: a quant-proof re-check of 86355b3..be7cdb2, then merge.** The read-tool (P4) items are listed in the PR body. |
| **#480** (merged **6b9b4fc**) | E0 harness on the scorer's EXP-022 mode, plus the B-pick guard (empty allowlist) | Quant-proof OK on 6a6b4d6 (comment 6068792855). Reviewer APPROVE. **The OFFICIAL E0 PASSED** (job #397, main 6b9b4fc; all `check` flags true; out `/data/mal/exp022/e0-official/`). **E0 record amendment (Am.3) MERGED** (#496, quant-proof OK on 0df2497). **P1 E0 is DONE.** Remaining before 10-16T01Z: a clean A3 monitor run (daily cron) and walk 2 submitted at about 00:30Z (cron 01c21bbd). Before 10-23: the read tool (P4), E1 and A2.
  - **If it passes, write the E0 record amendment** (dated, with quant-proof) before 10-16T01Z. It must include:
    1. the four blobs, `imported_module_blobs` and the venv versions, plus `FROZEN.md5`;
    2. the first-boot staging hours (10-14T21..10-15T23, plus the 10-16T00 feed hour);
    3. the exploration-vs-walk2 adapter differences (#479 body);
    4. **the 08-20 dry-run precount disclosure** (harness 5485459, scorer 86355b3, decide md5 bed92c12…, C md5 196cbbd8…, n_C 98);
    5. **the empty pick allowlist as a tightening of §2.1 item 3**;
    6. the pick_oracle and seal.
  - **If it fails, do NOT retry blindly.** Diagnose. Nothing may be fixed and retried after the window starts, but before 10-16T01 a fix and re-run is allowed (§2.1). |

**MiScusi jobs**

| Job | What | Notes |
| --- | --- | --- |
| #382 | Forward walk 1 (forward-1002) | **Extend or resubmit before ~10-15T11Z** (cron a676221a). Never open its outputs before the FINAL. |
| #391 | T2 B90 full run (#471), 5 configs | Report-only. Read the picks P2–P4 scopes against the §7 kill rule in `ARTIFACTS/lab/cap-pick-t2-boost-exit-predeclare-2026-10-08.md` |
| #394 | OpenRouter arm of the LLM trader | **Done, dead** (notebook n_o6OGSKeiRBB5Rg). Nemotron free −6.64% flat (n 90). DeepSeek paid −6.28% flat (n 40). Both FAIL. The named arm produced no file. Spend $0.08. The LLM-trader line is closed. |

## EXP-022 (CAP-PICK) path to 10-16T01Z

**Merged.** #461 scorer (7c2e587), #462 gate replay (blob 7d208874), #473 harness, #474 Amendment 1. The #387 kill check came back not a kill.

**Remaining, in order:**
1. **Merge #479**, after its edits and a quant-proof re-check.
2. **Merge the E0 harness PR**, after review and quant-proof.
3. **Official E0** on main as a **28 GB MiScusi job**: `run` with the pinned view explore-0814 and day 2026-08-20, no `--dry-run`. Then `check e0.json`.
4. **E0 record amendment,** in a dated EXP-022 amendment with quant-proof. It must include:
   - the four blobs plus every imported module blob (from `e0.json imported_module_blobs`) and the venv versions;
   - `FROZEN.md5`;
   - the first-boot staging hours: forward-1002 creates 10-14T21..10-15T23 (27 staged, 26 opened) plus the feed hour 10-16T00, all inside the buffer;
   - every difference between the exploration and walk2 adapters (from #479's body);
   - the pick_oracle and seal.
5. **Walk 2** submitted at about 10-16T00:30Z (cron 01c21bbd). Run `bash scripts/research/forward-walk2.sh` with params `{"start":"2026-10-16T01"}`, resumable, 10080 min, about 6 GB.
6. **A clean A3 monitor run** before 10-16T01 (the daily cron).
7. **Before 10-23T01:**
   - the read tool (P4), including the open items in #479's body and the notebook entry n_XhHuUT1hSpMCkw;
   - E1 (cron 3d374658);
   - A2.

## After a compaction (same session, owner's plan 10-08 ~20:40Z)
- **What survives a compaction:** crons (check with CronList), background agents, workflows and background shell loops. **Verify, don't recreate**, unless CronList shows one missing.
- **Watchdogs** (background loops on research-0):
  - the disk watchdog (task b975b8ddl): warns at 86%, SIGSTOPs hunts at 90%, syncs reports;
  - the CPU guard, restarted at handoff (task bf3f9fmn1): renices hunts at load 48.
  - If either is missing (`pgrep -af 'df --output=pcent'` or `pgrep -af loadavg`), restart it.
- **Agent ids, to resume with SendMessage:**
  - EXP-024 bundle builder `adc69e946dde3c975` (applying quant-proof edits 1–5 and the pins on #478); EXP-024 quant-proof `a68da208e5f9cdb81`;
  - H5 shadow builder `accfdac84de54afb2`; shadow reviewer `a16726a9ec630a41f`;
  - H5 executor builder `ab84c2d698b6716d6`;
  - #479 builder `a31eac1461a68c989`; #479 quant-proof `ad2e39687ffc72eeb`;
  - E0 harness builder `ac12844d0ae1b5598` (branch `claude/cap-pick-e0-exp022`, plus the precount); harness reviewer `a70139af1577132a7`; harness quant-proof `a4efdfa6397dca01f`;
  - T2 builder `a7815fa1df1ceed25`.
  - H5 live-unit builder `a16b48c5c2858494d` (#499); C1-NF verifier `a2dc77e610bfc3d72`.
- **Workflows:** hunt-2 `w9v7nomhy`, hunt-4 `w9b4m533o`, LAYA-opt `wo1wq4khh`.
- **MiScusi hand-off ho_Mkus_DAfsz-6iw** was prepared, but the owner chose to compact instead. Ignore it.

## First things to do in a new session (only if the session is replaced)
1. Run `miscusi_worker_start` with `inbox: true`, name `manager9`.
2. **Recreate the crons with CronCreate.** They are session-only, so they are gone.
   - **Daily structure monitor, 06:41Z.** Research-0, ops, 300 MB, 20 min: `/data/mal/venv/bin/python -m tools.pump_structure_monitor --out /data/mal/structure-monitor/daily.jsonl`. Read `halt.*`, `warn.*` and the new `watch.*` (#470). A halt before counting withdraws EXP-022.
   - **Daily decommissioned-probe check, 12:17Z.** Fast-0, the job #385 command. **Update it before the H5 canary is funded**, because it alerts when the probe wallet is active.
   - **Daily tip-tape archive, 03:23Z.** Fast-0, the job #352 command.
   - **One-shots:**
     - 10-09 15:13Z: the slot step;
     - 10-09 18:13Z: the EXP-024 merge check;
     - 10-13 09:17Z: EXP-022 readiness;
     - 10-14 10:07Z: (e′) dry run;
     - 10-14 11:23Z: extend #382;
     - 10-14 12:41Z: probe rent audit;
     - 10-16 00:23Z: submit walk 2;
     - 10-16 06:13Z: E1.
3. **Check the watchdogs on research-0** (they died with the session):
   - **Disk:** warn at 86%; at 90%, SIGSTOP the hunt processes.
   - **CPU:** renice hunt processes to 19 when load is 48 or more.
   - **Reports:** sync `/data/mal/hunt-1008` small files to `/data/mal/hunt-reports` every 10 min.
   Restart them as background loops (see this session's commands in the transcript), or as a cron.
4. **Collect results:** JUDGE-2, JUDGE-4, laya-opt REPORT, job #391, job #394. Report them to the owner.

## Rules that bite
- **The 10-16 FINAL** runs as written at about 10-16T02Z. It is reported compromised, and `exp012_forward` runs without `--strict-lines`. The (e′) dry run is due before 10-15T23Z.
- **EXP-022 seal (§9).** No outcome of a counted CAP-PICK hour is opened, priced or printed from any source except the sealed look. From 10-16T01, H5 (live and shadow) must not trade or price CAP-PICK picks: the pick_oracle reads only a boolean after the FINAL and fails closed.
- **EXP-024 declared observation.** The canary and shadow outcomes in Look 1's window [10-10, 10-16) are watched live. Look 1 is always read and reported as written, and nothing it shows changes EXP-024.
- **Hunts use exploration tape only.** Sealed blocks fresh-0802, fresh-0808 and fresh-0828 are never read.
- **Reserve convention:** PumpSwap rows are PRE-trade with V; bonding rows are POST-trade.
- **Subagents.** Builders stop at about 40 turns and reviewers at about 20; resume them with SendMessage. Builder-type agents can't call MiScusi tools, so the manager submits jobs. A reviewer once detached another agent's worktree, so always tell reviewers to use their own scratch worktree.

## Clocks
| When | What |
| --- | --- |
| ~10-09T14:30Z | 200 ms slots |
| 10-09 | H5 executor review; shadow long run; ask the owner for the 0.25 SOL once EXP-024 is merged and the executor is reviewed |
| **before 10-10T00:00Z** | **EXP-024 bundle merged (#478)** |
| ~10-10..15 | H5 canary live (0.02 SOL stakes) |
| ~10-12..14 | Scale H5 to 1 SOL per the owner override, if the conditions hold |
| before ~10-15T11Z | Extend #382 |
| before 10-15T23Z | (e′) dry run |
| before 10-16T00Z | #476 forward mode for EXP-024 Look 1 |
| before 10-16T01Z | E0 and its record amendment; clean monitor; walk 2 submitted |
| ~10-16T02Z | FINAL |
| ~10-16T07Z..10-17T12Z | EXP-024 Look 1 |
| 10-23 / 10-30 / 11-06 T01 | EXP-022 looks |
| 10-31 | Owner's income goal |
