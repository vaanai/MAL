# DEC-027: H5 champion-challenger plan (paper variants beside the live v1, one switch file)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-09.** Paper only until a switch is approved. The champion is H5-BOOSTFLOOR v1, live under [DEC-024](DEC-024-h5-live-canary.md). This file freezes no challenger, sets no threshold that binds, and starts no trade. It takes effect on merge, with the owner's OK and quant-proof OK on its final head. Every number marked **[PROPOSED]** is the manager's draft for quant-proof to set. |
| **Decider** | Vaan (owner) for every live switch (a dated line, section 6). The Claude manager runs the rest. |
| **Date** | 2026-10-09 |
| **Builds on** | DEC-024 (sections 4, 5, 6, 7, Amendments 1 to 3), [DEC-021](DEC-021-champion-challenger.md) (the lab's champion-challenger precedent: Sections 4 to 6, and the owner decision "a challenger swap never changes size or wallet"), [DEC-023](DEC-023-h5-family.md), [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) (sections 3, 3.1, 11, Amendments 2 to 4), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) section 9, DEC-018, DEC-019, [DEC-020](DEC-020-size-step-proposal.md) (sizing). |
| **Amends** | **On approval, DEC-024 section 4 "Rule" row only** ("Frozen H5-BOOSTFLOOR v1 …"), so that the executor may trade a pre-approved variant that the root-owned `VARIANT` file names (section 7), after the owner's dated line for it. Every other DEC-024 limit, halt, seal and scale-up condition stands. |
| **Does not amend** | The promotion gate, for any book. EXP-024's rule, parameters, looks, alpha, seal and read tool. EXP-022 and its CAP-PICK seal. DEC-021 sections 6 to 9. The tier ladder and the T0, T1, T2 limits (DEC-024 Amendment 3). DEC-024 sections 5 to 7. |

## 0. The request and the design (2026-10-09)

- **The owner's request.** Run 3 or 4 good H5 variants in paper beside the live v1, so the live bot can move to a better one once the 0.02 SOL speed trial is done.
- **The manager's design, which the owner approved.** One shared detector feed. A paired comparison on the same triggers. A switching rule written in advance. A root-owned variant file, like `/etc/mal-h5/TIER`, so Helm can move between pre-approved variants without a reinstall. Each live switch still needs the owner's dated line.
- **Provenance.** Relayed by the manager on 2026-10-09. The author did not open the owner's notebook entry and does not quote the owner's words here.
- **What the live trial's override covers.** The owner's override ([DEC-024](DEC-024-h5-live-canary.md) section 7, `OWNER_OVERRIDE_CONFIRMED`) was given for "this one strategy", H5 v1. EXP-024 section 11 (line 333) says an exit tied to the observed BOOST end, a different Q\*, or a guard other than 15% "would each be a **new** rule". So the override does not extend to a challenger by itself. Each switch line must say whether the owner extends it (section 6, item 6).

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

A candidate becomes a challenger only through a **freeze record** (section 4). The four below are the candidates this DEC lists. No fifth is added without an amendment, and a frozen challenger counts in k whether or not it is later dropped (section 6).

Each is a **different idea, not a threshold tweak of v1**. EXP-024 section 11 already calls three of them new rules needing their own discovery on exploration data. That discovery is a pre-freeze duty (section 4).

| ID | Idea | Hypothesis | Status |
| --- | --- | --- | --- |
| **C-LF** (a) | v2 loss filter: skip the entries a decision-time filter flags | Some of v1's large losers are visible at the trigger print | Candidate. **Rule text TBD.** |
| **C-BX** (b) | Exit tied to the observed BOOST end | v1's fixed 330 s timer sells after the cliff on pools where BOOST ends early | Candidate. Rule text below. |
| **C-Q35** (c) | A different drain threshold, Q\* = 35 SOL | A deeper drain leaves less downside per entry | Candidate. Rule text below. |
| **C-SYN** (d) | H5 on synthetic-migration pools | v1's rule also works on the pools EXP-024 Amendment 4 excluded | **Blocked until Look 2 is read** (about 2026-11-06). |

### 3(a). C-LF: the v2 loss filter

- **Idea.** A filter on decision-time fields, applied to v1's trigger. A pool the filter flags gets no trade. The entry, exit, Q\*, guard and stake are v1's.
- **Why it is a different idea.** It adds new information (a pool or flow feature at the trigger print) that v1 never used. v1 has no loss control: it holds to 330 s whatever happens (`/data/mal/hunt-1008/h5-lossfilter/MAP.md` section 4, [measured]).
- **Exploration facts, copied from MAP.md section 4** (frozen trade files, no filter built, counts only):
  - on the 1,124 confirmation trades, 212 (18.861%) lost 30% or more of the stake (net, binding cell); their flat sum is -8.3027 SOL against a book total of +13.9990 SOL;
  - if every one were skipped at zero cost, flat would be +24.454% against +12.455%. This is a **hindsight ceiling and not a filter**. No decision-time filter reaches it, and a real filter also removes winners.
- **Rule text: TBD.** It comes from the running research at `/data/mal/hunt-1008/h5-lossfilter/` (MAP.md and the decision-time feature table). As of this DEC the study has built no filter and joined no feature to an outcome (MAP.md section 0).
- **Freeze preconditions specific to C-LF:**
  - the filter is derived on the discovery block (explore-0814) and checked on the confirmation set once, as MAP.md section 1 splits them. The confirmation set is in-sample for the lab (EXP-024 section 9), so the check **is not evidence**; only the paper window is;
  - every configuration tried is logged in `data/tries.jsonl` (DEC-021 Section 1), and the count is stated in the freeze record. The search size bears on how far the exploration estimate shrinks (the winner's curse). It does not change the paper test's alpha, because the paper window is a fresh draw;
  - **no October shadow or canary outcome may enter the derivation.** The declared observation (EXP-024 section 3.1) lets the team watch v1's aggregate October outcomes. A filter tuned after looking at them is not frozen blind, and the freeze record says what was in view;
  - every feature must exist at or before the trigger print, and the shadow must log it (section 5.3);
  - the synthetic class is **not** a feature (section 3(d), EXP-024 Amendment 4 item D1).
- **Paired shape.** The challenger's trades are a subset of v1's. The paired difference is the negative of the SOL on the pools the filter skipped, so it measures only what the filter removes.

### 3(b). C-BX: exit tied to the observed BOOST end

- **Candidate rule text (not frozen).** Same universe, trigger, entry and guard as v1. Sell when the BOOST vault's **last slice is detected**, or at s0 + round(330 s / sps) (v1's timer), **whichever comes first**.
  - "Last slice detected" means the first print or vault update at which BOOST's cumulative spend reaches 0.999 x 17.585 SOL. This is the rule's own budget test, run on the same tracker, so it uses no future information.
  - If BOOST stops short of that line, the 330 s timer sells. An early stop is not a "last slice".
- **Why it is a different idea.** v1 sells on a clock. C-BX sells on an observed event, which is the "exit tied to the observed BOOST end" that EXP-024 section 11 and VERIFY.md (line 110) call a new rule.
- **Rationale (exploration, [measured] in `h5-flows/VERIFY.md`, "NEW: the exit is a timing race").**
  - The edge is concentrated before the cliff. Pooled confirmation, flat, 0.1 SOL, exit at s0 + 330 s: +13.208%; at 335 s: +12.122%; 340 s: +8.827%; 345 s: +5.291% (CI90 lower bound -0.439); 350 s: +1.341%.
  - BOOST's last slice moves earlier. p50 347.4 s in fresh-0903 and 342.9 s in oracle-0922; p10 334.2 s and 331.7 s. On the trades where BOOST had already ended at our exit (3.5% and 6.5%), the mean was +2.91%, against +13.588% for the rest.
  - The manager reports that BOOST now ends about 335 to 340 s after the first print, against 342 to 347 s in September. That reading is the A3 monitor's last-slice median and is not re-measured here.
- **What it can and cannot do, said plainly.**
  - With the 330 s cap, C-BX differs from v1 **only on pools whose BOOST finishes before about 330 s**. On every other pool it is v1, to the slot. So it is a tail-insurance variant, its pairing with v1 is very tight, and its expected gain is small and concentrated in few pools [inferred].
  - The share of triggered pools whose last slice precedes s0 + 330 s is a **structure count** (the `pool` record's `boost_last_slice_s`). The manager reads that share, with no outcome joined, before freezing, and records it. If it is too small for 80 paired pools to hold enough early-ending cases to show anything, C-BX is dropped before it takes a slot, because it could not be decided inside the horizon.
  - Detection comes after the final slice lands, and the sell lands after that (the shadow's exit lag is 0.55 s, `tools/h5_shadow.py`), so on an early-ending pool the sell still lands after the last slice. The variant limits how far past the end the sell goes. It does not sell before the end.
  - DEC-024 section 5 item 1 halts new buys when a post-step A3 run shows a last slice below 335 s. In a regime where that fires, v1 is already halted, and DEC-024 section 5 bars answering a halt with a retune. C-BX is not a way around that halt.
- **Not listed:** selling on a slice count (for example, after the 27th slice). It is the same idea on a different cut. Adding it needs an amendment and counts in k.
- **Live cost.** The sell becomes event-driven instead of timer-driven. That is a new send path, so it needs a keyless dry run and a first-20-fills check (section 7, item 5), as DEC-021 Section 7 requires for any changed send setting.

### 3(c). C-Q35: a different drain threshold

- **Candidate rule text (not frozen).** v1 with one constant changed: the trigger needs post-trade Q at most **35 SOL** incl. V (v1: 40). Universe, window (0 to 300 s), BOOST test, entry, exit at 330 s, guard and costs are v1's. One trade per pool, on the first print that meets the 35 SOL test.
- **The value is picked now, once, and no other value is tried.** The reason: the rule's discovery grid was Q\* in {40, 50, 60} and was monotone, "smaller Q\* better" (`h5-flows/RULE.md`, "Parameters chosen on discovery"). 40 SOL is the grid's low edge, so the only direction with any evidence is below 40. 45 SOL lies between 40 and 50 and is against the trend. 35 is the smallest step that keeps the trigger count large; it is half the grid's spacing.
- **Mechanism [inferred].** With constant product and Q at least V, the loss floor on an entry is near 1 - (Q_exit / Q_entry)^2 (MAP.md section 4). At Q_entry 40 SOL and Q_exit near the V floor of 17.6 SOL that is about -81%. At 35 SOL it is about -75%. The entry also waits for a deeper drain, so it takes fewer trades.
- **Choice made before any October challenger outcome is seen.** This section is written without opening any October shadow, canary or tape outcome. The value is fixed by the discovery text above, not by any result. If the exploration run (section 4, step 2) shows Q\* = 35 is not better than 40, C-Q35 is **withdrawn, not replaced**: no 30 and no 45.
- **Why it is a different idea.** A Q\* change is a new rule needing its own discovery (EXP-024 section 11). It also changes which print triggers, hence the entry state and the landing price, so its pairing with v1 is partial (ρ well below C-BX's).
- **Cost.** Fewer trades, so fewer paired pools in the window. The pair count is a structure count on exploration tape (trigger counts only) before freeze.

### 3(d). C-SYN: H5 on synthetic-migration pools (blocked)

- **Blocked until Look 2 is read, about 2026-11-06** (Look 2's earliest run is about 11-06T03Z; its deadline is 2026-11-14T00:00Z, EXP-024 section 8.1).
- **Why.** [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendment 4, item D1 (line 691): joining synthetic class to any H5 outcome (a fill, exit, P&L, mean, CI, day sign, win or loss, or any field derived from one), from any source, for any pool with s0 in a counted window, before Look 2 is read, is a breach of section 3. The read is then reported compromised, and a compromised read cannot support a live request.
  - C-SYN's whole evidence is H5 outcomes on synthetic pools, so its paired comparison **is** that join.
  - The shadow stopped signalling synthetic and unclassifiable pools (DEC-024 Amendment 2 item 1), and the executor refuses them. There are no such outcome records to compare.
  - The full-universe book "never decides" and is not computed or reported before Look 2 (EXP-024 Amendment 4 item A3).
- **Proposed extension [PROPOSED, manager to decide].** Treat pools with s0 in [2026-10-08T20:25Z, 2026-10-10T00) the same way. D1's letter covers counted-window pools only. But the same people would read the join before Look 2 and would carry it into the same decisions, and Amendment 4 item 4's attestation says no outcome has been split by class.
- **No design work is done on C-SYN in this DEC beyond this section.** After Look 2 is read it needs its own freeze, exploration and a fresh paper window, so its earliest decision is late November, after the October mandate window.

## 4. Freezing a challenger (before its paper window starts)

A candidate has no standing until every step below is done and recorded in a dated **Freeze N** amendment at the end of this file.

1. **Exploration discovery, on exploration data only.**
   - Allowed ranges are the exploration pool of MAP.md section 2: explore-0814 for discovery, and the 09-03 to 09-25 blocks for one confirmation look. Nothing else.
   - Never read: a sealed block, forward-1002, forward-1002ev, walk 2, forward-paper, the fast-0 tip archive of 10-05 to 10-07, and any shadow or canary outcome.
   - One run per candidate. For C-Q35 that is the frozen `s14_boostdip.py` (sha256 `70becfb7d7e48b8e24cee9ae807db29200db9990cf96d18ab2dce755301c1ad0`) with `DS=35` and every other input v1's. The result is reported whatever it shows.
   - The run can **withdraw** a candidate. It cannot change the candidate's text and cannot add a second value.
   - It is not evidence of edge. The confirmation blocks are in-sample for the lab (EXP-024 section 9: m at least 14, about 37 counting untracked readers). Only the paper window counts for section 6.
2. **The rule text, exact.** A fenced `rule` block in the Freeze amendment, with its sha256 and the one-line check command (as EXP-024 section 2 does). For C-LF the filter's constants and feature definitions are in it. No constant is left to code.
3. **A decision-equivalence proof, by md5, on 2026-09-20.** This is the day [DEC-024](DEC-024-h5-live-canary.md) Amendment 1 item 3 used.
   - The champion's trigger list must stay equal to `75cb0b0c585bc2479137cae31330e73e` (72 decisions), so adding a rule to the shadow cannot move v1.
   - Each challenger's list from the live engine must equal its frozen rule's list from the offline scorer.
   - The day is exploration tape. The proof is not evidence of edge.
4. **Keyless run.** The shadow carries the rule on live triggers with 0 build errors, and v1's records are unchanged by it.
5. **Reviews.** A `reviewer` pass on the shadow change and quant-proof's OK on the Freeze record, including the power statement (section 6, item 10).
6. **The window opens at the first full UTC hour** after both of these hold: the Freeze amendment is merged, and the shadow's start record shows the rule id with its sha256. No earlier hour is ever scored for that challenger. Replaying a challenger over hours that began before its window, even to "see how it would have done", is a breach of this DEC and makes that challenger ineligible.
7. **After the freeze, no edit.** A change of any constant is a new candidate with a new id. The old one is withdrawn and still counts in k.

## 5. Paper evaluation

### 5.1 Data and window

- **Source.** The shadow's own records, written live after the challenger's window opens. One detector feed, one process, N registered rules, each record tagged `rule_id` (section 5.3). The paired scorer is a later PR. It reads those records only. It never reads forward-1002, walk 2, the tip tape, or the EXP-024 read tool's output.
- **Hours.** Pools whose s0 is at or after the window's opening instant and before **2026-11-06T00** (the end of the declared observation; the shadow withholds outcomes after it, DEC-024 section 6). A decision that needs later pools waits for Look 2.
- **Excluded the same way for both arms.** Pools flagged `gap`, and bad hours (a gap record or a missing heartbeat). They are counted and reported.
- **Pools it never sees.** Synthetic and unclassified pools (DEC-024 Amendment 2) and, from 2026-10-16T01, CAP-PICK picks (section 5.4). The pick rate is unknown, so the pair count after 10-16T01 falls by an unmeasured amount.
- **Rate [measured, structure counts].** About 16 non-synthetic triggers a day (`docs/HANDOFF.md`, STATE 10-09 ~09:30Z, "Volume finding"; MiScusi n_KqwGD1bzt_lbpg). At that rate 80 paired pools take about 5 days and 100 take about 6. The rate may not be used as a basis for any EXP-024 change (EXP-024 Amendment 3, line 631), and it is not used as one here.

### 5.2 Pairing and pricing

- **Unit.** One row per eligible pool in which at least one of the two arms trades. An arm that does not trade on the pool is 0 (DEC-021 Section 4). The paired value is challenger minus champion, in SOL.
  - C-LF and C-BX share v1's entry, so their pairs are tight (ρ high).
  - C-Q35 enters on a different print, so its pairs are looser (ρ lower), and some pools are traded by only one arm.
- **Price.** The EXP-024 deciding cell D: stake 0.1 SOL, 1.9 s entry, END bound, own impact, tier fee, exit lag 0.55 s, 55,000 lamports per send, and the section 5 correction (the larger of the DEC-021 Section 1 haircut and the E1 term; the haircut alone, stated, if E1 is not yet recorded). Both fail legs. The pressure leg's intercept is refit once on the union of both arms' sends and held for both [PROPOSED], so the arms are not priced with different p.
- **Also reported.** B1 (3 s entry) and B2 (15% guard) per section 6 item 3, and the live tier's stake, because fixed costs weigh 5 times more at 0.02 SOL than at 0.1 SOL (DEC-024 section 4, "Trial-size effect").
- **Reported at every look, never deciding:** the trade-level bootstrap p, the per-date table, and the top-3 and best-date concentration. The best-date total is checked before anyone says a variant is positive (EXP-024 section 7 item 5).

### 5.3 What the shadow must log that it does not log today

All are decision-time fields, written on the trigger or `pool` record, never joined to an outcome.

1. **`rule_id` on every record of every registered rule** (`trigger`, `outcome`, `strip`, `excluded`). The champion keeps `H5-BOOSTFLOOR-v1`. The shadow already has a `variant` field on trigger records, meaning `pv` or `fv` (V per print or fixed V). The new field is named `rule_id` so the two cannot be confused. The `fv` triggers are never traded (`TRIGGER_VARIANT = "pv"`).
2. **One trigger evaluation per rule on the shared feed.** For C-Q35, a second trigger per pool at the first qualifying print with Q at most 35 SOL, with the same fields as a v1 trigger (`q_trigger_sol`, `slot`, landing and exit slots, BOOST spent). It cannot be rebuilt from v1's trigger, because it is a later print.
3. **For C-BX, a live `boost_done` event:** the pool, the slot, the block time, the receive time, the seconds from s0, and the cumulative spend at the line (0.999 x 17.585 SOL). Today the last-slice time is known only at pool close (`pool` record `boost_last_slice_s`, and the executor's `h5_boost_v1` rows, used for halts). A sell cannot wait for the pool to close. The trigger record also carries the rule's exit trigger slot, the earlier of the done slot and the 330 s slot, and which of the two it was.
4. **For C-LF, the filter's inputs and its verdict** on every v1 trigger: each feature value, `lf_id`, `lf_pass` and `lf_sha256`. The feature list is part of the Freeze record. A feature that cannot be computed at the trigger print is not allowed.
5. **A skip record per rule** for every pool the rule saw and did not trade, with the reason. Skips are counted per rule and per date. This is what makes the "0 where an arm does not trade" rows auditable.
6. **The start record** lists each registered `rule_id` with its rule sha256, so section 4 step 6 is checkable from the log.
7. **No class column.** None of these records pairs the synthetic class with an outcome row (section 5.4).

### 5.4 Seals and what they bar

- **EXP-024's seal and declared observation** (section 3, line 132; section 3.1; Amendment 2).
  - The declared observation covers "the live canary's and the shadow detector's outcomes" for pools with s0 in `[2026-10-10T00, 2026-11-06T00)`. A challenger rule's outcomes are the shadow's outcomes under a rule that the text never named. **This DEC does not rely on that wording.** It needs a companion EXP-024 amendment, merged with quant-proof's OK before any challenger outcome exists, that extends the declaration to the shadow's outcomes under any registered `rule_id`, for the same window and the same readers.
  - That amendment would be written with v1's aggregate October outcomes in view, so it cannot call itself outcome-blind. It is written before any challenger outcome exists, and says so.
  - Readers are those of DEC-024 section 6: the owner, Helm, the manager, builders, the watchdog and the daily check. The EXP-024 read tool is excluded, and its inputs do not change.
  - For a pool with s0 at or after 2026-11-06T00, only the allowlisted timing and count fields may be read until Look 2 is read. The shadow withholds the rest, for every `rule_id`.
  - From 2026-10-16T00 the shadow needs `--h5-look2-observed EXP-024-Am2` for challenger outcomes to appear, as for v1.
- **The CAP-PICK seal** (EXP-022 section 9; DEC-024 section 6). From 2026-10-16T01 to the end of EXP-022's read: no challenger trade, paper or live, on a mint the EXP-022 gate picked; no record joined to a pick; no per-pool challenger P&L for a pick before each CAP-PICK look. The pick oracle seals a pool for **every** `rule_id`, and its fail-closed rule (a feed missing or stale for more than 60 s) stands.
- **D1** (EXP-024 Amendment 4, line 691). No synthetic class is joined to any challenger outcome for a counted-window pool before Look 2 is read. The shadow writes no outcome for such pools, and the paired scorer has no class column.
- **No challenger outcome may change any EXP-024 parameter** (rule, Q\* = 40, 330 s exit, 15% guard, 1.9 s deciding cell, windows, alpha, universe, classifier, kill rules), delay a look, or re-scope it.
- **Disclosure the Look reports must carry.** The Look 1 and Look 2 reports add which variant was live on each UTC date (from the `variant_change` rows, section 7), and that challenger outcomes were observed in real time and could have influenced the choice of live variant. A live variant's trades are ordinary tape rows (EXP-024 section 3). A C-BX sell can land before v1's exit state on the same pool, and at 0.02 to 0.10 SOL against a pool of about 40 SOL that is about 0.05 to 0.25% of the pool. It is disclosed and not corrected.
