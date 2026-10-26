# P41 DECLARE: p37-cto-hold, detection + counts. COUNTS ONLY. Written before any count.

Declared at (date -u): 2026-10-26T17:30Z. Lane P41 (manager17). Exploration, no pricing, no P&L, no returns. 0 Helius credits; public RPC only (`https://api.mainnet-beta.solana.com`), cap **1,500 calls** in total, every call logged.
Branch `claude/p41-cto-hold`. Parent: IDEAS.md row #8 `p37-cto-hold` on `claude/p37-idea-sprint` (PR #946): "Hold 24 h after an admin-signed creator-fee redirect (a community takeover, CTO)"; capacity guess there "3/day × 0.25 × +3% = 0.0225 (borderline); tape coverage of the instruction unknown"; verdict "Near-miss (4th)".

**IDEAS.md fixes no kill rules for #8** (only the top 3 have a "Kill rules (fixed now)" paragraph). The kill rules below are fixed by this file, now, before any count.

## What was done before this file (no count kept)
- Read the pump / pump_amm IDLs on disk (`/data/mal/audit-1008/work/g_october_structure_check/ext_github/idl_0912/` and `idl_new/`, pump-public-docs snapshot of 2026-10-08) for instruction names, accounts and args.
- Read one row of each tape stream (trades, creates, migrations) of each allowed block for FIELD NAMES ONLY, the record-type tally of one creates and one migrations file per block, and the key set of trade rows in one hour file per block (no value kept).
- Read `observe/trade_decode.py` discriminator list.

## How a CTO shows on chain (from the IDLs, to be confirmed by the RPC pass)
- pump (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`) instruction `admin_cto` (disc `[125,126,214,134,77,229,188,89]`), args `is_holder_reward: Option<bool>`, `creator_fee_bps: Option<u64>`, `new_creator: Option<Pubkey>`. Signer: `admin_set_creator_authority` (a key stored in pump `Global` at byte offset 418). It CPIs into pump_amm `admin_cto_pool` (disc `[45,61,165,151,104,0,49,189]`, signer `pool_authority` PDA) when the coin has a pool, and into pump_fees (sharing config).
- Other creator-changing instructions, not admin CTOs: pump `set_creator` (signer `Global.set_creator_authority`; sets creator from metadata at launch), pump `set_metaplex_creator`, pump_amm `set_coin_creator` (permissionless, copies the curve creator), `migrate_bonding_curve_creator` / `migrate_pool_coin_creator` (fee-sharing migration). These are counted separately if seen and are NOT CTO triggers.
- Events: `SetCreatorEvent`, `SetBondingCurveCoinCreatorEvent`, `MigrateBondingCurveCreatorEvent`, `MigratePoolCoinCreatorEvent`, etc.

## What the tape holds (verified before this file)
The clean views of all five allowed blocks hold only `trade`, `create`, `complete` and `migration` records. Trade rows carry no `creator` / `coin_creator` field, and the decoder (`observe/trade_decode.py`) decodes TradeEvent, Buy/SellEvent, CreatePoolEvent and a few post-10-07 events, none of them a creator-change event. **So MAL's tape does not contain CTO events.** Detection must come from public RPC.

## Detection route (public RPC, frozen)
1. `getAccountInfo` of pump Global (`4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf`): read `admin_set_creator_authority` (offset 418) and `set_creator_authority` (offset 386). Also pump_amm `GlobalConfig` for its admin / `admin_set_coin_creator_authority` key, if the layout can be read from the IDL.
2. `getSignaturesForAddress(admin_set_creator_authority)`, paged back (limit 1000) until blockTime < 2026-08-14T12:00Z or history ends. Every signature this key signed is a candidate. Report per UTC day: total, err != null, and the oldest reachable blockTime.
3. `getTransaction` (jsonParsed off, maxSupportedTransactionVersion 0) on every successful candidate inside the allowed windows (below), up to the remaining call budget, in time order. Classify each by its top-level and inner instructions: `admin_cto` (pump), `admin_cto_pool` (pump_amm), other. Extract mint (pump `admin_cto` account 3, or the pool's base mint from the tape), `new_creator`, `creator_fee_bps`, `is_holder_reward`. A tx with no admin_cto/admin_cto_pool is counted as `other`, never as a trigger.
4. If the authority key changed inside or after the windows (oldest signature after 2026-08-14T12Z while the program already had `admin_cto`, or a gap), that is reported as a coverage hole for those days. No other address is searched unless found in a decoded admin_cto tx (for example an earlier authority named in a Global update); any extra address and its call cost are logged.
5. If public RPC does not serve the history (error or empty for the windows), the verdict is NOT_DECIDABLE with the missing data named.

## Allowed data (ledger at main 6a72511c, exploration pool rows)
- Window A: explore-0814 `[2026-08-14T12, 2026-08-28T12)`.
- Window B: fresh-0903 `[2026-09-03T12, 2026-09-09T12)` + exp011-0909 `[2026-09-09T12, 2026-09-15T12)`, contiguous.
- Window C: fast-pool `[2026-09-18T23, 2026-09-22T00)` + oracle-insample `[2026-09-22T00, 2026-09-25T07)` (hour 09-25T07 excluded; it is the Oracle live tape row's).
- Read from `/data/mal/clean-view/<block>/` only. No October hour, no arena tape, no forward-1016, no meta-capture, no fresh-0828 (reserved), no fresh-0802/0808 (reserved).
- Full UTC days: A 08-15..08-27 (13), B 09-04..09-14 (11), C 09-19..09-24 (6) = 30.

## Counts to make (counts only)
For each successful admin CTO event at time t (blockTime) inside a window:
- **Trigger**: the event's mint has at least one tape trade in `[t, t+1h)` (enterable). Events with no such trade are counted as `no_trade_1h`, not triggers.
- Per window and per full UTC day: events, triggers, venue at t (pump_bonding / pumpswap: venue of the last tape trade of the mint at or before t; `none` if no earlier trade in the window).
- **Pool age at t**: pumpswap: t minus the mint's `migration`/`complete` record time, else minus its first pumpswap trade in the window (then reported as a lower bound, `left_censored`). pump_bonding: t minus the `create` record time if in window, else lower bound.
- **Quote depth at t**: `quote_reserve` of the mint's last tape trade at or before t, in SOL. Conventions (verified, memory "reserve convention"): PumpSwap rows are PRE-trade vault quote (no virtual V added; V ≈ 17.58 SOL exists on migrated pump pools and is not added here); bonding rows are POST-trade virtual SOL reserves. Reported per venue, as median and quartiles, never mixed.
- COUNT LABELS only (no return computed or printed): `alive24` = at least one tape trade of the mint in `[t+23h, t+25h)`; `q_up24` = `quote_reserve` of the last trade at or before t+24h ≥ quote at t, same venue (a venue change counts as `venue_changed`, not up/down). Events whose t+25h is past their window end are `censored` and excluded from both shares' denominators (windows are not joined across gaps; B is contiguous, C is contiguous).

## Kill rules (fixed now, before any count)
- **K-D (detection)**: if the admin CTO instruction cannot be found by the route above on the allowed windows (history unreachable, authority unknown, or 0 decodable admin_cto txs while the route works and the instruction already exists in the program), the verdict is **NOT_DECIDABLE**, with the data that would decide it named. A route that works and shows the authority signing nothing in the windows is a real zero, reported as such (K0 then applies).
- **K0 (rate)**: median triggers per full UTC day over the covered full days < 3 (the row's own capacity guess) → **KILL**.
- **K1 (exit)**: share `alive24` < 0.5 among uncensored triggers → **KILL** (a 24 h hold on coins that stop trading has no exit).
- **K2 (dead-pool pattern)**: share `q_up24` < 0.25 among uncensored triggers with the same venue at t and t+24h → **KILL**. This is a direction count, not a return; it is the analogue of the time-cap check other lanes run before a mean.
- **K3 (capacity)**: fewer than 50% of triggers with quote depth at t ≥ 20 SOL (PumpSwap, vault quote) or ≥ 40 SOL virtual (bonding) → **KILL** (0.25 SOL would move the price by more than about 2.5% round trip).
- If fewer than 5 full UTC days are covered by the detection route, the verdict is NOT_DECIDABLE even if counts look fine.
- Otherwise: **ADVANCE-TO-PRICING-DECLARE**. That is not evidence of an edge; it only allows a separate frozen pricing DECLARE (entry rule, fees, both fail models, day sets chosen before any return is seen).
- No threshold is re-tuned after a count.

## Not made
Any price path, any return, any exit, any P&L, any join to HV4/Q50 outputs, any read of a sealed or reserved block.

## Seals obeyed (hunt30 SEALS-BRIEF, sha256 823cc062…)
HV4, Q50, DBC forward window and fwdv1/, arena tape and forward-1016 not touched. No tape hour ≥ 2026-10-01 read. Public chain history read by RPC is not tape. Disclosure: before this file, an agent `find /` for IDL files descended into `/data/mal/meta-capture/` and printed one filename (an `img/2026-10-24T00/*.png` path); no file there was opened. That tree is now excluded from every search.

## Amendment 1 (2026-10-26T18:46Z, written before the tape scan; no tape count of any CTO event made yet)

**What was seen before this amendment** (public RPC structure only): the signature history of `admin_set_creator_authority` = `UqN2p5bAzBqYdHXcgB6WLtuVrdvmy9JSAtgqZb3CMKw` (a Squads vault; the same key is `admin_set_coin_creator_authority` in PumpSwap `GlobalConfig`), per-day signature counts, and the instruction types of its 333 successful in-window transactions. Also, as a code check of `tools/p41_cto_counts.py`, a dry run on the first 32 decoded events against window C tape only printed partial labels (12 triggers; alive24 9 of 11; q_up24 2 of 11; depth_ok 10 of 12). No rule below was changed because of those numbers; K0-K3 are unchanged.

**Finding.** `admin_cto` first appears on 2026-09-12T15:31:15Z (pump IDL refresh of 2026-09-12, pump-public-docs `e0687ae9b7`). Before it, the same authority changed creators with the older instructions pump `admin_set_creator` (disc `[69,25,171,142,57,239,13,4]`, accounts `admin_set_creator_authority, global, mint, bonding_curve, ...`, arg `creator`) and pump_amm `admin_set_coin_creator` (disc `[242,40,117,145,73,96,105,104]`, accounts `admin_set_coin_creator_authority, global_config, pool, ...`, arg `coin_creator`), per the IDL at pump-public-docs `9c82f61cb7` (2026-07-15). The last `admin_set_creator` in the windows is 2026-09-11T11:31:57Z. In-window instruction counts: `admin_cto` 101, `admin_set_creator` 112, `admin_set_coin_creator` 62. Other authority txs (118) are Squads housekeeping, creator-fee collects/distributions and fee-sharing resets; they are not triggers.

**Under the DECLARE as written** (`admin_cto` only), days before the instruction existed are not covered (K-D: "the instruction already exists in the program"). So:
- **Set P (primary, as declared):** `admin_cto` events. Covered full UTC days: 09-13, 09-14 (window B) and 09-19 .. 09-24 (window C) = 8. 2026-09-12 is a switch-over day and is not covered.
- **Set S (amendment, declared now):** `admin_cto` ∪ `admin_set_creator` ∪ `admin_set_coin_creator`, one event per (tx, mint) (a tx with both old instructions is one event; a lone `admin_set_coin_creator` gets its mint from the pool account's `base_mint`, one `getAccountInfo`). Covered full days: all 30.
- K0-K3 and the censoring rules apply unchanged to each set. **ADVANCE-TO-PRICING-DECLARE needs both P and S to pass; otherwise KILL** (or NOT_DECIDABLE where the DECLARE says so). The `new_creator` subset (a new steward key, not only a holder-reward or fee-bps flag) is reported as a label for P and S, with no separate verdict.
- One in-window successful signature returned `null` from `getTransaction` (not decodable): a coverage hole of 1 tx, reported.
