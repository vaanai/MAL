# Paper incomplete-RPC honesty — schema align v0

| | |
| --- | --- |
| **ID** | `paper-incomplete-rpc-honesty-schema-align-v0` |
| **Status** | **Proposed** registration (2026-09-24). Not a measure. Not an EXP. Not a sealed-book close. |
| **Owner seat** | Scout commission. Helm AUTH 2026-09-23. |
| **Parent** | Paper chain PRs [#49](https://github.com/vaanai/MAL/pull/49)–[#53](https://github.com/vaanai/MAL/pull/53), main tip `f17b59f` |
| **Runtime object** | **None.** This stamp does not add a receipt schema or a CLI. Cite the tightened parent schemas. |
| **Soft GATE** | **Required** before merge. Watches that stay true stay **non-blocking**. |

JSON Schema on the paper chain is the citeable shape. Before this stamp it was looser than the CLIs on dishonest shapes the CLIs already refused. This registration tightens those schemas. It does not weaken a CLI, close the sealed book, or invent EV / lift / alpha.

`measure.kind` stays `none` on the parent stamps that have a measure object. `dual_read.sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays `false`. Graph stays cold. `global_95bps` and `launchlab_init` stay Proposed.

---

## What aligned

| Shape the CLI already refused | Schema now refuses it |
| --- | --- |
| `closed_book_claim=true` while `sealed_book_rpc_slice=incomplete`, and any closed-book claim on these stamps | `const: false` on scoreboard, scoreboard expectation, batch, and host-local dry-run. `closed_book_claim` is not a legal key inside a batch `source_rows` object. Hot-packet `sealed_book_rpc_slice` stays `const: incomplete`. |
| `measure.kind` other than `none`, `pass_fail_no_lift=true`, `invented_ev` / `invented_lift` / `claims_alpha` true | `const` on scoreboard, batch, and host-local `measure` and `honesty`. |
| Host-path open claims: `var_lib_mal_opened=true`, `honesty.var_lib_mal_read=true`, `ci_claimed_sealed_book_close=true`, `host_extract_checked_into_git=true` | `const: false` on the host-local receipt. Those names are not legal keys inside a batch source row. An operator-declared path string under `/var/lib/mal` stays legal. The string is not an open claim. |
| Numeric horizon or `Δ_exec` while status is `null_ok` or `fixture_joined_null_explicit` | Horizon map values and `delta_exec.value` stay `const: null` on hot-packet, paper-evaluate, and scoreboard. Null is not a zero return. |
| Graph slot value kind, and `graph.cold=true` while a slot value is filled | Hot-packet `graphSlot` if/then. `early_wallet_dt_min_seconds` stays null. |
| Whole-number float on an integer slot (`1.0`, `2.0` on `prior_mint_count`), including when the packet is nested by `$ref` under paper-evaluate and paper-scoreboard | The draft 2020-12 integer check those schemas declare rejects a whole-number float. `malStrictJsonInteger` alone does not survive that `$ref`. A JSON integer such as `2` still passes. |
| `graph.cold=false` when slots are null, empty, or every value is null | Unfilled ⇒ `cold` const true. |
| Case-variant return keys and the batch CLI forbidden set (`Mean_Return`, `EV`, `burst_count`, `outcome_mark`, and the rest of that set) | Batch `propertyNames` pattern is case-insensitive and lists the CLI forbidden names. |
| `graph_lift_status` disagreeing with `packet.graph.cold` | Paper-evaluate if/then. |
| Return keys on a batch source row (`mean_return`, `lift_vs_random`, `ev`, and the same family the batch CLI forbids) | Batch `sourceNode.propertyNames`. `ws_payload` and `price_proxy` stay legal on the row. They are not copied onto the hot packet as a return. |

Parent CLIs are unchanged. They still rebuild the object and still exit `1` on these shapes.

---

## Schemas touched

| Schema | Diff |
| --- | --- |
| [hot-packet-v0.schema.json](hot-packet-v0.schema.json) | Slot value kind + hypothesis status. `graph.cold` agrees with filled slots. |
| [paper-evaluate-hot-packet-v0.schema.json](paper-evaluate-hot-packet-v0.schema.json) | `graph_lift_status` follows `input.packet.graph.cold`. Horizons and `Δ_exec` were already `const: null`. |
| [paper-scoreboard-sealed-fixture-v0.schema.json](paper-scoreboard-sealed-fixture-v0.schema.json) | Already `const` on closed book, `measure.kind=none`, and null horizons. No structural edit. Tests cite it. |
| [paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json) | Source rows refuse dishonest names at every object. Top-level closed book and `measure.kind` were already `const`. |
| [paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json](paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json) | Already `const` on the open-claim bits, closed book, and `measure.kind=none`. No structural edit. Tests cite it. |

There is no `paper-incomplete-rpc-honesty-schema-align-v0.schema.json`. Operators do not need a new runtime object. The citeable align-pass object is this file.

---

## Soft watches

**Closed by this stamp**

| Watch id | Status |
| --- | --- |
| `schema_looser_than_cli` | **CLOSED** for the dishonest shapes in [What aligned](#what-aligned), including the three Soft GATE holes on `42e9a1a` (whole-number float on an integer slot, unfilled `graph.cold=false`, case-variant forbidden names). Schema validation refuses them. The CLIs still refuse them. Do not widen a CLI down to an older schema. |

Parent stamps #49–#53 still emit `schema_looser_than_cli` inside `soft_watches.items`. That list is the historical registration of those stamps. It is not a claim that the honesty shapes above still pass schema. This stamp does not rewrite those CLIs and does not drop the id from parent fixtures.

**Still true, non-blocking** (`blocking=false` on the parent stamps). Soft GATE is still required. This stamp does not close them.

| Watch id | What stays true |
| --- | --- |
| `sealed_book_rpc_slice_incomplete` | The slice stays `incomplete`. |
| `counts_are_not_returns` | Batch and scoreboard counts are stamp counts. |
| `label_share_is_not_a_return` | A label share is a count fraction. |
| `graph_stays_cold_on_this_stamp` | Graph lift stays null. This stamp does not score a slot. |
| `host_jsonl_not_readable_from_ci` | Host Oracle JSONL is not readable from CI. |
| `synthetic_launchlab_shape` | LaunchLab fixtures stay a shape example. Not an enum lock. |
| `graph_slot_shape_not_a_score` | An allowlisted slot integer is not lift. |
| `dec005_draft_unmerged` | Clock names still follow draft PR #8. |
| `synthetic_sealed_day_not_a_host_extract` | Checked-in days are synthetic. |
| `local_subset_is_not_dropped_history` | A smaller local set is not a dropped reject arm. |
| `host_jsonl_read_is_not_a_closed_book` | `host_jsonl_read=true` does not set `closed_book_claim`. |
| `operator_path_string_is_not_a_host_extract` | Path strings under `/var/lib/mal` are not file bytes. |
| `dry_run_does_not_ssh_or_open_var_lib_mal` | The dry-run CLI does not SSH and does not open `/var/lib/mal`. |
| `parent_stamp_not_rewritten` | Parent CLIs are not rewritten by this align pass. |

CLI recompute of a label, a decision clock, or a whole board is still how `validate` works. A schema miss on that recompute is not permission to cite a closed book, a measure, a host open, or a numeric horizon.

---

## Proof

```bash
python3 -m unittest tools.test_paper_incomplete_rpc_honesty_schema_align_v0
```

The test loads the parent schemas with JSON Schema 2020-12. Honest fixtures from #49–#53 pass schema and CLI. In-memory dishonest copies fail schema and CLI. Those copies are not checked in. Nothing under `/var/lib/mal` is added to git.

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
| Scored Oracle measure, invented EV / lift / alpha | Out. `measure.kind=none` |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete`. `closed_book_claim=false` |
| Host extracts in git, CI sealed-book close | Out |
| Host dry-run receipt capture | Out. Waits |
| Rewriting parent CLIs | Out |
| New runtime align-pass object | Out |

---

## Cross-links

- Hot packet: [HOT-PACKET-V0.md](HOT-PACKET-V0.md), [hot-packet-v0.schema.json](hot-packet-v0.schema.json), [PR #49](https://github.com/vaanai/MAL/pull/49)
- Evaluate: [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md), [PR #50](https://github.com/vaanai/MAL/pull/50)
- Scoreboard: [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md), [PR #51](https://github.com/vaanai/MAL/pull/51)
- Batch: [PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md), [PR #52](https://github.com/vaanai/MAL/pull/52)
- Host-local dry-run: [PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md), [PR #53](https://github.com/vaanai/MAL/pull/53) @ `f17b59f`
- Proof test: [tools/test_paper_incomplete_rpc_honesty_schema_align_v0.py](../tools/test_paper_incomplete_rpc_honesty_schema_align_v0.py)
- Manager digest: [SUMMARY.md](SUMMARY.md)
