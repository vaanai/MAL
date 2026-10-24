# P16 DBC graduations (2026-10-24)

Screen numbers on 3 exploration days (DBC 2026-10-14..16), not evidence, no gate. No sentence here says a strategy makes money. Rules: DECLARE.md (sha256 in SHA256SUMS.txt; "Declared at 2026-10-24T06:20:15Z",
written before any price; erratum 1 before pricing; erratum 2 after a loader bug, only counts seen). D1 hours [10-17T00, 10-24T00) never counted, screened or priced (code refuses them; post-migration history cut at 2026-10-17T00; one exception: see Disclosures below).
Priced run: job #1515 (identical rules to #1514, plus entry-impact distribution and a post-hoc impact diagnostic); #1511 was a loader bug (counts only read), #1512 crashed on a missing attribute (no price).
Numbers: grad.json. Baseline check: the P14 full-loss line of every strand cell reproduces screens_a2fix.json to the last digit.

## Migration decode (validated on real transactions)
- DBC `MigrationDammV2` is a separate transaction from the `curve_complete` event (6,541 completions, 6,458 migration txs in the window). It creates the pool in Meteora DAMM v2, program `cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG`.
- Pool address: the inner DAMM `initialize_pool_with_dynamic_config` instruction has the pool at accounts[7] (accounts[6] is the constant pool authority, the same for every pool: the P11 index lesson); 109 migrations use the plain
  `initialize_pool` variant (pool at accounts[6]). Both equal the DBC instruction's accounts[4] on every resolved migration (index_ok true for all 6,458 resolved; 0 unresolved after the fix). The DBC virtual pool is DBC accounts[0].
- Swaps: `EvtSwap2` self-CPI (180-byte payload, every one of the 1,568 probe events and no old `EvtSwap`). Reproduction: first swap from the init sqrt price reproduces quote and base liquidity to 1e-7; swap-to-swap quote-leg vs base-leg L ratio within 1% for 100% and within 1e-4 for 99.0% of 1,556 consecutive swap pairs in 5 probe pools (same math as DBC: constant L on sqrt price). Fee seen: 10 bps of gross in the probe pools (20% protocol share), fee mode 0 mostly.
- Tests: tools/test_damm_decode.py (real-tx fixtures), tools/test_damm_grad_price.py (rule walk).

## Fetch (credits)
- Probe: 5 pools, 1,250 assumed credits (manual script, not committed to the tool; 5 getTransaction + 12-page cap per pool).
- Job #1492 (killed at 615,680 counted after I found the plain-initialize variant) + #1506 resume: **999,970 counted credits** (assumed 10 per getTransaction, 50 per gTFA page; Helius billing not measured), 8 rps, one flock slot, hard cap 1,000,000, resumable.
  6,458 migrations resolved (6,458 x 10 = 64,580), 2,288 pools fetched (682 strand pools first, 659 of them linked, then 1,629 in sha256 order = 25% of the non-strand migrations), 42 truncated at the 40-page cap. Kill of #1492 lost up to 25 pools of accounting (2 pools were re-fetched).
- Output /data2/mal/venue-tape-backfill/meteora_damm_v2/pools/pool-<pool>.jsonl.zst (venue_tape schema, venue meteora_damm_v2; checkpoint.json). Per-pool cost: strand (hot) pools ~480 credits, the rest ~230.
- Not fetched: 4,170 migrated pools (cap). Strand cells have 12-18 strand positions each with no DAMM link (pool not fetched / migration outside the window): they keep the full loss.

## (i) Strand re-pricing (deciding leg 1.9 s, 0.25 SOL, rent 0, 160k sends; flat / pressure mean SOL per attempt)
| cell | attempts | stranded & filled | re-priced | P14 full loss (flat / press) | re-priced (flat / press) | ex-top-3 flat | ex-best-day flat | CI90 lo flat | days positive | label |
|---|---|---|---|---|---|---|---|---|---|---|
| m4 | 741 | 194 | 176 | -0.0347 / -0.0329 | +0.0224 / +0.0179 | +13.3 | +7.7 | +0.0143 | 3/3 | MARGINAL |
| m6 | 654 | 193 | 176 | -0.0413 / -0.0392 | +0.0233 / +0.0184 | +12.4 | +5.9 | +0.0152 | 3/3 | MARGINAL |
| m10 | 369 | 5 | 0 | +0.0212 / +0.0164 | unchanged (5 unlinked strands) | +5.2 | +3.6 | +0.0108 | 3/3 | MARGINAL |
| ms30 | 442 | 230 | 216 | -0.1099 / -0.0962 | +0.0062 / +0.0047 | +2.0 | +0.5 | +0.0009 | 2/3 | MARGINAL |
| ms50 | 420 | 243 | 231 | -0.1223 / -0.1084 | +0.0002 / +0.0000 | -0.3 | -1.0 | -0.0056 | 1/3 | FAIL |
| ms70 | 866 | 642 | 627 | -0.1617 / -0.1363 | -0.0064 / -0.0051 | -6.1 | -4.6 | -0.0106 | 0/3 | FAIL |
Per-day flat totals re-priced (10-14 / 10-15 / 10-16): m4 +8.87 / +5.34 / +2.39; m6 +9.40 / +3.85 / +2.01; ms30 +2.18 / +1.02 / -0.48; ms50 +1.06 / -0.59 / -0.37; ms70 -2.35 / -2.23 / -0.98.
Mean re-priced strand trip: m4 +0.031, ms30 +0.027, ms50 +0.010, ms70 +0.00004 SOL (stake 0.25). Exits on DAMM: m4 time 82 / sl 39 / trail 55; ms30 tp 213 / sl 3; ms50 tp 222 / sl 9; ms70 tp 565 / sl 61.
The first DAMM print is a median 11% above the decision spot (m4/ms30; 8% ms50, 13.5% ms70): the migrated pool starts at or above the last curve price and the stranded positions are carried into that.
Reading: the strand assumption does decide those cells (the P14 note). With real DAMM prices m4/m6 (and ms30 narrowly) flip sign. The m4/m6 flip comes from 176 positions that entered an early-momentum rule and ride the migration jump.
The P14 last-price bracket (m4 +0.031, m6 +0.032, ms30 +0.011, ms50 +0.010, ms70 +0.009) was optimistic against the real DAMM prices in all five cells (re-priced +0.022, +0.023, +0.006, 0.000, -0.006).
Open: (a) no competition for the migration/first-DAMM-print sell (snipers buy there too; our sell is the exit, the first print is at +11%); (b) the 1.9 s sell latency is measured on pump.fun stacks only; (c) 3 days of one regime; (d) the flipped cells are 2 of 6 re-priced and
12 + 3 cells declared; ex-top-3 and CI are in-sample trade-level; (e) strand pools unlinked or not fetched stay full loss (conservative); (f) price units: DAMM spot is (S/2^64)^2 like DBC, ratios exact, no decimals factor involved.

## (ii) D5 post-graduation hold
Declared universe U5 (0.25 SOL buy moves the spot <= 2% at the landing state): migrated pools are shallow. Entry impact of a 0.25 SOL buy over the 1,629 sampled non-strand pools: 0.5% of pools <= 2%, 47.1% <= 5%,
47.2% <= 25%; q10 4.4%, q25 4.6%, median 307%, q75 1.7e7 (the half of the pools with a tiny or broken liquidity state). Declared attempts: g1 6, g2 8, g3 8 in 3 days (<3 per day): **dropped unpriced** per DECLARE.
| cell | declared U5 attempts | per day |
|---|---|---|
| g1 tp +50% / 23 h | 6 | 3 / 2 / 1 |
| g2 tp +25% / 6 h | 8 | 3 / 2 / 3 |
| g3 time 1 h | 8 | 3 / 2 / 3 |
POST-HOC diagnostic (not declared, never a label; impact cap 5% or 10% or 25%, same pricing; 653-741 attempts, ~250/day; random non-strand sample): 
| cell (cap 5%) | attempts | flat mean | pressure mean | ex-top-3 flat | ex-best-day flat | CI90 lo | per-day flat | buy / sell fee bps |
|---|---|---|---|---|---|---|---|---|
| g1 tp+50%/23h | 653 | +0.0084 | +0.0063 | +4.6 | -9.2 | -0.0011 | -5.1 / -4.1 / +14.7 | 11 / 11 |
| g2 tp+25%/6h | 740 | -0.0075 | -0.0072 | -6.3 | -6.9 | -0.0142 | -3.0 / -4.0 / +1.4 | 11 / 19 |
| g3 time 1 h | 726 | -0.2037 | -0.1704 | -147.9 | -98.9 | -0.2046 | -49.8 / -49.2 / -49.0 | 12 / 37 |
Caps 10% and 25% add one attempt (the 4.4% group is the whole set). g1's mean is carried by one day (10-16 +14.7, the other two negative) and has CI lo below 0: FAIL by the P14 label. In the sampled pools the median price 1 h
after migration is about 2e-5 of the price at +2 s (82% fall by more than half): time-only holds are a loss of about 80% of the stake; this price-collapse figure is unexplained here (part of those pools are the broken-liquidity half) and was not validated beyond the fit
checks above, so g3's number is a flag, not a result.

## Not done / disclosure
- No quant-proof / reviewer pass yet. Not a promotion candidate; a DBC book has no executor, no measured migration-time leg, and the DAMM pressure-fail features (ssb/nearby) for strands are the DBC entry features.
- The P14 cells are unchanged; only the strand leg differs. The multiplicity is 26 cells: 23 DBC cells screened (P14 + P17) plus 3 D5 cells (QP count; an earlier draft said 12 + 3). m4 is the best of those 26, a winner's-curse pick.
- Credits: 999,970 counted (+1,250 probe, + a handful of manual getTransaction/getAccountInfo calls while building the decoder, < 100 credits assumed).

## QP conditions (2026-10-24, head 19cfb7ae)
- Gate: not cleared, not claimed. 0 out-of-sample trades, 3 UTC days (gate needs 5), all in-sample on the Swap2-era exploration days 10-14..16. m4 re-priced: flat n 741, mean +0.0224, CI90 lo +0.0143, ex-top-3 +13.3, ex-best-day +7.7; pressure +0.0179 / CI90 lo +0.0111. m6: flat +0.0233 / CI lo +0.0152; pressure +0.0184 / +0.0116. Winner's curse: 2 of 6 re-priced cells, best of 26.
- m4 and m6 share 193 of their 199 strand positions and their DAMM exit counts match exactly (82/55/39): one positive, not two. 10-16 holds only 52 of the trades. Neither has been priced on a Swap3 pool.
- m4/m6 are D1 variants. Hours [10-17, 10-24) are spent for every D1 variant (QP-RULING-1024-dbc-m10-swap3). m4/m6 can be reached only if m10's pre-registered forward read (>= 2026-11-01T00:10Z) PASSes. A FAIL or NOT_DECIDABLE there ends all D1 on DBC. There is no holdout-read revive path.
- Disclosure, smoke file: /data/mal/hunt-1008/push-1016/hunt28/p16/smoke/grad.json (06:53:11Z) holds re-priced strand P&L for m10 and ms30, priced on partial fetch data before "the one pricing run" (#1515). It is not listed in SHA256SUMS or above. No number in this report comes from it; it is not cited, and this report does not record that the lane read it.
- Disclosure, probe: the uncommitted probe3.py used t0+86400 with no 10-17 cut, so probe_raw.json holds 2 DAMM v2 txs at or after 10-17T00Z (last 2026-10-17T01:36:33Z). That is decoder validation on the DAMM venue, not DBC hours and not a D1 count, screen or price. Nothing at or after 10-25T00Z was touched.
