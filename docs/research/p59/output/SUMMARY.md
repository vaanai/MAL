| variant | grp | model | n | n/cal-day | mean % | delta vs base pp | total SOL | ex-top3 | ex-best-day | CI90 lo trade % | pos/days |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base | explore-0814 | flat | 307 | 21.93 | 6.4857 | +0.0000 | 4.9778 | 4.0532 | 4.0555 | 4.533 | 14/14 |
| base | explore-0814 | press | 307 | 21.93 | 5.1757 | +0.0000 | 3.9724 | 3.1950 | 3.1982 | 3.400 | 14/14 |
| base | september | flat | 25 | 2.27 | 5.6212 | +0.0000 | 0.3513 | 0.0004 | 0.2265 | 0.528 | 4/5 |
| base | september | press | 25 | 2.27 | 4.7240 | +0.0000 | 0.2952 | 0.0015 | 0.1912 | 0.470 | 4/5 |
| a40 | explore-0814 | flat | 651 | 46.50 | -0.1572 | -6.6429 | -0.2558 | -0.9749 | -0.5777 | -0.817 | 5/14 |
| a40 | explore-0814 | press | 651 | 46.50 | -0.2094 | -5.3851 | -0.3408 | -0.9095 | -0.6086 | -0.749 | 5/14 |
| a40 | september | flat | 135 | 12.27 | -2.1287 | -7.7500 | -0.7185 | -0.8497 | -0.6686 | -2.618 | 0/7 |
| a40 | september | press | 135 | 12.27 | -1.7964 | -6.5203 | -0.6063 | -0.7163 | -0.5633 | -2.204 | 0/7 |
| guardoff | explore-0814 | flat | 307 | 21.93 | 6.3857 | -0.1000 | 4.9010 | 3.9764 | 3.9787 | 4.431 | 14/14 |
| guardoff | explore-0814 | press | 307 | 21.93 | 5.0914 | -0.0843 | 3.9077 | 3.1303 | 3.1336 | 3.301 | 14/14 |
| guardoff | september | flat | 25 | 2.27 | 5.6212 | +0.0000 | 0.3513 | 0.0004 | 0.2265 | 0.528 | 4/5 |
| guardoff | september | press | 25 | 2.27 | 4.7238 | -0.0001 | 0.2952 | 0.0015 | 0.1912 | 0.470 | 4/5 |

Pooled (all four blocks): base: n 332, exits {'time': 297, 'tp': 35, 'sl': 0, 'trail': 0}, flat mean 6.4206%, press 5.1417%; a40: n 786, exits {'time': 776, 'tp': 10, 'sl': 0, 'trail': 0}, flat mean -0.4958%, press -0.4820%; guardoff: n 332, exits {'time': 297, 'tp': 35, 'sl': 0, 'trail': 0}, flat mean 6.3281%, press 5.0637%

- A40 line 1 delta>=+1.0pp both models (conf): FAIL
- A40 line 2 ex-top3 >0 both (conf): FAIL
- A40 line 3 ex-best-day >0 both (conf): FAIL
- A40 line 4 attempts/day >=4.0 conf and disc: PASS
- A40 line 4b (info) base conf attempts/day >=4.0: FAIL
- A40 line 5 discovery delta >0 both: FAIL
- A40 verdict: DROP
- A41 verdict: DROP (redundant: the engine's GUARD=1.15 is the base). Delta vs base is 0 by construction; no separate run (a40/base/guardoff only). guard-off ablation above is diagnostic.
