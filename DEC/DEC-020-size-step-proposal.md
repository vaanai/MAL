# DEC-020: Size step for the live probe at 0.25 SOL per entry (proposal)

| Field | Value |
| --- | --- |
| **Status** | **Decided 2026-10-06: Option A** (owner, relayed with Helm's note in the manager session). The 0.25 SOL step waits for the EXP-012 FINAL forward read (~2026-10-16) and a quant-proof agreement. **No new funding before then.** The 0.05 probe keeps running on faa3192 until its own stops (90 attempts, 0.25 SOL cap, 2026-10-12T00:00Z). See §7. |
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
- **Live build:** `faa319227eee420319eed06e85774f64dd2273b1`, live since 2026-10-05T23:09:56Z. It is #331 (mark from our own buy tx) plus #332 (log-only drift).
  - **Re-pin record (Helm):** all 13 manifest hashes match, installer RC=0 with "manifest verified", keyless dry run passed, `mode=LIVE` with unchanged limits, STOP removed at 23:10:32Z, previous build 7004b16 kept for rollback.
  - **Calibration replay that #331 required:** job #187, run on #331 at 0ab8ad1. #332 only adds logging. See [probe-calibration-2026-10-05.md](../ARTIFACTS/lab/probe-calibration-2026-10-05.md).
  - **No faa3192 trades** are in the table above; they had not happened when this was written.

The owner plans to add 1 SOL, which puts the wallet at about **1.36 SOL**.

## 3. Proposed limits for the 0.25 SOL step

These limits are enforced in executor code and config, not by a person. They are installed by a pinned re-pin as usual.

| Limit | Value | Why |
| --- | --- | --- |
| size per entry | **0.25 SOL** | 5× the probe, half the DEC-018 trial size |
| max open | **2** | caps exposure at about 0.52 SOL at cost |
| attempts at 0.25 | **40** | enough to measure size costs; a separate count, never pooled with the 0.05 trades |
| loss cap (realized, this step only) | **0.35 SOL** | about **3.5–4.7** full stop-losses. At 0.25 SOL one stop at −0.30 to −0.40 costs 0.075–0.10 SOL, and one rug like 7004b16's C71Lk8Ko costs about −0.2318 SOL. See §3a. |
| priority | 500,000 per side | landing conditions unchanged |
| exits / entry | frozen tp50/sl30, frozen threshold, no veto | the studied strategy, unchanged |
| end | the existing DEC-019 end instant, 2026-10-12T00:00Z, unless the owner extends it | |
| STOP/HALT, Discord watchdog | unchanged | |

**Wallet floor with about 1.36 SOL:**
- Hard worst case: cap hit, plus 2 open positions at zero: 1.36 − 0.35 − 2 × 0.252 ≈ **0.50 SOL** left.
- Realistic bad case: cap hit, with the open positions stopping near −40%: about **0.80 SOL** left.

## 3a. Expected attempts before the cap (replay, job #190)

**Correction.** The first version of this DEC (#333) said the 0.35 SOL cap covers "about 7 full stop-losses". That was the count at 0.05 SOL, not 0.25. Warden's review caught it, and the corrected numbers follow.

**Method:**
- Take the 22 closed trades on the fixed builds (a25eb17 and 7004b16), which sum to −52,825,937 lamports at 0.05 SOL.
- Scale each to 0.25 SOL: the price-move part ×5, with fees fixed at 1,010,000 lamports per round trip.
- This leaves out every size-proportional cost (exit lag, sell shortfall, MEV) and the extra price impact of a 5× order, so real results would be somewhat worse.
- Scaled sum: −0.1752 SOL, mean −0.00797 SOL per trade.
- The cap is checked on realized loss at buy time, as the executor does.

| Sequence | Cap 0.35 SOL (40 attempts) | Cap 0.20 SOL (Option B, 20 attempts) |
| --- | --- | --- |
| The 22 fixed-build trades in their real order | all 22 made, cap not hit | cap hit; **12** attempts made |
| The 7004b16 sequence alone (7 trades, sum −0.3739 SOL at 0.25) | cap hit after all **7** | cap hit; **6** of 7 made |
| Bootstrap, 10,000 random 40- or 20-trade sequences from the 22 | attempts p10 **4**, p50 **16**, p90 40, mean 20.4; P(cap hit) **0.737** | attempts p10 **2**, p50 **7**, p90 20, mean 10.1; P(cap hit) **0.733** |

**Reading:**
- If the fixed builds' live record is representative, the 0.25 step most likely stops early: about 16 trades at the median with the 0.35 cap, and 7 with the 0.20 cap. Both caps are hit about 3 times in 4.
- Option B's 20 attempts would most likely end after about 7 trades. That is too few to measure exit lag, sell shortfall or MEV, which is the reason for running it.
- The caps are left as proposed so the owner decides with correct numbers. Raising a cap is a separate owner decision.
- All of this uses the fixed builds' live record. Under Option A, the step only runs after a PASS on the forward read, which is evidence that this record may be worse than the strategy's true rate. That has not been measured.

## 3b. The 0.05 probe during the 0.25 step

Proposed: **the 0.05 probe stops (STOP) for the whole 0.25 step**, so the two never share the wallet.

If both ran at once, the hard worst case would include the 0.05 probe's remaining cap room (about 0.097 SOL at writing) and its 3 open positions (about 3 × 0.052 SOL). That gives 1.36 − 0.35 − 2 × 0.252 − 0.097 − 3 × 0.052 ≈ **0.25 SOL** left, instead of about 0.50.

## 4. Options for the owner

**Option A (default, recommended): start after the forward read.**
- Start the size step only if EXP-012's FINAL forward read (~2026-10-16) is a PASS and quant-proof agrees.
- If the read fails, the size step does not happen, and the funds stay for the next candidate or are withdrawn.

**Option B: a short cost-measurement run now.**
- 20 attempts at 0.25 SOL, loss cap 0.20 SOL, max open 2, before the forward read. Per §3a, it most likely stops after about 7 trades (p50; P(cap hit) 0.733 in the bootstrap).
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

## 7. Decision (2026-10-06): Option A, and the same-day package

**Decision.** The owner chose **Option A** on 2026-10-06, relayed together with Helm's note:
- The 0.25 SOL step starts only after the FINAL forward read (~2026-10-16T02Z) is a PASS and quant-proof agrees.
- There is no new funding before then.
- Option B is not taken.
- The 0.05 probe continues on `faa319227eee420319eed06e85774f64dd2273b1` at 0.05 SOL under DEC-019 Amendments 1/1a. It ends at its own stops, at the latest 2026-10-12T00:00Z.
- DEC-018's trial details are still due about 2026-10-14.

**If the read passes, the owner gets a same-day proposal. The manager prepares it before 10-16, and it contains:**

1. **State at the time.** Probe final attempts and realized loss, by build. Wallet balance from Helm's read. Wallet when this was recorded: **about 0.35 SOL** (Helm, 2026-10-06).
2. **Funding needed.** The hard worst case is the loss cap plus 2 open positions at zero: 0.35 + 2 × 0.252 = **0.854 SOL**, plus about 0.05 SOL kept for transaction fees and rent.
   - The wallet must hold about **0.90 SOL** at the start, so the floor never goes negative.
   - At about 0.35 SOL today that means at least **about 0.55 SOL** of new funding.
   - The 0.05 probe can still lose up to its remaining cap room before 10-12. At 10-06T00:33Z that room was 0.25 − 0.116494 = 0.133506 SOL. In that worst case the need rises to about **0.69 SOL**.
   - The owner's planned 1 SOL covers both. The figure is recomputed from the real wallet on the day.
3. **Worst case.**
   - Hard: the cap is hit and both open positions go to zero, leaving the wallet minus 0.854 SOL.
   - Realistic bad case: the cap is hit and the open positions stop near −40%.
   - Both are recomputed on the day, along with the job #190 replay's attempts-before-cap (p50 16 at the 0.35 cap, P(cap hit) about 0.74).
4. **Loss cap and watchdog.** The step's realized loss cap stays **0.35 SOL**, counted separately from the 0.05 probe.
   - Proposed `LOSS_ALERT_SOL` for Helm's `mal-probe-watch.timer`: **0.25 SOL**, about 71% of the cap, roughly 2.5–3 full stops at 0.25.
   - The watchdog must read the step's own counter, not the probe's.
5. **Pinned sha and manifest.** The DEC-020 limits are in executor code, not only in config:
   - a separate limit set (size 0.25, max_open 2, 40 attempts, cap 0.35, an end instant the owner sets);
   - a separate state file and fill-log tag, so the 0.05 and 0.25 counters never pool;
   - the never-re-buy list carried over.

   The change is built and reviewed before 10-16, including a security review. It is **not installed**. The proposal gives Helm the full sha, the 15-line manifest (13 today, plus the dec020 config and its drop-in) and the steps. The install is a re-pin at 0 open positions, after the probe has ended.

   **Code status:** the limits profile is in [#348](https://github.com/vaanai/MAL/pull/348). It is **not installed**. `DEC020_END_MS` is `None` in code, so dec020 refuses to start until the owner's end instant is written there in a reviewed one-line commit (a new sha).
6. **The read itself.** The FINAL verdict, quant-proof's note, and the V book under DEC-016 Am.4 at the measured live k.

Nothing in this section changes the gate, DEC-016, or DEC-018 §1.
