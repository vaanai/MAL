# P34 COST-OFFSET desk audit (2026-10-26)

Desk and public web only. 0 Helius credits, no tape, no pricing, no job, no host. No sealed output was opened. Lane definition: docs/research/p32-survivor-audit/AUDIT.md section 3, Lane 3 (origin/claude/p32-survivor-audit). Every number below is either quoted from a named source with the date it was read, or marked UNVERIFIED.

Read date for all web sources: 2026-10-26 (UTC, from `date -u` at 16:57-17:01). The official pump-public-docs repo shows no commit date in the page view; it reports 41 commits. Treat its doc dates as undated unless stated.

## 0. Bottom line

No programme reachable by a plain non-custodial paper wallet is verified to offset the round trip by 0.3 pp or more.

- The verified reachable offsets are about 0 pp. The one live trader rebate found (Frontier Traders) pays 0.001% of volume, which is 0.1 bp per side, and needs $10M of 30-day volume.
- Priority fees and Jito tips are costs, not offsets. At 0.25 SOL they are about 0.0001-0.13 pp round trip (base signature fee 0.004 pp round trip).
- The largest candidate offset is PumpSwap fee tiers. Official docs confirm a tier schedule exists, but the table itself is an image on the official page, and the numbers come from a third-party decoder that reads chain config. Whether any lab pool sits in a cheaper tier is UNVERIFIED. The tier that matters is set by market cap, and FLOOR's own rule (real quote <= 0.5 SOL) selects small pools, which are the expensive tiers.
- Kill rule applied as written: the net reachable offset I can verify is under 0.3 pp after fixed costs, so the verdict is **close the lane**. One bounded counts-only check could reopen it (section 4, item 1). That check is not run here.

## 1. Official and secondary terms, by programme

### 1a. PumpSwap LP, protocol, creator fees and tiers (item 1)

| Source | Read | What it says |
| --- | --- | --- |
| https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/PUMP_SWAP_README.md (official, pump-fun repo) | 2026-10-26 | LP fee 20 bps and protocol fee 5 bps, "charged on each buy and sell". Total 25 bps is my arithmetic. No tier table, no market-cap thresholds, no dynamic-fee text, no date. Admin can change fees with `update_fee_config`. |
| https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/FEE_PROGRAM_README.md (official) | 2026-10-26 | "We will use the following fee tiers starting from Monday:" followed by an image `fees.png`. The text gives one date: "On Monday, September 1, 20:00 UTC, these 2 accounts will become mandatory" (year not stated in the page; 1 Sept 2025 was a Monday). Tier thresholds and bps are in the image. Tier selection: first tier below its threshold, otherwise the highest tier whose threshold is met. Market cap for a canonical pool is `quoteReserve * baseMintSupply / baseReserve`. |
| https://raw.githubusercontent.com/pump-fun/pump-public-docs/main/docs/PUMP_SWAP_CREATOR_FEE_README.md (official) | 2026-10-26 | `GlobalConfig::coin_creator_fee_basis_points` is "currently set to 0". No creator bps value, no tier, no split. Mainnet date not stated. |
| https://github.com/hrekokovgmmek-lab/pumpswap-fee-decoder (third party, reads chain) | 2026-10-26 | Tier table "read from chain" from fee config 5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx. Shows 0 SOL: LP 2 / protocol 93 / creator 30 = 125 bps; 420 SOL: 20 / 5 / 95 = 120 bps; 1,470 SOL: 20 / 5 / 90 = 115 bps; 98,240 SOL: 20 / 5 / 5 = 30 bps; 25 tiers, middle rows not shown. **The column alignment looks wrong** (LP 2 and protocol 93 at 0 SOL are implausible next to the official 20/5), so treat the per-tier split as UNVERIFIED. The endpoints (125 bps to 30 bps total) match the A27 row and the creator-fee guide. |
| https://github.com/nirholas/pump-fun-sdk/blob/main/docs/fee-tiers.md (third party SDK) | 2026-10-26 | Confirms tiers by market cap, `FeeTier` with `marketCapLamportsThreshold`, and that LP/protocol/creator bps are per tier. No numbers, no official citation. Says a non-canonical pool (pool.creator not the pump pool authority PDA) charges about 0.30%: 0.05% protocol and 0.25% LP, no creator. **Non-canonical 0.30% is secondary only, UNVERIFIED.** |
| https://madeonsol.com/blog/pumpfun-revenue-sharing-pumpswap-fees-creators (third party, dated 2026-05-25) | 2026-10-26 | "Every swap on PumpSwap incurs a 0.25% fee"; says the protocol/creator split "has evolved"; no tier thresholds; cites no official doc. |

**Pump's own docs do not give a tier table in text.** The official text confirms that a market-cap tier schedule exists from 2025-09-01 and that the top-tier LP and protocol values are 20 and 5 bps. The tier values themselves are UNVERIFIED.

Dynamic Fee V1 (secondary): Bitget repost of a Jinse Finance report dated 2025-09-02 says creator fees are "tiered based on market capitalization", higher cap lower creator share. No numbers. Not a primary source.

**Answer to "A27 says the fee falls from 1.25% toward 0.30%":** the 0.30% end matches the official top-tier total (20 + 5 bps LP and protocol, plus a 5 bps creator line in the decoder). The 1.25% start is the decoder's 0-SOL total (125 bps). The 1.25% figure is also quoted as the bonding-curve fee by Coinpaprika (see 1b), so the A27 start may mix two products. UNVERIFIED for the 0-SOL tier.

### 1b. Bonding curve fees, creator fees, cashback, rewards (item 2)

| Programme | Who receives | Plain paper wallet can get it? | Round-trip size | Wash-like volume? |
| --- | --- | --- | --- | --- |
| Bonding curve total fee | pump.fun; creator | n/a (a cost) | Conflicting: 1.25% (Coinpaprika citing pump.fun/docs/fees, undated); 1% (MadeOnSol 2026-05-25; old Axios table). **UNVERIFIED** (https://pump.fun/docs/fees returned HTTP 403 on 2026-10-26). | n/a |
| Creator fee on bonding curve | creator vault | n/a (a cost to traders) | Official `Global::creator_fee_basis_points` "set to 0" (PUMP_CREATOR_FEE_README, official, mainnet date "Monday, May 12", year not stated). **Current creator fee on SOL pairs: official text says 0 standard schedule (PUMP_SWAP_CREATOR_FEE_README and create_v2 notes).** Not an offset. | n/a |
| Cashback coins (deprecated) | the buyer, from the creator fee | Only on cashback mints created between about 2026-02-17 and the deprecation (Cointelegraph, secondary). Official doc: "Cashback is deprecated: create_v2 rejects is_cashback_enabled = [true]. Existing cashback coins keep trading, and accrued cashback stays claimable." CLAIM_CASHBACK.md (official, read 2026-10-26) gives no bps or share and does not say which trades or pools. | **UNVERIFIED.** Upper bound only: if the whole creator line (30 bps at low cap per the decoder) came back on both sides, about 0.6 pp round trip. Official text suggests creator fee 0 on SOL pairs, so probably 0. | No. Trading the mint is enough. But the mint list is thin and cashback is closed to new mints. |
| Holder rewards coins (`is_holder_reward`) | holders (pump.fun pays out) | Not a trader rebate. Creator fee goes to holders of that coin. HOLDER_REWARDS_README (official, undated): "not paid to a creator wallet"; no trader rebate. | 0 for a trader | n/a |
| Sweeps (SWEEP_FEES.md, official) | protocol, creator vault, holder vault. Buyback paid in the trade. | No trader payout. Sweeps are permissionless, the payer only covers rent. | 0 | n/a |
| Official release notes (README.md, read 2026-10-26) | n/a | Dated (year not stated): September 30, negative virtual quote reserves (PumpSwap); October 13, depth-2 pump-coin quotes. No trader rebate or reward line. | 0 | n/a |
| PUMP-token volume rewards | would be traders | Not shown. Crypto Briefing (2025, secondary) reported SDK code for volume-based $PUMP rewards, "rumored", features "not yet activated". No official 2026 announcement found. | UNVERIFIED | **OWNER POLICY CHECK. Stopped on this item.** A volume reward is an incentive-seeking test, and anti-wash terms apply. |

### 1c. Meteora DBC and DAMM v2 (item 3)

Official DBC pages returned HTTP 404 (`docs.meteora.ag/core-products/dbc/formulas` and `.../fee-scheduler`) or are not fetched, so DBC numbers below are secondary or UNVERIFIED.

| Item | Source | Read | Value |
| --- | --- | --- | --- |
| DAMM v2 base fee minimum | https://docs.meteora.ag/core-products/damm-v2/fees/overview.md (official) | 2026-10-26 | Minimum 1 bp (100,000 numerator, denominator 1e9). Maximum up to 99% (version 0 pools 50%). |
| DAMM v2 base modes | same | 2026-10-26 | Time scheduler (linear or exponential decay), market-cap scheduler, rate limiter (deprecated for new configs). Exact decay parameters not in the page; UNVERIFIED. |
| DAMM v2 splits | same | 2026-10-26 | Protocol fee 20% of trading fee; referral 20% of protocol fee when a referral is present; LP fee is the rest; creator share of LP fee is configurable. No trader rebate. |
| Dynamic fee formula (on the DBC formulas page) | https://docs.meteora.ag/core-products/dbc/formulas (official, 2026-10-26) | 2026-10-26 | Dynamic fee = ceil((volatility accumulator x bin step)^2 x variable fee control / 1e11). Adds cost in volatile moments; not an offset. |
| DBC formulas | https://docs.meteora.ag/core-products/dbc/formulas (official, read 2026-10-26, page returned formulas) | 2026-10-26 | Total fee = base + dynamic, capped at 99%. Base is a scheduler (flat, linear or exponential decay) or rate limiter. Protocol 20% of trading fee, referral 20% of protocol. Creator share configurable 0-100% (secondary quick-launch example). No DBC default fee in bps found. **UNVERIFIED.** |
| DBC trader rebate | none found in any Meteora page read | 2026-10-26 | None. |
| DBC fee-clock already tested | p32 audit survivor table (origin/claude/p32-survivor-audit): "Fee clock and floor (P21) both FAIL." | n/a | Decay offset on DBC is already in the tape the m10 pricing used. Offset 0 for m10 unless its config differs. |

Note: the p20-incentive-farming row says "DAMM v2 10 bps". The official DAMM v2 minimum is 1 bp. The 10 bps figure is not supported by the official page. The lab does not trade DAMM v2 (p32 venue table), so this does not change any book.

### 1d. Priority fee and Jito tip norms (item 4)

| Item | Source | Read | Value |
| --- | --- | --- | --- |
| Base fee | https://solana.com/developers/guides/advanced/how-to-use-priority-fees (official) | 2026-10-26 | 5,000 lamports per signature, 50% burned. |
| Priority fee | same | 2026-10-26 | `ceil(compute_unit_price * compute_unit_limit / 1e6)` lamports, paid to the leader. Price is micro-lamports per CU. Default CU limit 200,000 per instruction; max 1,400,000 per transaction. |
| Jito minimum tip | https://docs.jito.wtf/lowlatencytxnsend/ (official) | 2026-10-26 | "Jito enforces a minimum tip of 1000 lamports for bundles." Bundles up to 5 transactions. Guidance: sendTransaction uses a 70/30 split of priority fee and tip; sendBundle uses only the tip. getTipAccounts returns tip accounts. Only date in page: example tip-floor 2024-09-01. |
| Typical clearing price in 2026 | none verified | n/a | UNVERIFIED. Secondary: Jito tips over 60% of priority-fee volume (RPCFast, May 2026). `getRecentPrioritizationFees` not run (public-desk only, no RPC call made). |

Pricing at a 0.25 SOL stake, using the lab's flat 160k CU per send (p32 audit):

| Cost | lamports per send | pp per send | pp round trip (2 sends) |
| --- | --- | --- | --- |
| Base fee, 1 signature | 5,000 | 0.0020 | 0.0040 |
| Priority 1,000 uL/CU | 160 | 0.000064 | 0.00013 |
| Priority 100,000 uL/CU | 16,000 | 0.0064 | 0.0128 |
| Priority 1,000,000 uL/CU | 160,000 | 0.064 | 0.128 |
| Jito minimum tip | 1,000 | 0.0004 | 0.0008 |

Even at 1 lamport per CU, fixed send cost is under 0.15 pp round trip at 0.25 SOL. These are costs to keep low, not offsets. The lab's own send-cost estimate (p25-alpenglow-send-cost, cited in the p32 audit) was not re-read here.

### 1e. Programmes that pay traders, not creators or LPs (item 5)

| Programme | Who receives | Plain non-custodial wallet at 0.25-1 SOL? | Round-trip size | Wash-like volume needed? |
| --- | --- | --- | --- | --- |
| Frontier Traders VIP taker rebate (Solana Foundation) | Traders on Solana venues, tracked network-wide; venues include pump.fun, Phantom, Jupiter, Raydium, Orca | Enrolment is open (paste a wallet). VIP 1 needs at least $10M 30-day volume; rebate **0.001%** per month. Higher tiers: VIP 2 0.002% at $100M, VIP 3 0.003% at $500M, VIP 4 0.004% at $2B, VIP 5 0.005% at $5B-$10B. Read of https://solana.com/news/introducing-frontier-traders (2026-10-26). **Date conflict**: page says "live until June 19th"; Solana Compass says launched September 14. | About 0.002 pp round trip at VIP 1 (0.1 bp per side). Higher tiers give 0.002-0.005% per month. | Not wash-like, but volume is about 66,700 SOL per month at $150 for VIP 1 (about 266,000 trades at 0.25 SOL). Not reachable at paper scale. |
| Frontier Traders maker track | Spot makers | Two-sided quoting on a listed asset; $15,000 per asset per month, split by volume; trades under $250k and within 2.3 bps of NBBO mid | Not a trader offset; not a non-custodial bot strategy | Requires making a market. Not pursued. |
| Trojan (Solana terminal) | The user, as a points/cashback share of the terminal's own fee | Custodial terminal. Outside MAL's non-custodial key rules and the paper-only setup. Terminal fee 0.9% per trade, cashback 20% of own fee (Bitrue promo, MadeOnSol secondary). | About 0.18 pp per side, 0.36 pp round trip if the 20% is real | Not wash-like, but terminal account is custodial and points are promotional. UNVERIFIED. **Stopped: not a non-custodial route.** |
| Axiom Pro (Solana terminal) | The user, as a rebate of terminal fees | Custodial-style terminal; Medium and MadeOnSol secondary; rebate "up to 43%" unverified; sources disagree | UNVERIFIED | Volume-tiered. **OWNER POLICY CHECK** if pursued (custodial terminal and volume-tiered rebate). Not pursued. |
| BullX | n/a | MadeOnSol says it shut down June 2026; single source | n/a | n/a |
| PUMP-token volume rewards | Traders | UNVERIFIED (see 1b) | UNVERIFIED | **OWNER POLICY CHECK.** |

## 2. Offset table (for the FLOOR / Q50 / DBC m10 mapping)

Pp per side unless marked round trip (rt). "Verified" means an official source states it; the number is not in the desk record.

| # | Offset | pp | Applies to FLOOR / Q50(H5) / DBC m10 | Confidence | Next step |
| --- | --- | --- | --- | --- | --- |
| 1 | PumpSwap cheaper tier (canonical pool, market cap at about 98,240 SOL, total 30 bps vs 125 bps at 0 SOL) | 0.95 per side (1.90 rt) vs the 0-SOL tier; about 0.05-0.10 per side between 115 and 120 bps tiers | **FLOOR: no** (its rule needs real quote <= 0.5 SOL, which is a small pool and therefore an expensive tier). **Q50/H5: UNVERIFIED** (pool caps not counted). **DBC m10: no** (different venue). | Low. Tier existence verified (official FEE_PROGRAM_README, 2025-09-01); numbers from a decoder with a column alignment that looks wrong. | Read FeeConfig from chain (public RPC, 0 Helius) and count Q50 trigger pools by market cap, counts only. |
| 2 | Non-canonical PumpSwap pool, flat 0.30% (no creator line) | about 0.85-0.95 per side vs a 115-125 bps tier (about 1.8 rt) | **UNVERIFIED** for all three. Applies only if a book trades non-canonical pools (pool.creator not the pump pool-authority PDA). | Very low. Third-party SDK only. | Count canonical vs non-canonical among FLOOR and Q50 triggers (arena counts; creates and counts only). |
| 3 | Cashback on existing cashback mints | up to about 0.3 per side (0.6 rt) if the whole creator line came back; official text suggests 0 on SOL pairs | Not FLOOR-specific. **UNVERIFIED** for Q50 and m10 (no cashback mint in the DBC config read). | Low. Mechanism confirmed deprecated for new mints; size not in any official doc read. | List cashback mints created before the deprecation and count how many FLOOR/Q50 triggers hit them. |
| 4 | Frontier Traders VIP 1 rebate | 0.001% of volume = 0.001 pp per side, about 0.002 rt | None (volume needed is about 66,700 SOL per month at $150) | High (official page read). | None. Closed. |
| 5 | Terminal fee cashback (Trojan, Axiom) | up to about 0.18 per side, unverified | None: custodial terminal, outside non-custodial rules | Very low | Stopped. OWNER POLICY CHECK if the owner wants it considered. |
| 6 | PUMP-token volume rewards | UNVERIFIED | Unknown | Very low | Stopped. OWNER POLICY CHECK (incentive-seeking). |
| 7 | DBC fee decay or creator share | 0 for m10 (fee clock already FAIL in P21) | DBC m10: 0 | Medium that the fee clock is already in the tape; it was tested and failed | None. |
| 8 | DAMM v2 cheaper base fee (1 bp minimum) | about 0.24 per side vs a 25 bps assumption (the p20 row's DBC figure), if a book traded DAMM v2 | None: no lab book trades DAMM v2 (p32 venue table) | High on the 1 bp minimum (official). | None unless a DAMM v2 book is proposed. |
| 9 | Priority-fee or Jito-tip saving | negative (a cost); at most 0.13 rt at 1 lamport per CU | n/a | High on the formulas; clearing price UNVERIFIED | Keep priority price low; use the lab's send-cost estimate. |
| 10 | Holder rewards or sweep payouts | 0 to traders | 0 | High | None. |

**Fixed-cost check.** Verified reachable offsets total about 0 pp (row 4 is 0.002 rt). Costs of priority fee and tip stay under 0.15 pp rt at 0.25 SOL. Net reachable verified offset after fixed costs: below 0.3 pp.

**Verdict: close the lane.** Reopen only if the row 1 or row 2 counts show that a material share of FLOOR or Q50 triggers sit on pools at least 0.3 pp cheaper per side than the lab's assumed fee. That check is counts only and is not run in this lane.

## 3. Pricing check (not an offset, but affects the 2.4% round trip)

- The latest official PumpSwap README gives 25 bps flat (LP 20, protocol 5). If a book was priced at 25 bps flat, it understates a low-cap pool by roughly 0.9-1.0 pp per side, if the decoder tiers are right. The 2.4% round trip the lab quotes is consistent with about 1.2% per side, which is the low-cap tier. The repo does not show the lab's fee input (searched docs/ and LAB_STATE.md; no fee constant found). **UNVERIFIED: the lab should confirm which fee its PumpSwap pricing uses.**
- The DBC pricing uses the per-pool config (dbc_config per the DESIGN note). Fee fields not checked here.

## 4. Repo records (docs/library/ideas.jsonl, read 2026-10-26)

- **lib-a-A27** (PumpSwap fee tier falls with market cap; status untested; source STRATEGY-TREE.md row 7c, dated 2026-10-08): the mechanism is now confirmed as an official tier schedule that starts 2025-09-01 (FEE_PROGRAM_README). The numbers 1.25% to 0.30% remain secondary. Its own note, "few pools reach the cheap tiers", is consistent with the FLOOR rule (small pools only). **What is new:** the official source exists and the tier table is an image. The row can stay untested.
- **p20-incentive-farming** (untested, guess 0.07 SOL/day): the premise "DAMM v2 10 bps" is not supported (official minimum 1 bp). The revive trigger (a live programme with official terms and reward above the round trip) is **not met**. The only official trader rebate found is 0.001%. **What is new:** Frontier Traders read from the official Solana Foundation page; it is far below the trigger.
- **web-capacity-1** (PumpSwap LP fee capture): its figures (LP 0.20% above 420 SOL, protocol 0.05%) match the official top-tier LP 20 bps and protocol 5 bps, and a secondary decoder. The "above 420 SOL" boundary is not confirmed; the decoder shows a 420 SOL row with total 120 bps, which does not fit a 420 SOL cliff. **What is new:** official README now shows LP 20 / protocol 5 with no tier table.
- **web-capacity-2** (sandwich competition): no change; the 96% share and 18 of 63 losing attacks are secondary.
- **web-capacity-3** (Meteora DLMM LP flow): no change; DLMM not used by any lab book.
- **web-capacity-4** (post-graduation cross-venue gap): no change; DAMM v2 1 bp minimum noted in section 1c; no measured gap.
- **web-capacity-5** (memecoin perps): no change; no pump-origin perp found.
- **web-capacity-6** (paid market making): no change; Frontier maker track is a separate programme and is not a trader offset.

## 5. Not done, and why

- No RPC call (`getRecentPrioritizationFees`, FeeConfig read, pool counts). Those are counts or public reads that the brief allows only at the next step, and they were not needed for the desk verdict.
- https://pump.fun/docs/fees returned HTTP 403. The bonding-curve fee is therefore UNVERIFIED.
- Meteora docs `/core-products/dbc/fee-scheduler` returned HTTP 404 and `dbc-fee-calculation` returned HTTP 404. DBC default fees are UNVERIFIED.
- The pump-public-docs `FEE_PROGRAM_README` tier image was not readable. The tier table is UNVERIFIED from an official source.
- No seal was touched. Nothing in this audit reads HV4, Q50 output, DBC forward window, meta-capture, or any ledger row.

## 6. Table: offset | pp | applies to FLOOR / Q50(H5) / DBC m10 | confidence | next step

| offset | pp | FLOOR / Q50(H5) / DBC m10 | confidence | next step |
| --- | --- | --- | --- | --- |
| PumpSwap cheaper market-cap tier (1) | 0.95 per side (1.90 rt), only at about 98k SOL cap | no / UNVERIFIED / no | low | FeeConfig read + Q50 trigger pool caps, counts only |
| Non-canonical pool 0.30% flat (2) | about 0.85-0.95 per side, UNVERIFIED | UNVERIFIED / UNVERIFIED / no | very low | canonical vs non-canonical count on FLOOR and Q50 triggers |
| Cashback on existing cashback mints (3) | up to about 0.3 per side, UNVERIFIED | no / UNVERIFIED / no | low | cashback mint list and trigger overlap |
| Frontier Traders VIP 1 (4) | 0.001 per side (0.002 rt) | no / no / no | high | none; closed |
| Terminal cashback, Trojan or Axiom (5) | up to 0.18 per side, UNVERIFIED | no (custodial, out of scope) / no / no | very low | OWNER POLICY CHECK if raised; not pursued |
| PUMP-token volume rewards (6) | UNVERIFIED | unknown | very low | OWNER POLICY CHECK (incentive-seeking). Stopped. |
| DBC fee decay (7) | 0 (already in tape; P21 FAIL) | no / no / 0 | medium | none |
| DAMM v2 1 bp minimum (8) | about 0.24 per side vs 25 bps, if traded | no / no / no | high (official) | none unless a DAMM v2 book is proposed |
| Priority fee and Jito tip (9) | cost, 0.13 rt max at 1 lamport per CU | cost | high on formulas | keep price low |
| Holder rewards and sweeps (10) | 0 to traders | 0 | high | none |

**Kill-rule result:** net reachable verified offset below 0.3 pp after fixed costs. **Close the lane**, with the row 1 and row 2 counts as the only reopen check.

**Owner policy:** stopped on PUMP-token volume rewards (row 6) and on Axiom volume-tiered rebates (row 5). No wash-like volume is proposed.

## 7. Items for the manager

1. Confirm which PumpSwap fee the lab's pricing uses (section 3). If it is 25 bps flat, the cost is understated at low caps.
2. Decide whether to run the counts-only check for rows 1-3. It uses public RPC and arena counts only, no Helius credits, no seal.
3. Owner policy check on PUMP volume rewards and on any custodial terminal rebate, before any test.
