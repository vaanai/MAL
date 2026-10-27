# P92 report: Haiku ranking vs numeric kNN on P86 cards

Verdict: **KILL (as an add-on to kNN). Haiku adds nothing over kNN on these cards.** The ensemble does not beat kNN either, so there is no LEAD. In-sample exploration days (fresh-0903, exp011-0909) that at least 14 families have read; 0 API calls; no book has cleared the promotion gate. Not judged by quant-proof.

## What was done
- PREREG (`PREREG.md`) committed and pushed before any rating (commit dab6131). 80 hours, stratified by UTC day, seed 1, from the 285 P86 test hours (13 days); all 8 P86 candidates per hour; cards carry 15 causal fields (P86's 10 features plus 5 descriptive), opaque ids, shuffled order. RANK_v0 system prompt verbatim except the exit phrase (stop/target replaced by "sell exactly 60 minutes later"). No outcome in any prompt.
- 8 Claude-plan Haiku subagents (model haiku), 10 hours each, Read + Write only, no scripts (each used exactly 3 tool calls). 80 of 80 hours returned full permutations; none re-asked, none excluded.
- Labels: P86 labelled y; 77 of 640 sample candidates were dead pools (no priced exit in P86's window) and are priced at their last print before t_d + 5400 s; 4 had no entry print and are excluded. kNN is the P86 walk-forward k = 25 score (P86 primary memory rule).

## Rank IC per hour (80 hours, 13 days)
| arm | mean IC | hour t | day-block t |
|---|---:|---:|---:|
| Haiku | +0.02723 | +0.517 | +0.455 |
| kNN | +0.29583 | +6.817 | +5.188 |
| ensemble (mean rank) | +0.24494 | +6.488 | +6.994 |

| difference | mean | day-block t | days + / - | sign test p (two-sided) |
|---|---:|---:|---:|---:|
| Haiku - kNN | -0.26860 | -2.810 | 2 / 11 | 0.0225 |
| ensemble - kNN | -0.05089 | -1.175 | 4 / 9 | 0.2668 |

IC correlation across hours between Haiku and kNN: -0.202. Haiku is better than kNN on 2 of 13 days only.

## Top-pick mean per trade, pp of the 0.25 SOL stake (80 hours)
Dead pools priced at last print (primary):

| arm | flat 15% | pressure-fail |
|---|---:|---:|
| Haiku | -23.725 | -18.419 |
| kNN | -11.453 | -9.189 |
| ensemble | -7.828 | -5.949 |
| U | -16.700 | -13.311 |
| R (seed 1) | -9.852 | -8.624 |

Dead pools dropped (P86 convention, report-only): Haiku -9.233 / -7.566, kNN -3.195 / -2.325, ensemble -0.389 / -0.018, U -8.315 / -6.520, R -5.291 / -4.364. Every arm is negative under both models in both pricings. Haiku's top pick is worse than U and R in the primary pricing.

## Reading
- Haiku's ordering carries almost no rank information on these cards (IC 0.027, t 0.5) and is significantly worse than kNN. The kNN IC here (0.296) is in line with P86's 0.278 on all 285 hours.
- The ensemble's IC falls below kNN's, so the registered LEAD condition fails. The ensemble's lower negative top-pick mean (-7.8 pp vs -11.5 pp flat) is a different quantity from the registered test (IC), is 80 picks on 13 days, and is not claimed.
- Capacity: every top-pick mean is negative, so capacity is none.

## Limits
- 80 hours, 13 days: the day-level sign test is low power; the Haiku shortfall is large enough that p = 0.0225 still holds. Only one prompt, one Haiku version, one pass per hour (no self-consistency).
- Cards lack HV7 fields absent from the P86 build (ret_1m, ret_240m, vol_5m/15m, impact_rt_*, dd_ath, risk.*, setup.type), so this is not HV7's card; the kNN here uses the same 10 features that are on the card.
- Dead-pool exit at last print is an approximation; pressure p for dead pools uses P86's curve. The kNN memory follows the P86 PREREG rule (known 00:02 look-ahead, P86 Note 2).
- Haiku rated hours it saw nothing about; labels never entered a prompt. Rating files committed in `ratings/`; prompts and keymap in `/data2/mal/p92/` (not committed).

## Files
`PREREG.md`, `p92_build.py`, `p92_build_output.txt`, `ratings/batch_*.json`, `p92_score.py`, `p92_score_output.txt`.
