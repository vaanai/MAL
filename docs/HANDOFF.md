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

## State at 2026-10-01 ~01:30Z (Manager2)

Manager2 took over from Manager1 at ~00:45Z (MiScusi worker "Manager2", inbox reader). Manager1's setup notes (research-0 venv, raw pool copies, EXP-011 lock, #181/#182) still hold; see the memory note and `git log`.

**Done**
- **Ledger:** `[2026-09-03T12, 2026-09-09T12)` reserved for the next confirmation test (#184), walkers recorded (#185). Owner unnamed until a pre-registration names it. If clean B3 fails its screen, release it with a changelog line.
- **Walkers on research-0** since 00:54Z. Key placed by Helm at `/var/lib/mal/backfill/helius.env` (claude-only). User units `mal-walker-w1/w2/w3`, 48h each, 1.2M credit cap each, code pinned `2317b95` at `/data/mal/code/walker`, out `/data/mal/blocks/fresh-0903/w{1,2,3}`. ~16 min per hour → all 144h about 14:00Z. Check with `journalctl --user -u mal-walker-wN -n1 -o cat` and each `checkpoint.json`. Then: `backfill_verify` (metadata + `--content`), dedupe, sha256 manifest; report credits per walker.
- **Dedupe (job #1):** done, matches the census; totals and manifest hashes in `ARTIFACTS/lab/dedupe-exploration-pool-2026-10-01.md` (#187). 97 fast migrations were duplicated, so pool-A results before today were on dirty data.
- **`/data/mal/clean-view/<block>`:** hardlinked mirror of `/data/mal/clean` under the raw file names (the dedupe wrote `*.deduped.jsonl.zst`, which the loaders don't find). Each has `VIEW.sha256`, sha-checked against the clean manifest. **Point every exploration job at clean-view.**

**In flight**
- **B3 clean re-run**: MiScusi job #4 `j_YqPs2Ks67RYcFg`, `--max-workers 3` on purpose (`--max-workers` sets the chunk count, so 3 keeps chunk boundaries identical to 09-28 and isolates the dedupe). Outputs in the job's out dir on research-0 (`~/.miscusi/jobs/<id>/out/b3-clean.{md,json}`). Next: lab note `ARTIFACTS/lab/exploration-entry-model-b3-clean-2026-10-01.*` against the 09-28 pre-stated screen (both fail models: pooled top-10% mean > 0, ex-top-3 SOL > 0, > half of 9 days positive). Do not move the screen. Then `quant-proof`.
- **#186** (`explore_exit` runner wired, root allowlist, strict block_time on the new path): in re-review.
- **MiScusi #12** (guard checks only touched paths; owner/Grokbot request): open, in adversarial security review. Grokbot deploys; never deploy from MAL.

## Next, in order

1. Clean B3 result → lab note → if a cell passes: pre-register EXP-012 on the reserved block (frozen model + threshold, as EXP-011 did), before the walkers finish.
2. Walkers done → verify/dedupe/manifest → one-shot read only per the pre-registration.
3. Merge #186 after APPROVE; then wire `explore_entry_filter` the same way (allowlist + strict hours). Run exploration sweeps on clean-view; log every try (`result.v1`).
4. Move Console/status collectors to research-0 before any live key lands on mal-fast-0.
5. EXP-009 dedupe amendment: paused by owner decision.

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
