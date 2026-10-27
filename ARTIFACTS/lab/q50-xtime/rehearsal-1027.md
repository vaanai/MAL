# Q50 X-time rehearsal at Y (2026-10-27, `date -u` ~20:30Z)

Records only. Rehearsal, NOT the X-time run: Y is not X (X is the merge sha of the filled DEC copy, after the gate read). No Q50/HV4 output, ledger, count, shadow/canary output or meta-capture was opened. No book has cleared the promotion gate.

Y = origin/main = `c8fcf761b773b8efc9b9a17387fbdc440c3eacec` at the start of this work. The worktree for this PR holds Y plus records/scripts-only commits (`git diff c8fcf761 HEAD -- tools` is empty, so the code under test is Y's).

## 1. Manifest: `MAL_REPO=<worktree> sh scripts/ops/q50-xtime-manifest.sh Y`
- rc 0. Pinned paths 43; list sha256 `244f9e85dd5870bfc47be1cf535eadecd0e2783cc17fee11440ab0ea7ae83720` (as expected).
- Changed pinned paths vs 0526d5cd: 4 (`tools/h5_arena_executor.py`, `tools/probe_executor.py`, `tools/pumpswap_simulate.py`, `tools/pumpswap_tx.py`); executor blob 70d789ff, sha256 ef8ae931...; the three mayhem blobs match their pins. Whole-tree changed paths 0526d5cd..Y: 709 (informational).
- Manifest file written on research-0: `~/h5-arena-manifest-<Y>.txt`, 2255 lines, sha256 `8168bdf514c0c8052c4142064ab86cb7c3065c2ebf7620d1d5fb8feb4cd28463`. This is the Y manifest, not X's; Helm gets X's, not this.
- `sh scripts/ops/test_q50_xtime_manifest.sh`: 6/6 PASS (head rc 0; extra line in each of 5 files rc 3).
- Host: mal-research-0 (checklist wants mal-fast-0 `~/MAL`); not run on fast-0.

## 2. Tests 1-17 + 17b (host: research-0, `/data/mal/venv/bin/python` 3.12.3, NOT mal-fast-0's Python)
- `tools/test_h5_arena_executor.py`, `tools/test_h5_arena_exec_wiring.py`, `tools/test_h5_arena_a3_pooled.py` (holds `test_17_*`, `test_17b_*`, `test_17c_*`), `tools/test_hv4_a3_pooled.py`: 240 passed, 33 subtests passed.
- Also `tools/test_pumpswap_mayhem_recipient.py` (Am.4 item 2 byte-identity), `tools/test_h5_executor.py`: green.
- `tools/test_a3_redeploy_check.py`: 1 FAILED, `test_frozen_guard_matches_the_shipped_pins_at_head` (`CheckError: pin missing: frozen_blobs is empty`, tools/a3_redeploy_check.py:734). Not one of tests 1-17/17b; a different tool (#778, pins file last changed there). Not investigated here; it fails at Y, so someone should check it before X. Not a claim about the executor.
- Must be re-run at X on mal-fast-0.

## 3. md5 replay: NOT run (it is a MiScusi job; the manager submits it)
At X (Y for a rehearsal, once the wrapper script is in Y's tree, which it is: #1027):
- machine: mal-research-0 (the 2026-09-20 shadow input is at `/data2/mal/scratch/l1g/shadow-2026-09-20-q50-a5f4137-rolling`, 18 MB); memory 8 GB (script header `q50-xtime-md5-replay.sh` l.11: "memory <= 8 GB, one job at a time on the host, no network"); resumable false; git_ref = the 40-hex sha.
- command: `sh /home/claude/.miscusi/repos/vaanai/MAL/scripts/ops/q50-xtime-md5-job.sh c8fcf761b773b8efc9b9a17387fbdc440c3eacec` (or a worktree path at that sha). Run under sh.
- pass: last line `md5-replay VERDICT: EQUAL` plus `#1481 counts required: A 137/137, B 101/101`, B synthetic_unconfirmed 0/0.
- copy dir for Y would be `...-rolling-installc8fcf761`; the earlier `-install0526` dir already exists on disk and is not in the way.

## 4. Not done
Dry run, install, X-time canary record check (only the new tool's unit test), N4 at X, tests on mal-fast-0.
