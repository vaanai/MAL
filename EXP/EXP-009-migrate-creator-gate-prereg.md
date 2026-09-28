# EXP-009 — Migrate creator-recurrence gate, pre-registration (holdout = unsealed fast hours)

**This file is written before any holdout data exists on disk.** It is a pre-registration only: no cell has been run, no threshold has been chosen, no result is reported. Declared (UTC): **2026-09-28T17:54:10Z**.

| Field | Value |
| --- | --- |
| **ID** | `EXP-009-migrate-creator-gate-prereg` |
| **Status** | **planned** — pre-registration; not run |
| **Owner seat** | Graph (measurement). `quant-proof` reviews before any sentence claims the gated book made money. |
| **Started** | 2026-09-28 |
| **Parent context** | Frozen migrate-direct cell ([ARTIFACTS/lab/migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md), locked 2026-09-27T13:06:36Z) is failing out of sample under both fail models. Venue fees on a filled round trip run ~2.435–2.511% ([ARTIFACTS/lab/fee-audit-2026-09-27.md](../ARTIFACTS/lab/fee-audit-2026-09-27.md) §1, §"Rescore at the slot-+1 priority"). 59.65% of attempts miss (same fee audit, §1 table). EXP-004 ([EXP-004-graph-creator-recurrence-v0.md](EXP-004-graph-creator-recurrence-v0.md)) found H-G1 (creator prior-mint count) and H-G3 (weak creator↔buyer recurrence) `DIRECTIONAL_NON_KILL` at 60s, both days scored, and explicitly not scored lift proof. |
| **Hypothesis** | Repeat or linked creators mark lower-quality `migrate` graduations. A gate that skips migrates from those creators raises gross per *filled* trade enough to clear the venue-fee drag the ungated frozen cell is currently failing under, on both the flat-15% and pressure-scale-1 fail models. |
| **Kill condition** | The primary cell (G1_only, §4) does not clear the unchanged promotion gate (§6) on the declared holdout (§5) under **both** fail models, **or** it clears only one of the two models, **or** it is the single best of the 4 cells and the multiple-comparison guard in §6 is not honored. Any of those kills the creator-recurrence gate for `migrate`. A single positive cell is not sufficient by itself — see the winner's-curse note already on record for the frozen cell (`LAB_STATE.md` §"First positive run"). |
| **Method** | Offline replay of the frozen migrate-direct execution (§2) with an added entry filter (§3–§4) on sealed backfill rows. No RPC calls beyond what the backward backfill has already made. No evaluate/runner code change. No new Helius credits (§9). |
| **As-of-T** | Constitution rule 3 (knowable-at-T). T is the migrate decision time — the same receive-clock substitution the frozen cell uses when `t_recv_ms` is missing ([migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md) §Windows). The creator gate feature must be computable from data sealed strictly before that T; see §3 and the feasibility finding in §8. |
| **Regime labels** | Unchanged from the base cell — the gate does not stratify by `regime_id`; every row still carries its regime tag at ingest (Constitution rule 4). |
| **Windows** | Not the EXP-004 1s/5s/15s/30s/60s Discovery grid. The outcome horizon is the base trade's own exit: `tp50_sl30` (first of +50% / −30% / 30-minute cap), unchanged from the frozen cell. |
| **Fail models** | Flat 15% on sends, and the pressure curve at scale 1 (frozen fit, not refit on this test set). Both are gates; scale 2 is reported only, per the frozen cell's own rule. |
| **Promotion gate** | Unchanged: ≥100 OOS trades, ≥5 distinct UTC days with a majority positive, lower 90% CI bound of mean SOL/trade > 0 (bootstrap 1,000 draws, seed 1, 5th percentile), total SOL still positive after removing the top 3 trades. Under **both** fail models. |
| **Result** | Not run. Pre-registration only. |
| **Conclusion** | Not run. |

---

## 1. Scope

One new degree of freedom versus the frozen cell: **whether the migrate is taken at all**, decided by a creator-history gate evaluated at the migrate decision time. Nothing else about execution changes (§2). At most 4 cells (§4). No evaluate/runner code changes. No live/forward-paper wiring in this PR.

## 2. Base trade — unchanged frozen-cell execution

Copied verbatim from [migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md), frozen 2026-09-27T13:06:36Z:

- Trigger: `migrate` (first PumpSwap print after a bonding print on that mint)
- Fill bound: slot+1 start (state is the last print with `slot < migration_slot + 1`)
- Slippage cap: 15% (`DEFAULT_SLIPPAGE_CAP = 0.15`)
- Exit: `tp50_sl30` (one sell; +50% / −30%, else the 30-minute cap)
- Route: direct (`portal_fee_ppm = 0`)
- Priority: 0.0005 SOL per side (500,000 lamports) — the slot+1 p75 of landed buys on the selection window. No Jito tip.
- Size: 0.5 SOL primary (500,000,000 lamports). 0.05 SOL reported secondary, same convention as the frozen cell.
- Fail models: flat 15% on sends, and the pressure curve at scale 1 (intercept −1.4548727851312098, slot slope 0.8, SOL slope 0.35, scale 1 — same fit as `mixed_net` in `tools/latency_curve.py`, not refit here).

This experiment does not touch priority, route, exit, fill bound, or size. It adds only an entry veto in front of the same trade.

## 3. Gate definitions — knowable-at-T only

Both features below are named directly from `tools/exp004_graph_discovery.py` (EXP-004's offline precompute), which is the only place in the repo that currently implements them. Neither uses `funding_graph.py` or any RPC call. The feasibility of running that code, or an equivalent, against backward-backfilled hours is the subject of §8 and is **not assumed** here.

- **G1 — creator prior-mint count.** `prior_mint_count(creator, T)` = the number of prior bonding-create events by the same creator wallet, with create time strictly before T, counted single-pass in true chronological order (`tools/exp004_graph_discovery.py::compute_graph_features_for_row` / `_update_creator_state`, lines 177–257). For a `migrate` entry, the creator identity is the creator recorded on that mint's own create event (looked up by mint), and T is the migrate decision time (§ As-of-T above), not the create time. This uses only creates sealed strictly before T — no RPC, no live state carried across restarts.
- **G3 — weak creator↔buyer recurrence.** `creator_buyer_recurrence_weak` = true iff `prior_mint_count(creator, T) > 0` **and** this create's `(initialBuy, solAmount)` pair matches a pair already recorded on one of that creator's own prior creates (`tools/exp004_graph_discovery.py::_buy_pattern`, `_select_hg3_positive`, lines 168–169, 437–443). It is a boolean, no threshold to tune.

`tools/forward_paper.py`'s live `WalletState.by_creator` (keyed on `create.creator`, lines 1288, 1334–1352, consumed by `funding_graph.fill_funding_features` and `laya_v0.creator_features`) is a third, independent implementation of "creator history" and is **not** used by this experiment — it resets to empty on every forward-paper process restart (`LAB_STATE.md` §"Manager update (2026-09-28)"), so it is left-censored on the live runner, not a source for a backward-hour holdout.

## 4. Cells (at most 4)

| Cell | Rule | Role |
| --- | --- | --- |
| `gate_off` | No filter — identical to the frozen cell. | Reference only, not a new claim. |
| `G1_only` | Enter iff `prior_mint_count(creator, T) >= k`, else skip the migrate. | **Primary** (the only cell provably causal on this holdout at declaration time — see §8). |
| `G3_only` | Enter iff `creator_buyer_recurrence_weak` is true, else skip. | Secondary, **contingent** on §8 — see §6. |
| `G1_and_G3` | Enter iff both `G1_only` and `G3_only` conditions hold. | Secondary, **contingent** on §8 — see §6. |

### Threshold procedure for k (fixed here; the value is not chosen in this file)

`k` is chosen **only** from the Oracle in-sample hours **2026-09-22T10:00:00Z through 2026-09-25T06:58:00Z** — the same in-sample window as the frozen cell's own 972-cell selection grid ([migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md) §Windows). No data outside that window, in-sample or holdout, is read to set k.

The fixed procedure: **k = the median value of `prior_mint_count(creator, T)` over all `migrate`-triggered rows in that in-sample window.** Ties round down (a fractional median rounds to the next lower integer). If that in-sample median is 0 — i.e. most creators in-sample are novel — k is instead fixed at 1 (first repeat), and that substitution is recorded, not re-tuned, in the later PR that computes it. This file fixes the rule; a later, separate PR computes and reports the number, before the holdout in §5 is read.

## 5. Holdout

**Primary holdout: fast-box backfill hours strictly older than 2026-09-19T01:00:00Z.**

- Declaration timestamp (now, UTC): **2026-09-28T17:54:10Z**.
- Current oldest sealed hour at declaration time: **2026-09-19T01 in progress** (not yet sealed) — per the fast-box backward walk, which proceeds from 2026-09-21T23Z backward (`LAB_STATE.md` §"Backfill split"). This file does not read the backfill manifest or any hour file to confirm that boundary; the point of a pre-registration is to fix the cut before the data behind it exists.
- Expected extent: the fast run stops at its +2,000,000-credit cap, expected to bind around 2026-09-29 18:00–19:00Z (`LAB_STATE.md` §"Manager update (2026-09-28)"), projected to reach down to roughly 2026-09-16.
- Disjointness from the frozen cell's existing OOS book: the frozen cell already used fast hours **2026-09-19T02 → 2026-09-21T23** and Oracle hours **2026-09-22T00–10** as its own out-of-sample book. Every hour in those two ranges is newer than the **2026-09-19T01:00:00Z** cut used here, so the 2026-09-19T01:00:00Z boundary already keeps this holdout disjoint from that OOS book — no separate exclusion step is required.
- This holdout does not exist yet at every hour; it fills in as the backfill walks backward. Scoring against it happens only after those hours are sealed, in a later PR.

**Secondary holdout (optional, note only): forward paper from the clean clock.** If a creator-gated book is later added to forward paper (a separate PR — not this one, and not proposed here), its rows from the clean clock **2026-09-28T00:00:00Z** onward would be a second, fully causal holdout (real-time decisions, no backfill-causality question of the kind in §8). Out of scope for this pre-registration; noted only so a later PR does not have to re-derive it.

## 6. Scoring and multiple comparisons

Score each cell under the unchanged promotion gate (top of this file), under both the flat-15% and pressure-scale-1 fail models, exactly as the frozen cell is scored.

Four cells are declared, but only **one** is eligible to be read as the primary result: **`G1_only`**, because it is the only cell shown causal on backward hours in §8 at the time of this declaration. `gate_off` is not a new claim — it reproduces the frozen cell and is reported for reference only. `G3_only` and `G1_and_G3` are secondary and **contingent**: per §8, the feature they depend on cannot currently be computed causally on backward hours, so they must not be used to declare success on this holdout, even if a later ad hoc computation happens to score them well, until the join described in §8 exists, is itself proven causal, and is pre-registered on its own terms. `G1_only` is declared as the single primary cell **in-sample, before the holdout in §5 is read** — the same winner's-curse guard already applied to the frozen cell's own selection (`LAB_STATE.md` §"First positive run").

## 7. Data dependency — feasibility of causal creator features on backward hours

This is the key feasibility check for this pre-registration, done by reading code, not by reading any data.

- `tools/exp004_graph_discovery.py` computes G1 and G3 from the **live PumpPortal WS `ingest_hot` schema only**. `is_bonding_create` requires `row["type"] == "ingest_hot"` plus `txType`/`stage` fields (`tools/exp002_paper_runner.py` lines 101–112), and the creator identity and buy pattern come from `traderPublicKey`, `initialBuy`, `solAmount` (`tools/exp004_graph_discovery.py` lines 161–169, via `_sealed_field`/`_sealed_float`). None of that is RPC-derived — it is read straight off the sealed create broadcast — so in principle it is knowable-at-T for any hour where that broadcast was sealed live.
- The backward backfill (`tools/pump_history_backfill.py`) does **not** use that schema. It reconstructs creates directly from on-chain `CreateEvent` logs via `decode_create_event` (lines 198–247), producing its own schema: `type="create"`, `mint`, `trader`, `creator`, `event_ts`, with `signature`/`slot`/`block_time`/`tx_index` stamped by `rows_from_block` (lines 383–444). The on-chain `CreateEvent` carries the creator pubkey and mint but **no dev-buy amount** — there is no `initialBuy`/`solAmount` field decoded there.
- **G1 is causally computable on backward hours**, but only via a new adapter that reimplements the same single-pass counting logic against the backfill's `creator`/`event_ts` fields instead of `traderPublicKey`/`t_ws`. That adapter does not exist yet; writing and testing it is a separate implementation PR, not this pre-registration. It needs zero new Helius credits — it only re-reads rows the backfill has already sealed via `getBlock`.
- **G3 is not currently computable on backward hours.** The feature needs the creator's own buy size on each of their creates, and `decode_create_event` does not carry it — the dev's first buy is a separate instruction in the same transaction, decoded into the backfill's `trades` sink, not the `creates` sink. Recovering it would need a same-transaction join between `creates` and `trades` (by `signature`/`tx_index`, both stamped on both sinks) that is not implemented anywhere in the tree today. Until that join exists and is itself shown causal (built only from data at or before T, no RPC call made after the fact), G3 cannot be scored on backward hours. This is exactly why §4/§6 restrict the primary cell to G1 only.
- This is a **different** failure mode from the funding-graph problem already on record. `tools/funding_graph.py` resolves wallets via live public-RPC/Helius calls at the time the graph-building process runs (module docstring, `tools/funding_graph.py` lines 9–11) and stamps `first_seen_ms` at resolution time, not at historical T — which is exactly why `skip_fresh_rug` was a no-op on the 972-cell grid: "the funding graph's earliest `first_seen_ms` is after the holdout cut, so unknown does not skip" (`ARTIFACTS/lab/latency-curve-2026-09-27.md` line 30). G1/G3 do not depend on `funding_graph.py` or any RPC call beyond the backfill's own `getBlock`s, so they do not inherit that specific problem — but G3 has its own, separate causal gap (missing dev-buy field on-chain in `CreateEvent`), documented above.

**Plain statement:** creator prior-mint count (G1) can be computed causally for the backward hours in §5, from the create stream itself, once a schema adapter is written. Weak creator↔buyer recurrence (G3) currently cannot, because the on-chain create event does not carry the dev's buy size and no join to recover it exists yet. This pre-registration restricts its primary claim to G1 for that reason.

## 8. Cost

Compute only. No new Helius credits in this PR (no code changes to any listener or backfill job). The future G1 adapter (§7) reads already-sealed backfill rows — zero new RPC calls. The future G3 join (§7), if built, would reuse the backfill's already-decoded `trades` sink from the same `getBlock` fetches — also zero new credits — but it is explicitly not being built in this PR.

## 9. Not in this test

- The value of k (fixed procedure only, §4).
- Any new backfill run or RPC lookback for the funding graph.
- Any evaluate/runner code change or forward-paper wiring.
- A different priority, tip, route, exit, or fill bound than §2.
- A refit of the pressure intercept.
- Any promotion. Any claim that a gated book made money.

---

## Sources

- [ARTIFACTS/lab/migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md) — frozen base-cell execution
- [ARTIFACTS/lab/migrate-direct-oos.md](../ARTIFACTS/lab/migrate-direct-oos.md) — frozen cell's existing OOS ranges (disjointness check, §5)
- [ARTIFACTS/lab/fee-audit-2026-09-27.md](../ARTIFACTS/lab/fee-audit-2026-09-27.md) — venue-fee and miss-rate figures cited in the parent context
- [ARTIFACTS/lab/latency-curve-2026-09-27.md](../ARTIFACTS/lab/latency-curve-2026-09-27.md) — funding-graph `first_seen_ms` causality note (§8)
- [EXP-004-graph-creator-recurrence-v0.md](EXP-004-graph-creator-recurrence-v0.md) — H-G1/H-G3 `DIRECTIONAL_NON_KILL` source, `tools/exp004_graph_discovery.py` definitions
- `tools/exp004_graph_discovery.py`, `tools/exp002_paper_runner.py`, `tools/pump_history_backfill.py`, `tools/funding_graph.py`, `tools/forward_paper.py` — code read for §7/§8
- `LAB_STATE.md` — clean clock, kill review, backfill split, winner's-curse note
