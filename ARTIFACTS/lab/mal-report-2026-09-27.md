# Meme Alpha Lab (MAL): baseline analytics report (read-only)

**Prepared:** Sat 2026-09-26, ~5:20 PM PT (host clock 2026-09-27 00:17 UTC).
**Scope:** GitHub `vaanai/MAL` (read-only MCP) and host `mal-core-vnic` (read-only SSH as `lyra-ro`; host key ED25519 `SHA256:Hy68mL6w…Ejs` verified). Nothing was written, restarted or installed on the host, and nothing was posted to GitHub.
**Times:** reported in PT, with UTC in parentheses where the promotion gate's UTC-day rule matters. PT = UTC − 7 h.
**Method:** every number below comes from a host file I copied to `/workspace/mal-analytics/host/`, from `decisions.jsonl` (copied gzip'd), or from a Python calculation over those files (`analyze.py`, `clean.py`, `tables.py`; outputs in `book_stats.json` and `clean_latency_stats.json`). Numbers taken from PR bodies are labelled **(PR claim)**. Where a number couldn't be computed, it says **UNAVAILABLE**.

---

## Key facts up front

1. **The forward paper runner is not running.** No `tools.forward_paper` process and no `mal-forward-paper` cgroup exist on the host. `positions.jsonl` was last written 2026-09-26 06:36 AM PT (13:36:42 UTC). The last decision is from 06:05 AM PT. As of this report it has been down about 10.7 h. I couldn't see the cause because `journalctl` / `systemctl --user` are not readable by `lyra-ro`.
2. **Every forward result after PR #95 is latency-contaminated.** From 11:54 AM PT 9/25 (18:54 UTC) the service ran minutes to hours behind the tape. Hourly median recv→decision was **2.3 h** in the 19:00 UTC hour and 4–37 min in most later hours. Fills were simulated at that lag: the shadow buy_all book's median applied entry latency is **1,147 s** and 93% of its fills are >5 s late. For mig15_top20_tp50_sl30, **100% of fills are >5 s late** (median 28 min). In other words, the lead candidates have **not** actually been forward-tested as designed. The service's in-memory daily P&L reset **12 times** (8 of them on 9/26 UTC), which points to repeated restarts or crashes.
3. **Every book loses money in every ledger and subset I computed.** That includes the realistic-latency pre-#95 window, the honest-latency (≤5 s) subset, the frozen LAYA holdout and the backfill backward holdout. Nothing is promoted. The one "win" in the lineage (signal-scan curve/migrate tp50_sl30, OOS n=13, 92% win) decayed to a 21–42% win rate and a negative mean forward.
4. **Fixed costs dominate at 0.05 SOL.** The modeled round trip at 0.05 SOL is **7.46%** of the stake (3.46% percentage fees plus 2 × 0.001 SOL priority = 4%). The most common trade outcome (the P50–P90 mode) is exactly **−7.457%**: price unchanged, fees paid.
5. **The host is not short of compute. It is misconfigured.** It is now **4 vCPU (Neoverse-N1) / 23.4 GiB RAM**, not the 2 OCPU / 12 GB in LAB_STATE. CPU is about 50% idle. One stuck batch job (`mal-attention-daily`, running 17 h 56 m under a 4 GiB `memory.high` cap) holds **4,027 MB of the 4,095 MB swap** and is thrashing.

---

## A. ENGINEERING PROGRESS

### A1. Merged since #72 (all merges on 9/25–9/26; merge times from the GitHub API)

| PR | Merged (PT) | What it does |
|---|---|---|
| #73 | 9/25 08:39 | Paper trade tape from public-RPC `logsSubscribe` (pump bonding + PumpSwap), hourly zstd-sealed, `mal-trade-tape.service`. |
| #74 | 9/25 08:39 | Docs: honest median/SOL rescore of EXP-002c at 125/350 bps. All rule filters KILL. |
| #75 | 9/25 08:40 | Own smart-wallet leaderboard from the tape (strict + noisy boards, follow-signal JSONL). |
| #76 | 9/25 08:41 | Paper tape scoreboard: curve/constant-product fills, fee stack, exit grid, buy-all baseline. |
| #77 | 9/25 08:41 | Signal scan (follow / crowd / curve / clean-launch) through the #76 scorer. |
| #78 | 9/25 08:55 | LAYA v0: LightGBM create-time/event-clock classifiers, walk-forward, frozen-candidate holdout, daily 04:15 UTC timer. |
| #79 | 9/25 08:42 | Docs: LAB_STATE rewritten for the tape-first direction (**now stale**: still says 2 OCPU/12 GB, open PRs #73–78). |
| #80 | 9/25 08:42 | Historical pump.fun backfill loader from `getBlock` (public or Helius) with credit caps. |
| #81 | 9/25 08:55 | **Forward paper book runner** (`tools.forward_paper`) tailing the live tape, 7 books, risk gate. |
| #82 | 9/25 08:53 | $0 attention tape (DexScreener / pump.fun / GeckoTerminal first-seen poller) plus a daily rescore job. |
| #83 | 9/25 08:56 | Attention promote floor raised to n ≥ 100. |
| #84 | 9/25 09:01 | Config can only tighten risk ceilings (0.05 SOL, 3 concurrent, 0.2 SOL/day, kill switch). |
| #85 | 9/25 09:25 | Attention entries can never precede the first tape print (a causality fix). |
| #86 | 9/25 09:43 | Causal creator/early-buyer funding graph for rug vetoes (`mal-funding-graph.service`). |
| #87 | 9/25 09:45 | Shared promotion gate (n ≥ 100 OOS, ≥5 UTC days with a majority positive, 90% CI low > 0, positive ex-top-3); causal top-k RankWindow. |
| #88 | 9/25 09:48 | Fill honesty: own size stays in reserves, misses in n at the priority fee, 0.001 SOL priority, 15% flat fail. |
| #89 | 9/25 09:53 | Funding scorer uses the shared gate; enricher moves to Helius at 5/s when a key exists. |
| #91 | 9/25 10:56 | Adds the recv→decision hop to offline labels; the gate must also pass under the pressure-fail model. |
| #92 | 9/25 10:18 | Offline pressure-dependent fail curve (intercept fit to the 28.9% failed-log share). |
| #93 | 9/25 10:31 | Helius backfill: skip live-tape hours, gap hour, 8 workers. |
| #94 | 9/25 11:34 | Post-migration PumpSwap swing research book (mig+1/5/15/30/60, attention-after-graduation). |
| #95 | 9/25 11:56 | Graduated books added to forward paper (attn_first_hold_60m, mig15_top20_tp50_sl30); shadow vs ceiling ledgers; freeze 18:25:57 UTC. |
| #96 | 9/25 12:42 | Daily retrain of the mig+15 LightGBM booster used by the forward book. |
| #97 | 9/25 21:13 | Backward holdout on sealed backfill days; one fillable reserve state per signature. |
| #98 | 9/25 21:23 | systemd caps for batch jobs plus a 4 GiB swap on the resized host (4 OCPU / 24 GB). |
| #99 | 9/25 23:12 | Backfill restarts after LAYA; batch CPU/IO priority lowered; attention-daily MemoryHigh 4G / Max 5G. |

Governance note: the bodies of #88, #91, #94, #95, #96, #97 and #98 say "Do not merge" or "Not merged", yet all were merged.

**Open PRs:** **#90** (bound the funding-graph lookup queue, Helius credit cap; the body says the host already runs it at 10/s) and **#100** (attention-daily reads only sealed hours). #100 is the fix for the crash seen in `attention-daily.log` (`FileNotFoundError … trades-2026-09-26T04.jsonl`). It is **not merged**, and the currently running attention-daily job is the one thrashing. No PRs after #100 exist.

### A2. What is running on the host now (ps, cgroups; 5:17 PM PT 9/26)

| Unit / process | State | Evidence |
|---|---|---|
| `mal-trade-tape` (observe.trade_tape) | running 20 h 02 m | 0.11 core average (7,917 CPU-s), 527 MB |
| `mal-observe` (PumpPortal creates) | running 23 h 39 m | 96 MB |
| `mal-attention` (poller) | running 23 h 29 m | 25,138 polls, 677 errors (2.7%), `http_429=0` |
| `mal-funding-graph` | running 19 h 59 m, `--rps 10` | **51,206 of 53,575 log lines are RPC error −32015** ("Transaction version (1) is not supported", i.e. `maxSupportedTransactionVersion` is missing). resolved = 8,994, dropped_stale = 23,315, credits 200,538 / 2,000,000 |
| `mal-pump-backfill` | running 18 h 09 m, 24 workers | 1.21 cores average (78,701 CPU-s), 2.7 GB. It has reached 2026-09-22 22:00 UTC; the log has 56 unique completed hours (58.55M trades, 79,452 creates, **only 17 migrations**, which looks under-detected next to ~415 graduations in 11.4 live hours) |
| `mal-attention-daily` | **stuck: running 17 h 56 m**, started ~11:21 PM PT 9/25 | 4,227 MB RSS under `memory.high` = 4 GiB. **4,027 MB in swap.** Only 2,617 CPU-s used in ~64,600 s wall time. Memory PSI some avg60 = 19.5%. The 9/26 scoreboard was never written (only `genuine.jsonl` / `laya_join.jsonl`) |
| `mal-forward-paper` | **NOT RUNNING** | no process or cgroup; last write 6:36 AM PT 9/26 |
| `mal-laya-v0` timer | last outputs 9:46–9:56 PM PT 9/25; mig15 retrain 10:09 PM PT 9/25; backward holdout 11:09 PM PT 9/25 | `laya-v0/out/*`, `graduated-swing/out/mig15_model.json` |
| Health job | **stale** | `health-latest.json` ts = 2026-09-24 11:34 PM PT (2026-09-25 06:34 UTC), ~42 h old: status ok, data disk 1%, Postgres accepting. It no longer reflects the host |

### A3. Data coverage and quality (tape `stats-*.jsonl`, 10-min windows, UTC days)

| UTC day | hours covered | trades | bonding | PumpSwap | creates seen | creates with a bonding trade | reconnects | chain→recv lag p50 (median of windows) | lag p99 (median / max) |
|---|---|---|---|---|---|---|---|---|---|
| 2026-09-25 (from 06:58) | 15.34 | 14,731,367 | 2,515,770 | 12,215,597 | 20,828 | 90.6% (window min 63.8%) | 169 | 1.44 s | 8.4 s / 36.0 s |
| 2026-09-26 | 23.51 | 21,133,908 | 4,058,885 | 17,075,023 | 34,570 | **87.5%** (window min 62.4%) | **725** | 1.69 s | 17.1 s / 79.5 s |

- **Gaps:** the biggest stats gap is **66.5 min** from 4:17 PM PT 9/25 (23:17 UTC), which matches the resize reboot. The sealed hour T23 is about 1/3 normal size. The forward decision stream has a matching 63-min gap. Other stats gaps are 10–18 min.
- **Missed events (proxy):** ~9–12% of creates have no bonding trade on the tape. The tape log records **581 slot jumps totalling 51,311 slots** (max 370), which is up to ~14% of the ~373k slots in the window (estimate: 41.5 h ÷ 0.4 s/slot). The current process shows reconnects = 485 and slot_jumps = 251 in 20 h. The log also has 500 `ws_closed` (code 1006) and 414 `ws_handshake_rejected` (HTTP 413) events, and reconnects averaged ~11/h on 9/25 against ~31/h on 9/26 (tape stats: 169 in 15.3 h, 725 in 23.5 h).
- **429s:** the grep hits for "429" in the tape, attention and backfill logs were false positives (numbers or `http_429=0`). **Real 429 rate ≈ 0** in the current logs. Funding graph: `limited=0`.
- **Failed on-chain notes:** 34.0M of 75.0M log notes (45%) are `failed_notes` (other people's reverted txs), per the tape heartbeat.
- **Non-WSOL PumpSwap rows** are excluded from SOL P&L (the PR #88 claim is 3.93M such prints in one window; not re-verified).
- **Disk:** sealed tape is 5.5 GB for ~41 h (≈3.2 GB/day); `keep_days=32`.

### A4. Latency (measured)

| Hop | Value | Source |
|---|---|---|
| chain→receive (public RPC) | p50 1.44–1.69 s daily; p99 8–17 s (max window 79.5 s) | tape stats |
| chain→receive, forward service sample | n=168,516, p50 1,520 ms, p99 9,451 ms | `latency.json` |
| recv→decision, pre-#95 (15:00–18:00 UTC 9/25) | hourly p50 **16 / 22 / 52 / 131 ms** | positions.jsonl |
| recv→decision, post-#95 | hourly p50 **120 s to 8,362 s** (e.g. 19:00 UTC 8,362 s; 02:00 639 s; 06:00 2,228 s; 12:00 0.26 s) | positions.jsonl |
| model predict | p50 52 µs, p90 68 µs (n=200) | LAYA scoreboard.md |
| decision→send | 0 (paper; there is no signer) | latency.json |

Knock-on effect: offline training labels now take their "recv→decision hop" from the broken service. `laya-backward.log` shows `hop_ms=24230` and the 04:15 UTC LAYA scoreboard used 3,711 ms, against 26 ms in PR #91. Label realism now depends on a runtime bug.

### A5. Compute headroom

- **Box:** 4 CPUs (Neoverse-N1, 1 thread/core), 23,974 MB RAM, 4 GiB swapfile, 48 GB root (15% used), 147 GB data volume (12% used, 123 GB free).
- **CPU:** load 2.72 / 2.77 / 2.75. vmstat shows ~49–50% idle, 7–12% iowait, 3–6% steal. CPU PSI some avg60 = 4.5%. Backfill is the largest consumer (~1.2 cores).
- **RAM / swap:** 7.6 GB used, 16.3 GB available, but **swap 100% used** with sustained swap-in/out of ~3.5–3.9 MB/s each way. Memory PSI full avg60 = 8.5%; IO PSI full avg60 = 12.2%. Nearly all swap belongs to `mal-attention-daily` hitting its cgroup `memory.high`. This is local cgroup reclaim, not a global RAM shortage.
- **Verdict:** at 4 vCPU / 24 GB, the box is **not** the bottleneck. The problems are one job whose in-memory design exceeds its cgroup cap, a forward runner that fell behind even while ~2 cores sat idle, and heavy batch jobs sharing IO. The old 2 OCPU / 12 GB box was probably contended on 9/25 (the mig15 train and LAYA fits ran while the forward lag began), but the lag continued after the resize.

### A6. What replaced LAYA, and was the switch good?

- **What decides now:** the decision engine is `tools.forward_paper serve` with `forward-paper.json`, running 9 books. LAYA LightGBM models remain *inputs* to 4 of them (laya_0.6, laya_0.7, t30_top1_hold30, buyers8_top5_ladder2x). The two leads are:
  - **attn_first_hold_60m:** rule-only. Buy at max(first attention sighting after graduation, first print) + latency, hold 60 min.
  - **mig15_top20_tp50_sl30:** a LightGBM (37 features, retrained daily; last fit 797 labeled rows, 167 positives, tape through 05:06 UTC 9/26). It is ranked by a causal RankWindow; the book takes the top 20% and exits at +50% / −30% with a 4 h cap.
  - Risk gate: 0.05 SOL, 3 concurrent, 0.2 SOL/day loss cap, kill file.
- **Was it good?** There is **no evidence** yet. Every book is negative (Part C), and the runner is currently down.

---

## B. RESEARCH PROGRESS

### B1. Pre-registered hypotheses and their status

| Hypothesis / candidate | Registered | Status | Evidence |
|---|---|---|---|
| EXP-002b/c rules v1/v2 | yes (EXP docs) | **KILLED** | `_honest-rescore-2026-09-2{0,1}_table.md`: all KILL at 350 bps. The 9/20 L3_minus_v2 KEEP_WATCHING came from one moon and was killed on 9/21. The **random** control was "KEEP_WATCHING" on 9/21, which confirms the noise level. |
| EXP-004 graph | yes | killed / mostly killed (not re-examined) | host `_exp004*` files; LAB_STATE |
| EXP-005 L3 smart-wallet follow | yes | **KILLED cross-day** (LAB_STATE). The 9/21 report was DIRECTIONAL_NON_KILL with priced_60s_n = 18 of 2,578 arm (tiny) | `_exp005-oracle-2026-09-21_report.md` |
| EXP-006 fill-sim | yes | **FAIL / INCOMPLETE** | `_exp006-oracle-2026-09-20_report.md` Overall FAIL; n = 9–44 |
| H-edge (edge after honest fees) | LAB_STATE open-hypothesis table | **Not supported**; the kill condition ("no median lift in paper + forward") is effectively met | Part C |
| H-graph (funding graph beats spine) | LAB_STATE + #86 frozen veto | **Untestable right now**: 96% of enricher log lines are version errors | A2 |
| H-rpc (free RPC is enough) | LAB_STATE | **Marginal**: 0 real 429s, but 9–12% of creates lack a bonding trade and reconnects are ~31/h | A3 |
| LAYA frozen trio (buyers_8 top5 ladder, T+30 top1 hold_30s, migrate hold_30s), frozen 15:30 UTC 9/25 | **yes, genuinely pre-registered** | **Failing** on both the forward and backward holdouts | Forward holdout (LAYA scoreboard, 2 UTC days): n = 203 / 194 / 507, means −0.0036 / −0.0060 / −0.0026 SOL, CIs entirely below 0 for all three. Backward (sealed 9/25 backfill): n = 104 / 102 / 281, all negative |
| Graduated leads (attn_first_hold_60m, mig15_top20_tp50_sl30), frozen 18:25:57 UTC 9/25 | **yes, but chosen as "nearest misses" out of ~72 scored graduated variants** on the same 11.4 h window | **Failing / not properly tested** | In-sample (graduated scoreboard): attn n = 70, mean +0.0134, median −0.042, ex-top-3 −0.89; mig15 n = 26, mean +0.0026. Backward holdout mig15: n = 34 sealed day, mean −0.0013; n = 50 across all scored hours, mean −0.0002. Forward: see C |
| Funding-graph rug veto | frozen rule (#86) | **Preliminary only** (433 creates, 1 veto) | `funding-graph/score-preliminary.json` |
| Attention event books | daily job | **No promote.** All kinds n < 30 on 9/25; the 9/26 run never finished | `attention/daily/2026-09-25/scoreboard.md` |

### B2. Forward / out-of-sample vs history

Nothing holds out of sample. Every signal that looked positive in-sample either had tiny n or depended on the tail, and every one is negative on forward or backward data:
- signal-scan **curve/migrate tp50_sl30**: OOS n = 13, win 92.3%, mean +0.0155. Forward as `migrate_tp50_sl30`: realistic-latency pre-#95 n = 50, win 38%, mean −0.0067. Honest-latency pooled n = 97, win 42%, mean −0.0036. Shadow n = 287, win 21%, mean −0.0078.
- **attn hold_60m**: in-sample mean +0.0134 (n = 70). Forward shadow n = 40, mean −0.0182; honest-latency n = 7, mean −0.0241.
- **mig15 top20**: in-sample +0.0026 (n = 26). Backward −0.0002 (n = 50). Forward shadow n = 13 closed, **0 winners**, mean −0.0395, **entry latency 12 s to 159 min, median 28 min**.

### B3. Overfitting risk

- **How many variants:** at least **~270 scored book variants**, before counting exit-grid multiplicity. Counted from host scoreboards: signal-scan 26 books, each picking its best exit from a grid on the first half; LAYA OOS point×take table 70 rows plus 84 barrier/ladder rows; graduated swing 42 buy-all point×rule rows plus 30 model top-k rows; attention ~12 event kinds; forward 9 books. The lead candidates are the maxima of that search. With about 270 tries on 1–2 days of tape, a few positive means with n ≈ 20–70 are exactly what chance produces.
- **Does the gate use unseen data?** Structurally, yes. The gate requires n ≥ 100 **OOS**, ≥5 UTC days with a majority positive, a 90% CI lower bound > 0, and positive ex-top-3, under both the flat-15% and pressure-fail models. Candidates were frozen with timestamps before their forward slices, and the backward holdout uses backfill hours never used in fitting. That design is sound. But: (a) the forward "OOS" slice is latency-corrupted after #95; (b) only 2 partial UTC days exist, so the day rule can't pass before ~9/30 even if everything worked; (c) the exploratory LAYA walk-forward still uses the whole tape, and the lead candidates were picked from exploratory tables on the same window as their in-sample stats.
- **Re-tuning on scored days?** The mig15 daily retrain fits on all labeled mig+15 decisions **up to tape end** and the forward book scores only *later* decisions after hot-reload, so it is walk-forward by construction (fit at 05:09 UTC on data through 05:06 UTC). The book identity (point, top 20%, tp50/sl30) was chosen once and frozen. I found **no evidence** of a book being re-tuned on the days it is scored. One subtle leak-like issue: the latency hop fed into training labels is recalibrated from forward-runner telemetry, which is now polluted (A4).
- **Human-behavior edge?** **No evidence.** The attention book (human-visible surfaces), wallet-follow (copy-trader flow) and crowd signals all lose on median and on OOS mean. Where means are positive, they come from 1–3 outliers (ex-top-3 negative in every case). The results look like fee drag plus a random fat right tail.

---

## C. ECONOMIC VALIDATION

**Source:** `/var/lib/mal/paper/forward-paper/positions.jsonl` (70,172 rows; 0 duplicate keys) and `decisions.jsonl` (474,393 rows). Forward decisions span **8:34 AM PT 9/25 → 6:05 AM PT 9/26** (2026-09-25 15:34 UTC → 2026-09-26 13:05 UTC), about 21.5 h, with a 63-min reboot gap. That is **2 partial UTC days** in total.

**Definitions:**
- Size is a fixed 0.05 SOL per trade. "ret" = pnl_sol / 0.05.
- A "trade" is a closed position (realized or `no_exit_liquidity` rug).
- "Failed exec" = a `miss` event (slippage cap exceeded, no state, curve complete, no liquidity). It costs the 0.001 SOL priority fee and is counted in total P&L and EV/attempt. The forward runner applies **no random landing-failure rate** (0%).
- "Censored" = opened but not closed when the runner stopped (excluded from returns).
- Max drawdown = peak-to-trough of cumulative SOL, with closes ordered by exit time.
- Time in trade = exit − entry.
- Capital utilization = Σ(0.05 × hold time) ÷ window, i.e. average SOL deployed, then as a fraction of the 3 × 0.05 = 0.15 SOL ceiling.
- Fees are **estimated** per trade from the code's constants (0.5% portal + 1.25% venue each side, 0.001 SOL priority each side; rugs pay the buy fee plus both priorities), because the rows don't carry a fee breakdown.
- Entry impact = executed price vs `entry_spot_sol` after fees.
- Top-X% "share": because every total is negative, the ratio of top-trade P&L to total is meaningless (negative), so I report the SOL the top trades contributed and the total without them.

**Which section to trust:**
- **C1** (pre-#95, realistic latency) and **C4** (honest-latency subset) are the only ones that test the strategies as designed.
- **C2/C3** (after #95) mostly test "enter minutes to hours late".
- C4 is selection-biased toward low-load periods.

**⚠ Small n:** the leads are tiny. mig15 has **13 closed** trades in shadow and **0** at honest latency; attn has **40** in shadow and **7** at honest latency. Every book is below the gate's n ≥ 100 except the buy_all / laya / t30 / migrate shadow books, and none has 5 UTC days.

#### C1. Pre-#95 single ledger (2026-09-25 15:34–18:54 UTC = 08:34–11:54 PT), realistic latency

| book | window (UTC) | opportunities (evals / mints) | filtered signals | fills | closed n | censored open | failed exec (miss) | fail rate | win rate | mean ret | median ret | avg winner SOL | avg loser SOL | PF | EV/attempt SOL | total P&L SOL (incl. miss fees) | ex-top-3 SOL | mean 90% CI SOL | max DD SOL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| migrate_tp50_sl30 | 09-25 15:36Z → 09-25 18:49Z | n/a (capped ledger) | — | 55 | 50 | 5 | 8 | 12.7% | 38.0% | -13.4% | -6.6% | 0.0237 | -0.0253 | 0.57 | -0.00590 | -0.342 | -0.480 | -0.0133 .. 0.0003 | 0.342 |
| migrate_hold_30s | 09-25 15:36Z → 09-25 18:49Z | n/a (capped ledger) | — | 96 | 96 | 0 | 19 | 16.5% | 20.8% | -11.1% | -5.5% | 0.0101 | -0.0097 | 0.27 | -0.00481 | -0.553 | -0.634 | -0.0083 .. -0.0030 | 0.553 |
| t30_top1_hold30 | 09-25 15:41Z → 09-25 18:53Z | n/a (capped ledger) | — | 47 | 47 | 0 | 3 | 6.0% | 38.3% | -12.0% | -7.2% | 0.0098 | -0.0158 | 0.39 | -0.00569 | -0.284 | -0.355 | -0.0104 .. -0.0018 | 0.342 |
| buyers8_top5_ladder2x | 09-25 16:00Z → 09-25 18:51Z | n/a (capped ledger) | — | 38 | 34 | 4 | 4 | 9.5% | 17.6% | -18.0% | -36.6% | 0.0506 | -0.0218 | 0.50 | -0.00817 | -0.310 | -0.506 | -0.0169 .. -0.0000 | 0.458 |
| laya_0.7 | 09-25 15:34Z → 09-25 18:50Z | n/a (capped ledger) | — | 201 | 201 | 0 | 22 | 9.9% | 30.8% | -9.7% | -10.8% | 0.0243 | -0.0179 | 0.61 | -0.00448 | -1.000 | -1.480 | -0.0084 .. -0.0010 | 1.204 |
| laya_0.6 | 09-25 15:34Z → 09-25 18:51Z | n/a (capped ledger) | — | 171 | 171 | 0 | 19 | 10.0% | 27.5% | -12.3% | -12.3% | 0.0255 | -0.0182 | 0.53 | -0.00565 | -1.073 | -1.499 | -0.0101 .. -0.0020 | 1.093 |
| buy_all | 09-25 15:34Z → 09-25 18:54Z | n/a (capped ledger) | — | 623 | 610 | 13 | 33 | 5.0% | 19.8% | -18.7% | -5.8% | 0.0368 | -0.0208 | 0.44 | -0.00894 | -5.749 | -6.694 | -0.0121 .. -0.0066 | 5.861 |

#### C1 — risk, time, cost, distribution

| book | ret std | worst 1% (P1) | worst 5% (P5) | mean of worst 5% | P25 | P50 | P75 | P90 | P95 | P99 | top1% trades SOL | top5% SOL | top10% SOL | total ex top10% SOL | hold median s | hold mean s | avg SOL deployed | util vs 0.15 SOL | est. fees SOL (total / per trade) | entry impact median / P90 | applied latency P50 / P90 s | fills >5 s latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| migrate_tp50_sl30 | 0.586 | -106.1% | -101.5% | -105.3% | -62.1% | -6.6% | 37.3% | 52.6% | 55.7% | 118.5% | 0.084 | 0.146 | 0.200 | -0.535 | 143 | 438 | 0.093 | 62.3% | 0.184 / 0.00368 | 0.1% / 3.9% | 1.6 / 17.7 | 14.5% |
| migrate_hold_30s | 0.314 | -95.6% | -85.2% | -91.3% | -9.1% | -5.5% | -1.3% | 15.4% | 25.3% | 59.5% | 0.055 | 0.131 | 0.180 | -0.714 | 30 | 30 | 0.012 | 8.2% | 0.355 / 0.00370 | 0.1% / 6.9% | 1.7 / 40.7 | 21.9% |
| t30_top1_hold30 | 0.362 | -97.2% | -79.3% | -91.4% | -24.4% | -7.2% | 9.9% | 26.3% | 43.6% | 50.0% | 0.025 | 0.073 | 0.107 | -0.389 | 30 | 30 | 0.006 | 4.1% | 0.173 / 0.00369 | 0.1% / 0.1% | 1.8 / 244.1 | 17.0% |
| buyers8_top5_ladder2x | 0.631 | -102.9% | -90.6% | -100.3% | -47.3% | -36.6% | -14.5% | 99.0% | 117.5% | 148.2% | 0.081 | 0.141 | 0.254 | -0.560 | 10 | 324 | 0.054 | 35.7% | 0.124 / 0.00364 | 0.9% / 7.2% | 1.6 / 80.8 | 18.4% |
| laya_0.7 | 0.636 | -108.1% | -92.7% | -103.4% | -38.2% | -10.8% | 5.4% | 41.5% | 65.3% | 207.2% | 0.502 | 0.956 | 1.200 | -2.177 | 30 | 30 | 0.026 | 17.1% | 0.746 / 0.00371 | 0.1% / 0.3% | 1.7 / 4.5 | 7.5% |
| laya_0.6 | 0.634 | -104.3% | -90.2% | -100.4% | -38.1% | -12.3% | 3.5% | 28.7% | 76.7% | 208.1% | 0.359 | 0.813 | 1.021 | -2.075 | 30 | 30 | 0.022 | 14.4% | 0.630 / 0.00369 | 0.1% / 0.6% | 1.7 / 4.6 | 8.8% |
| buy_all | 0.813 | -108.1% | -104.3% | -105.7% | -78.3% | -5.8% | -3.0% | 28.7% | 78.1% | 317.5% | 1.716 | 3.381 | 4.111 | -9.827 | 30 | 30 | 0.076 | 50.9% | 2.214 / 0.00363 | 0.2% / 0.2% | 1.6 / 1.9 | 0.5% |

#### C2. Shadow (promotion) ledger, uncapped (2026-09-25 18:54 UTC → 2026-09-26 13:05 UTC = 11:54 AM PT Sep 25 → 6:05 AM PT Sep 26) — LATENCY-CONTAMINATED

| book | window (UTC) | opportunities (evals / mints) | filtered signals | fills | closed n | censored open | failed exec (miss) | fail rate | win rate | mean ret | median ret | avg winner SOL | avg loser SOL | PF | EV/attempt SOL | total P&L SOL (incl. miss fees) | ex-top-3 SOL | mean 90% CI SOL | max DD SOL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mig15_top20_tp50_sl30 | 09-25 19:15Z → 09-26 13:01Z | 374 / 361 | 57 | 50 | 13 | 37 | 7 | 12.3% | 0.0% | -79.1% | -89.8% | — | -0.0395 | 0.00 | -0.02606 | -0.521 | -0.465 | -0.0459 .. -0.0325 | 0.521 |
| attn_first_hold_60m | 09-25 18:57Z → 09-26 13:00Z | 153 / 101 | 153 | 78 | 40 | 38 | 24 | 23.5% | 12.5% | -36.3% | -34.6% | 0.0411 | -0.0266 | 0.22 | -0.01173 | -0.750 | -0.903 | -0.0264 .. -0.0094 | 0.750 |
| migrate_tp50_sl30 | 09-25 18:55Z → 09-26 13:04Z | 500 / 478 | 500 | 378 | 287 | 91 | 121 | 24.2% | 21.3% | -15.6% | -7.5% | 0.0236 | -0.0163 | 0.39 | -0.00579 | -2.364 | -2.503 | -0.0101 .. -0.0056 | 2.373 |
| migrate_hold_30s | 09-25 18:55Z → 09-26 13:04Z | 499 / 477 | 499 | 374 | 372 | 2 | 121 | 24.4% | 7.8% | -7.1% | -7.0% | 0.0211 | -0.0056 | 0.32 | -0.00291 | -1.434 | -1.679 | -0.0047 .. -0.0022 | 1.443 |
| t30_top1_hold30 | 09-25 18:59Z → 09-26 12:59Z | 24023 / 23900 | 370 | 268 | 265 | 3 | 95 | 26.2% | 12.1% | -16.2% | -7.5% | 0.0248 | -0.0126 | 0.27 | -0.00623 | -2.243 | -2.597 | -0.0109 .. -0.0051 | 2.260 |
| buyers8_top5_ladder2x | 09-25 19:02Z → 09-26 13:05Z | 1936 / 1936 | 115 | 89 | 64 | 25 | 25 | 21.9% | 9.4% | -24.7% | -14.0% | 0.0344 | -0.0172 | 0.21 | -0.00915 | -0.814 | -0.953 | -0.0169 .. -0.0073 | 0.814 |
| laya_0.7 | 09-25 18:54Z → 09-26 13:05Z | 140724 / 24001 | 7400 | 1849 | 1834 | 15 | 556 | 23.1% | 10.7% | -16.7% | -7.5% | 0.0318 | -0.0132 | 0.29 | -0.00666 | -15.914 | -16.643 | -0.0094 .. -0.0072 | 16.023 |
| laya_0.6 | 09-25 18:54Z → 09-26 13:05Z | 140520 / 23996 | 22896 | 6282 | 6247 | 35 | 1109 | 15.0% | 5.2% | -18.1% | -7.5% | 0.0362 | -0.0115 | 0.17 | -0.00783 | -57.571 | -57.985 | -0.0096 .. -0.0085 | 57.579 |
| buy_all | 09-25 18:54Z → 09-26 13:05Z | 23322 / 23205 | 23322 | 21069 | 20996 | 73 | 2253 | 9.7% | 2.2% | -34.5% | -7.5% | 0.0522 | -0.0188 | 0.06 | -0.01569 | -364.755 | -366.090 | -0.0176 .. -0.0169 | 364.755 |

#### C2 — risk, time, cost, distribution

| book | ret std | worst 1% (P1) | worst 5% (P5) | mean of worst 5% | P25 | P50 | P75 | P90 | P95 | P99 | top1% trades SOL | top5% SOL | top10% SOL | total ex top10% SOL | hold median s | hold mean s | avg SOL deployed | util vs 0.15 SOL | est. fees SOL (total / per trade) | entry impact median / P90 | applied latency P50 / P90 s | fills >5 s latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mig15_top20_tp50_sl30 | 0.305 | -104.0% | -104.0% | -104.0% | -104.0% | -89.8% | -56.4% | -39.0% | -29.8% | -19.9% | -0.009 | -0.009 | -0.028 | -0.486 | 827 | 1311 | 0.014 | 9.4% | 0.040 / 0.00309 | -0.2% / 0.0% | 1689.1 / 4570.5 | 100.0% |
| attn_first_hold_60m | 0.657 | -104.0% | -104.0% | -104.0% | -97.2% | -34.6% | -7.4% | 6.6% | 65.9% | 160.6% | 0.110 | 0.144 | 0.205 | -0.931 | 3600 | 3600 | 0.114 | 76.1% | 0.139 / 0.00347 | 0.1% / 1.4% | 165.3 / 3245.9 | 73.1% |
| migrate_tp50_sl30 | 0.476 | -104.0% | -102.8% | -104.0% | -40.2% | -7.5% | -2.9% | 42.7% | 54.5% | 99.1% | 0.260 | 0.680 | 1.005 | -3.248 | 896 | 1009 | 0.221 | 147.1% | 1.049 / 0.00366 | -0.0% / 2.7% | 26.8 / 984.1 | 82.0% |
| migrate_hold_30s | 0.300 | -97.2% | -34.8% | -70.0% | -7.5% | -7.0% | -5.6% | -2.5% | 5.8% | 80.7% | 0.409 | 0.600 | 0.605 | -1.918 | 30 | 30 | 0.009 | 5.7% | 1.388 / 0.00373 | -0.0% / 2.5% | 27.1 / 990.8 | 82.4% |
| t30_top1_hold30 | 0.588 | -108.1% | -108.1% | -108.1% | -23.3% | -7.5% | -7.5% | 6.3% | 23.8% | 65.6% | 0.449 | 0.694 | 0.786 | -2.934 | 30 | 30 | 0.006 | 4.1% | 0.968 / 0.00365 | 0.1% / 0.9% | 99.0 / 2085.8 | 79.5% |
| buyers8_top5_ladder2x | 0.481 | -108.1% | -108.1% | -108.1% | -46.4% | -14.0% | -7.5% | -7.5% | 43.5% | 129.0% | 0.088 | 0.187 | 0.203 | -0.992 | 1848 | 1436 | 0.071 | 47.1% | 0.229 / 0.00358 | 3.3% / 64.8% | 160.9 / 2309.5 | 80.9% |
| laya_0.7 | 0.611 | -108.1% | -108.1% | -108.1% | -21.2% | -7.5% | -7.5% | 1.8% | 27.1% | 148.3% | 3.593 | 5.672 | 6.224 | -21.582 | 30 | 30 | 0.042 | 27.9% | 6.694 / 0.00365 | 0.2% / 2.0% | 313.5 / 3887.9 | 79.6% |
| laya_0.6 | 0.496 | -108.1% | -108.1% | -108.1% | -7.5% | -7.5% | -7.5% | -7.5% | 1.1% | 99.0% | 8.243 | 11.825 | 10.912 | -67.374 | 30 | 30 | 0.142 | 95.0% | 22.727 / 0.00364 | 0.2% / 0.8% | 819.1 / 3696.5 | 87.6% |
| buy_all | 0.629 | -108.1% | -108.1% | -108.1% | -108.1% | -7.5% | -7.5% | -7.5% | -7.5% | 41.3% | 21.631 | 21.707 | 17.792 | -380.295 | 30 | 30 | 0.479 | 319.1% | 73.453 / 0.00350 | 0.2% / 0.2% | 1147.2 / 5486.5 | 92.9% |

#### C3. Ceiling (execution) ledger: 3 concurrent, 0.2 SOL/day loss cap, same window as C2 — LATENCY-CONTAMINATED

| book | window (UTC) | opportunities (evals / mints) | filtered signals | fills | closed n | censored open | failed exec (miss) | fail rate | win rate | mean ret | median ret | avg winner SOL | avg loser SOL | PF | EV/attempt SOL | total P&L SOL (incl. miss fees) | ex-top-3 SOL | mean 90% CI SOL | max DD SOL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mig15_top20_tp50_sl30 | 09-25 19:15Z → 09-26 13:01Z | n/a (capped ledger) | — | 22 | 9 | 13 | 4 | 15.4% | 0.0% | -69.6% | -79.8% | — | -0.0348 | 0.00 | -0.02441 | -0.317 | -0.264 | — (n<10) | 0.317 |
| attn_first_hold_60m | 09-25 18:57Z → 09-26 12:43Z | n/a (capped ledger) | — | 39 | 17 | 22 | 9 | 18.8% | 11.8% | -51.8% | -46.4% | 0.0308 | -0.0334 | 0.12 | -0.01727 | -0.449 | -0.500 | -0.0362 .. -0.0153 | 0.449 |
| migrate_tp50_sl30 | 09-25 18:55Z → 09-26 13:01Z | n/a (capped ledger) | — | 95 | 74 | 21 | 28 | 22.8% | 25.7% | -18.0% | -14.2% | 0.0235 | -0.0202 | 0.40 | -0.00682 | -0.695 | -0.838 | -0.0140 .. -0.0037 | 0.705 |
| migrate_hold_30s | 09-25 18:55Z → 09-26 13:04Z | n/a (capped ledger) | — | 180 | 179 | 1 | 69 | 27.7% | 12.3% | -5.9% | -6.3% | 0.0264 | -0.0071 | 0.52 | -0.00242 | -0.600 | -0.897 | -0.0054 .. -0.0003 | 0.771 |
| t30_top1_hold30 | 09-25 18:59Z → 09-26 12:59Z | n/a (capped ledger) | — | 111 | 111 | 0 | 21 | 15.9% | 19.8% | -15.5% | -7.5% | 0.0134 | -0.0130 | 0.25 | -0.00668 | -0.881 | -0.968 | -0.0106 .. -0.0050 | 0.897 |
| buyers8_top5_ladder2x | 09-25 19:02Z → 09-26 13:00Z | n/a (capped ledger) | — | 52 | 38 | 14 | 16 | 23.5% | 15.8% | -19.4% | -17.0% | 0.0344 | -0.0180 | 0.36 | -0.00711 | -0.384 | -0.532 | -0.0167 .. -0.0024 | 0.403 |
| laya_0.7 | 09-25 18:54Z → 09-26 12:34Z | n/a (capped ledger) | — | 378 | 374 | 4 | 112 | 22.9% | 24.9% | -2.4% | -7.5% | 0.0344 | -0.0130 | 0.88 | -0.00116 | -0.563 | -1.619 | -0.0046 .. 0.0030 | 0.904 |
| laya_0.6 | 09-25 18:54Z → 09-26 12:31Z | n/a (capped ledger) | — | 215 | 212 | 3 | 63 | 22.7% | 19.3% | -18.5% | -8.5% | 0.0275 | -0.0181 | 0.36 | -0.00737 | -2.026 | -2.435 | -0.0127 .. -0.0058 | 2.033 |
| buy_all | 09-25 18:54Z → 09-26 12:30Z | n/a (capped ledger) | — | 145 | 145 | 0 | 40 | 21.6% | 14.5% | -37.3% | -10.0% | 0.0312 | -0.0271 | 0.19 | -0.01484 | -2.746 | -3.059 | -0.0230 .. -0.0138 | 2.746 |

#### C3 — risk, time, cost, distribution

| book | ret std | worst 1% (P1) | worst 5% (P5) | mean of worst 5% | P25 | P50 | P75 | P90 | P95 | P99 | top1% trades SOL | top5% SOL | top10% SOL | total ex top10% SOL | hold median s | hold mean s | avg SOL deployed | util vs 0.15 SOL | est. fees SOL (total / per trade) | entry impact median / P90 | applied latency P50 / P90 s | fills >5 s latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mig15_top20_tp50_sl30 | 0.324 | -104.0% | -104.0% | -104.0% | -104.0% | -79.8% | -42.7% | -33.9% | -25.7% | -19.1% | -0.009 | -0.009 | -0.009 | -0.305 | 810 | 1192 | 0.009 | 5.9% | 0.029 / 0.00318 | -0.2% / 0.1% | 1018.8 / 3734.3 | 100.0% |
| attn_first_hold_60m | 0.551 | -103.8% | -103.7% | -103.8% | -99.3% | -46.4% | -27.9% | 20.7% | 58.6% | 65.1% | 0.033 | 0.033 | 0.062 | -0.502 | 3600 | 3600 | 0.049 | 32.4% | 0.057 / 0.00334 | 0.1% / 1.6% | 33.6 / 741.2 | 64.1% |
| migrate_tp50_sl30 | 0.535 | -104.0% | -102.3% | -103.8% | -45.3% | -14.2% | 0.0% | 42.5% | 45.5% | 104.4% | 0.114 | 0.195 | 0.282 | -0.949 | 318 | 749 | 0.042 | 28.2% | 0.269 / 0.00363 | -0.1% / 13.4% | 18.4 / 855.9 | 68.4% |
| migrate_hold_30s | 0.419 | -101.4% | -64.3% | -87.7% | -7.5% | -6.3% | -5.1% | 1.1% | 23.5% | 168.8% | 0.292 | 0.543 | 0.581 | -1.112 | 30 | 30 | 0.004 | 2.7% | 0.670 / 0.00374 | 0.0% / 9.7% | 14.0 / 639.6 | 70.6% |
| t30_top1_hold30 | 0.360 | -108.1% | -94.5% | -104.4% | -21.6% | -7.5% | -5.4% | 18.2% | 30.9% | 65.6% | 0.075 | 0.178 | 0.247 | -1.107 | 30 | 30 | 0.003 | 1.7% | 0.406 / 0.00366 | 0.1% / 0.6% | 10.1 / 1175.6 | 60.4% |
| buyers8_top5_ladder2x | 0.537 | -108.1% | -108.1% | -108.1% | -44.1% | -17.0% | -7.5% | 35.5% | 58.4% | 148.5% | 0.088 | 0.139 | 0.187 | -0.555 | 102 | 870 | 0.026 | 17.1% | 0.138 / 0.00363 | 2.5% / 51.4% | 39.4 / 1187.7 | 67.3% |
| laya_0.7 | 0.886 | -108.1% | -86.9% | -102.8% | -24.3% | -7.5% | -0.2% | 35.3% | 59.5% | 324.0% | 1.335 | 2.387 | 2.811 | -3.261 | 30 | 30 | 0.009 | 5.9% | 1.412 / 0.00377 | 0.1% / 0.8% | 6.5 / 700.2 | 53.4% |
| laya_0.6 | 0.616 | -108.1% | -108.1% | -108.1% | -46.1% | -8.5% | -7.2% | 22.8% | 42.8% | 283.0% | 0.471 | 0.856 | 1.039 | -3.003 | 30 | 30 | 0.005 | 3.3% | 0.770 / 0.00363 | 0.2% / 0.9% | 4.4 / 702.9 | 45.6% |
| buy_all | 0.669 | -108.1% | -108.1% | -108.1% | -108.1% | -10.0% | -7.5% | 10.5% | 42.6% | 218.2% | 0.253 | 0.566 | 0.642 | -3.348 | 30 | 30 | 0.003 | 2.3% | 0.504 / 0.00347 | 0.2% / 0.2% | 2.3 / 722.8 | 36.6% |

#### C4. Honest-latency subset: pre-#95 ledger + shadow ledger, only fills whose simulated entry was ≤5 s after the decision clock

| book | window (UTC) | n closed | total fills (all latencies) | misses | fail rate | censored | win rate | mean ret | median ret | avg winner SOL | avg loser SOL | PF | total SOL (incl. miss fees) | ex-top-3 SOL | mean 90% CI SOL | P25 / P50 / P75 / P90 / P95 / P99 ret | trades per UTC day |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mig15_top20_tp50_sl30 | — | 0 | 50 | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| attn_first_hold_60m | 09-25 18:57Z → 09-26 09:58Z | 7 | 78 | 0 | 0.0% | 14 | 14.3% | -48.1% | -103.5% | 0.1104 | -0.0465 | 0.40 | -0.169 | -0.207 | -0.0517 .. 0.0178 | -103.7% / -103.5% / -71.4% / 63.5% / 142.1% / 205.0% | {'09-25': 2, '09-26': 5} |
| migrate_tp50_sl30 | 09-25 15:39Z → 09-26 13:00Z | 97 | 433 | 25 | 17.9% | 18 | 42.3% | -7.3% | -4.5% | 0.0226 | -0.0228 | 0.72 | -0.377 | -0.522 | -0.0080 .. 0.0010 | -42.3% / -4.5% / 41.3% / 54.4% / 60.0% / 95.5% | {'09-25': 48, '09-26': 49} |
| migrate_hold_30s | 09-25 15:36Z → 09-26 13:00Z | 141 | 470 | 33 | 19.0% | 0 | 17.0% | -8.1% | -5.5% | 0.0199 | -0.0090 | 0.46 | -0.603 | -0.821 | -0.0067 .. -0.0012 | -8.4% / -5.5% / -1.5% / 6.8% / 33.5% / 132.4% | {'09-25': 80, '09-26': 61} |
| t30_top1_hold30 | 09-25 15:41Z → 09-26 12:57Z | 94 | 315 | 2 | 2.1% | 0 | 36.2% | -15.1% | -7.6% | 0.0119 | -0.0185 | 0.36 | -0.709 | -0.810 | -0.0111 .. -0.0040 | -41.0% / -7.6% / 11.6% / 31.1% / 49.7% / 66.9% | {'09-25': 39, '09-26': 55} |
| buyers8_top5_ladder2x | 09-25 16:00Z → 09-26 12:53Z | 42 | 127 | 11 | 18.6% | 6 | 21.4% | -8.5% | -29.9% | 0.0517 | -0.0195 | 0.72 | -0.191 | -0.409 | -0.0123 .. 0.0041 | -44.1% / -29.9% / -14.3% / 108.8% / 119.6% / 170.5% | {'09-25': 28, '09-26': 14} |
| laya_0.7 | 09-25 15:34Z → 09-26 12:58Z | 564 | 2050 | 136 | 19.4% | 0 | 30.9% | -7.3% | -16.6% | 0.0321 | -0.0196 | 0.73 | -2.186 | -3.272 | -0.0067 .. -0.0003 | -43.6% / -16.6% / 5.4% / 43.8% / 79.5% / 404.2% | {'09-25': 222, '09-26': 342} |
| laya_0.6 | 09-25 15:34Z → 09-26 12:58Z | 931 | 6453 | 292 | 23.8% | 3 | 25.3% | -12.9% | -20.1% | 0.0382 | -0.0216 | 0.60 | -6.292 | -7.222 | -0.0087 .. -0.0040 | -54.9% / -20.1% / 0.3% / 43.9% / 104.7% / 413.1% | {'09-25': 199, '09-26': 732} |
| buy_all | 09-25 15:34Z → 09-26 13:00Z | 2081 | 21692 | 485 | 18.7% | 25 | 15.5% | -32.7% | -17.8% | 0.0467 | -0.0279 | 0.31 | -34.501 | -35.342 | -0.0180 .. -0.0148 | -108.1% / -17.8% / -5.1% / 21.4% / 84.5% / 324.1% | {'09-25': 685, '09-26': 1396} |

### C5. Other scored sources, for cross-checking (as reported in host scoreboards)

| Source | Window | Book | n | mean SOL/trade | result |
|---|---|---|---|---|---|
| LAYA forward holdout (`laya-v0/out/scoreboard.md`) | after 15:30 UTC 9/25 → 04:15 UTC run | buyers_8 top5 / T+30 top1 / migrate hold_30s | 203 / 194 / 507 | −0.0036 / −0.0060 / −0.0026 | all 90% CIs < 0 |
| Backward holdout (`backward_holdout.md`), sealed backfill 9/25 | 00:00–06:58 UTC 9/25 | buyers_8 / t30 / migrate / **mig15** | 104 / 102 / 281 / **34** | −0.0030 / −0.0039 / −0.0024 / **−0.0013** | no promote |
| LAYA scoreboard buy-all at the sampled latency | 21.42 h, 28,655 creates | buy every decision, hold_30s | 197,295 | −0.0129 (median −0.0033) | total −2,541.6 SOL (independent tickets) |
| Graduated swing buy-all (`graduated-swing/out/scoreboard.md`) | 07:00–18:26 UTC 9/25 | 42 point×rule books | 70–409 each | 41 of 42 means negative (only attn hold_60m +0.0134, n = 70) | no promote |

### C6. Are the fees and slippage realistic for Pump.fun?

**Modeled** (`paper_curve_math.py`, `paper_tape_scoreboard.py` on the host):
- Bonding-curve venue fee 1.25%.
- PumpSwap canonical market-cap tiers (1.25% below 420 SOL mcap: creator 0.30% + protocol 0.93% + LP 0.02%).
- PumpPortal Local 0.5% per side.
- **Priority fee 0.001 SOL per side** (20× the PumpPortal tutorial ceiling).
- ATA rent 0.00203928 SOL, recovered on a successful sell and lost on a rug.
- **Constant-product price impact of our own size**, on both entry and exit, with our buy kept in the reserves.
- All-or-nothing fills with a 15% slippage cap that turns into a miss.
- Measured chain→recv latency.
- Same-signature inner events applied before our fill (#97).

The fee constants cite the pump.fun fee page (20 May 2026). **I did not independently verify current pump.fun fees.**

**Not modeled or weak:**
- **Jito tips / bundles**: none.
- **Dynamic priority fees**: static 0.001 SOL; there is no landing log to calibrate against.
- **Landing failure in the forward runner**: 0%, while the offline scoreboards use a flat 15% and a pressure curve fit to the tape's 28.9% failed-log share, both assumptions.
- **Adverse selection from faster bots within the same slot**: only partly captured.
- **Price movement between decision and fill**: not separately logged.

Measured entry impact is small at 0.05 SOL (median ~0.1–0.2% for most books; P90 up to 2.7% on migrate books and 65% on buyers8 in shadow, where late fills hit thin curves).

**Bottom line:** the model is **reasonably conservative on explicit fees** and includes the bonding-curve / AMM fee and own price impact. It is **optimistic on execution** (no Jito, 0% landing failure in forward, no competition). The fixed 0.002 SOL of priority per round trip is 4% of a 0.05 SOL ticket; the modeled round trip is **7.46% at 0.05 SOL vs 3.86% at 0.5 SOL** (graduated-swing fee table).

### C7. Gross (before-fee) view on the honest-latency subset (fees are the code-constant estimate)

| book | n | gross mean ret | gross median ret |
|---|---|---|---|
| buy_all | 2,081 | −25.66% | −10.50% |
| laya_0.6 | 931 | −5.52% | −12.91% |
| laya_0.7 | 564 | **+0.19%** | −9.34% |
| t30_top1_hold30 | 94 | −7.73% | −0.17% |
| buyers8_top5_ladder2x | 42 (small n) | −1.11% | −22.84% |
| migrate_hold_30s | 141 | −0.63% | +2.00% |
| migrate_tp50_sl30 | 97 (small n) | **+0.20%** | +3.02% |
| attn_first_hold_60m | 7 (tiny n) | −41.41% | −97.76% |

How to read this: the models **do** rank. laya_0.7 is ~26 points better gross than buy-all, mostly by avoiding rugs. But even the best books are **≈0% gross**, against a ~7.3–7.5% round-trip cost. **Fees are not the only problem. There is no gross edge to pay them with.**

---

## Brutally honest verdict

**Are we getting closer to profit?** Not in P&L terms.

The engineering is real and good: an honest tape, an honest simulator, causal ranking, a strict pre-registered gate, frozen holdouts. That infrastructure has done its job by **reliably proving that nothing tested so far makes money**.

- Across ~270 variants, the best realistic-latency results are about −7% per trade net and about 0% gross.
- The two "lead candidates" were selected as nearest misses from that search. They fail the backward holdout (mig15 mean −0.0013 SOL, n = 34 sealed; −0.0002, n = 50 all hours). They have **never been validly forward-tested**, because the runner lagged by minutes to hours from 11:54 AM PT 9/25 and has been down since 6:36 AM PT 9/26.

The goal of thousands of dollars per month is not in sight:
- At 0.05 SOL tickets, 3 concurrent and a 0.2 SOL/day loss cap, even a hypothetical +5% net edge would earn 0.0025 SOL per trade.
- Converting to USD is **UNAVAILABLE** (no SOL/USD price in the data). At any plausible SOL price, reaching that goal needs size or trade counts orders of magnitude beyond the current ceilings, which would also raise impact.

**Single biggest blocker:** there is **no demonstrated gross edge**. The top books are about 0% before fees and ~7.5% of fixed cost sits on each 0.05 SOL trade. The runner outage and lag is the most urgent *operational* blocker, because it means the leads haven't even been measured, but fixing it won't create an edge.

**Top 3 highest-leverage next steps:**
1. **Make the forward test valid, and prove it every hour.**
   - Bring `mal-forward-paper` back up and find out why it restarted 12 times and stopped.
   - Add a lag guard: if recv→decision exceeds ~5 s, skip-to-live on restart or mark decisions invalid instead of back-filling late fills.
   - Alert on runner-down and lag, and revive the health job (stale for 42 h).
   - Exclude the post-#95 shadow/ceiling ledgers from any promotion math.
   - Stop feeding the polluted recv→decision hop into training labels.
2. **Search for gross lift before fee engineering, and fix the ticket economics.**
   - Report gross-before-fees lift vs buy-all for every book. If a book can't show a gross mean clearly above ~4–7%, drop it.
   - Paper-test 0.25–0.5 SOL and direct routing (per the graduated-swing table: 2.48% direct vs 3.46% portal, 3.86% total at 0.5 SOL) to see how much of the drag is structural.
3. **Freeze the search, collect ≥7 clean UTC days on the 5 already-frozen candidates with a pre-committed kill date, and repair the broken data feeds they depend on.**
   - Funding graph: add `maxSupportedTransactionVersion`; 96% of its log lines are errors.
   - Attention-daily: merge #100 and resize its memory model; it is thrashing 4 GB of swap.
   - Backfill: migration detection looks broken (17 migrations in 56 h).
   - No new variants until then.

**Is more cloud compute needed?** **No.**
- The host is already 4 vCPU / 23.4 GiB, with ~50% idle CPU, 16.3 GB RAM available, and 123 GB free on the data disk.
- The swap exhaustion is one job pinned against its own 4 GiB cgroup cap, and the forward lag persisted after the resize.
- The tape uses ~0.1 core and the backfill ~1.2 cores.
- Spending on compute would not change any P&L number in this report. Fix job design, caps and scheduling first.

---

## Metrics marked UNAVAILABLE (and what would be needed)

| Metric | Why | Needed |
|---|---|---|
| USD P&L, $/month | no SOL/USD price in any host file | a timestamped SOL/USD series |
| Per-trade actual fees and slippage breakdown | positions rows carry only net `pnl_sol`; fees in this report are code-constant estimates | log buy/sell venue fee, portal fee, priority and impact per fill |
| Price drift between decision and fill; exit-side impact | no decision-time price or exit spot logged | log spot at decision and at exit |
| Real failed-execution / landing rate; realistic priority fee | no live or devnet sends; forward uses 0% random fail, scoreboards use assumed 15% / pressure curve | signer landing logs (tiny live or simulation against a real RPC) |
| Valid forward metrics for mig15 and attn at designed latency | runner lag and outage; mig15 has 0 honest-latency fills, attn 7 | a working runner for ≥5–7 UTC days |
| Opportunity counts for the pre-#95 and ceiling ledgers | model-level skips were not split by ledger before #95 | n/a (shadow counts given) |
| Exact missed-event rate of the tape | only proxies (create coverage 87.5–90.6%, 51,311 slots in jumps) | reconcile the tape against a full block source for the same hours |
| Root cause of forward-paper restarts and stop | `journalctl` / user systemd not readable by `lyra-ro` | journal read access or a runner log file |
| Backfill directory contents, `/home/ubuntu/mal` | permission denied | read ACL for lyra-ro |
| Attention daily 9/26 scoreboard | job stuck, never written | completed job |
| Current host health from the health job | `health-latest.json` is from 9/24 11:34 PM PT | a running health timer |

## Data sources read
- GitHub: PR list (#41–#100, bodies of #73–#100), recent commits on main (latest #99, `67acf256`), `LAB_STATE.md`.
- Host: `paper/forward-paper/{forward-paper.json, positions.jsonl, decisions.jsonl, pnl-daily.jsonl, latency.json(l), offsets.json, src/tools/*.py}`; `paper/laya-v0/out/{scoreboard.md, backward_holdout.md}`; `paper/graduated-swing/out/{scoreboard.md, mig15_model.json}`; `paper/attention/daily/2026-09-25/scoreboard.md`; `paper/signal-scan/scan.md`; `paper/funding-graph/score-preliminary.json`; `paper/_honest-rescore-*_table.md`; `_exp005/_exp006` reports; `logs/*` (health, host-limit-watch, trade-tape, pump-backfill, attention(-daily), laya-backward, graduated-swing-train, helius-shadow, grep counts on observe / funding-graph); `sealed/trades/stats-*.jsonl` and the file listing; `ps`, `vmstat`, `/proc/pressure`, `/proc/meminfo`, `free`, `df`, `swapon`, per-unit cgroup stats.
- Local analysis artifacts: `/workspace/mal-analytics/{analyze.py, clean.py, tables.py, book_stats.json, clean_latency_stats.json, gross_clean.json, tape_stats.jsonl}`.
