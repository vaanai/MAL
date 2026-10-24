# P11 DECLARE-I5 (written before any I5 price; the CPMM swap math is measured, not priced, first)

Data: /data2/mal/venue-tape-backfill/launchlab_cpmm/pools (job #1474): Raydium CPMM pools created by LaunchLab MigrateToCpswap in the EXPLORATION window; history from graduation for 24 h, clipped at 2026-10-17T00.
Probe facts (jobs #1468-#1473): ~7 of 40 sampled graduations are WSOL-quoted (the rest are quoted in BONK or other tokens: not priced here; SOL-quoted only); a WSOL pool has about 550-1900 transactions in its first 24 h, 7-20 gTFA pages.
CPMM SwapEvent decodes fully (170 bytes: pool, vault balances before, amounts, fees, mints, creator_fee_on_input). Pool/accounts: initialize_with_permission, pool_state = accounts[4].

Cell (one only, nothing tuned): GF-TP150-24H.
- Universe: WSOL-quoted CPMM pool, fetched history complete (not truncated, 24 h window not clipped by the holdout edge), at least 5 swaps, a swap within 600 s after the entry time.
- Entry: first swap at or after graduation + 300 s; the decision is that swap (decision slot, decision spot = post-swap SOL/token price). The buy lands on the state after every swap with slot <= decision slot + k, k = round(1.9 s / 0.22 s) = 9 slots.
- Exit: take-profit when the post-swap price of a swap at slot >= decision + k + 1 is >= 1.5 x the decision spot; otherwise the time exit at graduation + 24 h - 30 s (the fetch ends 24 h after graduation, so the time cap is 30 s short of 24 h). The sell lands k slots after its decision, on the state after every swap with slot <= that slot (after the last known swap: derived).
- Stakes 0.25 SOL (deciding) and 1.0 SOL; sends 160,000 lamports x 2; rent 0 deciding and 2,039,280 reported; guard 1.15; flat 15% and pressure fail models (tools.arena_stack.fail_legs); exit lag 3.4 s sensitivity reported only.
- Fees: measured from the pool's own last observed swap in each direction (trade fee, plus creator fee when it is on the input; creator fee on the output otherwise).
- Blocks: A = graduations 2026-10-07T00..10-17T00 priced first; B = 2026-09-24T00..10-07T00 only if A is not FAIL. Statistics as in DECLARE.md. n will be small (tens): the n >= 100 gate cannot be met; the label is FAIL when either fail model has mean <= 0 or ex-top-3 <= 0, otherwise UNDERPOWERED-POSITIVE (never a pass).
- Known approximations: no haircut; the derived after-state after the last swap lacks the LP share of the trade fee; slot time 0.22 s fixed (measured on the LaunchLab tape, not on CPMM).
