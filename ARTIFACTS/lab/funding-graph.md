---
cursor:
  subagentId: "bc-d4079567-2fbe-5379-90ae-0c6ee47d1984"
---

# Funding graph — paper rug veto (preliminary)

Paper only. Goal is profit. `LAB_STATE.md` was not edited. Postgres was not opened. `mal-trade-tape.service`, `mal-observe.service`, and `mal-forward-paper.service` were not restarted and did not receive this code.

PR: https://github.com/vaanai/MAL/pull/86 merged as `462a6eb`. Helius-rate follow-up: https://github.com/vaanai/MAL/pull/89 (`cursor/funding-helius-rate-1984`, head `706495c`). Queue bound and the 150k credit cap: https://github.com/vaanai/MAL/pull/90 (`cursor/funding-queue-bound-1984`, head `5cd9490`). The enricher was restarted onto that tree at 2026-09-25 17:00:53 UTC, logged public RPC at 1/s, then switched to Helius at 2/s on the first key poll (~17:01:23 UTC; key file mtime 17:01:07 UTC). At 18:36:46 UTC it was restarted again with `MAL_FUNDING_RPC=public` and logged `rpc=public rps=1.0`. The next minute line stayed on that endpoint (`calls=62`, `limited=0`, credits still 4709/150000) with no Helius switch. At 20:13:27 UTC the public pin was removed and the unit was restarted onto Helius at 10/s with a 2M credit cap (`rpc=helius rps=10.0 credits=4709/2000000`). Tape and forward paper were not restarted for that switch. Tape (since 08:37:11 UTC), forward paper (since 16:52:29 UTC), and observe (since 2026-09-23 07:57:53 UTC) were not restarted.

## What is running

User unit `mal-funding-graph.service` (nice 19, idle IO). As of 20:13:27 UTC it is on Helius at 10 requests/s with a persisted 2M credit cap. Creators are queued ahead of early buyers. `MAL_FUNDING_RPC=public` still forces the public endpoint if set. Tree is `/var/lib/mal/paper/funding-graph`, not `~/mal` and not the live LAYA or forward-paper snapshots. Append-only rows are `/var/lib/mal/graph/funding-2026-09-25.jsonl`. A row is usable only when `first_seen_ms` is at or before the decision. Unknown features do not veto.

Started 2026-09-25 16:30:29 UTC. Restarted 16:35:51 UTC after a fix for HTTP 413 (below). After that restart the other units were still the same processes: tape active since 08:37:11 UTC, forward paper since 16:02:08 UTC, observe since 2026-09-23 07:57:53 UTC. `funding_graph.py` is not in `/var/lib/mal/paper/laya-v0` or `/var/lib/mal/paper/forward-paper`. The live forward process will not join this graph until a later snapshot deploy. This task did not do that deploy and did not retrain a model.

## Public RPC

Probe before the service, eight recent creator wallets, one signature page, target 2 rps:

| | |
| --- | --- |
| RPC | public (`api.mainnet-beta.solana.com`) |
| Wallets resolved | 8 / 8 |
| Calls | 16 |
| HTTP 429 | 0 |
| Elapsed | 8.549 s |
| Achieved | 1.872 calls/s, 56 wallets/min |

That rate is a short burst with one page. The service stays at 1 rps so it does not sit on the tape's RPC. After the 16:35 restart the minute logs showed about 60 calls/min, `limited=0`, and no backoff. No Helius key was loaded.

Before the fix, `getTransaction` HTTP 413 was requeued forever and burned the 1 rps budget (repeated `rpc_error=http 413` in the log, zero rows for those wallets). The process now writes a fail-open row (`tx_unavailable` if an older transaction cannot be read, `rpc_rejected` if the signature page itself is 413 even at limit 200) and does not treat a newer transfer as the funder. Post-fix logs in the scored window had `unavailable=0`; the 413 wallets had not been reached again yet. Of 224 rows at score time, 168 had a funder, 49 were `history_capped` (more than 3 signature pages), 7 were `no_inbound`. About 14 of the first 217 rows were exchange-funded. Creators are priority over early buyers; the queue was still hundreds deep, so many buyers are missing at a +30s decision.

## Score (preliminary)

Window: creates on 2026-09-25 from 180s before the first graph row (first row 16:30:29.634Z, last row used 16:40:51.350Z) through the hour file `trades-2026-09-25T16.jsonl` (700,068 lines, 433 creates). One decision per mint on the LAYA grid. PnL is `hold_30s` with the current `ENTRY_LATENCY_MS=1000`. Walk-forward is the existing expanding prefix; the veto is a pre-registered filter, not a pool top-k. Ranked books stay on `forward_paper._RankWindow`.

Funder known / vetoes by decision offset:

| Offset | Decisions | Funder known | Vetoes | hold_30s fills |
| --- | ---: | ---: | ---: | ---: |
| +5s | 433 | 78 | 0 | 397 |
| +15s | 431 | 137 | 1 | 392 |
| +30s | 427 | 169 | 1 | 369 |
| +60s | 396 | 173 | 1 | 343 |
| +120s | 350 | 158 | 1 | 311 |

Promotion sample is the +30s grid, out-of-sample half only.

| | Baseline | Kept (veto applied) |
| --- | ---: | ---: |
| OOS n | 184 | 183 |
| OOS with funder known | 100 | |
| OOS vetoed | 1 | |
| Mean SOL | −0.01369 | −0.01376 |
| Total SOL | −2.520 | −2.518 |
| UTC days | 1 | 1 |
| Majority of days positive | no | no |
| Mean 90% CI | [−0.01648, −0.01103] | |
| Total after dropping top 3 | | −2.626 |
| Older LAYA promote flag | | false |
| Tight promote | | **false** |

Lift of mean SOL: −0.000063. The single veto was a funder→creator→buyer loop (`f_funder_creator_buyer_loop=1`, no cluster history, not an exchange). That trade lost 0.00218 SOL, which is smaller than the book’s average loss, so dropping it made the mean slightly worse. Same-funder bundles did not fire out of sample. Two OOS trades had a prior rug fraction ≥ 0.5 (descriptive slice, mean −0.035 vs −0.018 off); that is not a promoted effect.

The tight bar is n ≥ 100 OOS, ≥ 5 distinct UTC days with a majority of those days positive, bootstrap CI lower bound of mean > 0, and total SOL still positive after dropping the top 3 trades. OOS n clears 100. Everything else fails. That score used the funding-graph copy of the bar. #87 put the same bar in `tools.laya_v0.book_stats`. The scorer now calls that function and the local copy is gone. The veto itself was not re-run.

Cluster “rug” in the features is a 30s price collapse (end/start < 0.5), not `no_exit_liquidity`. The scored PnL is the simulator’s `hold_30s`, which does include stuck no-exit losses. This run does not show that the veto removes that loss mode.

## Coverage at 1 rps (2026-09-25 16:44Z)

Creates whose T+30s had already elapsed. A creator counts as covered only when that wallet's graph row has a funder and `first_seen_ms` is at or before the create plus 30s. `/var/lib/mal/backfill/helius.env` was absent. No Helius key was in the unit environment.

| | Since 16:30:29Z | Since the 16:35:51Z restart |
| --- | ---: | ---: |
| Creates | 509 | 356 |
| Creates per minute | 35.5 | 39.6 |
| Lookup finished by T+30s | 76.4% | 74.4% |
| Creator funder known by T+30s | 49.3% | 47.8% |
| Lag p50, resolved after the create | 8.5 s | 5.7 s |
| Lag p90, resolved after the create | 22.7 s | 20.2 s |

The queue in the minute logs climbed from 125 to 857 and was still rising. Creators jump ahead of buyers, so creator lag sits near the 30s line (p90 about 20s) while about a quarter of lookups miss T+30s entirely. Of the lookups that finish in time, many are `history_capped` or `no_inbound`, which is why a known funder is only about half of creates. That is too thin to judge the veto. Early-buyer features are behind the same queue, so same-funder and loop coverage is thinner still.

The enricher stays at 1 rps on public RPC. When `HELIUS_API_KEY` is set, or a line appears in `/var/lib/mal/backfill/helius.env`, the running process switches to Helius. The default Helius rate is 5/s; `MAL_FUNDING_RPS` overrides that on Helius only (the unit drop-in is 2). Public RPC stays at 1/s even if that variable is set. A persisted counter at `/var/lib/mal/graph/helius-credits.json` stops Helius calls at 150k credits (one credit counted per `getSignaturesForAddress` or `getTransaction` attempt) and falls back to public RPC at 1/s. The key is not logged.

## Coverage on Helius at 2 rps (2026-09-25 18:33Z)

Same definition as the 1 rps table. Window is creates at or after the ~17:01:23 UTC switch whose T+30s had elapsed by 18:33:20 UTC. Credit file: 4,582 / 150,000. No `reason=credit_cap` line; the process is still on Helius.

| | Helius 2 rps since ~17:01:23Z | 1 rps public since 16:35:51Z |
| --- | ---: | ---: |
| Creates | 2,448 | 356 |
| Creates per minute | 26.8 | 39.6 |
| Lookup finished by T+30s | 63.0% | 74.4% |
| Creator funder known by T+30s | 28.5% (698) | 47.8% |
| Lag p50 / p90, resolved after the create | 4.1 s / 21.2 s | 5.7 s / 20.2 s |
| Hours in window | 1.53 | |
| Credits | 4,582 | |
| Credits per hour | ~2,990 | |

That is below the ~48% baseline. The minute log at 18:32Z was `resolved=1383 queue=241 calls=4456 limited=493 unavailable=0 dropped_stale=2214 dropped_cap=1002 credits=4456/150000`. HTTP 429s keep a 5s backoff, so the burn is about 0.83 calls/s rather than a sustained 2/s. The queue sits near the 256 cap. The veto was not re-run.

## Coverage on Helius at 10 rps (2026-09-25 21:13Z)

Developer plan. Window is creates at or after the 20:13:27 UTC restart whose T+30s had elapsed by 21:13:45 UTC (1.00 h, 1,378 creates, 23.0/min). A creator counts as covered when that wallet's graph row has a funder and `first_seen_ms` is at or before the create plus 30s. An early buyer is one of the first four non-bot, non-creator buys by that same deadline.

| | Helius 10 rps | Public 1 rps baseline |
| --- | ---: | ---: |
| Creator funder known by T+30s | 61.5% (847/1,378) | 47.8% |
| Lookup finished by T+30s | 91.2% | 74.4% |
| Lag p50 / p90 | 0.9 s / 8.2 s | 5.7 s / 20.2 s |
| Early-buyer funders known by T+30s | 34.8% (772/2,216) | |
| Creates whose early buyers are all known | 8.9% (123/1,378) | |
| HTTP 429s | 0 | |
| Credits burned | 4,395 | |
| Credits per hour | ~4,370 | |
| Credit counter | 9,104 / 2,000,000 | |

The queue stayed near empty (`queue=2`, `dropped_cap=0` on the later minute line). 10/s was not saturated.

## Rug veto re-run (since 16:30:29Z)

Shared `book_stats` promotion, plus the flat 15% fail rate and the pressure-fail scale-1 gate. One decision per mint at T+30s. PnL is `hold_30s` with the current labeler: a chain→receive draw plus the host recv→decision hop (272 ms from `/var/lib/mal/paper/forward-paper/latency.json`). 7,441 decisions, 3,736 with a funder known at the decision. Out-of-sample half only.

| | Baseline | Kept |
| --- | ---: | ---: |
| OOS n | 3,720 | 3,645 |
| Funder known | 2,266 | |
| Vetoed | 75 | |
| Mean SOL | −0.01225 | −0.01233 |
| Total SOL | −45.56 | −44.95 |
| UTC days | 1 | 1 |
| Mean 90% CI | [−0.01316, −0.01111] | [−0.01330, −0.01117] |
| Total after dropping top 3 | −48.22 | −47.60 |
| Flat 15% promote | false | false |
| Pressure scale-1 promote | false | false |

Lift of mean SOL is −0.000084. The 75 vetoed trades averaged −0.00819 SOL, a smaller loss than the book, so removing them did not help. Promote is false. One UTC day is enough to fail the five-day bar, and the mean CI is below zero on both gates.

## Veto as a filter (2026-09-25 22:00Z)

Same T+30s decisions since 16:30:29Z, n=7,876. A rug here is a 30s price collapse (end/start < 0.5) or a `no_exit_liquidity` hold. The veto fired 114 times (1.4%).

| Class | Base rate | Rate inside the veto | Recall |
| --- | ---: | ---: | ---: |
| No-exit | 25.6% (2,020) | 7.9% (9/114) | 0.4% |
| 30s price rug | 10.7% (808/7,565) | 20.2% (23/114) | 2.8% |
| Either | 35.9% | 28.1% (32/114) | 1.1% |

Fresh wallet, which the veto does not use, is the column with a higher no-exit rate: 45.6% (n=1,310) versus 23.4% when the wallet is not fresh. Exchange funding is lower (no-exit 16.8%, n=380). Of the veto itself, the loop arm is 42 trades at a 7% rug rate, same-funder is 18 at 22%, and the prior-rug arm is 60 at 42% on the union (only 2 of those are no-exits).

Info-only overlay, rule unchanged. Attention `hold_60m` through the 18:25Z study freeze is n=100, mean +0.0007 SOL, vetoes 0 (funder known on 32). The same book through the later tape is n=203, mean −0.0151 SOL, vetoes 0. mig+15 OOS top 20% (`tp50_sl30`) is n=32, mean +0.0030 SOL, vetoes 0 (funder known on 19). Delta is zero on both books. The mig+15 buy-all, not the top 20%, dropped one trade and the total moved by +0.001 SOL.

## Re-run status

The first score, above the coverage tables, used a fixed 1.0s entry and the older fill. The T+30s re-run in the Helius section uses the shared promotion rule and the labeler now on main (chain lag, recv→decision hop, 15% fail rate, pressure scale 1). That re-run does not promote.

## What could go wrong

- Fail-open: capped history, a 413, or a wallet still in the queue does not veto, so the rug still enters.
- A backfilled creator’s row cannot label that creator’s own past decisions. Only decisions at or after `first_seen_ms` count.
- At 1 rps the buyer queue lags the +30s decision, so same-funder and loop features are thin. Coverage at +5s was 78/433 versus 169/427 at +30s.
- The exchange list is three hot wallets. Disputed labels were left out. A missed exchange can look like a fresh funder.
- Do not load a model trained on these columns into the current forward snapshot. That process does not fill the columns.

## Tests

`python3 -m unittest tools.test_funding_graph tools.test_laya_v0 tools.test_forward_paper` — 66 tests, OK (2026-09-25), after the scorer switched to `book_stats`.
