# DEC-010 — Oracle Phase-0 handoff: provisioned host, data roles, autonomy

| Field | Value |
| --- | --- |
| **Status** | Active (working law) |
| **Decider** | Vaan (lab policy; host **provisioned and verified**) |
| **Date** | 2026-09-23 (recorded) |
| **Amends** | [DEC-009](DEC-009-oracle-always-free-phase0-host.md) (pending → **provisioned**; ops/autonomy/access laws) |
| **Amended by** | [DEC-011](DEC-011-cursor-oracle-access-cf-tunnel.md) (§5 access ask → **chosen path LIVE** 2026-09-23) |
| **Does not amend** | Cheap-first / measure-before-pay ([DEC-008](DEC-008-stack-phase-gates.md)); paper path ([DEC-006](DEC-006-detect-decode-evaluate-runners.md)); full detect book ([DEC-007](DEC-007-full-detect-book-anti-selection-bias.md)); EXP-002c closed facts; Always Free **2 OCPU / 12 GB** envelope (DEC-009) |
| **Handoff** | [ORACLE-PHASE0-HANDOFF.md](../ARTIFACTS/ORACLE-PHASE0-HANDOFF.md) |
| **Access** | [DEC-011](DEC-011-cursor-oracle-access-cf-tunnel.md) (**LIVE** 2026-09-23) |
| **BOM** | [ORACLE-ALWAYS-FREE-BOM-v0.md](../ARTIFACTS/ORACLE-ALWAYS-FREE-BOM-v0.md) (provisioned names; envelope still DEC-009) |
| **Decision log** | [ENGINEERING-DECISION-LOG.md](../ARTIFACTS/ENGINEERING-DECISION-LOG.md) |

## Decision

1. **Host is live.** Oracle Always Free **`mal-core-0`** (Phoenix **AD-1**, **`VM.Standard.A1.Flex`**, **2 OCPU / 12 GB**, **aarch64**, Ubuntu **24.04** Minimal, 2 GB swap) is **provisioned and verified by Vaan**. Boot **50 GB**; data volume **`mal-core-data`** **150 GB** at **`/var/lib/mal`**. VCN **`mal-vcn`** `10.0.0.0/16`; subnet **`mal-public`** `10.0.1.0/24`; IGW **`mal-igw`**; NSG **`mal-core-nsg`**. SSH ingress = owner home public IP only (break-glass); **no public app ports**. Cursor agents SSH via [DEC-011](DEC-011-cursor-oracle-access-cf-tunnel.md) Access TCP (hostname **`mal-core-vnic`**).

2. **Postgres operational, not provenance SoT.** PostgreSQL **16.15**, DB **`meme_core`**, role **`mal_app`** (non-superuser), **localhost only**, data dir **`/var/lib/mal/postgresql/16/main`**. Password held by owner — **never** in repo, docs, or chat. **Sealed JSONL** remains the append-only **provenance / event spine**. Postgres is the **operational / state** layer (token, wallet, relationship, derived, paper, ops). **Do not** treat Postgres as a mandatory replacement for provenance.

3. **Manager / Cursor workflow.** Grok managers **plan and review**. Cursor **implements and tests** (code, schema, ingest, graph, paper, monitoring — aggressively). Grok **integrates**. Cursor workers remain ephemeral; Grok seats remain the persistent managers ([DEC-001](DEC-001-lean-four-override.md), [DEC-002](DEC-002-memory-first-no-db-local.md)).

4. **Cursor DM status-card rule.** Whenever **any** manager launches a Cursor agent — **even after group-chat collab** — **DM Vaan a Cursor-agent status card** so he can see running/done and open the run. Transparency, not required supervision of every run.

5. **No permanent PC networking dependency.** Desired path: Human → Grok → Cursor Cloud → `mal-core-0` → Postgres/JSONL/runtime. **Access LIVE in [DEC-011](DEC-011-cursor-oracle-access-cf-tunnel.md):** Cloudflare Tunnel (`cloudflared`) on `mal-core-0` + Cloudflare Access Service Auth gating SSH + dedicated **`mal-cursor`** deploy key in Cursor Runtime Secrets (not the owner personal key). Backup: Tailscale **on the Oracle VM** + ephemeral agent auth keys. **Do not** expose Postgres publicly. **Do not** hand agents the owner’s personal SSH private key. **Do not** make the human PC a required always-on hop. **Do not** open SSH `:22` to the world. Cursor My Machines on-box is **parked** as phase-0 default.

6. **Autonomy inside the fence; escalate before spend/security.** Routine ops on the approved box are in-bounds. **Escalate to Vaan before** billing, leaving Always Free, weakening security, paid RPC/GPU/extra VMs/OKE/NAT/LB/Autonomous DB, public Postgres, extra public app ports, trading keys or X creds on host, or capital access. Always Free idle-reclaim: **legitimate** continuous workload (ingest/monitor/paper) — **never** fake keep-alive.

7. **Paper-only; no capital; no keys on host.** No wallet/trading keys on `mal-core-0`. Agents **never** get trading capital. Live exec remains deferred. Optional **real** meme-coin paper-trading utility is an **owner-offered open option** — **ask before adopting**. Trading/wallet stack (Axiom / Phantom / etc.) is an **open research item**; **do not invent a pick** in this DEC.

8. **Memory rule.** Seat agents retain **overall project direction**. GitHub Lab memory holds **actionable / ops detail**. Plans are **directions, not rigid locks**; profit is the goal — change a stupid lock rather than workaround it. Persistent engineering changes use [ENGINEERING-DECISION-LOG.md](../ARTIFACTS/ENGINEERING-DECISION-LOG.md) (what/why/tested/result/state/rollback/unresolved/implications).

## Rationale

- Pre-create DEC-009 sized and fenced the Always Free experiment. Vaan has now **created and verified** that inventory; Lab memory must stop saying “pending / nothing created.”
- Provenance vs ops split keeps EXP knowable-at-T audit (JSONL) while unblocking Layer-2 / paper / indexes on the already-installed `meme_core`.
- Cursor-as-workforce + Grok-as-managers matches the owner’s operating model and existing lean-four seats.
- Status cards give the owner cheap observability without blocking autonomy.
- Access architecture is a **security-boundary** change: [DEC-011](DEC-011-cursor-oracle-access-cf-tunnel.md) locks CF Tunnel + Access Service Auth + dedicated agent key (**LIVE** 2026-09-23); no public DB, no owner-key shortcut, no PC-as-hop, no public `:22`.
- Escalate-before-spend preserves cheap-first (DEC-008) and Always Free 2/12 (DEC-009) without pretending the owner will never pay.

## Out of scope (this DEC)

- Granting extra public listeners, live execution, Jito, PumpPortal trading API, wallet signing, capital
- Choosing Axiom vs Phantom vs other execution surfaces
- Adopting a real paper-trading venue without asking
- PAYG upgrade, paid shapes, 4 OCPU / 24 GB
- Migrating sealed JSONL / EXP provenance into Postgres as SoT
- Unparking bonk/mayhem; X keys on host

## Review trigger

- Access path changes (token rotation, Tailscale fallback, My Machines unpark) — log in [ENGINEERING-DECISION-LOG.md](../ARTIFACTS/ENGINEERING-DECISION-LOG.md); new DEC if the chosen path is abandoned.
- Measured need that would open a DEC-008 rung or leave Always Free — new DEC, not silent spend.
- Proposal to put any trading/X credential on `mal-core-0` or to enable live exec.
- Idle-reclaim event or block-volume pressure at the 200 GB cap.

## Overturn path

New DEC. Default remains: provisioned Always Free **2 OCPU / 12 GB** `mal-core-0`, JSONL provenance spine, Postgres ops/state, paper-only, no agent capital, no PC-as-permanent-hop, escalate-before-spend/security.
