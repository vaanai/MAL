# SHAKEDOWN-CHECKS-4-NOTES-1

Applies to SHAKEDOWN-CHECKS-4.md (sha256 a9b019ca9c8db38e4172a04ceb338c6edc03ef17333bc0ae3d462079bc7dd583), which stays byte-unchanged. Written on quant-proof's ruling on PR #774 (comment 6289565427).

R-1. Run sha. Change 1's run sha is the merge commit of #774 on main, 9ed4c5ff63935696b2a89150e13565e123047754, which becomes HV4_FROZEN_SHA. This replaces "the merged sha of #771" (b240191) in change 1 and nothing else.

R-2. What differs from b240191. In tools/hv4_runner.py only main()'s --register branch changes, plus two helpers (register_arms, arm_names_for) that Runner.register_all and Runner.__init__ call with the same arguments and expressions as before. A registration process builds no chain source, prefetcher or Runner, makes no chain call, and writes no obs row and no file under the runner root. The frozen-checkout check still runs before it writes bots.json. The run step (no --register) builds the chain source, starts the prefetcher and builds the Runner in the same order and with the same arguments as at b240191. tools/hv4_chain.py, hv4_prefetch.py, hv4_cards.py, hv4_arms.py, hv4_model.py, arena_paper.py, arena_rules.py and scripts/ are byte-identical to b240191. The other changes are tests, records and tools/hv4_shakedown_report.py revisions 3 and 4 (#770), which has sha256 f5e62ab57a72ebed0620b36a084893b10030350949204bd20624a78299b438ae at 9ed4c5ff63935696b2a89150e13565e123047754.

R-3. Proof. MiScusi job #1278 at 749a91f on the R2 window. v1 obs off: md5 0ed7819de0b8f6f80c155529f0757dc6 over 3,331 rows, equal to proof (a) 0ed7819de0b8f6f80c155529f0757dc6 over 3,331 rows. v2 obs on: md5 154615662411e8ed43a0c930a6632822 over 3,593 rows, equal to the d0d4ca8 run 154615662411e8ed43a0c930a6632822 over 3,593 rows. Ledger digests: 63bcc8b2b580e0405f17abbbc95ce8f1 = 63bcc8b2b580e0405f17abbbc95ce8f1 and c3bb58d91491575056ff127eb6c5fa04 = c3bb58d91491575056ff127eb6c5fa04. Result: /data/mal/hunt-1008/ops/proofs-774/ (r2_v1off.json, r2_v2on.json). The replay builds the Runner directly and does not run main(). The --register branch is covered by tools/test_hv4_runner.py at 9ed4c5ff63935696b2a89150e13565e123047754.

R-4. Caps. At 9ed4c5ff63935696b2a89150e13565e123047754, hv4_prefetch.HOURLY_BUDGET_V2 is 2,000 and hv4_chain.DEFAULT_DAILY_CALL_CAP is 48,000, so CHECKS-4 change 3 applies. Note N4 is at DESIGN.md sha256 bcd26affadf1c9900852ee9f2f3489b46c418b5ee393f75d154d965ee6e176d8.

R-5. Fresh directory. The stub runs with HV4_SK=/data/mal/arena/_shakedown-hv4-v2-1020, a directory that did not exist before the job. The procedure therefore registers once (it does not take the restart branch), and the registry, ledger and root are fresh.

R-6. Nothing else changes. No check, limit, rule M guard, NOTES-1 item, DESIGN note or verdict rule changes. Rule M applies as written. In a completed window, runner_start_rows_without_a_start_row must be 0, and a nonzero value is FAIL; this file does not excuse it.

R-7. If R-1 to R-5 do not all hold (a different sha, a non-identical proof, a different cap, or a reused directory), this file does not apply and no revision 4 stub verdict is valid at that sha.
