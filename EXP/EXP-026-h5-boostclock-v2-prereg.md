# EXP-026: H5-BOOSTCLOCK v2 forward paper read, pre-registration

**Draft. It must merge, with quant-proof's OK on its final head, before the v2 shadow starts.** Written 2026-10-10. To write it, no row of a sealed block, forward-1002, forward-1002ev, walk 2, the fast-0 tip archive, forward-paper or runner output was opened, and no October outcome, canary record or shadow outcome record was read. Its inputs are the iter-r2 directory `/data/mal/hunt-1008/iter-r2/h5-boostclock-exit/` (`FREEZE.md`, `REPORT.md` with quant-proof's verification appended, `bc_rule.py`, `s01_sim.py`), [EXP-024](EXP-024-h5-boostfloor-part1-prereg.md), [DEC-024](../DEC/DEC-024-h5-live-canary.md), [DEC-027](../DEC/DEC-027-h5-champion-challenger.md), the source of `tools/h5_shadow.py` and `tools/h5_executor.py`, and `docs/HANDOFF.md`. This file is docs only. The shadow change, the read tool and the count job are separate PRs (section 13). Nothing in this file says or implies that any book is positive. **A fail is the likely outcome** (section 11).

| Field | Value |
| --- | --- |
| **ID** | `EXP-026-h5-boostclock-v2-prereg` |
| **Status** | **draft**. Nothing run. The window opens no earlier than the merge instant + 24 h (section 0). |
| **Declared (UTC)** | 2026-10-10 (draft). |
| **Parent** | iter-r2 H5-BOOSTCLOCK-EXIT, cell **C10**: `FREEZE.md` (sha256 `5368cc7b757a2f7a6cb064c71193b4e0cd740583f7348a60cb9a864401c190e0`, written 2026-10-10T08:06:52Z before any exit P&L), `REPORT.md` (exploration; quant-proof verified: "holds on its own frozen rule", no DEC-027 candidate, "Registration: Not justified" as a challenger or a paired new-DEC test; section 1 says why this file is filed anyway). Base rule: H5-BOOSTFLOOR v1 (`h5-flows/RULE.md`, sha256 `c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c`). |
| **Rules** | [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md), [DEC-021](../DEC/DEC-021-champion-challenger.md) §1 and §8, [DEC-023](../DEC/DEC-023-h5-family.md), [DEC-024](../DEC/DEC-024-h5-live-canary.md) §5, §6, Amendments 2, 4 and 5, [DEC-027](../DEC/DEC-027-h5-champion-challenger.md) §3(b), §4, §5.3, §5.4, [EXP-024](EXP-024-h5-boostfloor-part1-prereg.md) §3, §3.1, §5–§7, §11, Amendments 2–4 (D1), [EXP-022](EXP-022-cap-pick-part1-prereg.md) §9. The live decision is [DEC-028](../DEC/DEC-028-h5-v2-canary.md) (draft). |
| **Hypothesis** | The frozen H5-BOOSTCLOCK v2 book (v1's trigger, entry and fills; the C10 exit) has mean SOL per trade > 0 at 0.1 SOL on plain October pools in the window, under both fail models, and clears the lab's promotion gate on that window. |
| **Kill condition** | The read does not pass (section 5), or a structure halt or a NOT_DECIDABLE condition (section 7), or a precondition fails (section 6). **One read, at one instant.** No retune, no re-read, no rescue hours. |
| **Expected outcome** | **FAIL is likely.** This file's estimate is P(pass) ≈ 0.03, range 0.01–0.06 [est] (section 11). |
| **Tools** | **Shadow:** `tools/h5_shadow.py` with the v2 rule registered (section 13). **Read:** `tools/exp026_read.py` (new, section 8). **Count job:** section 3.2. **Halt:** `tools/pump_structure_monitor.py` (A3), the blob EXP-024 §10 pins. |

**Labels.** [measured] is copied from a cited file that computed it. Exploration arithmetic is measured arithmetic, not evidence of edge. [inferred] is reasoned. [pinned] is a design value this file fixes; it is not evidence. [est] is an estimate.

## 0. Pinned lines and the window start

```
EXP026_RULE_ID: H5-BOOSTCLOCK-v2
EXP026_WINDOW_RULE: W0 = first full UTC hour at or after (merge instant + 24 h), and not before every section 6.1 item holds
EXP026_WINDOW_CAP_H: 336
EXP026_LAST_W0: 2026-10-23T00
```

- **Format.** Each line matches exactly once, as written. The read tool refuses unless they do and unless this file is clean against HEAD. The values never change after merge.
- **W0 [pinned].** The window opens at the first full UTC hour at or after the merge instant of this file plus 24 h. If any section 6.1 item does not hold then, W0 is the first full UTC hour after they all hold. **Never earlier.** The manager records the merge instant (`date -u`) and W0 in a dated line under "Window record" at the end of this file, before W0, from the merge record and the shadow's start record only.
- **Why +24 h.** The shadow must already be running the merged rule, with its md5 proof recorded, before the first counted hour. 24 h covers the restart, a keyless check of the v2 records and the companion merges. No pool with s0 before W0 is ever scored for EXP-026, even to "see how it would have done". Doing so is a breach (section 3.3).
- **Withdrawal.** If W0 has not occurred by **2026-10-23T00:00Z**, EXP-026 is withdrawn before counting and no outcome is read. A re-filing needs a new file and new hours.

## 1. Family, status, and what this is not

- **A new rule in the H5 family ([DEC-023](../DEC/DEC-023-h5-family.md)), not a retune of v1.** EXP-024 §11 (line 333) says "an exit tied to the observed BOOST end … would each be a **new** rule, needing its own discovery on exploration data". iter-r2 is that discovery. v2's hypothesis is v1's (a drained-pool trigger inside BOOST is positive) with an exit that follows BOOST's own slices instead of a fixed clock.
- **Not EXP-024.** EXP-024's Look 1 and Look 2 read the frozen v1 on the tape, as written. Nothing here changes any EXP-024 rule, window, α, read tool or kill rule. A PASS or FAIL here speaks about v2 only.
- **Not a DEC-027 challenger.** C10 fails iter-r2's C5 (DEC-027 §6 item 2's +3.0 pp floor) on pressure: **+2.9425 pp < +3.0** [measured, REPORT.md]. DEC-027 keeps C-BX parked, and this file does not reopen it or take a k slot.
- **Not a paired test.** The deciding question is whether v2's **own book** clears the promotion gate on fresh forward hours. The paired difference against v1 is report-only (section 9).
- **Not an answer to v1's halt.** DEC-024 §5: "A halt is never followed by a retune." v1's canary is STOPPED under §5.1 (fired 2026-10-10T07:11:07Z) and stays under DEC-024's own rules. This file is filed because the owner asked on 10-10 to "try to make H5 work" and BOOST now ends earlier in October. Its trigger is that halt. That is disclosed here, and it is why v2 needs its own fresh read.
- **Why it is filed after quant-proof's "park" recommendation.** Quant-proof found registration not justified **as a DEC-027 challenger or as a paired new-DEC test**: a +0.3 to about +2 pp paired effect needs far more than 660 pairs. This file asks a different question (v2's own book against the gate), whose power depends on v2's level, not on the paired gap. It is still expected to fail (section 11). **If quant-proof does not OK this filing on its final head, it does not merge.**
- **k = 1.** One rule, one deciding cell. C5, C15 and the fixed timers are not registered and are never added.

## 2. The frozen rule, copied exactly

The block below is the rule. Its sha256 is recorded under the block (check: `sed -n '/^~~~rule$/,/^~~~$/p' EXP/EXP-026-h5-boostclock-v2-prereg.md | sed '1d;$d' | sha256sum`).

~~~rule
# RULE H5-BOOSTCLOCK v2 (drafted 2026-10-10 from iter-r2 cell C10; frozen when EXP-026 merges)

Base. H5-BOOSTFLOOR v1, /data/mal/hunt-1008/h5-flows/RULE.md (sha256 c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c),
with the universe restricted to plain (non-synthetic) pools as EXP-024 Amendment 4 restricts it. Universe, BOOST tracking, trigger, entry,
fills, own impact, tier fee, 0.1 SOL stake, 55,000 lamports per send on the buy and on the sell, and the flat and pressure fail legs are
v1's, unchanged. Only the exit trigger changes.

## Exit (replaces v1's "Exit trigger at slot s0 + round(330 s / sps)")
- Pool clock: t(slot) = (slot - s0) * sps seconds after s0, with v1's s0 and sps.
- BOOST slices: the BOOST wallet's buys in the pool (v1's BOOST tracking; live, the per-pool BOOST vault), in (slot, tx_index,
  event_index) order. Slice i has time t_i = t(slot_i) and size a_i SOL.
- Usability lag OBS = 1.35 s: slice i may be used for a decision at pool time tau only if t_i + 1.35 <= tau.
  k(tau) = the number of slices usable at tau.
- Projection E, with k >= 3 usable slices (t_1..t_k, a_1..a_k):
    R_k = max(0, 17.585 - (a_1 + ... + a_k)) SOL;
    if R_k < 0.05 SOL: P_k = t_k (the budget is spent; the last slice has been seen);
    else: P_k = t_k + floor(R_k / mean(a_1, ..., a_k)) * median(t_2 - t_1, ..., t_k - t_(k-1)).
- Offset OFF = 10 s. Cap CAP = 330 s.
- Decision instant tau* = the first tau >= t(X) (X = our buy's landing slot) with k(tau) >= 3 and tau >= P_(k(tau)) - OFF.
  If there is no such tau below CAP, or the pool has fewer than 3 BOOST slices, or no BOOST wallet is found: no early exit.
- C = max(s0 + round(330 s / sps), X + 1), v1's exit trigger slot.
- Exit trigger slot: early exit -> min(max(s0 + ceil(tau* / sps - 1e-9), X + 1), C); no early exit -> C.
- The sell lands ceil(0.55 s / sps) slots after the exit trigger slot, END bound. Our buy stays in the pool until we sell.
  proceeds = tokens * Q' / (B' + tokens) * (1 - tier fee), as v1.
- v1's timer stays armed: a position not sold by slot C is sold at C.

## Code
- /data/mal/hunt-1008/iter-r2/h5-boostclock-exit/bc_rule.py: project_E, and cad_exit(t, a, off=10, cap=330, tmin=t(X), obs=1.35, kmin=3).
- /data/mal/hunt-1008/iter-r2/h5-boostclock-exit/s01_sim.py: cad_slot (the exit trigger slot) and fixed_slot(330) (C).

## Chosen on exploration data
- E: one of three projection formulas, chosen on discovery BOOST timing only (projection error, no P&L), s00c_calib.py.
- OFF = 10 s: from {5, 10, 15} on discovery (explore-0814) and confirmation (09-03..09-25) P&L, by FREEZE.md's frozen rule.
- OBS = 1.35 s = the 1.9 s entry latency minus the 0.55 s send-to-land, fixed a priori. CAP = v1's 330 s, fixed a priori.
~~~

**Rule block sha256:** `833c2175c1cae9ab061eed38419d744c44dae09e7734cdecb81f4c3980bb6d02` (computed 2026-10-10 on this draft; the merge head's value is the binding one, and the manager re-checks it in the Window record). `REPORT.md` at the time of writing: sha256 `a6ceb8d448e6e0d6b37f8f479c5b7f5ab18bb39e4772077de1b19ab3f7c235ae`.

**Frozen code** (sha256 from `out/freeze_sha.txt`, written 2026-10-10T08:06:52Z, and re-checked 2026-10-10 for this file):
- `bc_rule.py`: `6b3de49e6ae6fda3ca868bc4d77cd9078c3e04706cf12be112dc97718382651d`
- `s01_sim.py`: `f4e7c6a50bc83c2913ad665c1fcee2f2caa90deab6d86447c0c5469e8e4b4793`
- `bc_common.py` (v1 re-run, data guards): `49ce18ff31c42dac5b4caba4d6476ee27d674008bd3e6ba4485cbd8cba96e207`
- `s02_score.py`: `8e8a8f21f5ac426fb652e273770aef42aa6b1e0aa77a8144b8ab040103144501`

**What the rule leaves to this file.** The deciding cell is the 1.9 s entry with a 0.55 s sell lag, as EXP-024 §5 picks for v1. The live BOOST identity is the per-pool vault (`boost_src` "pda" or "event_authority"), as for v1. The frozen sim used v1's 1,600-slot hindsight detector for the BOOST wallet and a path-wide sps; quant-proof found both inherited and acceptable [measured, REPORT.md "The six checks"], and the live equivalents are the vault and the measured slot rate.

## 3. Data, window, stopping rule and seal

### 3.1 Source

- **The shadow's own records, written live after W0.** One detector feed, one process. v1 (`H5-BOOSTFLOOR-v1`) and v2 (`H5-BOOSTCLOCK-v2`) are evaluated on the same triggers; v2's entry is v1's entry, so the trades are the same pools.
- **Never read for EXP-026:** forward-1002, forward-1002ev, walk 2, the fast-0 tip archive, the EXP-024 read tool's inputs or output, any sealed block, and any canary record except the counts named in section 3.3.

### 3.2 Window and stopping rule

- **Counted trade.** A v2 trade counts if its pool's s0 (block time of the canonical pool's first print) is at or after W0, the pool is plain (section 4), its trigger record is not `excluded`, its pool close record has `reason` "horizon" and `gap` false, and the hours from its trigger to its exit landing are good (no gap record, no missing heartbeat).
- **Stopping instant E [pinned].** E is the first full UTC hour boundary h after W0 at which the counted trades with s0 in [W0, h) number **at least 100** and fall on **at least 5 distinct UTC dates**. If that has not happened by **W0 + 336 h (14 days)** or by **2026-11-06T00:00Z**, whichever is earlier, E is that instant. Pools with s0 at or after E are never scored.
- **The count job** (a MiScusi job, hourly) reads only the decision-time and structure fields of trigger and pool records (`mint`, `pool`, `s0`, trigger slot, `synthetic`, `synthetic_src`, `excluded`, `gap`, `reason`, `boost_src`) and the hour health records. It appends one line per hour to `/home/claude/data/exp026/counts.jsonl`: counted trades per date, dates, good and bad hours, and whether E is reached. It opens no outcome and no withheld file. A count is not an outcome.
- **The read instant.** One read, after all of: E has passed; every pool with s0 before E has its close record; the section 6.2 items hold; and the EXP-024 Look 1 report is written or 2026-10-17T12:00Z has passed, whichever is first (so no EXP-026 result exists while Look 1 is unread, as DEC-027 §6 item 6). **Deadline: E + 48 h**, or 2026-10-19T12:00Z if that is later. A read not run by its deadline is NOT_DECIDABLE, and the window is spent.

### 3.3 Outcomes withheld until the single read (the proposed option)

**Proposed design.** The shadow computes v2's exit decision and v2's outcome in real time, because only the running process holds the pool path. It writes every **v2 outcome-bearing field** (exit-landing state, proceeds, P&L, legs, the pressure inputs, and the price strip after the v2 exit) **only to a withheld store**, never to the hourly files that DEC-024 §6 lets people read. Nobody opens the store before the read instant. The read tool opens it once, under a lock (section 8).

- **The withheld store.** `<out-dir>/withheld-exp026/h5v2-<UTC hour>.jsonl`, mode 0600, one file per hour. At each hour close the shadow writes that file's sha256 into a clear `withheld_manifest` record in the normal hourly file. The read tool refuses if any hash differs. Helm is asked (not required) to put an auditd watch on the directory, as on the key.
- **v2 decision records.** Under DEC-028 Option A (no v2 live), v2's exit decision (tau\*, the exit trigger slot, k, P_k and its inputs) also goes to the withheld store. Under DEC-028 Option B the executor must act on it, so the shadow writes it in the clear only if started with `--h5v2-live-exit DEC-028-B` (logged in the start record). It carries no price.
- **The ban.** Before the read instant, no person, agent or job computes, opens or prints a v2 outcome (exit-state price, proceeds, P&L, mean, CI, day sign, win or loss, or a paired difference against v1) for any pool with s0 at or after W0, from any source. That includes **joining a v2 exit slot to v1's price strip or exit ladder**. v1's ladder (310, 320, 330 s …) is observed in real time under DEC-024 §6 and brackets most v2 exits; reading v1's records as DEC-024 allows is not a breach, and the join is.
- **Allowed before the read:** the section 3.2 counts; the A3 monitor's flags and values; hour health; BOOST timing fields of pool records (last slice, slice count), which are structure; the share of v2 exits that fired before the cap and the share that landed after the pool's last slice, **as counts from timing fields only** (these feed DEC-028 Option B's halts); v1's records as DEC-024 §6 allows.
- **A breach** is recorded here, dated, and the read is reported compromised. A compromised read cannot support a live request.
- **No peeking, and why it matters here.** The stopping rule is count-based and fixed, and the read runs once, so interim looks could not move the read. They could still move choices around it (withdrawing the file, the owner's live choice). The store makes that impossible for v2's own book.

**Options considered and not proposed:**
- **Real-time observation of v2's shadow outcomes** (as EXP-024 §3.1 and Amendment 2 do for v1). Rejected. It invites peeking at the gate statistics while the window runs, and v2 needs no live P&L watch under Option A.
- **Compute nothing until EXP-024 Look 2 is read** (about 2026-11-06). Rejected. It is slower than the owner's October window and runs into the 11-06 end of the declared observation.

### 3.4 EXP-024's seals and the declared observation

- **EXP-024 §3 seal.** It bars computing an H5 trigger outcome for an October V-range pool in its first 360 s from any source, except the declared observation. v2 trades v1's trigger, so the shadow's withheld v2 computation and EXP-026's single read are H5 trigger outcomes for pools inside Look 1's window `[2026-10-10T00, 2026-10-16T00)` and Look 2's added window `[2026-10-16T00, 2026-11-06T00)`. **They need a companion EXP-024 amendment** ("G-v2", section 13), merged with quant-proof's OK before the v2 shadow starts. Without it the v2 shadow does not start and W0 does not occur.
- **What G-v2 must say** (DEC-027 §5.4 G1 is the model; one amendment may serve both if it names `H5-BOOSTCLOCK-v2`):
  - (a) The shadow's withheld v2 computation and EXP-026's single read, for pools with s0 in [W0, E), are declared exceptions to §3. EXP-024's read tool is excluded from them, and its inputs are unchanged.
  - (b) Look 1 and Look 2 are always read and reported as written. They are never skipped, delayed, re-scoped or re-thresholded because of anything v2, its shadow, its canary or EXP-026's verdict shows.
  - (c) The Look 2 report (and the Look 1 report, if EXP-026 were read first) states that EXP-026's verdict and v2's book were known before it and could have influenced later choices.
  - (d) Under DEC-028 Option B, the v2 canary's own outcomes are observed in real time, as v1's are (§3.1, Amendment 2), and EXP-026 is read as written whatever they show.
  - (e) EXP-024 §3's premise that the canary's sells "fall at s0 + 330 s or later" fails for v2. Its conclusion still holds: a v2 sell comes after the pool's first qualifying print, which is the only trigger the pool ever has, so it cannot create a trigger.
  - (f) Written with v1's October aggregate outcomes in view (the declared observation), and before any v2 outcome exists.
- **Timing.** By section 3.2, EXP-026 is not read before the Look 1 report or Look 1's deadline. It will usually be read before Look 2 (about 11-06), so its verdict is known before Look 2. That is the (c) disclosure, and Look 2 is read as written.
- **The declared observation is not widened by this file** for v2's paper book. v1's canary and shadow outcomes stay observed as DEC-024 §6 says.

### 3.5 CAP-PICK and D1

- **CAP-PICK seal** (EXP-022 §9, DEC-024 §6). From 2026-10-16T01 to the end of EXP-022's read: no v2 trade, paper or live, on a mint the EXP-022 gate picked; no v2 record joined to a pick; no per-pool v2 P&L for a pick before each CAP-PICK look. The pick oracle seals a pool for every rule, and the shadow writes no v2 record, clear or withheld, for a sealed pool. The fail-closed rule (feed missing or stale for more than 60 s) stands.
- **The oracle dependency.** While the pick oracle is a stub, the shadow seals every pool from 2026-10-16T01 (DEC-024 §6, "Dependency"). **No EXP-026 trade accrues from 10-16T01 until PR #509 is merged and wired in.** Section 11 counts the cost.
- **D1** (EXP-024 Amendment 4, line 691). No synthetic class is joined to any v2 outcome for a counted-window pool before Look 2 is read. The read prices plain pools only. It computes no outcome for a synthetic or unclassified pool, writes no class column and splits no outcome by class. Restricting the population, as EXP-024 Amendment 4 does, is not a split.

## 4. Universe and pricing

- **Universe.** v1's universe restricted to plain pools: the shadow's decision-time class, `synthetic` exactly false and `synthetic_src` "rpc" (DEC-024 Amendment 2, Clarification 1). Excluded and unclassified pools do not count. `boost_src` must be "pda" or "event_authority" (DEC-024 Amendment 5 item 1's filter).
- **Re-classification before pricing (structure only).** The read re-classifies every counted pool with EXP-024 Amendment 4 B4 before any outcome is opened. A pool B4 calls synthetic or cannot classify is excluded. If B4 disagrees with the shadow on more than 1% of counted pools, the read is NOT_DECIDABLE. The count of disagreements is printed; nothing else about those pools is.
- **Deciding cell D [pinned].** 0.1 SOL; entry landing slot X = trigger slot + ceil(1.9 s / sps); v2's exit trigger slot (section 2); the sell lands ceil(0.55 s / sps) slots later; END bound; own impact; tier fee; V per print (the shadow's "pv" pricing, as the executor trades); 55,000 lamports per send on the buy and the sell; rent as EXP-024 §5.
- **Binding leg B1 (slow landing).** Entry at ceil(3.0 s / sps), sell lag ceil(1.35 s / sps). v2's decision is recomputed with tmin = B1's landing time and C = max(s0 + round(330 s / sps), X_B1 + 1). OBS stays 1.35 s (the rule's constant).
- **Binding leg B2 (15% guard).** D plus EXP-024 §5's buy `min_out` at the trigger print's post-trade price ÷ 1.15. A reverted buy costs one send.
- **Correction.** EXP-024 §5, unchanged: each trade loses the larger of DEC-021 §1's haircut and max(0, −2 × r̄) from E1, before rent and the fail mix. If E1's n is below 20 at the read instant, the read is NOT_DECIDABLE.
- **Fail legs.** EXP-024 §6: flat 0.85 × pnl + 0.15 × (−55,000); pressure (1 − p) × pnl + p × (−55,000), p from `tools.latency_curve.fit_curve` (slopes 0.8 / 0.35 at scale 1) with the intercept refit to mean p 0.289 on this book's own sends, per cell. The shadow's withheld record carries the pressure inputs (buys in the landing slot; buy lamports in the 2 s up to and including it).
- **sps and slots.** sps is the shadow's per-pool measured slot rate, as for v1. At 200 ms slots: 1.9 s entry is 10 slots, 0.55 s lag is 3, 3.0 s is 15, 1.35 s is 7, and 330 s is 1,650 after s0 [arithmetic].

## 5. Gate and decision rule

**PASS** if and only if **every** item below holds on D under **both** the flat and the pressure fail model:

1. n ≥ 100 counted trades.
2. ≥ 5 distinct UTC dates (of s0), with a majority positive (daily total > 0).
3. The lower 90% CI bound of mean SOL per trade > 0 (1,000 bootstrap draws over trades, seed 1, 5th percentile).
4. Total SOL > 0 after removing the top 3 trades.
5. Total SOL > 0 after removing the best UTC date. **Binding.**
6. **B1 and B2**, each with the correction, under both fail legs: mean > 0 and total > 0 after removing the top 3 trades. **Binding.**

Items 1–4 are the lab's promotion gate, as CLAUDE.md states it. Items 5 and 6 can only turn a pass into a fail: item 5 is the lab's top-day check (a concentrated book is not called positive), and item 6 exists because any live buy carries the guard and may land late. **Otherwise: FAIL.** A NOT_DECIDABLE read is not a pass.

**Reported, never deciding:** the paired difference against v1 (section 9); the EXP-024 §7 item 6 day-level t at α 0.025 (see section 10 on the open choice); the trade-level one-sided bootstrap p (10,000 draws, seed 1); the date-cluster CI90 (`common.gate`, 1,000 date resamples, seed 1).

## 6. Preconditions

### 6.1 Before W0 (if any is missing, W0 waits; by 2026-10-23T00 the file is withdrawn)

- **P0.** This file is merged with quant-proof's OK on its final head.
- **P1.** G-v2, the companion EXP-024 amendment (section 3.4), is merged with quant-proof's OK.
- **P2. md5 decision-equivalence on 2026-09-20** (fast-pool-0918; the day DEC-024 Amendment 1 item 3 and DEC-027 §4 item 3 use; exploration tape, not evidence of edge). The shadow's engine in replay mode, on the build that will run:
  - v1's trigger list still hashes to **`75cb0b0c585bc2479137cae31330e73e`** (72 decisions), so adding v2 cannot move v1;
  - **v2's exit decisions** hash equal to the frozen code's: one line per decision, `mint,trigger_slot,landing_slot,early_exit,exit_trigger_slot`, sorted by mint, md5 over the lines. The frozen side is `bc_rule.cad_exit` and `s01_sim.cad_slot` run on the same pools at the 1.9 s leg with OFF 10, CAP 330, OBS 1.35;
  - the B1 decision list is hashed the same way and recorded;
  - quant-proof re-runs the proof on the final head. Job id, head and both md5s go in the Window record.
- **P3. Brute-force check.** τ\* re-derived on a 0.01 s grid for every 09-20 decision and for at least 900 synthetic slice lists, 0 mismatches (the REPORT.md check, repeated on the live code).
- **P4. Shadow build.** The rule registered with `rule_id` and the rule block sha256 in the start record; the withheld store and manifest (section 3.3); per-decision logging of `boost_done` and the projection inputs (k, t_k, Σa, mean a, median interval, P_k, τ\*, early_exit, each slice's slot, block time, receive time); a `reviewer` pass; quant-proof's OK. v1's records are unchanged by it (P2).
- **P5. The shadow is running the merged build** (start record shows the rule id and sha256), with the EXP-024 Amendment 2 flag set for pools from 2026-10-16T00, and has run at least 6 h on live triggers with 0 build errors before W0.
- **P6.** The count job (section 3.2) is running.

### 6.2 Before the read (counts and hashes only)

- **P7.** The read tool is merged with quant-proof's OK (section 8), and its blob is recorded.
- **P8.** Every withheld hour file matches its manifest hash.
- **P9.** E1 is recorded with n ≥ 20.
- **P10.** Section 4's B4 re-classification is done (counts only).
- **P11.** The A3 runs inside [W0, E) showed none of the five H5 halt flags (EXP-024 Amendment 4 C1).

## 7. Kill rules

**NOT_DECIDABLE** (not a pass) if any of these holds:
- any of the five A3 halt flags fires inside [W0, E), above all `boost_budget_or_slices_changed` (E assumes a 17.585 SOL budget);
- more than 5% of the window's hours are bad;
- B4 disagrees with the shadow on more than 1% of counted pools;
- E1's n is below 20;
- a withheld file fails its hash, or a withheld hour is missing for an hour with counted trades;
- the read is not run by its deadline.

A NOT_DECIDABLE verdict after any outcome is opened spends the window. Before any outcome is opened (a P7–P11 refusal) it also spends the window, because v2 is not re-filed on these hours.

**FAIL, v2 retired.** No retune, no re-read, no other offset (C5, C15), no other OBS or CAP. A further BOOST-clock exit would be a new rule with its own exploration and a new filing.

**No live support** from a PASS (the conditions can only remove support) if any of these holds:
- v2's exit landed after its pool's last BOOST slice on more than 15% of counted trades (the share was 0.98% unstressed and 7.94% under S8 in iter-r2 [measured]);
- the **receive-time decision** (slices used only once received, and once t_i + 1.35 ≤ τ) differs from the rule's decision on more than 10% of counted trades (section 9). The paper rule then assumes an observation speed the live system did not have;
- the plain-pool median BOOST last slice over the window is below 330 s (outside the dose range iter-r2 tested, section 11);
- B2's guard reverted more than 40% of buys;
- a seal breach, or any A3 halt after the pass.

## 8. Read tool (`tools/exp026_read.py`, precondition P7)

- **Refusal.** It refuses unless: the section 0 lines match exactly once; this file is clean against HEAD; the rule block hashes to the merged value; the frozen code hashes match section 2; the P2 md5s are recorded; E1 has n ≥ 20; every withheld file matches its manifest.
- **Inputs.** The shadow's hourly files and the withheld store for pools with s0 in [W0, E), and the count job's log. It refuses forward-1002, forward-1002ev, walk 2, sealed blocks, forward-paper and runner paths, key files, the canary ledger, and any pool with a seal record.
- **Lock and ledger.** An O_EXCL lock before the first withheld file is opened. `started`, `completed` or `aborted` lines in `/data/mal/exp026/READS.jsonl`. It refuses a second run.
- **No overrides.** Sections 4, 5 and 7 are constants.
- **Order.** It prints the verdict and the report to stderr before writing any file.
- **Disclosure.** The report says: v1's canary and shadow outcomes were observed in real time for the same pools (DEC-024 §6); under DEC-028 Option B, so were the v2 canary's; v2's own paper outcomes were withheld until this read.

## 9. Report-only (never deciding)

- **Paired against v1**, on the same counted pools: v2 minus v1 per trade, SOL and pp of stake, flat and pressure; daily CI90 (date-cluster, 1,000 draws, seed 1); dates with a gain; drop-best-gain-date; ex-top-3. v1 is priced by the same tool from the same shadow records at its 330 s exit.
- **The realized shift**, next to the paired effect (REPORT.md §7): the window's plain-pool median BOOST last slice against September's 342.9–347.4 s (VERIFY), and the share of plain pools whose last slice is before 329.5 s, against September's 0.1026 (S4-equivalent) and 0.2239 (S8-equivalent) [measured, REPORT.md claim 6].
- **Exit mechanics:** share of early exits; τ\* distribution; exits landing after the last slice; projection error (actual last slice minus P_k at the decision).
- **Legs:** the receive-time decision; OBS 2.45 s; the rule's 1.3 s entry; START bound; stake 0.02 SOL (the canary's size, with fixed costs at 0.55% of stake); no correction; a sell-retry stress (p per sell, retries every 2 s at +55,000 lamports).
- **Statistics:** per-date table; median; mean without the top 5% of trades; share of P&L from trades above +100% gross; first-half and second-half means; the day-level t.

## 10. Multiplicity

- **The H5 exit family so far: 20 selectable exit rules** — iter-r1 sell-budget (6 cells), VERIFY's exit grid (7 exit times, 310–350 s), iter-r2 (7 cells: F300, F315, F320, F325, C5, C10, C15). REPORT.md counts iter-r1 as 14 arm cells, which gives "at least 28". iter-r2 alone computed 148 arm-level P&L cells on top of 8 v1 control books per part. C10 is the best of 7 (of 3 cadence cells) and of at least 20 exit rules. **H5 itself is the best of at least 23 hunt families** (JUDGE.md:58).
- **The confirmation tape is not independent.** VERIFY generated the BOOST-end hypothesis on it, and its exit grid was known before iter-r2's freeze. m ≥ 14 from EXP-024's merge, about 37 counting untracked readers (EXP-024 §9). That is why only fresh forward hours can decide.
- **No α slot is free.** The promotion gate's CI90 lower bound is a one-sided 5% bootstrap test. DEC-021 §8 holds the promotion-eligible October families at ≤ 0.05 in total, and both 0.025 slots are taken (EXP-022; EXP-024 through DEC-023). **A PASS here clears the promotion gate as CLAUDE.md writes it, but it adds a promotion-eligible read above DEC-021 §8's budget.** This draft uses the gate as the deciding bar, as the request specifies. **Open choice, fixed before merge** (quant-proof and the owner):
  - **M2 (as drafted).** Gate only. A DEC-021 §8 amendment records EXP-026 as outside the slot accounting and states that the lab-wide October error rises above 0.05.
  - **M1 (stricter).** Add the day-level t (EXP-024 §7 item 6) as a binding item at α 0.025, with a third DEC-021 §8 slot granted by the owner. It lowers P(pass) further (section 11).
- **EXP-024 is untouched.** Its k = 1, α 0.020 / 0.005 and m are unchanged. EXP-026 is a separate read, not a look of EXP-024.
- **No `data/tries.jsonl` line at registration.** The 20 exit tries are counted here.

## 11. Honest expectation and P(pass)

**What iter-r2 found, after quant-proof's corrections** [measured, REPORT.md, exploration; not evidence]:
- **Unstressed, v2 is non-inferior, not better.** Confirmation paired +0.2788 / +0.2620 pp (flat / pressure); the top 3 trades carry 68% of the gain (ex-top-3 +0.0991 of +0.3125 SOL). Discovery +0.1126 pp, daily CI90 [−0.0067, +0.0379], ex-top-3 −0.0181 SOL: it does not replicate there.
- **The stressed gain is true by construction.** S8 paired +3.2086 pp flat is v1's own loss from 330 s to 338 s (+2.8715), plus the unstressed edge (+0.2788), plus the cap relaxation (+0.0583). It assumes October is a pure BOOST shift with the cliff moving with it.
- **The false-alarm case costs a little.** If BOOST looks 8 s earlier but the cliff does not move (SL): −0.3355 pp flat, daily CI90 [−0.0420, +0.0074]. If the drop moves earlier and BOOST does not, v2 is about v1. A cliff that leads the last slice by more than 10 s is untested.
- **Where October sits.** 10-10's plain-pool share before 329.5 s, about 0.151 (DEC-024 Amendment 5, partial day), lies between September's S4 (0.1026) and S8 (0.2239) equivalents. That puts the paired gain at about **+1.39 to +3.21 pp before shrinkage**. After shrinkage (best of 7, in-sample tape, family of ≥ 20), expect **about +0.5 to +2 pp** [est].
- **v2's own level.** EXP-024 §15 puts v1's central October flat mean at about +2 to +4% before the correction. v2 adds the shrunk gain above, so **about +2.5 to +5.5% before the −0.8 pp correction stand-in** [est]. JUDGE.md's prior that October's mean is > 0 is 0.35 for v1; v2's is not much higher, about 0.40 [est].

**Power at n = 100** [inferred, normal approximation; not a simulation]:
- With a per-trade SD of about 0.45 × stake (assumed, not measured here; H5's tails are heavy: 5.78% of confirmation trades carry 110.2% of flat P&L, EXP-024 §16 item 8), the trade-level CI90 lower bound clears 0 only if the realized net mean is above about **+7.4% of stake**.
- The pressure leg runs about 1.5 pp below flat, and the correction takes about 0.8 pp, so the pressure leg alone passes item 3 with probability about Φ((μ − 9.7) / 4.5), where μ is the true flat mean before the correction:

  | μ (flat, before correction) | +2% | +3% | +4% | +6% | +8% |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | P(item 3, pressure leg) | 0.04 | 0.07 | 0.10 | 0.21 | 0.35 |

- Items 2, 4, 5 and 6 cut that further. Ex-top-3 alone is hard at n = 100 when a few trades carry the book. Taking about half [inferred]: P(pass | μ = +4%) ≈ 0.05, P(pass | μ = +6%) ≈ 0.10.
- **Feasibility.** The 100 trades need the CAP-PICK oracle wired in (section 3.5). Without it, only [W0, 2026-10-16T01) accrues: about 3–4 days at 8–16 plain triggers a day, 25–65 trades, a FAIL on item 1 [inferred]. The plain trigger rate itself is thin: about 16 a day rests on 7 triggers in 10.3 h, and quant-proof puts the executor at about 8 a day (DEC-027 §5.1).
- **Overall: P(pass) ≈ 0.03, range 0.01–0.06** [est]: about 0.40 × 0.05–0.10, times the chance the oracle is wired in time. **The expected outcome is FAIL.**
- **Before merge,** quant-proof is asked to run a resampling power check on these legs (as EXP-024 §15's `h5_power5.py`, on the iter-r2 `sim_conf.parquet` C10 rows, n = 100, both rates). This author could not run one on this host (no pandas). It changes no rule.
- **If it passed** [est]: at T1 (0.10 SOL), 8–16 trades a day and a net +2 to +3%, about 0.016–0.05 SOL a day. That is small against the owner's October target.

## 12. Disclosures: what was in view

1. **iter-r2's outputs**, including every cell's discovery and confirmation numbers, the decomposition, the false-alarm case and quant-proof's verification. The confirmation tape (09-03..09-25) is in-sample for the lab.
2. **October BOOST timing (structure, no outcome):** DEC-024 Amendment 5's plain and all-pool medians and shares (job #482), the A3 run #478 (median 333 s after migrate, n = 9) and its reconciliation, the task text's plain-pool day median of about 337 s on 10-10.
3. **v1's October aggregate canary and shadow outcomes** were observable to the manager under DEC-024 §6. This author opened none: no canary ledger, no shadow outcome record. The canary's 3 trades and +0.005199 SOL realized at STOP are quoted from DEC-024's halt record, where they are labelled not evidence.
4. **No v2 outcome on any October pool exists.** v2 has not run on live triggers.
5. **The draft was written after v1's §5.1 halt and because of it** (section 1).

## 13. Companion texts and builds

| By | Item |
| --- | --- |
| before merge | quant-proof OK on this file's final head, including the section 11 power check and the M1/M2 choice (section 10). |
| before merge | The DEC-021 §8 amendment for the chosen M1 or M2 (owner). |
| before W0 | **G-v2**, the EXP-024 amendment of section 3.4 (P1). It may be one text with DEC-027's G1. |
| before W0 | The shadow build (P4) with the md5 proof (P2) and the brute-force check (P3); reviewer pass. |
| before W0 | The count job (P6). |
| before W0 | A [DEC-023](../DEC/DEC-023-h5-family.md) note: a second rule in the H5 family, its read, and the α route chosen in section 10. |
| before W0 | An `EXP/README.md` row (in this PR). |
| before the read | `tools/exp026_read.py` (P7) with quant-proof's OK; E1 (P9). |
| any time | [DEC-028](../DEC/DEC-028-h5-v2-canary.md) (draft): the owner's choice between Option A and Option B. |

## 14. Out of scope

- Tool code (the shadow change, the read tool, the count job).
- The live executor change and any live trade (DEC-028).
- C5, C15, the fixed timers, C-BX, C-LF, C-Q35, C-SYN.
- Any sealed-block claim, and any change to EXP-024 or EXP-022.

## Window record

(Empty. The manager adds dated lines here: the merge instant, the merge-head rule sha256, the P2 job, head and md5s, the shadow start record, and W0.)

## Sources

- `/data/mal/hunt-1008/iter-r2/h5-boostclock-exit/`: `FREEZE.md`, `REPORT.md` (with quant-proof's verification, workflow wf_1604b712-f06), `bc_rule.py`, `s01_sim.py`, `out/freeze_sha.txt`.
- `/data/mal/hunt-1008/h5-flows/{RULE,REPORT,VERIFY}.md`, `/data/mal/hunt-1008/JUDGE.md`, read as cited in EXP-024.
- [EXP-024](EXP-024-h5-boostfloor-part1-prereg.md) (§0–§16, Amendments 1–4), [DEC-024](../DEC/DEC-024-h5-live-canary.md) (§4–§8, Amendments 1–5, halt record), [DEC-027](../DEC/DEC-027-h5-champion-challenger.md), [EXP-022](EXP-022-cap-pick-part1-prereg.md) §9.
- `tools/h5_shadow.py` (header, record types, seals) and `tools/h5_executor.py` (exit timer, BOOST rules), read as code only.
- `docs/HANDOFF.md`, STATE 10-10 ~07:30Z.
- No sealed data, forward-1002, forward-1002ev, walk 2, forward-paper P&L, or canary or shadow outcome record was opened to write this file.
