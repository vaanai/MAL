# MAL Console + mal-research-0: plan

**Status:** agreed with the owner, 2026-09-30 (v2). The design decisions below are settled. Building starts with the next manager session. The MiScusi side is specified in [console-miscusi-requirements.md](console-miscusi-requirements.md).

## 1. What it is, in one paragraph

The **MAL Console** is a private website where the owner sees everything MAL is doing, in one friendly place: machines, what's running, where each idea stands, and paper trading. There he can **tinker** (run his own tests on exploration data), and **talk to Claude** without interrupting it. It's a **view first**. Claude stays fully autonomous and never hands tasks to the owner. **MiScusi** runs the agents and the jobs, and the Console sits on top of it for MAL only. **GitHub** stays the record of decisions. The new server **mal-research-0** (Ryzen 5950X, 16 cores, 128 GB, 2×1 TB NVMe) does all heavy research, so `mal-fast-0` stops running out of memory. The owner doesn't need MiScusi or the Console open for anything to run. Agents run as services on the servers, and the Console is just a page he opens when he wants to look.

## 2. Hard rules (agreed; not settings)

1. **The owner's and DeepSeek's tests run on exploration data only.** The job runner enforces this, not the UI. Every result shows **"variant N of M tried on this data"**. A good exploration result can become at most a **"propose pre-registration"** note to Claude. Only a pre-registered test on a fresh, never-read block confirms anything (see [HOLDOUT_LEDGER.md](HOLDOUT_LEDGER.md)).
2. **Paper P&L is blinded during review windows.** During a registered forward-paper review (the next one ends 2026-10-05T05:00Z), the Console shows how the runner is running (lag, fills, trade counts, errors) but not per-book P&L until the review instant (DEC-014, single read).
3. **The Console can't touch production.** The Oracle runner, the listeners and the walkers are read-only from the Console. Its actions become jobs on mal-research-0 through MiScusi.
4. **Claude is never interrupted.** "Send to Claude" queues a note that Claude picks up at a natural break (§6).
5. **Paper only.** No trading, wallet or X keys anywhere, including the Console, DeepSeek and MiScusi.
6. **GitHub is the source of truth** for decisions, pre-registrations and results write-ups.
7. **Heavy compute never runs on mal-fast-0.**
8. **Only the manager session assigns data blocks.** Parallel Claude sessions request blocks, and only the manager reserves them in the ledger. MiScusi enforces who may edit the ledger.
9. **Every action is logged.** Console buttons, DeepSeek jobs and "Send to Claude" notes all go to an append-only log (who, what, when, which data).

## 3. Research mode now, live mode later

The Console starts in **research mode**. **Live mode** is designed in from day one so it's an add-on, not a rebuild. Its data model already has room for:
- money at risk and live P&L;
- position and daily-loss limits;
- a kill switch;
- an approval flow of candidate → forward paper → tiny live → scale, with the owner's sign-off at each step.

The live bot itself will run **outside MiScusi**, most likely on mal-fast-0, which is closest to the chain feeds and holds the only wallet key once live is approved. The Console only **watches** it and can press **stop**. Research continues alongside it.

## 4. Screens (v1 unless marked)

| Screen | What the owner sees and does | Data source |
| --- | --- | --- |
| **Home** | Machine cards · what's running now + next scheduled events · **Edge ladder** (top ideas by stage) · **data blocks available / reserved / used** · spend this month · anything waiting on him (Claude's questions, proposals from his tests) | Everything below |
| **Machines** | mal-core-0 / mal-fast-0 / mal-research-0: memory in real use, CPU, disk, key services green or red, slots busy, alerts | MiScusi health + MAL status files |
| **Paper** | The forward runner's operations (lag, fills, trades today, restarts). Per-book P&L only outside review windows | Oracle runner files, restart log |
| **Edge ladder / Experiments** | Every idea with its stage: idea → exploring → candidate → pre-registered → confirming → passed/failed → forward paper → live-approved → retired. The gate is shown as a **checklist** under both fail models. He can add ideas, comment and vote | `EXP/`, the ledger, result files |
| **Run a test** | Plain English → DeepSeek fills a **template** → he confirms → the job runs on research-0 against **exploration data only** → the result card. "Send to Claude" / "propose pre-registration" | MAL templates + MiScusi jobs |
| **Jobs** | Running, queued and finished jobs, with memory, logs and results. Pause, cancel, re-run and priority | MiScusi jobs (MAL filter) |
| **Claude** | One stream: his notes to Claude, and Claude's findings, decisions and questions back | MiScusi tasks + MAL daily notes |
| **Data** | The block catalog: which hour ranges exist, who owns each, read status, and the walkers' progress | The MAL data catalog |
| **Spend** | Helius credits, server costs, DeepSeek and Claude usage (income later) | MiScusi usage + MAL credit log |
| Ask the lab (v1.5) | Questions to DeepSeek, answered with file citations and raw numbers | Lab notes, results |
| Search (v1.5) | Across experiments, results and decisions | GitHub + result files |

**Glossary (nice-to-have):** if it's clean to build, tapping a metric or stage name shows a one-line meaning drawn from the guidebook's glossary. The guidebook (§8) is the primary way the owner learns the screens.

## 5. How MAL judges an edge (what every result card shows)

It's not Sharpe or drawdown. Each card shows these, under **both** fail models (flat 15% and pressure scale 1):
1. **Net per trade after all fees**: mean and median, in % and in SOL.
2. **90% CI lower bound** of mean SOL per trade (bootstrap 1,000 draws, seed 1). Needs to be > 0.
3. **Ex-top-3 SOL**: the total with the 3 best trades removed. Needs to be > 0. This is the moonshot detector.
4. **Days positive / days**: at least 5 UTC days, with a majority positive.
5. **n trades**, the selected fraction, trades per day, and **SOL per day**.
6. **Fill rate** and **fill-conditional net**.
7. **By source and market segment.**
8. **Stage, data block used, and tries on this data.**
9. **Concentration**: the share of profit from the top 5 trades.

The gate checklist is exactly LAB_STATE's promotion gate.

## 6. Owner ↔ Claude

- **Send to Claude** creates a MiScusi task tagged `mal-manager`. The manager session pulls it at natural breaks (session start, after a merge, after a read). Nothing pushes into a running turn.
- **Claude → owner** is the same stream: findings, decisions and questions. Claude doesn't delegate work to the owner.
- **DeepSeek escalation rules** are limited to what Claude should act on:
  - an exploration result passes the exploration screen (both models, ex-top-3 > 0), which becomes a "propose pre-registration" note;
  - repeated job failures;
  - a machine in trouble.
- Accepted ideas become `EXP/` proposals or lab notes in GitHub, **written by Claude**.

## 7. DeepSeek (OpenRouter)

- **Allowed:**
  - read result files, lab notes, EXP files, the ledger and the catalog;
  - fill **template** parameters within their schema ranges;
  - submit **exploration** jobs to research-0: **≤ 20 per day, ≤ 3 at once**, with declared resources;
  - write explanations that always quote the raw numbers;
  - act as the owner's **tutor and quizmaster** on the guidebook.
- **Not allowed:**
  - write or run code;
  - read confirmation or holdout blocks;
  - pre-register, promote or merge;
  - see secrets;
  - reach mal-core-0 or mal-fast-0.
- **Budget:** hard OpenRouter cap of **$10/month**, a pinned model, and every call logged.
- **Key:** the OpenRouter key lives in one protected file on research-0, readable only by the Console service. Helm places it.

## 8. Guidebook + quiz (built alongside v1)

Three short documents, written by the agent that builds the Console so they match the real screens:
1. **How MAL finds an edge.** The trade tape, the honest simulator, fees and slippage, the two fail models, and the promotion gate.
2. **How we avoid fooling ourselves.** Exploration vs confirmation, the holdout ledger, the winner's curse and "tries", ex-top-3, review blindness, and why good exploration results can still fail.
3. **Using the Console.** Every screen and every button: what it does and what it can't do. What DeepSeek is allowed to do. What the owner should do when something looks off.

After reading them, the owner is quizzed (by the agent or by DeepSeek) until he's confident. Every guidebook has a glossary, which is also what feeds the optional tap-for-meaning.

## 9. MAL-side contracts (small PRs, needed anyway)

1. **A standard `result.json`** from every exploration and scoring tool. It records: the tool, git SHA, command, config, the data blocks read (hour ranges + ledger owner), the §5 metrics per fail model, gate ticks, stage, runtime, and peak memory.
2. **Job templates**: parameterized entry points with a JSON schema of allowed knobs and ranges. First two:
   - `explore_entry_filter`: trigger, model/threshold, exit, size, priority, market-cap band, exploration days;
   - `explore_exit`: the exit family and its parameters.
3. **A data catalog** (`data/catalog.json`), generated from the ledger and the walker checkpoints. The job runner refuses any read the catalog doesn't allow for the job's role.
4. **Status files**: `/var/lib/mal/status/<host>.json`, one per box, updated every minute (MAL services, walker progress, runner lag, last restart).
5. **A tries log** (`data/tries.jsonl`): one line per exploration run.
6. **A credit log**: Helius credits per job, for the Spend screen.

## 10. mal-research-0

- **Runs:** all exploration and scoring jobs, feature builds, training and sweeps, plus the **backfill walkers** (4–6 in parallel if Helius limits allow, which turns a 6-day block into less than a day; Helm places the Helius key there). It also runs parallel Claude sessions (MiScusi slots) and hosts the **Console** (behind Cloudflare Access, owner's email only; Helm).
- **mal-fast-0 keeps:** the live listeners, the manager session and its timers, and MiScusi's brain. Later, the live bot.
- **Oracle keeps:** the forward-paper runner, the trade tape, and the collectors.
- **Disks:** 2×1 TB NVMe, already mirrored (RAID1) by the provider.
- **Data sync:** nightly and on-demand `rsync` of the sealed hour files listed in the catalog, with sha256 manifests. Confirmation blocks are **locked**: only the one-shot scorer role can read them.
- **Resources:** declared-resource jobs with a fixed ~16 GB reserve. The 2026-09-29 lessons stay: bounded windows, rows streamed to disk, whole-tree kill.
- **Failure isolation:**
  - if the Console is down, research continues;
  - if MiScusi is down, queued jobs wait and running jobs finish;
  - if research-0 is down, the paper runner and listeners are unaffected.

## 11. Build order

1. **MAL contracts** (§9), on the current servers.
2. **research-0 online:** MiScusi server install, health, slots and jobs; RAID1; data sync; walkers moved; heavy jobs moved.
3. **Console v1:** Home, Machines, Paper (ops + blinded P&L), Edge ladder/Experiments, Run a test (templates + DeepSeek), Jobs, Claude stream, Data, Spend, basic notifications. **Plus guidebook docs 1–2** written in parallel.
4. **Guidebook doc 3 + quiz** once the screens are real.
5. v1.5: Ask the lab, search, sweeps UI, the glossary tap-for-meaning.
6. Live mode, after a candidate passes forward paper and the owner approves live.

**Hosting (2026-09-30, owner + Helm):** v1 runs on **mal-fast-0** for now, as the `claude` user service `mal-console`. It binds 127.0.0.1:8787 only, with MemoryMax 512M and CPUQuota 50%. `console.tradervaan.com` routes to it through the host's Cloudflare Tunnel, behind Access with the owner's email only. The keys sit in mode-600 files under `~/.config/mal-console/`. This is light, so it doesn't break rule 7: the tests it submits still run on research-0. **It moves to mal-research-0 when MAL's data moves there, or before any live wallet key lands on mal-fast-0, whichever comes first.**

**Stack:** a separate repo `vaanai/mal-console`, in the same language and framework as MiScusi (TypeScript/Node), so the same agents maintain both. It reads MAL through the contracts in §9.
