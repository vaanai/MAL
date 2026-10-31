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
2. **Feed gap.** Any gap record in the book's feed (disconnect, resubscribe, `gap_silence_ms` 60,000 exceeded, a PumpSwap `data_loss`): new buys stop at once and restart only after the feed has been gap-free for 10 minutes and the manager has seen the gap record. A gap during an open position marks that live trade `gap`, reported separately; it is excluded from parity statistics by the same rule that excludes it from the paper read (DEC-031 §1.4), and counted. *(For a canary fed by `tools/h5_shadow.py` through `tools/h5_arena_executor.py`, read through Amendment 5 (DRAFT) once its conditions (a) to (c) hold: a `socket_reconnect` gap record with `flags_pools` the literal false holds nothing and marks no trip; every other gap record stops new buys and holds them 60 s; the "manager has seen" clause is replaced by the ledger and the daily review. For the HV4 bridge, see Amendment 6 (PR #1101). Otherwise the words above apply as written.)*
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

**Status: DRAFT.** Written 2026-10-27T13:51Z (`date -u`), records only, before any Q50 C0 send. It becomes text in force only after merge plus a quant-proof OK and a reviewer verdict on the merged head. It does not rewrite Amendment 3 section 8 item 1 ("The pinned set changes in exactly one blob"); it supersedes that count for the Q50 X-time install once in force. The 43-path list itself (sha256 `244f9e85dd5870bfc47be1cf535eadecd0e2783cc17fee11440ab0ea7ae83720`) keeps the same paths.

1. **Four changed blobs vs the reviewed install 0526d5cd, no others:**
   - `tools/h5_arena_executor.py` (the 5b blob, unchanged by this amendment: blob 70d789ff, sha256 ef8ae931...) *(Executor line superseded for the X-time install by Amendment 5 item 7 (DRAFT until in force): blob `b96f023fe64eadd63a2d377e08a0d004a7038d20`, sha256 `9cb7ac22acc75247d8788044054349019c7656a656036421529e7da4d97f1fde` (main 6117c35c with #1085, plus #1091 DEC-032 Am.7 and #1106 Am.5 item 6b; moved by #1091 and then #1106 under Am.5 item 7, last from `8369ab21` / `7e6d2f0e`). The words above are left as written.)*
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


## Amendment 2, Erratum 1: item 12 names the successor base S, not Q50's frozen sha (QP-RULING-1024-successor R7) (DRAFT)

**Status: DRAFT, NOT IN FORCE.** Written 2026-10-27T20:00:08Z (`date -u` on mal-research-0), revised 2026-10-27T20:18:49Z on QP-RULING-1027-s1 B2 and (a), records only, outcome-blind (nothing in DEC-031 Amendment 18's not-opened list was opened). It takes effect on merge with quant-proof's OK on the exact head and a reviewer verdict on the PR. It binds the copy of Amendment 2 in DEC-033 (Q50's canary copy, "copied as in the template") by reference; DEC-033's text is not edited.

**Lapse disclosed (corrected on QP-RULING-1027-s1 B2).** Amendment 2 merged as PR #860 at 2026-10-24T03:25:31Z. QP-RULING-1024-successor R7, which found item 12 internally inconsistent (12.1 "the same `frozen_sha` as Q50" cannot score any pool after 2026-11-06T00Z and cannot coexist with 12.2 (a)), was written from 2026-10-24T18:20Z, about 15 h later, so its deadline "before DEC-032 Am.2 merges" could not be met when it was written. The lapse is quant-proof's own: its OK-with-conditions on Amendment 2 (comment 6379106221) missed the 12.1/12.2 inconsistency. The deadline that binds is R7's other one, "before any 11-06 successor registration"; this erratum meets it. No successor has been registered, so no book was affected.

1. **Item 12.1, replaced.** "**The book.** A new `bot_id` with the same `rule_id` as Q50, at `frozen_sha` = the QP-ruled successor base S (QP-RULING-1024-successor R7; S\* = tag `q50-succ-base-1`, commit `5c2ab7c64b21358619e37068c6a53e4d183512ea`), not F50; F50 cannot score any pool with s0 >= 2026-11-06T00Z. It is registered in `bots.json` before its own clock, with its own H. It has its own DEC-032 copy, merged with QP OK and a reviewer verdict."
2. **Item 12.2 (a), clarified.** "A `SEAL_END_MS` code change" means: the successor's own `Book` row in `tools/h5_arena_executor.py` with its own `end_ms_seal` (its book end), and its own `FEED_NOTE_BOOKS` entry; Q50's `SEAL_END_MS` (2026-11-06T00Z) is not raised (raising it would widen Q50's executor observation past the EXP-024 Note under Amendment 10). The md5 decision-equivalence replay runs at that change's merge sha for Q50 (reproducing #1481's counts exactly) and for the successor.
3. **Item 12.2 (b), marked.** "(b) A late clock" yields no scorable trade at F50. At S it is a valid route that leaves a gap of at least 8 days; route (a) is the one used (DEC-031 Amendment 18).
4. **Item 12.3, changed (a substantive change, not an editorial one; QP-RULING-1027-s1 (a)).** It is binding before any successor registration. "Starting there rests on Q50's confirmation PASS for the same `frozen_sha`" reads "rests on Q50's confirmation PASS (DEC-031 §2 step 4 at p at most 0.05/c, QP OK on its exact files), with the successor at S, whose decision path quant-proof states in its OK on the successor's copy to be identical to F50's by the proofs of R7(b).3 (AST identity and md5 EQUAL)". The identity covers the shadow decision path (AST identity, seal grid, md5 #1548) and the executor's `decide_row` for the successor's row (its source-identity list and md5 replay EQUAL); it says nothing about a regime change between the two windows, and the copy states that. **Without a Q50 confirmation PASS there is no carry-over at any rung**: the successor's C0 then needs its own screen PASS (§1 item 1), as for any book. A Q50 screen PASS alone carries nothing.
5. **Unchanged:** every other word of Amendment 2, including item 12.4 (C1 -> C2 needs the successor's own gate read PASS, the item 5 checks over the successor's own live trips, and its own owner line), the rungs, caps, worst cases and halts. No book has cleared the promotion gate. Nothing here is a profit claim.

## Amendment 6: HV4 C0 copies read §1 item 4, §5 item 2, P7 at C0 and the owner line as DEC-031 Amendment 21 (DRAFT)

**Status: DRAFT until merge**, with quant-proof's OK on the exact head and a reviewer verdict on the PR. It is in the same PR as DEC-031 Amendment 21. Number = the next free DEC-032 amendment at merge: Amendment 5 is held by the q50-c0-feed draft (`/data/mal/hunt-1008/ops/reports/2026-10-30/q50-c0-feed-DEC032-Am5-DRAFT.md`, code branch `claude/q50-c0-gap-hold`); a renumber is a fill. Written 2026-10-30T15:39:03Z, revised 2026-10-30T15:56:51Z on quant-proof's comment 6527127124 (C5) (`date -u` on mal-research-0), records only, before any HV4 read verdict and before any HV4 C0 send. Scope: a DEC-032 copy for an HV4 `bot_id` (`hv4-*`) or for its successor (`hv4s1-*`, DEC-031 Amendment 21 item 1).
1. **§1 item 4.** For CON-L and BIG-L, a playbook version written by the book's own registered reviewer under DEC-031 §1.3 is not a code or prompt change (DEC-031 Amendment 21 item 2). This is the DEC-032 amendment that QP-RULING-1025-hv4-read-rule R2.2 (a) requires before an L arm is canaried. CON-P and the R arms stay excluded. It lapses with DEC-031 Amendment 21 item 2 (a merge at or after 2026-11-01T01:35:00Z).
2. **§5 item 2, and P7's `gap` share at C0,** read as DEC-031 Amendment 21 item 3.
3. **§2 owner answer, and Amendment 1 item 3 "Use":** the bridge reads the owner line from the root-owned file of DEC-031 Amendment 21 item 4. No sha256 of that file or of the copy is compiled into code. The copy still quotes the owner's line verbatim.
4. **In each copy.** Each HV4 copy lists items 1 to 3 as changes from the template (the status line above says a copy lists every change). Every other word of DEC-032 is unchanged. No book has cleared the promotion gate. Nothing here is a profit claim.


## Amendment 5: §5 item 2 "Feed gap" for canaries fed by `tools/h5_shadow.py`: a redundant-socket reconnect is not a gap, and a data-loss gap holds buys for 60 s (DRAFT)

*(Appended after Amendment 6, which merged first as PR #1101; #1101 reserved the number 5 for this text.)*

**Status: DRAFT, NOT IN FORCE.** Written 2026-10-30 (`date -u` 15:24Z at start; moved into this file 15:46Z) by a worker for manager18 (lane q50-c0-feed), records only, before any Q50 C0 send. Outcome-blind: no shadow output, ledger row content, decision row, `bots.json`, job status of #1419/#1421/#1543, or P&L was opened. It takes effect only when all of these hold:
- (a) this text merges with quant-proof's OK on the exact head and a reviewer verdict on the PR;
- (b) the code, PR #1085 (branch `claude/q50-c0-gap-hold`, head `d7e8bf9d2945fcb1add77dfbbba75fe3a7010b06`), merges with its own QP OK and reviewer verdict, **together with or after this text** (the reviewer's condition on #1085, comment 6526887133);
- (c) the canary's install sha contains that code, with these proofs at that sha (quant-proof's condition 2 on #1085):
  - the md5 decision-equivalence replay EQUAL (#1481 counts: A 137/137, B 101/101, B synthetic_unconfirmed 0/0);
  - the N4 rerun at X (QP-PRERULING-1027-xorder Q2);
  - a 1 h keyless dry run started inside 14:00-16:00Z (so that it is not confounded by time of day), reporting ledger counts by (kind, reason). Every `feed_reconnect_redundant` row must have `kind_` = `socket_reconnect` (any other kind is a held row ledgered under the wrong name; item 4). The startup limits must show `gap_hold=60000`, and `--status` must show `c0_guard=ok`.

The md5 replay already ran at #1085's head: EQUAL (job #1852, `sh scripts/ops/q50-xtime-md5-job.sh d7e8bf9d…`, with #1481's counts). Item 6b is **not** part of what takes effect here. It has its own conditions (item 6b); quant-proof found it acceptable in principle at C0 only (report on #1085, item 6).

**Relation to PR #1101** (DEC-031 Amendment 21 item 3 and DEC-032 Amendment 6, HV4 C0 feed latch, written by another lane, open at writing).
- Same rule shape: coverage-losing records hold new buys for 60,000 ms, with a 60,000 ms floor checked at start. A record that left some socket subscribed and delivering holds nothing and marks nothing. A live trip is marked `gap` by the paper read's own rule (§1.4). No human acknowledgement gates a buy.
- Different gap records: this amendment covers the `tools/h5_shadow.py` gap records (Q50's executor). Amendment 21 item 3 covers the HV4 bridge's arena tape and forwarder records. Neither changes the other's path.
- Two differences, stated:
  - (i) Amendment 21 item 3.3 adds a 24 h review latch: an unreviewed coverage-losing record more than 24 h old latches `feed_break`. It is not adopted for Q50; quant-proof ruling in its verdict on PR #1103 (issuecomment-6527330373: Q50's "manager has seen" clause was never built, so leaving the latch out loosens nothing that ran; Q50's SOL guards are per pool and need no human).
  - (ii) Amendment 21 item 3.4 adopts "P7 `gap` share at C0 blocks step-ups, no `STOP`" for HV4. Here the same rule is item 6b. It takes effect only with its own code PR and proofs, and at C0 only, with C1 unchanged.
- Numbering: #1101 reserves Amendment 5 for this text and takes Amendment 6. PR #1091 (Q50 owner-line file) labels its rule "DEC-032 Am.6 (DRAFT)". That label collides with #1101 and must be renumbered by the PR that writes #1091's DEC text. This amendment does not cover #1091.

A canary whose install sha does not contain the code runs under §5 item 2 as written for its whole run (no switch mid-run; the Am.3 item 6 pattern).

**Starting point.** The proposed text of audit rank 1, `/data/mal/hunt-1008/ops/reports/2026-10-30/audit-rules.md` line 13. Items 1-3 follow its first two clauses, with one difference: the hold is 60 s, not the 20 s its figure assumed (the reason is in item 2). Its third clause (P7 at C0) is item 6b, with its own conditions and code PR.

**Scope.** Canaries whose feed is the `gap` record stream of `tools/h5_shadow.py` and whose executor is `tools/h5_arena_executor.py`: Q50 (`h5v1q50-boostfloor-a1`), and a later H5 book on the same executor when its copy declares this amendment. **Not** the FLOOR dry executor (`tools/floor_arena_executor.py`, host/engine-restart gaps keep 10 minutes) and **not** the HV4/HV6 bridge path. Their feeds write different gap records; §5 item 2 stays as written for them.

**Basis (measured; no outcome data).**
- Helm's keyless 1 h dry runs at Y = c8fcf761 (HELM-REPLY-1028-1 §4, HELM-REPLY-1029-1). Ledger counts per hour were 149 and 135 `feed_reconnect_redundant`, and 47 and 42 `feed_gap`, against 2 and 0 in the 10-24 hour. The executor holds buys for 600 s on every one of them (`tools/h5_arena_executor.py:1226-1229` at main c92f23e3). So C0 would almost never buy.
- Census of mal-fast-0 at 2026-10-30T15:12:55Z (job #1839: `ps` and `ss`, no book file opened). The Q50 shadow (`--sockets 3 --syn-sockets 1 --ws-url wss://api.mainnet-beta.solana.com --ws-url wss://public.rpc.solanavibestation.com`) held 3 established connections, all to api.mainnet-beta (64.130.55.23), and **0** to public.rpc.solanavibestation.com (148.51.214.9).
- A websocket handshake to wss://public.rpc.solanavibestation.com is rejected with **HTTP 429**: from mal-fast-0 at 15:14:57Z (job #1842) and from mal-research-0 at 15:14:11Z. An HTTP `getHealth` to the same host returns 200 from both.
- Code at main c92f23e3 (the frozen shadow `tools/h5_shadow.py` is byte-identical to a5f41370). A refused handshake counts as a reconnect (`observe/trade_source.py:300`), with jittered backoff capped at 60 s (`:52-53`, `:497`). Each 5 s look with a reconnect writes a `socket_reconnect` gap (`tools/h5_shadow.py` `note_feed_stats`). `flags_pools` is true only when `common_down` finds an instant at which **every** socket was down or silent (`:536`). A socket that never comes up counts as down for the whole run (`observe/link_state.py`, class docstring).
- Load on fast-0's IP from api.mainnet-beta. Read with `ss -tnpi` by job #1856 at 15:29Z: three 10 s windows, connections that survived each window only.
  - Total received: 3.012, 1.486 and 4.282 MB/s, which is 90.4, 44.6 and 128.5 MB per 30 s.
  - By process: the Q50 shadow 1.967, 0.398 and 2.205 MB/s; the ubuntu `mal-fast-trade-tape` trial (`observe.trade_tape --sockets 2 --venues bonding,pumpswap`, running since about 10-02) 1.034, 1.078 and 2.060 MB/s; `fast_chain_probe --public-only` 0.011-0.018 MB/s.
  - Solana's public-RPC documentation gives a per-IP limit of 100 MB per 30 s. That limit is documented; we did not verify it.
- Drop history of the other consumer on the same IP and provider (`/var/lib/mal/logs/fast-trade-tape.log`, counts only, jobs #1853/#1855/#1856):
  - 1,400 to 3,500 reconnects per day since 10-11. Closes: 37,177 with code 1006. Handshake rejections: 13,283 with HTTP 413 and 3 with HTTP 429.
  - A strong daily cycle: about 0-5 reconnects per hour at 06-10Z and 100-210 per hour at 14-23Z.
  - In Helm's hours: 10-24 06Z 11 and 07Z 0 (the clean dry hour, 06:08-07:08Z); 10-28 16Z 157; 10-29 14Z 92 and 15Z 183 (the bad dry hours).
- **Reading (inferred; not proven, since the shadow's own per-socket stats are sealed).** Two causes act together.
  - (1) The svs endpoint now answers 429. One of the shadow's 3 sockets retries forever, at about one reconnect per 30 to 60 s at the backoff cap, which is about 60 to 120 `feed_reconnect_redundant` rows per hour. The 10-24 06Z hour had only 2, so svs was probably still working then.
  - (2) In busy UTC hours this IP takes about the documented per-IP data limit from api.mainnet-beta, and the server drops connections from the IP (code 1006). Without svs, the shadow's 2 live sockets are on that one provider and IP. A drop that hits both at once is a common outage: a `feed_gap` with flagged pools.
  - Helm's clean-versus-bad comparison (06Z against 14-17Z) is confounded by time of day.
  - The trade-tape trial is the only other public-websocket consumer of api.mainnet-beta on fast-0. P66 #1718 and the tip follower use other hosts (Helius / Cloudflare peers, 0.000 MB/s in the windows). The fast create listener uses PumpPortal. The HV6 producer runs on research-0.

### 1. A redundant-socket reconnect is not a gap for §5 item 2
A shadow `gap` record whose `kind` is exactly `socket_reconnect` **and** whose `flags_pools` is the literal JSON `false` does two things only: it is ledgered (`feed_reconnect_redundant`, base class) and it is counted in the manager's daily review. It does **not** stop new buys, and it does **not** mark a live trip `gap`. Code: `gap_loses_data(row)` returns False for this one shape only (`tools/h5_arena_executor.py:348@d7e8bf9d`). Why:
- (a) The shadow writes `flags_pools` false only when its per-socket link state finds no instant at which every socket was down or silent. Silent means no notice for 10 s (`SILENT_MS_DEFAULT`), or for 3 s while a peer delivered after it (`REL_SILENT_MS_DEFAULT`). So at every instant some socket was subscribed and delivering. The merged stream dedups by signature, so a print one socket missed came from another.
- (b) Such a record flags no pool (`tools/h5_shadow.py:1330`, `if flag_pools:`). The paper read excludes a trade only on a pool flag (`tools/arena_h5_score.py:435` `gap_in_span`). §5 item 2 says a gap trip is excluded "by the same rule that excludes it from the paper read". Marking live trips on these records was stricter than the paper rule. It made almost every live trip non-eligible, so P7 would soft-halt at 2 gap trips (Amendment 1 item 1).
- (c) Each trigger is still checked for completeness on its own pool (item 2 below).

### 2. Every other gap record is data loss: no new buys for 60 s
Every other `gap` record counts as data loss and fails closed. That covers `slot_jump`, `silence`, `feed_restart`, a `socket_reconnect` with `flags_pools` true, a missing, null or non-bool `flags_pools` (0 and "false" included), `false` on any kind other than `socket_reconnect`, an unknown kind, and a non-dict record. On such a record new buys stop at once. They restart once **60 s** pass with no further data-loss record (`GAP_HOLD_MS = 60_000`; each new record restarts the 60 s). Config may lengthen the hold up to 600,000 ms and never shorten it, so the old 10 minutes stays one config line away. Every trip whose exit has not landed is marked `gap`, as before.

What could go wrong buying after a gap, and what already blocks it (paths at the code head):

| Risk | Existing check (not changed) |
| --- | --- |
| The trigger was computed from an incomplete print set of its pool | The shadow flags every pool open at a data-loss record; such a trigger carries `gap: true` and is refused `feed_gap` with **no expiry** (`tools/h5_executor.py:1455`). It flags a pool announced before the gap (`announced_before_gap`, `tools/h5_shadow.py:1470`). It never tracks a pool whose CreatePool it missed (`require_announce`, `:1188`). The executor refuses an s0 more than 2 slots after the CreatePool (`S0_ANNOUNCE_LAG_MAX`, `tools/h5_executor.py:107`) and any `base_breaks_unresolved_settled` other than 0. That is the exact base-reserve chain: one missed print gives at least 1 (`:655`). |
| Stale price, or a backlog burst after a reconnect | Signal age at most 3.5 s (min of `SIGNAL_AGE_MAX_MS` 5,000 and `PRESEND_MAX_TRIGGER_AGE_MS` 3,500), judged before the gates, after them and at the `sendTransaction` call. Chain age against the executor's own getSlot history (`stale_trigger_chain`, `:1467`), the block-time age and `s0_recv_late`. `price_moved` on a fresh pool read (cache miss only, `:1660`). The **on-chain `min_out`** at the trigger price / 1.15 (`entry_tolerance_bps` 1500) is always there: a worse fill reverts. |
| Wrong pool state | The buy is built from the pool's own accounts read by RPC (`_static_for`), not from the feed. Pool or mint mismatch is refused. |
| Missed BOOST slice | Q50's exit pin is v1: a 330 s timer from s0 on the executor's own slot clock. BOOST rows feed only the structure halts, and a gapped pool's row is ignored (`boost_row_ignored`, `:2287`). No buy decision reads them. |
| Exit timing | The v1 exit plan uses s0_slot, sps and the executor's own getSlot history. No feed input. |
| The shadow reports a gap late | The latest report: a silence after 20 s plus one 5 s look. A common outage after `silent_ms` 10 s + 2 looks + 5 s margin = **25 s** (`tools/h5_shadow.py:274-275`). 60 s is more than 2 x 25 s and 3 x the base class's own 20 s floor. |

What the 60 s does **not** protect, said plainly: the per-pool checks above are the money guard, and the time hold is defence in depth on top of them. Under the feed measured on 10-28 and 10-29 (42 to 47 data-loss records per hour), and assuming they arrive as a Poisson process (they are probably clustered, which lowers the figure), buys would be held about 1 - e^(-47/60) = 54% of the time. Under the old rule the share was about 100%. In the 10-24 hour (0 data-loss records) the share is 0.

### 3. The live `gap` mark follows the paper read's rule
A live trip is marked `gap` only by a data-loss record (item 2). A buy decided before the last data-loss record and landing after it is marked at landing, as before (`tools/h5_arena_gates.py:240` `note_gap`, `on_buy`). P7 and Amendment 1 item 1 are unchanged by items 1 to 5; item 6b, under its own conditions, is the only change, at C0.

### 4. "and the manager has seen the gap record"
No sha of `tools/h5_arena_executor.py` has ever implemented this clause: there is no acknowledgement path, and buys restarted on the clock alone. The clause is replaced by the following. Every gap record is ledgered with its kind (`feed_gap` / `feed_reconnect_redundant`, field `kind_`). The manager reviews the per-kind counts each day. No human acknowledgement gates a buy. Disclosed here, not newly loosened. DEC-031 Amendment 21 item 3.3 (PR #1101) adds a 24 h review latch on the HV4 path. It is not adopted for Q50; quant-proof ruling in its verdict on PR #1103. Optional, not a condition: the daily tie-out lab note (DEC-033 §6 item 6) carries the per-kind gap counts so the daily review can be checked; revisit before C1. **Ledger case.** The base class names a gap row from `flags_pools` alone (`tools/h5_executor.py:2266-2267`). So a row with `flags_pools` false and a kind other than `socket_reconnect` is recorded as `feed_reconnect_redundant`, although the executor holds buys on it and marks open trips (`gap_loses_data` fails closed; item 2). The frozen shadow a5f41370 never writes that shape (only `socket_reconnect` can carry false). The daily review counts `feed_reconnect_redundant` rows by `kind_`, and treats any kind other than `socket_reconnect` as a held data-loss record.

### 5. Tripwire
`c0_guard_problem` refuses a live start if `GAP_HOLD_MS` or `GAP_HOLD_FLOOR_MS` is below 60,000. It also refuses if `gap_loses_data` stops treating every fail-closed shape of item 2 as data loss, or starts treating the item 1 shape as data loss (`gap_rule`).

### 6. What this does not fix (disclosed; feed remedies are a separate decision)
- With the feed as measured (svs at 429), a 330 s trip sees at least one data-loss record with probability about 1 - e^(-47 x 330 / 3600) = 0.987 (Poisson assumption). Almost every live trip would then be `gap`, and P7 soft-halts at 2 gap trips. The same flags exclude paper trades (`gap_flag`) and threaten Q50's 5% cap (NOT_DECIDABLE). This amendment does not make C0 measurable on that feed. It removes the redundant-churn block, and it removes the 10-minute block on a healthy feed.
- Feed remedies on the host side, none in this amendment and none touching #1419:
  - stop or move the ubuntu `mal-fast-trade-tape` trial, or cut it to one socket. It is about 1-2 MB/s of the IP's 1.5-4.3 MB/s from api.mainnet-beta. Before doing so, check what reads `/var/lib/mal/sealed/fast-trades-trial` (`tools/tape_coverage.py`'s default; possibly the fast forward-paper runner). docs/HOSTS.md lists the unit as "installed, disabled", yet it runs.
- Other feed remedies: restore a second working provider. A new endpoint for Q50 is a records change (HANDOFF §3). Future books (s1, Q60) choose their endpoints at registration. The research-0 arena feed's svs sockets are also refused (429 from that host).

### 6b. P7 `gap` share at C0 (audit rank 1): its own condition
Quant-proof ruled this **ACCEPTABLE in principle, at C0 only**, in its report on #1085 (comment 6526931175; `/data/mal/hunt-1008/ops/reports/2026-10-30/qp-1085-gap-hold.md` item 6). This item does **not** take effect with items 1 to 5. It takes effect only when all of these hold:
- (a) this text is in force;
- (b) the code PR #1106 (branch `claude/p7-c0-gap`) merges with its own QP OK and reviewer verdict, after or together with this text;
- (c) the canary's install sha contains that code, with item 6's proofs (d) and (e) of the QP report: the old-vs-new golden on synthetic fixtures, and the X-time manifest with `tools/arena_live_parity.py` as a fifth allowed path pinned by exact blob and sha256, plus its tamper probe.

**The rule.**
1. For a DEC-032 C0 canary whose parity tool is `tools/arena_live_parity.py` (Q50), and **at rung C0 only**, P7's `gap`-share part makes parity NOT_DECIDABLE. That part is a `gap` share above 5% from 20 live trips on, or 2 `gap` trips before 20 (Amendment 1 item 1).
2. That breach sets `match` false, and the verdict is "parity not shown". It is not a halt and creates no `STOP`. Every step-up stays blocked: the ladder steps up only on `match` true (`tools/h5_arena_ladder.py:315`).
3. The share never resets. Every `gap` trip is reported separately, and it counts inside every money total: daily loss, total stop, committed loss and wallet floor.
4. Unchanged, and still soft halts at C0:
   - P7's `no_twin` share and its small-count rule;
   - an `unreadable` trip;
   - a zero-row twins file past an exit;
   - missing, unreadable or stale state.
5. **At C1, and whenever the rung is missing or unknown, P7 stays exactly as written: a soft halt** (fail closed).

**Cost, said plainly.**
- On `gap` trips P1, P3, P4 and P6 are blind, because they use eligible trips only. Only P2, P5, the other P7 parts and the C0 stops then guard execution quality.
- On the feed measured on 10-28 and 10-29, the canary could spend up to its 0.20 SOL total stop (worst case 0.25 SOL to the wallet floor, §3) while producing almost no eligible trips. C1 would then be unreachable for the canary's life.
- This is a liveness change, not a fix for the feed.
- It is the same rule as DEC-031 Amendment 21 item 3.4 (PR #1101) for HV4 C0.

**Manifest.** For an install that contains the code of (b), the X-time manifest allows five changed paths: Amendment 4's four (executor per item 7) plus `tools/arena_live_parity.py`, at the blob and sha256 pinned in that PR's `scripts/ops/q50-xtime-manifest.sh`. Amendment 4's "exactly FOUR" is superseded for such an install. HELM-SHEET is re-pinned at X.

### 7. Re-pins (made in this records PR unless said otherwise)
- **Executor pin of Amendment 4 item 1.** For the executor line, this supersedes Amendment 4 item 1's "the 5b blob, unchanged by this amendment: blob 70d789ff, sha256 ef8ae931...". The X-time executor is #1085's blob `53337e087f672b685db3c4c43ec46ad70a1119ac`, sha256 `4f5f443a71fa14606f57a75c210e2b006659dcc3217c03f841d8682d17ee9359`. This is the `tools/h5_arena_executor.py` blob of #1085 at head `d7e8bf9d2945fcb1add77dfbbba75fe3a7010b06`. `scripts/ops/q50-xtime-manifest.sh` is re-pinned to it in #1085 itself (`EXEC_BLOB`, `EXEC_SHA256`), and its probe `sh scripts/ops/test_q50_xtime_manifest.sh` passes at that head. The four-path count and the 43-path list (sha256 `244f9e85…`) do not change. **If review changes #1085's executor, this pin and #1085's manifest pin move together, before either merges.** Any later PR that changes `tools/h5_arena_executor.py` (#1091, #1106) moves this pin, both Am.4 annotations, checklist row b' and the HELM-SHEET patch to the combined blob, in a records change merged with or before that PR, with an md5 replay EQUAL at the combined blob. At X the records name the same executor blob as `scripts/ops/q50-xtime-manifest.sh` at X, or no `mode=live` starts. **Re-pin record 1 (2026-10-30, PR #1091, DEC-032 Am.7 owner-line file):** the combined executor is blob `8369ab21349b568efd0eda4fd64fde2e90510bca`, sha256 `7e6d2f0e57388c0c3ea40eb85f6c208ad07aa2389067da70f0de6ca9679abfe0` (main 6117c35c, which holds #1085, plus #1091). The manifest, both Am.4 annotations, checklist row b' and the HELM-SHEET patch move to it in #1091. The md5 replay at #1091's head is recorded in that PR. **Re-pin record 2 (2026-10-30, PR #1106, Am.5 item 6b):** the combined executor is blob `b96f023fe64eadd63a2d377e08a0d004a7038d20`, sha256 `9cb7ac22acc75247d8788044054349019c7656a656036421529e7da4d97f1fde` (#1085 + #1091 + #1106). `tools/arena_live_parity.py` is the fifth allowed path, blob `989e3de66ff5ac2784f5c210f3cde1263a9ddf88`, sha256 `d22aff3cb7cc0e583d72b2e1f0704725033b44239ff61d7b5a8dcd417dc1cd90`. These records, the manifest and the HELM-SHEET patch move in #1106. The md5 replay at #1106's head is recorded in that PR. Annotation lines are added under Amendment 4 item 1 here and in DEC-033; Amendment 4's own words are left as written.
- **DEC-033.** Its verbatim copy of §5 item 2 gets a reading note. Its `FILL:feed_gap_source` row and its list of changes from the template are updated (this PR).
- **Owed, not in this PR:**
  - HELM-SHEET: the patch is re-pinned here; only applying it to the host sheet is owed (manager). The sheet's move to X is an X-time checklist item, not part of this amendment.

### 8. Does not amend
- The promotion gate.
- Any read rule.
- The registered Q50 paper book, its frozen shadow a5f41370, its argv or endpoints, or its feed.
- §3 limits, P1 to P7, §6, §7, and Amendments 1 to 4, except the Am.4 executor pin (item 7) and, only under item 6b's own conditions, P7's `gap`-share part at C0 (Amendment 1 item 1) and Amendment 4's 'exactly FOUR'.
- Any other canary's executor.

No book has cleared the promotion gate. Nothing here is a profit claim.


## Amendment 7: Q50's owner line may sit in a root-owned file Helm writes; the owner line no longer has to be in the installed tree (DRAFT)

**Status: DRAFT, NOT IN FORCE.** Written 2026-10-30 (`date -u` 16:19Z at start) by a worker for manager18 (lane q50-c0-feed), records only, before any Q50 read verdict and before any Q50 C0 send. It is in the same PR as its code, PR #1091 (branch `claude/q50-c0-owner-file`), so the rule and the code merge together. It takes effect only when all of these hold:
- (a) this text merges with quant-proof's OK on the exact head and a reviewer verdict on the PR;
- (b) the canary's install sha contains #1091's code, with the md5 decision-equivalence replay EQUAL at that sha (#1481 counts) and a 1 h keyless dry run at that sha.

Source: the rules audit, rank 4 (`/data/mal/hunt-1008/ops/reports/2026-10-30/audit-rules.md`). The HV4 counterpart is DEC-031 Amendment 21 item 4 (PR #1101, merged), carried into HV4 copies by Amendment 6 item 3. Outcome-blind: no Q50 output, ledger, decision row, `bots.json` or job status view was opened.

**Scope.** Q50 (`h5v1q50-boostfloor-a1`) on `tools/h5_arena_executor.py`, and a later H5 book on that executor whose copy declares this amendment. Not HV4 or HV6: Amendment 6 item 3 and DEC-031 Amendment 21 item 4 cover HV4.

### 1. The rule
1. A live start of the Q50 executor needs the owner's line for its `bot_id`. Until now that meant a `DEC-*canary*.md` record in the **installed** tree (`canary_record_refusal`, the template excluded). It may now instead be the root-owned file **`/etc/mal-h5-arena/OWNER_CANARY`** (`ARENA_OWNER_CANARY_PATH`, a code constant). The file:
   - is written by Helm, as root, on the owner's own word: the owner's standing line of Amendment 1 item 3, or a fresh line;
   - passes `h.root_file_problem`, the same checks as the arena `LIVE_OK`: a regular file, not a symlink, root:root, mode exactly 0644, in a root-owned directory that is not group- or other-writable, with lstat and fstat on the same inode;
   - holds exactly one non-blank line: `OWNER_CANARY_CONFIRMED: bot_id=<bot_id>`, naming this `bot_id` exactly (`owner_canary_file_refusal`).
2. When neither the DEC record nor the file covers the book, the live start is refused with the DEC record's reason, as before. The DEC-record path itself is unchanged.
3. **No sha256 of the file, or of a DEC copy, is compiled into code.**
4. The DEC copy still quotes the owner's line verbatim (§2). It still merges with QP OK and a reviewer verdict before any send, and it records Helm's sha256 of the file and the instant it was written (records PR R).

### 2. What it replaces in the stage order (QP-PRERULING-1027-xorder, Stage 2 item 4)
- **Stage 2 item 4 reads** "Add the bare line `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1`. It must be in the X tree, because the installed executor reads DEC/ at X."
- **Under this amendment:**
  - The bare line goes into the DEC copy in git: the copy PR or records PR R, before Helm writes `LIVE_OK`.
  - The executor reads it from `OWNER_CANARY`, so it **need not be in the installed tree**.
  - A reinstall is needed only when the pinned-set blobs of the tree to run differ from the installed tree's (the X-time manifest decides). A tree installed before the read, with the same pinned blobs, keeps its md5 EQUAL and its dry run.
- **Not replaced:**
  - every other Stage 2 and Stage 3 item (read-dependent fields, `POSTX:` tokens, records PR R, N4, the 1 h dry run at the install sha);
  - the owner's own word for the key, the wallet, funding, `LIVE_OK` and `TIER`.

### 3. Rewrite latch: not adopted for Q50, and why
DEC-031 Amendment 21 item 4 does three things for HV4's file: the bridge checks the file before **every** live buy; at the first good check it pins the file's (st_dev, st_ino, sha256); and a later rewrite, replacement or removal latches `owner_line`. Q50 does not take that latch. Its file is checked at live start (in `arena_start_refusal`, re-run by the constructor), the same as the DEC record has been at every sha. The reasons:
- **The owner's word already gates every Q50 buy.** The arena's own `/etc/mal-h5-arena/LIVE_OK` must exist, pass the same root-file checks and name exactly this `bot_id`, and it is re-read before every live buy and every rebroadcast (`_live_gate`). Removing it stops new buys at once. The HV4 bridge pins the owner file because it uses that file as one of two per-buy gates; on Q50 that per-buy role is LIVE_OK's.
- **A rewrite needs root, and root also writes LIVE_OK.** The latch would only alarm on a change that the same principal can make to the per-buy gate anyway. It protects no SOL beyond the C0 limits and LIVE_OK.
- **It is not a loosening of anything that ran.** No Q50 executor sha ever re-checked the owner line after start. The DEC record was also start-only.
- **Cost of adopting it:** a new executor path, a state field, a latch name, a new blob, re-pins, an md5 rerun and a Helm reinstall. Under the PUSH-BRIEF owner directive, a check that protects neither money nor gate validity is not added.

**Two smaller differences from Amendment 21 item 4, disclosed:**
- Q50's file must be mode 0644 exactly (the LIVE_OK pattern). HV4's must be "not group- or other-writable".
- Q50's `root_file_problem` does not test link count 1, which HV4's text names. Only root can create a hard link to a root-owned file in a root-owned directory, so this adds no exposure that root could not create directly.

### 4. Why money and gate validity are unchanged
- The send is still gated by Helm's root-owned files written on the owner's word (`OWNER_CANARY` at start, `LIVE_OK` before every buy), by `TIER` T1, by the C0 limits of §3 and by every halt of §5.
- No read, window, trade set or verdict changes.
- The canary is not gate evidence.

### 5. Code and proofs (PR #1091)
- `owner_canary_file_refusal` and `canary_record_refusal(..., owner_file=None)` in `tools/h5_arena_executor.py`. Tests: a valid file covers the book without a DEC record; another book, a prefix, two lines, an extra line, a bare id, an empty file, modes 0664 and 0600, a symlink and a missing file do not; the DEC record path still covers.
- The executor blob moves under Amendment 5 item 7 (Re-pin record 1).
- md5 replay EQUAL at the PR head; the PR body records its provenance.
- `scripts/ops/test_q50_canary_record_check.sh` passes.

No book has cleared the promotion gate. Nothing here is a profit claim.

## Amendment 8: DEC-030 push-family books (EXP-027, r4-b): admission to this template, the C0 terms, the confirmation track and the rung R (records only, outcome-blind)

**Status: in force on merge, with quant-proof's OK on the exact head and a reviewer verdict on the PR.** Written 2026-10-30 (`date -u` 22:04Z at start of writing) by a worker for manager18 (lane r4b-live), from quant-proof's ruling `/data/mal/hunt-1008/ops/manager18/QP-RULING-1030-r4b-live.md` (§1 ruling (a), §2 ruling (b) items A1 to A9 and B1 to B3, §3 economics). The same PR adds DEC-030 Amendment 1 (what a push-family PASS leads to) and the ledger sub-entry of item B1. If another PR takes the number 8 first, this text is renumbered at merge; nothing else changes.

**Outcome-blind (seal statement).** This text was written before E = 2026-11-07T03Z: no walk-2 outcome of r4-b's counted window was computed or seen (its counted hours exist, sealed), and no mark of the confirmation window of B1 had happened. Its writer opened no file under `/data/mal/blocks` (forward-1016 included), `/data/mal/meta-capture`, the arena tape, `/data/mal/r4b`, any shadow, canary or probe output, `fresh-08*`, or any October hour, and called no job status view of a book runner. The only data read for the r4-b live path is the exploration block explore-0814, as md5s, counts and timings (MiScusi #1867, #1888, #1903, #1907). It changes no gate, no EXP-027 byte, no read rule, no k, no m, no seal, no DEC-031 text, and no limit, halt or term of any other canary.

**Merge deadline (verdict-blindness, QP-RULING-1030 §2).** This amendment merges by **2026-11-07T03:00Z (E)**. After E it may still merge only before the `r4b_read` lock, and only with a written statement on the PR that no r4-b outcome at or after W0 was seen. Text merged after the lock cannot serve r4-b's first canary or its step rules. After EXP-027's read, an r4-b copy of this template may fill FILL fields only; any other change is a new amendment and is refused for r4-b's first canary.

No book has cleared the promotion gate. r4-b has zero out-of-sample trades. Nothing here says r4-b makes money.

### Part A: admission and the C0 canary (needed for C0)

**A1. What a push-family PASS leads to.** DEC-030 Amendment 1 (this PR) replaces, for push-family books, "the DEC-018 path and the owner's yes" (DEC-030 §1 and §1a; EXP-027 :136, :419 and :579 point to it) with "a live canary under DEC-032 as amended by Amendment 8, and the owner's yes", and DEC-030 §5's "DEC-018 and the owner's yes govern anything live" with "DEC-032 as amended by its Amendment 8, and the owner's yes, govern anything live" (DEC-030 Amendment 1 items 1 and 1a). EXP-027 stays byte-unchanged. DEC-018 is EXP-012's proposal and is not used for push-family books.

**A2. §1 item 1, scope.** After "a registered arena `bot_id` (DEC-031 §1.2) and its one gate read (§2) returned **PASS** on its deciding cell …", add: "**or a DEC-030 push-family book whose one registered read returned PASS on every deciding cell, with quant-proof's OK on the read's exact files.**" For r4-b this is EXP-027 §6.4 PASS on all four cells (flat 15 % and pressure at slope scale 1, each at 1.3 s and 1.9 s) at day-level p ≤ 0.025/k (EXP-027 §6.2, k = `EXP027_K`), with gate items 1 to 7 of EXP-027 §6.3. The multiplicity block of the owner ask is k and m from the ledger, not DEC-031's N. A NOT_DECIDABLE, FAIL, compromised or unread push-family book never qualifies.

**A3. A canary is not a new book (EXP-027 §10.5). Verbatim (QP-RULING-1030 §1, ruling (a)):**

> "A canary of a DEC-030 push-family book executes only the frozen book's own decision rows, capped by this template's tier limits in rank order within a mark; every skipped decision is logged with its reason. Its stake S is an execution limit, not the book's operating point. Its results are an unpromoted execution measurement: never evidence for the book, never a gate or confirmation read, never added to either, and never a reason for a step. It is therefore not a smaller-K or smaller-stake book in the sense of EXP-027 §10.5 (QP-RULING-1016 §5), and that sentence keeps its meaning for any book that is registered, ranked or priced at another K or stake."

Conditions it rests on (QP-RULING-1030 §1, items 1 to 4): every live entry and exit is a row the frozen K = 10 engine emitted; S is an execution limit; the comparator is the paper twin of the same decision row at S and the executor's actual fees; no step above C0 is justified by C0's P&L. A run that re-ranks, keeps top-k with k < 10, or is registered or priced for a gate at another stake is a new book and needs its own EXP.

**A4. §1 item 2, worst-in-slot (the lane's X2).** The worst-in-slot clause applies to event-triggered books. r4-b is an hourly cross-section, so it does not apply. In its place the copy quotes the EXP-027 read's report-only 4.0 s leg (mean per trade and CI90 lower bound, under both fail models) as information, never as a deciding item.

**A5. §1 item 4, the frozen set (X3).** For r4-b the frozen set is the EXP-027 §2.2 pins plus `tools/r4b_live.py` at the install sha. The `start` row of the decision log carries the install sha as `frozen_sha`. **A precondition of the first send (QP N1):** at the install sha, after EXP-027's read is written, an event-V md5 decision-equivalence replay of `r4b_live` against the EXP-027 read's own deciding rows and trades on walk 2 `[W0, E)` is EQUAL (the exploration replays are static-map mode only; explore-0814 has no event V). Any change to the engine is a new install sha, and that md5 is re-run before it is used.

**A6. §3 and §5, the paper twin (X4).** For r4-b the twin is the registered pricing engine: `tools/r4b_book.round_trip` at stake S and the executor's actual per-send fees (escalated resends as sent), **nofail, 1.9 s leg, E1 excluded decides** (E1 Option B charge reported beside it). `tools/arena_paper.py` is not used for r4-b. The parity tool for r4-b is a build item that §1 item 6 requires before the first send (QP N7).

**A7. Exits, re-entry and which decisions go live (X6, X7, X8).**
- X6. No stop-loss backstop: r4-b has no stop (EXP-027 :145, :554). Exits happen only at hour marks, by the book's rank and gap rules. The only backstop is a time stop at 48 h + 1 h + 60 s after the entry decision (a position cannot stay in the universe past graduation + 48 h). The bridge clamps `sl_backstop` to (0, 0.60] (`tools/arena_live_bridge.py`), so this needs a reviewed r4-b profile override of the backstops, not a config value (QP N6).
- X7. A mint is released for a new buy once its sell has landed (r4-b re-buys a mint at a later mark; the bridge's default never re-buys).
- X8. Entries of one mark are taken in rank order while open positions are below the tier's max open and the day's buy count is below its cap. Every skipped entry is logged with its reason. An exit row for a position the canary does not hold is ignored and counted.

**A8. Latency origin (X9, QP N5; text of QP-RULING-1030 Erratum 1).** "For r4-b, every 'decision to landed' in §5 (P5 and the 'live fills match paper' clause) is measured on chain from slot sd(T), the paper twin's decision slot (the first slot whose block_time is at or after the mark T, as in the twin's own row; EXP-027 :258-261). The deciding quantity is d_land = landed slot − sd(T), in slots. 'Landing at most 1.9 s' means d_land ≤ np.round(1.9 / slot_s), and 'above 3.0 s' means d_land > np.round(3.0 / slot_s), where slot_s is the per-day value the twin itself uses (EXP-027's pinned slot_s rule and rounding). sd(T) is the twin row's slot. If the live decision log's `decision_slot` differs, the twin's sd(T) decides, and the difference is logged and counted. A real-time P5 may use the live `decision_slot` as a provisional origin and is re-evaluated on the twin's sd(T). The measure never counts from the decision-log row, the send, or the time our own feed first saw any slot. Wall-clock times (feed first-seen of sd(T), decision-row write, send, landing) are logged only as diagnostics of where the delay goes. They never decide, and a missing wall-clock field is not a P7 condition." Counting from the row, the send or our feed's first sight of sd(T) would hide the feed and compute delay (about 1.4 s and 1.0 s today, QP-RULING-1030 §3). No wall-clock first-seen writer is required before a C0 start (QP on PR #1126); it is a recommended diagnostic only.

**A9. The owner's line.** Amendment 1 item 3's standing line covers a book that meets every §1 precondition, so it covers r4-b at C0 once A2 is in force. The manager tells the owner in one line at this merge, and again before the first send ("Notice before the first send"). Wallet funding and Helm's install still need the owner, as today.

**C0 for r4-b.** Section 3's C0 column, unchanged: S = min(0.25, 0.05) = **0.05 SOL**, max open 2, 30 buy attempts per UTC day, daily stop 0.10 SOL, total stop 0.20 SOL (committed loss including open exposure), wallet floor max(0.05 SOL, funded − 0.25 SOL), worst case 0.25 SOL, at most 14 days from the first send. **Disclosure (QP-RULING-1030 §3, in-sample arithmetic, not evidence):** at 505,000-lamport sends a round trip costs 2.02 % of 0.05 SOL, against 0.404 % at the registered 0.25 SOL, so a C0's expected P&L is about −1.05 % to −0.44 % per trade at 505,000-lamport sends, and about +0.33 % to +0.94 % at 160,000-lamport sends (the lower figure moves only the fees, the higher also credits the smaller own impact at 0.05 SOL). C0 measures execution only: latency from sd(T), entry drift against the 1.9 s twin, landing fails, sell-and-close on r4-b's universe, and the live adapter's agreement with paper. It says nothing about r4-b's edge.

### Part B: any step above C0

**B1. The confirmation track (the lane's X5).** DEC-031 §2 step 4 does not apply to push-family books. For r4-b, the confirmation is:
- **Engine and stack.** The same frozen engine, unchanged (the EXP-027 §2.2 pins; decisions as `r4b_live` at the install sha, md5-equal per A5), on the registered deciding stack of EXP-027 §5 (the send fee the EXP-027 read used: 505,000 lamports, or 160,000 only if §5.3 applied at that read; rent 0 under §5.2, E1 Option B, auto-stranded, both fail models), at 0.25 SOL per position, K = 10. The book is flat at its first mark. Past-only features may read earlier hours (feature buffer only).
- **Counted attempts.** Attempts (EXP-027 :297 definition) with entry marks in `[2026-11-07T06Z, B − 1 h]`, where 2026-11-07T06Z = E + 3 h is the end of the EXP-027 read's allowlist, so every price this read uses is later than any hour the EXP-027 read tool reads. Every position still open at the B mark is sold at the B mark (EXP-027 :104's convention at E); hours are read to B + 3 h (exit horizon 1 h plus the 2 extra trade hours of the adapter).
- **Read instant B (outcome-blind).** The first UTC-date boundary (00:00Z) at which at least 5 complete UTC dates lie in `[2026-11-07T06Z, B)` and at least 100 attempts are counted, both from attempt timestamps only, by the md5-pinned count tool (decisions only, no price, fill, exit or P&L). The earliest possible B is 2026-11-13T00Z (complete dates 11-08 to 11-12). **Cap:** B ≤ 2026-11-14T00Z. If the stop is not met by the cap, or any hour in `[2026-10-16T03, B + 3 h)` is missing or not strict-verified, the confirmation is NOT_DECIDABLE and no step above C0 is possible.
- **Deciding.** On all four EXP-027 cells (flat and pressure × 1.3 s and 1.9 s; the larger p decides), all of: (i) the CLAUDE.md promotion-gate items on the confirmation attempts (at least 100 trades; at least 5 distinct UTC dates with attempts, a majority positive; CI90 lower bound of mean SOL per trade > 0, 1,000 bootstrap draws, seed 1, 5th percentile, trades ordered (T0, mid) as EXP-027 §6.3 item 3; total SOL > 0 after removing the top 3 trades); (ii) total SOL > 0 after dropping the best UTC date (DEC-030 §1a binding term); (iii) the one-sided day-level t of DEC-030 §1a over the W UTC-date blocks holding attempts (the partial date 11-07 counts as one block; empty blocks dropped; df = W − 1) at **p ≤ 0.05/c**, c = the number of push-family books that have entered a confirmation by that read (r4-b alone: c = 1).
- **Low W (disclosed).** W = 6 or 7 is near DEC-030's floor of N ≥ 7 for a first read, so (iii) has low power. That costs r4-b a step, not validity: this read gates a step, not the promotion, and it never changes EXP-027's verdict.
- **One read.** No interim look. A FAIL or NOT_DECIDABLE ends r4-b's canary at the next 00:00Z (§7 last bullet) and allows no step; the owner may extend C0 for execution measurement only.
- **Seal.** Before B, no person, agent or job computes, opens or prints a paper outcome of any r4-b decision at a mark in `[2026-11-07T06Z, B]` (landing state, guard, fill, exit state, proceeds, P&L, sign), from any source, except the C0 canary's per-trip paper twins, which DEC-032 §7 already allows inside a confirmation window under its concurrent-observation disclosure: nothing seen there changes this read's rule, data or instant, and the confirmation report carries that disclosure. The count tool's outputs (attempt counts per date) are allowed. A breach is recorded in the ledger sub-entry and the read is reported compromised; it never turns a FAIL into NOT_DECIDABLE.
- **Tool.** The confirmation read tool is `tools/r4b_read.py`'s deciding path with this item's constants (window, B, cap, alpha 0.05/c), as a separate module or wrapper, merged with quant-proof's OK on its exact head **before B**. It opens only `/data/mal/blocks/forward-1016` hours `[2026-10-16T03, B + 3 h)`, counts only marks in `[2026-11-07T06Z, B − 1 h]`, and runs only after EXP-027's read is written.
- **Ledger.** The confirmation's hours are inside the reserved Forward walk 2 row, so this PR adds a counted-window sub-entry to that row's Status cell (DEC-030 §2a; DEC-014 rule 1; Rules item 5). It is not a new book: it does not raise k, m or the push-family count.
- **Walk-2 coverage and credits (disclosed; the manager re-checks at E).** The read needs walk 2 sealed and strict-verified through B + 2 h (2026-11-13T02 for the earliest B; 2026-11-14T02 at the cap). At the walker's own rate, 5,869,765 of 11,825,000 credits were used through 2026-10-30T13 (MiScusi job list, #1036), about 16.9k credits an hour, so the remaining 5,955,235 last to about 2026-11-14T06Z [arithmetic; the rate can rise]. That is about 28 h of margin at the earliest B and about 4 h at the cap. The wrapper stops before a partial hour when the cap is near (exit 5). If the margin shrinks, the manager raises the walker's credit cap in a successor job at the same decoder ref (`578a63ea…`, EXP027_DECODER_REF) before 2026-11-12, recorded in this sub-entry; otherwise the confirmation can end NOT_DECIDABLE. Successor #1290's 14-day limit (from its start after gate #1289, about 2026-11-03T18Z) runs past 2026-11-14.

**B2. The rung R replaces C1 for r4-b.** At 505,000-lamport sends C1 (0.10 SOL, 3 open) is fee-dominated (round trip 1.01 % of stake, central in-sample net about −0.04 %) and adds no information over C0. For r4-b, section 3's C1 column and section 7's "Step C0 to C1" and "Beyond C1" bullets are replaced by one rung:
- **R: the registered stake, 0.25 SOL (250,000,000 lamports) per position, max open m ≤ 10**, with m set by the owner's funded capital (DEC-020 governs size). Positions are a rank-ordered capped subset of the book's own rows (A3, X8); with m < 10 the realised mix is reported beside the full-book twin, never as the book's mean.
- **Step C0 → R needs all of:** the B1 confirmation PASS; §5 "live fills match paper" shown with the A8 latency origin (at least 100 eligible trips; CI90 lower bound of mean d_i ≥ −100 bps and B3; median d_land ≤ np.round(1.9 / slot_s) slots from sd(T); P7 off); no unresolved halt; at least 5 daily tie-outs signed off with no residual; the sell builder unchanged (EXP027_SELL_BUILDER, `tools/probe_live.py@b188a40b…`, or a builder proven by test to sell and close in one transaction, EXP-027 §5.2 (i)); and the owner's own dated line naming the deposit D, m, the stops below and the duration. Then Helm writes `TIER` as root.
- **R's limits (the writer's proposal for the manager and quant-proof; the owner's line may only lower them).** Capital at work per position c = 0.253 SOL (stake plus about 0.003 SOL of buy fee, ATA rent reserve and reserved sell fee). Lifetime committed-loss cap L = m × (c + 0.10) SOL, counted from the first send of the canary as Amendment 2 item 4 counts it (open positions at zero). Slack s = 0.05 + 0.01 × m SOL. Worst case = L + s. Wallet floor = D − (L + s), never below 0.05 SOL, so m ≤ (D − 0.10) / 0.363. Daily trigger: realized loss since 00:00Z plus open positions marked at their pool quote reaching −0.05 × m SOL holds new buys until 00:00Z (positions exit on their own rules). Intraday pre-send cap (Amendment 2 item 3(b) pattern): today's realized loss plus every open, pending or in-flight position at zero with its fees and reserves plus this buy's worst case ≤ m × c + 0.05 × m SOL. Buy attempts per UTC day ≤ 15 × m.

  | Deposits D | m | L | worst case | floor |
  |---|---|---|---|---|
  | 1.29 SOL (owner capital today) | 3 | 1.059 | 1.139 | 0.151 |
  | 2.00 SOL | 5 | 1.765 | 1.865 | 0.135 |
  | 3.00 SOL | 7 | 2.471 | 2.591 | 0.409 |
  | 3.73 SOL | 10 | 3.530 | 3.680 | 0.050 |

  Planning figures, in-sample, not evidence: 0.1462 SOL a day central at m = 10 (hunt13 / JUDGE-PUSH §3); in-sample maxDD 3.149798 SOL at m = 10 (EXP-027 :545).
- **Beyond R** (a stake above 0.25 SOL) is a different stake, so it is a new book under EXP-027 §10.5 and needs its own EXP.

**B3. Parity tolerance sized to r4-b's edge (a tightening; §5 allows a copy to tighten).** For the step to R, "live fills match paper" also needs: (the EXP-027 read's mean net SOL per trade at the 1.9 s leg, the lower of the two fail models, in bps of the 0.25 SOL stake) + (the CI90 lower bound of mean d_i, in bps) > 0. DEC-032's −100 bps bound alone would let live trail paper by more than r4-b's whole central edge (+56.52 bps in-sample, shrunk). If the read's figure is at or below 0, R is not reachable.

### What this amendment does not do
- It changes nothing for arena books, Q50, HV4, HV6 or any other canary.
- It does not register a book, open a slot, change k or m, move a window or release a seal.
- It authorizes no send: the first send still needs the EXP-027 read PASS, quant-proof's OK on the read, the r4-b copy of this template merged with every FILL filled, the A5 md5, the parity tool, Helm's DRY install with a 1 h dry run, and the owner's notice.

## Amendment 8, Erratum 1: the r4-b restart exit and the 52 h warm-up (records only, outcome-blind)

**Status: in force on merge, with quant-proof's OK on the exact head and a reviewer verdict on the PR. It must merge before E = 2026-11-07T03:00Z** (Amendment 8, "Merge deadline"): after EXP-027's read an r4-b copy may fill FILL fields only. Written 2026-10-31 (`date -u` 00:36Z at start) by a worker for manager18 (lane r4b-live), from quant-proof's OK on PR #1136 at `c80910ba` (its condition 1) and QP-RULING-1030 Addendum 3. Seal statement: written before E; no outcome of EXP-027's counted window was computed or seen, and no mark of the B1 confirmation window had happened; no file under `/data/mal/blocks`, `/data/mal/meta-capture`, the arena tape, `/data/mal/r4b`, or any shadow, canary or probe output was opened. No book has cleared the promotion gate.

**Why.** Amendment 8 item A7 (X6) says "The only backstop is a time stop at 48 h + 1 h + 60 s after the entry decision". PR #1136 adds two more pieces that bind a live r4-b canary, and the copy needs them in the DEC text:

1. **Restart exit (a backstop, not a book decision).** When the r4-b decision host restarts, its book is empty and it cannot see positions opened before the restart. On start, before it decides anything, it appends one `exit` row with reason **`restart`** for every entry in its decision log that has no exit row (`tools/r4b_live.restart_exits`). The executor follows `restart` exits like the book's own exits (it sells the held position). Each such trip is marked **`gap`**: it is **excluded from parity**, **counted** (`exit_restart`), counts toward **P7** (two such trips before 20 trips, then the 5 % share, Amendment 1 item 1), and is **never a P5 / landing-latency sample**. Repeated restarts therefore soft-halt the canary. In A7, "The only backstop is a time stop …" reads "The backstops are the time stop at 48 h + 1 h + 60 s after the entry decision and the restart exit of this erratum; neither is a book decision."
2. **Warm-up (no buys after a host start).** A restarted or newly started host emits **no entry rows** until it has ingested **52 h** of block time (`DEFAULT_WARMUP_S` = the 48 h grid + 4 h for the longest print window), with a flat book, as EXP-027's own H0 → W0 ramp does. Exits and the gap hold are not affected. The warm-up gives the frozen engine a clean start, not byte-equality with a full-history engine: the create-derived and bonding constants of mints created before the start keep the EXP-027 §3.4 skew, and the book starts flat. **A shorter warm-up, or a replay-on-start (walker hours after the read, then the arena tape, then the live feed), is allowed only with its own md5 decision-equivalence proof against the full-history engine**, recorded with its job id before the first send that uses it. Quant-proof's scheduling note: the host can start DRY at least 52 h before the planned first send; a DRY host writes decisions, not outcomes, and its decision log stays sealed (names and counts only) under EXP-027 §11 until the read and under Amendment 8 item B1 for marks from 2026-11-07T06Z until B.

**Does not change.** The C0 limits, P1 to P7 and their thresholds, A8 (latency in slots from sd(T)), the confirmation of B1, the rung R, B3, any gate, EXP-027 byte, k, m or seal. Nothing here creates a buy: both pieces only add exits or suppress entries (quant-proof on #1136: "No buy without an engine row").

## Amendment 8, Erratum 2: the r4-b live-feed bars (decision agreement and the C0 latency go/no-go) (records only, outcome-blind)

**Status: in force on merge, with quant-proof's OK on the exact head and a reviewer verdict on the PR. It must merge before E = 2026-11-07T03:00Z** so that it binds r4-b's first C0 send (after EXP-027's read an r4-b copy may fill FILL fields only; Amendment 8, "Merge deadline"). Written 2026-10-31 (`date -u` 00:44Z at start) by a worker for manager18 (lane r4b-live), from QP-RULING-1030 Addendum 4 (bars 1 and 2, fixed before any data they judge exists). Seal statement: written before E; no outcome of EXP-027's counted window was computed or seen, and no mark of the B1 confirmation window had happened; no decision row of any live or DRY r4-b host exists yet; no file under `/data/mal/blocks`, `/data/mal/meta-capture`, the arena tape, `/data/mal/r4b`, or any shadow, canary or probe output was opened. No book has cleared the promotion gate.

**Scope.** These bars apply when r4-b's live decision host is fed by any stream other than the walker's own hours (for example the public arena feed merged with a Helius mark-window socket, QP-RULING-1030 Addendum 3). They are added to Amendment 8's preconditions of the first send (A5 and the "What this amendment does not do" list). They only tighten.

**Bar 1, decision agreement (a precondition of the first C0 send).** Run after EXP-027's read is written. Compare the live-stream engine's entry rows (a DRY run of the frozen engine at the install sha, fed by the live stream) with **the EXP-027 read's own deciding rows** (its book's entries) on the same marks, over **at least 48 marks**:
1. **At least 95 % of the live entry rows are paper-book entries at the same mark, and at least 95 % of the paper entries are live entries.**
2. **Every** live-only or paper-only entry is attributed to a named cause: a fork or dropped slot, deduplication, null tx_index order, event_ts vs blockTime, an unresolved pool, or warm-up or state. **An unexplained difference blocks the first send until it is explained.**
3. **Every live-only entry has paper rank ≤ 15 at its mark, and every paper-only entry has live rank ≤ 15.** A difference from deeper ranks means a feature fault, and it blocks.
4. The per-mark share of identical top-10 entry sets is reported, as information only.
This comparison is decision-level and outcome-blind (it opens no landing, fill, exit or P&L). It does not replace A5's event-V md5 of the walker-fed engine; both are required.

**Bar 2, which commitment decides, and the C0 latency go/no-go.**
1. **Processed commitment may supply the decision inputs.** It never moves the A8 origin: d_land counts from the twin's sd(T) (walker or chain). The live `decision_slot` remains only the provisional real-time P5 origin (A8).
2. Prints the engine used from slots that confirmed or finalized later dropped count as `fork` differences under Bar 1.
3. The latency proof reports both commitments: d_ready, d_decide and d_would_send = slot − sd(T), in slots, with the per-day slot_s. **Go/no-go for the first C0 send: p50(d_would_send) + 4 slots ≤ k_1.9 = round(1.9 / slot_s), over at least 48 marks on mal-fast-0 with the T − 5 s precompute.** The 4 slots are the upper send-and-land allowance. If it fails, C0 waits for the feed and compute work; no exception.
4. The 4-slot allowance never enters the match clause or P5. Only measured live landings do.

**Does not change.** The C0 limits, P1 to P7, A8, B1, R, B3, Erratum 1, any gate, EXP-027 byte, k, m or seal. It creates no buy and authorizes no send.

## Amendment 8, Erratum 3: Bar 1 causes, the go/no-go read rules, incomplete marks, one trigger per run, and the replay-on-start fallback (records only, outcome-blind)

**Status: in force on merge, with quant-proof's OK on the exact head and a reviewer verdict on the PR. It must merge before E = 2026-11-07T03:00Z and before the r4-b DRY host's first counted mark**, because it fixes how the go/no-go of Erratum 2 is read. Written 2026-10-31 (`date -u` 01:25Z at start) by a worker for manager18 (lane r4b-live), from QP-RULING-1030 Addendum 5 ("Erratum 3" items 1 to 4, rulings (a) and (b), and the scheduling finding). Seal statement: written before E and before any DRY r4-b host has run; no outcome of EXP-027's counted window was computed or seen; no mark of the B1 window had happened; no file under `/data/mal/blocks`, `/data/mal/meta-capture`, the arena tape, `/data/mal/r4b`, or any shadow, canary or probe output was opened. No book has cleared the promotion gate.

1. **Bar 1's named causes (Erratum 2, Bar 1 item 2) gain two:** "late arrival" (an input of the mark ingested after its decision instant, counted per mark by the host as `late_inputs`) and "a held or abandoned mark" (credit cap, gap hold, incomplete inputs; logged with its reason). Downstream divergence after one of these stays "warm-up or state". The denominators keep the literal reading: the paper entries of held and abandoned marks count as paper-only entries.
2. **The go/no-go read rules (Erratum 2, Bar 2 item 3):**
   - (b1) k_1.9 is the **smallest** round(1.9 / slot_s) over the counted dates, slot_s from the host's own clock points.
   - (b2) Only full UTC dates (24 measured hours) count; a date is left out only because its slot_s is not final, never because of its marks' latency, and every date the host ran is reported. **Warm-up marks never count.** Held and abandoned marks are left out of the p50 and reported by reason.
   - **Read once:** at the first UTC-date boundary with at least 48 counted marks, over all counted marks up to that boundary. A NO_GO is re-read only after a declared change to the feed or compute path (a new install sha, or a dated config line with a `change_id`), recorded before the new run's first counted mark. No re-running until a GO appears.
3. **Incomplete marks (b3; QP-RULING-1030 Addendum 5 ruling (b3)).** The host never decides, and never writes a rank exit, on incomplete inputs. When the window cannot certify (failed, or not complete), the host waits up to the bound fixed in its config for the public-stream fallback: **the engine's clock guard plus the public stream itself past sd(T) (public high-water ≥ sd + 1, no open public gap at sd)**. It then calls decide(hold_entries=True): exits written, entries suppressed, status `late_public`, flagged. Only if that fallback also fails by the bound is nothing decided (`abandoned`), and the next decided mark's gap rule applies. The fallback is synthetic-tested; it needs no md5 of its own, because decide is unchanged, but it is in the same install sha as everything else.
4. **One trigger per run (ruling (a) condition 3).** The decision trigger (the engine's one-chain-second guard, or the source's full completeness signal with a certified sd, where decide refuses if its own sd differs) is fixed at the run's start and written in its start record. The DRY run on which Bar 1 and the go/no-go are read uses the trigger the C0 host will use. A trigger change is an engine change: a new install sha, A5's event-V md5 re-run at it, and a new run.
5. **Scheduling and the DRY replay-on-start alternative (QP-RULING-1030 Addendum 5, "Scheduling finding", binding arithmetic, quoted).** "Bar 1 compares against the read's own deciding rows, and those exist only at entry marks in [W0, E − 1 h], last 2026-11-07T02:00Z. The 48 counted marks must be post-warm-up DRY marks, so the earliest counted mark is 2026-11-05T03:00Z. With the 52 h warm-up, the DRY host must be running on the live merged stream by 2026-11-02T23:00Z, with zero slack. […] If the date cannot be met: a **DRY** host may warm by replaying the arena tape for [start − 52 h, start) before going live. Erratum 1 requires its md5 proof only 'before the first send that uses it', and a DRY host sends nothing. Bar 1 itself tests the resulting decisions. Same QP terms as the live DRY host: decisions only, decision log sealed (names and counts), no join to HV4 / Q50 outputs. If the C0 send host is that same process or inherits its state, Erratum 1's md5 applies before its first send."

**Does not change.** Bar 1's 95 % / rank-15 terms, Bar 2's go/no-go inequality, A5, A8, the C0 limits, P1 to P7, B1, R, B3, Errata 1 and 2, any gate, EXP-027 byte, k, m or seal. It creates no buy and authorizes no send.

## Amendment 8, Erratum 4: the r4-b DRY host's source configuration (tip tape as history, Helius window for the last slots), the "tip gap" cause, the tip catch-up as the DRY warm-up, memory, and the F2 backstop (records only, outcome-blind)

**Status: in force on merge, with quant-proof's OK on the exact head and a reviewer verdict on the PR. It must merge before the r4-b DRY host's first counted mark and before E = 2026-11-07T03:00Z.** The ledger changelog line and Status-cell note of the same PR must be in force before the P-C3 smoke, the first process that reads the tip tape. Written 2026-10-31 (`date -u` 05:39Z at start) by a worker for manager18 (lane r4b-live), from QP-RULING-1031-r4b-history (`/data/mal/hunt-1008/ops/manager18/QP-RULING-1031-r4b-history.md`, sha256 `85fa8801fde2b3c48010b628282690b40fb97110a308777a4ffd959eba886b71` as read at 05:36Z; option C, §2.1 to §2.5, §4 R3 items (1) to (6), §5, §6, §7). Seal statement: written before E and before any DRY r4-b host has run; no outcome of EXP-027's counted window was computed or seen; no mark of the B1 window had happened; no file under `/data/mal/blocks`, `/data/mal/meta-capture`, the arena tape or its mirror, `/var/lib/mal/sealed` (the tip tape included), `/data/mal/r4b`, or any shadow, canary or probe output was opened. No book has cleared the promotion gate. r4-b has zero out-of-sample trades; nothing here says it makes money. This erratum is about inputs, seals and proofs only.

**Why.** The ruling on the public arena tape: "Every public data-loss gap writes a declog `gap` row, and a gap row holds buys." "At 6,724 `slot_jump` + 154 data-loss reconnects + 6 silences in 52 h, a public gap falls within the 10 min before every mark," and Erratum 3 item 1 counts a gap-held mark's paper entries as paper-only, "so Bar 1 item 1 (≥ 95 % each way) fails by construction." The ruling: "A and D cannot reach a C0 that buys without weakening the in-force gap rule. I do not accept that change for chronic, real data loss." And: "The public stream is not complete by construction, and the tip follower is." The follower decodes with the walker's own code and, since 2026-10-10T13:17:17Z, runs event V with decoder blobs `observe/trade_decode.py` 238942a6, `observe/trade_store.py` ea4e11ed and `tools/pump_history_backfill.py` 9a8bebb3, which are the `EXP027_DECODER_REF` blobs of walk 2's job (EXP-027 §2.2). The ruling chose option C: the tip tape as history, the public tape dropped, the Helius window owning only the last slots.

1. **The source configuration (ruling C; R3 item (1)).** The r4-b DRY host (and later a C0 decision host at the same install sha) takes its history from the fast-0 tip tape, and the Helius mark window owns the slots in (D, sd). The public arena tape is not an input of this configuration.
   - (a) **Reader scope.** Only the pinned host reads `/var/lib/mal/sealed/fast-trades-tip`, and only the files the follower writes there: `trades-`, `creates-`, `migrations-` and `skipped-slots-<hour>.jsonl` and `gaps.jsonl`. It opens them read-only (never writes, moves or truncates) and reads hours at or after (start − 53 h) only. Code refusals (tested): any file whose hour is before 2026-10-16T01; any path outside that directory; `/data/mal/tip-tape-archive` and any other copy. The follower, its unit, its tree and the daily archive job are untouched, and this lane never restarts `mal-fast-tip-follower`. If the job user cannot read the directory, the manager grants read-only access to that one user on that one directory (an ACL r-x on the directory with a default r on files, or a read-only bind), recorded in LAB_STATE row 115 with the `date -u` time: no write bit, no other path. Catch-up reads run at `nice -n 19 ionice -c3`; the smoke shows the follower's `lag_slots` and `backlog_jumps` do not move during catch-up.
   - (b) **Tip health, failing closed.** At start and at every mark the host reads the follower's `status.json` and requires `decoder_blobs` = the three `EXP027_DECODER_REF` blobs, `decoder_blobs_pinned` true, `trade_event_v` true and `ts_ms` fresh (≤ 30 s). Otherwise the tip side is unhealthy, the mark cannot certify, and (f) applies. If start is refused, nothing runs.
   - (c) **D is the checkpoint's `last_done`.** D is read from `/var/lib/mal/fast-tip-follower/checkpoint.json`, not from `status.json`. `status.json` is rewritten only every `HEARTBEAT_S` = 15 s (up to about 75 slots stale); the follower writes `checkpoint.json` atomically after every slot, once the slot's rows are appended and flushed. When it shows `last_done = D` (with `writing` null), every row of every slot ≤ D is already in the files, and D is contiguous: every slot ≤ D is a block, a recorded skip or a recorded gap. Protocol (tested): read `checkpoint.json` (D), then drain the tip files to EOF. Files are bucketed by the hour of `t_recv_ms`, not block time, and marks fall exactly on that boundary, so the reader tails the current and the next hour's file.
   - (d) **Slot ownership, not key dedup.** The ruling: "Do **not** reuse HostMerger's key dedup across the two decoders. Tip rows come from the walker decoder; Helius window rows come from the arena / logs mapping. Whether their `event_index` agrees is unproven." So: **tip owns** every slot ≤ D, where D is checkpoint `last_done` at decide(T), and its rows go to the engine through the walker-schema path (`ingest_trade` / `ingest_create` / `ingest_migration`, the md5-proven replay path), **not** through `r4b_feed_adapter`. **Helius owns** the slots in (D, sd): at decide(T), and only for those slots, the window's buffered rows are ingested through the adapter, as built. Helius rows of slots ≤ D are discarded; Helius rows of slots ≥ sd are discarded (tip will deliver them). When tip later delivers a Helius-owned slot, its rows are dropped (counted `tip_dropped_owned`), never double. Ingestion stays in slot order. If tip later reports a Helius-owned slot as skipped, that is a `fork` (Erratum 2, Bar 2 item 2).
   - (e) **Certification (ruling (a); replaces "seam closed").** Every one of these must hold: `mark_complete` on the window (healthy, armed, covered, Helius processed tip ≥ sd + 1, no silence); D ≥ (first window-covered slot − 1); no tip gap open in (first covered slot, D]; the tip side healthy (b).
   - (f) **b3 for the tip configuration (Erratum 3 item 3, restated; in this configuration it takes the place of the public-stream fallback).** "If the window cannot certify, wait up to the bound for the clock guard **plus tip D ≥ sd + 1 with no open tip gap at sd** (about 1.5–2 s after the mark). Then decide(hold_entries=True), status `late_tip`. Otherwise the mark is `abandoned`." As in Erratum 3 item 3: exits written, entries suppressed, and the host never decides, and never writes a rank exit, on incomplete inputs.
   - (g) **The Helius window stays as built:** armed at T − 35 s, processed commitment, a cap of 50,000 credits a day at the 3-credit rate (about 1,744 credits per mark in #1949, about 42k a day).
   - (h) **Output.** The host writes decisions only. It computes no price, landing, fill, exit state or P&L, makes no join to HV4, Q50 or arena-book outputs, and reports nothing restricted to `[s0, s0 + 360)`. Agents never open the tip files, not even to list names; they may read `/var/lib/mal/fast-tip-follower/status.json` and `checkpoint.json` (slots, counters, latencies, blobs). Counts by reason on `gaps.jsonl` stay manager-only practice.
2. **Fixed per run (R3 item (2); extends Erratum 3 item 4).** The source configuration is fixed per run and written in the run's start record, beside the trigger of Erratum 3 item 4. A source change is a new run and a new `change_id`, recorded as in Erratum 3 item 2 before the new run's first counted mark.
3. **Bar 1's named causes gain "tip gap (flagged data loss)" (R3 item (3)).** When a follower counter moves (`gaps`, `skipped`, `backlog_jumps`, `unresolved_dropped`, in `checkpoint.json` and `status.json`), the host reads the new `gaps.jsonl` rows (slot ranges and reasons only) and treats each range as data loss in the existing G2 machinery: a `gap` row (buy hold); the seam held over the range until the 1,500-slot release (QP-RULING-1030 Addendum 6); and the Bar 1 cause is "tip gap". `unresolved_dropped` is the existing cause "an unresolved pool". The Helius-owned tail (item 1(d), about 5–15 slots per mark at processed commitment, with `event_ts` for block_time and a null tx_index; its rows stay in state, so later marks inherit them) falls under the existing causes "event_ts vs blockTime", "null tx_index order" and "fork". The denominators keep the literal reading of Erratum 3 item 1: the paper entries of held and abandoned marks count as paper-only entries.
4. **The tip catch-up is the DRY warm-up (R3 item (4); Erratum 3 item 5 for the tip source).** The ruling: "for the tip source config, the catch-up is the DRY run's warm-up without the 'if the date cannot be met' condition of Erratum 3 item 5. The first live mark after catch-up completes is a counted mark ((b2) warm-up exclusion satisfied)." The catch-up ingests 52 h of block time (not more unless memory allows), which meets Erratum 1 item 2's gate ("ingested 52 h of block time"). Under this configuration the catch-up rows are the same rows the live tip stream delivers; a live warm-up would instead carry Helius-owned tail slots at 52 marks. **Unchanged (Addendum 6 C2; Erratum 3 item 5, last sentence):** a catch-up state never becomes a send host's state without Erratum 1's md5 proof. The tip retention is 72 h, so a 52 h catch-up always fits.
5. **Memory: eviction of per-pool state (R3 item (5); applies only if an eviction change ships).** The horizon: a member's per-pool horizon is its whole life from s0 to graduation + 48 h, not 4 h (`models/r4b/features.txt` holds since-seed and whole-life features; the longest rolling window is 4 h).
   - (i) `Engine.prune(now_bt)` (print state of mints graduated more than 48 h + 2 h ago; bonding state of never-completed mints idle for 7 d) is already in code and horizon-safe. The ruling: "The host must call it every mark." If it does not today, wiring it in is a host change in the new install sha and is covered by the P-C2 md5.
   - (ii) **Allowed, with the md5 proof:** (M2) drop the print arrays of graduated pools whose cached membership is negative (non-PDA, V0 outside [17.5e9, 17.7e9], Mayhem, no s0 inside the s0 window); market minutes are built at ingest and are not touched. (M3) compact a member's prints older than T − 4 h − 10 min into exact cumulative state (counts; integer-lamport sums; float64 running sums in arrival order, which equal `np.cumsum`'s sequential sums; the trader-hash set; the running ATH / ATL with index and time; the creator cumsums), so `features_at` reads cumulative state plus the window arrays. Proof: (1) unit tests, features byte-equal to the pinned `mint_features` at every grid mark of random mints with eviction active; (2) P-C2's two-window explore-0814 md5 (lane window #1867: rows `f1317f7b2fc2f1d8cb1c603d475c9c4f`, trades `c079bae1c3abe86ae1b1551cb7b6e441`; QP window #1907: rows `0866f58614701e70d493a1b936708f29`, trades `0ef2f42543af0acba400747c20a87571`), with the eviction counters > 0 in the job output; (3) mutation checks: evicting at graduation + 47 h, or dropping one cumulative, must break a test or the md5. It is an engine change: a new install sha, A5's event-V md5 re-run at it, and this item.
   - (iii) **Not allowed** (lossy, not decision-equivalent): truncating a member's prints to the 4 h rolling horizon without carrying since-seed state; narrowing dtypes; pruning market or create minutes; shortening the 7-day bonding-idle rule; evicting any state of a held mint before its exit row.
   - (iv) **Measure first.** The P-C3 smoke reports `memory.stat` anon vs file. "If the anon peak is ≤ about 1.6 GB, ship no eviction change: use `posix_fadvise(DONTNEED)` on the catch-up files and keep the job at 1.9 GB. Only if anon is over that does M2 (cheapest), then M3, go in with their proofs."
6. **F2, the calendar backstop (R3 item (6); written now so that it is outcome-blind).** If no DRY run reaches 48 counted marks in `[W0, E − 1 h]`, Bar 1 (Erratum 2) is run instead against the deciding rows of the B1 confirmation (item B1: entry marks in `[2026-11-07T06Z, B − 1 h]`), after B. This applies in particular if neither C nor E is live by the ruling's Bar 1 hard date, 2026-11-05T02:59Z ("first counted mark ≤ 11-05T03Z gives ≥ 48 marks in [W0, E−1h], last 11-07T02Z, zero slack"). Only the reference set changes: Bar 1's terms (95 % each way, the named causes, rank ≤ 15), Erratum 3 items 1 and 2, and the Bar 2 go/no-go stay as written. The comparison is decisions only and is made after B; the B1 seal holds until B, and the DRY host's decision log for marks in `[2026-11-07T06Z, B]` stays sealed (names and counts only) until then. The ruling's estimate: F2 moves C0 to about 11-13..11-14.
7. **Why the decision-log seal matters more.** The ruling: "The tip tape is a near byte-copy of walk 2's own content: the same decoder, getBlock, real tx_index and block_time. So the DRY host's decision log at marks in [W0, E−1h] will be close to the read's own deciding rows. That is no outcome, but it makes the decision-log seal **more** important, not less: names and counts only until the read, and a breach is an EXP-027 §11 breach." The seal is unchanged: the decision log is sealed under EXP-027 §11 until the read and under Amendment 8 item B1 for marks from 2026-11-07T06Z until B (names and counts only). The tip tape is walk-2-hour information under DEC-030 §2 R1 and inherits the seals of the ledger row its hours fall in ("never a back door", ledger 2026-10-15); EXP-027 §3.6's D3 ruling is unchanged (feature-only use at decisions at or after s0 + 600 s is not D3 use, and the engine's D3 drop applies).
8. **Fallback E is not recorded here.** The ruling's §5 names E (public mirror history plus a getBlock fill of flagged public gap ranges) as the fallback only if C is blocked: P-C0 fails (follower down, unpinned or event V off) and cannot be fixed by the manager under the runbook by 2026-11-02T00Z; or read access cannot be arranged; or P-C2 or P-C3 fails and cannot be fixed by 2026-11-03T12Z. Its records ("Erratum 4 in its E variant; the §5 item 2 text for 'filled'") are written only if that trigger fires, and before E = 2026-11-07T03:00Z. The ruling: "**E is not equivalent to the walker's view**", and "public silent loss (unflagged)" would become a named cause.

**Proofs.** The ruling's §3 (P-C0 to P-C3, then the outcome-free tip-vs-walker structural comparison after the read) are the proofs of this configuration. This erratum records no result of them.

**Does not change.** Bar 1's 95 % and rank-15 terms, Bar 2's go/no-go inequality, A5, A8, the C0 limits, P1 to P7, B1, R, B3, Errata 1 to 3 (Errata 2 and 3 stay in force for any other source configuration), any gate, EXP-027 byte, k, m or seal. It creates no buy and authorizes no send.
