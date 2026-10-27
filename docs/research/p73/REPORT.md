# P73: Mayhem early-collapse fade (mayhem-flow-2). Verdict: NOT_DECIDABLE

Lane P73, 2026-10-27. Exploration tape only (explore-0814, fresh-0903, exp011-0909 via `/data/mal/audit-1008/tape/trades`, `block` filter). In-sample for the lab: at least 14 families have read these dates. No book has cleared the promotion gate. Pre-registration `PREREG.md` (commit d03d83fc) was pushed before any computation. **No pricing was run**, because the pre-registered counts step failed its own gate. There is no P&L, no CI and no capacity figure to report; a number would be fiction.

## What was run

1. `p73_extract.py`: 22,613,073 bonding rows on 201,001 mayhem mints (201,013 mayhem mints with a create in the three blocks).
2. `p73_counts.py` / `p73_counts2.py` (outputs in `p73_counts_output.txt`, `p73_counts2_output.txt`): convention and curve checks, then signal counts.
3. `p73_control.py` (`p73_control_output.txt`): the same check on one hour (2026-09-05T12), mayhem vs non-mayhem bonding mints.

## Counts

- Eligible mints (a print by +60 s and a print at or after +300 s): 76,807. Signal (p5 < 0.5 * p1) fires on 34,581 (45.023%) over 28 UTC days, 543 to 1,859 a day. The row's 20-23% figure is not reproduced; with the broken reserves below, price is not a reliable state.
- Token-count check: `token_raw` equals the change in `base_reserve` on 99.995% (buys) and 99.993% (sells) of consecutive prints. Direction (buy raises quote, lowers base) holds on 99.903% / 99.979% of usable pairs. So the post-trade convention is confirmed for bonding rows.

## Why it stops: the Mayhem curve is not priceable from this tape

The pre-registered rule: if the curve check fails on more than 5% of rows, the verdict is NOT_DECIDABLE and no pricing runs.

- 9.620% of Mayhem bonding rows have `quote_reserve == 0` (and `sol_lamports == 0`); 19,040 of 201,001 mints have at least one.
- Among usable consecutive pairs, constant-product `k = q*b` holds (|drift| < 1e-6) on only 40.295% (buys) and 36.341% (sells). Median |k drift| is 6.28% (buys) and 8.30% (sells); `dq / sol` has median 4.23 on buys where about 0.99 is expected (q5 to q95: 1.0 to 375). Reserves jump by amounts the logged trade does not explain, so flow is hidden between prints or the quote reserve carries something besides trade SOL. A printed example mint swings quote reserve between about 20 and 57 SOL on trades of 0.004 to 0.1 SOL.
- Control, hour 2026-09-05T12: non-mayhem bonding pairs 53,780, k holds on 100.000% (median drift 1.05e-11), q==0 on 0.113%. Mayhem pairs 27,249, k holds on 41.455% (median drift 5.91%), q==0 on 11.602%. The fault is specific to Mayhem mints, not to the tape or my loader.

Consequence: a buy at the collapsed price cannot be filled with constant-product math on tape reserves, and the +1/+5 min prices used for the signal are themselves unreliable (the 45% fire rate is probably noise from the same effect). Fills, stops and take-profits would all inherit that error; the sign of the result would depend on the unmodelled flow. Hence not priced.

## Not measured

Any P&L, both fail models, drop-top-3, drop-best-day, majority-positive days, CI90 and the capacity line: none, by the pre-registered stop. The Mayhem agent's own trades, the hidden flow, and the real Mayhem curve parameters were not investigated.

## Next step (library row mayhem-flow-2, now parked)

Revive only if the Mayhem curve is modelled from account state (curve account reads or decoded events that explain the quote-reserve jumps and the zero-reserve rows), verified to hold k on at least 95% of pairs on an exploration hour, then a fresh pre-registration. A non-Mayhem collapse-fade is a different row and is already covered by the crash-bounce family (dead).
