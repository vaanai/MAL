# Discovery — wallet / follow signals for paper→gated

| | |
| --- | --- |
| **Research as-of** | 2026-09-23 |
| **Owner seat** | Scout (signal **taxonomy** + paper path). Layer-2 precompute remains Graph. |
| **Sibling** | [GRAPH-DISCOVERY-V0.md](GRAPH-DISCOVERY-V0.md) (landed PR #25) — **same S1–S7 / H-G vocabulary** |
| **This file** | Discovery **taxonomy ARTIFACT** only. **Does not mint an EXP id.** |
| **Graph measurement** | EXP-004 (`EXP-004-graph-creator-recurrence-v0`) is **Graph-owned** — contract + CLI on `main` ([EXP-004](../EXP/EXP-004-graph-creator-recurrence-v0.md), [GRAPH-DISCOVERY-V0.md](GRAPH-DISCOVERY-V0.md) §9). **Full-book Oracle score INCOMPLETE** until operator courier JSONL + marks (`priced_n` floors). Scout **taxonomy only** — does not register EXP-004 here. |
| **Lab locks** | Paper only. Knowable-at-T. Sealed JSONL = provenance spine. Postgres = Layer-2 ops/relationship **cache**. X = Layer 4 later. Soft bonk/mayhem **parked**. No live keys. No invented lift numbers. No EXP-002c retune. |

**Question:** which wallet / smart-wallet / follow signals should MAL **actually use**, and how do they inform **maximize-profit on the paper→gated path** (detect→decode→evaluate→runners), not live trading?

**Answer (working):** day-1 uses **weak create-spine links** already on sealed creates ([DEC-005](https://github.com/vaanai/MAL/pull/8) draft PR #8 §4) plus Graph **as-of-T** `graph_snapshot_v0` scalars: **H-G1** creator age / prior-mints, **H-G3** weak creator↔buyer recurrence, **H-G4** early-wallet Δt (when sealed timing allows), **regime-gated** via **S1**. Follow/copy-adjacent signals are **S3 features** (veto / select / enrich) that combine with L1/L2 before LAYA — **never mirror this wallet**.

---

## 0. Product lock + Scout constraints (Graph S1–S7)

Respect [LAB_STATE.md](../LAB_STATE.md) north star and adopt Graph’s landed **Scout soft constraints** ([GRAPH-DISCOVERY-V0.md](GRAPH-DISCOVERY-V0.md) §0) as this brief’s working law:

| # | Constraint | Implication for wallet / follow signals |
| --- | --- | --- |
| **S1** | **Regime gate first** | Every cluster / wallet tag carries parent `regime_id` (or `regime_gate=unknown`). **No cross-regime merge.** Index **I4** is this gate, not a bonk knob |
| **S2** | **Knowable-as-of-T only** | Membership fixed at `T_decision` with `t_precompute_as_of ≤ T_decision`. No post-hoc cluster assignment. `t_ws` vs `t_event` = **latency-as-data**; do not rewrite `T` |
| **S3** | **Follow ≠ mirror** | L3 follow = **feature / veto / select / enrich** on paper→gated. Graph emits **capped scalars and falsifiers**, not copy-trade targets |
| **S4** | **Executable windows** | 1s / 5s / 15s / 30s / 60s. Prefer signals from sealed observe **before or at** `T_decision`. Late enrich updates **later rows only**, never the same `signature` |
| **S5** | **Bonk / mayhem parked** | Not cluster seeds or kill knobs. Default population: bonding creates |
| **S6** | **Taxonomy + falsifiers only** | No mark densify, no evaluate retune, no invented lift thresholds |
| **S7** | **X / social = Layer 4** | Not a cluster seed. No X keys on host |

| Product lock | Implication here |
| --- | --- |
| Stateful ecosystem, not token→AI→trade | Signals live on wallets / creators / clusters / sequences |
| L1 + L2 + L3 filtered smart-wallet | This brief names **L3 features that consume L2 H-G scalars**; it does not replace Graph |
| Profit is the goal; plans are directions | Index candidates are **killable** (H-G1…H-G4), not a rigid feature lock |
| JSONL spine; Postgres cache | Measurement joins sealed observe + **existing** marks |

---

## 1. Index / candidate (thin, killable) — preferred day-1

Treat these four as **day-1 candidates**. They **map onto Graph H-G1…H-G4** (plus S1 for stage). Thin enough to kill. Score on the **full detect book** ([DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md)). Do **not** promote unverified funding / prior-launch **trees** into hot follow features.

| Scout Index | Graph hyp | What it is | What it is not |
| --- | --- | --- | --- |
| **I3** Creator key age / **prior-mint count** from **sealed history we already have** | **H-G1** `creator_age_seconds`, `prior_mint_count` (optional **H-G2** burst: `burst_count`, `min_gap_seconds`) | Book-local first-seen `t_ws` of reported `traderPublicKey`; count of prior `txType=create` with same key, **`T_prior < T_decision`**, same `regime_gate_key` | **No RPC invent.** Not `getSignaturesForAddress` trees. Book start **censors** true on-chain age — stamp `book_t0` |
| **I1** Recurring **creator↔early-buyer co-occurrence** across mints (as-of-T sealed book) | **H-G3** `creator_buyer_recurrence_weak` (strong tier = later `graph_enrich` only) | **Sequence / cluster score** from keys that already co-occur on sealed creates **before** `T`. Weak spine: same `P` as creator on prior mints **and** repeated `initialBuy` / `solAmount` signature — **not** a distinct buyer wallet yet | **Not** “follow this wallet.” Not RPC-invented buyer lists on the hot packet |
| **I2** **Early-wallet co-presence** within Δt of create (**coordination signature**) | **H-G4** `early_wallet_dt` — label `cluster_tier=weak_creator_only` vs `enrich_trade` | **Capped cluster score** on the full detect book. v0 bound: create feed may only expose **creator** timing | **Not** post-T sniper tape as evaluate input at create `T`. **Park** copy-trade interpretation |
| **I4** **Regime-tagged stage flags** already on sealed creates | **S1** regime-gated stage (`stage=`, `market=`, `regime_id`) — Graph “regime-gated” on every H-G | Bonding-first population + `regime_id` honesty (EXP-002c **gate**, not a new sweet-spot) | Soft **bonk/mayhem parked** (S5) — not kill knobs. **H-G6** migration follow-through is **not** a create-`T` decode input |

**H-G5** (reserve / launch-style sequence) is a Graph **collinearity check**, not a Scout Index pick. **H-G6** is retrospective only (leak at create T).

### Honesty on “early-buyer” (H-G3 / H-G4)

Free observe is **`subscribeNewToken` + `subscribeMigration` only**. A sealed create typically carries **one** reported wallet: `traderPublicKey` (creator, `creator_verified=false`). There is **no** first-block buyer set on the hot row.

So **I1/I2 day-1** are Graph’s **weak** tiers:

- **H-G3 weak:** creator↔creator recurrence + repeated create-tx funding **proxy** (`initialBuy` / `solAmount`) across mints before `T`.
- **H-G4:** `cluster_tier=weak_creator_only` until trade/RPC subsample exists.

True **counterparty / early-buyer** edges need metered trade WS or RPC trade-log `user` fields (`creator_buyer_recurrence_enrich`). EXP-003’s current TradeEvent decode **skips** the `user` pubkey (price path only). That is **later `graph_enrich`**, not a silent Index upgrade. Do **not** densify marks to invent buyers (S6).

### I2 / H-G4 Δt — two clocks (do not mix) (S2, S4)

| Clock | Legal use | Leak if mixed |
| --- | --- | --- |
| **Pre-T window** `(T − Δt, T]` | **Evaluate / decode** cluster score (knowable-at-T) | — |
| **Post-T window** `(T, T + Δt]` | **Diagnostic / later L3 reassess** only; never decode-at-T | Using post-T buyers to pick the create-`T` runner is lookahead |

Default Δt for Graph’s **EXP-004** measurement is an **as-of-T** window on **create timestamps**, not trade tape. Exact seconds are an EXP knob, not a lock here.

---

## 2. Day-1 create spine = weak links only (DEC-005)

Fields on the **free PumpPortal create feed** are **weak create-spine links**: knowable-at-T as **reported**, not verified on-chain history ([REGIME-AT-INGEST-MATRIX.md](REGIME-AT-INGEST-MATRIX.md) creator row; DEC-005 §4; Graph §5.1).

| Field | Edge gloss | Stamp honesty |
| --- | --- | --- |
| `mint` + `signature` | Event identity | Required; `UNK` → void / reject |
| `traderPublicKey` → creator | mint↔creator **reported** | `creator_verified=false` until RPC CreateEvent |
| `bondingCurveKey` | curve PDA | WS as-of processed `T` |
| `initialBuy` / `solAmount` | **create-tx funding proxy only** | Not a funding tree; H-G3 weak signature |
| `regime_id`, `stage`, `knowable_at_t` | S1 gate | Spine |
| migration `pool` | mint↔venue | Migration rows; not a create-`T` follow feature |

**Do not** promote into hot follow features:

- Unverified **funding trees** / associated wallets (Graph park: unverified funding on hot packet)
- RPC **prior-launch trees** beyond our sealed book
- Silent `creator_verified=true` on the same sealed row
- Metered-trade counterparties as if they were on the create spine

**Strong provenance** lives in `type=regime_enrich` / `type=graph_enrich` **child rows** — never spine backfill. Graph packet = **`type=graph_snapshot_v0` sidecar**, not `ingest_hot` patch.

---

## 3. Park / theater (aligned with Graph §3.2)

| Parked | Why |
| --- | --- |
| **Single-wallet PnL chase / “alpha wallet” lists** | **S3** — no single-wallet chase |
| **Blind copy-trade / mirror this wallet** | DEC-009 + S3: never blind copy |
| **Unverified funding on the hot packet** | DEC-005 §4; Graph park |
| **Soft bonk/mayhem as kill knobs** | **S5** |
| **X edges / social cluster seeds** | **S7**; no X keys on host |
| **Hot-packet RPC backfill** | DEC-005 / matrix: no silent post-`T` RPC into the sealed hot row |
| Smart-wallet follow as v0 product | Graph: requires proven L2 + filter economics; L3 **later** after H-graph |
| Neo4j / Postgres-first graph theater | Postgres = Layer-2 **cache**; JSONL snapshots first |
| Paid Birdeye wallet index | Hard defer |
| Metered trade WS as day-1 graph | Graph v0 deferred |
| Graph scores inside evaluate **before EXP-004** | Would confound rules vs graph Discovery |
| **H-G6** create-time migration edge | Leak |

---

## 4. Signal taxonomy (classes)

Classes **beyond** Index are listed so later enrich has a name. **Day-1 implementers use §1 / H-G1…H-G4 only.**

| Class | Layer | Day-1? | Feature shape | Copy-trade risk |
| --- | --- | --- | --- | --- |
| **A. Wallet reputation (book-local)** | L2→L3 | **I3 / H-G1** | `prior_mint_count`, `creator_age_seconds`, as-of-T reject/runner history | Low if capped + full-book scored |
| **B. Creator history (sealed)** | L1/L2 | **I1+I3 / H-G1–H-G3** | Repeat-launch, burst, weak funding-proxy signature | Medium if “serial creator ⇒ buy” without L1 |
| **C. Smart-wallet / smart-money proxies** | L3 | **Parked as labels** | Any “smart” tag is a **hypothesis label** on a cluster score, not a vendor list | **High** if used as follow-set membership |
| **D. Follow / copy-adjacent** | L3 | **S3 features only** | Veto / select / enrich on the decode packet | **Forbidden** as “copy wallet X” |
| **E. Cluster / relationship hooks** | L2 | **I1+I2 / H-G3–H-G4** | Capped cluster / sequence score; `graph_snapshot_v0` **separate** from spine | Low if depth-capped ([CONSTITUTION.md](../CONSTITUTION.md) §5) |
| **F. Strong provenance / funding trees** | L2 enrich | **Later** | RPC CreateEvent `user`/`creator`, funding source | Do not hot-follow until verified |
| **G. Trade counterparties** | L2/L3 | **Later** | `creator_buyer_recurrence_enrich` / `enrich_trade` | Post-T: reassess only |
| **H. X / social identity** | L4 | **S7 parked** | — | Out of phase 0 |

---

## 5. Knowable-at-T / latency-as-data (S2)

| Class | Knowable at create `T`? | Latency-as-data |
| --- | --- | --- |
| **I4 / S1** regime/stage | **Yes** — stamped at ingest | `t_ws` always; `t_event` often **null** on PumpPortal create ([OBSERVE-JSONL-SCHEMA.md](OBSERVE-JSONL-SCHEMA.md)). `T_decision = t_event` if present else **`t_ws`** (DEC-005 §1). Do not slide `T` to enrich time |
| **I3 / H-G1** prior-mints / key age | **Yes**, sealed creates with `T_prior < T_decision`, same `regime_gate_key` | Left-censored at capture start. Stamp `n_prior`, `t_first_seen`, `book_t0` |
| **I1 / H-G3 weak** | **Yes**, same as H-G1 (prior sealed creates only) | Recurrence of 1 is a first-seen creator, not “alpha” |
| **I2 / H-G4** co-presence | **Pre-T window only**; may be empty (`cluster_tier=weak_creator_only`) | Post-T co-presence is **not** knowable at `T` (S4) |
| WS `traderPublicKey` | Knowable as **reported** | `creator_verified=false`; RPC confirm is a **child** packet |
| `initialBuy` / `solAmount` | Knowable as WS snapshot | Funding **proxy**, not path |
| Marks (EXP-003) | **Outcomes after T** | Never decode inputs. Join `T < t_mark ≤ T+H` |
| RPC wallet history | **Not** at `T` unless ingest-window RPC finished and flagged | Late enrich = later rows only (S4) |
| X | **Not** (S7) | Parked |

**Precompute:** all L2 scores on the decode packet MUST come from state updated **strictly before** `T_decision`; stamp `t_precompute_as_of` (DEC-005 §3). Graph sidecar: `type=graph_snapshot_v0`.

---

## 6. Executable windows — 1s / 5s / 15s / 30s / 60s (S4)

Paper horizons stay [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md). **Primary kill horizon: 60s** (same as EXP-002*). Features not ready **before** `T` cannot pick the runner at `T`.

| Signal | Ready for evaluate at `T`? | 1s | 5s | 15s | 30s | 60s | Later enrich |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **I4 / S1** stage / `regime_id` | **Yes** | same | same | same | same | same | RPC quote/instr/fee = child rows |
| **I3 / H-G1** prior-mint / key age | **Yes** if book has prior rows | same | same | same | score | **primary kill** | RPC true age |
| **I1 / H-G3 weak** | **Yes** (pre-T co-occurrence) | same | same | same | score | kill | `creator_buyer_recurrence_enrich` |
| **I2 / H-G4** (pre-T Δt) | **If** sealed timing exists; else honest empty | may be empty — **not** 0 | more mass | more | score | kill | `enrich_trade` |
| Post-T early buyers | **No** | leak | leak | leak | leak | leak | L3 reassess |
| Metered trade WS | **No** | — | — | — | — | — | Forward capture only |
| X | **No** (S7) | — | — | — | — | — | Layer 4 |

**1s caveat:** RPC `blockTime` is ~1s; many 1s **outcome** cells are N/A even when 5s is ok ([EXP-003](../EXP/EXP-003-post-create-marks.md)). That is an **outcome** limitation, not a reason to delay H-G1/S1 features. H-G4 falsifier: Δt **not ready** inside 1s from sealed rows (Graph §4).

---

## 7. Paper→gated maximize-profit (not live exec)

Pipeline: **detect** → **decode** (spine + optional capped as-of-T graph) → **evaluate** (packet only) → **runners** → **risk gate** (later) → exec **deferred** ([DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md)). Graph §6: score PRE **in parallel** to rules until H-graph PASS — **do not** put graph inside evaluate **before EXP-004**.

| Use (S3) | Who | Index / H-G | Rule |
| --- | --- | --- | --- |
| **Select** | Evaluate **after** EXP-004-style kill, or parallel Discovery score | I1, I3, I4 / H-G1, H-G3, S1 | Paper runner when as-of-T cluster / recurrence / stage packet passes a **documented**, killable rule. Combine with L1 curve fields. **Not** a copy of wallet X |
| **Veto** | Evaluate | I1, I2, I3 / H-G3, H-G4, H-G1 | Reject serial-spam farms, missing identity, regime mismatch, or a coordination signature that a **later EXP** shows dumps — do not assume. Packet-only |
| **Enrich** | Decode attach | H-G1…H-G4 caps | Capped scalars on `graph_snapshot_v0` |
| **Sizing-later** | Risk gate (not evaluate v0–v2) | I1–I3 caps | Cluster score may **cap notional** after a **non-adverse** selection hyp exists. **Not tonight.** No live size. No EXP-002c constant nibble |

**Maximize-profit implication (hypothesis, not a number):** EXP-002b/002c failed by selecting **modal launch snapshots** (adverse vs random at 60s). Wallet/cluster features are interesting **only if** they select a **different** runner cohort than v1/v2 sweet-spots — and that cohort is **non-adverse** on the **full detect book** after costs. If cluster scores just re-identify the same modal creates, the class is worthless (**H-G2** collinear-with-modal falsifier).

**Do not:** retune EXP-002c constants to absorb H-G1. New hyp → Graph’s EXP-004 measurement — or **pause**.

---

## 8. Kill criteria / falsifiers

No invented lift. Map onto Graph §4 falsifiers.

| ID | Class | Worthless if… |
| --- | --- | --- |
| **K-HG1** | H-G1 / I3 | Stratified outcomes vs spine-only show **no separable arm** at any of 1/5/15/30/60s; or **INCOMPLETE** priced coverage; or effect **vanishes** when split by `regime_id` / `instr=` / `fee=` (S1 leak); or the feature is just longer-capture censoring |
| **K-HG2** | H-G2 burst | Burst flags **redundant** with H-G1 marginals; or collinear with **modal launch snapshot** at T (EXP-002c pattern — attribute, do not retune rules) |
| **K-HG3** | H-G3 / I1 | Weak and strong tiers **disagree** on stratification; or buyer recurrence **only** appears cross-regime (S1 fail); or implemented as follow-wallet |
| **K-HG4** | H-G4 / I2 | Δt features **empty** on book; or **not ready** inside 1s from sealed rows (S4); or clusters **unstable** under regime split; or post-T tape leaked into the feature |
| **K-I4** | S1 / I4 | Stage flags **mix** bonding vs graduated; or bonk/mayhem smuggled in (S5) |
| **K-copy** | Any L3 | Feature is **mirror wallet X** / single-wallet PnL ranking without L1+L2 (S3) |
| **K-leak** | Any | Decode uses marks, post-T buyers, RPC trees, or X (S2/S7) |
| **H-graph** | Graph packet | A/B at **1s/5s/15s/30s/60s** shows **no stable lift** vs spine-only ([LAB_STATE.md](../LAB_STATE.md)) |

Proof gates for **EXP-004** (Graph, not this artifact): full-book labels; `no_lift_vs_random`; reject↔runner parity; priced_n floors or **INCOMPLETE**; **no promote** on incomplete marks; **no densify marks to rescue lift** (S6).

---

## 9. Cheap-first measurement path

**This artifact does not run an EXP.** Graph **`EXP-004-graph-creator-recurrence-v0`** is **landed on `main`** ([EXP-004](../EXP/EXP-004-graph-creator-recurrence-v0.md), [GRAPH-DISCOVERY-V0.md](GRAPH-DISCOVERY-V0.md) §9): regime-gated, capped as-of-T **H-G1 / H-G3 / optional H-G4** vs spine-only on the full detect book at 1/5/15/30/60s. Operator runs `python -m tools.exp004_graph_discovery` on laptop/Oracle JSONL + existing marks; cloud agents have no courier book — **no scored lift claimed here**.

Scout cheap-first inputs aligned to that EXP (laptop courier JSONL, same as EXP-002*):

| Step | What | Not |
| --- | --- | --- |
| 1 | Offline pass over `observe-*.jsonl`: for each bonding create at `T`, compute H-G1 (`n_prior`, `t_first_seen`) and H-G3/H-G4 from rows with `T_prior < T_decision`, scoped by `regime_gate_key` | RPC wallet crawl as a requirement |
| 2 | Attach scores as **`graph_snapshot_v0` sidecar** keyed by `signature` / `mint` — **do not** rewrite `ingest_hot` | Hot-packet RPC backfill |
| 3 | Score **full detect book** with **existing** `--marks`; last tick `T < t_mark ≤ T+H` | Dexscreener/Birdeye prices |
| 4 | Stratify H-G buckets vs **random same-n** and vs **v2 runner-set overlap** (new cohort vs modal slice) | Tightening v2 constants |
| 5 | If directionally **non-adverse** but thin priced_n → Proof may allow **denser marks for power only** | Densify-marks-as-retune |
| 6 | Paid RPC / metered trade WS **only if** create-miss or mark-hole meters trip [DEC-008](../DEC/DEC-008-stack-phase-gates.md) Gate 1 | Paid infra as a Discovery requirement |

---

## 10. Non-goals (this artifact)

- Minting an EXP id in this PR (Graph owns EXP-004 measurement on main)
- **EXP-002c retune** / OPTIMIZE-to-gate / new v2 constants
- Live trading, live keys, capital, PumpPortal trading API
- X collectors or X keys on `mal-core-0` (S7)
- Invented empirical lift / “smart money” vendor lists
- Unparking bonk/mayhem (S5)
- Promoting unverified funding/prior-launch **trees** into hot follow features
- Neo4j, paid RPC, Birdeye, gRPC as requirements
- Choosing a trading/wallet **execution surface** (Axiom / Phantom / …) — open research, no pick
- Real paper-trading utility beyond marks — **ask first**

---

## 11. Needs measurement (no numbers claimed)

| ID | Unknown |
| --- | --- |
| M1 | Distribution of `prior_mint_count` for `traderPublicKey` on sealed books (H-G1) — many keys may be first-seen |
| M2 | Whether H-G1/H-G3 buckets **overlap** the v2 runner set (same adverse modal slice — H-G2 falsifier) |
| M3 | H-G4 density in pre-T Δt on create-only JSONL (may be empty → INCOMPLETE, not a fake 0) |
| M4 | Whether EXP-003 logs can later expose trade `user` without a marks-retune (H-G3 strong; separate) |
| M5 | `t_ws` vs RPC `blockTime` skew for clustering (S2 clock honesty) |
| M6 | `regime_gate_key` normalization — full `regime_id` vs hashed prefix (Graph open Q5; Scout/Graph pick in G0 spec) |

---

## Sources

- Graph sibling: [GRAPH-DISCOVERY-V0.md](GRAPH-DISCOVERY-V0.md) (S1–S7, H-G1…H-G6, EXP-004 §9 landed); measurement: [EXP-004](../EXP/EXP-004-graph-creator-recurrence-v0.md)
- Lab: [LAB_STATE.md](../LAB_STATE.md), [CONSTITUTION.md](../CONSTITUTION.md), [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md), [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md)
- DEC-005 (draft PR #8): [hot-packet clocks + weak create-spine links](https://github.com/vaanai/MAL/pull/8)
- Spine: [OBSERVE-JSONL-SCHEMA.md](OBSERVE-JSONL-SCHEMA.md), [PUMPPORTAL-PAYLOAD-INVENTORY.md](PUMPPORTAL-PAYLOAD-INVENTORY.md), [REGIME-AT-INGEST-MATRIX.md](REGIME-AT-INGEST-MATRIX.md)
- Paper book: [EXP-002c](../EXP/EXP-002c-rules-v2-adverse-selection.md) **INCOMPLETE closed** (do not retune); [EXP-003](../EXP/EXP-003-post-create-marks.md) marks = outcome meter
- PumpPortal create/migration (free): https://pumpportal.fun/data-api/real-time/
