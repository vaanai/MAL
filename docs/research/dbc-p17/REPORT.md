# P17 DBC "hook pools" (2026-10-24)

Screen numbers on 7 disclosed-exploration days (2026-10-17..10-23; the m10 holdout is SPENT). Not evidence. No sentence says a strategy makes money.

## 1. What a "hook pool" is
- It is NOT a hook. From about 2026-10-16T04Z the DBC program emits **EvtSwap3** (the new generic swap event) for every swap; Swap2 events stop. Swap3 adds `fee_on_base_token`, `swap_mode`, and
  included/excluded transfer-fee amounts. P14's `hook_row` flagged any EvtSwap3 as a hook, so 100% of 10-17..10-23 rows (4.74M SOL-quoted rows) were "hook pools".
- Real transfer-hook rows (event/ix `...WithTransferHook`): 222 of 18,754 pools (1.2%) on 10-17..23. Their configs are `ConfigWithTransferHook` (290 of 7,399 configs): Token-2022 base mint plus one
  of at least 9 custom hook programs. Hook swaps cost more compute and accounts: 30 sampled txs median 83,404 CU / 26 account keys vs 57,915 CU / 17 keys for 30 non-hook swaps (priority fee
  dominates the SOL fee; hook median tx fee 72,703 vs 5,514 lamports reflects the senders' priority choices, not a protocol fee). The hook program ran in 30/30 sampled hook txs, none failed
  (failed txs are absent from the tape, so a hook that blocks sells would be invisible: hook pools stay EXCLUDED and unpriced).
- Transfer fee: 42 of 7,399 configs carry `transfer_fee_basis_points` > 0 (Token-2022 transfer fee); all sampled swap rows have included == excluded amounts. Those pools are excluded (not modelled).

## 2. Validation (tests: tools/test_dbc_swap3.py, fixture tools/data/dbc_swap3_fixture.json = 110 real trades incl. 30 hook-config trades)
- Vault deltas equal event amounts to the atom (buy: quote vault +amount_in, base vault -amount_out; sell mirrored); fees stay in the vault.
- Integer-exact reproduction from the config curve (account decoded from the IDL, tools/dbc_config.py): BUY S1 = S0 + (q<<128)//L, base_gross = L(S1-S0)//(S0 S1), quote = ceil(L(S1-S0)/2^128);
  SELL S1 = ceil(L S0/(L+b S0)), quote_gross = L(S0-S1)>>128. 110/110 fixture trades match on every leg (5,990 more in the first scan, 0 misses on single-segment trades); hook configs identical.
- Fee side: buy `fee_on_base_token` == (config collect_fee_mode == 1); sells always on the quote output. Total fee = trading + protocol (+ referral), protocol = 20%.
- Pricer on 4.74M Swap3 rows (tools/dbc_screen.py --validate): amount_out within 1 bp on 99.0% of buys and 99.3% of sells (bad-order rows excluded; P14 Swap2: 98.2% / 93.8%).
- EvtSwap3 has no migration_threshold: read from the config (equal to the Swap2 event for 755/755 configs). A first screens run returned 0 attempts from this bug (DECLARE addendum 1).
- Legacy path unchanged: `python3 tools/dbc_screen.py --cells m10` (job #1497, head 882c72f6) reproduces #1488's m10 JSON identically (attempts, refusals, deciding/1.0 SOL/rent/optimistic/lag blocks;
  flat +0.021171, pressure +0.016382, total +7.812). Everything new is behind `--swap3` / `--spent-root` / `--cfg-summary`.

## 3. Screens (job #1505; deciding leg 1.9 s, 0.25 SOL, 160k sends, rent 0; 7 days; DECLARE.md sha in SHA256SUMS.txt, declared 2026-10-24T06:52:06Z + addendum 06:55:29Z)
| cell | n | /day | fills | strands | flat mean | press mean | ex-top-3 | pos days | optimistic strand flat | rt fee bps | label |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ms30 | 1108 | 158 | 943 | 605 | -0.1148 | -0.1011 | -128.1 | 0/7 | +0.0129 | 198 | FAIL |
| ms50 | 1093 | 156 | 988 | 646 | -0.1233 | -0.1087 | -135.2 | 0/7 | +0.0123 | 172 | FAIL |
| ms70 | 1943 | 278 | 1863 | 1378 | -0.1533 | -0.1301 | -298.1 | 0/7 | +0.0094 | 110 | FAIL |
| bb1 | 2258 | 323 | 1729 | 87 | -0.0011 | -0.0004 | -5.5 | 3/7 | +0.0157 | 331 | FAIL |
| bb2 | 952 | 136 | 701 | 14 | -0.0019 | -0.0015 | -3.5 | 1/7 | +0.0097 | 362 | FAIL |
| bb4 | 198 | 28 | 146 | 2 | -0.0115 | -0.0090 | -2.9 | 0/7 | -0.0086 | 398 | FAIL |
| c3 | 3137 | 448 | 3000 | 157 | -0.0110 | -0.0096 | -35.6 | 0/7 | +0.0067 | 201 | FAIL |
| c4 | 2071 | 296 | 1947 | 51 | -0.0067 | -0.0064 | -15.0 | 0/7 | +0.0054 | 222 | FAIL |
| c6 | 1415 | 202 | 1317 | 20 | -0.0059 | -0.0059 | -9.4 | 1/7 | +0.0002 | 233 | FAIL |
| dbs1 (new D6) | 2487 | 355 | 2404 | 34 | -0.0135 | -0.0115 | -34.3 | 0/7 | -0.0068 | 357 | FAIL |
| dbs2 (new D6) | 1202 | 172 | 1158 | 6 | -0.0131 | -0.0117 | -16.3 | 0/7 | -0.0047 | 354 | FAIL |
Multiplicity 23 cells with P14's 12. All 11 fail the declared label; no gate-style number is met. D1 not run. The "optimistic strand" column is a bracket (sell at the last on-curve price), not a result.
Reading: with Swap3 priced, the fee on the trades these rules pick is 110-400 bps round trip, the same finding as P14; D2 is decided by the strand assumption, D3/D4/D6 are negative or zero.

## 4. What a fresh holdout needs
- Only hours after 2026-10-24T00 are untouched: [10-25T00, ...). The tape tool is tools/venue_backfill_dbc.py (P12), ~14,050 credits per hour measured => ~0.34M credits/day (about 1.7M for a
  5-day block, 2.4M for 7), at the 4-slot Helius limit (about 19 h and ~0.5M credits per 48 h block per memory) i.e. roughly 5-7 days of wall time to fetch 5 days of tape; the last day is
  complete only after it ends (10-25..10-29 available from 10-30T00Z).
- Needed before fetching: a pinned, pre-registered rule set (any of these 11 cells would be a NEW look; none is a candidate now), the Swap3 pricer commit sha, the config-threshold map
  refreshed for new configs (a 7,399-config fetch cost 74 calls), and the P14 one-look protocol (marker, preflight, no smoke on the block).
- Transfer-hook pools need an executor tx plan (hook accounts, +25k CU) and evidence the sell is not blocked before they can be priced; failed txs are not on this tape.

## Disclosure
- Window reads: the 168 spent-holdout hours were read for counts, validation and the 11-cell screen; hardlink dir /data2/mal/venue-tape-backfill/p17_union (240 hours).
- Helius credits: config fetches 73+7,399 accounts (75 calls), 60 getTransaction calls, no tape fetch. Order of events and the cancelled capacity run: DECLARE.md.
- Not reviewed by quant-proof or reviewer. test_dbc_price.test_one_look_marker_is_global_and_atomic now fails on this host because the real one-look marker exists (it asserts it does not); not touched here.
