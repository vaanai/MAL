# Exploration: entry model B3 re-run on the deduplicated pool (2026-10-01)

**Exploration, not a test.** This re-runs lane B3 (`ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md`) unchanged, on the same 9 exploration-pool days, after exact-duplicate rows were removed (`ARTIFACTS/lab/dedupe-exploration-pool-2026-10-01.md`). The data, settings, features, `--max-workers 3` (same chunk boundaries) and the pre-stated screen are the same as on 09-28. **Only the duplicate removal differs.** It answers one question: was the 09-28 screen result an artifact of duplicated rows? It adds **no** out-of-sample evidence. Winner's curse still applies: best of 6 cells in this lane, after at least three earlier passes over the same pool (#146, #152, #156).

**This is the unablated B3 model.** Its features include `same_slot_buys` and `nearby_buy_sol`, which are computed at the order's slot+1 landing state, after the decision time. EXP-011 §10 retired that version for lookahead (see the `.json` `feature_names`; `nearby_buy_sol` is a top-5 feature in 9/9 folds for `s2_clf`). So this run checks only whether duplicates caused the 09-28 result. **It says nothing about the ablated model** that any pre-registration would test. That model's clean-pool numbers are not measured here.

Run: MiScusi job #4 `j_YqPs2Ks67RYcFg`, mal-research-0, code `eba281b`, input `/data/mal/clean-view/<block>` (hardlinks of the deduplicated files, every file sha256-checked against `VIEW.sha256` before the run), wall time 1195s. Full output: [`exploration-entry-model-b3-clean-2026-10-01.raw.md`](exploration-entry-model-b3-clean-2026-10-01.raw.md) and `.json`. The raw file is unedited tool output: its frontmatter, its title and its last-line pointer to `exploration-entry-model-b3-2026-09-28.json` are template text. The grid for this run is `exploration-entry-model-b3-clean-2026-10-01.json`.

## Duplicate-removal disclosure

The fast pool lost 2,467,409 trade rows, 3,100 creates and 97 migrations, all in the resumed hours 2026-09-19T16, T17 and T20. Oracle in-sample lost 0 rows. The Oracle live tape lost 4,730 trade rows (5–326 per hour). **Rows scored are identical to 09-28** (`tpsl_tp50_sl30`: A=3029, C=3342, B=2300; `trail_30_act20`: A=3029, C=3341, B=2296), so in **this lane** the duplicated migrations never produced extra scored entries. That does not clear other studies: the 972-cell grid and B2 may have scored duplicates differently. The duplicates did change per-trade features and fills, and so which trades were selected: the lead cell's top-10% by source moved from 302/331/233 (fast / Oracle in-sample / Oracle live) to 302/329/235.

## Pre-stated screen: 09-28 vs clean (same rule, not moved)

A cell is a CANDIDATE only if, under BOTH fail models: pooled top-10% mean net % > 0, pooled ex-top-3 SOL > 0, and more than 4 of the 9 days positive.

| Exit / setting | n | Flat mean % (CI lo), 09-28 → clean | Flat ex-top-3 SOL | Flat days+ | Press mean % (CI lo), 09-28 → clean | Press ex-top-3 SOL | Press days+ | Screen |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- |
| `tpsl_tp50_sl30` / s1_reg | 866 | +4.49% (+2.32%) → +4.23% (+1.93%) | +17.5248 → +16.6832 | 7/9 → 7/9 | +2.88% (+1.35%) → +2.95% (+1.36%) | +11.0628 → +11.3581 | 6/9 → 7/9 | CANDIDATE → CANDIDATE |
| `tpsl_tp50_sl30` / s2_clf | 866 | +6.73% (+4.35%) → +6.05% (+3.52%) | +27.1253 → +24.1427 | 9/9 → 8/9 | +3.84% (+2.35%) → +3.72% (+2.26%) | +15.2739 → +14.7437 | 9/9 → 8/9 | CANDIDATE → CANDIDATE |
| `tpsl_tp50_sl30` / s3_reg_winsor | 866 | +4.52% (+2.33%) → +4.23% (+1.85%) | +17.9439 → +16.3776 | 7/9 → 7/9 | +2.89% (+1.40%) → +2.68% (+1.03%) | +11.1611 → +10.2386 | 6/9 → 6/9 | CANDIDATE → CANDIDATE |
| `trail_30_act20` / s1_reg | 866 | −1.86% (−6.18%) → −2.76% (−5.97%) | −25.1762 → −19.0454 | 3/9 → 2/9 | −2.26% (−4.43%) → −2.19% (−4.41%) | −17.6129 → −14.4253 | 3/9 → 1/9 | no → no |
| `trail_30_act20` / s2_clf | 866 | +3.44% (−0.18%) → +3.82% (−0.02%) | +5.5562 → +6.7981 | 6/9 → 6/9 | +2.06% (−0.46%) → +2.77% (−0.01%) | +1.4829 → +3.7395 | 6/9 → 6/9 | CANDIDATE → CANDIDATE |
| `trail_30_act20` / s3_reg_winsor | 866 | −0.36% (−3.04%) → −0.78% (−3.42%) | −7.0867 → −8.9136 | 2/9 → 5/9 | −0.62% (−2.31%) → −0.83% (−2.51%) | −6.1749 → −7.0951 | 2/9 → 5/9 | no → no |

**Same four cells pass on clean data.** The lead cell `tpsl_tp50_sl30`/`s2_clf` still passes the screen, but it weakened on every measure:
- flat mean −0.68 pp (+6.73% → +6.05%)
- flat CI lower bound −0.83 pp (+4.35% → +3.52%)
- flat ex-top-3 SOL −2.98 (+27.1253 → +24.1427)
- one day turned negative: **2026-09-20**, top-10% −0.13% flat and −0.41% pressure. That is a fast-pool day, and every planned holdout block is fast/backfill data.
- its fast-source top-10% fell from +4.23% to +3.73% flat (clean: +3.73% flat / +2.38% pressure, n=302)

`trail_30_act20`/`s2_clf` passes the screen only because the screen ignores the CI. Its CI lower bounds are −0.02% (flat) and −0.01% (pressure), so it would **fail the promotion gate's CI condition on both legs**.

**Day counts are fragile.** Removing duplicates from 3 hours of one pool moved `trail_30_act20`/`s3_reg_winsor` from 2/9 to 5/9 days, `trail_30_act20`/`s1_reg` pressure from 3/9 to 1/9, and the lead cell from 9/9 to 8/9. With about 100 trades per day, "8/9 days" is not a strength.

## What this does and does not mean

- The 09-28 screen result was **not** a duplicate-row artifact.
- This is still the exploration pool. It is not evidence for the promotion gate. The CI lower bounds above are in-pool leave-one-day-out numbers, not out-of-sample.
- Next step (EXP-012, separate PR): re-run EXP-011's freeze recipe unchanged (the ablated 18 features, the same parameters and threshold rule) on the deduplicated pool. Pre-state that its nested leave-one-day-out result must pass this same screen before the reserved block `[2026-09-03T12, 2026-09-09T12)` is read. EXP-011's frozen model is retired (its §8) and is not reused. `quant-proof` reviewed this note (2026-10-01).
