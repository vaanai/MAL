# COUNTS (l1_floor_option_v2): count-only, no price read

Triggers after the universe and pool-age refusals; `after overlap` applies the time-only one-open-position rule (an upper bound of attempts). RULES step 2 counts on explore-0814 ALONE (a cell under 20 attempts/day there is dropped WITHOUT pricing; the author drops it, the kit only flags it, as does the C1-NF drop).

| cell | triggers | after overlap | days>=1 | explore-0814 att/day (upper bound) | step 2 | 09-03 | 09-04 | 09-05 | 09-06 | 09-07 | 09-08 | 09-09 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| l1 | 19527 | 83 | 6 | na | na | 0 | 4 | 14 | 13 | 17 | 21 | 14 |
