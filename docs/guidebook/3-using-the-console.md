# 3. Using the Console

> **In short**
> The Console is a private, read-mostly window onto MAL: machines, the runner, every idea's stage, and a place to run your own exploration tests without touching production. The server enforces the rules — blinding, exploration-only data, DeepSeek's limits — so the interface can't bypass them. Anything marked **coming** below isn't live yet.

This assumes you've read [doc 1](1-how-mal-finds-an-edge.md) and [doc 2](2-how-we-avoid-fooling-ourselves.md); it only says where those ideas show up on screen. The Console lives at `console.tradervaan.com`, behind Cloudflare Access, owner-only. Nine screens, reached from the top nav (desktop) or the bottom tab bar (phone).

## Colours and the hatch pattern

- **Blue (accent)** — things you can press, or data your tests may read.
- **Green / red (ok / danger)** — money made or lost, a gate check passed or failed, a service up or down. Always shown with a sign and a number, never colour alone.
- **Amber (warn)** — needs attention soon: a "now" marker, a closing review window, a degraded machine.
- **Purple** — Claude's voice on the Claude screen: findings, decisions, questions.
- **Orange** — reserved for live mode (top stripe, stop button). You won't see it — the Console is research-only today.
- **Diagonal hatching** — the same repeating stripe everywhere something is locked or hidden by a rule: a holdout block owned by one experiment, blinded paper P&L during a review window, cache memory on a machine card. A hatched box always says why, and until when if that's known.

Every number, hour, ID and git SHA renders in a tabular monospace font; SOL amounts and percentages always carry an explicit sign.

## Home

Three "next up" cards (next scheduled read, daily restart, any open review), a card per machine, the edge ladder, a "For you" box (Claude's/DeepSeek's open questions), this month's spend, and the data-block timeline. Nothing here submits anything — it's a summary, and clicking through takes you to the fuller screen. If MiScusi is unreachable, a banner says so on every screen; if the Console server itself is unreachable, the page keeps its last data with a banner and an age.

## Machines

One card per host — `mal-fast-0`, `mal-core-0`, `mal-research-0` — with a health badge, memory (real use as a solid bar, cache as hatching, the cap as a red tick; the two are never added together), CPU, disk, and each service as a chip, plus the status file's age. Read-only: no restart or stop button lives here. `mal-research-0` has no status file yet, so its card is a fixed setup checklist instead of live numbers — **coming**.

## Paper

Whether the forward-paper runner is up, its lag, 24-hour lag breaches, the last ten restarts, and — outside a review window — a table of books with trades, open positions, and P&L. During an active review window the table is replaced by a hatched box naming the rule and when it unhides; the server strips the P&L field entirely, so there's no way to see it early. **The per-book P&L feed itself isn't wired up yet** (it reads empty even outside a window) — **coming**; the blinding is real, the data behind it isn't. Restart history and lag are never blinded.

## Edge ladder

Every idea as a card in a column for its stage (Exploring → Candidate → Pre-reg → Confirming → Fwd paper → Live → Failed), then every read result as a card: exploration cards dashed with a "variant N of M tried on this data" tag, confirmation cards solid with the full gate checklist under both fail models. Every exploration card has a "Propose pre-registration" button, but it's inert today — **coming**; tell Claude directly on the Claude screen instead. While a review window is open, forward-paper cards and results built from the reviewed data go hatched, same as Paper.

## Run a test

The one screen that changes something: it submits a job to `mal-research-0` through MiScusi, against **exploration data only** — enforced by the job runner itself, not this screen. You see a rules reminder, a quota strip (tests used today, jobs running, DeepSeek spend, each against its cap), and two tabs:

- **Ask DeepSeek** — describe the test in plain English; DeepSeek picks one template, fills its parameters, and explains what it will do. You can still edit every field before running it.
- **Build it yourself** — pick a template and fill it by hand, with the schema's ranges shown as hints and out-of-range values flagged.

Two templates exist: `explore_entry_filter` (an entry-selection model, a threshold or top-N cut, one of 41 exit rules) and `explore_exit`, both against the shared exploration pool. "Run on research-0" queues the job and takes you to Jobs; it's disabled while the form has an error or the quota strip shows a block. It can't reach a holdout or confirmation block, can't exceed **20 tests/UTC day**, **3 running at once**, or **$10/month** of DeepSeek spend — all server-enforced, so retrying doesn't get around them. The backtest code behind both templates is **coming**. It gets wired in after the EXP-011 read. Until then, a submitted test is refused with a clear "not implemented yet" error before it reads any data. Separately, `mal-research-0` isn't online yet, so real tests will start once both are in place.

## Jobs

Three columns — Running, Queued, Finished — each showing template, machine, peak memory, and age. Opening a job shows its state, parameters, a log tail, its result card if any, and actions. Pause, Cancel, Resume and Re-run each ask "Are you sure?" inline before firing. A re-run counts against the same daily/running limits as a fresh submit. If MiScusi is unreachable this screen just shows an empty list rather than an error — check the top banner, not this screen, for that signal.

## Claude

One stream, oldest to newest — Claude's and DeepSeek's items in purple/neutral text, your notes alongside — plus a composer. "Send" queues your note to MiScusi's inbox; it never interrupts a running turn, and Claude picks it up at a natural break. There's no way to force an immediate reply, and Claude never hands work back to you through this screen.

## Data

The same block timeline as Home, by host on one UTC scale, plus a table (block, host, hours, owner, whether your tests can read it, status) and a progress bar per backward walker. Solid blue = exploration pool, hatched = locked to one experiment or a review window, plain = sealed history with no special access. It's a read view of `data/catalog.json` and the walkers' status — no reassigning an owner or forcing a re-seal from here, and the catalog refreshes periodically, not on a live heartbeat.

## Spend

This month's four lines — Helius credits (against the ~20M autoscale headroom), DeepSeek (against its $10 cap), Claude, and server costs — each with a source note. A line reading "not entered yet" or "not configured" is never a fabricated zero; Claude and server spend both depend on figures nobody has supplied yet.

## What DeepSeek may and may not do

DeepSeek only appears in Run a test's "Ask DeepSeek" tab. It **may**: read the templates' descriptions, pick exactly one, and fill its parameters; write a plain-English explanation quoting what the test will do; act as your guidebook tutor (**coming**, not wired in yet).

It **may not**: write or run any code — its prompt forbids even mentioning scripts or shell commands, and it only ever returns a template name plus parameters; choose its own model — it's pinned in server config; touch anything but the exploration pool — the same job-runner check blocks it too; pre-register, promote, or merge; see a secret or a holdout/confirmation block; exceed **20 tests/day**, **3 running**, or **$10/month** — all enforced server-side, with every call (including failures) logged by cost, never by key. If DeepSeek's key isn't in place yet, drafting returns a clear "not configured" message rather than failing silently — **coming**, pending Helm placing the key.

## When something is down

- **Console server unreachable:** your open page keeps its last data, with a banner and an age. Nothing stale is shown as current.
- **MiScusi unreachable:** a banner shows on every screen; Jobs and quota reads come back empty rather than erroring — check the banner first.
- **A machine's status file stale or missing:** its card turns "Down" with a note naming the file, plus the file's age.
- **Something goes down mid-job:** research continues if the Console is down; queued jobs wait and running jobs finish if MiScusi is down; the paper runner and listeners are unaffected if research-0 is down (`docs/console-plan.md` §10).
- **A review window is active:** Paper's P&L and forward-paper ladder cards go hatched on purpose until the window ends. If `data/console.json` is missing, malformed, or has an unparseable window, the server treats that as active too — blinding fails **closed**, never open.

## When something looks off — a checklist

- **A red machine card.** Read the health note; it names the missing file or error. Claude reads the same status file and is already on it. `mal-research-0` reading "Setting up" is expected.
- **P&L hidden.** Check Home's "next up" strip for an open review window — that's by design (doc 2, "review blindness"). If no window looks open and P&L is still hidden, `console.json` is probably broken, which fails closed the same way.
- **A test refused.** The quota strip or the draft's errors say why: the day's 20 used, 3 already running, DeepSeek's cap reached, an out-of-range value, or MiScusi not configured. Not a bug — the limits working.
- **Jobs looks empty unexpectedly.** Check the MiScusi-unreachable banner before assuming nothing's running.
- **A number reads "—" or "not entered yet."** The Console refusing to guess, not a zero.
- **Something you expected just isn't there.** Check this doc for a "coming" tag first — Paper's per-book feed, "Propose pre-registration," `mal-research-0`'s live card, and DeepSeek's tutor mode are built but not yet wired to real data.

## What you can't break

1. See blinded paper P&L before a review window ends — enforced server-side.
2. Submit a test against anything but the exploration pool — checked by the job runner, not the UI.
3. Touch `mal-core-0` or `mal-fast-0` directly — every action becomes a MiScusi job or inbox note.
4. Interrupt a running Claude turn — "Send to Claude" only ever queues.
5. Expose a secret — the MiScusi and OpenRouter keys live in server-side files, never the browser or a log.
6. Start a trade, touch a wallet, or place a real order — every job here is paper-only; live mode's styling exists but is unused.

## Glossary

- **Panel** — the one card shape used everywhere; panels never nest.
- **Hatch** — the diagonal-stripe pattern for anything locked or blinded; always names why and, if known, until when.
- **Tick** — one gate-check cell: check-and-value if passed, cross-and-value if failed, em dash while pending.
- **Freshness / stale** — every panel's data carries its generation time and age; "stale" means older than that source's expected update cadence, shown rather than hidden.
- **Fail closed** — a broken or missing review-window file hides P&L rather than showing it; safety over convenience.
- **Review window** — the scheduled span during which forward-paper P&L is withheld, ending in a single, once-only read.
- **Quota strip** — Run a test's counter for today's tests, running jobs, and DeepSeek spend, each against its cap.
- **Draft** — DeepSeek's proposed template and parameters, editable before you run it; nothing runs until you press "Run on research-0."
- **Template** — a parameterized, schema-validated exploration job (`explore_entry_filter`, `explore_exit` today); only exploration-only templates ever appear here.
- **Ladder stage** — where one idea sits: Exploring, Candidate, Pre-reg, Confirming, Fwd paper, Live, or Failed.
- **Result card kind** — "Exploration · not evidence" (dashed) versus "Confirmation · one read" (solid, full gate checklist) — see doc 2.
- **Mode** — research (paper only, the only mode live today) versus live (money at risk, orange, not yet used).
- **MiScusi** — the agent platform the Console sits on top of; runs jobs and holds Claude's inbox.
- **research-0** — the planned heavy-compute host for exploration jobs and walkers; its card shows a setup checklist until it's online.
