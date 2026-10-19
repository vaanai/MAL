# EXP-028 Part 1: pre-registration of the hunt23 wave-2 holdout-bank read on fresh-0828

**Written and merged before any wave-2 outcome is read or priced.** This file is records only. No code, no data file, no `/data/mal` row, no block hour and no sealed path was opened to write it. Nothing in it is a measurement, and nothing in it says any book makes money. No book has cleared the promotion gate. The hunt23 wave-2 cohort may not move to its outcome phase (PHASE 2 of `hunt23/RULES.md` "Wave 2") until this file is on `main`: that order is what keeps its survivors bank-eligible ([DEC-031](../DEC/DEC-031-forward-paper-arena.md) Amendment 3 item 7 and item 8 (k)).

| Field | Value |
| --- | --- |
| **ID** | `EXP-028-hunt23-bank-part1-prereg` |
| **Status** | **planned**, Part 1 of 2. Order: this file merges; the manager sends PHASE 2 GO; the wave-2 screens run; a survivor claims (section 3); Part 2 merges (section 4); the pre-read checks pass (section 5); the one read runs (section 6). The block claim is inert unless a survivor meets section 3, and the Owner cell reverts to reserved if none does (section 8). |
| **Declared (UTC)** | 2026-10-19 (`date -u` read 2026-10-19T20:33:42Z when this text was drafted) |
| **Parent** | quant-proof's throughput ruling of 2026-10-19, item 4 "Version 2 (firm design)", pinned at [`ARTIFACTS/lab/qp-ruling-1019-throughput.md`](../ARTIFACTS/lab/qp-ruling-1019-throughput.md) (sha256 `b482a70abd5956d65ea88940e3a4283bad6c2ed4f1a7320ecb233fd1cc840d78`); [DEC-031](../DEC/DEC-031-forward-paper-arena.md) Amendment 3 (items 4, 7, 8) and Amendment 2 (the deciding cell); [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md) and its block-budget amendment of 2026-10-07; [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md); [EXP-015 plan v2](EXP-015-pooled-retrain-plan-v2.md) section 6 (the pre-read pattern); [EXP-021 Part 1](EXP-021-part1-prereg.md) (house style, and the claim it withdraws) |
| **Hypothesis** | A wave-2 rule that cleared the in-sample screen on exploration blocks still clears the full promotion gate, on all four deciding sub-cells, on one block it never touched, run as the frozen bot's exact code. This tests selection bias. It does not test the October regime. |
| **Kill condition** | A FAIL, or a NOT_DECIDABLE after the lock, kills that rule and every retune of it. The family gets no other block. See section 7. |
| **Tool** | None in this PR. The read tool, the screen-p tool and the pinned report tool are separate PRs, pinned path@sha256 in Part 2 (section 4). |

**Labels.** [pinned]: a design value this file fixes; not evidence. [inferred]: reasoned, not measured. [measured]: copied from a cited file.

## 1. Owner and block

- **Owner:** the hunt23 wave-2 cohort (section 2), as one EXP: **EXP-028**.
- **Block:** **fresh-0828, `[2026-08-28T12, 2026-09-03T12)`**, 144 hours, 7 UTC dates (08-28 and 09-03 are half days). Ledger row "Backup confirmation block". mal-research-0, three 48 h walkers: w1 `[2026-09-01T12, 2026-09-03T12)`, w2 `[2026-08-30T12, 2026-09-01T12)`, w3 `[2026-08-28T12, 2026-08-30T12)`; clean views `/data/mal/clean-view/fresh-0828/w{1,2,3}` with `VIEW.sha256`. **Sealed and verified 2026-10-02, never read** (ledger: 144/144, 0 flagged, 0 duplicate rows, jobs #58 to #60).
- **Order of the three August blocks [pinned].** fresh-0828 is spent first. **fresh-0808** (`[2026-08-08T12, 2026-08-14T12)`) is the next block, for a **later cohort** with its own EXP and its own Part 1; this file does not claim it. **fresh-0802** (`[2026-08-02T12, 2026-08-08T12)`) is **never spent here**: DEC-014 (block-budget rule 3) keeps at least one sealed, unread block unspent at all times, so at most two of the three can ever be spent, and a new block must be walked and entered in the ledger before the last unspent one could be.
- **One block per family, ever** (DEC-014 rule 2). A family here is one wave-2 agent. A family that claims fresh-0828 can claim no other block, whatever the verdict, and a precount refusal (section 5) does not give the entitlement back.
- **Seam, disclosed.** fresh-0828 starts at the hour `explore-0814` ends (2026-08-28T12), and `explore-0814` is a screen block for every wave-2 rule. So a read here is near-adjacent backcasting, not forecasting. No state, wallet history or creator history crosses the seam: the read builds history from the block's own rows from its first hour only (the first day undercounts, as in EXP-012 section 4.2).

## 2. Cohort

The cohort is the **closed list of the wave-2 DECLAREs** (DEC-031 Amendment 3 item 4). The manager writes the list in a MiScusi notebook entry with its instant **before any wave-2 outcome**, and never extends it. A book is in at most one cohort. Only a listed book may claim. Wave 1 (agents A, B, C; 33 cells, all killed) is **not** bank-eligible (the manager's decision in the pinned ruling) and is not in this cohort. A book declared later belongs to a later cohort.

The table is filled by the manager in Part 2 from the notebook entry, row for row. **The notebook entry is the record; a table row not in it is void, and a notebook row missing here is a Part 2 defect.** Rows beyond the notebooked list are not added.

| # | Agent family | DECLARE sha256 (and DECLARE-AMEND sha256, if any) | Notebook id | Notebook instant (UTC) |
| --- | --- | --- | --- | --- |
| W2-1 | *(Part 2)* | *(Part 2)* | *(Part 2)* | *(Part 2)* |
| W2-2 | *(Part 2)* | *(Part 2)* | *(Part 2)* | *(Part 2)* |
| W2-3 | *(Part 2)* | *(Part 2)* | *(Part 2)* | *(Part 2)* |
| *(one row per notebooked DECLARE)* | | | | |

Cohort notebook entry (id and instant): *(Part 2)*. PHASE 2 GO (notebook id and instant): *(Part 2)*. Part 1 merge commit (sha and committer time): *(Part 2)*. First wave-2 pricing job (id and start instant): *(Part 2)*. **If any wave-2 price precedes the Part 1 merge, this file is void for the cohort** (DEC-031 Amendment 3 item 8 (k)) and the cohort is not bank-eligible. PHASE 1 counts (structure only) are not prices.

## 3. Entry: who may claim the block

A wave-2 survivor may claim fresh-0828 **only if** all of these hold.

1. **Screen survivor.** It is not killed by `hunt23/RULES.md` Amendment 1 (all four sub-cells, explore-0814 alone and the September span pooled, the in-sample gate on all four sub-cells, ex-top-3 and ex-best-day) as `tools/arena_screen.py` applies it.
2. **Pooled screen p < 0.025 / m** [pinned]. Pooled over the four screen blocks (explore-0814 and the three September blocks), one-sided bootstrap p of mean SOL per attempt > 0 at the deciding cell (1.9 s, the stake in DECLARE, rent 0), 10,000 draws, seed 1, the share of draws with mean at or below 0. **p is the largest p over the four sub-cells** (flat and pressure, each under strand rules "sc" and "total"), from the trade-level resampler of `tools/arena_stack.py` (DEC-014 rule 1's p, extended to the four sub-cells as Amendment 1 does for the kill). The UTC-date-cluster p is reported beside it and decides at the Holm step (section 6), not here. `tools/arena_screen.py` at `9041cfb` prints CI90 lower bounds and **no p**, so **no survivor can claim until a p tool is merged with quant-proof's OK and pinned path@sha256** in the claim record. That tool is not in this PR.
3. **m, frozen** [pinned]:

   **m = 18 + H + X, with m at least 21.**
   - **18** is DEC-030 section 2's count of 17 (its round-2 bar p < 0.00147 is 0.025/17) plus 1 for EXP-027 (ledger, Forward walk 2 Status cell: "m >= 16; 18 on DEC-030 section 2's count of 17").
   - **H** is the number of hunt23 families that priced at least one cell on the exploration blocks before the claim: the wave-1 agents A, B and C (3), plus every agent in section 2's table that priced a cell. A hunt23 agent outside both (for example the speed study under `hunt23/`) is counted if it read any outcome on those blocks; the manager records yes or no with a notebook id.
   - **X** is every other family that has read outcomes on the 27 non-P1 dates since the count of 18 and that the manager names in the claim record (default: counted). The ledger's unlisted-reader count (about 37) is disclosed, not used. m never goes down.
   - **At H = 3 and X = 0, m = 21 and the bar is p < 0.025/21 = 0.00119.** Every wave-2 agent adds 1, so the real bar is stricter (for illustration only, not a count: three wave-2 agents give m = 24 and p < 0.00104).
   - **Order.** The family list behind H and X is notebooked with the cohort list, before any wave-2 price, so m is fixed by counting families and not by looking at a p. The claim record freezes m in the ledger row at claim time (DEC-014 rule 1).
4. **At most 3 survivors claim, and at most 1 per agent family.** If more qualify, the claimants are chosen mechanically [pinned]: within a family the survivor with the smaller screen p (the p of item 2), then across families the three smallest, ties broken by the order of section 2's table. Nobody picks (DEC-031 Amendment 3 item 2).
5. **Claim record.** A merged amendment to this file copies (not rounds) each claimant's p under every sub-cell and both resamplers, lists H and X, freezes m, and carries the matching ledger edit. Part 2 follows it.

## 4. Part 2: the freeze, merged before the read

Part 2 is a separate PR that merges **before** the read. It records, per claimed survivor:

- `bot_id`, the DECLARE sha256 and notebook id, `frozen_sha` (the code the read runs; one process per `frozen_sha`, DEC-031 Amendment 3 item 1), the rule module path@sha256, any model file and its md5, and the stake;
- the `StackConfig` pin of DEC-031 Amendment 2 item 8 (`rent` 0, `e1_floor_per_005` 384022, `strand_rule` "both", `k_rounding`, `feed_delay_slots`, `gap_slots` 150, `gap_silence_ms` 60000) and the V-band check as `path:line@frozen_sha` (Amendment 3 item 1);
- the **md5 decision-equivalence proof** (section 5 check 5), including the replay segment, which is exploration hours and never fresh-0828;
- the read tool, the screen-p tool and `tools/arena_report_legs.py`, each path@sha256; the V map path@sha256; the sha256 of each walker's `VIEW.sha256`; the expected hour count (144);
- the cohort table of section 2 and the three instants listed there.

`frozen_sha` is the sha the arena book registers at if it passes (DEC-031 Amendment 3 item 7 (b)). A claimant must not register in the arena before the result record of section 7 is on `main`, or it loses item 7 (its item 7 (a)).

## 5. Pre-read checks (before the lock; outcome-blind)

All run before any outcome row is opened. **A refusal before the lock spends nothing:** the block stays sealed and unread. One outcome-blind re-walk of a bad hour is allowed (EXP-015 section 6), recorded in a merged Part 2 amendment before the lock, then checks 1 to 5 run again. **Any failure after the lock spends the block** (section 6).

1. **Re-hash.** All **144/144** hours re-hashed against `VIEW.sha256` in each walker, with **0 flagged and 0 duplicate rows** (`tools/backfill_verify.py`, metadata and `--content --min-slots-per-hour 8000`).
2. **V map pin.** The V map is the Part 2 path@sha256, asserted at run time; the tool refuses if the file's sha256 differs. No fallback file unless a Part 2 amendment pins a named sha before the lock.
3. **V coverage > 99%** of the would-be prints, structure-only: of the PumpSwap WSOL-quote prints the claimed rules' entry predicates and pricing windows would read (decision slot to the end of the exit grace), the share whose pool has a V in the pinned map. Counts only, no price. The all-prints figure is report-only.
4. **Outcome-blind precount.** Per claimed survivor, would-be attempts by UTC date from decision timestamps only (a tool that loads no fill, exit or P&L, as DEC-031 section 2). **Refuse that survivor below 100 attempts or on fewer than 5 UTC dates.** The precount also reports the block-edge exclusions (attempts whose exit or grace window would run past 2026-09-03T12) from decision timestamps and the rule's maximum hold. A refused survivor leaves this read with no outcome seen.
5. **md5 equivalence with the arena host.** The read tool's decision stream (pool, decision slot, cell, exit spec) on the Part 2 replay segment has the same md5 as the arena host process running the same `frozen_sha`; the rule module's sha256 at `frozen_sha` equals the Part 2 pin; the tool refuses on a dirty `tools/` or a HEAD that is not an ancestor of `origin/main`.
6. **O_EXCL lock.** `/data/mal/exp028/HOLDOUT_READ.lock` is created with `O_EXCL` **before the first outcome row** is opened. A `started` line goes to the tries log under the lock. Checks 1 to 5 are re-asserted under it. After `started` there is no resume and no re-run.

## 6. The read

**One locked job, one read, all claimed survivors, once.** Nothing opens fresh-0828 before the lock (`hunt23/RULES.md` keeps `fresh-08*` closed to every agent). No retune and no peek: an interim read of a claimed rule on this block is a taint (DEC-014 (c); DEC-031 Amendment 3 item 4 (e)).

- **Deciding cell** (DEC-031 Amendment 2 and section 2): 1.9 s latency at the Part 2 stake, feed delay 7 slots, the E1 floor, 505,000 lamports per send, rent 0 on `filled` trips, flat 15% and pressure at slope scale 1, strand rules "sc" and "total", the engine's gap rules on. The frozen rule is run as frozen, including its 360-second pool-age filter. The 1.3 s leg and other stakes are reported and never decide.
- **Gate, on all four sub-cells** (flat and pressure, each under "sc" and "total"): at least 100 counted attempts (every entry decision, guarded reverts included); at least 5 distinct UTC dates with a majority of them positive; the CI90 lower bound of mean SOL per trade > 0 (1,000 draws, seed 1, 5th percentile, the gate's trade-level bound as `arena_score.cell_stats` calls it); total still > 0 after removing the top 3 trades.
- **Also, ex-best-day total > 0** on all four sub-cells.
- **Holm** [pinned]: alpha 0.05 over the **k claimed survivors in the locked job** (k at most 3; a survivor that fails the gate still counts in k). For each survivor, p_i is the largest one-sided p over the four sub-cells, computed by **two resamplers**, trade-level and UTC-date cluster, 10,000 draws, seed 1. Holm step-down runs on each resampler's p-values separately. **A survivor passes Holm only if it is rejected under both.** At k = 1 that is p at most 0.05.
- **Reported beside the verdict, never deciding:** the rent-charged leg (Amendment 2 item 7); the worst-in-slot leg with its **no-guard-rescue companion**, with R7's canary figure taken as the lower of the two (`tools/arena_report_legs.py`); the UTC-date-cluster CI90 lower bound; per-date and per-walker tables; the 1.3 s leg; block-edge exclusions (more than 5% of a survivor's attempts makes it NOT_DECIDABLE, as the screen kit treats it); the multiplicity block of Amendment 3 item 3, with the bank read counted. The Amendment 2 item 6 mint fetch is not applied here (it reads current chain state): it is disclosed, not a deciding figure, and a canary still needs it on the arena book's mints (Amendment 3 item 6).
- **NOT_DECIDABLE** (after the lock): any integrity failure, a missing or changed hour, a pin mismatch found late, an unresolved orphan or failed settlement or a counted trade without its pressure-leg P&L (DEC-014 Amendments 1 and 2), or a crash. It applies to every survivor in the job.

## 7. Outcomes

- **PASS** (gate on four sub-cells, ex-best-day > 0, Holm under both resamplers). A pass **earns an arena slot only**. Under DEC-031 Amendment 3 item 7 the book's **first arena read may serve as its confirmation** (p at most 0.05/c; that read adds 1 to c whatever it shows; the fallback path is counted twice). **A pass is NOT canary evidence** and does not stand in for any of DEC-031 section 2 steps 1 to 3 or Amendment 3 item 6. EXP-012 passed fresh-0903 and then FAILED forward (DEC-016 FINAL (A): FAIL on forward simulated paper, reported compromised). A pass is also not a P0 waiver: a book whose October structure count fails P0 is not registered (Amendment 3 item 1).
- **FAIL, or NOT_DECIDABLE after the lock.** The rule and **every retune of it are dead**, and its family has used its block. There is no second read, no new cell name, no retuned parameter.
- **Result record.** A records PR copies the verdicts and figures (not rounded), the multiplicity block and every taint touched. It merges before any registration that relies on a bank pass. The block is spent in every case and is never a confirmation holdout again. After a FAIL or NOT_DECIDABLE it goes to the exploration pool by a ledger edit at the verdict. After a PASS, the ledger edit at the verdict says where it goes.
- **What a PASS can claim, and only this:** "one gate clearance on an August getBlock-only block next to the screen data, at modelled costs, with the frozen bot's exact code." It is not a persistence result, not an October result and not a profit result.

## 8. Release of the August blocks, the EXP-021 withdrawal, the ledger

- **Release.** The owner released the August blocks on 2026-10-19 (~19:40Z, MiScusi notebook `n_wus56tfzDe_ahw`, as recorded by the manager; the owner's words are in that entry and are not copied here). The release permits a pre-registered claim. It reads no block and changes no gate.
- **EXP-021's conditional claim on fresh-0802 is withdrawn.** EXP-021 Part 1 section 1 made that claim conditional on a screen PASS. It is withdrawn today, before any screen row and before any hour of fresh-0802 was read. **EXP-021 stays paused** (owner decision O1, 2026-10-08). A note is added to EXP-021 Part 1.
- **Ledger** (this PR): the Backup confirmation block (fresh-0828) Owner cell becomes "EXP-028 (hunt23 wave-2 bank), Part 1 merged"; the Third backup block (fresh-0802) claim note is changed to say its EXP-021 candidacy is withdrawn and it is the unspent reserve; one dated Changelog line. The catalog now reads the fresh-0828 owner as EXP-028 and denies every other role and id, exploration included. That is a guard, not a permission: section 5's refusals still apply, and no EXP-028 tool exists yet.
- **If no survivor meets section 3**, or the manager records that none claimed, the Owner cell goes back to "reserved" by ledger edit. Nothing in this file then reads the block.

## 9. Disclosures and what this file does not do

- **Prior [inferred].** All 33 wave-1 cells were killed, so a claim at p < 0.025/m (at most 0.00119) is not the likely outcome, and a pass at the bank is less likely still. This is an estimate, not a measurement.
- **Multiplicity.** At most 3 survivors, Holm at 0.05, so the chance that a no-edge cohort passes the bank is at most 0.05 on this block, on top of the entry bar. The arena confirmation (p at most 0.05/c) is a second, separate correction.
- **Time order.** A frozen model may have been fit on September hours, which are later than this block. The bank checks selection bias; it does not test forward order. The model may contain no fresh-0828 row (the block is sealed).
- **Regime.** This is August data. The October regime is not tested here (the ruling: "an August pass checks for selection bias").
- **Not decided here, left to Part 2 and the claim record:** the numbers H and X, the cohort rows, the tools' shas, the replay segment, the V map path, the stake.
- **This file does not** open a block, define a rule, change the promotion gate, change DEC-031, or authorize any live action. The gate in CLAUDE.md is unchanged.

## Sources

[QP throughput ruling](../ARTIFACTS/lab/qp-ruling-1019-throughput.md) (item 4, Version 2); [DEC-031](../DEC/DEC-031-forward-paper-arena.md) Amendments 2 and 3; [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md) block budget (2026-10-07) and clarification; [DEC-030](../DEC/DEC-030-walk2-push-slot.md) section 2; [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md); [EXP-015 plan v2](EXP-015-pooled-retrain-plan-v2.md) section 6; [EXP-021 Part 1](EXP-021-part1-prereg.md); `hunt23/RULES.md` "Wave 2" (`/data/mal/hunt-1008/push-1016/hunt23/RULES.md`, a mutable `/data` file; its Wave 2 section was written 2026-10-19T20:27:02Z); `tools/arena_screen.py` at `9041cfb`; `tools/arena_report_legs.py`.
