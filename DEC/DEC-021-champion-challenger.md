# DEC-021: Champion/challenger on the second forward walk (proposal, rev 2)

| Field | Value |
| --- | --- |
| **Status** | **Decided 2026-10-06** (owner, in the manager session): approved as written, and the owner **delegates §6(f) to the manager**. A challenger that meets every condition in §6(a)–(e) and §7 replaces the champion without waiting for the owner. The owner's own reviewers check the work in parallel. Rev 4 passed quant-proof as sound. **Amendment 1 (2026-10-08, owner O2/O3):** CAP-PICK (EXP-022) is the walk-2 primary-promotion arm; see the end of this file. |
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

## Amendment 1 (2026-10-08): CAP-PICK (EXP-022) as the walk-2 primary-promotion arm (owner O2/O3)

**Approved by the owner on 2026-10-08** (O2, and O3 with its pre-approved fallback, SYN:458: "Yes, if the Part 1 can merge before 10-10T00Z; otherwise count from 10-16T01"), with the audit recommendations (MiScusi notebook `n_vS9qHGmF7-jinQ`; `ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md:457-458`). It covers **CAP-PICK, [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md), only**. Every other challenger stays under the text above.

**Why.** Two parts of the text above cannot be met for CAP-PICK (SYNTHESIS.md:250, D3):
- §2 requires a backward-block PASS. DEC-014 bars an EXP-012 retune from any further sealed block (`DEC/DEC-014-holdout-ledger-and-multiplicity.md:109`), and every block predates the 2026-10-02 program upgrade.
- §5's single read at α 0.025 has no early stop.

The audit judge also showed that a trade-level p fails to hold α under day clustering (`ARTIFACTS/lab/audit-2026-10-08/capv_JUDGE.md:115-123`).

For CAP-PICK only:

- **§2 (confirmation before registration).** No backward-block PASS is required. CAP-PICK is a **kill test**, registered directly as the walk-2 primary-promotion arm. Its exploration evidence is in-sample for the book design (capv_JUDGE.md:16), and it is never cited as confirmation.
- **§3 (registration and window).**
  - **Counted window:** `[2026-10-16T01, 2026-11-06T01)` (Option Y), set by the line `EXP022_COUNT_START: 2026-10-16T01` in EXP-022 §0. It lies wholly on walk 2.
  - **Option X rejected.** Option X (`[2026-10-10T00, 2026-10-31T00)`) was considered and rejected on 2026-10-08. The FINAL would have priced most of look 1's counted picks before look 1, and the tools could not be reviewed in time (EXP-022 §0).
  - **Deadline.** EXP-022 merges, and its E0 and monitor preconditions are met, before 2026-10-16T01. Otherwise CAP-PICK is withdrawn.
  - **Champion sim arm.** It is **not registered for walk 2**. The walk-2 family is CAP-PICK alone.
  - **Feature buffer.** The 48 h buffer above (`[2026-10-14T01, 2026-10-16T01)`, features only, read after the FINAL is written) covers CAP-PICK's first-day boot reads.
  - **A11 October check.** It is report-only and is not part of this family. It needs its own DEC-016 amendment.
  - **Not a DEC-017 secondary.** Its window differs from the FINAL's.
- **§4 (sim optimism in (c)), with §1's haircut.**
  - **Calibration set.** The named set is build **faa3192's closed round trips, priced by E1**: `tools/probe_sim_calibration.py` at `c745411` (#463). The command, fills sha256, tape dir and output field are pinned in EXP-022 §8.
  - **r̄ and n.** r̄ = `aggregate["faa3192"]["pnl_gap_lamports_live_minus_sim"]["mean"]`. If its n is below 20, (c) is NOT_DECIDABLE and no look runs.
  - **Correction.** Each filled attempt in every binding cell loses the **larger** of two amounts:
    - (a) §1's live haircut: sell −16 bps of proceeds and entry +26.08 bps (job #175), as `tools/exp012_backcheck.py:495-505` computes it;
    - (b) max(0, −2 × r̄), where the 2 scales r̄ linearly from 0.05 to 0.1 SOL.
  - **Why the larger.** It is the larger, not the sum, because both estimate the same live−sim gap. Each is reported separately. This is conservative, and **§1 is honoured**.
  - **Timing.** E1 runs after the 10-16 FINAL is written (DEC-016:162) and after the A11 read. No look runs before E1 is recorded.
  - **Drift monitor.** §4's drift monitor belongs to the switch family and does not apply.
- **§5 (read).** Three pre-registered looks, at days 7, 14 and 21, replace the single read and the "no interim peeking" line.
  - **Deciding p:** a day-level p, one-sided.
    - The clusters are 24 h blocks from 2026-10-16T01 (7 / 14 / 21 blocks). A block with no attempts is dropped; W is the number of blocks with at least one attempt (reported), and df = W − 1.
    - m_b is the mean SOL per attempt in block b, and sd is the sample SD of the W block means (ddof 1).
    - t = mean(m_b) / (sd / √W), and p = P(T_{W−1} ≥ t).
    - The larger of the flat and pressure p decides.
  - **Thresholds:** p ≤ 0.005 at day 7, 0.008 at day 14 and 0.012 at day 21. The Bonferroni sum is 0.025, the family α, with **Holm k = 1**.
  - **Gate:** at the look, the full promotion gate must also hold under both fail models, with "≥ 5 UTC days" counting UTC dates. EXP-022's binding ex-best-day and 1.9 s conditions apply too.
  - **Reported only:** the one-sided bootstrap p (10,000 draws, seed 1). It is reported at every look and never decides.
  - **Futility:** day-7 futility (flat and pressure means ≤ 0) is non-binding. It pauses build spending only.
  - **Window:** fixed in time (21 days), not in migrations. Its power is stated in EXP-022 §14: day-level p, template between-day SD, false pass 0.8%, power 5.1% at +1.0% flat and 22.2% at +1.9% flat (capv_JUDGE.md:119). Those figures were simulated with a binding day-7 futility (capv_JUDGE.md:115), and without the §4 correction above.
- **§7 (serial gatekeeping).** Not changed. Its second branch applies. The 10-16 FINAL is reported compromised (DEC-016 Am.2) and cannot by itself support live, so walk 2 has no gated champion. CAP-PICK is read as a primary-promotion family at k = 1, and a pass leads only to the DEC-018 path. The pre-live checks are at CAP-PICK's own operating point, and the 55k priority first needs a live calibration under a DEC-019 amendment.
- **§8 (cumulative error).** CAP-PICK is the walk-2 family, tested at α = 0.025, and no other arm joins it. The Bonferroni-across-walks rule is unchanged.

**Not changed:** §1 (honoured through §4 above), §6, §9, the promotion gate, and DEC-016 Am.2 and Am.3. No forward-1002 hour is read for CAP-PICK before the FINAL is written.

## Amendment 2 (2026-10-08): §8's second α slot goes to EXP-024 (H5-BOOSTFLOOR)

Proposed by the manager on 2026-10-08. The owner's OK is asked by 2026-10-09T20:00Z; if he has not answered, the manager decides under the owner's 2026-10-08 mandate and records it in the notebook ([DEC-023](DEC-023-h5-family.md)). It takes effect on merge, before 2026-10-10T00:00Z. It covers **[EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) only**. No hour was read to write it.

- **§8 (cumulative error).** The two α = 0.025 slots are now used: **EXP-022 holds the first, EXP-024 the second.** The overall rate across the promotion-eligible October families stays ≤ 0.05. A third family or walk needs a new DEC.
- **Inside EXP-024's slot.** α = 0.025 is split over two looks, 0.020 at Look 1 and 0.005 at Look 2, with k = 1 and no Holm (DEC-023 §2).
- **The walk-2 family is unchanged.** Amendment 1 says no other arm joins CAP-PICK. EXP-024 is not an arm of that family. It is a separate DEC-014 family with its own α, its own reads and its own tool. Its Look 1 reads forward-1002, and its Look 2 reads walk 2 only after EXP-022's read has ended (EXP-022 Amendment 2). EXP-022's thresholds (0.005, 0.008, 0.012), counted window and seal are unchanged.
- **§2, §3, §6 and §7 do not apply to EXP-024.** It is not a challenger to a champion and has no sim arm to pair. As in Amendment 1 for CAP-PICK, no backward-block PASS is required (DEC-023 §6 gives the reasons). A pass leads only to the DEC-018 path, the second branch of §7. The 10-16 FINAL stays reported compromised (DEC-016 Am.2).

**Not changed:** §1, §4, §5 (for EXP-022), §6, §9, the promotion gate, and DEC-016 Am.2 and Am.3.

## Amendment 3 (2026-10-09): a third alpha slot for EXP-025 (C1-NF), subject to the owner's written OK

Drafted 2026-10-09. The owner's OK on DEC-025 item O1 (a third slot, two looks) was confirmed by the manager the same day (`OWNER_DECISION_CONFIRMED` in DEC-025). It takes effect on merge, with quant-proof OK on its final head, before 2026-10-10T00:00Z. No hour was read to write it.

- **Section 8 (cumulative error).** Slot 1 is EXP-022, slot 2 is EXP-024 (Amendment 2). **Slot 3, alpha 0.025, is the register of [DEC-025](DEC-025-c1nf-family.md) section 2**, split over two looks (0.005 and 0.020, pinned in EXP-025 section 0), and whose only member at this amendment is [EXP-025](../EXP/EXP-025-c1nf-part1-prereg.md). The overall rate across the promotion-eligible October families is then bounded by 0.075, not 0.05. A fourth slot needs a new DEC.
- **Unchanged.** EXP-022's thresholds, counted window and seal; EXP-024's 0.020 / 0.005 split. Sections 2, 3, 6 and 7 do not apply to EXP-025 (it is not a challenger to a champion). As in Amendment 1 and Amendment 2, no backward-block PASS is required; a pass leads only to the DEC-018 path and the owner's yes.
