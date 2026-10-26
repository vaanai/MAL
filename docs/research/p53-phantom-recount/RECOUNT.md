# P53 phantom-row recount (QP-RULING-1026-phantom-rows, item 4)

Written 2026-10-26 ~22:55Z (`date -u` 22:55:03Z). Counts only. No price, fill, exit, outcome or return was computed or opened. Nothing here says a book makes money. No book has cleared the promotion gate.

## What was done

- **Filter.** The HV6 phantom filter `tools/hv6_phantom.py` (merged in #980, main 8cd8fe54; sha256 `0b684536bf8397d3983766dedce4b541e75ac854c95bb03241b3abc4543ea7d3`) is **imported**, not copied (`p53_mark.py`). One `PhantomFilter` sees every row of 136 closed arena-tape hours, 2026-10-19T08 to 2026-10-24T23, in file order, as `hv6_runner.apply` does. The tape starts at 2026-10-19T08:05Z, so the repeat-signature history has no gap at the start.
- **Marked lines.** Job #1686 (`run_p53.sh mark` at 4762bb19) wrote the dropped line indices to `/data/mal/hunt-1008/push-1016/p53-phantom-recount/p53_drops.json` (sha256 `d8b491751a002f26cd61a3ee8339d9a799d15b4d1e51c279e7dc434e3d75a43d`). Drops: `sig_ones` 4,411, `signer` 112,472, `repeat_slot` 5, total 116,888. Repeat signatures are mostly caught first as `signer` or `sig_ones` (the kind order is fixed by the filter).
- **Re-count.** Each counter of record ran **unchanged**, sha256-pinned, through `p53_run.py`. The wrapper wraps only `subprocess.Popen(["zstd", "-dc", ...])` and skips the marked lines. Each counter ran twice:
  - `--off` (nothing skipped). The output must equal the original output byte for byte.
  - `--drops` (the marked lines skipped). A skip log checks that every marked line of every file read was reached and skipped.
- **Equivalence.** All `--off` outputs equal the originals (sha256 below). All skip logs show 0 mismatches.

Phantom-class share of trade rows by day (job #1686):

| day | pump.fun trade rows | dropped | share | PumpSwap trade rows | dropped | share |
|---|---|---|---|---|---|---|
| 10-19 (from T08) | 2,131,028 | 6,150 | 0.2886% | 13,043,498 | 564 | 0.00432% |
| 10-20 | 3,227,188 | 8,842 | 0.2740% | 16,984,702 | 1,248 | 0.00735% |
| 10-21 | 3,098,235 | 17,844 | 0.5759% | 18,395,745 | 3,290 | 0.01788% |
| 10-22 | 3,053,857 | 21,351 | 0.6991% | 17,302,120 | 1,499 | 0.00866% |
| 10-23 | 3,506,518 | 27,701 | 0.7900% | 18,908,850 | 2,484 | 0.01314% |
| 10-24 | 3,240,527 | 24,457 | 0.7547% | 16,713,648 | 1,455 | 0.00871% |

Three create rows with an all-`1` signature were also dropped: 2 on 10-20 and 1 on 10-21.

## 1. P44 `hunt23-w3-pump-sink-1` ("kill not fired")

Counter: `inputs/p44_count.py` (copy of 72ee5874 on `claude/p44-revive-sweep`, sha256 `56d5a218...a7a8`), the same 96 files 10-21T00..10-24T23, the same signature list. Job #1687. The `--off` output sha256 is `a93b4068...c81c`, equal to the job #1656 output.

| day | share (child-leg SOL / PUMP-pool buy SOL), original -> filtered | PUMP buys with child leg | child-leg buys >= 0.5 SOL | SOL in child-leg buys | all PUMP-pool buy SOL |
|---|---|---|---|---|---|
| 10-21 | 0.9361 -> **0.1883** | 175 -> 169 | 6 -> **0** | 151.049112010 -> **2.391008571** | 161.356905481 -> 12.698802042 |
| 10-22 | 0.6588 -> **0.1410** | 297 -> 203 | 94 -> **0** | 254.807138949 -> **1.332296287** | 386.803067806 -> 9.451284557 |
| 10-23 | 0.7924 -> **0.1813** | 319 -> 89 | 230 -> **0** | 726.116623521 -> **1.604688192** | 916.361486216 -> 8.853450269 |
| 10-24 | 0.8142 -> **0.2056** | 235 -> 48 | 187 -> **0** | 579.663089358 -> **0.622234995** | 711.966860970 -> 3.025898368 |

- **Verdict: the kill rule still does not fire** on the daily share (0.1410 to 0.2056, all above 5%). It is unchanged as stated in P44: "daily, not hourly". The hourly share, which is what the rule says, was not counted, by P44 or here.
- **The P44 note does not survive.** P44 said: "A regime shift from 10-22: almost all of these buys are >= 1.0 SOL (94 of 94 on 10-22)." After the filter, there are 0 child-leg PUMP buys >= 0.25 SOL on every day. The SOL in child-leg buys falls from 151.05 / 254.81 / 726.12 / 579.66 to 2.39 / 1.33 / 1.60 / 0.62 SOL a day. The large buys were phantom-class rows.
- Other changes: `pump_child_curve_buys` 43,548 / 43,502 / 34,323 / 35,425 -> 40,217 / 38,110 / 29,318 / 30,649; `create|wsol|mayhem=0` on 10-21 15,028 -> 15,027.
- **Unchanged:** every other P44 count. That covers G1, Gz and G2 pools, BOOSTed pools, every H5-structure trigger count, the agent-signature counts and the gap counts. So none of P44's other met or not-met verdicts move.

## 2. Library rows since 10-19 that used arena-tape bonding rows

Grep of `docs/library/ideas.jsonl` at 0be9f676: 130 rows have `source_date` >= 2026-10-19. Of these, 32 mention "arena" in some field. Two also mention bonding, pumpfun, pump.fun or curve in the evidence fields:
- `hunt23-w3-qsink-1`;
- `hunt23-w3-burn-1`.

The `hunt23-w3-pump-sink-1` row itself does not mention "arena"; it is covered by section 1. In the other 30 rows, "arena" means the arena tools (`arena_paper`, `arena_screen`) run on explore-0814 or the September blocks, the arena universe definition, or a PumpSwap V-band count. None counts arena-tape bonding rows, by the row text. This was checked by reading the rows, not by opening their outputs.

### 2a. `hunt23-w3-burn-1`: CENSUS-PLAN-2 (job #1293), status explored-no-survivor

Counter `w3-burn/scripts/plan2_count.py` (sha256 `180a55df...0cde`), files 10-19T08..T22 and 10-20T00..T23. Job #1693. The `--off` output sha256 is `25b436db...2179`, equal to the original `out2/plan2_counts.json`.

| field | original | filtered |
|---|---|---|
| Q (qualifying) | 25 | 25 |
| hours_q | 12 | 12 |
| control_obs | 15431 | 15429 |
| control_exp | 15067.725998378322 | 15064.423985334839 |
| control_ratio (bar 1.5) | 1.0241094111786195 | 1.0242011254476158 |
| top wallet / top pool | 6 / 11 | 6 / 11 |
| claims_wsol / universe / post_claim / buys_ge05 (totals) | 29293 / 3657218 / 205473 / 143803 | 29240 / 3657095 / 205405 / 143798 |
| verdict | FAIL | **FAIL (unchanged)** |

### 2b. `hunt23-w3-qsink-1`: census, K0 and the plan-2 day counts, status untested

Counters, all sha256-pinned and run unchanged:
- `qsink_count.py` (`e16582ca...7f77`): the 10-19 census, 14 hours 10-19T08..T21;
- `qsink_count_day.py` (`c291cf2b...18cc`): `--k0` and `--day` for 10-20..10-24;
- `qsink_netflow_day_v2.py` (`6bea86e9...2e6a`): `--day` for 10-20..10-24.

Jobs #1694 (K0 and census), #1695 (day), #1700 (netflow2). Every `--off` output (12 files) equals the original file in `w3-qsink/out/` (sha256 in the job logs).

- **Census K0-R (10-19): PROVISIONAL_PASS -> PROVISIONAL_PASS (unchanged).** C1r 924.0, C1t 1117.96, C2 0.071, n_children 648, all unchanged. Child-buy signatures with a certain-SOL parent buy: 14,451 (4,587.965229950 SOL) -> 14,442 (4,532.797569074 SOL). MultiHop signatures / legs: 54,266 / 91,813 -> 54,260 / 91,801. Child bond buys: 15,962 -> 15,890.
- **K0 on 10-20..10-24: NOT_DECIDABLE -> NOT_DECIDABLE (unchanged).** Full days are still only 10-23 and 10-24, with 6 bad hours, C1 1551.5, C2 0.1878. Hour quality comes from gap and hb rows, which the filter does not touch. Only `create_WSOL` changes: 10-20 22,899 -> 22,897 and 10-21 22,406 -> 22,405.
- **Plan-2 P0 (LAUNCH cells, deciding): unchanged.**
  - L1 daily attempts are 20 / 33 / 11 / 14 / 19 (median 19) before and after.
  - L4 daily attempts are 15 / 16 / 8 / 13 / 17 (median 15) before and after.
  - Under COUNTS-PLAN-2 §5 (median >= 20 and no day < 10), both are P0 FAIL on the letter, before and after. K0 is NOT_DECIDABLE in any case.
  - This P0 reading is mine, made from the day files; the row says the results were "not in". It was not judged.
- **LOCK cells (approximate, never deciding) move down.** Daily attempts, 10-20..10-24:

| cell | original | median | filtered | median |
|---|---|---|---|---|
| K1a | 10 / 20 / 3 / 11 / 13 | 11 | 10 / 8 / 3 / 8 / 12 | 8 |
| K1b | 8 / 11 / 3 / 10 / 9 | 9 | 8 / 7 / 3 / 7 / 8 | 7 |
| K3a | 4 / 18 / 0 / 7 / 8 | 7 | 3 / 5 / 0 / 4 / 8 | 4 |
| K3b | 3 / 8 / 0 / 7 / 6 | 6 | 2 / 3 / 0 / 4 / 6 | 3 |

- **Gross parent buy SOL (certain), per day 10-20..10-24, original -> filtered:**

| day | original (SOL) | filtered (SOL) |
|---|---|---|
| 10-20 | 28,282.530269687 | 26,702.701065093 |
| 10-21 | 73,739.608974823 | **34,866.998116809** |
| 10-22 | 11,521.681500814 | 11,082.269842537 |
| 10-23 | 20,476.990307927 | 18,492.879374886 |
| 10-24 | 15,506.021283489 | 15,074.486755609 |

  On 10-21, more than half of the gross parent buying was phantom-class.
- **Net flow v2 (COUNTS-PLAN-2a/2b; report-only, "no verdict of their own", "change no COUNTS-PLAN-2 verdict"). The sign changes.** Job #1700. Net parent SOL, before -> after:

| day | net, all parents | out/in, all parents | net, universe U | out/in, U |
|---|---|---|---|---|
| 10-20 | +1548.6742 -> **-43.8238** | 0.9453 -> 1.0016 | -193.4867 -> -193.4867 | 1.0754 -> 1.0754 |
| 10-21 | +38161.6355 -> **-835.3403** | 0.4836 -> 1.0239 | +11225.0215 -> **-496.4807** | 0.341 -> 1.0935 |
| 10-22 | +17.0244 -> **-425.5542** | 0.9985 -> 1.0383 | -128.2142 -> -128.2142 | 1.0793 -> 1.0793 |
| 10-23 | +2073.4029 -> +84.5943 | 0.8989 -> 0.9954 | -227.3417 -> -229.9524 | 1.1771 -> 1.1795 |
| 10-24 | +547.7425 -> +115.5745 | 0.9647 -> 0.9923 | -115.3582 -> -120.7661 | 1.0905 -> 1.0952 |

  - Out SOL barely moves. The positive net inflow was phantom-class buy rows.
  - After the filter, parent flow is about flat over all parents (out/in 0.9923 to 1.0383), and net negative in U on every day.
  - In-window U split (P_neg_given_neg / P_pos_given_pos / lag1_pearson): it moves only on 10-21 (0.68 / 0.3 / 0.0258 -> 0.6863 / 0.2821 / 0.168), and slightly on 10-23 and 10-24.

## Changed verdicts

**None.** The verdicts are:
- pump-sink "kill not fired": unchanged;
- W3-B plan 2: FAIL, unchanged;
- QSINK census: PROVISIONAL_PASS, unchanged;
- QSINK K0: NOT_DECIDABLE, unchanged;
- QSINK LAUNCH P0: FAIL on the letter, unchanged.

Changed descriptions:
- The P44 pump-sink "regime shift to >= 1 SOL buys from 10-22" is phantom. After the filter there are 0 child-leg buys >= 0.25 SOL on any day, and about 0.6 to 2.4 SOL a day of child-leg buying.
- The QSINK gross parent buy SOL on 10-21 falls from 73,739.6 to 34,867.0.
- The QSINK LOCK cells' attempt counts fall.
- The QSINK net parent flow (plan 2a, report-only) was positive on all 5 days. After the filter it is about flat or negative: negative on 10-20, 10-21 and 10-22, and negative in U on every day.

## Not measured

- The hourly pump-sink share, which is what the kill rule says.
- Phantom rows from signers not on the list (bounded only by the QP ruling's 0 of 200 sample).
- The first sighting of a repeated signature, which the stream filter cannot retract (same as HV6).
- Any arena-tape hour at or after 2026-10-25T00Z.
- Any HV4, Q50 or HV6 output.
- Library rows before 10-19.

## Files

- `p53_mark.py`, `p53_run.py`, `run_p53.sh`, `test_p53.py` (6 tests);
- `inputs/` (pinned P44 counter and signature list).
- Outputs: `/data/mal/hunt-1008/push-1016/p53-phantom-recount/<step>/` (`off-*`, `on-*`, stdout, stderr, skip logs).

Walk-2-hour information (DEC-030 §2 R1).
