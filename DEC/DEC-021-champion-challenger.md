# DEC-021: Champion/challenger on the second forward walk (proposal)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-06.** It needs the owner's yes, because §5 decides what may replace the live strategy. |
| **Decider** | Vaan (owner) for §5. The Claude manager runs the rest. |
| **Builds on** | DEC-014 (ledger, multiplicity), DEC-016 (forward walk), DEC-017 (gatekept secondary family), DEC-019 (probe), DEC-020 (size step, Option A). |
| **Does not amend** | The promotion gate, EXP-012's 10-16 read, DEC-016 Amendment 2 (no peeking at `[10-06, 10-16)`), or DEC-020. |

## Context

- The live probe and the simulator agree. On the newest build (faa3192), the simulator reproduces the live exit decision on 8 of 8 closed trades, and the live−sim P&L gap is between −1,299,687 and +407,184 lamports per trade (job #209). Across all 36 closed live trades, exits agree 35/36.
- At today's operating point (k about 6, 0.05 SOL, 500k priority) the strategy is about break-even in both.
- So the simulator can now be used to test challengers. But "what would have worked" on past days is a best-of-N search. The 9 tuning days have been tried 74 times, and today every new idea on them came back flat or negative. A challenger can only replace the live strategy after it wins on data it has never seen.

## Decision (proposed)

1. **Search (research-0, exploration days only).**
   - Candidates come only from ledger-assigned exploration days (EXP-015 plan v2 grows the pool to about 35 days).
   - Every configuration is a logged try in `data/tries.jsonl`.
   - Families are pre-registered: entry k, priority fee, selector, exits.
   - Every deciding cell uses the most realistic measured costs: V pricing, exit lag 2, and the live haircut (sell −16 bps, entry +26.08 bps, from jobs #175 and #209). Nested LODO, with the trial count reported.
2. **Challengers.**
   - At most **3** per walk. Each is frozen by md5 and has its own `EXP-###`.
   - Each is merged before the first hour of the second forward walk (from 2026-10-16T01, a new ledger row reserved before any hour is sealed).
   - A challenger needs a passed derivation screen (DEC-017 §5) on exploration data. A confirmation read on a reserved block is better but not required.
3. **Shadow.**
   - Challengers are scored **offline on research-0** on the second forward walk's chain-complete hours, with the same calibrated simulator, on the same migrations the champion trades. The live runner and the probe executor on fast-0 do not change.
   - The champion's simulated trades are compared with its live fills every week. The per-trade live−sim residual is reported, so calibration drift is visible.
4. **Read.**
   - One pre-registered read per walk, on a window fixed at registration (proposal: 7 days).
   - Each challenger is tested on the **paired per-mint difference** against the champion, plus its own full-book promotion gate.
   - Holm–Bonferroni across the k registered challengers. One-sided bootstrap, 10,000 draws, seed 1, under both fail models.
   - No interim peeking. The DEC-016 Amendment 2 rule applies.
5. **Switch rule (owner).** A challenger replaces the champion only if all of these hold:
   - (a) at least **50** closed challenger trades in the window;
   - (b) the challenger − champion paired CI90 lower bound is **> 0** under both fail models after calibrated costs, Holm-adjusted;
   - (c) its own book clears the promotion gate;
   - (d) quant-proof agrees;
   - (e) the owner says yes.

   The swap is a pinned re-pin at 0 open positions, at a planned boundary: never mid-probe and never before EXP-012's 10-16 read. If no challenger passes, the champion stays.
6. **What this is not.** A challenger's paper win is not live evidence. A swap is followed by the same live calibration as every build. Size stays governed by DEC-020.

## Why at most 3

Compute is not the limit; research-0 can run many replay searches. The limit is the forward migration stream: about 20–40 tradable migrations a day, shared by every challenger. Each extra challenger tightens the Holm bar. With 3, each can reach 50+ trades in about a week and still face a fair test. With 10, the winner would mostly be the luckiest.

## Open for the owner

- Approve §5 (the switch rule) and the 7-day window, or change them.
- The first challengers are expected from EXP-015 (retrain on about 35 days) and EXP-013 (curve entry), if their screens pass.
