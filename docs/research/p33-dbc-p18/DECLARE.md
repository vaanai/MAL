# P33 DECLARE: DBC-P18 staging and strand measurement (counts and decode only, NO price)

Declared at: 2026-10-26T17:02:31Z (`date -u` on mal-research-0), on branch `claude/p33-dbc-p18` from origin/main `5071e29a`. Written BEFORE any measurement below was run.
Before this file I only (1) read the records named in the task, and (2) printed the row schema of one exploration tape hour (2026-10-14T12) and one DAMM v2 pool file, to name the fields. No count, latency or share below was computed before this file.

Lane: P33 (docs/research/p32-survivor-audit/AUDIT.md section 3, Lane 2, on origin/claude/p32-survivor-audit). Stage the P18 forward paper book so that a PASS of the m10 confirmation read (>= 2026-11-01T00:10Z) becomes a clock in days, and measure strand exits.

**No book has cleared the promotion gate. m10 is in-sample only (best of 23 screened DBC cells, about 2.2 Swap2-era days). Nothing here says any book makes money. Nothing is registered by this lane.**

## Seals (obeyed; from SEALS-BRIEF.md sha256 823cc062...)
- DBC forward window [2026-10-25T00Z, 2026-11-01T00:10Z) and `fwdv1/`: not listed, not opened. No live DBC feed, probe or decision log is started for any instant in it.
- DBC [2026-10-17T00, 2026-10-24T00) is SPENT for m10 and every D1 variant: not read at all by this lane (not even decode-only). `meteora_dbc_holdout/`, `p17_union/`, `decode-check-1017-1024/`, `holdout-read-1024*/`, `fwd-read-v1*/`, `fwd-v1/` are not opened.
- DAMM v2 rows at or after 2026-10-17T00Z are dropped by the measurement script (the fetch already cut them; the script re-checks).
- No HV4, Q50, arena or meta-capture file is touched. No sec3/rehearsal/screen P&L file is opened.
- Hours used: DBC exploration [2026-10-14T00, 2026-10-17T00) under `/data2/mal/venue-tape-backfill/meteora_dbc/` only, through the `tools.dbc_price` data guard; DAMM v2 pool files under `/data2/mal/venue-tape-backfill/meteora_damm_v2/pools/` (fetched by P16 for migrations inside that window) and its `checkpoint.json` (migration index).
- 0 Helius credits. No RPC call. No key read.

## What is NOT computed (NO price)
No fill, no fee-adjusted return, no P&L, no SOL per trade, no tp/sl outcome label. The engine's paper exit slot (below) is a slot number from the frozen exit timing on the decoded spot path; its reason (sl, trail or time) is not recorded and no fill is priced at it. Fee RATES of DAMM v2 sells (decoded `trading_fee + protocol_fee + compounding_fee + referral_fee` over the gross output) are reported as a cost parameter, not a price.

## M-a: detection-to-landing latency for the P18 path (what the tape allows)
The tape is a getTransactionsForAddress backfill: `t_recv_ms` = block_time x 1000, there is no receive time and no in-slot transaction index. So the feed lag and the send-to-land leg are NOT on the tape. Measured on tape, per causal m10 decision of the M-c engine run:
- a1 `slot_s` per hour (the engine's own `slot_s_of` over SOL-quoted usable rows) and `k_in = round(1.9 / slot_s)`.
- a2 slot-close wait: the engine decides slot s only when a row of a later slot arrives (any pool; `Engine.advance`). Wait = first tape slot > s with any DBC row, minus s, in slots and in seconds (slots x slot_s). The live feed (`failed:false`) delivers the same succeeded-transaction set the backfill holds; that equivalence is an assumption.
- a3 decide compute: wall time of each `Engine._decide` call that seals at least one decision (research-0 CPU, not fast-0: a proxy).
- a4 follower proxy (competition, not our latency): per decision, slots from s to the first later BUY print in the same pool by a signer other than the trigger's signer; and the number of BUY prints (any signer) in slots (s, s + k_in], i.e. buys that land before our modeled landing.
- a5 carried, not measured here: feed lag (P15: fast-0 processed public wss p50 0.49 s / p90 0.76 s; research-0 confirmed p50 0.89-1.64 s / p90 2.54-4.63 s; Helius `transactionSubscribe` never probed), and send-to-land (pump stack on fast-0: 2.20 slots mean at 155k priority, fee arm 2026-10-23; probe p50 2 / p90 3 slots read-to-land). No DBC send was ever made.
The path total is reported as a sum of components with each component labelled measured (tape), measured elsewhere (carried) or assumed.

## M-b: curve completion to the first DAMM v2 print; can a sell land within N s
Population: every `curve_complete` row in DBC [2026-10-14T00, 2026-10-17T00) (min slot per pool). Join: `checkpoint.json` `migs` (MigrationDammV2 tx: slot, block_time, damm_pool, wsol_is_b) by DBC pool; then the DAMM v2 pool file if fetched.
Per migrated pool: (i) completion -> migration tx (slots, s); (ii) completion -> first DAMM trade; (iii) completion -> first DAMM SELL (side sell = token in, WSOL out); (iv) the pool's activation point and type decoded from EvtInitializePool (IDL tail layout: activation_type u8, collect_fee_mode u8, liquidity u128, sqrt_price u128, activation_point u64, ...; sqrt_price/liquidity offsets are the ones `damm_decode.init_state` validated; activation fields are new and their decode is sanity-checked against the migration slot or block time and reported); (v) sell fee rate of the first DAMM sell and the median over sells in the first 60 s after migration; (vi) number of distinct sell signers in the first N s.
Shares with a sell possible by N in {0, 1, 2, 5, 10, 30, 60, 300, 3600} s after completion, where "possible" = migration tx landed, activation point passed, and (stricter) at least one other signer's sell landed by then.
Samples: (U) uniform = fetched pools NOT in docs/research/dbc-p16/strand_pools.json (sha256 order, P16 erratum 1); (H) strand-priority (hot) pools; all. U is primary. Era split by completion block time: Swap2-era < 2026-10-16T04:00Z <= Swap3-era; and by whether the DBC pool ever printed EvtSwap3. Censoring at 2026-10-17T00Z counted.

## M-c: share of positions open at curve completion (Swap3 era primary)
One causal run of `tools/dbc_fwd.Engine` (main `5071e29a`, dbc_fwd.py b43e7955, dbc_price.py 7db0afdd, dbc_screen.py 607e3173, dbc_decode.py 10a52a52, cfg_summary 913c2725), live policy, no oracle, `enable_swap3_pricing`, continuous over [2026-10-14T00, 2026-10-17T00), `first_ok_bt` = 2026-10-14T01:00Z, `ss_live` as `dbc_fwd.replay`, decision log (arena_declog, hash-chained) in the run's out dir. It bypasses ONLY `dbc_fwd.replay_guard` (10-16T04 end), as `tools/dbc_m10_engine_equiv.py` does; the `dbc_price` data guard is not bypassed.
Per sealed decision (pool, slot s, entry_slot = s + k_in, exit landing slot X from its exit row), with C = the pool's first curve_complete slot in the window:
- `completed_before_entry`: C <= entry_slot;
- `open_at_completion` (strand, P14's `ended`): entry_slot < C <= X;
- `upper_bound_open` (rule-free, no exit timing): entry_slot < C <= s + round(600 / slot_s) + k_out;
- not completed in the window (C absent or > the bound).
Reported by era of the decision (Swap2-era < 10-16T04 <= Swap3-era), by Swap3-pool flag, per UTC day; plus decisions per hour/day by era. A sanity line: the run's Swap2-era decision count and md5 vs the am5 replay (374, causal, window [10-14T00, 10-16T04)) are printed; a continuous run may differ (state from earlier hours), and any difference is reported, not fixed.

## Strand rule for P18: text fixed now, before the measurement
- DECIDING line (P18 copy of the m10 read): strand = FULL LOSS (stake + buy send + 2,039,280 rent), exactly R8 PRIMARY. Not changed by any measurement here.
- Paper strand ACTION (what an executor would do; sealed in P18's log, priced as a REPORT-ONLY line, never deciding): "On curve completion with the position open, the sell is decided at the first slot at which the feed has seen the DBC MigrationDammV2 transaction of that pool AND the DAMM v2 pool's activation point has passed; it lands k slots later (k = round(1.9 s / slot_s), the book's leg); it is priced on the DAMM v2 state after the last print with slot <= landing slot, at the pool's last observed sell fee rate. No tp, no hold, no wait for a price: sell all at the first possible slot. If no migration is seen within 600 s of completion, or the DAMM pool is not WSOL-quoted, or it has no state by then: full loss."
- A rule that waits for, or relies on, the jump of the first DAMM print above the decision spot (P16: median about +11%) is rejected.
- Feasibility bar (declared now; decided by M-b on sample U): the paper strand action is ADOPTED as P18's report-only line and executor action only if, among U migrations, (1) completion -> migration tx <= 60 s for >= 90%, (2) activation point passed at the migration slot + k for >= 90%, and (3) a sell by another signer lands within completion + 60 s for >= 50%. Otherwise the action is "not reachable": P18 reports full loss only and the executor gap list carries "no DAMM exit".
- M-c sets no rule; it sizes how much the strand rule can matter for m10.

## P18 day-1 dry score bar (fixed now; counts only, applied to the first full closed UTC day of the live P18 process, before registration)
All must hold; else the book is not registered (kill before the clock). An operational void (process crash > 1 h, feed silence > 1 h in total) re-runs the dry score once on the next full day; a second void or any logic failure kills.
1. Decision-log chain verifies end to end (arena_declog verify), 0 breaks, 1 start row.
2. Would-be attempts on the day >= 20 and <= 600 (exploration Swap3-era causal rate is printed by M-c for context; the bar is a broken-feed check, not an edge check).
3. Refusal classes known at decision time (warm-up, cooldown, universe exclusions) printed; unmapped-config SOL rows <= 5%, undecoded-event share <= 1%, `late_rows` <= 1% of SOL rows.
4. Live-vs-backfill cross-check on that day (DESIGN.md section 2): md5 of sorted (pool, signature) of live trigger prints vs the frozen `triggers_for` on the day's backfill; unexplained live-only plus backfill-only <= 2% of the union.
5. Gap rows: total gap time <= 1 h; 0 credit-cap exits.
No outcome, fill, exit price or P&L is opened by the dry score.

## Outputs
`docs/research/p33-dbc-p18/measure.py` (the one script; sha256 in SHA256SUMS.txt), `measure.json`, `MEASURE.md` numbers quoted unrounded from it, then `STAGING.md`. Heavy part (M-c engine run) as ONE MiScusi job on mal-research-0 under MemoryMax 8G after a free-memory check.
