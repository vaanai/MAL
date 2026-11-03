# Runbook: FALLBACK arena feed hand-off at H' #1652's end, 2026-11-11T22:00-22:08Z (research-0)

Written 2026-10-26 from 19:09Z (`date -u`). Records and tooling only. No profit claim; no book has cleared the promotion gate.

- Script: [scripts/research/feed-handover-1111.sh](../../scripts/research/feed-handover-1111.sh).
- Tests: [tools/test_feed_handover_1111.py](../../tools/test_feed_handover_1111.py).
- Base: [docs/runbooks/feed-handover-1107.md](feed-handover-1107.md). **Everything in that runbook applies unchanged** except what is listed here: design, refusals (rc 2-16), re-runs, `FH_START_IF_NONE`, test mode, what is signalled, and what to watch.
- Asked for by QP (OK on #945, comment 6424863148, "Risk") and by the manager (manager17): the tape must never go dark if the P35 v2 takeover slips.

## 1. What is different from 1107

| Item | 1107 | 1111 (this) |
|---|---|---|
| Old feed | #1516 | H' #1652 (the re-armed 1107 successor; the same f5bd26e7 feed, wrapper and argv) |
| Window | 2026-11-07T11:50-11:58Z | **2026-11-11T22:00:00-22:08:00Z** |
| Check A / B | 11:59Z / 12:12Z | **22:09:00Z** / **2026-11-12T01:00:00Z** (B is after #1652's limit end) |
| MIN_END (the new feed lives at least to) | 2026-11-09T02:00Z | **2026-11-18T22:00Z** (one week past the hand-off; QP pins the final value) |
| Submit window | [10-26T02:00Z, 11-07T11:45Z] | **[2026-11-04T22:00Z, 2026-11-11T21:45Z]**. An earlier start exits rc 15 |
| State dir / marker | `/data/mal/hunt-1008/ops/feed-handover-1107/` | `/data/mal/hunt-1008/ops/feed-handover-1111/` |
| Pins | HEAD f5bd26e7, arena_feed.py 7a9d424b..., wrapper 3d01f7ff... | the same |

The script body after the header equals 1107's byte for byte, except those pin lines and the log tag. `test_only_the_pins_differ_from_the_merged_1107_script` asserts this.

## 2. Instants: why these

- **Outside the registered tape reads.** HV6 is parked (smoke #1806 failed, 2026-10-30) and has no clock; the old reason (after HV6's 11-11T13:00Z read) is void. The instant stays valid. Per QP's re-check on #975, HV4 E4/E5/E6, the Q50 end-seal (11-06T00Z) and the A3 guard (to 11-07T00:57Z) all end before 11-11T22:00Z. At submit the manager re-checks the books registered then and **names the books in clock at submit** in the notebook entry. The `process_restart` gap must fall outside every one of them.
- **Before H''s end.** #1652 runs from about 2026-10-29T00:30Z to about **2026-11-12T00:30Z** (manager17; not verified here). The window ends about 2 h 20 min before that. **Before submitting, confirm #1652's limit end from `miscusi_job_status #1652`.** It must be after 22:08Z (the window) and before 01:00Z (check B). If it is not, move the pins in a new commit with tests, and get QP again.
- **After the v2 attempt.** The P35 v2 takeover window is about [11-11T14:00Z, 23:30Z] (DESIGN 4.1). **Pin the v2 instant at or before 21:30Z**, so this fallback runs only after v2 has either taken over or failed. See the v2 precondition in section 3.

## 3. Interplay with the v2 takeover

| State at 22:00Z | What this job does |
|---|---|
| v2 took over (the lock holder is `-m tools.arena_feed_v2`) | It matches no v1 feed. The lock is held, so it **refuses rc 13** and touches nothing. Tested: `test_a_v2_writer_holding_the_lock_is_never_touched`, also with `FH_START_IF_NONE=1`. This refusal is the expected outcome: notebook it as "fallback not needed". **Cancel the post-check C''** in that case: it would wake at 22:09Z and exit rc 16 ("no marker, the hand-off did not run"), which normally means "stop and ask QP". If C'' is not cancelled, its rc 16 (and the rc 13 of H'') are notebooked as "fallback not needed", not as a failure. |
| v2 did not run or aborted (rc 5 / 3); H''s v1 feed still writes | The plain 1107 hand-off: SIGTERM to the one v1 feed, then exec the pinned feed. One `process_restart` gap. Not exempt; it is a disclosed restart. |
| No feed at all | Default `FH_START_IF_NONE=0`: rc 12 (a manager acts). With 1, it starts the feed. Only the manager sets it, and it is notebooked. |

### v2 standby precondition (runbook-only; the script does not check it)

`tools/arena_feed_v2.py` is not on main today. DESIGN 2.1 gives v2 a standby process (`-m tools.arena_feed_v2`) on a separate `.arena-feed.standby.lock`, and a stopper job that retries until a deadline. This script looks only at exact-argv `tools.arena_feed` processes and `.arena-feed.lock`, so it cannot see a standby or a stopper. Cases it would mishandle: H'' and the v2 stopper both target the one v1 feed; the stopper later SIGTERMs H'''s fresh v1 feed (a second, unplanned restart); a polling standby takes the lock between H'''s lock check and the new feed's own flock (the new v1 exits 3, v2 holds the tape, continuity unproven). flock keeps the tape from two writers, but the seam would be unplanned.

- **If any v2 feed or standby code is merged or running before the submit, do NOT submit H'' / C'' until this PR is re-reviewed against that code.**
- **Cancel H'' and C'' before 22:00Z** if the v2 instant is pinned after 21:30Z, if the v2 stopper's deadline is not pinned before 21:45Z, or if a v2 standby or stopper is still in overlap at 21:45Z.
- Why runbook-only: a script refusal would need a new exit code and a fake-standby test, would change code that is byte-equal to the QP-ruled 1107 script, and would be written against a v2 design that has no code yet (the process names and flags could differ). It is better added once v2 exists, in the re-review above.

### Feed pin (decision, manager)

The fallback stays on HEAD **f5bd26e7** (`tools/arena_feed.py` sha256 7a9d424b...). This is the writer that every in-flight tape was recorded with, and a fallback must not change writer behaviour. Main has since merged PR #1152 (cfc04d1b, `LateSeen` 900 s late-duplicate filter; arena_feed.py is now 84cb6276...). #1152 says "deploy only at the next needed restart". **This fallback restart is deliberately excluded** from that: #1152 deploys on the planned v2 / normal path, folded into the one v2 takeover restart. `check_pins` refuses rc 10 at any other ref, so #1152 cannot be deployed here by accident. The restart record discloses that the new feed has the pre-#1152 late-duplicate behaviour. QP rules on this at re-review.

## 4. Before submit (manager)

1. This PR gets a reviewer verdict and a QP OK on its exact head, then merges. Copy the script out of the merge commit to `/data/mal/hunt-1008/ops/feed-handover-1111.sh`, chmod 440, and notebook its sha256, as in 1107 section 4.1.
2. Confirm #1652's limit end is in (22:08Z, 01:00Z) via `miscusi_job_list` or a `miscusi_job_extend` probe (no change made). If not, move the pins and get QP again. Check the v2 precondition in section 3 (no v2 code merged or running; else stop).
3. Dry run: a short job at git_ref f5bd26e7: `sh /data/mal/hunt-1008/ops/feed-handover-1111.sh dryrun`. Expect `pins ok` and one feed PID holding the lock. Before 11-04T22:00Z it also prints the expected `DRYRUN note: ... would end ...`.
4. Submit H'' and C'' with the job specs of 1107 section 5 and these differences:
   - H'' command: `sh /data/mal/hunt-1008/ops/feed-handover-1111.sh handover`, submitted inside [2026-11-04T22:00Z, 2026-11-11T21:45Z]. The name is #1652's name plus ` (fallback 11-11)`.
   - C'' command: `sh /data/mal/hunt-1008/ops/feed-handover-1111.sh check`, submitted with H''.
   - The same git_ref f5bd26e7, memMb 2253 / 256, cpus 1, resumable true, timeLimitSec 1209600, and no `after`.
5. Notebook (decision): the books in clock at submit (named), both ids, the submit instant (`date -u`), H'''s limit end, the script sha256 and its merge sha, `FH_START_IF_NONE`, and #1652's confirmed limit end.

## 5. Not covered

- It does not make the restart gapless: that is v2's job. It only makes sure the tape does not go dark.
- It does not cover a hand-off after H'''s own end (submit + 14 d). That one is owed if v2 is still not in use by then.
