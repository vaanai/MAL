# P17 DECLARE: DBC Swap3 universe screens (written before any attempt count, capacity count or price on 2026-10-17..10-23)

Status of the data: the m10 holdout [2026-10-17T00, 2026-10-24T00) was READ ONCE (#1494, FAIL, 0 attempts, marker HOLDOUT_READ_DONE_v2). It is SPENT. Everything below on those hours is
DISCLOSED EXPLORATION, not a holdout and not evidence. m10 and every D1 (early-momentum) variant are ENDED on 10-17..10-24: D1 is not run (code: `--swap3` never runs D1 cells).

What was run BEFORE this file (no ideas, no attempts, no prices): event-type counts per hour, vault-delta and integer-math checks on 110 real Swap3 trades (tools/test_dbc_swap3.py),
config-account decode, a 60-tx CU/fee sample, the legacy m10 re-run on 10-14..16 (byte-identical to #1488), and a `--validate --no-capacity` pricer validation on 10-17..10-23
(curve reproduction, fee distribution; it prints no attempt count and no capacity count). A first `--validate` run that would have printed capacity counts was cancelled before it wrote anything.

Universe U' (differs from P14 U in ONE rule): pool excluded only if it has a real transfer-hook row (event or ix name contains "TransferHook"); EvtSwap3 rows are priced
(tools.dbc_price.enable_swap3_pricing: Swap3 buy `fee_on_base_token` is exact fee-side evidence). Everything else as P14 U: SOL-quoted, config fee mode known, migration_threshold >= 5 SOL,
0.25 SOL buy impact <= 2% at the signal state, entry progress p <= 0.85, curve not completed, not a bad (order-uncertain) row. Hook-pool exclusion share per day is reported.

Window: hours [2026-10-17T00, 2026-10-24T00) from the hardlink dir /data2/mal/venue-tape-backfill/p17_union (same files); one warm-up hour (no signal before 10-17T01); attempts whose exit
window passes 10-24T00 are refused (block_edge). Seven UTC days.

Cells (IDEAS definitions of P14 for D2/D3/D4 unchanged; D6 new), 11 cells:
 D2 MILESTONE ms30, ms50, ms70; D3 BIG-BUY bb1, bb2, bb4; D4 CAPITULATION c3, c4, c6 (exactly docs/research/dbc-p14/IDEAS.md).
 D6 DIP-BUY (NEW): trigger on a SELL print with sell quote >= M SOL whose own move is between -25% and -2% of the spot (post / pre - 1), post progress p in [0.05, 0.80], universe U'.
   Variants dbs1 (M = 1.0 SOL), dbs2 (M = 2.0 SOL). Exit tp 1.10, sl 0.92, time 180 s, cooldown 120 s per pool. Hypothesis (not a claim): a single large sell that is not a cluster
   (D4 needs a cluster of K sells) leaves a temporary dip that a 1.9 s follower can buy.
 Rate bar: >= 3 attempts/day averaged over the 7 days after cooldown, else dropped unpriced and said so. No parameter changes after a count. One pricing look.
Multiplicity: 11 cells here + P14's 12 cells = 23 cells on this data family. A Bonferroni-style one-sided lower bound at alpha 0.05/23 is reported next to the trade-level CI90 lower.

Pricing: identical to P14 DECLARE (1.9 s legs, 0.25 SOL deciding / 1.0 SOL sensitivity, 2 x 160,000-lamport sends, guard 1.15 + migration room, own impact, PRIMARY strand = full loss with the
last-price strand as an optimistic bracket, rent 0 deciding with the 2,039,280 lamports leg reported (Token-2022 mints would cost slightly more), flat 15% and pressure fail models, no
haircut), plus Swap3-specific: per-trade fee from the event incl. transfer-hook fee fields (zero on all sampled configs), fee side from the Swap3 flag/config.
Labels as P14 (MARGINAL = both fail models mean > 0, ex-top-3 > 0, ex-best-day > 0; otherwise FAIL). Gate-style numbers (fills >= 100, >= 5 days with a majority positive, CI lo > 0) are
printed as exploration only. NO SENTENCE SAYS A CELL MAKES MONEY; a MARGINAL here would only justify a forward paper book that needs a fresh holdout (see REPORT).
Declared at: 2026-10-24T06:52:06Z
