# Arena records, 2026-10-24: H5 Q50 registration (`h5v1q50-boostfloor-a1`; DEC-031 Am.5, QP-RULING-1022-q50, QP-RULING-1022-q50-exec, QP-RULING-1022-q50-P2; DEC-031 Amendment 10, main 024ee71423a8cdeb7a358401b7b30f81a72531d1)

Written 2026-10-24 (UTC) by a worker agent from the manager's copied job outputs only: `/data/mal/hunt-1008/push-1016/hunt29/q50-records/FACTS-1024.md`, the execution sheet `/data/mal/hunt-1008/push-1016/hunt24/l3-boost/Q50-EXEC-1024.md` (sha256 `d1067102de0a4acbfc5b383d25a13cdad202b321733b6ca696e9b0a3ff529b9e`; not in git), and the QP rulings in the same directory. The writer ran nothing on any host and opened no ledger, bots.json, shadow, canary, probe or outcome file. Any value not in those sources is written "not recorded". The PR is opened BEFORE `clock_start_ms` 1792886400000 (2026-10-25T00:00:00Z). After the clock this file may only be ADDED to. Paper only. No book has cleared the promotion gate. Nothing here says any book makes money.

## 1. The line (checklist item 1)
- `bot_id` `h5v1q50-boostfloor-a1`; rule `H5-BOOSTFLOOR-Q50 v1`; rule file `EXP/EXP-024-RULE-Q50.md` sha256 `b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4`; `q_star_sol` 50.
- `frozen_sha` F50 = `a5f413704eb120c8f529101a6186e3a95de7f83e` (DEC-031 Am.5 5.3). Why F50 and not M: the registered `frozen_sha` and the shadow's `git_ref` are F50; `tools/h5_shadow.py` blob `f23cf492` is equal at `29e214aec8e75084f1910a458af3c7d8426751cf` (M); the wrapper is pinned at M, sha `f5a479ad`; QP-RULING-1022-q50-exec item 1 (sha256 `46adb0439cda59b1ac4e8cd1c4d24c189f5662f056c46ca288714009a5d2a3c6`).
- `registered_at_ms` **1792801215395** (G8 output; job `date -u` lines 00:20:11Z start, 00:20:15Z end). `clock_start_ms` 1792886400000 = 2026-10-25T00:00:00Z (1792886400 s, verified by computation in the sheet).
- Host mal-fast-0; sockets 3; endpoints `wss://api.mainnet-beta.solana.com`, `wss://public.rpc.solanavibestation.com`; stake default 0.30 SOL; deciding cell the 1.9 s leg at 0.30 SOL; rent CHARGED deciding, the rent-0 leg report-only (a2 ruling).
- Window [2026-10-25T00Z, 2026-11-06T00Z) = 12 dates; effective end 2026-11-06T00:00Z (arena end-seal).
- **H = 14** (P0 rate 35.333333333333336; H = min(90, max(14, ceil(150/rate)))). **Disclosure: there is no H field in bots.json**; the H pin lives in the notebook and this file.
- G8 printed JSON: `{"clock_start_ms": 1792886400000, "frozen_sha": "a5f413704eb120c8f529101a6186e3a95de7f83e", "q_star_sol": 50.0, "registered": ["h5v1q50-boostfloor-a1"], "registered_at_ms": 1792801215395, "rule": "H5-BOOSTFLOOR-Q50 v1"}`. `bots.json` sha256 after registration `b9eb603e6b3de8e8d01f51aefd4b67efc7d394de0fdde1e4243428ef277c1843` (a hash only).

## 2. Books file (item 2)
- v1 sha256 `aa1cd01f8c2dc2bce9463d8d1c2537868f0531dab472bd25e3e34af5a2831aa4` (printed as `BOOKS_V1_SHA256`).
- v2 sha256 `819c699d74a2fab8b8c67916a5732e54950d158c514d8fa24dabc9b65b7665c0` (printed as `BOOKS_V2_SHA256`), `BOOKS_V2_OK` printed. Verbatim v2:
  ```json
  {"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v2-boostclock-a2", "h5v1q50-boostfloor-a1"]}
  ```
- G8 job text: `/data/mal/hunt-1008/push-1016/hunt24/l3-boost/G8-register-1024.sh`, sha256 `8ea4e1f488cf0f0d204681960cfeb470b44c5d2960a8e12acc639314427ab22c`, verbatim from the sheet's STEP G8 block, submitted inline. The text itself is in the sheet (not in git) and is not copied here.
- G8 job #1417 (`j_mjM1h2TEA6qCzQ`), mal-fast-0, git 29e214ae, 1 GB, 0.1 cpu.

## 3. P0 (item 3)
- Fixed-first notebook (G7a text + QP addendum + manager pre-submit check): **n_dYHDF5CZxV9Kbw**, posted about 2026-10-24T00:12Z (manager's note); the P0 job #1413 was submitted at 1792800742374 ms, after it. The notebook tool does not expose creation instants. n_dYHDF5CZxV9Kbw was created by the manager after a `date -u` reading of 2026-10-24T00:10:59Z, and its write call returned before job #1413 was submitted at 1792800742374 ms (00:12:22.374Z); the submit call was made only after the notebook call completed (sequential tool calls in one session). Exact creation instant: not recorded.
- Job **#1413** (`j_Af3reOXXEdqtjg`), mal-research-0, git 29e214ae, 16 GB, 16 h limit. Finished Sat Oct 24 00:17:51 UTC 2026.
- COUNTER_SHA256 `8c6b490576690193934451af9d6dd4eff3ca649d066553bc172d31f1de2d2446` (checked inside the script; passed). `counter_rc=0`. Output `q50p0.json` sha256 `2d354bcedaeb068a0be138172951eb61ce962a89863c5217e8a74d88964999ae`.
- Files 72; hours_listed 72; hours_match True; hours_without_own_rows []. gap_progs ['pumpfun', 'pumpswap']; unknown_progs [].
- Deciding cell `G0_wsol_band|trig|Q50|strict`: 2026-10-21 46, 2026-10-22 25, 2026-10-23 35; sum 106; mean 35.333333333333336; min day 25.
- Gap counts: all_gap_rows 0, all_gap_ms 0; pumpswap_gap_rows 145; pumpswap_dataloss_ms 97170 (97.17 s; bar 3,600 s).
- AID_VERDICT PASS; the manager's decision: **PASS** (all conditions), H = 14.
- Runtime condition (QP Q4): the 57-file COUNTS run of record was NOT a MiScusi job; it was a background Bash task, started 2026-10-21T17:44:37Z and completed 17:48:17Z (about 220 s; file mtimes 17:44:31Z to 17:48:17Z). The duration therefore comes from the task's start/complete instants and file mtimes, not from MiScusi job metadata. 72/57 x about 278 s is far under the 8 h bar.
- Duplicate notebook entry: manager14 posted a duplicate G7a entry n_5Z8jSUNGcKbisw (about 00:14Z, same text and addendum) and marked it DUPLICATE in n_7AhY2f0NkJtStQ. The record is n_dYHDF5CZxV9Kbw plus job #1413; manager14 submitted no G7 job. manager14 checked at 00:13Z that zstd is present and all 72 hours of 10-21..23 were .zst with no .jsonl or .zst.tmp left.
- Disclosures: day 1 (2026-10-21) is an undercount at the start edge (a pool created before 2026-10-21T00:00Z is not tracked). Day 3 (2026-10-23) is a LOWER BOUND (300 s / 420 s tail cut at 10-24T00; DEC Am.5 1.7). Both are conservative for an "at least" bar.
- **Proxy departure (DEC Am.5 1):** the P0 count is a tape-based proxy, not the shadow's own trigger record. It includes synthetic/unclassified pools (an upper bound), its strict detector is a behavioural proxy, and the bar is the old Am.3 bar for that reason.

### 3a. Job text and addendum
- G7b job text: `/data/mal/hunt-1008/push-1016/hunt24/l3-boost/G7b-p0count-1024.sh`, sha256 `631f3b2f9a52f9129462201f0695a6f7324a60d739957b804bcdcfa94702d071`, extracted verbatim from the sheet's G7b block, run as `sh <that path>`.
- Q6 addendum, appended after the verbatim #790 text in the same notebook entry: "Addendum fixed before the job (QP-RULING-1022-q50-exec): (1) Day 1 is an undercount at the start edge: a pool whose pool_create row is before 2026-10-21T00:00Z is not tracked; conservative for an 'at least' bar, never a reason to pass. (2) Read-out: an hour with no rows of its own counts as a missing hour file; a gap prog other than pumpswap, pumpfun or all, or a non-zero counter exit, is NOT_DECIDABLE. (3) The job waits up to 60 min for all 72 hours to be compacted (.zst present, no .jsonl or .zst.tmp left); an hour still not compacted after that is a missing hour file. All three are stricter than or equal to the text above; nothing is relaxed."
- The verbatim #790 "To be notebooked before the job" text is in the body of PR #790; it is not re-copied here.

## 4. Job ids and instants (item 4)
| step | job | detail |
|---|---|---|
| P0 | #1413 `j_Af3reOXXEdqtjg` | research-0, finished 00:17:51Z |
| STEP 0 / a2 guard health | #1415 | 00:18Z; guard/dead-man runs 10-23 07:10 and 19:10, dead-man 13:10 green; books file `aa1cd01f...` (v1). NOT complete: the earlier 2026-10-22T19:10:05Z guard run was a HALT that ended both a2 books (see section 10, precondition-3 lapse) |
| kill-review line | notebook n_UaV-kYJ16fdpYA | `date -u` 00:19:26Z; job id: not recorded |
| G8 | #1417 `j_mjM1h2TEA6qCzQ` | 00:20:11Z to 00:20:15Z |
| G9a | #1418 (git 136cd98) | 00:20:40Z `{"applied": [], "deadman": "alive", "last_run_utc": "2026-10-23T19:10:05Z"}`; `A3_DEADMAN_RC=0`, `RC=0` |
| shadow | #1419 `j_Ya-YRMDZVfSHxw` | mal-fast-0, git a5f41370, 1.5 GB, 1 cpu, 14 d; started 00:21:03Z |
| successor | #1421 `j_RCCqv7aFc-CqRg` | queued `after` #1419; dir `h5-q50-<date>-succ`; end-seal refusal 1793923200000 |
| G9b | #1422 (git 136cd98) | 2026-10-24T00:22:39Z; RC lines below |
| day-1 dry score | #1424 `j_T40rMm-QoYudPg` | git 29e214ae; sleeps to 2026-10-25T06:30Z |
| daily score loop | #1425 `j_fJG55iBDBkzCbw` | git 29e214ae, 1.5 GB, 0.5 cpu, 14 d |

- Shadow first log line (arena launcher echo): `h5-q50: rule H5-BOOSTFLOOR-Q50 v1, q_star_sol=50, fresh-forward ARENA (v1 330 s exit, bx10 off), out=/home/claude/data/arena/h5-q50-2026-10-24, sockets=3, head=a5f413704eb120c8f529101a6186e3a95de7f83e, dirty=0`. A 429 handshake on the solanavibestation socket at start was logged as a warning. Cgroup `/user.slice/user-1002.slice/user@1002.service/app.slice/miscusi-job-jYaYRMDZVfSHxw.service`, MEMORY_MAX=1610612736.
- G9b printed: `A3_DEADMAN_RC=0`; monitor pins 6 unchanged, boost_enabled=True; HALT: none; `A3_MONITOR_RC=0`; verdict NONE; `A3_CHECK_RC=0`; `A3_APPLY_RC=0`; `RC=0`; `A3_VERDICT NONE run_utc 2026-10-24T00:22:39Z record_run_utc 2026-10-24T00:22:39Z`; `A3_FLAGS 6 pins_changed=halt:0,eval:1 boost_disabled=halt:0,eval:1 boost_share_low=halt:0,eval:1 boost_last_slice_early=halt:0,eval:1 boost_budget_or_slices_changed=halt:0,eval:1 synthetic_share_high=halt:0,eval:1`; `A3_FLAGS_OK`.
- Score loop time limit: the sheet says 15 d; the platform maximum is 14 d; 14 d from 10-24 00:3xZ covers END 2026-11-06T01:00Z (manager's note).
- Slots: #817 (old H5 detector long run, ends 10-26 anyway) was cancelled at about 00:3xZ to free a slot.
- Notebook ids: G7a n_dYHDF5CZxV9Kbw; P0 result n_gYxEhqREpwRApQ; kill-review n_UaV-kYJ16fdpYA; registration n_jBu9N8mUms6PDw; G9a / shadow / G9b / score jobs n_gOJ_A_FuxCIS3g; finding n_5ob4j6rvho4xuQ. Owner approval: n_e4wMMd_5XtLT4A (not opened by the writer).

## 5. Pins (item 5)
- M `29e214aec8e75084f1910a458af3c7d8426751cf` (tooling pin; origin/main had moved to `91544819112d6ec15eae753960e08bfab4be1823` at STEP 0; every Constants file hashes identically at both and the diff of those paths M..main is empty; jobs ran at git_ref 29e214ae). F50 `a5f413704eb120c8f529101a6186e3a95de7f83e`. G `136cd98dd6845e9e60b65153ab0f3c4f31959142`. FA2 `1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde`.
- STEP 0: `git diff --stat 46734be..29e214ae` lists only `ARTIFACTS/lab/arena-records-2026-10-22.md` (+172). a2-path diffs eb8b32d..M and 136cd98..M are empty.

| file | sha256 |
|---|---|
| `scripts/research/l3_count_q50p0.py` | `8c6b490576690193934451af9d6dd4eff3ca649d066553bc172d31f1de2d2446` |
| `EXP/EXP-024-RULE-Q50.md` | `b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4` |
| `tools/arena_h5_score_q50.py` | `f5a479adc0beaf26dbe46620a1fad4fc0864bf4eff72a8eff7521c22dc0b4f61` |
| `tools/arena_h5_score.py` | `dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006` |
| `tools/arena_h5_score_a2.py` | `78e5bdab0fe23d82ed5a21d1fa1fc66f4f550f10004a2df14077e83b48059710` |
| `tools/h5_shadow.py` (same at F50 and M) | `a5a1bdd7d30e21dd40d403826b216ec067e034db42d9e537b839057a6f79b5e0` |
| `tools/a3_halt.py` | `3f09131bdbba20f021819c644692a1eb3959e387c3d89e0a07200d5395488382` |
| `scripts/ops/a3-daily.sh` | `e7b463b4ba7ef1a9a566561c4dc69cf9be60beac5a35da7687d0ea6feee984df` |
| books file v1 / v2 | `aa1cd01f8c2dc2bce9463d8d1c2537868f0531dab472bd25e3e34af5a2831aa4` / `819c699d74a2fab8b8c67916a5732e54950d158c514d8fa24dabc9b65b7665c0` |

- `h5_shadow.py` blob `f23cf492` (equal at M). G6 cover: identity proof test `tools/test_h5_shadow_qstar_identity.py`, blob `cde0131a61117737219a84509190c40664f9565b`, sha256 `cefd13fa1d3b4a0dc00b51332233a3e6841fb4082ff13f745fe64b075be3a171` (DEC Am.5 5.3). Whether the manager ran the test: not recorded.

## 6. QP and reviewer comment ids; rulings (item 6)
| PR | merge | head | QP OK | reviewer |
|---|---|---|---|---|
| #789 | a5f41370 | 10c574f | 6329352988 | 6329449275 |
| #790 | 0ee6cc3b | 3d87f19 | 6329568784 | 6329570189 |
| #791 | 29e214ae | 46734be | 6329681487 | 6329680597 |

QP rulings (same directory as the sheet): QP-RULING-1022-q50.md sha256 `a0dcf39100d30b944544955981e7452e4449d5018a942d30a35a4c3a2057cac7` (G1-G10); QP-RULING-1022-q50-P2.md sha256 `df8517a42d78756a7b217a3b2969bff9c5ecdc0f0c027e2e335bc4e592f75008` (D1-D5); QP-RULING-1022-q50-exec.md sha256 `46adb0439cda59b1ac4e8cd1c4d24c189f5662f056c46ca288714009a5d2a3c6`. Owner approval: notebook n_e4wMMd_5XtLT4A.

## 7. Disclosures D1-D5 (QP-RULING-1022-q50-P2; written as disclosures, not fixes)
- **D1.** A pre-existing full-day v1-shadow vs frozen-s14 residual exists on the exploration day 2026-09-20: 2 pools of 72 (Q*=40) / 138 (Q*=50) s14 triggers. (a) `F9fckcTp...`: a parity-driver window cut, not a shadow defect, verified on that pool alone, whole-day re-run NOT done. (b) `7j99BJ9h...`: a replay-only exit-state fallback to a modelled post-state; the shadow is optimistic by 341.4 lamports on 0.1 SOL; 1 of 71 / 1 of 137 pools; the Q50 count is inferred. Sources: P2-DIAG-1022.md sha256 `7ce6f8f29a35549e8d2930465afabae11bcefda703882e6fc24d822c8adab6ff`; QP P2 addendum sha256 `df8517a42d78756a7b217a3b2969bff9c5ecdc0f0c027e2e335bc4e592f75008`.
- **D2.** The shadow is the same, so D1 applies equally to `h5v1-boostfloor-a2`, `h5v2-boostclock-a2` and `h5v1q50-boostfloor-a1`. No book's code or parameters change. The a2 records may carry it at their next update; it is not called new or fixed.
- **D3.** Every read of any of the three books must print, side by side, the `derived_last_deciding_leg` count, plus the report-only ex-derived total if it exceeds 5 % of attempts. Owed in a merged read tool before the first gate read of any of the three books (`tools/arena_h5_read.py` at 29e214ae is a1-only). Not built here; it does not block registration (QP exec item 9).
- **D4.** `ReplayVsFrozenTests` skips in /data/mal/venv (pandas absent); applies wherever a v1 parity claim is made.
- **D5. Not measured:** the live-feed fallback share and error; any day other than 2026-09-20; the whole-day extended-hours re-run; the Q*=50 fallback count from records. The re-run was not done before the clock; no output sha256 recorded.

## 8. Siblings, multiplicity, winner's curse, memory (item 8)
- Siblings (Am.3 8(e)): `h5v1-boostfloor-a2`, `h5v2-boostclock-a2` and Q50 share the trigger family and entry legs; Q50's pools contain v1's. At a read, the best of the three is in-sample. A Q50 screen pass while v1-a2 fails is selection on a correlated sibling, not new evidence. Every read prints all three side by side with the shared-pool count (counts only).
- N: +1 from the clock (Am.4 2.5). c: a Q50 screen pass enters confirmation automatically (Am.3 item 2) and adds 1 to c. Current values of N and c: not recorded.
- Winner's curse: Q50 is a discarded discovery cell. Discovery found Q*=40 best and monotone; the only edge figure cited for the family (+4.939% per attempt, best of 413 variants, enrichment p 0.1953) is Q40 and in-sample. No Q50 edge exists. Expect a lower per-trade edge than v1. At 1.0 SOL the cited impact (median 5.94%) exceeds the cited Q40 edge.
- fast-0 memory reading before the shadow start: MemAvailable 19,069,300 kB; `user-1002.slice` MemoryCurrent 13,500,489,728 (printed by the shadow command at start). Earlier reading at 00:18Z (#1415): MemAvailable 19,076,660 kB.
- a2 books: Q50 registration changed no file on the a2 path (see section 5 diffs). The a2 books had already ended before their clock (section 10).

## 9. Not measured (item 9)
- The Q50 trigger rate on the shadow's own feed (the P0 proxy is an upper bound on a different feed); any outcome; any P&L.
- No book has cleared the promotion gate. Nothing in this file says any book makes money. This file does not ask anyone to merge it.

## 10. A3 guard fix and redeploy (facts; 2026-10-24, before Q50's clock)
Notebooks: n_5ob4j6rvho4xuQ (finding), n_fF-zfsZwsQyiag (QP ruling); redeploy entry n_vPM3i2qr3yBuog.
- Finding: the 2026-10-22T19:10:05Z A3 guard run was a HALT (`synthetic_share_high`, 8/19). It ended both a2 books before their clock. Q50 was under the same guard.
- QP ruling `/data/mal/hunt-1008/push-1016/hunt29/a3fix/QP-RULING-1024-a3-synthetic.md`, final sha256 `0487fee28c6e36950947877fe0f1397a2b9fdb6e889327318b462b6fda8add29` (with Addenda 1-2).
- PR #840 merged to main `79a93e94464e26d3f7246e637fac0ae58430e043`. DEC-031 Amendment 10 (PR #845) merged to main `024ee71423a8cdeb7a358401b7b30f81a72531d1`.
- Guard pin tag `a3-guard-pin-1024` = `0ec7fae89abe00021871f35258a8bc659b3df6bd` (136cd98 + check tool + its tests); check tool `tools/a3_redeploy_check.py` blob `4cf7e363b2bf9901e11310d101431b31202a43c3`, sha256 `1f84072da673cc451f9e3a6baa8b25d6295b497305ffe7bc38fef42e3a710f70`; tests `tools/test_a3_redeploy_check.py` blob `391c866d127eb57aa3a771a9f5959a99d2932dc2`, sha256 `0dc1f27809831cd0235e83e5d3e3f59cd42a7573c39a1773789326520e94f3fc`; `h5_shadow.py` blob `23b9ccc2562f4cb83113a8b9faa4cb8d9e86907f` (main has `f23cf492`). Tests at the pin on fast-0: job #1442, 168 passed.
- Cancel order on 2026-10-24: queued successor #1332 cancelled first (job record: cancelled event at 1792803425842 ms = 00:57:05.842Z, confirmed cancelled), then loop #1318 (cancelled immediately after, before the #1443 ps check); ps check #1443 at 00:57:12Z showed 0 guard processes; new loop #1446 started 00:57:40Z, successor #1447 queued, both at the pin.
- First OFFICIAL run at 2026-10-24T00:57:40Z: all RCs 0, verdict NONE, `A3_FLAGS` 6 evaluated, none halting, `A3_FLAGS_OK` (jobs #1448, #1449). **It replaces G9b (section 4, run at 136cd98) as Q50's pre-clock green run** (Am.10 item 5(4)).
- a2 books: NOT_DECIDABLE (empty window, ended 2026-10-22T19:10:05Z before their clock), final. a2 shadow #1331, score #1344 and successors were cancelled at 00:50Z.
- **Disclosure (precondition 3 lapse):** Q50-EXEC precondition 3 required the manager to record any a2 HALT before G8. The 2026-10-22T19:10:05Z A3 HALT that ended both a2 books was NOT recorded before G8: Q50 registered at 00:20:15Z; the HALT was found at about 00:45Z (notebook n_5ob4j6rvho4xuQ). The QP A3 ruling says this lapse does not void Q50 by itself. This 2026-10-22T19:10:05Z halt is the second missed or late A3 halt after the 2026-10-20 `pins_changed` flag under the a1 (legacy) rule, whose halt job ran late (DEC-031 Amendment 10 disclosure). The G7a fixed-first entry's exact creation instant is not exposed by the notebook tool; the record states its bound (after 00:10:59Z, before the #1413 submit at 00:12:22.374Z), which QP accepted (comment 6378415838).
- Q50 remains governed by the A3 rule through the books file; any later HALT is not known at writing.
- **DEC-031 Amendment 11 (Q50 read instant counts trades, not attempts), drafted 2026-10-24T17:54:39Z before the clock 2026-10-25T00:00Z** (Q50 audit M1; QP-RULING-1024-q50-m1.md sha256 757e3286…; Q50-AUDIT.md sha256 c5be6d6a…). The read instant is the first 00:00Z boundary B ≤ 2026-11-06T00:00Z with ≥ 100 counted trades (closed, not excluded, not refused) on ≥ 5 dates; else NOT_DECIDABLE at the end-seal. Gate unchanged. The item-1 tool and score loop must be in place before 2026-10-30T00:00Z (owed with audit B1). Merge commit: FILL. No Q50 output, count or P&L was opened.
- DEC-031 Amendment 11: merge commit 0b018311b8f842637451e1d99914fa83eec4f7f2 (GitHub merged_at 2026-10-24T17:55:26Z, before the clock); this line records the merge commit for the dated Am.11 line above (appended as a new line, per quant-proof). Score loop: #1425 (wrapper f5a479ad) was cancelled before its first run and replaced by #1543 at 659f33e8 (wrapper 4ab3e909, Am.11 item-1 tool; scorer dfe7f018 unchanged), started 2026-10-24T18:14Z (notebook n_B2BBQ6shTBFdUw). Process lapse: PR #884 (the Am.11 item-1 tool) was merged with QP OK on its exact head (comment of 2026-10-24T18:13:32Z) but WITHOUT a reviewer verdict comment on the PR (the reviewer's PASS was given only in its reply); Am.11 item 5 requires both, so item 5 counts as met only from the instant the reviewer's verdict is posted on #884 (recorded late; instant FILL). No Q50 read can occur before 2026-10-30T00:00Z, so no read is affected.
- **Disclosure, written 2026-10-25T06:40:09Z (manager15; quant-proof check of the day-1 dry score, counts only).** Day-1 dry score (#1424, 2026-10-25T06:30Z, --dry-run, wrapper f5a479ad): triggers 10, refused 6 (all `bad_intent:base_breaks_unresolved_settled`), attempts 4, counted 4, excluded 0 (notebook n_kUNsdWW3WzsQiQ). A refusal share near one half matches the a1 books under DEC-031 Amendment 1 (12 of 23). P0 (35.33 triggers a day) did not model refusals, so refusals explain the gap between P0 and the counted rate. Refusals are decided before any outcome. Missed prints rise with feed load, so kept trades lean toward quieter pools (Amendment 1 bias note): the deciding set is shifted, sign unknown, not only thinned. The scorer's phrase "Attempt definition amended after the clock (DEC-031 Amendment 1 …)" is inaccurate for this book: Amendment 1 (2026-10-19) predates Q50's clock (2026-10-25T00:00Z); for Q50 the refusal rule was in force from the start. Pace: at about 15 counted a day the Am.11 read lands around 2026-11-01; below about 9 a day there is no read by 2026-11-06. No rule changes after the clock. No book has cleared the promotion gate.
