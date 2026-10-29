# MAL manager handoff: manager17 → next manager (written 2026-10-29T03:1xZ, from `date -u` 03:07Z)

Read this file first. Then read the last END block of the manager17 state log, at /data/mal/hunt-1008/ops/manager17/state-push-1026-manager17.md; the memory note `push-1026-manager17` points to it. After that, read the newest MiScusi notebook entries.

No book has cleared the promotion gate. Profit so far is 0, and no real SOL is being traded.

## 0. Owner direction (current)
- **Goals (the owner's targets, not evidence):** reach $20/day first, then stack ("+1 SOL, then 40, then 60…"), with ~$200/day as the longer goal; the owner accepts that November is unlikely. Keep looking for ideas even after the first profit. The gate does not change.
- **Push and pacing:** the owner (10-27) asked to expand on Q50 and hedge it, keep stacking small strategies, and look for structural edges. On 10-27 at ~22Z the owner's usage was at 83%, so they said to "chill" until the next manager on Thursday 10-29. The next push is yours.
- **Approvals on record:**
  - **Q50 C0 go-live (10-27, "Pre approved.", n_VDvEvazDU9jzYw):** applies only if Q50's read PASSes and the in-band restatement PASSes and QP gives an OK. Then Helm may create the canary key and write LIVE_OK/TIER=C0 at the manager's signal, and the owner funds 0.5 SOL. C0 limits only. **The owner must relay this to Helm in their own words**, because Helm acts only on Vaan's word. This approval does not cover C1/C2, s1, Q60 or any other book.
  - **10-28 (n_8Yfl--RzLCx2Dw):** API spend past 11-11 is OK. $192 is left before 11-11, after which the cap is $200 per month; the owner wants the $192 used before the reset. HV6's extra Helius credits are OK. Goal: **"really good self-improving general trading bots"**. API spend goes to the Haiku TRADER only (its decisions and their backtests), never to research or agents.
  - Earlier lines remain: HV6 ≤3 USD/day (K1), HV7 ≤0.30 USD/day (q_6_kzYJva9XXuYQ), and the 10-23 standing C0 OK.
- **Hand-offs:** a MiScusi hand-off notice normally means COMPACT. This one is a real hand-off, because the owner planned the next push "with the next manager".

## 1. Books and clocks (all paper)
| Book | State | Next step |
|---|---|---|
| **Q50** `h5v1q50-boostfloor-a1` (frozen a5f41370; clock 10-25T00Z; end-seal 11-06T00Z) | Shadow #1419, then #1421; daily score #1543 at 00:20Z. | **Read:** DEC-031 Am.11 item 1, at the first 00:00Z boundary B ≤ 2026-11-06T00Z with ≥100 counted trades on ≥5 distinct UTC decision dates, **where every attempt decided before B has closed or been excluded**. The earliest B is 10-30T00Z, and the instant comes only from the count tool's write-once file. **If no such B exists by the end-seal, the book ENDS NOT_DECIDABLE** (no early read, no extension). Q50's read and legs run from **a31d9864829d1f0645fef8be095eb8df575c2d00** with the books v2 copy (sha 819c699d…), per QP on #1065. Then: in-band restatement (`tools/arena_h5_inband.py`, #1054; an in-band FAIL blocks C0), QP, then the DEC-033 stage-2 copy, whose merge sha is X. |
| **Q50 successor s1** `h5v1q50-boostfloor-s1` | Committed (DEC-031 Am.17 #1058, Am.18 #1057; note EXP/EXP-024-note-s1-successor.md, blob 7015abc9). Paper code merged (#1065). | Clock 11-06T00Z (slip goes to 11-07). Register 11-04 via `scripts/ops/q50s1-register.sh`, which needs `--preclock-notebook-ref`. Shadow start 11-05 20:00–22:30Z via `scripts/ops/q50s1-shadow-start.sh` with a per-process MemoryMax. QP conditions on #1065: run the 7 test modules on the fast-0 executor venv (solders) before registration and by 11-11T00Z. Run `q50s1-shadow-start.sh --check` on fast-0 **by 11-04** (QP C2). Any allowlist for uncapped units such as miscusi-agent.service needs a QP ruling before 11-05T20Z. Today --check stops at "no s1 line" before the memory check (#1783). Live code C6/C9 stays DEFERRED until Q50's X is installed or Q50 ends with no PASS. |
| **Q60 sibling** `h5v1q60-boostfloor-a1` | Decision merged (DEC-031 Am.19 #1064; EXP/EXP-024-RULE-Q60.md). Clock 2026-11-07T00Z, H 14. | Owed before the clock: tag `q60-base-1` (S* + Q*=60 level + the compare_frozen refuse fix, tag only); proofs (i)–(v), where Run C is a fresh s14 at DS=60 on 09-20 via parity_v2; the note PR (own QP and reviewer); P0; smoke; registration. The memory-fit check must cover every H5 shadow and the smoke. Build it as one more row in s1's tools. |
| **HV4** (9 Haiku bots) | Runner #1561, then #1562. NOTIFICATIONS ONLY. **Never call `miscusi_job_status` on #1561/#1562** (it is a seal view). | Read from 10-30 at the earliest. The attempts-vs-trades read rule needs its own QP ruling first. |
| **HV6** | **SLIPPED past 10-30** (n_gRpxhSP-5yLKTg). Smoke #1806 FAILED: chunk p95 41 s; refusals 34%; parse_error about 60%, because max_tokens 400 truncates 4-card replies. | Three fixes at a new final sha: (A) HV6-only entry max_tokens of about 1200; (B) concurrent arm calls, plus fitting the board into 12 calls/h; (C) a paced prefetch subclass, which can go to 2,000/h now that the owner has OKed the credits. Then the proof chain, then a smoke that PASSes, then the next rolling clock. The producer #1748 covers until 11-11T00:15Z, so a later clock needs a successor. Diagnosis: scratchpad `hv6-smoke-diag.md` (summarised in n_gRpxhSP-5yLKTg). |
| **HV7** (Haiku rank) | Proofs done: RESULT_UNIT 329/329; EQUIV_1D PASS (n_PwyBFGvsZH5q4A); F_3D PASS (n_5mn5ovgPxYL_Hw); E1 drill #1771 PASS. Real-model smoke #1779 (smk-1027b, pin n_ARQhZdnJJ6_Msg) is running and ends ~10-29T05–06Z. | Then: fill the #1019 pack (RESULT_SMOKE, HV7_FINAL_SHA = c8fcf761), get QP and reviewer on the pack, merge DEC-031 Am.16, register for **T0 11-01**. **Start the runner in [10-31T22:05Z, 22:45Z]**, never inside [H:48, H:02). Run the pre-T0 counts check by 11-01T01:00Z. |
| **DBC m10** | Forward window [10-25, 11-01T00:10Z). Read chain submitted (§5). | ONE read via job #1804, a confirmation only. If it PASSes, P18 B' (DEC-031 Am.20 #1066): P0 = the read's attempts_by_day, clock 11-04T00Z, expiry 11-06T00Z. P18 code is ready: #1067 and the wire md5 PASS #1805. Owed for P18: fast-0 staging (venv, worktree, gate jobs J1–J4 per `docs/research/p18-prebuild/gate_jobs.md`), probe, dry score, registration pack and guard. |

## 2. Seals (list all of them in every tape-touching brief)
**The binding list is /data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md (sha256 prefix 823cc062).** Paste it verbatim into every tape-touching brief. It includes the HV4 no-retune rule, Q50 #817 and the canary, the Look-2 scope, D1 SPENT scope, the arena-join and in-sample rules, ledger D2/D3 (docs/HOLDOUT_LEDGER.md), and walk-2. The bullets below are a summary plus this session's additions, not a replacement.
- **HV4:** outputs, ledgers and decision rows; the status view and log tail of #1561/#1562.
- **Q50 and s1:** outputs, shadow dirs, score and count files, and bots.json contents. **Never call `miscusi_job_status` on #1419/#1421/#1543.** For job limits, use `miscusi_job_list`. A contact on 10-28 is disclosed in n_RKchrIJwFfAT5A for QP to rule on.
- **EXP-024 Look-2:** H5-family pools to 2026-11-14. Declared observations: Am.10 note (Q50 live), s1 note (blob 7015abc9), and the Q60 note when it lands.
- **DBC:** the forward window and fwdv1/. Never run any #890 test file, nor `tools.test_dbc_m10_read_hardening`. LaunchLab [10-17, 10-24) is SPENT.
- **Other:**
  - /data/mal/meta-capture/: never list or read it.
  - fresh-0802 is the last unspent reserve; fresh-0828 belongs to EXP-028.
  - Arena tape: counts only, under QP rulings.
  - HV replay outputs: md5s and field names only.

## 3. Live path (Q50 C0)
- **Pre-stage PASS (HELM-REPLY-1028-1):** reinstall at Y=c8fcf761 plus a 1 h keyless dry run on fast-0; all 6 criteria passed. Units are stopped and disabled, and there is no key.
- **BLOCKER (n_-pbYbpY143vu4g):** the executor holds buys for ≥600 s on EVERY feed gap row (h5_arena_executor.py:1227-1229). The pre-stage hour had 149 feed_reconnect_redundant plus 47 feed_gap rows, so a live C0 would almost never buy.
  - Likely cause: public ws churn on fast-0's IP. I cancelled the P45 meme.fun recorder (#1667/#1668) at about 17:20Z on 10-28.
  - **HELM-ASK-1028-1** asks for a repeat 1 h keyless dry run on 10-29 to re-measure; the reply will be HELM-REPLY-1029-1.
  - If the gap rate stays high, the next step is a QP-reviewed restart of the Q50 shadow on other endpoints or fewer sockets. That writes a feed_restart and departs from the registered endpoints, so it is a records change. The other option is to move the fast listener and tip-tape load off that IP.
  - The same gaps may also cut Q50's counted paper attempts, which raises the NOT_DECIDABLE risk.
- **DEC-033 stage order (QP-PRERULING-1027-xorder):**
  - Stage 2 is the copy PR, whose merge is X: read figures, in-band, seal statement, bare `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1`.
  - Stage 3 is records PR R, after X: POSTX tokens.
  - Use `scripts/ops/q50-canary-record-check.sh`.
  - At X: manifest, md5 replay (`q50-xtime-md5-job.sh X`, which must reproduce #1481: 137/137 and 101/101), N4 re-run (#1758 is a disclosure only), Helm install plus 1 h dry run, and the webhook install confirmation.
- **A3 guard:** #1446 is extended to 11-07T00:57Z. Dead-man #1319 runs to about 11-04T21:13Z, then #1333 takes over.

## 4. Merged this session (10-27/28)
| PR | What it is |
|---|---|
| #1054 | in-band tool |
| #1055 | P95 structural census |
| #1056 | Q50 C0 stage-1 records + record check |
| #1057 | s1 design |
| #1058 | s1 policy |
| #1059 / #1061 / #1063 | P98 synthetic-completer counter. V1a FAILED, so no count was run and the lane is closed. |
| #1060 | P96 Q50 forward veto prereg. Earliest run ~11-20; the scored-rows export PR is still owed. |
| #1062 | P97: callout-follow is NOT_TESTABLE; holder rewards are real but weak |
| #1064 | Q60 decision |
| #1065 | s1 paper code |
| #1066 | P18 B' |
| #1067 | P18 prebuild |
| #1068 | DBC REGISTRATION addendum 2c |
| #1069 | P91 KILL |

**Ideas closed:** P29 keeper-absorb (KILL, priced 10-26), P91 cost-gated kNN (KILL), P98 synthetic completer (V1a FAIL), callout-follow (no feed). Holder-carry is weak: the P70 drift per token held (about −24.3%/day) is about 24–28× the carry at turnover 1. The structural census found BOOST to be the only large protocol-funded payment.

## 5. Fixed instants: host-side MiScusi jobs (they run without a session)
- **Daily:**
  - 00:05 HV4 reviewers; 00:10/00:15/23:50 digests; 00:20 Q50 score; 01:10 HV4 D3 table; 01:30 HV4 instant #1591;
  - 07:10/19:10 A3 #1446; 07:40 radar #1577, then #1647; 13:10 dead-man.
- **DBC chain** (all at 304bb9cd on research-0):
  - Gates g2 #1797 (10-29T06Z), g3 #1799, g4 #1801.
  - G passes #1798/#1800/#1802 are independent, each with a bounded 100 min wait.
  - G5 FINAL #1803 runs after F #1546, then **READ #1804** (Option A, 11 GB) runs after F and #1803.
  - G pass 1 #1796 finished rc 0 with no gaps. #1785–#1793 were cancelled and never ran.
  - If #1803 is killed, #1804 never starts; resubmit both.
- **Feed handover:** H' #1652 is armed and sleeps until 11-07T11:50Z (n_NglnQrS8iEMa1Q); post-check C #1585.
- **Meta-capture:** #1814, restarted 10-28 after an RSS guard exit; cap 2500 MB. It grows slowly, so restart it or fix the leak if it nears the cap.
- **Walk 2:** #1036, then gate #1289 (11-03), then #1290.

## 6. Session crons (recreate them if lost)
The full JSON is at /data/mal/hunt-1008/ops/manager16-session-crons-1026.json plus recurring-crons-1011.json. The recurring ones:
- hourly push check at :23;
- disk guard every 2 h at :37;
- Helm channel at :47;
- daily: digest copy 00:23, HV4 L8 01:21, tip-tape archive 03:23, A3 monitor 06:41, housekeeping 09:13, H5 health 12:17.

One-shots to recreate:
- 10-31 21:07Z: check research-0 room for DBC READ #1804, which needs about 11 GB;
- 11-01 04:13Z: DBC read watch.

## 7. Owed, by date
- **10-29:**
  - The HV7 smoke result (#1779): fill and review the #1019 pack, register for 11-01. The runner starts on 10-31 in [22:05, 22:45Z].
  - Helm's repeat dry run (feed gaps).
  - Start the HV6 fix work (A/B/C) at a new sha.
  - Plan the use of the $192 API budget, and the self-improving Haiku trader direction (see §8).
- **10-30T00Z onward:** the Q50 read and the HV4 read (the HV4 attempts rule needs a QP ruling first).
- **10-31:**
  - Q60 tag and proofs (clock 11-07).
  - s1 fast-0 tests.
  - Memory room for DBC READ.
- **11-01:** DBC READ #1804, then P18 B' (11-04) if it PASSes. HV7 T0.
- **11-04/05:** s1 registration and its shadow (11-05 20:00–22:30Z).
- **Records owed:**
  - LAB_STATE "Now" lines: HV6 slip, P91 KILL, feed hand-off re-arm, meta-capture restart.
  - The old open lane PRs (#944–#993): triage, close or merge.

## 8. Lessons (also in memory)
- Run `date -u` before writing any timestamp. I guessed times twice this session.
- **Smoke tools:** HV7's smoke tool was derived from HV6's. When one crashes, check its siblings.
- **Production-path tests:** smoke and real-path tools must be tested with `MAL_ARENA_TEST` unset. Two setup crashes, #1776 and #1769, came from test-only register overrides.
- **Parallel agents:** they clobbered a shared scratchpad comment file, so a wrong verdict body got posted on #1059. Brief agents to use `mktemp`, post with `-R vaanai/MAL`, and paste the grep check. Check that each verdict body matches its PR.
- **Never `miscusi_job_status` sealed jobs** (feedback-no-status-sealed-jobs).
- **Serial job chains:** in a chain, one killed link blocks every later step. Use bounded waits, and hang each daily pass off a gate instead of off the previous pass.
- **Owner note on the Haiku direction (judgment, not evidence):** the evidence is that Haiku is weak as a raw ranker (P92 IC 0.02723 vs kNN 0.296; P93 AUC 0.4476, CI90 [0.3384, 0.5499]). A "self-improving general bot" may be more promising if it learns when to act on candidate structural signals than if it tries to pick winners. Candidates: the BOOST floor (Q50, in-sample only, unproven forward) and the avoid-bad-pools veto (P96, pre-registered, untested). Neither has cleared the gate.
