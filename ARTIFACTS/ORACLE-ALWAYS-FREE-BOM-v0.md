# Oracle Always Free BOM v0 — `mal-core-0`

| | |
| --- | --- |
| **As-of** | 2026-09-23 |
| **Status** | **Provisioned and verified by Vaan.** Live inventory in [ORACLE-PHASE0-HANDOFF.md](ORACLE-PHASE0-HANDOFF.md). This BOM keeps the Always Free **envelope**, charge foot-guns, and **as-built** names/paths. Pre-create “nothing created” language is **historical**. |
| **Target** | **$0 / mo** Always Free (home region). Not a paid VPS. |
| **Decision** | [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md) (envelope); [DEC-010](../DEC/DEC-010-oracle-phase0-handoff-autonomy.md) (provisioned + autonomy); [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) (access design, pending implement) |
| **Limits source** | [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) |

**Critical sizing (do not invent 4 / 24):** Always Free Ampere A1 for **free tenancies** = **2 OCPU + 12 GB RAM total** (pool: **1,500 OCPU-hours + 9,000 GB-hours / month**). Paid tenancies advertise a larger Ampere *hour* pool (3,000 / 18,000 ≡ 4 / 24) — **MAL does not claim or use that** unless a future spend DEC says the tenancy is paid **and** Vaan accepts it. **This BOM is 2 OCPU / 12 GB.**

Council **soft-OK** (Scout / Graph / Proof): JSONL stays EXP/provenance spine; aarch64 gaps escalate to Helm (not a veto); no trading/X keys on host; bonk/mayhem parked.

---

## As-built inventory (provisioned)

Names/paths below are **verified**. Do not “re-create” this BOM. Do not invent **4 OCPU / 24 GB**.

### 1. Optional compartment

- Name: `mal-phase0` (optional; tenancy root is acceptable). Not required to match a specific as-built name in Lab memory.

### 2. Network (as-built)

- VCN **`mal-vcn`** — `10.0.0.0/16`
- Public subnet **`mal-public`** — `10.0.1.0/24`
- Internet Gateway **`mal-igw`** (no NAT Gateway — NAT **can charge**)

Free-tier tenancies: up to **2 VCNs**. One is enough.

### 3. NSG **`mal-core-nsg`** (as-built; pre-create placeholder was `mal-nsg`)

Bind the instance VNIC to this NSG. Mirror in **ufw** on the box.

| Direction | Rule | Notes |
| --- | --- | --- |
| Ingress | TCP **22** from **owner home public IP only** | Never commit the real CIDR. Placeholder in git: `VAAN_SSH_CIDR`. |
| Ingress | **No public app ports** (no public 443) | Cursor access = [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) (CF Tunnel + Access, **pending owner implement**). **Do not** open Postgres or extra listeners. |
| Egress | Allow **443** (HTTPS / WSS) and **53** (DNS) | PumpPortal WS, free RPC, GitHub, OS updates. |
| Deny / do not open | Public **5432**, **6379**, **3000**, **8080**, metrics | Postgres, Redis (if any), dev UIs, Prometheus — localhost / tunnel only. |

No extra public listeners “for convenience.” **Do not expose Postgres publicly.**

### 4. ONE compute — `mal-core-0` (as-built)

| Field | Value |
| --- | --- |
| Shape | **`VM.Standard.A1.Flex`** |
| OCPU | **2** (the **entire** Always Free A1 pool) |
| RAM | **12 GB** (the **entire** Always Free A1 memory pool) |
| Arch | **aarch64 / ARM** |
| Image | Canonical **Ubuntu 24.04 Minimal** aarch64; **2 GB swap** |
| Name | `mal-core-0` |
| Region / AD | **Phoenix**, **AD-1** (home region) |
| Boot volume | **50 GB** |
| Data volume | **`mal-core-data`**, **150 GB**, mounted **`/var/lib/mal`** |
| **Block total** | **50 + 150 = 200 GB** — **at the Always Free cap**. No third volume. |
| Public IP | Ephemeral public IPv4 as provisioned. **Reserved public IP only if Console still labels it Always Free.** |
| Agent SSH | **Not granted.** Design locked in [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) (**decided, pending owner implement**). Do **not** claim tunnel exists. |

**A1 capacity was available; instance exists.** If a **future** resize/recreate hits an A1 capacity miss: **STOP.** Do **not** pick paid shapes, do **not** “just use AMD standard,” do **not** upgrade the account without a spend DEC. **Escalate to Helm / Vaan.**

**Degraded emergency-only note:** Always Free also includes **2× `VM.Standard.E2.1.Micro`** (AMD, **1/8 OCPU + 1 GB** each). **Not viable** as the primary continuous **WS + DB** host. Do not split the observe spine onto micros as a capacity workaround.

Also unused in phase 0 (available, **defer**): **2× Always Free Autonomous AI Database** slots (1 OCPU / 20 GB each). **Do not create.** Use **on-box Postgres 16**.

### 5. Database (as-built)

- **PostgreSQL 16.15** on the VM. Data dir **`/var/lib/mal/postgresql/16/main`**.
- Database **`meme_core`**. Role **`mal_app`** (non-superuser; cannot create DBs/roles). Password = **owner only** — never in git/docs/chat.
- **Not** Autonomous DB, **not** managed HeatWave, **not** Redis-as-SoT.
- **Role (DEC-010):** **operational / state** layer (token/wallet/relationship/derived/paper/ops). **Sealed JSONL = provenance/event spine.** Do **not** treat Postgres as a mandatory provenance replacement. Do **not** force-migrate observe marks or EXP provenance into Postgres as SoT.
- Bound **localhost only**. NSG/ufw **deny public 5432**. Connection owner-tested.

### 6. Optional Object Storage

- Bucket `mal-archives` — **cold backups only**
- Always Free Object Storage ≈ **20 GB** total on Always Free-only accounts (plus 50k API requests/month). Stay ≤ 20 GB. Not a live database; not a substitute for JSONL or `/var/lib/mal`.

### 7. Do not create

| Resource | Why |
| --- | --- |
| **NAT Gateway** | **Can charge**; not Always Free. Public subnet + IGW covers egress. |
| **Load Balancer** | Skip unless Console confirms the **Always Free 10 Mbps** Flexible LB **and** Vaan still wants it. Day-1 has **no public 443**. |
| **OKE** | Paid control plane / worker risk; out of phase 0. |
| **GPU** | Not Always Free; LAYA is CPU/paper until measured. |
| **Second fat VM** | A1 pool is **2 OCPU / 12 GB total** — a second A1 would exceed Always Free. |
| **Paid RPC** | Still DEC-008 Gate 1; PumpPortal free WS + public/free RPC. |
| **Autonomous DB** | Defer; on-box Postgres. |
| **Reserved public IP** | Only if still Always Free in Console; else ephemeral. |

---

## OS baseline (as-built + remaining)

Owner-verified on host:

- Ubuntu **24.04 Minimal**; **unattended security updates** enabled
- SSH: public-key **on**; password auth **off**; keyboard-interactive **off**; root password login **off**
- OCI NSG **`mal-core-nsg`** as primary perimeter; Postgres **localhost**
- **No** wallet/trading keys; **no** trading capital for agents; paper-only

Still for bootstrap (not claimed done by this docs PR):

- `docker` + **docker compose plugin** (if used)
- `fail2ban`, `ufw` mirroring NSG, `chrony` (clocks for `t_ws` / paper marks)
- Containers **must be multi-arch / aarch64**. **S2:** Solana client / replay / LAYA binary gaps = escalate to Helm, not a reason to switch to x86 paid shapes.

**Idle reclamation:** Always Free instances may be reclaimed if, over 7 days, CPU p95 **and** network **and** (A1) memory utilization are all **&lt; 20%**. Prefer **legitimate** continuous workload (ingest / monitor / paper) — **never fake keep-alive**. Source: same Always Free doc, “Idle Compute Instances.”

---

## Secrets — never in git / never world-readable

| Secret | Phase-0 rule |
| --- | --- |
| RPC keys / URLs with keys | systemd creds or root-only files under `/etc/mal/` (not in repo, not in JSONL) |
| Future X / Twitter tokens | **Do not place on this host in phase 0** (council S3) |
| Chat / LLM tokens | Off box or root-only; not needed for observe spine |
| OCI API keys | Vaan laptop / operator; not world-readable on `mal-core-0` |
| DB password (`mal_app`) | **Owner only.** systemd credentials or root-only on host when app bootstrap happens. **Never** in repo, docs, or chat. |
| **Wallet / trading private keys** | **NEVER on this host in phase 0** — paper only. Isolated from agents. |

Placeholder only in git: `VAAN_SSH_CIDR`. No real CIDRs, keys, or OCIDs in this repo.

---

## Charge foot-guns

Stay inside Always Free or **STOP** and ask Helm/Vaan.

| Foot-gun | Why it bills / kills free |
| --- | --- |
| A1 **&gt; 2 OCPU** or **&gt; 12 GB** | Exceeds Always Free tenancy Ampere pool (do **not** use 4 / 24) |
| Block volume **&gt; 200 GB** combined (boot + data) | Always Free block cap |
| **NAT Gateway** | Can charge; not required with public subnet + IGW |
| Non-free **Load Balancer** | Always Free LB is a specific 10 Mbps Flexible shape — confirm or skip |
| **Autonomous DB** “because it is free” as the live spine | Unused slot is fine to leave idle; creating it does not replace JSONL law and burns the free DB quota |
| **Paid shapes** after A1 capacity miss | Violates STOP rule |
| **Reserved public IPv4** if Console would charge | Use ephemeral |
| **Egress overage** | Always Free outbound transfer is **10 TB/month** — unlikely early; still do not turn the box into a public file host |
| Home-region violation | Always Free compute/block **home region only** |

---

## Next (host already exists)

1. **Owner implement:** Cursor↔Oracle access per [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) (CF Tunnel + Access + `mal-cursor` Cursor secret). **Pending** — do not claim live. **No** public Postgres; **no** owner personal SSH key to agents; **no** PC-as-permanent-hop.
2. Bootstrap `/var/lib/mal` app dirs, sealed JSONL on the host, `meme_core` schema, ingest, paper marks, logging/monitoring/backups — **after** access exists.
3. Laptop = operator + **data courier** (local EXP CLIs / GitHub PRs). Not the 24/7 host.
4. Paper path unchanged: detect → decode → evaluate → runners. **No live keys.** Bonk/mayhem **parked**.
5. Open research (no pick): trading/wallet execution surface (Axiom / Phantom / etc.). Optional real paper-trading utility — **ask Vaan first**.

---

## Primary sources

- Always Free resource list (A1 **2 OCPU / 12 GB**, **200 GB** block, **20 GB** object, 2× E2.1.Micro, 2× Autonomous, 10 TB egress, idle reclaim): https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- Paid-tenancy Ampere hour pool (do **not** treat as this BOM): https://www.oracle.com/cloud/price-list/
- Lab: [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md), [DEC-010](../DEC/DEC-010-oracle-phase0-handoff-autonomy.md), [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md), [DEC-002](../DEC/DEC-002-memory-first-no-db-local.md) (amended), [DEC-008](../DEC/DEC-008-stack-phase-gates.md), [ORACLE-PHASE0-HANDOFF.md](ORACLE-PHASE0-HANDOFF.md)
