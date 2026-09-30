# What MAL needs from MiScusi

**For:** the owner's MiScusi developer. **From:** the MAL manager session, 2026-09-30.
This builds on MiScusi's own A1–A9 plan. Each item says **why MAL needs it**, so the developer can pick the simplest design that satisfies it. The priorities are MAL's: **P0** is needed before mal-research-0 goes live, **P1** is needed for Console v1, and **P2** is later. The MAL-side design is in [console-plan.md](console-plan.md).

## P0: servers and safe heavy jobs

1. **Headless server install** (A8). It runs as a proper system service on a root/headless Linux VPS and survives reboots. It also takes a one-line setup per server.
   *Why:* all three MAL boxes are headless servers, and the agents have to keep running with the owner's computer off.
2. **Machine health reports** (A8): memory **in real use (cgroup `anon`, not file cache)**, CPU, disk, drive health, and named services up or down. Plus alerts.
   *Why:* on 2026-09-29, cache-inflated memory numbers misled the manager, and two outages followed.
3. **Slots per machine** (A1), with a memory check before a new session or job is admitted. There's also a **machine rule: "no heavy jobs on mal-fast-0"**.
   *Why:* mal-fast-0 runs the live listeners (and later the live bot). Two concurrent replay jobs there caused an OOM and a reboot.
4. **Long compute jobs** (A4):
   - **Declared resources**: memory, cores, time limit. The job is admitted only if it fits, and **systemd enforces** the limits.
   - A **fixed reserve** that jobs can never use: ~16 GB on research-0, ~6 GB on mal-fast-0.
   - A **queue on disk** that survives reboots. Interrupted jobs are marked *interrupted*, not lost.
   - **The whole process tree is killed** on a limit, including multiprocessing workers that get reparented.
   - **Structured result wake-ups**: the job writes `result.json`, and that (not the logs) is what wakes the agent.
   - **Failure, OOM and timeout wake-ups** carrying the last 50 log lines and the reason.
   - **Priorities** (urgent / normal / background) and a **per-session cap**.
   - **Reproducibility record** for every job: git SHA, full command, config, data version.
   *Why:* this replaces MAL's hand-built memory guards, which failed once. Claude spends no tokens while a job runs for hours.
5. **Job roles**, passed to the job's environment and visible in the record: `exploration`, `confirmation-oneshot`, `ops`.
   *Why:* MAL's data catalog refuses reads the role doesn't allow. That's how the Console can't touch holdout data (MAL hard rule 1).
6. **Guardrails** (A7):
   - **Secret guard**, so no wallet or private keys are ever shown to agents or pushed.
   - **Protected paths** in the MAL repo: agents may change them only by PR from the manager session, and Console-originated jobs can never edit them. The paths: `docs/HOLDOUT_LEDGER.md`, `DEC/`, merged `EXP/*-prereg*.md`, `ops/claude-schedules/`, host-deploy scripts under `scripts/mal-core/`, and any future `live/`.
   - **A per-machine command allowlist** instead of approval pop-ups: `python*`, `uv*`, `git*`, the jobs tool, `rsync`.
   *Why:* this is a trading system, so the safety rules can't depend on agents behaving.
7. **One ledger writer.** Only the session tagged **manager** may edit `docs/HOLDOUT_LEDGER.md` or reserve data blocks. Other sessions *request* a block through a task.
   *Why:* parallel Claude sessions could claim or read the same untouched block, which would silently ruin the only honest test.

## P1: needed for Console v1

8. **A job API** the Console can call: submit (from a template name + parameters + role + resources), status, logs, result, cancel, pause, re-run, priority. Also **sweeps as one unit**: N settings, K at a time, one progress bar, one ranked result and one wake-up.
   *Why:* "Run a test" and the Jobs screen are thin layers on this.
9. **Sessions join as workers** (A2): pick up the next task by tag, report progress, submit results, and **wait for events without spending tokens**. Plus a **shared lab notebook**.
   *Why:* parallel research sessions on research-0 coordinate through this instead of through the owner.
10. **An inbox that never interrupts.** "Send to Claude" becomes a task tagged `mal-manager` that the manager session pulls at natural breaks. Replies come back on the same task.
    *Why:* it's the owner's channel to Claude, and it must never land in the middle of a running turn.
11. **A MAL section / project view**: MAL's machines, jobs, sessions, inbox, notebook and usage grouped together, hidden from the owner's friends.
    *Why:* MAL is now the main workload. Friends must never see trading or experiments.
12. **Usage and cost data**: Claude usage per 5-hour window (A6), DeepSeek spend, and per-job resource use, exposed so the Console's Spend screen can read it.
13. **Notifications** to the owner's phone: machine trouble, job failures, finished sweeps, questions from Claude. The owner chooses which he gets.
14. **An append-only action log** for everything done through MiScusi on MAL's behalf: who, what, when, and which data.
    *Why:* MAL hard rule 9. It's how we audit later that nothing bent the rules.

## P1: session handoffs and memory (the owner asked for this)

15. **Managed handoffs.** When a manager session's context gets large, MiScusi helps it write the handoff (the repo's `docs/HANDOFF.md` plus a memory note). It then starts the new session with the kickoff prompt, confirms the new session has taken over, and **stands the old one down**: no more merges or restarts, cron-like wake-ups cancelled, and only then archived.
    *Why:* the 2026-09-28 handoff left the old session alive, and it merged a PR after the new one took over. The 2026-09-29 handoff had to be redone by hand.
16. **Context and health per session**: the context-size meter, the last activity, and what it's waiting on.
17. **A memory view.** Read and edit the Claude memory notes for a project (`~/.claude/projects/<project>/memory/`): the index plus each note, with history.
    *Why:* the owner can see what the agents "remember" and correct it, without logging in to a server.

## P2: later

18. **Live-mode hooks.** A read-only status feed from the future live bot (which runs **outside** MiScusi and holds the only wallet key), plus a **stop** signal path the Console can trigger. MiScusi never holds live keys.
19. **Scheduled runs** (e.g. a nightly re-test of candidates) through the job system.
20. **Perforce** (Part B) is unrelated to MAL. There are no MAL requirements.

## Answers to MiScusi's open questions, from MAL's side

1. **Headless or live by default:** headless for research-0 workers. Remote Control stays for the manager session and for watching.
2. **The research box:** the per-machine command allowlist, with the secret guard and protected paths as the backstop.
3. **Protected paths:** the list in item 6.
4. **The experiment table:** not in MiScusi. It lives in the MAL Console, using MAL's own metrics.
