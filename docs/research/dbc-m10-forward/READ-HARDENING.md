# DBC m10 forward read: hardening under QP-RULING-1024-read-hardening.md (DRAFT)

Ruling: /data/mal/hunt-1008/push-1016/hunt29/dbc/QP-RULING-1024-read-hardening.md, sha256 3e880055063e294d3b384a533b7e7a79974632688b7dc66b731ec1e520adebf2.
Tag dbc-m10-forward-v1-am1 = 7d5be9e51a39db1b8030843a273f747eb92a2967. No tagged file is changed. No AM1-AM6 or R term is changed. Drafted 2026-10-24T18:17:26Z (`date -u`).
Gate status is unchanged. m10 has NOT cleared the promotion gate. The read is a CONFIRMATION only.
Nothing in this PR was run on real tape. Nothing under /data2/mal/venue-tape-backfill/fwdv1/ and no hour at or after 2026-10-25T00Z was listed, counted or opened. No marker file was touched.

## Item 2a: old-tool README (the manager creates it; not done by this PR)
Path: `/data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-v1/README-OLD-TOOL-DISABLED.txt`. Exact content (one line, then a newline):
```
R2 read tool is tools/dbc_fwd_read_am1.py (tag dbc-m10-forward-v1-am1). tools/dbc_fwd_read.py must never run; see QP-RULING-1024-read-hardening.md
```
Then run `chmod a-w` on the file, and then on the directory. The read job's guards check that the README exists, that it names the am1 tool, and that the file and the directory have no write bit (exit 12 otherwise).
Proof without the window: `tools/test_dbc_m10_read_hardening.py::test_old_tool_refuses_into_readme_dir_without_claim` calls the unamended run() on a scratch copy of this directory, with an empty
scratch root, now = 2026-11-02 and a scratch marker. It returns rc 3 and does not create the marker. For the record copy, the manager can repeat this on the real directory with a scratch root and a scratch marker, as item 2a describes.

## Item 2c: REGISTRATION addendum (records-only PR, dated with `date -u` before 2026-11-01T00:00Z)
> Addendum <date -u>: R2's read tool is tools/dbc_fwd_read_am1.py at tag dbc-m10-forward-v1-am1. tools/dbc_fwd_read.py is retained only as an imported library. Any invocation of it as a program or of its run() against FWD_ROOT is forbidden; a marker v3 claim by it ends m10 (AM6 class, licenses nothing). (QP-RULING-1024-read-hardening.md item 2, sha256 3e880055...)

The same line goes into docs/HANDOFF.md. This PR does not edit HANDOFF.

## Item 5: credit cap declaration (declare it before the first window day is fetched, 2026-10-26T00:20Z)
> Declared <date -u>: the fwdv1 fetch (out dir /data2/mal/venue-tape-backfill/fwdv1) runs with ONE fixed cumulative cap, `--credit-cap 3700000` (1.5 x the 2.465M of the Swap3 holdout week; QP minimum 3.7M).
> Procedure G has a separate cumulative budget of 300,000 credits over all passes. Neither is adjusted day by day against the meter. If the cap binds (rc 5), the missing names make the read refuse before the claim (not a look). A resume with a higher cap is then allowed and is recorded with its job id and credits.

Applying it: #1510 hard-codes `--credit-cap 2800000`. The checkpoint's credits_used is cumulative, so the cap covers the whole window. Steps:
1. Cancel #1510 while it is sleeping (any time before 2026-10-26T00:20Z).
2. Check with `ps` that no venue_backfill_dbc process survived the cancel (a cancel does not kill `--scope` children).
3. Submit scripts/research/dbc-m10-fetch-fwdv1.sh, which is #1510's script with only the cap changed. Use the same host, cwd and time limit as #1510.
Note: the script text is the copy of #1510's command found in the session scratchpad (fwd.sh, 07:11Z). Diff it against #1510's recorded command before you submit.

## Pin finding (item 1, step 1)
PINS.txt was written at tag dbc-m10-forward-v1 and records tools/venue_backfill.py = 15605f65.... At the am1 tag that file is 7185d58c2cc176f0...:
#856 (5c8b355a) added one docstring line. The 11 FILES of dbc_fwd_read_am1.py match AMENDMENT-1.md / SHA256SUMS-AM1.txt (venue_backfill_dbc.py matches PINS.txt).
tools/dbc_m10_read_ops.py pins the 12 values as they are AT THE AM1 TAG (checked by `git show 7d5be9e5:<path> | sha256sum`). If it used the stale PINS.txt value, every read would refuse with exit 10.

## Files (sha256 at the PR head; the read job checks the ops and launcher sha256 itself)
- scripts/research/dbc-m10-read-job.sh: the read job (item 1). Exit codes: 10 pins, 11 completeness, 12 guards, 13 memory (all before the claim, logged to READ-ATTEMPTS.log). Otherwise the tool's rc.
- tools/dbc_m10_read_launch.py: the Option A launcher (item 3).
- tools/dbc_m10_read_ops.py: the checks before the claim.
- tools/dbc_m10_gapfix.py: procedure G (item 4).
- tools/dbc_m10_decode_check.py and tools/dbc_m10_engine_equiv.py: the item 6 checks.
- scripts/research/dbc-m10-fetch-fwdv1.sh: the fetch with the item 5 cap.
- Tests: `python3 -m tools.test_dbc_m10_read_hardening` (synthetic only).

## Job specs (mal-research-0; ops checkout = a detached worktree of the merge commit, e.g. /data2/mal/wt/dbc-read-ops; tag worktree /data2/mal/wt/dbc-m10-read-am1)
Prep (once): `git -C ~/.miscusi/repos/vaanai/MAL worktree add --detach /data2/mal/wt/dbc-m10-read-am1 7d5be9e51a39db1b8030843a273f747eb92a2967` (the read job creates it if it is absent).
- F (fetch, replaces #1510): `sh /data2/mal/wt/dbc-read-ops/scripts/research/dbc-m10-fetch-fwdv1.sh`. Use #1510's cwd and time limit (past 2026-11-01T06Z). resumable false. Submit before 2026-10-26T00:20Z.
- G pass N (only when `python3 /data2/mal/wt/dbc-read-ops/tools/dbc_m10_gapfix.py identify` lists labels; only after a day's fetch has ended):
  `python3 /data2/mal/wt/dbc-read-ops/tools/dbc_m10_gapfix.py pass --pass N --wt /data2/mal/wt/dbc-m10-read-am1 --budget 300000`. Time limit 3 h, MemoryMax 4G, resumable false.
  It refuses while any venue_backfill_dbc process runs. The last pass must end before the read starts.
- C1 decode-only check (item 6, SPENT hours):
  `python3 /data2/mal/wt/dbc-read-ops/tools/dbc_m10_decode_check.py --root /data2/mal/venue-tape-backfill/meteora_dbc_holdout --start 2026-10-17T00 --end 2026-10-24T00 --out /data/mal/hunt-1008/push-1016/hunt29/dbc/decode-check-1017-1024`
  Time limit 4 h, MemoryMax 2G, resumable false. The root is the #1494 holdout root (L.HOLDOUT_ROOT). Confirm it before you submit.
- C2 engine-equivalence check (item 6, in-sample [2026-10-16T04, 2026-10-17T00)):
  `PYTHONDONTWRITEBYTECODE=1 python3 /data2/mal/wt/dbc-read-ops/tools/dbc_m10_engine_equiv.py --tag-root /data2/mal/wt/dbc-m10-read-am1 --out /data/mal/hunt-1008/push-1016/hunt29/dbc/sw3-engine-equiv`
  Time limit 2 h, MemoryMax 24G, resumable false. Exit codes: 0 PASS, 2 UNINFORMATIVE, 1 FAIL (the read must not run as tagged), 3 refused. The output records peak RSS and wall time.
- READ (item 1): `sh /data2/mal/wt/dbc-read-ops/scripts/research/dbc-m10-read-job.sh A <GiB>` (Option A needs a reviewer verdict and a QP OK on this PR by 2026-11-01T00:00Z; otherwise use `B`).
  after: F and the final G pass (if any). Time limit 10 h (>= 8 h; the job also waits for 00:10Z and for the 900 s mtime). resumable false.
  MemoryMax and `<GiB>` (the MemAvailable floor) both come from item 6 sizing: 1.5 x max(peak RSS of #1534 (52 h) x 3.3, peak RSS of C2 (20 h) x 8.4).
  For time: #1534 took 929.5 s for the causal engine over 52 h (about 51 min at 168 h). The resolve, pricing and AM4 passes come on top.
