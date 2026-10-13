# Review: pump.fun FEES program redeploy, 2026-10-12T17:40:24Z

Reviewer: Claude subagent (mechanism review, DEC-024 Amendment 7 item 2, items 1-5 only; item 6 is the manager's), 2026-10-13T07:36Z.
Sources: public RPC `api.mainnet-beta.solana.com` (0 credits, about 890 attempts), `gh` on `pump-fun/pump-public-docs`, the repo (read-only), `/data/mal/audit-1008/reports/work-md/g_october_structure_check/`.
Outcome-blind: no canary ledger, shadow record, paper twin, forward-paper or walk row, runner file or fill/exit/P&L was opened. No trade outcome was computed. No Helius key or key file was read.
Scripts and raw outputs: `/data/mal/hunt-1008/h5-work/program-upgrade-1013/` (`raw/` holds the JSON outputs and a response cache).

## Verdict

**Item 1 changed (the fees program only). Items 2, 3, 4 and 5 are unchanged.** Every item-4 band is inside (table in section 4; n=40 post-upgrade BOOST pools). No config account differs from its pin. No layout differs. The old fees binary is not on disk, so what the new binary does differently is **not established**. Reported, not judged: one BOOST pool in 40 had a 303 s keeper gap (last slice 635 s); the population shifts in section 5.

**Item 6 (added by the manager, section 6): met with stated limits.** 12 of 12 simulated round trips on post-upgrade pools, 0 simulate errors, 0 fee-bps mismatches, tiers covered [0, 1, 9, 24]. The manager's notes for quant-proof at the end list what is left to its ruling.

## 1. What changed: changed (fees program only)

| Program | deploy slot | programdata sha256 (whole account) | len | vs pin |
| --- | --- | --- | --- | --- |
| pump `6EF8rre…` | 454596459 | `8c8f6aae2324281b…8d8772` | 10,485,760 | equal |
| PumpSwap `pAMMBay…` | 454596406 | `868a875eba7f4dccb…1f6889` | 10,485,760 | equal |
| fees `pfeeUxB6…` | **456132171** | **`3cf9ee9a380c0ced31171802d402551910ceb8c4819ede4ef44aaf63a12dbfac`** | 948,989 | **sha and slot differ**; pin `4757ce83c3a2e197…`, slot 454596501; length unchanged |

- Re-read at ctx slot 456356703-716 from each program account to its programdata (script `s01_programs.py`). Fees ELF-only sha (bytes 45..) `4f2955d0…6b35e`.
- **Deploy tx** `5xTsu4qw4z7t…` (slot 456132171, 2026-10-12T17:40:24Z, no error): inner BPFLoaderUpgradeable "Upgraded program pfeeUxB6…" via Squads `SQDS4ep…` `VaultTransactionExecute`. **Signer `5sFJMw5dC59oKGKA9LgeYQCSmYS6rLeCegxMkcM2Pwby`**, not `6W6qsDbo…` of 10-08 and 10-02. Same Squads program. The tx lists upgrade authority `7gZufwwA…` among its accounts. The signer is a different executing key; I did not look up Squads membership (not checked).
- **Upgrade authority** on all three programdata accounts: `7gZufwwAo17y5kg8FMyJy2phgpvv9RSdzWtdXiWHjFr8` (unchanged). The last upgrade of pump (454596459) and PumpSwap (454596406) is the 10-08 one: PumpSwap was not redeployed.
- **pump Global**: 1,088 bytes (unchanged). First 1,087 bytes sha `6afa5f43e71cf996…522cc6f` = the 10-09 review's value; last byte (max_curve_depth) = 1. Full sha `32729edb5b0dca02…ec450032`.
- **Docs**: head still `2293f9a` (2026-10-08T18:32:13Z); no commit since. `idl/pump_fees.json` last changed in `8cda1fa` (2026-10-07), before this deploy; its sha256 `f111d2e5…` equals the copy the audit fetched on 10-08. The docs and IDL do not describe this deploy.
- **Binary**: not compared. The previous fees binary (sha `4757ce83…`) is not on disk (the audit's `raw/programdata.json` keeps only headers). New binary only: all 30 IDL instruction names and all 35 IDL error messages are present, and no instruction name outside the IDL (plus Anchor's `Idl*` admin set) appears. That says nothing about what changed inside.

## 2. Layouts: unchanged

Blocks: 4 before (456132167-170), 4 after (172-175), 3 mid (456250000-002), 4 recent (456356000-003). 414 / 678 / 230 / 131 successful PumpSwap txs.
- **Executor lists** (`tools/pumpswap_tx.py` `swap_accounts`: buy 26, sell 24, buy_exact_quote_in 26). Top-level instructions with the discriminators `66063d12…` (buy), `33e685a4…` (sell), `c62e1552…` (buy_exact_quote_in) show the same (n_accounts, data length) shapes before and after: sell 23/24 accounts, buy 25/26, buy_exact_quote_in 25/26, data 24-26 bytes. Shapes seen only after the deploy: sell/26 and buy_exact_quote_in/25 with data 24, one inner CPI each, inside the single `MultiHopSwap` tx. Neither is a top-level executor shape.
- **Events**: BuyEvent 489/492/504/507 bytes, SellEvent 441, both before and after. ix_name, V (offsets 401+/392) and creator_fee_unclaimed decoded on 100% of Buy/Sell events: pre 188/188, post 699/699 (`observe/trade_decode.py`).
- **Q**: in single-trade txs, the event quote and base reserves equal the pool's pre-trade token balances (`preTokenBalances`) on pre 180/180, post 643/643. On WSOL-quoted pools quote + V was computed on 146 pre and 482 post events (V < 0 on 0 pre, 7 post).
- **Fees program in PumpSwap's trade path**: PumpSwap calls the fees program by CPI inside buy/sell (the `fee_config` and `FEE_PROGRAM` accounts the executor passes). The call is `get_fees_with_quote_mint` (discriminator `9aed8a5c…`, 2 accounts, 57-byte data) and returns 3 u64 (lp, protocol, creator bps). Before: 163 CPIs, after: 620, all 24-byte returns. Fee-program compute units min 5,688 both sides; median 6,037 before, 5,785-6,051 after. Return triples: 12 distinct before, 24 after (larger sample); not a like-for-like test. **Fee tier equality needs item 6.**

## 3. Fees: unchanged

| account | sha256 now | pin | len |
| --- | --- | --- | --- |
| bonding FeeConfig | `3504fae20640e15bd441257ab118aa96fb4e99190defc06a01aae26b307bf204` | same | 4097 |
| PumpSwap FeeConfig | `d8706882a217e82af619b754e37beedf9338a25787ebec717e7d69d2cf347c9d` | same | 4097 |
| PumpSwap GlobalConfig | `a698009a419d0b8c56ff257626d15cba305c4989a602fa86e65ca252b2c200f4` | same | 949 |

All three equal their pins (ctx slot 456356859-861). The config sha covers the tiers, bps, recipients and the boost flag. It does not cover the redeployed program's fee computation (item 6).

## 4. BOOST: unchanged, all bands inside

Post-upgrade sample = 64 graduations with migrate slot > 456132171, **no pre-deploy graduation counted**: `post_new` 40 (2026-10-13T06:12-07:09Z, slots 456338607-456354425) + `post_early` 24 (10-12T20:27-20:37Z, slots 456177937-456180667). 40 are non-mayhem WSOL with InitBoost, profiled. The first 2.8 h after the deploy are not sampled. Pre-upgrade comparison = 20 graduations 10-12T17:20-17:31Z (13 profiled).

| Check (DEC band) | Post measure | n | Inside? |
| --- | --- | --- | --- |
| InitBoost on >= 80% of non-mayhem WSOL | 40/40 = 1.000 (11/11 early, 29/29 new; monitor 17/17) | 40 | inside |
| Slices: BoostBuyAndBurn, keeper-signed, zero fee | 205/205 slices (7 pools, every slice fetched); pre 88/88 | 205 | inside |
| GlobalConfig 949 bytes, byte 939 = 1, authority unchanged | 949, 1, `HTVZVEQM…2S5r`; sha equals pin | 1 | inside |
| Budget median within 17.585 +-1% (17.409-17.761) | 17.585994 SOL (min 17.585994, max 17.585995); pre 17.585994. Sum of used lamports on the 7 fully fetched pools 17,584,505,288-17,584,505,983 (10-10: 17,584,505,288) | 40 | inside |
| Slices per pool 29-30 | median 29; signature-list counts 28:2, 29:21, 30:12, 31:4, 32:1 (33/40 in 29-30). Exact keeper counts 29,29,30,28,30,29,30 (6/7). Pre: counts 28:3, 29:6, 30:4 (10/13); exact 29,29,30 | 40 | inside on the median; 7/40 pools are individually outside, and 3/13 were pre-upgrade |
| Median slice interval within +-10% of pre median | per pool (last verified - first)/(n-1): post 11.759 s vs pre 11.893 s (-1.1%); pooled consecutive gaps median 12 s vs 12 s (0%), n 1,141 vs 365; September 12.06 s | 40 vs 13 | inside |
| Median last slice in [328, 347] s (4 A3 medians 342, 333, 339.5, 341.5, +-5) | 336 s (min 327, max 635); by sample 335 (n=29), 343 (n=11); pre sample median 337 (n=13); monitor 10-13 332 (n=17) | 40 | inside |

Notes, not judged. The slice count from the signature list is an upper bound (the monitor says so); the exact keeper counts are in the table. One pool (`F1wBbkTh…`, migrated 06:27:19Z) had a 303 s gap between two slices; all 30 slices were keeper-signed and zero-fee, last at 635 s. Slices are signed by the keeper `HTVZ…` (GlobalConfig.boost_authority); the boost-vault PDA is in the instruction accounts of 205/205 and signs by CPI only (a PDA cannot sign the tx). "Zero fee" test: the vault's WSOL change equals minus the event's used amount, and the single positive WSOL change equals it, with exactly two WSOL accounts changed.

## 5. H5's inputs: unchanged

- **Q = quote + V**: see section 2 (events decode, quote equals the pre-trade balance). At s0 on 13 post pools V = 17,584,505,288-17,584,506,405 lamports, Q = 84.990-86.103 SOL; pre (n=6) V 17,584,505,288-17,584,505,626, Q 84.990-88.371.
- **s0 = first PumpSwap print** (searched in blocks migrate..+4): found 13/13 post, 6/6 pre; slot offset 0 on 11 and 1 on 2 (post), 0 on 6 (pre). s0 is inside the migrate tx itself on 3/13 post and 1/6 pre; creator_fee_unclaimed decoded on 19/19.
- **Class (PostCompleteBuyEvent in the curve-completing tx)** is read by the unchanged monitor code. Synthetic count: post_new 6/40, post_early 1/21 (3 not found), pre 4/19 (1 not found). Monitor 10-13: 3/20. Population note: mayhem pools 21/64 post (12/24 early, 9/40 new) vs 5/20 pre; non-WSOL graduations 4/64 vs 2/20.

## Residual risks

1. The new fees binary is not diffed against the old one, so the change is **not established**. Config sha equality and the identical CPI shape do not prove the same fee computation. Item 6's per-tier fee check covers it.
2. A new executing signer for the Squads vault upgrade. Not identified.
3. The first 2.8 h after the deploy have no BOOST sample; layouts were sampled at 15 blocks in 4 places.
4. One keeper gap (303 s) in 40 pools; A3's median-based flags would not show it if it recurred.
5. Slice count 28 appears on both sides of the deploy; the "29-30" band is read on the median.
6. Sections 2 and 5 are samples (n stated), not a census.

## Re-pin recommendation

`tools/pump_structure_pins.json` `programs.fees` only; everything else keeps its value:
- `deploy_slot`: **456132171**, `deploy_utc`: **2026-10-12T17:40:24Z**
- `sha256`: **3cf9ee9a380c0ced31171802d402551910ceb8c4819ede4ef44aaf63a12dbfac**, `data_len`: 948989
- pump, PumpSwap, the three config accounts and `boost` (17.585, 29) unchanged. `--write-pins` also rewrites `pinned_utc` and `pinned_at_slot`.

The next A3 run's 20-graduation sample is entirely post-upgrade (the 10-13 sample: slots 456344532-456352816 > 456132171), so DEC-024 resume step 3 can be met from it if it reaches 15 profiled pools.

## Scripts (program-upgrade-1013/)

`common.py` (cached client), `s01_programs.py`, `s01b_deploytx.py`, `s01c_feesstrings.py`, `s02_layouts.py`, `s02b_layouts_v2.py`, `s02c_feeret_sets.py`, `s03_configs.py`, `s04_boost.py`, `s05_bands.py`, `s06_slices.py`, `s07_s0.py`, `s07b_s0_blocks.py`; outputs in `raw/` and `rpc_calls.log`.

## 6. Executor dry run and fee tiers (the manager's part, added 2026-10-13T07:36:55Z): met, with the limits below

- **Tool.** `tools/h5_upgrade_dryrun.py` at main `deb5098f9ae0741bfb9216e1d2ca524c98fb6015` (PR #628; `Reviewer CHANGES` 6140563134 at `eb83c1c`, then `Reviewer OK` 6140635256 at `8efa312`). Keyless: `getAccountInfo`, `getMultipleAccounts` and `simulateTransaction` (sigVerify false) only. One transaction per pool: the executor's `buy_exact_quote_in` then the executor's `sell` of the buy's guaranteed minimum base out, 0.02 SOL.
- **Declared before the run.** `/data/mal/hunt-1008/h5-work/program-upgrade-1013-item6/DECLARATION.md` (written 2026-10-13T07:34:09Z, sha256 `78cf7f38f3a4fac0c55dd92afc61b0b8c61cf67f6727651700559cc29e595500`): the 6 newest eligible rows of `raw/s04_post_new.json` and the 6 newest of `raw/s04_post_early.json` (migrate slot > 456132171, WSOL quote, InitBoost, not mayhem, `synthetic` false); 12 distinct pools; the tool's verdict over all 12 is the result; one repeat allowed only for RPC errors or `tier_boundary`, never after a fee mismatch or a simulate error.
- **Run 1 (the only run).** MiScusi job #911 on mal-research-0, 2026-10-13T07:34:19Z to 07:34:46Z, public RPC, tool exit 0. Output `program-upgrade-1013-item6/run1.json`, sha256 `28daafd2ceece081320b8ca4f7fedcad69f573267f1f7ab7cc2f1cef2eb68a35`.
  - `round_trips_ok` 12 of 12; `n_sim_errors` 0; `n_fee_mismatch` 0; `n_fee_match` 12; verdict `PASS`.
  - PumpSwap FeeConfig sha256 read by the tool equals the pin (`d8706882…47c9d`).
  - Fee bps (LP, protocol, creator), observed = expected on the buy and on the sell of every pool: (2, 93, 30) on 9 pools (tier 0), (20, 5, 95) on 1 (tier 1), (20, 5, 55) on 1 (tier 9), (20, 5, 5) on 1 (tier 24). `tiers_covered` [0, 1, 9, 24].
  - Report-only, not part of the verdict: fee amount equal to ceil(base × bps / 10000) on 72 of 72 legs.
  - Compute units 190,430 to 223,430 per round-trip transaction over the 12 pools (`run1.json`).
- **Limits (the reviewer's list, PR #628 comment 6140635256).** (1) Only the events' reported fee bps gate; fee amounts are report-only. (2) Tiers are verified only for `tiers_covered`; 9 of 12 pools sat in tier 0. (3) The market-cap formula for the expected tier is source-backed in structure and matched the chain on 237 of 245 prints in EXP-024; it is not a specification. (4) The simulated sell omits the final base-token-account close. (5) The buy uses 300 bps slippage (the live executor uses 0) and the `H5Limits` default compute-unit limits; the sell runs on post-buy reserves. (6) A `processed` simulation with a public fee payer: it does not test landing, priority fees, tips, balance floors or the wallet. (7) The tool checks the PumpSwap FeeConfig by pinned address and sha256 only; the other two config accounts and the program hashes are sections 1 and 3. (8) FeeConfig read, pool snapshot and simulation are at different instants; a concurrent change is not detected. (9) It shows nothing about BOOST or H5's inputs. (10) One RPC provider's simulation; non-canonical pools are rejected; mayhem, holder-rewards and cashback coins are not modelled.

## Manager's notes for quant-proof (not judged here)

1. **Slices per pool, band "29–30".** Read on the median it is inside (29). Read per pool, 7 of 40 post-upgrade pools are outside by the signature-list count (28: 2, 31: 4, 32: 1; that count is an upper bound), and 1 of 7 exact keeper counts is 28. Before the deploy 3 of 13 were 28. Which reading the DEC means is quant-proof's call.
2. **Cadence comparison sample.** The DEC says the pre-upgrade median is "the same measure on the pre-upgrade runs' graduations" (the last 4 A3 runs). The A3 records keep no pool list, so section 4 used 13 profiled graduations from 2026-10-12T17:20 to 17:31Z, the hour before the deploy. That is a different pre-upgrade sample from the one the DEC names. Post 11.759 s against pre 11.893 s; September 12.06 s.
3. **One keeper gap.** 1 pool in 40 had a 303 s gap between two slices and its last slice at 635 s. None in the 13 pre-deploy pools. Not attributed.
4. **Unsampled span.** No BOOST sample for the first 2.8 h after the deploy (17:40Z to about 20:27Z on 10-12).
5. **New executing signer** on the Squads upgrade (`5sFJMw5d…`), not identified. The upgrade authority is unchanged.
6. **The binary change is not established** (no old binary on disk). Section 6 is the only test of the fee computation, with its limits.
7. **One canary buy row is dated after the deploy instant** (MiScusi job #909, ledger `kind` and `ts_ms` keys only). No fill, exit or P&L row was opened for this review.
