# Runbook: arena feed hand-off, 2026-11-07T11:50-11:58Z (research-0)

Written 2026-10-24 (manager15 worker). Records and tooling only. No profit claim; no book has cleared the promotion gate.
Script: [scripts/research/feed-handover-1107.sh](../../scripts/research/feed-handover-1107.sh). Tests: [tools/test_feed_handover_1107.py](../../tools/test_feed_handover_1107.py).

## 1. Facts this runbook rests on

| Fact | Source |
|---|---|
| Feed job #1516 = `j_Pfx3xriF3SGvZQ`, research-0, command `sh /data/mal/hunt-1008/ops/hv4-feed-shakedown.sh feed`, git `f5bd26e779746f40692a760a2b6a5d4e0cadc0b7`, started 2026-10-24T12:10:24Z, limit 1,209,600 s | manager brief; LAB_STATE "Now" (mid-week restart record 2026-10-24) |
| #1516 stops at **2026-11-07T12:10:24Z**; MiScusi refuses a total above 14 d, so it cannot be extended | DEC-031 Amendment 12 item 5 |
| It feeds HV4 (clock 2026-10-25T00Z; seg2 to about 2026-11-08T13:05Z) and FLOOR (clock 2026-10-26T00Z; book window to END_H + 25 h = 2026-11-08T01:00Z, slip 2026-11-09T01:00Z) | #779 section 3a; DEC-031 Am.12 item 5 |
| The successor is pinned for **2026-11-07T11:50-11:58Z**, as host-side MiScusi jobs; its `process_restart` gap is not exempt | #779 section 3a; DEC-031 Am.12 item 5 |
| Procedure: `/data/mal/hunt-1008/push-1016/hunt28/l2/QP-RULING-1023-feed-handover.md`, sha256 `170bd3bbf11ce8d0bcf9f59bd25895338c4d7d5e9331fd213ed271ad20a9bf62`, section 3 | re-hashed 2026-10-24 |
| 10-24 lessons: the MiScusi cancel left the scope child alive (it needed SIGTERM); a fresh process writes one `process_restart`/`all` row (span 5,193 ms on 10-24) and no `feed_restart`; a MiScusi `after` does NOT fire on a time-limit kill | notebook n_e0fsG-6YopjbOw; QP-RULING-1024-779-feed (B) |
| Owner rule: every fixed-instant step is a host-side MiScusi job, never a session cron. A MiScusi job cannot cancel another job | CLAUDE.md; manager brief |
| Wrapper `/data/mal/hunt-1008/ops/hv4-feed-shakedown.sh` sha256 `3d01f7ff6d51c22219fb116f438dac81fc6e657fdadcd961cd7bb19b96ec7205`; `tools/arena_feed.py` at f5bd26e7 sha256 `7a9d424b6584b097ee61e4c7b48bef6e43b317bdd438ba588074057b5828826f` | re-hashed 2026-10-24 |

The feed code does not change (same commit, same wrapper, same arena_feed.py sha256), so this is not a runner change and needs no md5 replay proof. It is still a mid-week feed restart: it goes in LAB_STATE (section 6).

## 2. Design

Two host-side MiScusi jobs, both submitted days ahead and both sleeping:

- **H (the successor feed).** `feed-handover-1107.sh handover`. At submit it runs every check at once and exits loud if one fails, so a bad submit is seen days early. Then it sleeps (it logs every 6 h) to 11:50:00Z, checks again, and SIGTERMs the one old feed process. It waits until that process is gone (SIGKILL only after 60 s, disclosed, as in QP section 3 step 2), checks that the tape lock is free and no feed runs, writes a marker, and then runs `exec sh /data/mal/hunt-1008/ops/hv4-feed-shakedown.sh feed`. The wrapper execs `systemd-run --scope` and then python, so the new feed is this job's own process and the gap is only the old feed's stop plus the new feed's start-up. The old job #1516 is not cancelled: a job cannot cancel a job. After its process exits, #1516 ends as a MiScusi job (about 11:50Z, not 12:10:24Z).
- **C (the post-check).** `feed-handover-1107.sh check`. Check A runs at 11:59:00Z and passes only if all of these hold:
  - exactly one feed process, and it holds the lock;
  - the status file is under 120 s old;
  - the old PID is gone;
  - exactly one `process_restart` gap row in the hand-off hour, with span ≤ 600 s (QP section 3 step 4).

  Check B runs at 12:12:00Z, after #1516's limit instant. It checks that the same PID is still the only feed. C prints `CHECK_OK` (exit 0) or `!!!! FEED HANDOVER REFUSED rc=16` and its `CHECK FAIL:` lines.

What gets signalled: only a process that meets every one of these:
- it belongs to the job's uid;
- its argv has `-m tools.arena_feed` and `--out-dir /data/mal/arena/tape`;
- it is not a zombie;
- it is the only such process;
- it holds the flock on `/data/mal/arena/tape/.arena-feed.lock` (read from `/proc/locks`; the script never takes or touches the lock).

The script re-reads the process start time before each signal, so a reused PID is never signalled. Nothing else is ever signalled.

Refusals. Each one prints `!!!! FEED HANDOVER REFUSED rc=<n>` to stdout and stderr:

| rc | Meaning | Old feed state |
|---|---|---|
| 2 | bad mode, or an `FH_*` override without `FH_TEST=1` | untouched |
| 10 | pin: HEAD ≠ f5bd26e7, arena_feed.py or wrapper sha256 differs, dirty checkout | untouched |
| 11 | time: after 11:58:00Z (at submit, at wake, or just before SIGTERM) | untouched |
| 12 | feed count ≠ 1 (or 0 without `FH_START_IF_NONE=1`); or a feed appeared after the stop | untouched, except "appeared after the stop" |
| 13 | tape dir, lock or marker state unexpected (lock missing, held by a non-feed PID, still held after the stop; marker write failed) | untouched, except "after the stop" lines, which say "act now" |
| 14 | the old feed survived SIGTERM + 60 s + SIGKILL + 10 s; nothing started | signalled |
| 15 | submitted too early: start + 14 d < 2026-11-09T02:00:00Z | untouched |
| 16 | post-check failed | n/a |

Re-runs. If H is restarted after the marker exists (host reboot, MiScusi resume) and no feed runs, it execs the feed at once and logs `RESUME ... UNPLANNED restart: disclose it`. If a feed is running, it refuses (rc 12). It never starts a second feed. If H is restarted before 11:50Z, it simply sleeps again.

`FH_START_IF_NONE=1` (production option, default off). If no old feed process exists at 11:50Z, H starts the feed instead of refusing. The default follows QP section 3 step 0 ("exactly 1 PID"). Only the manager may set it, and if set it goes in the notebook entry. Trade-off: with the default, a dead old feed at 11:50Z stays dead until a manager acts. With the option on, the feed restarts at once, and a restart racing a resumed #1516 is still safe, because the second instance exits 3 on the lock.

## 3. Submit-date math (the new feed must live to ≥ 2026-11-09T02:00Z)

- The MiScusi limit counts from the job's start; H starts on submission. End = start + 1,209,600 s (14 d, the maximum).
- The coverage needed is FLOOR's slip END_H + 25 h (2026-11-09T01:00Z) plus 1 h of margin, so 2026-11-09T02:00:00Z. HV4 seg2 ends about 2026-11-08T13:05Z, so it is covered too.
- **Earliest submit: 2026-11-09T02:00:00Z − 14 d = 2026-10-26T02:00:00Z.** The script enforces this: an earlier start exits rc 15 at once and prints the earliest instant.
- **Latest submit: 2026-11-07T11:45Z.** That leaves time for the submit-time checks and the log check. A start in the window [11:50, 11:58]Z is accepted and runs the hand-off at once; after 11:58Z the script refuses.
- **Recommended: 2026-10-26, 02:00-06:00Z** (end 2026-11-09T02:00-06:00Z). That is the first instant that covers FLOOR's slip end. From then on the 11-07 instant lives in a host-side job, not in a session. A later submit gives a longer tail (end = submit + 14 d) but depends on a session existing to submit it. Either way the new feed ends at submit + 14 d. Any book that needs the feed after that end needs another hand-off, pinned the same way; that is owed now.
- C: any submit after 2026-10-24T12:13Z covers 12:12Z on 11-07. Submit it with H.
- If research-0 reboots while H sleeps: a resumed H re-sleeps. Whether MiScusi counts the limit from the first start or from the resume is unchecked (QP 1023). From the first start, the end is unchanged; from the resume, it is later. If H was not resumed, resubmit it (same spec) inside the window above.

## 4. Before submit (manager)

1. Merge this PR (QP + reviewer verdicts on the PR). Then copy the script out of the merged commit. H runs at f5bd26e7, which does not contain it, so it runs a pinned copy, like the wrapper:
   ```sh
   git -C <a clean main checkout> show <merge sha>:scripts/research/feed-handover-1107.sh > /data/mal/hunt-1008/ops/feed-handover-1107.sh
   chmod 440 /data/mal/hunt-1008/ops/feed-handover-1107.sh
   sha256sum /data/mal/hunt-1008/ops/feed-handover-1107.sh   # must equal the blob's sha256 at the merge sha; notebook it
   ```
2. Dry run, a short MiScusi job on research-0 at git_ref f5bd26e7, mem 256 MB, limit 600 s: `sh /data/mal/hunt-1008/ops/feed-handover-1107.sh dryrun`.
   - Expected log lines: `pins ok`; `feed processes=1 [<pid>] lock holders=1 [<same pid>]`; `window: not open yet`; `DRYRUN_OK old_pid=<pid>`.
   - Before 2026-10-26T02:00Z it also prints `DRYRUN note: a job started now would end ...`; that is expected.
   - It sends no signal, writes nothing and creates no dir. It reads `/proc`, `/proc/locks`, the status file's mtime and `df`. It reads no tape row.
3. Check `systemctl show user-1000.slice -p MemoryCurrent` (research-0). It is not a heavy job, but H reserves 2,253 MB for 12+ days.

## 5. Job specs (exact)

**H: successor feed** (submit 2026-10-26T02:00Z-06:00Z recommended; valid [2026-10-26T02:00Z, 2026-11-07T11:45Z])

| Field | Value |
|---|---|
| machine | `mal-research-0` (research-0, the same machine as #1516) |
| repo | `vaanai/MAL` |
| git_ref | `f5bd26e779746f40692a760a2b6a5d4e0cadc0b7` (the pinned commit, not `main`) |
| command | `sh /data/mal/hunt-1008/ops/feed-handover-1107.sh handover` |
| name | #1516's name, plus ` (successor 11-07)` |
| memMb | 2253 (same as #1207/#1516; the wrapper's scope is MemoryMax=2G) |
| cpus | 1 |
| resumable | true |
| timeLimitSec | 1209600 (the maximum; must be the maximum, the end math in section 3 assumes it) |
| after | none (an `after` on #1516 would never fire: a time-limit kill does not trigger it) |

**C: post-check** (submit with H)

| Field | Value |
|---|---|
| machine | `mal-research-0` |
| repo / git_ref | `vaanai/MAL` / `f5bd26e779746f40692a760a2b6a5d4e0cadc0b7` (unused by the check; same ref for one fewer variable) |
| command | `sh /data/mal/hunt-1008/ops/feed-handover-1107.sh check` |
| memMb | 256 |
| cpus | 1 |
| resumable | true (a resumed check re-sleeps to its instants) |
| timeLimitSec | 1209600 |

Within 5 min of submitting, read both logs. Each must show `pins ok` (H) or `start` (C), one feed PID holding the lock (H), and `sleeping until 2026-11-07T11:50:00Z` (H) or `...11:59:00Z (check A)` (C). Any `REFUSED` line: fix and resubmit.

## 6. What the manager notebooks

1. **At submit** (kind decision): "feed hand-off 11-07 armed". Include:
   - H and C job ids and MiScusi ids, and the submit instant from `date -u`;
   - H's limit end (submit + 14 d) and that it is ≥ 2026-11-09T02:00Z;
   - git_ref f5bd26e7; the sha256 of `/data/mal/hunt-1008/ops/feed-handover-1107.sh` and the merge sha it came from;
   - the wrapper sha256 3d01f7ff...; QP ruling sha256 170bd3bb...; this runbook;
   - `FH_START_IF_NONE` value (default 0);
   - the dry-run job id and its `DRYRUN_OK` line.
2. **Daily until 11-07** (in the existing digest): H and C still running. If one is gone, resubmit it inside the section 3 window and notebook the new id.
3. **On 11-07 after check B** (kind result): "Planned feed restart 11-07 DONE" (or FAILED). Include:
   - #1516 / j_Pfx3xriF3SGvZQ → H's ids;
   - from H's log: SIGTERM instant, gone instant, exec instant and `sigkill` 0/1 (copy the marker file `/data/mal/hunt-1008/ops/feed-handover-1107/HANDOVER_DONE` verbatim);
   - from C's log: the `process_restart` from_ms / to_ms / span_ms / clean_stop / torn_tail and the tape hour, and the count of rows (must be 1);
   - the feed_restart rows (expect 0, as on 10-24);
   - #1516's end state and end instant in MiScusi;
   - check A / check B verdicts.

   Then add the LAB_STATE "Now" mid-week restart line. Cite it in #779 section 4 (HV4) and in FLOOR's records: under DEC-031 Am.12 item 5, the gap is not exempt.
4. **If anything refused**: the refusal line verbatim, what was done, and whether the feed was down (from when to when).

## 7. On 2026-11-07 (what to watch)

- 11:50:00Z H wakes: `WAKE`, `pins ok`, `wake: feed processes=1 [...]`, `SIGTERM sent to <pid> at ...`, `old feed <pid> gone at ...`, `EXEC new feed in this process`. Then the wrapper prints `head=f5bd26e7... mode=feed`.
- If H refuses with rc 2, 10, 11, 12 or 13 BEFORE `SIGTERM sent`, the old feed is still running. It keeps running until #1516's limit at 12:10:24Z. Before then, run QP-RULING-1023 section 3 by hand: SIGTERM the PID, then submit the same spec as H with command `sh /data/mal/hunt-1008/ops/hv4-feed-shakedown.sh feed`. Ask QP first if time allows.
- A refusal AFTER `SIGTERM sent` means the feed is down: act now. Find the lock holder (`/proc/locks`, inode of `.arena-feed.lock`), then submit the plain feed job.
- 11:59Z C check A; 12:12Z check B. `CHECK_OK` closes it. On rc 16, stop and ask QP (QP 1023 section 3 step 4: exit 3, a second PID, span over 600 s, or no writes).
- #1516 should end in MiScusi at about 11:50Z, when its process exits. If MiScusi still shows it running after 11:58Z, ps-check first (`pgrep -af 'tools[.]arena_feed'`: only H's PID). Then it is safe to cancel it, because it has no feed process left. Check with ps again after the cancel.
- If MiScusi restarts #1516 for any reason, its feed exits 3 on the lock (the new feed holds it). Check B then still shows one PID.
- Gap size. Fake feed in the tests: SIGTERM to exec in about 0.17 s. The real stop took about 2 s on 10-24 (SIGTERM 12:10:17Z, last row 12:10:19.445Z), and the real start-up adds python imports and the socket connects. Expected well under the 10-24 span of 5,193 ms, which included a manual submit. UNMEASURED until 11-07.
- Compaction: the window is clear of the HH:02 compaction of the closed hour (QP 1023 section 1(a) torn-tail risk). At 11:50 nothing is compacting.

## 8. Test mode

- `FH_TEST=1` allows these overrides:
  - `FH_T_START`, `FH_T_END`, `FH_CHECK_A`, `FH_CHECK_B`, `FH_MIN_END`, `FH_LIMIT_SEC`;
  - `FH_HEAD`, `FH_FEED_SHA`, `FH_WRAPPER`, `FH_WRAPPER_SHA`;
  - `FH_TAPE`, `FH_STATE`, `FH_TERM_WAIT_S`, `FH_KILL_WAIT_S`, `FH_PY`.
- In test mode the script refuses the production tape and state paths. It only matches `--out-dir <FH_TAPE>`, so it can never see the real feed.
- Without `FH_TEST=1`, any override is refused (rc 2).
- Tests (fake feed in a temp git repo: same flock, status file every 0.1 s, clean stop on SIGTERM or `FAKE_IGNORE_TERM=1`, one `process_restart` row on a restart):
  ```sh
  /data/mal/venv/bin/python -m pytest -q tools/test_feed_handover_1107.py
  ```
  They cover:
  - the pinned instants, including the 2026-10-26T02:00Z earliest submit;
  - the hand-off with the PID kept through exec and a bystander feed on another tape left alone, then check A/B;
  - SIGKILL after the TERM wait, disclosed;
  - the refusals (two feeds, after the window, submitted too early, lock not held, tape/lock missing, zero feeds, pin and dirty mismatches, overrides in production mode, production tape in test mode);
  - resume after the marker, and no second feed;
  - a dry run that signals nothing;
  - check failures (no marker, no feed, two `process_restart` rows).
- Manual rehearsal on a fake process (no pytest): see the `Rig` class in the test file. Start the fake `tools.arena_feed` on a temp `--out-dir`, set `FH_TEST=1` and the overrides with `FH_T_START` a few seconds ahead, and run `sh scripts/research/feed-handover-1107.sh handover` from the fake repo.

## 9. Not covered

- The script reads no tape rows. The one exception is check A: it parses only lines that contain `process_restart` in the hand-off hour file(s) (QP section 3 step 4). It reads no ledger, bots.json, score or P&L.
- It does not decide whether positions open across the gap are excluded. The engine and DEC-031 §1.4 / Am.12 item 5 decide that.
- It does not cover a hand-off after H's own end (submit + 14 d).
