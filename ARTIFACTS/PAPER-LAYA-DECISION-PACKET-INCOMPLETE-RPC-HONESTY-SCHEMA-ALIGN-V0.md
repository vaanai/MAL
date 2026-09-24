# Paper LAYA decision-packet incomplete-RPC honesty — schema align v0

| | |
| --- | --- |
| **ID** | `paper-laya-decision-packet-incomplete-rpc-honesty-schema-align-v0` |
| **Status** | **Proposed** registration (2026-09-24). **Soft GATE pending.** Not a measure. Not an EXP. Not a sealed-book close. |
| **Owner seat** | Scout commission. Helm AUTH 2026-09-23. |
| **Parent** | LAYA decision-packet chain PRs [#67](https://github.com/vaanai/MAL/pull/67)–[#71](https://github.com/vaanai/MAL/pull/71), main tip `dc691db` after Formal #71 |
| **Mirror** | [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md) (#54), [PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md) (#61), [PAPER-LAYA-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-LAYA-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md) (#66) — same honesty caps on the #67–#71 citeable schemas; **no** rewrite of parent #49–#71 CLIs |
| **Runtime object** | **None.** This stamp does not add a receipt schema or a CLI. Cite the tightened decision-packet parent schemas. |
| **Soft GATE** | **Pending.** Watches that stay true stay **non-blocking**. |

JSON Schema on the LAYA decision-packet stack (#67–#71) is the citeable shape. Parent registrations #67–#69 already `const`-lock closed book, `measure.kind=none`, null horizons / `Δ_exec`, LAYA caps (`authorize_run` / `risk_gate_unlock` / `live_trading` false), `risk_gate.decision=locked`, digest-only citations, `repoFixturePathHonesty` on fixture paths, and ordered `soft_watches.items` via `prefixItems` from Soft GATE on those stamps. On #70 and #71, `soft_watches.items` was still an unordered enum bag with correct cardinality — dishonest permutations and duplicate-id lists passed schema while the CLIs already refused them. This registration closes that gap and documents the align pass.

**Soft GATE pending.** Soft watch `schema_looser_than_cli` is **CLOSED** for watched decision-packet shapes once this stamp merges after Soft GATE. Soft GATE PASS (later) ≠ Discovery promote / continuous observe-wiring / production enum lock / densify / EXP-002c retune / Graph revive / X on host / live trading / EXP-006 promote / LAYA authorize-run / **risk-gate unlock**. No new runtime object. No CLI rewrite. `risk_gate.unlock=false`; Graph **cold**; paper-only.

`measure.kind` stays `none` on these stamps. `dual_read.sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays `false`. Graph stays cold. `global_95bps` and `launchlab_init` stay Proposed.

---

## What aligned

| Shape the CLI already refused | Schema now refuses it |
| --- | --- |
| `closed_book_claim=true` or `sealed_book_rpc_slice` closed / complete | `const: false` / `const: incomplete` on decision packet, scoreboard, batch, dry-run, and capture (already on #67–#71; tests cite them). |
| `measure.kind` other than `none`, invented EV / lift / alpha, `pass_fail_no_lift=true` | `const` on `measure` and `honesty` blocks (already on #67–#71). |
| Host-path open claims: `rpc=true`, `host_jsonl_read=true`, `marks_joined=true`, `var_lib_mal_read=true`, `host_extract_checked_into_git=true` | `const: false` on `input` and `honesty` where applicable (already on #67–#71). CLI still refuses `/var/lib/mal` paths lexically before FS touch. |
| Numeric horizon or `Δ_exec` while status is null-explicit | Horizon map values and `delta_exec.value` stay `const: null` with explicit status (already on #67–#68). |
| `laya.authorize_run` / `laya.risk_gate_unlock` / `laya.live_trading` true | `const: false` on `laya` caps (already on #67–#71). |
| `risk_gate.unlock=true` or `risk_gate.decision` other than `locked` | `const` on `risk_gate` (already on #67–#71). |
| Fixture paths with `..`, in-path `/var/lib/mal`, collapse segments (`//`, `./`, trailing `/`, backslashes) inside allowed `fixtures/...` prefixes | `repoFixturePathHonesty` on #67–#71 paths (already on #67–#71 Soft GATE; tests cite). |
| Return / EV keys in object trees | Root and nested `additionalProperties: false` plus CLI `FORBIDDEN_KEYS` walk (already refused by both; tests cite). |
| Capture refuse shapes: `subject.kind` / `subject.reason` / `forms` / `probes` mismatch | `if` / `then` coupling on #71 capture schema (already on #71 Soft GATE; tests cite). |
| Unregistered, reordered, partial, or wrong-slot duplicate `soft_watches.items` on #70 dry-run and #71 capture | **New on this stamp:** ordered `prefixItems` with one `const` per slot (`uniqueItems` on #70; #71 keeps deliberate duplicate ids at fixed slots from the CLI merge). Enum-only bags no longer admit permutations or `[items[0]] * n` duplicates on #70. |
| #70 non-operator `invocation.jsonl` / `manifest` / `expectation` paths that pass `repoFixturePathHonesty` but are not the checked-in courier fixtures (wrong basename, wrong fixture dir, case mismatch, leading/trailing whitespace) | **Soft GATE FAIL #1 fix:** `prefixItems` `const` arrays (`syntheticCheckedInInvocation*`) on synthetic replay and projection receipts; operator-declared slots stay separate host-path `const`s. |
| #70 `invocation.executed_by_this_process` flipped away from the CLI rebuild (`true` when parent invoked, `false` on operator-declared) | **Soft GATE FAIL #1 fix:** receipt-kind `if` / `then` locks `executed_by_this_process` to the rebuilt value. |

Parent decision-packet CLIs are unchanged. They still rebuild the object and still exit `1` on these shapes.

---

## Schemas touched

| Schema | Diff |
| --- | --- |
| [paper-laya-precompute-decision-packet-v0.schema.json](paper-laya-precompute-decision-packet-v0.schema.json) | Already `prefixItems` on `soft_watches.items` (39), `repoFixturePathHonesty`, citation spine `if` / `then`. No structural edit. Tests cite it. |
| [paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json](paper-laya-decision-packet-scoreboard-sealed-fixture-v0.schema.json) | Already `prefixItems` on `soft_watches.items` (42) and honesty caps. No structural edit. Tests cite it. |
| [paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json](paper-laya-decision-packet-batch-oracle-sealed-day-incomplete-rpc-v0.schema.json) | Already `prefixItems` on `soft_watches.items` (46) and manifest/day bindings from Soft GATE FAIL #1–#5. No structural edit. Tests cite it. |
| [paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json](paper-laya-decision-packet-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0.schema.json) | **`soft_watches.items`:** enum bag → ordered `prefixItems` (47) + `uniqueItems` + `items: false`. **FAIL #1 fix:** non-operator invocation paths → checked-in `prefixItems` `const`s; `executed_by_this_process` locked per `receipt_kind`. Other honesty bits were already `const` on #70. |
| [paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.schema.json](paper-laya-decision-packet-host-local-dry-run-receipt-capture-v0.schema.json) | **`soft_watches.items`:** enum bag → ordered `prefixItems` (50) + `items: false` (no `uniqueItems` — CLI merge repeats three capture watches at fixed slots). Refuse/receipt `if` / `then` coupling was already on #71. |

There is no `paper-laya-decision-packet-incomplete-rpc-honesty-schema-align-v0.schema.json`. Operators do not need a new runtime object. The citeable align-pass object is this file.

---

## Soft watches

**Closed by this stamp (after Soft GATE merge)**

| Watch id | Status |
| --- | --- |
| `schema_looser_than_cli` | **CLOSED** for the dishonest shapes in [What aligned](#what-aligned), including unordered or duplicate `soft_watches.items` on #70/#71 while cardinality matched. Schema validation refuses them. The CLIs still refuse them. Do not widen a CLI down to an older schema. |

Parent stamps #67–#71 still emit `schema_looser_than_cli` inside `soft_watches.items`. That list is the historical registration of those stamps. It is not a claim that the honesty shapes above still pass schema only after this align pass closes the last gaps.

**Still true, non-blocking** (`blocking=false` on the parent stamps). **Soft GATE pending.** This stamp does not close them.

| Watch id | What stays true |
| --- | --- |
| `sealed_book_rpc_slice_incomplete` | The slice stays `incomplete`. |
| `surround_counts_are_not_returns` / decision-packet count watches | Shares are stamp counts. |
| `decision_packet_not_risk_gate_unlock` / batch / capture lock caps | Merge ≠ risk-gate unlock or LAYA authorize-run. |
| `synthetic_scoreboard_not_a_host_extract` / batch / dry-run / capture not host extracts | Fixtures are not host extracts. |
| `graph_stays_cold_on_this_stamp` | Graph lift stays null. |
| `host_jsonl_read_is_not_a_closed_book` | `host_jsonl_read=true` does not set `closed_book_claim`. |
| `operator_path_string_is_not_a_host_extract` | Path strings under `/var/lib/mal` are not file bytes. |
| `dry_run_does_not_ssh_or_open_var_lib_mal` | Dry-run CLIs do not SSH and do not open `/var/lib/mal`. |
| `parent_stamp_not_rewritten` | Parent CLIs are not rewritten by this align pass. |

CLI recompute of a decision packet, scoreboard, batch, dry-run receipt, or capture is still how `validate` works. A schema miss on that recompute is not permission to cite a closed book, a measure, LAYA authorize-run, risk-gate unlock, or live trading.

---

## Proof

```bash
python3 -m unittest tools.test_paper_laya_decision_packet_incomplete_rpc_honesty_schema_align_v0
python3 -m unittest tools.test_paper_laya_precompute_decision_packet_v0 tools.test_paper_laya_decision_packet_scoreboard_sealed_fixture_v0 tools.test_paper_laya_decision_packet_batch_oracle_sealed_day_incomplete_rpc_v0 tools.test_paper_laya_decision_packet_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 tools.test_paper_laya_decision_packet_host_local_dry_run_receipt_capture_v0
```

The align test loads the decision-packet schemas with JSON Schema 2020-12 and a registry of all citeable paper schemas. Honest fixtures from #67–#71 pass schema and CLI. In-memory dishonest copies fail schema and CLI. Those copies are not checked in. Nothing under `/var/lib/mal` is added to git.

`observe/client.py` is untouched.

---

## Non-goals

| Cap | Held |
| --- | --- |
| Live capital, trading keys, PumpPortal trade API | Out |
| `observe/client.py` / encoder promote | Untouched |
| Continuous observe-wiring | Out |
| Production lock of `global_95bps` or `launchlab_init` | Out. Both stay **Proposed** |
| Discovery promote, Graph revive, X ingest, mark densify | Out |
| EXP-002c retune | Out |
| EXP-006 promote or harness rewrite | Out |
| LAYA authorize-run / risk-gate unlock / live trading | Out |
| Scored Oracle measure, invented EV / lift / alpha | Out. `measure.kind=none` |
| Closing `sealed_book_rpc_slice` | Out. Stays `incomplete`. `closed_book_claim=false` |
| Rewriting parent #49–#71 CLIs | Out |
| New runtime align-pass object | Out |
| Soft GATE PASS at merge | **Pending** — ≠ Discovery / wiring / enum / densify / EXP-002c / Graph / X / live / EXP-006 / LAYA authorize-run / **risk-gate unlock** |

---

## Cross-links

- Decision packet (#67): [PAPER-LAYA-PRECOMPUTE-DECISION-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-DECISION-PACKET-V0.md), [PR #67](https://github.com/vaanai/MAL/pull/67) squash `52a06e7`
- Decision-packet scoreboard (#68): [PAPER-LAYA-DECISION-PACKET-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-LAYA-DECISION-PACKET-SCOREBOARD-SEALED-FIXTURE-V0.md), [PR #68](https://github.com/vaanai/MAL/pull/68) squash `d31af86`
- Decision-packet batch (#69): [PAPER-LAYA-DECISION-PACKET-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md](PAPER-LAYA-DECISION-PACKET-BATCH-ORACLE-SEALED-DAY-INCOMPLETE-RPC-V0.md), [PR #69](https://github.com/vaanai/MAL/pull/69) squash `adadaa8`
- Decision-packet batch dry-run (#70): [PAPER-LAYA-DECISION-PACKET-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md](PAPER-LAYA-DECISION-PACKET-BATCH-HOST-LOCAL-SEALED-JSONL-DRY-RUN-INCOMPLETE-RPC-V0.md), [PR #70](https://github.com/vaanai/MAL/pull/70) squash `d020fc9`
- Decision-packet capture (#71): [PAPER-LAYA-DECISION-PACKET-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md](PAPER-LAYA-DECISION-PACKET-HOST-LOCAL-DRY-RUN-RECEIPT-CAPTURE-V0.md), [PR #71](https://github.com/vaanai/MAL/pull/71) squash `3d83622`
- LAYA surround honesty (#66): [PAPER-LAYA-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-LAYA-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md)
- Proof test: [tools/test_paper_laya_decision_packet_incomplete_rpc_honesty_schema_align_v0.py](../tools/test_paper_laya_decision_packet_incomplete_rpc_honesty_schema_align_v0.py)
- Manager digest: [SUMMARY.md](SUMMARY.md)
