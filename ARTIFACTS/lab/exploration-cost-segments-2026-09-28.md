---
schema: exploration_cost_segments_v1
---

# Exploration: fee tier / cost-aware selection (2026-09-28)

**EXPLORATION ONLY.** This is lane C of a three-lane cost sweep. Nothing here is a promotion claim, nothing here retunes the frozen migrate cell (locked 2026-09-27T13:06:36Z), and no cell in this file has been pre-registered. It produces candidate selection rules for a future `EXP-###` pre-registration only.

## 0. Question

The fee audit ([ARTIFACTS/lab/fee-audit-2026-09-27.md](fee-audit-2026-09-27.md)) puts venue fees at **2.511%** of size on the frozen cell's realized fills — about 85-90% of total cost — while tuning priority, size or latency only moves the total by ~0.1pp. But venue fee is not one number: it depends on venue (bonding vs PumpSwap) and, on PumpSwap, on a market-cap tier. So which coins and which phase get traded may decide most of the cost. This note measures that, on the exploration pool only.

## 1. The fee schedule the simulator uses (as written in code)

Source: `tools/paper_curve_math.py`. All fees are basis points expressed as parts-per-million (ppm), applied via `after_fee()` (`tools/paper_curve_math.py:230-236`).

### Bonding curve (pre-migration)

One flat rate at every market cap:

```
BONDING_FEE_PPM = 12_500   # 1.25%, tools/paper_curve_math.py:30
```

`venue_fee_ppm("pump_bonding", mcap)` (`tools/paper_curve_math.py:130-135`) always returns `BONDING_FEE_PPM`, regardless of `mcap`. Buy and sell pay the same 1.25%.

### PumpSwap, canonical pool (post-migration)

A 25-step schedule on SOL market cap, inclusive lower bound of each tier, total ppm = creator + protocol + LP (`tools/paper_curve_math.py:50-76`, `pumpswap_sol_fee_ppm()` at `tools/paper_curve_math.py:108-116`):

| Market cap (SOL), inclusive lower bound | Total ppm | Total % |
| ---: | ---: | ---: |
| 0 | 12,500 | 1.250% |
| 420 | 12,000 | 1.200% |
| 1,470 | 11,500 | 1.150% |
| 2,460 | 11,000 | 1.100% |
| 3,440 | 10,500 | 1.050% |
| 4,420 | 10,000 | 1.000% |
| 9,820 | 9,500 | 0.950% |
| 14,740 | 9,000 | 0.900% |
| 19,650 | 8,500 | 0.850% |
| 24,560 | 8,000 | 0.800% |
| 29,470 | 7,500 | 0.750% |
| 34,380 | 7,000 | 0.700% |
| 39,300 | 6,500 | 0.650% |
| 44,210 | 6,000 | 0.600% |
| 49,120 | 5,500 | 0.550% |
| 54,030 | 5,250 | 0.525% |
| 58,940 | 5,000 | 0.500% |
| 63,860 | 4,750 | 0.475% |
| 68,770 | 4,500 | 0.450% |
| 73,681 | 4,250 | 0.425% |
| 78,590 | 4,000 | 0.400% |
| 83,500 | 3,750 | 0.375% |
| 88,400 | 3,500 | 0.350% |
| 93,330 | 3,250 | 0.325% |
| 98,240 | 3,000 | 0.300% |

Per-side split (creator/protocol/LP, `tools/paper_curve_math.py:78-105`, `pumpswap_sol_fee_split()` at `tools/paper_curve_math.py:118-127`) — first two rows: 0 SOL is creator 3,000 + protocol 9,300 + LP 200 = 12,500; 420 SOL is creator 9,500 + protocol 500 + LP 2,000 = 12,000. LP stays in the pool; creator and protocol leave it.

`venue_fee_ppm("pumpswap", mcap)` (`tools/paper_curve_math.py:130-135`) dispatches to `pumpswap_sol_fee_ppm`. Non-canonical PumpSwap pools use a flat 0.30% instead (not this table; the docstring at `tools/paper_curve_math.py:47-49` and the fee audit both note migrated pump.fun coins are always canonical).

### Portal (not used in this exploration — direct route only)

`PORTAL_FEE_PPM = 5_000` (0.5%), applied sequentially before the venue fee, each side (`tools/paper_curve_math.py:31`, `quote_buy`/`quote_sell` at `tools/paper_curve_math.py:262-326`). This exploration uses `direct` only (portal fee 0), matching the frozen cell.

### Is the code a simplification of the real on-chain schedule?

Per the fee audit's live on-chain read (slot 450,986,715, 2026-09-27T11:42:45Z, `ARTIFACTS/lab/fee-audit-2026-09-27.md` SS2):

- **Bonding and the first two PumpSwap tiers match chain exactly, 0.00 points.** A buy at graduation (410.88 SOL mcap) pays 125bps on both chain and code; a sell at +50% (616.32 SOL) pays 120bps on both.
- **Known simplification, immaterial here:** above 54,030 SOL mcap, five on-chain tiers store an integer creator basis point where the code (and the 20-May-2026 docs page it was read from) uses a half point (chain 28/23/18/13/8 vs code 27.5/22.5/17.5/12.5/7.5) — about 0.5bps/side, ~0.01pp per round trip, and only when both legs sit in one of those five tiers. As shown below (SS3), this exploration's population sits almost entirely under 54,030 SOL mcap, so this simplification does not materially affect the numbers in this file.
- The simulator also treats the whole canonical venue fee as leaving the pool on a sell, which is "slightly less quote than an LP-stays buy" per the `pumpswap_pool_quote_delta` docstring (`tools/paper_curve_math.py:139-159`) — a reserve-accounting nuance, not a fee-rate error; it does not change which ppm a trade is charged.

## 2. Method

- **Hard data fence:** fast-box sealed hours `2026-09-19T01` through `2026-09-21T23` inclusive only — the "fast pre-cut" block, owned by the exploration pool per [docs/HOLDOUT_LEDGER.md](../../docs/HOLDOUT_LEDGER.md). Never older than `2026-09-19T01` (EXP-009's holdout cut, `EXP/EXP-009-migrate-creator-gate-prereg.md` Amendment 3). Enforced by an explicit whitelist and an `assert` on every hour opened, in `tools/explore_cost_segments.py` (`explore_hours()`, `stream_chunk()`). Hours actually read are printed to the run log and reproduced below (SS "Run").
- **Base execution**, matching the frozen cell: migrate trigger, slot+1 start, direct route, 0.0005 SOL/side priority (the frozen OOS slot-+1 p75), 0.5 SOL size, tp50_sl30 exit.
- **Fill/fee/fail code reuse**, not re-derived: `_try_buy`, `_one_sell_close`, `_pressure`, `_state_index`, `_slot_time`, `_state_at`, `_delayed`, `mixed_net`, `FailCurve`/`Pressure` (`tools/latency_curve.py`); `venue_fee_ppm`, `market_cap_sol`, `reserves_with_our_buy`, `spot_sol_per_ui` (`tools/paper_curve_math.py`). The only new code is orchestration: `tools/explore_cost_segments.py` reads out the market cap at entry and exit (which the frozen scorer computes internally but never returns) and offers alternate entry clocks. Its TP/SL trigger scan is a literal copy of `_tpsl` (`tools/latency_curve.py:325-369`), kept separate only so the exit `TapePrint` can be read for its market cap — no fee formula is redefined.
- **Fail models:** flat 15% (`FLAT_FAIL`) and pressure at declared scale 1 (`PRESSURE_INTERCEPT = -1.4548727851312098`, the same fit as the latency curve and the frozen OOS rescore), both via `mixed_net`, exactly as `migrate_direct_oos.py` computes them.
- **Graduated-swing entries:** for the same set of migrations, additional entries at migrate+N minutes (N = 1, 5, 15, 60), state read via `_state_at` at wall-clock `mig_ms + N*60_000`, then the same tp50_sl30 exit. These entries do **not** apply the frozen cell's slot+1 execution delay on the buy (only on the sell) and do not carry a reference-price slippage cap — disclosed here, not hidden: they are a looser, exploratory approximation of "buy N minutes after migration," not a second frozen cell.
- **Parallelism:** `multiprocessing`, 3 worker processes (`os.nice(19)` inside each worker, invoked additionally under a `nice -n 19` wrapper), each processing an independent contiguous ~24h chunk of the 71 sealed hours with `admit_on_bond=True` so no chunk depends on creation history from an earlier chunk. This trades a small undercount at chunk boundaries (a migration whose bonding history started in a different chunk than its migration hour is still caught — `admit_on_bond` re-admits any mint on its first in-chunk bonding print — but a mint's true creation time before the chunk start is not recovered) for full parallel independence. Disclosed, not hidden; this is exploration, not a frozen count.
- Lane A (exits) was running its own replay over the same hours concurrently; this run used 3 workers under `nice -n 19`, not the ~400% CPU a production backfill uses.

## 3. Run

(hours read, row counts, elapsed time — filled in from the actual run log)

## 4. Fee-by-segment table

(filled in from `explore-cost-segments-2026-09-28.json`)

## 5. Does gross hold up in the cheaper segments?

(filled in)

## 6. Candidate selection rules for a future pre-registered test

(filled in)

## 7. What this is not

- Not a promotion claim. No cell here has cleared, or been tested against, the promotion gate.
- Not a retune of the frozen migrate cell — the frozen cell's own parameters (locked 2026-09-27T13:06:36Z) are unchanged; this file only segments its population and offers alternate entry clocks for future pre-registration.
- The migrate+N-minute entries are a looser approximation (SS2) and their gross/net numbers should not be read as an achievable execution without a proper pre-registration of their own fill/latency model.
