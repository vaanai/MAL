---
cursor:
  subagentId: "bc-bb521b3b-b9d9-5fad-aee6-15508fa225ba"
---

# Smart-wallet / KOL research (web-only, 2026-09-25)

Paper-only L2 (tracked wallets) + L3 (clusters). Edge vs humans/FOMO, not MEV.

## 1. How tools rank “smart” wallets + cheap access

| Source | How they identify / rank | Programmatic access |
|---|---|---|
| **FOMO** ([fomo.family](https://fomo.family/answers/buy-crypto-following-top-traders), [copy-trading explainer](https://fomotrading.app/copy-trading/)) | In-app users only. Ranks **24h / 7d / 30d / all-time PnL**; token-page “who made money”; social feed. Advises 30d curve across many tokens, not 24h one-shots. Manual copy (push → tap). Claims **625k+** public-PnL traders. | **No public API.** App-only. |
| **GMGN** ([gmgn-skills](https://github.com/GMGNAI/gmgn-skills), [token tags](https://github.com/GMGNAI/gmgn-skills/blob/HEAD/skills/gmgn-token/SKILL.md)) | Opaque algo tags: `smart_degen` (hist. profitable), `renowned` (KOL), plus `sniper` / `bundler` / `rat_trader` / `fresh_wallet` / `dex_bot` (Axiom/Photon/BullX/Trojan/GMGN). Rank tabs: 1d/7d/30d **PnL, winrate, profit, open_count**. KOL ≠ alpha. | Official **Agent API** (key at gmgn.ai/ai). Unofficial JSON used by scrapers: `/defi/quotation/v1/rank/sol/wallets/7d?tag=smart_degen&orderby=pnl_7d` ([nirholas/scrape-smart-wallets](https://github.com/nirholas/scrape-smart-wallets)). ToS-grey. |
| **Kolscan** ([kolscan.io/leaderboard](https://kolscan.io/leaderboard)) | Curated **named KOLs** (X/TG mapped). Rank **realized SOL PnL + wins/losses** over 24h / 7d / 30d. Social identity, not algo smart-money. | No official API. SSR `initLeaderboard` JSON + `POST /api/leaderboard` ([yksanjo/kol-tracker](https://github.com/yksanjo/kol-tracker), [nirholas/kol-quest](https://github.com/nirholas/kol-quest)). |
| **Cielo** | **Your follow-list** PnL/ROI/WR/volume (6h–30d). “Wallet discovery” on Pro/Whale. Anti-bot/spam filters in API. Not a global KOL board. | App free (≤250 wallets, 10 SOL). API: feed free; PnL `$89+/mo` Builder ([docs](https://developer.cielo.finance/docs/getting-started)). |
| **Photon** | Manual terminal; **no copy-trade**. Ranked by others as Photon-fee wallets. | **No public API.** Third-party board: [solanatracker.io/leaderboard/photon](https://www.solanatracker.io/leaderboard/photon). |
| **BullX Neo** | User-added tracker + alerts; token **Top Traders / Sniper / Holders** (dev, rat, cross). Docs warn not to blindly copy. | No official API. Unofficial scripts ([Malakia-sol/bullx-neo-api-scripts](https://github.com/Malakia-sol/bullx-neo-api-scripts)). KOLSCAN top-50 import JSON ([sn3ll/KOL-Wallets-BULLX](https://github.com/sn3ll/KOL-Wallets-BULLX)). |
| **Axiom Trader Scan** ([trade-on-axiom.com](https://trade-on-axiom.com/product/trader-scan)) | Full-chain **FIFO realized PnL**, WR, avg R, profit factor, sizing, survivability; playstyle (sniper/swing/scalper/insider/MM); wash/airdrop/bridge adjust; cluster mapping. 24h/7d/30d/all-time. Copy hooks. | **Axiom API** (paid; undocumented publicly here). Third-party: [solanatracker axiom board](https://www.solanatracker.io/leaderboard/axiom). |
| **Birdeye** | **Per-token** top traders (`volume`, `trade`, `realized_pnl`, `hold_volume`); Solana tags `dev,bundler,sniper,insider,smart_trader`. Wallet leaderboard is **AUM ≥$100k**, not meme PnL. | Official REST. Free Standard **30k CU / 1 rps**; wallet APIs often paid ([pricing](https://docs.birdeye.so/docs/pricing), [`/defi/v2/tokens/top_traders`](https://docs.birdeye.so/reference/get-defi-v2-tokens-top_traders)). |
| **Solana Tracker** | PnL V2 over **top 500k** wallets: realized/ROI/WR; `minTrades=20`, `minDays=3`, `maxSingleTokenPct`, `excludeArbitrage`, `platform` (axiom/photon/bloom/fomo/gmgn/pumpfun). Separate **KOL** board. | Best cheap JSON: `GET https://data.solanatracker.io/v2/pnl/leaderboard/top` + `/kols`. **Free 10k req/mo**; €50/mo 200k ([pricing](https://docs.solanatracker.io/pricing), [docs](https://docs.solanatracker.io/guides/pnl-v2/leaderboards)). |
| **Dune** | Community SQL: realized PnL on pump.fun buys+DEX sells. | Public queries → API `GET /api/v1/query/{id}/results` (free key, credits/MB). IDs: [6912832](https://dune.com/queries/6912832) 7d PnL; [5484678](https://dune.com/queries/5484678) 7d smart money; [5452508](https://dune.com/queries/5452508) active wallets. Dashboards: [adam_tehc alpha wallets](https://dune.com/adam_tehc/pump-fun-alpha-wallets) (top 10k / 30d), [wallet analysor](https://dune.com/adam_tehc/pumpfun-wallet-analysor), [otpchaser trench kings](https://dune.com/otpchaser/daily-trench-kings). |

**Practical seed list (cheap):** Solana Tracker free KOL + top (`days=30`, `maxSingleTokenPct`, `excludeArbitrage`) ∪ Kolscan SSR top-50 (all 3 TFs) ∪ Dune 6912832/adam_tehc. Treat GMGN `smart_degen` as noisy prior, not ground truth.

## 2. Latency window (sizes L2)

| Hop | Typical | Source |
|---|---|---|
| Solana slot | ~400 ms | [Luo et al. WWW’26](https://arxiv.org/html/2601.08641v2) |
| Launch sniper fight | 50–250 ms bursts; mostly over before next leader | [Onchain Divers](https://onchaindivers.substack.com/p/what-a-validator-sees-two-pumpfun) |
| Auto copy (Axiom/GMGN/Trojan) | **same-block ~0.4–1 s** claimed | FOMO vs-terminals table |
| GMGN TG / aggregator alert | **5–10 s** typical; “zero-latency” bot claims ms; TG rate-limits drop msgs | [r/solana](https://www.reddit.com/r/solana/comments/1rchqih/sorry_if_dumb_question_but_is_there_way_to/); [GMGN docs](https://docs.gmgn.ai/index/wallet-alert-on-telegram) |
| FOMO **push** | **2–4 s** after fill | [fomotrading.app](https://fomotrading.app/copy-trading/) |
| FOMO **human tap** | **10–40 s** unlock→confirm; entry **5–15% worse** on thin memes; worse-entry cost **1–15%** | same |
| Median **copied hold** | **24 s** (mean 42 min, long-tail bags). 77% of copies on pump.fun curve. 63% of targets have 1 copier; max **1,523** | [uwuu 1,710 trades](https://uwuu.ai/blog/solana-copy-trading-statistics) |
| Copy EV vs latency | Win-rate ~46% @0.5s → 28% @1s → **4% @5s**; **τ≥2s guaranteed loser** after fees (sim of 100k copies of top-50 leaders) | [Kurnovskii 2025](https://romankurnovskii.com/en/blog/pumpfun-copy-trading-feasibility/) |
| Sniper cohort → extra buyers | +16% non-cohort buyers in **30 min**, SOL inflow NS | [arXiv:2607.02795](https://arxiv.org/pdf/2607.02795.pdf) |

**MAL window:** beat FOMO humans (**<10 s**, ideally **<2–4 s** before push). Auto-copiers already in by ~1 s — do not race MEV/snipers. If we land ≥24 s we are often **exit liquidity** for the original (uwuu). Prefer leaders whose hold >> copy-lag (minutes, not seconds).

## 3. Failure modes + how operators filter

| Mode | Pattern | Filter |
|---|---|---|
| **Follower-farm then dump** | Build WR/PnL, get 100s–1k+ copiers, dump into copies | Cap follow-count; skip wallets with huge public follow; treat high-copier KOLs as **exit risk** (FOMO: “tens of thousands of followers on a thin coin”). Track **sell vs our fill**, not just their buy. |
| **KOL paid shill** | Public `renowned` wallet buys **~20s later** for screenshot; cheap supply already in bundle ([DEV](https://dev.to/coolbb/tracking-kol-smart-money-wallets-for-memecoin-snipes-what-people-dont-solve-3ole)) | Score **fill quality** (slot vs create, curve reserves), not PnL tile. `renowned` ≠ copyable. |
| **Bundle / create-slot sniper** | Jito `bundle_id` with create; ~44% supply in create slot → transfer to **distributor** that never buys (copy tools see green distributor, red sniper) ([Conyr teardown](https://conyr.ai/blog/anatomy-of-a-pumpfun-extraction-crew)) | Drop `entry_slot−create_slot≤2` **and** `hold_slots<50`. Same-bundle-as-create = insider. Transfer-in without buy = uncopyable. |
| **Persistent sniper rings** | 1,012 cohorts of 2–12 wallets, 166k launches | Co-occurrence / union-find; treat as **launch classifier**, not clone list ([arXiv:2607.02795](https://arxiv.org/pdf/2607.02795.pdf)). |
| **Wash / wash-PnL** | Round-trips, self-funding loops, comment bots (Luo WWW’26) | Exclude wash/airdrop (Axiom); `excludeArbitrage`; buy≈sell volume with tiny hold; GMGN `wash-trade` flag. |
| **One-hit wonder / 24h board** | Single launch vertical PnL | `maxSingleTokenPct`; require **N tokens + N days**; FOMO: skip 24h board. |
| **MEV / HFT** | Hold **<5–10 s**; near-zero inter-tx variance; Jito tips | Kurnovskii: `<10s` algo flag; `<5s` ~14% of wallets, sandwich/arb. Do not copy. |

**Operator checklist (madeonsol / GMGN / Axiom / FOMO):** `tokens_traded≥20`; independent (low KOL pair `agreement_rate`); not `bundler`/`sniper`/`rat_trader`/`dex_bot`/`fresh_wallet`; funding graph first; 30d smooth equity, staggered sells; size similar to ours.

## 4. Recipe: derive list from pump.fun tape

Scope: pump.fun program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` + PumpSwap sells after migrate. FIFO per `(wallet, mint)`. Marks = outcomes only (as EXP-005).

1. **Round-trips:** buy/sell pairs; realized SOL PnL net of 1% curve fee + tx. Unrealized optional, never for rank.
2. **Eligibility (30d rolling, Solana Tracker-like):** `closed_tokens ≥ 20`, `active_days ≥ 7`, `min_invested ≥ 1 SOL`, `max_single_token_pnl_share ≤ 40%`.
3. **Rank (not raw PnL):** profit factor or avg R; require **WR 35–65%** (90%+ tiny R = sniper; 30% large R = swing — Axiom). Prefer **median hold 2–30 min** (drop `<30s` median; Kurnovskii: don’t copy holds `<5 min`).
4. **Early-entry (copyable, not sniper):** fraction of buys with `3 ≤ Δslot_from_create ≤ 150` (≈1–60s) on tokens that later 2×. High rate **inside slots 0–2** → sniper/bundle, **veto**.
5. **Consistency:** PnL>0 in **≥2 of last 4 weeks**; skip 24h stars. Overlap Kolscan **all 3 TFs** (~20 stable names) as **watch**, not clone.
6. **Bot/MEV veto:** median hold `<10s`; buy-sell same minute repeatedly (FOMO skip); Jito-tip correlation; known `dex_bot` fee programs; fresh wallet; creator/funder cluster (L3).
7. **Copyability gate:** they **bought** (not transfer-in); our modeled lag 0.5–2s still leaves EV>0 given their size (skip leaders ≥5–10 SOL on thin curve — 11–46% slippage tax).
8. **Cap list:** 20–50 wallets; refresh daily; decay 7d PnL.

**Suggested v0 thresholds (starting filters, not proven alpha):**

| Metric | Keep | Veto |
|---|---|---|
| Closed mints / 30d | ≥20 | <10 |
| Realized PnL 30d | >0 and not 1-token | — |
| Win rate (closed) | 35–65% | >80% with avg gain <10% or hold <30s |
| Median hold | 2–30 min | <10s (bot); <30s if also early-slot |
| Early-entry | some 1–60s organic | ≥50% of PnL from Δslot≤2 |
| Max one-mint PnL share | ≤40% | >70% |
| Follow/KOL fame | low | Kolscan-famous + thin-coin dumps |

Seed with Tracker/Dune/Kolscan, **re-score on own tape** — public boards are the farm.

## 5. OSS to borrow (not to execute live)

| Repo | What | License |
|---|---|---|
| [haccer/pumpfun-research](https://github.com/haccer/pumpfun-research) | Per-wallet pump.fun PnL, delay-to-first-sell | MIT |
| [yegor104/pumpfun-tracker](https://github.com/yegor104/pumpfun-tracker) | WS decode + per-wallet realized PnL across curve→PumpSwap | unspecified |
| [ace8ecar-source/walletintel-v2](https://github.com/ace8ecar-source/walletintel-v2) | FIFO PnL, WR, sniper/scalper/smart labels, score | unspecified (OSS) |
| [kukapay/pumpfun-wallets-mcp](https://github.com/kukapay/pumpfun-wallets-mcp) | Wallet profitability via Dune | MIT |
| [CodeMuscle/solbeam](https://github.com/CodeMuscle/solbeam) | Helius wallet tracker + rug score (incomplete) | MIT |
| [Rezzecup/pump-fun-rug-checker-lite](https://github.com/rezzecup/pump-fun-rug-checker-lite) | mint/freeze/LP/dev-concentration gate | unspecified |
| [ccan23/rugcheck](https://github.com/ccan23/rugcheck) | rugcheck.xyz wrapper | MIT |
| [agentic-reserve/solana-forensic-intelligence](https://github.com/agentic-reserve/solana-forensic-intelligence) | Forensic clustering | MIT |
| [yksanjo/kol-tracker](https://github.com/yksanjo/kol-tracker) | Kolscan SSR snapshot | unspecified |
| [nirholas/scrape-smart-wallets](https://github.com/nirholas/scrape-smart-wallets) | GMGN unofficial endpoints | Other |
| [rckprtr/pumpdotfun-sdk](https://github.com/rckprtr/pumpdotfun-sdk) | Program ix decode | MIT |

Skip copy-bot repos (Jito snipers). Papers: [Luo WWW’26](https://arxiv.org/html/2601.08641v2) (filters; copier +3% vs leader +14% w/ frictions); [arXiv:2607.02795](https://arxiv.org/pdf/2607.02795.pdf) (cohorts).

**Implication for MAL:** L2 = small **filtered** set from **own tape**, seeded by Tracker/Dune/Kolscan; act **<2–4 s** on **copyable** fills (not create-slot). L3 = creator/bundle/funder/co-fire clusters as **veto/enrich**, never as clone list. Matches EXP-005: never blind-mirror wallet X.
