# DEC-008 — Stack spend phase gates (cheap-first default)

| Field | Value |
| --- | --- |
| **Status** | Draft (working gates; not a shopping list) |
| **Decider** | Vaan (lab policy) |
| **Date** | 2026-09-21 (recorded) |
| **Requires** | [DEC-002](DEC-002-memory-first-no-db-local.md), [DEC-006](DEC-006-detect-decode-evaluate-runners.md), [DEC-007](DEC-007-full-detect-book-anti-selection-bias.md) |
| **Evidence brief** | [STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md](../ARTIFACTS/STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md) |

## Decision

**Default:** remain on **phase-0 cheap path** (~**$0/mo** infra) until each gate below is satisfied. Spending is **rung-based**; skipping rungs requires a new DEC with kill-attempt or measured failure logs.

Oracle Always Free **`mal-core-0`** ([DEC-009](DEC-009-oracle-always-free-phase0-host.md)) is a **Gate 0** $0 host experiment (**2 OCPU / 12 GB** A1; **pending provision**), **not** Gate 5 paid VPS and **not** the ~$180–200 meta stack.

This DEC does **not** authorize purchases. It defines **what must be true** before a separate spend DEC or Vaan explicit approval.

### Gate 0 — Always on (current)

| Must be true | Rung |
| --- | --- |
| Observe via **PumpPortal WS** + sealed JSONL | **$0** |
| Continuous host: Oracle Always Free `mal-core-0` (**2 OCPU / 12 GB**; pending) **or** laptop until cutover | **$0** |
| Evaluate **rules-only** on knowable-at-T packet until H-jev ladder runs | **$0** |
| **No live capital**, no exec send, no Jito | **$0** |
| X **off spine** (manual reassess samples only) | **$0** |

### Gate 1 — Paid RPC (~$49–50/mo class)

**May reassess** Helius Developer or QuickNode Build **only if all:**

1. **7+ days** of logs with 429/403 or timeout rate on backfill **above lab tolerance** (document % and methods), **and**
2. **Missed-create or regime-backfill audit** attributes failures to RPC capacity (not WS gap, not client bugs), **and**
3. [EXP-002b](../EXP/EXP-002b-evaluate-rules-v1.md) or successor is **not blocked** solely for lack of RPC — i.e. buying RPC is for **observe/decode quality**, not to substitute for unfinished EXP scoring.

**Still defer:** Business-tier gRPC ($499+).

### Gate 2 — Local Laya on hot path (marginal **$0**; engineering cost only)

**Open hypothesis** — test **after** Gate 3 baseline, not before.

**May promote local Laya ahead of cloud JEV only if all:**

1. Rules-only evaluate on full detect book has a **closed** EXP stamp (PASS or FAIL, not INCOMPLETE for lift/parity when priced_n allows).
2. Same book, same [DEC-007](DEC-007-full-detect-book-anti-selection-bias.md) reject-cohort checks: Laya **beats** rules on primary horizon **after** documented cost model, **or** explicit DEC records H-jev kill of rules and promotes Laya as next baseline.
3. `Δ_jev` logged; p95 **`Δ_observe + Δ_packet` &lt; primary horizon** (inference not pretending to fix slow observe).

**Cloud JEV API:** defer unless local Laya fails Gate 2 and regret correlates with **model quality** not observe — new DEC required.

### Gate 3 — Rules / evaluate EXP closure (blocks most spend)

**No infra rung above Gate 0** for alpha reasons until:

1. [EXP-002b](../EXP/EXP-002b-evaluate-rules-v1.md) local run **closed** with lift and reject-cohort parity gates **not INCOMPLETE** (or honest FAIL recorded).
2. Post-create **horizon marks** populated enough that primary horizon (60s) is priced for **both** runner and reject arms where schema allows — or EXP documents **N/A** dominance and follow-on mark EXP is logged.

*While EXP-002b marks/lift remain incomplete, Gate 1 is for RPC pain only; Gates 4–6 are **closed**.*

### Gate 4 — Twitter / X paid (~$50–100/mo cap class)

**May reassess** only if all:

1. Gate 3 closed (filter economics known).
2. H-social EXP design merged; **allowlisted** reassess on mints **already on spine**.
3. Projected **$/month** at chosen poll/webhook rate **&lt;** measured lift band (document in EXP).

**Never** for mint discovery or cashtag firehose.

### Gate 5 — VPS 24/7 (or ~$180–200 bundled stack)

**Not this gate:** Oracle Always Free `mal-core-0` ([DEC-009](DEC-009-oracle-always-free-phase0-host.md)) is Gate 0.

**May reassess** a **paid** VPS only if all:

1. Gate 3 closed.
2. **14-day** uptime log: home observe miss **&gt; agreed threshold** during peak windows **or** documented network block — not preference for “always-on server.”
3. Pilot week on smallest VPS proves **lower** `Δ_observe` or **higher** capture vs home — **needs measurement**, not assumption.

Bundled Redis/managed Postgres in meta stacks: **separate** — on-box Postgres is already allowed as Layer-2 cache ([DEC-009](DEC-009-oracle-always-free-phase0-host.md)); managed/Autonomous still wait on volume/multi-writer/query pain.

### Gate 6 — Jito bundles / paid send path

**May reassess** only if all:

1. Gate 3 **PASS** (or promoted filter with Proof sign-off).
2. **Exec DEC** exists (not paper-only).
3. **Tiny** live capital with kill limits; paper `Δ_exec` model validated.
4. Bundle spend tied to **fill improvement** metric, not observe.

## Rationale

- Separates **“infra that fixes logging”** (RPC) from **“infra that buys edge”** (gRPC, colo, bundles) — edge spend waits on EXP outcomes.
- **Local Laya** stays an **open hypothesis** compatible with local-first; it does not bypass rules baseline or full detect book law.
- Prevents **$180–200/mo** stack from becoming default while profitability remains hypothesis.

## Review trigger

- Any rung opened in production without gate evidence → revert spend and log EXP gap.
- PumpPortal or program upgrade changes create path → re-audit Gate 0–1 only.

## Overturn path

New DEC with measured logs and council alignment; default remains cheap until then.
