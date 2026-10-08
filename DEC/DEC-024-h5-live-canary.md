# DEC-024: H5 live measurement canary (0.02 SOL)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-08; the owner approved the canary in principle the same day (his words are in section 1).** The limits below are the manager's terms for his confirmation. It takes effect on merge, with quant-proof OK on its final head. **No live send** until every item in section 8 is done, and never before [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Part 1 is merged. |
| **Decider** | Vaan (owner) |
| **Date** | 2026-10-08 |
| **Builds on** | [DEC-019](DEC-019-execution-probe.md) (probe wallet, custody, executor), DEC-020, DEC-021 §7, [DEC-023](DEC-023-h5-family.md), EXP-024, EXP-022 §9 |
| **Amends** | The paper-only fence and the "no trading keys on hosts" rule (CLAUDE.md, CONSTITUTION), **only** for one executor profile (`h5`) on the one probe wallet DEC-019 created, on `mal-fast-0`, for this canary. DEC-019 limited that exception to "the duration of the probe". This DEC extends it to the canary and to nothing else. |
| **Does not amend** | **The promotion gate.** DEC-014, DEC-016, DEC-021 §6 and §9, EXP-022, EXP-024's rule, looks and seal. Canary trades are never a book and never count toward any gate, read or promotion. |

## 1. The owner's instruction (verbatim, 2026-10-08)

> "I've got a little over a quarter sol for you to mess around with to test. If that speeds things up, we can hopefully get to live trading with 1 sol in a couple days rather than waiting for simulation tests."

This is a **measurement canary**. It measures how the frozen H5 rule lands and exits on chain. It does not test whether H5 makes money, and its size could not tell.

## 2. What it is and is not

- **It is:** a live measurement of H5's execution: landing time from the trigger print, failure and expiry rates, the 15% guard's revert rate, and the precision of the timer exit at s0 + 330 s.
- **It is not:** gate evidence, a book, a paper pass, or a claim that H5 is positive. No report, note, Console entry or message may say a canary result shows a profit or an edge.
- **It does not change the gate.** The promotion gate, the EXP-024 rule and its thresholds are unchanged. CLAUDE.md and the owner's 10-08 mandate say nothing goes live before a book clears the promotion gate on both fail models. This DEC does not waive that for any size above the canary. See section 7.
- **It does not replace the read.** EXP-024's Look 1 and Look 2 stand.

## 3. When it starts

- **After EXP-024 Part 1 is merged.** The rule is then frozen by sha256 and the read tool has no overrides, so nothing the canary shows can change the read.
- **After every item in section 8.** The canary is built keyless first (a detector, an executor profile, an outcome-blind shadow of at least 24 h on live triggers with 0 build errors, and a md5 decision-equivalence replay against the frozen scorer on exploration or already-read September tape only, never on the fast-0 tip archive of 10-05..07).
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
4. **Divergence.** After at least 100 fills, the live−twin per-trade CI90 upper bound below −1 pp (live worse than its paper twin by more than 1 pp). The twin is an H5 outcome, so this is evaluated only once the relevant EXP-024 look has been read (section 6).
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

**EXP-024 outcome seal.** The canary trades the same pools the Look 1 window counts. Its fills, exits, P&L and wallet deltas are H5 outcomes under the EXP-024 seal (EXP-024 §3).
- The executor writes them only under `/var/lib/mal-live` (mal-live, 0700). The manager and builders read only allowlisted timing and count fields, as `tools/runner_timing_read.py` does: decision → send ms, send → landed ms and slots, the in-slot index, the outcome class (landed, failed, expired, guard revert) and the day's attempt count.
- Until Look 1 has been read (or has ended NOT_DECIDABLE with no outcome computed), nobody computes or prints a canary fill price, exit, per-trade P&L, day total or wallet delta, and the paper twin is not run.
- **Disclosed exposure.** The wallet is public on chain, so anyone can compute its P&L. This DEC cannot prevent that. If a canary outcome is seen by a person who can change the read's inputs, the Look 1 report records it. If the manager or a builder opens an outcome early, the EXP-024 read is reported compromised, and the DEC-016 Am.2 consequence applies.
- **Consequence for pace.** The twin and the live−twin comparison cannot be run before Look 1 is read (about 2026-10-16T07Z at the earliest). So the scale-up decision in section 7 cannot come earlier than that, unless the owner chooses to open the outcomes and accept a compromised EXP-024 read.

## 7. Scale-up to 1 SOL

The scale-up needs **the owner's OK and all** of these:
- live execution is consistent with the sim: the canary's landing p50 is at most 3.0 s, and the live−twin per-trade CI90 upper bound is not below −1 pp;
- the shadow run (the paper twin of the canary's decisions, priced by the frozen sim at 0.02 SOL) is not negative, meaning its mean per trade is not below zero;
- EXP-024 Look 1, if it has been read, is not negative: its deciding-cell flat and pressure means are not below zero.

**These are necessary and not sufficient.** The twin and Look 1 are H5 outcomes, and they open only as section 6 allows. They do not clear the promotion gate. CLAUDE.md and the 10-08 mandate say that nothing goes live before a book clears the gate on both fail models. This DEC does not waive that. If the owner wants a scale-up without a gate pass, that is a separate decision, recorded as an amendment that names the exception, and it must be labeled an unpromoted trial in every report. The manager asks the owner before any scale-up.

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
- A keyless outcome-blind shadow of at least 24 h with 0 build errors.
- The stop-probability simulation rerun at the canary's limits (section 4).
- No Jito, no Sender, no LaserStream, no paid RPC: **$0 extra** (owner plan 10-08). Public RPC may serve as an exit-only fallback sender.

## Open for the owner

1. **The 1 SOL scale-up route.** Section 7 lists necessary conditions only. Is a scale-up before a gate pass acceptable to you at all? If yes, it needs its own amendment.
2. **The seal and the pace.** Keeping the EXP-024 seal means the live−twin check waits for Look 1 (about 10-16T07Z). Opening the canary outcomes earlier is possible, and it compromises EXP-024's read.
3. **The limits.** Stake 0.02, 2 open, 30 a day, stops 0.08 and 0.12, and a 14-day duration are the manager's terms.

## Sources

- `/data/mal/hunt-1008/h5-work/LIVE-PLAN.md` (execution plan, risk limits, build estimates) and `PLAN.md` §5 and §7 (kill rules, stops).
- [DEC-019](DEC-019-execution-probe.md), [DEC-020](DEC-020-size-step-proposal.md), [DEC-021](DEC-021-champion-challenger.md) §7, [DEC-018](DEC-018-live-trial-readiness.md).
- [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) §9.
