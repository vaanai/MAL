# Two-socket fast tape: coverage FAIL over 2026-10-03T20–22

Daily DEC-015 §2.2 check. Chain truth is the DEC-016 forward walk (`/data/mal/blocks/forward-1002`). Tool `tools.tape_coverage`, MiScusi job #103. Report on mal-research-0: `/data/mal/ops/tape-coverage-sockets2/tape-coverage-chain-2026-10-03T20-sockets2.{json,md}`.

| Hour | Chain rows | Fast coverage | Oracle coverage |
| --- | ---: | ---: | ---: |
| 2026-10-03T20 | 1,280,577 | 94.561% | 79.946% |
| 2026-10-03T21 | 1,272,201 | 93.172% | 77.689% |
| **Pooled** | 2,552,778 | **93.869%** | **78.821%** |

**Verdict:** FAIL against the 95% bar.
- At signature level, fast has 93.857% and Oracle 78.797%.
- By venue, fast has 93.46% of bonding rows and 93.98% of PumpSwap rows. The misses are spread across both venues, so they point to gaps in time, not to decoding.
- Fast has 0 duplicate identities. Earlier windows on the same unit and code (`ca542c5`, `--sockets 2`) were 99.282% (10-02T16–18, #249) and 100.000% (10-03T00–02, job #89).

## Cause (read-only checks, jobs #104 and #105)

- `fast-trade-tape.log` shows 40–73 `ws_connect` per hour from 2026-10-03T14Z onward, against almost none over 10-03T00–13Z.
- From 10-03T14Z on there were 154 handshake rejections, all `status_code=413` on `feed=public_rpc_logs`, socket 0.
- Abnormal closes (`code=1006`) were spread evenly: 123 on socket 1 and 111 on socket 0.
- The feed is the free public Solana RPC `logsSubscribe`. Its throttling and drops in busy periods leave windows where both sockets are down.

A third free socket is unlikely to help, because the limit is on the endpoint side.

## Impact

- **EXP-012's FINAL forward read (DEC-016) is not affected.** It is scored on getBlock chain hours, not on this tape.
- **The fast-0 paper runner is affected.** DEC-016 Amendment 3 (b) row 0 needs coverage ≥ 95% of scorer-entered mints over the runner's clean window. A feed that fails in busy hours blocks a live request.
- **Feed options went to the owner** (MiScusi question `q_Oj6H5DPXKSswZg`):
  - (A) a getBlock tip follower, about 0.32M credits a day;
  - (B) Helius websocket `logsSubscribe`;
  - A+B, which the manager recommends;
  - (C) keep the free feed.

Not measured: per-minute loss against per-socket state. The coverage JSON is per hour, and the log has a socket id only on close and reject lines.
