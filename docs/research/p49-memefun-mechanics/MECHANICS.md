# P49: meme.fun mechanics. Is there any forced or scheduled flow?

Lane P49, manager17. Written 2026-10-26 ~19:15Z (`date -u`). Desk read plus public chain reads only: **112 public RPC calls** (ledger `data/p49_calls.jsonl`: 98 to `api.mainnet-beta.solana.com`, 14 to `solana-rpc.publicnode.com`; 13 of the 112 were refusals: 12 pruned-block `getBlockTime` and 1 `getProgramAccounts` 403 on publicnode). **0 Helius credits.** No websocket, no MAL tape, no seal-window data, no pricing. **Counts and protocol mechanics only. No edge claim. Nothing here says a book makes money. No book has cleared the promotion gate.**

Program `MEME4gfGe6itEpxgM1xaBVmiFF5XuCTc4oVXUE5w56F`. Sources:
* the on-chain Anchor IDL (copy on `origin/claude/p45-memefun-watch`, `docs/research/p45-memefun-watch/idl.json`);
* the site's own docs at `https://meme.fun/docs`, `/docs/api` and `/docs/security-audit`. These pages are client-rendered, so the text was read from the site's JS bundles. The site names a Paladin Blockchain Security audit, which was not read;
* public chain state (`scripts/`, `data/`).

A web search for "meme.fun solana launchpad" and for the program id found **no third-party coverage**: no articles, no docs mirror, no X announcement in the index. The project's own docs are the only written source.

## 0. Bottom line

* **meme.fun has no forced or scheduled flow of any kind.**
  * There is no graduation and no migration. The program has 11 instructions (`initialize`, `create_token`, `buy`, `sell`, `swap`, `claim_creator_fees`, `claim_protocol_fees`, `set_fee_recipient`, `set_curve_config`, `propose_admin`, `accept_admin`), and none of them creates a pool, adds liquidity or moves reserves. The docs say so in plain words: "It never migrates", "every token trades on the meme.fun dex for life. no migrations."
  * There are no protocol buys or sells, no buyback, no keeper and no crank.
  * There is no creator allocation (the full 1B supply is minted to the curve), no vesting, no lock, no auction and no launch delay.
* **The BOOST analogue does not exist here.** BOOST is a protocol-forced buyer of about 17.6 SOL at PumpSwap graduation. meme.fun has no graduation event, so it has no forced buyer.
* **Counts at slot 461775935 (`getProgramAccounts`, 2026-10-26T19:04:21Z):**
  * **516** bonding curves exist since 2026-08-21. 500 were created before 2026-10-26 (tests and pre-launch), 4 on 10-26 before 14:18Z, and **12** at or after the public launch (first at 14:18:17Z).
  * **0 graduations**: none exists on-chain. **0** curves have reached the "fully real" point (166.744285715 SOL real) and **0** have reached the 350-SOL fee tier.
  * The largest curve holds **54.043078811 SOL** real. All 516 curves together hold 141.137388112 SOL.
* **Recommendation: close P49 as a forced-flow lane.** Add three counts-only tripwires to P45 (§5), at ~3 public calls a day. Any one of them firing reopens the question.

## 1. Token lifecycle, exactly

**Launch (`create_token`).**
* One transaction creates the Token-2022 mint (6 decimals), the `BondingCurve` PDA (seeds `["bonding-curve", mint]`), a fresh reserve token account, a fresh classic-SPL WSOL `sol_vault`, and Metaplex metadata.
* It mints the full 1,000,000,000 tokens (raw 10^15) to the curve and revokes the mint authority.
* The metadata URI must be IPFS or Arweave (error 6017).
* The payer becomes `creator`, which is pinned for life.
* The creator gets **no token allocation**. A creator who wants tokens has to buy them on the curve like anyone else.
* `Global.config` is snapshotted onto the curve and frozen. `set_curve_config` changes only future launches.

**Live Global config** (`data/global.json`, Global PDA `GxaaVK4syzWYAHc88QBR8q1a3TP9t1aQmQuQ1Vx9wkK7`):

| field | value |
|---|---|
| `initial_virtual_sol` V0 | 33,348,857,143 lamports (33.348857143 SOL) |
| `drain_alpha_bps` α | 4000 (α = 0.40) |
| `curve_shape` | 1 (tapered) |
| `fee_tier_threshold_lamports` | 350,000,000,000 (350 SOL of **real** reserves, not market cap) |
| low tier (< 350 SOL): creator / protocol / LP bps | 98 / 20 / 0 (total 118) |
| high tier (≥ 350 SOL): creator / protocol / LP bps | 0 / 5 / 25 (total 30) |
| admin = upgrade authority | `55cVpLhERp4aJZqZ1hAV6rSApu661ErwtBTCpbb8KACb`, a Squads v4 vault (its txs are `VaultTransactionExecute`; the last one, 2026-10-04T15:24:54Z, ran `SetCurveConfig`). The docs' "24-hour timelock" was **not verified**. |
| fee_recipient (treasury) | `7BRjUvfDZ2Vyhzv1ajsj12nnFFkbqJcz4GXtEk51hT6F` |
| last program deploy | slot 453295836, 2026-10-04T15:23:30Z |

Curve config populations in the snapshot:
* 499 curves are `curve_shape` 1 with the fees above;
* 13 are `curve_shape` 60 (the original α-drain) with the same fees;
* 4 are shape 60 with 50 / 20 / 25 low-tier fees. All 17 shape-60 curves predate 2026-10-04.

**Curve formula.** Read from the site's pricing code (`computeBuy` / `computeSell`), then checked against chain: the formula reproduces `virtual_sol_reserves` exactly on **516 of 516** curves.
* Pricing is constant product, x·y = k, between x = V(R) and y = the curve's token reserves. Here R = `real_sol_reserves` (net of fees).
  * At launch, `virtual_token_reserves` = `real_token_reserves` = 10^15 raw, and x = V0.
  * k = V0 · 10^15 is fixed for the life of the curve.
* Tapered shape (1):
  * V(R) = V0 + (1−α)·R + α²·R²/(4·V0) while R < R\* = 2·V0/α = **166.744285715 SOL**;
  * V(R) = R above that point. This is the docs' "by about 167 SOL of real liquidity the curve is fully real".
  * V and dV/dR are both continuous at R\*.
* Original shape (60): V(R) = V0 + (1−α)·R up to R = V0/α, then V(R) = R.
* Buy: fee = ceil(gross · total_bps / 10^4); net = gross − fee; tokens_out = y − k / V(R + net), capped at the real token reserves.
* Sell: the exact inverse. The fee is taken on the SOL out, and the payout is bounded by R and a rent floor.
* The execution price is (x/y) / (dV/dR). Reference points (arithmetic on the formula, not a price model):

| real SOL R | share of supply sold | marginal price (SOL/token) | supply × price (SOL) |
|---|---|---|---|
| 0 | 0% | 5.558e-08 | 55.6 |
| 10 | 15.51% | 7.486e-08 | 74.9 |
| 54.043 (largest curve now; chain shows 51.8622% sold, matching) | 51.86% | 1.972e-07 | 197.2 |
| 166.744 (R\*, fully real) | 80.00% | 8.337e-07 | 833.7 |
| 350 (fee-tier line) | 90.47% | 3.673e-06 | 3,673.3 |

There is **no ceiling and no completion point in practice**. Tokens approach zero only asymptotically, so `CurveComplete` (6004) is unreachable at any realistic R.

**Fees and recipients.**
* Creator and protocol slices accrue inside the curve's WSOL vault as `creator_fees_owed` / `protocol_fees_owed`, outside R. They are withdrawn as WSOL by `claim_creator_fees` (the creator only) and `claim_protocol_fees` (the admin, paid to the treasury's WSOL account).
* Claims do not change R or the token reserves, so **a claim has zero price effect**.
* The LP slice (0 in the low tier, 25 bps at R ≥ 350 SOL) is added to R and never paid out.
* The creator fee pauses at R ≥ 350 SOL and resumes if R falls back below. This is a live lookup with no latch: `creator_fees_ended` and `mcap_high_since` are documented vestiges and are always false/0.
* Round-trip fee cost for a trader: 2 × 118 = 236 bps below 350 SOL, 2 × 30 = 60 bps above, plus the Solana tx fee and WSOL/ATA rent. Fees only; impact is not counted here.

**Graduation / destination venue.** **None.** Two things could look like one, and neither is:
* The front-end bundle has `feed:graduating` "complete" / "migrate" (with `poolAddress`) handlers and "graduated"/"migrated" badges. These look like launchpad-template code. The program has no migrate instruction. When or whether the indexer emits "complete" (perhaps at R\*) was **not measured**; it would only be a UI label.
* Crossing R\* changes nothing on-chain except that V(R) becomes R.

**Protocol-side buys or sells.** **None.**
* No instruction lets the protocol trade.
* The treasury wallet has 6 signatures ever (2026-08-19 → 2026-10-24T04:12:56Z, none on 10-26). The 4 that were opened are System, ComputeBudget and ATA calls, not trades.
* The protocol's claims land in its WSOL account and are SOL, not meme tokens.

**Anti-sniper error codes in the IDL.** `CooldownActive` 6001, `EarlyBuyCapExceeded` 6002, `MaxWalletExceeded` 6003 and `VestingNotMatured` 6015 are in the IDL, but:
* no config field drives them;
* the docs' error table omits them;
* chain shows no sign of them. In all 1,613 program txs since 2026-10-26T00:00Z (`getSignaturesForAddress`, 2 pages), 63 failed:
  * 56 × Custom 6001 at instruction index 2, all on one curve (`2YTyvT4S…`, mint `3N99AqAy…`, created 18:10:00Z), 17–47 s after create. The one opened is a third-party guard program, `L2TExMFKdjpN9kozasaurPirfHy9P8sbXoAN1qA3S95`, not meme.fun. The other 55 share index 2 and the same burst wallets but were not opened;
  * 3 × 6009 (slippage; the one opened is meme.fun `buy.rs:111`);
  * 2 × 6017;
  * 1 × 6008;
  * 1 × 6012.
* Of the 16 curves created on 10-26, 12 saw their first successful post-create tx 1–10 slots (0–3 s) after creation, and none had a failed tx before it. The other 4 had at most 12 txs in total, and their first came 48–735 slots later (no demand, no failures). Source: `data/cooldown.json`.

Conclusion: **there is no launch delay or cooldown live**. These codes look vestigial, like `mcap_high_since`, but that is inferred and not proven from the binary.

**Upgrade risk.** An upgrade could add any of these later. The admin is a multisig, and the claimed 24 h timelock (not verified) would give a day's public notice. Tripwire T1 in §5 watches for this.

## 2. Mechanism screen (forced? predictable minutes ahead? large vs depth?), capacity at 0.25 SOL

Capacity here means SOL/day of forced counter-flow a 0.25-SOL, non-racing entry could ride. For reference, BOOST is about 17.6 SOL forced per PumpSwap graduation.

| Candidate | Exists on meme.fun? | Forced or scheduled? | Predictable minutes ahead? | Size vs pool depth | Events so far | Capacity at 0.25 SOL |
|---|---|---|---|---|---|---|
| Graduation / migration buy or pool seed | **no**: no instruction | — | — | 0 | 0 | **0** |
| Protocol buyback / treasury buys | **no**: no instruction; treasury never traded | — | — | 0 | 0 | **0** |
| Fee-funded buys | **no buy**. Fees accrue as WSOL. The LP slice (25 bps, only at R ≥ 350 SOL) is added to R passively | passive and continuous, not an order | yes (rule) | 0.25% of volume; e.g. 100 SOL/day of volume at R = 350 adds 0.25 SOL to R (≈ 0.07% of R). Arithmetic, not measured | 0 curves at ≥ 350 SOL | **≈ 0** |
| Fee claims (creator / protocol) | yes, WSOL withdrawals | discretionary | no | 0 effect on R or price | not counted | **0** |
| Creator lock / vesting / unlock dump | **no**: creator gets 0 tokens; no vesting account or instruction | — | — | 0 | — | **0** |
| Curve with a known ceiling (R\* = 166.744 SOL; fee tier at 350 SOL) | the curve is deterministic, but no event fires at R\*; the 350-SOL line only changes fees (118 → 30 bps) | **not forced**: crossing needs discretionary buyers | the R distance is visible, but the crossing time is not | — | **0** curves at R\*, **0** at 350 | **0** forced; any tier-crossing effect would be discretionary attention flow (not this lane) |
| Auction phase | **no** | — | — | — | — | **0** |
| Launch delay / cooldown | **no**: first post-create successes at +1 to +10 slots; the 6001s are third-party | — | — | — | — | **0** |

**Capacity screen result: 0 SOL/day of forced flow** at any stake, so trades/day × stake × net edge = 0 for every row. What remains is discretionary flow on a deterministic curve. That is the same class MAL already runs on other venues (event reaction, momentum). It is not the BOOST class.

## 3. Counts from public chain

Snapshot `getProgramAccounts` (BondingCurve discriminator) at slot 461775935, 2026-10-26T19:04:21Z. Creation times are exact `getBlockTime` for every curve created after slot 460591131; earlier ones are interpolated between 13 anchors. The full table is `data/curves_snapshot_slot461775935.csv`.

| Count | Value |
|---|---|
| BondingCurve accounts | **516** (323 distinct creators; top creator `8ahdQu…` has 16, all early tests) |
| created 2026-08-21 … 10-09 | 237 (incl. 124 on 10-08, 49 on 10-07; day bins before 10-23T21:11Z are interpolated) |
| created 10-14 … 10-25 | 263 (incl. 232 on 10-23) |
| created 2026-10-26 before 14:18Z | 4 |
| created ≥ 2026-10-26T14:18Z (public launch), up to 18:10:00Z | **12** |
| curves with any tokens out now (real tokens < supply) | 39 of 516; 477 sit at full supply |
| graduations | **0**. No such event exists |
| curves with R ≥ 166.744285715 SOL (fully real) | **0** |
| curves with R ≥ 350 SOL (fee tier) | **0** |
| curves with R ≥ 10 / ≥ 1 / ≥ 0.1 SOL | 2 / 11 / 17 |
| largest R | 54.043078811 SOL (curve `4FYyiCA6…`, mint `RFVyEip2…`, created 17:06:10Z, 51.8622% of supply sold); next 40.651 SOL (a shape-60 test curve from 08-21) |
| total R over all curves | 141.137388112 SOL |
| unclaimed fees, all curves | creator 40.622003677 SOL, protocol 5.356351271 SOL |
| program txs since 2026-10-26T00:00Z (to 19:06:15Z) | 1,613, of which 63 failed (§1) |
| protocol-side flow at "graduation" | **0 SOL**: no graduation; the treasury made no trades |

**Note on volume (arithmetic, not measured as volume).** The first public launch is curve `GGHQ6Q…`, the GT pool P36 named, mint `6fAJ5V4C…`, 14:18:17Z. It holds R = 0.001156942 SOL but 1.949317850 SOL of protocol fees owed. At 20 bps that means at least about 975 SOL of round-trip flow (fees owed is a lower bound, since claims reduce it). Most of meme.fun's day-0 volume is churn on near-empty curves, not depth. That fits P36's remark about pools seeded by one operator.

**Not measured:**
* a decode of buys/sells or volume per curve;
* the creator's own buys at launch;
* when the indexer labels a token "graduated";
* the Squads timelock;
* the program binary (only the IDL, the docs and the chain behaviour);
* a cross-check against the P45 tape on mal-fast-0. That host was not reachable by name from this session, and P45's daily counts will cover it.

## 4. Seal and rule notes

* No MAL tape, no arena tape, no HV4/Q50 output, no DBC window, no `/data/mal/meta-capture/`.
* meme.fun is not in any ledger domain.
* No websocket was opened. Public HTTP only, spread over two endpoints to keep load off the arena feed's endpoint; 112 calls against the 500 cap.
* No pricing and no P&L.
* Day-0 venue state can change with one upgrade. Every number above is dated to its slot.

## 5. Recommendation

**Close P49 as a forced-flow lane** (no mechanism exists). **Do not open a new lane.** Add these counts-only tripwires to P45's daily job, about 3 public calls a day and 0 Helius:

* **T1, program change.** `getAccountInfo` on the ProgramData account `GZDLU7daJ4kmnceSUhmPEi6XykDjVgSekXQw53eDoBCq` (45-byte slice). Alert if `last_deploy_slot` ≠ 453295836. On alert, re-fetch the IDL and diff the instruction list for any migrate / pool / buyback / auction / vesting instruction.
* **T2, config change.** `getAccountInfo` on Global `GxaaVK4syzWYAHc88QBR8q1a3TP9t1aQmQuQ1Vx9wkK7`. Alert on any change to `config`, `fee_recipient` or `pending_admin` against `data/global.json`.
* **T3, tier census.** One `getProgramAccounts` (BondingCurve discriminator, base58 `4y6pru6YvC7`) a day. Write counts only: curves total, created that day, R ≥ 166.744 SOL, R ≥ 350 SOL, max R. This is also an independent cross-check of P45's creates/day.

**Revive triggers for a forced-flow lane** (QP ruling needed before any pricing):
* T1 shows an instruction that moves reserves or trades on the protocol's behalf;
* or T2 shows a config that adds a protocol buy.

Curves crossing 350 SOL are **not** a revive trigger for this lane. That flow would be discretionary, and belongs to P45's own promote rule (≥ 200 creates/day and slot-0-1 share < 10%), which is unchanged.
