# P44 revive-trigger sweep

Status: plan fixed 2026-10-26T17:47Z (`date -u`), before the count run of record. Results are added below the plan in a later commit.

## Plan (fixed before the count run)

- Counter: `p44_count.py` sha256 `56d5a21878624971a0b1b1244772d784ea19bd4bc4d2fc53638e7ddad450a7a8`, a copy of hunt24 L3 `scripts/l3_count.py` (sha256 `c45bfbfa...7424d`) with five marked changes (see its docstring).
- Signature list: `data/agent_buyback_signer_sigs_1021_1026.txt` sha256 `b1027804a5a8117d5497c364b53748e12d92cfa49ccaf012401724d560de8c32`. These are the 194 successful signatures of the Agent Payments buyback signer `GmFrDZT2cdrqykgTikVdXbe8EtCgzUDM9VsDhQnwsUsG` with blockTime at or after 2026-10-21T00Z, from one public-RPC `getSignaturesForAddress` call at 2026-10-26T17:45Z.
- Data: closed arena tape hours 2026-10-21T00 .. 2026-10-24T23 (96 files, 4 full UTC days). These hours are walk-2-hour information (DEC-030 §2 R1) and are disclosed as such. No hour at or after 2026-10-25T00Z is read by the counter (path guard).
- The counter drops G0 (WSOL quote, V0 in [17.5e9, 17.7e9], the H5 / EXP-024 / Q50 universe) at its first print. No G0 trigger or BOOST test is computed.
- Outputs: pools, BOOSTed pools and H5-structure trigger counts per day for G1 (off-band WSOL), Gz, and G2 (PUMP, USDC and other quotes). Create, complete and pool_create rows by quote class and mayhem flag. Agent-signature trade rows by SOL size.
- Counts only. No price after any decision. No fill, exit, outcome or return. No ledger, bots.json, shadow, canary or sealed path is opened. Per C4 of the QP verdict on #900, these counts are ops information. They never decide or exclude an hour for HV4, Q50 or DBC m10.
- Run: MiScusi job, `run_p44_counts.sh`. Output goes to `/data/mal/hunt-1008/push-1016/p44-revive-sweep/p44_counts.json`.

## Results (written 2026-10-26 ~18:00Z, after job #1656)

Counts only. Nothing here says a book makes money. No book has cleared the promotion gate. A met trigger is a licence to freeze a rule, not evidence of an edge.

**What was read.** The arena tape, through MiScusi job #1656 (`run_p44_counts.sh` at f8df962b, counter sha256 `56d5a218...a7a8`). The run took 5 min, read 96 files and 84,454,373 rows, and wrote output sha256 `a93b4068f06d0fb308a7f31bdf04cf503fe7e7316210b6cded8aa11a49c4c81c`, copied to `data/p44_counts.json`. It also read:
- the structure monitor (`/data/mal/structure-monitor/daily.jsonl`, 20 runs from 2026-10-08T10:25Z to 2026-10-26T07:11Z, and `slice-watch.jsonl`);
- the change radar (`/data/mal/change-radar-arena1/`, run 2026-10-26T07:40Z);
- public RPC (`api.mainnet-beta.solana.com`, 0 Helius): 1 getAccountInfo, 1 getSignaturesForAddress on the buyback signer, 3 getSignaturesForAddress on the Moonit, Boop and Heaven programs, and 62 getTransaction calls for the USDC InitBoost check. That check took the 79 USDC-quoted `pool_create` rows on the 10-21..10-24 tape (creates only), kept the 62 with a pump mint, and looked for `Instruction: InitBoost` in each migrate tx's logs (`usdc_initboost_check.py`);
- MiScusi job status for #658 only.

**What was not read.** Nothing listed here was opened:
- G0 (V-band WSOL) trigger counts; the counter never computes them;
- any HV4 or Q50 output, status or log;
- the H5 arena books' ledgers and digests;
- the C1-NF shadow's out-dir;
- DBC venue tape;
- meta-capture;
- any hour at or after 2026-10-25T00Z.

Tape quality: PumpSwap data-loss gaps over the four days were 110, 25, 10 and 1, totalling 61,906, 22,560, 12,704 and 146 ms. Pump.fun data-loss gaps were 1,492, 1,613, 1,141 and 877, totalling 2,370,695, 2,402,852, 1,111,293 and 810,628 ms. Overlapping gap rows are summed, so the ms totals are an upper bound.

### Class of every stated trigger (`data/trigger_classes.tsv`, `classify_triggers.py`)

There are 302 non-duplicate rows with a stated revive_trigger, plus SB-H5, which has none in source. Hand overrides are applied first, then keyword rules. The keyword tags are a reading aid only; this table and the main table govern.

| class | rows | meaning |
|---|---|---|
| NOW (measured here) | 13 | measurable now on allowed data; measured below |
| MET-EARLIER | 1 | p25-pump-order-keeper-absorb, met on counts 2026-10-25 (P29, #931) |
| NOW-NOT-RUN | 4 | measurable now (public HTTP or RPC) but not run in this sweep |
| LATER | 13 | gated by a date, a seal end, or a tracker that must be rebuilt |
| PROCEDURAL | 130 | the trigger is a new DECLARE, a gate or CI result, a QP or DEC step, or a priced screen; not a count |
| EXTERNAL | 46 | official page, owner policy, CEX or another chain; desk work |
| CAPABILITY | 15 | needs a feed, latency, engine, sell path or fee tier MAL does not have |
| DEPENDS | 43 | the trigger is another line, a new rule, or unclear |
| NONE | 38 | the row says none, n/a, or dead on mechanism |

### Main table

| row | trigger (short) | current value | met? | data source | what a revival would need |
|---|---|---|---|---|---|
| lib-c-P1 / cap-impact-4 / lib-b-E6 (C1-NF) | >= 10 kept trades/day on 2 days | **No tracker running.** Shadow #658 (j_FoxKZkBja_12Ag, mal-fast-0) was cancelled 2026-10-22T19:53:49Z (cancel requested by user `thevaanster` in the job record) after 286 h 10 min; monitor #662's limit was about 10-17T22Z. Last recorded rate is about 1 kept/day (cap-impact-4, JUDGE-PUSH §1.2). | unknown (last known: not met) | job status only; the out-dir `/home/claude/data/c1nf-shadow` was not opened (EXP-025 Look 2's R8 refusal is due after 10-26T12Z) | Owner or manager OK to restart a counts-only kept counter (#658 was cancelled from the `thevaanster` account). Then 2 days >= 10 kept, a new registration (EXP-025 ends NOT_DECIDABLE), QP, an alpha slot. |
| H5 BOOST (no library trigger; manager's framing) | Sept 53.5/day vs Oct rate | Not recounted (G0 dropped). Cited: L3 COUNTS 10-20 strict Q*40 = 16; STACK.md "October arena counts 11-16/day". | n/a | cited files | H5 is not shelved. It runs as arena books a1/a2 and the Q50 read (earliest B 2026-10-30T00Z). No revival step exists. |
| lib-c-M1 / library-capacity-3 (H5 cadence exit C10) | October last-slice shift >= ~8 s against Sept 343-347 s | Median of 20 run medians 340.25 s (range 332-342; each run has 6-18 pools). Shift 2.75-6.75 s on the pooled reading; 11-15 s only on single small runs (332 s 10-13, 332.5 s 10-17). | **not met** (near on single runs) | structure monitor `boost.last_slice_after_migrate_s` | A DEC-027 amendment or a new DEC, a frozen rule, an md5 exit proof, and a fresh window of about 660 pairs. |
| lib-c-R2.SB-H5 (depth tripwire) | Global.max_curve_depth leaves 1 | **2** (byte 1087 of `4wTV1Y...`, ctx slot 461753727, sha `dd2b9fa2...`, unchanged since the 2026-10-14T07:11Z run) | **met** (since 10-13; already in a3-repin-2026-10-14 review) | public RPC getAccountInfo | Not a strategy. The library row is stale and should be updated. The review says depth-2 coins are not WSOL-quoted, so they are outside H5/Q50. |
| lib-a-A3 (USDC BOOST flow) | a USDC-quoted BOOST flow seen again | **On-chain InitBoost on USDC-quoted pump-mint pools: 0 / 0 / 1 / 1** (10-21 / 10-22 / 10-23 / 10-24; checked 8 / 14 / 19 / 15; 1 / 0 / 5 / 0 fetch fails). The behavioural detector gave 0 / 0 / 5 / 1, so it over-counts USDC about 3x. The monitor sample before this showed 0 of 12. | **met, barely** (2 in 4 days; the tree said gone after 09-15) | public RPC getTransaction on the pool_create signature (62 calls); p44_counts.json | Far below any book rate. A new rule, an engine quote-conversion model, a DEC amendment. Not worth a lane at this rate. |
| boost-custom-v2-1 (H5 v1 on BOOSTed PUMP/USDC custom pairs) | BOOSTed PUMP- or USDC-quoted graduations reach "several per day" | BOOSTed pools, behavioural: PUMP 19 / 6 / 13 / 3, USDC 0 / 0 / 5 / 1, together 19 / 6 / 18 / 4. Other-quote 67 / 63 / 69 / 43. Monitor InitBoost: other-quote (incl. PUMP) 83 of 83 sampled. | **met on counts** (PUMP alone, behavioural detector) | p44_counts.json; monitor | An engine model for quote-conversion legs under a DEC amendment (DEC-031 Am.3 8(i)), a frozen rule, QP. Trigger counts are thin (next row). |
| boost-universe-1 / -2 (H5 on custom-pair BOOSTed, scaled V) | quote mint known from a chain read, and >= 20 trades/day | Quote mint is on the tape (`pool_create.quote_mint`). H5-structure strict Q*40 triggers on all custom pairs: 8 / 6 / 9 / 4 (PUMP 4 / 0 / 4 / 1, USDC 0 / 0 / 0 / 0, other 4 / 6 / 5 / 3). The loose detector gives 24 / 16 / 31 / 10; loose over-counts per L3. | **not met** (strict); half the trigger (quote mint) met | p44_counts.json | >= 20/day on the strict detector, a fee schedule read, quote-conversion legs. |
| l3-boost-custompair-1 | engine model, and >= 20 triggers/day on 3 full closed days | as above: strict Q*40 8 / 6 / 9 / 4; strict Q*60 other 13 / 18 / 10 / 9 | **not met** | p44_counts.json | as above |
| l3-boost-offband-1 (G1 off-band WSOL) | >= 20 triggers/day on 3 full closed days | loose Q*40/50/60: 2 / 2 / 1 / 0; BOOSTed G1 pools 13 / 16 / 8 / 13 | **not met** (about 10x short) | p44_counts.json | pool supply or a DEC-031 P0 floor change |
| mech-buyer-scout-1 (Agent Payments buyback) | >= 13 buys/day of >= 0.5 SOL into one pool | Buys in the signer's txs (tape join): 14 / 21 / 16 / 15 per day; >= 0.5 SOL 0 / 1 / 0 / 3; >= 1.0 SOL 0 / 0 / 0 / 2; distinct pools 5 / 7 / 8 / 5. Signer txs per day (RPC): 28 / 39 / 33 / 32 (10-25: 36). | **not met** (max 3 against 13) | RPC signatures joined to tape by signature | none planned |
| web-cross-venue-6 (Moonit) | pools active again | Program `MoonCVVN...`: 987 ok of the newest 1,000 txs from 2026-10-20T14:50:59Z to 10-26T17:39:39Z, about 161.4 ok tx/day. Instructions not decoded. | **unresolved** (program active; pool trading not shown) | public RPC | Decode the instruction mix (creates vs swaps), then a census row. Volume is tiny next to pump. |
| census-heaven-boop-moonit | recheck monthly | Heaven about 27.6 ok tx/day (927 of 1,000 since 09-23); Boop about 5.9/day (661 of 1,000 since 07-06) | rechecked: tiny | public RPC | none |
| p25-pump-order-keeper-absorb | tag rule, and tagged sells >= 100/day with >= 20 clusters/day | Met on fresh-0903, 2026-10-25 (P29 RESULTS.md: median full day 3,139 tagged sells, 227 SL60 clusters, 225 passing the 2% bar). PRICING-DECLARE v4 is on main, not priced. | **met** (earlier) | docs/research/p29-keeper-absorb | QP re-check on the exact PRICING-DECLARE head, then the single priced run on the declared blocks. |
| hunt23-w3-pump-sink-1 (untested; a kill test, not a revive) | dead if PUMP-pool buy SOL inside child-buy signatures < 5% | Daily share 0.9361 / 0.6588 / 0.7924 / 0.8142. Child-leg PUMP buys >= 0.5 SOL: 6 / 94 / 230 / 187 (L3 10-19..21: 4 / 2 / 3). SOL in child-leg buys: 151.05 / 254.81 / 726.12 / 579.66. | kill not fired (daily, not hourly) | p44_counts.json | A regime shift from 10-22: almost all of these buys are >= 1.0 SOL (94 of 94 on 10-22). It belongs to the QSINK line (hunt23-w3-qsink-1). Counts only. |
| boost-universe-4 | October-class data allowed under the EXP-024 seal end state | seal runs to its end state (first 2026-11-14T00:00Z) | later | ledger / EXP-024 | wait |
| lib-c-J2 / lib-a-A19 (attention Route B) | forward tape after about 11-06 | not yet | later | JUDGE-14 | wait |
| dbc-strand-damm-continue, dbc-early-momentum, p37-dbc-stall-migrate | m10 forward read (>= 2026-11-01T00:10Z) | DBC forward window sealed | later | ledger | wait for the m10 read |
| p25-redeploy-holiday | D0 job on the 10-08 and 10-13 changes | not run (needs role oct-structure-d0) | later | — | Manager submits the D0 job. A 10-19T17:47:02Z pump/PumpSwap redeploy also sits inside the arena tape (feed start 10-19T08:05Z), so 9.7 h of same-day pre-change control exists there (counts only, not done). |
| w4s-short-cex-listing-watch | >= 1 pump-origin perp listing/day for 7 days | no poll running; last measured about 0.13/day (W4-S, 09-05..10-20) | not run | public CEX APIs | a counts-only poll job |

Other arena structure, per day 10-21 / 10-22 / 10-23 / 10-24:
- complete rows: 769 / 727 / 904 / 769;
- non-mayhem WSOL completes: 372 / 385 / 483 / 431, plus mayhem-unknown 139 / 106 / 107 / 82 (create before tape start);
- mayhem WSOL completes: 158 / 153 / 218 / 191;
- USDC completes: mayhem 5 / 9 / 13 / 13, non-mayhem 0 / 0 / 1 / 1;
- G0 pools (count only, no test): 569 / 568 / 643 / 551;
- PUMP-quoted child creates: 5,003 / 5,126 / 4,990 / 4,178.

### Met or near-met rows

- **Met:** p25-pump-order-keeper-absorb (on counts, 10-25); boost-custom-v2-1 (behavioural BOOST count only; PUMP 19 / 6 / 13 / 3 a day); lib-c-R2.SB-H5 (tripwire fired 10-13; protection item, not a strategy); lib-a-A3, barely (2 InitBoost USDC graduations in 4 days).
- **Near-met or unresolved:** lib-c-M1 (2.75-6.75 s pooled against an 8 s bar); web-cross-venue-6 (Moonit program active, pools not shown).
- **Not met:** C1-NF (no tracker; last known about 1/day); boost-universe-1/2 and l3-boost-custompair-1 (strict 4-9/day against 20); l3-boost-offband-1 (0-2/day against 20); mech-buyer-scout-1 (0-3/day against 13).

### Recommendation (at most 2)

1. **p25-pump-order-keeper-absorb (P29). Run the frozen pricing once QP re-checks PRICING-DECLARE v4 on its exact head.** This is the only met trigger on a mechanism with volume.
   - Capacity line, quoted from PRICING-DECLARE §8 (arithmetic, not a result): N is at most about 225/day before the distinct-signature, PumpSwap-only, d >= 3% and 360 s filters, so the real N is lower. At 0.25 SOL and a net edge e: N = 100/day gives 0.125 / 0.25 / 0.50 SOL/day at e = 0.5% / 1% / 2%.
   - The sign of e is unknown. Entry latency after the last tagged sell is binding: the sell is in the pool before we can see it. The untagged cascade family (hunt29-flush-1) was negative on the same days.
   - Days are the exploration pool, so the result is in-sample. Out-of-sample needs unsealed later days per the ledger.
2. **boost-custom-v2-1. Do not revive yet. Run one counts-confirmation step first:** an on-chain InitBoost count of PUMP- and other-quoted graduations on 3 closed days (the same 1-call-per-pool check as for USDC; the behavioural detector over-counted USDC about 3x), plus a desk cost of the quote-conversion route.
   - The behavioural count says "several per day", but the strict H5-structure triggers are only 8 / 6 / 9 / 4 per day across all custom pairs.
   - Capacity line (arithmetic): about 7 triggers/day x 0.25 SOL = about 1.7 SOL/day turned over. At an assumed +1% net per trade that is about 0.017 SOL/day; at +5%, about 0.085 SOL/day.
   - The exploration evidence is 47 tradeable trades (1.31/day), with the positive total resting on two blocks. The quote-conversion legs cost about 1.5 pp (boost-custom-v2-2).
   - Any book is arena-only and needs an engine model plus a DEC amendment.

Not recommended: a C1-NF revival. Its trigger cannot be read without a tracker, and #658 was cancelled from the `thevaanster` account. If the manager wants it, the cheap step is the owner's OK for a counts-only kept counter. Hard-memory caps apply.
