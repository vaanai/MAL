# Observe sealed-row stamp (Scout sampler)

| | |
| --- | --- |
| **As-of** | 2026-09-21 |
| **Owner seat** | Scout (sampler CLI); Vaan re-runs locally |
| **Pairs with** | [OBSERVE-JSONL-SCHEMA.md](OBSERVE-JSONL-SCHEMA.md), [DEC-004](../DEC/DEC-004-regime-id-encoding.md), [EXP-001](../EXP/EXP-001-24h-ws-capture.md) |
| **Script** | [`tools/observe_sealed_row_stamp.py`](../tools/observe_sealed_row_stamp.py) |

Durable, stdlib-only, no-network **day-wide** stamp of PumpPortal observe JSONL (`observe_hot_v0` / `ingest_hot`). Replaces ad-hoc `data/observe/_scout_*` notes. **Does not reopen** Scout (manager) PASS on Vaan’s local capture — that verdict stands; this tool is so Vaan can re-audit without chat turns.

Live captures stay gitignored (`data/observe/observe-YYYY-MM-DD.jsonl`). Pass file paths as CLI args. Do not commit live JSONL.

## How to run

```bash
python tools/observe_sealed_row_stamp.py data/observe/observe-YYYY-MM-DD.jsonl
python tools/observe_sealed_row_stamp.py path/to/a.jsonl path/to/b.jsonl
```

Prints a thin `STAMP: PASS|FAIL` summary, per-gate status, and soft inventory counts. Writes `observe-sealed-row-stamp.json` next to the JSONL parent when writable, else under `data/observe/`. Override with `--artifact PATH` or skip with `--no-artifact`.

Synthetic dry-run (no live capture required):

```bash
python tools/observe_sealed_row_stamp.py tools/fixtures/observe_sealed_row_stamp_mini.jsonl --artifact /tmp/observe-sealed-row-stamp.json
python -m unittest tools.test_observe_sealed_row_stamp
```

## Exit codes

| Code | Meaning |
| --- | --- |
| **0** | Hard gates **PASS** (every `ingest_hot` row; at least one row; JSONL parseable) |
| **1** | Hard gates **FAIL** (or zero hot rows, or JSONL parse errors) |
| **2** | CLI usage / missing input file |

**Soft inventory never flips PASS → FAIL alone.** Bonk-style pool still labeled `bonding` / `market=bonding_curve`, and `ws_fields_unknown` keys that are present in `ws_payload`, are counted — not kill signals.

## Hard PASS gates (day-wide)

All files on the command line are AND-ed. One failing row fails the stamp.

| Gate | Pass rule |
| --- | --- |
| **dual_clocks** | `t_ws` present and parseable ISO-8601 (`Z` ok). `t_event` **null** (or absent/empty) is legal. Dual-clock **void** only when `t_ws` is missing. Non-null `t_event` must be parseable ISO if present. |
| **stage_map** | `stage` ∈ `{bonding, bonding_complete, migrating, pumpswap, legacy_raydium, UNK}`. Vendor `txType` `migrate` / `migration` (case-insensitive, payload or top-level) **must** be `stage=migrating`. |
| **regime_id** | DEC-004 pipe `key=value`, **no spaces** around `\|` / `=`, canonical key order `env\|source\|stream\|stage\|quote\|commitment\|venue\|instr\|fee\|market`. `stage=` in the pipe **equals** `row.stage`. |
| **knowable_at_t** | Object present. `quote` / `instr` / `fee` / `venue` match the regime pipe. Values containing `assumed` / `unverified` / `pending` **must not** claim the matching `*_verified=true`. |

Non-`ingest_hot` / non-`observe_hot_v0` lines are skipped (counted), not hard-failed.

## Soft inventory (notes only)

| Note | What is counted |
| --- | --- |
| Non-pump pool still bonding | `ws_payload.pool` / top-level `pool` looks like bonk/raydium/… while `stage` is bonding-ish or `market=bonding_curve` |
| `ws_fields_unknown` listed present | Keys listed in `ws_fields_unknown` that also exist in `ws_payload` (inventory drift); histogram of those keys |
| `is_mayhem_mode` | Rows whose payload has `isMayhemMode` / `is_mayhem_mode` |
| `pool` histogram | Distinct pool labels/pubkeys seen |
| `t_event` null vs present | Clock mix; null remains legal |

This stamp is **not** Proof’s EXP-001 n=100 RPC mis-label audit.

## Artifact shape

JSON object, `schema_version=observe_sealed_row_stamp_v0`: `stamp`, `exit_code`, `row_count`, `hard_gates` (pass / fail_count / ≤5 examples), `soft_inventory`, `files_detail`. Live stamp JSON under `data/observe/` is gitignored.
