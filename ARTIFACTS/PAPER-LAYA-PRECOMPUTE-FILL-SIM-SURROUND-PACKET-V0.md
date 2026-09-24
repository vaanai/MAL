# LAYA precompute fill-sim surround packet — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-laya-precompute-fill-sim-surround-packet-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). **Soft GATE PASS Formal-stamped** (Lyra; kill `bc-18aa6b2a-3c7a-54e5-a5a2-4a0e83faea48`; implement `bc-1f7c99a8-a086-5265-b622-bee310fe63aa`; head `f7eeb849cb31385cc652249e70b2913ecba945a5`; [Soft GATE comment](https://github.com/vaanai/MAL/pull/63#issuecomment-5809183727); merged [PR #63](https://github.com/vaanai/MAL/pull/63) squash `171cd61` on `main`). Not a measure. Not LAYA authorize-run. Not risk-gate unlock. Not live. |
| **Owner seat** | Proof (surround + Soft GATE); Scout (fill-sim spine on cited scoreboards); Helm (AUTH) |
| **Commission** | Consumer of merged fill-sim scoreboard ([PR #58](https://github.com/vaanai/MAL/pull/58) squash `dfbab7a`) and optional fill-sim batch rollup ([PR #59](https://github.com/vaanai/MAL/pull/59) squash `4664363`). Parent #49–#62 CLIs and EXP-006 harness are **not** rewritten. |
| **Schema** | [paper-laya-precompute-fill-sim-surround-packet-v0.schema.json](paper-laya-precompute-fill-sim-surround-packet-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/](../fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/) — synthetic surround packets only |
| **CLI** | `python -m tools.paper_laya_precompute_fill_sim_surround_packet_v0` — example / validate / assemble on local JSON only |
| **Soft GATE** | **PASS** — Formal stamp Lyra (kill `bc-18aa6b2a-3c7a-54e5-a5a2-4a0e83faea48`; implement `bc-1f7c99a8-a086-5265-b622-bee310fe63aa`; squash `171cd61` on `main`). Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers how validated [`paper_fill_sim_scoreboard_sealed_fixture_v0`](PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md) digests (and optionally a #59 batch `rollup` citation) are assembled into a **fixtures-only surround packet** shaped for later LAYA precompute consumption. The packet cites counts and join summaries. It does **not** embed fill-sim stamp bodies, hot packets, horizons, `Δ_exec`, EV, or lift.

Merge ≠ LAYA authorize-run ≠ risk-gate unlock ≠ live trading ≠ Oracle measure ≠ EXP-006 promote ≠ Discovery promote.

**Soft GATE PASS** (Formal stamp Lyra; 2026-09-24; head `f7eeb849cb31385cc652249e70b2913ecba945a5`). Caps held: `measure.kind=none`; sealed book **incomplete**; `closed_book_claim=false`; `laya.authorize_run=false`; `laya.risk_gate_unlock=false`; `laya.live_trading=false`; DEC-007 both arms; horizons and `Δ_exec` null. Soft GATE PASS ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X keys on host ≠ live trading ≠ EXP-006 promote ≠ LAYA authorize-run ≠ risk-gate unlock.

---

## What this surround packet is

| Piece | v0 fact |
| --- | --- |
| Input | One or more checked-in fill-sim scoreboard JSON files under `fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/`, and/or one checked-in fill-sim batch under `fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/` (scoreboards extracted from `days[].scoreboard`; optional `rollup` digest cited) |
| Read path | Local JSON only. `rpc=false`. `observe_jsonl_tail=false`. `host_extract_required=false`. `marks_joined=false`. No `/var/lib/mal` |
| Output | `scoreboard_digests[]` with label and fill-sim status **counts** (not returns), fixture-join summaries, and an optional `batch_digest.rollup` copy when a batch path is supplied |
| Precompute slot | `precompute.status=fixture_shape_only_not_executed`. Features and graph scores stay not ready on fixtures |
| LAYA caps | `laya.authorize_run=false`, `laya.risk_gate_unlock=false`, `laya.live_trading=false` |

Rates are **stamp counts**. `share` is `{numerator, denominator}` of the cited local set. `share_kind=count_fraction_not_a_return`. A zero numerator is a count of zero stamps. It is not a 0% return.

---

## DEC-007 both arms

Surround `full_book.policy=dec007_both_arms_retained`, `reject_stamps_dropped=0`. Aggregated `label_rates.rows` list `runner` then `reject`. Aggregated `fill_sim_status_counts` list `documented_model_only` then `reject_arm_no_pretend_buy`. Embedded scoreboard digests retain both arms on their own tables.

---

## Horizons and `Δ_exec`

Null with explicit status on the surround object and on the nested `precompute` block. Null is not 0% and not zero cost.

| Object | Status | Value |
| --- | --- | --- |
| Surround `horizons` | `surround_packet_null_explicit` | `values` all `null` |
| Surround `delta_exec` | `surround_packet_null_explicit` | `value=null`, `reason=laya_precompute_fixture_surround_has_no_scored_fill` |
| `precompute.horizons` / `precompute.delta_exec` | same | same explicit null |

---

## Sealed book stays incomplete

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `laya_precompute_surround_does_not_close_the_sealed_book` |

`graph_lift` is null. `graph_policy=cold`.

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge of this registration | Docs + schema + synthetic fixtures + fixture CLI only |
| LAYA | **Does not** authorize-run, unlock risk gate, or enable live trading |
| Parent CLIs | **Not** rewritten (#49–#62) |
| EXP-006 | **Not** promoted. Harness **not** rewritten |
| Marks | **Not joined** as a scored measure |
| Host | CLI refuses `/var/lib/mal`, `//var/lib/mal`, and relative `var/lib/mal/...` **lexically** before FS touch |
| Measure | `measure.kind=none`. No `PASS` / `FAIL_NO_LIFT` process exit |

**Soft GATE PASS** (Formal stamp Lyra). Soft GATE PASS ≠ Discovery promote ≠ wiring ≠ enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X on host ≠ live ≠ EXP-006 promote.

---

## Soft watches (non-blocking)

**Soft GATE PASS** (Formal stamp Lyra). `soft_watches.blocking=false`. Inherited watches from #49–#62 remain listed and non-blocking.

Named on this registration:

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `surround_counts_are_not_returns` | Label and fill-sim shares are stamp counts | Not EV or lift |
| `surround_not_laya_authorize_run` | `laya.authorize_run=false` | Merge ≠ LAYA run |
| `surround_not_risk_gate_unlock` | `laya.risk_gate_unlock=false` | Merge ≠ risk gate |
| `surround_not_live_trading` | `laya.live_trading=false` | Paper-only registration |
| `synthetic_scoreboard_not_a_host_extract` | Cited scoreboards are synthetic fixtures | Not an Oracle extract |
| `batch_digest_optional_not_a_measure` | Optional #59 rollup is a count digest only | Not a scored batch run |

---

## CLI

From the repo root:

```bash
python -m tools.paper_laya_precompute_fill_sim_surround_packet_v0 example --which mixed-scoreboard
python -m tools.paper_laya_precompute_fill_sim_surround_packet_v0 example --which batch-two-day-with-digest
python -m tools.paper_laya_precompute_fill_sim_surround_packet_v0 validate \
  fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/mixed_scoreboard.json
python -m tools.paper_laya_precompute_fill_sim_surround_packet_v0 assemble \
  --scoreboard fixtures/paper_fill_sim_scoreboard_sealed_fixture_v0/mixed_runner_reject.json
python -m tools.paper_laya_precompute_fill_sim_surround_packet_v0 assemble \
  --batch fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json
```

Exit `0` when a surround packet prints or validates. Exit `1` on invalid input or a path under `/var/lib/mal`. There is no `PASS` / `FAIL_NO_LIFT` exit.

Proof:

```bash
python3 -m unittest tools.test_paper_laya_precompute_fill_sim_surround_packet_v0
```

| Fixture | What it shows |
| --- | --- |
| `mixed_scoreboard.json` | One validated fill-sim scoreboard digest; no batch rollup |
| `batch_two_day_with_digest.json` | Two scoreboard digests from the checked-in #59 two-day batch plus cited `rollup` |

---

## Non-goals

| Cap | Held |
| --- | --- |
| Rewrite #49–#62 parent CLIs or EXP-006 harness | Out |
| `observe/client.py` | Untouched |
| Host paths, SSH, Oracle re-run, marks join as scored measure | Out |
| LAYA authorize-run / risk-gate unlock / live | Out |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete` |
| Soft GATE PASS at merge | **PASS** (Formal stamp Lyra; kill `bc-18aa6b2a-3c7a-54e5-a5a2-4a0e83faea48`; head `f7eeb84`) — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 / LAYA authorize-run / risk-gate unlock |

---

## Cross-links

- Scoreboards cited: [PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md)
- Optional batch rollup: [PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md)
- LAYA stack brief: [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md)
- This surround packet: [PR #63](https://github.com/vaanai/MAL/pull/63) squash `171cd61` on `main` (Soft GATE PASS Formal-stamped)
- Pipeline: [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
