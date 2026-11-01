# Runbook: hand-off of the tip writer (#2033) and the tip push (#2039) to successors

Written 2026-11-01 (L8, F3 of the host report `/data/mal/hunt-1008/ops/reports/2026-11-02/L4-nd-audit.md`). Procedure only; nothing here was run. Paper only, 0 credits, no keys. No profit claim.
Code: [tools/arena_tip_tape_writer.py](../../tools/arena_tip_tape_writer.py) (unchanged), [tools/arena_tip_push.py](../../tools/arena_tip_push.py) (unchanged), [tools/arena_tip_continuity.py](../../tools/arena_tip_continuity.py), [tools/arena_restart_equiv.py](../../tools/arena_restart_equiv.py).
Jobs: [scripts/ops/arena-tip-writer-job.sh](../../scripts/ops/arena-tip-writer-job.sh), [scripts/ops/arena-tip-push-job.sh](../../scripts/ops/arena-tip-push-job.sh). Drill: [scripts/ops/arena-tip-restart-drill.sh](../../scripts/ops/arena-tip-restart-drill.sh).

## 1. Facts this rests on

| Fact | Source |
|---|---|
| Writer #2033 and push #2039 were extended to the 14-day total; both end about 2026-11-15T07Z. One hand-off of each falls inside HV7's 14-day window (clock 2026-11-07T00Z) | L8 brief; L4 F3 ops half |
| MiScusi refuses a job total above 14 d, so a longer window always has a hand-off | CLAUDE.md; DEC-031 Am.12 item 5 |
| The writer is resumable: `<out>/.writer/state.json` is the checkpoint. A restart restores the reader and the adapter from it and reads on from there. It reads the follower's live dir, which keeps 72 h | writer module docstring (RESUME) |
| A resume is refused if the state is older than `RESUME_MAX_AGE_H` = 60 h (`state_older_than_60h`), if the config changed (`config_changed`), or if the sha256 of the writer, the adapter or the vendored reader changed (`pins_changed_*`). A fresh start reaches back at most `LOOKBACK_MAX_H` = 70 h | `Writer._validate_state`; constants |
| Margins: a resumed cursor is at most 60 h old and the live dir keeps 72 h (12 h to spare); a fresh start has 2 h to spare | `tools/test_arena_tip_writer_restart.py` |
| A second writer on the same dir exits 3 (flock); `.writer/stopped.json` (exits 6, 7, 8, 9) makes a plain re-run refuse until a human removes it | writer; `test_s2`, `test_a_the_latch_still_refuses...` |
| The writer's output is a pure function of the tip rows. Killed after every poll, or down for hours while the follower goes on, it resumes to the bytes of a writer that never stopped, with no `tip_gap_writer_hole` and no writer-sourced gap row | `test_a_a_restart_with_a_downtime_leaves_no_hole...`; the existing kill-after-every-poll test |
| The push is resumable: the receiver resumes from its local sizes, the sender from `.push/verified.json`. Receiver lock: a second receiver exits 3 | push module; job header |
| An engine fed by the persisted derived copy can be restarted by replay (adopt open positions, exits from the tape, no model call) with an md5 restart-equivalence on a synthetic fixture | `tools/test_arena_tip_continuity.py`, `tools/test_arena_restart_equiv.py` |
| Jobs run under `sh`; a cancel leaves a `systemd-run --scope` child alive; a wrapper crash blocks an `after` chain; 8 running jobs per session | memory notes (miscusi-jobs-use-sh, feedback-cancel-kills-scope, miscusi-session-job-cap) |

## 2. Shape of the hand-off

Sequential, not overlapping: two writers cannot share the dir (exit 3), so the successor starts when the predecessor has stopped. The hole is the gap between the two, which is short (seconds to minutes) against a 12 h margin. The successor is submitted **days ahead**, as a host-side MiScusi job chained with `after` on the predecessor, so no session cron and no person has to be awake at the instant.

```
#2033 writer (ends ~11-15T07Z)  --after-->  W2 writer  (same OUT, same config, same code sha256s)
#2039 push   (ends ~11-15T07Z)  --after-->  P2 push    (same SRC, same REMOTE_DST, same pinned REMOTE_REPO)
```

The push is chained after the push, not after the writer: it only reads the writer's dir, and a missing writer for a few minutes only means no new hour files to ship.

## 3. Pre-flight (any time before 11-14; all read-only or dry)

1. Names and sizes only, never open a `tape-*` file: the successor's commit must give the same sha256 for `tools/arena_tip_tape_writer.py`, `tools/arena_tip_adapter.py`, `tools/arena_tip_reader.py` as the running job printed (`JOB pin` lines of #2033's log). A different sha is `pins_changed_*` and a refused resume (a new H0; QP). This PR adds files and edits none of these three.
2. `REMOTE_REPO` on research-0 must be at the successor's commit with the same bytes of every file the receiver imports (the push job checks this and refuses). Prepare the checkout, then dry-run: `DRY=1 REMOTE_REPO=... sh scripts/ops/arena-tip-push-job.sh`, `DRY=1 sh scripts/ops/arena-tip-writer-job.sh`.
3. Same environment as the predecessor: `OUT`, `CATCH_UP_H`, `KEEP_VERIFIED_H`, `MIN_FREE_PCT`, `MEM_MAX`, `SLICE_MAX_BYTES` (the config is part of the state; a change is `config_changed`). On a resume `START_MS` and `CATCH_UP_H` come from the state.
4. Memory: `systemctl show user-1002.slice -p MemoryCurrent` is below `SLICE_MAX_BYTES` at the successor's start; the writer's job refuses otherwise (W6). The predecessor has exited by then, so its share is free.
5. Run `sh scripts/ops/arena-tip-restart-drill.sh` on the successor's commit (synthetic; about 2 minutes). It must end `DRILL end ... OK`.

## 4. Submit (days ahead, once pre-flight is clean)

Both as MiScusi jobs on `mal-fast-0`: `resumable: true`, writer `mem_mb 1600 cpus 1`, push `mem_mb 300 cpus 1`, the time limit the longest the 14-day cap allows from the planned start (so the NEXT hand-off is as late as possible), command `sh scripts/ops/arena-tip-writer-job.sh` / `sh scripts/ops/arena-tip-push-job.sh` with the environment of section 3, and `after` set to the predecessor's job id.

**Verify at submit, do not assume** (the two things this runbook could not check offline):
- What `after` waits for when the predecessor ends on its time limit (the job script forwards a SIGTERM and the writer should stop clean with state `stopped`; MiScusi may still record a limit stop as not-succeeded). Ask the job tool's documentation or read the status of a short test chain. If `after` does not fire on a limit stop, do **not** rely on it: use the fallback below.
- That the successor's `after` target is the job id, not the wrapper.

**Fallback if `after` does not fire on a limit stop:** submit the successor as a host-side job that sleeps (re-checking every 60 s) until the predecessor's `.writer/status.json` heartbeat is stale for 120 s and `.writer/lock` is free, then execs the job script. It must exit loud (not silently wait forever) if the heartbeat is stale and `stopped.json` exists (a latch: section 6). A job cannot cancel another job; this one never needs to.

Record both successors in LAB_STATE as a mid-week restart plan (CLAUDE.md: every mid-week runner restart is recorded), with the planned instant and the ids.

## 5. At the instant (about 11-15T07Z) and after

Watch with names, counts and heartbeats only (`.writer/status.json`, `.writer/counts.jsonl`, `.push/*`):

| Check | Expected |
|---|---|
| Predecessor log | `JOB end ... rc=0` (a clean stop; state saved) |
| Successor log | `JOB start ... out=<same OUT>`, no `REFUSED` |
| `.writer/status.json` | `state` goes to `catching_up` then `live`; `resumed` true; `caught_up` true within minutes; `problem` null |
| Gap rows | none with `src` writer; `gaps.written` unchanged by the hand-off. `tip_gap_writer_hole` never appears |
| Heartbeat gap | one entry in `hb_gaps` for the stop (informational, not a data gap) |
| Push | the receiver reports verified hours advancing; sizes on research-0 grow; no MISMATCH |
| Downtime | note the seconds between the predecessor's `JOB end` and the successor's `JOB start` in LAB_STATE |

## 6. If it goes wrong (every branch fails closed)

| Symptom | Meaning | Action |
|---|---|---|
| Successor exits 2 `state_older_than_60h` | it started more than 60 h after the predecessor stopped | Not a hole: a refusal. The derived copy has a real discontinuity if a fresh start is used; that is a new H0 and a QP question. Never delete `state.json` to force it |
| Exit 2 `pins_changed_*` / `config_changed` | code or config differs from the predecessor's | Fix the checkout or env to match. Do not edit the state |
| Exit 3 | the predecessor is still alive (a cancel left a scope child) | `ps` for the old process; SIGTERM it; the lock frees; resubmit |
| Exit 5 | tip unhealthy too long | the follower, not the hand-off; gap rows `tip_gap_status_unhealthy` apply as registered |
| Exit 6, 7, 8, 9 | latched: `.writer/stopped.json` | 6/7 a human; 8 the manager discards the derived copy on both hosts and a later start is a new H0; 9 QP re-rules first. Unchanged by this PR |
| Push exits 2 / 3 | receiver mismatch / refused | a human reads the message; the sender never forces |

## 7. The engine side

The writer/push hand-off does not restart the engine. A restart of the book host (the HV7-T / HV4-T / FLOOR-T runner) is a separate event. With [tools/arena_tip_continuity.py](../../tools/arena_tip_continuity.py) a restarted engine can rebuild its state from the persisted derived copy and price open positions' rule-based exits from the tape instead of booking them `engine_restart`. It is **not wired into any runner yet**: until a runner calls `restore_engine` (and QP rules on DEC-031 Am.23 item 7), a runner restart still books `engine_restart` through `ArenaEngine.recover()`, and `restore_engine` raising `ContinuityRefused` must fall back to exactly that. Before any runner relies on it, run `scripts/ops/arena-restart-equiv-job.sh` on exploration hours for that runner's exit rule and read `all_equal`.

## 8. What this does not cover

- The follower's own restart (a `backlog_jump` or a restart without its checkpoint is latched exit 6/9 and a registered gap row).
- A host reboot: the jobs are not auto-resubmitted by this runbook; the same fixed-instant rule applies (a host-side job, not a session cron).
- Anything about whether any book makes money.
