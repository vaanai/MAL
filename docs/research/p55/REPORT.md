# P55: exit H5 when BOOST ends (lib-c-N3, w2-pump-idl-3). Verdict: KILL

Lane P55, 2026-10-26. The research agent did the work. The manager wrote this file from the agent's final report, because the agent's Write tool refused report files. Numbers are copied unrounded from `p55_analyze_output.txt`. No book has cleared the promotion gate.

## 1. Coverage: exploration tape has no boost events

- The clean-view trade tape for explore-0814, fresh-0903 and exp011-0909 has 624 hourly files. It carries only `pump_bonding` and `pumpswap` trade rows.
- A sample of 11 hours (every 60th file, 11,228,324 rows) contains no `boost_buy_and_burn` rows and no `boost_vault_remaining` field. The text hits ("boost" 8, "vault" 32, "burn" 1,946, "remaining" 0) are all random base58 substrings.
- The walker decodes boost events only when run with `--event-v`. The Aug/Sep walks did not use it, and these blocks have no events directory.
- So the real field covers 0% of the exploration data. This lane priced a **trade-tape proxy**: cumulative spend by the causally detected BOOST wallet, plus a stall clock. A trigger on the real `boost_vault_remaining` is still **untested**.

## 2. Pre-registration

`PREREG.md` was committed at 726f4659, before any pricing. It fixes one parameter set and no grid:

- **E1:** proxy vault remaining ≤ 1.0 SOL.
- **E2:** no causal BOOST buy for ceil(36 s / sps) slots.
- **Exit:** whichever comes first of E1, E2 and the frozen 330 s trigger. The sell lands 2 slots later (exit lag 2, the realistic primary).
- **Kill lines:**
  - mean net change per trade vs the frozen exit below +1.0 pp under both fail models;
  - driven by the top 3 trades;
  - driven by one day;
  - wrong sign in either set;
  - fewer than 30 changed exits.

## 3. Pricing

All 2,826 frozen binding trades were re-priced: 1,702 discovery and 1,124 confirmation. Pricing used PumpSwap pre-trade reserves and exit lag 2, against the frozen exit re-priced at lag 2.

- **Tool check:** with the frozen lag and exit, the re-priced pnl equals the stored pnl exactly (max difference 0.0 lamports).
- **Exits changed:** 590 of 2,826 trades (20.878%).

| | flat 15% | pressure-fail, slope 1 |
|---|---:|---:|
| mean delta (pp of 0.1 SOL stake) | -1.7024 | -1.4554 |
| discovery / confirmation | -1.4930 / -2.0194 | -1.5826 / -1.2629 |
| mean delta without the top 3 | -2.0209 | -1.7012 |
| mean delta without the best day (2026-08-14) | -1.9199 | -1.6118 |

**Kill lines hit:** 1, 2, 3 and 4.

**Component checks** (report only, not a selection):

| trigger alone | flat | pressure |
|---|---:|---:|
| E1 | +0.1889 pp | +0.1682 pp |
| E2 | -1.8913 pp | -1.6239 pp |

E1 alone stays far below the +1.0 pp bar. E2, the stall clock, cuts winners.

**Other checks:**
- **Tail:** the rule changes the exit on only 25 of the 91 trades at or below -50% (flat). It does not reach the deep tail.
- **Proxy caveat:** E1 fires on 417 trades, some about 293 s early. This is the known 37% disagreement between the causal detector and the frozen BOOST wallet, not a new finding.
- **Sensitivity:** at the frozen 0.55 s lag, the mean delta is -1.6879 pp flat and -1.4367 pp pressure.
- **Scope:** these are in-sample lab dates, already read by at least 14 families.

## 4. Library and next step

- `lib-c-N3` → explored-no-survivor, for the trade-tape proxy only.
- `w2-pump-idl-3` stays untested. Its next step is an approved `--event-v` capture on a free block.

## 5. Seals

No seal contact. The lane used only the Aug/Sep exploration blocks (explore-0814, fresh-0903, exp011-0909) and the audit-1008 exploration paths already used by H5 studies. It touched no HV4/Q50 output or job, meta-capture, DBC, arena tape, October hour, FLOOR G1, LaunchLab or D1. Pricing ran under `systemd-run --user --scope -p MemoryMax=8G` and finished in under 5 minutes.

## Files

`PREREG.md`, `p55_coverage.py`, `p55_price.py`, `p55_analyze.py`, `p55_analyze_output.txt`.
