# Fast trade tape with 2 redundant sockets: coverage against chain, 2026-10-02T16–18

- **Tape:** the fast tape trial on `mal-fast-0` has run `ca542c5` (#233) with `--sockets 2` since 2026-10-02T13:39:04Z (#234).
- **Run:** MiScusi job #80 (code `0949ee1`), window `[2026-10-02T16, 18)`.
- **Chain truth:** the DEC-016 forward walk `/data/mal/blocks/forward-1002`, hours 15–17, each verified with 0 issues. No extra Helius credits.
- **Per-socket counters:** from the tape's heartbeat lines (job #81, read-only).
- **Generated report:** [tape-coverage-chain-2026-10-02T16-sockets2.md](tape-coverage-chain-2026-10-02T16-sockets2.md) (JSON alongside). The numbers below are copied from it.

## Result: PASS (DEC-015 §2.2 bar 95% against chain)

| Slice | Chain rows | Fast (2 sockets) coverage | Oracle (1 socket) coverage |
| --- | ---: | ---: | ---: |
| 2026-10-02T16 | 1,239,798 | 99.575% | 83.574% |
| 2026-10-02T17 | 1,077,123 | 98.944% | 80.321% |
| **Pooled** | 2,316,921 | **99.282%** | **82.062%** |

- **By venue (fast):** bonding 99.073%, PumpSwap 99.337%.
- **Signature level:** fast 99.297%, Oracle 82.035%.

## This was a reconnect episode, not a quiet stretch

Fast-tape heartbeat cumulative counters at 16:00Z and 18:00Z:
- **Reconnects** went from 91 to 107, so 16 in the window: 11 websocket closes with code 1006 and 5 HTTP 413 rejections. 5 hit socket 0 and 11 hit socket 1.
- **Since the 13:39Z restart** (by 18:00Z), HTTP 413 rejections are 19 on socket 0 and 31 on socket 1, and code-1006 closes are 29 and 28.
- **Dedup by 18:00Z:** `dedup_dropped` 14,246,717 and `dedup_mismatch` 1,128, about 0.008% of duplicates whose two copies differed in `(failed, len(logs))`. The first arrival was kept.

## The three windows together

| Window | Setup | Reconnects | Fast coverage of chain | Oracle coverage of chain |
| --- | --- | ---: | ---: | ---: |
| `[10-01T17, 19)` | 1 socket | 64 | 84.759% | 66.241% |
| `[10-02T06, 08)` | 1 socket | 0 | 100.000% | 98.615% |
| `[10-02T16, 18)` | **2 sockets** | 16 | **99.282%** | 82.062% |

**Reading:**
- In this window the single-socket Oracle tape lost about 18% of chain trades. The two-socket fast tape lost 0.718% while reconnecting 16 times.
- The two sockets reconnect at different times, so one covers while the other is down.
- This fits redundancy closing most of the reconnect loss. It is one 2-hour window, not a long-run rate.

## Not measured; open items

- **Coverage over days.** The forward walk now gives hourly chain truth, so this can run as a daily check.
- **The remaining 0.718% gap.** Unknown whether it is both sockets reconnecting together, or something else.
- **HTTP 413 rate.** It is not yet known whether 2 sockets raise the rejection rate against 1. There is no 1-socket control on the same endpoint at the same time.
- **The 1,128 mismatched duplicates.** Which copy is better is not checked.
- **Row values and runner lag.**
