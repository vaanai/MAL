# Oracle forward-paper runner lag, 2026-10-01

Copied from files outside git so DEC-015 can cite them.

- `/home/claude/reports/daily-review/2026-10-01.md` (05:02Z daily review): `mal-healthcheck.service` failed at 04:56:12Z. `runner-status.json` at 04:59:54Z showed `lag_ms: 5869` against `stale_cap_ms: 5000` and `fail_rate: 0.15`. `lag_breach_count: 33` over the last ≤288 `health.jsonl` lines (~24 h). Daily-review trend: 85 → 83 → 48 → 15 → 33. The review found memory not to be the cause (cgroup `memory.current` ≈ 1.51 GiB of `memory.high` 10 GiB) and left the cause undiagnosed.
- `/home/claude/reports/runner-restarts.jsonl` (pre-restart samples; one sample each): 2026-09-30 `lag_ms` 7, RSS 4,369,720 kB; 2026-10-01 `lag_ms` 2,688, RSS 4,729,048 kB.

`lag_ms` is wall clock minus the tape's receive time (`tools/forward_paper.py`), so it measures the runner's **backlog**, not network latency. HOSTS.md records the LAYA job once pushing it to about 12.5 s, so contention on Oracle is a live hypothesis.
