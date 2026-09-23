# DEC-009 — Oracle Always Free phase-0 continuous host

> **Amended 2026-09-23 by [DEC-010](DEC-010-oracle-phase0-handoff-autonomy.md)** (provisioned) **and [DEC-011](DEC-011-cursor-oracle-access-cf-tunnel.md)** (access design decided, **pending owner implement**). Always Free **2 OCPU / 12 GB** envelope, JSONL EXP spine, and paper/no-keys rules **remain**. Live inventory + autonomy: [ORACLE-PHASE0-HANDOFF.md](../ARTIFACTS/ORACLE-PHASE0-HANDOFF.md). Original pre-create text below is **historical** except where still true (sizing, cheap-first, JSONL spine).

| Field | Value |
| --- | --- |
| **Status** | Active (working law). **Host provisioned and verified (2026-09-23).** See [DEC-010](DEC-010-oracle-phase0-handoff-autonomy.md). |
| **Decider** | Vaan (lab policy). Council **soft-OK** Scout / Graph / Proof (2026-09-22) — soft flags below, not hard blockers. |
| **Date** | 2026-09-22 (recorded); amended 2026-09-23 |
| **Amends** | [DEC-002](DEC-002-memory-first-no-db-local.md) (no-DB / laptop-only-forever for continuous runtime) |
| **Amended by** | [DEC-010](DEC-010-oracle-phase0-handoff-autonomy.md) (pending → provisioned; workflow / access / escalate laws); access design [DEC-011](DEC-011-cursor-oracle-access-cf-tunnel.md) (pending owner implement) |
| **Does not amend** | Cheap-first / measure-before-pay ([DEC-008](DEC-008-stack-phase-gates.md)); paper path ([DEC-006](DEC-006-detect-decode-evaluate-runners.md)); full detect book ([DEC-007](DEC-007-full-detect-book-anti-selection-bias.md)); EXP-002c closed facts |
| **BOM** | [ORACLE-ALWAYS-FREE-BOM-v0.md](../ARTIFACTS/ORACLE-ALWAYS-FREE-BOM-v0.md) (provisioned names) |
| **Handoff** | [ORACLE-PHASE0-HANDOFF.md](../ARTIFACTS/ORACLE-PHASE0-HANDOFF.md) |

## Product alignment (encoded; not a dump)

MAL is a **low-cost, high-speed Solana meme-coin intelligence** lab. Phase 0 is **paper first**, **free-first**, **measure bottlenecks before paid upgrades**.

**Three intelligence layers** (independent, simultaneous, combinable):

1. **New-coin** — create-time market spine: token, creator history, liquidity/holders/velocity, safety.
2. **Entity / relationship / graph** — persistent profiles and edges (creators, wallets, collaborators, launches).
3. **Smart wallet / person following (filtered)** — dynamically selected set; never blind copy. Person action + token + history + graph + market + safety → LAYA.

**LAYA** = real-time **decision layer** on **already-precomputed** features → **deterministic risk gate** → **execution** (**execution deferred**; no live capital, no send path).

**X / Twitter** = **later additive layer 4** (cherry-on-top / reassess accelerator). **Not** primary discovery. **No X keys on the host in phase 0.**

**Agents:** Grok managers (Helm / Scout / Graph / Proof) persist; Cursor workers ship PRs/artifacts and exit. **Trading keys isolated from agents** — never on `mal-core-0` in phase 0.

## Decision

1. **Always Free first.** Phase-0 continuous host is an **Oracle Cloud Always Free** experiment targeting **$0/mo infra**. This is **not** a paid VPS and does **not** open [DEC-008](DEC-008-stack-phase-gates.md) Gate 5.
2. **Single compute:** one **`VM.Standard.A1.Flex`**, **2 OCPU / 12 GB RAM**, arch **aarch64 / ARM**, name **`mal-core-0`**. **Do not claim or provision 4 OCPU / 24 GB** — that is the **paid-tenancy** Ampere free-hour pool, not Always Free tenancies. Official Always Free Ampere pool = **1,500 OCPU-hours + 9,000 GB-hours / month** ≡ **2 OCPU + 12 GB** on a free tenancy. Sources: [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).
3. **On-box PostgreSQL 16** on that VM (not Autonomous DB in phase 0). **As of DEC-010:** Postgres = **operational/state** layer (`meme_core` / `mal_app`, localhost, data dir `/var/lib/mal/postgresql/16/main`). **Sealed JSONL remains the provenance / EXP spine.** Do **not** treat Postgres as a mandatory provenance replacement. GitHub remains SoT for `DEC/` / `EXP/` / `LAB_STATE.md`.
4. **No live keys on this host in phase 0.** Wallet / trading private keys **never**. **No X / Twitter API tokens.** Paper only. RPC/chat/OCI/DB secrets stay off git and off world-readable disk (systemd creds or root-only).
5. **GitHub Lab memory remains SoT.** Managers reload `LAB_STATE.md` + `ARTIFACTS/SUMMARY.md` + DECs. Workers do not become persistent agents.
6. **Measure before paid upgrades.** Capacity miss, 429s, disk, or uptime pain → log + Helm/Vaan; **do not** silently pick paid shapes, NAT Gateway, paid RPC, OKE, GPU, or a second fat VM. (DEC-010: escalate before any spend / security-boundary change.)
7. **Laptop role:** operator console + **data courier**. Laptop is **not** the 24/7 observe host. **Human PC must not** become a permanent Cursor→Oracle networking hop ([DEC-011](DEC-011-cursor-oracle-access-cf-tunnel.md)). Agents do **not** yet have SSH (**pending owner implement** of CF Tunnel + Access).

### Council soft flags (not hard blockers)

| ID | Flag | How to treat |
| --- | --- | --- |
| S1 | **JSONL spine** — sealed JSONL stays EXP / knowable-at-T truth day-1; Postgres is cache/ops, not EXP SoT | Soft. Day-1 cutover must keep append-only JSONL. |
| S2 | **aarch64 binary gaps** (Solana client / replay / LAYA wheels) | Soft. **Post-create escalate to Helm** — **not** a pre-create veto. Prefer multi-arch/aarch64 images from day-1. |
| S3 | **No trading keys and no X keys** on the host in phase 0 | Soft reaffirm of a hard secret rule. Paper only. |
| S4 | **Bonk / mayhem still parked** (evaluate + ingest expansion) | Soft. Unchanged from EXP-002c / DEC-003 parks. Host choice does not unpark. |

## Rationale

- Product doc wants a **$0** always-on box for ingest, features, paper, and (later) LAYA — without buying the ~$180–200 “hobbyist VPS” stack before edge is proven.
- Always Free Ampere **was** 4 OCPU / 24 GB; **enforced limit for free tenancies is 2 / 12** (document as-of 2026-09-22). Lab must size to **2 / 12** or it will bill or be terminated.
- On-box Postgres unblocks Layer-2 graph/ops state without Autonomous lock-in or a managed-DB invoice. Keeping JSONL as EXP spine preserves knowable-at-T audit and existing EXP-001/002/003 tooling.
- Public 443 on day-1 is unnecessary (SSH tunnel / Cloudflare Tunnel later). NSG + UFW keep Postgres/Redis/UI/metrics off the internet.

## Out of scope (phase 0)

- Live execution, Jito, PumpPortal trading API, wallet signing
- X/Twitter ingest or tokens
- Autonomous DB, OKE, GPU, NAT Gateway, paid Load Balancer, paid RPC
- Migrating sealed JSONL / marks / EXP provenance into Postgres as source of truth
- Unparking bonk/mayhem

## Review trigger

- **Provision complete (2026-09-23).** Remaining: owner **implements** Cursor→Oracle access ([DEC-011](DEC-011-cursor-oracle-access-cf-tunnel.md)); then observe/paper bootstrap on `/var/lib/mal`. Do **not** claim the tunnel exists.
- Idle-instance reclamation risk (Always Free idle policy) or block-volume approaching **200 GB** combined — legitimate workload only; never fake keep-alive.
- Measured need that would open a DEC-008 rung (RPC 429s, disk, uptime) — new DEC, not silent spend.
- aarch64 gap that blocks observe/paper cutover → Helm, then amend BOM (still Always Free first).

## Overturn path

New DEC. Default remains Always Free **2 OCPU / 12 GB** single A1 + JSONL EXP spine until then.
