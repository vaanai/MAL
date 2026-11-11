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
