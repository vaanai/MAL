# COUNTS (l1_floor_option_v2): count-only, no price read

Triggers after the universe and pool-age refusals; `after overlap` applies the time-only one-open-position rule (an upper bound of attempts). RULES step 2 counts on explore-0814 ALONE (a cell under 20 attempts/day there is dropped WITHOUT pricing; the author drops it, the kit only flags it, as does the C1-NF drop).

| cell | triggers | after overlap | days>=1 | explore-0814 att/day (upper bound) | step 2 | 08-08 | 08-09 | 08-10 | 08-11 | 08-12 | 08-13 | 08-14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| l1 | 21080 | 93 | 6 | na | na | 0 | 1 | 6 | 18 | 26 | 27 | 15 |
