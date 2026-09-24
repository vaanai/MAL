# LAYA precompute decision packet — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-laya-precompute-decision-packet-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). **Soft GATE pending** Formal. Not a measure. Not LAYA authorize-run. Not risk-gate unlock. Not live. |
| **Owner seat** | Proof (decision packet + Soft GATE); Scout (surround + lock citation paths); Helm (AUTH) |
| **Commission** | North-star step after merged LAYA lock receipt [#65](https://github.com/vaanai/MAL/pull/65) (`7979988`): package validated [#64](https://github.com/vaanai/MAL/pull/64) and/or [#63](https://github.com/vaanai/MAL/pull/63) surround fixtures with [#65](https://github.com/vaanai/MAL/pull/65) lock-receipt fixtures for later LAYA consumption. Parent #49–#65 CLIs and EXP-006 harness are **not** rewritten. |
| **Schema** | [paper-laya-precompute-decision-packet-v0.schema.json](paper-laya-precompute-decision-packet-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_laya_precompute_decision_packet_v0/](../fixtures/paper_laya_precompute_decision_packet_v0/) — decision packets citing surround + lock receipt fixtures only |
| **CLI** | `python -m tools.paper_laya_precompute_decision_packet_v0` — example / validate / assemble on local JSON only |
| **Soft GATE** | **Pending** Formal — Soft GATE FAIL #1 `schema_looser_than_cli` holes **closed** on [PR #67](https://github.com/vaanai/MAL/pull/67) (union rule, mixed-spine citation cardinality, digest/path binding, `soft_watches` identity). Formal stamp still pending. |

This file registers a **fixtures-only decision packet** that cites validated LAYA precompute surround JSON (#63/#64) and lock receipts (#65) already on `main`. The packet records surround digests plus lock-receipt digests while the risk gate stays **locked**: `laya.risk_gate_unlock=false`, `laya.authorize_run=false`, `laya.live_trading=false`, `risk_gate.decision=locked`, `risk_gate.unlock=false`. It does **not** unlock the gate, authorize a LAYA run, or enable live trading.

Citeable inputs on `main`: [PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md) (#64 @ `277a142`); [PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md) (#63 @ `171cd61`); [PAPER-LAYA-RISK-GATE-LOCK-RECEIPT-V0.md](PAPER-LAYA-RISK-GATE-LOCK-RECEIPT-V0.md) (#65 @ `7979988`). Mirror pattern: lock receipt (#65) plus explicit surround citations; digest only (`surround_body_embedded=false`, `lock_receipt_body_embedded=false`).

Merge ≠ risk-gate unlock ≠ LAYA authorize-run ≠ live trading ≠ Oracle measure ≠ EXP-006 promote.

**Soft GATE pending** Formal. Schema now matches CLI on assembly union, mixed-spine dual citations, fixture-anchored digests and paths, and ordered `soft_watches` identity (not only const caps). Caps held on honest fixtures: `measure.kind=none`; sealed book **incomplete**; `closed_book_claim=false`; Graph **cold**; `risk_gate.decision=locked`; `risk_gate.unlock=false`; `laya.authorize_run=false`; `laya.risk_gate_unlock=false`; `laya.live_trading=false`. Soft GATE PASS (when stamped) ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X on host ≠ live trading ≠ EXP-006 promote ≠ LAYA authorize-run ≠ **risk-gate unlock**.

---

## What the decision packet records

`packet_kind=decision`. Each object cites one or more checked-in surround JSON files under `fixtures/paper_laya_precompute_surround_packet_v0/` and/or `fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/`, plus one or more lock receipts under `fixtures/paper_laya_risk_gate_lock_receipt_v0/`. Parent surround and lock-receipt CLIs validate those files on assemble; this stamp does **not** rewrite them.

| Field | v0 fact |
| --- | --- |
| `surround_citations[].registration` | Parent surround id, schema, PR, commit (#64 or #63); `rewritten_by_this_stamp=false` |
| `surround_citations[].fixture_path` | Repo-relative path to the cited surround fixture |
| `surround_citations[].surround_body_embedded` | `false` — digest only |
| `surround_citations[].digest` | Stamp **counts** copied from the surround aggregate (`full_book`, `label_rates`; optional `fill_sim_status_counts` on fill-sim spine). Not returns |
| `lock_receipt_citations[].registration` | Parent lock receipt id, schema, PR #65, commit `7979988`; `rewritten_by_this_stamp=false` |
| `lock_receipt_citations[].fixture_path` | Repo-relative path to the cited lock receipt fixture |
| `lock_receipt_citations[].lock_receipt_body_embedded` | `false` — digest only |
| `lock_receipt_citations[].digest` | Lock fields (`receipt_kind`, `risk_gate`, cited `surround_fixture_paths`, citation count). Not full receipt body |
| `risk_gate.decision` | `locked` |
| `risk_gate.unlock` | `false` |
| `risk_gate.reason` | `paper_registration_fixture_decision_packet_not_risk_gate_unlock` |
| `carries.surround_body` / `carries.lock_receipt_body` | `false` |
| `carries.host_bytes` | `false` |

Assembly rule: the set of `input.assembly.surround_paths` must equal the union of `surround_paths` on all cited lock receipts.

Counts and shares on digests are stamp counts, not EV or lift.

---

## Horizons and `Δ_exec`

Null with explicit status on the packet object. Null is not 0% and not zero cost.

| Object | Status | Value |
| --- | --- | --- |
| `horizons` | `decision_packet_null_explicit` | `values` all `null` |
| `delta_exec` | `decision_packet_null_explicit` | `value=null`, `reason=laya_precompute_decision_packet_has_no_scored_fill` |

---

## Sealed book stays incomplete

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `laya_precompute_decision_packet_does_not_close_the_sealed_book` |
| `measure.kind` | `none` |
| `graph_policy` | `cold` |
| `graph_lift` | `null` |

The schema const-refuses dishonest unlock, authorize-run, live trading, closed book, and `measure.kind` other than `none`.

---

## CLI

From the repo root:

```bash
python -m tools.paper_laya_precompute_decision_packet_v0 example --which non-fill-surround
python -m tools.paper_laya_precompute_decision_packet_v0 example --which fill-sim-surround
python -m tools.paper_laya_precompute_decision_packet_v0 example --which mixed-spines
python -m tools.paper_laya_precompute_decision_packet_v0 validate \
  fixtures/paper_laya_precompute_decision_packet_v0/decision_non_fill_sim_surround.json
python -m tools.paper_laya_precompute_decision_packet_v0 assemble \
  --surround fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json \
  --lock-receipt fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_non_fill_sim_surround.json
```

`validate` refuses `/var/lib/mal`, `//var/lib/mal`, `///var/lib/mal`, relative `var/lib/mal/...`, `./var/lib/mal/...`, and lexical `..` forms under that root **before** `read_text` / `open` / `stat` / `lstat` / `resolve`.

Exit `0` when a packet prints or validates. Exit `1` on invalid input or a refused path. No `PASS` / `FAIL_NO_LIFT` exit.

Proof:

```bash
python3 -m unittest tools.test_paper_laya_precompute_decision_packet_v0
```

| Fixture | What it shows |
| --- | --- |
| `decision_non_fill_sim_surround.json` | Decision packet citing #64 surround + matching #65 lock receipt |
| `decision_fill_sim_surround.json` | Decision packet citing #63 fill-sim surround + matching #65 lock receipt |

Honest fixtures pass schema and CLI. In-memory dishonest copies fail both. `observe/client.py` is untouched.

---

## Soft watches (non-blocking)

**Soft GATE pending.** `soft_watches.blocking=false`. Inherited lock-receipt / surround-chain watches plus decision-packet watches. They do not fail merge once Soft GATE passes.

| Watch id | Why it does not block |
| --- | --- |
| `decision_packet_not_risk_gate_unlock` | `risk_gate.unlock=false`; merge ≠ unlock |
| `decision_packet_not_laya_authorize_run` | `laya.authorize_run=false` | Merge ≠ LAYA run |
| `decision_packet_not_live` | `laya.live_trading=false` | Paper-only registration |
| `decision_packet_cites_lock_receipt_not_unlock` | Lock receipt digest cites `receipt_kind=lock` | Not an unlock stamp |
| `decision_counts_are_stamp_counts_not_returns` | Digests are stamp tallies | Not EV or lift |
| (inherited surround / lock / paper chain ids) | Listed for traceability | Non-blocking on parent registrations |

`global_95bps` and `launchlab_init` stay **Proposed**.

---

## Non-goals

| Cap | Held |
| --- | --- |
| Rewrite #49–#65 parent CLIs or EXP-006 harness | Out |
| `observe/client.py` | Untouched |
| Risk-gate unlock / LAYA authorize-run / live trading | Out — stay false / locked |
| Host extract in git, SSH, RPC, CI read of `/var/lib/mal` | Out |
| Scored Oracle measure, invented EV / lift / alpha | Out. `measure.kind=none` |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete` |
| Embedding full surround or lock receipt bodies | Out. Digest only |
| Soft GATE PASS at merge | **Pending** — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 / LAYA authorize-run / **risk-gate unlock** |

---

## Cross-links

- Non-fill-sim surround (#64): [PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md)
- Fill-sim surround (#63): [PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md)
- Lock receipt (#65): [PAPER-LAYA-RISK-GATE-LOCK-RECEIPT-V0.md](PAPER-LAYA-RISK-GATE-LOCK-RECEIPT-V0.md)
- Schema align (#66): [PAPER-LAYA-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-LAYA-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md)
- Lab memory: [LAB_STATE.md](../LAB_STATE.md) (§11r); EDL: [ENGINEERING-DECISION-LOG.md](ENGINEERING-DECISION-LOG.md) (EDL-017)
- LAYA stack brief: [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md)
