# P33 STAGING: the P18 forward paper DBC book (m10 copy), ready to clock after an m10 PASS

Written 2026-10-26 (`date -u` 2026-10-26T17:31:53Z before the measurement sections were written). Status: STAGING ONLY. **Nothing is registered by this file.** No bot_id, no bots.json line, no clock, no P0.
**No book has cleared the promotion gate. m10 is in-sample only (best of 23 screened DBC cells, about 2.2 Swap2-era days; Swap3-era rehearsal about one-fifth of that). Nothing here says any book makes money.**
Inputs: DECLARE.md (rules fixed 2026-10-26T17:02:31Z), MEASURE.md (job #1648), docs/research/dbc-p18/DESIGN.md, docs/research/dbc-m10-forward/REGISTRATION.md + AMENDMENT-1.md + READ-HARDENING.md, DEC-031 (Am.4 P0/H, §2 read), AUDIT.md §3 Lane 2 (origin/claude/p32-survivor-audit), P39 (PR #949, notebook n_jRvB_DyhCnpB4Q).

## 0. Precondition and kill rules
- P18 exists only if the registered m10 confirmation read (tools/dbc_fwd_read_am1.py at tag dbc-m10-forward-v1-am1, >= 2026-11-01T00:10Z) returns PASS.
- **m10 FAIL, NOT_DECIDABLE, or PASS-SCREEN-LOOKAHEAD (AM6 class) kills P18 and every D1 variant on DBC (m4, m6 and the strand-continuation line included). No sibling, no re-tune, no other cell, no new window.** Any staging work (code, feed, logs) is then stopped and left unregistered.
- Day-1 dry score below the bar (section 4) kills P18 before its clock (one operational re-run allowed, as DECLARE.md says).
- P18's own read: FAIL or NOT_DECIDABLE is final for its bot_id (DEC-031 §2). Not reached by clock + H: NOT_DECIDABLE (DEC-031 Am.4 item 2.3).
- A3-style guard flag (section 3, B3) ends the window as for H5 Am.10.
- No start in a kill-review week (none recorded in LAB_STATE at 5071e29a; re-check at registration).

## 1. Book spec: a copy of m10 as frozen in its read
| item | P18 | source |
|---|---|---|
| cell | D1 m10 exactly: BUY print, post progress p in [0.02, 0.35]; pool's last 60 s: buys >= 10, net buy SOL >= 4.0, post spot / window-first pre-spot - 1 in [+5%, +40%]; exits sl 0.90, trail 0.90 on the running peak from the decision spot, time 600 s; cooldown 600 s per pool | R5 |
| universe | SOL-quoted; migration_threshold >= 5 SOL (config value for Swap3 rows); 0.25 SOL buy impact <= 2% at the signal state; p <= 0.85; curve not completed; not a bad row; one warm-up hour after process start | R5 |
| exclusions (counted per day) | transfer-hook pools, configs with transfer_fee_bps > 0, unmapped configs; EvtSwap3 is not a hook | R6 |
| deciding set | causal live-policy decisions of tools/dbc_fwd.Engine, SEALED in the hash-chained decision log at decision time (not re-derived after the fact) | AM1 (the live log replaces the read's replay) |
| refusal of a sealed decision | AM2 verbatim: bad_state priced with bad_row zeroed (still refused: full loss); completed_before_entry and no_state: -160,000 lamports; block_edge and bad_hour: excluded; key not found in the day's backfill: full loss; > 1% not found: NOT_DECIDABLE | AM2, SEC3-DECLARE (a)-(d) |
| pricing | frozen pricer (dbc_price 7db0afdd, dbc_screen 607e3173, arena_stack a39b2ba6) on the closed day's Helius backfill; Swap3 config-curve path; stake 0.25 SOL; guard 1.15 + migration room; 2 x 160,000-lamport sends; rent 0 (rent-charged line reported); no haircut | R7 |
| legs | 1.9 s both legs, k = round(1.9 / slot_s) per hour from the tape (measured k = 9 on all 551 exploration decisions) | R9 |
| fail models | flat 15% and pressure (slope 1, intercept refit to mean p 0.289); both decide | R10 |
| **strand (DECIDING)** | **FULL LOSS (stake + buy send + 2,039,280 rent), exactly R8 PRIMARY. Unchanged by P33.** | R8 |
| strand (REPORT-ONLY line and executor action) | DECLARE.md text: sell all at the first slot at which the MigrationDammV2 tx is seen and the DAMM v2 pool is active, landing k slots later, priced on the DAMM v2 state; no migration within 600 s of completion, non-WSOL pool or no state = full loss. Never deciding, never combined into a pass | section 2 |
| robustness | flat AND pressure mean at a 2.5 s entry leg (exit 1.9 s) > 0 | R9 |
| read | DEC-031 §2: first 00:00Z boundary with >= 100 counted attempts on >= 5 UTC dates (outcome-blind decision-timestamp count); then the promotion gate under both fail models: >= 100 trades, >= 5 days with a strict majority positive, CI90 lo of mean SOL per attempt > 0 (1,000 draws, seed 1, 5th pct), ex-top-3 > 0; plus R11's ex-best-day > 0, mean > 0 and the R9 2.5 s condition | DEC-031 §2, R11 |
| multiplicity | N + 1 at the clock (DEC-031 Am.3/Am.4) | |
| H | min(90, max(14, ceil(150 / rate))) from P0; exploration Swap3-era causal rate 177 decisions in 20 h (about 212 a day) gives H = 14 if the live rate is >= 150/14 (about 10.714) a day | DEC-031 Am.4 item 2.2 |

Declared differences from the read (unavoidable, stated at registration): live rows carry receive time (`bt_src: recv`, no blockTime at processed), so the 60 s window and warm-up run on receive time live; triggers are sealed live, and the closed day's backfill is used only to price them and to cross-check the trigger set (section 4 item 4).

## 2. The strand rule, fixed by measurement (MEASURE.md)
- **Deciding: full loss (R8), unchanged.** That is also manager17's instruction after P39: the conservative strand rule stays unless the exit reliably beats the DAMM v2 drain.
- What the measurements say:
  - m10's strand share is small but not zero: Swap3-era 7 of 177 decisions open at completion (0.03954802259887006), upper bound 12 of 177; Swap2-era 8 of 374. At the full-loss charge that is 0.009973982824858757 SOL per attempt of stake lost on Swap3-era hours (arithmetic, not a price).
  - **13 of m10's 15 strands are in pools that did not migrate at all within the window** (85 of 6,541 completions; median 28.87 h before window end; their configs mostly take a 99% migration fee). For those, no DAMM exit exists in the measured hours: full loss is the honest rule, not just the conservative one.
  - Where a pool does migrate (U sample), completion -> migration p50 0 s / p90 5 s / max 190 s; the DAMM pool is active from the migration (activation_point = migration time in 2,288 of 2,288); a sell landing at migration + 9 slots precedes the drain in 773 of 773 U pools with >= 1 SOL reserve (4 drained within 5 s, p10 drain 35.9 s after migration). So the immediate-sell action is feasible and beats the drain on the measured pools; the declared feasibility bar is met (b1 0.9984474728307745, b2 1.0, b3 0.9821976672805403).
  - Holding on DAMM is not safe: 735 of 740 drained U pools drained within 600 s of migration (P39: 927 of 967 pools with >= 1 SOL kept < 1% of it by +10 min). P16's strand re-pricing (m4/m6 positive, tp/sl/trail kept on DAMM up to the cap) is exposed to the drain and should be treated as unconfirmed at best; this lane does not re-price it (it is a D1 variant: dies with m10 anyway, revives only through m10's PASS).
- Consequence for P18: the report-only line uses the immediate-sell action (adopted per the bar); it prices at most the 2 of 15 exploration strands that migrated. A rule that waits for the first DAMM print's jump above the decision spot is rejected (AUDIT). Not measured: the fill price of the immediate sell, and competition from other holders selling into the same first seconds.

## 3. Runner path (reuse what exists; build only the gaps)
Existing (merged on main, 5071e29a):
- Feed: `tools/dbc_fwd_feed.py` (Helius transactionSubscribe, processed, failed:false, DBC program; reconnect/gap rows, credit meter, cap exit 5, key redaction). `--live` is refused in this build; tested on a fake socket only.
- Decider: `tools/dbc_fwd.py` Engine (live policy, causal; replay CLI only). Log: `tools/arena_declog.py` DecisionLog (hash chain, verify, md5).
- Day backfill: `tools/venue_backfill_dbc.py run --start D --end D+1 --out-dir <P18 root> --credit-cap <cap>` (about 0.35M credits a day, 2.465M per Swap3-era week measured on the holdout fetch).
- Pricer and verdict: `tools/dbc_fwd_sec3.py` Pricer/price (AM1/AM2 lines), `tools/dbc_fwd_read_am1.py` (the one-look pattern: marker, LOOK CONSUMED, preflight, config map R4), `tools/dbc_fwd_read.verdict` (R11 + R9). Ops guards pattern: `tools/dbc_m10_read_ops.py`, `scripts/research/dbc-m10-read-job.sh`.
- Config map: `tools/dbc_cfg_fetch.py` + `tools/dbc_config.py` (base map docs/research/dbc-p17/data/cfg_summary.json).
- Strand report line: `tools/damm_v2_fetch.py` (DAMM history of the day's migrated strand pools; about 480 credits a pool) and `tools/damm_grad_price.py` (Ser state; a new immediate-sell function is needed, not ser_rule's tp/sl/trail).
- Checks: `tools/dbc_m10_decode_check.py`, `tools/dbc_m10_engine_equiv.py`, `tools/dbc_latency_detect.py`.

To build (three PRs, builder + reviewer + QP each; equivalence proofs on exploration hours only, never on [10-17, 10-24) or >= 10-25):
- **B1 live process** (`dbc_fwd --live` or `tools/dbc_p18_run.py`): feed -> Engine -> DecisionLog on mal-fast-0 with t_recv/t_decide, start/gap/hb rows, restart via a start row quoting the previous head; **pool pruning** (completed pools and pools idle beyond the horizon: the unpruned engine peaked at 1062.0 MiB over 72 h, and fast-0 refuses jobs >= 2 GB); strand-action rows (migration seen, sell decision); optional slot close on the feed's next-slot signal (cuts the measured slot-close p90 of 3 slots). Proof: the both-oracle replay md5s 6965028e46dba86f7293f4d27284a321 / 074a827303e94e8049284f67aafe7471 and the causal 374-decision md5 c25f759683a51699e4582f0657af712b unchanged with pruning on.
- **B2 scorer** (`tools/dbc_p18_read.py`): the am1 reader's logic on the SEALED live log (no re-run of the engine), P18 window guard as a wrapper (do not edit dbc_price.py; the read's pins stay intact), own one-look marker and out dir, outcome-blind boundary counter, full-loss deciding line, report-only DAMM strand line, live-vs-backfill cross-check (`dbc_p18_xcheck`), day-1 dry-score mode (counts only). Tests synthetic, plus the legacy path still reproducing #1488.
- **B3 registration pack**: bots.json-style record (frozen sha, rule, stake, H pin per Am.7), P0 counter (decision-timestamp count of the live log), the A3-style daily guard from DESIGN.md §3 item 4 (program upgrade, unseen discriminator, undecoded > 1%, unmapped-config SOL > 5%, hook/transfer-fee exclusion jump, swap-mode mix change), job scripts (feed+decider as one resumable MiScusi job on mal-fast-0, <= 1 GB target; daily backfill + pricing jobs on research-0 chained with `after`).
- First live act: the 10-minute transactionSubscribe probe (about 3k credits, DESIGN.md) that measures bytes, credits and lag. Not before 2026-11-01T00:10Z: a live DBC feed before then would see sealed-window hours.

## 4. Day-1 dry score bar (fixed in DECLARE.md before measurement; counts only)
On the first full closed UTC day of the live process: (1) chain verifies, 0 breaks, 1 start row; (2) would-be attempts 20..600 (exploration context: 177 in 20 Swap3-era hours); (3) unmapped-config SOL rows <= 5%, undecoded events <= 1%, late_rows <= 1% of SOL rows; (4) live vs backfill trigger sets: unexplained difference <= 2% of the union; (5) gap time <= 1 h, 0 credit-cap exits. Any logic failure kills P18; an operational void allows one re-run on the next full day. No outcome, fill or P&L is opened.

## 5. The clock, given an 11-01 PASS
The read job starts at 2026-11-01T00:10Z (after the last fwdv1 fetch and G pass); a PASS is known on 11-01 (hours later). HANDOFF line 47: "a DBC live paper book (P18 PRs 2-4) [is] not built. Build them only if a read PASSes."
| plan | what happens when | earliest clock | earliest read boundary (100 attempts on 5 dates, if about 212/day holds) |
|---|---|---|---|
| A (HANDOFF as written) | B1-B3 built after the PASS, merged about 11-03; probe and process start 11-03; dry score on 11-04 (run 11-05 after its backfill); P0 = 11-04, 11-05, 11-06; registration 11-07 | **2026-11-08T00:00Z** | 2026-11-13T00:00Z |
| **B (recommended)** | manager lifts line 47 for BUILD only now: B1-B3 merged by 10-31 on synthetic tests and exploration-hour replays; no live DBC process before 2026-11-01T00:10Z. Probe + process start 11-01 about 01Z (decision rows only; nothing scored while the read runs); dry score on 11-02; P0 = 11-02, 11-03, 11-04; registration 11-05 | **2026-11-06T00:00Z** | 2026-11-11T00:00Z |
| B' (needs a QP-ruled departure, like Am.5) | as B, with P0 replaced by the read's own printed attempts per day (R13, 7 days); registration 11-03 after the dry score | 2026-11-04T00:00Z | 2026-11-09T00:00Z |
If m10 does not PASS under B or B', the process is stopped at once and nothing is registered; the cost is the build time and about one day of feed credits. Every date moves to the next 00:00Z after any slipped item; nothing is cut to keep a date. The read then still needs the boundary day's backfill and pricing (hours after 00:00Z).

Credits (estimates, not measured): feed about 85k/day (researched rule, unprobed); daily backfill about 0.35M; strand DAMM fetch a few thousand a day. About 5-6M over a 14-day H.

## 6. Not decided here
Plan A, B or B' (manager; B' needs QP). Whether P16's m4/m6 strand line should be marked invalid in the library (P39's lane, not this one). Registration text, bot_id, frozen sha.
