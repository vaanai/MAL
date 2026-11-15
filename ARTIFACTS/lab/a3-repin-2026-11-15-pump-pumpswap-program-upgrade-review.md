# Review: pump AND PumpSwap program redeploys of 2026-11-15T17:20Z (slots 469811025 and 469811067), re-pin

Builder: Claude worker, 2026-11-15 (started `date -u` 19:36Z), mal-research-0. Public RPC (api.mainnet-beta.solana.com) and unauthenticated GitHub only. 0 Helius credits, no key or env file. No tape, arena, book, ledger, shadow, declog, tip or FLOOR file opened; no price path, outcome or P&L computed. Scripts and small outputs: `ARTIFACTS/lab/program-upgrade-1115/` (`out/*.json`); raw responses are off-repo in `/data2/mal/scratch/a3-repin-1115/`. This is a reading of chain and repo facts. No transaction was built, signed, simulated or sent. No sentence here says any book makes money; no book has cleared the promotion gate.

Standard: `/data/mal/hunt-1008/ops/QP-RULING-1115-a3-repin-dual.md` (sha256 64e37d09...), which carries `QP-RULING-1114-a3-repin-pump.md` Q1 items 1-13 over with the new slots. Each section below says which bar it answers.

## Verdict

PENDING (filled in at the end of the run).

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

