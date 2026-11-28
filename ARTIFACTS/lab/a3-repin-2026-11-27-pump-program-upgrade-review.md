# Review: pump program redeploy of 2026-11-27T22:20:22Z (slot 474715515), re-pin

Builder: Claude worker, 2026-11-28 (started `date -u` 08:4xZ), mal-research-0 worktree `/data2/mal/wt/a3-repin-1128`. Public RPC (api.mainnet-beta.solana.com) and unauthenticated GitHub only. 0 Helius credits, no key or env file. No tape, arena, book, ledger, shadow, declog, tip or FLOOR file opened; no price path, outcome or P&L computed. Scripts and small outputs: `ARTIFACTS/lab/program-upgrade-1128/` (`out/*.json`); raw responses are off-repo in `/data2/mal/scratch/a3-repin-1128/`. This is a reading of chain and repo facts. No transaction was built, signed, simulated or sent by this builder. No sentence here says any book makes money; no book has cleared the promotion gate.

Standard: `/data/mal/hunt-1008/ops/QP-RULING-1128-a3-repin-pump.md` did not exist when this review was started (checked after each stage). Until it exists the standard is `QP-RULING-1114-a3-repin-pump.md` plus `-ADD1.md` (the last pump-only re-pin) with the new slots, with the 1121 ruling's F-items not applying (fees unchanged). If the 1128 ruling arrives, it controls and the closing section says where it differs.

## 1. Chain facts read (1114 Q1 item 11)

Read from public RPC at slots 474891634 to 474891683 (2026-11-28 about 08:49Z; `out/s01_programdata.json`):

| Program | programdata | deploy_slot (pin) to chain | UTC | sha256 | len |
| --- | --- | --- | --- | --- | --- |
| pump `6EF8rrec...` | `B5MvUwXd...` | 471854454 to **474715515** | 2026-11-27T22:20:22Z | `46bf6c8ee4587208` to `c6dd30fdf0645a455d4c82fa8fd374f0b5b52f1348828dc93f110f526fca51a3` | 10485760 unchanged |
| PumpSwap `pAMMBay6...` | `6naEzKeU...` | 471854493, equals pin | (2026-11-20T19:28:12Z) | `92d73e65...`, equals pin | 10485760 |
| fees `pfeeUxB6...` | `75Uu23mq...` | 471854409, equals pin | (2026-11-20T19:27:54Z) | `519e166e...`, equals pin | 948989 |

PumpSwap and fees did NOT change: their programdata headers still carry the 2026-11-20 deploy slots (the header slot is the slot of the last write), their sha256 equal the pins, and the three pinned config accounts (`bonding_fee_config`, `pumpswap_fee_config`, `pumpswap_global_config`) equal their pinned sha256 and length. Per the brief the per-program PumpSwap and fees checks are skipped; PumpSwap is still sampled (stage 5 and the PumpSwap events inside pump-invoked transactions).

Pins diff (`s02`, monitor `--write-pins` to scratch, slot 474891816, 2026-11-28T08:50:21Z; re-run for stage 7 after the last sample): exactly `programs.pump.deploy_slot`, `deploy_utc`, `sha256` and `pinned_utc`, `pinned_at_slot`; `data_len` unchanged; 0 unexpected keys (`out/s02_pins_diff.json`).

## 2. Docs and IDL (`s03`, `out/s03_docs_idl.json`)

pump-public-docs `main` is `e244b35ba70864c029411e81ed1bbd4dee4d2926` (2026-11-27T23:20:10Z), "docs: block 0 tax on the bonding curve (rate 0 until further notice), tax_exempt_creator, pump IDL refreshed". Two commits ahead of the pinned `639b87fb`: `bc35796` (2026-11-20T21:34:02Z, fee recipient rotation, README and docs only) and `e244b35`. sha256 of the IDLs against `tools/a3_redeploy_pins.json`:

| IDL | pinned | main e244b35 | equal to pin |
| --- | --- | --- | --- |
| pump | `7616681e...` | `a6c28543705f154d55215f6b12ecebdd1208bde6b68ea3b0dd30021b522a09c7` | **NO, changed** |
| pump_amm | `b7d8c57a...` | same | yes |
| pump_fees | `6b9fa3ec...` | same | yes |

Pump IDL diff, old pinned (639b87f) vs new (e244b35), by name (structural, `s03` hashes each definition):

- **Instructions:** 1 added (`set_block0_tax_bps`, arg `block0_tax_bps: u64`, accounts global, authority, event_authority, program); 2 changed (`create_v2`: arg and account lists equal, definition differs only in docs/metadata of the item as hashed; `update_creator_fee_config`: new trailing arg `min_configurable_creator_fee_bps: u64`); 0 removed. Discriminators of the changed ones equal old.
- **Accounts:** 0 added, 0 removed, 0 discriminator changes. Account type layouts that changed (append only): `BondingCurve` (+`created_at_slot: u64`, +`tax_exempt_creator: pubkey`), `Global` (+`min_configurable_creator_fee_bps: u64`, +`block0_tax_bps: u64`).
- **Events:** 0 added, 0 removed, 0 discriminator changes. Layouts that changed, all by APPENDING fields (old field list is an exact prefix of the new): `TradeEvent` 35 to 37 fields (+`quote_transfer_fee: u64`, +`block0_fee: u64`, 16 bytes), `PostCompleteBuyEvent` 16 to 17 (+`block0_fee: u64`), `UpdateCreatorFeeConfigEvent` 4 to 5 (+`min_configurable_creator_fee_bps: u64`).
- **Events NOT changed:** `CreateEvent`, `CompleteEvent`, `CompletePumpAmmMigrationEvent` and every other pump event (layout hash equal). `migrate`, `create`, `create_v2`, `complete` instruction argument lists and account lists are equal to old. PumpSwap (pump_amm) and fees IDLs are byte-equal.
- **Errors:** 4 added (6110 `QuoteMintNotWritable`, 6111 `CreatorFeeRangeInvalid`, 6112 `Block0TaxBpsOutOfRange`, 6113 `MultiHopBlock0TaxLegRequired`), 1 message changed (6078 `CreatorFeeBpsOutOfRange`).

So, from the docs alone: migrate / graduation (PumpSwap pool creation) did not change in the IDL; the CreateEvent and CompleteEvent layouts did not change; the TradeEvent layout grew by 16 trailing bytes. FLOOR's host decodes `TradeEvent` (bonding trades) and reads the prefix; whether the 8-byte trailing block pinned in `a3_redeploy_pins.json` (TradeEvent trailing 8) is now 16 or 24 is measured on chain in section 6, not assumed. The docs say the tax rate is 0 until further notice; what the chain shows is measured below.

## 8. Write enumeration (1114 Q1 item 10, route (a))

Route (a): `s07_upgrade_history.py` on public RPC, 0 credits (`out/s07_upgrade_history.json`). Range from 2026-11-27T19:00:00Z (the A3 record of 2026-11-27T19:10:05Z applied none, a manager's fact, so no write precedes it) through now; paged until a signature older than the start: COMPLETE true.

- **pump programdata `B5MvUwXd...`:** 1 signature in the window, successful. **Exactly one Upgrade:** slot 474715515, 2026-11-27T22:20:22Z, signature `5PJrdWV24HTnd91JFLGCiTvxQRQgs8mycTkyVi7hzgvoK8GsJH6px97DYriGp3FiDcjA7eHWNe1Ka7hWBEsgqquB`, a BPF-loader Upgrade (tag 3) as an inner instruction (programs 11111111, BPFLoade, ComputeB, SQDS4ep6: Squads CPI).
- No other write in the window. So the pre interval is (471854454, 474715515) and the post interval is (474715515, now]. P ends at least 150 slots before 474715515 and A starts exactly 150 slots after, so each sample lies inside one interval.
- PumpSwap and fees: not enumerated (their header slots equal the 2026-11-20 pins, which proves no later write).

## 3. Sample rule, DECLARED BEFORE ANY SAMPLE FETCH (1114 Q1 item 1)

Declared at the commit that adds this section. At this commit only these were read from the chain: the programdata and config accounts (s01), the monitor's `--write-pins` read to scratch (s02), docs and IDL (s03), and the write enumeration of the pump programdata (s07). No block of any sample had been fetched. Structure only: counts, discriminators, layout lengths, field equality of already-decoded fields.

Constants: `D` = `E` = 474715515 (2026-11-27T22:20:22Z), the only write.

- **CHAIN-ONLY.** No tape, arena, tip, `/var/lib/mal*`, book, ledger, shadow, declog or FLOOR file is opened. The manager's hourly counts (after 22:20:22Z: FLOOR buys 4 landed 0 failed, sells 4 landed 0 failed) are quoted as the manager's counts, not as data read here.
- **Sample A (post), single stage:** 24 anchors evenly spaced (integer `lo + (hi-lo)*i//23`) between `D`+150 and (latest finalized slot at run time) minus 1000; at each anchor the first 10 non-skipped slots at or after the anchor (`getBlock`, finalized, full, version 1). Every successful tx touching pump, PumpSwap or fees is in the sample. Bars: >= 240 blocks from `D`+150, >= 10 distinct UTC hours (the run starts about 10.5 h after the deploy, so 22Z to 08Z is 11 hours; if fewer than 10 hours result, a second stage is fetched later, not a pass), >= 5,000 successful txs touching pump or PumpSwap, WSOL-only PumpSwap Buy+Sell events >= 1,000, CreateEvent >= 10, `CompleteEvent` >= 1 else the supplementary sample, every PumpSwap event type seen in P appears in A or M.
- **Sample P (pre control):** 12 anchors x 10 non-skipped blocks evenly spaced between `E`-60000 and `E`-600 (`SAMPLE=pre`). Every block ends at least 150 slots before `E`. Bar: >= 100 blocks over >= 5 distinct UTC hours (about 6.7 h). P is partly before the enumeration start 19:00Z; for those hours the "no write" evidence is the A3 record of 19:10:05Z (second-hand) plus the unchanged binary layout P shows.
- **M (post migrate sample, `SAMPLE=m`):** candidates are the successful signatures of `mon.MIGRATION_FEE_ACCOUNT` paged back from now (MIGRATE_K rule, up to 40 pages); M = the first 40 settled (blockTime <= now - 480 s) candidates with slot >= `D`+150, oldest first, fetched whole, kept when `parse_migrate_tx` reads a CompletePumpAmmMigrationEvent. Bars: >= 20 usable, >= 10 WSOL-quoted. **M_pre:** the last 40 with slot <= `E`-150, same keep rule, bar >= 10.
- **Frozen decoder:** the three EXP027_DECODER_REF blobs (`observe/trade_decode.py` 238942a6, `observe/trade_store.py` ea4e11ed, `tools/pump_history_backfill.py` 9a8bebb3) via `rows_from_block(event_v=True)` and `records_from_logs(event_v=True)`. **Independent decode:** `s06_parity.py`, a borsh walker driven by the docs IDLs (no `observe/` code). The walker reads the IDL of the docs main head. Because the pump IDL changed, the walker is run against BOTH the new IDL (e244b35) and the old pinned IDL (639b87f) for the pump program in the trade-event paths; the pre sample is read against the old IDL, the post sample against the new one, and the number of trailing bytes of every event is reported either way.
- **Non-decode** is any of N1-N6 as defined in `a3-repin-2026-10-21-program-upgrade-review.md` section 3. Pass bar: 0 unexplained.
- **Pump events (C3'):** (a) prefix parity pre and post on every pump event and on PumpSwap events inside pump-invoked txs; (b) trailing bytes per event against `tools/a3_redeploy_pins.json` `trailing_bytes` exactly (TradeEvent, BuyEvent, SellEvent, WithdrawEvent 8, others 0); a TradeEvent trailing count different from 8 is reported as a finding and NOT a silent pass; (c) the 8-byte field by universe split; (d) Token-2022 owner and extension report for the base mints of M and M_pre.
- **Migration economics (ADD1 D replacement bar, 1121 D2 form):** medians of `sol_amount`, `mint_amount`, `initial_liquidity` equal pre's to the lamport; `pool_migration_fee` constant and equal pre's; every post value inside the range of the pre union (M_pre of this review plus the cached M_pre and M of the 1114, 1115 and 1121 reviews if present on disk).
- **CompleteEvent supplementary sample (ADD1 C), if A holds fewer than 5:** `s22_complete_events.py`, keep post when slot >= 474715665, pre when slot <= 474715365; K = 8 post, 5 pre; bars 0 field inequalities, blob 144 and trailing 0, 0 unknown discriminators.
- **Instruction shape (stage 5, REQUIRED):** from A, M and P, every successful WSOL PumpSwap `buy`, `buy_exact_quote_in`, `sell` instruction (top-level or inner): 8-byte discriminator, data length, account count, owner program per account position; compared against the executor's shape (`tools/pumpswap_tx.py`, blob read at `e5fd255b` and the installed commit as the 1121 review did). At least 20 successful WSOL buy_exact_quote_in and 20 sell post. Failed txs with these discriminators are counted by error word post vs pre (failure share more than 5 points above pre goes to QP). Graduation-created pools after the deploy must trade with the same account shape.
- **(c3) pool accounts:** 10 pools created after `D` (from CreatePoolEvents of A/M) read with `getMultipleAccounts`: owner, data length, 8-byte discriminator and `pumpswap_tx` pool-state offsets, against the 24 pool accounts of the 1114 review where cached.
- **Keyless sim:** the script and exact commands only (`keyless-sim/`); NOT run, no MiScusi job submitted; the manager runs it if QP requires it.
- **Re-pin (stage 7):** `tools/pump_structure_pins.json` `programs.pump` only, through the monitor's own `--write-pins` path at a slot after the last sample, plus the fixture `tools/fixtures/pump_structure_monitor/programdata.json` and `tools/test_pump_structure_monitor.py` deploy-slot list, only if every bar is clean; otherwise the PR is a draft and the pins stay.
