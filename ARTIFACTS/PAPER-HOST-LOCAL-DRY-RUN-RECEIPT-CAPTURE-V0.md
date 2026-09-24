# Host-local dry-run receipt capture — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-host-local-dry-run-receipt-capture-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). Not a measure. Not an EXP. Not an executed host dry-run. Not a sealed-book close. |
| **Owner seat** | Proof (capture + Soft GATE); Scout (host-local path shape); Helm (AUTH) |
| **Commission** | Helm AUTH. Soft GATE required before merge. Capture around the merged host-local dry-run ([PR #53](https://github.com/vaanai/MAL/pull/53) @ `f17b59f`). Honesty align that left this capture waiting: [PR #54](https://github.com/vaanai/MAL/pull/54) @ `ca4e815`. |
| **Schema** | [paper-host-local-dry-run-receipt-capture-v0.schema.json](paper-host-local-dry-run-receipt-capture-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_host_local_dry_run_receipt_capture_v0/](../fixtures/paper_host_local_dry_run_receipt_capture_v0/) — capture records, not host extracts |
| **CLI** | `python -m tools.paper_host_local_dry_run_receipt_capture_v0` — example / validate. Calls the #53 CLI. Does not rewrite it. |
| **Parent dry-run** | [PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md). Not rewritten. |
| **Soft GATE** | **Required** before merge. Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers how refuse and receipt outcomes from the #53 host-local sealed JSONL dry-run are captured and cited. The capture is knowable from the invocation and from the dry-run rebuild or refuse. It does not read `/var/lib/mal`. It does not embed a receipt body, a parent batch, host bytes, a horizon, `Δ_exec`, or a return.

Citeable stack: [PR #49](https://github.com/vaanai/MAL/pull/49) @ `274faa2`, [PR #50](https://github.com/vaanai/MAL/pull/50) @ `224166c`, [PR #51](https://github.com/vaanai/MAL/pull/51) @ `aa31768`, [PR #52](https://github.com/vaanai/MAL/pull/52) @ `243e11b`, [PR #53](https://github.com/vaanai/MAL/pull/53) @ `f17b59f`, [PR #54](https://github.com/vaanai/MAL/pull/54) @ `ca4e815`.

---

## What is recorded

`capture_kind=receipt`. The object cites one checked-in #53 receipt by repo path. `recorded.receipt_body_embedded=false`. `carries.receipt_body=false`. `carries.host_bytes=false`.

| Capture fixture | Cited receipt | What the digest records |
| --- | --- | --- |
| `receipt_synthetic_replay.json` | `fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json` | `host_jsonl_read_on_receipt=false`. `paths_opened_on_receipt=true` (in-repo synthetic JSONL). `parent_digest_included=true` |
| `receipt_projection_on_synthetic.json` | `fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/projection_on_synthetic.json` | `host_jsonl_read_on_receipt=true` on synthetic files. `host_path_declared_on_receipt=false`. Book stays incomplete |
| `receipt_operator_declared.json` | `fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json` | `host_jsonl_read_on_receipt=true`. `host_path_declared_on_receipt=true`. `paths_opened_on_receipt=false`. `parent_digest_included=false`. `outcome.path_opened=false` |

The digest copies honesty bits already on that receipt: `host_jsonl_read`, `host_path_declared`, `parent_invoked`, `paths_opened`, and whether `parent_digest.included` is true. It does not copy rollup counts, horizons, or `Δ_exec`.

`outcome.reason=receipt_rebuilt`. `outcome.exit_class=0`. `outcome.var_lib_mal_opened=false` on every capture, including when the cited receipt has `host_jsonl_read=true`.

---

## What is refused

`capture_kind=refuse`. The object records that the #53 CLI exited `1`. `outcome.recorded=false`. `outcome.path_opened=false`. `recorded.host_bytes_embedded=false`. A refused path is not a failed measure. `measure.kind` stays `none`.

| Capture fixture | Reason | Forms the #53 CLI already refuses |
| --- | --- | --- |
| `refuse_host_path.json` | `host_path_not_opened` | `lexical_absolute_validate` on `/var/lib/mal/paper/paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0/receipt.json`. `lexical_absolute_receipt` on the documented `/var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl` and `observe-2026-09-21.jsonl` pair plus the two expectation paths. `cwd_abspath_validate` for cwd `/` with `var/lib/mal/paper/receipt.json`, and cwd `/var/lib` with `mal/paper/receipt.json`. `realpath_symlink_dotdot_validate` (temp symlink; `path_stored=false`). `outside_repo_receipt` for `/tmp/mal-paper-capture-outside-repo/observe-2026-09-20.jsonl` and the day-21 pair. `forms_refused=6`. `parent_batch_invoked_by_capture=false` |
| `refuse_dishonest_receipt.json` | `dishonest_receipt_refused` | In-memory probes on a synthetic-replay receipt: `closed_book_claim`, `measure_kind`, `var_lib_mal_opened`, `var_lib_mal_read`, `invented_ev`. `probes_refused=5`. `probe_document_checked_in=false`. The dishonest documents are not in git |

Path strings under `/var/lib/mal` on the host-path capture are the same strings #53 already stores on `operator_declared`. They are not file bytes. `knowable_at_t.host_file_bytes_required=false`.

The capture CLI `validate` uses the same open-refuse as #53 before `read_text`. A capture file path under `/var/lib/mal` exits `1` and is not read.

---

## Knowable at T

At capture time the knowable inputs are the invocation (command, path strings, receipt kind) and the #53 rebuild or refuse. `knowable_at_t.sealed_book_status=incomplete`. Numeric horizons, `Δ_exec`, and returns are not knowable on this path, so they are not recorded (`numeric_horizon_known=false`, `delta_exec_known=false`, `return_known=false`).

| `knowable_at_t.basis` | When |
| --- | --- |
| `rebuilt_receipt_honesty_digest` | A cited receipt rebuilt and matched the checked-in file |
| `host_path_refuse_without_open` | The six host-open forms exited `1` with no `read_text` and no parent batch |
| `dishonest_receipt_probe` | Each in-memory probe failed `validate_receipt` |

`host_jsonl_read_on_receipt=true` is the parent flag on the cited receipt. It does not set `closed_book_claim` and it does not mean this process read `/var/lib/mal`.

---

## Incomplete RPC

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `capture_does_not_close_the_sealed_book` |
| `measure.kind` | `none` |
| `measure.pass_fail_no_lift` | `false` |
| `measure.invented_ev` | `false` |
| `measure.invented_lift` | `false` |
| `measure.claims_alpha` | `false` |
| `graph_policy` | `cold` |
| `graph_lift` | `null` |
| `full_book.policy` | `dec007_both_arms_retained` |
| `full_book.both_arms_unchanged` | `true` |
| `full_book.reject_stamps_dropped` | `0` |
| `parent_dry_run.rewritten_by_this_stamp` | `false` |
| `honesty_align.rewritten_by_this_stamp` | `false` |

`schema_looser_than_cli` stays the historical id. [PR #54](https://github.com/vaanai/MAL/pull/54) closed it for the parent shapes. This schema const-refuses a closed book, `measure.kind` other than `none`, a host open, embedded host bytes, and a knowable return on the capture object. The CLI still rebuilds the capture. Do not widen the CLI down to a looser schema.

---

## CLI

From the repo root:

```bash
python -m tools.paper_host_local_dry_run_receipt_capture_v0 example --which synthetic-replay
python -m tools.paper_host_local_dry_run_receipt_capture_v0 example --which projection-on-synthetic
python -m tools.paper_host_local_dry_run_receipt_capture_v0 example --which operator-declared
python -m tools.paper_host_local_dry_run_receipt_capture_v0 example --which refuse-host-path
python -m tools.paper_host_local_dry_run_receipt_capture_v0 example --which refuse-dishonest-receipt
python -m tools.paper_host_local_dry_run_receipt_capture_v0 validate \
  fixtures/paper_host_local_dry_run_receipt_capture_v0/refuse_host_path.json
```

Exit `0` when a capture is printed or a file validates. Exit `1` when a capture does not rebuild or a path is under `/var/lib/mal`. There is no `PASS` / `FAIL_NO_LIFT` exit.

Proof:

```bash
python3 -m unittest tools.test_paper_host_local_dry_run_receipt_capture_v0
```

Honest fixtures pass schema and CLI. In-memory dishonest copies fail both. `observe/client.py` is untouched.

---

## Soft watches (non-blocking)

**Soft GATE is required** before merge. `soft_watches.blocking=false`. The watches do not fail merge of this registration.

Inherited from #49–#54, still listed, still non-blocking. `schema_looser_than_cli` remains the historical id closed by #54.

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `schema_looser_than_cli` | Historical id. This capture schema const-refuses the dishonest capture shapes. | Do not widen the CLI. |
| `synthetic_launchlab_shape` | Inherited name. | Still not an enum lock. |
| `graph_slot_shape_not_a_score` | Inherited name. Graph lift stays null. | This stamp does not score a slot. |
| `dec005_draft_unmerged` | Inherited. | Hooks only. Not a claim that DEC-005 merged. |
| `sealed_book_rpc_slice_incomplete` | The capture keeps the slice `incomplete`. | This capture does not close the sealed book. |
| `label_share_is_not_a_return` | Inherited scoreboard rule. This capture copies no label share. | A count is not EV. |
| `synthetic_sealed_day_not_a_host_extract` | Cited receipts are the #53 synthetic files. | Checked-in captures are not Oracle extracts. |
| `local_subset_is_not_dropped_history` | Inherited. | This stamp does not drop a reject arm. |
| `host_jsonl_not_readable_from_ci` | Host Oracle JSONL is not readable from CI. | A missing host file is not a failed measure. |
| `counts_are_not_returns` | Rollup counts are not copied here. | Counts are not returns. |
| `graph_stays_cold_on_this_stamp` | Graph lift stays null. | Do not treat a later graph scalar as scored here. |
| `host_jsonl_read_is_not_a_closed_book` | A cited receipt may have `host_jsonl_read=true`. | That bit does not set `closed_book_claim`. |
| `operator_path_string_is_not_a_host_extract` | Refuse and operator-declared paths may name `/var/lib/mal`. | The string is not file bytes. |
| `dry_run_does_not_ssh_or_open_var_lib_mal` | #53 and this capture do not SSH and do not open `/var/lib/mal`. | A refused host path is not a failed measure. |
| `parent_stamp_not_rewritten` | The #53 CLI, schema, and receipts stay as merged. | This registration cites that stamp. |
| `capture_records_refusal_not_a_measure` | `capture_kind=refuse` records exit class `1`. | `measure.kind` stays `none`. |
| `cited_receipt_is_not_a_host_extract` | Receipt captures cite repo paths. | The receipt body is not embedded. |
| `path_string_on_a_refuse_is_not_file_bytes` | The host-path capture stores path strings. | `path_stored=false` on the realpath form. `host_bytes_embedded=false`. |

`global_95bps` and `launchlab_init` stay **Proposed**.

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
| `PASS` / `FAIL_NO_LIFT` measure exit | Out |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete`. `closed_book_claim=false` |
| Executed host dry-run, host extracts in git, CI sealed-book close | Out |
| SSH, RPC, or a read of `/var/lib/mal` | Out |
| Rewriting the #53 dry-run CLI or any parent CLI | Out |
| Embedding the receipt body, parent batch, horizons, or `Δ_exec` | Out |
| Soft watches above | Listed, **non-blocking**. Soft GATE still **required** before merge |

---

## Cross-links

- Dry-run this capture cites: [PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md), [PR #53](https://github.com/vaanai/MAL/pull/53) @ `f17b59f`
- Honesty align that deferred this capture: [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md), [PR #54](https://github.com/vaanai/MAL/pull/54) @ `ca4e815`
- Parent batch: [PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md), [PR #52](https://github.com/vaanai/MAL/pull/52) @ `243e11b`
- Path/flag note: [paper_batch_host_local_sealed_jsonl_dry_run.md](../tools/paper_batch_host_local_sealed_jsonl_dry_run.md)
- Full book: [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Manager digest: [SUMMARY.md](SUMMARY.md)
