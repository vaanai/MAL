# FLOOR (l1-floor-v2) registration record, DRAFT for clock 2026-10-26T00:00:00Z at frozen sha 5e12f8f597571d6d5191a7ba38125c67ad723b7d (DEC-031 §1.2, Am.2, Am.3, Am.4 item 2, Am.6)

Nothing is registered. No book has cleared the promotion gate; every L1 figure is in-sample and nothing here says the book makes money. Pack: `/data/mal/hunt-1008/push-1016/hunt24/floor-1026/` (INDEX.md). Basis: QP-RULING-1021-floor.md (Q1 label, C1-C10), Am.6 (smoke-then-clock, covers this book: registration after 90ab73dc).

Label, verbatim (QP Q1): "lib-c-L1 FLOOR-OPTION v2. Screen: NOT_DECIDABLE (block-edge exclusion 23.2% > 5%, decision-time only; job #1301). In-sample only, 303 of 327 on explore-0814 (mined block: G4 13,600 cells + v2 8-cell grid); September 23 trades; lineage G4 (lib-b-C1/C2); NOT bank-eligible; arena only forever (Am.3 item 4); not a screen survivor; 0 out-of-sample trades."

## L-items
| Item | What | Status | Evidence / FILL |
| --- | --- | --- | --- |
| L1 | frozen sha, rule, deciding cell 1.9 s at 0.25 SOL, rent 0 (host `stack_config`), uncapped, read rule, §1.4 pins (arena_feed 7a9d424b, arena_paper 9d2fdb04, arena_score a508ed79), Am.2 config pin, V-band + WSOL check (`tools/arena_rule_l1.py on_row`: quote_mint first, then V band; FILL path:line@5e12f8f) | DONE in this record, FILL line numbers | |
| L2 | P0 (Am.3 item 1 / Am.4 item 2) and H pin | FILL | fixed-first entry id FILL; tool PR #810 merge + sha256 FILL; A1/A2 job ids, days, counts, output sha256 FILL; H = min(90,max(14,ceil(150/att_lo rate))) FILL; notebook copy of H and this commit must match (Am.7 covers HV4 only: FILL the FLOOR H-pin amendment PR) |
| L3 | unit tests green at 5e12f8f | FILL | job id, pass/skip counts |
| L4 | md5 equivalence | DONE | C4(iii) PASS: n_K0r6QAxV4sGcTg, n_Nubu8k52JnZWoA, n_lDzn896HV-BgYA (slice A 5 entries md5 792269fc, slice B 343 entries md5 8130f63b, trim counters 0, peak RSS 2.88 GB); FILL: no port beyond #796 |
| L5 | live smoke >= 2 h at 5e12f8f (no LLM) | FILL | blocking-list notebook id (before smoke); S1/S2/S3 job ids; (a)-(e) counts; cap 8000 MB, slope x 336 h and x H |
| L6 | per-hour health tool pinned: arena_hour_health 227f92be; dry run on >= 2 closed hours | FILL | output sha256 |
| L7 | thresholds (unit, denominator, label); X rule P99 clean hours, X = max(A,120), min_samples 12, feed checks 900/0/0/0 | FILL | final JSON byte-copy + THR_SHA256 |
| L8 | 300 s sampler (own samples file) + daily FINAL table | FILL | D1/D3 job ids |
| L9 | registration line + H notebooked before the clock | FILL | `--register` output, entry id |

## Other pins and conditions
Read tools (all reads via `arena_am6_read` only): arena_hour_health 227f92be, arena_am6_cut c9b5cf1d, arena_am6_read ef6e4555, arena_am6_exclusions dfcc5cd6, arena_score a508ed79. One process per frozen_sha; no planned restart (a restart books open positions engine_restart; FILL job time limit vs H). QP C5 report-only FIFO K=4 leg; C6 R7 legs at the read; C7 seals (age >= 24 h implies > 360 s; path:line FILL); C9 kill-review written confirmation FILL; Q2 reconciliation status FILL. N + 1 from the clock; confirmation needs >= 100 further attempts on >= 5 further dates, about 35 + 35 days at 3 a day.
