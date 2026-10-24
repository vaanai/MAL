# P14 DECLARE (written before any attempt count, capacity count or price)

Date: see the line "Declared at" below (date -u). Pricer validation (curve reproduction on 2 exploration hours, 10-14T12-13) was run BEFORE this file: it uses no signal and no count of attempts.

Window: exploration hours [2026-10-14T00, 2026-10-17T00) of /data2/mal/venue-tape-backfill/meteora_dbc/ only. [10-17T00, 10-24T00) is a RESERVED holdout: tools/dbc_price.py
refuses those paths (raw and resolved); only directory listings are ever taken of them. No pump/PumpSwap data is used.

Data preparation (declared; found by the validation step): the tape order inside a slot is not the execution order. Rows are re-ordered inside each (pool, slot) group by the chain
S0_j = S1_i (tools.dbc_price.order_fix; L carried from rows whose pre-state is certain). Groups that do not chain uniquely keep tape order; those rows and the row after them are
flagged `bad` and are never a signal, entry or exit landing state (counted as refused `bad_state`). All swap modes stay in the chain.

Pricing (tools/dbc_price.py, tools/dbc_screen.py): decision at the signal print's slot; the buy lands on the state after every print with slot <= decision + k, k = round(1.9 s / slot_s),
slot_s per UTC hour from the tape's own slot / block_time (0.4 s fallback); exits land the same 1.9 s after their decision (3.4 s exit lag reported as a sensitivity only).
Curve: constant liquidity sqrt-price curve; L carried per pool from the last trade with quote leg >= 0.01 SOL; PER-TRADE fee rate = the pool's last observed rate on that side
(causal; at the decision print for the buy, at the last print <= the exit decision for the sell), fee side by the config's fee mode, fallback 1% when the pool has no earlier same-side
print. Our own impact is applied at stake (0.25 SOL deciding; 1.0 SOL also priced); our buy is re-injected into the exit state. Guard: exec price / decision spot > 1.15, or the stake
would pass migration_threshold at the landing state = not filled, one send paid. Sends 160,000 lamports each, 2 per filled trip; rent 0 deciding (assumption: the sell closes the
token ATA and refunds it; the rent-charged leg 2,039,280 lamports per filled trip is reported beside it; Token-2022 mints would cost more and are not separated). A position whose
sell lands at or after the pool's curve_complete slot is priced stranded (stake + buy send + rent lost), counted. No haircut (none measured on this venue): optimistic by an unknown amount.
Fail models: flat 15% and pressure (slope scale 1, intercept refit per cell, mean p 0.289) via tools.arena_stack.fail_legs. ssb = buy prints in the landing slot, nearby = buy lamports in the 2 s before it.
Stats: mean SOL/trip, total, ex-top-3, drop-best-day, per UTC day, trade-level CI90 lower (1,000 draws, seed 1, 5th pct) and date-clustered CI90 lower.
The gate numbers (n >= 100, >= 5 days with a majority positive, CI lo > 0, ex-top-3 > 0) are reported as exploration only. THREE days cannot meet the 5-day criterion: the best possible
label is MARGINAL and it is not a gate pass. No sentence says a cell makes money.

Cells: the 12 in IDEAS.md (D1 m4/m6/m10, D2 ms30/ms50/ms70, D3 bb1/bb2/bb4, D4 c3/c4/c6). Counts first; < 3 attempts/day on average = dropped unpriced. Universe U, rules, exits and cooldowns exactly as IDEAS.md.
Labels (applied after pricing, not tuned): MARGINAL = both fail models mean > 0, ex-top-3 > 0 and ex-best-day > 0 at 1.9 s / 0.25 SOL / rent 0 (n and day criteria cannot be met by 3 days); otherwise FAIL.
Multiplicity: 12 cells, one pricing look on the 72 hours. One run, no re-tuning. The capacity count (trades per day with <= 2% impact for 0.25 SOL) is a count, not a price.
Declared at: 2026-10-24T03:58:38Z
