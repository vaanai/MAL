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
| [EXP-004](EXP-004-graph-creator-recurrence-v0.md) | Graph creator-recurrence Discovery (H-G1…H-G4) | **DIRECTIONAL_WATCH** (Oracle 2026-09-20/21 courier); H-G2 **kill** @60s | No promote; see EXP-004b |
| [EXP-004b](EXP-004b-nh-g1a-ordinal-prior-mint-v0.md) | NH-G1a ordinal prior-mint depth (Graph child) | **Scored** (Oracle 2026-09-20/21 sealed) | **KILL** cross-day — ordinal lane **parked**; NH-G3a **not next** (new council pick) |
| [EXP-005](EXP-005-smart-wallet-follow-discovery-v0.md) | Smart-wallet / follow L3 Discovery (Scout) | **DIRECTIONAL_WATCH** (Oracle 2026-09-20/21 sealed) | L3 packet `L3_PACKET_V0`; high v2-runner overlap — soft watch only; **no promote** |
| [EXP-005b](EXP-005b-l3-residual-vs-exp002c-v2-falsifier-v0.md) | L3 residual vs EXP-002c v2-runner overlap falsifier (Scout) | **DIRECTIONAL_WATCH (residual)** (Oracle 2026-09-20/21 sealed) | Primary **L3_minus_v2** sep+random **PASS** @60s both days; thin residual priced_n; **no promote** |
| [EXP-006](EXP-006-paper-would-have-happened-harness-v0.md) | Paper “would-have-happened” fill harness v0 (P0) | **INCOMPLETE (scored)**; harness **GATE PASS + merged** | P0 CLI + paper_fill side JSONL; primary **L3_minus_v2** **K-lift-rescue** split; **no promote** |
| [EXP-007](EXP-007-platform-regime-taxonomy-v0.md) | Platform-regime taxonomy v0 (curve version / fees / graduation / quote) | **INCOMPLETE (scored audit)** (Oracle 2026-09-20/21 sealed) | Coverage/knowable-at-T **PASS**; RPC-resolved platform slice **INCOMPLETE**; wiring still Proposed |
| [EXP-007b](EXP-007b-platform-regime-rpc-enrich-v0.md) | Platform-regime RPC enrich + audit re-score (capped sample) | **INCOMPLETE (scored)** (Oracle 2026-09-20/21) | 500/day `regime_enrich`; enriched **K-platform-rpc-resolved** still **INCOMPLETE**; merge ≠ observe-wiring |
| [EXP-007c](EXP-007c-fee-knowable-at-t-v0.md) | Fee dimension knowable-at-T on reused 007b sample | **INCOMPLETE (scored)** (Oracle 2026-09-20/21) | **K-fee-knowable-at-t INCOMPLETE** (~86–89% `unverified`; Global **95 bps** ≠ `global_100bps`; ~11–14% `creator_dynamic`) |
| [EXP-007d](EXP-007d-fee-global-95bps-enum-v0.md) | Proposed `global_95bps` enum + fee gate re-score (reuse 007c) | **PASS (K-fee)** / **Proposed enum** (Oracle 2026-09-20/21) | **K-fee-knowable-at-t PASS** (0% `unverified`); **85.6%** / **89.4%** `global_95bps`; enriched composite **INCOMPLETE** (instr/quote); merge ≠ wiring |
| [EXP-007e](EXP-007e-instr-quote-residual-v0.md) | Instr/quote residual diagnosis + restamp (reuse 007d sample) | **PASS (K-platform-rpc-resolved)** enriched (Oracle 2026-09-20/21) | Quote offset + LaunchLab + `create_v2` SOL fallback; **1×** `instr_log_gap` day **21**; sealed book **INCOMPLETE**; merge ≠ wiring |
| [EXP-008](EXP-008-x-account-quality-propagation-v0.md) | X account-quality / propagation brief v0 (Layer 4 additive) | **Proposed, not run** (docs registration only) | Scout owns social brief; observe spine first; EXP-007 stratify law; no X keys / no social CLI in registration PR |
