# FLOOR lane host stage 1: replay proof P0-P2 and kill-line check (QP-RULING-1112 condition 11, R5; DEC-035 Am.7)

Code: `scripts/ops/floor_stage1_proof.py` (+ `.sh` wrapper for MiScusi jobs, which run under sh). Tests: `tools/test_floor_stage1_proof.py`. Subcommands `p0`, `p1`, `p2`, `killcheck`, each with `--count-only`. Stdout is counts, md5, flag words and (killcheck only) order ids; a small JSON goes to `<out>/<cmd>.json`. Nothing here says the rule makes money. NOT RUN on real tape by the author.

## Scope (manager decision, DEC-035 Am.7)
Am.7 installs with `cheap_restart: true` and `status_span_no_loss` FALSE, and the unit runs WITHOUT `--since-hour`. The install needs **P0 and P2**; `killcheck` runs after each restart that can be cheap. **P1 is required only before `status_span_no_loss` is switched on (a later amendment).**

## Tape source: hv7 only
The script reads only closed hours of `/data2/mal/arena-tip-feed/hv7` (default `--tape-src`; the 201 h seed from 2026-11-02T01 is reachable from hv7 alone, so the scratch tapecopy is NOT used and not named in the seals line). Hours are `tape-YYYY-MM-DDTHH.jsonl.zst`, no gaps allowed (`--count-only` reports missing hours).
- P0: 2026-11-02T01 .. 2026-11-10T23 (215 hours).
- P1 and P2: 2026-11-02T01 .. 2026-11-12T23 (263 hours); fallback P2 window below needs the same. `--to` must not be the current UTC hour.
- killcheck: 2026-11-02T01 up to the hour after restart + BLOCK_MS (about 24 h 1 min).

## Windows (pre-declared)
- Seed 2026-11-02T01; the host needs it >= 201 h before `--from`, so the earliest valid `--from` is **2026-11-10T10Z**.
- W1 (P0) = [2026-11-10T10Z, 2026-11-11T00Z), 14 h. Writer status spans in it are counted with the host's own `captured_no_loss_row` on the raw gap rows (t_recv in [from - 365 min, to)); the proof refuses (exit 1) unless the count is 0. P0 also requires entry + refusal rows >= 1 (exit 3 otherwise: equality over an empty log proves nothing).
- W2 / P2 window = [2026-11-12T00Z, 2026-11-13T00Z). 11-10 is not used for P1: its holes are `tip_gap_backlog_jump`, not whitelisted, so v2 = v1 there (vacuous).
- **Fallback (pre-declared, QP):** if the W2/P2 run is vacuous (exit 3: no T with a reference Buy in (T, T+5 min] and another position open at T, T + 7 h + horizon inside the window), rerun with `--from 2026-11-11T00 --to 2026-11-13T00`. Do not shorten D or the horizon.

## Reference and pin
v1 = `git archive <merge>^1 tools observe` (a0ae2091, the live host); v2 = `git archive <merge> tools observe`, PINNED to the stage-1 merge aa05923f (not HEAD): the script refuses unless HEAD's and the merge's `tools/floor_lane_host.py` blob is `ba7050e1ed3bbf1538a2745f4604110cadb80a7f` and the working file equals it. The archive is written to a file and both git's and tar's exit codes are checked. Each replay is a `python -I` subprocess that runs its own tree's `floor_lane_host.main(["replay", ...])`; pins are verified per tree.

## Precount (run first; opens nothing, creates nothing)
```
miscusi-agent job submit --name floor-s1-precount --machine mal-research-0 --role exploration --mem 2G --cpus 1 --time 10 --repo vaanai/MAL --ref <sha> -- "sh scripts/ops/floor_stage1_proof.sh p2 precount --count-only"
```
Expect `hours_missing=0`, `v1_parent_resolvable=True`, the two host md5s differing. Repeat with `p0`.

## Jobs (one at a time; check `systemctl show user-1002.slice -p MemoryCurrent` first)
Memory: P0 runs three replays one after the other (v1, v2 flags absent, v2 both flags on); each at most about 8 GB. P2 frees the reference and each case host before the next (peak 2-3 hosts during the restart cases), so `--mem 32G`. Time unmeasured (the ~10-day A/B replays took 2 h 17 m): budget 0.5-1.5 h per replay. P2 copies phase A's root once per case: it refuses unless free space stays >= 25.5 % of /data2 after the copies; delete the run dir after the result is recorded.
```
miscusi-agent job submit --name floor-s1-p0 --machine mal-research-0 --role exploration --mem 16G --cpus 2 --time 360 --repo vaanai/MAL --ref <sha> -- "sh scripts/ops/floor_stage1_proof.sh p0 p0-<date>"
miscusi-agent job submit --name floor-s1-p2 --machine mal-research-0 --role exploration --mem 32G --cpus 2 --time 600 --repo vaanai/MAL --ref <sha> -- "sh scripts/ops/floor_stage1_proof.sh p2 p2-<date>"
miscusi-agent job submit --name floor-s1-p1 --machine mal-research-0 --role exploration --mem 16G --cpus 2 --time 360 --repo vaanai/MAL --ref <sha> -- "sh scripts/ops/floor_stage1_proof.sh p1 p1-<date>"   # only before status_span_no_loss is switched on
```
Output dirs are new directories under `/data2/mal/scratch/floor-stage1-proof/`; any other is refused, nothing is overwritten. The dirs hold full decision logs (pool ids): only the script reads them.

## Pass criteria
- **P0**: `P0 PASS`: decision-log day-file md5s equal across v1 (flags absent), v2 (flags absent) and v2 with BOTH flags on (`files_md5_differ=0`, `files_md5_differ_on=0`); checkpoint equal for v1 and v2-off (`checkpoint_equal=True`) and for v2-on after removing only `guard` and `ended` (`checkpoint_equal_on_minus_guard_ended=True`); `status_spans_in_w1=0`; `n_entry + n_refusal >= 1`. `status_span_no_loss` cannot act in W1 (no status span).
- **P1**: `P1 PASS`: every `*_MUST0` count is 0, `v2_only_entries >= 1` (else exit 3), `v2_only_entries == v2_only_at_v1_refusal_rows`.
- **P2**: `P2 PASS`: nine cases, case (f), and `P2-CHECK missing_checkpoint_refuses=True`. The restart rebuild is `main run`'s with a checkpoint and no `--since-hour`; `start_duration_s_INFO` is information. Cases: a: D = 5 min, 2 h, warm-5 min, warm+5 min, 7 h; c exception; d in_doubt; e unknown (v1 checkpoint); e unknown (status-flag-only checkpoint). Per case: prev_end, fallback (as expected), shadow_complete, pools blocked, reference buys in downtime (>= 1), `n_ref_positions_open_in_downtime` and `..._uncovered_MUST0`, post-restart entries and `..._not_in_reference_MUST0`, `n_dispatched_during_shadow_MUST0`, `n_rows_written_by_shadow_MUST0`, `n_ref_pools_bought_in_downtime_missed_MUST0`. Coverage (QP B2): a reference position open in [T, T+D+10 s] is covered only if the pool's block after catch_up lasts to the position's exit (capped at restart + horizon) or the lane holds an open position on that pool at the restart. `blocked_equals_ref_buys` is informational (the empty-book shadow can block more). Case (f): `P2-F f2` a restart inside 24 h of the unflagged deploy span refuses every pool and the refusal comes from a `host_down` span; `P2-F f3` after the span aged out the restart is cheap (`shadow_complete=True`, `prev_end=signal`, no refusal). If P2 fails only on a post-restart entry, report it to QP (the lane runs on a fake clock up to 1.1 s later than the reference); do not alter the check.
- **killcheck**: `KILLCHECK PASS`: `n_on_reference_held_pools_MUST0=0`.

## Kill-line check (QP point 5)
```
miscusi-agent job submit --name floor-s1-killcheck --machine mal-research-0 --role exploration --mem 32G --cpus 2 --time 360 --repo vaanai/MAL --ref <sha> -- "sh scripts/ops/floor_stage1_proof.sh killcheck kc-<date> --live-root <path to the lane root> --restart-ms <restart instant in ms>"
```
`--live-root` has no default. It reads only the `entry` rows of `decisions/l1-floor-v2/*.jsonl` in [restart, restart + BLOCK_MS], runs the unguarded reference (the same code as P2) over [restart - 25 h, restart + BLOCK_MS] on hv7, and prints `n_live_entries`, `n_on_reference_held_pools_MUST0` (a live entry on a pool where the reference holds a position open at the entry's trigger) and the order ids of any such entry. Pools are matched in process and never printed. If the live log is on mal-fast-0 and the job runs on research-0, the manager must make the entry rows readable there; killcheck is only built and unit-tested here (no end-to-end test).

## Paste into DEC-035 Am.7's install record
Merge sha, PR head sha of the proof script, window instants, the `P0 ...`, `P2-CASE ...`, `P2-F ...`, `P2-CHECK ...` lines (counts and md5 only), and the host md5s. Say what a PASS does NOT show: live fills, slippage or P&L (FakeEngine; QP condition 13); what the live host does at a real restart (live staleness, real warm-up timing, the real catch_up extension; that is killcheck's job after each cheap restart); start duration under the real unit and fast-0 disk; the first live run (the deploy start is refuse-all by design); the real `exception` and `in_doubt` write paths; `status_span_no_loss` (P1).

## SEALS-BRIEF lines to add before the first run (drafts; next free letters after (f))
- (g) FLOOR stage-1 replay proof (QP-RULING-1112 condition 12): `scripts/ops/floor_stage1_proof.py` (+ `.sh`), subcommands p0, p1, p2, at the PR head sha, with `tools/floor_lane_host.py` at the stage-1 merge aa05923f (blob ba7050e1) and the v1 host at its first parent. Reads ONLY closed hours 2026-11-02T01..2026-11-13T00 of the research-0 hv7 copy `/data2/mal/arena-tip-feed/hv7`, through symlinks in `/data2/mal/scratch/floor-stage1-proof/<run>/tapelink`; path guard `scripts/research/floor_tape_paths.py`. Writes only under `/data2/mal/scratch/floor-stage1-proof/`; the run dirs hold full decision logs (pool ids) and are read only by the script. Outputs: counts and md5 only (no pool id, price, P&L or row content). Paper only.
- (h) FLOOR kill-line check (QP-RULING-1112 point 5): subcommand `killcheck` of the same script and sha. Reads (1) the `entry` rows of the live lane decision log `decisions/l1-floor-v2/*.jsonl` at a path given by the manager, only the rows in [restart, restart + BLOCK_MS], and (2) the hv7 closed hours as in (g). Prints `n_live_entries`, `n_on_reference_held_pools` and the order ids of offending entries; no pool id or price. Never starts, configures or restarts a lane executor or host. Writes under `/data2/mal/scratch/floor-stage1-proof/`.

## Not covered
`main replay` cannot exercise lane_start or the live catch_up extension; P2 drives them with a fake clock and an in-memory engine (decision logic, not fills). The four cases `c_exception`, `d_in_doubt`, `e_unknown_v1_checkpoint` and `e_unknown_status_flag_only` are HAND-EDITED phase-A checkpoints (the `ended` or `guard` keys): they prove that the fallback fires on the mark with a pickle that is not ahead, not that the mark is written. The real write paths (an exception while deciding a Buy, a failed final mark write, `in_doubt` before a non-periodic pickle) are covered by PR #1332's unit tests (`tools/test_floor_lane_host_stage1.py`: `test_an_exception_while_deciding_a_buy_makes_the_restart_refuse_every_pool`, `test_case_iv_the_final_lane_write_fails_after_the_pickle_the_in_doubt_mark_forces_refuse_all`).
