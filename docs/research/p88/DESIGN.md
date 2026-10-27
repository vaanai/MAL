# KNN-C: a cost-gated numeric kNN ranker as a forward paper book (design v0, 2026-10-27)

Desk design, lane P88 (manager17). Records only. **Nothing here says KNN-C makes money.** KNN-C has 0 trades, no sha, no clock. No book has
cleared the promotion gate. Paper only. No tape, data dir, ledger, card, job output or sealed path was opened to write this. No price, no
engine run, no outcome was computed. Every rate, sd and power figure below is arithmetic or an assumption unless a committed file is named as
its source. Time from `date -u` on mal-research-0: 2026-10-27T15:00Z.

**Precondition.** This design is used only if the pending quant-proof leakage audit of P86 (PR #1043) passes. Section 7.1 lists one leak this
lane found in P86's memory rule; the audit should rule on it. If the audit fails P86, KNN-C is not built.

Sources read: `docs/research/p86/PREREG.md`, `REPORT.md`, `p86_analyze_output.txt`, `p86_counts.txt` and the scoring lines of `p86_analyze.py`
(branch `claude/p86-knn-baseline`, head `05e1f1b83292218b0afe8c03c4dc0c24eb9cf71f`); `docs/research/p78/REPORT.md` and `costmap.json` (main);
`docs/research/p87/REPORT.md` and `p87_count_output.txt` (branch `claude/p87-b7-pullback`, head `82a65c88`); DEC-031 §1, §2, Amendments 2
(rent and scope, grep), 3, 4, 6 and 15; `docs/research/hv7/DESIGN.md`; the function lists of `tools/hv7_runner.py` and `tools/arena_paper.py`
(grep only). Seals: `/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md` (sha256 `823cc062...`). The P86, P78 and P87 figures quoted here
are in-sample on exploration dates that at least 14 families have read.

## 0. What KNN-C is, and the expected result

Each hour, a fixed tape-only screen yields up to 8 PumpSwap candidates (P86's HV7-like set). A frozen numeric kNN (P86: k = 25, 10 causal
features) scores each. The book buys the top-scored pool **only if** its predicted return clears that pool's fee-band cost floor plus a margin
m fixed before the clock. Otherwise it abstains that hour. Two placebo arms price the same hours.

**Pre-registered expectation for L's absolute gate read: FAIL.** P86's ungated top pick lost -1.128 pp (flat) and -0.656 pp (pressure) per
trade in-sample at the 55,000-lamport P55 stack, which is cheaper than the arena's deciding stack by about 1.128 pp at 0.25 SOL (section 3.2).
The cost gate may lift the mean, and nothing measured says by how much. KNN-C is worth running because it gives (a) a forward, out-of-sample
test of P86's rank IC (0.27814 in-sample) on November tape, and (b) a forward test of whether "predicted return above the cost floor" selects
hours that clear costs. Both are paired against placebos on the same hours. A FAIL with L - U > 0 is the most likely informative result.

## 1. Universe and period

### 1.1 Period
Hourly. Decision instant `t_d = H:02:00.000Z`, over tape prints with receive time `< t_d` (P86 uses block_time; section 6.2 fixes the arena
mapping). Fixed entry instant for every arm `t_e = H:02:45.000Z`, as HV7. No LLM call sits between them; the 45 s keep KNN-C's latency legs
comparable with HV7's, and the 1.9 s leg starts at `t_e` (DEC-031 §1.4 "latency start" maps the decision record at `t_e` to the tape's slot
clock). Exit: P86's label rule, a plain time exit. The sell is triggered by the first print at or after `t_d + 60 min`, and the 2-slot exit lag
applies. Exits run before entries at the same instant. One position per arm per hour; no cooldown, so the same pool can be bought in
consecutive hours (disclosed; the contrasts use daily sums).

### 1.2 Screen (frozen from P86 PREREG "Candidate set", unchanged)
At `t_d`, over prints before `t_d`, per canonical pool:
- WSOL quote; V in [17.5e9, 17.7e9] lamports inclusive (V missing or out of band: not a candidate). This is also DEC-031 Amendment 2 item 6's
  V-band check, so KNN-C is rent-0-covered (section 5.1);
- age since the pool's first PumpSwap print in the arena tape: 2 h to 7 d inclusive;
- real quote reserve at the last print >= 30 SOL;
- in the last 15 min: >= 20 prints and >= 8 distinct traders; last print within 120 s of `t_d`;
- `vol_60m` (buy + sell SOL, last 60 min) >= 10 SOL;
- round-trip impact at 0.25 SOL <= 3.0% (`hv4_cards.impact_rt` on quote + V);
- rows with a 64-ones signature or a `hv6_phantom.PHANTOM_SIGNERS` trader are dropped (P86's phantom rule).

Candidates = survivors ordered by `vol_60m` descending (ties by pool id), top N = 8. An hour with fewer than 3 is **not counted** in any arm
(HV7's N_MIN = 3).

**Added, one fail-closed filter: market cap <= 5,000,000 SOL** at the last print (supply-1B formula, as P78 and `tools/paper_curve_math`).
Count basis: P87's B7 funnel dropped 693 of 28,712 tradeable B7 pool-hours (2.41%) at this cap (`p87_count_output.txt`), and P78 found pools
above 10M SOL that may be non-canonical or have a supply other than 1B (P78 "B7 composition"). A pool whose supply is not 1B makes the fee-band
lookup in the gate wrong, so the cap protects the gate. On P86's V-band set it is expected to bind rarely (not counted; P0 counts it, section 4.5).

### 1.3 Decision: no B4-B7 restriction, no 6 h range filter

KNN-C does **not** restrict the universe to fee bands B4-B7 and does **not** add the 6 h range >= 2% filter. Reasons, from counts and costs only:

1. **The frozen model's evidence is for P86's set.** P86's IC (0.27814) and its memory (4,600 priced candidates) come from the unrestricted
   V-band screen. P86 does not report the band mix of its candidates, so no committed count says how many memory points or candidate hours
   survive a B4-B7 cut. Restricting would freeze a model on a population that its evidence does not describe, with a memory of unknown size
   (possibly too few same-band neighbours for k = 25). Per P86's own report, much of the IC is plausibly liquidity, fee-tier and concentration
   structure. A band cut removes part of that by construction, so the IC would not carry over.
2. **The gate does the band work per pool.** The floor in the gate is the pool's own band floor (section 3). A B1 pool must clear a floor about
   3.3 times a B7 pool's (P78 floor at 0.25 SOL: 3.0438% against 0.6479%), so high-fee pools are traded only when predicted to clear their own
   cost. A universe cut would be a second, cruder version of the same filter.
3. **The range filter has no count that applies here.** Its only count, P87's, is vacuous: 0 signals dropped, because P87's dip rule implies a
   range above 2% (`p87_count_output.txt`). What it would remove from an hourly top-8-by-`vol_60m` set is not counted anywhere. The pegged-pool
   problem it targets is P78's: 34.5% of tradeable B7 pool-hours have a median 2-slot move below 0.001%. P86's screen already requires 20 prints,
   8 distinct traders and 10 SOL of volume in the last 15 or 60 min, and a pegged pool is expected to score near its memory neighbours'
   (small) mean. Whether pegged pools enter the top 8 is **not measured**. P0 reports the 6 h range of each counted top pick, report-only
   (section 4.5).
4. **Capacity.** P78 counts tradeable pool-hours/day of 757.3 (B4), 240.7 (B5), 150.0 (B6) and 990.1 (B7), against 1,328 (B1) and 1,579 (B2);
   P86 finds about 110 screen survivors per hour (`p86_counts.txt`: mean 109.99, median 118). The full screen keeps the hourly set full
   (620 of 621 P86 hours had >= 3 survivors). A band cut has no count that shows it stays full on V-band pools.

Not chosen, recorded: a B4-B7 + range variant would be a second book (+1 in N, a sibling under Am.3 8(e)), and its memory would have to be
re-screened first. It is not registered here. It can come back only as a new design with its own counts.

## 2. Model (frozen from P86)

- **Code.** Ported verbatim in logic from these P86 files (branch `claude/p86-knn-baseline` at `05e1f1b83292218b0afe8c03c4dc0c24eb9cf71f`):
  - `docs/research/p86/p86_analyze.py`, sha256 `c7b9c0737456b0d1cb5dcc58995e07f3cad12b789d93110d833f3a6377a54956` (kNN scoring:
    `K = 25`, `FEATS`, z-score, Euclidean, `argsort(kind="stable")`);
  - `docs/research/p86/p86_build.py`, sha256 `db335be9cfdddab2687d10f694415cfd39fe13bc57f91204d3c0c777db74aa75` (screen and the 10 features);
  - `docs/research/p86/p86_label.py`, sha256 `0ac964e814222e5aae061cf144d80e0f61e19126a016eb961e33c9118c6458b0` (label).

  The port is a new module (section 6). An md5 decision-equivalence proof against these files, on a pinned replay, is owed before the clock
  (section 6.3). If #1043 changes these files before merge, the hashes here are re-pinned in the records PR and the change is disclosed.
- **Features (10, causal, prints before `t_d`):** `log_vol60`, `ret_5m`, `ret_15m`, `ret_60m`, `flow60`, `log_signers15`, `log_age`,
  `top3_60m`, `close_vs_hi` (6 h high), `log_qeff` (P86 PREREG "Features"). A candidate with an undefined feature is dropped and counted.
- **Score.** `yhat` = mean label of the k = 25 nearest memory points, Euclidean distance on z-scored features, ties by memory order (stable).
  The label is P86's: no-fail net P&L / stake at 0.25 SOL, P55 pricing (fee tier on quote + V and base, 2 x 55,000 lamports), entry at
  `slot_d + 2`, exit trigger `t_d + 60 min`, exit lag 2, exit-print window 1,800 s (P86 Amendment 1). A candidate whose exit is unpriced in the
  window is dropped from memory and counted (P86's rule; its bias is section 7.2).
- **Standardisation rule (frozen).** For each memory snapshot `M_D` (below): mu and sd per feature over all points of `M_D`, population sd
  (`ddof = 0`); sd = 0 is replaced by 1. Memory points and that day's candidates are both standardised with that day's (mu, sd). Nothing is
  fitted on the scored day.
- **Memory.**
  - At the clock: every priced P86 candidate from all three exploration blocks (explore-0814, fresh-0903, exp011-0909), 4,600 points in
    P86's hours with >= 3 candidates (`p86_analyze_output.txt`: 4,600 candidates, 618 hours). At registration all three blocks lie in the
    past, so using fresh-0903 and exp011-0909 labels as memory is not a test read. They stop being any kind of test for P86 once they are in
    KNN-C's memory, and this is disclosed.
  - Rolling: from the clock on, the book's own candidates of the arena tape, labelled by the frozen P86 label (ported to the arena tape
    format), are added day by day.
  - **Snapshot rule (stricter than P86, section 7.1).** `M_D`, used for every decision with `t_d` in `[D 00:00Z, D+1 00:00Z)`, holds exactly
    the points whose exit print's receive time is before `D 00:00:00Z`. A candidate of `D-1 23:02` whose exit resolves after midnight enters
    `M_{D+1}`. Unresolved points wait. Points dropped as unpriced never enter. `M_D` is written once at `D 00:00:00Z`, and its sha256 is
    notebooked before the `D 00:02` decision (DEC-031 §1.3: a version uses only outcomes whose exit is before its write instant). No arena
    day before the clock is ever labelled (arena tape: counts and creates only); the first arena points enter `M_{T0+1}`.
  - No weighting, no forgetting, no cap: the plain P86 rule. Memory grows by about 24 x 7.2 = 173 points a day (P86's mean N 7.2 per hour;
    a planning number, not a count of arena hours).

## 3. Cost-aware gate

### 3.1 The trap: the label is already net
P86's `yhat` is a **net** return. It already carries the pool's own fee tier on both legs, its own impact and 2 x 55,000 lamports, all at
P55 costs. Comparing `yhat` with P78's full round-trip floor would charge fees twice. The gate therefore charges only the cost that the
arena's deciding stack adds **on top of** P86's label. That is written two ways, which are the same inequality:

- with a predicted gross return `ghat = yhat + c_b`, where `c_b` = P78 round-trip fee % of band b + 2 x 55,000 / stake (what the label
  deducted, at the band's median, own impact excluded as in P78): trade iff `ghat > F_b + m`, with `F_b` = P78 `floor_025_pct(b)` +
  `Delta_stack`;
- equivalently, trade iff **`yhat > G_b + m`**, with `G_b = F_b - c_b = lambda_b + Delta_stack`.

The second form is the one implemented (it needs no band-median approximation of the pool's own fee, which `yhat` already has exactly).

### 3.2 The terms (constants pinned now; 0.25 SOL, the deciding stake)
- `Delta_stack`, the arena's deciding stack over P55 (DEC-031 §1.4 and Amendment 2):
  - sends: 2 x (505,000 - 55,000) = 900,000 lamports = 0.36 pp;
  - E1 floor: max(haircut, 384,022 lamports per 0.05 SOL). At 0.25 SOL the floor is 1,920,110 lamports = 0.768044 pp. The haircut
    (+26.08 / -16 bps, about 42.08 bps = 0.4208 pp) is smaller, so the floor binds: 0.768044 pp;
  - rent: 0 (Amendment 2 covers KNN-C, section 5.1). The rent-charged leg, +2,039,280 lamports = 0.815712 pp, is printed beside every
    deciding figure. It does not enter the gate.
  - **`Delta_stack` = 1.128044 pp.** (Any numeric match with P86's flat book mean of -1.128 pp is a coincidence. That mean did not set
    this figure.)
- `lambda_b`, P78's median 2-slot absolute move for band b (`costmap.json` `lag2_med_pct`). It stands in for the extra latency of the 1.9 s
  leg over P86's `slot_d + 2` entry. It is a cost proxy only; the sign of that move for this rule is not measured.

| band (mcap SOL) | B0 <420 | B1 420-1470 | B2 1470-9820 | B3 9820-24560 | B4 24560-58940 | B5 58940-78590 | B6 78590-98240 | B7 >=98240 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `lambda_b` (pp) | 0.8705281583 | 0.6142356501 | 0.221775263 | 0.0668578177 | 0.0477710366 | 0.0315881205 | 0.0266665094 | 0.0047746687 |
| `G_b` = `lambda_b` + 1.128044 (pp) | 1.9985721583 | 1.7422796501 | 1.349819263 | 1.1949018177 | 1.1758150366 | 1.1596321205 | 1.1547105094 | 1.1328186687 |
| P78 full floor at 0.25 SOL, for reference (pp) | 3.3989031583 | 3.0438356501 | 2.354750263 | 1.9027578177 | 1.4868710366 | 1.0233318705 | 0.8192602594 | 0.6478746687 |

The band is the pool's market cap at its last print before `t_d` (supply-1B formula). `costmap.json` sha256
`581593ec5254d93099a7d6a5c599f751d2713fd714faee546c5ec7c31aa1d5dd` (main). The constants are copied into the module and are never
recomputed on arena data.

### 3.3 Margin m (rule fixed now; number computed before P0, pinned before registration)
`m` cannot be a number in this desk lane: P86's committed outputs do not include the distribution of top scores, and computing it is a run.
The rule below is fixed now, before any such number exists. It uses **no fresh-0903 or exp011-0909 label**.

1. **Pinning set.** The 285 P86 test hours (fresh-0903 and exp011-0909, hours with >= 3 candidates). Their candidates' features are loaded;
   their label column is not loaded (the tool refuses it, section 6.1 K0).
2. **Pinning memory.** Explore-0814 only (the 2,549 P86 explore points with their labels), as one fixed memory, standardised by section 2's
   rule over that memory.
3. For each pinning hour h: `e_h = yhat_top,h - G_{b(top,h)}`, where `top` is the hour's highest-`yhat` candidate (ties by screen order).
4. **`m = max(0, median_h e_h)`**, written to 6 decimal places in pp, together with the count of hours with `e_h > m` and the tool's output
   sha256, in a MiScusi notebook entry before P0 runs. It goes into the `bots.json` line before the clock.

Why the median (q* = 0.5): the gate should abstain in about half the hours, so it actually selects, while keeping about 12 attempts a day,
so that B comes inside H and the contrast keeps >= 8 days. Without a margin it would barely bind: 261 of 285 P86 hours already had a top score
above 0 (a count). This is a **throughput rule, not a calibrated break-even.** `yhat_top` is the max of up to 8 noisy kNN means, so it is
biased upward, and its memory drops dead pools (section 7.2). The gate states a preference ordering over hours. It does not claim that an
open hour clears costs. Whether open hours do better is measured forward, report-only (section 4.3).

Disclosed: this lane's author has seen P86's REPORT (book means, the 261/285 count). The median rule was chosen to fix throughput and was not
tuned to any outcome. No alternative quantile was evaluated, and none will be.

### 3.4 Decision per hour
At `t_d`: build the screen (section 1.2). If fewer than 3 candidates, the hour is not counted. Otherwise score all N with `M_D`. `top` =
argmax `yhat` (ties by screen order). **Gate open** iff `yhat_top > G_{b(top)} + m`, strictly. If open, L buys `top` at `t_e`; if closed, L
logs a `closed` row (an abstention, not an attempt). The decision row carries: `t_d`, the N candidate ids in screen order, every `yhat`, `b`,
`G_b`, `m`, gate state, the `M_D` sha256 and the code sha. It carries no price after `t_d`.

## 4. Arms and reads

### 4.1 Arms (3 bot ids; one promotable)

| Arm | `bot_id` | Hours | Pick | Promotable |
|---|---|---|---|---|
| **L** | `knnc0-L` | gate-open hours | `top` (section 3.4) | yes |
| **U** | `knnc0-U` | **every** counted hour (open and closed) | all N candidates of the hour, 0.25 SOL each; per-hour value = mean over N | no (null) |
| **R** | `knnc0-R` | every counted hour | one uniform draw from **all screen survivors** of the hour (not only the top 8): index `int(sha256("knnc-R|" + t_d_iso + "|" + ",".join(survivor_ids_in_screen_order)).hexdigest(), 16) mod n_survivors` | no (placebo) |

- **L - U on the gated set** is the primary contrast: in each gate-open hour, L's pick minus the mean of the same hour's N candidates. That is
  "uniform random over the same gated set" with no draw noise (HV7 section 3, "Why U").
- U also prices every closed hour. This gives two report-only figures without another `bot_id`: the gate's value (mean of U's `top` row in open
  hours against closed hours) and the rank IC of `yhat` against U-priced returns.
- R asks whether trading this screen at all is worse than a random survivor. R - U (survivors against top-8) is report-only.
- No S arm. P86 found the `vol_60m` leader the worst arm (in-sample), and S adds a cell without a question KNN-C needs.
- **Cross-arm pricing independence** (HV7 C2's hard test): byte-identical entry and exit prices, every leg and both fail models, when L, U
  and R buy the same pool at the same `t_e`. If the test fails, the design is invalid and the book is not registered.
- U holds up to 8 open positions for one `bot_id` per hour. Reuse HV7's tested solution (`hv7_runner.py` line 525 passes `u_set` as a list of
  picks). If that path does not apply, use shadow ids `knnc0-U1..U8` (same arithmetic; N counts them as one cell, disclosed).

### 4.2 Gate read at B (L only)
Unchanged DEC-031 §2 with Amendments 4 (item 2) and 6:
- **B** = the first 00:00Z boundary with >= 100 counted L attempts (gate-open entries, guarded reverts included) on >= 5 UTC dates, counted by
  the outcome-blind decision-timestamp tool (`knnc_count.py`), with attempts in Am.6-excluded hours dropped. The read covers the attempts
  decided before B, once all have exited (61 min plus the exit window).
- **Test:** the CLAUDE.md gate: >= 100 out-of-sample trades; >= 5 distinct UTC days with a majority positive; CI90 lower bound of mean SOL per
  trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile); total positive after removing the top 3 trades; under both the flat 15% and the
  pressure-fail model at slope scale 1.
- **Deciding cell:** the 1.9 s leg at 0.25 SOL, rent 0 (Amendment 2), all four sub-cells (flat and pressure, each under strand rules sc and
  total; Am.3 8(a)). Printed beside it and never deciding: the rent-charged leg, the 1.3 s leg, stakes 0.5 and 1.0, a day-block bootstrap,
  and ex-best-day. Ex-best-day > 0 is required before any canary proposal.
- **Horizon.** `H = min(90, max(14, ceil(150 / rate)))` days, with `rate` = P0's gate-open hours a day (Am.4 item 2.2), pinned in `bots.json`
  and never extended. No B by T0 + H: NOT_DECIDABLE, final.
- Caps: §1.4's 5% feed-gap cap; Am.6's 10% excluded-hour cap.

### 4.3 L - U contrast, read once at T0 + H
- **Window:** all gate-open hours with `t_e` in `[T0, T0 + H)`, read once after the last exit. It is not read at B. As HV7 notes, an exact
  sign-flip on 5 days has a minimum p of 2^-5 = 0.03125, above 0.025, so a read at B could never pass.
- **Test:** per UTC day, the sum over gate-open hours of (L pick - mean of the hour's N) in SOL at 0.25 SOL, the deciding leg; exact one-sided
  sign-flip on the daily sums; **p <= 0.025 in each of the four sub-cells**; **>= 8 non-zero days**, else NOT_DECIDABLE (`contrast_days`).
- **Secondary, report-only, Holm-ranked:** L - R; R - U; the gate's value (U's `top` row, open against closed hours); rank IC of `yhat` against
  U-priced 60-min returns (mean, t, day-block t, as P86 printed them).
- **A contrast pass is not a promotion.** L - U > 0 with L's gate FAIL reads: "the frozen kNN selects better than random from this screen, and
  the screen plus costs lose more than the selection adds." It licenses a new design and never a canary.
- **MDE line (arithmetic, not a measurement; QP to replace it with an exact sign-flip simulation, as for HV7).** Assumptions: per-trade sd s
  of the 60-min net return 8% or 12% (HV7's assumption; P86 does not report it); sd(L - U) = 1.06 s at N = 8 (zero cross-pool correlation);
  12 open hours a day; D = 14 days; normal approximation to a one-sided test at 0.025 with 80% power: MDE = 2.802 x 1.06 s / sqrt(12 x 14)
  = 0.229 s, i.e. **a per-trade lift of about 1.83 pp (s = 8%) or 2.75 pp (s = 12%)**, or a rank IC of about 0.229 / 1.4264 = **0.16**
  (E[max of 8 N(0,1)] = 1.4264, as HV7). The exact sign-flip test on 14 daily sums is less efficient than this, so the true MDE is larger.
  P86's in-sample IC (0.27814) is above it. A November IC below about 0.16 will likely read "not significant". **A non-pass is reported as
  "no skill detected; MDE IC about 0.16", never as "no skill".**

### 4.4 E1 early halt (outcome-blind; a host-side MiScusi job scheduled before T0)
At T0 + 2 d 00:00Z, `knnc_count.py` runs once and its output sha256 is notebooked. `A_L` = counted L attempts in `[T0, T0 + 2 d)`; `C` = counted
hours (open + closed).
- `A_L < 12` (6 a day; 100 attempts in 14 days need 7.2 a day): every KNN-C gate read and contrast is NOT_DECIDABLE (`E1_entry_rate`). This is
  written once, before any KNN-C outcome is opened. Entries halt in all arms at the next 00:00Z, and code exits run to completion.
- `C < 24` (fewer than half the hours have >= 3 candidates): the same, with reason `E1_screen_starved`. Since KNN-C has no LLM, an E1 halt
  means a screen, feed or memory fault, or a gate closed by its own memory.
- Report-only: the gate-open share `A_L / C`, and the daily `M_D` sha256 list (all present or not).
- E1 never yields PASS or FAIL. No other count is read before B. The halt path is drilled at the final sha on synthetic tape before T0
  (HV6/HV7 E1 drill pattern); without the drill, E1 is declaration-only.
- Bias note (as Am.15 item 8): the gate depends on memory labels, so `A_L` is a function of earlier outcomes through `M_D`. E1 and B move only
  on decision-side counts. They can end a read for lack of attempts, and cannot create a pass.

### 4.5 Expected trades per day (counts only; P0 replaces this)
- Hours: P86 had >= 3 survivors in 620 of 621 decision hours (`p86_counts.txt`), so counted hours are at most about 24 a day in Aug/Sep.
- Gate share: on the pinning set the median rule opens at most 50% of hours by construction (fewer if `m = 0` binds). The pinning memory
  (explore-only, 2,549 points) is smaller than the live memory (4,600 + arena points), so the live share differs and is not predicted here.
- Upper reference: with no cost term and no margin, 261 of 285 P86 test hours (91.6%) had a top score above 0. At 21.92 hours/day
  (`p86_analyze_output.txt`) that is at most 20.07 a day. KNN-C's gate is stricter, since `G_b` > 0.
- **Planning figure: about 12 L attempts a day (24 x 0.5).** Then B is about T0 + 9 d and H = 14. At 7.2 a day, H = max(14, ceil(150 / 7.2) =
  21) = 21.
- **P0 (Am.3 item 1 as replaced by Am.4 item 2; structure-only).** The P0 rule text goes in a notebook before the job runs. The P0 tool is the
  KNN-C decision path at the registration-candidate sha, on >= 3 full closed arena days before registration, with memory = the 4,600
  exploration points only. No arena label, price, engine run or exit is involved. Outputs per UTC day: counted hours, gate-open hours, screen
  survivors (median), and the mcap-cap drop count, plus feed gap counts. Report-only: the band mix of the top picks and the 6 h range of the
  top pick (min, median). Not registered if: the average is below 2.0 gate-open hours a day or any day has 0 (Am.4 2.1); a hard gap or
  `data_loss` > 1 h (NOT_DECIDABLE); or a KNN-C kill line (below) fires.
- **KNN-C kill lines (before the clock, structure only):** P0 gate-open rate < 7.2 a day, so that n = 100 is not reachable in 14 days; this
  is stricter than Am.4's floor, and KNN-C stops instead of running a 21-90 day book. Also: the md5 proof fails, the pricing-independence test
  fails, the QP audit of P86 is not OK, or any seal question is open.

## 5. DEC-031 fit, seals, multiplicity

### 5.1 Fit
- **Rent:** Amendment 2 covers KNN-C if its registration and clock follow that merge (they do). The V-band check of Am.2 item 6 is the screen's
  first line (section 1.2), named as `path:line@frozen_sha` in the records PR. The host passes `rent=0`. `StackConfig` is pinned: `rent` 0,
  `e1_floor_per_005` 384022, `strand_rule` "both", `k_rounding`, `feed_delay_slots`, `gap_slots` 150, `gap_silence_ms` 60000 (Am.2 item 8).
- **Am.3:** rolling registration; clock = the first 00:00Z after the registration record (and after the smoke, Am.6 1.4); P0 (as above);
  one process per `frozen_sha`; automatic confirmation of any screen pass at 0.05/c; the R8 multiplicity block; no second frozen copy.
  Am.3 item 4 (October screening) is **not used**: no rule is priced on any arena hour before the clock.
- **Am.6:** smoke of >= 2 h on the real feed at the `frozen_sha` (blocking list (a)-(e); (f) does not apply, no LLM); per-hour integrity
  tool and thresholds pinned (price-path: arena feed gaps; decision-input: tape staleness > 120 s at `t_d`, stamped by the feed); 10% cap.
- **§1.3 (self-improving book):** the rolling memory makes KNN-C a self-updating book. Its frozen part is the code, k, the features, the label
  rule, the snapshot rule and `m`. Each `M_D` is hashed and logged before its first use, and uses only exits before its write instant. Nothing
  else may write memory.

### 5.2 Seals (SEALS-BRIEF 823cc062)
- No HV4, Q50, HV6 or HV7 output, ledger, decision row or job status is read by any KNN-C tool. U and R are KNN-C's own bot ids.
- **EXP-024 Look-2** (to its end state, first 2026-11-14T00:00Z): KNN-C's universe is V-range pools, but only at age >= 2 h, so no outcome
  is ever in a pool's first 360 s. QP to confirm.
- **EXP-025 C1-NF:** KNN-C is not C1-NF's grid, exit or labels (a kNN on 10 tape fields, a 60-min time exit). QP to confirm under Am.3 8(b).
- **Arena tape:** before the clock, counts only: P0, and the Am.6 1.3 smoke, whose outputs are never opened for price, fill, exit or P&L
  (QP to confirm that no §3.5 or §3.7 seal covers the smoke). Arena-day labels exist only from T0 on and only inside the book's own memory and ledger.
- **DBC, LaunchLab, FLOOR G1, meta-capture, forward-1016:** not touched. KNN-C is PumpSwap-only on the arena tape.
- **Walk-2 information (DEC-030 §2 R1):** P0 counts on arena days, so KNN-C is an arena-only book forever.

### 5.3 Multiplicity and siblings
- KNN-C adds 3 `bot_id`s (`knnc0-L`, `-U`, `-R`; +8 if the U shadow ids are needed, disclosed as one cell) to N, with one promotable cell.
  Each read carries the Am.3 item 3 block: N counted from the notebook copies at that read (HV4's 9, Q50, the H5 books, the hunt23/cohort books,
  HV6's and HV7's arms, stopped and NOT_DECIDABLE books included), the plain line 1 - 0.95^N, and the report-only p (10,000 draws, seed 1, the
  larger of the two fail models) with its Holm rank.
- **Siblings (Am.3 8(e)).** HV7 runs a near-identical screen (HV6 cards, top 8 by `f.vol_60m`, 2 h-7 d) on overlapping days. KNN-C's screen is
  P86's tape-only version (no chain-fact vetoes), so the two candidate sets overlap but are not equal. An HV7 pass and a KNN-C pass on the same
  days are not independent confirmations; the best of HV7-L, HV7-S and KNN-C-L at any read is in-sample for that selection.
- **Design history (disclosed):** KNN-C was designed after P86's in-sample read (IC, book means), P78's cost map and P87's B7 counts and
  result. All are exploration dates read by >= 14 families. No October or arena outcome was seen.

## 6. Code reuse, build plan, budget

### 6.1 Reuse
- **As is (imported, not edited):** `tools/arena_state.py` (`ArenaState`), `tools/arena_paper.py` (`ArenaEngine`, `register_bot`,
  `StackConfig`), `tools/arena_score.py` (gate sub-cells), `tools/hv6_phantom.py` (`PHANTOM_SIGNERS`), `tools/hv4_cards.py` (`impact_rt`,
  `AGE_MIN_S`, `AGE_MAX_S`, constants only), Am.6's per-hour report tool `arena_am6_read` at its pinned sha, `tools/latency_curve.py` only if
  the P86 label port needs it (it does not for the no-fail label).
- **Patterns copied (no hv7_ file edited):** from `tools/hv7_runner.py`: the hourly loop, the shared call clock per batch (all arms' entries at
  `t_e`), exits before entries, the U list-of-picks path, `assert_identical_pricing`, resume rules and the decision log class; from
  `hv7_count.py` / `hv7_p0_count.py`: the outcome-blind counter; from `hv7_register.py`: hard-coded ids, `--live-registry --confirm`; from
  `hv7_read.py`: the exact sign-flip, Holm block and >= 8 non-zero-day rule (import if they are pure functions, else copy with a test that
  pins equal outputs).
- **Not used:** `hv6_cards.scan` / `build_card` (KNN-C's screen is P86's, so memory and live candidates come from one screen definition), and
  any LLM caller.

### 6.2 New code (builder chunks, each <= 40 turns, WIP commit by turn 28)

| Chunk | Branch | Files | Tests / proofs |
|---|---|---|---|
| **K0** | `claude/knnc-core` | `tools/knnc_model.py` (pure: P86 screen predicates, 10 features, z-score rule, kNN k = 25 with stable ties, `G_b` table, gate, R index), `tools/knnc_pin_m.py` (section 3.3; refuses to load `y`/`pnl*` columns for rows with `td >= 2026-09-03`) | equal outputs to P86 functions on synthetic rows; ties; sd = 0; refuses a future print (as-of mutation raises); `knnc_pin_m` label-refusal test; `G_b` constants equal `costmap.json` values to the last digit |
| **K1** | `claude/knnc-tape-port` | `tools/knnc_tape.py` (arena tape -> P86 per-pool state: prints, traders, V, reserves, first-print age, 6 h high; arena receive-time mapping), `tools/knnc_label.py` (P86 label on arena-format rows, for memory only) | **md5 decision-equivalence:** on a pinned replay of exploration hours converted to the arena format (§1.4 parity-set class), candidate ids, features, `yhat` and gate state are md5-equal to the P86 path; the label port is md5-equal to `p86_label.py` on the same hours (exploration, not test, labels; explore-0814 days only) |
| **K2** | `claude/knnc-runner` | `tools/knnc_runner.py` (hourly loop; L/U/R at `t_e`; daily `M_D` writer at 00:00Z with sha256 row; `closed` rows; bounded memory housekeeping) | hard pricing-independence test (L, U, R same pool, same `t_e`); StubTape drill: one entry per arm per open hour, none in closed or < 3 hours; `M_D` excludes exits after its write instant (planted 23:02 case); two seed-identical runs byte-equal; restart and resume |
| **K3** | `claude/knnc-count-register` | `tools/knnc_count.py` (P0, B count, E1), `tools/knnc_register.py`, E1 drill, P0 job, `m` job | P0 counter loads no price or exit (import guard); E1 thresholds; registration refuses without `m`, H, `M_T0` sha |
| K4 | `claude/knnc-registration` | records PR, DEC-031 amendment (section 8), `bots.json` lines | QP OK on the exact head + reviewer verdict |
| K5 | `claude/knnc-read` | `tools/knnc_read.py` (gate at B; L - U at T0 + H; MDE line; multiplicity block) | owed before B, not before the clock; synthetic books with known answers; refuses the gate before B and the contrast before T0 + H + exit |

Order: QP audit of P86 OK -> K0 -> K1 (needs exploration tape in the arena format; a MiScusi job) -> K2 -> K3 -> the `m` job (MiScusi,
exploration only) -> P0 job (arena tape, counts only) -> smoke (Am.6) -> K4 merged -> clock.

### 6.3 Budget
- **API: 0 USD** (no LLM).
- **Helius: 0 credits.** KNN-C reads the arena tape that `tools/arena_feed.py` already writes from public RPC. No RPC call from the runner.
  The `m` job and the md5 replay read exploration clean views on disk.
- **Memory:** kNN memory is small (about 4,600 + 173 a day points x 10 float64, under 1 MB; distance matrix 8 x 7,000 per hour). The cost is
  the per-pool tape state: 6 h of prints for every V-band pool aged up to 7 d. The cap is pinned before the smoke at **MemoryMax 8G**, with
  Am.6 1.3(d)'s RSS-slope check, on mal-research-0 (121 GB; the arena tape is there). The `m` job and the md5 replay run as MiScusi jobs, one
  heavy job at a time, behind HV6/HV7's replay slot.
- **Disk:** decision logs and `M_D` snapshots, about 1 MB a day; the DEC-031 §3.8 walk-2 reserve check applies unchanged.

### 6.4 Clock
Earliest realistic clock **2026-11-03T00:00:00Z** (QP audit, K0-K3, `m`, P0 on 3 closed days, smoke, records PR). Fallback 2026-11-04T00:00Z.
Then B is about 11-12 at 12 a day, and T0 + H = 11-17 with H = 14. No API horizon binds. Kill-review check: the registration repeats HV7's
check line on its own date (CLAUDE.md: no forward book is added in a kill-review week).

## 7. Risks

1. **Leakage (if the audit is weak).**
   - **Found in this lane (for the QP audit):** P86's memory rule is `x.day < d` (`p86_analyze.py`, walk-forward block). For the 00:02 decision
     of day D, memory holds day D-1's 23:02 candidates. Their exit trigger is the first print at or after 00:02 of day D, and their sell
     resolves up to 1,800 s later. So the 00:02 decision each day uses up to about 8 labels that do not yet exist (P86 PREREG's sentence "an exit
     at 00:02 of the next day is therefore never used before it exists" is not right for the 00:02 hour). The leak is small (about 8 points of
     2,549 to 4,517, one hour in 24), but it is real. KNN-C's snapshot rule (section 2) removes it. The audit should re-run P86's IC with the
     strict rule, or rule that it is negligible.
   - Other items to audit: the V map files (`pool_v_*.json`) may have been fetched after the fact, so V-band membership could carry
     hindsight; the first-print age; feature windows strictly before `t_d`; the pressure proxy uses slot, not `t_recv`.
   - If the audit finds a leak that explains a material part of IC 0.278, KNN-C is not built.
2. **Survivorship in the label.** Memory drops candidates with no exit print within 1,800 s (P86: 360 of 4,960 dropped). These are disproportionately
   dead pools, so `yhat` is biased upward. Live, the arena prices them as strands (a total loss). The gate is therefore not a break-even
   (section 3.3), and the forward read is the judge. Report-only: the daily count of dropped memory points.
3. **Regime drift (Aug/Sep memory to November).** At T0 the memory is 2.5 to 3 months old. Fee tiers, the pump.fun program (redeploys
   10-19), bot activity and the V-band population may have moved. The rolling memory dilutes the old points only slowly (about 173 a day
   against 4,600). No forgetting is added, because that would be a new parameter with no evidence. Report-only at each read: the share of each
   decision's 25 neighbours that are arena points, and the IC split by that share (tercile). A structure change (Am.4 A3-type redeploy) does not
   halt KNN-C automatically; any decoder failure shows as feed gaps and Am.6 exclusions.
4. **Thin edge against costs.** P86's in-sample ungated top pick was -1.128 / -0.656 pp per trade at P55 costs, and the arena stack adds
   1.128044 pp more (section 3.2). For the gate read to pass, the cost gate must lift L's mean by more than about 2.26 pp (flat) or 1.78 pp (pressure) over
   P86's ungated pick in November, plus 1.645 s / sqrt(n) (1.32 to 1.97 pp at n = 100, s = 8% to 12%). Nothing measured says it will. Expect FAIL (section 0).
5. **Multiplicity with HV6/HV7.** These are sibling screens on the same days (section 5.3). KNN-C also adds 3 to N, so the plain line rises.
   Under Am.3 item 2 any screen pass enters a confirmation at 0.05/c. A KNN-C pass in the same week as an HV7 pass is one finding, not two.
6. **Gate starvation by memory.** If November labels are poor, `yhat` falls, the gate closes, and the book ends at E1 or at H as
   NOT_DECIDABLE. That is the gate working as designed, and it is reported as such, never as a loss. It costs one N.
7. **Sticky leaders.** The same pool can be L's pick for many consecutive hours (cross-section features change slowly). The day-block bootstrap
   (report-only) and the daily-sum contrast absorb this. The count of distinct pools per day is reported.

## 8. Draft DEC-031 amendment (DRAFT: not in force; for QP and reviewer; the number is assigned at merge)

> ### Amendment N (DRAFT, written 2026-10-27T15:00Z from `date -u` on mal-research-0; records only; before the clock of any book it covers and before any arena read it touches; outcome-blind; status: DRAFT until merge, QP OK and reviewer verdict)
> DEC-031 Amendment N: the KNN-C books. Scope: `knnc0-L`, `knnc0-U`, `knnc0-R` (and `knnc0-U1..U8` if used), at one `frozen_sha`, one clock.
> No arena tape, ledger, `bots.json` line, outcome, fill, exit or P&L was opened to write it. Design: `docs/research/p88/DESIGN.md` at its
> merged sha. Precondition: quant-proof's OK on the P86 leakage audit (PR #1043), cited by comment id.
> 1. **Self-updating memory (§1.3).** The book's memory snapshot `M_D` is written once at D 00:00:00Z, holds only points whose exit print's
>    receive time is before that instant, and its sha256 is notebooked before the D 00:02 decision. The frozen part is the code, k = 25, the
>    10 features, the label rule, the standardisation rule, the snapshot rule, `G_b` and `m`. No other process writes memory. No arena day
>    before the clock is labelled.
> 2. **Attempts.** An L attempt is a gate-open entry decision. A gate-closed hour is an abstention row: never an attempt, never in n. U and R
>    attempts are their entries in counted hours (>= 3 candidates).
> 3. **Read instant and horizon.** §2 "When" and Am.4 items 2.2-2.3 as merged, with `rate` = P0's gate-open hours a day. Kill line before the
>    clock: P0 rate below 7.2 a day, and the book is not registered.
> 4. **Contrast.** L - U on gate-open hours, daily sums, exact one-sided sign-flip, p <= 0.025 in each of the four sub-cells, >= 8 non-zero
>    days, read once at T0 + H after the last exit. A pass is not a promotion and licenses no canary. The MDE line is printed with it.
> 5. **E1.** At T0 + 2 d: `A_L` < 12 or counted hours < 24 makes every KNN-C read NOT_DECIDABLE, written before any outcome is opened;
>    entries halt at the next 00:00Z. Only decision timestamps and row kinds are read.
> 6. **Deciding cell.** The 1.9 s leg, 0.25 SOL, rent 0 (Amendment 2; the V-band check is the screen's first predicate), the rent-charged
>    leg beside it. Only L is promotable.
> 7. **Disclosure.** The design followed P86 (in-sample IC and book means), P78 and P87 on exploration dates read by >= 14 families. `m` was
>    computed by a pinned rule from explore-0814 labels only, before P0. KNN-C is a sibling of HV7 under Am.3 8(e). Walk-2-hour information
>    (P0 on arena days): arena-only, forever. Attempt counts depend on earlier outcomes through `M_D`; E1 and B can end a read for lack of
>    attempts and cannot create a pass.
> 8. **Unchanged:** the promotion gate and every threshold (n >= 100; >= 5 UTC dates with a majority positive; CI90 lower bound > 0, 1,000
>    draws, seed 1, 5th percentile; ex-top-3 > 0; flat 15% and pressure-fail at slope scale 1); one read per `bot_id`; the 5% and 10% caps;
>    N and c; Amendments 2, 3, 4 and 6 as merged; the seals and §3. No book has cleared the promotion gate.

## 9. What was not measured

The live gate share, `m`, P0 counts, any November IC, the band mix of P86 candidates, the per-trade sd s, the latency cost of the 1.9 s leg
on this screen (`lambda_b` is a proxy), RSS under load, and whether pegged pools enter the top 8. Not judged by quant-proof. No profit claim.
