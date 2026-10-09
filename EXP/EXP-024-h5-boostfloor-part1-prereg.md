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

## Sources

- `/data/mal/hunt-1008/h5-flows/{RULE,REPORT,VERIFY}.md` and `out/`
- `/data/mal/hunt-1008/JUDGE.md`
- `/data/mal/hunt-1008/h5-work/{EXP-024-draft,PLAN,LIVE-PLAN,ROBUST}.md`
- `/data/mal/audit-1008/reports/g_october_structure_check.md`
- `ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md`
- DEC-014, DEC-016, DEC-017, DEC-019, DEC-021, EXP-022, HOLDOUT_LEDGER and `docs/HANDOFF.md` on `main` at `57c0de3`
- PR #476
- Power scripts: `/data/mal/hunt-1008/h5-work/h5_power{,2,3,4}.py` (read only `h5-flows/out/boostdip_frozen_conf.parquet`) and `h5_power5.py` (the rerun on the pre-registered legs; also reads `latency_conf.parquet` and `capguard_conf.parquet`)
