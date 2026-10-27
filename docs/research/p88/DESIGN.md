# KNN-C: a cost-gated numeric kNN ranker as a forward paper book (design v1, 2026-10-27; STATUS: SHELVED / REDESIGN)

Desk design, lane P88 (manager17). Records only. **Nothing here says KNN-C makes money.** KNN-C has 0 trades, no sha, no clock. No book has
cleared the promotion gate. Paper only. No tape, data dir, ledger, card, job output or sealed path was opened to write this. No price, no
engine run, no outcome was computed. Every rate, sd and power figure below is arithmetic, an assumption or a synthetic simulation unless a
committed file is named as its source. v0 written 2026-10-27T15:00Z, v1 2026-10-27T15:43Z (`date -u`, mal-research-0).

> **STATUS: SHELVED / REDESIGN (manager decision, 2026-10-27, after quant-proof CHANGES 6446421063 and reviewer CHANGES 6446421595 on PR
> #1046 at c37d31a9; both recommend REDESIGN).** This file is a parked design record. **K1 to K5 are not built and KNN-C is not registered.**
> Revival goes only through the two steps of section 10: (a) K0 plus an exploration gate-share count with -100% labels, and (b) the kNN as a
> veto on a near-zero-base universe (lane P91). Section 8's amendment text is **DRAFT, not for registration**.

**v1 changes.** v1 applies the QP and reviewer findings on #1046: the P86 audit precondition is marked met; survivorship-corrected figures are
added (sections 0, 4.3, 7.4); QP's rulings are applied (dead pools in memory at -100%, their resolution instant, m expected at its floor of 0,
the Am.15 count margin, E1 dropping excluded hours, age from the pool index, U shadow ids counted in N, smoke isolation of memory); QP's
simulated MDE replaces the arithmetic line; and the status above.

**Precondition: met.** The P86 leakage audit passed. #1043 is merged, with QP OK at fb211319 (comment 6445612612) and at d75b279c (comment
6445641299). The 00:02 memory leak that v0 flagged is **resolved**: under the strict memory rule the IC is 0.27818 (t 11.303), against 0.27814
(P86 REPORT, correction 6). The leak is negligible.

Sources read: `docs/research/p86/PREREG.md`, `REPORT.md` (including "Corrections after QP and reviewer review of #1043"),
`p86_analyze_output.txt`, `p86_counts.txt` and the scoring lines of `p86_analyze.py` (origin/main 341e4d9a); `docs/research/p78/REPORT.md`
and `costmap.json` (main); `docs/research/p87/REPORT.md` and `p87_count_output.txt` (branch `claude/p87-b7-pullback`, head `82a65c88`);
DEC-031 §1, §2, Amendments 2 (rent and scope, grep), 3, 4, 6 and 15; `docs/research/hv7/DESIGN.md`; the function lists of
`tools/hv7_runner.py` and `tools/arena_paper.py` (grep only); QP comment 6446421063 and reviewer comment 6446421595 on #1046. Seals:
`/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md` (sha256 `823cc062...`). The P86, P78 and P87 figures quoted here are in-sample on
exploration dates that at least 14 families have read.

## 0. What KNN-C is, and the expected result

Each hour, a fixed tape-only screen yields up to 8 PumpSwap candidates (P86's HV7-like set). A frozen numeric kNN (P86: k = 25, 10 causal
features) scores each. The book buys the top-scored pool **only if** its predicted return clears that pool's fee-band cost floor plus a margin
m fixed before the clock. Otherwise it abstains that hour. Two placebo arms price the same hours.

**Expected result for L's absolute gate read: FAIL, with near certainty (QP 6446421063 item 11).**
- **v0's basis is superseded.** v0 rested on P86's survivor-only ungated top pick (-1.128 pp flat / -0.656 pp pressure at the P55 stack). With
  the dead pools put back in (P86 REPORT correction 1, flat model): **book -8.185 pp (dead pools at last price) / -9.150 pp (at -100%); U
  -14.267 / -15.261 pp; book - U +6.081 / +6.112 pp.** In 28 of 286 hours the true top pick later died, and the survivor-only book silently
  took the next pick (a look-ahead selection).
- **Ungated L at the arena stack** is about **-9.3 to -10.3 pp per trade**: -8.185 / -9.150 pp plus the arena add-on of 1.128044 pp (section
  3.2). This is QP's arithmetic; pressure with deaths was not computed.
- **What a pass would need.** To pass (CI90 lower bound > 0 at n of about 100), the cost gate alone would have to lift L by roughly 11 to 12 pp
  or more. Nothing measured supports that.
- **Likelier end.** The book more likely dies before any read: with -100% labels and m = 0 (section 3.3), the gate-open share is likely below
  the 7.2-a-day P0 kill line (section 4.5).

What the book would have tested: a forward, out-of-sample check of P86's rank IC (0.27814 survivor-only; **0.244 / 0.242 with the memory
refit on dead pools**, which is the figure to cite), and whether "predicted return above the cost floor" selects hours that clear costs. Both
are paired against placebos. At best (about 60% power, section 4.3), a pass confirms that a structural ordering picks less-bad pools from a
universe that loses about 14 to 15 pp per trade. P86's next-hour placebo (IC 0.28802 against 0.21098) already shows that the signal is a
persistent pool-type property.

## 1. Universe and period

### 1.1 Period
Hourly. Decision instant `t_d = H:02:00.000Z`, over tape prints with receive time `< t_d` (P86 uses block_time; section 6.2 fixes the arena
mapping). Fixed entry instant for every arm `t_e = H:02:45.000Z`, as HV7. No LLM call sits between them; the 45 s keep KNN-C's latency legs
comparable with HV7's, and the 1.9 s leg starts at `t_e` (DEC-031 §1.4 "latency start"). The 45 s wait itself is not priced in the label: P86's
IC holds at +30 s (0.27873) and +60 s (0.27453), but its level at +45 s is not measured (QP item 4). Exit: P86's label rule, a plain time exit.
The sell is triggered by the first print at or after `t_d + 60 min`, and the 2-slot exit lag applies. Exits run before entries at the same
instant. One position per arm per hour; no cooldown, so the same pool can be bought in consecutive hours (disclosed; the contrasts use daily
sums).

### 1.2 Screen (frozen from P86 PREREG "Candidate set", with one change: age)
At `t_d`, over prints before `t_d`, per canonical pool:
- WSOL quote; V in [17.5e9, 17.7e9] lamports inclusive (V missing or out of band: not a candidate). This is also DEC-031 Amendment 2 item 6's
  V-band check, so KNN-C is rent-0-covered (section 5.1);
- **age 2 h to 7 d inclusive, measured from the pool's first print in the arena-tape pool index (R6), not from the history this process has
  loaded (QP item 6(d)).** The records PR names this 2 h predicate as `path:line@frozen_sha`, as the filter that implies R9(b) (QP item 10(a)).
  Disclosed: `log_age` in the exploration memory is truncated at the block start (P86 Limits), so the feature's live distribution is shifted
  against the memory's;
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

1. **The frozen model's evidence is for P86's set.** P86's IC and its memory come from the unrestricted V-band screen. P86 does not report the
   band mix of its candidates, so no committed count says how many memory points or candidate hours survive a B4-B7 cut. Restricting would
   freeze a model on a population that its evidence does not describe, with a memory of unknown size (possibly too few same-band neighbours for
   k = 25). P86's IC is mostly structural (structural feature group 0.30390, returns group 0.23708; REPORT correction 3). A band cut removes part
   of that by construction, so the IC would not carry over.
2. **The gate does the band work per pool.** The floor in the gate is the pool's own band floor (section 3). A B1 pool must clear a floor about
   3.3 times a B7 pool's (P78 floor at 0.25 SOL: 3.0438% against 0.6479%), so high-fee pools are traded only when predicted to clear their own
   cost. A universe cut would be a second, cruder version of the same filter.
3. **The range filter has no count that applies here.** Its only count, P87's, is vacuous: 0 signals dropped, because P87's dip rule implies a
   range above 2% (`p87_count_output.txt`). What it would remove from an hourly top-8-by-`vol_60m` set is not counted anywhere. The pegged-pool
   problem it targets is P78's: 34.5% of tradeable B7 pool-hours have a median 2-slot move below 0.001%. P86's screen already requires 20 prints,
   8 distinct traders and 10 SOL of volume in the last 15 or 60 min. Whether pegged pools enter the top 8 is **not measured**. P0 reports the 6 h
   range of each counted top pick, report-only (section 4.5).
4. **Capacity.** P78 counts tradeable pool-hours/day of 757.3 (B4), 240.7 (B5), 150.0 (B6) and 990.1 (B7), against 1,328 (B1) and 1,579 (B2);
   P86 finds about 110 screen survivors per hour (`p86_counts.txt`: mean 109.99, median 118). The full screen keeps the hourly set full
   (620 of 621 P86 hours had >= 3 survivors). A band cut has no count that shows it stays full on V-band pools.

Not chosen, recorded: a B4-B7 + range variant would be a second book (+1 in N, a sibling under Am.3 8(e)), and its memory would have to be
re-screened first. It is not registered here. Section 10 (b) is the redesign that replaces this question.

## 2. Model (frozen from P86, with the QP label ruling)

- **Code (reference pin).** The logic is ported from these P86 files. Their sha256 values are the same on origin/main (341e4d9a; QP checked
  790b847b) and on the P86 branch head 05e1f1b8. #1043 changed text only (fb211319), so no re-pin is needed:
  - `docs/research/p86/p86_analyze.py`, sha256 `c7b9c0737456b0d1cb5dcc58995e07f3cad12b789d93110d833f3a6377a54956` (kNN scoring:
    `K = 25`, `FEATS`, z-score, Euclidean, `argsort(kind="stable")`);
  - `docs/research/p86/p86_build.py`, sha256 `db335be9cfdddab2687d10f694415cfd39fe13bc57f91204d3c0c777db74aa75` (screen and the 10 features);
  - `docs/research/p86/p86_label.py`, sha256 `0ac964e814222e5aae061cf144d80e0f61e19126a016eb961e33c9118c6458b0` (label).

  The real freeze is K1's md5 proof (section 6.2). The port differs from P86 **by intent** in two places: the memory snapshot rule (below,
  in place of `x.day < d`) and the dead-pool label (below). The md5 equivalence is therefore stated against a **fixed memory set** (explore-0814
  as memory, scoring fresh-0903 features) on the priced subset. The dead-pool rule has its own fixture test; it cannot be md5-equal to
  `p86_label.py`, which drops these points (QP item 1).
- **Features (10, causal, prints before `t_d`):** `log_vol60`, `ret_5m`, `ret_15m`, `ret_60m`, `flow60`, `log_signers15`, `log_age`,
  `top3_60m`, `close_vs_hi` (6 h high), `log_qeff` (P86 PREREG "Features"). A candidate with an undefined feature is dropped and counted.
- **Score.** `yhat` = mean label of the k = 25 nearest memory points, Euclidean distance on z-scored features, ties by memory order (stable).
- **Label (QP ruling 6446421063 item 4: dead pools enter memory at -100%).** P86's label: no-fail net P&L / stake at 0.25 SOL, P55 pricing
  (fee tier on quote + V and base, 2 x 55,000 lamports), entry at `slot_d + 2`, exit trigger `t_d + 60 min`, exit lag 2, exit-print window
  1,800 s (P86 Amendment 1), **except**:
  - **Dead pools.** A candidate dropped by P86 for "no trigger print" or "no exit print" in its window **enters memory**. Its label is -100%
    of stake, minus the P55 buy-leg costs the label already charges (the buy send and the buy fee). Reason: DEC-031 §1.4's strand class says an
    unpriced exit "is a total loss of stake plus the buy send and rent. It is never dropped". `arena_score` also decides on all four sub-cells,
    "total" among them (Am.3 8(a)). A gate that has to keep L off trades that strand should learn from the label the arena will book.
  - **Never filled.** "No entry print" candidates were never filled. They stay out of memory.
  - **Report-only.** A last-price variant of the dead-pool label may be printed. It never decides.
  - **Scope.** This label is used in the exploration memory, in the rolling arena memory, in the `m` job (section 3.3) and in P0 (section 4.5).
  - **Disclosed, small cost terms (not blocking).** Under-counted: the fail-leg send (about 0.03 pp flat; about 0.08 pp under pressure at mean
    p 0.289) and the strand rent of 2,039,280 lamports (Am.2 item 4) on the dead share. Over-counted: `lambda_b` (section 3.2), a median
    absolute move that stands in for a signed cost.
- **Standardisation rule (frozen; QP item 3: leak-free).** For each memory snapshot `M_D` (below): mu and sd per feature over all points of
  `M_D`, population sd (`ddof = 0`); sd = 0 is replaced by 1. Memory points and that day's candidates are both standardised with that day's
  (mu, sd). Nothing is fitted on the scored day. This matches `p86_analyze.py` lines 31-34.
- **Memory.**
  - **At the clock (`M_T0`):** every filled P86 candidate from all three exploration blocks (explore-0814, fresh-0903, exp011-0909), dead
    pools included at -100%. That is P86's 4,600 priced points plus the dropped points that had an entry print (360 dropped in all, of which 47
    had no entry print and stay out; P86 REPORT). At registration all three blocks lie in the past, so using the fresh-0903 and exp011-0909
    labels as memory is not a test read. Once they are in KNN-C's memory they are no longer any kind of test for P86, and this is disclosed.
    `M_T0`'s sha256 is pinned before the smoke and checked equal at the clock (QP item 10(c)).
  - **Rolling:** from the clock on, the book's own candidates on the arena tape, labelled by the same rule, are added day by day.
  - **Snapshot rule (QP item 2).** `M_D`, used for every decision with `t_d` in `[D 00:00Z, D+1 00:00Z)`, holds exactly the points whose
    **resolution instant** is before `D 00:00:00Z`:
    - for a priced point, the receive time of its exit print;
    - for a dead point, the end of its pricing window: `t_trigger + 1,800 s`, or `t_d + 60 min + 1,800 s` when there is no trigger print.

    A candidate of `D-1 23:02` that resolves after midnight enters `M_{D+1}`; unresolved points wait. This removes the 00:02 leak that P86
    had (resolved: negligible, strict IC 0.27818). Without the dead-point instant, a builder would either drop dead points (survivorship again)
    or insert them early (a leak). `M_D` is written once at `D 00:00:00Z`, and its sha256 is notebooked before the `D 00:02` decision (DEC-031
    §1.3: a version uses only outcomes whose exit is before its write instant). No arena day before the clock is ever labelled (arena tape:
    counts and creates only); the first arena points enter `M_{T0+1}`.
  - **Smoke isolation (QP item 10(c)).** During the Am.6 smoke the labeller and the `M_D` writer are off, or write only in the scratch root.
    No smoke label ever enters `M_T0` or any `M_D`.
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

The second form is the one implemented (it needs no band-median approximation of the pool's own fee, which `yhat` already has exactly). QP
checked it against `costmap.json`: `floor_025_pct` = `rt_fee_pct` + 0.044 pp of P55 sends + `lag2_med_pct`, so `F_b - c_b = lambda_b +
Delta_stack`. There is no double count (QP item 4).

### 3.2 The terms (constants pinned now; 0.25 SOL, the deciding stake)
- `Delta_stack`, the arena's deciding stack over P55 (DEC-031 §1.4 and Amendment 2):
  - sends: 2 x (505,000 - 55,000) = 900,000 lamports = 0.36 pp;
  - E1 floor: max(haircut, 384,022 lamports per 0.05 SOL). At 0.25 SOL the floor is 1,920,110 lamports = 0.768044 pp. The haircut
    (+26.08 / -16 bps, about 42.08 bps = 0.4208 pp) is smaller, so the floor binds: 0.768044 pp. It is "a max and not a sum" (Am.2 item 1);
  - rent: 0 (Amendment 2 covers KNN-C, section 5.1). The rent-charged leg, +2,039,280 lamports = 0.815712 pp, is printed beside every
    deciding figure. It does not enter the gate.
  - **`Delta_stack` = 1.128044 pp.** (Any numeric match with P86's survivor-only flat book mean of -1.128 pp is a coincidence. That mean did
    not set this figure.)
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

### 3.3 Margin m (rule fixed; number never computed; expected to bind at 0)
`m` cannot be a number in this desk lane: P86's committed outputs do not include the distribution of top scores, and computing it is a run.
The rule below was fixed before any such number existed. It uses **no fresh-0903 or exp011-0909 label** (QP item 5: frozen and outcome-safe).

1. **Pinning set.** The 285 P86 test hours (fresh-0903 and exp011-0909, hours with >= 3 candidates). Their candidates' features are loaded;
   their label column is not loaded (the tool refuses it, section 6.2 K0).
2. **Pinning memory.** Explore-0814 only, **with section 2's labels (dead pools at -100%)**, as one fixed memory, standardised by section
   2's rule over that memory.
3. For each pinning hour h: `e_h = yhat_top,h - G_{b(top,h)}`, where `top` is the hour's highest-`yhat` candidate (ties by screen order).
4. **`m = max(0, median_h e_h)`**, written to 6 decimal places in pp, together with the count of hours with `e_h > m` and the tool's output
   sha256, in a MiScusi notebook entry before P0 runs. It goes into the `bots.json` line before the clock.

**The m = 0 case (QP item 5(b)), which is the expected one.** With dead pools at -100%, U's in-sample mean moves from -7.493 to -15.261 pp,
so `yhat` falls by several pp on average and the median `e_h` is very likely negative. Then `m = 0` and the gate reads `yhat_top > G_b`: a
predicted net return of at least +1.13 to +2.00 pp, against a memory whose mean is about -15 pp. The gate-open share can then be far below 50%.
**The v0 planning figure of "about 12 a day" is withdrawn.** It held only if `m > 0` bound at the median. The gate-open rate is unknown until
it is counted (section 10 (a)).

Why the median (q* = 0.5), as v0 set it: so that, when `m > 0`, the gate abstains in about half the hours. Without a margin and without the
cost term, the gate would barely bind on survivor-only labels: 261 of 285 P86 hours had a top score above 0 (a count). This is a **throughput
rule, not a calibrated break-even.** `yhat_top` is the max of up to 8 noisy kNN means, so it is biased upward. The gate states a preference
ordering over hours. It does not claim that an open hour clears costs.

Disclosed: this lane's author had seen P86's REPORT (book means, the 261/285 count) before choosing q* = 0.5. The median rule was chosen to set
throughput and was not tuned to any outcome. No alternative quantile was evaluated, and none will be.

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
  "uniform random over the same gated set" with no draw noise (HV7 section 3, "Why U"). QP item 6: both arms are priced on the same gate-open
  hours, chosen from pre-decision information only, consistent with DEC-031 §1-2 and Am.3/4/6.
- U also prices every closed hour. This gives two report-only figures without another `bot_id`: the gate's value (mean of U's `top` row in open
  hours against closed hours) and the rank IC of `yhat` against U-priced returns.
- R asks whether trading this screen at all is worse than a random survivor. R - U (survivors against top-8) is report-only.
- No S arm. P86 found the `vol_60m` leader the worst arm (in-sample), and S adds a cell without a question KNN-C needs.
- **Cross-arm pricing independence** (HV7 C2's hard test): byte-identical entry and exit prices, every leg and both fail models, when L, U
  and R buy the same pool at the same `t_e`. If the test fails, the design is invalid and the book is not registered.
- **U's 8 positions (QP item 6(a)).** U holds up to 8 open positions for one `bot_id` per hour, through HV7's list-of-picks path
  (`hv7_runner.py` line 525 passes `u_set` as a list of picks). If that path does not apply and shadow ids `knnc0-U1..U8` are used, **each
  is its own `bots.json` line and counts in N** (§1.2, Am.3 item 3): N then grows by 10, not 3.

### 4.2 Gate read at B (L only)
Unchanged DEC-031 §2 with Amendments 4 (item 2) and 6, plus Am.15's count margin:
- **B (Am.15 margin form, QP item 6(b)).** B is the first 00:00Z boundary with clock_start_ms < B <= clock + H such that, among the L attempts
  decided in `[clock, B)` and not dropped by Am.6 (decided in an excluded hour or a restart gap), `O - floor(0.05 x O) >= 100`, with the O
  attempts on at least 5 distinct UTC dates. O = gate-open entries (guarded reverts included). The count is made by the outcome-blind
  decision-timestamp tool (`knnc_count.py`), which loads no close, exclusion class, fill, exit, leg or P&L. If the gate's trade set (after the
  §1.4 feed-gap and Am.6 span exclusions) still has n < 100 at B, the read is NOT_DECIDABLE, final, labelled "count-only, not a loss". The read
  covers the attempts decided before B, once all have exited (61 min plus the exit window).
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
- **In-sample reference (not a prediction).** With dead pools included, P86's book - U is +6.081 pp (last price) / +6.112 pp (-100%), flat,
  against book -8.185 / -9.150 pp and U -14.267 / -15.261 pp. In 28 of 286 hours the true top pick later died. The refit IC is 0.244 / 0.242.
- **MDE (QP synthetic simulation, comment 6446421063 item 9; it replaces v0's arithmetic line of 0.16).**
  - Method: synthetic data only, no lab data. N = 8 per hour; a Gaussian copula between score and return; exact one-sided sign-flip over all
    2^D sign vectors on daily sums of (L - mean of N); p <= 0.025; 1,000 runs; seed 1. Script `mde_sim_1046.py` (QP scratchpad `pr1046/`,
    sha256 `a4258a7193c0c5dee8c6f491b2972226f7e8edcc60541e810c3d48e9a96ec87c`; not committed).
  - **Normal returns, no deaths:** 80% power at a rank IC of about **0.13**, at 12 hours a day x 14 d and at 7 x 21 d (s = 8% or 12%).
  - **8% of candidates dead at -100%, death not predicted by the score** (matches P86 in-sample: the top pick died in 28 of 286 hours, about
    U's rate): survivor s = 12%: power 0.57 at IC 0.22 and 0.76 at IC 0.29 (lift +6.25 pp at IC 0.29, close to P86's book - U of +6.1 pp, so
    this calibration is the realistic one); survivor s = 20%: 0.70 at IC 0.18 and 0.84 at IC 0.22.
  - **Reading.** "MDE rank IC about 0.13 (no deaths) to about 0.29 (8% unpredicted deaths, s = 12%)." At the refit in-sample IC of 0.242, the
    contrast has **about 60-65% power** if November matches Aug/Sep, and less with drift. **A non-pass is reported as "no skill detected at
    that MDE", never as "no skill".**

### 4.4 E1 early halt (outcome-blind; a host-side MiScusi job scheduled before T0)
At T0 + 2 d 00:00Z, `knnc_count.py` runs once and its output sha256 is notebooked. `A_L` = counted L attempts in `[T0, T0 + 2 d)`, **dropping
attempts decided in Am.6-excluded hours or restart gaps, as B does (QP item 6(c))**; `C` = counted hours (open + closed), with the same drop.
- `A_L < 12` (6 a day; 100 attempts in 14 days need 7.2 a day): every KNN-C gate read and contrast is NOT_DECIDABLE (`E1_entry_rate`). This is
  written once, before any KNN-C outcome is opened. Entries halt in all arms at the next 00:00Z, and code exits run to completion.
- `C < 24` (fewer than half the hours have >= 3 candidates): the same, with reason `E1_screen_starved`. Since KNN-C has no LLM, an E1 halt
  means a screen, feed or memory fault, or a gate closed by its own memory.
- Report-only: the gate-open share `A_L / C`, and the daily `M_D` sha256 list (all present or not).
- E1 never yields PASS or FAIL. No other count is read before B. The halt path is drilled at the final sha on synthetic tape before T0
  (HV6/HV7 E1 drill pattern); without the drill, E1 is declaration-only. QP item 7: OK.
- Bias note (as Am.15 item 8): the gate depends on memory labels, so `A_L` is a function of earlier outcomes through `M_D`. E1 and B move only
  on decision-side counts. They can end a read for lack of attempts, and cannot create a pass.

### 4.5 Expected trades per day (counts only; P0 replaces this)
- Hours: P86 had >= 3 survivors in 620 of 621 decision hours (`p86_counts.txt`), so counted hours are at most about 24 a day in Aug/Sep.
- **Gate share: unknown, and likely low.** With -100% labels, `m` is expected to be 0 and the gate to read `yhat_top > G_b` (section 3.3). No
  count of that share exists. **The v0 planning figure (about 12 a day, 24 x 0.5) is withdrawn**; it held only if `m > 0`.
- Upper reference (survivor-only labels, no cost term, no margin): 261 of 285 P86 test hours (91.6%) had a top score above 0. At 21.92
  hours/day (`p86_analyze_output.txt`) that is at most 20.07 a day. With -100% labels and `G_b` > 0 the true share is lower, by an unknown
  amount.
- If the rate were 7.2 a day, H = max(14, ceil(150 / 7.2) = 21) = 21.
- **P0 (Am.3 item 1 as replaced by Am.4 item 2; structure-only).** The P0 rule text goes in a notebook before the job runs. The P0 tool is the
  KNN-C decision path at the registration-candidate sha, on >= 3 full closed arena days before registration, with memory = `M_T0` (exploration
  only, section 2 labels). No arena label, price, engine run or exit is involved. Outputs per UTC day: counted hours, gate-open hours, screen
  survivors (median), and the mcap-cap drop count, plus feed gap counts. Report-only: the band mix of the top picks and the 6 h range of the
  top pick (min, median). Not registered if: the average is below 2.0 gate-open hours a day or any day has 0 (Am.4 2.1); a hard gap or
  `data_loss` > 1 h (NOT_DECIDABLE); or a KNN-C kill line (below) fires.
- **KNN-C kill lines (before the clock, structure only):** P0 gate-open rate < 7.2 a day, so that n = 100 is not reachable in 14 days. This is
  stricter than Am.4's floor, which a book may pre-register (QP item 8: OK), and KNN-C stops instead of running a 21-90 day book. Also: the md5
  proof fails, the pricing-independence test fails, or any seal question is open. Section 10 (a) runs this kill on exploration data first,
  before anything else is built.

## 5. DEC-031 fit, seals, multiplicity

### 5.1 Fit
- **Rent:** Amendment 2 covers KNN-C if its registration and clock follow that merge (they would). The V-band check of Am.2 item 6 is the
  screen's first line (section 1.2), named as `path:line@frozen_sha` in the records PR. The host passes `rent=0`. `StackConfig` is pinned:
  `rent` 0, `e1_floor_per_005` 384022, `strand_rule` "both", `k_rounding`, `feed_delay_slots`, `gap_slots` 150, `gap_silence_ms` 60000 (Am.2
  item 8).
- **Am.3:** rolling registration; clock = the first 00:00Z after the registration record (and after the smoke, Am.6 1.4); P0 (as above);
  one process per `frozen_sha`; automatic confirmation of any screen pass at 0.05/c; the R8 multiplicity block; no second frozen copy.
  Am.3 item 4 (October screening) is **not used**: no rule is priced on any arena hour before the clock.
- **Am.6:** smoke of >= 2 h on the real feed at the `frozen_sha` (blocking list (a)-(e); (f) does not apply, no LLM); per-hour integrity
  tool and thresholds pinned (price-path: arena feed gaps; decision-input: tape staleness > 120 s at `t_d`, stamped by the feed); 10% cap.
- **Am.15:** its margin form of the read instant is adopted for KNN-C's B (section 4.2).
- **§1.3 (self-improving book):** the rolling memory makes KNN-C a self-updating book. Its frozen part is the code, k, the features, the label
  rule (dead pools at -100%), the resolution-instant and snapshot rules, `G_b` and `m`. Each `M_D` is hashed and logged before its first use,
  and uses only points resolved before its write instant. Nothing else may write memory, the smoke included.

### 5.2 Seals (SEALS-BRIEF 823cc062; QP rulings 6446421063 item 10)
- No HV4, Q50, HV6 or HV7 output, ledger, decision row or job status is read by any KNN-C tool. U and R are KNN-C's own bot ids.
- **EXP-024 Look-2: OK.** The screen requires age >= 2 h (from the arena-tape pool index) and a last print within 120 s of `t_d`, so s0 <=
  `t_d` - 7,200 s and the last print is >= `t_d` - 120 s >= s0 + 360 s. For a pool whose create is not on the tape, measured age is at most
  true age, which is the safe direction. No outcome falls in a pool's first 360 s in the book, the memory, P0 or the smoke, so Am.3 8(c) is met.
  Condition: the records PR names the 2 h predicate as `path:line@frozen_sha`, as the R9(b)-implying filter, with age from the pool index.
- **EXP-025 C1-NF: OK.** C1-NF's grid is whole minutes from grad + 10 min to grad + 24 h, with exit E3 at 300 s and a LightGBM stack. KNN-C
  decides hourly at H:02 with a 60-min time exit and a kNN on 10 tape fields, so it matches neither the grid nor the exit, and Am.3 8(b) is not
  triggered. No C1-NF label, pass-A file or October C1-NF row is computed or read. Disclosed: the universes overlap at ages 2-24 h.
- **Smoke: OK, with an added condition.** Neither §3.5 nor §3.7 covers the smoke's outcomes, so it may run with pricing on. Am.6 1.3 governs:
  counts, timings and flags only; decision and ledger rows are never opened for price, fill, exit or P&L; never an input to any read. Those
  hours are in-clock archive rows (>= 10-25T00Z), in-sample for any rule. **Added:** the smoke's labeller and `M_D` writer are off or write only
  in the scratch root; no smoke label enters `M_T0` or any `M_D`; `M_T0`'s sha256 is pinned before the smoke and checked equal at the clock.
- **DBC, LaunchLab, FLOOR G1, meta-capture, forward-1016:** not touched. KNN-C is PumpSwap-only on the arena tape.
- **Walk-2 information (DEC-030 §2 R1):** P0 counts on arena days, so KNN-C would be an arena-only book forever.

### 5.3 Multiplicity and siblings
- KNN-C would add 3 `bot_id`s to N (`knnc0-L`, `-U`, `-R`), with one promotable cell. If the U shadow ids `knnc0-U1..U8` are used, each counts:
  10 in all (section 4.1). Each read carries the Am.3 item 3 block: N counted from the notebook copies at that read (HV4's 9, Q50, the H5 books,
  the hunt23/cohort books, HV6's and HV7's arms, stopped and NOT_DECIDABLE books included), the plain line 1 - 0.95^N, and the report-only p
  (10,000 draws, seed 1, the larger of the two fail models) with its Holm rank.
- **Siblings (Am.3 8(e)).** HV7 runs a near-identical screen (HV6 cards, top 8 by `f.vol_60m`, 2 h-7 d) on overlapping days. KNN-C's screen is
  P86's tape-only version (no chain-fact vetoes), so the two candidate sets overlap but are not equal. An HV7 pass and a KNN-C pass on the same
  days are not independent confirmations; the best of HV7-L, HV7-S and KNN-C-L at any read is in-sample for that selection.
- **Design history (disclosed):** KNN-C was designed after P86's in-sample read (IC, book means, and in v1 the survivorship corrections),
  P78's cost map and P87's B7 counts and result. All are exploration dates read by >= 14 families. No October or arena outcome was seen.

## 6. Code reuse, build plan, budget (parked: not built)

### 6.1 Reuse
- **As is (imported, not edited):** `tools/arena_state.py` (`ArenaState`), `tools/arena_paper.py` (`ArenaEngine`, `register_bot`,
  `StackConfig`), `tools/arena_score.py` (gate sub-cells), `tools/hv6_phantom.py` (`PHANTOM_SIGNERS`), `tools/hv4_cards.py` (`impact_rt`,
  `AGE_MIN_S`, `AGE_MAX_S`, constants only), Am.6's per-hour report tool `arena_am6_read` at its pinned sha, the arena-tape pool index (R6)
  for age.
- **Patterns copied (no hv7_ file edited):** from `tools/hv7_runner.py`: the hourly loop, the shared call clock per batch (all arms' entries at
  `t_e`), exits before entries, the U list-of-picks path, `assert_identical_pricing`, resume rules and the decision log class; from
  `hv7_count.py` / `hv7_p0_count.py`: the outcome-blind counter; from `hv7_register.py`: hard-coded ids, `--live-registry --confirm`; from
  `hv7_read.py`: the exact sign-flip, Holm block and >= 8 non-zero-day rule (import if they are pure functions, else copy with a test that
  pins equal outputs).
- **Not used:** `hv6_cards.scan` / `build_card` (KNN-C's screen is P86's, so memory and live candidates come from one screen definition), and
  any LLM caller.

### 6.2 New code (builder chunks, each <= 40 turns, WIP commit by turn 28). **Shelved: only K0 may be built, under section 10 (a). K1-K5 are not built.**

| Chunk | Branch | Files | Tests / proofs |
|---|---|---|---|
| **K0** | `claude/knnc-core` | `tools/knnc_model.py` (pure: P86 screen predicates, 10 features, z-score rule, kNN k = 25 with stable ties, the dead-pool label rule and resolution instant, `G_b` table, gate, R index), `tools/knnc_pin_m.py` (section 3.3 plus the section 10 (a) gate-share count; refuses to load `y`/`pnl*` columns for rows with `td >= 2026-09-03`) | equal outputs to P86 functions on synthetic rows; ties; sd = 0; refuses a future print (as-of mutation raises); dead-pool fixture test (label and resolution instant); `knnc_pin_m` label-refusal test; `G_b` constants equal `costmap.json` values to the last digit |
| K1 (not built) | `claude/knnc-tape-port` | `tools/knnc_tape.py` (arena tape -> P86 per-pool state; age from the pool index; arena receive-time mapping), `tools/knnc_label.py` (section 2 label on arena-format rows, for memory only) | **md5 decision-equivalence against a fixed memory set** (explore-0814 as memory, scoring fresh-0903 features) on the priced subset, on a pinned replay of exploration hours converted to the arena format (§1.4 parity-set class): candidate ids, features, `yhat` and gate state md5-equal to the P86 path; the dead-pool rule by its own fixture, not by md5 against `p86_label.py` |
| K2 (not built) | `claude/knnc-runner` | `tools/knnc_runner.py` (hourly loop; L/U/R at `t_e`; daily `M_D` writer at 00:00Z with sha256 row; `closed` rows; smoke mode with labeller and `M_D` writer off; bounded memory housekeeping) | hard pricing-independence test (L, U, R same pool, same `t_e`); StubTape drill: one entry per arm per open hour, none in closed or < 3 hours; `M_D` excludes points resolved after its write instant (planted 23:02 case and planted dead point); smoke mode writes no memory; two seed-identical runs byte-equal; restart and resume |
| K3 (not built) | `claude/knnc-count-register` | `tools/knnc_count.py` (P0, Am.15-margin B count, E1 with Am.6 drops), `tools/knnc_register.py`, E1 drill, P0 job, `m` job | P0 counter loads no price or exit (import guard); E1 thresholds and drops; registration refuses without `m`, H and the `M_T0` sha |
| K4 (not built) | `claude/knnc-registration` | records PR, DEC-031 amendment, `bots.json` lines | QP OK on the exact head + reviewer verdict |
| K5 (not built) | `claude/knnc-read` | `tools/knnc_read.py` (gate at B; L - U at T0 + H; MDE line; multiplicity block) | synthetic books with known answers; refuses the gate before B and the contrast before T0 + H + exit |

### 6.3 Budget (if ever built)
- **API: 0 USD** (no LLM). **Helius: 0 credits.** KNN-C reads the arena tape that `tools/arena_feed.py` already writes from public RPC, and
  the runner makes no RPC call. The `m` job, the gate-share count and the md5 replay read exploration clean views on disk.
- **Memory:** the kNN memory is small (about 5,000 + 173 a day points x 10 float64, under 1 MB; distance matrix 8 x 7,000 per hour). The cost is
  the per-pool tape state: 6 h of prints for every V-band pool aged up to 7 d. The cap is pinned before the smoke at **MemoryMax 8G**, with
  Am.6 1.3(d)'s RSS-slope check, on mal-research-0. Section 10 (a)'s job runs as a MiScusi job, one heavy job at a time.
- **Disk:** decision logs and `M_D` snapshots, about 1 MB a day; the DEC-031 §3.8 walk-2 reserve check applies unchanged.

### 6.4 Clock
None. v0's "earliest realistic clock 2026-11-03T00:00Z" is withdrawn with the shelving.

## 7. Risks

1. **Leakage: resolved.** v0 found that P86's memory rule (`x.day < d`) let the 00:02 decision of day D use day D-1's 23:02 labels before they
   existed. QP's audit measured it: under the strict rule the IC is 0.27818 (t 11.303), against 0.27814, so it is negligible (P86 REPORT
   correction 6). KNN-C's snapshot rule with the resolution instant (section 2) removes it, dead points included. The IC also holds at +30 s and
   +60 s entry lags. QP found no leakage (6445612612).
2. **Survivorship in the label: ruled (QP item 4).** P86 dropped 360 of 4,960 candidates for lack of an exit print, mostly dead pools, which
   biased `yhat` and every level upward. KNN-C's memory now includes them at -100% (section 2), with their resolution instant pinned. Last-price
   labels are report-only. The levels to cite are the corrected ones (section 0).
3. **Regime drift (Aug/Sep memory to November).** At T0 the memory would be 2.5 to 3 months old. Fee tiers, the pump.fun program (redeploys on
   10-19), bot activity and the V-band population may have moved. The rolling memory dilutes the old points only slowly (about 173 a day
   against about 5,000). No forgetting is added, because that would be a new parameter with no evidence. Report-only at each read: the share
   of each decision's 25 neighbours that are arena points, and the IC split by that share (tercile). `log_age` is also shifted (section 1.2).
4. **Thin edge against costs: decisive.** With dead pools, P86's in-sample ungated top pick is -8.185 pp (last price) / -9.150 pp (-100%), flat,
   at P55 costs, and U is -14.267 / -15.261 pp. In 28 of 286 hours the true top pick later died. The arena stack adds 1.128044 pp, so ungated L
   would be about **-9.3 to -10.3 pp** per trade. To pass the gate at n of about 100, the cost gate alone would have to lift L by roughly 11 to
   12 pp or more. Nothing measured says it can. FAIL is expected with near certainty, and a stop at the P0 kill line is likelier still
   (section 0). This is why KNN-C is shelved.
5. **Multiplicity with HV6/HV7.** These are sibling screens on the same days (section 5.3). KNN-C would also add 3 (or 10) to N, so the plain
   line rises. Under Am.3 item 2 any screen pass enters a confirmation at 0.05/c. A KNN-C pass in the same week as an HV7 pass is one finding,
   not two.
6. **Gate starvation by memory.** With -100% labels the gate may close in most hours, so the book would end at P0, E1 or H as NOT_DECIDABLE.
   That is the gate working as designed, and it would be reported as such, never as a loss. It costs one N if registered, and nothing if caught
   at section 10 (a).
7. **Sticky leaders.** The same pool can be L's pick for many consecutive hours (P86's signal is a persistent pool-type property: next-hour
   placebo IC 0.28802). The day-block bootstrap (report-only) and the daily-sum contrast absorb this. The count of distinct pools per day would
   be reported.

## 8. Draft DEC-031 amendment (DRAFT, NOT FOR REGISTRATION: KNN-C is shelved; kept as a record and as a template for a revival under section 10)

> ### Amendment N (DRAFT, NOT FOR REGISTRATION; written 2026-10-27T15:00Z, revised 2026-10-27T15:43Z from `date -u` on mal-research-0; records only; outcome-blind; it would need QP OK, a reviewer verdict and a revival under DESIGN section 10 before any use)
> DEC-031 Amendment N: the KNN-C books. Scope: `knnc0-L`, `knnc0-U`, `knnc0-R` (and `knnc0-U1..U8` if used, each its own `bots.json` line
> counted in N), at one `frozen_sha`, one clock. No arena tape, ledger, `bots.json` line, outcome, fill, exit or P&L was opened to write it.
> Design: `docs/research/p88/DESIGN.md` at its merged sha. Precondition met: quant-proof's OK on the P86 leakage audit (#1043, comments
> 6445612612 and 6445641299). Status at writing: shelved (QP 6446421063 and reviewer 6446421595 on #1046: REDESIGN).
> 0. **Revival gate.** Nothing in this amendment applies unless DESIGN section 10 (a)'s exploration gate-share count, with -100% labels,
>    shows at least 7.2 gate-open hours a day, and a new design record cites that count. Section 10 (b) (lane P91) is a different design and
>    would need its own amendment.
> 1. **Self-updating memory (§1.3).** The book's memory snapshot `M_D` is written once at D 00:00:00Z. It holds only points whose resolution
>    instant is before that instant: for a priced point, its exit print's receive time; for a dead point, `t_trigger + 1,800 s`, or
>    `t_d + 60 min + 1,800 s` with no trigger print. Its sha256 is notebooked before the D 00:02 decision. The frozen part is the code, k = 25,
>    the 10 features, the label rule, the standardisation rule, the snapshot rule, `G_b` and `m`. No other process writes memory. No arena day
>    before the clock is labelled.
> 2. **Label.** P86's no-fail net label, except that a filled candidate with no trigger or exit print in its window enters memory at -100% of
>    stake minus the P55 buy-leg costs (DEC-031 §1.4 strand class). A last-price variant is report-only. Never-filled candidates stay out.
> 3. **Smoke isolation.** During the Am.6 smoke the labeller and `M_D` writer are off or write only in the scratch root. No smoke label enters
>    any memory. `M_T0`'s sha256 (exploration memory only, item 2 labels) is pinned before the smoke and checked equal at the clock.
> 4. **Attempts.** An L attempt is a gate-open entry decision. A gate-closed hour is an abstention row: never an attempt, never in n. U and R
>    attempts are their entries in counted hours (>= 3 candidates).
> 5. **Read instant and horizon.** B is the first 00:00Z boundary with clock < B <= clock + H at which the L attempts decided in [clock, B)
>    and not dropped by Amendment 6 satisfy O - floor(0.05 x O) >= 100 on at least 5 distinct UTC dates (Am.15 item 1's margin form), counted
>    from decision timestamps and row kinds only. If the trade set still has n < 100 at B, the read is NOT_DECIDABLE, "count-only, not a loss".
>    H follows Am.4 item 2.2 with `rate` = P0's gate-open hours a day. Kill line before the clock: P0 rate below 7.2 a day, and the book is
>    not registered.
> 6. **Contrast.** L - U on gate-open hours, daily sums, exact one-sided sign-flip, p <= 0.025 in each of the four sub-cells, >= 8 non-zero
>    days, read once at T0 + H after the last exit. A pass is not a promotion and licenses no canary. The MDE line printed with it is "MDE rank
>    IC about 0.13 (no deaths) to about 0.29 (8% unpredicted deaths, s = 12%)", and a non-pass reads "no skill detected at that MDE".
> 7. **E1.** At T0 + 2 d, with attempts and hours in Am.6-excluded hours or restart gaps dropped: `A_L` < 12 or counted hours < 24 makes every
>    KNN-C read NOT_DECIDABLE, written before any outcome is opened; entries halt at the next 00:00Z. Only decision timestamps and row kinds are
>    read.
> 8. **Deciding cell.** The 1.9 s leg, 0.25 SOL, rent 0 (Amendment 2; the V-band check is the screen's first predicate), the rent-charged
>    leg beside it. Only L is promotable. Age, and the 2 h predicate that implies R9(b), come from the arena-tape pool index and are named as
>    `path:line@frozen_sha`.
> 9. **Disclosure.** The design followed P86 (in-sample IC, book means and, in v1, the survivorship corrections: book -8.185 / -9.150 pp, U
>    -14.267 / -15.261 pp, refit IC 0.244 / 0.242), P78 and P87, on exploration dates read by >= 14 families. `m` comes from a pinned rule
>    using explore-0814 labels (dead pools at -100%) only, and is expected to be 0. KNN-C is a sibling of HV7 under Am.3 8(e). Walk-2-hour
>    information (P0 on arena days): arena-only, forever. Attempt counts depend on earlier outcomes through `M_D`; E1 and B can end a read for
>    lack of attempts and cannot create a pass. The universe overlaps C1-NF's at ages 2-24 h, with no grid or exit match.
> 10. **Unchanged:** the promotion gate and every threshold (n >= 100; >= 5 UTC dates with a majority positive; CI90 lower bound > 0, 1,000
>    draws, seed 1, 5th percentile; ex-top-3 > 0; flat 15% and pressure-fail at slope scale 1); one read per `bot_id`; the 5% and 10% caps;
>    N and c; Amendments 2, 3, 4, 6 and 15 as merged; the seals and §3. No book has cleared the promotion gate.

## 9. What was not measured

`m`, the gate-open share (live or on exploration with -100% labels), P0 counts, any November IC, the band mix of P86 candidates, the survivor
per-trade sd s, the death rate on the arena tape, pressure-model figures with deaths, the cost of the 45 s wait and of the 1.9 s leg on this
screen (`lambda_b` is a proxy), RSS under load, and whether pegged pools enter the top 8. The MDE figures are a synthetic simulation. No profit
claim.

## 10. Status: SHELVED / REDESIGN, and the two revival steps

**Decision (manager17, 2026-10-27, on QP 6446421063 and reviewer 6446421595):** KNN-C is shelved as designed. K1 to K5 are not built; it is
not registered; no N is spent. This PR merges as a parked design record. Revival only through:

- **(a) Cheap gate first (QP item 11 (i)).** Build K0 only (section 6.2), then run the `m` job plus a **gate-share count on exploration data
  with -100% labels**, as a MiScusi job. Memory is explore-0814 with section 2's labels, scored on the 285 pinning hours (features only, labels
  refused, section 3.3), giving gate-open hours per hour scaled to a day. This is outcome-safe as designed: no fresh-0903 or exp011-0909 label
  is loaded. **If fewer than 7.2 hours a day would open, KNN-C stays shelved for good as designed**, at near-zero cost. If 7.2 or more open, a
  new design record cites the count and re-reads section 0's expected value before K1 is considered. The expected value is still FAIL; a
  count that clears 7.2 a day removes only the P0 kill, not the cost problem.
- **(b) kNN as a veto on a near-zero-base universe (lane P91; QP item 11 (ii)).** This is the redesign that could reach profit. The kNN, or its
  structural features, is used as a veto or ranker on a universe whose U at the arena stack, with strands at -100%, is within about 1 pp of
  zero. That universe must first be shown on exploration counts and an exploration U, with the P86-style IC re-measured on it. If no such
  universe is found, shelve. P91's own design and pre-registration govern it. Nothing here pre-registers it.

Library row `p88-knn-c-forward`: status `untested` (never run), with this verdict in `why_stopped` and these two steps in `revive_trigger`
and `next_step`.
