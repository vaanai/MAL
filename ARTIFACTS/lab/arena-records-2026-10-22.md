# Arena records, 2026-10-22: H5 a2 registration (DEC-031 §1.2, Am.4 items 1.10 / 1.13 / 2, QP-RULING-1022-a2pack (5))

DRAFT. Written 2026-10-21T21:39Z (`date -u`) by a builder agent, desk only: nothing was run on any host, nothing registered, no job submitted. No ledger, bots.json, shadow, outcome or notebook file was opened. Cells marked `<FILL 10-22>` are filled by the manager from the job logs after the step they name, and the PR is pushed (open) BEFORE 2026-10-23T00:00:00Z (`clock_start_ms` 1792713600000). After the clock this file may only ADD post-clock facts; nothing in it may change (QP (5)). Nothing here says any book makes money, and no book has cleared the promotion gate. Paper only.

Books: `h5v1-boostfloor-a2` and `h5v2-boostclock-a2`, new `bot_id`s at the same rule and frozen sha as the ended a1 pair (arena-records-2026-10-18.md section 6). Execution sheet: `/data/mal/hunt-1008/push-1016/hunt24/h5-canary/A2-EXEC-1022.md` (not in git; the commands are quoted in section 3 and the job ids in section 12).

## 1. Books file (Am.4 item 1.10)
- Path (mal-fast-0): `$HOME/data/arena/a3-books.json`. The registry is mal-fast-0 `$HOME/data/arena/bots.json`, as for a1 (DEC-031 §1.2 names `/data/mal/arena/bots.json`; the real path follows the a1 precedent, QP (1)e).
- **v0 (now; job #1317; legacy ids only, new_rule list empty):** verbatim `{"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"], "new_rule_bot_ids": []}`. sha256 `b406b3aa...` (first 8 hex, from the manager log; full value `<FILL 10-22: full sha256 from the #1317 log>`).
- **v1 (written in the same job as the registration, via tmp + mv):** verbatim
  ```json
  {"legacy_bot_ids": ["h5v1-boostfloor-a1", "h5v2-boostclock-a1"], "new_rule_bot_ids": ["h5v1-boostfloor-a2", "h5v2-boostclock-a2"]}
  ```
  sha256 `<FILL 10-22: BOOKS_V1_SHA256 from the registration job log>`.
- Validation: `tools/a3_halt.py` refuses a listed id that is not registered and any registered `h5*` book in neither list, on every `deadman` and `apply` call. The a1 ids stay listed as legacy (ended, never removed).

## 2. Pins
- **H pin:** `H = <FILL 10-22: from the P0 average rate; H = min(90, max(14, ceil(150 / rate)))>` (Am.4 item 2.2). Effective end 2026-11-06T00:00:00Z, the arena end-seal, whatever H is: H >= 14 days for every rate and clock + 14 days = 2026-11-06T00:00Z (QP (6)). **Disclosure:** `register_books` has no H field, so the `bots.json` lines carry no H. QP-RULING-1022-a2pack (6) accepts the notebook-plus-records pin for a2. A later book whose H could end before its seal needs a registry field first (scorer PR or DEC note).
- **Code pins** (git, origin/main eb8b32deef236257e7b5c9dca62a77a5c6afc455; sha256 of the file):

  | file | sha256 |
  |---|---|
  | `tools/arena_h5_score.py` (unmodified, the registration module and the scorer) | `dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006` |
  | `tools/arena_h5_score_a2.py` (the a2 scoring wrapper) | `78e5bdab0fe23d82ed5a21d1fa1fc66f4f550f10004a2df14077e83b48059710` |
  | `tools/arena_h5_window_count.py` (counts tool, P0 and the a1 sub-window) | `5499965e8c42eeaa1d7d36e7be4bac6a67c5e99184f01a825982adeb15857fcc` |
  | `tools/a3_halt.py` | `3f09131bdbba20f021819c644692a1eb3959e387c3d89e0a07200d5395488382` |
  | `scripts/ops/a3-daily.sh` | `e7b463b4ba7ef1a9a566561c4dc69cf9be60beac5a35da7687d0ea6feee984df` |
  | `scripts/research/arena-h5.sh` | `b51b1d1057d8745b6891b540bc73c1105c270eb58eed651e1c13de5ca23b8a73` |
  | `tools/h5_shadow.py` at frozen 1f7a89c (blob `23b9ccc2562f4cb83113a8b9faa4cb8d9e86907f`) | `e91f3f84dbb9a162b6cdb456bac40f3902d73db775924f5df834cf536401abb0` |

  `frozen_sha` `1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde` for both books (same as a1). The blob pin of `tools/h5_shadow.py` is by text (EXP-024 Note under Am.9 (e), QP (5)): `git rev-parse 1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde:tools/h5_shadow.py` prints `23b9ccc2562f4cb83113a8b9faa4cb8d9e86907f`.
- **Wrapper sha256 `78e5bdab...` (full value above)** goes in with this record (QP (2) iv). Whitelist: `score` only; `register`, `halt`, `refit` exit 2. It drops, for `h5v2-boostclock-a2` only, every pv trigger whose `s0_t_recv_ms` is missing, not an integer, or earlier than `clock_start_ms` + 60,000 ms (counted as `dropped_s0_before_clock`, plus `dropped_decided_before_clock` and `dropped_after_window`).
- **StackConfig pin (Am.2 item 8). Reported as the code shows it, not resolved:** the H5 a2 books are scored by `tools/arena_h5_cost.py` (`STACK_ID` `DEC-031-rev2`), not by `tools/arena_paper.py`'s `StackConfig`; neither `tools/arena_h5_score.py` nor `tools/h5_shadow.py` constructs a `StackConfig` (grep at origin/main eb8b32d and, for the shadow, at 1f7a89c). The pinned constants that play the role of the `StackConfig` keys, from `tools/arena_h5_cost.py` at eb8b32d:

  | Am.2 item 8 key | value in the H5 cost module | note |
  |---|---|---|
  | `rent` | `RENT` = 2,039,280 lamports per filled trip; `RENT_STRANDED` = 2,039,280 | **not 0.** Am.2 item 3 says each covered host passes `rent=0` in its `StackConfig`; the H5 host has no `StackConfig`. The scorer docstring (`tools/arena_h5_score.py:13`) says rent 2,039,280. See open question 1. |
  | `e1_floor_per_005` | `E1_FIXED_PER_REF` 384,022 at `E1_REF_STAKE` 50,000,000 (0.05 SOL) | equal to the Am.2 value |
  | `strand_rule` | `STRANDS = ("dec031", "sc")`; every leg is priced under both (the "both" semantics) | |
  | `k_rounding`, `feed_delay_slots` | no such key: H5 measures delay per trigger on the chain clock (arena-records-2026-10-18 section 3) | |
  | `gap_slots` 150, `gap_silence_ms` 60000 | no such key: the shadow's own `gap` records (`slot_jump`, `silence`, `feed_restart`, `socket_reconnect`) feed the counts tool's gap measure | |
  | other | `SEND` 505,000; `H_IN` 0.002608; `H_OUT` 0.0016; `GUARD` 1.15; `FAIL_FLAT` 0.15; `PRESS_SLOPE_SLOT` 0.8; `PRESS_SLOPE_SOL` 0.35; deciding stake 0.30 SOL, cell 1p9 | |

  The Am.2 item 8 read check (compare `StackConfig.as_dict()` on every engine start row) has no H5 counterpart. **No claim is made that Am.2 item 8 is satisfied; this is the pin as found.** QP rules (open question 1).
- **V-band check (Am.2 item 6, Am.3), as `path:line@frozen_sha`:**
  - `tools/h5_shadow.py:194-195@1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde`: `V_LO = 17.5e9`, `V_HI = 17.7e9` (lamports).
  - `tools/h5_shadow.py:1400@1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde`: `if not (V_LO <= v_raw <= V_HI)` on the first print's `virtual_quote_reserves`, counter `rejected_v_range`.
  - `tools/h5_shadow.py:1410-1411@1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde`: `if ann is not None and ann[3] is not None and ann[3] != WSOL_MINT:` counter `rejected_quote_mint`.
  - **Deviation from the DEC wording, disclosed:** Am.2 item 2 / item 6 say `quote_mint == WSOL` is "read before the V test". In the frozen code the V test (line 1400) runs BEFORE the quote_mint test (line 1410), by design ("V range first", comment at line 1400), and the quote_mint test applies only when an announce record exists (`ann[3] is not None`); an unannounced pool is rejected earlier at `rejected_unannounced` when `require_announce` is set. The order does not change which pools are admitted (a pool must pass both), only which counter a rejection lands in. Not re-run or re-verified here.

## 3. Registration (to fill after the step; Am.3 item 1, §1.2)
- Registration job: `<FILL 10-22: job id>`, mal-fast-0, git_ref eb8b32deef236257e7b5c9dca62a77a5c6afc455, in-process `tools.arena_h5_score.register_books(..., generation="a2")` on the UNMODIFIED module (QP (1): accepted; no `-I`; asserts that `BOTS` is exactly the two a1 ids before and after the call, and that the returned keys are exactly the two a2 ids; `python -c` from the pinned tree). Never from the wrapper.
- Arguments copied from the a1 registration record (arena-records-2026-10-18 section 5): `frozen_sha` 1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde; `sockets` 3; `endpoints` `wss://api.mainnet-beta.solana.com` and `wss://public.rpc.solanavibestation.com`; `host` mal-fast-0; `stake` default (0.30); `clock_start_ms` 1792713600000.
- Printed line: `<FILL 10-22: the registered / clock_start_ms / registered_at_ms JSON line>`. `registered_at_ms` for `h5v1-boostfloor-a2` `<FILL 10-22>`, for `h5v2-boostclock-a2` `<FILL 10-22>`. Registered at `<FILL 10-22: UTC instant>`, on 2026-10-22 UTC, so the clock is the first 00:00Z after the record (Am.3 item 1).
- Lines (what `register_books` writes; values copied from the printed JSON and the a1 precedent, never from reading bots.json): rule `H5-BOOSTFLOOR v1`; `exit_rule` v1 (330 s) for `h5v1-boostfloor-a2`, v2 (bx10) with `freeze_sha256` `5368cc7b757a2f7a6cb064c71193b4e0cd740583f7348a60cb9a864401c190e0` for `h5v2-boostclock-a2`; `deciding_stake_sol` 0.30; `deciding_cell` 1p9; `stack` DEC-031-rev2; scorer `tools/arena_h5_score.py`; cost module `tools/arena_h5_cost.py`; `window_end_ms` null and `stopped_ms` null at registration.
- Registry sha256 after registration: `<FILL 10-22: not computed unless the job prints it; leave UNKNOWN if not printed>`.
- Shadow job (started after `registered_at_ms`): `<FILL 10-22: job id>` at git_ref 1f7a89cdf02ff46c3e33b9dcc88531c75d2b6cde, dir `$HOME/data/arena/h5-2026-10-22`, first log line `<FILL 10-22: head= and dirty= from the log>`.
- **One a2 dir.** The a2 shadow dir is the only dir ever passed to the a2 scorer (`--shadow-dir $HOME/data/arena/h5-2026-10-22`; a successor dir, if the 13-day limit forces one, is added and named in the notebook). The a1 dir `h5-2026-10-18` is never passed to the a2 scorer: the scorer would refuse it (`started_before_registration`), and the a2 books do not take a1's attempts, ledger or dir (QP #785 note).
- a2 is NOT a continuation of a1: a new `bot_id`, adds 2 to N (Am.4 item 2.5), discloses the a1 end (A3 `pins_changed` 2026-10-20T07:11:27Z, window 3 dates < 5, NOT_DECIDABLE, FINAL; arena-records-2026-10-18 section 6, job #1299, notebook n_h2cG8iJoOrg1OQ). The a1 lines stay in `bots.json`, ended, and count in N at every later read.
- Kill-review-week line (notebook, written before registration): `<FILL 10-22: notebook id and `date -u`>`; no kill-review week in force in [2026-10-22, 2026-10-30] (only 2026-10-05T05:00:00Z, past).

## 4. P0 (Am.4 item 2.1; QP (6)); fixed-first before the job
- Fixed-first notebook entry n_LxvmyBBeNbnILw, 2026-10-21T21:28:07Z (from the manager log; the entry text is the QP rule in `P0-RULE-a2-QP.txt`). Window: UTC days 2026-10-19, 2026-10-20, 2026-10-21 = [1792368000000, 1792627200000), dir `h5-2026-10-18` only, `mal-fast-0 $HOME/data/arena`; no other window may be tried after a failure.
- Job: #1322, mal-fast-0, submitted to run at or after 2026-10-22T00:10:00Z, counts tool `tools/arena_h5_window_count.py` at 3f3aea76193accaa9e8902397bba127dbb3b4aca (sha256 `5499965e8c42eeaa1d7d36e7be4bac6a67c5e99184f01a825982adeb15857fcc`).
- **P0 result placeholder:** attempts by date `<FILL 10-22: 10-19 / 10-20 / 10-21>`; average per day `<FILL 10-22>`; feed gaps by date and `data_loss_ms` `<FILL 10-22>`; reconcile vs `arena_h5_score score --dry-run` (a1, [1792303200000, 1792480287000)) `<FILL 10-22: EQUAL | DIFFER>`; verdict `<FILL 10-22: PASS | NOT_DECIDABLE | DROP>`; output sha256 of p0.json / ref.json / a1sub.json / score-dry.json `<FILL 10-22>`; notebook id `<FILL 10-22>`. PASS rule (verbatim in the notebook): reconcile EQUAL; average >= 2.0 attempts/day and no date at 0 (a lower bound passes only if it clears this); no hard gap; `data_loss_ms` <= 3,600,000. Registration only on PASS.

## 5. a1 post-redeploy sub-window count (owed by arena-records-2026-10-18 section 6; QP repin Q3 item 5)
- Counts only, `[2026-10-19T15:46:16Z, 2026-10-20T07:11:27Z)` = `[1792424776000, 1792480287000)`, `h5v1-boostfloor-a1` (the a1 pair share counts): attempts `<FILL 10-22>`; refused `<FILL 10-22>`; per UTC date `<FILL 10-22>`; unclassified-line counts and whether it is a LOWER BOUND `<FILL 10-22>`; source: job #1322 section "A1 SUBWINDOW", sha256 `<FILL 10-22>`. Reported as counts, as a lower bound if the tool exits 3. No outcome, fill, exit or P&L was read.

## 6. Truncated-log audit pre-declaration (Am.4 item 1.13 / repin Q2; QP (8)); outcome-blind, before the a2 clock
"Before any outcome is opened, each a2 read runs a counts-only audit listing the counted attempts whose pool had any successful transaction with `Log truncated` between entry and exit (`getSignaturesForAddress(pool)` and `getTransaction` meta). If MORE THAN 5% of counted attempts are affected (the exclusion cap's level) the read is NOT_DECIDABLE. Otherwise the count is disclosed. The audit does not exclude attempts from the count." The 5% threshold is fixed and is not re-opened at the read.
- The rule is merged DEC text (DEC-031 Am.4 item 1.13, 136cd98dd6845e9e60b65153ab0f3c4f31959142). The audit TOOL is owed before the a2 read instant (merged, counts-only, QP OK, run before any outcome is opened); it does not exist in the repo at eb8b32d. If it does not exist at the read instant, the read waits (QP (8)).
- Disclosures that go with the registration (review doc `ARTIFACTS/lab/a3-repin-2026-10-21-program-upgrade-review.md`, post sample A+B+C, 23,471 txs): WSOL-only PumpSwap txs with truncated logs 209/5,771 = 3.62% post vs 11/2,723 = 0.40% pre; 17/5,579 = 0.30% post without the one pool; one-pool one-hour episode (384 CPI-only WSOL PumpSwap SellEvents in pool `34tiphuqXUgkH9c2KCrFqCiyuhz58HEbhQBk61ub46bU`; 384/3,157 post vs 0/1,040 pre); `records_from_logs` cannot see CPI-only events.

## 7. Mixed-tx table (review doc section 8(c), post sample A+B+C, 23,471 txs; QP (9): cite as the review doc's table)

| class (post sample A+B+C) | events | trailing u64 non-zero |
| --- | --- | --- |
| PumpSwap events, WSOL-only txs | 6,559 | 0 |
| pump TradeEvents, event quote_mint = SOL (zero pubkey) | 2,879 | 0 |
| pump TradeEvents, other event quote_mint | 646 | 64 |
| PumpSwap events, non-WSOL-only txs | 5,269 | 63 |
| PumpSwap events, mixed txs (both) | 38 | 2 |
| PumpSwap events, txs with no buy/sell instruction (BOOST slices etc.) | 179 | 2 |

Pre sample (15:46Z to 17:47Z): 0 non-zero on all classes. The 2 non-zero PumpSwap events labelled WSOL in the pre-count by instruction accounts are both in mixed txs (attribution ambiguous). The QP repin ruling's own split (A and P only, 124 of the PR's 131 non-zero): 0 on WSOL-only txs, 62 non-WSOL-only, 2 mixed, 1 no-amm-trade; all 59 non-zero pump TradeEvents have a non-SOL quote_mint. Source file: the review doc; QP repin ruling (`qp-ruling-1021-repin.md`) carries only the "2 on mixed txs" sentence, and the QP a2-pack ruling (9) confirms this is the table the repin addendum at 10bd425 relied on.

## 8. The missing 2026-10-21 daily A3 line (QP-RULING-1021-a3 C6, QP (5): explain and restore, or "cause unexplained")
- Observed (QP-RULING-1021-a3, 2026-10-21T17:52Z): `daily.jsonl` (research-0 `/data/mal/structure-monitor/daily.jsonl`) had no line after 2026-10-20T07:11:27Z (mtime 2026-10-20 07:14); the 10-21 daily A3 run was not in the official file. A catch-up run was job #1300 (manager log).
- **Cause: UNKNOWN.** No record or notebook text found in the repo or in `/data/mal/hunt-1008/ops/manager13/push-1021-manager13.md` names a cause. The only nearby facts are that the research-0 daily was a session-driven run, that the manager's session crons were re-created on 10-21 (log line "Session crons re-created"), and that CLAUDE.md names lost session crons as the cause of the EXP-022 P2 miss of 10-15. This is NOT stated as the cause of the 10-21 miss; it is not shown. Mark: cause unexplained unless the manager adds a notebook id here: `<FILL 10-22: cause or "cause unexplained", notebook id>`.
- **Restored (C6):** the guard now runs on mal-fast-0 as host-side MiScusi jobs, not session crons: loop #1318 (07:10Z and 19:10Z, immediate trial run), dead-man #1319 (13:10Z), at 136cd98dd6845e9e60b65153ab0f3c4f31959142, pinned tree. Setup #1317 (books v0, empty state, frozen objects present). The first fast-0 trial run #1318 at 2026-10-21T21:13:51Z gave verdict NONE with six flags clear, one applied line (read job #1321). C5 official run: job #1314, 2026-10-21T20:54:01Z on research-0, `/data/mal/structure-monitor/daily.jsonl` line 15, six flags clear, synthetic 0/20; notebook n_TMetA3HCe95hnA. Successor jobs (13-day limits end about 11-04): `<FILL 10-22: job ids of the after-chained successors and the 11-03 reminder>`.

## 9. Run-fault disclosure: a trigger with a missing `s0_t_recv_ms`
For `h5v2-boostclock-a2` the wrapper drops any pv trigger whose `s0_t_recv_ms` is missing, non-integer or < clock + 60 s before `score_book` runs (fail-closed). That filter applies to v2 only. A trigger with a missing `s0_t_recv_ms` is NOT dropped for `h5v1-boostfloor-a2` or the a1 ids: the scorer's executor parser raises a schema fault on it (PR #787 body: "a trigger with a missing s0 still faults the v1 book's parser (schema fault, run stops) when v1-a2 is scored in the same run"). A run that hits it STOPS and is reported as a RUN FAULT; it is not worked around by hand and the dir is not edited. The count of pools dropped from v2 (`dropped_s0_before_clock`, `dropped_decided_before_clock`, `dropped_after_window`) is disclosed in every read; v1-a2 and v2-a2 populations may therefore differ by a few pools. The a2 shadow starts 2026-10-22, so its dir holds v2 records for pools opened before the clock; they are not covered by EXP-024 Am.9 and nobody opens them.

## 10. Feasibility sentence (QP (9), Am.4 item 2.6)
About 12 attempts a day (the a3 ruling's order of magnitude): 100 attempts on 5 or more UTC dates is reachable inside the 14-day window if nothing halts (about 9 dates). The real risks are an A3 FAIL or ERROR halt (any PumpSwap redeploy is a certain false halt on public RPC, `A3_ENUM_ENV_FILE` unset; a pump redeploy needs a PASS within 24 h), a rate below about 7 a day, and a lapsed guard. This is an expectation about counts, not about profit. No one plans live money on this path.

## 11. Am.4 item 1.14 disclosures (as quoted in the DEC)
Quoted from DEC-031 Am.4 item 1.14 at 136cd98 (PumpSwap enumeration fails closed on public RPC; the N-1 CompleteEvent / PostCompleteBuyEvent fail-open; judgement pins; one-sample pins; deadline costs; Token-2022 base-mint fail-open; guard job "not exercised live" until its first fast-0 run). `A3_ENUM_ENV_FILE` is left unset: 0 Helius credits used by the guard.

## 12. Job ids and merge shas
- Guard loop **#1318**, dead-man **#1319**, guard setup #1317, guard trial read #1321 (all mal-fast-0 at 136cd98dd6845e9e60b65153ab0f3c4f31959142). C5 run **#1314**.
- P0 job #1322; a1 halt job #1299; a1 shadow #1158 (to be cancelled before registration; ps check job `<FILL 10-22>`); a1 daily score #1201 and digest #1255 (not touched).
- Registration job, a2 shadow job, deadman check, successors, 11-03 reminder, a2 day-1 dry score, a2 daily score: `<FILL 10-22: job ids>`.
- PR merge shas on origin/main:
  - #783 counts tool: `3f3aea76193accaa9e8902397bba127dbb3b4aca` (2026-10-21T21:27:09Z);
  - #784 EXP-024 Note under Am.9 for `h5v2-boostclock-a2` (C7): `024dff25a98534e93034e31489eae2856765e80d` (2026-10-21T21:28:43Z);
  - #785 a2 scoring wrapper: `89dbe120de5546e2f2c0f552a03000438888c672` (2026-10-21T21:22:25Z);
  - #787 wrapper s0 filter: `eb8b32deef236257e7b5c9dca62a77a5c6afc455` (2026-10-21T21:34:53Z).
  - Earlier pins: #782 DEC-031 Am.4 `136cd98dd6845e9e60b65153ab0f3c4f31959142`; #778 `22338cf88525b966aa055fbf7890a092f034dd2c`; #780 `b1a4997b24c7a542526c4b8377948336c78c9d5d`.
- Reviewer / QP comment ids: #783 reviewer 6326909015, QP 6326923946; #784 reviewer 6326955329, QP 6326952781; #785 reviewer 6326814646, QP 6326837924; #787 reviewer 6327063452, QP 6327061179 (from the manager log, not re-read from GitHub). QP a2-pack ruling: `QP-RULING-1022-a2pack.md`, sha256 prefix 36ae8df0 (manager log).

## 13. Day-1 dry score (post-clock; this section may only be ADDED to after the clock)
`python -m tools.arena_h5_score_a2 score --dry-run --shadow-dir $HOME/data/arena/h5-2026-10-22`, job `<FILL after the clock>`: both a2 books `registered: true`, `refused_dirs` empty `<FILL after the clock>`; counts only. If it fails it is reported at once and not fixed by hand (QP GO item 12).

## Open questions for QP
1. **StackConfig / rent.** Am.2 item 3 says each covered host passes `rent=0` in its `StackConfig`; the H5 books have no `StackConfig` and are priced by `tools/arena_h5_cost.py` with `RENT` 2,039,280 (section 2). Does Am.2 cover the H5 a2 books at all, and if it does, is the rent-charged H5 stack the intended comparator? Reported here as found.
2. The V-band order (V test before the quote_mint test) differs from the DEC wording; accepted as disclosed?
3. Full sha256 of the v0 books file is not in the files available to the author (only the first 8 hex).

Nothing here is a profit claim. No book has cleared the promotion gate.
