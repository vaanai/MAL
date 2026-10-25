# P26 DECLARE: p25-redeploy-holiday. COUNTS ONLY. Written before any count.

Declared at (date -u): 2026-10-25T13:17Z. Lane P26, manager15. Exploration, no pricing, no P&L. 0 Helius credits.

## What was done before this file
One structure peek on hour 2026-10-15T12 (row types and counts only): trade rows are EvtSwap2 with a `signer`; `create` rows carry pool and slot; `event_undecoded` rows exist (300 of 35,605) and carry no signer.

## Windows and ledger check
- DBC `[2026-10-14T00, 2026-10-17T00)`, /data2/mal/venue-tape-backfill/meteora_dbc (72 hour files, 10-14, 10-15, 10-16 only). Called exploration E1 in docs/research/dbc-p14/DECLARE.md, dbc-p21/DECLARE.md and P25 IDEAS.md; docs/HOLDOUT_LEDGER.md has no DBC row. I treat E1 as allowed because those DECLAREs name it exploration and `[10-17T00, ...)` as reserved/spent. Not touched: meteora_dbc_holdout, fwdv1, p17_union, any hour >= 10-17 or >= 10-25.
- forward-1002 D0 (second leg, around 2026-10-08T16:20Z and 2026-10-13T15:40Z): the ledger releases D0 for exploration only through catalog role `oct-structure-d0` as a MiScusi job listing input sha256 and printing resolved_gates(), and "released hours never confirm". A direct read from this session would bypass that role, so **this lane does NOT read forward-1002.** It writes a job spec for the manager instead (REPORT.md).

## Rule (DBC leg)
- Change: DBC EvtSwap2 -> EvtSwap3 at about 2026-10-16T04Z (locate it from the tape by event-name counts per hour).
- Per new pool (type create), signers = distinct `signer` among trade rows of that pool with slot in [create_slot, create_slot + 5]. Hour stats: pools created, median signers.
- Post = 10-16T04 onward (up to 10-16T23, 20 h). Control = same clock hours of 10-14 and 10-15 (mean of the two medians).
- Hour valid only if >= 5 pools created in it on every compared day.
- Holiday on the DBC leg = post median signers >= 25% below control for >= 3 valid hours.
- Decode confound check: per hour count event_undecoded rows and trade rows by event name. If post-switch swaps appear as undecoded (no signer), the metric is not computable from this tape and the leg is BLOCKED, not "no holiday".

## Counts to make
Per hour over the 72 h: trades by event name, undecoded rows, creates, curve_complete, per-hour medians as above; per-day totals; distinct pools created and distinct signers per day.

## Kill rules (fixed now)
1. DBC leg: fewer than 3 valid post hours, or no run of >= 3 hours with a >= 25% drop: DBC leg NEGATIVE.
2. The revive criterion needs a drop after >= 2 of 3 changes. The DBC window holds only ONE change, so even a positive DBC leg cannot clear the idea; it only qualifies the D0 leg (job spec). Negative DBC leg plus a negative D0 leg = DEAD.
3. Fewer than 5 usable new pools/day = dead.

## Capacity screen (guess, IDEAS.md)
2 layout changes/week x 6 h x ~20 trades x 0.25 SOL x +2% = 0.2 SOL/week = 0.029 SOL/day (about $4.3/day at $150). Windows are rare and short; A3 halts MAL's own windows then; trading in them needs a new DEC. Even at 10x the guess this is under 0.3 SOL/day. No profit claim; no book has cleared the promotion gate.
