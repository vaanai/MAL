# DEC-025: C1-NF is a new DEC-014 family on forward hours, with its own alpha slot, two looks and a second counted reader (EXP-025)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-09.** It takes effect on merge, with quant-proof OK on its final head, before 2026-10-10T00:00Z. The owner's answers on O1, O2 and O3 were relayed by the manager the same day (`OWNER_DECISION_CONFIRMED` below). The alpha pair is a placeholder until quant-proof recommends the split. |
| **Decider** | Vaan (owner) for the alpha slot, the reader exception and the canary. The Claude manager runs the rest. |
| **Date** | 2026-10-09 |
| **Builds on** | DEC-014 (ledger, block budget, family lineage), DEC-016 (Am.2, Am.7, Am.8), DEC-021 (section 8, Am.1 to Am.3), [DEC-023](DEC-023-h5-family.md) (the template), [EXP-025](../EXP/EXP-025-c1nf-part1-prereg.md), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) section 9. |
| **Amends** | [DEC-021](DEC-021-champion-challenger.md) section 8 (by its Amendment 3), and [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md) rule 1 (each block has exactly one owner), only for the second counted reader described in section 3. |
| **Does not amend** | The promotion gate and its thresholds. EXP-022's thresholds, counted window and seal. EXP-024's alpha 0.020 / 0.005 and its look schedule. DEC-016 Am.2 (the FINAL stays reported compromised). It does not authorize any live order: the canary (O3) runs only under DEC-026 (to be written). |

```
OWNER_DECISION_CONFIRMED: 2026-10-09 | O1 third alpha slot of 0.025 | O2 reader exceptions (forward-1002 after the FINAL; walk 2 as a second reader with the boolean oracle) | design: TWO LOOKS | source: MiScusi notebook entry "OWNER: register C1-NF (EXP-025) with TWO looks"; the manager's session of 2026-10-09 about 02Z, AskUserQuestion answer "Two looks (Recommended)" | relayed by the manager to the drafter; the drafter did not read the notebook entry
OWNER_DECISION_CONFIRMED: 2026-10-09 | O3 small live C1-NF test trades in parallel with the formal read | owner's words as relayed: "I'm also down to do some testing with some small trades in parallel." | source: MiScusi notebook entry "OWNER: approves small live C1-NF test trades in parallel with its formal read (O3)"; the manager's session of 2026-10-09 about 02Z | relayed by the manager to the drafter; the drafter did not read the notebook entry | scope (0.02 SOL stakes, a separate wallet, DEC-026) is the manager's, not the owner's words
```

**Provenance.** The two lines above record decisions the manager relayed on 2026-10-09. The quoted words are the manager's relay. This draft does not quote the owner's own words beyond that sentence, and the drafter has not seen the notebook entries. quant-proof and the manager should check them against the entries before merge.

## Context

- JUDGE-4 (2026-10-09) ranked C1-NF third of the survivors of hunts 1 to 4 and wrote a conditional pre-registration spec. The adversarial VERIFY returned **SURVIVES-VERIFY** with three conditions (a deterministic ledger, `h_top5` and `h_top10` reported, the adverse-fill risk disclosed). All three are in EXP-025.
- It is a **lead, not evidence**: the cap was chosen after the read from about 4 splits, on top of a best-of-about-24 cell after a 144-cell scan, and about 40 hunts have read September.
- DEC-021 section 8 gave two walks alpha 0.025 each, so the promotion-eligible October families stay at 0.05 in total. EXP-022 holds the first slot, EXP-024 the second (Am.2). A third family needs a new DEC (Am.2, JUDGE-4 section 3.4). The owner approved that third slot (O1).
- The only data that can be evidence for C1-NF are fresh October hours: forward-1002 (owned by the EXP-012 forward book, walked to 2026-10-16T01) and walk 2 (owned by EXP-022 from 2026-10-16T01).

## Decision

### 1. C1-NF is a new family, k = 1

- Its hypothesis is a different primary hypothesis from EXP-012's migrate-entry selector and from CAP-PICK (EXP-025 section 1). It uses new information (post-graduation hot flows, the prior-day wallet ledger, creator history, the top-holder cap).
- It is **not a paired test** and claims no sealed block. It adds one to m (**m >= 15**, about 37 counting the unlisted readers).
- **k = 1.** One deciding cell; the binding legs are conjunctive. r4-b and G4 are not registered here. No arm is added after merge.

### 2. The third alpha slot and the two looks

- **Slot 3 alpha is 0.025**, shared by the register below. With slots 1 (EXP-022) and 2 (EXP-024) the overall promotion-eligible rate across October families is bounded by 0.075, not 0.05. The owner approved this (O1).
- **Register (JUDGE-4 section 3.4).** Members: **EXP-025 (C1-NF)**. A later post-FINAL forward reader (r4-b, G4) is added only by an amendment to this file merged before C1-NF's Look 1 read lock is written, together with its own Part 1.
- **Two looks, Bonferroni over looks.** The slot's alpha for a member is 0.025 / k, where k is the number of members in the register when the member's first read lock is written; **k = 1 at this merge**. Inside it the member's two looks carry `EXP025_ALPHA_LOOK1` and `EXP025_ALPHA_LOOK2`, which sum to 0.025 / k. **The pair 0.008 and 0.017 is a placeholder** until quant-proof recommends the split. It is one pair of pinned lines (EXP-025 section 0) and one pair of constants in `tools/exp025_power.py`; replacing it edits only those, and a test fails if they disagree or do not sum to 0.025. An added member can only tighten the pair (k = 2 halves each value); it can never loosen it.
- **Look 1** reads counted decisions `[2026-10-10T00, 2026-10-17T00)` (7 dates) about 2026-10-17T03Z. **Look 2** reads the cumulative `[2026-10-10T00, 2026-10-24T00)` (14 dates) about 2026-10-24T03Z, **only if Look 1 did not pass**. PASS at the first look that passes. No futility rule. A look not run by its deadline is NOT_DECIDABLE and its alpha is not carried forward.
- **What the looks cost.** At the placeholder pair, over both looks the probability of a pass is 0.666 if September's edge holds, 0.230 at half the edge, 0.079 at a quarter (20 trades per day), against 0.713 / 0.264 / 0.087 for one read of the 14 dates at alpha 0.025 [simulation, EXP-025 section 3]. The two looks buy an answer on about 10-17.

### 3. The reader exception

- Ledger rule 1 gives each block of hours one owner. The Forward walk row `[2026-10-02T10, 2026-10-16T01)` belongs to the EXP-012 forward book. The Forward walk 2 row `[2026-10-16T01, 2026-11-16T01)` belongs to EXP-022.
- **Exception, for EXP-025 only (O2, approved).** EXP-025 is a further **counted reader** of forward-1002, and a **non-owner reader of walk 2** while EXP-022 is counting. It is not an owner. It reads:
  - forward-1002 `[2026-10-02T15, 2026-10-16T01)`: ledger and features, and counted decisions from 2026-10-10T00;
  - walk 2 `[2026-10-16T01, 2026-10-17T02)` at Look 1, and to `2026-10-24T02` at Look 2.
- **Conditions, all of them:**
  - **After the FINAL.** No C1-NF process opens any forward-1002 hour before the EXP-012 FINAL (A) report is written (DEC-016 Am.8; the FINAL marker only, as in Am.7).
  - **Closed files stay closed** (DEC-016 Am.2): none of `OUT/rows.jsonl`, `OUT/scratch/*.jsonl`, a FINAL `report.json` or `report.md`; not the (B) V-map files.
  - **EXP-022 section 9 holds.** From 2026-10-16T01 until EXP-022's read ends, every mint whose canonical pool first prints at or after 2026-10-16T01 is looked up with the boolean `pick_oracle(mint)`. A pick is dropped from training, scoring and the book. No CAP-PICK field is written into a C1-NF record. **A missing, stale, erroring, non-boolean or undecided oracle makes the read NOT_DECIDABLE.** It reads only the boolean and only after the FINAL marker.
  - **No CAP-PICK outcome is opened.** C1-NF reads chain tape, never EXP-022's outcome rows, `LOOK_READS.jsonl` (except as the oracle contract says), or paper-twin fields.
  - **Disclosed** in EXP-012 Amendment 4 (DEC-014(a)), EXP-022 Amendment 4 (ledger rule 3) and the ledger.
- **Why this is not the rejected Option X.** Option X would have made EXP-022 a second owner of forward-1002 hours with CAP-PICK's picks priced by the FINAL. C1-NF has no selector in common with the FINAL, prices no pick, and is a reader, not an owner.
- **No new overlapping ledger row.** `tools/mal_catalog.py` rejects two ranged rows over one hour. The exception is recorded in the Status cells of the two rows, in a "Further counted reader" bullet that names EXP-025's counted window, and in the changelog.

### 4. The declared observation, the tip tape and the canary (O3, approved)

- The manager runs a paper shadow and a small live canary before and during the counted windows. Their outcomes may be watched in real time. **Look 1 and Look 2 are fixed** (rule, data, analysis, pass bar, alpha pair) **and always reported as written**; nothing the shadow or canary shows may change any EXP-025 parameter (EXP-025 section 5.1).
- **The canary** uses 0.02 SOL stakes and a separate wallet, and runs under its own **DEC-026, to be written; it does not block this PR**. No C1-NF live order is sent until DEC-026 is merged and its preconditions hold. The CLAUDE.md paper-only fence stands until then. DEC-026 owns the wallet floor, size plan, halt rules and key handling; this DEC decides none of them.
- **Scale-up beyond canary size needs a passed read or a further explicit owner override.** The canary is measurement, never gate evidence. It is separate from DEC-024's H5 canary and override.
- The tip tape (`tools/fast_tip_follower.py`, `/data/mal/tip-tape-archive`) may compute C1-NF features and run the shadow. It is a live capture, not forward-walk, forward-paper or runner P&L. The counted read uses only the pinned October adapter on forward-1002 and walk 2.

### 5. What a PASS leads to

Quant-proof on the read; a paper twin on the H5 executor core; the owner's yes with a size plan of at most 4 x 0.25 SOL open at a 1 SOL bankroll; then the DEC-018 / DEC-019 / DEC-020 path. A paper pass is not live evidence. On a FAIL C1-NF is closed: no re-threshold, no re-cap, no re-read.

## Who decides what

| Item | Status | Decider |
| --- | --- | --- |
| **O1.** A third alpha slot of 0.025 (overall rate up to 0.075 instead of 0.05) | **Approved**, relayed 2026-10-09 | Owner |
| **O2.** The reader exception, with oracle exclusion | **Approved**, relayed 2026-10-09 | Owner |
| Two looks (7 and 14 dates) | **Chosen by the owner**, relayed 2026-10-09 | Owner |
| **O3.** A small live canary in parallel | **Approved**, relayed 2026-10-09; runs only under DEC-026 | Owner; DEC-026 by the manager |
| The alpha pair (0.008 / 0.017 placeholder) | Open: quant-proof recommends | Manager |
| 1.3 s as the deciding latency, rent on every fill, binding items 5 to 7, halves by date rather than by tape, the deadlines, the Look 1 precount threshold | Design choices inside the gate; each can only tighten the bar relative to CLAUDE.md | Manager |
| The V source for forward-1002 (P6) | Not decided here. EXP-024 Look 1 needs the same one; both files should share one pinned source | Manager, with the EXP-024 scorer work |
| The adapter, the read tool, the oracle, the E0 checks, the refusals, the deterministic ledger | Outcome-blind engineering | Manager and quant-proof |
| Scale-up beyond canary size | Needs a passed read or a further explicit owner override | Owner |

## Owner decisions

1. **Answered (relayed).** O1, O2 and the two-look design on 2026-10-09; O3 on 2026-10-09. See `OWNER_DECISION_CONFIRMED`.
2. **Open for the owner later:** any scale-up beyond canary size before a passed read, and a PASS's size plan.
3. **Accept that the read is probably a fail.** The edge was found after the read on spent blocks (EXP-025 section 9). By the simulation, at the placeholder pair, either look passes about 67% of the time if September's edge holds, 23% if it is half, and 8% at a quarter.

## Not decided here

- The canary's wallet, floor, size plan, halts and keys (DEC-026).
- The V source for forward-1002.
- Any sealed-block claim.
- r4-b's or G4's registration.
- Any change to EXP-012, EXP-022 or EXP-024 other than the disclosures in their amendments.
