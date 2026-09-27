---
cursor:
  subagentId: "bc-3010be97-788f-5230-b249-96c7b15fe53f"
---

# mal-core-0 hardening after the 2026-09-25 OOM

Paper only. Host `mal-core-vnic` via DEC-011. Deploy-key fingerprint matched `SHA256:HH+tRTOIpyvYorf+FhZ7INifqOm2L7NTcvPLdqMPVTo`. Cloudflare Tunnel config, Access, Postgres listen address, and sshd's port were not changed. No DB password was used (`sudo -u postgres` peer).

## 1. Reconnect

Boot now running is `Sat 2026-09-26 00:13:47 UTC` (a 2-minute boot at 00:08:59 was shut down at 00:11:41). `nproc=4`. `free -m` total **23974 MB** (~23.4 GiB; `free -g` prints 23). `cloudflared` active (pid 799, ~56 MB). Postgres 16 `postgresql@16-main` accepting on `127.0.0.1:5432` only. `meme_core` `pg_is_in_recovery=f`.

## 2. What came back

No tmux, no user crontab. System timers are stock (apt, logrotate, fstrim). User units that started at 00:13:53: observe, trade tape, attention, forward paper, funding graph, plus timers `mal-laya-v0` (next 04:39Z) and `mal-attention-daily` (04:45Z). **Backfill did not auto-start** (`mal-pump-backfill.service` was disabled). LAYA was not running.

Stopped until caps were in place: forward paper, funding graph. Backfill and LAYA were already stopped. Collectors left up (they were healthy).

## 3. Data window 20:15Z Sep 25 → 00:09Z Sep 26

Journal boot `-2` last line is `2026-09-25T23:34:34Z` kernel "Under memory pressure, flushing caches." No OOM-kill line. `cloudflared` QUIC timeouts from 23:25Z. Postgres autovacuum "took too long to start" 23:29Z–00:02Z. Tape hours **T20–T22 are complete** (last event at :59). The box did not stop writing at the reported ~21:40Z CF 1033; collectors ran until ~23:26Z.

| stream | gap | notes |
| --- | --- | --- |
| trade tape | **23:26:44.311Z → 00:09:09.668Z** (42m 25s) | T23 sealed zst, 360,305 rows, ends 23:26:44. Valid zstd. |
| trade tape | **00:11:36.612Z → 00:13:54.755Z** (138s) | double reboot |
| attention | **23:25:42.603Z → 00:09:09.692Z** (43m 27s) | T20–T22 full; T23 sealed early, valid zstd |
| observe | 23:26:45.538Z → 23:37:32.199Z (647s), then **23:37:32.199Z → 00:09:14.445Z** (31m 42s) | plus 00:11:35 → 00:13:56 (141s). Last lines are valid JSON |
| graph funding | **23:25:45.012Z → 00:09:11.371Z** (43m 26s) | daily JSONL, both ends valid JSON |
| backfill | no torn lines | see below |

Five ~64s `t_recv_ms` stalls inside T20/T21/T23 (none in T22). They also occur before the crash window. Not treated as corruption.

**Half-written files.** No `*.partial` leftovers. `zstd -t` on 26 window archives: 0 bad. Backfill hours left unsealed on purpose:

- `2026-09-24T21` partial, 5775/13463 slots. Checkpoint offsets sit on newlines. Trades file is 623,000 bytes past the checkpoint; that tail is valid JSON.
- `2026-09-25T16` partial, 2700/13828 slots. Trades tail +876,569 bytes, valid JSON.

Left in place for resume. Not sealed (the hour is unfinished) and not quarantined (no torn line). `trades-2026-09-25T07.jsonl.zst` exists and is not in `checkpoint.json` `hours`. Postgres: `database system was not properly shut down; automatic recovery in progress` at 00:09, redo finished, ready. 00:11 shutdown was clean. No corruption messages.

## 4–5. Limits and swap

The table below is the first install. Section 9 supersedes it: training is MemoryHigh=10G / MemoryMax=11G, the slice is 12G / 13G, and tape, forward, and funding are 1G so the training window still leaves ≥6G.

Installed from `scripts/mal-core/install-host-limits.sh`. User drop-ins under `~/.config/systemd/user/`. Batch units are in `mal-batch.slice` **MemoryHigh=10G MemoryMax=12G**, so the sum cannot eat the ≥6G Postgres/OS reserve. Collector caps from boot measurement (tape cgroup ~244 MB, observe RSS ~27 MB, attention RSS ~35 MB).

| unit | MemoryHigh / Max | other |
| --- | --- | --- |
| mal-pump-backfill | 3G / 4G | OOMScoreAdjust=1000, slice |
| mal-forward-paper | 1.5G / 2G | 700 |
| mal-laya-v0 (timer starts this unit; graduated-swing is a child) | 7G / 8G | CPUQuota=200%, OOMScoreAdjust=400 |
| mal-funding-graph | 1.5G / 2G | 700 |
| mal-trade-tape | 1.5G / 2G | cgroup max confirmed 2G |
| mal-observe | 768M / 1G | cgroup max confirmed 1G |
| mal-attention | 768M / 1G | cgroup max confirmed 1G |

`cloudflared`, `postgresql@16-main`, and `ssh` drop-ins set `OOMScoreAdjust=-900`. Applied to the running daemon PIDs without restarting cloudflared. sshd session children forked into a user scope stay at 0; the listener is -900.

Swap: replaced the 2G `/swapfile` with **4G** (`free -m` swap 4095). `/etc/fstab` still has `/swapfile none swap sw 0 0`. `vm.swappiness=10` in `/etc/sysctl.d/99-mal-swappiness.conf`.

## 6. Peaks, ~10 min after each restart

Watch log `/var/lib/mal/logs/host-limit-watch.log` (00:39Z–01:39Z). All six stayed active. Host available memory stayed above 20 GB.

| job | peak cgroup | peak RSS | peak CPU |
| --- | ---: | ---: | ---: |
| observe | 16.9 MiB | 26.9 MiB | <1% |
| attention | 23.4 MiB | 33.1 MiB | <1% |
| trade tape | 194 MiB | 65 MiB | 24% |
| funding graph | 366 MiB | 281 MiB | 8% |
| forward paper | 177 MiB | 206 MiB | 65% |
| backfill | 2.14 GiB | 1.83 GiB | 127% |

## 7. PR #97 re-score

Code is commit `49865246` (`cursor/laya-backfill-holdout-bc91`) at `/var/lib/mal/paper/laya-pr97/src`. Timer snapshot `/var/lib/mal/paper/laya-v0/out/scoreboard.md` is still mtime 2026-09-25 16:40Z. Output: `/var/lib/mal/paper/laya-pr97/out/` (`backward_holdout.json`, `scoreboard.md`). Backfill was stopped during the run so it would not share the batch slice; it was started again after. Collectors, forward paper, and funding stayed up.

The 8G cap pinned `memory.max` (working set ~8 GiB RSS + 1.8 GiB swap, log frozen). The one-shot unit was raised to **MemoryMax=12G** (slice temporarily 14G, then restored to 12G). Peak cgroup **11.01 GiB**, RSS ~11.0 GiB, single-threaded (`OMP_NUM_THREADS=1`), ran 01:43Z–04:05Z. Daily `mal-laya-v0.service` was still 8G at the end of this re-score; section 9 raised it before the 04:19 timer fire. Hop this run is 201 ms (latency file p50), not the earlier 272 ms. Stamped backfill trades 7,983,303 (was 5,201,846). Scored hours now include 2026-09-24T22–23 and 2026-09-25T00–06, so n is not the old T01–T06 set.

**Migration book, scored hours: +114.414 SOL → −0.622 SOL** (n 238 → 360, mean −0.00173, ex top 3 −0.789). Sep 25 sealed day alone: n=281, total **−0.526 SOL**. Pressure scale 1 total −0.561. Nothing promotes.

Scored-hour deltas (old T01–T06 pool → new pool, extra sealed hours included):

| book | old total | new total | delta |
| --- | ---: | ---: | ---: |
| migrate_hold_30s | +114.414 | −0.622 | −115.036 |
| buyers_8_top5_ladder_2x | −0.235 | −0.049 | +0.186 |
| t30_top1_hold_30s | −0.034 | −0.364 | −0.331 |
| mig15_top20_tp50_sl30 | −0.162 | −0.137 | +0.025 |

Live forward holdout (frozen 15:30Z, models fit before, scored after). n grew because the post-freeze tape is longer, not only because of the fill fix. Old → new total SOL: buyers_8 −0.126 → **+0.292** (n 14 → 156); t30 −0.040 → **−0.584** (n 14 → 157); migrate −0.347 → **−1.196** (n 33 → 365). None promote. Walk-forward folds are all `hold_30s` (test n ≈ 21k). OOS medians stay near −0.003 SOL.

## 8. Repo

PR https://github.com/vaanai/MAL/pull/98 branch `cursor/host-oom-limits-e53f`. Not merged. Ops config only.

## 9. 2026-09-26 04:15 follow-up

The 04:15 fit had not started (timer was armed for 04:38). It was not OOM-killed. `daemon-reload` after 04:15 made `Persistent=true` fire the unit at 04:19:08. That run is the daily fit, under the new cap: cgroup `memory.max=11G`, `memory.high=10G`, backfill stopped in the same second. A `/bin/false` probe confirmed `ExecStopPost` starts backfill again after a failed oneshot.

Training-window caps (backfill's 4G is not held): laya 11G + forward 1G + funding 1G inside a 13G slice, plus tape 1G + observe 1G + attention 1G + attention-daily 1G. Effective total 17G. Usable RAM 23974 MiB, remainder 6.4 GiB. Tape, forward, and funding were lowered to 1G (measured peaks 194 / 177 / 366 MiB) so the 11G fit still leaves ≥6G. No dtype/chunking change; re-benchmarking the trainer was not cheap. The daily fit later finished at 05:09, success, cgroup peak **10.0 GiB** (journal), swap peak 194 MiB. The earlier re-score peak remains 11.01 GiB.

## 10. 2026-09-26 morning follow-up

PR https://github.com/vaanai/MAL/pull/99 branch `cursor/ops-batch-priority-e53f`. Not merged. #98 is on main as `993aa7d`.

**Backward holdout.** Timer snapshot `/var/lib/mal/paper/laya-v0` was still the pre-#97 tree (no `laya_backfill_holdout.py`). Deployed main `993aa7d` into `src/` and replaced `laya-v0.sh` so the 04:15 runner passes `--backfill-dir`. Re-ran only `python -m tools.laya_backfill_holdout` under MemoryHigh=10G/MemoryMax=11G with backfill stopped. Result success, peak **9.0 GiB**, 0 swap. `migrate_hold_30s` scored pool **+114.41 → −0.931 SOL** (n 238→407). Sealed 2026-09-25 alone **−0.672** (n=281). Nothing promotes. Same files copied to `laya-backfill-holdout/out/` so that path is not the old table.

**Backfill.** `ExecStopPost` did start it at 05:09:13; it was active and sealing `2026-09-24T20` by 05:13. The brief sampled the stop. The start is logged before laya is fully dead, so `Conflicts=` can cancel it. `OnSuccess=`/`OnFailure=` `mal-pump-backfill-resume.service` starts it after the unit is inactive. A `/bin/false` probe and the holdout's OnSuccess both restarted it. Active again at 06:09:41 with `--rps 25 --credit-cap 4000000 --workers 24`.

`systemctl --user revert` on the running units deleted their drop-ins, including backfill `limits.conf` and funding `zz-helius.conf`. Restored from the live process environment (no secrets printed): backfill cap 4000000, rps 25, lookup 10, workers 24; funding rps 10, credit cap 2000000. Live cgroup caps were put back (tape 1G, backfill 4G).

**Attention-daily.** At MemoryHigh=768M it held ~768 MiB and ~2.2 GiB swap (`memory.events` high=47148, iowait ~15%). Raised live, RSS pinned at **4.00 GiB** (the temporary 4G max) with ~2.1 GiB still swapped and iowait then ~1%. Cap is now MemoryHigh=4G/MemoryMax=5G. It waits until `mal-laya-v0` is inactive so the 5G is not added to the 11G fit. Training window is laya 11G + forward 1G + funding 512M + tape 1G + observe 256M + attention 256M = **14G** of 23974 MiB, ~9.4G left. The 04:45 run then exited `FileNotFoundError` on `trades-2026-09-26T04.jsonl` (hour sealed mid-scan), not an OOM.

**Tape lag.** Yesterday all-day p50 median **1.44s**. Today 05:06 (the brief) **4.41s** during the fit plus attention swap. Public RPC `1006` reconnects were up all night, including before 04:15 while the 11G re-score was swapping. After the holdout, 06:06 p50 **1.63s**, 0 reconnects, IO pressure avg10=0. Batch units are Nice=19, IOSchedulingClass=idle, CPUWeight 10–20. Tape and forward paper are CPUWeight=1000; forward MemoryHigh raised 768M→1G (it was at ~806 MiB plus 122 MiB swap).
