---
cursor:
  subagentId: "bc-40e80a1a-3719-5a32-ae4d-f3808b1d03be"
---

# Forward-paper runner lag — 2026-09-27

Paper only. Backfill was left stopped. `mal-laya-v0` was not started. Attention-daily was not started. No Cloudflare, port 22, or trading-key changes. The runner unit was not restarted.

## Bottleneck

One thread in the exit replan. Not disk, not the 2 MiB read chunk, not backfill CPU.

py-spy 0.4.2, pid 33168, 8s at 50 Hz (399 samples, 0 errors), backfill already stopped:

| Frame | Share |
| --- | ---: |
| `drain_until` → `_try_exit` → `simulate_exit` → `_plan_exit` | 96.3% |
| `_spot_with_our_buy` | 85.1% |
| `json.loads` / `poll` | 0.7% |

`_exits_through` runs on every tape timestamp for every open book. `_plan_exit` walks that mint's prints from the entry every time. Open mints are excluded from the 180s prune, so a `tp50_sl30` position keeps the whole flow. Holds (`attn_first_hold_60m`) do not walk prints. The live open set at 06:45Z was one `migrate_tp50_sl30` (ceiling and shadow) plus a few attention holds.

Host at 06:41Z: 4 cores, load 1.16, about 72% idle, iowait 0, `sdb` util 0.3%. Runner RSS ~560 MiB, 1 thread, `cpu_cores` 1.000 over 3s and over 5s, all user time. systemd `CPUWeight=1000` and `Nice=0` are set; the process niceness is still 19 because `forward-paper.sh` execs `nice -n 19`. That did not matter: the thread was already scheduled for a full core, with three cores idle. A backfill `CPUQuota` cannot speed this up.

Tape growth on the open hour was ~83 KiB/s. `read_bytes` did not move during a 5s sample (page cache). `stale_dropped` rose 437229 → 532433 from 06:41 to 06:51, so rows older than 5s are still discarded. The runner never gets back to the live edge because the exit walk is slower than the tape.

## Lag, backfill stopped

`runner-status.json`. Backfill `inactive` the whole window. No tight poll: one read, then work, then one read ten minutes later.

| UTC | `lag_ms` | `stale_dropped` |
| --- | ---: | ---: |
| 06:41:24 | 11449 | 437229 |
| 06:43:34 | 15982 | 458701 |
| 06:45:28 | 16347 | 474964 |
| 06:51:39 | **20055** | 532433 |

06:41:24 → 06:51:39 is +8606 ms. Lag is not clearing with backfill stopped.

Backfill was not restarted. The last figure with it running is the prior note: 15371 ms at ~06:38Z, then 13528 ms sixty seconds after the stop. This session has no new "backfill running" sample. Starting it again would not remove the one-core exit walk.

## Change (not deployed)

Draft PR: https://github.com/vaanai/MAL/pull/103

Branch `cursor/forward-paper-exit-scan-03be` at `6674e4e`.

`_plan_exit_resume` returns the same `(trigger, t_fill)` as `_plan_exit`. The same print list is a cache hit. An in-order suffix continues the walk. A mid-list insert or a same-signature collapse starts over. The runner skips `simulate_exit` / `simulate_ladder` while that plan is still in the future. The close still calls those functions.

Local, not the host: 200 full replans of a 2000-print path took 0.30s. Cached checks of that same path took under 1ms.

Tests: `ExitScanResumeTests`, `SameSignatureFillTests`, `tools.test_forward_paper` passed. `FillAndExitTests.test_unchanged_book_round_trip_loses_fees_and_stays_realized` still errors on `fee_stack_lamports`; `label_row` on `main` does not copy that field (`git show HEAD` row has no such key).

## What could go wrong

A suffix treated as in-order when a print belongs earlier would delay an exit until the next full walk. Inserts and same-signature updates force that walk. Closes still go through `simulate_exit`, so a resume miss delays a close rather than inventing one. The single closing walk can still stall once on a very long open mint.

## Deploy after merge

PR #103 merged as `264d1b91d5f2c4fed25120517a2b5b67c56cfcbb` at 2026-09-27T06:56:23Z.

Host grep of the old `src/tools/forward_paper.py` for `TxOrder` returned 0. The #97 split-signature ordering fix was not on the box. The old tree is kept at `/var/lib/mal/paper/forward-paper/src-pre-103`. The live `src` is a depth-1 checkout of that merge. The existing LAYA venv imported it. No new venv. Only `mal-forward-paper` was restarted. `positions.jsonl` was not deleted.

`guard-live.json` was moved aside (`guard-live.json.before-264d1b9`, previous live time 2026-09-27T01:25:24Z). The clean process stamped the live edge itself:

| Field | Value |
| --- | --- |
| `live_at` | 2026-09-27T06:58:12Z |
| `clean_start` | 2026-09-28T00:00:00Z |
| `invalid-for-promotion.json` `until` | 2026-09-27T06:58:12Z |

| When | `lag_ms` | RSS | cgroup memory |
| --- | ---: | ---: | ---: |
| 06:58:27Z, backfill stopped, just started | 20 | 162 MiB | 123 MiB |
| 07:08:43Z, backfill still stopped | 168 | 265 MiB | 236 MiB |
| 07:18:53Z, backfill running ~10 min | **24194** | 358 MiB | 335 MiB |

Memory max is 1 GiB. `oom_kill` stayed 0. Same pid 37795 through the window. `stale_dropped` was 0 until backfill ran, then 22986.

Backfill was stopped at 07:19:18Z and left inactive. `mal-laya-v0` and `mal-attention-daily` were not started.
