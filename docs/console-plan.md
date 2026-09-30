# MAL Console + mal-research-0: plan

**Status:** proposal, 2026-09-30. Written by the MAL manager session from the owner's feature list (drafted with Deck) and MiScusi's A1–A8 plan. Nothing here is built yet. The owner approves the order and the decisions in §9.

## 1. Why, in one paragraph

The owner wants to see everything MAL is doing in one friendly place: machines, what's running, the best edge, paper trading. He wants to tinker (run tests, leave ideas, steer) without spending Claude's usage or interrupting Claude. And the new server, **mal-research-0** (Ryzen 5950X, 16 cores, 128 GB, 2×1 TB NVMe), should take over all the heavy research, so the fragile `mal-fast-0` stops running out of memory. The Console is a MAL-only view and control layer. **MiScusi** runs the agents and the jobs. **GitHub** stays the record.

## 2. The rules the design must obey (non-negotiable, from how MAL actually works)

1. **The Console can't become a p-hacking machine.** MAL's edge claims only mean something because of the holdout ledger ([docs/HOLDOUT_LEDGER.md](HOLDOUT_LEDGER.md)): every block of historical hours has one owner, and confirmation blocks are read once. So:
   - Every test the owner or DeepSeek runs may read **exploration-pool hours only**. That's enforced in the job runner, not in the UI.
   - Every run is counted. Every result shows **"variant N of M tried on this data"** next to the number (the winner's curse, made visible).
   - A good exploration result becomes, at most, a **"propose pre-registration"** button. Only a pre-registered test on a fresh block can confirm anything.
2. **Kill-review blindness.** During a registered forward-paper review window (the next one ends 2026-10-05T05:00Z), the Console shows paper **operations** (lag, fills, trade counts, errors) but **not per-book P&L** until the review instant. This is a rule, not a toggle (DEC-014 single read). After the read, everything unblinds.
3. **Nothing in the Console can touch production.** The Oracle runner, the listeners and the walkers are read-only from the Console. Actions become jobs on mal-research-0 through MiScusi. There are no restart buttons for the paper runner in v1.
4. **Claude is never interrupted.** The owner's notes and flags go into an inbox that Claude reads at natural breaks (§6).
5. **Paper only, no trading, wallet or X keys anywhere.** That includes the Console, DeepSeek and MiScusi.
6. **GitHub is the source of truth.** The Console reads EXP files, the ledger, LAB_STATE and lab notes. It doesn't keep its own copy of decisions.
7. **Heavy compute never runs on mal-fast-0 again.** MiScusi enforces this with a machine rule (A1/A7/A8).

## 3. What the Console shows and does (mapped to Deck's list)

| Area | v1? | Data source | Notes / changes to Deck's list |
| --- | --- | --- | --- |
| **Home** | ✅ | See rows below | Machine health · what's running · the **Edge ladder** (below) · waiting on you · quick actions |
| **Machines** (A) | ✅ | MiScusi A8 health reports + A1 slots | Plus the MAL services on each box (runner, listeners, walkers) from a small status file per box (§5) |
| **Paper view** (B) | ✅ (ops only) | Oracle `runner-status.json`, `mem-census.jsonl`, `health.jsonl`, `/home/claude/reports/runner-restarts.jsonl` | Per-book P&L is **blinded during review windows** (rule 2). Strategy-vs-replay drift comes later |
| **Edge ladder** (replaces "best edge") | ✅ | EXP files, ledger, lab notes, `holdout_report.json`, `kill_review` output | See §4. One number can't tell you how strong an edge is; its stage does |
| **Experiment board** (C) | ✅ | `EXP/*.md` front matter + the ledger + result JSONs | Statuses: idea → exploring → candidate → **pre-registered** → confirming → passed/failed → forward paper → live-approved → retired. The owner can add ideas and comments, and vote |
| **Run a test** (D) | ✅ (templates only) | Job templates in MAL (§5), run through MiScusi A4 | DeepSeek fills a template from plain English. The owner confirms. The job runs on research-0 against **exploration hours only** |
| **Jobs** (E) | ✅ | MiScusi A4, filtered to MAL | Pause, cancel, re-run and priority pass through to MiScusi |
| **Owner ↔ Claude** (F) | ✅ | MiScusi tasks + a notes file in GitHub | See §6 |
| **Ask the lab** (G) | v1.5 | DeepSeek over lab notes, results and the ledger | Its answers cite files and always show the raw numbers |
| **Search** (H) | v1.5 | Index of `ARTIFACTS/`, `EXP/`, `DEC/`, result JSONs | |
| **Notifications** (I) | v1 (basic) | MiScusi notification center | Machine down, a job failed, a sweep done, a new ladder step, a question from Claude |
| **Settings** (J) | ✅ | Console config | The escalation rules, DeepSeek's cap and model, which views are shown. **Rules 1–3 are not settings** |
| **Secrets vault** (K) | ❌ hold | none | MAL is keyless by design. At most an inventory: secret name, where it lives, who uses it, never values. Helm decides |

### Home page, concretely

- **Machines:** one card each for mal-core-0, mal-fast-0 and mal-research-0, with memory (in real use, not cache), CPU, disk, key services green or red, and slots busy.
- **Right now:** running and queued jobs with ETAs, walkers' progress to their blocks, the paper runner's lag and today's trade count, and the next scheduled events (daily restart 00:00Z, daily review 05:00Z, review instants).
- **Edge ladder:** the top 3 hypotheses by stage, each with its gate checklist (§4).
- **Waiting on you:** approvals, Claude's questions, DeepSeek escalations, pre-registrations proposed from your tests.
- **Quick actions:** "Try a variation of the top candidate", "Re-run", "Add an idea", "Send to Claude".

## 4. How MAL judges an edge (the metrics the Console uses)

This is the owner's question 2. It's not Sharpe or drawdown. Every result card shows these, under **both** fail models (flat 15% and pressure scale 1):

1. **Net SOL per trade after all fees**: mean and median, in % of size and in SOL.
2. **90% CI lower bound** of mean SOL/trade (bootstrap 1,000 draws, seed 1). Passing needs > 0.
3. **Ex-top-3 SOL**: total with the 3 best trades removed. Passing needs > 0. This is the moonshot detector (it killed the trailing exit).
4. **Days positive / days**: at least 5 UTC days with a majority positive.
5. **n trades**, **selected fraction** (for filters), **trades per day**, and **SOL per day** (the scale).
6. **Fill rate** and **fill-conditional net** (does the edge come from price, or from dodging misses?).
7. **By source / segment**: fast box vs Oracle, and the market-cap band.
8. **Stage and data used**: exploration, pre-registered or confirmed; which ledger block; **tries on this data**.
9. **Concentration**: the share of profit from the top 5 trades.

The gate is exactly LAB_STATE's promotion gate. The Console shows it as a checklist with ticks, never as one score.

**Edge ladder today (2026-09-30):** EXP-011, a learned migrate entry filter, is the only candidate. It's at "pre-registration pending, confirmation read ~2026-09-30T20Z". EXP-009 is a screen, and the migrate-direct cell is retired.

## 5. What MAL must add so the Console has something clean to read

These are small MAL-side PRs, and they're needed anyway:

1. **A standard `result.json`** written by every exploration and scoring tool (`exploration_*`, `exp011_score`, `kill_review`, future templates). It holds: tool, git SHA, command, config, data block(s) read (hour ranges + ledger owner), the metrics from §4 per fail model, gate ticks, a stage label, runtime, and peak RSS. The Console and DeepSeek read these, never logs.
2. **Job templates**: parameterized entry points with a JSON schema for their knobs, e.g. `explore_entry_filter` (trigger, model/threshold, exit, size, priority, market-cap band, exploration days) and `explore_exit` (the exit family and its parameters). They come from the existing tools, with ranges whitelisted in the schema, so DeepSeek can only pick values.
3. **A data catalog**: `data/catalog.json`, generated from the ledger and the walker checkpoints. For each block it lists the hours, host path, owner and status (sealed, unread, read once). The job runner refuses any read that the catalog doesn't allow for that job's role (§7).
4. **A status file per box** (`/var/lib/mal/status/<host>.json`, updated every minute by a tiny timer): MAL services, walker progress, runner lag, last restart. The Console reads these rather than SSH-ing around.
5. **A tries counter**: every exploration run appends one line to `data/tries.jsonl` (template, config hash, data block). That feeds "variant N of M" and the multiplicity accounting.

## 6. Owner ↔ Claude without interrupting Claude

- **"Send to Claude"** creates a **MiScusi task** tagged `mal-manager`, priority `normal`, with a structured body: what, why, links, and the owner's question. The manager session pulls tasks at its natural breaks (MiScusi A2 "pick up next task", plus a check at session start and after each merge or read). Nothing pushes into a running turn.
- **Claude's feed back** comes from the same system (task replies and results), plus the MAL daily note. The Console shows it as one stream.
- **Escalation rules** (DeepSeek → Claude) are limited to things Claude should act on:
  - a template result passes the **exploration screen**, with the ex-top-3 and both-models checks, on at least N variants, which gets a "propose pre-registration" note;
  - a job fails repeatedly;
  - a machine is in trouble.
  "Beat the best on exploration" is never phrased as "found an edge".
- Durable record: accepted ideas become `EXP/` proposals or lab-note entries in GitHub, written by Claude, not by the Console.

## 7. DeepSeek (via OpenRouter): allowed and not allowed

- **Allowed:**
  - read `result.json`, lab notes, EXP files, the ledger and the catalog;
  - fill **template parameters** within their schema ranges;
  - submit **exploration-role** jobs to research-0 (≤ K per day, ≤ M concurrent, declared resources);
  - write notes and explanations that always quote the raw numbers.
- **Not allowed:**
  - write or run arbitrary code;
  - read any block not in the exploration pool;
  - pre-register, promote or merge;
  - see secrets;
  - reach anything on mal-core-0 or mal-fast-0;
  - use any tool outside MiScusi's job API.
- The OpenRouter hard cap is set at the owner's $5–10/month. Its model is pinned, and it's logged per call.
- **Its explanations are labelled "assistant read, check the numbers".** It judges nothing about the gate; the checklist does that.

## 8. mal-research-0: the heavy-compute box (this is where the speed comes from)

- **Moves there:** all exploration and EXP scoring jobs; the feature-table builds; model training; sweeps. Later also the **backfill walkers**. On mal-fast-0 they share an 8G slice with the live listeners, and they're the reason fresh confirmation blocks take ~2 days. On research-0, 4–6 walkers in parallel would bring a 6-day block down to under a day, if Helius rate limits allow. That needs the Helius key placed there by Helm, and a credit report.
- **Stays on mal-fast-0:** the three live listeners, the Claude manager session and its timers, and MiScusi's brain. Nothing heavy.
- **Stays on Oracle:** the forward-paper runner, the trade tape, and the collectors.
- **Data sync:** a nightly `rsync` (plus on demand) of the sealed hour files the catalog lists to research-0 (`/data/mal/…`), with sha256 manifests like Helm's export. Confirmation blocks are synced and **locked**: readable only by the one-shot scorer role.
- **Resources:** MiScusi A4 declared-resource jobs with a fixed reserve (~16 GB), e.g. 6 slots × ~12 GB for exploration jobs. The lessons from 2026-09-29 carry over: bounded windows, rows streamed to disk, and whole-tree kill.
- **Hosting the Console:** research-0 (it has headroom), behind Cloudflare Access for the owner's email only. The tunnel, Access and ports are Helm's.

## 9. What MiScusi must provide first (and what the Console needs from it)

| MiScusi piece | Needed for | Must-have detail for MAL |
| --- | --- | --- |
| A8 servers + health | Machines, Home | Headless root service; memory in **anon** (not cache), disk, services |
| A1 slots + machine rules | Jobs, safety | "No heavy jobs on mal-fast-0"; research-0 slots; a memory check before admit |
| A4 long jobs | Run a test, Jobs | Declared mem/CPU/time; a queue that survives reboots; **structured `result.json` wake-ups**; failure, OOM and timeout wake-ups with the last 50 lines; priorities + per-session caps; sweeps as one unit; reproducibility (SHA, command, config, data version) |
| A2 inbox/notebook | Owner ↔ Claude | Tasks pulled at breaks; the notebook shown in the Console |
| A7 guardrails | Safety | Secret guard; protected paths in MAL (see below); per-machine command allowlist |
| Job **roles** (new ask) | Holdout safety | `exploration` / `confirmation-oneshot` / `ops`: the job runner passes the role to MAL's catalog check (§5.3) |

**Protected paths in the MAL repo** (the owner's decision 3; my defaults): `docs/HOLDOUT_LEDGER.md`, `DEC/`, the pre-registered `EXP/*-prereg.md` files once merged, `ops/claude-schedules/`, anything under `scripts/mal-core/` that deploys to hosts, and any future `live/` directory. Agents may propose changes by PR. MiScusi blocks direct edits from Console-originated jobs.

## 10. v1 cut, and order

1. **MAL-side contracts** (§5.1–5.5): `result.json`, the first two templates, the catalog, the status files, tries. That's small PRs, and it can start now.
2. **mal-research-0 up** through MiScusi A8/A1/A4 (Helm: users, the tunnel, Access, the Helius key if walkers move there). Then data sync, and heavy jobs move.
3. **Console v1:** Home, Machines, Paper (ops, blinded P&L), Edge ladder, Experiment board, Run a test (templates + DeepSeek fill + confirm), Jobs, Send to Claude, basic notifications.
4. v1.5: Ask the lab, search, sweeps UI, strategy-vs-replay drift.
5. Later: a profit home page after live is approved, a candidate → forward → live approval flow with the owner's sign-off, and a cost panel.

**Stack suggestion:** match MiScusi's (TypeScript/Node), so the same agents maintain both. It lives in a separate repo (e.g. `vaanai/mal-console`) with its own deploys, reading MAL through the files and contracts above.

## 11. Decisions the owner needs to make

1. OK with **kill-review blindness** (rule 2) and **exploration-only tinkering** (rule 1)? I strongly recommend both. They're what keeps any result from the Console honest.
2. Protected paths: accept the defaults in §9?
3. Move the **walkers** to research-0 (faster fresh blocks, and the Helius key would live there; Helm's call on placement)?
4. DeepSeek limits: jobs per day, concurrency, the monthly cap.
5. Console repo and stack: separate repo + TypeScript (the default)?
