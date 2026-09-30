# What MAL needs from MiScusi

**For:** the owner's MiScusi developer. **From:** the MAL manager session, 2026-09-30 (v2).

**How to use this list.** MiScusi is a general platform, and the owner's friends use it too. MAL (a paper-trading research lab for Pump.fun meme coins, heading toward live trading) is now its most important workload, so it needs a **private, walled-off space** inside MiScusi, plus a handful of capabilities that are generic but MAL-hardened.
- Build **§1 first**, then **P0 → P1 → P2**.
- Each item has **Why** (what goes wrong without it) and **Done when** (a concrete check).
- Build it generically wherever you can: another private project should be able to use the same features. Keep MAL's specific rules in MAL's project settings, not hard-coded.
- The MAL-side design this plugs into is [console-plan.md](console-plan.md): a separate MAL Console website that calls MiScusi's job API.

---

## §1. Separation: MAL as a private project (build first)

1. **Private project/workspace "MAL".** Only the owner can see or act on it. Friends never see its machines, sessions, jobs, notebook, inbox, usage or logs, and it never appears in their lists, search or notifications.
   **Why:** it's a trading system. Friends must not see strategies or results, and MAL mustn't clutter their MiScusi.
   **Done when:** a friend account's UI and API return nothing about MAL, including in global search and the activity feed.
2. **Dedicated machine pool.** `mal-research-0`, `mal-fast-0` and `mal-core-0` belong to the MAL project. Other projects' jobs and sessions are **never** scheduled on them, and MAL work never lands on a friend's machine.
   **Why:** mal-fast-0 runs live market listeners (and later the live bot), and research-0 is sized for MAL's heavy jobs.
   **Done when:** submitting a friend-project job while pinned to a MAL machine is refused, and so is MAL work going to a non-MAL machine.
3. **Separate secrets, notebook, inbox, usage accounting and action log** per project. No cross-project reads.
   **Done when:** MAL's usage and cost reports include only MAL work, and friends' reports include none of it.
4. **Project-level policy settings**, used by the items below: protected paths, the command allowlist, machine rules, job roles, and who may edit the ledger. These are **settings on the MAL project**, not global behaviour.
   **Done when:** changing MAL's policy leaves other projects unaffected.

## P0: before mal-research-0 goes live

(The server's disks are already RAID1. The owner's other agent, Grokbot, does the base OS setup.)

5. **Headless server install.** MiScusi's agent runs as a proper **system service** on a root/headless Linux VPS, starts at boot, and has a one-line setup.
   **Why:** all MAL machines are headless servers, and the agents must run with the owner's computer off.
   **Done when:** after a reboot of research-0, the MiScusi agent is back online with no login.
6. **Honest machine health.** Report **memory in real use (cgroup `anon`, not page cache)**, CPU, disk, drive health, and named service states (MAL provides the list per machine), plus alerts.
   **Why:** on 2026-09-29, cache-inflated memory numbers misled the manager, and the box OOM'd twice.
   **Done when:** health shows `anon` next to total memory, and an alert fires when a named service is down.
7. **Slots and machine rules.** Parallel-session slots per machine, a memory check before admitting work, and a rule: **no heavy jobs on mal-fast-0.**
   **Done when:** a heavy job targeting mal-fast-0 is refused, and research-0 shows "N/M slots busy".
8. **Long compute jobs**:
   - **declared** memory, cores and time limit; admitted only if it fits, **enforced by systemd**;
   - a **fixed reserve** jobs can't use: ~16 GB on research-0, ~6 GB on mal-fast-0;
   - a queue **on disk** that survives reboots (interrupted jobs are marked *interrupted*, and resumable ones restart);
   - the **whole process tree killed** on a limit, including multiprocessing workers reparented to systemd;
   - wake-up with **`result.json`** (not logs);
   - **failure, OOM and timeout wake-ups** with the reason plus the last 50 log lines;
   - priorities (urgent / normal / background) and a per-session cap;
   - a **reproducibility record**: git SHA, full command, config, data version.
   **Why:** MAL's hand-built memory guards failed once and took the box down.
   **Done when:** a job that exceeds its memory is killed along with all its children, the agent is woken with the reason, and nothing else on the box is affected.
9. **Job roles:** `exploration`, `confirmation-oneshot`, `ops`, passed to the job environment and stored in its record. MAL's code uses the role to decide which data a job may read.
   **Why:** it's how the Console and DeepSeek can never read MAL's untouched test data.
   **Done when:** the role is visible in the job record and in the job's environment.
10. **Guardrails (MAL project policy):**
    - a **secret guard** that redacts wallet or private-key-like strings from what agents see and blocks pushes that contain them;
    - **protected paths** in the MAL repo, editable only by PRs from the **manager** session and never by Console-originated jobs: `docs/HOLDOUT_LEDGER.md`, `DEC/`, merged `EXP/*-prereg*.md`, `ops/claude-schedules/`, host-deploy scripts under `scripts/mal-core/`, and any future `live/`;
    - a **per-machine command allowlist** instead of approval pop-ups: `python*`, `uv*`, `git*`, `rsync`, the jobs tool.
    **Done when:** a non-manager session's commit touching a protected path is rejected.
11. **One ledger writer.** Only the session tagged **manager** may edit `docs/HOLDOUT_LEDGER.md` or reserve data blocks. Other sessions request a block with a task to the manager.
    **Why:** two parallel sessions claiming or reading the same untouched block would silently ruin the only honest test.
    **Done when:** a worker session's ledger edit is blocked and it's told to file a request instead.

## P1: needed for the MAL Console v1

12. **A job API** the Console calls (see the interface below): submit from a template name + parameters + role + resources; status, logs, result, cancel, pause, re-run, re-prioritise; and **sweeps as one unit** (N settings, K at a time, one progress bar, one ranked result, one wake-up).
13. **Sessions as workers:** pick up the next task by tag, report progress, submit results, **wait for events without spending tokens**, and a shared **lab notebook**.
14. **An inbox that never interrupts:** "Send to Claude" from the Console becomes a task tagged `mal-manager`. The manager session pulls it at natural breaks, and replies return on the same task.
15. **A MAL section in MiScusi's own UI** (inside the private project): MAL's machines, sessions, jobs, inbox and notebook in one place.
16. **Cost data for the Console's Spend panel:** Claude usage per 5-hour window, DeepSeek/OpenRouter spend (if calls go through MiScusi), per-job resource use, and a place to enter fixed monthly server costs (e.g. research-0 at $100/mo). MAL provides its own Helius-credit log, and the Console combines the two.
17. **Notifications** to the owner's phone: machine trouble, job failures, finished sweeps, questions from Claude. The owner picks which.
18. **An append-only action log** of everything done in the MAL project: who, what, when, which machine, and which data.
19. **Managed handoffs:** help the manager session write its handoff (the repo's `docs/HANDOFF.md` plus a memory note), start the new session with the kickoff prompt, confirm the new session has taken over, then **stand the old one down**. That means no merges or restarts, its scheduled wake-ups cancelled, and only then archive it.
    **Why:** in the 2026-09-28 handoff the old session stayed alive and merged a PR. In the 2026-09-29 one, the handoff had to be redone by hand.
    **Done when:** after a handoff, the old session can't act and has nothing scheduled.
20. **Session health:** the context-size meter, last activity, and what it's waiting on.
21. **A memory view:** read and edit a project's Claude memory notes (`~/.claude/projects/<project>/memory/`, the index plus notes), with history.
22. **Graceful failure:** if MiScusi's brain is down, running jobs finish and queued ones wait. The Console shows "MiScusi unreachable", not wrong data. Nothing MiScusi does can stop the Oracle paper runner or the live listeners.

## P2: later

23. **Live-mode hooks:** a read-only status feed from the future **live bot**, which runs **outside** MiScusi and holds the only wallet key, plus a **stop** signal the Console can trigger. MiScusi never holds live keys.
24. **Scheduled runs** through the job system (e.g. a nightly re-test of candidates).

## The interface the Console will call (a sketch; exact shape is up to MiScusi)

- `POST /projects/mal/jobs` with `{template, params, role, machine?, resources{mem_gb, cores, max_minutes}, priority, sweep?{grid, concurrency}}` → `job_id`
- `GET /projects/mal/jobs?state=…` · `GET /jobs/{id}` (status, record, `result.json`, log tail) · `POST /jobs/{id}/{cancel|pause|resume|rerun|priority}`
- `GET /projects/mal/machines` (health including `anon` memory, slots, services, alerts)
- `POST /projects/mal/tasks` (`tag=mal-manager`, a body from "Send to Claude") · `GET /projects/mal/tasks?tag=…` (the stream)
- `GET /projects/mal/usage` (Claude, DeepSeek, per-job resources, fixed costs)
- `GET /projects/mal/actions` (the action log)
- Auth: the Console runs on research-0 behind Cloudflare Access (Helm). Its MiScusi calls use a **project-scoped token** that can only act on the MAL project, with nothing else.

## What MiScusi must NOT do for MAL

- Hold or pass wallet, trading or X keys. Run or control the live bot beyond the P2 stop signal.
- Schedule heavy work on mal-fast-0, or any non-MAL work on MAL machines.
- Show MAL anything to other users.
- Build a MAL experiment or strategy table. That lives in the MAL Console, using MAL's own metrics.

## Answers to MiScusi's open questions (MAL's side)

1. **Headless or live by default:** headless for research-0 workers. Remote Control stays for the manager session and for watching.
2. **The research box:** the per-machine command allowlist (item 10), with the secret guard and protected paths as the backstop.
3. **Protected paths:** item 10.
4. **The experiment table:** not in MiScusi (see above).
