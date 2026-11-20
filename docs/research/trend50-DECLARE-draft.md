# trend50 DECLARE (draft): daily trend swing on the top ~50 Solana memecoins

Status: DRAFT, [T1]. Method 2 of `/data/mal/hunt-1008/ops/reports/2026-11-20/coin-methods-scan.md` (cm1120-top50-trend).
Written before any rule return was computed. No return of this rule, or of any variant, exists as of this text.

## 0. What this is

This is a **desk test** on public data (GeckoTerminal daily OHLCV). It is not a paper book, not a live book, not a DEC-035 lane trade, and no tape, seal or Helius credit is touched. A pass here does not promote anything and does not by itself justify live capital. It only decides whether a forward paper book is worth proposing. Nothing here says the rule makes money.

## 1. Data (step 0)

- Raw pull: `/data2/mal/trend50/raw/` (`MANIFEST.json` with URL, fetch time, status, sha256 and bytes per file; `candidates.json`; `lists/`; `ohlcv/`). Script `/data/mal/hunt-1008/ops/reports/2026-11-20/trend50-pull.py` (sha256 in `trend50-pull.sha256` beside it). MiScusi job #3170 on mal-research-0.
- Window: 2026-05-01 .. 2026-11-19 UTC, plus bars from 2026-04-01 as warm-up. Daily bars, USD, only days with trades (empty days are absent, not zero-filled; an absent day inside a coin's life is a missing day and the coin is not tradable that day).
- Candidates are discovered from GeckoTerminal pool lists (volume, tx count, trending, new pools, five DEXes). **Known limit:** the lists show pools alive today, so dead coins enter only if the pool is still listed; survivorship is reduced, not removed. Dead/delisted pools that appear are kept. Coverage is reported in the PR body and must be read with this limit.
- **Known limit:** GeckoTerminal OHLCV has no liquidity history. Liquidity is a snapshot per pool at fetch time (`liq_usd_snapshot`). Point-in-time liquidity is a declared proxy (section 3).

## 1b. Coverage result (step 0, counts only)

Full report: `/data/mal/hunt-1008/ops/reports/2026-11-20/trend50-coverage.md`. MANIFEST.json sha256 a779c71bf981b46d71cf1122ee0c5734c62545ae1145c41f3093e36c554af3c2 (994 fetches, 0 non-200).

- 929 candidate pools with bars, 642 unique tokens after the exclusion list; 100 coins ever in the point-in-time top 50.
- Universe size per day: May 0, June median 37, July 44, August 39, September to November 50. 130 of 203 days have under 50 coins, 38 days have none.
- Dead or delisted coins kept: 0. All candidates come from today's lists, so survivorship bias is not removed. Median first-bar date is 2026-11-17.
- Consequence: the train split is thin (no universe in May) and the read split is the only well-populated one. Any PASS in section 5 must be read with this survivorship caveat; a historical pool source is needed before a forward book rests on it.

## 2. Universe (point-in-time)

On each date d, using only bars up to and including the close of d-1: rank candidate coins by trailing 30-day sum of USD volume; the universe is the top 50. A coin must also have at least 30 bars history and median daily volume over the trailing 30 bars of at least USD 250,000. Stablecoins, SOL, wrapped SOL and liquid-staking tokens are excluded by symbol list (fixed before the pull in the script's candidate names; list printed in the run record). One pool per token: the pool with the highest summed volume in the window.

## 3. Frozen rule (one rule, no tuning)

- Signal at the close of day t (all conditions): close(t) > mean of close over t-19..t, and close(t)/close(t-20) - 1 > 0, and coin in the universe for t+1.
- Entry: at the **open of day t+1** (the daily open is the only fill price used).
- Exit: signal false at a close (close below the 20-day mean, or 20-day return <= 0) exits at the open of the next day; or a close at least 25% below the entry price exits at the next open. No other exits. Maximum hold unbounded.
- Selection when more than 10 signals are live: the 10 with the highest 20-day return at t, ties by volume. Maximum 10 open positions. Held positions are not rotated out while their signal is true.
- Sizing: **0.25 SOL per position**, fixed, no compounding, no leverage, long only. USD marks use the SOL/USD of that day's daily bar of the SOL/USDC pool (also pulled) for conversion only.
- Fees per side (all fixed in advance): pool fee 0.25% of notional; Jupiter routing and slippage allowance 0.10%; network + priority fee 0.0005 SOL flat; price impact = 2 x trade_usd / L_t, where L_t = liq_usd_snapshot x min(1, vol7_t / vol7_end) (vol7 = trailing 7-day mean daily volume; vol7_end = same at the last bar). Coins with L_t under USD 100,000 are not tradable. A position held to the end of the window is closed at the last bar open and costed like any exit.
- Cost stress: every figure is also reported with all per-side costs doubled.

## 4. Split

- Train: bars before 2026-09-01 (entries 2026-05-01 .. 2026-08-31). Use: pipeline checks and counts only (universe size, trade counts, fills). **No parameter is tuned.** If the rule is changed after any return is seen, this DECLARE is void and a new one with a new name is required.
- Read: 2026-09-01 .. 2026-11-19 entries, **one read, once**. The read is run by one pinned script whose sha256 is added to this file before it runs.

## 5. Pass / kill lines (read split, net SOL after the section 3 costs)

Per-trade net SOL, trades are round trips.

- PASS (desk): all of: at least 100 trades; at least 5 distinct UTC entry days with a majority positive; lower 90% CI bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile); total SOL positive after removing the top 3 trades; and the same four conditions at doubled costs. Positive day-concentration check: total positive after dropping the best date.
- KILL: fewer-than-gate failures are scored as: CI lower bound <= 0, or top-3-removed total <= 0, or total <= 0 at doubled costs -> KILL, the idea row is closed.
- NOT_DECIDABLE: fewer than 100 trades in the read, with no KILL condition triggered on the trades that exist. No extension of the window and no second read.
- PASS means only: propose a forward paper book under its own DECLARE. It is not the DEC-035 / CLAUDE.md promotion gate (that needs forward out-of-sample rows and the owner's word).

## 6. Not computed

No return, hit rate, drawdown or trade count of this or any rule has been computed in this PR.
