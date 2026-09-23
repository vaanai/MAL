# Oracle Phase-0 handoff (durable Lab memory)

| | |
| --- | --- |
| **As-of** | 2026-09-23 |
| **Status** | **Provisioned and verified by Vaan.** Host is live. Access design **DEC-011 decided, pending owner implement.** Agents do **not** yet have SSH. |
| **Locks** | [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md) (Always Free envelope), [DEC-010](../DEC/DEC-010-oracle-phase0-handoff-autonomy.md) (handoff + autonomy), [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) (access design **decided, pending owner implement**) |
| **BOM (envelope / foot-guns)** | [ORACLE-ALWAYS-FREE-BOM-v0.md](ORACLE-ALWAYS-FREE-BOM-v0.md) — inventory names here supersede pre-create placeholders |
| **Decision log format** | [ENGINEERING-DECISION-LOG.md](ENGINEERING-DECISION-LOG.md) |

Condensed from Vaan’s Oracle Phase-0 handoff. **Encode facts; this is not a PDF dump.** No secrets, no real SSH CIDR, no passwords, no OCIDs.

---

## 1. Mission (direction)

Oracle Phase 0 infrastructure is **done**. Grok managers own building, configuring, testing, and operating the meme-coin research/paper system. Vaan provides direction, major architecture/security/capital decisions, and human authorization.

Agents should continue useful work while the owner is unavailable. **Escalate only** when human input is genuinely required (spend, security boundary, capital, credentials). Primary implementation workforce = **Cursor**. Grok **plans, reviews, integrates** — does not unnecessarily implement.

**Autonomy is encouraged. Uncontrolled expansion of cost, privileges, credentials, or security risk is not.**

---

## 2. Product north star (unchanged)

Fast, continuously operating Pump.fun / Solana meme-coin research, later trading. Continuously observe new coins and ecosystem activity; keep token/account/creator state; eventually **LAYA** as decision/buyer. **X is a later additive layer**, not the foundation.

Not merely `token → AI → trade`. Build a **stateful ecosystem representation**, including relationships among wallets, creators, accounts, communities.

**Three intelligence layers** → precompute → **LAYA** → **risk gate** → **exec deferred**. Paper first. Prove durable edge before scaling infra or spend.

Plans are **directions, not rigid locks**. Profit (not revenue theater) is the goal. **Change a stupid lock** rather than workaround it.

**North-star memory rule:** seat agents retain **overall project direction**. GitHub Lab memory holds **actionable / ops detail** to reload. Prefer direction retention over stuffing all detail into chat. Do not let local assumptions silently redefine the project.

---

## 3. Provisioned inventory (verified)

Encode **exactly**. Still **2 OCPU / 12 GB** — **not** 4/24.

### Compute

| Field | Value |
| --- | --- |
| Instance | **`mal-core-0`** |
| Cloud | Oracle Cloud Infrastructure |
| Region / AD | **Phoenix**, **AD-1** |
| Shape | **`VM.Standard.A1.Flex`** |
| Size | **2 OCPU / 12 GB RAM**, **aarch64 / ARM64** |
| Swap | **2 GB** |
| OS | **Ubuntu 24.04 Minimal** |
| Envelope | Always Free eligible; **$0/mo** intent |

### Storage

| Volume | Size | Notes |
| --- | --- | --- |
| Boot | **50 GB** | OS |
| Data `mal-core-data` | **150 GB** | Mounted **`/var/lib/mal`** |

Combined block = **200 GB** (Always Free cap). Data volume is for Postgres, sealed JSONL / provenance, paper marks, and other runtime data.

### Network

| Object | Value |
| --- | --- |
| VCN | **`mal-vcn`** `10.0.0.0/16` |
| Subnet | **`mal-public`** `10.0.1.0/24` |
| IGW | **`mal-igw`** |
| NSG | **`mal-core-nsg`** |
| SSH ingress | **Owner home public IP only** (never commit the real CIDR) |
| Public app ports | **None** |

PostgreSQL **must remain private** and **must never** be exposed publicly.

### PostgreSQL (operational)

| Field | Value |
| --- | --- |
| Version | **16.15** (installed, connection tested) |
| Database | **`meme_core`** |
| Role | **`mal_app`** — **non-superuser**; cannot create databases or roles |
| Bind | **localhost only**; port 5432 **not** public |
| Data dir | **`/var/lib/mal/postgresql/16/main`** |
| Password | Held by **owner only**. **Never** in git, docs, chat, or plaintext artifacts |

### Host security baseline (verified)

- SSH public-key auth on; password auth **off**; keyboard-interactive **off**; root password login **off**
- OCI NSG is the primary network perimeter
- Ubuntu unattended security updates **on**
- **No wallet private keys** on the machine
- **No trading authorization / private keys** on the machine
- **No trading capital** accessible to development agents
- System is **paper-only**

**Do not claim agents already have SSH or host access.** Access **design** is [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) (**decided, pending owner implement**). Plumbing is the next owner **implement** (§7) — not an open architecture pick.

---

## 4. Data architecture law

| Layer | Role |
| --- | --- |
| **Sealed JSONL** | Provenance / event **spine**. Append-only, reproducible observations/events. |
| **PostgreSQL** | Operational / **state** layer: current token/wallet/relationship state, derived features, indexes, fast lookups, paper-trading state, ops metadata, other mutable/query-oriented data. |

**Do not treat Postgres as a mandatory replacement for provenance.** Exact schema and `/var/lib/mal` subdirectory layout are for the implementation team, within this intent.

---

## 5. Workflow law

```
Grok managers  →  plan / decompose
Cursor         →  implement / test / verify
Grok managers  →  review / integrate
               →  next task
```

Use Cursor **aggressively** for: application code, data models, schemas/migrations, ingest/parsers/event processing, token/account state, relationship/graph, paper-marking, logging, tests, services, deployment, monitoring, backups, debugging, refactoring, infra inspection, ops scripts, docs generated from implementation state.

Grok concentrates on: architecture, decomposition, coordination, delegation, review, validation, security, integration, project-level decisions, escalation to Vaan.

---

## 6. Cursor observability (standing rule)

**Whenever any manager launches a Cursor agent** — even after group-chat collab — **DM Vaan a Cursor-agent status card**.

The owner must be able to see running / done and **open the run** (instructions + work) without asking. He will not inspect every run; the card is transparency, not a request for supervision.

---

## 7. Cursor → Oracle access (**DEC-011 decided**, pending owner implement)

Desired path (no human PC in the loop):

```
Human  →  Grok  →  Cursor Cloud / agents  →  mal-core-0  →  Postgres / JSONL / runtime
```

**The human PC must not be a permanent networking dependency.** Owner should be able to shut the PC and have agents continue.

**Architecture locked in [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md)** (2026-09-22 council; Helm + Scout/Graph/Proof soft OK):

| Role | Choice |
| --- | --- |
| **Chosen** | Cloudflare Tunnel (`cloudflared`) **on `mal-core-0`** + Cloudflare Access for **SSH** + dedicated **`mal-cursor` ed25519** deploy key (**not** the owner personal key) |
| **Backup** | Tailscale **on the Oracle VM** (not owner PC) + ephemeral agent auth keys |
| **Parked** | Cursor My Machines on-box as phase-0 default (aarch64 / resource risk on Always Free **2 OCPU / 12 GB**) |
| **Rejected** | Human PC as permanent jump host; sharing owner personal SSH key; public Postgres; opening SSH :22 to the world |
| **Status** | **Decided, pending owner implement.** Do **not** claim the tunnel, Access hostname, or agent SSH already exists. |

**Owner implements** tunnel/Access/`mal-cursor` in-console / on-host. Agents get access **only after** owner confirms hostname + `mal-cursor` key ready as a **Cursor secret**. **No secrets, real hostnames, CIDRs, or keys in git** (placeholder only: `MAL_CURSOR_SSH_HOSTNAME`).

**Do not:**

- expose Postgres (or extra app ports) publicly to simplify agent access — **never** a public DB endpoint on the tunnel
- hand agents the owner’s **personal SSH private key** as a shortcut
- make the owner PC a required always-on hop
- deploy access plumbing autonomously before owner implementation + confirm
- put trading / X keys on the host
- treat this section as “tunnel is live”

Agents **do not** currently have SSH to `mal-core-0`. Sealed **JSONL** remains provenance/EXP spine.

---

## 8. Autonomy vs escalate

**OK without asking:** routine install/config, directories, service restarts, Postgres schemas/tables/indexes/migrations, deploy app code, tests, logs, monitoring, routine backups, debugging, cleanup, internal tooling, infra inspection, maintaining the application — **inside** the approved Always Free box and security baseline.

**Escalate to Vaan before:**

- billing / spending money
- expanding beyond approved Always Free
- weakening a security boundary
- paid RPC, GPU, extra VMs, OKE, NAT Gateway, Load Balancer, Autonomous DB
- unnecessary extra block/object storage
- public Postgres or extra public app ports
- trading/wallet private keys or trading authorization on the host
- capital access for Cursor/Grok
- X/Twitter credentials “because a future layer might need them”
- other external credentials not required **now**
- PAYG upgrade (even if the intent is still Always Free + alerts)

Owner is not against paying if it means more profit — **wants transparency and a clear reason first**. If a needed feature looks paid, first hunt an Always Free / existing-resource path.

**Idle reclaim:** Always Free may reclaim idle compute. Prefer **legitimate continuous workload** (ingest, monitor, paper, health, maintenance). **Never fake keep-alive.** If guaranteed persistence beyond idle policy is required, escalate.

---

## 9. Open options / research (do not invent a pick)

| Item | Status |
| --- | --- |
| **Real paper-trading utility** for meme coins (not only “would have” marks) | Owner-offered **open option**. **Ask before adopting.** |
| **Trading / wallet execution surface** (Axiom, Phantom, other) | **Open research.** May affect design. Send a Cursor research bot **if needed** (and **DM the status card**). **Do not pick a stack in this handoff.** |
| External integrations (Solana RPC/WSS, Pump.fun feeds, later X, other market data) | Clean interfaces + config **placeholders**. Do not prematurely store credentials. X remains later additive. |
| Graph / relationship layer | Required capability (wallets, creators, deployers, accounts, communities, tokens, txs, funding/creation/interaction/temporal edges). Exact graph impl is engineering’s. Tokens must not be treated as isolated events. |

Paper path now: observe → parse → store → track → analyze → paper mark → evaluate → improve. Live trading is a **separate future security decision**, not an automatic unlock.

---

## 10. Immediate next (after this memory lands)

Foundation is a **secure blank Oracle workshop**. Next: smallest robust path to **observable, reproducible, continuously operable** research infra.

**Owner implement first:** [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) Cursor↔Oracle access (§7) — tunnel + Access + `mal-cursor` Cursor secret. Then confirm to managers. **Do not** claim it is already live.

**Then bootstrap (team, once access exists):** `/var/lib/mal` subdirs, app layout, sealed JSONL storage, Postgres schema, token/wallet/relationship state, event ingestion, paper marks, logging, monitoring, backups, services, runtime orchestration, tests, deployment, persistent engineering memory.

Do **not** over-engineer. Do **not** wait idle for the owner PC.

---

## 11. What this document is not

- Not a claim that agents can SSH today
- Not a claim that Cloudflare Tunnel / Access / `mal-cursor` access is already live ([DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) is **pending owner implement**)
- Not a license to put secrets in git or chat
- Not a 4 OCPU / 24 GB resize
- Not a live-trading enablement
- Not a PDF reprint
