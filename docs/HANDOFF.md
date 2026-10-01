# Manager handoff 2026-10-01 (early UTC)

Replace this page at the next handoff; don't append to it. Read it first, then [LAB_STATE.md](../LAB_STATE.md), [CONSTITUTION.md](../CONSTITUTION.md), [docs/HOSTS.md](HOSTS.md), [docs/HOLDOUT_LEDGER.md](HOLDOUT_LEDGER.md) and the B3 lab note [ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md](../ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md).

`main` includes #181 and #182 (both merged 2026-10-01). Paper only. Once the new session confirms it has taken over, the old session (Manager1, 2026-10-01) stands down.

## The goal, and why it is reachable

**The owner's goal: real money in, and profitable, by the end of October 2026.** The owner wants the project to earn back what has been put into it, which means thousands of dollars of revenue. That is the owner's target, not evidence. The path to it is fixed and does not bend: clean tape → honest simulator → signals → one-shot test on unseen data → forward paper → promotion gate → owner approval → live.

What changed: we now have **mal-research-0** (32 cores, 125 GB RAM, ~868 GB disk), and it is idle. Compute is no longer the bottleneck. The things that cost us weeks were **waiting** and **mistakes**: a walker bug that duplicated rows and dropped an hour spent the whole EXP-011 holdout and we learned nothing from it. The way to go 5x faster is:

1. **Never lose a test to bad data again.** Every block gets fetched with the fixed walker, checked with `tools/backfill_verify.py`, deduplicated, and fingerprinted (sha256) before anyone reads it.
2. **Run many ideas in parallel on research-0**, on exploration data only, with every try logged. One dead candidate must not reset the clock: always have the next candidate already queued with its own fresh block.
3. **Cut waiting time.** Walkers on research-0, several at once. Merge reviewed PRs promptly. Keep mal-fast-0 for live listeners, not heavy jobs.

The bar does not drop. Winner's curse is real (B3 was the best of many cells). The gate in CLAUDE.md is what turns "looked good" into money. Hold it, and move fast everywhere else.

### Timeline to end of October (plan, not promise)

| When | What |
| --- | --- |
| 10-01 | Clean exploration pool on research-0 (job #1 running). Re-run B3 on clean data on research-0. |
| 10-01 → 10-02 | If B3 still clears its pre-stated screen: register the fresh block in the ledger **before** any hour is sealed, pre-register EXP-012, start walkers on research-0. In parallel, queue backup exploration lanes (exits, other entry families). |
| 10-05 05:00Z | Kill review of the 9 forward books (procedure below). No new forward books before it. |
| ~10-06 → 10-08 | Fresh block sealed, `backfill_verify` clean, one-shot read. |
| ~10-08 → 10-20 | If it passes: forward paper until ≥100 trades over ≥5 UTC days, majority positive, CI and ex-top-3 checks under both fail models. |
| late October | Gate cleared + owner approves → small live size. |

There is little slack. Every day of waiting on a key, a merge or a re-fetch comes straight out of it.

## What the 2026-10-01 session did

- **Took over in MiScusi** as worker "Manager1" (inbox reader), session MALsession1 `s_y1y4vFh5TCjy8A`. GitHub auth OK (`vaanai`), push OK, `ssh mal-research-0` OK.
- **EXP-011 lock** `/home/claude/data/exp011/HOLDOUT_READ.lock` set read-only. EXP-011 is closed NOT_DECIDABLE and is never re-run.
- **Exploration pool copied to research-0**, sha256-verified file by file against the source, each dir has `SOURCE.sha256`:
  - `/data/mal/raw/fast-pool-2026-09-18T23_2026-09-22T00` (73 hours: fast pre-cut + the two EXP-009 exclusion hours; B3's Pool A whitelist uses 71 of them)
  - `/data/mal/raw/oracle-insample-2026-09-22_25`
  - `/data/mal/raw/oracle-live-2026-09-25_27` (no migrations dir; creates are **daily** `creates/observe-<day>.jsonl`)
  - The EXP-009 and EXP-011 blocks were deliberately **not** copied.
- **research-0 Python venv** `/data/mal/venv`, pinned to mal-fast-0's versions: numpy 2.5.3, lightgbm 4.7.0, scikit-learn 1.9.1, scipy 1.18.1 (`/data/mal/venv/FREEZE.txt`). System `python3` there has no numpy: always use the venv for B3.
- **#181 merged**: B3 takes `--fast-dir`, `--oracle-insample-dir`, `--oracle-live-dir`, `--no-checkpoint`. Defaults unchanged; reviewer approved (158 tests, spawn smoke test confirmed roots reach workers). `exploration_exits.ALL_HOURS` is now lazy.
- **#182** (owner/Grokbot request): repo `.claude/settings.json` deny rules use `//etc/...` (absolute) and drop `Write(...)` rules covered by `Edit(...)`. Merged after a reviewer pass. Its open caveat: no fresh session has yet been observed refusing a Write to a denied path; check once. Note that Read/Edit denies never stop Bash (e.g. `sudo tee`), before or after this PR.

## In flight

**MiScusi job #1 `j_1JgjH_GG0uL02A` "dedupe exploration pool"** on research-0. It runs `backfill_verify --content --dedupe-out` on the three raw dirs into `/data/mal/clean/<same names>`, dedupes the daily `observe-*.jsonl` with `awk '!seen[$0]++'`, and writes `MANIFEST.sha256` in each clean dir. Check with `miscusi_job_status`. When it ends:
- Oracle dirs have no `checkpoint.json`, so `rc=1` / `hours_flagged` there is **expected**. Read the per-hour rows/unique counts instead.
- Sanity: the fast hours 2026-09-19T16, T17, T20 should lose ~974k / 476k / 1.02M trade rows (census, `/home/claude/data/dup-census/census.json`). Oracle in-sample should lose 0. Oracle live ~5–326 per hour.
- Record the totals in a lab note.

## Next, in order

1. **B3 re-run on clean data, on research-0** (MiScusi job, `role: exploration`, run from `main` ≥ `ab80ca4`):
   ```
   /data/mal/venv/bin/python tools/exploration_entry_model_b3.py \
     --fast-dir /data/mal/clean/fast-pool-2026-09-18T23_2026-09-22T00 \
     --oracle-insample-dir /data/mal/clean/oracle-insample-2026-09-22_25 \
     --oracle-live-dir /data/mal/clean/oracle-live-2026-09-25_27 \
     --no-checkpoint --max-workers 24 \
     --out-md $MISCUSI_OUTPUT_DIR/b3-clean.md --out-json $MISCUSI_OUTPUT_DIR/b3-clean.json
   ```
   Check the exact `--max-workers` semantics and memory per worker first (it was 3 on mal-fast-0). Then commit the outputs as a new lab note (`ARTIFACTS/lab/exploration-entry-model-b3-clean-2026-10-01.*`) with the duplicate-removal disclosure, and compare against the screen pre-stated in the 09-28 B3 note. Do **not** move the screen. Check ex-top-3 and the frozen post-ablation feature list before calling anything a candidate. `quant-proof` before any sentence says it made money.
2. **Fresh confirmation block.** Next unassigned range is older than 2026-09-09T12, e.g. `[2026-09-03T12, 2026-09-09T12)`. Add the ledger row **before** any hour is sealed. Walk it on research-0 with the fixed walker (`pump_history_backfill.py` from main ≥ `a47dbdb`; split across 3+ walkers by non-overlapping sub-ranges). **Blocked on Helm placing the Helius key on research-0**: the owner was asked to request it. Credits: walkers B + C used ~1.9M for 144h, so a 6-day block is ~2–3M of the ~20M headroom. Report credits per unit. Run `backfill_verify` (metadata + `--content`) before the one-shot read; any duplicate or missing hour stops the read.
3. **Parallel lanes on research-0** so one failure doesn't stall us: wire the two template runners (`explore_entry_filter`, `explore_exit`, #171 — they raise NotImplementedError; every lookback/buffer hour must pass `check_read`), then run sweeps on the clean pool. Every try goes in the tries log (`result.v1`, #166) for multiplicity. Then ask the owner to define the templates in MiScusi from `docs/contracts/job-templates.md` (the space has **no templates** yet).
4. **Move the rest to research-0**: Console (`vaanai/mal-console` `deploy/README.md`), status collectors, walkers. The Console moves before any live key lands on mal-fast-0.
5. EXP-009's block has resumed (duplicated) hours: its screen scorer must dedupe first, via a pre-registered amendment. Paused by owner decision.

## Dated items

- **Daily 00:00:00Z:** Oracle runner restarts. Check `/home/claude/reports/runner-restarts.jsonl` for `ok: true`, `head_sha d7485d2`. Daily review 05:00Z.
- **2026-10-05T05:00:00Z kill review:** on a snapshot, settle orphans → pressure stamp → `tools/kill_review.py --pressure-from-ms <2026-09-29T00:00:00Z ms> --holm-draws 10000` → `quant-proof` → LAB_STATE. After it, set `review_windows: []` in `data/console.json` deliberately. Build the per-book paper P&L feed only after the review instant.

## Console (unchanged since 09-30)

Live at `console.tradervaan.com` on mal-fast-0 (user service `mal-console`, 127.0.0.1:8787, from `~/apps/mal-console`, 512M / 50% CPU). The MiScusi `mck_` key is still pending: put it at `~/.config/mal-console/miscusi.key` (mode 600), then `systemctl --user restart mal-console`.

## Paused (owner decision), not forgotten

EXP-009 screen scorer; Lane D (run on research-0 with `chunk_plan`); Oracle lag spikes; backfill retry on `IncompleteRead`; warm start / live-readiness track; #90 keep.

## Gotchas found this session

- New Claude sessions on both servers start in **bypass permissions** (Grokbot, 2026-10-01). Server deny rules and MiScusi's protected-path guard still apply.
- MiScusi's protected-path guard rejects any Bash command whose **text** names `.env`, even in a commit message or PR body. Put messages in files (`git commit -F`, `gh pr create --body-file`).
- The clone at `~/.miscusi/repos/vaanai/MAL` had no git identity; it is now set repo-local to `Claude Code <reallybadaireviews@gmail.com>`, matching `/home/claude/MAL`.
- `/home/ubuntu/mal-oos` (the old walkers' code) is a plain file copy, not a git repo. No walker was running at 2026-10-01T00:30Z.
- MiScusi jobs run on a clean checkout of what's **pushed**; outputs go to `$MISCUSI_OUTPUT_DIR`, and you commit them yourself.

## Rules this project learned the hard way

1. **Memory on mal-fast-0:** one heavy job, ≤2 workers, bounded windows, whole-tree kill. Check `anon`, not `MemoryCurrent`. Better: run heavy work on research-0.
2. **Docs describe merged state only.** Fix the data and the wording, never a rule.
3. **Holdout ledger:** only the manager assigns blocks, before sealing. A block that's been seen can't become a confirmation holdout.
4. **Check ex-top-3 and the frozen feature list before calling anything a candidate.**
5. **Offline replicas mirror every live branch.**
6. **Builders stop at ~40 turns.** Tight numbered scopes; commit their WIP yourself.
7. **Run merges as separate commands.**
8. **Security review before merge for anything that gates data or limits.**
9. **Verify data before reading it.** `backfill_verify` + sha256 manifest, every block, every time. This is the rule that would have saved EXP-011.
