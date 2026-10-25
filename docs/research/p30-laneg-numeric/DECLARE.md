# P30 lane-G numeric rules: DECLARE (pre-registered before any pricing)

Written 2026-10-25 by lane P30 (worker). **Exploration only. No edge claim of any kind. No book has cleared the promotion gate. Paper only.**
Nothing here says any rule makes money; a result is "a cell survived a screen" at most, and the first positive cell of 48 is a winner's-curse candidate by construction.

## 1. Question

Is there ANY simple numeric entry rule on HV4 lane-G cards (HV4/HV6 GEN universe: established PumpSwap pools, age 2 h to 7 d, real quote >= 30 SOL, active) that is
positive after MAL costs at 1.9 s latency, judged ex-top-3 and ex-best-day, with an honest selection correction over the 48 cells tried? A positive answer would be a stronger
numeric baseline for the Haiku arms (HV4 lane G, HV6 GEN); a negative answer is the expected outcome (HV6 DESIGN section 1 priors: every measured universe loses before selection).

## 2. Seals and data (every line obeyed)

* Source of seal text: /data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md (HANDOFF section 2 at 12282a82).
* Reads only: `/data/mal/clean-view/explore-0814` (train) and `/data/mal/clean-view/fresh-0903` (validation), hour files of 2026-08-14T12..08-28T12 and 2026-09-03T12..09-09T12.
  **No hour >= 2026-10-25T00Z, no hour in [2026-10-02T10, 2026-10-16T01) (D2/D3), no arena tape, no forward-1016, no /data/mal/meta-capture/, no HV4 / Q50 / HV6 / DBC output of any kind.**
  No file of HV4, HV6, Q50, DBC, LaunchLab is opened for data. The kit's own path guards (`tools/arena_screen.py`: forbidden words, sealed hours, block roots) stay on.
* Ledger (docs/HOLDOUT_LEDGER.md, read 2026-10-25): `explore-0814` = "exploration pool" ("never a confirmation holdout"; many exploration reads, EXP-014 screen v2 and others;
  the DEC-014 m counts apply only to claims on the 27 non-P1 dates, and this lane makes no claim). `fresh-0903` = "exploration pool", spent by EXP-012's one read (2026-10-01, job #32) and
  moved to the exploration pool by the 2026-10-06 edit; it is NOT a confirmation holdout for anyone. So "validation" below is a second exploration block, **not** an out-of-sample gate block
  and not a DEC-014 family read. Even a pass there leaves the book far from the gate (both blocks are Aug/Sept, a different regime from October; fresh-0903 is spent).
* Walk-2 disclosure: nothing here uses forward-1016 or arena tape (walk-2 hours).

## 3. Universe (decision-time, causal, from print rows)

A pool is a candidate at a scan mark when ALL hold (HV4 lane-G filters, `tools/hv4_cards.py` s1; HV6 DESIGN section 2):
* PumpSwap, quote WSOL (kit loads only WSOL rows) and V in [17.5e9, 17.7e9] lamports (kit AM.2 universe, the boosted/canonical pools; V from `pool_v_0814.json`, then `pool_v_0909.json`).
  Consequence: pools without a known V are out; this is the arena engine's own universe (V~17.58 SOL added to the quote in every price, `arena_screen.legs` / `arena_stack`).
* Age since first PumpSwap print in the block in [2 h, 7 d]. **Pools whose first print is in the block's first hour are age-unknown and excluded** (the block cannot tell a new pool from
  an old one); in a gap (missing hours) a pool first seen after the gap looks young (counted, disclosed).
* Real quote reserve (post-trade, V not included) >= 30 SOL at the last print <= the mark; >= 20 prints and >= 8 unique signers (crc32 of trader) in the last 15 min; last print
  <= 120 s before the mark; gross volume last 60 min >= 10 SOL. The 3.0% round-trip impact veto at 0.25 SOL is implied by quote >= 30 SOL (+V 17.58) and is not coded.
* Not applied: HV4 chain-fact vetoes (no chain data in the block), wash-net and quote-ref vetoes. Disclosed: this universe is a superset of HV4's.

## 4. Scan, decision, entry (as the HV4 scanner, by minute)

* Scan marks: every minute mark M (hour start + 60 k). Features use only rows with block_time <= M (integer seconds). The decision slot = the pool's last print slot with block_time <= M.
  (HV4 scans at :02; using <= :00 data is 2 s more conservative; disclosed.)
* Entry = the kit's engine: decision at the 1.9 s deciding leg (1.3 s reported), first print >= landing + 1 slot, PumpSwap PRE-trade reserves + V on the landing print, 7-slot feed delay,
  E1 floor, 1.15 guard, fees per `tools/arena_paper.py` stack config (505,000 lamports per send, venue tiers +26.08 / -16 bps), rent 0 deciding (rent-charged reported). Stake 0.25 SOL deciding (1.0 SOL reported by the kit).
  The kit's imported functions (`arena_stack.mint_states / round_trip / book_fail_legs`) are used as-is; nothing is re-implemented.
* Throttle: at most one trigger per (entry rule, pool) per 30 min (HV4 setup throttle). The kit also holds one open position per (cell, pool).

## 5. Feature definitions (price = post-trade (q + V +- sol*fee-factor)/(base after) of a print, as `arena_screen.post_price`)

* `r15 = P(M)/P(M-15 min) - 1`, `r60 = P(M)/P(M-60 min) - 1`, P(x) = price after the last print with block_time <= x.
* `gross15, gross60` = buy+sell SOL in the window; `net15` = buy SOL - sell SOL in the last 15 min; `flow = net15/gross15`.
* `accel = 4*gross15/gross60`.

## 6. Grid: 24 entry rules x 2 exits = 48 cells (frozen here; no cell added, dropped or retuned after the first price)

Entry shape (6): `MOM` r15 >= +5%; `MOM2` r15 >= +10%; `PULL` r60 >= +10% and r15 <= -4%; `PULLD` r60 >= +10% and r15 <= -8%; `BNC` r60 <= -10% and r15 >= +3%; `KNIFE` r15 <= -8% and r60 <= 0.
Flow filter (2): `F0` none; `F1` flow >= +0.10.   Volume filter (2): `A0` none; `A1` accel >= 1.5.   Names `SHAPE-F?-A?` (e.g. `PULL-F1-A0`).
Exit (2), time-based exits from the decision with the kit's tp/sl on POST-trade prices of prints after landing: `X1` = HV4 X-arm style: stop -8% (sl 0.92), target +20% (tp 1.20), time 60 min;
`X2` = wider: stop -15% (sl 0.85), target +50% (tp 1.50), time 240 min. Cell name `<entry>|<exit>`. All cells `event_triggered=True` (the strict default).
Pre-set constants (not tuned): the thresholds above are round numbers fixed before any print was priced; the lane looks at no feature distribution beyond count-only trigger counts.

## 7. Statistics per cell (the kit's, both fail models, deciding leg 1.9 s / 0.25 SOL / rent 0, strand rule `total` decides, `sc` reported)

n, mean SOL/trade (and % of stake) under flat 15% fail AND pressure-fail slope 1 (intercept refit per cell, as the kit); CI90 lower bound of mean SOL/trade (1,000 bootstrap draws, seed 1,
5th percentile) trade-level (gate's `boot_lo`) and UTC-date-clustered; total ex-top-3; total ex-best-day; positive-day share; exits; top-day share.

## 8. Selection correction (computed by `tools/p30_laneg_screen.py`, report-only, after the verdict)

* The best cell's rank among the 48 by `min(mean_flat, mean_pressure)` (strand `total`), and the number of cells with positive mean under both models.
* **White reality check**, date-cluster bootstrap: matrix of per-cell (sum SOL, n) per UTC day; B = 10,000 draws of days with replacement (the SAME day draw for all 48 cells), seed 1;
  per cell the draw's ratio (sum/n) is centred on its observed ratio; V* = max over cells of the centred draw; p = (1 + #{V* >= V_obs}) / (1 + B) with V_obs = max over cells of the
  observed ratio. Run for the flat model and the pressure model separately; the reported RC p is the larger of the two.
* A cell may survive and still have RC p > 0.10: then it is a selection-noise candidate and is reported as such. RC p informs the verdict text; it does not change the kill rule.

## 9. Kill rule and the single validation look

* TRAIN (explore-0814 only). **Kill:** if no cell has (n >= 100, >= 5 UTC days) AND CI90 LB (trade-level) > 0 AND total ex-top-3 > 0 AND total ex-best-day > 0 under BOTH flat and
  pressure models (strand `total`, 0.25 SOL, 1.9 s, rent 0), the lane stops: **no validation look**, result "not run: train kill".
* Else: **freeze the single best cell** = the passing cell with the highest `min(mean_flat, mean_pressure)` SOL/trade (ties: higher n). Its name, thresholds and exit are written to
  `docs/research/p30-laneg-numeric/FROZEN.md` and pushed BEFORE fresh-0903 is touched. Then ONE run of the frozen rule (a one-cell rule object, same code) on fresh-0903, once, reported
  mechanically with the same statistics and the gate columns; no second look, no retune, no neighbour cell.
* Validation pass text (mechanical, not a promotion): "PASS-EXPLORATION" only if n >= 100 and all four conditions hold under both models on fresh-0903; otherwise "FAIL". Either way
  no book has cleared the promotion gate (2 exploration blocks, one spent, Aug/Sept regime, 48-cell selection).
* Capacity line for any survivor (trades/day x stake x net edge, impact at 0.25 and 1.0 SOL) from the kit's report.

## 10. Pipeline checks before the priced run (no outcome read)

Unit tests on synthetic hours for every feature and the throttle/age rules; the kit's causal probe on a sample; a **count-only** full pass on the real layout of explore-0814
(triggers per cell per day, pre-declared refusals: age-unknown, gaps, V). The count-only pass reads no outcome. The priced run is one job; the rule file sha256 is recorded in the job output.

## 11. Compute

MiScusi jobs (sh), mem_gb <= 6, 2 cpu, one at a time on research-0; HV6 replays are queued there, so jobs are short. 0 Helius credits (local clean-view files).
