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
- **Never before 2026-10-10T00:00Z** and never while the A3 monitor shows a halt.

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
4. **Divergence.** After at least 100 fills, the live−twin per-trade CI90 upper bound below −1 pp (live worse than its paper twin by more than 1 pp). The twin is an H5 outcome. For pools in Look 1's window it is observed in real time (section 6); for later pools it stays sealed as section 6 says.
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
- **Scope.** Pools whose s0 is in `[2026-10-10T00, 2026-10-16T00)`. The executor still writes its fills, exits and wallet deltas under `/var/lib/mal-live` (mal-live, 0700). For these pools the manager and builders may read them. For pools with s0 at or after 2026-10-16T00 (Look 2's added window), they read only the allowlisted timing and count fields (decision → send ms, send → landed ms and slots, the in-slot index, the outcome class, the day's attempt count, as `tools/runner_timing_read.py` does) until Look 2 is read.
- "Look 1's rule, data, analysis and pass bar are fixed by [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Part 1. Look 1 is always read and always reported as written. It is never skipped, delayed, re-scoped or re-thresholded because of anything the canary or shadow shows. Nothing the canary shows may change any EXP-024 parameter." A canary halt (section 5) stops the canary only. It does not stop, delay or change Look 1.
- "The canary's scale-up decision is a separate business decision, not EXP-024 evidence."
- "Disclosure: concurrent observation lets canary results influence later choices (for example an owner scale-up). That never changes the formal read, and the read reports it."
- **Not covered.** The EXP-024 read tool's computation of H5 outcomes from the tape stays sealed until the look. **CAP-PICK is not touched:** the CAP-PICK seal above holds exactly as written.
- **Provenance.** A decision relayed by the manager on 2026-10-08. The sentences in quotation marks are the manager's wording. The owner's own words on it are not quoted here.
- **Wallet exposure.** The wallet is public on chain, so anyone can compute its P&L. That is consistent with the declared observation for Look 1's window pools, and it is still a seal exposure for later pools.

## 7. Scale-up to 1 SOL

The scale-up needs **the owner's OK (the override line below) and all** of these:
- live execution is consistent with the sim: the canary's landing p50 is at most 3.0 s, measured over at least 20 landed buys, and the live−twin per-trade CI90 upper bound is not below −1 pp, after at least 100 fills;
- the shadow run (the paper twin of the canary's decisions, priced by the frozen sim at 0.02 SOL) is not negative, meaning its mean per trade is not below zero;
- EXP-024 Look 1, if it has been read, is not negative: its deciding-cell flat and pressure means are not below zero. **Plainly: Look 1 cannot be read before about 10-16, so this condition cannot bind before then, and it does not apply to a scale-up made in 10-12 to 10-14.** It applies only to a scale-up made after Look 1 has been read;
- the canary's own realized result, after fees, is not negative: its mean per closed trade is not below zero;
- no live-halt rule (section 5) and no stop that ends the canary has fired and is unresolved. These rules, and the daily and total stops, stay in force at every size;
- the owner states the 1 SOL stake, open-position cap and stops in writing (a dated line in this file).

**These are necessary and not sufficient.** The twin is observed in real time for Look 1's window pools (section 6), and Look 1, if it is read, is read as written. Neither clears the promotion gate. CLAUDE.md and the 10-08 mandate say that nothing goes live before a book clears the gate on both fail models. **This DEC does not waive that on its own.**

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

## Open for the owner

1. **The 1 SOL scale-up route.** Answered 2026-10-08 on the `OWNER_OVERRIDE_CONFIRMED:` line in section 7 (see its provenance note). Still open: the trial's stake, open-position cap and stops at 1 SOL, in writing.
2. **The declared observation.** Section 6 records a decision relayed by the manager: canary and shadow outcomes for Look 1's window pools are seen in real time, and Look 1 is read as written whatever they show. Please confirm it in your own words. It can influence your scale-up choice, and the Look 1 report says so.
3. **The limits.** Stake 0.02, 2 open, 30 a day, stops 0.08 and 0.12, and a 14-day duration are the manager's terms.

## Sources

- `/data/mal/hunt-1008/h5-work/LIVE-PLAN.md` (execution plan, risk limits, build estimates) and `PLAN.md` §5 and §7 (kill rules, stops).
- [DEC-019](DEC-019-execution-probe.md), [DEC-020](DEC-020-size-step-proposal.md), [DEC-021](DEC-021-champion-challenger.md) §7, [DEC-018](DEC-018-live-trial-readiness.md).
- [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) §9.
