# DEC-011 — Cursor↔Oracle access: Cloudflare Tunnel + Access

| Field | Value |
| --- | --- |
| **Status** | **Decided; owner implementing.** Tunnel / Access / `mal-cursor` key do **not** exist in Lab memory as live plumbing. **Do not** claim agents already have SSH. |
| **Decider** | Vaan (accepted). **Helm recommendation.** Council **soft-OK** Scout / Graph / Proof (2026-09-22). |
| **Date** | 2026-09-22 (decision); recorded 2026-09-23 |
| **Amends** | [DEC-010](DEC-010-oracle-phase0-handoff-autonomy.md) §5 (open recommend → **chosen path**; owner still implements) |
| **Does not amend** | Always Free **2 OCPU / 12 GB** envelope ([DEC-009](DEC-009-oracle-always-free-phase0-host.md)); cheap-first ([DEC-008](DEC-008-stack-phase-gates.md)); paper path ([DEC-006](DEC-006-detect-decode-evaluate-runners.md)); JSONL provenance spine; no trading / X keys on host; EXP-002c closed facts |
| **Handoff** | [ORACLE-PHASE0-HANDOFF.md](../ARTIFACTS/ORACLE-PHASE0-HANDOFF.md) §7 |
| **Decision log** | [ENGINEERING-DECISION-LOG.md](../ARTIFACTS/ENGINEERING-DECISION-LOG.md) |

## Decision

1. **Chosen path:** **Cloudflare Tunnel** (`cloudflared`) **on `mal-core-0`** + **Cloudflare Access** gating **SSH** + a dedicated **`mal-cursor` ed25519 deploy key**. That key is **not** the owner’s personal SSH key. Desired path remains: Human → Grok → Cursor Cloud → `mal-core-0` → Postgres/JSONL/runtime — **without** the human PC as a hop.

2. **Backup path (if chosen stalls):** **Tailscale on the Oracle VM** (not on the owner PC as the required hop) + **ephemeral agent auth keys**. Same fences as the chosen path (localhost Postgres, no owner personal key, no public `:22`).

3. **Postgres stays localhost-only.** Never publish Postgres (or extra app ports) via the tunnel as a public DB hostname. Agents that eventually SSH use **on-box** `meme_core` / `mal_app`. NSG/ufw **deny public 5432**.

4. **Owner SSH break-glass stays owner-only.** Home-IP `:22` (never commit the real CIDR) + the owner’s personal key remain **Vaan’s** console path. **Do not** open SSH `:22` to the world to “make agents work.” **Do not** hand agents the owner personal private key.

5. **Secrets / names.** No tunnel hostnames, Access policy IDs, account IDs, CIDRs, or key material in git, this DEC, or chat. Placeholder identity in Lab memory: **`mal-cursor`**. Password / trading / X rules unchanged: **none of those on the host.**

6. **Implementation status.** **Decided.** **Owner implements** in-console / on-host. Agents must **not** deploy `cloudflared`, Access, Tailscale, or keys autonomously. After owner says plumbing is ready, team bootstraps `/var/lib/mal` (dirs, `meme_core` schema, sealed JSONL ingest, paper, monitoring).

7. **Cursor My Machines parked** as the phase-0 **default** (aarch64 / resource risk on the 2 OCPU / 12 GB ARM box). Not a forever ban; not the access design we are implementing now.

## Tradeoffs

| Option | Why it helps | Cost / risk | Verdict |
| --- | --- | --- | --- |
| **CF Tunnel + Access + `mal-cursor` key** | Outbound-only daemon (no extra public listeners); identity gate in front of SSH; dedicated agent key; no PC hop; fits Always Free | Owner Cloudflare/Access ops; small `cloudflared` footprint on the box | **Chosen** |
| **Tailscale on Oracle + ephemeral auth keys** | Private overlay; no public `:22`; no PC hop | Second overlay; auth-key hygiene | **Backup** |
| **Cursor My Machines on-box** | Native Cursor UX | **aarch64** + RAM/CPU on a **2/12** Always Free ARM VM — phase-0 resource risk | **Park** (not default) |
| Human PC as jump host | Familiar SSH | PC must stay on; violates DEC-010 | **Rejected** |
| Share owner personal SSH key with agents | Fast shortcut | Mixes owner identity with ephemeral workers; blast radius | **Rejected** |
| Public Postgres (or tunneled public DB) | Convenient remote SQL | Constitution / DEC-010 fence; internet-facing state DB | **Rejected** |
| Open SSH `:22` to the world | Convenient remote shell | Scanners; NSG violation | **Rejected** |

## Constraints (hard)

- **Sealed JSONL** remains the provenance / EXP spine. Postgres remains **ops/state** (`meme_core` / `mal_app`). This DEC does not migrate provenance into Postgres.
- **No trading keys and no X keys** on `mal-core-0`. Paper only. Agents never get trading capital.
- **No invented hostnames / CIDRs / secrets** in Lab memory.
- **Do not** claim the tunnel already exists. Status is **decided; owner implementing.**

## Council

Helm recommended the chosen path. Scout / Graph / Proof **soft-OK** (2026-09-22). Owner **accepted** and will implement. Soft flags are not hard blockers:

| ID | Flag | How to treat |
| --- | --- | --- |
| A1 | JSONL spine + localhost Postgres unchanged | Soft reaffirm of DEC-009/010 |
| A2 | `cloudflared` on aarch64 / 2/12 envelope | Soft. Prefer official aarch64 bits; escalate Helm if the daemon does not fit |
| A3 | Access policy + `mal-cursor` key hygiene | Soft. Owner-held; never git |
| A4 | My Machines remain parked as default | Soft. Revisit only with a new DEC if aarch64/resource picture changes |

## Rationale

- DEC-010 left access as an **open owner ask**: recommend with tradeoffs, then Vaan implements. This DEC **locks the recommendation**.
- Tunnel + Access keeps the OCI NSG tight (owner-IP `:22`, **no public app ports**) while letting ephemeral Cursor workers reach the box **without** a human PC.
- A **dedicated deploy key** separates agent blast radius from the owner’s personal SSH identity.
- Tailscale-on-Oracle is a **real** backup (same no-PC, no-public-DB fences) if Cloudflare path blocks.
- My Machines **on the box** would compete with observe/Postgres on a small ARM VM and is an aarch64 unknown — park, do not default.

## Out of scope (this DEC)

- Installing `cloudflared` / Access / Tailscale / keys (owner action; **not done**)
- Granting agents SSH today
- Public 443 as a website; public Postgres; extra public app ports
- Live execution, wallet keys, X credentials, capital
- PAYG / leaving Always Free / 4 OCPU / 24 GB
- Unparking bonk/mayhem; choosing a trading/wallet surface

## Next steps

**Owner (implements DEC-011):**

1. Run **`cloudflared`** on **`mal-core-0`** (outbound to Cloudflare; **no** extra public listeners).
2. Gate **SSH** with **Cloudflare Access** (identity / service-token — owner chooses; **do not** document secrets here).
3. Create dedicated **`mal-cursor` ed25519** deploy key: public half on the host; private half owner-held for agents. **Not** the owner personal key. **Not** in git.
4. Keep owner personal key + home-IP `:22` as **owner break-glass**. **Do not** open `:22` to the world.
5. Confirm Postgres still **localhost-only**. **Do not** publish a public/tunneled DB hostname.
6. Tell Helm when plumbing is ready. Until then, agents **do not** have SSH.

**Team (after owner says access exists):** bootstrap `/var/lib/mal` subdirs, sealed JSONL on the host, `meme_core` schema, ingest, paper marks, logging/monitoring/backups, services. Do **not** idle-wait on the owner PC. Do **not** fake Always Free keep-alive.

## Review trigger

- Owner reports tunnel/Access/`mal-cursor` ready — or rejects and falls back to Tailscale-on-Oracle (same fences; log in [ENGINEERING-DECISION-LOG.md](../ARTIFACTS/ENGINEERING-DECISION-LOG.md)).
- Proposal to unpark My Machines on-box, open public app ports, or expose Postgres.
- `cloudflared` aarch64/resource miss on the 2/12 envelope → Helm, then consider backup path.

## Overturn path

New DEC. Default remains: **CF Tunnel + Access + `mal-cursor` key**, owner implementing, Postgres localhost, JSONL provenance spine, no agent SSH until owner says so.
