---
cursor:
  subagentId: "bc-2a759be3-8276-582f-b362-63147814bb02"
---

# PR #97 migrate hold_30s +114 SOL — targeted trace

Read-only. Host via DEC-011. Backfill hours `2026-09-25T01`–`T06` at `/var/lib/mal/backfill/trades`. Holdout JSON `/var/lib/mal/paper/laya-backfill-holdout/out/backward_holdout.json`.

## Scoreboard (not three equal 38 SOL trades)

`migrate_hold_30s` n=238, total +114.41390925, `total_ex_best_sol` −0.300381637, `total_ex_top3_sol` −0.518618664.

- Best trade = total − ex_best = **+114.714 SOL** (one mint).
- 2nd+3rd together ≈ +0.219 SOL.
- 114/3 ≈ 38 was an average, not three ~38 SOL fills.

n=238 equals the count of backfill creates in T01–T06 that have a bonding print and a later wSOL PumpSwap print. Fraction is 1.0 (no model filter). Headline is the 15% fail mix: reported ≈ 0.85 × raw.

## Schema vs live tape

Sample PumpSwap backfill row (`source=backfill`, `t_recv_ms=null`, `block_time=event_ts`):

| field | backfill | live tape |
| --- | --- | --- |
| `quote_mint` | WSOL | WSOL when stamped |
| `quote_is_wsol` | true (0 missing in T01–T06 wSOL path) | true after 08:37Z restart |
| `token_raw` | present | present |
| `pool_quote_amount` / `lp_fee` | stripped by `stored_trade` | stripped |
| `feed` | null on PumpSwap slim row; bonding has `helius_getblock` | `public_rpc_logs` |
| decimals | `price = quote/(base*1000)` → 6dp token, 9dp quote | same |

Not a USDC-as-SOL or decimals bug. `print_from_trade_row` does apply `pumpswap_post_trade_reserves` (the +621 pre-trade skip is not what fired here).

Synthetic clock (`LagDraw`): `t_recv_ms = block_time*1000 + max(0, independent live lag) + 272`. One draw **per row**, so two `BuyEvent`s in the same signature get different recv times. Sort key is `(t_recv_ms, slot, event_index)` — `event_index` is per-tx, not block order.

## The three (actually: the family of) unfillable trades

First wSOL PumpSwap print on the hot mints is always the same geometry: pre-trade **67.405853768 SOL / 206_900_000_000_000 raw**, ~1 SOL `Buy`/`BuyExactQuoteIn`, post-trade ~68.39 SOL / 204.5e12. Same signature then has a second buy of **2k–11k SOL** that drains the pool in that transaction.

Public RPC `getTransaction` (confirmed): every signature is **one tx, two Buy instructions**.

| mint | sig (both buys) | slot | 1st buy SOL | 2nd buy SOL | paper raw if fill after 1st / exit +30s |
| --- | --- | ---: | ---: | ---: | ---: |
| `qhyWQAfBWW8p…pump` | `3eJ5FYtdzW318BYD…` | 450235843 | 1.000 | 10997.45 | +911 (mixed +775) |
| `ADAjxqfpPxyv…pump` | `537dZG7kseHuGuFT…` | 450277269 | 0.988 | 4940.71 | +201 (mixed +171) |
| `HZGUvGnxGBoo…pump` | `24yG8yQoEYp2YWhJ…` | 450220725 | 1.000 | 4396.38 | +157 (mixed +133) |
| `EE4A72cmNv8k…pump` | `2zvHGv4rMvFdt6xB…` | 450256164 | 1.000 | 3998.44 | +131 (mixed +111) |
| `HdTCFmaynjYw…pump` | `4Cabceg68DytmFwj…` | 450276181 | 0.988 | 2436.76 | +51 (mixed +44) |
| `2MFqe3ApnnBG…pump` | `2PUWg48kasMmcjSR…` | 450275627 | 0.988 | 1747.04 | +27 (mixed +23) |

On-chain logs (example `3eJ5FY…`): `InitializeAccount3`, **Buy**, TransferChecked, **Buy**, TransferChecked. Slot and `blockTime` match the backfill row.

Fill after the second buy: ~−0.002 SOL (dust). Fill after the first buy and exit on any later print that already has the drained reserves: hundreds of SOL on a 0.05 size. That is not a 30s market move; the second buy already landed in the same signature.

Which of those becomes the +114.7 headline depends on the lag draws (seed print first, extra entry lag still before any post-drain print so slippage vs the migrate-print ref does not miss). Slippage at 15% is why the +775 path is not guaranteed into n; the paths that do land are still unfillable.

## Root cause

Same class as the +621 SOL migrate bug: the paper book treats an **intra-transaction** PumpSwap state as a pool a later send could buy. There, `BuyEvent` reserves were pre-trade and were not advanced. Here, post-trade **is** applied to the ~1 SOL inner buy, but the **second inner buy in the same tx** is a separate tape row with its own synthetic recv, so the sim can buy the 67 SOL pool and sell the 2k–11k SOL pool.

Live: those two buys are atomic. A decision at the first print’s receive time cannot insert 0.05 SOL between them.

## Recommended fix (do not apply in this pass)

1. One fillable state per `signature`: apply every event in the tx, then expose only the post-last-event reserves.
2. Stamp synthetic `t_recv_ms` once per signature (or once per slot), not per event. Order remaining ties by block transaction index, then `event_index`.
3. Optional gate: if two PumpSwap prints share `signature` and `slot`, do not allow an entry time strictly between their event indices.
4. Keep `quote_is_wsol is True` and post-trade advance; those are not the bug.
