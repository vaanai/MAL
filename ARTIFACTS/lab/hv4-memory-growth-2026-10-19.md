# HV4 stub memory growth, measured 2026-10-19 (tools/hv4_memreplay.py output)

Paper only. The chain layer is mocked (no RPC call), the model is StubModel, the data is the closed arena tape hours 2026-10-19T08 to T16 (T09 to T11 for section 1). Nothing here says any book made money.

**Status after QP 6274522375 (NOT OK at 8ee39dc).** The `ArenaEngine.prune()` call in the runner is withdrawn: it is not pricing-equivalent (section 5). What this branch bounds now is `QuoteHistory` only, which saves about 30 MB of 6.6 GB at 9 h. The engine's print history (the real grower, sections 1 and 2) is untouched and waits for a state-preserving trim in `tools/arena_paper.py` (its own branch). Section 4 keeps the withdrawn and what-if runs beside the current one for comparison; they are not in this branch's code.

Sections 1 to 3 are written by the harness, not by hand. Section 1 is a tracemalloc run (3 closed hours, a snapshot every 15 simulated minutes) on the code before any fix; its mock chain refused every card on a slot mismatch (fixed afterwards in the harness), so card-side structures are not in it; the engine and state sites are. RSS in that run is inflated by tracemalloc itself.

## 1. tracemalloc, before any fix

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

Reading the sites: `arena_paper.py:723/727/869/886/923/926/927` are the engine's `PoolHist` (one entry per print of every pool: the order-key tuple, the dedupe tuple, nine parallel lists, the float copies); `decoder.py:353` is the JSON decoder, and most of its bytes are the 88-character signature strings those tuples keep alive; `arena_state.py:438/442/466` are the state's own 60-minute event deque, per-trader net dict and candles; `hv4_runner.py:567/357/358` is `QuoteHistory` (the floats and the two lists).

## 2. 9 h, bounds OFF (`RunnerConfig.bound_memory=False`: exactly the 2026-10-19 behaviour)

| sim (UTC) | RSS MB | tracemalloc MB | rows applied | state pools | engine pools | engine hist rows | quote rows | prefetch pools |
|---|---|---|---|---|---|---|---|---|
| 10-19T08:15 | 237 |  | 127342 | 1914 | 1887 | 126014 | 60743 | 0 |
| 10-19T08:30 | 438 |  | 333441 | 3951 | 3884 | 330963 | 135276 | 0 |
| 10-19T08:45 | 669 |  | 566945 | 5304 | 5163 | 563516 | 228807 | 0 |
| 10-19T09:00 | 845 |  | 770974 | 6616 | 6445 | 766834 | 302704 | 0 |
| 10-19T09:15 | 1020 |  | 969849 | 7709 | 7471 | 964090 | 371582 | 0 |
| 10-19T09:30 | 1181 |  | 1150133 | 8621 | 8330 | 1143655 | 432848 | 0 |
| 10-19T09:45 | 1350 |  | 1349879 | 9967 | 9618 | 1341578 | 511492 | 0 |
| 10-19T10:00 | 1531 |  | 1580201 | 11279 | 10897 | 1570751 | 597030 | 0 |
| 10-19T10:15 | 1739 |  | 1811257 | 12324 | 11880 | 1795711 | 687633 | 124 |
| 10-19T10:30 | 1927 |  | 2036948 | 13188 | 12690 | 2020646 | 749256 | 155 |
| 10-19T10:45 | 2141 |  | 2287059 | 14154 | 13582 | 2269973 | 835089 | 174 |
| 10-19T11:00 | 2285 |  | 2484507 | 15115 | 14492 | 2466661 | 877683 | 177 |
| 10-19T11:15 | 2476 |  | 2693869 | 15899 | 15210 | 2675391 | 925180 | 179 |
| 10-19T11:30 | 2601 |  | 2871073 | 16798 | 16010 | 2851565 | 922271 | 179 |
| 10-19T11:45 | 2778 |  | 3067370 | 17841 | 16967 | 3046397 | 980521 | 189 |
| 10-19T12:00 | 2917 |  | 3254619 | 18910 | 17967 | 3232847 | 1034526 | 186 |
| 10-19T12:15 | 3091 |  | 3448358 | 19685 | 18669 | 3425949 | 1099653 | 179 |
| 10-19T12:30 | 3288 |  | 3648727 | 20513 | 19415 | 3625691 | 1172250 | 184 |
| 10-19T12:45 | 3474 |  | 3885075 | 21393 | 20209 | 3861311 | 1264902 | 175 |
| 10-19T13:00 | 3673 |  | 4134225 | 22270 | 21021 | 4109779 | 1373292 | 172 |
| 10-19T13:15 | 3924 |  | 4382144 | 23402 | 22035 | 4356900 | 1482480 | 170 |
| 10-19T13:30 | 4136 |  | 4645070 | 24290 | 22846 | 4619075 | 1594459 | 166 |
| 10-19T13:45 | 4350 |  | 4906425 | 25414 | 23892 | 4879500 | 1695736 | 167 |
| 10-19T14:00 | 4518 |  | 5128845 | 26450 | 24829 | 5101199 | 1667409 | 171 |
| 10-19T14:15 | 4697 |  | 5350959 | 26925 | 25748 | 5322204 | 1693369 | 181 |
| 10-19T14:30 | 4855 |  | 5573399 | 26515 | 26565 | 5544041 | 1774805 | 188 |
| 10-19T14:45 | 5044 |  | 5822840 | 26693 | 27528 | 5791297 | 1857499 | 196 |
| 10-19T15:00 | 5179 |  | 6076025 | 26711 | 28317 | 6043795 | 1920835 | 192 |
| 10-19T15:15 | 5387 |  | 6319614 | 26679 | 29139 | 6286637 | 1988589 | 193 |
| 10-19T15:30 | 5590 |  | 6581533 | 27014 | 29981 | 6547121 | 2064635 | 199 |
| 10-19T15:45 | 5774 |  | 6841192 | 27216 | 30822 | 6806067 | 2162458 | 200 |
| 10-19T16:00 | 5938 |  | 7089678 | 27495 | 31686 | 7053880 | 2244794 | 191 |
| 10-19T16:15 | 6124 |  | 7337527 | 27915 | 32708 | 7300685 | 2347855 | 191 |
| 10-19T16:30 | 6294 |  | 7580250 | 28333 | 33679 | 7542673 | 2432648 | 194 |
| 10-19T16:45 | 6484 |  | 7851172 | 28578 | 34626 | 7812903 | 2531077 | 190 |
| 10-19T17:00 | 6653 |  | 8123858 | 28856 | 35632 | 8084909 | 2632796 | 189 |

## 3. 9 h, this branch (QuoteHistory trim and sweep)

| sim (UTC) | RSS MB | tracemalloc MB | rows applied | state pools | engine pools | engine hist rows | quote rows | prefetch pools |
|---|---|---|---|---|---|---|---|---|
| 10-19T08:15 | 247 |  | 127342 | 1914 | 1887 | 126014 | 60743 | 0 |
| 10-19T08:30 | 445 |  | 333441 | 3951 | 3884 | 330963 | 135276 | 0 |
| 10-19T08:45 | 665 |  | 566945 | 5304 | 5163 | 563516 | 228807 | 0 |
| 10-19T09:00 | 832 |  | 770974 | 6616 | 6445 | 766834 | 302704 | 0 |
| 10-19T09:15 | 1027 |  | 969849 | 7709 | 7471 | 964090 | 371582 | 0 |
| 10-19T09:30 | 1181 |  | 1150133 | 8621 | 8330 | 1143655 | 432848 | 0 |
| 10-19T09:45 | 1343 |  | 1349879 | 9967 | 9618 | 1341578 | 511492 | 0 |
| 10-19T10:00 | 1520 |  | 1580201 | 11279 | 10897 | 1570751 | 597030 | 0 |
| 10-19T10:15 | 1737 |  | 1811257 | 12324 | 11880 | 1795711 | 687633 | 124 |
| 10-19T10:30 | 1926 |  | 2036948 | 13188 | 12690 | 2020646 | 726543 | 155 |
| 10-19T10:45 | 2139 |  | 2287059 | 14154 | 13582 | 2269973 | 796743 | 174 |
| 10-19T11:00 | 2285 |  | 2484507 | 15115 | 14492 | 2466661 | 825962 | 177 |
| 10-19T11:15 | 2483 |  | 2693869 | 15899 | 15210 | 2675391 | 861892 | 179 |
| 10-19T11:30 | 2604 |  | 2871073 | 16798 | 16010 | 2851565 | 841742 | 179 |
| 10-19T11:45 | 2763 |  | 3067370 | 17841 | 16967 | 3046397 | 882005 | 189 |
| 10-19T12:00 | 2910 |  | 3254619 | 18910 | 17967 | 3232847 | 920128 | 186 |
| 10-19T12:15 | 3085 |  | 3448358 | 19685 | 18669 | 3425949 | 968088 | 179 |
| 10-19T12:30 | 3269 |  | 3648727 | 20513 | 19415 | 3625691 | 1027685 | 184 |
| 10-19T12:45 | 3472 |  | 3885075 | 21393 | 20209 | 3861311 | 1104426 | 175 |
| 10-19T13:00 | 3679 |  | 4134225 | 22270 | 21021 | 4109779 | 1195079 | 172 |
| 10-19T13:15 | 3913 |  | 4382144 | 23402 | 22035 | 4356900 | 1292398 | 170 |
| 10-19T13:30 | 4126 |  | 4645070 | 24290 | 22846 | 4619075 | 1392404 | 166 |
| 10-19T13:45 | 4339 |  | 4906425 | 25414 | 23892 | 4879500 | 1470361 | 167 |
| 10-19T14:00 | 4506 |  | 5128845 | 26450 | 24829 | 5101199 | 1441723 | 171 |
| 10-19T14:15 | 4676 |  | 5350959 | 26925 | 25748 | 5322204 | 1443343 | 181 |
| 10-19T14:30 | 4842 |  | 5573399 | 26515 | 26565 | 5544041 | 1505315 | 188 |
| 10-19T14:45 | 5017 |  | 5822840 | 26693 | 27528 | 5791297 | 1570154 | 196 |
| 10-19T15:00 | 5191 |  | 6076025 | 26711 | 28317 | 6043795 | 1614840 | 192 |
| 10-19T15:15 | 5374 |  | 6319614 | 26679 | 29139 | 6286637 | 1644806 | 193 |
| 10-19T15:30 | 5569 |  | 6581533 | 27014 | 29981 | 6547121 | 1702221 | 199 |
| 10-19T15:45 | 5756 |  | 6841192 | 27216 | 30822 | 6806067 | 1792445 | 200 |
| 10-19T16:00 | 5919 |  | 7089678 | 27495 | 31686 | 7053880 | 1845166 | 191 |
| 10-19T16:15 | 6095 |  | 7337527 | 27915 | 32708 | 7300685 | 1929071 | 191 |
| 10-19T16:30 | 6263 |  | 7580250 | 28333 | 33679 | 7542673 | 1973023 | 194 |
| 10-19T16:45 | 6456 |  | 7851172 | 28578 | 34626 | 7812903 | 2052581 | 190 |
| 10-19T17:00 | 6617 |  | 8123858 | 28856 | 35632 | 8084909 | 2139027 | 189 |

## 4. Hourly side by side (RSS, MB)

| sim (UTC) | rows applied | bounds off (the 2026-10-19 behaviour) | this PR (QuoteHistory bound only) | withdrawn: engine prune() + quote bounds (8ee39dc) | what-if, never in the runner: + trim of active pools' history to 20,000 slots |
|---|---|---|---|---|---|
| 10-19T09:00 | 770974 | 845 | 832 | 844 | 829 |
| 10-19T10:00 | 1580201 | 1531 | 1520 | 1441 | 1200 |
| 10-19T11:00 | 2484507 | 2285 | 2285 | 1904 | 1486 |
| 10-19T12:00 | 3254619 | 2917 | 2910 | 2220 | 1558 |
| 10-19T13:00 | 4134225 | 3673 | 3679 | 2500 | 1773 |
| 10-19T14:00 | 5128845 | 4518 | 4506 | 2966 | 2124 |
| 10-19T15:00 | 6076025 | 5179 | 5191 | 3304 | 2135 |
| 10-19T16:00 | 7089678 | 5938 | 5919 | 3639 | 2282 |
| 10-19T17:00 | 8123858 | 6653 | 6617 | 3790 | 2354 |

## 5. Pricing equivalence of the engine, print by print (QP edit 2)

The harness feeds a second, never-pruned engine the same rows and, at each sample, compares for every print the primary engine still holds (matched by order key) the triple (slot, v, vsrc). Real 9 h tape, all pools (WSOL or not, bonding or PumpSwap).

| run | pools held at the end | prints held at the end | prints differing at the end | distinct differing prints over the run | pools where last_v_row differs | pools where mint differs |
|---|---|---|---|---|---|---|
| this branch (no engine prune) | 35632 | 8084909 | **0** | **0** | 0 | 0 |
| withdrawn: `engine.prune()` every 5 tape minutes (the exposure) | 9590 | 3575940 | 520 | 982 (in 249 pools) | 91 | 2127 |

In the withdrawn run every differing print is `none<-pool_prev` (V source in the pruned engine, from the unbounded engine's): a revival print with V outside [0, 30 SOL] is priced `none` (a `missing_v` exclusion) instead of from the pool's earlier V. 982 prints over 249 pools is the measured exposure of that change on this tape; the 37 X-arm opens of this stub replay priced identically (the ledger digest without `mint` is unchanged), although 2 of them sat on re-created pools.

QP's count over one closed hour (T11, counts only; not mine): 645,463 PumpSwap trade rows, 0 lacking `virtual_quote_reserves`, 151,589 with V outside [0, 30 SOL] (137,509 negative, 14,080 above 30 SOL); 140 of 2,025 WSOL pools had both in-band and out-of-band rows in that hour. So the `pool_prev` fallback is live on about 7% of WSOL pools.

## 6. Decision log and ledger on the real 9 h tape (timing rows off: they read the wall clock)

| run | decision log md5 (every arm log, SCAN, RISK) | ledger digest (mint included) | ledger digest without `mint` |
|---|---|---|---|
| bounds off | eb77b1a22de7a9a1cacebffb0f486366 | d0dba3f062a3fa8ff9c9fed9cb4a4318 | 208b3d1de42dd2c0c251251183d2ce04 |
| this branch | eb77b1a22de7a9a1cacebffb0f486366 | d0dba3f062a3fa8ff9c9fed9cb4a4318 | 208b3d1de42dd2c0c251251183d2ce04 |
| withdrawn: engine prune | eb77b1a22de7a9a1cacebffb0f486366 | 631963549751978c3544ce858081dc35 | 208b3d1de42dd2c0c251251183d2ce04 |

The digest drops the process-start record, `engine_id`, `code_head` and `code_dirty`. In this stub replay the stub abstains and only CON-X and BIG-X trade (37 open rows, 102 trade rows). The withdrawn run differs only in the `mint` field of 5 rows (2 positions): `PoolHist` takes the mint from the first print it saw, and a pruned pool is re-created from a later print. `mint` is not read by `arena_score.py`, but `hv4_read.decision_keys` is (kind, position id, entry ms, pool, mint) and the V1 seed-1 keys of DESIGN Amendment A1 use it. The sweep of `QuoteHistory` was argued equivalent only up to the state's own 60 s sweep; it now drops a pool only when the state no longer holds the key.

## 7. Replay of the failing run (#1236): state warm-up from 08:30, first tick 14:35:23, public chain (holders off), timing on

| sim (UTC) | rows applied | RSS MB, bounds off | RSS MB, this PR |
|---|---|---|---|
| 10-19T14:45 | 5489143 | 4785 | 4767 |
| 10-19T15:00 | 5742328 | 4959 | 4952 |
| 10-19T15:15 | 5985917 | 5143 | 5140 |
| 10-19T15:30 | 6247836 | 5352 | 5340 |
| 10-19T15:45 | 6507495 | 5542 | 5521 |
| 10-19T16:00 | 6755981 | 5701 | 5688 |
| 10-19T16:15 | 7003830 | 5887 | 5875 |
| 10-19T16:30 | 7246553 | 6051 | 6029 |
| 10-19T16:45 | 7517475 | 6250 | 6233 |
| 10-19T17:00 | 7790161 | 6422 | 6399 |

The real process was at 6,274,564 kB anon-rss at 17:07:29Z. The warm-up (5.3 million prints read before the first tick) alone costs 4.8 GB with the engine keeping every print.
