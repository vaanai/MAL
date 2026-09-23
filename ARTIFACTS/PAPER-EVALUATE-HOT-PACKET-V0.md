# Paper evaluate → runners on hot-packet v0 — Proposed paper stamp

| | |
| --- | --- |
| **ID** | `paper-evaluate-hot-packet-v0` |
| **Status** | **Proposed** paper registration (2026-09-23). Not a measure. Not run. |
| **Owner seat** | Proof (evaluate + paper runner stamp); Scout (packet spine); Helm (FORMAL auth) |
| **Commission** | Helm FORMAL auth 2026-09-23. Consumer of merged hot-packet v0 (`274faa2`, PR #49). |
| **Schema** | [paper-evaluate-hot-packet-v0.schema.json](paper-evaluate-hot-packet-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/paper_evaluate_hot_packet_v0/](../fixtures/paper_evaluate_hot_packet_v0/) — synthetic, not host extracts |
| **CLI** | `python -m tools.paper_evaluate_hot_packet_v0` — validate / example / evaluate on local JSON only |
| **Soft GATE** | Required. Watches in [Soft watches](#soft-watches-non-blocking) stay **non-blocking**. |

This file is the object **LAYA / DEC-006** cite for an evaluate→runners step whose **only decode input** is a validated [`hot_packet_v0`](HOT-PACKET-V0.md). It is an artifact registration, same class as hot-packet v0.

It is **not** EXP-009. It is **not** a sealed Oracle measure. It is **not** an EXP-002c retune. An EXP needs a hypothesis, windows, and a kill-attempt ([EXP/README.md](../EXP/README.md)). This stamp has none of those. Merge does not score a book.

---

## What this stamp is

[DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md) splits the paper path:

| Stage | This registration |
| --- | --- |
| Detect | Unchanged. Sealed `ingest_hot` rows stay the book. This stamp does not read them. |
| Decode | One object: `type=hot_packet` / `schema_version=hot_packet_v0`. |
| Evaluate | Rules-only label `runner` \| `reject` from **that object’s fields**. |
| Runners | Paper promotion **shape** at the packet decision clock. Horizon values and `Δ_exec` stay null, with an explicit status. |

The output is `type=paper_evaluate_stamp` / `schema_version=paper_evaluate_hot_packet_v0` / `id=paper-evaluate-hot-packet-v0`.

| Rule | v0 fact |
| --- | --- |
| Decode input | The embedded `input.packet` only |
| Raw WS replay | Off this step (`input.raw_ws_replay=false`) |
| Post-hoc enrich on the same evaluate step | Off (`input.post_hoc_enrich_on_evaluate=false`). An enriched overlay already inside the packet is a prior decode fact, not a new enrich call |
| `ws_payload` | Absent. The hot-packet byte cap already rejects a pasted payload |

---

## Evaluate rules

Rules-only. No JEV, no LLM, no EXP-002b / EXP-002c numeric thresholds (market-cap bands, zero-creator-buy, reject-rate floors). Those stay on their EXPs. This ruleset id is `paper_evaluate_hot_packet_v0_rules`.

Knowable-at-T means: read `l1_spine`, `regime`, `knowable_at_t`, `graph`, `clocks`, `dual_read`, `honesty`, `provenance` as they already sit on the packet. Do not synthesize a field the packet left null.

| Order | Reason code | Reject when |
| --- | --- | --- |
| 1 | `missing_signature` | `l1_spine.signature` is missing, blank, or `UNK` |
| 2 | `missing_mint` | `l1_spine.mint` is missing, blank, or `UNK` |
| 3 | `stage_not_bonding` | `l1_spine.stage` or `regime.components.stage` is not `bonding` |
| 4 | `honesty_reject` | `paper_only` is not true, `honesty.registration` is not `proposed`, or any hot-packet honesty hard-cap flag is true |
| 5 | `overlay_discipline` | A Proposed tag is locked `production`, a Proposed tag sits on `overlay=sealed`, a sealed overlay has `quote_verified` or `venue_verified` true, or `creator_verified` is true |

Label **`runner`** when the list is empty. Label **`reject`** otherwise. `reasons` is this closed list only. Slot ids and lift figures are not reasons.

**Identity** is `signature` and `mint` only. `traderPublicKey` stays the weak create-spine link from hot-packet v0. A null trader is not `missing_signature`.

**`UNK`** is a non-empty string, so hot-packet validation accepts it. Evaluate treats `UNK` as missing identity (same token as [EXP-002](../EXP/EXP-002-evaluate-runner-v0.md)). The checked-in reject fixture is `mint=UNK`.

**Overlay discipline** (do not “fix” this into an EXP-002 sealed-row honesty reject):

| Packet fact | Evaluate result |
| --- | --- |
| Sealed overlay, `fee=unverified`, `instr=pending_rpc`, verified flags false | Not a reject. That is the honest sealed create. |
| Enriched overlay, `fee=global_95bps` locked `proposed` | Not a reject. The tag stays **Proposed**. |
| Enriched overlay, `instr=launchlab_init` / `venue=launchlab` / `market=launchlab_pool` locked `proposed`, fee left `unverified` | Not a reject. Shape only. |
| Proposed tag on sealed overlay, or Proposed tag locked `production` | `overlay_discipline`. |

EXP-002 `knowable_at_t_honest` rejects a non-`unverified` fee because it reads a sealed row plus `ws_payload`. This step has no `ws_payload` and must not copy that rule onto an enriched overlay.

**Stamp path.** The CLI emits a stamp only after `hot_packet_v0` validation succeeds. Honesty and overlay failures already fail that validation, so they do not appear on a checked-in stamp. They stay in the table so a looser schema cannot launder them into `runner`. If validation and these rules ever disagree, the CLI emits no stamp.

### Graph-cold default

Empty or null slots do not invent graph lift. A filled allowlisted slot does not invent it either. v0 does not score H-G1, and it does not revive H-G2, ordinal buckets, NH-Index, or NH-G3a.

| `graph.cold` | Slots | `graph_lift` | `graph_lift_status` |
| --- | --- | --- | --- |
| `true` | null, `[]`, or every value null | `null` | `not_used_graph_cold` |
| `false` | at least one non-null allowlisted value | `null` | `not_used_slots_not_scored` |

`graph_lift` has no numeric value in this schema. Zero is not a stand-in.

---

## Runner stamp

Paper promotion shape at the packet decision clock. Present on **both** labels so a later book can keep the reject arm ([DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)).

| Field | Rule |
| --- | --- |
| `kind` | `paper_promotion` when `evaluate_label=runner`. `reject_arm_retained` when `reject`. |
| `pretend_buy` | `true` only on `runner`. `false` on `reject`. Still paper. |
| `live_capital` | `false` |
| `T` | Copy of `input.packet.clocks.T_decision`. Not enrich time, not emit time, not wall clock. |
| `clock_source` | `dec005_draft_pr8_unmerged`. [DEC-005](https://github.com/vaanai/MAL/pull/8) is still draft PR #8. |

**Decision clock (DEC-005 draft intent, already enforced on the packet).** `T_decision` is `t_event` when `t_event` is non-null, otherwise `t_ws`. This step does not recompute it. If the draft DEC later merges, the field name stays.

### Horizons

Keys, every value **null** on this registration. `horizon_status=null_ok_no_marks_on_this_stamp`.

| Family | Keys |
| --- | --- |
| DEC-006 primary set | `1s`, `5s`, `15s`, `30s`, `60s` |
| Extended | `+2s`, `+10s`, `+5m`, `peak`, `drawdown` |

Null means no mark was joined. It is not a 0% return. This CLI does not read `outcome_mark` files and does not invent prices. EXP-002 / EXP-003 / EXP-006 remain the scored horizon machinery.

### `delta_exec` / `Δ_exec`

| Field | v0 value |
| --- | --- |
| `field` | `delta_exec` |
| `also_called` | `Δ_exec` |
| `value` | `null` |
| `status` | `null_ok` |
| `reason` | `paper_stamp_has_no_fill` |

`null_ok` is the explicit status: a fill, fee, slippage, and latency were **not** computed. Null is legal. It is not a zero cost and not a PASS. The packet’s own `delta_exec.reason` stays `paper_packet_has_no_fill`. This stamp does not fill it.

---

## Full detect book

[DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md): evaluate labels do not delete history. Outcomes, when a later measure joins them, cover rejects and runners on the same yardstick.

Every stamp, both labels, sets:

| Field | Value |
| --- | --- |
| `full_book.policy` | `dec007_both_arms_retained` |
| `full_book.arm_retained` | `true` |
| `full_book.deletes_detect_history` | `false` |

This registration does **not** build a confusion matrix, a cohort mean, or a kill gate. A book is a set of stamps. Dropping `evaluate_label=reject` stamps would break the policy. The CLI has no measure exit codes (no `PASS` / `FAIL_NO_LIFT`).

---

## Soft watches (non-blocking)

Inherited from the hot-packet v0 Soft GATE (PR #49). `soft_watches.blocking=false`. `soft_watches.source=hot_packet_v0_soft_gate_pr49`. They do not fail merge of this registration.

| Watch id | What it is | Why it does not block |
| --- | --- | --- |
| `schema_looser_than_cli` | JSON Schema is the citeable shape. The CLI is tighter: it recomputes the label from the embedded packet, checks `T` against `clocks.T_decision`, and checks `graph_lift_status` against `graph.cold`. The same gap exists on hot-packet v0 (regime pipe vs components, `graph.cold` vs filled slots, `source_day` vs `t_ws`, slot value kinds). | Do not widen the CLI down to the schema. Do not hold this consumer for a schema rewrite. |
| `synthetic_launchlab_shape` | `enriched_launchlab_init_runner.json` is a shape example: Proposed `launchlab_init` / `launchlab` / `launchlab_pool`, fee left `unverified`. | Not a LaunchLab cohort, not a sealed-book rate, not an enum lock. |
| `graph_slot_shape_not_a_score` | `sealed_graph_slots_not_scored_runner.json` copies the hot-packet slot shape (one H-G1-shaped integer, H-G4 null). | `graph_lift` stays null. The integer is not lift and not a promote. |
| `dec005_draft_unmerged` | Clock field names follow draft PR #8. | Hooks only. Not a claim that DEC-005 merged. |
| `sealed_book_rpc_slice_incomplete` | Every embedded packet keeps `dual_read.sealed_book_rpc_slice=incomplete`. | This stamp does not close the sealed book. EXP-007e enriched PASS stays a sample overlay. |

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge of this registration | Docs + schema + synthetic fixtures + fixture CLI. **Does not** wire evaluate into observe |
| Input | Validated `hot_packet_v0` only |
| Encoder | **Not** promoted. `observe/client.py` is untouched |
| Enum production lock | **No.** `global_95bps` and `launchlab_init` stay **Proposed**. `venue=launchlab` and `market=launchlab_pool` stay **Proposed** |
| Discovery promote | **No.** EXP-004 / 004b / 005 / 005b soft watches stay watches. No Graph revive |
| EXP-002c | **Not** retuned. INCOMPLETE closed stands. No new thresholds |
| Oracle measure | **No.** `honesty.scored_oracle_measure=false`. No invented lift, EV, or return |
| Graph lift | **No.** `graph_lift=null` |
| Trading | Paper only. No live capital, no trading keys, no PumpPortal trade API |
| `Δ_exec` | Null with status `null_ok` |

`honesty` is const-false on scored measure, wiring, encoder promote, enum lock, Discovery promote, graph-lane revive, invented lift, EXP-002c retune, live capital, trading keys, and PumpPortal trade API. A stamp that flips those bits does not validate.

---

## CLI

From the repo root:

```bash
python -m tools.paper_evaluate_hot_packet_v0 example --which sealed-cold-runner
python -m tools.paper_evaluate_hot_packet_v0 example --which enriched-fee-runner
python -m tools.paper_evaluate_hot_packet_v0 example --which enriched-launchlab-runner
python -m tools.paper_evaluate_hot_packet_v0 example --which graph-slots-not-scored
python -m tools.paper_evaluate_hot_packet_v0 example --which reject-missing-identity
python -m tools.paper_evaluate_hot_packet_v0 validate fixtures/paper_evaluate_hot_packet_v0/sealed_cold_graph_runner.json
python -m tools.paper_evaluate_hot_packet_v0 evaluate fixtures/hot_packet_v0/sealed_create_cold_graph.json
```

`--which` values: `sealed-cold-runner`, `enriched-fee-runner`, `enriched-launchlab-runner`, `graph-slots-not-scored`, `reject-missing-identity`.

`evaluate` reads one local hot-packet JSON file and prints a stamp. It does not call RPC, does not tail observe JSONL, and does not write a side book. Exit `0` when a stamp is emitted. Exit `1` when the file is not a valid `hot_packet_v0`. There is no measure exit code.

| Fixture | What it shows |
| --- | --- |
| `sealed_cold_graph_runner.json` | Sealed defaults, `graph.slots=null`, label `runner`, lift null |
| `enriched_global_95bps_runner.json` | Proposed `fee=global_95bps` on enriched overlay, still `runner`, fee not production-locked |
| `enriched_launchlab_init_runner.json` | Synthetic LaunchLab shape, label `runner`, fee left `unverified` |
| `sealed_graph_slots_not_scored_runner.json` | Allowlisted slot filled, `graph_lift_status=not_used_slots_not_scored` |
| `reject_missing_identity.json` | `mint=UNK`, label `reject`, reason `missing_mint`, arm retained |

Checked-in fixtures are **synthetic**. They are not Oracle extracts and they are not lift evidence.

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
| Scored Oracle measure, invented lift / EV | Out |
| Joining marks or filling `Δ_exec` | Out |
| Soft watches above | Listed, **non-blocking** |

---

## Cross-links

- Decode packet: [HOT-PACKET-V0.md](HOT-PACKET-V0.md), [hot-packet-v0.schema.json](hot-packet-v0.schema.json)
- Pipeline: [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md)
- Full book: [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)
- Clocks draft (unmerged): [PR #8](https://github.com/vaanai/MAL/pull/8)
- Vocabulary only (do not retune): [EXP-002](../EXP/EXP-002-evaluate-runner-v0.md), [EXP-006](../EXP/EXP-006-paper-would-have-happened-harness-v0.md), [PAPER-TRADING-SURFACE-BRIEF.md](PAPER-TRADING-SURFACE-BRIEF.md)
- Local-set counts of these stamps (Proposed, sealed-day fixtures only): [PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md](PAPER-SCOREBOARD-SEALED-FIXTURE-V0.md)
