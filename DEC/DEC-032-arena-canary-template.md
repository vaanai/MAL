# DEC-032: Arena live canary, template (fill-in-the-blanks for a DEC-031 screen pass)

| Field | Value |
| --- | --- |
| **Status** | **TEMPLATE. NOT IN FORCE (Amendment 1, 2026-10-23, at the end of this file). Written 2026-10-23 (`date -u` 10:10Z at start), records only.** It allows no live send, no wallet use, no tier, no `LIVE_OK`. It becomes a canary DEC for one book only when a copy of it, with every `FILL` of section 9 filled and the template text otherwise unchanged (any other change is listed and justified in the copy), merges as its own numbered DEC with quant-proof's OK on the exact head and a reviewer verdict on that PR. Even then nothing is sent until the owner's own OK of section 2 is recorded and Helm acts (section 8). |
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

> Vaan, book `FILL:bot_id` passed the arena screen read at `FILL:read_instant` on its deciding cell (`FILL:book_stake` SOL stake, 1.9 s leg). All figures in this paragraph are IN-SAMPLE and selected: mean `FILL:mean` SOL per trade, CI90 lower bound `FILL:ci_lo`, one-sided p `FILL:p`, ex-best-day total `FILL:ex_best_day` SOL, worst-in-slot mean `FILL:wis_mean` with CI90 lower bound `FILL:wis_lo`, `FILL:n` attempts on `FILL:d` days. This is a screen, the best of `FILL:N` books: if no book had an edge, the chance of at least one screen pass would be up to 1 - 0.95^N (40% at N = 10, 79% at N = 30). It is not a profit claim and it has not been confirmed; the confirmation is at least 100 more attempts on at least 5 more UTC dates with p at most 0.05/c (c is set when that read is made). I am asking for a small live **execution measurement**, not a bet on the edge. If you say yes: the wallet is funded with `FILL:fund` SOL (your choice, 0.35 to 1.00 SOL; starting balance `FILL:start_balance`), the stake is `FILL:canary_stake` SOL per trade, at most 2 open at a time, at most 30 buys a day. Trading stops by itself at a realized loss of `FILL:daily_stop` SOL in a day, and at `FILL:total_stop` SOL of total loss counting open positions as lost; the worst case is `FILL:worst_case` SOL lost, leaving the wallet at `FILL:floor` SOL or more. The fee per send is `FILL:fee` (taken from the installed executor's config, not assumed). "Live matches paper" means, in plain words: over at least 100 finished live trades, each compared with a paper twin of the same decision priced at the same fees, live is not worse than paper by more than 1 percentage point of stake on average (we are 90% sure of that), and live orders land within 1.9 seconds at the median; until 100 trades exist we say "parity not shown" and take no step up. It halts by itself on a pump program redeploy, a feed gap, a break in the decision log, a missing or stale loss, balance, twin or parity reading, or when live fills drift from paper (section 5), checked on every landed buy and every closed trade. It runs for at most `FILL:days` days. It does not raise the stake: any larger stake needs the confirmation and a new line from you. Do you approve (yes / no / change), and will you fund the wallet and tell Helm to set the files yourself?

**Checklist (all must be shown to the owner as done or open, in the ask or beside it):**
- [ ] DEC-031 screen read PASS, with multiplicity block, p, Holm rank, N, c
- [ ] quant-proof OK on the read, exact files and head sha: `FILL:qp_comment`
- [ ] ex-best-day total > 0: `FILL:ex_best_day`; worst-in-slot mean and CI90 lo: `FILL:wis_mean`, `FILL:wis_lo`
- [ ] seal check stated by quant-proof: `FILL:seal_status`
- [ ] mint fetch done: `FILL:mint_fetch` (job id, close-blocked count of mints, credits)
- [ ] sell builder pinned (section 4), tx simulate output attached: `FILL:simulate_ref`
- [ ] this copy merged as its own DEC (QP OK + reviewer verdict on head `FILL:head_sha`)
- [ ] executor/bridge installed DRY by Helm at `FILL:install_sha`; dry run of at least 1 h with 0 build errors (`FILL:dryrun_job`) and a md5 decision-equivalence replay proof against the frozen decision log (`FILL:md5_proof`)
- [ ] parity tool (`FILL:twin_tool`) and tie-out tool (`FILL:tie_out_tool`) built and run dry (section 5, 6); alerts go to `FILL:alert_target`
- [ ] paper twin repriced at the canary stake with the executor's actual per-send fees (section 4), leg and E1 treatment as pinned in section 5 (`FILL:twin_leg`, `FILL:twin_e1`)
- [ ] funding amount and wallet address (public) named (`FILL:wallet_address`); withdraw address kept by `FILL:withdraw_holder`
- [ ] end instant `FILL:end_ms` set in code/config; C0 constants at `FILL:c0_constants`

**Owner answer** is recorded as a dated line in the copy, verbatim, with the MiScusi question and notebook ids, in the form used by DEC-024 (`OWNER_CANARY_CONFIRMED: <date> (owner, in session, asked by <manager>). Question: "...". Answer: "..." Notebook: <id>`). Nobody fills that line except from the owner's own message. The owner's OK covers the wallet, funding, `LIVE_OK` and `TIER`; each stays a separate act (Helm creates `LIVE_OK` and `TIER` as root; the executor cannot).

## 3. Limits (code constants; config may only lower them)
`S` is the canary stake. S = min(`FILL:book_stake`, 0.05 SOL). If the book's deciding stake is above S, the canary measures execution at S only: impact at the deciding stake is NOT tested, the paper twin is repriced at S with the engine's own impact (capacity-first lesson), and any step up re-prices at the new stake.

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
- **Wallet floor is the binding bound, and the worst case is derived from it.** floor = max(0.05 SOL, funded - 0.25 SOL). The maximum worst case is therefore funded - floor = 0.25 SOL at every funding of 0.30 SOL or more: 0.20 SOL committed-loss cap plus 0.05 SOL of slack for what the reserve cannot see (escalated resend fees, a failed-sell fee, dust). A buy is refused unless balance minus open stakes at zero minus this stake, fees, rent and the sell reserve is at least the floor (DEC-024 §4, DEC-020 §3 style; the balance has already paid for open stakes, counting them again is deliberate). Both the committed-loss check and the floor check must pass, so the two cannot disagree: at the 0.35 SOL minimum funding the floor is 0.10 SOL and the worst case 0.25 SOL. `FILL:worst_case` is 0.25 and `FILL:floor` is funded - 0.25 (not below 0.05).
- **Fail closed.** No buy is sent if any of these is missing, unreadable or stale (older than 60 s, or older than the last landed tx): the committed-loss state file, the balance read, the paper twin for the decision row, the parity state, the `STOP`/`LIVE_OK`/`TIER` reads, or the decision-log chain check. Exits of open positions continue. Each such refusal is logged with its reason and counted.
- **Fixed cost vs stake.** At S = 0.05 SOL, two sends at 505,000 lamports are 2.0% of the stake, at 160,000 about 0.64%; the E1 floor adds 384,022 lamports per 0.05 SOL. A canary measures execution, not edge, and the report says so.
- **Paper twin.** Every live entry has a paper twin priced by `tools/arena_paper.py` from the same decision row, at stake S and with the executor's actual per-send fees, escalated resends included as sent (section 4). The twin is the comparator for section 5, priced as pinned there. Where the live sell path does not close the ATA in the sell transaction, the rent-charged leg is the twin (DEC-031 Amendment 2 item 5).
- **Venue and universe:** exactly the book's registered universe, nothing else. Canonical pools only (quote WSOL, V in [17.5e9, 17.7e9]) where Amendment 2 item 6 applies. Token-2022 extensions outside {metadataPointer, tokenMetadata} or any freeze authority: pre-send refusal (strand class = total loss).
- **Sell:** `min_out` at 0.85 of the quote; after 2 reverts or a time bound, resend at 0.65 with higher priority; never `min_out` 0 (DEC-024 §4). Time-stop and stop-loss backstops run with a warm sell plan (bridge S2); the exit poll and sell build are the fast ones. Residual gap-through below the stop is not fixable by polling and is reported, not hidden.
- **Buy guard:** on-chain `min_out` at the decision price divided by 1.15; signal age at most 5 s.
- **Files:** `STOP` (no new buys, exits continue), `HALT` (freeze), `LIVE_OK` and `TIER` root-owned. Live requires `LIVE_OK` present and a valid `TIER` (T0 here). Missing, unsafe or unknown files mean DRY or T0. The wallet-wide `/var/lib/mal-live/STOP` stays the kill switch; the manager takes no STOP, HALT or unit action (Helm does).

## 4. Fee tier and sell builder
- **The fee is a FILL taken from the installed executor's config, never assumed.** `FILL:fee` = buy, sell and escalated priority in lamports, plus the 5,000 base fee, each cited as path:line@install_sha. Today they differ by path:
  - `tools/h5_executor.py:267-268` (origin/main): buy 55,000, sell 55,000, escalated 150,000 (H5 books and the #793 subclass, which does not override them);
  - `tools/probe_live.py` feearm and earlier profiles (the bridge's `LiveExecutor`): 500,000, and the feearm profile refuses any other priority (`ARTIFACTS/lab/feearm-landing-2026-10-23.md`: "the live executor's priority (still 500,000)").
- **The twin is priced at exactly those per-send fees, escalated resends as actually sent.** Pricing it at the registered stack's 505,000 per send while live pays 60,000 would add about +178 bps per round trip at S = 0.05 SOL ((505,000 - 60,000) x 2 / 0.05 SOL) to d_i and hide P3 and P4. Any gap between the executor's fees and the book's registered stack is declared in the DEC copy before the first send, with the bps it moves; the paper read and the confirmation stay at the registered stack (never changed by the canary).
- **155,000 priority (160,000 per send) for new arena books** belongs to DEC-031 Amendment 8, now PR #809 (`claude/arena-send-160k`, draft; `stack_config_v2`). It is not merged at writing and changes no registered book's stack. A canary uses a priority other than the executor's installed one only if that is a reviewed executor config change, named in `FILL:fee`, and the owner's line names it. The fee-arm result (Test A and Test B PASS) is a cost input only.
- **Sell and close the base ATA in one transaction** under the mint's own token program (DEC-031 Amendment 2 item 5, Amendment 3 item 6). The builder is pinned as `FILL:builder_path@sha256`; today `tools/h5_executor.py` (lines 2524-2525 at the time of Amendment 2) and `tools/probe_live.py` (759-760) do. `tools/pumpswap_tx.sell_instructions` alone closes only the WSOL ATA and is not allowed alone. A tx simulate output for a sell of a recorded mint is attached to the owner ask. If the close cannot run the whole sell reverts and the position sticks (strand behaviour).

## 5. Halts and the parity metric
Any halt creates `STOP` (no new buys); open positions exit on their rules unless the manager asks Helm for `HALT`. A halt is never followed by a retune of the book. A hard halt is cleared only by the manager's written review and, for items marked (O), the owner.

**Structure halts (hard):**
1. **A3 / redeploy (O).** Any A3 halt flag, `pins_changed` (any item) or `program_changed` of the pump or PumpSwap programs, fees program or any pinned account. A canary does not use DEC-031 Amendment 4's automatic upgrade check (its item 1.1 excludes canaries). Resume only after the manager's upgrade review (the DEC-024 13 October resume record is the pattern) and the owner's line.
2. **Feed gap.** Any gap record in the book's feed (disconnect, resubscribe, `gap_silence_ms` 60,000 exceeded, a PumpSwap `data_loss`): new buys stop at once and restart only after the feed has been gap-free for 10 minutes and the manager has seen the gap record. A gap during an open position marks that live trade `gap`, reported separately; it is excluded from parity statistics by the same rule that excludes it from the paper read (DEC-031 §1.4), and counted.
3. **Decision-log integrity.** A hash-chain break, a `frozen_sha` mismatch (the executor's `start` row repeats the pin), or a config key differing from the registration pin: hard latch (bridge `chain_break`, `frozen_sha_mismatch`).
4. **Stuck position.** A position not closed by its exit instant + 600 s: no new buys, alert, retries back off 30 s to 10 min, abandoned after 10 and Helm runs the root sell-and-close tool. Strand class = total loss in the report.
5. **Seal / universe.** An entry outside the registered universe, a close-blocked mint reaching the sender, or a pool inside a closed seal: hard halt and a record. A seal breach ends the canary.
6. **Stops.** Daily stop, total stop, wallet floor, absolute `end_ms`. The total stop and floor need the owner (O) to restart.
7. **Wallet tie-out.** Section 6 residual not zero on a closed day.

**Parity metric (live vs paper, measurable).** Pinned before the first send, no choice after data:
- **Twin comparator:** `tools/arena_paper.py` at the book's registered config, the **nofail** leg (a live trip that landed on both sides is compared with the engine's landed outcome, not the 15% blend), the **1.9 s latency leg** (the deciding leg of DEC-031 §2), priced at the executor's actual per-send fees (section 4). **E1 floor treatment: the twin is priced WITHOUT the E1 floor (about +77 bps of cost at 0.05 SOL) and decides; the twin with E1 is reported beside it.** Excluding E1 is the stricter comparison (E1 is a haircut that would mask live drift). `FILL:twin_leg` = "nofail, 1.9 s" and `FILL:twin_e1` = "E1 excluded decides" and a copy does not change them after its first send.
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
0. Baseline: the starting balance `FILL:start_balance` and the funding tx are recorded by the owner/Helm before the first send; each later day starts from the previous signed-off balance.
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
7. Owner: yes/no, wallet funding, own instruction to Helm. Helm: pinned root install from the named sha, manifest, key via credential, drop-ins, watchdog; `TIER` T0 and `LIVE_OK`.
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

Everything else in this template stays as written. A copy that changes a limit upward, a halt threshold looser, or the fee rule is not a fill and needs its own amendment text and review.

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

## Amendment 2: pre-approvable stake ladder C0 -> C1 -> C2 for `h5v1q50-boostfloor-a1` only (DRAFT)

**Status: DRAFT, NOT IN FORCE.** Written 2026-10-24 (`date -u` 03:00Z at start) by a builder agent for manager15; records only, outcome-blind: no forward-book output, ledger, runner status, canary, probe or October outcome was opened. It takes effect only when all hold: (a) this text merges with quant-proof's OK on the exact head and a reviewer verdict on the PR; (b) the owner's own message contains the line of item 8 verbatim; (c) the build items of item 9 are merged, installed by Helm and dry-run. Until then section 3's C1 column and section 7 stand as written, and nothing above C0 is allowed. No book has cleared the promotion gate. Q50 has no gate read (its clock starts 2026-10-25T00:00Z) and no edge figure of its own.

**Purpose.** Let the owner approve, once and in advance, the stake steps of the first H5-family canary, so that a step happens when its evidence is in, without waiting for a new owner message. It moves no evidence bar: every step still needs DEC-031 §2 step 4 (the confirmation PASS at p at most 0.05/c, and live fills matching paper), and section 7's checks. It only removes the wait for a second owner message once those are met.

**Does not amend.** The promotion gate and its thresholds; DEC-031 (screen read, confirmation, c, taints, seals); sections 1, 2, 4, 5 and 6 of this DEC; Amendment 1; the halts P1 to P7 and their thresholds; the Q50 DEC copy's declarations; EXP-024 and its notes (the Q50 declared observation covers pools with s0 in `[2026-10-25T00:00Z, 2026-11-06T00:00Z)` and nothing later).

### 1. Scope
Only `bot_id` `h5v1q50-boostfloor-a1` at its registered `frozen_sha`, in the canary of its merged DEC copy (the copy of this template for Q50), one wallet, one executor process (`tools/h5_arena_executor.py`). Any other book (the a2 siblings, FLOOR, HV4, a later Q50 variant), a second wallet or a second instance is outside this amendment. For Q50, this amendment replaces section 3's C1 column and the "Beyond C1" bullet of section 7.

### 2. Preconditions (unchanged; each is required before C0, and stays required at every rung)
1. The book's one DEC-031 gate read is a **PASS** on its deciding cell (0.30 SOL, 1.9 s leg, both fail models), with the multiplicity block and quant-proof's OK on the exact files. A screen PASS is in-sample for selection, the best of N.
2. The Q50 DEC copy merged as its own DEC (QP OK on head, reviewer verdict), every FILL filled, `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1` written under Amendment 1 item 3.
3. Helm's pinned install from the install sha and a dry run of at least 1 h with 0 build errors; the md5 decision-equivalence replay EQUAL at that sha.
C0 then runs exactly as section 3 and the copy say. This amendment adds nothing at C0 except the step-down target of item 5 and the lifetime accounting of item 4.

### 3. Rungs
Stakes chosen from `/data/mal/hunt-1008/push-1016/hunt29/p5/STACK.md` §1 and `stake_tables.csv` (H5 v1 Q*=40 latest-regime rows, the capacity-pessimistic impact model B; Q50 has no rows of its own) and from Q50's deciding stake.

| | **C0** (as section 3) | **C1** | **C2** |
| --- | --- | --- | --- |
| Stake per entry | **0.05 SOL** | **0.15 SOL** | **0.30 SOL** |
| Max open | 2 | 3 | 4 |
| Buy attempts per UTC day (guard reverts count) | 30 | 40 | 50 |
| Daily stop (realized loss in a UTC day; no new buys until 00:00Z, and a step down per item 5) | 0.10 SOL | 0.30 SOL | 0.60 SOL |
| Lifetime committed-loss cap (item 4) | 0.20 SOL | 0.70 SOL | 1.70 SOL |
| Slack the reserve cannot see (resend fees, failed-sell fee, dust) | 0.05 SOL | 0.10 SOL | 0.15 SOL |
| **Worst case** (cap plus slack; the most the wallet can lose, all rungs counted) | **0.25 SOL** | **0.80 SOL** | **1.85 SOL** |
| Capital at work (max open x (stake + about 0.003 SOL buy fee, ATA rent and reserved sell fees)) | 0.106 SOL | 0.459 SOL | 1.212 SOL |
| Owner deposits needed in the canary wallet (worst case + 0.10, live gains not counted) | 0.35 to 1.00 SOL | **0.90 SOL** (fund to 1.00) | **1.95 SOL** (fund to 2.00) |
| Wallet floor | deposits - 0.25, not below 0.05 | deposits - 0.80 | deposits - 1.85 |

Why these numbers:
- **C1 = 0.15 SOL.** It is the low end of the 0.15 to 0.25 band. Under STACK's model B the per-trade return of the Q40 rows is almost flat from 0.05 to 0.15 SOL (pressure 4.17% to 4.12%; flat 4.96% to 4.85%) and falls by 0.25 SOL (3.78% / 4.42%), so 0.15 is the largest stake in the band that model B does not mark down. It is 3 x C0; the C0 parity at 0.05 SOL does not test impact at 0.15, which is why C1 is re-priced (item 6) and measured live before C2.
- **C2 = 0.30 SOL, not 0.5.** 0.30 SOL is Q50's deciding stake: the gate read and the confirmation read are priced at 0.30 SOL with the engine's own impact, so it is the largest stake any read covers. At 0.5 SOL no read exists for Q50, and model B marks the Q40 rows down to 2.79% (pressure) from 3.78% at 0.25. A stake above 0.30 SOL is outside this amendment (item 7).
- **Max open 2, 3, 4 and the caps.** Each lifetime cap is the rung's capital at work plus room for realized loss (C0 0.094, C1 about 0.24, C2 about 0.49 SOL). Once realized loss eats that room, the pre-send check allows fewer open positions, so the rung derates itself before its cap.
- **Daily stops** are 2 stakes at every rung, as at C0.
- **Fixed cost.** The executor's 60,000 lamports per send are 0.24% of stake per round trip at C0, 0.08% at C1 and 0.04% at C2.

### 4. Lifetime money accounting (replaces section 3's per-tier total stop for Q50)
- **Committed loss** is counted from the first live send, for the whole canary, and never resets on a step, a restart or a step down: the net realized loss since the first send (losses minus gains, floored at 0) plus every open or pending position's stake at zero, its buy fee, rent reserve and reserved sell fees, plus every in-flight buy's worst case (section 3, unchanged method).
- **The current rung's cap binds.** No buy is sent unless committed loss plus that buy's worst case is at most the current rung's lifetime cap, and the wallet floor check passes (section 3 method, with the floor of the highest rung reached). After a step down to C0, the cap is C0's 0.20 SOL counted from the first send: if the canary has already lost more than that, no new buy passes and the canary is in effect stopped.
- **Hence the owner's worst case** over the whole canary is the worst case of the highest rung reached: 0.25, 0.80 or 1.85 SOL. Owner deposits fix the floor; live gains never lower it and never count toward the deposits a step needs.
- Hitting the lifetime cap or the floor ends the canary (section 5 item 6, (O)), as at C0.

### 5. Step rules (computed by the executor on every landed buy and every closed trip; the daily job re-computes them)
**Step C0 -> C1: all of these, at one instant.**
1. **Confirmation PASS.** The DEC-031 §2 step 4 read of Q50 is a PASS at p at most 0.05/c, with quant-proof's OK on its exact files, recorded in a merged PR (the unlock record of item 9).
2. **Live fills match paper (section 5, unchanged):** at least **100** eligible trips at C0; the CI90 lower bound of mean d_i (1,000 draws, seed 1) at or above -100 bps; landing p50 at most 1.9 s; P7 off.
3. **Trailing fill gap.** Over the last 30 eligible trips: mean d_i at or above **-100 bps**; mean g_entry at most **+100 bps** (as the tool computes it, the declared +26 bps bias included, which errs toward refusing); cumulative mean exit gap (copy item 14) at most **100 bps**.
4. **Halts.** No halt of the "to stop" class below since the first send, resolved or not. The executor's `ARENA_HALTS` set mixes both classes; the class lists below decide. At most one soft halt since the first send, cleared by the manager's written review, and none in the last 48 h.
5. **Tie-out.** Every closed UTC day since the first send is signed off with residual 0 (section 6), at least 5 days, plus a tie-out at the step instant (section 6: "before every tier step").
6. **Live total.** The chain total since the first send (from the tie-out, all fees and rent counted) is at least **-0.10 SOL**. This is a loss guard, not an edge test: 100 trips at 0.05 SOL cannot show an edge.
7. **Impact at 0.15 SOL.** The confirmation read's reported-beside cells at 0.10 SOL and 0.25 SOL (scorer `STAKES` 0.10/0.25/0.30) both meet the gate's tests (CI90 lower bound > 0, ex-top-3 total > 0, a majority of days positive, both fail models). They bracket 0.15 SOL. This check is on a step already earned; it never picks a book, a cell or a stake.
8. **Wallet.** Owner deposits at least 0.90 SOL (the tie-out names the deposits), and the floor check passes for one C1 buy with 3 open.
9. **Time.** At least one full UTC day remains before `end_ms`.

**Step C1 -> C2: all of these, at one instant.**
1. At least **30** eligible trips at the C1 stake, and at least one full UTC day at C1 signed off by the tie-out with residual 0.
2. Live fills still match paper over all eligible trips since the first send (no window reset; item 2 of the C0 -> C1 list).
3. Trailing fill gap over the last 30 eligible trips (all at C1): as C0 -> C1 item 3.
4. No hard halt or latch since the first send; no soft halt at C1.
5. Tie-outs as C0 -> C1 item 5.
6. **Live total.** The chain total since the first send is **above 0**, and the chain total of the C1 trips without their 3 best trips is at least **-0.15 SOL** (one C1 stake).
7. Impact at 0.30 SOL is the confirmation read's own deciding cell (item 1 of the C0 -> C1 list); nothing extra.
8. Owner deposits at least 1.95 SOL, and the floor check passes for one C2 buy with 4 open.
9. At least one full UTC day remains before `end_ms`.

The 20-trip rule (Amendment 1 item 1; P3 after 20 trips) stays a halt rule. It is not a step rule: a step needs the 100 eligible trips of section 5's match test, which this amendment does not lower.

**Step down (automatic, no human act needed to step down).**
- **To C0** (stake 0.05 SOL, max open 2, C0's lifetime cap from item 4): any soft halt (P1, P2, P3, P5, P7, `exit_gap_gt_200bps`, `entry_gap_gt_200bps`, `landing_fail_excess_gt_10pp`, `sell_send_late_x2`); a daily stop at C1 or C2; a rung drawdown, i.e. the realized net since entering the rung at or below minus the rung's daily stop (C1 -0.30, C2 -0.60 SOL), over any number of days; trailing-30 mean d_i below **-200 bps**; owner deposits below the rung's need (a withdrawal). A soft halt also creates `STOP` as section 5 says, and the manager's written review clears it; buying resumes at C0.
- **To stop** (the canary ends; restart only as section 5 says, (O) items need the owner): any hard halt or latch (A3 / `a3_pins_changed` / `pins_changed` / `program_changed`, `feed_break`, `twin_pin_mismatch`, `close_blocked_strand`, `two_stuck_sells`, `landed_buy_unreadable`, out-of-rule entry, a seal or universe breach, P4); a tie-out residual; the lifetime cap; the wallet floor; `end_ms`; a confirmation FAIL or NOT_DECIDABLE (section 7: the canary ends at the next 00:00Z).
- **No automatic step back up.** After any step down, the rung stays C0 for the rest of the canary. A second step up needs a new owner line.

### 6. Re-pricing and parity at a new stake
- At each rung the paper twin is priced at the rung's stake (0.15 or 0.30 SOL) with the engine's own impact and the executor's actual per-send fees; d_i, g_entry and the exit gap are in bps of each trip's own stake. The twin's pins (copy item 10) carry the stake per trip; a twin priced at another stake is a missing twin (P7).
- Section 5's thresholds apply unchanged at every rung. Statistics run over all eligible trips since the first send; the trailing-30 checks of item 5 are the only per-rung windows.
- Per-attempt pressure p (copy item 5: "required before C1, not C0") is in the parity tool before C1.

### 7. Not pre-approved by the line of item 8
- Any stake above 0.30 SOL, more than 4 open, more than 50 buys a day, or a looser cap, stop or floor.
- Any other book, wallet or instance (a2 v1/v2, FLOOR, HV4, a Q50 retune).
- Any change to the promotion gate, DEC-031's screen or confirmation rule, c, the halts or their thresholds.
- A canary longer than its copy's `FILL:days` (at most 14 days) or past `end_ms`, which is clamped to 2026-11-06T00:00Z (`SEAL_END_MS`); any new declared observation under EXP-024 after that date.
- A second step up after a step down.
- The funding transfers themselves (the owner makes them) and any Helm install other than the one-time items of item 9.

### 8. The owner's line (to be quoted verbatim from the owner's own message)
The manager asks through `miscusi_ask`, with this amendment's item 3 table, item 5 and the worst cases in view. The owner approves by sending this line exactly, on its own line, in their own message:

`OWNER_LADDER_PREAPPROVED: book=h5v1q50-boostfloor-a1 rungs=C0,C1,C2 stake_sol=0.05,0.15,0.30 max_open=2,3,4 daily_stop_sol=0.10,0.30,0.60 lifetime_cap_sol=0.20,0.70,1.70 worst_case_sol=0.25,0.80,1.85 deposits_sol=0.35,0.90,1.95 rules=DEC-032-Am2 helm_ceiling=C2`

It is recorded in the Q50 DEC copy as a dated line (`OWNER_LADDER_PREAPPROVED: <date> (owner, in session, asked by <manager>). Question: "...". Answer: "..." Notebook: <id>`), and nobody writes it except from the owner's own message. `helm_ceiling=C2` asks Helm, once, to write the root files of item 9. Any word changed in the line is not this approval; the manager asks again. Any later "no" from the owner steps the canary down to C0 at once and ends the pre-approval.

### 9. Build items before any step above C0 (none exists on main at writing)
1. Executor: `LADDER` table (item 3) compiled in code with C0 = `DEC032_C0`; the rung computed only from the executor's own state (parity state, halts, tie-out records, balance, deposits record); a live start refuses if any constant is looser than item 3 (`ladder_guard_problem`); step-down rules of item 5; lifetime accounting of item 4.
2. Root files, written by Helm once after the owner's line: `/etc/mal-h5-arena/TIER` = `LADDER_C2` (the ceiling; `T1` keeps the canary at C0, fail-safe) and `/etc/mal-h5-arena/LADDER_UNLOCK`, written by Helm when the manager shows the merged confirmation PASS (report sha256, QP comment id). Without `LADDER_UNLOCK` the rung stays C0 whatever the live checks show.
3. Twin writer priced at each trip's stake (item 6); parity tool with per-attempt pressure p and the trailing-30 checks; tie-out reporting deposits and the C1-trip ex-top-3 total.
4. Tests for every step and step-down rule, QP OK, reviewer verdict; md5 decision-equivalence replay EQUAL at the new install sha (the rule's decisions do not change with the stake); Helm's install and a 1 h dry run; the restart recorded in LAB_STATE.

### 10. Timeline under today's windows (stated so nobody plans on a rung that cannot occur)
- Q50's clock starts 2026-10-25T00:00Z. The gate read is at the first 00:00Z with at least 100 attempts on at least 5 UTC dates: about 2026-10-30T00:00Z at the earliest.
- The confirmation needs at least 100 attempts on at least 5 further UTC dates: about 2026-11-04T00:00Z at the earliest.
- The canary's `end_ms` is clamped to 2026-11-06T00:00Z, and the Q50 declared observation covers no pool after it.
- So C1 can run for about 2 days at most, and C2 (at least 30 C1 trips plus one full signed C1 day plus one more day before `end_ms`) **cannot be reached inside the current window**. C2 needs a new window first: a new EXP-024 declared observation, or the EXP-024 seal's end (first 2026-11-14T00:00Z), and an owner extension. Neither is in this amendment. Under such a window the step rules here apply unchanged.

### 11. In-sample $ reference (labelled; not evidence)
At Q50's planned 20 to 35 triggers a day, **if live matched paper, and paper here is the in-sample Q40 sibling's rows** (STACK.md, model B, pressure, $150 per SOL, latest-regime rows on or after 09-18; best of N, winner's curse; Q50 has no edge figure and STACK.md does not stack it):

| rung | SOL per trade (model B, pressure) | in-sample $/day at 20 trades / at 30 (C0 cap) or 35 trades | 50% haircut |
| --- | --- | --- | --- |
| C0 | 0.0021 (4.17% of 0.05) | $6 / $9 | $3 / $5 |
| C1 | 0.0062 (4.12% of 0.15) | $19 / $32 | $9 / $16 |
| C2 | 0.0107 (3.58% of 0.30, interpolated) | $32 / $56 | $16 / $28 |

Without each day's 3 best trades, 52% to 68% of these daily figures remains in the Q40 rows. Trades per day are triggers, not fills: band skips, landing fails and the open cap are not modelled. A zero or negative true edge is consistent with these numbers (STACK.md §2 "Blunt"). No sentence here says the book makes money; the canary is an unpromoted execution measurement at every rung.
