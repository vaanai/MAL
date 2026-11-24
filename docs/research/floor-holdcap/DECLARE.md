# FLOOR hold-cap screen: DECLARE (written before any run)

T1. PAPER only. In-sample research. Not a gate read. Nothing here says any book makes money.

Origin: the owner's Duet research channel suggested that shorter holds may earn more per SOL of capital while FLOOR is capital-short.

## 1. Books

Three dry books, copies of manifest `/data2/mal/scratch/floor-open-sim/manifests/A.json` (osb-a: host lane, stakes 0.1/0.25/0.5/1.0, send_lamports 160000, seed_from_hour 2026-11-03T03,
tape_dir `.../keep`, lane gap_lookback_s 21600). Each copy changes only: book_id, title, out_root (own dir, 0700, `setfacl -b -k`), start_hour 2026-11-03T03, and `rule_overrides.HOLD_S`.
Each runs `osb_follow.py --once --max-hours 369`, which ends at 2026-11-18T12:00Z. No code change to the simulator: `osb_follow.py` already accepts `rule_overrides` `HOLD_S` (see `apply_overrides`).

| book_id | HOLD_S override | hold cap |
| --- | --- | --- |
| holdcap-control24 | absent | 24 h (as live) |
| holdcap-h12 | 43200 | 12 h |
| holdcap-h6 | 21600 | 6 h |

`HOLD_S` also moves `HOLD_MS` and the lane host's `BLOCK_MS` (hold + cooldown + margin), so per-pool re-entry comes sooner as well. A variant therefore differs from the control in two ways, the exit time and the re-entry block; the screen does not separate them.

## 2. Hours

In-sample only: FLOOR's in-sample hours and osb-a's board hours, [2026-11-03T03:00Z, 2026-11-18T12:00Z). They end before the brake-study E2 held-out window, which starts at 2026-11-18T12:00Z and is never touched. The reader prices with `--to-ms` 2026-11-18T12:00Z.

Counted trades: entry in [2026-11-03T03:00Z, 2026-11-17T12:00Z), so every counted trade can reach its 24 h exit before the pricing end. Booking day is the exit UTC day, as the board. Complete days (for test iii): exit days 2026-11-04 to 2026-11-16 inclusive.

## 3. Pricing

`scripts/research/floor_holdcap_read.py` prices each ledger with the pinned board pricer: `floor_nov_pnl` (rule total, latency 1p9, send 160000, ledger-send 160000) called as `osb_board.price_snapshot` does, trades loaded with `osb_board.book_trades`. Legs: `flat_1p9` (flat 15% fail) and `press_1p9` (pressure-fail, slope scale 1). No cost is re-implemented. "Strict 1p9 leg" means the strict (gap-excluding, `gap_exclusion` on, as osb-a) book at latency 1p9.

Per book x stake x leg the reader prints n, total net, mean per trade, ex-top-3, mean hold hours, SOL-days of capital used (sum of stake x hold days), net per SOL-day, and per-day totals.

## 4. Capital-constrained portfolio sim (`--wallet-sim`)

The counted trade list at the 0.25 cell, in entry-time order, at stake 0.40 with FLOOR's live limits. Net per trade is scaled linearly from the 0.25 cell to 0.40: linear scale; impact not modelled. An entry is taken only if capital allows; capital returns at exit (an exit at or before an entry's instant frees its capital first).

- Budget, full wipe: realized - sum over open (stake + R) - (new stake + R) > -budget. R = `LANE_MIN_FEE_RESERVE` (3,860,000 lamports), budget = `LANE_BUDGET_MAX_LAMPORTS` (5.00 SOL), both read from `tools/floor_lane_executor.py`.
- Wallet floor 0.75 SOL: start_free + realized - sum open (stake + R) - (new stake + R) >= floor, start_free default 2.00 SOL (parameter).
- Max open: `LANE_MAX_OPEN_DEFAULT` (8) from the executor.

Output per variant and leg: realized SOL/day (over the counted window, 14.375 days), entries taken, entries skipped for capital (with the reason split), net per SOL-day.

## 5. Kill/adopt line (fixed now)

A shorter cap "wins clearly" only if, at the deciding cell (stake 0.25, strict 1p9 leg) under BOTH the flat 15% and the pressure legs, it holds all three:

1. Capital-constrained realized SOL/day (section 4) is at least 1.3x the control's. If the control's figure is at or below 0, this reads: the variant is above 0 and at least control + 0.3 x |control|.
2. It stays above the control after dropping each variant's top 3 trades (ex-top-3 total, 0.25 cell; an absent value, fewer than 4 trades, fails).
3. A majority of complete days (section 2) are at or above the control's per-day total.

Otherwise the verdict is "no clear win". The reader prints this verdict per variant; there is no other adopt path. Even a "wins clearly" is an in-sample screen result only; any live step needs its own registration.

## 6. Disclosures

- In-sample. Not a gate read; no promotion claim.
- Best of 3 variants (control plus two caps), on top of the board's own count of book rows; treat any figure as shrunk (winner's curse).
- `BLOCK_MS` also shortens re-entry, so the effect is exit timing plus re-entry.
- Both fail legs are reported; the verdict needs both.
- The wallet sim scales linearly from the 0.25 cell; impact at 0.40 is not modelled.
- The seeds and tape are the osb-a ones; the books are dry copies, no live fills enter.
- The E2 window (from 2026-11-18T12:00Z) is untouched.

## 7. Run

`scripts/research/floor_holdcap_run.sh` as a MiScusi job on mal-research-0, by the manager. Stdout carries only manifest sha256, hour counts, decision md5s and the reader's summary lines.
