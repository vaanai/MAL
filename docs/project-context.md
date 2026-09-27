# MAL — Project Context

## Goal
- Profit. Completion only matters if it leads to profit; change the plan when it hinders that.
- Edge = information + latency vs humans and copy-traders (e.g. FOMO app users), not MEV bots. Be the smartest, not the fastest.

## Strategy
- Data "onion" wrapped around LAYA (open-source JEV-style decision engine, target <100ms):
  - Every new token analyzed in real time (filters, token/creator/market state)
  - Tracked wallets (leaderboards, smart money) acted on as soon as they move
  - Wallet relationship graph (creators, promoters, coordinated clusters)
- X/social data is an acceleration layer, not a dependency.
- Edge is unproven: replay/backtest, then paper trading, then controlled live testing with hard risk controls.

## Infrastructure
- Repo / source of truth: github.com/vaanai/MAL (read LAB_STATE.md first).
- Host: Oracle mal-core-0 (mal-core-vnic, aarch64), resized 2026-09-26 to 4 OCPU / 24 GB (still Always Free), account on Pay-As-You-Go with a $30/month email budget alert; small overages OK if needed. Every heavy job must run under systemd memory/CPU limits (OOM crash on 2026-09-25).
- Previous host spec: 2 OCPU / 12 GB, data at /var/lib/mal. LAYA hosted there.
- Access per DEC-011 and tools/oracle_ssh_smoke.md via cloudflared + dedicated agent key (runtime secrets, names only).
- Hard fences: Postgres localhost-only, never guess its password; never open port 22; never weaken Tunnel/Access; no trading or X keys on host; fingerprint mismatch or missing secret means stop and escalate.
- Status: paper-only. Going live needs explicit owner approval.

## Decisions
- Hold time (coordinator's call, owner delegated): no fixed hold. Record each token's full price path from the trade tape and score an exit grid (30s to 30 min, plus take-profit / trailing-stop rules). That grid becomes LAYA's exit-policy training data.
- Copying public leaderboard wallets directly is out (auto copy-bots are in within ~1s). Build our own filtered wallet list from the tape; the wallet graph is mainly a veto against farms and rugs.
- Live feed stays on free public RPC: Helius logsSubscribe firehose costs ~172k credits/hour (budget ~4k/hour) for only ~0.2–2s less lag. For live trading, use targeted Helius subscriptions on held/candidate mints only.
- Helius autoscaling up to $100/month (~20M extra credits) is available if it speeds time-to-profit or raises profit; spend only when it clearly pays back. Owner prefers short replies and no acknowledgement for info-only messages.
- Costs to recover so far: ~$100–200 (Helius Developer, Cursor usage, on-demand). Helius Developer monthly budget: backfill 4M credits, funding enricher 2M, live feed ≤3M, reserve 1M.
- Spend: owner is fine with modest data spend and small live amounts if they meaningfully improve training and testing. Keep it cheap.
- Live bar (proposed): about 7 days of forward paper trading on the tape-based simulator, positive after fees. Then a small live calibration run (tiny fixed size, small bankroll, daily loss cap, kill switch), only after explicit owner approval.
- Graduated tokens pay the same 1.25% PumpSwap venue fee as the bonding curve, so post-migration trading is not cheaper. Direct transaction building (2.48% round trip) beats PumpPortal Local (3.46%); prefer direct routing when execution is built.
- No trusted seed wallets exist yet; derive from data, using public boards only as a seed.

- Promotion rule (tightened after red-team audit): n ≥ 100 out-of-sample trades, ≥5 distinct UTC days with a majority positive, bootstrap 90% CI lower bound of mean SOL/trade > 0, still positive after dropping the top 3 trades, causal top-k selection identical to live, labels at measured live latency, failed fills counted, and must pass under both a flat 15% fail rate and the pressure-conditioned fail model (scale 1).

## Working preferences (owner: BadAIReviews)
- Coordinator manages only; all research, coding, doc writing, and reading of long outputs goes to workers (owner repeated this 2026-09-25). Coordinator reads worker summaries, decides, delegates. Workers own their PRs end to end (push, CI, merge when green) and report only outcomes.
- Worker models: Cursor Grok 4.7, Cursor Grok 4.6, or Composer 2.5 only, pick per task. Fast variants always OFF.
- Drive the project proactively; don't follow the plan blindly.
- Workers may merge PRs once tests and CI are green (owner approved 2026-09-25). Exception: changes touching trading logic, risk controls, execution, secrets, or architecture need coordinator review first (worker sends a ≤10-line summary of what changed and what could go wrong).
- Principle: maximize research progress per manager dollar. Don't spend manager reasoning to maintain activity; spend it freely when a decision can change the direction, validity, safety, or expected value of the project (e.g. spotting leakage, changing targets or promotion rules).
- Flow: worker discovers → summarizes → coordinator evaluates → coordinator changes research direction. Stay active in steering, not passive.
- Daily scoreboard is read by a worker, which returns a compact brief: each signal's result, confidence, robustness tests passed/failed, anomalies, recommendation.

## Decision 2026-09-27

- Search frozen 2026-09-27.
- Post-#95 forward book is void because of lag, and the host copy was missing the split-transaction fix. Window: 2026-09-25T19:00:00Z through 2026-09-27T06:58:12Z. Raw tape stays.
- Clean clock is 7 full UTC days starting 2026-09-28T00:00:00Z. Kill review at 2026-10-05T05:00:00Z.
- No new variants until that review.
- Live size stays 0.05 SOL.

## Decision 2026-09-27 — spend ladder
- Phase 1 (~$75–100/month all-in): month-to-month VPS in the city that wins a check against the Helius and Jito endpoints we will use, plus existing Helius Developer, overage capped at $25. No shreds, no $499 gRPC.
- Next dollar of latency is spent only if a paper curve of the same strategy (about 400 / 800 / 1,400 ms now; 50–200 ms only after the fast feed) is still climbing. $150–300 is the next tier. $1,000+ shreds only after that curve prices them.
- +0.2% gross does not survive either fee stack: ~7.5% at 0.05 SOL with portal and 0.001 SOL priority, or ~2.5% venue-only. Speed is a new test, not a rescue of that book.
- Box is OVH VPS-4, datacenter DE (Frankfurt / Limburg), month-to-month at $28.70 including the required 1-day backup. Locked 2026-09-27 because beta.helius-rpc.com answers in 1.4 ms from an OVH Frankfurt probe and the Frankfurt Jito engine in 2.3 ms. Virginia lands on the Newark edge. Details in [region benchmark](research/vps-region-benchmark.md).
- Oracle keeps the public tape, backfill, training, and archive. The VPS runs a fast listener (Helius preprocessedSubscribe on creates and a watchlist, $25 overage cap) and later a fast paper runner.
- Speed is a new pre-registered test for first-second entries and same-slot wallet follows. It does not reopen the frozen search.
- Fast host is ssh-fast.tradervaan.com (mal-fast-0), OVH VPS-4 in Frankfurt, live 2026-09-27. Same tunnel and agent key as Oracle. No trading keys. Kernel updates wait for a planned reboot.
