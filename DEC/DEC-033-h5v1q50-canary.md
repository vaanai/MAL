# DEC-033: Arena live canary, Q50 (`h5v1q50-boostfloor-a1`), copy of DEC-032 with Amendments 2 and 3 (DRAFT)

| Field | Value |
| --- | --- |
| **Status** | **DRAFT COPY of DEC-032 (with its Amendments 1, 2, 3) for `h5v1q50-boostfloor-a1`. NOT IN FORCE.** Opened as DEC-033 on 2026-10-27 (`date -u` 03:2xZ) by a worker; records only. Every `FILL:gate-read`, `FILL:owner` and `FILL:review` marker is open: it becomes a canary DEC only when the DEC-031 gate read PASSes on the deciding cell, the fields are filled, QP OKs the exact head, a reviewer verdict is on the PR, and the owner line is added by the owner's word. No book has cleared the promotion gate. The text of Amendments 2 and 3 below is copied as in the template (Am.2 is still marked DRAFT there). |
| **Decider** | Vaan (owner), for each live send, wallet funding and tier. No agent message, manager message or PR text is the owner's consent. |
| **Builds on** | [DEC-031](DEC-031-forward-paper-arena.md) §2 (screen, canary, confirmation), §4 and Amendments 2, 3 (item 6 canary prerequisites), 4 (item 1.1: a canary still halts on `pins_changed` and `program_changed`) and 6; [DEC-024](DEC-024-h5-live-canary.md) §4 to §8 and Amendment 3 (limits, halts, STOP/LIVE_OK/TIER files, T0/T1 ladder, wallet floor); [DEC-019](DEC-019-execution-probe.md) Amendment 2 and `ARTIFACTS/lab/feearm-landing-2026-10-23.md` (fee arm); `ARTIFACTS/lab/probe-final-2026-10-07.md` (wallet tie-out); `/data/mal/hunt-1008/push-1016/hunt25/LIVE-READINESS.md` (desk note, not a repo file) |
| **Does not amend** | The promotion gate and its thresholds. DEC-031's deciding cell, one read per `bot_id`, confirmation rule or taint list. DEC-024 and its running or stopped canaries. Any seal. The ledger. |

## 0. What this is and is not
- **Is:** a pre-written canary DEC so that, on the day a book passes a DEC-031 screen read, only book-specific values remain. Its purpose is an **execution measurement** at small limits: do live fills match the paper engine?
- **Is not:** gate evidence, a promotion, a profit claim, or a way around DEC-031 §2 step 4. A screen pass is the best of N in-sample for selection (the multiplicity block of the read gives N). Stake above the canary's limits needs the confirmation of section 7. No report of a canary says the book passed or is positive; every report labels it "unpromoted execution measurement".
- **Same-day path:** DEC-031 §2 allows a canary without a confirmation first (Amendment 3 item 6, "No confirmation first"). The only same-day-incompatible steps are the owner's OK, funding and Helm's install (LIVE-READINESS §2). This template pre-writes everything else.

## 1. Scope: which books may use it
A book may use this template only if all hold at the instant the owner is asked:
1. It is a registered arena `bot_id` (DEC-031 §1.2) and its one gate read (§2) returned **PASS** on its deciding cell (1.9 s leg at its pinned stake, flat and pressure, strand rules sc and total), with the multiplicity block (Amendment 3 item 3) attached. A NOT_DECIDABLE, FAIL, or an unread book never qualifies. A pre-gate "interim look" never qualifies.
2. DEC-031 Amendment 3 item 6 holds, each item quoted with its source: quant-proof's OK on the read on its exact files; total positive ex-best-day (`tools/arena_score.py` reports `ex_best_day_sol` and `not_proposed_for_canary`); the **worst-in-slot mean and its CI90 lower bound** quoted (if "worst-in-slot not measured", no proposal; for an event-triggered book a worst-in-slot mean at or below 0 is not proposed, per the cohort-2 ruling's recommendation); the mint fetch of the book's traded mints done (Amendment 2 item 6), with its close-blocked count quoted; and the sell builder of section 4.
3. The book's pools are outside every open seal, or inside a declared observation that covers live outcomes: DEC-031 §3 (EXP-024 Look-2 seal for H5 until 2026-11-14 and the v2 rule of §3.5; C1-NF seal; D2/D3/D4). quant-proof states the seal check in its OK. A live trade on a sealed pool is a seal exposure.
4. The book's `frozen_sha` is the code the executor runs. Any code or prompt change is a new `bot_id` and cannot use the canary.
5. The book's rule has a live executor path (H5 books: `tools/h5_executor.py` and the book-pinned subclass of #793; HV4, FLOOR and other rule books: `tools/arena_live_bridge.py` over `tools/probe_live.LiveExecutor`). No third path is built.
6. A parity tool computes section 5's metrics for the book before the first send (section 8, item 6).

A book that qualifies is not obliged to go live. Nobody chooses which passes are confirmed or counted (DEC-031 Amendment 3 item 2); that is separate from whether a canary is asked for.

## 2. The owner ask (pre-drafted; send through `miscusi_ask` only after section 8 items 1 to 5 are done)
**Paragraph (fill the FILL fields, change nothing else):**

> Vaan, book `h5v1q50-boostfloor-a1` passed the arena screen read at `FILL:gate-read` on its deciding cell (`0.30 SOL deciding stake (scorer STAKES 0.10/0.25/0.30 at a5f4137; confirm the pinned stake from the registry at PASS)` SOL stake, 1.9 s leg). All figures in this paragraph are IN-SAMPLE and selected: mean `FILL:gate-read` SOL per trade, CI90 lower bound `FILL:gate-read`, one-sided p `FILL:gate-read`, ex-best-day total `FILL:gate-read` SOL, worst-in-slot mean `FILL:gate-read` with CI90 lower bound `FILL:gate-read`, `FILL:gate-read` attempts on `FILL:gate-read` days. This is a screen, the best of `FILL:gate-read` books: if no book had an edge, the chance of at least one screen pass would be up to 1 - 0.95^N (40% at N = 10, 79% at N = 30). It is not a profit claim and it has not been confirmed; the confirmation is at least 100 more attempts on at least 5 more UTC dates with p at most 0.05/c (c is set when that read is made). I am asking for a small live **execution measurement**, not a bet on the edge. If you say yes: the wallet is funded with `FILL:gate-read (owner, 0.35 to 1.00 SOL)` SOL (your choice, 0.35 to 1.00 SOL; starting balance `FILL:gate-read (Helm/owner, before the first send)`), the stake is `0.05 SOL (50,000,000 lamports; S = min(book stake, 0.05); `C0_TIER.stake_lamports`)` SOL per trade, at most 2 open at a time, at most 30 buys a day. Trading stops by itself at a realized loss of `0.10` SOL in a day, and at `0.20` SOL of total loss counting open positions as lost; the worst case is `0.25` SOL lost, leaving the wallet at `funded - 0.25 SOL, never below 0.05 SOL (`wallet_floor_lamports`)` SOL or more. The fee per send is `buy 55,000, sell 55,000, escalated 150,000 lamports of priority plus the 5,000 base fee per send (tools/h5_executor.py:267-268 `H5_DEFAULT` @install_sha; `PRIORITY_DEFAULT` in tools/h5_arena_executor.py). Declared gap to the registered stack DEC-031-rev2 (505,000 per send): see section 11 item 6` (taken from the installed executor's config, not assumed). "Live matches paper" means, in plain words: over at least 100 finished live trades, each compared with a paper twin of the same decision priced at the same fees, live is not worse than paper by more than 1 percentage point of stake on average (we are 90% sure of that), and live orders land within 1.9 seconds at the median; until 100 trades exist we say "parity not shown" and take no step up. It halts by itself on a pump program redeploy, a feed gap, a break in the decision log, a missing or stale loss, balance, twin or parity reading, or when live fills drift from paper (section 5), checked on every landed buy and every closed trade. It runs for at most `at most 14 (base `max_days` from the first buy attempt; config end_ms clamped to the book's seal end 2026-11-06T00:00:00Z, `SEAL_END_MS`)` days. It does not raise the stake: any larger stake needs the confirmation and a new line from you. Do you approve (yes / no / change), and will you fund the wallet and tell Helm to set the files yourself?

**Checklist (all must be shown to the owner as done or open, in the ask or beside it):**
- [ ] DEC-031 screen read PASS, with multiplicity block, p, Holm rank, N, c
- [ ] quant-proof OK on the read, exact files and head sha: `FILL:gate-read`
- [ ] ex-best-day total > 0: `FILL:gate-read`; worst-in-slot mean and CI90 lo: `FILL:gate-read`, `FILL:gate-read`
- [ ] seal check stated by quant-proof: `FILL:gate-read (quant-proof's statement; draft in section 11 item 9)`
- [ ] mint fetch done: `FILL:gate-read (job id, mints, close-blocked count, credits)` (job id, close-blocked count of mints, credits)
- [ ] sell builder pinned (section 4), tx simulate output attached: `FILL:gate-read (tools/arena_sell_simulate.py output on one recorded Token mint AND one recorded Token-2022 mint, showing the sell closes the base ATA; blocker 4)`
- [ ] this copy merged as its own DEC (QP OK + reviewer verdict on head `POSTX:head_sha`)
- [ ] executor/bridge installed DRY by Helm at `POSTX:install_sha (a main merge sha that contains #836 (live entry), #838 (install pack) and the reviewed FEED_NOTE_MERGED flip; every path:line below is cited at it)`; dry run of at least 1 h with 0 build errors (`POSTX:dryrun_job (Helm's 1 h dry run, HELM-SHEET section D)`) and a md5 decision-equivalence replay proof against the frozen decision log (`#1412 (Q50; rolling-sps input #1409) EQUAL (scope: section 11 item 7) plus the re-run at the install sha: POSTX:md5_proof`)
- [ ] parity tool (`tools/arena_twin_writer.py sha256 1e9cb00e08ec69df269b19b9774d02feed7d3e91a61c7f727b7abbdc5fe560bb (`TWIN_WRITER_SHA256`), run by hunt29/p1/jobs/twin-writer-loop.sh sha256 bc22d2dced68af734f29d811f7c9642bddb7c1bc4096d946ff70c17435e87812 (GIT_REF re-pinned at install); parity `tools/arena_live_parity.py` @install_sha`) and tie-out tool (`tools/arena_tieout.py and tools/arena_tieout_fetch.py @install_sha, run by hunt29/p1/jobs/tieout-daily.sh sha256 b6e4c8e4db0ed955bdf909e03be4228ea8da01c705afcbafbee82fbaa032a048`) built and run dry (section 5, 6); alerts go to the Discord webhook of the watchdog (`FILL:alert_target`, section 9a)
- [ ] paper twin repriced at the canary stake with the executor's actual per-send fees (section 4), leg and E1 treatment as pinned in section 5 (`nofail, 1.9 s`, `E1 excluded decides`)
- [ ] funding amount and wallet address (public) named (`POSTX:wallet_address (Helm's fresh canary wallet, public key only)`); withdraw address kept by `Helm`
- [ ] end instant `POSTX:end_ms` set in code/config; C0 constants at `tools/h5_arena_executor.py `C0_TIER` and `DEC032_C0` @install_sha (stake 0.05 SOL, max open 2, 30 buys a day, daily stop 0.10 SOL, total stop 0.20 SOL); a live start refuses if any is looser (`c0_guard_problem`)`

**Owner answer** is recorded as a dated line in the copy, verbatim, with the MiScusi question and notebook ids, in the form used by DEC-024 (`OWNER_CANARY_CONFIRMED: <date> (owner, in session, asked by <manager>). Question: "...". Answer: "..." Notebook: <id>`). Nobody fills that line except from the owner's own message. The owner's OK covers the wallet, funding, `LIVE_OK` and `TIER`; each stays a separate act (Helm creates `LIVE_OK` and `TIER` as root; the executor cannot).

## 3. Limits (code constants; config may only lower them)
`S` is the canary stake. S = min(`0.30 SOL deciding stake (scorer STAKES 0.10/0.25/0.30 at a5f4137; confirm the pinned stake from the registry at PASS)`, 0.05 SOL). If the book's deciding stake is above S, the canary measures execution at S only: impact at the deciding stake is NOT tested, the paper twin is repriced at S with the engine's own impact (capacity-first lesson), and any step up re-prices at the new stake.

| Tier | C0 canary (this DEC) | C1 (needs section 7 and an owner line; not authorized here) |
| --- | --- | --- |
| Stake per entry | S, at most **0.05 SOL** | at most **0.10 SOL** |
| Max open | **2** | 3 |
| Buy attempts per UTC day (guard reverts count) | **30** | 40 |
| Daily stop (realized loss in a UTC day; no new buys until 00:00Z) | **0.10 SOL** | 0.20 SOL |
| Total stop (see below) | **0.20 SOL** | 0.45 SOL |
| Wallet needed | funded 0.35 to 1.00 SOL | funded 1.00 SOL |
| Wallet floor | funded minus 0.25, never below 0.05 SOL | funded minus 0.55 |
| Duration | at most 14 days from the first send (code constant, explicit `end_ms`); extension is the owner's decision | owner's line |

- **Total stop includes open exposure.** committed loss = realized loss since the first send (floored at 0) + every open or pending position's stake at zero + its buy fee + rent reserve + reserved sell fees + every in-flight buy's worst case. No new buy is sent unless committed loss plus that buy's worst case is at most the total stop (pre-send check, the feearm cap pattern of DEC-019 Am.2). Because in-flight buys are inside committed loss, committed loss cannot pass 0.20 SOL at C0. This is stricter than DEC-024 §4, whose stops counted closed trades only. The total stop does not reset on restart and ends the canary until the owner restarts it.
- **Wallet floor is the binding bound, and the worst case is derived from it.** floor = max(0.05 SOL, funded - 0.25 SOL). The maximum worst case is therefore funded - floor = 0.25 SOL at every funding of 0.30 SOL or more: 0.20 SOL committed-loss cap plus 0.05 SOL of slack for what the reserve cannot see (escalated resend fees, a failed-sell fee, dust). A buy is refused unless balance minus open stakes at zero minus this stake, fees, rent and the sell reserve is at least the floor (DEC-024 §4, DEC-020 §3 style; the balance has already paid for open stakes, counting them again is deliberate). Both the committed-loss check and the floor check must pass, so the two cannot disagree: at the 0.35 SOL minimum funding the floor is 0.10 SOL and the worst case 0.25 SOL. `0.25` is 0.25 and `funded - 0.25 SOL, never below 0.05 SOL (`wallet_floor_lamports`)` is funded - 0.25 (not below 0.05).
- **Fail closed.** No buy is sent if any of these is missing, unreadable or stale (older than 60 s, or older than the last landed tx): the committed-loss state file, the balance read, the paper twin for the decision row, the parity state, the `STOP`/`LIVE_OK`/`TIER` reads, or the decision-log chain check. Exits of open positions continue. Each such refusal is logged with its reason and counted.
- **Fixed cost vs stake.** At S = 0.05 SOL, two sends at 505,000 lamports are 2.0% of the stake, at 160,000 about 0.64%; the E1 floor adds 384,022 lamports per 0.05 SOL. A canary measures execution, not edge, and the report says so.
- **Paper twin.** Every live entry has a paper twin priced by `tools/arena_paper.py` from the same decision row, at stake S and with the executor's actual per-send fees, escalated resends included as sent (section 4). The twin is the comparator for section 5, priced as pinned there. Where the live sell path does not close the ATA in the sell transaction, the rent-charged leg is the twin (DEC-031 Amendment 2 item 5).
- **Venue and universe:** exactly the book's registered universe, nothing else. Canonical pools only (quote WSOL, V in [17.5e9, 17.7e9]) where Amendment 2 item 6 applies. Token-2022 extensions outside {metadataPointer, tokenMetadata} or any freeze authority: pre-send refusal (strand class = total loss).
- **Sell:** `min_out` at 0.85 of the quote; after 2 reverts or a time bound, resend at 0.65 with higher priority; never `min_out` 0 (DEC-024 §4). Time-stop and stop-loss backstops run with a warm sell plan (bridge S2); the exit poll and sell build are the fast ones. Residual gap-through below the stop is not fixable by polling and is reported, not hidden.
- **Buy guard:** on-chain `min_out` at the decision price divided by 1.15; signal age at most 5 s.
- **Files:** `STOP` (no new buys, exits continue), `HALT` (freeze), `LIVE_OK` and `TIER` root-owned. Live requires `LIVE_OK` present and a valid `TIER` (**T1 here, at `/etc/mal-h5-arena/TIER`**, plus `/etc/mal-h5-arena/LIVE_OK` naming the bot_id; section 11 item 1). Missing, unsafe or unknown files mean DRY or T0. The wallet-wide `/var/lib/mal-live/STOP` stays the kill switch; the manager takes no STOP, HALT or unit action (Helm does).

## 4. Fee tier and sell builder
- **The fee is a FILL taken from the installed executor's config, never assumed.** `buy 55,000, sell 55,000, escalated 150,000 lamports of priority plus the 5,000 base fee per send (tools/h5_executor.py:267-268 `H5_DEFAULT` @install_sha; `PRIORITY_DEFAULT` in tools/h5_arena_executor.py). Declared gap to the registered stack DEC-031-rev2 (505,000 per send): see section 11 item 6` = buy, sell and escalated priority in lamports, plus the 5,000 base fee, each cited as path:line@install_sha. Today they differ by path:
  - `tools/h5_executor.py:267-268` (origin/main): buy 55,000, sell 55,000, escalated 150,000 (H5 books and the #793 subclass, which does not override them);
  - `tools/probe_live.py` feearm and earlier profiles (the bridge's `LiveExecutor`): 500,000, and the feearm profile refuses any other priority (`ARTIFACTS/lab/feearm-landing-2026-10-23.md`: "the live executor's priority (still 500,000)").
- **The twin is priced at exactly those per-send fees, escalated resends as actually sent.** Pricing it at the registered stack's 505,000 per send while live pays 60,000 would add about +178 bps per round trip at S = 0.05 SOL ((505,000 - 60,000) x 2 / 0.05 SOL) to d_i and hide P3 and P4. Any gap between the executor's fees and the book's registered stack is declared in the DEC copy before the first send, with the bps it moves; the paper read and the confirmation stay at the registered stack (never changed by the canary).
- **155,000 priority (160,000 per send) for new arena books** belongs to DEC-031 Amendment 8, now PR #809 (`claude/arena-send-160k`, draft; `stack_config_v2`). It is not merged at writing and changes no registered book's stack. A canary uses a priority other than the executor's installed one only if that is a reviewed executor config change, named in `buy 55,000, sell 55,000, escalated 150,000 lamports of priority plus the 5,000 base fee per send (tools/h5_executor.py:267-268 `H5_DEFAULT` @install_sha; `PRIORITY_DEFAULT` in tools/h5_arena_executor.py). Declared gap to the registered stack DEC-031-rev2 (505,000 per send): see section 11 item 6`, and the owner's line names it. The fee-arm result (Test A and Test B PASS) is a cost input only.
- **Sell and close the base ATA in one transaction** under the mint's own token program (DEC-031 Amendment 2 item 5, Amendment 3 item 6). The builder is pinned as `tools/h5_executor.py `H5Executor._arm_sell`: file sha256 bc4c957c11caa536e107fc9b944fc33d13397039fcaf74cba45c8c2c603d5c17, symbol 3c4bf3f8a748afcbc2a991a9126988f023acf9692c20d8009a69f3d37764effb; tools/probe_live.py `close_token_account` 93119b2ee...; tools/pumpswap_tx.py `sell_instructions` ad40960e... (`SELL_BUILDER_PIN`, rechecked at every live start by `sell_pin_problem`)`; today `tools/h5_executor.py` (lines 2524-2525 at the time of Amendment 2) and `tools/probe_live.py` (759-760) do. `tools/pumpswap_tx.sell_instructions` alone closes only the WSOL ATA and is not allowed alone. A tx simulate output for a sell of a recorded mint is attached to the owner ask. If the close cannot run the whole sell reverts and the position sticks (strand behaviour).

## 5. Halts and the parity metric
Any halt creates `STOP` (no new buys); open positions exit on their rules unless the manager asks Helm for `HALT`. A halt is never followed by a retune of the book. A hard halt is cleared only by the manager's written review and, for items marked (O), the owner.

**Structure halts (hard):**
1. **A3 / redeploy (O).** Any A3 halt flag, `pins_changed` (any item) or `program_changed` of the pump or PumpSwap programs, fees program or any pinned account. A canary does not use DEC-031 Amendment 4's automatic upgrade check (its item 1.1 excludes canaries). Resume only after the manager's upgrade review (the DEC-024 13 October resume record is the pattern) and the owner's line.
2. **Feed gap.** Any gap record in the book's feed (disconnect, resubscribe, `gap_silence_ms` 60,000 exceeded, a PumpSwap `data_loss`): new buys stop at once and restart only after the feed has been gap-free for 10 minutes and the manager has seen the gap record. A gap during an open position marks that live trade `gap`, reported separately; it is excluded from parity statistics by the same rule that excludes it from the paper read (DEC-031 §1.4), and counted. *(Read through DEC-032 Amendment 5 (DRAFT), for this copy only and only once Amendment 5's conditions (a) to (c) hold, the install sha containing PR #1085. Under it, a `socket_reconnect` gap record with `flags_pools` the literal false holds nothing and marks no trip. Every other gap record stops new buys at once; buys restart after 60 s free of such records (`GAP_HOLD_MS` 60,000; config may lengthen it to 600,000), and every open trip is marked `gap`. The clause "and the manager has seen the gap record" is replaced by the ledger and the daily review (Amendment 5 item 4). Until those conditions hold, and on any install sha without #1085, the words above apply as written.)*
3. **Decision-log integrity.** A hash-chain break, a `frozen_sha` mismatch (the executor's `start` row repeats the pin), or a config key differing from the registration pin: hard latch (bridge `chain_break`, `frozen_sha_mismatch`).
4. **Stuck position.** A position not closed by its exit instant + 600 s: no new buys, alert, retries back off 30 s to 10 min, abandoned after 10 and Helm runs the root sell-and-close tool. Strand class = total loss in the report.
5. **Seal / universe.** An entry outside the registered universe, a close-blocked mint reaching the sender, or a pool inside a closed seal: hard halt and a record. A seal breach ends the canary.
6. **Stops.** Daily stop, total stop, wallet floor, absolute `end_ms`. The total stop and floor need the owner (O) to restart.
7. **Wallet tie-out.** Section 6 residual not zero on a closed day.

**Parity metric (live vs paper, measurable).** Pinned before the first send, no choice after data:
- **Twin comparator:** `tools/arena_paper.py` at the book's registered config, the **nofail** leg (a live trip that landed on both sides is compared with the engine's landed outcome, not the 15% blend), the **1.9 s latency leg** (the deciding leg of DEC-031 §2), priced at the executor's actual per-send fees (section 4). **E1 floor treatment: the twin is priced WITHOUT the E1 floor (about +77 bps of cost at 0.05 SOL) and decides; the twin with E1 is reported beside it.** Excluding E1 is the stricter comparison (E1 is a haircut that would mask live drift). `nofail, 1.9 s` = "nofail, 1.9 s" and `E1 excluded decides` = "E1 excluded decides" and a copy does not change them after its first send.
- A live round trip is **eligible** if it has a twin, is not marked `gap`, and its exit has landed. Every non-eligible trip is counted by reason: `no_twin` (including a failed twin price) and `gap`. If either share exceeds **5%** of live trips, parity is NOT_DECIDABLE and a soft halt follows (as DEC-031 §1.4's 5% cap). *(Small-sample rule: Amendment 1 item 1.)* A live buy with no frozen decision row is a hard latch (`out_of_rule_entry`, DEC-024). Which paper decisions go live is set only by the caps in decision order; every skipped decision is logged with its reason.
- **d_i** (bps of stake) = (live net wallet delta of the trip from chain: sells received minus buys paid minus all fees, rent refunded counted, minus the twin P&L) divided by `stake_i`, times 10,000.
- **g_entry_i** (bps) = live buy average price over the twin's entry price, minus 1, times 10,000. Positive = live paid more. **g_exit_i** likewise for the sell (negative = live received less).
- **Landing-fail excess** = live share of buy sends that failed or expired minus the larger of the two paper models' mean modelled failure share over the same attempts (flat 15% and pressure refit mean p, where p is the pressure model's per-attempt failure probability `p_fail_press` taken from `arena_stack.fail_legs`). *(Wording per Amendment 1 item 2.)*
- **No window reset.** Aggregate statistics run over all eligible trips since the first send. Clearing a soft halt, a restart or a tier change resets no window. Rolling statistics (P1, P2, P3, P5) are over the last N and never skip a bad stretch.
- **Cadence.** P1 to P6 are evaluated by the executor on every landed buy and every closed trip, not daily; the daily job (section 6) re-computes them as a check.

Thresholds. Provenance: P4 and P5 are DEC-024 §5 items 4 and 3 (divergence CI90 upper bound below -1 pp after 100 fills; landing p50 above 3.0 s over the first 20 landed buys, then rolling 20). P1 and P2 are LIVE-READINESS §3 (entry gap above 200 bps after 10 fills; landing-fail excess above 10 pp after 30). P3, P6 and P7 are this template's proposals. A copy may only tighten them.

| Id | Condition | Action |
| --- | --- | --- |
| P1 | mean g_entry over the first 10 eligible fills, then rolling 20, above **200 bps** | soft halt (`STOP`), manager review |
| P2 | landing-fail excess above **10 pp** after at least 30 buy attempts, rolling 30 | soft halt |
| P3 | mean d_i below **-300 bps** after at least 20 eligible trips, rolling 20 | soft halt |
| P4 | after at least 100 eligible trips, CI90 upper bound of mean d_i (1,000 bootstrap draws, seed 1, 95th percentile) below **-100 bps** | hard halt (O): live is confidently worse than paper by more than 1 pp |
| P5 | landing p50 (decision to landed) above **3.0 s** over the first 20 landed buys, then rolling 20; or sells landing after their exit bound in more than 5% of exits | soft halt |
| P6 | mean g_exit worse than **-300 bps** over the first 10 stop-loss exits | soft halt, report gap-through share |
| P7 | `no_twin` or `gap` share of live trips above **5%**, or any missing, unreadable or stale parity/loss/balance/twin state (section 3, fail closed) | soft halt; no buy until restored (small-sample rule: Amendment 1 item 1) |

P3 and P4 are halts, not the match test: they reject only a confidently worse live. The match test (below) is stricter on purpose.

**"Live fills match paper" (DEC-031 §2 step 4; the only meaning used for a step up and at the confirmation read).** It holds only if ALL are true over all eligible trips since the first send: at least **100** eligible trips; the **CI90 lower bound of mean d_i** (1,000 draws, seed 1, 5th percentile) is at or above **-100 bps**; landing p50 (decision to landed) is at most **1.9 s** (the deciding leg); P7 is off; no hard halt is unresolved. Otherwise the report says **"parity not shown"** and no step is taken. A soft halt may be cleared by the manager after a written review that names the cause; two soft halts of the same id end the canary until the owner decides. Parity is required in addition to the confirmation and never replaces it. The parity tool (`arena_live_parity`, LIVE-READINESS S5) is wired into the executor's gate (soft halts need no human to act first). Until it exists no canary starts.

## 6. Daily reconciliation (wallet tie-out)
Every UTC day (a MiScusi scheduled job on the host, never a session cron) and again at every halt, end of canary and before every tier step, using the pattern of `ARTIFACTS/lab/probe-final-2026-10-07.md` and `tools/probe_rent_audit.py` (public address only, finalized commitment, no key file opened, the RPC key never printed):
0. Baseline: the starting balance `POSTX:start_balance (Helm/owner, before the first send)` and the funding tx are recorded by the owner/Helm before the first send; each later day starts from the previous signed-off balance.
1. List every signature of the canary wallet; fetch each tx's wallet delta; sum.
2. Assert `balance_now - sum(all per-tx deltas) == 0` to the lamport, and the first tx's pre-balance equals the previously reconciled balance (0 for a fresh wallet). The tool exits non-zero otherwise.
3. Match every ledger send signature to a chain signature (0 missing in either direction, including sends whose ledger row says dropped or expired).
4. Split the deltas into: deposits and withdrawals (only the owner's and Helm's, named), buys, sells, base and priority fees, ATA rent paid and refunded, pool fees as implied, failed-tx fees. Report open ATAs (`getTokenAccountsByOwner`, Token and Token-2022): any non-zero token account with no open ledger position is a stranded position and a P4-class alert.
5. Report ledger realized P&L against the chain sum for closed trips; a difference above one base fee (5,000 lamports) with no explanation, a missing signature, or an unexplained inflow or outflow is a hard halt (O) and the day is not signed off. Unsolicited tiny inflows (address-poisoning dust) are listed, not netted away silently.
6. Write the result in a lab note and a MiScusi notebook entry, and copy numbers into `data/console.json` unrounded.
The reconciliation is an execution report; it contains no sentence that says the book made money.

## 7. Confirmation track and the size ladder
- **Confirmation** is exactly DEC-031 §2 step 4 and Amendment 3 item 2 and runs automatically after the screen pass; the canary neither starts nor shortens it. The same `bot_id`, unchanged, must clear the same gate on its deciding cell over attempts decided **after the screen read's instant**: at least **100 attempts on at least 5 further UTC dates**, read once at the outcome-blind instant, and the one-sided bootstrap p (10,000 draws, seed 1, the larger p of the two fail models) at most **0.05/c**, c the number of arena books that have entered a confirmation by that read (failed and NOT_DECIDABLE included; a bank-pass book per Amendment 3 item 7). The 5 dates are the binding limit.
- **The canary may run inside the confirmation window** (Amendment 3 item 6, "In parallel"). Live results never replace it and are not added to the paper read. The live canary's own days are not a second confirmation.
- **Independence.** Nothing the canary shows may stop, delay, re-scope or change the confirmation read; a canary halt stops the canary only. The canary install never restarts or touches the paper book's process (DEC-031 Amendment 6 item 4; one process per `frozen_sha`). The confirmation report carries a concurrent-observation disclosure: the wallet is public and watched in real time, so results can influence later choices (an owner step) but never the formal read. Every report, notebook entry and Console entry about the canary carries the label "unpromoted execution measurement".
- **Step C0 to C1** requires all of: the confirmation read PASS at p at most 0.05/c; section 5 **"live fills match paper" shown** at that instant (at least 100 eligible trips, CI90 lower bound of mean d_i at or above -100 bps, landing p50 at most 1.9 s, P7 off), otherwise "parity not shown" and no step; no unresolved halt; at least 5 daily tie-outs signed off with no residual; section 6 residual 0; the book's impact re-priced at C1's stake with the engine's own impact (a capacity-first screen: trades per day times stake times net edge, impact at size) over the confirmation window, reported, and still positive on the gate; this re-price is a check on a step already earned, never used to pick a book, cell or stake; the sell builder unchanged; the owner's own dated line naming the stake, open cap and stops; and the wallet funded to 1.00 SOL by the owner. Then Helm writes `TIER` as root.
- **Beyond C1** (stake above 0.10 SOL) is not defined here. It needs a new amendment, the owner's line, more capital from the owner after confidence, and a re-price at the new stake. Nothing in this template authorizes it.
- A confirmation FAIL or NOT_DECIDABLE ends the book's canary at the next 00:00Z; the owner may extend for execution measurement only.

## 8. Order of acts (who, and what the template cannot do)
1. Manager and scorer: read PASS, multiplicity block, ex-best-day, worst-in-slot. (1 to 2 h)
2. quant-proof: the read, seals, taints, c; forced verdict as a plain agent. (2 to 4 h)
3. Builder: copy this template to the numbered DEC, fill section 9, open the PR; QP OK on head and reviewer verdict on the PR (workers do not merge).
4. Builder: mint fetch of traded mints, sell-builder pin and simulate output.
5. Manager: the owner ask of section 2 through `miscusi_ask`.
6. Parity and tie-out tools, executor/bridge installed DRY, 1 h dry run, md5 replay: these are build items that must already exist (see PR body of the template PR for their state).
7. Owner: yes/no, wallet funding, own instruction to Helm. Helm: pinned root install from the named sha, manifest, key via credential, drop-ins, watchdog; `/etc/mal-h5-arena/TIER` **T1**, `/etc/mal-h5/LIVE_OK` and `/etc/mal-h5-arena/LIVE_OK` (content: the bot_id) (section 11 item 1).
8. Manager: reviews dry output, sets `end_ms`, gives go. The manager restarts nothing on a host unit.
Only steps 1 to 5 and 8 are the lab's. Step 7 needs the owner and Helm.

## 9. BOOK-SPECIFIC FILL fields (the only values a copy may set)
| Field | Meaning |
| --- | --- |
| `FILL:dec_number` | new DEC id of the copy |
| `FILL:bot_id`, `FILL:frozen_sha`, `FILL:clock_start_ms` | the book (from `bots.json` and its notebook copy) |
| `FILL:read_instant`, `FILL:n`, `FILL:d` | screen read instant, attempts, UTC dates |
| `FILL:mean`, `FILL:ci_lo`, `FILL:p`, `FILL:ex_best_day`, `FILL:wis_mean`, `FILL:wis_lo` | in-sample read figures, labelled in-sample |
| `FILL:N`, `FILL:holm` | multiplicity block at the screen read. c is not a fill: it is computed at the confirmation read by DEC-031 §2 step 4 |
| `FILL:qp_comment`, `FILL:reviewer_comment`, `FILL:head_sha` | review ids and head sha of the DEC copy's PR |
| `FILL:install_sha` | the sha Helm installs; every executor path:line is cited at it |
| `FILL:seal_status` | which seals apply and the declared observation covering live outcomes |
| `FILL:book_stake` | the stake pinned in `bots.json` (S = min of it and 0.05 SOL) |
| `FILL:canary_stake` | S |
| `FILL:universe` | the rule's pool universe and the path:line@frozen_sha of the check (V band, WSOL) |
| `FILL:venue_exit_rule` | exit rule(s) and bounds (timer, stop, backstops) |
| `FILL:executor` | `h5_executor` profile or `arena_live_bridge`, config sha256, manifest |
| `FILL:c0_constants` | path:line@install_sha of the C0 limits (new code: h5_executor's T0 tier is 0.02 stake with a closed-only total stop) |
| `FILL:builder_path@sha256` | pinned sell+close-ATA builder |
| `FILL:simulate_ref` | reference to the sell simulate output |
| `FILL:fee` | executor buy, sell and escalated priority, path:line@install_sha (section 4) and the declared gap to the registered stack in bps |
| `FILL:twin_tool` | the paper twin and parity tool, path@sha256 |
| `FILL:twin_leg`, `FILL:twin_e1` | pinned "nofail, 1.9 s" and "E1 excluded decides" (section 5); changed only by amendment |
| `FILL:fund`, `FILL:start_balance`, `FILL:floor`, `FILL:worst_case` | funding (0.35 to 1.00 SOL), starting balance baseline, floor (funded - 0.25, not below 0.05), worst case (0.25) |
| `FILL:wallet_address`, `FILL:withdraw_holder` | public address; who holds withdraw (Helm) |
| `FILL:daily_stop`, `FILL:total_stop` | default 0.10 and 0.20; config may only lower |
| `FILL:days`, `FILL:end_ms` | at most 14 days; explicit end |
| `FILL:feed_gap_source` | the book's gap log (arena tape gap records, or `h5_shadow` `gap` records) |
| `FILL:a3_source` | the A3 monitor or pin source for pump program changes |
| `FILL:mint_fetch` | job id, counts, close-blocked share, credits used |
| `FILL:tie_out_tool`, `FILL:tie_out_job` | tie-out tool path@sha256; scheduled reconciliation job id |
| `FILL:dryrun_job`, `FILL:md5_proof` | dry-run job id; md5 decision-equivalence replay proof reference |
| `FILL:alert_target` | watchdog/alert target for stops, halts and the wallet line |
| `FILL:owner_line` | the owner's verbatim answer, recorded only from the owner's own message |
| `FILL:owner_deposit_addresses` | POSTX:owner_deposit_addresses (public owner addresses, pinned after the owner names them; Am.2 section 9) |

Everything else in this template stays as written. A copy that changes a limit upward, a halt threshold looser, or the fee rule is not a fill and needs its own amendment text and review.

## 9a. Fill values of this copy
| Field | Value |
| --- | --- |
| `FILL:dec_number` | DEC-033 (next free number at origin/main d8325fa7, 2026-10-27; no DEC-033 branch or PR found) |
| `FILL:bot_id` | h5v1q50-boostfloor-a1 |
| `FILL:frozen_sha` | a5f413704eb120c8f529101a6186e3a95de7f83e |
| `FILL:clock_start_ms` | 1792886400000 (2026-10-25T00:00:00Z) |
| `FILL:read_instant` | FILL:gate-read |
| `FILL:n` | FILL:gate-read |
| `FILL:d` | FILL:gate-read |
| `FILL:mean` | FILL:gate-read |
| `FILL:ci_lo` | FILL:gate-read |
| `FILL:p` | FILL:gate-read |
| `FILL:ex_best_day` | FILL:gate-read |
| `FILL:wis_mean` | FILL:gate-read |
| `FILL:wis_lo` | FILL:gate-read |
| `FILL:N` | FILL:gate-read |
| `FILL:holm` | FILL:gate-read |
| `FILL:qp_comment` | POSTX:qp_comment |
| `FILL:reviewer_comment` | POSTX:reviewer_comment |
| `FILL:head_sha` | POSTX:head_sha |
| `FILL:install_sha` | POSTX:install_sha (the sha X that the Q50 X-time pack installs; it must contain this merged DEC/DEC-033 file, because the executor checks the canary record in DEC/ of the INSTALLED tree (`tools/h5_arena_executor.py:315`, `canary_record_refusal`) and 0526d5cd has no DEC-033; it must also contain #851 and #852. Whether X also contains #972 (Am.3 item 5b) is a manager choice: Am.3 allows §5 item 1 without it. Every path:line below is cited at X) |
| `FILL:seal_status` | see section 11 item 9 (corrected text, QP-RULING-1025-q50-seal-live ruling 4 and C8); the checked-date field stays open until the read, and the install-sha field is `POSTX:install_sha` |
| `FILL:book_stake` | 0.30 SOL deciding stake (scorer STAKES 0.10/0.25/0.30 at a5f4137; confirm the pinned stake from the registry at PASS) |
| `FILL:canary_stake` | 0.05 SOL (50,000,000 lamports; S = min(book stake, 0.05); `C0_TIER.stake_lamports`) |
| `FILL:universe` | the frozen rule's pool universe at a5f4137 (Q* 50 SOL (Q50_RULE, compiled `q_star_lamports` 50e9)) INTERSECTED with the canonical band: quote WSOL and V in [17.5e9, 17.7e9] lamports (tools/h5_arena_gates.py `v_band_refusal`, `V_BAND`, @install_sha). See section 11 item 3 (V-band subset) |
| `FILL:venue_exit_rule` | H5-BOOSTFLOOR v1: timed sell at s0 + 330 s (send lead 0.5 s), late bound s0 + 335 s, hard deadline s0 + 400 s, sell ladder min_out 0.85 then 0.65 (tools/h5_executor.py exit_plan @install_sha); compiled exit pin `v1` (tools/h5_arena_executor.py BOOKS) |
| `FILL:executor` | `h5_executor` subclass `tools/h5_arena_executor.py` ArenaExecutor, book `h5v1q50-boostfloor-a1` (compiled BOOKS row; priority profile `default`), unit `mal-h5-arena-executor` with live drop-in, config `/etc/mal-h5-arena/live.json` sha256 POSTX:config_sha256, install manifest POSTX:manifest_sha256 |
| `FILL:c0_constants` | tools/h5_arena_executor.py `C0_TIER` and `DEC032_C0` @install_sha (stake 0.05 SOL, max open 2, 30 buys a day, daily stop 0.10 SOL, total stop 0.20 SOL); a live start refuses if any is looser (`c0_guard_problem`) |
| `FILL:builder_path@sha256` | tools/h5_executor.py `H5Executor._arm_sell`: file sha256 bc4c957c11caa536e107fc9b944fc33d13397039fcaf74cba45c8c2c603d5c17, symbol 3c4bf3f8a748afcbc2a991a9126988f023acf9692c20d8009a69f3d37764effb; tools/probe_live.py `close_token_account` 93119b2ee...; tools/pumpswap_tx.py `sell_instructions` ad40960e... (`SELL_BUILDER_PIN`, rechecked at every live start by `sell_pin_problem`) |
| `FILL:simulate_ref` | POSTX:simulate_ref (MiScusi job id, output dir and `q50-n4-summary.json` sha256 of `tools/q50_n4_rerun.py` at the merged sha: one Token and one Token-2022 mint with s0 in September 2026, `--priority default`, unsigned, sigVerify false, no keys; blocker 3) |
| `FILL:fee` | FILLED 2026-10-27 (records PR; the install sha X inside is `POSTX:install_sha`): buy 55,000, sell 55,000, escalated 150,000 lamports of priority plus the 5,000 base fee per send, cited at `tools/h5_executor.py:267-268@X` (X = `POSTX:install_sha`, the install sha) and `PRIORITY_DEFAULT` in `tools/h5_arena_executor.py`. Manager decision: C0 uses the compiled default (the HANDOFF "p155 pin" is stale for Q50). Declared gap to the registered DEC-031-rev2 stack (505,000 per send): section 11 item 6 |
| `FILL:twin_tool` | tools/arena_twin_writer.py sha256 1e9cb00e08ec69df269b19b9774d02feed7d3e91a61c7f727b7abbdc5fe560bb (`TWIN_WRITER_SHA256`), run by hunt29/p1/jobs/twin-writer-loop.sh sha256 bc22d2dced68af734f29d811f7c9642bddb7c1bc4096d946ff70c17435e87812 (CURRENT sha256, GIT_REF 0526d5cd inside; re-set at X: POSTX:twin_script_sha256); parity `tools/arena_live_parity.py` @install_sha |
| `FILL:twin_leg` | nofail, 1.9 s |
| `FILL:twin_e1` | E1 excluded decides |
| `FILL:fund` | POSTX:fund (owner, 0.35 to 1.00 SOL) |
| `FILL:start_balance` | POSTX:start_balance (Helm/owner, before the first send) |
| `FILL:floor` | funded - 0.25 SOL, never below 0.05 SOL (`wallet_floor_lamports`) |
| `FILL:worst_case` | 0.25 |
| `FILL:wallet_address` | POSTX:wallet_address (Helm's fresh canary wallet, public key only) |
| `FILL:withdraw_holder` | Helm |
| `FILL:daily_stop` | 0.10 |
| `FILL:total_stop` | 0.20 |
| `FILL:days` | at most 14 (base `max_days` from the first buy attempt; config end_ms clamped to the book's seal end 2026-11-06T00:00:00Z, `SEAL_END_MS`) |
| `FILL:end_ms` | POSTX:end_ms |
| `FILL:feed_gap_source` | the h5_shadow `gap` records in the book's arena shadow dir (bound read-only at /srv/mal-h5-arena/feed). At an install sha containing PR #1085, with DEC-032 Amendment 5 in force: a data-loss gap record (`gap_loses_data`) holds buys 60 s (`GAP_HOLD_MS` 60,000) and marks open trips `gap`; a `socket_reconnect` record with `flags_pools` the literal false holds nothing and marks no trip. On any other install sha: any gap holds buys 10 min (`GAP_HOLD_MS` 600,000) |
| `FILL:a3_source` | mal-fast-0 A3 guard daily record /home/claude/data/structure-monitor/daily.jsonl (scripts/ops/a3-daily.sh loop job; bound read-only, config `a3_record_file`); stale after 36 h, future stamp, unreadable or any halt flag latches `a3_pins_changed` |
| `FILL:a3_guard_source` | per Am.3 section 6: `dirname(FILL:a3_source)/a3-applied.jsonl` on mal-fast-0; guard loop job and alert job ids with pins and time limits from the job list (the manager's job facts as read from `miscusi_job_status` on 2026-10-27 (`date -u` ~20Z), correcting Am.3 section 7: loop #1446 (pin 0ec7fae8 = tag a3-guard-pin-1024) started 2026-10-24T00:57:39Z, limit now extended to 14 d, ends 2026-11-07T00:57:39Z; successor #1447 queued `after` #1446, 13 d; alert #1540 runs to A3_ALERT_UNTIL 2026-11-06T17:53Z; dead-man #1319 limit now 14 d, ends about 2026-11-04T21:13Z (corrected 2026-10-27: the first figure, 11-04T05Z, was wrong; record the real limit from the job list), successor #1333 queued `after` it. Re-read and record the live values at X; the 48 h rule of Am.3 section 7 item 2 still applies): POSTX:a3_guard_source (re-check at X, checklist blocker 8) |
| `FILL:mint_fetch` | FILL:gate-read (job id, mints, close-blocked count, credits) |
| `FILL:tie_out_tool` | tools/arena_tieout.py and tools/arena_tieout_fetch.py @install_sha, run by hunt29/p1/jobs/tieout-daily.sh sha256 b6e4c8e4db0ed955bdf909e03be4228ea8da01c705afcbafbee82fbaa032a048 (CURRENT sha256, GIT_REF 0526d5cd inside; re-set at X: POSTX:tie_out_script_sha256) |
| `FILL:tie_out_job` | POSTX:tie_out_job |
| `FILL:dryrun_job` | POSTX:dryrun_job (Helm's 1 h dry run, HELM-SHEET section D) |
| `FILL:md5_proof` | job #1481 at install sha 0526d5cd: VERDICT EQUAL, Run A 137/137, Run B 101/101 (QP-RULING corrections 2026-10-25). Any other install sha needs its own EQUAL replay: POSTX:md5_proof (job at X, checklist blocker 5) |
| `FILL:alert_target` | Discord, via the watchdog `scripts/mal-fast/h5-arena-watch.py` run by `mal-h5-arena-watch.timer` on mal-fast-0 (every 5 min, `scripts/mal-fast/mal-h5-arena-watch.timer:7`). It posts when an alert is NEW, again every 6 h while it stays (`REPEAT_S`, h5-arena-watch.py:6,57) and RESOLVED when it clears (:246), to the webhook in `ARENA_WATCH_DISCORD_WEBHOOK` (:28,65), which must match a Discord webhook URL (:66,298); the value is held by Helm in `/etc/mal-h5-arena-watch/watch.env`, root 0600 in a root 0700 dir (`mal-h5-arena-watch.service:10-13`), is removed from the environment once read (:319) and is never printed or recorded here. Helm posts one `--test-message` when enabling the timer (:36,320-322; HELM-SHEET section G step 4), and the watchdog must be enabled before section F step 6 (HELM-SHEET section G). Record of the 10-24 install (HELM-REPLY-1024 section 5): the timer ran keyless every 5 min 06:10Z-07:05Z, `alerts=0 posted=0`, then was stopped and disabled at 07:08:37Z; `watch.env` and the unit files stay installed. That reply does not say the webhook value was set or the test message posted, so a webhook install confirmation is owed by Helm at X: `POSTX:webhook_install_confirmation` (HELM-ASK items owed at X). |
| `FILL:owner_line` | the OWNER_CANARY_STANDING_OK line of DEC-032 Amendment 1 item 3, quoted verbatim in section 10a; the owner's answer to the §2 notice, ids and any funding line: POSTX:owner_answer_ids |

## 11. Book-specific declarations (QP-RULING-1023-wiring rulings 7/8, DEC-copy text items)
Each item below is a declaration the copy makes before the first send. None is a result. quant-proof's OK on the merged head of this copy is the binding; until then each item is a draft.

1. **TIER T1 (ruling 7 item 6; replaces "T0" in §3 "Files" and §8 step 7).** The arena canary reads its OWN tier file `/etc/mal-h5-arena/TIER` (root:root 0644, no symlink, root parent; `ARENA_TIER_PATH`), and a live start needs it to read exactly `T1` (`ARENA_LIVE_TIER`); the shared `/etc/mal-h5/TIER` is ignored for arena books. T1 only lifts the base tier's stake ceiling so that S = 0.05 SOL is reachable; the C0 table compiled in code (`C0_TIER`) is the ceiling whatever the file says (min per field). A `T0` file refuses the start (`arena_tier_not_t1:T0`), which is fail-safe. Live also needs `/etc/mal-h5-arena/LIVE_OK` whose content is exactly `h5v1q50-boostfloor-a1` (#836), in addition to `/etc/mal-h5/LIVE_OK`.
2. **decision_chain substitute (ruling 7 item 7), verbatim, needs QP OK.** "The H5 executor has no hash-chained decision log. Its analogue stamps `decision_chain` in the parity state: the feed's start record is pinned to the book's frozen sha (`code_sha` == `a5f413704eb120c8f529101a6186e3a95de7f83e`, `fresh_forward` ARENA, `dirty` false, `tip_union` off, the book's rule and Q* record), and no `feed_break` is latched. A start record that fails any of these latches `feed_break` (hard) and the stamp stops, so the parity tool's P7 refuses every buy. This substitutes for DEC-032 §3's decision-log chain check and §5 structure halt 3 (hash-chain break); a `frozen_sha` mismatch is the `feed_break` latch." QP OK on this text: POSTX:qp_ok_id.
3. **V-band subset (ruling 7 item 5).** The canary trades only canonical pools: quote WSOL and V in [17.5e9, 17.7e9] (`v_band_refusal`, the band of DEC-031 Am.2 item 6 and DEC-032 §3). The paper book does not filter on the band (its pricing sanity range is 0 to 30 SOL), so the screen read is on a WIDER universe than the canary. A band skip is not a cap (DEC-032 §5): it is logged and counted, never a reason to pick another decision. Before the first send, the book's read is restated IN BAND (the same read on the band subset: n, mean, CI90 lo, ex-best-day, both fail models) or an amendment declares the subset: FILL:gate-read (restated in-band figures, labelled in-sample, or the amendment ref). Tool: `tools/arena_h5_inband.py` (PR #1054, merged be4c7a64; QP OK 6452377389 at fd4db8f5). Command: `python3 -m tools.arena_h5_inband --ledger-root L --shadow-dir D --books-file F --bot h5v1q50-boostfloor-a1 --read-instant MS --i-am-the-read --quote-source shadow-wsol --out OUT.json`. QP conditions:
   - (a) The declaration quotes BOTH pressure-leg figures, the used one (the read's full-book intercept) and the in-band subset's own refit. If the pressure-leg verdicts differ, it says so and never cites the kinder one alone.
   - (b) `--quote-source shadow-wsol` holds only for shadow dirs written by h5_shadow at a5f4137 with the default announce flag, and only for pools whose announcement carries the quote mint. The read job MUST pass `--quote-source shadow-wsol`.
   - (c) The tool has no subset flag for ex-best-day: the declaration states explicitly any in-band ex-best-day total at or below 0.
   - (d) The restatement FAILS, and C0 is blocked (QP-PRERULING-1027-xorder Q4 items 2 and 5), if under EITHER the flat 15% model OR the pressure model at scale 1 (strand rules sc and total) any of these holds in band: CI90 lower bound (1,000 draws, seed 1, 5th percentile) at or below 0; n below 100; fewer than 5 UTC days; not a strict majority of positive days; ex-top-3 total at or below 0. An in-band NOT_DECIDABLE (n below 100 or fewer than 5 days) also blocks. Nothing is rounded, and the amendment route is closed after the read instant. The declaration lists, for each sub-cell: n; the UTC dates and the positive dates; mean; CI90 lower bound; one-sided p; ex-best-day total; ex-top-3 total; and the count of excluded out-of-band trades; all labelled IN-SAMPLE and best of N.
   - (e) Disclosure of #1054 finding (a) (QP 6452377389): the tool takes the first pv trigger of any shadow dir passed and does not apply the scorer's dir filter (`identity_problem`: code_sha, fresh_forward, dirty, start; clock, window and Look-2 seal), so its docstring's "the scorer's reader and order" overstates the match, and condition (b)'s precondition is not checked by the tool. The read job passes only the dirs the scorer counted.
   - Every figure is IN-SAMPLE, best of N, report-only; not a promotion.
4. **+26 bps g_entry bias (ruling 6 item 21, ruling 7).** The twin's entry price is stake / engine tokens BEFORE the H_IN haircut, so g_entry carries a bias of +26.08 bps (= H_IN). It enters only P1 (> +200 bps), not d_i and not the match test, so its direction is fail-safe. `live_buy_price` uses the same unit as the twin's `entry_price` (stake / tokens, before the haircut; ruling 5 (iv)).
5. **Constant p 0.289 (ruling 7 item 14).** The executor has no per-attempt pressure features, so every attempt carries `arena_stack.PRESS_P_TARGET` = 0.289, the pressure leg's refit mean over the cell's fills. The landing-fail excess (P2) then compares the live failure share with max(15%, 28.9%). Per window this is up to 13.9 pp looser than per-attempt p when the attempts are calm (floor 0.15) and stricter when they are hot; P2 halts at 12 of 30 fails. Per-attempt p is required before C1, not C0.
6. **Fee gap (ruling 7 item 13, folded into closed item 19).** The executor sends 55,000 / 55,000 / 150,000 priority + 5,000 base (60,000 per buy or timed sell send; 155,000 per escalated or emergency sell send). The registered stack DEC-031-rev2 prices 505,000 per send. Gap per round trip at timed exits: 2 x (505,000 - 60,000) = 890,000 lamports = 178 bps of S = 0.05 SOL. The paper read and the confirmation stay at the registered 505,000 stack; the canary changes neither. The twin is written at 60,000 per send (twin-writer-loop.sh BUY_SEND/SELL_SEND) and parity uses `pnl_live_basis_lamports` = pnl(E1 off) + RENT + fee_delta (#830), so d_i is at live fees. Residual, declared: an escalated or emergency live resend costs 95,000 more than the twin's 60,000 sell send and a failed earlier buy send is in neither side's buy fee; both make live look worse than the twin (stricter, P3/P4 fire earlier).
7. **md5 scope (ruling 7 item 12).** Decision equivalence for this book: job #1412 (Q50; rolling-sps input #1409) EQUAL, together with jobs #1410/#1411/#1412 for the three H5 books. What it proves and does not (QP md5 ruling and addenda): it compares the executor's `decide_row` verdicts with the frozen scorer's filters on 2026-09-20 (exploration pool, ledger row "Fast pre-cut 2026-09-19T01 -> 2026-09-22T00"; no outcome read), from a git-archive export at the frozen sha with H5_CODE_SHA set; `dirty=false` is the wrapper's assertion, not a measurement by h5_shadow; the input is the `--sps rolling` replay (rolling sps changes per-pool sps, so the trigger set and timing may differ from the pool-sps parity replay; expected counts were re-derived from the new raw dir); Run B substitutes the `synthetic_src` label `pre_event_binary` -> `rpc` on trigger records whose `synthetic` is literally false, so it proves decide_row equivalence GIVEN a plain class, not the class source; Run A shows every pv trigger refused `synthetic_unconfirmed` on both sides. Not covered: the live trigger source, the path-only refusals (gates, mint screen, V band, LIVE_OK, signal age), anything after the decision. Run counts: Disclosure (QP ruling on #1056: accepted; #1412 is cited for its EQUAL verdict only and no #1412 count may ever be quoted): #1412 run counts are not retrievable from the job list visible to manager17. The binding evidence is #1481 at install sha 0526d5cd (VERDICT EQUAL, Run A 137/137, Run B 101/101, B synthetic_unconfirmed 0/0; QP-RULING corrections 2026-10-25, the md5_proof row of section 9a), superseded at install by POSTX:md5_proof at X. The Q50 frozen shadow sha a5f4137 is cited from QP-RULING-1022-q50-exec item 1. These jobs ran before #836; the replay is re-run at the install sha before the install: POSTX:md5_proof.
8. **FEED_NOTE (ruling 7 item 12). Status at 2026-10-27: done.** The note (#851) and the flip (#852) are merged; `FEED_NOTE_MERGED = True`, `FEED_NOTE_BOOKS = {h5v1q50-boostfloor-a1}` at `tools/h5_arena_executor.py:81-83` on origin/main d8325fa7. The older text follows for the record. `FEED_NOTE_MERGED` is False on main: the executor reads no live feed and every live start is refused (`live_feed_note_unmerged`, #836). Before the dry run on the live feed and before the first send: the EXP-024 declared-observation note for this executor (scope item 17: the executor reads the book's arena feed during the EXP-024 Look-2 seal and its live trips create H5 outcomes) merges with QP OK (merge 556f7d8c1aeae5bb17e67bd97e0860bea784884b, verified with gh 2026-10-27); then a reviewed PR flips the constant (#852, merge e2471186383c698825d8ada4bcaa7ba635568bc8, verified with gh 2026-10-27); then the md5 replay is re-run at that sha (item 7).
9. **Seal statement (corrected; replaces the draft clause on the D2 seal; QP-RULING-1025-q50-seal-live ruling 4 and C8).** Seal check (DEC-032 §1 item 3), checked FILL:gate-read (`date -u`) on install sha POSTX:install_sha:
   - The book's pools are V-range PumpSwap pools in their first 360 s on October hours, inside the EXP-024 §3 seal (Look-2 class) until EXP-024's end state, first listed 2026-11-14T00:00Z.
   - They are inside a declared observation that covers live outcomes: the EXP-024 "Note under Amendment 10" (#851, 556f7d8c), scope (a), "its live fills, exits, wallet deltas and realized P&L". It covers bot_id h5v1q50-boostfloor-a1 only (rule sha256 b3f8ecbf..., frozen_sha a5f41370, clock 1792886400000) and pools with s0 in [2026-10-25T00:00:00Z, 2026-11-06T00:00:00Z).
   - The executor is tools/h5_arena_executor.py at X. That sha contains 556f7d8c and the flip e2471186 (#852), with FEED_NOTE_BOOKS = {h5v1q50-boostfloor-a1} and end_ms <= 1793923200000. The md5 decision-equivalence replay is EQUAL at X (job POSTX:md5_proof).
   - Joins are limited to the Note's (b) items 1 to 4 until 2026-11-14T00:00Z. No outcome is split by synthetic class or by the Q* = 40 v1 trigger.
   - No other open seal applies. D2/D3/D4 cover [2026-10-02T10, 2026-10-16T01) and that tip tape. The C1-NF seal is [2026-10-10T00, 2026-10-24T02). CAP-PICK is moot (DEC-031 §3.5). No DBC, HV4 or arena-tape output is read by the executor, twin writer, parity tool or tie-out. Ledger D2/D3 checked FILL:gate-read; the #1059 breach is disclosed and is not a release.
   - A send on a pool outside that window, or any join outside (b), is a seal exposure and a DEC-032 §5 item 5 hard halt.
   - EXP-024 is neither spent nor tainted: its read ended NOT_DECIDABLE on 2026-10-13 and Look 2 does not run. The observation is disclosed by Note (g).
   - **Scope sentence (C8 (b)).** DEC-031 §3.5's "the live canary" and §4's "outputs of the H5 executor" mean the DEC-024 executor and canary. The outputs of this C0 (`h5v1q50-boostfloor-a1` under `tools/h5_arena_executor.py`) are observable under the EXP-024 Note under Amendment 10 (a) and this copy.
   QP seal statement: FILL:review.
10. **Twin pins (ruling 3 blocker 2 / P5; the pin VALUE).** The twin writer's run record must carry `config_hash` = 2d061335ac051e91e1f20c6794a01196e4164317170bf320f91466d46c5100a2 (sha256 of canonical JSON of adapter `h5_twin_adapter_v1`, `arena_h5_cost_sha256` b20d51ceb939822ce813ca48b14348e61873277fef54a3d71af660e37fe03328 = tools/arena_h5_cost.py at a5f4137, stack_id `DEC-031-rev2`) and `code_sha256` = 1e9cb00e08ec69df269b19b9774d02feed7d3e91a61c7f727b7abbdc5fe560bb. The executor checks both (`twin_pins`) and latches `twin_pin_mismatch` otherwise. The writer refuses ledger rows whose `frozen_sha` is not a5f4137. The writer job argv is pinned in hunt29/p1/jobs/twin-writer-loop.sh (sha256 above); Helm/manager submit it with only the four paths and the bot id varying.
11. **Twin rows and the buy-2 window (ruling 3 items 1, 3).** Only PRICED twin rows count (`load_twin_ids(priced_only=True)`): a `price_failed` row is a missing twin. A second buy waits while the first trip is open without a priced twin (`open_untwinned_cap_1`); a trip past its exit without one refuses every buy (`past_exit_untwinned`). The writer runs every 20 s and once on the empty trips file before the first buy; a run record older than 60 s or than the last landed tx is P7.
12. **Rent 0 in the twin (ruling 7 item 4).** The twin's rent 0 (`pnl_rent0_lamports`, and RENT added back in the live basis) is a declared twin-only deviation that holds only if the live sell closes the base ATA in the sell transaction. Shown on chain by the sell simulate on Token and Token-2022: POSTX:simulate_ref (= `FILL:simulate_ref`). Until then no send.
13. **live_net_lamports is the chain delta (ruling 7 item 8).** Shown on chain, not by the executor's own arithmetic: FILL:gate-read (method: the first day's tie-out matches each closed trip's `live_net_lamports` to the chain wallet delta of its buy and sell signatures, to the lamport).
14. **Exit-gap input (lane P1b, #847; QP ruling 2026-10-24 (a), accepted with conditions).**
   - **The check.** The executor writes its own `exit_gap_file` (`<state_dir>/arena-parity/exit-gaps.jsonl`). For each eligible closed trip it records (1 - live_sell_price / twin exit_price) x 10^4 bps. Its `FidelityMonitor` latches `exit_gap_gt_200bps` when the cumulative mean is above 200 bps after 10 trips. A live start refuses any other path.
   - **Why it stands in for P6.** DEC-032 §5 P6 ("-300 bps over the first 10 stop-loss exits") never fires for a timer book. The stand-in is tighter on every axis: 200 bps, all eligible exits, and it keeps evaluating after exit 10. Late-run drift is covered by P3 (rolling d).
   - **Declared unit bias.** The twin `exit_price` is proceeds / tokh. The proceeds carry the (1 - H_OUT) haircut, H_OUT = 0.0016, and tokh = tok / (1 + H_IN). Live is sol_received / tokens. So the twin price is about 16 bps low, and live looks about 16 bps better than it is. That is the fail-open direction. The effective threshold is about 216 bps, still tighter than P6's 300.
   - **Analogue of P6's "report gap-through share".** For a timer book this is P5's late-sell share: sells landing after their exit bound, s0 + 335 s.
   - **Nothing is skipped silently.**
     - A priced twin with a live sell price that is not finite or not > 0 latches at once (`exit_gap_live_price_bad`).
     - A trip with no priced twin, or no exit price > 0, or an unreadable twins file, latches 300 s after its exit landed (`exit_gap_no_priced_twin`).
     - Before that grace ends, such trips are bounded only by the parity P7 no_twin count (Am.1: 2 before 20 trips, else 5%) and by the twin buy gate.
15. **Tie-out ledger input (lane P1b, #843).** `tools/arena_tieout_ledger.py` exports the live ledger to `{sends, trips, open_mints}`. The executor writes one `arena_send` row per signature, carrying the blockhash's last valid height, so an expired send can be a confirmed drop. The daily job runs the export before the fetch. A refusal means no tie-out that day, so buys stop (fail closed). The job (hunt29/p1/jobs/tieout-daily.sh) runs the export as step 0; GIT_REF must be re-pinned to #843's merge sha. Declared (QP #843 condition 2): a `landed:false` row whose fail_class is not "expired" exports as `landed`. That includes a row with no fail_class at all, such as an RPC rejection that never reached chain. Such a row gives a false hard halt (`landed_send_missing_on_chain`), never a false sign-off. A mint bought twice in one exported window is a refusal, so a same-mint re-entry blocks that day's sign-off.
16. **Stake impact.** C0 runs at 0.05 SOL; the deciding stake is 0.30 SOL deciding stake (scorer STAKES 0.10/0.25/0.30 at a5f4137; confirm the pinned stake from the registry at PASS). Impact at the deciding stake is NOT tested; the twin is priced at 0.05 SOL from the same pool states after a 1-lamport self-check at a recorded stake (#830).

17. **Impact disclosure (C8 (c)).** EXP-024 §3's tape-impact bullet named DEC-024's 0.02 SOL. A C0 buy of 0.05 SOL is about 0.1% of Q and about 0.2% in price on a pool with Q of about 45 SOL (QP estimate). It is chain truth, disclosed and not corrected.
18. **Sell-simulate mints (C7).** From outside the Look-2 class (a September graduation or a non-V-range pool); see the QP pre-ruling and `tools/q50_n4_rerun.py`. Pass/fail and instruction decode only, not proceeds.
19. **Twin double-count.** The twin does not remove the canary's own trades from the paper pool states, so it double-counts their impact. QP estimate (not measured): about 20 bps per side at 0.05 SOL, about 40 bps of stake per round trip in live's favour, loosening the -100 bps match test by about 40%. This is the section 11 declaration (with the bps) required before the first send, unless the twin fix has merged by then (a reviewed PR to a pinned tool dropping the canary's own signatures). Using parity for C0 -> C1 additionally waits on settling it.
20. **Disclosure line for the Q50 confirmation read.** From the first send, the twin rows are observable in real time under the EXP-024 Note under Amendment 10 (a), so the confirmation read is no longer blind to those positions. Declared and allowed (DEC-032 §7; DEC-031 Am.3 item 6 "In parallel"); it does not change the read. The owner is told.
21. **Label.** Every canary record, report, notebook and Console entry carries "unpromoted execution measurement".

## 10a. Owner line (Amendment 1 item 3)
`FILL:owner_line`, quoted verbatim from DEC-032 Amendment 1 item 3:

OWNER_CANARY_STANDING_OK: 2026-10-23 (owner, in session, to manager14). Answer: "As for when a strategy passes, you can start the live trial straight away. Time is crucial, as we're coming up on one month with no profit now, so we need to move quick." Notebook: n_ecHJTWJ5nmrttQ (MiScusi notebook of this repo's session)

C0 only. The standing line covers stake at most 0.05 SOL, at most 2 open, daily stop 0.10 SOL and the §3 wallet floor; not the funding transfer, not Helm's install, not C1.

The executor's line (DEC-032 Amendment 1 item 3 "Use"; written ONLY when the manager uses the standing line for this book, on a line of its own with nothing after the id; until then it stays this placeholder, which the executor does not match):

<!-- OWNER LINE GOES HERE, on a line of its own, only on the owner's word (DEC-032 Am.1 item 3 "Use"): the bare line "OWNER_CANARY_CONFIRMED:" + " bot_id=h5v1q50-boostfloor-a1". Not written by the drafter. Until then the executor refuses: canary_record_for_bot_id_missing. -->

## Changes from the template (listed and justified, as the template's status line requires)
1. §3 "Files" and §8 step 7: `TIER` T0 -> **T1** at `/etc/mal-h5-arena/TIER`, plus the arena's own `LIVE_OK` naming the bot_id (QP-RULING-1023-wiring ruling 7 item 6; #825 item 2; #836). Fail-safe: a T0 file refuses the start.
2. Amendments 2 and 3 of DEC-032 are appended as copied from the template file at origin/main d8325fa7, with one change: the dated 2026-10-27 correction to Am.3 section 7 (guard-loop job facts) is added in both (item 5). Section 9a (the fill values) and section 11 (the book-specific declarations that rulings 7/8 require in the copy) are added. Section 10a quotes the owner line. Nothing else in the template text is changed; no limit is raised, no halt threshold loosened, the fee rule unchanged.
3. **Fill order: three stages plus day 1 (QP-PRERULING-1027-xorder Q1; an interpretation of "every FILL filled", not a waiver).** `install_sha` is X, and so is every job run at X; the copy PR's own review ids and head sha cannot exist on the head they review. So "every FILL filled" binds before the first send, and at X it binds as "no open marker", meaning no FILL token whose tag is gate-read, review or owner, and no angle-bracket-FILL token (the exact pattern is in `scripts/ops/q50-canary-record-check.sh`). Every X-dependent field carries one `POSTX:<field>` token, never `FILL:`; the set of tokens is exactly the table below (the check script `scripts/ops/q50-canary-record-check.sh` compares them, and after records PR R it demands 0 outside the declared table).

   | Stage | When | What | Gate |
   | --- | --- | --- | --- |
   | 1 | now, before the read | records PR on DEC-033: twin/tie-out script pins, A3 dated correction, Am.4 status line (in force, #1027), `alert_target` (from the watchdog code), section 11 item 8 (#851 556f7d8c, #852 e2471186) | QP OK on the exact head + reviewer verdict |
   | 2 | at X: the copy PR; its merge is X | every read-dependent field, the status line, the bare owner line `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1`, every X-dependent field as a `POSTX:` token | QP OK on the exact head + reviewer verdict |
   | 3 | after X, before the first send: records PR R | the `POSTX:` fields below. R may touch DEC-033, HELM-SHEET, SHA256SUMS and job scripts only: no pinned path, no other canary DEC. R is never installed (Helm installs X). R must merge before Helm starts `mode=live`. | QP OK on R's exact head + reviewer verdict |
   | 4 | after day 1, not a send precondition | the section 11 item 13 result (day-1 tie-out matches each trip's chain delta); a mismatch is the section 6 item 5 hard halt (O) | n/a |

   The twin and tie-out scripts embed `GIT_REF` (now 0526d5cd), so their sha256 changes when it is re-pinned: they are re-set after X (stage 3), not at stage 1. The current hashes are named until then.

   The `POSTX:` table (declared set):

   | Token | Meaning |
   | --- | --- |
   | `POSTX:install_sha` | X |
   | `POSTX:config_sha256`, `POSTX:manifest_sha256` | executor config and install manifest sha256 at X |
   | `POSTX:md5_proof` | md5 replay job and verdict at X |
   | `POSTX:dryrun_job` | Helm's 1 h dry run at X |
   | `POSTX:simulate_ref` | N4 re-run at X (item 4 below) |
   | `POSTX:qp_comment`, `POSTX:reviewer_comment`, `POSTX:head_sha` | review ids and head of the stage 2 copy PR |
   | `POSTX:qp_ok_id` | QP OK on the section 11 item 2 text |
   | `POSTX:twin_script_sha256`, `POSTX:tie_out_script_sha256` | twin-writer-loop.sh and tieout-daily.sh sha256 with GIT_REF at X |
   | `POSTX:tie_out_job` | scheduled reconciliation job |
   | `POSTX:fund`, `POSTX:start_balance`, `POSTX:wallet_address`, `POSTX:owner_deposit_addresses` | funding facts (owner and Helm) |
   | `POSTX:end_ms` | explicit end, at most 1793923200000 |
   | `POSTX:owner_answer_ids` | the owner's answer ids |
   | `POSTX:a3_guard_source` | timestamped re-confirmation of the A3 chain |
   | `POSTX:webhook_install_confirmation` | Helm's confirmation that the watchdog webhook is set and a test message posted (no value shown) |
4. **N4 re-runs at X (QP-PRERULING-1027-xorder Q2).** #1758 (pre-check at a68338a1) does NOT satisfy the binding N4: Am.4 item 4 requires a re-run at X that includes a mayhem pool, and chain state can change before X. The re-run uses the same two pre-declared pools (Token DB3kaj.../6Btazse7..., Token-2022 5tMa323P.../AqjAELrJ...; no re-selection), `--priority default`, unsigned, job git_ref = X; it records the blob ids of the four Am.4 paths plus `tools/h5_executor.py` and `pool_is_mayhem` for each pool. Helius via `--rpc-env` is acceptable (key never in argv or output; `rpc_host` recorded). A FAIL is a STOP and never falls back to #1758. #1758's use of Helius is disclosed in `POSTX:simulate_ref`; it is disclosure only.
5. **Dated A3 correction (QP-PRERULING-1027-xorder Q5 item 4).** The 2026-10-27 correction to the guard-loop job facts (Am.3 section 7: #1446, #1540, #1319) is appended to Am.3 section 7 in this copy AND in DEC-032 Am.3 section 7. The rule in its items 1-3 is unchanged. The job facts are the manager's; quant-proof did not verify them.
6. **DEC-032 Amendment 5 (DRAFT), feed gap (§5 item 2), adopted by reference.** Written 2026-10-30, records only. It is listed here for three reasons: §5 item 2's copied words carry a reading note; `FILL:feed_gap_source` names the amended hold; and Amendment 4 item 1's executor line carries the moved pin. It takes effect for this copy only when Amendment 5's conditions (a) to (c) hold. The Q50 executor's code is PR #1085, whose md5 replay was EQUAL at head d7e8bf9d (job #1852). Amendment 5 item 6b (P7 `gap` share at C0; quant-proof: acceptable in principle, at C0 only, report qp-1085-gap-hold item 6) is adopted by reference under its own conditions (a) to (c): the code PR #1106 (branch `claude/p7-c0-gap`) and the five-path manifest. Until then P7 applies as written. Consistent with DEC-031 Amendment 21 item 3 and DEC-032 Amendment 6 (PR #1101, HV4), except that Amendment 21's 24 h review latch is not adopted for Q50; quant-proof ruling in its verdict on PR #1103. Why money is safe, item by item: DEC-032 Amendment 5 item 2's table.

## 10. Open points for the first user of this template
- The parity tool (S5), the executor for the book (#793 for H5) and the worst-in-slot figure (S4) are not on main at writing; section 1 items 2 and 6 and section 5 hold those as preconditions, not as assumptions.
- *(Updated by Amendment 1 item 3: the owner's standing line covers the C0 stake, open cap, daily stop and wallet floor shown to the owner; the 0.20 total stop and P3, P6, P7 remain the manager's and quant-proof's values.)* P3, P6, P7 and the C0 limits are the writer's proposal for the manager and quant-proof to confirm (P1, P2, P4, P5 carry LIVE-READINESS / DEC-024 §5 values); none are yet owner-stated. The owner confirms the limits by answering section 2 with them in view.
- DEC-031 Amendment 8 is PR #809 (draft, not merged). Section 4 follows its merged text; if it changes the send cost, a copy re-derives the twin fees from it.

## Amendment 1 (2026-10-23, manager14; records only; QP ruling hunt28/l1b/QP-RULING-1023-canary-tools.md)

1. **Small-sample rule for the 5% shares (§5 bullet "eligible", P7).** The `no_twin` and `gap` shares are tested against 5% only once there are at least **20 live trips** (eligible plus non-eligible). Before that, **2 `no_twin` trips, or 2 `gap` trips,** give the P7 soft halt. Counts run from the first live send and never reset (no window reset, as §5). Every non-eligible trip is logged with its reason for the manager's review. The missing, unreadable or stale state part of P7 stays immediate, at any trip count. Until this amendment merges, and in any tool head that does not implement it, the literal 5% rule applies.
2. **Landing-fail excess wording (§5).** In "pressure refit mean 1 - p", `p` is read as the pressure model's modelled **failure** probability per attempt, so the modelled failure share is the mean of `p_fail_press` (not of 1 minus it), taken from `arena_stack.fail_legs`; §5's text is edited to read "mean p". The tool checks that each p is in [0, 1].
3. **Owner's standing line (§2, §9 `FILL:owner_line`).** Recorded from the owner's own message, not an agent's. The manager's question, in the owner's view: "When a strategy passes, can I start its live trial straight away under those caps from the 1 SOL? Or do you want to OK each one?", where "those caps" were stated in the same message as "at most 0.05 SOL per trade, 2 trades open, 0.10 SOL max loss per day, and a hard wallet floor". The answer: `OWNER_CANARY_STANDING_OK: 2026-10-23 (owner, in session, to manager14). Answer: "As for when a strategy passes, you can start the live trial straight away. Time is crucial, as we're coming up on one month with no profit now, so we need to move quick." Notebook: n_ecHJTWJ5nmrttQ (MiScusi notebook of this repo's session)`.
   - **Covers:** the §2 owner OK for a book that meets every §1 precondition, at stake at most 0.05 SOL, at most 2 open, a daily stop of 0.10 SOL and the §3 wallet floor (the limits the owner was shown).
   - **Not owner-stated:** the 0.20 SOL total stop and halts P3, P6, P7. They only stop a canary earlier; they stand as the manager's and quant-proof's values, and the owner may change them.
   - **Does not cover:** C1 or any step-up, stakes above C0, the wallet funding transfer, or Helm's install (§8 item 7).
   - **Notice before the first send:** the owner receives the copy's filled §2 paragraph (the book's in-sample screen figures, best of N, and the worst case at C0) as notice before the first send. Any "no" from the owner stops the canary.
   - **Use:** a copy quotes this line verbatim as `FILL:owner_line` and adds, on a line of its own with nothing after the id, `OWNER_CANARY_CONFIRMED: bot_id=<bot_id>` (the executor matches that line exactly; the reference to this item goes on the line above it); the manager tells the owner in one line when it is used.
4. **Tools.** Item 1 binds only on a parity-tool head that implements it; on any other head the literal 5% rule of §5 applies.

## Owner ladder pre-approval (DEC-032 Amendment 2, item 8)
OWNER_LADDER_PREAPPROVED: 2026-10-24 (owner, in session, asked by manager15). Question: "If you approve, send this line exactly: OWNER_LADDER_PREAPPROVED: book=h5v1q50-boostfloor-a1 rungs=C0,C1 …" (with hunt29/ladder/OWNER-SUMMARY.md). Answer: "OWNER_LADDER_PREAPPROVED: book=h5v1q50-boostfloor-a1 rungs=C0,C1 stake_sol=0.05,0.15 max_open=2,3 daily_trigger_sol=0.10,0.30 intraday_cap_sol=0.20,0.55 lifetime_cap_sol=0.20,0.70 worst_case_sol=0.25,0.80 deposits_sol=0.35,1.00 rules=DEC-032-Am2 helm_ceiling=C1" Notebook: n_p5ZEchJioV1W7A

## Amendment 2: pre-approvable stake ladder C0 -> C1 for `h5v1q50-boostfloor-a1`, C2 only in a successor book (DRAFT)

**Status: DRAFT, NOT IN FORCE.**
- Written 2026-10-24 (`date -u` 03:00Z at start) by a builder agent for manager15. Revised the same day on quant-proof's OK-WITH-CONDITIONS (comment 6379106221, conditions C1 to C6).
- Records only and outcome-blind: no forward-book output, ledger, runner status, canary, probe or October outcome was opened.

**It takes effect only when all of these hold:**
- (a) this text merges, with quant-proof's OK on the exact head and a reviewer verdict on the PR;
- (b) the owner's own message contains the line of item 8, verbatim;
- (c) the build items of item 9 are merged, installed by Helm and dry-run.

Until then, section 3's C1 column and section 7 stand as written, and nothing above C0 is allowed.

**Evidence status.**
- No book has cleared the promotion gate.
- Q50 has no gate read (its clock starts 2026-10-25T00:00Z) and no edge figure of its own. hunt24 `DESIGN.md` says "no Q50 edge exists".

**Purpose.** The owner approves the C0 -> C1 step of the first H5-family canary once, in advance. The step then happens when its evidence is in, without waiting for a second owner message.
- This amendment moves no evidence bar. The step still needs DEC-031 §2 step 4: the confirmation PASS at p at most 0.05/c, and live fills matching paper.
- The edge evidence for the step is that out-of-sample paper confirmation. The live checks test execution and losses only.
- C2 is defined here, but it is reachable only in a successor book (item 12) with its own owner line.

**Does not amend.**
- The promotion gate and its thresholds.
- DEC-031: the screen read, the confirmation, c, the taints, the seals, and Amendment 4 item 2.2 (H is never extended).
- Sections 1, 2, 4, 5 and 6 of this DEC, and Amendment 1.
- The halts P1 to P7 and their thresholds.
- The Q50 DEC copy's declarations.
- EXP-024 and its notes. Q50's declared observation covers pools with s0 in `[2026-10-25T00:00Z, 2026-11-06T00:00Z)` and nothing later (EXP-024 Am.10 (d)).

### 1. Scope, and what this replaces
**Scope.**
- Covered: only `bot_id` `h5v1q50-boostfloor-a1` at its registered `frozen_sha`, in the canary of its merged DEC copy (the copy of this template for Q50), with one wallet and one executor process (`tools/h5_arena_executor.py`).
- Item 12 covers its successor.
- Not covered: any other book (the a2 siblings, FLOOR, HV4), a second wallet, or a second instance.

**For Q50, this amendment replaces these words of the template, and nothing else.**
1. **Section 3, the C1 column**, is replaced by item 3's C1 column. This is a raise:
   - stake 0.10 -> 0.15 SOL;
   - daily stop 0.20 -> 0.30 SOL;
   - total stop 0.45 -> 0.70 SOL;
   - floor "funded minus 0.55" -> "deposits minus 0.80".

   It is listed here as a change. It is not a fill, and it needs the owner's line of item 8.
2. **Section 3's per-tier total stop** is replaced by item 4's lifetime accounting, and by item 3's daily trigger and intraday cap.
3. **Section 7, the "Step C0 to C1" bullet:**
   - (i) "the book's impact re-priced at C1's stake with the engine's own impact (a capacity-first screen: trades per day times stake times net edge, impact at size) over the confirmation window, reported, and still positive on the gate" is replaced by **item 5, C0 -> C1, check 7**. That check takes the confirmation read's reported-beside cells at 0.10 and 0.25 SOL, which bracket C1's 0.15 SOL. A separate re-price at exactly 0.15 SOL is not run. The bracket is the stand-in, and this item says so.
   - (ii) "the owner's own dated line naming the stake, open cap and stops" is replaced by the pre-approval line of item 8.
   - (iii) "Then Helm writes `TIER` as root" is replaced by item 9: the root `TIER` ceiling and `LADDER_UNLOCK`.
   - **Every other condition of that bullet still binds**, word for word:
     - the confirmation PASS;
     - "live fills match paper" shown;
     - no unresolved halt;
     - at least 5 signed tie-outs and a residual of 0;
     - the re-price "is a check on a step already earned, never used to pick a book, cell or stake";
     - **the sell builder unchanged**;
     - **the wallet funded to 1.00 SOL by the owner** (item 3: deposits of at least 1.00 SOL).
4. **Section 7, the "Beyond C1" bullet**, is replaced by item 12 for C2. Above C2 stays undefined.

### 2. Preconditions (unchanged)
Each is required before C0 and stays required at every rung.
1. The book's one DEC-031 gate read is a **PASS** on its deciding cell (0.30 SOL, 1.9 s leg, both fail models), with the multiplicity block and quant-proof's OK on the exact files. A screen PASS is in-sample for selection: the best of N.
2. The Q50 DEC copy has merged as its own DEC:
   - QP OK on its head and a reviewer verdict;
   - every FILL filled;
   - `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1` written under Amendment 1 item 3.
3. Helm's pinned install from the install sha, and a dry run of at least 1 h with 0 build errors. The md5 decision-equivalence replay is EQUAL at that sha.

C0 then runs exactly as section 3 and the copy say. At C0, this amendment adds only:
- item 3's daily trigger on realized plus marked loss;
- item 4's lifetime accounting.

### 3. Rungs
The stakes were chosen from `/data/mal/hunt-1008/push-1016/hunt29/p5/STACK.md` §1 and `stake_tables.csv`, and from Q50's deciding stake.
- STACK's rows are H5 v1 Q*=40 latest-regime rows, priced with the capacity-pessimistic impact model B.
- Q50 has no rows of its own.

| | **C0** (as section 3) | **C1** (pre-approvable) | **C2** (successor book only, item 12) |
| --- | --- | --- | --- |
| Stake per entry | **0.05 SOL** | **0.15 SOL** | **0.30 SOL** |
| Max open | 2 | 3 | 4 |
| Buy attempts per UTC day (guard reverts count) | 30 | 40 | 50 |
| **Daily trigger**: realized loss since 00:00Z plus open positions marked at their pool quote reaches minus this | 0.10 SOL | 0.30 SOL | 0.60 SOL |
| **Intraday cap** (pre-send; open positions at zero) | 0.20 SOL | 0.55 SOL | 1.40 SOL |
| Most one UTC day can lose (intraday cap plus slack) | 0.25 SOL | 0.65 SOL | 1.55 SOL |
| Lifetime committed-loss cap (item 4) | 0.20 SOL | 0.70 SOL | 1.70 SOL |
| Slack the reserve cannot see (resend fees, failed-sell fee, dust) | 0.05 SOL | 0.10 SOL | 0.15 SOL |
| **Worst case** (lifetime cap plus slack; the most the wallet can lose, all rungs counted) | **0.25 SOL** | **0.80 SOL** | **1.85 SOL** |
| Capital at work: max open x (stake + about 0.003 SOL of buy fee, ATA rent and reserved sell fees) | 0.106 SOL | 0.459 SOL | 1.212 SOL |
| Owner deposits needed (chain transfers from pinned owner addresses; live gains not counted) | 0.35 to 1.00 SOL | **1.00 SOL** (section 7) | **2.00 SOL** |
| Wallet floor | deposits - 0.25, not below 0.05 | deposits - 0.80 | deposits - 1.85 |

**The daily trigger and the intraday cap (QP C6).** These replace "the daily stop only blocks new buys".
- **(a) Daily trigger.** On every landed tx, and at least every 10 s while a position is open, the executor computes the day's loss:
  - realized net loss since 00:00Z;
  - minus the open positions' marked value relative to their cost. The mark is the sell quote for each position's tokens from the pool's current reserves in the feed.

  If that loss reaches the trigger:
  - `STOP` holds new buys until 00:00Z;
  - at C1 or C2 it is also a step down (item 5);
  - open positions exit on their own rules (330 s timer and backstops);
  - **an exit never frees room for a new buy on that UTC day**: the trigger holds until 00:00Z even if the marked loss recovers.

  A missing or stale mark (older than 10 s) means that position counts at zero.
- **(b) Intraday cap**, a pre-send check that does not depend on any mark. No buy is sent unless all of these, added together, stay at or below the rung's intraday cap:
  - today's realized net loss since 00:00Z (floored at 0);
  - every open, pending or in-flight position at zero, with its buy fee, rent reserve and reserved sell fees (positions carried over from the previous day included);
  - this buy's worst case.
- **Why one day cannot exceed the cap.** Every lamport a day can lose comes from two places:
  - trips closed that day;
  - positions still open.

  At every buy instant, both were inside the intraday cap with open positions counted as total losses. After the last buy of the day, losses can come only from those open positions, which were already counted at zero. So one UTC day's loss is at most the intraday cap plus the slack: **0.25, 0.65 or 1.55 SOL**. The daily trigger usually stops new buys much earlier.
- **Fit to the open cap.** The intraday cap fits the rung's full open cap: 0.106 of 0.20, 0.459 of 0.55, 1.212 of 1.40 SOL. After a realized loss of 0.094, 0.091 or 0.188 SOL in one day, fewer positions can be open at once.

**Why these stakes.**
- **C1 = 0.15 SOL.** It is the low end of the 0.15 to 0.25 band. Under model B, the per-trade return of the Q40 rows is almost flat from 0.05 to 0.15 SOL (pressure 4.17% to 4.12%) and falls by 0.25 SOL (3.78%). So 0.15 is the largest stake in the band that model B does not mark down.
- **C2 = 0.30 SOL, not 0.5.** 0.30 SOL is Q50's deciding stake: the gate read and the confirmation read are priced there with the engine's own impact.
  - No read covers 0.5 SOL.
  - Model B marks the Q40 rows down to 2.79% at 0.5 SOL.
- **Daily triggers** are 2 stakes at every rung, as at C0.
- **Lifetime caps** are the capital at work plus room for realized loss: 0.094, 0.241 and 0.488 SOL.
- **Fixed cost.** 120,000 lamports per round trip are 0.24%, 0.08% and 0.04% of the stake.

### 4. Lifetime money accounting (replaces section 3's per-tier total stop for Q50)
- **Committed loss** is counted from the first live send, for the whole canary. It never resets on a step, a restart or a step down. It is the sum of:
  - the net realized loss since the first send (losses minus gains, floored at 0);
  - every open or pending position's stake at zero, with its buy fee, rent reserve and reserved sell fees;
  - every in-flight buy's worst case.

  The method is unchanged from section 3.
- **The current rung's caps bind.** A buy is sent only if all three checks pass:
  - the lifetime cap check;
  - item 3's intraday cap check;
  - the wallet floor check (section 3 method, with the floor of the highest rung reached).

  After a step down to C0, the lifetime cap is C0's 0.20 SOL, counted from the first send. If the canary has already lost more than that, no new buy passes and the canary is in effect stopped.
- **The owner's worst case** over the whole canary is the worst case of the highest rung reached: 0.25 or 0.80 SOL (1.85 in a successor at C2).
  - The floor is set by the owner's deposits.
  - Live gains never lower the floor and never count toward the deposits a step needs.
- Hitting the lifetime cap or the floor ends the canary (section 5 item 6, (O)).

### 5. Step rules
The executor computes these on every landed buy and every closed trip. **Only the executor steps up**, and only from the sources of item 9. The daily job may report, write `STOP`, or step down. It never steps up.

**Step C0 -> C1: all of these, at one instant.**
1. **Confirmation PASS.** The DEC-031 §2 step 4 read of Q50 is a PASS at p at most 0.05/c, with quant-proof's OK on its exact files. Source: the root-owned `LADDER_UNLOCK` (item 9).
2. **Live fills match paper** (section 5, unchanged):
   - at least **100** eligible trips at C0;
   - the CI90 lower bound of mean d_i (1,000 draws, seed 1) at or above -100 bps;
   - landing p50 at most 1.9 s;
   - P7 off.
3. **Trailing fill gap**, over the last 30 eligible trips:
   - mean d_i at or above **-100 bps**;
   - mean g_entry at most **+100 bps** (as the tool computes it, with the declared +26 bps bias included, which errs toward refusing);
   - cumulative mean exit gap (copy item 14) at most **100 bps**.
4. **Halts.**
   - No halt of the "stop" class below since the first send, resolved or not. The executor's `ARENA_HALTS` set mixes both classes; the class lists below decide.
   - Soft halts at C0 are **pauses, not step downs** (see below). At most one since the first send, cleared by the manager's written review, and none in the last 48 h.
5. **Tie-out.**
   - Every closed UTC day since the first send is signed off with residual 0, at least 5 days, by the Helm-installed tie-out unit (item 9).
   - The executor runs its own chain tie-out at the step instant (section 6: "before every tier step") and finds residual 0.
6. **Live total, a loss guard, not an edge test.** The chain total since the first send (all fees and rent counted) is at least **-0.10 SOL**. 100 trips at 0.05 SOL cannot show an edge.
7. **Impact at 0.15 SOL** (replaces §7's re-price wording; item 1.3 (i)).
   - The confirmation read's reported-beside cells at 0.10 SOL and 0.25 SOL (scorer `STAKES` 0.10/0.25/0.30) both meet the gate's tests under **both** fail models:
     - CI90 lower bound > 0;
     - ex-top-3 total > 0;
     - a majority of days positive.
   - Source: `LADDER_UNLOCK`.
   - This check is on a step already earned. It never picks a book, a cell or a stake.
8. **Wallet.** Owner deposits of at least **1.00 SOL**, from chain transfers in from the owner addresses pinned in the copy. The floor check must also pass for one C1 buy with 3 open.
9. **Sell builder unchanged.** `SELL_BUILDER_PIN` re-checked at the step instant.
10. **Time.** At least one full UTC day remains before `end_ms`.

**Step C1 -> C2.** Only in a successor book (item 12), and only under that book's own owner line. The checks, all at one instant:
1. At least **30** eligible trips at the C1 stake, and at least one full UTC day at C1 signed off with residual 0.
2. Live fills still match paper over all eligible trips since the first send (no window reset).
3. The trailing fill gap over the last 30 eligible trips (all at C1), as in C0 -> C1 check 3.
4. No stop-class halt since the first send, and no soft halt at C1.
5. Tie-outs as in C0 -> C1 check 5.
6. **Loss guards, not edge tests.**
   - The chain total since the first send is **above 0**.
   - The chain total of the C1 trips without their 3 best trips is at least **-0.15 SOL** (one C1 stake).

   Over 30 trips, "total > 0" is about one standard deviation of noise. "Ex-top-3 at least -0.15" is looser than the gate's "ex-top-3 > 0". Neither is evidence of edge. **No report cites a step as evidence of edge.**
7. The successor's own gate read PASS (item 12). Impact at 0.30 SOL is that read's deciding cell.
8. Owner deposits of at least **2.00 SOL**. The floor check must pass for one C2 buy with 4 open.
9. The sell builder unchanged, and at least one full UTC day remaining before `end_ms`.

The 20-trip rule (Amendment 1 item 1; P3 after 20 trips) stays a halt rule. It is not a step rule. A step needs the 100 eligible trips of section 5's match test, which this amendment does not lower.

**Pause, step down, stop.**
- **Pause at C0** (no step down): either of these.
  - A soft halt: P1, P2, P3, P5, P7, `exit_gap_gt_200bps`, `entry_gap_gt_200bps`, `landing_fail_excess_gt_10pp` or `sell_send_late_x2`. `STOP` holds until the manager's written review clears it, as section 5 says. A second soft halt with the same id ends the canary, as section 5 says.
  - The daily trigger: `STOP` until 00:00Z.

  A pause does not block a later C0 -> C1 step beyond check 4's limit.
- **Step down to C0, from C1 or C2**, on any of these:
  - a soft halt, which also pauses as above, then resumes at C0;
  - the daily trigger;
  - a rung drawdown: the realized net since entering the rung at or below minus the rung's daily trigger (C1 -0.30, C2 -0.60 SOL), over any number of days;
  - trailing-30 mean d_i below **-200 bps**;
  - owner deposits below the rung's need (a withdrawal).

  **There is no automatic step back up after a step down.** A second step up needs a new owner line.
- **Stop.** The canary ends. A restart is only as section 5 says, and (O) items need the owner. Any of these, at any rung:
  - any hard halt or latch: A3 (`a3_pins_changed`, `pins_changed`, `program_changed`), `feed_break`, `twin_pin_mismatch`, `close_blocked_strand`, `two_stuck_sells` or `landed_buy_unreadable`;
  - an out-of-rule entry;
  - a seal or universe breach;
  - P4;
  - a tie-out residual;
  - the lifetime cap, or the wallet floor;
  - `end_ms`;
  - a confirmation FAIL or NOT_DECIDABLE (section 7: the canary ends at the next 00:00Z).

### 6. Re-pricing and parity at a new stake
- At each rung, the paper twin is priced at the rung's stake with the engine's own impact and the executor's actual per-send fees.
- d_i, g_entry and the exit gap are in bps of each trip's own stake.
- The twin's pins (copy item 10) carry the stake per trip. A twin priced at another stake counts as a missing twin (P7).
- Section 5's thresholds apply unchanged at every rung.
- Statistics run over all eligible trips since the first send. The trailing-30 checks of item 5 are the only per-rung windows.
- Per-attempt pressure p (copy item 5: "required before C1, not C0") is in the parity tool before C1.

### 7. Not pre-approved by the line of item 8
- **C2.** Any stake above 0.15 SOL, more than 3 open, more than 40 buys a day, or a looser cap, trigger or floor. C2 needs item 12 and the successor's own owner line.
- Any other book, wallet or instance (a2 v1/v2, FLOOR, HV4, a Q50 retune). The successor of item 12 needs its own line.
- Any change to:
  - the promotion gate;
  - DEC-031's screen or confirmation rule, or c;
  - the halts or their thresholds.
- A canary longer than its copy's `FILL:days` (at most 14 days), or past `end_ms`. `end_ms` is clamped to 2026-11-06T00:00Z (`SEAL_END_MS`).
- A second step up after a step down.
- The funding transfers themselves (the owner makes them), and any Helm install other than the one-time items of item 9.

### 8. The owner's line (to be quoted verbatim from the owner's own message)
**How it is asked.** The manager asks through `miscusi_ask`, with these in view:
- item 3's C0 and C1 columns;
- item 5;
- the worst cases.

**The line.** The owner approves by sending this line exactly, on its own line, in their own message:

`OWNER_LADDER_PREAPPROVED: book=h5v1q50-boostfloor-a1 rungs=C0,C1 stake_sol=0.05,0.15 max_open=2,3 daily_trigger_sol=0.10,0.30 intraday_cap_sol=0.20,0.55 lifetime_cap_sol=0.20,0.70 worst_case_sol=0.25,0.80 deposits_sol=0.35,1.00 rules=DEC-032-Am2 helm_ceiling=C1`

**How it is recorded.**
- It goes into the Q50 DEC copy as a dated line: `OWNER_LADDER_PREAPPROVED: <date> (owner, in session, asked by <manager>). Question: "...". Answer: "..." Notebook: <id>`.
- Nobody writes it except from the owner's own message.
- `helm_ceiling=C1` asks Helm, once, to write the root files of item 9 with the ceiling C1.
- If any word of the line is changed, it is not this approval, and the manager asks again.
- A later "no" from the owner steps the canary down to C0 at once and ends the pre-approval.

### 9. Build items, and the only sources a step up may read (QP C3)
None of these exists on main at writing.

1. **Executor.**
   - A `LADDER` table (item 3) compiled in code, with C0 = `DEC032_C0`.
   - A live start refuses if any constant is looser than item 3 (`ladder_guard_problem`).
   - Item 3's daily trigger (with the mark) and intraday cap; item 4's lifetime accounting; item 5's pause and step-down rules.
   - The rung and every step record are written to the executor's own state dir. That dir is owned by the `mal-live` service user, and the manager account cannot write it. The records are hash-chained, and each record carries the sha256 of the one before it.
2. **Step-up inputs.** Each comes only from the chain or from a file the manager account cannot write.

   | Input | Source |
   | --- | --- |
   | Ceiling | `/etc/mal-h5-arena/TIER` = `LADDER_C1` (root:root 0644, written by Helm once after the owner's line). `T1` keeps the canary at C0; that is the fail-safe. |
   | Confirmation PASS and check 7 | `/etc/mal-h5-arena/LADDER_UNLOCK` (root:root 0644), written by Helm when the manager shows the merged read (contents below). Without it the rung stays C0. |
   | Deposits | Chain: finalized transfers into the canary wallet from the owner addresses pinned (public) in the DEC copy (`FILL:owner_deposit_addresses`). Any other inflow is not a deposit. |
   | Live total, balance, step-instant tie-out | Chain (finalized), computed by the executor itself. |
   | Daily tie-out sign-offs | The tie-out run by the Helm-installed unit as `mal-live`, writing hash-chained daily records into a `mal-live`-owned dir. The MiScusi copy is a report, never an input. |
   | Parity (d_i, g_entry, exit gap, match test), halts | The executor's own `mal-live`-owned, hash-chained state. |

   **What `LADDER_UNLOCK` contains:**
   - the bot_id;
   - the confirmation report path and sha256;
   - the verdict PASS, with p and 0.05/c;
   - the 0.10 and 0.25 SOL cells' gate results under both fail models (CI90 lower bound, ex-top-3, days positive);
   - the QP comment id;
   - the merged PR number.
3. **Twin and tools.** The twin writer prices at each trip's stake (item 6). The parity tool gets per-attempt pressure p and the trailing-30 checks. The tie-out reports deposits by pinned address.
4. **Proof and install.**
   - Tests for every step, pause and step-down rule, and for the intraday cap bound.
   - QP OK and a reviewer verdict.
   - The md5 decision-equivalence replay EQUAL at the new install sha (the rule's decisions do not change with the stake).
   - Helm's install and a 1 h dry run.
   - The restart recorded in LAB_STATE.

### 10. Timeline under today's windows
This is stated so that nobody plans on a rung that cannot occur.
- Q50's clock starts 2026-10-25T00:00Z.
- The gate read is at the first 00:00Z with at least 100 attempts on at least 5 UTC dates: about 2026-10-30T00:00Z at the earliest.
- The confirmation needs at least 100 attempts on at least 5 further UTC dates: about 2026-11-04T00:00Z at the earliest.
- `end_ms` is clamped to 2026-11-06T00:00Z, and nothing covers Q50 after it (EXP-024 Am.10 (d)). H is never extended (DEC-031 Am.4 2.2).
- So C1 can run for about 2 days at most, and C2 cannot occur in Q50 at all. Past 11-06, only item 12 applies.

### 11. In-sample $ reference (labelled; not evidence)
**No Q50 edge figure exists.** hunt24 `DESIGN.md` says "no Q50 edge exists", and STACK.md does not stack Q50.

The figures below are what the rows say **if live matched paper, and paper here is the in-sample Q40 sibling's rows**:
- STACK.md, model B, pressure fail model;
- latest-regime rows (on or after 09-18) at 13 trades a day, scaled to Q50's planned 20 to 35 triggers a day: a double extrapolation;
- $150 per SOL;
- best of N, with winner's curse.

| rung | daily trigger | SOL per trade (model B, pressure) | in-sample $/day at 20 trades / at 30 (C0 cap) or 35 trades | 50% haircut |
| --- | --- | --- | --- | --- |
| C0 | 0.10 SOL | 0.0021 (4.17% of 0.05) | $6 / $9 | $3 / $5 |
| C1 | 0.30 SOL | 0.0062 (4.12% of 0.15) | $19 / $32 | $9 / $16 |
| C2 (successor only) | 0.60 SOL | 0.0107 (3.58% of 0.30, interpolated) | $32 / $56 | $16 / $28 |

How to read these figures:
- Without each day's 3 best trades, 52% to 68% of each figure remains in the Q40 rows.
- Trades per day are triggers, not fills: band skips, landing fails and the open cap are not modelled.
- A zero or negative true edge is consistent with these numbers (STACK.md §2, "Blunt").
- No sentence here says the book makes money. The canary is an unpromoted execution measurement at every rung.

### 12. Successor book past 2026-11-06 (the only route to running after 11-06, and to C2)
1. **The book.** A new `bot_id` with the same `rule_id` and the same `frozen_sha` as Q50.
   - It is registered in `bots.json` before its own clock, with its own H.
   - It has its own DEC-032 copy, merged with QP OK and a reviewer verdict.
2. **Coverage.** One of these two:
   - **(a) A new declared observation.** A new EXP-024 declared observation for pools with s0 at or after the successor's clock, merged with QP OK and a reviewer verdict **before that clock** (the Am.9/Am.10 pattern; no sealed data opened). It also needs:
     - a DEC-031 §3.5 end-seal successor;
     - a `SEAL_END_MS` code change with an md5 decision-equivalence replay proof at its sha.
   - **(b) A late clock.** A successor clock at or after EXP-024's end state, which is 2026-11-14T00:00Z at the earliest. That leaves a gap of at least 8 days after 11-06.
3. **Rung carry-over, down only.**
   - The successor may start live at the rung Q50 held at its end, or lower, never higher. Starting there rests on Q50's confirmation PASS for the same `frozen_sha`, and needs:
     - its own DEC copy;
     - the coverage of 2;
     - Helm's install and dry run;
     - its own owner line.
   - The lifetime accounting of item 4 starts again for the successor's wallet. The worst case is stated afresh in its owner line.
4. **A further step up (C1 -> C2)** needs all of these:
   - the successor's **own** gate read PASS: at least 100 attempts on at least 5 UTC dates of its own clock, both fail models, deciding cell at 0.30 SOL, with c incremented for the successor;
   - the C1 -> C2 checks of item 5, over the successor's own live trips (Q50's trips are reported beside them, never counted);
   - its **own** owner line naming C2 (`rungs=C1,C2`, `helm_ceiling=C2`, deposits 2.00 SOL).

   This amendment does not pre-approve any of it.

## Amendment 3: in every DEC-032 C0 canary (Q50, HV4, HV6), `synthetic_share_high` is decided by the DEC-031 Am.10 pooled guard, not by the raw daily record (text in force; applies per canary)

**Status: TEXT IN FORCE from 433fe06c (merged 2026-10-26, PR #971); applies to a canary only when condition (b) holds for that canary.** Updated 2026-10-26 (`date -u` 19:19Z), records only. A canary whose install sha does not contain item 5, or (Q50) lacks item 8's re-install proofs, runs under §5 item 1 as written.
- **(a) MET.** This text merged as 433fe06c from PR #971 at head 7b2fcf85. Quant-proof OK: comment 6424806553. Reviewer APPROVE: 6424819933.
- **(b) code merged; per-canary part NOT MET.**
  - Code 5b (Q50 / `tools/h5_arena_executor.py`) merged as 4646e542 from PR #972. QP OK 6425036235, reviewer APPROVE 6424980798.
  - Code 5a (HV4/HV6 C0 / `tools/hv4_live_c0.py`, `tools/arena_live_bridge.py`) merged as bcc0bd2f from PR #977. QP OK 6425156767, reviewer APPROVE 6425105976.
  - Still open, so no canary is covered yet: no canary's install sha is recorded as containing 4646e542 / bcc0bd2f. Not done, as far as this record knows: the Q50 re-install at X with item 8's md5 replay (EQUAL at X, 137/137 and 101/101) and 1 h dry run; the HV4/HV6 install-sha replay and dry run; the `FILL:a3_guard_source` fill per canary (loop job, guard pin, alert job) with the item 7 precondition jobs (loop job 3, alert #1540 extension or successor, dead-man job 3) submitted. Those job ids and limits were not looked up for this record.
- Nothing here is a profit claim. No book has cleared the promotion gate.
- Written 2026-10-26 (`date -u` 18:44:05Z at start) by a builder agent for manager17. Extended at 18:47Z, on manager17's decision, to cover the Q50 (H5) C0 canary too. Revised at about 18:58Z for quant-proof's comment 6424657511 at 588c5b9d (text fixes F1 to F5), and again for comment 6424749534 at 321d7981 (F6 and the dead-man lines, QP's replacement text). Records only.
- Basis: quant-proof's comment 6424350003 on PR #969 (head 733dd371), ruling (b). It found that the HV4 C0 bridge's A3 check reads the raw daily record, as §5 item 1 says and line for line as `h5_arena_executor.a3_record_problem`. Under the raw synthetic flag a 14-day C0 is likely to halt on sampling noise. `h5_arena_executor.a3_record_problem`, which is the Q50 C0 path, uses the same raw rule.
- Outcome-blind. In view when this was written: that QP comment, quant-proof's pre-ruling `/data/mal/hunt-1008/push-1016/hunt30/q50-c0/QP-PRERULING-1026-n2-n4.md` (N2), this DEC, DEC-031 Am.4, Am.10 and Am.15, and the code of `tools/a3_redeploy_check.py`, `tools/a3_halt.py`, `tools/a3_applied_alert.py`, `scripts/ops/a3-daily.sh`, `tools/pump_structure_monitor.py`, `tools/h5_arena_executor.py` and `tools/hv4_live_c0.py` / `tools/arena_live_bridge.py` at PR #969 head aa925782. Not opened: any A3 daily record, verdict or applied log, `a3-books.json`, any HV4 or Q50 output, decision row, ledger, `bots.json`, or job status or log.
- No book has cleared the promotion gate. Nothing here is a profit claim.

**It takes effect only when all of these hold:**
- (a) this text merges, with quant-proof's OK on the exact head and a reviewer verdict on the PR;
- (b) the code of item 5 merges in its own PR (QP OK on the exact head and a reviewer verdict), and the canary's install sha contains it. For Q50 that also means item 8's re-install proofs.

Until then §5 item 1 stands as written: any raw A3 halt flag latches. A canary started on a head without item 5 runs under §5 item 1 as written for its whole run.

**Scope: every DEC-032 C0 live canary, with one rule on both executor paths.**
- **Q50** (`h5v1q50-boostfloor-a1`) runs through `tools/h5_arena_executor.py`.
- **HV4** runs through `tools/arena_live_bridge.py` with the `hv4_c0` profile and `tools/hv4_live_c0.py`. These are the arms that DEC-031 Am.15 item 5 allows.
- **HV6**, in future, may use `hv4_live_c0`'s A3 functions unchanged. An HV6 copy uses this amendment only if it declares it, with the FILL of item 6.
- A canary on a later H5 book, through the same executor, is covered when its copy declares it.

**Does not amend.**
- The promotion gate and its thresholds.
- Any registered paper book or read rule.
  - HV4 is frozen at 9ed4c5ff, and its read rule (DEC-031 §2, Am.15) does not change.
  - Q50's paper book, its frozen shadow (a5f4137), its read rule and its A3 rule do not change. Q50's paper window runs under the Am.10 guard (per the manager's brief; the books file was not opened here). So the canary and the paper book now act on the same synthetic decision.
  - No read, window end, count or decision of any book changes.
- DEC-031 Am.10 and its item 3. Item 3 ("HV4 and FLOOR do not use the guard … This amendment touches nothing they run") stays true for the HV4 and FLOOR paper books and for HV4's frozen code at 9ed4c5ff. The guard still never ends an HV4 or FLOOR window, and no id is added to or removed from `a3-books.json`.
  - **Two disclosed exceptions to item 3's literal words** (F1):
    - (i) The HV4 (and HV6) C0 canary, which is live code and not the frozen book, now depends read-only on the guard's `a3-applied.jsonl` (item 1).
    - (ii) Test 14 imports `tools.a3_halt` and `tools.a3_redeploy_check` in an HV4 test file under `tools/`. QP ruling 3.1's grep would match it. That is a test-only import: no HV4 runtime module imports either.
- The other A3 flags, `pins_changed`, `program_changed`, the 36 h stale bound, the 300 s future bound and fail closed.
- §5's rule that A3 is an (O) halt, and that a canary does not use DEC-031 Am.4's automatic upgrade check.
- §3 limits, P1 to P7, §6, §7, Amendments 1 and 2.

### 1. The rule (replaces §5 item 1's words "Any A3 halt flag" for every canary in scope, on both executor paths)
New buys stop, and `a3_pins_changed` latches, if ANY of these holds. Exits keep running, as now.
1. **Raw record, unchanged except synthetic.** The newest line of the A3 daily record (`FILL:a3_source`) shows any halt flag other than `synthetic_share_high` set, malformed or not evaluated. Or `program_changed` warns. Or the record is missing, unreadable, flag-less, older than 36 h or stamped more than 300 s in the future. This is exactly the check of `h5_arena_executor.a3_record_problem` and of PR #969, with one change: the key `synthetic_share_high` is skipped.
2. **The guard's verdict for the same run is not clear.** The newest line of the guard's applied log (`a3-applied.jsonl`, written by `tools/a3_halt.py apply` in `scripts/ops/a3-daily.sh`) has the same `run_utc` as the record's newest line, and its `verdict` is `HALT`, `FAIL` or `ERROR`. Only `NONE` and `PASS` are clear.
3. **The guard's log is missing or wrong.** It is missing, unreadable, a link, oversize, or its newest line has no valid `run_utc` or an unknown verdict. Or its newest `run_utc` is later than the record's. Or it is earlier than the record's for more than `A3_GUARD_LAG_MS` = 2 h, counted from when the record line was written (the record file's mtime, and never before its `run_utc`).

While the guard's newest `run_utc` is earlier than the record's, and inside that 2 h bound, new buys are **refused** (`a3_guard_pending`). This is a refusal, not a latch. It is counted, and it clears when the guard's line for that run arrives clear.

**What this means for `synthetic_share_high`:**
- The canary halts on it only through the guard's pooled z-test (DEC-031 Am.10 item 1: K = 4, p0 = 75/339, phi = 2.0, halt iff z > 3.0902).
- The single-run share (> 0.35) no longer decides.
- **When it counts as unevaluated.** The guard treats synthetic as unevaluated when this run has no valid counts or `n_checked` < 10 (`SYN_MIN_RUN_CHECKED` = 10, DEC-031 Am.10 item 1). That is not the monitor's own minimum of 5.
- **When an unevaluated flag halts.** Only on two consecutive runs; the guard's existing rule then gives a HARD verdict. Under the raw rule:
  - a run with `n_checked` < 5 latches at once;
  - a run with 5 <= `n_checked` < 10 is evaluated against 0.35.
- **New liveness cost, not a safety cost** (F2). Two consecutive runs with 5 <= `n_checked` < 10 now latch the canary through the guard's HARD verdict. The raw rule would have evaluated both runs.

**What does not change:**
- `pins_changed` (any item) and `program_changed` latch from the raw record, exactly as now.
- A guard `PASS` (a program-only redeploy that DEC-031 Am.4 records as record-only) never clears a raw `pins_changed`. The canary still does not use Am.4's automatic upgrade check.
- Resume after any A3 latch is still the manager's upgrade review plus the owner's line (O).

**The guard is a stricter backstop for every other reason too.** A guard HALT for any reason latches: a boost flag, a degraded record whose pins differ from the pins file while the flag is not set, a failed redeploy check, or a two-run unevaluated flag. The raw rule did not see the degraded-record case.

### 2. False-trip probability under each rule (DEC-031 Am.10 item 2's figures)
The Am.10 figures assume the pooled base rate 0.2212 and come from structure counts. They are simulated under a fitted beta-binomial null, not observed on new data.

| | Raw single-run flag (§5 item 1 as written) | Pooled guard (this amendment) |
| --- | --- | --- |
| Per run | about 0.10 (binomial); **0.157** under the beta-binomial fitted to the observed overdispersion (matches 3 of 18 observed runs above 0.35) | **0.0014** (20,000 simulated paths) |
| Over 24 runs (Am.10's 12-day window at two runs a day) | about **0.98** | **0.023** |
| Over 14 runs (Q50: its read is at or after 2026-10-30T00Z, and its `end_ms` is clamped to 2026-11-06T00:00Z, so at most about 7 days; this amendment's arithmetic) | 1 - (1 - 0.157)^14 = 0.9085 (independent runs; 0.7712 at 0.10) | at most 14 x 0.0014 = 0.0196 (union bound) |
| Over 28 runs (a full 14-day HV4 C0 at two runs a day; this amendment's arithmetic, not an Am.10 figure) | 1 - (1 - 0.157)^28 = 0.9916 (assumes independent runs; 0.9477 at 0.10) | at most 28 x 0.0014 = 0.0392 (union bound; holds under any dependence between runs) |

**Detection (Am.10 item 2).**
- A sustained synthetic share of 0.45 for 2 days trips with probability 0.62.
- A sustained 0.60 for 2 days trips with probability 0.99.
- A sustained 0.45 over the window trips with probability 1.00.
- The pooled rule is therefore slower than the raw flag, by up to about 2 days (K = 4 runs).

**What the flag protects.** Am.10 calls it "a regime alarm (a sustained shift in how graduations happen), not a per-trade protection". Am.10 says that for H5 books, after Amendment 4's per-pool exclusion of synthetic and unclassified pools. Q50 is an H5 book, so that per-pool exclusion applies to it. This amendment does not claim that HV4's or HV6's rule excludes synthetic pools per pool. For every canary the flag stays a regime alarm, and the slower detection is the cost accepted here.

**Not measured.**
- How often a run has `n_checked` below 5, which the raw rule latches on the first run. Also how often two consecutive runs have `n_checked` below 10, which the guard latches (F2). Neither Am.10 nor this amendment gives a rate for either.
- How long the guard's apply takes after the monitor writes its line. The 2 h bound is a declared choice, not a measurement. Past it the canary latches, so the bound can never let a buy through.

### 3. New dependencies, all fail closed
- **The guard loop must run for the canary's whole life.** This is the `a3-daily.sh` loop on mal-fast-0 that writes the record and the applied log into the same directory (`A3_OUT`, default `$HOME/data/structure-monitor`, with `A3_APPLIED` at its default).
  - If the loop stops, the record goes stale (36 h) and the canary latches, as now.
  - If the monitor writes but the apply does not run, the canary refuses buys for up to 2 h, then latches `a3_guard_missing`. For example, a PartialApply (an A3 flag with legacy_bot_ids empty, or a HARD verdict with new_rule_bot_ids empty): apply runs the valid rule's halts, writes no applied line and exits 2. The raw rule did not see this case.
- **The loop runs at a guard pin that implements Am.10.** That is the check tool sha256 `1f84072da673cc451f9e3a6baa8b25d6295b497305ffe7bc38fef42e3a710f70` at tag `a3-guard-pin-1024` (0ec7fae8), or a later pin with quant-proof's OK that keeps item 1's constants byte for byte. The copy names it (item 6).
- **The H5-arena books file and registry stay intact** (F3). Five cases stop the guard. Cases 1 to 3 fail in tools/a3_halt.py's _check, which runs first in the deadman step of every a3-daily.sh run. That step exits 2 before the monitor runs, so no new record is written. Cases 4 and 5 are PartialApply in apply: a new record is written, but no applied line.
  1. a registered `h5*` book that is in neither list of `a3-books.json`;
  2. a listed id that is not registered;
  3. no books registered at all;
  4. a HARD verdict while `new_rule_bot_ids` is empty;
  5. a flag instant while `legacy_bot_ids` is empty.

  Cases 1 to 3: the canary keeps acting on the last record and its guard line until that record is 36 h old, then latches a3_record_stale. This is the same exposure as under the raw rule. Cases 4 and 5: the canary refuses buys for 2 h, then latches a3_guard_missing. Both paths fail closed. But H5-arena registry hygiene now decides C0 liveness, for HV4 and HV6 as well as Q50. So while any canary runs:
  - `a3-books.json` and the arena registry (`bots.json`) are not archived, moved or emptied;
  - every new `h5*` registration is added to a list of the books file at the same time;
  - an edit to either file is checked against the cases above before it lands (item 7).
- **The loop outlives the later canary.** See item 7.
- **Same directory.** The applied log is read only as `dirname(a3_record_file)/a3-applied.jsonl`. No separate path is configured, so the record and the verdict come from the same loop.

### 4. Not a read change
- This amendment touches only the live executors' A3 halt rule.
- No Q50, HV4 or HV6 paper decision, row, window, exclusion, count or read changes. The guard writes no HV4 or HV6 registry entry, and its handling of Q50's paper window is unchanged.
- The md5 decision-equivalence proof of each canary install (§2 checklist) is still re-run at the install sha that contains item 5. For Q50, see item 8.

### 5. The code change (a separate PR after this merges)

There is one rule on both paths.
- **5a, HV4/HV6:** `tools/hv4_live_c0.py` at PR #969 head, plus wiring in `tools/arena_live_bridge.py`.
- **5b, Q50:** `tools/h5_arena_executor.py`.
- They may be one PR or two.
- The H5 copy lives in `h5_arena_executor.py` itself, not in a new shared module, so that Q50's 43-path pinned set (item 8) gains no path.

**5a. `tools/hv4_live_c0.py`, new constants** (next to `A3_LATCH`):
```python
A3_GUARD_FLAGS = ("synthetic_share_high",)  # DEC-032 Am.3: decided by the DEC-031 Am.10 pooled guard (a3-applied.jsonl), never by the raw record
A3_APPLIED_NAME = "a3-applied.jsonl"  # scripts/ops/a3-daily.sh: $A3_OUT/a3-applied.jsonl, the same directory as the daily record
A3_GUARD_VERDICTS = ("NONE", "PASS", "HALT", "FAIL", "ERROR")  # == tools.a3_halt.VERDICTS (pinned by a test)
A3_GUARD_CLEAR = ("NONE", "PASS")  # PASS never clears a raw pins_changed: the raw check runs first and latches on it
A3_GUARD_LAG_MS = 2 * 3_600_000  # declared choice, not measured: a pending guard refuses buys; past this it latches
A3_APPLIED_MAX_BYTES = 16 * 1024 * 1024
```
**`a3_halt_problem`: one change.** Skip the guard's flags in the raw flag scan, and change nothing else:
```python
    why = sorted(k for k, v in flags.items() if k not in A3_GUARD_FLAGS
                 and (not (isinstance(v, dict) and isinstance(v.get("halt"), bool)) or v["halt"] or v.get("evaluated") is False))
```
**New `a3_problem(record_path, now_ms) -> (latch, refuse)`.** It returns the raw reason if there is one. Otherwise it reads the newest line of `os.path.join(os.path.dirname(record_path), A3_APPLIED_NAME)`. The read is lstat regular, `st_nlink == 1`, `O_NOFOLLOW`, an inode re-check and the size cap, the same safe read as `feed_gap_problem`. Then, in this order:
- a missing or unreadable line gives `a3_guard_unreadable`;
- no valid `run_utc`, or a verdict not in `A3_GUARD_VERDICTS`, gives `a3_guard_malformed`;
- the applied `run_utc` is later than the record's: `a3_guard_ahead`;
- it is earlier: `(None, "a3_guard_pending")` while `now_ms - max(record run_ms, record file mtime_ms) <= A3_GUARD_LAG_MS`, else `a3_guard_missing`;
- they are equal and the verdict is not in `A3_GUARD_CLEAR`: `a3_guard:<verdict>`;
- otherwise `(None, None)`.

A prototype of exactly this logic was run on synthetic fixtures only (no host file); its eleven cases are the first eleven tests below.

**`tools/arena_live_bridge.py`, wiring only:**
- `a3_tick` calls `c0.a3_problem` and latches `c0.A3_LATCH` on `latch`.
- It stores `refuse` until the next poll.
- The buy gate refuses on a stored `refuse` and counts it as `a3_guard_pending_refusal`, in the same place as `feed_gap`.
- `main`'s live start check is unchanged: `a3_record_file` stays required, and the applied path is derived from it.
- Any pinned function hash that covers these functions is re-pinned in the same PR.

**5b. `tools/h5_arena_executor.py` (Q50 path), the same rule:**
- Add the same constants beside `A3_MAX_AGE_MS`.
- `a3_record_problem`: the same one-line skip of `A3_GUARD_FLAGS` in its `why` scan. Its other returns and the `(reasons, problem)` shape do not change.
- Add a module-level `a3_guard_problem(record_path, now_ms) -> (latch, refuse)`, with the same order of checks as `hv4_live_c0.a3_problem` and the same safe read.
- `ArenaExecutor.a3_tick`:
  - A raw problem or raw reasons latch `H_A3` as now.
  - Otherwise, a guard `latch` gives `latch_external(H_A3, why=<latch>)`.
  - A guard `refuse` is stored in `self._a3_refuse`; anything else sets it to `None`.
- `ArenaExecutor._hard_refusal`: after `_halted_arena()`, return `self._a3_refuse` when it is set.
- `self._a3_refuse` starts as `None`. When no `a3_record_file` is configured, `a3_tick` returns before reading anything (as now), so `_a3_refuse` stays `None`. This is the replay and dry-without-record case.
- `start_refusal`'s `a3_record_file_missing` check does not change. The applied path is derived from the record path, so no config key is added.

**Tests the code PR must carry** (synthetic fixtures only; tests 1 to 12 run on BOTH paths, `hv4_live_c0.a3_problem` and `h5_arena_executor.a3_guard_problem`, and give the same result on each):
1. raw synthetic halt with guard NONE for the same run: no latch, no refusal;
2. raw synthetic not evaluated with guard NONE: no latch;
3. raw clear with guard HALT, FAIL or ERROR: latch `a3_guard:<verdict>`;
4. raw `pins_changed` with guard PASS: latch on `pins_changed`;
5. raw `program_changed` warn with guard NONE: latch;
6. guard behind, inside the bound: refusal only;
7. guard behind, past the bound: latch `a3_guard_missing`;
8. guard ahead: latch;
9. guard log missing: latch;
10. unknown verdict: latch;
11. raw stale or future: latch as now;
12. guard log is a symlink, has a second hard link or is oversize: latch;
13. **equivalence with the H5 path:** over a fixture corpus, the reasons of `c0.a3_halt_problem` equal those of `h5_arena_executor.a3_record_problem` minus `synthetic_share_high`, and the problems are equal. This replaces QP (b)'s "pin to `a3_record_problem`" test;
14. `A3_GUARD_VERDICTS == tools.a3_halt.VERDICTS`, and the pooled constants of `tools.a3_redeploy_check` (`SYN_POOL_K` 4, `SYN_P0` 75/339, `SYN_PHI` 2.0, `SYN_Z_HALT` 3.0902) equal item 1's;
15. the bridge test: a pending guard refuses buys while exits keep running, and a guard HALT latches `a3_pins_changed`;
16. the H5 executor test, the same as 15: a pending guard makes `_hard_refusal` return `a3_guard_pending` while exits run, and a guard HALT latches `H_A3`;
17. H5 with no `a3_record_file`: `a3_tick` reads no file, and `_hard_refusal` returns what it returned before the change for a fixture trigger set. This is the replay case.
17b. H5 with `a3_record_file` set, the raw record clear and the guard `NONE` for the same `run_utc`: `a3_tick` latches nothing, `_a3_refuse` is `None`, and `_hard_refusal` returns the pre-change value for the fixture trigger set. This is the live, guard-clear case (item 8.3 (a)).

The 5b PR also prints item 8.3 (b)'s per-function source-identity list.

### 6. FILL for a copy that uses this amendment
| Field | Meaning |
| --- | --- |
| `FILL:a3_guard_source` | `dirname(FILL:a3_source)/a3-applied.jsonl` on mal-fast-0; the `a3-daily.sh` loop job id and its guard pin (check tool sha256 as item 3); the time limit and successor chain that cover the later `end_ms` (item 7); the alert job (`tools/a3_applied_alert.py`). The manager names #1446, then successor #1447 (loop) and #1540 (alert). These ids were not looked up by this writer (item 7). The copy records them from the job list. |

The copy's owner notice (§2) is unchanged in words: "It halts by itself on a pump program redeploy" stays true.

### 7. Guard-loop lifetime: the loop must outlive the LATER canary `end_ms`
**Which end counts.**
- Q50 C0: `end_ms` is at most 2026-11-06T00:00Z (Am.2 §10).
- HV4 C0: `end_ms` is at most 14 days after its first send. That send comes after a recorded HV4 PASS read, which is at or before 1794096000000 (2026-11-08T00:00Z, DEC-031 Am.15 item 1). So an HV4 `end_ms` can be as late as about 2026-11-22T00:00Z, or later if the owner extends.
- The binding end is the latest `end_ms` among canaries that are running or approved. Call it `E`.

**What must hold.** The `a3-daily.sh` loop and its successor chain run without a missed twice-daily run until at least `E`. The loop is the job that writes `daily.jsonl` and `a3-applied.jsonl` at the guard pin of item 3.

**Am.10 item 5(3) design.** The loop is resumable, runs 13 days, and is then followed by a new successor at the guard pin.

**The facts, as the manager gives them** (quant-proof did not verify them, and neither did this writer):
- Loop #1446 ends about 2026-11-06T18Z.
- #1447 runs `after` #1446 with a 13 d limit, so it ends about 2026-11-19T18Z. An `after` job starts when its predecessor ends, so it ends earlier if #1446 dies early.
- Alert #1540 ends about 2026-11-08T13Z.
- Dead-man loop #1319 (a3-daily.sh deadman at 13:10Z, pin 136cd98) ends about 2026-11-03T21Z. #1333 runs after it with a 13 d limit, so it ends about 2026-11-16T21Z, and earlier if #1319 dies early.
- Q50 alone (E <= 2026-11-06T00Z) is covered by #1446, with only about 18 h of margin. #1447 stays queued.

**Correction 2026-10-27 (`date -u` ~20Z, records PR; the facts above are left as written).** Read from `miscusi_job_status`: #1446 started 2026-10-24T00:57:39Z with a 312 h limit, so it ends at 2026-11-06T00:57:39Z, not "about 2026-11-06T18Z"; the margin over Q50's E (2026-11-06T00Z) was 57 min, not 18 h. Its limit has since been extended to 14 d, so it now ends 2026-11-07T00:57:39Z. Alert #1540 runs to A3_ALERT_UNTIL 2026-11-06T17:53Z (job limit 2026-11-07T17:53Z), not "about 2026-11-08T13Z". Dead-man #1319 was due to end 2026-11-03T21:13:55Z; its limit is now 14 d, so it ends about 2026-11-04T21:13Z (QP-PRERULING-1027-xorder Q5 item 2; the real limit is to be read from the job list); #1333 stays queued after it. Successors #1447 and #1333 are still queued. Latest submit times if an HV4 or HV6 canary moves E (QP-PRERULING-1027-xorder Q5 item 4): loop job 3 by about 2026-11-18T00:57Z; the alert by 2026-11-05T17:53Z, by a successor, because extending the job does not move A3_ALERT_UNTIL. Q50 alone is covered. Not checked by quant-proof.

**Before each canary's first send** (a precondition, recorded in `FILL:a3_guard_source`):
1. The manager records the running loop job, its pin and its time limit, and every queued successor with its `after` link and time limit.
2. **The one rule (48 h)** (F5). Every chain job whose limit is before `E` is either:
   - extended (`miscusi_job_extend`) to cover `E`; or
   - given a successor `after` it, at its predecessor's pin with the same header sha checks, resumable, 13 d. For the loop chain that is the guard pin of item 3. For the dead-man chain it is 136cd98 or the guard pin (see below).

   This is done at least **48 h before that job's actual limit**. A queued successor's limit is computed from its real start, not from the nominal chain. The same rule is applied again whenever `E` moves later, that is when a new canary is approved or the owner extends one.
3. **Required now, from the facts above:**
   - **Loop job 3:** `after` #1447, pin 0ec7fae8 with the same header sha checks, resumable, 13 d. It is submitted before the first HV4 C0 send, as a `FILL:a3_guard_source` precondition. If no HV4 C0 is approved, it is still submitted at least 48 h before #1447's actual limit. An HV4 `E` can reach about 2026-11-22T00Z, at least about 2.25 days past #1447.
   - **Alert #1540:** extended, or given a successor, before the first HV4 send, and at the latest by 2026-11-06T13Z.
   - **Dead-man job 3:** after #1333, resumable, 13 d, at pin 136cd98 or the guard pin. tools/a3_halt.py, tools/arena_h5_score.py and scripts/ops/a3-daily.sh are byte-identical between the two. It is submitted before the first HV4 C0 send, and at the latest 48 h before #1333's actual limit. An HV4 E of about 2026-11-22T00Z is about 5 days past #1333.

**Nothing the guard depends on is stopped or emptied while any canary runs.** That covers the loop, the alert job, the dead-man job, `a3-books.json` and the arena registry (item 3, F3). This holds even if Q50's paper book ends (window end, A3 or dead-man end). The Q50 lane's step that stops the loop, or archives its files, is gated on "no canary running".

**What a lapse costs.** A missed run is not silent:
- if the record goes stale (36 h), the canary latches `a3_record_stale`;
- if the apply is missing, the canary refuses buys for up to 2 h, then latches `a3_guard_missing`.

Either latch ends the canary until the manager reviews it and the owner restarts it (O). A loop lapse is therefore a liveness cost and never a silent pass.

**Why this writer did not look up the job list.** A full job list shows the status of every job, including HV4 #1561/#1562 and Q50 shadows #1419/#1421. Viewing those statuses is sealed (SEALS-BRIEF). The job ids above are the manager's. The manager checks them, and the time limits, from the job list.

### 8. Q50: the code rides the re-install at X that is already required
Quant-proof's pre-ruling N2 (`QP-PRERULING-1026-n2-n4.md`, verdict (a)) already requires these at X, the merge sha of Q50's DEC copy, before any Q50 live send: a new install, the md5 replay re-run at X with no inheritance, and a 1 h dry run at X. The 5b change is installed there and adds no extra install step.

1. **The pinned set changes in exactly one blob.**
   - The 43-path list itself (list sha256 `244f9e85dd5870bfc47be1cf535eadecd0e2783cc17fee11440ab0ea7ae83720`) keeps the same paths, because 5b adds no file.
   - The blob of `tools/h5_arena_executor.py` changes, so `manifest-q50-X.sh` step 2 prints `PINNED_SET_CHANGED` for that one path.
   - Under N2 item 3 this makes X a code change. The 5b diff needs its own reviewer and QP pass before the replay counts.
   - The script's reference for that one path is then re-pinned from 0526d5cd to the 5b merge commit. The other 42 paths stay checked against 0526d5cd and must be identical.
   - HELM-SHEET §A's hash of `./tools/h5_arena_executor.py` (`c451a615...`) is re-pinned to the new blob.
   - The test files are not in the pinned set.
2. **The md5 replay at X must still reproduce #1481's counts exactly:** Run A 137/137, Run B 101/101 attempts, Run B `synthetic_unconfirmed` 0/0. Any other count is STOP, no install.
3. **What the replay proves, and what it does not** (F4).
   - `h5_arena_exec_replay.executor_verdicts` builds `ArenaExecutor(StubRpc(), cfg, None, ...)` with no `a3_record_file`, and calls only `decide_row`.
   - `decide_row` never reaches `a3_tick` or `_hard_refusal`. `_hard_refusal`'s one caller is the base buy path (`tools/h5_executor.py` `_handle_trigger`), which the replay does not run.
   - So the replay EQUAL shows that `decide_row` and its callees are unchanged. It does **not** exercise any 5b line. It stays the STOP gate as written.
   - The 5b changes are proven by two further items, both required on the 5b PR:
     - **(a) Test 17b, live and guard clear.** With `a3_record_file` set, the raw record clear and the guard `NONE` for the same `run_utc`: `a3_tick` latches nothing, `_a3_refuse` is `None`, and `_hard_refusal` returns the pre-change value for the fixture trigger set. Test 17 covers the no-record case.
     - **(b) Diff confinement, printed on the 5b PR.** A per-function source-identity list (an AST or `inspect.getsource` sha256 per top-level function and method) of `tools/h5_arena_executor.py` at 0526d5cd and at the 5b head. Every function and method is identical except the named ones: the new constants, `a3_record_problem`, `a3_guard_problem`, `a3_tick`, `_hard_refusal`, and the `_a3_refuse` init line. `decide_row` and everything it calls must be in the identical set.
   - **Before the replay counts at X** (quant-proof, comment 6424657511 §5): QP OK on the 5b PR's exact head and a reviewer verdict on that PR, with (a) and (b); the manifest showing exactly one `PINNED_SET_CHANGED` path and the list sha256 still 244f9e85…; the re-pins (new blob id and sha256 from the 5b merge commit, HELM-SHEET §A) in the Q50 copy's records PR; and tests 1 to 17 and 17b green at the 5b head on mal-fast-0's Python.
4. **The bind mount already covers the applied log.** `scripts/mal-fast/mal-h5-arena-executor-binds.conf` (in the pinned set, unchanged) binds the whole guard output directory read-only: `BindReadOnlyPaths=__A3_DIR__:/srv/mal-h5-arena/a3`, with `a3_record_file` at `/srv/mal-h5-arena/a3/daily.jsonl`. So the derived path `/srv/mal-h5-arena/a3/a3-applied.jsonl` is visible with no unit change. The dry run must show the guard read succeeding through that bind; `a3_guard_unreadable` there is a STOP. The HV4 install pack needs the same: the whole directory bound, not the single file.
5. **The 1 h dry run at X** is configured with `a3_record_file` set to the fast-0 record, so the guard read runs. It reports the number of `a3_guard_pending` refusals and any A3 latch, counts only.
6. **No switch mid-run.** If 5b is not in X, Q50's C0 runs under §5 item 1 as written (the raw rule) for its whole run. Moving it later needs another install with its own replay and dry run.

### 9. Disclosure
- **Direction.** This lowers the chance of a halt. It is a liveness change. The safety halts on real program or account changes are kept as they are, and the guard adds latch reasons the raw rule did not have. Under the null, the synthetic regime alarm's per-run false-trip rate goes from 0.157 to 0.0014, and it detects a real shift later (item 2).
- **Chosen from DEC text, code and QP rulings only.** No A3 record, verdict or applied line was read for this amendment, and no job status or log. Am.10's figures were chosen on structure counts (18 post-redeploy runs, pooled 75/339). No HV4 or Q50 outcome exists in any input.
- **QP (b)'s other owed items stand.** These are link and owner checks on `a3_record_file`, the dependencies in the install pack (precondition 14), and the equivalence test, now in the form of test 13.

**Open points.**
- **The lag bound.** The 2 h `A3_GUARD_LAG_MS` was confirmed as written by quant-proof (comment 6424657511 §3). The clock runs from the later of the record's `run_utc` and the record file's mtime. Once past the bound the canary latches; a newer line does not reset the clock.
- **Item 7.** The job ids and limits in item 7 are the manager's facts. Neither quant-proof nor this writer checked them. The actual running and queued jobs, their pins and their limits are recorded in the C0 DEC copy at first send (`FILL:a3_guard_source`), where quant-proof checks them.
  - Latest submit times under the 48 h rule, on those facts: loop job 3 about 2026-11-17T18Z; dead-man job 3 about 2026-11-14T21Z; alert #1540, extension or successor, 2026-11-06T13Z.
  - All three move earlier if a predecessor ends early, and all three are preconditions of the first HV4 send.

## Amendment 4: Q50 X-time install, the pinned set changes in exactly FOUR blobs (mayhem fee recipient fix) (DRAFT)

**Status: IN FORCE since PR #1027** (merge a68338a1; quant-proof OK 6444537128 and reviewer APPROVE 6444537901, both at the merged head 2fcd126624b32dbad95741c335d1ae837c2892ac, comments at 2026-10-27T14:24Z). Status line corrected by the records PR of 2026-10-27; the draft status below is the text as first written.

**Status (as written): DRAFT.** Written 2026-10-27T13:51Z (`date -u`), records only, before any Q50 C0 send. It becomes text in force only after merge plus a quant-proof OK and a reviewer verdict on the merged head. It does not rewrite Amendment 3 section 8 item 1 ("The pinned set changes in exactly one blob"); it supersedes that count for the Q50 X-time install once in force. The 43-path list itself (sha256 `244f9e85dd5870bfc47be1cf535eadecd0e2783cc17fee11440ab0ea7ae83720`) keeps the same paths.

1. **Four changed blobs vs the reviewed install 0526d5cd, no others:**
   - `tools/h5_arena_executor.py` (the 5b blob, unchanged by this amendment: blob 70d789ff, sha256 ef8ae931...) *(Executor line superseded for the X-time install by DEC-032 Amendment 5 item 7 (DRAFT until in force): blob `8369ab21349b568efd0eda4fd64fde2e90510bca`, sha256 `7e6d2f0e57388c0c3ea40eb85f6c208ad07aa2389067da70f0de6ca9679abfe0` (main 6117c35c with #1085, plus #1091 DEC-032 Am.7; moved by #1091 from #1085's `53337e08` / `4f5f443a` under Am.5 item 7). The words above are left as written.)*
   - `tools/pumpswap_tx.py`
   - `tools/pumpswap_simulate.py`
   - `tools/probe_executor.py`

   `scripts/ops/q50-xtime-manifest.sh` (PR #1027) exits 3 `PINNED_SET_CHANGED` unless exactly these four differ, and pins each of the three new blobs by exact git blob and sha256, as it does the executor (any other content, even one appended line, is exit 3):
   - `tools/pumpswap_tx.py`: blob `2c9f6dda26ddbed50a6231b47a85c91723a19d50`, sha256 `2c71d3c0d561968fd8836e688f21c50f5e19d1947a4be547b5e40a041d841655`
   - `tools/pumpswap_simulate.py`: blob `ec5f22a8048c100500bde49ca34622d88b6e6706`, sha256 `63d27af8f71066c480da0fb6d3a7cb163f6f623d2ec93b3e84bb5793d8156966`
   - `tools/probe_executor.py`: blob `07b2851b8086a7cd402574ad601af26a7344db84`, sha256 `f786d4012a442f7136993caceae1a0b9062230148d1f7d04e907028a82022b01`

   Probe: `sh scripts/ops/test_q50_xtime_manifest.sh` (local throwaway origin): X = head gives rc 0; one extra line in each of the three files, the executor, or `tools/probe_live.py` gives rc 3.
2. **Reason** (notebook decision n_aIm_21YbvT68_A). N4 #1750 found PumpSwap error 6013 `InvalidProtocolFeeRecipient` on a mayhem pool (pool byte 243 = 1): the builder always chose `protocol_fee_recipients[1]`, and a mayhem pool accepts only a reserved recipient. PR #1027 adds one shared helper `tools/pumpswap_tx.py fee_recipient` (and `pool_is_mayhem`) used on the buy and the sell path. Non-mayhem tx bytes are byte-identical to the old rule: `tools/test_pumpswap_mayhem_recipient.py` `BuilderPaths.test_simulate_non_mayhem_bytes_identical` and `test_snapshot_non_mayhem_bytes_identical_cached_and_uncached` compare the serialized buy and sell messages. Not shown: that every one of the 8 reserved keys is accepted (2 were simulated in #1025), and any simulate of a mayhem buy.
3. **Moved pins** (`tools/hv4_live_c0.py`, SELL_BUILDER_PIN / PIN_EXTRA):
   - `pe.fetch_snapshot_sha256`: e0c0d598b99a92864d401b32a99f2f70550565c03a3618df975484cd47f49b74 -> 4eca167a0f3c4fcd3e1d1a53cdab00fa4f7a183c3895cf1caca2ec9c666de4c5 (body changed).
   - `x.pumpswap_tx.fee_recipient_sha256`: new, 930899fddb8b24e796bce00b5044322c9aad6a8903ef952b5592898782c8ed03.
   - `x.pumpswap_tx.pool_is_mayhem_sha256`: new, 74d043fcf2cd07d136f0d13eb5785105210e5060faeebbdafb745280d59923d7.
4. **Still owed before C0 (none done at the time of writing):**
   - the manifest run at X: `sh scripts/ops/q50-xtime-manifest.sh <X>`, rc 0, four changed paths, the three blob pins of item 1, list sha256 `244f9e85...`; its manifest sha256 goes to Helm;
   - every other precondition in `ARTIFACTS/lab/q50-xtime/Q50-X-TIME-CHECKLIST.md` section G and the go-live blocker list (section I): tests 1-17 and 17b green at X on mal-fast-0, the 1 h dry run at X (0 build errors, `a3_guard_unreadable` = 0, `a3_guard_pending` and latch counts reported), the DEC copy and the canary record check at X, and the HELM-SHEET re-pin to X in one records PR;
   - the md5 replay at X: `sh scripts/ops/q50-xtime-md5-job.sh <X>`, last line `md5-replay VERDICT: EQUAL`, #1481 counts A 137/137, B 101/101, B synthetic_unconfirmed 0/0. The replay exercises `decide_row` only and does NOT test the recipient change. That change is covered by the byte-identity tests (item 2) plus the N4 rerun below;
   - Helm's re-pinned install of X, requested through the owner;
   - an N4 rerun at X that includes a mayhem pool (sell simulate; a mayhem buy has not been simulated).
   - Known behaviour, disclosed: if the global mayhem switch is off (`mayhem_mode_enabled` = 0), a mayhem pool's exit snapshot refuses to price (`mayhem_refused`). The live exit then never sells blind: the position is held and `unpriced_position` is alerted every 60 s without naming the reason, until the switch returns. The alert text is not changed in this PR because `tools/probe_live.py` is a pinned path outside the four. A mayhem snapshot also always refreshes the shared parsed global-config cache (`StaticCache.put`); tx bytes of non-mayhem pools are unchanged (byte-identity tests).
5. **Not a result.** No book has cleared the promotion gate. Nothing here is a profit claim. Gate unchanged.

