# EXP-009 — Migrate creator-recurrence gate, pre-registration (holdout = unsealed fast hours)

**This file is written before any holdout data exists on disk.** It is a pre-registration only: no cell has been run, no threshold has been chosen, no result is reported. Declared (UTC): **2026-09-28T17:54:10Z**.

**Amendment (2026-09-28, manager review of PR #130):** redefines G1 with a fixed 24h trailing window instead of unbounded lookback (§3), adds a burn-in eligibility check and an unknown-creator exclusion applied to every cell including `gate_off` (§4), and extends the k threshold procedure to use the same eligible population (§4c, §5). No data was read to make this amendment; it only tightens the causality rule already declared.

**Amendment 2 (2026-09-28T18:05:51Z — commit time of the amendment; originally mis-stamped 19:20:00Z, corrected in a follow-up PR — manager review, direction lock, owner review of PR #130):** the original hypothesis text (below) described repeat/linked creators as lower-quality and the gate as skipping them, while every cell defined in §5 does the opposite — each one *enters* on recurrence (`G1_only`: `prior_mint_count_24h >= k`; `G3_only`/`G1_and_G3`: recurrence true) and skips the rest. That was a self-contradiction, not a design choice. This amendment locks the direction to match the cells already defined: serial/linked creators are the positive arm this experiment tests, on the only prior evidence available (EXP-004 H-G1/H-G3 `DIRECTIONAL_NON_KILL`, §"Prior evidence strength" below). The hypothesis and this amendment replace the earlier "skip low-quality repeat creators" framing everywhere it appeared in this file. No cell definition in §5 changed — only the prose explaining them. No k had been computed and no holdout hour had been read at the time of this amendment.

| Field | Value |
| --- | --- |
| **ID** | `EXP-009-migrate-creator-gate-prereg` |
| **Status** | **planned** — pre-registration; not run |
| **Owner seat** | Graph (measurement). `quant-proof` reviews before any sentence claims the gated book made money. |
| **Started** | 2026-09-28 |
| **Parent context** | Frozen migrate-direct cell ([ARTIFACTS/lab/migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md), locked 2026-09-27T13:06:36Z) is failing out of sample under both fail models. Venue fees on a filled round trip run ~2.435–2.511% ([ARTIFACTS/lab/fee-audit-2026-09-27.md](../ARTIFACTS/lab/fee-audit-2026-09-27.md) §1, §"Rescore at the slot-+1 priority"). 59.65% of attempts miss (same fee audit, §1 table). EXP-004 ([EXP-004-graph-creator-recurrence-v0.md](EXP-004-graph-creator-recurrence-v0.md)) found H-G1 (creator prior-mint count) and H-G3 (weak creator↔buyer recurrence) `DIRECTIONAL_NON_KILL` at 60s, both days scored, and explicitly not scored lift proof. |
| **Hypothesis** | Serial or linked creators — repeat mints within a fixed trailing window, or a creator whose own buy pattern recurs across their mints — are the **positive** arm, not the arm to avoid. **Mechanism:** this direction comes from EXP-004's H-G1 and H-G3 positive arms (`prior_mint_count > 0`, and weak creator↔buyer recurrence), both `DIRECTIONAL_NON_KILL` at the 60s priced floor on 2026-09-20 and 2026-09-21 — the only prior evidence available for this feature family (see "Prior evidence strength" in §1). A gate that enters **only** on migrates from those recurring creators (G1: `prior_mint_count_24h(creator, T) >= k`; G3: `creator_buyer_recurrence_weak`) is expected to raise gross per *filled* trade above the unfiltered `gate_off` population enough to clear the venue-fee drag the ungated frozen cell is currently failing under, on both the flat-15% and pressure-scale-1 fail models. This is not a claim that repeat or linked creators are lower quality, and no cell in §5 tests the direction of skipping them — see the direction-lock note under §5. |
| **Kill condition** | The primary cell (G1_only, §5) does not clear the unchanged promotion gate (§7) on the declared, burn-in-eligible holdout (§6) under **both** fail models, **or** it clears only one of the two models, **or** its gross per filled trade is not above `gate_off`'s on the same §4-eligible population, **or** it is the single best of the 4 cells and the multiple-comparison guard in §7 is not honored. Any of those kills the creator-recurrence gate for `migrate`. A single positive cell is not sufficient by itself — see the winner's-curse note already on record for the frozen cell (`LAB_STATE.md` §"First positive run"). Given the prior evidence is weak (§1), a null or negative holdout result is the expected base case, not a surprise. |
| **Method** | Offline replay of the frozen migrate-direct execution (§2) with an added entry filter (§3–§5) on sealed backfill rows, restricted to the burn-in-eligible population (§4). No RPC calls beyond what the backward backfill has already made. No evaluate/runner code change. No new Helius credits (§9). |
| **As-of-T** | Constitution rule 3 (knowable-at-T). T is the migrate decision time — the same receive-clock substitution the frozen cell uses when `t_recv_ms` is missing ([migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md) §Windows). The creator gate feature must be computable from data sealed strictly before that T, over a bounded and fully-covered trailing window (§3–§4); see the feasibility finding in §8. |
| **Regime labels** | Unchanged from the base cell — the gate does not stratify by `regime_id`; every row still carries its regime tag at ingest (Constitution rule 4). |
| **Windows** | Not the EXP-004 1s/5s/15s/30s/60s Discovery grid. The outcome horizon is the base trade's own exit: `tp50_sl30` (first of +50% / −30% / 30-minute cap), unchanged from the frozen cell. Not to be confused with the new 24h creator-history window (§3), which is an eligibility window, not an outcome horizon. |
| **Fail models** | Flat 15% on sends, and the pressure curve at scale 1 (frozen fit, not refit on this test set). Both are gates; scale 2 is reported only, per the frozen cell's own rule. |
| **Promotion gate** | Unchanged: ≥100 OOS trades, ≥5 distinct UTC days with a majority positive, lower 90% CI bound of mean SOL/trade > 0 (bootstrap 1,000 draws, seed 1, 5th percentile), total SOL still positive after removing the top 3 trades. Under **both** fail models. |
| **Result** | Not run. Pre-registration only. |
| **Conclusion** | Not run. |

---

## 1. Scope

One new degree of freedom versus the frozen cell: **whether the migrate is taken at all**, decided by a creator-history gate evaluated at the migrate decision time over a fixed trailing window (§3), restricted to rows where that window is fully known (§4). Nothing else about execution changes (§2). At most 4 cells (§5). No evaluate/runner code changes. No live/forward-paper wiring in this PR.

### Prior evidence strength (weak)

The direction locked in §5's cells rests on EXP-004 alone, and that evidence is **weak**, not lift proof:

- EXP-004's own stamp for H-G1/H-G3 is `DIRECTIONAL_NON_KILL` — soft watch, explicitly recorded as "not scored lift proof or Discovery promotion" ([EXP-004-graph-creator-recurrence-v0.md](EXP-004-graph-creator-recurrence-v0.md) line 25). It cleared `separable_vs_spine` and `no_lift_vs_random`, two kill-avoidance gates, not a promotion gate.
- The priced population behind that stamp is thin: `priced_60s_n` = 102 on 2026-09-20 and 122 on 2026-09-21, out of population_n in the thousands ([EXP-004-graph-creator-recurrence-v0.md](EXP-004-graph-creator-recurrence-v0.md) lines 93–94).
- It is a **different trigger and horizon** than this experiment: EXP-004 marks off the `create` broadcast at a 60-second horizon, not the `migrate` trigger at `tp50_sl30` this file uses (§2, §Windows above).
- The one attempt to sharpen the same prior-mint feature into an ordinal signal, **EXP-004b**, was **killed cross-day**: `bucket_3plus` (the closest ordinal analog to a high-recurrence G1 gate) was soft on 2026-09-20 only and failed the spine comparison on 2026-09-21, so the NH-G1a falsifier fired and the ordinal lane was killed, not promoted ([EXP-004b-nh-g1a-ordinal-prior-mint-v0.md](EXP-004b-nh-g1a-ordinal-prior-mint-v0.md) lines 98–115). The novel-creator arm, `bucket_0`, also failed on both days.

Net: G1 is not proven lift on any prior test, cross-day replication of the closest ordinal version already failed, and the trigger/horizon here differ from every prior test of the feature. A null or negative result on this holdout is the expected base case, not evidence against the pipeline — see the kill condition above.

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

Both features below are named directly from `tools/exp004_graph_discovery.py` (EXP-004's offline precompute), which is the only place in the repo that currently implements anything close to them. Neither uses `funding_graph.py` or any RPC call. The feasibility of running that code, or an equivalent, against backward-backfilled hours is the subject of §8 and is **not assumed** here.

- **G1 — creator prior-mint count, fixed 24h trailing window.** `prior_mint_count_24h(creator, T)` = the number of that creator's bonding-create events with create time in the half-open trailing window **`[T − W, T)`, W = 24 hours (86,400 seconds), fixed now, not tuned**. This **supersedes** the original declaration (which counted all history before T with no window). Unbounded lookback biases the gate: the holdout sits at the oldest edge of the fast backfill, where coverage behind any given T is thin or absent, so almost every creator would look artificially "novel" there, while in-sample (2026-09-22T10–2026-09-25T06) sits behind days of preceding sealed coverage. A fixed window removes that asymmetry — both in-sample and holdout measure the *same* 24h of history, not however much happens to be on disk. The natural implementation extends `_prune_lookback` (`tools/exp004_graph_discovery.py` lines 172–174), which already implements exactly this kind of trailing-window prune for G2's `burst_count`, to `prior_mint_count` instead of leaving it unbounded (`compute_graph_features_for_row`/`_update_creator_state`, lines 177–257, would need a companion windowed counter). For a `migrate` entry, creator identity = the creator recorded on that mint's own create event (looked up by mint); T = the migrate decision time (§ As-of-T above), not the create time. Only creates sealed strictly before T, inside `[T-W, T)`, are counted — no RPC, no live state carried across restarts, no data from T or later.
- **G3 — weak creator↔buyer recurrence.** `creator_buyer_recurrence_weak` = true iff `prior_mint_count(creator, T) > 0` (unbounded, as EXP-004 defines it — **not** windowed by W in this amendment; §8 shows it is currently infeasible on backward hours regardless of window choice, so narrowing its lookback does not change its status here) **and** this create's `(initialBuy, solAmount)` pair matches a pair already recorded on one of that creator's own prior creates (`tools/exp004_graph_discovery.py::_buy_pattern`, `_select_hg3_positive`, lines 168–169, 437–443). It is a boolean, no threshold to tune.

`tools/forward_paper.py`'s live `WalletState.by_creator` (keyed on `create.creator`, lines 1288, 1334–1352, consumed by `funding_graph.fill_funding_features` and `laya_v0.creator_features`) is a third, independent implementation of "creator history" and is **not** used by this experiment — it resets to empty on every forward-paper process restart (`LAB_STATE.md` §"Manager update (2026-09-28)"), so it is left-censored on the live runner, not a source for a backward-hour holdout.

## 4. Eligibility: burn-in and unknown creator

A migrate row is **eligible** for scoring in **every** cell — including `gate_off` — only if both (a) and (b) below hold. A row that fails either check is excluded from all four cells, not only the gated ones, so `gate_off` is scored on exactly the same population as `G1_only`/`G3_only`/`G1_and_G3`; the comparison stays apples to apples and `gate_off`'s own n is not the frozen cell's original n.

**(a) Unknown creator.** If the migrating mint's own create event is not present in our data — created before sealed coverage began, or otherwise missing — the creator is unknown and the row is excluded from every cell. No cell may substitute a default or zero `prior_mint_count_24h` for an unknown creator: "creator unknown" and "creator known, 24h count = 0" are different states and must not be conflated. A row with a known creator and a true zero in the last 24h remains eligible and is gated normally (it lands in `G1_only`'s skip side unless `k = 0`).

**(b) Burn-in: continuous 24h coverage.** A row is eligible only if continuous create coverage exists over the **entire** trailing window `[T − W, T)`, W = 24h. "Continuous" means every UTC hour in that window has a sealed, non-partial creates file on whichever host supplies it. `tools/pump_history_backfill.py`'s own checkpoint already carries this per-hour marker: `checkpoint["hours"][key]["status"]` is `"sealed"` or `"partial"` (lines ~1000–1125), and the fast-box/Oracle split in `LAB_STATE.md` §"Backfill split" already reports hours this way ("2026-09-21T23 sealed... 2026-09-21T22 in progress"). The scorer must walk every hour boundary in `[T-W, T)`, confirm each one is present with `status == "sealed"` (not missing, not `"partial"`) on the host that covers it, and confirm the covered hours are contiguous with no gap where responsibility switches between Oracle and the fast box. A single missing or partial hour anywhere in the window fails burn-in and excludes the row — it is **not** treated as `gate_off`-eligible with an unverified gate.

  **In-sample (Oracle window, 2026-09-22T10:00:00Z–2026-09-25T06:58:00Z).** The trailing window for the earliest in-sample row (T = 2026-09-22T10:00:00Z) reaches back to 2026-09-21T10:00:00Z. Coverage is Oracle sealed hours from 2026-09-22T00:00Z onward, plus fast-box sealed hours 2026-09-21T23 and older for the part of the window before 2026-09-22T00:00Z — both ranges are already sealed and already form the frozen cell's own OOS boundaries ([migrate-direct-oos.md](../ARTIFACTS/lab/migrate-direct-oos.md)). That makes burn-in plausible for the whole in-sample window, but this file does **not** assume it verified: the later scoring PR must walk hour-by-hour from 2026-09-21T10:00:00Z through 2026-09-25T06:58:00Z, on whichever host (Oracle or fast) covers each hour, and confirm every one shows `sealed`, not `partial` or missing, before computing k or scoring `gate_off`/`G1_only` in-sample.

  **Holdout (fast-box, strictly older than 2026-09-19T01:00:00Z).** Because the fast box only ever adds older hours as it walks backward, the trailing window is not fully covered near the current sealed floor. **Holdout eligibility therefore begins at (oldest sealed fast hour at the time of scoring) + 24h**, and that boundary moves further back in time as the backfill continues — it mirrors the backfill's own growth rather than being fixed today. A holdout row with `T − W` older than the oldest sealed fast hour at scoring time is not yet eligible and is excluded as a burn-in failure, never scored as if the gate passed. The later scoring PR must record, at scoring time, what the oldest sealed fast hour was and therefore what the earliest eligible holdout T was that run.

**(c) k's threshold procedure inherits the same rule.** The in-sample median used to fix k (§5) is computed **only** over rows that pass both (a) and (b) above for the in-sample window — the same unknown-creator exclusion and the same 24h burn-in check, not a separate or looser rule. An in-sample row that fails burn-in or has an unknown creator contributes nothing to the median.

**Reporting.** The scoring PR (a later PR, not this one) must report, per cell and separately for the in-sample k-computation population and the holdout scoring population: the count of rows excluded for unknown creator, and the count excluded for burn-in failure. Those two counts are not interchangeable and must not be merged into one "excluded" number.

## 5. Cells (at most 4)

| Cell | Rule | Role |
| --- | --- | --- |
| `gate_off` | No filter, scored only over the eligible population from §4. | Reference only, not a new claim — but its n is the §4-filtered n, not the frozen cell's original n. |
| `G1_only` | Enter iff `prior_mint_count_24h(creator, T) >= k`, else skip the migrate. Only evaluated on §4-eligible rows. | **Primary** (the only cell provably causal on this holdout at declaration time — see §8). |
| `G3_only` | Enter iff `creator_buyer_recurrence_weak` is true, else skip. Only evaluated on §4-eligible rows. | Secondary, **contingent** on §8 — see §7. |
| `G1_and_G3` | Enter iff both `G1_only` and `G3_only` conditions hold. Only evaluated on §4-eligible rows. | Secondary, **contingent** on §8 — see §7. |

**Direction locked 2026-09-28 (manager, after owner review): gated cells ENTER on serial/linked creators; no cell in this experiment tests the skip direction. Flipping direction after k is computed or after any holdout row is read is forbidden.**

### Threshold procedure for k (fixed here; the value is not chosen in this file)

`k` is chosen **only** from the Oracle in-sample hours **2026-09-22T10:00:00Z through 2026-09-25T06:58:00Z** — the same in-sample window as the frozen cell's own 972-cell selection grid ([migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md) §Windows) — and only over rows eligible under §4. No data outside that window, in-sample or holdout, is read to set k.

The fixed procedure: **k = the median value of `prior_mint_count_24h(creator, T)` over all §4-eligible `migrate`-triggered rows in that in-sample window.** Ties round down (a fractional median rounds to the next lower integer). If that in-sample median is 0 — i.e. most eligible creators in-sample have no create in their own trailing 24h — k is instead fixed at 1 (first repeat within 24h), and that substitution is recorded, not re-tuned, in the later PR that computes it. This file fixes the rule; a later, separate PR verifies burn-in (§4b), computes and reports k, and reports the exclusion counts (§4, "Reporting"), before the holdout in §6 is read.

## 6. Holdout

**Primary holdout: fast-box backfill hours strictly older than 2026-09-19T01:00:00Z, further restricted by the burn-in floor in §4(b).**

- Declaration timestamp (now, UTC): **2026-09-28T17:54:10Z**.
- Current oldest sealed hour at declaration time: **2026-09-19T01 in progress** (not yet sealed) — per the fast-box backward walk, which proceeds from 2026-09-21T23Z backward (`LAB_STATE.md` §"Backfill split"). This file does not read the backfill manifest or any hour file to confirm that boundary; the point of a pre-registration is to fix the cut before the data behind it exists.
- Expected extent: the fast run stops at its +2,000,000-credit cap, expected to bind around 2026-09-29 18:00–19:00Z (`LAB_STATE.md` §"Manager update (2026-09-28)"), projected to reach down to roughly 2026-09-16.
- Disjointness from the frozen cell's existing OOS book: the frozen cell already used fast hours **2026-09-19T02 → 2026-09-21T23** and Oracle hours **2026-09-22T00–10** as its own out-of-sample book. Every hour in those two ranges is newer than the **2026-09-19T01:00:00Z** cut used here, so that cut already keeps this holdout disjoint from that OOS book — no separate exclusion step is required.
- Burn-in floor (§4b): the raw cut is 2026-09-19T01:00:00Z, but a row is not actually eligible until its full trailing window `[T-24h, T)` is sealed. At any given scoring time, the earliest eligible holdout T is `(oldest sealed fast hour at that time) + 24h`, which is older than 2026-09-19T01:00:00Z once the backfill has walked far enough past it — this file does not compute that value now; it only fixes the rule the later scoring PR must apply.
- This holdout does not exist yet at every hour; it fills in as the backfill walks backward. Scoring against it happens only after those hours are sealed and the §4 checks pass, in a later PR.

**Secondary holdout (optional, note only): forward paper from the clean clock.** If a creator-gated book is later added to forward paper (a separate PR — not this one, and not proposed here), its rows from the clean clock **2026-09-28T00:00:00Z** onward would be a second, fully causal holdout (real-time decisions, no backfill-causality or burn-in question of the kind in §4/§8). Out of scope for this pre-registration; noted only so a later PR does not have to re-derive it.

## 7. Scoring and multiple comparisons

Score each cell under the unchanged promotion gate (top of this file), under both the flat-15% and pressure-scale-1 fail models, over the §4-eligible population only, exactly as the frozen cell is scored otherwise.

Four cells are declared, but only **one** is eligible to be read as the primary result: **`G1_only`**, because it is the only cell shown causal on backward hours in §8 at the time of this declaration. `gate_off` is not a new claim — it reproduces the frozen cell's execution, on the §4-eligible population, and is reported for reference only. `G3_only` and `G1_and_G3` are secondary and **contingent**: per §8, the feature they depend on cannot currently be computed causally on backward hours, so they must not be used to declare success on this holdout, even if a later ad hoc computation happens to score them well, until the join described in §8 exists, is itself proven causal, and is pre-registered on its own terms. `G1_only` is declared as the single primary cell **in-sample, before the holdout in §6 is read** — the same winner's-curse guard already applied to the frozen cell's own selection (`LAB_STATE.md` §"First positive run").

The scoring PR must report the per-cell exclusion counts required by §4 ("Reporting") alongside every result table — a cell's n is not comparable across cells or to the frozen cell's original n without them.

## 8. Data dependency — feasibility of causal creator features on backward hours

This is the key feasibility check for this pre-registration, done by reading code, not by reading any data. The 24h-window amendment (§3) does not change this finding — it changes what is counted, not whether the underlying source can be read causally.

- `tools/exp004_graph_discovery.py` computes G1 and G3 from the **live PumpPortal WS `ingest_hot` schema only**. `is_bonding_create` requires `row["type"] == "ingest_hot"` plus `txType`/`stage` fields (`tools/exp002_paper_runner.py` lines 101–112), and the creator identity and buy pattern come from `traderPublicKey`, `initialBuy`, `solAmount` (`tools/exp004_graph_discovery.py` lines 161–169, via `_sealed_field`/`_sealed_float`). None of that is RPC-derived — it is read straight off the sealed create broadcast — so in principle it is knowable-at-T for any hour where that broadcast was sealed live.
- The backward backfill (`tools/pump_history_backfill.py`) does **not** use that schema. It reconstructs creates directly from on-chain `CreateEvent` logs via `decode_create_event` (lines 198–247), producing its own schema: `type="create"`, `mint`, `trader`, `creator`, `event_ts`, with `signature`/`slot`/`block_time`/`tx_index` stamped by `rows_from_block` (lines 383–444), and a per-hour `sealed`/`partial` checkpoint status (lines ~1000–1125) — the same status field §4(b)'s burn-in check reads. The on-chain `CreateEvent` carries the creator pubkey and mint but **no dev-buy amount** — there is no `initialBuy`/`solAmount` field decoded there.
- **G1 (now `prior_mint_count_24h`) is causally computable on backward hours**, but only via a new adapter that reimplements the same windowed counting logic against the backfill's `creator`/`event_ts` fields instead of `traderPublicKey`/`t_ws`, and that also exposes the per-hour `sealed`/`partial` status needed for §4(b)'s burn-in check. That adapter does not exist yet; writing and testing it is a separate implementation PR, not this pre-registration. It needs zero new Helius credits — it only re-reads rows and checkpoint status the backfill has already sealed via `getBlock`.
- **G3 is not currently computable on backward hours**, windowed or not. The feature needs the creator's own buy size on each of their creates, and `decode_create_event` does not carry it — the dev's first buy is a separate instruction in the same transaction, decoded into the backfill's `trades` sink, not the `creates` sink. Recovering it would need a same-transaction join between `creates` and `trades` (by `signature`/`tx_index`, both stamped on both sinks) that is not implemented anywhere in the tree today. Until that join exists and is itself shown causal (built only from data at or before T, no RPC call made after the fact), G3 cannot be scored on backward hours. This is exactly why §5/§7 restrict the primary cell to G1 only.
- This is a **different** failure mode from the funding-graph problem already on record. `tools/funding_graph.py` resolves wallets via live public-RPC/Helius calls at the time the graph-building process runs (module docstring, `tools/funding_graph.py` lines 9–11) and stamps `first_seen_ms` at resolution time, not at historical T — which is exactly why `skip_fresh_rug` was a no-op on the 972-cell grid: "the funding graph's earliest `first_seen_ms` is after the holdout cut, so unknown does not skip" (`ARTIFACTS/lab/latency-curve-2026-09-27.md` line 30). G1/G3 do not depend on `funding_graph.py` or any RPC call beyond the backfill's own `getBlock`s, so they do not inherit that specific problem — but G3 has its own, separate causal gap (missing dev-buy field on-chain in `CreateEvent`), documented above.

**Plain statement:** creator prior-mint count in a fixed 24h window (G1) can be computed causally for the backward hours in §6, from the create stream itself, once a schema adapter is written that also exposes per-hour seal status for burn-in. Weak creator↔buyer recurrence (G3) currently cannot, because the on-chain create event does not carry the dev's buy size and no join to recover it exists yet. This pre-registration restricts its primary claim to G1 for that reason.

## 9. Cost

Compute only. No new Helius credits in this PR (no code changes to any listener or backfill job). The future G1 adapter (§8) reads already-sealed backfill rows and their existing `sealed`/`partial` checkpoint status — zero new RPC calls. The future G3 join (§8), if built, would reuse the backfill's already-decoded `trades` sink from the same `getBlock` fetches — also zero new credits — but it is explicitly not being built in this PR.

## 10. Not in this test

- The value of k (fixed procedure only, §5).
- Any new backfill run or RPC lookback for the funding graph.
- Any evaluate/runner code change or forward-paper wiring.
- A different priority, tip, route, exit, or fill bound than §2.
- A refit of the pressure intercept.
- A different W than 24h, or a windowed G3.
- Any promotion. Any claim that a gated book made money.

---

## Sources

- [ARTIFACTS/lab/migrate-direct-prereg.md](../ARTIFACTS/lab/migrate-direct-prereg.md) — frozen base-cell execution
- [ARTIFACTS/lab/migrate-direct-oos.md](../ARTIFACTS/lab/migrate-direct-oos.md) — frozen cell's existing OOS ranges (disjointness check, §6)
- [ARTIFACTS/lab/fee-audit-2026-09-27.md](../ARTIFACTS/lab/fee-audit-2026-09-27.md) — venue-fee and miss-rate figures cited in the parent context
- [ARTIFACTS/lab/latency-curve-2026-09-27.md](../ARTIFACTS/lab/latency-curve-2026-09-27.md) — funding-graph `first_seen_ms` causality note (§8)
- [EXP-004-graph-creator-recurrence-v0.md](EXP-004-graph-creator-recurrence-v0.md) — H-G1/H-G3 `DIRECTIONAL_NON_KILL` source, `tools/exp004_graph_discovery.py` definitions
- `tools/exp004_graph_discovery.py`, `tools/exp002_paper_runner.py`, `tools/pump_history_backfill.py`, `tools/funding_graph.py`, `tools/forward_paper.py` — code read for §3/§4/§8
- `LAB_STATE.md` — clean clock, kill review, backfill split, winner's-curse note
