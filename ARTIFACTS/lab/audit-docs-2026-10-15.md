# Audit 2026-10-15 (manager11): docs, memory and library, read against the November goal

Written 2026-10-15, after the research-0 outage. The inputs were a read-only inventory of every repo doc, the memory directory, the strategy library and the ops directory (sizes, dates, statuses, cross-checks), plus the manager's own reading. Nothing here is an edge claim. No book has cleared the promotion gate.

## 1. The verdict in five lines

1. **The docs are bloated, and the bloat costs real money in usage.** A new session that follows the CLAUDE.md reload order loads about 68k tokens before any DEC file, and about 208k with the 13 DEC files in live use. Most of that is dated history, not current rules.
2. **The docs that every session reads first are the stale ones.** CLAUDE.md still names EXP-011 as the candidate (closed NOT_DECIDABLE on 09-30). Its "This week's clocks" table ends at 10-05. LAB_STATE opens with a 10-01 "gate PASS" that later lines correct.
3. **The process is honest but fragile.** Many pre-registered reads hang on fixed instants, and on 10-15 one lost session voided CAP-PICK (EXP-022). Rules that only a live session can keep are a single point of failure.
4. **The biggest gap is not in the docs.** No document turns the goal (about $200/day by the end of November) into a target that research can screen against: trades per day × stake × net edge, plus the capital and liquidity that would need. Without one, we keep finding lines that are honest and small.
5. **The library is thorough but flat.** It has 248 rows: 96 dead, 54 untested, and the nearest-to-live shortlist is not linked from the index. It needs a "can this reach the goal?" column, more than it needs more rows.

## 2. What we need (keep)

- **The promotion gate, the holdout ledger, pre-registration, and quant-proof on any money claim.** These are why nothing false has gone live. Keep them unchanged.
- **CONSTITUTION.md** (3 KB), **HOSTS.md**, the **DEC files as law**, and the **EXP files as the record of what was tested.** These are the record. They just should not all be *reloaded* every session.
- **The idea library** (`docs/library/`): one row per idea, the same day it comes up. Keep the practice.
- **Memory feedback notes** (pinned exit gates, top-day concentration, the reserve convention, jobs under sh, owner-visible channels, and others). They are short and they encode costly lessons.

## 3. What we do not need (cut or archive)

| Item | Size | Action |
|---|---|---|
| LAB_STATE "2026-10-12 events" | 148 KB, 73% of LAB_STATE | Move to `ARTIFACTS/lab/lab-state-archive-2026-10.md`. Keep a pointer and a 10-line current summary. |
| LAB_STATE "Latest confirmation result" (10-01 PASS), "Kill review 10-05", "stale-fill void" | ~9 KB | Mark them superseded and move them to the archive. |
| CLAUDE.md "Current candidate", "This week's clocks", the three-backward-walkers paragraph | ~3 KB, wrong | Replace with a 3-line pointer to HANDOFF / LAB_STATE "now". The owner notes and rules stay word for word. |
| HANDOFF.md | 49 KB | The next handoff is at most ~8 KB: state, in-flight work with ids, next 72 h, where the rules are. History goes to the archive. |
| DEC reload | 644 KB of DEC text | Change the reload order to name the DECs that are actually live (HANDOFF lists them) and say "read the section you need", not "read the active DEC files". |
| Memory `state-2026-10-04.md` | 290 KB | **Done tonight.** Renamed to an archive. The new pointer `state-2026-10-15.md` is 1.5 KB. `state-2026-10-02` and `execution-probe` were deleted as obsolete. Four stale descriptions were fixed. |
| Ops dir: expired one-shots and superseded run-rule files; two superseded library copies (~168 KB) | ~250 KB | **Leave in place.** Records and PR comments cite these paths, and nobody reloads them, so moving them would break citations and save nothing. |

## 4. Records that disagree with each other (fix in one records PR)

- EXP-011's own status cell says "planned", while LAB_STATE says closed NOT_DECIDABLE on 09-30.
- Several DEC status cells disagree with other docs. DEC-016 says "Proposed" (its FINAL runs tonight). DEC-024 says "Proposed" while it ran a live canary. DEC-026 says "DRAFT" though it is merged. The LAB_STATE decisions index stops at DEC-021, and DEC-022 to DEC-029 are missing from it.
- EXP/README lists statuses (planned, running, done) that the files do not use. Use one status line format in every EXP file (`Status: <word> (<date>)`).

## 5. What we are missing

1. **A goal-to-research target spec (the most important item).**
   - Daily SOL = trades/day × stake × net edge after fees and fails.
   - $200/day is about 1–1.5 SOL/day (SOL price assumed $130–$200), depending on the SOL price.
   - Examples that reach it (illustrative arithmetic, not a measured edge): 30 trades/day × 1 SOL × 4% net, or 100 trades/day × 0.5 SOL × 2.5% net.
   - Today's capital is 1.29 SOL, so either the edge must hold at much larger stakes than we have tested (pump.fun pool depth and slippage set that limit), or capital must grow. Growth comes from the owner adding funds after a gate pass, or from compounding, which at realistic edges takes weeks.
   - Every library row and every hunt should be screened on three numbers before work starts: trades/day, edge at the target size under the pressure fail model, and the capital needed. A line that cannot reach about 0.3 SOL/day even if everything goes right is a learning line, not a profit line.
2. **Capacity and slippage at size.** Most results are at 0.02–0.1 SOL. We have not measured how edge decays at 0.5–2 SOL on the lines that trade often (G4-FLOORDIP ~31/day, C1-NF ~20/day in September).
3. **Ops resilience.** Back every fixed-instant step with a host-side job: scheduled, or chained with `after`, as done tonight for the FINAL. At every session start, check CronList against the pointer files. A missed step should page the owner or Helm, not wait for a manager.
4. **A fast forward-validation lane.** Most pre-registered reads land on 11-15 or 11-22, too late for November. Walk 2 is now unowned, because CAP-PICK was withdrawn. Proposal: the owner returns it to "reserved", and it becomes the forward confirmation block for the push's best candidate, with a short counted window (7 days) and its pre-registration merged before its start hour.
5. **One place for "what's live, what's next".** Today the answer is spread across HANDOFF, LAB_STATE, the Console, the notebook and memory. The Console is the owner's view. LAB_STATE should be the agent's one-page "now", and everything else is history.

## 6. Plan

- **Tonight (done or running):**
  - memory cleanup;
  - this audit;
  - the F1 October-release ledger PR, so research can use October data from 10-16;
  - the FINAL and its markers chained on the hosts.
- **10-16 morning:**
  - one records PR: the FINAL result, labelled compromised; the markers; the walk-2 log line; QP's open items;
  - one docs-slimming PR (section 3 rows 1–5 and section 4, with no rule text changed; reviewer, plus QP only where a DEC cell changes).
- **10-16 onward, the push:**
  - (a) Score every non-dead library row on the section 5.1 target spec. The work is a Haiku fan-out with one judge pass.
  - (b) Take the top 2–3 lines that could plausibly reach about 0.3+ SOL/day into a capacity and slippage study at 0.25–1 SOL.
  - (c) Propose walk 2 as their forward confirmation block (the owner's ledger edit plus QP).
  - (d) Bring the owner a capital plan tied to a gate pass.
