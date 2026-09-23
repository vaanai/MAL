# Paper-trading surface brief — execution realism above marks

| | |
| --- | --- |
| **Research as-of** | 2026-09-23 |
| **Owner seat** | Scout + Proof (market surface + paper book) |
| **Commission** | Manager research artifact — **no broker implementation in this PR** |
| **Unblocks** | Honest **Δ_exec** on the paper book ([DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md), [DEC-005](../DEC/DEC-005-hot-packet-clocks-and-provenance.md) draft) without live capital |
| **Lab locks** | Sealed **JSONL** = provenance spine; Postgres = **L2 ops/state** only ([DEC-002](../DEC/DEC-002-memory-first-no-db-local.md) amended). **No trading keys / no X keys on `mal-core-0`.** Marks path ([EXP-003](../EXP/EXP-003-post-create-marks.md), [POST-CREATE-MARKS-BRIEF.md](POST-CREATE-MARKS-BRIEF.md)) **stays**. Bonk/mayhem **parked**. **No invented lift/EV numbers.** |

---

## Problem

MAL’s phase-0 **runners** ([DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md)) stamp a **pretend-buy** at decision time `T` and score **gross** outcomes at **1s / 5s / 15s / 30s / 60s** (plus +2s / +10s / +5m / peak / DD when ticks exist) using **outcome marks** ([EXP-003](../EXP/EXP-003-post-create-marks.md)). That answers: *“What was the price proxy after T at horizon H?”*

It does **not** yet answer:

- **When** the hypothetical order would have **filled** (decision → submit → land latency).
- **At what effective price** for a **size** (curve depth, not mid/mark).
- **Fees** (protocol, creator, priority, ATA/rent) and **slippage tolerance** interactions.
- **Failed / partial** landing (simulation reject, insufficient liquidity, route change at migration).

Vaan wants a **fidelity layer above marks**: same sealed detect book and evaluate labels, but **would-have-happened → simulated broker** so filters can be tuned before any live-capital DEC. This brief compares external “paper venues” vs an **in-lab fill simulator** on top of existing marks/trade ticks.

**Orthogonal:** Graph [EXP-004](GRAPH-DISCOVERY-V0.md) (creator recurrence) — do **not** claim or block on it here.

---

## What the marks-only path already gives (baseline)

| Capability | Marks path (EXP-003 / EXP-002) | Source |
| --- | --- | --- |
| Post-create **price proxy** time series | `outcome_mark` side JSONL joined on `mint` / `parent_signature` | [POST-CREATE-MARKS-BRIEF.md](POST-CREATE-MARKS-BRIEF.md) |
| **Knowable-at-H** horizon returns | Last tick with `T < t_mark ≤ T+H`; discard `t_mark > T+H` | [EXP-003 §4](../EXP/EXP-003-post-create-marks.md) |
| **Full detect book** + runner/reject labels | Evaluate on packet; outcomes on **both** arms | [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md) |
| **Gross** lift / parity gates | EXP-002 family; `Δ_exec` often **N/A** until cost model exists | [EXP-002](../EXP/EXP-002-evaluate-runner-v0.md) |
| **No keys** on oracle host for scoring | RPC backfill + local CLI on Vaan JSONL | [LAB_STATE.md](../LAB_STATE.md) |

### What marks miss (execution realism gap)

| Gap | Why it matters for Pump.fun memes |
| --- | --- |
| **Instant fill at T** | Runners implicitly buy at create `t_ws`; real flow is detect → decode → evaluate → **submit** (often hundreds of ms to seconds later). |
| **Size / depth** | `marketCapSol` / curve reserves are not a guaranteed fill price for **SOL notional**. |
| **Fees & priority** | Bonding-curve fee tiers, creator fee, tx priority, and (post-migration) route fees are not in mark rows. |
| **Slippage setting** | User/API `slippage` caps change whether the tx **would** have landed. |
| **Landing failure** | Marks from **successful** txs do not model “tx never landed” or preflight failure. |
| **Venue switch** | `pool: pump` vs `pump-amm` / Raydium after migration ([PumpPortal trading API](https://pumpportal.fun/trading-api/)) — marks on bonding curve alone understate path change. |

---

## Options matrix (paper / sim surfaces)

**Legend — Pump.fun fit:** **High** = mainnet bonding-curve microstructure; **Low** = different chain/program/universe; **None** = not automatable for MAL spine.

**Legend — host/key posture:** **MAL-safe** = no signing keys on `mal-core-0`; **Owner-local** = Vaan machine only, never in git; **Reject** = violates phase-0 locks or is live mainnet capital.

| ID | Surface | Fidelity (fills / fees / slip / latency) | API / access | Cost (phase 0) | Pump.fun fit | Host / keys | Kill criteria |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **P0** | **Custom fill-sim on marks + trade ticks** (MAL-owned; side JSONL) | **Configurable:** curve quote at `T_fill`, fee bps from documented Pump math, slippage cap, latency `L`, optional fail rate. Not identical to Jito landing without measurement. | **No vendor** — consumes sealed ingest + `outcome_mark` + optional `subscribeTokenTrade` ticks ([real-time docs](https://pumpportal.fun/data-api/real-time/)) | **$0** if RPC/backfill only; metered WS if forward dense trades (0.01 SOL / 10k msgs, wallet ≥0.02 SOL) | **High** (same mints as observe book) | **MAL-safe** on oracle; producers on **Owner-local** | Cannot calibrate `L`/fees vs reality without forward measurement; abandon if sim params become opaque knobs that rescue lift |
| **A1** | **Axiom.trade (official UI)** | Live **on-chain** execution; analytics, limits, one-click — **not** a paper ledger ([docs](https://docs.axiom.trade/), [llms-full](https://docs.axiom.trade/llms-full.txt)) | Web app + embedded wallet; **no documented paper API** | $0 to browse; trades cost real SOL + fees | **High** for human trading UX | **Reject** for automated lab spine (no headless paper contract) | No API for sealed JSONL join; requires real funds for “practice” |
| **A2** | **Axiom + browser extensions** (e.g. DryFlip, Axiom Trade Simulator, AxiomExtend — third-party) | Extension-local **virtual SOL**; marketing claims “realistic fills/slippage” — **unverified** by MAL ([Chrome Web Store example](https://chromewebstore.google.com/detail/axiom-trade-simulator/jffmmbggiiidafbadcinhcbkclphbcch), [AxiomExtend](https://axiomextend.xyz/)) | Chrome extension overlay; **not** a server API | $0–extension pricing varies | **Medium** (UI parity only) | **Owner-local** manual; **no** extension on `mal-core-0` | No provenance spine; permission risk; not reproducible on sealed book |
| **A3** | **AxiomTradeAPI-py** (community SDK) | **Live** buy/sell; wraps PumpPortal-style trading ([ChipaDocs](https://docs.chipatrade.com/docs/axiomtradeapi/), [API ref](https://chipadevteam.github.io/AxiomTradeAPI-py/api-reference/)) | Email/browser auth, tokens on disk; WebSocket new tokens | $0 SDK; trades cost SOL | **High** for live automation | **Reject** on shared host — trading auth + keys | Same as PumpPortal live API — not paper |
| **B1** | **Phantom — Testnet mode** | Real devnet txs; **not** mainnet curve behavior ([Phantom testnet docs](https://docs.phantom.com/developer-powertools/testnet-mode)) | Wallet UX; devnet SOL via [faucet](https://faucet.solana.com/) | $0 | **None** for Pump.fun meme book (mainnet creates) | Devnet keys **Owner-local** | Different universe; cannot replay Sep observe JSONL |
| **B2** | **Phantom + mainnet micro-SOL** | **Real** fills and fees | User-signed txs | Real capital at risk | **High** but **live** | **Reject** for phase-0 paper DEC | Violates “paper-only day-1” recommendation |
| **C1** | **PumpPortal Lightning Trading API** | **Real mainnet** landing; `skipPreflight: false` runs **simulation before send** — still broadcasts if you proceed ([trading API](https://pumpportal.fun/trading-api/), [FAQ preflight](https://pumpportal.fun/FAQ/)) | `POST https://pumpportal.fun/api/trade?api-key=…`; needs API key + funded wallet | Trading fees + priority + 0.01 SOL/10k WS if paired with data | **High** | **Owner-local** key only; **never** in repo / not on oracle by default | **Not paper** — kills on “no live path day-1”; sim is pre-send debug only |
| **C2** | **PumpPortal `trade-local`** (unsigned tx return) | Build tx locally; **you** sign/send — still mainnet | Documented alongside Lightning API | Same | **High** | Requires **private key** material to sign | Live capital; park under spend + exec DEC |
| **D1** | **Solana RPC `simulateTransaction`** | VM-level sim of a **constructed** swap/buy tx; good for “would revert?” — needs correct ix builder + accounts at slot | Public RPC / Helius free tier | $0 until 429s; Helius upgrade gated ([API brief](API-COST-LATENCY-BRIEF.md)) | **Medium** — must build Pump instructions ([pump SDK](https://www.npmjs.com/package/@pump-fun/pump-sdk)) | **MAL-safe** if **no** signing key (simulate unsigned or ephemeral) | Heavy engineering; still not full Jito bundle race |
| **E1** | **Solana devnet Pump program** | On-chain devnet program ID exists; **very low activity** ([pump SDK devnet examples](https://www.npmjs.com/package/@pump-fun/pump-sdk)) | `https://api.devnet.solana.com` | $0 | **Low** — not MAL mainnet book | Devnet keys | Cannot validate mainnet adverse selection |
| **E2** | **testnetpump.fun** (unofficial) | Separate RPC / faucet; community testing ([example write-up](https://web3engineering.co.uk/knowledge-base/testing-with-testnetpumpfun)) | Custom RPC endpoint | $0 | **Low** — unofficial, not PumpPortal | N/A | Not joinable to sealed mainnet JSONL |
| **F1** | **Reference OSS “paper fill engines”** (e.g. [sentinel](https://github.com/MuLIAICHI/sentinel) `execution/` paper mode, [solana-pumpfun-bot](https://github.com/DeeKalshiWay/solana-pumpfun-bot) `paper_executor.py`) | **Patterns only:** virtual wallet, slippage/MEV/fail knobs — **no MAL lift claims** | Copy ideas, not vendor SLA | $0 | **High** if wired to PumpPortal WS + curve math | **MAL-safe** if no live gate | Fork maintenance cost; do not import wholesale |
| **G1** | **CEX / perps testnets** (e.g. ChipaX $100k paper on Hyperliquid — cited in [AxiomTradeAPI-py README](https://github.com/ChipaDevTeam/AxiomTradeAPI-py)) | Perps fee model | API | $0 paper | **None** for Pump.fun bonding creates | N/A | Wrong instrument |
| **H1** | **Jupiter / Raydium mainnet** | AMM path **after** graduation; not create-window bonding curve | Aggregator APIs | $0 reads; txs cost SOL | **Partial** (post-migration leg) | Live keys if trading | Out of scope for 1s–60s create-window EXP unless migration hypothesis |

---

## Comparison table — marks-only vs recommended paper layer

| Dimension | Marks-only (current) | + Paper fill-sim (P0) | External Axiom extension (A2) | PumpPortal live + preflight (C1) |
| --- | --- | --- | --- | --- |
| **Join key** | `mint`, `parent_signature`, horizons | Same + `runner_stamp_id` / `paper_order_id` | Manual ticker | Wallet + mint |
| **Provenance** | Sealed side `outcome_mark` | Sealed side `paper_fill` / `paper_order` (proposed) | Extension storage | On-chain tx sigs |
| **Fill time** | Implicit **T** | **T + L** (explicit latency param) | User click time | Network landing |
| **Fees in outcome** | `Δ_exec` **N/A** / `fee=unverified` | Labeled `fee_model_id` + bps assumptions | Opaque | Real fees |
| **Slippage** | None | Curve + `max_slippage_bps` | Extension-defined | Real |
| **Failed tx** | N/A | Can stamp `status=sim_reject` | Unknown | Real fail + cost |
| **Replay on Sep JSONL** | **Yes** (RPC backfill) | **Yes** (backfill + sim at historical ticks) | **No** | **No** (unless re-run live) |
| **Oracle host** | Safe | Safe (sim is compute-only) | N/A | **Keys required** — defer |
| **Spend gate** | Public RPC | Optional metered WS | $0 | SOL + API |

---

## Recommended day-1 path (paper-only)

### Primary: **P0 — Custom fill-simulator above marks** (spec + CLI in a **follow-on** PR; **not** this artifact PR)

1. **Keep** EXP-003 marks as the **outcome meter** for horizon returns (unchanged law).
2. **Add** a parallel **paper execution** side stream (proposed types in [OBSERVE-JSONL-SCHEMA.md](OBSERVE-JSONL-SCHEMA.md) extension — `paper_order`, `paper_fill`, `paper_position_delta`) keyed to:
   - parent create `signature` / `mint`
   - evaluate label (`runner` | `reject`) — fills only on **runner** unless doing full-book counterfactual
   - `t_decision` = sealed `t_ws` (until DEC-005 `T_decision` merges)
3. **Fill model (v0 — honest, knowable-at-T):**
   - `t_intent` = `t_decision` (or `t_decision + decode_latency_ms` if measured later).
   - `t_fill` = `t_intent + L_ms` where `L_ms` is a **documented constant or distribution** (needs measurement; start with single constant, e.g. 400–800 ms — **not** a fitted lift knob).
   - **Price:** last `outcome_mark` or trade tick with `t_mark ≤ t_fill` (same anti-lookahead as marks); if none, `status=unfilled`.
   - **Size:** fixed paper notional in SOL (e.g. 0.1 SOL) — document in EXP, not tuned per coin.
   - **Fees:** use Pump **protocol + creator fee bps** from on-chain `feeConfig` at or before `t_fill` via RPC account read ([pump SDK fee fetch pattern](https://github.com/nirholas/pump-fun-sdk/blob/main/docs/getting-started.md)) — stamp `fee_source=on_chain_snapshot` vs `fee_assumed` when RPC missing.
   - **Slippage:** apply constant-product bonding curve delta for `size_sol` vs reserves at `t_fill` (from mark row `vSolInBondingCurve` / `vTokensInBondingCurve` when present); cap by `max_slippage_bps`.
   - **Δ_exec:** `gross_return - fees - slip_penalty` at each horizon **on filled position**, separate from mark-only gross.
4. **Postgres:** optional mirror into existing `paper_positions` stub ([sql/meme_core/001_ops_state_stubs.sql](../sql/meme_core/001_ops_state_stubs.sql)) — **cache only**, rebuildable from JSONL.
5. **Windows:** report paper PnL at the same **1s / 5s / 15s / 30s / 60s** marks (and extended windows when ticks exist).

**Why not external “paper brokers” day-1:** No Solana/Pump.fun service exposes a **headless, sealed-JSONL-joinable paper ledger** with known fee/slip models. Axiom paper is **third-party UI** (A2). PumpPortal **is execution** (C1). Devnet (E1) is the wrong universe.

### Parked alternatives (require explicit Helm/Vaan approval)

| Alt | When to reconsider | Spend / credential gate |
| --- | --- | --- |
| **C1 PumpPortal live micro-size** | Calibrating `L_ms` and fail rates against **real** landing | Owner wallet + API key **local only**; SOL spend; not on `mal-core-0` |
| **A2 Axiom extension** | Human UX check only — “does our filter feel tradable?” | Install on operator browser only |
| **D1 RPC `simulateTransaction`** | After P0 v0 stable; refine revert/fail without broadcasting | RPC volume → Helius free/paid ([API brief](API-COST-LATENCY-BRIEF.md)) |
| **C1 + `skipPreflight: false`** | Debug failed ix only | Same as C1 |

### Explicit non-recommendations

- **Live capital** or **host-stored signing keys** for phase-0 paper DEC.
- **ChipaX / CEX paper** for Pump.fun create-window hypotheses (G1).
- **Dexscreener** as fill price (forbidden on spine, [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md)).

---

## Knowable-at-T / no future leakage (sim design law)

| Rule | Detail |
| --- | --- |
| **Decision inputs** | Evaluate uses **decode packet at T** only — unchanged ([DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md)). |
| **Fill inputs** | Any tick/account snapshot used for fill must have `t_obs ≤ t_fill`. |
| **Horizon marks** | Unchanged: `T < t_mark ≤ T+H` for outcome marks; paper layer does not relax this. |
| **Fee config** | On-chain fee tier read must be from slot/time **≤ t_fill** (or parent mark’s slot if that is the lab’s RPC source). |
| **Forward WS** | `subscribeTokenTrade` ticks are knowable at receive time `t_ws`; for historical book use RPC backfill ([EXP-003](../EXP/EXP-003-post-create-marks.md)), not live WS. |
| **Migration** | If `stage` migrates before `t_fill`, v0 sim may stamp `status=route_unknown` rather than guess Raydium depth — **honest N/A** beats fake precision. |

---

## Honest “test trading by next week” scope

Calendar estimates are misleading for agents; this is what is **technically** achievable from **2026-09-23** with current locks:

| Deliverable | Status this PR | ~1 week with approval | Blocked without Vaan |
| --- | --- | --- | --- |
| **Research + recommendation** (this artifact) | **Done in PR** | — | — |
| **Thin pointers** in `LAB_STATE.md` / `SUMMARY.md` / `ARTIFACTS/README.md` | **Done in PR** | — | — |
| **paper_fill JSONL schema + DEC stub** | Spec in this brief only | Draft DEC amendment or OBSERVE schema PR | — |
| **Fill-sim CLI** (`tools.exp006_paper_fill_sim`) | **Landed** — Oracle sealed **2026-09-20/21** stamp [EXP-006](../EXP/EXP-006-paper-would-have-happened-harness-v0.md) (**INCOMPLETE** cross-day; **no promote**) | Owner-local replay on same JSONL law | Subsample marks coverage |
| **Calibrated latency / fail rate** | Research-only | Needs forward capture or **C1** micro-live calibration run | Wallet + API key + SOL |
| **Dense trade ticks forward** | Documented | `subscribeTokenTrade` TTL on new creates | PumpPortal API key + ≥0.02 SOL wallet |
| **Automated Axiom paper** | **Not feasible** | Still **no** — no API | Axiom auth / extension automation |
| **Oracle (`mal-core-0`) running broker** | **Not recommended** | Batch sim on sealed files OK; **no keys** | Trading secrets on host |

**Plain language:** “Test trading next week” can mean **(a)** replaying the sealed book through a **documented sim** with fees/slip/latency knobs and comparing to marks-only EXP-002 gates — **not** **(b)** a third-party paper account mirroring every meme fill. **(b)** is blocked on credentials and is **live-adjacent** for PumpPortal.

---

## Measurement backlog (needs lab benchmarks — no invented numbers)

1. Distribution of **detect → evaluate → submit** latency on `mal-core-0` (observe + rules only).
2. **PumpPortal WS** `t_ws` vs RPC `blockTime` skew for same mint (marks brief §needs measurement).
3. **Sim vs micro-live** on a **fixed** list of 20 mints (Owner-local C1) — compare fill price at 0.1 SOL; record in sealed JSONL, not chat.
4. **Reject rate** of `simulateTransaction` for typical buy ix at create+5s (D1).

---

## Proposed side-record shape (informative — not merged schema yet)

```json
{
  "type": "paper_fill",
  "schema": "paper_fill_v0",
  "parent_signature": "<create sig>",
  "mint": "<mint>",
  "runner_label": "runner",
  "t_decision": 1726840000.123,
  "t_fill": 1726840000.623,
  "latency_ms": 500,
  "size_sol": 0.1,
  "fill_price_proxy": 1.23e-7,
  "fee_model_id": "pump_on_chain_bps_v0",
  "fee_sol": 0.00001,
  "slippage_bps": 85,
  "status": "filled",
  "source": "sim_curve_v0",
  "knowable_at_t": true
}
```

Append-only under `data/observe/paper-YYYY-MM-DD.jsonl` (gitignored), same courier model as marks.

---

## Primary sources (external)

- PumpPortal real-time WS: https://pumpportal.fun/data-api/real-time/
- PumpPortal trading / preflight: https://pumpportal.fun/trading-api/ , https://pumpportal.fun/FAQ/
- Axiom product docs (live trading): https://docs.axiom.trade/
- Phantom testnet mode: https://docs.phantom.com/developer-powertools/testnet-mode
- Solana clusters / RPC limits: https://solana.com/docs/references/clusters
- Pump SDK (fees / devnet): https://www.npmjs.com/package/@pump-fun/pump-sdk , https://github.com/nirholas/pump-fun-sdk/blob/main/docs/getting-started.md
- MAL marks law: [POST-CREATE-MARKS-BRIEF.md](POST-CREATE-MARKS-BRIEF.md), [EXP-003](../EXP/EXP-003-post-create-marks.md)

---

## Decision summary

| Question | Answer |
| --- | --- |
| **Day-1 paper path?** | **P0** — [`tools.exp006_paper_fill_sim`](../tools/exp006_paper_fill_sim.py) on sealed JSONL + marks ([EXP-006](../EXP/EXP-006-paper-would-have-happened-harness-v0.md) scored **INCOMPLETE**; **no promote**). |
| **Marks path?** | **Keep** — gross horizons and coverage unchanged. |
| **Axiom / Phantom?** | **Human or live** — not MAL spine; extensions **parked** for automation. |
| **PumpPortal trading API?** | **Live exec** — park for calibration only with Owner-local keys + spend approval. |
| **Spend before approval?** | **No** paid RPC tier, metered WS at scale, or mainnet SOL burns. |

**Review trigger:** If P0 sim systematically **improves** lift vs marks-only on the **same** rules without changing evaluate packet, treat as **leakage or overfit** — audit join keys and `t_fill` law before any promotion.
