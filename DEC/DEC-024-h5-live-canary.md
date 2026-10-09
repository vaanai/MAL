# DEC-024: H5 live measurement canary (0.02 SOL)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-08; the owner approved the canary in principle the same day (his words are in section 1).** The limits below are the manager's terms for his confirmation. It takes effect on merge, with quant-proof OK on its final head. **No live send** until every item in section 8 is done, and never before [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Part 1 is merged. |
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

**Not re-run.** The stop-probability table of Amendment 1 item 4 used September's confirmation day counts, which have no synthetic pools. Excluding them lowers the number of trades by the synthetic share (about 0.20 to 0.37 on the readings above [inferred]). The table is not re-run here. Section 4 holds: a stop firing is not evidence about H5.

**Unchanged:**
- the limits in section 4, and the other halts in section 5 apart from item 3 above;
- the seals in section 6, and the scale-up conditions in section 7 apart from item 5;
- the owner override line;
- every EXP-024 and EXP-022 rule. EXP-022 is not amended here; its synthetic handling needs its own amendment (EXP-022 Amendment 5, item 3).

**Takes effect** on merge, with quant-proof's OK on its final head, before the official P2 run and before 2026-10-10T00:00Z. It starts no trade. `LIVE_OK` and the go to Helm stay separate acts.

## Open for the owner

1. **The 1 SOL scale-up route.** Answered 2026-10-08 on the `OWNER_OVERRIDE_CONFIRMED:` line in section 7 (see its provenance note). Still open: the trial's stake, open-position cap and stops at 1 SOL, in writing.
2. **The declared observation.** Section 6 records a decision relayed by the manager: canary and shadow outcomes for Look 1's window pools are seen in real time, and Look 1 is read as written whatever they show. EXP-024 Amendment 2 (2026-10-09) extends it, as a manager decision derived from your scale ladder, the 10-08 mandate and the override line, to Look 2's added window `[2026-10-16T00, 2026-11-06T00)`, and Look 2 is read as written whatever they show. You may revoke the extension. Please confirm it in your own words. It can influence your scale-up choice, and the Look 1 and Look 2 reports say so.
3. **The limits.** Stake 0.02, 2 open, 30 a day, stops 0.08 and 0.12, and a 14-day duration are the manager's terms.

## Sources

- `/data/mal/hunt-1008/h5-work/LIVE-PLAN.md` (execution plan, risk limits, build estimates) and `PLAN.md` §5 and §7 (kill rules, stops).
- [DEC-019](DEC-019-execution-probe.md), [DEC-020](DEC-020-size-step-proposal.md), [DEC-021](DEC-021-champion-challenger.md) §7, [DEC-018](DEC-018-live-trial-readiness.md).
- [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) §9.
