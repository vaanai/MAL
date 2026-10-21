# Review: pump / PumpSwap redeploys of 2026-10-19 (15:46Z and 17:47Z), re-pin for the H5 a2 books

Reviewer: Claude worker, 2026-10-21 (`date -u` 17:55Z to 18:35Z), mal-research-0. Public RPC (api.mainnet-beta.solana.com) and unauthenticated GitHub only. 0 Helius credits. No ledger, bots.json, shadow, fill or P&L file opened; no price path computed. Scripts and small outputs: `ARTIFACTS/lab/program-upgrade-1021/` (`out/*.json`); raw responses are off-repo in `/data2/mal/scratch/a3-repin-1021/` (raw, raw_pre). This is a reading of chain and repo facts. It is not a decision and not a claim about any book's profit.

## Verdict

1. **LOUD: the event layout changed on 2026-10-19 at about 15:46Z, about two hours BEFORE the 17:47Z redeploy that the monitor flagged.** Every `TradeEvent` (pump), `BuyEvent` and `SellEvent` (PumpSwap) gained **8 trailing bytes** beyond the docs-main IDL (pump-public-docs 639b87f, unchanged since 10-13). The pump programdata has two successful upgrade writes that day: slot **458888140** (blockTime 1792424776 = 2026-10-19T15:46:16Z) and 458921440 (17:47:02Z). TradeEvent changes within slots 458888133-458888141; BuyEvent and SellEvent within 458888174-458888182 (a PumpSwap deploy near 15:46:23Z is inferred from the event layout; its programdata signature list could not be paged back to it, so that deploy slot is **not observed**). The pins file records only the last deploy slot per program, so the A3 monitor saw one change, not two. See section 5(a).
2. **The frozen decoder is unaffected on the sample.** 0 field inequalities on 14,677 Trade/Buy/Sell events (independent IDL decode, section 4); the decoder reads fixed offsets inside the unchanged prefix. 0 unknown event discriminators and 0 unknown instruction discriminators on 23,471 post-redeploy txs. BuyV2/SellV2 and v3 families decode (section 4).
3. Every non-decode is explained (section 4): 604 CPI-only events in log-truncated txs, 294 PumpSwap events refused by the 100,000-SOL quote cap (none on a WSOL quote), 8 BOOST-slice buys without a trade instruction, 1 tape tx the public RPC no longer serves. None is new on 10-19 in kind; rates are in section 5.
4. Fee configs (bonding, PumpSwap, PumpSwap global) and the fees program are unchanged (sha256 equal to the pins at slot 459721762). BOOST flags are clear in both A3 runs after the redeploy.
5. **Whether this supports restarting H5 books is not mine to rule.** What the evidence supports: decode parity holds for the frozen 1f7a89c path, and the instruction set and fee configs are unchanged. What it does not support yet: a clean "no layout change". The 8 extra bytes are a live layout change that the docs IDL does not describe, 131 of 15,572 post-17:47Z values are non-zero (all 1,310 between 15:46Z and 17:47Z are zero; meaning unknown), and the real change instant (15:46Z) differs from the one the A3 flag used (17:47Z). Quant-proof must rule on that before any a2 clock (QP-RULING-1021-a3 C2/C3: "any layout change found means the review fails" is in tension with "0 unexplained non-decodes").

## 1. The A3 records

**10-20 record (job #1271), run_utc 2026-10-20T07:11:27Z (`/data/mal/structure-monitor/daily.jsonl`, status partial, `quote_mix: RpcError: 20 item(s) skipped`):**

| flag | halt | reason (verbatim) |
| --- | --- | --- |
| pins_changed | **true** | program_pump deploy_slot 456494698 -> 458921440 (2026-10-19T17:47:02Z); program_pumpswap deploy_slot 454596406 -> 458921517 (2026-10-19T17:47:20Z) |
| boost_disabled | false | GlobalConfig.boost_enabled = 1 |
| boost_share_low | false | InitBoost on 10/10 = 1.000 of non-mayhem WSOL graduations (halt below 0.8) |
| boost_last_slice_early | false | median last slice 340.5 s after migrate over 10 verified pools of 10 (halt below 315 s) |
| boost_budget_or_slices_changed | false | median budget 17.586 SOL vs pin 17.585 (+0.0%); median slices 30.0 vs pin 29 (+3.4%); halt beyond +/-20% |
| synthetic_share_high | false | PostCompleteBuyEvent on 2/19 = 0.105 of graduations (halt above 0.35) |

Warn: `program_changed` (pump sha256 ba1f91ac... to 0b692730..., PumpSwap 868a875e... to e3d3be22...), `stage_errors`. Docs head 639b87f unchanged.

**10-21 catch-up run (job #1300), run_utc 2026-10-21T17:55:29Z (line 14 of the file, status ok, no errors):** only `pins_changed` halts (same reason strings); boost_disabled false; boost_share_low false (10/10); boost_last_slice_early false (median 342.0 s over 10 of 10); boost_budget_or_slices_changed false (budget 17.586 vs 17.585, slices 30.5 vs 29, +5.2%); **synthetic_share_high false at the limit: PostCompleteBuyEvent on 7/20 = 0.350 (halt above 0.35)**, up from 0.105 the day before; `docs_watch` n_new 0, latest 639b87f (2026-10-13T15:37:48Z). All rules evaluated (n_not_evaluated 0). 215 RPC calls. The file had no line between 10-20T07:11Z and this run (QP C6, not examined here).

## 2. Docs / IDL / binary diff

- **Docs.** pump-public-docs `main` = 639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22 (2026-10-13T15:37:48Z), the commit the 10-14 review pinned. The GitHub compare API gives 639b87f...main `identical`, ahead_by 0, total_commits 0; branches: main 639b87f plus three old feature branches. `idl/pump.json`, `idl/pump_amm.json`, `idl/pump_fees.json` at main equal those at 639b87f byte for byte (sha256 `7616681e...7207`, `b7d8c57a...ab89c`, `6b9fa3ec...2b29`; `out/` has no copy, the three fetches are reproducible from the URLs `https://raw.githubusercontent.com/pump-fun/pump-public-docs/{main,639b87f...}/idl/*.json`). So **no instruction, discriminator, event layout or fee logic changed in the public docs** after 10-13, and the docs do not describe the 10-19 upgrades.
- **Binaries (`s04_binary_strings.py`, offline).** New programdata: pump sha256 `0b692730f9707139fd86d1c1e3a7d63a3f7cb0702278975fa99df90883aab508` (slot 458921440), PumpSwap `e3d3be2256841163ba89b6184c2cc90ca1700c820bf887e5bb58b0aa668f5840` (458921517), both 10,485,760 bytes, deploy slots equal the monitor's (`out/s01_programdata.json`). The 10-13 pump binary (`ba1f91ac...`, kept by the 10-14 review) is diffed against the new one: 472 of 2,560 4-KiB pages differ; the printable-string differences are exactly (i) a new error `QuoteMintNotWritable` with the text "quote_mint must be writable for a quote with a Token-2022 transfer fee: its withheld fees are harvested to the mint" (not in the docs IDL, whose 110 error messages are all still present), (ii) a new source path `programs/pump/src/util/transfer_fee.rs`, (iii) the Token-2022 `src/extension/transfer_fee/instruction.rs`. The set of instruction names is identical between the two binaries and every IDL name (56 pump, 38 PumpSwap) is present (4 pump and 2 PumpSwap names cannot be confirmed at the byte level because another string follows directly, as in the 10-14 review). Reading: Token-2022 transfer-fee quote support, relevant to non-WSOL quotes; not shown to touch WSOL trades. The old PumpSwap binary is not on disk, so PumpSwap is **not diffed**. Neither binary is the 15:46Z version: the 15:46Z and 17:47Z pump writes are both inside this 10-13 to 10-21 diff.

## 3. Decode-parity sample rule, DECLARED BEFORE ANY FETCH OR REPLAY

Declared at the commit that adds this section and `ARTIFACTS/lab/program-upgrade-1021/s05_fetch_sample.py` (the code is the rule). Nothing of the sample had been fetched when this was written. Structure only: counts, discriminators, layout lengths, field equality of already-emitted fields; no price path, outcome or P&L is computed or saved.

- **Hours:** the 24 closed tape hours 2026-10-19T18 to 2026-10-20T17 of `/data/mal/arena/tape` (all after the 17:47:20Z redeploy).
- **Sample A (decoder-agnostic, from the chain, not from the tape):** per hour, anchor = the minimum `slot` of the first 200 pumpfun trade rows of the hour's tape file (slot only). The first 10 non-skipped slots at or after anchor+3000 are fetched with `getBlock` (public RPC, finalized, full, maxSupportedTransactionVersion 1). Every successful tx that touches pump, PumpSwap or the fees program is in the sample. Because the sample is taken from blocks, a tx the decoder cannot read is still in it.
- **Sample B (tape-stratified, to reach rare variants):** per hour, in the first 600,000 lines of the tape file: the first signature for each (prog, ix_name) of type `trade` (this reaches BuyV2, buy_exact_quote_in_v2, multi_hop_swap; a PumpSwap SellEvent has no ix_name, so SellV2 is covered by sample A only), and the first 3 of each of `create`, `complete`, `pool_create`. Fetched with `getTransaction`. B proves field equality and layout on rare types; it cannot show a non-decode by itself, since a tape row exists only if the feed decoded it.
- **Sample C (BOOST):** in the 6 hours with index % 4 == 0, the first B tx that `parse_migrate_tx` reads as a migration gives the pool; `getSignaturesForAddress(boost vault, 100)`; the 2 oldest and 2 newest successful signatures are fetched. These cover InitBoost and BoostBuyAndBurn (slice) events.
- **Frozen decoder:** `observe/trade_decode.py` and `tools/pump_history_backfill.py` are byte-identical between 1f7a89c and this branch's base (checked by blob sha in section 3 results). Paths run: `records_from_logs(event_v=True)` (the path `tools/h5_shadow.py:2547` uses), `decode_extra_event`, and `rows_from_block(event_v=True)` for the walker counts (unknown_disc, bad_disc, cpi_only_disc).
- **Independent decode:** a from-scratch borsh walker driven by the docs IDLs (pump, pump_amm, pump_fees at pump-public-docs 639b87f; main is identical), with its own base58 and log-stack program attribution; it shares no code with `observe/`.
- **A NON-DECODE is any of:** (N1) an IDL event blob (attributed to pump, PumpSwap or fees by the log stack) that the frozen path does not turn into a row or event; (N2) a blob attributed to those programs with a discriminator not in the IDL events; (N3) an IDL event blob the independent walker does not consume to exactly its length; (N4) an instruction of those programs with a discriminator not in the IDL; (N5) a trade-family instruction whose successful tx has fewer trade events than trade instructions, or a frozen row count different from the independent count of trade events in the logs; (N6) any field inequality between the frozen row and the independent decode. Pass bar: 0 unexplained, every one explained.
- A PumpSwap row with `mint` unresolved (pool not yet in the cache) is counted separately; it is a resolution gap, not a decode failure.

### Sample P (pre-redeploy control), added at the coordinator's request before it was fetched
Same rule as sample A, same code (`SAMPLE=pre`), on the tape hours 2026-10-19T08 to 2026-10-19T17 (anchor+3000 slots, 10 blocks per hour; the hour-17 anchor+3000 is about 17:11Z, before the 17:47:02Z redeploy). No B or C part. Purpose: are the 8 trailing bytes on Trade/Buy/Sell events, the log-truncated CPI-only events and the 100,000-SOL-capped PumpSwap events new on 10-19, or already there before the redeploy?
- **Capped events (b):** a PumpSwap Buy/Sell event whose `user_quote_amount_in/out` exceeds `_MAX_SWAP_SOL_LAMPORTS` (1e14 lamports). The quote mint is taken from the `quote_mint` account of the pump_amm trade instruction(s) in the same tx (IDL account order). If any capped event has WSOL as quote mint, the decode is wrong.
- **Migrate txs (c):** every tx in samples A, B, C that holds a `CompletePumpAmmMigrationEvent`; for the non-trade events the rule reads (CreateEvent, CompleteEvent, CompletePumpAmmMigrationEvent, CreatePoolEvent, PostCompleteBuyEvent, InitBoostEvent, BoostBuyAndBurnEvent, Sweep*), both log-sourced and inner-instruction-sourced, the frozen function's output is compared with the independent IDL parse by value containment: every non-meta frozen field value must equal some value of the IDL parse.

## 4. Decode-parity results

Frozen path blobs: `observe/trade_decode.py` `238942a6`, `tools/pump_history_backfill.py` `9a8bebb3`, `tools/h5_shadow.py` `23b9ccc2` are identical at 1f7a89c and on this branch's base (git blob shas, `out/s06_parity.json` `blob_sha`). Independent decoder: `s06_parity.py` (own base58, borsh walker from the docs IDLs, invoke-stack program attribution). Sample sizes: post-redeploy A = 240 blocks, 23,009 successful pump/PumpSwap/fees txs (0 failed txs in the sample set by construction), B = 462 txs analysed (1 of 463 not served, see N-FETCH), C = 6 BOOST pools; P = 100 blocks, 9,827 txs.

| | post-redeploy (A+B+C) | pre-redeploy control P (10-19T08..17) |
| --- | --- | --- |
| trade events, independent IDL parse (pump Trade / PumpSwap Buy / PumpSwap Sell) | 3,525 / 5,214 / 6,831 | 1,283 / 2,618 / 2,569 |
| fields compared frozen vs independent (Trade / Buy / Sell) | 3,319 / 5,061 / 6,297 | 1,192 / 2,559 / 2,512 |
| **field inequalities (N6)** | **0** | **0** |
| unknown event discriminators of pump / PumpSwap / fees (N2) | **0** | **0** |
| unknown instruction discriminators (N4) | **0** | **0** |
| instructions seen (v2/v3 families) | buy_v2 84+226, buy_v3 209, buy_exact_quote_in_v2 224+541, _v3 576, sell_v2 218+645, sell_v3 667 (pump+PumpSwap) | same families, smaller counts |
| events consumed exactly by the docs IDL | all except the +8 bytes (below) | all except the +8 bytes after slot ~458888140 |
| txs with truncated logs | 379 | 56 |
| trade events visible only in an inner-instruction self-CPI (N1) | 599 (206 Trade, 384 Sell, 9 Buy) + 5 CompleteEvent; **604 of 604 in log-truncated txs** | 96 (91 Trade, 5 Buy), 96 of 96 in log-truncated txs |
| PumpSwap events refused by the 100,000-SOL cap | 294 of 12,045 (2.44%) | 111 of 5,187 (2.14%) |
| `records_from_logs` rows vs independent log trade events | 14,677 vs 14,971; the 294 difference is exactly the cap (151 txs, all `explained_by_cap`) | 6,263 vs 6,374; 111 = cap (58 txs) |
| trade event with no trade instruction (N5) | 8, all `boost_buy_and_burn` slices | 4, all boost slices |
| PumpSwap rows whose mint is unresolved in a per-tx decode | 11,358 (pool not in the empty per-tx cache; a resolution gap, not a decode failure) | 5,071 |

Explanations of every non-decode, none left open:
- **N1, 604 post / 96 pre.** The event is not in `logMessages` because the RPC log is cut at 10 KB (`Log truncated`); it is in the tx's inner instruction as an Anchor self-CPI (tag e445a52e51cb9a1d). `records_from_logs` (the h5_shadow path, logsSubscribe) cannot see it. This is a property of the log feed, present before the redeploy, and it is a pre-existing blind spot, not new. Rate: post 604 events in 379 truncated txs of 23,471; pre 96 in 56 of 9,827 (about 2.6% vs 1.0% of sampled txs truncated; the increase is **not explained**, `get_fees_with_quote_mint` CPI calls appear in 12,345 post vs 5,170 pre tx instructions, one per trade, which lengthens logs; that is a hypothesis, not tested).
- **Cap, 294 post / 111 pre.** `td._MAX_SWAP_SOL_LAMPORTS` = 100,000 SOL; the refused amounts are 1.0e14 to 3.9e15 lamports. Quote mint taken from the `quote_mint` account of the pump_amm trade instruction in the same tx: **post, 0 of 294 on WSOL, 0 without an identified mint**; pre, 1 of 111 is in a tx that has two quote mints (one WSOL), so which one the capped event belongs to is not determined by this method (`refused_rows` in `out/s06_parity_pre.json`). So the cap removes non-WSOL (token-quoted) events, as intended; the pre-sample exception is not resolved.
- **N5 BOOST slices, 8 / 4.** A `boost_buy_and_burn` instruction emits a PumpSwap BuyEvent; it is not a buy/sell instruction. Equals the 8 / 4 `BoostBuyAndBurnEvent` seen.
- **Empty tag CPI, 2 post / 0 pre.** A `MultiHopCurveSwap` inner call carries the bare event-authority tag with no payload (8 bytes). Not an event.
- **N-FETCH, 1.** Tape signature `4F4yuTFM...` (hour 2026-10-20T12, `buy_exact_quote_in`, pumpfun) returned null from the public RPC at finalized and confirmed on re-try. It is **unverified**, not decoded.

### Migrate txs and BOOST events (item c)
The 28 migration txs in the post sample (every tx with a `CompletePumpAmmMigrationEvent`; 100% of them carry it only as an inner-instruction self-CPI, 0 in the logs) plus the other events the rule reads, frozen function output vs independent IDL parse by value containment (every frozen field value equals a value of the IDL parse; the native-SOL zero pubkey stored as WSOL by `_quote_or_wsol` is accepted by design):

| event (source) | count | frozen decodes | equal to IDL parse |
| --- | --- | --- | --- |
| CompletePumpAmmMigrationEvent (inner CPI, in the 28 migrate txs) | 28 | 28 | 28 |
| InitBoostEvent (inner CPI 18, log 1; all in migrate txs) | 19 | 19 | 19 |
| pump_amm CreatePoolEvent (log; 28 in the migrate txs) | 73 | 73 | 73 |
| pump PostCompleteBuyEvent (log) | 16 | 16 | 16 |
| pump CompleteEvent (log 67, CPI-only 5) | 72 | 72 | 72 |
| pump CreateEvent (log) | 103 | 103 | 103 |
| BoostBuyAndBurnEvent (slices, log) | 8 | 8 | 8 |
| SweepPoolFee / SweepBondingCurveFee | 3 / 11 | 3 / 11 | 3 / 11 |

Caveats: 10 of the 28 migration txs have no InitBoost in the sample (mayhem or non-WSOL, not examined here); the walker (`rows_from_block`) turns the CPI-only migration events into rows, the log-only `records_from_logs` does not. The CPI-only `CompleteEvent` (5) is not turned into a row by the walker (it decodes CPI-only migration and extra events only) and is counted N1. Sample C (BOOST slices): see `c_sample` in `out/s06_parity.json`; only 8 slice events were seen in total, so the slice layout is covered thinly.

## 5. The three coordinator questions

**(a) The 8 trailing bytes: new, and earlier than the flagged redeploy.** Pre-redeploy control P (tape hours 10-19T08 to 10-19T17, 6,470 trade events): the 5,148 log-sourced events in the eight 1,000-slot buckets 458763000 to 458878000 all have 0 trailing bytes; all 1,310 events with trailing bytes lie at slots 458894793 to 458911466 (the two last buckets), none earlier. Single-block bisection (`s07_upgrade_history.py`): TradeEvent goes from 0 to 8 trailing bytes within slots 458888133 to 458888141, Buy and Sell within 458888174 to 458888182. The pump programdata's successful upgrade writes are at 458888140 (2026-10-19T15:46:16Z) and 458921440 (17:47:02Z). **So the 8 bytes are new on 10-19 (absent before ~15:46Z, present after), they arrived with an upgrade that the pins and A3 did not flag separately, and that is about 2 h before the slot 458921440 the monitor reports.** The extra 8 bytes read as a u64: in P (15:46Z to 17:47Z) all 1,310 are 0; in the post-17:47Z sample **131 of 15,572 are non-zero** (0.84%; first at slot 458927944, values from 16,087 up to the millions; `trail8_nonzero` in `out/s06_parity.json`), so the field starts to carry data only after the 17:47Z redeploy. Its name and meaning are in no public source found (the docs IDL has none). It is present on pump TradeEvent, PumpSwap BuyEvent and SellEvent, and on 2 PumpSwap WithdrawEvents. The frozen decoder reads only fixed offsets inside the old prefix and `event_v_fields` offsets; they stayed correct (0 inequalities in 14,677 comparisons across both sides of the change, pre sample included), so nothing breaks, but the length guards (`>= 184`, `>= 113`) are minimums and would not notice a further growth. **Needs QP before any a2 clock** (a live layout change, and the a1 end record uses 17:47Z / 07:11Z instants).

**(b) Capped events.** Post: 294 of 12,045 PumpSwap Buy/Sell events (144 Buy, 150 Sell, 2.44%); pre: 111 of 5,187 (2.14%). Amounts 1.0e14 to 3.9e15 lamports. Quote mint by instruction accounts: 0 of 294 on WSOL post; one pre event ambiguous. Not a WSOL decode error on the sample.

**(c) Migrate inner-instruction events:** see the table above (28 migrations, 19 InitBoost, 73 CreatePool, 16 PostCompleteBuy; all decode and equal).

## 6. Re-pin

`tools/pump_structure_pins.json` regenerated with the monitor's own `--write-pins` (public RPC, slot 459721762, 2026-10-21T17:57:28Z; `s02_write_pins.py`, 9 RPC calls). Diff limited to programs.pump (deploy_slot 458921440, deploy_utc 2026-10-19T17:47:02Z, sha256 0b692730...b508), programs.pumpswap (458921517, 2026-10-19T17:47:20Z, e3d3be22...5840), pinned_utc, pinned_at_slot; accounts, fees program, BOOST pin unchanged. The `programdata.json` fixture was re-recorded and one test line updated; `pytest tools/test_pump_structure_monitor.py tools/test_h5_shadow_synthetic.py tools/test_h5_upgrade_dryrun.py`: 201 passed, 1 skipped. Effect: the next daily A3 run shows pins_changed clear, **but** the pin records the 17:47Z slot and cannot represent the 15:46Z upgrade.

## 7. NOT checked

- The PumpSwap 15:46Z deploy slot (inferred from events only), the 15:46Z binary (never captured), the old PumpSwap binary (not diffed).
- On-chain Anchor IDL accounts (docs IDLs only); the meaning of the 8 bytes; the 9 non-zero values.
- Any fee computation of the redeployed programs (no `h5_upgrade_dryrun` run; fee configs are byte-equal only); the executor's remaining accounts after the deploy (simulation not re-run).
- Events outside the sampled blocks and rare types: no BoostBuyAndBurn beyond 8, no PostCompleteBuy outside 16, sample is 10 blocks of each of 24 hours (about 1.5% of slots).
- Whether logsSubscribe delivers the same truncated logs as `getBlock` (assumed).
- The dry A3 shape of the re-pin (the next official daily run), outcomes, prices, P&L, profitability of any book: none measured.
