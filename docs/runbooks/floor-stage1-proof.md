# FLOOR lane host stage 1: replay proof P0-P2 (QP-RULING-1112 condition 11, R5)

Code: `scripts/ops/floor_stage1_proof.py` (+ `.sh` wrapper for MiScusi jobs, which run under sh). Tests: `tools/test_floor_stage1_proof.py`. Stdout is counts, md5 and flag words only; a small JSON goes to `<out>/<cmd>.json`. Nothing here says the rule makes money. NOT RUN on real tape by the author.

## Windows (pre-declared)
- Seed: every replay seeds the pool table from 2026-11-02T01 (first hour of the research-0 hv7 copy). The host needs the seed at least 201 h before `--from-ms`, so the earliest valid `--from` is **2026-11-10T10Z**. 2026-11-10T00Z is not reachable.
- W1 (P0) = [2026-11-10T10Z, 2026-11-11T00Z), 14 h. The script counts writer status spans in it (the host's own `captured_no_loss_row` predicate on the raw gap rows with t_recv in [from - 365 min, to)) and refuses (exit 1) unless the count is 0. It does hold the backlog_jump holes G2-G8; both hosts refuse alike there.
- W2 (P1, P2) = [2026-11-12T00Z, 2026-11-13T00Z), QP's window (holds the 11-12 status span). The 11-10 day is not used for P1: its holes are `tip_gap_backlog_jump`, which stage 1 does not whitelist, so v2 = v1 there and P1 would be vacuous (exit 3).
- Closed hours only: `--to` must not be the current UTC hour.

## v1 reference
`git archive <merge>^1 tools observe` into `<out>/trees/v1` and HEAD into `<out>/trees/v2` (HEAD's `floor_lane_host.py` must equal the working file). Each replay is a subprocess that puts its tree first on sys.path and runs that tree's own `floor_lane_host.main(["replay", ...])`: pins are verified against that tree's files, no module is shared. P0 compares v1 (flags absent) with v2 (flags absent). P1 compares v1 with v2 + `status_span_no_loss`. Needs the repo history (a shallow clone cannot resolve `<merge>^1`; `--count-only` checks it).

## Precount (run first; opens nothing, creates nothing)
```
miscusi-agent job submit --name floor-s1-precount --machine mal-research-0 --role exploration --mem 2G --cpus 1 --time 10 --repo vaanai/MAL --ref <sha> -- "sh scripts/ops/floor_stage1_proof.sh p1 precount --count-only"
```
Expect `hours_missing=0`, `v1_parent_resolvable=True`, the two host md5s differing. Repeat with `p0` and `p2` if their windows differ.

## Jobs (one at a time; check `systemctl show user-1002.slice -p MemoryCurrent` first)
Memory: the host's own replay peak, expected at most 8 GB (pool table of ~8.5 days + ArenaState). Time: unmeasured; the ~10-day A/B replays took 2 h 17 m, so budget 0.5-1.5 h per replay.
```
miscusi-agent job submit --name floor-s1-p0 --machine mal-research-0 --role exploration --mem 16G --cpus 2 --time 240 --repo vaanai/MAL --ref <sha> -- "sh scripts/ops/floor_stage1_proof.sh p0 p0-<date>"
miscusi-agent job submit --name floor-s1-p1 --machine mal-research-0 --role exploration --mem 16G --cpus 2 --time 360 --repo vaanai/MAL --ref <sha> -- "sh scripts/ops/floor_stage1_proof.sh p1 p1-<date>"
miscusi-agent job submit --name floor-s1-p2 --machine mal-research-0 --role exploration --mem 16G --cpus 2 --time 600 --repo vaanai/MAL --ref <sha> -- "sh scripts/ops/floor_stage1_proof.sh p2 p2-<date>"
```
(`<sha>` = the PR head containing this script; the host under test is the checkout's `tools/floor_lane_host.py`, which must be the stage-1 merge's.) Output dirs are new directories under `/data2/mal/scratch/floor-stage1-proof/`; the script refuses any other and never overwrites.

## Pass criteria
- **P0**: `P0 PASS`: file set equal, `files_md5_differ=0` (md5 of every whole decision-log day file), `checkpoint_equal=True`, `status_spans_in_w1=0`.
- **P1**: `P1 PASS`: every `*_MUST0` count is 0, `v2_only_entries >= 1` (else exit 3, window proves nothing), `v2_only_entries == v2_only_at_v1_refusal_rows`; also printed: status span count and durations, refusals by reason/gap kind for v1 and v2, entries, md5 of the common entry and exit projections.
- **P2**: `P2 PASS`: nine cases (a: D = 5 min, 2 h, warm-5 min, warm+5 min, 7 h; c exception; d in_doubt; e unknown v1 checkpoint; e unknown status-flag-only checkpoint). Per case: prev_end, fallback (as expected), shadow_complete, n pools blocked, n reference buys in downtime (>= 1), post-restart entries, entries not in the reference (0), dispatched during shadow (0), rows written by the shadow (0), reference pools bought in the downtime that were not blocked (0), reference positions open in the downtime not covered (0). `blocked_equals_ref_buys` is informational: the empty-book shadow also blocks pools the reference holds, so the blocked set can be larger. T is chosen from the reference's own decisions and kept in the JSON only.

## Paste into DEC-035 Am.7's install record
Merge sha, PR head sha of the proof script, window instants, and the lines `P0 ...`, `P1 ...`, `P2-CASE ...` (counts and md5 only), plus the host md5s. State that the proof is host-level only (QP condition 13).

## SEALS-BRIEF line to add before the first run (draft, FLOOR LANE READERS (g))
- (g) FLOOR stage-1 replay proof (QP-RULING-1112 condition 12): `scripts/ops/floor_stage1_proof.py` (+ `.sh`) at the PR head sha, with `tools/floor_lane_host.py` at the stage-1 merge sha aa05923f and the v1 host at its first parent. Reads ONLY closed hours 2026-11-02T01..2026-11-13T00 of the research-0 hv7 copy (`/data2/mal/arena-tip-feed/hv7`, or `/data2/mal/scratch/floor-nov-funnel/tapecopy` for its hours) through symlinks in `/data2/mal/scratch/floor-stage1-proof/<run>/tapelink`; the path guard is `scripts/research/floor_tape_paths.py`. Writes only under `/data2/mal/scratch/floor-stage1-proof/`. Outputs: counts and md5 only (no pool id, price, P&L or row content). Paper only.

## Not covered
`main replay` cannot exercise lane_start or the live catch_up extension; P2 drives them with a fake clock and an in-memory engine (decision logic, not fills). The v1 checkpoint cases are made by editing the phase-A checkpoint (removing `guard` and `ended`), not by running a v1 process.
