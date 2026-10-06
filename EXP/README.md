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
- **One topic per PR.** Workers do not merge. The Claude manager merges after review ([DEC-012](../DEC/DEC-012-tool-neutral-manager-workers.md), [DEC-013](../DEC/DEC-013-claude-manager-merges.md)).

## Index

Maintain a table in this README when experiments exist:

| ID | Title | Status | Conclusion |
| --- | --- | --- | --- |
| [EXP-001](EXP-001-regime-stage-mislabel.md) | Regime/stage mislabel sample | **PASS closed** (tooling; local scoring optional archive) | Mislabel gates satisfied for pipeline; promote ladder not started |
| [EXP-002](EXP-002-evaluate-runner-v0.md) | Evaluate→runner v0 (rules-only paper) | **INCOMPLETE closed** (vacuous v0 filter; tooling OK) | Tooling only; see EXP-002b |
| [EXP-002b](EXP-002b-evaluate-rules-v1.md) | Evaluate→runner rules v1 (stricter) | **FAIL closed** (~81% reject; adverse lift) | **FAIL_NO_LIFT_VS_RANDOM**; see EXP-002c |
| [EXP-002c](EXP-002c-rules-v2-adverse-selection.md) | Evaluate rules v2 (anti-adverse-selection) | **INCOMPLETE closed** (~63% reject; lift FAIL) | Proof stamp **INCOMPLETE** (reject_rate_band); DISCOVERY then 002d or pause |
| [EXP-002c addendum](EXP-002c-addendum-honest-return-distributions.md) | Honest median/p10/p90/SOL rescore (125 + 350 bps) | **Scored** (Oracle 2026-09-20/21); 09-24 public-RPC backfill 429-limited | **All listed filters KILL** vs spine @60s after 350 bps; means were outlier-dominated; **no retune** |
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
| [EXP-009](EXP-009-migrate-creator-gate-prereg.md) | Migrate creator-recurrence gate, pre-registration (holdout = unsealed fast hours) | **planned** — pre-registration only, not run | Fixes gate cells (G1 24h-window prior-mint count / G3), k-threshold procedure, burn-in + unknown-creator exclusion (applies to gate_off too), and holdout (fast hours < 2026-09-19T01:00Z, floor raised by burn-in) before any of that data is read; G1 is causally computable on backward hours via a new backfill adapter, G3 (weak creator↔buyer recurrence) is not yet — primary cell restricted to G1 |
| [EXP-011](EXP-011-migrate-entry-model-prereg.md) | Migrate entry model (frozen, leakage-ablated), pre-registration (holdout = fresh fast walker-B block 2026-09-09T12..09-15T12) | **planned** — pre-registration only, not run | Freezes lane B3's best-of-6 `tpsl_tp50_sl30 / s2_clf` cell with the two slot+1-landing lookahead features (`same_slot_buys`, `nearby_buy_sol`) removed (PR #156 audit); ablated 9-day LODO survives (flat +6.22%, pressure +3.59%, 9/9 days). Model/threshold/feature set frozen in `ARTIFACTS/exp011/`; single primary cell, one read of the reserved holdout after all 144 hours seal, via a follow-up `tools/exp011_score.py` |
| [EXP-016](EXP-016-rug-veto-plan.md) | Rug-risk entry veto layered on frozen EXP-012 (exploration plan) | **planned**: plan only, not a pre-registration, nothing run | Strict outcome label (pool SOL side −36.75% within 3 slots, or an untaped drain, inside the hold window), new launch-bundle, creator and wallet-cluster features from the tape, 6-candidate nested-LODO veto, paired bars at realistic costs; confirmation target `[2026-08-02T12, 2026-08-08T12)` only through a merged Part 1 |

## Decode contracts (not experiments)

Hot-packet v0 is **not** an EXP id. It has no hypothesis, windows, or kill-attempt. LAYA designers cite the artifact.

| Artifact | Role | Status |
| --- | --- | --- |
| [HOT-PACKET-V0](../ARTIFACTS/HOT-PACKET-V0.md) | Proposed paper packet: L1 spine, regime locks, capped graph slots | **Proposed** registration — not a measure; merge ≠ observe-wiring ≠ enum production lock ≠ Discovery promote |
| [PAPER-EVALUATE-HOT-PACKET-V0](../ARTIFACTS/PAPER-EVALUATE-HOT-PACKET-V0.md) | Proposed evaluate→runners stamp; input is `hot_packet_v0` only | **Proposed** registration — not a measure; merge ≠ wiring ≠ enum lock ≠ Discovery / Graph revive ≠ EXP-002c retune |
