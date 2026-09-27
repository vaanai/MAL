---
cursor:
  subagentId: "bc-49681961-bc79-5d5e-9ae7-c11daa2568af"
---

# Paper signal scan (follow / crowd / curve / clean-launch)

Paper only. Goal is profit vs the PR #76 buy-every-create book. Recorder and `LAB_STATE.md` were not modified.

PR: https://github.com/vaanai/MAL/pull/77 (`cursor/signal-scan-68af`, stacked on #76, merges #75 follow-signal hook).

Host: `mal-core-vnic`. Fingerprint check in `agent-ssh.sh` passed. `nice -n 19` + `ionice -c3`. Temp code dir removed. Outputs: `/var/lib/mal/paper/signal-scan/` (`scan.json`, `scan.md`, `labels.jsonl`, `signal_features.jsonl`). Postgres not used. No `:22`, Tunnel/Access, or recorder changes.

Tests: `python3 -m unittest tools.test_paper_signal_scan` — **17 OK**. Parent scoreboard + wallet-leaderboard tests still pass.

## Headline

**Buy every create at T+1s, hold_30s, fail 0, 0.05 SOL:** n=1133, median **−0.001989 SOL**, mean **−0.007404 SOL**, win 18.1%, total **−8.388 SOL**. OOS (second half of mints) median **−0.001987**. Same book as PR #76; tape is longer.

Window 2026-09-25 **06:58:37Z–08:30:26Z**. 1387 creates in window. 1,428,336 tape lines, 465,681 kept prints, 0 bad JSON.

**Two books have an out-of-sample median above that baseline. Neither is a promote.**

1. **curve/migrate** (first PumpSwap print + 1s, exit `tp50_sl30` picked on train): OOS n=**13**, median **+0.0225 SOL**, win 92%. Full n=28, median +0.0164, total +0.221 SOL. Tiny sample. The default hold_30s on the same 28 fills is median **−0.00044**. Hourly PumpSwap rows without `quote_is_wsol` are dropped, so this book is mostly early-window migrations. After the #76 post-trade reserve fix this is not the false +621 SOL fill. Treat as a lead for the model, not a live rule.
2. **clean/norug**: OOS n=331, median **−0.001829** vs baseline **−0.001987** (Δ +0.00016 SOL, ~0.3% of size). Win rate **8.5% vs 15.2%**. Same fee-loss cluster. Not alpha.

Every other book loses on OOS median. Follow has a higher win rate (~23–30% OOS vs 15%) and a **worse** median (late entry). Crowd `n5_w5s` has a positive *mean* from a fat right tail; median stays negative.

Exit rule is chosen on the first half of unique mints by `signal_t_ms` and reported on the second half. One position per mint per book. Size 0.05 SOL, real fees, rugs kept, fail rate 0. Totals are independent trades, not a bankroll.

## Baseline (recomputed on this tape)

| book | exit | n | median | mean | win | total SOL | no-exit | miss |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| buy_every_create | hold_30s | 1133 | -0.001989 | -0.007404 | 18.1% | -8.388 | 145 | 253 |
| buy_every oos | hold_30s | 573 | -0.001987 | -0.009017 | 15.2% | -5.167 | 61 | 120 |
| random 20% seed 1 | hold_30s | 226 | -0.002036 | -0.003544 | 20.8% | -0.801 | 32 | 51 |
| random oos | hold_30s | 113 | -0.001989 | -0.007685 | 15.0% | -0.868 | 11 | 26 |

p10 on buy-every is still the stuck-loss rug (−0.052139 SOL). Train also picked hold_30s.

## 1. Follow (noisy_v0 board, replayed on this tape)

50 wallets from `leaderboard-noisy_v0.jsonl`. First buy per mint. Copyable = `delta_slot_from_create > 2`. Latency is added to the leader's `t_recv_ms`. Board is scored on the same window (lookahead).

| book | L | n | median | mean | win | total | oos n | oos median | oos win | vs base oos |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| noisy_v0 | 0.5s | 180 | -0.004152 | -0.004263 | 35.0% | -0.767 | 87 | -0.005751 | 29.9% | no |
| noisy_v0 | 1s | 177 | -0.004028 | -0.004191 | 32.2% | -0.742 | 83 | -0.006127 | 22.9% | no |
| noisy_v0 | 2s | 171 | -0.004039 | -0.004381 | 31.0% | -0.749 | 81 | -0.006066 | 24.7% | no |
| noisy_v0 | 5s | 162 | -0.004055 | -0.004637 | 30.9% | -0.751 | 76 | -0.006017 | 23.7% | no |
| copyable | 0.5s | 169 | -0.004189 | -0.004156 | 32.5% | -0.702 | 80 | -0.006070 | 25.0% | no |
| copyable | 1s | 168 | -0.003632 | -0.004089 | 32.1% | -0.687 | 78 | -0.006097 | 21.8% | no |
| copyable | 2s | 164 | -0.004056 | -0.004325 | 31.7% | -0.709 | 77 | -0.006066 | 23.4% | no |
| copyable | 5s | 154 | -0.004020 | -0.004214 | 33.1% | -0.649 | 72 | -0.005287 | 23.6% | no |

Best exit on train: hold_30s every time. Faster copy raises win rate a little and does not flip the median. Fewer rugs than buy-every (no-exit 2–3 vs 145) but the typical fill is a later, more expensive entry. This is a scalper/late-buyer board (median buyer rank 74 on the #75 run). Features for the model: `f_sig_delta_slot_from_create`, `f_sig_wallet_*`.

## 2. Crowd / momentum

≥N distinct wallets buy inside W seconds. Sniper prints (Δslot ≤ 2) dropped. 3083 sniper/bot wallets vetoed on this tape (full-window, same lookahead as the board). Creators excluded.

| book | n | median | mean | win | total | oos n | oos median | oos mean | oos win | vs base oos |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| n3_w1s | 257 | -0.003252 | -0.005990 | 28.8% | -1.539 | 114 | -0.004287 | -0.008120 | 29.8% | no |
| n3_w2s | 284 | -0.003093 | -0.001296 | 28.9% | -0.368 | 129 | -0.004422 | +0.000010 | 30.2% | no |
| n3_w5s | 306 | -0.003188 | -0.004137 | 27.1% | -1.266 | 137 | -0.004422 | -0.003541 | 28.5% | no |
| n5_w1s | 171 | -0.002451 | -0.004332 | 26.9% | -0.741 | 74 | -0.002107 | -0.004022 | 32.4% | no |
| n5_w2s | 184 | -0.003505 | -0.004510 | 38.6% | -0.830 | 82 | -0.003973 | -0.004798 | 37.8% | no |
| **n5_w5s** | 220 | -0.002887 | **+0.005438** | 29.5% | **+1.196** | 94 | -0.003353 | **+0.019223** | 29.8% | no |
| n8_w1s | 88 | -0.009595 | -0.000883 | 48.9% | -0.078 | 30 | -0.014283 | +0.000580 | 43.3% | no |
| n8_w2s | 121 | -0.000895 | -0.003844 | 44.6% | -0.465 | 48 | -0.003154 | -0.003909 | 43.8% | no |
| n8_w5s | 154 | -0.001942 | -0.004139 | 40.9% | -0.637 | 66 | -0.004431 | -0.004781 | 39.4% | no |

n5_w2s / n8_* picked hold_1m or tp50_sl30 on train; the rest hold_30s. **n5_w5s mean is not a median edge** — p90 is only +0.0095 SOL; the positive mean is a short right tail. n8_w2s is the least-bad median on the full sample (−0.0009) and still loses OOS. Features: `f_sig_n`, `f_sig_window_ms`, `f_sig_unique_wallets`.

## 3. Curve progress + migrate

Progress = real tokens bought / 793.1M. Signal = first print that crosses the threshold, fill +1s.

| book | n | median | mean | win | total | best exit | oos n | oos median | oos win | vs base oos |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| p30 | 193 | -0.010668 | -0.009569 | 24.4% | -1.847 | hold_30s | 91 | -0.011817 | 25.3% | no |
| p50 | 74 | -0.008694 | -0.008436 | 21.6% | -0.624 | hold_30s | 34 | -0.007563 | 20.6% | no |
| p70 | 53 | -0.004822 | -0.011403 | 35.8% | -0.604 | hold_1m | 23 | -0.021243 | 13.0% | no |
| p90 | 27 | -0.005023 | -0.015530 | 48.1% | -0.419 | hold_1m | 13 | -0.048196 | 23.1% | no |
| **migrate** | **28** | **+0.016417** | **+0.007882** | **78.6%** | **+0.221** | **tp50_sl30** | **13** | **+0.022548** | **92.3%** | **YES** |

p70/p90 train medians were positive and OOS collapsed (classic overfit on n≈15). Miss rate is high at 50%+ (curve complete / slippage). Migrate hold_30s on the same 28: median −0.00044, win 43% — the flag is the stop grid, not “buy migrate and hold.” n=13 OOS is too small to size. Feature: `f_curve_progress`, `f_migrated`.

## 4. Clean-launch

Filter at T+1s (same clock as baseline). Sniper share = non-creator buy SOL with Δslot ≤ 2 / all non-creator buy SOL at or before T+1s. Prior rug = this creator sold within 60s of a previous mint’s first print.

| book | n | median | mean | win | total | oos n | oos median | oos win | vs base oos |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| s25_norug | 2 | -0.024337 | -0.024337 | 0% | -0.049 | 2 | -0.024337 | 0% | no |
| s50_norug | 3 | -0.001877 | -0.016851 | 0% | -0.051 | 2 | -0.024337 | 0% | no |
| s50 | 10 | -0.026066 | -0.025304 | 0% | -0.253 | 7 | -0.035638 | 0% | no |
| **norug** | 666 | -0.001829 | -0.008346 | 11.1% | -5.559 | 331 | **-0.001829** | 8.5% | **YES*** |

\*Δ vs baseline OOS median is +0.00016 SOL. Win rate is worse. Most creates are first-time creators, so norug ≈ a large subset of buy-every. Sniper-share gates almost never fire by T+1s (n=2–10) and those fills went 0/10. Underpowered books fell back to hold_30s. Features: `f_sig_sniper_share`, `f_sig_creator_prior_rugs`.

## What to hand the LightGBM worker

Do **not** promote any of these as a standalone rule on this ~90 min tape. Join keys are on `signal_features.jsonl` / label `f_*` columns:

- follow: `f_sig_delta_slot_from_create`, `f_sig_wallet_win_rate`, `f_sig_wallet_median_hold_ms`, `f_sig_copyable`
- crowd: `f_sig_n`, `f_sig_window_ms`, `f_sig_unique_wallets`
- curve: `f_curve_progress`, `f_migrated`, `f_ms_from_create`
- clean: `f_sig_sniper_share`, `f_sig_creator_prior_rugs`

Migrated + tp/sl is the only place the raw scan printed a positive OOS median. Encode `f_migrated` and let the model see it with the rest of the tape; do not hard-code “buy every migrate.”

## Honesty

- noisy_v0 board and sniper/bot vetoes use the same short window (lookahead).
- PumpSwap rows without `quote_is_wsol` (hourly files after ~07:48) are not priced as SOL; late migrations can look like no-exit or never enter the migrate book.
- One 0.05 SOL fill per mint per book. Totals are not a 1 SOL bankroll.
- Best exit is picked on train median; p70/p90 show that this still overfits when n is tiny.
- Follow JSONL from #75 was not overwritten; this run replayed the 50 board wallets on the current tape.

Reusable scorers: `tools/paper_signal_{follow,crowd,curve,clean,core,scan}.py`. Host helper: `scripts/mal-core/paper-signal-scan.sh`.
