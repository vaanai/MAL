# Latency brief: why "entry time" keeps growing, and does cutting it help (desk compile, 2026-10-26)

Desk only. Nothing priced, no tape opened, no sealed output read. Every number below is quoted from an existing MAL file; "my arithmetic" marks anything I computed. P33 MEASURE.md is on branch `origin/claude/p33-dbc-p18` (commit 2ac6fca0), not yet on main; line numbers for it refer to that commit. No book has cleared the promotion gate and nothing here says a book makes money.

## 0. Plain answer

MAL's landing latency did not get worse. The headline numbers measure different things, over different start and end points:

| Headline | What it spans | Source |
|---|---|---|
| 1.3-1.6 s | pump/PumpSwap event to landing, the fast-entry thesis (audit) | ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md:20, :116 |
| 1.9 s | a modelled leg: send-to-land only, pinned in the books as the pessimistic leg (the fixed-build p90) | SYNTHESIS.md:273; docs/research/p29-keeper-absorb/PRICING-DECLARE.md:36 |
| about 2.5 s (p90) | P33: trigger print on chain to landed, sum of five component p90s, DBC path | P33 MEASURE.md:64 |
| about 4.4 s | P29 only: 1.0 s tag detection (declared, unmeasured) + 7-slot feed delay + 1.9 s | PRICING-DECLARE.md:36-38 (the sum itself is my arithmetic) |

Each later number adds a stage the earlier one left out (feed delay, slot-close wait, tag detection). The 1.9 s leg stayed constant. What grew is the scope of the sum, as the work moved from "land after we see it" to "land after the chain event". The slowest stage is the feed (public websocket), not the send.

## 1. Components as measured

| Component | p50 | p90 | Status and source |
|---|---|---|---|
| Feed lag, chain to receive (fast-0 public wss, processed) | 0.49 s | 0.76 s | CARRIED from P15, one 10-minute window 2026-10-24; Helius transactionSubscribe never probed. P33 MEASURE.md:59 |
| Same feed, confirmed commitment | 0.67 s | 0.98 s | P15 via P33 MEASURE.md:65 |
| Public logsSubscribe on Oracle, confirmed | 1.3-1.5 s | p99 about 2 s, later windows 7-16 s | docs/research/vps-options.md:87 (trade tape 25 Sep) |
| Slot-close wait (engine decides slot s at the first later-slot DBC row) | 1 slot = 0.21662953820217953 s | 3 slots = 0.6479913529093857 s (p99 16 slots = 3.4828958309088396 s, max 124 slots) | MEASURED on tape. P33 MEASURE.md:60 |
| Decision compute per sealing call | 0.004741581040434539 s | 0.010323497001081705 s | MEASURED on research-0 CPU, fast-0 not measured. MEASURE.md:61 |
| Decision to send | 0.221 s | 0.453 s | CARRIED, pump probe on fast-0. MEASURE.md:62. Audit: 55% of the 221 ms is file IPC (SYNTHESIS.md:116) |
| Send to landed | 2 slots (about 0.42 s) | 3 slots (about 0.64 s) | CARRIED, pump probe and fee arm (mean 2.20 slots); no DBC send ever made. MEASURE.md:63 |
| Sum, trigger print to landed | about 1.35 s | about 2.51 s | sum of p90s overstates a true p90. MEASURE.md:64. With the confirmed feed: 1.53 s / 2.73 s (MEASURE.md:65) |
| Slot length | 0.21659845931632163 s p50 on the DBC tape (MEASURE.md:56); 0.368 s per slot August median (ideas.jsonl:435 why_stopped) | | The two differ by block; "k slots" converts to different seconds in different months |

Other measured landing data:
- Live probe 10-05: every buy landed 2 slots after the executor's pool-state read; k from the curve's complete event was 17-22 slots, 11-15 from the migration tx (ARTIFACTS/lab/probe-live-2026-10-05.md:30, :34). Cause: the executor waited for the runner's post-simulated-latency row, so latency was counted twice; PR #307 expected saving about 6 slots (probe-live:36).
- Where MAL lands vs the on-chain pool-create slot: k p50 5 (faa3192, n 34), p50 6 (fixed builds, n 56); 0 of 62 at k<=2; in-slot index p50 0.448 (SYNTHESIS.md:34). At 0.368 s/slot that is about 1.8-2.2 s (my arithmetic), the same order as the 1.9 s pin.
- Discovery: the tip follower polls getSlot every 0.4 s and sees the pool about 3 slots after create (create to read p50 3, read to land p50 2); the processed stream leads by p50 1,012 ms but misses 7.7% (SYNTHESIS.md:186).
- P15 (as quoted by P6): MAL DBC entry leg p50 about 1.1-1.3 s, p90 about 1.9-2.1 s, which omitted the slot-close wait (hunt29-p6-latency/DESK-NOTE.md:25; MEASURE.md:65).
- Fee-landing study (tools/fee_landing_study.py, report ARTIFACTS/lab/fee-landing-study-v2-2026-10-06.md): fee does not decide whether a buy lands early; who lands early is set by when they send. In-slot, the higher CU-price buy came first in 61.4% of unequal pairs, mean Spearman 0.206 (report :22, :26-27). Observational and confounded.

Where the headline numbers come from:
- 1.3-1.6 s: audit synthesis, "MAL's event-to-landing time is about 1.3-1.6 s" (SYNTHESIS.md:116). The owner's "about 200 ms" is only decision to send.
- 1.9 s: fixed-build p90, used as the pessimistic leg (SYNTHESIS.md:273); the kit constant LAT_DECIDING (PRICING-DECLARE.md:36). It excludes the feed delay.
- about 2.5 s: P33's sum of p90s, and also the stop line registered for DBC (docs/research/dbc-m10-forward/REGISTRATION.md:63, :90, "stop if signal-to-landing p90 > 2.5 s").
- about 4.4 s: P29 primary entry = last tagged slot + 7 + ceil((1.0 + 1.9)/sps) (PRICING-DECLARE.md:38). 1.0 + 1.9 + 7 slots (about 1.5 s per DESK-NOTE.md:6) = about 4.4 s (my arithmetic; the figure is not printed in the file). T_tag = 1.0 s is declared, UNMEASURED (PRICING-DECLARE.md:37). The STRESS leg uses 3.0 s (line 40). If the forward feed shows a p90 above 1.0 s, the result is NON-TRANSFERABLE (line 41).

Not measured anywhere I could find: Helius transactionSubscribe lag, any DBC send, decide time on fast-0, tag-detection lag, and the true 4.4 s as an observed quantity.

## 2. Does lower latency help? Per family

| Family | Result | Turns positive at lower latency? | Source |
|---|---|---|---|
| EXP-012 / H5 BOOST, V-priced | Flat mean 0.03021 at k=1, 0.01879 at k=6, 0.00581 at k=12; CI90 lower bound below 0 from k=12 (pressure mean under 18% of its k=1 value at k>=12). Unfiltered book (n=8,801) negative at every k | Already positive at k=1..8 with CI above 0; edge decays with k. Faster helps, slower than about 12 slots kills it. This is the source of "gone by k>=12" | ARTIFACTS/lab/exp012-latency-virtual-2026-10-04.md:22-28, :31, :52; DEC/DEC-022-fast-entry-build.md:14 |
| Fast-entry thesis (bonding curve) | 0 of 3,168 tradeable cells (>=1 slot after the event) positive; best gross +0.666% / +0.762% / +1.599% vs a 2.47% fee round trip | No at any tradeable latency. k<=1 belongs to bundles and same-slot reactors | SYNTHESIS.md:42, :127, :411 |
| CAP-PICK | Entering at 0.8 s instead of 1.3 s is +0.244 pp pooled, all from August (+0.620); September blocks -0.10 to -0.24. EXP-020 paired k2-k6 +1.023% on 27 dates but -0.466 and -0.935 in the two September blocks | No. Gain is real only in August; CAP-PICK was withdrawn 10-15 | SYNTHESIS.md:152-153, :411 |
| Live probe | 55 fixed-build trips: price +0.496% per trip, fees 4.486% (priority 2.000, pool buy 1.206, pool sell 1.241, base 0.020, failed 0.018), realized -3.990%. Priority about 10x what early landers pay | Lost on fees, not price. Landing speed was not the cause | SYNTHESIS.md:26 area, :83; README.md:26 |
| P22 resting bid | At latency 0 (resting-bid upper bound, no adverse selection), all 120 summary rows negative, gross included; largest -0.0132 SOL/trade | No. Negative even at zero latency | docs/research/p22-wick/DECLARE.md:12-13; REPORT.md:16 |
| hunt23 speed audit | Grid feed delay 2/4/7 slots x entry 0.5/0.9/1.3/1.9 s; fastest vs slowest saves 3.315 s; paired mean change -0.1175, -0.0006, +0.0145 pp (flat) on three screen cells, -1.2017 pp on generic E1; levels stay negative (-0.81%, -2.18%, -2.11%). Manager call: NO-GO on a paid speed track | No | docs/library/ideas.jsonl:435 (why_stopped) |
| P6 latency (hunt29, 10-24) | Entry leg 1.9/1.0/0.6/0.3 s on nine lines; FLOOR v2 flat +0.01605 -> +0.01654 SOL/trade (+0.0065 SOL/day), REVIVE cells stay negative at every leg; BREADTH b5 and SELLABS s1 negative at every leg. No line crosses zero | No. Positive stays positive, negative stays negative | ARTIFACTS/lab/hunt29-p6-latency/DESK-NOTE.md:10-17, :44 |
| P29 keeper-absorb | Binding constraint is entry latency from the last tagged sell; the tagged cluster is visible only after the fill lands | Not tested by lowering; stress leg at 3.0 s report-only | docs/research/p29-keeper-absorb/PRICING-DECLARE.md:36-40; memory note |
| DBC (P21/M10) | Required robustness: mean > 0 at a 2.5 s entry leg, both models. P21 D7 failed it (flat -0.0070 / pressure -0.0058). M10 registration quoted +0.0097 flat / +0.0078 pressure at 2.5 s | Positive only for the M10 set; P21 cells negative at 2.5 s | docs/research/dbc-p21/REPORT.md:44; dbc-m10-forward/REGISTRATION.md:17, :63 |

Reading: latency matters inside EXP-012 (decay by k) and not as a rescue for any family that is already negative. None of the screens above flips sign from speed alone. These are exploration screens on mined blocks, in-sample, not gate evidence.

## 3. What would reduce latency, and its cost

| Option | Cost | Estimated effect | Status and source |
|---|---|---|---|
| Helius transactionSubscribe (processed) on the Developer plan | 2 credits per 0.1 MB + 1 per connection; est. 6-18k credits/month filtered to the migration authority | about 0.4 s behind chain (Helius doc figure, not measured) vs 0.49 / 0.76 s public wss p50/p90 | memory helius-plan-limits; not probed (MEASURE.md:59) |
| Helius preprocessedSubscribe | 0.1 credit per message, all paid plans | vendor: shreds about 8 ms ahead; est. p50 0.3-0.7 s feed from Frankfurt; feed side about -0.5 to -0.9 s vs 1.3-1.5 s | vps-options.md:95; DESK-NOTE.md |
| LaserStream WebSocket (included on Developer) | same credit rate | vendor: about 200 ms faster than Agave websockets (1.3 s to about 1.1 s) | vps-options.md:96 |
| LaserStream gRPC | $499 Business / $999 Professional | audit measured the processed stream p50 1,012 ms ahead of the tip follower (SYNTHESIS.md:186); feed only, not landing | DESK-NOTE.md:32; vps-options.md:97 |
| Helius Sender / landing services (Nozomi, Lunar, RPC Fast) | tip minimum 0.001 SOL per send (about 0.67% of a 0.25 SOL round trip) | vendor: median 0 slots; no independent benchmark. Cheap test about 60 buys of 0.01 SOL, cap 0.15 SOL, needs owner go and a DEC-019 amendment | DESK-NOTE.md:30, :40 |
| Jito bundles | minimum bundle tip 1,000 lamports, which loses when the auction is hot; not needed for a single direct buy | no MAL measurement | vps-options.md:106 |
| Frankfurt location | already in Frankfurt (OVH, fast-0); Ashburn to Frankfurt about 93 ms; Oracle to Helius beta endpoint 0.85-1.0 ms | the box is not the bottleneck: the public tape's 1.3-1.5 s p50 is confirmation lag and RPC queueing | vps-options.md:20; vps-region-benchmark.md:35-36 |
| Engine slot-close fix (P18) | builder time; needs its own md5 equivalence proof | removes up to 1 slot (p50) / 3 slots (p90 0.648 s) of wait, which P33 shows pushes the p90 sum to the 2.5 s stop line | MEASURE.md:60, :65; decision-neutral per MEASURE.md:65 |
| Fix executor double-counted latency (PR #307) | already done | about 6 slots saved on the live probe | probe-live-2026-10-05.md:36 |

Packages cost: mid $499; max about $2,000-5,600 per month; break-even 0.08-0.13 and 0.33-1.44 SOL/day (DESK-NOTE.md:34-ish, round45 s(f)). P6 conclusion: the best case landing gain is +0.0065 SOL/day on FLOOR v2 and +0.013 to +0.042 on REVIVE cells that stay negative (DESK-NOTE.md:44), below the $499 package break-even for every line except two whose totals are -28 and -20 SOL/day.

Estimates that are vendor claims are labelled; none is an independent MAL measurement.

## 4. Open and what was not checked
- The 4.4 s is a modelled sum; no observed 4.4 s landing exists. Its largest part (7-slot feed delay, about 1.5 s) is the kit's constant, QP-required.
- Tag-detection (1.0 s) and Helius transactionSubscribe lag are unmeasured.
- k-to-seconds conversion depends on block slot length (0.2166 s on the DBC tape, 0.368 s August median); compare k, not seconds, across months.
- Source reports for hunt23 are in /data/mal/hunt-1008 and were not opened; I used the library row (ideas.jsonl:435), which says no REPORT.md exists.
- Line numbers: P33 MEASURE.md lines are from commit 2ac6fca0; re-check once it merges.
