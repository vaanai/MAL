# Paper scoreboard on sealed-day fixtures — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-scoreboard-sealed-fixture-v0` |
| **Status** | **Proposed** paper registration (2026-09-23). Not a measure. Not run. |
| **Owner seat** | Proof (scoreboard + Soft GATE); Scout (packet spine stays on the embedded stamps); Helm (FORMAL auth) |
| **Commission** | Helm FORMAL auth 2026-09-23. Consumer of merged paper-evaluate (`224166c`, PR #50) and hot-packet v0 (`274faa2`, PR #49). |
| **Schema** | [paper-scoreboard-sealed-fixture-v0.schema.json](paper-scoreboard-sealed-fixture-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_scoreboard_sealed_fixture_v0/](../fixtures/paper_scoreboard_sealed_fixture_v0/) — synthetic, not host extracts |
| **CLI** | `python -m tools.paper_scoreboard_sealed_fixture_v0` — validate / example / score on local JSON only |
| **Soft GATE** | **Required** for Proof. Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file is the object **LAYA / DEC-006** cite when a local set of validated [`paper_evaluate_hot_packet_v0`](PAPER-EVALUATE-HOT-PACKET-V0.md) stamps is counted against a **checked-in sealed-day expectation**. It is an artifact registration, same class as hot-packet v0 and paper-evaluate.

It is **not** EXP-009. It is **not** a sealed Oracle measure. It is **not** an EXP-002c retune. An EXP needs a hypothesis, windows, and a kill-attempt ([EXP/README.md](../EXP/README.md)). This scoreboard has none of those. Merge does not score a sealed book and does not claim alpha.

---

## What this scoreboard is

[DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md) stops at a paper stamp. [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md) says a later book keeps both arms. v0 names that local-set count `type=paper_scoreboard` / `schema_version=paper_scoreboard_sealed_fixture_v0` / `id=paper-scoreboard-sealed-fixture-v0`.

| Piece | v0 fact |
| --- | --- |
| Stamp input | One or more local JSON objects that validate as `paper_evaluate_hot_packet_v0`, or a directory of those files |
| Fixture side | One checked-in sealed-day expectation. Synthetic is the checked-in origin. `mint=UNK` is that reject row’s identity, not a wildcard |
| Read path | Local JSON only. `rpc=false`. `observe_jsonl_tail=false`. `host_extract_required=false` |
| Output | Label counts, a reason-code histogram, a not-scored graph status, null horizons, null `Δ_exec`, join counts |
| Population | `n` is the local set. `local_set_is_not_the_sealed_book=true`. Day `2026-09-20` on these fixtures is the string on the parent stamps. It is not a read of `observe-2026-09-20.jsonl` |

Rates are **stamp counts**. `share` is `{numerator, denominator}` of this local set. `share_kind=count_fraction_not_a_return`. A zero numerator means zero stamps. It is not a 0% return.

---

## DEC-007 both arms

Every scoreboard sets:

| Field | Value |
| --- | --- |
| `full_book.policy` | `dec007_both_arms_retained` |
| `full_book.arm_retained` | `true` |
| `full_book.deletes_detect_history` | `false` |
| `full_book.reject_stamps_dropped` | `0` |
| `label_rates.rows` | `runner` then `reject`, both rows always present |

`score` counts every valid stamp it was given. It has no switch that drops `evaluate_label=reject`. A duplicate identity is counted twice (`duplicate_identity_n`) and both rows stay.

`expectation_rows_not_in_local_set` is a checklist gap. Those rows were not in the files passed to `score`. The gap is not a deleted arm. The all-runner example has `reject_n=0` and `expectation_rows_not_in_local_set=1` because the identity-reject stamp was not in that set. The reject row is still on the table with share `0/4`.

A fixture disagreement does not relabel a stamp and does not delete it. `label_disagree` / `reason_disagree` are join counts. They are not a kill gate.

---

## Label and reason tables

`label_rates` is runner versus reject on the local set.

`reason_histogram` is the closed paper-evaluate reason list, in that order, including zeros:

`missing_signature`, `missing_mint`, `stage_not_bonding`, `honesty_reject`, `overlay_discipline`.

`n` on a reason row is how many stamps in the set carry that code. A stamp with two codes increments both. The denominator is the stamp count, so shares need not sum to 1. The checked-in sets are single-reason or empty.

Checked-in sets, all on synthetic day `2026-09-20`:

| Example | Local set | n | runner | reject | Graph aggregate | Expectation rows absent |
| --- | --- | --- | --- | --- | --- | --- |
| `all-runner` | enriched fee, graph slots, LaunchLab shape, sealed cold | 4 | 4 | 0 | `not_scored_mixed` | 1 |
| `mixed` | those four plus identity reject | 5 | 4 | 1 | `not_scored_mixed` | 0 |
| `identity-reject` | `mint=UNK` / `missing_mint` only | 1 | 0 | 1 | `not_used_graph_cold` | 4 |
| `graph-cold` | sealed cold only | 1 | 1 | 0 | `not_used_graph_cold` | 4 |
| `slots-not-scored` | allowlisted slot filled, lift unused | 1 | 1 | 0 | `not_used_slots_not_scored` | 4 |

Mixed reason histogram: `missing_mint=1`. The other four codes are `0`. That `0` is a count of stamps. It is not a return.

`graph_lift` is null on every board. `not_scored_mixed` means the set contains both `not_used_graph_cold` and `not_used_slots_not_scored`. Neither status is a score. The integer on the slot fixture is not lift and not a promote.

There is no fee table, no LaunchLab cohort rate, and no production-enum tally. `global_95bps` and `launchlab_init` stay **Proposed** on the embedded stamps.

---

## Horizons and `Δ_exec`

Null with an explicit status. The same rule as paper-evaluate: null is not 0%.

| Object | Status | Value |
| --- | --- | --- |
| Each embedded stamp | `runner_stamp.horizon_status=null_ok_no_marks_on_this_stamp` | every horizon key `null` |
| Scoreboard `horizons` | `fixture_joined_null_explicit` | `values` all `null`. `null_is_not_zero_return=true` |
| Each embedded stamp `delta_exec` | `null_ok` | `value=null` |
| Scoreboard `delta_exec` | `fixture_joined_null_explicit` | `value=null`, `reason=sealed_day_fixture_has_no_fill`, `null_is_not_zero_cost=true` |

The fixture join copies that emptiness. It does not read marks and it does not invent a price. `expectation.horizon_join.values_supplied` is `false`. A `true` flag, a numeric horizon, or a zero `delta_exec` is refused. The CLI emits no scoreboard for that file.

`also_called` remains `Δ_exec`.

---

## Sealed book stays incomplete

Every embedded packet already has `dual_read.sealed_book_rpc_slice=incomplete` (hot-packet v0). The scoreboard repeats it:

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `fixture_join_does_not_close_the_sealed_book` |
| `fixture_join.closed_book` | `false` |
| `expectation.sealed_book_rpc_slice` | `incomplete` |
| `sealed_days[].sealed_book_rpc_slice` | `incomplete` |

An expectation with `origin` other than `synthetic`, or with `sealed_book_rpc_slice=complete`, does not validate. EXP-007e enriched PASS stays a sample overlay. This registration does not close the book.

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge of this registration | Docs + schema + synthetic fixtures + fixture CLI. **Does not** wire a book into observe |
| Input | Validated `paper_evaluate_hot_packet_v0` stamps plus one synthetic sealed-day expectation |
| Encoder | **Not** promoted. `observe/client.py` is untouched |
| Enum production lock | **No.** `global_95bps` and `launchlab_init` stay **Proposed**. `venue=launchlab` and `market=launchlab_pool` stay **Proposed** |
| Discovery promote | **No.** No Graph revive, no X ingest, no mark densify |
| EXP-002c | **Not** retuned. No new evaluate threshold. No invented lift |
| EV / alpha | **No.** `honesty.invented_ev=false`, `honesty.claims_alpha=false`, `measure.kind=none` |
| Measure exit | **None.** `measure.pass_fail_no_lift=false`. The process exits `0` when a scoreboard is printed and `1` when input is not valid. It does not exit `PASS` or `FAIL_NO_LIFT` |
| Oracle measure | **No.** `honesty.scored_oracle_measure=false` |
| Trading | Paper only. No live capital, no trading keys, no PumpPortal trade API |
| `Δ_exec` | Null, status `fixture_joined_null_explicit` |

`honesty` is const-false on scored measure, wiring, encoder promote, enum lock, Discovery promote, graph-lane revive, invented lift, invented EV, alpha claim, EXP-002c retune, live capital, trading keys, and PumpPortal trade API. A scoreboard that flips those bits does not validate.

Join disagreement is still exit `0`. A mismatch is a count on the board. It is not alpha and it is not a fail-no-lift.

---

## Soft watches (non-blocking)

**Soft GATE is required** before Proof treats this registration as cited law. The watches below are inherited or named so a reader does not promote them by accident. `soft_watches.blocking=false`. `soft_watches.source=hot_packet_v0_soft_gate_pr49_and_paper_evaluate_pr50`. They do not fail merge of this registration.

Inherited from hot-packet v0 Soft GATE (PR #49) via paper-evaluate (PR #50):

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `schema_looser_than_cli` | JSON Schema is the citeable shape. The CLI recomputes the board from the embedded stamps and the expectation, and it refuses numeric horizons. | Do not widen the CLI down to the schema. Do not hold this consumer for a schema rewrite. |
| `synthetic_launchlab_shape` | `all-runner` and `mixed` embed the paper-evaluate LaunchLab shape stamp. | Not a LaunchLab cohort, not a sealed-book rate, not an enum lock. |
| `graph_slot_shape_not_a_score` | `slots-not-scored` embeds the allowlisted slot stamp. | `graph_lift` stays null. The integer is not lift. |
| `dec005_draft_unmerged` | Embedded stamps still carry `clock_source=dec005_draft_pr8_unmerged`. | Hooks only. Not a claim that DEC-005 merged. |
| `sealed_book_rpc_slice_incomplete` | Every packet and this board keep the slice `incomplete`. | This scoreboard does not close the sealed book. |

Named on this registration:

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `label_share_is_not_a_return` | `4/5` runner on the mixed fixture is a count fraction. | Do not read it as EV, lift, or a return. |
| `synthetic_sealed_day_not_a_host_extract` | Day `2026-09-20` matches a calendar day that sealed observe also uses. These files are synthetic. | Do not treat the fixture as an Oracle extract or a population. |
| `local_subset_is_not_dropped_history` | A smaller local set leaves expectation rows unused. | Unused checklist rows are not a dropped reject arm. `reject_stamps_dropped` stays `0`. |

---

## CLI

From the repo root:

```bash
python -m tools.paper_scoreboard_sealed_fixture_v0 example --which all-runner
python -m tools.paper_scoreboard_sealed_fixture_v0 example --which mixed
python -m tools.paper_scoreboard_sealed_fixture_v0 example --which identity-reject
python -m tools.paper_scoreboard_sealed_fixture_v0 example --which graph-cold
python -m tools.paper_scoreboard_sealed_fixture_v0 example --which slots-not-scored
python -m tools.paper_scoreboard_sealed_fixture_v0 validate fixtures/paper_scoreboard_sealed_fixture_v0/mixed_runner_reject.json
python -m tools.paper_scoreboard_sealed_fixture_v0 score \
  --expectation fixtures/paper_scoreboard_sealed_fixture_v0/sealed_day_2026-09-20_expectation.json \
  fixtures/paper_evaluate_hot_packet_v0/sealed_cold_graph_runner.json \
  fixtures/paper_evaluate_hot_packet_v0/reject_missing_identity.json
```

`--which` values: `all-runner`, `mixed`, `identity-reject`, `graph-cold`, `slots-not-scored`.

`score` reads local stamp JSON (paths and/or `--dir`) and one `--expectation` file. It prints one scoreboard. It does not call RPC, does not tail observe JSONL, and does not write a side book. Exit `0` when a scoreboard is printed, including when `label_disagree_n` is non-zero. Exit `1` when a file is missing, is not valid JSON, is not a valid stamp, or the expectation is not a synthetic sealed-day checklist. There is no measure exit code.

| Fixture | What it shows |
| --- | --- |
| `sealed_day_2026-09-20_expectation.json` | Synthetic checklist for the five checked-in paper-evaluate identities. Input to `score`, not itself a scoreboard |
| `all_runner.json` | Four runners. Reject row present with `n=0`. One expectation row absent |
| `mixed_runner_reject.json` | Both arms, `missing_mint` once, shares `4/5` and `1/5` |
| `identity_reject.json` | Reject arm retained. `mint=UNK` |
| `graph_cold.json` | `graph_lift=null`, status `not_used_graph_cold` |
| `slots_not_scored.json` | Filled allowlisted slot, status `not_used_slots_not_scored` |

Checked-in fixtures are **synthetic**. They are not Oracle extracts and they are not lift evidence.

---

## Non-goals

| Cap | Held |
| --- | --- |
| Live capital, trading keys, PumpPortal trade API | Out |
| `observe/client.py` / encoder promote | Untouched |
| Continuous observe-wiring | Out |
| Production lock of `global_95bps` or `launchlab_init` | Out. Both stay **Proposed** |
| Discovery promote, Graph revive, X ingest, mark densify | Out |
| EXP-002c retune, new evaluate thresholds | Out |
| Scored Oracle measure, invented lift / EV / cohort alpha | Out |
| `PASS` / `FAIL_NO_LIFT` measure exit | Out |
| Joining marks or filling `Δ_exec` with a number | Out |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete` |
| Soft watches above | Listed, **non-blocking**. Soft GATE still **required** for Proof |

---

## Cross-links

- Stamps being counted: [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md), [paper-evaluate-hot-packet-v0.schema.json](paper-evaluate-hot-packet-v0.schema.json)
- Decode packet: [HOT-PACKET-V0.md](HOT-PACKET-V0.md), [hot-packet-v0.schema.json](hot-packet-v0.schema.json)
- Pipeline: [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md)
- Full book: [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Clocks draft (unmerged): [PR #8](https://github.com/vaanai/MAL/pull/8)
- Parent registrations: [PR #49](https://github.com/vaanai/MAL/pull/49), [PR #50](https://github.com/vaanai/MAL/pull/50)
