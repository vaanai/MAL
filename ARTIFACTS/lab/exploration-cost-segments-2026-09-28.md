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

71 sealed hours, `2026-09-19T01` through `2026-09-21T23` inclusive, 3 UTC days (`2026-09-19`, `2026-09-20`, `2026-09-21`). 3 worker processes under `nice -n 19`, wall time 516.8s. **12,727 rows** written (5 entry variants x up to ~3,246 migrations). At the frozen trigger (offset 0), miss rate is **69.2%** (2,245 of 3,246) — higher than the fee audit's 59.65% because this run uses the 0.5 SOL primary size (bigger fills hit the slippage cap more often) on a different 3-day window, not the same cell.

## 4. Fee-by-segment table

### 4a. At the frozen trigger itself (offset 0, slot+1, direct): almost no tier to select on

| Entry fee tier | n filled | mean gross % | mean fee % | mean net flat % | CI-lo flat % | mean net pressure % | CI-lo pressure % | days+ (flat/pressure) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 12,500 ppm (1.25%, mcap < 420 SOL) | 987 (98.6%) | +2.373 | 2.482 | −0.277 | −2.480 | −0.477 | −1.688 | 1/3, 1/3 |
| 12,000 ppm (1.20%, mcap 420–1,470 SOL) | 14 (1.4%) | −4.288 | 2.339 | −5.818 | −25.988 | −2.315 | −10.044 | 2/3, 2/3 |

At the trigger, 98.6% of fills land in the single top fee tier — the same ≈1.25% the fee audit already measured. **There is essentially no fee-tier lever available at the frozen decision point itself**; the trigger fires within ~1 slot of graduation, before the pool has had time to move mcap into a cheaper tier. The 14-row 12,000ppm bucket is too small to read (CI-lo −25.99%) and is reported for completeness only, not as a finding.

### 4b. Waiting for a cheaper tier (migrate+N minutes): the tier mix opens up fast

| Offset | n filled | top tier (12,000–12,500ppm) | mid (9,000–11,500ppm) | cheap (< 9,000ppm) | cheap tier share |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 min (frozen trigger) | 1,001 | 1,001 (100.0%) | 0 | 0 | 0.0% |
| 1 min | 3,171 | 2,111 (66.6%) | 806 (25.4%) | 254 (8.0%) | 8.0% |
| 5 min | 3,139 | 2,159 (68.8%) | 741 (23.6%) | 239 (7.6%) | 7.6% |
| 15 min | 2,798 | 2,119 (75.7%) | 490 (17.5%) | 189 (6.8%) | 6.8% |
| 60 min | 373 | 326 (87.4%) | 32 (8.6%) | 15 (4.0%) | 4.0% |

By 1 minute after migration, **8% of fills are already paying under 0.90%** venue fee instead of 1.25% — some as low as 0.30% (111 of 3,171 fills at exactly 3,000ppm). The mechanism is real and mechanical: `pumpswap_sol_fee_ppm` steps down every time mcap crosses a threshold (SS1), and mcap moves fast in the first minutes after a migration.

### 4c. Fee saved vs. gross paid, by band and offset

| Offset | Band | n | mean gross % | mean fee % | mean net flat % | CI-lo flat % | mean net pressure % | CI-lo pressure % | days+ (flat/press) |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 0 | top | 1,001 | +2.280 | 2.480 | −0.355 | −2.541 | −0.502 | −1.709 | 1/3, 1/3 |
| 1 | top | 2,111 | −9.073 | 2.355 | −9.899 | −11.236 | −8.381 | −9.420 | 0/3, 0/3 |
| 1 | mid | 806 | −5.633 | 1.997 | −6.670 | −10.914 | −5.573 | −8.765 | 1/3, 1/3 |
| 1 | cheap | 254 | −7.575 | 0.909 | −7.396 | −11.214 | −6.075 | −9.426 | 0/3, 0/3 |
| 5 | top | 2,159 | −12.254 | 2.315 | −12.568 | −13.865 | −11.070 | −12.127 | 0/3, 0/3 |
| 5 | mid | 741 | −16.086 | 1.876 | −15.453 | −19.710 | −11.315 | −14.722 | 0/3, 0/3 |
| 5 | cheap | 239 | −7.414 | 0.922 | −7.270 | −11.125 | −5.684 | −8.736 | 0/3, 0/3 |
| 15 | top | 2,119 | +4.897 | 2.532 | +1.825 | −5.034 | +2.092 | −4.487 | 2/3, 2/3 |
| 15 | mid | 490 | −9.796 | 1.967 | −10.183 | −13.920 | −7.503 | −10.419 | 0/3, 0/3 |
| 15 | cheap | 189 | −3.669 | 0.927 | −4.091 | −8.904 | −3.432 | −7.376 | 1/3, 1/3 |
| 60 | top | 326 | −7.151 | 2.388 | −8.293 | −10.786 | −7.562 | −9.834 | 0/3, 0/3 |
| 60 | mid | 32 | −33.303 | 1.711 | −29.946 | −44.791 | −21.606 | −32.483 | 0/3, 0/3 |
| 60 | cheap | 15 | −67.883 | 0.848 | −58.607 | −71.945 | −46.883 | −58.235 | 1/3, 1/3 |

`fee %` is the true round-trip venue cost (`gross − net0`, size-relative — includes rent on a failed close), not a doubled entry-tier estimate. Bands: top = 12,000–12,500ppm (1.20–1.25%), mid = 9,000–11,500ppm (0.90–1.15%), cheap = below 9,000ppm (< 0.90%). All numbers use the frozen fail-mix code (`mixed_net`, `FailCurve` at `PRESSURE_INTERCEPT`, `FLAT_FAIL = 0.15`), not a re-derivation. `n_days = 3` throughout (this window has only 3 UTC days — well short of the promotion gate's 5-day minimum, consistent with SS7: this is exploration, not a gate-eligible book).

## 5. Does gross hold up in the cheaper segments?

**Mostly no.** The cheap and mid bands save 1.4–1.7 percentage points of fee versus the top band (fee % ≈ 0.85–0.93% vs ≈ 2.32–2.53%) at every offset — the mechanism in SS1/SS4b is confirmed. But gross collapses far harder than that at 1 and 5 minutes: every band at offset 1 and 5 has negative mean gross, and the cheap/mid bands are not better than top — at offset 5, mid is *worse* (gross −16.086%) than top (−12.254%). The fee saving (≈1.9pp) is swamped by an adverse-selection cost that is an order of magnitude larger. Offset 60 is worse again across all bands, worst in the cheap band (gross −67.883%, n=15 — noisy, but directionally consistent with staleness, not with a discount).

The one exception is **offset 15, top band**: gross +4.897%, net flat +1.825% (CI-lo −5.034%, still crosses zero), net pressure +2.092% (CI-lo −4.487%), 2 of 3 days positive under both fail models. This is the only band/offset combination in this table with positive mean net under both fail models. It does not clear the promotion gate (n=2,119 trades but only 3 UTC days, not the required 5; CI lower bound is negative on both fail models) and is not being claimed as an edge — it is a single cell out of 15 read here, the same winner's-curse shape already on record for the frozen cell (`LAB_STATE.md` SS"First positive run"). It is flagged only as worth a dedicated, larger, pre-registered test.

**Reading:** the frozen cell's edge is concentrated at the migrate instant, not in the fee it pays there. Waiting to reach a cheaper PumpSwap tier means buying into a pool that has already run — on this window, that costs far more in adverse price selection than the tier saves in fee, except possibly in a narrow window around 15 minutes that needs its own test before being trusted.

## 6. Candidate selection rules for a future pre-registered test

Each rule below is knowable at decision time T from state already read at T (no future information), per the task's requirement. None has cleared, or been tested against, the promotion gate.

1. **Entry-tier filter at the frozen trigger.** At slot+1/direct, the (rare, 1.4% of fills here) trades that land in the 12,000ppm tier instead of 12,500ppm — meaning mcap already crossed 420 SOL before the buy landed — read worse in this sample (mean net flat −5.818% vs −0.277%, n=14 vs 987). Too small to act on from this window alone, but cheap to log (it's just `buy.venue_fee_ppm`, already computed by the existing frozen scorer) and worth carrying as a covariate into a much larger pre-registered read before deciding whether "already ran before you landed" is informative or noise.
2. **Migrate+15-minute entry, top fee tier only.** The single cell in SS5 with positive mean net under both fail models. Explicitly weak (CI lower bound negative both ways, only 3 UTC days, single best-of-15 cell) — proposed only as a pre-registration candidate with a materially larger holdout (more days, ideally a different window than the one that surfaced it), not as a result.
3. **Tier-conditioned position sizing instead of tier-conditioned timing.** Since blind time delay pays the fee saving but loses more to adverse selection, a sizing rule — full size only in the top tier (mcap < 420 SOL, i.e., still at the un-run price), reduced size in mid/cheap tiers reached at the same decision time — could in principle capture the rare cheap-tier fill without taking on the systematic adverse-selection cost of *waiting* for one. Untested here; the frozen trigger essentially never reaches a cheap tier (SS4a), so this rule only bites on a decision point that itself waits, which is exactly the mechanism SS5 shows losing money — it would need to be paired with a signal that predicts *which* delayed entries avoid the adverse-selection cost, not adopted on tier alone.

## 7. What this is not

- Not a promotion claim. No cell here has cleared, or been tested against, the promotion gate.
- Not a retune of the frozen migrate cell — the frozen cell's own parameters (locked 2026-09-27T13:06:36Z) are unchanged; this file only segments its population and offers alternate entry clocks for future pre-registration.
- The migrate+N-minute entries are a looser approximation (SS2) and their gross/net numbers should not be read as an achievable execution without a proper pre-registration of their own fill/latency model.
