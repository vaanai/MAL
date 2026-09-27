---
cursor:
  subagentId: "bc-00ca065d-448c-5171-b62f-f3838cc74324"
---

# MAL Oracle host health — `mal-core-vnic`

**Checked:** 2026-09-25T06:35Z (UTC)  
**Method:** DEC-011 path via `scripts/mal-core/agent-ssh.sh` (cloudflared 2026.9.1 Access TCP → SSH). Deploy-key fingerprint verified. **Read-only** — no config or service changes.  
**Paper-only:** confirmed (`healthcheck` reports `paper_only: true`).

## SSH smoke

| Check | Expected | Actual |
| --- | --- | --- |
| `hostname` | `mal-core-vnic` | `mal-core-vnic` |
| `whoami` | `ubuntu` | `ubuntu` |
| `uname -m` | `aarch64` | `aarch64` |

## `/var/lib/mal/eng/healthcheck.sh`

**Status:** `ok`

| Check | Result |
| --- | --- |
| Data volume `/var/lib/mal` | 1% used (~384M / 147G on `/dev/sdb`) |
| Postgres `127.0.0.1:5432` | `accepting` |
| Peer `psql -d meme_core` | `ok` |
| Sealed JSONL dir writable | yes (`/var/lib/mal/sealed/jsonl`) |

Latest record also written to `/var/lib/mal/logs/health-latest.json` and `health.jsonl`.

## Systemd services and timers

### System (running, MAL-relevant)

| Unit | State | Notes |
| --- | --- | --- |
| `cloudflared.service` | **active (running)** | Outbound CF Tunnel on host |
| `postgresql@16-main.service` | **active (running)** | Listens **127.0.0.1:5432** only |

No `mal*.service` units loaded at system level (`systemctl list-units 'mal*'` → 0).

### User (`ubuntu`, linger enabled)

| Unit | State | Notes |
| --- | --- | --- |
| `mal-observe.service` | **active (running)** since 2026-09-23 07:57:53 UTC (~1d 22h) | PID 12088: `python -m observe` → `/var/lib/mal/sealed/jsonl` |
| User timers | none MAL-specific | Only `launchpadlib-cache-clean.timer` |

### LAYA

**Not installed / not running.** No `laya.service`, `mal-laya.service`, or LAYA packages; BOOTSTRAP describes LAYA as pipeline north star, not a deployed host daemon.

## Ingest / data flow

**Active:** PumpPortal WebSocket observe ingest (`subscribeNewToken` + `subscribeMigration`), sealed daily `observe-YYYY-MM-DD.jsonl` under `/var/lib/mal/sealed/jsonl`. All sampled records: `type=ingest_hot`.

| Day (UTC) | Lines | Streams (approx) | `t_ws` span | Approx rate |
| --- | ---: | --- | --- | ---: |
| 2026-09-25 (partial) | 7,691 | 7,394 new token / 297 migration | 00:00:10 → 06:35:09 | ~1,168 events/h |
| 2026-09-24 (full) | 34,188 | 32,993 / 1,195 | full UTC day | ~1,425 events/h |
| 2026-09-23 (from service start) | 25,115 | 24,296 / 819 | 07:57:56 → 23:59:58 | ~1,566 events/h |

**Last ingest activity (from `observe.log`):** sealed writes through **2026-09-25 06:35:09 UTC**; file mtime matches live ingest.

**Paper marks JSONL:** `marks-2026-09-20.jsonl` and `marks-2026-09-21.jsonl` only (last modified **2026-09-23 15:57**). No marks files for 2026-09-23+ — marks pipeline not updating on host (observe-only ingest since bootstrap).

## Postgres (`meme_core`, peer auth)

| Table | `n_live_tup` |
| --- | ---: |
| `schema_migrations` | 1 |
| `ops_meta` | 1 |
| `tokens`, `wallets`, `relationships`, `paper_positions` | 0 |

Schema present; no entity rows populated yet. Password not used or probed.

## Resource headroom

| Resource | Value |
| --- | --- |
| CPU | 2 cores; load **0.00**; ~100% idle at sample |
| RAM | 11 Gi total; **~608 Mi used**; **~11 Gi available** (incl. cache) |
| Swap | 2 Gi, **0 used** |
| Root `/` | 10% used (4.7G / 48G) |
| Data `/var/lib/mal` | **1%** used |

Uptime: **~2 days 6h** at check time.

## Network / access fences (observed)

- Postgres: **localhost:5432** only (good).
- SSH `:22` listening on `0.0.0.0` / `[::]` (owner break-glass path per DEC-011; not opened by this check).
- Agent path used CF Access TCP only; temp key and agent `cloudflared` cleaned up after sessions.

## Recent log notes (no secrets)

| Source | Severity | Summary |
| --- | --- | --- |
| `sshd` | noise | Internet scanner auth failures (e.g. 2026-09-25 ~01:38 UTC); expected on public :22 |
| `mal.observe` / `observe.log` | low | Occasional `skip_unclassified`; WS close **1006** with brief **502** handshake on reconnect (2026-09-24 ~23:48, 2026-09-25 ~04:35) then auto-reconnect — ingest continued |
| `cloudflared` | info/warn | Tunnel reconnects on 2026-09-23; **2026-09-25** log suggests binary **2026.9.1** newer than **2026.9.3** available (informational; not changed) |
| `postgresql@16-main` | historical | Start refusal PID-file warning **2026-09-23 01:48**; cluster **currently healthy** |

`journalctl --user -u mal-observe.service` shows only the initial start line — runtime logging goes to `/var/lib/mal/logs/observe.log` (~13 MB).

## Overall

| Area | Verdict |
| --- | --- |
| Access / SSH | **OK** |
| Core infra (tunnel, Postgres, disk, RAM) | **OK** |
| Sealed observe ingest | **OK** (flowing) |
| LAYA runtime | **Not deployed** |
| Postgres entity state | **Empty** (bootstrap schema only) |
| Paper marks on host | **Stale** (last update 2026-09-23) |

**Blockers:** none for read-only health. Marks/entity backfill and LAYA deployment remain future operator work per LAB bootstrap scope.
