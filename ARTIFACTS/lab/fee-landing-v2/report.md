# Fee landing study v2

**Observational only. Only a live A/B can answer whether a lower fee lands us as early. Any fee change needs a DEC-019 amendment signed off by Vaan and Helm.**

Days: 2026-08-26, 2026-08-27. Counts: {"migrations": 2367, "buys": 28623, "first_buys": 26983, "first_buys_no_tip": 20591, "slots_fetched": 37912, "slots_missing": 0, "slots_skipped": 40}.

Tip accounts listed: {"jito": {"n_accounts": 8, "source": "https://docs.jito.wtf/lowlatencytxnsend/"}, "helius_sender": {"n_accounts": 10, "source": "https://www.helius.dev/docs/sending-transactions/sender"}}. NOT listed (no sourced address): nozomi_temporal, bloxroute, 0slot, nextblock. a tip paid through an unlisted service is not detected, so no-tip is an upper bound on truly tip-free buys.

The DECIDING read is the no-tip rows of (b) and (c). With-tip and all-buys rows are context only. Fees are our-equivalent (cu_price x our CU limit / 1e6) except where marked raw.

Fetch status: {"state": "complete", "torn_bytes_removed": 0, "fetched": 37912, "resumed": 0, "skipped_slots": 40, "errors": 0, "credits_used": 37912}

## (a) Fee by landing offset k

| k | n | with tip | our-eq p10/p50/p90 | no-tip our-eq p10/p50/p90 | raw lamports p10/p50/p90 |
|---|---|---|---|---|---|
| 0 | 989 | 152 | 1,640/10,000/1,021,937 | 1,083/10,000/332,415 | 1,001/4,600/1,100,000 |
| 1 | 1711 | 222 | 10,000/56,170/1,388,889 | 10,000/56,170/375,000 | 4,600/50,778/1,000,000 |
| 2 | 1884 | 278 | 12,500/56,170/375,000 | 22,984/56,170/291,384 | 20,778/50,778/309,912 |
| 3 | 1783 | 308 | 10,000/56,170/625,000 | 10,000/56,170/375,000 | 4,600/50,778/636,000 |
| 4 | 2050 | 527 | 10,000/56,170/1,000,000 | 10,000/56,170/500,000 | 4,600/50,778/1,000,000 |
| 5 | 2007 | 475 | 10,000/56,170/641,387 | 10,000/50,000/500,000 | 5,000/50,778/599,999 |
| 6 | 1883 | 414 | 10,000/52,500/833,334 | 10,000/29,167/625,000 | 5,910/50,000/1,000,000 |
| 7 | 1696 | 414 | 10,000/41,667/833,334 | 10,000/25,000/833,334 | 10,000/45,000/1,000,000 |
| 8 | 1767 | 625 | 10,000/37,500/833,334 | 10,000/41,667/833,334 | 10,000/30,000/1,000,000 |
| 9 | 1326 | 372 | 10,000/83,334/960,577 | 10,000/83,334/1,091,475 | 10,000/100,000/1,000,000 |
| 10 | 1205 | 394 | 8,334/109,148/1,091,382 | 8,654/109,145/1,666,667 | 10,000/100,000/1,000,000 |
| 11 | 1168 | 377 | 8,334/109,150/1,091,303 | 8,334/109,141/1,091,536 | 5,000/100,000/1,000,000 |
| 12 | 1192 | 404 | 8,334/109,148/963,385 | 8,334/109,126/1,545,334 | 10,000/100,000/1,000,000 |
| 13 | 1240 | 404 | 8,334/109,150/973,324 | 8,334/109,143/1,091,525 | 10,000/100,000/1,000,000 |
| 14 | 1246 | 360 | 5,682/83,334/833,334 | 5,682/83,334/833,334 | 5,000/100,000/1,000,000 |
| 15 | 1234 | 348 | 8,334/83,334/833,334 | 8,334/83,334/1,091,189 | 10,000/100,000/1,000,000 |
| 16 | 1268 | 353 | 7,397/109,150/861,110 | 5,682/109,156/1,091,377 | 5,000/100,000/1,000,000 |

## (b) Buys landing at k <= 6: share with our-equivalent fee at most

- no_tip (DECIDING): n = 9931; <=150k: 0.802; <=250k: 0.829; <=500k: 0.917
- with_tip (context only): n = 2376; <=150k: 0.611; <=250k: 0.695; <=500k: 0.745
- all (context only): n = 12307; <=150k: 0.765; <=250k: 0.803; <=500k: 0.883

## (c) P(k <= 6 | our-equivalent fee bucket), first buy per wallet per pool, with late landers (k 9..16)

### no_tip (DECIDING)

| bucket | n | k<=6 | k 7-8 | late k 9-16 | P(k<=6) [90% CI] | late share [90% CI] |
|---|---|---|---|---|---|---|
| le150k | 13049 | 7838 | 1618 | 3593 | 0.601 [0.587, 0.613] | 0.275 [0.264, 0.289] |
| 150k_250k | 982 | 247 | 114 | 621 | 0.252 [0.225, 0.279] | 0.632 [0.602, 0.662] |
| 250k_500k | 1725 | 851 | 251 | 623 | 0.493 [0.470, 0.518] | 0.361 [0.337, 0.383] |
| gt500k | 2332 | 796 | 308 | 1228 | 0.341 [0.317, 0.364] | 0.527 [0.503, 0.551] |

### with_tip (context only)

| bucket | n | k<=6 | k 7-8 | late k 9-16 | P(k<=6) [90% CI] | late share [90% CI] |
|---|---|---|---|---|---|---|
| le150k | 3813 | 1435 | 685 | 1693 | 0.376 [0.358, 0.394] | 0.444 [0.428, 0.462] |
| 150k_250k | 482 | 200 | 107 | 175 | 0.415 [0.377, 0.454] | 0.363 [0.328, 0.398] |
| 250k_500k | 236 | 111 | 11 | 114 | 0.470 [0.415, 0.525] | 0.483 [0.431, 0.538] |
| gt500k | 1445 | 599 | 198 | 648 | 0.415 [0.386, 0.443] | 0.448 [0.420, 0.475] |

### all (context only)

| bucket | n | k<=6 | k 7-8 | late k 9-16 | P(k<=6) [90% CI] | late share [90% CI] |
|---|---|---|---|---|---|---|
| le150k | 16862 | 9273 | 2303 | 5286 | 0.550 [0.537, 0.563] | 0.313 [0.301, 0.326] |
| 150k_250k | 1464 | 447 | 221 | 796 | 0.305 [0.282, 0.328] | 0.544 [0.520, 0.569] |
| 250k_500k | 1961 | 962 | 262 | 737 | 0.491 [0.469, 0.513] | 0.376 [0.355, 0.397] |
| gt500k | 3777 | 1395 | 506 | 1876 | 0.369 [0.350, 0.391] | 0.497 [0.477, 0.517] |

## (d) Fee per CU vs position inside a slot

- no_tip: slots with 2+ buys 4541, unequal-price pairs 11083, share with higher cu_price earlier 0.614, mean Spearman 0.206
- all: slots with 2+ buys 6316, unequal-price pairs 20961, share with higher cu_price earlier 0.602, mean Spearman 0.198

## (e) Buys with no ComputeBudget price (excluded from the fee buckets and from (d))

buys 2974, first buys 2919 (k<=6: 2177, late k 9-16: 568), tipped share (buys) 0.145, (first buys) 0.143. k distribution of buys: {"0": 291, "1": 455, "2": 364, "3": 359, "4": 358, "5": 211, "6": 149, "7": 88, "8": 91, "9": 75, "10": 75, "11": 67, "12": 54, "13": 63, "14": 78, "15": 91, "16": 105}
