# DEC-027: H5 champion-challenger plan (paper variants beside the live v1, one switch file)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-09; revision 2 after quant-proof's review of 975d8db (NOT OK), which this revision applies.** Paper only until a switch is approved. The champion is H5-BOOSTFLOOR v1, live under [DEC-024](DEC-024-h5-live-canary.md). This file freezes no challenger and starts no trade. It takes effect on merge, with the owner's OK and quant-proof OK on its final head. **Gating item G1** (section 5.4): no multi-rule shadow starts until the companion EXP-024 amendment has merged. |
| **Decider** | Vaan (owner) for every live switch (a dated line, section 6). The Claude manager runs the rest. |
| **Date** | 2026-10-09 |
| **Builds on** | DEC-024 (sections 4, 5, 6, 7, Amendments 1 to 3), [DEC-021](DEC-021-champion-challenger.md) (the lab's champion-challenger precedent: §4 to §7, and the owner decision "a challenger swap never changes size or wallet"), [DEC-023](DEC-023-h5-family.md), [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) (sections 3, 3.1, 7, 11, Amendments 2 to 4), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) section 9, DEC-018, DEC-019, [DEC-020](DEC-020-size-step-proposal.md) (sizing). |
| **Amends** | **On approval, DEC-024 section 4 "Rule" row only** ("Frozen H5-BOOSTFLOOR v1 …"), so that the executor may trade a pre-approved variant that the root-owned `VARIANT` file names (section 7), after the owner's dated line for it. Every other DEC-024 limit, halt, seal and scale-up condition stands. |
| **Does not amend** | The promotion gate, for any book. EXP-024's rule, parameters, looks, alpha, seal and read tool (G1 is a separate amendment to EXP-024). EXP-022 and its CAP-PICK seal. DEC-021 §6 to §9. The tier ladder and the T0, T1, T2 limits (DEC-024 Amendment 3). DEC-024 sections 5 to 7. |

## 0. The request and the design (2026-10-09)

- **The owner's request.** Run 3 or 4 good H5 variants in paper beside the live v1, so the live bot can move to a better one once the 0.02 SOL speed trial is done.
- **The manager's design, which the owner approved.** One shared detector feed. A paired comparison on the same triggers. A switching rule written in advance. A root-owned variant file, like `/etc/mal-h5/TIER`, so Helm can move between pre-approved variants without a reinstall. Each live switch still needs the owner's dated line.
- **Provenance.** Relayed by the manager on 2026-10-09. The author did not open the owner's notebook entry and does not quote the owner's words here. Revision 2 follows quant-proof's review of #526 at 975d8db as the coordinator relayed it; the author did not open the review file.
- **How many this DEC can deliver.** The owner asked for 3 or 4. This revision allows **at most 2 challengers ever frozen** (section 6, item 4), so it cannot deliver 3 or 4. Section 10 asks the owner.
- **What the live trial's override covers.** The owner's override ([DEC-024](DEC-024-h5-live-canary.md) section 7, `OWNER_OVERRIDE_CONFIRMED`) was given for "this one strategy", H5 v1. EXP-024 section 11 (line 333) says an exit tied to the observed BOOST end, a different Q\*, or a guard other than 15% "would each be a **new** rule". So the override does not extend to a challenger by itself. Each switch line must say whether the owner extends it (section 6, item 7).

## 1. Scope and what this is not

- **Paper first.** Challengers run in the keyless shadow only. No challenger sends a transaction until the owner's dated line, a compiled-in variant, and Helm's file write (section 7).
- **Not gate evidence.** A challenger's paper result is not a book, not a promotion, and not EXP-024. No report, note or Console entry may say a challenger is positive or "passes". It is an unpromoted trial in every report (DEC-024 section 7).
- **Not a second EXP-024.** EXP-024's Look 1 and Look 2 read the frozen v1 on the tape, as written. Nothing a challenger shows may change any EXP-024 parameter, delay a look, re-scope it, or re-threshold it. A PASS or FAIL speaks about v1 only. A live variant has no formal read of its own (section 8).
- **Winner's curse.** Section 8 says why the switching rule exists.
- **No live-halt is answered by a switch.** DEC-024 section 5 says "A halt is never followed by a retune". A variant is never switched on to get past a halt.

## 2. The champion

- **H5-BOOSTFLOOR v1** (`/data/mal/hunt-1008/h5-flows/RULE.md`, sha256 `c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c`; EXP-024 section 2): the first non-BOOST sell at 0 to 300 s with post-trade Q at most 40 SOL incl. V and BOOST spent below 0.999 x 17.585 SOL, held to s0 + 330 s, one trade per pool, 15% buy guard.
- Live variant `v1` is the executor's default and fail-safe (section 7).
- **Stays the reference arm after any switch.** The shadow keeps scoring v1 on every pool, so a revert is always possible and every later comparison stays paired.

## 3. Challengers: candidates, not yet frozen

A candidate becomes a challenger only through a **freeze record** (section 4). The four below are the candidates this DEC lists. No fifth is added without an amendment. A frozen challenger counts in k whether or not it is later dropped, and **k is at most 2** (section 6, item 4).

Two of the candidates add something v1 does not have: C-LF a filter on new information, C-BX an event exit. **C-Q35 is a threshold retune of v1** and is labelled as one. EXP-024 section 11 calls a different Q\* a new rule needing its own discovery on exploration data, and section 4 gives it that discovery.

| ID | Idea | Hypothesis | Status |
| --- | --- | --- | --- |
| **C-LF** (a) | v2 loss filter: skip the entries a decision-time filter flags | Some of v1's large losers are visible at the trigger print | Candidate. **Not freezable until it has rule text and a REPORT.** |
| **C-BX** (b) | Exit tied to the observed BOOST end | v1's fixed 330 s timer sells after the cliff on pools where BOOST ends early | **Parked. Not to be frozen under this DEC.** |
| **C-Q35** (c) | Threshold retune: Q\* = 35 SOL | Trimming the top of the 30 to 40 SOL band leaves less downside per entry | Candidate, with a numeric withdrawal rule. |
| **C-SYN** (d) | H5 on synthetic-migration pools | v1's rule also works on the pools EXP-024 Amendment 4 excluded | **Blocked until Look 2 is read** (about 2026-11-06). |

### 3(a). C-LF: the v2 loss filter

- **Idea.** A filter on decision-time fields, applied to v1's trigger. A pool the filter flags gets no trade. The entry, exit, Q\*, guard and stake are v1's.
- **Why it is a different idea.** It adds new information (a pool or flow feature at the trigger print) that v1 never used. v1 has no loss control: it holds to 330 s whatever happens (`/data/mal/hunt-1008/h5-lossfilter/MAP.md` section 4, [measured]).
- **Exploration facts, from MAP.md section 4** (frozen trade files, no filter built, counts only):
  - on the 1,124 confirmation trades, 212 (18.861%) lost 30% or more of the stake (net, binding cell); their flat sum is -8.3027 SOL against a book total of +13.9990 SOL;
  - in the **paired unit** (0 where the filter skips), skipping all 212 at zero cost would add **7.387 pp** to the flat mean (the big-loser contribution, -7.387 pp of +12.455%). That is a **hindsight ceiling and not a filter**; no decision-time filter reaches it, and a real filter also removes winners.
  - The retained-set mean of +24.454% that MAP.md also prints is a per-trade mean on fewer trades. It is not the paired unit and is not used here.
- **The switching bar against that ceiling.** The section 6 effect floor of +3.0 pp is about 40% of the 7.39 pp ceiling. A filter would have to capture two-fifths of the best case that nothing reaches.
- **Not freezable until there is rule text and a REPORT.** Rule text TBD, from the running research at `/data/mal/hunt-1008/h5-lossfilter/`. As of this DEC the study has built no filter and joined no feature to an outcome (MAP.md section 0). The REPORT is an exploration write-up with the discovery and confirmation numbers and the `data/tries.jsonl` count, with quant-proof's OK.
- **Limits on any C-LF rule:**
  - **features are computed in-process, with no RPC on the buy path** (DEC-024 Amendment 2, Clarification 1 keeps RPC off that path);
  - **no field derived from the synthetic classifier** (`synthetic`, `synthetic_src`, `tools/synthetic_class.py`), under EXP-024 Amendment 4 item D1;
  - every feature exists at or before the trigger print, and the shadow logs it (section 5.3);
  - derived on the discovery block (explore-0814) and checked on the confirmation set once. That set is in-sample for the lab (EXP-024 section 9), so the check **is not evidence**;
  - every configuration tried is logged in `data/tries.jsonl` (DEC-021 §1), and the count is stated in the Freeze record. The search size bears on how far the exploration estimate shrinks, not on the paper test's alpha;
  - **no October shadow or canary outcome enters the derivation.** The declared observation (EXP-024 section 3.1) lets the team watch v1's aggregate October outcomes. A filter tuned after looking at them is not frozen blind, and the Freeze record says what was in view.
- **Paired shape.** The challenger's trades are a subset of v1's. The paired difference is the negative of the SOL on the pools the filter skipped.

### 3(b). C-BX: exit tied to the observed BOOST end (parked)

- **Do not freeze.** Quant-proof's review of 975d8db found it too thin to be worth a slot:
  - it differs from v1 on only about **3.5% to 6.5% of trades** (the trades where BOOST had already ended at v1's exit, `h5-flows/VERIFY.md`, "NEW: the exit is a timing race"), and those trades **still returned +2.91%** (mean; VERIFY.md does not say whether pooled or per block). Its gain is at most about 1 pp [quant-proof];
  - the regime in which it would help, BOOST ending early, is the one in which DEC-024 section 5 item 1 halts the canary, and DEC-024 section 5 bars answering a halt with a retune.
- **Candidate rule text, kept for the record, not frozen.** Same universe, trigger, entry and guard as v1. Sell when the BOOST vault's last slice is detected (cumulative spend reaches 0.999 x 17.585 SOL), or at s0 + round(330 s / sps), whichever comes first. If BOOST stops short of that line, the timer sells.
- **Reopening** needs an amendment with a structure count of the early-ending share, and it counts in k (which is capped at 2).
- **Not listed:** selling on a slice count. It is the same idea on a different cut and is not proposed.

### 3(c). C-Q35: a threshold retune, Q\* = 35 SOL

- **Label.** This is a **threshold retune of v1**, not a new idea. The sentence "not a threshold tweak" does not apply to it.
- **Candidate rule text (not frozen).** v1 with one constant changed: the trigger needs post-trade Q at most **35 SOL** incl. V (v1: 40). Universe, window (0 to 300 s), BOOST test, entry, exit at 330 s, guard and costs are v1's. One trade per pool, on the first print that meets the 35 SOL test.
- **What the evidence does and does not say.**
  - The rule's discovery grid was Q\* in {40, 50, 60}: 40 positive on every exit, 50 weaker, 60 about zero (`h5-flows/REPORT.md`, line 24). Across that grid, smaller was better.
  - **That trend does not continue below 40.** The one finer slice in `REPORT.md` (line 23; the D = 0.4 version, H = 15 s) gives net **+10.9% for Q in (0, 30], +16.1% for (30, 40] and +2.1% for (40, 50]**, with n = 82 / 31 / 89. It is not monotone, and its counts are small.
  - So 35 is a test of whether trimming the top of the (30, 40] band helps. It has no supporting trend. The only reason is the mechanism [inferred]: with constant product and Q at least V, the loss floor on an entry is near 1 - (Q_exit / Q_entry)^2 (MAP.md section 4), about -81% at Q 40 and about -75% at Q 35 with the exit near the V floor of 17.6 SOL.
- **The value is picked now, once, and no other value is tried.** This section is written without opening any October shadow, canary or tape outcome.
- **Tries.** The Q\* grid is logged as **4 tries** ({35, 40, 50, 60}) in `data/tries.jsonl`.
- **Numeric withdrawal rule, fixed now.** One exploration run, on explore-0814 only, at the 1.9 s binding leg (the frozen `s14_boostdip.py` with `DS=35`). **Withdraw C-Q35 if any of these holds:**
  - the flat mean is **at most +11.593%** (v1's explore-0814 binding-leg flat mean, MAP.md section 3);
  - the pressure mean is **at most +10.596%** (v1's);
  - the trigger count is **below 851** (half of v1's 1,702).

  The confirmation look is report-only and **cannot rescue it**. A withdrawn C-Q35 is not replaced: no 30 and no 45.
- **Cost.** Fewer trades, so fewer paired pools in the window. Its pairs are looser than C-LF's, because it enters on a different print.

### 3(d). C-SYN: H5 on synthetic-migration pools (blocked)

- **Blocked until Look 2 is read, about 2026-11-06** (Look 2's earliest run is about 11-06T03Z; its deadline is 2026-11-14T00:00Z, EXP-024 section 8.1).
- **Why.** [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendment 4, item D1 (line 691): joining synthetic class to any H5 outcome (a fill, exit, P&L, mean, CI, day sign, win or loss, or any field derived from one), from any source, for any pool with s0 in a counted window, before Look 2 is read, is a breach of section 3. The read is then reported compromised, and a compromised read cannot support a live request.
  - C-SYN's whole evidence is H5 outcomes on synthetic pools, so its paired comparison **is** that join.
  - The shadow stopped signalling synthetic and unclassifiable pools (DEC-024 Amendment 2 item 1), and the executor refuses them. There are no such outcome records to compare.
  - The full-universe book "never decides" and is not computed or reported before Look 2 (EXP-024 Amendment 4 item A3).
- **Binding in this DEC, for C-SYN only.** The block extends to pools with s0 in [2026-10-08T20:25Z, 2026-10-10T00). Joining synthetic class to any H5 outcome (shadow, canary, tape or paper) for such a pool before Look 2 is read makes **C-SYN permanently ineligible**. The breach is disclosed in the Look 2 report. It is **not an EXP-024 breach clause**: EXP-024 D1 covers counted-window pools, and this DEC does not amend it.
- **No design work is done on C-SYN beyond this section.** After Look 2 is read it needs its own freeze, exploration and a fresh paper window, so its earliest decision is late November, after the October mandate window.

## 4. Freezing a challenger (before its paper window starts)

A candidate has no standing until every step below is done and recorded in a dated **Freeze N** amendment at the end of this file. A third freeze is refused (section 6, item 4).

1. **Exploration discovery, on exploration data only.**
   - Allowed ranges are the exploration pool of MAP.md section 2: explore-0814 for discovery, and the 09-03 to 09-25 blocks for one confirmation look. Nothing else.
   - Never read: a sealed block, forward-1002, forward-1002ev, walk 2, forward-paper, the fast-0 tip archive of 10-05 to 10-07, and any shadow or canary outcome.
   - One run per candidate. For C-Q35, the run and its **numeric withdrawal rule** are in section 3(c). The result is reported whatever it shows.
   - The run can **withdraw** a candidate. It cannot change the candidate's text and cannot add a second value. **The confirmation look is report-only and cannot rescue a candidate the discovery run withdrew.**
   - It is not evidence of edge. The confirmation blocks are in-sample for the lab (EXP-024 section 9: m at least 14, about 37 counting untracked readers). Only the paper window counts for section 6.
2. **The rule text, exact.** A fenced `rule` block in the Freeze amendment, with its sha256 and the one-line check command (as EXP-024 section 2 does). No constant is left to code.
3. **A decision-equivalence proof, by md5, on 2026-09-20.** This is the day [DEC-024](DEC-024-h5-live-canary.md) Amendment 1 item 3 used.
   - The champion's trigger list must stay equal to `75cb0b0c585bc2479137cae31330e73e` (72 decisions), so adding a rule to the shadow cannot move v1.
   - Each challenger's list from the live engine must equal its frozen rule's list from the offline scorer.
   - **The proof covers each variant's exit decisions as well as its entries:** the exit trigger slot of every decision must match the frozen scorer's.
   - The day is exploration tape. The proof is not evidence of edge.
4. **Keyless run.** The shadow carries the rule on live triggers with 0 build errors, and v1's records are unchanged by it.
5. **Reviews.** A `reviewer` pass on the shadow change and quant-proof's OK on the Freeze record, including the power statement (section 6, item 11).
6. **The window opens at the first full UTC hour** after all of these hold: **G1 has merged**, the Freeze amendment has merged, and the shadow's start record shows the rule id with its sha256. No earlier hour is ever scored for that challenger. Replaying a challenger over hours that began before its window, even to "see how it would have done", is a breach of this DEC and makes that challenger ineligible.
7. **After the freeze, no edit.** A change of any constant is a new candidate with a new id. The old one is withdrawn and still counts in k.

## 5. Paper evaluation

### 5.1 Data and window

- **Source.** The shadow's own records, written live after the challenger's window opens. One detector feed, one process, N registered rules, each record tagged `rule_id` (section 5.3). The paired scorer is a later PR. It reads those records only. It never reads forward-1002, walk 2, the tip tape, or the EXP-024 read tool's output.
- **Hours.** Pools whose s0 is at or after the window's opening instant and before **2026-11-06T00** (the end of the declared observation; the shadow withholds outcomes after it, DEC-024 section 6). A decision that needs later pools waits for Look 2.
- **Excluded the same way for both arms.** Pools flagged `gap`, and bad hours (a gap record or a missing heartbeat). They are counted and reported.
- **Pools it never sees.** Synthetic and unclassified pools (DEC-024 Amendment 2) and, from 2026-10-16T01, CAP-PICK picks (section 5.4).
- **Pairs stop accruing while the oracle is a stub.** DEC-024 section 6 ("Dependency") has the shadow seal every pool from 2026-10-16T01 until the real CAP-PICK pick oracle (#509) is merged and wired in. **No pair accrues from 10-16T01 until then.**
- **Rate [measured, thin].** "About 16 non-synthetic triggers a day" (`docs/HANDOFF.md`, STATE 10-09 ~09:30Z, "Volume finding"; MiScusi n_KqwGD1bzt_lbpg) **rests on 7 triggers in 10.3 h**. Quant-proof puts the executor at about 8 a day [quant-proof]. So 100 paired pools take about 6 days at 16 a day and about 12 days at 8 a day, and the figure is not firm. The rate may not be used as a basis for any EXP-024 change (EXP-024 Amendment 3, line 631), and it is not used as one here.

### 5.2 Pairing and pricing

- **Unit.** One row per eligible pool in which at least one of the two arms trades. An arm that does not trade on the pool is 0 (DEC-021 §4). The paired value is challenger minus champion, in SOL.
  - C-LF shares v1's entry, so its pairs are tight (ρ high).
  - C-Q35 enters on a different print, so its pairs are looser (ρ lower), and some pools are traded by only one arm.
- **Price.** The EXP-024 deciding cell D: stake 0.1 SOL, 1.9 s entry, END bound, own impact, tier fee, exit lag 0.55 s, 55,000 lamports per send, and the section 5 correction (the larger of the DEC-021 §1 haircut and the E1 term; the haircut alone, stated, if E1 is not yet recorded). Both fail legs. The pressure leg uses slopes 0.8 and 0.35 at scale 1, with the intercept **refit once on the union of both arms' sends to mean p 0.289** and held for both, so the arms are not priced with different p.
- **Also reported.** B1 (3 s entry) and B2 (15% guard) per section 6 item 5, and the live tier's stake, because fixed costs weigh 5 times more at 0.02 SOL than at 0.1 SOL (DEC-024 section 4, "Trial-size effect").
- **Reported at every look, never deciding:** the date-cluster bootstrap, the per-date table, and the top-3 and best-date concentration. The best-date total is checked before anyone says a variant is positive (EXP-024 section 7 item 5).

### 5.3 What the shadow must log that it does not log today

All are decision-time fields, written on the trigger or `pool` record, never joined to an outcome.

1. **`rule_id` on every record of every registered rule** (`trigger`, `outcome`, `strip`, `excluded`). The champion keeps `H5-BOOSTFLOOR-v1`. The shadow already has a `variant` field on trigger records, meaning `pv` or `fv` (V per print or fixed V). The new field is named `rule_id` so the two cannot be confused. The `fv` triggers are never traded (`TRIGGER_VARIANT = "pv"`).
2. **One trigger evaluation per rule on the shared feed.** For C-Q35, a second trigger per pool at the first qualifying print with Q at most 35 SOL, with the same fields as a v1 trigger (`q_trigger_sol`, `slot`, landing and exit slots, BOOST spent). It cannot be rebuilt from v1's trigger, because it is a later print.
3. **For C-LF, the filter's inputs and its verdict** on every v1 trigger: each feature value, `lf_id`, `lf_pass` and `lf_sha256`. The feature list is part of the Freeze record. A feature that cannot be computed in-process at the trigger print is not allowed.
4. **A skip count per rule, with limits that protect the seals:**
   - **No per-pool skip record for a CAP-PICK-sealed pool.** For those pools the shadow keeps only the **unlabelled hourly count** of skipped pools.
   - **Skip reasons that reveal the Q path** (for example, "Q never reached 35 SOL") are **withheld wherever outcomes are withheld**: pools with s0 at or after 2026-11-06T00, pools at or after 2026-10-16T00 without the Look 2 flag, and CAP-PICK-sealed pools. The Q path is outcome-bearing; the shadow already withholds `min_q` (`tools/h5_shadow.py`, header).
   - Elsewhere skips are counted per rule and per date with their reason, so the "0 where an arm does not trade" rows are auditable.
5. **The start record** lists each registered `rule_id` with its rule sha256, so section 4 step 6 is checkable from the log.
6. **No class column.** None of these records pairs the synthetic class with an outcome row (section 5.4).
7. **Only if C-BX is reopened:** a live `boost_done` event (pool, slot, block time, receive time, seconds from s0, cumulative spend at the line). It is a shadow-side field and **never substitutes for the A3 monitor** (G1 item f).

### 5.4 Seals, and the gating item G1

**G1: the companion EXP-024 amendment is a required precondition. It must merge, with quant-proof's OK on its final head, before any multi-rule shadow starts.** It is not written in this PR. This DEC lists what it must contain:

- **(a) Scope.** It covers outcomes under **any `rule_id` with a merged Freeze record**, for pools with s0 in `[2026-10-10T00, 2026-11-06T00)`. Readers are those of DEC-024 section 6 (the owner, Helm, the manager, builders, the watchdog and the daily check). The EXP-024 read tool is excluded and its inputs are unchanged.
- **(b) Reach of the read's guard sentences.** EXP-024 section 3.1's sentence "never skipped, delayed, re-scoped or re-thresholded" and section 11 cover challenger outcomes, paired statistics and switches. None of them can skip, delay, re-scope or re-threshold a look.
- **(c) Reports.** The Look 1 and Look 2 reports list the **live variant per UTC date**.
- **(d) Section 3's premise.** EXP-024 section 3 says the canary's sells "fall at s0 + 330 s or later, outside the rule's trigger window [0, 300] s". That premise fails for a live variant, whose exit may be earlier. **The conclusion still holds:** every variant trades only at or after v1's trigger print (C-LF is a subset of v1's triggers, and C-Q35 enters on the first print with Q at most 35 SOL, which is never earlier than v1's), so a variant's trade cannot create the first trigger on a pool.
- **(e) Not outcome-blind.** It is written with v1's October aggregate outcomes in view (the declared observation), and says so. It is written before any challenger outcome exists.
- **(f) `boost_done` never substitutes for the A3 monitor.** The A3 monitor's flags and its last-slice median (EXP-024 sections 10 and 11) stay the only inputs to the halts and to NOT_DECIDABLE.

The other seals:

- **The Look 2 flag.** From 2026-10-16T00 the shadow needs `--h5-look2-observed EXP-024-Am2` for challenger outcomes to appear, as for v1. For a pool with s0 at or after 2026-11-06T00, only the allowlisted timing and count fields may be read until Look 2 is read, and the shadow withholds the rest for every `rule_id`.
- **The CAP-PICK seal** (EXP-022 section 9; DEC-024 section 6). From 2026-10-16T01 to the end of EXP-022's read: no challenger trade, paper or live, on a mint the EXP-022 gate picked; no record joined to a pick; no per-pool challenger P&L for a pick before each CAP-PICK look. The pick oracle seals a pool for **every** `rule_id`, and its fail-closed rule (a feed missing or stale for more than 60 s) stands.
- **D1** (EXP-024 Amendment 4, line 691). No synthetic class is joined to any challenger outcome for a counted-window pool before Look 2 is read. The shadow writes no outcome for such pools, and the paired scorer has no class column.
- **No challenger outcome may change any EXP-024 parameter** (rule, Q\* = 40, 330 s exit, 15% guard, 1.9 s deciding cell, windows, alpha, universe, classifier, kill rules), delay a look, or re-scope it.
- **Tape disclosure.** A live variant's trades are ordinary tape rows (EXP-024 section 3). At 0.02 to 0.10 SOL against a pool of about 40 SOL that is about 0.05 to 0.25% of the pool. It is disclosed and not corrected.

## 6. The switching rule (written before any challenger outcome)

These values are quant-proof's, from its review of 975d8db (they replace the earlier PROPOSED values). A switch needs all items. They can only remove a switch.

1. **Minimums.** At least **100 paired pools**, at least **100 challenger trades**, and at least **5 distinct UTC dates** of s0, counted from the challenger's window. The paired difference is positive on a **majority of dates**.
2. **Effect.** The point estimate of the paired difference (challenger minus champion, per eligible pool, SOL, deciding cell D) is at least **+3.0 pp of stake**, under **both** the flat and the pressure fail model.
3. **Tests**, each at the corrected alpha of item 4, under both fail models. The pressure leg uses slopes 0.8 and 0.35 at scale 1, with the intercept refit once on both arms' sends to mean p 0.289.
   - A **one-sided pool-level paired bootstrap** (10,000 draws, seed 1): the lower bound is above 0.
   - **And** the EXP-024 section 7 item 6 **day-level t on the paired date means**: clusters are UTC dates, W is the number of dates with at least one paired pool, df = W - 1, t = mean(m_d) / (sd / √W), p = P(T_{W-1} ≥ t), one-sided. The larger of the flat and pressure p decides.
   - The **date-cluster bootstrap is report-only.**
4. **Multiplicity.** One-sided 0.05 in total, **Bonferroni over 2 looks** (0.025 per look), and **Holm over k within a look**. **k counts every challenger ever frozen, and k is at most 2**: a third freeze is refused and needs a new DEC. At k = 2 the rank-1 level is **0.0125**, and rank 2 is 0.025.
5. **Robustness.** The paired sum stays above 0 after removing the **top 3 paired differences** and after removing the **best UTC date**. The challenger's own book holds the same (mean per trade above 0, total above 0 ex-top-3 trades and ex-best-date) at D, B1 (3 s entry) and B2 (15% guard), under both fail models.
6. **Looks, fixed in advance.** L = 2.
   - **The first look comes after the Look 1 report is written, or after Look 1's deadline of 2026-10-17T12:00Z, whichever is first**, and not before item 1 is met. So no switch is decided while Look 1 is unread.
   - The second look is 7 days after the first. There is no other look.
   - Real-time observation of challenger outcomes is declared (section 5.4) but is not a look. Nobody switches on a reading outside a look.
   - A challenger that fails at the first look is not given more hours to rescue it. A challenger frozen after the first look joins at the second look only, on its own pairs.
7. **The owner's dated line.** The only thing that authorizes a switch. It lives at the end of this file in the form below, and Helm writes the file only after it exists. The manager first posts a MiScusi notebook decision and a Console entry with every number of items 1 to 5 (the DEC-021 §6(f) practice).

   ```
   OWNER_VARIANT_SWITCH: <date> (owner, in session, asked by <manager>). Variant: <id>. Tier in force: <T>. DEC-024 override extended to this variant: yes|no. Challenger's own gate numbers: n=<n>, dates=<d>, CI90 lower bound flat=<x> pressure=<y>, ex-top-3=<z>, cleared: yes|no.
   ```

   - The own gate numbers are those of the promotion gate for the challenger's own book (CLAUDE.md). **"cleared: no" means the switch rests on the override alone**, and the owner's line says so.
   - If "DEC-024 override extended" is "no", the switch is refused: the live trial's override was given for v1 only (section 0).
8. **Preconditions on the day.** No DEC-024 section 5 halt and no stop that ends the canary has fired and is unresolved. The A3 monitor shows none of the five flags. No seal breach. The variant is compiled in with its proofs (section 7, item 5). The champion's live-versus-twin check has not tripped. The switch happens only at **00:00Z with 0 open positions** (section 7, item 3).
9. **Dwell and number of switches.** The dwell is **7 days**. **At most one discretionary switch is made under DEC-027**, counting a re-adoption after a revert; a further switch needs a new DEC. A fail-safe return to v1 (the file missing, invalid or unapproved, a halt, or a failed first-20 check) is not a switch.
10. **quant-proof** agrees with the numbers before the owner is asked.
11. **Power, stated plainly** [quant-proof's figures from its review of 975d8db; not recomputed here].
    - At k = 2 and an effect of +3 pp, power is about **0.05 to 0.12 at 80 pairs** and **0.08 to 0.24 at 160 pairs**.
    - At the executor's roughly 8 a day, a look at the date item 6 allows would see about **40 pairs**, with power of **0.03 to 0.07**; item 1's 100 pairs would take about 12 days.
    - **80% power needs about 660 pairs, roughly 6 weeks.**
    - For scale, DEC-021's +0.003 SOL per 0.05 SOL trade is +6 pp at a per-trade SD of 0.4 times the stake.
    - **Expected result: no switch.**

## 7. The live selector (a design; the code is a later PR)

Modeled on the tier file (`tools/h5_executor.py`: `TIER_FILE_PATH`, `TIERS`, `read_tier`, `root_file_problem`, `_refresh_tier`).

1. **The file.** `/etc/mal-h5/VARIANT`, a constant `VARIANT_FILE_PATH` beside `TIER_FILE_PATH`. root:root 0644 in a root-owned directory, not a symlink, checked by the same `root_file_problem` that checks `LIVE_OK` and `TIER`. The executor cannot write it. Helm writes it.
2. **Content and default.** Exactly one token from the compiled-in table (whitespace ignored): `v1`, or a challenger's id once it is compiled in. Missing, unsafe, unreadable, invalid or unknown means **`v1`**, with the reason, as `read_tier` falls back to T0. An unsafe, invalid or unknown read alerts once per distinct problem. No file is needed to run v1.
3. **When it is read.** Before every buy decision, at the call site that refreshes the tier. The variant is then stamped (`rule_id`) on the intent, the pending buy and the position. **An open position leaves by its own variant's exit rule**, whatever the file says later. A file change never touches an open position, as a tier change does not.
4. **Compiled in, only.** A `VARIANTS` table of code constants, with its keys pinned by a test as `tools/test_h5_executor_tiers.py` pins `TIERS`. Each entry holds the `rule_id`, the sha256 of its frozen rule, its exit mode and the head it was approved at. A file cannot add a variant or change a constant. Adding one is a reviewed code change plus a pinned reinstall by Helm (manifest and sha256, as for the canary). That reinstall is the only one the design ever needs. Moving between variants already compiled in needs none.
5. **Before a variant is compiled in:**
   - its Freeze record is merged (section 4);
   - its own md5 decision-equivalence replay is recorded on 2026-09-20 or other exploration or already-read tape, never the fast-0 tip archive of 10-05 to 10-07, and v1's md5 `75cb0b0c…` is unchanged by its presence;
   - structure-only fixtures cover its decision paths;
   - a `reviewer` pass, a **security review** (the executor holds a key) and quant-proof's OK on the final head;
   - a keyless executor dry run on the real feed shows at least 5 complete simulated round trips with 0 simulate errors (the DEC-024 Amendment 1 standard). For C-BX that includes the event-driven sell path.

   **After a switch,** the first 20 landed buys of the new variant must show a landing p50 of at most 3.0 s (DEC-024 section 5 item 3). If they do not, Helm writes `v1`. That is a revert, not a switch.
6. **What a switch does not change.** The tier file and the tier. The stake, open-position cap and attempts per day. The daily and total stops **and their baselines**: the counters belong to the run and the tier, not to a variant, so a variant never starts with a fresh allowance. The wallet floor. `LIVE_OK`, `STOP` and `HALT`. The CAP-PICK exclusion and the synthetic exclusion. Every fail-closed rule.
   - **Halts stay in force across a switch.** DEC-024 section 5 applies to the active variant, with the variant's own times (for example, "late sells" is measured against the variant's exit time, and item 4's twin is the active variant's twin). A switch clears nothing.
7. **Triggers.** The shadow evaluates every registered rule on the shared feed. The executor acts only on a trigger whose `rule_id` equals the active variant's and refuses any other (`rule_id_mismatch`). How an old-shape record without `rule_id` is treated is the build PR's compatibility step, and it must fail closed.
8. **Ledger.** A `variant_change` row on every change, like `tier_change`: from, to, problem, the tier, open positions and the counters' baseline. The shadow's start record names the variant, so the Look reports can list it by date (section 5.4).
9. **Watchdog.** Alerts on `variant_file_problem` and on `variant_unapproved` (the file names a variant with no `OWNER_VARIANT_SWITCH` line). The executor cannot read a prose line, so the second alert is the manager's daily check, run with the existing H5 daily check. A change inside the dwell (section 6, item 8) also alerts. These detect. They do not stop the executor, because the fail-safe default is already `v1`.
10. **Who does what.** The owner writes the dated line. The manager posts the numbers and gets quant-proof's OK. Helm installs builds and writes `VARIANT` as root only after the line exists, and writes `v1` or removes the file on a halt the manager asks for. Builders write the code. The executor only reads.

## 8. What this is not, and why the rule is strict

- **Not gate evidence, not EXP-024, not a promotion.** The paper window lies in the same hours as EXP-024's looks and is chosen from N. It is not a fresh pre-registered read of any challenger. A challenger has no path to the promotion gate except a new pre-registration on hours it has not touched.
- **The winner's curse, plainly.** Take the best of N paper variants on the same hours and you pick the luckiest as often as the best. v1 is itself the best of at least 23 hunt families on the same dates (EXP-024 section 9, JUDGE.md:58). Its block means decayed from +26.727% to +6.781% (DEC-023 Context). **The switching rule exists to stop the lab switching to the luckiest variant.** It does that with:
  - a bound corrected for k x L;
  - a minimum effect of +1.0 pp;
  - fixed looks, with no rescue hours;
  - the challenger's own book tested on its own (items 2 and 3 of section 6);
  - a dwell, and the owner's dated line.
- **A pass is still optimistic.** DEC-021 says a reported winning margin "will usually overstate the true one" (winner's curse floor at n = 100). A switched variant should be expected to do worse live than its paper margin, and no report may quote the margin as an expected gain.
- **A live variant has no formal read.** EXP-024's Look 1 and Look 2 read the frozen v1. A PASS or FAIL speaks about v1 only. The live trial of a variant is an unpromoted trial under the owner's override, in every report.
- **No challenger result changes EXP-024.** See section 5.4.

## 9. Timeline

- **2026-10-09 (today).** This draft is a PR. The owner decides. The other critical-path items (the 19:23Z official A3 run, Helm's install) are not touched by it.
- **Within 1 to 2 days of approval (about 10-10 to 10-11).** The first paper challengers, as far as their steps allow:
  - **C-BX and C-Q35:** exploration step (section 4, step 1), the early-end share count for C-BX, Freeze 1, the N-rule shadow build with its md5 proof and review, a shadow restart (one bad hour, scheduled by the manager), then the window opens at the next full UTC hour.
  - **C-LF:** only when its rule text exists. It is not promised for this step. It may join later (section 6, item 5).
  - **C-SYN:** not before about 11-06.
- **2026-10-16T00 and T01.** The flag for outcomes in Look 2's added window, and the CAP-PICK seal. Pair counts fall by the pick rate.
- **About 10-16T07Z to 10-17T12Z.** EXP-024 Look 1 (earliest 10-16T07Z, deadline 10-17T12:00Z). It is not touched by this DEC. **A FAIL is the expected outcome** (P(Look 1 pass) of about 0.0265, EXP-024 section 15), and DEC-024 section 5 then stops new buys at the next 00:00Z unless the owner extends the canary.
- **Earliest switch decision: about 10-17 to 10-20** (the first look, section 6 item 5; the second look 7 days later). A switch then also needs:
  - the owner's extension of the canary past a Look 1 FAIL, for the live trial of a variant (open question 2);
  - the executor's `end_ms` (now 10-16T00:30Z) and DEC-024 section 4's 14-day cap extended (`docs/HANDOFF.md`, next steps, item 5);
  - the real CAP-PICK pick oracle (#509) merged and wired in;
  - the variant compiled in with its proofs (section 7, item 5).
- **2026-11-06T00.** The declared observation ends for the shadow's outcomes. Look 2's earliest run is about 11-06T03Z. C-SYN's freeze can come only after Look 2 is read, so its earliest decision is late November.

## 10. Open for the owner

1. **Approve the design,** and the one-row change to DEC-024 section 4 (the executor may trade a pre-approved variant that `VARIANT` names, after your dated line).
2. **After a Look 1 FAIL,** DEC-024 section 5 ends new buys unless you extend the canary. Do you want the canary extended so a challenger can be live? Without it, the 10-17 to 10-20 decision window has nothing to switch.
3. **Does the live-trial override extend to a challenger?** You gave it for "this one strategy", v1. This DEC makes you answer for each switch on the dated line, and a "no" refuses the switch.
4. **Tier after a switch.** The design keeps the tier as it is. Do you want a new variant to run its first 20 landed buys at T0? That would be a separate act by Helm on the tier file, not something the selector does.
5. **How many.** You asked for 3 or 4. Three can run before Look 2: C-BX, C-Q35 and C-LF, and C-LF only once its rule text exists. The fourth, C-SYN, cannot start before about 11-06.
6. **The thresholds** in section 6 are the manager's draft. Quant-proof sets them, and the numbers go to you before the first look.
7. **The proposed extension of D1** to pools with s0 in [2026-10-08T20:25Z, 2026-10-10T00) (section 3(d)).
8. **The companion EXP-024 amendment** (section 5.4). It extends the declared observation to challenger paper outcomes. It is a manager decision derived from your design, as Amendment 2 was, and you may revoke it.

## Sources

- [DEC-024](DEC-024-h5-live-canary.md) (sections 3 to 7, Amendments 1 to 3), [DEC-021](DEC-021-champion-challenger.md) (§1, §4 to §7, and the owner decision), [DEC-023](DEC-023-h5-family.md), [DEC-018](DEC-018-live-trial-readiness.md), [DEC-019](DEC-019-execution-probe.md), [DEC-020](DEC-020-size-step-proposal.md).
- [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md): sections 2, 3, 3.1, 7, 9, 11, 15, Amendments 2 to 4 (D1 at line 691).
- [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) section 9.
- `tools/h5_shadow.py` (record types, `EXIT_LADDER_S`, the trigger record's fields, `boost_last_slice_s`) and `tools/h5_executor.py` (`TIERS`, `TIER_FILE_PATH`, `read_tier`, `root_file_problem`, `_refresh_tier`, `TRIGGER_VARIANT`).
- `/data/mal/hunt-1008/h5-flows/RULE.md`, `VERIFY.md` ("NEW: the exit is a timing race"), and `/data/mal/hunt-1008/h5-lossfilter/MAP.md` (sections 0, 1, 2, 4), read as text only.
- `docs/HANDOFF.md`, STATE 10-09 ~09:30Z (volume finding, next steps).
- No sealed data, forward-1002, forward-1002ev, walk 2, forward-paper P&L, or shadow or canary outcome record was opened to write this file.
