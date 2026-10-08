# EXP-023: USDC-BOOST tripwire (plan)

| Field | Value |
| --- | --- |
| **Status** | **Plan and pre-registered trigger. Nothing has been read and no edge is claimed.** The tripwire is a monitor rule. The paper check below does not exist as code and runs only if the tripwire fires. |
| **Date** | 2026-10-08 |
| **Approval** | Owner-approved 2026-10-08 as task T1 of the edge scan. |
| **Source** | Judge report `/data/mal/audit-1008/work/edge-scan-1008/REPORT.md`, section T1 and candidates 4 and 5 (merged into T1). The figures come from that report's candidate-4 evidence and the red-team reviews. |
| **Where it runs** | `tools/pump_structure_monitor.py` (the daily structure monitor, #456), rule `usdc_boost_regime`. Tests: `tools/test_pump_structure_monitor.py`. |
| **Prior (judge, an estimate)** | 0.03 for candidate 4 and 0.03 for candidate 5 [inferred]. The judge says to keep it as a zero-cost monitor field and not to build USDC execution until the trigger fires. |

## 1. What this is for

Both positive books MAL has found ride pump.fun's own BOOST buyer. In USDC-quoted pools, during Aug to mid-Sep, almost nobody else traded in the first 5 minutes, so the BOOST push came through. That regime was gone by 09-19 and none was seen in October. This plan watches for it coming back, cheaply, and says in advance what happens if it does. It is **not** a claim that the regime is profitable.

## 2. The judge's figures, copied with their labels

All are [measured: usd.py/an5.py; exploration pool only] unless marked. Book: entry at the end of slot s0+5, exit at entry+300 s, 1.25% per leg + 1,010,000 lamports at 0.5 SOL, **no fail model**.

**Pre-declared rule U1** (written after seeing only the 09-05..09-12 V-map run):

| Block | n | Net | Days | Result |
| --- | --- | --- | --- | --- |
| Discovery, 09-04..09-14 | 246 (24.6/day) | mean +12.16%, trimmed +5.60%, day-CI90 lo +4.44% | 9/10 days positive | discovery |
| Confirmation, Aug 15-27 | 127 (9.8/day) | mean +73.1% (one pool +8,350%), trimmed +3.68%, day lo +0.35% | 10/13 days, ex-top-3 total +3.11 SOL | PASS |
| Confirmation, 09-19..09-24 | 13 (2.6/day) | mean -31.0%, day lo -48% | **0/5 days** | **FAIL** |

**U1 is therefore NOT confirmed.**

**Post-hoc split** (WHALE50-equivalent slot-0 buy >= 2.84 V). **It was chosen after U1 failed.** Treat it as a description of what the split did, not as evidence for this plan.

| Block | n | Net | Days |
| --- | --- | --- | --- |
| Aug | 84 | +5.81% net | 13/13 days, day lo +4.77% |
| 09-04..14 | 237 | +5.11% net | 10/10 days, day lo +5.02% |
| 09-19..24 | 5 | +0.94% | 1/3 days |

- Per-pool net in the split is tight (p25 +4.0%, p50 +5.1%, p75 +6.1%) [measured].
- Latency does not bind: 09-04..14 trimmed net k2 +5.60%, k5 +5.60%, k10 +5.37%, k20 +5.40%, k40 +5.35% [measured].
- The late block's 5 pools are "mostly a different animal" (slot-0/V of 1,976, 1,960 and 57.6; 94-1,923 traders in the first 300 s against 8-10 before). Only one pool in 6 days matched the old pattern (B58nWY, 09-21, 18 traders, +8.8%) [measured: u_all.parquet, red team].
- Custom-quote pools ("other" V class): 09-05..14 n 156, trimmed +8.9%; 09-19..24 n 89, trimmed -5.8%, ex-top-3 mean -6.7%. "Not robust."
- The USDC flow was one operator family's self-dealing bundle that "appears and vanishes on their schedule" [measured, red team]. It did not start or stop with a program upgrade (USDC pairs live since 05-21, BOOST since 07-21), so a program-diff watch would not have fired on it.

**October** [measured: public RPC, 0 Helius]:

- 10-08 12:29 to 15:41Z: 4,000 crank signatures, every 35th taken, 109 transactions, about 77 distinct pools: 73 WSOL, 4 other-quote, **0 USDC**. At the 09-04..14 share (about 3.6% of BOOST pools) about 2.7 would be expected, so P(0 | regime present) is about 0.06.
- Earlier sample, 10-08 14:45-15:29Z: 40 sampled: 37 WSOL, 3 pump-token-quoted, 0 USDC.

## 3. What the monitor measures

Each daily run reads the 20 newest settled graduations (the existing sample, 0 extra calls) plus up to 40 older ones (`--quote-mix-extra`; about 42 extra RPC calls: 199 total against 157 in two live public-RPC runs on 2026-10-08 with and without it, plus 3 `getAccountInfo` calls for the program hashes in both). For each migrate tx it keeps only the quote mint class (WSOL, USDC `EPjFWdd5...Dt1v`, other) and whether the tx carries `InitBoost`. A **USDC-quoted BOOST graduation** is a migrate tx with a USDC quote mint and `InitBoost`. Counts only are written: no mint, pool or signature.

The daily statistic is

`usdc_boost_per_day_est = (USDC-quoted BOOST graduations in the sample) x 86,400 / (sample span in seconds)`

and is null unless the sample has at least 20 graduations spanning at least 300 s.

Limits, stated up front:

- **It is a rate at run time, not a count of a UTC day.** A full day is about 1,200 graduations (1,168 to 1,492 a day on 10-01..07 [measured, g_october inv B F8]); one `getTransaction` each is far beyond the public-RPC budget. The run is at about 06:41Z every day, so the time-of-day effect is the same on every run.
- **The sample is about one hour of flow.** With 60 graduations over about 4,100 to 4,300 s, a single USDC BOOST graduation already estimates about 20 a day, so in practice a day qualifies when the sample holds at least one. The "10 a day" bar is a rate bar, not a count bar.
- **Detector power is low at the trigger's own level** (computed below).
- **Not recorded:** slot-0 buy relative to V and the number of distinct traders in the first 300 s, by quote class. The migrate-tx sample does not contain them. They need each pool's own tape (a signature list and transactions per pool) and trade sizes the monitor never reads. Skipped, not approximated.
- The judge's identification ("BOOST wallet's own total 2.50e9-2.53e9 raw") is not used. The monitor uses `InitBoost` plus the quote mint in the migrate event, which is what it already decodes.

### Power of the pre-registered rule [computed; not measured]

Assumptions: Poisson arrivals of USDC BOOST graduations, 1,200 graduations a day, a 60-graduation sample spanning 4,320 s (so a day qualifies when the sample holds at least one), 61 daily runs from 2026-10-09 to the kill date.

| True USDC BOOST graduations/day | P(a day qualifies) | P(5 in a row at a given start) | P(fires within 61 runs) |
| --- | --- | --- | --- |
| 1 | 0.049 | 0.0000003 | about 0 |
| 2.6 (the 09-19..24 rate) | 0.122 | 0.00003 | 0.001 |
| 5 | 0.221 | 0.0005 | 0.023 |
| 9.8 (the Aug confirmation rate) | 0.387 | 0.0087 | 0.272 |
| 10 (the trigger's bar) | 0.393 | 0.0094 | 0.288 |
| 24.6 (the 09-04..14 discovery rate) | 0.708 | 0.178 | 0.991 |
| 50 | 0.918 | 0.652 | 1.000 |
| 100 | 0.993 | 0.967 | 1.000 |

So the tripwire catches a return at about the 09-04..14 rate (24.6 a day) or higher, almost surely within the window. At the bar itself (10 a day) it fires within the window only about 29% of the time. **"Not fired by 2026-12-08" is weak evidence of absence below about 20 a day.** It is not evidence that a smaller regime does not exist.

## 4. Trigger (pre-registered)

`usdc_boost_regime` is a WARN in the daily monitor record. It fires when `usdc_boost_per_day_est >= 10` on **5 consecutive daily runs**, one per UTC date, read from `daily.jsonl`. Exact rules, tested in `tools/test_pump_structure_monitor.py`:

- A date with no record ends the streak (a gap resets it).
- A date whose record has no estimate (sample too small or too short, quote-mix stage failed, or a record from before this rule existed) ends the streak.
- A date below 10.0 ends the streak. Exactly 10.0 counts.
- One value per UTC date: the newest record of that date. A re-run on the same date replaces the earlier value and is not added to it.
- Records dated after the run are ignored.
- It never halts. It has no side effect.

## 5. If it fires

1. The manager opens a **new pre-registered EXP** with its **own fresh forward window**. The window starts after the run that fired. Nothing is tuned on past data: not on Aug or Sep, not on the post-hoc whale split, not on the 09-19..24 block.
2. The new EXP fixes before any read: the USDC fee tier and costs (the judge's 1.25% per leg is [inferred] for USDC, "not measured"), the fail model, the entry rule, and whether a whale-type filter is tested at all. A filter is allowed only if it is written down in the new EXP and tested on its fresh window.
3. The **report-only paper check** inside that EXP:
   - book: k5 entry (the end of slot s0+5) to a 300 s cap, on every USDC-quoted BOOST pool;
   - run **only after the trigger** and **only on post-trigger hours**; earlier hours are not scored;
   - bar: net >= +2% and >= 4 of 5 post-trigger UTC days positive;
   - this is a look, not the promotion gate. Passing it earns only the next step: the gate in `CLAUDE.md` (>= 100 out-of-sample trades, >= 5 distinct UTC days with a majority positive, lower 90% CI bound > 0, positive after removing the top 3 trades, under both fail models) and then the owner's approval.
4. It is scored from the walk-2 tape and the A8 decoder when they are live, not from new Helius spend. Any credit use is decided in the new EXP.

## 6. Kill

- **Kill date: 2026-12-08.** If `usdc_boost_regime` has not fired by then, EXP-023 is closed as NOT FIRED. The extra quote-mix sampling (`--quote-mix-extra`) is then set to 0 by the manager. This closing step is this plan's own wording of the judge's "stop if nothing actionable appears by 2026-12-08".
- The program-hash and docs watches stay: the judge values them as protection for CAP-PICK, not as a profit candidate.
- **No USDC execution is built unless the tripwire fires and the new EXP's paper check passes.** No USDC float, no USDC fee tier work, no decoder work for USDC execution before that.
- Cost while running: about 15 minutes of monitor runtime a day, 0 Helius credits, no paid data.

## 7. What this plan does not claim

- It does not say the USDC regime was profitable. U1 failed its confirmation (n 13, -31.0%, 0/5 days), and the +5.81% / +5.11% split was picked after that failure.
- It does not say the tripwire is likely to fire. The judge's prior is 0.03.
- A fire is a prompt to run a new pre-registered test, nothing more.
