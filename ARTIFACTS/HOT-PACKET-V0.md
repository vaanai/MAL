# Hot packet v0 — Proposed paper contract

| | |
| --- | --- |
| **ID** | `hot-packet-v0` |
| **Status** | **Proposed** paper registration (2026-09-23). Not a measure. Not run. |
| **Owner seat** | Scout (L1 spine + regime tags); Graph (capped slots stay cold); Helm (FORMAL auth) |
| **Commission** | Helm FORMAL auth 2026-09-23, after EXP-007e Soft GATE PASS. Squash `37a98a8` is the parent this contract sits on. |
| **Schema** | [hot-packet-v0.schema.json](hot-packet-v0.schema.json) (JSON Schema 2020-12) |
| **Fixtures** | [fixtures/hot_packet_v0/](../fixtures/hot_packet_v0/) — synthetic, not host extracts |
| **CLI** | `python -m tools.hot_packet_v0` — validate / example only |

This file is the object **LAYA designers cite** for a capped decode packet. It is **not** EXP-009. An EXP needs a hypothesis, windows, and a kill-attempt ([EXP/README.md](../EXP/README.md)). Next work item 11 is a JSON spec aligned to the ingest matrix, so the registration lives next to [OBSERVE-JSONL-SCHEMA.md](OBSERVE-JSONL-SCHEMA.md).

**Default for design:** Graph is **cold** (`graph.slots` null or empty). Sealed creates keep observe defaults (`fee=unverified`, `instr=pending_rpc`, `quote=wsol_assumed`). Resolved platform tags appear only as an **enriched** overlay.

---

## What this packet is

DEC-006 decode is a knowable-at-T packet: regime, plus capped as-of-T graph **when evidence exists**. v0 names that packet `type=hot_packet` / `schema_version=hot_packet_v0`.

It is a **projection** a future decode step would build. It is not the sealed detect row. [OBSERVE-JSONL-SCHEMA.md](OBSERVE-JSONL-SCHEMA.md) `type=ingest_hot` stays the provenance spine. This packet references that row. It does not edit it.

| Layer | v0 contents |
| --- | --- |
| **L1 spine** | Create-time market fields knowable at decision T |
| **Regime** | DEC-004 `regime_id` plus per-component production / proposed lock |
| **Graph** | At most 5 slots. Null, empty, and all-null are valid. Killed lanes are not slots |

---

## Field inventory

### Envelope

| Field | v0 value | Role |
| --- | --- | --- |
| `schema_version` | `hot_packet_v0` | Contract id |
| `type` | `hot_packet` | Not `ingest_hot`, not `regime_enrich`, not `graph_snapshot_v0` |
| `paper_only` | `true` | No trading path |
| `byte_cap` | `8192` | Compact UTF-8 JSON size. Rejects a pasted `ws_payload` |
| `honesty` | see [Honesty](#honesty) | Machine-checked non-claims |
| `dual_read.overlay` | `sealed` \| `enriched` | Which book the regime tags were copied from |
| `dual_read.sealed_book_rpc_slice` | `incomplete` | Constant. Enriched PASS does not close the sealed book |
| `dual_read.enrich_type` | `null` or `regime_enrich` | `regime_enrich` only when overlay is `enriched` |

### L1 spine (knowable at T)

Every key is required. JSON `null` means the sealed create did not carry the field at T. Do not synthesize.

| Field | Type | Knowable-at-T rule |
| --- | --- | --- |
| `signature`, `mint` | string | WS at `t_ws`. Same strings as the parent `ingest_hot` row |
| `txType` | `create` | v0 is the bonding-create spine |
| `stream` | `subscribeNewToken` | Handler at T. Not rewritten |
| `source` | `pumpportal_ws` | |
| `commitment` | `processed` | FAQ assumption. Do not upgrade this row to `confirmed` |
| `stage` | DEC-004 stage | Create T stamps `bonding`. Do not infer `pumpswap` from curve fill |
| `traderPublicKey` | string or null | Weak create-spine link. `creator_verified` stays `false` |
| `name`, `symbol`, `uri` | string or null | Verbatim WS strings |
| `bondingCurveKey` | string or null | WS reported. A bogus key is not repaired inside this object |
| `initialBuy`, `solAmount` | number ≥ 0 or null | WS reported amounts. Units stay as delivered |
| `vTokensInBondingCurve`, `vSolInBondingCurve`, `marketCapSol` | number ≥ 0 or null | WS snapshot. `reserves_source=ws`. Do not overwrite with a later RPC reserve |
| `pool` | null on `stage=bonding` | Migration pool is a different event. Do not infer it |

Forbidden on the packet: `ws_payload`, Dexscreener, Birdeye, X / Twitter fields, outcome marks, lift numbers, secrets.

### Regime

`regime.regime_id` is the DEC-004 pipe string. Canonical order:

`env`, `source`, `stream`, `stage`, `quote`, `commitment`, `venue`, `instr`, `fee`, `market`

`regime.components` mirrors that string. `regime.component_lock` is `production` or `proposed` **per key**.

| Tag | Lock | Where it may appear |
| --- | --- | --- |
| `fee=unverified`, `fee=global_100bps`, `fee=creator_dynamic`, `fee=pumpswap_pool` | production vocabulary | `unverified` is the sealed-book default |
| `fee=global_95bps` | **Proposed** (EXP-007d) | Enriched overlay only. Never map 95 → `global_100bps` |
| `instr=pending_rpc`, `create`, `create_v2`, `unknown_until_rpc` | production vocabulary | `pending_rpc` is the sealed-book default |
| `instr=launchlab_init` | **Proposed** (EXP-007e) | Enriched overlay only |
| `venue=pump_program` | production vocabulary | Sealed default |
| `venue=launchlab` | **Proposed** (companion to `launchlab_init`; not in REGIME-ENUM production table) | Enriched overlay only |
| `market=bonding_curve` and the other DEC-004 market tags | production vocabulary | Sealed create default is `bonding_curve` |
| `market=launchlab_pool` | **Proposed** (enrich code tag; not production-locked) | Enriched overlay only |
| `quote=wsol_assumed` | production vocabulary | Sealed default, `quote_verified=false` |
| `quote=wsol` / `usdc` / `other` | production vocabulary | Verified quote only on enriched overlay |

`knowable_at_t.quote|instr|fee|venue` must equal `regime.components`. `quote_verified` and `venue_verified` may be `true` only on `overlay=enriched`. `creator_verified` is `false` in v0.

**Sealed overlay** (what the full sealed book still is, EXP-007 through EXP-007e): `fee=unverified`, `instr=pending_rpc`, `venue=pump_program`, `quote=wsol_assumed`, `market=bonding_curve`, `stage=bonding`, both verified flags false. Putting a Proposed tag on `overlay=sealed` is a schema error.

### Graph slots

| Field | Rule |
| --- | --- |
| `graph.cold` | `true` when `slots` is null, `[]`, or every `value` is null |
| `graph.max_slots` | `5` |
| `graph.lane_policy` | `exp004_capped_scalars_only` |
| `graph.slots` | `null` or an array of length 0–5. Elements may be null |

Allowlist (already named on the EXP-004 weak create spine). Filling one in a fixture is a **shape example**, not a promote and not a new score:

| `slot_id` | `hypothesis_status` | Value in v0 |
| --- | --- | --- |
| `creator_age_seconds` | `directional_watch_not_promoted` (H-G1 family) | number ≥ 0 or null |
| `prior_mint_count` | `directional_watch_not_promoted` (H-G1) | integer ≥ 0 or null |
| `min_gap_seconds` | `directional_watch_not_promoted` | number ≥ 0 or null. This is seconds since last create. It is **not** `burst_count` |
| `creator_buyer_recurrence_weak` | `directional_watch_not_promoted` (H-G3) | boolean or null |
| `early_wallet_dt_min_seconds` | `incomplete_do_not_densify` (H-G4) | **null only** |

`evidence_tier` is `weak_ws` only. No `rpc_verified` graph upgrade in this contract.

**Not slots** (parked, killed, or out of cap): `burst_count` (H-G2 `KILL_NO_SEPARABLE_ARM`), ordinal / `bucket_*` (NH-G1a cross-day KILL), NH-Index, NH-G3a, funding paths, multi-hop, X edges. The validator rejects those ids.

### Clocks and `Δ_exec`

[DEC-005](https://github.com/vaanai/MAL/pull/8) is still a **draft** (PR #8, not merged). LAB_STATE cross-links it and does not treat it as merged law. This contract uses the draft’s field names so a later merge does not invent a second clock vocabulary. `clocks.clock_source` is `dec005_draft_pr8_unmerged`.

| Field | Rule |
| --- | --- |
| `t_ws` | Parent receipt time. Timezone-aware ISO-8601 |
| `t_event` | Vendor time when the sealed row has one; otherwise **null**. Never synthesized |
| `T_decision` | `t_event` when non-null; otherwise `t_ws`. Not enrich time, not emit time |
| `t_precompute_as_of` | ≤ `T_decision`. On a Graph-cold packet it equals `T_decision` |
| `precompute_features_merged` | `false` when `graph.cold`, else `true` |
| `delta_exec.value` | **null**. `reason=paper_packet_has_no_fill`. No fill is computed here |

### Provenance

| Field | Rule |
| --- | --- |
| `spine` | `sealed_jsonl` |
| `source_day` | UTC calendar day of `t_ws` (`YYYY-MM-DD`). Day-aligned with `observe-YYYY-MM-DD.jsonl`. Do not join another day’s marks onto this packet |
| `parent_type` / `parent_schema` | `ingest_hot` / `observe_hot_v0` |
| `parent_signature`, `mint` | Match `l1_spine` |
| `fixture_origin` | `synthetic` on the checked-in examples. `sealed_row_projection` is reserved for a later offline copy from a sealed row |
| `supersedes` | `null`. Fix-forward is a new packet, and v0 does not pretend one |
| `day_aligned` | `true` |

Checked-in fixtures are **synthetic**. They are not Oracle extracts and they are not lift evidence.

---

## Knowable-at-T rules

1. L1 values are copied from the sealed create (top-level fields or WS keys present at `t_ws`) or they are null. This CLI does not perform that copy and does not open JSONL.
2. Labels that were not verified at T stay unverified (`*_assumed`, `*_verified=false`, `fee=unverified`, `instr=pending_rpc`).
3. Platform tags that EXP-007b–e resolved live on a child `regime_enrich` row. The hot packet may **point at** that overlay. It may not write them back onto `overlay=sealed`.
4. Graph slot values, when present, use only state with `t_precompute_as_of` ≤ `T_decision`. H-G4 stays null so create-spine emptiness is not filled in.
5. Outcome marks, paper fills, and `Δ_exec` economics are later packets. They are not fields of this object.
6. Dexscreener and Birdeye stay off the packet ([REGIME-AT-INGEST-MATRIX.md](REGIME-AT-INGEST-MATRIX.md)).

Matrix backfill law is unchanged: do not patch a sealed `ingest_hot` row. Emit a new record.

---

## Honesty

| Claim | v0 fact |
| --- | --- |
| Merge of this registration | Docs + schema + fixture CLI. **Does not** authorize continuous observe-wiring |
| Encoder | **Not** promoted. `observe/client.py` still seals `ingest_hot` with WS defaults |
| Enum production lock | **No.** `global_95bps` and `launchlab_init` stay **Proposed**. `venue=launchlab` and `market=launchlab_pool` stay **Proposed** companions. `honesty.enum_production_lock` is `false` |
| Discovery promote | **No.** EXP-004 / 004b / 005 / 005b soft watches stay watches. H-G2 stays killed. Ordinal lane stays parked. EXP-002c stays INCOMPLETE. No invented lift |
| EXP-007e enriched PASS | Knowable-at-T instr/quote on the **reused ≤500/day** sample. Sealed full book stays **INCOMPLETE** (`dual_read.sealed_book_rpc_slice=incomplete` on every v0 packet) |
| EXP-007d fee PASS | Same dual read: Proposed `global_95bps` on the enriched sample; sealed book fee stays `unverified` |
| Graph | Cold unless a future authorized precompute attaches an allowlisted weak slot. This PR does not revive a parked lane and does not densify |
| DEC-005 | Draft PR #8 only. Clock field names are hooks, not a statement that the DEC merged |
| Trading / X keys | None. Paper only |

`honesty` is const-false on wiring, encoder promote, enum lock, Discovery promote, graph-lane revive, and invented lift. A packet that flips those bits does not validate.

---

## CLI

From the repo root:

```bash
python -m tools.hot_packet_v0 example --which sealed-cold
python -m tools.hot_packet_v0 example --which enriched-fee
python -m tools.hot_packet_v0 example --which enriched-launchlab
python -m tools.hot_packet_v0 example --which graph-slots
python -m tools.hot_packet_v0 validate fixtures/hot_packet_v0/sealed_create_cold_graph.json
```

`--which` values: `sealed-cold`, `enriched-fee`, `enriched-launchlab`, `graph-slots`.

The tool reads local JSON and checks the rules in this file. It does not call RPC, does not tail observe JSONL, and does not write a packet onto a sealed row.

| Fixture | What it shows |
| --- | --- |
| `sealed_create_cold_graph.json` | Sealed defaults, `graph.slots=null` |
| `enriched_global_95bps_cold_graph.json` | Proposed `fee=global_95bps` on enriched overlay, Graph still cold |
| `enriched_launchlab_init_cold_graph.json` | Proposed `launchlab_init` / `launchlab` / `launchlab_pool`; fee left `unverified` |
| `sealed_graph_slots_nullable.json` | Five-slot cap, one H-G1-shaped integer, H-G4 null, no burst slot |

---

## Non-goals

- Continuous encoder or observe-wiring
- Production lock of `global_95bps` or `launchlab_init`
- Graph revive, X ingest, mark densify, EXP-002c retune
- A scored measure, a Discovery promote, or a live Oracle extract
- Filling `delta_exec`

---

## Cross-links

- Pipeline: [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md)
- Evaluate→runners consumer (Proposed, packet only): [PAPER-EVALUATE-HOT-PACKET-V0.md](PAPER-EVALUATE-HOT-PACKET-V0.md)
- Clocks draft (unmerged): [PR #8](https://github.com/vaanai/MAL/pull/8)
- Encoding: [DEC-004](../DEC/DEC-004-regime-id-encoding.md), [REGIME-ENUM-V0.md](REGIME-ENUM-V0.md), [REGIME-AT-INGEST-MATRIX.md](REGIME-AT-INGEST-MATRIX.md)
- Sealed row: [OBSERVE-JSONL-SCHEMA.md](OBSERVE-JSONL-SCHEMA.md)
- Overlay law: [EXP-007](../EXP/EXP-007-platform-regime-taxonomy-v0.md), [EXP-007d](../EXP/EXP-007d-fee-global-95bps-enum-v0.md), [EXP-007e](../EXP/EXP-007e-instr-quote-residual-v0.md)
- Graph cap and parked lanes: [GRAPH-DISCOVERY-V0.md](GRAPH-DISCOVERY-V0.md), [GRAPH-EXP004-NEXT-HYP-V0.md](GRAPH-EXP004-NEXT-HYP-V0.md), [EXP-004](../EXP/EXP-004-graph-creator-recurrence-v0.md)
