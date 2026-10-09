# DEC-025: C1-NF is a new DEC-014 family on forward hours, with its own alpha slot and a second counted reader (EXP-025)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-09.** It takes effect on merge, with quant-proof OK on its final head, before 2026-10-10T00:00Z. Owner items O1 and O2 below need the owner's written OK. A worker's draft cannot give that OK, and nothing in this file claims it. |
| **Decider** | Vaan (owner) for O1 (a third alpha slot) and O2 (the reader exception). The Claude manager runs everything else. O3 (a live canary) is the owner's alone and is not requested here. |
| **Date** | 2026-10-09 |
| **Builds on** | DEC-014 (ledger, block budget, family lineage), DEC-016 (Am.2, Am.7, Am.8), DEC-021 (section 8, Am.1 to Am.3), [DEC-023](DEC-023-h5-family.md) (the template), [EXP-025](../EXP/EXP-025-c1nf-part1-prereg.md), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) section 9. |
| **Amends** | [DEC-021](DEC-021-champion-challenger.md) section 8 (by its Amendment 3), and [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md) rule 1 (each block has exactly one owner), only for the second counted reader described in section 3. |
| **Does not amend** | The promotion gate and its thresholds. EXP-022's thresholds, counted window and seal. EXP-024's alpha 0.020 / 0.005 and its look schedule. DEC-016 Am.2 (the FINAL stays reported compromised). The paper-only fence. |

## Context

- JUDGE-4 (2026-10-09) ranked C1-NF third of the survivors of hunts 1 to 4 and wrote a conditional pre-registration spec. The adversarial VERIFY returned **SURVIVES-VERIFY** with three conditions (a deterministic ledger, `h_top5` and `h_top10` reported, the adverse-fill risk disclosed). All three are in EXP-025.
- It is a **lead, not evidence**: the cap was chosen after the read from about 4 splits, on top of a best-of-about-24 cell after a 144-cell scan, and about 40 hunts have read September.
- DEC-021 section 8 gave two walks alpha 0.025 each, so the promotion-eligible October families stay at 0.05 in total. EXP-022 holds the first slot, EXP-024 the second (Am.2). A third family needs a new DEC (Am.2, JUDGE-4 section 3.4).
- The only data that can be evidence for C1-NF are fresh October hours: forward-1002 (owned by the EXP-012 forward book, walked to 2026-10-16T01) and walk 2 (owned by EXP-022 from 2026-10-16T01).

## Decision

### 1. C1-NF is a new family, k = 1

- Its hypothesis is a different primary hypothesis from EXP-012's migrate-entry selector and from CAP-PICK (EXP-025 section 1). It uses new information (post-graduation hot flows, the prior-day wallet ledger, creator history, the top-holder cap).
- It is **not a paired test** and claims no sealed block. It adds one to m (**m >= 15**, about 37 counting the unlisted readers).
- **k = 1.** One deciding cell; the binding legs are conjunctive. r4-b and G4 are not registered here. No arm is added after merge.

### 2. The third alpha slot (register)

- **Slot 3 alpha is 0.025**, shared by the register below. With slots 1 (EXP-022) and 2 (EXP-024) the overall promotion-eligible rate across October families is bounded by 0.075, not 0.05. This is the change O1 asks the owner to accept.
- **Register (JUDGE-4 section 3.4).** Members: **EXP-025 (C1-NF)**. A later post-FINAL forward reader (r4-b, G4) is added only by an amendment to this file merged before C1-NF's read lock is written, together with its own Part 1.
- **The member's alpha is 0.025 / k**, where k is the number of members in the register at the instant that member's read lock is written. Bonferroni, which is no looser than Holm over the same k. **k = 1 at this merge, so alpha = 0.025.** An added member can only tighten C1-NF's bar (k = 2 gives 0.0125, k = 3 gives 0.00833); it can never loosen it. EXP-025 section 3 prints the power at all three.
- **Alternative the manager may choose before the merge.** Fix alpha at 0.0125 and hold the other 0.0125 for one co-registrant. It loses power at once (14 dates, half the edge: 0.289 to 0.192) to protect a family that does not exist. It is not recommended.
- **Within the slot:** one look, no futility rule, no second look. An extension of the counted window needs a new filing.

### 3. The reader exception

- Ledger rule 1 gives each block of hours one owner. The Forward walk row `[2026-10-02T10, 2026-10-16T01)` belongs to the EXP-012 forward book. The Forward walk 2 row `[2026-10-16T01, 2026-11-16T01)` belongs to EXP-022.
- **Exception, for EXP-025 only.** EXP-025 is a second (with EXP-024: a third) **counted reader** of forward-1002, and a **non-owner reader of walk 2** while EXP-022 is counting. It is not an owner. It reads:
  - forward-1002 `[2026-10-02T15, 2026-10-16T01)`: ledger and features, and counted decisions from 2026-10-10T00;
  - walk 2 `[2026-10-16T01, 2026-10-24T02)`: counted decisions and exits.
- **Conditions, all of them:**
  - **After the FINAL.** No C1-NF process opens any forward-1002 hour before the EXP-012 FINAL (A) report is written (DEC-016 Am.8; the FINAL marker only, as in Am.7).
  - **Closed files stay closed** (DEC-016 Am.2): none of `OUT/rows.jsonl`, `OUT/scratch/*.jsonl`, a FINAL `report.json` or `report.md`; not the (B) V-map files.
  - **EXP-022 section 9 holds.** From 2026-10-16T01 until EXP-022's read ends, every mint whose canonical pool first prints at or after 2026-10-16T01 is looked up with the boolean `pick_oracle(mint)`. A pick is dropped from training, scoring and the book. No CAP-PICK field is written into a C1-NF record. **A missing, stale, erroring, non-boolean or undecided oracle makes the read NOT_DECIDABLE.** It reads only the boolean and only after the FINAL marker.
  - **No CAP-PICK outcome is opened.** C1-NF reads chain tape, never EXP-022's outcome rows, `LOOK_READS.jsonl` (except as the oracle contract says), or paper-twin fields.
  - **Disclosed** in EXP-012 Amendment 4 (DEC-014(a)), EXP-022 Amendment 4 (ledger rule 3) and the ledger.
- **Why this is not the rejected Option X.** Option X would have made EXP-022 a second owner of forward-1002 hours with CAP-PICK's picks priced by the FINAL. C1-NF has no selector in common with the FINAL, prices no pick, and is a reader, not an owner.
- **No new overlapping ledger row.** `tools/mal_catalog.py` rejects two ranged rows over one hour. The exception is recorded in the Status cells of the two rows, in a "Second counted reader" bullet that names EXP-025's counted window, and in the changelog.

### 4. The declared observation and the tip tape

- The manager intends a paper shadow, and perhaps later a small live canary, inside the counted window. Their outcomes may be watched in real time. The read's rule, data, analysis and pass bar are fixed; the read is always run and reported as written; nothing the shadow or canary shows may change any EXP-025 parameter (EXP-025 section 5.1).
- The tip tape (`tools/fast_tip_follower.py`, `/data/mal/tip-tape-archive`) may compute C1-NF features and run the shadow. It is a live capture, not forward-walk, forward-paper or runner P&L. The counted read uses only the pinned October adapter on forward-1002 and walk 2.
- **A live canary before the read needs the owner's written override (O3).** It is separate from DEC-024's H5 override (`OWNER_OVERRIDE_CONFIRMED`) and from the 10-08 mandate, and neither covers C1-NF.

### 5. What a PASS leads to

Quant-proof on the read; a paper twin on the H5 executor core; the owner's yes with a size plan of at most 4 x 0.25 SOL open at a 1 SOL bankroll; then the DEC-018 / DEC-019 / DEC-020 path. A paper pass is not live evidence. On a FAIL C1-NF is closed: no re-threshold, no re-cap, no re-read.

## Who decides what

| Item | Needs | Why |
| --- | --- | --- |
| **O1.** A third alpha slot of 0.025 (overall rate up to 0.075 instead of 0.05) | **The owner's written OK** | DEC-021 section 8 is the owner-approved "two walks at alpha 0.025". This changes the cumulative error budget. |
| **O2.** The reader exception: C1-NF as a counted reader of forward-1002 after the FINAL, and a non-owner reader of walk 2 while EXP-022 counts, with oracle exclusion | **The owner's written OK** | Ledger rule 1 / 3 and the Option X pattern; DEC-023 section 3 treated the same pattern as an owner decision. |
| **O3.** A live canary before the read | **The owner's written override**, not requested here | The paper-only fence; DEC-024's override is H5's alone. |
| The window length (14 dates), 1.3 s as the deciding latency, rent charged on every fill, binding items 5 to 7 of EXP-025 section 7, by-half instead of by-tape | The manager | Design choices inside the gate. Each can only tighten the bar relative to CLAUDE.md, and each is flagged in EXP-025. Restoring JUDGE-4's 1.9 s as gating or its by-tape halves lowers power. |
| The alpha rule (0.025 / k), the register, Bonferroni vs Holm | The manager, inside the owner's O1 budget | Section 2. |
| The paper shadow, the tip-tape use for features | The manager | Paper and keyless. |
| The adapter, the read tool, the oracle, the E0 checks, the refusals, the deterministic ledger | The manager and quant-proof | Outcome-blind engineering. |

**On the fallback used for DEC-023.** DEC-023 let the manager decide under the owner's 2026-10-08 mandate ("full autonomy, the gate kept") if the owner had not answered by a deadline. Whether that mandate covers O1 or O2 is the manager's call, recorded in the notebook; this file does not claim it does. It changes a cumulative error budget the owner set, so the safer reading is to wait for the owner's written OK.

## Owner decisions

1. **O1 and O2 (the ask).** "Vaan: may the lab register C1-NF (EXP-025) as a third alpha slot (0.025; the overall error budget for October families goes from 0.05 to up to 0.075) and let it read forward-1002 after the FINAL and walk 2 as a second reader, with CAP-PICK's picks excluded by a boolean oracle? It is one 14-day read, 2026-10-10 to 10-24, with the gate unchanged and the day-level test added. By my power estimate it passes about 70% of the time if September's edge holds, about 29% if the edge is half, and it can only start counting on 10-10 if you answer before the PR merges. A pass leads only to a paper twin and to your yes; it does not start live trading."
2. **O3 is not asked.** If the manager wants a live C1-NF canary before the read, that is a separate ask with its own wallet floor and size plan.
3. **Accept that the read is probably a fail.** The edge was found after the read on spent blocks (EXP-025 section 9).

## Not decided here

- The live build and any canary.
- Any sealed-block claim.
- r4-b's or G4's registration.
- Any change to EXP-012, EXP-022 or EXP-024 other than the disclosures in their amendments.
