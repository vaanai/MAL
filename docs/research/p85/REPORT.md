# P85 report: FLOOR-OPTION v2 on fresh-0808, one read

Verdict: **NOT_DECIDABLE** (pre-declared: n < 100). The kill line did not fire: mean > 0 under both fail models and the total without the top 3 is > 0 under both. Signs match the in-sample F0 result, at lower size. This is a weak lead for the manager and quant-proof, never a pass, and it changes no running book. In-sample lab dates that many families have read; fresh-0808 abuts explore-0814, where FLOOR was selected, so this is a neighbour, not an independent test. Nothing here says FLOOR makes money.

Pick: library row `reprice-160k-l1` (rule of `lib-c-L1`); shortlist and drops in PREREG.md. Block: `[2026-08-08T12, 2026-08-14T12)` (ledger row "Second backup confirmation block": reserved, no owner, sealed and verified, never read; ledger entry committed before any count or price, PR commit d073e67c). The block is now read (one disclosed read). Manager accepted the claim.

## Seal check (manager's request)
- Wrapper `run160_p85.py` as committed (1f07ed39) keeps `fresh-0802` and `fresh-0828` in the kit's FORBIDDEN words and asserts, before pricing, that every path the loader receives is under `/data/mal/clean-view/fresh-0808`. The first two jobs (#1759 counts, #1760 priced, kit a822fcc1) ran with the broader wrapper (`fresh-08` removed). I re-ran the loader's path resolution alone at a822fcc1 with that same config, no data read: it returns exactly 144 files, all under `clean-view/fresh-0808/w{1,2,3}` (48 each), none containing 0802 or 0828. The kit's discovery only globs inside the block root it is given and refuses files resolving outside it, and the aux builder hard-codes the fresh-0808 view; no listing of fresh-0802 or fresh-0828 was made by any job. No seal contact.
- The reported numbers come from job #1761 (counts then priced, guarded wrapper, kit 1f07ed39). Its `filelist.txt` (counts and priced) has 144 lines each, 0 outside fresh-0808, 0 matching 0802/0828. The `results.json` cells are byte-identical to job #1760's.
- Files: out/counts_COUNTS.md, out/counts.json, out/priced_TABLE.md, out/results.json, out/filelist.txt, out/priced_filelist.txt.

## Counts (before pricing)
21,080 raw triggers, 93 after the time-only overlap rule (upper bound), per UTC day 08-08..08-14: 0, 1, 6, 18, 26, 27, 15. After the kit's exclusions: 59 attempts, 58 fills, 36 block-edge exclusions (37.9% of 95 > the 5% cap; the block edge and the 24 h age warm-up bite a 6-day block). 5 UTC days have trades.

## Priced (deciding leg: 1.9 s, 0.25 SOL stake, rent 0, strand rule total, 160,000 lamports per send, both fail models; kit PumpSwap pre-trade reserves with V map, kit fee legs)
| | flat 15% | pressure (slope 1) |
|---|---|---|
| n (attempts / fills) | 59 / 58 | 59 / 58 |
| mean SOL per trade | +0.017083 (+6.83%) | +0.013043 (+5.22%) |
| total SOL | +1.007925 | +0.769554 |
| total without top 3 | +0.485367 | +0.329274 |
| total without best UTC day | +0.504367 | +0.345846 |
| days positive | 4 of 5 (majority) | 4 of 5 (majority) |
| CI90 lower bound, per trade (1,000 draws, seed 1) | +0.001329 SOL | **-0.002093 SOL** |
| CI90 lower bound, date-cluster (kit "ci_lo") | +0.009296 | +0.004709 |
| top-day share of total | 0.4996 | 0.5506 |

Exits: 48 time, 11 take-profit. Strands 2 (total rule). Worst-in-slot equals the END leg (+0.0171 flat, +0.0130 pressure). Rent-charged leg: flat +0.015438, pressure +0.011668 per trade. Kit flags: KILLED EVENT (n 59 < 100; trade-level CI lower bound <= 0 under pressure; exclusion share 37.9% > 5%; 0 of 4 canonical blocks). The kit's KILL is its in-sample screen verdict, not my kill line.

Capacity (attempts/day x stake x net mean, impact priced inside the mean): 8.43 attempts/day (59 over 7 UTC dates) x 0.25 SOL: about +0.144 SOL/day flat, +0.110 SOL/day pressure. Round-trip impact 1.354% at 0.25 SOL, 5.415% at 1.0 SOL; at 1.0 SOL the kit gives mean +0.0667 flat / +0.0509 pressure per trade, about +0.56 / +0.43 SOL/day, with no depth, concurrency or 24 h-hold capital check (positions are held up to 24 h, so several are open at once).

## Promotion-gate arithmetic (report only; this is not a registered forward read)
- n >= 100: **no** (59 attempts).
- >= 5 UTC days with a majority positive: yes (5 days, 4 positive).
- CI90 lower bound of mean per trade > 0: flat yes (+0.0013), **pressure no (-0.0021)**.
- Total positive without the top 3: yes under both (+0.485 / +0.329).
Not a gate pass: fails on n and on the pressure CI.

## Not measured / caveats
- V-map coverage of the block's pools was not reported separately (the kit applies its default map and does not print a coverage figure); not checked against my own 95% refusal line, which the PREREG promised. This is a gap in the pre-registered counts step.
- The kit's figures are not the bot's: slot arithmetic for the 24 h exit, no md5 decision-equivalence replay here.
- The per-trade mean (flat +0.0171 vs F0 +0.01605; pressure +0.0130 vs +0.01285) is at or above F0's, but F0 rests on 332 trades over 19 days and this is 59 trades on 5 days with about half the total on one day, 11 take-profit exits carrying it, and a pressure CI lower bound below zero. It neither confirms nor refutes F0 at this n. Same sign as fresh-0903 (P7), which was smaller; sign agreement is a point for persistence, not for size.
- FLOOR's registered forward route (records #810/#811) is untouched.
- Cost: fresh-0808 is spent as a clean confirmation block (EXP-028's "next block for a later cohort" is affected); fresh-0802 and fresh-0828 untouched.
