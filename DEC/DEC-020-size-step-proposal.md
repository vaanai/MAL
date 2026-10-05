# DEC-020: Size step for the live probe at 0.25 SOL per entry (proposal)

| Field | Value |
| --- | --- |
| **Status** | **Proposed. Nothing trades at 0.25 SOL until the owner says yes in writing to one option below.** |
| **Decider** | Vaan (owner). The manager prepares; Helm installs. |
| **Date** | 2026-10-05 |
| **Builds on** | DEC-018 (live-trial readiness, proposed) and DEC-019 (the 0.05 SOL execution probe, Amendments 1 and 1a). |
| **Does not amend** | The promotion gate, DEC-016 and its amendments, or DEC-018 §1 (when a gated trial may start). Probe trades never count toward any gate. |

## 1. Why a size step

Every study on 2026-10-05 points the same way. At 0.05 SOL with the 500,000-lamport priority fee, EXP-012 is about break-even after fees.
- **Operating point v2** (exploration, #327): the frozen point at k=6 has a pressure mean of 0.00058 SOL per trade, CI90 [−0.00012, 0.00131].
- **Exit re-check with a 2-slot exit lag** (#180): 0.00035, CI90 [−0.00037, 0.00108].
- **The same cell at a 155k fee:** 0.00110, CI90 [0.00040, 0.00182].
- **Why the fee matters:** it is 2.02% of a 0.05 SOL round trip, and about 0.40% of a 0.25 SOL one.

A larger size is the only way fees stop absorbing the edge, if the edge exists. **No simulation in our setup can say whether 0.25 SOL works** (quant-proof on #323). The costs that grow with size are not modelled: slower exits, a worse sell fill, MEV and sandwiching, and other traders reacting to our order. Only live trades at that size can measure them.

## 2. Probe state when this was written

Source: the pinned `--status` and fills, 2026-10-05 ~23Z.
- **Attempts:** 28/90 used.
- **Realized:** −0.153362 SOL. Wallet about 0.356 SOL before any new funding.
- **By build, closed trades:**
  - 8a6849b: 6 trades, −100,536,289 lamports;
  - a25eb17: 15 trades, +27,606,913;
  - 7004b16: 7 trades, −80,432,850, including one rug at −47,168,631.
- **Execution on the fixed builds:**
  - entry lands 5–6 slots after migration;
  - sell decision→send takes about 30 ms;
  - sim and live agree on exit decisions in 27/28 trades (job #187).
- **Live build:** faa3192, which includes the mark from our own buy tx and log-only drift.

The owner plans to add 1 SOL, which puts the wallet at about **1.36 SOL**.

## 3. Proposed limits for the 0.25 SOL step

These limits are enforced in executor code and config, not by a person. They are installed by a pinned re-pin as usual.

| Limit | Value | Why |
| --- | --- | --- |
| size per entry | **0.25 SOL** | 5× the probe, half the DEC-018 trial size |
| max open | **2** | caps exposure at about 0.52 SOL at cost |
| attempts at 0.25 | **40** | enough to measure size costs; a separate count, never pooled with the 0.05 trades |
| loss cap (realized, this step only) | **0.35 SOL** | about 7 full stop-losses at −0.30 to −0.40 |
| priority | 500,000 per side | landing conditions unchanged |
| exits / entry | frozen tp50/sl30, frozen threshold, no veto | the studied strategy, unchanged |
| end | the existing DEC-019 end instant, 2026-10-12T00:00Z, unless the owner extends it | |
| STOP/HALT, Discord watchdog | unchanged | |

**Wallet floor with about 1.36 SOL:**
- Hard worst case: cap hit, plus 2 open positions at zero: 1.36 − 0.35 − 2 × 0.252 ≈ **0.50 SOL** left.
- Realistic bad case: cap hit, with the open positions stopping near −40%: about **0.80 SOL** left.

## 4. Options for the owner

**Option A (default, recommended): start after the forward read.**
- Start the size step only if EXP-012's FINAL forward read (~2026-10-16) is a PASS and quant-proof agrees.
- If the read fails, the size step does not happen, and the funds stay for the next candidate or are withdrawn.

**Option B: a short cost-measurement run now.**
- 20 attempts at 0.25 SOL, loss cap 0.20 SOL, max open 2, before the forward read.
- Purpose: measure the size-proportional costs (exit lag, sell shortfall, MEV) early, so a PASS on 10-16 can go straight to a trial.
- Risk: it puts real money behind an edge that is not yet proven out-of-sample. With the 0.20 cap, about 0.65 SOL stays in the wallet in the hard worst case.
- This is the owner's call. The manager does not recommend it over Option A.

## 5. What the manager prepares for either option

1. Config and code for the 0.25 step:
   - a separate attempt counter and a separate loss cap, so the 0.05 probe and the 0.25 step never pool;
   - review, a calibration replay and a manifest, as for every executor change.
2. A per-size report:
   - entry slippage vs quote by size;
   - exit fill vs quote;
   - sim-vs-live gap by size, using `tools/probe_sim_calibration.py`;
   - sandwiching signs: other wallets' trades in our landing slot.
3. A DEC-019 §7 lab note at the end, grouped by build and by size.

## 6. Honest expectations

At 0.25 SOL the fee drag falls to about 0.4%. If the exploration edge holds out-of-sample, that is where it can show up net of fees. The 22 trades on the two fixed builds total −52,825,937 lamports, about −0.0024 SOL per trade. That is within noise for heavy-tailed trades, but it does not support the edge either. The size step exists to measure size costs and, with Option A, to trade only after the held-out forward read supports it.
