# EXP-024 Part 1: H5-BOOSTFLOOR forward read, pre-registration

**Written and merged before the first counted hour.** Written 2026-10-08. To write it, no row of a sealed block (fresh-0802, fresh-0808, fresh-0828) was opened, and neither was any forward-1002 or forward-1016 file, any forward-paper or runner output, or any key. Its inputs are RULE/REPORT/VERIFY under `/data/mal/hunt-1008/h5-flows/`, the hunt-1 judge (`/data/mal/hunt-1008/JUDGE.md`), the Track A draft, the synthesis judge's plan (`PLAN.md`), the live plan, the robustness study, the repo docs, and exploration-grade power simulations on September rows that were already read (section 15). This file is docs only. The read tool, the extractor and the live canary build are separate PRs (sections 12 and 18). Nothing in this file says or implies that any book is positive. **A fail is the likely outcome** (section 15).

| Field | Value |
| --- | --- |
| **ID** | `EXP-024-h5-boostfloor-part1-prereg` |
| **Status** | **planned**. Counting starts at 2026-10-10T00 (section 0). |
| **Declared (UTC)** | 2026-10-08. It must merge before 2026-10-10T00:00Z. |
| **Parent** | hunt-1008 / h5-flows: `RULE.md` (sha256 `c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c`), `REPORT.md`, `VERIFY.md` (adversarial verifier: not refuted). `/data/mal/hunt-1008/JUDGE.md` (ranks H5 first of the hunt-1 survivors). Repo port: draft PR #476 (`tools/boostfloor_score.py`, head `a9e23be`). |
| **Rules** | [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md) (ledger, Holm, block budget, family lineage), [DEC-016](../DEC/DEC-016-exp012-forward-on-chain-hours.md) (forward-1002, seal, FINAL; Am.7), [DEC-017](../DEC/DEC-017-forward-secondary-family.md) §2, [DEC-021](../DEC/DEC-021-champion-challenger.md) §8, Am.1 and Am.2, [DEC-023](../DEC/DEC-023-h5-family.md) (this family), [DEC-024](../DEC/DEC-024-h5-live-canary.md) (the measurement canary), [EXP-022](EXP-022-cap-pick-part1-prereg.md) §9 and Amendment 2, [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md). |
| **Hypothesis** | The frozen H5-BOOSTFLOOR v1 book has mean SOL per trade > 0 at 0.1 SOL on post-upgrade October chain hours, under both fail models, at a **1.9 s entry** and a 0.55 s exit lag, after the live−sim correction. It must also stay positive, with a positive ex-top-3 total, at a 3 s entry with a 1.35 s exit lag, and with a 15% min_out guard (section 7). |
| **Kill condition** | No look passes (section 7); or Look 1 futility (section 8.3); or a structure halt (section 11); or a precondition fails (section 10). Two pre-registered looks, no retune, no re-read. |
| **Expected outcome** | **FAIL is likely.** The judge's estimate is P(Look 1 pass) ≈ 0.0265 and P(either look) ≈ 0.1078 (section 15). |
| **Tools** | **Read:** a sealed forward read mode built on #476, plus a forward extractor (section 12). **Halt:** `tools/pump_structure_monitor.py` (A3, shared with EXP-022). **Calibration:** E1, the EXP-022 §8 run of `tools/probe_sim_calibration.py`. |

**Labels.** [measured] is copied from a cited file that computed it. Exploration arithmetic is measured arithmetic, not evidence of edge. [inferred] is reasoned. [pinned] is a design value this file fixes; it is not evidence. [est] is an estimate.

## 0. Counting start (the one pinned line)

```
EXP024_COUNT_START: 2026-10-10T00
```

- **Format.** The line matches `^EXP024_COUNT_START: 2026-10-10T00$` exactly once. The read tool refuses unless it does, and unless this file is clean against HEAD. The value never changes after merge.
- **Why 10-10T00.** A counted window must be fixed before its first hour begins in real time. That is ledger rule 4 and the Forward walk 2 row; the audit used it to reject forward-1002 `[10-03, 10-10)` for CAP-PICK (SYNTHESIS D3). This file can merge at the earliest during 10-09. So the first legitimate full UTC day is 10-10.
- **Not counted:**
  - `[2026-10-02T15, 2026-10-06T00)`: EXP-012's buffer. It is already sealed.
  - `[2026-10-06T00, 2026-10-10T00)`: these hours elapsed before this file existed. 10-05..10-07 also carry the recorded DEC-016 Am.2 seal exposure, and 10-09 holds the forecast 200 ms step.
  - None of these hours is read for H5, not even as report-only lines.
- **Deadline.** This file and every section 17 item marked "before 10-10T00Z" must merge before 2026-10-10T00:00Z, with quant-proof OK on the final heads. Otherwise:
  - this pinned start is void;
  - a re-filed Part 1 may pin `2026-10-11T00`, if it merges before that instant. Look 1 then has 5 dates and lower power (section 15). The live build pauses (DEC-024);
  - after 2026-10-11T00:00Z, Look 1 is dropped. The family can then use only Look 2 hours, and only through a new filing.

## 1. Family status (DEC-014, DEC-023)

- **A new family, not EXP-012 or CAP-PICK lineage.** Under the DEC-014 clarification (2026-10-07), a family is a primary hypothesis, not a code lineage. [DEC-023](../DEC/DEC-023-h5-family.md) records this decision.
  - **H5's hypothesis:** a depth trigger inside the BOOST window is positive. The trigger is pool Q at most 40 SOL incl. V, within 0–300 s, with BOOST budget left, and the trade is held to 330 s.
  - **H5 uses new information** that no earlier book used: pool depth against the V floor, and BOOST spend at the trigger print.
  - **No EXP-012 component:** H5 has no EXP-012 model, threshold, feature set or pick list.
  - **Not a retune of CAP-PICK.** CAP-PICK's numeric parameters (cap, guard, size, priority, k) are not what H5 changes. H5 removes the selector and enters on a different event.
- **What H5 shares:**
  - the BOOST-era universe (V in [17.5, 17.7] SOL), with CAP-PICK and with hunt H4 (QUIET-BOOST);
  - the BOOST mechanism. A single `toggle_boost` ends CAP-PICK, H4 and H5 (JUDGE.md:24).
- **What sharing means here:**
  - Outcomes are positively correlated, so Bonferroni across the H5 and EXP-022 families is conservative, not anti-conservative.
  - H5 is a separate family with its own α (section 9). It is not an arm of the walk-2 family.
- **Not a paired test.** The clarification's paired-difference requirement applies to a new component added to a base book that claims a sealed block. H5 has no base book and claims no sealed block.
- **k = 1.** H4 (verifier: weak), H2 and G4 are **not** registered here. No arm is added after merge.

## 2. The frozen rule, copied exactly

Source: `/data/mal/hunt-1008/h5-flows/RULE.md`. Its sha256 is `c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c`, re-checked 2026-10-08T19:51Z. The block below is the file byte for byte (check: `sed -n '/^~~~rule$/,/^~~~$/p' EXP/EXP-024-h5-boostfloor-part1-prereg.md | sed '1d;$d' | sha256sum`).

~~~rule
# RULE H5-BOOSTFLOOR v1 (frozen 2026-10-08, before any confirmation data was read)

Thesis H5 (predictable flows). The pump.fun BOOST agent is a price-insensitive TWAP buyer: after every non-mayhem graduation it spends
exactly 17.585 SOL in ~29 slices over ~345 s, whatever the price. PumpSwap price = (real quote + V) / base with V = 17.58 SOL, so sells
cannot push Q = real quote + V below V. Rule: when early dumping has drained the pool close to its V floor while BOOST still has budget,
buy and hold to just before BOOST's last slice.

## Universe (decision-time information only)
- Every `complete` graduation whose canonical PumpSwap pool has V in [17.5, 17.7] SOL (non-mayhem, BOOST era). Canonical pool and V as in
  /data/mal/audit-1008/work/g_reachable_cap_book_rescore/meta/<day>.parquet (first V-range pool after `complete`); paths from
  .../paths/<day>.parquet (PumpSwap PRE-trade reserves, ordered slot, tx_index, event_index). The graduation precedes every decision; no
  future event defines the universe.
- s0 = slot of the canonical pool's first print. sps = seconds per slot of that pool, from block_time over its path (must be in (0.15, 0.6)).

## BOOST tracking
- BOOST wallet = the buy-only wallet with >= 3 buys, each 0.2..2.0 SOL, total <= 17.7 SOL, in the pool's first 1,600 slots (most buys wins).
  Live equivalent: the per-pool BOOST vault PDA. Completion of the 17.585 SOL budget is NOT required (an early stop counts against the rule).
- spent(i) = cumulative BOOST buy SOL up to and including print i.

## Trigger (one trade per pool, first qualifying print)
- print i is a sell (non-BOOST by construction), t_i = (slot_i - s0) * sps in [0, 300] s,
- post-trade Q_i = real quote + V <= 40 SOL (post-trade state of print i = pre-trade state of print i+1),
- spent(i) < 0.999 * 17.585 SOL.

## Entry
- Landing slot X = slot_i + ceil(1.3 s / sps) (primary leg) or ceil(1.9 s / sps) (binding leg). END bound: pool state after every print
  in slots <= X. Buy size S in {0.1, 0.25} SOL; net = S * (1 - f), f = canonical PumpSwap tier (tools/paper_curve_math.PUMPSWAP_SOL_FEE_TIERS)
  on the landing state's market cap; tokens = B * net / (Q + net). Our own impact is applied (constant product on Q incl. V).

## Exit
- Exit trigger at slot s0 + round(330 s / sps) (just before BOOST's last slice and the documented post-BOOST cliff). The sell lands
  ceil(0.55 s / sps) slots later, END bound; our buy stays in the pool until we sell; proceeds = tokens * Q' / (B' + tokens) * (1 - tier fee).

## Costs and legs
- 55,000 lamports priority per send, on the buy and on the sell.
- Flat fail: pnl_flat = 0.85 * pnl + 0.15 * (-55,000 lamports).
- Pressure fail: tools/latency_curve.fit_curve (slopes 0.8 / 0.35 at scale 1, intercept refit so mean p = 0.289 on this book's sends) on
  (buys in the landing slot, buy lamports in the 2 s up to and including the landing slot); pnl_press = (1-p) * pnl + p * (-55,000).
- Gate statistics: common.gate (date-cluster bootstrap, 1,000 day resamples, seed 1, 5th/95th percentiles).

## Code
- s14_boostdip.py run with env DS=40 TMIN=0 TMAX=300 TEND=330 NOBOOSTREQ=0 (MODE unset); the rule's exit is column H == 'end'.
- s15_score_dip.py scores it. Discovery numbers that motivated the rule: out/s15_dip_v2_disc_grid.txt and the full-leg rows in REPORT.md.

## Parameters chosen on discovery (explore-0814 only)
- Q* = 40 SOL from {40, 50, 60} (monotone: smaller Q* better); exit 'end' (330 s after first print) from {15, 30, 60, 120 s after landing, end};
  trigger window [0, 300] s fixed a priori; BOOST remaining check fixed a priori.
~~~

**Frozen code** (sha256, re-checked 2026-10-08T19:51Z):
- `s14_boostdip.py`: `70becfb7d7e48b8e24cee9ae807db29200db9990cf96d18ab2dce755301c1ad0`
- `s15_score_dip.py`: `b91febe61613c2e94792c627bd5bc0cbe54b52f6a7beb874f7af8ed9f3410e89`
- `common.py`: `8061d4c3ebd57d0aee75c0356a23f9048b3272624547670cdf8604a273fb56d9`

**Reference implementations of the two added binding legs** (exploration scripts in the same directory; they implement the 3 s entry and the 15% guard at the rule's primary leg, and are cited only as the reference for the definitions in section 5):
- `s19_latency.py`: `e185198dc479a703ad96beec3e7c924dd9ddf6159da7d50c8d846facc386a473`
- `s18_capguard.py`: `83b26f4f8b33daeb8a5944633b188d6b6492e992766783639fbf06508b20e230`

The rule's exit is `H == 'end'`, with `DS=40 TMIN=0 TMAX=300 TEND=330 NOBOOSTREQ=0`. #476 reproduces all 32 discovery and confirmation cells, and matches per trade (max |Δpnl| 4.8e-7 lamports) [measured, PR #476 body].

**What the rule's text leaves to this file.** The rule names two entry legs (1.3 s "primary", 1.9 s "binding"). This file picks the **1.9 s entry as the deciding cell**, and adds two binding legs (section 5). Nothing in the rule's trigger, exit, universe, costs or Q* changes. The choice of the deciding cell is made before any October outcome exists, from the live plan's expected landing of about 1.5–2.0 s p50 [inferred, LIVE-PLAN §2.5], not from any result.

## 3. Data, windows and seal

| | Look 1 | Look 2 (cumulative) |
| --- | --- | --- |
| Counted s0 window | `[2026-10-10T00, 2026-10-16T00)` | `[2026-10-10T00, 2026-11-06T00)` |
| UTC dates | 6 (10-10 … 10-15) | 27 (10-10 … 11-05) |
| Hours read | forward-1002 `[2026-10-09T23, 2026-10-16T01)` | the Look 1 hours, plus walk 2 `[2026-10-16T01, 2026-11-06T01)` |
| Earliest run | after the EXP-012 FINAL (A) is written (about 10-16T02Z) and the section 8.1 conditions hold | after EXP-022's read has ended (section 8.1) |
| α | 0.020 | 0.005 |

- **Counted trade.** A trade counts in a look if the block_time of its canonical pool's first print (s0) falls in the look's counted window.
- **Hour 2026-10-09T23** is read only for `complete` events of pools whose s0 falls in the window (universe membership). No outcome from it is used.
- **Walk boundary.** Paths that cross the forward-1002 / walk-2 boundary (10-16T01) are joined in (slot, tx_index, event_index) order.
- **Seal (from merge until the look that reads them, and for Look 2 hours until EXP-022's read has ended).** No person, agent or job does any of these on any October hour of a V-range pool in its first 360 s, from any source (forward-1002, walk 2, the fast-0 tip tape, the live listener, RPC, a paper runner or a live wallet), **except the real-time observation declared in section 3.1**:
  - computes, opens or prints an H5 trigger outcome, fill, exit, P&L, mean, CI or day sign;
  - prices the H5 trade set.
- **What stays allowed:**
  - the A3 monitor's flags and its listed values;
  - hour counts (sealed, verified, bad);
  - the section 10 precount, which is counts only;
  - the real-time observation of the live canary's and the shadow detector's outcomes for pools in Look 1's window (section 3.1).
- **No early forward-1002 read.** No H5 process reads any forward-1002 hour before the EXP-012 FINAL (A) report is written. This is the EXP-022 §9 / DEC-021 Am.1 rule, now also DEC-016 Am.7.
- **Walk-2 hours wait for EXP-022.** No H5 process reads any walk-2 hour before EXP-022's read has ended. EXP-022's looks are cumulative, and H5 rows price CAP-PICK picks wherever the mints overlap. Reading earlier would breach EXP-022 §9 ("prices the counted picks from any source"). See EXP-022 Amendment 2.
- **CAP-PICK seal, both ways.** From 2026-10-16T01 to the end of EXP-022's read:
  - no H5 trade, live or paper, is taken on a mint the EXP-022 gate picked, and no H5 record is joined to a CAP-PICK pick;
  - no per-pool H5 P&L is produced before each CAP-PICK look.
- **The live canary.** The DEC-024 canary may trade inside the counted window. Its buys and sells are real chain trades by one participant at 0.02 SOL, about 0.05% of a pool with Q of about 40 SOL. They appear in the tape as ordinary rows. The read tool does not remove them, because filtering the tape would be an edit of chain truth. Its sells fall at s0 + 330 s or later, outside the rule's trigger window [0, 300] s, so they cannot trigger a pool. This is disclosed, not corrected.
- **A breach** is recorded here, dated, and the read is reported compromised. A compromised read cannot support a live request.

### 3.1 Declared observation (the owner's decision, 10-08)

- "By the owner's decision (10-08), the live canary's and the shadow detector's outcomes for pools inside Look 1's window are observed in real time. This is declared before the window opens and before any canary trade."
- **Scope.** Pools whose s0 falls in Look 1's counted window `[2026-10-10T00, 2026-10-16T00)`. The canary is the [DEC-024](../DEC/DEC-024-h5-live-canary.md) executor. The shadow detector is the same detector run keyless on live triggers, with no orders.
- "Look 1's rule, data, analysis and pass bar are fixed by this file. Look 1 is always read and always reported as written. It is never skipped, delayed, re-scoped or re-thresholded because of anything the canary or shadow shows. Nothing the canary shows may change any EXP-024 parameter."
  - Look 1 still runs only under the section 8.1 and section 11 conditions (hours, tools, E1, the A3 monitor). None of them depends on the canary. A canary halt (DEC-024 section 5) stops the canary only. It does not stop, delay or change Look 1.
- "The canary's scale-up decision is a separate business decision, not EXP-024 evidence." DEC-024 section 7 governs it, including the owner's recorded override for H5's live trial. The override changes nothing in this file's rule, looks, bar or report.
- "Disclosure: concurrent observation lets canary results influence later choices (for example an owner scale-up). That never changes the formal read, and the read reports it." (Section 12: the read's report carries this disclosure.)
- **What this does not cover.**
  - The read tool's own computation of H5 outcomes from forward-1002 or walk-2 rows stays unopened until the look (section 3).
  - Pools with s0 at or after 2026-10-16T00 are Look 2's added window. The section 3 seal applies to their canary and shadow outcomes until Look 2 is read.
  - **EXP-022 and CAP-PICK are not touched.** From 2026-10-16T01 the CAP-PICK seal holds exactly as before (EXP-022 section 9). The pick exclusion and the fail-closed rule (the pick feed missing or stale for more than 60 s halts H5 buys) are in EXP-022 Am.2 item 2 and DEC-024:80.
- **Provenance.** This subsection records a decision relayed by the manager on 2026-10-08. The sentences in quotation marks are the manager's wording. The owner's own words are not quoted here.

## 4. Universe and pricing on the walk tape (an operational translation, fixed now)

- **Universe.** Exactly the rule's universe: every `complete` graduation whose canonical PumpSwap pool, the first V-range pool after `complete`, has V0 in [17.5, 17.7] SOL. s0 is that pool's first print.
- **No migration filter.** 95.35% of canonical migrations lack a `migration` row on the walker tape, while 99.80% have a `complete` row (`docs/HANDOFF.md`, notebook n_6FKDVJWrbaoGYg). So no migration row is required.
- **V0** is the pool's virtual quote reserve at s0, decoded from the s0 print's trade event. PumpSwap rows are PRE-trade, and pending fees are 0 at a fresh pool's first print.
  - **Walk 2:** V0 is read from the tape (`--event-v`).
  - **forward-1002**, which was walked without event-V: V0 comes from `getTransaction` (`maxSupportedTransactionVersion` 1) of the s0 print, fetched after the FINAL (A) is written.
  - **Fallback:** the pool account (V + pending counters) fetched into a new, empty file at or after 2026-10-16T00:00Z, using `tools/exp012_forward_vmap.py` `pools`/`fetch --new` on H5's hours. Its sha256 is recorded.
  - **Neither available:** the missing-V0 rule below applies.
- **Trigger: tape quote + V0 [pinned].** The trigger uses post-trade Q = tape quote reserve + V0, as the rule is written and as in September, where pending was 0. The live detector uses the same trigger quantity (DEC-024), so the live and the read decisions are equivalent by construction.
  - Since 09-30, v2/v3 trades keep fees in the vault until a sweep (g_october item 7). So this Q can overstate the effective quote by pending(t), and the trigger can fire a little later or less often.
  - This is disclosed and not corrected. Correcting the trigger would need V(t) at every print and would make the live detector and the read differ.
- **Fill pricing: the program's price, V(t) [pinned].** Every priced state uses effective quote = tape quote reserve + V(t), where V(t) is the signed V carried by the event of the print that defines the state. The decoder's pre-/post-trade convention is fixed by its fixture tests (#467). This applies to the landing state (END bound) and the exit state.
  - On forward-1002, V(t) for those prints comes from `getTransaction` after the FINAL. That is about 2 calls per trade.
  - On walk 2 it comes from the tape.
  - The book priced on tape quote + V0 (the rule's literal pricing) is reported, never deciding.
- **Slot-switch exclusion [pinned].** A pool is excluded if its path from s0 to its exit-landing state spans the first slot of epoch 1053, slot 454,896,000 (SIMD-0525, 200 ms slots; forecast about 2026-10-09T14:21–14:41Z, not a measurement; DEC-016 Am.6). A per-pool constant sps would mis-time the 330 s exit on such a pool: a pool that starts 60 s before the switch gets sps 0.2017 and its "330 s" exit lands at about 342 s (ROBUST §3 [measured on exploration paths]). The excluded pools are counted and reported.
  - If the step takes effect before 2026-10-10T00:00Z, no counted pool straddles it and the count is 0.
  - If the chain's step lands at another slot, an outcome-blind dated amendment restates the slot from the walked hours' slot spans, before 2026-10-16T00:00Z.
- **Missing V0 or V(t).** This covers a pool or print that cannot be decoded or fetched.
  - The trade is priced at the lower P&L of two cases: (a) pending = 0; (b) pending at the exit state = 3.83% of effective quote and 0 at entry. 3.83% is the high end of g_october's p50 bound at 300 s [measured bound, `g_october_structure_check.md` item 7].
  - If such trades are in the top 3 of the deciding cell or any binding leg, or are more than 1% of the look's trades, the look is **NOT_DECIDABLE**.
  - A V0-unknown pool that would otherwise qualify is included at the lower P&L of V0 = 17.5 and V0 = 17.7 SOL.
- **sps** is per pool, from block_time over its path, exactly as the rule says. It must be in (0.15, 0.6). At 200 ms slots that gives these slot counts [arithmetic]:

  | Quantity | Seconds | Slots at 200 ms |
  | --- | ---: | ---: |
  | deciding entry | 1.9 | 10 |
  | binding-leg entry | 3.0 | 15 |
  | exit trigger | 330 | 1,650 after s0 |
  | sell lag, deciding | 0.55 | 3 |
  | sell lag, binding leg | 1.35 | 7 |
  | the rule's original entry (report-only) | 1.3 | 7 |

- **BOOST wallet:** the rule's heuristic detector, unchanged. At 200 ms, 1,600 slots is 320 s. The budget check never bound in September (VERIFY §1). The per-pool BOOST PDA is reported beside the detector, never deciding.
- **Coverage.** A trade counts only if every hour is good (sealed, `backfill_verify --content` OK, sha256 line) from the hour of its `complete` through the hour of its exit state. Excluded trades are counted and reported. If more than 5% of the look's window hours are bad, the look is NOT_DECIDABLE.
- **Synthetic-migration pools** (pool opens above the seed) stay in the universe. Their count is reported.

## 5. Execution, costs and legs

- **Deciding cell (D) [pinned]:** stake 0.1 SOL; entry landing slot X = slot_i + ceil(1.9 s / sps); exit trigger s0 + round(330 s / sps); the sell lands ceil(0.55 s / sps) slots later; END bound; own impact; tier fee; the correction below.
- **Binding leg B1 (slow landing) [pinned]:** as D, with the entry at ceil(3.0 s / sps) slots and the sell lag at ceil(1.35 s / sps) slots. The exit trigger stays s0 + round(330 s / sps), so a late sell is a later sell, not an earlier exit. The same correction applies.
- **Binding leg B2 (15% guard) [pinned]:** as D, plus an on-chain min_out on the buy.
  - min_out = floor(cp_buy_out(0.1 SOL, Q_i, B_i, tier) / 1.15), where (Q_i, B_i) is the trigger print's post-trade state (Q incl. V0, the trigger quantity).
  - The buy reverts if the landing state would give fewer tokens than min_out. A reverted buy opens no position and costs one send, 55,000 lamports (the same value under both fail legs).
  - Reference: `s18_capguard.py`, which rejected 21.5% of buys at the 1.3 s entry in the September confirmation [measured, exploration]. The 1.9 s share is unmeasured.
  - 0.15 is the lab's `DEFAULT_SLIPPAGE_CAP`. It is not tuned. A guard of 0.30 is not tried.
- **Report-only legs:** section 13.
- **No guard in D.** The rule has none. B2 is binding because a live buy must carry a min_out, so a book that only wins unguarded cannot be traded.
- **Stake.** Deciding: **0.1 SOL** (100,000,000 lamports).
- **Priority.** 55,000 lamports per send, on the buy and on the sell.
- **Rent.** 2,039,280 lamports on a trade whose sell cannot fill its full balance; otherwise 0. This is the EXP-022 §4.5 convention. The rule omits rent because it is refunded on close.
- **Live−sim correction (EXP-022 §8, adopted unchanged).** Every trade in D, B1 and B2 loses the **larger** of two amounts, before rent and the fail mix:
  - (a) DEC-021 §1's haircut: H = P × (1 − (1 − 0.002608) × (1 − 0.0016)), where P is the sell proceeds after the pool fee;
  - (b) max(0, −2 × r̄), with r̄ from E1 (`aggregate["faa3192"]["pnl_gap_lamports_live_minus_sim"]["mean"]`), scaled linearly from 0.05 to 0.1 SOL.
  - If E1's n < 20, no look runs, and a look not run by its deadline is NOT_DECIDABLE.
  - The correction is a cost, not a rule change. It can only lower the book.

## 6. Fail legs

- **Flat:** 0.85 × pnl + 0.15 × (−55,000).
- **Pressure:** (1 − p) × pnl + p × (−55,000). p = sigmoid(c + 0.8 × log1p(buys in the landing slot) + 0.35 × log1p(buy lamports in the 2 s up to and including the landing slot)). c is refit by `tools.latency_curve.fit_curve` so that mean p = 0.289 over the look's own sends, separately for each cell and each look.
- **Disclosed:** shorter slots mean fewer same-slot buys (DEC-016 Am.6 (g) A). Because c is refit, only the cross-section of p moves.

## 7. Gate statistics and the decision rule

**Deciding cell D.** Look L passes if and only if **every** item below holds under **both** the flat and pressure legs:

1. n ≥ 100 trades.
2. ≥ 5 distinct UTC dates (of s0), with a majority positive (daily total > 0): at least 4 of 6 at Look 1, at least 14 of 27 at Look 2.
3. The lower 90% CI bound of mean SOL per trade > 0 (1,000 bootstrap draws over trades, seed 1, 5th percentile). This is CLAUDE.md, literally.
4. Total SOL > 0 after removing the top 3 trades.
5. Total SOL > 0 after removing the best UTC date. **Binding.**
6. **Day-level p ≤ α_L.**
   - Clusters are UTC dates (24 h blocks from 00:00Z). A date with no trades is dropped; W is the count of the rest, and df = W − 1.
   - m_d is the mean SOL per trade on date d, sd is the sample SD of the W date means (ddof 1), t = mean(m_d) / (sd / √W), and p = P(T_{W−1} ≥ t), one-sided.
   - The larger of the flat and pressure p decides.
   - **α_1 = 0.020, α_2 = 0.005.**
7. **Binding legs B1 and B2,** each with the same correction, each under both fail legs: **mean > 0 and total SOL > 0 after removing the top 3 trades.**

Items 1–4 are the promotion gate, unchanged. Items 5–7 can only turn a pass into a fail.

**Reported at every look, never deciding:**
- the trade-level one-sided bootstrap p (10,000 draws, seed 1);
- the date-cluster CI90 (`common.gate`, 1,000 date resamples, seed 1).

## 8. Look schedule, futility and outcomes

### 8.1 When a look runs (once each)

- **Look 1** runs after all of these hold:
  - (a) the EXP-012 FINAL (A) report is written;
  - (b) every hour of `[10-09T23, 10-16T01)` is sealed and verified;
  - (c) E1 is recorded with n ≥ 20 (cron at 10-16T06:13Z);
  - (d) the read tool is merged (section 12);
  - (e) the section 10 precount, V fetch and pricing check are done;
  - (f) the A3 run of 10-16 06:41Z has shown no halt.

  Earliest: about **2026-10-16T07Z**. Deadline: **2026-10-17T12:00Z**. A Look 1 not run by then is NOT_DECIDABLE, and its α is not carried forward.
- **Look 2** runs after EXP-022's read has ended (a PASS at some look, look 3 done or NOT_DECIDABLE, a halt, or a withdrawal) and every hour to 11-06T01 is sealed and verified.
  - Earliest: about **2026-11-06T03Z**. Deadline: **2026-11-14T00:00Z**, because EXP-022's look-3 limit is 11-13T01.
  - Look 2 needs walk 2 to run through at least 2026-11-06T02, whatever EXP-022's status (EXP-022 Amendment 2).

### 8.2 Outcome

- **PASS** at the first look that passes. The read then ends.
- **FAIL** if no look passes.
- A NOT_DECIDABLE look is not a pass.

### 8.3 Futility (binding)

At Look 1, if the flat mean ≤ 0 **and** the pressure mean ≤ 0 on D, the read ends **FAIL** and Look 2 does not run. Futility can only stop the read, so α is unchanged.

### 8.4 Decision stability

Look 2 re-derives Look 1's trades from the same hours and the same V files (by sha256). Its forward-1002 rows must match Look 1's rows by md5, or Look 2 refuses.

## 9. Multiplicity

- **Within the family:** k = 1. There is one deciding cell; the binding legs are conjunctive. α 0.025 = 0.020 + 0.005 (Bonferroni over looks). No Holm.
- **Across families.** EXP-024 takes DEC-021 §8's second α = 0.025 slot, through DEC-021 Am.2 and [DEC-023](../DEC/DEC-023-h5-family.md). EXP-022 holds the first slot. So the promotion-eligible October families stay at ≤ 0.05 in total. EXP-012's FINAL is reported compromised and cannot support live by itself (DEC-016 Am.2).
  - EXP-022's thresholds (0.005 / 0.008 / 0.012, k = 1) are **unchanged**.
  - A later walk-3 family needs a new DEC.
- **DEC-014 block budget (m).**
  - It governs sealed-block claims. EXP-024 claims no block, so m does not set this read's bar.
  - EXP-024 read outcomes on the 27 non-P1 dates (P2 discovery; P3/P4 confirmation), so **m ≥ 14** from this merge (DEC-023, HOLDOUT_LEDGER).
  - The hunt-1008 investigations that read P2–P4 outcomes are untracked readers. JUDGE.md:58 counts at least 23 directories, plus the audit's 16 edge-scan strategies. That makes m about 37 for any future block claim [inferred].
- **Selection.** H5 is the best of at least 23 hunt families on the same dates (JUDGE.md:58). That is why only fresh forward hours can be evidence.

## 10. Preconditions and integrity

**Before 2026-10-10T00:00Z.** If any of these is missing, H5 is withdrawn before counting and no outcome is read:
- **P0.** This file is merged, with quant-proof OK on its final head.
- **P1.** Every section 17 item marked "before 10-10T00Z" is merged (DEC-023, DEC-024, DEC-021 Am.2, DEC-016 Am.7, EXP-012 Am.3, EXP-022 Am.2 and the ledger edit, which ship with this file).
- **P2.** The last A3 run before 10-10T00Z shows no halt, and no core rule went unevaluated on two consecutive days.

**Pinned now (2026-10-08, before the window opens and before any observation):**
- **E0-H5 day:** 2026-09-20 (fast-pool-0918), as P4 says below.
- **A3 monitor:** `tools/pump_structure_monitor.py` is the blob `1ca0a88cecf0853d94336ea046ba1a910b79f198`, the blob at origin/main `16855f2f55c7e7916c7fc8932809279b04b06504` fetched on 2026-10-08 (`git rev-parse origin/main:tools/pump_structure_monitor.py`). The A3 halt flags, the last-slice median in section 11, and "core rule unevaluated" mean what that blob computes. Any other monitor blob needs a dated, outcome-blind amendment before it is used in a look.

**Before 2026-10-16T00:00Z:**
- **P3. Tools.** The read tool and the forward extractor are merged with quant-proof OK, and their merge commits are recorded in a dated amendment. The read tool includes forward mode, V(t) pricing, the correction, the day-level t, the B1 and B2 legs and the lock. If they miss this deadline, H5 is withdrawn before reading any forward hour.
- **P4. E0-H5.** Recorded in the same amendment, all on one exploration day pinned by its `VIEW.sha256` (**pinned 2026-10-08:** 2026-09-20, fast-pool-0918; its `VIEW.sha256` is recorded in the P3 amendment):
  1. The extractor, run on the raw walker files, gives meta and paths equal by md5 to `g_reachable_cap_book_rescore/{meta,paths}/<day>.parquet` (canonical rows).
  2. The read tool reproduces that day's `boostdip_frozen_conf.parquet` rows (`H == 'end'`) by md5 on (mint, leg, stake, pnl rounded to the lamport).
  3. The blob shas of the extractor, the read tool, `tools/latency_curve.py`, `tools/paper_curve_math.py` and #476's module are recorded. Each look runs in a worktree where they match.

**After the FINAL (A), before Look 1 (outcome-blind; counts and hashes only):**
- **P5. V fetch:** the s0, landing-state and exit-state `getTransaction` records, plus the fallback account fetch. The file sha256s are recorded. Ids stay in files and are never printed.
- **P6. Precount:** the count of good hours, `complete` events, V-range pools, null-V, pools skipped on sps, slot-switch exclusions, and triggers per date. If there are fewer than 100 triggers or fewer than 5 dates with a trigger, Look 1 is NOT_DECIDABLE and no P&L is computed.
- **P7. Pricing check, on 1,000 PumpSwap prints sampled evenly from V-range pools in the window (outcome-blind):**
  - sells must match the tier at vault + V(t) within 1 bp on ≥ 75% of prints;
  - buys must match the implied tier fee within 1 bp on ≥ 90%.
  - If either fails, the frozen fee and pricing model does not describe October, and both looks are NOT_DECIDABLE.

## 11. Kill rules (pre-registered by 2026-10-10T00Z)

**NOT_DECIDABLE.** A look is NOT_DECIDABLE, and is not a pass, if any of these holds:
- fewer than 100 triggers, or fewer than 5 dates with a trigger (P6);
- the pricing check fails (P7);
- V is missing on more than 1% of the look's trades, or on a trade in the top 3 of D or of a binding leg (section 4);
- E1's n is below 20 (section 5);
- any A3 halt flag (below);
- the BOOST last-slice median is below 330 s. It is the median over the look's window of the A3 monitor's daily median last-slice time (PDA-derived), as the monitor blob pinned in section 10 defines it;
- more than 5% of the look's window hours are bad (section 4).

**A3 halt.** Any of the A3 monitor's six halt flags (`pins_changed`, `boost_disabled`, `boost_share_low`, `boost_last_slice_early`, `boost_budget_or_slices_changed`, `synthetic_share_high`), or a core rule unevaluated on two consecutive daily runs.
- **Before 10-10T00Z:** H5 is withdrawn.
- **Inside a counted window:** the read ends NOT_DECIDABLE at that point unless it has already passed. There is no later look, no retune, and no resumption if the monitor clears.

**FAIL, v1 retired.** Look 1 has a flat mean and a pressure mean on D that are both ≤ 0 (section 8.3), or no look passes.
- An exit tied to the observed BOOST end, a different Q*, or a guard other than 15% would each be a **new** rule, needing its own discovery on exploration data (VERIFY "NEW"). None is a retune of v1.
- There is no re-read on any window.

**Spending.**
- NOT_DECIDABLE after any outcome is computed spends that look's window.
- NOT_DECIDABLE before any outcome is computed (a P3–P7 refusal) does not, except as the next bullet says for Look 1. The family may then be filed again only on hours that begin after a new filing.
- From 2026-10-10T00Z, because of §3.1, any refusal or non-run of Look 1 spends Look 1's window and α_1, and v1 is not re-filed.

**Seal.** No H5 trade on, or join to, a CAP-PICK pick, and no per-pool H5 P&L before each CAP-PICK look (section 3). A breach is compromised: the read cannot support live. The section 3.1 observation is declared, so it is not a breach.

**No live.** Independent of PASS or FAIL, no live support from this read (the DEC-024 canary and any trial under DEC-024 section 7's owner override are separate decisions) if any of these holds. The conditions can only remove support:
- the 340 s exit leg (section 13) has a mean ≤ 0 under either fail model;
- the guard leg B2 fails (it is binding, so this is a look that did not pass);
- BOOST had ended before our exit landed on more than 15% of the traded pools (section 13, mechanism);
- a seal breach, or any A3 halt after the pass.

**Live halt.** The live-halt rules are in [DEC-024](../DEC/DEC-024-h5-live-canary.md) §5.

## 12. Read tool (built on #476; precondition P3)

- **Refusal.** It refuses unless:
  - the section 0 line matches exactly once;
  - this file is clean against HEAD;
  - RULE.md and the three frozen files hash to section 2's values;
  - the E0-H5 blobs match;
  - E1 is recorded with n ≥ 20.
- **Hours.** It reads only the section 3 hours. It refuses:
  - any forward-1002 hour before the FINAL (A) marker exists;
  - any walk-2 hour before EXP-022's `LOOK_READS.jsonl` shows a terminal state;
  - sealed blocks, forward-paper and runner paths, and key files (#476's refusal set, extended).
- **Lock and ledger.** It takes an O_EXCL lock per look before the first outcome row. It appends `started`, `completed` or `aborted` to `/data/mal/exp024/LOOK_READS.jsonl`. It refuses a second run of a look, a look before its conditions, and any look after a PASS, a futility stop or a halt.
- **No overrides.** Sections 4–8 are constants, with no CLI override.
- **Order.** It prints the verdict and the report to stderr before writing any file.
- **Disclosure.** Its report carries the section 3.1 disclosure: the live canary's and the shadow detector's outcomes for Look 1's window were observed in real time, and that can have influenced later choices such as an owner scale-up. The disclosure does not change the verdict.
- **Fixes.** After Look 1, the tool may change only to fix a defect, by a dated amendment. The fixed tool must reproduce Look 1's rows sha256 on every binding leg. A fix never changes a parameter.
- **Catalog.** If reads go through `mal_catalog.check_read`, a disclosed non-owner allowance (#465, adapted) must merge first. Otherwise the tool allowlists exactly the section 3 hours.

## 13. Report-only (never deciding)

- **Legs:**
  - the rule's original entry: 1.3 s with a 0.55 s lag;
  - entry at 1.9 s with a 1.35 s lag;
  - entry at 3.0 s with a 0.55 s lag;
  - exit trigger at 310, 320, 335, **340**, 345 and 350 s (the 340 s leg feeds the "no live" rule);
  - sell-retry stress: each sell fails with its own pressure p and is retried every 2 s at +55,000 lamports;
  - START bound, and the worse of START and END;
  - stakes 0.05, 0.25 and 0.5 SOL;
  - the book without the correction, and with only (a) or only (b);
  - the rule's literal V0 pricing;
  - the guard at 0.30 is **not** run, to avoid a tuning menu.
- **Statistics:**
  - per-date table;
  - median;
  - the mean winsorised at +100%, with its days positive;
  - the mean without the top 5% of trades;
  - the share of P&L carried by trades above +100% gross;
  - first-half and second-half means.
- **Mechanism:**
  - each pool's BOOST last-slice time (PDA), and the share of trades whose pool's BOOST had ended before our exit landed, with each group's mean;
  - trigger time and Q at the trigger;
  - detector against PDA disagreement;
  - the post-BOOST control: the same trigger in [360, 600] s, held to +840 s.
- **Diagnostics:** ms per slot per hour; synthetic-pool count; slot-switch exclusions; V(t) − V0 at entry and exit; excluded hours and trades.

## 14. After the read

- **A PASS supports only the DEC-018/DEC-019/DEC-020 path at H5's own operating point.** That means:
  - a reviewed live build with the trigger, the BOOST PDA read and the timer exit, under md5 decision-equivalence against the read tool;
  - landing and exit-timing evidence from the DEC-024 canary;
  - the owner's yes.
  - A paper pass is not live evidence.
- **No-live conditions** are fixed in section 11.
- **The DEC-024 section 7 override** covers H5's live trial only. It is not a pass of this read. Nothing in this file's rule, looks, bar or report changes because of it, and no report of that trial says the book passed.
- **Size.** The 0.25 SOL leg (report-only) with a mean ≤ 0 or an ex-top-3 total ≤ 0 caps live at 0.1 SOL. The rule is not validated above about 0.5 SOL.
- **EXP-022 protection during live.** Until EXP-022's read ends, no H5 live or paper trade may be taken on a mint the EXP-022 gate picked. The live gate's decisions are online, so that set is known in real time. Without this, live H5 P&L would price CAP-PICK's counted picks and compromise EXP-022.

## 15. Honest expectation and power

**All of this is exploration arithmetic on rows already read.** It is not evidence.

**What the most recent September analogue gives** [measured; `h5-work/h5_power2.py`, sha256 `0fc604807ca72412c8d399acdb615bc4882226b8006648c7240bf8c2a7236855`]. These are the last 6 full September dates, 09-19..09-24, n = 510, the rule's primary 1.3 s leg, 0.1 SOL, no correction:

| Leg | Trade mean | Date-mean SD | Day-level p |
| --- | ---: | ---: | ---: |
| flat | +5.592% | 4.611 pp | 0.0196 |
| pressure | +4.719% | — | 0.0345 |
| 1.9 s, flat / pressure | — | — | 0.0087 / 0.0130 |

**On the earlier design's 1.3 s cell, Look 1's rule would not have passed on September's latest six days**: the pressure p is 0.0345, above 0.020. That is the judge's "late September would fail Look 1" line below. The pre-registered deciding cell is the 1.9 s entry, and the same rows do better there before the correction; see the rerun below.

**Simulated power** [measured, simulation; `h5-work/h5_power4.py`, sha256 `8f30647ea094b79ed62a61bd88d4a7f6dd02647ecbbbd4b57fac0589f8710234`]. Method:
- trades drawn i.i.d. from those six dates, shifted to a true pre-correction flat mean μ, then −0.8 pp for section 5;
- 60 trades per date, with a date effect of N(0, 3 pp);
- binding futility; the robustness leg approximated by the 1.9 s / 0.55 s rows;
- 1,500 runs.

| μ (flat, pre-correction) | 0 | +2% | +3% | +4% | +5% | +6% | +8% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| P(pass at Look 1) | 0.00 | 0.01 | 0.02 | 0.05 | 0.10 | 0.15 | 0.35 |
| P(pass at either look) | 0.00 | 0.02 | 0.05 | 0.20 | 0.49 | 0.76 | 0.99 |

- **These figures are for the earlier design.** The simulation used the 1.3 s cell as the deciding cell and a 1.9 s / 0.55 s robustness leg. It did not model the 3 s / 1.35 s leg or the 15% guard. The judge's changes (deciding cell at 1.9 s, plus B1 and B2) make the pass condition stricter and the deciding mean lower (about 0.75 pp in September), so these figures are upper bounds for this design [inferred]. The rerun the judge asked for (PLAN §1) is the block below. It does not change any rule here.
**Rerun on the pre-registered legs** [measured, exploration arithmetic; `/data/mal/hunt-1008/h5-work/h5_power5.py`, sha256 `168f3a40e0ecf1fc7103ddf2be2e5c24c59641d837c0ecb330b7075afdd49722`; written by the author of this file because PLAN.md §1 asks for the rerun]. It reads only the September confirmation rows already read (`boostdip_frozen_conf`, `latency_conf` and `capguard_conf` under `h5-flows/out/`).
- **Legs.** D is the 1.9 s entry with a 0.55 s lag. The B1 stand-in is the 3 s entry with a 0.55 s lag, because **no 1.35 s exit-lag data exists**, so B1 is optimistic here. B2 is the 1.9 s leg with the 15% guard: a trade whose landing price is above 1.15 × the trigger price is rejected and costs −55,000 lamports under both fail legs. That rejects 23.13% of the 1,124 confirmation trades at the 1.9 s entry (21.53% at 1.3 s, which reproduces `s18_capguard.py`'s 0.215).
- **The six dates 09-19..09-24 (n = 510), pre-correction leg means.** D: flat +4.576%, pressure +3.862%. B1 stand-in: flat +2.997%, pressure +1.779%. B2: flat +4.406%, pressure +3.529%. The rule's 1.3 s cell: flat +5.592%.
- **Would the pre-registered rule have passed on those six dates?** Before the correction, **yes on every item** at α 0.020, including the B1 and B2 stand-ins (day-level p 0.0087 flat, 0.0130 pressure). With a flat −0.8 pp stand-in for the section 5 correction, **no**: the day-level p is 0.0178 flat and 0.0291 pressure, and the pressure side fails item 6. Neither run is evidence: these dates are in the rule's own confirmation set.
- **Power**, same method as the table above (i.i.d. draws of 60 trades per date from those six dates, one constant shift for every leg, date effect N(0, 3 pp), a flat −0.8 pp stand-in for the correction, binding futility, 1,500 runs, seed 5). The μ axis is still the 1.3 s cell's pre-correction flat mean, so the columns compare with the table above:

  | μ (flat, pre-correction) | 0 | +2% | +3% | +4% | +5% | +6% | +8% |
  | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | P(pass at Look 1) | 0.00 | 0.00 | 0.01 | 0.04 | 0.07 | 0.12 | 0.33 |
  | P(pass at either look) | 0.00 | 0.00 | 0.01 | 0.09 | 0.32 | 0.63 | 0.97 |

- **Reading.** The rerun is at or below the earlier table at every μ, and well below it at +5% and +6% for either look (0.32 and 0.63 against 0.49 and 0.76). The judge's odds below were **not** recomputed from it and are, if anything, high [inferred]. The stand-ins are optimistic (no 1.35 s lag, a flat correction). The copied table and odds are left as the judge and Track A gave them.

- **Other α splits** (no correction, no futility, `h5_power3.py`, sha256 `14f07a7332009c0f7bce299ff5e990cc37928372187695c3088c41ffcff20ce7`):

  | Split (Look 1 / Look 2) | Look 1 at +6% | Both looks at +4% |
  | --- | ---: | ---: |
  | 0.025 / 0 | 0.26 | 0.08 |
  | 0.020 / 0.005 | 0.22 | 0.41 |
  | 0.015 / 0.010 | 0.16 | 0.53 |

  0.020 / 0.005 keeps most of Look 1, which serves the owner's 10-31 date, and still gives a strong backstop.
- **October prior** [inferred]:
  - Block means fell from +26.7% to about +5–6%.
  - October BOOST ends earlier: median last slice 337 s after the migrate tx (n = 8, JUDGE.md:20), against September's 342.9–347.4 s. That pushes the 330 s exit toward September's 335–340 s cells (oracle-0922: +4.619% at 335 s, +0.764% at 340 s).
  - The first A3 run (job #383) had a median of 341.5 s.
  - **A central October flat mean of about +2–4% before the correction is plausible.** At +2–4% (`h5_power5.py`, the pre-registered design), Look 1 passes 0.00–0.04 of the time and either look 0.00–0.09. The Track A figures for the earlier design, 1–5% and 2–20%, are in the table above. JUDGE.md gives H5 an October prior of 0.35.

**The judge's honest odds** (PLAN.md §6, 2026-10-08T20Z) [est]:
- **Prior** that October's mean is > 0: 0.35.
- **Track A power:** at a true +2–4%, Look 1 passes 0.01–0.05 and either look 0.02–0.20; at +5–6%, 0.10–0.15 and 0.49–0.76.
- Late September would fail Look 1 (pressure p 0.0345).
- **P(Look 1 pass) ≈ 0.0265; P(either look) ≈ 0.1078.** Look 2 decides after 10-31.
- **If it passes** [est]:
  - Start from the latest-block edge: +5.765% flat, +4.955% pressure (VERIFY §4, 09-18..25, n = 526).
  - Subtract 0.75 pp (1.9 s entry), 0.71–1.62 pp (guard), 0.8 pp (correction) and 0.37 pp (exit timing).
  - Net **+1.415% to +3.137% per attempt**; at 53.5–94 attempts/day, **0.037–0.147 SOL/day at 0.05 SOL** (0.075–0.294 at 0.10), 0.454–1.769 SOL by 10-31.
  - 7 SOL needs the early-September edge back.
- **What this means for 10-31.** A PASS on 10-16 would be a surprise. It would still need the section 14 path. Look 2 decides only after 10-31.

## 16. Disclosures: every prior read, and the decay

1. **Discovery.** h5-flows read explore-0814 (P2, `[08-14T12, 08-28T12)`):
   - the census (08-20..21) and pairs (08-20);
   - the actor and lagged-flow study on 08-14..08-17 (99,682 mints);
   - the graduation learner (08-14..08-20, then 15 days);
   - the BOOST-dip v1 D×H grid, the depth slices, the v2 grid (Q* {40, 50, 60} × exit {15, 30, 60, 120 s after landing, end}), the ratio trigger and the post-BOOST control.
   - The selection was one pick from about 25–30 cells (VERIFY §5).
   - Discovery result (0.1 SOL, 1.3 s): flat +12.161% [+8.336, +16.454] 13/15, n 1,702; median −9.75%.
2. **Confirmation, read once.** RULE.md sha written at 18:05:44Z; first confirmation output at 18:17:46Z, on 2026-10-08.
   - The read covered 09-03..09-25, 21 dates: fresh-0903 (P3, spent by EXP-012), exp011-0909 (P4), fast-pool-0918 (09-18T23..09-22) and oracle-insample-0922.
   - **EXP-009's owned hours `[09-15T12, 09-18T23)` are not in it.** The g_reachable meta for 09-15 ends at 11:58:19Z, and for 09-18 it spans 23:04–23:58Z [measured].
   - Result at 1.3 s: flat +13.208% [+8.251, +19.060] 17/21, pressure +11.418% [+7.134], n 1,124, median +0.76%. At the 1.9 s entry: flat +12.455% [+7.656, +18.305] 17/21, pressure +10.882% [+6.576] (REPORT.md).
   - The same run computed exits H = 15, 30, 60 and 120 s in `boostdip_frozen_conf.parquet`. Only `end` was scored in `frozen_conf_score.txt`.
   - Report-only stress on the same rows: latency 1.3 to 12 s, the 15% guard, stake 0.1 to 4 SOL, and tails. **The 3 s entry gave flat +12.213% [+6.871, +18.761], 16/21; the 15% guard (at the 1.3 s entry) gave +11.588% [+6.820, +17.610], 17/21, ex-top-3 +9.3914 SOL** (`s19_latency_conf.txt`, `s18_capguard_conf.txt`). These legs were looked at before they were made binding; their selection is the judge's, from the live plan, not from a search. **No exploration number exists for the 1.35 s exit lag.**
3. **Verifier.** It re-derived the result from the raw `audit-1008/tape` on all 21 dates: +12.868% / +11.206%, CI lo +7.838 / +6.792, n 1,123. It also ran:
   - the exit grid from 310 to 350 s on confirmation and discovery;
   - the START/END bounds;
   - the sell-retry stress;
   - BOOST last-slice timing by block;
   - drop-block cuts.
4. **The repo port #476** re-ran discovery and confirmation, a reproduction only.
5. **The hunt-1 judge and this file's author** resampled the confirmation rows for power. A 6-date analogue was computed in section 15.
6. **Decay.**
   - Block flat means: +26.727% → +11.706% → +5.352% → +6.781% (verifier: +27.1 → +10.8 → +5.2 → +6.2).
   - fast-pool-0918 alone fails: CI lo < 0, flat ex-top-3 −0.0192 SOL.
   - The last two blocks: +5.765% [+3.242, +8.943], 6/8, ex-top-3 +1.7479 SOL.
   - Without fresh-0903: +7.548% [+3.758, +11.969].
7. **Exit cliff.** The pooled confirmation goes +13.208% at 330 s, +8.827% at 340 s (11/21), +5.291% at 345 s (CI lo −0.439). oracle-0922 goes +6.781% at 330 s, +0.764% at 340 s, −2.101% at 345 s.
   - August was flat over the same range. The cliff sharpened in September.
   - BOOST's last slice p50 moved from 347.4 s to 342.9 s, and its p10 from 334.2 s to 331.7 s.
   - Trades where BOOST had already ended: +2.91%, against +13.588% for the rest.
8. **Tails.** 5.78% of confirmation trades carry 110.2% of flat P&L (discovery: 8.34% / 162.3%).
9. **In-sample for the lab.** The BOOST, V and post-BOOST cliff facts behind the 330 s exit were learned on these September dates by d12, the audit and CAP-PICK's design. At least 13 families plus the hunts and the audit have read the 27 non-P1 dates.
10. **Capacity.** Impact keeps other traders' reserve deltas fixed, which understates impact in a drained pool. The rule is not validated above about 0.5 SOL.
11. **October is unmeasured:**
    - the 10-02 redeploy;
    - v2 fee-keeping (pending in the vault, section 4);
    - 200 ms slots (forecast for epoch 1053, about 10-09T14:30Z; not a measurement);
    - synthetic migration (unused 0/10);
    - BOOST cadence set off-chain;
    - competition (hunt H4 found the mid-BOOST round trip thinly contested).
12. **Future information in the frozen rule.** sps uses the pool's later block_time. The BOOST detector looks 1,600 slots ahead. The verifier found both immaterial. They are kept frozen, with live equivalents (wall clock, PDA).
13. **Seal record.** No H5 process has read forward-1002, forward-1016, forward-paper or runner outputs, or a sealed block. October structure seen so far is BOOST profiles and config only (g_october, invB, the A3 monitor). No H5 outcome was seen.
14. **Considered and not adopted.** The hunt-1 judge proposed fresh-0828 as the deciding read (JUDGE.md §2.1). It is not adopted here, for four reasons:
    - it is pre-upgrade August, so it says nothing about the October decay or the cliff;
    - the block budget (p < 0.025/m, m ≥ 14, about 37 honestly) leaves power of about 0.15–0.19 even if H5 is real (JUDGE.md:105);
    - it spends a reserve block;
    - fresh-0828 is adjacent to the strongest block, fresh-0903.
15. **The live canary reads the same pools.** DEC-024 may trade the rule at 0.02 SOL inside the counted window. It is measurement, not evidence. Its outcomes and the shadow detector's, for pools in Look 1's window, are observed in real time by the declared decision in section 3.1 (DEC-024 §6). It starts only after this file is merged, so it cannot change the frozen rule or the read tool, and Look 1 is read and reported as written whatever it shows.

## 17. Companion texts and dates

All items marked "before 10-10T00Z" ship in the same PR as this file.

| By | Item |
| --- | --- |
| before 10-10T00Z | **[DEC-023](../DEC/DEC-023-h5-family.md)** (new): H5 is a separate DEC-014 family on forward hours. k = 1. α 0.025, split 0.020 / 0.005, using DEC-021 §8's second slot. The ledger second-reader exception for forward-1002 `[10-09T23, 10-16T01)`, read only after the FINAL. m ≥ 14. No backward-block PASS is required (as DEC-021 Am.1 did for CAP-PICK). A pass leads only to DEC-018/019. |
| before 10-10T00Z | **[DEC-024](../DEC/DEC-024-h5-live-canary.md)** (new): the owner-approved live measurement canary. It starts only after this file is merged. |
| before 10-10T00Z | **DEC-021 Am.2:** §8's second α slot goes to EXP-024. The walk-2 family stays CAP-PICK alone (k = 1, thresholds unchanged). |
| before 10-10T00Z | **DEC-016 Am.7:** forward-1002 `[10-09T23, 10-16T01)` is also read by EXP-024's tool, only after the FINAL (A) is written. EXP-024 uses its own V fetch, not the (B) files. The H5 seal applies. |
| before 10-10T00Z | **EXP-012 Am.3:** discloses EXP-024's non-owner counted read (DEC-014(a)). The FINAL is unchanged. |
| before 10-10T00Z | **EXP-022 Am.2:** discloses that EXP-024 Look 2 reads walk-2 `[10-16T01, 11-06T01)` only after EXP-022's read ends. H5 trades exclude CAP-PICK picks. Walk 2 runs to at least 11-06T02 whatever EXP-022's status. EXP-022's thresholds and seal are unchanged. |
| before 10-10T00Z | **HOLDOUT_LEDGER:** a Status line on the Forward walk row and the Forward walk 2 row, a changelog line, and m ≥ 14. |
| before 10-16T00Z | Read tool and forward extractor merged (P3). E0-H5 recorded (P4). |
| after the FINAL, before Look 1 | The E1 record (n, r̄, sha), and the P5–P7 counts and hashes, in a dated amendment. |
| after EXP-022 ends, before Look 2 | Any defect fix, only by reproduction (section 12). |

## 18. Out of scope

- Tool code (the read tool, the extractor, E0-H5).
- The live build, its limits and the canary (DEC-024); production live size (DEC-018/019/020).
- H4, H2 and G4.
- Any sealed-block claim.
- No `data/tries.jsonl` line at registration.

## Amendments

### Amendment 1 (2026-10-09, before any counted hour and before any forward value was read; revised the same day after quant-proof's review): V for forward-1002 comes from forward-1002ev first, behind a cross-source check

Outcome-blind. To write or revise it, no row, report or scratch file of forward-1002, forward-1002ev or walk 2 was opened, and no H5 outcome, trigger, fill, exit or P&L was computed or read. Its inputs are the walker and decoder source, file lists, the repo docs and quant-proof's review. It changes where Look 1 gets V and adds two checks that can only remove support; nothing else.

**Why.** Section 4 gives forward-1002 V by `getTransaction` at about 2 calls per trade, because job #382 was walked without `--event-v`. A second walk, `forward-1002ev` (MiScusi job #433, `[2026-10-09T00, 2026-10-16T01)`, `--event-v`, [DEC-016 Amendment 9](../DEC/DEC-016-exp012-forward-on-chain-hours.md)), carries V on the tape for the same hours. Look 1 and Look 2 then price from the same decoder for V (DEC-016 Amendment 9 item 5 pins it and records the version gap).

**The V source order for forward-1002 (replaces section 4's "forward-1002" bullets for V0 and V(t) and the "Fallback" bullet at lines 166-175; Look 2 on walk 2 already has event V and is unchanged).** Per print that needs V0 or V(t), take the first source that has it, and never choose among sources by their effect on a result:

1. **forward-1002ev, through `tools/forward_v_join.py join`**, run once after the EXP-012 FINAL (A) is written, and **only if the cross-source check below passes line A**. A print gets its V from here when its hour is usable (both walks' hour sealed and verified, the file hash matches its verify line, and the 1:1 match rate is at least 99.5% over all trade rows and over PumpSwap rows) and the print matches an ev row 1:1 on (slot, signature, event_index) with `sol_lamports`, `token_raw`, `quote_reserve` and `base_reserve` equal and an ev `virtual_quote_reserves` present. The convention of that V (the stored V before the trade) is the decoder's, pinned by its fixture tests (#467).
2. **`getTransaction`** (`maxSupportedTransactionVersion` 1) for the prints on the join's **fallback list**: every readable PumpSwap row of a refused or bad hour (a forward-1002 hour with an unreadable line included), and the individual rows of a usable hour that did not match, differed in a field, or matched an ev row with no V. It is fetched for the prints this section already fetches (s0, the landing state, the exit state) and for **all of P7's 1,000 sampled prints** (line B needs their raw events; P7's tier lines take V in this same order). An ev hour that was **not walked** (the walk hit its credit cap, was cancelled or stopped) is a bad hour, not an empty one: it is on this list, and it does not make the forward-1002 hour bad under the Coverage rule (forward-1002's own hours are what Coverage counts).
3. **The pool account** (V + pending counters), exactly as the "Fallback" bullet says: a new, empty file at or after 2026-10-16T00:00Z with `tools/exp012_forward_vmap.py` `pools`/`fetch --new`, sha256 recorded. This is now the third source.
4. **None of the three:** the missing-V rule of section 4 applies unchanged (the lower P&L of the two cases, the top-3 and 1% NOT_DECIDABLE tests).

**Cross-source V check (P5; replaces the former "Not decided here").** It runs after the FINAL (A) and before Look 1, prints counts only, and costs about 300 `getTransaction` calls (about 1,300 with P7's 1,000 sampled prints, below).
- **Sample: 300 prints.** For each of 100 Look 1 triggers, the s0 print, the landing-state print and the exit-state print that price the deciding cell D. The triggers are chosen by ascending sha256 of the ASCII string `signature:event_index` of the trigger print; if there are fewer than 100 triggers, all of them. (The triggers are found with the joined V; the sample tests that V.)
- **Fetch:** `getTransaction` (`maxSupportedTransactionVersion` 1), decoded with the pinned `records_from_logs(event_v=True)` of `observe/trade_decode.py` (the blob in DEC-016 Amendment 9 item 5), rows keyed by (slot, signature, event_index).
- **Line A, equality.** For each compared print, the joined ev `virtual_quote_reserves`, `quote_reserve` and `base_reserve` must equal the `getTransaction` decode's **to the lamport on 100% of compared prints**. At most 3 prints of the sample (1% of 300) may be **not comparable** (the print is absent from the decode, or the fetch failed after retries). A sampled print whose V already came from the fallback (source 2: its hour is refused or bad, or it is on the fallback list) is **counted separately as `from_fallback`**, not compared and not a miss, because its V is already a `getTransaction` value. Both sources decode the same event bytes, so any difference is a defect. Zero misses in 300 bounds the miss rate at about 1% (one-sided 95%), the same 1% as section 4's missing-V rule.
  - **If line A fails**, forward-1002ev is not used for Look 1: `getTransaction` (source 2, then source 3) is used for every print section 4 fetches (s0, the landing state, the exit state) and for P7's sample.
- **Line B, price level (on the same raw events):** see P7 below.
- **Output:** counts only (prints sampled, compared, `from_fallback`, not comparable, equal, and differing per field); ids stay in files; the file sha256 is recorded.

**P5 (section 10), restated.** After the FINAL (A) and before Look 1, outcome-blind: (a) run `tools/forward_v_join.py join` into a new empty directory; record the sha256 of `join-report.json`, `fallback.jsonl` and each `v-<hour>.jsonl.zst`; print only counts (hours usable and refused with their reason codes, rows joined, rows on the fallback list); (b) run the cross-source check above; (c) fetch the step 2 and step 3 records as before and record their file sha256s. Ids stay in files and are never printed. The decoder pins of DEC-016 Amendment 9 item 5 are recorded with them (`python3 -m tools.forward_v_join pins` exits 0).

**P7 (section 10), extended with line B.** The two tier lines stand unchanged and are computed on V from the source order above. They alone cannot catch a V decode defect on up to 25% of sells (10% of buys), far more than the 1% tolerance, and the stored tape row cannot carry a constant-product line (it keeps `sol_lamports` with fees, `token_raw` and the reserves, and drops `pool_quote_amount`, `lp_fee` and `protocol_fee`). So **line B runs on raw `getTransaction` events of the 300 cross-source prints plus P7's 1,000 sampled prints** (all 1,000 are fetched; about 1,300 calls in total), on at least 99% of each side. A print matches if it is within 1 bp **or** within 2 units of the integer law (base units for buys, lamports for sells), so a rounding difference on a dust trade is not a miss:
- **sells:** `pool_quote_amount` against `(quote_reserve + V) * base_in // (base_reserve + base_in)`, where `base_in` is the sell's `token_raw`;
- **buys, excluding every buy whose `ix_name` starts with `buy_exact_quote_in` (v1 and v2), and every row with `zero_sol`:** `token_raw` against `base_reserve * qin // (quote_reserve + V + qin)`, where `qin` is the buy's `pool_quote_amount`. A buy with no `ix_name` is not comparable.
A side with no comparable event fails that side. **If either side fails, P7 fails and both looks are NOT_DECIDABLE**, as section 10 already says for P7.

**The seal.** forward-1002ev is sealed as forward-1002 is ([DEC-016 Amendment 9](../DEC/DEC-016-exp012-forward-on-chain-hours.md) item 2). **Before the FINAL only the walker, `tools/backfill_verify.py` and hour counts touch it, and no hash-only, md5, join or match-rate run happens.** The join tool refuses every mode that opens a trade file until the FINAL marker is in the external FINAL ledger. This adds nothing to section 3's allowed list, and section 3's "No early forward-1002 read" rule is unchanged and covers forward-1002ev. After the FINAL, section 3 and 3.1 still hold: no H5 outcome, fill, exit or P&L is computed or opened before the look, and nothing in this amendment lets the tool, the join or the cross-source check compute one. An ev hour that proves bad after the FINAL may be re-walked once (DEC-016 Amendment 9 item 4); that is a manager decision and never a read.

**Not changed.** The rule, parameters, universe, trigger, fill pricing convention, costs, legs, gate statistics, look schedule, futility, multiplicity, kill rules, pinned counting start (section 0), the V-range and sps conditions, and the NOT_DECIDABLE conditions apart from the two added above (line A moves Look 1 to the section 4 path, and line B fails P7). The window, hours read and alpha of both looks are as in section 3. Nothing is added to the families or to m.

**Walk 2 and Look 2.** Look 2 reads walk-2 hours with V from the tape. Before Look 2 reads, DEC-016 Amendment 9 item 5 requires walk 2's job gitRef and decoder blobs to be recorded and to equal the pins; if they do not, **Look 2 is NOT_DECIDABLE**.

**Companion text.** EXP-025 (merged, `fcc7e99`) reads forward-1002ev **directly** for `[2026-10-09T00, 2026-10-16T01)` (its P6) with its own raw-JSONL cross-check against forward-1002, not through the join tool. It shares the V source and the decoder pin; its seal is the same closed list. This amendment does not edit EXP-025 and does not register it as a reader of anything.

### Amendment 2 (2026-10-09; final revision 2026-10-09T06:57Z; no Look 2-window outcome exists before 2026-10-16T00Z, and none had been observed at the final revision): the declared observation extends to Look 2's added window, for the canary's and the shadow detector's real-time outcomes only

Outcome-blind for every pool with s0 at or after 2026-10-10T00, the start of Look 1's counted window. The heading records the instant of the final revision. At that instant Look 1's window had not opened and neither had Look 2's added window (2026-10-16T00), so no canary or shadow outcome of any pool in either window had been seen, and none could exist: [DEC-024](../DEC/DEC-024-h5-live-canary.md) section 3 bars any canary send before 2026-10-10T00:00Z. To write and revise it, no row, report or scratch file of forward-1002, forward-1002ev, walk 2, forward-paper or a runner was opened, and no H5 trigger outcome, fill, exit or P&L was computed or read for any pool with s0 at or after 2026-10-10T00, from any source. The one H5 outcome computation made while building the code guard is its replay-parity run on the 2026-09-20 exploration tape, which lies outside every counted window (it is the E0-H5 day pinned in section 10); it computed 154 outcomes and 77 strips, and only counts were recorded. Its inputs are this file, DEC-024, the source of `tools/h5_shadow.py` and `tools/h5_executor.py`, and the owner's recorded decisions listed under Provenance. It extends one exception in section 3.1 and changes nothing else.

**The gap it closes.** Section 3.1 declares the real-time observation for pools whose s0 is in Look 1's window `[2026-10-10T00, 2026-10-16T00)`, and its "What this does not cover" bullet (line 158) says that for pools with s0 at or after 2026-10-16T00 the section 3 seal applies to the canary's and shadow's outcomes until Look 2 is read. The tools did not match that text, in two ways:
- The shadow detector hides outcomes only through the EXP-022 CAP-PICK hook. That hook starts at 2026-10-16T01 and its pick oracle is, at this date, a stub that suppresses everything. So a pool with s0 in `[2026-10-16T00, 2026-10-16T01)` had its outcome, legs, exit ladder and price strip written in the clear, and once a real pick oracle is wired in, every non-pick pool would have been unsealed for the whole of Look 2.
- The executor writes fills and realized P&L to its ledger, and its live config ends at `end_ms` 2026-10-16T00:30Z (`scripts/mal-fast/h5-executor-live.json`), so it can trade pools whose s0 is in the first half hour of Look 2's added window.

The owner's recorded decisions (Provenance) need the live P&L to be watched: the scale ladder's step checks use it, and the 10-08 mandate runs through October. A canary that is blind from 2026-10-16 cannot apply the ladder's "results are not negative" checks.

**The declaration.** The sentences in quotation marks are the manager's wording, in the form of section 3.1.
- "By a manager decision derived from the owner's recorded decisions (2026-10-09), the live canary's and the shadow detector's outcomes for pools inside Look 2's added window are observed in real time. This is declared before Look 2's window opens and before any Look 2 outcome exists."
- **Scope.** Pools whose s0 (the block_time of the canonical pool's first print, as in section 3) falls in Look 2's added window `[2026-10-16T00, 2026-11-06T00)`, the end excluded. A pool with s0 at or after 2026-11-06T00 is outside this declaration. The canary is the DEC-024 executor. The shadow detector is the same detector run keyless on live triggers, with no orders. What may be observed in real time is their outcomes: the executor's fills, exits, wallet deltas and realized P&L, and the shadow's outcome, strip, exit-ladder and pool records. Who watches is not limited here (the owner, Helm, the manager, builders, the DEC-024 watchdog and the daily check do). The one user this does not reach is the read tool, whose inputs stay the section 3 hours. Look 1's window is unchanged and stays as section 3.1 says. Where section 3 (the real-time observation bullet in the allowed list, line 139), section 3.1 (line 158), section 12 (Disclosure, line 366) and section 16 item 15 (line 524) name only Look 1's window, they are read as extended by this amendment to Look 2's added window, for the canary's and the shadow's real-time outcomes and for nothing else.
- "Look 2's rule, data, analysis and pass bar are fixed by this file. Look 2 is always read and always reported as written. It is never skipped, delayed, re-scoped or re-thresholded because of anything the canary or shadow shows. Nothing the canary shows may change any EXP-024 parameter."
  - "As written" includes section 8. Look 2 runs only under the section 8.1 and section 11 conditions, and not at all after a Look 1 PASS (8.2) or a Look 1 futility stop (8.3). Nothing the canary or shadow shows adds, removes or changes any of those conditions. A canary halt (DEC-024 section 5) stops the canary only. It does not stop, delay or change Look 2.
- "The canary's scale-up decision is a separate business decision, not EXP-024 evidence." DEC-024 section 7 governs it, including the owner's recorded override for H5's live trial and the owner's scale ladder. The override and the ladder change nothing in this file's rule, looks, bar or report.
- "Disclosure: concurrent observation lets canary results influence later choices (for example an owner scale-up). That never changes the formal read, and the read reports it."

**Report disclosure (replaces the Disclosure bullet of section 12, line 366, for the Look 2 report).** The Look 2 report must carry the concurrent-observation disclosure. It states that the live canary's and the shadow detector's outcomes were observed in real time for pools in Look 1's window (section 3.1) and for pools in Look 2's added window `[2026-10-16T00, 2026-11-06T00)` (this amendment), with CAP-PICK picks excluded, and that this can have influenced later choices such as an owner scale-up or a step of the DEC-024 scale ladder. The disclosure does not change the verdict. Section 13 lists no report text, so it is unchanged. The Look 1 report keeps the section 12 text. The report also states that any EXP-024 amendment or read-tool fix dated at or after 2026-10-16T00Z was made with Look 2-window outcomes in view. Each one is listed in the Look 2 report and is not called outcome-blind.

**Unchanged.**
- **The read tool's own computation.** The read tool's computation of H5 outcomes from forward-1002 and walk-2 rows stays sealed until the look. Section 3's bans are unchanged: no early forward-1002 read before the EXP-012 FINAL (A), and no walk-2 hour before EXP-022's read has ended. The read tool's inputs remain the section 3 hours (section 12). The shadow's output and the canary's ledger are not among them, and the tool's refusal set is not loosened. Observing the canary's and the shadow's real-time outcomes is not a read of the tape and opens no tape row.
- **The CAP-PICK seal.** From 2026-10-16T01 to the end of EXP-022's read, no H5 trade, live or paper, is taken on a mint the EXP-022 gate picked, no H5 record is joined to a pick, and no per-pool H5 P&L for a pick is produced before each CAP-PICK look (section 3, EXP-022 section 9, EXP-022 Amendment 2 item 2, DEC-024 section 6). The oracle fails closed: a pick feed that is missing or stale for more than 60 s halts H5 buys, and an oracle that errors seals the pool. This amendment covers non-pick pools only. A pick stays sealed whatever the declaration says.
- **The pick-oracle dependency.** H5 trades from 2026-10-16T01 only if the real CAP-PICK pick oracle (PR #509, `claude/cap-pick-oracle`) is merged and wired in. Until then the executor refuses every buy in the seal window and the shadow seals every pool. This amendment neither supplies the oracle nor loosens the fail-closed rule.
- **The rule and the read.** The rule, parameters, universe, trigger, fill pricing, costs, legs, gate statistics (every item of section 7), look schedule, futility, multiplicity, kill rules, pinned counting start (section 0), windows, hours read and α of both looks (section 3), and the NOT_DECIDABLE conditions. Nothing is added to the families or to m.

**Spending (the Look 2 copy of the last bullet of section 11, Spending).** From 2026-10-16T00Z, because of this amendment, any refusal or non-run of Look 2 that section 8 requires spends Look 2's window and α_2, and v1 is not re-filed. A Look 2 that section 8 does not require (after a Look 1 PASS or a Look 1 futility stop) spends nothing.

**Enforcement in the tools.** `tools/h5_shadow.py` applies the H5 seal on its own. For a pool whose s0 is at or after 2026-10-16T00:00Z it writes the decision-time trigger records the executor needs and withholds every outcome-bearing record (outcome, legs, exit ladder, price strip, min_q), unless the process was started with `--h5-look2-observed EXP-024-Am2` (wrapper variable `H5_LOOK2_OBSERVED`), which the start record logs. The flag lifts the withholding for `[2026-10-16T00, 2026-11-06T00)` only: a pool with s0 at or after 2026-11-06T00:00Z stays withheld whatever the flag says. The CAP-PICK seal still applies on top from 2026-10-16T01 and ignores the flag. The flag is for the shadow job started after this amendment is merged. The daily check (`scripts/mal-fast/h5-daily-check.py`) alerts when, inside the window with `LIVE_OK` set, the newest shadow start record does not show `h5_look2.observed` true, because a shadow without the flag leaves the canary with no paper twin.

**Provenance.** This is a manager decision derived from the owner's recorded decisions:
- the owner's scale ladder T0 0.02, T1 0.10, T2 0.30 SOL, 25 to 50 trades per step, onward: MiScusi notebook entry n_xaHk-8t27C8qbw, "OWNER: scale ladder…", manager-verified 2026-10-09, which holds the owner's verbatim answer;
- the 10-08 real-income mandate (`docs/HANDOFF.md`, "Owner mandate and decisions"), which is the source of "through October": the ladder itself says "onward";
- the `OWNER_OVERRIDE_CONFIRMED` line in DEC-024 section 7.

These need the live P&L to be watched, and the probe wallet is public on chain (DEC-024 section 6), so anyone can compute it. The sentences in quotation marks are the manager's wording. The owner's own words are not quoted here. The notebook ID and its verification are as the manager relayed them, and the author of this amendment did not open the entry. The owner may revoke this decision. A revocation applies from its recorded instant, restores the section 3 seal for pools whose s0 is after that instant, and does not undo the disclosure for outcomes already observed. DEC-024 section 6 mirrors this amendment.

### Amendment 3 (2026-10-09, before any canary send and before any pool with s0 in hour 2026-10-09T23 existed): declared observation for pools with s0 before 2026-10-10T00

By the owner's decision of 2026-10-09 ([DEC-024](../DEC/DEC-024-h5-live-canary.md) Amendment 1), the live canary's and the shadow detector's outcomes for pools with s0 in [2026-10-08T20:25Z, 2026-10-10T00) are observed in real time. This adds one item to section 3's exception list and changes nothing else.
- The shadow has printed such outcomes since about 2026-10-08T21:30Z. For pools with s0 before this amendment's merge, that is disclosed here, not declared in advance. Every such pool is outside every look's counted window. Hour 2026-10-09T23 stays a Look 1 read hour for `complete` events only, and no outcome from it is used.
- This is also the carve-out for DEC-016 Amendment 7's H5-seal sentence on hour 2026-10-09T23.
- Section 3.1's sentences apply unchanged. Look 1 is always read and reported as written. It is never skipped, delayed, withdrawn, re-filed, re-scoped or re-thresholded because of anything the canary or shadow shows, including outcomes for pools with s0 before 2026-10-10T00. The read tool's inputs are unchanged.
- The Look 1 report's section 12 disclosure adds: canary and shadow outcomes for pools with s0 before 2026-10-10T00 were observed in real time, from about 2026-10-08T21:30Z (shadow) and from the first canary send (canary).

### Amendment 4 (2026-10-09, drafted from 07:58Z; before any counted hour, before the official P2 run of the A3 monitor, and before the synthetic classifier is run on any counted pool): synthetic-migration pools leave the universe (a population restriction), the A3 halt list drops `synthetic_share_high`, P2 is read as the last run, and the official P2 run is declared

**This amendment is blind to every counted-window outcome. It is not outcome-blind.** No pool with s0 at or after 2026-10-10T00 exists yet, so no outcome of a counted pool has been seen. What was in view is listed in items 1 to 4. To write it, the author opened no row, report or scratch file of forward-1002, forward-1002ev, walk 2, forward-paper, a runner, the shadow or the canary, and computed no H5 outcome. Its inputs are this file, DEC-024, `tools/pump_structure_monitor.py`, the A3 re-pin note on PR #517 (`ARTIFACTS/lab/a3-repin-2026-10-09.md`), the program-upgrade review it copies, and the manager's relay of quant-proof's ruling and the owner's decision. **Line numbers below are those of this file on main at `ecdc7af`, before this amendment.**

**What was in view (disclosed, not hidden)**
1. **The readings.** [measured; the A3 re-pin note, section 5, and the program-upgrade review]
   - The review: a PostCompleteBuyEvent in the curve-completing transaction on 0 of 61 graduations before the 2026-10-08T16:20Z redeploy, and 12 of 61 after (windows 1/15, 3/14, 2/16, 6/16; pooled 0.197).
   - The monitor's `synthetic_share_high` at the daily run of 2026-10-09T07:11:06Z (job #445): **6/19 = 0.316**, no halt from that flag. At the dry check of 07:38:11Z: **7/19 = 0.368, a halt** against the 0.35 line. The two samples overlap in slot range 454788273 to 454793322, so they are not independent.
   - The H5 evidence for the rule (September) had 0 synthetic pools. This amendment is written with those two readings known, so it is not a rule fixed before the share was seen.
2. **The dry check is not the P2 run.** It ran once, at 07:38:11Z, on the unmerged pins of PR #517 (head `f654bdd` at the time of writing), writing to a scratch `--out` seeded with a copy of `daily.jsonl`. The official `/data/mal/structure-monitor/daily.jsonl` was only read; the note records its md5 and its 2-line length as unchanged. Besides `synthetic_share_high`, every other halt flag was clear, and `program_changed` was evaluated and clear. It is disclosed here and in every amendment that touches the synthetic share. It is not counted as the last A3 run before 10-10T00Z.
3. **Amendment 3's aggregate pre-window shadow outcomes were in view.** Shadow outcomes for pools with s0 before 2026-10-10T00 have been observable since about 2026-10-08T21:30Z (Amendment 3), and the aggregate of them, not split by any class, was available to the people who decided this exclusion. This amendment does not claim that none of it was seen. Those pools are in no counted window. The basis of the exclusion (item A2) does not include them.
4. **Attestation (made by the manager, who merges this amendment).**
   - No H5 outcome (a fill, exit, P&L, mean, CI, day sign, win or loss, or any field derived from one) has been split by synthetic class. No synthetic class, from the program-upgrade review or from the monitor classifier, has been joined to any shadow, canary or tape outcome, fill or P&L field. The review's synthetic mints were never joined to canary records or to shadow outcome records.
   - **One structure-only join exists, and it is disclosed.** On 2026-10-09 at about 07:50Z the manager started an outcome-blind structure measurement (a subagent; its shadow-field projection ran as MiScusi job #448; output under `/data/mal/hunt-1008/h5-work/synthetic-1009/`). It projected only `pool`, `mint`, s0, the trigger slot and the BOOST last slice from the shadow's `trigger` and `pool` records, and classified those pools as synthetic or not with the monitor classifier. It was for counts: triggers by class, s0 lag by class and BOOST timing by class. It read no outcome record and no P&L field, and joined the class to none. So synthetic class was joined to shadow trigger and pool structure fields for counts only, and never to any outcome or P&L field.
   - The basis (item A2) does not use job #448's numbers, and nothing below depends on them.

#### A. The population restriction

**A1. What changes.** Synthetic-migration pools are excluded from the deciding cell D and from every leg: the binding legs B1 and B2 and every report-only leg of section 13. Pools that cannot be classified (item B3) are excluded the same way. The trade set of every look is the rule's universe restricted to pools classified non-synthetic.
- **Replaces section 4, line 164** ("**Universe.** Exactly the rule's universe: every `complete` graduation whose canonical PumpSwap pool, the first V-range pool after `complete`, has V0 in [17.5, 17.7] SOL. s0 is that pool's first print."). New text: *The rule's universe, restricted to pools classified non-synthetic (Amendment 4, item B). s0 is the canonical pool's first print, as the rule says.*
- **Replaces section 4, line 198** ("**Synthetic-migration pools** (pool opens above the seed) stay in the universe. Their count is reported."). New text: *Synthetic-migration pools, as classified in Amendment 4 item B, are excluded from D and every leg. Pools that cannot be classified are excluded. Both counts are reported, per date (P6).*
- "Synthetic" in this file now means item B1's classifier, not "pool opens above the seed". The two are not claimed to be the same set.
- The two replaced lines are superseded where they conflict with this amendment. They are not edited in place, as in Amendment 1. Section 13's diagnostic "synthetic-pool count" (line 395) stands. Section 16 item 11's "synthetic migration (unused 0/10)" (line 514) is a statement of what September could not measure; item 1 above is the October disclosure.

**A2. A population restriction, not a retune.**
- **Unchanged:** the RULE block (the section 2 `sed … | sha256sum` check still gives `c66b1a59…9abe56c`, because the block is untouched), its trigger, Q* = 40 SOL, entry, exit at 330 s, costs and fail legs, the deciding cell and both binding legs, the correction, the V-range universe condition, sps, the windows, and the gate statistics of section 7.
- **What it does:** it removes a class of pools that the rule's evidence never contained (0 synthetic pools in September, section 16). It is a restriction of the population the frozen rule is read on. No parameter of the rule moves.
- **Basis: A3 and the program-upgrade review only.** The grounds are the monitor's structure readings (item 1) and the review's finding that the redeploy added synthetic migrations the rule's evidence did not contain. Amendment 3 (line 631) forbids re-scoping Look 1 "because of anything the canary or shadow shows". Nothing the canary or the shadow shows is a basis here. If any such thing were, this amendment would be void.
- **Cost, stated plainly.** The trade count falls by the synthetic share, about 0.20 to 0.37 on the readings in item 1 [inferred, not a forecast]. Section 15's power arithmetic was computed on September rows, which have 0 synthetic pools, so it applies per trade to the restricted population; it is not re-run, and with fewer trades the chance of a pass is, if anything, lower than section 15 says [inferred].
- **Counting floors.** P6's counts and section 11's "fewer than 100 triggers, or fewer than 5 dates with a trigger" are counted on the restricted population. The thresholds are not changed.

**A3. Multiplicity.** k = 1 and α are unchanged: α_1 = 0.020 and α_2 = 0.005 (sections 7 and 9). The restriction adds no arm, cell, look or α, and does not touch m. **The full-universe book (synthetic pools included) never decides.** It is not computed or reported before Look 2 has been read (item D1). If it is reported after that, it is report-only.

#### B. The classifier, the source order and unclassified pools

**B1. The classifier.** `post_complete_buy_seen` (`tools/pump_structure_monitor.py:482-490`, in the monitor blob `1ca0a88c…`). A pool is synthetic if and only if its first return value, `seen`, is true. That value is true on the PostCompleteBuyEvent discriminator `DISC_POST_COMPLETE_BUY` (`:115`) **alone**. The `mint_match` refinement is not used.
- It is applied to **the transaction that carries the mint's CompleteEvent** (the curve-completing transaction; in the monitor, `annotate_completion`, `:823` onward) **and to the pool's migrate transaction when the two differ**. The pool is synthetic if the event is seen in either. This matches the monitor's own class, "seen in the completing tx or in the migrate tx" (`:818`, `:833`). If either transaction cannot be found or read, the pool is unclassified (B3).
- By construction the discriminator-alone test can over-count (a mint-decode miss or a layout change counts as synthetic, `:484`). That direction removes pools. It never hides a synthetic one.
- The classifier code does not change. The monitor blob stays `1ca0a88cecf0853d94336ea046ba1a910b79f198`, as section 10 pins it.

**B2. The source order (fixed; never chosen by effect).** Both sources are read after the EXP-012 FINAL (A) is written, by the read tool, and neither opens an outcome. For every V-range pool, `getTransaction` (`maxSupportedTransactionVersion` 1) of the CompleteEvent transaction, and of the migrate transaction when it differs (B1), is tested with `post_complete_buy_seen`. A decoded tape `post_complete_buy` row (`observe/trade_decode.py:241`) can only mark a pool synthetic. The tape never settles non-synthetic. If a fetch fails after retries and the tape has no row, the pool is unclassified (B3).
- Why the tape cannot settle non-synthetic: the walker's `post_complete_buy_missing` flag (`tools/pump_history_backfill.py:515-523`) fires only when an hour saw the event and wrote no rows at all, so a partial loss leaves it false; `decode_extra_event` returns None on a short or out-of-window blob (`observe/trade_decode.py:225-228`), so a layout change would hide a synthetic pool; and forward-1002 was walked without event-V (line 168), so its hours probably carry no flag.
- The choice of source is never made by looking at a pool's trigger, P&L, day or any outcome.

**B3. Unclassified pools.** An unclassified pool is excluded and counted. **If more than 1% of the look's V-range pools (s0 in the look's counted window, before the synthetic exclusion) are unclassified, the look is NOT_DECIDABLE.** This is added to the section 11 NOT_DECIDABLE list. The classification is part of P6, so it is computed before any outcome. A refusal on it falls under section 11, Spending.

#### C. The A3 halt list and the precount

**C1. Section 11, line 328 and line 324.** The A3 halt is now **five** flags: `pins_changed`, `boost_disabled`, `boost_share_low`, `boost_last_slice_early` and `boost_budget_or_slices_changed`, or a core rule unevaluated on two consecutive daily runs. "Any A3 halt flag" in the NOT_DECIDABLE list (line 324) and "any A3 halt after the pass" in the no-live rules (line 347) mean these five.
- **`synthetic_share_high` is recorded and reported at every look. It never decides.** The monitor still computes it, because its blob does not change.
- P2 and section 11 read the five flags one by one from the record's `halt.flags`. They do not read `halt.any`, which also counts the sixth. A run on which `synthetic_share_high` is the only flag that fired counts as a run with no halt.
- Lines 329-330 (before 10-10T00Z: withdrawn; inside a counted window: NOT_DECIDABLE, no resumption) are unchanged and apply to the five.

**C2. P6 (section 10, line 311)** also prints **per-date synthetic and non-synthetic counts** of V-range pools (by s0 date), and the per-date unclassified count. Counts only. The trigger count per date is taken on the restricted population, and no trigger-by-class table is printed.

#### D. The seal

**D1. A breach.** Joining synthetic class to any H5 outcome (a fill, exit, P&L, mean, CI, day sign, win or loss, or any field derived from one), from any source, for any pool with s0 in a counted window, **before Look 2 is read**, is a breach of section 3 (line 146): it is recorded here, dated, and the read is reported compromised. A compromised read cannot support a live request. Counts of pools and triggers by class (C2) are not outcomes. Section 3.1's real-time observation of the canary's and the shadow's aggregate outcomes stands, and does not extend to splitting them by class.

#### E. Clarification: P2 is the last run (not a change)

P2 (section 10, line 296) names the test: **"The last A3 run before 10-10T00Z shows no halt"**, and no core rule unevaluated on two consecutive days. Section 11 (lines 328-329, "Before 10-10T00Z: H5 is withdrawn") is the consequence of that test. It is not a second, stricter test that every earlier run must also pass. So:
- **A reviewed re-pin plus a clean last run cures `pins_changed`.** The 07:11:06Z halt on `pins_changed` (job #445) is cured by PR #517, the re-pin (merged as `82cd674`), if the last official run before 10-10T00Z shows none of the five flags.
- **The reading cuts both ways.** A halt on any of the five flags in the last run before 10-10T00Z withdraws H5, whatever ran clean earlier.
- The same reading is recorded for EXP-022 (P2 at line 320 against line 358) in that file, before the 2026-10-10T06:41Z daily run.

#### F. The official P2 run: the protocol, declared before the run

The dry check (item 2) is not the P2 run. One more run is allowed, under this protocol, which is fixed here before it starts:
1. **Exactly one official run**, with default arguments and `--out /data/mal/structure-monitor/daily.jsonl` (the default output, written out), that is `python -m tools.pump_structure_monitor --out /data/mal/structure-monitor/daily.jsonl`, run as a MiScusi job.
2. **Code and pins:** `main` at the merge commit of PR #517, `82cd6746b0550758c36a26d3f27bf1a2c3540414`, where `tools/pump_structure_monitor.py` is the blob `1ca0a88cecf0853d94336ea046ba1a910b79f198` and `tools/pump_structure_pins.json` is the blob `7486f57f372e79c7d852f9cb70991d20043d4a8f` (`git rev-parse HEAD:<path>` is recorded in the job's start record).
3. **Start time (UTC): 2026-10-09T19:23:00Z. Backup time (UTC), used only under item 5: 2026-10-09T21:53:00Z.** Both are fixed in this declaration, after this amendment, EXP-022's clarification and DEC-024 Amendment 2 have merged (PR #517 already has), and before 2026-10-10T00:00Z.
4. **No other monitor run of any kind before 2026-10-10T00:00Z.** That covers scheduled, manual, dry, scratch-`--out` and `--write-pins` runs.
5. **An errored run counts as it stands, with no retry, with one exception.** If the official job printed no flag summary (it did not start, the host was down, or RPC was unreachable before `emit_summary`; an unreachable RPC returns before any flag is evaluated, `tools/pump_structure_monitor.py:1761-1763`, and `emit_summary` prints the flags before the record is written, `:1787`), exactly one backup run starts at the item 3 backup time, under items 1, 2, 4 and 6, with no further retry. If a summary was printed, that summary is the run, even if the file write failed. If the backup also prints no summary, the last run is the 07:11:06Z run (job #445, `pins_changed`) and P2 is not met.
6. Section C1 governs the reading: the five flags, one by one. The run's `synthetic_share_high` value is recorded and reported, and decides nothing.

#### G. Conditions for this amendment to take effect

It carries **quant-proof's OK on its final head** and **the owner's approval**: MiScusi notebook entry `n_xtknDqL-ychBNg` (2026-10-09), which the manager relayed; the author did not open the entry. It merges **before the official run (item F) and before 2026-10-10T00:00Z**. If it does not, items A, B and C do not apply, section 11 stands as pinned, and a halt on `synthetic_share_high` in the last run withdraws H5. The companion text is DEC-024 Amendment 2 (the executor and the shadow apply the same classifier at decision time).

**Not changed.** The rule, parameters, trigger, entry, exit, fill pricing, costs, legs, correction, gate statistics (every item of section 7), look schedule, futility, α, k = 1 and m, the pinned counting start (section 0), windows, hours read, the read tool's hour allowlist, the seal of section 3 apart from item D1, the CAP-PICK seal, and every NOT_DECIDABLE condition apart from item B3. EXP-022 is not amended here.

#### Amendment 4, Clarification 1 (2026-10-09, before 2026-10-10T00:00Z and before the official P2 run): how the read locates the two transactions B1 tests

**Why.** B1 defines the class by "the transaction that carries the mint's CompleteEvent" and the pool's migrate transaction. It does not say how the read finds them, and leaving that choice to the read tool's builder would mean making it later. This clarification fixes the search procedure now. It is not a re-scope.

**Prompted by.** The structure measurement disclosed in item 4 (job #448's projection, `/data/mal/hunt-1008/h5-work/synthetic-1009/REPORT.md`) found that a 10-signature window on the curve PDA missed the completing transaction in 20 of 160 graduations, because failed sniper transactions crowd it.
- Only that depth finding prompted this clarification.
- Its trigger counts by class are **not** a basis here or anywhere else in this amendment (item 4; Amendment 3, line 631).

**B4. Locating the transactions** (read time, after the FINAL; `getTransaction` with `maxSupportedTransactionVersion` 1; finalized commitment):
1. **The migrate transaction.** This is the transaction that carries the PumpSwap `CreatePoolEvent` for the pool, the same transaction that carries pump's `CompletePumpAmmMigrationEvent`.
   - If the tape has a row from that event, its signature locates the transaction.
   - Otherwise, test the transaction of the pool's s0 print first. Then call `getSignaturesForAddress` on the pool address with `before` = the s0 print's signature, paginating newest-first to a cap of **1,000 signatures**. The migrate transaction is the oldest successful transaction in that set that carries a `CreatePoolEvent` for the pool.
2. **The CompleteEvent transaction.**
   - If the migrate transaction carries the mint's CompleteEvent, it is also the completing transaction.
   - Otherwise, a tape `complete` row's signature, if present, locates the transaction.
   - Failing both, call `getSignaturesForAddress` on the bonding-curve PDA with `before` = the migrate transaction's signature, paginating newest-first to a cap of **1,000 signatures**. The completing transaction is the newest **successful** (`err` null) transaction in that set whose events include a CompleteEvent for the mint.
   - **Each located transaction must carry the event that defines it.** A located migrate transaction must carry `CompletePumpAmmMigrationEvent` for the mint, and a located completing transaction must carry the mint's CompleteEvent. A transaction that fails this check is not used, and the next source in the order is tried. If no source passes, the pool is unclassified (B3).
3. **The tape only locates.** In every case the class comes from `getTransaction` of the located transactions, tested with `post_complete_buy_seen` (B1, B2), on event blobs extracted by the monitor's `tx_event_blobs` (`tools/pump_structure_monitor.py:411`), which reads both `Program data:` logs and emit_cpi inner instructions. Not finding either transaction within its cap, or a failed fetch after retries, makes the pool unclassified (B3).
4. **Unchanged:** the B1 classifier, the B2 rule that the tape never settles non-synthetic, the B3 1% cap, every threshold, and items A, C, D, F and G.

**Live side.** DEC-024 Amendment 2, Clarification 1 states the decision-time version of this procedure. That version is bounded by the trigger time, not by the read's caps. It is not evidence and does not bind the read.

This clarification carries quant-proof's OK on its final head and merges before 2026-10-10T00:00Z.

### Pointer (2026-10-10; not an amendment): seal end state and Amendment 4 D1

This read's seal end states, and quant-proof's ruling that Amendment 4 item D1 lifts at EXP-024's end state (the full-universe book stays report-only; after a Look 1 PASS a synthetic split cannot widen the live universe), are recorded in [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md), Rules, "Clarification, 2026-10-10" (rulings (3a) and (3b)); that text changes no rule, data, bar or timing of this read and releases no data.

### Amendment 5 (2026-10-10T13:06Z; it must merge before 2026-10-16T00:00Z): the P3 and P4 record (tool pins, merge commits, E0-H5), the extractor's V map, and parse_failed hours

Outcome-blind. To write it, no row, report or scratch file of forward-1002, forward-1002ev or walk 2 was opened, and no October label, H5 trigger, fill, exit or P&L was computed or read. Its inputs are the repo at main `742d7c9` (with #476 merged as `42c1e41`, #562 as `0ef32e2` and #566 as `737c307`), the MiScusi records of jobs #538, #546 and #551, and the E0 record files those jobs wrote on exploration day 2026-09-20. It records what section 10 (P3, P4) and section 12 require. It changes no rule, parameter, window, leg, threshold or refusal.

**Status.** Every blob below is the blob on main `742d7c9` (except `inputs`, which Amendment 6 and then Amendment 7 replace in this same A line), from `git ls-tree origin/main` at 2026-10-10T13:06Z. This amendment takes effect when quant-proof has posted OK on its final head and it merges before 2026-10-16T00:00Z. If either misses, section 10 P3 withdraws H5, and section 11 "Spending" (from 2026-10-10T00Z) spends Look 1. Every blob in A is also unchanged on main `b63555b` (merged into this branch; checked 2026-10-10T13:34Z) and on main `dbf2780`, which adds #568 (merged into this branch; `git rev-parse origin/main:<path>` equals each A blob for all eight keys, and `git diff --name-status dbf2780^ dbf2780` lists only the two new files `tools/h5_forward_vmap.py` and `tools/test_h5_forward_vmap.py`; checked 2026-10-10T13:55Z). Items D (the forward vmap) and E (`parse_failed` hours) record the manager's decisions, declared before any October data is read.

**What would change a pin.** The pins are the eight blobs of the A line.
- A change to the read tool, for example to apply item E in code, changes the `read_tool` blob. That needs a new A line, a P4.2 E0 re-run at the new blob, and an amendment merged before 2026-10-16T00:00Z. This amendment makes no such change: E is applied from P6's counts.
- The vmap producer (D) is a NEW file, `tools/h5_forward_vmap.py` (#568). It is not an A-line key and it edits no pinned file, so no pinned blob changes.
- Any edit to `tools/forward_v_join.py` or `tools/h5_forward_extract.py` changes the `v_join` or `extractor` blob. That breaks the pins and forces an E0-H5 re-run at the new blobs, a new A line and an amendment before the same deadline.

#### A. The pin line (section 10 P4 item 3; section 12 "the E0-H5 blobs match")

`integrity()` in `tools/boostfloor_read.py` runs, in order: `check_count_start`; `parse_p3_pins`, which refuses unless exactly one line of this file starts with the prefix and gives all eight keys as `key=path@<40-hex blob>`, seven of whose paths are fixed in `P3_PIN_PATHS`; `check_clean` (this file, the monitor and every pinned path clean against HEAD); `check_pins` (each blob equals the look worktree's file); `check_monitor` (`tools/pump_structure_monitor.py` at `1ca0a88c…`, Am.4 B1, which is also its blob on main `742d7c9`); and `check_frozen` (section 2).

```
EXP024_P3_PINS: read_tool=tools/boostfloor_read.py@fefe0c156ce75839cf973da82e408f0dd9454678 score_module=tools/boostfloor_score.py@e63cca50b367f7d9b8bdf782429f4f0618c5cc87 latency_curve=tools/latency_curve.py@c194134b199d2d772a57d5a6a6cb3c5c3d25d669 paper_curve_math=tools/paper_curve_math.py@42daf5adcdc1532cb29a6f5079997d25b10c86ea extractor=tools/h5_forward_extract.py@92d070f53d521445e5a4e2c84c8f871a1026b026 v_join=tools/forward_v_join.py@8b60e5bf5fc8a45b28a587b96a01aa20a07df2ad synthetic_class=tools/synthetic_class.py@930c8caa5d55a68e7886828b0e171acf107a2af1 inputs=tools/boostfloor_inputs.py@edf0d2d7bc7aec0f7c0c2f95f0e9df7ad95eef76
```

| Key | Path | Blob on main `742d7c9` | E0 run at this blob |
| --- | --- | --- | --- |
| `read_tool` | `tools/boostfloor_read.py` | `fefe0c15…` | job #551 at `3f2e26c`: the record's `read_tool_blob` |
| `score_module` | `tools/boostfloor_score.py` | `e63cca50…` | job #551 at `3f2e26c`: the same blob in that tree |
| `latency_curve` | `tools/latency_curve.py` | `c194134b…` | job #546: the record's `blobs` |
| `paper_curve_math` | `tools/paper_curve_math.py` | `42daf5ad…` | job #546: the record's `blobs` |
| `extractor` | `tools/h5_forward_extract.py` | `92d070f5…` (#566) | job #546 at `810221d`: the record's `blobs`, `extractor_dirty` false |
| `v_join` | `tools/forward_v_join.py` | `8b60e5bf…` | job #546: the record's `blobs` |
| `synthetic_class` | `tools/synthetic_class.py` | `930c8caa…` | no E0 covers it (classify only) |
| `inputs` | `tools/boostfloor_inputs.py` | `edf0d2d7…` (Amendment 7; before it `482c73b0…` from Amendment 6, and on main `742d7c9` `adef463e…`) | P4.2 E0 re-run at the Amendment 7 head: MiScusi job #637 (`j_moeSkSMA-gqXsg`) at `cbf6ae3bb2643110c72aa2f838736657ce679e0c`: the job log names `inputs` blob `edf0d2d7…`, verdict PASS, `integrity()` rc 0 (Amendment 7 D; C, P4.2). Before it, P4.2 E0 re-run at the Amendment 6 head: MiScusi job #593 (`j_YaAXrHjxM3U5Eg`) at `594919da06a5e7cbb6b8cd049864df87601d0284`: the job log names `inputs` blob `482c73b0…`, verdict PASS (C, P4.2). Job #551 at `3f2e26c` ran the old blob `adef463e…`. |

**Check.** At `b1024ad`, the head that first added this amendment (main `742d7c9` merged in; there the branch changed only the two EXP files), `python -m tools.boostfloor_read pins` printed the seven fixed paths with the blobs of the A line as first written (`inputs` `adef463e…`) and accepted the line, and `integrity()` passed on mal-research-0 (count start, pin line, clean, pins, monitor, frozen). At `594919d` the branch also changes `tools/boostfloor_inputs.py` and `tools/test_boostfloor_read.py` (Amendment 6). There, `python -m tools.boostfloor_read pins` printed the seven fixed paths, `inputs` `482c73b0…` among them, and exited 0 (job #593). The PR body records the output.

#### B. Merge commits (section 10 P3)

| PR | What | Final head | Merge commit | quant-proof OK on the final head |
| --- | --- | --- | --- | --- |
| #476 | read tool (forward mode), #476 module, inputs producer | `3f2e26c` (job #551's code; same three blobs as main) | `42c1e41ff28ec068bc0315a4bc247c35abab68ff` | [OK at `3f2e26c`](https://github.com/vaanai/MAL/pull/476#issuecomment-6097758381) |
| #562 | forward extractor and E0-H5 | `38c3c73` | `0ef32e254ac9f7c85bb752e37f6418afa761729d` | [OK at `38c3c73`](https://github.com/vaanai/MAL/pull/562#issuecomment-6097303978) |
| #566 | extractor: keep null-V0 pools, fix the `cp1` v pairing | `810221d` (job #546's code; same extractor blob as main) | `737c3078e4bb27def876cdc16c35fa6bdaa5faaf` | [OK at `810221d`](https://github.com/vaanai/MAL/pull/566#issuecomment-6097536311) |

Each link is the manager's merge comment on that PR, which records quant-proof's OK at the head named.

**Catalog (section 12).** On main `742d7c9` the read tool does not call `mal_catalog.check_read`. The only mention is its docstring, which says it allowlists exactly the section 3 hours. So #465 is not a precondition.

#### C. E0-H5 records (section 10 P4), exploration day 2026-09-20, fast-pool-0918

- **VIEW.sha256** of the pinned day: `05486f70f53c7ef848b151f40d310ecc16e3ef517ff98ed7d4348250a32effe8`, the `view_sha256` of jobs #546 and #538.
- **P4.1, extractor: MiScusi job #546. This is the E0-H5 record the forward run uses.** It ran `h5_forward_extract e0` on mal-research-0 at code `810221d6147f26d6031fcc1a6a535f75d6cce85a` (the #566 head, merged as `737c307`). The record's `blobs` give the extractor `92d070f5…`, `forward_v_join` `8b60e5bf…`, `latency_curve` `c194134b…` and `paper_curve_math` `42daf5ad…`, all equal to A, with `extractor_dirty` false. Resources: an 8 GB cap and an 8.0 GB peak, equal to the cap, so the forward run uses at least 12 GB. Verdict: **PASS**.
  - meta: rows_md5 `bf9c1f61ed80c1c32a0b05d70f849751` equals the reference, 815/815 rows.
  - paths: rows_md5 `d6e0b8e345757b7f3fc8b8cb9fdb4e41` equals the reference, 5,383,117/5,383,117 rows.
  - Order check: ok. 25 groups and 269 rows moved, all inside open ties. 0 groups moved outside open ties, and the (mint, slot) sequence has 0 differences.
  - `canon_null_v` 0, `canon_null_v_multipool` 0, `vmap_pools_null_v_kept` 321. The E0 vmap is `/data/mal/pumpswap-virtual/pool_v_0909.json`, sha256 `70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e`.
  - Record: `/data/mal/exp024/e0-h5-810221d/E0-H5.json`, sha256 `ec4eb87428528a0c37430309b2954cf6d0011e2ce9ab6a1a4b11b69444702d0f` [measured 2026-10-10T13:05Z; equals the job log].
  - Forward mode accepts only an E0 record written by the extractor blob it runs, so this record, not #538's, goes to `--e0-record`.
- **Job #538** is kept as history. It ran at `38c3c73` (the #562 head) with extractor blob `da81a2fa…`, which is no longer the pinned blob. It had the same verdict, md5s and row counts. Record: `/data/mal/exp024/e0-h5-38c3c73/E0-H5.json`, sha256 `67ff0c7a0013a4ba5cc5f0fc38368798b5bc92796c802f009cf54869eedb9ee8` [measured 2026-10-10T13:05Z].
- **P4.2, read tool: MiScusi job #551.** It ran `boostfloor_read e0` on mal-research-0 at code `3f2e26c3958a327c24bdd134c19ffdda9079423f` (the #476 head after the quant-proof r4 edits) with an 8 GB cap, a 1.5 GB peak, and exit 0.
  - `equal` true: n_tool 288 = n_ref 288.
  - md5_tool = md5_ref = `328264a4f22176f7048d9f7719ae930b`, over (mint, leg, stake, pnl rounded to the lamport) of the `boostdip_frozen_conf.parquet` rows with `H == 'end'` on 2026-09-20.
  - `conf_sha256` `a4798efaa362122e1866db6ca0c34c4b75a17aba6f9a6ffd5940515808c977ca`; `read_tool_blob` `fefe0c156ce75839cf973da82e408f0dd9454678`. The job log prints `inputs` blob `adef463e0258b522318eb2c8b9051516b4776685`. Both equaled A as first written; Amendment 6 replaces the `inputs` blob, so #551 no longer covers `inputs`, and the P4.2 E0 re-run at the Amendment 6 head is job #593 (the last sub-bullet of P4.2).
  - Record: the job's `e0.json` in its MiScusi output directory, `/home/claude/.miscusi/jobs/j_vz_rlwvrMJWXSQ/out/e0.json` on mal-research-0, sha256 `839548727f5b29b256b0f892a441704fd7968fe688e8829ad30023bc648c852c` [measured 2026-10-10T13:34Z; its content equals the JSON the job log prints].
  - It replaces job #540 (code `b3dbc80`, read-tool blob `f92abdd6…`, same n and md5), whose blob is no longer pinned.
  - **Re-run at the Amendment 6 head: MiScusi job #593 (`j_YaAXrHjxM3U5Eg`).** It ran #551's command unchanged on mal-research-0 at `594919da06a5e7cbb6b8cd049864df87601d0284` (role exploration, day 2026-09-20 only, 8 GB cap, 525 MB peak, exit 0), then `boostfloor_read pins` (rc 0). Verdict PASS: n_tool 288 = n_ref 288; md5_tool = md5_ref = `328264a4f22176f7048d9f7719ae930b`; conf_sha256 `a4798efaa362122e1866db6ca0c34c4b75a17aba6f9a6ffd5940515808c977ca`; read_tool_blob `fefe0c156ce75839cf973da82e408f0dd9454678`; the job log prints `inputs` blob `482c73b0b1a51ed438ba06584310389d2fbc1721`. Record `/home/claude/.miscusi/jobs/j_YaAXrHjxM3U5Eg/out/e0.json`, sha256 `839548727f5b29b256b0f892a441704fd7968fe688e8829ad30023bc648c852c`, byte-equal (cmp) to #551's record; `pins.json` sha256 `a163adc150e36614e7f67b717ea197f907a9eb299bab9b4bc378841de9f27b51`. `boostfloor_read e0` does not import `tools/boostfloor_inputs.py`, so its output is unchanged by construction. This E0 covers the new `inputs` blob procedurally, because the log names the blob, but it does not exercise line B. Line B's behavior is covered only by the unit tests in `tools/test_boostfloor_read.py` (76 tests, OK at 594919d).
  - **Re-run at the Amendment 7 head: MiScusi job #637 (`j_moeSkSMA-gqXsg`)** at `cbf6ae3bb2643110c72aa2f838736657ce679e0c`, #593's command unchanged, with `inputs` blob `edf0d2d7bc7aec0f7c0c2f95f0e9df7ad95eef76`: verdict PASS, the same n, md5s and conf_sha256, record byte-equal to #593's (sha256 `83954872…`), pins rc 0, `integrity()` rc 0. Details in Amendment 7 D.
- **P4.3, blobs:** the extractor, the read tool, `tools/latency_curve.py`, `tools/paper_curve_math.py` and #476's module are the A line, and the E0 runs above ran at those blobs.

#### D. The extractor's `--vmap` for the forward run

**What the extractor uses it for.** In forward mode, `--vmap` does one thing: it chooses the canonical pool, which is the first V-range or null-V pool after `complete` (#566). The value is copied into `meta.v`. It is not an Am.1 V0 source (`VMAP_ROLE`). The read tool uses it only in P5 fetch planning and `classify` (`VSources.vmap_fallback`), never in `precount` or `look`.

**The manager's choice (2026-10-10).** The forward vmap is Am.1's first source. Every PumpSwap pool with a print in the extract's hours gets one entry:
- the joined forward-1002ev `virtual_quote_reserves` at that pool's **first print** (a PRE-trade value). The first print is the pool's earliest PumpSwap row in forward-1002's 146 Look 1 hours [2026-10-09T23, 2026-10-16T01), in the order the extractor gives a path. It is the extractor's s0 print except for a pool that printed before its mint's `complete`;
- **null** where the join gives that print no V: the fallback list, a refused, bad or unwalked ev hour, an ev row without V, or two ev rows that give the print different V (`null_ev_conflict`); also a first print with no (slot, signature, event_index) key (`null_first_print_unkeyed`);
- **null** where the first print is uncertain (`null_first_print_uncertain`): the pool is first seen in a forward-1002 hour that is not usable, stops on a read error or holds an unreadable line, or in the hour right after such an hour; or a row of the pool has no integer slot. A pool first seen when neither its own hour nor the hour before it is such an hour keeps its V, because the extractor's hole rule already drops a mint whose migration hour, or the next hour, is unusable.

Null-V0 pools stay in, per #566: `load_vmap` keeps a null as V-unknown, the pool reaches meta with `v` null, and the read tool resolves it by the Am.1 order, else by the section 4 missing-V rule. A known V outside [17.5, 17.7] SOL is dropped, as before.

**Did main `742d7c9` support it? Not end to end. #568 adds the missing tool.**
- The extractor reads a static file shaped `{"v": {pool: lamports|null}}` (`load_vmap`).
- `forward_v_join join --emit v` writes the V fields per matched row, keyed by (slot, signature, event_index), plus the fallback list. It writes no per-pool map.
- No tool on main `742d7c9` reduces the join output to `{pool: V at the pool's first print | null}`. `tools/h5_forward_vmap.py` (#568, below) does.

**So, what is used: the map that #568's producer writes.** The forward extract runs with the chosen vmap from a small, outcome-blind producer. That producer reads the P5(a) join output and the forward-1002 PumpSwap prints after the FINAL, writes the map, prints counts only (pools, null-V pools) and records the map's sha256. The forward manifest records it as `vmap_sha256`. **The producer (merged).** #568, `claude/h5-forward-vmap`, merge commit `dbf27806a54eb04c3a09faeee364d356b670011c` (`dbf2780`). It adds two new files and edits no pinned file: `tools/h5_forward_vmap.py`, blob `55bcd9b023182d04bc083b9eeebda65a1786a710`, and `tools/test_h5_forward_vmap.py`, blob `3a8fb010808da9f7878a77f1d1e539e4fb86040c` (`git rev-parse origin/main:<path>`, 2026-10-10T13:55Z). Its mode `ev` writes the map chosen above. Both modes refuse before opening anything unless the EXP-012 FINAL marker is in the external FINAL ledger. `ev` then refuses unless:
- `--vjoin` is named `vjoin` (`p5/vjoin`), so that the line A check below reads the P5 dir's own `cross_source.json`;
- `p5/cross_source.json`, if it exists, records `line_a_pass: true` (before P5 runs the file does not exist, and `ev` runs);
- `join-report.json` is the join of [2026-10-09T23, 2026-10-16T01) on `/data/mal/blocks/forward-1002`, lists exactly those 146 hours, and records every decoder pin ok;
- every `v-<hour>` file is for a usable hour and hashes to the report's sha256, no refused hour has one, and no other `v-*` file is in the dir.

The map's sha256 is recorded in the P6 precount amendment. The producer is not an `integrity()` key, so the A line does not change.

**If line A fails [the manager's declared choice, 2026-10-10T13:43Z, before any forward-1002/forward-1002ev row is read].** Am.1 then does not use forward-1002ev for Look 1, so the `ev` map is not used either.

**(a) The fallback vmap.** It is #568's `gettx` mode (`h5_forward_vmap gettx`). Per pool, it takes:
1. **source 2:** the P5 `getTransaction` record of the pool's first print, accepted by the rule of `boostfloor_read.load_gettx` (status ok, `fields_equal` true, an integer `virtual_quote_reserves`, and the record's content key equal to the tape row's) plus two checks `load_gettx` does not make: the decode's pool equals the pool, and exactly one record has the print's (slot, signature, event_index) key (a print with two records is rejected, counted as `gettx_record_rejected`);
2. else **source 3:** the pool's integer V0 in `account_v0.json`;
3. else **null** (`null_no_source`).

A pool whose first print is uncertain (see above) skips source 2 and still takes source 3, which is the pool's V0 and not a print's V; if it has none, it is null (`null_first_print_uncertain`). There is no all-null map. At main `dbf2780` the mode refuses, after the FINAL check and before it reads a base row, unless `p5/cross_source.json` exists and records `line_a_pass: false` (`true` refuses, and so does a file with no `line_a_pass`), `p5/gettx_v.jsonl` exists and hashes to the `gettx_sha256` that file recorded, and `p5/account_v0.json` exists. Mode `ev` refuses once `p5/cross_source.json` exists and does not record `line_a_pass: true`, so it refuses after line A has failed.

**(b) The order if line A fails: one fixed pass, no loop.**
1. `h5_forward_vmap gettx` on the original P5 dir;
2. re-extract into a new dir E2;
3. re-run `boostfloor_inputs` P5's s0 fetch and `account` on E2 into a new P5 dir P5b. Line A is not judged again: ev is already out (see (b3));
4. `h5_forward_vmap gettx` on P5b;
5. re-extract into E3;
6. classify and P6 on E3.

Each re-extract runs with the map just written and job #546's E0 record, and its manifest's `vmap_sha256` is that map's. After step 5 the first extract and E2 are not used. After this pass, any pool still lacking a source-2 or source-3 record takes section 4's missing-V rule. It is counted and reported in P6 as `canon_null_v` and `canon_null_v_multipool`. The credits the re-fetch uses are recorded.

**(b1) New dirs at fixed paths [the manager's declared choice, 2026-10-10T13:55Z, before any forward-1002/forward-1002ev row is read].** The pinned tools take no path flag. `boostfloor_inputs` and `boostfloor_read` build every path from `Layout.for_look(ROOT, 1)`, with `ROOT = /data/mal/exp024`:
- `boostfloor_inputs p5` writes `/data/mal/exp024/p5/gettx_v.jsonl`, `cross_source.json`, `line_a_sample.jsonl` and `p7_sample.jsonl`, and refuses if one of them exists ("P5 runs once"). It reads the join at `/data/mal/exp024/p5/vjoin` and the extract at `/data/mal/exp024/look1/extract`;
- `boostfloor_inputs account` reads `/data/mal/exp024/p5/account/map.json` and writes `/data/mal/exp024/p5/account_v0.json`, a new file only;
- `boostfloor_read classify` reads the extract at `/data/mal/exp024/look1/extract`, writes `/data/mal/exp024/p6/classes-look1.jsonl`, and refuses if it exists ("classes are written once"); `precount` writes `/data/mal/exp024/p6/precount-look1.json`.

So in (b), "a new dir" means renaming the existing directory aside, then re-running the pinned tool at its fixed path: `mv <dir> <dir>.pass1`, and a second rename of the same directory is `.pass2`. The directories are `/data/mal/exp024/p5` (for P5b), `/data/mal/exp024/p6` (for a second `classify`) and `/data/mal/exp024/look1/extract` (for E2 and E3, which `p5` and `classify` read only at that path). Nothing is deleted, the renamed directories are kept for the audit, and each rename is logged with `date -u` in the read record.

**(b2) `account` before `gettx` [the manager's declared choice, 2026-10-10T13:55Z, before any forward-1002/forward-1002ev row is read].** If line A fails, `account` runs on the original P5 dir first (as in order (c)), so step 1's `gettx` has `account_v0.json`; `gettx` refuses without it.

**(b3) Line A is judged once [the manager's declared choice, 2026-10-10T13:55Z, before any forward-1002/forward-1002ev row is read].** Line A is judged only on the original P5. If the re-run P5 (P5b) records `line_a_pass: true` in its own `cross_source.json`, `gettx` on P5b would refuse. In that case the second pass stops: `classify` and P6 run on E2 (step 2's extract), and pools lacking a source-2 or source-3 record take section 4's missing-V rule, counted in P6 (`canon_null_v`, `canon_null_v_multipool`). There is no switch back to ev. If P5b records `line_a_pass: false`, steps 4 to 6 run as written.

**Reporting [pre-declared; outcome-blind; counts only].** P6 reports the forward extract's `canon_null_v` and `canon_null_v_multipool` (job #546's E0-H5 record gave 0 and 0), with the producer's counts (pools, pools with V, nulls by reason) and the map's sha256.

Not used, with reasons:
- `pool_v_0909.json` is the E0 map, a 2026-09-09 snapshot. October pools are absent from it, and the extractor drops absent pools.
- The pool-account map from `exp012_forward_vmap fetch --new` is the only file in the extractor's shape that main can write for October pools. Its raw `v` is the stored V at fetch time (at or after 2026-10-16T00Z), pending counters included. It is not V at s0, so it would choose canonical pools by a later state. The `exp012_forward_vmap fetch --new` fallback is not used: the producer merged in #568 (`dbf2780`). (The same fetch still feeds source 3 through `account_v0.json`. That file holds the pool's V0 from `exp012_forward_vmap._v0`, not this raw `v`.)

**(c) The order if line A passes [the manager's declared choice, 2026-10-10T13:43Z, before any forward-1002/forward-1002ev row is read].** Each step needs the one before it:
1. `forward_v_join join` (P5(a));
2. `h5_forward_vmap ev`;
3. `h5_forward_extract forward --vmap`, with job #546's E0 record;
4. `classify`;
5. P5 (`boostfloor_inputs p5`), which plans its fetches from the extract;
6. `account` (`boostfloor_inputs account`);
7. P6 `precount`.

This puts `classify` before P5. `boostfloor_read classify` reads no P5 file (it builds its pools with the extractor's vmap V0 only), and `boostfloor_inputs p5` loads the classes file `classify` writes.

All of these run after the FINAL (A), and none computes an outcome. If line A fails at step 5, the pass in (b) applies.

#### E. Disclosure: parse_failed hours

**How the two tools differ.** In forward mode, the extractor drops an hour when duckdb cannot parse its raw file (strict lines). It records the reason `parse_failed`, removes the hour's parquet, and writes the reason to `manifest.json` (`hour_reasons`, `reason_counts`). Its hole rule then excludes every mint whose migration hour, or the next hour, is unusable.

The read tool on main `742d7c9` does not read that manifest. Its good and bad hours come from `_good_bad`, which calls `tools/forward_v_join.hour_state(..., strict=True)` on forward-1002. `hour_state` checks that the hour is sealed, its last verify line, the trades sha256, duplicates and `bad_lines`. It does not parse the file with duckdb.

**The effect.** Suppose `hour_state` calls an hour `ok` but the extractor dropped it as `parse_failed`. Then the read tool counts that hour as good in section 4's "more than 5% of the look's window hours are bad" test, yet the hour contributes no meta or paths rows. The bad-hour share is understated by the number of such hours. Pools with s0 in that hour, or whose window runs into it, are missing from the universe without being counted as excluded.

**Reporting [pre-declared; outcome-blind; it adds nothing to a verdict].** The P6 precount amendment records:
- the extractor manifest's `reason_counts`;
- the number of hours that `hour_state` calls `ok` and the manifest calls `parse_failed`, as two counts: (i) over the 144 `COUNT_HOURS` `[2026-10-10T00, 2026-10-16T00)`, the span of the 5% test below; (ii) over the 146 `READ_HOURS` `[2026-10-09T23, 2026-10-16T01)`, the span the extractor covers.

**The manager's decision (2026-10-10; declared before any October data is read; fail closed, R1).** An hour with any `parse_failed` line counts as a BAD hour. The pinned read tool does not read the manifest, so the rule is written here and applied from the P6 count, with no read-tool change. The pinned 5% test measures over the 144 `COUNT_HOURS`, not the 146 `READ_HOURS`: `_good_bad` lists the `COUNT_HOURS` whose state is not `ok`, `precount` divides that count by `len(COUNT_HOURS)` (recorded as `count_hours`, 144), and `not_decidable_reasons` tests that share against 5%. So the numerator of section 4's 5% test is the read tool's bad count hours plus the `COUNT_HOURS` that `hour_state` calls `ok` and the manifest calls `parse_failed`, over `count_hours`. If that share is more than 5% (more than 7.2 of the 144 hours, so 8 or more), Look 1 is NOT_DECIDABLE, and section 11 "Spending" applies. A `parse_failed` hour outside the `COUNT_HOURS` (2026-10-09T23 or 2026-10-16T00) adds nothing to the numerator. If the count is 0, the rule changes nothing.

Disclosed, not covered by the count: the extractor's hole rule already removes every mint whose migration hour, or the next hour, is `parse_failed`, but a later `parse_failed` hour inside a trade's span is not visible to the pinned read tool, which would treat that trade as covered. This case is triggered by the 146-hour count (ii), not the 144-hour count (i), because a trade from 2026-10-15T23 can run into 2026-10-16T00, which is a `READ_HOUR` outside the `COUNT_HOURS`. If the 146-hour count is not 0, that case needs a ruling before any outcome.

#### F. Not changed

Nothing else changes: the rule, the parameters, the universe (apart from the #566 null-V decision recorded in D), the trigger, the pricing, the costs, the legs, the gate statistics, the look schedule, futility, multiplicity, the kill rules, the refusals and the section 0 counting start. This amendment adds no reader and opens no sealed block.

### Amendment 6 (2026-10-10T14:16Z; it must merge before 2026-10-16T00:00Z and before any look's P7 sample is drawn): P7 line B, buy side, per the quant-proof ruling QP-P7-1010

Outcome-blind. It follows the quant-proof ruling of 2026-10-10 (about 14:00Z), `/data/mal/hunt-1008/c1nf-verify/QP-P7-1010.md`, sha256 `bf298d8ad516bbc69b59bbba6c32dfcbde5f3cd2a3a5efd4fc17b85033a2bafc`, items 2 and 3. To write it, no row, report or scratch file of forward-1002, forward-1002ev, walk 2, forward-paper, a runner, the canary or the shadow was opened, and no October label, H5 trigger, fill, exit or P&L was computed or read. Its inputs are the ruling and the manager's brief that quotes the tip-check counts below; this text copies those counts and did not reopen the job outputs.

#### A. The rule (the ruling's text, quoted)

> **P7 line 1 (EXP-025) / line B (EXP-024), buy side, amended [pinned]. Sells unchanged.**
> 1. *Comparable buys.* A sampled buy is comparable only if it has no `zero_sol` and its `ix_name` is exactly `buy` or `buy_v2`. Every other buy is in neither denominator, with cause `buy_exact_quote_in` (prefix), `no_ix_name` (missing/null/empty), or `ix_not_listed` (any other name, `multi_hop_swap` included). EXP-025 decides from the sampled tape row before any fetch; EXP-024 decides from the raw decode's `ix_name` (forward-1002 rows carry none; it is the instruction name and carries no outcome). Counts are printed per cause and, within `ix_not_listed`, per name.
> 2. *Law.* A comparable buy matches if the raw event's `pool_quote_amount` is within tolerance of `ceil(Q·token_raw / (base_reserve − token_raw))`, in integers `-((-Q*token_raw) // (base_reserve - token_raw))`. Q = `quote_reserve_mapped + V0` (EXP-025; vault + V(t) with V0 = 0 in the tip check) or `quote_reserve + V` (EXP-024); `base_reserve` and `token_raw` are the raw event's. `base_reserve <= token_raw` is a miss. This **replaces** the forward law for buys; the forward law is not an alternative.
> 3. *Tolerance.* Constants unchanged: 1 bp (EXP-025 of actual; EXP-024 of the law, as now) or 2 units, the units now lamports on both sides.
> 4. *Unchanged:* sell law and match, the 1,000-print draw and frame, the 99% bars, EXP-025's top-up to 100 comparable buys (its candidates follow item 1), unresolved = miss (EXP-025), and the consequences (R14; EXP-024 both looks NOT_DECIDABLE).

**For EXP-024 this replaces the buys bullet of Amendment 1's line B** ("buys, excluding every buy whose `ix_name` starts with `buy_exact_quote_in` ... A buy with no `ix_name` is not comparable"). The sells bullet, the print population (the 300 cross-source prints plus P7's 1,000 sampled prints), the 99% bar per side, "a side with no comparable event fails that side", and "if either side fails, P7 fails and both looks are NOT_DECIDABLE" stand.

**Unresolved = miss (the ruling's "Also needed"; it can only remove support).** Every line B print is scored. A print with no `gettx_v.jsonl` record, a record whose status is not `ok` (`absent`, `fetch_failed:*`, `no_raw_ref`), a decode with no buy/sell side, or a comparable event whose law fields (`quote_reserve`, `base_reserve`, `virtual_quote_reserves`, `pool_quote_amount`, `token_raw`) are not all integers is a comparable **miss**. Its side is the decode's when there is one, else the tape side; a print whose tape side is unknown is a miss on both sides. An unresolved buy cannot be excluded, because its `ix_name` is not known. Before this amendment `p7_check` dropped sampled keys with no record, `line_b` skipped every status other than `ok`, and a comparable event with a non-integer field was "not comparable".

#### B. The code (the `inputs` pin)

`tools/boostfloor_inputs.py` goes from blob `adef463e…` (main `742d7c9`) to `482c73b0b1a51ed438ba06584310389d2fbc1721`. The single A line of Amendment 5 is updated in place, because `parse_p3_pins` refuses a second line. No other A-line key changes.
- `buy_exclusion` decides from the raw decode, in this order: `zero_sol` (the "no `zero_sol`" of item 1), `buy_exact_quote_in` (prefix, v1 and v2), `no_ix_name` (missing, null or empty), `ix_not_listed` (any other name, compared exactly, so `Buy` is not `buy`). `buy_law` is the item 2 integer law and returns no value (a miss) when `base_reserve <= token_raw`. `within_b` is the unchanged tolerance: within 2 lamports, or within 1 bp of the law.
- `line_b` output adds, for buys, `excluded_by` (per cause), `ix_not_listed_by_name`, `by_ix_name` (n and matches for `buy` and `buy_v2`, reported separately as ruling item 4 asks), `unresolved` per side, and `side_unknown`. The `p7` mode prints those counts and the file sha256 to stdout. No id is printed.
- `p7_check` scores the union of the line A sample and the P7 sample, taking each key's tape side from `p7_sample.jsonl` first, then `line_a_sample.jsonl`. P5 now writes `isbuy` on each `line_a_sample.jsonl` row (from the extract's `isbuy`, as for `p7_sample.jsonl`). P5 has not run.
- The tier lines (section 10 P7), line A, P5's plan and fetch, the account, BOOST PDA and E1 modes are unchanged.
- Tests (`tools/test_boostfloor_read.py`, the ruling's shared cases): a dust exact-out buy that the forward law misses hits under the inverse law; exact-in buys hit (`buy` and `buy_v2`, including about 4,898 lamports); 5 bp of fee inside `pool_quote_amount` misses; an LP-sized (20 bp) offset and a gross-vault column (3% above Q) miss on buys, and both miss on sells; `base_reserve <= token_raw` misses; `multi_hop_swap`, an unknown name, the exact-in family, a missing, null or empty name, and `zero_sol` are excluded with per-cause and per-name counts; unresolved prints (no record, `absent`, `fetch_failed`, a null V) are misses on their side; and `p7_check` keeps a sampled key whose record is missing.

**What this needs.** A new `inputs` blob means the P4.2 E0 (the #551 form, `boostfloor_read e0`) is re-run at this amendment's head and recorded in this file before 2026-10-16T00:00Z (ruling item 2, "EXP-024"). `python -m tools.boostfloor_read pins` must exit 0 on the head that merges. Done: job #593 at `594919d` (Amendment 5 C, P4.2); pins rc 0.

#### C. Disclosure (ruling item 3)

- **What was run.** An outcome-blind law-match check on tip-tape prints, which the tip-follower event-V runbook (steps 7–8) required: MiScusi jobs #573 (crashed, no result), #576, #583, #585, #586 and #587. Window `[2026-10-10T13:17:18Z, 13:35:00Z)`. 1,174 Helius credits in total.
- **Counts (#576).** Sells 400/400 within tolerance. Comparable buys under the old pinned rule 165/176 (0.9375), with 424 excluded buys (400 + 176 + 424 = 1,000). Of the 11 buy misses, 9 were `multi_hop_swap` (about 5 bp ×8, about 100 bp ×1) and 2 were dust exact-out `buy` prints (1.603 bp and 4.767 bp, inside the exact-out rounding bound of about 2.0 bp and 6.0 bp at qin of about 4,898 and 1,660 lamports). The frame by `ix_name`: `buy` 20,418; `buy_v2` 4,137; `multi_hop_swap` 1,523; `buy_exact_quote_in` 61,219; `buy_exact_quote_in_v2` 3,152; sells 65,822 (156,271 in all).
- **In-sample.** #576 shaped this amendment. Re-scoring #576 under it is a diagnostic only. 154 of #576's 165 forward-law buy hits have never been checked under the inverse law, and no `buy_v2` print has been checked under it (all 11 refetched hits were `buy`). If `buy_v2` misses later, it is not dropped from the whitelist after the fact.
- **Overlap.** These prints sit inside both reads' October hours, so the same transactions are in forward-1002ev's hours.
- **When.** Written while the declared observations run (EXP-024 section 3.1 and Amendments 2 and 3; EXP-025 section 5.1 and its Amendment 3).
- **Inputs.** The check's inputs and this amendment's inputs contain no outcome. No label, fill, exit, P&L, mean, CI or day sign was computed, and the H5 trade set was not priced.

#### D. Conditions

This amendment takes effect only if quant-proof posts OK on its final head and it merges before any look's P7 sample is drawn and before 2026-10-16T00:00Z. After a P7 sample is drawn, no further amendment of line B. If it does not take effect, line B stays as Amendment 1 pinned it, and P7 is run and reported under that rule.

#### E. Unchanged

The sell law and its match, the tolerance constants (1 bp of the law or 2 units), the 99% bar per side, the line B population (the 300 cross-source prints plus P7's 1,000 sampled prints), the 1,000-print draw and its frame, line A, the tier lines and their 75% and 90% bars, and the consequence (if either side fails, P7 fails and both looks are NOT_DECIDABLE). Also unchanged: every other A-line blob, the rule, the parameters, the universe, the trigger, the pricing, the costs, the legs, the gate statistics, the windows, the look schedule, futility, multiplicity, the kill rules and the section 0 counting start. This amendment adds no reader and opens no sealed block.

**Not addressed here (ruling item 4).** The tier lines have never run on October prints. The ruling asks for one outcome-blind run on a fresh window before 2026-10-15 and, if they fail, an amendment only under the same discipline. That is separate from this amendment.

### Amendment 7 (written 2026-10-10T19:28Z, from `date -u`; before any look's P7 sample is drawn; it must merge before any look's P7 sample is drawn and before 2026-10-16T00:00Z): P7 tier lines, buy side amended

Outcome-blind in its inputs (see E, "Outcome records in existence"). It is the amendment that Amendment 6's "Not addressed here (ruling item 4)" provides for: the tier lines ran once on October prints, and their buy side failed. To write it, no row, report or scratch file of forward-1002, forward-1002ev, walk 2, forward-paper, a runner, the canary or the shadow was opened, and no October label, H5 trigger, fill, exit or P&L was computed or read; the H5 trade set was not priced. Its inputs are listed in E. It changes only the buy bullet of section 10 P7 and the `inputs` key of the A line (with the Amendment 5 A status sentence and table row that name it). It is numbered after Amendment 6 on main `822cefb`; amendments take numbers in merge order. Line numbers below are those of this file on main `822cefb`.

#### A. Basis: what failed, and the mechanism on refetched October prints [measured]

**What failed.** MiScusi job #598 ran `tools/tip_event_v_p7.py` (blob `4503a916…`) on the tip follower's declared hour `[2026-10-10T15:00:00Z, 16:00:00Z)`, declared at 14:40:43Z, before H; it waited until 16:05Z and drew 1,000 prints. Its report-only `line2_exp024` runs `tools.boostfloor_inputs.tier_lines` as section 10 pinned it, on tape rows with V from each print's own stamp:
- sells: 392/429 = 0.9138 (need 0.75);
- buys: 139/571 = 0.2434 (need 0.90). By `ix_name`: `buy` 116/152, `buy_v2` 23/31, `buy_exact_quote_in` 0/354, `buy_exact_quote_in_v2` 0/33, `multi_hop_swap` 0/1.

Left unchanged, P7 fails, and both looks are NOT_DECIDABLE (section 11; from 2026-10-10T00Z this spends Look 1's window and α_1). The same buy relation in EXP-025's driver on September adapter rows (job #607, 2026-09-20, rows without `ix_name`) gave buys 151/600 = 0.2517 and sells 372/400 = 0.93, so the failure is not October-specific.

**#598's provenance.** MiScusi shows #598 as `failed` (exit 1) only because its post-run copy glob (`/tmp/p7acc.BNy3yk/out/acc-20261010T1500Z/*`, a 0700 directory) failed after the tool had printed `pass: true` and its counts. Job #614 recovered the two files. The recovered `p7-tip-prints.jsonl` has sha256 `187beaaa0d79ae925a9956f514d60c0b6dcffce803017af463aba59407418fcb`, equal to the `prints_sha256` the tool printed in #598's log.

**The mechanism, on 245 refetched prints of that draw.** Job #623 (mal-fast-0, code `8333715`) fetched an evenly spaced subsample of each (side, `ix_name`) group of the 1,000 with `getTransaction` (245 calls, 245 credits, 2 per second, no errors or retries, cap 250) and decoded the raw event bytes at decoder blob `238942a6`. Job #624 re-read the saved transactions (0 credits). In what follows qin = ceil(Q·tok/(b − tok)) with Q = vault + V, sol is the tape's `sol_lamports` (event bytes 112:120), pqa the event's `pool_quote_amount` (bytes 64:72), and bps_i the event's LP, protocol and creator fee rates.
1. **Exact-out buys (`buy`, `buy_v2`; 91 prints): the fee is charged on top of the curve input.** pqa = qin on 91/91, and sol = pqa + Σ ceil(pqa·bps_i/10^4) on 91/91. So 1 − qin/sol = f/(1 + f), and the pinned relation misses by f²/(1 + f): 1.543 bp at 125 bp, 1.423 at 120, 1.307 at 115, 1.197 at 110, 1.091 at 105, 0.990 at 100 [arithmetic]. Every sampled exact-out print at a tier of 110 bp or more missed under the pinned relation (0/20); at 100 bp or less, 69/71 hit.
2. **Exact-in buys (`buy_exact_quote_in`, `_v2`; 93 prints): the fee law is the same; the tape field means something else.** For this family sol is the net curve input: sol − qin is +1 on 60/60 and 1 to 3 on 33/33. The user's spend is pqa, which equals the limit at bytes 24:32 on 93/93. pqa = sol + the reported fees on 93/93; each fee = ceil(sol·bps_i/10^4) on 92/93 (LP), 92/93 (protocol) and 90/93 (creator). On a tape row the implied fee therefore reads about 0, and the print misses by the whole tier (−125 to −30 bp). No tolerance or tier change can score these prints from tape fields.
3. **`multi_hop_swap` (1 print):** the exact-in field layout; it pays only the 5 bp protocol fee (LP 0, creator 0), where the helper's tier is 120 bp.
4. **Sells (60 prints):** sol = pqa − Σ ceil(pqa·bps_i/10^4) on 60/60, so the pinned sell relation is right. Its 7 sampled misses are 2 tier mismatches and 5 dust prints (sol 520 to 12,683 lamports).
5. **The tier:** the chain's total bps equal the helper's tier on 237/245; `common2.fee_frac` and `boostfloor_score.tier_fee` gave the same tier on every print. The chain sets the tier from the mint's actual supply, not 10^9 tokens, and charges whole bps where the table has half-bp tiers (42.5 → 43, 37.5 → 38).
6. **The tape omits** `pool_quote_amount`, the three fee amounts and the supply (245/245); every field it does carry equaled the raw event on 245/245. P5's raw decode carries them (D).

So the failure is the buy relation, not V, Q or the tier table.

**Known before #598.** The field meaning in item 2 was already in the repo before #598 ran: the comment at `tools/test_exp025_p7.py:245-247` (merged in #577, `be3f057`, 2026-10-10T16:04:11Z, before #598's run at 16:05Z, written on the public-chain fixture `buy_exact_quote_in_v2_499_kept`) says that print's "tape sol_lamports is net of fees". The exclusion in B follows from the field meaning, not from the 0% count.

#### B. The rule [pinned]

> **P7 tier lines, buy side, amended [pinned]. Sells unchanged.** Each sampled tape buy of P7's 1,000 is decided in this order:
> - **(a) Skip.** A buy with tape sol ≤ 0, tok ≤ 0 or tok ≥ b is skipped and is in no denominator, as before. This is decided on tape fields before anything else.
> - **(b) Unresolved = miss.** A buy with no P5 `gettx_v.jsonl` record at its content key, a record whose status is not `ok` (`absent`, `fetch_failed:*`, `no_raw_ref`), a record whose `fields_equal` is not `true`, or a decode whose side is not `buy`, is **in the buy denominator as a miss**. Its `ix_name` is not known from a decode that is this print, so it cannot be excluded.
> - **(c) Excluded.** For a resolved buy, `buy_exclusion(decoded)` (Amendment 6 B: `zero_sol`, then `buy_exact_quote_in` by prefix, v1 and v2, then `no_ix_name` for a missing, null or empty name, then `ix_not_listed` for any name other than exactly `buy` or `buy_v2`, `multi_hop_swap` included) gives a cause: the buy is in neither denominator, counted per cause and per name. These are line B's buys, decided from the same raw decode.
> - **(d) No V, non-integer = miss.** A buy whose V is missing by the Amendment 1 source order is a miss, as before. V is taken as an integer: `v = int(v)` only if `float(v).is_integer()`, otherwise a miss; tape sol, tok, q and b must be Python ints and not bool, otherwise a miss.
> - **(e) The relation.** Q = tape `quote_reserve` + V. ppm = `paper_curve_math.pumpswap_sol_fee_ppm(Q / b * 10**6)`, which is `boostfloor_score.tier_fee(Q, b)` × 10^6. With qin = `-((-Q*tok) // (b - tok))` (the Amendment 6 `buy_law`), a buy matches if `abs(sol * 10**6 - qin * (10**6 + ppm)) <= 100 * qin`. In words: the implied fee on the curve input, sol/qin − 1, is within 1 bp of the tier. This **replaces** `|(1 − tok·Q/(b − tok)/sol) − tier_fee| <= 1e-4`; the replaced relation is not an alternative.
>
> **Tolerance.** 1 bp of qin: the section 10 "within 1 bp" (`BP`; EXP-025 names the same constant `P7_TOLERANCE_BP`), written as `TIER_BUY_TOL_PPM = 100` ppm of qin. There is no unit allowance.
>
> **Unchanged:** the sell tier line and its match; the 1,000-print sample and V by the source order; the 75% and 90% bars; a side with nothing to score fails; "if either fails … both looks are NOT_DECIDABLE".

**Stricter than line B on unresolved buys (said, not aligned).** Amendment 6's line B unresolved list (line 915) has no `fields_equal` condition, because line B takes every law field from the decode. The tier line scores the tape's law fields with the decode's `ix_name`, so it trusts that name only when the decode is this print: `fields_equal` true, and a buy. It is therefore stricter than line B: a P5 fetch gap or a field mismatch is a tier-line miss. This can only remove support. Before this amendment such a buy could still hit through the ev V.

**Why these choices.**
- **Population.** Exact-in buys cannot be scored on a tape row (A.2), so keeping them fails every look by construction. `multi_hop_swap` pays a different fee (A.3). The tier line and line B now judge the same buys.
- **Relation.** It is the chain's (A.1), with the tier from the read's own helper. When the tier is right, the per-component rounding leaves sol − qin·(1 + f) in [0, 3) lamports [arithmetic].
- **Tolerance, no unit allowance.** The tier steps in `tools/paper_curve_math.py` are 5 bp from 125 down to 55 bp, then 2.5 bp (52.5, 50, 47.5 … 30). An N-lamport allowance would not pass every dust buy: with N = 3, a 25 bp error is still caught for qin above about 1,200 lamports. The reason for no allowance is that the pinned constant stays and the dust misses were seen in-sample (E). The cost: a buy with qin below about 30,000 lamports can miss on rounding alone (3 lamports is 1 bp there).
- **Half-bp tiers.** At 52.5, 47.5, 42.5, 37.5 and 32.5 bp the chain charges whole bps (A.5), so the residual there is +0.5 bp plus rounding, and the effective tolerance is 0.5 bp: prints with qin below about 60,000 lamports can miss on rounding alone at those tiers. A unit test pins this at the 42.5 bp tier (D).
- **What it still catches** [arithmetic; each is a test in D]: a tier one step off (2.5 bp or more): at any qin when the chain charges more than the tier, and, when it charges less, for qin above about 20,000 lamports (a 2.5 bp step) or 7,500 (a 5 bp step), because the three ceils only add, and add less than 3 lamports; a fee charged inside qin, which is the simulator's relation, by f²/(1 − f): 1.582 bp at 125 bp and 1.010 bp at 100 bp; +5 bp of fee; Q off by 2% (a gross-vault or pending-fee column), which moves qin by the same share.

#### C. What this replaces

- **Section 10 P7, second bullet** ("buys must match the implied tier fee within 1 bp on ≥ 90%", line 314) now reads as B. The sells bullet and the 90% bar stand.
- **Amendment 1, "The two tier lines stand unchanged"** (line 576), and **Amendment 6 E, "the tier lines and their 75% and 90% bars"** (line 943), now hold for the sell line and for both bars only.
- **Amendment 6, "Not addressed here (ruling item 4)"** (line 945): this is that amendment.
- **What differs:** (a) the population: exact-in, `multi_hop_swap`, nameless and `zero_sol` buys leave the buy tier line, and unresolved buys are misses; (b) the relation: the implied fee is taken on the curve input, not on the user amount. Nothing else.

#### D. The code (the `inputs` pin)

`tools/boostfloor_inputs.py` goes from blob `482c73b0…` (Amendment 6) to `edf0d2d7bc7aec0f7c0c2f95f0e9df7ad95eef76`, from branch `claude/exp024-tier-lines-amend`. The `inputs=` key of the single A line (Amendment 5 A) is updated in place, because `parse_p3_pins` refuses a second line, and the Amendment 5 A table row says so. No other A-line key changes: `read_tool` `fefe0c15`, `score_module` `e63cca50`, `latency_curve` `c194134b`, `paper_curve_math` `42daf5ad`, `extractor` `92d070f5`, `v_join` `8b60e5bf`, `synthetic_class` `930c8caa`. The decoder (`observe/trade_decode.py`, `238942a6`) and `tools/tip_event_v_p7.py` (`4503a916`) are not edited.
- **`tier_lines(sample, v_of, rec_of)`.** `rec_of`, the print's P5 record, is a required argument, so the replaced relation cannot be called. Each buy is decided by `tier_buy_one` in the order of B; `tier_buy_hit` is the integer relation; `int_v` is the V conversion. The sell branch computes as before (V is passed through `float()` there, as `p7_check` did). The buy output adds `unresolved` and `unresolved_by` (`no_record`, `status`, `fields_equal`, `side`), `non_int`, `excluded`, `excluded_by`, `excluded_by_name`, `by_ix_name` (n and matches for `buy` and `buy_v2`) and `tolerance_ppm_of_qin`. The sell output is unchanged.
- **`p7_check`** passes V as an int (the ev V when line A passed, else the P5 `getTransaction` V; both are ints) and the P5 records. `pass` is still the two tier lines and both line B sides. It computes the report-only check of E inside a guard: an error there is reported by its type and never stops P7.
- **P5's record gains six fields** for the report-only check in E: the decoder's three fee amounts (`lp_fee`, `protocol_fee`, `creator_fee`) and the three fee rates (`lp_fee_basis_points`, `protocol_fee_basis_points`, `coin_creator_fee_basis_points`), which `attach_fee_bps` reads from the same event bytes (72, 88, 344). It walks the events in `records_from_logs`' own order, stamps a row only when the walk gives one event per row and the event's bytes 112:120 equal the row's `sol_lamports`, and otherwise (or on any exception) leaves them null. No fetch, call count, other field or `fields_equal` changes.
- **The `p7` mode** prints the tier-line counts and the report-only exact-in counts beside line B's (counts and instruction names only).
- **The tool that #598 accepted, `tools/tip_event_v_p7.py`, is not edited.** Its report-only `line2_exp024` calls `tier_lines` without `rec_of`, so it now reports `{"error": "TypeError", "scored": false}`; its line 1 and `pass` are unaffected. `tools/test_tip_event_v_p7.py` is updated to say so.
- **Tests.** `tools/test_boostfloor_inputs_tier.py` (new) and `tools/test_boostfloor_read.py` `test_tier_lines`: the shared synthetic cases `tools/fixtures/p7_line2_buy_cases.json` (38 integer cases: chain-true exact-out buys at 125, 120, 115, 110, 105, 100, 90 and 30 bp hit; the simulator's relation, +5 bp, a 5 bp tier step and a 2.5 bp step (charged as 33 whole bps), and Q ± 2% miss; a dust buy at 30 bp misses; the integer edge both ways; a net-sol print named `buy` misses; every exclusion cause and name; the skip rule), the 42.5 bp half-tier (43 bp hits, 44 misses, dust misses), a 2.5 bp step down at the 32.5 bp tier (missed at qin 10^8, a hit on dust), the replaced relation documented on the table, the order of B, int V, the public-chain fixtures through `decode_tx` (`buy_v1_481_wsol` hits; `buy_exact_quote_in_496` is excluded, with its tape sol one lamport over the ceil law and its fees ceiled on sol), `attach_fee_bps` and its guards, `exact_in_raw`, and `p7_check` end to end. The suite (`tools.test_boostfloor_inputs_tier`, `tools.test_boostfloor_read`, `tools.test_tip_event_v_p7`, `tools.test_exp025_p7`, `tools.test_boostfloor_score`) ran 245 tests, OK (1 skipped), at `cbf6ae3` under `/data/mal/audit-1008/venv/bin/python -m unittest`.
- **Real-layout parity (0 credits): MiScusi job #632** on mal-fast-0 at `f555b75` (the earlier inputs blob `cf68edc7…`; the final blob `edf0d2d7…` adds only the guard on the report-only check), script sha256 `2a9a7d1c…`, on #598's prints (`187beaaa…`), #625's per-print detail (`f995d1d4…`) and #623's transaction cache (`b1d708ea…`). Record `~/data/exp024-am7-parity/parity.json`, sha256 `9a2247fa34e7759475431fe174a60c6f83ef56067652bc12bbaa3340df9364f9`.
  - **Part A, the 1,000 on tape rows** (the tape row standing in for the decode): per-print identity with #625 on all 183 comparable buys (0 differ); buys 175/183 (`buy` 144/152, `buy_v2` 31/31); excluded `buy_exact_quote_in` 387 (354 v1, 33 v2) and `ix_not_listed` 1 (`multi_hop_swap`); 0 skipped, unresolved, no-V or non-integer; sells 392/429. **PASS** as declared.
  - **Part B, the real P5 layout on #623's 245 refetched prints** (`fetch_prints` with `decode_tx` on the cached transactions, a P5 directory written, `p7_check` run on it): fetch `ok` 245/245, `fields_equal` 245/245, fee rates stamped 245/245; tier sells 53/60; tier buys **90/91** (`buy` 59/60, `buy_v2` 31/31); excluded `buy_exact_quote_in` 93 (60 + 33) and `ix_not_listed` 1; exact-in check n 93, pqa = sol + fees 93/93, each fee = ceil(sol·bps_i) on 92 (LP), 92 (protocol) and 90 (creator) of 93, pqa − sol = Σ ceil 90/93, chain bps = tier 91/93 (within 0.5 bp 92/93); line B buys 91/91, sells 60/60. The job's pre-declared buy count was 89/91 (`buy` 58/60), copied from #624's H1 column, so **the job's verdict is FAIL** on that one count; every other declared count held. **Job #634** (0 credits, the same 91 prints on tape rows) explains the one print: a dust `buy` at the 30 bp tier (qin 7,783 lamports, sol 7,807) is +1.197 bp under #624's H1, which used the unceiled curve input tok·Q/(b − tok) = 7,782.72, and +0.836 bp under the rule's ceil qin, so it hits. #624's form gives 58/60 on these prints and the rule gives 59/60; Part A's per-print identity with #625, which uses the ceil law, covers the same prints. The code is not changed by this.
  - **At the final blob: MiScusi job #636** on mal-fast-0 at `cbf6ae3` (`inputs` `edf0d2d7…`), the same script and inputs: its `parity.json` (`~/data/exp024-am7-parity-final/parity.json`, sha256 `0d94cd4ef63c95bcfbf8d77ef546847882e33c7f48846faac16cf99858bb2945`) equals #632's in every key but `inputs_blob`, as declared in its command. Verdict PASS.
- **Joint parity at both PR heads: MiScusi job #639.** It ran on mal-fast-0 (role ops, 800 MB cap, peak 69 MB, exit 0, verdict PASS) from 2026-10-10T19:48:00Z to 19:48:09Z, with 0 credits and no RPC.
  - **Code run.** This branch at `300129f8751e8d1a711fecc66742d9ba6721d4c8` and EXP-025's `claude/exp025-p7-line2-amend` at `795a20118a3f78d388108bf6128c1460ade32956`, each from its own `git archive` tree and in its own process, plus main `822cefb` for the sells. The job refused to start unless both heads, its script (sha256 `34c3147a7c8a464cd467528c547f3e223099ebd2ab069702ad8abd81d58e5ec4`, in the job command) and the three input files below matched.
    - This file: `tools/boostfloor_inputs.tier_lines` and `tier_buy_one` at blob `edf0d2d7…`, as #632 part A ran them: the tape row stands in for the P5 decode (status `ok`, `fields_equal` true, the decoded side, `zero_sol` and `ix_name`), and V is the print's own stamp, as an int.
    - EXP-025: `tools/exp025_p7.line2_tally` → `line2_one` → `ARTIFACTS/exp025/p7_line2_amend.py`, sha-checked at load (module sha256 `1908931c…`, driver blob `26f47531…`). Its Q is tape `quote_reserve` + V, with V0 = 0.
  - **Inputs (sha256).**
    - #598's prints file `p7-tip-prints.jsonl`: `187beaaa0d79ae925a9956f514d60c0b6dcffce803017af463aba59407418fcb`.
    - The tip tape hour `trades-2026-10-10T15.jsonl`: `03606a81ca48ca552adfb04e7479f0cc74986ab001d0650fab97a11a4f1996d4` (992,481 lines; 1,000 of 1,000 main keys joined; 0 duplicate content keys).
    - #625's `detail.jsonl`: `f995d1d4ba54f8057eb6b15447fff5394f4ce852b6cb8d79ede01c9bdbdd93bf`. #625's `summary.json`: `9cabf076d2e8f4a62c27ac2f35c046de69a5a9c82b5f3053f301fc684ae6ce2a`.
  - **Counts, the same for both implementations.**
    - Comparable buys 183: 175 hits (`buy` 144/152, `buy_v2` 31/31) and 8 misses.
    - Excluded 388: `buy_exact_quote_in` 354, `buy_exact_quote_in_v2` 33, `multi_hop_swap` 1 (`ix_not_listed`). Skipped 0.
    - This file's unresolved, no-V and non-integer counts are all 0. EXP-025's misses by cause are all 0.
  - **Per print, both implementations are identical to #625's `detail.jsonl`** on all 183 comparable buys, in order. The fields compared are slot, event index, name, sol, qin, tier ppm and hit, with 0 mismatches in either implementation. Between the two implementations, all 571 buys get the same class (hit, miss or the same exclusion cause), and all 429 sells get the same outcome.
  - **Sells are unchanged, print by print.**
    - This file's `tier_lines` gives 392/429 both at the head and at main `822cefb` (blob `482c73b0…`, called as `tip_event_v_p7` called it for #598), equal on every sell.
    - EXP-025's driver gives 392/429 both at its head and at main, equal on every sell. The tip tool's rounded EXP-025 form, which #598 and #625 report, gives 390/429.
  - **Tier lines on these prints:** buys 175/183 and sells 392/429; the buy tier line's `pass` is true.
  - **Record:** `~/data/p7l2-parity-both-20261010/parity.json` on mal-fast-0, sha256 `e73bbee17ad11bec57a4038f9b7593cd3b3d9961823d04dd9ca7662f4251d2b0`. The joined tape rows carry signatures, so they were deleted after the run.
  - This is in-sample (E). It checks both code paths on the real tape layout. It is not evidence for the rule.
- **E0: MiScusi job #637 (`j_moeSkSMA-gqXsg`).** It ran #593's command unchanged on mal-research-0 at `cbf6ae3bb2643110c72aa2f838736657ce679e0c` (this amendment with the A line at `edf0d2d7…`; role exploration, day 2026-09-20 only, 8 GB cap, 1.5 GB peak, exit 0), then `boostfloor_read pins` (rc 0), then `boostfloor_read.integrity()` (rc 0: count start, pin line, clean, pins, monitor `1ca0a88c…`, frozen files). Verdict **PASS**: n_tool 288 = n_ref 288; md5_tool = md5_ref = `328264a4f22176f7048d9f7719ae930b`; conf_sha256 `a4798efaa362122e1866db6ca0c34c4b75a17aba6f9a6ffd5940515808c977ca`; read_tool_blob `fefe0c156ce75839cf973da82e408f0dd9454678`; the job log prints `inputs` blob `edf0d2d7bc7aec0f7c0c2f95f0e9df7ad95eef76`. Record `/home/claude/.miscusi/jobs/j_moeSkSMA-gqXsg/out/e0.json`, sha256 `839548727f5b29b256b0f892a441704fd7968fe688e8829ad30023bc648c852c`, byte-equal (cmp) to #593's and #551's record; `pins.json` sha256 `24e8b9843ceff879b94540d46591b755b6de094f16b8edcfca18c659253d2fa8`. Job #635 (`j_QsXRLmmvFT4pCA`) ran the same at `6e63cb9`, with the earlier `inputs` blob `cf68edc7…` (before the guard on the report-only check), with the same result; it no longer covers the pinned blob. `boostfloor_read e0` does not import `tools/boostfloor_inputs.py`, so its output is unchanged by construction: this E0 covers the new `inputs` blob procedurally (the log names it, and `integrity()` checked it against the A line), and it does not exercise the tier lines. They are covered by the tests and by #632/#636 above. `python -m tools.boostfloor_read pins` must also exit 0 on the head that merges.

**The look's P7 record.** `p7` runs `boostfloor_read.integrity()` and records the head; `check_pins` refuses unless `tools/boostfloor_inputs.py` is the A-line blob, so a `pricing_check.json` from another `inputs` blob cannot be written by the pinned flow.

#### E. Disclosure

**What was seen when this was written:**
- #598's draw, as the tier-line counts per side and per `ix_name` above (#598 and its recovery #614);
- #607's September counts;
- the refetched mechanism sample: #623 (245 credits) and #624 (0 credits);
- the in-sample rescore #625 and its cross-tab #626 (mal-fast-0, `822cefb`, tape rows of the declared hour, 0 credits);
- the repo's public-chain fixtures in `tools/fixtures/walk2_event_v`, decoded offline;
- this amendment's parity jobs #632 and #636 and the diagnostic #634 (0 credits, D), and its E0 runs #635 and #637 (D).

**The in-sample rescore (#625, #626).**
- Buys 175/183 = 0.9563 (`buy` 144/152, `buy_v2` 31/31) under both helpers (their ppm differ on 0/183); 388 excluded (`buy_exact_quote_in` 354, `buy_exact_quote_in_v2` 33, `multi_hop_swap` 1); 0 skipped. Sells unchanged (392/429 in this file's form).
- By tier, amended against the replaced relation on the same 183: at 105 bp and above 35/36 against 0/36; at 100 bp 15/15 against 15/15; below 100 bp 125/132 against 124/132. Old to new: 139 hit both ways, 36 changed from miss to hit, 8 missed both ways, none changed from hit to miss.
- The 8 misses: 7 dust prints (qin 1,858 to 14,159 lamports, a residual of 0.43 to 2.52 lamports, 1.39 to 3.26 bp; six at 30 bp, one at 90 bp) and 1 at +5.0 bp at the 105 bp tier (qin 520,701,948), where the chain charged 110; it was not refetched.
- **This rescore is in-sample, because #598 shaped the rule. It is a diagnostic, never an acceptance.**

**When the rule was fixed, and how much of the count was seen first.** The author fixed the rule at 2026-10-10T18:44:40Z; that instant is not independently verifiable. The provable upper bound is job #625, which started at 18:48:00Z with the rescore script embedded in its command (sha256 `c6378c6e06042084d0b0e27380655676727736bd3a16c837f3e3249d171f4935`). Before the rule was fixed, #624's H1 column, which is the amended relation itself (with the unceiled curve input), had already scored 91 of the 183 comparable buys (#623's 60 `buy` and 31 `buy_v2`), 89/91 hits. So about half of 175/183 was known in advance: by #624's count the remainder is about 86/92 (0.935), with 6 of the 8 misses in it. Under the rule's ceil qin those 91 are 90/91 (job #634, D), so the remainder is 85/92 (0.924), with 7 of the 8 misses.

**Expected n.** The buy tier line's denominator in the looks is not measured. On tip-tape draws it was 183 comparable buys of 1,000 (#598) and 176 (#576's window), about 18%. In #598's draw 107 of the 183 were at the 30 bp tier and 36 at 105 bp or more; the V-range frame's tier mix and dust share are not measured, and nothing here predicts them. An empty side fails, and a small n makes the 90% bar noisy.

**Overlap.** The declared hour lies inside Look 1's counted window. It is one hour of the 144-hour window, so about 1/144 of the frame: about 7 prints of a 1,000 draw are expected from it [arithmetic, if prints were spread evenly over hours]. No exclusion is made.

**Simulator gap: disclosed, not changed.** Section 2 "Entry" (net = S·(1 − f)), `boostfloor_score.fill_round_trip` and `boostfloor_read`'s round trip charge a buy as net = S(1 − f). The chain gives net = S/(1 + f) up to rounding, for exact-out buys and for `buy_exact_quote_in`, the executor's default (`tools/h5_executor.py:1314`). So the simulator books S·f²/(1 + f) less curve input: at the deciding stake of 0.1 SOL and 125 bp, 15,432 lamports, about 15,239 lamports (0.0000152 SOL) less proceeds per round trip at an unchanged exit [arithmetic]. The direction is conservative, and section 2 is frozen, so it stays. The amended tier line tests the tier at Q; it does not validate the simulator's buy algebra, and no report may say it does.

**Report-only raw check of the executors' family (pre-declared; it can never change P7).** `p7_check` writes `exact_in_raw_report_only` from the P5 records of the P7 sample at no extra fetch. Population: sampled tape buys whose record is `ok`, `fields_equal` true, decoded as a buy, and excluded as `buy_exact_quote_in` (v1 and v2). It counts: pqa = sol + the three reported fees; each fee = ceil(sol·bps_i/10^4); pqa − sol = Σ ceil(sol·bps_i/10^4); and the chain's bps sum against the tier at Q = q + V (equal, and within 0.5 bp); per name, with prints lacking a field counted apart. It is reported with P7 and is not in `pass`, not a bar, and not a NOT_DECIDABLE condition, whatever it shows.

**Routed outside this amendment.** For exact-in buys the tape `sol_lamports` is the net curve input, so it understates the trader's spend by about f on about 70% of buys. Which features, triggers or BOOST `spent(i)` sums read buy-side tape SOL, and whether September's training rows used the same field, is a separate counts-only audit. Nothing here changes or depends on it.

**Optional fresh-hour run.** A run of the amended rule on a fresh declared tip-tape hour, if any, is report-only: at the merged heads, declared before its hour starts, and it can change nothing in this file.

**When.** Written while the declared observations run (section 3.1, Amendments 2 and 3).

**Outcome records in existence.** H5 canary and shadow outcomes for Look 1 pools could exist from 2026-10-10T00Z (section 3.1). None was opened for this amendment. Its inputs contain no outcome: no label, trigger outcome, fill, exit, P&L, mean, CI or day sign, and no canary or shadow record.

#### F. Conditions

- This amendment takes effect only if quant-proof posts OK on its final head and it merges before any look's P7 sample is drawn and before 2026-10-16T00:00Z.
- **The tier lines are not amended again once this amendment merges.**
- If it does not take effect, the tier lines stay as section 10 pinned them, P7 fails on this evidence, both looks are NOT_DECIDABLE, and Look 1's window and α_1 are spent (section 11, "Spending").

#### G. Not changed

The sell tier line, line A and line B; the 1,000-print sample, its frame and the V source order; the 75%, 90% and 99% bars; every other A-line blob; the rule, parameters, universe, trigger, pricing, costs, legs, gate statistics, windows, look schedule, futility, multiplicity, kill rules and the section 0 counting start. This amendment adds no reader and opens no sealed block.

## Sources

- `/data/mal/hunt-1008/h5-flows/{RULE,REPORT,VERIFY}.md` and `out/`
- `/data/mal/hunt-1008/JUDGE.md`
- `/data/mal/hunt-1008/h5-work/{EXP-024-draft,PLAN,LIVE-PLAN,ROBUST}.md`
- `/data/mal/audit-1008/reports/g_october_structure_check.md`
- `ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md`
- DEC-014, DEC-016, DEC-017, DEC-019, DEC-021, EXP-022, HOLDOUT_LEDGER and `docs/HANDOFF.md` on `main` at `57c0de3`
- PR #476
- Power scripts: `/data/mal/hunt-1008/h5-work/h5_power{,2,3,4}.py` (read only `h5-flows/out/boostdip_frozen_conf.parquet`) and `h5_power5.py` (the rerun on the pre-registered legs; also reads `latency_conf.parquet` and `capguard_conf.parquet`)
