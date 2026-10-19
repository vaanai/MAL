# HV4 stub memory growth, measured 2026-10-19 (tools/hv4_memreplay.py output)

Paper only. The chain layer is mocked (no RPC call), the model is StubModel, the data is the closed arena tape hours 2026-10-19T08 to T16 (T09 to T11 for section 1). Sections 1 to 3 are written by the harness, not by hand. Nothing here says any book made money.

Section 1 is the tracemalloc run (3 closed hours, a snapshot every 15 simulated minutes) BEFORE the fix. Its mock chain refused every card on a slot mismatch (fixed afterwards in the harness), so card-side structures are not in it; the engine and state sites are. RSS in that run is inflated by tracemalloc itself.
Sections 2 and 3 are 9 h RSS curves without tracemalloc: bounds off (the 2026-10-19 behaviour, `RunnerConfig.bound_memory=False`) and on.
Section 4 puts the hourly points of 2, 3 and a what-if side by side. The what-if trims the engine's print history of every pool without a position to the last 20,000 slots from the harness; it is NOT in the runner and needs a decision on `tools/arena_paper.py` (pinned).
Section 5 is the decision-equivalence check on the real 9 h tape.

## 1. tracemalloc, before the fix

| sim (UTC) | RSS MB | tracemalloc MB | rows applied | state pools | engine pools | quote rows | prefetch pools |
|---|---|---|---|---|---|---|---|
| 10-19T09:15 | 1153 | 218 | 198706 | 2499 | 2430 | 68829 | 0 |
| 10-19T09:30 | 2326 | 380 | 378990 | 3717 | 3595 | 130095 | 0 |
| 10-19T09:45 | 3255 | 528 | 578736 | 5314 | 5134 | 208739 | 0 |
| 10-19T10:00 | 4218 | 703 | 809058 | 6785 | 6572 | 294277 | 0 |
| 10-19T10:15 | 5206 | 905 | 1040114 | 7948 | 7673 | 384880 | 0 |
| 10-19T10:30 | 6372 | 1081 | 1265805 | 8905 | 8576 | 472388 | 0 |
| 10-19T10:45 | 7441 | 1287 | 1515916 | 9955 | 9552 | 580649 | 0 |
| 10-19T11:00 | 8115 | 1406 | 1713364 | 10977 | 10523 | 660024 | 0 |
| 10-19T11:15 | 9017 | 1584 | 1922726 | 11818 | 11298 | 748325 | 140 |
| 10-19T11:30 | 9620 | 1698 | 2099930 | 12766 | 12147 | 797299 | 165 |
| 10-19T11:45 | 10499 | 1870 | 2296227 | 13853 | 13148 | 856988 | 185 |
| 10-19T12:00 | 11673 | 1984 | 2483476 | 14961 | 14187 | 912429 | 185 |

## Top 15 growing allocation sites, 10-19T09:15 to 10-19T12:00

| site (file:line) | size delta MB | count delta |
|---|---|---|
| decoder.py:353 | 615.5 | 9998651 |
| arena_paper.py:886 | 181.5 | 2268757 |
| arena_paper.py:727 | 175.7 | 105813 |
| arena_paper.py:926 | 163.4 | 6806271 |
| arena_paper.py:923 | 163.1 | 2264940 |
| arena_state.py:438 | 127.6 | 1601849 |
| arena_paper.py:723 | 125.6 | 5138 |
| arena_paper.py:927 | 54.5 | 2268757 |
| arena_paper.py:869 | 33.9 | 1411781 |
| arena_state.py:442 | 28.5 | 481433 |
| arena_state.py:466 | 20.9 | 303227 |
| hv4_runner.py:567 | 20.2 | 843601 |
| arena_state.py:129 | 9.5 | 24921 |
| hv4_runner.py:358 | 7.3 | 2335 |
| hv4_runner.py:357 | 7.3 | 2335 |

## Largest allocation sites at 10-19T12:00

| site (file:line) | MB |
|---|---|
| decoder.py:353 | 692.8 |
| arena_paper.py:886 | 197.3 |
| arena_paper.py:727 | 191.1 |
| arena_paper.py:926 | 177.5 |
| arena_paper.py:923 | 177.3 |
| arena_state.py:438 | 143.4 |
| arena_paper.py:723 | 136.5 |
| arena_paper.py:927 | 59.2 |
| arena_paper.py:869 | 36.8 |
| arena_state.py:442 | 32.2 |
| arena_state.py:466 | 22.8 |
| hv4_runner.py:567 | 21.9 |
| arena_state.py:129 | 11.4 |
| hv4_runner.py:358 | 7.9 |
| hv4_runner.py:357 | 7.9 |

Reading the sites: `arena_paper.py:723/727/869/886/923/926/927` are the engine's `PoolHist` (one entry per print of every pool: the order-key tuple, the dedupe tuple, nine parallel lists, the float copies); `decoder.py:353` is the JSON decoder, and most of its bytes are the 88-character signature strings those tuples keep alive; `arena_state.py:438/442/466` are the state's own 60-minute event deque, per-trader net dict and candles; `hv4_runner.py:357/358` is `QuoteHistory`.

## 2. 9 h, bounds OFF

| sim (UTC) | RSS MB | tracemalloc MB | rows applied | state pools | engine pools | engine hist rows | quote rows | prefetch pools |
|---|---|---|---|---|---|---|---|---|
| 10-19T08:15 | 245 |  | 127342 | 1914 | 1887 | 126014 | 60743 | 0 |
| 10-19T08:30 | 433 |  | 333441 | 3951 | 3884 | 330963 | 135276 | 0 |
| 10-19T08:45 | 673 |  | 566945 | 5304 | 5163 | 563516 | 228807 | 0 |
| 10-19T09:00 | 836 |  | 770974 | 6616 | 6445 | 766834 | 302704 | 0 |
| 10-19T09:15 | 1020 |  | 969849 | 7709 | 7471 | 964090 | 371582 | 0 |
| 10-19T09:30 | 1192 |  | 1150133 | 8621 | 8330 | 1143655 | 432848 | 0 |
| 10-19T09:45 | 1344 |  | 1349879 | 9967 | 9618 | 1341578 | 511492 | 0 |
| 10-19T10:00 | 1517 |  | 1580201 | 11279 | 10897 | 1570751 | 597030 | 0 |
| 10-19T10:15 | 1728 |  | 1811257 | 12324 | 11880 | 1795711 | 687633 | 124 |
| 10-19T10:30 | 1922 |  | 2036948 | 13188 | 12690 | 2020646 | 726543 | 155 |
| 10-19T10:45 | 2138 |  | 2287059 | 14154 | 13582 | 2269973 | 796743 | 174 |
| 10-19T11:00 | 2284 |  | 2484507 | 15115 | 14492 | 2466661 | 825962 | 177 |
| 10-19T11:15 | 2483 |  | 2693869 | 15899 | 15210 | 2675391 | 861892 | 179 |
| 10-19T11:30 | 2607 |  | 2871073 | 16798 | 16010 | 2851565 | 841742 | 179 |
| 10-19T11:45 | 2769 |  | 3067370 | 17841 | 16967 | 3046397 | 882005 | 189 |
| 10-19T12:00 | 2911 |  | 3254619 | 18910 | 17967 | 3232847 | 920128 | 186 |
| 10-19T12:15 | 3101 |  | 3448358 | 19685 | 18669 | 3425949 | 968088 | 179 |
| 10-19T12:30 | 3277 |  | 3648727 | 20513 | 19415 | 3625691 | 1027685 | 184 |
| 10-19T12:45 | 3474 |  | 3885075 | 21393 | 20209 | 3861311 | 1104426 | 175 |
| 10-19T13:00 | 3679 |  | 4134225 | 22270 | 21021 | 4109779 | 1195079 | 172 |
| 10-19T13:15 | 3896 |  | 4382144 | 23402 | 22035 | 4356900 | 1292398 | 170 |
| 10-19T13:30 | 4128 |  | 4645070 | 24290 | 22846 | 4619075 | 1392404 | 166 |
| 10-19T13:45 | 4336 |  | 4906425 | 25414 | 23892 | 4879500 | 1470361 | 167 |
| 10-19T14:00 | 4505 |  | 5128845 | 26450 | 24829 | 5101199 | 1441723 | 171 |
| 10-19T14:15 | 4684 |  | 5350959 | 26925 | 25748 | 5322204 | 1443354 | 181 |
| 10-19T14:30 | 4842 |  | 5573399 | 26515 | 26565 | 5544041 | 1505564 | 188 |
| 10-19T14:45 | 5017 |  | 5822840 | 26693 | 27528 | 5791297 | 1570447 | 196 |
| 10-19T15:00 | 5162 |  | 6076025 | 26711 | 28317 | 6043795 | 1617174 | 192 |
| 10-19T15:15 | 5371 |  | 6319614 | 26679 | 29139 | 6286637 | 1676499 | 193 |
| 10-19T15:30 | 5574 |  | 6581533 | 27014 | 29981 | 6547121 | 1733999 | 199 |
| 10-19T15:45 | 5743 |  | 6841192 | 27216 | 30822 | 6806067 | 1824277 | 200 |
| 10-19T16:00 | 5921 |  | 7089678 | 27495 | 31686 | 7053880 | 1898169 | 191 |
| 10-19T16:15 | 6101 |  | 7337527 | 27915 | 32708 | 7300685 | 1982939 | 191 |
| 10-19T16:30 | 6266 |  | 7580250 | 28333 | 33679 | 7542673 | 2048596 | 194 |
| 10-19T16:45 | 6459 |  | 7851172 | 28578 | 34626 | 7812903 | 2128474 | 190 |
| 10-19T17:00 | 6630 |  | 8123858 | 28856 | 35632 | 8084909 | 2215737 | 189 |

## 3. 9 h, bounds ON

| sim (UTC) | RSS MB | tracemalloc MB | rows applied | state pools | engine pools | engine hist rows | quote rows | prefetch pools |
|---|---|---|---|---|---|---|---|---|
| 10-19T08:15 | 237 |  | 127342 | 1914 | 1887 | 126014 | 60743 | 0 |
| 10-19T08:30 | 446 |  | 333441 | 3951 | 3884 | 330963 | 135276 | 0 |
| 10-19T08:45 | 659 |  | 566945 | 5304 | 5163 | 563516 | 228807 | 0 |
| 10-19T09:00 | 844 |  | 770974 | 6616 | 6445 | 766834 | 302704 | 0 |
| 10-19T09:15 | 1034 |  | 969849 | 7709 | 7471 | 964090 | 371582 | 0 |
| 10-19T09:30 | 1186 |  | 1150133 | 8621 | 7795 | 1133305 | 432848 | 0 |
| 10-19T09:45 | 1308 |  | 1349879 | 9967 | 7608 | 1281259 | 511492 | 0 |
| 10-19T10:00 | 1441 |  | 1580201 | 11279 | 7878 | 1418118 | 597030 | 0 |
| 10-19T10:15 | 1568 |  | 1811257 | 12324 | 7878 | 1532827 | 687633 | 124 |
| 10-19T10:30 | 1699 |  | 2036948 | 13188 | 7576 | 1660593 | 726543 | 155 |
| 10-19T10:45 | 1848 |  | 2287059 | 14154 | 7804 | 1805012 | 796743 | 174 |
| 10-19T11:00 | 1904 |  | 2484507 | 15115 | 7905 | 1854175 | 825962 | 177 |
| 10-19T11:15 | 1991 |  | 2693869 | 15899 | 7835 | 1910301 | 861892 | 179 |
| 10-19T11:30 | 2055 |  | 2871073 | 16798 | 7612 | 1987229 | 841742 | 179 |
| 10-19T11:45 | 2133 |  | 3067370 | 17841 | 7760 | 2009559 | 882005 | 189 |
| 10-19T12:00 | 2220 |  | 3254619 | 18910 | 8011 | 2112282 | 920128 | 186 |
| 10-19T12:15 | 2275 |  | 3448358 | 19685 | 7832 | 2057768 | 968088 | 179 |
| 10-19T12:30 | 2341 |  | 3648727 | 20513 | 7821 | 2137750 | 1027685 | 184 |
| 10-19T12:45 | 2393 |  | 3885075 | 21393 | 7915 | 2097791 | 1104426 | 175 |
| 10-19T13:00 | 2500 |  | 4134225 | 22270 | 7873 | 2204674 | 1195079 | 172 |
| 10-19T13:15 | 2622 |  | 4382144 | 23402 | 7891 | 2276777 | 1292398 | 170 |
| 10-19T13:30 | 2777 |  | 4645070 | 24290 | 8004 | 2430225 | 1392404 | 166 |
| 10-19T13:45 | 2892 |  | 4906425 | 25414 | 8671 | 2571294 | 1470361 | 167 |
| 10-19T14:00 | 2966 |  | 5128845 | 26450 | 8981 | 2652610 | 1441723 | 171 |
| 10-19T14:15 | 3068 |  | 5350959 | 26925 | 9268 | 2750139 | 1443343 | 181 |
| 10-19T14:30 | 3134 |  | 5573399 | 26515 | 9229 | 2830815 | 1505314 | 188 |
| 10-19T14:45 | 3245 |  | 5822840 | 26693 | 9295 | 2950368 | 1570154 | 196 |
| 10-19T15:00 | 3304 |  | 6076025 | 26711 | 8776 | 3066066 | 1614840 | 192 |
| 10-19T15:15 | 3392 |  | 6319614 | 26679 | 8700 | 3119068 | 1644806 | 193 |
| 10-19T15:30 | 3496 |  | 6581533 | 27014 | 8450 | 3236042 | 1702221 | 199 |
| 10-19T15:45 | 3580 |  | 6841192 | 27216 | 8541 | 3347551 | 1792445 | 200 |
| 10-19T16:00 | 3639 |  | 7089678 | 27495 | 8661 | 3388430 | 1845166 | 191 |
| 10-19T16:15 | 3679 |  | 7337527 | 27915 | 9073 | 3447354 | 1929071 | 191 |
| 10-19T16:30 | 3707 |  | 7580250 | 28333 | 9201 | 3439110 | 1973023 | 194 |
| 10-19T16:45 | 3738 |  | 7851172 | 28578 | 9414 | 3512100 | 2051929 | 190 |
| 10-19T17:00 | 3790 |  | 8123858 | 28856 | 9590 | 3575940 | 2139027 | 189 |

## 4. Hourly side by side

| sim (UTC) | rows applied | RSS MB, bounds off | RSS MB, prune + quote sweep (this PR) | RSS MB, plus engine hist trim (what-if) | engine hist rows (what-if) |
|---|---|---|---|---|---|
| 10-19T09:00 | 770974 | 836 | 844 | 829 | 766834 |
| 10-19T10:00 | 1580201 | 1517 | 1441 | 1200 | 1040773 |
| 10-19T11:00 | 2484507 | 2284 | 1904 | 1486 | 1154632 |
| 10-19T12:00 | 3254619 | 2911 | 2220 | 1558 | 998795 |
| 10-19T13:00 | 4134225 | 3679 | 2500 | 1773 | 1090908 |
| 10-19T14:00 | 5128845 | 4505 | 2966 | 2124 | 1277461 |
| 10-19T15:00 | 6076025 | 5162 | 3304 | 2135 | 1205502 |
| 10-19T16:00 | 7089678 | 5921 | 3639 | 2282 | 1303474 |
| 10-19T17:00 | 8123858 | 6630 | 3790 | 2354 | 1341628 |

## 5. Decision equivalence on the real 9 h tape (timing rows off: they use the wall clock)

| run | decision log md5 (all arms, SCAN, RISK) | ledger digest | ledger digest without the `mint` field |
|---|---|---|---|
| bounds off | eb77b1a22de7a9a1cacebffb0f486366 | d0dba3f062a3fa8ff9c9fed9cb4a4318 | 208b3d1de42dd2c0c251251183d2ce04 |
| bounds on (this PR) | eb77b1a22de7a9a1cacebffb0f486366 | 631963549751978c3544ce858081dc35 | 208b3d1de42dd2c0c251251183d2ce04 |
| bounds on + engine hist trim (what-if) | eb77b1a22de7a9a1cacebffb0f486366 | 631963549751978c3544ce858081dc35 | 208b3d1de42dd2c0c251251183d2ce04 |

The digest drops the process-start record, `engine_id`, `code_head` and `code_dirty` (process identity). The two runs differ in the `mint` field of 5 ledger rows (2 positions, one BIG-X and one CON-X): the unbounded engine keeps the `PoolHist` it created from the pool's first print, which carried no mint, so those rows say `null`; after a prune the engine re-creates the history from a later print and the rows carry the mint. No price, size, slot, fill, exclusion or PnL field differs. 37 open rows and 102 trade rows in all, 139 rows over two bots (CON-X, BIG-X): in this stub replay only the X arms trade.

## 6. Replay of the failing run (#1236): state warm-up from 08:30, first tick 14:35:23, public chain (holders off), timing on

| sim (UTC) | rows applied | RSS MB, bounds off | RSS MB, bounds on |
|---|---|---|---|
| 10-19T14:45 | 5489143 | 4785 | 3147 |
| 10-19T15:00 | 5742328 | 4959 | 3217 |
| 10-19T15:15 | 5985917 | 5143 | 3307 |
| 10-19T15:30 | 6247836 | 5352 | 3400 |
| 10-19T15:45 | 6507495 | 5542 | 3491 |
| 10-19T16:00 | 6755981 | 5701 | 3564 |
| 10-19T16:15 | 7003830 | 5887 | 3614 |
| 10-19T16:30 | 7246553 | 6051 | 3660 |
| 10-19T16:45 | 7517475 | 6250 | 3683 |
| 10-19T17:00 | 7790161 | 6422 | 3719 |

The real process was at 6,274,564 kB anon-rss at 17:07:29Z. The warm-up (5.3 million prints read before the first tick) already costs 4.8 GB with the engine keeping every print; with the bounds on it is 3.1 GB.
