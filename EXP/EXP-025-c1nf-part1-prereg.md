# EXP-025 Part 1: C1-NF post-graduation momentum forward read (two looks), pre-registration

**Written and merged before the first counted hour.** Written 2026-10-09. To write it, no row of a sealed block (fresh-0802, fresh-0808, fresh-0828) was opened, and neither was any forward-1002 or forward-1016 file, any forward-paper or runner output, or any key. The exploration tape was opened for one purpose only: the wallet-ledger determinism check (`ARTIFACTS/exp025/ledger/determinism_check.log`, section 11.1). Its inputs are `/data/mal/hunt-1008/JUDGE-4.md` (sections 3.3 and 3.4), the C1 hunt's frozen files, and the adversarial VERIFY (`ARTIFACTS/exp025/verify/VERIFY.md`, `results.json`), all copied into the repo and pinned (section 2.3). This file is docs only. The October adapter, the read tool and the pick oracle are separate PRs (section 11). Nothing in this file says or implies that any book is positive. **The VERIFY numbers are exploration on four spent September blocks. They are not evidence of an edge** (section 9).

| Field | Value |
| --- | --- |
| **ID** | `EXP-025-c1nf-part1-prereg` |
| **Status** | **planned**. Counting starts at 2026-10-10T00 (section 0). |
| **Declared (UTC)** | 2026-10-09. It must merge before 2026-10-10T00:00Z (target 2026-10-09T23:00Z). The owner's choice of a two-look design and his OK on DEC-025 O1, O2 and O3 were confirmed by the manager on 2026-10-09 (DEC-025 `OWNER_DECISION_CONFIRMED`). Quant-proof's 12 required edits on 95a0cc6 are applied in this version. |
| **Parent** | hunt-1008 / c1-cascade-postgrad: `RULE.md` (sha256 `a22cebc481f36cda70fff07663e525b5b72ebee8741c843b7489038dbd49281c`), `REPORT.md`; `/data/mal/hunt-1008/JUDGE-4.md` section 3.3 (ranks C1-NF third of the hunt 1-4 survivors); `c1nf-verify/VERIFY.md` (verdict SURVIVES-VERIFY). |
| **Rules** | [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md) (ledger, Holm, block budget, family lineage), [DEC-016](../DEC/DEC-016-exp012-forward-on-chain-hours.md) (forward-1002, seal, FINAL; Am.7, Am.8), [DEC-021](../DEC/DEC-021-champion-challenger.md) section 8, Am.1 to Am.3, [DEC-023](../DEC/DEC-023-h5-family.md), [DEC-025](../DEC/DEC-025-c1nf-family.md) (this family), [EXP-022](EXP-022-cap-pick-part1-prereg.md) section 9 and Amendment 4, [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md). |
| **Hypothesis** | The frozen C1 cascade plus the top-holder cap (C1-NF, section 2) has mean SOL per trade > 0 at 0.25 SOL on October post-graduation decisions, under both fail models, at a 1.3 s entry, 505,000 lamports per send and rent on every fill (section 6), and clears the gate and the day-level test at that look's alpha (section 7). Look 1 counts decisions in `[2026-10-10T00, 2026-10-17T00)` (7 dates). Look 2, only if Look 1 did not pass, counts the cumulative `[2026-10-10T00, 2026-10-24T00)` (14 dates). |
| **Kill condition** | Neither look passes (section 7); or a refusal fires (section 11.4, NOT_DECIDABLE); or a precondition fails (section 10). Two looks. No retune, no re-cap, no re-read. |
| **Expected outcome** | **Unknown, probably a fail.** The edge was found after the read on spent blocks. Conditional on the stated model and the registered pair (0.005, 0.020), P(pass at either look) is about 0.82 if September holds, 0.44 if the edge is half and 0.17 at a quarter, at 20 trades per day (0.64 / 0.29 / 0.11 at 12 per day; 0.75 / 0.36 / 0.14 with 12 per day on walk-2 dates; section 3). |
| **Tools** | **Read:** a locked read tool built on the October adapter (section 11). **Pick exclusion:** `pick_oracle(mint) -> bool` (section 5.3). **Determinism:** `ARTIFACTS/exp025/ledger/01_wallet_daily_det.py`. **October patches and V mapping:** `ARTIFACTS/exp025/patches/`, `event_v_map.py` (section 2.4). **Power:** `tools/exp025_power.py`. |

**Labels.** [measured] is copied from a cited file that computed it. [pinned] is a design value this file fixes; it is not evidence. [inferred] is reasoned. [est] is an estimate. Numbers from VERIFY are copied, not rounded up.

## 0. Counting start, looks and alpha (the pinned lines)

```
EXP025_COUNT_START: 2026-10-10T00
EXP025_LOOK1_END: 2026-10-17T00
EXP025_COUNT_END: 2026-10-24T00
EXP025_ALPHA_LOOK1: 0.005
EXP025_ALPHA_LOOK2: 0.020
```

- **Format.** Each line matches its pattern (for example `^EXP025_COUNT_START: 2026-10-10T00$`) exactly once. The read tool refuses unless all five do, and unless this file is clean against HEAD. The values never change after merge.
- **The alpha pair is one constant pair.** `EXP025_ALPHA_LOOK1` + `EXP025_ALPHA_LOOK2` = 0.025 / k (Bonferroni over looks; k = 1 at this merge, DEC-025 section 2). **(0.005, 0.020) is quant-proof's recommendation** (section 3). Replacing it before the merge means editing these two lines and the constants `ALPHA_LOOK1` and `ALPHA_LOOK2` in `tools/exp025_power.py`, then re-running the power table; `tools/test_exp025.py` fails if the lines, the constants or the sum disagree. After merge they are fixed.
- **Counted decision.** A selected row counts at a look if its decision time T (a whole UTC minute) satisfies `START <= T < LOOK1_END` (Look 1, 7 dates, 10-10 to 10-16) or `START <= T < COUNT_END` (Look 2, 14 dates, 10-10 to 10-23). The last counted exit of Look 1 lands before 2026-10-17T01:10 and of Look 2 before 2026-10-24T01:10; hours 2026-10-17T01 and 2026-10-24T01 are read for exits only.
- **Why 10-10T00.** A counted window must be fixed before its first hour begins in real time (ledger rule 4; EXP-024 section 0). This file can merge during 10-09, so 10-10T00 is the first full UTC day. October hours before it are read as ledger, feature and **training-label** input only, after the FINAL (section 4).
- **If the merge is late [pinned].** The only alternative start is `C = 2026-10-11T00`, and only if this file (with DEC-025, quant-proof OK on its final head) merges before that instant. The three date lines then become `2026-10-11T00`, `2026-10-18T00` and `2026-10-25T00` by a dated, outcome-blind amendment merged before 2026-10-11T00, and the allowlists of section 4 move by one day. **The amendment also replaces both patches** with the late-merge variants `patches/late_merge_C1011/common2_look{1,2}.patch` (segment 3 ends `2026-10-18T02` and `2026-10-25T02`; sha256 in section 2.4), which are authored and pinned now so that nothing is authored after a window opens. Without them pass A's grid clip `SEGS[seg][1] - MAX_HOLD - MARGIN` would stop at 2026-10-17T00:55 and drop Look 1's last date. The alpha lines and everything else are unchanged. If the file has not merged by 2026-10-11T00:00Z, C1-NF v1 is **withdrawn** on these hours (section 11.5). No other start date is allowed.
- **No outcome before merge [pinned].** No C1-NF shadow or canary outcome (label, fill, exit, P&L, mean, CI or day sign) is computed before this file merges, so the start can not be chosen after watching outcomes.
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
- pricing uses the pinned event-V mapping `quote_reserve := vault + V(t) - V0` of section 2.4 (not a carried column that pass A never reads); "real quote >= 20 SOL" is pass A's `qreal`, the vault net of pending fees;
- seconds per slot measured per UTC hour; after about 2026-10-09T14:30Z slots are 200 ms, so the 0.55 s sell lag is 3 slots (`ceil(0.55 / s)`, as pass A computes it);
- the wallet ledger is built from tape days strictly before D: the exploration days plus October hours (section 11.2).
- October training rows come only from graduations at or after 2026-10-09T00 (the first hour with event V, P6); the 36 exploration days are unchanged. This departs from RULE.md's "every stage-1 row with t < D 00:00Z - 3,600 s", which has no October rows before 10-09T00 to train on;

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
| **`patches/common2_look1.patch`** / **`patches/common2_look2.patch`** (section 2.4) | `404669118447557f42bad7aa9cdb0b41e48ce6916b0cc7e003a7d643ece45722` / `885ce0d89f50c82562c2edeb572491080ef80d30ef1a328a67a3b7d6d7e32fb6` |
| **`event_v_map.py`** (section 2.4) | in `SHA256SUMS` |
| `ref/convert.py` (walker JSONL to the hunt tape layout), `ref/build_shared.py` (`tokens.parquet`, `bars_1m`) | in `SHA256SUMS`; the layout reference for the adapter (section 11.2) |
| `verify/VERIFY.md`, `verify/results.json`, `verify/v2_sim.py`, `verify/v3_report.py` | in `SHA256SUMS`; the numbers cited here and the independent simulator that cross-checks the read tool (section 10, P4) |

**Training data (not in the repo; 0.5 GB).** VERIFY's rebuild produced `ml/disc.npz` (sha256 `4b68d55b93158c3967926fb9b41192ba2a5aee27075669b95af374bd0a582409`) and `ml/conf.npz` (`0dc37940dee7768086fe6f1f618c80fdc97b0a93fec80af01e19d902f4649e1d`) from a **non-deterministic** ledger. They are reference only. The training set the read uses is the rebuild on the deterministic ledger (section 10, P2), whose hashes are recorded in a dated amendment before 2026-10-16T01:00Z and copied to a backed-up path.

### 2.4 Pinned October patches and the event-V mapping (not rule changes)

**October segment and paths (quant-proof R1).** As pinned, `scripts/common2.py` lists only the three September segments in `SEGS`, `scripts/11_passA.py` skips a graduation whose `seg_of(g0)` is negative (lines 109-110), and its grid end is `min(g0 + GRID_END, SEGS[seg][1] - MAX_HOLD - MARGIN)` (line 146). Run unpatched on October tape every graduation is skipped. One exact diff per look fixes this and edits no other file:

| Patch (sha256) | Applied `common2.py` (sha256) | October segment 3 | Paths |
| --- | --- | --- | --- |
| `patches/common2_look1.patch` `404669118447557f42bad7aa9cdb0b41e48ce6916b0cc7e003a7d643ece45722` | `7060cbd537571dc8a2da6249d4443adf2453224b8bca6aa46c68dbfbee803a11` | `[2026-10-02T15, 2026-10-17T02)` | `O = /data/mal/exp025/look1`, `TAPE = O/tape`, `SH = O/hunt-shared`, `TMP = /data/mal/exp025/tmp/look1` |
| `patches/common2_look2.patch` `885ce0d89f50c82562c2edeb572491080ef80d30ef1a328a67a3b7d6d7e32fb6` | `ca6d4b75790de653410df985e8598e8a336d0016d40c0535b5a73b2d0bd0ac7e` | `[2026-10-02T15, 2026-10-24T02)` | the same with `look2` |
| `patches/late_merge_C1011/common2_look1.patch` `0a4ca896595c963f4964319ab0c51338ab82b56d3076e0f986cf3b434bfb8d40` (late-merge variant, only for `C = 2026-10-11T00`) | `c87809cf3052999063ebd8c871539b05f1252ac9421ecdf29e0b0396a67120d2` | `[2026-10-02T15, 2026-10-18T02)` | as Look 1 above |
| `patches/late_merge_C1011/common2_look2.patch` `58b24f71c7e8565a06c6734e19bffeb77a18ab4855a1760a2154644c51bcbb89` (late-merge variant) | `e5eb33d76524d57ea7f0a396e965fb5c233b1e6a55be388628b05d409e682c11` | `[2026-10-02T15, 2026-10-25T02)` | as Look 2 above |

- **The late-merge rows differ from the on-time patches only in the end of segment 3** (one day later, section 0; a test checks this). They are used only if the file merges after 2026-10-10T00:00Z and before 2026-10-11T00:00Z.
- **It is a pinned patch, never a rule change.** It adds a segment and changes four path constants. No threshold, feature, fill, exit, cost or filter changes. `seg_of` and the grid clip read the patched `SEGS`, so `11_passA.py` itself is not edited. `tools/test_exp025.py` applies each patch to the pinned `common2.py` and checks the result's sha256, `seg_of` on September and October instants, and that the three September segments are unchanged.
- **Invocation.** `11_passA.py <D>` for graduation days D from 2026-10-09 to the day before the look's end (V coverage starts 10-09T00, P6). `10_meta.py`, `12_passC.py` and `14_export.py` run as pinned on the look's `O`.

**Event-V mapping (quant-proof R2) [pinned].** Pass A prices with one constant per-pool `V = tokens.v0_lamports` added to the tape's `quote_reserve`. It never reads a carried per-print column, so the adapter must put the per-print V into `quote_reserve` itself:

`quote_reserve := vault + V(t) - V0`

- **Inputs are the same print's own values; nothing is chained from the previous print.** `vault` is the decoder's `quote_reserve` of the print: the event's pool quote token reserves, the raw vault balance **before** the trade. #467 pins it: `tools/test_walk2_event_v.py::test_event_quote_reserve_equals_vault_prebalance` (row `quote_reserve` equals the vault's `preTokenBalances` on five real transactions, including the October ones). `V(t)` is the same print's event `virtual_quote_reserves`. **That V is the stored V before the trade rests on two things:** (1) the audit R5 statement in the `VLawFixtureTests` docstring ("event V is the stored V BEFORE the trade; event pool_quote_token_reserves is the raw vault BEFORE the trade"); (2) the exact constant-product match on `sell_v2_kept.json` (`test_constant_product_uses_vault_plus_event_v`): with this V and the pre-trade vault the residual is 0 lamports, against -3 lamports if V were post-trade [quant-proof's computation on that fixture; not re-run here]. The boost-buy fixture is **not** evidence for the convention: it is fee-free, so it cannot tell pre-trade from post-trade. The pre/post difference is under 0.01 bp for pricing; what matters is the price level, meaning whether pending fees are included. `V0` is V(t) at the pool's first print s0 (pending fees are 0 at a fresh pool) and is the value the adapter writes to `tokens.v0_lamports`.
- **Why not chain.** A chain from the previous print's post-trade state holds across a fee sweep (a sweep leaves vault + V unchanged) but breaks across an LP deposit or withdraw, about 1% of pools, because the tape's `base_reserve` includes the LP change and a chained quote does not.
- **The E0 day cannot test the convention.** On 2026-09-20 V(t) = V0 and pending is 0, so the mapping is the identity there. The check on October prints is P7 below.
- Pass A then prices with `quote_reserve + V0 = vault + V(t)`, and `qreal` is the vault net of pending fees. If V(t) = V0 throughout, the mapping returns the vault and the September behaviour. Without it the October price would be gross vault + V0, whose unswept fees the audit bounds at 2.46% to 3.83% of effective quote at 300 s (daily p50) and calls an optimistic bias [measured, `/data/mal/audit-1008/reports/g_october_structure_check.md:45,90-92`, relayed by quant-proof].
- The functions are in `ARTIFACTS/exp025/event_v_map.py` (`map_quote_reserve`, `mapped_series`, the P7 constants and decision rules, and the two constant-product laws). Fixture tests in `tools/test_exp025.py` check the identity `q + V0 = vault + V(t)`, that the mapping uses the print's own vault and V (not a chained one, with an LP-deposit case), the constant-V case, that a mapping which ignores pending fees misses the constant-product law by orders of magnitude more than 1 bp while the pinned mapping does not, and the P7 decision rules.

### 2.5 Choices pinned at this merge (quant-proof R7)

| Choice | Pinned |
| --- | --- |
| October segment and paths, per look | the two patches above |
| Event-V mapping | section 2.4 |
| Event-V inputs and the October pricing check | the same print's own pre-trade `quote_reserve` and event V (section 2.4); P7 (section 10) |
| Look-directory history | section 11.2: exploration `tokens.parquet` byte-identical plus October rows; P2's `out/mout` plus October pass-A output; P2's 36 `wl` days plus October days |
| Late-merge patches | `patches/late_merge_C1011/` (section 2.4) |
| Missing-V pricing for counted trades | section 6 |
| Oracle exclusion point and the oracle source | section 5.3: before `11_passA.py`; union of the live intents and EXP-022's replay pick decisions at E0 commit `6b9b4fc14bbfb69f04ee1bf2b2c50b1d3cab1572`; booleans only |
| V source for forward-1002 | `forward-1002ev`, read directly for `[2026-10-09T00, 2026-10-16T01)` (P6) |
| E0 day (ledger, adapter and pricing layer) | **2026-09-20** (fast-pool-0918; 24 hour files; 34 C1-NF trades in VERIFY's September book) |
| Wallet-ledger hole | frozen as is, not filled (section 11.2) |
| Allowlists | section 4 |
| Late merge | section 0 |
| Alpha pair and look deadlines | sections 0 and 3 |
| Decoder blob | P6 |

## 3. The two looks, the alpha pair and the power

**Design [pinned; the owner chose "Two looks", 2026-10-09].**

| | Look 1 | Look 2 (cumulative) |
| --- | --- | --- |
| Counted decision dates | `[2026-10-10T00, 2026-10-17T00)`, 7 dates | `[2026-10-10T00, 2026-10-24T00)`, 14 dates |
| Tapes | 10-10 to 10-15 on forward-1002 (needs the FINAL); 10-16 on walk 2 (needs the oracle) | adds 10-17 to 10-23 on walk 2 (needs the oracle) |
| Allowlisted hours | section 4 | section 4 |
| Earliest run | about **2026-10-17T03Z**, after the last exit, the FINAL and the section 10 preconditions | about **2026-10-24T03Z** |
| Deadline | 2026-10-18T12:00Z | 2026-10-26T12:00Z |
| Runs | always | only if Look 1 did not pass (FAIL or NOT_DECIDABLE) |
| alpha | `EXP025_ALPHA_LOOK1` = **0.005** | `EXP025_ALPHA_LOOK2` = **0.020** |

- **PASS at the first look that passes**; the read then ends and Look 2 does not run. FAIL if no look passes. A look not run by its deadline is NOT_DECIDABLE and spends its alpha (section 11.5).
- **No futility rule.** A Look 1 FAIL does not end the read. (The manager may add one before the merge; it could only stop the read and would spend no alpha.)
- **Look 2 re-derives Look 1's decisions** for 10-10 to 10-16 from the same hours and the same pinned inputs. They must match by md5 or Look 2 refuses (R9). The daily expanding retrain uses only labels before each decision day, so a later look cannot change an earlier day's decisions.
- **Power** (`tools/exp025_power.py`, default mode `looks`; 1,200 simulations per row, Monte Carlo SE <= 0.014; table in `ARTIFACTS/exp025/power/power_table_looks.txt` and `.json`). The probability that **every** deciding item of section 7 holds on **both** fail legs at the deciding cell (1.3 s, 505,000 per send, rent on every fill). **The legs are weighted by expected value as the gate does** (`mlcommon.legs`: flat = 0.85 pnl - 0.15 fee; pressure = (1 - p) pnl - p fee), not drawn at random; the earlier draft drew failures at random and understated every power (quant-proof R10). One simulated October serves both looks. The edge is relative to September's rent-inclusive effect (flat +7.458%, pressure +6.640%, VERIFY section 5.2). B1 (1.9 s, item 9) is not simulated.

| Scenario | Edge | n at Look 1 / 2 | Look 1 | Look 2, if Look 1 did not pass | **Either look** | One 14-date read at 0.025 (comparison) |
| --- | --- | --- | --- | --- | --- | --- |
| 20/day on all dates | September holds | 141 / 281 | 0.258 | 0.562 | **0.821** | 0.834 |
| 20/day on all dates | **Half the edge** | 141 / 281 | 0.081 | 0.354 | **0.435** | 0.448 |
| 20/day on all dates | Quarter | 141 / 281 | 0.033 | 0.136 | 0.168 | 0.169 |
| 12/day on all dates | September holds | 83 / 167 | 0.033 | 0.608 | 0.642 | 0.660 |
| 12/day on all dates | Half the edge | 83 / 167 | 0.013 | 0.276 | 0.289 | 0.305 |
| 12/day on all dates | Quarter | 83 / 167 | 0.006 | 0.100 | 0.106 | 0.116 |
| 20/day on forward-1002, 12/day on walk-2 dates (oracle exclusion) | September holds | 131 / 215 | 0.212 | 0.537 | 0.749 | 0.762 |
| same | Half the edge | 131 / 215 | 0.077 | 0.279 | 0.357 | 0.357 |
| same | Quarter | 131 / 215 | 0.024 | 0.115 | 0.139 | 0.138 |
| 20/day with a day effect (SD 0.03 of stake) | September holds | 140 / 281 | 0.209 | 0.618 | 0.827 | 0.840 |
| same | Half the edge | 140 / 281 | 0.058 | 0.321 | 0.379 | 0.414 |
| same | Quarter | 140 / 281 | 0.022 | 0.120 | 0.142 | 0.152 |

- **What it says [simulation; no October information].** At (0.005, 0.020), Look 1 passes about 26% of the time if September holds and 8% at half the edge. Either look passes about 82% and 44%. A real half-size edge is missed about 56% of the time. With only 12 per day on walk-2 dates the figures are 75% and 36%. The model does not simulate correlated fill failure.
- **Other splits (either look, 20/day; same run; September / half / quarter).** (0.008, 0.017): 0.815 / 0.426 / 0.154. (0.003, 0.022): 0.831 / 0.437 / 0.171. (0.010, 0.015): 0.807 / 0.419 / 0.146. With 12/day on walk-2 dates at half the edge: 0.348 / 0.359 / 0.334 for those three.
- **Why (0.005, 0.020), per quant-proof.** Total power is equal or higher than (0.008, 0.017) in every case it ran. Look 1 is the thinner read: 6 of its 7 dates are on one tape, n is about 141 against the floor of 100, its halves are 4 and 3 dates, and its dates are watched live, so it carries the smaller share. 0.005 matches EXP-022's first look. The cost is the chance of an early answer on about 10-17: Look 1 passes 26% instead of 33% if September holds, and 8% instead of 12% at half the edge. (0.008, 0.017) is also a valid Bonferroni pair.
- **Null check, from quant-proof's own simulation [not re-run here].** 20,000 simulations with the trade mean set to exactly 0, day-level t alone: Look 1 at 0.008 rejects 0.00815; Look 2 at 0.017 rejects 0.01675; either look 0.0227 for (0.008, 0.017) and 0.0233 for (0.005, 0.020). **(0.008, 0.022) rejects 0.0276, which is over 0.025 and breaks the slot; it is not used.**
- **Earlier single-window table** (6, 7, 10 and 14 dates at one alpha, random-draw legs): `ARTIFACTS/exp025/power/power_table.txt`, superseded.
- **A FAIL at half the edge would not prove the edge is absent.** Over both looks the test misses a real half-size edge more than half the time.
- **12/day rows.** Walk-2 days lose the picks CAP-PICK took (section 5), and October volume is unmeasured. They are sensitivities, not forecasts.
- **Model and assumptions (all printed by the script).**
  - Per-trade returns are a four-part mixture calibrated to VERIFY's numbers: win rate 66.8%, 22 of 419 trades lose >= 90%, median +10.17%, p95 +63.17%, no-fail mean +9.625%, top-3 average about +165%, top-10 about +122% [measured, VERIFY sections 5 and 6, `results.json`]. The model's trade SD is 0.373 against 0.342 implied by VERIFY's trade CI (slightly conservative).
  - The fitted day effect is 0 (September's between-day variance of daily means is below the trade-level noise); the day-effect row adds SD 0.03 of stake. The edge scales the positive parts, not the loss tail.
  - Flat failure 15%; the pressure leg uses one effective failure rate (0.2408) set so that September's rent-inclusive pressure mean is reproduced; the real leg has a per-trade rate.
  - **Not simulated:** B1 (1.9 s), correlated fill failure, the canary's footprint, and any October regime change.
- **Adverse fills.** The edge sits in the right tail of hot entries, so correlated fill failure is the main untested live risk. VERIFY section 6 [measured]: if the best 10% of fills failed, the no-fail mean (1.3 s, 505,000) would fall to +1.855%; if the best 28.9% failed, to -5.116%. The power table does not include this.

## 4. Data, windows and seal

| | Hours read | Source | When |
| --- | --- | --- | --- |
| Ledger, creates and market counts only; **never labels** | forward-1002 `[2026-10-02T15, 2026-10-09T00)` | `/data/mal/blocks/forward-1002` | after the FINAL is written |
| Counted decisions, exits, training labels from graduations at or after 10-09T00 | Look 1: `forward-1002ev` `[2026-10-09T00, 2026-10-16T01)` and walk 2 `[2026-10-16T01, 2026-10-17T02)`. Look 2 adds walk 2 to `2026-10-24T02` | `/data/mal/blocks/forward-1002ev`, `/data/mal/blocks/forward-1016` (planned) | after the FINAL; walk-2 hours as sealed and verified |
| Training | the 36 exploration days plus October universe rows from graduations at or after 2026-10-09T00 | exploration tape; the two above | inside the one locked job of each look |

- **Which pre-10-10 hours supply labels.** Graduations before 2026-10-09T00 have no V0 and cannot pass the universe V band, so forward-1002 `[10-02T15, 10-09T00)` supplies the ledger, creates and market counts only, never labels (P6 item 2). October **training labels** (pass-A `pnl_E3` on October rows) come only from graduations at or after 2026-10-09T00, read from forward-1002ev, so hours in `[2026-10-09T00, 2026-10-10T00)` are training-label input before the window opens. They are read after the FINAL and inside the look's locked job, and are sealed from merge like any other October label.
- **Allowlist per look [pinned, R9].** Look 1: forward-1002 `[2026-10-02T15, 2026-10-09T00)`, forward-1002ev `[2026-10-09T00, 2026-10-16T01)`, walk 2 `[2026-10-16T01, 2026-10-17T02)`. Look 2: the same plus walk 2 `[2026-10-17T02, 2026-10-24T02)`. The adapter **materialises only the look's allowlisted hours** into `/data/mal/exp025/look{1,2}/tape`. Pass A opens hours `D T00 .. D+2 T01` and skips a missing file, so near a look's end it would otherwise reach walk-2 hours past the allowlist, which are EXP-022's counted hours; with only the allowlisted files present it cannot. **The read tool refuses to open any hour outside the allowlist** (R12).
- **Counted selection.** A selection counts in a look if its decision time T is in that look's window (section 0). The universe is every `complete` graduation whose canonical pool is found by PDA (RULE.md universe), minus oracle-True mints on walk 2 (section 5.3).
- **Seal (from merge until the look that reads them) [pinned, R7].** No person, agent or job does any of these on **any October hour**, from any source (forward-1002, forward-1002ev, walk 2, the tip tape, the live listener, RPC, a paper runner or a live wallet), **except the declared observation in section 5**:
  - computes, opens or prints a C1-NF label, fill, exit, P&L, mean, CI or day sign for **any October row**, whether a decision inside a window or a training row before it;
  - prices the C1-NF book on a counted decision.
- **What stays allowed:** hour counts (sealed, verified, bad); the section 11.3 precount, which is counts only; the A3 structure monitor's flags; building and testing the adapter on exploration hours.
- **No early forward-1002 read.** No C1-NF process opens any forward-1002 hour before the EXP-012 FINAL (A) report is written (DEC-016 Am.8). **Before the FINAL, only the walker, `backfill_verify` and hour counts touch `forward-1002ev`** (it is a second copy of the same hours, same owner, not a new block). The tool checks the FINAL marker, the entry in the external ledger `/data/mal/exp012-forward/FINAL_READS.jsonl`, and opens none of the files DEC-016 Am.2 keeps closed (`OUT/rows.jsonl`, `OUT/scratch/*.jsonl`, a FINAL `report.json` or `report.md`). It does not use the (B) V-map files.
- **A breach** is recorded here, dated, and the read is reported compromised. A compromised read cannot support a live request.

## 5. Declared observation, tip tape, and the CAP-PICK seal

### 5.1 Declared observation (the manager's ruling, 2026-10-09; modelled on EXP-024 section 3.1 and DEC-024)

- "The manager intends to run a C1-NF **paper shadow** and a small **live canary** (O3, approved; see below) after this file merges, before and during the counted windows. No shadow or canary outcome is computed before the merge. Their outcomes for decisions inside the windows may be watched in real time. This is declared before the window opens and before any shadow or canary trade."
- "The read's rule, data, analysis and pass bar are fixed by this file. Look 1 and Look 2 are always run (Look 2 under its own condition) and reported as written. Neither is skipped, delayed, re-scoped or re-thresholded because of anything the shadow or the canary shows. Nothing the shadow or canary shows may change any EXP-025 parameter."
- **The shadow is not the read.** It uses different inputs (the tip tape, a tip-tape ledger, its own V source), so its decisions will differ from the read's. The read never uses the shadow's trades, features or ledger, and the read's report states the declared observation and any disagreement the manager chooses to record.
- **A small live canary is approved (O3), confirmed by the manager 2026-10-09.** DEC-025 `OWNER_DECISION_CONFIRMED` records the owner's words as the manager recorded them: "I'm also down to do some testing with some small trades in parallel." The manager's scope, which is his and not a quotation of the owner: 0.02 SOL stakes, a separate wallet, running before and during the counted windows, **under its own DEC-026, which is to be written and does not block this PR**. No C1-NF live order is sent until DEC-026 is merged and its own preconditions hold. The canary is separate from H5's DEC-024 canary and override. **Its outcomes fall under this declared observation:** watched live, with Look 1 and Look 2 fixed and always reported as written. **Scale-up beyond canary size needs a passed read or a further explicit owner override.** At 0.02 SOL against a stage-1 real quote of at least 20 SOL the canary is at most 0.1% of a pool's quote; its trades are real chain activity by one participant, appear in the tape as ordinary rows, and are not removed (that would edit chain truth). The read reports them. A paper shadow needs no DEC.
- **Provenance.** This subsection records a ruling the manager gave on 2026-10-09. The quoted sentences are the manager's wording. The only owner's words quoted are the O3 sentence above.

### 5.2 The tip tape

- The fast-0 tip-follower tape (`tools/fast_tip_follower.py`; daily archive `/data/mal/tip-tape-archive`) may be used to compute C1-NF features and to run the shadow. It is a live capture. It is **not** forward-walk, forward-paper or runner P&L, and its use for features is not a read of any sealed output.
- It may not compute or print a C1-NF outcome (label, fill, P&L) for a decision in the window before the read, except through the shadow under 5.1.
- **The counted read uses the pinned October adapter on forward-1002 and walk 2 only** (section 11.2). The tip tape is never a counted data source.

### 5.3 The EXP-022 section 9 seal (from 2026-10-16T01Z)

- Walk 2 belongs to EXP-022. EXP-022 section 9 forbids pricing the counted picks from any source. C1-NF therefore never prices a CAP-PICK pick of a counted hour.
- **Exclusion at the universe, before pass A [pinned, R4].** From 2026-10-16T01 until EXP-022's read ends, every mint whose canonical pool's first print is at or after 2026-10-16T01 is looked up with `pick_oracle(mint) -> bool`, the contract of EXP-022 Amendment 2 item 2 and DEC-024 section 6. **A mint answered `True` is removed from `work/universe.parquet` after `10_meta.py` and before any `11_passA.py` run**, so no grid, candidate or `mout` row ever exists for it. Pass A computes `pnl_E1..E8` and a 0 to 6 h `mout` return from the graduation price for every universe mint, and `rgrad` at graduation + 10 minutes; those overlap CAP-PICK's first 300 s, so dropping the mint later (at scoring or in the book) would still price a pick. The shadow applies the same rule. The C1-NF side reads only the boolean, writes no CAP-PICK field into any C1-NF record, and joins no C1-NF record to a pick.
- **The oracle's source [pinned].** The union of (a) the live gate intents (DEC-024 section 6) and (b) the `pick` decisions of EXP-022's walk-tape replay (EXP-022 section 2) at the commit EXP-022's E0 pins, `6b9b4fc14bbfb69f04ee1bf2b2c50b1d3cab1572` (Amendment 3), kept in memory as booleans only. The live intents alone can miss replay picks (receive time, uptime); the union is a superset of the counted pick set. Both parts read only after the FINAL marker.
- **Fail closed.** If the oracle is missing, stale for more than 60 s at the shadow, errors, returns a non-boolean, or is undecided for any in-scope mint, then for the **shadow** no walk-2 buy is priced, and for the **read** the result is **NOT_DECIDABLE** (section 11.4 R7). It spends the look's alpha (section 11.5) and is never a pass.
- **Effect.** The exclusion removes some of the hottest early pools, which C1-NF's stage 1 also likes. It biases the walk-2 sample against the book, and shrinks n. It is disclosed, not corrected. The report prints the excluded count per date (counts only, no P&L).
- **No per-pool join to CAP-PICK.** The read tool gets the oracle's booleans in memory. The C1-NF report never says which mints were picks.
- **Disclosure.** C1-NF's walk-2 reads are non-owner reads of an EXP-022 block (ledger rule 3), made while EXP-022 is counting; [EXP-022 Amendment 4](EXP-022-cap-pick-part1-prereg.md) records them. They open only chain tape, never a CAP-PICK outcome row.

## 6. Execution, costs and legs

- **Deciding cell [pinned]:** stake 0.25 SOL; buy lands at decision slot + `round(1.3 s / measured s-per-slot of that UTC hour)`; END bound; own trade applied; PumpSwap fee tier on (quote + V, base); **505,000 lamports per send**, buy and sell; **rent 2,039,280 lamports on every fill** (the pessimistic reading: it is refunded only if the executor closes the ATA on every sell, which is unconfirmed); guard 1.15 x the decision-time spot, where a guarded buy costs one send (505,000); exit E3 (sell 300 s after landing, landing 0.55 s after the deadline, `ceil(0.55 / s)` slots); one position per mint, a selected row taken only if t >= the previous exit + 60 s for that mint.
- **Fail legs [pinned], both deciding:** flat (a fill pays 0.85 pnl + 0.15 (-505,000 lamports)) and pressure (slopes 0.8 / 0.35, `p = sigmoid(c + 0.8 log1p(same-slot buys at landing) + 0.35 log1p(buy SOL in the 2 s to landing))`, intercept `c` refit so the mean p over filled (non-guarded) attempts is 0.289). A guarded row is -505,000 on every leg.
- **Missing V (quant-proof R3) [pinned].** A counted trade whose landing or exit state has no decoded V(t) is priced at the **lower** P&L of two assumptions at the exit: pending fees 0, and pending fees 3.83% of effective quote (the upper end of the audit's 300 s bound). The look is **NOT_DECIDABLE** if any such trade is among the top 3 trades of either leg, or if such trades are more than 1% of the look's trades (R11). This is the rule of EXP-022 section 3 and EXP-024 section 4. `getTransaction` can fill only the states that price a counted trade (P6).
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
9. **Binding leg B1, the 1.9 s entry [pinned].** As the deciding cell with the buy landing at `round(1.9 s / s-per-slot)` (same costs, rent, exit and guard): **mean > 0 and total SOL > 0 after removing the top 3 trades, on both fail legs** (the form of EXP-024's B1). VERIFY, September, 505,000 with rent [measured]: flat +7.511%, pressure +6.323%. This is looser than JUDGE-4 section 3.3.7, which applied the full gate at both latencies (section 9).

Items 1 to 4 are the promotion gate, unchanged. Items 5 to 9 can only turn a pass into a fail.

**Outcome.** PASS at the first look that passes (the read then ends and Look 2 does not run). FAIL if no look passes. A NOT_DECIDABLE look (section 11.4) is not a pass and carries no alpha forward. **On a FAIL, C1-NF is closed. No re-threshold, no re-cap, no re-read.** On a PASS: (1) quant-proof on the read; (2) a paper twin on the H5 executor core; (3) the owner's yes, with a size plan of at most 4 x 0.25 SOL open at a 1 SOL bankroll (5-minute holds); then only the DEC-018 / DEC-019 / DEC-020 path. The post-hoc bot path (45.048 SOL) is not a size estimate. The canary (section 5.1) is measurement, never gate evidence.

## 8. Report-only (never deciding)

Each is reported on both fail legs unless it says otherwise, at every look that runs.
- **The farm check.** The frozen C1 primary **without** the cap, to show whether the farm population is still there (VERIFY: 8,760 trades, flat -2.536%, pressure -2.039% at 505,000, September).
- **Caps and gameability.** `h_top1` <= 0.3 and <= 0.7; `h_top5` <= 0.5 and `h_top10` <= 0.5 (a farm operator can defeat a per-wallet cap by splitting the bag; VERIFY condition 2). In September `h_top5` <= 0.5 gave n 418 and the same mean as the 0.3 cap.
- **Legs.** The full gate at 1.9 s (B1 decides only on mean and ex-top-3); worst-in-slot (buy and sell); START bound; sell lags 2 s and 5 s; 55,000 per send; no rent; 0.1 SOL; 4.0 s; the 3.0 s report-only latency.
- **Fill-risk stress.** The mean if the best 10% and the best 28.9% of fills fail (VERIFY section 6 construction), the no-fail leg, and the by-tail table (p1, p5, median, p95, share losing >= 90%).
- **Splits.** Per date; per tape (forward-1002 vs walk 2); per half; ex-best-day, ex-top-10; mint concentration (mints, trades per mint, positive mints, total without the top 5 mints); the walk-2 count excluded by the oracle, per date (counts only).
- **Selections.** Per-day graduations, stage-1 rows, selections and kept rows (also printed by the precount); the model score threshold sweep (> 0.0 to > 0.05).
- **Out-of-support table.** Medians of `qreal`, `h_top1`, `wb5_new`, `v5`, `age`, `new5` on October picks against the discovery picks (VERIFY section 6: `qreal` 142.65, `h_top1` 0.0365, `wb5_new` 0.984, `v5` 628.7, `age` 995, `new5` 414) and against September's C1-NF picks.
- **Ledger coverage and drift.** Section 11.2: per decision date, the share of the 5-minute buyers and sellers known to the ledger, against the exploration picks.
- **V* and overhang strata** (JUDGE-4 leaf-4 ingredients), as removed-versus-kept per date.
- **Declared observation.** The text of section 5.1 and, if recorded, the shadow's and canary's results, labelled as not the read.

## 9. Multiplicity and disclosures

- **Within the family:** k = 1. One deciding cell; two looks; alpha 0.025 split by Bonferroni over looks (section 0).
- **Across families:** DEC-025 opens a third alpha slot of 0.025 (section 2 of the DEC). With EXP-022 and EXP-024 the promotion-eligible October families can carry an overall rate of up to 0.075, not 0.05. The owner's OK on this (O1) was confirmed by the manager on 2026-10-09 (DEC-025 `OWNER_DECISION_CONFIRMED`).
- **Selection, in full [measured or counted from the hunt files]:**
  - C1's primary was the best of about 24 walk-forward cells, after a 144-cell split scan (JUDGE-4 section 3.3).
  - The farm cap was chosen **after** the read, from about 4 post-hoc splits in `x06_posthoc.py` (real quote < 400, age < 1,800 s, a combined split, `h_top1`).
  - About 40 hunts have read the four September blocks (JUDGE-4 section 1); exp011-0909 carries several pooled passes.
  - The DEC-014 m is >= 15 from this merge, and about 37 counting the unlisted readers [inferred].
- **What VERIFY did and did not do.** It rebuilt the pipeline from the pinned scripts, reproduced the frozen primary at 99.25% of C1's trades (Jaccard 98.53%; the gap is the non-deterministic ledger, section 11.1), re-derived `h_top1` independently (causal, max relative difference 5.9e-8), and ran an independent simulator. No kill rule fired. It is not evidence of an edge: the cap was chosen after the read and the September blocks are spent.
- **Decay and instability.** Block means (flat, 505,000, no rent) were +8.823 / +5.898 / +12.974 / +4.794% (fresh-0903, exp011-0909, fast-pool-0918, oracle-insample-0922). The second half of September was 8 of 11 days positive with the negative days at n of 3, 1 and 4. The `v5` and `new5` medians are higher in September than in discovery. October is unmeasured.
- **Known feature-drift risk: the wallet-ledger hole.** The exploration tape ends 2026-09-25T07 and the first forward-1002 hour is 2026-10-02T15. The manager's live-build plan places the hole in the live view at 09-25T07 to 10-05T05. For the read, wallets seen only inside the hole look new, so "new wallet" shares are inflated and creator history is undercounted: the creator features (`cr_prev_creates`, `cr_prev_grads`, `cr_known_out`, `cr_lmax6`, `cr_lend6`, `cr_big6`) see only the tokens that `tokens.parquet` and `out/mout` know, and the hole removes the creators' tokens made inside it. The look directories carry the exploration history (section 11.2), so the hole thins that history but does not reset it. The size of the effect on picks is unmeasured. Section 11.2 gives the ruling. Mitigating, not proof: the training tape already contains two ledger holes (08-28T12 to 09-03T12 and 09-15T12 to 09-18T23).
- **October regime.** BOOST, event V, the 200 ms slots (about 10-09T14:30Z) and PDA pools are not in the September evidence. The read prices on them.
- **Looser than JUDGE-4 on one point.** JUDGE-4 section 3.3.7 applied the whole gate at 1.3 s and at 1.9 s. Here 1.9 s decides only as B1 (mean > 0 and ex-top-3 > 0 on both legs, item 9); the full gate at 1.9 s is report-only. VERIFY shows 1.9 s close to 1.3 s (without rent: flat +8.203% against +8.151%, pressure +6.902% against +7.220%), so the difference is small, but it is a loosening and is disclosed as one.
- **Not exchangeable with H5 or CAP-PICK.** C1-NF is BOOST-independent per JUDGE-4; a BOOST switch-off kills H5 and CAP-PICK together, not necessarily C1-NF.

## 10. Preconditions and integrity

**Before 2026-10-10T00:00Z.** If any is missing, C1-NF is withdrawn before counting and no outcome is read:
- **P0.** This file is merged, with quant-proof OK on its final head.
- **P1.** DEC-025 and its amendments (DEC-016 Am.8, DEC-021 Am.3, EXP-012 Am.4, EXP-022 Am.4) and the ledger edit are merged in the same PR, and DEC-025 carries `OWNER_DECISION_CONFIRMED` for O1, O2 and O3 (confirmed by the manager, 2026-10-09). If the merge slips, section 0's late-merge rule is the only way to start later.

**Before 2026-10-16T01:00Z, the first walk-2 hour.** If any is missing, the walk-2 part cannot be read and the look is NOT_DECIDABLE (it spends the look, section 11.5). Their choices are pinned in section 2.5; only their execution is open:
- **P2. Deterministic rebuild (E0-C1).** Run the rebuild of `verify/run_rebuild.sh` with `ledger/01_wallet_daily_det.py` in place of the original ledger step, as one MiScusi job on research-0 (about 35 minutes, at most 2 workers, memory-capped). The pinned scripts are not edited: the exploration run copies them to a working directory and applies only the path patches of `verify/PATCHES.diff`; the October runs apply `patches/common2_look{1,2}.patch` (section 2.4). Record the sha256 of each patched file. Record the sha256 of the new `disc.npz`, `conf.npz` and the pinned `16_confirm.py` primary in a dated, outcome-blind amendment, and copy the three to a backed-up path. **Acceptance (reproducibility, not tuning):** the rebuilt unconstrained primary at 1.3 s must have n within 3% of 8,797 and, in spec order, the C1-NF book n within 400 to 440. A rebuild outside these bands withdraws C1-NF. It does not choose another cap, threshold or ledger.
- **P3. October adapter (section 11.2)** built, merged with quant-proof OK, and pinned by blob sha. Its E0, on exploration day **2026-09-20**: run on that day's raw walker files it reproduces `ref/convert.py`'s trade rows and `ref/build_shared.py`'s `tokens` rows by md5, the event-V mapping fixture of section 2.4 passes, and each look's `universe.parquet` gives every exploration token the same `mid` as P2's (section 11.2).
- **P4. Read tool** merged with quant-proof OK: adapter + the two pinned patches + pinned pass A + daily retrain + the cap + book + legs + section 7. Its E0, on exploration day **2026-09-20** (quant-proof R11): the pricing layer's **P&L equals `verify/v2_sim.py`'s per trade to the lamport**, shown by equal md5 over (mint, decision time, leg, pnl in lamports) for that day's C1-NF selections; and the tool's statistics (n, days, days positive, means, trade CI, date CI, totals, ex-top-3, ex-best-day, day-level t) **equal `verify/v3_report.py`'s output** on VERIFY's simulation rows. (VERIFY's 99.4% agreement compared pass-A columns with `v2_sim.py` and is not this bar; on the 419 C1-NF rows that agreement was 89.0%.)
- **P5. The pick oracle** (section 5.3) exists, with the pinned union source, is tested for a boolean-only answer, and is wired to a fail-closed shadow, by 2026-10-16T01:00Z. Look 1's 10-16 date already needs it. Without it the walk-2 part, and with it both looks, is NOT_DECIDABLE.
- **P6. `forward-1002ev`: the event-V re-walk of forward-1002 `[2026-10-09T00, 2026-10-16T01)` (quant-proof; the shared V source).** forward-1002 was walked without event V, and the read needs per-print V. EXP-024 Look 1 prices the same hours and needs the same V solution, so **EXP-024 and EXP-025 share this one pinned V source**; moving EXP-024 section 4 from `getTransaction` to it needs its own dated amendment, best merged before 2026-10-10T00Z, and if later it must be an equal-data substitution shown by forward-1002ev V matching `getTransaction` V on a pinned sample of s0, landing and exit prints. A separate builder on branch `claude/forward-1002ev` writes the walker, the join tool and the EXP-024 / DEC-016 amendments. This file references that branch and does not edit it. Pinned here:
  1. **Seal.** `[10-09T00, 10-16T01)` lies inside the FINAL's window, so forward-1002ev is a second copy of sealed hours: same owner, not a new block (ledger note; DEC-016 amendment on the builder's branch). **Before the FINAL marker only the walker, `backfill_verify` and hour counts touch it.** The cross-check below opens forward-1002 rows and runs only after the FINAL.
  2. **Coverage starts at 10-09T00.** Graduations from 10-02T15 to 10-08 get no V0, cannot pass the universe V band, and so supply no October training row and no `mout` creator outcome. Training is the 36 exploration days plus October universe rows from graduations at or after 2026-10-09T00 (section 2.2). The earlier forward-1002 hours feed only the ledger, creates and market counts. This departs from RULE.md and is disclosed (section 9).
  3. **Tape and cross-check [my call].** The tape for `[2026-10-09T00, 2026-10-16T01)` is **forward-1002ev read directly**, so every counted date comes from the same decoder as walk 2. forward-1002 is the cross-check, joined **on the raw JSONL** (the hunt-layout trade table has no `signature` column, and main's decoder may emit events forward-1002 lacks, which shifts `event_index`), requiring equal `sol_lamports`, `token_raw` and both reserves. **An hour whose 1:1 match rate is below 99.5% is a bad hour (R1).**
  4. **Fallback scope.** `getTransaction` fills only the states that price a counted trade: the decision print (for the guard), the landing state and the exit state. It cannot rebuild per-print V for features across a bad hour. Feature coverage stays under R3 of section 11.4; counted trades fall under the missing-V rule of section 6.
  5. **Decoder.** The same decoder blob for forward-1002ev and walk 2. `tools/pump_history_backfill.py` is blob `9a8bebb32adcf86de060b55f5a08110d11c0a550` at main `a3e923c` [measured 2026-10-09]. The dated amendment that records forward-1002ev's completion records both runs' blobs, and the read tool refuses unless they are equal (R13).
  6. **Credits.** About 3.3M [inferred; quant-proof's arithmetic: after about 10-09T14:34 an hour is about 18,000 slots, so the range is about 2.98M slots at about 1.16 credits per slot]. The approved 3.0M may be short before any fallback calls. Record the credits used (CLAUDE.md). Run as a MiScusi job. **Hours not walked are bad hours (R1).**

**Before each look (outcome-blind; counts and hashes only):** the section 11.3 precount, the P7 pricing check, and the section 11.4 refusals.
- **P7. Pricing check on October prints [pinned; quant-proof S2 and T1; the form of EXP-024 section 10 P7].** One sample of 1,000 canonical-pool PumpSwap prints, spread evenly over the look's V-covered hours (`[2026-10-09T00, look end)`; one deterministic stride; outcome-blind; no P&L). Two lines on that sample, and **both must hold**:
  1. **Constant product (first line).** On at least **99%** of the sampled sells, the gross quote out equals `(quote_reserve_mapped + V0) * base_in // (base_reserve + base_in)` within **1 bp**; this is the same field and law as `test_constant_product_uses_vault_plus_event_v` in `tools/test_walk2_event_v.py`. On at least **99%** of the sampled `buy` prints (excluding `buy_exact_quote_in`, whose fields differ), `token_raw` equals `base_reserve * qin // (quote_reserve_mapped + V0 + qin)` within **1 bp**.
  2. **Fee tier (second line, kept).** Sells must match the fee tier at the mapped Q (`quote_reserve_mapped + V0`, which is vault + V(t)) within 1 bp on at least 75% of the sampled sells; buys must match the implied tier fee within 1 bp on at least 90% of the sampled buys (EXP-024 section 10 P7).
  **Why the constant-product line leads.** A mapping that ignores pending fees misses the price level by about 2% to 4%, and the exact integer law separates a correct mapping from an incorrect one by orders of magnitude (it is exact on #467's sell and v1-buy fixtures), while the fee-tier line is coarser. **If either line fails, P7 fails, R14 fires, and the look is NOT_DECIDABLE and spent under section 11.5.** P7 prints the sample size, the matches per line and the shares, and nothing else.

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
- `hunt-shared/tokens.parquet` and `bars_1m/` with the columns `ref/build_shared.py` writes (`grad_src`, `v0_lamports`, `ps_first_ms`, `pool`, `create_ms`, `complete_ms`, `complete_slot`, creator and bonding-curve stats, `ps_first_price`). **`tokens.parquet` is the exploration file, byte-identical, plus the adapter's October rows** (below).
- `wl/<day>.parquet` from `ledger/01_wallet_daily_det.py`: **P2's 36 exploration days plus the October days.**
- **Look-directory assembly (quant-proof S1) [pinned].** Pass A reads its history from the look directory: `10_meta.py` builds `creates.parquet` from `{SH}/tokens.parquet`, `12_passC.py` reads every `{O}/out/mout/*.parquet`, and `11_passA.py` reads every `{O}/wl/*.parquet`. If those held only October rows, every October row's creator-history features (`cr_prev_creates`, `cr_prev_grads`, `cr_known_out`, `cr_lmax6`, `cr_lend6`, `cr_big6`) would be computed from October history alone, while the model was trained on rows whose history starts 2026-08-14: a silent feature drift VERIFY never tested. So each look directory holds:
  1. `hunt-shared/tokens.parquet` = the exploration `tokens.parquet` (sha256 recorded in the P2 amendment), byte-identical rows, with the adapter's October rows appended;
  2. `out/mout/` = P2's exploration `mout` files plus the October pass-A output;
  3. `wl/` = P2's 36 deterministic days plus the October days;
  4. `work/universe.parquet`, `creates.parquet` and `mkt.parquet` rebuilt by the pinned `10_meta.py` on that `tokens.parquet`.
  **The E0 (P3, P4) must show that the look's `universe.parquet` gives every exploration token the same `mid` as P2's.** `mid` is `row_number() OVER (ORDER BY complete_ms, mint)` and the copied `mout` rows are keyed by it; a different `mid` breaks the own-token exclusion in pass A (`sel = kmid != mid`). October tokens complete later, so they extend the numbering rather than renumber it.

**Transformations that differ from September (all pre-declared).**
1. **Strict lines.** The reader refuses bad lines (a NUL byte, or a line that is not a JSON object) and the hour that holds one is bad (`--strict-lines`; EXP-022 section 10 "Bad lines"). Bad hours are counted and reported; they are never skipped silently.
2. **Event V.** `quote_reserve := vault + V(t) - V0` from the PRE-trade state of each print (section 2.4), with `V0` written to `tokens.v0_lamports`. The event V comes from `--event-v` (#467). Walk 2 and `forward-1002ev` use the same decoder blob (P6); the earlier forward-1002 hours (before 10-09T00) carry no V and feed only the ledger, creates and market counts.
3. **Canonical pool by PDA.** The canonical PumpSwap pool of a graduated mint is derived by PDA instead of the September V band. The match to the tape's pool must be >= 95% of non-Mayhem completes (refusal R2).
4. **Slot time.** Seconds per slot are measured per UTC hour from the clock (the pinned pass A does this); after about 10-09T14:30Z they are 200 ms.
5. **Universe filter.** `v0_lamports` between 17.5e9 and 17.7e9 and the first print within [-5 s, +120 s] of `complete`, as RULE.md says. The adapter fills `tokens` so the pinned `10_meta.py` selects the same universe; it does not change the filter.
6. **Allowlist and patches.** The adapter materialises only the look's allowlisted hours (section 4) into the look's `tape/`, and the two pinned patches of section 2.4 set the segment and the paths. Nothing outside the allowlist is written, and the read tool refuses it (R12).

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

It runs on the real October layout after the adapter is built and the FINAL is written, **before any P&L**, with the model trained on the 36 exploration days only (no October label). A first-time try on fixtures is not a substitute: fixtures miss loader bugs (the lesson of the EXP-016 and EXP-021 try-spending runs). It is run once for each look, on that look's window and allowlisted hours only (7 dates for Look 1, 14 for Look 2). It prints, per UTC date of the window: graduations, universe rows, stage-1 rows, selections at prediction > 0.02, kept rows after the cap, rows dropped by the oracle (walk-2 dates), bad hours, PDA-match share, V-coverage share, ledger-coverage share, fallback slot-time hours. It prints no price, fill, exit, pnl, mean, CI or day sign.

### 11.4 Pre-declared refusals (NOT_DECIDABLE; outcome-blind; none is a pass)

- **R1.** More than 2% of the ledger and counted hours (`[2026-10-02T15, 2026-10-17T02)` at Look 1, `[2026-10-02T15, 2026-10-24T02)` at Look 2) are bad or unverified, or any hour a counted attempt needs (from its mint's create hour through the hour that holds the landing block_time + 300 s + 60 s) is bad (that attempt is excluded and counted; more than 5% of attempts excluded makes the look NOT_DECIDABLE). **A bad hour includes** an hour of `forward-1002ev` that was not walked, and an hour whose raw-JSONL cross-check against forward-1002 has a 1:1 match rate below 99.5% (P6).
- **R2.** The PDA canonical-pool match is below 95% of non-Mayhem completes.
- **R3.** Per-print V coverage on canonical-pool prints of universe pools is below 95%.
- **R4.** The precount's kept selections over the look's window are below 90 (Look 1) or 150 (Look 2), or fewer than 5 of 7 (Look 1) or 8 of 14 (Look 2) dates have a kept selection. These are about 62% of the expected 140 and 280.
- **R5. No refusal on a high selection count.** A flood of selections is the failure C1 had on the farm. It stays a possible FAIL and is reported; it is never an escape hatch.
- **R6.** More than 5% of hours use the nearest-hour slot-time fallback of the pinned pass A.
- **R7.** The pick oracle is missing, stale, errors, returns a non-boolean, or is undecided for any in-scope walk-2 mint (at either look).
- **R8.** P2 to P6 are not all met by their deadlines.
- **R9.** Decision stability: a re-derivation of any decision from the window start must match the first by md5, or the read refuses. Look 2 re-derives Look 1's decisions (10-10 to 10-16) and they must match Look 1's record by md5, or Look 2 refuses.
- **R10.** A seal breach is not a refusal: the read runs and is reported compromised (section 4).
- **R11.** Missing V (section 6): a counted trade priced under the missing-V rule is among the top 3 trades of either leg, or such trades are more than 1% of the look's trades.
- **R12.** The tool is asked to open an hour outside the look's allowlist (section 4).
- **R13.** The recorded decoder blobs of `forward-1002ev` and walk 2 differ, or the E0 records of P3 and P4 are missing.
- **R14.** Either line of the P7 pricing check fails (or its sample is empty on either side).

### 11.5 Spending [pinned; the form of EXP-024 section 11]

- From 2026-10-10T00Z, any refusal, withdrawal, non-run or NOT_DECIDABLE of a look **spends that look's alpha and window.** The alpha is not carried to the other look. Those hours have begun and are watched by the shadow and the canary (section 5.1); filing again on them would be a forking path (let the oracle fail, then re-file on the tape that looked good).
- **C1-NF v1 is never re-filed on any hour in `[2026-10-10T00, 2026-10-24T02)`.** No forward-1002-only variant is filed after the fact.
- A failure before 2026-10-10T00:00Z, before any hour has begun (P0 or P1), does not spend; section 0's late-merge rule is then the only way to start later, and only at `C = 2026-10-11T00`.
- A look that runs and FAILs is closed (section 7).

## 12. What is not decided here

- The live canary's design. It runs only under DEC-026 (to be written), with 0.02 SOL stakes and a separate wallet; scale-up beyond canary size needs a passed read or a further explicit owner override (section 5.1).
- Any sealed-block claim (none is made).
- Any change to EXP-012, EXP-022, EXP-024, the shadow detector or the H5 canary, other than the disclosures in the amendments listed in P1.

## Amendments

### Amendment 1 (2026-10-09, before any counted hour): P7 line 1 runs on raw events

No row of forward-1002, forward-1002ev, walk 2, a forward-paper book or a runner was opened, priced or printed to write this amendment, and no key was used. It was written from the repo's decoder and store code (`observe/trade_decode.py`, `observe/trade_store.py`) and its fixture tests (`tools/test_walk2_event_v.py`), all at main `fcc7e99`. The rule block, the windows, the two alpha lines, items 1 to 9 of section 7, the two 99% bars and P7's consequence (R14) are unchanged. Two details of line 1 are **aligned with the price-level rule of EXP-024** (quant-proof E2 and E3 on #505, relayed by the manager 2026-10-09; EXP-024's own text is not quoted here), so that both families price October the same way: which prints are comparable, and a 2-unit alternative to the 1 bp match. Three further changes come from quant-proof's review of #507 at `12f2b2e` (E1 to E3): the sample frame holds only pools that have a V0; `quote_reserve_mapped` is the adapter's written column; the buy side is topped up to 100 comparable buys. Part 7 lists exactly what differs from section 10. Labels: **[measured]** is read from code or a test; **[pinned]** is a rule this amendment fixes; **[inferred]** is reasoned; **[est]** is an estimate.

**1. The defect [measured from code; found by quant-proof's review of PR #505, the forward-1002ev PR].**
- Section 10 P7 line 1 compares the gross quote out of a sell, and the quote input `qin` of a buy, with the constant-product law. A stored PumpSwap tape row has neither. `stored_trade` (`observe/trade_store.py:49`) keeps `sol_lamports` (the user's amount, fees included: `user_quote_amount_in` and `user_quote_amount_out`, `observe/trade_decode.py:404` and `:435`), `token_raw`, `quote_reserve`, `base_reserve`, `pool`, `slot`, `signature`, `event_index` and the event-V keys. It **drops `pool_quote_amount`, `lp_fee`, `protocol_fee`** and `creator_fee`. The walker writes `stored_trade(record)` rows (`backfill_trade_row`, `tools/pump_history_backfill.py:371`), so `forward-1002ev` and walk 2 do not carry them.
- The raw event does. `observe/trade_decode.py` decodes event bytes `[64:72]` to the key **`pool_quote_amount`** on a buy (`_decode_buy`, line 413, comment "CP input") and on a sell (`_decode_sell`, line 445, comment "CP gross that leaves the pool"); `seal_trade` keeps it (line 534). That is the IDL's `quote_amount_in` on a BuyEvent and `quote_amount_out` on a SellEvent [inferred from the offsets and the comments]. The fixture test reads the same bytes: `test_constant_product_uses_vault_plus_event_v` takes `blob[64:72]` as the sell's gross and the buy's `qin`.
- So line 1, as section 10 wrote it, can not be computed from the tape. This amendment changes where its inputs come from, and aligns the two details named above with EXP-024.

**2. P7 line 1, amended [pinned].** Line 2's rule (the fee tier) is unchanged and was not re-examined here. Both lines run on **one sample of 1,000 prints** (section 10 P7), drawn once, outcome-blind, from one frame that is fixed before the draw. No sampled print is dropped, replaced or re-drawn after it is fetched.
- **Frame [pinned; quant-proof E1].** The frame, for both lines, is the canonical-pool PumpSwap prints of pools that have a **non-null `tokens.v0_lamports`** in the look's `tokens.parquet`, over the look's V-covered hours, in (slot, tx_index, event_index) order (`p7_raw_frame`). It follows EXP-024's frame (V-range pools). Why: section 10 P6 item 2. Graduations from 2026-10-02T15 to 2026-10-08, and older pools, have no V0 (V coverage starts at 2026-10-09T00) and can not pass the universe V band, so a print of such a pool has no V0 to price with. `no_v0` can therefore never occur and is not a reason; a V0 that is not an integer reaching the check is a defect in the caller, and the check raises.
- **Main draw [pinned here; section 10 gave no formula].** The start of each of 1,000 equal segments of the frame: the prints at indices `(k * N) // 1000` for k = 0 to 999, N being the frame size, or the whole frame if N is 1,000 or fewer (`p7_raw_main_draw`). One deterministic stride, spread evenly over the look's hours.
- **Population, fixed by the sample before any fetch.** A sampled row is **not comparable** and is in neither denominator if, tested in this order (`P7_RAW_EXCLUSIONS`): its tape row has `zero_sol` (either side): `zero_sol`; its `side` is neither `sell` nor `buy`: `not_buy_or_sell`; it is a buy whose tape `ix_name` begins with `buy_exact_quote_in` (that is `buy_exact_quote_in` and `buy_exact_quote_in_v2`; their `quote_amount_in` is net of the protocol and creator fee, as the docstring of `test_fee_keeping_leaves_vault_plus_v_changed_by_cp_input_only` says): `buy_exact_quote_in`; or it is a buy with no `ix_name` (missing, null or empty), which can not be told from that family: `no_ix_name`. The comparable sells are the other sampled `sell` rows and the comparable buys the other sampled `buy` rows. Tape rows keep `ix_name` and `zero_sol` (`EVENT_V_KEYS` and `stored_trade`).
- **Buy top-up [pinned; line 1 only; quant-proof E3].** If the 1,000 hold fewer than 100 comparable buys, say h, add `need = 100 - h` comparable buys of the frame that are not already in the sample, drawn by a second fixed stride over the same hours, offset half a stride: the comparable buys not yet drawn, B of them in frame order, are cut into `need` equal segments and the pick is the midpoint of each, at index `((2k + 1) * B) // (2 * need)` for k = 0 to `need - 1` (`p7_raw_buy_topup`). The selection reads only the tape's pool, key, `side`, `zero_sol` and `ix_name`, all fixed before any fetch. If B is `need` or fewer, which is when the hours hold 100 comparable buys or fewer in all, all of them are used. Line 1's buy population is then the sample's comparable buys plus the top-up. Line 2 is computed on the 1,000 only. The 99% bar is unchanged, and a side with none still fails.
- **Fetch.** `getTransaction(signature)` with `encoding` `json`, `commitment` `confirmed` and `maxSupportedTransactionVersion` 1 (the walker's getBlock settings, `tools/pump_history_backfill.py:1080-1083`). Up to 3 attempts per transaction (`P7_RAW_TX_ATTEMPTS`). A transaction that holds several sampled prints is fetched once.
- **Decode.** `records_from_logs(meta.logMessages, slot=<response slot>, signature=<signature>, t_recv_ms=0, commitment="confirmed", feed=<any>, event_v=True)`, from `observe/trade_decode.py` at blob `238942a6b3c5425389eddfde4d11268c300acbec` [measured: the blob at `a3e923c` and at `fcc7e99`, unchanged since #467]. The tool refuses on another blob; recording a new one needs a dated, outcome-blind amendment before the P7 run (the form of P6 item 5). The record is keyed by **(slot, signature, event_index)**: `event_index` is the position in the transaction's decoded trade list, the walker's own index.
- **Identity.** The keyed record is the sampled print only if `slot`, `signature` and `event_index` are equal and `pool`, `side`, `sol_lamports`, `token_raw`, `quote_reserve`, `base_reserve`, `virtual_quote_reserves`, `ix_name` and `zero_sol` equal the tape row's (the fields of P6 item 3's cross-check, plus V, `ix_name` and `zero_sol`; `zero_sol` is absent on a comparable row, so a raw event that carries it is a different print). The adapter row (next bullet) is the same print only if its `slot`, `tx_index`, `event_index` and `pool` equal the tape row's and its `base_reserve` and `token_raw` equal the raw event's; otherwise it is an `identity_mismatch`.
- **Law inputs [pinned; quant-proof E2].** `quote_reserve_mapped` is the **adapter's written column**, not a rebuild from the raw vault and V(t): the `quote_reserve` of the row of the look's materialised `tape/trades/<hour>.parquet` at `(slot, tx_index, event_index)` with the same `pool`. That is the column pass A prices on. The sampled tape row carries `slot`, `tx_index`, `event_index` and `pool` (the walker writes `tx_index` on every trade row). `V0` is `tokens.v0_lamports` of the pool, which the frame guarantees exists. `base_reserve`, `token_raw` and `pool_quote_amount` are the raw event's. **A missing adapter row is a miss** (`no_adapter_row`). So P7 checks what pass A will actually read, adapter code included: the pinned mapping (section 2.4), its V0, and the writing of the column. An adapter that writes the gross vault fails P7 (tested).
- **Sells.** `pool_quote_amount` equals `(quote_reserve_mapped + V0) * base_in // (base_reserve + base_in)` within **1 bp** or **2 lamports**, with `base_in` the sell's `token_raw`, on at least **99%** of the comparable sampled sells.
- **Buys.** `token_raw` equals `base_reserve * qin // (quote_reserve_mapped + V0 + qin)` within **1 bp** or **2 base units**, with `qin` the raw event's `pool_quote_amount`, on at least **99%** of the comparable buys (the sample's plus the top-up).
- **Match** (`event_v_map.within_tolerance`, `P7_CP_TOLERANCE_UNITS` = 2): the raw field is within 1 bp of the integer law (`|actual - model| * 10,000 <= |actual|`, `within_bp`) or within 2 units of it (`|actual - model| <= 2`), in integers, `actual` being the raw field. The unit alternative is for dust trades, where 1 bp is less than one unit and a rounding step would be a miss. It is the wider test only for an amount of 20,000 units or less; above that 1 bp is. It can pass a dust print whatever the mapping, so the 99% bar is carried by the larger prints; an adapter column that ignores pending fees misses by 2% to 4% of the amount, far outside both tests on any print above about 100 units.
- **Unresolved prints are misses.** A comparable sampled print with no usable raw event counts as a miss on its line and **stays in the denominator**. The reasons are a closed list (`P7_RAW_REASONS`), tested in this order: `fetch_failed` (every attempt failed, or the transaction is null), `no_record` (no record at that key), `no_adapter_row` (no row of the look's `tape/trades` at the print's key), `slot_mismatch`, `identity_mismatch`, `field_missing` (a field the law reads, the adapter's `quote_reserve` included, is absent or not an integer). There is no `no_v0` reason: the frame holds only pools with a V0.
- **Outcomes are unchanged.** The two 99% bars and the consequence are section 10's. **A side with no comparable event fails that side.** If either line of P7 fails, or a side fails for that reason, P7 fails, R14 fires, the look is NOT_DECIDABLE, and it is spent under section 11.5.
- **Printed:** the frame size, the sample size, the top-up size, the comparable sell and buy populations, the number not comparable with its count per cause, the hits and shares per line, the unresolved count per reason, and the credits used. Nothing else: no signature, pool, mint, amount, price or reserve. Signatures and per-print outcomes stay in new files in the job directory; the record carries their sha256.

**3. When it runs [pinned].** After the DEC-016 FINAL marker, and after the look's `tape/` and `tokens.parquet` are materialised (P3), since line 1 reads the adapter's rows; as part of the section 10 "before each look" checks. The sample is drawn from raw `forward-1002ev` rows and, for the walk-2 hours of the look's window, from walk-2 rows once those hours are sealed and verified; section 4 and P6 item 1 open them only after the FINAL. Look 2 draws its own sample over its hours, as P7 says.

**4. Credits [est].** One `getTransaction` per sampled transaction: about 1,000 calls per look, plus at most 100 for the buy top-up, fewer if a transaction holds more than one sampled print, and at most 3,300 with the retry cap. The repo prices a standard Helius call at 1 credit (`PUBLISHED_GETBLOCK_CREDITS`, for getBlock; getTransaction's price is not probed here), so about 1,100 credits per look at most and about 2,200 if both looks run, against about 3.3M for P6's walk. The P7 record states the credits actually used, the unit (the job and its Helius slot) and what the calls made possible, as CLAUDE.md asks.

**5. Seal disclosure [pinned].**
- No hash-only, md5, join or match-rate run on `forward-1002ev` happens before the FINAL, for this amendment or for P7. This is section 4 and P6 item 1 as written: before the FINAL only the walker, `backfill_verify` and hour counts touch it.
- P7's frame, its sample draw, its raw-event fetch and its comparison all run after the FINAL. They open tape rows (to draw the sample), the look's materialised adapter rows (to read the written column) and public chain transactions (to fetch the raw events). They open no `OUT/rows.jsonl`, no scratch file and no FINAL report, and no forward-paper or runner output.
- They compute no label, fill, exit, P&L, mean, CI or day sign (section 4). The law compares one quote amount with the program's own formula on the same print; it prices no book. P7 does not call `pick_oracle`, writes no CAP-PICK field and joins no print to a pick (section 5.3).

**6. Code and tests.** `ARTIFACTS/exp025/event_v_map.py` gains pure helpers (`p7_raw_frame`, `p7_raw_main_draw`, `p7_raw_buy_topup`, `p7_raw_draw`, `p7_raw_exclusion`, `p7_raw_line`, `within_tolerance`, `p7_raw_hit`, `p7_raw_check`, `p7_raw_tally`, `p7_raw_pass` and the `P7_RAW_*` and `P7_CP_TOLERANCE_UNITS` constants); `SHA256SUMS` carries the new sha256. `tools/test_exp025.py::RawEventP7` tests them: a real sell and a real buy from the repo's fixtures, decoded with the pinned decoder and the real `stored_trade`, hit exactly with a correct adapter row (and `stored_trade` is shown to drop `pool_quote_amount`); **an adapter that writes the gross vault fails P7** (a whole sample of misses, and one or two bad rows among 100 against the 99% bar), and so does a column off by an LP-sized amount; a missing or foreign adapter row is a miss; the frame drops pools with a null V0 and the check raises on a non-integer V0; the main draw is evenly spread and deterministic; the top-up brings comparable buys to 100 at the midpoints of `need` equal segments, reads only pool, key, `side`, `zero_sol` and `ix_name`, uses all comparable buys when the hours hold fewer than 100, and a frame with none fails that side; the 1 bp edge; the 2-unit edge on dust sells and buys (2 hits, 3 misses, and 1 bp alone would miss the 2); `buy_exact_quote_in`, `zero_sol` and nameless buys leave every denominator, each with its own cause; each unresolved reason is a miss that keeps the denominator; the 99% rule per side. The driver that looks up the rows, draws the sample, fetches and decodes is built with the read tool (P4); it must decide every hit through these helpers.

**7. What differs from section 10, what is not measured, what is not changed.**
- **Differs from section 10 P7 [pinned].** (a) *Population* (aligned with EXP-024): section 10 left out only `buy_exact_quote_in` buys; this amendment also leaves out every `zero_sol` row and every buy with no `ix_name`. That narrows the population. (b) *Match* (aligned with EXP-024): section 10 said within 1 bp; this amendment says within 1 bp or within 2 units. That loosens the match for dust prints only (part 2, Match). (c) *Frame* (E1): the sample of both lines is drawn only from pools with a non-null V0; section 10 said "canonical-pool prints" and did not say so. Line 2's rule is unchanged, but its sample is drawn from this frame. (d) *Price level source* (E2): `quote_reserve_mapped` is the adapter's written column at the print's key, which section 10 named but did not source. (e) *Buy top-up* (E3, line 1 only): line 1's buy side is topped up to 100 comparable buys from the same hours, so line 1 can fetch up to 100 prints beyond the 1,000. The main draw's formula (part 2) is pinned here because section 10 gave none. None of these moves a 99% bar, the law, the pinned mapping or the consequence.
- **Not measured:** no October print has been checked against the law; the fetch success rate; how many of the 1,000 will be comparable buys (a buy needs an `ix_name`, and router buys are `buy_exact_quote_in_v2`), and so whether the top-up will fire. The top-up secures 100 comparable buys when the hours have them; if they hold fewer, all are used and 99% of a small side leaves no room for a miss (29 of 30 is 96.7%); a side with none fails.
- **Not changed:** the rule block, the looks and windows, `EXP025_ALPHA_LOOK1` and `EXP025_ALPHA_LOOK2`, items 1 to 9 of section 7, the 99% and 75% and 90% bars, R14, section 2.4's mapping, section 11.5, and the rule of line 2 of P7.

### Amendment 2 (2026-10-09, about 17:30Z, the owner's decision; before 2026-10-10T00:00Z and before any C1-NF outcome exists): synthetic-migration pools stay in the universe

```
OWNER_SYNTHETIC_DECISION_EXP025: 2026-10-09 (owner, in session, asked by manager9 via AskUserQuestion). Question: "C1-NF's formal test starts counting at midnight UTC tonight. Synthetic pools (~35% of graduations now, zero in September's evidence) are inside its universe. Unlike H5, its rules have no synthetic-pool halt that could kill the test. Keep them in or exclude them? This must be filed before midnight UTC." Answer: "Keep them in (Recommended)".
```

**This amendment is blind to every C1-NF outcome. It is not called outcome-blind.**
- **No C1-NF outcome exists.** No C1-NF shadow or canary has run, and no C1-NF label, fill, exit or P&L has been computed on any October hour (section 0, "No outcome before merge"). Verified at 2026-10-09T17:53:08Z on mal-fast-0 by MiScusi job #468:
  - no unit or unit file matching `c1nf`;
  - no `c1nf` output directory under `$HOME/data`, `/var/lib/mal*` or `/var/lib/mal-live`;
  - no running `c1nf` process.
  
  The MiScusi job list holds only the exploration-only C1-NF VERIFY jobs (#415, #420, #421, #423).
- **What was in view:**
  - the program-upgrade review's counts (0 of 61 synthetic graduations before the 2026-10-08T16:20Z redeploy, 12 of 61 after);
  - the A3 monitor's `synthetic_share_high` readings, 6/19 = 0.316 (job #445) and 7/19 = 0.368 (one dry check);
  - the structure measurement `/data/mal/hunt-1008/h5-work/synthetic-1009/REPORT.md`: 35.0% of 160 classified graduations, the synthetic first print 0 s after migrate at the median (max 7 s), and H5 trigger counts by class. That report is H5's, counts only.
  - Aggregate H5 shadow outcomes were also in view. They are H5 outcomes, not C1-NF outcomes.
- **No synthetic class has been joined to any C1-NF record.**

**What it sets**
1. **The universe is unchanged.** Synthetic-migration pools (a pump `PostCompleteBuyEvent` in the curve-completing or the migrate transaction) stay in C1-NF's universe, its training rows and both looks. Nothing in section 2, the rule, the cap, the windows, alpha, the legs or the gate changes.
   - October training labels come only from graduations at or after 2026-10-09T00 (section 4). Those include synthetic pools, so the model's October training rows include them too. That is a consequence of keeping the universe, not a change.
   - **No later exclusion.** No later amendment excludes synthetic or unclassified pools from either look or from training. C1-NF v1 is never re-filed restricted by class on any hour in `[2026-10-10T00, 2026-10-24T02)` (section 11.5).
2. **Why it is kept.** This is a basis in structure only.
   - EXP-025 has no A3 halt that a rising synthetic share could fire: section 5 lists the A3 flags as allowed reading, not as a refusal.
   - Excluding the pools would cut Look 1's expected selections from about 140 to roughly 90–113 [inferred; quant-proof, 2026-10-09]. R4's minimum is 90 (section 11.4).
   - The live canary (DEC-026) trades the same population.
3. **What the test therefore measures.** The verdict is C1-NF's result on October's actual population, synthetic pools included. September's evidence had none (0 of 61 before the redeploy). Every report states this.
4. **The class is recorded, as structure only.**
   - At each look's precount (section 11.3), the precount also prints, per UTC date, the number of universe rows and kept rows that are synthetic, non-synthetic or unclassified. It prints counts only, never a price, fill, exit, pnl, mean, CI or day sign.
   - The class uses EXP-024 Amendment 4 B1 (discriminator alone, on the completing and the migrate transaction; monitor blob `1ca0a88cecf0853d94336ea046ba1a910b79f198`), the B2 rule that the tape can only mark a pool synthetic, and Clarification 1's B4 lookup with 1,000-signature caps.
   - An unclassified pool stays in the universe. It is counted, not excluded.
   - **The class counts are report-only.** No R item reads them. R4 and gate item 1 count all universe rows, whatever their class.
   - A failed fetch or any unclassified share never makes a look NOT_DECIDABLE. If classification cannot finish, the affected pools print as unclassified and the look runs on schedule.
   - The classification runs after the FINAL (section 4), and its Helius credits are recorded.
5. **Seal.** Before Look 2 is read (or, if Look 2 does not run, before the final C1-NF report), joining the synthetic class to any C1-NF outcome from any source is a breach. That covers a label, fill, exit, pnl, mean, CI, day sign, win or loss, or any field derived from one. A breach is recorded here, dated, and the read is reported compromised.
   - Section 5.1's real-time observation does not extend to splits by class.
   - Before the seal ends, no C1-NF shadow or canary record that carries an outcome also carries the class. The class is logged as a separate counts-only stream.
6. **After the final look, report-only.** The result split by class may be printed after the final look's verdict is written and recorded, labelled report-only. It never decides and never re-scopes either look.
7. **Effect.** This amendment carries quant-proof's OK on its final head and the owner line above, and it is to merge before 2026-10-10T00:00Z. If it merges after that instant, items 1–6 apply from the merge instant. Each look's report then lists the amendment as dated after the window opened, and states whether any C1-NF outcome existed at merge.

### Amendment 3 (written 2026-10-10T09:30:19Z, from `date -u`; before any canary send): declared observation for the canary and shadow outcomes, the canary's stake, the executor's CAP-PICK skip, observers, no split by synthetic class, report disclosure

**Where it comes from.** This is the draft in [DEC-026](../DEC/DEC-026-c1nf-live-canary.md) Appendix A, adopted as EXP-025's next amendment. DEC-026 section 4 and section 11 item 1 say it must merge before the first C1-NF canary send. Amendment 2 (#529) merged at 2026-10-09T17:53:58Z, so this is Amendment 3. Item 11 lists every change from the DEC-026 draft.

**This amendment is blind to every C1-NF outcome. This was checked when it was written and is checked again at merge.**
- **Written before any canary send.** At 2026-10-10T09:29:49Z (`date -u` on the host), MiScusi job #499 on `mal-fast-0` looked at names only:
  - no system unit or unit file matched `c1nf`, and no user unit matched except job #499 itself;
  - no process matched `c1nf`;
  - no entry under the job user's `$HOME/data`, `/var/lib/mal` or `/var/lib/mal-live` matched `c1nf`.

  DEC-026 grants the canary's key exception on `mal-fast-0` only (its "Amends" row), and there is no C1-NF executor unit there. So no canary send has been made. The writer did not look up any wallet. DEC-026 section 11 listed the second wallet (item 21) and its funding (item 22) as not done on 2026-10-09.
- **No C1-NF shadow or canary outcome exists, as far as names and status lines show.**
  - The MiScusi job list read at 2026-10-10T09:30Z (jobs #433 to #499) holds no C1-NF shadow, executor or dry-run job. Its only C1-NF jobs are the presence checks #468 and #499.
  - The shadow (#503) is an open draft at `50eeaa1` (guard unchanged at `9a80398`) and has not run on `mal-fast-0` (above).
  - The executor (#530) merged at 2026-10-10T08:59:53Z and is not installed (above).
- **What was in view.** To write this amendment the writer read:
  - DEC-026 (with Appendix A), and EXP-025 with Amendments 1 and 2;
  - the PR list and the bodies of #503 and #544;
  - `tools/c1nf_shadow.py` at `50eeaa1`, searched for its outcome guard only;
  - the MiScusi job list's names and status lines, and job #499's output;
  - for the edits of 2026-10-10T11:00:31Z (quant-proof's E1 to E3 on `abd413c`): quant-proof's review text, `tools/c1nf_shadow.py` at `9a80398` (its `OUTCOME_START_MS` and `outcome_allowed` lines only), DEC-026 Appendix A item 3, and #558's title and heading.

  The status lines show other families' metrics: H5 replay-proof counts, BOOST timing structure counts and hunt-r3 discovery numbers. None of them is a C1-NF outcome.

  No sealed block, forward-1002, forward-1002ev, walk-2, forward-paper or runner row was opened. No synthetic class was joined to any C1-NF record.
- **At merge.** No edit is made to this file after quant-proof's OK. Before merging, the merger posts a PR comment with `date -u` confirming that no C1-NF shadow or canary outcome (label, fill, exit, P&L, mean, CI or day sign) of any October decision has been computed or seen. The merge commit's instant is the merge instant and is recorded in LAB_STATE. If an outcome had been computed or seen, the PR is not merged as is: this paragraph is rewritten to say what was seen and when, item 6 lists this amendment as made with outcomes in view, and quant-proof re-OKs the new head.

**The gap it closes.** Section 5.1 declares the real-time observation for "decisions inside the windows" and names a 0.02 SOL canary. It does not cover:
- the canary's actual stake;
- decisions at or after 2026-10-24T00;
- the live executor's CAP-PICK skip (section 5.3 names only the read and the shadow);
- the observers and the report disclosure;
- the bar on splitting outcomes by synthetic class, which Amendment 2 sets for the read side.

1. **Pre-window decisions: no declaration.**
   - DEC-026's draft item 1 declared observation for decisions with T in `[<merge instant>, 2026-10-10T00)`. This amendment is written after 2026-10-10T00:00Z, so that interval is empty and the item declares nothing.
   - Every October decision before 2026-10-10T00 stays under section 4's seal.
   - In live mode the shadow's code guard writes the pick of such a decision but no outcome (`OUTCOME_START_MS` = 2026-10-10T00:00Z in `tools/c1nf_shadow.py` at #503's `50eeaa1` (guard unchanged at `9a80398`), not yet merged). Its replay-mode exemption covers exploration hours only. DEC-026 section 11 item 8 makes the guard binding. The merged executor never acts on a decision before 2026-10-10T00. It counts such a decision under the refusal `pre_window`, with no mint (`PICK_WINDOW_START_MS` = 1791590400000 in `tools/c1nf_executor.py` on main).
   - The O-6 default (DEC-026 section 3) is recorded but covers no decision. Decisions with T in `[2026-10-09T00, 2026-10-10T00)` are also October training-label input to both looks (section 4), so this is the stricter reading, not a loss of cover. Relaxing the guard needs a further dated EXP-025 amendment, with quant-proof's OK on its final head, written before any such outcome is computed or seen.
2. **Post-window decisions.** "The same outcomes for decisions with T at or after 2026-10-24T00, up to the canary's `end_ms`, are observed in real time. No look reads them." This is not a CAP-PICK exception (item 3).
3. **The executor's CAP-PICK skip.** Section 5.3's rule binds the read and the shadow. DEC-026's live executor follows the pick feed as built (DEC-024 Amendment 6 items 2 and 3, EXP-022 Amendment 6 item 1, DEC-026 Amendment 1 B), not section 5.3's pinned union source, which is read only after the FINAL.
   - From 2026-10-16T01 to the end of EXP-022's read, the executor buys a mint only on an exact `False` from the live `PickOracle` on `pick_file`. `True`, `None` (no row, a gate row with no score, a feed stale for more than 60 s, no FINAL marker, an error) or a non-boolean means no buy. It writes no CAP-PICK field into any record and joins no record to a pick.
   - **Residual, not declared here.** EXP-022's counted picks are the walk-2 replay's picks. They are not known at a C1-NF decision and differ from the online set both ways (EXP-022 Amendment 6 item 2). So the canary can fill, and the shadow can record, a replay-only pick. EXP-022 Amendment 6 items 2 to 4 declare that residual for H5 only. For C1-NF it is EXP-022's to declare, by its own amendment before 2026-10-16T01; this amendment does not declare it and does not call such a trade a non-breach. EXP-025's read is not affected: section 5.3 removes every mint the union source answers True for.
   - Before each CAP-PICK look, no person, agent or job joins a C1-NF canary or shadow record, or the canary wallet's history, to the replay's picks, or counts the overlap (EXP-022 Amendment 6 item 3, applied to C1-NF).
   - If the executor refuses a wider set than section 5.3's keying (DEC-026 section 9.1), each look's report says so.
4. **The canary's stake.**
   - Section 5.1's "0.02 SOL stakes" and "at most 0.1% of a pool's quote", and section 12's "0.02 SOL stakes", now read **0.05 SOL stakes** and **at most about 0.25% of a pool's quote** (0.05 SOL against a stage-1 real quote of at least 20 SOL).
   - The separate wallet and DEC-026 are unchanged.
   - The canary's trades remain real chain activity and appear in the tape as ordinary rows. They are not removed, because that would edit chain truth. The read reports them.
   - DEC-025 section 4 also names 0.02 SOL. DEC-025 is not edited here. DEC-026 section 1 records that the owner's answers supersede it.
5. **Observers, and no split by synthetic class.**
   - The owner, Helm, the manager, builders, the DEC-026 watchdog (its Discord alerts, including the stops and the wallet line) and the daily check may read the canary's ledger and the shadow's outcomes in real time.
   - The read tool may not. Its inputs stay section 4's hours. It never takes the canary's ledger or the shadow's output, and its refusal set is not loosened.
   - **No observer, and no report, note, alert, Console entry or message, may split C1-NF outcomes by synthetic class before the final look.** This is Amendment 2 item 5: "Before Look 2 is read (or, if Look 2 does not run, before the final C1-NF report)". The class stays a counts-only stream.
   - A breach is recorded here and the read is reported compromised.
6. **Report disclosure.** The Look 1 report and the Look 2 report each state:
   - that the shadow's and canary's outcomes were observed in real time for decisions in the windows (section 5.1) and, if used, for decisions at or after 2026-10-24T00 (item 2);
   - that this can have influenced later choices, for example an owner step to a larger stake;
   - that **any EXP-025 amendment dated after the first such outcome was made with outcomes in view.** The report lists it and does not call it outcome-blind.

   The disclosure does not change the verdict.
7. **Unchanged.**
   - These stay as they are: the rule block, the five pinned lines, the patches and hashes, the windows, the alpha pair (0.005, 0.020), items 1 to 9 of section 7, P2 to P7, R1 to R14, section 11.5, k = 1, and everything Amendments 1 and 2 set.
   - Section 5.1's sentences stand:
     - Look 1 and Look 2 are always read as written. They are never skipped, delayed, re-scoped or re-thresholded because of anything the shadow or canary shows.
     - Nothing the shadow or canary shows may change any EXP-025 parameter.
     - A canary halt, stop or step stops or changes the canary only.
8. **Spending.** Nothing is spent. No look is refused, withdrawn or left unrun because of this amendment.
9. **Enforcement in the tools.**
   - The shadow writes no outcome-bearing record for a decision before 2026-10-10T00 (binding; item 1).
   - The shadow and the executor write the class only to a separate counts-only stream, if they write it at all.
   - The daily check and the watchdog print no outcome by class.
   - The build status of these is DEC-026 section 11's (items 4, 8 and 10). This amendment does not claim that they are built.
10. **Provenance.**
    - This is a manager-side draft by the DEC-026 drafter (DEC-026 Appendix A), derived from DEC-025 section 4, EXP-025 section 5.1 and Amendment 2. A worker adopted it here with the changes of item 11.
    - The owner's words are the O3 sentence and the `OWNER_CANARY_CONFIRMED` line and addendum of DEC-026. DEC-026 Appendix A records that the owner has not seen this wording.
    - The owner may revoke it. A revocation applies from its recorded instant and restores section 4's seal for decisions after that instant. It does not undo the disclosure for outcomes already observed.
11. **Changes from DEC-026 Appendix A** (wording and dating only, apart from (c) and (h)):
    - (a) It is numbered 3, because #529 merged as Amendment 2.
    - (b) It is dated by its writing instant; the merge instant is the merge commit's, with no edit after the OK.
    - (c) Item 1 declares nothing, because this amendment is written after 2026-10-10T00. The draft had made item 1 depend on the O-6 default and held it in reserve.
    - (d) The blindness paragraph records job #499 and what was in view.
    - (e) Item 5 quotes Amendment 2 item 5 word for word.
    - (f) Item 9 says what is not yet built.
    - (g) The timing paragraph is updated, because 2026-10-10T00:00Z has passed.
    - (h) Item 3 follows DEC-024 Amendment 6, EXP-022 Amendment 6 and DEC-026 Amendment 1 B: the executor follows the pick feed as built, not section 5.3's pinned union source, and the replay-only residual is left to EXP-022 to declare (quant-proof's edit E1 on `abd413c`).

**Timing.** It is written at 2026-10-10T09:30:19Z, after 2026-10-10T00:00Z. DEC-026 section 8 allows this because the shadow's outcome guard is binding and the shadow has not run. **It must merge before the first canary send** (DEC-026 section 4 and section 11 item 1). It merges with quant-proof's OK on its final head (DEC-026 Q11).

**Why this cannot change a read.** EXP-025 sections 0, 3 and 7 fix the rule, data, analysis, pass bar and alpha pair. This amendment changes none of them, and it edits no pinned line, patch, hash or window. Section 5.1 already covers in-window decisions.

### Pointer (2026-10-10; not an amendment): seal end state

This read's seal end states and the release terms (quant-proof ruling (3a)) are recorded in [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md), Rules, "Clarification, 2026-10-10"; that text changes no rule, data, bar or timing of this read and releases no data.

### Amendment 4 (written 2026-10-10T10:34:03Z, from `date -u`; before 2026-10-16T01:00Z; outcome-blind, counts and hashes only): the P2 deterministic rebuild record

Numbered after Amendment 3 (#548, open when this was written). Amendments take numbers in merge order: if this merges before #548, the manager renames this heading to Amendment 3, and #548's to Amendment 4, at merge. The written instants stay as they are. This amendment records P2 (section 10) and the hashes that section 2.3 says a dated amendment records. It changes no rule, cap, threshold, feature or band.

**What ran [measured].** MiScusi job **#504** (`j_zbuqJu4lySNRfw`), on `mal-research-0`, role `exploration`, 6 CPU, 40 GB memory cap (peak 9.2 GB), 2 pass-A workers, repo at `b17dc0b`. It ran 38 minutes, ended 2026-10-10T10:33:22Z with exit 0, and ran once. It read the exploration tape only (`/data/mal/audit-1008/tape`, `/data/mal/hunt-shared`).
- **Script.** `run_p2.sh` (sha256 `20c8ac02950d734616ce4aaeb546bd53a9a9ea401804072b6e8c7f3302744f9a`). It runs the steps of `verify/run_rebuild.sh` in the same order: ledger, `10_meta.py`, `11_passA.py` per day (the 36 days of C1's `logs/A_*.log`, 2 workers, the same memory and load wait), `12_passC.py` (2 chunks), `14_export.py`, `16_confirm.py`. It differs in five places:
  1. **Ledger.** `ledger/01_wallet_daily_det.py` replaces `01_wallet_daily.py`. Its arguments are `--tape /data/mal/audit-1008/tape/trades --out <W>/wl --tmp <W>/tmp_duck`, with its defaults of 3 threads and 6 GB. Those are the original step's tape, output, temp directory and settings.
  2. **Working directory.** `W = /data/mal/hunt-1008/c1nf-p2`, a new directory. The script refuses to run if `wl/`, `out/cand/`, `out/candx/` or either npz already holds output. The only patch is the `common2.py` hunk of `verify/PATCHES.diff` (`O` and `TMP`), with its target changed from `/data/mal/hunt-1008/c1nf-verify` to `W`. **Why it is not applied verbatim:** verbatim, it points at VERIFY's directory. The ledger step skips any day already in `wl/`, and `run_rebuild.sh` skips any day already in `out/cand/`, so the run would reuse VERIFY's non-deterministic ledger and pass-A output. The `01_wallet_daily.py` hunk of `PATCHES.diff` is not applied, because that step is replaced and the deterministic ledger takes its paths as arguments. The diff applied is `common2_p2.diff` (sha256 `baa3870187d04a95f190c35eb595375e01f51daa5317950e4fcf3690cce9d42d`), with two changed lines.
  3. **`rule.json`** is copied from `ARTIFACTS/exp025/` (sha256 `e158e0b9…c5c196`, equal to C1's `ml/rule.json`, checked) instead of C1's directory.
  4. **No P&L printed.** `16_confirm.py`'s stdout stays in `logs/confirm_primary.log`. The script does not print it, and the writer did not open it.
  5. **Count step.** `p2_counts.py` (sha256 `a385abbad97b96103822b81492d23986e5f3df716db85adfd801a07acc1556c6`) follows the confirmation part of VERIFY's `v/v1_wf.py`. It re-runs the walk-forward with the pinned `mlcommon` (`lgb_params`: `deterministic=True`, seed 1, 3 threads) and `rule.json` (it asserts `model = lgb`, `exit = E3` and cap 0.5). From the pinned scorer's `confirm_primary_trades.npz` it opens only the `idx` array. It applies `c1nf_cap.apply_cap_before_book` before `mlcommon.book` (leg `p`, E3) and prints counts only.
- **Staged files (sha256; `staged_sha256.txt`, checked by `sha256sum -c` at job start).** Every pinned file equals section 2.3. Only `common2.py` is patched.

| File in `W` | sha256 |
| --- | --- |
| `scripts/common2.py` (**patched**: `O`, `TMP`) | `737631fbdb7e5367b71f86283a6575795574b7b68634fdde29d80b826ecf931f` |
| `scripts/01_wallet_daily_det.py` | `bc1838f10ff1798dec1e896da34d1a82b9707b404d2e36de317edf0034bf3be5` |
| `scripts/10_meta.py` | `5a25d0b122b5835ccea30922e73ce62a83e0a66af10d4f923495a09b7ba3346e` |
| `scripts/11_passA.py` | `24b1822e35a3b96337f8205370accc8eb9862d8411c9fda68296fb040ea79122` |
| `scripts/12_passC.py` | `22e6329415d40243ddcfab1b3f1793b25fd251cb9bf52be7e76e29300d6d0946` |
| `scripts/14_export.py` | `7dc2d151094defa349353fccdfdb92a6b542c9fa03828ad7dc77a3b3aecee868` |
| `scripts/16_confirm.py` | `c2fc497f141a9e650238beba26c4627eb51053a31e0e161ce6612cfc819ba0ae` |
| `scripts/mlcommon.py` | `bf783986e338cd924c2a99c5fbd3df2d5ec24d765772e654f44b5e63f4dca46f` |
| `scripts/c1nf_cap.py` | `e0335aeba5a9abec509e5e70d1070c77fd9e524b31456cb6c9ebdd7fc4ba6ba6` |
| `ml/rule.json` | `e158e0b9069a7db98a26c55b68f74055f44611330b2c636e68f37e4379c5c196` |

**Acceptance (section 10 P2) [measured, job #504; counts only].**

| Count | Band | Rebuilt n | In band |
| --- | --- | --- | --- |
| Unconstrained primary at 1.3 s (leg `p`, E3; the pinned `16_confirm.py` book) | 8,797 ± 3%, so 8,534 to 9,060 | **8,786** | yes |
| C1-NF book, spec order (`h_top1` ≤ 0.5 and not NaN, before the per-mint book) | 400 to 440 | **422** | yes |

- **P2 is accepted.** P2 does not withdraw C1-NF. Acceptance shows reproducibility only. It says nothing about P&L, and no P&L was computed for this record.
- The count step's recount of the primary equals the pinned scorer's book row for row: 8,786 rows with the same indices.
- Other counts: 806,592 discovery rows and 625,889 confirmation rows; 21 confirmation days; 49,738 stage-2 selections (rows, before the book), of which 1,515 survive the cap.
- VERIFY's rebuild on the non-deterministic ledger had 8,797 (C1's file 8,799) and 419. This one has 11 fewer primary trades and 3 more C1-NF trades. That spread is what the ledger change can produce (section 11.1: `cash` sign flips change the `skill` feature) [inferred; not analysed further].

**The read's training set [measured; section 2.3].** These replace VERIFY's `disc.npz` / `conf.npz` (`4b68d55b…` / `0dc37940…`), which stay reference only.

| File | sha256 |
| --- | --- |
| `ml/disc.npz` | `c4138e08675e17a3d139ed946ea308835d9faa6e87a36948f15fd92952dfa61f` |
| `ml/conf.npz` | `87bba5689a5968a9e9fb39735768e6a673d6de92929bdc29adcc5f315baf4026` |
| `ml/confirm_primary.json` (the pinned `16_confirm.py` primary; holds P&L, hashed and copied, not opened) | `0b5c2bbc58bafa45cf1eb46b157671532b34361bbb9caf20bbfa1eec0a2f1fd5` |
| `ml/confirm_primary_trades.npz` (the same; only `idx` was opened, by the count step) | `404a0df40bd5395e89d12f663d3c3fa69bebc394fac25969989231d27b6a8033` |
| `scripts/16_confirm.py` (pinned, unedited) | `c2fc497f141a9e650238beba26c4627eb51053a31e0e161ce6612cfc819ba0ae` |

**Look-directory inputs (section 11.2) [measured].**
- Exploration `hunt-shared/tokens.parquet`: `2c01a6d4b140e3710f45234594881f9bb26428da101f4b426bafa68fbb2a2681`.
- P2 `work/universe.parquet` (the `mid` reference for P3's E0): `217110887c88d4332d54e3c65e3b0475214d49804d72e42d47154145ef86e211`.
- P2's 36 deterministic `wl/` days: manifest `wl_sha256.txt` (sha256 `026310101a60f8a6d7c1c34f4c38374ca5015d6d7f42d907adaee2c0cd2d6431`), 36 files with 36 distinct hashes.
- P2's `out/mout/`: manifest `mout_sha256.txt` (sha256 `64239ce8d5f1e7e21bdc228f83c49fcab3b4847ff762570b03bd77eb718d913c`), 36 files.
- Cross-check with section 11.1: `wl/2026-09-20.parquet` has sha256 `123487ab288c389efd47effc3ae7c15acac4b8402a4789b9b8937d761df6a9c5`, equal to the determinism check's file.

**Backed-up copy.** The job copied these files to `/data/mal/c1nf/p2/` and checked the copies with `sha256sum -c`:
- `disc.npz`, `conf.npz`, `confirm_primary.json`, `confirm_primary_trades.npz`, `rule.json`, `p2_counts.json` (counts only);
- the manifests `p2_outputs_sha256.txt`, `staged_sha256.txt`, `wl_sha256.txt`, `mout_sha256.txt`, `shared_sha256.txt`;
- `run_p2.sh`, `p2_counts.py`, `common2_p2.diff`.

`/data/mal/c1nf` has no `.nobackup` marker, while `/data/mal/hunt-1008` has one. `/data` is in the nightly restic set (`mal-backup.timer`, about 03:17Z, `--exclude-if-present .nobackup`) [measured 2026-10-10]. No snapshot holds these files yet: they were written at 10:33Z, after the 2026-10-10T03:17Z run, and that run failed (`Fatal: unable to save snapshot`; the target reported `no space left on device`). Until a snapshot that contains `/data/mal/c1nf/p2/` is confirmed, the copy is on local disk only. The manager records that snapshot's id, checked with `restic ls`, in a dated line before 2026-10-16T01:00Z. The working directory `/data/mal/hunt-1008/c1nf-p2` (2.7 GB, including `wl/` and `out/mout/`) is kept for look assembly.

**Outcome-blindness.**
- Neither the job log nor the job result holds a P&L, return, fill or exit value of the rebuild, and the writer printed or opened none. The job log and result hold only counts and hashes.
- Looking for how 8,797 and 419 were defined, the writer saw numbers in `verify/VERIFY.md`. Those are exploration-pool numbers already published in the repo and cited by this file, and no seal covers them.
- No sealed block was opened. Neither was any forward-1002 or forward-1002ev file, walk 2, any October label, or any forward-paper, canary or shadow output.

**What this does not do.**
- It runs no October step. The look patches of section 2.4 belong to the read tool (P4).
- It is not P3 or P4.
- It is not the canary's model pin. #550 trains on VERIFY's arrays, and a model built on P2's arrays needs its own pin and dated line.
- Quant-proof reviewed it on head `f7da961` (2026-10-10). The run matches section 10 P2, with the `common2.py` retarget disclosed above. The count step is count-only. The bands and hashes check against the files.

### Amendment 5 [number TBD at merge: open #548 holds Amendment 3 and open #558 holds Amendment 4] (2026-10-10, DRAFT; the manager fills every TBD after the merges, and it must merge before 2026-10-16T01:00Z): the P3 and P4 pins (October adapter, read tool)

Outcome-blind. To draft it, no row, report or scratch file of forward-1002, forward-1002ev or walk 2 was opened, and no October label, C1-NF selection, fill or P&L was computed or read. Its inputs are the heads of #563 and #561, the MiScusi records of jobs #537 and #541, and those jobs' E0 record files on exploration day 2026-09-20. It records what section 10 P3 and P4 require. It changes no rule, cap, threshold, leg, patch or refusal.

**Status at drafting.** P3 and P4 are not met yet: #563 and #561 are not merged, and neither has quant-proof OK. P2 (Amendment 4, #558) is open too, and P3's `mid` check depends on P2. This amendment takes effect only when three things are true: every TBD below is filled, the manager has checked each blob against merged main, and it merges before 2026-10-16T01:00Z. If that misses, section 10 makes the walk-2 part NOT_DECIDABLE, and section 11.5 spends the look.

#### A. P3: October adapter (section 11.2)

| Item | Value |
| --- | --- |
| PR | #563 `claude/exp025-october-adapter`, head `bad950361580ec2865d58884f6f1222c6642221d` |
| Pinned blob | `tools/exp025_adapter.py` `20ba1c02f01e18e90eef3633a8d6ddd9e8e572a2` (at `bad9503`) |
| Merge commit | **TBD** |
| quant-proof OK on the final head | **TBD** (link) |

**E0 on 2026-09-20: MiScusi job #537.** It ran on mal-research-0 at code `996fa29`. The adapter blob at `996fa29` is `20ba1c02…`, equal to the `bad9503` head's, and the record's own `blobs` entry says the same. Resources: a 16 GB cap and an 11 GB peak. Verdict: **PASS**.
- Rows equal by md5: trades, creates and migrations against `ref/convert.py`, and `tokens` against `ref/build_shared.py`.
- The section 2.4 event-V fixture: ok.
- Idempotence: equal.
- The `mid` check: pass. `p2_rows` 24,319, `later_new_rows` 815, `bad_hours` 0.
- Record: `/data/mal/exp025/e0/p3_e0_2026-09-20.json`, sha256 `f2b9e7ea98c9c9236b7677fde9e82b1b29d1941523f0ac93f646e27143514fdf` [measured 2026-10-10]. Its `view_sha256_file` is `05486f70f53c7ef848b151f40d310ecc16e3ef517ff98ed7d4348250a32effe8`.

**TBD (manager):**
- Every adapter run writes over this record path; the job keeps the previous record under a `.jobNNN` name. Copy the record to a backed-up path and confirm its sha256 there.
- Confirm that #537's `mid` check used the P2 artifacts that Amendment 4 (#558) pins. That was not checked here. If it did not, re-run this E0 after #558 merges.
- If the adapter blob changes before merge, re-run the E0 at the merged blob and replace this entry.

#### B. P4: read tool

| Item | Value |
| --- | --- |
| PR | #561 `claude/exp025-read-tool`, head `93bed0064274159dc2ae2a9d9b05e9c2b9871839` (draft) |
| Pinned blob | `tools/exp025_read.py` `10d7beeb27533ebd1918c432da5a2170947db6f7` (at `93bed00`) |
| Merge commit | **TBD** |
| quant-proof OK on the final head | **TBD** (link) |

**E0 on 2026-09-20: MiScusi job #541.** It ran on mal-research-0 at code `8b953c6`. The read-tool blob at `8b953c6` is `10d7beeb…`, equal to the `93bed00` head's. Resources: a 6 GB cap and a 1.3 GB peak. Verdict: **PASS**.
- Pricing to the lamport (quant-proof R11): the md5 over (mint, decision time, leg, pnl in lamports) is `7d5eb193a13495b84b06ac64f50049c8` for `verify/v2_sim.py` with `verify/v3_report.py`, and the same for the read tool. That day has 34 C1-NF selections (`n_c1nf_day` 34), and the job priced 657 rows. `pool_mismatch_v2` is 0, and `lamport_all_tags` is true.
- Statistics equal to `verify/v3_report.py` on all eight tags: `p` and `b`, each for `END_l055_55` and `END_l055_505`, each with and without rent.
- Record: `/data/mal/exp025/e0/p4_r6/p4_e0.json`, sha256 `143328696e21e99a5e578ee0e3b326e84dad29698a7ddcf894b114c7011befd0` [measured 2026-10-10]. Its `v2_rerun.sha256` is `177af784cdd23d39915187f2be60764b50b43da01f33d8c98008f777acbc8ffa`.

**TBD (manager):**
- Only the read-tool file's blob was compared here. The read tool also needs the adapter, the two pinned patches (`patches/common2_look{1,2}.patch`) and pinned pass A; their blobs at `8b953c6` were not compared with the final merge. Pin them here, or re-run this E0 on merged main.
- Copy the record to a backed-up path.

#### C. Not changed

Nothing else changes: the rule, the cap, the threshold, the patches' content, the event-V mapping, the legs, the statistics, the looks, alpha, the refusals R1–R14 and the section 0 counting start. This draft adds no reader and opens no sealed block.

## Sources

`observe/trade_decode.py`, `observe/trade_store.py` and `tools/test_walk2_event_v.py` (Amendment 1); `/data/mal/hunt-1008/JUDGE-4.md` sections 3.3 and 3.4; `/data/mal/hunt-1008/c1nf-verify/VERIFY.md` and `v/results.json` (copied to `ARTIFACTS/exp025/verify/`); `/data/mal/hunt-1008/c1-cascade-postgrad/` (RULE.md, scripts, ml/); [EXP-024](EXP-024-h5-boostfloor-part1-prereg.md) (template), [EXP-022](EXP-022-cap-pick-part1-prereg.md) sections 9 to 10, [DEC-023](../DEC/DEC-023-h5-family.md), [DEC-021](../DEC/DEC-021-champion-challenger.md), [DEC-016](../DEC/DEC-016-exp012-forward-on-chain-hours.md) Am.2, Am.7, [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md), `docs/HANDOFF.md`; [DEC-026](../DEC/DEC-026-c1nf-live-canary.md) section 8 and Appendix A, `tools/c1nf_executor.py` (`PICK_WINDOW_START_MS`) and #503's `tools/c1nf_shadow.py` at `50eeaa1` (guard unchanged at `9a80398`) (`OUTCOME_START_MS`), MiScusi job #499 (Amendment 3).
