# Fill-sim batch host-local sealed JSONL dry-run, incomplete RPC — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). **Soft GATE PASS Formal-stamped** (Lyra; kill `bc-6d12d7af-2c6d-5f23-98f1-5d4dc8717db0`; implement `bc-7863de85-9f38-5fce-89e0-8f25464b2272`; [PR #60](https://github.com/vaanai/MAL/pull/60) tip `ec94ce5`; squash-merge on `main` pending). Not a measure. Not an executed host dry-run. |
| **Owner seat** | Proof (receipt + Soft GATE); Scout (host-local path shape); Helm (AUTH) |
| **Commission** | Receipt around the merged fill-sim parent batch ([PR #59](https://github.com/vaanai/MAL/pull/59) squash `4664363` on `main` @ `d352ea6` lab tip). Parent #49–#59 CLIs and EXP-006 harness are **not** rewritten. |
| **Schema** | [paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json](paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/](../fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/) — synthetic receipts, not host extracts |
| **CLI** | `python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0` — example / validate / receipt on local files only |
| **Operator shape** | [paper_fill_sim_batch_host_local_sealed_jsonl_dry_run.md](../tools/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run.md) — paths and flags only. No executed host stdout |
| **Parent** | [PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md). Not rewritten. |
| **Soft GATE** | **PASS** — Formal stamp Lyra (kill `bc-6d12d7af-2c6d-5f23-98f1-5d4dc8717db0`; implement `bc-7863de85-9f38-5fce-89e0-8f25464b2272`; [PR #60](https://github.com/vaanai/MAL/pull/60) tip `ec94ce5`). Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers the **operator-local dry-run shape** for running the merged fill-sim parent CLI against host-local day-aligned sealed observe JSONL. Courier calendar days are `2026-09-20` and `2026-09-21`. The host name in the receipt is `mal-core-vnic`. The documented paths sit under `/var/lib/mal`.

It is the same receipt class as [PR #53](https://github.com/vaanai/MAL/pull/53), but the parent CLI is the fill-sim batch from [PR #59](https://github.com/vaanai/MAL/pull/59). It is **not** an EXP. It is **not** a scored Oracle measure. It is **not** a claim that CI, or this registration, executed a sealed-book close.

Merge ≠ executed host dry-run ≠ Oracle measure ≠ EXP-006 promote.

**Soft GATE PASS** (Formal stamp Lyra). Soft GATE PASS ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X keys on host ≠ live trading ≠ EXP-006 promote.

---

## What a host-local dry-run is

An operator on `mal-core-vnic` can point the fill-sim parent CLI at the sealed observe files for those two courier days, with `--fixture-origin sealed_row_projection`. This stamp records the command as a **receipt**: paths, flags, and honesty bits.

| Piece | v0 fact |
| --- | --- |
| Object | `type=paper_dry_run_receipt` / `schema_version=paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0` / `id=paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0` |
| Parent | `paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0` @ `4664363` (PR #59). `parent.rewritten_by_this_stamp=false` |
| Days | `2026-09-20`, `2026-09-21` |
| Host string | `invocation.host=mal-core-vnic`. `invocation.host_contacted=false` |
| Read path of **this** CLI | In-repo files only, and only when `parent_invoked=true`. `rpc=false`. `ssh_to_host=false`. `var_lib_mal_opened=false` |
| Parent body | Not copied. `carries.parent_batch_body=false`. `carries.rollup_counts=false`. `carries.horizons=false`. `carries.delta_exec=false`. `carries.host_bytes=false` |

Three checked-in receipts, all synthetic as files in git:

| `receipt_kind` | What it records |
| --- | --- |
| `synthetic_replay` | Parent fill-sim batch CLI invoked on the parent's checked-in synthetic JSONL. `fixture_origin=synthetic`. `host_jsonl_read=false` |
| `projection_on_synthetic` | Same synthetic files, parent flag `--fixture-origin sealed_row_projection`. `host_jsonl_read=true`. Paths stay under `fixtures/`. `var_lib_mal_opened=false` |
| `operator_declared` | Documented `/var/lib/mal/...` path strings and the same parent flag. `host_jsonl_read=true`. `host_path_declared=true`. `parent_invoked=false`. This process does not open those paths |

When the parent is invoked, `parent_digest.fill_sim_status_arms` lists `documented_model_only` then `reject_arm_no_pretend_buy` (DEC-007 order). The batch body and scoreboards are not embedded.

---

## What a host-local dry-run is not

| Claim | Held here |
| --- | --- |
| Scored Oracle measure | `measure.kind=none`. `honesty.scored_oracle_measure=false` |
| Closed sealed book | `dual_read.sealed_book_rpc_slice=incomplete`. `dual_read.closed_book_claim=false` |
| CI or this PR executed a host read | `honesty.executed_host_sealed_book=false`. `honesty.ci_claimed_sealed_book_close=false`. `honesty.var_lib_mal_read=false` |
| Host extract in git | `honesty.host_extract_checked_into_git=false`. `carries.host_bytes=false` |
| EXP-006 promote / harness rewrite | `honesty.exp006_promoted=false`. `honesty.exp006_harness_rewritten=false` |
| Marks join as scored measure | `input.marks_joined=false`. `honesty.marks_joined_on_this_stamp=false` |
| EV / alpha / measure exit | No `PASS` / `FAIL_NO_LIFT` exit |
| Rewrite of the parent stamp | Parent artifact, parent CLI stay as merged in PR #59 |

A receipt may set `input.host_jsonl_read=true` and may store an operator-local path under `/var/lib/mal`. Those fields do not set `closed_book_claim`, do not set `measure.kind`, and do not mean this process read the host file.

---

## Incomplete RPC

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `fill_sim_host_local_dry_run_does_not_close_the_sealed_book` |
| `measure.kind` | `none` |
| `input.var_lib_mal_opened` | `false` on every receipt |
| Parent digest, when `included=true` | `sealed_book_rpc_slice=incomplete`, `closed_book_claim=false`, `measure_kind=none`, `marks_joined=false`, both fill-sim arms on `fill_sim_status_arms`, `batch_embedded=false` |

---

## DEC-007 both arms

`full_book.policy=dec007_both_arms_retained`, `reject_stamps_dropped=0`. Rollup counts stay on the parent batch when an operator runs that CLI. They are not copied here (`carries.rollup_counts=false`).

Graph stays cold. Horizons and `Δ_exec` are not on this receipt (`carries.horizons=false`, `carries.delta_exec=false`).

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge of this registration | Docs + receipt schema + synthetic receipts + receipt CLI. **Does not** run the host dry-run |
| `observe/client.py` | **Untouched** |
| Soft GATE at merge | **PASS** (Formal stamp Lyra; kill `bc-6d12d7af-2c6d-5f23-98f1-5d4dc8717db0`) — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 promote |
| Host access from this CLI | No RPC. No SSH. Refuses `/var/lib/mal`, `//var/lib/mal`, and relative `var/lib/mal/...` **lexically before any filesystem touch** |

---

## Soft watches (non-blocking)

**Soft GATE PASS** (Formal stamp Lyra). `soft_watches.blocking=false`. Inherited #49–#59 watches plus dry-run watches on this stamp. They do not fail merge of this registration.

Named on this registration:

| Watch id | Why it does not block |
| --- | --- |
| `host_jsonl_read_is_not_a_closed_book` | Parent flag only |
| `operator_path_string_is_not_a_host_extract` | Path strings are not bytes |
| `dry_run_does_not_ssh_or_open_var_lib_mal` | Refused host paths are not a failed measure |
| `parent_stamp_not_rewritten` | Receipt around PR #59 parent |
| `fill_sim_host_local_not_a_host_extract` | Checked-in receipts are synthetic |
| `fill_sim_status_share_is_not_a_return` | Digest arms are vocabulary, not EV |

---

## CLI

```bash
python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which synthetic-replay
python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which projection-on-synthetic
python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which operator-declared
python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 validate \
  fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json
python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 receipt \
  --fixture-origin sealed_row_projection \
  --jsonl fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-20.jsonl \
  --jsonl fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-21.jsonl \
  --expectation fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json \
  --expectation fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json
```

`validate` and `receipt` refuse host-root paths lexically (including `//var/lib/mal` and relative `var/lib/mal/...` at cwd `/` or `/var/lib`) before `read_text`, `stat`, or parent invocation. Exit `0` when a receipt validates or prints. Exit `1` on invalid input or refused host path. No measure exit.

Proof:

```bash
python3 -m unittest tools.test_paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0
```

---

## Non-goals

| Cap | Held |
| --- | --- |
| Rewrite #49–#59 parent CLIs or EXP-006 harness | Out |
| Host extract in git, SSH, RPC, `/var/lib/mal` read from CI | Out |
| `PASS` / `FAIL_NO_LIFT` exit | Out |
| Soft GATE PASS at merge | **PASS** (Formal stamp Lyra; kill `bc-6d12d7af-2c6d-5f23-98f1-5d4dc8717db0`) — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 |

---

## Cross-links

- Parent fill-sim batch (#59): [PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md)
- Oracle batch dry-run receipt (#53): [PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md)
- [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- [SUMMARY.md](SUMMARY.md)
