# DEC-FILL:review: Arena live canary, HV4 `hv4-BIG-X`, copy of DEC-032 with Amendments 1 and 3 (STAGE-2 SKELETON, DRAFT)

> **SKELETON, NOT A DEC, NOT IN FORCE. Lives in `ARTIFACTS/lab/c0-drafts/hv4/` on purpose.** Written 2026-10-30 (`date -u` 15:2xZ) by the hv4-c0-ready lane, records only, from DEC-032 at main `c92f23e3` (sections 0-10, Amendment 1 and Amendment 3 copied verbatim by script; Amendments 2 and 4 and the Am.2 erratum are Q50-only and not carried) and the PR #958 HV4 arm draft (superseded by this file). No HV4 output, ledger, decision row, `bots.json`, read record, job log or status was opened. No book has cleared the promotion gate; nothing here says any HV4 arm makes money.
>
> **Use (stage 2, only after `hv4-BIG-X`'s one Am.15 read is RECORDED with status PASS):** copy this file to `DEC/DEC-0NN-hv4-big-x-c0-canary.md`, delete the three `>` skeleton notes above the table, fill every `FILL:gate-read` (figures from the recorded read, the `.wis.json` and QP's check), `FILL:review` (DEC number, PR head, review ids, QP's seal statement, mint fetch, simulate) and `FILL:owner` (only from the owner's own words). The stage-2 PR is RECORDS ONLY: no code changes at event time (manager decision 2026-10-30: the owner confirmation is a root-owned file whose presence and exact line the bridge checks, with no sha pinned in code; PR `claude/hv4-c0-start-gate`). QP OK on the exact head and a reviewer verdict on the PR; the manager merges. The install sha X is the main sha Helm installed (it must contain the code PRs of section 11 item 12, and may predate this DEC). Every `POSTX:` value is filled after the install by a stage-3 records PR R, as in DEC-033.
>
> **Markers.** `FILL:gate-read` = a figure from the recorded read or its WIS record. `FILL:review` = a stage-2 fact (DEC number, head, review ids, QP statements, mint fetch, simulate). `FILL:owner` = a line only the owner's own message fills. `POSTX:` = a fact that exists only after X (stage-3 companion file). Check before the PR: `grep -cE 'FILL:(gate-read|review|owner)([^A-Za-z_-]|$)' <file>` must print 0, and `grep -cx 'OWNER_CANARY_CONFIRMED: bot_id=hv4-BIG-X' <file>` must print 1. Before stage 3: `grep -c 'POSTX:' <file>` must print 0.

| Field | Value |
| --- | --- |
| **Status** | **DRAFT COPY of DEC-032 (with its Amendments 1 and 3) for `hv4-BIG-X`. NOT IN FORCE.** It becomes this book's canary DEC only when `hv4-BIG-X`'s Am.15 read is recorded as PASS on the deciding cell, every `FILL:gate-read`, `FILL:review` and `FILL:owner` marker is filled, QP OKs the exact head, a reviewer verdict is on the PR, and the owner line is added on the owner's word (standing line, DEC-032 Am.1 item 3). A canary is an "unpromoted execution measurement" (DEC-032 §0). No book has cleared the promotion gate. |
| **Decider** | Vaan (owner), for each live send, wallet funding and tier. No agent message, manager message or PR text is the owner's consent. |
| **Builds on** | [DEC-031](DEC-031-forward-paper-arena.md) §2 (screen, canary, confirmation), §4 and Amendments 2, 3 (item 6 canary prerequisites), 4 (item 1.1), 6 and 15 (items 1 to 8: HV4 read instant, live consumer, worst-in-slot); QP-RULING-1025-hv4-read-rule R2.1 and R2.2 (a) to (g) (host text sha256 c2343c9c…, quoted in docs/rules/hv4.md section 4b C3); [DEC-032](DEC-032-arena-canary-template.md) (template, Am.1, Am.3); HV4 registration `ARTIFACTS/lab/hv4-registration-1023.md`; [DEC-024](DEC-024-h5-live-canary.md) §4 to §8 and Amendment 3; [DEC-019](DEC-019-execution-probe.md) Amendment 2 and `ARTIFACTS/lab/feearm-landing-2026-10-23.md`; `ARTIFACTS/lab/probe-final-2026-10-07.md` |
| **Does not amend** | The promotion gate and its thresholds. DEC-031's deciding cell, one read per `bot_id`, the Am.15 read rule, the confirmation rule or taint list. Any HV4 sibling bot. DEC-024 and its canaries. Any seal. The ledger. |

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

## 9a. Fill values of this copy
| Field | Value |
| --- | --- |
| `FILL:dec_number` | FILL:review (the next free DEC number at the PR; DEC-035 is free at main `c92f23e3` on 2026-10-30; a second HV4 copy takes the one after) |
| `FILL:bot_id` | hv4-BIG-X |
| `FILL:frozen_sha` | 9ed4c5ff63935696b2a89150e13565e123047754 (`hv4_live_c0.FROZEN_SHA`; registry line, `ARTIFACTS/lab/hv4-registration-1023.md:80`) |
| `FILL:clock_start_ms` | 1792886400000 (2026-10-25T00:00:00Z) |
| `FILL:read_instant` | FILL:gate-read (B of the write-once instant record `/data/mal/arena/reads/am15/hv4-BIG-X.instant.json`; the pack expects 1793318400000 = 2026-10-30T00:00:00Z, HV4-READ-PACK-1031 §2; the as-of 1793404800000 = 2026-10-31T00:00:00Z; the record path and its sha256 from the job's `RECORD` line; the instant the record was written, which is R2.2 (d)'s start) |
| `FILL:n` | FILL:gate-read (trades: Am.15 item 2 trade set at stake 0.25; also quote O and R of Am.15 item 1 from the instant record) |
| `FILL:d` | FILL:gate-read (distinct UTC trade-entry dates, QP-RULING-1029 D3) |
| `FILL:mean` | FILL:gate-read (deciding 1.9 s cell at 0.25, flat and pressure, weakest sub-cell named) |
| `FILL:ci_lo` | FILL:gate-read |
| `FILL:p` | FILL:gate-read (one-sided bootstrap p, the larger of the two fail models) |
| `FILL:ex_best_day` | FILL:gate-read (every deciding sub-cell; `not_proposed_for_canary` must be false) |
| `FILL:wis_mean` | FILL:gate-read (from `hv4-BIG-X.wis.json`, written once by `scripts/ops/hv4-wis-job.sh` after the record; "not measured" or a mean <= 0 = no canary, Am.15 item 6) |
| `FILL:wis_lo` | FILL:gate-read |
| `FILL:N` | FILL:gate-read (arena books in the registry with a clock before the read, stopped ones included, their cells, how many read; HV4 alone has 9 siblings) |
| `FILL:holm` | FILL:gate-read |
| `FILL:qp_comment` | FILL:review |
| `FILL:reviewer_comment` | FILL:review |
| `FILL:head_sha` | FILL:review |
| `FILL:install_sha` | POSTX:install_sha (the main sha Helm installed on mal-fast-0, with its manifest; it must contain every PR listed in section 11 item 12, including the one-line `LIVE_REFUSAL_ACTIVE = False` flip with its own QP pass; it need not contain this DEC) |
| `FILL:seal_status` | FILL:review (QP's statement: HV4 trades lane-G arena tape; no H5 / EXP-024 Look-2, C1-NF or D2/D3/D4 class applies, lane H is off (registration Note N2); R2.1 respected; ledger `docs/HOLDOUT_LEDGER.md` checked at `date -u`) |
| `FILL:book_stake` | 0.25 (registry `deciding_stake`; `ARTIFACTS/lab/hv4-registration-1023.md:80`) |
| `FILL:canary_stake` | S = min(card stake, 0.05 SOL) = 0.05 SOL = 50,000,000 lamports for every card size 0.25 / 0.5 / 1.0 (`hv4_live_c0.c0_stake`, `C0_LIMITS["stake_lamports"]`); impact at the deciding stake 0.25 is NOT tested |
| `FILL:universe` | quote WSOL and V in [17.5e9, 17.7e9] lamports inclusive: `tools/hv4_cards.py:458-465@9ed4c5ff` (frozen) and `hv4_live_c0.UNIVERSE_V_RANGE`, `WSOL_MINT` @X (a test compares them with the bridge's band) |
| `FILL:venue_exit_rule` | PumpSwap; persona v0 plan defaults (rule-book arm, no model): stop -25%, target +100%, trail 30% (arms after +50%), time stop 360 min; code limits hard stop -40%, max hold 720 min, max open 6, 3 entries per hour, day loss stop 1.5 SOL (`tools/hv4_arms.py:79-83@9ed4c5ff`). Exits are the book's own exit rows; bridge backstops: time stop at `max_hold_ms` + `BACKSTOP_EXTRA_MS` and stop-loss with a warm sell plan; `max_hold_ms` at least 43,200,000 (720 min, the persona's max hold) so the book's own exit row can run; the bridge adds BACKSTOP_EXTRA_MS |
| `FILL:executor` | `tools/arena_live_bridge.py` profile `hv4_c0` over `tools/probe_live.LiveExecutor` (BridgeExecutor); unit `mal-hv4-c0-bridge@hv4-BIG-X` (install pack PR `claude/hv4-c0-install-pack`); config sha256 POSTX:config_sha256; manifest POSTX:manifest_sha256 |
| `FILL:c0_constants` | `tools/hv4_live_c0.py` `C0_LIMITS` @X: stake 50,000,000; max open 2; 30 buys a day; daily stop 100,000,000; total stop 200,000,000 (committed loss incl. open exposure, `total_stop_problem`); `FUNDED_MIN/MAX_LAMPORTS` 350,000,000 / 1,000,000,000; `FLOOR_BELOW_FUNDED_LAMPORTS` 250,000,000; `MIN_WALLET_FLOOR_LAMPORTS` 50,000,000; `config_refusal` refuses anything looser |
| `FILL:builder_path@sha256` | `tools/probe_live.py` `LiveExecutor._start_sell` + `close_token_account` (base ATA closed under the mint's own token program in the same tx), pinned by `hv4_live_c0.SELL_BUILDER_PIN` (symbol sha256 a1931a78…, plus the exit snapshot builder and the 0.85 / 0.65 ladder) @X; `sell_pin_problem` runs at every live start |
| `FILL:simulate_ref` | FILL:review (job id and record sha256s of `tools/hv4_c0_sell_simulate.py` on one Token and one Token-2022 public September mint, both rungs, through the bridge's pinned builder; PR `claude/hv4-c0-sell-simulate`. `tools/arena_sell_simulate.py` drives the H5 builder and does NOT qualify for this book) |
| `FILL:fee` | buy 55,000 / sell 55,000 / escalated 150,000 lamports of priority, plus the 5,000 base fee per send (`hv4_live_c0.PRIORITY_PIN` @X, equal to `h5_executor` H5_DEFAULT, a test asserts it); escalation after 2 reverts or 345,000 ms; declared gap to the registered DEC-031-rev2 stack (505,000 per send): 2 x (505,000 - 60,000) = 890,000 lamports per round trip = 178 bps of S, arithmetic, not measured; the read and the confirmation stay at the registered stack. Owner fee line: section 10b |
| `FILL:twin_tool` | `tools/hv4_c0_twin.py` over `tools/arena_twin_writer.py` sha256 1e9cb00e08ec69df269b19b9774d02feed7d3e91a61c7f727b7abbdc5fe560bb (`TWIN_WRITER_SHA256`), run on mal-research-0 against `/data/mal/arena/ledger/hv4-BIG-X` only after the recorded PASS, carried by `tools/hv4_c0_twin_relay.py` (PR `claude/hv4-c0-twin-carry`); parity `tools/arena_live_parity.py` through `tools/hv4_c0_parity.py` @X |
| `FILL:twin_leg` | nofail, 1.9 s |
| `FILL:twin_e1` | E1 excluded decides |
| `FILL:fund` | FILL:owner (0.35 to 1.00 SOL; the lane recommends 0.35 at C0, `ARTIFACTS/lab/hv4-c0-golive-checklist-2026-10-30.md` section 3) |
| `FILL:start_balance` | POSTX:start_balance (Helm/owner, before the first send) |
| `FILL:floor` | funded - 0.25 SOL, never below 0.05 (0.10 SOL at funding 0.35) |
| `FILL:worst_case` | 0.25 SOL |
| `FILL:wallet_address` | POSTX:wallet_address (Helm's fresh canary wallet, public key only) |
| `FILL:withdraw_holder` | Helm |
| `FILL:daily_stop` | 0.10 SOL |
| `FILL:total_stop` | 0.20 SOL |
| `FILL:days` | at most 14 |
| `FILL:end_ms` | POSTX:end_ms, at most min(first send + 14 d, 1794143100000 = 2026-11-08T13:05:00Z, HV4 END); the code refuses an `end_ms` past first start + 14 d (`hv4_live_c0.end_cap_problem`, write-once window file); entries at or after 1794096000000 (2026-11-08T00:00Z) count in no read |
| `FILL:feed_gap_source` | the forwarder's `transport/hv4-BIG-X.gaps.jsonl` and `.status.json` (`tools/decision_forwarder.py`) and the book's own tape gaps `transport/hv4-BIG-X.tapegaps.jsonl` (`tools/hv4_gap_relay.py`; PRs `claude/hv4-gap-relay-v2`, `claude/hv4-tapegap-carry`) |
| `FILL:a3_source` | POSTX:a3_record_file (the A3 daily record on mal-fast-0 that the Q50 canary also binds) |
| `FILL:a3_guard_source` | POSTX:a3_guard_source (Am.3 §6; the guard loop and successor must outlive this copy's `end_ms`, Am.3 §7; loop #1446 ends 2026-11-07T00:57:39Z with #1447 queued `after` it, per DEC-033 §9a) |
| `FILL:mint_fetch` | FILL:review (job id, traded mints, close-blocked count, credits; report-only for HV4, DEC-031 Am.3 item 6) |
| `FILL:tie_out_tool` | `tools/arena_tieout.py` with `tools/hv4_c0_tieout.py` (exporter) @X |
| `FILL:tie_out_job` | POSTX:tie_out_job |
| `FILL:dryrun_job` | POSTX:dryrun_job (at least 1 h DRY at X on the forwarded real root, which is allowed only after the recorded PASS, R2.1) |
| `FILL:md5_proof` | POSTX:md5_proof (`tools/hv4_c0_orders_proof.py` at X: orders equal rows on a SYNTHETIC log, EQUAL; PR `claude/hv4-c0-orders-proof`) |
| `FILL:alert_target` | POSTX:alert_target |
| `FILL:owner_line` | section 10a |

## 10a. Owner line (Amendment 1 item 3)
`FILL:owner_line`, quoted verbatim from DEC-032 Amendment 1 item 3:

OWNER_CANARY_STANDING_OK: 2026-10-23 (owner, in session, to manager14). Answer: "As for when a strategy passes, you can start the live trial straight away. Time is crucial, as we're coming up on one month with no profit now, so we need to move quick." Notebook: n_ecHJTWJ5nmrttQ (MiScusi notebook of this repo's session)

C0 only. The standing line covers stake at most 0.05 SOL, at most 2 open, daily stop 0.10 SOL and the §3 wallet floor; not the funding transfer, not Helm's install, not C1.

The bridge's line (DEC-032 Amendment 1 item 3 "Use"; written ONLY when the manager uses the standing line for this book, on a line of its own with nothing after the id. The bridge does not read this file: it reads a ROOT-OWNED file on mal-fast-0 that Helm writes on the owner's word, and requires exactly one line equal to it there (section 11 item 10); until then it refuses `owner_dec_missing` or `owner_line_missing`):

<!-- OWNER LINE GOES HERE at stage 2, on a line of its own, only on the owner's word (DEC-032 Am.1 item 3 "Use"): the bare line "OWNER_CANARY_CONFIRMED:" + " bot_id=hv4-BIG-X". Not written by the drafter. -->

## 10b. Owner fee line (DEC-032 §4)
DEC-032 §4: "A canary uses a priority other than the executor's installed one only if that is a reviewed executor config change, named in `FILL:fee`, and the owner's line names it." The bridge's `LiveExecutor` default priority is 500,000 (DEC-032 §4 text), while `hv4_c0` compiles 55,000 / 55,000 / 150,000 and refuses any other. Whether that is "a priority other than the executor's installed one" is open (QP). Until QP rules it is not, this line is required before the first send:

FILL:owner (the owner's own words naming the fee: buy 55,000 / sell 55,000 / escalated 150,000 lamports of priority plus the 5,000 base fee per send, replacing the 500,000 default; date and notebook id. The lane recommends asking it NOW, before the read, in the pre-stage owner message, with the 2026-10-05 lesson in view: lower priority can land slower, which P5 and the match test measure.)

## 11. Book-specific declarations (made before the first send)
1. **R2.2 (a) Arm.** `hv4-BIG-X` is an X arm (rule book, no model call; `model_ids` [] in the registry line). Only CON-F, CON-X, BIG-F and BIG-X may be canaried; L, P and R never (Am.15 item 5). Code: `hv4_live_c0.arm_refusal`.
2. **R2.2 (b) Frozen.** bot_id, frozen_sha 9ed4c5ff63935696b2a89150e13565e123047754 and the config pin sha256 1e611a5a636dfe6bd563ab5258f6b498cdb8906500ee8bf2f0264a2621621986 are compiled in; the `start` row must repeat them (`frozen_sha_mismatch` latch). X arm: no model id applies (`model_problem` accepts []).
3. **R2.2 (c) Own directory only.** The bridge reads only `decisions/hv4-BIG-X/` under the forwarded root on mal-fast-0 (`tools/decision_forwarder.py pull`; the root is outside /data/mal/arena), mounted read-only into the unit; no other arm's directory, no ledger, no `bots.json`. The forwarder's `serve` on mal-research-0 serves only this bot's directory and refuses until this bot's recorded PASS passes `hv4_live_c0.read_record_problem`. Declared residual (from #913): a byte copy of HV4 day files under a non-arena root would not be gated; none may be made.
4. **R2.2 (d) Only rows after the read.** Rows whose entry decision is at or after the instant the read was recorded (`FILL:gate-read`) become orders; earlier rows are chain-verified only. Before the record no bridge process, dry or live, reads any HV4 decision row (R2.1).
5. **R2.2 (e) Siblings untouched.** Until every HV4 sibling read is recorded or ended, no HV4 bot is stopped, restarted or re-pinned because of canary output; only the pinned 2026-11-07T12:05Z handover and Am.6 item 4 crash restarts are allowed. Every sibling read discloses this canary's first send instant (recorded after the first send in the stage-3 records, not a POSTX field of this file).
6. **R2.2 (f) Confirmation exposure.** From the first send the wallet is public and the arm's post-read decisions are acted on; the confirmation read for `hv4-BIG-X` discloses this exposure. Allowed by DEC-031 Am.3 item 6 ("No confirmation first", "In parallel"). A canary halt stops the canary only; never a change to the paper book, its sha, instant or rule.
7. **R2.2 (g) Universe.** Equals the read's (section 9a `FILL:universe`); no subset declaration. A missing V is `v_missing` in HV4 and `universe_v_out_of_range` in the canary; both are logged skips, never a cap and never a reason to pick another decision. Token-2022 extensions outside {metadataPointer, tokenMetadata} or a freeze authority are refused before the send.
8. **Caps vs persona.** The canary caps (S, max open 2, 30 buys a day) are tighter than the persona's (stop -25%, target +100%, trail 30% (arms after +50%), time stop 360 min; max open 6, 3 entries per hour). A decision skipped for the canary's caps is logged with its reason and never replaced by another decision.
9. **Per-trade loss latch (declared; tighter than DEC-032 §5).** 30% of S = 15,000,000 lamports (`hv4_live_c0.PER_TRADE_LOSS_PCT`; config may only lower it). One closed trade losing more latches `per_trade_loss_cap` (no new buys; exits continue) until the manager clears it. A BIG paper hard stop is -40%, which exceeds 30% of S, so a BIG exit that gaps through the -25% plan stop to the hard stop trips the latch by design; it is reported as such.
10. **Owner record source (change from the template).** The bridge does not read `DEC/`. It reads a ROOT-OWNED file outside the installed tree, `/etc/mal-hv4-c0/hv4-BIG-X/CANARY_DEC.md` (per-bot path from PR `claude/hv4-c0-start-gate`; at main c92f23e3 the path is the shared `/etc/mal-hv4-c0/CANARY_DEC.md` and a compiled sha pin applies, which that PR removes), and requires a regular root-owned file, not group or other writable, with exactly one line `OWNER_CANARY_CONFIRMED: bot_id=hv4-BIG-X`. Helm writes it only on the owner's word, as a byte copy of this merged DEC (`git show <merge sha>:DEC/<this file> | install -m 0644 -o root -g root /dev/stdin <path>`, per-bot dir root:root 0755), and reports its `sha256sum` and write instant (POSTX:owner_record_sha256), which must equal this file's blob at the merge sha. Per DEC-031 Am.21 item 4 the bridge pins the file's (st_dev, st_ino, sha256) at its first good check; a later rewrite, replacement or removal latches `owner_line` (hard, new buys only; exits continue) until the manager clears it with the bridge stopped.
11. **Read record on the bridge host.** `read_record_problem` requires the record at realpath `/data/mal/arena/reads/hv4-BIG-X.json`. On mal-fast-0 that is a root-owned 0444 byte copy of the research-0 record, whose sha256 must equal the read job's `RECORD` line (POSTX:read_record_copy). The bridge pins its (dev, ino, sha256) at the first check; a rewrite later latches `read_record` for good (QP N9).
12. **Transport and side streams (declared).** Decision rows: `decision_forwarder` pull on mal-fast-0 over `ssh mal-research-0`; measured hop p90 21.33 to 36.22 ms on a synthetic log (P46 DESIGN §4; not a service level). Tape gaps: `hv4_gap_relay` on mal-research-0, carried as `transport/hv4-BIG-X.tapegaps.jsonl`; a missing or stale stream refuses buys. Twins: computed on mal-research-0 from this bot's ledger after the PASS and carried down by `hv4_c0_twin_relay`. The install sha X must contain the merged PRs for: the start gate and the removal of `hv4_c0_incomplete` (QP #959 item 15, its own diff with a fresh QP pass), gap relay v2 (P7-a/b/d), tapegap carry (P7-c/e), twin carry, send rows (precondition 8 residuals), install pack and orders proof (precondition 14), and the bridge sell simulate (POSTX:x_contains, with PR numbers).
13. **Feed-gap halt (DEC-032 §5 item 2, or DEC-032 Am.5 if merged).** The installed value of `hv4_live_c0.FEED_GAP_MANUAL_REVIEW` decides (PR #1090): `True` = §5 item 2 as written and as built (every gap row latches `feed_break`, cleared after 10 gap-free minutes AND a root-owned review file naming exactly the newest gap instant, `/etc/mal-hv4-c0/gap-clear/hv4-BIG-X.json`); `False` = Am.5 (DRAFT), the Q50 executor's rule (#1085): a data-loss gap record holds new buys for 60 s and marks open trips `gap`; forwarder transport events (`stall`, `disconnect`, `reconnect`, `local_torn_trimmed`) neither hold nor latch, and `transport_stall` refuses buys while the forwarder is not connected. `False` is allowed only if Am.5 has merged before the first send. In both modes a malformed gap log or a forwarder `stopped_mismatch` latches, and exits continue. Installed value: FILL:review.
14. **Multiplicity and confirmation.** This is a screen pass, the best of `FILL:N` books and of nine HV4 siblings; in-sample for selection (Am.15 item 8). The confirmation (Am.15 item 3) is reachable only if B <= 2026-11-03T00Z and counts attempts decided after the screen read's instant on at least 5 further UTC dates before 1794096000000, with p at most 0.05/c. Stake above C0 needs it; nothing here authorizes it.

## Changes from the template (listed and justified, as the template's status line requires)
1. §3 "Files" and §8 step 7: `TIER` T0 -> **T1** (`hv4_live_c0.TIER_NAME`; a T0 file refuses the start), and `LIVE_OK` must contain exactly the bot_id (`hv4_live_c0.live_ok_problem`).
2. §2 owner line source: a root-owned file on the bridge host with exactly one owner line (section 11 item 10), not `DEC/` of the installed tree, and no sha pinned in code (manager decision 2026-10-30).
3. §4 fee: the compiled 55,000 / 55,000 / 150,000 differs from the bridge executor's 500,000 default; owner fee line in section 10b unless QP rules it unnecessary.
4. §5: per-trade loss latch declared (section 11 item 9), tighter than the template. Feed-gap halt per section 11 item 13: as written, or Am.5 (DRAFT) only if it has merged.
5. §3 Duration: the 14-day cap is enforced in code from a write-once window file shared by dry and live (QP #965 N-C: a dry start with the real bot_id starts the 14-day clock).
6. Amendments 2 and 4 and the Amendment 2 erratum are Q50-only and are not carried.
No limit is raised and no halt threshold is loosened.

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
