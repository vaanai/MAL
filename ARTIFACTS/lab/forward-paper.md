---
cursor:
  subagentId: "bc-67ed01ca-20af-5999-ad0f-918b46f1518b"
---

# Forward paper service

Graduated books: https://github.com/vaanai/MAL/pull/95 (`cursor/grad-forward-books-518b`, not merged). Freeze `2026-09-25T18:25:57Z` (`1790360757000`), the swing study's tape end. A swing decision at or before that instant is logged `before_freeze` and not filled.

| book | clock | take | exit |
| --- | --- | --- | --- |
| attn_first_hold_60m | genuine attention first-seen after that mint's first PumpSwap print | every such arrival, one open position per mint | hold_60m (60 min; the 4h swing cap) |
| mig15_top20_tp50_sl30 | migration + 15 min | causal top 20% of the swing LightGBM | tp50/sl30 with the study's 4h max hold |

Same ceilings on the execution ledger: 0.05 SOL, 3 concurrent, 0.2 SOL daily loss, kill switch. An open 60-minute hold keeps its concurrent slot; a later entry logs `max_concurrent`. Each book also keeps a shadow ledger that fills every signal the strategy would take, with no concurrent cap and no daily-loss halt. Position size and the kill switch still apply there. The daily summary reports both. `promotion` is the shadow ledger. `capacity` is the ceilinged one. Shadow rows in `decisions.jsonl` and `positions.jsonl` are marked `ledger=shadow` and are not sent. The existing migrate book's tp50/sl30 stays on the 30-minute rule. Attention is tailed from `/var/lib/mal/attention` at EOF. The mig+15 file is `/var/lib/mal/paper/graduated-swing/out/mig15_model.txt`; it is not on the host yet, so that book logs `no_model` until a booster is saved. Nothing in the study promotes.

Host `mal-forward-paper` was restarted onto the shadow-ledger code at 2026-09-25 18:54:08Z (PID 62072). The 18:47:03Z process (PID 61844) had the books without the shadow ledger. Banner lists both new books and the freeze. A minute later decisions carried `ledger` `ceiling` and `shadow`. About a minute later LAYA was still scoring (last laya_0.7 0.570, `below_threshold`). `mal-trade-tape` (48407), `mal-observe` (12088), and `mal-attention` (54088) were not restarted. `/var/lib/mal/paper/laya-v0/src` was not modified. The service copy under `/var/lib/mal/paper/forward-paper/src` was updated so it can import the swing feature code.

Follow-up: https://github.com/vaanai/MAL/pull/84 (`cursor/risk-ceilings-518b` on main). Config may only tighten position size (0.05 SOL), concurrent positions (3), and the daily loss cap (0.2 SOL). A wider value, a null cap, or a disabled kill switch is logged and refused, including on JSON reload. Buy-all in the repo config now uses those ceilings.

Paper only. No keys, no signing, no send. Goal is a book that can sit ~7 days positive before any live money. Nothing here is that book yet.

PR: https://github.com/vaanai/MAL/pull/81 (`cursor/forward-paper-518b`, stacked on #78). Head `1a7c721`, rebased onto `1c7d6f4` (event clocks and barrier classifiers).

Host `mal-core-vnic`. User unit `mal-forward-paper.service` is active (Nice=19, ~3% CPU, ~150MB). `mal-trade-tape.service`, `mal-observe.service`, and `mal-laya-v0.timer` stayed active. Code lives in `/var/lib/mal/paper/forward-paper` and uses the LAYA venv. The recorder unit and `/var/lib/mal/paper/laya-v0/src` were not modified. Postgres was not opened. Tunnel and Access were not touched.

## What is running

The service tails the open hour file and today's observe file from EOF. It keeps per-mint state, builds packets with the same `packet_at` / `WalletState` path as the offline builder, scores the entry model at `/var/lib/mal/paper/laya-v0/out/entry_model.txt` (reload every 30s), and fills with the PR #76 curve model. Entry delay is the measured chain lag plus time from the decision clock to the simulated send. It is not the old 1s constant.

Risk gate, per strategy book: 0.05 SOL hard cap, 3 concurrent, 0.2 SOL daily realized-loss halt, 60s creator cooldown, 300s token cooldown, one open position per mint. A `KILL` file at `/var/lib/mal/paper/forward-paper/KILL` blocks new entries and still manages exits. Buy-all has no concurrent cap and no daily-loss cap so it can be compared with the offline scoreboard. The kill switch still applies to it.

Books in `forward-paper.json`:

| book | clock | take | exit |
| --- | --- | --- | --- |
| buy_all | create | every create | hold_30s |
| laya_0.6 / laya_0.7 | every LAYA clock | score ≥ 0.6 / 0.7 on the pnl model | deploy rule (hold_30s today) |
| migrate_tp50_sl30 | first PumpSwap print | every migration | tp50/sl30 |
| buyers8_top5_ladder2x | buyers_8 | causal top 5% of the barrier model | ladder_2x_t30 |
| t30_top1_hold30 | T+30s grid | causal top 1% of the pnl model | hold_30s |
| migrate_hold_30s | first PumpSwap print | every migration | hold_30s |
| attn_first_hold_60m | genuine attention after migration | one open position per mint | hold_60m |
| mig15_top20_tp50_sl30 | migration + 15 min | causal top 20% of the swing model | tp50/sl30 (4h cap) |

Top-k is not a future-hour percentile. A score is taken when it sits in the top fraction of the last 500 scores at that clock, and not before the window holds 1/fraction scores (20 for 5%, 100 for 1%). Logs are append-only JSONL under `/var/lib/mal/paper/forward-paper/` (`decisions`, `positions`, `pnl-daily`, `latency`). Decision and fill rows are not kept in RAM.

Packets also fire the new clocks (curve 20/40/60/80 and clean-buyer 5/10/20) so a replay matches `build_feature_rows`. Fixture parity test covers that. The process started at EOF, so wallet and creator history is empty until it has been up. Live packets are not the full-history packets the model was trained on.

## Baseline vs the offline fill

Replay of sealed `trades-2026-09-25T14.jsonl.zst`, first 20 minutes (14:00:00Z–14:19:59Z), with `observe-2026-09-25.jsonl`. Output: `/var/lib/mal/paper/forward-paper/replay-2026-09-25T14b`.

Same logged latency, resimulated with `try_entry` / `simulate_exit`: **0 PnL mismatches** on 334 closed buy-all trades.

Against the old constant 1s `hold_30s` on the same creates:

| | n | median SOL | total SOL |
| --- | ---: | ---: | ---: |
| online, measured delay (~1.42s) | 334 | −0.001966 | −3.405 |
| constant 1s | 351 | −0.001965 | −3.219 |
| gap (online − 1s) | −17 | −0.0000006 | **−0.186** |

The median does not move. The total is 0.19 SOL worse because the measured delay fills 17 fewer trades and the ones it does fill are a bit worse. That gap is the cost of waiting for the real receive lag instead of the 1s seed.

On that same slice the other books lost or did not trade: laya 0.6 −0.242 (n=18), laya 0.7 −0.221 (n=46), migrate tp50/sl30 +0.017 (n=5), T+30s top 1% −0.007 (n=3, after 99 warmup skips), migrate hold_30s −0.002 (n=8). None of these are a 7-day result.

## Latency, and what Helius would buy

Sealed 20 minutes, 74,643 prints with a usable `event_ts`:

| hop | p50 | p99 |
| --- | ---: | ---: |
| chain → tape receive | 1,454 ms | 16,722 ms |
| receive → decision | n/a on replay (no wall clock) | |
| decision → simulated send | 0 | 0 |
| applied entry delay | 1,418 ms | 2,456 ms |

Live tail, a few minutes after start (6,472 prints, 143 entries): chain→receive p50 1,858 ms / p99 13,854 ms; receive→decision p50 17 ms / p99 166 ms; decision→send 0; applied entry p50 1,997 ms / p99 4,638 ms. The applied figure is the cached chain median plus the post-receive delay, so it lags the raw chain percentile until the median refreshes.

Helius `transactionSubscribe` would cut **chain→receive** (about 1.5s at the median, and the multi-second tail). It would not change the ~20ms scoring hop or the fee stack. The decision clock is already tape-receive time. The paper entry then waits that measured chain lag again, so fills sit about one median chain lag later than a pure post-receive delay. That is the delay the service applies, on purpose, instead of 1s.

## Barrier book

`buyers8_top5_ladder2x` skipped all 100 `buyers_8` clocks in the slice with `no_model`. The host has `entry_model.txt` and does not have `barrier_hit_100_30.txt`. This branch's trainer writes that file on the next full fit. The daily timer still runs the untouched snapshot under `/var/lib/mal/paper/laya-v0/src`, which does not. Until that file is produced, the ladder book logs the skip and does not open.

## Tests

`tools.test_forward_paper`, `tools.test_laya_v0.NoLookaheadTests`, and `tools.test_paper_tape_scoreboard`: 46 passed, 1 skipped. The parity test checks online packets against `build_feature_rows` on a fixture, including the new clocks.
