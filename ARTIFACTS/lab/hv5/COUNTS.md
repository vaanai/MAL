# COUNTS: HV5 cohort on explore-0814 (counts only; no price after a decision, no outcome, no API call)

Written 2026-10-21T17:54:34Z (host clock). DECLARE.md sha256 8dcb7ea76b58b2a5deafd341f2f685197fc6dce4a3387514beb5bcf6b9dfde11. Tool tools/hv5_cohort_counts.py sha256 be9987aae03ca674d2900a9c23a51bfc789133fa59843ebbb36e33ce1fc3ff78; hv4_cards.py sha256 fb33f750d2ba270711aa37b86348505d93946b974f071b765ff317e36f64ce52.
In-sample exploration block only. Nothing here is a P&L, a label or a profit claim. Attempts are entries the CODE rule book makes, before any model veto, entry cap or ranking.

Bar (DECLARE section 3): attempts/day >= 20 AND >= 100 attempts in the worst 7-consecutive-day window.

| source | attempts | pools | days with >=1 | attempts/day | median day | min day | worst 7-day | bar |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| H5F | 1,698 | 1,698 | 15 | 121.3 | 106 | 41 | 708 | MET |
| CON_PULLBACK | 6,718 | 1,196 | 15 | 488.6 | 504 | 47 | 2547 | MET |
| BIG_BREAKOUT | 1,730 | 826 | 15 | 125.8 | 119 | 2 | 507 | MET |
| BIG_SECOND_WAVE | 6,764 | 1,073 | 15 | 491.9 | 494 | 42 | 2697 | MET |

## Delta-feature availability, share of attempts (as-of strict; flags only)

| source | holders 4-12 min | creator history | buyer cohort | BOOST state | all four | all-four attempts | all-four per day | all-four worst 7-day | bar on all-four subset |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| H5F | 11.4% | 59.7% | 53.5% | 84.3% | 5.5% | 94 | 6.7 | 41 | NOT MET |
| CON_PULLBACK | 90.7% | 42.8% | 67.7% | 90.7% | 28.3% | 1,904 | 138.5 | 627 | MET |
| BIG_BREAKOUT | 89.4% | 27.1% | 72.7% | 89.4% | 16.2% | 280 | 20.4 | 74 | NOT MET |
| BIG_SECOND_WAVE | 89.6% | 44.7% | 79.2% | 89.6% | 35.6% | 2,409 | 175.2 | 818 | MET |

## Attempts per UTC day

| source | 08-14 | 08-15 | 08-16 | 08-17 | 08-18 | 08-19 | 08-20 | 08-21 | 08-22 | 08-23 | 08-24 | 08-25 | 08-26 | 08-27 | 08-28 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H5F | 64 | 65 | 77 | 106 | 92 | 190 | 114 | 121 | 96 | 62 | 108 | 137 | 176 | 249 | 41 |
| CON_PULLBACK | 47 | 289 | 353 | 393 | 529 | 500 | 436 | 527 | 504 | 535 | 569 | 608 | 653 | 528 | 247 |
| BIG_BREAKOUT | 2 | 39 | 70 | 60 | 102 | 115 | 119 | 152 | 167 | 136 | 167 | 145 | 179 | 182 | 95 |
| BIG_SECOND_WAVE | 42 | 302 | 360 | 437 | 591 | 516 | 449 | 561 | 459 | 494 | 542 | 555 | 680 | 526 | 250 |

- H5F coverage 14.00 days (block 2026-08-14T12..08-28T12); CON/BIG scans start 13.75 days before the block end (6 h of block history first) and run every 5 min at :02.
- H5F pools scanned (canonical, V band, >= 10 prints): 11,172. For reference only, the frozen h5-flows run reported n=1,702 priced trades on explore-0814 (REPORT.md); this count is a trigger count, not that run. Differences: as-of block-time clock instead of the whole-path seconds-per-slot, post-trade Q = pre-trade Q + V - sol (no fee), strictly as-of BOOST detection, no leg or sps validity filter.
- Left-censored CON/BIG pools excluded (first PumpSwap bar within 60 min of the block start): 1,405.
- CON/BIG features come from 1-minute bars through the imported hv4_cards predicates; signers_15m and buyer cohort are sums of per-minute distinct traders (upper bounds); attempts are setups fired before ranking and entry caps, so real HV4 entries are at most these.
- Availability flags are about the existence of as-of inputs, not about their values. The holder flag is a proxy (create row at least 12 min old in the block) because the bars carry no holder count.
