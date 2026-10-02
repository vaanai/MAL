# DEC-017: more models scored on the same forward hours, gatekept behind EXP-012

| Field | Value |
| --- | --- |
| **Status** | **Active** (2026-10-02), after a `quant-proof` review of the first draft. |
| **Decider** | Claude manager (`mal-research-0`), on the owner's direction (2026-10-02: "once the walk starts, let's push to get a couple more models testing at the same time to use our data to the fullest") |
| **Date** | 2026-10-02 |
| **Amends** | **DEC-014(b)**: secondary cells read at the forward read form a gatekept family behind EXP-012, not one Holm family with it. **DEC-016 Amendment 1 §3**: "if another book is a live candidate at the same read, Holm applies" is replaced by §2 below for books registered under this DEC. **DEC-016 §2**: the forward walk's hours may be scored by the secondaries' scorer as well. |
| **Does not amend** | The promotion gate's thresholds; EXP-012's pre-registration, window `[2026-10-06T00, 2026-10-16T00)`, single read and verdict; the paper-only fence; DEC-016 Amendment 2 (no peeking), which applies to every secondary. |

## Context

- The forward walk (DEC-016, MiScusi job #71) produces chain-complete hours. Scoring more models on them costs no Helius credits.
- Each extra model read is another test. If the secondaries formed a second family at α = 0.05 next to EXP-012's α = 0.05, the chance of at least one false PASS at the read would be up to about 0.10. If they joined EXP-012's family, EXP-012's pre-registered k = 1 bar would tighten after the fact. Neither is acceptable.

## Decision

1. **Serial gatekeeping.**
   - EXP-012 is read first, at α = 0.05, k = 1, exactly as pre-registered.
   - The **secondary family S is read only if EXP-012 PASSes.** It is then tested with Holm–Bonferroni at α = 0.05 (one-sided bootstrap p-value, 10,000 draws, seed 1, under both fail models), and each secondary must also clear the promotion gate.
   - If EXP-012 fails, every secondary's verdict is "not tested (gate closed)". Its rows are still reported, as exploration only.
   - The probability of at least one false PASS at the read stays at 0.05.
2. **One family per walk, one read.**
   - Every secondary scored on the forward walk belongs to family S, read once at EXP-012's read time (about 2026-10-16T02Z) on the window `[2026-10-06T00, 2026-10-16T00)`.
   - k is fixed at the registration deadline **2026-10-05T23:59Z**. No secondary is added after it, and none is withdrawn once registered.
   - A model that misses the deadline needs a new walk window and a new DEC.
3. **Pre-registration.** Each secondary has its own `EXP-###`, merged before 2026-10-05T23:59Z, which is before any **window** hour (≥ 2026-10-06T00) is sealed. Buffer hours from 2026-10-04T00 may already be sealed; they carry no counted trade. It fixes:
   - **Artifacts:** the frozen model, threshold, features and execution, by md5.
   - **Derivation data:** the exploration-only data, with the sha256 of every input view. It states that no row from EXP-012's holdout or `ARTIFACTS/exp012/read/` was used.
   - **Tries:** the number of variants tried, every one logged in the tries log.
   - **Tested quantity**, per point 4.
   - **Overlap:** the expected overlap of its entered set with EXP-012's, measured on the exploration pool.
4. **Tested quantity for variants that share EXP-012's entries.** The full book must clear the gate, **and** the increment must be positive:
   - **(b) Exit variant:** the paired per-mint difference (variant − EXP-012 reference) has a 90% CI lower bound > 0 under both fail models.
   - **(c) Looser threshold:** the marginal band `[t_loose, t_frozen)` clears the gate by itself.
   - **(a) Refit on the expanded pool:** the full-book gate only, with its overlap with EXP-012's entered set reported.
5. **Derivation screens, which must pass on exploration data before registration.**
   - **Exit variants:** the exit is chosen by **nested LODO**, picking on n−1 days and scoring the held-out day. The nested estimate of the selection procedure's advantage over `tp50_sl30` must be > 0 under both fail models and on the fast source alone. An exit-side latency check is also required.
   - **Refit:** a period-transfer screen. Train on the August days, test on the September days, and the reverse. Report the selected fraction per period, and the September-only and fast-only screens must pass. The exact days are pinned by sha256.
   - **Looser threshold:** chosen on exploration data only. Not tuned to restore the holdout's selected fraction.
6. **If more than one book PASSes:** the live-trial candidate order is fixed now. EXP-012 comes first, then the secondaries in registration order. No best-of choice after the read.
7. **One scorer path.** Secondaries use `tools/exp012_forward.py`'s machinery, generalized to take an artifact dir and execution spec from the pre-registration. It keeps the pinned window, the INTERIM/FINAL rules, a lock and an external FINAL ledger per experiment.
8. **Ledger.**
   - The forward walk row keeps EXP-012 as owner.
   - Each secondary's read is disclosed as a dated amendment in EXP-012's file and in the row's Status.
   - The row's "read only by" names the generalized scorer.

## Consequences

- More candidates per forward day at no credit cost, with the read's overall false-PASS chance held at 0.05.
- A secondary can promote only if EXP-012 passes. That is the price of not loosening the error rate.
- Variants that share EXP-012's entries must show an increment, not restate EXP-012's trades.
- A refit and EXP-012 read on the same 10 days share the day regime, so a joint pass is closer to one observation of those days than to two.
