# DEC-021: Champion/challenger on the second forward walk (proposal, rev 2)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-06, rev 2** after the quant-proof review of rev 1, which asked for 9 edits, all applied here. It needs the owner's yes, because §6 decides what may replace the live strategy. |
| **Decider** | Vaan (owner) for §6. The Claude manager runs the rest. |
| **Builds on** | DEC-014, DEC-016 (Am.1–4), DEC-017, DEC-019, DEC-020 (Option A). |
| **Amends** | **DEC-016 Am.1 §5** and **DEC-018 §1**, only in this respect: a challenger that clears §6 may replace the live strategy, after the same pre-live checks the champion had to pass (§7). |
| **Does not amend** | The promotion gate's thresholds, EXP-012's 10-16 read, DEC-016 Am.2 (no peeking at `[10-06, 10-16)`), or DEC-020. |

## Context

- On the newest build the simulator reproduces the live exit decision on 8/8 closed trades, and on 35/36 overall. The faa3192 live−sim P&L gap is between −1,299,687 and +407,184 lamports per trade ([probe-calibration-2026-10-06.md](../ARTIFACTS/lab/probe-calibration-2026-10-06.md), job #209). It leans negative.
- **Live record:** all 36 closed trades total −174,249,232 lamports, about −0.0048 SOL/trade (job #208).
  - The 30 trades on the fixed builds total −73,712,943 lamports, about −0.0025/trade, including one rug at −47,168,631.
  - The simulator predicted +0.00058/trade at this point (#327).
- "What would have worked" on past days is best-of-N search. The 9 tuning days have had 74 tries.

## What this can and cannot detect (read first)

With per-trade SD about 0.02 SOL at 0.05 SOL size (normal approximation, one-sided, Holm rank 1 at α/3):

| true improvement per trade | power at n = 100, ρ = 0.5 | power at n = 100, ρ = 0.9 | trades for 80% power (ρ = 0.5 / 0.9) |
| --- | ---: | ---: | ---: |
| +0.001 SOL | 0.052 | 0.156 | 3,528 / 706 |
| +0.003 SOL | 0.265 | 0.89 | 392 / 78 |
| +0.006 SOL | 0.808 | 1.0 | 98 / 20 |

ρ is the per-migration correlation between the two arms. It is high when they share most entries, as an exit variant does, and low for a different selector.

- **At our flow** (DEC-016 Am.1: about 75 trades a day), a one-week window reliably detects only **large** improvements: about +0.006 SOL per 0.05 SOL trade for a different selector, or about +0.003 for a variant that shares most entries.
- A challenger that passes will usually **overstate** its gain (winner's curse).
- Small improvements need either far more forward data or a mechanism that doesn't need a statistical win, such as a fee or latency change measured directly.

## Decision (proposed)

1. **Search (research-0, exploration only).**
   - Candidates come only from ledger-assigned exploration days. No forward-walk hour is in any derivation; each input view is listed with its sha256.
   - Every configuration is logged in `data/tries.jsonl`, and families are pre-registered.
   - Every deciding cell uses realistic measured costs: V pricing; exit lag 2; the live haircut at its conservative end (sell −16 bps of proceeds, within the measured −11..−16; entry +26.08 bps mean, job #175); both fail models.
2. **Confirmation before registration.** A challenger must PASS a one-shot read on a reserved, unread block under the promotion gate before it can be registered for a walk. This is the same bar EXP-012 met. A derivation screen alone is not enough.
3. **Registration.**
   - At most **3** challengers per walk. Each is frozen by md5 with its own `EXP-###`.
   - Each is merged before the first hour of the walk. The second forward walk, `[2026-10-16T01, …)`, is reserved by its own ledger row (PR #352) before any hour is sealed.
   - Its 48 h feature buffer reads first-walk hours `[2026-10-14T01, 2026-10-16T01)` for features only. Under DEC-014(a) that is disclosed here as a non-owner read: no outcome from those hours is used.
4. **Shadow and the paired arm.**
   - Both arms are **simulated** on the walk's chain-complete hours on research-0, with the same scorer, size, k, haircut and both fail models.
   - The paired unit is per-migration P&L over every migration in the window, with 0 where an arm does not enter.
   - The live runner and executor on fast-0 do not change.
   - **Drift monitor:** the champion's sim is compared with its own live fills on the same mints. If the median live−sim residual is below −0.002 SOL per 0.05 SOL trade, or exit agreement is below 90%, the read is **NOT_DECIDABLE**.
5. **Read.**
   - One pre-registered read per walk. The window length is set before the walk by a power calculation using the ρ and SD measured on exploration data. It is never shorter than 7 days, and the DEC states the power at the pre-declared minimum effect, even if it is below 0.5.
   - **Holm–Bonferroni across the k challengers** applies both to the paired test (b) and to each challenger's own full-book mean > 0 test (c). One-sided bootstrap, 10,000 draws, seed 1, both fail models. Days and ex-top-3 are reported per arm.
   - No interim peeking.
6. **Switch rule (owner).** A challenger replaces the champion only if all hold:
   - (a) at least **100** closed challenger trades in the window;
   - (b) the paired challenger − champion CI90 lower bound is > 0 under both fail models, Holm-adjusted;
   - (c) its own book clears the full promotion gate, Holm-adjusted;
   - (d) the drift monitor is clean;
   - (e) quant-proof agrees;
   - (f) the owner says yes.
7. **Serial gatekeeping and pre-live checks.**
   - The switch family opens only if EXP-012's FINAL read passes on both book (A) and book (B) (DEC-016 Am.4) and quant-proof agrees.
   - Otherwise there is no gated champion, and the challengers are read as a **primary promotion family** (Holm at k, full gate, both fail models). A pass then leads to the normal DEC-018 path, not a "switch".
   - Before any swap, the challenger passes DEC-016 Am.1 §5, Am.3(a)/(b) and Am.4 at its own operating point.
   - A challenger that changes priority fee or entry k first needs a live calibration at that setting, which is a DEC-019 amendment for the owner and Helm.
   - Swaps are pinned re-pins at 0 open positions, at a planned boundary. Never mid-probe.
8. **Cumulative error.** This DEC covers at most **2** walks, each at α = 0.025 (Bonferroni across walks), so the overall false-switch rate is ≤ 0.05. A third walk needs a new DEC.
9. **Not live evidence.** A paper win is not live evidence. Size stays governed by DEC-020.

## Open for the owner

- Approve §6 (the switch rule) and §8 (two walks at α = 0.025 each), or change them.
- The first candidates are EXP-015 (pooled retrain, PR #352) and EXP-013 (curve entry), but only if their screens and confirmation reads pass.
