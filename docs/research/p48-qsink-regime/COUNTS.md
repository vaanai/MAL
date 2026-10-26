# P48: QSINK regime change, counts only

Status: written 2026-10-26 ~19:10Z (`date -u` 19:06:34Z before writing). Lane P48, manager17. Counts only. Nothing here says a book makes money. No book has cleared the promotion gate. No price, fill, exit, P&L or outcome is computed anywhere.

**Disclosure.** Everything below is built on arena tape hours 2026-10-19T08 .. 2026-10-24T23 (136 closed hours, all before 2026-10-25T00Z; 10-19 is a partial day from the feed start at about 08:05Z). These are **walk-2-hour information** (DEC-030 §2 R1). No in-clock hour (>= 2026-10-25T00Z) was read from the tape. A rule priced on these hours would be in-sample, never OOS.

## Short answer

1. **Nothing changed on chain on 10-22 for PUMP.** The P44 jump (child-leg PUMP buys >= 0.5 SOL: 6 / 94 / 230 / 187 for 10-21..10-24) is **tape rows from transactions that never landed**. Almost all of them come from one signer, `BUpWiWy8Tqed4jy86dr7ZpoaE747DpQsrGnwmtpMwfBr`, plus a few from `oi1ewFJP7HikgdbjhnUboCzuWXHanAe4Wtvy8UFEnd6`. Public RPC `getSignatureStatuses` (with `searchTransactionHistory`) found **none** of the sampled PUMP-pool buys >= 0.5 SOL from these two: `BUpWiWy8` 0 of 73 in a first sample and 0 of 20 in a second, `oi1ewFJP` 0 of 10 and 0 of 8. It also found 0 of 60 `BUpWiWy8` routed buys >= 0.5 SOL into PUMP, USDC and pump-coin parents (20 each). Controls from the same pools and days were found **240 of 240** (USDC and pump-coin parents, non-phantom signers, buys >= 0.5 SOL) and **48 of 48** (small PUMP-pool trades). **Landed** routed PUMP buys >= 0.5 SOL: **0 on every day**, 10-19 to 10-24. Child creation did not change either. PUMP-quoted creates were 3,236 (partial day) / 4,130 / 5,003 / 5,126 / 4,990 / 4,178. Sampled creates on 10-20, 10-22 and 10-23 call only the pump.fun program (`6EF8...`), with no third-party launchpad program. The web search found no pump.fun or PumpSwap announcement for 10-20..10-23. **One actor, and not a real flow.**
2. **Size and shape (landed class):**
   - PUMP parents get **1.363 to 2.926 SOL a day** of routed buys, in 22 to 154 buys, against 8.859 to 19.427 SOL of daily buys in all of PUMP's WSOL PumpSwap pools. PUMP's real liquidity sits in its USDC pool, and most PUMP-child trades carry no SOL leg on PumpSwap.
   - The QSINK volume is in **other quote mints**. Through pump-coin parents, landed routed buys were 7,910 (partial day) / 28,372 / 59,813 / 9,083 / 19,380 / 15,214 SOL a day. Through the USDC/WSOL pool, 3,526 to 7,876 SOL a day.
   - Routed sells match routed buys every day. The net routed flow into pump-coin parents was **negative on all six days** (-421 to -1,826 SOL a day).
   - Same signer, same child: for USDC and pump-coin parents, 0.747 to 0.858 of routed buys are followed by a routed sell within 1 h, and 0.838 to 0.912 within 24 h. That is mostly round-trip bot flow, which is the reversal J3 found, and it is faster than J3 implied.
3. **Which parents:** many, not just PUMP. There are 358 to 583 pump-coin parents with any landed routed buy per day. Among them, 3 to 10 per day take >= 500 SOL; most stay hot for one or two days and then fade (`CSocnzcR...pump`: 16,342 then 51,510 SOL, then 1,811 / 443 / 109). Steadier parents route a large share of their pool's daily buy SOL every day: `NTRNDMqQ...` 0.390 to 0.573, USDC 0.430 to 0.620, `H74CYmXg...` 0.173 to 0.731. PUMP is a minor parent by SOL.
4. **No slow structure is shown on counts.** Forced parent buying is offset by forced parent selling within the hour, net negative by day. Capacity is tiny even on optimistic numbers (section 4). No pre-10-08 exploration block has pump-coin quotes, so only October data (sealed domains or walk-2) or a forward test could decide. **Recommendation: do not open a pricing lane.** Fix or flag the tape-quality issue first (section 0). It touches every arena consumer.

## 0. Tape quality: rows of transactions that never landed (manager action)

- **What:** the arena tape at `/data/mal/arena/tape` has trade rows whose signatures are not on chain, as ok or as failed. Public RPC `getSignatureStatuses` does find this signer's *failed* transactions: 10 of 10 recent signatures were found, 4 of them with an error. So "not found" means the transaction never landed, not that it failed. The cause is not established. The feed is a public-RPC `logsSubscribe` capture at confirmed commitment, and `tools/arena_feed.py:204` drops `note.failed`. In one hour (10-23T12), 2 of the 9 distinct `BUpWiWy8` signatures in the PUMP pool appear at **two different slots** (460448763 and 460450207). A landed transaction cannot do that.
- **Evidence (public RPC, 0 Helius):**
  - `BUpWiWy8` PUMP-pool buys >= 0.5 SOL: 0 of 73 found in the first sample (every one on 10-19..10-21, then 20 per day), and 0 of 20 in a second sample of its routed PUMP legs. Its USDC-leg buys: 0 of 20 found. Its pump-coin-leg buys: 0 of 20. Its child curve buys with no SOL leg: 0 of 20.
  - `BUpWiWy8` child sells: 10 of 20 found.
  - `oi1ewFJP` PUMP-pool buys >= 0.5 SOL: 0 of 10 found, and 0 of 8 routed ones in the second sample.
  - Controls: 240 of 240 and 48 of 48 found.
  - Signers with a high but partial reserve-marker miss rate (`MriyaNN8` 0.381, `7dGrdJRY` 0.258, `8oSD5a9E` 0.374): 60 of 60 found. Their misses are marker noise.
  - Files: `data/p48_landing_sample*.json`, `data/p48_marker_validation.json`.
- **Tape-only marker:** PumpSwap reserves on the tape are PRE-trade. A row "applied" when the next print in its pool shows the quote reserve moved by the row's `pool_quote_amount`. `BUpWiWy8`'s rows in the top-42 parents' WSOL pools: 4,327 of 4,366 not applied (0.991), 72,643.9 of 72,678.8 SOL. `oi1ewFJP`: 65 of 66 (0.985). All rows: 0.1264 not applied. In busy pools the marker is noisy, so it is a flag, not a filter: 57 of 60 sampled "not applied" rows from other signers landed. `data/p48_trader_marker_top.json` has the top 15 signers.
- **Size on the tape:** rows signed by `BUpWiWy8` per day were 6,059 / 8,961 / 19,942 / 21,508 / 28,882 / 24,964 (10-19..10-24). In the PUMP/WSOL pool `539m4m...` (about 14 SOL deep), its rows in the sampled hour 10-23T12 are repeated buys of 2.652 SOL. P44's buy/Q ratio p50 on 10-23 is 0.185. Across 7 sampled hours over the six days, the pool's quote reserve stays at 13.745 to 14.905 SOL, although its phantom-class buy rows sum to 40.739 to 923.42 SOL a day. On chain today, its newest 1,000 transactions (2026-10-26 18:29:33Z to 18:39:24Z) are 951 failed. They go through a guard program `L2TExMFKdjpN9kozasaurPirfHy9P8sbXoAN1qA3S95` (custom errors 6001 / 6042).
- **Why it matters beyond QSINK:** any arena-tape consumer that reads prints, buyer sets or SOL sizes can see these rows. That includes P44's pump-sink counts, behavioural BOOST detectors, and the arena paper books. **I did not check any HV4, Q50 or arena-book output** (sealed or out of scope). Suggested ops step, for the manager to decide: a feed-side or reader-side filter. Two candidates: drop a signature seen at more than one slot, and/or drop the two signers listed above. Plus a QP note on whether past arena counts need a re-count. P44's `hunt23-w3-pump-sink-1` row ("kill not fired") rests on these rows.

## Method

All code is in this directory. Outputs are on mal-research-0 in `/data/mal/hunt-1008/push-1016/p48-qsink-regime/`; the small ones are copied to `data/`.

| step | file | what | run |
|---|---|---|---|
| pass 1 | `p48_extract.py pass1` | create rows with a non-WSOL quote (children), pool_create, complete | inline scope (MemoryMax 8G), 59 s |
| pass 2 | `p48_extract.py pass2` | trades of children (tape creates, plus any pump.fun curve trade with `zero_sol`, which covers children created before the tape start) and of PUMP; plus every PumpSwap trade sharing a signature with a child trade (the parent leg) | inline scope, 1 min 57 s, 7,141,440 rows |
| pass 3 | `p48_extract.py pass3` | every row of the 713 PUMP/WSOL buys >= 0.5 SOL (discovery only) | inline scope |
| pass 4 | `p48_extract.py pass4` | every PumpSwap trade of 42 parent mints: those with >= 500 SOL routed buys on the raw tape, with PUMP and USDC added if missing (together 92.07% of raw routed buy SOL) | inline scope, 36 s, 2,946,846 rows |
| group | `p48_group.py` | one record per transaction holding a child trade: child rows, SOL legs (PumpSwap, WSOL quote, mint not a traded child) | inline scope, 1 min 34 s, 5,445,646 records |
| labels | `p48_analyze.py` | landing class, routed legs, parent pool volume, reversal labels, child cohorts, seal guard | MiScusi job **#1676** (`j_hjEHAoVutgHhrA`, @215fe184, peak 2.9 GB, 49 s) |

Definitions:
- A **routed buy (sell)** is a SOL leg on the buy (sell) side inside a transaction that trades a child.
- **Landed class** means the leg's signer is not one of the two phantom signers. Every other signer is sampled, not proven: the 240 of 240 and 60 of 60 RPC checks.
- **Reversal label:** a routed buy is followed by a routed sell from the same signer of the same child within 1 h, 6 h or 24 h. A buy whose window passes 2026-10-25T00Z is censored and left out.

Seal guard: legs within 360 s of a V-band WSOL `pool_create` on the tape are dropped. None were. No G0 trigger, BOOST test or H5 outcome is computed.

Output `data/p48_counts.json` sha256 `4fc30bf1c047be10ce157b7d28667605926f5a70eaec6fb94684f350ca53a74a`. Intermediates: pass1 `8c5a0d2e...b885`, pass2 `5b3303ba...59ad`, pass4 `7b54a05b...3b96`, sigs `becaf12c...865f`.

## 1. What changed on 10-22

- Landed routed PUMP buys did not change: 22 / 60 / 154 / 134 / 59 / 46 buys, 1.758 / 2.295 / 2.926 / 2.537 / 2.753 / 1.363 SOL. The phantom class did: 7 / 7 / 6 / 144 / 304 / 241 buys, 40.739 / 85.804 / 148.658 / 380.004 / 882.6 / 680.557 SOL.
- **One actor.** `BUpWiWy8` is a multi-quote child buyer.
  - Child buy rows on the tape: 4,393 / 6,568 / 14,635 / 17,333 / 23,040 / 19,917 per day, on 999 / 1,462 / 1,604 / 1,689 / 1,699 / 1,250 distinct children. Children span PreStocks, xStocks, USDC, PUMP and pump coins.
  - First buy after create, p50: 18.6 / 33.6 / 29.2 / 18.3 / 12.0 / 10.3 s.
  - Never the child's creator (0 of its 66,298 buys of tape-created children).
  - It was already present on 10-19..10-21 (5 / 6 / 4 PUMP buys >= 0.5 SOL). From 10-22 a larger share of its rows went to PUMP children, after its 10-21 rows went to `CSocnzcR...pump` (1,353 buys, 37,986.4 SOL; its pump-coin-leg buys sampled 0 of 20 found).
- PUMP-child creators are spread out: 252 / 401 / 700 / 887 / 638 / 469 distinct per day, with a top creator share of 0.475 (partial 10-19), then 0.1978 / 0.0925 / 0.0542 / 0.0631 / 0.0759. The creating program is pump.fun itself (6 sampled create txs on 10-20, 10-22 and 10-23).
- Web (10-20..10-23): no pump.fun or PumpSwap announcement found. The custom-pair launch is dated 2026-09-10 (tokenized stocks and RWAs; Bankless, FXStreet). The library has pump-coin parents since 2026-10-08 (`p25-quote-drift-completion`). [web, unverified]

## 2. Size and shape (per day, landed class unless marked)

**PUMP parents**

| day | routed buys | buy SOL | buys >= 0.5 SOL | routed sells | sell SOL | children with a routed buy | phantom buys | phantom buy SOL |
|---|---|---|---|---|---|---|---|---|
| 10-19 | 22 | 1.758 | 0 | 53 | 1.319 | 11 | 7 | 40.739 |
| 10-20 | 60 | 2.295 | 0 | 55 | 1.391 | 18 | 7 | 85.804 |
| 10-21 | 154 | 2.926 | 0 | 180 | 3.85 | 79 | 6 | 148.658 |
| 10-22 | 134 | 2.537 | 0 | 107 | 3.042 | 26 | 144 | 380.004 |
| 10-23 | 59 | 2.753 | 0 | 78 | 3.017 | 28 | 304 | 882.6 |
| 10-24 | 46 | 1.363 | 0 | 52 | 0.96 | 20 | 241 | 680.557 |

**USDC parents**

| day | routed buys | buy SOL | buys >= 0.5 SOL | routed sells | sell SOL | children with a routed buy | phantom buys | phantom buy SOL |
|---|---|---|---|---|---|---|---|---|
| 10-19 | 11910 | 6945.261 | 3859 | 9544 | 6768.495 | 724 | 0 | 0 |
| 10-20 | 14301 | 7144.725 | 4048 | 10662 | 7341.596 | 869 | 0 | 0 |
| 10-21 | 9961 | 5024.627 | 2763 | 7825 | 5133.691 | 680 | 0 | 0 |
| 10-22 | 10707 | 5571.16 | 3106 | 9456 | 5544.724 | 704 | 8 | 38.454 |
| 10-23 | 14238 | 7876.402 | 3851 | 11502 | 8522.438 | 696 | 10 | 27.081 |
| 10-24 | 7419 | 3526.089 | 1884 | 5300 | 3494.461 | 549 | 26 | 112.006 |

**pumpcoin parents**

| day | routed buys | buy SOL | buys >= 0.5 SOL | routed sells | sell SOL | children with a routed buy | phantom buys | phantom buy SOL |
|---|---|---|---|---|---|---|---|---|
| 10-19 | 20129 | 7910.101 | 4328 | 17176 | 8471.887 | 560 | 12 | 90.1 |
| 10-20 | 58717 | 28372.205 | 16159 | 50379 | 28900.142 | 1265 | 107 | 1592.754 |
| 10-21 | 132391 | 59813.146 | 31202 | 112006 | 61638.934 | 1740 | 1410 | 39625.53 |
| 10-22 | 25929 | 9082.594 | 5149 | 21024 | 9594.751 | 881 | 58 | 436.446 |
| 10-23 | 62860 | 19380.18 | 10632 | 50901 | 19801.601 | 993 | 686 | 2592.267 |
| 10-24 | 46517 | 15214.451 | 7973 | 39554 | 15688.671 | 845 | 157 | 662.551 |

**other parents**

| day | routed buys | buy SOL | buys >= 0.5 SOL | routed sells | sell SOL | children with a routed buy | phantom buys | phantom buy SOL |
|---|---|---|---|---|---|---|---|---|
| 10-19 | 5014 | 1722.946 | 1019 | 3924 | 1736.226 | 168 | 7 | 35.181 |
| 10-20 | 9434 | 3340.49 | 1888 | 8100 | 3364.322 | 251 | 5 | 67.975 |
| 10-21 | 6096 | 2097.46 | 1257 | 5220 | 2281.484 | 219 | 0 | 0 |
| 10-22 | 10756 | 3596.047 | 2033 | 8800 | 3640.688 | 294 | 1 | 2.966 |
| 10-23 | 5746 | 2440.244 | 1560 | 5359 | 2628.074 | 237 | 30 | 238.813 |
| 10-24 | 12594 | 4140.51 | 2500 | 9755 | 4059.238 | 296 | 21 | 104.246 |

**Reversal labels** (landed class). Share of routed buys whose signer sold the same child through a routed sell within h. Buys whose window passes 2026-10-25T00Z are censored and left out.

| class | day | buys (1h) | with sell 1h | buys (6h) | with sell 6h | buys (24h) | with sell 24h |
|---|---|---|---|---|---|---|---|
| PUMP | 10-19 | 22 | 8 (0.364) | 22 | 15 (0.682) | 22 | 16 (0.727) |
| PUMP | 10-20 | 60 | 24 (0.400) | 60 | 43 (0.717) | 60 | 44 (0.733) |
| PUMP | 10-21 | 154 | 109 (0.708) | 154 | 122 (0.792) | 154 | 126 (0.818) |
| PUMP | 10-22 | 134 | 44 (0.328) | 134 | 70 (0.522) | 134 | 77 (0.575) |
| PUMP | 10-23 | 59 | 42 (0.712) | 59 | 45 (0.763) | 59 | 45 (0.763) |
| PUMP | 10-24 | 42 | 14 (0.333) | 32 | 18 (0.562) | 0 | censored |
| USDC | 10-19 | 11910 | 10088 (0.847) | 11910 | 10493 (0.881) | 11910 | 10656 (0.895) |
| USDC | 10-20 | 14301 | 11903 (0.832) | 14301 | 12516 (0.875) | 14301 | 13042 (0.912) |
| USDC | 10-21 | 9961 | 8273 (0.831) | 9961 | 8683 (0.872) | 9961 | 8890 (0.892) |
| USDC | 10-22 | 10707 | 9183 (0.858) | 10707 | 9543 (0.891) | 10707 | 9648 (0.901) |
| USDC | 10-23 | 14238 | 11795 (0.828) | 14238 | 12607 (0.885) | 14238 | 12845 (0.902) |
| USDC | 10-24 | 7249 | 5888 (0.812) | 5435 | 4796 (0.882) | 0 | censored |
| pumpcoin | 10-19 | 20129 | 15034 (0.747) | 20129 | 16385 (0.814) | 20129 | 16866 (0.838) |
| pumpcoin | 10-20 | 58717 | 49827 (0.849) | 58717 | 51739 (0.881) | 58717 | 52721 (0.898) |
| pumpcoin | 10-21 | 132391 | 105846 (0.799) | 132391 | 112783 (0.852) | 132391 | 117599 (0.888) |
| pumpcoin | 10-22 | 25929 | 19493 (0.752) | 25929 | 21312 (0.822) | 25929 | 22453 (0.866) |
| pumpcoin | 10-23 | 62860 | 50013 (0.796) | 62860 | 54148 (0.861) | 62860 | 56741 (0.903) |
| pumpcoin | 10-24 | 45563 | 35735 (0.784) | 36806 | 31789 (0.864) | 0 | censored |
| other | 10-19 | 5014 | 3681 (0.734) | 5014 | 4049 (0.808) | 5014 | 4325 (0.863) |
| other | 10-20 | 9434 | 5575 (0.591) | 9434 | 6448 (0.683) | 9434 | 6909 (0.732) |
| other | 10-21 | 6096 | 4595 (0.754) | 6096 | 5024 (0.824) | 6096 | 5241 (0.860) |
| other | 10-22 | 10756 | 8312 (0.773) | 10756 | 9005 (0.837) | 10756 | 9352 (0.869) |
| other | 10-23 | 5746 | 4580 (0.797) | 5746 | 4850 (0.844) | 5746 | 5081 (0.884) |
| other | 10-24 | 12537 | 8564 (0.683) | 11167 | 9716 (0.870) | 0 | censored |

**Child cohorts** (landed class, all signers). Routed buy and sell SOL in the first h after each child's first routed buy, by the day of that first buy.

| class | day | children (1h) | buy SOL 1h | sell SOL 1h | children (6h) | buy SOL 6h | sell SOL 6h | children (24h) | buy SOL 24h | sell SOL 24h |
|---|---|---|---|---|---|---|---|---|---|---|
| PUMP | 10-19 | 6 | 1.719 | 1.102 | 6 | 1.739 | 1.174 | 6 | 1.793 | 1.275 |
| PUMP | 10-20 | 12 | 1.956 | 1.052 | 12 | 2.005 | 1.2 | 12 | 2.098 | 1.51 |
| PUMP | 10-21 | 70 | 5.088 | 12.988 | 70 | 19.663 | 22.137 | 70 | 25.62 | 30.093 |
| PUMP | 10-22 | 13 | 2.727 | 5.664 | 13 | 3.157 | 5.664 | 13 | 3.162 | 5.664 |
| PUMP | 10-23 | 21 | 10.606 | 7.432 | 21 | 10.913 | 9.275 | 21 | 10.913 | 9.276 |
| PUMP | 10-24 | 14 | 1.063 | 1.978 | 8 | 1.428 | 0.475 | 0 | censored | censored |
| USDC | 10-19 | 713 | 5807.951 | 5223.962 | 713 | 7177.827 | 6888.76 | 713 | 8106.278 | 7935.531 |
| USDC | 10-20 | 799 | 5477.46 | 5142.241 | 799 | 5951.939 | 5724.891 | 799 | 6295.743 | 6130.412 |
| USDC | 10-21 | 595 | 3889.491 | 3658.479 | 595 | 4880.836 | 4887.884 | 595 | 5255.444 | 5444.053 |
| USDC | 10-22 | 622 | 4406.074 | 4083.993 | 622 | 5260.505 | 5089.797 | 622 | 5496.644 | 5314.449 |
| USDC | 10-23 | 610 | 5805.25 | 6381.389 | 610 | 6974.464 | 7768.324 | 610 | 7708.892 | 8514.092 |
| USDC | 10-24 | 443 | 2336.721 | 2197.335 | 353 | 2048.294 | 2010.978 | 0 | censored | censored |
| pumpcoin | 10-19 | 516 | 6800.095 | 6869.709 | 516 | 7783.65 | 7995.09 | 516 | 8165.684 | 8436.087 |
| pumpcoin | 10-20 | 1106 | 34650.407 | 34437.183 | 1106 | 39550.11 | 39926.2 | 1106 | 40892.736 | 41475.955 |
| pumpcoin | 10-21 | 1430 | 40384.63 | 40200.064 | 1430 | 43982.907 | 44037.144 | 1430 | 47167.888 | 47376.724 |
| pumpcoin | 10-22 | 555 | 6980.103 | 6943.39 | 555 | 9486.793 | 9419.196 | 555 | 10695.306 | 10815.774 |
| pumpcoin | 10-23 | 658 | 13383.543 | 12876.78 | 658 | 14967.112 | 14590.084 | 658 | 16094.712 | 15984.862 |
| pumpcoin | 10-24 | 515 | 10319.232 | 10241.726 | 387 | 9873.777 | 9989.884 | 0 | censored | censored |
| other | 10-19 | 137 | 1420.915 | 1317.365 | 137 | 1659.463 | 1590.846 | 137 | 1889.289 | 1853.504 |
| other | 10-20 | 156 | 2100.043 | 1796.364 | 156 | 3007.997 | 2351.212 | 156 | 3263.164 | 2552.71 |
| other | 10-21 | 131 | 1441.1 | 1449.933 | 131 | 1524.506 | 1560.876 | 131 | 1841.844 | 1916.052 |
| other | 10-22 | 176 | 2544.587 | 2446.352 | 176 | 3415.281 | 3263.6 | 176 | 3582.592 | 3454.171 |
| other | 10-23 | 136 | 1541.197 | 1590.949 | 136 | 1680.89 | 1723.647 | 136 | 2517.611 | 2550.042 |
| other | 10-24 | 170 | 2987.775 | 2896.394 | 143 | 3166.73 | 3090.508 | 0 | censored | censored |

**Top 15 parents by landed routed buy SOL.** Each cell: routed buy SOL / all landed buy SOL in the parent's WSOL PumpSwap pools that day (share); then routed sell SOL. A dash means no routed buy that day.

| parent | 10-19 | 10-20 | 10-21 | 10-22 | 10-23 | 10-24 |
|---|---|---|---|---|---|---|
| `2qgnTgJb...` | - | 3799.367 / 10392.009 (0.366); sell 4063.42 | 179.488 / 961.853 (0.187); sell 156.499 | 4.648 / 95.565 (0.049); sell 12.655 | 0.362 / 11.976 (0.030); sell 0.756 | 0.003 / 8.249 (0.000); sell 0.894 |
| `5tCju6YN...` | 303.239 / 1096.363 (0.277); sell 316.436 | 7.004 / 1078.092 (0.006); sell 22.281 | 267.878 / 1653.22 (0.162); sell 302.053 | 63.639 / 1170.817 (0.054); sell 48.838 | 15.373 / 950.829 (0.016); sell 41.311 | 942.847 / 8384.923 (0.112); sell 1018.235 |
| `6KEiPuRt...` | - | 1499.037 / 11482.818 (0.131); sell 1417.836 | 0.115 / 411.57 (0.000); sell 8.559 | 1.49 / 178.454 (0.008); sell 5.481 | 0.125 / 253.762 (0.000); sell 0.102 | - |
| `8idEA8Qo...` | - | - | - | 1783.468 / 13882.093 (0.128); sell 1702.226 | 6943.329 / 29598.024 (0.235); sell 7057.046 | 72.064 / 2703.748 (0.027); sell 98.503 |
| `9cRCn9rG...` | 22.35 / 1500.648 (0.015); sell 33.5 | 9.817 / 11336.6 (0.001); sell 23.53 | 450.526 / 4871.425 (0.092); sell 504.833 | 1258.755 / 5853.37 (0.215); sell 1439.357 | 1117.148 / 8551.497 (0.131); sell 1078.727 | 162.488 / 9426.539 (0.017); sell 168.401 |
| `CMo7GfUh...` | - | - | 2325.867 / 30317.74 (0.077); sell 2305.981 | 496.412 / 9213.927 (0.054); sell 504.162 | 17.049 / 1322.017 (0.013); sell 16.401 | 0.725 / 362.759 (0.002); sell 6.695 |
| `CSocnzcR...` | - | 16342.173 / 97346.534 (0.168); sell 16207.549 | 51510.299 / 160399.838 (0.321); sell 52996.713 | 1811.168 / 56517.184 (0.032); sell 2017.045 | 442.878 / 12550.044 (0.035); sell 490.892 | 108.598 / 4143.72 (0.026); sell 97.223 |
| `Ce2gx9KG...` | 1513.892 / 2923.777 (0.518); sell 1509.808 | 372.544 / 1080.971 (0.345); sell 396.12 | 36.762 / 1157.387 (0.032); sell 43.325 | 65.193 / 1347.797 (0.048); sell 56.238 | 44.673 / 1832.935 (0.024); sell 64.261 | 910.524 / 2454.746 (0.371); sell 997.904 |
| `CicoFLHq...` | - | 2575.858 / 12534.669 (0.205); sell 2304.118 | 4.58 / 778.251 (0.006); sell 9.402 | 0.28 / 51.168 (0.005); sell 0.639 | 0.814 / 5.289 (0.154); sell 0.243 | 0.343 / 20.338 (0.017); sell 0.828 |
| `EPjFWdd5...` | 6945.261 / 12039.333 (0.577); sell 6768.495 | 7144.725 / 14599.515 (0.489); sell 7341.596 | 5024.627 / 11676.851 (0.430); sell 5133.691 | 5571.16 / 10863.778 (0.513); sell 5544.724 | 7876.402 / 12703.633 (0.620); sell 8522.438 | 3526.089 / 7315.525 (0.482); sell 3494.461 |
| `EU1MHvQv...` | - | - | - | 140.777 / 9374.007 (0.015); sell 111.548 | 3216.305 / 16635.207 (0.193); sell 3184.205 | 275.251 / 2520.224 (0.109); sell 314.494 |
| `FUMMypMw...` | - | - | - | - | - | 2332.258 / 12632.591 (0.185); sell 2318.013 |
| `H74CYmXg...` | 7.39 / 42.744 (0.173); sell 8.642 | 58.614 / 147.417 (0.398); sell 56.842 | 113.342 / 155.908 (0.727); sell 112.374 | 922.405 / 1261.552 (0.731); sell 904.659 | 298.937 / 495.046 (0.604); sell 367.493 | 56.42 / 229.691 (0.246); sell 41.555 |
| `J8PSdNP3...` | 301.932 / 651.915 (0.463); sell 313.076 | 6.377 / 1689.87 (0.004); sell 8.502 | 75.467 / 1598.998 (0.047); sell 98.639 | 19.765 / 556.852 (0.035); sell 18.712 | 55.607 / 869.472 (0.064); sell 89.494 | 3625.214 / 5358.107 (0.677); sell 3835.687 |
| `NTRNDMqQ...` | 1455.075 / 2856.989 (0.509); sell 1384.268 | 1497.882 / 2612.889 (0.573); sell 1501.712 | 750.789 / 1593.246 (0.471); sell 797.488 | 1077.557 / 1987.752 (0.542); sell 1069.571 | 674.513 / 1730.566 (0.390); sell 705.126 | 1470.148 / 2831.335 (0.519); sell 1397.294 |

PUMP's WSOL PumpSwap pools, all signers, landed class buy SOL per day: 10-19 19.427, 10-20 13.442, 10-21 12.744, 10-22 9.799, 10-23 8.859, 10-24 3.146; phantom class buy SOL: 10-19 40.739, 10-20 85.804, 10-21 148.658, 10-22 380.004, 10-23 923.42, 10-24 724.846.

Reading the tables:
- **Net flow.** Across all pump-coin parents, landed routed buys minus routed sells was -561.8 / -527.9 / -1,825.8 / -512.2 / -421.4 / -474.2 SOL per day (inline count on `p48_sigs.jsonl`, landed class). The gross routed buy behind those figures was 7,910.1 / 28,372.2 / 59,813.1 / 9,082.6 / 19,380.2 / 15,214.5 SOL.
- **Reversal.** Share of routed buys with a same-signer routed sell of the same child, by class and day (min to max):
  - USDC and pump-coin parents: 0.747 to 0.858 within 1 h, 0.814 to 0.891 within 6 h, 0.838 to 0.912 within 24 h;
  - other quotes: from 0.591 within 1 h;
  - PUMP (tiny counts): 0.328 to 0.712 within 1 h.
- **Child cohorts.** In the first hour after a child's first routed buy, routed sell SOL is within about 15% of routed buy SOL in every class with volume. Examples: pump-coin 10-21 40,384.63 buy vs 40,200.064 sell; USDC 10-19 5,807.951 vs 5,223.962; other 10-20 2,100.043 vs 1,796.364.
- **Daily parent-pool share** (landed routed buy SOL over all landed buy SOL in the parent's WSOL PumpSwap pools): 0.000 to 0.731 by parent and day (table above). It is high on a parent's hot day and decays within one to two days.
- `legs_side_mismatch` counts legs whose side differs from every child row in the transaction. They are kept in the totals and are 0 to 1,829 per class and day (`data/p48_counts.json`).

## 3. Which parents

- **Pump-coin parents.** 358 / 499 / 560 / 583 / 549 / 509 parents had any landed routed buy, by day. 18 / 13 / 18 / 13 / 15 / 26 took >= 100 SOL, and 3 / 7 / 3 / 5 / 10 / 8 took >= 500 SOL. New entrants at >= 500 SOL (under 100 SOL on every earlier tape day) were 3 / 7 / 2 / 2 / 7 / 4.
- **Child creates by quote class** (`creates` in `p48_counts.json`):
  - PUMP 3,236 / 4,130 / 5,003 / 5,126 / 4,990 / 4,178;
  - USDC 413 / 506 / 454 / 344 / 496 / 466;
  - pump-coin quotes 712 / 1,971 / 2,359 / 1,030 / 1,319 / 1,309;
  - other quotes (PreStocks, xStocks and others) 3,306 / 5,392 / 3,526 / 4,556 / 3,843 / 3,715.
  - 1,642 distinct quote mints over the six days.
- **Concentration** (top signer share of landed routed buy SOL): PUMP 0.37 to 0.79 (`Cbc8F5d2...` on 5 of 6 days, 8 to 40 signers). USDC, pump-coin and other parents 0.006 to 0.061 (1,705 to 30,539 signers). The landed flow is many actors. The phantom flow is one.

## 4. Slow structure, capacity screen, kill rules

**What the counts rule out.** The idea was a predictable parent buy pressure while a child is live. Counts show that pressure is matched by routed sells from the same children within the hour, net negative by day. So a parent-drift ride has no counted net flow to stand on. Front-running the forced buy is impossible: it sits inside the child buyer's own transaction (scout §4).

**What is left.** A "parent of the day" schedule. A parent turns into a popular quote for one or two days: new >= 500 SOL parents are 2 to 7 a day, and child creates per parent spike (`CSocnzcR`: 447 / 1,153 / 80 / 22 / 4 creates over 10-20..10-24). Whether the parent's own pool drifts after its child-create rate first crosses a bar is a **price** question, not answered here.

**Capacity (arithmetic, not a result).** Take N = 2 to 7 new hot parents a day, one trade each, at 0.25 SOL. At a net edge e after costs of +1% / +2% / +5%, that is 0.005 to 0.0175 / 0.01 to 0.035 / 0.025 to 0.0875 SOL a day. Even at e = +5% and N = 7, it is under 0.09 SOL a day. Impact on the thinner parents (the 100 to 500 SOL/day tier) would cut e further (feedback-capacity-first).

**Data for a decision.**
- No pre-10-08 exploration block has pump-coin quotes (feature from about 2026-10-08 per the library).
- The September blocks have custom pairs only from 09-10 (xStocks and the like), with no pump-coin parents.
- The October archive is under the D2/D3 domain seals.
- The arena tape is walk-2 and in-sample, and it carries phantom rows.
- **So only a forward test, or an October domain release by the owner, could decide.**

**Kill rules, fixed now** (2026-10-26, before any price is seen), for any later look at a parent-of-the-day drift rule:
- K0 (data): every trigger input must come from landed rows. Phantom signers are filtered, plus a sampled `getSignatureStatuses` check of >= 20 trigger transactions per day with >= 19 found. Fail means no look.
- K1 (rate): >= 3 triggers a day, landed class, on >= 4 of 5 declared days. Fail means dead.
- K2 (counts before price): the median net routed flow (buy minus sell SOL, landed) into the parent in the 6 h after the trigger must be > 0 on the declared days. Fail means dead before pricing.
- K3 (price, after a registration and QP): at 0.25 SOL with honest fees and the 15% fail model, dead if any of these hold: mean SOL/trade <= 0; total after removing the top 3 trades <= 0; total after removing the best day <= 0.
- K4 (capacity): dead if the trades/day x 0.25 SOL x the measured net edge is under 0.05 SOL a day.
- K5 (seals): no October archive hour is used without the ledger's domain release. No arena hour >= 2026-10-25T00Z is used as anything but forward-in-clock data under its own registration.

## What was read and what was not

- **Read:**
  - arena tape hours 2026-10-19T08..2026-10-24T23 (path guard in `p48_extract.py`; creates, trades, pool_create and complete rows; reserves only for the landing marker);
  - P44's SWEEP.md, `p44_count.py` and `p44_counts.json` on `origin/claude/p44-revive-sweep`;
  - the library rows `lib-c-J3`, `hunt23-w3-qsink-1` and `hunt23-w3-pump-sink-1`;
  - `tools/arena_feed.py` (two lines);
  - the docs/HOLDOUT_LEDGER.md arena rows;
  - the web (2 searches).
- **Public RPC** `api.mainnet-beta.solana.com`, **0 Helius**: 85 call attempts (73 answered, 10 retried after errors, 2 unlogged probes). That is 50 getSignatureStatuses, 19 getTransaction, 3 getSignaturesForAddress, 1 getAccountInfo and 1 getSlot answered or tried, under the 1,000 cap.
- **Not read:**
  - any tape hour at or after 2026-10-25T00Z;
  - HV4 or Q50 outputs, status or logs;
  - arena book ledgers, `bots.json` or any reads/ dir;
  - DBC tape or fwdv1;
  - meta-capture;
  - any G0 trigger, BOOST test or H5 outcome.
- **Not done:** no price, return, fill or P&L. No check of whether past arena counts or books used phantom rows.

## Reproduce

```
D=/data/mal/hunt-1008/push-1016/p48-qsink-regime
python3 -I p48_extract.py pass1 $D && python3 -I p48_extract.py pass2 $D
python3 -I p48_group.py $D
# p48_parents_top.txt: parents with >= 500 SOL routed buys on the raw tape, plus PUMP and USDC (inline step, see COUNTS.md Method)
python3 -I p48_extract.py pass4 $D && python3 -I p48_analyze.py $D $D/p48_counts.json
```
