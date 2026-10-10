# DEC-023: H5-BOOSTFLOOR is a new DEC-014 family on forward hours (EXP-024)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-08.** It takes effect on merge, with quant-proof OK on its final head, before 2026-10-10T00:00Z. The manager asks the owner for an OK on this DEC and on DEC-021 Amendment 2 by 2026-10-09T20:00Z. If the owner has not answered by then, the manager decides under the owner's 2026-10-08 mandate (`docs/HANDOFF.md`, "Owner mandate": full autonomy, the gate kept) and writes the decision to the MiScusi notebook and to this file. |
| **Decider** | Vaan (owner) for the α split and the ledger exception. The Claude manager runs the rest. |
| **Date** | 2026-10-08 |
| **Builds on** | DEC-014 (ledger, block budget, family lineage), DEC-016 (Am.2, Am.7), DEC-017 §2, DEC-021 (§8, Am.1, Am.2), [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md). |
| **Amends** | [DEC-021](DEC-021-champion-challenger.md) §8 (by its Amendment 2), and [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md) rule 1 (each block has exactly one owner), only for the second counted reader described in section 3. |
| **Does not amend** | The promotion gate and its thresholds. EXP-022's thresholds (0.005 / 0.008 / 0.012), its counted window and its seal. DEC-016 Am.2 (the FINAL stays reported compromised). DEC-021 §6, §7 and §9. The paper-only fence (DEC-024 is the only live text, and it is measurement). |

## Context

- The hunt-1 judge ranked H5-BOOSTFLOOR v1 first of the survivors. The verifier did not refute it. It is still exploration grade: its confirmation tape is in-sample for the lab, its block means decayed (+26.727% → +11.706% → +5.352% → +6.781%), and October is unmeasured (EXP-024 §16).
- The only data that can be evidence for it are fresh hours. The forward walk (forward-1002) is walked until 2026-10-16T01 and is owned by the EXP-012 forward book. Walk 2 starts at 2026-10-16T01 and is owned by EXP-022.
- DEC-021 Amendment 1 made CAP-PICK (EXP-022) the walk-2 primary-promotion arm, and left §8's second α slot unused.

## Decision

### 1. H5 is a new family, k = 1

- Under the DEC-014 clarification (2026-10-07), a family is a primary hypothesis, not a code lineage. H5's hypothesis is that a depth trigger inside the BOOST window is positive: pool Q at most 40 SOL including V, within 0–300 s of the first print, with BOOST budget left, held to 330 s.
- It uses **new information** that no earlier book used (pool depth against the V floor, and BOOST spend at the trigger print). It has no EXP-012 model, threshold, feature set or pick list. It is not a retune of CAP-PICK.
- It is **not a paired test** and claims no sealed block, so the DEC-014 paired-difference requirement does not apply to it.
- **k = 1.** One deciding cell. The binding legs are conjunctive. H4, H2 and G4 are not registered. No arm is added after merge.

### 2. The α split

- DEC-021 §8 allows at most two walks, each family at α = 0.025, Bonferroni across them, so the overall rate is ≤ 0.05. **EXP-022 holds the first slot. EXP-024 takes the second** (DEC-021 Amendment 2). With that, §8's slots are used. A third needs a new DEC.
- Inside EXP-024's slot, α = 0.025 is split over two looks: **0.020 at Look 1** and **0.005 at Look 2** (Bonferroni over looks, no Holm because k = 1).
- **Why 0.020 / 0.005.** In the author's exploration power simulation, the split keeps most of Look 1's power (0.22 at a true +6%) and still gives a backstop (0.41 for both looks at +4%). 0.015 / 0.010 gives 0.16 and 0.53; 0.025 / 0 gives 0.26 and 0.08 [measured, simulation; EXP-024 §15, `h5_power3.py`]. Look 1 serves the owner's 10-31 date.
- The powers above are exploration arithmetic and are not evidence. They are for the earlier design (a 1.3 s deciding cell). EXP-024 §15 has a rerun on the pre-registered legs, which is lower at every μ (at +4%: 0.04 at Look 1 and 0.09 for either look).

### 3. The ledger second-reader exception

- Ledger rule 1 gives each block of hours exactly one owner. The forward walk row `[2026-10-02T10, 2026-10-16T01)` belongs to the EXP-012 forward book.
- **Exception, for EXP-024 only.** EXP-024 is a second **counted reader** of forward-1002 `[2026-10-09T23, 2026-10-16T01)`. It is not an owner. The owner of the row is unchanged.
- **Conditions, all of them:**
  - EXP-024 reads these hours **only after the EXP-012 FINAL report is written** (DEC-016 Amendment 7). Before that, no H5 process opens any forward-1002 hour.
  - EXP-024 uses its own V fetch and does not use the (B) files or any file that Amendment 2 keeps closed.
  - EXP-024 has no EXP-012 component, so the FINAL's picks do not select H5's trades and H5's outcomes do not change the FINAL.
  - The read is disclosed in EXP-012 Amendment 3 (DEC-014(a)) and in the ledger row.
- **Why this is not the rejected Option X.** Option X would have made EXP-022 a second owner counting `[2026-10-10T00, 2026-10-16T01)` with CAP-PICK's picks priced by the FINAL before look 1. H5 has no selector, and its trades are not the FINAL's picks. The ordering "after the FINAL" removes the same-pool problem for the FINAL.
- **The call.** This is the Option X / O3 pattern (a second counted reader of owned forward hours). For EXP-022 the owner approved O3 conditionally on 2026-10-08 ("Yes, if the Part 1 can merge before 10-10T00Z; otherwise count from 10-16T01"), and the manager then rejected Option X for EXP-022 (EXP-022 §0). For EXP-024 the exception needs its own decision: the owner's, or the manager's under the 10-08 mandate if the owner has not answered by 2026-10-09T20:00Z. It is written to the notebook.
- **No new overlapping ledger row.** `tools/mal_catalog.py` rejects two ranged rows over one hour. The exception is recorded in the Status cells of the Forward walk and Forward walk 2 rows, in a ledger changelog line, and here.

### 4. The block budget: m ≥ 14

- EXP-024 claims no sealed block. DEC-014's p < 0.025 / m therefore does not set its bar.
- It does count as a family that read outcomes on the 27 non-P1 dates (P2 explore-0814, and the P3/P4 September confirmation dates, through the h5-flows study). So **m ≥ 14** from this merge, one above the 2026-10-08 floor of 13.
- m can only go up. The hunt-1008 directories that read P2–P4 outcomes (at least 23, JUDGE.md:58) and the audit's 16 edge-scan strategies are untracked readers. Counting them gives m of about 37 for any future block claim [inferred]. H5 is the best of at least 23 hunt families on those dates, which is why only fresh forward hours can be evidence for it.

### 5. Read order and dependencies

| Look | Earliest | Deadline | Needs |
| --- | --- | --- | --- |
| Look 1 (s0 in `[2026-10-10T00, 2026-10-16T00)`, α 0.020) | about 2026-10-16T07Z, after the EXP-012 FINAL | 2026-10-17T12:00Z | EXP-024 §8.1 (a)–(f) |
| Look 2 (cumulative to 2026-11-06T00, α 0.005) | about 2026-11-06T03Z, after EXP-022's read ends | 2026-11-14T00:00Z | EXP-022 terminal state; walk 2 running to at least 2026-11-06T02 |

### 6. What a PASS leads to

- Only the DEC-018 / DEC-019 / DEC-020 path at H5's own operating point, plus the owner's yes. No backward-block PASS is required, as in DEC-021 Amendment 1 for CAP-PICK. Every sealed block predates the 2026-10-02 program upgrade. Under the block budget (p < 0.025 / m, m ≥ 14), a 6-day sealed block would have power of about 0.15–0.19 even if H5 held (JUDGE.md:105), and reading one would spend a reserve block (EXP-024 §16 item 14).
- A paper pass is not live evidence. The [DEC-024](DEC-024-h5-live-canary.md) canary is measurement, never gate evidence, and it does not change this section.

## Owner decisions

1. **OK DEC-023 and DEC-021 Amendment 2** by 2026-10-09T20:00Z (the α split and the ledger exception). If no answer, the manager decides and records it.
2. **Accept that the read is probably a fail.** The judge's estimate is P(Look 1 pass) ≈ 0.0265 and P(either look) ≈ 0.1078 [est] (EXP-024 §15). The family exists because a cheap, pre-registered forward read is the only way to learn whether H5 survives October.

## Not decided here

- The live build and the canary (DEC-024).
- Any sealed-block claim.
- Any change to EXP-022, other than the disclosures in its Amendment 2.
