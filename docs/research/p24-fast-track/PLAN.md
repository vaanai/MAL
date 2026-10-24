# P24 fast track: idea → registered forward book in 24–36 h, gate and seals unchanged

Lane P24 (manager15), written 2026-10-24 from `date -u` on mal-research-0. Records and code only. No tape, ledger, `bots.json` content, shadow output, probe output, fwd-v1 or holdout directory, and no gap row or hour-health output for [2026-10-22T21Z, 2026-10-26T21Z) was opened to write it. The timelines come from QP ruling files, pack scripts, manager logs, commit and PR times, and the records files named in each row. Nothing here says any book makes money. No book has cleared the promotion gate.

**Goal.** Cut the time from idea to a registered forward paper book to under 24–36 h. Do not lower the promotion gate, the caps or any seal. Make every forward book "live-ready", so that a PASS goes to the DEC-032 canary without new findings.

**Built in this PR (section 5):**
- `tools/fasttrack_book.py` turns one manifest into a lint, sha256 pins at the frozen sha, and the whole registration pack. 36 tests.
- `tools/causal_harness.py` makes screen decisions causal by construction, runs a truncate/perturb lookahead check on any batch screen, and gives md5 stream/batch equivalence. 16 tests.

---

## 1. Where the days went

### 1.1 Timelines (UTC; "+" means elapsed since the idea)

**FLOOR (`l1-floor-v2`, DEC-031 Am.9).** Not registered. Its clock is planned for 2026-10-26T00Z, 17 d 03 h after the idea.

| Instant | Step | + |
|---|---|---|
| 10-08 20:52 | Idea, G4-FLOORDIP (fd24f3be) | 0 |
| 10-21 17:40 | Screen declared (DECLARE-L4) | 12 d 21 h |
| 10-21 18:38 | QP: GO WITH CONDITIONS, "not now" | 12 d 22 h |
| 10-22 15:50 / 19:31 | Port #786 merged; memory fix #796; frozen sha 5e12f8f5 | 13 d 23 h |
| 10-23 10:16–10:27 | P0 tool #810 and pack; QP CHANGES K1–K6; ON HOLD (14 d job cap, feed gaps) | 14 d 13 h |
| 10-23 17:10–17:20 | Fast-track ruling; Am.9 drafted, 3 QP rounds, merged | 14 d 20 h |
| 10-23 17:43 | Gap scan: the 10-26 window fails by construction (deadline window) | 14 d 21 h |
| 10-23 20:02–20:07 | Erratum 1. QP OK'd a day-early formula twice; the reviewer caught it | 14 d 23 h |
| 10-24 12:51–15:27 | Smoke S1/S2; QP PASS with a wording erratum and a start-lag finding | 15 d 18 h |
| 10-24 16:42–16:53 | Prep ruling (stale sha, seal contact, L3 env); X ruling; Erratum 2 #880 | 15 d 20 h |
| 10-25 23:00–23:45 → 10-26 00:00 | E3 register, E4 run, clock (planned) | 17 d 03 h |

**HV4 (Haiku v4, 9 lane-G arms).** Not registered. Its clock is planned for 10-25T00Z, about 165 h after the idea. The clock slipped 4 times (10-21 → 10-22 → 10-23 → 10-24 → 10-25).

| Instant | Step | + |
|---|---|---|
| 10-18 03:01 | Folder and DESIGN | 0 |
| 10-18 03:49–05:32 | Build PRs #710–#715 merged | 2.5 h |
| 10-19 17:07 | 24 h stub OOM (about 740 MB/h) | 38 h |
| 10-19 23:00 | Stub fails scan timing (check 6): **T0 10-21 lost** | 44 h |
| 10-20 → 10-21 17:33 | v2 at 9ed4c5f; stub delayed about 30 h by the SSD pause | 86 h |
| 10-22 18:11 | Report NOT_PASSED: check 9 had no data source. **T0 10-23 lost** | 111 h |
| 10-22 19:28–19:42 | Am.6 (smoke-then-clock) merged; the P0 count is read as required. **T0 → 10-25** | 112 h |
| 10-23 01:52 | Real-model smoke PASS (QP Addendum 4) | 123 h |
| 10-24 01:24 | X-rule STOP (0 of 72 clean hours), then CLEAN v2 | 142 h |
| 10-24 12:53 | Final records head 297fd27a, QP OK and reviewer APPROVE (about 21 QP rounds) | 154 h |

**Q50 (`h5v1q50-boostfloor-a1`).** Registered at idea +54.6 h. Clock 10-25T00Z (+78 h).

| Instant | Step | + |
|---|---|---|
| 10-21 17:44 | Structure counts, L3 design | 0 |
| 10-21 23:35 | QP: NO-GO today, G1–G10, earliest clock 10-25 | 5.8 h |
| 10-22 00:17–00:49 | #789–#791 merged; exec ruling Q1–Q12; sheet locked | 7 h |
| 10-22 19:10 | A3 guard HALT ends both a2 books. **Nobody saw it for 29.6 h** | — |
| 10-23 17:2x | md5 replay EQUAL | 47.6 h |
| 10-24 00:12–00:21 | P0 PASS; registered; shadow starts | 54.6 h |
| 10-24 00:45 | HALT found 25 min after registration (precondition 3 had lapsed) | 55 h |

**DBC m10.** No book is running. The earliest forward clock is 10-30 under the design. All instants are 10-24.

| Instant | Step | + |
|---|---|---|
| 03:55–03:59 | Pricer and screen written. **Lookahead L1/L2/L3 introduced** | 0 |
| 04:07 | Screen: m10 best of 12 | 0.1 h |
| 04:14 | QP 1: eligible for one holdout look. Saw L1 and L3; tested only L3 ("does not enter P&L") | 0.3 h |
| 06:24–06:27 | Holdout read #1493 crashes (subdirectory layout); void | 2.5 h |
| 07:02 | #1494 NOT_DECIDABLE: EvtSwap3 labelled as a hook, 0 triggers. **7 holdout days spent; confirmation ≥ 11-01** | 3 h |
| 07:09 | Forward registration tag | 3.2 h |
| 14:20 | Causal engine replay: 98 of 400 screen entries are screen-only | 10.4 h |
| 14:28 | QP 5: lookahead real, enters entry selection | 10.5 h |
| 15:40 | Amendment 1: the causal set decides. The causal flat mean is about 62% of the screen's, and the CI90 lower bound fell about 4x | 11.7 h |

### 1.2 Top 5 time sinks

| # | Sink | Cost | Books | Root cause | What catches it early |
|---|---|---|---|---|---|
| 1 | **Clock slips from pre-clock checks that were not about the rule** | HV4 96 h (4 slips); FLOOR 6.7 h hold and a 1-day slip margin | HV4, FLOOR | Each slip cost a whole UTC day, because the clock is the first 00:00Z after the record. The causes were memory growth, scan p99, a check with no data source (check 9), a P0 rule read two ways, and a job cap above the platform's 14 d | Am.6 (already merged) removed the 24 h stub. Lint M06 (job limit vs the 14 d cap), M19 (job memory vs host cap), M20 (RSS projection over the window), and a pre-mortem item for every check, with its data source named |
| 2 | **Mechanical QP rounds and latent mechanical defects** | FLOOR 14 rounds (4 mechanical, 3 mixed); HV4 about 21 (9 mechanical); Q50 about 10 (6 mechanical). Latent 20–29 h each | all | Shas, deadlines, cadences and thresholds typed by hand into 15+ scripts per pack: stale E3 sha posted (20.5 h), "CLOCK − 24 h 15 min" OK'd twice by QP, smoke "≥ 10 status lines/h" when the code emits 6 (29 h), run starting 5 min before a 15-min start-up (29 h), gate constant 2.0 vs 12.5, Am.9 citing 160,000 per send when code prices 505,000, FILL placeholders across 5 commits | **fasttrack_book**: all computed from one manifest and the code at the frozen sha (M04–M09, M24; `pin` constants, cadence and tool shas; `verify`; PINS.sha256 checked by every script) |
| 3 | **Lookahead found after the screen, the holdout and the build** | DBC: 10.5 h latent. The selection (best of 12) was weakened, R7 was withdrawn, and the rework took Am.1, #1533, #1534 and #879. The flattered mean was about 1.6x the causal one | DBC m10 | Back-fill from later rows (L1), a whole-window majority (L2), and a refusal that reads the exit row (L3), all in vectorized screen code | **causal_harness** `check` at screen time: truncate and perturb probes at the decision instants. Its tests reproduce L1 and L3 and catch them |
| 4 | **Idea → DECLARE queue, and design feasibility found late** | FLOOR: 12 d 21 h from idea to DECLARE; then 6.7 h on hold for feasibility (job cap, feed gaps, X rule); 20.7 h latent X-rule trap; 2.8 h deadline-window failure | FLOOR, HV4 | No standard DECLARE. Feasibility (job cap, feed health of the needed window, threshold computability) was checked after the DEC text | DECLARE template (2.1) plus pre-mortem PM03/PM04/PM08 answered before the first QP round. Lint M06; dry run of the hour-health thresholds on an old unsealed period |
| 5 | **One-shot data spent by unchecked tooling, and unseen halts** | DBC: 7 holdout days burned (swap3, 0 triggers) and the confirmation pushed about a week; #1493 used the one allowed pre-read failure. Q50: A3 HALT unseen for 29.6 h, a2 books NOT_DECIDABLE | DBC, Q50 | No count-only pass on the real layout. No exclusion-share gate. No alert on a guard HALT | Real-layout precount (memory: feedback-real-layout-precount); a "0 triggers or exclusion share > X% means NOT_DECIDABLE" rule pre-declared; PM12 halt alerts; register.sh refuses without PRECONDITION files |

### 1.3 Late defects and the standard check for each

"Kit" names the check in this PR. "Proposed" means not built yet.

| Defect (book) | Latent | Kit |
|---|---|---|
| Stale E3 sha in a public notebook entry (FLOOR) | 20.5 h | `verify` (V2: pins recomputed at the frozen sha); every script runs `sha256sum -c PINS.sha256` |
| Stale QP-ruling shas, wrong L6 pin (HV4) | 1 QP round | `pin` computes every sha256 from `git show`; `tool_refs` for tools at other commits |
| Day-early deadline formula; leftover "T0 10-23" text (FLOOR, HV4) | 2.3 h; wording | `schedule()`: every instant computed once (test_schedule_is_computed_not_typed) |
| Smoke cadence "≥ 10/h" vs 6/h in code (FLOOR) | 29 h | `pin` reads TICK_MS × STATUS_EVERY_TICKS at the frozen sha; the blocking list uses the derived value |
| Run start 5 min before a 15-min start-up (FLOOR) | 29 h | M07 and run.sh: start at clock − startup_s − margin; refuse if started after clock − startup_s |
| Job limit above the 14 d cap (FLOOR hold, Q50 15 d sheet) | 6.7 h | M06 |
| 505,000 vs 160,000 per send in DEC text (FLOOR Am.9 item 9) | 2.9 h | `constants`: DEC numbers in the manifest checked against the code literals at the frozen sha |
| Gate constant 2.0/day vs 12.5 in the script (FLOOR A2-v2) | 3 min | `constants` (same mechanism) |
| L3 tests: wrong venv, glob matched nothing (FLOOR) | 20.5 h | `pin` refuses a test path not in the frozen sha. Proposed: `pytest --collect-only` in the job's venv as PRECONDITION-tests |
| Threshold key or X rule not computable (FLOOR, HV4 #1452) | 20.7 h | `pin` checks the threshold keys against the tool's THRESHOLD_KEYS at its own commit. Proposed: dry run on an unsealed period |
| FILL/TBD placeholders across commits (HV4) | several rounds | M24; REGISTRATION.md has only RUNTIME lines, filled from job output |
| Lookahead L1/L2/L3 (DBC) | 10.5 h | `causal_harness check` / `run_stream` |
| Holdout tool found 0 files (DBC #1493) | 4 min, 1 of 1 allowed failure | Proposed: a generated real-layout preflight. Same pattern as PRECONDITION files |
| A3 HALT unseen (Q50) | 29.6 h | PM12; register.sh refuses without PRECONDITION files. Proposed: halt-to-notebook alert job |
| Memory growth (HV4 OOM), reviewer 2.0 of 2 GB (HV4) | 38 h | M19/M20; smoke-check (d) projects RSS over the whole window |
| Smoke root, scorer glob, stub default dir (FLOOR, Q50, HV4) | — | M10/M11/M12; generated paths only |
| Bash-isms in job scripts (memory: jobs run under sh) | — | `lint-sh`; render refuses a generated script with one; `sh -n` in the tests |

---

## 2. Standard kit

The rule for every item: whatever can be computed is computed. QP reads the DECLARE, the manifest, and any line that differs from the approved template.

### 2.1 DECLARE template (proposed; not built)
One file and one notebook entry, with its instant, before the first price. Fields:
1. **Rule.** Entry predicate, exit, universe, stake, deciding cell. The cells are listed and counted toward N.
2. **Data.** Exploration hours, as a closed list. Seal check against the ledger and DEC-031 §3. Holdout hours are never listed.
3. **Screen.** Tool path at a commit, `causal_harness check` command, probes and seed.
4. **Kill rule.** Written before any price.
5. **Exclusions.** The exclusion-share NOT_DECIDABLE rule (DBC swap3 lesson): 0 triggers, or an exclusion share above X%, means NOT_DECIDABLE, not FAIL.
6. **Capacity line.** Trades per day × stake × net edge, and impact at size.
7. **Live path.** The executor named in DEC-032 §1 item 5.

### 2.2 Screen harness, causal-only by construction (BUILT: `tools/causal_harness.py`)
- **`run_stream(decide, rows)`.** Rows go in one at a time, in (t, seq) order. `decide(view, row)` sees a `CausalView` holding only rows already delivered, projected to `allowed_fields`. A decision stamped before its row raises `LookaheadError`.
- **`lookahead_check(screen, rows)`.** For any batch screen (pandas, numpy, DuckDB). At seeded probe instants (each decision instant and the ms before it), the screen is re-run (a) on rows with t ≤ c and (b) with every numeric field of rows after c perturbed. The decisions at or before c must be byte-identical (canonical JSON). The report carries keys and instants only, never a price.
- **`stream_batch_equivalence`.** md5 of the canonical decision lists from the stream and the batch screen. This is the lab's md5 decision-equivalence form.
- **Limit.** A future value precomputed into a past row's column is invisible to both probes. Screens therefore compute their features from raw rows, and `check_fields` refuses outcome-looking column names.

### 2.3 Registration template from one manifest (BUILT: `tools/fasttrack_book.py`)
The manifest holds:
- bot_id, profile, rule names, frozen_sha, clock, slip, horizon, stake, lead and start-up times;
- paths, jobs, smoke, thresholds with labels, caps, cost, constants, pins, tool_refs, tests and preconditions;
- the LLM block, DEC refs and the pre-mortem.

The commands:
- `lint` runs 26 checks.
- `pin` reads `git show <sha>:<path>`. It requires the commit to be on an origin branch and records whether it is on origin/main (the DBC tags were not). It also checks rule names, cadence, threshold keys and DEC constants against the code at the sha.
- `render` writes the pack, once:
  - MANIFEST, pins, thresholds and PINS.sha256;
  - register.sh, run.sh, smoke.sh and smoke-check.sh;
  - blocking-list.txt, jobs.json (MiScusi job specs), REGISTRATION.md and SHA256SUMS.
- `verify` re-hashes the pack and recomputes the pins at the frozen sha. Run it before posting any pin.

Each generated script refuses all of these:
- the wrong sha;
- a dirty checkout;
- the wrong host;
- a stale pinned file;
- its own time window;
- (register.sh only) a missing `PRECONDITION-<x>.ok` file for p0, gapcheck, smoke, md5, tests, qp and reviewer, plus any extras the book adds.

`docs/research/p24-fast-track/example-floor.manifest.json` mirrors FLOOR's hand pack. On the real repo, `pin` reproduces FLOOR's L7 hour-health pin `227f92be…` and the 6-per-hour cadence. This is an example only: it changes nothing for FLOOR.

### 2.4 Pre-registration pre-mortem (BUILT as manifest items PM01–PM13; lint M23)
Each item needs a written answer before the first QP round:

| Item | Topic | What the answer covers |
|---|---|---|
| PM01 | Cost | Deciding stack; PumpSwap PRE-trade reserves with V ≈ 17.58 SOL; bonding POST-trade; E1 |
| PM02 | Fail models | Both fail models |
| PM03 | Exclusions | 5% / 10% caps and the expected excluded share from closed days |
| PM04 | Restarts and feed | Job limit vs window; feed job end vs window end; planned restarts |
| PM05 | Day-1 scorer | Dry run on day 1, with a glob that matches the bot_id |
| PM06 | Paper → live | Executor and md5 replay |
| PM07 | Seals | Every open seal vs the window |
| PM08 | P0 | P0 count on unseen days |
| PM09 | Capacity | Trades/day × stake × net edge, impact at size |
| PM10 | Memory | RSS projection |
| PM11 | Lookahead | causal_harness verdict |
| PM12 | Halts | Halt alerts |
| PM13 | LLM | Real-model smoke, parse bound, $/day × window under the cap |

Lint enforces the numeric parts:
- caps equal to DEC-031 (M14);
- both fail models (M15);
- deciding leg 1.9 s (M16);
- V and PRE-trade for PumpSwap (M17);
- E1 (M18).

### 2.5 Standard smoke (BUILT: generated smoke.sh and smoke-check.sh)
- DEC-031 Am.6 item 1.3, on a scratch root and registry that lint proves are apart from the book's.
- The RSS sampler is built in.
- The blocking list is (a) to (f): (b) is derived from the code's cadence, and (d) is the RSS projection over horizon plus tail.
- The list is written to blocking-list.txt to be notebooked before the job.

### 2.6 The 24–36 h schedule this kit allows
The day-boundary clock (Am.3 item 1) is the binding constraint. Everything before it has to fit inside one working block.

| T+ | Step | Owner |
|---|---|---|
| 0–2 h | DECLARE notebooked; screen with `causal_harness check` (CAUSAL_OK report sha) | agent |
| 2–4 h | P0 structure-only count on closed unseen days (job); QP screen ruling on the DECLARE and the screen | agent, QP |
| 4–6 h | Port to an arena host (rule book: StateView, pure rule); md5 replay; manifest; `lint`; `render` | builder |
| 6–9 h | Smoke (2 h job) and L3 tests (job), in parallel; QP fast-lane review: manifest plus template diff only | jobs, QP |
| 9–12 h | PRECONDITION files from job outputs; `verify`; records PR; register job before the computed deadline | manager |
| next 00:00Z | Clock | — |

That gives 12 h of work, plus up to 24 h of wait for the next 00:00Z, for 12–36 h in total. The slip rule (one day) stays.

---

## 3. Proposed DEC-031 Amendment 11: fast lane (DRAFT ONLY, not proposed for merge in this PR)

> **Amendment 11 (DRAFT; records only; before the clock of any book it covers; outcome-blind; status: DRAFT until merge).** DEC-031 Amendment 11: the fast lane. A book may register through an approved registration template. quant-proof then reviews the book's declaration, its manifest and every difference from the template, not the whole pack. The promotion gate, §2's deciding cell, one read per `bot_id`, the outcome-blind read instant, the caps of §1.4 and Am.6 item 3, the multiplicity block and c of Am.3, every seal of §3, Am.3 item 1's P0 count and clock rule, and Am.6's smoke and integrity rules are unchanged.
>
> 1. **Approved template.** A version of `tools/fasttrack_book.py` (and `tools/causal_harness.py`) at a commit on origin/main, named by its sha256 in a merged records PR with quant-proof's OK on the exact head and a reviewer verdict. A template change is a new template sha with its own OK. A book keeps the template sha it rendered with.
> 2. **Eligibility.** A book is in the fast lane only if all of these hold:
>    - (a) its pack is rendered by an approved template, with `lint` PASS and `verify` PASS printed in the records PR;
>    - (b) its generated files are byte-identical to the template's render of its manifest (SHA256SUMS), and any hand-written file is listed and reviewed in full;
>    - (c) its DECLARE was notebooked, with its instant, before the first price;
>    - (d) the screen that selected it carries a `causal_harness` CAUSAL_OK report (path@sha256), or the rule runs only in an arena host that shows the rule rows up to the decision instant only, with the md5 replay of the lab's runner-change rule;
>    - (e) every pre-mortem item is answered.
> 3. **What quant-proof reviews.** The DECLARE; the manifest; the screen's harness report; the pre-mortem answers; any hand-written file. quant-proof does not re-derive items the approved template computes or lints (pins, instants, cadence, job limits, constants, paths, caps). A finding of that class is a template defect: it is fixed by a new template version, and the book re-renders.
> 4. **Turnaround.** The fast-lane ruling is one round. A CHANGES lists every item at once. A mechanical CHANGES that the template could have caught becomes a lint check in the next template (ratchet).
> 5. **Live-ready.** The records PR names the live executor path (DEC-032 §1 item 5) and the md5 replay plan (PM06). A screen PASS then needs only DEC-032 section 8 items, not new findings about the book.
> 6. **Not allowed.** A manifest that loosens a cap; a hand edit of a generated file without listing it; a frozen sha or tool ref not on origin; a register job without its PRECONDITION files; a template that reads any ledger, outcome, fill, exit or P&L.
> 7. **Counting.** A fast-lane book is an arena book like any other: it counts in N from its clock and, on a screen pass, in c. A pass is a screen, not a profit claim.

Why this keeps the bar:
- The template is reviewed once, and harder than any one pack: it has tests for each defect class.
- Every substantive judgement (rule, data, seals, multiplicity) stays with quant-proof.
- The gate, read rule, caps and seals are untouched.

---

## 4. Signal → Haiku bot path

These are the current HV4 facts, from the timeline agent's reading of `tools/hv4_*`:
- **Model and output caps.** `claude-haiku-5-5`, thinking off. max_tokens are entry 400, risk 400, re-check 120 and reviewer 4,000.
- **Cards.** About 700 tokens each. They are built in `tools/hv4_cards.py`; `filters()` is at line 445 and the hard vetoes are code, failing closed.
- **The `x` field.** It is reserved (`hv4_cards.py:1112` `"x": None`) and refused non-null "in this window" (`hv4_prompts.py:221`).
- **Cost.** These figures are assumed in DESIGN §6, not measured:
  - $0.000495 per entry call;
  - about $2/day expected, $4.24/day at the call caps;
  - hard cap $4.70/day; month guard $160.
- **Smoke.** 384/384 calls OK; tokens and $ were not measured.

There are three routes for a newly screened signal, fastest first:

| Route | How | New bot_id? | API $ | Time |
|---|---|---|---|---|
| A. **Code book** | The signal is the rule or a veto in an arena_rules_v2 book through the fast lane | yes | 0 | 12–36 h (section 2.6) |
| B. **HV4 hard veto** | A new veto in `hv4_cards.filters()`, failing closed. Candidates it removes never reach the model | yes (code change, DEC-031 §1.2) | ≤ HV4's: fewer calls | Fast lane plus a real-model smoke (Am.6 1.3(f)) |
| C. **HV4 card feature** | A new HV4 window whose `x` schema carries the signal value at `as_of_ms`. The value is written by a causal producer (`causal_harness.run_stream` decide()), stamped by the producer, not the book (Am.6 2.2) | yes, new window | Estimate, not measured: a short `x` field on a ~3,200-token prompt, about +1–2% over HV4's spend | Fast lane plus the real-model smoke, PM13 and a playbook note |

**Budget (API cap $200 to 2026-11-11, owner 10-21; offline LLM tests run on the Claude plan, not the API).**
- From the 10-25 clock to 11-11 is 17 days.
- HV4 alone: about $34 expected ($2 × 17) and $80 at its hard cap ($4.70 × 17).
- If HV4's $160 month guard is treated as reserved, about $40 is left: one more LLM book at ≤ $2.35/day over 17 days.
- If only HV4's hard-cap use is reserved, about $120 is left: one more book at HV4's caps.
- Either way, no more than one extra LLM book before 11-11.

**Recommendation.**
1. Every new signal goes first as route A or B (zero or negative API cost).
2. Route C is used only for a signal whose code book or veto passed its own screen read, so the LLM book tests the LLM's use of the signal, not the signal.
3. A signal never reaches a running HV4 book: any change is a new `bot_id` and clock. Replaying a signal over a registered book's counted days is an interim peek (Am.3 4(e)).

---

## 5. What was built, and what was not measured

| Piece | Files | Tests |
|---|---|---|
| Manifest-driven registration generator | `tools/fasttrack_book.py`, `docs/research/p24-fast-track/example-floor.manifest.json` | `tools/test_fasttrack_book.py`: 36 green. 24 single-defect lint cases; schedule arithmetic; sh lint; pin and refusals on a throwaway origin repo; render + verify + stale-pin catch; `sh -n` on generated scripts; real-repo reproduction of FLOOR's 227f92be pin and 6/h cadence |
| Causal screen harness | `tools/causal_harness.py` | `tools/test_causal_harness.py`: 16 green. Full-series quantile, next-row peek, back-fill (DBC L1), mis-stamped decision and exit-row refusal (DBC L3) caught; causal screen passes; stream view isolation; field refusal; md5 equivalence; CLI |

**Not measured:**
- No book has been rendered or registered with the kit. The time saving is an estimate from the timelines above.
- The harness has not been run on any real screen (DBC or others). Running it on `tools/dbc_screen.py` over exploration hours needs a counts-only job and is the first follow-up.
- Only the `arena_rules_v2` host profile exists. HV4 (`hv4_runner`), the H5 executor and `dbc_fwd` need their own profiles.

**Next builds, by leverage:**
1. `causal_harness` run on the next screen as a standard job.
2. DECLARE template and lint.
3. Generated day-1 dry-score job and halt-alert job.
4. HV4 and H5 profiles.
5. `fasttrack_book diff` (a pack vs the approved template's render) for quant-proof.
