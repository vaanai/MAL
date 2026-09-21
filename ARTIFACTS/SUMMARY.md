# Manager summary (≤20 lines)

**As-of:** 2026-09-21

- **New:** [STARTER-STACK-OPTIONS-BRIEF.md](STARTER-STACK-OPTIONS-BRIEF.md) — JEV/hot-path budgets, X feed options, create-feed vendors, starter stack **options** (not one mandate).
- Lab memory: `LAB_STATE.md`, `CONSTITUTION.md`, `DEC/`, `EXP/`, `ARTIFACTS/`.
- **Pipeline (council 2026-09-20 PT):** detect→decode→evaluate→runners paper path [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md); full detect book + filter kill on reject/runner parity [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md).
- **Regime-at-ingest v0:** [DEC-003](../DEC/DEC-003-regime-at-ingest-v0.md), encoding [DEC-004](../DEC/DEC-004-regime-id-encoding.md); matrix + JSONL schema unchanged.
- **Phase-0 observe:** PumpPortal WS → sealed JSONL; X/social **reassess only** after mint on spine — no discovery path in brief.
- **JEV path:** precompute → hot packet → JEV → risk gate → exec; ms on inference rarely beats observe/stage correctness on bonding memes until exec DEC.
- **Social:** official X pay-per-use ($0.005/post read, webhooks $0.005/event) vs third-party scrapers — compliance risk; defer firehose.
- **Create feeds:** PumpPortal free WS baseline; Helius/Bitquery/gRPC are **measure-before-buy** alternates ([API brief](API-COST-LATENCY-BRIEF.md)).
- **Recommended starter:** keep $0 spine; Helius free/$49 RPC only on 429/lag metrics; rules JEV + deterministic gate; A/B packet timing before any gRPC.
- **Hard defer:** Birdeye paid, Dexscreener on spine, buy-infra-first, PumpPortal trading API.
- **EXP-001:** local mislabel CLI `python -m tools.exp001_mislabel` vs sealed JSONL on Vaan's PC (`observe-2026-09-20` ~15k, `observe-2026-09-21` ~19k); [lock + runbook](../EXP/EXP-001-regime-stage-mislabel.md).
- **Next:** Vaan runs EXP-001 (n=100, seed=1, PASS/FAIL vs >1% / >5%); then name first evaluate→runner EXP.
