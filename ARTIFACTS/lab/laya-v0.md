---
cursor:
  subagentId: "bc-f772e4fe-d409-5a16-ac18-26ac5f06bc91"
---

# LAYA v0 — walk-forward paper model

Paper only. Recorder and `mal-trade-tape.service` were not modified. `LAB_STATE.md` was not edited. Not merged. This branch hides an intra-transaction pool from paper fills. The host tables have not been re-scored.

## What is on main

#87, #88, #89, #91, #92, #94, #95, and #96 are on main (`280ddca` when this branch was cut). Promotion is n ≥ 100, ≥ 5 UTC days with a majority positive, mean 90% CI lo > 0, and total still positive after dropping the top 3, under both the flat 15% fail rate and the pressure model at slope scale 1. Scale 2 is not a gate. Offline top-k is the shared `RankWindow`.

The host timer snapshot `/var/lib/mal/paper/laya-v0/` was replaced with those main files after #91 merged. `entry_latency_report` does not take `graph`. The timer script is main's script (latency report, then the mig+15 trainer). It does not include this branch.

## Backward holdout (this branch)

Draft PR https://github.com/vaanai/MAL/pull/97, branch `cursor/laya-backfill-holdout-bc91`, head `4986524`. Do not merge.

Backfill rows (`source=backfill`, null `t_recv_ms`) are stamped `t_recv_ms = block_time*1000 + one live chain→receive draw per signature + recv→decision hop`. Every inner event of that signature shares the draw. A missing or `UNK` signature still draws per row. The lag map is keyed by signature for the whole file and is not cleared when the slot changes. A new file starts a new map. A negative draw is floored at 0 before the hop. Block time is not the receive time. Rows with no block time are dropped. The synthetic clock is not added to the live lag pool. Backfill entry labels add another chain draw and do not add the hop again.

This hop came from `/var/lib/mal/paper/forward-paper/latency.json`: recv→decision n=134, p50 272.5 ms, so the applied hop is 272 ms (the 26 ms floor only binds when the file is lower).

Models are fit only on live decisions at or before `2026-09-25T15:30:00Z`. Backfill is not in the fit. The mig+15 book is graduated swing's mig+15 / top 20% / `tp50_sl30` (0.05 SOL), scored with the same causal window. Per-day results are separate from the forward holdout. A day is sealed only when every holdout hour of that date (through 06:58Z on 2026-09-25) has a finished stats file. The 04:15 script passes `--backfill-dir` when `/var/lib/mal/backfill/trades` exists.

## First run (side tree, not the timer)

`/var/lib/mal/paper/laya-backfill-holdout/out/backward_holdout.json`, finished 2026-09-25 21:30Z. Nice 19. Train n=64818 live decisions, mig+15 labeled 239. Backfill stamped 5,201,846 trades, dropped 0 for a missing clock. Backfill decisions 45,432.

Sealed hours at score time: 2026-09-25T01 through T06. T00 was still open, so 2026-09-25 is not a sealed day. The sealed-day pool is empty (n=0 on every book). Nothing promotes.

Scored hours, one partial day:

| book | n | mean | total | ex top 3 | CI lo | p1 total | p2 total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| buyers_8 top 5%, 2x ladder | 62 | −0.0038 | −0.235 | −0.538 | −0.0093 | −0.233 | −0.255 |
| T+30s top 1%, hold_30s | 71 | −0.00047 | −0.034 | −0.229 | −0.0039 | −0.028 | −0.023 |
| migration hold_30s | 238 | +0.481 | +114.41 | −0.519 | −0.0023 | +6.53 | −0.132 |
| mig+15 top 20%, tp50/sl30 | 27 | −0.0060 | −0.162 | −0.222 | −0.0128 | −0.131 | −0.124 |

Migration's +114 SOL is the top 3 trades. Without them the total is −0.52 SOL, the mean CI includes values below 0, and pressure scale 1 still fails the tail drop (ex top 3 −0.44). Scale 2 total is −0.13. One UTC day, so the 5-day clause fails every book.

That +114.7 SOL trade is one signature (`3eJ5FY…`): a 1 SOL buy and a 10,997 SOL buy in the same transaction. The old clock drew a receive time per row, so the paper book could buy the pool after the first buy and sell the drained pool. Head `7da11e0` stamps one receive time per signature and exposes one fillable state per signature (reserves after the last inner event). Order inside a slot is transaction position, then event index, on live tape and backfill. `tx_index` on the row wins; otherwise the position is the order that signature was first read.

## Re-score blocked (2026-09-25 23:42Z)

The backward holdout and the forward/walk-forward tables were not re-run. Two clean attempts, each with a fresh cloudflared 2026.9.1 binary and `scripts/mal-core/agent-ssh.sh`, failed the same way. cloudflared logged `failed to connect to origin error="websocket: bad handshake" originURL=https://ssh.tradervaan.com`. SSH then reported `kex_exchange_identification: read: Connection reset by peer` on `127.0.0.1:2222`. The deploy-key fingerprint check did not fail. No host files were written. The 21:30Z table above is still the last score. Live-book deltas are unknown; a same-slot reorder from `event_index` to read-order transaction position can move them, and that was not measured.

Local tests after this fix: `python3 -m unittest discover -s tools` 812 OK, 1 skipped; `discover -s observe` 48 OK, 2 skipped.

## Helius receive time

Implemented as above for this holdout. Do not drop null `t_recv_ms` rows, and do not use block time as the receive time.

## Next model iteration (after #97 merges)

Do not put this in #97. Source: [funding-graph.md](funding-graph.md), veto-as-filter section (2026-09-25 22:00Z). Same T+30s decisions since 16:30:29Z, n=7,876. A rug is a 30s price collapse (end/start < 0.5) or a `no_exit_liquidity` hold.

The hand-built `rug_veto` fired 114 times (1.4%) and was useless. No-exit inside the veto was 7.9% (9/114) against a 25.6% base (recall 0.4%). On the earlier shared-promotion re-run, the 75 vetoed trades averaged −0.00819 SOL, a smaller loss than the book, so dropping them moved mean SOL by −0.000084. Do not add another veto rule.

The column the veto does not use is the one that separates no-exits: `f_creator_fresh_wallet` is 45.6% no-exit (n=1,310) versus 23.4% when the wallet is not fresh. Exchange funding is lower (16.8%, n=380). The prior-rug arm of the veto was 60 trades at 42% on the union, but only 2 of those were no-exits, so that slice is not a rule either.

On the next fit, after #97 is merged, keep these in the LAYA feature packet and let the booster weigh them:

- `f_creator_fresh_wallet` (already in `FEATURE_NAMES`)
- `f_funder_prior_rug_frac` (cluster prior rugs; already in the packet)
- a coverage flag that is 1 only when a graph row is visible at the decision

`f_funder_known` is not that coverage flag. `fill_funding_features` sets it only when a funder resolved, and a missing row, a capped history, and `no_inbound` all look the same (known=0, fresh=NaN). The model needs a separate observed-at-decision bit so an unresolved creator is not treated as not-fresh.

Join stays strict: a row is usable only when `first_seen_ms` ≤ the decision time. A backfilled creator row must not label that creator’s earlier decisions. Do not load a model trained on these columns into a forward snapshot that does not fill them.
