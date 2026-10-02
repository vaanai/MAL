# Manager handoff 2026-10-02 (manager on mal-research-0 → next manager)

Replace this page at the next handoff; don't append to it. Read it first, then:
- [LAB_STATE.md](../LAB_STATE.md)
- [CONSTITUTION.md](../CONSTITUTION.md)
- [DEC-015](../DEC/DEC-015-forward-paper-on-fast.md), [DEC-016](../DEC/DEC-016-exp012-forward-on-chain-hours.md) (with Amendments 1–2) and [DEC-017](../DEC/DEC-017-forward-secondary-family.md)
- [EXP-013 plan](../EXP/EXP-013-graduation-classifier-plan.md) (with Amendments 1–4)
- [docs/HOLDOUT_LEDGER.md](HOLDOUT_LEDGER.md)

`main` is at `d2056cb` (#249) plus this handoff PR. Paper only. The owner's goal is still real money by the end of October 2026: the owner's target, not evidence.

## State at 2026-10-02T19:00Z

**One forward book: EXP-012.**
- It is scored on chain-complete forward hours by the exact code that passed its one-shot read (DEC-016).
- Counting window `[2026-10-06T00, 2026-10-16T00)`. **One FINAL read** at about 2026-10-16T02Z with `tools/exp012_forward.py`. Before that, reports are INTERIM: counts only.
- **No peeking** (DEC-016 Amendment 2): nobody opens `rows.jsonl`, `OUT/scratch/*` or a FINAL report before the read. Only `score`, `report` and `export-decisions` (P&L-free) read them.
- A PASS supports only asking the owner for a small live trial, and only after both of these:
  - the latency/size sensitivity re-score at the measured fast-0 lag;
  - the runner-vs-scorer comparison (DEC-016 Am.1 §5).

**DEC-017 secondaries: none.**
- All three candidates failed their pre-stated screens on exploration data (#242): exit variants (nested LODO negative), the expanded-pool refit (fast-only screen FAIL), and the p80 threshold band (negative).
- k = 0 for the 10-16 read. The registration deadline 10-05T23:59Z effectively passes unused.

**Next independent signal: EXP-013, graduation-completion classifier (exploration stage).**
- The plan, screen and tries cap were fixed before any code: [EXP-013 plan](../EXP/EXP-013-graduation-classifier-plan.md), #243 and Amendments #245–#248.
  - Primary entry is slot+4.
  - Screen item 4a: all getBlock days pass. 4b: the August days alone pass.
  - It must stay positive at slot+8 and keep Jaccard with EXP-012 at most 0.5.
  - At most 3 tries.
  - **The screen runs exactly once**, on the 9-day pool plus every `explore-0814/wN` view verified by **2026-10-04T12:00Z**.
- PR1 (trigger, features, exit, guarded table builder) is merged as #244 (215 tests). **Nothing has run on real data.**
- PR2 (model + LODO) and PR3 (screens) are not started.
- If it passes: a pre-registration for a k = 1 one-shot read on the **backup block** `[2026-08-28T12, 2026-09-03T12)`. That block is verified (#58–#60), unread and reserved.
- Honest prior: about 20% or less.

**Feeds.**
- Live tapes miss trades during websocket reconnect episodes:
  - 10-01: fast 84.759% and Oracle 66.241% of chain;
  - quiet 10-02T06–08: fast 100.000%.
- The fast trial tape now runs `ca542c5` (#233) with **`--sockets 2`** (since 10-02T13:39:04Z, #234). Over 10-02T16–18 it caught **99.282%** of chain trades with 16 reconnects, while Oracle's single socket caught 82.062% (#249).
- DEC-015 §2.2: redundant sockets are the runner feed plan, with a daily coverage check against the forward walk.

**Runner track (live readiness).**
- The EXP-012 model gate in `tools/forward_paper.py` is merged (#228). The md5 decision-equivalence replay passed, A and B, on 3 h of Oracle tape.
- The fast-0 deploy kit is merged (#231): `scripts/mal-fast/`, with an observe unit, `mal-forward.slice` (5G/6G), the runner unit, a restart timer and installers with `--commit <sha>`.
- **Nothing is installed on fast-0.** The installer refuses before 2026-10-05T05:00Z except `--files-only`.

## In-flight MiScusi jobs (session MALsession1, all on mal-research-0)

| Job | Id | What | Notes |
| --- | --- | --- | --- |
| #71 | `j_XRdI7CrrMEa_JQ` | DEC-016 forward walk → `/data/mal/blocks/forward-1002` | Owner-approved 10-02. Since hour 10-02T15, 3 hours sealed, 0 issues, about 13.5k credits per hour. Holds one Helius slot at `--rps 8`. **168 h limit: resubmit the same command (`bash scripts/research/forward-walk.sh`, params `{"start":"2026-10-02T15"}`, resumable, 10080 min) before about 2026-10-09T15Z.** It is idempotent. |
| #45 | `j_78hqb5Pqn-wzbA` | expansion w2 `[08-24T12, 08-26T12)` | 23/48 at 18:51Z |
| #46 | `j_wu1EzqTU6IyCuw` | expansion w3 `[08-22T12, 08-24T12)` | 24/48 |
| #55 | `j_y8U5tq9aA77qwQ` | expansion w4 `[08-20T12, 08-22T12)` | 21/48 |
| #72 | `j_LZAzubRuOflZqw` | expansion w5 `[08-18T12, 08-20T12)` | after #55; 15/48 already sealed |
| #49 | `j_4XFKvo58yvSEJw` | expansion w6 `[08-16T12, 08-18T12)` | after #45 |
| #50 | `j_XYPA00Q8bIUERA` | expansion w7 `[08-14T12, 08-16T12)` | after #46 |
| #82–#87 | | verify, dedupe and clean-view for w2–w7 | each `after` its walker; uses `/data/mal/ops/verify-block.sh` |

- Walkers seal about 2.3 hours per wall hour. w2–w4 should finish around 10-03T06–08Z, and w5–w7 around 10-04T03–09Z, which is tight against EXP-013's 10-04T12Z cutoff.
- Only views that actually have `VIEW.sha256` by the cutoff go into the screen.

Outside MiScusi:
- **Fast tape trial unit** `mal-fast-trade-tape` on mal-fast-0 (ubuntu user unit, drop-in `trial.conf`, `--sockets 2`, output `/var/lib/mal/sealed/fast-trades-trial`, 2-day retention).
  - Rollback: copy back the backups in `/var/lib/mal/eng/observe.bak-69ff892-*`, then run daemon-reload and restart.
- `mal-status-research.timer` on mal-fast-0 stays.

## Next steps, in order

1. **Daily:** check #71's newest hours in `/data/mal/blocks/forward-1002/verify.jsonl` (issues must be `[]`).
2. **Daily, two-socket coverage check:** a MiScusi job on **mal-fast-0** (keep it under 2 GB; fast-0 refuses heavy jobs) running `tools.tape_coverage` against `--chain-dir /data/mal/blocks/forward-1002 --chain-ssh mal-research-0` over 2 complete hours. Copy the pattern from job #80. Write a short lab note if it fails or changes materially.
3. **As walkers finish:** #82–#87 verify them. Check each `/data/mal/ops/verify/explore-0814-wN-content.json` shows 0 flagged and 0 duplicates.
4. **EXP-013 PR2 and PR3** (builder, worktree):
   - the LightGBM model with LODO on the table from `tools/exp013_grad_table.py`;
   - the screen per the plan: 4a and 4b, slot+8, Jaccard, edge-day reporting, the 3-tries cap, and the tries log at an absolute path (`MAL_TRIES_LOG=/data/mal/ops/tries/tries.jsonl`, then copy to `data/tries.jsonl`).
   - Review both. Table builds before 10-04T12Z are debug only. **Run the screen once** after the cutoff, with a manifest of pinned view shas.
   - If PASS: write the EXP-013 pre-registration (backup block, k = 1), ask `quant-proof`, merge, then the one-shot read. If FAIL: close the family; don't re-tune.
5. **2026-10-05T05:00:00Z kill review** of the 9 Oracle books, on a snapshot:
   1. settle orphans;
   2. pressure stamp;
   3. run `tools/kill_review.py --pressure-from-ms <2026-09-29T00:00:00Z ms> --holm-draws 10000`;
   4. `quant-proof`;
   5. update LAB_STATE.

   Note as context Oracle's tape coverage (66.241% / 98.615% / 82.062% of chain in the three windows) and its lag breaches. After the review, set `review_windows: []` in `data/console.json`.
6. **After the kill review: the runner on fast-0** (DEC-015, DEC-016 §3):
   - build the x86_64 venv with the pinned `scripts/mal-fast/requirements-fast-forward.txt` (numpy 2.5.3 wheel availability on Python 3.12 is unverified);
   - `install-fast-observe.sh --commit <sha>` as ubuntu;
   - point `tape_dir` in `fast-forward-paper.json` at the two-socket tape dir;
   - `install-fast-forward-paper.sh --commit <sha>`;
   - start the runner and enable the restart timer;
   - 2-day lag probation;
   - record it all in LAB_STATE.

   The runner's paper rows are for the runner-vs-scorer comparison, using `tools/forward_exp012_replay.py` and the scorer's P&L-free `export-decisions`. They are not a second gate.
7. **About 10-16T02Z:** the EXP-012 FINAL read, then `quant-proof`, then latency/size sensitivity at the measured lag, then the runner comparison, then the owner.

## Decisions today (and why)

- **DEC-016 (owner-approved):** EXP-012's forward book is scored on getBlock forward hours with the read's own code. Why: the live tapes miss trades, and the runner can't run EXP-012 as tested (a fill-model difference, and the 0.05 SOL cap against 0.5).
- **DEC-016 Am. 1:** one read after 10 full days. About 750 trades gives enough power if the edge halves; a 5-day read would be a coin flip.
- **DEC-016 Am. 2:** no peeking at P&L files.
- **DEC-017 (`quant-proof`-revised):** secondaries are gatekept behind EXP-012, read with Holm only if EXP-012 passes, so the overall false-PASS rate stays 0.05. A variant's tested quantity is its increment over EXP-012.
- **Forward walk** at `--rps 8`, holding one of 4 Helius slots (#235). The walkers dropped to 3 slots.
- **Exit and threshold variants dropped.** The frozen exit and threshold already sit where extra trades stop paying (#241, #242).

## Gotchas found this session

- **fast-0 refuses heavy MiScusi jobs** (≥2 GB or ≥2 CPUs) since 10-02. Request 1.9 GB or less there.
- **research-0 has no SSH config to other hosts.** Steps that need fast-0-local data run as MiScusi jobs on fast-0. The fast-0 job user can SSH to mal-core-0 and mal-research-0, and results come back by scp to `/data/mal/ops/` (create the target dir first).
- **`tools/tape_coverage` exits 1 on a non-PASS verdict.** Don't use `set -e`; capture `RC=$?`.
- **No `/usr/bin/time` on research-0.**
- **MiScusi's reported "peak" can sit at the declared limit without an OOM** (file cache). Size tape-pass jobs at 20–48 GB, and don't cancel on peak alone.
- **The session job cap is 16.** Flock-waiting walkers count against it, so chain walkers with `after`.
- **"Pool A" / "fast pool" is the fast-box getBlock backfill**, not the live listener.
- **Ledger Hours cells must parse in `tools/mal_catalog.py`.** Run the catalog tests after any ledger table edit (#225).
- **Some combined shell commands get denied by the permission system:** `git worktree add` plus merge with `-c user.*`, and `rm -rf` in the scratchpad. Don't retry them in another form. Use a builder in its own worktree instead.
- **`gh pr edit` fails.** Use `gh api -X PATCH .../pulls/N -F body=@file`.

## Dated items

- **Daily 00:00:00Z:** Oracle runner restarts. Check `/home/claude/reports/runner-restarts.jsonl` for `ok: true` and `head_sha d7485d2`. The daily review is at 05:00Z.
- **2026-10-04T12:00Z:** EXP-013 view cutoff.
- **2026-10-05T05:00:00Z:** kill review (step 5).
- **2026-10-05T23:59Z:** DEC-017 registration deadline (k = 0).
- **2026-10-06T00:00Z:** EXP-012 forward clean clock.
- **About 2026-10-09T15Z:** resubmit forward walk #71.
- **About 2026-10-16T02Z:** EXP-012 FINAL read.

## Paused (owner decision), not forgotten

EXP-009 screen scorer; Lane D; Oracle lag spikes; backfill retry on `IncompleteRead`; #90 keep.

## Rules this project learned the hard way

1. **Memory on mal-fast-0:** one heavy job, at most 2 workers. Better: run heavy work on research-0.
2. **Docs describe merged state only.** Fix the data and the wording, never a rule.
3. **Holdout ledger:** only the manager assigns blocks, before sealing.
4. **Check ex-top-3 and the frozen feature list before calling anything a candidate.**
5. **Fix the screen, the tries cap and every design ambiguity before any data.** Never reinterpret a screen after seeing a result.
6. **Builders stop at about 40 turns.** Use tight numbered scopes, and give every PR a reviewer pass. Use `quant-proof` for anything about edge or multiplicity.
7. **Run merges as separate commands.**
8. **Security review before merge for anything that gates data, limits or installs units.**
9. **Verify data before reading it:** `backfill_verify` plus a sha256 manifest, every block, every time.
