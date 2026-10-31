# P33 measurements: DBC latency on our path, completion -> DAMM v2, strand share (counts and decode only)

Written 2026-10-26T17:31:53Z (`date -u`). Rules: DECLARE.md (declared 2026-10-26T17:02:31Z, commit 125f0bc2, before any count). Script measure.py (sha256 1db7f626..., commit 5687d67f).
Run: MiScusi job #1648 on mal-research-0, code 5687d67f, 20 min, peak 1.2 GB. Output measure.json sha256 b18cc774454b2e913f94e0ddffe6c8fd0aa7d936823636d9971bd808393a4691 (copy in this dir).
Hours: DBC exploration [2026-10-14T00, 2026-10-17T00) only (72 hours, 0 incomplete). DAMM v2 histories of P16's fetch, cut at 2026-10-17T00Z. 0 Helius credits, no RPC.
**Nothing is priced. In-sample exploration hours only. No book has cleared the promotion gate. Nothing here says any book makes money.**

Disclosures:
- Dev runs before #1648: the `damm` step was run once inline (M-b numbers, identical to #1648), and the `measure` step was dev-tested once on the existing am5 causal log (Swap2-era decisions only); that dev run showed the Swap2-era strand counts (8 of 374) before the job. No rule or bar was changed after either.
- Two POST-HOC decode diagnostics, added after seeing #1648 and labelled as such, never a bar: `migopt.py` (config migration fields of pools that completed but never migrated; it reads the P17 raw config cache `/data/mal/hunt-1008/push-1016/hunt28/p17/cfg_raw_all.json`, which holds config ACCOUNTS only, no tape rows; only configs of exploration-hour pools are looked up), and `drain.py` (DAMM v2 liquidity drain against the first possible sell; added at manager17's request after P39, PR #949).

## M-c: m10 positions open at curve completion (causal dbc_fwd.Engine, live policy, continuous 72 h)
Engine run: 551 sealed decisions, 551 exit rows, declog md5 07b2ed2ac86bd4674a0b862db109548f, head 0a6b25d5... seq 1174, wall 1151.4 s, peak RSS 1062.0 MiB.
Sanity: the 374 decisions before 2026-10-16T04Z equal the am5 causal log exactly (md5 c25f759683a51699e4582f0657af712b both; 0 only-mine, 0 only-am5).

| era (decision time) | decisions | per day | open at completion | share | upper bound (rule-free, completion inside entry..cap+exit leg) | share | completed before entry |
|---|---|---|---|---|---|---|---|
| Swap2 (< 10-16T04) | 374 | 10-14 194, 10-15 148, 10-16 32 | 8 | 0.0213903743315508 | 19 | 0.05080213903743316 | 0 |
| **Swap3 [10-16T04, 10-17T00)** | **177** (20 h) | 10-16 177 | **7** | **0.03954802259887006** | 12 | 0.06779661016949153 | 1 |
| all | 551 | | 15 | 0.02722323049001815 | 31 | 0.056261343012704176 | 1 |

- Swap3-era pools: 2,531 pools printed EvtSwap3; 1,575 of the 6,541 completions in the window are Swap3-era. All 177 Swap3-era decisions are in Swap3 pools; 167 pools, 19 of which complete in the window.
- For decisions in pools that complete, completion came after the decision by p50 77.0 s (Swap3) / 32.0 s (Swap2).
- Arithmetic only (not a price): at the registered full-loss strand charge (0.25 + 0.00016 + 0.00203928 SOL), the Swap3-era strand share costs 0.03954802259887006 x 0.25219928 = 0.009973982824858757 SOL per attempt before anything the position would otherwise have returned.
- **Where m10's strands go (post-hoc, migopt.py):** 13 of the 15 open-at-completion positions, and the 1 completed-before-entry, are in pools that completed but had NO MigrationDammV2 transaction in the window (85 of 6,541 completions; median completion 28.87 h before the window end, so not an edge effect). 27 of m10's 527 decision pools are among those 85. All m10 pools have migration_option 1 (DAMM v2). The 85 non-migrating completions differ in config: migration_fee_percentage 99 in 48 of 85 (vs 0 of 6,456 migrated), creator_migration_fee_percentage 100 in 48 (vs 7), migration_fee_option 0 in 51 (vs 23). Cause not established; the tape shows only that these pools did not migrate within hours.

## M-b: curve completion -> DAMM v2 (uniform sample U = fetched non-strand pools, sha256 order; block time has 1 s resolution)
| | U all | U Swap2-era | U Swap3-era | strand (hot) pools |
|---|---|---|---|---|
| completions with migration / DAMM history | 5,797 / 1,629 | 4,258 / 1,179 | 1,539 / 450 | 659 / 659 |
| completion -> migration tx, s: p50 / p90 / p99 / max | 0 / 5 / 9 / 190 | 0 / 5 / 9 / 190 | 0 / 5 / 9 / 119 | 1 / 2 / 3 / 4 |
| same, slots: p50 / p90 / p99 / max | 2 / 24 / 40.08 / 883 | 2 / 24 / 40.86 / 883 | 2 / 25 / 38 / 552 | 3 / 8 / 13 / 19 |
| migration in the completion slot | 2,338 | 1,783 | 555 | 60 |
| completion -> first DAMM print, s: p50 / p90 / p99 | 2 / 9 / 19.76 | 2 / 9 / 20 | 2 / 8 / 18.51 | 2 / 4 / 9 |
| completion -> first DAMM sell (anyone), s: p50 / p90 / p99 | 4 / 16 / 53.58 | 4 / 16 / 46.36 | 3 / 16 / 74.61 | 3 / 24 / 76.42 |
| migration -> first DAMM sell, slots: p50 / p90 | 12 / 72 | 12 / 72 | 11 / 73 | 9 / 105.2 |
| first DAMM sell fee rate: p50 / p90 / p99 | 0.001000053676498061 / 0.0010089419957268338 / 0.002999766801249095 | | | 0.0010000126590932238 / 0.0012000783731320765 / 0.0012294064459510026 |

- Activation: all 2,288 DAMM pools decode activation_type 1 (timestamp) with activation_point equal to the migration block time (difference 0 for all). So a sell is allowed from the migration slot. Decode checks: init collect_fee_mode equals the first trade's on 2,284 of 2,284 pools with trades; init slot equals the migration slot on 2,288 of 2,288.
- Share with a sell possible N s after completion (U, n 1,629; migration landed and active / another signer's sell landed): 0 s 920 / 194; 1 s 1,358 / 269; 2 s 1,436 / 484; 5 s 1,507 / 891; 10 s 1,617 / 1,153; 30 s 1,625 / 1,577; 60 s 1,628 / 1,600; 300 s 1,629 / 1,613; 3600 s 1,629 / 1,614. Swap3-era U (n 450): 0 s 262 / 57; 2 s 401 / 168; 5 s 410 / 262; 10 s 447 / 336; 60 s 450 / 443.
- Feasibility bar (DECLARE.md, on U): b1 completion -> migration <= 60 s 0.9984474728307745 (n 5,797); b2 active at migration + 1.9 s 1.0 (n 1,629); b3 another signer's sell within 60 s 0.9821976672805403. **Bar met as declared**; see the drain line and STAGING.md for why that does not change P18's deciding line.
- 85 completions have no migration in the index (above); 4,168 migrated pools were not fetched by P16 (credit cap).

### Drain vs first possible sell (post-hoc, drain.py; manager17 after P39, PR #949, notebook n_jRvB_DyhCnpB4Q)
Drain = first DAMM print whose post-trade WSOL reserve is < 1% of the pool's running maximum, that maximum being >= 1 SOL. First possible sell landing = migration slot + 9 (the decisions' k_in).
| | U all | U Swap3-era | strand pools |
|---|---|---|---|
| pools with max WSOL reserve >= 1 SOL / drained before the cut | 773 / 740 | 235 / 225 | 597 / 581 |
| migration -> drain, s: p10 / p50 / p90 / p99 | 35.9 / 148.5 / 374.1 / 569.22 | 45.0 / 165.0 / 455.4 / 565.44 | 44.0 / 199.0 / 510.0 / 603.0 |
| drained within 2 / 5 / 10 / 30 / 60 s of migration | 0 / 4 / 9 / 61 / 142 | 0 / 1 / 2 / 11 / 30 | 0 / 2 / 6 / 37 / 82 |
| sell landing (migration + 9 slots) before the drain | 773 of 773 | 235 of 235 | 597 of 597 |
| WSOL reserve at that landing, SOL: p10 / p50 / p90 (pools with a print by then) | 10.9128292192 / 11.055555546 / 14.2078978184 (n 469) | 10.912728631 / 11.014659707 / 19.8524740314 (n 159) | 10.883728363 / 11.057910983500001 / 20.3382010741 (n 450) |
Reading: an immediate sell lands before the drain in every measured pool, with 4 of 773 U pools drained within 5 s. A rule that holds on DAMM up to a time cap (P16's strand re-pricing kept the tp/sl/trail up to decision + 600 s) is exposed to it: 735 of 740 drained U pools drained within 600 s of migration. This is consistent with P39's finding; it does not price the immediate sell (competition from other holders selling at migration is not modelled; first sells by others land at p50 4 s).

## M-a: detection-to-landing on the P18 path
Tape (measured, 551 decisions): slot_s p50 0.21659845931632163 s (p10 0.2156510276229852, p99 0.21806060606060607); k_in = 9 for all 551.
| component | p50 | p90 | p99 | source |
|---|---|---|---|---|
| feed lag, chain -> receive | 0.49 s | 0.76 s | n/a | CARRIED: P15, fast-0 public wss processed, one 10-min window 2026-10-24 (Helius transactionSubscribe never probed) |
| slot-close wait (engine decides slot s at the first later-slot DBC row) | 1 slot = 0.21662953820217953 s | 3 slots = 0.6479913529093857 s | 16 slots = 3.4828958309088396 s (max 124 slots = 26.73677891830242 s) | MEASURED on tape (proxy: tape = succeeded txs, as `failed:false`) |
| decide compute per sealing call | 0.004741581040434539 s | 0.010323497001081705 s | 0.018997325998498127 s | MEASURED on research-0 CPU (fast-0 not measured) |
| decision -> send | 0.221 s | 0.453 s | | CARRIED: pump probe, fast-0 |
| send -> landed | 2 slots (about 0.42 s) | 3 slots (about 0.64 s) | | CARRIED: pump probe / fee arm (mean 2.20 slots); no DBC send ever made |
| **sum (chain time of trigger print -> landed)** | **about 1.35 s** | **about 2.51 s (sum of p90s)** | | sum of components; p90 sum overstates a true p90 |
With a confirmed feed (P15 p50 0.67 / p90 0.98 s) the sums are about 1.53 s / 2.73 s. P15's 1.1-1.3 s / 1.9-2.1 s omitted the slot-close wait; with it the p90 sum reaches the 2.5 s stop line. A decision-neutral fix exists (close slot s on the feed's next-slot signal instead of the next DBC row); it needs its own md5 equivalence proof.
Competition proxy (not our latency): another signer's buy lands p50 2 / p90 21 slots after the trigger slot; within k = 9 slots for 0.7731397459165155 of decisions (Swap3 0.7401129943502824); buy prints landing before our modeled landing: p50 4, p90 16, max 37.

## Not measured
Feed lag on Helius transactionSubscribe and its credit rate on DBC; any DBC send or landing; fast-0 decide time; the price at which a strand sell would fill (P&L not computed); competition at the migration sell; why 85 pools did not migrate; post-window history of those pools (it lies in spent or sealed hours and was not looked at); whether m10's Swap3-era strand share holds on later hours.
