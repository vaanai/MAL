> Repo copy of /data/mal/hunt-1008/push-1016/hunt23/QP-RULING-1019-throughput.md (source sha256 79cea8998a6e45aaddfed9dd36987c9af346b1463847b3a1b507c5511450565d, mtime 2026-10-19T19:46:36Z; it holds version 1 and "Version 2"), with this note added; everything below it is the source unchanged. The source is a mutable /data file, and this copy is the cited text. The source's own times are approximate: its header (about 19:41Z), the "~19:45Z" on its manager-decisions heading and the "~19:50Z" on its Version 2 heading are not the fixed instants. The file mtime is. The Version 2 heading's "~19:50Z" is later than that mtime. Version 2's "Item 6(j)" (a bank Part 1 merged after any price of its cohort) collides with version 1's item 6(j) (memory and restarts); DEC-031 Amendment 3 item 8 lists it as (k).

# QP ruling 2026-10-19: hunt23 meme-coin push, throughput plan (desk only)

Saved by manager12 from the quant-proof agent's final message, which came in at about 19:41Z. The text below is the agent's, unchanged in substance. No tape, ledger, bots.json, block or sealed path was opened. No book has cleared the gate. Nothing here is a profit claim.

## 1. Rolling registration: GO WITH CONDITIONS
No amendment is needed to register books one at a time.

DEC-031 gives each bot_id its own clock and one read (§1.2, §2). Nothing in it requires cohorts.
- The bots.json line and its notebook copy go in before the clock, and the records PR follows within 24 h.
- One process per frozen_sha (R5).
- The Am. 2 V-band check is named as path:line@frozen_sha.

**Every read must state:**
- N: every book with a clock before the read, stopped and NOT_DECIDABLE books included, plus the verdicts of the books already read.
- For each book: its agent, the DECLARE sha256 and notebook id, the cells declared, priced, killed and surviving, its blocks, and any October hours used.
- The total cell count across hunt23, J3 and cohort 2. Books dropped at P0 or at the bank are disclosed but not counted in N.
- The one-sided p (10,000 draws, seed 1, the larger of the two fail models).
- A plain line: if no book has an edge, the chance of at least one screen pass is up to 1−0.95^N. That is 40% at N=10 and 79% at N=30.

**Family-wise rule:** keep DEC-031's two stages. The confirmation on a fresh window at 0.05/c is the correction. Add no Bonferroni over N at the screen.

**One gap needs a DEC-031 Amendment 3, merged before the first hunt23 clock:** every screen PASS enters confirmation automatically and counts in c. Otherwise someone chooses which passes to confirm.

## 2. Screen bar: GO WITH CONDITIONS
The RULES.md kill rule is needed but is not enough.
- With no edge, a cell clears "3 of 4 blocks positive" 5/16 of the time.
- The three September blocks are back-to-back P1 hours, so the test really covers two periods, not four. Oracle in-sample is also the block where the 972-cell grid was mined.
- RULES.md decides on strand rule "total" only. tools/arena_score.py (lines 33-35) decides on four sub-cells: flat and pressure, each under "sc" and "total".

**Add these to DECLARE.md before pricing:**
- (a) The kill rule applies to all four sub-cells.
- (b) explore-0814 alone, and the September span pooled, each have mean > 0 under both fail models.
- (c) The gate itself, pooled in-sample, on all four sub-cells: CI90 lower bound > 0 (1,000 draws, seed 1), a majority of dates positive, ex-top-3 > 0 and ex-best-day > 0.
- (d) At most one survivor per declared rule group, picked by a statistic fixed in advance, and at most 2 per agent.
- (e) Event-triggered cells need a worst-in-slot mean > 0 (R7).
- (f) Before registration, an October activity count on the tape in R3's form, fixed before it runs: at least 60 attempts over 3 days and no day under 10.

**Why not Bonferroni at 12:** the fresh-window confirmation already controls false passes. And a survivor without an in-sample CI90 lower bound above 0 has little chance of passing about 150 forward attempts once winner's curse shrinks it.

## 3. October arena tape for outcome screening: GO WITH CONDITIONS
- (a) Any book whose design touched these hours is an arena book only, forever (DEC-031 §3.3, R9(a)).
- (b) One exploration window per cohort, written in a notebook entry before any October price: a list of closed tape hours from 2026-10-19T08:05:20Z. It is never extended. Each book's clock falls after the window's last hour and after the last exit of any trade priced in it.
- (c) The window can only kill. It removes an Aug/Sep survivor whose deciding-cell mean is ≤ 0 in any sub-cell. It never adds, ranks or picks a cell. A cell designed on October alone takes the R9(d) route with its own 12-cell declaration.
- (d) Replay through ArenaEngine on the tape only, with gap rules on, the pinned engine shas and the book's own StackConfig. Apply the R9(b) 360-s filter. No C1-NF reproduction. No other book's ledger or decision log.
- (e) Never price a registered, unread book's rule, or a retune of it, on that book's counted hours. That is an interim peek (DEC-014 (c)).
- (f) October figures are labelled in-sample and never cited. Those cells count in N, and J3's overlapping window is disclosed next to them.

## 4. Fresh-08 holdout bank: GO WITH CONDITIONS; NO to a one-UTC-day test
The ledger corrects the premise. Only fresh-0802 is EXP-021's (Part 1 §1, and that claim is inert unless its screen passes). fresh-0808 and fresh-0828 are reserved with no owner. Their earlier claimants (EXP-013, 014, 015, 017) are closed or SCREEN NONE, and EXP-024 rejected 0828. No other live claim was found.

**DEC-014's block budget (2026-10-07) binds:**
- The claim needs the survivor's pooled screen p < 0.025/m. m is at least 16, plus the three hunt23 families, so at least 19. That means p < 0.0013.
- One block per family.
- One block always stays unspent, so at most 2 of the 3 can be spent.
- The Part 1 pre-registration must merge before the screen is scored. So the bank's pre-registration has to merge before any hunt23 cell is priced. Otherwise no hunt23 survivor can use it.

**Design:**
- Spend fresh-0828 first (closest to September; it sits next to explore-0814, which is disclosed), then fresh-0808. Keep fresh-0802 in reserve.
- One block per cohort, read once in one locked job, after an outcome-blind precount shows at least 100 attempts on at least 5 dates.
- At most 3 survivors per block, one per agent family, with Holm at α = 0.05 (10,000 draws).
- Pass: the full gate on all four sub-cells, ex-best-day > 0, and Holm, all on the frozen bot's exact code (md5-equivalent).
- A pass earns registration only, not a shortcut. An August pass checks for selection bias. It does not test the October regime.
- A fail, or a NOT_DECIDABLE after the lock, is final for that rule and every retune. The block is spent, and the family can claim no other block.

The gate needs at least 5 UTC dates, so one day of data cannot be decisive. "One day" can only mean compute time.

## 5. After an arena pass: GO WITH CONDITIONS
- **Before the canary:** quant-proof's check, the owner's OK, a canary DEC, ex-best-day positive, the worst-in-slot mean and CI90 lower bound quoted (R7), and Am. 2 items 5 and 6 (the sell builder and the mint fetch). DEC-031 needs no confirmation before a small canary.
- **Shortest honest confirmation:** §2 step 4 as written. The same bot_id, at least 100 attempts on at least 5 further UTC dates, and p ≤ 0.05/c. The 5 dates are the binding limit: 5 days at 30 attempts a day.
- **Parallel:** yes. The canary runs inside the confirmation window.
- **A second frozen copy: NO.** It is a new bot_id that repeats the parent's decisions. It either sits inside the screen window or duplicates the confirmation, and it adds to N.

**Timeline for a book registered on 10-22 at 30 attempts a day:**

| Step | Date |
| --- | --- |
| Clock | 10-23T00:00Z |
| Screen read boundary (150 attempts, 10-23 to 10-27) | 10-28T00:00Z |
| Canary request, after exits and the mint fetch (up to 24 h) | 10-28 for short holds, 10-29 for 24 h holds |
| Confirmation boundary | 11-02T00:00Z |
| Stake above canary limits, at the earliest | about 11-02/03 |

## 6. What in the plan would taint a later read
- (a) RULES.md decides on "total" only (see item 2).
- (b) RULES.md leaves out the EXP-025 §4 and DEC-031 §3.7 seal. Books with clocks before 10-24T02 trade inside the C1-NF seal, and the long-hold agent works in C1-NF's universe. Drop any cell that matches C1's decision grid and exit before pricing.
- (c) The rent-0 universe is the same as EXP-024's V-range universe. A cell without the 360-s filter cannot run before 11-14, and adding the filter after the screen makes it a new rule.
- (d) Pricing registered books' rules on their counted hours (item 3e).
- (e) Siblings forward: if several near-identical books run, the best of them at read is in-sample.
- (f) The DECLARE sha256 lives only in a /data file that can change. It needs a notebook entry with its instant before the first price, or the whole screen counts as undeclared.
- (g) Repeat screens on the same four blocks. Every priced cell counts, and each family adds to m.
- (h) Wallet and creator history. A forward book can build it only from the tape (R6), so an August screen on full history does not carry over.
- (i) Edits to shared engine files while books are running (R5(c)).
- (j) Out-of-memory events or restarts cause feed gaps, which make every open position in that process NOT_DECIDABLE (R4, R5). Set a memory cap per process, and plan around J3's 64 GB job.

## Manager decisions on this ruling (2026-10-19 ~19:45Z)
- RULES.md Amendment 1 applies items 2, 3 and 6. It was written at 19:42:30Z, before any hunt23 price, and sent to all three idea agents.
- **The holdout bank is NOT used for wave 1.** Its pre-registration would have to merge before any pricing, and a bank pass earns registration only, not a shortcut. The bank stays sealed for a later wave, and only if a pre-registration merges first.
- DEC-031 Amendment 3 (every screen pass enters confirmation automatically and counts in c) goes into a records PR before the first hunt23 clock.

## Version 2 (same QP agent, after the owner released the August blocks; arrived ~19:50Z): changes from version 1
- **Item 1:** each screen read also reports, report-only, its p (10,000 draws, seed 1, the larger of the two fail models) and its Holm rank among all arena reads. Pin items 1, 3 and 6 in DEC-031 Amendment 3 before the first hunt23 clock.
- **Item 2(f):** before registration, a structure-only October count (P0, R3 template). Drop the cell below 20 attempts/day, or if any day is below 10.
- **Item 4 (bank, firm design):**
  - **Unit:** one block per cohort, owned by one EXP, with at most 3 survivors and at most 1 per agent family. Each family gets one block, ever.
  - **Entry:** pooled screen p < 0.025/m, with m frozen at the claim and at least 21, so p < 0.0012.
  - **Timing:** Part 1 merges before ANY price of that cohort. Part 2, with the frozen shas, merges before the read.
  - **Read:** the full gate on all four sub-cells at the Am.2 deciding cell, with rent-charged and worst-in-slot reported, ex-best-day > 0, and a Holm pass at 0.05 across the cohort (10,000 draws, trade-level and date-cluster).
  - **A pass is NOT canary evidence:** EXP-012 passed fresh-0903, then FAILED forward. A bank pass earns an arena slot. Under Amendment 3, that book's FIRST arena read may serve as its confirmation (p ≤ 0.05/c).
  - **Order:** fresh-0828, then fresh-0808. fresh-0802 stays in reserve.
  - **Pre-read checks:** as in EXP-015 §6 (re-hash 144/144 hours, pin the V map, V coverage > 99%, an outcome-blind precount ≥ 100 attempts over ≥ 5 dates, md5 equivalence, an O_EXCL lock).
  - **A FAIL, or NOT_DECIDABLE after the lock,** kills the rule and every retune. The block goes to exploration.
  - **Record the release** as an EXP-021 withdrawal plus a ledger edit.
- **Item 6(j):** a bank Part 1 merged after any price of the cohort it serves is a taint.

**Manager:**
- Wave 1 (agents A/B/C) is NOT bank-eligible.
- For wave 2, a bank Part 1 claiming fresh-0828 merges BEFORE any wave-2 price.
- With the bank, the time saved is one 5-day confirmation window: the first arena read doubles as confirmation.
