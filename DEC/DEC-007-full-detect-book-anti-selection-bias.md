# DEC-007 — Full detect book (anti-selection-bias)

| Field | Value |
| --- | --- |
| **Status** | Active (working law; council 2026-09-20 PT) |
| **Decider** | Council alignment (Helm / Scout / Graph / Proof) |
| **Date** | 2026-09-20 (recorded) |
| **Requires** | [DEC-006](DEC-006-detect-decode-evaluate-runners.md) pipeline vocabulary |

## Decision

### Full detect book — HARD

1. **Outcomes recorded for rejects and runners alike** — every detect row that reaches decode gets runner-style outcome marks on the paper book, whether evaluate passed or rejected.
2. **Evaluate labels never delete history** — reject/pass is append-only metadata; no dropping rows from the detect book because a filter said no.
3. **Evaluate→runner EXP reporting** MUST include:
   - **Confusion matrix** (evaluate vs eventual paper outcome under costs), and
   - **Reject-cohort marks** under the same fee/slippage/latency model as runners.
4. **Kill on the filter (selection bias):** if reject cohorts match runner cohorts **after costs**, the evaluate stage is **killed** — the filter is not adding information, only selecting winners post hoc.
5. **Graph flags** scored on the **full detect book** as well — not only evaluate-pass rows.

### Relationship to decode/evaluate

- Decode remains knowable-at-T per [DEC-003](DEC-003-regime-at-ingest-v0.md) / [DEC-004](DEC-004-regime-id-encoding.md).
- Evaluate reads the packet only ([DEC-006](DEC-006-detect-decode-evaluate-runners.md)); the detect book is the **universe** for outcome labeling, not the evaluate-pass subset.

## Rationale

- Prevents “great backtest” that only labels trades the filter already picked.
- Forces Proof to falsify filters on the same economic yardstick as promoted runners.

## Review trigger

- EXP shows label storage or tooling cannot retain reject outcomes at scale — document gap in EXP; do not waive book completeness without new DEC.
- Evaluate stage bypassed in an EXP — invalid for promotion evidence.

## Overturn path

New DEC with kill-attempt evidence; never silently shrink the labeled universe.
