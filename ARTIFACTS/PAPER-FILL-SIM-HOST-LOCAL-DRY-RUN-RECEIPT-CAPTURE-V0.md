# Fill-sim host-local dry-run receipt capture — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-fill-sim-host-local-dry-run-receipt-capture-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). **Soft GATE PASS Formal-stamped** (Lyra; kill `bc-b734540b-bd7c-586f-b18a-03a726adc849`; implement `bc-b1559a7e-f390-5fdb-8de1-ed305d224c8b`; merged [PR #62](https://github.com/vaanai/MAL/pull/62) squash `a1a3f26` on `main`). Not a measure. Not an EXP. Not an executed host dry-run. Not a sealed-book close. |
| **Owner seat** | Proof (capture + Soft GATE); Scout (host-local path shape); Helm (AUTH) |
| **Commission** | Helm AUTH. Capture around the merged fill-sim host-local dry-run ([PR #60](https://github.com/vaanai/MAL/pull/60) @ `d3b5554`). Honesty align on the fill-sim chain: [PR #61](https://github.com/vaanai/MAL/pull/61) @ `677e88b`. The #60 dry-run CLI is **not** rewritten. |
| **Schema** | [paper-fill-sim-host-local-dry-run-receipt-capture-v0.schema.json](paper-fill-sim-host-local-dry-run-receipt-capture-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_fill_sim_host_local_dry_run_receipt_capture_v0/](../fixtures/paper_fill_sim_host_local_dry_run_receipt_capture_v0/) — capture records, not host extracts |
| **CLI** | `python -m tools.paper_fill_sim_host_local_dry_run_receipt_capture_v0` — example / validate. Calls the #60 CLI. Does not rewrite it. |
| **Parent dry-run** | [PAPER-FILL-SIM-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md). Not rewritten. |
| **Soft GATE** | **PASS** — Formal stamp Lyra (kill `bc-b734540b-bd7c-586f-b18a-03a726adc849`; implement `bc-b1559a7e-f390-5fdb-8de1-ed305d224c8b`; squash `a1a3f26` on `main`). Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers how refuse and receipt outcomes from the #60 fill-sim host-local sealed JSONL dry-run are captured and cited. The capture is knowable from the invocation and from the dry-run rebuild or refuse. It does not read `/var/lib/mal`. It does not embed a receipt body, a parent batch, host bytes, a horizon, `Δ_exec`, or a return.

Citeable stack on `main` through #62 Formal @ `a1a3f26` (squash); parent dry-run #60 @ `d3b5554`; honesty align #61 @ `677e88b`.

Merge ≠ executed host dry-run ≠ Oracle measure ≠ EXP-006 promote.

**Soft GATE PASS** (Formal stamp Lyra). Soft GATE PASS ≠ Discovery promote / continuous observe-wiring / production enum lock / densify / EXP-002c retune / Graph revive / X on host / live trading / EXP-006 promote. The #60 CLI is not rewritten.

---

## What is recorded

`capture_kind=receipt`. The object cites one checked-in #60 receipt by repo path. `recorded.receipt_body_embedded=false`. `carries.receipt_body=false`. `carries.host_bytes=false`.

| Capture fixture | Cited receipt | What the digest records |
| --- | --- | --- |
| `receipt_synthetic_replay.json` | `fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json` | `host_jsonl_read_on_receipt=false`. `paths_opened_on_receipt=true`. `parent_digest_included=true`. `fill_sim_status_arms_on_receipt` lists both DEC-007 arms |
| `receipt_projection_on_synthetic.json` | `fixtures/.../projection_on_synthetic.json` | `host_jsonl_read_on_receipt=true` on synthetic files. `host_path_declared_on_receipt=false`. Book stays incomplete |
| `receipt_operator_declared.json` | `fixtures/.../operator_declared.json` | `host_jsonl_read_on_receipt=true`. `host_path_declared_on_receipt=true`. `paths_opened_on_receipt=false`. `parent_digest_included=false`. `fill_sim_status_arms_on_receipt=null`. `outcome.path_opened=false` |

The digest copies honesty bits already on that receipt. It does not copy rollup counts, horizons, or `Δ_exec`.

`outcome.reason=receipt_rebuilt`. `outcome.exit_class=0`. `outcome.var_lib_mal_opened=false` on every capture.

---

## What is refused

`capture_kind=refuse`. The object records that the #60 CLI exited `1`. `outcome.recorded=false`. `outcome.path_opened=false`. A refused path is not a failed measure. `measure.kind` stays `none`.

| Capture fixture | Reason | Forms the #60 CLI already refuses |
| --- | --- | --- |
| `refuse_host_path.json` | `host_path_not_opened` | `lexical_absolute_validate` on `/var/lib/mal/paper/paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0/receipt.json`. `lexical_absolute_receipt` on documented `/var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl` and `observe-2026-09-21.jsonl` plus fill-sim expectation paths. `cwd_abspath_validate` for cwd `/` with `var/lib/mal/paper/receipt.json`, and cwd `/var/lib` with `mal/paper/receipt.json`. `realpath_symlink_dotdot_validate` (temp symlink; `path_stored=false`). `outside_repo_receipt` for `/tmp/mal-paper-fill-sim-capture-outside-repo/...`. `forms_refused=6`. `parent_batch_invoked_by_capture=false` |
| `refuse_dishonest_receipt.json` | `dishonest_receipt_refused` | In-memory probes on a synthetic-replay receipt: `closed_book_claim`, `measure_kind`, `var_lib_mal_opened`, `var_lib_mal_read`, `invented_ev`. `probes_refused=5`. `probe_document_checked_in=false` |

Path strings under `/var/lib/mal` on the host-path capture are the same strings #60 already stores on `operator_declared`. They are not file bytes.

The capture CLI `validate` refuses `/var/lib/mal`, `//var/lib/mal`, and relative `var/lib/mal/...` **lexically before any filesystem touch** (same as #60), then `read_text`.

---

## Knowable at T

At capture time the knowable inputs are the invocation and the #60 rebuild or refuse. `knowable_at_t.sealed_book_status=incomplete`. Numeric horizons, `Δ_exec`, and returns are not knowable on this path.

---

## Incomplete RPC

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `capture_does_not_close_the_sealed_book` |
| `measure.kind` | `none` |
| `graph_policy` | `cold` |
| `graph_lift` | `null` |
| `full_book.policy` | `dec007_both_arms_retained` |
| `full_book.fill_sim_status_arms` | `documented_model_only`, `reject_arm_no_pretend_buy` (prefix order) |
| `parent_dry_run.rewritten_by_this_stamp` | `false` |
| `honesty_align.rewritten_by_this_stamp` | `false` |

`schema_looser_than_cli` is **CLOSED** for watched fill-sim shapes (#61). This capture schema const-refuses closed book, `measure.kind` other than `none`, host open claims, embedded host bytes, and knowable returns on the capture object.

---

## CLI

From the repo root:

```bash
python -m tools.paper_fill_sim_host_local_dry_run_receipt_capture_v0 example --which synthetic-replay
python -m tools.paper_fill_sim_host_local_dry_run_receipt_capture_v0 example --which projection-on-synthetic
python -m tools.paper_fill_sim_host_local_dry_run_receipt_capture_v0 example --which operator-declared
python -m tools.paper_fill_sim_host_local_dry_run_receipt_capture_v0 example --which refuse-host-path
python -m tools.paper_fill_sim_host_local_dry_run_receipt_capture_v0 example --which refuse-dishonest-receipt
python -m tools.paper_fill_sim_host_local_dry_run_receipt_capture_v0 validate \
  fixtures/paper_fill_sim_host_local_dry_run_receipt_capture_v0/refuse_host_path.json
```

Exit `0` when a capture is printed or a file validates. Exit `1` when a capture does not rebuild or a path is refused under the #60 host-path rules. No `PASS` / `FAIL_NO_LIFT` exit.

Proof:

```bash
python3 -m unittest tools.test_paper_fill_sim_host_local_dry_run_receipt_capture_v0
```

Honest fixtures pass schema and CLI. In-memory dishonest copies fail both. `observe/client.py` is untouched.

---

## Soft watches (non-blocking)

**Soft GATE PASS** (Formal stamp Lyra). `soft_watches.blocking=false`. Inherited fill-sim chain watches plus capture watches. They do not fail merge of this registration.

| Watch id | Why it does not block |
| --- | --- |
| `schema_looser_than_cli` | Historical id **CLOSED** on watched fill-sim shapes (#61) |
| `fill_sim_host_local_not_a_host_extract` | Cited receipts are the #60 synthetic files |
| `fill_sim_status_share_is_not_a_return` | Digest arms are vocabulary, not EV |
| `capture_records_refusal_not_a_measure` | `capture_kind=refuse` records exit class `1` |
| `cited_receipt_is_not_a_host_extract` | Receipt captures cite repo paths only |
| `path_string_on_a_refuse_is_not_file_bytes` | Host-path capture stores path strings only |
| `dry_run_does_not_ssh_or_open_var_lib_mal` | #60 and this capture do not open `/var/lib/mal` |
| `parent_stamp_not_rewritten` | The #60 CLI stays as merged |
| `graph_stays_cold_on_this_stamp` | Graph lift stays null |

`global_95bps` and `launchlab_init` stay **Proposed**.

---

## Non-goals

| Cap | Held |
| --- | --- |
| Rewrite #49–#61 parent CLIs or EXP-006 harness | Out |
| `observe/client.py` | Untouched |
| Host extract in git, SSH, RPC, CI read of `/var/lib/mal` | Out |
| Scored Oracle measure, invented EV / lift / alpha | Out. `measure.kind=none` |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete` |
| Executed host dry-run | Out |
| Embedding receipt body, batch, horizons, or `Δ_exec` | Out |

---

## Cross-links

- Dry-run this capture cites: [PAPER-FILL-SIM-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md), [PR #60](https://github.com/vaanai/MAL/pull/60) @ `d3b5554`
- Honesty align: [PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md), [PR #61](https://github.com/vaanai/MAL/pull/61) @ `677e88b`
- This capture: [PR #62](https://github.com/vaanai/MAL/pull/62) squash `a1a3f26` on `main` (Soft GATE PASS Formal-stamped)
- Oracle batch dry-run receipt (#55 pattern): [PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md)
- [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- [SUMMARY.md](SUMMARY.md)
