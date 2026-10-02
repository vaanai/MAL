# DEC-016: EXP-012's forward book is scored on forward getBlock hours, by the read's own code

| Field | Value |
| --- | --- |
| **Status** | **Proposed** (2026-10-02). **Nothing starts before the owner's OK.** It spends paid Helius credits on forward data, and the owner reserved paid-feed changes to himself (via Helm, 2026-10-02). With the clean clock of Amendment 1 (2026-10-06T00:00:00Z), the walk must start by **2026-10-04T00Z**: 48 h of buffer, so every counted mint (created up to 24 h before its migration) also has 24 h of creator history. |
| **Decider** | Claude manager (`mal-research-0`). Owner confirmation asked 2026-10-02. |
| **Date** | 2026-10-02 |
| **Amends** | [DEC-015](DEC-015-forward-paper-on-fast.md) §1, only for **how EXP-012's forward book is measured**. DEC-015's runner track on `mal-fast-0` continues as the live-readiness track (§3). |
| **Does not amend** | The promotion gate, the paper-only fence, DEC-014 (ledger, single read), "never add forward books during a kill-review week" (nothing counts before 2026-10-05T05:00:00Z), and EXP-012 §9 (forward paper, never direct promotion). |

## Context

1. **The live tapes miss trades.** On 2026-10-01T17–19 the fast public tape held 84.759% of chain trades and Oracle's tape 66.241% ([note](../ARTIFACTS/lab/tape-coverage-chain-verdict-2026-10-02.md), DEC-015 §2.2 FAIL).
2. **EXP-012 was frozen and read on getBlock walks**, which are chain-complete. A forward book on an incomplete tape measures a different input from the one the read passed on.
3. **The runner cannot run EXP-012 as pre-registered** (review of `tools/forward_paper.py` on `main` at `e67b387`):
   - It has no model gate on `migrate` books.
   - Its fill model is time-based (receive time plus median chain latency, portal route, 0.001 SOL priority, a random 15% fail draw). The read's fill model is slot-based (slot+1 start, direct route, 0.0005 SOL per side, both fail models as expected values). Most of EXP-012's lift is fill prediction (98.9% vs 28.0% fill), so this difference matters.
   - Its hard size ceiling is 0.05 SOL (`HARD_MAX_POSITION_LAMPORTS`). EXP-012 is 0.5 SOL.
   - Some features cannot be rebuilt online exactly: the 32-minute watch-list truncation, and `creator_prior_mints_24h` after each 00:00Z restart.
4. **There is a precedent.** The migrate-direct forward book was scored offline on sealed forward hours at 0.5 SOL (`tools/migrate_direct_oos.py forward`).

## Decision

1. **Forward walk (on the owner's OK).** A getBlock follower on `mal-research-0`, run as a MiScusi job:
   - It walks each complete UTC hour about 5 minutes after it ends, into `/data/mal/blocks/forward-1002`, with the same walker code the training walks use.
   - Every sealed hour gets `backfill_verify --content` (0 flagged, 0 duplicates) and a sha256 line before any scorer reads it.
   - Cost: about 13.4k credits per hour, about 0.32M/day, about 9.7M/month, inside the ~40M/month budget, at `--rps 5`.
   - The same hours are the chain truth for further tape-coverage checks.
2. **Measurement (on owner confirmation).** EXP-012's forward book is the frozen model, threshold and execution of EXP-012 §2, scored by a forward scorer (`tools/exp012_forward.py`) that reuses `tools/exp012_score.py`'s table build and scoring path unchanged, on sealed forward hours only.
   - It checks the frozen artifacts' md5 (`ARTIFACTS/exp012/FROZEN.md5`) on every run.
   - Its rows are append-only, keyed by mint and migration time.
   - The gate is computed by `tools.exp011_score.compute_gate`, under both fail models.
3. **Clean clock: 2026-10-05T05:00:00Z.** Only migrations at or after it count. Hours before it are feature buffer only. The scorer's pool starts 48 h before the clean clock: `buffer_hours=24` for create-to-migration, plus 24 h of creator history (`creator_prior_mints_24h`). It reads no hour before 2026-10-03T05.
4. **Gate:** exactly CLAUDE.md's, under both fail models. A pass here is **forward simulated paper**, not live evidence. It earns the owner's decision on a small live trial, with the live-readiness track (§3) as a precondition.

## 3. The live-readiness track (DEC-015, unchanged in purpose)

Before any live order, the fast-0 runner must show that the replay's fill and latency assumptions hold on a real feed:
- an EXP-012 model gate inside the runner, with feature parity measured against the forward scorer;
- a gate-grade feed (redundant sockets or a getBlock tip follower, decided by the truth-1002 re-check);
- md5 equivalence of the existing books;
- its own memory slice;
- lag probation.

Its paper rows are compared with the forward scorer's rows for the same mints, to measure fill and entry-timing agreement. They are not a second gate.

## Consequences

- If confirmed, EXP-012's forward measurement no longer waits on the fast tape, the runner redesign or lag probation. It can count from 2026-10-05T05:00:00Z.
- Until the owner answers, a builder writes and tests `tools/exp012_forward.py`. It is needed either way, because it can also score a forward tape.
- About 9.7M credits/month go to the forward walk while it runs. Report: unit (MiScusi job id), credits per day, and what it enables (forward book plus continuous chain truth).
- If the owner declines, no forward walk runs, and EXP-012's forward book waits for the runner track and a gate-grade feed, as DEC-015 planned.

## Amendment 1 (2026-10-02): one pre-registered read, a full-day clock, and what a PASS does and does not support

This follows a `quant-proof` review of the design. It is fixed before any forward hour is walked.

1. **Clean clock: 2026-10-06T00:00:00Z.** It replaces 2026-10-05T05:00:00Z in §2.3, so the clock starts after the kill-review week and on a full UTC day. The pool starts 48 h earlier, at 2026-10-04T00Z.
2. **One read, at a fixed end: 10 full UTC days, `[2026-10-06T00, 2026-10-16T00)`.**
   - The read runs once, after the last migration in the window has had its 30-minute exit cap and its hour is sealed and verified, at about 2026-10-16T02Z.
   - Any `report` before then is **INTERIM**: it shows counts, and **no verdict, mean, CI or day sign**.
   - The end date is not moved by what interim counts show.
   - Why 10 days: about 75 trades/day (451 over 6 days at the read) gives about 750 trades. The read's CIs imply a per-trade spread of about 0.21 SOL (flat) and 0.12 SOL (pressure). At n ≈ 750 the pressure CI lower bound stays above 0 even if the mean halves (0.0103 − 1.645 × 0.0044 ≈ 0.003). A 5-day book (n ≈ 375) would be a coin flip at a halved mean.
3. **Gate:** CLAUDE.md's promotion gate, under both fail models, k = 1 (a single pre-registered primary book). If another book is a live candidate at the same read, DEC-014 Holm at 10,000 draws applies.
4. **Report-only context (no gate role), recorded per day:**
   - the unfiltered migrate-direct baseline on the same hours;
   - the selected fraction;
   - the fill rate.
   These separate the model from the market.
5. **What a PASS supports.** Only asking the owner for a small live trial, and only after both of these:
   - (a) **Latency and size sensitivity** on the same rows: re-scored at entry slot + k for the measured fast-0 p50 and p90 latency, and at the trial's size and priority fee. **A PASS that turns negative at measured latency or at trial size does not support live.** The rule is stated now, before any forward data exists.
   - (b) The **live-readiness comparison** (§3): runner paper rows against scorer rows for the same mints, with tolerances fixed before the read. It covers entered-set overlap, fill agreement, entry slot and price difference, realized fail rate against the pressure model by bucket, and exit price difference.
6. **Not changed:** the frozen model, threshold, execution, size (0.5 SOL in the scorer) and both fail models.

## Amendment 2 (2026-10-02): files that hold P&L before the read are not opened

`tools/exp012_forward.py` (#220, #223) enforces the pinned window, INTERIM-only reports, the `final_read.lock` and an external FINAL ledger at `/data/mal/exp012-forward/FINAL_READS.jsonl`. Three things still hold per-row nets at rest during the window, because the scorer needs them:

- `OUT/rows.jsonl`
- `OUT/scratch/*.jsonl`
- a FINAL `report.json` or `report.md`, which only exists after the read

**Rule:** before the FINAL read, no person, agent or job opens or prints these files, or any `flat`, `press`, `*_sol` or `gross` field from them. Only `tools/exp012_forward.py score` and `report` read them. Monitoring uses INTERIM `report` output and the `runs.jsonl` counts only. A breach is recorded here, dated, and the read is reported as compromised.
