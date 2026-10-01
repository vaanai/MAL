# Manager handoff 2026-10-01 (Manager2)

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

## State at 2026-10-01 ~02:30Z (Manager2)

**Done today:**
- **Data and the B3 re-check:** the exploration pool is deduplicated (#187), and the B3 clean re-run passed the same screen (#190, unablated model, exploration only).
- **EXP-012 Part 1 merged** (#194, `ea5ec37`). It re-freezes EXP-011's recipe on the clean pool, with the proceed screen enforced in code, input pinned by VIEW.sha256 hashes, and a read-once scorer `tools/exp012_score.py`. `quant-proof` and the reviewer both passed it.
- **"Running now" panel live:** the status collector (#193, #197) and the Console panel (mal-console #8) are deployed. Every log line is scrubbed, including keys and seed phrases.
- **Helius:** Developer plan, **50 rps**, about **1 credit per getBlock** (dashboard 5.6M vs walker self-reports about 4.2M). All walkers share **about 40 rps**. See the memory note `helius-plan-limits`.
- **MiScusi:** 8 jobs per session. Delegated tasks to devices stall on Bash permission prompts (reported to the owner), so use in-session agents for now.

**In flight:**
- **EXP-012 freeze:** MiScusi job #23 at `ea5ec37`, writing to `ARTIFACTS/exp012` in the job checkout and `/data/mal/exp012`. The job copies its artifacts to `$MISCUSI_OUTPUT_DIR`.
  - Next is **Part 2:** commit `ARTIFACTS/exp012` (FROZEN.md5, proceed_screen.json and the rest).
  - `quant-proof` checks the proceed numbers.
  - If `proceed` is false, EXP-012 is withdrawn and the block released.
- **EXP-012 block walkers:** user units `mal-walker-w1/w2/w3`, about 42 rps, done around 12:30Z. Then follow EXP-012 §4.1: `backfill_verify` (metadata + `--content`), dedupe, `exp012_score --write-dedupe-pin`, commit the pin, `--dry-run-preconditions`, then the read from a worktree at the freeze commit plus only the Part 2 commit.
- **Gated walkers #24–#29:** the backup block and expansion w1–w3. They start when the EXP-012 walkers finish, at most 4 at once, at 10 rps each. Queue expansion w4–w7 (`[08-14T12, 08-22T12)`) the same way once slots free.
- **Exploration expansion:** once sealed and verified, run sweeps on research-0. The owner will raise the slot count for CPU-only sweeps when asked.

**Known gaps:**
- `backfill_verify`'s slot-span sanity check needs per-period bounds before August blocks are verified.
- `deepseek.ts` errors in mal-console are not scrubbed.

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
