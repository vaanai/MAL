> Repo copy of /data/mal/hunt-1008/push-1016/hunt21/QP-RULING-1019-cohort2.md (source sha256 1583c4d7a8b8194bd60356dd027bbc436d56c8250617a1a17543b41dd31cb8cc, mtime 2026-10-19T17:36:28Z), with this note added. Its header times (~17:40Z, ~17:35Z on R9, ~17:50Z on R9(f)) are wrong; the file mtime 2026-10-19T17:23:17Z (sha256 c6f0a5e3ed022b6aa319238738e97a245d681de7daa138bb08abad342dbefe81) bounds R1 to R8: they were in the file by then, and DEC-031 Amendment 2 cites R1 and R8 item 7 only.

# QP ruling 2026-10-19 (~17:40Z): arena cohort 2 (rule bots) and arena rent, R1-R8

Quant-proof, PAPER ONLY. Read at origin/main 77bdf7e in my own worktree /tmp/claude-1000/qp-arena-1019: DEC-031 (rev 2 and Amendment 1), CLAUDE.md gate, tools/arena_paper.py, tools/arena_stack.py, tools/arena_score.py, tools/arena_state.py (warm-up only), tools/arena_h5_cost.py (rent lines), tools/hv4_runner.py (stack_config), the sell paths of tools/probe_live.py, tools/h5_executor.py and tools/pumpswap_tx.py (source only), tools/probe_sim_calibration.py, DEC-021:54 and Amendment 7, EXP-027 §5.2, the two RULE.md files and COHORT2-SCOPE.md.
Not opened: the arena tape, ledgers, bots.json, _shakedown-*, runner-status.json, /data/mal/blocks, the tip tape, /var/lib/mal/sealed, any shadow/canary/probe/walk output, outputs of #817 #729 #658 #662. The probe figures quoted below (61 of 61 refunds, 1,513,840 chain rent) come from EXP-027 §5.2's pinned text, not from probe outputs.

No book has cleared the promotion gate. Nothing below is a profit claim.

## R1. RENT: OK WITH CONDITIONS. Rent refunded is the honest deciding cost. It needs a DEC-031 amendment, not a config ruling alone. H5 stays as registered.

Facts from source:
- Both live sell paths put sell and base-ATA close in ONE transaction, under the mint's own token program:
  - tools/h5_executor.py:2524-2525: `tx.sell_instructions(...)` + `pl.close_token_account(base_ata, user, base_tp)`;
  - tools/probe_live.py:759-760: the same.
- tools/pumpswap_tx.sell_instructions on its own closes only the WSOL ATA (pumpswap_tx.py:428). A builder that used it without the extra close would leave the base ATA rent locked. That is the trap condition (d) below guards against.
- The close is atomic with the sell. If the close cannot run (tokens left behind, a close-blocking extension), the whole sell reverts and the position sticks. That is strand behaviour, not "rent lost on a good sell".
- Probe evidence (EXP-027 §5.2, QP-FU §1a): 61 of 61 probe trips refunded, net 0.
- Chain rent on 2026-10-16 was 1,513,840 for the 170-byte Token-2022 ATA. So the arena's 2,039,280 is 35% above what the chain charges even when rent really is lost.
- The lab's other deciding stack already prices rent at 0: arena_stack.RENT_DECIDING = 0 (vendored from r4b_frozen), and EXP027_RENT_DECIDING is 0 under §5.2 (i)-(iii). DEC-031 §1.4 says the arena prices "at the lab's deciding stack", then lists rent 2,039,280. Those two statements disagree. The arena binding default (arena_paper.RENT_LAMPORTS = F.RENT_REPORT) is the report-only leg of the lab stack.

E1 floor, both directions:
- probe_sim_calibration.pnl_of builds the sim P&L as proceeds - spend - the LIVE fees of both sides - the failed-sell cost - rent_net(b, s). rent_net is the same trip's live rent charged minus rent refunded.
- The live P&L (probe_live, sell sol_delta - buy_cost) contains that same net rent.
- So rent, and fees too, cancel in live minus sim by construction. The 384,022-per-0.05-SOL gap (n = 8) is a pool-leg / execution gap with no rent in it, whether rent was refunded or not.
- Result: rent-0 drops no cost that E1 absorbed, and rent-charged does not double count with E1. Rent must be priced on its own, at what live pays. With an in-tx close, live pays 0. The floor and the haircut stay as max(), not a sum (arena_paper.price_leg); that is correct.
- In the deciding stack, rent changes no other term: the auto-strand threshold (proceeds < one send), the E1 extra (pnl0 - pnl, where rent cancels) and the pressure refit (ssb, nearby, filled) do not depend on it.

Size of the effect: 2,039,280 per filled trip is 0.816% of stake at 0.25 SOL, 0.408% at 0.5 and 0.204% at 1.0. That is about 0.8 of the scope's ~1.9 pp extra arena drag at 0.25 (my check of the scope's arithmetic; not a measurement). This is a large term. Keeping a cost the executor does not pay is not "conservative honesty": it is a mislabelled haircut. If the lab wants more margin, it belongs in a named haircut, not in rent.

Adversarial, the other way. What rent-0 must not hide:
(1) A strand (unpriced or invalid exit, proceeds < one send) still loses stake + buy send + rent. The config already does this: _trip passes cfg.rent only as `rent`, and rent_stranded stays F.RENT_STRANDED = 2,039,280.
(2) Close-blocking mints (freeze authority, withheld transfer fees, other extensions). The arena has no mint-account check today, so these would be priced as clean trips. Rent-charged gets them wrong too (live they strand, a total loss).
(3) A live canary that sells through a builder without the in-tx close. Then rent is real.
(4) One-off accounts (the PumpSwap user_volume_accumulator) are paid once per wallet, not per trip. They are not a per-trip cost. Disclose them; do not charge them per trip.

Ruling and conditions:
(a) Form. arena_paper's docstring cannot override merged DEC text, and DEC-031 §1.4 names 2,039,280 per filled trip. So this needs DEC-031 Amendment 2: QP OK on the exact head plus a reviewer verdict on the PR, merged before the first clock it touches.
  - It applies uniformly to every book whose clock_start_ms is after the merge and whose registration is after the merge. There is no per-book choice.
  - HV4: only if the amendment merges before HV4's registration and T0 2026-10-21T00:00Z, and hv4_runner.py passes rent=0 explicitly. Line 1595 builds `stack_config(feed_delay_slots=...)` with the default, so today it is rent-charged. This is a code change before HV4's freeze.
  - If that does not happen, HV4 decides at rent-charged as built, and the amendment covers cohort 2 only.
  - Pass rent=0 in each host's StackConfig. Do not change arena_paper's default: its sha256 is a §1.4 pin.
(b) Outcome-blind statement. The amendment states which arena, HV4-shakedown or replay P&L figures the chooser had in view when choosing. This is a deterministic per-trade shift and the ruling rests on code and pre-arena evidence (QP-FU §1a, 2026-10-16), but the disclosure is owed.
(c) Strand rent stays 2,039,280. It is not re-based downward to chain rent. Disclose that it is 34,800 below EXP-027's Token-2022 convention (2,074,080) and 525,440 above the chain's 1,513,840.
(d) Canary carry-over (EXP-027 condition (i) mirrored). A canary from a rent-0 book must sell through a builder that sells and closes the base ATA in one transaction under the mint's token program, pinned path@sha256 (today h5_executor.py and probe_live.py do). Otherwise the rent-charged leg is the paper comparator for that canary.
(e) Close-blocking class (EXP-027 (iii) and item 4 mirrored). Rent-0 books trade only canonical pump pools (V on the deciding row in the canonical band; see R2).
  - After the read boundary, run one structure-only mint-account fetch of the book's traded mints (counts; credits recorded).
  - A freeze authority, an extension outside {metadataPointer, tokenMetadata}, or a failed fetch makes that trip strand class in the deciding cell.
  - This is pre-declared in the amendment. Census prior: 295 of 300 Token-2022 with exactly those two extensions, 5 SPL, 0 freeze (EXP-027 allowlist census).
(f) Rent-charged leg reported beside every deciding figure, as an exact transform of the rows. Per filled non-strand trip: flat leg - 0.85 x 2,039,280; pressure leg - (1 - p_i) x 2,039,280, with p_i the cell's own refit probability. Guarded and strand rows are unchanged.
  - Compute it with a pinned report tool, or with a scorer change carrying QP OK and the sha disclosed.
  - Any pass sentence quotes it. A book that passes only at rent 0 is described in those words.
(g) Config pin. The records PR pins each new book's StackConfig.as_dict(): rent 0, e1_floor_per_005 384022, strand_rule "both", k_rounding, feed_delay_slots, gap_slots 150, gap_silence_ms 60000. The read checks that the ledger rows' `stack` equals the pin.
  - The scorer only refuses MIXED configs inside a bot (arena_score.py:300-302). It never checks the values, so a wrong config would pass silently.
(h) H5 books (clock 2026-10-18T06:00Z) are unchanged: deciding rent stays 2,039,280 (tools/arena_h5_cost.py RENT). Every H5 read:
  - discloses this ruling;
  - may print a rent-0 leg (+0.85 x rent flat, +(1 - p_i) x rent pressure, per filled non-strand trip) labelled "report-only, added after the clock, never deciding".
  - A post-clock switch in the known-favourable direction is a researcher degree of freedom. That is why it is not allowed even though rent-0 is the truer cost.

If the amendment does not merge before a book's clock, that book decides at rent-charged as built. No retroactive switch.

## R2. h2ps-a1 is a new bot_id with no inherited evidence: YES

The new bot_id itself is OK. h2ps-a1 is not H2 Book A.
- Book A was frozen 2026-10-08 as a bonding+PumpSwap book.
- It was selected on DISCOVERY 2026-08-14T12..08-28T11 and confirmed on September.
- It ran at 0.1/0.25 SOL, 55,000 per send, no rent, no E1, no strand class, 1.3 s primary leg, deciding on a date-cluster CI.
- Its PumpSwap-only subset was never reported (scope: "bonding share unknown").

The read must state:
1. New bot_id. No evidence from Book A or JUDGE.md (+2.59% flat, +2.35% pressure at 1.3 s, +2.38% pressure at 1.9 s; 55k stack) carries over, as a prior or as a confirmation. The PumpSwap restriction exists because arena_score makes any bonding row REPORT_ONLY, an engineering reason, and nobody measured this subset.
2. Selection: in-sample. The wallets are the top 50 by t among eligible wallets on August data, roughly 2 months stale at the clock. Book A's pressure SOL was 79% one wallet (4XWC...), per the scope quoting JUDGE.md. Report per-wallet attempt counts and P&L share, and the total ex-top wallet, as report-only.
3. Universe as frozen in RULE.md: PumpSwap prints on canonical pools only, V in [17.5e9, 17.7e9] on the triggering row, and WSOL quote. The scope's "PumpSwap/WSOL" omits the V band. It must be in the rule module.
   - Without the band, a non-canonical pool with V = 0 and real quote < 17.5 SOL is priced entry_invalid. arena_stack.round_trip: Qe < V_LO means a total-loss strand. That is a pricing artefact.
4. Declared mechanics changes from RULE.md:
   - one attempt per mint counting PumpSwap signals only (a wallet's bonding buy neither triggers nor uses up the mint);
   - exit on the followed wallet's first sell after its signal buy, or 600 s, decided when the sell row is seen;
   - the engine's feed delay d plus exit lag replaces RULE.md's latency arithmetic;
   - deciding cell 1.9 s at 0.25 SOL, because register_bot allows only 0.25/0.5/1.0 (arena_paper.py:240) and Book A's 0.1 is impossible. 1.3 s is report-only.
5. `trader` identity: name the field H2's September tape used and the one the arena tape uses (schema line 38: "the signer / user wallet"), from decoder code, not data. If they differ (event user vs fee payer), P0 is void and the book is not registered until it is fixed.
6. The frozen wallet list (sha256 345d3903...) is inside the frozen commit, or loaded as path@sha256 and verified at start. It lives outside the repo today, so frozen_sha would not cover it.

## R3. P0 activity precondition for h2ps-a1: OK, with this rule fixed BEFORE the count runs

The rule I accept:
- Fix: this text, with its instant, in a MiScusi notebook entry (and the records PR) before the counting job starts. The count must not have been seen.
- Window: W = [2026-10-19T00:00:00Z, 2026-10-22T00:00:00Z), 3 whole UTC days of arena tape. It avoids the first tape day and ends before the 10-23T00Z clock.
- Tool: the h2ps-a1 rule module at its registration-candidate sha, in a counts-only mode with no ArenaEngine, no prices, no exits and no P&L. It reads only type, prog, side, trader, mint/pool, quote_mint, virtual_quote_reserves, slot and t_recv_ms. It applies the bot's exact entry predicate: a buy by a frozen wallet, PumpSwap, WSOL quote, V in [17.5e9, 17.7e9] on the row, first such signal per mint.
- Outputs: S = would-be attempts in W, per UTC day; distinct wallets with >= 1 signal; the top wallet's share; 4XWC's count; feed gap counts in W. Nothing else.
- Drop rule. h2ps-a1 is not registered if ANY of these holds:
  (i) S < 60 (below 20 per day, so 100 attempts would need more than 5 dates);
  (ii) any day of W has fewer than 10;
  (iii) fewer than 5 distinct wallets signal;
  (iv) 4XWC has fewer than 15 signals in W (5 per day; September was 18.6 per day across both venues). If the carrier of Book A's evidence is quiet on PumpSwap, this is a different book.
  - If W holds a hard gap or a pumpswap data_loss gap longer than 1 h in total, P0 is NOT_DECIDABLE and the book is not registered on it. Re-run on the next pre-declared 3-day window with a later clock.
- Record: the counts, job id and sha256 of the output go in the notebook. A dropped book never enters bots.json, so it does not count in multiplicity. It gets a library row "dropped at P0 (activity)".

## R4. g4v1-a1 (24 h holds) vs the 5% cap: OK WITH CONDITIONS under the current rule. Plausibly NOT_DECIDABLE. No narrowed exclusion now.

Code facts:
- A PumpSwap position is excluded by gaps of prog None, "pumpswap" or "all" in its decision-to-exit span (arena_paper._gap_hit, lines 1182-1190).
- pumpfun gaps hit bonding positions only. So the 22-80 per hour pumpfun slot_jump gaps do not touch G4.
- What does hit G4:
  - feed_start, process_restart and feed_restart (prog None);
  - the engine's own engine_silence (> 60 s) and engine_slot_jump (> 150 slots) between consecutive trade rows of ANY program (prog None, lines 932-939);
  - pumpswap data_loss gaps;
  - a restart of G4's own engine (engine_restart).

Arithmetic (scope rate 31.5 attempts per day):
- About 31 positions are open at any time.
- A read at >= 100 attempts over >= 5 dates covers about 160 attempts. 5% of that is about 8.
- ONE qualifying gap anywhere in [first counted decision, last covered exit], about 6-7 days, excludes about 31 trades: NOT_DECIDABLE.
- The evidence is 6 clean hours (10-19T10..16Z). That says almost nothing about a clean week. The SSD move would be a certain hard gap.

Conditions to register:
(a) Own process. The cohort host runs G4 alone, apart from h2ps-a1 and HV4.
(b) The SSD move and every planned feed, host or kernel change are finished before the clock. A declared change freeze then covers [clock, read boundary + 24 h + settle] for the tape writer, research-0 and the G4 process: no reboots, no feed redeploys, no restarts. Free disk margin is checked so the 25% / 20% floors cannot trip.
(c) Pre-clock gap health, outcome-blind and fixed before it runs:
  - Window: a 72 h window that starts after the SSD move.
  - Method: replay the tape through ArenaEngine's gap logic with no bot (counts only).
  - Required: zero hard gaps, zero pumpswap data_loss gaps, and zero engine_silence or engine_slot_jump events.
  - If this fails, do not register g4v1-a1. If the window cannot finish before 10-23T00Z, move the clock (see R6: 10-27T00Z also fixes the age problem).
(d) The read reports the full-span exclusion count and the number of decisions whose 24 h feature window (vol24, p_6) held a gap.

A narrowed rule is NOT OK for g4v1-a1. Examples: exclude only when a gap overlaps the entry or exit pricing windows; slot-level exclusion.
- The reasons:
  - It would be written knowing this book's exposure.
  - G4's TP +50% is path-dependent: a gap can hide the TP print, and the prints are missing, so the miss cannot be measured.
- What would be acceptable: a general DEC-031 amendment, for every book registered after it, with the full-span count and a full-span-excluded leg reported. This can be argued (PumpSwap rows carry absolute PRE-trade reserves, so the exit price itself is exact), but it is not this cohort's call.

Honest expectation: P(decidable) is unknown and may be low. A NOT_DECIDABLE book still counts in every multiplicity disclosure.

## R5. Shared host and frozen_sha semantics: ACCEPT, with conditions

frozen_sha is a commit, so files added in later commits cannot change what an earlier book runs. What matters is the process. Conditions:
(a) Each host process runs from a clean checkout at exactly its books' frozen_sha. One process hosts only books that share that sha. A later cohort is a new process on a new sha, never the old process restarted on a newer checkout.
  - The scorer already refuses an open row or engine start record whose code_head is not the registered frozen_sha, or is dirty (arena_score docstring, REFUSED). Keep that.
(b) Host and rule files are never edited after registration. A bug fix is a new module and a new bot_id. The records PR lists sha256 of every cohort file (host, rule modules, arena_declog, the wallet list, G4 parameters).
(c) Shared engine files (arena_feed, arena_paper, arena_score, arena_stack) stay under §1.4: any change is disclosed in every read it touches, with QP OK, including reads of earlier cohorts scored by a later scorer.
(d) Books in one process share fate. A crash makes every open position of every book in it engine_restart. That is why R4(a) applies.

## R6. Pre-clock tape history for state: OK for state only, from the arena tape only

- Warm-up and rolling windows from arena-tape rows before the clock are OK. This is the HV4 precedent (WARM_MIN_DEFAULT = 365). Pre-clock rows carry no counted decision.
- Conditions:
  - no order before clock_start_ms;
  - the as-of discipline (state.poll(until_ms = T - 1) for tick rules);
  - no parameter is chosen or tuned on pre-clock would-be trades (G4 is frozen 2026-10-08 and stays so).
- A persisted pool_create / complete index is OK only if:
  - it is built from the arena tape by deterministic code inside the frozen commit;
  - it is as-of strict (an entry counts only when its row's t_recv_ms < T);
  - its sha256 is logged at the clock and at each restart;
  - it can be rebuilt from the tape by the frozen code (determinism test).
- An index or age source from anywhere else is NOT OK: walk-2 blocks, Oracle archive, backfill, Helius or RPC history.
  - G4 ages 24-200 h at a 10-23 clock reach back to completes from about 10-14T16. [10-02T10, 10-16T01) is closed "from any source" (DEC-031 §3.6), and 10-16..10-18 is walk-2-hour data that the arena does not open.
- Effect: before about tape start + 200 h (about 10-26T08 if the tape started 10-18T00), a pool whose complete is not on the tape has unknown age and is not traded. This is a deterministic, outcome-blind narrowing: the early universe is younger pools only, and it is not the frozen G4 universe.
- Choose before registration:
  - clock 10-23T00Z with the truncation declared in the rule and stated in the read (G4's JUDGE evidence does not transfer to it); or
  - clock 2026-10-27T00:00Z with the full 24-200 h window (recommended, and it fits R4(c)).

## R7. Worst-in-slot: YES, as a report-only leg. Required in any canary proposal.

- The engine fills END-bound: the PRE-trade state of the first print at or after the landing slot + 1, which is the pool after the whole landing slot.
- The live tape has no tx_index, so within-slot order is receive order (arena_paper docstring).
- Worst-in-slot needs no order: buy at the highest-price state among the landing slot's states, sell at the lowest of the exit slot's. It is the honest bound for an order-blind tape.
- cap-impact-3 measured 1.6-1.7 pt of cost from it on a fast-hold book.
- Conditions:
  - A pinned report tool, sha in the records PR, adds the worst-in-slot leg plus the END-minus-worst gap for every cohort book, computed from ledger and tape. It is never deciding: the deciding cell is fixed by DEC-031 §2, and changing that is an amendment.
  - It is mandatory for event-triggered books (h2ps-a1: it enters into the slot cluster right behind a followed wallet's buy).
  - For G4 (hourly grid, quiet pools) it is expected to be small, but it is reported.
- Before any canary proposal, the proposal quotes the worst-in-slot mean and CI90 lower bound. If the tool does not exist by the read, the read says "worst-in-slot not measured", and no canary is proposed from that book until it is.
- Recommendation for the canary's own DEC (not a gate change): an event-triggered book whose worst-in-slot mean is <= 0 is not proposed.

## R8. Multiplicity: what every read states once cohort 2 is in bots.json

Every arena read (HV4 arms, H5 books, cohort books) states:
1. N = the number of arena books in bots.json with a clock before this read, stopped ones included. List them by bot_id and group: H5 2, HV4 9 arms, cohort 2 (h2ps-a1, g4v1-a1, later floorv2-a1), any other. Also state how many have been read, with their verdicts.
2. Cells: each book has one deciding decision, a conjunction of four sub-cells (1.9 s at the pinned stake x flat/pressure x strand rules sc/total). The report-only cells are listed but are not selection.
3. Upstream selection behind each book:
   - h2ps-a1: top 50 of eligible wallets on August data;
   - g4v1-a1: picked from the g4-survivors search; the number of cells searched is quoted from its REPORT/JUDGE;
   - HV4: 9 arms, self-improving;
   - cohort 2 itself: picked from the idea library on Aug/Sep evidence (scope §1). No arena outcome was used.
4. The books share one tape and the same days, so results are correlated. That does not reduce N, which is stated as the count.
5. A PASS is a screen: the best of N, in-sample for selection, winner's curse. It allows only DEC-031 §2 steps 1-3.
6. Confirmation: the 0.05/c threshold, with c = books that have entered confirmation, failed and NOT_DECIDABLE included (DEC-014 (b)).
7. Rent (R1): rent-charged and rent-0 legs side by side, which one decides, and why.
8. For H5 reads, add Amendment 1's refusal counts as already required.
9. The cohort's P0 outcomes (dropped books by name, "never registered"). They are not in N, but they are disclosed.

## Scope claims checked in code
- Confirmed:
  - REPORT_ONLY on any bonding row (arena_score docstring);
  - stakes limited to 0.25/0.5/1.0 (arena_paper.py:240);
  - binding rent 2,039,280 and E1 floor 384,022 per 0.05 SOL (arena_paper.py:85, 91, 686-690);
  - the scope's drag arithmetic, about 1.9 pp at 0.25, of which rent is 0.816 pp.
- Corrected:
  - "any logged data_loss gap" excludes a trade only for its own program, or for prog None/"all" gaps (arena_paper.py:1182-1190);
  - H2-PS must keep RULE.md's canonical V band.
- Not verified, quoted only: the JUDGE figures, 86.6 and 31.5 attempts per day, the 79% 4XWC share.

## R9. Designing an arena-only book (J3) from pre-clock arena-tape history: OK WITH CONDITIONS (added 2026-10-19 ~17:35Z)

Sources read for R9: DEC-030 §2 R1 (line 55) and §3; DEC-031 §3.3, §3.5, §3.6, §3.7 and §2; EXP-024 §3 seal (lines 132-140) and universe (line 164); EXP-025 §4 seal (lines 250-253) and its universe. The coordinator's figure "373 pump-coin-quoted pools in 9 h" is quoted, not verified.

(a) DEC-030 §2 R1 / DEC-031 §3.3 status.
- The arena tape covers walk-2 calendar hours. DEC-031 §3.3: "Arena results are walk-2-hour information under DEC-030 §2 R1 ('a book's design must not use them'). No book designed, tuned or selected with any arena result registers as a push-family book on walk 2."
- So a J3 designed, tuned or selected on arena-tape hours may be an ARENA book only. It can never be a push-family book, and neither can any variant or descendant of it, nor any book selected using its exploration. It takes no DEC-030 slot and does not change EXP-027's k.
- That is allowed. The arena lane exists for this (DEC-031 §0).
- Its forward read is a screen under DEC-031 §2 (steps 1-3 only). Stake above a canary needs the §2 confirmation on later attempts at 0.05/c.

(b) EXP-024 Look-2 seal. It is in force until EXP-024's end state, the first listed being 2026-11-14T00:00Z. It bars computing, opening or printing a trigger outcome, fill, exit, P&L, mean, CI or day sign on any October hour of a V-range pool in its first 360 s, from any source. DEC-030 §3 also places in the H5 domain any join of a synthetic class to an outcome and BOOST-timing outcomes.
- The PARENT is the priced pool, and it can be a V-range pool.
  - Required: a parent is skipped at decision time (no attempt) unless its canonical pool's first print s0 is at least 360 s before the decision, AND its last print before the decision is itself at or after s0 + 360 s. That keeps the guard spot, the entry, the exit and the nearby window outside the first 360 s.
  - s0 comes from the arena-tape pool index under R6. A pool whose create is not on the tape has s0 < tape start, so it qualifies only for decisions at or after tape start + 360 s.
  - The same filter applies in the exploration and in the frozen bot.
- CHILD pools are quoted in a pump coin, not WSOL, so they are not V-range pools by EXP-024's universe ("V0 in [17.5, 17.7] SOL" on the canonical pool of a complete graduation).
  - They may be used as trigger events only: their create, first trades, counts and timestamps.
  - No child fill, exit or P&L is ever computed or printed.
  - No result is split by an H5 trigger, BOOST timing or a synthetic class.
  - Any V-band test reads quote_mint == WSOL first, so a coincidental raw V in [17.5e9, 17.7e9] on a non-WSOL pool is never treated as V-range.
- A parent still on its bonding curve makes a book REPORT_ONLY (arena_score). So J3 buys only parents with a canonical WSOL PumpSwap pool, with V in the band on the row (R2 item 3). Pre-graduation parents are out of the deciding universe.

(c) EXP-025 C1-NF seal. It is in force until EXP-025's end state "Look 2 refused" is on main, not before the R8 run after 2026-10-26T12:00Z. It bars computing a C1-NF label, fill, exit, P&L, mean, CI or day sign for ANY October row, from any source.
- C1-NF's universe is canonical V-band graduations, decisions from grad + 10 min to grad + 24 h, which can overlap J3's parents. Overlap of pools is allowed. Reproducing C1-NF is not.
- J3 must not:
  - use C1's pass-A label or exit rule (pnl_E3), its decision grid, its features, its LAYA stage or its model (ARTIFACTS/c1nf_model/, C1 RULE.md);
  - open the outputs of #658 or #662.
- If a J3 cell would coincide with a C1-NF decision point and its exit rule, the cell is dropped from the exploration spec before anything is priced.
- Also in force: the D2/D3 domain seals, so nothing from [2026-10-02T10, 2026-10-16T01) and no tip tape (DEC-031 §3.6). The tape starts 10-18, so this holds if only the arena tape is used. EXP-027 isolation (§3.1): no r4-b asset.

(d) What pricing pre-clock arena-tape hours makes in-sample.
- Every arena-tape hour on which any J3 cell is priced or inspected, call it W_e, is in-sample for J3. That covers every variant, every cell, and any book designed or selected with the exploration.
- W_e never counts for any of them.
- Required:
  - The exploration spec is fixed in writing (a MiScusi notebook entry) before the first price: the hours of W_e (closed tape hours only), at most 12 declared cells, a kill rule (drop if any 1.9 s cell has mean <= 0, ex-top-3 <= 0 or ex-best-day <= 0), the arena stack (R1 as in force at the time; rent-charged if Amendment 2 has not merged), the R9(b) filter, the deciding cell 1.9 s at the stake to be pinned, and the arena engine's gap rules ON (gap_slots 150, gap_silence_ms 60000).
  - Pricing runs as a replay through ArenaEngine on the arena tape only. Bonding parents are not priced.
  - The chosen cell is frozen as a new bot_id. clock_start_ms is after W_e's last hour and after the last exit of any trade priced in W_e.
  - The read states: W_e, the cells tried (all of them, killed ones included), the chosen cell's W_e figures labelled IN-SAMPLE, and that the forward read is its first out-of-sample test. The W_e figures are never cited as evidence.
  - No retune on counted days (DEC-031 §2). Any J3 v2 built after the clock is a new bot_id. Its own W_e may include J3 v1's counted days, but then those days are in-sample for v2.
- One trap: other arena books' counted days (H5 from 10-18T06, HV4 from 10-21) may lie inside W_e. Pricing J3 there opens no seal of theirs, provided J3's exploration reads only the tape (never their ledgers or decision logs) and the R9(b) filter holds.

(e) Does an August-data count come first? YES. It is a cheap, outcome-blind fork, and it is required before any October pricing.
- Rule, fixed before it runs:
  - Count, structure only, on exploration-pool hours the ledger lists as open (for example explore-0814; never a sealed block such as fresh-0802, fresh-0808 or fresh-0828, and nothing in October), the J3 trigger events under the exact J3 trigger definition: child pools quoted in a pump coin whose parent has a canonical WSOL pool passing R9(b)'s age filter. Count per UTC date.
  - No price, no reserve beyond V/quote_mint, no outcome.
- Fork:
  - If Aug-Sep give at least 100 trigger events on at least 5 dates, J3 is designed on Aug-Sep. October arena hours before the clock are then NOT priced for J3. The October tape stays forward-only. That keeps the cleaner out-of-sample read, and it keeps a push-family route possible: the 373-pool October count only motivated the idea, and if J3 is ever proposed for walk 2 its EXP discloses that count for QP to rule on then.
  - If the count is below that, the mechanism does not exist in a usable form before October. Then the R9(d) route on pre-clock arena hours is allowed, with J3 arena-only forever.
- Record the count, job id and output sha256 in the notebook. Either branch counts its cells in multiplicity (R8).

Expectation: the scope rates J3 a weak prior ("Desk Q1 says net buying only"). In-sample selection on a few October days, plus winner's curse over up to 12 cells, means a W_e "pass" is likely to fade forward. Only the forward read counts.

## R9(f). The Aug-Sep J3 trigger count may join a pinned V map, structure only (added 2026-10-19 ~17:50Z)

Ruling: YES. The count may join a V map, structure only, using V for the band test and nothing else. No other V source is required. Conditions follow.

Provenance, read from origin/main text (the map files were not opened):
- `/data/mal/pumpswap-virtual/pool_v_0814.json`, sha256 `2506f7d2d8475e44ca70a8c536dbb7405930b1092edca331dbbe611236b4d2f8`. Job #196, 34,945 pools, read-only since 2026-10-06T00:30Z. Pinned in EXP-012-backcheck-0814:69, EXP-013:166 and EXP-014:174, and used by H2's RULE.md ("V map pool_v_0814 over pool_v_0909").
- V is constant per pool. DEC-016:228: across the 0814 -> 0909 refetch, all 34,892 non-null values were unchanged; a closed account reads null.
- So V is a per-pool structural constant, read from account state by RPC. It is not an outcome. No seal covers it: the EXP-024, EXP-025 and D2/D3 seals cover outcomes and pricing of October hours, and these are August pools.
- Known gap, in the repo's own words: the 0814 map "was built only from the pools of mints migrating in the views, but trades in the window also hit other pools".
  - EXP-012 backcheck: 29 pools missing, plus 1 closed account.
  - EXP-013 Am.7: 675 pools, 1.59% of triggered-mint prints, missing.
  - A complete map exists: `pool_v_0909.json` (job #232, every PumpSwap pool in 841 trade files over all research views, 226,073 pools, 321 null). EXP-016 and EXP-021 also pin the fixed-parser merge `pool_v_exp016.json`, sha256 `1f3e772d12cedbdb2dd860f619361fc0fdc88872fd5fa68639a11f91945162ec`.

Conditions:
1. Pin path@sha256 in the count's notebook amendment BEFORE the parent-filtered count runs.
   - Preferred: a complete map (`pool_v_0909.json`, its sha256 taken by sha256sum at pin time, or `pool_v_exp016.json` at the sha above).
   - `pool_v_0814.json` is acceptable only with the coverage rule in item 3.
   - That same pinned map is J3's only V source for the whole Aug-Sep design, pricing included. The count and the design must not use different V sources.
2. Structure only.
   - The join key is the parent's PumpSwap pool address. The only field read from the map is V.
   - V is used only for the parent-canonical test: quote_mint == WSOL (from the tape) AND V in [17.5e9, 17.7e9].
   - From the tape, the count reads only trigger events (the child's create and first buy) and the times the R9(b) age test needs (the parent pool's first print, and its last print before the trigger).
   - It reads no reserves, prices, exits or P&L.
   - If the parent pool's first print is before the view start, s0 is taken as earlier than the view start (same logic as on the arena tape).
3. Coverage. Report how many candidate parents are absent from the map or null, and their share of the 2,315 upper-bound events. Absent or null parents are NOT counted (conservative).
   - If they hold more than 5% of the upper-bound events, the count on that map is NOT_DECIDABLE. It is then re-run once on the complete map, pinned before the re-run. This fallback is declared now.
4. Same predicate as the bot. The count's parent predicate must equal the frozen J3 bot's predicate. The "mint ends in pump" pre-filter stays in the filtered count only if the bot uses it too. Otherwise drop it: the canonical WSOL V-band test is the criterion.
   - Disclose that the forward bot reads V from the tape row (virtual_quote_reserves) while the design reads the map. This is equivalent because V is constant per pool (DEC-016:228).
5. The threshold is unchanged. The upper bound (2,315 child pools over 15 UTC dates, 58-261 per date) is now in view. R9(e)'s fork (at least 100 trigger events on at least 5 dates) stays exactly as fixed. The upper-bound pass, its notebook amendment and the Haiku stop (J3-COUNT-0814.md, sha 33890df3) are disclosed in J3's record.
6. Design pricing. On the Aug-Sep branch, parents are priced with PRE-trade reserves plus the map's V at the arena stack. A parent print without V is not priced and is counted as a no-V exclusion. The exploration spec declares a no-V cap before any price (EXP-013's 1%-of-prints pre-pass is the precedent). Breaching the cap refuses the exploration; no outcome is computed.

A fresh RPC refetch of V is not needed (V is constant per pool). One is allowed only if it is pinned path@sha256 before use, structure only, and its credits are recorded.
