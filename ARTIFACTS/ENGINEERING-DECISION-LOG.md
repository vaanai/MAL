# Engineering decision log

Persistent trail for **meaningful** infra / implementation changes. **DEC/** remains the lock. This log lets a future agent reload **what changed** without reconstructing chat.

Handoff source: Oracle Phase-0 §19 — for each meaningful change record **what / why / tested / verification / current state / rollback / unresolved / architectural implications**.

**Newest first.** No secrets, CIDRs, passwords, or keys.

## Entry format

| Field | Intent |
| --- | --- |
| **ID** | `EDL-NNN` (monotonic) |
| **Date** | Calendar date |
| **What changed** | Concrete delta |
| **Why** | Decision driver |
| **What was tested** | How it was checked (or “docs-only / N/A”) |
| **Verification** | Result |
| **Current state** | What is true now |
| **Rollback** | How to undo or freeze |
| **Unresolved** | Open asks / risks |
| **Implications** | Architecture / ops consequences |
| **Pointers** | DEC / EXP / artifacts |

---

## EDL-002 — Cursor↔Oracle access design locked (DEC-011)

| Field | Value |
| --- | --- |
| **ID** | EDL-002 |
| **Date** | 2026-09-22 (council); recorded 2026-09-23 |
| **What changed** | Open DEC-010 access ask → **DEC-011 decided.** Chosen: Cloudflare Tunnel (`cloudflared`) on `mal-core-0` + Cloudflare Access for SSH + dedicated `mal-cursor` ed25519 deploy key. Backup: Tailscale on the Oracle VM + ephemeral agent keys. |
| **Why** | Agents need a path to the host without the human PC as a permanent hop, without sharing the owner personal SSH key, and without public Postgres or world-open :22. |
| **What was tested** | Docs-only / N/A. Council soft-OK Helm + Scout/Graph/Proof. **No** tunnel deployed by this PR. |
| **Verification** | Design recorded. Owner implement **not** done. Agents: **no** host access. |
| **Current state** | **Decided, pending owner implement.** Do **not** claim tunnel/Access/hostname/`mal-cursor` SSH exists. Next owner: implement DEC-011, then confirm hostname + Cursor secret. |
| **Rollback** | Docs: revert this PR / new DEC. Infra: nothing to unroll yet (plumbing not claimed). Do not fall back to PC-as-hop or owner personal key. |
| **Unresolved** | Owner implement + confirm. Optional real paper-trading utility (ask first). Trading/wallet execution surface (Axiom / Phantom / etc.) — **no pick.** My Machines remain **parked**. |
| **Implications** | Postgres stays localhost; JSONL stays provenance/EXP spine; no trading/X keys on host; `mal-cursor` is the agent key (not owner personal); Tailscale-on-Oracle is backup only; My Machines not phase-0 default (aarch64 / 2 OCPU/12 GB risk). |
| **Pointers** | [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md), [DEC-010](../DEC/DEC-010-oracle-phase0-handoff-autonomy.md), [ORACLE-PHASE0-HANDOFF.md](ORACLE-PHASE0-HANDOFF.md) |

---

## EDL-001 — Oracle Phase 0 host provisioned (handoff)

| Field | Value |
| --- | --- |
| **ID** | EDL-001 |
| **Date** | 2026-09-23 |
| **What changed** | Lab memory: `mal-core-0` **pending** → **provisioned and verified**. DEC-010 locks inventory, JSONL vs Postgres roles, manager/Cursor workflow, DM status-card rule, no-PC-dependency access, escalate-before-spend/security, paper-only. |
| **Why** | Vaan provisioned Always Free inventory and handed autonomy to Grok+Cursor within fences. Pre-create DEC-009 text was stale. |
| **What was tested** | Owner-verified: SSH baseline, NSG, Postgres 16.15 localhost bind, `mal_app` on `meme_core`, data dir on `/var/lib/mal`. This PR is **docs-only** (no agent SSH). |
| **Verification** | Owner: host live, paper-only, no wallet/trading keys on box. Agents: **no** host access yet. |
| **Current state** | Secure blank Oracle workshop. Access **design** locked in EDL-002 / [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) — **pending owner implement.** Then bootstrap dirs/schema/ingest. EXP-002c facts **unchanged**. |
| **Rollback** | Docs: revert this PR. Infra: do **not** destroy Always Free resources without Vaan. Do not revert to “laptop is 24/7 host.” |
| **Unresolved** | Owner **implement** of DEC-011 (not an architecture pick). Optional real paper-trading utility (ask first). Trading/wallet execution surface research (Axiom / Phantom / etc.) — **no pick**. |
| **Implications** | JSONL stays provenance spine; Postgres is ops/state only. Human PC must not be a permanent networking hop. DM Vaan a Cursor status card on every Cursor launch. |
| **Pointers** | [DEC-010](../DEC/DEC-010-oracle-phase0-handoff-autonomy.md), [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md), [ORACLE-PHASE0-HANDOFF.md](ORACLE-PHASE0-HANDOFF.md), [ORACLE-ALWAYS-FREE-BOM-v0.md](ORACLE-ALWAYS-FREE-BOM-v0.md) |
