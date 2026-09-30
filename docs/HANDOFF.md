# Manager handoff 2026-09-30

The owner is starting a new manager session. **Your main job is to build the MAL Console with the owner.** First, run one time-boxed research read (item A). Read this page first, then [docs/console-plan.md](console-plan.md), [docs/console-miscusi-requirements.md](console-miscusi-requirements.md), [LAB_STATE.md](../LAB_STATE.md), [CLAUDE.md](../CLAUDE.md), [CONSTITUTION.md](../CONSTITUTION.md), [docs/HOSTS.md](HOSTS.md) and [docs/HOLDOUT_LEDGER.md](HOLDOUT_LEDGER.md). The previous sessions' narrative is in [ARTIFACTS/daily/2026-09-28-manager-session.md](../ARTIFACTS/daily/2026-09-28-manager-session.md). Replace this page at the next handoff; don't append to it.

As of **2026-09-30 ~07:30Z**, `main` at `b9ddb3c` (#164) plus this handoff PR. Paper only.

## Where we are

- **Research.** The frozen migrate-direct cell is dead. Exits and fee tiers are dead ends. **Entry selection is the lever.** The learned entry model (B3, #156) survived a leakage audit. Its one confirmation test, **EXP-011**, is **pre-registered and frozen** (#164):
  - model md5 `eb2189343fe08640345d75eb17363e32`;
  - threshold `0.8012473581008048`;
  - an in-sample fixed-threshold preview of pressure +4.92% (CI lo +3.36%), 9/9 days. This is **not evidence**; the holdout is.
- **The owner's new priority (2026-09-30):** a **MAL Console**, a private website where he sees everything, tinkers with tests on exploration data, and talks to Claude without interrupting it. It runs on MiScusi (his agent platform) and a new heavy-compute server, **mal-research-0** (Ryzen 5950X, 16 cores, 128 GB, 2×1 TB NVMe in RAID1). Grokbot is doing the base server setup, and the owner will connect it to MiScusi with you. The design is **agreed**: [docs/console-plan.md](console-plan.md).

## A. Time-boxed first: the EXP-011 one-shot read (~2026-09-30T19–20Z)

Walker C (`mal-fast-backfill-c`) finishes the holdout block around then. Walker B is already complete.

1. Check: `python3 -c "import json;c=json.load(open('/var/lib/mal/backfill-fast-c/checkpoint.json'));print(sum(v.get('status')=='sealed' for v in c['hours'].values()),'/72')"`. It needs 72/72.
2. `cd ~/MAL && git pull --ff-only && python3 -m tools.exp011_score --dry-run-preconditions`. It must print OK. Otherwise it refuses, reading nothing and writing no lock.
3. Check memory before the heavy step: `grep ^anon /sys/fs/cgroup/user.slice/user-1002.slice/memory.stat` (< 3 GB). **Nothing else heavy may run.** The scorer uses 2 workers with bounded windows (peak ~9 GB total for a job of this size).
4. `python3 -m tools.exp011_score` **once**. It writes the lock `/home/claude/data/exp011/HOLDOUT_READ.lock` and the report `/home/claude/data/exp011/holdout_report.{md,json}`. The first line is `VERDICT: PASS|FAIL`.
5. `quant-proof` reviews the report before any sentence says it made money. Record the verdict in `EXP/EXP-011-migrate-entry-model-prereg.md` (a results section), LAB_STATE and the ledger (block status: "read once"). Tell the owner in plain English.
6. **PASS** earns a forward-paper book **after** the 2026-10-05 kill review, never live directly. **FAIL** kills EXP-011, with no second read. Either way, the Console work continues.

## B. The main job: build the MAL Console

Read [console-plan.md](console-plan.md) end to end. The decisions in it are the owner's, so don't reopen them. Key ones:
- The Console is **view-first**, and Claude stays autonomous. Claude doesn't delegate tasks to the owner.
- Two **hard rules** that are not settings: the owner's and DeepSeek's tests read **exploration data only** (enforced at the job layer), and **paper P&L is blinded during review windows**.
- **DeepSeek via OpenRouter** is the owner's assistant: ≤20 jobs a day, ≤3 at once, $10/month cap, templates only, and it's also his tutor and quizmaster.
- A **separate repo** `vaanai/mal-console`, in MiScusi's language (TypeScript/Node).

Order (§11 of the plan):
1. **MAL-side contracts first, in this repo** (§9 of the plan): the standard `result.json`, the first two job templates (`explore_entry_filter`, `explore_exit`), the data catalog built from the ledger, per-box status files, the tries log, and the credit log. These are small PRs and are useful even without the Console.
2. **Give the owner the MiScusi checklist.** [docs/console-miscusi-requirements.md](console-miscusi-requirements.md) is already written. Go through it with him and adjust anything that turned out different, so he can pass it to his MiScusi developer. §1 (separation: MAL as a private project) and P0 come before research-0 goes live.
3. **Bring mal-research-0 online with the owner:** the MiScusi install, health, slots and jobs; data sync (rsync with sha256 manifests); move the backfill walkers there (Helm places the Helius key); move all heavy jobs there. **mal-fast-0 keeps only its listeners, the manager and its timers.**
4. **Console v1:** Home, Machines, Paper (ops + blinded P&L), Edge ladder/Experiments, Run a test (templates + DeepSeek), Jobs, the Claude stream, Data, Spend, and basic notifications.
5. **Guidebook + quiz:** three documents (how MAL finds an edge, how we avoid fooling ourselves, using the Console) written against the real screens. Then quiz the owner, or have DeepSeek do it, until he's confident.

## C. Other dated items

- **Every day, 00:00:00Z:** the Oracle runner restarts on its own. Check the new line in `/home/claude/reports/runner-restarts.jsonl` (`ok: true`, `head_sha d7485d2`). The daily review runs at 05:00Z into `/home/claude/reports/daily-review/`.
- **2026-10-05T05:00:00Z kill review:** a single read on a **snapshot** of Oracle's `positions.jsonl`, in this order:
  1. `tools/forward_paper_settle_orphans.py`
  2. `tools/forward_paper_pressure_stamp.py`
  3. `tools/kill_review.py --pressure-from-ms <2026-09-29T00:00:00Z in ms> --holm-draws 10000`
  Then `quant-proof` reviews it, and you record it in LAB_STATE. No new forward books before then.

## D. Paused (owner decision, 2026-09-30), not forgotten

- **The EXP-009 screen:** walker 1 is floored at 2026-09-15T12. A scorer still needs to be built per its pre-reg.
- **Lane D** (early bonding-curve entry filter, branch `claude/explore-early-entry-model`): it needs `chunk_plan` bounded windows. Run it on research-0.
- **Oracle runner lag spikes** of 5–7.5 s (~14 per day, a falling trend). The cause is unknown. Don't change the runner before 2026-10-05.
- **The backfill has no retry on Helius `IncompleteRead`.** The process exits and systemd restarts it from its checkpoint, so no data is lost. It's a small fix in `tools/pump_history_backfill.py`, to be deployed when the walkers next restart.
- Restart-neutral warm start (PR 2) and the live-readiness track follow a confirmed candidate.
- [#90](https://github.com/vaanai/MAL/pull/90): keep.

## E. Resources, as of 2026-09-30 ~07:00Z

- **Helius:** 3,565,824 credits used across the walkers at 07:04Z. Walker 1: 1,962,678 of its 2.3M cap (149/156 hours). Walker B: 848,039, done (72/72). Walker C: 755,107 of its 1.5M cap (42/72) (raised from 1.1M, because its hours cost ~18.6k each). This is within the owner's ~20M headroom. Report credits per job.
- **Exploration data on mal-fast-0:** `/home/claude/data/oracle-insample-2026-09-22_25` (sha256-verified), `/home/claude/data/oracle-live-2026-09-25_27`, and the EXP-011 training table `/home/claude/data/exp011/run-20260930b/table.jsonl` (md5 `03a85170…`). Move them to research-0 when it's up.

## Rules this project learned the hard way

1. **Memory on mal-fast-0** (22 GB, no swap; claude slice hard cap 15G). Replay workers use 3–5 GB each. Run **one heavy job at a time, ≤2 workers, bounded windows** (`chunk_plan`, 12 h home + 24 h buffer), stream rows to disk, and run it under `tools/exp011_rss_monitor.sh`-style **tagged whole-tree kill**. Check real use through `anon`, not `MemoryCurrent`. Never run heavy jobs unattended overnight. Better still, run them on research-0.
2. **Docs describe only what is merged.** The owner's reviewers (Lyra, Grokbot) audit every merge. Fix the data and the wording, never a rule.
3. **Holdout ledger:** the owner row is written before a walker seals any hour. Timestamps come from `date -u`. A block that has already been seen can't become a confirmation holdout. Only the manager assigns blocks.
4. **Check ex-top-3 before calling anything a candidate.** Also check every "feature" claim against the frozen feature list (EXP-011's pre-reg initially said it used no trailing-window features, and it did).
5. **Offline replicas of live logic** mirror every live branch, for every book kind.
6. **Builders stop at ~40 turns.** Commit their WIP yourself, and resume them with tight numbered steps, or start a fresh builder.
7. **Run merges as separate commands.** Don't chain them with `set -e`.
8. **A one-shot test gets a `quant-proof` review of train/score equivalence before its read** (EXP-011's review caught an unpinned `num_threads`; re-freezing proved it byte-identical).

## Host how-to

See the "Host how-to" in [HOSTS.md](HOSTS.md) and the memory notes. The verified commands:
- **Oracle:** `ssh mal-core-0`, which is read-only. `sudo -n systemctl --user -M ubuntu@ restart <mal-unit>` works; `show`, `status`, `enable` and `disable` are not permitted.
- **Reading the runner's SHA:** `git -c safe.directory=/var/lib/mal/paper/forward-paper/src -C /var/lib/mal/paper/forward-paper/src rev-parse HEAD`.
- **Fast-box ubuntu units:** `sudo -u ubuntu XDG_RUNTIME_DIR=/run/user/$(id -u ubuntu) systemctl --user …`. That covers the walkers `mal-fast-backfill`, `-b` and `-c`, all enabled, with unit files in `/home/ubuntu/.config/systemd/user/`, which are **not in git**.
- **Helm** owns ufw, sshd, cloudflared, Access, Oracle admin and the memory limits. Ask through the owner.
