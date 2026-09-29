# Manager handoff 2026-09-29

The owner is starting a new manager session. Read this page first, then [LAB_STATE.md](../LAB_STATE.md), [CLAUDE.md](../CLAUDE.md), [CONSTITUTION.md](../CONSTITUTION.md), [docs/HOSTS.md](HOSTS.md), and [docs/HOLDOUT_LEDGER.md](HOLDOUT_LEDGER.md). The narrative of the previous session is in [ARTIFACTS/daily/2026-09-28-manager-session.md](../ARTIFACTS/daily/2026-09-28-manager-session.md). This page is meant to be short-lived. Replace it at the next handoff; don't append to it.

As of **2026-09-29 ~05:45Z**, `main` at `568be90` (#159). Paper only.

## Where we are, in one paragraph

The frozen migrate-direct cell is **dead** (formal FAIL, 2026-09-28 21:00Z). Exits and fee tiers were explored and are dead ends. The lever is **entry selection**. The exploration-pool entry model **B3** (#156) is the first result that survives costs on held-out days: S2 classifier, tp50_sl30, top 10%, 9/9 held-out days positive, and after a leakage ablation flat +6.22% (CI lo +3.85%) and pressure +3.59% (CI lo +2.06%). It is **exploration, not a promote**. Its confirmation test, **EXP-011**, is built but **not yet pre-registered on `main`**. Its fresh holdout block `[2026-09-09T12, 2026-09-15T12)` is being downloaded and must not be read until the pre-registration and its one-shot scorer are merged. The forward-paper kill review is 2026-10-05T05:00Z, and its scoring chain is complete.

## Pending, time-boxed (UTC)

**(a) EXP-011, the priority.** Branch `claude/exp011-prereg` (worktree `/home/claude/MAL/.claude/worktrees/agent-ae78cfb34ba409bdd`), **not a PR yet**. It holds:
- `tools/exp011_freeze.py`, the pre-registration `EXP/EXP-011-migrate-entry-model-prereg.md` (md5 and threshold still to be filled), and the ledger owner change;
- rows-to-disk streaming for all three pools;
- `tools/exp011_build_table.py` (Phase A) and `tools/exp011_rss_monitor.sh`;
- at `12e248b` (branch head), an **unfinished** `chunk_plan(..., max_home_hours=...)` in `tools/exploration_exits.py`, used by pools A/B/C. It splits a pool into more, smaller home windows that run ≤`max_workers` at a time. 89 tests pass.

Phase A has **not** produced a table yet. Its last attempt was killed at the 5 GB/worker guard, because the pool-B (Oracle live tape) `watch` dict grows over a ~32 h worker window. Steps:
1. Finish the chunking:
   - Wire `--max-home-hours` into `tools/exp011_build_table.py`.
   - **Fix the open risk:** each chunk reads only `buffer_hours` (default 2) past its home window, so a mint created near a chunk's end that migrates more than 2 h later is dropped. The old ~32 h windows rarely dropped these. Set a buffer that covers the create→migrate lag (e.g. 24 h), and prove the chunked and unchunked outputs give identical rows on a few-hour sample before the full run.
   - Fix the monitor so it kills the whole process tree, including spawn workers reparented to `systemd --user`. Match them by an env or cmdline tag, and use `setsid`.
   - Unit-test all of it on tiny slices only.
2. **Run Phase A attended**, alone, with ≤2 workers, under the monitor, after `systemctl show user-1002.slice -p MemoryCurrent` < 3 GB. Output: `/home/claude/data/exp011/table.jsonl`.
3. Phase B, from the table only: train the final model twice (md5 must match), set threshold = the 90th percentile of the outer OOF scores, and run the **nested fixed-threshold LODO** as a report-only preview. Write `ARTIFACTS/exp011/*` and fill the md5, threshold and nested table into the pre-reg.
4. Open and merge the EXP-011 PR (the ledger owner becomes EXP-011). Only then change the docs from "pending pre-registration" to "pre-registered".
5. Build `tools/exp011_score.py`: it loads the frozen model, checks the md5, **refuses to run unless both walker B and walker C checkpoints show all 72 of their hours sealed**, reads exactly `[2026-09-09T12, 2026-09-15T12)` from `/var/lib/mal/backfill-fast-b` + `-c`, streams with bounded windows, and applies the unchanged gate under both fail models (n ≥ 100, ≥5 UTC days with a majority positive, CI lo > 0 at 1,000 draws seed 1, ex-top-3 > 0). It also reports fill-conditional net and the selected fraction. Merge it before the read.
6. **Read once** after walkers B + C finish (~2026-09-30T02–03Z). Have `quant-proof` review before any sentence says it made money. A pass earns a forward-paper book **after** 2026-10-05, never live directly. A fail kills EXP-011, and there's no second read.

**(b) EXP-009 screen.** Walker 1 reaches its floor (2026-09-15T12) at ~2026-09-30T02Z. EXP-009 is a **screen** (k = 1, #154; ~2.5 eligible days), and a pass earns only a forward trial. A scorer still needs building. Follow `EXP/EXP-009-migrate-creator-gate-prereg.md` and all its amendments literally: G1 24 h lookback, burn-in, unknown-creator exclusion, the trades-based migrate trigger. Report how many rows differ from the migrations-sink trigger used to compute k. Read once.

**(c) Every day at 00:00:00Z:** the Oracle runner restarts automatically (claude timer `mal-runner-daily-restart`). Check the new line in `/home/claude/reports/runner-restarts.jsonl` (`ok: true`, lag under 5 s, `head_sha`). The daily review runs at 05:00Z into `/home/claude/reports/daily-review/`.

**(d) 2026-10-05T05:00:00Z kill review.** A single read, on a **snapshot** of Oracle's `positions.jsonl` (copy it with `ssh mal-core-0 'cat …' > local`). Order:
1. `tools/forward_paper_settle_orphans.py` (restart-dropped opens).
2. `tools/forward_paper_pressure_stamp.py` (the pressure leg).
3. `tools/kill_review.py --pressure-from-ms <2026-09-29T00:00:00Z in ms> --holm-draws 10000`.
The flat leg counts from 2026-09-28T00:00:00Z. Holm runs across the 9 books. An incomplete book is NOT_DECIDABLE. `quant-proof` reviews before anything is written as a result. Record it in LAB_STATE. No new forward books before then.

## Open threads

- **Lane D** (a learned filter on early bonding-curve entries): branch `claude/explore-early-entry-model` at `9e9b08e`, with streaming in place and **not yet run to completion**. It needs the same bounded-window chunking as EXP-011. Run it alone, after EXP-011's heavy steps, with its pre-stated screen (the nested fixed-threshold version).
- **More data:** after 2026-10-05, the forward-paper data from 2026-09-28 joins the exploration pool. Buy further fresh holdout blocks in ≥6-day chunks (~2M Helius credits each; a walker covers ~3 history hours per wall hour, and walkers scale in parallel) as candidates need them. Record the owner in the ledger **before** the walker starts.
- **Helius credits** (owner's ~20M autoscale headroom): 1,554,720 used across the three walkers at 05:40Z, against caps of 2.3M (walker 1), 1.1M (B) and 1.1M (C). Report credits per job in the daily note.
- **Live readiness** (after a confirmation): restart-neutral warm start (PR 1 is merged as #127; PR 2, the `serve()` wiring, isn't done), a forward book for the candidate, and then the live bar (~7 days forward, tiny size, the owner's explicit yes).
- [#90](https://github.com/vaanai/MAL/pull/90): keep.
- Deploy provenance: `/home/ubuntu/mal-oos` on mal-fast-0 is not a git checkout (its backfill module matched git main byte for byte on 2026-09-28). The walker unit files live in `/home/ubuntu/.config/systemd/user/` (`mal-fast-backfill-b/-c.service`, and walker 1's drop-in `mal-fast-backfill.service.d/range.conf`) and are not in git.

## Rules this session learned the hard way

1. **Memory on mal-fast-0.** 22 GB, no swap. The claude account is capped at 15G hard (no soft throttle). Exploration replays use ~3–5 GB per worker. **One heavy job at a time, ≤2 workers, bounded home windows, rows streamed to disk, check `MemoryCurrent` first, and use a whole-tree kill guard.** Two concurrent 3-worker jobs caused an OOM and a reboot (2026-09-29 02:15Z). Never run heavy jobs unattended overnight.
2. **Docs describe only what is merged.** Don't call something pre-registered, ready or live until its PR is on `main`. The owner's reviewers (Lyra, Grokbot) read every merge and caught this three times. Fix the data and the wording, never the rule.
3. **Holdout ledger.** Write the owner row before a walker seals any hour. Take timestamps from `date -u`, never estimates (two future timestamps had to be corrected). A block that was already seen can't become a confirmation holdout.
4. **Check ex-top-3 before calling anything a candidate.** The trailing stop looked positive pooled and was three moonshots.
5. **Offline replicas of live logic** (settlement, pressure stamp) must mirror every live branch (e.g. `LadderRule`) for every book kind. See the memory notes.
6. **Builders stop at ~40 turns.** Commit their WIP yourself (`git add/commit/push` in their worktree), then resume them with a tight numbered list. Prefer a fresh builder over resuming one whose context is huge.
7. **Run merges as separate commands.** A chained `set -e` command merged a PR after an earlier step had failed.

## Host how-to (verified this session)

- **Oracle `mal-core-0`:** `ssh mal-core-0` (read-only claude account). Restart units with `sudo -n systemctl --user -M ubuntu@ restart <mal-unit>`; `show`/`status`/`enable`/`disable` are NOT permitted. To read the runner's git SHA: `git -c safe.directory=/var/lib/mal/paper/forward-paper/src -C /var/lib/mal/paper/forward-paper/src rev-parse HEAD`. Code updates: ask the owner to have Helm fast-forward `/var/lib/mal/paper/forward-paper/src`. Helm exported the in-sample backfill to `/var/lib/mal/export/insample-backfill-20260922T00Z-20260925T07Z` (readable).
- **`mal-fast-0`:** control ubuntu units with `sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/$(id -u ubuntu) systemctl --user …` (walkers: `mal-fast-backfill`, `-b`, `-c`, all enabled). Claude's own timers use plain `systemctl --user`. Walker progress: `/var/lib/mal/backfill-fast{,-b,-c}/checkpoint.json` (`credits_used`, `hours[*].status`). Exploration data copies: `/home/claude/data/oracle-insample-2026-09-22_25` (sha256-verified) and `/home/claude/data/oracle-live-2026-09-25_27`. The OOM outage history is in the daily note.
- **Helm** owns ufw, sshd, cloudflared, Cloudflare Access, Oracle admin, and the cgroup/memory limits on both boxes. Ask through the owner.

## Pointers

- State: [LAB_STATE.md](../LAB_STATE.md) · Summary: [ARTIFACTS/SUMMARY.md](../ARTIFACTS/SUMMARY.md)
- Ledger: [docs/HOLDOUT_LEDGER.md](HOLDOUT_LEDGER.md) · DEC-014: [DEC/DEC-014-holdout-ledger-and-multiplicity.md](../DEC/DEC-014-holdout-ledger-and-multiplicity.md)
- Entry-model evidence: [ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md](../ARTIFACTS/lab/exploration-entry-model-b3-2026-09-28.md) and the audit comment on [#156](https://github.com/vaanai/MAL/pull/156)
- Session narrative: [ARTIFACTS/daily/2026-09-28-manager-session.md](../ARTIFACTS/daily/2026-09-28-manager-session.md)
