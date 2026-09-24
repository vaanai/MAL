# Fill-sim oracle sealed-day paper batch, incomplete RPC — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). **Soft GATE PASS Formal-stamped** (Lyra; kill `bc-0e1391cf-be21-5491-a9e9-026f19c486e0`; implement `bc-3aa00e4f-01c6-556d-9b6c-c44b26fe7181`; merged [PR #59](https://github.com/vaanai/MAL/pull/59) squash `4664363` on `main`). Not a measure. Not a host run. |
| **Owner seat** | Proof (batch + Soft GATE); Scout (fill-sim spine stays on embedded stamps); Helm (AUTH) |
| **Commission** | Consumer of merged fill-sim scoreboard ([PR #58](https://github.com/vaanai/MAL/pull/58) squash `dfbab7a`), fill-sim bind ([PR #57](https://github.com/vaanai/MAL/pull/57)), and the #49–#52 packet spine. Parent #49–#58 CLIs and EXP-006 harness are **not** rewritten. |
| **Schema** | [paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/](../fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/) — synthetic, not host extracts |
| **CLI** | `python -m tools.paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0` — example / validate / batch on local JSON only |
| **Soft GATE** | **PASS** — Formal stamp Lyra (kill `bc-0e1391cf-be21-5491-a9e9-026f19c486e0`; implement `bc-3aa00e4f-01c6-556d-9b6c-c44b26fe7181`; squash `4664363` on `main`). Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers a **fixtures-only** day-aligned paper batch with fill-sim: sealed observe JSONL → [`hot_packet_v0`](HOT-PACKET-V0.md) → [`paper_evaluate_hot_packet_v0`](PAPER-EVALUATE-HOT-PACKET-V0.md) → [`paper_fill_sim_hot_packet_evaluate_v0`](PAPER-FILL-SIM-HOT-PACKET-EVALUATE-V0.md) → [`paper_fill_sim_scoreboard_sealed_fixture_v0`](PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md).

Merge ≠ Oracle measure ≠ EXP-006 promote ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ Graph revive ≠ live trading.

**Soft GATE PASS** (Formal stamp Lyra). Soft GATE PASS ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X keys on host ≠ live trading ≠ EXP-006 promote.

Cloud agents cannot read host Oracle JSONL. Checked-in day strings `2026-09-20` and `2026-09-21` are calendar labels used elsewhere; they are not a read of host `observe-*.jsonl`.

---

## What this batch is

| Stage | This registration |
| --- | --- |
| Detect | Local `observe-YYYY-MM-DD.jsonl` lines. Synthetic `ingest_hot` creates plus one skipped non-create per day |
| Decode | `tools.hot_packet_v0.validate_packet` on a sealed-overlay projection. Graph slots stay null |
| Evaluate | `tools.paper_evaluate_hot_packet_v0.stamp_from_packet` |
| Fill-sim bind | `tools.paper_fill_sim_hot_packet_evaluate_v0.bind_from_evaluate_stamp` |
| Runners / book | `tools.paper_fill_sim_scoreboard_sealed_fixture_v0.score_stamps`, one scoreboard per day |

| Piece | v0 fact |
| --- | --- |
| Read path | Local files only. `rpc=false`. `observe_jsonl_tail=false`. `host_extract_required=false`. `marks_joined=false`. No `/var/lib/mal` |
| Overlay | `sealed`. Enriched enum tags refused on the sealed row |
| RPC slice | `dual_read.sealed_book_rpc_slice=incomplete` on batch, scoreboards, packets, and fill-sim stamps |
| Closed book | `dual_read.closed_book_claim=false` everywhere. `fixture_join.closed_book=false` |
| Population | `local_set_is_not_the_sealed_book=true`. `n` is a stamp count |

---

## DEC-007 both arms

`full_book.policy=dec007_both_arms_retained`, `reject_stamps_dropped=0`. Each scoreboard keeps `label_rates.rows` with `runner` then `reject`.

`fill_sim_status_counts` lists `documented_model_only` then `reject_arm_no_pretend_buy` on every board. The reject fill-sim row stays on the table even when `n=0`.

Checked-in synthetic days (same census pattern as #52):

| Day | Projected | Runner | Reject | Skip | Reason with `n>0` |
| --- | --- | --- | --- | --- | --- |
| `2026-09-20` | 2 | 1 | 1 (`mint=UNK`) | `outcome_mark` | `missing_mint` |
| `2026-09-21` | 2 | 1 | 1 (`signature=UNK`) | migration `ingest_hot` | `missing_signature` |

Rollup `n=4`, runner `2`, reject `2`. `rollup.share_kind=count_fraction_not_a_return`. Counts are not returns, EV, or lift.

---

## Horizons and `Δ_exec`

Null with explicit status on embedded fill-sim stamps and scoreboards. Skipped mark `price_proxy` stays on the source row only.

`measure.kind=none`. `measure.pass_fail_no_lift=false`. No `PASS` / `FAIL_NO_LIFT` process exit.

---

## Graph stays cold

`graph_policy=cold`, `graph_lift=null`. Embedded packets and fill-sim stamps keep graph slots null.

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge | Docs + schema + synthetic fixtures + fixture CLI only |
| `observe/client.py` | **Untouched** |
| EXP-006 | **Not** promoted. Harness **not** rewritten |
| Marks | **Not** joined as a scored measure |
| Host | CLI refuses `/var/lib/mal` and `//var/lib/mal` **lexically** before any filesystem touch |
| Oracle measure | **No** |

**Soft GATE PASS** (Formal stamp Lyra). Soft GATE PASS ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X keys on host ≠ live trading ≠ EXP-006 promote.

---

## Soft watches (non-blocking)

**Soft GATE PASS** (Formal stamp Lyra). `soft_watches.blocking=false`. Inherited watches from #49–#58 remain listed and non-blocking.

Named on this registration:

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `fill_sim_batch_not_a_host_extract` | Checked-in JSONL is synthetic | Not an Oracle extract |
| `fill_sim_batch_counts_are_not_returns` | Rollup and fill-sim status shares are stamp counts | Not EV or lift |

---

## CLI

```bash
python -m tools.paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0 example --which two-day
python -m tools.paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0 validate \
  fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json
python -m tools.paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0 batch \
  --jsonl fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-20.jsonl \
  --jsonl fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-21.jsonl \
  --expectation fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json \
  --expectation fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json
```

`batch` and `validate` refuse host-root paths lexically (including a leading `//` before normalization). Exit `0` when a batch prints; exit `1` on invalid input. No measure exit.

Proof:

```bash
python3 -m unittest tools.test_paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0
```

---

## Non-goals

| Cap | Held |
| --- | --- |
| Rewrite #49–#58 parent CLIs or EXP-006 harness | Out |
| Host paths, SSH, Oracle re-run, marks join as scored measure | Out |
| `PASS` / `FAIL_NO_LIFT` exit | Out |
| Soft GATE PASS at merge | **PASS** (Formal stamp Lyra; kill `bc-0e1391cf-be21-5491-a9e9-026f19c486e0`) — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 |

---

## Cross-links

- Parent batch (#52): [PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md)
- Fill-sim bind: [PAPER-FILL-SIM-HOT-PACKET-EVALUATE-V0.md](PAPER-FILL-SIM-HOT-PACKET-EVALUATE-V0.md)
- Fill-sim scoreboard: [PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-FILL-SIM-SCOREBOARD-SEALED-FIXTURE-V0.md)
- [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- [SUMMARY.md](SUMMARY.md)
