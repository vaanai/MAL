# EXP-025 Part 1: C1-NF post-graduation momentum forward read (two looks), pre-registration

**Written and merged before the first counted hour.** Written 2026-10-09. To write it, no row of a sealed block (fresh-0802, fresh-0808, fresh-0828) was opened, and neither was any forward-1002 or forward-1016 file, any forward-paper or runner output, or any key. The exploration tape was opened for one purpose only: the wallet-ledger determinism check (`ARTIFACTS/exp025/ledger/determinism_check.log`, section 11.1). Its inputs are `/data/mal/hunt-1008/JUDGE-4.md` (sections 3.3 and 3.4), the C1 hunt's frozen files, and the adversarial VERIFY (`ARTIFACTS/exp025/verify/VERIFY.md`, `results.json`), all copied into the repo and pinned (section 2.3). This file is docs only. The October adapter, the read tool and the pick oracle are separate PRs (section 11). Nothing in this file says or implies that any book is positive. **The VERIFY numbers are exploration on four spent September blocks. They are not evidence of an edge** (section 9).

| Field | Value |
| --- | --- |
| **ID** | `EXP-025-c1nf-part1-prereg` |
| **Status** | **planned**. Counting starts at 2026-10-10T00 (section 0). |
| **Declared (UTC)** | 2026-10-09. It must merge before 2026-10-10T00:00Z (target 2026-10-09T23:00Z). The owner's choice of a two-look design and his OK on DEC-025 O1, O2 and O3 were relayed by the manager on 2026-10-09 (DEC-025 `OWNER_DECISION_CONFIRMED`). |
| **Parent** | hunt-1008 / c1-cascade-postgrad: `RULE.md` (sha256 `a22cebc481f36cda70fff07663e525b5b72ebee8741c843b7489038dbd49281c`), `REPORT.md`; `/data/mal/hunt-1008/JUDGE-4.md` section 3.3 (ranks C1-NF third of the hunt 1-4 survivors); `c1nf-verify/VERIFY.md` (verdict SURVIVES-VERIFY). |
| **Rules** | [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md) (ledger, Holm, block budget, family lineage), [DEC-016](../DEC/DEC-016-exp012-forward-on-chain-hours.md) (forward-1002, seal, FINAL; Am.7, Am.8), [DEC-021](../DEC/DEC-021-champion-challenger.md) section 8, Am.1 to Am.3, [DEC-023](../DEC/DEC-023-h5-family.md), [DEC-025](../DEC/DEC-025-c1nf-family.md) (this family), [EXP-022](EXP-022-cap-pick-part1-prereg.md) section 9 and Amendment 4, [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md). |
| **Hypothesis** | The frozen C1 cascade plus the top-holder cap (C1-NF, section 2) has mean SOL per trade > 0 at 0.25 SOL on October post-graduation decisions, under both fail models, at a 1.3 s entry, 505,000 lamports per send and rent on every fill (section 6), and clears the gate and the day-level test at that look's alpha (section 7). Look 1 counts decisions in `[2026-10-10T00, 2026-10-17T00)` (7 dates). Look 2, only if Look 1 did not pass, counts the cumulative `[2026-10-10T00, 2026-10-24T00)` (14 dates). |
| **Kill condition** | Neither look passes (section 7); or a refusal fires (section 11.4, NOT_DECIDABLE); or a precondition fails (section 10). Two looks. No retune, no re-cap, no re-read. |
| **Expected outcome** | **Unknown, probably a fail.** The edge was found after the read on spent blocks. Conditional on the stated model and the placeholder alpha pair, P(pass at either look) is about 0.67 if September holds, 0.23 if the edge is half and 0.08 at a quarter, at 20 trades per day (0.43 / 0.15 / 0.06 at 12 per day; section 3). |
| **Tools** | **Read:** a locked read tool built on the October adapter (section 11). **Pick exclusion:** `pick_oracle(mint) -> bool` (section 5). **Determinism:** `ARTIFACTS/exp025/ledger/01_wallet_daily_det.py`. **Power:** `tools/exp025_power.py`. |

**Labels.** [measured] is copied from a cited file that computed it. [pinned] is a design value this file fixes; it is not evidence. [inferred] is reasoned. [est] is an estimate. Numbers from VERIFY are copied, not rounded up.

## 0. Counting start, looks and alpha (the pinned lines)

```
EXP025_COUNT_START: 2026-10-10T00
EXP025_LOOK1_END: 2026-10-17T00
EXP025_COUNT_END: 2026-10-24T00
EXP025_ALPHA_LOOK1: 0.008
EXP025_ALPHA_LOOK2: 0.017
```

- **Format.** Each line matches its pattern (for example `^EXP025_COUNT_START: 2026-10-10T00$`) exactly once. The read tool refuses unless all five do, and unless this file is clean against HEAD. The values never change after merge.
- **The alpha pair is one constant pair.** `EXP025_ALPHA_LOOK1` + `EXP025_ALPHA_LOOK2` = 0.025 / k (Bonferroni over looks; k = 1 at this merge, DEC-025 section 2). **0.008 and 0.017 are a placeholder** until quant-proof recommends the split. Replacing them means editing these two lines and the constants `ALPHA_LOOK1` and `ALPHA_LOOK2` in `tools/exp025_power.py`, then re-running the power table; `tools/test_exp025.py` fails if the lines, the constants or the sum disagree. Nothing else in the file depends on the values. After merge they are fixed.
- **Counted decision.** A selected row counts at a look if its decision time T (a whole UTC minute) satisfies `START <= T < LOOK1_END` (Look 1, 7 dates, 10-10 to 10-16) or `START <= T < COUNT_END` (Look 2, 14 dates, 10-10 to 10-23). The last counted exit of Look 1 lands before 2026-10-17T01:10 and of Look 2 before 2026-10-24T01:10; hours 2026-10-17T01 and 2026-10-24T01 are read for exits only.
- **Why 10-10T00.** A counted window must be fixed before its first hour begins in real time (ledger rule 4; EXP-024 section 0). This file can merge during 10-09, so 10-10T00 is the first full UTC day. Ledger feature hours before it are read as features only, after the FINAL (section 4).
- **If the merge is late.** If this file (with DEC-025, quant-proof OK on its final head) is not merged by 2026-10-10T00:00Z, the pinned dates are void. The counted start is then the first 00:00Z after the merge, `C`, set by a dated, outcome-blind amendment merged before `C`; Look 1 ends at `C + 7 d` and the cumulative end is `C + 14 d`. The alpha lines do not change. Nothing else changes. If `C` is later than 10-15T00, Look 1 has fewer than one forward-1002 date and the amendment must say so.
- **Not counted.** Every decision before 2026-10-10T00, whatever its source. The tip tape and the shadow (section 5) may have seen such hours; none is read for C1-NF's outcome.

## 1. Family status (DEC-014, DEC-025)

- **A new family, not EXP-012 or CAP-PICK lineage.** Under the DEC-014 clarification (2026-10-07) a family is a primary hypothesis. C1-NF's hypothesis is that a LightGBM entry model on post-graduation hot flows, wallet intelligence and creator history, restricted to non-farm pools (top-holder share <= 0.5), is positive on 5-minute holds. It uses information no earlier registered book used (the 107-feature post-graduation set and the prior-day wallet ledger). It has no EXP-012 model, threshold or pick list.
- **k = 1.** One deciding cell. The binding legs are conjunctive. No arm is added after merge.
- **Related, not shared.** r4-b (laya-opt) and H5 (EXP-024) select overlapping hot post-graduation tokens. Outcomes are probably positively correlated, so Bonferroni across the families is conservative, not anti-conservative. C1-NF's ranking power rests on the model (VERIFY section 6: the mean rises with the threshold), not on the cap alone.
- **Not a paired test and no sealed block claimed.** It is a forward read on October chain hours. DEC-014's p < 0.025 / m does not set its bar. It adds one family to m (**m >= 15**), because the C1 hunt read outcomes on the 27 non-P1 dates.

## 2. The frozen rule, copied exactly

### 2.1 C1's RULE.md, byte for byte

Source: `/data/mal/hunt-1008/c1-cascade-postgrad/RULE.md`, copy `ARTIFACTS/exp025/RULE.md`, sha256 `a22cebc481f36cda70fff07663e525b5b72ebee8741c843b7489038dbd49281c`, re-checked 2026-10-09. The block below is the file byte for byte (check: `sed -n '/^~~~rule$/,/^~~~$/p' EXP/EXP-025-c1nf-part1-prereg.md | sed '1d;$d' | sha256sum`).

~~~rule
# C1 cascade post-grad — FROZEN RULE (v2)

Frozen 2026-10-08 before any CONFIRMATION label is read. Chosen on DISCOVERY (graduation days 2026-08-14..2026-08-28) only.
The sha256 of this file, of `ml/rule.json`, `ml/rule_secondary.json` and of every script below is recorded in REPORT.md. None of them changes after that.

## Universe and decision points (scripts/10_meta.py, scripts/11_passA.py)
- Every pump.fun `complete` graduation whose canonical PumpSwap pool (hunt-shared `tokens.pool`) has V in [17.5e9, 17.7e9] lamports and whose first
  PumpSwap print is within [-5 s, +120 s] of the `complete` row. Known at graduation time; no survivorship filter.
- Decision grid: whole UTC minutes from grad + 10 min to grad + 24 h, clipped so decision + 3,600 s + 300 s stays inside the tape segment
  (S2 [2026-09-03T12, 2026-09-15T12), S3 [2026-09-18T23, 2026-09-25T07)). A point is alive if the pool printed in the previous 60 min.
- Features use only prints with slot < decision slot (= first tape slot whose block_time >= T), the prior-day wallet ledger (tape days strictly
  before the decision's UTC day), and as-of cross-token tables (scripts/12_passC.py).

## Stage 1 (LAYA filter)
`(v5 >= 1 SOL and surge >= 3) or new5 >= 20`, and real quote reserve (quote_reserve, without V) >= 20 SOL, inside the pass-A superset
`v5 >= 0.5 and (surge >= 1.5 or new5 >= 8)`. v5 = PumpSwap SOL volume of the last 300 s; surge = v5 / (v60 / 12 + 0.05); new5 = first-time buyers of
the pool in the last 300 s. (`ml/rule.json` "stage1": [1.0, 3.0, 20.0, 20.0].)

## Stage 2 (smart model) — scripts/16_confirm.py with ml/rule.json
- Features: the 107 columns exported by scripts/14_export.py (stage-1 flow features, trajectory shape, PumpSwap holder concentration, curve-holder
  selling, wallet intelligence of the 5 / 15 min buyers and sellers from the prior-day ledger, creator track record, narrative heat, market activity,
  pre-grad bonding stats, hour of day). No landing-time or future field.
- Model: LightGBM regression, objective huber (alpha 0.05), learning rate 0.03, 31 leaves, min_data_in_leaf 300, feature_fraction 0.7,
  bagging 0.8 / 1, lambda_l2 10, 400 rounds, seed 1, deterministic, num_threads 3 (mlcommon.lgb_params).
  Target y = clip(pnl_E3 at 1.3 s / 0.25 SOL, -0.5, 1.0) on stage-1 rows.
- WALK-FORWARD (pre-declared): for each CONFIRMATION UTC decision day D, retrain from scratch on every stage-1 row with t < D 00:00Z - 3,600 s
  (all DISCOVERY rows and earlier CONFIRMATION rows; every such label is resolved before D), then score day D's stage-1 rows.
- BUY when the prediction > 0.02 (2% of stake, net).

## Execution and exit (as pass A computed them; no refit)
- Size 0.25 SOL; buy lands at decision slot + round(1.3 s / measured seconds-per-slot of that UTC hour) (primary) or 1.9 s (binding), END bound,
  own trade applied, PumpSwap fee tier on (quote + V, base); 55,000 lamports per send on buy and sell; guard: the buy reverts (one send fee) if
  size / tokens out > 1.15 x the decision-time spot.
- Exit E3: time exit. Deadline = first print whose block_time >= block_time(last print at or before the landing slot) + 300 s; the sell fills at the
  state before the first print with slot >= deadline-print slot + ceil(0.55 s / s-per-slot) + 1 (state after the last print if none).
- Book: rows in time order; a mint holds at most one position; a selected row is taken only if t >= the previous exit time + 60 s for that mint.

## Fail legs and statistics (mlcommon.legs, mlcommon.gate)
- flat: a fill pays 0.85 pnl + 0.15 (-55,000); pressure: p = sigmoid(c + 0.8 log1p(same-slot buys at landing) + 0.35 log1p(buy SOL in the 2 s up to
  landing)), c fitted so the mean p over the book's fills is 0.289; a guarded row is -55,000 on every leg.
- Per leg: n, days, days positive, mean % of stake, trade-level CI90 lower bound (1,000 draws, seed 1, 5th pct), date-cluster CI90 lower bound
  (1,000 day resamples, seed 1), total SOL, ex-top-3, ex-best-day, per block, both latencies.
- Gate (both flat and pressure, primary latency deciding, binding reported): n >= 100, >= 5 days with a majority positive, trade-level CI90 lower
  bound > 0, total ex top-3 > 0. The date-cluster CI90 lower bound is reported next to it and called out if <= 0.
- Verdict: "promising" if the gate is met or nearly met on both legs; "weak" if positive but not near; "dead" otherwise.

## Secondary book (REPORT-ONLY, never decides the verdict) — ml/rule_secondary.json
Same everything, exit E7 (time exit 900 s), target pnl_E7, threshold 0.06. It measures whether the discovery "ramp farm" pattern persists.

## The one CONFIRMATION run
1. `14_export.py ml/conf.npz <graduation days 2026-09-03 .. 2026-09-25>` (all 21 confirmation graduation days present on the tape).
2. `16_confirm.py ml/rule.json ml/disc.npz ml/conf.npz ml/confirm_primary.json` and the same with `ml/rule_secondary.json` -> `ml/confirm_secondary.json`.
3. Copy the numbers into REPORT.md. No re-run with changed settings.
~~~

### 2.2 The one added line (the C1-NF cap), and the kept parts

**Added line [pinned].** After stage 2 (prediction > 0.02) and **before** the one-position-per-mint book, drop the row if `h_top1` > 0.5 or `h_top1` is NaN. `h_top1` is the largest positive cumulative PumpSwap net-token position among wallets that traded the canonical pool before the decision slot, divided by the sum of all positive positions; pre-graduation holders are not counted. The code is `ARTIFACTS/exp025/c1nf_cap.py` (`apply_cap_before_book`); `h_top1` itself is computed by the pinned `scripts/11_passA.py`. The comparison is `<=`: exactly 0.5 stays.

**Kept as frozen.** Stage 1, the 107 features, the LightGBM huber model and params, the daily expanding retrain (decision day D trains on every stage-1 row with t < D 00:00Z - 3,600 s: all 36 exploration days with farm rows included, as frozen, plus October rows before that cutoff), exit E3 (sell 300 s after landing), stake 0.25 SOL, the 1.15 x decision-spot guard (a guarded buy costs one send). `ml/rule.json` is pinned (section 2.3).

**October adaptations [pinned; not tuned]** (JUDGE-4 section 3.3.3):
- canonical pool found by PDA instead of the September V band;
- pricing Q = vault quote + per-print event V; "real quote >= 20 SOL" uses the vault quote net of pending fees, as decoded;
- seconds per slot measured per UTC hour; after about 2026-10-09T14:30Z slots are 200 ms, so the 0.55 s sell lag is 3 slots (`ceil(0.55 / s)`, as pass A computes it);
- the wallet ledger is built from tape days strictly before D: the exploration days plus October hours (section 11.2).

### 2.3 Pinned artifacts

All are copied into the repo (`/data/mal/hunt-1008` is `.nobackup`). `ARTIFACTS/exp025/SHA256SUMS` lists every file; `tools/test_exp025.py` checks it. The JUDGE-4 section 3.3.2 hashes all match [measured, 2026-10-09].

| Artifact (under `ARTIFACTS/exp025/`) | sha256 |
| --- | --- |
| `RULE.md` | `a22cebc481f36cda70fff07663e525b5b72ebee8741c843b7489038dbd49281c` |
| `rule.json` | `e158e0b9069a7db98a26c55b68f74055f44611330b2c636e68f37e4379c5c196` |
| `scripts/common2.py` | `39701e73a78a19e47f33eda05154efcf9095755bfe788e0d8762e83bd89e70bd` |
| `scripts/10_meta.py` | `5a25d0b122b5835ccea30922e73ce62a83e0a66af10d4f923495a09b7ba3346e` |
| `scripts/11_passA.py` (defines `h_top1`) | `24b1822e35a3b96337f8205370accc8eb9862d8411c9fda68296fb040ea79122` |
| `scripts/12_passC.py` | `22e6329415d40243ddcfab1b3f1793b25fd251cb9bf52be7e76e29300d6d0946` |
| `scripts/14_export.py` | `7dc2d151094defa349353fccdfdb92a6b542c9fa03828ad7dc77a3b3aecee868` |
| `scripts/mlcommon.py` | `bf783986e338cd924c2a99c5fbd3df2d5ec24d765772e654f44b5e63f4dca46f` |
| `scripts/16_confirm.py` | `c2fc497f141a9e650238beba26c4627eb51053a31e0e161ce6612cfc819ba0ae` |
| `scripts/x07_posthoc_gate.py` (the subset as evaluated) | `3daed3dd226f5288a4a391bc01a49976a15f2091fd1c0c1f448eaec41343812c` |
| `ref/confirm_primary_trades.npz` (C1's frozen primary book) | `a5e27eb6035cef20534fbdd99e150b992aae8687d4b7f93f28f36c1812dedbf2` |
| `scripts/01_wallet_daily_ORIGINAL_nondeterministic.py` (superseded; kept for provenance) | `0580faa05bd0add3f69c93b08f5a9675e06c227de0278d512a424c34687fc414` |
| **`ledger/01_wallet_daily_det.py`** (replaces the line above; section 11.1) | `bc1838f10ff1798dec1e896da34d1a82b9707b404d2e36de317edf0034bf3be5` |
| `ledger/determinism_check.py` / `.log` | `8da3ce73…485dc` / `6767a870…ea57b` (full values in `SHA256SUMS`) |
| **`c1nf_cap.py`** (the added line as code) | `e0335aeba5a9abec509e5e70d1070c77fd9e524b31456cb6c9ebdd7fc4ba6ba6` |
| `ref/convert.py` (walker JSONL to the hunt tape layout), `ref/build_shared.py` (`tokens.parquet`, `bars_1m`) | in `SHA256SUMS`; the layout reference for the adapter (section 11.2) |
| `verify/VERIFY.md`, `verify/results.json`, `verify/v2_sim.py`, `verify/v3_report.py` | in `SHA256SUMS`; the numbers cited here and the independent simulator that cross-checks the read tool (section 10, P4) |

**Training data (not in the repo; 0.5 GB).** VERIFY's rebuild produced `ml/disc.npz` (sha256 `4b68d55b93158c3967926fb9b41192ba2a5aee27075669b95af374bd0a582409`) and `ml/conf.npz` (`0dc37940dee7768086fe6f1f618c80fdc97b0a93fec80af01e19d902f4649e1d`) from a **non-deterministic** ledger. They are reference only. The training set the read uses is the rebuild on the deterministic ledger (section 10, P2), whose hashes are recorded in a dated amendment before 2026-10-16T01:00Z and copied to a backed-up path.

## 3. The two looks, the alpha pair and the power

**Design [pinned; the owner chose "Two looks", relayed 2026-10-09].**

| | Look 1 | Look 2 (cumulative) |
| --- | --- | --- |
| Counted decision dates | `[2026-10-10T00, 2026-10-17T00)`, 7 dates | `[2026-10-10T00, 2026-10-24T00)`, 14 dates |
| Tapes | 10-10 to 10-15 on forward-1002 (needs the FINAL); 10-16 on walk 2 (needs the oracle) | adds 10-17 to 10-23 on walk 2 (needs the oracle) |
| Hours read | forward-1002 `[2026-10-02T15, 2026-10-16T01)`; walk 2 `[2026-10-16T01, 2026-10-17T02)` | adds walk 2 to `2026-10-24T02` |
| Earliest run | about **2026-10-17T03Z**, after the last exit, the FINAL and the section 10 preconditions | about **2026-10-24T03Z** |
| Deadline | 2026-10-18T12:00Z | 2026-10-26T12:00Z |
| Runs | always | only if Look 1 did not pass (FAIL or NOT_DECIDABLE) |
| alpha | `EXP025_ALPHA_LOOK1` = 0.008 (placeholder) | `EXP025_ALPHA_LOOK2` = 0.017 (placeholder) |

- **PASS at the first look that passes**; the read then ends and Look 2 does not run. FAIL if no look passes. A look not run by its deadline is NOT_DECIDABLE and its alpha is not carried forward.
- **No futility rule.** A Look 1 FAIL does not end the read. (The manager may add one before the merge; it could only stop the read and would spend no alpha.)
- **Look 2 re-derives Look 1's decisions** for 10-10 to 10-16 from the same hours and the same pinned inputs. They must match by md5 or Look 2 refuses (R9). The daily expanding retrain uses only labels before each decision day, so a later look cannot change an earlier day's decisions.
- **Power** (`tools/exp025_power.py`, default mode `looks`; 1,500 simulations per row, Monte Carlo SE <= 0.013; table in `ARTIFACTS/exp025/power/power_table_looks.txt` and `.json`). The probability that **every** deciding item of section 7 holds on **both** fail legs at the deciding cell (1.3 s, 505,000 per send, rent on every fill). One simulated October serves both looks. The edge is relative to September's rent-inclusive effect (flat +7.458%, pressure +6.640%, VERIFY section 5.2).

| Edge | Trades/day | n at Look 1 / Look 2 | Look 1 | Look 2, if Look 1 did not pass | **Either look** | One 14-date read at alpha 0.025 (comparison) |
| --- | --- | --- | --- | --- | --- | --- |
| September holds | 20 | 139 / 278 | 0.161 | 0.505 | **0.666** | 0.713 |
| **Half the edge** | 20 | 139 / 278 | 0.039 | 0.191 | **0.230** | 0.264 |
| Quarter | 20 | 139 / 278 | 0.014 | 0.065 | **0.079** | 0.087 |
| September holds | 12 | 85 / 168 | 0.023 | 0.409 | 0.432 | 0.491 |
| Half the edge | 12 | 85 / 168 | 0.007 | 0.145 | 0.151 | 0.174 |
| Quarter | 12 | 85 / 168 | 0.002 | 0.057 | 0.059 | 0.075 |

  Placeholder pair (0.008, 0.017). The comparison column is one read of the 14 dates at alpha 0.025 (not registered). Look 1 is mostly a look for a large edge: at half the edge it passes 4% of the time. Look 2 carries nearly all of the power. Two looks cost about 0.05 of power at September's edge and 0.03 at half the edge, against one read of the 14 dates, and buy an answer on about 10-17.
- **Other splits (either look, 20/day; same run).** (0.005, 0.020): 0.681 / 0.247 / 0.082 at September / half / quarter. (0.010, 0.015): 0.647 / 0.215 / 0.077. (0.0125, 0.0125): 0.624 / 0.210 / 0.075. A tighter Look 1 raises the total at September's edge and at half the edge, because Look 1 spends alpha for little power. This is the comparison quant-proof can use to recommend the pair.
- **Earlier single-window table** (6, 7, 10 and 14 dates at one alpha): `ARTIFACTS/exp025/power/power_table.txt`. For the record: a 14-date single read gave 0.703 / 0.289 / 0.071 in that run.
- **A FAIL at half the edge would not prove the edge is absent.** Even over both looks the test is more likely to miss a real half-size edge than to catch it (0.77 miss).
- **12/day rows.** Walk-2 days lose the picks CAP-PICK took (section 5), and October volume is unmeasured. 12/day is a sensitivity, not a forecast.
- **Model and assumptions (all printed by the script).**
  - Per-trade returns are a four-part mixture calibrated to VERIFY's numbers: win rate 66.8%, 22 of 419 trades lose >= 90%, median +10.17%, p95 +63.17%, no-fail mean +9.625%, top-3 average about +165%, top-10 about +122% [measured, VERIFY sections 5 and 6, `results.json`]. The model's trade SD is 0.373 against 0.342 implied by VERIFY's trade CI (slightly conservative).
  - The between-day variance of September's daily means is below the trade-level noise, so the day effect is 0. The edge scales the positive parts, not the loss tail.
  - Flat failure 15%. The pressure leg uses one effective failure rate (0.2408), set so that September's rent-inclusive pressure mean is reproduced; the real leg has a per-trade rate.
  - **Not simulated:** the 1.9 s cell (report-only here), correlated fill failure, the other binding legs, the canary's own footprint, and any October regime change. The model has no October information.
- **Adverse fills.** The edge sits in the right tail of hot entries, so correlated fill failure is the main untested live risk. VERIFY section 6 [measured]: if the best 10% of fills failed, the no-fail mean (1.3 s, 505,000) would fall to +1.855%; if the best 28.9% failed, to -5.116%. The power table does not include this.

## 4. Data, windows and seal

| | Hours read | Source | When |
| --- | --- | --- | --- |
| Ledger and features | forward-1002 `[2026-10-02T15, 2026-10-16T01)` | `/data/mal/blocks/forward-1002` | after the FINAL is written |
| Counted decisions and exits | Look 1: forward-1002 to 10-16T01 and walk 2 `[2026-10-16T01, 2026-10-17T02)`. Look 2 adds walk 2 to `2026-10-24T02` | `/data/mal/blocks/forward-1016` (planned) | after the FINAL; walk-2 hours as sealed and verified |
| Training | the 36 exploration days (spent blocks) plus October labels before each cutoff | exploration tape; the two above | inside the one locked job |

- **Hours.** Every opened hour needs a `backfill_verify --content` OK line and a sha256 line (DEC-016:26). Pass A reads trade hours `D T00 .. D+2 T01` for the pools graduating on D; the decision grid runs to graduation + 24 h, so graduations from 2026-10-09T00 matter. Graduation-day D = 10-09 decisions run into 10-10; their trade hours are in forward-1002.
- **Counted selection.** A selection counts in the read if its decision time T is in the window (section 0). The universe is every `complete` graduation whose canonical pool is found by PDA (RULE.md universe).
- **Seal (from merge until the read).** No person, agent or job does any of these on any October hour, from any source (forward-1002, walk 2, the tip tape, the live listener, RPC, a paper runner or a live wallet), **except the declared observation in section 5**:
  - computes, opens or prints a C1-NF label, fill, exit, P&L, mean, CI or day sign for a decision whose T is in the window;
  - prices the C1-NF book on a counted decision.
- **What stays allowed:** hour counts (sealed, verified, bad); the section 11.3 precount, which is counts only; the A3 structure monitor's flags; building and testing the adapter on exploration hours.
- **No early forward-1002 read.** No C1-NF process opens any forward-1002 hour before the EXP-012 FINAL (A) report is written (DEC-016 Am.7 and Am.8). Its tool checks the FINAL marker, the entry in the external ledger `/data/mal/exp012-forward/FINAL_READS.jsonl`, and opens none of the files DEC-016 Am.2 keeps closed (`OUT/rows.jsonl`, `OUT/scratch/*.jsonl`, a FINAL `report.json` or `report.md`). It does not use the (B) V-map files.
- **A breach** is recorded here, dated, and the read is reported compromised. A compromised read cannot support a live request.

## 5. Declared observation, tip tape, and the CAP-PICK seal

### 5.1 Declared observation (the manager's ruling, 2026-10-09; modelled on EXP-024 section 3.1 and DEC-024)

- "The manager intends to run a C1-NF **paper shadow** and a small **live canary** (O3, approved; see below) before and during the counted windows. Their outcomes for decisions inside the windows may be watched in real time. This is declared before the window opens and before any shadow or canary trade."
- "The read's rule, data, analysis and pass bar are fixed by this file. Look 1 and Look 2 are always run (Look 2 under its own condition) and reported as written. Neither is skipped, delayed, re-scoped or re-thresholded because of anything the shadow or the canary shows. Nothing the shadow or canary shows may change any EXP-025 parameter."
- **The shadow is not the read.** It uses different inputs (the tip tape, a tip-tape ledger, its own V source), so its decisions will differ from the read's. The read never uses the shadow's trades, features or ledger, and the read's report states the declared observation and any disagreement the manager chooses to record.
- **A small live canary is approved (O3), relayed 2026-10-09.** DEC-025 `OWNER_DECISION_CONFIRMED` records the owner's words as the manager relayed them: "I'm also down to do some testing with some small trades in parallel." The manager's scope, which is his and not a quotation of the owner: 0.02 SOL stakes, a separate wallet, running before and during the counted windows, **under its own DEC-026, which is to be written and does not block this PR**. No C1-NF live order is sent until DEC-026 is merged and its own preconditions hold. The canary is separate from H5's DEC-024 canary and override. **Its outcomes fall under this declared observation:** watched live, with Look 1 and Look 2 fixed and always reported as written. **Scale-up beyond canary size needs a passed read or a further explicit owner override.** At 0.02 SOL against a stage-1 real quote of at least 20 SOL the canary is at most 0.1% of a pool's quote; its trades are real chain activity by one participant, appear in the tape as ordinary rows, and are not removed (that would edit chain truth). The read reports them. A paper shadow needs no DEC.
- **Provenance.** This subsection records a ruling relayed by the manager on 2026-10-09. The quoted sentences are the manager's wording. No owner's words are quoted.

### 5.2 The tip tape

- The fast-0 tip-follower tape (`tools/fast_tip_follower.py`; daily archive `/data/mal/tip-tape-archive`) may be used to compute C1-NF features and to run the shadow. It is a live capture. It is **not** forward-walk, forward-paper or runner P&L, and its use for features is not a read of any sealed output.
- It may not compute or print a C1-NF outcome (label, fill, P&L) for a decision in the window before the read, except through the shadow under 5.1.
- **The counted read uses the pinned October adapter on forward-1002 and walk 2 only** (section 11.2). The tip tape is never a counted data source.

### 5.3 The EXP-022 section 9 seal (from 2026-10-16T01Z)

- Walk 2 belongs to EXP-022. EXP-022 section 9 forbids pricing the counted picks from any source. C1-NF therefore never prices a CAP-PICK pick of a counted hour.
- **Exclusion by the boolean oracle [pinned].** From 2026-10-16T01 until EXP-022's read ends, every mint whose canonical pool's first print is at or after 2026-10-16T01 is looked up with `pick_oracle(mint) -> bool`, the contract of EXP-022 Amendment 2 item 2 and DEC-024 section 6. A mint answered `True` is dropped from training, scoring and the book (all its rows). The C1-NF side reads only that boolean, writes no CAP-PICK field into any C1-NF record, and joins no C1-NF record to a pick.
- **Fail closed.** If the oracle is missing, stale for more than 60 s at the shadow, errors, returns a non-boolean, or is undecided for any in-scope mint, then for the **shadow** no walk-2 buy is priced, and for the **read** the result is **NOT_DECIDABLE** (section 11.4 R7). It is never a pass and never an excuse to read on a truncated window. A manager may file a new pre-registration for a forward-1002-only window before the read; it would be a different test (power 0.249 at September's edge and 0.080 at half the edge at alpha 0.025, `power/power_table.txt`).
- **Effect.** The exclusion removes some of the hottest early pools, which C1-NF's stage 1 also likes. It biases the walk-2 sample against the book, and shrinks n. It is disclosed, not corrected. The report prints the excluded count per date (counts only, no P&L).
- **No per-pool join to CAP-PICK.** The read tool gets the oracle's booleans in memory. The C1-NF report never says which mints were picks.
- **Disclosure.** C1-NF's walk-2 reads are non-owner reads of an EXP-022 block (ledger rule 3), made while EXP-022 is counting; [EXP-022 Amendment 4](EXP-022-cap-pick-part1-prereg.md) records them. They open only chain tape, never a CAP-PICK outcome row.

## 6. Execution, costs and legs

- **Deciding cell [pinned]:** stake 0.25 SOL; buy lands at decision slot + `round(1.3 s / measured s-per-slot of that UTC hour)`; END bound; own trade applied; PumpSwap fee tier on (quote + V, base); **505,000 lamports per send**, buy and sell; **rent 2,039,280 lamports on every fill** (the pessimistic reading: it is refunded only if the executor closes the ATA on every sell, which is unconfirmed); guard 1.15 x the decision-time spot, where a guarded buy costs one send (505,000); exit E3 (sell 300 s after landing, landing 0.55 s after the deadline, `ceil(0.55 / s)` slots); one position per mint, a selected row taken only if t >= the previous exit + 60 s for that mint.
- **Fail legs [pinned], both deciding:** flat (a fill pays 0.85 pnl + 0.15 (-505,000 lamports)) and pressure (slopes 0.8 / 0.35, `p = sigmoid(c + 0.8 log1p(same-slot buys at landing) + 0.35 log1p(buy SOL in the 2 s to landing))`, intercept `c` refit so the mean p over the book's sends is 0.289). A guarded row is -505,000 on every leg.
- **VERIFY's numbers for this cell** (independent simulator, 1.3 s, 505,000, rent per fill, END bound, spec order; 419 trades) [measured, VERIFY section 5.2]: flat +7.458% (date-cluster CI90 lo +4.611), 16/21 days positive; pressure +6.640% (+4.258), 17/21. The same cell without rent: flat +8.151% (date CI lo +5.304), pressure +7.220% (+4.837), 18/21 days. These are September, exploration, post-hoc cap.
- **Where the deciding prices come from [pinned].** The pinned pass A (`11_passA.py`) bakes 55,000 lamports per send into its P&L columns, and the frozen training label `pnl_E3` is that 55,000 column. The label and the model stay as frozen, so the selection (prediction > 0.02, then the cap, then the book) is the frozen one. The **deciding P&L** of each selected row is priced by the read tool's pricing layer, which must be equivalent to `verify/v2_sim.py` (the independent simulator that produced every VERIFY number cited above: clock-based exit, END bound, own trade applied, the tier fee, the 1.15 x guard, 505,000 per send, rent per fill). The pass-A-column book (RULE.md's next-print exit at 55,000) is reported beside it (VERIFY: flat +8.484%, pressure +7.528%, 55,000, no rent, September). P4's E0 compares the two on an exploration day.
- **Stakes in SOL.** All gate statistics are in SOL per trade at 0.25 SOL. The 0.1 SOL leg is report-only.

## 7. Gate statistics and the decision rule

The read passes if and only if **every** item holds under **both** the flat and pressure legs at the deciding cell (section 6):

1. n >= 100 trades.
2. >= 5 distinct UTC dates (of the decision time), with a strict majority positive (daily total > 0).
3. The lower 90% CI bound of mean SOL per trade > 0 (1,000 bootstrap draws over trades, seed 1, 5th percentile). This is CLAUDE.md, literally.
4. Total SOL > 0 after removing the top 3 trades.
5. Total SOL > 0 after removing the best UTC date. **Binding.**
6. The date-cluster CI90 lower bound of mean SOL per trade > 0 (1,000 date resamples, seed 1). **Binding** (JUDGE-4 section 3.3.7).
7. Mean > 0 in each half of the look's counted dates (Look 1: the first 4 dates and the last 3; Look 2: the first 7 and the last 7). **Binding.** JUDGE-4 says "each tape"; the walk-2 tape has 8 dates and the forward-1002 tape 6, and a per-tape sign is noisier, so the by-tape means are report-only (section 8). The manager may restore by-tape; it lowers power.
8. **Day-level p <= alpha.** Clusters are UTC dates (24 h blocks from 00:00Z). A date with no trades is dropped; W is the count of the rest, df = W - 1. m_d is the mean SOL per trade on date d, sd is the sample SD of the W date means (ddof 1), t = mean(m_d) / (sd / sqrt(W)), p = P(T_{W-1} >= t), one-sided. **The larger of the flat and pressure p decides.** alpha is the look's: `EXP025_ALPHA_LOOK1` at Look 1, `EXP025_ALPHA_LOOK2` at Look 2 (section 0; the pair sums to 0.025 / k, k = 1 at this merge, DEC-025 section 2).

Items 1 to 4 are the promotion gate, unchanged. Items 5 to 8 can only turn a pass into a fail.

**Outcome.** PASS at the first look that passes (the read then ends and Look 2 does not run). FAIL if no look passes. A NOT_DECIDABLE look (section 11.4) is not a pass and carries no alpha forward. **On a FAIL, C1-NF is closed. No re-threshold, no re-cap, no re-read.** On a PASS: (1) quant-proof on the read; (2) a paper twin on the H5 executor core; (3) the owner's yes, with a size plan of at most 4 x 0.25 SOL open at a 1 SOL bankroll (5-minute holds); then only the DEC-018 / DEC-019 / DEC-020 path. The post-hoc bot path (45.048 SOL) is not a size estimate. The canary (section 5.1) is measurement, never gate evidence.

## 8. Report-only (never deciding)

Each is reported on both fail legs unless it says otherwise, at every look that runs.
- **The farm check.** The frozen C1 primary **without** the cap, to show whether the farm population is still there (VERIFY: 8,760 trades, flat -2.536%, pressure -2.039% at 505,000, September).
- **Caps and gameability.** `h_top1` <= 0.3 and <= 0.7; `h_top5` <= 0.5 and `h_top10` <= 0.5 (a farm operator can defeat a per-wallet cap by splitting the bag; VERIFY condition 2). In September `h_top5` <= 0.5 gave n 418 and the same mean as the 0.3 cap.
- **Legs.** 1.9 s entry; worst-in-slot (buy and sell); START bound; sell lags 2 s and 5 s; 55,000 per send; no rent; 0.1 SOL; 4.0 s; the 3.0 s report-only latency.
- **Fill-risk stress.** The mean if the best 10% and the best 28.9% of fills fail (VERIFY section 6 construction), the no-fail leg, and the by-tail table (p1, p5, median, p95, share losing >= 90%).
- **Splits.** Per date; per tape (forward-1002 vs walk 2); per half; ex-best-day, ex-top-10; mint concentration (mints, trades per mint, positive mints, total without the top 5 mints); the walk-2 count excluded by the oracle, per date (counts only).
- **Selections.** Per-day graduations, stage-1 rows, selections and kept rows (also printed by the precount); the model score threshold sweep (> 0.0 to > 0.05).
- **Out-of-support table.** Medians of `qreal`, `h_top1`, `wb5_new`, `v5`, `age`, `new5` on October picks against the discovery picks (VERIFY section 6: `qreal` 142.65, `h_top1` 0.0365, `wb5_new` 0.984, `v5` 628.7, `age` 995, `new5` 414) and against September's C1-NF picks.
- **Ledger coverage and drift.** Section 11.2: per decision date, the share of the 5-minute buyers and sellers known to the ledger, against the exploration picks.
- **V* and overhang strata** (JUDGE-4 leaf-4 ingredients), as removed-versus-kept per date.
- **Declared observation.** The text of section 5.1 and, if recorded, the shadow's and canary's results, labelled as not the read.

## 9. Multiplicity and disclosures

- **Within the family:** k = 1. One deciding cell; two looks; alpha 0.025 split by Bonferroni over looks (section 0).
- **Across families:** DEC-025 opens a third alpha slot of 0.025 (section 2 of the DEC). With EXP-022 and EXP-024 the promotion-eligible October families can carry an overall rate of up to 0.075, not 0.05. The owner's OK on this (O1) was relayed by the manager on 2026-10-09 (DEC-025 `OWNER_DECISION_CONFIRMED`).
- **Selection, in full [measured or counted from the hunt files]:**
  - C1's primary was the best of about 24 walk-forward cells, after a 144-cell split scan (JUDGE-4 section 3.3).
  - The farm cap was chosen **after** the read, from about 4 post-hoc splits in `x06_posthoc.py` (real quote < 400, age < 1,800 s, a combined split, `h_top1`).
  - About 40 hunts have read the four September blocks (JUDGE-4 section 1); exp011-0909 carries several pooled passes.
  - The DEC-014 m is >= 15 from this merge, and about 37 counting the unlisted readers [inferred].
- **What VERIFY did and did not do.** It rebuilt the pipeline from the pinned scripts, reproduced the frozen primary at 99.25% of C1's trades (Jaccard 98.53%; the gap is the non-deterministic ledger, section 11.1), re-derived `h_top1` independently (causal, max relative difference 5.9e-8), and ran an independent simulator. No kill rule fired. It is not evidence of an edge: the cap was chosen after the read and the September blocks are spent.
- **Decay and instability.** Block means (flat, 505,000, no rent) were +8.823 / +5.898 / +12.974 / +4.794% (fresh-0903, exp011-0909, fast-pool-0918, oracle-insample-0922). The second half of September was 8 of 11 days positive with the negative days at n of 3, 1 and 4. The `v5` and `new5` medians are higher in September than in discovery. October is unmeasured.
- **Known feature-drift risk: the wallet-ledger hole.** The exploration tape ends 2026-09-25T07 and the first forward-1002 hour is 2026-10-02T15. The manager's live-build plan places the hole in the live view at 09-25T07 to 10-05T05. For the read, wallets seen only inside the hole look new, so "new wallet" shares are inflated and creator history is undercounted. The size of the effect on picks is unmeasured. Section 11.2 gives the ruling. Mitigating, not proof: the training tape already contains two ledger holes (08-28T12 to 09-03T12 and 09-15T12 to 09-18T23).
- **October regime.** BOOST, event V, the 200 ms slots (about 10-09T14:30Z) and PDA pools are not in the September evidence. The read prices on them.
- **Not exchangeable with H5 or CAP-PICK.** C1-NF is BOOST-independent per JUDGE-4; a BOOST switch-off kills H5 and CAP-PICK together, not necessarily C1-NF.

## 10. Preconditions and integrity

**Before 2026-10-10T00:00Z.** If any is missing, C1-NF is withdrawn before counting and no outcome is read:
- **P0.** This file is merged, with quant-proof OK on its final head.
- **P1.** DEC-025 and its amendments (DEC-016 Am.8, DEC-021 Am.3, EXP-012 Am.4, EXP-022 Am.4) and the ledger edit are merged in the same PR, and DEC-025 carries `OWNER_DECISION_CONFIRMED` for O1, O2 and O3 (relayed by the manager, 2026-10-09). If the merge slips, section 0's late rule applies.

**Before 2026-10-16T01:00Z, the first walk-2 hour.** If any is missing, the walk-2 part cannot be read and the read is NOT_DECIDABLE:
- **P2. Deterministic rebuild (E0-C1).** Run the rebuild of `verify/run_rebuild.sh` with `ledger/01_wallet_daily_det.py` in place of the original ledger step, as one MiScusi job on research-0 (about 35 minutes, at most 2 workers, memory-capped). The pinned scripts are not edited: the run copies them to a working directory and applies the path-only patches of `verify/PATCHES.diff`, recording the sha256 of each patched file. Record the sha256 of the new `disc.npz`, `conf.npz` and the pinned `16_confirm.py` primary in a dated, outcome-blind amendment, and copy the three to a backed-up path. **Acceptance (reproducibility, not tuning):** the rebuilt unconstrained primary at 1.3 s must have n within 3% of 8,797 and, in spec order, the C1-NF book n within 400 to 440. A rebuild outside these bands withdraws C1-NF. It does not choose another cap, threshold or ledger.
- **P3. October adapter (section 11.2)** built, merged with quant-proof OK, and pinned by blob sha. Its E0: run on one exploration day's raw walker files it reproduces `ref/convert.py`'s trade rows and `ref/build_shared.py`'s `tokens` rows by md5.
- **P4. Read tool** merged with quant-proof OK: adapter + pinned pass A + daily retrain + the cap + book + legs + section 7. Its E0: on one exploration day it reproduces the independent simulator's (`verify/v2_sim.py`) per-trade results for the same selections to within 1% of stake on at least 99% of trades (VERIFY: 99.4% on all picks).
- **P5. The pick oracle** (section 5.3) exists, is tested for a boolean-only answer, and is wired to a fail-closed shadow, by 2026-10-16T01:00Z. Look 1's 10-16 date already needs it. Without it the walk-2 part, and with it both looks, is NOT_DECIDABLE.
- **P6. One pinned V source for forward-1002 (not decided here).** forward-1002 was walked without event V, and the read needs per-print V for canonical-pool prints. EXP-024 Look 1 also prices forward-1002 and needs the same V solution. **EXP-024 and EXP-025 should share one pinned V source.** The manager decides it with the EXP-024 scorer work, and a dated, outcome-blind amendment to both files records it (file sha256) before the first read of either. EXP-025 neither chooses nor builds a separate one. Candidates, as EXP-024 section 10 P5 frames them: a re-walk with `--event-v`, or `getTransaction` reconstruction; the Helius credit cost is not estimated here.

**Before the read (after the FINAL, outcome-blind; counts and hashes only):** the section 11.3 precount and the section 11.4 refusals.

**In each locked job:** each look is run once, in one locked MiScusi job. After the lock there is no resume.

## 11. The ledger fix, the October adapter, the precount and the refusals

### 11.1 Deterministic wallet ledger (VERIFY condition 1) [measured]

- **The defect.** `01_wallet_daily.py` summed float SOL in parallel in DuckDB. Two runs of one day differ on 38,107 to 69,660 wallet rows (VERIFY section 2); `cash` changes sign for about 3 wallets per day, which flips `skill = cash > 0` and shifts the LightGBM trees. C1 deleted its `wl/`, so its exact features cannot be recovered.
- **The fix** (`ledger/01_wallet_daily_det.py`, sha256 in section 2.3): integer-lamport sums; `nwin` on exact integer sums; SOL columns on a 2^-20 SOL grid, so the cross-day float sums inside the pinned `11_passA.py` are exact in any add order (that script is not edited); rows written `ORDER BY th` in zstd. Maximum grid error 4.77e-7 SOL per wallet-day.
- **Check** (`ledger/determinism_check.py`, log `ledger/determinism_check.log`; exploration tape only; day 2026-09-20, 24 hour files, 1,193,201 wallet rows):
  - the ORIGINAL logic run twice: **96,929 of 1,193,201 rows differ; 4 `cash` sign flips**;
  - the deterministic ledger run twice (threads 3, then 1, different temp dirs): **file sha256 equal**, `123487ab288c389efd47effc3ae7c15acac4b8402a4789b9b8937d761df6a9c5` both;
  - all 36 tape days built twice (threads 3 and 1): **36 of 36 file sha256 equal**;
  - the pass-A aggregation over the 35 prior days, 5 runs (threads 3, 3, 2, 1, 1): **one result hash**, and the aggregated `cash` equals the exact integer reference on all 13,812,176 wallets.
- **What this does not show.** The rebuilt model and book on the deterministic ledger are not yet computed (P2). VERIFY's numbers were produced on the non-deterministic ledger.

### 11.2 The October adapter: spec (must be built and pinned before the read; P3)

**Purpose.** Turn the October walker output (forward-1002 and walk 2: JSONL.zst trade, create and migration rows per UTC hour) into the layout the pinned scripts read. It adds no feature and changes no rule.

**Output layout (the "hunt layout").**
- `tape/trades/<YYYY-MM-DDTHH>.parquet`, `tape/creates/…`, `tape/migrations/…` with exactly the columns of `ref/convert.py` (`TR_COLS`, `CR_COLS`, `MG_COLS`), including `pool`, `slot`, `tx_index`, `event_index`, `block_time`, `quote_reserve`, `base_reserve`, `lp_fee`, `protocol_fee`, `creator_fee`, `block`, `hour`.
- `hunt-shared/tokens.parquet` and `bars_1m/` with the columns `ref/build_shared.py` writes (`grad_src`, `v0_lamports`, `ps_first_ms`, `pool`, `create_ms`, `complete_ms`, `complete_slot`, creator and bonding-curve stats, `ps_first_price`).
- `wl/<day>.parquet` from `ledger/01_wallet_daily_det.py`.

**Transformations that differ from September (all pre-declared).**
1. **Strict lines.** The reader refuses bad lines (a NUL byte, or a line that is not a JSON object) and the hour that holds one is bad (`--strict-lines`; EXP-022 section 10 "Bad lines"). Bad hours are counted and reported; they are never skipped silently.
2. **Event V.** `quote_reserve` for PumpSwap rows is the vault quote and the per-print event V is carried so that Q = vault quote + V (`--event-v`, #467). The reserve convention is checked first: PumpSwap tape reserves are PRE-trade, bonding reserves POST-trade (EXP-024 section 4). forward-1002 was walked without event V; P6 fixes its source.
3. **Canonical pool by PDA.** The canonical PumpSwap pool of a graduated mint is derived by PDA instead of the September V band. The match to the tape's pool must be >= 95% of non-Mayhem completes (refusal R2).
4. **Slot time.** Seconds per slot are measured per UTC hour from the clock (the pinned pass A does this); after about 10-09T14:30Z they are 200 ms.
5. **Universe filter.** `v0_lamports` between 17.5e9 and 17.7e9 and the first print within [-5 s, +120 s] of `complete`, as RULE.md says. The adapter fills `tokens` so the pinned `10_meta.py` selects the same universe; it does not change the filter.

**Wallet-ledger hole: the ruling [pinned; the manager may overrule before the merge].**
- The exploration ledger ends at 2026-09-25T07; the first forward-1002 hour is 2026-10-02T15. The read ledger for decision day D is built **from the exploration days plus every October hour in `[2026-10-02T15, D)` read from forward-1002 and walk 2, after the FINAL.** The adapter must fill that range; it is available only after the FINAL, which is also when the read runs.
- The part `[2026-09-25T07, 2026-10-02T15)` has no pinned source. **It is frozen as is: not filled.** No backfill, no tip-tape fill, no new getBlock walk. Any fill would be a new unpinned data source and a tuning surface, and the tip tape is not a counted source (section 5.2).
- For the shadow, the live view's hole runs to 10-05T05 (the manager's plan); the shadow's ledger is built from the tip tape and is not the read's.
- The first-48-hours rule of JUDGE-4 is moot: the first counted decision is 2026-10-10T00, more than 48 h after 10-04T15.
- **Disclosed as a known feature-drift risk** (section 9) and reported (section 8, ledger coverage). It never gates and never excuses a failure.

**Tests the adapter ships with.**
- E0 on an exploration day (P3).
- Idempotence: running it twice gives equal output sha256.
- A refusal test for every item of 11.4.

### 11.3 The mandatory count-only precount on the real layout

It runs on the real October layout after the adapter is built and the FINAL is written, **before any P&L**, with the model trained on the 36 exploration days only (no October label). A first-time try on fixtures is not a substitute: fixtures miss loader bugs (the lesson of the EXP-016 and EXP-021 try-spending runs). It is run once for each look, on that look's window (7 dates for Look 1, 14 for Look 2). It prints, per UTC date of the window: graduations, universe rows, stage-1 rows, selections at prediction > 0.02, kept rows after the cap, rows dropped by the oracle (walk-2 dates), bad hours, PDA-match share, V-coverage share, ledger-coverage share, fallback slot-time hours. It prints no price, fill, exit, pnl, mean, CI or day sign.

### 11.4 Pre-declared refusals (NOT_DECIDABLE; outcome-blind; none is a pass)

- **R1.** More than 2% of the ledger and counted hours (`[2026-10-02T15, 2026-10-17T02)` at Look 1, `[2026-10-02T15, 2026-10-24T02)` at Look 2) are bad or unverified, or any hour a counted attempt needs (from its mint's create hour through the hour that holds the landing block_time + 300 s + 60 s) is bad (that attempt is excluded and counted; more than 5% of attempts excluded makes the read NOT_DECIDABLE).
- **R2.** The PDA canonical-pool match is below 95% of non-Mayhem completes.
- **R3.** Per-print V coverage on canonical-pool prints of universe pools is below 95%.
- **R4.** The precount's kept selections over the look's window are below 90 (Look 1) or 150 (Look 2), or fewer than 5 of 7 (Look 1) or 8 of 14 (Look 2) dates have a kept selection. These are about 62% of the expected 140 and 280.
- **R5. No refusal on a high selection count.** A flood of selections is the failure C1 had on the farm. It stays a possible FAIL and is reported; it is never an escape hatch.
- **R6.** More than 5% of hours use the nearest-hour slot-time fallback of the pinned pass A.
- **R7.** The pick oracle is missing, stale, errors, returns a non-boolean, or is undecided for any in-scope walk-2 mint (at either look).
- **R8.** P2 to P6 are not all met by their deadlines.
- **R9.** Decision stability: a re-derivation of any decision from the window start must match the first by md5, or the read refuses. Look 2 re-derives Look 1's decisions (10-10 to 10-16) and they must match Look 1's record by md5, or Look 2 refuses.
- **R10.** A seal breach is not a refusal: the read runs and is reported compromised (section 4).

## 12. What is not decided here

- The live canary's design. It runs only under DEC-026 (to be written), with 0.02 SOL stakes and a separate wallet; scale-up beyond canary size needs a passed read or a further explicit owner override (section 5.1).
- Any sealed-block claim (none is made).
- Any change to EXP-012, EXP-022, EXP-024, the shadow detector or the H5 canary, other than the disclosures in the amendments listed in P1.

## Sources

`/data/mal/hunt-1008/JUDGE-4.md` sections 3.3 and 3.4; `/data/mal/hunt-1008/c1nf-verify/VERIFY.md` and `v/results.json` (copied to `ARTIFACTS/exp025/verify/`); `/data/mal/hunt-1008/c1-cascade-postgrad/` (RULE.md, scripts, ml/); [EXP-024](EXP-024-h5-boostfloor-part1-prereg.md) (template), [EXP-022](EXP-022-cap-pick-part1-prereg.md) sections 9 to 10, [DEC-023](../DEC/DEC-023-h5-family.md), [DEC-021](../DEC/DEC-021-champion-challenger.md), [DEC-016](../DEC/DEC-016-exp012-forward-on-chain-hours.md) Am.2, Am.7, [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md), `docs/HANDOFF.md`.
