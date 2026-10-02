# Trade tape coverage against chain truth, quiet-stretch re-check, 2026-10-02

Follows [tape-coverage-chain-verdict-2026-10-02.md](tape-coverage-chain-verdict-2026-10-02.md), which recorded a FAIL on `[10-01T17, 19)`. This re-check covers the window `[2026-10-02T06, 08)`, a stretch where the fast tape's stats showed 0 websocket reconnects.

- **Run:** MiScusi job #52 on `mal-fast-0` (code `ec57807`). It read the fast tape locally, Oracle's tape over `ssh mal-core-0`, and chain truth over `ssh mal-research-0` from `/data/mal/blocks/truth-1002`.
- **Chain truth:** job #44, getBlock walk of `[10-02T05, 09)`, 54,083 credits. `backfill_verify --content` found 4 hours checked and 0 flagged.

Generated report: [tape-coverage-chain-2026-10-02.md](tape-coverage-chain-2026-10-02.md) (JSON alongside). The numbers below are copied from it.

## Result

**Fast tape against chain: PASS, 100.000%** of chain trade identities over 2 complete hours.

| Slice | Chain rows | Fast coverage | Oracle coverage |
| --- | ---: | ---: | ---: |
| 2026-10-02T06 | 904,725 | 100.000% | 97.278% |
| 2026-10-02T07 | 874,004 | 100.000% | 100.000% |
| **Pooled** | 1,778,729 | **100.000%** | **98.615%** |

- **Signature level:** fast holds 1,736,996 of 1,736,996 chain signatures; Oracle holds 98.602%.
- **By venue, fast:** bonding 100.000%, PumpSwap 100.000%.
- **Tape rows with no chain counterpart:** fast 0.191%, Oracle 0.193%.
- **Fast against Oracle:** 100.000% of Oracle rows (reverse 98.618%).
- **Lead:** on matched rows, `t_recv_ms_fast - t_recv_ms_oracle` has p50 −188 ms and p90 −80 ms, so fast is first on 98.553% of rows. Excluding the 96,393 pairs more than 5 s apart: p50 −179 ms.

## What the two windows together say

| Window | Fast reconnects (stats) | Fast coverage of chain | Oracle coverage of chain |
| --- | --- | ---: | ---: |
| `[10-01T17, 19)` | 64 (47 code 1006, 17 HTTP 413) | 84.759% | 66.241% |
| `[10-02T06, 08)` | 0 | 100.000% | 98.615% |

- **Explanation fitting both windows:** when its socket is stable, the public `logsSubscribe` feed is complete. The losses come with the reconnect episodes, so this is not silent dropping in normal operation.
- **Limits:** this is two windows. It does not show how often reconnect episodes happen, or that a reconnect is the only cause.
- **Oracle's tape** follows the same pattern, at lower coverage in both windows.

## Consequences for DEC-015 §2.2

- **The fast feed is gate-grade when stable and not during reconnect episodes.** A forward book on it needs one of two things before it counts:
  - (a) reconnect resilience: redundant concurrent sockets deduplicated by `(signature, event_index)`, at $0;
  - (b) gap repair: on a reconnect or slot jump, fetch the missed slots by getBlock. This costs Helius credits only on gaps, so it needs the owner's OK under the paid-feed rule.
- **Either way, coverage must be measured continuously against chain truth, not assumed.** If DEC-016's forward walk runs, it provides that chain truth every hour at no extra cost.
- **The 9 Oracle books** traded on a tape whose coverage varies with reconnect episodes, from 66.241% to 98.615% across these two windows. That is context for the kill review.

## Not measured

- How often reconnect episodes occur over days.
- Whether redundant sockets close the gap.
- The runner's own lag on this feed.
- Values, as opposed to identities.
