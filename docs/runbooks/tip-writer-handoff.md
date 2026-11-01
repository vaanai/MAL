# Runbook: hand-off of the tip writer (#2033) and the tip push (#2039) to successors

Written 2026-11-01 (L8, F3 of the host report `/data/mal/hunt-1008/ops/reports/2026-11-02/L4-nd-audit.md`), revised the same day on QP's CHANGES on #1183 (B5). Procedure only; nothing here was run. Paper only, 0 credits, no keys. No profit claim.
Code: [tools/arena_tip_tape_writer.py](../../tools/arena_tip_tape_writer.py) (unchanged), [tools/arena_tip_push.py](../../tools/arena_tip_push.py) (unchanged), [tools/arena_tip_continuity.py](../../tools/arena_tip_continuity.py), [tools/arena_restart_equiv.py](../../tools/arena_restart_equiv.py), [tools/hv7_restart_equiv.py](../../tools/hv7_restart_equiv.py).
Jobs: [scripts/ops/arena-tip-writer-job.sh](../../scripts/ops/arena-tip-writer-job.sh), [scripts/ops/arena-tip-push-job.sh](../../scripts/ops/arena-tip-push-job.sh). Drill: [scripts/ops/arena-tip-restart-drill.sh](../../scripts/ops/arena-tip-restart-drill.sh). Proof jobs: [tip-restart-proof-jobs.md](tip-restart-proof-jobs.md).

## 0. What this hand-off costs, under the rules as they stand (read this first)

DEC-031 Amendment 23 item 5 (`writer_hour_complete`, price-path) fails ANY UTC hour that holds a writer restart, a writer heartbeat gap over 60 s, or a closed hour not verified on the engine host within 15 min after it ends. A hand-off of the writer (or of the push, if it delays a verification past 15 min) is therefore NOT informational: **the hour that holds the hand-off, about 2026-11-15T07Z, fails the whole hour for every covered book whose counted window contains it.** For such a book:

- the hour is an excluded price-path hour (Amendment 6 item 2.4): attempts decided in it go to the 10% cap, and every trade whose span overlaps it is a §1.4 exclusion in the 5% cap;
- under Amendment 23 item 7 a restart of the writer or of the engine host is `engine_restart` for every position open at that instant (Amendment 6 item 4 unchanged);
- the cost is **one excluded hour, plus the positions open across it, for every covered window that holds ~2026-11-15T07Z** (HV7-T, whose clock is 2026-11-07T00Z and whose window is 14 days, and every other tip-source book whose window reaches it; read each registration for its own window).

The code in this PR (continuity, the restart-equivalence proofs) does **not** change that. Under the rules as written it only becomes usable for books registered AFTER a DEC-031 amendment merges (draft: the "Amendment <N>" section of DEC-031 in this PR), which must merge before the covered books' registration and smoke. Books registered or smoked before keep items 5 and 7 as written for their whole window. Record the hand-off hour in LAB_STATE as an excluded price-path hour of the tip source, with the planned instant, before it happens.

## 1. Facts this rests on

| Fact | Source |
|---|---|
| Writer #2033 and push #2039 were extended to the 14-day total; both end about 2026-11-15T07Z. One hand-off of each falls inside HV7's 14-day window (clock 2026-11-07T00Z) | L8 brief; L4 F3 ops half |
| MiScusi refuses a job total above 14 d, so a longer window always has a hand-off | CLAUDE.md; DEC-031 Am.12 item 5 |
| **A MiScusi `after` chain fires only when the predecessor exits 0.** A job that ends on its time limit does not exit 0 (the limit stops it), so `after` alone does NOT start a successor at a time-limit end. A gate job is needed (section 4) | QP on #1183 (B5); memory note miscusi-jobs-use-sh (a wrapper crash also blocks an `after` chain) |
| The writer is resumable: `<out>/.writer/state.json` is the checkpoint. A restart restores the reader and the adapter from it and reads on from there. It reads the follower's live dir, which keeps 72 h | writer module docstring (RESUME) |
| A resume is refused if the state is older than `RESUME_MAX_AGE_H` = 60 h (`state_older_than_60h`), if the config changed (`config_changed`), or if the sha256 of the writer, the adapter or the vendored reader changed (`pins_changed_*`). A fresh start reaches back at most `LOOKBACK_MAX_H` = 70 h | `Writer._validate_state`; constants |
| Margins: a resumed cursor is at most 60 h old and the live dir keeps 72 h (12 h to spare); a fresh start has 2 h to spare | `tools/test_arena_tip_writer_restart.py` |
| A second writer on the same dir exits 3 (flock); `.writer/stopped.json` (exits 6, 7, 8, 9) makes a plain re-run refuse until a human removes it | writer; `test_s2`, `test_a_the_latch_still_refuses...` |
| The writer's output is a pure function of the tip rows: killed after every poll, or down for hours while the follower goes on, it resumes to the bytes of a writer that never stopped, with no `tip_gap_writer_hole` (synthetic fixtures; the exploration-hours proof P1 is owed) | `test_a_a_restart_with_a_downtime_leaves_no_hole...` |
| The push is resumable: the receiver resumes from its local sizes, the sender from `.push/verified.json`. Receiver lock: a second receiver exits 3 | push module; job header |
| The job script's nested `systemd-run --scope` child survives a MiScusi cancel. After any cancel or limit stop, check with `ps` that no old writer or sender process is alive before the successor starts | memory note feedback-cancel-kills-scope; the job headers |
| Jobs run under `sh`; 8 running jobs per session | memory notes |

## 2. Shape of the hand-off

Sequential, not overlapping: two writers cannot share the dir (exit 3), so the successor starts when the predecessor has stopped. The gap between the two is short (seconds to minutes) against a 12 h resume margin, so the derived copy has no hole; the hour still fails (section 0). The successor is submitted **days ahead** as host-side jobs, so no session cron and no person has to be awake at the instant.

```
#2033 writer (ends ~11-15T07Z)  --gate-->  W2 writer  (same OUT, same config, same code sha256s)
#2039 push   (ends ~11-15T07Z)  --gate-->  P2 push    (same SRC, same REMOTE_DST, same pinned REMOTE_REPO)
```

The push is gated on the push, not on the writer: it only reads the writer's dir.

## 3. Pre-flight (any time before 11-14; all read-only or dry)

1. Names and sizes only, never open a `tape-*` file. The successor's commit must give the same sha256 for `tools/arena_tip_tape_writer.py`, `tools/arena_tip_adapter.py`, `tools/arena_tip_reader.py` as the **writer's pinned sha256s in the W12 ledger changelog line and the registration record** (the covered books pin those, not job #2033's log). A different sha is `pins_changed_*` and a refused resume (a new H0; QP). This PR adds files and edits none of these three.
2. `REMOTE_REPO` on research-0 must be at the successor's commit with the same bytes of every file the receiver imports (the push job checks this and refuses). Prepare the checkout, then dry-run: `DRY=1 REMOTE_REPO=... sh scripts/ops/arena-tip-push-job.sh`, `DRY=1 sh scripts/ops/arena-tip-writer-job.sh`.
3. Same environment as the predecessor: `OUT`, `CATCH_UP_H`, `KEEP_VERIFIED_H`, `MIN_FREE_PCT`, `MEM_MAX`, `SLICE_MAX_BYTES` (the config is part of the state; a change is `config_changed`). On a resume `START_MS` and `CATCH_UP_H` come from the state.
4. Memory: `systemctl show user-1002.slice -p MemoryCurrent` is below `SLICE_MAX_BYTES` at the successor's start; the writer's job refuses otherwise (W6).
5. Run `sh scripts/ops/arena-tip-restart-drill.sh` on the successor's commit (synthetic; a few minutes). It must end `DRILL end ... OK`.

## 4. Submit (days ahead, once pre-flight is clean)

Both as MiScusi jobs on `mal-fast-0`: `resumable: true`, writer `mem_mb 1600 cpus 1`, push `mem_mb 300 cpus 1`, the time limit the longest the 14-day cap allows from the planned start (so the NEXT hand-off is as late as possible), command `sh scripts/ops/arena-tip-writer-job.sh` / `sh scripts/ops/arena-tip-push-job.sh` with the environment of section 3.

**Chaining.** Because `after` fires only on exit 0, a predecessor that ends on its time limit does not release it. Use a gate job per successor:

1. The gate is a host-side MiScusi job (`sh`, no bash-isms) submitted days ahead, `mem_mb 100`, that waits until the predecessor's `.writer/status.json` heartbeat (writer) or `.push/status.json` (push) has been stale for 120 s AND the old process is gone (`ps`, no scope child) AND `.writer/lock` is free, then exits 0. It must exit LOUD (non-zero, with the reason) if `.writer/stopped.json` exists (a latch: section 6) or if it has waited past a deadline (predecessor's planned end + 2 h).
2. The successor is submitted with `after` = the gate job's id. It starts only when the gate exits 0.
3. If the predecessor ends clean (exit 0) before its limit, the same gate still releases the successor (the heartbeat goes stale); `after` on the predecessor is not used.
4. A job cannot cancel another job; the gate never needs to.

Record both successors and both gates in LAB_STATE as a mid-week restart plan (CLAUDE.md: every mid-week runner restart is recorded), with the planned instant and the ids, and the section 0 hand-off hour as an excluded price-path hour.

## 5. At the instant (about 11-15T07Z) and after

Watch with names, counts and heartbeats only (`.writer/status.json`, `.writer/counts.jsonl`, `.push/*`):

| Check | Expected |
|---|---|
| Predecessor log | `JOB end ... rc=` 0 or the limit stop; no old process in `ps` |
| Gate log | exit 0, with the instant it released |
| Successor log | `JOB start ... out=<same OUT>`, no `REFUSED` |
| `.writer/status.json` | `state` goes to `catching_up` then `live`; `resumed` true; `caught_up` true within minutes; `problem` null |
| Gap rows | none with `src` writer; `tip_gap_writer_hole` never appears (a data check; it does NOT save the hour) |
| The hand-off hour | **counted as failed `writer_hour_complete`** (a writer restart in the hour; a heartbeat gap over 60 s if the stop took that long). It is excluded for every covered window that holds it (section 0) |
| Heartbeat gap | one entry in `hb_gaps` for the stop: a price-path signal under item 5, not informational |
| Push | the receiver reports verified hours advancing; sizes on research-0 grow; no MISMATCH; the hour closing next to the hand-off is verified within 15 min or it fails too |
| Downtime | note the seconds between the predecessor's end and the successor's `JOB start` in LAB_STATE |

## 6. If it goes wrong (every branch fails closed)

| Symptom | Meaning | Action |
|---|---|---|
| Successor exits 2 `state_older_than_60h` | it started more than 60 h after the predecessor stopped | A refusal, not a silent hole. A fresh start is a new H0 and a QP question. Never delete `state.json` to force it |
| Exit 2 `pins_changed_*` / `config_changed` | code or config differs from the predecessor's | Fix the checkout or env to match. Do not edit the state |
| Exit 3 | the predecessor is still alive (a cancel left a scope child) | `ps` for the old process; SIGTERM it; the lock frees; the gate re-checks |
| Exit 5 | tip unhealthy too long | the follower, not the hand-off; gap rows `tip_gap_status_unhealthy` apply as registered |
| Exit 6, 7, 8, 9 | latched: `.writer/stopped.json` | 6/7 a human; 8 the manager discards the derived copy on both hosts and a later start is a new H0; 9 QP re-rules first. Unchanged by this PR |
| Push exits 2 / 3 | receiver mismatch / refused | a human reads the message; the sender never forces |
| The gate exits non-zero | latch, or the deadline passed | the successor does not start; a human decides |

## 7. The engine side

The writer/push hand-off does not restart the engine. A restart of the book host (the HV7-T / HV4-T / FLOOR-T runner) is a separate event, and under Amendment 23 item 7 as written it is `engine_restart` for the open positions. [tools/arena_tip_continuity.py](../../tools/arena_tip_continuity.py) can rebuild a restarted engine from the persisted derived copy (state only: no decision in the downtime, a sell logged before the kill used as logged from the sell journal, the replayed state checked against the checkpoint) and [tools/hv7_restart_equiv.py](../../tools/hv7_restart_equiv.py) wires that around HV7's real runner from the outside. It is **not wired into any runner**, and it is a rule change from the literal reading of item 7: it applies only to books registered after the DEC-031 amendment merges, whose proofs (P1 to P3 in [tip-restart-proof-jobs.md](tip-restart-proof-jobs.md)) are owed first. `restore_engine` raising `ContinuityRefused` must fall back to `ArenaEngine.recover()` (engine_restart).

## 8. What this does not cover

- The follower's own restart (a `backlog_jump` or a restart without its checkpoint is latched exit 6/9 and a registered gap row).
- A host reboot: the jobs are not auto-resubmitted by this runbook; the same fixed-instant rule applies (a host-side job, not a session cron).
- Anything about whether any book makes money.
