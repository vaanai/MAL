# Paper LAYA incomplete-RPC honesty — schema align v0

| | |
| --- | --- |
| **ID** | `paper-laya-incomplete-rpc-honesty-schema-align-v0` |
| **Status** | **Proposed** registration (2026-09-24). Not a measure. Not an EXP. Not a sealed-book close. |
| **Owner seat** | Scout commission. Helm AUTH 2026-09-23. |
| **Parent** | LAYA paper stack PRs [#63](https://github.com/vaanai/MAL/pull/63)–[#65](https://github.com/vaanai/MAL/pull/65), main tip `9d866b0` (post-#65 squash `7979988`) |
| **Mirror** | [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md) (#54), [PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md) (#61) — same honesty caps on the LAYA citeable schemas; **no** rewrite of parent #49–#65 CLIs |
| **Runtime object** | **None.** This stamp does not add a receipt schema or a CLI. Cite the tightened LAYA parent schemas. |
| **Soft GATE** | **Required** before merge. Watches that stay true stay **non-blocking**. |

JSON Schema on the LAYA paper stack (#63–#65) is the citeable shape. Before this stamp, a few dishonest shapes the CLIs already refused still passed schema-only validation (notably unregistered `soft_watches.items`, dishonest `graph_lift_aggregate_status` strings on surround digests, and single-arm `fill_sim_status_counts` on lock-receipt fill-sim digests). The #63–#65 registrations already `const`-lock closed book, `measure.kind=none`, null horizons / `Δ_exec`, LAYA caps (`authorize_run` / `risk_gate_unlock` / `live_trading` false), risk-gate lock, digest-only surround citations, and Graph cold. This registration closes the remaining watched gaps and documents the align pass.

**Soft GATE pending.** Soft watch `schema_looser_than_cli` is **CLOSED** for watched LAYA shapes after merge. Soft GATE PASS ≠ Discovery promote / continuous observe-wiring / production enum lock / densify / EXP-002c retune / Graph revive / X on host / live trading / EXP-006 promote / LAYA authorize-run / risk-gate unlock. No new runtime object. No CLI rewrite.

`measure.kind` stays `none` on these stamps. `dual_read.sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays `false`. Graph stays cold. `global_95bps` and `launchlab_init` stay Proposed.

---

## What aligned

| Shape the CLI already refused | Schema now refuses it |
| --- | --- |
| `closed_book_claim=true` or `sealed_book_rpc_slice` closed / complete | `const: false` / `const: incomplete` on surround packets, lock receipt, and nested scoreboard digests (already on #63–#65; tests cite them). |
| `measure.kind` other than `none`, invented EV / lift / alpha, `pass_fail_no_lift=true` | `const` on `measure` and `honesty` blocks (already on #63–#65). |
| Host-path open claims: `rpc=true`, `host_jsonl_read=true`, `marks_joined=true`, `var_lib_mal_read=true`, `host_extract_checked_into_git=true` | `const: false` on `input` and `honesty` (already on #63–#65). CLI still refuses `/var/lib/mal` paths lexically before FS touch. |
| Numeric horizon or `Δ_exec` while status is null-explicit on surround / lock receipt | Horizon map values and `delta_exec.value` stay `const: null` with explicit status (already on #63–#65). |
| `laya.authorize_run` / `laya.risk_gate_unlock` / `laya.live_trading` true | `const: false` on `laya` caps (already on #63–#65). |
| `risk_gate.unlock=true` or `risk_gate.decision` other than `locked` | `const` on `risk_gate` (#65; tests cite). |
| `surround_body_embedded=true` or `carries.surround_body=true` on lock receipt | `const: false` (#65; tests cite). |
| `stamp_bodies_embedded=true` on surround digests | `const: false` on digests (#63–#64). |
| Return / EV / lift keys anywhere in the surround or lock object tree | Root and nested `additionalProperties: false` plus CLI `FORBIDDEN_KEYS` walk (already refused by both; tests cite). |
| Graph revive claims: `graph_lane_revived=true`, `graph_policy` warm, scored `graph_lift`, dishonest `graph_lift_aggregate_status` | **Soft GATE FAIL #2–#3 hole closed:** `graph_lift_aggregate_status` on scoreboard digests is tied to **anchored** `citation.repo_path` patterns via `if` / `then`, with an `else` that rejects unmatched paths (including case variants). In-enum swaps on known paths fail schema. Out-of-enum `graph_scored_lift` still fails both. Other Graph caps were already `const`. |
| Lock-receipt host paths (`fixture_path`, `input.assembly.surround_paths`) under `/var/lib/mal` or lexical `var/lib/mal` / `..` forms | **Soft GATE FAIL #2–#3 hole closed:** `layaSurroundFixturePath` and surround `assembly` paths use `repoFixturePathHonesty` (`not` / `pattern` refuses `..`, in-path `/var/lib/mal`, and leading host-root shapes) under the allowed `fixtures/...` prefix (#54/#61 style). |
| Non-fill lock-receipt citation carrying `fill_sim_status_counts` | **Soft GATE FAIL #2 hole closed:** non-fill spine forbids `digest.fill_sim_status_counts` (`not: { required: [...] }`). |
| Fill-sim lock-receipt citation wearing non-fill `registration` while keeping fill-sim `fixture_path` and omitting DEC-007 counts | **Soft GATE FAIL #3 hole closed:** `if` / `then` pairs `fixture_path` prefix with the matching `registration` const block and fill-sim digest key requirements (non-fill prefix pairs non-fill registration). |
| Unregistered or substituted `soft_watches.items` on surround packets | **New on this stamp:** `items` enum matches each registration’s `SOFT_WATCH_ITEMS` (#63–#65). `minItems` / `maxItems` lock full CLI cardinality (17 / 29 / 34); partial one-id subsets fail schema. |
| Fill-sim digest omitting a DEC-007 fill-sim arm on lock receipt | **New on this stamp:** `fill_sim_status_counts` `prefixItems` with `documented_model_only` then `reject_arm_no_pretend_buy` on #65 citation digests when the key is present. |
| Fill-sim lock-receipt citation digest omitting `fill_sim_status_counts` entirely | **Soft GATE FAIL hole closed:** `if` / `then` on `registration.id == paper-laya-precompute-fill-sim-surround-packet-v0` requires `digest.fill_sim_status_counts` (non-fill citations still omit the key). |

Parent LAYA CLIs are unchanged. They still rebuild the object and still exit `1` on these shapes.

---

## Schemas touched

| Schema | Diff |
| --- | --- |
| [paper-laya-precompute-surround-packet-v0.schema.json](paper-laya-precompute-surround-packet-v0.schema.json) | `soft_watches.items` enum plus `minItems` / `maxItems` 17; `scoreboardAssemblyPath` / `batchAssemblyPath` with `repoFixturePathHonesty`; `scoreboardDigestKnownRepoPath` plus anchored graph-status `if` / `then` / fail-closed `else`. Other honesty bits were already `const` on #64. |
| [paper-laya-precompute-fill-sim-surround-packet-v0.schema.json](paper-laya-precompute-fill-sim-surround-packet-v0.schema.json) | `soft_watches.items` enum plus `minItems` / `maxItems` 29; same assembly path honesty and anchored digest graph-status tie. Top-level `fill_sim_status_counts` DEC-007 `prefixItems` were already on #63. |
| [paper-laya-risk-gate-lock-receipt-v0.schema.json](paper-laya-risk-gate-lock-receipt-v0.schema.json) | `layaSurroundFixturePath` with `repoFixturePathHonesty`; `fill_sim_status_counts` DEC-007 `prefixItems`; `if` / `then` pairs `fixture_path` prefix with registration and digest key rules; `soft_watches.items` enum plus `minItems` / `maxItems` 34. Lock caps were already on #65. |

There is no `paper-laya-incomplete-rpc-honesty-schema-align-v0.schema.json`. Operators do not need a new runtime object. The citeable align-pass object is this file.

---

## Soft watches

**Closed by this stamp**

| Watch id | Status |
| --- | --- |
| `schema_looser_than_cli` | **CLOSED** for the dishonest shapes in [What aligned](#what-aligned), including unregistered or partial `soft_watches.items`, in-enum graph aggregate status swaps on digests, lock-receipt host paths, non-fill citations carrying `fill_sim_status_counts`, omitted or single-arm `fill_sim_status_counts` on fill-sim lock-receipt citation digests. Schema validation refuses them. The CLIs still refuse them. Do not widen a CLI down to an older schema. |

Parent stamps #63–#65 still emit `schema_looser_than_cli` inside `soft_watches.items`. That list is the historical registration of those stamps. It is not a claim that the honesty shapes above still pass schema only after this align pass closes the last gaps.

**Still true, non-blocking** (`blocking=false` on the parent stamps). Soft GATE is still required. This stamp does not close them.

| Watch id | What stays true |
| --- | --- |
| `sealed_book_rpc_slice_incomplete` | The slice stays `incomplete`. |
| `surround_counts_are_not_returns` / `lock_counts_are_stamp_counts_not_returns` | Shares are stamp counts. |
| `surround_not_laya_authorize_run` / `receipt_not_laya_authorize_run` | Merge ≠ LAYA run. |
| `surround_not_risk_gate_unlock` / `receipt_not_risk_gate_unlock` | Merge ≠ risk-gate unlock. |
| `surround_not_live_trading` / `receipt_not_live` | Paper-only registration. |
| `synthetic_scoreboard_not_a_host_extract` / `synthetic_surround_not_host_extract` | Fixtures are not host extracts. |
| `batch_digest_optional_not_a_measure` | Optional batch rollup is a count digest only. |
| `graph_stays_cold_on_this_stamp` | Graph lift stays null. |
| `fill_sim_vocabulary_not_exp006_promote` | EXP-006 harness stays vocabulary-only. |
| `parent_stamp_not_rewritten` | Parent CLIs are not rewritten by this align pass. |

CLI recompute of a surround packet or lock receipt is still how `validate` works. A schema miss on that recompute is not permission to cite a closed book, a measure, LAYA authorize-run, risk-gate unlock, or live trading.

---

## Proof

```bash
python3 -m unittest tools.test_paper_laya_incomplete_rpc_honesty_schema_align_v0
python3 -m unittest tools.test_paper_laya_precompute_surround_packet_v0 tools.test_paper_laya_precompute_fill_sim_surround_packet_v0 tools.test_paper_laya_risk_gate_lock_receipt_v0
```

The align test loads the LAYA schemas with JSON Schema 2020-12 and a registry of all citeable paper schemas. Honest fixtures from #63–#65 pass schema and CLI. In-memory dishonest copies fail schema and CLI. Those copies are not checked in. Nothing under `/var/lib/mal` is added to git.

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
| Rewriting parent #49–#65 CLIs | Out |
| New runtime align-pass object | Out |

---

## Cross-links

- Fill-sim surround (#63): [PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-FILL-SIM-SURROUND-PACKET-V0.md), [PR #63](https://github.com/vaanai/MAL/pull/63) squash `171cd61`
- Non-fill-sim surround (#64): [PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md](PAPER-LAYA-PRECOMPUTE-SURROUND-PACKET-V0.md), [PR #64](https://github.com/vaanai/MAL/pull/64) squash `277a142`
- Risk-gate lock receipt (#65): [PAPER-LAYA-RISK-GATE-LOCK-RECEIPT-V0.md](PAPER-LAYA-RISK-GATE-LOCK-RECEIPT-V0.md), [PR #65](https://github.com/vaanai/MAL/pull/65) squash `7979988`
- Parent honesty align: [PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md), [PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md](PAPER-FILL-SIM-INCOMPLETE-RPC-HONESTY-SCHEMA-ALIGN-V0.md)
- Proof test: [tools/test_paper_laya_incomplete_rpc_honesty_schema_align_v0.py](../tools/test_paper_laya_incomplete_rpc_honesty_schema_align_v0.py)
- Manager digest: [SUMMARY.md](SUMMARY.md)
