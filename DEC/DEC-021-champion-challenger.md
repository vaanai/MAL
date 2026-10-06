# DEC-021: Champion/challenger on the second forward walk (proposal, rev 2)

| Field | Value |
| --- | --- |
| **Status** | **Decided 2026-10-06** (owner, in the manager session): approved as written, and the owner **delegates §6(f) to the manager**. A challenger that meets every condition in §6(a)–(e) and §7 replaces the champion without waiting for the owner. The owner's own reviewers check the work in parallel. Rev 4 passed quant-proof as sound. |
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

This uses per-trade SD about 0.02 SOL at 0.05 SOL size, n = 100, a normal approximation, one-sided, and **Holm rank 1 at the per-walk family α = 0.025 (0.025/3 = 0.00833)**. ρ is the per-migration correlation between the arms. It is near 0 for a different selector and high for a variant that shares most entries.

| true improvement per trade | power ρ=0 | power ρ=0.5 | power ρ=0.9 | trades for 80% power (ρ = 0 / 0.5 / 0.9) |
| --- | ---: | ---: | ---: | ---: |
| +0.001 SOL | 0.021 | 0.029 | 0.101 | 8,375 / 4,188 / 838 |
| +0.003 SOL | 0.091 | 0.186 | 0.832 | 931 / 465 / 93 |
| +0.006 SOL | 0.393 | 0.728 | 1.000 | 233 / 116 / 23 |

- **A different selector** (ρ ≈ 0): a one-week window does **not** reliably detect even +0.006 SOL per 0.05 SOL trade (power 0.393), and it needs about 233 trades for 80%.
- **A close variant** (ρ ≈ 0.9) is detectable from about +0.003.
- **Winner's curse floors at n = 100.** To pass, the observed difference must be at least 0.0068 (ρ = 0), 0.0048 (ρ = 0.5) or 0.0021 (ρ = 0.9) SOL per trade. A reported winning margin will usually overstate the true one.
- Small improvements need either far more forward data or a mechanism measured directly, such as a fee or latency change on live fills.

## Decision (proposed)

1. **Search (research-0, exploration only).**
   - Candidates come only from ledger-assigned exploration days. No forward-walk hour is in any derivation; each input view is listed with its sha256.
   - Every configuration is logged in `data/tries.jsonl`, and families are pre-registered.
   - Every deciding cell uses realistic measured costs: V pricing; exit lag 2; the live haircut at its conservative end (sell −16 bps of proceeds, within the measured −11..−16; entry +26.08 bps mean, job #175); both fail models.
2. **Confirmation before registration.** A challenger must PASS a one-shot read on a reserved, unread block under the promotion gate before it can be registered for a walk. This is the same bar EXP-012 met. A derivation screen alone is not enough.
3. **Registration.**
   - At most **3** challengers per walk. Each is frozen by md5 with its own `EXP-###`.
   - Each is merged before the first hour of the walk. The second forward walk, `[2026-10-16T01, …)`, is reserved by its own ledger row (PR #352), which must merge before 2026-10-16T01, with the owner named as "DEC-021 walk-2 family: the champion sim arm plus the registered challengers", before any hour is sealed.
   - Its 48 h feature buffer reads first-walk hours `[2026-10-14T01, 2026-10-16T01)` for features only. Under DEC-014(a) that is disclosed here as a non-owner read: no outcome from those hours is used.
4. **Shadow and the paired arm.**
   - Both arms are **simulated** on the walk's chain-complete hours on research-0, with the same scorer, size, k, haircut and both fail models.
   - The paired unit is per-migration P&L over every migration in the window, with 0 where an arm does not enter.
   - The live runner and executor on fast-0 do not change.
   - **Pre-declared minimum effect δmin:** +0.003 SOL per 0.05 SOL trade, scaled linearly with size (+0.015 at 0.25 SOL).
   - **Drift monitor (switch family):**
     - The champion's sim is compared with its own live fills on the same mints in the window.
     - It needs at least **20** live champion fills, otherwise the read is **NOT_DECIDABLE**.
     - If the **mean** live−sim residual is below −δmin/2 (−0.0015 per 0.05 SOL, scaled with size), or exit agreement is below 90%, the read is NOT_DECIDABLE.
   - **Sim optimism in (c):** each challenger trade's simulated P&L is adjusted by **min(0, r̄)**. r̄ is the mean live−sim residual per trade (live minus sim), scaled with size. The adjustment can only lower the book; a positive r̄ is never applied. r̄ comes from a named set of at least 20 closed fills from **one** build, cited by job id. In the switch family it is the window's champion fills. **No qualifying calibration exists yet:** faa3192 has n = 8, with mean −384,022 and median −209,670 lamports per trade (probe-calibration-2026-10-06). Until one exists, (c) in a primary-promotion-family read is **NOT_DECIDABLE**.
5. **Read.**
   - One pre-registered read per walk. The window length is set before the walk by a power calculation using the ρ and SD measured on exploration data. It is never shorter than 7 days, and the DEC states the power at the pre-declared minimum effect, even if it is below 0.5.
   - **Holm–Bonferroni across the k challengers at the per-walk family α = 0.025** applies both to the paired test (b) and to each challenger's own full-book mean > 0 test (c). Each test is a one-sided bootstrap p-value (10,000 draws, seed 1, both fail models) at or below its Holm threshold. At rank 1 that is 0.00833, a one-sided 99.17% bound. The base gate (CI90, 1,000 draws, seed 1) applies on top. Days and ex-top-3 are reported per arm.
   - The window is sized in **migrations**, because the paired unit is per migration.
   - No interim peeking.
6. **Switch rule (owner).** A challenger replaces the champion only if all hold:
   - (a) at least **100** closed challenger trades in the window;
   - (b) the paired challenger − champion one-sided bootstrap p-value is at or below its Holm threshold at family α = 0.025, under both fail models;
   - (c) its own book, after the §4 residual subtraction, has its mean > 0 test at or below its Holm threshold and clears the full promotion gate;
   - (d) the drift monitor is clean;
   - (e) quant-proof agrees;
   - (f) ~~the owner says yes~~ **delegated to the manager (owner, 2026-10-06).** Before any switch, the manager posts a notebook decision and a Console entry with every §6 number. The re-pin is still Helm's: full sha, manifest and steps, at 0 open positions.
7. **Serial gatekeeping and pre-live checks.**
   - The switch family opens only if EXP-012's FINAL read passes on both book (A) and book (B) (DEC-016 Am.4) and quant-proof agrees.
   - Otherwise there is no gated champion, and the challengers are read as a **primary promotion family** (Holm at k, full gate, both fail models). A pass then leads to the normal DEC-018 path, not a "switch".
   - Before any swap, the challenger passes DEC-016 Am.1 §5, Am.3(a)/(b) and Am.4 at its own operating point.
   - A challenger that changes priority fee or entry k first needs a live calibration at that setting, which is a DEC-019 amendment for the owner and Helm.
   - Swaps are pinned re-pins at 0 open positions, at a planned boundary. Never mid-probe.
8. **Cumulative error.** This DEC covers at most **2** walks. Each walk's family, whichever opens (switch or primary promotion), is tested at α = 0.025, Bonferroni across walks, so the overall false-switch-or-promotion rate is ≤ 0.05. A third walk needs a new DEC.
9. **Not live evidence.** A paper win is not live evidence. Size stays governed by DEC-020.

## Owner decision (2026-10-06)

§6 and §8 are approved as written, and §6(f) is delegated to the manager. Size and funding stay governed by DEC-020 and the owner: a challenger swap never changes size or wallet.

## Open for the owner (historical)

- Approve §6 (the switch rule) and §8 (two walks at α = 0.025 each), or change them.
- The first candidates are EXP-015 (pooled retrain, PR #352) and EXP-013 (curve entry), but only if their screens and confirmation reads pass.
