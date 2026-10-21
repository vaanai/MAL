# RULE H5-BOOSTFLOOR-Q50 v1 (sibling of H5-BOOSTFLOOR v1; frozen 2026-10-21, before any Q50 trigger outcome was computed on an October pool)

Thesis H5 (predictable flows). The pump.fun BOOST agent is a price-insensitive TWAP buyer: after every non-mayhem graduation it spends
exactly 17.585 SOL in ~29 slices over ~345 s, whatever the price. PumpSwap price = (real quote + V) / base with V = 17.58 SOL, so sells
cannot push Q = real quote + V below V. Rule: when early dumping has drained the pool close to its V floor while BOOST still has budget,
buy and hold to just before BOOST's last slice.

## Universe (decision-time information only)
- Every `complete` graduation whose canonical PumpSwap pool has V in [17.5, 17.7] SOL (non-mayhem, BOOST era). Canonical pool and V as in
  /data/mal/audit-1008/work/g_reachable_cap_book_rescore/meta/<day>.parquet (first V-range pool after `complete`); paths from
  .../paths/<day>.parquet (PumpSwap PRE-trade reserves, ordered slot, tx_index, event_index). The graduation precedes every decision; no
  future event defines the universe.
- s0 = slot of the canonical pool's first print. sps = seconds per slot of that pool, from block_time over its path (must be in (0.15, 0.6)).

## BOOST tracking
- BOOST wallet = the buy-only wallet with >= 3 buys, each 0.2..2.0 SOL, total <= 17.7 SOL, in the pool's first 1,600 slots (most buys wins).
  Live equivalent: the per-pool BOOST vault PDA. Completion of the 17.585 SOL budget is NOT required (an early stop counts against the rule).
- spent(i) = cumulative BOOST buy SOL up to and including print i.

## Trigger (one trade per pool, first qualifying print)
- print i is a sell (non-BOOST by construction), t_i = (slot_i - s0) * sps in [0, 300] s,
- post-trade Q_i = real quote + V <= 50 SOL (post-trade state of print i = pre-trade state of print i+1),
- spent(i) < 0.999 * 17.585 SOL.

## Entry
- Landing slot X = slot_i + ceil(1.3 s / sps) (primary leg) or ceil(1.9 s / sps) (binding leg). END bound: pool state after every print
  in slots <= X. Buy size S in {0.1, 0.25} SOL; net = S * (1 - f), f = canonical PumpSwap tier (tools/paper_curve_math.PUMPSWAP_SOL_FEE_TIERS)
  on the landing state's market cap; tokens = B * net / (Q + net). Our own impact is applied (constant product on Q incl. V).

## Exit
- Exit trigger at slot s0 + round(330 s / sps) (just before BOOST's last slice and the documented post-BOOST cliff). The sell lands
  ceil(0.55 s / sps) slots later, END bound; our buy stays in the pool until we sell; proceeds = tokens * Q' / (B' + tokens) * (1 - tier fee).

## Costs and legs
- 55,000 lamports priority per send, on the buy and on the sell.
- Flat fail: pnl_flat = 0.85 * pnl + 0.15 * (-55,000 lamports).
- Pressure fail: tools/latency_curve.fit_curve (slopes 0.8 / 0.35 at scale 1, intercept refit so mean p = 0.289 on this book's sends) on
  (buys in the landing slot, buy lamports in the 2 s up to and including the landing slot); pnl_press = (1-p) * pnl + p * (-55,000).
- Gate statistics: common.gate (date-cluster bootstrap, 1,000 day resamples, seed 1, 5th/95th percentiles).

## Code
- s14_boostdip.py run with env DS=50 TMIN=0 TMAX=300 TEND=330 NOBOOSTREQ=0 (MODE unset); the rule's exit is column H == 'end'.
- s15_score_dip.py scores it. Discovery numbers that motivated the rule: out/s15_dip_v2_disc_grid.txt and the full-leg rows in REPORT.md.

## Parameters chosen on discovery (explore-0814 only)
- Q* = 40 SOL from {40, 50, 60} (monotone: smaller Q* better); exit 'end' (330 s after first print) from {15, 30, 60, 120 s after landing, end};
  trigger window [0, 300] s fixed a priori; BOOST remaining check fixed a priori. Q* = 50 here, a discarded discovery cell (discovery: monotone, smaller Q* better); chosen 2026-10-21 for attempts/day, not for edge.
