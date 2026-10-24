# P14 Meteora DBC screens (2026-10-24)

Screen numbers on 3 exploration days (2026-10-14..16, 72 hours), not evidence. Holdout hours 2026-10-17T00..10-24T00 never opened (code refuses them). Three days cannot meet the
5-day criterion, so no cell can be a gate candidate. No sentence here says a strategy makes money. IDEAS.md and DECLARE.md (sha256 in SHA256SUMS.txt; "Declared at 2026-10-24T03:58:38Z")
were written before any attempt count, capacity count or price. Jobs: #1479 (validate, counts, price), #1483 (same prices, plus diagnostics added after the first look).

## Pricer (tools/dbc_price.py, tools/dbc_screen.py)
- Constant-liquidity sqrt-price curve. L is not on the tape; it is implied per trade from the quote leg and carried per pool. Per-trade fees from the decoded events; fee side (input or output)
  inferred per config (385 of 1,243 configs with evidence take the fee on input). Own impact, own buy re-injected at exit, guard 1.15, quote room to migration_threshold, 2 x 160,000 sends,
  rent 0 (ATA assumed refunded; rent-charged leg reported), flat 15% and pressure fail models.
- Finding: tape order inside a slot is NOT execution order (628,487 multi-row (pool, slot) groups; 406,174 re-ordered by the S0 = previous S1 chain; 28,616 would not chain, 108,990 rows flagged bad
  and never used as signal or landing state). Without the fix only ~30% of rows reproduced.
- Validation (predict each trade's amount_out from its pre-state and the carried L, 2.09M rows excluding bad): buys 98.2% within 1 bp, 99.4% within 1%; sells 93.8% within 1 bp, 97.4% within 1%.
  Sells are worse (segment changes, stale L). The validation uses the row's own fee rate and its L comes from earlier rows, but the order fix used L from isolated rows including some of the validated rows (mild leak, not removed).
- NOT validated: Swap3 / transfer-hook / exact-out. 2,678 pools with a hook or Swap3 row are excluded from signals; they carry most of 10-16 volume (tradable rows 117k of 752k on 10-16 vs ~93% on 10-14/15), so day 3 is thin.
- Tests: tools/test_dbc_price.py (real rows fixture, 5 tests, run as `python3 -m tools.test_dbc_price`; pytest not installed on the host).

## Fee actually paid
All SOL rows: 25 bps on 2.06M of 2.23M rows, 0 bps 84k, 100 bps 56k, 200 bps 8k, 300 bps 6k; 3.7% of rows above 100 bps. At OUR priced trades (young, hot pools) the fee is higher:
mean round trip 84-195 bps by cell (m10 87: buy mean 52, sell mean 35; bb2 180; c3 84). Buy fee p90 100-300 bps and p99 about 400 bps; the share of trips with round-trip >= 100 bps is 19-86% by cell.
So the "0.5% round trip" hypothesis holds only for the median trade, not for the trades these rules pick.

## Capacity (counts, no price)
Rows (SOL-quoted trades, tradable pools) where the post-state lets a 0.25 SOL buy move the spot by <= 2% (own fee included), per UTC day:
| day | all rows | tradable rows | impact <= 2% rows (buys) | universe rows (also p <= 0.85, not completed, thr >= 5 SOL) | pools with such a state |
|---|---|---|---|---|---|
| 10-14 | 790,538 | 737,633 | 226,064 (110,510) | 220,596 | 1,370 |
| 10-15 | 689,679 | 650,243 | 194,293 (99,220) | 194,292 | 1,176 |
| 10-16 | 751,794 | 117,056 | 48,100 (23,687) | 48,100 | 211 |
Median 0.25 SOL impact over tradable rows is 1.9%; 52% of rows are at or under 2%. By threshold band (rows, of which <= 2%): thr<20 SOL 1,376,023 (673,582); 20-60 SOL 20,622 (19,001); >= 60 SOL 91,721 (86,899).

## Screens (deciding leg: 1.9 s, 0.25 SOL, rent 0, 160k sends; SOL per trip; three days)
Attempts after cooldown. "strand" = the curve completed before the sell could land; priced as a full loss (stake + buy send + rent). "opt" = the same attempt with the strand sold at the last on-curve price instead
(OPTIMISTIC: ignores the migration, the new pool and any latency; shown only as a bracket). pos days = flat model.
| cell | n | /day | fills | strand | flat mean | pressure mean | ex-top-3 (flat) | pos days | flat mean opt | label |
|---|---|---|---|---|---|---|---|---|---|---|
| m4 | 740 | 247 | 589 | 194 | -0.0341 | -0.0324 | -28.6 | 0/3 | +0.0312 | FAIL |
| m6 | 653 | 218 | 519 | 193 | -0.0406 | -0.0386 | -29.4 | 0/3 | +0.0321 | FAIL |
| m10 | 369 | 123 | 243 | 5 | +0.0218 | +0.0169 | +5.4 | 3/3 | +0.0358 | MARGINAL |
| ms30 | 442 | 147 | 358 | 230 | -0.1095 | -0.0960 | -49.1 | 0/3 | +0.0112 | FAIL |
| ms50 | 420 | 140 | 379 | 243 | -0.1221 | -0.1082 | -51.6 | 0/3 | +0.0095 | FAIL |
| ms70 | 865 | 288 | 820 | 642 | -0.1618 | -0.1364 | -140.2 | 0/3 | +0.0094 | FAIL |
| bb1 | 512 | 171 | 350 | 38 | -0.0043 | -0.0024 | -5.2 | 2/3 | +0.0269 | FAIL |
| bb2 | 173 | 58 | 112 | 1 | -0.0003 | -0.0004 | -0.8 | 2/3 | +0.0010 | FAIL |
| bb4 | 25 | 8 | 14 | 0 | +0.0007 | +0.0008 | -0.3 | 1/3 | +0.0007 | FAIL |
| c3 | 1097 | 366 | 989 | 75 | -0.0091 | -0.0079 | -10.8 | 1/3 | +0.0117 | FAIL |
| c4 | 735 | 245 | 635 | 23 | -0.0043 | -0.0048 | -3.8 | 1/3 | +0.0064 | FAIL |
| c6 | 522 | 174 | 447 | 6 | +0.0006 | -0.0016 | -0.4 | 2/3 | +0.0038 | FAIL |
m10 detail: flat total +8.05 SOL over 243 fills, per day +4.36 / +2.92 / +0.77; trade CI90 lo +0.0117, date CI90 lo +0.0204 (3 dates); ex-best-pool +7.03, best 3 pools +2.61; 1.0 SOL stake +0.0688 per trip;
rent charged +0.0207; exit lag 3.4 s +0.0299. All 50 attempts in 20-60 SOL-threshold pools average +0.068, the 319 in >= 60 SOL pools +0.015; there are none in < 20 SOL pools (the 10-13 SOL pools carry the strand losses in m4/m6/ms*).
Fill model notes: no haircut applied (none measured); the fee used for a fill is the pool's last observed same-side rate, which for a decaying fee scheduler is an approximation in either direction; 19-86% of trips pay >= 100 bps round trip.

## Reading (bounded)
- The verdict for ms30/50/70 and m4/m6 is decided by the strand assumption, not by the rule: full loss gives -0.03 to -0.16 per trip, last-price gives about +0.01 to +0.03. The tape cannot settle it (no DAMM v2 swaps): D5.
- m10 survives the declared label under the pessimistic strand rule too, because it fires in large-threshold pools. It is one of 12 cells on 3 days of one regime (winner's curse), its trade count of 243 fills is a screen, and it
  has not met any gate criterion. If the manager wants it carried, it needs a frozen definition and a read on the reserved hours once.
- bb and c cells: nothing, fee is not what kills them (the trades selected are young-pool trades at 150-195 bps round trip).

## Not done / disclosure
- D5 (post-completion hold) not priced: needs migrate tx -> DAMM v2 pool address -> per-pool history + decoder; probe 5 pools first (<= ~5,000 credits assumed). No Helius credits were used by this lane (tape already on disk).
- Smoke runs on subsets (counts on 10-14T00..06, one m10 price on 10-14T00..05, validation on 10-14T00..03 and T12-13) preceded the full run; the full run was priced once on all 72 hours (#1479); #1483 re-ran
  it identically with added diagnostics (concentration, strand-at-last-price), which were added after seeing #1479 and change no label.
- The hook-pool exclusion removes most of 10-16; hook pools themselves are unpriced.
- Not reviewed by quant-proof or reviewer.
