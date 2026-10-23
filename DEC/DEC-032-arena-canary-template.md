# DEC-032: Arena live canary, template (fill-in-the-blanks for a DEC-031 screen pass)

| Field | Value |
| --- | --- |
| **Status** | **TEMPLATE. NOT IN FORCE. Written 2026-10-23 (`date -u` 10:10Z at start), records only.** It allows no live send, no wallet use, no tier, no `LIVE_OK`. It becomes a canary DEC for one book only when a copy of it, with every `FILL` of section 9 filled and the template text otherwise unchanged (any other change is listed and justified in the copy), merges as its own numbered DEC with quant-proof's OK on the exact head and a reviewer verdict on that PR. Even then nothing is sent until the owner's own OK of section 2 is recorded and Helm acts (section 8). |
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

> Vaan, book `FILL:bot_id` passed the arena screen read at `FILL:read_instant` on its deciding cell (`FILL:stake` SOL stake, 1.9 s leg; mean `FILL:mean` SOL per trade in-sample, CI90 lower bound `FILL:ci_lo`, ex-best-day total `FILL:ex_best_day` SOL, worst-in-slot mean `FILL:wis_mean` with CI90 lower bound `FILL:wis_lo`, `FILL:n` attempts on `FILL:d` days). This is a screen, the best of `FILL:N` books, so it is not a profit claim and it has not been confirmed: the confirmation is at least 100 more attempts on at least 5 more UTC dates. I am asking for a small live **execution measurement**, not a bet on the edge. If you say yes: the wallet is funded with `FILL:fund` SOL (your choice, 0.35 to 1.00 SOL), the stake is `FILL:canary_stake` SOL per trade, at most 2 open at a time, at most 30 buys a day. Trading stops by itself at a realized loss of `FILL:daily_stop` SOL in a day, and at `FILL:total_stop` SOL of total loss counting open positions as lost; the worst case is about `FILL:worst_case` SOL lost, leaving the wallet at about `FILL:floor` SOL or more. The priority fee is `FILL:fee` lamports per send. It halts on a pump program redeploy, a feed gap, a break in the decision log, or when live fills drift from the paper engine (section 5). It runs for at most `FILL:days` days. It does not raise the stake: any larger stake needs the confirmation and a new line from you. The paper twin of every live trade runs beside it. Do you approve (yes / no / change), and will you fund the wallet and tell Helm to set the files yourself?

**Checklist (all must be shown to the owner as done or open, in the ask or beside it):**
- [ ] DEC-031 screen read PASS, with multiplicity block, p, Holm rank, N, c
- [ ] quant-proof OK on the read, exact files and head sha: `FILL:qp_comment`
- [ ] ex-best-day total > 0: `FILL`; worst-in-slot mean and CI90 lo: `FILL`
- [ ] seal check stated by quant-proof: `FILL`
- [ ] mint fetch done: close-blocked `FILL` of `FILL` mints
- [ ] sell builder pinned (section 4), tx simulate output attached
- [ ] this copy merged as its own DEC (QP OK + reviewer verdict on head `FILL:sha`)
- [ ] executor/bridge installed DRY by Helm at `FILL:install_sha`; dry run of at least 1 h with 0 build errors and a md5 decision-equivalence replay proof against the frozen decision log
- [ ] parity tool and daily tie-out tool built and run dry (section 5, 6)
- [ ] paper twin repriced at the canary stake with the same send cost as the live fee (section 3)
- [ ] funding amount and wallet address (public) named; withdraw address kept by Helm
- [ ] end instant `FILL:end_ms` set in code/config

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

- **Total stop includes open exposure.** committed loss = realized loss since the first send (floored at 0) + every open or pending position's stake at zero + its buy fee + rent reserve + reserved sell fees + every in-flight buy's worst case. No new buy is sent unless committed loss plus that buy's worst case is at most the total stop (pre-send check, the feearm cap pattern of DEC-019 Am.2). This is stricter than DEC-024 §4, whose stops counted closed trades only. The total stop does not reset on restart and ends the canary until the owner restarts it.
- **Wallet floor** (DEC-024 §4, DEC-020 §3 style): a buy is refused unless balance minus open stakes at zero minus this stake, fees, rent and the sell reserve is at least the floor. The balance has already paid for open stakes; counting them again is deliberate.
- **Worst case at C0** is the total stop (0.20 SOL) plus at most one buy in flight at the moment it fires (about 0.06 SOL), so about 0.26 SOL, leaving at least funded minus 0.26. At the minimum funding of 0.30 SOL that is about 0.04 SOL, below the 0.05 floor: **minimum funding is therefore 0.35 SOL**, and `FILL:fund` must be at least that.
- **Fixed cost vs stake.** At S = 0.05 SOL, two sends at 505,000 lamports are 2.0% of the stake, at 160,000 about 0.64%; the E1 floor adds 384,022 lamports per 0.05 SOL. A canary measures execution, not edge, and the report says so.
- **Paper twin.** Every live entry has a paper twin priced by `tools/arena_paper.py` from the same decision row, at stake S and with the live send cost actually used (section 4). The twin is the comparator for section 5. Where the live sell path does not close the ATA in the sell transaction, the rent-charged leg is the twin (DEC-031 Amendment 2 item 5).
- **Venue and universe:** exactly the book's registered universe, nothing else. Canonical pools only (quote WSOL, V in [17.5e9, 17.7e9]) where Amendment 2 item 6 applies. Token-2022 extensions outside {metadataPointer, tokenMetadata} or any freeze authority: pre-send refusal (strand class = total loss).
- **Sell:** `min_out` at 0.85 of the quote; after 2 reverts or a time bound, resend at 0.65 with higher priority; never `min_out` 0 (DEC-024 §4). Time-stop and stop-loss backstops run with a warm sell plan (bridge S2); the exit poll and sell build are the fast ones. Residual gap-through below the stop is not fixable by polling and is reported, not hidden.
- **Buy guard:** on-chain `min_out` at the decision price divided by 1.15; signal age at most 5 s.
- **Files:** `STOP` (no new buys, exits continue), `HALT` (freeze), `LIVE_OK` and `TIER` root-owned. Live requires `LIVE_OK` present and a valid `TIER` (T0 here). Missing, unsafe or unknown files mean DRY or T0. The wallet-wide `/var/lib/mal-live/STOP` stays the kill switch; the manager takes no STOP, HALT or unit action (Helm does).

## 4. Fee tier and sell builder
- **Priority fee: 500,000 lamports per send** (505,000 with the base fee). This is the live executor's only setting today (`ARTIFACTS/lab/feearm-landing-2026-10-23.md`: "the live executor's priority (still 500,000)").
- **155,000 priority (160,000 per send) is allowed only if DEC-031 Amendment 8, the send-cost floor amendment, is merged.** That text does not exist on main at writing (DEC-031 ends at Amendment 7; no open PR carries it). The fee-arm result (Test A and Test B PASS: 150/152 vs 146/148 landed, lag 2.20 vs 2.44 slots) is a cost input only, and the lab note says that using 160,000 for new books needs a DEC-031 amendment with a new parity proof, quant-proof and reviewer, merged before the first clock it covers. Therefore:
  - the book's paper stack must be the one it registered under; a book registered at 505,000 per send is canaried at 500,000;
  - 155,000 additionally needs the executor config change reviewed (the feearm profile refuses any other priority today), the live-vs-paper twin to use the same send cost as live, and the owner's line naming the fee. Otherwise the twin and the live trade disagree by construction and parity (section 5) would measure the fee, not the fill.
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

**Parity metric (live vs paper, measurable).** A live round trip is eligible if it has a paper twin (section 3), is not marked `gap`, and its exit has landed. For eligible trip i, with `stake_i` in lamports:
- **d_i** (bps of stake) = (live net wallet delta of the trip, from chain: sells received minus buys paid minus all fees, rent refunded counted) minus (paper twin P&L at the deciding stack's flat-15% leg with the same send cost, same rent treatment, at the same stake), divided by `stake_i`, times 10,000. The twin's fail model is not applied to a trip that actually landed: for a live trip that landed on both sides, the twin is the landed outcome of the paper engine without the fail blend ("nofail" leg), so d_i measures price and cost, not the 15% assumption.
- **g_entry_i** (bps) = live buy average price (SOL per token, from the landed tx meta) over the paper twin's entry price, minus 1, times 10,000. Positive = live paid more. **g_exit_i** likewise for the sell (negative = live received less).
- **lag_i** = landed slot minus decision slot (live), compared with the twin's assumed latency slots.
- **Landing-fail excess** = live share of buy sends that failed or expired minus the larger of the two paper models' mean modelled failure share over the same attempts (flat 15% and the pressure refit's mean 1 - p).

Thresholds (the first three are carried from LIVE-READINESS §3 and DEC-024 §5 item 4; a copy may only tighten them):

| Id | Condition | Action |
| --- | --- | --- |
| P1 | mean g_entry over the first 10 eligible fills, and then a rolling 20, above **200 bps** | soft halt (`STOP`), manager review |
| P2 | landing-fail excess above **10 pp** after at least 30 buy attempts, rolling 30 | soft halt |
| P3 | mean d_i below **-300 bps** after at least 20 eligible trips, rolling 20 | soft halt |
| P4 | after at least 100 eligible trips, the CI90 upper bound of mean d_i (1,000 bootstrap draws, seed 1, 95th percentile) below **-100 bps** | hard halt (O): live is worse than paper by more than 1 pp |
| P5 | landing p50 (decision to landed) above **3.0 s** over the first 20 landed buys, then rolling 20; or sells landing after their exit bound in more than 5% of exits | soft halt |
| P6 | mean exit gap g_exit worse than **-300 bps** over the first 10 stop-loss exits | soft halt, report gap-through share |

A soft halt may be cleared by the manager after a written review that names the cause; two soft halts of the same id end the canary until the owner decides. "Live fills must match paper" (DEC-031 §2 step 4) is read as: no P1 to P6 condition is on at the confirmation read and P4 is not triggered. Parity is required in addition to the confirmation and never replaces it. The parity tool (`arena_live_parity`, LIVE-READINESS S5) computes P1 to P6 daily and is wired into the executor's gate (soft halts need no human to act first). Until it exists no canary starts.

## 6. Daily reconciliation (wallet tie-out)
Every UTC day (a MiScusi scheduled job on the host, never a session cron) and again at every halt, end of canary and before every tier step, using the pattern of `ARTIFACTS/lab/probe-final-2026-10-07.md` and `tools/probe_rent_audit.py` (public address only, finalized commitment, no key file opened, the RPC key never printed):
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
- **Step C0 to C1** requires all of: the confirmation read PASS at p at most 0.05/c; section 5 parity clean at that instant over at least 50 eligible trips (P1 to P6 off, P4 not triggered); no unresolved halt; at least 5 daily tie-outs signed off with no residual; section 6 residual 0; the book's impact re-priced at C1's stake with the engine's own impact (a capacity-first screen: trades per day times stake times net edge, impact at size) and still positive on the gate; the sell builder unchanged; the owner's own dated line naming the stake, open cap and stops; and the wallet funded to 1.00 SOL by the owner. Then Helm writes `TIER` as root.
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
Only steps 1 to 5 and 8 are the lab's. Steps 7 needs the owner and Helm.

## 9. BOOK-SPECIFIC FILL fields (the only values a copy may set)
| Field | Meaning |
| --- | --- |
| `FILL:dec_number` | new DEC id of the copy |
| `FILL:bot_id`, `FILL:frozen_sha`, `FILL:clock_start_ms` | the book (from `bots.json` and its notebook copy) |
| `FILL:read_instant`, `FILL:n`, `FILL:d` | screen read instant, attempts, UTC dates |
| `FILL:mean`, `FILL:ci_lo`, `FILL:ex_best_day`, `FILL:wis_mean`, `FILL:wis_lo` | in-sample read figures, labelled in-sample |
| `FILL:N`, `FILL:p`, `FILL:holm`, `FILL:c` | multiplicity block (N, screen p, Holm rank, c at the confirmation) |
| `FILL:qp_comment`, `FILL:reviewer_comment`, `FILL:head_sha` | review ids on the DEC PR |
| `FILL:seal_status` | which seals apply and the declared observation covering live outcomes |
| `FILL:book_stake` | the stake pinned in `bots.json` (S = min of it and 0.05 SOL) |
| `FILL:canary_stake` | S |
| `FILL:universe` | the rule's pool universe and the path:line@frozen_sha of the check (V band, WSOL) |
| `FILL:venue_exit_rule` | exit rule(s) and bounds (timer, stop, backstops) for the book |
| `FILL:executor` | `h5_executor` profile or `arena_live_bridge`, install sha, config sha256, manifest |
| `FILL:builder_path@sha256` | pinned sell+close-ATA builder |
| `FILL:fee` | 500,000 (or 155,000 only with Amendment 8 merged and the book registered under it) |
| `FILL:twin_tool` | the paper twin and parity tool, path@sha256 |
| `FILL:fund`, `FILL:floor`, `FILL:worst_case` | wallet funding (at least 0.35, at most 1.00 SOL), floor, worst case |
| `FILL:wallet_address`, `FILL:withdraw_holder` | public address; who holds withdraw (Helm) |
| `FILL:daily_stop`, `FILL:total_stop` | default 0.10 and 0.20; config may only lower |
| `FILL:days`, `FILL:end_ms` | at most 14 days; explicit end |
| `FILL:feed_gap_source` | the book's gap log (arena tape gap records, or `h5_shadow` `gap` records) |
| `FILL:a3_source` | the A3 monitor or pin source for pump program changes |
| `FILL:mint_fetch` | job id, counts, close-blocked share, credits used |
| `FILL:owner_line` | the owner's verbatim answer, recorded only from the owner's own message |
| `FILL:tie_out_job` | the scheduled reconciliation job id |

Everything else in this template stays as written. A copy that changes a limit upward, a halt threshold looser, or the fee rule is not a fill and needs its own amendment text and review.

## 10. Open points for the first user of this template
- The parity tool (S5), the executor for the book (#793 for H5) and the worst-in-slot figure (S4) are not on main at writing; section 1 items 2 and 6 and section 5 hold those as preconditions, not as assumptions.
- Thresholds P1 to P6 and the C0 limits are the writer's proposal for the manager and quant-proof to confirm; they are not yet owner-stated. The owner confirms the limits by answering section 2 with them in view.
- `Amendment 8` is referenced as the condition for the lower fee; if it merges with a different number or terms, section 4 follows its text.
