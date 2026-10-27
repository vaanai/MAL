# P62 pre-registration (written before any outcome is joined)

Idea: library row `p60-feeshare-commit-tag`. Step 1 (feasibility) is in the PR body: the label
needs one public-RPC getTransaction of the coin's create signature; it is causal because the
SharingConfig is created inside the create tx (13 of 14 checked, 0-2 s; the other 2 at +31 s and +69 s,
or not found in the oldest 5 PDA signatures).

## Feature F (known at the create tx, hence at any later entry)
`cfg = 1` if the logs of the coin's create transaction (signature from the exploration `creates` tape)
contain `CreateFeeSharingConfig` (fee program pfeeUx...). Else `cfg = 0`. Configs created after the
create tx are NOT seen (conservative; disclosed). Coins whose create row is not in the same block
are excluded and counted.

## Label BL (strict big loser)
Population: `complete` (migration) events in the block. Reference price = price_sol of the first
PumpSwap WSOL-quote trade with event_ts >= migration event_ts. BL = 1 if any later PumpSwap WSOL
trade with event_ts <= migration event_ts + 1800 s has price_sol <= 0.20 x reference. Eligible only
if the reference trade exists and at least one later trade exists within the window. Price is the
tape's `price_sol` (ratio only; absolute V-ignorance is not corrected, disclosed).

## Split (temporal, fixed now)
Discovery = explore-0814 (2026-08-14..28). Confirmation = fresh-0903 + exp011-0909 (2026-09-03..15).
All three are exploration-pool blocks per docs/HOLDOUT_LEDGER.md. Sample up to 400 eligible
migrations per split, random.Random(1) over a sorted list.

## Veto rule and score
Hypothesis (from mechanism, sign fixed): a coin WITHOUT a fee-sharing config is more likely a big loser.
Score = 1 - cfg. Veto = (cfg == 0). AUC = rank AUC of score vs BL (ties 0.5).

## KILL rules
1. Label near-constant: one value > 95% (step 1 measured 92% absent, so not triggered at step 1).
2. Confirmation AUC < placebo + 0.05, placebo = mean AUC of 1000 shuffles of cfg across coins in the
   confirmation set (random.Random(1)); the 95th percentile is also reported.
3. Veto share on the confirmation sample < 1%. (Added at the same time: veto share > 50% is not a
   selective veto and also KILLs it as a filter. The share is of exploration migrations, a proxy,
   because H5/Q50 entries are sealed and not opened.)
4. Discovery AUC is reported as a sign check only.

No book claim. No H5/Q50 file, ledger or output is read. Public RPC only, 0 Helius credits.
