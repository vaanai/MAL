# LAYA decision-packet batch oracle on sealed-day fixtures — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). **Soft GATE PASS Formal-stamped** (Lyra; kill `bc-5e4e8d4d-c120-59ce-915f-43ee1d7eedf1`; fix `bc-dd6817fa-6217-5dca-86b1-0449370f5d29`; head `e13fbd76a6c14c57af8d3cf1ca2ba517b0e3ee59`; [Soft GATE comment](https://github.com/vaanai/MAL/pull/69#issuecomment-5812462969); merged [PR #69](https://github.com/vaanai/MAL/pull/69) squash `adadaa8` on `main`). Not a measure. Not a host run. |
| **Owner seat** | Proof (batch + Soft GATE); Scout (decision-packet spine stays on cited packets); Helm (FORMAL auth) |
| **Commission** | Mirror of merged #52 / #59 batch shape, but stamped inputs are validated [`paper_laya_precompute_decision_packet_v0`](PAPER-LAYA-PRECOMPUTE-DECISION-PACKET-V0.md) (#67 squash `52a06e7`) counted by [`paper_laya_decision_packet_scoreboard_sealed_fixture_v0`](PAPER-LAYA-DECISION-PACKET-SCOREBOARD-SEALED-FIXTURE-V0.md) (#68 squash `d31af86`). Decision-packet cite chain on main: #63/#64 surround + #65 lock through #67. Parent #49–#68 CLIs and EXP-006 harness are **not** rewritten. |
| **Schema** | [paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/](../fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/) — synthetic calendar labels `2026-09-20` / `2026-09-21`; not host extracts |
| **CLI** | `python -m tools.paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0` — `example` / `validate` / `batch` on local JSON only |
| **Soft GATE** | **PASS** — Formal stamp Lyra (kill `bc-5e4e8d4d-c120-59ce-915f-43ee1d7eedf1`; fix `bc-dd6817fa-6217-5dca-86b1-0449370f5d29`; head `e13fbd76a6c14c57af8d3cf1ca2ba517b0e3ee59`; [Soft GATE comment](https://github.com/vaanai/MAL/pull/69#issuecomment-5812462969); squash `adadaa8` on `main`). FAIL #1–#5 **closed**; `schema_looser_than_cli` **CLOSED** for watched shapes. Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers a **fixtures-only** day-aligned batch: `decision-day-YYYY-MM-DD.json` manifest → cite/assemble validated #67 decision packets (themselves citing #63/#64 surround + #65 lock) → count with the #68 scoreboard path → one scoreboard per day + rollup.

Merge ≠ Oracle measure ≠ LAYA authorize-run ≠ risk-gate unlock ≠ live trading ≠ Discovery promote.

**Soft GATE PASS** (Formal stamp Lyra). Soft GATE PASS ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X keys on host ≠ live trading ≠ EXP-006 promote ≠ LAYA authorize-run ≠ **risk-gate unlock**.

Cloud agents cannot read host Oracle JSONL. Checked-in day strings are calendar labels used elsewhere; they are not a read of host `observe-*.jsonl`.

---

## What this batch is

| Stage | This registration |
| --- | --- |
| Detect / cite | Local `decision-day-YYYY-MM-DD.json` manifest listing validated #67 decision-packet fixture paths (or #67 `assemble` specs) |
| Score | `tools.paper_laya_decision_packet_scoreboard_sealed_fixture_v0.score_packets`, one scoreboard per day |
| Rollup | Digest stamp counts across days (not returns / EV / lift) |

| Piece | v0 fact |
| --- | --- |
| Read path | Local files only. `rpc=false`. `observe_jsonl_tail=false`. `host_extract_required=false`. `host_jsonl_read=false`. `marks_joined=false`. No `/var/lib/mal` |
| RPC slice | `dual_read.sealed_book_rpc_slice=incomplete` on batch, scoreboards, and cited packets |
| Closed book | `dual_read.closed_book_claim=false` everywhere. `fixture_join.closed_book=false` |
| Population | `local_set_is_not_the_sealed_book=true`. Rollup `n` is a **digest stamp** count |

Checked-in synthetic days:

| Day | Packets cited | Digest stamps (runner / reject) |
| --- | --- | --- |
| `2026-09-20` | non-fill, fill-sim, mixed (#67 fixtures) | 15 (12 / 3) |
| `2026-09-21` | non-fill, fill-sim only | 10 (8 / 2) |

Rollup `packet_n=5`, digest `n=30`, runner `24`, reject `6`. `rollup.share_kind=count_fraction_not_a_return`. Counts are not returns, EV, or lift.

---

## DEC-007 both arms

`full_book.policy=dec007_both_arms_retained`, `reject_stamps_dropped=0`. Each scoreboard keeps `label_rates.rows` with `runner` then `reject` on aggregated digest counts. `spine_profile_counts` keeps non-fill-sim, fill-sim, and mixed rows (zeros stay on the table). `fill_sim_status_counts` lists both fill-sim digest arms when cited.

Scoreboard + cited packets keep `risk_gate.decision=locked`, `risk_gate.unlock=false`, `laya.authorize_run=false`, `laya.risk_gate_unlock=false`, `laya.live_trading=false`.

---

## Horizons and `Δ_exec`

Null with explicit status on embedded decision packets and scoreboards. `null` ≠ 0 return / 0 cost.

`measure.kind=none`. `measure.pass_fail_no_lift=false`. No `PASS` / `FAIL_NO_LIFT` process exit.

---

## Graph stays cold

`graph_policy=cold`, `graph_lift=null` on batch, scoreboards, and cited packets.

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge | Docs + schema + synthetic fixtures + fixture CLI only |
| `observe/client.py` | **Untouched** |
| EXP-006 | **Not** promoted. Harness **not** rewritten |
| Host | CLI refuses `/var/lib/mal`, `//var/lib/mal`, `///var/lib/mal`, relative/`./` forms, lexical `..` under that root, and collapse forms before any filesystem touch |
| Oracle measure | **No** |

**Soft GATE PASS** (Formal stamp Lyra). Soft GATE PASS ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X keys on host ≠ live trading ≠ EXP-006 promote ≠ LAYA authorize-run ≠ **risk-gate unlock**.

---

## Soft watches (non-blocking)

**Soft GATE PASS** (Formal stamp Lyra). `soft_watches.blocking=false`. Inherited watches from #67/#68 stay listed and non-blocking.

Draft 2020-12 binds batch aggregates to cited decision-packet fixture sets (same rescore path as `validate_batch`): per-day scoreboards, `rollup` / `full_book` digest stamp counts, `knownRollupDaysList` enum on `rollup.days`, and checked-in expectation rows. FAIL #1–#5 holes **closed**; `schema_looser_than_cli` **CLOSED** for watched shapes.

Named on this registration:

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `decision_packet_batch_not_a_host_extract` | Checked-in manifests cite git fixtures only | Not an Oracle extract |
| `decision_packet_batch_counts_are_not_returns` | Rollup and digest shares are stamp counts | Not EV or lift |
| `decision_packet_batch_not_risk_gate_unlock` | Batch does not set unlock | Receipt-only registration |
| `decision_packet_batch_not_laya_authorize_run` | LAYA caps stay false on batch and boards | Not authorize-run |

**Soft GATE PASS** (Formal stamp Lyra; 2026-09-24; head `e13fbd76a6c14c57af8d3cf1ca2ba517b0e3ee59`; kill `bc-5e4e8d4d-c120-59ce-915f-43ee1d7eedf1`; fix `bc-dd6817fa-6217-5dca-86b1-0449370f5d29`; [Soft GATE comment](https://github.com/vaanai/MAL/pull/69#issuecomment-5812462969); `python3 -m unittest tools.test_paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0` — **38 OK**; parent #68 test **29 OK**; parent #67 test **29 OK**; parent #63–#66 tests **15 / 19 / 22 / 30 OK** per Soft GATE). Caps held on honest fixtures: `measure.kind=none`; sealed book **incomplete**; `closed_book_claim=false`; Graph **cold**; `risk_gate.decision=locked`; `risk_gate.unlock=false`; `laya.authorize_run=false`; `laya.risk_gate_unlock=false`; `laya.live_trading=false`. Soft GATE PASS ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X on host ≠ live trading ≠ EXP-006 promote ≠ LAYA authorize-run ≠ **risk-gate unlock**. Paper-only. No new runtime object. Parent #49–#68 CLIs and EXP-006 harness not rewritten. `observe/client.py` untouched.

---

## CLI

```bash
python -m tools.paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0 example --which two-day
python -m tools.paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0 validate \
  fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/two_day.json
python -m tools.paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0 batch \
  --manifest fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/decision-day-2026-09-20.json \
  --manifest fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/decision-day-2026-09-21.json \
  --expectation fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json \
  --expectation fixtures/paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json
```

`batch` and `validate` refuse host-root paths lexically before `read_text` / `open` / `stat` / `resolve`. Exit `0` when a batch prints; exit `1` on invalid input. No measure exit.

Proof:

```bash
python3 -m unittest tools.test_paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0
```

---

## Parent chain

Citeable on `main` through #68: [#67 decision packet](PAPER-LAYA-PRECOMPUTE-DECISION-PACKET-V0.md) → [#68 scoreboard](PAPER-LAYA-DECISION-PACKET-SCOREBOARD-SEALED-FIXTURE-V0.md). This batch does not rewrite parent CLIs. `observe/client.py` is untouched.

---

## Non-goals

| Cap | Held |
| --- | --- |
| Rewrite #49–#68 parent CLIs or EXP-006 harness | Out |
| Host paths, SSH, Oracle re-run, marks join as scored measure | Out |
| `PASS` / `FAIL_NO_LIFT` exit | Out |
| Soft GATE PASS at merge | **PASS** (Formal stamp Lyra; kill `bc-5e4e8d4d-c120-59ce-915f-43ee1d7eedf1`) — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 / LAYA authorize-run / **risk-gate unlock** |

---

## Cross-links

- Parent batch (#52): [PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md)
- Fill-sim batch (#59): [PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-FILL-SIM-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md)
- [#67 decision packet](PAPER-LAYA-PRECOMPUTE-DECISION-PACKET-V0.md), [#68 scoreboard](PAPER-LAYA-DECISION-PACKET-SCOREBOARD-SEALED-FIXTURE-V0.md)
- [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- [SUMMARY.md](SUMMARY.md)
