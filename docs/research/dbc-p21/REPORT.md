# P21 DBC D7 FLOOR and D9 SCHEDULED: exploration screen (2026-10-24)

**EXPLORATION SCREEN NUMBERS. Not evidence, not a gate read, no money claim.** E1 [10-14T00, 10-17T00) is exploration; E2 [10-17T00, 10-24T00) is SPENT for D1 and is disclosed exploration only (read before by P17). All six labelled cells FAIL. Neither primary advances. No book has cleared the promotion gate.

Rules: DECLARE.md (sha256 b5ac515d..., declared 2026-10-24T13:49:01Z, committed 3fc17b45 before any count or price; SHA256SUMS.txt). Binding ruling: QP-RULING-1024-p20.md (sha256 4b9aa044...). D8 not screened (QP: no path to a book).

## Runs
| step | job | code | output |
|---|---|---|---|
| counts only (attempts per day per cell, refusals, exclusions, D9 fee validation) | #1526 (3 min, peak 3.3 GB) | 653fce42 | data/counts.json (committed 9f9bafc8 before pricing) |
| the one pricing run | #1527 (3 min, peak 3.6 GB) | 3f3e1a5f (= 653fce42 + a fix to the advance check for an empty latency line; no rule change) | data/screens.json (sha256 926cac7f...) |

- Data: /data2/mal/venue-tape-backfill/p17_union only, 240 good hours [10-14T00, 10-24T00), 0 bad hours; 6,971,697 SOL trade rows, 27,545 pools, 28,999 create rows. No fwdv1/, no meteora_dbc_holdout/ root, no hour >= 10-24T00 (code guard + tools/test_dbc_p21.py), no DAMM print read.
- **E3 (10-24) not used.** It may only be fetched after 2026-10-25T00:20Z; this lane stops at 02:00Z, before a day fetch could finish. Nothing was fetched.
- Config: P17's cached fetch (7,399 accounts, fetched ~06:55Z today under the current program); all 7,399 decode with the IDL v0.2.2 decoder. **Helius credits: 0.**
- Multiplicity: m = 26 before, **32 after** (6 labelled cells). Report-only lines do not add to m.

## Counts (job #1526)
- Pools excluded per day: no create row in the hours (age, peak, activation unknown) 176 on 10-14 (pools older than the data), then 8-61/day; hook / transfer-fee / unmapped 7-67/day. Config base-fee modes: 6,769 mode 0, 629 mode 1, 1 mode 2; 634 configs pass the D9 schedule rule (164 + 130 candidate pools of modes 0 / 1; no rate-limiter pool).
- Floor check: 0 of 6,584,496 good rows print below the config start price (the hard floor holds on the tape).
- D9 fee validation (counts only; tape rate vs config base fee): configs without dynamic fee: before expiry 4,809 / 5,247 (91.7%) within 1 bp; after expiry (600 s) 6,556 / 6,934 (94.5%) within 1 bp. Configs with dynamic fee: 2.8% / 5.4% within 1 bp (the variable fee is on top of the base; priced with the declared dynamic-excess rule). The decoded schedule reproduces the tape for non-dynamic configs, so D9 was priced.
- All six cells pass the rate bar (12.3 to 15.0 attempts/day). D7 24 h cells lose 10-23 (block_edge 14-15).

## Screen (deciding: 0.25 SOL, 1.9 s legs / D9 landing at expiry + 2 slots, rent 0, 2 x 160k sends, strand = full loss; mean SOL per attempt)
| cell | n | days pos | flat mean | flat CI90 lo | flat ex-top-3 | flat ex-best-day | press mean | press CI90 lo | press ex-top-3 | Bonferroni lo (m 32) flat | rt fee bps (mean) | label |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| d7_s102_h6 | 133 | 0/10 | -0.0066 | -0.0072 | -0.892 | -0.838 | -0.0055 | -0.0061 | -0.749 | -0.0077 | 298 | FAIL |
| **d7_s105_h6 (primary)** | 150 | 1/10 | -0.0070 | -0.0083 | -1.196 | -1.090 | -0.0058 | -0.0070 | -1.003 | -0.0099 | 329 | FAIL |
| d7_s102_h24 | 125 | 0/9 | -0.0052 | -0.0068 | -0.812 | -0.611 | -0.0044 | -0.0057 | -0.682 | -0.0084 | 307 | FAIL |
| d7_s105_h24 | 140 | 1/9 | -0.0050 | -0.0074 | -1.049 | -0.751 | -0.0042 | -0.0062 | -0.881 | -0.0098 | 335 | FAIL |
| **d9_t60 (primary)** | 123 | 2/10 | -0.0058 | -0.0147 | -1.383 | -1.007 | -0.0050 | -0.0124 | -1.153 | -0.0227 | 412 | FAIL |
| d9_t300 | 123 | 2/10 | -0.0227 | -0.0382 | -4.666 | -3.583 | -0.0191 | -0.0317 | -3.860 | -0.0545 | 406 | FAIL |

Best of 6 by flat mean: d7_s105_h24 at -0.0050. Date-clustered CI90 lo (information): -0.0074 / -0.0088 / -0.0071 / -0.0081 / -0.0143 / -0.0426 (same order).

### E2 alone and eras (flat mean; pressure in brackets)
| cell | Swap2 [10-14T01, 10-16T04) | Swap3-E1 [10-16T04, 10-17T00) | E2 (n, mean, days pos) |
|---|---|---|---|
| d7_s105_h6 | 25, -0.0088 | 13, -0.0073 | 112, -0.0065 (-0.0055), 1/7 |
| d9_t60 | 33, -0.0128 | 15, -0.0200 | 75, +0.00004 (-0.0003), 2/7; ex-top-3 -0.661, ex-best-day -0.285 |
| d9_t300 | 32, -0.0362 | 15, +0.0531 (15 trips, one era) | 76, -0.0319, 1/7 |

### Advance rule (declared): NEITHER primary advances
d7_s105_h6 and d9_t60 fail every check: not MARGINAL on E2 or on E1 + E2 under either model, no positive-day majority, and the required latency line is negative (D7 2.5 s entry: flat -0.0070 / pressure -0.0058; D9 expiry + round(2.5/slot_s): flat -0.0065 / pressure -0.0055). D7 and D9 on DBC are not eligible for a registration.

## Report-only lines (never a label)
- Stakes, rent charged (flat mean): d7_s105_h6 0.1 SOL -0.0030 (rent -0.0046), 1.0 SOL -0.0270 (rent -0.0287), 0.25 SOL rent -0.0086. d9_t60 0.1 SOL -0.0025 (rent -0.0041), 1.0 SOL -0.0214, 0.25 SOL rent -0.0075.
- D7 latency: entry 1.0 / 1.3 / 2.5 / 3.4 s and exit 3.4 s give the same mean to 4 decimals (-0.0070 to -0.0071 primary): floor pools rarely print inside a few seconds.
- D7 time-cap share of filled trips (E14 dead-pool warning): 99.3% (s105 h6), 100% (s102 h6), 99.2% / 97.8% (24 h). tp +50% fired 0-3 times per cell. The loss is the round-trip fee (median 250-383 bps on these young, still-scheduled pools) plus sends.
- D7 capital: maximum concurrent open 12-13 positions (3.0-3.25 SOL) at 6 h, 30-32 (7.5-8.0 SOL) at 24 h. Capped at 1.29 SOL open (first-come): 31-93 attempts skipped; primary capped flat mean -0.0074.
- D9 landings: expiry + 0 (optimistic, ahead of same-slot buyers) t60 flat -0.0045 / pressure -0.0045; expiry + round(1.9/slot_s) -0.0083 / -0.0070; expiry + round(2.5/slot_s) -0.0065 / -0.0055. Flat 50% race line t60 -0.0035 (a failed send costs only the send, so a higher fail rate looks less negative on a losing cell).
- D9 by mode (flat, t60): linear scheduler 57 trips -0.0093, exponential 66 trips -0.0029.
- D9 random-time control (seed 1; 38 trips, 81 refused not alive): t60 flat -0.0134, t300 -0.0312.
- Strand: 0 strands in D7, 1 (t60) / 3 (t300) in D9. Last on-curve price bracket (optimistic): d9_t60 -0.0028, d9_t300 -0.0043. The P16 DAMM continuation line was not computed (no DAMM data for E2; E1 DAMM data clipped); with 0-3 strands per cell it cannot change a label.
- No bad-row exclusion (A3 lookahead check): d7_s105_h6 -0.0070; d9_t60 -0.0016 flat / -0.0019 pressure (124 trips); d9_t300 -0.0219.

## Reading
- D7: the floor is real (no print below the start price), but pools that pumped to >= 2x and came back within 5% of the start mostly stay dead for 6-24 h: 97-100% of trips end at the cap, and each trip pays about 3% in round-trip fees on these young pools. Same shape as lib-b-E14 on PumpSwap. Capacity is also far below the P20 guess (about 15/day, not 100/day: the 2% impact bar removes most near-start states).
- D9: the config schedule is computable and validated, but buying at the end of the fee schedule loses at every landing tried, including the optimistic expiry + 0. The E2 near-zero mean of t60 comes from two days (10-20, 10-22) and its ex-top-3 is negative.
- The pressure model is pump-fit and unvalidated on DBC; both models agree on every label here.

## Disclosures
- The ideas were written after P14, P16 and P17 results were seen. E2 is spent for D1 and was read by P17; these hours can never confirm anything.
- One code fix between the counts and the pricing run (advance check, `req.get`): no rule, cell or parameter changed; both runs give the same attempt counts.
- Not reviewed by quant-proof or reviewer. Nothing deployed. Credits 0; MiScusi jobs #1526, #1527.
