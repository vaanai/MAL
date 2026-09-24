# LAYA decision-packet host-local dry-run receipt capture — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). **Soft GATE PASS Formal-stamped** (Lyra; kill `bc-583eb833-6443-55f7-a685-b1ad3e166b6b`; head `cecdf6767c71d34da3927ba32646b674787eab45`; [Soft GATE comment](https://github.com/vaanai/MAL/pull/71#issuecomment-5813702684); FAIL #1 [closed](https://github.com/vaanai/MAL/pull/71#issuecomment-5813349728); FAIL #2 [closed](https://github.com/vaanai/MAL/pull/71#issuecomment-5813497572); open [PR #71](https://github.com/vaanai/MAL/pull/71) squash pending on `main`). Not a measure. Not an EXP. Not an executed host dry-run. Not a sealed-book close. |
| **Owner seat** | Proof (capture + Soft GATE); Scout (host-local path shape); Helm (AUTH) |
| **Commission** | Helm AUTH. Capture around the merged LAYA decision-packet host-local dry-run ([PR #70](https://github.com/vaanai/MAL/pull/70) @ `d020fc9`). Honesty align on the LAYA chain: [PR #66](https://github.com/vaanai/MAL/pull/66) @ `3128128`. The #70 dry-run CLI is **not** rewritten. |
| **Schema** | [paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.schema.json](paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0/](../fixtures/paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0/) — capture records, not host extracts |
| **CLI** | `python -m tools.paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0` — example / validate. Calls the #70 CLI. Does not rewrite it. |
| **Parent dry-run** | [PAPER-LAYA-DECISION-PACKET-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-LAYA-DECISION-PACKET-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md). Not rewritten. |
| **Soft GATE** | **PASS** — Formal stamp Lyra (kill `bc-583eb833-6443-55f7-a685-b1ad3e166b6b`; head `cecdf6767c71d34da3927ba32646b674787eab45`; [comment](https://github.com/vaanai/MAL/pull/71#issuecomment-5813702684)). Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers how refuse and receipt outcomes from the #70 LAYA decision-packet host-local sealed JSONL dry-run are captured and cited. The capture is knowable from the invocation and from the dry-run rebuild or refuse. It does not read `/var/lib/mal`. It does not embed a receipt body, a parent batch, host bytes, a horizon, `Δ_exec`, or a return.

Citeable stack on `main` through parent dry-run #70 @ `d020fc9` (squash); honesty align #66 @ `3128128`. Mirror of #55 / #62 on the LAYA decision-packet host-local dry-run.

Merge ≠ executed host dry-run ≠ Oracle measure ≠ LAYA authorize-run ≠ risk-gate unlock ≠ live.

**Soft GATE PASS** (Formal stamp Lyra). Soft GATE PASS ≠ Discovery promote / continuous observe-wiring / production enum lock / densify / EXP-002c retune / Graph revive / X on host / live trading / EXP-006 promote / LAYA authorize-run / **risk-gate unlock**. The #70 CLI is not rewritten.

---

## What is recorded

`capture_kind=receipt`. The object cites one checked-in #70 receipt by repo path. `recorded.receipt_body_embedded=false`. `carries.receipt_body=false`. `carries.host_bytes=false`.

| Capture fixture | Cited receipt | What the digest records |
| --- | --- | --- |
| `receipt_synthetic_replay.json` | `fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/synthetic_replay.json` | `host_jsonl_read_on_receipt=false`. `paths_opened_on_receipt=true`. `parent_digest_included=true`. `digest_label_arms_on_receipt` lists both DEC-007 arms (`runner`, `reject`) |
| `receipt_projection_on_synthetic.json` | `fixtures/.../projection_on_synthetic.json` | `host_jsonl_read_on_receipt=true` on synthetic files. `host_path_declared_on_receipt=false`. Book stays incomplete |
| `receipt_operator_declared.json` | `fixtures/.../operator_declared.json` | `host_jsonl_read_on_receipt=true`. `host_path_declared_on_receipt=true`. `paths_opened_on_receipt=false`. `parent_digest_included=false`. `digest_label_arms_on_receipt=null`. `outcome.path_opened=false` |

The digest copies honesty bits already on that receipt. It does not copy rollup counts, horizons, or `Δ_exec`.

`outcome.reason=receipt_rebuilt`. `outcome.exit_class=0`. `outcome.var_lib_mal_opened=false` on every capture. `risk_gate.decision=locked`. `risk_gate.unlock=false`. `laya.authorize_run=false`. `laya.risk_gate_unlock=false`. `laya.live_trading=false`.

---

## What is refused

`capture_kind=refuse`. The object records that the #70 CLI exited `1`. `outcome.recorded=false`. `outcome.path_opened=false`. A refused path is not a failed measure. `measure.kind` stays `none`.

| Capture fixture | Reason | Forms the #70 CLI already refuses |
| --- | --- | --- |
| `refuse_host_path.json` | `host_path_not_opened` | `lexical_absolute_validate` on `/var/lib/mal/paper/paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0/receipt.json`. `lexical_absolute_receipt` on documented `/var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl` and `observe-2026-09-21.jsonl` plus matching `decision-day-*.json` manifests and expectation paths. `cwd_abspath_validate` for cwd `/` with `var/lib/mal/paper/receipt.json`, and cwd `/var/lib` with `mal/paper/receipt.json`. `realpath_symlink_dotdot_validate` (temp symlink; `path_stored=false`). `outside_repo_receipt` for `/tmp/mal-paper-laya-capture-outside-repo/...`. `forms_refused=6`. `parent_batch_invoked_by_capture=false` |
| `refuse_dishonest_receipt.json` | `dishonest_receipt_refused` | In-memory probes on a synthetic-replay receipt: `closed_book_claim`, `measure_kind`, `var_lib_mal_opened`, `var_lib_mal_read`, `invented_ev`. `probes_refused=5`. `probe_document_checked_in=false` |

Path strings under `/var/lib/mal` on the host-path capture are the same strings #70 already stores on `operator_declared`. They are not file bytes.

The capture CLI `validate` refuses `/var/lib/mal`, `//var/lib/mal`, `///var/lib/mal`, relative and `./var/lib/mal/...`, lexical `..` under that root, and collapse forms (`//`, `./`, trailing `/`, backslash) inside allowed fixture prefixes **lexically before any filesystem touch**, then `read_text`.

---

## Knowable at T

At capture time the knowable inputs are the invocation and the #70 rebuild or refuse. `knowable_at_t.sealed_book_status=incomplete`. Numeric horizons, `Δ_exec`, and returns are not knowable on this path.

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
| `full_book.digest_label_arms` | `runner`, `reject` (prefix order) |
| `risk_gate.decision` | `locked` |
| `risk_gate.unlock` | `false` |
| `laya.authorize_run` | `false` |
| `laya.risk_gate_unlock` | `false` |
| `laya.live_trading` | `false` |
| `parent_dry_run.rewritten_by_this_stamp` | `false` |
| `honesty_align.rewritten_by_this_stamp` | `false` |

`schema_looser_than_cli` is **CLOSED** for watched LAYA shapes (#66). This capture schema const-refuses closed book, `measure.kind` other than `none`, host open claims, embedded host bytes, risk-gate unlock, LAYA authorize-run, and knowable returns on the capture object.

---

## CLI

From the repo root:

```bash
python -m tools.paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0 example --which synthetic-replay
python -m tools.paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0 example --which projection-on-synthetic
python -m tools.paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0 example --which operator-declared
python -m tools.paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0 example --which refuse-host-path
python -m tools.paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0 example --which refuse-dishonest-receipt
python -m tools.paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0 validate \
  fixtures/paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0/refuse_host_path.json
```

Exit `0` when a capture is printed or a file validates. Exit `1` when a capture does not rebuild or a path is refused under the #70 host-path rules. No `PASS` / `FAIL_NO_LIFT` exit.

Proof:

```bash
python3 -m unittest tools.test_paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0
```

Honest fixtures pass schema and CLI. In-memory dishonest copies fail both. `observe/client.py` is untouched.

---

## Soft watches (non-blocking)

Soft GATE **pending**. `soft_watches.blocking=false`. Inherited LAYA chain watches plus capture watches. They do not fail merge of this registration once Soft GATE passes.

| Watch id | Why it does not block |
| --- | --- |
| `schema_looser_than_cli` | Historical id **CLOSED** on watched LAYA shapes (#66) |
| `decision_packet_batch_host_local_not_a_host_extract` | Cited receipts are the #70 synthetic files |
| `decision_packet_batch_counts_are_not_returns` | Digest arms are vocabulary, not EV |
| `capture_records_refusal_not_a_measure` | `capture_kind=refuse` records exit class `1` |
| `cited_receipt_is_not_a_host_extract` | Receipt captures cite repo paths only |
| `path_string_on_a_refuse_is_not_file_bytes` | Host-path capture stores path strings only |
| `dry_run_does_not_ssh_or_open_var_lib_mal` | #70 and this capture do not open `/var/lib/mal` |
| `parent_stamp_not_rewritten` | The #70 CLI stays as merged |
| `graph_stays_cold_on_this_stamp` | Graph lift stays null |
| `decision_packet_batch_not_risk_gate_unlock` | `risk_gate.unlock=false` on capture |
| `decision_packet_batch_not_laya_authorize_run` | `laya.authorize_run=false` on capture |

`global_95bps` and `launchlab_init` stay **Proposed**.

---

## Non-goals

| Cap | Held |
| --- | --- |
| Rewrite #49–#70 parent CLIs or EXP-006 harness | Out |
| `observe/client.py` | Untouched |
| Host extract in git, SSH, RPC, CI read of `/var/lib/mal` | Out |
| Scored Oracle measure, invented EV / lift / alpha | Out. `measure.kind=none` |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete` |
| Executed host dry-run | Out |
| Embedding receipt body, batch, horizons, or `Δ_exec` | Out |
| LAYA authorize-run, risk-gate unlock, live | Out |

---

## Cross-links

- Dry-run this capture cites: [PAPER-LAYA-DECISION-PACKET-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-LAYA-DECISION-PACKET-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md), [PR #70](https://github.com/vaanai/MAL/pull/70) @ `d020fc9`
- Honesty align: [PAPER-LAYA-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-LAYA-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md), [PR #66](https://github.com/vaanai/MAL/pull/66) @ `3128128`
- Oracle batch dry-run receipt (#55 pattern): [PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md)
- Fill-sim capture (#62 pattern): [PAPER-FILL-SIM-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-FILL-SIM-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md)
- This capture: [PR #71](https://github.com/vaanai/MAL/pull/71) (Soft GATE PASS Formal-stamped; squash pending on `main`)
- [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- [SUMMARY.md](SUMMARY.md)
