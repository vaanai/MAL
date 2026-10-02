# Manager handoff 2026-10-02 (Manager2 → manager on mal-research-0)

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

## State at 2026-10-02 06:25Z (Manager2 → manager on mal-research-0)

The manager moves to `mal-research-0`. Read this section, then LAB_STATE.md, DEC-015, the EXP-012 file, and these memory notes:
- `helius-plan-limits`
- `long-jobs-via-miscusi`
- `owner-wants-autonomy`

The memory dir is per host. Recreate the key facts on research-0 from this file if the notes aren't there.

### Headline

**EXP-012 one-shot read: promotion-gate PASS under both fail models** (#208, checked by `quant-proof`). Block `[2026-09-03T12, 2026-09-09T12)` was read once:
- n = 451
- flat 0.0349 SOL/trade, CI90 [0.0187, 0.0513]
- pressure 0.0205 SOL/trade, CI90 [0.0110, 0.0299]
- 6/7 days positive
- ex-top-3 +14.04 / +8.22 SOL

This is a backward replay, **not money made**. Caveats, all in the EXP-012 Result:
- The block is favourable: the unfiltered base trade is also positive.
- The lift is mostly fill prediction (98.9% vs 28.0% fill); among fills it is not significant.
- The unfiltered book made more total SOL.
- Returns fade toward the latest day.

Per EXP-012 §9 this earns a forward-paper book after the 2026-10-05T05:00Z kill review, on `mal-fast-0` per DEC-015. Live needs a forward gate pass plus owner approval.

### Decisions (and why)

- **EXP-012 re-froze EXP-011's recipe on clean data** rather than reusing the retired model. EXP-011 §8 retired it, and its threshold was calibrated on the dirty pool. Trail: pre-registration #194, freeze #200 (job #23 at `ea5ec37`), pin #207, read job #32, result #208.
- **DEC-015 (#202–#204): new forward books run on `mal-fast-0`.**
  - Why: paper should run where live will most likely run, and Oracle's runner backlog breaches its 5 s cap (cause undiagnosed).
  - The runner is a systemd service: an owner-confirmed exception to "every long-running job is a MiScusi job".
  - EXP-012's book needs only the trade tape, observe creates and the model.
  - Preconditions: its own `mal-forward.slice` (5G/6G), tape coverage, md5 equivalence replay, lag probation.
- **Helius:** Developer plan, **50 rps**, about 1 credit/getBlock (dashboard-confirmed). Walkers share **about 40 rps** via 4 flock slots `/data/mal/locks/helius-{1..4}.lock` at `--rps 10` each. Budget is about 40M credits/month.
- **August hours have fewer slots** (about 8,700 / 9,850 vs 11,400 in Sept). August walkers use `--min-slots-per-hour 8000`, and their pre-read verify needs the same per-period bounds.
- **Visibility:** every long job is a MiScusi job. Every result goes to the MiScusi notebook and `data/console.json` (merge, then `git -C ~/MAL pull` on mal-fast-0, where the Console reads it), and result.v1 records go to `~/data/results/` on mal-fast-0.

### Still running: everything Manager2 started

MiScusi jobs (session MALsession1, all on mal-research-0, all gated walkers at `--rps 10`):

| Job | Id | Range | Sealed at 06:23Z | Credits |
| --- | --- | --- | --- | --- |
| #24 | `j_E7dSR9LYxDoduw` | backup `fresh-0828/w1` `[09-01T12, 09-03T12)` | 39/48 | 457,807 |
| #25 | `j_2LixVPrbGaWkqQ` | backup `fresh-0828/w2` `[08-30T12, 09-01T12)` | 38/48 | 445,361 |
| #26 | `j_0rbscFxTzCBzcg` | backup `fresh-0828/w3` `[08-28T12, 08-30T12)` | 38/48 | 450,089 |
| #27 | `j_H4GFQEl5oxYsmA` | expansion `explore-0814/w1` `[08-26T12, 08-28T12)` | 39/48 | 394,233 |
| #28 | `j_bmj84Om3r7SA5w` | expansion w2 `[08-24T12, 08-26T12)` | waiting for a lock slot | 0 |
| #29 | `j_aX7jwPae1-WZnw` | expansion w3 `[08-22T12, 08-24T12)` | waiting for a lock slot | 0 |
| #33 | `j_bBRcAQDLslxOqQ` | expansion w4 `[08-20T12, 08-22T12)` | waiting for a lock slot | 0 |

(#28/#29's progress text still says "waiting for EXP-012 walkers"; that stage has passed and they now wait on lock slots.) The backup and expansion-w1 walkers should finish around 10:00–11:00Z. Then w2–w4 take the slots.

**Not queued yet:** expansion w5 `[08-18T12, 08-20T12)`, w6 `[08-16T12, 08-18T12)`, w7 `[08-14T12, 08-16T12)`. Queue them as MiScusi jobs with the same gated command (copy #33's), output `/data/mal/blocks/explore-0814/wN`. w5's dir holds a harmless early checkpoint.

Outside MiScusi:
- **Fast trade tape trial.** Ubuntu user unit `mal-fast-trade-tape` on **mal-fast-0**, started (not enabled) 2026-10-01T16:25Z, drop-in `trial.conf`, output `/var/lib/mal/sealed/fast-trades-trial` (2.2 GB at 06:23Z, 2-day retention; disk 46% used). Stop it once the coverage comparison is done, unless DEC-015 proceeds with it.
- **`mal-status-research.timer`** on mal-fast-0 (claude user, every 5 min): status collection for research-0. It is meant to stay.
- The EXP-012 walker units `mal-walker-w1..w3` on research-0 are **inactive** (finished). Remove them at leisure.

**Finished, waiting for analysis:** job #35 `j_yUm7HdE50yxCOA`, the chain-truth walk `/data/mal/blocks/truth-1001` (2/2 sealed, 26,988 credits).

### Housekeeping done at handoff

- The tape-coverage worktree held a staged edit to `ARTIFACTS/lab/fast-tape-coverage-2026-10-01.md`. It was byte-identical to `1a57108`, the builder's pre-review text, and would have reverted the review fixes now on main (7afbc9a, via #212). It was dropped.
- The `~/MAL` stash "wip-verify-backfill-fixes-tmp-407693" (`613c4fd`, on `claude/backfill-integrity`) is an earlier draft of #179 and is **superseded by main**. Every test class in it exists on main, and main has the later 10,500 minimum and a fuller heal message. Safe to drop; left for the owner.

### Next steps, in order

1. **#35 has sealed:** run the tape coverage command from the 10-01 notes (`python3 -m tools.tape_coverage --start 2026-10-01T17 --end 2026-10-01T19 --chain-dir /data/mal/blocks/truth-1001 --chain-ssh mal-research-0 --margin-s 60 --json-out ARTIFACTS/lab/tape-coverage-chain-2026-10-01.json --md-out ARTIFACTS/lab/tape-coverage-chain-2026-10-01.md`; from research-0, drop `--chain-ssh` since the dir is local; the fast tape side is on mal-fast-0, so check how the tool reaches it). Quote both fast-vs-chain and Oracle-vs-chain, write the lab note, and update DEC-015 §2.2 with the verdict.
2. Queue expansion w5–w7 walkers (see the table above) as slots free.
3. DEC-015 remaining preconditions (all allowed before 10-05; **no forward book starts before 2026-10-05T05:00Z**):
   - observe unit on fast-0 (creates);
   - md5 equivalence replay of the runner (MiScusi job on research-0);
   - create `mal-forward.slice` and a runner unit (not started);
   - DEC-014 tooling works on fast output.
   Build these with builders in worktrees and review each.
4. **2026-10-05T05:00Z kill review** of the 9 Oracle books: snapshot, settle orphans, pressure stamp, `tools/kill_review.py --pressure-from-ms <2026-09-29T00:00:00Z ms> --holm-draws 10000`, `quant-proof`, LAB_STATE. Note Oracle tape quality and lag breaches as context. After it: `review_windows: []` in data/console.json.
5. After the kill review: start the EXP-012 forward book on fast-0 on probation (DEC-015). Its clean clock starts only after the 2-day lag probation.
6. When the expansion data is verified: exploration sweeps with `explore_entry_filter` / `explore_exit` templates (#205, #186) on research-0. Log every try (result.v1 tries log). Ask the owner for more MiScusi slots for CPU sweeps at that point (offered: 12–16).

### Gotchas

- **Clean-view mirror:** `backfill_verify --dedupe-out` writes `*.deduped.jsonl.zst`, which loaders don't find. Build `/data/mal/clean-view/<block>` as hardlinks under the raw names, with `VIEW.sha256` checked against the clean `MANIFEST.sha256` (script in the session history: copy the approach from `/data/mal/clean-view`).
- **Python on research-0:** always `/data/mal/venv/bin/python`, with `PYTHONPATH=$PWD` and `-m tools.X`.
- **Delegated MiScusi device tasks stall on Bash permission prompts.** Use in-session builder agents (worktree isolation) instead.
- **`gh pr edit` fails** (classic projects). Use `gh api -X PATCH repos/vaanai/MAL/pulls/N`.
- **Don't merge `tools/` changes** between a freeze and its read; the scorer refuses if `tools/` differ from the freeze commit.
- **Catalog hosts:** `research` (mal-research-0 blocks). Owner cells starting with "reserved" parse as `reserved` (denied for every role), per #210.
- **Stale agent worktrees** in `.claude/worktrees/` can be removed with `git worktree remove` (literal paths).

## Dated items

- **Daily 00:00:00Z:** Oracle runner restarts. Check `/home/claude/reports/runner-restarts.jsonl` for `ok: true`, `head_sha d7485d2`. Daily review 05:00Z.
- **2026-10-05T05:00:00Z kill review:** on a snapshot, settle orphans → pressure stamp → `tools/kill_review.py --pressure-from-ms <2026-09-29T00:00:00Z ms> --holm-draws 10000` → `quant-proof` → LAB_STATE. After it, set `review_windows: []` in `data/console.json` deliberately. Build the per-book paper P&L feed only after the review instant.

## Console

Live at `console.tradervaan.com` on mal-fast-0 (user service `mal-console`, 127.0.0.1:8787, from `~/apps/mal-console`, 512M / 50% CPU). It is deployed at mal-console `c67b7d1` and includes the "Running now" panel (#8) and DeepSeek error scrubbing (#9). The MiScusi key is in place. To redeploy, follow `deploy/README.md` in mal-console. The scrubber in `packages/server/src/scrub.ts` must stay at parity with `tools/mal_status.py` `scrub_log_line`; regenerate `test/fixtures/scrub-vectors.json` when either changes. Status timers on mal-fast-0: `mal-status` (every minute), `mal-status-core` (every 5 min) and `mal-status-research` (every 5 min, installed 10-01).

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
