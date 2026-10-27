# P89 pre-registration: P86 kNN score as a veto on the frozen H5 binding trades (lib: p67-knn-case-memory follow-on)

Written before any H5 outcome (pnl, flat, press, net_pct) was joined to any P86 feature or score. Exploration blocks only (explore-0814, fresh-0903, exp011-0909, fast-pool-0918, oracle-insample-0922 as in P55). In-sample lab dates read by at least 14 families (EXP-024 sec. 9). A pass is a LEAD toward an H5/Q50 amendment only; it never changes a running book. No book has cleared the promotion gate.

## Population and score (frozen)

- Trades: `/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet`, 2,826 trades (discovery 1,702, confirmation 1,124), paths from `/data/mal/audit-1008/work/g_reachable_cap_book_rescore/{meta,paths}` (PumpSwap PRE-trade reserves, plus V as P55).
- Features: P86's ten (log_vol60, ret_5m, ret_15m, ret_60m, flow60, log_signers15, log_age, top3_60m, close_vs_hi, log_qeff), defined exactly as in `docs/research/p86/p86_build.py`, evaluated at the trade's entry instant t_e = block time of the trigger print + 1.9 s (leg b landing), using prints with slot strictly before the entry slot X only. The "price at or before t - w" rule and the "any undefined feature -> dropped and counted" rule are P86's, unchanged.
- Memory: P86's explore-0814 candidate population (`/data2/mal/p86/labeled.parquet`, rows with td < 2026-09-03), labels y as P86 defined. Standardised on memory only; k = 25; Euclidean; score = mean y of 25 nearest.
- Step 1 (counts only, no outcome): report how many of the 2,826 trades have all ten features computable.

## Rule (ONE parameter set)

- Veto the lowest 15% of kNN scores. Threshold = 15th percentile of scores on the H5 DISCOVERY set (scores only). Applied once, unchanged, to confirmation.
- Pre-declared inapplicability clause: if fewer than 50% of trades (and fewer than 100 per set) have computable features, the veto cannot be applied as specified. No outcome is joined, no relaxed-feature variant is run in this lane, and the verdict is NOT_DECIDABLE (domain mismatch). A relaxed variant would be a new pre-registration.

## Reads (only if the clause above does not trigger)

Per set and per fail model (flat 15%; pressure-fail slope 1; stored columns `flat` and `press`), P55 exact pricing (reproduce stored pnl, max diff 0.0): vetoed share; vetoed mean vs kept mean; book mean delta (pp of 0.1 SOL stake: kept-book mean minus all-trades mean); delta ex-top-3 and ex-best-day; kept-book total ex-top-3; big-loser capture (share of trades with pnl <= -50% that were vetoed); placebo: random 15% veto, 1,000 draws, seed 1, p = share of draws with delta >= observed.

## Kill line (confirmation)

KILL if delta < +1.0 pp under either model, or placebo p > 0.10, or the result is driven by the top 3 or one day (delta total <= 0 after removing the top 3 trades by contribution or the best day).

## Seals

Exploration blocks only. No Q50/H5 forward output, no jobs #1419/#1421, no October hour, no arena, DBC, LaunchLab, FLOOR G1 or meta-capture. EXP-024 Look-2 seal untouched.
