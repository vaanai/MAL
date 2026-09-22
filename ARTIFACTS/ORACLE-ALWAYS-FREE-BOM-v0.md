# Oracle Always Free BOM v0 — `mal-core-0`

| | |
| --- | --- |
| **As-of** | 2026-09-22 |
| **Status** | **Pending Vaan provision — nothing created.** Docs + DEC only. |
| **Target** | **$0 / mo** Always Free (home region). Not a paid VPS. |
| **Decision** | [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md) |
| **Limits source** | [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) (verify in Console **Limits, Quotas and Usage** before create) |

**Critical sizing (do not invent 4 / 24):** Always Free Ampere A1 for **free tenancies** = **2 OCPU + 12 GB RAM total** (pool: **1,500 OCPU-hours + 9,000 GB-hours / month**). Paid tenancies advertise a larger Ampere *hour* pool (3,000 / 18,000 ≡ 4 / 24) — **MAL does not claim or use that** unless a future spend DEC says the tenancy is paid **and** Vaan accepts it. **This BOM is 2 OCPU / 12 GB.**

Council **soft-OK** (Scout / Graph / Proof): JSONL stays EXP spine day-1; aarch64 gaps escalate **after** create (not a veto); no trading/X keys on host; bonk/mayhem parked. Soft flags, not pre-create blockers.

---

## Create now (target $0 Always Free)

Nothing below exists until Vaan provisions. Names are the intended inventory.

### 1. Optional compartment

- Name: `mal-phase0` (optional; tenancy root is acceptable if Vaan prefers fewer IAM objects)

### 2. Network

- VCN `mal-vcn` — `10.0.0.0/16`
- Public subnet `mal-public` — `10.0.1.0/24`
- **Internet Gateway** (no NAT Gateway — NAT **can charge**; not on the Always Free resource list)

Free-tier tenancies: up to **2 VCNs**. One is enough.

### 3. NSG `mal-nsg`

Bind the instance VNIC to this NSG. Mirror in **ufw** on the box.

| Direction | Rule | Notes |
| --- | --- | --- |
| Ingress | TCP **22** from **Vaan SSH CIDR only** | Placeholder `VAAN_SSH_CIDR` — replace at provision; never commit the real CIDR if it is sensitive. |
| Ingress | **No public 443 day-1** | Prefer **SSH tunnel** / Cloudflare Tunnel later. Free UI is not a public website on day-1. |
| Egress | Allow **443** (HTTPS / WSS) and **53** (DNS) | PumpPortal WS, free RPC, GitHub, OS updates. |
| Deny / do not open | Public **5432**, **6379**, **3000**, **8080**, metrics | Postgres, Redis (if any), dev UIs, Prometheus — localhost / tunnel only. |

No extra public listeners “for convenience.”

### 4. ONE compute — `mal-core-0`

| Field | Value |
| --- | --- |
| Shape | **`VM.Standard.A1.Flex`** |
| OCPU | **2** (the **entire** Always Free A1 pool) |
| RAM | **12 GB** (the **entire** Always Free A1 memory pool) |
| Arch | **aarch64 / ARM** |
| Image | Canonical **Ubuntu 22.04 or 24.04 Minimal aarch64**, Console **Always Free eligible** |
| Name | `mal-core-0` |
| Boot volume | **50 GB** (default Always Free-friendly; counts toward 200 GB) |
| Extra block volume | **150 GB** → mount **`/var/lib/mal`** (Postgres data + sealed JSONL + paper marks) |
| **Block total** | **50 + 150 = 200 GB** — **at the Always Free cap** (boot + data **combined**). No third volume. |
| Public IP | **Ephemeral public IPv4 OK** (instance in public subnet + IGW). **Reserved public IP only if Console still labels it Always Free** — if attaching a reserved IP would **charge**, **skip it** and keep ephemeral. Flag in provision notes. |
| Region | **Home region only** (Always Free compute + block must be home region). |

**If A1 capacity is missing in the home region: STOP.** Do **not** pick paid shapes, do **not** “just use AMD standard,” do **not** upgrade the account to get capacity without a spend DEC. **Escalate to Helm / Vaan.** Retry later or another AD in the **same** home region.

**Degraded emergency-only note:** Always Free also includes **2× `VM.Standard.E2.1.Micro`** (AMD, **1/8 OCPU + 1 GB** each). **Not viable** as the primary continuous **WS + DB** host. Do not split the observe spine onto micros as a capacity workaround.

Also unused in phase 0 (available, **defer**): **2× Always Free Autonomous AI Database** slots (1 OCPU / 20 GB each). **Do not create.** Use **on-box Postgres 16**.

### 5. Database

- **PostgreSQL 16 on the VM** (package or aarch64 container), data dir under `/var/lib/mal`.
- **Not** Autonomous DB, **not** managed HeatWave, **not** Redis-as-SoT.
- **Role (council S1):** Layer-2 **cache / continuous ops aid** (entity graph working set, ops indexes). **Sealed JSONL remains the EXP / knowable-at-T spine day-1.** Do **not** force-migrate observe marks or EXP provenance into Postgres on day-1.
- Listen **localhost** (and/or private VCN IP). NSG/ufw **deny public 5432**.

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

## OS baseline (document only — apply at provision)

Not executed in this PR.

- `docker` + **docker compose plugin**
- `fail2ban`
- `unattended-upgrades`
- `ufw` **mirroring NSG** (22 from `VAAN_SSH_CIDR` only; no public 5432/6379/3000/8080/metrics; egress 443/53)
- `chrony` (clocks for `t_ws` / paper marks)
- SSH: **Vaan pubkey only**; **password auth off**; no other keys
- Containers **must be multi-arch / aarch64**. **S2:** Solana client / replay / LAYA binary gaps = **post-create escalate to Helm**, not a reason to skip create or switch to x86 paid shapes.

**Idle reclamation (operator note):** Always Free instances may be reclaimed if, over 7 days, CPU p95 **and** network **and** (A1) memory utilization are all **&lt; 20%**. Continuous WS + Postgres is the intended keep-alive; a silent idle box is a foot-gun. Source: same Always Free doc, “Idle Compute Instances.”

---

## Secrets — never in git / never world-readable

| Secret | Phase-0 rule |
| --- | --- |
| RPC keys / URLs with keys | systemd creds or root-only files under `/etc/mal/` (not in repo, not in JSONL) |
| Future X / Twitter tokens | **Do not place on this host in phase 0** (council S3) |
| Chat / LLM tokens | Off box or root-only; not needed for observe spine |
| OCI API keys | Vaan laptop / operator; not world-readable on `mal-core-0` |
| DB password | systemd credentials or root-only |
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

## Cutover intent (after provision)

1. Vaan creates inventory **exactly** as this BOM (or records a STOP).
2. OS baseline + Postgres on `/var/lib/mal` + docker aarch64.
3. Observe client writes **sealed JSONL** on the host (EXP spine). Optional Postgres **cache** for Layer-2 / ops — **no** day-1 provenance migration.
4. Laptop becomes operator + **data courier** (pull JSONL/marks for local EXP CLIs / GitHub PRs).
5. Paper path unchanged: detect → decode → evaluate → runners. **No live keys.** Bonk/mayhem **parked**.

---

## Primary sources

- Always Free resource list (A1 **2 OCPU / 12 GB**, **200 GB** block, **20 GB** object, 2× E2.1.Micro, 2× Autonomous, 10 TB egress, idle reclaim): https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- Paid-tenancy Ampere hour pool (do **not** treat as this BOM): https://www.oracle.com/cloud/price-list/
- Lab: [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md), [DEC-002](../DEC/DEC-002-memory-first-no-db-local.md) (amended), [DEC-008](../DEC/DEC-008-stack-phase-gates.md)
