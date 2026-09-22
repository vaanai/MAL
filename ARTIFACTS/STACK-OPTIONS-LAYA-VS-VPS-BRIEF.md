# Stack options — local Laya vs VPS-first vs phase-0 cheap path

| | |
| --- | --- |
| **Research as-of** | 2026-09-21 |
| **Audience** | Vaan / Helm / Scout / Graph / Proof |
| **Scope** | Honest compare of **deployment + hot-path** options for observe → packet → evaluate → (later) exec; **not** a purchase order |
| **Lab locks** | Cheap-first; measure before pay; paper before live capital; profitability is hypothesis; X = reassess only; [DEC-002](../DEC/DEC-002-memory-first-no-db-local.md) **amended by** [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md); companion [STARTER-STACK-OPTIONS-BRIEF.md](STARTER-STACK-OPTIONS-BRIEF.md), [API-COST-LATENCY-BRIEF.md](API-COST-LATENCY-BRIEF.md) |

---

## Executive framing

Vaan is leaning **away from VPS-first long-term** toward **local Laya** (local JEV-class scorer, ~**9 ms** inference class, **$0** marginal, slightly weaker than cloud JEV) **if** the surrounding system is strong enough that inference is not the bottleneck.

A common “serious hobbyist” stack cited in meta-discussion lands around **~$180–200/mo**: paid RPC (~$50), Twitter API (~$100), small VPS 24/7, Postgres/Redis, optional Jito send path — plus engineering time. That stack can plausibly sit **~200–400 ms behind dedicated MEV** on observe/send while still **ahead of manual retail** — but only **after measurement** on MAL’s own `Δ_*` columns, not from vendor hero numbers.

**This brief does not mandate that stack.** Phase 0 remains: **Windows local observe**, **PumpPortal free WS spine**, **public or Helius-free RPC** for light backfill, **JSONL + in-repo artifacts**, **rules-only evaluate** until EXP lift gates clear.

**Decision gates (not a buy list):** [DEC-008](../DEC/DEC-008-stack-phase-gates.md).

**Phase-0 host experiment ([DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md), 2026-09-22):** Oracle **Always Free** Ampere A1 **`mal-core-0`** (**2 OCPU / 12 GB**, **not 4/24**; **pending Vaan provision**) is the chosen continuous-host experiment. This is **not** a paid VPS and does **not** open DEC-008 Gate 5. Laptop remains operator + data courier until cutover. Local Laya remains an open hypothesis to run **on that host** (aarch64) after the rules baseline — not a reason to buy cloud JEV or a ~$180–200 stack. BOM: [ORACLE-ALWAYS-FREE-BOM-v0.md](ORACLE-ALWAYS-FREE-BOM-v0.md).

---

## Position in the race (honest)

| Actor | Typical observe + decide class | What wins |
| --- | --- | --- |
| Colocated MEV / shred-adjacent bots | **Tens of ms** on see + send | Same-block snipes, bundle auctions |
| Fast aggregator + paid RPC + VPS near NYC | **~100–400 ms** behind leaders on create path (planning bucket) | Beating retail and slow bots |
| PumpPortal WS + home PC + rules | **~50–500 ms** `Δ_observe` class ([starter brief §1.2](STARTER-STACK-OPTIONS-BRIEF.md)) | **Filtering** and **not mis-labeling stage** before exec exists |
| Retail manual | **Seconds+** | Not the benchmark |

**Claim to test, not assume:** paid RPC + always-on host moves MAL from “misses launches under throttle” to “stable enough to score EXP-002b marks” — not automatically from “losing” to “winning” vs MEV.

---

## Phase 0 (now) — baseline

| Dimension | Choice | Est. $/mo | Latency class | Evidence that would justify *staying* here |
| --- | --- | --- | --- | --- |
| **Observe spine** | PumpPortal WS `subscribeNewToken` + `subscribeMigration` on **local Windows** | **$0** | FAQ: **&lt;100 ms behind gRPC** (NYC); home ISP adds jitter | EXP-002b runs complete; miss rate of creates **not** dominated by WS gaps |
| **RPC** | Public mainnet-beta + spot checks; Helius **free** if 429s | **$0** | Backfill **100 ms–s+**; 429 bursts | `getTransaction` / account reads succeed for **regime backfill** on stratified sample; no systematic **missed creates** attributable to RPC-only path |
| **Storage** | JSONL under `data/observe/` + gitignored EXP outputs | **$0** | N/A (disk) | Book size &lt; **~1–5 GB/day** retained; single writer (Vaan PC) |
| **Evaluate** | Rules-only (`tools.exp002_paper_runner`, **v1** default) | **$0** | **&lt;1 ms** | [EXP-002b](../EXP/EXP-002b-evaluate-rules-v1.md) produces thick reject cohort; lift/parity gates **closed** (PASS or FAIL), not INCOMPLETE |
| **JEV / Laya** | **Off hot path** | **$0** | — | H-jev: rules baseline scored first per [LAB_STATE](../LAB_STATE.md) |
| **Social** | Manual / exports only | **$0** | Minutes | H-social not on critical path for EXP-002b |
| **Exec** | None | **$0** | — | Constitution + DEC-006 paper only |

**Phase-0 total infra:** **~$0/mo** (team LLM ~$60/mo is separate per constitution).

**Current blockers (lab truth as-of 2026-09-21):** [EXP-002](../EXP/EXP-002-evaluate-runner-v0.md) v0 **INCOMPLETE closed** (vacuous filter). **EXP-002b** tooling ready; local **marks/lift** and reject-cohort parity **pending**. Many horizons stay **N/A** until post-create price proxies exist in JSONL — infra spend does not fix missing outcome columns.

---

## Hot path — local Laya vs cloud JEV vs rules-only

| Option | Est. $/mo | Inference latency | Strengths | Weaknesses | EXP evidence to adopt |
| --- | --- | --- | --- | --- | --- |
| **Rules-only** (current EXP path) | **$0** | **&lt;1 ms** | Auditable; knowable-at-T; DEC-006/007 friendly | Alpha ceiling | EXP-002b: **lift vs random** at 60s **and** reject≠runner after costs; else **kill** filter |
| **Local Laya** (local JEV-class, ~9 ms cited) | **$0** GPU/CPU amortized | **~5–15 ms** class | No network tail; privacy; repeatable | “Slightly dumber” than cloud; needs packet schema + labels | **After** rules baseline closed: same book, same gates; Laya beats rules on **full detect book** under [DEC-007](../DEC/DEC-007-full-detect-book-anti-selection-bias.md); `Δ_jev` logged |
| **Cloud JEV** (remote inference API) | **$20–200+** usage-dependent | **20–200 ms** tail (network) | Stronger models | Violates local-first spirit; cost; provenance harder | Only if local Laya **fails** H-jev but measured regret is **inference-quality** not observe; explicit DEC |
| **LLM per mint on spine** | High | **Seconds** | Manager explanations | Not hot path | Off spine only |

**Hypothesis (open):** Local Laya is viable **if** `Δ_observe + Δ_packet + Δ_gate` dominate and cloud model quality gap does not erase paper edge. **Default:** stay rules-only until EXP-002b (and any follow-on mark EXP) closes.

---

## Listener — Python vs Rust (when 5–10× parse speed matters)

| Factor | Python (current `observe/`) | Rust (`solana-client` / Yellowstone gRPC consumer) |
| --- | --- | --- |
| **Cost** | **$0** dev time familiar | Engineer time; same host |
| **Parse / fan-out** | Fine for **WS JSON** at PumpPortal rates | Matters when **raw tx firehose**, multiple program filters, or **per-slot** decode at scale |
| **Latency win** | Often **not** first bottleneck vs WS aggregator | Wins when CPU decode blocks **emitting hot packet** within 500 ms ingest policy |
| **When 5–10× matters** | Rare if spine is PumpPortal **pre-parsed** creates | If MAL moves to **self-serve geyser/grpc** or multi-stream merge |

**Evidence to rewrite listener in Rust:** logs show **p95 `Δ_packet` &gt; 100 ms** attributable to Python decode on **same machine** while RPC/WS idle; **or** parallel tap requirement exceeds one GIL-bound process. **Not** justified by “Rust is faster” alone while Phase-0 WS spine suffices.

---

## Storage — Redis / Postgres vs JSONL / SQLite

| Store | Est. $/mo | When it pays | MAL phase-0 |
| --- | --- | --- | --- |
| **JSONL + markdown** | **$0** | Single writer; replay via CLI; EXP-sized books | **Yes** — EXP / knowable-at-T spine ([DEC-002](../DEC/DEC-002-memory-first-no-db-local.md) as amended) |
| **SQLite** (local file) | **$0** | Indexed replay, ad hoc SQL on **one** machine; still no Redis | **Maybe** when EXP queries hurt; still no DEC required if file-local |
| **Redis** | **$0** self-host or **$15–30** managed | Sub-ms **dev reputation / holder sets** shared by **multiple** hot-path workers; eviction TTL state | **Defer** until multi-process precompute or &gt;1 writer; not for EXP-002b |
| **Postgres** | **$0** self-host (Always Free on-box) or **$15–50** managed | Layer-2 cache / continuous ops; graph working set | **On-box OK** per [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md) as **cache**, **not** EXP SoT. Managed/Autonomous **defer**. |

**Failure modes that force upgrade from flat files:** cannot retain **full detect book** reject outcomes; replay **&gt; tens of minutes** per EXP iteration; **corruption** from concurrent writers without merge discipline.

---

## RPC — free vs Helius / QuickNode ~$49–50

| Tier | Est. $/mo | Latency / reliability | Failure mode that forces upgrade |
| --- | --- | --- | --- |
| **Public RPC** | **$0** | 429/403; **~100 req/10s** class limits | Sustained **429 rate** blocks `getTransaction` backfill → **wrong/missing `regime_id`** or EXP marks |
| **Helius / QN free** | **$0** | Higher headroom; still capped | Same, at higher volume threshold |
| **Developer ~$49** | **~$49–50** | **~50 req/s** class; enhanced methods | **Documented** miss rate: creates seen on WS but **RPC confirm fails** &gt; X% on sample; or 429 **&gt; Y%** of backfill window during peak hours |
| **Business gRPC ~$499+** | **$499+** | Sub-slot streams | **Only** if EXP shows **actionable regret** vs PumpPortal on **same mint set** at documented T — not phase 0 |

**What does *not* justify $50 yet:** occasional 429 on manual CLI; vanity “production RPC”; chasing MEV without exec DEC.

**MAL metric:** log `Δ_ws_rpc`, 429 counts, and **missed-create audit** (WS row without successful backfill where matrix requires RPC).

---

## Twitter / X ~$100/mo — reassess accelerator only

| Approach | Est. $/mo | Latency | Lab fit |
| --- | --- | --- | --- |
| **None (phase 0)** | **$0** | — | **Default** |
| **Pay-per-use / ~$100 cap** | **~$50–100** with hard limit | REST **100 ms–s**; webhooks event-driven | **Allowlisted** accounts; mint **already on spine** |
| **Enterprise / firehose** | **$5k+** | Fast but discovery-shaped | **Reject** — violates constitution |

**Evidence to spend:** H-social EXP shows **measured lift** on reassess windows (e.g. decision time vs KOL post) **&gt;** API + scoring cost; **not** because “everyone uses Twitter.” **Not** required for EXP-002b closure.

---

## VPS ~$180–200/mo stack vs keep-PC-on

| Deployment | Est. $/mo | Uptime | Latency to RPC/WS | Ops burden |
| --- | --- | --- | --- | --- |
| **Home PC Windows (now)** | **~$0** marginal (+ power) | Sleep/reboot gaps | ISP-dependent; may be fine for observe | Low; Vaan already runs JSONL |
| **Small VPS 24/7** (often bundled in $180–200 meta stack) | **~$20–80** compute alone | **99%+** if monitored | Often **closer to NYC/EU** POPs — **needs measurement** vs home | SSH, deploy, secrets, billing |
| **Full meta stack** (VPS + $50 RPC + $100 X + Redis/Postgres) | **~$180–200** | High | Theoretical retail-plus | **High** for phase 0 — buys services before edge proof |

| Tradeoff | Honest read |
| --- | --- |
| **VPS for observe** | Only if home **uptime** or **NAT** breaks hypothesis (missed hours &gt; X% of peak pump window) **and** WS works from VPS |
| **VPS for exec later** | Send path may need stable egress; still **after** paper gate |
| **Keep PC + Laya** | Aligns with local-first if **uptime acceptable** and `Δ_observe` acceptable |

**Evidence to rent VPS:** 7-day log shows **&gt;5–10%** missed observe hours during documented peak windows **or** home IP blocked/throttled; not “VPS feels pro.”

---

## Jito bundles / private send

| Item | Est. $/mo | When |
| --- | --- | --- |
| **Jito tips + bundle infra** | Variable (tips dominate) | **After** paper edge + **tiny** live capital gate; exec DEC exists |
| **PumpPortal Lightning / dedicated send** | Usage-based | Same |

**Prerequisite chain:** EXP-002b (or successor) **PASS** on filter lift → full book parity holds → exec DEC → paper-live tiny size → then measure `Δ_exec` sensitivity to bundles.

**Do not buy** bundle access to “fix” losing paper book.

---

## Reference stacks (comparison table)

| Stack | Rough $/mo | Observe class | Decide class | Fits MAL today? |
| --- | --- | --- | --- | --- |
| **A — Phase 0 (locked)** | **~$0** | PumpPortal WS + free RPC | Rules-only evaluate | **Yes** |
| **B — Laya-local strong surround** | **~$0–50** (optional RPC only) | Same spine; better precompute on PC | Local Laya + rules gate | **Hypothesis** — test after rules EXP |
| **C — Meta VPS bundle** | **~$180–200** | VPS + paid RPC + DB/cache | Cloud JEV + social poll | **No** until gates in DEC-008 |
| **D — MEV-adjacent** | **$500–5k+** | gRPC / colo | Custom Rust + bundles | **Defer** hard |

---

## What NOT to buy now (while EXP-002b marks/lift incomplete)

Explicit **do-not-buy** until EXP-002b closes lift/parity gates (or records honest FAIL) **and** post-create horizon marks are populated enough that gates are not **INCOMPLETE**:

| Item | Why wait |
| --- | --- |
| **Helius/QuickNode paid ~$50** | 429 pain unquantified; misses not attributed to RPC on audit |
| **Twitter / X ~$100** | H-social unsettled; X not discovery; EXP-002b does not need it |
| **VPS 24/7 / $180–200 bundle** | Uptime problem not proven vs Always Free `mal-core-0`; do not treat DEC-009 as this buy |
| **Managed Redis / Autonomous Postgres** | On-box Postgres OK as Layer-2 cache; JSONL remains EXP spine |
| **Yellowstone / Helius Business gRPC** | No regret vs PumpPortal at T |
| **Jito / bundles / Lightning send** | No exec DEC; no paper edge |
| **Birdeye paid, Bitquery paid spine** | Hard defer per constitution |
| **Cloud JEV API** | Rules + local Laya not tested |
| **Rust rewrite of listener** | No measured Python `Δ_packet` failure |
| **PumpPortal metered trade firehose** | Create spine only for phase 0 |

---

## Unknowns / needs measurement

| ID | Question |
| --- | --- |
| L1 | Home Windows `Δ_observe` p50/p95 vs same client on **$20 VPS** NYC during peak |
| L2 | Local Laya vs rules on **same** EXP-002b book — does 9 ms matter vs 60s horizon noise? |
| L3 | Missed-create rate: WS present, RPC backfill failed — free vs $49 RPC |
| L4 | PC uptime vs pump peak hours (ET) over 14 days |
| L5 | Whether inference gap (local vs cloud JEV) exceeds bonding-curve noise at primary horizon |

---

## Primary sources

- PumpPortal: https://pumpportal.fun/data-api/real-time/ , https://pumpportal.fun/FAQ/
- Solana RPC limits: https://solana.com/docs/references/clusters
- Helius / QuickNode pricing: https://www.helius.dev/pricing , https://www.quicknode.com/pricing
- Jito: https://jito.wtf/ (bundles — exec-era)
- Lab: [CONSTITUTION.md](../CONSTITUTION.md), [DEC-008](../DEC/DEC-008-stack-phase-gates.md)
