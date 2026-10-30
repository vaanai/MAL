# Edge hunt 2026-10-30: speed and recent pump changes

Lane edge-hunt (manager18 push, brief `/data/mal/hunt-1008/ops/manager18/PUSH-BRIEF-1030.md`). Written 2026-10-30, `date -u` 15:19Z. Base main `c92f23e3`.

**What this is.** Desk work on the lab's own notes plus a small public-chain and public-web read. **No tape row was priced.** No price after any decision was computed. No sealed, forward, holdout, arena-outcome, DBC-window, shadow, canary, probe or meta-capture path was opened. 0 Helius credits. Public RPC calls: 17 for the holder-rewards probe and 2 for the volume-accumulator probe (`api.mainnet-beta.solana.com`). Public web: GitHub `pump-fun/pump-public-docs` (unauthenticated API and raw files), DefiLlama's public volume API, and vendor pricing pages through web search. Arena data used: only the change radar's daily row counts (`/data/mal/change-radar-arena1/daily-counts.jsonl`), which are counts. The structure monitor's synthetic-share lines (`/data/mal/structure-monitor/daily.jsonl`) were read as counts.

**No book has cleared the promotion gate.** Every number quoted below is copied from its source, which is named. Most are in-sample exploration figures, best-of-many, or vendor claims, and are labelled that way. Every SOL/day figure in sections 3 and 4 is my judgment, not a measurement.

Seals this note respects (SEALS-BRIEF.md sha256 823cc062f25415fa4bd4a248646d77c5eafff89b024a9b7437f59a4d365ed6b4, pasted in the PREREG): EXP-024 Look-2 (no outcome of an October V-range pool in its first 360 s until 2026-11-14T00Z), D2/D3, DBC forward window [2026-10-25T00Z, 2026-11-01T00:10Z) and fwdv1, DBC [10-17, 10-24) spent, HV4/Q50 output classes, arena tape counts and creates only, meta-capture never, fresh-0802 untouched.

## 1. Speed: where MAL is, and what speed is worth

### 1.1 MAL's signal-to-landing path on mal-fast-0 today (measured, from lab notes)

| Leg | p50 | p90 | Source |
| --- | --- | --- | --- |
| Feed: chain time to receive, public wss, processed | 0.49 s | 0.76 s | `docs/research/dbc-p15/LATENCY.md` (P15, fast-0) |
| Feed: same, confirmed | about 0.67 s | 0.98 s | same |
| Decision to send (probe build faa3192) | 221 ms | 453 ms | `ARTIFACTS/lab/probe-final-2026-10-07.md` (55% of it is file IPC, `ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md:116`) |
| Read to landed, slots | 2 | 3 | probe-final (61 round trips) |
| Read to landed, slots, fee-arm test 10-16..10-19 | mean 2.200 (155,000 priority), 2.438 (500,000) | | `ARTIFACTS/lab/feearm-landing-2026-10-23.md` |
| Total, signal print to landed | 1.1 s (processed) / 1.3 s (confirmed) | 1.9 s / 2.1 s (sum of p90s) | P15 LATENCY.md table |
| Landed slot vs pool-create slot, live buys 10-05..10-07 | k 5 (n 34), 6 (n 56) | 0 of 62 at k <= 2 | audit SYNTHESIS.md:34 |

Slot time now: 211 to 218 ms per slot in every structure-monitor record from 2026-10-09T19:23Z to 2026-10-30 (`/data/mal/structure-monitor/daily.jsonl`, `ms_per_slot`). So 1.1 s is about 5 slots today; in August (about 368 ms a slot) it was about 3.

### 1.2 How much edge exists at each latency (existing lab measurements)

| Family | Latency grid | Numbers (copied) | Status of the numbers |
| --- | --- | --- | --- |
| Migration entry, identified wallet cohorts, pool-level ROI over 30 min, before app and tx fees | k = slots after the first print | Aug / Sep: k0 +24.88% / +12.08%; k1-2 +24.44% / -4.93%; k3-5 -8.09% / -30.33%; k6 to 10 s -7.29% / -16.30%. Ex-top-3 pools, untagged: k0 +3.79% / +11.07%; k1-2 +4.69% / -15.99%; k3-5 -27.97% / -48.55% | realized cohort outcomes, not MAL fills (`/data/mal/audit-1008/reports/g_app_attributed_retail_timing.md`:39-41, 252-267) |
| EXP-012 (selected migrations, V pricing, 0.5 SOL, pressure model) | k 1 / 4 / 6 / 8 / 12 | +0.01866 / +0.01332 / +0.01246 / +0.00769 / +0.00335 SOL per trade; CI90 low > 0 to k 8, < 0 at k 12 | in-sample (881 OOF-selected rows); the frozen migrate-direct cell then FAILED its one-shot forward read on 2026-09-28 (library `lib-c-Q2.12`) |
| Unselected migration book | START k = 1 and later | 0 of 1,008 cells robust at probe fees; 2 at lean fee, both START k = 1; none at k >= 3 | `/data/mal/audit-1008/reports/d05_thesis_migration.md` via SYNTHESIS.md |
| H5 BOOST-floor v1 (Sep confirmation, flat model, 0.1 SOL) | entry latency 1.3 / 1.9 / 3 / 6 / 12 s | +13.208% / +12.455% / +12.213% / +9.826% / +6.432% | in-sample confirmation figure, decaying by block (+26.7 to +6.8%) (`/data/mal/hunt-1008/h5-flows/REPORT.md`:74-75) |
| Nine screen lines (FLOOR v2, REVIVE, w2-quiet, strand, BREADTH, SELLABS) | entry leg 1.9 to 0.3 s | gain 0.0000 to +0.0008 SOL per trade; no line changes sign | in-sample exploration (`ARTIFACTS/lab/hunt29-p6-latency/DESK-NOTE.md`) |
| screen-1019 cells and a generic E1 entry | feed 2/4/7 slots x leg 0.5 to 1.9 s | no grid point flips; the generic entry is lower when faster | in-sample (library `hunt23-speed-1`) |

Reading:
- **The only latency-bound family the lab has found is migration entry.** Its money sits in the first-print slot (k0), which launch bundles and same-slot snipers hold. Of identified cohorts, k0 is positive in both periods; k1-2 is positive only in August.
- **H5 is not a speed race,** but it is not flat either. 1.9 s to 1.3 s is worth +0.753 pp of stake in the Sep confirmation figure (flat model). Below 1.3 s nothing is measured.
- **Everything else is flat in latency** (P6, hunt23-speed).

### 1.3 What a faster path would take, and what it costs

| Upgrade | Cost (source, date) | Expected effect on MAL's path | Measured? |
| --- | --- | --- | --- |
| In-process decision (drop the file IPC between runner and executor) | engineering only | decision to send from 221 ms to tens of ms | no |
| Yellowstone gRPC feed | Chainstack add-on $49/month for 2 streams, $149/month for 7 (page updated July 2026); Shreder from 3 SOL/month; Alchemy $75/TB; QuickNode Scale $499/month incl. gRPC; Helius Business $499 plus LaserStream data from $400 per 5 TB (web search 2026-10-30; prices change often) | feed from 0.49 s to perhaps 0.2-0.3 s (vendor-class claim, about 200 ms faster than Agave websockets) | no; the lab measured a processed stream p50 1,012 ms ahead of the old confirmed getBlock tip follower (`ARTIFACTS/lab/fast-entry-trigger-2026-10-07.md`), but fast-0 already has a processed wss at 0.49 s |
| Staked / SWQoS send | Helius Sender SWQoS-only tip 0.000005 SOL; Sender Max (Jito, Harmonic, Rakurai routes) minimum tip 0.001 SOL per send, plus a priority fee (Helius docs) | landing from 2 slots to perhaps 1 (vendor "median 0 slots"), unmeasured | no; the planned 60-buy two-arm landing test (`speed-route-5`, DEC-019 Amendment 3) was never run |
| Priority fee | 155,000 lands as well as 500,000 (2.200 vs 2.438 slots, 150/152 vs 146/148 landed) | none to gain from paying more | yes, `feearm-landing-2026-10-23.md` |
| Co-location | already in Frankfurt (OVH); validator-adjacent bare metal EUR 140-980/month (vendor, P6) | small | no |
| Shreds | Helius shreds $800-1,000 per IP; Jito ShredStream shut down 2026-09-05 (P6) | feed sub-100 ms (vendor) | no |

Best realistic path (judgment): 0.2-0.3 s feed + 0.05 s decision + 1-2 slots landing = about 0.5-0.8 s, i.e. 2-4 slots now. Cheapest version: Chainstack gRPC $49-149/month (about 0.008-0.025 SOL/day at an assumed $200/SOL, my arithmetic) plus SWQoS-only sends (5,000 lamports a send), plus the free in-process decision.

### 1.4 Answer to (A): is there a realistic "be faster" edge at our capital?

**Not by buying infrastructure.** No upgrade gets MAL into the first-print slot. A same-slot seat needs leader-aware sending plus a shred feed, and it is still a race against launch bundles. The 2-4 slots that a $49-499/month path could reach today (about 0.4-0.85 s) fall in the k1-2 band of the August-slot cohort table, which was +24.44% in August and -4.93% in September. That band is not stable enough to pay for. Every other priced family is flat in latency.

**Two openings remain, and both are cheap to test before any spend:**
1. **Synthetic migration (since 2026-10-07)** turns the first-print seat into a completion seat. The buy that crosses the curve's limit gets the pool's opening price for any size, before `migrate_v2` and before BOOST. That is a "k = -1" entry, and it does not need to beat `migrate_v2` or the bundles. It still has to be the first buy to cross the limit (section 2.1). The fast test is a proxy k-curve on exploration blocks (section 4, PREREG).
2. **Sub-second entry for the H5/Q50 BOOST-floor family.** The family has a measured slope (+0.753 pp from 1.9 s to 1.3 s). Most of the faster path is free engineering. The fast test is a latency sweep of the frozen rule on exploration blocks.

## 2. Recent changes: what changed and whether any of it pays

Pump docs (`pump-fun/pump-public-docs`) last changed on 2026-10-13 (`639b87f`). The commits since September are 09-12 (holder rewards; cashback deprecated), 09-29 (negative virtual quote reserves), 10-07 (v3/v2 trades, multi-hop swap, pump coins as quote mints, fee sweeps, synthetic migration), 10-08 (pump-quote reserves from the raise) and 10-13 (depth-2 pump-coin quotes).

| Change (date) | What it does (source) | New mechanical edge? |
| --- | --- | --- |
| Synthetic migration (10-07) | the buy that empties a non-Mayhem curve has no max size: it buys the rest of the curve, then pool-bound tokens at the price the new pool would charge; nothing else trades until `migrate_v2`; bonding fee schedule on both parts (`docs/SYNTHETIC_MIGRATION.md`) | **Yes, candidate.** It is the only change that opens a seat which used to need same-slot speed (section 2.1). |
| v3 trades, fee sweeps (10-07) | protocol and creator fees stay in the curve or pool until a permissionless sweep; the price ignores them (negative VQR) | No. A sweep pays its caller nothing and does not move the price (P95 census, class 11-12). |
| `multi_hop_swap`, pump coins as quote, depth 2 (10-07, 10-13) | buying a child coin routes through its parents in one tx | No new edge. The forced-parent-buy idea is `lib-c-J3` (KILL). On 10-30 `Global.max_curve_depth` = 2 (my read). |
| 10-19 redeploys (15:46Z and 17:47Z) | Token-2022 transfer-fee quote support (new error `QuoteMintNotWritable`); 8 trailing bytes on Trade/Buy/Sell events, non-zero only on Token-2022 quotes (`ARTIFACTS/lab/a3-repin-2026-10-21-program-upgrade-review.md`) | No. It touches non-WSOL (PreStocks) quotes only. Existing rows: `p25-prestock-quote-band`, `p60-t22-quote-pnl-audit`. |
| App Platform Fee 0.45% (10-17) | app trades pay more; bots and terminals do not | No (a tag, `p25-app-fee-retail-tag`). |
| Holder rewards (09-12) | creator fee goes to holders; pump.fun pays out with `distribute_fee_to_holders`, signed by `Global.holder_reward_claim_authority`, with amounts computed off-chain (IDL) | **No (probed, section 2.2).** It is a continuous drip, so there is no payout moment to buy into. |
| Volume incentives | `GlobalVolumeAccumulator` of pump and of PumpSwap | No. Both are all zero on 2026-10-30T15:17Z (my read: start_time 0, end_time 0, 0 non-zero days), as P83 found on 10-27. |
| Radar "new vocab" 10-29/10-30 (MultiHopSwap, BuyExactQuoteInV2/V3, Init/CloseUserVolumeAccumulator, DistributeFeeToHolders, ...) | | **No change in the protocol.** I decoded all 24 discriminators the radar listed, and every one is a documented IDL name at docs `639b87f` (10-13). The v2/v3 and multi-hop instructions were already in the 10-21 review's sample. The radar samples 20 txs per program per day, so "new" means "new to its sample". Recommendation for the radar playbook: compare against the docs IDL and a cumulative vocabulary before raising MED. |
| PumpSwap trade rows x0.48 (10-28) | arena counts: 7,942,939 (10-28) and 7,618,354 (10-29) against a baseline median of 16.5-16.7M (radar) | **Mostly real, not a feed fault.** DefiLlama's PumpSwap daily volume was 140.6M (10-28) and 118.7M USD (10-29), against 397.0 / 293.4 / 259.0 / 266.4M on 10-20..10-23 (x0.42-0.50). pump.fun bonding volume 122.7 / 127.3M against 142-168M (x0.8); arena pumpfun trade rows 2.55M / 2.31M against about 3.0-3.2M. Completes and pool creates also fell (radar: x0.66, x0.65). DefiLlama's daily series is noisy (10-24 112.7M, 10-25 310.9M), so treat this as a cross-check, not proof. |
| Alpenglow (10-21) and 200 ms target slots | slots measured 211-218 ms | No edge by itself. The latency findings are in slots; `p60-slot-clock-decay` covers this. |

### 2.1 Synthetic completion: what the door is, and what is known about who uses it

- Mechanism (pump docs): `buy_exact_quote_in_v3(spendable, min_out, partial_fill)` spends the whole budget. The curve part is priced at curve prices and completes the curve. The rest buys pool-bound tokens on the reserves the migration will deposit. `migrate_v2` then opens the pool exactly there, and BOOST (about 17.585 SOL over about 340 s) follows on non-Mayhem graduations. If the budget does not reach the end of the curve, it is an ordinary buy. So at a 0.25 SOL stake, MAL can force completion only when 0.25 SOL or less is left on the curve.
- **Forced completion removes the stall risk.** The graduation play (`web-oct-mechanics-1`: buy at 85% progress, sell at or before complete) died with 71% of exits on the stop, i.e. curves that did not finish. A completing buy cannot stall: MAL's own buy completes the curve. What is left is the post-migration path into BOOST, which is what the k-curve test prices.
- Who completes now: the structure monitor's PostCompleteBuyEvent share of sampled graduations was 0/16 (10-08), 0.316, 0.353, 0.222, 0.368, 0.316, 0.150, 0.176, 0.294, 0.263, 0.200, 0.000 (10-19), 0.105, 0.350, 0.000, 0.150, 0.100, 0.105, 0.150, 0.050, 0.053, 0.222, 0.474 (10-29), 0.316 (10-30). Samples are about 19 graduations a day, so the share is noisy, around 0.2. Most completions still use the old capped buys. Whether the synthetic ones are held by a few bots (or by launchers' own bundles) is unknown. That is the counts test of `p95-synthetic-completer` (QP ruling on #1055 needed for the enumerator).
- Pricing October synthetic pools is sealed to 2026-11-14T00Z (EXP-024 Look-2: V-range pools, first 360 s). The proxy below needs no October data.

### 2.2 Holder rewards: counts-only probe (public RPC, 17 calls, 2026-10-30T15:16Z)

Script `/data2/mal/scratch/edge-hunt-1030/scripts/holder_probe.py`, output `/data2/mal/scratch/edge-hunt-1030/rpc/holder_probe.json` (scratch, off-repo; volume-accumulator read: `gva_probe.py` / `gva_probe.json` in the same folders).
- `Global` (`4wTV1Ymi...`): `holder_reward_claim_authority` = `HckQ93Xq...`, `is_holder_reward_enabled` 1, `max_curve_depth` 2.
- The authority's latest 1,000 signatures span 12 s (block times 1793373387 to 1793373399). 25 succeeded and 975 failed.
- Sampled successful txs: each pays one mint's holders in batches of 6-8 recipients. Totals per event were 0.000001 to 0.000993 units of the coin's quote (two mints; both quotes were non-SOL tokens). Each event appears twice (log and self-CPI), so there are 11 distinct events in 12 txs.
- Reading: a high-frequency drip of tiny batches, with amounts computed off-chain. No payout moment exists to buy ahead of, and a holder cannot trigger one (the authority signs). The carry version (`p81-holder-carry`) is a different question and stays as it is.

## 3. Ideas (one library row each, added on this branch)

| Id | Mechanism | Trades/day x stake x net edge (judgment unless cited) | Latency need | What proves or kills it fast |
| --- | --- | --- | --- | --- |
| `eh1030-kcurve-boost-ride` | first-in entry at the pool's opening reserves (k = -1, the synthetic completer's seat) and at k = 0..12, hold into BOOST, exit s0 + 330 s, organic V-range graduations | if k = -1 works: 20/day x 0.25 SOL x +3% = 0.15 SOL/day; ceiling set by how many completions MAL can win (unknown) and by impact (about 0.6% round trip at 0.25 SOL on about 85 SOL quote) | none for the proxy; live, the completion must land before the natural crossing buy | proxy k-curve on 4 exploration blocks (PREREG): KILL if k = -1 is not positive on all deciding sub-cells |
| `eh1030-h5-subsecond-entry` | H5/Q50 frozen rule entered at 0.3-1.0 s instead of 1.3-1.9 s via free path changes (in-process decision, processed or gRPC feed, SWQoS-only send) | gain at most about +0.75 to +2 pp of stake; at about 16 triggers/day (10-09 shadow count, stale) x 0.25 SOL x 1.25 pp = 0.05 SOL/day before tips | sub-second | latency sweep of the frozen rule on exploration blocks; KILL the speed work if 1.9 s to 0.5 s adds < +0.5 pp under either fail model |
| `eh1030-paid-speed-track` | buy gRPC + Sender/Jito + co-location to reach migration entries at k 2-4 | negative: the k 3-5 band is negative in both periods and k 1-2 was negative in September; cost 0.008-0.13 SOL/day | k 0-2 needed, not reachable | desk verdict NO-GO; revive only if the k-curve test shows the mean positive at k >= 3 |
| `eh1030-holder-reward-drip` | buy ahead of holder-reward payouts | 0: no discrete payout | n/a | probed: continuous drip, authority-signed, tiny batches |
| `eh1030-boost-volume-regime` | BOOST's fixed budget is a larger share of post-migration flow when PumpSwap volume halves (10-28/29) | not a trade; a regime signal for sizing the BOOST family | n/a | 4-block check: H5 block means +26.7 / +11.7 / +5.4 / +6.8% against DefiLlama PumpSwap volume medians 715.2 / 377.2 / 485.6 / 348.1M USD; no usable relation on 4 points, and the highest-volume block had the best edge, so low October volume is not shown to help |

## 4. Ranking for a fast exploration test

1. **`eh1030-kcurve-boost-ride` (PREREG skeleton: `PREREG-KCURVE.md`).** It answers both questions in one run, on exploration blocks only. (A) how much edge exists at k = -1..12 for the BOOST ride; (B) whether the only new mechanical door (synthetic completion) is worth a counts test and a live completion test. One kill line closes the paid-speed question for migration entries.
2. **`eh1030-h5-subsecond-entry`.** A cheap sweep of a frozen rule, and its upside is mostly free engineering. Small: at most about 0.05 SOL/day at October trigger counts (judgment).
3. **`p95-synthetic-completer` counts test** (existing row, not mine). Run it only if rank 1 passes: top-5 signer share and median pool-part `quote_in` of synthetic completions over 3 UTC days, enumerator under a QP ruling.

## 5. Rules that block without protecting gate validity or money (the owner asked for these)

- **hunt23 RULES Amendment 1 item 3 (360-s pool age on every V-range rule):** it protects the EXP-024 Look-2 seal, so it is not arbitrary. But it blocks every first-seconds BOOST rule from becoming a forward book until 2026-11-14T00Z. Proposed change: write now that the 360-s requirement lapses automatically at the Look-2 end state (first 2026-11-14T00Z), so a rule that passes the k-curve test can register its forward clock for 11-14 without another amendment.
- **DEC-031 §1.4 pins 505,000 lamports per send** while 155,000 lands as well (`feearm-landing-2026-10-23.md`). This is conservative, so it does not bias a book upward. It does charge about 0.69 million lamports more per round trip than measured (0.28 pp of 0.25 SOL). Proposed change: allow new books to declare 160,000 with the parity proof that note names. This is not urgent for this lane's test, which reports both.

## 6. Not measured

- No price path or outcome of any pool, on any block. The k-curve and the H5 sweep are skeletons, not results.
- MAL's landing with Sender, Jito or gRPC: not measured. Vendor figures only.
- Who wins synthetic completions, and how often MAL could win one.
- Holder-reward weighting (balance snapshot or time-weighted): not needed after the drip finding, and not measured.
- SOL/USD: the $200 used for monthly costs is an assumption.
