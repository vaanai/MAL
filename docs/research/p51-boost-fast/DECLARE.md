# P51 BOOST-fast: does a Helius-fed entry (3-5 fewer slots) improve the frozen H5 / Q50 rule? DECLARE

Written 2026-10-26T20:30Z (date -u), before the sweep ran and before any P51 outcome was computed. Exploration only, in-sample. **This is not an edge claim.** No book has cleared the promotion gate. Nothing here says a book makes money.

## 1. Question

The H5 BOOST-floor entry decays with entry delay. Today's live/arena path charges about 9 slots (in August/September slot units) between the trigger print and the landing: the 1.9 s leg plus the 7-slot arena feed delay (QP ruling `/data/mal/hunt-1008/push-1016/hunt30/feed-lag/QP-RULING-1026-feed-lag.md`: arena PumpSwap head lag p50 0.857 s, p90 1.402 s; P47 Helius transactionSubscribe p50 0.04 s, its p90 2.02 s likely fit noise). How much does the frozen rule's per-trade mean improve if total entry delay drops by 3, 4 or 5 slots?

## 2. Frozen rules (pins)

| Rule | Pin | Role here |
| --- | --- | --- |
| H5-BOOSTFLOOR-Q50 v1 | `EXP/EXP-024-RULE-Q50.md` sha256 `b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4` (= the `rule-q50` fence of EXP-024, checked 2026-10-26T20:28Z) | **deciding** (Q* = 50 SOL) |
| H5-BOOSTFLOOR v1 | `/data/mal/hunt-1008/h5-flows/RULE.md` sha256 `c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c` | report-only (Q* = 40 SOL) |
| Scorer port | `tools/boostfloor_score.py` sha256 `030114221cfaadea7ec64bc0dcd0d8e8131d2cac7be049e5a363511e5e781e9f` at origin/main `0be9f676` | its pool state, BOOST detector, fills, fail legs and `gate_stats` are imported unchanged |
| Pressure curve | `tools/latency_curve.py` sha256 `129a8dddb8567bfa4cb3f35120b5da702439ff0df930a1bfb9d542ecd6a983db` | refit per (rule, entry cell) on that cell's sends, as the rule says |
| Fee tiers | `tools/paper_curve_math.py` sha256 `019c64c317d98d8b11d1319d8202ca72d49df3fee12b983cf3c6f407753fcafa` | canonical PumpSwap tier |

Everything in the rule stays as written (universe V in [17.5, 17.7] SOL, BOOST wallet detector, trigger window [0, 300] s, BOOST budget check, exit at s0 + round(330 s / sps) plus ceil(0.55 s / sps), END-bound states, own impact on Q incl. V, 55,000 lamports per send, flat 15% fail, pressure fail at slope scale 1 with mean p 0.289) **except the entry landing slot**, which this sweep replaces by `X = slot_i + k`. The arena-layer terms of DEC-031 (WSOL, synthetic exclusion, stack, rent) are not applied; this is the exploration tape, not the arena.

## 3. Entry grid

- **Deciding grid (fixed slots after the trigger print):** k_cur = 9 (manager brief: 1.9 s leg + 7-slot feed delay, in exploration-block slot units), and k_cur−3 = 6, k_cur−4 = 5, k_cur−5 = 4.
- **Report-only:** k = 8 and k = 7 (k_cur−1, k_cur−2); and two seconds legs converted per pool, `k = ceil(d / sps)`: d = 3.302 s (1.9 s + arena feed p90 1.402 s, "today") and d = 2.485 s (today minus the measured p50 gap 0.857 − 0.04 = 0.817 s, "Helius").
- **Unit caveat, declared now.** The feed-lag measurements are in October slots (sps about 0.2125 s). The exploration blocks run at about 0.37–0.40 s per slot. So "4 slots" here is about 1.5–1.6 s, roughly twice the measured 0.817 s Helius gain. The seconds legs are the time-matched view; the slot grid is the brief's view. Both are printed.

## 4. Data (exploration pool only, docs/HOLDOUT_LEDGER.md)

Derived views `/data/mal/audit-1008/work/g_reachable_cap_book_rescore/{meta,paths}/<day>.parquet` (Aug/Sep days only). Blocks and graduation windows (meta `blk`, checked by count on 2026-10-26: 11,172 / 4,141 / 3,953 / 2,406 / 2,651 V-range meta rows, all inside these windows):

| Block | Window |
| --- | --- |
| explore-0814 | [2026-08-14T12, 2026-08-28T12) |
| fresh-0903 | [2026-09-03T12, 2026-09-09T12) |
| exp011-0909 | [2026-09-09T12, 2026-09-15T12) |
| fast-pool-0918 | [2026-09-18T23, 2026-09-22T00) |
| oracle-insample-0922 | [2026-09-22T00, 2026-09-25T07) |

The tool refuses any row whose block is not one of these five, whose graduation is outside its block window, any EXP-009 hour [09-15T12, 09-18T23), and anything at or after 2026-10-02T10 (inherited refusals of `boostfloor_score`). No October hour, no forward, arena, HV4, Q50 shadow/canary, DBC, LaunchLab or meta-capture file is read. EXP-024 Look-2 seal: untouched (no October hour at all).

## 5. Stakes and impact

0.25 SOL (deciding) and 0.5 SOL (report-only; shows own impact at double size). Own impact on Q incl. V as in the rule; other traders' deltas held fixed.

## 6. Statistics

Per (rule, entry cell, stake, fail model), pooled and per block: n, UTC days, mean % of stake per trade, mean SOL per trade, CI90 by date-cluster bootstrap (1,000 day resamples, seed 1, 5th/95th percentile; the rule's `common.gate`), days positive, total SOL, total ex-top-3, total ex-best-day. Improvement = paired mean of (pnl at k) − (pnl at k_cur) on the pools that trade in both cells, in pp of stake, with its own date-cluster CI90.

## 7. Kill rule (deciding cell: Q50, 0.25 SOL)

PASS only if, under **both** the flat and the pressure fail models:
1. pooled paired improvement at k_cur−4 = 5 vs k_cur = 9 is **≥ +1.0 pp per trade**; and
2. the improvement is > 0 in **≥ 3 of the 5 blocks**.

Otherwise P51 **closes**. PASS means only "worth a registration", never "a book is positive".

## 8. Multiplicity and status disclosure

- This is a k-sweep on data that has already been screened many times: H5 was discovered on explore-0814 and its confirmation days are the September blocks used here; Q* = 50 is a discarded discovery cell; the EXP-012 latency curve and the h5-flows `s19_latency.py` legs (1.3 / 1.9 / 3 / 6 s) already read these pools. Everything is in-sample. Winner's curse applies to the level of every cell.
- Under DEC-014 a change of k is a successor, not a new family. A pass spends no block and earns no claim. Any forward use needs a new registration with its own clean clock and both fail models.
- 8 entry cells × 2 rules × 2 stakes × 2 fail models are printed; only the cell in section 7 decides.

## 9. Capacity

trades/day in the latest exploration regime (oracle-insample-0922, Q50 trades / its UTC-day span) × 0.25 SOL × pooled improvement at k_cur−4 = SOL/day of improvement. October trade rates are not used (Q50 outputs and October H5 outcomes are sealed).

## 10. Run

`tools/p51_boost_fast_sweep.py`, MiScusi job on mal-research-0, MemoryMax ≤ 8 GB, output `/data/mal/hunt-1008/p51-boost-fast/`. Result file: `docs/research/p51-boost-fast/RESULT.md`.
