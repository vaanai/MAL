# Experiment registry (EXP-xxx)

Phase-0 experiments are **files in this directory**, not a database. One experiment per file: `EXP-001-short-slug.md` (or `.json` if machine-heavy).

## Required fields

| Field | Description |
| --- | --- |
| **ID** | `EXP-xxx` monotonic |
| **Hypothesis** | Falsifiable claim tied to [LAB_STATE.md](../LAB_STATE.md) open hypothesis |
| **Method** | Data sources, as-of-T rules, filters, baseline comparator |
| **As-of-T rules** | What information is allowed at decision time T (no lookahead) |
| **Regime labels** | Regime ID at ingest (define taxonomy in experiment) |
| **Windows** | Evaluate at **1s / 5s / 15s / 30s / 60s** from trigger event (state which are primary) |
| **Result** | Metrics, plots, or links to artifact paths (no secrets) |
| **Kill-attempt** | Explicit test that would **reject** hypothesis if failed |
| **Conclusion** | Promote / iterate / kill; update LAB_STATE if promoted or killed |

## Template (markdown)

```markdown
# EXP-xxx — Title

- **Status:** planned | running | done
- **Owner seat:** Scout | Graph | Proof | Helm
- **Started:** YYYY-MM-DD
- **Hypothesis:** …
- **Method:** …
- **As-of-T:** …
- **Regime labels:** …
- **Windows:** 1s, 5s, 15s, 30s, 60s — primary: …
- **Kill-attempt:** …
- **Result:** …
- **Conclusion:** …
```

## Rules

- **Kill-attempt before promote** — No promotion to “active decision” without a documented failed kill-attempt or passed kill criteria.
- **Immutable snapshots** — Export decision packets to `ARTIFACTS/` when an experiment closes; do not rewrite past results.
- **Fast mode** — Stay OFF unless constitution exception (imperative).

## Index

Maintain a table in this README when experiments exist:

| ID | Title | Status | Conclusion |
| --- | --- | --- | --- |
| [EXP-001](EXP-001-regime-stage-mislabel.md) | Regime/stage mislabel sample | **PASS closed** (tooling; local scoring optional archive) | Mislabel gates satisfied for pipeline; promote ladder not started |
| [EXP-002](EXP-002-evaluate-runner-v0.md) | Evaluate→runner v0 (rules-only paper) | **INCOMPLETE closed** (vacuous v0 filter; tooling OK) | Tooling only; see EXP-002b |
| [EXP-002b](EXP-002b-evaluate-rules-v1.md) | Evaluate→runner rules v1 (stricter) | **FAIL closed** (~81% reject; adverse lift) | **FAIL_NO_LIFT_VS_RANDOM**; see EXP-002c |
| [EXP-002c](EXP-002c-rules-v2-adverse-selection.md) | Evaluate rules v2 (anti-adverse-selection) | **INCOMPLETE closed** (~63% reject; lift FAIL) | Proof stamp **INCOMPLETE** (reject_rate_band); DISCOVERY then 002d or pause |
| [EXP-003](EXP-003-post-create-marks.md) | Post-create marks onto sealed observe | Schema + coverage CLI landed (#14) | Pending RPC producer + READY coverage |
