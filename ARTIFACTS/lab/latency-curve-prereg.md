---
cursor:
  subagentId: "bc-62f43346-aca0-54a5-86d6-bc6dbeacf527"
---

# Latency curve — pre-registration (before any score)

Paper only. No live orders, no keys, no threshold search. Written before `tools/latency_curve.py` was run on the tape. Constants below are the scoring rules. They are not fit to this run's PnL.

## Question

How does expected paper PnL change as the fill lands one to four slots after the trigger, versus today's measured receive? The spend ladder needs that curve before any dollar past phase 1.

## Clock (locked from the host, not from PnL)

Read 2026-09-27 from `/var/lib/mal/paper/forward-paper/latency.json` (`forward_paper_latency_v1`), before this score:

| Piece | Value | n |
| --- | ---: | ---: |
| chain→receive p50 | 1,349 ms | 280,894 |
| recv→decision hop p50 | 38 ms | 155 |
| **Measured entry** | **1,387 ms** (sum of those two p50s) | |
| applied_entry p50 (not used; median of a sum) | 1,391 ms | 155 |

`decision_to_send` p50 was 0. The measured column is trigger chain time + 1,387 ms for every row. It is not a fresh lag draw and not `applied_entry`.

Backfill rows have null `t_recv_ms`. Chain time is `block_time` in unix seconds. Slot bounds use the `slot` field, not that second.

## Latency grid

Trigger slot is the slot of the trigger event (create slot, or the migration print's slot).

| Column | Landing |
| --- | --- |
| slot+1, +2, +3, +4 | `trigger_slot + k` |
| measured | chain time + 1,387 ms |

Two bounds on every slot column:

- **start (optimistic):** pool after every fillable print with `slot < target`, and before any trade in the target slot.
- **end (conservative):** pool after every fillable print with `slot <= target`.

The measured column is one clock, not a start/end pair. `block_time` is one-second, so that clock includes every print whose block second is `<= trigger_second + 1.387s`.

Empty target slot: both bounds see the same pool (no trades in the slot).

### Ordering (PR #97, unchanged)

Fill order is `(t_recv, slot, tx_index, event_index)`. On backfill, `t_recv` for this order is `block_time * 1000` (one clock per block, no synthetic lag draw on the slot grid). A missing `tx_index` is the read order of that signature inside the slot (`TxOrder` in `paper_price_path.py`). One signature is one fill state: reserves after its last inner event, visible only once every inner event is applied (`collapse_fillable`). A paper fill cannot land between two instructions of the same transaction.

PumpSwap rows are moved to post-trade reserves with `print_from_trade_row` before that collapse. Bonding reserves stay post-trade as stored.

Create-payload reserves are an anchor at the create slot with `event_index = -1` only so they lose to a real print of the same signature. They are not a second trade.

## What is scored

Attempts stay in n when the entry misses (slippage, no liquidity, curve complete, no state). A miss costs one priority fee. Censored exits (tape does not reach the exit time) stay out of n. One attempt per mint per strategy.

Slippage cap is the existing 15% (`DEFAULT_SLIPPAGE_CAP`). Not retuned. Create strategies use the create-payload price as the reference. Migrate uses the migration trigger print's price. A create reference at migration would reject the book for price drift, which is not the frozen rule.

The reader keeps 32 minutes after the trigger so a 30-minute exit plus a slot offset is inside the tape. That buffer is not an exit rule. If the holdout ends before that buffer, the attempt is censored.

Exit timer starts at the landing. `hold_30s` sells on the last fillable print with chain time `<= landing_chain_ms + 30_000`. `tp50_sl30` and `ladder_2x_t30` use the existing parameters (`tp=0.50`, `sl=0.30`, scale at 2x of half, trail 30%, hard stop 30%, 30 minute cap). A stop or scale print at slot P is filled with the same slot offset: exit slot `P+k` and the same start/end bound. The measured column fills that exit at the trigger print's chain time + 1,387 ms. `SLOT_MS = 400` is used only to test whether the tape has reached an offset exit. It does not set the price.

Our paper buy is re-injected with `reserves_with_our_buy` on later prints. Quotes are `quote_buy` / `quote_sell`.

### Strategies

| Id | Rule |
| --- | --- |
| `buy_all` | Buy every create. Trigger = create slot. |
| `skip_fresh_rug` | Same entries, drop a create only when a funding feature is visible at the landing time and it says skip. Unknown does not skip. |
| `migrate` | Buy every migration (first PumpSwap print strictly after a bonding print). Trigger = that print's slot. This is the frozen migrate entry, not a new filter. |

Skip rule, existing definitions only (`funding_graph.py`, frozen before the 2026-09-25 score):

- Skip if `f_creator_fresh_wallet == 1` (wallet age `< 3600s` and the funder is not an exchange).
- Skip if `f_funder_prior_scored >= 2` and `f_funder_prior_rug_frac >= 0.50` (`RUG_VETO_MIN_SCORED`, `RUG_VETO_RUG_FRAC`).
- A row is visible only when `first_seen_ms` is at or before the landing. Same-funder and loop arms are not this rule.

No other first-second rule. The only published first-second numeric gates before today are sniper-share 0.25 and 0.50 at T+1s in `signal-scan.md` (2026-09-25). Those books were n≤10 and 0 wins. They are not reopened.

### Frozen model books

These five entry rules stay unchanged (no refit, no new threshold): `laya_0.6` (score ≥ 0.6), `laya_0.7` (score ≥ 0.7), `migrate_hold_30s`, `t30_top1_hold_30s` (causal top 1% at +30s), `buyers8_top5_ladder_2x` (causal top 5% at 8 buyers, barrier model).

`migrate_hold_30s` and `migrate_tp50_sl30` are the `migrate` entry above with `hold_30s` and `tp50_sl30`. The three model books need the full feature builder, which has peaked near 10 GiB on this host. This job is capped at 8 GiB. They are scored on the slot grid only if that pass stays inside the cap without refitting and without a feature approximation. If it does not, those rows are reported as not scored. They are not replaced by an all-create slice.

### Exits

`hold_30s`, `tp50_sl30`, `ladder_2x_t30`. Same three on `buy_all`, `skip_fresh_rug`, and `migrate`.

## Fees and fails

Gross is price edge before portal, venue, and priority: `net + fee_stack`, with priority cancelled, matching `fee_stack_lamports`. Misses contribute 0 gross. Stuck exits keep the existing identity (gross `−net_in` when the sell cannot land).

Net is after fees. Priority is a fixed lamports cost per side (`1 + sell_legs` on a send, 1 on a miss) and is applied after the quote, so 0.001 / 0.0003 / 0.0001 SOL per side do not get a second curve simulation.

| Axis | Values |
| --- | --- |
| Route | direct: portal 0, venue 1.25%/side. portal: 0.5% then 1.25%/side |
| Size | 0.05 SOL and 0.5 SOL |
| Priority | 0.001, 0.0003, 0.0001 SOL per side |
| Flat fail | 15%. A send is `(1−0.15)·net + 0.15·(−priority)`. Misses stay at one priority. |
| Pressure | scale 1. Slopes `B_SLOT=0.8`, `B_SOL=0.35` from `paper_fail_pressure.py`. Intercept fit once so mean p on **buy_all / hold_30s / measured** sends equals 0.289. That one curve is applied to every cell. Not refit per cell. |

Headline compact table: **end-of-slot bound, direct, 0.05 SOL, priority 0.001, flat 15%**, cell = net mean % (gross mean %). The start-of-slot bound is the other number in the same cell. Other fee rows are in the JSON. A cell is positive only if net mean is > 0 under **both** fail models.

## Data

- **Holdout:** sealed backfill hours whose `block_time_end` is at or before `2026-09-25T06:58:00Z`, same rule as `sealed_holdout_hours`. Partial UTC days are scored and labeled partial. They are not a complete-day pool.
- **Dev:** live tape 2026-09-25 through 2026-09-27, labeled dev. Creates in the void window `2026-09-25T19:00:00Z` through `2026-09-27T06:58:12Z` are excluded. Dev is not the headline.
- Non-wSOL prints are dropped the same way as the tape scoreboard.

## Job limits

Oracle only (`ssh.tradervaan.com`). Not the Frankfurt host. `systemd-run` with MemoryMax 8G, CPUQuota 150%, Nice 19, IO scheduling idle. Read `mal-forward-paper` `lag_ms` before start and about every 10 minutes. Sleep while `lag_ms > 5000`. Do not stop other units. Do not start the LAYA timer.

## What would change the conclusion

The curve is flat if gross from slot+4 to slot+1, on holdout `buy_all` / `hold_30s` / end bound, moves by less than the fee floor that phase 1 already cannot pay (~2.5% venue-only round trip at 0.5 SOL, ~7.5% portal+priority at 0.05 SOL). A positive cell still has to clear both fail models. n, UTC days, bootstrap 90% CI lower bound of mean (1,000 draws, seed 1, same cluster bootstrap as `book_stats`), and total after dropping the top 3 trades are reported for any positive cell and for the best cell.
