# EXP-001 — Regime/stage mislabel sample

Council lock (Proof / Scout / Helm). **No observe-wiring code changes** and **no live trading**. Local audit CLI lives in `tools/` so Vaan can score sealed JSONL on his PC (cloud cannot read that capture).

| Field | Value |
| --- | --- |
| **ID** | `EXP-001` |
| **Status** | Tooling ready. Population is local sealed JSONL on Vaan's host (not in git). Scoring pending local CLI run. |
| **Owner seat** | Proof (audit). Scout produced the WS population via `python -m observe`. |
| **Locked** | 2026-09-20 (method). Tooling 2026-09-21. |
| **Depends on** | Population: local JSONL from [observe](../observe/). Law: [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md), [DEC-004](../DEC/DEC-004-regime-id-encoding.md), [REGIME-AT-INGEST-MATRIX.md](../ARTIFACTS/REGIME-AT-INGEST-MATRIX.md), [PUMPPORTAL-PAYLOAD-INVENTORY.md](../ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md), [OBSERVE-JSONL-SCHEMA.md](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md) |
| **Hypothesis** | On sealed `ingest_hot` **create** packets, stamped `regime_id` and top-level `stage` agree with an independent reconstruction from DEC-003 / matrix rules + the WS→field map (knowable-at-T only). Hard-disagree rate is ≤1% (DEC/enum review) and ≤5% (wiring block). |
| **Method** | Independent check of the **sealed ingest packet** vs DEC-003 / regime-at-ingest matrix + WS→field map. Record **agree** / **disagree** / **inconclusive**. Never use a later RPC or enrich rewrite as the unit or as ground truth for this EXP. CLI: `python -m tools.exp001_mislabel`. |
| **As-of-T** | Only fields on the sealed packet at ingest (`t_ws`, `ws_payload`, stream, stamped labels). No lookahead, no `getTransaction`, no bonding-curve RPC, no Dexscreener/Birdeye, no child `regime_enrich` rows. |
| **Regime labels** | Labels under test: stamped `regime_id` (DEC-004 pipe `key=value`) + stage vocabulary only. |
| **Windows** | **N/A.** 1s / 5s / 15s / 30s / 60s predictive windows are out of scope. This EXP does **not** start the promote ladder. |
| **Kill-attempt** | Hard-disagree rate on the n=100 sample: **>1%** → DEC/enum review; **>5%** → wiring block. Dual-timestamp: `t_ws` always required; `t_event` null is **legal** (do not void or score as disagree). Void a row **only** if `t_ws` is missing. |
| **Result** | Pending local run (outputs under `data/observe/_exp001_*`, gitignored). |
| **Conclusion** | Pending. Promote ladder **not** started by this EXP. |

---

## 1. Scope lock

| Item | Lock |
| --- | --- |
| **Name** | EXP-001 — regime/stage mislabel sample |
| **Unit** | Sealed ingest packet only — never a later RPC/enrich rewrite |
| **Labels under test** | Stamped `regime_id` + stage vocab only |
| **Sealed packet** | Append-only `type=ingest_hot` JSONL row ([schema](../ARTIFACTS/OBSERVE-JSONL-SCHEMA.md)); corrections are new rows, not in-place edits ([CONSTITUTION.md](../CONSTITUTION.md) §6, matrix “do not backfill”) |
| **Knowable-at-T** | Reconstruction uses only information on that sealed packet at `T = t_ws` |
| **Latency-as-data** | Dual timestamps: `t_ws` (receipt) vs `t_event` (vendor event time). Δ is optional descriptive data when both exist; it is **not** a label verdict. |
| **Promote ladder** | **Not started** by this EXP (no EV, no strategy promote, no hot-packet promotion) |
| **Composer / Fast** | Composer 2.5 preferred; Fast mode **OFF** ([CONSTITUTION.md](../CONSTITUTION.md) §10) |

---

## 2. Population

**Create events** from sealed observe JSONL (`data/observe/observe-*.jsonl`).

| Rule | Detail |
| --- | --- |
| Source | `data/observe/observe-YYYY-MM-DD.jsonl` (UTC date of `t_ws`; gitignored) |
| Include | `type=ingest_hot` and `txType=create` (creates only). Expected `stream=subscribeNewToken`; a create with another stream still enters the population and is scored. |
| Exclude | Migration rows; `type=regime_enrich` / debug rows; any packet that is not the original sealed ingest line |
| Known local capture (Vaan PC; not in git) | `data/observe/observe-2026-09-20.jsonl` (~15k lines); `data/observe/observe-2026-09-21.jsonl` (~19k lines). Scout stamp-hygiene audit already **PASS** day-wide; this EXP is **mislabel of regime/stage stamps**, not capture uptime. |
| Capture ops | Local `python -m observe`, default `wss://pumpportal.fun/api/data`. Inventory key-diff vs [PUMPPORTAL-PAYLOAD-INVENTORY.md](../ARTIFACTS/PUMPPORTAL-PAYLOAD-INVENTORY.md) is Scout follow-on, not a verdict here. |

If JSONL is absent on the machine running the CLI, do not substitute live RPC, a short smoke file as “the” result, or enrich packets.

---

## 3. Sample

- **n = 100** create packets.
- **Stratify** by `regime_id` and top-level `stage` when volume allows (target equal or proportional allocation per observed stratum; record allocation rule).
- If a stratum is too thin for stratification (typical if almost all creates share one v0 composite), use **simple random** sampling and **report stratum counts** for the population and the sample.
- Record RNG seed (CLI default **`--seed 1`**) and the list of sampled `signature` (or JSONL line ids) in the summary JSON.
- Draw the sample **after** applying timestamp void rules (§6). Replacement draws: same inclusion rules until n=100 or population exhausted (if N<100, score all eligible creates and report N).

---

## 4. Unit of analysis

The **sealed ingest packet** (`ingest_hot` JSONL object) is the only unit.

| Allowed | Forbidden |
| --- | --- |
| Fields on that JSONL line, including verbatim `ws_payload` | `getTransaction` / account RPC as ground truth |
| Independent reconstruction from DEC-003, DEC-004, matrix, inventory WS→field map | Rewriting `stage` / `regime_id` on the row |
| Comparing stamped labels to that reconstruction | Treating a later `regime_enrich` child as the unit |
| Shared `observe.regime.build_regime_id` / `seal_ingest_record` for encoding + stage map | Divergent reimplementation of DEC-004 encoding |
| | Dexscreener, Birdeye, graph scores, social |

---

## 5. Labels under test

Only:

1. **Top-level `stage`** — vocabulary (DEC-004): `bonding` \| `bonding_complete` \| `migrating` \| `pumpswap` \| `legacy_raydium` \| `UNK`.
2. **Stamped `regime_id`** — pipe `key=value`, canonical key order (DEC-004): `env`, `source`, `stream`, `stage`, `quote`, `commitment`, `venue`, `instr`, `fee`, `market`.

Do **not** score quote-mint truth, instruction lineage, fee regime, pool program, or creator verification as primary labels. Those are RPC/enrich dimensions. The composite `regime_id` is checked for **encoding + WS-knowable defaults**, not on-chain confirmation.

---

## 6. Timestamp rules (dual timestamp / latency-as-data)

| Field | Rule |
| --- | --- |
| **`t_ws`** | **Always required.** Void the row (exclude from sample and from the denominator) **only** if `t_ws` is missing, null, or empty. |
| **`t_event`** | **Nullable.** `null` is **LEGAL**. Do **not** void rows for null `t_event`. Do **not** count null `t_event` as disagree. Do **not** synthesize `t_event`. |
| **Δ latency** | If `t_event` is non-null, Proof **may** record `t_ws − t_event` as descriptive latency-as-data. It does not enter agree/disagree. |

**Method kill:** a scoring pass that voids or hard-disagrees on null `t_event` is an invalid EXP-001 run (dual-timestamp rule broken).

---

## 7. Method — independent check vs DEC-003 / matrix + WS→field map

For each sampled sealed packet, reconstruct **expected** `stage` and **expected** `regime_id` from `stream` + `ws_payload` using the map below (no RPC). Compare to stamped fields.

### 7.1 WS→field map (creates; knowable-at-T)

| WS / packet input | Expected stamp |
| --- | --- |
| `stream=subscribeNewToken` and `ws_payload.txType=create` (or equivalent creation event) | `stage=bonding` |
| Same create, free WS (no `quote_mint` on payload) | `quote=wsol_assumed` inside `regime_id`; do **not** require `quote_verified=true` |
| PumpPortal FAQ commitment | `commitment=processed`, `source=pumpportal_ws`, `env=mainnet` |
| Create at T (matrix) | `venue=pump_program`, `instr=pending_rpc`, `fee=unverified`, `market=bonding_curve` |
| `stream` / `txType` missing or unknown on a row that still entered the create population | top-level `stage=UNK` and `stage=UNK` inside `regime_id` (non-empty `regime_id` still required) |
| `bonding_complete`, `pumpswap`, `legacy_raydium` | **RPC-only** at ingest unless a WS field is inventory-proven — **must not** appear on a WS-only create packet |
| `migrating` | Migration stream / `txType=migration` — **must not** appear on a create packet |

**Create `regime_id` reconstruction (v0 default):**

```text
env=mainnet|source=pumpportal_ws|stream=subscribeNewToken|stage=bonding|quote=wsol_assumed|commitment=processed|venue=pump_program|instr=pending_rpc|fee=unverified|market=bonding_curve
```

If expected `stage=UNK`, the `stage=` token and `market=` follow DEC-004 (`market=UNK` when stage is not in the bonding/migration/pumpswap set).

### 7.2 Checks (labels under test)

1. Top-level `stage` is in the locked vocabulary (including `UNK`).
2. Top-level `stage` equals reconstructed stage from §7.1.
3. `regime_id` parses as pipe `key=value` with DEC-004 canonical keys/order.
4. `regime_id` `stage=` token equals top-level `stage`.
5. WS-knowable `regime_id` tokens (`env`, `source`, `stream`, `quote`, `commitment`, `venue`, `instr`, `fee`, `market`) equal the reconstruction for that packet. Unexpected `quote=wsol` (verified) or `instr=create_v2` on a sealed create **without** those facts in `ws_payload` is **hard disagree** (lookahead / illegal backfill), not “better labeling.”

### 7.3 Verdicts

| Verdict | When |
| --- | --- |
| **agree** | All §7.2 checks pass. |
| **disagree** (hard) | Any §7.2 check fails with a determinate reconstructed expectation. |
| **inconclusive** | Packet cannot be scored without leaving the sealed unit or the map (e.g. missing/`null` `ws_payload`; malformed JSONL; `stream` vs `txType` conflict the map does not resolve). **Not** counted in the hard-disagree rate. Report count and reasons. |

Null `t_event` is never inconclusive by itself.

### 7.4 Rates

```text
hard_disagree_rate = (# hard disagree) / (# agree + # hard disagree)
```

Inconclusive and voided (`t_ws` missing) rows are excluded from the denominator. Report raw counts: eligible population N, voided, sampled, agree, disagree, inconclusive.

---

## 8. Kill tiers

| Tier | Threshold | Action |
| --- | --- | --- |
| DEC/enum review | **>1%** hard disagree | Open enum v1 and/or DEC-003/DEC-004 review (same trigger language as DEC-003). Wiring may stay up pending Helm. |
| Wiring block | **>5%** hard disagree | Observe-wiring is **measurement-blocked** until labels/map/client are fixed forward (new packets; do not edit sealed rows). |
| Dual-timestamp | `t_ws` required; `t_event` null legal | Void only missing `t_ws`. Invalid run if null `t_event` is voided or scored as disagree. |

No other kill (uptime, inventory drift, RPC mismatch) is in scope for EXP-001.

CLI summary JSON writes explicit **PASS/FAIL** for both gates (`gates.dec_enum_review`, `gates.wiring_block`) plus `overall` (`PASS` / `FAIL_DEC_REVIEW` / `FAIL_WIRING_BLOCK` / `INCOMPLETE`).

---

## 9. Out of scope

- Predictive EV / PnL / 1s–60s windows
- Graph edges and capped graph scores
- Social / X
- RPC ground-truth of quote, instr, fee, pool program, creator
- Dexscreener or Birdeye
- Trading, risk gate, execution
- **Promote ladder** — this EXP does not promote regime-at-ingest from working law to a higher rung, and does not start strategy promotion
- Observe-wiring edits (this deliverable is docs + local CLI only)

---

## 10. Results artifact (when Proof scores)

After a valid local run, Proof appends a **new** file (do not rewrite this lock):

- `EXP/EXP-001-results.md` — seed, stratum counts, n, voided, verdict table (`signature`, stamped `stage` / `regime_id`, reconstructed, verdict, notes), rates, kill-tier outcome.

Copy counts from `data/observe/_exp001_summary.json`. Sealed JSONL rows stay immutable.

---

## 11. Result / conclusion

| Field | Value |
| --- | --- |
| **Result** | _Pending — local CLI not yet run against Vaan's sealed JSONL._ |
| **Conclusion** | _Pending. This EXP does not start the promote ladder._ |

---

## 12. Local CLI runbook (Windows / WSL)

Offline. Needs only the repo plus sealed JSONL. No API key, no RPC.

**Outputs (gitignored):**

| File | Contents |
| --- | --- |
| `data/observe/_exp001_judgments.jsonl` | One judgment object per sampled row |
| `data/observe/_exp001_judgments.csv` | Same rows, spreadsheet-friendly |
| `data/observe/_exp001_summary.json` | n, void_n, agree/disagree/inconclusive counts + rates, PASS/FAIL vs >1% and >5%, stratum counts, seed, sample signatures |

### WSL / Linux / macOS (from repo root)

```bash
cd /path/to/MAL
# venv optional for this CLI (stdlib + in-repo observe.regime)
python -m tools.exp001_mislabel \
  data/observe/observe-2026-09-20.jsonl \
  data/observe/observe-2026-09-21.jsonl \
  --seed 1 \
  --n 100 \
  --output-dir data/observe \
  --prefix _exp001
# If `python` is missing: python3 -m tools.exp001_mislabel … (same args)
```

### Windows (cmd, from repo root)

```text
cd C:\path\to\MAL
python -m tools.exp001_mislabel data\observe\observe-2026-09-20.jsonl data\observe\observe-2026-09-21.jsonl --seed 1 --n 100 --output-dir data\observe --prefix _exp001
```

### Windows (PowerShell, from repo root)

```powershell
Set-Location C:\path\to\MAL
python -m tools.exp001_mislabel `
  data\observe\observe-2026-09-20.jsonl `
  data\observe\observe-2026-09-21.jsonl `
  --seed 1 --n 100 --output-dir data\observe --prefix _exp001
```

Default seed is **1**. Do not change seed unless recording a new run in `EXP/EXP-001-results.md`.

**Exit codes:** `0` overall PASS; `2` FAIL_DEC_REVIEW or FAIL_WIRING_BLOCK; `3` INCOMPLETE (no scored agree+disagree); `1` missing files / usage.

**Tests (no live JSONL required):**

```bash
python -m unittest tools.test_exp001_mislabel
```
