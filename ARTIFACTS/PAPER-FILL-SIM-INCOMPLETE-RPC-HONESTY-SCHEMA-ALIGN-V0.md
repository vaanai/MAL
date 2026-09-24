# Paper fill-sim incomplete-RPC honesty — schema align v0

| | |
| --- | --- |
| **ID** | `paper-fill-sim-incomplete-rpc-honesty-schema-align-v0` |
| **Status** | **Proposed** registration (2026-09-24). **Soft GATE PASS Formal-stamped** (Lyra; kill `bc-ad05f533-0b48-562c-815f-b6fbbe9de646`; implement `bc-49b65bee-fd81-5d5d-a4f1-7bdfb45a0c2a`; merged [PR #61](https://github.com/vaanai/MAL/pull/61) squash `677e88b` on `main`). Not a measure. Not an EXP. Not a sealed-book close. |
| **Owner seat** | Scout commission. Helm AUTH 2026-09-23. |
| **Parent** | Paper fill-sim chain PRs [#57](https://github.com/vaanai/MAL/pull/57)–[#60](https://github.com/vaanai/MAL/pull/60), main tip `677e88b` after Formal #61 |
| **Mirror** | [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md) (#54) — same honesty caps on the fill-sim citeable schemas; **no** rewrite of parent #49–#60 CLIs |
| **Runtime object** | **None.** This stamp does not add a receipt schema or a CLI. Cite the tightened fill-sim schemas. |
| **Soft GATE** | **PASS** — Formal stamp Lyra (kill `bc-ad05f533-0b48-562c-815f-b6fbbe9de646`; implement `bc-49b65bee-fd81-5d5d-a4f1-7bdfb45a0c2a`; squash `677e88b` on `main`). Watches that stay true stay **non-blocking**. |

JSON Schema on the fill-sim paper chain (#57–#60) is the citeable shape. Before this stamp, `graph_lift_status` on the fill-sim bind could disagree with `input.stamp.input.packet.graph.cold` while the CLI rebuild refused that bind. The #57–#60 registrations already `const`-lock closed book, `measure.kind=none`, null horizons / `Δ_exec`, DEC-007 arm rows, batch source-row forbidden names, and host-local open-claim bits. This registration closes the remaining watched gap and documents the align pass.

**Soft GATE PASS** (Formal stamp Lyra). Soft watch `schema_looser_than_cli` is **CLOSED** for watched fill-sim shapes. Soft GATE PASS ≠ Discovery promote / continuous observe-wiring / production enum lock / densify / EXP-002c retune / Graph revive / X on host / live trading / EXP-006 promote. No new runtime object. No CLI rewrite.

`measure.kind` stays `none` on these stamps. `dual_read.sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays `false`. Graph stays cold. `global_95bps` and `launchlab_init` stay Proposed.

---

## What aligned

| Shape the CLI already refused | Schema now refuses it |
| --- | --- |
| `closed_book_claim=true` or `sealed_book_rpc_slice` closed / complete on incomplete registrations | `const: false` / `const: incomplete` on fill-sim stamp, scoreboard, expectation, batch, and host-local dry-run receipts (already on #57–#60; tests cite them). |
| `measure.kind` other than `none`, `pass_fail_no_lift=true`, invented EV / lift / alpha | `const` on fill-sim stamp, scoreboard, batch, and host-local `measure` and `honesty` (already on #57–#60). |
| Host-path open claims: `var_lib_mal_opened=true`, `honesty.var_lib_mal_read=true`, `ci_claimed_sealed_book_close=true`, `host_extract_checked_into_git=true` | `const: false` on the fill-sim host-local receipt. Operator-declared path strings under `/var/lib/mal` stay legal; the string is not an open claim. |
| Numeric horizon or `Δ_exec` while status is null-explicit on fill-sim registrations | Horizon map values and `delta_exec.value` stay `const: null` with explicit status on stamp and scoreboard (already on #57–#58). |
| Non-null fill-sim outcomes on the bind (`fee_sol`, `t_fill`, …) | `const: null` on fill-sim stamp `fill_sim` fields (already on #57). |
| `fill_sim_status_counts`, `label_rates.rows`, batch `rollup.rows`, and host `parent_digest.fill_sim_status_arms` omitting the reject arm | `prefixItems` / `minItems` / `maxItems` matching #58–#60 CLI tokens (already on those schemas; tests cite them). |
| Return / EV keys on batch source rows and on scoreboard rows | Batch `sourceNode.propertyNames` pattern (case-insensitive forbidden set from #59). Scoreboard rows use closed objects; dishonest keys fail `additionalProperties: false`. |
| `graph_lift_status` disagreeing with embedded packet `graph.cold` on the fill-sim bind | **New on this stamp:** `allOf` if/then on [paper-fill-sim-hot-packet-evaluate-v0.schema.json](paper-fill-sim-hot-packet-evaluate-v0.schema.json) (path `input.stamp.input.packet.graph.cold`). Nested scoreboards inherit via `$ref` to the fill-sim stamp schema. |
| Whole-number float on an integer graph slot inside the embedded evaluate stamp | Inherited from parent #54 tighten on `paper-evaluate-hot-packet-v0` / `hot-packet-v0` `$ref` (draft-04 `jsonIntegerOrNull`; no `TYPE_CHECKER` mutate). |

Parent fill-sim CLIs are unchanged. They still rebuild the object and still exit `1` on these shapes.

---

## Schemas touched

| Schema | Diff |
| --- | --- |
| [paper-fill-sim-hot-packet-evaluate-v0.schema.json](paper-fill-sim-hot-packet-evaluate-v0.schema.json) | `graph_lift_status` follows embedded `input.stamp.input.packet.graph.cold`. Other honesty bits were already `const` on #57. |
| [paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json](paper-fill-sim-scoreboard-sealed-fixture-v0.schema.json) | Already `const` on closed book, `measure.kind=none`, null horizons, DEC-007 `prefixItems`. No structural edit. Tests cite it. |
| [paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json) | Already `const` on closed book, `measure.kind=none`, rollup / source-row honesty, nested scoreboard `$ref`. No structural edit. Tests cite it. |
| [paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json](paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json) | Already `const` on open-claim bits, closed book, `measure.kind=none`, fill-sim arm `prefixItems` on parent digest. No structural edit. Tests cite it. |

There is no `paper-fill-sim-incomplete-rpc-honesty-schema-align-v0.schema.json`. Operators do not need a new runtime object. The citeable align-pass object is this file.

---

## Soft watches

**Closed by this stamp**

| Watch id | Status |
| --- | --- |
| `schema_looser_than_cli` | **CLOSED** for the dishonest shapes in [What aligned](#what-aligned), including `graph_lift_status` vs embedded packet `graph.cold` on the fill-sim bind. Schema validation refuses them. The CLIs still refuse them. Do not widen a CLI down to an older schema. |

Parent stamps #57–#60 still emit `schema_looser_than_cli` inside `soft_watches.items`. That list is the historical registration of those stamps. It is not a claim that the honesty shapes above still pass schema only after this align pass closes the last gap.

**Still true, non-blocking** (`blocking=false` on the parent stamps). **Soft GATE PASS** (Formal stamp Lyra). This stamp does not close them.

| Watch id | What stays true |
| --- | --- |
| `sealed_book_rpc_slice_incomplete` | The slice stays `incomplete`. |
| `counts_are_not_returns` | Batch and scoreboard counts are stamp counts. |
| `label_share_is_not_a_return` / `fill_sim_status_share_is_not_a_return` | Label and fill-sim status shares are count fractions. |
| `graph_stays_cold_on_this_stamp` | Graph lift stays null. |
| `host_jsonl_not_readable_from_ci` | Host Oracle JSONL is not readable from CI. |
| `fill_sim_vocabulary_not_exp006_promote` | EXP-006 harness stays vocabulary-only on this chain. |
| `documented_constants_not_a_scored_fill` | Documented fill model constants are not a scored Oracle fill. |
| `parent_evaluate_cli_not_rewritten` / `exp006_harness_not_rewritten` | Parent CLIs and EXP-006 harness are not rewritten. |
| `host_jsonl_read_is_not_a_closed_book` | `host_jsonl_read=true` does not set `closed_book_claim`. |
| `operator_path_string_is_not_a_host_extract` | Path strings under `/var/lib/mal` are not file bytes. |
| `dry_run_does_not_ssh_or_open_var_lib_mal` | The dry-run CLI does not SSH and does not open `/var/lib/mal`. |
| `fill_sim_host_local_not_a_host_extract` | Checked-in receipts are not host extracts. |
| `dec005_draft_unmerged` | Clock names still follow draft PR #8. |
| `synthetic_sealed_day_not_a_host_extract` | Checked-in days are synthetic. |
| `local_subset_is_not_dropped_history` | A smaller local set is not a dropped reject arm. |
| `parent_stamp_not_rewritten` | Parent CLIs are not rewritten by this align pass. |

CLI recompute of a bind, a board, or a batch is still how `validate` works. A schema miss on that recompute is not permission to cite a closed book, a measure, a host open, or a numeric horizon.

---

## Proof

```bash
python3 -m unittest tools.test_paper_fill_sim_incomplete_rpc_honesty_schema_align_v0
python3 -m unittest tools.test_paper_fill_sim_hot_packet_evaluate_v0 tools.test_paper_fill_sim_scoreboard_sealed_fixture_v0 tools.test_paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0 tools.test_paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0
```

The align test loads the fill-sim schemas with JSON Schema 2020-12 and a registry of all citeable paper schemas. Honest fixtures from #57–#60 pass schema and CLI. In-memory dishonest copies fail schema and CLI. Those copies are not checked in. Nothing under `/var/lib/mal` is added to git.

`observe/client.py` is untouched.

---

## Non-goals

| Cap | Held |
| --- | --- |
| Live capital, trading keys, PumpPortal trade API | Out |
| `observe/client.py` / encoder promote | Untouched |
| Continuous observe-wiring | Out |
| Production lock of `global_95bps` or `launchlab_init` | Out. Both stay **Proposed** |
| Discovery promote, Graph revive, X ingest, mark densify | Out |
| EXP-002c retune | Out |
| EXP-006 promote or harness rewrite | Out |
| Scored Oracle measure, invented EV / lift / alpha | Out. `measure.kind=none` |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete`. `closed_book_claim=false` |
| Host extracts in git, CI sealed-book close | Out |
| Host dry-run receipt capture beyond #60 | Out |
| Rewriting parent #49–#60 fill-sim CLIs | Out |
| New runtime align-pass object | Out |

Soft GATE PASS ≠ Discovery promote / continuous observe-wiring / production enum lock / densify / EXP-002c retune / Graph revive / X on host / live trading / EXP-006 promote.

---

## Cross-links

- Fill-sim bind: [PAPER-FILL-SIM-HOT-PACKET-EVALUATE-V0.md](PAPER-FILL-SIM-HOT-PACKET-EVALUATE-V0.md), [PR #57](https://github.com/vaanai/MAL/pull/57)
- Fill-sim scoreboard: [PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md), [PR #58](https://github.com/vaanai/MAL/pull/58)
- Fill-sim batch: [PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md), [PR #59](https://github.com/vaanai/MAL/pull/59)
- Fill-sim host-local dry-run: [PAPER-FILL-SIM-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md), [PR #60](https://github.com/vaanai/MAL/pull/60) squash `d3b5554` on `main`
- This align pass: [PR #61](https://github.com/vaanai/MAL/pull/61) squash `677e88b` on `main` (Soft GATE PASS Formal-stamped)
- Parent honesty align: [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md), [PR #54](https://github.com/vaanai/MAL/pull/54)
- Proof test: [tools/test_paper_fill_sim_incomplete_rpc_honesty_schema_align_v0.py](../tools/test_paper_fill_sim_incomplete_rpc_honesty_schema_align_v0.py)
- Manager digest: [SUMMARY.md](SUMMARY.md)
