# DEC-011 — Cursor↔Oracle access via Cloudflare Tunnel + Access

| Field | Value |
| --- | --- |
| **Status** | **Decided, pending owner implement.** Do **not** claim a tunnel, Access policy, hostname, or agent SSH already exists. |
| **Decider** | Helm (lab policy). Council **soft-OK** Scout / Graph / Proof (2026-09-22) — soft flags below, not hard blockers. |
| **Date** | 2026-09-22 (council); recorded 2026-09-23 |
| **Amends** | [DEC-010](DEC-010-oracle-phase0-handoff-autonomy.md) §5 / handoff §7 (**open access ask → decided design**; plumbing still owner-implemented) |
| **Does not amend** | Always Free **2 OCPU / 12 GB** envelope ([DEC-009](DEC-009-oracle-always-free-phase0-host.md)); cheap-first ([DEC-008](DEC-008-stack-phase-gates.md)); paper path ([DEC-006](DEC-006-detect-decode-evaluate-runners.md)); full detect book ([DEC-007](DEC-007-full-detect-book-anti-selection-bias.md)); JSONL provenance spine vs Postgres ops/state (DEC-010); EXP-002c closed facts |
| **Handoff** | [ORACLE-PHASE0-HANDOFF.md](../ARTIFACTS/ORACLE-PHASE0-HANDOFF.md) §7 |
| **Decision log** | [ENGINEERING-DECISION-LOG.md](../ARTIFACTS/ENGINEERING-DECISION-LOG.md) (EDL-002) |

## Product / fence alignment

Access plumbing is a **security-boundary** change so Cursor Cloud agents can reach **`mal-core-0`** without the human PC as a hop. It does **not** enable live trading, put keys on the host, expose Postgres, or resize Always Free.

Desired path (unchanged from DEC-010):

```
Human  →  Grok  →  Cursor Cloud / agents  →  mal-core-0  →  Postgres / JSONL / runtime
```

Agents **still do not have SSH** until the owner confirms implement-complete (hostname + `mal-cursor` key ready as a **Cursor secret**). **No secrets, CIDRs, real hostnames, or keys in this repo.**

## Decision

1. **Chosen:** Cloudflare Tunnel (`cloudflared`) **on `mal-core-0`** + **Cloudflare Access** in front of **SSH** + a dedicated **`mal-cursor` ed25519 deploy key** (authorized on the host for agent use). This is **not** the owner’s personal SSH key.
2. **Backup (if chosen path fails or is blocked):** **Tailscale on the Oracle VM** (not on the owner PC as the required hop) + **ephemeral agent auth keys**. Same fences: no public Postgres, no owner personal key, no PC-as-permanent-hop.
3. **Rejected:** human PC as a **permanent jump host**; sharing the **owner’s personal SSH private key** with agents; **public Postgres** (or a tunnel hostname that is a public DB endpoint); opening **SSH :22 to the world**; **Cursor My Machines on-box** as the **phase-0 default** (**park** — aarch64 / resource risk on Always Free **2 OCPU / 12 GB**).
4. **Owner implements** tunnel + Access + host `authorized_keys` for `mal-cursor`. Agents do **not** deploy this autonomously. Agents get access **only after** the owner confirms: (a) SSH hostname is ready (operational fact — **not** committed here), and (b) `mal-cursor` private key is stored as a **Cursor secret** (never git). Until that confirm, Lab memory stays: **decided, not deployed**.
5. **Postgres remains localhost-only.** Tunnel may carry **SSH** (and later, if separately decided, other **private** app paths). **Never** expose Postgres via tunnel as a public DB endpoint. Port 5432 stays bound to localhost; NSG still has **no public app ports**.
6. **No trading / X keys on host.** Access plumbing is not a reason to place wallet, trading, or X/Twitter credentials on `mal-core-0`.
7. **Sealed JSONL remains the provenance / EXP spine** (Proof soft flag). Access choice does not migrate EXP/knowable-at-T truth into Postgres.

### Council soft flags (not hard blockers)

| ID | Flag | How to treat |
| --- | --- | --- |
| S1 | **JSONL spine** — sealed JSONL stays provenance / EXP / knowable-at-T truth; Postgres stays ops/state | Soft (Proof). Access DEC must not quietly make Postgres the EXP SoT. |
| S2 | **No trading keys and no X keys** on the host | Soft reaffirm of a hard secret rule. Tunnel ≠ credential stash. |
| S3 | **My Machines parked** as phase-0 default — aarch64 + RAM/CPU risk on **2 OCPU / 12 GB** Always Free | Soft. Revisit only with a new DEC after measured headroom or a supported aarch64 story. |
| S4 | **Do not claim the tunnel exists** until owner confirm | Soft process flag. Status is decided / pending implement. |

## Chosen path (intent — not a claim it is live)

Owner-side, **when implemented**:

- `cloudflared` runs **on `mal-core-0`** (outbound HTTPS to Cloudflare; **no new public inbound** on the NSG for SSH-to-the-world or apps).
- Cloudflare Tunnel origin for this DEC is **SSH on localhost** (existing `sshd`). **Not** Postgres.
- **Cloudflare Access** gates who can reach that SSH hostname (identity policy). Break-glass for the owner remains the existing **owner-home-IP NSG SSH** path + owner’s own key — still **not** shared with agents.
- A dedicated **`mal-cursor` ed25519** keypair: public half in host `authorized_keys` (agent principal only); private half **only** as a Cursor secret / owner secret store. **Rotate/revoke** this key without touching the owner’s personal key.
- Git placeholders only (never real values): `MAL_CURSOR_SSH_HOSTNAME`, `mal-cursor` key name. Same rule as `VAAN_SSH_CIDR`.

**Not in this DEC:** Access policy IdP details, real hostnames, tunnel UUIDs, account IDs, CIDRs, or install commands that belong in owner console work.

## Tradeoffs

| Option | Why it helps | Cost / risk | Verdict |
| --- | --- | --- | --- |
| **CF Tunnel + Access + `mal-cursor` key** | No PC hop; no public :22; identity gate; dedicated revocable key; outbound-only from the VM; Postgres can stay localhost | Owner Cloudflare/Access work; `cloudflared` is another small daemon on a tight **2/12** box; hostname + key are secrets (not git) | **Chosen** |
| **Tailscale on Oracle + ephemeral agent keys** | Also no PC hop; mesh; no public :22; keys can be short-lived | Overlay + ACL ops; still owner install on the VM; must not land Tailscale on the **owner PC as the hop** | **Backup** |
| **Cursor My Machines on `mal-core-0`** | Native Cursor worker host | **aarch64** + RAM/CPU contention on Always Free **2 OCPU / 12 GB**; phase-0 default would compete with Postgres/observe | **Parked** (not phase-0 default) |
| **Owner PC as jump / always-on hop** | Fast for a human at the desk | PC must stay on; violates DEC-010 “no permanent PC networking dependency” | **Rejected** |
| **Share owner personal SSH key** | Zero new key material | Blast radius; hard to revoke independently; violates DEC-010 | **Rejected** |
| **Public Postgres / DB hostname on the tunnel** | Convenient client strings | Turns ops DB into a public endpoint; DEC-010 forbid | **Rejected** |
| **SSH :22 open to the world** | “Just ssh” | Brute-force surface; NSG today is owner-home-IP only | **Rejected** |

## Rejected / parked (explicit)

- **Human PC as permanent jump host** — owner must be able to shut the PC; agents continue.
- **Owner personal SSH private key to agents** — use **`mal-cursor`** only.
- **Public Postgres** — including “just for agents” and “tunnel as a public DB URL.”
- **World-open SSH :22** — do not widen NSG SSH beyond the existing owner-home-IP break-glass.
- **Cursor My Machines on-box as phase-0 default** — **park.** Reopen only with evidence the Always Free box can spare aarch64 worker resources without starving Postgres/JSONL ingest.

## Next steps (owner, then agents)

1. **Owner implements** chosen path on `mal-core-0` + Cloudflare (tunnel, Access for SSH, `mal-cursor` public key on host). Docs here stay **pending** until that happens.
2. **Owner confirms** to managers (not via git secrets): SSH hostname ready **and** `mal-cursor` private key stored as a **Cursor secret**.
3. **Then** agents may use SSH-over-Access with that secret. Still **no** Postgres public bind; still **no** trading/X keys on host.
4. **Then** bootstrap `/var/lib/mal` app dirs, `meme_core` schema, sealed JSONL ingest, paper, monitoring (existing DEC-010 next work).
5. If chosen path is blocked, **backup** is Tailscale **on the Oracle VM** + ephemeral agent keys — still owner-implemented; still not PC-as-hop.

## Out of scope (this DEC)

- Claiming tunnel/Access/hostname/`mal-cursor` access is already live
- Agent-autonomous install of `cloudflared` / Tailscale / My Machines
- Live execution, wallet/trading keys, X tokens, capital
- PAYG / paid shapes / 4 OCPU / 24 GB
- Migrating sealed JSONL / EXP provenance into Postgres as SoT
- Unparking My Machines without a new DEC

## Review trigger

- Owner completes (or rejects) implement and confirms hostname + Cursor secret — then Lab memory may say **access ready** (still no secrets in git).
- Need to **fail over to Tailscale backup** or **unpark My Machines** — new DEC or an explicit amend, not a silent swap.
- Proposal to publish Postgres (or extra app ports) through the tunnel.
- `cloudflared` (or backup overlay) measured as a resource problem on **2 OCPU / 12 GB**.

## Overturn path

New DEC. Default until then: **chosen design is CF Tunnel + Access + `mal-cursor` key**; **status is decided / pending owner implement**; agents have **no** SSH; Postgres localhost; JSONL provenance spine; no PC-as-permanent-hop; no owner personal key to agents.
