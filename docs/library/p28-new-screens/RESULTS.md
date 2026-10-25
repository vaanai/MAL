# P28 counts-only results

2026-10-25. MiScusi jobs #1602 (explore-0814, 2026-08-24T12 to 2026-08-28T12) and #1603 (fresh-0903, first 96 hours), code commit bd02ebde, cells and bars as in DECLARE.md (sha256 beside it, written before any count). Counts only: no price, outcome or P&L was computed. Both blocks are exploration pool per docs/HOLDOUT_LEDGER.md. Raw JSON: `p28_counts_explore-0814.json`, `p28_counts_fresh-0903.json`.

Universe: WSOL PumpSwap rows with V in [17.5e9, 17.7e9]. About 26.9M (explore) and 25.5M (fresh) in-universe buys over 96 h. Each block has 5 UTC dates, the first and last partial (2 h age gate and block edges).

| Cell | explore-0814 triggers (per UTC date) | fresh-0903 triggers (per UTC date) | one-way impact <= 1.5% share at 0.25 / 0.5 / 1.0 SOL (explore; fresh) | Qeff p10 / p50 / p90 SOL (explore; fresh) | Bar (>= 10/day and >= 40% at 0.25) |
| --- | --- | --- | --- | --- | --- |
| pre_r1 (PRESET) | 15 (3 dates: 1, 4, 10) | 0 | 1.0 / 1.0 / 1.0; n/a | 256 / 338 / 417; n/a | below 10/day |
| cs_k5 | 10,331 (1437, 2672, 2721, 2319, 1182) | 3,894 (455, 912, 960, 1112, 455) | 1.0 / 0.97 / 0.89; 1.0 / 0.98 / 0.93 | 62 / 240 / 3,737; 81 / 246 / 3,691 | clears |
| cs_k8 | 4,239 (591, 1091, 1138, 965, 454) | 1,613 (188, 380, 359, 491, 195) | 1.0 / 0.98 / 0.94; 1.0 / 0.99 / 0.96 | 84 / 316 / 3,877; 106 / 325 / 5,956 | clears |
| cs_k12 | 1,837 (262, 478, 519, 401, 177) | 733 (87, 181, 159, 227, 79) | 1.0 / 1.0 / 0.97; 1.0 / 1.0 / 0.98 | 119 / 374 / 3,880; 130 / 348 / 8,107 | clears |
| ss_r3 | 5,942 (594, 1589, 1649, 1452, 658) | 3,736 (498, 920, 894, 1022, 402) | 1.0 / 0.98 / 0.91; 1.0 / 0.78 / 0.73 | 69 / 218 / 1,132; 22 / 179 / 1,072 | clears |
| ss_r5 | 2,581 (210, 718, 719, 659, 275) | 1,516 (196, 374, 337, 424, 185) | 1.0 / 0.98 / 0.93; 1.0 / 0.84 / 0.80 | 77 / 259 / 1,600; 23 / 215 / 781 | clears |

Per-cell notes.
- PRESET: base rate of in-universe buys whose lamports are an exact multiple of 1e6 is 0.088% (explore) and 0.058% (fresh), at or below the 0.1% chance level, so the KILL rule (<= 0.2%) fires: PumpSwap buys on the tape carry no app-preset round-amount signature (the pump app sends exact-out with slippage). Exact 0.1 SOL multiples are 0.009% / 0.006%. Dead on structure; no pricing.
- CALL-SYNC and SIZE-SKEW: counts are far above the bar, i.e. the cells are NOT selective: hundreds to thousands of triggers a day, in deep pools (median Qeff 180-370 SOL, so impact is not the constraint; the 2.4% cost floor is). Triggers are not de-duplicated across the per-hour cooldown boundary (cooldown is applied per hour), so the totals are a mild upper bound. The cells need a second selector before any price, otherwise the screen prices a near-universe.
- SIZE-SKEW veto mirror (ratio <= 1/3): 3.2k-7.6k flags per UTC date (explore), 2.7k-6.5k (fresh), about 4x the long-side counts, so the ratio carries a clear two-sided split; whether it carries information is a pricing question, not answered here.
- GRAD-GLUT: about 62-140 (explore) and 69-155 (fresh) newly seen in-band pools per hour (p10-p90), median 97 and 96. Hours at >= 2x or <= 0.5x of the trailing 24 h median: 0 and 1 of 48 evaluated (explore), 0 and 0 of 48 (fresh). That is under the declared 5% bar: the supply-regime signal does not vary enough to be a sleeve switch. KILL (counts).
- Caveats: first-seen is within the block, so a "new" pool may be an older pool first printing in the block (first 24 h of the series excluded); `trader` is a crc32; the V map is static per pool; the 96 h windows are two blocks, ten dates in total, not a sample of October. Nothing is out of sample and nothing says a rule makes money.

## Seal disclosure

Read: explore-0814 and fresh-0903 trade files through `/data/mal/clean-view` (exploration pool). Only file names (no contents) of `/data/mal/arena/tape` were listed once, and `tools/arena_tape_schema.md` was read. Not opened or listed: `/data/mal/meta-capture` (coordinator seal notice received mid-task; the top-level `ls /data/mal` of the task showed no entry for it in the first 40 names and nothing inside it was listed), forward-1016, HV4/Q50/HV6/DBC outputs, any October hour. No result here is built on arena-tape or forward-1016 contents.
