# DEC-019 Amendment 2 fee-arm probe: landing verdict (written 2026-10-23T04:10:19Z from date -u)

Cost measurement, not a P&L result. No probe P&L is reported. No book changes status, and no recomputed history counts as out-of-sample. No book has cleared the promotion gate.

**Verdict: PASS (Test A and Test B both hold).**

Pinned wording (DEC-019 Am.2 "What changes the floor"): At 155,000 priority, 150/152 sends landed vs 146/148 at 500,000 (lower bound -2.70 pp, margin -5); buy lag 2.20 vs 2.44 slots (upper bound -0.007, margin +0.5). From DEC-019 Amendment 2, the simulator send cost is 160,000 lamports, and the measured lag shift is added. This is a cost input, not a P&L result. No book changes status, and no recomputed history counts as out-of-sample.

## Inputs
- Data: Helm's read-only extract /data/mal/hunt-1008/push-1016/hunt26/landing/feearm-landing-1022.csv (300 rows, sha256 9a5f93fe436c1bb1fc30a25be4917c25d786d961c0ebc0022b5717cb6d30ab88), from mal-fast-0 /var/lib/mal-live/probe-fills-feearm.jsonl, sends 2026-10-16T19:51:57Z to 2026-10-19T21:57:58Z. No amounts or P&L.
- C1 (Helm, 2026-10-23): executor build d29ae534905a3179d575d994f437bf54079d35f8; current pointed to it from 2026-10-16T18:49Z; not switched in the window (unit up 10-16T19:44:09Z through 10-19T22:00Z; next stop 10-21T06:25Z).
- C2 (Helm): 300 send rows (150 buys, 150 sells) = 296 landed + 4 failed; no row contains "dropped" or "expired"; --status shows attempts=150/150 sends=300, equal to the logged rows, so no signed send is missing from the ledger.
- Ruling: quant-proof, ARTIFACTS/lab/qp-ruling-1023-feearm.md (copy of /data/mal/hunt-1008/push-1016/hunt26/landing/QP-RULING-1023-feearm.md, sha256 34cecedcd908d33eaf78b628eefd1493600e7725eff6d161fe5ccc6ecedb2264).

## Tests (as pre-registered)
- Test A, every send (sell retries are new signed transactions and count as sends): 155,000 arm 150/152 (buys 75/75, sells 75/77); 500,000 arm 146/148 (buys 73/75, sells 73/73). All 4 failures slippage_exceeded. Difference +0.036 pp; Newcombe-Wilson one-sided 95% lower bound -2.700 pp. Holds. Fragility: 3 more failed test sends would have moved the bound below -5 pp.
- Test B, landed buys, slots_between: 155,000 mean 2.200 (n 75; 1:6, 2:50, 3:17, 4:2); 500,000 mean 2.438 (n 73; 1:3, 2:48, 3:19, 6:1, 8:2). Difference -0.238. 1,000 bootstrap draws (arms resampled separately), numpy default_rng(1): 95th percentile upper bound -0.0073. Holds (other generators +0.003 to +0.006; seeds 1-200 all below +0.032, per QP).
- Secondary: slot+1 share 6/75 vs 3/73.

## How to use it
- Measured lag shift credited as 0 slots (the test checked "not slower", not "faster").
- Only 24 of 150 buys fell in 10Z-18Z: the busiest hours are thinly sampled.
- Watch item: both test-arm failures were sells that hit slippage on chain.
- Does NOT change: any running book's pinned stack; any past result (reruns at 160,000 are in-sample exploration, winner's curse applies); any gate threshold; the live executor's priority (still 500,000).
- New arena books: DEC-031 section 1.4 pins 505,000 per send, and 505,000 is hard-coded in tools/arena_paper.py (sha pinned) and other arena tools. Using 160,000 for new books needs a DEC-031 amendment with a new parity proof, quant-proof and reviewer, merged before the first clock it covers.
