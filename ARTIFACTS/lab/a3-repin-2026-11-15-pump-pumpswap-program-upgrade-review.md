# Review: pump AND PumpSwap program redeploys of 2026-11-15T17:20Z (slots 469811025 and 469811067), re-pin

Builder: Claude worker, 2026-11-15 (started `date -u` 19:36Z), mal-research-0. Public RPC (api.mainnet-beta.solana.com) and unauthenticated GitHub only. 0 Helius credits, no key or env file. No tape, arena, book, ledger, shadow, declog, tip or FLOOR file opened; no price path, outcome or P&L computed. Scripts and small outputs: `ARTIFACTS/lab/program-upgrade-1115/` (`out/*.json`); raw responses are off-repo in `/data2/mal/scratch/a3-repin-1115/`. This is a reading of chain and repo facts. No transaction was built, signed, simulated or sent. No sentence here says any book makes money; no book has cleared the promotion gate.

Standard: `/data/mal/hunt-1008/ops/QP-RULING-1115-a3-repin-dual.md` (sha256 64e37d09...), which carries `QP-RULING-1114-a3-repin-pump.md` Q1 items 1-13 over with the new slots. Each section below says which bar it answers.

## Verdict

**NOT CLEAN on three bars of QP-RULING-1115 (stage 1). The pins are NOT changed in this PR. Draft PR; the case goes to QP.** No unknown discriminator, no field inequality, no trailing-byte change, no prefix inequality, no new Token-2022 extension. The three bars that read not clean:

| Bar | Result |
| --- | --- |
| (b) fee triples, post subset of pre, WSOL-only Buy/Sell | **NOT MET as declared.** Post has 24 distinct (lp, protocol, coin_creator) bps triples, pre (P + M_pre) 23. Five post triples are absent from the declared pre set: `0,5,0` (1 Sell), `2,93,0` (3 Buy, 1 Sell), `20,0,5` (1 Sell), `20,5,30` (1 Buy), `20,5,45` (2 Buy). Report-only post-hoc extension (s19, section 6c): four of the five occur in the 35,474 pre-redeploy txs of the 2026-11-14 review samples (PumpSwap at the old binary); only `20,0,5` (1 SellEvent, protocol_fee_bps 0) occurs in no pre data read. The extension does not replace the declared bar. Goes to QP. |
| Stage 1 sample: CompleteEvent >= 1 | **NOT MET.** A1 + M hold 0 `pump.CompleteEvent` (41 `CompletePumpAmmMigrationEvent`, 17 `CreateEvent`). All other stage 1 counts are met (section 4). |
| Item 9 (ii) migration economics, post inside pre range | **NOT MET.** Of 29 WSOL post migrations, `sol_amount` min 14,991,574 (pre min 34,744,342), `mint_amount` min 195,275,405,814,792 (pre min 201,060,921,814,347), `initial_liquidity` min 55,693,416,671 (pre min 84,785,637,697): the post minima lie below the pre minima; maxima, medians and `pool_migration_fee` (15,000,001 in both) are equal. Per ruling item 9 (ii) a post value outside the pre range fails pending QP. |

Everything else that this builder measures passes (table in section 11).

## 1. Chain facts read (stage 1; ruling Q1 item 11, (d), (e))

(filled by stage results below)

## 3. Sample rule, DECLARED BEFORE ANY SAMPLE FETCH (ruling Q1 item 1 and (a))

Declared at the commit that adds this section together with `program-upgrade-1115/s05_fetch_sample.py` and `s10_fetch_migrate_sample.py` (the code is the rule). At this commit only these were read from the chain: the three programdata accounts and three config accounts (`s01`), the monitor's `--write-pins` read to scratch (`s02`), the docs and IDL hashes (`s03`), and the write enumeration of the two programdata accounts (`s07`, signatures and transactions of the programdata addresses only). No block of any sample had been fetched. Structure only: counts, discriminators, layout lengths, field equality of already-decoded fields.

Constants: `D_pump` = 469811025 (2026-11-15T17:20:10Z), `D_swap` = 469811067 (2026-11-15T17:20:20Z), `D` = `D_swap`.

- **CHAIN-ONLY.** No tape, `/data/mal/arena*`, `/data2/mal/arena-tip-feed`, `/var/lib/mal*`, book, ledger, shadow, declog or FLOOR file is opened or parsed. Anchors are slot numbers only.
- **Stage 1, sample A1 (post):** 24 anchors evenly spaced (integer `lo + (hi-lo)*i//23`) between `D`+150 and (latest finalized slot at run time, `getSlot`) minus 1000. At each anchor the first 10 non-skipped slots at or after the anchor are fetched (`getBlock`, finalized, full, maxSupportedTransactionVersion 1). Every successful tx touching pump (`6EF8rrec...`), PumpSwap (`pAMMBay6...`) or fees (`pfeeUxB6...`) is in the sample. Target 240 blocks, which spans every UTC hour from `D` to the fetch instant (evenly spaced anchors). Stage 1 bars (ruling (a)): >= 120 blocks from `D`+150; >= 3 distinct UTC hours; >= 6,000 successful pump/PumpSwap txs; WSOL-only PumpSwap Buy+Sell events >= 1,000; CreateEvent >= 10; CompleteEvent >= 1; migration and CreatePool events through M; every PumpSwap event type seen in P appears in A1 or M (a type absent from both is named, not a fail).
- **Sample P (pre control):** 12 anchors evenly spaced between `D_pump`-60000 and `D_pump`-600, 10 non-skipped blocks each (`SAMPLE=pre`, 120 blocks; every block ends at least 150 slots before `D_pump`, so before 2026-11-15T17:20:10Z). Bar: >= 100 blocks over >= 5 distinct UTC hours (60,000 slots is about 6.7 h). It sits after 2026-11-15T07:10:06Z (both programs at their pins in that record), so it ends before the earliest enumerated write (`s07`).
- **M (post migrate sample):** `s10_fetch_migrate_sample.py` (`SAMPLE=m`): candidates are the successful signatures of `mon.MIGRATION_FEE_ACCOUNT` paged back from now (the MIGRATE_K rule of `tools/a3_redeploy_check.py`); M = the first 40 settled (blockTime <= now - 480 s) candidates with slot >= `D`+150, oldest first, each fetched whole (`getTransaction`, logs plus inner instructions), kept when `parse_migrate_tx` reads a CompletePumpAmmMigrationEvent. Bars: >= 20 usable, >= 10 WSOL-quoted. **M_pre:** the last 40 candidates with slot <= `D_pump`-150, same keep rule, bar >= 10.
- **Frozen decoder:** the three EXP027_DECODER_REF blobs (`observe/trade_decode.py` 238942a6, `observe/trade_store.py` ea4e11ed, `tools/pump_history_backfill.py` 9a8bebb3) via `rows_from_block(event_v=True)` and `records_from_logs(event_v=True)`. **Independent decode:** `s06_parity.py`, a borsh walker driven by the docs IDLs (no `observe/` code).
- **Non-decode** is any of N1-N6 as defined in `a3-repin-2026-10-21-program-upgrade-review.md` section 3. Pass bar: 0 unexplained.
- **PumpSwap events are tests, not controls** (ruling (b)): prefix parity pre vs post on every PumpSwap event type seen; trailing bytes (pinned types equal `tools/a3_redeploy_pins.json`, unpinned types post equal pre); the 8-byte field; fee-bps triples (lp, protocol, coin_creator) on WSOL-only Buy and Sell, post set must be a subset of pre set, counts per triple; reserve and virtual-reserve field parity on Buy and Sell.
- **Instruction shape (ruling (c1), stage 5):** from A1, M and P, every successful WSOL PumpSwap `buy`, `buy_exact_quote_in`, `sell` instruction (top-level or inner): 8-byte discriminator, data length, account count, and the owner program of each account position (one `getMultipleAccounts` pass over up to 200 instructions per side, at most 100 accounts per call). The executor's shape at e5fd255b (disc, length, account count, fixed addresses, PDA derivations) is compared; at least 20 successful WSOL buy_exact_quote_in and 20 sell post are required. Failed txs with these discriminators are counted by error word, post and pre.
- **(c3) pool accounts:** the 24 pool accounts of the 1114 review plus 10 pools created after `D` (from CreatePoolEvents of A1/M), read with `getMultipleAccounts`: owner, data length, 8-byte discriminator, and the `pumpswap_tx` pool-state offsets.
- **Mint/pool state (Token-2022):** one `getMultipleAccounts` read of the base mints of M (post) and M_pre, plus the WSOL mint owner: owner program and extension types.
- **(c2) keyless simulation** is not this builder's: a manager job. Section 11 holds the placeholder.

### 3-stage-2. Stage 2 (completion), declared in the SAME commit, before any stage-1 fetch (ruling (a))

Not run by this PR. Code is in place and is the rule:
- **A2:** `SAMPLE=a2 A1_HI=<hi of s05_plan_a1.json> s05_fetch_sample.py`: 24 anchors x 10 non-skipped blocks evenly spaced between (A1's hi + 1000) and (latest finalized at run time) - 1000, to be run after 2026-11-16T03:30Z so that A1 + A2 cover >= 10 distinct UTC hours after `D` (through at least 2026-11-16T03Z). At least 120 further blocks.
- **M2:** `SAMPLE=m2 M_LAST_SLOT=<last slot of M> s10_fetch_migrate_sample.py`: the next 40 settled migrate transactions after M.
- Every item 6, 7 and 9 bar holds on A1 + A2 + M + M2. The result goes into a T1 PR with QP OK on its exact head by 2026-11-16T15:00:00Z. If stage 2 fails or is late, the manager creates the lane STOP file (ruling (a)).


## 4. Notes on the declared samples as run

- A1: 24 anchors x 10 non-skipped blocks, `lo` 469811217 (= `D`+150), `hi` 469850395, latest finalized slot at plan 469851395 (2026-11-15 about 19:4xZ); 240 blocks, 255 getBlock attempts, 19,044 successful txs touching pump, PumpSwap or fees, 3 distinct UTC hours (17, 18, 19), first block exactly `D`+150 (`out/s05_plan_a1.json`). Stage 1 counts against the bars: blocks 240 (>= 120) met; hours 3 (>= 3) met; txs 19,044 (>= 6,000) met; WSOL-only PumpSwap Buy+Sell events 1,342 + 1,033 txs-level classes, event counts in section 6 (>= 1,000) met; CreateEvent 17 (>= 10) met; **CompleteEvent 0 (>= 1) NOT met**; migration and CreatePool through M (41 and 44) met.
- P: 12 anchors x 10 blocks, 120 blocks, 8,264 successful txs, UTC hours 13 to 17 (5 distinct), last block 469810434 = 591 slots before `D_pump` and before 17:20:10Z. M: 40 usable (29 WSOL-quoted; 26 candidates were not migrate txs, 0 unfetchable), slots 469811458 to 469823253. M_pre: 40 usable (33 WSOL), slots 469798787 to 469810606. Bars met.
- The sample is successful transactions only (the s05 index rule); failed transactions are counted separately from the cached blocks (s20).
- One declared tool detail: `s02` was run twice (469849077, then 469855021 after the last sample fetch); the second is the re-pin read of section 9.

## 5. Decode paths under test (item 5)

Frozen decoder blobs in this tree: `observe/trade_decode.py` 238942a6, `observe/trade_store.py` ea4e11ed, `tools/pump_history_backfill.py` 9a8bebb3, each equal to the EXP027_DECODER_REF prefix (`out/s06_parity*.json` `blob_sha`). Paths run: `rows_from_block(event_v=True)`, `records_from_logs(event_v=True)`; the independent walker `s06_parity.py` is IDL-driven and shares no `observe/` code.

**Follower's running blobs (manager fact, job #2837, 2026-11-15T19:42:13Z, names and hashes only; not read by this builder):** the tip follower is PID 2625920, running since 2026-11-13T06:03:32Z, NRestarts 0, source tree cdc72b080127d4e396a459e84cb0527f96119b23; status file decoder_blobs: `observe/trade_decode.py` 238942a6b3c5425389eddfde4d11268c300acbec, `observe/trade_store.py` ea4e11eddf9f034e3bc7318ce8743337d753f350, `tools/pump_history_backfill.py` 9a8bebb32adcf86de060b55f5a08110d11c0a550 (the same three as the 11-14 review; no restart since). Feed counters at 19:33:27Z (job #2834): event_v_errors 0, event_v_mismatch 0, event_v_missing 0 (pumpswap 0) of 49,164,393 stamped; migrations_inner_errors 0; pool_mismatch 406 of 1,685 added; decoder_blobs_pinned true. The follower's inner-migration path was not re-run in this review; it maps fields of an event `trade_decode` decodes (11-14 review section 5).

## 6. Parity results (items 6, 7; ruling (b))

Answers: item 6 and ruling (b) (a), (b), (c), reserve fields. Source: `out/s06_parity*.json`, `s17`, `s12`, `s08`, `s14`.

- **Discriminators.** 0 unknown event discriminators and 0 unknown instruction discriminators (pump, PumpSwap, fees) in A1, P, M, M_pre.
- **Field inequalities.** 0 on 12,482 frozen trade rows compared post (TradeEvent 3,363; BuyEvent 3,824; SellEvent 5,295), pre 5,810 rows (1,596 / 1,904 / 2,310 compared fields; see `s06_parity_pre.json`). The compare covers mint/pool, trader, timestamp, sol and token amounts, `quote_reserve`, `base_reserve` (pool reserves), `virtual_quote_reserves`, fees, `ix_name`, side (`s06_parity.py compare()`): this is the reserve and virtual-reserve parity of ruling (b). 0 value-unmatched non-trade events: CompletePumpAmmMigrationEvent 41, CreateEvent 17, CreatePoolEvent 44, SweepBondingCurveFeeEvent 34, BoostBuyAndBurnEvent 14, InitBoostEvent 30, SweepPoolFeeEvent 2.
- **Non-decodes (all explained).** N5 "records_from_logs row count differs": 291 post, all `explained_by_cap` (frozen decoder's per-swap SOL cap on non-WSOL quote swaps: 269 BuyEvent and 282 SellEvent refused `sol_lamports > cap`, by design; pre control equally, 152 explained); N5 "trade event with 0 recognised trade instruction": 14 post, 3 pre, all `boost_buy_and_burn` BuyEvents or `multi_hop_swap` legs (`out/s21_n5_explain.json`); 0 truncated-log txs in A1 or P (`log_truncated_txs` 0).
- **Trailing bytes (b) / prefix parity (a).** Every PumpSwap event type seen, post vs pre (`out/s17_pumpswap_events.json`): BuyEvent 4,093 post / 2,055 pre, all with 8 trailing; SellEvent 5,577 / 2,461, all 8; WithdrawEvent 1 / 1, 8; BoostBuyAndBurnEvent 14 / 3, 0; ClaimCashbackEvent 36 / 6, 0; CloseUserVolumeAccumulatorEvent 588 / 210, 0; CollectCoinCreatorFeeEvent 9 / 6, 0; CreatePoolEvent 44 / 41, 0; InitBoostEvent 30 / 22, 0; InitUserVolumeAccumulatorEvent 117 / 37, 0; SweepPoolFeeEvent 2 / 1, 0. ExtendAccountEvent: 1 pre, 0 post (named, not failed). No other event types. pump events: TradeEvent 3,363 post, all 8 trailing; every other pump event 0. Pinned types equal `tools/a3_redeploy_pins.json` `trailing_bytes`; unpinned types equal pre. 0 prefix inequalities (every blob consumed by the walker to its length plus the trailing block). Observed blob lengths of `CreateEvent` and `TradeEvent` lie outside the pinned `disc_len` in post AND pre (known drift, ruling QP-RULING-1115-c14-disc-len; not caused by this redeploy: CreateEvent post 324..376, pre 334..372; TradeEvent post 390..507, pre 390..473; the post maximum 507 exceeds the pre maximum 473 and the 11-14 post maximum is not re-read here, reported as is).
- **8-byte field (c).** Non-zero: 0 of 2,375 WSOL-only PumpSwap Buy/Sell events post (0 of 1,316 pre) and 0 of 2,582 SOL-quoted pump TradeEvents post (0 of 1,163 pre). Non-zero values occur only on token-quoted events, post and pre alike (post: non_wsol_only 51 of 6,958; mixed 5 of 101; pump TradeEvents with quote `Pren1...` 25 of 781).
- **Fee triples (new, ruling (b)). NOT MET as declared**, see Verdict. Counts per triple post and pre (WSOL-only, Buy and Sell) are in `out/s17_pumpswap_events.json` `wsol_only_fee_triples`; the dominant triples are `20,5,95` (post 908, pre 605), `2,93,30` (594, 192), `20,5,5` (202, 135). The `coin_creator_fee_basis_points` component takes 20+ values in both groups.
- **Mayhem flag (s14).** 17 CreateEvents post, 0 mayhem mismatches, 0 mint/creator/bonding_curve mismatches (10 mayhem-true); pre 8 CreateEvents, 0 mismatches (3 mayhem-true). 4 CreatePoolEvents post (pool mayhem true 0), 1 pre.
- **Token-2022 (d).** WSOL mint owner `TokenkegQ...`, no TransferFeeConfig (as pre). Post base mints: 82 Token-2022 rows (all extensions 18 and 19 only: MetadataPointer, TokenMetadata), 3 Tokenkeg rows; pre 80 and 1. 0 TransferFeeConfig, 0 TransferHook, PermanentDelegate, NonTransferable or DefaultAccountState, 0 extension type absent from pre (`extension_types_post_not_in_pre` empty). Token-quoted quote mints with non-zero trailing field carry transfer-fee extensions (type 1) in post and pre alike.
- **Docs and IDL (section 2).** Head of pump-public-docs main is still `639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22` (2026-10-13), compare status identical; the sha256 of the pump, pump_amm and pump_fees IDLs equal the pins in `tools/a3_redeploy_pins.json` (`out/s03_docs_idl.json`). So the docs did not move with the redeploys.

### 6c. Post-hoc extension (report-only): fee triples against more pre-redeploy chain (s19)

Declared here, after `s17` showed the five absent triples; it reads only cached chain blocks of the 2026-11-14 review (PumpSwap unchanged the whole time, 35,474 successful txs, 5,839 WSOL-only Buy/Sell events). Union of that set and P: 30 distinct triples. Post triples absent from P: `0,5,0`, `2,93,0`, `20,0,5`, `20,5,30`, `20,5,45`; absent also from the extension: `20,0,5` only. Not part of the declared bars; it informs QP.

## 7. Migration and pool-creation events (item 9 (i)-(iv), ruling (c3))

- **(i) Pool-table inputs.** 0 value mismatches on CreateEvent, CompleteEvent (none present), migration and CreatePool rows; mayhem flag parity in section 6.
- **(ii) Migration economics** (29 WSOL post vs 33 WSOL pre; `out/s11_migration_economics.json`). `sol_amount` post min 14,991,574 / median 84,990,359,056 / max 90,049,772,950 vs pre 34,744,342 / 84,990,359,056 / 87,458,594,583; `mint_amount` post 195,275,405,814,792 / 206,900,000,000,000 / 206,900,000,000,000 vs pre 201,060,921,814,347 / same / same; `pool_migration_fee` 15,000,001 in all; `creator_fee_bps` 0 in all. Post minima lie below pre minima and the post maximum `sol_amount` is above the pre maximum: **item 9 (ii) NOT MET as declared** (Verdict). First-print V (virtual_quote_reserves of the oldest Buy/Sell seen): observed for 11 of 20 post and 6 of 10 pre pools (page budget); values 0 (observed in both groups), min/median/max post 0 / 0 / 17,584,506,268, pre 0 / 8,792,252,644 / 17,584,505,399; inside [17.5e9, 17.7e9]: 5 of 11 post, 3 of 6 pre. Disclosed, not a bar.
- **(iii) Pool accounts (c3).** The 24 pool accounts of the 11-14 review (12 post-1114, 12 pre-1114), read now: 24 of 24 have the same owner, data length and 8-byte discriminator as the 11-14 values. 10 pools created after `D` (first 10 WSOL pools of M): owner `pAMMBay6...`, data length 287 (some) or 301, 8-byte discriminator `f19a6d0411b16dbc`; `coin_creator` set on 10 of 10 and equal to the CreatePoolEvent's; base mint equal to the event's on 10 of 10; token-account owner programs recorded in the JSON (`c3_pool_accounts`). Lengths: 301 on 23 of the 24 old pools and 9 of the 10 new, 287 on 1 and 1 (the same split).
- **(iv) Fees.** The 2026-11-15T19:10:06Z monitor record's `pins_changed` reason (manager's brief, second-hand, not read by this builder) names `programs.pump` and `programs.pumpswap` only. `out/s01_programdata.json` (read at slot 469849002..030): the fees programdata and the three config accounts (`bonding_fee_config`, `pumpswap_fee_config`, `pumpswap_global_config`) equal the pinned sha256 and length.

## 8. Write enumeration (item 10, ruling (d))

Route (a): `s07_upgrade_history.py` on public RPC, 0 credits, 177 RPC attempts (`out/s07_upgrade_history.json`). Range: from 2026-11-15T07:00:00Z (paged until a signature older than that: 1 page of 1,000 per account suffices; COMPLETE true for both) through now.
- **pump programdata `B5MvUwXd...`:** 1 signature in the window, successful. **Exactly one Upgrade**: slot 469811025, 2026-11-15T17:20:10Z, signature `37aGknd9Jy8gHXPQXbDj6mg1ytSEWNJFqbT3Z7qCrzyjJ2Z5hvv4B7fAvf4nt4boSwKL5US7SKiqDfbpkbukCXgN`, a BPF-loader Upgrade (tag 3) as an inner instruction of a Squads (`SQDS4ep6`) transaction.
- **PumpSwap programdata `6naEzKeU...`:** 176 signatures in the window (173 successful, 3 failed, not writes). **Exactly one Upgrade**: slot 469811067, 2026-11-15T17:20:20Z, signature `2uB6vN55cWe95KmCpk5FH7jkaFoXjLt3MLDyETiLfuQVJzcUt1FeES8W1NEwJufvyQMeaWZfVdB7R1C7RELp8uCS`, inner loader Upgrade under Squads. The other 172 successful signatures reference the programdata without calling the loader.
- No write before 17:20:10Z in the window; P ends 591 slots before `D_pump` and A1 starts exactly 150 slots after `D_swap`, so each sample sits wholly inside its interval. The 3 failed PumpSwap-programdata signatures are listed in the JSON by slot only if the manager needs them (not classified).

## 9. Pins diff (item 11, ruling (e))

**Pins NOT changed in this PR** (the review is not clean, see Verdict). The monitor's own `--write-pins` path was run read-only to scratch twice (s02): at slot 469855021 (2026-11-15T19:57:46Z, after the last A1/M sample block 469850404) it produced exactly these differences from main's pins and no other: `pinned_at_slot` 469269411 -> 469855021; `pinned_utc` -> 2026-11-15T19:57:46Z; `programs.pump` deploy_slot 469811025, deploy_utc 2026-11-15T17:20:10Z, sha256 `bfd2a2bd4a062f03f2c389bb836fb63c1b9b7983e255e4f84a826287ef31b8be`, data_len 10485760; `programs.pumpswap` deploy_slot 469811067, deploy_utc 2026-11-15T17:20:20Z, sha256 `78046072fdf83eee2ac366c9da0bee22cdb030a2d7b194aa9ab81a14ea13671e`, data_len 10485760. No other deploy slot or hash was seen (no newer write). Accounts, fees and boost: byte-identical. The fixture was re-recorded by `s15_rerecord_fixture.py` (decodes to [469811025, 469811067, 456132171], context slot 469855044), and the line-354 test list changed to the same. These three changes are saved as a patch NOT applied: `program-upgrade-1115/out/repin_candidate_NOT_APPLIED.patch` (`git apply` on main). With the candidate applied (not committed), `MAL_ARENA_TEST=1 /data/mal/venv/bin/python -m pytest -q tools/test_pump_structure_monitor.py tools/test_a3_redeploy_check.py tools/test_a3_halt.py tools/test_h5_shadow_synthetic.py`: 346 passed, 1 skipped, 0 failed. (The ruling's baseline of 345 passed / 2 skipped is for `/usr/bin/python3` on mal-fast-0; this run used the research-0 venv.) The tools tree of this PR is unchanged.

## 10. Binary diff (item 7, report-only) (s04)

- **pump** c849155c... -> bfd2a2bd...: same length; 474 of 2,560 4-KiB pages differ; printable-string diff: two changed runs only: a new string `buy_exact_sol_in` (an IDL instruction name; present in the docs IDL) and the added `subtract with overflow` panic text. 4 IDL instruction names have no bare occurrence in either binary (AddQuoteControlMint, Buy, SetQuoteControlMintReserves, UpdateBuybackConfig); the IDL error "Creator fee basis points must be between 1 and the configured maximum" is absent from both.
- **PumpSwap** e3d3be22... -> 78046072...: same length; 311 of 2,560 pages differ; three changed runs: new strings `buy_exact_quote_in` and `buy` (path strings of `instructions/swap/buy.rs` and the boost module; both are IDL instruction names) and a re-ordering of the `programs/pump-amm/src/lib.rs` path text; the standalone run `BondingCurveFeeConfigSharingConfig` is unchanged apart from its prefix. IDL instruction names without bare occurrence in old and new: ClaimCashback, UpdateBuybackConfig. No new instruction or event name appears on a buy, sell, create_pool or migrate path that is not in the unchanged docs IDL. Reading aid only, not a proof of equal behaviour.

## 11. FLOOR instruction shape (ruling (c1)), and the table of bars

Executor at commit `e5fd255b`: `tools/pumpswap_tx.py` (`DISC_BUY_EXACT_QUOTE_IN = bytes.fromhex("c62e1552b4d9e870")`, `DISC_SELL = bytes.fromhex("33e685a4017f83ad")`; header: buy_exact_quote_in 25 data bytes with 26 accounts, sell 24 bytes with 24 accounts; fixed addresses `GLOBAL_CONFIG` ADyA8hde..., `WSOL_MINT`, `SYSTEM_PROGRAM`, `ATA_PROGRAM`, `event_authority()`, `PUMPSWAP_PROGRAM`, `global_volume_accumulator()`, `fee_config()`, `FEE_PROGRAM`; PDAs `pool_v2(base_mint)`, `user_volume_accumulator(user)`, `creator_vault_authority(coin_creator)`). `tools/pumpswap_tx.py` was imported unchanged from `git show e5fd255b:tools/pumpswap_tx.py` (sha256 2c71d3c0...) for the derivations. Nothing was built, signed, simulated or sent.

Shapes that landed (WSOL-quoted, success, top-level or inner; `out/s18_instruction_shapes.json`):
- **buy_exact_quote_in** `c62e1552b4d9e870`: post 25-byte/26-account 375 and 24-byte/26-account 275 (plus 12 instructions of other shapes); pre 168 and 174. **The executor's exact shape (25 bytes, 26 accounts) is accepted post-deploy: 375 successful instructions** (pre 168).
- **sell** `33e685a4017f83ad`: post 24-byte/24-account **557** (pre 184) and 17 with 23 accounts (pre 8); **the executor's exact shape landed 557 times post.**
- Fixed-address positions equal the executor's derivation in every successful instruction, post and pre: buy 2, 4, 13, 14, 15, 16, 19, 21, 22 on 660 of 660 (pre 348 of 348); sell 2, 4, 13, 14, 15, 16, 19, 20 on 576 of 576 (pre 192 of 192). PDA positions with the event's coin_creator: `creator_vault_authority` 657 of 657 (buy) and 559 of 559 (sell); `user_volume_accumulator` 657 of 657; `pool_v2` 655 of 657 (buy) and 558 of 559 (sell) (pre 342 of 345 and 184 of 184); pool vault ATAs 657 of 657 and 559 of 559. The few misses are on instructions with other shapes (27-account or 25-account variants); no executor-shaped instruction missed on a fixed address.
- **Failed shares** (all WSOL-quoted txs in the cached blocks, `out/s20_failed_shape_txs.json`): buy_exact_quote_in post 618 failed of 1,278 (48.36%), pre 334 of 682 (48.97%); sell post 331 of 907 (36.49%), pre 115 of 307 (37.46%). Top words post: buy 6040 BuySlippageBelowMinBaseAmountOut 493, 6001 ZeroBaseAmount 99; sell 6004 ExceededSlippage 233, 6001 26. The post share is below pre on both: the 5-percentage-point trigger does not fire.
- **Owner programs by position (read now; accounts closed since are `(closed or absent now)`; `out/s18`):** both sides, 200 instructions per side post, 200 buy / 184 sell pre. Position 0 pool, 2 global_config: owner PumpSwap; 3 base mint: Token-2022 195 / Tokenkeg 5 (post sell); 4 quote mint: Tokenkeg; 7: Token-2022 or Tokenkeg like the mint, 8 Tokenkeg; 9 fee recipient: system (194) or a program-owned account (6); 17 vault ATA: Tokenkeg; 19 (sell) fee_config: fees program `pfeeUxB6...`; 22 (sell) buyback recipient: fees program; the program accounts (positions 11 to 16 where they are programs) are owned by their loaders as expected. Pre has the same owner sets per position (see JSON).

**(c2) keyless simulation (manager's job): placeholder.** Script `program-upgrade-1115/s16_keyless_sim.py` (sha256 fe2c7ba00dbacc9e127f3b9da6bb2932eaf4afd0dd3e269efd5507decfd00fa4, a byte copy of the manager's file at 2026-11-15T19:5xZ). Job id: ______; six `err` values: ______. (Not run by this builder; the review cannot pass (c) without 6 of 6 `err` null.)

**Stage 2 (A2, M2): not run, declared in section 3.** Stage 2's result belongs to a later T1 PR (ruling (a)).

### Bars, one line each

| Ruling bar | Result |
| --- | --- |
| 1 rule declared and committed before fetch | met (section 3, commit before the first `getBlock`) |
| 2 / (a) stage-1 sample A1 | met except CompleteEvent 0 |
| 3 M (>= 20 usable, >= 10 WSOL) | met: 40 usable, 29 WSOL |
| 4 P (>= 100 blocks, >= 5 hours, before earliest write) + M_pre (>= 10) | met: 120 blocks, 5 hours; M_pre 40 |
| 5 decode paths | met (frozen blobs equal EXP027; follower facts by manager) |
| 6 discriminators, field inequalities, non-decodes | met: 0 / 0 / all explained |
| (b) prefix parity and trailing bytes, all PumpSwap types | met |
| (b) 8-byte field | met: 0 non-zero |
| (b) fee triples post subset of pre | **NOT met** (5 new triples) |
| (b) reserve fields | met (0 inequalities) |
| (d) Token-2022 | met: no new extension |
| 9 (ii) migration economics | **NOT met** (post minima below pre minima) |
| 9 (iii) / (c3) pool accounts | met on owner, length, discriminator (24 of 24 equal 1114; 10 new sane) |
| 9 (iv) fee pins | met (fees program and three accounts equal pins) |
| (c1) executor shape accepted post-deploy | met: 375 and 557 successful, 0 fixed-address misses |
| (c2) keyless simulation | placeholder, manager's |
| 10 / (d) write enumeration | met: exactly 1 Upgrade per program, route (a) |
| 11 / (e) re-pin read | read made after the last sample; pins NOT changed |

## 12. Not checked

No tape, tip, ledger, book or P&L file. No Helius key, SSH or MiScusi job. The 07:10:06Z and 19:10:06Z monitor records themselves (brief facts only). First-print V for pools beyond the page budget. Owner programs of position accounts at transaction time (read now). The follower's own inner-migration path was not re-run. Stage 2 (A2, M2). No simulation (c2). No claim that any book makes money; no book has cleared the promotion gate.
