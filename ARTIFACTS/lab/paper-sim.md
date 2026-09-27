---
cursor:
  subagentId: "bc-e7ff36ef-56ee-5890-95cd-3cf9cae6093c"
---

# Paper tape scoreboard

Paper only. First honest book on the pump.fun + PumpSwap tape. Recorder and its service were not modified. `LAB_STATE.md` was not edited.

PR: https://github.com/vaanai/MAL/pull/76 (`cursor/paper-tape-score-093c`, stacked on #73).

Host labels (not in git): `/var/lib/mal/paper/paper-tape-2026-09-25/labels.jsonl` plus `scoreboard.json` and `scoreboard.md`. Schema `paper_exit_label_v1`. Features `f_*` use the create payload and tape prints with `t_recv_ms <= T` only.

Fill-honesty rerun (merged as https://github.com/vaanai/MAL/pull/88, `7d5b018` on main): `/var/lib/mal/paper/paper-tape-fill-2026-09-25/`.

`mal-forward-paper.service` was restarted alone at 16:52:29Z onto that fill code (priority 1_000_000 lamports, `reserves_with_our_buy`). Tape, observe, and attention were not restarted. About a minute later the process was scoring all seven books (buy_all closed 4 including one slippage miss, laya_0.6/0.7 closed 7/8, migrate_hold_30s closed 1, top-k books in warmup). Those counts are since the restart. The pressure-fail curve below was not deployed. At 17:14Z the service was still pid 56348, NRestarts 0, ActiveState active.

## Pressure fail curve (offline)

Draft PR: https://github.com/vaanai/MAL/pull/92 (`cursor/fail-pressure-093c`). Not merged. Host output: `/var/lib/mal/paper/fail-pressure-2026-09-25/table.json`.

`P(fail) = sigmoid(-1.270557 + 0.8·log1p(same-slot buy count) + 0.35·log1p(nearby buy SOL))`. Nearby window is 2000 ms, causal, same mint. The intercept is fit on the 16,900 buy-all sends so their mean probability is 0.289. The same intercept is used on the other books. Failed notices are not on the tape, so the slopes are declared, not an MLE, and pressure on landed prints understates competition. Scale 0 is flat 0.289. Scales 0.5 and 2 refit the intercept. Misses stay in n at −0.001 SOL and are not mixed again. Censored holds are outside n. Books are uncapped independent 0.05 SOL attempts at +1s latency.

Window 2026-09-25 06:58:37Z–17:10:40Z. 19,352 creates. Graph loaded (1204 wallets). buyers_8 signals 2600, warmup 19, below 2390, take 191 (one censored, so n=190).

| book | scale | n | mean p | mean SOL | total SOL | miss | no-exit | censored |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| buy_all | 0 | 19352 | 0.2890 | -0.009253 | -179.067189 | 2452 | 3693 | 0 |
| buyers_8_top5 | 0 | 190 | 0.2890 | -0.003951 | -0.750749 | 55 | 3 | 1 |
| migrate | 0 | 612 | 0.2890 | -0.002486 | -1.521623 | 154 | 2 | 3 |
| buy_all | 1 | 19352 | 0.2890 | -0.009445 | -182.778092 | 2452 | 3693 | 0 |
| buyers_8_top5 | 1 | 190 | 0.3365 | -0.003925 | -0.745844 | 55 | 3 | 1 |
| migrate | 1 | 612 | 0.5438 | -0.001720 | -1.052875 | 154 | 2 | 3 |
| buy_all | 2 | 19352 | 0.2890 | -0.009615 | -186.062074 | 2452 | 3693 | 0 |
| buyers_8_top5 | 2 | 190 | 0.3796 | -0.003960 | -0.752397 | 55 | 3 | 1 |
| migrate | 2 | 612 | 0.6343 | -0.001547 | -0.946599 | 154 | 2 | 3 |

At scale 1, buy-all is worse than the flat 28.9% mixture (−179.067 → −182.778) because the hotter sends were the less-bad trades. buyers_8 mean p rises to 0.337 and the total barely moves (−0.751 → −0.746). migrate mean p is 0.544, and that higher fail rate flatters the negative book (−1.522 → −1.053).

## Fill-honesty headline

**Buy every create at T+1s, hold 30s, fail rate 15%, priority 0.001 SOL, misses inside n, size 0.05 SOL.**

n=11339, median **−0.003319 SOL**, mean **−0.010654 SOL**, p10 −0.046083, p90 +0.006640, win rate 13.5%, total **−120.809 SOL**, no-exit 2144, censored 0, miss 1978.

Window 2026-09-25 06:58:37Z–16:43:10Z. 11339 creates, all inside n. 9,108,765 tape lines, 4,104,995 kept prints. 3,927,420 prints excluded from SOL PnL because `quote_is_wsol` is not true (false or missing on PumpSwap). Random 20% (seed 1, 2268 mints): hold_30s n=2268, median −0.003319, mean −0.010327, total −23.421 SOL, win 14.2%, no-exit 434, miss 391. Every exit rule on both books is negative.

The 0% column is the unflattered tape (misses still in n, no failed-entry mixture): hold_30s total **−140.127 SOL**, mean −0.012358. 10% total −127.249. 25% total −107.930. Higher fail rates look better because a failed entry only burns 0.001 SOL and skips the loser. Exit-fail-only (entry kept, exit can revert) at 25% is −230.077 SOL.

Versus the pre-fix book below (fail 0, priority 50k lamports, misses excluded, own impact dropped on the next print, PumpSwap quote was the gross user amount): that window ended 08:12:54Z with 1177 creates, hold_30s n=958, median −0.002035, mean −0.007052, total −6.755, win 18.5%, miss 215 outside n. The new total is not a same-window delta. The window is about ten hours longer. On a per-trade basis the median moved from −0.002035 to −0.003319 and the mean from −0.007052 to −0.010654 at the 15% headline (−0.012358 at 0%). Two priority fees alone rose by 0.0019 SOL. p10 −0.046083 is the 15% mixture of the stuck loss (size + both 0.001 SOL priority fees + rent), not the raw stuck loss.

Latency, same headline knobs, hold_30s: 0.5s total −112.422 (miss 1549), 1s −120.809 (miss 1978), 2s −122.791 (miss 2590), 5s −122.375 (miss 2608). Median stays −0.003319. n=11339 at every latency because misses stay in n and the tape runs past the 30s hold.

Own impact: a later same-venue print keeps our net quote in the constant product and our tokens out of the base. The bonding payable cap is still the tape reserve, so a curve sitting on the 30 SOL virtual floor cannot pay a sell just because the paper buy was added back. PumpSwap quote moves by pool-net (`quote_in + lp_fee` on a buy, constant-product gross on a sell), with the canonical fee when the row only has the user amount.

## Pre-fix headline

**Buy every create at T+1s, hold 30s, fail rate 0, size 0.05 SOL.** The section below is the book before the fill-honesty change. Misses were outside n. Priority was 50_000 lamports.

n=958, median **−0.002035 SOL**, mean **−0.007052 SOL**, p10 −0.052139, p90 +0.020133, win rate 18.5%, total **−6.755 SOL**, no-exit 128, censored 4, miss 215.

Window 2026-09-25 06:58:37Z–08:12:54Z. 1177 creates in window. Every exit rule on both the buy-every book and the 20% random subsample (seed 1, 235 mints) has a negative median and a negative total. p10 is the stuck-loss rug (−0.05213928 SOL = size + both priority fees + token-account rent) on every rule.

Totals are a sum of independent 0.05 SOL trades, not a 1 SOL bankroll and not a concurrency cap.

## Buy every create, T+1s, fail rate 0

| exit | n | median | mean | p10 | p90 | win | total SOL | no-exit | censored | miss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| hold_30s | 958 | -0.002035 | -0.007052 | -0.052139 | 0.020133 | 18.5% | -6.755497 | 128 | 4 | 215 |
| hold_1m | 949 | -0.002266 | -0.010518 | -0.052139 | 0.021867 | 18.2% | -9.981536 | 159 | 13 | 215 |
| hold_2m | 943 | -0.009814 | -0.015348 | -0.052139 | 0.012805 | 15.8% | -14.473173 | 202 | 19 | 215 |
| hold_5m | 904 | -0.038619 | -0.020706 | -0.052139 | 0.013773 | 14.6% | -18.718226 | 270 | 58 | 215 |
| hold_10m | 846 | -0.045707 | -0.023193 | -0.052139 | 0.009652 | 13.4% | -19.620884 | 292 | 116 | 215 |
| hold_15m | 774 | -0.046389 | -0.023122 | -0.052139 | 0.013136 | 13.8% | -17.896705 | 276 | 188 | 215 |
| hold_30m | 604 | -0.047341 | -0.026476 | -0.052139 | 0.008110 | 14.2% | -15.991627 | 243 | 358 | 215 |
| tp50_sl30 | 757 | -0.019061 | -0.015852 | -0.052139 | 0.030838 | 23.5% | -12.000260 | 235 | 205 | 215 |
| tp100_sl50 | 730 | -0.028193 | -0.016200 | -0.052139 | 0.050335 | 21.9% | -11.825928 | 243 | 232 | 215 |
| tp200_sl50 | 719 | -0.029084 | -0.016500 | -0.052139 | 0.049230 | 16.6% | -11.863259 | 243 | 243 | 215 |
| trail30 | 750 | -0.017911 | -0.017476 | -0.052139 | 0.018599 | 16.9% | -13.106824 | 237 | 212 | 215 |
| trail50 | 724 | -0.026181 | -0.019380 | -0.052139 | 0.013624 | 15.5% | -14.030978 | 241 | 238 | 215 |

Random subsample, same latency and fail rate: hold_30s n=193, median −0.002039, mean −0.011242, total −2.170 SOL, win 17.1%, no-exit 29. Longer holds and the stop rules are also negative (worst total is hold_10m at −3.845 SOL).

Fail-rate sensitivity on the buy-every book makes the total more negative. Symmetric 10% / 30% on hold_30s is −9.972 / −13.814 SOL. Exit-fail-only (does not skip losers) is −11.075 / −19.714 SOL. Headline stays fail rate 0.

Latency, hold_30s, fail 0:

| L | n | median | mean | total SOL | win | no-exit | miss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5s | 1004 | -0.002060 | -0.006955 | -6.983 | 20.9% | 152 | 169 |
| 1s | 958 | -0.002035 | -0.007052 | -6.755 | 18.5% | 128 | 215 |
| 2s | 885 | -0.001964 | -0.007688 | -6.803 | 17.5% | 121 | 289 |
| 5s | 861 | -0.001920 | -0.008137 | -7.006 | 15.8% | 113 | 314 |

## What the fill does

Signal time is observe `t_ws`. The book is the last tape print with `t_recv_ms <= T+L`. Create-payload reserves are the anchor only when the tape has not printed yet. Entry size 0.05 SOL. Portal 0.5% then venue fee, both sides (bonding 1.25% flat; PumpSwap canonical SOL mcap tiers from the 20 May 2026 fees page). Priority 0.00005 SOL per side. Rent 0.00203928 SOL, returned when the sell lands, stuck on no-exit. A buy that would complete the bonding curve is a miss, not a partial. Slippage above 15% versus the T quote is a miss, not a loss. An exit after the last tape timestamp is censored (null pnl, outside n). No-exit liquidity is a realized loss and stays inside n.

PumpSwap `BuyEvent` / `SellEvent` reserves on this tape are the pool **before** that trade. The next print's base moves by exactly this print's `token_raw`. The path adds `sol_lamports` to quote and moves base by `token_raw` before a fill can use the print. Bonding-curve reserves are already post-trade and are not advanced again. A PumpSwap row with no `token_raw` is left as recorded.

That correction removes a false T+0.5s win. Migration prints were carrying an ~8,889 SOL buy whose tokens were still inside `base_reserve`. A 0.5s decision landed after that print and before the next one, bought the pre-sniper pool (~68 SOL / 204M tokens), and sold the drained pool (~9,000 SOL / 2M tokens) for about +621 SOL on mint `57LVTCfa…pump`. The trade was already in the row. After the advance, that entry is a slippage miss. The rerun has no realized hold_30s pnl above +1 SOL at 0.5s (median −0.002060, total −6.983).

## Scan

| | |
| --- | ---: |
| lines | 1,169,334 |
| kept prints | 442,336 |
| bad json | 0 |
| unresolved mint | 0 |
| non-wSOL (explicit) | 295,413 |
| other mint | 258,860 |
| skipped kept mint | 172,725 |

Files: `trades-2026-09-25.jsonl.zst`, `trades-2026-09-25T07.jsonl.zst`, `trades-2026-09-25T08.jsonl`. Creates from `observe-2026-09-25.jsonl`. Run under `nice -n 19`. Temp code dir removed. USDC and other non-wSOL pools are not priced as SOL.

The hourly files from the recorder rotation omit `quote_is_wsol`. Those PumpSwap rows are skipped, not assumed to be wSOL (the day file shows a large explicit non-wSOL share). Some late-window migrations can therefore show up as no-exit because the pool print was dropped. The day zst, which covers the window through about 07:48Z, still has the flag.

Tests: `python3 -m unittest tools.test_paper_tape_scoreboard` — 27 OK, including the recorded bonding-curve program-data fixture and the pre-trade PumpSwap reserve case.
