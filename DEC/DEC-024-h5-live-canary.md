# DEC-024: H5 live measurement canary (0.02 SOL)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-08; the owner approved the canary in principle the same day (the owner's words are in section 1).** The limits below are the manager's terms for the owner's confirmation. It takes effect on merge, with quant-proof OK on its final head. **No live send** until every item in section 8 is done, and never before [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Part 1 is merged. |
| **Decider** | Vaan (owner) |
| **Date** | 2026-10-08 |
| **Builds on** | [DEC-019](DEC-019-execution-probe.md) (probe wallet, custody, executor), DEC-020, DEC-021 §7, [DEC-023](DEC-023-h5-family.md), EXP-024, EXP-022 §9 |
| **Amends** | The paper-only fence and the "no trading keys on hosts" rule (CLAUDE.md, CONSTITUTION), **only** for one executor profile (`h5`) on the one probe wallet DEC-019 created, on `mal-fast-0`, for this canary and for H5's live trial under the owner override in section 7. DEC-019 limited that exception to "the duration of the probe". This DEC extends it to the canary and to nothing else. |
| **Does not amend** | **The promotion gate for any other book.** (H5's live trial is covered only by the owner override recorded in section 7.) DEC-014, DEC-016, DEC-021 §6 and §9, EXP-022, EXP-024's rule, looks and seal. Canary trades are never a book and never count toward any gate, read or promotion. |

## 1. The owner's instruction (verbatim, 2026-10-08)

> "I've got a little over a quarter sol for you to mess around with to test. If that speeds things up, we can hopefully get to live trading with 1 sol in a couple days rather than waiting for simulation tests."

This is a **measurement canary**. It measures how the frozen H5 rule lands and exits on chain. It does not test whether H5 makes money, and its size could not tell.

## 2. What it is and is not

- **It is:** a live measurement of H5's execution: landing time from the trigger print, failure and expiry rates, the 15% guard's revert rate, and the precision of the timer exit at s0 + 330 s.
- **It is not:** gate evidence, a book, a paper pass, or a claim that H5 is positive. No report, note, Console entry or message may say a canary result shows a profit or an edge.
- **It does not change the gate on its own.** The promotion gate (the only exception route is the owner-override clause in section 7), the EXP-024 rule and its thresholds are unchanged. CLAUDE.md and the owner's 10-08 mandate say nothing goes live before a book clears the promotion gate on both fail models. This DEC does not waive that on its own. Section 7 records the owner's override for H5's live trial only.
- **It does not replace the read.** EXP-024's Look 1 and Look 2 stand.

## 3. When it starts

- **After EXP-024 Part 1 is merged.** The rule is then frozen by sha256 and the read tool has no overrides, so nothing the canary shows can change the read.
- **After every item in section 8.** The canary is built keyless first (a detector, an executor profile, a keyless shadow detector run of at least 24 h on live triggers with 0 build errors, and a md5 decision-equivalence replay against the frozen scorer on exploration or already-read September tape only, never on the fast-0 tip archive of 10-05..07).
- **Never before 2026-10-10T00:00Z** and never while the A3 monitor shows a halt. *(The date bar is lifted by Amendment 1, the owner's decision of 2026-10-09; the A3 halt bar stays.)*

## 4. Limits (code constants; config may only lower them)

| Limit | Value |
| --- | --- |
| Rule | Frozen H5-BOOSTFLOOR v1: the trigger on tape quote + V0 (the same quantity as the read), one trade per pool, exit timer at s0 + round(330 s / sps), full-balance sell plus ATA close |
| Venue | PumpSwap only, non-mayhem WSOL pools in the rule's universe |
| Stake | **0.02 SOL** per entry |
| Concurrent | at most **2** open positions |
| Attempts | at most **30** buy attempts per UTC day, counting guard reverts. Triggers that arrive when 2 positions are open or the day's 30 are used are logged as skipped |
| Daily stop | realized loss of **0.08 SOL** in a UTC day: no new buys until 00:00Z |
| Total stop | realized loss of **0.12 SOL** since the first send: no new buys until the owner restarts it |
| Priority | 55,000 lamports per send, on the buy and the sell (the rule's cost). DEC-021 §7 says a new priority setting needs a live calibration; this canary is it |
| Buy guard | on-chain `min_out` at the trigger print's post-trade price ÷ 1.15 (the lab's `DEFAULT_SLIPPAGE_CAP`) |
| Sell | `min_out` at 0.85 of the quote; after 2 reverts, or once s0 + 345 s has passed, resend at 0.65 with higher priority. Never `min_out` 0 |
| Balance guard | stake + 0.02 SOL (the probe's constant) |
| Duration | at most 14 days from the first send (code constant). Then STOP. An extension is the owner's decision |
| Files | `STOP` (no new buys; open positions still exit on the timer) and `LIVE_OK`. The executor sends **only while `LIVE_OK` exists**. Helm creates it as root; the executor cannot. Removing it stops new buys like `STOP`. `HALT` (freeze all) is the probe's existing file |
| Wallet | the DEC-019 probe wallet `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk` on `mal-fast-0`, held by Helm. The manager never sees the key (DEC-019 §5) |
| Funding | the owner's "a little over a quarter SOL" (at least 0.25 SOL). Nothing more |

**State at writing** (LIVE-PLAN §1.1, from LAB_STATE; Helm confirms before funding): the probe is stopped, its unit is inactive and disabled, `STOP` is in place, the watchdog timer is disabled, and the wallet held 0 lamports after Helm's 2026-10-07 withdraw.

**Wallet floor, hard worst case.** With 0.25 SOL funded: 0.25 − 0.12 (total stop) − 0.04 (two open positions go to zero) − about 0.005 (fees and rent on them) ≈ **0.085 SOL**. The realized-loss stops count closed trades only, so the open-exposure term is not optional.

**Trial-size effect.** At 0.02 SOL the fixed costs weigh five times what they do at 0.1 SOL. Two sends at 55,000 lamports are 110,000 lamports, 0.55% of the stake (0.11% at 0.1 SOL). The ATA rent of 1,513,840 lamports is refunded on close, but a stuck position strands it, which is 7.6% of the stake. The paper twin and the live−sim comparison must be priced at 0.02 SOL, not scaled from 0.1 SOL.

**What the stops do.** They are circuit breakers for a build or regime fault, not a P&L tool. The daily stop is 4 stakes and the total stop is 6 stakes. The LIVE-PLAN simulation (0.05 SOL, stops at 5 and 10 stakes, 13 days; flat leg; `h5-work/live-plan-mc/mc2.py`) gave P(total stop) of 0.47 for a zero-edge book and 0.89 at −3% per trade [simulated, exploration]. The canary's tighter total stop will fire at least as often on a zero-edge book [inferred]. A rerun at the canary's limits is in section 8. A stop firing is not evidence of anything about H5.

## 5. Live-halt rules

Any of these halts new buys at once (`STOP`). Open positions exit on the timer unless the manager places `HALT`. A halt is never followed by a retune.

1. **BOOST end early.** A post-step A3 run with a last slice below 335 s after the first print, or below 337 s on two runs.
2. **Late sells.** More than 5% of sells land after s0 + 335 s.
3. **Slow landing.** The canary's landing p50 (trigger print → landed) above 3.0 s, over the first 20 landed buys and then a rolling 20.
4. **Divergence.** After at least 100 fills, the live−twin per-trade CI90 upper bound below −1 pp (live worse than its paper twin by more than 1 pp). The twin is an H5 outcome. For pools in Look 1's window and in Look 2's added window it is observed in real time (section 6, EXP-024 Amendment 2), which for Look 2's window needs the shadow to run with the declared flag. A CAP-PICK pick is never traded and has no twin.
5. **A stuck position.** A position not closed by s0 + 600 s: halt new buys and alert. Retries back off from 30 s to 10 min; after 10 attempts it is abandoned and Helm runs a root sell-and-close.
6. **Structure.** Any A3 halt flag, `boost_enabled` false, a V-range non-mayhem WSOL pool with no BOOST vault, ms per slot outside [150, 450], or a changed pinned config or program data.

**Stops that end the canary.**
- **BOOST off or changed** (`toggle_boost`). This also ends CAP-PICK and H4.
- **EXP-024 Part 1 not merged by 2026-10-11T00:00Z** (refiled pin): the live build pauses.
- **A threat to EXP-022's 2026-10-16T01 preconditions.**
- **A seal breach.**
- **EXP-024 Look 1 is a FAIL:** new buys stop at the next 00:00Z after the Look 1 report, unless the owner extends the canary for execution measurement only.

## 6. Seal

**CAP-PICK seal.** From 2026-10-16T01 to the end of EXP-022's read, there is no H5 live or paper trade on, or joined to, any CAP-PICK pick, and no per-pool H5 P&L is produced before each CAP-PICK look. A breach is recorded as compromised.
- The executor skips any mint the EXP-022 gate picked. The picks come from the gate's online decisions. The executor reads only the `mint` field of the decision-time intents into an in-process set. It writes no CAP-PICK field into any H5 record, and no H5 record is joined to a pick. If the pick feed is missing or stale for more than 60 s, H5 buys halt (fail closed).

**Declared observation (EXP-024 section 3.1).** "By the owner's decision (10-08), the live canary's and the shadow detector's outcomes for pools inside Look 1's window are observed in real time. This is declared before the window opens and before any canary trade."
- **Scope.** Pools whose s0 is in `[2026-10-10T00, 2026-10-16T00)` (Look 1's window) and, by [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendment 2 (2026-10-09), pools whose s0 is in `[2026-10-16T00, 2026-11-06T00)` (Look 2's added window; this window only, the end excluded). The executor still writes its fills, exits and wallet deltas under `/var/lib/mal-live` (mal-live, 0700), and the shadow its outcome records. For pools in these two windows the owner, Helm, the manager, builders, the section 8 watchdog (its Discord alerts, which include the daily and total realized-loss stops and the wallet line) and the daily check may read them in real time. The one reader excluded is the EXP-024 read tool: its inputs remain the section 3 hours. For a pool with s0 at or after 2026-11-06T00 (outside both windows) they read only the allowlisted timing and count fields (decision → send ms, send → landed ms and slots, the in-slot index, the outcome class, the day's attempt count, as `tools/runner_timing_read.py` does) until Look 2 is read.
  - *Before Amendment 2* this bullet limited the manager and builders, for pools with s0 at or after 2026-10-16T00, to those fields until Look 2 was read. Amendment 2 lifts that for `[2026-10-16T00, 2026-11-06T00)` and for the canary's and the shadow's real-time outcomes only. It does not narrow who may watch Look 1's window.
- **Amendment 2 (Look 2's added window).** "By a manager decision derived from the owner's recorded decisions (2026-10-09), the live canary's and the shadow detector's outcomes for pools inside Look 2's added window are observed in real time. This is declared before Look 2's window opens and before any Look 2 outcome exists." The owner's recorded decisions are the scale ladder (T0 0.02, T1 0.10, T2 0.30 SOL, 25 to 50 trades per step, onward; MiScusi notebook n_xaHk-8t27C8qbw, "OWNER: scale ladder…", manager-verified 2026-10-09, with the owner's verbatim answer in it), the 10-08 real-income mandate (`docs/HANDOFF.md`, "Owner mandate and decisions"; it is the source of "through October") and the `OWNER_OVERRIDE_CONFIRMED` line in section 7. They need the live P&L to be watched. The owner may revoke it (EXP-024 Amendment 2, Provenance).
  - "Look 2's rule, data, analysis and pass bar are fixed by EXP-024 Part 1. Look 2 is always read and always reported as written. It is never skipped, delayed, re-scoped or re-thresholded because of anything the canary or shadow shows. Nothing the canary shows may change any EXP-024 parameter." "As written" includes EXP-024 section 8: Look 2 runs only under sections 8.1 and 11, so there is no Look 2 after a Look 1 PASS or a Look 1 futility stop, whatever the canary or shadow shows. A canary halt (section 5) stops the canary only. It does not stop, delay or change Look 2. The Look 2 report carries the concurrent-observation disclosure.
  - **Unchanged.** The EXP-024 read tool's own computation from forward-1002 and walk 2 stays sealed until the look, and EXP-024 section 3's bans on those reads stand.
  - **The CAP-PICK seal is unchanged and applies on top** (previous bullet group). Amendment 2 covers non-pick pools only: no H5 trade on a pick, no H5 record joined to a pick, no per-pool H5 P&L for a pick before each CAP-PICK look, and the oracle fails closed.
  - **Dependency.** H5 trades from 2026-10-16T01 only if the real CAP-PICK pick oracle (PR #509, `claude/cap-pick-oracle`) is merged and wired in. Until then the executor refuses every buy in the seal window and the shadow seals every pool.
  - **Shadow flag.** `tools/h5_shadow.py` withholds outcome-bearing records for pools with s0 at or after 2026-10-16T00Z (trigger records are still written) unless it was started with `--h5-look2-observed EXP-024-Am2` (wrapper variable `H5_LOOK2_OBSERVED`). The flag lifts the withholding for `[2026-10-16T00, 2026-11-06T00)` only: a pool with s0 at or after 2026-11-06T00 stays withheld whatever the flag says. The shadow job is started with the flag only after Amendment 2 is merged, and a MiScusi resume must set the variable again. The CAP-PICK seal ignores the flag. The daily check (`scripts/mal-fast/h5-daily-check.py`, which the watchdog also runs) alerts `h5_look2_not_observed` when, from 2026-10-16T00Z to 2026-11-06T00Z with `LIVE_OK` set, the newest shadow start record does not show `h5_look2.observed` true, because without it the canary has no paper twin and section 5 rule 4 cannot fire.
- "Look 1's rule, data, analysis and pass bar are fixed by [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Part 1. Look 1 is always read and always reported as written. It is never skipped, delayed, re-scoped or re-thresholded because of anything the canary or shadow shows. Nothing the canary shows may change any EXP-024 parameter." A canary halt (section 5) stops the canary only. It does not stop, delay or change Look 1.
- "The canary's scale-up decision is a separate business decision, not EXP-024 evidence."
- "Disclosure: concurrent observation lets canary results influence later choices (for example an owner scale-up). That never changes the formal read, and the read reports it."
- **Not covered.** The EXP-024 read tool's computation of H5 outcomes from the tape stays sealed until the look. **CAP-PICK is not touched:** the CAP-PICK seal above holds exactly as written.
- **Provenance.** The Look 1 declaration is a decision relayed by the manager on 2026-10-08. The Amendment 2 extension is a manager decision of 2026-10-09 derived from the owner's recorded decisions listed above. The sentences in quotation marks are the manager's wording. The owner's own words on them are not quoted here.
- **Wallet exposure.** The wallet is public on chain, so anyone can compute its P&L. That is consistent with the declared observation for pools in Look 1's window and Look 2's added window. A trade on a pool with s0 outside both windows would be a seal exposure for that pool. None is planned (the duration cap is 14 days from the first send, section 4), and an owner extension of the canary past 2026-11-06T00 would need its own decision.

## 7. Scale-up to 1 SOL

The scale-up needs **the owner's OK (the override line below) and all** of these:
- live execution is consistent with the sim: the canary's landing p50 is at most 3.0 s, measured over at least 20 landed buys, and the live−twin per-trade CI90 upper bound is not below −1 pp, after at least 100 fills;
- the shadow run (the paper twin of the canary's decisions, priced by the frozen sim at 0.02 SOL) is not negative, meaning its mean per trade is not below zero;
- EXP-024 Look 1, if it has been read, is not negative: its deciding-cell flat and pressure means are not below zero. **Plainly: Look 1 cannot be read before about 10-16, so this condition cannot bind before then, and it does not apply to a scale-up made in 10-12 to 10-14.** It applies only to a scale-up made after Look 1 has been read;
- the canary's own realized result, after fees, is not negative: its mean per closed trade is not below zero;
- no live-halt rule (section 5) and no stop that ends the canary has fired and is unresolved. These rules, and the daily and total stops, stay in force at every size;
- the owner states the 1 SOL stake, open-position cap and stops in writing (a dated line in this file).

**These are necessary and not sufficient.** The twin is observed in real time for pools in Look 1's window and Look 2's added window (section 6, EXP-024 Amendment 2), and Look 1 and Look 2, if they are read, are read as written. Neither clears the promotion gate. CLAUDE.md and the 10-08 mandate say that nothing goes live before a book clears the gate on both fail models. **This DEC does not waive that on its own.**

**A scale-up to 1 SOL before a gate pass is an owner override of the CLAUDE.md promotion rule for H5's live trial. It requires the owner's explicit written confirmation, recorded in this file by a dated line before any scale-up.** The override applies to **H5's live trial only**. The promotion gate is unchanged for every other book, and EXP-024's formal read stays binding as written. The scale-up decision is a separate business decision and is not EXP-024 evidence. It is labeled an unpromoted trial in every report, and no report of it says the book passed or is positive. The line below holds the owner's confirmation. Nothing else fills it, and until a line is there, there is no scale-up.

```
OWNER_OVERRIDE_CONFIRMED: 2026-10-08 (owner, in session, asked by manager9). Question: "Can H5 scale from the 0.25 SOL test to 1 SOL before the formal October test (about 10-16/17) has passed? That would mean trading real money without the lab's profit gate, for this one strategy." Answer: "Yes, full 1 SOL" (manager's paraphrase of the option the owner chose: scale to 1 SOL as soon as live execution matches the simulation and the live and shadow results aren't negative, about 10-12 to 10-14). Verbatim question, options and answer are in MiScusi notebook n_v3FFb1ibpGKmRQ (manager-verified 10-08).
```

**Provenance of that line.** 2026-10-08T20:16Z (`date -u`): the builder recorded it from the coordinator's relay of the manager's record. The owner's own message is not in the builder's context, and the builder did not check it. The manager states that the entry was verified on 2026-10-08; the builder did not open it. Quant-proof can check it there. The line records a decision. It starts no trade. The owner's funding of the wallet, Helm's `LIVE_OK` and the rules in section 5 stay separate acts and rules. The owner's answer names the target (1 SOL) and the timing, and not the trial's stake or stops; those are still the owner's to state in writing (next paragraph).

Size above the canary is governed by DEC-018, DEC-019 and DEC-020, and by the owner. A scale-up to 1 SOL would use LIVE-PLAN's L1 terms as a starting proposal (0.05 SOL, 3 open, stops 0.25 daily and 0.5 total), which the owner must state in writing.

## 8. What must happen before the first live send

**Owner:**
- Confirm this DEC and its limits (the limits are the manager's terms).
- Fund the probe wallet with the "little over a quarter SOL" and nothing more.
- Keep the withdraw address with Helm (DEC-019 §6 item 6).
- Decide the open questions below.

**Helm:**
- Confirm the wallet balance and the state at writing.
- Do a pinned root-owned install of the executor with the `h5` profile (manifest and sha256 check, as for the probe), with the live drop-in.
- Set `LIVE_OK` (root-owned) only after the install is checked, and remove it on any halt the owner or manager asks for.
- Keep the auditd watch on the key.
- Re-target the durable Discord watchdog (`mal-probe-watch.timer`, now disabled) to H5: stuck, structure halt, stop fired, restarts, wallet balance.
- Run the sell-and-close root tool if a position is ever abandoned.

**Manager and builders:**
- Detector, `h5` executor profile, limits as code constants with tests (stops fire, config only lowers them, a restart cannot reset them), the exit timer, the CAP-PICK exclusion, and the BOOST tripwire.
- A `reviewer` pass and a **security review** (the executor holds a key), and quant-proof on the md5 equivalence replay.
- A keyless shadow of at least 24 h with 0 build errors.
- The stop-probability simulation rerun at the canary's limits (section 4).
- No Jito, no Sender, no LaserStream, no paid RPC: **$0 extra** (owner plan 10-08). Public RPC may serve as an exit-only fallback sender.

## Amendment 1 (2026-10-09, about 07:15Z, the owner's decision, before any canary send): early start

**The owner's decision.** The manager asked the owner in session (AskUserQuestion, 2026-10-09 about 07:05Z):
- Question: "DEC-024 (written 10-08) bars live H5 trades before 10-10 00:00 UTC and requires a 24 h shadow soak first. Start now anyway?"
- Answer: "Start now (Recommended)".
- The option text: "I amend DEC-024 to allow the early start and Helm goes live within the hour at 0.02 SOL/trade. Today's pools aren't counted in the formal reads. The soak is replaced by the reviews, ~9.5 h of live shadow runs, 5 clean simulated round trips and Helm's dry run. Worst case is the ~0.10 SOL loss stop."
- The record is the MiScusi notebook entry "OWNER: H5 canary may start before 10-10T00Z (DEC-024 Amendment 1)".

**What changes.**

1. **Section 3, "Never before 2026-10-10T00:00Z", is lifted.** The canary may send from the moment Helm creates `LIVE_OK` after this amendment is merged.
   - Why this cannot change a read:
     - Every pool with s0 before 2026-10-10T00 is outside every EXP-024 look's counted window, which starts at 2026-10-10T00 (EXP-024 section 0).
     - EXP-024 Part 1, Amendment 1 and Amendment 2 were all merged before any canary send.
     - No rule, window, alpha, gate item or read-tool input changes.
   - Hour 2026-10-09T23 is read by Look 1 only for `complete` events (universe membership), and no outcome from it is used (EXP-024 section 3).
2. **Observation of the early pools is declared, in EXP-024 Amendment 3.** For pools whose s0 falls before 2026-10-10T00, the canary's and the shadow's outcomes are observed in real time, by the same readers as section 6. The shadow has printed such outcomes since about 2026-10-08T21:30Z, after EXP-024 merged (2026-10-08T20:25Z); that part is disclosed, not declared in advance.
   - These pools are in no look's counted window.
   - Hour 2026-10-09T23 is a Look 1 read hour, so EXP-024 section 3's seal runs on it, and its exception list is EXP-024's own. EXP-024 Amendment 3 enters this observation there and is also the carve-out for DEC-016 Amendment 7's H5-seal sentence.
   - The EXP-024 read tool still never takes the canary's ledger or the shadow's output as input.
3. **Section 3 and section 8's "keyless shadow of at least 24 h with 0 build errors" is replaced by this evidence:**
   - **Shadow runs.** Keyless runs on live triggers from 2026-10-08 about 21:30Z, across reviewed heads, with no crash: MiScusi jobs #399 (ff31026), #424 (fe7eb43), #428 (3dbe1de), #438 (91b7a34) and #440 (fc0816a). Each was stopped by the manager for an upgrade. The go-live shadow is #447 on main 88eef14, started 2026-10-09T07:16:53Z (MiScusi start event) with `H5_LOOK2_OBSERVED=EXP-024-Am2`.
   - **Replay.** Replay against the frozen rule on 2026-09-20 is 72/72, with max trigger-time difference 0.0 s. It was re-run by quant-proof on each head.
   - **md5.** The md5 decision-equivalence replay of sections 3 and 8 is not replaced: md5 `75cb0b0c585bc2479137cae31330e73e` on both lists (72 decisions each), from MiScusi job #446 on main 88eef14 (`/data/mal/hunt-1008/h5-work/md5proof-88eef14/md5proof.json`). The shadow's pv trigger list and the frozen rule's list for pools with s0 on 2026-09-20 hash equal. The go-live shadow is restarted on 88eef14, the head this md5 covers (MiScusi job #447), before Helm creates `LIVE_OK`; #440 (fc0816a) does not feed the live executor.
   - **Dry runs.**
     - Keyless executor dry runs on the real feed (jobs #404 to #435): 5 complete simulated round trips with 0 simulate errors, sells sent at s0 + 330.4 to 330.8 s.
     - Helm's pinned-install dry run: 11 minutes, 0 restarts, no startup refusal.
   - **Reviews.** Review rounds on the executor (#484), the shadow (#477, #510) and the unit (#499). Every MUST-FIX is fixed. The files are in `/data/mal/hunt-1008/h5-work/REVIEW-*.md`.
4. **Section 8's stop-probability rerun at the canary's limits is done.** It is report-only, and section 4 still applies: a stop firing is not evidence about H5.
   - **Setup:**
     - stake 0.02 SOL, daily stop 0.08, total stop 0.1045 SOL (35% of the funded 0.298689 SOL);
     - at most 30 trades per day;
     - the September confirmation day counts × 0.6, the share of triggers that pass the executor's static checks live;
     - a fixed-cost term of 0.44% per trade: two 55,000-lamport sends are 0.55% of a 0.02 SOL stake, and the 0.1 SOL `flat` P&L already carries 0.11% (section 4, trial-size effect);
     - trades drawn independently, with replacement, from the 1,124 flat-leg trades on 21 September days (2026-09-03 to 09-25, `live-plan-mc/conf_p01.parquet`); "latest blocks" is days from 2026-09-18;
     - not modeled: the pressure leg, the 2-open-position cap and the code's open-exposure check, live fills worse than paper, stuck positions and stranded rent;
     - 6 days; the live config runs to `end_ms` 2026-10-16T00:30Z, about 6.7 days, and at 7 days a zero-edge book's P(total stop) is 0.673;
     - the +13.21% row is the selected rule's September mean (winner's curse). EXP-024 section 15 expects a FAIL (P(Look 1 pass) ≈ 0.0265);
     - 10,000 simulations, seed 1; reproduced by quant-proof (`scratchpad/qp513-mc/run3.py`).

| Assumed edge per trade (gross, 0.1 SOL flat leg) | Net of the 0.02 SOL fixed-cost term | P(total stop) | Total P&L p5 / p50 / p95 (SOL) | P(T0 P&L > 0) |
| --- | --- | --- | --- | --- |
| September pooled, +13.21% | +12.77% | 0.011 | +0.031 / +0.337 / +0.804 | 0.971 |
| Latest blocks, +6.15% | +5.71% | 0.030 | −0.044 / +0.152 / +0.406 | 0.901 |
| Half the latest, +3.07% | +2.63% | 0.150 | −0.109 / +0.058 / +0.315 | 0.679 |
| Zero | −0.44% | 0.643 | −0.113 / −0.106 / +0.376 | 0.281 |
| −3% | −3.44% | 0.763 | −0.114 / −0.107 / +0.267 | 0.176 |

   The payoff is right-skewed, so a zero-edge book hits the total stop most of the time (0.643). Section 4 holds: neither a stop nor a positive T0 result is evidence about H5. A zero-edge book ends T0 above 0 in 28% of runs and a −3% book in 18%, so section 7's "not negative" check often passes a losing book.

**Unchanged:**
- the limits in section 4;
- the tier ladder;
- the halts in section 5;
- the seals in section 6 (Look 1, the Look 2 declared observation of EXP-024 Amendment 2, and CAP-PICK from 2026-10-16T01 with the oracle failing closed);
- the scale-up conditions in section 7;
- every EXP-024 and EXP-022 rule.

EXP-024 Amendment 2's sentence "DEC-024 section 3 bars any canary send before 2026-10-10T00:00Z" was true when that amendment merged (2026-10-09T07:03Z, before this one). Its conclusion, that no Look 2-window outcome exists before 2026-10-16T00, still holds.

## Amendment 2 (2026-10-09, the owner's decision; before any canary send, before any counted hour and before the official P2 run of the A3 monitor): synthetic-migration pools get no buy

**The owner's decision.** The owner approved excluding synthetic pools from H5. The record is the MiScusi notebook entry `n_xtknDqL-ychBNg` (2026-10-09), as the manager relayed it; the author of this amendment did not open the entry. The read-side text is [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Amendment 4 (a population restriction, not a retune). This amendment is its live-side companion. Line numbers below are those of this file on main at `ecdc7af`.

**Disclosure.** The program-upgrade review found synthetic migrations (a PostCompleteBuyEvent in the curve-completing transaction) on 0 of 61 graduations before the 2026-10-08T16:20Z redeploy and 12 of 61 after. The A3 monitor's `synthetic_share_high` read **6/19 = 0.316** at the daily run of 2026-10-09T07:11:06Z (job #445; halt line 0.35) and **7/19 = 0.368, a halt**, at one dry check at 07:38:11Z. The dry check used the unmerged pins of PR #517 and a scratch `--out`. It is not the P2 run, and it is disclosed in every amendment that touches the synthetic share. The rule's September evidence had 0 synthetic pools.

**What changes**
1. **One classifier, at decision time.** The executor and the shadow apply the same classifier as the read: `post_complete_buy_seen` (`tools/pump_structure_monitor.py:482-490`), on the PostCompleteBuyEvent discriminator alone, on the mint's CompleteEvent transaction and on the pool's migrate transaction when the two differ; the pool is synthetic if the event is seen in either (EXP-024 Amendment 4, item B1).
   - **A synthetic pool gets no buy.** A pool that cannot be classified at decision time (the completing or the migrate transaction cannot be found or read) gets no buy either. It fails closed, as the pick feed does in section 6.
   - The shadow stops signalling synthetic and unclassifiable pools. Skips are logged with their reason and counted. Counts by class are structure fields, not outcomes (EXP-024 Amendment 4, item D1 bars joining class to any outcome before Look 2 is read).
2. **The build's proof.** The build that carries this change carries:
   - an **md5 decision-equivalence replay on 2026-09-20**, the day Amendment 1 item 3 used. The replay is decision-equivalent because September has 0 synthetic pools, so the classifier must remove nothing: the md5 of the trigger list must equal the frozen scorer's, as in Amendment 1 item 3;
   - a **structure-only fixture** of post-upgrade synthetic completing transactions (recorded from public RPC, with no outcome or P&L), on which the classifier says synthetic, and of non-synthetic ones, on which it says not;
   - a `reviewer` pass and quant-proof on the final head, as for the rest of section 8.
3. **Section 5.6 (Structure), as amended.**
   - **Until Helm installs and verifies that build, `synthetic_share_high` stays a live halt.** Section 5.6 and the A3 halt bar of section 3 read as written, so the canary does not start while the monitor shows it.
   - **After that, `synthetic_share_high` is recorded and reported only.** It is no longer a live halt. The install is a pinned, root-owned install with the manifest and sha256 check of section 8. The manager records, in a dated line under this amendment, the instant Helm reports the install verified. The change takes effect at that instant and not before.
   - **The other five A3 halt flags stay live halts:** `pins_changed`, `boost_disabled`, `boost_share_low`, `boost_last_slice_early` and `boost_budget_or_slices_changed`. So do the other items of section 5.6.
4. **`program_changed` is a live halt for the canary.** Section 5.6 halts on "a changed pinned config or program data". The monitor's `program_changed` (programdata sha256 against the pins that PR #517 adds) is a WARN in the monitor and not one of its halt flags. For the canary it is a live halt all the same. This does not change EXP-024: its section 11 lists five flags, `program_changed` is not one, and a canary halt never stops, delays or changes Look 1 or Look 2 (section 6).
5. **Section 7.** The scale-up condition "no live-halt rule (section 5) … has fired and is unresolved" reads with the amended section 5.6. Item 3 above removes `synthetic_share_high` from it only after the install.

**Clarification 1 (2026-10-09, before any canary send).**
- **Who applies the classifier.** The shadow detector applies it at decision time. The executor applies the shadow's class: it acts on a trigger only if the record's `synthetic` field is exactly `false`, and refuses any other value, including an absent field (`synthetic_unconfirmed`), so it fails closed. It also refuses a trigger on a pool or mint the shadow has already marked `excluded`.
  - The executor does not re-fetch the transactions itself. Doing so would put at least three RPC round trips on the buy path (PR #519, item 3).
  - Every decision row records `synthetic` and `synthetic_src`, so any traded pool can be re-classified afterwards. From the first live day, a daily audit re-classifies every pool the executor sent a buy on, using EXP-024 Am.4 B4. It prints only the count of pools where the shadow's class disagrees with B4's, with no fill, size, exit or P&L field. A disagreement on any pool is a live halt under §5.6. A pool the shadow called plain on which B4 sees the PostCompleteBuyEvent in any readable located transaction counts as a disagreement, even if the other transaction is unreadable (added 2026-10-09 in PR #523, quant-proof OK on its final head). A pool the audit cannot classify is reported at once (alert), re-audited the next day, and counted as a disagreement if it is still unclassified then. The audit (never the formal read) may locate the migrate transaction on the curve PDA (newest successful transaction carrying both this pool's CreatePoolEvent and this mint's CompletePumpAmmMigrationEvent) when the pool-address search fails. The formal read's B4 is unchanged (added 2026-10-09 in PR #523, quant-proof OK on its final head).
  - Item 1's "the executor and the shadow apply the same classifier" means this division of work.
- **How the shadow locates the transactions live.** It uses the procedure of EXP-024 Amendment 4, Clarification 1 (B4), adapted to decision time:
  - The shadow's PumpSwap-only subscription does not deliver the completing transaction in most synthetic cases. The structure measurement of 2026-10-09 found it delivered in 10 of 56.
  - So the lookup starts at the pool's CreatePool notice. It calls `getSignaturesForAddress` on the curve PDA with a limit of at least 100, skipping failed transactions, then `getTransaction`, at `confirmed` commitment where the API allows. It tests event blobs extracted by the monitor's `tx_event_blobs` (`tools/pump_structure_monitor.py:411`), which reads both `Program data:` logs and emit_cpi inner instructions, and it applies the same check that each located transaction carries its defining event (EXP-024 Am.4 B4). It retries with backoff until the pool is classified or its trigger arrives.
  - A pool not classified by its trigger time gets no buy (`excluded`, reason `unclassified`).
  - The live lookup is not evidence and does not bind the read.

**Install-verified record (item 3), dated 2026-10-09.**

```
H5_SYNTHETIC_BUILD_INSTALL_VERIFIED: 2026-10-09T16:52Z.
```

- **Helm's report.** Helm reported part 1 done to the owner: the pinned reinstall at `af02e90561e23b13a3a2fa89c63faad0da899d33`, checked against the manifest (sha256 `b8e7981d…758a`, PR #519 comment 6078235561), plus the keyless dry-run start check. The owner relayed this to the manager at about 16:52Z. Helm's own report time was not relayed, so this line uses the relay time, which is the earliest instant the manager knew.
- **Manager's check.** At 16:53Z the manager ran job #467 on mal-fast-0:
  - the installed `tools/h5_executor.py` sha256 is `e44d1b4c5ffb25c0705509b1e175015acee6c019c2a73e27053479e591f91152`, which matches the manifest;
  - the unit is inactive and disabled;
  - `/etc/mal-h5/` holds neither TIER nor LIVE_OK.
- **Effect.** From this instant, `synthetic_share_high` is recorded and reported only. It is no longer a live halt (item 3).
- **Go-live pair.** The go-live shadow is MiScusi job #454 on main `af02e90`. Its shadow blob is `ea061266`, and its md5 decision-equivalence on 09-20 is `75cb0b0c585bc2479137cae31330e73e`, equal, proved at 4dd43d2 with the same blob. The keyless dry run is job #455 on the same head.
- **Still required before the first send.** The official A3 run (EXP-024 Am.4 F, job #449, 19:23Z) must show none of the five halt flags and no `program_changed` (items 3–4). Step 11 follows only after that.

**Not re-run.** The stop-probability table of Amendment 1 item 4 used September's confirmation day counts, which have no synthetic pools. Excluding them lowers the number of trades by the synthetic share (about 0.20 to 0.37 on the readings above [inferred]). The table is not re-run here. Section 4 holds: a stop firing is not evidence about H5.

**Unchanged:**
- the limits in section 4, and the other halts in section 5 apart from item 3 above;
- the seals in section 6, and the scale-up conditions in section 7 apart from item 5;
- the owner override line;
- every EXP-024 and EXP-022 rule. EXP-022 is not amended here; its synthetic handling needs its own amendment (EXP-022 Amendment 5, item 3).

**Takes effect** on merge, with quant-proof's OK on its final head, before the official P2 run and before 2026-10-10T00:00Z. It starts no trade. `LIVE_OK` and the go to Helm stay separate acts.

## Amendment 3 (2026-10-09, about 16:00Z, the owner's decision, before any canary send): a short T0 trial, then T1 with a 1 SOL wallet

```
OWNER_LADDER_CONFIRMED: 2026-10-09 (owner, in session, asked by manager9). Owner, verbatim: "When we go live, I presume 0.02 per trade is going to get washed out by fees, let's use it to do a quick trial run, and then depending on how we feel about the strategy, let's bump it up to 0.05 or 0.1 right away." Answers to the manager's three questions (AskUserQuestion, the recommended option each time): trial length "~20 trades"; next size "0.10 SOL", whose option text the owner chose read verbatim "This is tier T1, already built into the bot: Helm changes one file and no reinstall is needed. Limits: up to 3 open trades, 40 a day, daily stop 0.40 SOL, total stop 0.60 SOL or 35% of the wallet, whichever is lower."; wallet "Top up to ~1 SOL", whose option text read "Send about 0.7 SOL when the trial checks out. At 0.10/trade the total stop becomes 0.35 SOL, enough to ride out normal swings. This is within the 1 SOL you approved on 10-08." MiScusi notebook n_VzbN0Cri0QAVPA.
```

The manager corrected one premise before asking: at 0.02 SOL, H5's fixed costs are about 0.55% of the stake (section 4, "Trial-size effect"), so the trial is not "washed out by fees". It is only small in SOL.

**What this sets**
1. **T0 is a short trial.** T0 (0.02 SOL) runs until **20 landed buys**. This replaces the 25–50 trade length of the 10-09 ladder for T0 only. T1 → T2 stays at 25–50 trades per step.
2. **The step to T1 requires all of these at that point:**
   - the landing p50 is at most 3.0 s over those 20 landed buys (section 5, item 3);
   - no live-halt rule and no stop that ends the canary has fired and is unresolved;
   - the canary's realized mean per closed trade, after fees, is not below zero;
   - the shadow twin's mean per trade over the same trades is not below zero;
   - if EXP-024 Look 1 has been read, its deciding-cell flat and pressure means are not below zero (section 7).

   Section 7's live−twin CI90 condition needs at least 100 fills. **It is waived for the T0 → T1 step by the owner's decision above** (a 20-trade trial cannot meet it). It still binds as the section 5 item 4 live halt from 100 fills on, at any tier.

   **At 20 trades both mean checks are weak.** Amendment 1's simulation had a zero-edge book end the full T0 run above zero in 28% of runs (section 8 table, line 191); over 20 trades the checks are weaker still. Passing them is not evidence about H5.

   If either mean is negative at 20 landed buys, there is no step. The manager reports, and only a new dated line from the owner can step anyway.
3. **Wallet.** Before T1 the owner tops the wallet up to about 1 SOL. That supersedes section 4's "Funding … Nothing more".
   - The manager updates `/data/mal/hunt-1008/h5-work/FUNDED_SOL`.
   - Helm updates `H5_WATCH_FUNDED_SOL` in `/etc/mal-h5-watch/watch.env`.
   - Only then does Helm write `T1` to `/etc/mal-h5/TIER`.
4. **T1 limits are the executor's code constants** (`tools/h5_executor.py`, `TIERS`):
   - 0.10 SOL stake, 3 open, 40 attempts per UTC day;
   - daily stop 0.40 SOL;
   - total stop min(0.60 SOL, 35% of the wallet when the tier started), about 0.35 SOL with a 1 SOL wallet;
   - wallet floor 0.05 SOL.

   The owner confirmed these values by choosing the option whose text is quoted in the line above. While T1 is active, this item supersedes section 4's T0 rows for stake, open positions, attempts, and the daily and total stops.

   **Worst case at T1 with a 1 SOL wallet.** Realized losses stop at about 0.35 SOL (the total stop). Up to 3 × 0.10 SOL can be open when it fires, and fees and rent add a little, so up to about **0.66 SOL** can be lost, leaving a floor of about **0.34 SOL**. The 0.40 SOL daily stop is above the 0.35 SOL total stop, so at this wallet size it never binds before the total stop.
5. **What it is.** This is section 7's scale-up toward 1 SOL under `OWNER_OVERRIDE_CONFIRMED`. It is an unpromoted trial in every report. It changes nothing in EXP-024 and nothing in section 5's halts.


**Amendment 3, addendum (2026-10-09, about 17:10Z, the owner's decision, before any canary send): the T1 top-up is +0.5 SOL, not +0.7.**

```
OWNER_CAPITAL_CONFIRMED: 2026-10-09 (owner, in session, asked by manager9). Owner, verbatim: "I'm depositing 1 sol in my phantom wallet ontop of the 0.29 that was just sent to helms wallet, so that's all we have across the tests." Answer to the manager's split question (AskUserQuestion): "H5 +0.5, C1-NF 0.5" (the recommended option). MiScusi notebook n_MAnWCLTcb6rPJA.
```

- **What changes in Amendment 3.**
  - Item 3's "about 1 SOL" becomes about **0.79 SOL**: 0.298688847 SOL already funded, plus 0.5 SOL at the T1 step.
  - Item 4's "about 0.35 SOL with a 1 SOL wallet" becomes min(0.60, 35% × about 0.79) ≈ **0.28 SOL**.
- **Worst case at T1 with about 0.79 SOL.**
  - Realized losses stop at about 0.28 SOL.
  - Up to 3 × 0.10 SOL can be open when the stop fires, and fees and rent add a little.
  - So up to about **0.59 SOL** can be lost, leaving a floor of about **0.20 SOL**. That is above the 0.05 SOL wallet floor and the balance guard.
  - The 0.40 SOL daily stop is above the total stop, so it never binds first.
- **Capital limit.** The owner's total capital across all tests is about 1.29 SOL. C1-NF's separate 0.5 SOL wallet is governed by DEC-026, not here. No further capital is planned. No code or DEC rule ties T2 to a wallet size: the balance guard alone would allow T2 with about 1 SOL. About 2 SOL is the manager's sizing line. At 2 SOL the T2 total stop is 0.70 SOL, and up to about 1.61 SOL can be lost. T2 needs a new owner line in any case (Open item 1), and with no further capital it can only come from profits.
- **Unchanged.** Every other item of Amendment 3 stands.


## Go-live record (2026-10-09)

```
H5_CANARY_LIVE: 2026-10-09T19:29:53Z at T0 (0.02 SOL), install af02e90561e23b13a3a2fa89c63faad0da899d33.
```

- **Official A3 run.** EXP-024 Am.4 F, job #449: 2026-10-09T19:23:02Z on main 82cd674, monitor blob 1ca0a88c, pins blob 7486f57f.
  - None of the five H5 halt flags fired, so **EXP-024 P2 is met**.
  - `synthetic_share_high` read 6/17 = 0.353. It is recorded only (EXP-024 Am.4 C1; this file's Amendment 2 item 3, install verified at 16:52Z).
  - `program_changed` was clear.
  - `ms_per_slot_moved` gave a WARN, 267.26 → 217.39 ms/slot. That is the slot step and lies inside section 5.6's [150, 450] ms band.
- **Step 11, done by Helm and relayed by the owner:**
  - `H5_WATCH_FUNDED_SOL=0.298688847`;
  - wallet-wide STOP removed at 19:29:31Z;
  - TIER `T0`; LIVE_OK created;
  - unit started at 19:29:53Z, active and enabled, running as `mal-live`;
  - the only credential is `LoadCredential=probe-wallet`, and `check-h5-unit.py` passed;
  - first watchdog pass: `alerts=0`.
- **Manager check (job #469, about 19:31Z):** ALERTS=0. Unit files equal the pinned copies, the wallet balances to the funded amount, and the shadow feed is fresh.
- **Total stop at T0.** The executor logs the T0 table constant, `total_loss_lamports=120000000` (0.12 SOL). The binding stop is min(0.12, 35% of the wallet at tier start), about **0.1045 SOL** (`tools/h5_executor.py:1034-1038`).
- **Go-live pair.** The shadow is MiScusi job #454. The keyless dry run, job #455, keeps running for comparison.


## Halt record: section 5.1, 2026-10-10

DEC-024 §5.1 FIRED at 2026-10-10T07:11:07Z.

- **The run.** The post-step A3 run (job #478, epoch 1053, 218.18 ms/slot) read a median BOOST last slice of 333 s after the migrate tx (n=9, min 329, max 353).
- **Why it fires.** s0 is at or after the migrate tx, so the median after the first print is at most 333 s. That is below 335 s.
- **Two-run clause.** This run is also the first post-step run below 337 s.
- **STOP.** Placed at 07:15:10Z (job #479; open 0, pending 0).
- **Resumed.** STOP removed at 2026-10-11T00:15:06.586Z under Amendment 4 item 4 and Amendment 5 item 1 (see the resume record under Amendment 4 item 4). This halt stays recorded as the first strike.
- **Clearing.** DEC-024 has no clearing rule for a §5 halt. STOP stays until the owner records a dated amendment that:
  - names the §5.1 measure from that instant forward;
  - does not reclassify this halt;
  - states the resume condition.
- **Disclosure.** That amendment is written after the halt, while the executor's own ≥30-pool day medians were known:
  - 10-09: 340.306 s over 117 pools;
  - 10-10 at 07:15Z: 337.556 s over 200 pools, not latched.

Source: the quant-proof ruling of 2026-10-10, asked after the fire.

The ruling's other points, recorded here:
- **The executor's measure does not replace this clause.** The executor's in-code §5.1 measure (UTC-day median after s0, at least 30 pools, "two UTC days") is an extra live check. Adopting it in place of the A3-run clause after this fire would be a retune (section 5).
- **The DEC text and the deployed code disagreed at go-live.** They differ on what is measured, on the 30-pool minimum, and on "two runs" vs "two UTC days". The monitor's own `boost_last_slice_early` flag halts below 315 s after migrate. Nobody recorded which reading governed. The manager's pre-fire default was the A3-run reading.
- **No A3 run clears the halt on its own.** An after-migrate reading cannot prove that the after-first-print median is at or above a line.
- **Until an amendment exists:**
  - the STOP holds to the end of the 14-day duration;
  - T0 → T1 stays blocked (Amendment 3 item 2; section 7).
- **The canary at STOP** is not evidence (sections 2 and 4):
  - 3 trades at 0.02 SOL;
  - realized +0.005199 SOL;
  - 3 sells landed, 0 late.
- **Formal tests.**
  - EXP-024 Look 1: no A3 halt flag fired (`boost_last_slice_early` halts below 315 s), so Look 1 is not halted. This run's 333 s is one of the daily medians in EXP-024 §11's 330 s window check.
  - EXP-022: all six flags were clear.
- **Other canary halts.** `program_changed` (a canary live halt under Amendment 2 item 4) was clear: 3 programdata hashes unchanged.

## Halt record: section 5.6, 2026-10-11 (synthetic-class audit), and the owner's resume

- **Halt.** The daily synthetic-class audit (Amendment 2, Clarification 1), job #745, ran at main `87d956b` on DAY 2026-10-10 with the re-audit of 2026-10-09. It printed `{"date":"2026-10-10","n_pools":3,"n_disagree":2,"n_unclassified_now":0,"halt":true}` and exited 3.
  - Both "disagreements" were the two 2026-10-09 pools.
  - Their stderr reasons: `reaudit:fetch_failed:migrate:RpcError;…` and `reaudit:fetch_failed:signatures:pool:RpcError;…`. Both pools were unclassified on the second look because the public RPC returned errors.
  - The day before, the 10-10 run had classified both and found `n_disagree` 0.
- **STOP.** The manager placed `/var/lib/mal-live/h5/STOP` at once, at **2026-10-11T12:50:06.694Z** (MiScusi job #746: touch rc 0, test present; `--status` stop_file=True, open 0, pending 0). HALT was not placed: nothing was open.
- **Investigation (evidence only, not a clearance).** Job #747 ran the same audit on the same ledger with `--min-interval 1.0`: `{"date":"2026-10-10","n_pools":3,"n_disagree":0,"n_unclassified_now":0,"halt":false}`. All three pools classified and agreed with the shadow's class.
- **No written resume path.** This DEC had none for a §5.6 audit halt. The manager put the choice to the owner (MiScusi question q_IKDh83qq9-6gpw: resume now, recommended; or wait for a tool fix).
- **Owner's decision.** At about 2026-10-11T15:40Z, in session, the owner answered: "Resume H5". The STOP was removed at **2026-10-11T15:43:38.903Z** (MiScusi job #750: wallet-wide STOP and HALT absent, H5 HALT absent, `rm` rc 0, then `--status` stop_file=False halt_file=False, open 0, pending 0, unit active).
- **Fix.** `tools/h5_synthetic_audit.py` now re-classifies a pool left unclassified only by an RPC error (`RpcError` or `RpcUnreachable`) after 60 s and then 180 s before counting it. Every other reason, and a pool still unclassified after the waits, counts exactly as before. Also, the runbook's audit procedure now uses `/usr/bin/python3`; job #744 had exited 127 on the missing `/data/mal/venv`.
- **Unchanged.** What counts as a disagreement; §5.6; the STOP-at-once rule. This record changes no section 5 rule. Section 5.1's strike count is unaffected: this is a §5.6 halt.

## Halt record: executor latch `out_of_rule_entry`, 2026-10-12, and the manager's clear

- **Halt.** At 2026-10-12T18:28:02.667Z the live executor latched `out_of_rule_entry` (ledger `halt_latched`). The buy landed at slot 456145178, 29 slots after trigger slot 456145149; s0 slot 456145135; sps 0.2187173840025174. At this sps 29 slots is about 6.3 s. The executor's entry limit `ENTRY_LATE_S` is 5.0 s (23 slots at this sps). The executor sold at once (`exit_landing` landed slot 456145183). After it: attempts 11, sells_landed 11, sells_late 0, open 0, pending 0. No STOP or HALT file at `/var/lib/mal-live/` or `/var/lib/mal-live/h5/` (job #857).
- **Cause** (job #859; from the decision row's timing fields; no fill row was opened). `anchor.trigger_feed_lag_ms` 5419 (the field `anchor.trigger_feed_lag_ms`, recorded as 5419); `anchor.s0_feed_lag_ms` 406; `gap` false; decision at 18:28:01.765Z. The pre-send age limit `max_trigger_age_ms` is 10,000 ms (code default in `tools/h5_executor.py`, line 336; a grep of the live config for `max_trigger_age_ms`, `gap_hold_ms` and `heartbeat_max_age_ms` found no key under those names), while a buy that lands more than 5 s after the trigger is out of the rule. In this case the trigger feed lag was 5,419 ms, under the 10,000 ms limit, so the pre-send check did not refuse it, and the buy landed 29 slots (about 6.3 s) after the trigger, past the 23-slot entry limit. This is one observed case; no other latch is recorded. The pre-send age limit (10,000 ms) is longer than the entry limit (`ENTRY_LATE_S` 5.0 s). The executor has two pre-send age refusals at the same 10 s limit, `stale_trigger` and `stale_trigger_chain` (`_hard_refusal`, h5_executor.py lines 1311-1321).
- **How common** (job #860). Over all 11 decision rows since go-live: `trigger_feed_lag_ms` min −90, p50 81, p90 1,220, max 5,419; 1 of 11 above 1,500 ms. Feed-gap rows 1 to 21 per 10 minutes (the 17:20 bucket has none listed) and redundant-socket reconnect rows 9 to 40 per 10 minutes, in the 10-minute buckets 16:10Z to 18:30Z (the 18:30 bucket is partial; job #858). The 18:20 bucket: feed_gap 18, feed_reconnect_redundant 37, halt_latched 1, out_of_rule_entry 1.
- **Not a section 5 rule.** Section 5 lists six live-halt rules and none is this latch. The runbook (`docs/runbooks/h5-executor.md`, "Halt and refusal names") lists it as an executor latch cleared only by `--clear-halt`. This DEC had no written resume path for it.
- **The owner's words** (2026-10-12, in session, forwarding the Console assistant's note): "Please decide whether to clear the latch, and whether a staleness check on the trigger (state_slot vs current tip) should veto entries before send." and "Keep working and pushing us to profitability rapidly."
- **The clear** (the manager, job #862). Two status checks 6 s apart (no kill file, open 0, pending 0, latched halts exactly `out_of_rule_entry`); `systemctl stop mal-h5-executor` at 18:38:41.272Z (ActiveState inactive); `--live --clear-halt out_of_rule_entry` as `mal-live` at 18:38:41.306Z ("cleared out_of_rule_entry", rc 0); `systemctl start` at 18:38:41.596Z; at 18:38:49.651Z ActiveState active and `--status`: stop_file=False, halt_file=False, halts=[], attempts 11, open 0, pending 0, sells_landed 11.
- **Deviation from the runbook's Wind-down.** The gate was not closed by STOP or by removing `LIVE_OK`. The latched halt was already refusing buys (`halt_latched` in the executor's refusal check) and the two status checks showed open 0 and pending 0 before the stop. The Wind-down's first line (removing `LIVE_OK`, or placing STOP) was not run.
- **No retune.** No rule, threshold, config or code changed. Tier stays T0.
- **Manager's condition, recorded here; it is not a section 5 rule and no DEC-024 rule text is changed:** if `out_of_rule_entry` latches again before the pre-send fix is installed, H5 stays halted until the fix is installed.
- **Status.** H5 has not cleared the promotion gate.
- **Owed (planned, not done).** A reviewed executor change that refuses, before send, a trigger too old to land within 5 s, with an md5 decision-equivalence proof, reviewer and quant-proof verdicts on the PR, then Helm's pinned reinstall. The manager will name this halt in any T1 proposal (manager's note; not a DEC-024 rule).

## Amendment 4 (2026-10-10T07:27:25Z, the owner's decision; written after the section 5.1 halt of 2026-10-10T07:11:07Z): the section 5.1 measure

OWNER_51_MEASURE_CONFIRMED: 2026-10-10T07:27:25Z (owner, in session, asked by manager9).

The owner answered two questions:
- How to handle the halt: "Restart on bot's count (Recommended)".
- Whether a clean restart counts as resolved for the step to T1: "Yes, counts as resolved (Recommended)".

The verbatim questions, options and option descriptions are in MiScusi notebook n_SmtvxryJ4N4Dfw. The instant is the manager's `date -u` read right after the answer.

**Provenance of the items.**
- **From the owner's chosen option text:** the measure (the bot's own count over 30+ pools a day); the resume condition (10-10 completes at or above 337 s); "one more day below 337 s, or a daily check below 330 s, stops it again"; and the fired check becoming report-only.
- **Added by the manager after quant-proof review, both stricter:** two consecutive unevaluated days (under 30 pools) halt, and the manager's daily read that enforces the 07:11Z fire as the first "below 337".

1. **This loosens section 5.1 after it fired.**
   - The A3-run clause that fired (job #478: 333 s after migrate, n=9) is report-only from 2026-10-10T07:27:25Z.
   - When the owner chose this, the executor's measure was known not to have latched (10-09: 340.306 s over 117 pools; 10-10 at 07:15Z: 337.556 s over 200 pools).
   - The 07:11Z fire stays recorded as a fire (see "Halt record: section 5.1, 2026-10-10").
2. **From 2026-10-10T07:27:25Z, section 5.1 is** the executor's UTC-day median of the BOOST last slice after s0, over at least 30 pools (install sha256 `e44d1b4c5ffb25c0705509b1e175015acee6c019c2a73e27053479e591f91152`).
   - Below 335 s halts.
   - Below 337 s on two completed UTC days halts, and the 10-10T07:11Z fire counts as the first. The executor cannot count that fire, so after each 00:00Z the manager reads the completed day's median and places STOP on the first completed UTC day, from 10-11 on, below 337 s.
   - A completed day with fewer than 30 pools is unevaluated. Two consecutive unevaluated days halt.
   - Rule (b) stays: BOOST over before our sell on more than 15% of at least 20 paired sells. It cannot bind before 20 paired sells; there is 1 now.
   - Rule (c) stays: below 300 s on 3 pools in a day.
   - **Backstop:** an A3 run whose median last slice after migrate is below 330 s halts. 330 s is EXP-024's pre-registered line, and an after-migrate reading is an upper bound on the after-first-print one.
3. **Unchanged: section 5.6 in full.**
   - Every A3 halt flag stays a live halt, including `boost_last_slice_early` (median below 315 s after migrate), and so does `program_changed`.
   - The A3 last-slice median is still reported on every run.
4. **Resume.**
   - Resume only when UTC day 10-10 has completed with an executor median at or above 337 s over at least 30 pools, and no other section 5 rule or stop has fired.
   - The manager then removes STOP in a MiScusi job and records here the instant and 10-10's median.
   - If 10-10 completes below 337 s, or with fewer than 30 pools, there is no resume under this amendment.
   - Meeting item 4 **does** resolve the 10-10 halt for section 7 and Amendment 3 item 2 (the owner's second answer). The T0 → T1 step still needs Amendment 3's other conditions: 20 landed buys, landing p50 ≤ 3.0 s, canary and twin means not negative, and Look 1 not negative if read.
   - **Resume record (2026-10-11, the manager).** Item 4 and Amendment 5 item 1 both held, so the manager removed `/var/lib/mal-live/h5/STOP` at **2026-10-11T00:15:06.586Z** (MiScusi job #669: `rm` rc 0, then `--status` `stop_file=False halt_file=False`, `halts=[]`, open 0, pending 0; the wallet-wide `STOP` and `HALT` and H5's `HALT` absent). The owner pre-authorized this removal in session on 2026-10-10, conditional on this check passing; Helm took no action. The figures, read at 00:13:56Z (job #667, main `8b40475`):
     - **Executor (this item's measure):** day 10-10 median **339.573 s over 580 pools**; `boost_lt300` 0 that day; latched halts none.
     - **`tools/h5_boost_day_check.py --mode resume --day 2026-10-10` (Amendment 5 item 6):** all 24 hour files read, executor filter applied (137 pool records out on `gap`, one of them also on `boost_src`), day keyed by file hour. All **339.5735 s, n 580**; plain **337.772 s, n 411**; synthetic 341.727 s, n 169. Verdict `resume_ok`.
     - **No other section 5 rule or stop since 07:15Z.** No A3 run after the 07:14Z one (the next is 2026-10-11T06:41Z); `program_changed` clear; 0 late sells; no stuck position. The running checks at 12:17Z and 18:18Z read `no_halt` (plain 337.068 s, n 234, and 337.254 s, n 315). The 23:47Z running check did not run; the resume read above covers the whole completed day.
     - **The daily check.** Before removal it read ALERTS=1, a single alert, `h5_idle` ("LIVE_OK is present but the canary is not trading: STOP exists"), which is the STOP itself. 20 s after removal it read ALERTS=0.
     - **From here.** Amendment 4 item 2 and Amendment 5 item 1 apply as written: a completed UTC day from 10-11 on below 337 s (executor, or plain over at least 30 plain pools) is the second strike and halts; below 335 s halts at any time.
5. **Not changed:** the section 4 limits, the other section 5 rules, the section 6 seals, EXP-024 and EXP-022. Canary results are not evidence.

## Amendment 5 (2026-10-10T08:15Z, the manager's decision; stricter only; written after the section 5.1 halt and after Amendment 4): plain pools, reconciliation, enforcement

**Provenance.** The owner forwarded a reviewer's findings on Amendment 4 (2026-10-10, about 08:00Z) "purely as information to act on and make sure we're doing things correctly". The manager made this amendment in response. It changes only what can add a halt or block a resume, and it loosens nothing in Amendment 4.

**What it does to the owner's choice.** Item 1 adds a condition to the owner's resume rule. It can therefore block a resume that Amendment 4 alone would allow. The owner may strike item 1 by a dated line here.

**Disclosure.** This was written after the plain-pool medians below were measured. Partial day 10-10 already read below 337 s, so this addition was chosen knowing it may block the 10-11 resume.

1. **A plain-pool measure is added to section 5.1.**
   - **What it measures.** Amendment 4's measure, the executor's own UTC-day median over all pools, stays. Section 5.1 also judges the day median of the BOOST last slice after s0 over the PLAIN pools: `synthetic` false and `synthetic_src` `rpc`, the only pools H5 trades.
   - **The source.** The shadow's pool records, filtered the way the executor filters them: `reason` "horizon", `gap` false, `boost_src` "pda" or "event_authority". `gap` and `boost_src` are structure fields; PR #538 adds them to the tool's allowlist for this. The day key is the hour of the shadow's file, while the executor keys by read time; this is disclosed.
   - **Pool counts.** A plain median needs at least 30 plain pools, and the tool's all-pool median needs at least 30 pools. Each group is judged on its own count.
   - **When it halts.**
     - A completed UTC day from 10-11 on with a plain median below 337 s halts. This is the second strike; the 10-10T07:11Z fire was the first.
     - A running plain median below 335 s halts at any time.
   - **Fewer than 30 plain pools.** Plain is not judged that day. Amendment 4's all-pool rule still applies.
   - **Resume.** Amendment 4 item 4's resume also requires day 10-10's plain median at or above 337 s over at least 30 plain pools. Fewer than 30 means no resume.
     Met on 2026-10-11: plain 337.772 s over 411 plain pools (Amendment 4 item 4's resume record).
2. **Why.** The executor's day median counts synthetic pools, which H5 never trades. Measured (MiScusi job #482; shadow records, not the executor's exact filter):

   | Window | Pool set | n | Median | Share before 329.5 s |
   |---|---|---|---|---|
   | 10-09, 19-23Z (go-live day) | plain | 152 | 337.752 s | 0.105 |
   | | all | 211 | 340.306 s | — |
   | | synthetic | 59 | 345.587 s | — |
   | 10-10, 00-07Z (partial day) | plain | 159 | 336.435 s | 0.151 |
   | | all | 223 | 337.648 s | — |
   | | synthetic | 64 | 342.175 s | — |

   - Plain pools end BOOST earlier.
   - **Share before 329.5 s.** 329.5 s is a fixed constant just under the rule's s0 + 330 s exit. It matches our sells' reported typical landing. No canary field is joined to any pool. The share is a population-level preview of EXP-024 §11's mechanism condition ("BOOST ended before our exit on more than 15% of trades") and of rule (b)'s 15% line. The plain share on 10-10 so far is 0.151, above that line.
   - **The all-pool 10-09 figure is the job's own.** Job #482 computed 340.306 s itself over 211 pools. The executor's 10-09 figure, 340.306 s over 117 pools, is the same value by coincidence of the middle pool: both counts are odd, so each median is one pool's value.
3. **Reconciliation of A3 job #478 with the shadow, on the same 9 mints.** Files: `/data/mal/hunt-1008/h5-work/a3-reconcile-1010/REPORT.md`; MiScusi jobs #483 and #484; 112 public RPC calls, 0 Helius credits.
   - **The rebuild is exact.** It used the monitor's helpers with its own time bounds and matches #478's counts and BOOST distribution: n=9, min 329, median 333, max 353.
   - **Same second.** A3 and the shadow put the last slice on the same absolute second on all 9 pools.
   - **The clock is not the cause.** s0 is the migrate slot on 8 of 9 pools; the ninth is 1 s (2 slots) later.
   - **Medians on these 9 mints:**
     - 333 s after migrate and 333 s after s0, both by block time;
     - 331.499 s by the executor's slots × slot-time estimator;
     - plain (n=6) 332.5 s, synthetic (n=3) 338 s.
   - **Conclusion.** The gap between A3's 333 s and the day medians comes from which pools were sampled, a low draw, not from the clock. A bootstrap from 10-10's distribution so far (10,000 draws, seed 1) puts P(a 9-pool median ≤ 333 s) at 0.194 by block time. On the executor's own slots × slot-time clock, P(≤ 331.499 s) is 0.059, the less favourable figure. On that clock these 9 coins read lower still, so A3 did not overstate anything.
4. **The 330 s backstop is an upper bound.** Amendment 4 item 2's A3 backstop is measured after migrate. That is an upper bound on the time after the first print. By itself it cannot show BOOST outlasting our sell at about 329.5 s. On the sampled pools the bound is close, because s0 equals the migrate slot on 8 of 9.
5. **No A3-run 335 s backstop.**
   - Amendment 4 made the A3-run clause report-only by the owner's explicit choice, so reinstating it is the owner's decision.
   - On about 9 pools it is noisy (item 3).
   - Until 20 paired sells exist for rule (b), item 1's running plain check stands in its place, run every 6 hours.
6. **Enforcement is a scheduled job, not a manual read.**
   - **The owner's measure stays primary.** Every scheduled run also reads the executor's own completed-day median, from `h5-counters.json` by the allowlisted `sudo dd`, as Amendment 4 item 2 defines it. The tool's figures are an extra check.
   - **When a halt is due.** If the executor's figure says so, or either of the tool's figures does.
   - **When a resume is allowed.** Only if the executor's figure and both of the tool's figures clear.
   - **The tool.** `tools/h5_boost_day_check.py` (PR #538, once merged) runs as a MiScusi job on mal-fast-0:
     - day mode after each 00:00Z;
     - running mode every 6 hours;
     - resume mode once, for 10-10.
   - **The log.** Each run appends one JSON line to `/home/claude/data/h5-daily/boost-day.jsonl`.
   - **Placing STOP.** Day and running modes run with `--place-stop`: on a due halt the job touches STOP, verifies it, and exits non-zero.
   - **Never removing STOP.** The tool never removes STOP. Resuming stays a manager step, after Amendment 4 item 4 and item 1 above both hold.
   - **Until #538 merges,** the manager runs the same computation inline in a MiScusi job, with item 1's filter: `reason` "horizon", `gap` false, `boost_src` "pda" or "event_authority". The fields read are the allowlist plus `gap` and `boost_src`. If a run cannot apply that filter, its record names the filter actually used, and its plain figure does not count as item 1's measure. That blocks a resume.
7. **Code note.** The `on_boost_row` docstring (`tools/h5_executor.py`) says ">= 10 pools"; the code uses 30. The fix rides with the pre-10-16 reinstall (the pick-oracle wiring).
8. **Look 1 disclosure.** The Look 1 report states that BOOST timing split by pool class was observed before the look for pools in Look 1's window, from 10-10T00Z on (jobs #482 and #485, and the scheduled checks under item 6). It is structure timing, never joined to an outcome (EXP-024 Am.4 D1).
9. **Not changed:**
   - Amendment 4's items and the owner's choices in it;
   - section 5.6;
   - the other section 5 rules and section 4's limits;
   - EXP-024 and EXP-022.

   Canary results are not evidence.

## Amendment 6 (2026-10-10, the manager's decision; written before 2026-10-16T01:00Z, before any counted CAP-PICK hour and before any H5 trade in the seal window; no CAP-PICK outcome, paper-twin field or gate-log row was read to write it): the pick feed as built (section 6)

**Written** 2026-10-10T09:01:46Z (`date -u`). That is before 2026-10-16T01:00Z, and before the exporter job or a reinstalled executor is deployed: PR #509 (head `3a6dae3`) and PR #540 (head `e7312eb`) are open and unmerged at this instant. The text is quant-proof's ruling on those two heads (2026-10-10). Item 8 is the manager's record of the duration and `end_ms`. Implemented and cleared by quant-proof at #509 `8b3ce7c` and #540 `4feec6d`.

Section 6 says the executor "reads only the `mint` field of the decision-time intents into an in-process set". The feed as built (PR #509 `tools/cap_pick_oracle.py`, wired by PR #540) differs as follows. Items 1 to 3 and 5 to 6 are stricter for H5. Item 4 is declared here.

1. **Exporter.** A MiScusi job (`cap_pick_oracle.py export`) reads the paper runner's `exp012-gate.jsonl` and `intents.jsonl`. It reads only:
   - the `mint` and `entered` of `forward_paper_exp012_gate_v1` rows of book `exp012_migrate_tp50_sl30`;
   - the `mint` of `forward_paper_intent_v1` rows of that book;
   - any walk-2 replay decision list.

   It extracts only those values, with fixed patterns, and parses no other part of a line. It writes `{mint, pick, t_ms}` rows and `{hb, t_ms}` heartbeats to `picks.jsonl`. It opens no source before the FINAL marker exists and 2026-10-16T02:00:00Z (DEC-016 Amendment 2). It prints no count that includes intents-file lines.
2. **Readers.** The H5 executor and the H5 shadow read only `picks.jsonl` and the replay boolean files, through `PickOracle`, into memory. The answer has three states:
   - **True:** any source picked the mint. True is sticky.
   - **False:** a gate row with a computed score decided not-pick, and no source picked it.
   - **None:** anything else. This includes no row, a gate row with no score, a feed stale for more than 60 s, no FINAL marker, or an error.
3. **Rule.** In the seal window:
   - The executor buys only on an exact False. True, None, a non-boolean or an error all refuse, so an undecided mint is never bought.
   - The shadow writes a pool's outcome-bearing records only on an exact False at each decision point. A trigger that fires on None is withheld for good.
4. **Residual (declared).** EXP-022's counted picks are the walk-2 replay's picks (EXP-022 section 2). They are not known at H5 decision time, and they differ from the online set both ways (EXP-022 section 17 item 12; not measured on October data). H5, live or shadow, can therefore trade or record a mint the live gate scored below threshold that the replay later picks.

   For EXP-022 Amendment 2 item 2, "a mint the EXP-022 gate picked" means a mint the oracle answers True for at the H5 decision. A trade or record on a replay-only pick made under item 3 is not a breach. These hold:
   - (a) Before each CAP-PICK look, no person, agent or job joins any H5 executor record, H5 shadow record or the wallet history to the replay pick list or to any CAP-PICK pick set, or counts the overlap. A join is a breach.
   - (b) After each look is written, a count-only step reports two numbers: how many counted picks in the look's window have an H5 live fill, and how many have an H5 shadow outcome record. Both go into the CAP-PICK look report and the EXP-024 report. They change no computation, threshold or verdict.
   - (c) The report states that H5's own live fills on such mints are on the walk-2 tape the CAP-PICK read prices.
5. **Records and counts.**
   - A pool the oracle never cleared closes with a `sealed: true` record that holds only open-time fields. It marks "pick or undecided" by mint, with no outcome. Section 6 seals outcomes and joins, not pick identity.
   - `sealed_hour` prints `pools_opened` and the string "withheld" for `decisions`, because an H5 decision on a pick is outcome-linked.
   - For the same reason, no executor ledger row, counter, alert or log holds a `seal_pick` count before EXP-022's read ends.
6. **Markers.** The manager creates `~/data/cap-pick-oracle/FINAL_WRITTEN` and `/var/lib/mal-live/h5/FINAL_WRITTEN` only after the DEC-016 FINAL row is in `/data/mal/exp012-forward/FINAL_READS.jsonl`. The creation instant (`date -u`) is recorded in LAB_STATE.
7. **Unchanged.** The 60 s fail-closed halt, the seal window, the breach consequence, and every other rule in section 6.
8. **Duration and `end_ms` (recorded; nothing here changes them).**
   - **The owner did not extend the 14-day duration** (section 4). The owner's words, 2026-10-10 about 08:35Z: "Don't extend the trial I think, let's keep things moving" (MiScusi notebook decision entry "OWNER 2026-10-10 ~08:35Z: do not extend H5's 14-day trial", id n_FdE6WF1rhq8-Hw, confirmed by the manager who created it). Section 4's cap stands.
   - **The live config's `end_ms` is 2026-10-16T00:30Z at this writing.** From that instant the installed v1 executor refuses every new buy (`end_instant`, `tools/h5_executor.py`). So it stops buying 30 minutes before the seal window opens at 2026-10-16T01:00Z. Extending `end_ms`, at most to section 4's cap of 2026-10-23T19:29:53Z, is a reviewed config change plus a reinstall (the manager's step); a run past that cap is the owner's decision (section 4). 2026-10-16T00:30Z is earlier than that cap (14 days from the first send, 2026-10-09T19:29:53Z).
   - **The oracle wiring still matters.**
     - The H5 shadow is the canary's paper twin. It keeps running through Look 2's added window `[2026-10-16T00, 2026-11-06T00)`, and items 2, 3 and 5 govern which of its records it writes in the seal window. Until PR #540 is merged and the shadow restarted on it, the shadow seals every pool in the seal window (section 6, Dependency).
     - Any executor that runs in the seal window (an `end_ms` after 2026-10-16T01:00Z) must carry items 2, 3 and 5 (PR #540 or a later head with the same seal code).

**Item 8 record (2026-10-11, the owner's choice; the manager's step).** Written 2026-10-11T02:55:40Z (`date -u`).
- **The owner's answer.** On 2026-10-11, in session, the manager asked "Should the reinstall extend its end time to the 14-day cap?" and the owner answered "Extend to 10-23 (Recommended)".
- **The change (PR #591).** `scripts/mal-fast/h5-executor-live.json` `end_ms` goes from 1792110600000 (2026-10-16T00:30Z) to 1792783793000 (2026-10-23T19:29:53Z). That is section 4's cap: 14 days from the first send, 2026-10-09T19:29:53Z (Go-live record). No other limit, stop, tier or rule changes.
- **The duration is not extended.** Section 4's 14-day cap and the 2026-10-10 bullet above stand. The new `end_ms` equals the cap and is not later. A run past it is the owner's decision (section 4).
- **The seal.** The new `end_ms` is after 2026-10-16T01:00Z, so the executor runs in the seal window. The executor at the named sha carries items 2, 3 and 5: the PR #540 seal code, merged as `288f490` and re-cleared by quant-proof (record below). Item 8 requires this for any `end_ms` after 2026-10-16T01:00Z.
- **When it takes effect.** Only at Helm's pinned reinstall at the sha the manager names, before 2026-10-16T01:00Z, while the bot has nothing open. Until then the installed `af02e90` executor keeps `end_ms` 2026-10-16T00:30Z.
- **What binds first: max_days raised to 14 (the manager's decision under the owner's delegation).** `max_days` is a separate duration stop: a code constant counted from the executor's first buy attempt (`H5_DEFAULT`, `tools/h5_executor.py`; config may only lower it). It was 10 days, which would have stopped new buys at or after 2026-10-19T19:29:53Z, before the new `end_ms`. The manager asked the owner whether to raise it to 14. The owner answered: "You can chose based on whatever you think is going to get us to our goal honestly. ... Decisions like these you can make more autonomously, just let me know." (MiScusi notebook decisions n_u3ZVBw_dYwYvHA for the end time and n_Z69jRvirhW_HyQ for this). PR #591 raises `max_days` to 14, which is section 4's cap ("at most 14 days from the first send (code constant)"). `first_attempt_ms` is at or after the first send, so `end_ms` (the cap) binds first, and nothing runs past section 4's cap. No other limit, stop or tier changes.
- **Proofs.** The shadow detector is unchanged by this PR. md5 decision-equivalence on the 25-hour replay 2026-09-20T00..2026-09-21T00 (the same as job #498): `75cb0b0c585bc2479137cae31330e73e`, 72/72, MiScusi job #691 at `1538d6e` (PASS). The executor's one-constant change (`max_days` 10 to 14) is covered by `tools/test_h5_executor_rules.py::test_shipped_configs`: the constant, the clamp, and that `end_ms` binds first. The H5 suites pass: 1078 passed, 15 skipped.

**Amendment 6, re-clearance record (2026-10-10T09:32:14Z).** Quant-proof re-cleared #540 at `bea3f30` (a merge of main `b5ba835` into `4feec6d`). The executor, pinned configs, oracle wrapper and seal code are identical to `4feec6d`. The shadow adds #541's report-only `outcome_bx10` behind the same CAP-PICK and H5 Look-2 seals. md5 decision-equivalence at `bea3f30`: `75cb0b0c585bc2479137cae31330e73e`, 72/72 (MiScusi job #498, 25-hour replay 2026-09-20T00..2026-09-21T00). Merged as `288f490`.

## Amendment 7 (2026-10-11, the owner's decisions; written before EXP-024's Look 1 read and blind to its outcome; no canary ledger, shadow outcome record or Look 1 statistic was read to write it): a Look 1 FAIL keeps T0 for execution measurement, the resume after a benign program upgrade, and a pointer to the v2 pre-authorization

OWNER_H5_1011_CONFIRMED: 2026-10-11T03:35:22Z (owner, in session, asked by the manager). Four questions, each answered with the recommended option. MiScusi notebook n_soP9VudXSnG4Jw holds the verbatim questions, option texts and answers. The instant is the session's timestamp on the owner's answer.

**Written** 2026-10-11T03:45:37Z (`date -u`). Main was `5ebc7ca` and v1 was trading at T0 (resumed 2026-10-11T00:15:06.586Z, Amendment 4 item 4). The executor's `end_ms` is 2026-10-23T19:29:53Z (Amendment 6 item 8, PR #591, named sha `f873aac`).

**Numbering.** Main holds Amendments 1 to 6 at writing, and no open PR proposes an Amendment 7, so this is Amendment 7. Its first draft (`f618f8e`) numbered it 8 from the manager's brief; it was renumbered 2026-10-11, and DEC-028 §3.5 (PR #542) cites it as Amendment 7.

**Revised** 2026-10-11T05:32:25Z (`date -u`) for quant-proof's check of `f618f8e` (OK_WITH_FIXES): item 1's last bullet, and item 2's review items 4 and 6 and resume steps 1 and 3. Every change is stricter or a clarification. No canary ledger, shadow outcome record or Look 1 statistic was read for the revision.

### 1. A Look 1 FAIL keeps H5 at T0 for execution measurement only (section 5)

- **The question, verbatim:** "If H5's 10-16 read comes back 'stop' (its October average at or below zero), H5's rules end new buys the next day unless you keep it running at the smallest size (0.02 SOL) to keep measuring how live trading behaves. Decide now, before the read, so the choice can't be swayed by the result?"
- **The answer:** "Keep it at 0.02 SOL (Recommended)". **The option text:** "H5 keeps trading at 0.02 SOL through 10-23 for execution data (fills, landing speed, sells), with all halts and loss stops kept. Expected cost is tiny if the edge is near zero. The +0.5 SOL step-up stays blocked."
- **What it changes.** One stop in section 5 reads: "EXP-024 Look 1 is a FAIL: new buys stop at the next 00:00Z after the Look 1 report, unless the owner extends the canary for execution measurement only." This is that extension. It was decided before the read, so it cannot depend on the result. On a Look 1 FAIL (EXP-024 §8.3: the flat and pressure means on D both ≤ 0), new buys do **not** stop at the next 00:00Z, and the canary continues on these terms:
  - **T0 only, 0.02 SOL per entry.** T1 stays blocked: Amendment 3's step-up is not taken, and the +0.5 SOL top-up is not sent.
  - **Through the 14-day end only.** That is 2026-10-23T19:29:53Z (`end_ms`), or `max_days` if it binds first. This item does not extend the duration.
  - **Every section 5 halt as amended stays live.** That includes section 5.1 under Amendments 4 and 5, every A3 halt flag, `program_changed`, the stuck-position rule and divergence. Every other stop stays. So do section 4's limits, including the daily stop, the total stop (the counters continue) and the 2-open and 30-a-day caps.
  - **Seals unchanged.** Section 6, the CAP-PICK seal (Amendment 6) and EXP-024 Amendment 4's D1 all stand.
- **What it is not.** It is not evidence and never a book. EXP-024's FAIL retires v1 as a candidate, and this item does not soften that. Reports after the FAIL label the canary "execution measurement after an EXP-024 Look 1 FAIL; not a book". No report may say the canary shows a profit or an edge. It changes no EXP-024 parameter, look or read.
- **Which rule it covers.** It covers the `h5` profile at T0 whichever H5 rule it trades: v1, or v2 if DEC-028 §3.5's pre-authorized switch has happened (item 3). That matches the owner's two answers, which both run H5 at 0.02 SOL until 10-23 [manager's reading, accepted by quant-proof at `f618f8e`]. **It waives only the EXP-024 Look 1 FAIL stop** of section 5. If v2 is the rule traded, its halts are DEC-028 §4's (section 5.1 is report-only for v2), and an EXP-026 FAIL still ends v2 at the next 00:00Z after the read (DEC-028 §4's stops).

### 2. Resume after a pump.fun program upgrade (section 5.6), after the manager's mechanism review

- **The question, verbatim:** "If pump.fun upgrades its programs again, H5 halts automatically ('program changed'). May H5 resume once I've reviewed that the upgrade doesn't change the boost mechanics H5 depends on, without asking you each time?"
- **The answer:** "Yes, after my review (Recommended)". **The option text:** "I review the change, using the same template as the 10-08 redeploy. If boost and fees behave the same, I resume H5 and tell you. Anything that changes boost keeps H5 stopped until you decide."
- **Scope.** A STOP placed because the pump (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`), PumpSwap (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`) or fees (`pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ`) program was redeployed. The A3 monitor shows this as `program_changed` (programdata sha256 against the pins) and as `pins_changed` from a changed deploy slot.
  - **Not in scope: a changed config account.** If the bonding FeeConfig, PumpSwap FeeConfig or PumpSwap GlobalConfig sha256 differs from its pin, that is a change to fees or BOOST by definition. H5 stays stopped until the owner decides.
  - **Not in scope: any other halt that fires with the upgrade.** That includes another A3 flag, section 5.1, landing, late sells or a stuck position. Each keeps its own resume rule.
- **The review.** The template is `ARTIFACTS/lab/a3-repin-2026-10-09-program-upgrade-review.md` (the 10-08 redeploy). The review is outcome-blind: no canary ledger, shadow outcome record or paper twin is read for it (EXP-024 Amendment 3). It is written to `/data/mal/hunt-1008/h5-work/PROGRAM-UPGRADE-<date>.md`, and a copy goes in `ARTIFACTS/lab/`.
  1. **What changed.** Which programs, the deploy slots and signer, the upgrade authority, the programdata sha256 against the pins, account sizes (pump Global), and the public docs and IDL commits.
  2. **Layouts.** The executor's PumpSwap `buy`, `sell` and `buy_exact_quote_in` account lists. The decoder's event offsets (BuyEvent, SellEvent, V). That Q = quote + V still matches the vault's pre-trade balance.
  3. **Fees unchanged.** The three pinned config accounts (bonding FeeConfig, PumpSwap FeeConfig, PumpSwap GlobalConfig) have sha256 equal to `tools/pump_structure_pins.json`. That fixes the fee tiers, bps, recipients and the boost flag. Any difference sends the decision to the owner.
  4. **BOOST unchanged.** **Post-upgrade evidence only:** every check in this item uses only graduations migrated after the new deploy slot (the latest redeployed program's), at least **15** of them; a graduation migrated before that slot never counts. "Pre-upgrade" below means the last 4 A3 runs before the deploy slot. BOOST unchanged means all of the following:
     - **InitBoost** is still a PumpSwap instruction in the migrate transaction of non-mayhem WSOL graduations, on at least 80% of the post-upgrade sample (A3 `boost_share_low` evaluated and clear).
     - **Slices** are still PumpSwap `BoostBuyAndBurn` transactions signed by the boost-vault PDA (`["boost_vault", pool]`, PumpSwap), and still pay zero fees. That was 77/77 slices on 10-10, job #638.
     - **GlobalConfig** is still 949 bytes, with `boost_enabled` (byte 939) = 1 and `boost_authority` (bytes 907..939) unchanged.
     - **Budget:** the post-upgrade median full-pool BOOST total is within **±1% of 17.585 SOL** (pinned `budget_sol` 17.585; 17,584,505,288 lamports on 10-10, job #638).
     - **Slices per pool:** 29–30 (pinned 29).
     - **Slice cadence:** the post-upgrade median slice interval is within **±10% of the pre-upgrade median** (the same measure on the pre-upgrade runs' graduations; September's was 12.06 s). DEC-028's [6, 24] s band is a v2 halt band and does not show cadence unchanged.
     - **Last slice:** the post-upgrade median last slice after migrate lies within **[min − 5 s, max + 5 s]** of the 4 pre-upgrade A3 run medians.
     - **A3's flags are necessary, not sufficient.** `boost_budget_or_slices_changed` (±20%) and `boost_last_slice_early` (315 s) must be evaluated and clear, but the bands above are the test.
     - **If any of these is outside its band, BOOST has changed.** H5 stays stopped until the owner decides.
  5. **H5's inputs unchanged** [manager's addition, stricter]. The quantities the rule reads are unchanged: Q = quote + V, s0 as the first PumpSwap print, and the plain/synthetic class (Amendment 2). A change here, such as 10-08's synthetic migrations, also sends the decision to the owner. A population shift that leaves these unchanged is reported to the owner with the resume.
  6. **Executor.** A keyless dry run on the live programs, with at least 5 simulated buy and sell round trips and 0 simulate errors (the template's residual risk 6). **Fees behave the same:** in that dry run, every simulated buy and sell event's fee bps (LP, protocol, creator) equals the tier the pinned PumpSwap FeeConfig gives that pool. Equal config sha256s (item 3) pin the fee parameters, not a redeployed program's fee computation, so this check is needed too. Any mismatch sends the decision to the owner. The check is keyless and outcome-blind, and reads no tape.
- **The resume steps, in order.**
  1. The review finds items 2–6 unchanged. **A layout change (item 2) is not resumed under this item.** It needs a reviewed code PR with an md5 decision-equivalence proof and a Helm reinstall, outside this item, and H5 stays stopped until then.
  2. Re-pin PR: `tools/pump_structure_monitor.py --write-pins`, with the three program sha256s, citing the review. It merges with quant-proof's OK.
  3. An A3 run after the re-pin is clean on the five H5 halt flags (`pins_changed`, `boost_disabled`, `boost_share_low`, `boost_last_slice_early`, `boost_budget_or_slices_changed`), and `program_changed` is clear. **Clean means evaluated and clear, on post-upgrade graduations only:** the run's whole BOOST sample migrated after the new deploy slot, it has at least 15 such graduations, and all five flags are evaluated. The monitor sets `MIN_BOOST_POOLS = 3` and reports an unevaluated flag as `False` (`tools/pump_structure_monitor.py`), so a run whose flags rest on fewer pools, on pre-upgrade pools, or on none does not count.
  4. No other section 5 rule or stop is active.
  5. The manager removes `STOP` in a MiScusi job and records here the instant, the review file, the re-pin commit and the A3 job. The owner is told with a MiScusi notebook entry and the Console.
- **What it does not change.** It resumes the canary only. EXP-024's A3 rule is EXP-024's own: inside a counted window, a `pins_changed` flag ends the read NOT_DECIDABLE, with no resumption if the monitor clears. EXP-022's own A3 rules also stand. This item cures neither, and a resumed canary does not revive either read. Amendment 3's T1 conditions are unchanged. No retune follows a halt (section 5).

### 3. Pointer: H5 v2's pre-authorized Option B (DEC-028)

- The owner chose DEC-028 Option A, with a pre-authorized Option B. The terms are in DEC-028 §3.5 (`DEC/DEC-028-h5-v2-canary.md`, draft, PR #542; not on main at writing). If v1 is halted by a section 5.1 BOOST-timing halt, H5-BOOSTCLOCK v2 may go live at T0 0.02 SOL on this wallet, with section 4's caps and the counters continued, until 2026-10-23T19:29:53Z. That needs v2's live sell code reviewed and one Helm reinstall.
- **Here, nothing changes for v1.** A section 5.1 halt of v1 is recorded under this DEC as usual, and v1's resume rules (Amendments 4 and 5) stand. v2 is a new rule pre-registered in EXP-026, not a retune of v1 (DEC-028 §1). v1 and v2 are never live together (DEC-028 §3.4).
- The owner also confirmed DEC-028 §3.1's two required sentences verbatim, recorded there as `OWNER_OVERRIDE_V2_SENTENCES_CONFIRMED` (2026-10-11T05:26:36Z, MiScusi notebook n_fZNH1URVIkXIJQ).
- **Until DEC-028 merges** with quant-proof's OK on its final head, this pointer authorizes nothing.

## Manager note (2026-10-11, the manager; stricter only): phase-2 gate rule for the union field

Written before any side-by-side union data was read. First written 2026-10-11T06:07:44Z (`date -u`) on main `10fab0a`. Revised 2026-10-11T06:18:49Z after quant-proof's review of PR #600: item 4's sealed-record sentence corrected, plus tightenings only. No union field, `base_union` count, tip row, shadow record, canary ledger or outcome was read to write it. It authorizes nothing. It changes no rule in section 5, section 6, Amendment 6 or Amendment 7.

1. **What is live.** PR #596 (`5f6b304`) adds report-only fields to shadow records: `base_breaks_unresolved_settled_union`, `base_union` and `base_unmatched_*`. No decision reads them. The live executor's gate is unchanged: it refuses on the feed's own `base_breaks_unresolved_settled` (`bad_intent:base_breaks_unresolved_settled`, `tools/h5_executor.py`).
2. **Phase 2 is a live decision change.** Any change that makes the executor gate on `base_breaks_unresolved_settled_union` or on any union-derived field ("phase 2") changes live executor and shadow decisions. That holds whenever it merges: in Look 1's window while the canary is live, in Look 2's added window `[2026-10-16T00, 2026-11-06T00)` (DEC-024 section 6, EXP-024 Amendment 2), and in the seal window from 2026-10-16T01:00Z. Before merge it needs **all** of:
   - the owner's dated decision;
   - a DEC-024 amendment;
   - the md5 decision-equivalence replay every runner change carries;
   - **and** a declared decision-change account: which live decisions change, and that no other does;
   - quant-proof's OK posted on the PR, on its final head.
3. **The acceptance rule is fixed now and may only be tightened.** A phase-2 proposal may use only mechanism and counts:
   - **union status and coverage:**
     - the share of records whose `base_union.status` is `ok`;
     - tip lag (`tip_lag_slots`);
     - the `ws_only`, `tip_only` and `both` counts, and `ws_only_sig_on_tip`;
     - whether the two decoders agree on `event_index`;
   - **the gate's pass and refuse counts** under the union view against the websocket view;
   - **price accuracy:** only the trigger print's own pre-trade q and base in the trigger record, against the tip's row for the same `(signature, event_index)`. Never the landing-slot state, the entry fill, the exit, the ladder or any later print.

   It never uses, computes or looks at which triggers won or lost, fills, P&L or any other outcome. No count here is joined to the replay pick list or to any CAP-PICK pick set (DEC-024 Amendment 6 item 4(a); EXP-022 Amendment 6 item 3). A replay-only pick's trigger record is the declared residual.
4. **Data before any proposal.**
   - **Duration:** at least 24 h of union data, counted only from the shadow's restart on the merged follow-up PR in item 5, as shown by the sha in that restart's start record.
   - **Coverage:** `base_union.status` is `ok` on at least 90% of the trigger records in that data.
   - **Decoder agreement:** whether the two decoders agree on `event_index`, measured on live data. A replay uses the tape as its own tip, so it cannot measure this.
   - **Sealed records:** none is opened for any count here (CAP-PICK sealed or H5 Look-2 sealed).
     - A CAP-PICK-sealed record carries no union fields.
     - An H5 Look-2-sealed pool's close record carries none either.
     - That pool's trigger record (`h5_look2_sealed: true`) keeps the decision-time `base_breaks_unresolved_settled_union` and `base_union`, and no count here uses it.
     - Counts filter on `sealed` and `h5_look2_sealed`, never on whether a field is present.
5. **No look-ahead.** The trigger-time union fields count only tip rows at or below the trigger print's slot once the follow-up PR that enforces this is merged and installed in the shadow. Until then, no phase-2 proposal may be made. Union data from before that install is not counted toward item 4: it can include tip rows above the trigger slot.

**Addendum (2026-10-11T07:10Z, the manager; stricter only, records the install).**
- **The follow-up of item 5.** It is #602, merged as `b65e2ec`.
  - It was installed in the shadow restart of 2026-10-11T07:05:48.244Z (MiScusi #735), from a clean checkout of `b65e2ec` (0 porcelain lines).
  - The start record carries code_sha `b65e2ec3b315dde2c6728f2b8e9054f6ac6ac05e`.
  - Item 4's 24 h counts from that instant.
- **Naming.** In trigger records, `base_union.tip_lag_slots` is now `tip_lag_slots_global`, and `tip_hw_slot` is now `tip_hw_slot_global`. Pool close records keep `tip_lag_slots`. Item 3's "tip lag" means the same measure under either name, and a count must name the field it uses.
- **Status.** A trigger record whose `base_union.status` is `regress` or `ahead` counts as **not** `ok` in item 4's 90% share.
- **Clean checkout.** A later reinstall of the shadow that is to count toward item 4 records an empty `git status --porcelain` with its start.

### Phase note: pre-send trigger age limit (2026-10-12)

On 2026-10-12 the live feed delivered a trigger print 5,419 ms after its chain time. The executor sent the buy and it landed 29 slots after the trigger. That is more than the 5 s entry window (`ENTRY_LATE_S`), so it was an `out_of_rule_entry`: an immediate exit and a latched halt. The old pre-send limit on trigger age was 10 s, so a trigger between about 4 s and 10 s late was bought although it could not land in the rule. This is the event in "Halt record: executor latch `out_of_rule_entry`, 2026-10-12, and the manager's clear".

The change: the three existing stale checks (`stale_trigger` twice, `stale_trigger_chain`) now use `PRESEND_MAX_TRIGGER_AGE_MS` = 3_500 ms, which is `ENTRY_LATE_S` less a 1.5 s allowance for build, send and landing. A config value may shorten it and never lengthen it. The change only adds refusals. A trigger the old limit refused is still refused. C1-NF keeps its own 10 s value.

It is not in force until Helm's pinned reinstall of the named sha. It is not a retune of the rule: the 5 s entry window, the halt, the exit logic and every rule threshold are unchanged. It needs "Reviewer OK" and "QP OK" on the exact head.

## Open for the owner

1. **The 1 SOL scale-up route.** Answered 2026-10-08 on the `OWNER_OVERRIDE_CONFIRMED:` line in section 7 (see its provenance note). The trial's stake, open-position cap and stops at about 1 SOL were answered 2026-10-09 on the `OWNER_LADDER_CONFIRMED:` line (Amendment 3: T1 code-constant limits). Still open: T2 and anything above it.
2. **The declared observation.** Section 6 records a decision relayed by the manager: canary and shadow outcomes for Look 1's window pools are seen in real time, and Look 1 is read as written whatever they show. EXP-024 Amendment 2 (2026-10-09) extends it, as a manager decision derived from your scale ladder, the 10-08 mandate and the override line, to Look 2's added window `[2026-10-16T00, 2026-11-06T00)`, and Look 2 is read as written whatever they show. You may revoke the extension. Please confirm it in your own words. It can influence your scale-up choice, and the Look 1 and Look 2 reports say so.
3. **The limits.** Stake 0.02, 2 open, 30 a day, stops 0.08 and 0.12, and a 14-day duration are the manager's terms.

## Sources

- `/data/mal/hunt-1008/h5-work/LIVE-PLAN.md` (execution plan, risk limits, build estimates) and `PLAN.md` §5 and §7 (kill rules, stops).
- [DEC-019](DEC-019-execution-probe.md), [DEC-020](DEC-020-size-step-proposal.md), [DEC-021](DEC-021-champion-challenger.md) §7, [DEC-018](DEC-018-live-trial-readiness.md).
- [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) §9.
