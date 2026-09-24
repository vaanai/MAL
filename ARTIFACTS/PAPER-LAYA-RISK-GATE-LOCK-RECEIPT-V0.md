# LAYA risk-gate lock receipt — Proposed paper registration

| | |
| --- | --- |
| **ID** | `paper-laya-risk-gate-lock-receipt-v0` |
| **Status** | **Proposed** paper registration (2026-09-24). **Soft GATE PASS Formal-stamped** (Lyra; kill `bc-aafaf4ca-d06f-5b11-b5cc-ca3f2ababd80`; head `6d238a141bc0d143815d2ac8eacc07dabe07aaec`; [Soft GATE comment](https://github.com/vaanai/MAL/pull/65#issuecomment-5809577563); [PR #65](https://github.com/vaanai/MAL/pull/65)). Not a measure. Not LAYA authorize-run. Not risk-gate unlock. Not live. |
| **Owner seat** | Proof (lock receipt + Soft GATE); Scout (surround citation paths); Helm (AUTH) |
| **Commission** | North-star step after merged LAYA precompute surround packets: [#64](https://github.com/vaanai/MAL/pull/64) non-fill-sim (`277a142`) and/or [#63](https://github.com/vaanai/MAL/pull/63) fill-sim (`171cd61`). Parent #49–#64 CLIs and EXP-006 harness are **not** rewritten. |
| **Schema** | [paper-laya-risk-gate-lock-receipt-v0.schema.json](paper-laya-risk-gate-lock-receipt-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_laya_risk_gate_lock_receipt_v0/](../fixtures/paper_laya_risk_gate_lock_receipt_v0/) — lock receipts citing surround fixtures only |
| **CLI** | `python -m tools.paper_laya_risk_gate_lock_receipt_v0` — example / validate / assemble on local JSON only |
| **Soft GATE** | **PASS** — Formal stamp Lyra (kill `bc-aafaf4ca-d06f-5b11-b5cc-ca3f2ababd80`; head `6d238a141bc0d143815d2ac8eacc07dabe07aaec`). Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking** (`blocking=false`). |

This file registers a **fixtures-only lock receipt** that cites validated LAYA precompute surround packets already on `main`. The receipt records that the risk gate stays **locked**: `laya.risk_gate_unlock=false`, `laya.authorize_run=false`, `laya.live_trading=false`, `risk_gate.decision=locked`, `risk_gate.unlock=false`. It does **not** unlock the gate, authorize a LAYA run, or enable live trading.

Citeable surround inputs on `main`: [PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md) (#64 @ `277a142`); [PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md) (#63 @ `171cd61`). Mirror pattern: [#55](PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md) / [#62](PAPER-FILL-SIM-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md) receipt capture (cite + digest; path strings ≠ host bytes).

Merge ≠ risk-gate unlock ≠ LAYA authorize-run ≠ live trading ≠ Oracle measure ≠ EXP-006 promote.

**Soft GATE PASS** (Formal stamp Lyra; 2026-09-24; head `6d238a141bc0d143815d2ac8eacc07dabe07aaec`; [Soft GATE comment](https://github.com/vaanai/MAL/pull/65#issuecomment-5809577563); `python3 -m unittest tools.test_paper_laya_risk_gate_lock_receipt_v0` — 22 OK per Soft GATE). Caps held: `measure.kind=none`; sealed book **incomplete**; `closed_book_claim=false`; Graph **cold**; `risk_gate.decision=locked`; `risk_gate.unlock=false`; `laya.authorize_run=false`; `laya.risk_gate_unlock=false`; `laya.live_trading=false`; surround digest only (`surround_body_embedded=false`). Soft GATE PASS ≠ Discovery promote ≠ continuous observe-wiring ≠ production enum lock ≠ densify ≠ EXP-002c retune ≠ Graph revive ≠ X on host ≠ live trading ≠ EXP-006 promote ≠ LAYA authorize-run ≠ **risk-gate unlock**.

---

## What the lock receipt records

`receipt_kind=lock`. Each object cites one or more checked-in surround JSON files under `fixtures/paper_laya_precompute_surround_packet_v0/` and/or `fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/`. The parent surround CLIs validate those files on assemble; this stamp does **not** rewrite them.

| Field | v0 fact |
| --- | --- |
| `surround_citations[].registration` | Parent surround id, schema, PR, commit (#64 or #63); `rewritten_by_this_stamp=false` |
| `surround_citations[].fixture_path` | Repo-relative path to the cited surround fixture |
| `surround_citations[].surround_body_embedded` | `false` — digest only |
| `surround_citations[].digest` | Stamp **counts** copied from the surround aggregate (`full_book`, `label_rates`; optional `fill_sim_status_counts` on fill-sim spine). Not returns |
| `risk_gate.decision` | `locked` |
| `risk_gate.unlock` | `false` |
| `risk_gate.reason` | `paper_registration_fixture_receipt_only_not_risk_gate_unlock` |
| `carries.surround_body` | `false` |
| `carries.host_bytes` | `false` |

Counts and shares on the digest are stamp counts, not EV or lift.

---

## Horizons and `Δ_exec`

Null with explicit status on the receipt object. Null is not 0% and not zero cost.

| Object | Status | Value |
| --- | --- | --- |
| `horizons` | `lock_receipt_null_explicit` | `values` all `null` |
| `delta_exec` | `lock_receipt_null_explicit` | `value=null`, `reason=laya_risk_gate_lock_receipt_has_no_scored_fill` |

---

## Sealed book stays incomplete

| Field | Value |
| --- | --- |
| `dual_read.sealed_book_rpc_slice` | `incomplete` |
| `dual_read.closed_book_claim` | `false` |
| `dual_read.incomplete_reason` | `laya_risk_gate_lock_receipt_does_not_close_the_sealed_book` |
| `measure.kind` | `none` |
| `graph_policy` | `cold` |
| `graph_lift` | `null` |

The schema const-refuses dishonest unlock, authorize-run, live trading, closed book, and `measure.kind` other than `none`.

---

## CLI

From the repo root:

```bash
python -m tools.paper_laya_risk_gate_lock_receipt_v0 example --which non-fill-surround
python -m tools.paper_laya_risk_gate_lock_receipt_v0 example --which fill-sim-surround
python -m tools.paper_laya_risk_gate_lock_receipt_v0 example --which mixed-spines
python -m tools.paper_laya_risk_gate_lock_receipt_v0 validate \
  fixtures/paper_laya_risk_gate_lock_receipt_v0/receipt_non_fill_sim_surround.json
python -m tools.paper_laya_risk_gate_lock_receipt_v0 assemble \
  --surround fixtures/paper_laya_precompute_surround_packet_v0/mixed_scoreboard.json
```

`validate` refuses `/var/lib/mal`, `//var/lib/mal`, `///var/lib/mal`, relative `var/lib/mal/...`, `./var/lib/mal/...`, and lexical `..` forms under that root **before** `read_text` / `open` / `stat` / `lstat` / `resolve`.

Exit `0` when a receipt prints or validates. Exit `1` on invalid input or a refused path. No `PASS` / `FAIL_NO_LIFT` exit.

Proof:

```bash
python3 -m unittest tools.test_paper_laya_risk_gate_lock_receipt_v0
```

| Fixture | What it shows |
| --- | --- |
| `receipt_non_fill_sim_surround.json` | Lock receipt citing #64 `mixed_scoreboard.json` surround fixture |
| `receipt_fill_sim_surround.json` | Lock receipt citing #63 fill-sim `mixed_scoreboard.json` surround fixture |

Honest fixtures pass schema and CLI. In-memory dishonest copies fail both. `observe/client.py` is untouched.

---

## Soft watches (non-blocking)

**Soft GATE PASS** (Formal stamp Lyra). `soft_watches.blocking=false`. Inherited surround-chain watches plus receipt watches. They do not fail merge once Soft GATE passes.

| Watch id | Why it does not block |
| --- | --- |
| `receipt_not_risk_gate_unlock` | `risk_gate.unlock=false`; merge ≠ unlock |
| `receipt_not_laya_authorize_run` | `laya.authorize_run=false` | Merge ≠ LAYA run |
| `receipt_not_live` | `laya.live_trading=false` | Paper-only registration |
| `synthetic_surround_not_host_extract` | Cited surrounds are synthetic fixtures | Not an Oracle extract |
| `lock_counts_are_stamp_counts_not_returns` | Digest counts are stamp tallies | Not EV or lift |
| (inherited surround / paper chain ids) | Listed for traceability | Non-blocking on parent registrations |

`global_95bps` and `launchlab_init` stay **Proposed**.

---

## Non-goals

| Cap | Held |
| --- | --- |
| Rewrite #49–#64 parent CLIs or EXP-006 harness | Out |
| `observe/client.py` | Untouched |
| Risk-gate unlock / LAYA authorize-run / live trading | Out — stay false / locked |
| Host extract in git, SSH, RPC, CI read of `/var/lib/mal` | Out |
| Scored Oracle measure, invented EV / lift / alpha | Out. `measure.kind=none` |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete` |
| Embedding full surround bodies | Out. Digest only |
| Soft GATE PASS at merge | **PASS** (Formal stamp Lyra; kill `bc-aafaf4ca-d06f-5b11-b5cc-ca3f2ababd80`; head `6d238a1`) — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 / LAYA authorize-run / **risk-gate unlock** |

---

## Cross-links

- Non-fill-sim surround (#64): [PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md), fixtures `fixtures/paper_laya_precompute_surround_packet_v0/`, CLI `python -m tools.paper_laya_precompute_surround_packet_v0`
- Fill-sim surround (#63): [PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md), fixtures `fixtures/paper_laya_precompute_fill_sim_surround_packet_v0/`, CLI `python -m tools.paper_laya_precompute_fill_sim_surround_packet_v0`
- Receipt capture mirrors: [PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md) (#55), [PAPER-FILL-SIM-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-FILL-SIM-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md) (#62)
- This lock receipt: [PR #65](https://github.com/vaanai/MAL/pull/65) (Soft GATE PASS Formal-stamped)
- Lab memory: [LAB_STATE.md](../LAB_STATE.md) (§11p); EDL: [ENGINEERING-DECISION-LOG.md](ENGINEERING-DECISION-LOG.md) (EDL-015)
- LAYA stack brief: [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md)
