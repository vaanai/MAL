# Proof jobs P1 to P3 for tip-source restart continuity (QP's verdict on #1183)

Written 2026-11-01. NOTHING HERE WAS RUN. Each is a MiScusi job (`miscusi_job_submit`; `sh`, no bash-isms), on **explore-0814 hours only** (hunt23 RULES.md; pinned in `tools/arena_restart_equiv.EXPLORATION` as
`[2026-08-14T00, 2026-08-28T00)`, the narrower reading of "Aug 14-28"), printing md5s, field names and counts only. A min-rows floor is notebooked before each job, and credits are reported (all three use 0).
One heavy replay job at a time on a host: check `systemctl show user-1002.slice -p MemoryCurrent` before submitting. No result here says any book makes money; the gate is unchanged.

All three pass conditions are from QP's verdict on #1183 ("Before any runner relies on this"). They are owed before a book relies on the DEC-031 amendment that changes Am.23 items 5 and 7 for books
registered after it merges (the "Amendment <N>" section in this PR).

## P1. Writer drill on exploration hours

A fake tip dir built from explore-0814 walker hours (the arm-T pattern of the QP verdict on #1165), the pinned writer killed at 5 or more points with downtimes of 0, minutes, and over 1 h but under 60 h.
Pass: the derived copy's per-hour sha256 equals an uninterrupted writer's, no writer hole, the new writer resumed; 60 h or more is refused (`state_older_than_60h`). The receiver's verification of every closed
hour is arena_tip_push's own check and is read from the push job's `.push/verified.json` on a push run, not here.

```
DRY=1 HOURS=2026-08-16T12..2026-08-16T18 EXPLORATION=explore-0814 \
  sh scripts/ops/arena-tip-writer-drill-job.sh
# job: machine mal-research-0, resumable false, cpus 1, mem_mb 8000, time limit 4 h
HOURS=2026-08-16T12..2026-08-16T18 EXPLORATION=explore-0814 KILLS=3,10,25,60,120 DOWNTIMES_S=0,300,7200,36000,212400 \
  sh scripts/ops/arena-tip-writer-drill-job.sh
```

Core tested on a synthetic tip dir: `tools/test_arena_tip_writer_restart.py::test_p1_...`. The walker-hours builder call in `tools/arena_tip_writer_drill.py main` follows `scripts/research/arena_tip_equiv.py`'s own `main` and is untested here.

## P2. Restart equivalence with HV7's REAL runner

HV7-T at its F1 sha, the model replaced by the decision log or the replay cache (no API call). Uninterrupted-with-blind-interval against kill + restore, at 6 or more kill points, downtimes of 0, 120 s and 1 h,
two checkpoint lags. Pass: ledger md5 equal per bot (in order and as a set), zero `engine_restart` rows, fingerprint equal at the checkpoint row, zero sell decisions in the blind interval; a mutant control
(`CONTROL=1`: the sell journal lost) must differ.

```
DRY=1 FACTORY=<module>:<function> TAPE_DIR=/data2/mal/scratch/hv7-restart/explore-hours EXPLORATION=explore-0814 \
  START_MS=<first tick> END_MS=<last tick + 1 s> KILLS_MS=<k1>,<k2>,<k3>,<k4>,<k5>,<k6> DOWNTIMES_S=0,120,3600 LAGS=0,1 CONTROL=1 \
  sh scripts/ops/hv7-restart-equiv-job.sh
# job: machine mal-research-0, resumable false, cpus 1, mem_mb 6000, time limit 6 h; then the same without DRY=1
```

**Not built:** the FACTORY for exploration hours (`function(work_dir, runner_cls, tape_dir)` returning an HV7 runner of `runner_cls` at HV7's F1 sha with the decision-log/replay-cache model). The job refuses
without it. The synthetic factory and the comparison itself are tested in `tools/test_hv7_restart_equiv.py` (HV7's real `Hv7Runner` code, `tools/test_hv7_runner.py` fixtures).

## P3. A kill between a logged sell and its pricing

The same job with `KILLS_MS=pending-sell`: the kill instant is the first tick after which the engine holds a sell it has decided and not priced (in memory only). The sell is used as logged from the sell
journal, never re-derived; with the journal lost (`CONTROL=1`) the same positions become `engine_restart` (fail closed, whole positions, one row per stake).

```
DRY=1 FACTORY=<module>:<function> TAPE_DIR=/data2/mal/scratch/hv7-restart/explore-hours EXPLORATION=explore-0814 \
  START_MS=<first tick> END_MS=<last tick + 1 s> KILLS_MS=pending-sell DOWNTIMES_S=0,120 LAGS=0 CONTROL=1 \
  sh scripts/ops/hv7-restart-equiv-job.sh
```

Synthetic coverage: `tools/test_hv7_restart_equiv.py::test_b2_...` and `test_p2_p3_...`; `tools/test_arena_tip_continuity.py::test_b2_...`.

## Also owed, not a job

- The reference-driver read (`scripts/ops/arena-restart-equiv-job.sh`, `EXPLORATION=explore-0814`) is a cheaper cross-check of the continuity module; it is NOT P2 (the reference driver is not a book's runner).
- W2 of QP's list: wire the book's own runner (HV7's `resume` calls `engine.recover()` today). `tools/hv7_restart_equiv.Hv7ContinuityRunner` does it from the outside for the proofs; the registered runner is
  not edited here.
