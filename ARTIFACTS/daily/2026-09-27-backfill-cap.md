---
cursor:
  subagentId: "bc-40e80a1a-3719-5a32-ae4d-f3808b1d03be"
---

# Backfill CPU cap — 2026-09-27

Ops only. Forward-paper was not restarted (pid 37795). LAYA and attention-daily were not started.

## Cap

Repo drop-in `scripts/mal-core/host-limits/mal-pump-backfill.service.d/10-memory.conf`, merged as `5c0ebb948332a6b638851f21a4804104a9ae9d44` ([PR #104](https://github.com/vaanai/MAL/pull/104)).

| Setting | Value |
| --- | --- |
| `CPUQuota` | **50%** (half of one core) |
| Host `cpu.max` | `50000 100000` |
| `CPUQuotaPerSecUSec` | `500ms` |
| `IOSchedulingClass` | idle (unchanged) |
| `CPUWeight` | 10 (unchanged) |

`systemd-analyze verify` accepted the unit. Worker count was not lowered. Host `limits.conf` still sets `MAL_BACKFILL_WORKERS=24`. iowait during the capped run was about 0, so the damage was not disk.

Forward-paper had to finish catching up from the uncapped run before this start. Lag was 1 ms at 07:28:29Z. Backfill started at 07:28:43Z.

## Readings

| When | `lag_ms` | iowait |
| --- | ---: | ---: |
| 07:30:44Z, about 2 min | **197** | 0.25% |
| 07:38:45Z, about 10 min | **39** | 0.00% |

Both under 5000 ms. `stale_dropped` stayed 78667. Backfill was throttled (`cpu.max` held). It is still **active**.
