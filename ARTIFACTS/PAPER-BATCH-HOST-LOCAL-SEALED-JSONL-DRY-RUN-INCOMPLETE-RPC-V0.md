# Host-local sealed JSONL dry-run, incomplete RPC — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0` |
| **Status** | **Proposed** paper registration (2026-09-23). Not a measure. Not an executed host dry-run. |
| **Owner seat** | Proof (receipt + Soft GATE); Scout (host-local path shape); Helm (AUTH) |
| **Commission** | Helm AUTH 2026-09-23 (Scout commission). Soft GATE required before merge. Receipt around the merged parent batch ([PR #52](https://github.com/vaanai/MAL/pull/52) @ `243e11b`). |
| **Schema** | [paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json](paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/](../fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/) — synthetic receipts, not host extracts |
| **CLI** | `python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0` — example / validate / receipt on local files only |
| **Operator shape** | [paper_batch_host_local_sealed_jsonl_dry_run.md](../tools/paper_batch_host_local_sealed_jsonl_dry_run.md) — paths and flags only. No executed host stdout |
| **Parent** | [PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md). Not rewritten. |
| **Soft GATE** | **Required** before merge. Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). #52 watches ride on this stamp with `blocking=false`. |

This file registers the **operator-local dry-run shape** for running the already-merged parent CLI against host-local day-aligned sealed observe JSONL. Courier calendar days are `2026-09-20` and `2026-09-21`. The host name in the receipt is `mal-core-vnic`. The documented paths sit under `/var/lib/mal`.

It is the same class as [PR #49](https://github.com/vaanai/MAL/pull/49)–[PR #52](https://github.com/vaanai/MAL/pull/52). It is **not** an EXP. It is **not** a scored Oracle measure. It is **not** a claim that CI, or this registration, executed a sealed-book close.

---

## What a host-local dry-run is

An operator on `mal-core-vnic` can point the parent CLI at the sealed observe files for those two courier days, with `--fixture-origin sealed_row_projection`. That flag is already specified on the parent. This stamp records the command as a **receipt**: paths, flags, and honesty bits.

| Piece | v0 fact |
| --- | --- |
| Object | `type=paper_dry_run_receipt` / `schema_version=paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0` / `id=paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0` |
| Parent | `paper-batch-oracle-sealed-day-incomplete-rpc-v0` @ `243e11b` (PR #52). `parent.rewritten_by_this_stamp=false` |
| Days | `2026-09-20`, `2026-09-21` |
| Host string | `invocation.host=mal-core-vnic`. `invocation.host_contacted=false` |
| Read path of **this** CLI | In-repo files only, and only when `parent_invoked=true`. `rpc=false`. `ssh_to_host=false`. `var_lib_mal_opened=false` |
| Parent body | Not copied. `carries.parent_batch_body=false`. `carries.rollup_counts=false`. `carries.horizons=false`. `carries.delta_exec=false`. `carries.host_bytes=false` |

Three checked-in receipts, all synthetic as files in git:

| `receipt_kind` | What it records |
| --- | --- |
| `synthetic_replay` | Parent CLI invoked on the parent's checked-in synthetic JSONL. `fixture_origin=synthetic`. `host_jsonl_read=false` |
| `projection_on_synthetic` | Same synthetic files, parent flag `--fixture-origin sealed_row_projection`. `host_jsonl_read=true`. Paths stay under `fixtures/`. `var_lib_mal_opened=false` |
| `operator_declared` | Documented `/var/lib/mal/...` path strings and the same parent flag. `host_jsonl_read=true`. `host_path_declared=true`. `parent_invoked=false`. This process does not open those paths |

---

## What a host-local dry-run is not

| Claim | Held here |
| --- | --- |
| Scored Oracle measure | `measure.kind=none`. `honesty.scored_oracle_measure=false` |
| Closed sealed book | `dual_read.sealed_book_rpc_slice=incomplete`. `dual_read.closed_book_claim=false` |
| CI or this PR executed a host read | `honesty.executed_host_sealed_book=false`. `honesty.ci_claimed_sealed_book_close=false`. `honesty.var_lib_mal_read=false` |
| Host extract in git | `honesty.host_extract_checked_into_git=false`. `carries.host_bytes=false` |
| EXP, EV, lift, alpha | `honesty.invented_ev=false`. `honesty.invented_lift=false`. `honesty.claims_alpha=false`. No `PASS` / `FAIL_NO_LIFT` exit |
| Rewrite of the parent stamp | Parent artifact, parent CLI, and [paper_batch_oracle_run.md](../tools/paper_batch_oracle_run.md) stay as merged |

A receipt may set `input.host_jsonl_read=true` and may store an operator-local path under `/var/lib/mal`. Those fields do not set `closed_book_claim`, do not set `measure.kind`, and do not mean this process read the host file.

---

## Incomplete RPC

The parent stamp already keeps the sealed full book **incomplete**. This receipt repeats that honesty on its own object and, when it actually calls the parent, on the thin digest.

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `host_local_dry_run_does_not_close_the_sealed_book` |
| `measure.kind` | `none` |
| `measure.pass_fail_no_lift` | `false` |
| `measure.invented_ev` | `false` |
| `measure.invented_lift` | `false` |
| `measure.claims_alpha` | `false` |
| `input.var_lib_mal_opened` | `false` on every receipt, including `host_jsonl_read=true` |
| `input.ssh_to_host` | `false` |
| `input.rpc` | `false` |
| Parent digest, when `included=true` | `sealed_book_rpc_slice=incomplete`, `closed_book_claim=false`, `measure_kind=none`, `executed_host_sealed_book=false`, `scored_oracle_measure=false`, `batch_embedded=false` |
| Parent digest, operator-declared | `included=false`. Reason `operator_shape_not_executed_by_this_registration` |
| `honesty.executed_host_sealed_book` | `false` |
| `honesty.scored_oracle_measure` | `false` |
| `honesty.ci_claimed_sealed_book_close` | `false` |

`projection_on_synthetic` is the CI proof that the parent flag can stamp `host_jsonl_read=true` on synthetic files while the book stays incomplete. `operator_declared` is the proof that the host path string can sit on a receipt while `var_lib_mal_opened` stays false. Neither file is a host extract.

---

## DEC-007 both arms

This stamp does not edit DEC-007 and does not drop an arm. It also does not publish a new population count. Rollup counts stay on the parent batch when an operator runs that CLI. They are not copied here (`carries.rollup_counts=false`), so this receipt cannot be read as a return.

| Field | Value |
| --- | --- |
| `full_book.policy` | `dec007_both_arms_retained` |
| `full_book.arm_retained` | `true` |
| `full_book.deletes_detect_history` | `false` |
| `full_book.reject_stamps_dropped` | `0` |
| `full_book.local_set_is_not_the_sealed_book` | `true` |
| `full_book.both_arms_unchanged` | `true` |

Graph stays cold. `graph.slots` are not attached. `graph_lift=null`. `graph_policy=cold`. H-G2, ordinal buckets, NH-Index, and NH-G3a stay off this receipt.

Horizons and `Δ_exec` are not on this receipt (`carries.horizons=false`, `carries.delta_exec=false`). The parent batch, when run, still leaves those null under its own stamp. This registration does not fill them.

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge of this registration | Docs + receipt schema + synthetic receipts + receipt CLI. **Does not** run the host dry-run |
| Input | Three receipts. Two replay the parent on the parent's synthetic JSONL. One records host path strings and does not open them |
| Encoder | **Not** promoted. `observe/client.py` is untouched |
| Enum production lock | **No.** `global_95bps` and `launchlab_init` stay **Proposed** |
| Discovery promote | **No.** No Graph revive, no X ingest, no mark densify |
| EXP-002c | **Not** retuned. No new evaluate threshold. No invented lift |
| EV / alpha | **No.** `measure.kind=none` |
| Measure exit | **None.** Exit `0` when a receipt is printed. Exit `1` when input is not a valid receipt or a path is under `/var/lib/mal`. No `PASS` / `FAIL_NO_LIFT` |
| Oracle measure | **No.** `honesty.scored_oracle_measure=false`. `honesty.executed_host_sealed_book=false` |
| Trading | Paper only. No live capital, no trading keys, no PumpPortal trade API |
| Host access from this CLI | No RPC. No SSH. No read of `/var/lib/mal` |

`honesty` is const-false on scored measure, executed host sealed book, CI sealed-book close, SSH, host extract in git, `/var/lib/mal` read, wiring, encoder promote, enum lock, Discovery promote, graph-lane revive, invented lift, invented EV, alpha claim, EXP-002c retune, live capital, trading keys, and PumpPortal trade API. A receipt that flips those bits does not validate.

---

## Soft watches (non-blocking)

**Soft GATE is required** before merge. `soft_watches.blocking=false`. `soft_watches.source=hot_packet_v0_soft_gate_pr49_paper_evaluate_pr50_scoreboard_pr51_batch_pr52`. The watches do not fail merge of this registration.

Inherited from hot-packet v0 (PR #49), paper-evaluate (PR #50), paper-scoreboard (PR #51), and the parent batch (PR #52). They ride **non-blocking** on this stamp:

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `schema_looser_than_cli` | JSON Schema is the citeable shape. The CLI rebuilds the receipt and refuses a closed book, a measure kind, and a host-path open. | Do not widen the CLI down to the schema. Do not hold this consumer for a schema rewrite. |
| `synthetic_launchlab_shape` | Inherited name from the parent chain. | Still not an enum lock. |
| `graph_slot_shape_not_a_score` | Inherited name. This stamp keeps graph lift null. | `graph_lift` stays null. |
| `dec005_draft_unmerged` | Inherited from the parent chain. | Hooks only. Not a claim that DEC-005 merged. |
| `sealed_book_rpc_slice_incomplete` | This receipt and any parent digest keep the slice `incomplete`. | This dry-run does not close the sealed book. |
| `label_share_is_not_a_return` | Inherited scoreboard rule. This receipt does not copy label shares. | Do not read a parent count as EV. |
| `synthetic_sealed_day_not_a_host_extract` | Day strings match calendar days sealed observe also uses. | Checked-in receipts are synthetic. |
| `local_subset_is_not_dropped_history` | Inherited scoreboard rule. | This stamp does not drop a reject arm. |
| `host_jsonl_not_readable_from_ci` | Host Oracle JSONL is not readable from CI or from a cloud agent. | A missing host file is not a failed measure. |
| `counts_are_not_returns` | Parent rollup counts are stamp counts. They are not copied onto this receipt. | Counts are not returns, not EV, and not lift. |
| `graph_stays_cold_on_this_stamp` | Graph lift stays null. | Do not treat a later graph scalar as already scored here. |

Named on this registration:

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `host_jsonl_read_is_not_a_closed_book` | `input.host_jsonl_read=true` is the parent flag `sealed_row_projection`. | It does not set `closed_book_claim` or `measure.kind`. |
| `operator_path_string_is_not_a_host_extract` | The operator-declared receipt stores `/var/lib/mal/...` path strings. | Those strings are not file bytes. Nothing under `/var/lib/mal` is checked in. |
| `dry_run_does_not_ssh_or_open_var_lib_mal` | This CLI does not SSH to `mal-core-vnic` and refuses to open `/var/lib/mal`. | A refused host path is not a failed measure. |
| `parent_stamp_not_rewritten` | Parent artifact, parent CLI, and the parent runbook stay as merged in PR #52. | This registration is a receipt around that stamp. |

---

## CLI

From the repo root:

```bash
python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which synthetic-replay
python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which projection-on-synthetic
python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which operator-declared
python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 validate \
  fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/operator_declared.json
python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 receipt \
  --fixture-origin sealed_row_projection \
  --jsonl fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-20.jsonl \
  --jsonl fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-21.jsonl \
  --expectation fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json \
  --expectation fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json
```

`--which` values: `synthetic-replay`, `projection-on-synthetic`, `operator-declared`.

`receipt` wraps the parent batch CLI in-process. It prints a receipt, not the parent batch body. It accepts only the courier pair `2026-09-20` / `2026-09-21`. A path under `/var/lib/mal` exits `1` before the parent CLI is called. There is no SSH client in this module.

`validate` uses that same refuse, lexical check then realpath, before `read_text`. Soft GATE FAIL on this draft (a host-path receipt was accepted because `validate` read `/var/lib/mal` first) is closed by that order: exit `1`, no file open, no `parent_main`. Soft GATE FAIL #2 is closed the same way for a relative arg: refuse `os.path.abspath` (the cwd path `read_text` opens) before any read, including when that abspath is under `/var/lib/mal` and the repo-joined path is not. The locked forms are cwd `/` with arg `var/lib/mal/...`, and cwd `/var/lib` with arg `mal/...`. Soft GATE FAIL #3 is closed by a further check that calls `os.path.realpath` on the cwd-joined string before `abspath` or lexical `normpath`, so a symlink/`..` component is still visible to the kernel. That realpath under `/var/lib/mal` exits `1` with no `read_text`.

`example --which operator-declared` writes the documented host paths and does not open them. `host_jsonl_read` on that receipt is the declared parent flag. `closed_book_claim` stays false.

Exit `0` when a receipt is printed or a file validates. Exit `1` when a file is missing, a receipt does not rebuild, or a path is under `/var/lib/mal`. There is no measure exit code.

| Fixture | What it shows |
| --- | --- |
| `synthetic_replay.json` | Parent invoked on synthetic JSONL. `host_jsonl_read=false`. Book incomplete |
| `projection_on_synthetic.json` | Parent flag `sealed_row_projection` on those same synthetic files. `host_jsonl_read=true`. Book incomplete. Host bytes not read |
| `operator_declared.json` | Host path strings for `mal-core-vnic`. Parent not invoked. `var_lib_mal_opened=false` |

Checked-in fixtures are **synthetic receipts**. They are not Oracle extracts and they are not lift evidence. The JSONL they replay, when they replay any, is the parent's synthetic pair.

The command an operator would run **on the host** against `/var/lib/mal` remains the parent CLI, documented in [paper_batch_oracle_run.md](../tools/paper_batch_oracle_run.md) and repeated as a flag shape in the sibling runbook. This registration does not check in that stdout.

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
| Closed-book claim when `host_jsonl_read=true` or a host path is recorded | Out. `closed_book_claim` stays `false` |
| Host extracts in git, CI claim of a sealed-book close | Out |
| SSH to `mal-core-vnic`, RPC, or a read of `/var/lib/mal` by this CLI | Out |
| Rewriting the parent batch stamp | Out |
| Soft watches above | Listed, **non-blocking**. Soft GATE still **required** before merge |

---

## Cross-links

- Parent batch (not rewritten): [PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md), [paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json), [PR #52](https://github.com/vaanai/MAL/pull/52) @ `243e11b`
- Parent operator flags: [paper_batch_oracle_run.md](../tools/paper_batch_oracle_run.md)
- This stamp's path/flag note: [paper_batch_host_local_sealed_jsonl_dry_run.md](../tools/paper_batch_host_local_sealed_jsonl_dry_run.md)
- Scoreboard: [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md)
- Evaluate: [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md)
- Decode packet: [HOT-PACKET-V0.md](HOT-PACKET-V0.md)
- Pipeline: [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md)
- Full book: [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Manager digest: [SUMMARY.md](SUMMARY.md)
- Parent registrations: [PR #49](https://github.com/vaanai/MAL/pull/49), [PR #50](https://github.com/vaanai/MAL/pull/50), [PR #51](https://github.com/vaanai/MAL/pull/51), [PR #52](https://github.com/vaanai/MAL/pull/52)
