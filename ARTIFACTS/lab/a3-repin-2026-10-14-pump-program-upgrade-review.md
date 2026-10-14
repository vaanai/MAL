# Review: pump.fun PUMP program redeploy, 2026-10-13T15:40:41Z

Reviewer: Claude subagent (worker). Round 0 (head `e3af678`): 2026-10-14T07:21Z to 07:35Z. Round 1 (after the reviewer's comment 6163359908 and quant-proof's comment 6163455594, both at `e3af678`): review text and saved outputs only, offline. The review is a reading of chain and repo facts. It is not a decision.

Sources:
- **Public RPC, round 0 only** (`api.mainnet-beta.solana.com`, 0 credits): 143 attempts, 138 answered 200 and 5 answered 429 and were retried; first 2026-10-14T07:21:43.637Z, last **07:26:50.564Z**. **Round 1 made no Solana RPC call**; the log still has the same 143 attempts. The per-attempt log is `/data/mal/program-upgrade-1014/raw/rpc_calls.log`, copied to `out/rpc_calls.log` with a summary in `out/s13_rpc_log_summary.json` (by script: 8 + 1 + 5 + 22 + 107). `out/s05_graduations.json` records `attempts` 6 only because it was written by a cache-served rerun; the 107 for step 5 are in the log.
- **GitHub, unauthenticated** (`pump-fun/pump-public-docs`). Round 0: 10 requests, 8 by hand with curl and 2 in `s03_program_global.py` (the two `idl/pump.json` copies, kept off-repo); the 8 hand-fetched responses were **[not saved]** at the time. Round 1: `s10_docs_commit.py` fetched the same URLs again (8 requests: the commit JSON, the `.diff`, and `idl/pump.json`, `pump_fees.json`, `pump_amm.json` at 2293f9a and 639b87f) and saved them under `out/docs/` (the `.diff`, a slim commit JSON, the six IDL files gzipped) with a sha256 manifest `out/s10_docs_manifest.json`. The sha256 of the six IDL re-fetches equals the round-0 hand-fetched values. The commit JSON from the API differs in bytes from the round-0 fetch (sha256 differs); its stats (529 total, +456 / -73), 11 files and date are the same. The round-0 `.diff` has no sha256 to compare.
- The repo (read-only), `/data/mal/structure-monitor/daily.jsonl` (the A3 records), and, for the pre-deploy comparison in section 6, the response cache of the 10-13 fees review (public-chain `getTransaction` responses, read offline).

Seal and data, exactly:
- No canary ledger, fill, exit or P&L row, shadow record, paper twin, forward-paper, walk or runner file was opened. No Helius key or key file was read. `boost_slice_watch` output was not read.
- **Public chain data of October was read.** `s04_blocks.py` decoded 18 blocks with the walker (slots 456494692-697 and 456494700-705, about 2026-10-13T15:40:40Z to 15:40:42Z, and slots 456753000-005, about 2026-10-14T07:20Z). `s09_pool_v.py` read the current V (the pool's virtual quote reserve) of the 60 pools that graduated on 2026-10-14 (block times 05:51:58Z to 07:24:08Z). Round 1's `s11_migration_amounts.py` read the migration deposit amounts of those 60 migrations and of 84 earlier ones (slots 456126828-456354425, 2026-10-12 and 10-13) from cache. All of these hours are inside EXP-012's FINAL window `[2026-10-06T00, 2026-10-16T00)` (EXP-022 section 9).
- **What was kept:** counts, and V and the migration deposit amounts (pool-opening parameters). No trade price, trade size, path or P&L was printed or saved. Quant-proof's reading is that this is not a breach of the seal texts; it is disclosed because it happened.

Scripts and small outputs: `ARTIFACTS/lab/program-upgrade-1014/` (`out/*.json`, `out/*.txt`, `out/docs/`). Raw responses are cached off-repo in `/data/mal/program-upgrade-1014/raw/`.

## Verdict

**No layout change was found in the pump IDL or in the samples decoded (18 blocks, 3 creates, 60 migrations). The binary is not diffed, and 3 + 3 PumpSwap Buy/Sell events in the recent block sample were not decoded, cause not examined.** The documented change is depth-2 pump-coin quotes, switched on by `Global.max_curve_depth` 1 to 2 (the byte change is measured; the meaning is docs text). That depth-2 coins are never WSOL-quoted is docs text, **not measured**: 0 of 60 graduates are depth 2.

**CAP-PICK, stated narrowly (section 6).**
- In the **`walk2` adapter, which is the read**, a graduation whose migration-event quote mint is not WSOL is excluded by code (`tools/cap_pick_score.py:1232`), and the gate replay drops non-WSOL PumpSwap prints before any decision (`tools/forward_paper.py:803`).
- The **exploration adapter (E0 only) proxies WSOL by V band**, not by the event's quote mint (EXP-022 Amendment 3 item 4 row 2: "A non-WSOL pool with V in the band would be an attempt").
- **No output of this review runs either filter on a non-WSOL graduation.** The filter's only run in the repo is a fixture test (`tools/test_cap_pick_score_exp022.py:278`, quote mint `USDCmint`; the file gives 90 passed in round 1).
- The pool-opening amounts that the pump program sets at migration, which the guard seed uses, were read for 56 post-deploy and 78 pre-deploy WSOL graduations: the median and the mode are the same before and after; the share of WSOL openings whose base deposit equals `SEED_B` is lower after (41 of 56 against 69 of 78; quant-proof's one-sided Fisher p = 0.021, unplanned, mayhem and synthetic not split, samples from different hours; cause not established); the seed constants are **not equal** to the modal amounts (`SEED_Q` is 95 lamports above the modal opening quote). Section 6.

What is established, not established and not known:
- **Established:** pump program redeployed at slot 456494698 (2026-10-13T15:40:41Z), sha256 `ba1f91ac…6671`; PumpSwap and fees were not redeployed; the pump IDL has no change to any instruction, account, event or type (docs text on `create_v2`, four error messages, one new error 6109; `out/s00_idldiff_pump.txt`); `Global`'s first 1,087 bytes are the value the 10-09 and 10-13 reviews give, and only its last byte (`max_curve_depth`) is different, 1 to 2.
- **Not established:** what else the new binary does. The old pump binary (sha `8c8f6aae…`) is not on disk, so the two binaries are **not diffed**. The binary is consistent with the new IDL text (section 1); that is not a comparison with the old one.
- **Not known:** how many depth-2 coins exist; when `Global` was changed (between the A3 runs of 2026-10-13T07:11:58Z and 2026-10-14T07:11:10Z); the V0 of any post-deploy pool.
- **Re-pin effect, checked offline on the halted record:** with the old pins the 07:11:10Z record gives `pins_check.changed` = the pump deploy slot and `program_changed` on pump; with the new pins `changed` = [] and all three programdata hashes are `ok` (`s08_pins_offline.py`, `out/s08_pins_offline.json`). That is a re-evaluation of the record, not an A3 run and not EXP-022's P2 run.

## 1. What changed

| Item | Before | Now |
| --- | --- | --- |
| pump `6EF8rre…` programdata `B5MvUwXd…`: deploy slot / UTC | 454596459 / 2026-10-08T16:20:17Z | **456494698 / 2026-10-13T15:40:41Z** |
| pump programdata sha256 (whole account) | `8c8f6aae2324281b2199c4314e6a041b707ef0cb9bcb132c677b7bb6289d8772` | **`ba1f91ac91dd8c578fd069230601f090775b88acbeecd680bde10d90e3606671`** |
| pump programdata ELF-only sha256 (bytes 45 on) | not recorded | `a8bc61552f2854e327189f623ae63e8855c3b673f9587993448f5fd167182cf1` |
| pump programdata length | 10,485,760 | 10,485,760 (a fixed-size buffer; equal length says nothing) |
| PumpSwap `pAMMBay…` deploy slot | 454596406 | 454596406 (not redeployed; sha `868a875e…` equals its pin) |
| fees `pfeeUxB6…` deploy slot | 456132171 | 456132171 (not redeployed; sha `3cf9ee9a…` equals its pin) |
| Upgrade authority on all three | `7gZufwwAo17y5kg8FMyJy2phgpvv9RSdzWtdXiWHjFr8` | unchanged on all three |

(Source: `out/s03_program_global.json`, `out/s02_fixture.json`, `out/s01_pins_diff.json`.)

- **Deploy tx** `2fAUGTBfLUViHGAq…` (slot 456494698, block time 1791906041 = 2026-10-13T15:40:41Z, no error): top level ComputeBudget x2, System, Squads `SQDS4ep65T869zMMBKyuUq6aD6EgTu8psMjkvj52pCf`; inner BPFLoaderUpgradeable; log "Upgraded program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"; the upgrade authority is among the tx accounts. **Signer `5sFJMw5dC59oKGKA9LgeYQCSmYS6rLeCegxMkcM2Pwby`**, the same executing key the 10-13 review gives for the fees redeploy of 2026-10-12T17:40:24Z, and not the `6W6qsDbo…` that the 10-09 review gives for 10-08 and 10-02 (both taken from those reviews). Squads membership of that key was not looked up.
- **Signatures on the programdata account** (`getSignaturesForAddress`, 5 newest; `out/s03_program_global.json`): slots 456494698, 454596459 (10-08), 452654932 (10-02), 452417959 and 452372213 (both 2026-10-01 per the audit's `s15_upgrade_history.txt`; whether those two are upgrades was not checked). The deploy slots of record in the A3 pins are 452654932 (10-02), 454596459 (10-08) and now 456494698 (10-13).
- **Docs** [639b87f](https://github.com/pump-fun/pump-public-docs/commit/639b87fb45c3adeffa6f7c4a1bf0cb85e392bf22), 2026-10-13T15:37:48Z, 2 min 53 s before the deploy tx: "docs: depth 2 pump coin quotes (create_v2 quote chain accounts), SDKs to pump-sdk 4.1.0 and pump-rust-client 0.4.1, pump and fee IDLs refreshed". **11 files, +456 / -73** (`out/s10_docs_manifest.json` and `out/docs/639b87f.commit.slim.json`: stats total 529, additions 456, deletions 73, 11 files). Everything said below about the docs text is in `out/docs/639b87f.diff`; the lines quoted are saved in `out/s10_docs_quotes.txt`.
  - `README.md` and `docs/instructions/CREATE_WITH_PUMP_COIN_QUOTE.md`: "Starting **October 13**, `create_v2` can pair a new coin with a pump coin that is itself paired with a pump coin (SOL → A → B → C)". "A coin paired with a pump coin has the quote coin's depth plus one." "**Depth 2 is enabled on October 13**" through `Global.max_curve_depth`; the program cap is 5 (the `MAX_CURVE_DEPTH` line of the same file and the error 6105 text). The new coin's starting `virtual_quote_reserves` is computed from the live reserves of every coin on the chain (in the `.diff`, not a saved quote line). "Q must not be a mayhem-mode coin" and "The new coin cannot be in mayhem mode" are unchanged context lines of the diff.
  - SDK bumps only in `SYNTHETIC_MIGRATION.md`, `MULTI_HOP_SWAP.md`, `PUMP_SWAP_TRADE_V2.md`, `SWEEP_FEES.md`, `TRADE_V3.md` (pump-sdk 4.0.0 to 4.1.0, pump-rust-client 0.4.0 to 0.4.1; pump-swap-sdk stays 2.1.0).
  - `idl/pump.json` (`out/s00_idldiff_pump.txt`): instructions 56 / 56, accounts 7 / 7, events 32 / 32, types 47 / 47, none added or removed, **none changed except docs text on `create_v2`**. Errors 109 to 110: new 6109 `QuoteChainAccountsRequired`; the messages of 6100, 6101, 6105 and 6107 changed. `Global` is 1,088 bytes and `BondingCurve` 166 bytes in both copies (`out/s00_offsets_pump.txt`).
  - `idl/pump_fees.json` (`out/s00_idldiff_fees.txt`): new instruction `withdraw` (30 to 31 instructions), `WithdrawEvent`, errors 6035 and 6036. This describes the **fees** program, which was redeployed on 10-12, not on 10-13.
  - `idl/pump_amm.json`: byte-identical at both heads (sha256 `b7d8c57a4d9c4dd0109a9ab893052352333252d4eedced10ac091d4d66cab89b`, `out/s10_docs_manifest.json`; `out/s00_idldiff_amm.txt`).
- **Binary against the docs** (`s03_program_global.py`, substring test, and `s12_binary_names.py`, boundary-aware; no comparison with the old binary):
  - all 56 IDL instruction names are present as `Instruction: <Name>`. The ELF stores these Rust string pieces back to back, so a name is "confirmed" when the next bytes are a non-identifier byte or the next piece `Instruction: `: **52 of 56 are confirmed** (including 7 of the 8 names that are prefixes of others: Sell, Create, Migrate, Initialize, ClaimCashback, CollectCreatorFee, DistributeCreatorFees). **4 are not confirmable at the byte level** because another string follows directly: `Buy` (followed by `CreateV2Disabled…`), `AddQuoteControlMint`, `SetQuoteControlMintReserves`, `UpdateBuybackConfig` (each followed by an error-name string). The reviewer's check (comment 6163359908 N1) reports a bare occurrence for each of the 8 prefix names; this script does not reproduce that for `Buy`;
  - all 110 IDL error messages (new text) are present (`out/s12_binary_names.json`);
  - of the five messages the docs commit changed or added, the **new** text is present once each for 6100, 6101, 6105, 6107 and 6109; the **old** text is absent for 6100, 6101 and 6107; for 6105 the old text is present once, and it is the same occurrence as the new text (the old text is a strict prefix of the new one; 0 old occurrences are outside the new text);
  - `SetMaxCurveDepth` and `MultiHopCurveSwap` are present once each (both first seen in the 10-08 binary per the 10-09 review).
- **Previous binary:** not on disk. **What the new pump binary does beyond the docs text is not established.**
- **No X or blog announcement** was searched for in this review (not checked).

## 2. Layouts the lab uses

- **Executor.** It trades PumpSwap `buy`, `sell` and `buy_exact_quote_in` (`tools/pumpswap_tx.py`). PumpSwap was not redeployed and its IDL is byte-identical, so nothing about the executor's lists is re-tested here. The executor does not call the pump program's bonding instructions.
- **Decoder, walker and monitor, run on blocks either side of the deploy** (`s04_blocks.py`, `getBlock` full, 6 blocks each, public RPC, `tools/pump_history_backfill.rows_from_block(event_v=True)`). Blocks: **pre** 456494692-697, **post** 456494700-705 (deploy+2 on, since a program deployed in slot S is visible from S+1), **recent** 456753000-005 (about 07:20Z). Small samples. (`out/s04_blocks.json`, `out/s07_bad_events.json`.)

  | | pre | post | recent |
  | --- | --- | --- | --- |
  | successful txs touching the pump program | 83 | 97 | 71 |
  | pump TradeEvent blobs / walker bonding trade rows | 63 / 63 | 65 / 65 | 40 / 40 |
  | TradeEvent lengths (bytes) | 382, 383, 395, 397, 417, 431 | 382, 383, 395, 397, 417, 431 | 382, 383, 395, 397 |
  | CreateEvent blobs / decoded by `decode_create_event` | 1 / 1 (354 B) | 1 / 1 (349 B) | 1 / 1 (347 B) |
  | creates by quote (walker) | 1 WSOL | 1 WSOL | 1 WSOL |
  | `bad_disc`, pump `TradeEvent` disc (rejected by the sanity window, or no event-V tail) | 4 of 67 | 1 of 66 | 0 of 40 |
  | `bad_disc`, PumpSwap `BuyEvent` / `SellEvent` | 1 of 173 / 1 of 237 | 0 of 164 / 0 of 181 | 3 of 129 / 3 of 137 |

  - The pump instruction names seen are the same families in all three groups: `Buy`, `Sell`, `BuyV2/V3`, `SellV2/V3`, `BuyExactQuoteInV2/V3`, `BuyExactSolIn`, `CreateV2`, `DistributeCreatorFees(V2)`, `MigrateBondingCurveCreator`/`ExtendAccount`/`Sweep*` in places. No `MultiHopCurveSwap` appeared in 18 blocks. The bad-event rows count log lines (`s07_bad_events.py`); the blob row above counts de-duplicated blobs (`s04_blocks.py`), so 63 / 65 / 40 and 67 / 66 / 40 differ by duplicates.
  - The bad TradeEvents are 133 or 147 bytes (`s07_bad_events.py`), not the 382-431 byte events; they are in pre (4) and post (1), not in recent. The bad PumpSwap Buy and Sell events are 489 and 441 bytes (the normal lengths) and `decode_program_data` returns None on them; 1 and 1 in pre, 0 and 0 in post, 3 and 3 in recent. **The cause was not examined.** PumpSwap was not redeployed and the pump program does not emit PumpSwap events. With 6 blocks each the difference is a count of 6 against 2 against 0. If any of them is on a non-mayhem WSOL pool, the layout statement above would not hold for PumpSwap events; that was not checked.
- **BondingCurve size** (`s04_blocks.py`, `s05_graduations.py`): `space` 166 bytes on the one curve created in each of pre, post and recent (n = 1 each), and on **60 of 60** graduates' curves (created at unknown times). The IDL says 8 + 158 = 166 in both IDL copies (`out/s00_offsets_pump.txt`). So the size did not change **where it could be read**; a curve created right before the deploy and one created right after are 166 bytes each (n = 1 each).
- **Global** (`s03_program_global.py`, account `4wTV1Y…`, ctx slot 456754038): 1,088 bytes, same as before. **Sha of the first 1,087 bytes `6afa5f43e71cf99644f62568a48666fe970f8396a4c4d6bd144e14bcc522cc6f`**, which is the value the 10-09 review (`6afa5f43…`) and the 10-13 review (`6afa5f43e71cf996…522cc6f`) give (taken from those files, not re-derived). The last byte (`max_curve_depth`, offset 1087) is **2**; the 10-13 review reads **1** (ctx slots 456356703-716; its value, not re-measured). Full-account sha: `32729edb…` in the A3 records of 10-09 to 10-13, `dd2b9fa23e2e80d35a0f33329db5c7deb3e88d5ed73bceb1ffa397225f437f8b` on 10-14. So **the only change found is `max_curve_depth` 1 to 2**. Fields read (unchanged by the sha): fee_basis_points 95, initial_virtual_token_reserves 1,073,000,000,000,000, initial_virtual_sol_reserves 30,000,000,000, initial_real_token_reserves 793,100,000,000,000, create_v2_enabled 1, mayhem_mode_enabled 1, is_holder_reward_enabled 1, whitelisted quote mint list = USDC `EPjFWdd5…`. `Global` is **not a pinned account** in `tools/pump_structure_pins.json` (the monitor records it with `pinned: false`), so its change raised no flag by itself; the program redeploy did.
- **Migrate path.** `Migrate` and `MigrateV2` both still run after the deploy (`out/s05_graduations.json`, `out/s06_records.json`):

  | sample | `MigrateV2` | `Migrate` |
  | --- | --- | --- |
  | A3 run 2026-10-14T07:11:10Z (20 graduations, all after the deploy) | 17 | 3 |
  | A3 run 2026-10-13T07:11:58Z (before the deploy) | 19 | 1 |
  | A3 runs 10-08 to 10-12 (five runs, 20 each) | 17, 17, 17, 20, 15 | 3, 3, 3, 0, 5 |
  | 60 newest migrate txs on 2026-10-14 (07:26Z, `s05_graduations.py`) | 55 | 5 |

  `CompletePumpAmmMigrationEvent` decoded on 60 of 60 (the sampler keeps only txs that carry it; the pool, mint, curve and quote mint fields come from the offsets in `tools/pump_structure_monitor.py:445-458`). On all 60, the Pool account's `quote_mint` (PumpSwap `Pool` byte 75) equals the migration event's quote mint. The pool account is 301 bytes on 59 and **287 bytes on 1** (a depth-1 pump-coin-quoted graduation, slot 456751899); the docs-head pump_amm IDL totals 287 (`out/s00_offsets_amm.txt`). Why one pool is 14 bytes shorter was not examined.

## 3. Fees

- **Config accounts unchanged.** The A3 record of 2026-10-14T07:11:10Z (ctx slot 456750646) and the pin writer (`--write-pins`, slot 456753561) read all three equal to their pins:

  | account | sha256 | len |
  | --- | --- | --- |
  | bonding FeeConfig `8Wf5Ti…` | `3504fae20640e15bd441257ab118aa96fb4e99190defc06a01aae26b307bf204` | 4097 |
  | PumpSwap FeeConfig `5PHirr…` | `d8706882a217e82af619b754e37beedf9338a25787ebec717e7d69d2cf347c9d` | 4097 |
  | PumpSwap GlobalConfig `ADyA8h…` | `a698009a419d0b8c56ff257626d15cba305c4989a602fa86e65ca252b2c200f4` | 949 |

- **pump `Global`**: fee_basis_points 95 and the curve parameters are in the first 1,087 bytes, whose sha is unchanged (section 2).
- **Not tested:** the redeployed pump program's own fee computation. The docs commit says nothing about fees on the bonding curve. The 10-13 fees review's item-6 dry run covered PumpSwap (via the fees program) only, and nothing like it was run for this deploy.

## 4. BOOST

The 2026-10-14T07:11:10Z A3 run (job #996) is the first run whose sample is entirely after the pump deploy (graduations in slots 456737816-456747832; deploy slot 456494698). It is the monitor's own record; nothing was re-measured. Medians across the A3 runs, in order (counts from `s06_records.py`, `out/s06_records.json`):

| Run (UTC) | slots after pump deploy | InitBoost / non-mayhem WSOL | slices median (signature-list count) | budget SOL median | last slice s, median [min-max] |
| --- | --- | --- | --- | --- | --- |
| 10-08 10:25 | no | 12 / 12 | 29.5 | 17.586 | 341.5 [329-354] |
| 10-09 07:11 | no | 16 / 16 | 29 | 17.586 | 335 [324-365] |
| 10-09 19:23 | no | 15 / 15 | 30 | 17.586 | 342 [332-357] |
| 10-10 07:11 | no | 9 / 9 | 30 | 17.586 | 333 [329-353] |
| 10-11 07:11 | no | 18 / 18 | 30 | 17.586 | 339.5 [324-358] |
| 10-12 07:57 | no | 8 / 8 | 29.5 | 17.586 | 341.5 [329-354] |
| 10-13 07:11 | no | 17 / 17 | 29 | 17.586 | 332 [328-352] |
| **10-14 07:11** | **yes** | **13 / 13** | **29 [28-32]** | **17.586 (min = max)** | **340 [328-356]**, 13 of 13 keeper-verified |

- The 10-14 run's first slice is 0-7 s after the migrate (median 2). The budget is 17.586 SOL on all 13 pools. The halt flags `boost_disabled`, `boost_share_low`, `boost_last_slice_early`, `boost_budget_or_slices_changed` and `synthetic_share_high` (3/17 = 0.176) did not fire; the only halt is `pins_changed`.
- **Not measured here:** exact keeper slice counts, per-pool gaps of 60 s or more, and the share of pools under 29 slices (the owner's two daily trackers). The slice-watch job's output was not read.
- **n = 13, which is below the 15 that Amendment 7 item 2 needs for item 4.** At n < 15 the owner's median line does not apply, and the per-pool range 28-32 is outside 29-30. These fields therefore show no change attributable to the deploy in a sample that is too small to be the item-4 sample; they do not show that BOOST is unchanged.

## 5. H5's inputs

- **Q = quote + V, s0 = first PumpSwap print, the PostCompleteBuy class.** PumpSwap and its IDL are unchanged and its events are not emitted by the pump program. The pump program does run the migration (`Migrate`, `MigrateV2`), which creates the PumpSwap pool and sets its initial V. What was read:
  - Pool V, now (`s09_pool_v.py`, `out/s09_pool_v.json`; **current V, not V0**: a pool that kept v2 fees has V below its opening value), of the 60 newest graduates' pools. WSOL-quoted: 56 (38 non-mayhem). Of the 38 non-mayhem pools, **11 have V in 17,584,505,288 to 17,584,506,405 lamports, the range of 13 fresh post-fees-deploy pools at s0 in the 10-13 review; 0 are above that range**; 16 more are lower but inside [17.5e9, 17.7e9] (27 of 38 in the band); 11 are below 17.5e9 now (11.56e9 to 17.48e9). Maximum 17,584,505,289. The 18 mayhem WSOL pools have V of zero or less. The pool's **opening** V was not read for any of them.
  - Synthetic (PostCompleteBuyEvent in the curve-completing tx): A3 record 10-14 3 of 17 checked (3 completions not found), 10-13 3 of 20, 10-12 6 of 19, 10-11 7 of 19, 10-10 4 of 18, 10-09 19:23 6 of 17.
- **Not measured:** whether s0 lags the migrate on synthetic pools; Q = quote + V against the vault's pre-trade balance on post-deploy events; the post-deploy first-print V of any pool.

## 6. CAP-PICK (EXP-022) inputs

What the book reads (`EXP/EXP-022-cap-pick-part1-prereg.md` sections 1-4, 9-11, Amendments 3 and 5) and whether this change touches it. "Touches" means a code or chain fact that moved, not an effect on any result.

| Input | Where | This change |
| --- | --- | --- |
| **Pools and quote traded.** Canonical PumpSwap pool = the `pool` of the mint's `migration` event; **quote mint WSOL** from that event; non-mayhem from the create event; V0 in [17.5e9, 17.7e9] lamports; s0 within 80 s of the complete event (section 3) | `tools/cap_pick_score.py:1232` (`source == "walk2"` branch: `e.migration_quote_mint != WSOL_MINT` is excluded as `quote_not_wsol`, `:1121`); the event's quote mint is taken at `:1325` and decoded at `tools/pump_history_backfill.py:315`; `_quote_or_wsol` (`:319`) maps only the native-SOL mint to WSOL and passes every other mint through | **For the walk2 adapter (the read) a non-WSOL graduation is dropped, by code.** The gate replay drops a print with `quote_is_wsol` false (`tools/forward_paper.py:803`); PumpSwap rows get `quote_is_wsol` from the pool's CreatePool quote (`observe/trade_decode.py:589`); the migrate trigger is the first kept PumpSwap print (`tools/cap_pick_gate_replay.py:566`). So a non-WSOL graduate's own pool does not trigger a decision. **The exploration adapter (E0 only) proxies WSOL by V band**: EXP-022 Amendment 3 item 4 row 2 says "A non-WSOL pool with V in the band would be an attempt"; E0's span is one UTC day of exploration tape (section 2.1 item 1), which predates this redeploy. **No output of this review runs either filter on a non-WSOL graduation**; the 4 non-WSOL graduates in `out/s05_graduations.json` are listed, not filtered. The walk2 exclusion has a fixture test (`tools/test_cap_pick_score_exp022.py:278`, `USDCmint`, 90 passed in round 1), not a post-deploy graduation. That a depth-2 coin is never WSOL-quoted is **docs text** (a coin paired with a pump coin has the quote coin's depth plus one, section 1), not measured. |
| **Graduation population** | 60 newest migrate txs, 2026-10-14 | WSOL 56, other 4 (4 distinct quote mints), USDC 0. A3 runs: `other` in the 20-graduation sample 10-14 **1/20**; before the deploy 2, 0, 2, 2, 2, 4, 2 of 20 (14 of 140 over 7 runs); A3 60-sample quote mix `other` 10-14 **4/60**, before 2, 4, 4, 6, 6, 2 (24 of 360 over 6 runs). **Inside the earlier range.** |
| **Depth of the graduates** (`s05_graduations.py`, each graduate's BondingCurve byte 141) | 60 curves | wsol/depth 0: 56; other/depth 1: **2**; other/depth 0: 2; **depth 2: 0**. Both depth-1 graduates have a pump coin as quote (the quote coin's own curve exists, depth 0); the two depth-0 `other` graduates are quoted in a mint that is not a pump coin. **Depth-2 graduations in this sample: 0.** How many depth-2 coins have been created since 10-13: **not known** (3 creates in 18 blocks, all SOL-quoted; too few to say). A depth-2 coin must first raise the curve's equivalent in a pump-coin quote before it graduates, so graduation counts lag creation counts (reasoning, not measured). |
| **The create event** | `tools/pump_history_backfill.py:250-275` reads `quote_mint` from the tail (`token_program`, `is_mayhem`, `is_cashback`, `quote_mint`, 66 bytes) and `is_mayhem_mode`; migration event quote at `:315` (`raw[168:200]`) | Layout unchanged in the IDL and in the 3 creates and 60 migrations decoded (1 of 1 per group; 60 of 60). |
| **Bonding prints** feeding the frozen model's features | `observe/trade_decode.py:382-383`: **every `pump_bonding` row is labelled `quote_mint` WSOL, `quote_is_wsol` True**, and its `sol_lamports` is the event's `sol_amount` whatever the curve's quote is. The row's `mint` is `raw[8:40]` (the event's own mint), and the gate keeps one accumulator per mint (`tools/forward_exp012_gate.py` `note_print`, `self.acc.get(mint)`) | A code reading, not an observation: no output shows a TradeEvent from a non-SOL curve, and whether such prints are rejected by `_MAX_BONDING_SOL_LAMPORTS` (`observe/trade_decode.py:65`, used at `:363` and `:367`) was not tested. By code a mislabelled print enters **only its own mint's accumulator**, so it enters no WSOL mint's features. One edge case (quant-proof's reading): a non-WSOL mint that also had a WSOL-quoted PumpSwap pool could get a gate decision on mislabelled features; the scorer still drops it at `:1232`, so it could appear only in report-only gate-decision counts. `bad_disc` is not read by `tools/backfill_verify.py` (no match), so rejected non-SOL prints cannot make a walk-2 hour bad by that route. Pre-existing for depth-1 curves: the 10-09 review says the 10-08 upgrade added pump-coin quote mints; no output here shows when a non-SOL curve's print first appeared. **Depth 2 adds more curves of this kind; the count is not known.** |
| **Creator history** (`creator_prior_mints_24h`, one of the 18 features) | `tools/forward_exp012_gate.py:285` (`note_create`) and `tools/forward_paper.py:1488` (`_register_create`) take every create; neither has a quote-mint test (upstream filters not checked); `tools/exploration_entry_model.py:217` counts the creator's prior mints in 24 h, and `:266-285` (`build_creator_history`) builds the training history from `create` rows with no quote filter | A creator's earlier non-WSOL-quoted creates (depth 1 or 2) would count toward a later WSOL mint's feature, in training and in the read alike. Same since pump coins became quotes; **share of such creates not known.** Disclosure only. The other 17 features were not read for cross-mint terms here (quant-proof reads them as per-mint). |
| **V0 and the guard seed** `seed_p = (SEED_Q + V0) / SEED_B`, `SEED_Q` = 67,405,853,863, `SEED_B` = 206,900,000,000,000 (EXP-022 section 4.3) | `tools/cap_pick_score.py:135-136`, `:459`. The pool's opening quote and base are set by the **pump** program at migration; they are not in `Global`. | **Measured offline from the cached migrate txs** (`s11_migration_amounts.py`, `out/s11_migration_amounts.json`; WSOL-quoted graduations only, by class). **Post-deploy** (the 60 graduates of step 5, 56 WSOL, slots 456728763-456754256) and **pre-deploy** (84 migrations in the 10-13 review's cache, 78 WSOL, slots 456126828-456354425): `mint_amount` (base deposited) equals `SEED_B` on **41 of 56** post and **69 of 78** pre (min 195,733,742,545,754 post, 198,986,026,420,007 pre; max 206,900,000,000,000 both); `sol_amount` median **84,990,359,056** in both, equal to `SEED_Q` on **0** of 56 and **0** of 78 (also 0 after subtracting `pool_migration_fee`, 15,000,001 on every one); the pool's quote vault after the migrate tx has its mode at **67,405,853,768** (17 of 56 post, 30 of 78 pre), which is **95 lamports below `SEED_Q`** (about 1.1e-9 of 85 SOL), and equals `SEED_Q` on 0 of 134; the pool's base vault after the tx equals `SEED_B` on 39 of 56 post and 64 of 78 pre. So the modal and median opening amounts are **identical before and after**. The share at `SEED_B` is not: 41 of 56 post against 69 of 78 pre (one-sided Fisher p = 0.021, quant-proof's computation, unplanned); the exact modal `sol_amount` is on 18 of 56 against 31 of 78; post has a base value 205,001,933,174,807 and a `sol_amount` 85,777,265,694 on 3 pools each; pre has at most 1 pool on any non-modal base value and at most 2 on any non-modal `sol_amount` (84,990,359,059 on 2). Mayhem and synthetic pools are not split. The pre sample starts before the 10-12 fees redeploy (slot 456126828 < 456132171). Whether the shift is the redeploy, the class mix or the hour of day is not established. `SEED_Q` is 95 lamports from the modal quote; `SEED_B` is the modal base. Pools that open with less base than `SEED_B` or with a smaller quote (post min `sol_amount` 17,441,208; pre 8,700,479) are not classified here; the 10-09 review ties lower-quote openings to synthetic migrations, which was not tested. V0 itself (the first print's V) was not read. |
| **PumpSwap prints, pre-trade reserves, fee tiers** | PumpSwap (not redeployed), PumpSwap FeeConfig (sha equal to its pin) | Not touched (section 3). PumpSwap `BuyEvent` / `SellEvent` that the walker could not decode: 3 of 129 and 3 of 137 in the recent group (section 2); cause not examined. |
| **Halt (P2)** | EXP-022 section 10 P2, section 11, Amendment 5 item 1: a reviewed re-pin plus a clean last run before 2026-10-16T01:00Z cures `pins_changed` | **Touched.** The 10-14T07:11:10Z run halted on `pins_changed` alone. With the new pins the same record gives no change (Verdict). The last run before 10-16T01:00Z is the 10-15 run (EXP-022 Amendment 5 names about 06:41Z; the recorded runs started at 07:11Z on 10-09, 10-10, 10-11, 10-13 and 10-14 and 07:57Z on 10-12). The cure needs that run to show no halt on all six flags. |

Plainly:
- **This change does not alter, as far as these samples show, the create or migration event layouts, the PumpSwap programs, the fee configs or the modal pool-opening amounts (their centre; the share of non-modal openings is higher after, section 6) that CAP-PICK reads.** It widens the population of non-WSOL coins; for the read (walk2) they are dropped by code, and no output of this review runs that drop on a real non-WSOL graduation.
- **It does move:** (a) the pump binary (not diffed), (b) `Global.max_curve_depth`, (c) the A3 `pins_changed` state.
- **Not known:** whether the new binary changes anything the docs do not say; the count of depth-2 coins created or graduated; whether non-SOL curves' bonding prints enter the walk tape as WSOL rows with quote-coin units (and how many); the share of creator-history counts that are non-WSOL creates; the V0 of any post-deploy pool; why the constants differ from the modal amounts by 95 lamports.
- **Not touched by this review:** the pick set, the book, any outcome (all sealed by EXP-022 section 9).

## Residual risks

1. **The binary is not diffed.** Strings and the IDL agree (52 of 56 instruction names with a confirmed boundary); behaviour beyond them is not established. The 10-13 fees review had the same limit.
2. **Another redeploy would halt again.** P2 reads the last run before 10-16T01:00Z, and the cure needs no halt on all six flags (`synthetic_share_high` included). A redeploy of **any pinned program** before that run halts P2 and withdraws CAP-PICK; EXP-022 section 11 puts the trigger hour of a post-start `pins_changed` redeploy at the deploy slot's hour. Pump deploy slots of record fall on 10-02, 10-08 and 10-13, and fees on 10-08 and 10-12. Nothing in this PR can prevent or detect that earlier than the next daily run.
3. **`Global` is unpinned**, so a change to `max_curve_depth` (now 2, program cap 5) raises no flag by itself. The monitor code is not touched here.
4. **Samples are small:** 6 blocks per group, 1 create and 1 curve size per group, 60 graduates, 13 BOOST pools. The decoder differences in section 2 are counts of 6, 2 and 0.
5. **Executing signer** `5sFJMw5d…` on the Squads upgrade is the same as the fees upgrade; not identified.
6. **The Pool account size varies** (301 on 59, 287 on 1). Not examined.
7. **Depth-2 volume and the tape's labelling of non-SOL curves** are open (section 6). The synthetic-migration share (3/17 on 10-14) is a separate matter and is not related to this change by any evidence read here.
8. **Merge timing and checkout.** The pins file the daily runner reads must carry the re-pin before the 10-15 run. Which checkout the runner reads was not checked here. At this head the blobs are `tools/pump_structure_pins.json` `be2bc1a2d8f7bfd919e13faf2309de89f741a8f9` (`git hash-object`; it changes if the pins file moves) and `tools/pump_structure_monitor.py` `1ca0a88cecf0853d94336ea046ba1a910b79f198` (unchanged).
9. **The seed constants are not the modal amounts** (section 6): 95 lamports at the quote mode, and 15 of 56 post (9 of 78 pre) pools open with less base than `SEED_B`. The modal and median amounts are identical before and after; the share of openings below `SEED_B` is higher after (15 of 56 against 9 of 78), cause not established; whether the constants are right for the other pools is not examined.

## From the rulings on round 1 (decided elsewhere; recorded here, not decided here)

Quant-proof comment 6163455594, section 1(d):
- The P2 declaration (the manager's, merged before the 10-15 run) names the job and the checkout, and the job prints at its start the pins blob (at this head `be2bc1a2…`) and the monitor blob (`1ca0a88c…`).
- No `pump_structure_monitor` run of any kind (scratch, dry, `--write-pins`) starts between the P2 run's start and 2026-10-16T01:00:00Z. This review's `--write-pins` run and RPC reads were on 2026-10-14, before that window.
- The 10-14 run evaluated both core rules (`boost_share_low`, `boost_last_slice_early`), so a 10-15 run with a core rule unevaluated is one day, not two.

## Re-pin recommendation

`tools/pump_structure_pins.json`, `programs.pump` only; everything else keeps its value (this PR):
- `deploy_slot`: **456494698**, `deploy_utc`: **2026-10-13T15:40:41Z**
- `sha256`: **ba1f91ac91dd8c578fd069230601f090775b88acbeecd680bde10d90e3606671**, `data_len`: 10485760 (unchanged)
- `--write-pins` also rewrote `pinned_utc` (2026-10-14T07:21:43Z) and `pinned_at_slot` (456753561). The three account pins, the PumpSwap and fees program pins and `boost` (17.585 SOL, 29 slices) are unchanged. The monitor code is untouched.
- `tools/fixtures/pump_structure_monitor/programdata.json` re-recorded from public RPC (ctx slot 456753674; decoded deploy slots 456494698, 454596406, 456132171, equal to the pins); `tools/test_pump_structure_monitor.py` expects the new pump slot. `115 passed`.

## Scripts (`ARTIFACTS/lab/program-upgrade-1014/`)

Round 0: `common.py` (public RPC only, per-attempt log, hard stop at 09:25Z, refuses to start while `tools.boost_slice_watch` runs), `s00_idldiff.py`, `s00_offsets.py` (no network; outputs now in `out/s00_idldiff_{pump,fees,amm}.txt` and `out/s00_offsets_{pump,amm}.txt`), `s01_write_pins.py` (8 calls), `s02_fixture.py` (1), `s03_program_global.py` (5), `s04_blocks.py` (22), `s05_graduations.py` (107, 4 of them retries), `s06_records.py` (offline), `s07_bad_events.py` (cache only), `s08_pins_offline.py` (offline), `s09_pool_v.py` (cache only). The 143 attempts are 8 + 1 + 5 + 22 + 107 by script tag in `rpc_calls.log`.

Round 1 (no Solana RPC): `s10_docs_commit.py` (GitHub only; host-checked; saves `out/docs/`, `out/s10_docs_manifest.json`, `out/s10_docs_quotes.txt`, the IDL diffs and offsets), `s11_migration_amounts.py` (cache files only), `s12_binary_names.py` (the saved ELF only), `s13_rpc_log_summary.py` (the log only).

**[not saved]:** the round-0 hand-fetched GitHub responses (re-fetched in round 1, sha256 equal for the six IDL files, not equal for the commit JSON); the round-0 `.diff`; the off-repo raw cache and the 10 MB ELF (`/data/mal/program-upgrade-1014/raw/`, referenced by sha256 in `out/s12_binary_names.json`).
