# Review: pump program redeploy of 2026-11-13T20:12:28Z (slot 469055660), re-pin

Builder: Claude worker, 2026-11-14 (`date -u` 08:35Z to about 09:05Z), mal-research-0. Public RPC (api.mainnet-beta.solana.com) and unauthenticated GitHub only. 0 Helius credits, no key or env file. No tape, arena, book, ledger, shadow, declog or FLOOR file opened; no price path, outcome or P&L computed. Scripts and small outputs: `ARTIFACTS/lab/program-upgrade-1114/` (`out/*.json`); raw responses are off-repo in `/data2/mal/scratch/a3-repin-1114/`. This is a reading of chain and repo facts. It is not a decision and not a claim about any book's profit. Standard applied: QP ruling 384d99f3 (`/data/mal/hunt-1008/ops/QP-RULING-1114-a3-repin-pump.md`) Q1, items 1 to 13.

## Verdict

**Every bar of Q1 that this builder can measure is met. No layout change was found. The review reads CLEAN on the evidence below, subject to the open items listed under "Open items for the manager and QP" (none is a failed bar; two are NOT OBSERVED).** The judgement is the manager's and the verdict agents'.

| Q1 item | Bar | Result |
| --- | --- | --- |
| 1 | rule declared and committed before fetch, chain-only | PASS. Section 3 (sample A, P) at commit `b70c22d5`; section 3b (P2, M, M_pre) at `f846fede`, before their fetch. No tape or book path opened. |
| 2 | sample A: >= 240 blocks, >= 10 UTC hours, all >= 150 slots after deploy, >= 5,000 txs, all FLOOR event types, WSOL Buy+Sell >= 1,000 | PASS. 240 blocks, 13 distinct UTC hours (2026-11-13T20 to 2026-11-14T08), first block 3,000 slots after the deploy, 19,214 successful pump/PumpSwap/fees txs. Events: CreateEvent 25 (all `create_v2`; no v1 `create` instruction in A, P or P2), CompleteEvent 1, CompletePumpAmmMigrationEvent 41 (A+M), PumpSwap CreatePoolEvent 43, WSOL-only PumpSwap Buy 1,573 + Sell 1,207 = 2,780. |
| 3 | M: first 40 settled migrate txs from deploy+150, >= 20 usable, >= 10 WSOL | PASS. 40 usable (slots 469055967 to 469070200), 27 WSOL-quoted; 16 candidate txs were not migrate txs, 0 unfetchable. |
| 4 | P: >= 100 blocks, >= 5 UTC hours, ends >= 150 slots before the earliest write; >= 10 pre migrate txs | PASS. P (100 blocks) + P2 (100 blocks) = 200 blocks over 6 distinct UTC hours (2026-11-13T15 to T20), last block 469055069 = 591 slots before 469055660. M_pre: 40 txs (33 WSOL), slots 469041001 to 469053685. |
| 5 | decode paths under test | PASS for the three EXP027 blobs and the follower inner-migration path (section 5). **Follower running blob: to be named by the manager.** |
| 6 | 0 unknown discriminators, 0 field inequalities, every non-decode explained, truncated/CPI-only rates | PASS. 0 unknown event and 0 unknown instruction discriminators of pump, PumpSwap, fees in A, P, P2, M, M_pre. 0 field inequalities on 11,964 frozen trade rows (post) and 0 on every non-trade event read (section 6). All non-decodes explained. Truncated-log rate of WSOL-only PumpSwap trade txs: post 18 / 2,770 = 0.65% vs pre 44 / 3,056 = 1.44% (post is below pre). |
| 7 | C3' (a) (b) (c) (d), docs/IDL, binary diff | PASS (a), (b), (c), (d); docs unchanged; binary diff report-only (section 2). |
| 8 | layout change | None: no event outside the pinned trailing set, 0 prefix inequalities. |
| 9 | FLOOR items (i) to (iv) | PASS (i) 0 mismatches; (ii) post values inside the pre range; (iii) 12 post pools equal the pre pools; (iv) fee pins equal. First-print V is NOT OBSERVED for 8 of 20 post and 6 of 10 pre pools (page budget), see section 7. |
| 10 | write enumeration | PASS on route (a), public RPC: **exactly one successful Upgrade in the required range** (slot 469055660, 2026-11-13T20:12:28Z, signature below). Classification of all 2,582 successful signatures back to 458921440 is partial (see section 8). |
| 11 | re-pin read after the last sample | PASS. Read at slot 469269411 (2026-11-14T08:58:57Z); only `programs.pump` and the two header keys differ from main's pins. |
| 12, 13 | not required / fallback | not needed. |

Findings, numbered:

1. **Layout unchanged.** Trailing bytes equal `tools/a3_redeploy_pins.json` `trailing_bytes` on every pinned event: TradeEvent, BuyEvent, SellEvent, WithdrawEvent 8 on every observed event (2,712 / 4,485 / 5,386 / 3), 0 on every other pinned event; identical pre and post. 0 prefix inequalities, 0 unknown discriminators.
2. **The 8-byte field is 0 where it matters.** SOL-quoted pump TradeEvents: 0 non-zero of 2,336 post (pre 0 of 2,236); WSOL-only PumpSwap events: 0 non-zero of 2,780 post (pre 0 of 3,076). Non-zero values occur only on token-quoted events, in pre and post alike (section 6).
3. **Migration path unchanged.** 41 post CompletePumpAmmMigrationEvents (all CPI-only, as pre), 0 value-unmatched, the follower's inner-migration path reuses the same decoder (section 5); economics inside the pre range (section 7).
4. **Mints and pools.** 80 post vs 82 pre Token-2022 base mints, all with extension types 18 and 19 only (MetadataPointer, TokenMetadata); 0 TransferFeeConfig, 0 TransferHook / PermanentDelegate / NonTransferable / DefaultAccountState, 0 extension type absent from pre.
5. **Binary.** The pump programdata differs from the 10-19 binary in 473 of 2,560 4-KiB pages; the printable-string difference is one new error name `CreatorFeeRangeInvalid` and two reworded/added creator-fee messages (section 2). PumpSwap programdata equals the pin and the 1021 binary byte for byte; fees program equals the pin.
6. **No tape-stratified sample B this time.** Reached in A, P, P2, M, M_pre: instruction families create_v2, buy_v2/v3, buy_exact_quote_in_v2/v3, sell_v2/v3, multi_hop_curve_swap, migrate_v2, migrate (v1, 3 txs in M_pre only), create_pool, init_boost, boost_buy_and_burn (PumpSwap buy_v2, buy_exact_quote_in_v2, sell_v2, multi_hop_swap); events CreateEvent, CompleteEvent, PostCompleteBuyEvent, CompletePumpAmmMigrationEvent, CreatePoolEvent, InitBoostEvent, BoostBuyAndBurnEvent, Sweep*, Trade/Buy/Sell/Withdraw. NOT reached: the v1 `create` instruction and the v1 `buy`/`sell` instructions; PumpSwap `buy_v3`/`sell_v3` do not exist in these samples; PostCompleteBuyEvent and CompleteEvent only once each.

### Open items for the manager and QP

- **Follower running blob:** to be named by the manager. `tools/fast_tip_follower.py` at this base is blob `63a206a5d83f777116961a3336308a515dd3991d`.
- **Test fixture is red after the pins change.** `MAL_ARENA_TEST=1 pytest tools/test_pump_structure_monitor.py -q`: 99 passed, 16 failed with the new pins; 115 passed with main's pins. Cause: `tools/fixtures/pump_structure_monitor/programdata.json` still holds the pump deploy slot 458921440 (and line 354 of the test expects `[458921440, 458921517, 456132171]`); the 1021 re-pin re-recorded it (`program-upgrade-1021/s03_rerecord_fixture.py`). Not changed here (outside the files this task names).
- **`disc_len` pin drift, not caused by this redeploy:** observed pump TradeEvent blob lengths 390..473 and CreateEvent 344..376 (post) lie outside `a3_redeploy_pins.json` `disc_len` (366..429 and 333); the same lengths are observed in the pre control (P2, 4.4 to 8.9 hours before the deploy, no programdata write in between), so it is the variable-length `ix_name` / strings, not a layout change. If `tools/a3_redeploy_check.py` compares `disc_len`, it would flag this on pre data too. Not changed (the pins file is not to be edited).
- **PumpSwap DepositEvent** is not in `trailing_bytes` pins; one post event carries 8 trailing bytes (no pre sample event). PumpSwap's programdata is unchanged, so this is not caused by the redeploy; reported only.
- **First-print V NOT OBSERVED** for 8 of 20 post and 6 of 10 pre pools (hot pools exhaust the 8-page budget). Observed V includes values far from [17.5e9, 17.7e9] (min 0) in post and pre alike: post 6 of 12 observed inside the band, pre 2 of 4 (section 7).
- **New creator-fee error text in the binary** (`CreatorFeeRangeInvalid`, minimum configurable creator fee): path attribution (create / UpdateCreatorFeeConfig) not determined here; no new discriminator, event or instruction name appeared in any sample. Q1 item 7 asks for a QP look when a new name sits on a FLOOR path.
- **Classification of all successful programdata signatures** back to 458921440 is partial: see section 8.

## 1. The A3 records

**Record of 2026-11-14T07:10:05Z (copied from the coordinator's brief; the record file itself was not opened by this builder):** `pins_changed` halts: "program_pump deploy_slot 458921440 -> 469055660 (2026-11-13T20:12:28Z)". Warn `program_changed`: pump programdata sha256 0b692730f9707139... -> c849155c1d5bf990..., len 10485760 -> 10485760. PumpSwap and fees program pins, the three account pins (bonding_fee_config, pumpswap_fee_config, pumpswap_global_config) and the docs head are reported unchanged by the monitor. The automatic check `tools/a3_redeploy_check.py` gave verdict ERROR: "write enumeration failed for pump (CallBudgetExceeded: call cap 450 reached before getTransaction)".

## 2. Docs / IDL / binary diff

- **Programdata and configs (`s01`, `out/s01_programdata.json`, finalized reads at slots 469263116 to 469263162).** pump: deploy slot **469055660**, sha256 `c849155c1d5bf990c564bad8df406d704ed05b8d3dc4ee30ce2350bfbc4edf3a`, length 10,485,760 (differs from the old pin by design). PumpSwap: deploy slot 458921517, sha256 `e3d3be2256841163ba89b6184c2cc90ca1700c820bf887e5bb58b0aa668f5840`, length 10,485,760: **equals the pin** in `tools/pump_structure_pins.json`. Fees: deploy slot 456132171, sha256 `3cf9ee9a380c0ced31171802d402551910ceb8c4819ede4ef44aaf63a12dbfac`, length 948,989: **equals the pin**. The three config accounts (bonding_fee_config 4,097 B `3504fae2...`, pumpswap_fee_config 4,097 B `d8706882...`, pumpswap_global_config 949 B `a698009a...`) **equal the pins** (`equals_pin` true for all three).
- **Docs (`s03`, `out/s03_docs_idl.json`).** pump-public-docs `main` = 639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22 (2026-10-13T15:37:48Z); compare `639b87f...main`: identical, ahead_by 0, no file changed. IDL sha256: pump `7616681e...7207`, pump_amm `b7d8c57a...ab89b`, pump_fees `6b9fa3ec...2b29`; **all three equal `tools/a3_redeploy_pins.json` `idl`** (main and 639b87f give the same hashes). So the independent walker's IDL is the pinned one.
- **Binary diff (`s04`, `out/s04_binary_strings.json`; old = the 1021 review binary 0b692730..., kept at `/data2/mal/scratch/a3-repin-1021/pd/pump.bin`).** 473 of 2,560 4-KiB pages differ. Printable-string runs: 64 old, 63 new. The differences: (i) a new error name `CreatorFeeRangeInvalid` inserted in the error-name list after `QuoteMintNotWritable`; (ii) the message "Creator fee basis points must be between 1 and the configured maximum" became "...between the configured minimum (at least 1) and the configured maximum"; (iii) a new message "min_configurable_creator_fee_bps must not exceed max_configurable_creator_fee_bps". No new "Instruction: <Name>" string appeared (all binary instruction tokens are IDL names or the Anchor IDL instructions); the docs IDL does not contain `CreatorFeeRangeInvalid` (docs unchanged, so it cannot). PumpSwap binary: byte-identical to the 1021 binary. Report-only under Q1 item 7.

## 3. Decode-parity sample rule, DECLARED BEFORE ANY BLOCK FETCH

Declared at the commit that adds this section together with `program-upgrade-1114/s05_fetch_sample.py` (the code is the rule). No block or transaction of the sample had been fetched when this was written (only the three programdata and three config accounts, `s01`). Structure only: counts, discriminators, layout lengths, field equality of already-decoded fields.

- **CHAIN-ONLY.** No tape, `/data/mal/arena*`, `/data2/mal/arena-tip-feed`, `/var/lib/mal*`, book, ledger, shadow, declog or FLOOR file is opened or parsed. Anchors are slot numbers only. There is **no tape-stratified sample B** this time.
- **Sample A (post redeploy):** 24 anchors evenly spaced (integer `lo + (hi-lo)*i//23`) between slot 469055660+3000 and (latest finalized slot at run time, from `getSlot` finalized) minus 1000. At each anchor, the first 10 non-skipped slots at or after the anchor are fetched with `getBlock` (finalized, full, maxSupportedTransactionVersion 1). Every successful tx that touches pump (`6EF8rrec...`), PumpSwap (`pAMMBay6...`) or fees (`pfeeUxB6...`) is in the sample.
- **Sample P (pre control):** 10 anchors evenly spaced between 469055660-40000 and 469055660-600 (so every block is at least 500 slots before the deploy), 10 blocks each, same code (`SAMPLE=pre`).
- **Sample C (BOOST):** for up to 6 migration txs found in sample A (slot order, one per pool; a tx counts when `tools.pump_structure_monitor.parse_migrate_tx` reads a CompletePumpAmmMigrationEvent), `getSignaturesForAddress(boost vault, limit 100)`; the 2 oldest and 2 newest successful signatures are fetched with `getTransaction`.
- **Frozen decoder:** `observe/trade_decode.py` (git blob must be 238942a6...) and `tools/pump_history_backfill.py`; paths run: `records_from_logs(event_v=True)`, `decode_extra_event`, `rows_from_block(event_v=True)`.
- **Independent decode:** `s06_parity.py`, a from-scratch borsh walker driven by the docs IDLs (current docs main; see section 2 for whether it differs from 639b87f), with its own base58 and log-stack program attribution.
- **A NON-DECODE** is any of N1 to N6 exactly as defined in `a3-repin-2026-10-21-program-upgrade-review.md` section 3: (N1) an IDL event blob the frozen path does not turn into a row or event; (N2) unknown event discriminator; (N3) an IDL event the independent walker does not consume to its length; (N4) unknown instruction discriminator; (N5) trade-instruction vs trade-event count mismatch or frozen-vs-independent row count; (N6) any field inequality. Pass bar: 0 unexplained.
- Migration and pool-creation events (CompleteEvent, CompletePumpAmmMigrationEvent, CreatePoolEvent, InitBoostEvent, BoostBuyAndBurnEvent, PostCompleteBuyEvent): both log-sourced and inner-instruction-sourced, frozen output compared with the independent IDL parse by value containment, as in 1021.

## 3b. Added after QP ruling 384d99f3 (`/data/mal/hunt-1008/ops/QP-RULING-1114-a3-repin-pump.md` Q1), declared at the commit that adds this section, before any fetch of these samples

Sample A and P above were already being fetched when the ruling arrived (their rule stands, unchanged). The following is ADDED; nothing of it had been fetched when this was written.

- **P2 (pre control extension, item 4):** same code as P (`SAMPLE=pre2 UPGRADE_RAW=.../raw_pre2 s05_fetch_sample.py`): 10 anchors evenly spaced between 469055660-80000 and 469055660-41000, 10 non-skipped blocks each. P (40,000 slots, about 4.4 h) alone may not span 5 distinct UTC hours; P + P2 cover 80,000 slots (about 8.9 h). All P and P2 blocks end more than 500 slots before 469055660. If the write enumeration (s07/s09) finds a write inside P or P2, the review says so and the sample is split at it.
- **M (post migrate sample, item 3):** `s10_fetch_migrate_sample.py`. Candidates are the successful signatures of `mon.MIGRATION_FEE_ACCOUNT` read back from now (the MIGRATE_K rule of `tools/a3_redeploy_check.py migrate_sample`). M = the first 40 settled (blockTime <= now - 480 s) candidates with slot >= 469055660 + 150, oldest first, each fetched whole with `getTransaction` (logs plus inner instructions), kept when `parse_migrate_tx` reads a CompletePumpAmmMigrationEvent. Bars: >= 20 usable, >= 10 WSOL-quoted.
- **M_pre (pre migrate control, item 4):** the last 40 candidates with slot <= 469055660 - 150 (walking backwards), same keep rule; bar >= 10.
- **Migrate txs in A, P, P2** are also analysed as in 1021 (events of every migrate tx found in the blocks).
- **Item 2 bars** (>= 240 blocks over >= 10 distinct UTC hours, all >= 150 slots after the deploy, >= 5,000 successful pump/PumpSwap txs, every FLOOR event type present, WSOL PumpSwap Buy+Sell >= 1,000): counted from sample A as fetched; if A is short of any type, a further declared extension is added here before its fetch.
- **Mint/pool state (items 7d, 9iii):** after the samples, one `getMultipleAccounts` read (public RPC) of the base mints and pools of M (post, >= 20 and the WSOL ones) and M_pre: owner program, data length, 8-byte discriminator, Token-2022 extension types, pool `coin_creator` and token-program fields. Read now (a transfer-fee or other extension cannot be added after mint creation).
- **Follower paths (item 5):** `rows_from_block(event_v=True)`, `records_from_logs(event_v=True)`, plus `tools/fast_tip_follower.py inner_migration_rows` (blob at this base 63a206a5d83f777116961a3336308a515dd3991d), which builds a reduced view of the block and calls `rows_from_block(view, {}, FEED, event_v=True)` again, i.e. it only maps fields of an event `trade_decode` decodes; plus a pool-PDA guard (`canon(mint) == pool`). The follower's running blob: to be named by the manager.


## 4. Notes on the declared samples as run

- Sample A, P, P2: executed exactly as declared. One restart of the post fetch is disclosed: the first run of `s05_fetch_sample.py` was slow in its (now unused) sample-C step, so it was stopped and restarted with a per-block cache and `S05_LATEST=469263564` (the latest finalized slot the first run had used) so the 24 anchors were identical; one stray block (469067685, from a restart with a newer `getSlot`) was fetched and deleted before any analysis. A: first anchor 469058660, last anchor 469262564, `latest_finalized_slot_at_plan` 469263564 (`out/s05_plan.json`). The sample-C step found 0 migration txs in A's blocks, so the migrate evidence is M (section 3b), as the ruling asks.
- Item 2 event bars counted from A alone (CompleteEvent 1, CreateEvent 25) are met without a further extension; no extension of A was added.
- Item 4: P (469015660..469055069, 4 distinct UTC hours) and P2 (468975660..469014669, 3 hours) together span 6 distinct hours.

## 5. Decode paths under test (item 5)

Frozen blobs at this base: `observe/trade_decode.py` `238942a6b3c5425389eddfde4d11268c300acbec`, `observe/trade_store.py` `ea4e11eddf9f034e3bc7318ce8743337d753f350`, `tools/pump_history_backfill.py` `9a8bebb32adcf86de060b55f5a08110d11c0a550` (all three equal EXP027_DECODER_REF; `out/s06_parity.json` `blob_sha` shows `observe/trade_decode.py` equal at 1f7a89c). Paths run: `records_from_logs(event_v=True)`, `rows_from_block(event_v=True)` (walker: 2,712 pump trades, 25 creates, 2 migrations, 9,252 unresolved PumpSwap rows in A; the unresolved rows are a per-tx empty pool cache, a resolution gap, not a decode failure), `decode_create_event`, `decode_complete_event`, `decode_migration_event`, `decode_extra_event`.

Follower's own migration-row path: `tools/fast_tip_follower.py` `inner_migration_rows` (blob at main 63a206a5d83f777116961a3336308a515dd3991d). It builds a reduced view of the block and calls `rows_from_block(view, {}, FEED, event_v=True)` again (quoted: `ev = rows_from_block(view, {}, FEED, event_v=True)`), keeps only rows with `event_source == "inner_event"`, and guards each row with `canon(str(r.get("mint"))) == r.get("pool")` (pool must be the canonical PumpSwap PDA of its mint). It therefore only maps fields of an event that `trade_decode`/`pump_history_backfill` decode. Follower running blob: to be named by the manager.

## 6. Parity results (items 6, 7)

Independent decoder: `s06_parity.py` (own base58, borsh walker from the pinned IDLs, invoke-stack attribution; shares no code with `observe/`). Same code on every column. P and P2 combined, plus M_pre, are "pre".

| | post: A (240 blocks) + M (40) | pre: P (100) | pre: P2 (100) | pre: M_pre (40) |
| --- | --- | --- | --- | --- |
| successful txs analysed | 19,214 + 40 | 8,340 | 7,920 | 40 |
| trade events compared, frozen vs independent (Buy / Sell / Trade) | 4,172 / 5,082 / 2,710 | 2,077 / 2,348 / 1,440 | 2,060 / 2,282 / 1,431 | none |
| **field inequalities (N6)** | **0** | **0** | **0** | 0 |
| unknown event discriminators (N2) | **0** | **0** | **0** | **0** |
| unknown instruction discriminators (N4) | **0** | **0** | **0** | **0** |
| non-trade events (Create, Complete, migration, CreatePool, InitBoost, PostCompleteBuy, Sweep, BoostBuyAndBurn) value-unmatched / total | 0 / 508 | 0 / 38 | 0 / 23 | 0 / 109 |
| log-truncated txs | 81 (incl. 40 of M) | 52 | 50 | 40 of 40 |
| CPI-only events (N1), all in log-truncated txs | 17 trade/other + 41 migration + 32 InitBoost (migration events are CPI-only by design) | 10 | 18 | 9 + 40 migration + 29 InitBoost |
| PumpSwap events refused by the 100,000-SOL cap | 602 of 12,566 log trade events | 246 | 194 | 0 |
| capped events on a WSOL quote (quote mint from the same tx's `quote_mint` account) | **0 of 602; 0 with no mint identified** | 0 | 0 | 0 |
| frozen rows vs independent log trade events | 11,964 vs 12,566; the 602 difference equals the cap exactly (308 txs, all `explained_by_cap`) | 5,865 vs 6,111 (246 = cap, 127 txs) | 5,773 vs 5,967 (194 = cap, 98 txs) | 0 |
| trade event with no trade instruction (N5) | 10, all `boost_buy_and_burn` (10 instructions, 10 events) | 5 | 4 | 0 |

Every non-decode is explained: N5 cap (frozen row count differs by exactly the capped events), N5 boost slices (a `boost_buy_and_burn` instruction emits a BuyEvent), N1 CPI-only events (all inside log-truncated txs, RPC log cut at 10 KB; invisible to `records_from_logs`, the known blind spot). Unexplained: **0**.

Truncated-log and CPI-only rates for WSOL-only PumpSwap trade txs (`out/s08_universe_split.json`): post 18 / 2,770 (0.65%); P 26 / 1,583 (1.64%); P2 18 / 1,473 (1.22%); pre combined 44 / 3,056 (1.44%). WSOL-pool PumpSwap events seen only as CPI: post Sell 1 of 1,207, Buy 8 of 1,573; pre Sell 0 of 1,262, Buy 9 of 1,814. The post rate is below the pre rate: no "post above 2x pre and above 1%" trigger. No single-pool episode needed to be excluded.

Mayhem flag and CreateEvent fields (`s14_mayhem_parity.py`, explicit boolean comparison because the value-containment compare skips booleans): `bf.decode_create_event` vs the independent parse on every CreateEvent: post 25 (15 mayhem), P 17 (6), P2 7 (2): **0 mayhem mismatches, 0 mint/creator/bonding_curve mismatches**. The frozen `decode_program_data` row of a CreatePoolEvent has no mayhem key (stated, not compared); the independent CreatePoolEvent `is_mayhem_mode` is 0 on all 3 + 2 CreatePoolEvents inside A/P blocks; the pool-account flag is compared across pools in section 7.

Per-event-type table against `tools/a3_redeploy_pins.json` (`out/s12_pins_table.json`): trailing bytes: every pinned event is exactly as pinned (8 on Trade/Buy/Sell/Withdraw with n = n_trailing8 in both post and pre; 0 on CompletePumpAmmMigrationEvent, CreatePoolEvent, InitBoostEvent, CreateEvent, SweepBondingCurveFeeEvent, ExtendAccountEvent, CollectCreatorFeeEvent, Init/CloseUserVolumeAccumulatorEvent). `disc_len`: the two exceptions are in "Open items" above (identical in pre).

C3' (c), the trailing u64 by universe (`s08`): 

| class | post events / non-zero | pre (P+P2) events / non-zero |
| --- | --- | --- |
| WSOL-only PumpSwap events | 2,780 / **0** | 3,076 / **0** |
| pump TradeEvents, quote SOL (zero pubkey) or WSOL | 2,336 / **0** | 2,236 / **0** |
| pump TradeEvents, other quote | 376 / 0 | 655 / 69 (quotes `MAG7thco...` 52, `DGuudmnJ...` 5, `PreweJYE...` 12) |
| PumpSwap non-WSOL-only | 6,710 / 1 (quote `Pre8AREm...`) | 5,915 / 8 |
| PumpSwap mixed / no trade instruction | 65 / 0, 316 / 0 | 39 / 0, 193 / 0 |

C3' (d), Token-2022 (`s08`, mints read now, finalized): WSOL mint owner `TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA` (legacy SPL Token), 82 bytes. Base mints of every CreatePoolEvent and CompletePumpAmmMigrationEvent seen: post 80 Token-2022 + 4 SPL Token, pre 82 Token-2022 + 2 SPL Token. Post WSOL-quoted rows: CreatePool 26 Token-2022 + 1 SPL Token, migrations 26 Token-2022 + 1 SPL Token (the 40 M txs contribute 27 WSOL migrations). **TransferFeeConfig: 0 of all rows (post and pre). Extension types present: 18 (MetadataPointer) and 19 (TokenMetadata) on every Token-2022 mint, post and pre; extension types in post and absent from pre: none; TransferHook, PermanentDelegate, NonTransferable, DefaultAccountState: 0.** Caveat: mint state is read now (an extension cannot be added after mint creation).

## 7. Migration and pool-creation events (items 9 (ii), (iii))

`s11_migration_economics.py`, WSOL-quoted migrations of M (post, 27) and M_pre (33); chain structure constants only, no path after a first print.

| field | post (27) min / median / max | pre (33) min / median / max |
| --- | --- | --- |
| CompletePumpAmmMigrationEvent sol_amount | 44,262,268 / 84,990,359,716 / 85,972,654,318 | 12,582,941 / 84,990,359,056 / 94,846,797,684 |
| mint_amount (= CreatePool pool_base_amount) | 204,536,028,675,450 / 206,900,000,000,000 / 206,900,000,000,000 | 185,399,044,810,855 / 206,900,000,000,000 / 206,900,000,000,000 |
| pool_migration_fee | 15,000,001 (all) | 15,000,001 (all) |
| CreatePool pool_quote_amount (= sol_amount) | same as sol_amount | same as sol_amount |
| initial_liquidity | 95,696,725,383 / 4,193,388,296,962 / 4,193,388,373,364 | 51,023,626,810 / 4,193,388,282,604 / 4,193,388,381,407 |
| creator_fee_bps (CreatePool) | 0 (all) | 0 (all) |
| mayhem pools | 9 of 27 | 11 of 33 |
| first-print V (virtual_quote_reserves of the oldest PumpSwap Buy/Sell event of the pool) | observed 12 of 20 pools tried: min 0, median 8,792,252,647, max 17,584,506,049; 6 of 12 inside [17.5e9, 17.7e9]; **8 NOT OBSERVED** (page budget) | observed 4 of 10 tried: min 0, median 8,792,252,846.5, max 17,584,506,117; 2 of 4 inside the band; **6 NOT OBSERVED** |

Post values lie inside the pre range on every field above (post min sol_amount 44,262,268 is above pre min 12,582,941; post max 85,972,654,318 is below pre max 94,846,797,684). V is inside the band on some post pools, so FLOOR's universe does not dry up on this sample; values far from the band exist in pre and post alike (the stored V is a pool state at the first print; its meaning was not examined).

Pool accounts (`getMultipleAccounts` now, 12 post and 12 pre WSOL pools): owner `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` on all 24; account discriminator `f19a6d0411b16dbc` on all 24; data length 301 on all 12 post and on 11 of 12 pre (one pre pool has 287 bytes, an older shape); `coin_creator` non-zero on all 24; pool base token account owned by Token-2022 `TokenzQd...` and quote token account by `Tokenkeg...` on all 24 (the pool account itself has no token-program field; the two token accounts' owner programs are quoted); `creator_fee_bps` 0, `can_edit_creator_fee` 0 on all 24; `is_holder_reward` 1 on 1 post and 2 pre; `is_mayhem_mode` 1 on 4 post and 3 pre. No post pool differs from the pre pools in owner, length (post uniform at the pre majority), discriminator, `coin_creator` set, or token-program fields.

Migration event parity (frozen vs independent, `s06`): CompletePumpAmmMigrationEvent 41 post (all CPI-only) / 41 pre (M_pre 40 + 1): 41 and 41 decode, 0 value-unmatched; CreatePoolEvent 43 post / 43 pre: 0 unmatched; InitBoostEvent 32 / 30: 0 unmatched; CompleteEvent 1 / 1, PostCompleteBuyEvent 1 / 1, BoostBuyAndBurnEvent 10 / 9, CreateEvent 25 / 24, SweepBondingCurveFeeEvent 27 / 20: 0 unmatched each. Value containment does not compare booleans: the mayhem flag is compared explicitly in section 6.

## 8. Write enumeration (item 10)

Route (a), public RPC, 0 credits (`s07_upgrade_history.py`, `s09_classify_pd_sigs.py`). `getSignaturesForAddress` on the pump programdata `B5MvUwXdiW1NMM6QFFD3ssPKBujD4zMohncbM73Z2BQu`, finalized, 4 pages of 1,000: 3,631 signatures back to slot 241767269, so **COMPLETE back to the pin** (the list reaches 458921440 and beyond; `out/s07_upgrade_history.json`). After the pin: 2,582 successful and 904 failed signatures. The great majority are pump-program transactions that carry the programdata account read-only (first seen 2026-10-24).

Classification (programdata writable? BPF Upgradeable Loader called top-level or inner?): `out/s09_classify_late.json` covers **every successful signature at slot >= 469,000,000** (7: the write below and 6 pump transactions of 2026-11-14 with the programdata read-only). The last successful signature before the deploy is at slot 468678252 (2026-11-12T21:36:54Z, a pump transaction); the required range, from 2026-11-13T19:00Z (about slot 469,045,000) through 469055660, contains **no other successful signature** on the account.

**Upgrade writes in the required range: exactly one.**

| slot | UTC | signature | form |
| --- | --- | --- | --- |
| 469055660 | 2026-11-13T20:12:28Z (blockTime 2026-11-13T20:12:28Z) | `3L8JuKZBtP1LNN1PxNHAyRKTTjNHAmptWYgzyyLnuwxxysTWRsueRFAezL7YF7qL6Nkfc1iV8iKZZMGLXMdV5WEY` | programdata writable; BPF loader invoked as an INNER instruction of Squads (`SQDS4ep6...`), fee payer `7zc8kzBr...` |

The deploy slot in the programdata header (469055660) matches. Back to 458921440 (preferred, not required): `s09 all` classifies every successful signature in slot order; at the time of writing the classified part (see `/data2/mal/scratch/a3-repin-1114/s09_all.log`; `out/s09_classify_sample.json` for a 12-tx spread) shows no writable-programdata transaction other than the above; the unclassified remainder is **NOT OBSERVED** as classified, but cannot hold an unseen write in the required range because the signature list itself has no entry there. The 10-19 lesson (a write other than the flagged one) is covered for the required range by that emptiness.

## 9. Pins diff (item 11)

`tools/pump_structure_pins.json` regenerated with the monitor's own `--write-pins` (public RPC, `s02_write_pins.py`) at slot **469269411**, 2026-11-14T08:58:57Z, after the last sample fetch (last block of A: 469262573; M: 469070200). Diff against main: `programs.pump.deploy_slot` 458921440 -> 469055660, `deploy_utc` 2026-10-19T17:47:02Z -> 2026-11-13T20:12:28Z, `sha256` 0b692730...b508 -> c849155c1d5bf990c564bad8df406d704ed05b8d3dc4ee30ce2350bfbc4edf3a, `data_len` 10485760 (unchanged), plus `pinned_utc` and `pinned_at_slot`. "UNEXPECTED DIFFS: []": accounts, PumpSwap, fees and boost entries are byte-identical. `tools/a3_redeploy_pins.json` and `tools/a3_fees_check_pins.json` are not edited. Their evidence still holds on the measured structure (trailing set, IDL sha256s, `required_events`); `a3_redeploy_pins.json` `reviewed_programdata` records the 0b692730 hash as RECORD ONLY (no code compares it); `disc_len` is discussed under "Open items".

## 10. Not checked

- The follower's running blob and its status/unit (manager).
- Any fee computation or a simulation of a PumpSwap buy/sell on a new pool (not required, item 9 (iii)).
- The meaning of the 8-byte field, the stored V values far from the band, or the new creator-fee error path.
- Events outside the sampled blocks: 240 + 200 blocks are about 1.5% and 1.0% of the slots in their windows; rare types beyond the counts above (v1 `create`, v1 `migrate` post-deploy, PostCompleteBuyEvent 1, CompleteEvent 1) are thin.
- First-print V for 8 post and 6 pre pools; the whole of the 2,582 signature classification (section 8).
- Whether logsSubscribe delivers the same truncated logs as `getBlock` (assumed, as in 1021).
- Outcomes, prices, P&L, profitability of any book: none measured.
