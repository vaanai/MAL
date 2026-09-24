# Paper fill-sim on hot-packet evaluate — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-fill-sim-hot-packet-evaluate-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). Not a measure. Not an EXP run. Not an EXP-006 Oracle replay. |
| **Owner seat** | Proof (fill-sim bind + Soft GATE); Scout (hot-packet / evaluate spine); Helm (AUTH) |
| **Commission** | Helm AUTH. Soft GATE required before merge. Consumer of merged paper-evaluate ([PR #50](https://github.com/vaanai/MAL/pull/50) @ `224166c`) and the #49–#55 citeable chain on `main` (tip includes #56 Lab-memory night close @ `3529b47`). |
| **Schema** | [paper-fill-sim-hot-packet-evaluate-v0.schema.json](paper-fill-sim-hot-packet-evaluate-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_fill_sim_hot_packet_evaluate_v0/](../fixtures/paper_fill_sim_hot_packet_evaluate_v0/) — synthetic only, not host extracts |
| **CLI** | `python -m tools.paper_fill_sim_hot_packet_evaluate_v0` — example / validate / sim (fixtures only) |
| **Parent evaluate** | [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md). Not rewritten. |
| **Vocabulary** | [PAPER-TRADING-SURFACE-BRIEF.md](PAPER-TRADING-SURFACE-BRIEF.md), [EXP-006](../EXP/EXP-006-paper-would-have-happened-harness-v0.md) — **vocabulary only**; do **not** promote EXP-006 or re-run the Oracle measure. |
| **Soft GATE** | **Required** before merge. Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers how **EXP-006-style fill-sim honesty** (documented P0 constants and field names) binds onto a validated [`paper_evaluate_hot_packet_v0`](PAPER-EVALUATE-HOT-PACKET-V0.md) stamp — **runner and reject arms** — without joining marks, without tailing observe JSONL, and without scoring a sealed book.

Merge ≠ authorize Oracle sealed measure ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X keys on host ≠ live trading.

---

## What this stamp is

[DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md) stops at paper evaluate→runners. [PAPER-TRADING-SURFACE-BRIEF.md](PAPER-TRADING-SURFACE-BRIEF.md) names a **fill-sim layer above marks**. v0 cites that vocabulary on the **hot-packet evaluate path** only:

| Piece | v0 fact |
| --- | --- |
| Input | One object that validates as `paper_evaluate_hot_packet_v0` (embedded under `input.stamp`) |
| Output | `type=paper_fill_sim_stamp` / `schema_version=paper_fill_sim_hot_packet_evaluate_v0` / `id=paper-fill-sim-hot-packet-evaluate-v0` |
| Fill model | Documented EXP-006 P0 constants (`latency_ms`, `fee_model_id`, `total_fee_bps`, …). **Not fitted to lift.** |
| Fill outcomes on this registration | **Null.** `fill_status`, `t_fill`, `fill_price_proxy`, `fee_sol`, `slippage_bps` stay null. |
| Horizons / `Δ_exec` | Null with status `fill_sim_registration_null_explicit`. Not 0% return. Not zero cost. |
| Measure | `measure.kind=none`. No `PASS` / `FAIL_NO_LIFT` process exit. |

The CLI **does not** call [`tools/exp006_paper_fill_sim`](../tools/exp006_paper_fill_sim.py). It **does not** rewrite [`tools/paper_evaluate_hot_packet_v0`](../tools/paper_evaluate_hot_packet_v0.py) or any #49–#55 parent CLI.

---

## Evaluate arms and fill-sim status

Both arms bind. [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md) policy is unchanged.

| `evaluate_label` | `fill_sim.status` | `pretend_buy` |
| --- | --- | --- |
| `runner` | `documented_model_only` | `true` (from parent evaluate stamp) |
| `reject` | `reject_arm_no_pretend_buy` | `false` |

On **reject**, fill-sim constants are **vocabulary only** — no pretend buy, no simulated fill on this registration. The reject arm is retained; `full_book.reject_stamps_dropped=0`.

---

## Documented fill model (not a scored fill)

Constants match EXP-006 P0 documentation ([EXP-006](../EXP/EXP-006-paper-would-have-happened-harness-v0.md), [PAPER-TRADING-SURFACE-BRIEF.md](PAPER-TRADING-SURFACE-BRIEF.md)):

| Field | v0 value |
| --- | --- |
| `latency_ms` | `500` |
| `paper_size_sol` | `0.1` |
| `fee_model_id` | `pump_assumed_bps_v0` |
| `total_fee_bps` | `125.0` |
| `max_slippage_bps` | `500.0` |
| `sim_reject_on_slip` | `true` |
| `fitted_to_lift` | `false` |

Attaching these fields is **not** a claim that a fill occurred on this stamp. Numeric fill outcomes stay null until a **separate** authorized measure joins marks and JSONL.

---

## Horizons and `Δ_exec`

Same honesty class as #50–#55: null is legal when incomplete.

| Object | Status | Value |
| --- | --- | --- |
| Parent `runner_stamp.horizons` | `null_ok_no_marks_on_this_stamp` | every key `null` |
| This stamp `horizons` | `fill_sim_registration_null_explicit` | `values` all `null`; `null_is_not_zero_return=true` |
| Parent `runner_stamp.delta_exec` | `null_ok` | `value=null` |
| This stamp `delta_exec` | `fill_sim_registration_null_explicit` | `value=null`, `reason=fill_sim_stamp_has_no_scored_delta`, `null_is_not_zero_cost=true` |

This registration does **not** invent EV, lift, alpha, or a priced horizon.

---

## Sealed book stays incomplete

Embedded packets keep `dual_read.sealed_book_rpc_slice=incomplete` from hot-packet v0. This stamp repeats:

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `fill_sim_bind_does_not_close_the_sealed_book` |
| `graph_policy` | `cold` |
| `graph_lift` | `null` |

Graph lanes stay cold. No Graph revive, no densify, no ordinal / NH scoring.

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge of this registration | Docs + schema + synthetic fixtures + fixture CLI. **Does not** wire fill-sim into observe |
| Input | Validated `paper_evaluate_hot_packet_v0` only |
| EXP-006 | **Not promoted.** `exp006_vocabulary.promoted=false`, `honesty.exp006_promoted=false`. Harness **not** rewritten |
| Oracle measure | **No.** `honesty.scored_oracle_measure=false`, `measure.kind=none` |
| Marks | **Not joined** on this path (`input.marks_joined=false`) |
| Encoder | **Not** promoted. `observe/client.py` is untouched |
| Enum production lock | **No.** `global_95bps` and `launchlab_init` stay **Proposed** on embedded packets |
| Discovery promote | **No.** No Graph revive |
| EXP-002c | **Not** retuned |
| Host | No `/var/lib/mal` read, no SSH, no host bytes in git |
| Trading | Paper only. No live capital, no trading keys, no PumpPortal trade API |

---

## CLI

From the repo root:

```bash
python -m tools.paper_fill_sim_hot_packet_evaluate_v0 example --which sealed-cold-runner
python -m tools.paper_fill_sim_hot_packet_evaluate_v0 example --which enriched-fee-runner
python -m tools.paper_fill_sim_hot_packet_evaluate_v0 example --which enriched-launchlab-runner
python -m tools.paper_fill_sim_hot_packet_evaluate_v0 example --which graph-slots-not-scored
python -m tools.paper_fill_sim_hot_packet_evaluate_v0 example --which reject-missing-identity
python -m tools.paper_fill_sim_hot_packet_evaluate_v0 validate \
  fixtures/paper_fill_sim_hot_packet_evaluate_v0/sealed_cold_runner.json
python -m tools.paper_fill_sim_hot_packet_evaluate_v0 sim \
  fixtures/paper_evaluate_hot_packet_v0/sealed_cold_graph_runner.json
```

`sim` accepts only paths under `fixtures/paper_evaluate_hot_packet_v0/` or `fixtures/paper_fill_sim_hot_packet_evaluate_v0/`. Paths under `/var/lib/mal` exit `1` without opening.

Exit `0` when a stamp validates or prints. Exit `1` when the shape is dishonest or input is not a valid parent evaluate stamp. There is **no** measure exit code.

Proof:

```bash
python3 -m unittest tools.test_paper_fill_sim_hot_packet_evaluate_v0
```

| Fixture | What it shows |
| --- | --- |
| `sealed_cold_runner.json` | Runner bind; graph cold; fill model constants; null fill outcomes |
| `enriched_fee_runner.json` | Proposed `global_95bps` on parent; still not production-locked |
| `enriched_launchlab_runner.json` | Synthetic LaunchLab shape; vocabulary only |
| `graph_slots_not_scored.json` | Allowlisted slot on parent; `graph_lift` null |
| `reject_missing_identity.json` | Reject arm; `reject_arm_no_pretend_buy` |

Checked-in fixtures are **synthetic**. They are not Oracle extracts and not lift evidence.

---

## Soft watches (non-blocking)

**Soft GATE is required** before merge. `soft_watches.blocking=false`. Inherited watches from #49–#55 remain listed and non-blocking. `schema_looser_than_cli` stays the historical id closed by [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md) for parent shapes; this schema const-refuses closed book, non-`none` measure, numeric horizons / `Δ_exec` under null status, and non-null fill outcomes on this registration. The CLI still rebuilds the bind.

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `fill_sim_vocabulary_not_exp006_promote` | Field names mirror EXP-006 / paper brief | Merge ≠ promote EXP-006 ≠ Oracle re-run |
| `documented_constants_not_a_scored_fill` | `fill_model` present; outcomes null | Constants are not a fill score |
| `parent_evaluate_cli_not_rewritten` | #50 CLI unchanged | Bind is a new module only |
| `exp006_harness_not_rewritten` | [`tools/exp006_paper_fill_sim`](../tools/exp006_paper_fill_sim.py) untouched | Vocabulary cite only |

---

## Non-goals

| Cap | Held |
| --- | --- |
| EXP-006 Oracle measure / promote | Out |
| Live capital, trading keys, PumpPortal trade API | Out |
| `observe/client.py` / encoder promote | Untouched |
| Continuous observe-wiring | Out |
| Production lock of `global_95bps` or `launchlab_init` | Out |
| Discovery promote, Graph revive, X ingest, mark densify | Out |
| EXP-002c retune | Out |
| Invented lift / EV / alpha | Out |
| Host extract in git, `/var/lib/mal` open from CI | Out |
| Soft watches above | Listed, **non-blocking** |

---

## Cross-links

- Parent evaluate: [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md)
- Hot-packet decode: [HOT-PACKET-V0.md](HOT-PACKET-V0.md)
- Paper chain through #55: [PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md)
- Honesty caps: [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md)
- Fill-sim spec (do not conflate with this registration): [EXP-006](../EXP/EXP-006-paper-would-have-happened-harness-v0.md)
