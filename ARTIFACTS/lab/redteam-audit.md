---
cursor:
  subagentId: "bc-2a759be3-8276-582f-b362-63147814bb02"
---

# Red-team audit — paper / LAYA / forward (main @ `2db4cfd`)

Independent review of `github.com/vaanai/MAL` `origin/main` (`2db4cfd`, post #84). Did not author this code. Goal: ways **results could be wrong** such that a **promote** fires on noise. Paper-only. No fixes applied.

Scope: LAYA v0, tape fill-sim (#76), signal scan, wallet leaderboard, attention, forward-paper service. Host notes in sibling internal files were used as context; reproductions below are **in-repo fixtures / synthetic**, not a new Oracle run.

Severity: **P0** = the coded promote rule would accept a book that the 7-day live bar would not. **P1** = would flip sign, n, or ranking of a candidate vs a causal/live book. **P2** = biases the scoreboard but currently blocked by n / negative mean.

---

## P0 — Coded promote can fire on one UTC day

`tools/laya_v0.py` `book_stats` / `tools/paper_attention_promote.py` `book_stats`:

```
majority = n_days > 0 and days_positive * 2 > n_days
promote = n >= 100 and mean_ci90[0] > 0 and total_ex_best > 0 and majority
```

There is **no `min_days`**. One positive UTC day ⇒ `n_days=1`, `2>1`, majority passes. Their own test locks this in: `test_promotion_needs_n_and_the_tail_clauses` promotes 100 identical +0.001 SOL trades all on `DAY`.

Reproduction (`PYTHONPATH=. python3` against main):

| book | n | n_days | majority | mean CI90 | ex-best | **promote** |
| --- | ---: | ---: | --- | --- | ---: | --- |
| 100 × +0.001 SOL, one day | 100 | 1 | true | [0.001, 0.001] | 0.099 | **true** |
| 98 × +0.0002 + two +5 SOL, one day | 100 | 1 | true | [0.0002, 0.20] | 5.02 | **true** |

Drop-best removes **one** tail. Winsor 1% (computed, **not** in the promote predicate) also leaves the second +5. CI is a mint-cluster bootstrap (unit is correct) but with 98 tiny winners the lower bound stays > 0.

The 7-day forward bar in project context is **not encoded**. Frozen candidates are currently `promote=false` only because n≪100 / CI crosses 0 — not because the rule requires multiple days.

**Fix:** require `n_days >= 7` (or a pre-registered calendar span), mint-level n, and a second tail clause (winsorized mean CI > 0, or drop-best-2 / 5% trimmed). Do not promote from a single UTC day.

---

## P0/P1 — Frozen / OOS top-k is lookahead; live RankWindow is not

Offline `score_frozen_candidates` and `_selection_table` call `_topk` on the **entire** (fold × point) or **entire holdout pool**. Live `forward_paper._RankWindow` waits until `ceil(1/frac)` scores (100 for 1%, 20 for 5%) and only then takes a score that is in the top frac of the last 500.

Reproduction on 273 scores with three early stars at idx 50–52 (mirrors t30 holdout pool n=273):

- Offline top 1% → **k=3, indices [50, 51, 52]**
- Online RankWindow 1% → **0 takes** (stars arrived during warmup; they then sit in the window and block later takes)

The 15:30Z frozen table’s `t30_top1_hold_30s` n=3 is exactly this estimator. It is not the book `mal-forward-paper.service` would have traded. If those three (or the next 97) go green, the frozen table can print a promote-shaped mean the live service never earned.

Same class: exploratory `_selection_table` top 5%/1% on each walk-forward **test fold** ranks against future scores in that fold.

**Fix:** score frozen and OOS tables with the same `_RankWindow` path as the service (warmup + rolling window). Do not `_topk` a completed pool. Keep the frozen list, but stop treating pool-wide percentiles as a tradable book.

---

## P1 — Fill optimism (own impact off, PumpSwap quote gross, misses dropped, fail=0)

### Own 0.05 SOL is not in the book once anyone else prints

`paper_tape_scoreboard.ASSUMPTIONS["own_impact"]` and `simulate_exit`: sell uses `entry.quote_after` only if `_same_quote` (same print as entry). Any later tape print → observed reserves **without** injecting our buy.

Fixture: same entry, hold_30s, tape_end far enough. No later print: pnl **−0.00183 SOL** (walks our post-buy curve). One later 1 SOL buy on the tape: pnl **−0.00012 SOL**. The later print made the exit look ~0.0017 SOL better because our size is missing from the pool. On a 30s hold, a later print is the common case.

### PumpSwap post-trade adds `user_quote_amount_in`, not pool-net

Decoder (`observe/trade_decode.py`): PumpSwap `sol_lamports` is `user_quote_amount_in` / `user_quote_amount_out`. `pumpswap_post_trade_reserves` does `quote + sol_lamports` on a buy (and `quote - sol_lamports` on a sell). Fees never leave the quote. Reproduction, 1 SOL buy into a 10 SOL pool at 1.25%: quote overstated **0.0125 SOL**; a later 5e12-token sell pays **+67,115 lamports** extra vs net-to-pool reserves.

Sells into that book are optimistic. Buys after a taped buy are slightly pessimistic. Round-trip books that exit on a post-buy print pick up the optimistic side.

Bonding reserves are post-trade and are not advanced again (correct; the +621 SOL migrate bug was this class and is fixed for PumpSwap *pre* vs *post*, not for fees inside the increment).

### Slippage-miss and fail_rate 0

`missed_slippage` / `missed_curve_complete` → `pnl=None`, **outside n**. Fixture: cheap create quote + hot print at T+1s → `missed_slippage`, not a loss. Headline fail rate is 0; 10%/30% sensitivity exists but is not the scoreboard. Public RPC dropped **28.9%** `err != null` notifications (trade-tape.md); landing at `PRIORITY_FEE_LAMPORTS=50_000` on a launch is not that tape.

**Fix:** always apply own buy to the curve until a print that cannot be explained without it, or simulate against a residual book. Increment PumpSwap quote by pool-net (input minus lp/protocol/creator fees from the event, or `after_fee(sol, venue_ppm)`). Count misses as 0 with a separate miss-rate gate, or as a stuck-entry cost. Headline at a non-zero fail rate / realistic priority, not 0 / 50k lamports.

Bonding **1.25% vs EXP-007d Global 95 bps** is the other way (fewer tokens out) — not a false-positive source; keep it, but do not mix it with PumpSwap gross-quote optimism.

---

## P1 — Offline vs online parity

| Clock / model | Offline LAYA labels | Forward service |
| --- | --- | --- |
| Entry delay | `ENTRY_LATENCY_MS = 1000` | cached chain-lag median **plus** recv→decision (`LatencyMeter.measure`) ~1.4–2s |
| Top-k | pool `_topk` | `_RankWindow` warmup |
| Deploy model | `run_models` fits on **all labeled rows** (`in_sample_only: true`) | hot-reloads that file |
| Wallet / creator | full-tape causal `WalletState` | empty until the process has been up (forward-paper.md) |

Replay already measured **0 PnL mismatches** when the same logged latency is re-simmed, and **−0.186 SOL / 20 min** vs the 1s seed (334 vs 351 fills). The model is trained on earlier fills than it will get, then served from an in-sample booster, into a book with cold wallet features.

**Fix:** train labels with the same `LatencyMeter` rule as serve. Fit the served `entry_model.txt` only on `decision_t_ms <= freeze` (or yesterday). Seed forward-paper wallet/creator state from a sealed snapshot, or do not score leader features until warmup. Parity test should include RankWindow and measured delay, not only packet columns.

---

## P1 — Leaderboard / follow / crowd lookahead

`wallet_leaderboard.consume_trades` FIFO-closes on the **full window**, then ranks. Follow JSONL is first buy of those wallets. Closed-mint WR/PnL **uses future sells**. Open lots at window end never become trips (closed-only survivorship). `parse_trade` skips `quote_is_wsol is False` but **keeps missing flags**; fill-sim PumpSwap requires `quote_is_wsol is True`. Hourly files before 08:37Z omitted the flag → leaderboard can treat USDC/unknown quote as SOL while the scoreboard drops those prints (no-exit / never migrate).

Signal-scan `veto_sniper_bot_wallets` uses full-window sniper share and later sells (`short_holds`). Admitted in signal-scan.md. Crowd/follow OOS medians still lost, so this did not promote — it still **selects which wallets exist on the board**.

LAYA v0 `WalletState` / `_on_sell` is time-ordered (good). `_leader_ok` never requires **net** PnL > 0 (WR band + concentration of **positive** mint pnls only).

**Fix:** walk the tape once; a wallet is eligible at t only from trips with `last_sell_ms <= t`. Drop missing `quote_is_wsol` in `parse_trade` (same as fill-sim). Require net closed PnL > 0 for leader flags. Rebuild follow JSONL from that causal board, not the 49-minute noisy_v0 list.

---

## P1 — Attention `paid_at` is time travel

`paper_attention_score.bind_signal` + `dex_paid_at_clock`: entry clock is Dex `paymentTimestamp` when present, not first-seen. Snapshot score: 13/33 paid_at **before first tape print**; median lag vs print 8.7 min. That is buying in the past. Daily genuine job uses first-seen + 1s (better). LAYA `FEATURE_NAMES` does **not** yet include attention; `laya_join.jsonl` is join-by `t_ms` (first-seen) which is the right key — do not switch to `event_t_ms`.

**Fix:** never simulate an entry before `t_first_ms`. Keep `event_t_ms` as a lag feature only after `t_ms <= decision`.

---

## P2 — Survivorship / selection / recorder

- **Censored** exits (tape ended before hold) have null pnl and are **outside n**. Longer rules shrink n; `choose_rule` / `pick_best_exit` then pick on the surviving remainder (train median, min_n 30 / 8). p70/p90 already collapsed OOS (signal-scan.md).
- **Unfinished horizons** at the right edge of every window.
- **Zero-SOL / unpriceable** creates: kept on tape with null price; scoreboard misses. 7/42 portal creates in the first 31 min had `solAmount=0`.
- **Recorder hole** ~07:30–07:48Z (restart to hourly). Attention scoring skipped leftover `trades-2026-09-25.jsonl.zst` to avoid double-count → window starts 07:48Z.
- **Migrate book n=28** lives mostly in the early window where `quote_is_wsol` existed. Train/test split is first/second half of **that** book’s mints, not a later calendar day. Positive OOS median + tp50_sl30 was a train-picked stop on n=13.
- **Time-of-day:** `f_hour_sin/cos` on a single UTC day can memorize session, not hour-of-day.
- **Failed logsSubscribe** dropped; coverage 91–95% of portal creates, not 100%.
- Backfill rows have **null `t_recv_ms`**. Causal gate is recv. Using `block_time` as recv would be optimistic; dropping them under-covers. Synthesize recv as noted in laya-v0.md before joining.

**Fix:** report n_eligible / n_miss / n_censored / n_no_exit beside n. Split by calendar day, not mint-index. Do not pick exits on n<100. Dual-read day+hourly files with a (sig, event_index) key so the 07:30 gap and leftover zst are neither holes nor doubles.

---

## P2 — Statistics / HARKing / multiple testing

Walk-forward (`walk_forward`) is expanding-prefix and **strictly later in decision time** (tested). Labels are hold_30s → train last decision at `cut` uses tape through `cut+30s`, which is **inside the test slice** (repro: 29s overlap on every fold). No embargo of `max(hold, barrier 30m)`.

`choose_rule` / `pick_best_exit` search 12 exits on train. `_selection_table` × 5-ish clocks × 4 fractions × 2 barrier heads × thresholds is dozens of slices; promote is per slice. Frozen three names were picked after the 15:18Z search; freeze is 15:30Z; holdout is **~14 minutes**. `FROZEN_CANDIDATES` in source is that HARKed list.

Bootstrap unit (resample whole mints, seed 1, 1000 draws) is the right cluster for multi-clock mints. Degenerate CI when all pnls equal is expected. **Do not** read exploratory `oos_n=30010` as 30k independent tokens (9.7k creates, several clocks each).

**Fix:** embargo ≥ max label horizon. Pre-register clocks/exits/fractions; freeze before any search on that tape. Holdout = next UTC day, not 14 minutes. Multiple-test the frozen list (or require all frozen names, not the best).

---

## Same-slot / decimals / rent / SOL vs non-SOL (fill)

- Prints sort `(t_recv_ms, slot, event_index)`. `event_index` is **per tx**, not slot order. Same-millisecond burst ⇒ last print is not last on-chain. Fill can sit mid-slot.
- `TOKEN_DECIMALS = 6` hardcoded. Non-6 would mis-scale `spot_sol_per_ui`.
- Rent 2,039,280 returned on successful sell — fine for close-out; ATA reuse not modeled.
- Fill-sim refuses PumpSwap unless `quote_is_wsol is True` (correct). Leaderboard does not (P1 above).
- Completing the curve is a miss, not a partial (correct). Selling more real SOL than the curve has reverts (correct). Rugs/`no_exit_liquidity` stay **inside n** as stuck loss (correct).

---

## What would not flip a promote today

Nothing on the frozen list or the buy-every book is near a real promote: medians are fee-negative, frozen n is 1–4, migrate n=13 OOS. The **danger is the rule + the estimators**, not a green light already printed. Do not treat “we didn’t promote” as “these books are honest.”

---

## Repro

From a checkout of `origin/main`:

```bash
PYTHONPATH=. python3 -c "..."  # eight checks in the audit worktree script
```

All eight assertions above passed on `2db4cfd` (promotion 1-day; two-outlier promote; 29s embargo hole; offline top-k vs 0 online takes; PumpSwap +67115 lamport sell bias; own-impact pnl gap; 1.25% vs 95 bps tokens; slippage miss not in n).

---

## Recommended order of work (do not implement here)

1. Encode `n_days >= 7` + mint n + harder tail into `book_stats` / attention promote (P0).
2. Score frozen/OOS with `_RankWindow`, not pool `_topk` (P0/P1).
3. Own-impact + PumpSwap fee-net reserves; miss/fail in the headline (P1).
4. Train/serve the same delay and the freeze-only booster (P1).
5. Causal leaderboard; drop missing quote flags (P1).
6. Embargo walk-forward; next-day holdout; stop HARKing the frozen list (P2).
