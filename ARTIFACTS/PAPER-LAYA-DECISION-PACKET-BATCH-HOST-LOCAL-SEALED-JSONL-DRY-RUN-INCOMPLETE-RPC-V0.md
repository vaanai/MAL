# LAYA decision-packet batch host-local sealed JSONL dry-run, incomplete RPC — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). **Soft GATE PASS Formal-stamped** (Lyra; kill `bc-6e0d511c-983b-59c3-8ca8-c388292ea5ed`; implement `bc-287de2e1-0979-5997-bc22-8e6288d630b6`; head `e0090890a1de5e3d8a5fd7d3aaab72a9cf3de1e5`; [Soft GATE comment](https://github.com/vaanai/MAL/pull/70#issuecomment-5813072359); FAIL #1 [closed](https://github.com/vaanai/MAL/pull/70#issuecomment-5812727576) (`bc-b8e638fa-8495-556e-a536-06a0ccfc4a20`); FAIL #2 [closed](https://github.com/vaanai/MAL/pull/70#issuecomment-5812927365) (`bc-2755445b-b73e-5f3f-8964-c616a34373d0`); merged [PR #70](https://github.com/vaanai/MAL/pull/70) squash pending on `main`). Not a measure. Not an executed host dry-run. |
| **Owner seat** | Proof (receipt + Soft GATE); Scout (host-local path shape); Helm (AUTH) |
| **Commission** | Receipt around the merged LAYA decision-packet parent batch ([PR #69](https://github.com/vaanai/MAL/pull/69) squash `adadaa8` on `main`). Parent #49–#69 CLIs and EXP-006 harness are **not** rewritten. |
| **Schema** | [paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json](paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/](../fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/) — synthetic receipts, not host extracts |
| **CLI** | `python -m tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0` — example / validate / receipt on local files only |
| **Operator shape** | [paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run.md](../tools/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run.md) — paths and flags only. No executed host stdout |
| **Parent** | [PAPER-LAYA-DECISION-PACKET-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-LAYA-DECISION-PACKET-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md). Not rewritten. |
| **Soft GATE** | **PASS** — Formal stamp Lyra (kill `bc-6e0d511c-983b-59c3-8ca8-c388292ea5ed`; implement `bc-287de2e1-0979-5997-bc22-8e6288d630b6`; head `e0090890a1de5e3d8a5fd7d3aaab72a9cf3de1e5`; [Soft GATE comment](https://github.com/vaanai/MAL/pull/70#issuecomment-5813072359); FAIL #1–#2 **closed**). Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers the **operator-local dry-run shape** for running the merged LAYA decision-packet parent batch CLI against host-local day-aligned sealed JSONL calendar labels `2026-09-20` and `2026-09-21` on `mal-core-vnic`, with documented paths under `/var/lib/mal`.

It is the same receipt class as [PR #53](https://github.com/vaanai/MAL/pull/53) and [PR #60](https://github.com/vaanai/MAL/pull/60), but the parent CLI is the LAYA decision-packet batch from [PR #69](https://github.com/vaanai/MAL/pull/69). It is **not** an EXP. It is **not** a scored Oracle measure. It is **not** a claim that CI, or this registration, executed a sealed-book close.

Merge ≠ executed host dry-run ≠ Oracle measure ≠ LAYA authorize-run ≠ risk-gate unlock ≠ live trading ≠ EXP-006 promote.

**Soft GATE PASS** (Formal stamp Lyra). Soft GATE PASS ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X keys on host ≠ live trading ≠ EXP-006 promote ≠ LAYA authorize-run ≠ **risk-gate unlock**.

---

## What a host-local dry-run is

An operator on `mal-core-vnic` can point the parent batch CLI at `decision-day-YYYY-MM-DD.json` manifests and matching expectation files for those two courier days (calendar-aligned with sealed observe JSONL names). This stamp records the command as a **receipt**: sealed JSONL label paths, manifest paths, flags, and honesty bits.

| Piece | v0 fact |
| --- | --- |
| Object | `type=paper_dry_run_receipt` / `schema_version=paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0` / `id=paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0` |
| Parent | `paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0` @ `adadaa8` (PR #69). `parent.rewritten_by_this_stamp=false` |
| Days | `2026-09-20`, `2026-09-21` |
| Host string | `invocation.host=mal-core-vnic`. `invocation.host_contacted=false` |
| Read path of **this** CLI | In-repo manifest and expectation files only when `parent_invoked=true`. Sealed JSONL label strings are not opened. `rpc=false`. `ssh_to_host=false`. `var_lib_mal_opened=false` |
| Parent body | Not copied. `carries.parent_batch_body=false`. `carries.rollup_counts=false`. `carries.horizons=false`. `carries.delta_exec=false`. `carries.host_bytes=false` |

Three checked-in receipts, all synthetic as files in git:

| `receipt_kind` | What it records |
| --- | --- |
| `synthetic_replay` | Parent batch CLI invoked on checked-in synthetic manifests. `fixture_origin=synthetic`. `host_jsonl_read=false` |
| `projection_on_synthetic` | Same synthetic manifests; receipt `fixture_origin=sealed_row_projection`. `host_jsonl_read=true`. Sealed JSONL label strings cite parent #69 fixtures under `fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/` (not opened). `var_lib_mal_opened=false` |
| `operator_declared` | Documented `/var/lib/mal/...` sealed JSONL, manifest, and expectation path strings. `host_jsonl_read=true`. `host_path_declared=true`. `parent_invoked=false`. This process does not open those paths |

When the parent is invoked, `parent_digest.digest_label_arms` lists `runner` then `reject` (DEC-007 order on digest stamp counts). The batch body and scoreboards are not embedded.

`risk_gate.decision=locked`, `risk_gate.unlock=false`, `laya.authorize_run=false`, `laya.risk_gate_unlock=false`, `laya.live_trading=false` on the receipt and on parent digest when included.

---

## What a host-local dry-run is not

| Claim | Held here |
| --- | --- |
| Scored Oracle measure | `measure.kind=none`. `honesty.scored_oracle_measure=false` |
| Closed sealed book | `dual_read.sealed_book_rpc_slice=incomplete`. `dual_read.closed_book_claim=false` |
| CI or this PR executed a host read | `honesty.executed_host_sealed_book=false`. `honesty.ci_claimed_sealed_book_close=false`. `honesty.var_lib_mal_read=false` |
| Host extract in git | `honesty.host_extract_checked_into_git=false`. `carries.host_bytes=false` |
| LAYA authorize-run / risk-gate unlock / live | `laya.authorize_run=false`; `laya.risk_gate_unlock=false`; `laya.live_trading=false`; `risk_gate.unlock=false` |
| EXP-006 promote / harness rewrite | `honesty.exp006_promoted=false`. `honesty.exp006_harness_rewritten=false` |
| Marks join as scored measure | `input.marks_joined=false`. `honesty.marks_joined_on_this_stamp=false` |
| EV / alpha / measure exit | No `PASS` / `FAIL_NO_LIFT` exit |
| Rewrite of the parent stamp | Parent artifact, parent CLI stay as merged in PR #69 |

A receipt may set `input.host_jsonl_read=true` and may store operator-local path strings under `/var/lib/mal`. Those fields do not set `closed_book_claim`, do not set `measure.kind`, and do not mean this process read the host file.

---

## Incomplete RPC

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `laya_decision_packet_host_local_dry_run_does_not_close_the_sealed_book` |
| `measure.kind` | `none` |
| `input.var_lib_mal_opened` | `false` on every receipt |
| Parent digest, when `included=true` | `sealed_book_rpc_slice=incomplete`, `closed_book_claim=false`, `measure_kind=none`, `marks_joined=false`, `digest_label_arms` on runner/reject, `host_jsonl_read=false` on parent batch input, `batch_embedded=false` |

---

## DEC-007 both arms

`full_book.policy=dec007_both_arms_retained`, `reject_stamps_dropped=0`. Rollup digest stamp counts stay on the parent batch when an operator runs that CLI. They are not copied here (`carries.rollup_counts=false`).

Graph stays cold. Horizons and `Δ_exec` are not on this receipt (`carries.horizons=false`, `carries.delta_exec=false`).

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge of this registration | Docs + receipt schema + synthetic receipts + receipt CLI. **Does not** run the host dry-run |
| `observe/client.py` | **Untouched** |
| Soft GATE at merge | **PASS** (Formal stamp Lyra; kill `bc-6e0d511c-983b-59c3-8ca8-c388292ea5ed`) — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 promote / LAYA authorize-run / **risk-gate unlock** |
| Host access from this CLI | No RPC. No SSH. Refuses `/var/lib/mal`, `//var/lib/mal`, `///var/lib/mal`, relative/`./` forms, lexical `..` under that root, and collapse forms **lexically before any filesystem touch** |

---

## Soft watches (non-blocking)

**Soft GATE PASS** (Formal stamp Lyra). `soft_watches.blocking=false`. Inherited #67–#69 watches plus dry-run watches on this stamp. They do not fail merge of this registration.

Named on this registration:

| Watch id | Why it does not block |
| --- | --- |
| `host_jsonl_read_is_not_a_closed_book` | Label path strings only |
| `operator_path_string_is_not_a_host_extract` | Path strings are not bytes |
| `dry_run_does_not_ssh_or_open_var_lib_mal` | Refused host paths are not a failed measure |
| `parent_stamp_not_rewritten` | Receipt around PR #69 parent |
| `decision_packet_batch_host_local_not_a_host_extract` | Checked-in receipts are synthetic |
| `decision_packet_batch_counts_are_not_returns` | Digest arms are vocabulary, not EV |

---

## CLI

```bash
python -m tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which synthetic-replay
python -m tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which projection-on-synthetic
python -m tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which operator-declared
python -m tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 validate \
  fixtures/paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json
python -m tools.paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 receipt \
  --receipt-kind projection_on_synthetic \
  --jsonl fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-20.jsonl \
  --jsonl fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-21.jsonl \
  --manifest fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/decision-day-2026-09-20.json \
  --manifest fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/decision-day-2026-09-21.json \
  --expectation fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json \
  --expectation fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json
```

`validate` and `receipt` refuse host-root paths lexically (including `//var/lib/mal` and relative `var/lib/mal/...` at cwd `/` or `/var/lib`) before `read_text`, `stat`, or parent invocation. Exit `0` when a receipt validates or prints. Exit `1` on invalid input or refused host path. No measure exit.

Proof:

```bash
python3 -m unittest tools.test_paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0
```

---

## Non-goals

| Cap | Held |
| --- | --- |
| Rewrite #49–#69 parent CLIs or EXP-006 harness | Out |
| Host extract in git, SSH, RPC, `/var/lib/mal` read from CI | Out |
| `PASS` / `FAIL_NO_LIFT` exit | Out |
| Soft GATE PASS at merge | **PASS** (Formal stamp Lyra; kill `bc-6e0d511c-983b-59c3-8ca8-c388292ea5ed`) — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 / LAYA authorize-run / **risk-gate unlock** |

---

## Cross-links

- Parent LAYA decision-packet batch (#69): [PAPER-LAYA-DECISION-PACKET-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-LAYA-DECISION-PACKET-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md)
- Fill-sim batch dry-run receipt (#60): [PAPER-FILL-SIM-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md)
- [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- [SUMMARY.md](SUMMARY.md)
