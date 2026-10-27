QP VERDICT at 2ac6fca00ad0dc36f9cfec7b36f7fd1e5e58e6cc: OK

# QP ruling 2026-10-26 (written 2026-10-26T17:38:20Z, `date -u`): PR #950 P33 measurement + P18 staging; Plan B

Scope: PR #950 (claude/p33-dbc-p18, head 2ac6fca00ad0dc36f9cfec7b36f7fd1e5e58e6cc), DECLARE/MEASURE/STAGING.md, measure.py/json (job #1648), drain.py/json, migopt.py/json. Read against REGISTRATION.md R1-R14, AMENDMENT-1.md AM1-AM6, READ-HARDENING.md, DEC-031 Am.4 item 2 and Am.5 item 1, HOLDOUT_LEDGER, HANDOFF §1/§4.
Not opened: fwdv1/, the DBC window [10-25T00Z, 11-01T00:10Z), any DBC hour in [10-17, 10-24), HV4/Q50/arena/meta-capture outputs, any P&L file. One count only, on P33's own exploration output: `dropped_after_cut` > 0 in 0 of 2,288 DAMM pools (so no DAMM row at or after 10-17T00Z reached the script). cfg_raw_all.json: stat only (mtime 2026-10-24T06:55:04Z), not opened by me.

## Gate status (the CLAUDE.md promotion gate)
Nothing in #950 is an edge claim and none is made. m10 has 0 out-of-sample trades. Its figures are all in-sample, best of 23 screened DBC cells: flat +0.0122 SOL/trade on the 52 Swap2-era hours (HANDOFF); rehearsal #1507 flat +0.01358 per attempt (trade CI90 lo +0.00635); the Swap3-era day 10-16 "about +0.004 per attempt, about one-fifth" (REGISTRATION). Winner's curse applies in full. Gate: NOT cleared (0 of 100 OOS trades, 0 OOS days). The 11-01 read is a confirmation only (HANDOFF §1). A PASS licenses P18, not C0, and its trades never count toward P18's gate.

## Numbers checked against the files (unrounded)
- Strand share Swap3-era 7/177 = 0.03954802259887006; upper bound 12/177; Swap2 8/374 = 0.0213903743315508. Charge 0.25 + 0.00016 + 0.00203928 = 0.25219928 SOL. x share = 0.009973982824858757 SOL/attempt. Correct.
- Bar b1 0.9984474728307745 (n 5,797), b2 1.0 (n 1,629), b3 0.9821976672805403 = 1,600/1,629. Correct as declared.
- Latency p90 sum 0.76 + 0.6479913529093857 + 0.010323497001081705 + 0.453 + about 0.64 = about 2.51 s. p50 sum about 1.35 s. Correct. Carried items are labelled carried. The Helius feed was never probed.
- Causal sanity: 374 Swap2 decisions md5 c25f759683a51699e4582f0657af712b equal am5 (0/0 only-one-side).

## Q1. Is Plan B allowed? Yes, with the conditions below.
Building on exploration hours [10-14T00, 10-17T00) and synthetic tests touches neither the m10 seal nor its confirmation-only status. Reasons:
- The read runs from the tag worktree, and the ops tool checks its 12 pins there (dbc_m10_read_ops.py).
- AM6's conditions were fixed at the am1 tag instant (7d5be9e5, before 10-25T00Z).
- Exploration hours are in-sample already, and the P18 clock hours are disjoint from the window.
- Building before the look is better than building after it. P18's rule, code and bars get frozen before anyone sees m10's outcome. So no P18 parameter can be chosen with the read's result in view.

Plan B lifts HANDOFF §4 line 47 ("Build them only if a read PASSes") for BUILD only. The manager records that in the HANDOFF and a notebook entry.

Before the read, the build must NOT do any of the following (each one is a condition of this OK):
1. Open, list beyond R3 markers, count, replay, smoke or test on fwdv1/ or any DBC hour >= 2026-10-17T00Z. Proofs and tests may use only [10-14T00, 10-17T00) and synthetic data. This includes [10-17, 10-24) (SPENT; C1 decode-only is the only allowed touch, and it is done).
2. Modify any of the 12 pinned paths on main: dbc_fwd.py, dbc_fwd_read(_am1).py, dbc_fwd_sec3.py, dbc_price.py, dbc_screen.py, dbc_config.py, dbc_decode.py, arena_stack.py, arena_declog.py, venue_backfill(_dbc).py, cfg_summary.json. Also do not modify tools/dbc_m10_read_ops.py or scripts/research/dbc-m10-read-job.sh. Do not touch the tag, the tag worktree, the ops worktree, the v3 marker, fwd-read-v1/, or the scheduled F/G/read jobs. **STAGING §3's "`dbc_fwd --live`" option is struck.** Pool pruning and the live loop go in a NEW module (tools/dbc_p18_run.py, wrapping or subclassing Engine). The md5 proofs are the ones STAGING names: 6965028e46dba86f7293f4d27284a321 / 074a827303e94e8049284f67aafe7471 / c25f759683a51699e4582f0657af712b, with pruning on.
3. Start any live DBC process before the read. That covers a Helius transactionSubscribe connection, the 10-min probe, a P18 decision log, or a credit meter. The start trigger is NOT a clock time. It is the read job printing "LOOK CONSUMED" (that is after the preflight saw all 168 hours complete). Reason: the probe and the feed share the Helius key and rate budget with F, and an incomplete-hour refetch is allowed only before the claim. STAGING's "about 01Z" moves to "after LOOK CONSUMED". The dry-score day (11-02) does not change.
4. Create a bot_id, a bots.json line, an N increment, a P0 run or a registration record before a recorded PASS.
5. Run a heavy P18 job on research-0 from 10-31T22:00Z until the read finishes (its MemoryMax is about 10 GiB, exit 13 guard).
6. Build any DBC executor, live.json or C0 artifact. Plan B covers paper only.

After the read:
- On FAIL, NOT_DECIDABLE or PASS-SCREEN-LOOKAHEAD, stop the process at once. Register nothing. Do not reuse the code for a sibling cell.
- On PASS, P18's rule, pins and bars are exactly those merged before the look. Any later change to deciding text is a new registration with the change disclosed, and the clock moves.

## Q2. B' (P0 = the read's printed per-day attempt counts; clock 11-04): allowed as a declared departure, Am.5-style, with these conditions:
(a) A DEC-031 amendment carrying the departure must get a QP OK and a reviewer verdict, and must MERGE BEFORE the read's LOOK CONSUMED. Otherwise B' is unavailable and Plan B applies. The B-vs-B' choice must not be made with the read's counts in view.
(b) The count is R13's per-UTC-day attempt n of the AM1 deciding set for 10-25 through 10-31, quoted verbatim. Only that field is used. It is copied after the verdict is recorded.
(c) The proxy is not conservative. It is a backfill with blockTime; the live process uses receive time and can drop rows, so it is an upper bound. Pass bar: mean >= 20 per day and no day < 10, as in Am.5 item 1.3. Below the bar, B' gives NOT_DECIDABLE and Plan B (live P0) applies. It is not a drop.
(d) H = min(90, max(14, ceil(150/rate))). A PASS implies >= 100 fills in 7 days, about 14.29 a day at least, so H = 14 is effectively fixed. Pin it before the clock.
(e) The day-1 dry score on the LIVE process at the registration-candidate sha is still required. That includes item 4, the live-vs-backfill cross-check (the only link between proxy and process), and Q4's latency item. One process per sha.
Why this is acceptable: P0 decides only the floor and H, and given a PASS the proxy cannot move either.

## Q3. Strand: FULL LOSS deciding, immediate DAMM sell report-only: OK.
- This equals R8 PRIMARY and does not change the copy of m10. For the non-migrating pools it is also the honest value: 13 of 15 m10 strands sit in pools with no MigrationDammV2 in the window (85 of 6,541 completions), so no exit exists.
- The feasibility bar was met as declared, but on U migrated non-strand pools. It says nothing about the 13 non-migrating strands.
- Every P18 read must print the report line's coverage (strands with a migration / all strands) and the unmodelled items: fill price and competition at the migration sell.
- The report line never decides, is never combined into a pass, and is never used to choose between lines afterwards.
- P16's m4/m6 hold-on-DAMM re-pricing stays unconfirmed: 735 of 740 drained U pools drained within 600 s.

## Q4. Latency: the summed p90 is about 2.51 s against the 2.5 s line.
1. **Deciding legs are unchanged (a copy of the read):** 1.9 s both legs, k = round(1.9/slot_s) per hour, printed (9 on all 551 exploration decisions).
   - A pinned constant k = 9 "worst-in-slot" is NOT allowed as deciding, because it would make P18 a different cell from the one confirmed.
   - It may be a report-only line if pinned before the clock.
2. R9's 2.5 s ENTRY-leg condition (flat AND pressure mean > 0) stays DECIDING, as in STAGING §1. Add R9's report-only 3.4 s entry and 3.4 s exit lines.
3. **New pre-clock bar (dry-score item 6):**
   - B1 logs per decision: trigger slot, t_recv of the trigger row, t_decide, and the highest slot seen at t_decide.
   - Define L90 = p90 over the dry-score day of (t_decide - t_recv_trigger) + max(0.76 s, the Helius probe's measured feed-lag p90) + 0.453 s + 3 x slot_s.
   - Bar: L90 <= 2.5 s. If it fails, there is no registration at that sha. One fix at a new sha is allowed, with an md5 equivalence proof and a new dry score. This is pre-clock and outcome-blind.
   - At today's components L90 is about 2.52 s. In practice, then, the slot-close-on-next-slot change has to be in B1. Its replay md5 proof cannot show live neutrality: a processed feed can deliver slot-s rows after a later slot's signal. So `late_rows` and item 4 must be printed separately for that mode, and the <= 1% / <= 2% bars apply.
4. **During the clock latency is REPORT-ONLY:** a daily notebook table, never an exclusion, early stop or window end. Load and latency follow outcomes (DEC-031 Am.6 principle).
5. The C0 stop rule ("signal-to-landing p90 > 2.5 s") is unchanged and applies to real sends only.

## Q5. migopt.py reading hunt28/p17/cfg_raw_all.json: NOT a seal contact.
- It is a cache of config ACCOUNTS (static on-chain state keyed by config address), not tape rows.
- It was written 2026-10-24T06:55:04Z, before the forward window.
- It is the same P17 config fetch family as the pinned R4 map cfg_summary.json (913c2725).
- Lookups were keyed only by configs seen in exploration hours, and only aggregates over exploration pools were printed (migopt.json). The SPENT rule bans count, screen, engine or price on [10-17, 10-24) tape. None of that was done.
- MEASURE.md's disclosure is enough.
- Recommendation: future scripts should map configs through tools/dbc_config + cfg_summary.json or tools/dbc_cfg_fetch on exploration keys, so whole-cache loads are not needed.

## Q6. Day-1 dry score and kill rules: sufficient once these are added (all outcome-blind, fixed before the live process starts):
- Item 6 is the latency bar (Q4.3).
- Item 7: peak RSS of the feed+decider <= 1 GiB target, hard bar < 1.5 GiB, on the day with pruning on. fast-0 refuses jobs of 2 GB or more.
- Item 8: feed credits for the day are printed. More than 3x the 85k estimate is an operational void.
- The dry-score and P0 counters read decision-row kind and timestamp only, never exit rows. The Engine writes paper exit slots, and those are outcome-linked.
- A sha change after the dry score needs a new dry score.
- Re-check for a kill-review week at registration.
- The registration states multiplicity N+1 and discloses best-of-23, the Swap3 one-fifth figure and the receive-time differences.
- The existing kill rules (m10 not PASS kills P18 and every D1 variant; a logic failure kills; one operational re-run; FAIL or NOT_DECIDABLE is final per bot_id; the A3-style flag ends the window; no extension of H) are adequate.

## Conditions for this OK
The OK covers #950 as a measurement and staging record. Q1 items 1-6, Q2 (a)-(e) and Q4/Q6 additions bind B1-B3 and any B' amendment; each of those PRs needs its own QP OK and reviewer verdict. No book has cleared the promotion gate.
