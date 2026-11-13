# DEC-035: Live-learning lane (real SOL at micro size, before the gate)

| Field | Value |
| --- | --- |
| **Status** | IN FORCE from merge. Written 2026-11-02 (`date -u` 20:5xZ), manager19. |
| **Decider** | Vaan (owner). Approval in chat, 2026-11-02 ~20:45Z: "You have the yes on it, and you can communicate with helm, my grokbot agent, for whatever you need." MiScusi notebook n_H7IIZL1Qke4dVA. |
| **Amends** | CLAUDE.md "Hard rules" (paper only) and the "North star" live clause, for this lane only. For books in this lane it overrides DEC-032's owner-ask step, its "Helm only" STOP rule and its A3 owner-resume line (§2 item 6, §4): the owner's approval above covers every book run under this DEC, within the limits in §2. |
| **Does not amend** | The promotion gate. Any registered read's rules or scoring (§7 adds a disclosure only). Hosts, firewall, SSH, Cloudflare. |

## 1. Why
For six weeks every candidate stayed on paper, and most reads ended NOT_DECIDABLE for operational reasons (feed gaps, A3 halts, lost crons, failed smokes; lane L4 audit, `ops/reports/2026-11-02/L4-nd-audit.md`: 4 market verdicts in 30 events). Simulator fidelity has been the open question on every line (PumpSwap virtual reserve, exit lag, latency). Small real trades measure fills, latency, fees and P&L directly. This lane is a **measurement budget**, not a promotion.

## 2. Limits (hard; in code or unit config, not in prompts)
1. **Total budget:** lane P&L = the sum over every lane trade since the lane started of (SOL received - SOL sent - all fees: network, priority, tip, unreclaimed rent). Open positions are marked at the executor's last fresh quote; a mark older than the configured `mark_stale_s` (at most 60 s) counts as zero value. When lane P&L <= -0.30 SOL, every executor writes the lane STOP file (item 6) and sends no new entries. A new entry is sent only if lane P&L - (stakes of all open positions + the new stake + a configured fee reserve) > -0.30 SOL, so losing every open position in full cannot break the budget. The budget never resets.
2. **Stake:** 0.02-0.03 SOL sent per entry, fees not included. One entry per position (no adding). An executor refuses to start if its configured stake is outside [0.02, 0.03].
3. **Open positions:** at most 3 across the lane (raised to 8 by Amendment 1, 2026-11-08, below). Every executor takes the lock on one shared lane ledger and reads it before each send (with a single executor, its own ledger is the lane ledger). A position is open from its send until its exit lands or it is written off; a send not yet confirmed counts as open.
4. **Daily loss stop** (applied as written for floor-lane-1 by Amendment 1): 0.10 SOL, measured as lane P&L now (as in item 1) minus lane P&L at the last 00:00Z. At -0.10 SOL there are no new entries until the next 00:00Z. Only this stop resets daily.
5. **Keys:** the wallet key is placed by Helm, root-only, at the path the executor's unit binds. No Claude session reads, prints or copies it. One wallet for the lane, funded by the owner (the budget in item 1 is the loss limit, not the balance).
6. **Kill switch:** one lane STOP file at a fixed path set in the unit config. Every executor checks it before every send. The manager or any session may create it (this overrides DEC-032's "Helm only" for STOP; HALT and unit actions stay Helm's). It stops new entries only; open positions keep their exit rule. When the item 1 budget stop writes it, it survives restarts and only a recorded owner budget change removes it; otherwise only the manager removes it, after a note in the MiScusi notebook. Any wallet-wide STOP Helm already uses (e.g. `/var/lib/mal-live/STOP`) is honoured as well.
7. **Tests:** each limit in items 1-4 and 6 has a unit test, and the tests pass at the executor's installed sha before the first send.

## 3. Which books
Any frozen rule with a live executor path may run in this lane, at the manager's choice, without a gate read: H5-family (h5_executor / h5_arena_executor), arena books via arena_live_bridge, and the slow-bot line once built. Each book gets its own `bot_id`, frozen sha and log of every send. A code or prompt change is a new `bot_id` (logged, not re-approved). The executors run on `mal-fast-0` only. Workers (builders, reviewers, host-ops) never start, fund, configure or restart a lane executor and never read the wallet key; only the manager starts a book; funding and key placement are the owner's and Helm's.

## 4. Program redeploys
A pump / PumpSwap redeploy (A3 `pins_changed`) does **not** end this lane. On a flag the executor pauses new entries. The manager (not a scheduled job; a job may only collect the decode evidence) re-checks the decode on post-redeploy transactions, writes the re-pin review in the DEC-024 13 October resume-record pattern, re-pins and resumes, the same day if the check is clean. The owner delegated the resume: no owner line is needed for this lane (this overrides DEC-032 halts item 1 for lane books only). Other A3 flags (synthetic share, feed health) pause entries until the check is clean.

## 5. Reporting
Each UTC day: trades, realized SOL after all fees, live-vs-sim gap (landing delay, fill slippage, exit timing) per book, to the MiScusi notebook and `data/console.json`. Figures are real money and are reported as they are, including losses.

## 6. Scaling
Above 0.03 SOL per position or above the 0.30 SOL loss budget: only through the promotion gate (CLAUDE.md), on real-fill trades or a registered read, plus the owner's word. Live-learning trades count toward a gate read only under a registration merged to main before its start instant. The registration names one bot_id, its frozen sha, a start instant that is still in the future when the PR merges, and the rule that every send of that bot_id from that instant on is in the read. No trade is dropped: failed sends and failed landings count at their real cost, fees included. All four gate tests run on that full set: at least 100 trades; at least 5 UTC days, a majority of them positive; a 90% CI lower bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile); and a total still positive after dropping the top 3. Real-fill failures are observed, not modelled; the flat 15% and pressure models apply only to simulated trades, and real and simulated trades are never pooled. The read lists every other bot_id that ran in the lane over the same window, with its result. A pass at 0.02-0.03 SOL does not show the edge holds at a larger stake, so each scaling step states its stake and a price-impact estimate at that stake. Quant-proof rules on the read at its exact head. This DEC changes no gate threshold.

## 7. Interaction with seals
Live outcomes of a book in this lane are the lane's own data and are not sealed. If a book's pools or tokens overlap a running registered read (for example EXP-024 Look-2, to 2026-11-14), then before the book's first live send in the overlap: a PR is merged that adds an entry to docs/HOLDOUT_LEDGER.md naming the read, the book (bot_id and frozen sha), the overlap and the instant, and that PR carries a QP verdict at its exact head stating the seal check (ledger D2/D3/D4, EXP-024 Look-2, C1-NF, FLOOR). The seal check reads ledger and registration text only, never sealed outcomes. From then on that read is reported as "not blind: live-lane observation declared <instant>". After that instant the read's rules, stops and scoring do not change, and no live trade is added to its count. The live lane does not stop because of it.

## Amendment 1 (2026-11-08): lane open-position cap 3 to 8; daily stop as written for floor-lane-1
**Decider.** Vaan (owner). Owner in chat to manager21, 2026-11-08 ~08:5xZ, replying to the manager's proposal "raise the cap from 3 to 8 open positions, same 0.03 SOL size, same 0.30 SOL total stop":
> "You can raise the cap, that's a decision you can make on your own. Good work, keep it pushing."

**Change.** (1) §2 item 3 reads "at most 8 across the lane" in place of "at most 3". The lock, the shared lane ledger, the open-position definition and the `lane_full` refusal are unchanged. (2) §2 item 4 is applied as written: the daily stop is marked lane P&L now minus lane P&L at the last 00:00Z <= -0.10 SOL. For floor-lane-1 there is no worst-case daily pre-send (code: `FloorLaneLedger.evaluate` in `tools/floor_lane_executor.py` omits it; the byte-pinned `tools/h5_lane_executor.py` is unchanged). The worst-case pre-send applies to the total budget (item 1) only. (3) For floor-lane-1 the item 1 pre-send uses cost-capped lane P&L: each open filled position counts min(fresh mark, its stake), a stale or missing mark counts 0, so unrealised gains never add headroom and losses still count (`FloorLaneLedger.capped_pnl`). The budget stop and the daily stop checks are unchanged. Executors that already do a daily pre-send (the stopped h5-lane executors) keep it.

**Why.** floor-lane-1 (the only running bot in the lane) makes about 13 entries a day (replay MiScusi #2265) and holds up to 24 h. At 3 open most entries are refused with `lane_full`, which slows learning. Maximum exposure at the new cap is 8 x 0.03 = 0.24 SOL, under the 0.30 SOL budget. The §2 item 1 pre-send check, run on cost-capped lane P&L (cost-capped lane P&L - (open stakes + new stake + fee reserve) > -0.30 SOL), guarantees that after any accepted entry, losing every open position and the new one in full ends above -0.30 SOL less the fee reserve (property-tested). It does not rely on marks: on plain marked P&L a +100% mark on 7 positions would admit an 8th entry whose full wipe ends at -0.494 SOL (QP, PR #1279). Item 2 is needed for item 1 to have effect: the daily pre-send (open stakes + new stake + fee reserve per position against 0.10 SOL) refuses a third open position at stake 0.03, so a cap of 8 was unreachable with it. Side effect of item 1: after losses that check refuses entries earlier, with `lane_budget_presend`, even when fewer than 8 positions are open.

**Consequence, stated plainly.** (a) Lifetime: with the cost-capped pre-send, a full wipe of every open position plus the one being entered ends at or above -0.30 SOL (less fees beyond the per-position reserve); this holds on a cost basis whatever the marks. (b) One UTC day: the daily stop is checked before an entry only (on marked P&L and on the cost basis, both since 00:00Z), with no worst-case pre-send. An entry admitted at a day P&L just above -0.10 SOL followed by a full wipe of 8 positions can lose up to about 0.10 + 8 x 0.03 SOL plus fees (about 0.37 SOL, QP) in one day, but never past the lifetime bound in (a). (c) A quote gap over 60 s with 4 or more open (4 x 0.03 > 0.10) drops marked P&L enough to trip the daily stop; entries are refused while the marks stay stale and resume once fresh quotes bring the day's marked P&L back above -0.10 SOL. It does not by itself write the permanent budget STOP, because the pre-send already reserved for a wipe at cost. (d) In code the daily stop is a check before each entry, not a latch: for floor-lane-1 (as already for the h5-lane executors) entries resume within the same UTC day once marked lane P&L since 00:00Z is back above -0.10 SOL. This amendment records that behaviour in place of §2 item 4's "until the next 00:00Z" for floor-lane-1.

**Unchanged.** Stake bounds [0.02, 0.03] SOL, the -0.30 SOL total budget, the -0.10 SOL daily stop (marked, as written), the STOP file and every other §2 item. §6 scaling is unchanged: a stake above 0.03 SOL or a budget above 0.30 SOL still goes only through the promotion gate and the owner's word. The code cap is `LANE_MAX_OPEN` in `tools/floor_lane_executor.py`. The stopped h5-lane executors keep their own cap of 3 in code; this amendment does not change them.

## Amendment 2 (2026-11-09): floor-lane-1 keeps its bot_id for the probe-port limit correction
**Decider.** manager21, under the owner's word of 2026-11-08 ~08:5xZ quoted in Amendment 1 ("You can raise the cap, that's a decision you can make on your own").

**Found.** The live port of floor-lane-1 inherits the DEC-019 probe profile, which clamps the open cap to 3 (not this DEC's 8), stops buys after 60 attempts, and stops them 7 days after the first attempt (EXP-029 Erratum 1c).

**Change.** §3 says "A code or prompt change is a new `bot_id`". One exception is ruled here. The fix in PR #1297 changes only `FloorLanePort.__init__` in `tools/floor_lane_executor.py`: it sets the probe executor's `max_open` to the lane's, `max_attempts` to 100,000 and `max_days` to 60. It changes no host decision and no dry row, and it only makes §2 item 3 (Amendment 1) take effect. It keeps the bot_id `floor-lane-1`. A successor bot_id would orphan the open positions (EXP-029 Erratum 1b, revision history).

**Unchanged.** Every §2 limit; §3 for any other code change; §6 scaling.

**Amendment 2, wording note (2026-11-09, EXP-029 Erratum 1d item 7).** "It only makes §2 item 3 take effect" means: it makes §2 item 3 take effect and removes two probe stops (60 attempts, 7 days) that this DEC does not set. The same `FloorLanePort` serves the forge DSL lane executor, so the fix applies to DSL lane bots too; none is running.

## Amendment 3 (2026-11-09): the §2 item 1 pre-send is the full-wipe bound (no double count)
**Decider.** manager21, under the owner's standing go-live authority (2026-11-06: act without asking, go live when confident on evidence) and the lane-cap word quoted in Amendment 1. The quote covers caps; this amendment changes how the existing 0.30 SOL budget is evaluated, not its size. Written 2026-11-09T13:48:09Z, after EXP-029's S; see EXP-029 Erratum 1e.

**Found.** §2 item 1 sends a new entry only if "lane P&L - (stakes of all open positions + the new stake + a configured fee reserve) > -0.30 SOL, so losing every open position in full cannot break the budget". Lane P&L is cash plus marks, and cash already holds every filled buy's SOL out. Subtracting the open stakes again charges each filled position's loss twice whenever its mark is below its stake (a stale or missing mark counts 0). Live, from 2026-11-09T10:46:27Z, floor-lane-1 refused 16 host entries `lane_budget_presend` (to 12:33:53Z; MiScusi #2344) with cash -139,591,048 lamports and 5 open, although losing all 5 plus the new one in full ends near -192,750,000 lamports, inside -300,000,000.

**Change.** The item 1 pre-send is the bound its own sentence states: a new entry is sent only if cash - (stakes of open positions whose buy is not yet filled + the new stake + the fee reserve) > -0.30 SOL. Filled positions are in cash at their full cost; unfilled intents are charged at their stake; marks never add headroom. Implemented for floor-lane-1 in `tools/floor_lane_executor.py` (`wipe_floor`, PR #1303). `tools/h5_lane_executor.py` is byte-pinned and keeps its own copy; no H5 lane bot runs. The forge DSL lane executor uses the same `floor_lane_executor` ledger class, so DSL lane bots inherit this check; none is running. floor-lane-1 keeps its bot_id, as in Amendment 2: the change touches only the live lane check, not the host or any dry row.

**Unchanged.** The 0.30 SOL budget and its STOP (lane P&L <= -0.30), the fee reserve, the daily stop, the open cap 8, the stake, §3 for any other code change, §6.

## Amendment 4 (2026-11-10): floor-lane-1 step-up to 0.10 SOL, budget 0.60 SOL; fresh marks; open stakes counted once in the wallet floor
**Decider.** Vaan (owner).

The owner's question came in two copies.
- **In chat**, manager22 put two options to the owner, in these exact words:
  > "**(A) Stay at 0.03 until the gate test reaches 100 trades** (the fixes speed that up). If it passes, scale to 0.10–0.25 with real confidence behind it."
  >
  > "**(B) Step up to 0.10 once the risk-check fix is installed and the 10-trade fills check passes.** Same S and every trade so far, re-registered. The loss cap stays at your 0.6 SOL."

  The same chat message added: "raising the stake past 0.03 breaks two of the lab's rules: It needs your explicit override of the rule that only a book that passed the promotion gate can scale. It automatically ends FLOOR's current gate test as a FAIL on process grounds."
- **In MiScusi**, question q_rciWQWvV_i20Rg (17:35Z) offered:
  - A+: "keep 0.03 SOL a trade, raise the open-position limit 8 → 16, raise the lifetime loss budget 0.30 → 0.60 SOL (your limit)…";
  - B: "raise the stake to 0.10 after trade #10. Only about 5 positions fit at once, and by its own rules the gate test ends as a FAIL on process grounds."

**Answer.** The owner answered in chat. The manager's state file records the answer at 17:42:36Z; that is the write time of the state line, and the answer came shortly before it.
> "As for my two choices. Let's step it up. If we can get to 9 dollars a day that will work for now. We need to work hard to get there. From there putting more capital behind it, and having the 9 dollars stack into more capital we can use is easy."

**How the manager reads it.** "Step it up" is option B as offered in chat, including its 0.6 SOL loss cap. "9 dollars a day" is the owner's target, not evidence (CLAUDE.md owner notes).

The owner's standing ramp word of 2026-11-08 (MiScusi notebook, "OWNER 11-08 ~00Z") was: "Let's get started on Floor, you can ramp it up as you see it working." The 0.6 SOL ceiling is the manager's record of the ladder (memory `owner-golive-1108`). It is not the owner's own words; the owner accepted it by choosing B.

**Scope of the override.** It overrides §6 ("only through the promotion gate") for this step and for floor-lane-1 only. It does not lower the promotion gate (CLAUDE.md): **floor-lane-1 has not cleared the gate**, and nothing in this amendment says it makes money.

**Found** (manager22, 2026-11-10):
1. **Stale marks.** An open position's mark is V-correct, but it goes stale about 300 s after entry: the pool cache is re-primed only while young, and a stale mark counts 0 (§2 item 1). The item 4 daily stop's marked arm then trips at about the 4th net new entry in a UTC day. (A false budget STOP through stale marks alone is prevented by the Am.3 pre-send; it was reachable only through fees beyond the per-position reserve.)
   - Audit: `/data/mal/hunt-1008/ops/reports/2026-11-10/floor-mark-daily-stop-audit.md`.
2. **Held stakes counted twice.** The wallet floor (`floor_arena_executor.floor_problem`) adds the open stakes on top of a cash balance that has already paid the landed ones (EXP-029 Erratum 1f, "Found").

**Change** (floor-lane-1 only):
1. **Stake:** 100,000,000 lamports (0.10 SOL) per entry. The §2 item 2 bounds read [0.02, 0.10] SOL; code `FLOOR_LANE_STAKE_BOUNDS_LAMPORTS`.
   - The port now sets the inner probe's size and loss cap to the lane values; its own 0.05 / 0.35 SOL clamps would refuse every 0.10 buy.
2. **Budget:** item 1 reads −0.60 SOL in place of −0.30 SOL; code ceiling `LANE_BUDGET_MAX_LAMPORTS` 600,000,000.
   - The budget counts from lane start, 0.03 history included; it is not 0.60 more. It still never resets.
   - Each open position is charged at full stake. The bound study found tails to −21% and three −100% strands (`/data/mal/hunt-1008/push-1110/floor-study/PART1-BOUND.md`), so no reduced weight is used.
3. **Open cap:** at most 16 across the lane (code ceiling `LANE_MAX_OPEN` 16; the config sets the value in use; owner, Amendment 1: cap raises are the manager's call). At 0.10 SOL other checks bind first (QP arithmetic, PR #1314):
   - at a balance of 1,088,736,731 lamports, the wallet floor binds at 3 opens;
   - after the Erratum 1f top-up (1,338,736,731), the wallet floor, `committed_loss_stop` and the pre-send tie at 5;
   - each −0.1055 SOL of lane cash removes one.
4. **Fresh marks:** the executor re-primes the pool cache of every open mint at most once per 240 s (`FloorLanePort.warm`), so a healthy open position keeps a fresh, V-correct mark. A failed fetch leaves the mark stale (counted 0), as now.
   - **Side effect on exits:** a sell later than about 300 s after entry is now built from that warm snapshot (`min_out` included), where before it came from a full snapshot fetched at exit (arena_live_bridge.py:1478-1490; probe_executor.py:1320, :1358-1365; probe_live.py:751-754). Its vault reserves come from the batched read and are used only while at most about 1 s old (`BATCH_MAX_AGE_MS` 1,000 ms; arena_live_bridge.py:1485, probe_executor.py:72); the pool state and V come from a cache at most 240 s old. (Corrected 2026-11-11T10:47:07Z by `date -u`, QP MINOR 2 on #1314 at c2ad6ab1.)
   - **Load:** one full pool snapshot per open mint per 240 s, at most 16 mints, so at most about 4 RPC snapshots a minute.
   - The post-install fills check splits sells by snapshot kind.
5. **Wallet floor:** `wallet_floor_count_held_once` is true in the floor-lane-1 config. The floor then requires the stakes of buys not yet landed (`buy_sending`, `buy_sent`) + the new stake + send fee + rent reserve + (open + 1) × sell reserve + `wallet_floor_lamports` (max(0.05, funded − 0.25)). The stakes of landed buys are no longer added a second time.
6. **Daily stop:** unchanged at 0.10 SOL, both arms. With item 4 the marked arm measures real marks. Consequences at the new size (Am.1 (b), (c) restated):
   - **Daily stop:** it is checked before an entry only, so one UTC day can lose up to the whole remaining lifetime budget (about 5 × 0.1039 SOL plus up to 0.10 already lost that day), never past −0.60 SOL lifetime.
   - **Quote gaps:** a quote gap over 60 s, or an executor restart (which empties the marks), with even **one** open 0.10 position trips the marked arm until marks are fresh again. At 0.03 that took 4.
7. **Price impact at 0.10 (§6; in-sample, `PART1-BOUND.md` "Distance to the floor"):** price / floor after our own buy is 1.0113 (q = 0) to 1.0693 (q = 0.5 SOL), against 1.0034 to 1.0612 at 0.03. About 0.8% higher post-buy price (about 0.4% on the average fill) per 0.07 SOL of extra stake against V + q ≈ 18 SOL (QP arithmetic: 1.0113/1.0034 − 1 = 0.79% at q = 0, 1.0693/1.0612 − 1 = 0.76% at q = 0.5; corrected 2026-11-11T10:47:07Z by `date -u` from "0.55%", QP MINOR 1 on #1314). Precondition (b) below measures fills at 0.03 only; the first 10 fills at 0.10 are checked with the same tool after install.

**Bot id.** floor-lane-1 keeps its bot_id and ledger, as in Amendments 2 and 3. Items 4 and 5 are execution-only: they change live lane checks and, for item 4, the sell snapshot, but no host decision and no dry row, because the port is live-only (Errata 1c/1e grounds). Item 1 changes the stake, which EXP-029 §3 treats as a rule change. EXP-029 records the end in Erratum 1g.

**Install.** One HELM-ASK covers the merge sha of this PR, the changed blobs (`tools/floor_lane_executor.py`, `tools/floor_arena_executor.py`, `tools/arena_live_bridge.py`, `tools/forge/dsl_lane_executor.py`) and a new live config: stake 100,000,000; budget 600,000,000; max_open 16; daily 100,000,000; `wallet_floor_count_held_once` true; `funded_lamports` 1,000,000,000. Helm restarts the executor only; the decision host is never restarted.

Before the ask, all of these hold, and the manager records them in the state file:
- (a) QP OK and REVIEWER APPROVE at the exact head of this PR;
- (b) `tools.floor_fills_vs_model` prints `FILLS_MATCH_MODEL yes` on at least 10 measured trades (at 0.03);
- (c) the §2 item 7 unit tests pass at the merge sha;
- (d) the lane STOP file is absent, and lane cash is recorded.

**Scaling past this step.** Above 0.10 SOL, or above a 0.60 SOL budget, requires the owner's word in a new amendment, with its stake and a price-impact estimate (§6).

**Unchanged.** Keys, STOP/HALT, A3 pauses, the shared lane ledger and lock, the host, the rule `l1-floor-v2` and its exits, §4, §5, §7, and every other lane bot's limits. The DSL lane keeps 0.03 / 0.30 / 8 (`DSL_LANE_*` constants); the stopped H5 lane executors keep theirs.

## Amendment 5 (2026-11-12): floor-lane-1 lifetime budget 1.20 SOL, daily stop 0.20 SOL; stake unchanged
**Decider.** Vaan (owner).

**The question.** Manager22 asked the owner in chat, 2026-11-12 ~01:0xZ, in these exact words:
> "How much total loss are you willing to risk on the scaled-up lane? I'd suggest about 1.2 SOL, with the daily stop raised to 0.2 SOL, once the new capital lands. That one number decides how many positions we can hold."

**The answer.** The owner answered in chat at ~01:1xZ. The manager's state file (`/data/mal/hunt-1008/ops/manager22/state-manager22.md`, line "2026-11-12T01:14:36Z OWNER DECISION") records it at 01:14:36Z; that is the write time of the state line, and the answer came shortly before it.
> "As for what you need from me, I think we can go with what you suggest, and obviously we're hoping to be profitable, and not hit that point lol. Otherwise good job with the replay and digging into what's happening to make sure everything is solid, I'm going to try and get this 2 sol tonight. And you just keep things moving"

**How the manager reads it.**
- "What you suggest" is the 1.2 SOL total lane loss limit and the 0.2 SOL daily stop.
- The 1.2 SOL counts from lane start, with the 0.03 and the 0.10 history included. It is not 1.2 more, and it never resets.
- The 0.20 SOL daily stop applies to both arms (marked and cost-basis) of the item 4 daily stop.
- "I'm going to try and get this 2 sol tonight" makes the top-up a condition: this applies once the owner's top-up is on chain, not before. The owner's hope that the lane is profitable is the owner's hope, not evidence.

**Scope of the override.** As in Amendment 4, it overrides §6 ("only through the promotion gate") for this step and for floor-lane-1 only. It does not lower the promotion gate (CLAUDE.md): **floor-lane-1 has not cleared the gate**, and nothing in this amendment says it makes money.

**Change** (floor-lane-1 only):
1. **Budget:** item 1 reads −1.20 SOL in place of −0.60 SOL; code ceiling `LANE_BUDGET_MAX_LAMPORTS` 1,200,000,000 (`tools/floor_lane_executor.py`).
2. **Daily stop:** item 4 reads 0.20 SOL in place of 0.10 SOL; code ceiling `FLOOR_LANE_DAILY_MAX_LAMPORTS` 200,000,000, used by the floor-lane-1 config validation only. The shared `LANE_DAILY_MAX_LAMPORTS` stays 100,000,000 because the DSL lane (`tools/forge/dsl_lane_executor.py`, a pinned blob that is not changed) reads it as its default and its ceiling. `LANE_BUDGET_MAX_LAMPORTS` is read by floor-lane-1 validation alone (the DSL lane has `DSL_LANE_BUDGET_MAX_LAMPORTS`, 300,000,000). The default for a config without `daily_loss_lamports` stays 100,000,000 (new `LANE_DAILY_DEFAULT_LAMPORTS`, as the budget default stays 300,000,000); `tools/lane_daily_report.py` uses that default when run without `--config`.
3. **No inner clamp.** The other places that carry a 0.60 or 0.10 SOL figure for this lane were checked:
   - the probe `Limits` clamp (loss cap 350,000,000): the port sets `loss_cap_lamports` to `cfg.total_loss_lamports`, so 1.20 SOL passes (test `test_am5_port_lifts_probe_loss_cap_to_1200m`);
   - the DEC-032 C0 table in `floor_arena_executor.tier_limits` (daily 100,000,000, total 200,000,000, min() with config): the lane's own `tier_limits` returns the lane values (test `test_am5_tier_limits_carry_the_lane_values_not_the_c0_table`);
   - `committed_loss_stop` and `total_loss_lamports` read `cfg.total_loss_lamports` = the lane budget;
   - `lane_daily_report` takes the budget and daily values from the lane config when `--config` is given;
   - the stake bounds stay [0.02, 0.10] SOL and `LANE_MAX_OPEN` stays 16.

**Capacity at 0.10 SOL** (reserve 3,860,000 = rent 2,100,000 + send fee 160,000 + sell reserve 1,600,000; per-position worst case 103,860,000):
- **Pre-send (item 1, Am.3 full-wipe bound).** Lane cash = realized − n × 0.10 SOL with n filled opens; realized +15,081,595 (the 10 closed trades, HELM-REPLY-1111-1).
  - Admitting the 11th (n = 10): 15,081,595 − 1,000,000,000 − (100,000,000 + 11 × 3,860,000) = −1,127,378,405, inside −1,200,000,000: allowed.
  - Admitting the 12th (n = 11): 15,081,595 − 1,100,000,000 − (100,000,000 + 12 × 3,860,000) = −1,231,238,405, past −1,200,000,000: `lane_budget_presend`.
  - So the pre-send allows **11 opens**. Test `test_am5_presend_allows_the_11th_open_at_010_and_refuses_the_12th`.
- **`committed_loss_stop`** (realized loss floored at 0, so gains give no room): 11 × 103,860,000 = 1,142,460,000 ≤ 1,200,000,000 < 12 × 103,860,000 = 1,246,320,000. Also **11 opens**.
- **Wallet floor** (funded 1,000,000,000, held-once: floor = max(0.05, funded − 0.25) = 750,000,000). Each landed open takes about 102,260,000 (stake + rent + send fee) from the balance. The next open needs 100,000,000 + 160,000 + 2,100,000 + (n + 1) × 1,600,000 + 750,000,000.
  - Balance 1,119,513,796 (the manager's reading, 0 open assumed; re-record it before install): **3 opens**. n = 2 passes (balance left 914,993,796 against a need of 857,060,000); n = 3 fails (812,733,796 against 858,660,000).
  - Balance about 3,119,500,000 (after a 2 SOL top-up): the floor allows 22, above the 16 cap and above 11, so it does not bind.
- **Which binds first.** Before the top-up the wallet floor binds, at 3 opens (as Amendment 4 item 3 found at 3 to 5). After the top-up the pre-send and `committed_loss_stop` tie at 11; `max_open` 16 never binds at 0.10 SOL. Each −0.1039 SOL of realized lane cash removes about one open from the pre-send.
- These counts are structural arithmetic, not a forecast of how many entries the host triggers. Test `test_am5_committed_loss_stop_ties_at_11_and_wallet_floor_binds_at_3_then_not`.

**Consequences.**
- **Daily stop:** it is checked before an entry only (Am.4 item 6, restated), so one UTC day can lose more than 0.20 SOL. The bound is min(remaining lifetime room, about 0.20 SOL already lost that day plus 11 × 0.1039 SOL of positions admitted after it). The remaining lifetime room cannot exceed 1.2 SOL plus realized gains, about 1.215 SOL now (1,200,000,000 + realized 15,081,595), and lifetime P&L never goes past −1.20 SOL. The stop fires at −0.20 SOL, not before (tests `test_am5_daily_stop_fires_at_minus_200m_not_before`, `test_am5_daily_stop_via_the_bridge_at_minus_200m`).
- **Quote gaps:** a quote gap over 60 s, or an executor restart (which empties the marks), with even one open 0.10 position trips the marked arm until marks are fresh again (Am.4 item 6 restated). At 0.20 the marked arm needs a larger loss to trip, but a stale mark still counts 0.
- **Budget stop:** at −1.20 SOL lifetime the lane STOP file is written, as for any lane budget stop.

**Price impact (§6).** The stake stays 0.10 SOL, so per-trade impact is as in Amendment 4 item 7. More concurrent positions are in distinct pools (one open per pool), so they do not add impact to each other; they add exposure.

**Bot id.** floor-lane-1 keeps its bot_id and ledger. The change is to two lane ceilings and the config; no decision logic and no host rule changes.

**EXP-029.** Read of Erratum 1g (EXP/EXP-029-floor-lane-1-gate-read-prereg.md, line 289): the owner's 0.10 step-up ended the gate read as a procedural FAIL at the install instant, 2026-11-12T00:46:04.584Z (HELM-REPLY-1111-1), and any later registration keeps S = 2026-11-09T06:00:00Z and every trade since. This amendment changes no stake, so it adds no FAIL-end event of its own, and the read is already ended. The Erratum 1g descriptive tests run on the trades until 2026-11-13T00Z, so an install before then is noted in that read's report; this is a note, not an erratum. No EXP-029 erratum is written. The Erratum 1g rule 3 (no second look) is unchanged.

**Install.** One HELM-ASK covers the merge sha of this PR, the changed blobs (`tools/floor_lane_executor.py`, `tools/lane_daily_report.py`; `tools/forge/dsl_lane_executor.py` is not changed and keeps blob 8cbc81a5) and a new live config. It is identical to `live-fc6ab6b2579655be792f8d12864ab209b8434d0d.json` except `budget_lamports` 1,200,000,000 and `daily_loss_lamports` 200,000,000 (`funded_lamports` stays 1,000,000,000, so the wallet floor stays 0.75 SOL; stake 100,000,000; max_open 16; `wallet_floor_count_held_once` true). Helm restarts the executor only; the decision host is never restarted. Until Helm installs it, the live lane keeps 0.60 / 0.10.

Before the ask, all of these hold, and the manager records them in the state file:
- (a) QP OK and REVIEWER APPROVE at the exact head of this PR;
- (b) the owner's top-up is visible on chain, and the balance is recorded;
- (c) the unit tests pass at the merge sha (every test file that imports `floor_lane_executor`, plus the arena executor tests: `test_floor_lane_executor`, `test_floor_arena_executor`, `test_floor_risk_accounting`, `test_floor_lane_mark_refresh`, `test_lane_daily_report`, `test_floor_fills_vs_model`, `test_forge_dsl_lane_executor`);
- (d) the lane STOP file is absent, and lane cash and the number of open positions (0 or more) are recorded;
- (e) if at least 10 trades at 0.10 SOL have closed by then, `tools.floor_fills_vs_model` prints `FILLS_MATCH_MODEL yes` on them. If fewer have closed, the check runs at the 10th closed 0.10 trade, and a "no" stops new entries through the lane STOP file.

**Scaling past this step.** A stake above 0.10 SOL, or a budget above 1.20 SOL, requires the owner's word in a new amendment, with its stake and a price-impact estimate (§6).

**Unchanged.** Keys, STOP/HALT, A3 pauses, the shared lane ledger and lock, the host, the rule `l1-floor-v2` and its exits, the stake bounds [0.02, 0.10], `LANE_MAX_OPEN` 16, `FUNDED_MAX_LAMPORTS`, §4, §5, §7, and every other lane bot's limits. `tools/h5_lane_executor.py` and the DSL lane (`DSL_LANE_*`: 0.03 / 0.30 / 8) keep their constants; the stopped H5 lane executors keep theirs.

## Amendment 6 (2026-11-12): floor-lane-1 stake ceiling 0.25 SOL, lifetime budget 2.10 SOL, daily stop 0.50 SOL (install gated on 20 closed trades at 0.10)
**Decider.** Vaan (owner).

**The recommendation.** The manager (manager22) put it to the owner in chat, 2026-11-12 ~06:1xZ, in these exact words:
> "My recommendation: after 20 closed trades at 0.10 (instead of the planned 30), if they're net positive and the fills keep matching the model."

and, on the loss budget:
> "With about 3.1 SOL in the wallet we can afford 8–9 positions at 0.25. That needs a 2.1 SOL loss budget"

**The answer.** The owner answered in chat, 2026-11-12 ~06:4xZ, in these exact words:
> "alright it's go time … the first 10 trades ending up positive looked good, so now we're scaling. Your recommendations look good and I accept the loss budget for whatever you think is going to get us more profitable. We need to be smart, we need to make sure everything is working properly, you need to be competent and proactive."

**How the manager reads it.**
- "Your recommendations" is the step-up to 0.25 SOL stake after 20 closed trades at 0.10 SOL (not the 30 first planned), conditional on those trades being net positive and the fills continuing to match the model. The condition is the manager's own text that the owner accepted; this amendment writes it down as install preconditions (b) and (c) below.
- "The loss budget" is the 2.1 SOL lifetime loss budget. As in Amendment 5 it counts from lane start, with the 0.03, 0.10 and any later history included. It is not 2.1 more, and it never resets.
- "Smart ... everything working properly" is read as: the preconditions are checks that must pass, not a formality. A "no" on (b) or (c) means the lane stays at 0.10 SOL and the manager reports to the owner.
- The owner's "first 10 trades ending up positive looked good" refers to the 10 trades at 0.03 SOL. It is the owner's reading, not evidence of an edge.

**Scope of the override.** As in Amendments 4 and 5, it overrides §6 ("only through the promotion gate") for this step and for floor-lane-1 only. It does not lower the promotion gate (CLAUDE.md): **floor-lane-1 has not cleared the gate**, and nothing in this amendment says it makes money.

**Evidence the manager had (all small, none a gate read).**
- Live: 10 closed trades at 0.03 SOL, +0.015081595 SOL after all fees; fills check #2439: 10/10 matched the model. The 10 trades are at about one-eighth (0.03 / 0.25 = 0.12) of the stake now asked for, and they say nothing about fills or impact at 0.25.
- November paper replay (`/data/mal/hunt-1008/ops/reports/2026-11-12/floor-nov-funnel.md`, section "Manager erratum": the replay charged refundable ATA rent as a cost; corrected reader). Rent-corrected, flat leg, K = 16, stake 0.25: n 50, mean +5.66%, median −2.21%, total +0.7072 SOL, ex top 3 +0.1988 SOL, 6 of 8 entry-day buckets positive (11-07 is split at the deploy; combined 11-07 is −0.0096 SOL, so 6 of 7 UTC days positive; per_day in `/data2/mal/scratch/floor-nov-funnel/pnl-rentfix.json`). In-sample, not a gate read, and 29 feed-gap positions are unpriced (the busy 11-09 and 11-10 days). The median is negative, and the ex-top-3 total is a fraction of the total. **Caveat, the regime the live host runs in:** the post-deploy subset (11-07T19Z onward, same cell) is n 17, total +0.3106 SOL, ex top 3 **−0.1035 SOL** (pressure leg −0.0970). The positive pooled ex-top-3 comes from the pre-deploy rows together with the post-deploy rows; the most relevant slice has a negative ex-top-3.
- Price impact at 0.25 (§6): see "Price impact" below.

**Change** (floor-lane-1 only; no decision logic changes):
1. **Stake ceiling:** item 2 reads [0.02, 0.25] SOL; `FLOOR_LANE_STAKE_BOUNDS_LAMPORTS` (20,000,000, 250,000,000) in place of (20,000,000, 100,000,000). The DSL lane has its own `DSL_LANE_STAKE_BOUNDS_LAMPORTS` (20,000,000, 30,000,000), which does not move.
2. **Budget:** item 1 reads −2.10 SOL in place of −1.20 SOL; `LANE_BUDGET_MAX_LAMPORTS` 2,100,000,000.
3. **Daily stop:** item 4 reads 0.50 SOL in place of 0.20 SOL; `FLOOR_LANE_DAILY_MAX_LAMPORTS` 500,000,000.
4. **No shared constant moves.** Every reader of the three constants was grepped across `tools/` (including `tools/forge/`, `floor_arena_executor.py`, `floor_risk_accounting`, `lane_daily_report`, `h5_*`, `slowbot/`). `FLOOR_LANE_DAILY_MAX_LAMPORTS` already was floor-only (Am.5). `LANE_BUDGET_MAX_LAMPORTS` is read by floor-lane-1 validation alone (`tools/slowbot/live_executor.py` and `tools/h5_lane_executor.py` use `h5_lane_executor`'s own copy). `FLOOR_LANE_STAKE_BOUNDS_LAMPORTS` is read only inside `floor_lane_executor.py` and by `lane_daily_report` (as the minimum, index 0, unchanged). The shared `LANE_DAILY_MAX_LAMPORTS` stays 100,000,000 and the defaults stay (`LANE_BUDGET_DEFAULT_LAMPORTS` 300,000,000, `LANE_DAILY_DEFAULT_LAMPORTS` 100,000,000, `LANE_MAX_OPEN_DEFAULT` 8). `tools/forge/dsl_lane_executor.py` (blob 8cbc81a5e7b92790a3dc651fce234af39d4003f7), `tools/floor_arena_executor.py` and `tools/arena_live_bridge.py` are not changed (`git diff origin/main` on the three is empty). No new split was needed in this amendment; the Am.5 split already covers the daily ceiling.
5. **No inner clamp** (checked in Am.5 for 1.20; the same paths carry 2.10): the probe `Limits` loss cap is set to `cfg.total_loss_lamports` by the port, the lane's own `tier_limits` returns the lane values, and `committed_loss_stop` reads the lane budget. The tests for these (`test_am5_*`) are unchanged and pass at the new numbers through the config.

**Capacity at 0.25 SOL** (reserve 3,860,000 = rent 2,100,000 + send fee 160,000 + sell reserve 1,600,000; per-position worst case 253,860,000; install config `max_open` 16):
- **Pre-send (item 1, Am.3 full-wipe bound).** Lane cash = realized − n × 0.25 SOL with n filled opens; realized +15,081,595 now (it moves with every closed trade; the install records the live figure).
  - Admitting the 8th (n = 7): 15,081,595 − 1,750,000,000 − (250,000,000 + 8 × 3,860,000) = −2,015,798,405, inside −2,100,000,000: allowed.
  - Admitting the 9th (n = 8): 15,081,595 − 2,000,000,000 − (250,000,000 + 9 × 3,860,000) = −2,269,658,405, past −2,100,000,000: `lane_budget_presend`.
  - So the pre-send allows **8 opens**; `max_open` 16 never binds. Test `test_am6_presend_admits_8_opens_at_025_and_refuses_the_9th`.
- **`committed_loss_stop`** (realized loss floored at 0): 8 × 253,860,000 = 2,030,880,000 ≤ 2,100,000,000 < 9 × 253,860,000 = 2,284,740,000. Also **8 opens**.
- **Wallet floor** (funded 1,000,000,000, held-once: floor = max(0.05, funded − 0.25) = 750,000,000). Each landed open takes 252,260,000 (stake + rent + send fee) from the balance. The next open needs 250,000,000 + 160,000 + 2,100,000 + (n + 1) × 1,600,000 + 750,000,000.
  - Balance 3,160,503,113 (the manager's reading 2026-11-12, 0 open assumed; re-record it before install): the floor alone admits **9 opens**. n = 8 passes (balance left 1,142,423,113 against a need of 1,016,660,000); n = 9 fails (890,163,113 against 1,018,260,000).
- **Which binds first.** The budget (pre-send and `committed_loss_stop` tie) binds at **8**; the wallet floor at 9. The wallet floor binds first if the balance at install is lower: each 0.252 SOL less balance removes about one open. Run together as the executor runs them, the 9th is refused with `committed_loss_stop` (test `test_am6_committed_loss_stop_and_wallet_floor_capacity_at_025`). The roughly "8–9 positions" of the manager's recommendation is this arithmetic.
- These counts are structural, not a forecast of how many entries the host triggers.

**Why the daily stop is 0.50.** The marked daily stop (`FloorLaneLedger.evaluate`: `view.pnl - snap <= -daily`, where `pnl` counts open filled positions at fresh marks and a stale or missing mark as 0) would at 0.25 × 8 open (2.0 SOL exposed) halt new entries on a roughly 10% dip of the marks (0.20 SOL), or on one stale mark (a 0.25 SOL position counted at 0). The mark is a sell quote, so the pool fees on both legs plus the rent and buy fee held in cash until the close already lower the marked P&L at the fill, about 0.007 SOL per position at 0.25 (about 0.055 SOL with 8 open). `evaluate` also stops on a second, cost-basis arm (`view.cost - base[1] <= -daily`), which carries open filled positions at their stake; at 0.50 that arm is a pure realized-plus-fees loss stop. A 0.20 SOL stop would be a mark-noise switch on the marked arm, not a loss stop. At 0.50 SOL it stays a loss stop, and it is still well under the 2.10 SOL lifetime budget. The stale-mark behaviour itself is unchanged (Am.4 item 6): one stale 0.25 position moves the marked P&L by up to 0.25 SOL, half the stop.

**Consequences.**
- **Daily stop:** checked before an entry only, so one UTC day can lose more than 0.50 SOL. The bound is min(remaining lifetime room, about 0.50 SOL already lost that day plus 8 × 0.2539 SOL of positions admitted after it). The remaining lifetime room cannot exceed 2.1 SOL plus realized gains, about 2.115 SOL now (2,100,000,000 + realized 15,081,595), and lifetime P&L never goes past −2.10 SOL. (Same pattern as Am.5, QP M1 of #1317.)
- **Worst case.** The pre-send admits an entry only if the full-wipe lane P&L after the new stake and one fee reserve (3,860,000) per position is still above −2.10 SOL, and the budget stop writes the lane STOP at −2.10 SOL. So lifetime lane loss is bounded by the 2.10 SOL budget; the fee reserves of every counted position are inside that bound, not on top of it. The buy spends exactly the stake (exact quote-in, spend margin 0): a worse fill price means fewer tokens, not more SOL lost, and the full-wipe bound already counts the whole stake as lost. The fill row books the actual SOL out, so the token-account rent and the buy fee are in lane cash at the fill. Sells stop after 10 attempts, so sell fees stay inside the per-position sell reserve. The remaining real escape paths are rows that never reach the ledger (backlogged rows block entries) and estimated fill rows. The wallet floor (0.75 SOL) and the fills-vs-model check watch those; the wallet cannot be taken below the floor by an entry.
- **Quote gaps:** unchanged (Am.4 item 6): a gap over 60 s or an executor restart empties the marks, and the marked arm counts them 0 until fresh.
- **Budget stop:** at −2.10 SOL lifetime the lane STOP file is written, as for any lane budget stop.

**Price impact (§6; in-sample, Am.4 item 7 arithmetic, V + q ≈ 18 SOL, constant product; figures from the REVIEWER comment on #1318).** Post-buy price / floor at q = 0 is (18.25 / 18)² ≈ 1.0280 at 0.25, against 1.0113 at 0.10. The average-fill premium is about 0.25 / 18 ≈ 1.39%, against 0.56% at 0.10. Round-trip impact is about 0.007 SOL per trade at 0.25, against about 0.0011 SOL at 0.10. So impact in percent scales about 2.5×, and the impact cost in SOL per trade about 6.25× (stake squared). These are model figures, not fills; fills at 0.10 cannot show impact at 0.25, so a fills check runs after the install (Post-install check below). The model's fill price already includes the pool's virtual reserve. The paper replay at 0.25 (above) applies the same impact model. More concurrent positions are in distinct pools (one open per pool), so they do not add impact to each other; they add exposure.

**Bot id.** floor-lane-1 keeps its bot_id and ledger. The change is to three lane ceilings and the config; no decision logic and no host rule changes.

**EXP-029.** Erratum 1g (EXP/EXP-029-floor-lane-1-gate-read-prereg.md, line 289) already ended the gate read as a procedural FAIL at the 0.10 install instant, 2026-11-12T00:46:04.584Z (HELM-REPLY-1111-1), and any later registration keeps S = 2026-11-09T06:00:00Z and every trade since. This amendment changes no registration, writes no EXP-029 erratum, and adds no FAIL-end event; Erratum 1g rule 3 (no second look) is unchanged. The 20-trade check below is a precondition of this install, a descriptive read for the manager, and not a gate read.

**Install.** Not installed by this PR. One HELM-ASK covers the merge sha of this PR, the changed blob `tools/floor_lane_executor.py` (`tools/forge/dsl_lane_executor.py` is not changed and keeps blob 8cbc81a5e7b92790a3dc651fce234af39d4003f7) and a new live config. It is identical to the Am.5 config except `stake_lamports` 250,000,000, `budget_lamports` 2,100,000,000 and `daily_loss_lamports` 500,000,000; `max_open` 16 (the budget binds at 8 by the arithmetic above); `funded_lamports` stays 1,000,000,000 (wallet floor 0.75 SOL); `wallet_floor_count_held_once` true; everything else as in the Am.5 config. Helm restarts the executor only; the decision host is never restarted. Until Helm installs it, the live lane keeps the Am.5 limits (0.10 / 1.20 / 0.20).

Before the ask, all of these hold, and the manager records them in the state file:
- (a) QP OK and REVIEWER APPROVE at the exact head of this PR;
- (b) decided ONCE, on the first 20 closed filled round trips (an exit or a write-off after a fill) at stake 0.10 SOL since 2026-11-12T00:46:04.584Z, in lane-ledger order of exit. Failed or guarded buys do not count toward the 20, but their fees count in the realized sum. Pass = realized P&L after all fees over exactly those 20 ≥ 0. There are no rolling re-checks at 21, 22, ...;
- (c) `tools.floor_fills_vs_model` prints `FILLS_MATCH_MODEL yes` over the 0.10 trades, checked at the 10th and at the 20th of those same trades (the check at 20 is over exactly those 20);
- (d) the unit tests pass at the merge sha (every test file that imports `floor_lane_executor`, plus the arena executor tests: `test_floor_lane_executor`, `test_floor_arena_executor`, `test_floor_risk_accounting`, `test_floor_lane_mark_refresh`, `test_lane_daily_report`, `test_floor_fills_vs_model`, `test_forge_dsl_lane_executor`);
- (e) the lane STOP file and the wallet-wide STOP and HALT are absent, and lane cash and the number of open positions (0 or more) are recorded;
- (f) the wallet balance is recorded, and the wallet-floor capacity at that balance (the floor arithmetic above, with the open positions' stakes already out of the balance) is at least the budget capacity (8); otherwise the lower number is stated in the ask.

A "no" on (b) or (c): the lane stays at 0.10 SOL, nothing is installed, and the manager reports to the owner. The only re-decision is one second checkpoint, on the first 40 closed filled round trips at 0.10, and only with a new owner word.

**Post-install check.** After the install, `tools.floor_fills_vs_model` runs at the 10th closed trade at stake 0.25. A "no" writes the lane STOP file, and the manager asks Helm to return to the 0.10 config (the Am.5 config).

**Scaling past this step.** A stake above 0.25 SOL, or a budget above 2.10 SOL, requires the owner's word in a new amendment, with its stake and a price-impact estimate (§6).

**Unchanged.** Keys, STOP/HALT, A3 pauses, the shared lane ledger and lock, the host, the rule `l1-floor-v2` and its exits, `LANE_MAX_OPEN` 16, the shared `LANE_DAILY_MAX_LAMPORTS` 100,000,000, `FUNDED_MAX_LAMPORTS`, §4, §5, §7, and every other lane bot's limits. `tools/h5_lane_executor.py` and the DSL lane (`DSL_LANE_*`: 0.03 / 0.30 / 8) keep their constants; the stopped H5 lane executors keep theirs.

**Erratum 1 (2026-11-12, verdict nits on #1318; merged as 8dec3a3b; wording only, no limit, code or install condition changes beyond making (b) exact).** From the final QP and REVIEWER verdicts at 82dec346:
1. The combined 11-07 figure in the evidence bullet is −0.0096 SOL (the file's value is −0.009575; QP nit). The line above is corrected in place.
2. In "Why the daily stop is 0.50", the drop of the marked P&L at the fill comes from the pool fees on both legs plus the rent and buy fee held in cash until the close, not from price impact (QP nit). The word is corrected in that sentence only; the "Price impact" section is unchanged.
3. Precondition (b): the realized sum over the 20 trades counts every lane-ledger row of those 20 positions, plus the fee rows of failed or guarded buys whose send falls between 2026-11-12T00:46:04.584Z and the exit instant of the 20th trade (REVIEWER minor 1, QP nit). Failed or guarded buys still do not count toward the 20.
4. In "Worst case", the fills-vs-model check does not catch rows missing from the lane ledger: it compares fills with the model. Only the wallet floor bounds those (REVIEWER minor 2). Read "The wallet floor (0.75 SOL) and the fills-vs-model check watch those" with this correction.

**Erratum 2 (2026-11-12, rent add-back in the November paper figures was overstated; found by the T0 review of PR #1324; figures only, no decision, limit, code or install condition changes).** Cause: the "Evidence the manager had" replay figures add back token-ATA rent as 2 × 1,513,840 lamports per filled trade, but the flat and pressure legs already carry the fail blend (`tools/arena_stack.py` `fail_legs`), so the right add-back is (2 − f) × 1,513,840 per filled trade (flat f = 0.15; pressure f = the row's `p_press_provisional`) and 1 × 1,513,840 per guarded or stranded trade (`scripts/research/floor_nov_pnl.py --send-lamports 160000`, merged 0982c931). Every total in the evidence bullet was too high. Old figures were re-derived from the same ledger by the corrected reader with the old add-back put back (2 × per filled trade, 1 × per guarded trade); they equal the amendment's quoted digits, so each row below is a like-for-like old → corrected pair. All rows are the 0.25 stake, flat leg, first-come cap K = 16 (uncapped, n 50), unless stated; the "post" rows are the post-deploy subset (11-07T19Z onward, same cell). Digits are the reader's, not rounded up: SOL to 6 decimals, percent to 4.

| Quoted figure | Amendment 6 (old) | Corrected |
| --- | --- | --- |
| n | 50 | 50 |
| mean | +5.66% (5.6577) | +5.57% (5.5705) |
| median | −2.21% (−2.2085) | −2.30% (−2.2993) |
| total SOL | +0.7072 (0.707207) | +0.6963 (0.696308) |
| ex top 3 SOL | +0.1988 (0.198778) | +0.1886 (0.188559) |
| positive entry-day buckets | 6 of 8 | 6 of 8 (unchanged) |
| combined 11-07 SOL | −0.0096 (−0.009575) | −0.0118 (−0.011846) |
| UTC days positive | 6 of 7 | 6 of 7 (unchanged) |
| post-deploy n | 17 | 17 |
| post-deploy total SOL | +0.3106 (0.310571) | +0.3067 (0.306711) |
| post-deploy ex top 3 SOL | −0.1035 (−0.103473) | −0.1067 (−0.106652) |
| post-deploy ex top 3 SOL, pressure leg | −0.0970 (−0.097002) | −0.1011 (−0.101139) |

The post-deploy ex-top-3 stays negative, and the median stays negative. Not re-derived: the 0.10 cells and the K = 3 and K = 8 cells are not quoted in this DEC and are not restated here; none of the amendment's other figures came from this replay. The amendment's decision, limits and gate are unchanged: the first 20 closed 0.10 trades must be ≥ 0 after all fees (b), the fills check must read yes at trade 10 and at trade 20 (c), and preconditions (a) to (f) stand as written. The figures remain in-sample, paper, n 50 priced with 29 of 79 entries unpriced (feed_gap), and not a gate read. Original text above is not edited.

Review nits from #1318 and #1319, carried as wording only:
1. 11-07 rounding (QP nit on #1318, "The combined 11-07 figure is −0.009575 SOL. That rounds to −0.0096, not −0.0095"): already applied by Erratum 1 item 1; the figure is now superseded by the corrected −0.0118 above.
2. "Impact" wording (QP nit on #1318, "so 'impact' is not the right word"): already applied by Erratum 1 item 2; no further change.
3. Precondition (b) window (REVIEWER nit on #1319, "Suggest inclusive at both ends, so a failed buy sent in the same ms as the 20th exit is charged"): the window in Erratum 1 item 3 is inclusive at both ends, [2026-11-12T00:46:04.584Z, exit instant of the 20th trade].
4. Provenance of "about 0.007 SOL per position" (REVIEWER nit on #1319, "the number is not derived from the cause now named"): the figure is the round-trip impact estimate of the "Price impact" section (2 × 0.25² / 18.25 = 0.00685; 8 × 0.00685 = 0.0548 for 8 open), taken from the REVIEWER comment on #1318. It was not derived from pool fees plus rent and buy fee, and no fee derivation has been made. Read "about 0.007" and "about 0.055" in "Why the daily stop is 0.50" as that impact estimate. The sentence's cause (pool fees plus rent and buy fee) stays as Erratum 1 item 2 set it. The magnitude is small against the 0.50 stop, and the rationale does not rest on the exact number.

**Erratum 3 (2026-11-13, the 29 November positions that Amendment 6 and Errata 1 and 2 leave unpriced have been priced through their feed holes; figures only, no decision, limit, code or install condition changes).** Sources: PR #1336 (merged 3b0a5c38); job #2527 at 829d90db; result file `/data2/mal/scratch/floor-gap-range/gap.json`, sha256 `31757d8abf106ce3c7389958f2e5c175c03a1ff2ccbbd905d114f58ae1038b66` (copy at `/data/mal/hunt-1008/ops/reports/2026-11-13/job2527/gap.json`); report `/data/mal/hunt-1008/ops/reports/2026-11-12/floor-gap-range.md`, section "Result"; the three QP VERDICT comments on #1336 (8a63b731, 2cee4c0f, 60d203d3, all OK). Paper, in-sample, send 160,000 lamports, the rent-corrected reading of Erratum 2. Group A is the 50 normally priced positions of Amendment 6 and Erratum 2 (its 0.10 flat K = 16 total 0.2715999931 and 0.25 flat K = 16 total 0.6963075077 equal the Erratum 2 figures). Group B is the 29 entries that the engine excluded for feed gaps, priced through the holes as if the holes were absent. A+B is all 79.

1. **The sentence from the QP on #1336, verbatim:** "Erratum (QP on #1336): the n 50 November paper figures leave out 29 positions (entered 2026-11-09 and 11-10) that the engine excluded for feed gaps; priced through the holes (paper, in-sample, job #2527, send 160,000), they sum to -0.6951137523 SOL at 0.25 flat (-0.2845396379 at 0.10 flat), making the 79-position uncapped total +0.0011937554 SOL at 0.25 flat (-0.0129396448 at 0.10 flat); one of the 29 (-0.25219928 SOL at 0.25) is a strand-rule total loss on a 0.13% k fall, not an observed drain, and without it the 79-position total is +0.2533930354 SOL at 0.25 flat."
2. **Counts (gap.json `counts`).** 29 = 24 entered 2026-11-09 plus 5 entered 2026-11-10. All 29 exit at the 24 h time stop (`overlap_time_exit` 29), which is the host tick at the entry decision plus the hold; none shows a take-profit print (`overlap_tp_exit` 0). "0 take-profits" means that no take-profit print was visible: a crossing inside a hole is seen at the first print after it, or never, so hidden take-profits are not excluded. 26 of the 29 exits use the pool's last known print (`derived_last`; 41 of the 50 in group A do too). If a pool traded inside a hole and printed nothing afterwards, that exit state is stale; this is the main unmeasured risk in group B.
3. **Table** (pooled, from `groups[...]["pooled"]` in gap.json; first-come cap K, "uncapped" is K = None; cut-over hour 2026-11-07T18Z excluded; mean and median in percent of stake to 4 decimals, SOL to 6 decimals; the exact digits are in gap.json). The cap runs within each group, so the K = 8 and K = 16 rows of A+B are not the sum of the A and B rows.

| stake, leg | group | cap | n | mean % | median % | total SOL | ex-top-3 SOL |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.25 flat | A (n 50) | K=8 | 39 | 7.3939 | -2.2222 | 0.720907 | 0.213159 |
| 0.25 flat | A (n 50) | K=16 | 50 | 5.5705 | -2.2993 | 0.696308 | 0.188559 |
| 0.25 flat | A (n 50) | uncapped | 50 | 5.5705 | -2.2993 | 0.696308 | 0.188559 |
| 0.25 flat | B (the 29) | K=8 | 12 | -8.6142 | -3.4554 | -0.258427 | -0.246690 |
| 0.25 flat | B (the 29) | K=16 | 20 | -12.7405 | -4.6264 | -0.637026 | -0.651204 |
| 0.25 flat | B (the 29) | uncapped | 29 | -9.5878 | -2.8830 | -0.695114 | -0.709292 |
| 0.25 flat | A+B | K=8 | 48 | 5.1254 | -2.3019 | 0.615043 | 0.107295 |
| 0.25 flat | A+B | K=16 | 67 | 2.4597 | -2.4207 | 0.412001 | -0.095747 |
| 0.25 flat | A+B | uncapped | 79 | 0.0060 | -2.4220 | 0.001194 | -0.506554 |
| 0.25 pressure | A (n 50) | K=8 | 39 | 6.9542 | -2.1069 | 0.678034 | 0.197015 |
| 0.25 pressure | A (n 50) | K=16 | 50 | 5.2290 | -2.1808 | 0.653625 | 0.172607 |
| 0.25 pressure | A (n 50) | uncapped | 50 | 5.2290 | -2.1808 | 0.653625 | 0.172607 |
| 0.25 pressure | B (the 29) | K=8 | 12 | -8.1285 | -3.2770 | -0.243854 | -0.232708 |
| 0.25 pressure | B (the 29) | K=16 | 20 | -12.3011 | -4.1836 | -0.615053 | -0.628447 |
| 0.25 pressure | B (the 29) | uncapped | 29 | -9.2436 | -2.7343 | -0.670161 | -0.683555 |
| 0.25 pressure | A+B | K=8 | 48 | 4.8139 | -2.1842 | 0.577668 | 0.096650 |
| 0.25 pressure | A+B | K=16 | 67 | 2.2998 | -2.2948 | 0.385217 | -0.095802 |
| 0.25 pressure | A+B | uncapped | 79 | -0.0837 | -2.2980 | -0.016536 | -0.497554 |
| 0.10 flat | A (n 50) | K=8 | 39 | 7.2636 | -2.3971 | 0.283282 | 0.079850 |
| 0.10 flat | A (n 50) | K=16 | 50 | 5.4320 | -2.4745 | 0.271600 | 0.068169 |
| 0.10 flat | A (n 50) | uncapped | 50 | 5.4320 | -2.4745 | 0.271600 | 0.068169 |
| 0.10 flat | B (the 29) | K=8 | 12 | -8.8014 | -3.6350 | -0.105617 | -0.100476 |
| 0.10 flat | B (the 29) | K=16 | 20 | -12.9859 | -4.8097 | -0.259717 | -0.264985 |
| 0.10 flat | B (the 29) | uncapped | 29 | -9.8117 | -3.0606 | -0.284540 | -0.289807 |
| 0.10 flat | A+B | K=8 | 48 | 4.9854 | -2.4772 | 0.239301 | 0.035870 |
| 0.10 flat | A+B | K=16 | 67 | 2.3098 | -2.5964 | 0.154760 | -0.048671 |
| 0.10 flat | A+B | uncapped | 79 | -0.1638 | -2.5977 | -0.012940 | -0.216371 |

4. **Sensitivity to one position, FU8vnN6g (position 97a12683585ec423), from the QP on #1336.** It is a pricing-rule total loss, not a drained pool: entry 2026-11-09T12:33:53.180Z, time-stop exit decision 2026-11-10T12:33:56Z, state `derived_last`. The strand rule (`exit_invalid`) prices it at stake plus send plus stranded rent: -252,199,280 lamports at 0.25 (-100.88%). k fell 0.13% (k_x / k_e = 0.998700) against a tolerance of 0.1%. The exit price was 1.25 times the entry price, and under the other pricing rule (sc, no strand class) the same row is `filled` at +44,452,068 lamports at 0.25 (+17.78%). The ledger cannot say whether the k fall is real or an artefact of a stale `derived_last` state. It is the only strand among the 79. Uncapped, n 79, 0.25 flat, total / ex-top-3 SOL: (i) as priced +0.0011937554 / -0.5065542767; (ii) -100% exactly +0.0033930354 / -0.5043549967; (iii) left out (n 78) +0.2533930354 / -0.2543549967; (iv) other-rule price +0.2978451033 / -0.2099029288. K = 8 and K = 16 are unchanged, because the position is capped out of both. At the pressure leg, 0.25 uncapped: (i) -0.0165358669, (iii) 0.2356634131, (iv) 0.2777319049.
5. **Statements of Amendment 6 and Errata 1 and 2 that this bears on.** Judged strictly from the numbers above. Amendment 6 quotes the 0.25 flat K = 16 cell, n 50; with group B added the same cell has n 67 (K = 16 admits 20 of the 29) and the uncapped cell has n 79. Amendment 6 gives no SOL/day figure, so none is judged.
   - "n 50" (evidence bullet): holds as the count of the priced positions; it is not the count of entries (79).
   - "mean +5.66%" (corrected in Erratum 2 to +5.57%, 5.5705): with the 29, the mean is +2.4597% at K = 16 and +0.0060% uncapped. It remains positive in both, and is no longer +5.57%.
   - "median −2.21%" (Erratum 2: −2.30%): with the 29, −2.4207% at K = 16 and −2.4220% uncapped. It stays negative.
   - "total +0.7072 SOL" (Erratum 2: +0.6963): no longer holds for all entries; with the 29 it is +0.412001 at K = 16 and +0.001194 uncapped, and +0.615043 at K = 8.
   - "ex top 3 +0.1988 SOL" (Erratum 2: +0.1886) and "the positive pooled ex-top-3": no longer holds at K = 16 (−0.095747) or uncapped (−0.506554); at K = 8 it is +0.107295.
   - Amendment 6's sentence "The positive pooled ex-top-3 comes from the pre-deploy rows together with the post-deploy rows" no longer applies: with the 29 the pooled ex-top-3 is negative at K = 16 and uncapped.
   - "the ex-top-3 total is a fraction of the total": cannot be said with the 29 at K = 16 or uncapped, because the ex-top-3 total is negative while the total is positive.
   - "6 of 8 entry-day buckets positive … 6 of 7 UTC days positive": no longer holds. With the 29, 5 of 8 UTC entry days are positive (2026-11-03, 04, 05, 06, 08); 11-07 (−0.011846), 11-09 (−0.517911, 28 positions) and 11-10 (−0.030893, 5 positions) are negative. With 11-07 split at the deploy, as in Amendment 6, it is 5 of 9 buckets. Basis: the day counts use gap.json `groups["0.25:flat"]["A+B"]["per_day"]`, whose rows are uncapped; the 11-07 split (−0.005090 before the deploy, −0.006756 after) is not in gap.json and comes from job #2527's paper ledger.
   - "Caveat, the regime the live host runs in: the post-deploy subset … n 17, total +0.3106 SOL, ex top 3 −0.1035 SOL (pressure leg −0.0970)" (Erratum 2: +0.3067, −0.1067, −0.1011): all 29 entered after the deploy. These figures are not in gap.json. They were recomputed by the QP on #1338 from job #2527's paper ledger (`/data2/mal/scratch/floor-gap-range/run/ledger`), with the first-come cap applied within the post-deploy entries, as `floor_nov_pnl.py` does for its pooled "post" scope (that method reproduces Erratum 2's post-deploy n 17, +0.306711 / −0.106652 flat and −0.101139 pressure). Post-deploy, with the 29, 0.25, total / ex-top-3 SOL:

     | post-deploy, with the 29 | flat | pressure |
     | --- | --- | --- |
     | K=8 (n 22) | −0.132716 / −0.285101 | −0.126435 / −0.270170 |
     | K=16 (n 34) | +0.022404 / −0.390959 | +0.021440 / −0.369547 |
     | uncapped (n 46) | −0.388403 / −0.801766 | −0.380313 / −0.771300 |

     The caveat's negative ex-top-3 holds at every cap and is larger; the total is +0.022404 at K = 16 (the quoted cell) and negative at K = 8 and uncapped.
   - "29 feed-gap positions are unpriced": no longer true of the research record; they are priced through the holes here, with the limits in item 6. It remains true of the n 50 figures.
   - "In-sample, not a gate read": holds, and holds for the 79.
6. **Limits.** In-sample paper research; not a gate read. The holes are priced as if absent (as if no data were missing). One burst day carries 24 of the 29 (2026-11-09). No executor cap, wallet floor, gap guard or live fills enter any figure. 26 of the 29 exits use a last known print; hidden take-profits are not excluded. One position decides the sign of the uncapped 0.25 flat total (item 4). Nothing here says the book is or is not profitable. The amendment's decision, limits (stake 0.25, budget 2.10, daily 0.50) and install gate are unchanged by this erratum: the first 20 closed 0.10 trades must be ≥ 0 after all fees (b); the fills check must read yes at trade 10 and at trade 20 (c); preconditions (a) to (f) stand as written. The gate is a live-fills gate, and no figure in this erratum is a gate read.
7. **Two wording minors left from the Erratum 2 reviews (#1328).** (a) Erratum 2 labels nits 3 and 4 "REVIEWER nit on #1319"; both quoted sentences are from the QP verdict on #1319 (QP VERDICT @c0171251, Nits 1 and 2), not the REVIEWER verdict. Read them as "QP nit on #1319". The formula 2 × 0.25² / 18.25 = 0.00685 is written out in the QP verdict on #1318 at 82dec346; the REVIEWER comment on #1318 gives only "about 0.007". (b) Erratum 2 item 4 and "Why the daily stop is 0.50" name the cause of the marked P&L drop at the fill (pool fees plus rent and buy fee), while the number "about 0.007 SOL per position" (and "about 0.055 SOL with 8 open") is the round-trip impact estimate of the "Price impact" section. The number is not a measured or derived value of that cause. This DEC gives no derived figure for it; the 0.50 stop does not rest on the number.

**Erratum 4 to Amendment 6 (2026-11-13, what counts toward the 20 trades of precondition (b) and the fills checks of (c); QP-RULING-1113-am6-count; wording only, no threshold, stake, cap, budget, key, clock, limit, code or install condition changes, and the gate is no easier to pass).** Written 2026-11-13T08:5xZ (`date -u` 08:52:19Z at start of writing). Records only; outcome-blind beyond the two order ids and the one realized figure named in the ruling. Status: in force from its merge with QP OK on the exact head and a reviewer verdict. Source: `/data/mal/hunt-1008/ops/QP-RULING-1113-am6-count.md`, sha256 `fb7974f2f5086e0b8e33558a77b161283566135a8417741899cc705408f589c7`, section "Erratum needed?", whose proposed text is the core below. The ruling worked from the DEC text at main 4ef63d48 and the code of `tools/floor_fills_vs_model.py` and `tools/arena_live_bridge.py` at that commit. Amendment 6's 20-trade check is "a precondition of this install, a descriptive read for the manager, and not a gate read" ("EXP-029" paragraph, line 249); nothing here says floor-lane-1 makes money.

1. **What counts as one of the 20.** Amendment 6 (b): "the first 20 closed filled round trips (an exit or a write-off after a fill) at stake 0.10 SOL since 2026-11-12T00:46:04.584Z, in lane-ledger order of exit". A filled 0.10 buy that the executor unwinds because the host's paper append was refused (`paper_refused`) is a closed filled round trip. Order 73a31cf948fba66e (exit reason `paper_refused`, sell landed 2026-11-13T00:32:59Z, realized −0.000633034 SOL after all fees) counts, as trade 1 of the 20 on the manager's count. A non-rule exit after a fill counts. Beside Amendment 7's lists (i) and (ii), the (b) report lists (iii) every one of the 20 not closed by a rule exit (take profit 1.5x or the 24 h time stop), with its exit reason and cause. The ruling rejected the reading "count only round trips closed by a rule exit": the text does not support it, and it would select on one known outcome.
2. **Its loss counts everywhere the lane counts loss.** All its lane-ledger rows count in the (b) realized sum (Erratum 1 item 3: "counts every lane-ledger row of those 20 positions"). Its loss counts in the lane P&L of §2 item 1, the 2.1 SOL lifetime loss budget ("counts from lane start ... never resets", line 208) and the daily stop of §2 item 4 for 2026-11-13. It is in the (c) `FILLS_MATCH_MODEL` checks at the 10th and at the 20th trade. A `no` that comes from it is a `no`: Amendment 7's report-to-QP path covers list (ii) only, and the path is not extended to list (iii).
3. **Order id e3b7cc9de20e65bd counts when it closes.** Bought 2026-11-12T14:06:15Z at stake 0.10 and filled; an exit on the executor's 24 h clock is "an exit ... after a fill", so it counts in lane-ledger order of exit (trade 2 on the manager's count, if nothing else closes first). It is on Amendment 7 list (ii) ("every one whose position was open across a host restart (host take-profit lost)"), where counting it in (b) is conservative ("can only lower their P&L"). For each list-(ii) position the (b) report states the host-down spans and the host and executor restarts while it was open, and its exit instant against entry plus 24 h. For e3b7cc9de20e65bd it states: its list (ii) membership; host death 2026-11-13T00:33:06Z (HostFatal LedgerWriteFailed), host down to the Helm start at 2026-11-13T02:19:18Z (stopped Helm start 01:15:20Z to 01:15:37Z), host take-profit lost from the first death; host and executor both restarted 2026-11-13T06:03:33Z by an unattended package upgrade while it was open (not a manager or Helm restart; an executor restart empties the marks, line 242; to be recorded in LAB_STATE as a mid-week runner restart); its actual exit instant against entry plus 24 h (2026-11-13T14:06:15Z) and its exit reason as the tool prints it; and that it is the open pre-C position that the EXP-029 procedural-FAIL read must disclose (the ruling cites the `docs/HANDOFF.md` deadline row "11-14 01:00 (R)"). For (c): an exit made on the executor's 24 h clock is flagged under flag_c when its live return is below −12%, whatever exit reason the tool prints. (The tool's own flag_c is exit reason `time_24h` and live return below −12%, and the tool labels an exit with no host `time_24h` row `no_decision_exit`, on which its flag_c cannot fire.) A `no` that comes only from list-(ii) trades goes to quant-proof before any decision, as Amendment 7 rules: "It is not an automatic pass, and not an automatic fail." Any other `no`, including one from list (iii), is a `no`.
4. **Exact-set check.** `tools.floor_fills_vs_model --since/--until` filter on the send instant, while the 20 are fixed in order of exit. At each of the checks at 10 and at 20, the manager states the order ids of the first 10 and of the first 20 in lane-ledger exit order, and confirms that the tool's scored closed set equals exactly that set. If no send window gives that set, the manager reports to quant-proof before deciding.
5. **Counting start.** The count starts at 2026-11-12T00:46:04.584Z, the 0.10 install; Amendment 6's merge and the landing of the owner's capital do not move it. Every closed 0.10 position sent at or after that instant is eligible in exit order. Positions at 0.03 are not "at stake 0.10" and never count. Failed or guarded buys do not count toward the 20; their fee rows count in the realized sum over [2026-11-12T00:46:04.584Z, exit instant of the 20th trade], inclusive (Erratum 1 item 3, Erratum 2 nit 3). Decided once; no rolling re-checks at 21, 22, ... (line 255); the only re-decision is the 40-trade checkpoint with a new owner word (line 261).

**Not checked.** The ruling did not open any ledger, decision log, tape, job status or job log and used no SSH. Its statement that no other 0.10 trade closed before 73a31cf948fba66e ("0 of 20") is the manager's count, not verified by QP; the manager's lane report is the source of that count and of the positions "trade 1" and "trade 2". This erratum did not open any of those either and repeats the ruling's facts only. Nothing in Amendments 6 or 7 other than the counting rules above changes.

## Amendment 7 (2026-11-13): floor-lane-1 decision host "stage 1" install (cheap restart on; the deploy restart is a 24 h refuse-all; `status_span_no_loss` stays off)
Written 2026-11-13T00:1xZ (`date -u` 00:16:13Z) by manager23; revised 2026-11-13T00:2xZ (`date -u` 00:26:07Z) after QP NOT OK and the REVIEWER MINORs at 8db68744 on PR #1335. Nothing in this amendment says floor-lane-1 makes money, and **floor-lane-1 has not cleared the promotion gate**.

**Decider.** manager23. §3 makes a code change a new `bot_id` that is "logged, not re-approved"; this amendment raises no §2 limit and touches no §6 scaling step, so this DEC's text asks for no owner word. It keeps the bot_id by a named exception to §3, as Amendment 2 did. Helm acts on the owner's word in the Helm chat (see Install).

**Sources.** `/data/mal/hunt-1008/ops/QP-RULING-1112-floor-guard-stage1.md` (conditions 1–13; the runbook says the ruling wins over the design: "the ruling wins", docs/runbooks/floor-lane-host-stage1.md line 3); the QP verdicts on PR #1332, the last at 60f9c7a2 (OK; "not an install approval"); `docs/runbooks/floor-lane-host-stage1.md` at main aa05923f; the design `/data/mal/hunt-1008/ops/reports/2026-11-12/floor-gap-guard-v2-design.md`.

**Found.** The decision host refuses every pool for BLOCK_MS (24 h + 1.9 s hold + 60 s cooldown + 1 s tick + 5 s margin) after any host_down span, so any host restart is a day without entries (docs/HANDOFF.md §0 item 1). It also refuses for 6 h after a writer status span in which no row was lost (11-12, 16:04:39Z, 763 ms; HANDOFF §1). PR #1332, merged as main `aa05923fc7d2593c4d70c7c80b2f64dee371d700` with QP OK and REVIEWER APPROVE at head 60f9c7a2, adds two host config flags that narrow those refusals. Both default to false. With both flags off the host is byte-identical to v1 (golden md5 `aba8b479bc9f1841f70fe4dffb288d2d`, which QP verified against main's own code).

**Change** (floor-lane-1's decision host only; config-gated):
1. **What changes, in plain words.** With `cheap_restart` true:
   - **(b) Cheap restart.** Take a restart where the previous run itself had `cheap_restart` true and ended cleanly (`prev_end` `end`) or by a signal (`prev_end` `signal`). The host rebuilds which pools the reference rule bought during the downtime by a silent run of the frozen rule over the warm-up rows (empty book, `now_ms = t_recv + 10 s`, no log row, no engine call, no table write). Only those pools are blocked, each for BLOCK_MS (why `restart_shadow`). Today every pool is refused for 24 h.
   - **Every other previous end keeps the 24 h refuse-all:** `exception` and `in_doubt` (fallback `unclean_exit`), and `unknown` (fallback `unknown_prev_end`). Any completeness failure of QP condition 7 keeps it too (fallbacks `downtime_exceeds_warmup`, `rebuild_unknown`, `rebuild_covers_downtime`, `hook_errors_in_warmup`, `refusal_row_unparseable`, `shadow_error`).
   - **(c) Re-block from refusal rows.** On resume, pool blocks are rebuilt from **all** `entry_refused_gap` rows still inside BLOCK_MS (trigger_ms + BLOCK_MS > now), max-merged with the checkpoint blocks.
   - **(a) A status span is not loss** (`status_span_no_loss`). This amendment does **not** turn it on (item 4).
2. **This change ALTERS DECISIONS, so it is broader than the Amendment 2 precedent**, which changed no host decision and no dry row.
   - Here, a pool that v1 refuses after a host_down span can be entered. The change can only turn refusals into entries. It changes no exit, no trigger the rule sees, and no take-profit or time stop (QP ruling R4).
   - At host level, decisions become a superset of v1's: v1 entries ⊆ v2 entries, and every v2-only entry sits at a v1 refusal row.
   - **At executor level, live fills are not a superset.** QP condition 13: "Host decisions become a superset of v1's, but live fills do not: the extra entries use DEC-035 §2 headroom (pre-send budget, daily stop, wallet floor) and can displace a later entry v1 would also have made."
   - The P1 replay claim (Install precondition (b)) is host-level only.
3. **The deploy restart is NOT cheap.**
   - The first start with `cheap_restart: true` loads a checkpoint that the live v1 host wrote without it. It therefore reads `prev_end: unknown` and takes `fallback: unknown_prev_end`, with `shadow_complete: false`.
   - So the host refuses **every** pool for 24 h after the install, exactly as a v1 restart does. The runbook: "The FIRST start with `cheap_restart: true` from any checkpoint not written with `cheap_restart: true` … refuses ALL pools for 24 h".
   - The checkpoint that run writes carries `guard.cheap_restart: true`, so a later restart can read `prev_end` `end` or `signal`.
   - "A restart within BLOCK_MS of the deploy start's host_down span still refuses every pool until that span's end + BLOCK_MS, because the deploy span is unflagged and stays in the checkpoint; the same holds after any fallback restart. The first restart whose cost is only the shadow-blocked pools is one made more than BLOCK_MS after the last unflagged host_down span ended." (QP R4 on #1335 at 8db68744. It corrects the wording "any restart after the first flagged periodic lane checkpoint, about 60 s after the deploy start" from QP at 60f9c7a2, which the first draft of this amendment quoted.)
4. **`status_span_no_loss` stays `false` under this amendment.**
   - **Switching it on** needs its own amendment or erratum, merged with QP OK and REVIEWER APPROVE at its head. It comes after the counts-only check of QP conditions 4–5: for every hour with a status span since S, the writer hour-health `status_failures` codes are only `status_stale`, `status_missing` or `status_ts_missing`.
   - **While it is on:** the check repeats daily. Any other code (`decoder_blobs_mismatch`, `decoder_blobs_not_pinned`, `event_v_off`) means the lane STOP file, then the flag off. No follower decoder change is made while the flag is on.
   - **Recorded blobs.** These are the writer, adapter and reader that the live writer (#2033 or successor) runs. They are pinned in the docs/HOLDOUT_LEDGER.md W12 line and verified here at main aa05923f:
     - `tools/arena_tip_tape_writer.py`: sha256 `8152da3928dee2b928f647821be07d623e61fb7c417b3b280d16c7dbf176b400`, blob bb753902;
     - `tools/arena_tip_adapter.py`: sha256 `2c1eeba138d83ef370aeefaa4f71d1109166e911df68bbf90016684dd0656116`, blob 4a6b3762;
     - `tools/arena_tip_reader.py`: sha256 `20cb05c7115ed5825bef1034d6ad2e83286554f3f4ef631da2df86ae789c2887`, blob fbb13d28.
   - If any of the three runs at a different blob (the 11-15 writer successor included), the flag stays off, or goes off, until QP re-rules.
5. **Code at install.**
   - **New host.** Main `aa05923fc7d2593c4d70c7c80b2f64dee371d700`, or a later main whose `tools/floor_lane_host.py` and every file the host imports have the same blobs. `tools/floor_lane_host.py` blob `ba7050e1ed3bbf1538a2745f4604110cadb80a7f`, sha256 `201f3d4a19343ff5d978d24c2c981ea3e9cdb26e75549ef7d7bdd42fdd2e0e0b` (the start row's `host_sha256`).
   - **Running v1 host.** Main e89f15364c85: `tools/floor_lane_host.py` blob a4387c24, sha256 `d805085c7a8a32522707a34531b1226172b706820953783338b0985c865a8348`, as in HELM-REPLY-1107-2.
   - **Diff between them.** Over the 38 EXP-029 Appendix A files, `git diff e89f15364c85 aa05923f` touches four:
     - `tools/floor_lane_host.py`;
     - three executor files (`tools/floor_lane_executor.py`, `tools/floor_arena_executor.py`, `tools/arena_live_bridge.py`). They come from Amendments 4–6 and are already in the live executor tree. The host does not import them (grep of the host's import chain).
   - **Unchanged pins.** `FROZEN_SHA` stays `5e12f8f597571d6d5191a7ba38125c67ad723b7d`. PINS and IDENTICAL_AT_FROZEN are unchanged. The checkpoint schema stays `floor_lane_host_ckpt_v1`, with additive optional fields only (QP condition 9).
6. **Config and tree at install.**
   - **The live host today.** Manager job #2538 on mal-fast-0 at 2026-11-13T00:2xZ inspected the unit (names and config keys only). `mal-floor-lane-host.service` (MainPID 3484327, active) runs `/usr/local/lib/mal-floor-lane/venv/bin/python -E -s -B -u tools/floor_lane_host.py run --config /etc/mal-floor-lane/host.json --since-hour 2026-10-29T11`. Its WorkingDirectory is `/usr/local/lib/mal-floor-lane/current`, set by drop-in `/etc/systemd/system/mal-floor-lane-host.service.d/runtime.conf`.
   - **The live host config** is `/etc/mal-floor-lane/host.json`: 451 bytes, root-owned 0644, mtime 2026-11-08 08:30, sha256 `60f7a60a2b6478ec8ef1efaed657ec6ecc4194297f41a765467fb10d95bd2a71`. Its keys are `clock_start_ms`, `end_ms`, `feed_delay_slots`, `frozen_sha`, `gap_lookback_s` (21600), `ledger_root`, `registry`, `root`, `stack_version`, `tape_dir` and `warm_min` (365). It has no `cheap_restart` key and no `status_span_no_loss` key.
   - **The new host config** is a copy of `/etc/mal-floor-lane/host.json` at sha256 `60f7a60a…` with exactly one key added, `"cheap_restart": true`. `status_span_no_loss` stays absent, which the code reads as false (`_cfg_flag`). Every other field stays byte-for-byte identical in value.
   - **Its file name.** DEC-035 sets no naming precedent for the host config (the executor's `live-<sha>.json` names are executor configs). The new file's name is chosen by Helm and recorded with its sha256 in the reply. `/etc/mal-floor-lane/host.json` is kept unchanged for rollback.
   - **The new tree** is a new clean root-owned directory beside `/usr/local/lib/mal-floor-lane/current`, checked out at main aa05923f. How `current` (or the unit's WorkingDirectory) is switched to it is Helm's choice. The rollback switches it back and uses the old config.
   - **The unit's `--since-hour 2026-10-29T11` is dropped** (QP R1 on #1335).
     - **Why.** With a checkpoint present, `--since-hour` still sends the start through `rebuild_table_since` (`tools/floor_lane_host.py`, `main`). That path hard-links (or, where `os.link` fails, copies) every tape hour from 2026-10-29T11 up to the warm-up start into a temporary `floor-lane-seed-*` directory under `<root>/lane/seed-tmp`. The directory is removed after the rebuild; only a SIGKILL during the copy leaves it behind. `PoolTable.rebuild` then applies only rows after the pickle's hwm, so the table is the same as `rebuild_table` gives.
     - **The cost.** In copy mode, one start temporarily needs about the whole tape since 10-29 free on the filesystem that holds the decision root, and every start takes longer (the start duration grows with the copy). Start duration does not decide completeness (QP R1 (a)); triggers during it are stale, then shadowed, so they cost entries, not safety.
     - **Required.** "The new unit runs `… tools/floor_lane_host.py run --config <new config>` **without** `--since-hour`. With the lane checkpoint and the pool-table pickle present, the start uses `rebuild_table` on the tape dir (no links, no copies, the same table); a missing checkpoint or pickle makes the start refuse ("first start (no checkpoint): --since-hour is required"), which is fail-closed and needs the manager. Helm reports the new ExecStart line. Before the start Helm checks that `<root>/lane/seed-tmp` holds no leftover `floor-lane-seed-*` directory and reports it (names and sizes only). The rollback ExecStart is the old one." (QP R1.)

**Bot id.**
- floor-lane-1 keeps its bot_id, its decision root and its ledger, by a named exception to §3 ("A code or prompt change is a new `bot_id`").
- The ground is Amendment 2's: a successor bot_id would orphan the open positions.
- Unlike Amendments 2, 3 and 6, this change alters host decisions (Change item 2).
- Every decision row after the install can be traced to its host version. The start row's binding carries `host_head` and `host_sha256`, and, with a flag on, `guard: {status_span_no_loss, cheap_restart}`.

**Restart cost at any host restart** (this code does not change it). Positions open at a host restart lose the host take-profit and exit on the executor's 24 h clock. The design (floor-gap-guard-v2-design.md) says so twice:
- §3.3: "The live position keeps its own 24 h clock (`:15-16`) but **loses the host's take-profit**."
- "Residual restart cost": "positions open at the restart lose the host take-profit and time-exit at 24 h".

**Authorised host restarts.**
- This amendment overrides "the decision host is never restarted" (Amendments 4, 5 and 6, Install) and docs/HANDOFF.md §0 item 1 ("Never restart `mal-floor-lane-host.service`") for this install, its rollback, and the later restarts of the standing rule below.
- **The install.** One deploy restart, through Helm, by one HELM-ASK; and the rollback restart of the kill line below.
- "**Later host restarts (standing rule).** After the install, a restart of `mal-floor-lane-host.service` is allowed without a new amendment when the code and the host config are the blobs and sha256 this amendment (or a later merged amendment or erratum) installed, and it is made for an authorised install, a rollback, or an operational need (a crash, a host fault, a Helm maintenance). Each such restart goes through Helm, is logged in LAB_STATE, the manager state file and the MiScusi notebook with: the stop and start instants, how the previous process ended (systemd `code=`/status), the `lane_start` `prev_end`, the `lane_restart_shadow` `fallback`, `shadow_complete`, `n_shadow_buys`, `n_blocked_pools` and `blocked_by_why`, and the number of open positions at the stop (they lose the host take-profit). No restart is made while a kill-line check of the previous restart is pending. A restart that changes code or config needs its own amendment or erratum, as before." (QP R3 on #1335.) Each is checked as in Post-install check (b).
- The executor is not restarted by this install.

**Timing.** QP condition 1, quoted exactly:
> "**Install timing.** Not before C = 2026-11-13T00:00:00Z. At or after R = 2026-11-14T01:00:00Z, unless the lane report at the stop instant shows **zero** open positions whose entry is before C (then any time after C). Not inside the 11-15 tip hand-off window (HANDOFF). The stop and start instants go into LAB_STATE, the manager state file and the MiScusi notebook."

- **C and R** are EXP-029 Erratum 1g's. Its rule 1: "C is the first 00:00Z after it, R = C + 25 h". The event was the 0.10 install at 2026-11-12T00:46:04.584Z.
- **The tip hand-off window** is [2026-11-15T05:00:00Z, 2026-11-15T09:00:00Z). It lasts past 09:00Z until all three docs/HANDOFF.md §2 "11-15 06:41" confirmations hold. HANDOFF lists them as three items:
  - "the gates exited 0;"
  - "the successors are running, resumed and caught_up, with no REFUSED;"
  - "a counts-only FLOOR host gap check."

  Sources for the window:
  - docs/runbooks/tip-writer-handoff.md §5: "At the instant (about 11-15T07Z) and after";
  - HANDOFF §2: "Writer #2033 hits its limit ~06:09Z and push #2039 ~06:24Z … The gates (#2501, deadline 08:08Z; #2502, deadline 08:23Z) release the queued successors";
  - the manager's state file (`/data/mal/hunt-1008/ops/manager23/state-manager23.md`, line 2026-11-12T21:44Z): "outside 11-15 05-09Z".
- **Records.** The host stop and start instants go into LAB_STATE (CLAUDE.md: "Record every mid-week runner restart in LAB_STATE"), the manager state file and the MiScusi notebook.
- **Memory.** No heavy replay job runs on mal-fast-0 while the host starts. The 11-08 start used about 6.51 GiB (HELM-REPLY-1107-2).

**Install.** Not installed by this PR. One HELM-ASK covers:
- a clean root-owned checkout of the merge sha above (the host refuses a dirty checkout);
- the blob check of item 5;
- the new config of item 6 (Helm sends the diff);
- one stop and start of `mal-floor-lane-host.service` only;
- the rollback line.

Before the ask, all of the following hold, and the manager records them in the state file.
- (a) **Verdicts.** QP OK and REVIEWER APPROVE at the exact head of this amendment's PR, and the PR is merged to main.
- (b) **Replay proof P0–P2.** This is QP condition 11 as amended by the final verdict at 60f9c7a2.
  - **How it runs.** At aa05923f, as manager MiScusi jobs, one heavy replay at a time.
  - **Outputs.** Counts and md5 only: no P&L, no exit reason or tp/sl mix, no mint or pool names.
  - **Windows** (QP ruling R5): W1 = [2026-11-10T10Z, 2026-11-11T00Z) and W2 = [2026-11-12T00Z, 2026-11-13T00Z), both seeded from 2026-11-02T01.
  - **P0 (W1), through `main replay`,** flags off against both flags on. It prints:
    - the md5 of every decision-log day file (every row kind, chain included), which must be equal;
    - the count of writer status spans in W1, which must be 0.
  - **P1 (W2), through `main replay` with `status_span_no_loss` true.** This is the main-level flags-on test of (a). It prints:
    - the whitelisted spans (n and durations);
    - v1 refusals by (reason, gap kind), and v2 refusals;
    - entries, v1 and v2;
    - v2-only entries matched to v1 refusal rows (the two counts must be equal);
    - v1 entries missing in v2 (must be 0);
    - v2 refusals not in v1 (must be 0);
    - the md5 of the common entry and exit projections (must be equal);
    - at least one v2-only entry (non-vacuous).
  - **P2, through a harness** that drives the host class in `main run`'s order (`note_rebuild` → `lane_start` → `resume` → `run`). `main replay` never calls those. QP R1: "**P2 does not need to measure start time with the unit's arguments.** Start time does not decide completeness. It must call the **same rebuild `main run` makes without `--since-hour`** (`rebuild_table(table, tape_dir, warm_start)`, then `note_rebuild(warm_start, n)`), so that precondition (b)'s P2 drives the install's real path."
    - **Cases.** D ∈ {5 min, 2 h, warm_min − 5 min, warm_min + 5 min, 7 h}, plus one `unclean_exit` case and one `unknown_prev_end` case. T_k is chosen from counts so that at least one reference Buy lies inside the downtime and at least one position is open at T_k.
    - **It prints, per case:** `prev_end`, `fallback`, `shadow_complete`, `n_shadow_buys` and `n_blocked_pools`.
    - **Three claims, each must be `yes`:**
      - SUBSET against the **unguarded, uninterrupted** reference;
      - the shadow blocks cover every reference position open in [T_k, T_k + D + 10 s];
      - the fallback is taken exactly when QP condition 7 fails.
    - **Expected fallbacks:** D = warm_min + 5 min and D = 7 h fall back with `downtime_exceeds_warmup`; the `unclean_exit` and `unknown_prev_end` cases fall back under those names.
- (c) **Seals** (QP condition 12). Before the proof jobs run, the manager:
  - adds a SEALS-BRIEF "FLOOR LANE READERS" line naming the jobs, the code, the read-only dir and the outputs (counts and md5):
    - the jobs are the P0–P2 proof jobs **and the kill-line check job** of Post-install check (the unguarded, uninterrupted replay against the live entry rows after each cheap restart);
    - the code is `tools/floor_lane_host.py` at aa05923f (blob ba7050e1) plus the P2 harness file and the kill-line check file at their commits;
    - the read-only dir is research-0 `/data2/mal/arena-tip-feed/hv7`, or fast-0 `/home/claude/mal-data/floor-tip/tape`;
  - updates the SEALS-BRIEF sha in AGENT-BRIEF-COMMON.

  QP's ruling calls this line (f). The SEALS-BRIEF already has an (f), for the FLOOR research scripts, so the new line (or lines, one for the proof jobs and one for the kill-line check) takes the next free letter after (f).
- (d) **Unit tests** pass at the merge sha: `tools/test_floor_lane_host_stage1.py` and `tools/test_floor_lane_host.py`. The second includes the golden `GOLDEN_V1_SCENARIO_MD5`; it took QP over 600 s, so run it as a job.
- (e) **Stops.**
  - The lane STOP file and the wallet-wide STOP and HALT files are absent.
  - Lane cash and the number of open positions are recorded.
  - Each open position's entry instant is compared with C (Timing).
- (f) **Rollback line**, recorded.
  - These are kept unchanged:
    - the previous host tree, `/usr/local/lib/mal-floor-lane/current` as of the install (the clean root-owned checkout of e89f15364c85; host blob a4387c24, sha256 d805085c…);
    - the previous host config, `/etc/mal-floor-lane/host.json` (sha256 60f7a60a…).
  - Rollback = switch `current` (or the WorkingDirectory) back, point `--config` at the old file, and restart the host through Helm.
  - Main's v1 code loads a v2 checkpoint (QP and the reviewer verified this on #1332). It ignores the extra fields and treats every `shadow_complete` host_down span as unflagged, so it refuses every pool for BLOCK_MS after that span.
  - The rollback restart is itself a host_down span, with the same 24 h refuse-all.

Helm acts on the owner's word given in Helm chat (docs/HANDOFF.md §1); the manager asks the owner to give it.

**Post-install check.** Counts only, by a manager job that prints counts and order ids only (HANDOFF §0 item 4).
- (a) **The deploy start.** All of these hold:
  - the `lane_start` info shows `prev_end: unknown`;
  - the `lane_restart_shadow` row shows `prev_end: unknown`, `fallback: unknown_prev_end` and `shadow_complete: false`;
  - the start row's binding shows `host_sha256` 201f3d4a… and `guard: {cheap_restart: true, status_span_no_loss: false}`;
  - the lane checkpoint written about 60 s later carries `guard.cheap_restart: true`;
  - there is no `entry` row until BLOCK_MS after the host_down span ends;
  - "the start's `table_rebuilt_rows` line shows `since_hour: null`; the start duration (unit start → `lane_start` line → first live tick) is recorded; `<root>/lane/seed-tmp` holds no new directory." (QP R1.)
- (b) **Each later host restart** (the standing rule above). The `lane_start` info and the `lane_restart_shadow` row show `prev_end` `end` or `signal`, and one of:
  - `shadow_complete: true`, with counts for `n_shadow_buys`, `n_blocked_pools` and `blocked_by_why`;
  - a named fallback.

  A restart within BLOCK_MS of the end of the deploy span (or of any other unflagged host_down span) still refuses every pool until that span's end + BLOCK_MS, whatever its own shadow says (Change item 3); the check expects exactly that.
- **Kill line.** Either of these triggers it:
  - "any entry on a pool the reference holds. After each restart whose `lane_restart_shadow` row shows `shadow_complete: true`, a manager job (counts and order ids only) takes every `entry` row of the live decision log in [restart start, restart start + BLOCK_MS] (pool, decision ms) and checks it against the positions of an unguarded, uninterrupted replay of the rule over [restart start − 201 h seed, restart start + BLOCK_MS] on the research-0 tip copy (`/data2/mal/arena-tip-feed/hv7`, the SEALS-BRIEF readers line of precondition (c)). It prints n_live_entries, n_on_reference_held_pools (must be 0) and the order ids of any such entry. It runs once the tip copy covers the window (within 48 h of the restart; a partial run at 24 h is allowed and is completed later). An entry on a held pool is the kill line." (QP R2 on #1335. This is not a re-run of the P2 harness: it tests what the live host did.)
  - **any `floor_lane_host: HostFatal` or `HostError` on the new paths:** the `in_doubt` or `ended` mark, the `lane_restart_shadow` or `entry_gap_span_ignored` evidence rows, or the refusal-row re-block.

  Then set both flags false and restart the host on the old config through Helm (the rollback line).
- **`lane_restart_shadow_failed`.** This row is fail-closed (the span flag is cleared) and is not a kill. No further host restart is asked for until QP has read it.

**Amendment 6 interaction.** QP condition 2 asks the amendment to say "whether v2-host trades at 0.10 count toward Am.6 precondition (b)".

**The rule (QP ruling on #1335 at 8db68744): trades under the new host count toward the 20.**
- **Why.** Same rule `l1-floor-v2`, same executor, same stake. Am.6 (b) is a live-fills gate on "the first 20 closed filled round trips … at stake 0.10 SOL", not a host-version test.
- **What the Am.6 (b) report lists separately:**
  - (i) every one of the 20 whose entry v1 would have refused. That is either:
    - an entry whose trigger lies inside BLOCK_MS after a host_down span flagged `shadow_complete` in a `lane_restart_shadow` row (v1 refuses every pool there); or
    - an entry with an `entry_gap_span_ignored` row whose `then` is allow. There are none while `status_span_no_loss` is false.
  - (ii) every one whose position was open across a host restart (host take-profit lost).
- **Pass/fail.** Precondition (b) is still decided on all 20, as Am.6 registered it.
- **Precondition (c).** QP: "Positions in list (ii) lost the host take-profit, which can only lower their P&L, so counting them in (b) is conservative. For (c), if `FILLS_MATCH_MODEL` reads `no` and the no comes only from list-(ii) positions, the manager reports that to QP before any decision. It is not an automatic pass, and not an automatic fail."
- **When list (i) can be non-empty.** Only after a restart made more than BLOCK_MS after the last unflagged host_down span ended (Change item 3). The install is at or after R, and the deploy start is a 24 h refuse-all.

**EXP-029.**
- `tools/floor_lane_host.py` is an Appendix A file, and EXP-029 already ended on procedure (Erratum 1g).
- Installed after C, the change puts no trade from a changed host inside [S, C), so no EXP-029 erratum is written.
- Erratum 1g rule 3 governs any later read: "Any later registration of `floor-lane-1`, or of `l1-floor-v2` under any lane bot_id, keeps S = 2026-11-09T06:00:00Z and includes every trade since S, at 0.03 and at 0.10 SOL. The clock never restarts." So v1-host and v2-host trades both enter it, split by the host sha and the install instant recorded here.

**Unchanged.**
- §2: every limit, the STOP file and keys.
- §6: scaling.
- The executor, its config and its limits (Amendment 5 is live; Amendment 6 is not installed).
- The rule `l1-floor-v2` (frozen sha 5e12f8f5), its exits, and PINS.
- §3 for any other code change; §4, §5 and §7.
- Every other lane bot.

**Erratum 1 to Amendment 7 (precondition (c) timing).** The SEALS-BRIEF line for the proof jobs (P0–P2, `scripts/ops/floor_stage1_proof.py` at its merged commit) is added before the proof jobs run. The line for the kill-line check job and its file, at their merged commit, is added before that job's first run, and in any case before the first host restart made more than BLOCK_MS after the end of the last unflagged host_down span (the first restart that can be cheap). Until that line exists and the kill-line check has passed its end-to-end smoke run (QP on #1337 at 496d6e5a), no such restart is made. Nothing else in Amendment 7 changes.

Source: the text above is QP's, verbatim, from the QP VERDICT OK on PR #1337 at 97a5532f2d2c56780362d5a3f1135d4a4b679d19 (section "Am.7 precondition (c) after removing killcheck"). Written 2026-11-13T01:2xZ (`date -u` 01:24:45Z). PR #1337 is merged as main 4c862bba8e852154ba730c75196832863985fd74 and holds P0–P2 only; the kill-line check is not on main.

**Erratum 2 to Amendment 7 (precondition (b) windows and seed; QP-RULING-1113-floor-stage1-p2-vacuous).** Precondition (b)'s line "Windows (QP ruling R5): W1 = [2026-11-10T10Z, 2026-11-11T00Z) and W2 = [2026-11-12T00Z, 2026-11-13T00Z), both seeded from 2026-11-02T01" is replaced as follows, because the research-0 hv7 copy had rotated past 2026-11-02T01 (precount #2549) and P2 found no restart instant in the quiet 11-11/11-12 tape (jobs #2563 and #2567: exit 3, 2 reference entries, no T).
- The tape is the frozen hard-link copy `/data2/mal/scratch/floor-stage1-proof/tapecopy-1113/` (list sha256 d0a11d3132f5e206df7eae65de1d7e3036d7d975c05c1ffea30dbda0ad751b91), seed 2026-11-03T01 (its first hour).
- P0: W1 = [2026-11-12T00Z, 2026-11-12T15Z), seed 2026-11-03T01 (at least 201 h before W1). Run as job #2554 at main 1482a80d: PASS.
- P2: window [2026-11-08T00Z, 2026-11-10T00Z), seed 2026-11-03T01, about 119 h before the window. That is shorter than the host's 201 h rule, so P2 runs with the proof script's P2-only `--seed-relaxed` flag (seed at least 96 h before the window), which prints `SEED_RELAXED` and `seed_h_before_from` on every P2 line. The guarded host and the unguarded reference share the seed and the tape, and every P2 claim compares the two, so the shorter seed changes which pools can trigger (only pools born at or after the seed, about 24 to 167 h old in the window) and not what the claims test. Not exercised: triggers of older pools, and the pool-table age-drop path past 201 h.
- If that run is vacuous (exit 3), it is re-run once on [2026-11-09T00Z, 2026-11-11T00Z) with the same seed. If that is vacuous too, the manager stops and asks QP. No other window is tried, and no window is chosen from any P2 output other than this vacuity exit.
- These windows were fixed on 2026-11-13 after the manager knew the per-day entry counts of the November paper replay (`/data/mal/hunt-1008/ops/reports/2026-11-12/floor-nov-funnel.md` line 36: decision-log entries per day 4/6/8/5/11/13/28/5/0 for 2026-11-03..11-11; in-sample, not live) and the two vacuous P2 results, and before any P2 case output. They select where decisions exist, not an outcome.
- The P2 cases, D values, horizon, claims and pass rule are unchanged. SEALS-BRIEF line (g) names the proof script at the merged commit that carries `--seed-relaxed`. Nothing else in Amendment 7 changes.

Source: the text above is QP's, verbatim, from `/data/mal/hunt-1008/ops/QP-RULING-1113-floor-stage1-p2-vacuous.md` (Revision 1, sha256 dc4e1746399440864b9d24aa36ea0a59f86f87944a8238fe0482bca55bd55492), section "Erratum text". Written 2026-11-13T04:3xZ (`date -u` 04:31:13Z); Revision 1 2026-11-13 (QP review of PR #1346).

**Erratum 3 to Amendment 7 (precondition (b): P1).** For this install, which keeps `status_span_no_loss` false (Change item 4), precondition (b) is met by P0 and P2 as set out in Erratum 2. P1 (the `main replay` test with `status_span_no_loss` true on a window that holds a writer status span) is not run for this install; it becomes a precondition of the amendment or erratum that switches `status_span_no_loss` on (Change item 4), which declares its window, seed and tape. Nothing else in Amendment 7 changes.

Source: the text above is QP's, verbatim, from `/data/mal/hunt-1008/ops/QP-RULING-1113-floor-stage1-p2-vacuous.md` (sha256 71062fc6e22ae0b4d58a981416940f5325e05531af202004f4e5c9497ba67f3b), section "Addendum 1" item (1). Written 2026-11-13T07:0xZ (`date -u` 07:07:28Z).

**Erratum 4 to Amendment 7 (wording: trigger-pool counts are not entry counts; written 2026-11-13T09:41Z from `date -u`; records only; changes no condition, threshold, window, pin or clock; in force from its merge with QP OK on the exact head and a reviewer verdict).** Erratum 2's bullet "These windows were fixed on 2026-11-13 after the manager knew the per-day entry counts of the November paper replay (`/data/mal/hunt-1008/ops/reports/2026-11-12/floor-nov-funnel.md` line 36: decision-log entries per day 4/6/8/5/11/13/28/5/0 for 2026-11-03..11-11; in-sample, not live) …" calls figures "per-day entry counts". The figures 4/6/8/5/11/13/28/5/0 in that bullet are per-day counts of the funnel's distinct trigger pools (in-sample, November paper replay), as funnel line 36 says ("match the funnel's distinct trigger pools"). They are not live entry counts of any book, and they are not entry counts of the replay: they sum to 80, while the replay has 79 entries (the reviewer reads the record of #1338 and the QP verdict on #1336 as settling that for 2026-11-08 the figure 13 is the trigger-pool count and 12 is the entry count; not re-checked here). No figure, window, seed or pass rule changes. Erratum 2 is not edited.

Source: the reviewer's minor on PR #1346 at the head carrying Erratum 2 Revision 1 (verdict comment, "MINOR (for QP's ruling file, not this PR …)"). The reviewer gave no replacement text; this paragraph states which figures are trigger-pool counts and changes none.

## Amendment 8 (2026-11-13): floor-lane-1 "bundle 2": the host's `status_span_no_loss` on (part A) and a FLOOR-only A3 flag list in the executor (part B)
Written 2026-11-13T12:4xZ (`date -u` 12:41:58Z) for manager23; the code of part B is the builder's at 55ad180979fad9d6ac21d670f1f3b106d5672233. Nothing in this amendment says floor-lane-1 or `l1-floor-v2` makes money, and **floor-lane-1 has not cleared the promotion gate**. No promotion-gate threshold changes.

**Decider.** manager23 for this text. The install needs the owner's word (Install). floor-lane-1 keeps its bot_id, decision root and ledger by a named exception to §3, on Amendment 2's ground (a successor bot_id would orphan open positions), as Amendments 4–7 did.

**Sources.** Part A: `/data/mal/hunt-1008/ops/reports/2026-11-12/floor-gap-guard-v2-design.md` §0 and §2.1; `/data/mal/hunt-1008/ops/QP-RULING-1112-floor-guard-stage1.md` (sha256 66d860cb…) conditions 3–5; Amendment 7 item 4 and Errata 1–3. Part B: `/data/mal/hunt-1008/ops/QP-RULING-1113-a3-burst-recheck.md` (sha256 b0d37eeba540cc3b6f406f26646428df18cde8957e6f76e501ba4a4330e0f3c6), section Q3 ("DEFENSIBLE, with the allowlist below"; Q4: "the durable fix"); `/data/mal/hunt-1008/ops/reports/2026-11-13/other-quote-graduations.md` (sha256 ccd04665…); MiScusi notebook n_L5RQa1IeCA4Jlg and n_eYfBbStvz_mpMw (the manager's facts; not re-read here).

**Found.**
- Part A: on 2026-11-12 one writer status span (`tip_gap_status_unhealthy`, prog all, missed_slots 0, 763 ms, at 16:04:39Z) lost no row, yet the host refused every entry for about 6 h (`gap_lookback_s` 21600; Amendment 7 "Found").
- Part B: the A3 record of 2026-11-13T07:10:05Z had `boost_share_low` and `synthetic_share_high` set and two BOOST rules not evaluated. The cause was one creator account graduating 19 of 30 other-quote coins at 06:31–06:38Z. The FLOOR executor paused new entries until a later clean record (up to 12 h).
- Text against code: §4 and the `a3_pause_tick` docstring (`tools/floor_lane_executor.py`) name only `pins_changed` / `program_changed` and stale or unreadable records, and §4 adds "Other A3 flags (synthetic share, feed health) pause entries". The code, through `h5_arena_executor.a3_record_problem` (blob b96f023f), pauses on every halt flag except `synthetic_share_high` (`A3_GUARD_FLAGS`), and on no warn flag except `program_changed`. Item B1 settles which holds. The docstring is not edited.

**Scope.** Two independent config switches, each off by default and each installable alone:
- No change to the stake, `max_open`, budget, daily stop, wallet floor, keys, STOP/HALT files, or the rule `l1-floor-v2` (frozen sha 5e12f8f5, its exits, PINS). §2 limits and §6 are unchanged.
- The H5 books, `tools/h5_arena_executor.py` (including the shared `a3_record_problem`), the DSL lane (`tools/forge/dsl_lane_executor.py`, same function and path) and the A3 monitor are untouched. The A3 record is never edited by hand.

**Part A. `status_span_no_loss: true` on the decision host.**
- **A1. What it does** (`tools/floor_lane_host.py` lines 22–24, 83, 198–210, 287–289, 465–469; Amendment 7 item 1(a)). A tip-writer status pause span stops counting as data loss for the gap guard. A span counts as no-loss only when it matches a raw gap row that the host captured itself in `_on_row` with: `type` gap, `src` writer, `prog` all, `kind` `tip_gap_status_unhealthy`, `reason` `status_unhealthy`, `data_loss` true, `missed_slots` 0, `from_slot` == `to_slot`, and `silent_ms` == `to_ms − from_ms` ≤ 120 000 (QP condition 3). Every other span still refuses as in v1: reader problem, writer hole, every adapter (`src` tip) span, and any span reloaded without the captured fields. A Buy that the span alone would have refused logs an `entry_gap_span_ignored` row (`then` allow or refuse). The flag only turns refusals into entries. It changes no exit, no trigger, no host_down handling and no pool block.
- **A2. Config.** The host config installed by Amendment 7 (`"cheap_restart": true`, file named in Helm's reply with its sha256) plus exactly one key, `"status_span_no_loss": true`. Every other value stays byte-for-byte the same. The code is unchanged: `tools/floor_lane_host.py` blob ba7050e1ed3bbf1538a2745f4604110cadb80a7f, sha256 `201f3d4a19343ff5d978d24c2c981ea3e9cdb26e75549ef7d7bdd42fdd2e0e0b`. These are Amendment 7's item 5 values, and they equal main 71a446b4 and this PR's head (`git diff origin/main...HEAD --stat` touches only `tools/floor_lane_executor.py` and `tools/test_floor_lane_a3_flag_list.py`).
- **A3. The restart.** Part A is one host restart under Amendment 7's standing rule for later host restarts. Its "needs its own amendment" clause is met by this amendment. It is allowed only:
  - after stage 1 is installed and its Post-install check (a) is recorded. If stage 1 is not installed when part A is wanted, part A waits;
  - more than BLOCK_MS after the end of the last unflagged host_down span: the stage-1 deploy span, the 2026-11-13T06:03:33Z unattended-restart span, or any later fallback span. Inside that window the host refuses every pool whatever its config (Amendment 7 item 3), so the restart would buy nothing and would only add a span;
  - with no kill-line check of an earlier restart pending (standing rule);
  - not inside the 11-15 tip hand-off window (Amendment 7 Timing).

  The previous run will have `cheap_restart` true and a Helm stop ends it by signal, so this restart can be **the first cheap restart**. Amendment 7 Erratum 1 therefore applies in full: SEALS-BRIEF lines (h) and (i) exist, and the kill-line check must have passed its end-to-end smoke run before this restart. After it, the kill-line check runs as Amendment 7 states. It must print `n_on_reference_held_pools` 0, and any entry on a held pool is the kill line. A named fallback (`shadow_complete` false) is not a kill. It costs a 24 h refuse-all, after which part A stays on.
- **A4. Amendment 7 item 4 binds while the flag is on.** The writer, adapter and reader must run at the blobs recorded there (8152da39…, 2c1eeba1…, 20cb05c7…), or the flag stays off (or goes off) until QP re-rules; the 11-15 writer successor is included. The counts-only `status_failures` check repeats daily. Any code other than `status_stale`, `status_missing` or `status_ts_missing` means the lane STOP file, then the flag off. No follower decoder change is made while the flag is on.
- **A5. Amendment 6 count.** From part A's install, Amendment 7's list (i) can hold entries with an `entry_gap_span_ignored` row whose `then` is allow. Amendment 7's rule for the 20 is unchanged.

**Part B. FLOOR-only A3 flag list (executor key `a3_floor_flag_list`).**
- **B1. The list in force for floor-lane-1** (this settles §4 and the docstring for this lane only; the H5 books and the DSL lane keep their own text and code). New entries pause, and exits keep running, while the newest line of the configured A3 record:
  - **key absent or not JSON `true` (today):** has any `halt.flags` entry that is set, malformed or `evaluated` false, except `synthetic_share_high`; or has `warn.flags.program_changed.warn` true; or is unreadable or empty (`a3_record_unreadable`), has no halt flags, has no `run_utc`, is more than 36 h old, is more than 5 min in the future, or raises (`a3_record_error`). `synthetic_share_high` and every other warn flag do not pause floor-lane-1. That matches QP's view on the DEC-031 Am.27 item 12 r4-b-T precedent: FLOOR trades WSOL pools at least 24 h old and has no synthetic-class term. §4's "synthetic share, feed health" words do not apply to floor-lane-1.
  - **key `true`:** the same, except that `boost_share_low`, `boost_last_slice_early` and `boost_budget_or_slices_changed` never pause, whether set, unevaluated or malformed. This is an allowlist: any flag not named here, including a new monitor flag, still pauses. `pins_changed`, `program_changed` and `boost_disabled` (set or unevaluated) still pause. Every record problem still pauses, even when the record also holds an ignored flag. Code: `FLOOR_A3_IGNORED_FLAGS` and `a3_pause_tick` in `tools/floor_lane_executor.py`.
- **B2. Why the three do not concern `l1-floor-v2`.** The rule enters on WSOL PumpSwap sell prints of pools 24 h to 200 h old (`tools/arena_rule_l1.py` lines 3–20). None of `tools/arena_rule_l1.py`, `tools/floor_lane_host.py`, `tools/arena_live_bridge.py` or `tools/arena_pool_table.py` contains "boost" (grep at 55ad1809). `tools/floor_arena_executor.py` mentions it once, in a docstring about the H5 executor, and `tools/floor_lane_executor.py` only in this PR's new constant. The three flags measure the keeper BOOST TWAP of new graduations (`tools/pump_structure_monitor.py` lines 1389–1434):
  - InitBoost share below 0.80 of sampled non-mayhem WSOL graduations;
  - median keeper-verified last slice under 315 s after the migrate tx;
  - median BOOST budget or slice count more than 20% off its pins.

  **What is given up:** a pause on BOOST-mechanics changes and on samples too thin to judge them. These three flags are also A3's only signal of a change in the BOOST regime that shapes the graduated pools reaching FLOOR's 24–200 h window, and FLOOR's evidence (the in-sample hours and every live trade) was gathered under the regime they watch. With the key on, such a shift reaches FLOOR only through the rule's own per-row tests (V in [17.5e9, 17.7e9] lamports, `tools/arena_pool_table.py` line 35; real quote vault ≤ 0.5 SOL after the print, `tools/arena_rule_l1.py` lines 7–12) and the §2 loss limits, not through A3. A real pump or PumpSwap program change, fee or global-config change still pauses through `pins_changed` (deploy slots and the fee and global-config hashes) or `program_changed`, and an unread or changed GlobalConfig still pauses through `boost_disabled` (QP Q3).
- **B3. Where the key lives.** The executor's live config `/etc/mal-floor-lane/live-<pin sha>.json` (live today: `live-320cccc5f2ec81d40d18f8991f11b15d29b52f74.json`, sha256 aa6c355f…, Amendment 5, LAB_STATE). The new config is the config in force at the install plus exactly one top-level key, `"a3_floor_flag_list": true`. It is the Amendment 6 config if that step installs in the same visit. Only JSON `true` turns it on (`raw.get(...) is True`).
- **B4. Code at install.** `tools/floor_lane_executor.py` at the merge sha (blob 99ca24d4926d5938a3aeb840114b520db02f72ab at 55ad1809; Helm reports the merged blob). That blob also carries the Amendment 6 code ceilings, merged in 8dec3a3b: stake bounds 0.02–0.25 SOL, budget 2.10 SOL, daily 0.50 SOL. The live 320cccc5 tree has the Amendment 5 ones. **If part B installs before the Amendment 6 gate passes,** the config keeps the Amendment 5 values (stake 100,000,000; budget 1,200,000,000; daily 200,000,000 lamports) and Helm's config diff shows only the one added key. The limits in force are the config's. The raised ceilings bound what a config may hold. They are not limits in use. The part-B HELM-ASK states that the blob carries the Amendment 6 code ceilings, whether the Amendment 6 gate has passed, and the `stake_lamports`, `budget_lamports` and `daily_loss_lamports` of the new config. Until the Amendment 6 install, records say "Amendment 6 code ceilings present; Amendment 6 config not installed", never "Amendment 6 installed".
- **B5. The restart.** An executor-only restart (`mal-floor-lane-executor`), as in Amendments 4–6. It is not a host restart: it adds no host_down span and no refuse-all. It does empty the marks, so one open position trips the marked arm until marks are fresh again (Amendment 4 item 6).
- **B6. Proof precedent.** Amendments 4, 5 and 6 installed executor code changes on QP OK, REVIEWER APPROVE and the unit tests at the merge sha, with no replay proof: lines 121–124 (Am.4 (a), (c)), 183–186 (Am.5 (a), (c)) and 253–257 (Am.6 (a), (d)). Am.4 (line 117) gave the ground: "execution-only … no host decision and no dry row, because the port is live-only". Part B is the same kind of change. The executor reads A3 only with a lane ledger (`a3_pause_tick` returns when `self.lane is None`, and DRY configs have none), so a DRY md5 replay cannot see it. The QP Q3 (e) replay ask is therefore met by tests:
  - with the key absent, the pause decision and every logged row are unchanged (`test_off_boost_flag_pauses_unchanged`, `test_default_is_off`, `test_flag_off_never_logs_a3_ignored`, and the 112 cases of `tools/test_floor_lane_executor.py` with `tools/test_floor_lane_a3_flag_list.py`);
  - with the key on, the allowlist behaves as B1 (`test_on_*`).

  QP is asked to confirm that ground (PR open question).

**Install.** Not installed by this PR. Each part goes by HELM-ASK through Helm, with the owner's own word in the Helm chat. New code and config are outside the owner's 2026-11-13 restart pre-approval: it covers "future FLOOR restarts on the same code and config (startup-only fixes; excludes new code, caps, keys, funds, the clock)", HELM-REPLY-1113-2 as recorded in docs/HANDOFF.md. Part A, part B and the Amendment 6 stake step (0.25 SOL, gated on 20 closed trades at 0.10) may go in one Helm visit or separately. Each keeps its own preconditions, and this amendment does not change the Amendment 6 gate. Before the ask, the following hold for the part(s) asked, and the manager records them in the state file:
- (a) **Verdicts.** QP OK and REVIEWER APPROVE at the exact head of this PR, and the PR merged to main.
- (b) **Part A: P1 PASS.** P1 is Amendment 7 precondition (b)'s P1, quoted exactly (lines 469–477):
  > - **P1 (W2), through `main replay` with `status_span_no_loss` true.** This is the main-level flags-on test of (a). It prints:
  >   - the whitelisted spans (n and durations);
  >   - v1 refusals by (reason, gap kind), and v2 refusals;
  >   - entries, v1 and v2;
  >   - v2-only entries matched to v1 refusal rows (the two counts must be equal);
  >   - v1 entries missing in v2 (must be 0);
  >   - v2 refusals not in v1 (must be 0);
  >   - the md5 of the common entry and exit projections (must be equal);
  >   - at least one v2-only entry (non-vacuous).

  - **Declared here** (Amendment 7 Erratum 3). The author wrote this while job #2602 was running and read none of its output. Window W2 = [2026-11-12T00Z, 2026-11-13T00Z). Tape: the frozen copy `/data2/mal/scratch/floor-stage1-proof/tapecopy-1113/` (list sha256 d0a11d3132f5e206df7eae65de1d7e3036d7d975c05c1ffea30dbda0ad751b91; the only tape that SEALS-BRIEF line (g) lets `p1` read). Seed 2026-11-03T01, its first hour, at least 201 h before W2 with no `--seed-relaxed`. Script `scripts/ops/floor_stage1_proof.py` blob 06016c55, the same at 1670b44f and 71a446b4.
  - **PASS** means the run exits 0 and prints `P1 PASS`, and its P1 line shows every item above. The script's `triggers_changed_MUST0` and `exits_changed_MUST0` are the "must be equal" item. The P1 output's printed window, seed hour and tape list sha256 must also equal the declaration above.
  - **The job.** MiScusi #2602, at main 71a446b4. A PASS there stays valid at the merge sha while `tools/floor_lane_host.py` has sha256 201f3d4a… and every file the host imports has its 71a446b4 blob. This PR changes neither.
  - **If vacuous** (exit 3: no status span in the window, or no v2-only entry), P1 runs once more, on the script header's pre-declared fallback `--from 2026-11-11T10 --to 2026-11-13T00`, with the same tape and seed.
  - **If that is vacuous too, or either run FAILs** (exit 1; a FAIL gets no fallback), part A is not installed under this amendment, the manager reports to QP, and part B may install alone. No other window is tried.
- (c) **Part A: Amendment 7 item 4 checks.** The counts-only `status_failures` check over every hour with a status span since S = 2026-11-09T06:00:00Z finds only the three whitelisted codes. The writer, adapter and reader blobs check (item A4) holds on the day of the restart.
- (d) **Part A: restart conditions.** Item A3 holds: stage 1 installed, its Post-install check (a) recorded, Erratum 1's smoke PASS, more than BLOCK_MS since the last unflagged host_down span ended, no kill-line check pending, and outside the 11-15 window.
- (e) **Part B: unit tests at the merge sha.** These are the Amendment 6 (d) set plus `tools/test_floor_lane_a3_flag_list.py`. Measured at 55ad1809 on research-0: 291 passed over those eight files (112 for `test_floor_lane_a3_flag_list` + `test_floor_lane_executor`). The host tests are not required, because the host code is unchanged.
- (f) **Stops.** The lane STOP file and the wallet-wide STOP and HALT files are absent. Lane cash and the number of open positions are recorded.
- (g) **Rollback line.** It is recorded. For part A: the Amendment 7 host config, kept unchanged. For part B: the previous live executor config and tree, kept unchanged.
- (h) **Part B: QP Q3 (b) count.** A manager job, counts only (flag names and record instants; no outcome, fill or P&L), reports over every A3 record since floor-lane-1 first had `a3_record_file` configured: the number of records; the number whose only set, unevaluated or malformed halt flags (other than `synthetic_share_high`) are among the three in B1; and the hours FLOOR spent in `a3_pause` whose `why` named only those flags. The figures go into the state file and the HELM-ASK. Q3 (b) is not supplied in this amendment.

**Post-install check** (counts and order ids only, by a manager job).
- **Part A.** The start row binding shows `host_sha256` 201f3d4a… and `guard: {cheap_restart: true, status_span_no_loss: true}`. The `lane_start` and `lane_restart_shadow` rows show `prev_end` `signal` or `end`, with `shadow_complete` true or a named fallback. Then:
  - the kill-line check of item A3;
  - the first `entry_gap_span_ignored` rows, reported by count (split by `then`) and the order ids of the entries they allowed only;
  - the daily `status_failures` check.
- **Part B.** The executor start row carries `a3_floor_flag_list: true`. The first `a3_ignored` rows, and `a3_pause` / `a3_resume` rows with `ignored`, are reported by count and order ids only. A pause on any flag outside the three, or on a record problem, still shows as `a3_pause`. The start row's `lane` block shows `stake_lamports`, `budget_lamports` and `daily_loss_lamports` equal to the installed config (100,000,000 / 1,200,000,000 / 200,000,000 before the Amendment 6 gate); any other value is a rollback.
- **Records.** Each restart's stop and start instants go to LAB_STATE, the manager state file and the MiScusi notebook (Amendment 7 standing rule; CLAUDE.md).

**Rollback.** Remove the key (or keys) from the config and restart the same unit through Helm. No code rollback is needed, because with the key off the code behaves as before.
- **Part A's rollback** is a host restart under Amendment 7's standing rule, and is cheap only when item A3's conditions hold.
- **Part B's rollback** is an executor-only restart.
- **Triggers.**
  - A kill-line hit or a `HostFatal` / `HostError` on the new paths: Amendment 7's kill line (both host flags false, old config).
  - A non-whitelisted `status_failures` code: lane STOP file, then part A off.
  - A writer, adapter or reader blob change: part A off until QP re-rules.

**Unchanged.** §2 (every limit, the STOP file, keys), §3 for any other change, §4 for every other lane book, and §5, §6, §7. Amendments 4–7 and their errata; the Amendment 6 gate and its count; EXP-029 Erratum 1g rule 3 (any later registration splits trades by host sha and executor config, using the install instants recorded here). Every other lane bot. Nothing here says the rule makes money, and no promotion-gate threshold changes.

**Erratum 1 to Amendment 8 (precondition (b): the P1 job; item B6's test count; written 2026-11-13T14:32Z from `date -u`).**
1. **Job #2602 gave no P1 result.** Precondition (b) names "MiScusi #2602, at main 71a446b4". The manager submitted that job without `--tape-src`, so the script linked the research-0 hv7 copy directly and not the frozen copy `tapecopy-1113`. Its v1 replay ran 2026-11-13 12:33Z to 13:09Z with seed 2026-11-03T12, the first hour then in hv7. hv7 retention then removed hour 2026-11-03T12, the v2 replay failed in its seed rebuild, and the script exited 4 (`REFUSED: replay child failed`). Its only output was the `P1-WINDOW` line (1 status span, 763 ms). No v1 against v2 comparison was computed or seen. The manager found the cause at about 13:10Z from: the script's stdout file; a listing of file names and sizes in the run dir; the two replay config files `v1/config.json` and `v2/config.json` (paths and flags only); the names and link targets of two `tapelink` entries; a line count of both `child.log` files; and `p1-1113/v2/child.log` itself (22 lines, opened by the manager: one zstd error line and a Python traceback with file paths and line numbers; no row content, pool id, price or P&L). `v1/child.log`, the decision logs and the ledger dirs were not opened. Line (g) lets only the script read the run dirs, so opening that file is a second disclosure item.
2. **Disclosure.** The v1 replay of #2602 read hv7 hours [2026-11-03T12, 2026-11-13T00) directly (last hour 2026-11-12T23). SEALS-BRIEF line (g) lets the proof read those hours only through the frozen copy. The files are the copy's own (hard links; #2606 verified 227 of the copy's hours as the same inode as hv7, and hour 2026-11-03T12 by its listed size), and the hours lie inside line (g)'s range. No row content, price or P&L was printed or read. The run dir `/data2/mal/scratch/floor-stage1-proof/p1-1113` is kept read-only. Notebook n_P2cO0QlbHx49tg.
3. **(b) is read with job #2606 in place of #2602.** Command: `sh scripts/ops/floor_stage1_proof.sh p1 p1-1113b --tape-src /data2/mal/scratch/floor-stage1-proof/tapecopy-1113`, at main 1670b44fe108b47e14ccf9330a268c5f63e1cb2f, the commit SEALS-BRIEF line (g) names. At 1670b44f, at 71a446b4 and at the Amendment 8 merge 9c52f4fc these blobs are identical: `scripts/ops/floor_stage1_proof.py` 06016c551abe62885ce980f31bbffd0c3f160678, `scripts/ops/floor_stage1_proof.sh` 43d1ad88ce992da8335c7e745e0379d9c3a7a166, `scripts/research/floor_tape_paths.py` b99f50bf018710883ee0fe9981d23d452b7791e0, `tools/floor_lane_host.py` ba7050e1ed3bbf1538a2745f4604110cadb80a7f (sha256 201f3d4a…). The window, tape and seed are the ones declared in (b): W2 (the script's default), `tapecopy-1113`, seed 2026-11-03T01. #2606 is the first P1 run to reach a comparison. No window or argument was chosen from any P1 output.
4. **Result of #2606** (2026-11-13, exit 0; output `/data2/mal/scratch/floor-stage1-proof/p1-1113b.stdout`, sha256 08b825ce71cbdf424c82bf36b7ab77d7367b5a4685240829b886e07c5ef0aa41, run dir read-only): `P1 PASS`. The printed fields:
   - `status_spans=1`, `durations_ms=763`, `by_kind={'tip_gap_status_unhealthy': 1}`;
   - `entries_v1=1 entries_v2=2 entries_common=1 v2_only_entries=1 v2_only_at_v1_refusal_rows=1`;
   - `v2_only_not_at_v1_refusal_MUST0=0 entries_on_triggers_v1_never_saw_MUST0=0 v1_entries_missing_in_v2_MUST0=0 triggers_changed_MUST0=0 exits_changed_MUST0=0 v2_refusals_not_in_v1_MUST0=0 v1_refusals_dropped_unexplained_MUST0=0`;
   - `refusal_rows_v1=3 refusal_rows_v2=0`, `refusals_v1_by_reason_gapkind={'feed_gap/tip_gap_status_unhealthy': 1, 'pool_blocked_after_gap/none': 2}`, `span_ignored_rows_v2=1`;
   - `common_entry_md5=bb2e6b7456d8998c44e72e53be66a8da common_exit_md5=c676ed1d3bec334e54b7133560eb6275 equal_counts_v2only_vs_refusals=True nonvacuous=True pass=True`;
   - `seed_hour=2026-11-03T01 earliest_valid_from=2026-11-11T10 list_sha256=d0a11d3132f5e206df7eae65de1d7e3036d7d975c05c1ffea30dbda0ad751b91 hours_checked=239 hours_same_inode=227 hours_no_longer_in_hv7=12 size_mismatch_MUST0=0 not_same_inode_MUST0=0 missing_or_unlisted_MUST0=0`.
   - also printed: `status_kind_any_src=1`, `n_gap_rows=1`, `refusal_triggers_v1=3 refusal_triggers_v2=0`, `refusals_v2_by_reason_gapkind={}`, `status_spans_in_window=1`, `test_mode=False`.

   The printed seed hour and tape list sha256 equal the declaration in (b). The script does not print the window: it is W2 = [2026-11-12T00Z, 2026-11-13T00Z) because the command above has no `--from`/`--to` (constant `W2`, `cmd_p1` → `common(a, W2, "p1")`, `scripts/ops/floor_stage1_proof.py` blob 06016c55, lines 87 and 932), and the manager confirmed that command against the MiScusi record of #2606. QP rules that this meets (b)'s printed-window clause for #2606 (QP comment on PR #1354 at d20598dc). The proof is thin: it rests on one writer status span and one v2-only entry. It says nothing about money.
5. **Item B6's "112 cases"** is the count at 55ad1809. At the merge sha 9c52f4fc the count is 115 for the two files and 294 for the eight-file set of precondition (e) (job #2605, research-0; executor blob fd7e36c1ed1d9f0f6cfd69a07af93371c9cc278d).
6. **Unchanged.** Preconditions (c), (d), (f) and (g) for part A are still owed at the part-A ask. The pass rule, the fallback rule and every other line of Amendment 8 stand. Part B does not depend on P1.

## Amendment 9 (2026-11-13): open simulation books
Written 2026-11-13T18:1xZ (`date -u` 18:12:55Z) for manager23, at main d1e752be. Nothing here says FLOOR or any variant makes money; **no book has cleared the promotion gate**. No promotion-gate threshold changes.

**Decider and ground.** manager23, on the owner's direction of 2026-11-13 (~17:5xZ). The manager's record of the owner's chat message (`memory/owner-open-sims-1113.md`, shortened there) has these two sentences:
> "We need to start running simulations and forward tests as if they're trading live at certain points in which we think would start to generate more profit and see how they are doing in real time. … That way if we see FLOOR with more money in each trade or more entries doing well, we can start to get what's live doing the same thing."

Sources: `/data/mal/hunt-1008/ops/reports/2026-11-13/open-sim-board-design.md` §1, §3, §5; `/data/mal/hunt-1008/ops/QP-RULING-1113-open-sims.md` Q4; the QP verdicts on PR #1357 (`ops/reports/2026-11-13/pr1357-qp.md`).

**1. The class.** An open simulation book is a paper replay of a named rule and configuration on the tip tape as it arrives, from a recorded start instant, with every parameter fixed at that instant. Its trades and results are shown to the owner as they happen and are not sealed. It is not a registration, not a DEC-031 arena book and not a gate read. No open-simulation trade counts toward the promotion gate or a §6 read, is pooled with real fills, or becomes part of a later read's data. A change of parameter, rule code or simulator version is a new book with a new start instant; the old book is stopped or left running, never edited. Days before the start instant are labelled in-sample.

**2. Honesty rules (QP Q4; each is kept because breaking it puts money behind a number that will not repeat).**
1. Fixed parameters per book; a tweak is a new book with a new start instant. Otherwise the record shows money the current settings never made.
2. Every number is labelled in-sample or forward. In-sample numbers overstate; mixed into a forward total they make a losing configuration look positive.
3. The board says how many book rows are watched, and the best one is treated as shrunk. With 10 zero-edge configurations the best still shows a profit by chance (DEC-031 Amendment 3 item 3).
4. Per-day totals and the total after dropping the top 3 trades are always shown. One lucky day or token can carry the whole sum (EXP-017).
5. Before a sim steers live, one book at the exact live configuration is compared with live fills trade by trade (item 3 below). FLOOR's probe exits filled −326 to −780 bps worse than modelled.
6. Honest costs at the simulated size: real fees per send, rent, priority, impact at that stake, and both fail models until live fills measure them. Leaving them out invents profit.
7. A sim that overlaps a registered read is declared in writing before its first output (§7). An undeclared peek voids the read.

**Not required for open sims** (they protect claims an open sim does not make): read dates, seals on the sim's own results, P0 floors, N accounting, and outcome-blind read tools. They stay unchanged for registered reads and the gate.

**3. When a sim may steer the live bot.** Only as one step inside this DEC's limits, with the owner's word where §2 or an amendment requires it, and only while the control book (the exact live configuration: design §1 book A) reconciles with live fills inside the bands below. On any band failure every book is marked "SIM OFF: not decision-grade", no live step is taken from the board, and the owner is told the same day; a simulator fix is a new simulator version and every book reruns from its own start under it.

Initial constants (QP may adjust them on the reconciliation tool's PR):
- **H1** live entries with no control-book entry = 0.
- **H2** `unexplained` sim-only entries = 0 per day.
- **H3** exit reason equal on every matched trade, except coded cases (host down at the take-profit, so the executor's 24 h clock sold).
- **S1** (rolling 20 matched, needs ≥ 10) median entry slip in [−1.5, +3.0] pp; at most 2 trades beyond ±8 pp.
- **S2** median (live − sim) net within ±3 pp of stake; |Σlive − Σsim| ≤ 2% of Σstake.
- **S3** live fees per trade ≤ model + 0.5 pp of stake; unreclaimed rent 0.
- **S4** live landing p50 ≤ 1.9 s and p90 ≤ 3.0 s.

Until the reconciliation exists, a board number may inform a proposal to the owner, but the board says "NOT COMPARABLE". **The Amendment 6 gate (20 closed trades at 0.10 before the 0.25 stake) is not changed by this amendment.**

**4. Books at start (v0, PR #1357, merged d1e752be).** From the daily job `scripts/research/floor_forward_ab.sh` (replay from 2026-11-02T01Z), forward-from 2026-11-12 (entries on or after 2026-11-12T00:00Z; earlier entries are in-sample, including those that close later):
- **D0**: `l1-floor-v2` as live, without the lane gap guard;
- **C0**: vault1.0 (`Q_STAR=1.0e9`), without the gap guard; picked from a variant search on 2026-11-03..11-11.

Each at 0.10, 0.25, 0.5 and 1.0 SOL, labelled as QP required on #1357: 0.10 "model; live fills exist, not reconciled (recon NOT COMPARABLE)"; 0.25 "model; live check pending"; 0.5 and 1.0 "model only, extrapolated; not for steering". Neither v0 book is the control book. A day is complete only when the tape runs at least 90 min past its end.

**Later books** (control A under the lane guard, and C, D1, E1, E2 from design §1; the follow-mode driver) start under this amendment when their tool PRs merge. Each is recorded by a MiScusi notebook entry with its manifest sha256 and start instant. No further amendment is needed per book.

**5. SEALS-BRIEF lines.** The brief is a host file the manager edits. Each line below is in force when its tool's PR merges and the manager adds the line naming the merged commit; (j)'s PR is #1357 (d1e752be).
- **(j)**, verbatim from QP's verdict on #1357:
  > (j) OPEN SIM BOARD v0 (PR #1357): `floor_forward_ab.py board` reads only the two pnl json files of one floor_forward_ab.sh work dir under /data2/mal/scratch/floor-forward-ab and writes board.json and board.md there; the manager may copy exactly those two files (no other file of the work dir) to ~/data/open-sim/ on research-0 for the Console. They carry no pool id, mint, signature, price or reserve. Paper only; an open simulation, not a gate read.
- **(k)**, draft:
  > (k) OPEN SIM FOLLOW DRIVER (PR #[n], commit [sha]): scripts/research/osb_follow.py reads ONLY closed hv7 hours that the arena tip receiver's verification log lists, hard-linked under /data2/mal/scratch/floor-open-sim (its keeper dir and per-book dirs); it writes only under /data2/mal/scratch/floor-open-sim and to ~/data/open-sim on research-0. Files under ~/data/open-sim carry no pool id, mint, signature, absolute price or reserve. Paper only; an open simulation, not a gate read.
- **(l)**, draft:
  > (l) OPEN SIM LIVE EXTRACT AND RECONCILIATION (PR #[n], commit [sha]): scripts/ops/osb_live_extract.py runs on mal-fast-0 only as a manager job with sudo -n; it reads the floor-lane-1 decision rows (entry, exit, entry_refused_gap, entry_gap_span_ignored, lane_restart_shadow(_failed), start), the bridge log rows (buy_sent, buy_landed, sell_sent, sell_landed, refusals with reason, gap and gap_hold), the lane ledger cash rows and the fills log fields spend_lamports, tokens_received and price_vs_quote_bps; it writes one 0600 extract keyed by order id and copies it to research-0 (0640); stdout is counts and sha256 only. scripts/ops/osb_recon.py on research-0 is the only reader of the extract. It may print, per order id, only: order id, sim trade id, entry and exit UTC, stake, class, reason code, entry slip pp, landing ms, live and sim exit reason, live and sim net SOL, fees in pp of stake. Pool, mint, signature, absolute price and reserves never leave the extract and the tool.

**6. Ledger.** One HOLDOUT_LEDGER changelog line (this PR) records the hours as open FLOOR information.

**Outside this amendment.** r4-b (DEC-031 Amendment 31, PR #1358).

**Unchanged.** §2 (every limit, the STOP files, keys), §3–§7, the Amendment 6 gate and its count, Amendments 1–8, and the promotion gate. Nothing here says FLOOR or any variant makes money; no book has cleared the promotion gate.
