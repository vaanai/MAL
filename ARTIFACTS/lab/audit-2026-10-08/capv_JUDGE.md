# capv_JUDGE: verdict on the CAP-PICK verification (audit 2026-10-08)

Judge for the three CAP-PICK verifiers: `capv_cleanroom.md`, `capv_leakage.md` and `capv_robustness.md`. The claim is in `g_reachable_cap_book_rescore.md` §5, with scripts in `work/g_reachable_cap_book_rescore/` (called `G/` below) and the pre-declaration in `G/prereg.json` (07:12:38Z). SYNTHESIS (`reports/SYNTHESIS.md` §5–§6, A1) already names CAP-PICK the single critical-path candidate.

**Scope.** Read-only. I used exploration-pool rows only: G's `rows_*.parquet`, robustness's `surf/*.parquet`, the d08 cache table and the EXP-012 training table for `time_to_migrate_s`. I read lab code for definitions only. I opened no sealed block and no forward or runner output, and priced nothing at or after 2026-10-02T10Z. Every script ran under `systemd-run --user --scope -p MemoryMax=4G nice -n 19`. Judge scripts and outputs are in `/data/mal/audit-1008/work/capv/judge/` (called `J/` below). Written 2026-10-08T10:19Z.

**Labels.** [measured] = computed here or copied from a cited file. [inferred] = reasoned from measured numbers. Every figure below is the mean SOL per attempt as % of stake unless it says otherwise. "date lo" is the date-cluster CI90 lower bound (1,000 day resamples, seed 1).

## Verdict: HOLDS_WEAKER

**The arithmetic holds exactly. The edge it implies does not.**
- Two verifiers matched the claim to 0 lamports on all 24,272 attempts. The clean room matched it to within 0.1 pp. I reproduced the pick book again (`J/out/j1_repro_controls.txt`).
- The BOOST mechanism is real in the pool, and no look-ahead was found.

**+3.819% is not an out-of-sample edge, and it is not the book MAL would trade:**
1. **Out-of-sample only for the model's weights.** The 300 s cap (`d07_exits.md:7`), the seed×1.15 guard (d07 §4) and the picks × short-exit pairing (d05 §5) were all chosen on these same blocks, after about 20,000 cell-block reads.
2. **New, not raised by any verifier: the live EXP-012 gate cannot trade 8.0% of the P2–P4 picks**, and those picks are the best ones (+7.571%).
   - `tools/forward_exp012_gate.py` (module note 5) gives `no_features` to any mint that migrates more than 60 minutes after create. The offline builder that made G's scores still scores them.
   - On the live-tradable set, P2–P4 is **+3.492%**, not +3.819%. P1 is +1.419%, and oracle is −0.985%.
3. **The latest pre-October data is about zero at the SYNTHESIS deciding cell.** On P1 (0.1 SOL, flat leg, tradable picks):
   - at 1.3 s: +1.24%, with ex-best-day −0.034 SOL;
   - at the binding 1.9 s leg: −0.37% (3/7 days positive);
   - oracle-insample alone: −0.87% at 1.3 s and −2.72% at 1.9 s.
4. **The pick book's daily mean falls.** On the tradable set: Spearman −0.347, p 0.0409.

**Best honest estimate:**
- About **+1.5%** per attempt out of sample for August–September.
- About **+0.3% to +0.5%** for October (P(≤ 0) about 40%).

**CAP-PICK should stay the single pre-registered candidate**, because nothing else has positive evidence at a reachable operating point. But treat it as a cheap kill test with about a **10%** chance of passing, not as the route to profit. The spec needs the fixes in §4 first.

## 1. What the three verifiers established, and where I settle their disagreements

| Point | cleanroom | leakage | robustness | Judge |
|---|---|---|---|---|
| Reproduction | Own sim, own code: P2–P4 +3.847 vs +3.819; ALL +3.405 vs +3.379; oracle −0.389 vs −0.414; D7 +0.789 vs +0.784 | Exact, 0 lamports on 24,272 attempts | Exact, 0 lamports for P, D2, D11 | Reproduced: P2–P4 3.819 [1.84] 20/28, flat 3.298, pressure 2.753, P3–P4 3.408 [1.36], P1 1.841 [−0.30] [measured] |
| Look-ahead | none found | none material; every input knowable | not examined | Agree: none |
| "Never trained on" | true for the training rows, not for the exit | true for the weights; the cap was picked on P2 (63% of P2–P4 pick attempts) | not true for the design; about 20k reads before the pre-declaration | Agree: out-of-sample for the model, in-sample for the book |
| Which picks apply live | documented features give +3.558; "the live runner decides" | — | — | **Neither.** The live gate truncates features at exactly 32 min (G's offline pipeline does so at the first flush after 32 min) and skips migrations over 60 min. Live-tradable P2–P4 is +3.492 (§2.1) |
| Pick lift = exposure? | — | — | "much of it is exposure"; per fill it reverses on exp011 and oracle | **Partly corrected.** Against an exposure-matched control (fill share 0.88), the pick premium is +3.553 on P2–P4 and +5.078 on P1. The oracle "reversal" comes from instant pools in the comparison set. Only exp011 truly reverses: −3.426 (§2.2) |
| Expectation | holds_weaker; no number given | holds_weaker; P3–P4 +3.408 | Aug–Sep +1.7 to +2.0; October central +0.5 | Aug–Sep about +1.5 (live-tradable); October +0.3 to +0.5 (§3) |

All three returned holds_weaker, and I agree. None returned does_not_hold. The claim's own text reports the negatives: oracle −0.414, the falling trend, and "not out-of-sample evidence". What fails is the use made of it: SYNTHESIS A1 cites "+3.819% … out of sample for the model" as the reason CAP-PICK is the candidate.

## 2. Judge checks [measured]

### 2.1 The live gate's pick set (`J/j3_slowmig.py`, `J/out/j3_slowmig.txt`)

The live gate is `tools/forward_exp012_gate.py`, the code the fast-0 paper runner uses and that SYNTHESIS A5 re-points to CAP-PICK. Its module notes list how it differs from the offline builder:
- note 4: truncation at exactly 32 min;
- note 5: a mint that migrates more than 60 min after create gets `no_features`;
- notes 7–8: mints created before the 00:00Z restart, or while the runner is down, are never gated.

`time_to_migrate_s` comes from the d08 cache table (f0) for P2–P4 and from `/data/mal/exp012/table.jsonl` for P1. It is known for all 3,009 picks.

| Scope | Picks | Over 60 min (share, mean) | All picks | Live-tradable (≤ 60 min) [date lo] days, ex-best-day |
|---|---:|---|---:|---|
| P2–P4 | 2,340 | 188 (8.0%), +7.571 | +3.819 | **+3.492 [+1.50] 20/28, +27.60 SOL** |
| P1 (OOF) | 669 | 44 (6.6%), +7.835 | +1.841 | +1.419 [−0.96] 5/7, −0.29 SOL |
| oracle | 381 | 24 (6.3%), +8.079 | −0.414 | −0.985 [−2.69] 2/4 |
| ALL | 3,009 | 232 (7.7%), +7.621 | +3.379 | +3.025 [+1.29] 25/35 |

- The clean room's "g-only" picks, the 128 averaging +12.479%, are probably mostly this slow-migration slice [inferred]. Every clean-room feature mismatch was a slow migration, with a median time-to-migrate of 11,008 s.
- The clean room's documented-definition picks (+3.558) assume no truncation at all, which is not what the live gate does.
- For the 32–60 min band (69 P2–P4 picks), online and offline features can still differ at the truncation edge. The replay helper `tools/forward_exp012_replay.py` measures this. I did not.

### 2.2 Exposure-matched control and the cap's gain by block (`J/j1_repro_controls.py`)

The control is guard-passing **organic, slower (non-instant), scored non-picks**: same cell, live leg, 0.5 SOL, fill share 0.88–0.92 against picks' 0.83–0.93.

| Scope | Control book | Pick − control per attempt [date CI90] days | Cap − 30-min hold on picks (paired) [date lo] |
|---|---:|---|---|
| explore | −0.407 | +4.466 [+1.57, +7.46] 12/14 | +3.323 [+2.05] |
| fresh | −0.744 | +4.747 [+1.46, +8.13] 5/7 | +2.918 [+0.95] |
| exp011 | +6.180 | **−3.426 [−7.23, +0.79] 2/7** | +2.031 [−0.13] |
| fast-pool (OOF) | −4.716 | +9.540 [+6.87, +11.85] 3/3 | +2.303 [+1.25] |
| oracle (OOF) | −1.916 | +1.502 [−0.79, +3.41] 2/4 | **+0.217 [−0.72] 2/4** |
| P2–P4 | +0.266 [−0.64] 14/28 | +3.553 [+1.36, +5.59] 19/28 | +3.019 [+2.01] |
| P1 | **−3.238 [−4.28] 1/7** | +5.078 [+2.04, +8.20] 5/7 | +1.115 [+0.20] |

**What this shows** [inferred]:
- **The EXP-012 selection carries the book.** Without it, the same capped, guarded book of slower organic pools is negative on P1.
- **There is therefore no simpler structural fallback.** The selection premium is large but unstable by block, and P1's premium uses adjacent-day OOF scores.
- **The cap's own gain on picks also decays**, from +3.3 to +0.2 pp, while BOOST is still detected on 99.6% of fills. Erosion of the BOOST-end effect is not something the A3 structure monitor can see; only the read can.

### 2.3 SYNTHESIS A1's deciding cell on exploration rows (`J/j4_deciding_cell.py`)

The cell is 0.1 SOL, guard 1.15, cap 300 s, live-tradable picks, built from robustness's `surf` rows with a pressure intercept fitted on the selection's fills. Entries are mean [date lo] days, then ex-best-day SOL.

| Leg, entry, exit lag | P2–P4 | P3–P4 | P1 (OOF) | oracle |
|---|---|---|---|---|
| flat, 1.3 s, lag 2 (primary) | +2.93 [+1.21] 20/28, +4.616 | +2.86 [+1.08] | +1.24 [−0.82] 5/7, **−0.034** | −0.87 [−2.39] 2/4 |
| flat, 1.9 s, lag 2 (binding latency leg) | +2.71 [+0.96] 19/28 | +3.36 [+1.29] | **−0.37 [−2.28] 3/7** | **−2.72 [−3.79] 0/4** |
| flat, 1.3 s, lag 4 (≈ 1.07 s at 267 ms) | +2.18 [+0.52] | +2.02 [+0.18] | +0.75 [−1.35] | −1.23 |
| pressure, 1.3 s, lag 2 | +2.43 [+0.98] 19/28 | +2.28 [+0.66] | +1.39 [−0.42] 5/7 | −0.61 |
| pressure, 1.9 s, lag 2 | +2.16 [+0.71] | +2.46 [+0.69] | −0.10 [−1.86] 3/7 | −2.32 0/4 |

- A2's kill check is stated on P2–P4: flat ≥ +1.0, date lo > 0, mean > 0 at 1.9 s. It passes by construction (+2.93 and +2.71), so it tests the scorer, not the book.
- The latest regime at the deciding cell is about zero, and negative at 1.9 s.

### 2.4 Trend (`J/j5_trend.py`)

Pick book, daily means over 35 day-units:

| Picks | Leg | Spearman (p) | OLS slope (p) |
|---|---|---|---|
| All | live | −0.362 (0.0328) | −0.1194 pp/day (0.1175) |
| Tradable | live | −0.347 (0.0409) | −0.1195 pp/day (0.1213) |
| Tradable | flat | −0.347 (0.0409) | −0.1032 pp/day (0.1213) |

A straight-line fit gives −2.0% (flat) at 10-20. That extrapolation rests on a weak slope and is not an estimate; I use it only as a downside anchor.

### 2.5 The read's false-pass rate depends on day clustering (`J/j2_power_cluster.py`)

**Setup.** I re-ran SYNTHESIS's 7/14/21 design (the `capick_v4.py` model; gate on both legs plus one-sided p ≤ 0.005 / 0.008 / 0.012; binding day-7 futility) with 3,000 simulations per cell. Its p-value is trade-level, as is DEC-021's one-sided bootstrap (`DEC/DEC-021-champion-challenger.md:57`). I compared it with a day-level p (one-sided t on day means, W−1 df), and inflated the between-day SD over the Aug–Sep template (3.09 pp, flat).

| Between-day SD | False pass, trade p | False pass, day p | Power at +1.0% flat, trade / day p | Power at +1.9% flat, trade / day p |
|---|---:|---:|---|---|
| 3.09 (template) | 2.8% | 0.8% | 15.4% / 5.1% | 45.3% / 22.2% |
| 4.63 (×1.5) | 6.0% | 0.9% | 22.7% / 4.5% | 47.8% / 15.2% |
| 6.18 (×2) | 11.7% | 1.0% | 28.1% / 3.7% | 48.0% / 10.4% |

- **The DEC-021 family α of 0.025 holds only if October's day-to-day variance is no larger than August–September's.** October brings a program redeploy, 200 ms slots, per-trade V and a declining trend, so that is not assured.
- **P(pass) for SYNTHESIS's design**, integrated over a prior on the true October flat edge (trade p, template variance, primary leg only):

  | Prior on the flat edge | P(pass) |
  |---|---:|
  | N(0.43, 1.0) | 13.8% |
  | N(0.43, 1.5) | 19.2% |
  | N(1.55, 0.8) (Aug–Sep adjusted) | 35.8% |
  | N(1.9, 1.0) (SYNTHESIS-like) | 45.2% |

- **With the binding 1.9 s leg as well** (about 1.6 pp below 1.3 s on P1), I put the overall P(pass) at **about 10%**, and about 5–7% with a day-level p [inferred]. SYNTHESIS states 25–30%.

## 3. Best honest point estimates and ranges

These are for the book MAL would trade: picks the live gate can score, seed×1.15 `min_out`, 300 s wall-clock cap, 55k per send.

| Quantity | Point | Range | Basis |
|---|---:|---|---|
| Claimed (P2–P4, live leg, 0.5 SOL) | +3.819 | date lo +1.84 | Reproduced exactly [measured] |
| Same, picks the live gate can trade | +3.492 | date lo +1.50 | §2.1 [measured] |
| Same at the deciding cell (0.1 SOL), flat / pressure | +2.93 / +2.43 | date lo +1.21 / +0.98 | §2.3 [measured] |
| **Out of sample for the whole book, Aug–Sep regime, live leg** | **≈ +1.5** | +0.5 to +2.5 | Live-tradable +3.49 less a design-selection haircut of 1.8–2.1 pp (robustness: EB +1.74 to +1.97 on +3.819; best-of-10 +1.88; P2–P4 → P1 drop) [inferred] |
| Same, gate legs at 0.1 SOL: flat / pressure | ≈ +1.3 / ≈ +1.1 | — | Scaled by the measured flat/live and pressure/live ratios [inferred] |
| **October (post-upgrade), live leg** | **≈ +0.3 to +0.5** (flat ≈ +0.3) | True mean about −1.5 to +2.0 (80%); P(≤ 0) about 40% | See October anchors below [inferred] |

**October anchors:**
- the Aug–Sep level, +1.5;
- the latest regime at the deciding cell: P1 +1.24 flat, with ex-best-day < 0, and −0.37 at 1.9 s; oracle −0.87;
- the falling trend and the decaying cap gain (§2.2, §2.4);
- unpriced regime changes: the 10-02 redeploy, 200 ms slots from about 10-09T14:34Z, per-trade V, synthetic migration, and BOOST cadence set off-chain. BOOST was still on in a 10-graduation October sample, with spans of 328–348 s.

This agrees with robustness's +0.5% central on all picks; the live-tradable set runs about 0.3 pp lower in exploration.

**A 21-day realized mean adds noise on top.** Its SE is about 1.05 pp on the flat leg (SYNTHESIS model: residual SD 34.47%, between-day SD 3.09%, 88.5 attempts/day), so ±1.7 pp at 90%.

**Economics** [inferred]:
- **Attempts:** about 77 live-tradable pick attempts per day (2,152 over 28 P2–P4 day-units).
- **Break-even against the ~0.11 SOL/day running cost (SYNTHESIS A1):** about 1.4% at 0.1 SOL, 0.6% at 0.25 SOL and 0.3% at 0.5 SOL.
- **At the October central:** CAP-PICK does not pay MAL's running cost at the stakes a 1–1.5 SOL bankroll carries.

## 4. Spec problems to fix before the CAP-PICK (EXP-022) Part 1 is merged

SYNTHESIS A1, A2, A3, A8 and A9 already settle these, so they need no change:
- flat 15% and pressure as the deciding legs, with live 1/62 report-only;
- 0.1 SOL deciding stake;
- every parameter in ms;
- the event-V decoder;
- the InitBoost and V-band universe;
- the structure-halt rules;
- scorer reproduction to ≤ 0.01 pp;
- no use of forward-1002 `[10-03, 10-10)` as gate evidence.

**Must fix (blocking):**

1. **Pin the pick set to the gate that will trade.**
   - Either change `forward_exp012_gate.py` so it keeps accumulators until migration, with the 32-min truncation unchanged; or make the scorer reproduce the gate exactly: truncation at exactly 32 min, `no_features` over 60 min, the 00:00Z-restart dead mints and downtime skips.
   - Require md5 decision-equivalence between scorer and runner on a `forward_exp012_replay.py` replay before the first counted hour.
   - Restate every exploration figure on that set: P2–P4 +3.492, not +3.819.
   - Do not use the clean room's untruncated rebuild (+3.558); it is a third pick set.
2. **Expected effect, power and framing.**
   - Remove "+3.819% out of sample" as the effect size. P2–P4 is out of sample for the model only; the cap, guard and picks × short-exit pairing were selected on it.
   - State the honest expectation (Aug–Sep ≈ +1.5% live / ≈ +1.3% flat; October ≈ +0.3% to +0.5%).
   - State the power at that expectation (about 6% at +0.5% flat; about 45% at +1.9%) and P(pass) of about 10%.
   - Pre-declare that a fail is the expected outcome and goes to A12, and that a pass would be surprising.
   - This is a selection haircut. D6's "no pre-registered haircut" covers execution only, so it does not apply.
3. **Make the alpha robust to day clustering.** DEC-021's trade-level one-sided bootstrap p gives a 2.8% / 6.0% / 11.7% false pass at 1× / 1.5× / 2× the template's between-day SD (§2.5). Choose one of these in the O2 amendment:
   - (a) a day-level p, one-sided t on day means. False pass stays near 1%; power at +1.9% flat falls to 22%.
   - (b) the trade-level p plus a pre-computed threshold table keyed to the realized between-day SD.

   Either way, report both p-values at every look.
4. **Decide the binding latency leg now.**
   - Keep 1.9 s binding (mean > 0 and ex-top-3 > 0 on both legs), or replace it with the A6-measured p90 only if A6 reports before the first counted hour. No switch after counting starts.
   - Disclose that on P1 this leg is −0.37 flat (3/7 days) and on oracle −2.72 (0/4), so it is the likeliest failure point.
   - Pin the entry anchor: the pool-create slot = first PumpSwap print. Leakage measured s0 − migrate slot at p50 0, p99 2.
   - Pin the ms → slot mapping (per-hour measured ms/slot, with an explicit rounding rule; 1.3 s is 6.5 slots at 200 ms) and the END bound.
5. **Exit lag.**
   - Keep 550 ms as the primary and 1.35 s as the pessimistic leg, as `probe-final-2026-10-07.md` recommends (lag 2 / lag 5, or resample the 46 mark-consistent trades). The leakage verifier's p90 8–10 tail is an entry-gap artefact per that note.
   - State whether the 1.35 s leg is binding or report-only, and that it applies to the timer-fired deadline sell too.
   - Lag 4 (≈ 1.07 s at 267 ms) costs about 0.75 pp at the deciding cell on P2–P4 (§2.3).
6. **Guard formula, exactly as the executor will send it.**
   - Net-of-fee or gross input: G's net guard is ≈ 1.1646× the seed in gross terms, and a gross guard is stricter, +4.117 on P2–P4.
   - Which fee tier is used, the V0 source at decision time (pool account read; per-trade V since 09-30), and the rounding of `min_out`.
   - Synthetic-migration pools above the seed count as rejects at −fee.
   - The executor currently takes `min_out` from the fresh quote (`tools/probe_executor.py:988-1007`, `:1210`). The replacement needs md5 equivalence before any live use.
7. **Cap anchor.**
   - 300 s on the landing slot's block_time, or the executor's landing clock. Not a day-mean slot count; the block_time cap changes P2–P4 by −0.06 pp per leakage.
   - The deadline sell lands at cap + exit lag. The live executor fires it from a pre-signed timer.
8. **Unit and gate conditions.**
   - The deciding statistic is the mean per attempt. Guard rejects and failed sends count at −fee. Mints that are unscored or skipped are not attempts.
   - "Trades" means fills for the ≥ 100 count. Report the per-fill mean too.
   - Add **ex-best-day > 0 on both legs** as binding. That is lab practice after EXP-017. Per-block top-day shares are 63–78%, and P1's ex-best-day at the deciding cell is already −0.034 SOL.
9. **Pressure leg.** Write the rule out: slopes at scale 1, intercept fitted so mean p = 0.289 over the read's own fills for that cell. That is `tools/latency_curve.fit_curve` and G's rule.
10. **Rent and dust.** Token-account rent is refunded when the account is closed in the sell tx (`probe_executor.py:1304`; the probe measured 1,513,840 lamports charged and refunded, net 0 over 61 trips). It is charged on any trip whose sell fails or leaves dust: 1.5–2.0% of a 0.1 SOL stake (the lab constant is 2,039,280 lamports). G's sim charges none, so the A2 scorer must add it.

**Report-only additions (not binding):**

11. **Pick − exposure-matched control.** The control is guard-passing organic, slower non-picks, fill share ≈ 0.88. Report it alongside "pick − guarded baseline" (fill share ≈ 0.4). Also report:
    - the paired cap − 30-min gain on picks, which was +0.217 on oracle;
    - first-half versus second-half means;
    - the exit-type split (tp / sl / deadline) and deadline-exit fill quality.
12. **Disclose:**
    - the own-buy constant-offset approximation: about +0.1 to +0.2 pp per attempt optimistic [inferred from leakage];
    - the 1/62 fail rate was measured at 500k priority, not 55k;
    - the October BOOST sample is n = 10;
    - P1 is OOF on adjacent days, and its threshold was cut on those same scores;
    - A2's P2–P4 kill check passes by construction. Add P1 as a disclosed line.

**Timing.** Do not merge the Part 1 without items 1–10. If that misses 10-10T00Z, count from 10-16T01 and run the A11 October report-only check. Do not rush an under-specified pre-registration to gain six days.

## 5. Should CAP-PICK still be the single critical-path candidate?

**Yes, as the single pre-registered candidate, but re-scoped.**

**Why it stays:**
- No other family has a positive measured book at a reachable operating point (SYNTHESIS §5.4).
- Its selection is load-bearing: the exposure-matched structural book is −3.238% on P1.
- Its mechanism was still present in October (n = 10).
- The read costs 0 credits.
- A second promotion arm would split α without a better prior.

**What changes:**
1. **Odds.** Tell the owner P(pass) is about 10% (5–20%), not 25–30%. Tell them the honest October edge (about +0.3% to +0.5%) is below running cost at 0.1–0.25 SOL. The likely result is A12; pre-agree O7 now.
2. **Spend.**
   - Fund now only what the read needs: A1 with §4, A2 (plus the rent, gate-replication and P1 lines), A3, A8, A9, and the gate pick-set fix.
   - Defer A5 (the 2.5–3 builder-day live path) and A6 / A7 until a look is not futile: A11 if counting from 10-16, otherwise day 7. At P(pass) ≈ 10%, building first wastes about 90% of that effort.
   - A delayed live start after a real pass costs a few days at about +0.03 to +0.16 SOL/day.
3. **Kill.**
   - Keep the day-7 futility and A11 rules binding for spending.
   - If the A3 monitor halts, or the pick-set replay shows the live gate cannot match the scorer, withdraw before counting.

## 6. Not covered

- I did not run `forward_exp012_replay.py`. The size of the online/offline difference in the 32–60 min band, and the daily-restart skips, are unmeasured.
- No October market data was read. BOOST in October rests on the n = 10 sample in `g_october_structure_check`.
- I did not re-simulate entries or exits. The deciding-cell numbers reuse robustness's `surf` rows, and exit lag 4 stands in for 1.35 s.
- The P(pass) for the binding 1.9 s leg is inferred, not simulated jointly.

## Files

`/data/mal/audit-1008/work/capv/judge/`:
- `j1_repro_controls.py` → `out/j1_repro_controls.txt`, `out/j1_books.csv`: reproduction, exposure-matched control, paired cap gain;
- `j2_power_cluster.py` → `out/j2_power.txt`, `out/j2_power.csv`: alpha under day clustering, prior-weighted P(pass);
- `j3_slowmig.py` → `out/j3_slowmig.txt`: the live gate's 60-minute skip;
- `j4_deciding_cell.py` → `out/j4_deciding_cell.txt`, `out/j4_deciding_cell.csv`: SYNTHESIS A1 cell, 1.3 s / 1.9 s × lag 2 / 4, three legs;
- `j5_trend.py` → `out/j5_trend.txt`: daily trend, all and tradable picks.
