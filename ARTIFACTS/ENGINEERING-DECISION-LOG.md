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

## EDL-004 — Hot-packet v0 Proposed paper contract

| Field | Value |
| --- | --- |
| **ID** | EDL-004 |
| **Date** | 2026-09-23 |
| **What changed** | Registered **Proposed** `hot_packet_v0`: [HOT-PACKET-V0.md](HOT-PACKET-V0.md), [hot-packet-v0.schema.json](hot-packet-v0.schema.json), synthetic fixtures, `python -m tools.hot_packet_v0` (example / validate). |
| **Why** | LAYA design needs a citeable capped decode packet (L1 spine, regime locks, graph slots) without continuous observe-wiring. |
| **What was tested** | `python3 -m unittest tools.test_hot_packet_v0`. Fixture CLI validate. No RPC. No host JSONL. |
| **Verification** | Unit tests pass. Examples match fixtures. Proposed tags rejected on sealed overlay. Burst slot and non-null H-G4 rejected. |
| **Current state** | Contract is **Proposed**. `global_95bps` and `launchlab_init` stay Proposed. Graph default is cold. Sealed book RPC slice stays incomplete. DEC-005 remains draft PR #8. |
| **Rollback** | Revert this registration. Sealed `ingest_hot` rows are untouched. |
| **Unresolved** | Encoder / observe-wiring not authorized. Enum production lock not authorized. DEC-005 not merged. No Discovery promote. |
| **Implications** | Decode designers cite this packet. Do not treat merge as wiring, encoder promote, or a scored measure. |
| **Pointers** | [HOT-PACKET-V0.md](HOT-PACKET-V0.md), [EXP-007e](../EXP/EXP-007e-instr-quote-residual-v0.md), [DEC-006](../DEC/DEC-006-detect-decode-evaluate-runners.md) |

---

## EDL-003 — DEC-011 access LIVE + mal-core-0 paper bootstrap

| Field | Value |
| --- | --- |
| **ID** | EDL-003 |
| **Date** | 2026-09-23 |
| **What changed** | DEC-011 path **LIVE** for Cursor Cloud agents: CF Access Service Auth + Runtime Secrets; smoke `mal-core-vnic` / aarch64 / paper-only. Host bootstrap: `/var/lib/mal` subdirs, `meme_core` ops/state stub schema, sealed JSONL ingest path (user systemd `mal-observe` + run script), healthcheck, on-host `eng/BOOTSTRAP.md`. Lab memory no longer claims agents lack SSH. |
| **Why** | Overnight greenlight: owner implemented Access plumbing; agents must bootstrap the blank workshop without the owner PC. |
| **What was tested** | Agent hop: cloudflared 2026.9.1 Access TCP → SSH ubuntu. Host: layout, `sudo -u postgres` schema apply (or `BLOCKED:needs_db_password`), healthcheck, observe unit. Key fingerprint matched. Temp key/`cloudflared` cleaned after session. |
| **Verification** | See PR + `/var/lib/mal/eng/BOOTSTRAP.md` + `/var/lib/mal/logs/health-latest.json`. EXP-002c facts **unchanged** (no promotion, no optimize-to-gate). |
| **Current state** | Agents **can** SSH via Access. Postgres localhost-only. JSONL = provenance spine. Paper-only. No trading/X keys on host. |
| **Rollback** | Docs: revert this PR. Host dirs/unit/schema: stop `mal-observe`, leave PG data; do **not** open `:22` or public Postgres. Do not delete Always Free resources. |
| **Unresolved** | Helm morning review. Optional real paper-trading utility (ask first). Trading/wallet surface — **no pick**. Backups not scheduled yet. |
| **Implications** | Human PC is not a hop. Runtime Secret **names** may appear in runbooks; **values** never in git. Access TCP hostname is gated, not public SSH. |
| **Pointers** | [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md), [ORACLE-HOST-BOOTSTRAP.md](ORACLE-HOST-BOOTSTRAP.md), [oracle_ssh_smoke.md](../tools/oracle_ssh_smoke.md), [sql/meme_core/](../sql/meme_core/) |

---

## EDL-002 — Cursor↔Oracle access decided (DEC-011)

| Field | Value |
| --- | --- |
| **ID** | EDL-002 |
| **Date** | 2026-09-22 (decision); recorded 2026-09-23 |
| **What changed** | DEC-010 open access ask → **DEC-011 lock.** Chosen: Cloudflare Tunnel (`cloudflared`) on `mal-core-0` + Cloudflare Access gating SSH + dedicated `mal-cursor` ed25519 deploy key (not owner personal). Backup: Tailscale on Oracle VM + ephemeral agent auth keys. Rejected: PC jump host, owner-key share, public Postgres, `:22` to the world. My Machines on-box **parked** as phase-0 default. |
| **Why** | Helm recommendation + Scout/Graph/Proof soft-OK + owner accepted. Need agent path to the live box without a human PC hop or a public DB. |
| **What was tested** | Docs-only / N/A. No tunnel, Access app, or `mal-cursor` key claimed in this PR. |
| **Verification** | Decision recorded. Owner will implement. Agents still have **no** SSH. |
| **Current state** | **Superseded by EDL-003** (path LIVE 2026-09-23). This entry is the decision-time snapshot. |
| **Rollback** | Docs: revert this PR / new DEC. Infra: do **not** tear down Always Free resources. Do not open `:22` or public Postgres as a workaround. |
| **Unresolved** | Owner implementation of tunnel/Access/`mal-cursor`. Optional real paper-trading utility (ask first). Trading/wallet execution surface (Axiom / Phantom / etc.) — **no pick**. |
| **Implications** | Postgres remains localhost-only (never a public/tunneled DB). JSONL stays provenance spine. Human PC is not a hop. No owner personal key to agents. |
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
| **Current state** | Secure blank Oracle workshop. Access architecture **locked in DEC-011** (owner implementing; tunnel not live). Then bootstrap dirs/schema/ingest. EXP-002c facts **unchanged**. |
| **Rollback** | Docs: revert that PR. Infra: do **not** destroy Always Free resources without Vaan. Do not revert to “laptop is 24/7 host.” |
| **Unresolved** | Owner implementation of DEC-011. Optional real paper-trading utility (ask first). Trading/wallet execution surface research (Axiom / Phantom / etc.) — **no pick**. |
| **Implications** | JSONL stays provenance spine; Postgres is ops/state only. Human PC must not be a permanent networking hop. DM Vaan a Cursor status card on every Cursor launch. |
| **Pointers** | [DEC-011](../DEC/DEC-011-cursor-oracle-access-cf-tunnel.md), [DEC-010](../DEC/DEC-010-oracle-phase0-handoff-autonomy.md), [DEC-009](../DEC/DEC-009-oracle-always-free-phase0-host.md), [ORACLE-PHASE0-HANDOFF.md](ORACLE-PHASE0-HANDOFF.md), [ORACLE-ALWAYS-FREE-BOM-v0.md](ORACLE-ALWAYS-FREE-BOM-v0.md) |
