---
cursor:
  subagentId: "bc-3f62ef8e-cb14-549b-9552-01b229927d01"
---

# MAL state audit — built vs planned

**As-of:** 2026-09-25. Repo `github.com/vaanai/MAL`, lab memory `LAB_STATE.md` (2026-09-24, `main` @ `28185a5`, post #72). Read-only. No code changes. Host facts below are from the sibling check `host-health.md` (2026-09-25T06:35Z), not a second SSH session.

North star (`LAB_STATE.md` Objective, `DEC/DEC-009`): three layers → precompute → LAYA → risk gate → exec later. Paper only. Edge unproven.

## Built vs planned

| Layer | Working | Only planned / fixture |
| --- | --- | --- |
| **New-token ingest** | **Live.** `observe/client.py` subscribes PumpPortal `subscribeNewToken` + `subscribeMigration`, stamps regime, appends `ingest_hot` JSONL. Host unit `mal-observe.service` running since 2026-09-23 07:57Z. Rates ~1.2–1.6k events/h (`host-health.md`). | Trade stream, account stream, latency columns `Δ_ws_rpc` / `Δ_decision` (`ARTIFACTS/API-COST-LATENCY-BRIEF.md` §3). `DEC-005` clocks still draft PR #8, not in `DEC/`. |
| **Tracked wallets** | Offline label only. `tools/exp005_smart_wallet_follow.py` `L3_PACKET_V0` = creator prior-mint count **or** weak creator↔buyer recurrence, veto if burst ≥2. Scored 2026-09-20/21. | No leaderboard, no `subscribeAccountTrade`, no wallet tape. Postgres `wallets` = **0 rows**. |
| **Wallet graph** | Offline scalars in `tools/exp004_graph_discovery.py` (H-G1…H-G4) on sealed creates. H-G2 **killed**; ordinal child EXP-004b **killed**. | No graph worker. Hot packet graph slots stay **cold** (`tools/hot_packet_v0.py`). `relationships` table = **0 rows**. `ARTIFACTS/GRAPH-DISCOVERY-V0.md` §2.3 is stale on “no SQL” — stubs exist in `sql/meme_core/001_ops_state_stubs.sql` and are empty. |
| **LAYA** | Schema/CLI chain #63–#72: surround packets, lock receipts, decision packets. Every object forces `laya.authorize_run=false`, `risk_gate.unlock=false`, `measure.kind=none`. | **Not installed** on the host. No model, no &lt;100ms loop, no live packet from observe. |
| **Execution / risk** | None. Constitution phase-0 fence. EXP-006 fill-sim is a constant model (`FILL_LATENCY_MS=500`, `TOTAL_FEE_BPS=125`, size 0.1 SOL) in `tools/exp006_paper_fill_sim.py`. | Risk gate in git is a **locked receipt fixture** (`ARTIFACTS/PAPER-LAYA-RISK-GATE-LOCK-RECEIPT-V0.md`), not a runtime gate. No send path, no Jito, no keys. |
| **Replay / backtest** | Offline CLIs: `exp002_paper_runner`, `exp003_*`, `exp004*`, `exp005*`, `exp006`, `exp007*`. Unit tests on synthetic fixtures for the #49–#72 chain. | Those paper CLIs **refuse** `/var/lib/mal` and score **synthetic** days. They are not a replay of host JSONL. Only courier days **2026-09-20 and 2026-09-21** have marks. |
| **Paper trading** | One sealed fill-sim on those two days (EXP-006). `paper_positions` = **0 rows**. | No forward paper book. Marks files on host stop at 2026-09-21 (mtime 2026-09-23). Observe for 09-23…09-25 is unscored. |

**Code layout that matters:** `observe/` (WS client), `tools/exp00*` (the only scorers), `tools/paper_*` and `tools/paper_laya_*` (fixture honesty), `scripts/mal-core/` (host unit + empty schema apply), `sql/meme_core/` (stubs), `DEC/` `EXP/` `ARTIFACTS/` (memory). No Rust listener, no Geyser consumer, no broker.

## Data sources

| Source | In use? | Latency (lab’s own words) | Cost |
| --- | --- | --- | --- |
| PumpPortal WS `wss://pumpportal.fun/api/data` | **Yes**, free methods only | Vendor FAQ: &lt;100 ms behind gRPC from **NYC**. Host is Phoenix. **No MAL Δ measurement.** Reconnects 1006/502 seen 09-24/09-25; ingest resumed. | **$0**. Trade/account WS is metered (0.01 SOL / 10k msgs) and **not** subscribed. |
| Solana public RPC `api.mainnet-beta.solana.com` | **Yes**, offline: EXP-003 marks, EXP-007b–e enrich (cap 500/day) | Docs: ~100 ms–seconds; ~40 req/10s/method. `getTransaction` rejects `processed`. | **$0**. 429s are the known risk; Gate 1 not opened. |
| Helius / QuickNode / Triton / Yellowstone gRPC | **No** | gRPC marketed sub-10 ms to ~100 ms; unmeasured here | Deferred. Business gRPC **$499/mo**; dedicated node **~$2,900+/mo** (`ARTIFACTS/API-COST-LATENCY-BRIEF.md`). |
| Dexscreener / Birdeye | **No** on spine | — | Hard-deferred. |
| X API | **No** | — | EXP-008 is docs-only. Tiers historically $200–$5k+/mo. |
| Postgres 16 on box | Process **up**, data **empty** except `schema_migrations` + `ops_meta` | n/a | Always Free. |
| Cloudflare Tunnel + Access | **Live** (DEC-011) | SSH path, not market data | Owner CF account; not a market-data bill. |
| Oracle `mal-core-0` | **Live**, 2 OCPU / 12 GB, disk ~1% used, CPU idle | n/a | **$0** Always Free. Not a paid VPS (`DEC/DEC-008` Gate 0 vs Gate 5). |

Paid-service dependency today: **none for market data.** Separate constitution line: soft ~$60/mo team LLM, not RPC.

## Evidence of edge

**No profitable expectancy is shown.** What exists:

- **EXP-002b FAIL closed** — `FAIL_NO_LIFT_VS_RANDOM`. ~81% reject; runner 60s mean ~3% vs random ~20–30% on the marked subsample (`EXP/EXP-002b-evaluate-rules-v1.md`).
- **EXP-002c INCOMPLETE closed, lift FAIL.** Book 32,896 creates (both courier days). Reject rate 63% (misses 70% floor). Runner priced_n=96 mean return **≈0.39%**; reject priced_n=888 mean **≈3202%**; random priced_n=410 mean **≈2500%**. Parity PASS means the arms differ; the selected arm is the worse one (`EXP/EXP-002c-rules-v2-adverse-selection.md`). Means are arithmetic averages of `return_pct` (`tools/exp002_paper_runner.py` `_mean_returns`) — outlier-dominated, no median or SOL PnL in the stamp.
- **EXP-004** Oracle 2026-09-23: 09-20 pop 13,029 / priced_60s **102**; 09-21 pop 15,487 / priced_60s **122**. H-G2 kill. H-G1/H-G3 directional watch, not lift proof. H-G4 incomplete (`EXP/EXP-004-graph-creator-recurrence-v0.md`).
- **EXP-004b** ordinal prior-mint: **cross-day KILL**.
- **EXP-005** L3 packet: sep+random PASS at 60s both days, priced_n **22 / 18**, ~46–50% overlap with the failed v2 runner set. Stamped **DIRECTIONAL_WATCH**, explicit non-promotion. Means **not in git**.
- **EXP-005b** residual L3 minus v2: same watch, priced_n **14 / 11**.
- **EXP-006** fill-sim: fill_rate **100%** because a mark existed; K-lift-rescue **FAIL** day 20 / **PASS** day 21 on n=14/11. Scientific stamp **INCOMPLETE**. Not a broker result.
- **EXP-007…007e:** regime/fee taxonomy. Fee enum `global_95bps` knowable-at-T PASS on a 500/day sample. Not a return test. Sealed full book still fee-unverified.
- **EXP-001 PASS:** mislabel audit only.
- **EXP-003** file still says result **Pending**, while later EXPs consumed a subsample of marks.
- **#49–#72** scoreboards: counts of synthetic stamps. `measure.kind=none`. Not evidence.

Host artifacts for 004/005/006 live under `/var/lib/mal/paper/_exp00*-oracle-*` (gitignored). Repo does not contain the return distributions.

Coverage: **~0.8%** of bonding creates on the scored days have a 60s mark (102/13029, 122/15487).

## Top 5 blockers

Ranked by what blocks a yes/no on expectancy. Each next step is one worker, no new vendor, no live capital.

1. **Outcomes cover &lt;1% of creates, and only two days.** A real edge is invisible, and the published FAIL is on a thin, outlier-sensitive mean. **Next:** run existing `tools.exp003_rpc_backfill` + `tools.exp003_marks` for **one** new sealed day (`observe-2026-09-24.jsonl` on the host), seeded subsample, public RPC, write coverage (ok ticks at 1s/5s/15s/30s/60s) plus median and p90 of `return_pct` — no rule changes. Stop on sustained 429 and record the rate (that is the Gate 1 meter).
2. **Every scored filter loses or is unproven vs random.** v1 and v2 select the low-return arm; L3 “pass” is n≈11–22 with unpublished means and heavy overlap with v2. **Next:** one worker reads the gitignored host JSON for EXP-005/005b/006 and publishes a single table: n, mean, **median**, win-rate, and SOL after 125 bps at 60s for spine vs v2-runner vs L3-minus-v2. If median is not above spine, stamp the watch **killed** in an EXP note. Do not retune constants.
3. **Paper is not forward.** Observe has run ~2 days since the last mark file. There is no daily scoreboard. **Next:** one idempotent host job for **2026-09-24 only**: subsample marks (blocker 1 output) → `exp002_paper_runner --rules v2` → one markdown scoreboard under the host paper dir, copied into an EXP addendum. Proves the loop, not a new alpha.
4. **Layers 2–3 are not the onion in the product sentence.** “Smart wallet” today is “this creator minted before,” computed offline. Postgres graph tables are empty. Buying Birdeye or a wallet WS before a create-spine test is premature (`DEC-008`). **Next:** one offline feature on the existing 09-20/21 book: wallets/creators whose **prior** mints (strictly `T_prior < T`) had a positive 60s mark, scored with the same sep+random gates as EXP-004. Kill or watch in one EXP file. No new feed, no graph DB.
5. **LAYA and the risk gate are not a decision path.** #63–#72 cannot read host JSONL and cannot emit a return. DEC-008 Gate 2 says local LAYA waits on a **closed** rules EXP. **Next:** do not install LAYA. One worker deletes nothing; they add a single replay script that maps one real sealed day through the **existing** hot-packet builder (graph cold, rules v2 label, horizons null) and prints row counts. If that script is awkward because the builder only accepts fixtures, the fix is “accept a JSONL path,” not another schema.

## Where the plan fights the profit goal

- **The #49–#72 chain is the bulk of recent `main` and it cannot change expectancy.** Each PR re-locks `measure.kind=none`, Graph cold, and a lexical ban on `/var/lib/mal`. That is receipt theater. `LAB_STATE.md` Next work items 11–11w are longer than the actual experiment results.
- **“Smart-wallet follow” (EXP-005) does not follow wallets.** It re-slices creator recurrence. Treating a DIRECTIONAL_WATCH as Layer 3 progress will send the next month at the wrong feature.
- **The densify freeze is protecting a failed rule, and also freezing the only meter.** EXP-002c correctly forbids tightening v2 to hit a 70% reject rate. It should not forbid marks on **new** days while the host disk sits at 1% and CPU is idle. Without marks, paper trading cannot start.
- **EXP-007b–e and EXP-008 are side quests.** Fee enum precision and an X brief do not address the adverse-selection result. X stays correctly off until a spine filter has a number (`CONSTITUTION.md`, DEC-008 Gate 4).
- **Latency shopping is already parked and should stay parked.** API and stack briefs (`ARTIFACTS/API-COST-LATENCY-BRIEF.md`, `ARTIFACTS/STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md`) show gRPC/colo/Jito as MEV-adjacent. The stated edge is information vs humans and copy-traders, not same-slot races. Do not open Gate 1 or Gate 6 to “catch up.”
- **`ARTIFACTS/GRAPH-DISCOVERY-V0.md` §2.3 is behind the repo** (SQL stubs and hot-packet schema now exist) but its warning still holds: empty tables and cold graph slots are not a graph.
- **Arithmetic mean of meme `return_pct` is a bad kill statistic** when one name can move the mean by orders of magnitude (reject ≈3202% vs runner ≈0.4%). The FAIL direction is informative; the magnitude is not an SOL expectancy.

What is **not** waste: free PumpPortal ingest on the Always Free box, JSONL as the audit spine, full-book reject labels (DEC-007), and refusing live keys. Those match the goal.

## Owner-only questions

1. **Hold horizon.** Is the product a 60s scalp (DEC-006 primary) or a multi-minute filter vs FOMO? That choice changes which marks are worth RPC.
2. **Return definition.** Is `marketCapSol` / `vSolInBondingCurve` percent change an acceptable PnL proxy, or do you require SOL-after-fees on a fixed size before any PASS counts?
3. **Wallet layer source.** Should “tracked wallets” be derived only from MAL’s create spine, or from a list you already trust (and would that list require a paid API)?
4. **Densify override.** May workers run public-RPC mark backfill on the host for days after 2026-09-21, knowing EXP-002c said densify is not a lift rescue?
5. **LAYA binary.** Which open-source tree, and do you want it on the 2/12 box only after a rules PASS (DEC-008) or sooner?
6. **Live bar.** What paper record (days, median, max loss) must exist before any live SOL, and what is the kill size? Not in the repo.
