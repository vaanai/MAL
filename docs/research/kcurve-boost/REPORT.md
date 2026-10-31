# kcurve-boost REPORT: K-1 and K0..K12 BOOST ride, August/September exploration blocks

**EXPLORATION-ONLY, FIRST 360 S: no October hour; not registrable before EXP-024's end state.**

**No cell survived; no book has cleared the promotion gate.** All eight cells are KILLED. This is in-sample exploration on blocks the lab has read many times.
- Ruling: quant-proof, `/data/mal/hunt-1008/ops/manager18/QP-RULING-1031-kcurve-price.md` (sha256 350c241c2a5cbcb54cec33b233631b3692ea8af9230e6fdb392df1db83b70e08; notebook n_ZDxWd-uSGxKjag).
- Library row: `eh1030-kcurve-boost-ride`.

## Registration and run
- **Pre-registration.** `PREREG.md` (sha256 338567229c03…fcc2); DECLARE n_QvBXoCeREuSVtQ, AMEND 1 n_xk9BaUnXL5mfFA, AMEND 2 n_3whCFMWjepDBkA.
- **Adapter.** `kcurve_price.py` (sha256 a3967bef…3530) at main 2fc83b00, with the kit pins unchanged.
- **T1 (job #1919): PASS.** The top-5 completers hold 0.1697 of 2,121 synthetic completions (kill line >= 0.80), so K-1 was priced. T1 does not measure P(win).
- **Precount.** #1922 refused (194 of 2,535 oracle-live opening prints not unique); #1925 was clean after the duplicate-row rule (counts.json 3501f5e3…).
- **The one pricing run.** Job #1935, exit 0, results.json sha256 5319cab93ca9bf080494bc42d989e318cd8e95a37612251637c2d039481ee7e1 (#1934 was cancelled while queued and never ran).
- **Blocks.** explore-0814, plus oracle-insample, fast-pool and oracle-live pooled as the September period.
- **Universe.** Organic V-band graduations with an identified opening print: 5,539 deciding trips per cell (explore 3,793, oracle-insample 642, fast-pool 613, oracle-live 491). There were 2 block-edge exclusions.
- **Costs.** 0.25 SOL stake, rent 0, 505,000 lamports a send.
- **Statistics.** Flat 15% and pressure at slope 1; strand rules total and sc. Kill rule: the kit's `kill_verdict`, plus worst-in-slot (R7), measured on every cell.
- **Exit.** s0 + 330 s, plus the 7-slot feed delay and an exit lag of at least 2 slots.

## Verdict table (in-sample)
Columns two to four give the pooled mean as a % of stake, flat / pressure. sc equals total on every cell (no strands). Worst-in-slot gives the lower leg in SOL per trade, flat / pressure.

| Cell | Pooled | explore-0814 alone | September pooled | Blocks positive | Worst-in-slot lower | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| K-1 | +30.6505 / +7.6434 | +44.458 / +11.28 | +0.655 / -0.257 | 2/4 | +0.075071 / +0.017987 | KILLED within the remainder margin |
| K0 | -0.2081 / -0.1446 | +0.262 / +0.178 | -1.23 / -0.845 | 1/4 | -0.006037 / -0.005061 | KILLED |
| K1 | +0.5116 / +0.4435 | +1.085 / +0.958 | -0.734 / -0.673 | 2/4 | -0.004891 / -0.00381 | KILLED |
| K2 | +0.8164 / +0.6770 | +1.28 / +1.078 | -0.191 / -0.194 | 2/4 | -0.00366 / -0.002869 | KILLED |
| K3 | +0.7301 / +0.7328 | +1.182 / +1.256 | -0.252 / -0.403 | 2/4 | -0.00284 / -0.001875 | KILLED |
| K5 | +0.5219 / +0.4739 | +0.485 / +0.511 | +0.603 / +0.393 | 3/4 | -0.002384 / -0.001704 | KILLED |
| K8 | -0.3365 / -0.1920 | +0.187 / +0.211 | -1.474 / -1.068 | 1/4 | -0.004042 / -0.002933 | KILLED |
| K12 | -0.8229 / -0.6114 | -0.317 / -0.126 | -1.921 / -1.666 | 0/4 | -0.005198 / -0.003906 | KILLED |

Kill reasons, as QP reproduced them:
- **K-1:** blocks positive 2/4 < 3, and the September pressure mean is <= 0.
- **K0:** every mean-type line, both CI90 bounds, the majority of dates, blocks, September, and worst-in-slot.
- **K1:** ex-top-3, both CI90 bounds, majority, blocks, September, and worst-in-slot.
- **K2:** both CI90 bounds, majority, blocks, September, and worst-in-slot.
- **K3:** both CI90 bounds, majority (11/25 and 12/25), blocks, September, and worst-in-slot.
- **K5:** flat ex-top-3; both CI90 bounds (trade-level lower bound -1.1654% / -0.8886%); flat majority 11/25; and worst-in-slot.
- **K8 and K12:** every line, both groups, and worst-in-slot.

## K-1: the positive rests on August only
- **Where the positive comes from.** explore-0814 is +44.458% flat / +11.28% pressure. The September period is +0.655% flat / -0.257% pressure, with 2 of its 4 blocks positive under all four sub-cells.
- **The decay.** The drop from August to September repeats the BOOST-family block decay (H5). October is not on this tape. K-1 is a kill-only optimistic proxy (PREREG section 1).
- **The remainder margin (PREREG C2).** M-bar is 0.002253096 SOL a trip (0.90% of stake). It bounds the advantage a real completer could gain from buying the curve's remainder.
  - Both pooled sub-cell means are above -M-bar, and every failing figure is smaller than M-bar per trade. Hence the registered label "K-1 KILLED within the remainder margin".
- **What that label means.**
  - K-1 did not survive, so nothing is licensed: no completion-probe design, and no October pricing pre-registration by survival.
  - The kill consequences for `p95-synthetic-completer` do not apply. That route stays open only through a new pre-registration that prices the curve-remainder purchase, or names a new universe or exit.
  - For October synthetic pools, such a pre-registration comes only after EXP-024's end state, with the owner's D3 release.
- **An artifact, not a result.** The report-only K-1 universe without the organic filter is an artifact of instant launches: the launcher's bundle holds the completion, so the seat cannot be had. Its figure is not a result and is not quoted here.

## Group B: paid speed
- **Every cell K0..K12 is killed.** Each fails at least the CI90 lower bounds and worst-in-slot.
- **Paid speed stays NO-GO.** Per PREREG section 9, paid speed for migration entries stays NO-GO.
- **The revive line is not met.** `eh1030-paid-speed-track`'s revive line is not met: K3 and K5 have positive pooled means, but both cells are killed.

**k in seconds per block.** Seconds = k x the block's mean seconds per slot. K-1 sits ahead of every print.

| Block | Mean s per slot | K12 in seconds |
| --- | --- | --- |
| explore-0814 | 0.3901 | about 4.68 |
| oracle-insample | 0.2661 | |
| fast-pool | 0.2667 | |
| oracle-live | 0.2682 | |

## Disclosures
- **Duplicate rows** (QP-RULING-1031-kcurve-dupes, condition 6).
  - oracle-live carries re-delivered duplicate trade rows. They differ only in `t_recv_ms`, so the 2026-10-01 byte-identical dedupe missed them.
  - Size: 93,202 of 27,290,350 rows (0.3415%), and about 5% of seed openings in QP's 3-hour count.
  - The adapter drops them before any use. Non-unique openings fell from 194 to 0. The other blocks had none.
  - Rows that share (signature, event_index) but differ (about 0.007% of rows) are not collapsed. Prices are unaffected; `ssb`/`nearby` would have been double-counted.
- **NOT MEASURED** (registered in the PREREG, not produced by the adapter):
  - completing-buy sizes (section 12 item 4; the structure probe's explore-0814/w1 figures are in PREREG section 1.2);
  - the section 4 item 3 report-only count of missing-create pools whose first bonding print is at least 60 s before s0.
- **Memory cap.** The pricing job ran under its 2.2 GB MiScusi job cap, not the PREREG section 12.5 `systemd-run` MemoryMax=12G scope. That is a lower cap, and the job exited 0.
- **Tape order (R-d).** oracle-live has no `tx_index`. The END state of slot m + k is the pre-trade state of the next slot's first print in tape order (the kit's convention). K-1's opening print is found without using within-slot order.
- **Multiplicity.** These 8 cells add 8 to m.

## Library
`eh1030-kcurve-boost-ride` → `explored-no-survivor`. `p95-synthetic-completer` keeps its status, with a note. `eh1030-paid-speed-track` stays NO-GO, with a note.

In-sample exploration only. Nothing here says a strategy makes money. No book has cleared the promotion gate.
