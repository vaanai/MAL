# Manager handoff 2026-09-30 (evening)

The owner is moving the manager session into **MiScusi**. Read this page first. Then read [docs/console-plan.md](console-plan.md), [docs/console-miscusi-requirements.md](console-miscusi-requirements.md), [LAB_STATE.md](../LAB_STATE.md), [CLAUDE.md](../CLAUDE.md), [CONSTITUTION.md](../CONSTITUTION.md), [docs/HOSTS.md](HOSTS.md) and [docs/HOLDOUT_LEDGER.md](HOLDOUT_LEDGER.md). Replace this page at the next handoff; don't append to it.

As of **2026-09-30T17:46Z**, `main` is at `2b51e70` (#172). Paper only. **Once the new session confirms it has taken over, the old session stands down:** no merges, no restarts, nothing scheduled.

## You are running inside MiScusi

The owner started you from MiScusi (Agents → Start a Claude session, Manager + Read the inbox) on mal-fast-0.
- **MiScusi tools:** use them for the inbox (the Console's "Send to Claude" notes arrive there, tagged `mal-manager`, and you pick them up at natural breaks), for jobs on mal-research-0, and for hand-offs.
- **Stand-down:** confirm to the owner when you've taken over. The previous session (started before MiScusi pairing, so it has no MiScusi tools) then stands down when the owner tells it to.
- **Tone:** talk to the owner in plain English, with how each thing moves MAL toward profit.

## A. EXP-011 is closed: NOT_DECIDABLE (2026-09-30T20:45Z)

The one-shot read ran once at 20:25:56Z, wrote its lock, and aborted on a missing sealed hour. No outcome was observed. The holdout is spent, and **it is never re-run** (the lock exists). The causes are walker data-integrity bugs: a backwards slot range seals empty hours, resumed hours get exact duplicate rows, and resumes probably lose held rows. Details are in [EXP-011's Result section](../EXP/EXP-011-migrate-entry-model-prereg.md) and the ledger.

What's next:
1. Review and merge the walker-fix PR (`claude/backfill-integrity`, adding `tools/backfill_verify.py`).
2. Read the exploration-pool duplicate census at `/home/claude/data/dup-census/census.{out,json}`.
3. Deduplicate the exploration pool into clean copies, preferably on research-0 under `/data/mal`, with sha256 manifests.
4. Re-run B3 exploration on clean data.
5. If it still clears its pre-stated screen, pre-register a new experiment on a fresh ≥6-day block. Walk that block with the fixed walker, preferably on research-0 (Helm places the Helius key), and run `backfill_verify` before any read.
6. EXP-009's block also has resumed hours. Its screen scorer must deduplicate first, via a pre-registered amendment.

## B. Console: where it stands

- **MAL-side contracts, all merged:**
  - #166 `result.v1` + tries log.
  - #167 data catalog + read guard (`tools/mal_catalog.py`; confirmation-oneshot needs a real `EXP-###`).
  - #168 status files + credit log. The claude timers `mal-status` (1 min) and `mal-status-core` (5 min) are **enabled on mal-fast-0** and write `~/data/status/*.json` and `~/data/credits/helius-credits.jsonl`.
  - #169 `data/console.json`, which the manager maintains: ladder, events, review windows. Blinding **fails closed** if it's unreadable. After the 10-05 review, set `review_windows: []` deliberately.
  - #171 job templates `explore_entry_filter` / `explore_exit` via `tools/mal_job.py`. The refusal path is complete. **The runners raise NotImplementedError.**
  - #170 / #172: guidebook docs 1–3.
- **Console repo `vaanai/mal-console` (private, clone at `~/mal-console`):** v1 is complete in code (PRs #1–#5).
  - A Fastify server with server-side blinding.
  - A React/Vite web app with 9 screens, including Run a test (DeepSeek drafts, 20/day, 3 running, $10/month, fail-closed limits) and Jobs.
  - It uses MiScusi's tokens and live updates over SSE.
  - A clickable sample-data preview: https://claude.ai/artifact/9Qgh1aBQxFZcKyBmU6ThLs. Screenshot QA: `~/venvs/shots/bin/python ~/venvs/shots/shoot.py <url> <prefix> <routes…>`.
- **LIVE since 2026-09-30 ~18:30Z at `console.tradervaan.com`**, on **mal-fast-0** for now (decided with the owner and Helm):
  - It runs as the claude user service `mal-console` on 127.0.0.1:8787, from the pinned worktree `~/apps/mal-console`, capped at 512M and 50% CPU.
  - Helm routed the hostname through the host tunnel, behind Access with the owner's email only.
  - Deploy steps are in `vaanai/mal-console` `deploy/README.md`. It **moves to research-0 when the data does, or before any live key lands on mal-fast-0.**
  - The OpenRouter key is in place at `~/.config/mal-console/openrouter.key`. **The MiScusi `mck_` key is still pending.** When the owner gives it, put it at `~/.config/mal-console/miscusi.key` (mode 600), then run `systemctl --user restart mal-console`. Jobs, the Claude stream, spend and Send-to-Claude then come alive.

## C. Next, in order

1. **After the EXP-011 read:** wire the two template runners.
   - They need `tools/exploration_entry_model_b3.py` / `tools/exploration_exits.py` to accept `size_sol`, `priority_fee_tier`, and a single-cell/day-subset entry point.
   - **Every lookback or buffer hour a runner reads must also pass `check_read`** (review note on #171).
   - Don't change behaviour for existing callers.
2. **MiScusi (the owner + Grokbot are setting it up now).** When the owner messages from MiScusi:
   - get the MAL space **Console key** (`mck_`, operate);
   - join research-0 to the MAL space;
   - define the two templates in MiScusi from `docs/contracts/job-templates.md`.
   - The MiScusi checklist is mostly built already. Gaps to raise with the owner's developer: fixed server-cost entry, explicit brain-down behaviour, and a real-box test of systemd job limits + the headless install.
3. **research-0 online.** The link from mal-fast-0 is live (Helm, 2026-09-30T18:55Z): `ssh mal-research-0`, `/data/mal`, details in [HOSTS.md](HOSTS.md). The next steps are:
   - data sync (rsync + sha256 manifests);
   - status files for research-0, and get fast/core status to research-0 (sync every minute, or run the collectors there);
   - deploy the Console there;
   - the OpenRouter key (Helm places it);
   - move the walkers (Helm places the Helius key) and all heavy jobs.
4. **After 2026-10-05:** a per-book paper P&L feed for the Paper screen. Build it only after the review instant, so nobody can peek (DEC-014).
5. **Owner quiz** on guidebook 1–3 once he has read them.

## D. Other dated items

- **Every day at 00:00:00Z:** the Oracle runner restarts. Check `/home/claude/reports/runner-restarts.jsonl` for `ok: true`, `head_sha d7485d2`. The daily review runs at 05:00Z.
- **2026-10-05T05:00:00Z kill review:** on a snapshot, run settle orphans → pressure stamp → `tools/kill_review.py --pressure-from-ms <2026-09-29T00:00:00Z ms> --holm-draws 10000`. Then `quant-proof`, then LAB_STATE. No new forward books before then.

## E. Paused (owner decision), not forgotten

- The EXP-009 screen scorer (walker 1 is complete at 156/156).
- Lane D (run it on research-0 with `chunk_plan`).
- Oracle lag spikes.
- The backfill has no retry on `IncompleteRead` (walker C restarted once, 15:58Z; the checkpoint resumed).
- The warm start / live-readiness track.
- #90: keep.

## F. Resources

**Helius, at 17:46Z:**

| Walker | Credits used |
| --- | --- |
| Walker 1 | 2,039,888 |
| Walker B | 848,039 |
| Walker C | 1,032,940 of its 1.5M cap |

That's about 3.92M in total, within the owner's ~20M headroom.

## Rules this project learned the hard way

1. **Memory on mal-fast-0:** one heavy job, ≤2 workers, bounded windows, whole-tree kill. Check `anon`, not `MemoryCurrent`.
2. **Docs describe merged state only.** Fix the data and the wording, never a rule.
3. **Holdout ledger:** only the manager assigns blocks. A block that's been seen can't become a confirmation holdout.
4. **Check ex-top-3 and the frozen feature list before calling anything a candidate.** The guidebook review caught B3's pre-ablation numbers quoted as post-ablation.
5. **Offline replicas mirror every live branch.**
6. **Builders stop at ~40 turns.** Commit their WIP yourself, then resume them with tight numbered steps.
7. **Run merges as separate commands.**
8. **Security review before merge for anything that gates data or limits.** Reviews on 2026-09-30 caught a spoofable exp_id, fail-open blinding and fail-open running limits.
