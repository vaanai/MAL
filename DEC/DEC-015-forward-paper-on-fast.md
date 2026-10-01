# DEC-015 — New forward-paper books run on mal-fast-0

| Field | Value |
| --- | --- |
| **Status** | Active (plan). Nothing starts before the 2026-10-05T05:00Z kill review |
| **Decider** | Vaan (owner, in the manager session 2026-10-01: new forward trading should run on the fast server), with the Claude manager |
| **Date** | 2026-10-01 |
| **Does not amend** | Promotion gate, paper-only fence, DEC-014 (holdout ledger, single read at the kill review), "never add forward books during a kill-review week", "every long-running job is a MiScusi job" (see §2.6 for why the runner is a service) |

## Context

1. **Oracle's runner keeps falling behind.** The 9 forward books run on Oracle `mal-core-0` (`mal-forward-paper.service`, code `d7485d2`). Its `lag_ms` is the runner's backlog behind the tape (wall clock minus receive time), not network distance. It breached the 5,000 ms stale cap 33 times in about 24 h on 2026-10-01, and read 5,869 ms at 04:59:54Z. The cause is not diagnosed; Oracle contention is a live hypothesis. Numbers and sources: [ARTIFACTS/daily/2026-10-01-oracle-runner-lag.md](../ARTIFACTS/daily/2026-10-01-oracle-runner-lag.md).
2. **Paper should run where live will run.** Live is gated on the promotion gate and owner approval, and when it comes it will **most likely** run from `mal-fast-0` ([docs/console-plan.md](../docs/console-plan.md)). Paper books only predict live results if they see the same feeds, timing and host as live will.
3. **`mal-fast-0` is not ahead on every feed.** [ARTIFACTS/lab/fast-listener-2026-09-27.md](../ARTIFACTS/lab/fast-listener-2026-09-27.md) shows fast-0 behind Oracle on free create feeds (PumpPortal about 59 ms, public logs about 42 ms behind). Only paid Helius preprocessed is ahead (about +106–109 ms). The fast public trade tape (`mal-fast-trade-tape`, public `logsSubscribe`, $0 in Helius credits) had a median lead under 150 ms when measured.

So the move is justified by (2), and by (1) as a **hypothesis to test**: the backlog may simply not happen on a less contended host. It is not justified by a claim that fast-0 is closer to the chain on every feed.

## Decision

1. **Which books.** Every forward-paper book **started after** the 2026-10-05T05:00Z kill review runs on `mal-fast-0`. The first is EXP-012's book, if its one-shot read passes. The 9 existing books stay on Oracle, untouched, through the kill review. No book moves mid-measurement. New books get their own HOLDOUT_LEDGER entry; the existing forward-paper row stays Oracle.
2. **Preconditions on `mal-fast-0`**, each measured and recorded in LAB_STATE before the first book counts toward the gate:
   1. **Every runner input exists on fast-0.** That is the trade tape, observe creates, attention snapshots and the `funding-*.jsonl` graph (`tools/forward_paper.py`). Today those are produced on Oracle. For each input there must be a written plan (produce it on fast-0, or ship it from Oracle with a measured delay) before the runner starts.
   2. **Feed check.** `mal-fast-trade-tape` gets a fresh trial run, which is not a forward book. Bars: coverage at least 95% against Oracle's tape over the same window (the bar from the fast-listener note), and lead or lag reported per feed. It costs no Helius credits on public `logsSubscribe`; if any Helius path is used, the credits per day are measured too.
   3. **Code equivalence.** An md5 decision-equivalence replay of the fast-0 runner build against Oracle's runner on the same recorded tape, as CLAUDE.md requires for every runner change. This proves the **code** makes the same decisions; it does not prove the live fast feed behaves like Oracle's, which is what check 2.2 is for. The replay is a heavy job: it runs on `mal-research-0` as a MiScusi job.
   4. **Memory.** The runner's RSS grows about 200–300 MB/h on Oracle and reached 4.5 GiB at the daily restart. On `mal-fast-0` (about 23 GiB) it runs in `user-1000.slice` (ubuntu, `MemoryMax` 8G, shared with the listeners). Its unit gets `MemoryHigh=5G` and `MemoryMax=6G`, plus the existing 00:00Z daily restart. If measured growth would cross `MemoryHigh` before the restart, the restart goes to every 12 h, with DEC-014's orphan settlement handling the open positions. That is decided from the measurement before any book starts, not after.
   5. **Lag.** Over 2 consecutive UTC days of the runner running with no books counted, it has **at most 3 lag-cap breaches per 24 h** (the daily review's alarm level), measured the same way as on Oracle.
   6. **Service, not a job.** The forward runner is a 24/7 service with daily restarts, not a batch job, so it is a systemd unit, the documented exception to "every long-running job is a MiScusi job". All its build, replay and measurement work runs as MiScusi jobs.
   7. **Kill-review tooling.** DEC-014's orphan-settlement and pressure-stamp tools and the code floor (at least `d7485d2`) work on the fast runner's output. They are tested on the trial output before a book counts.
3. **Before 10-05 05:00Z.** The unit may be built and the feed trial (2.2) run, but **no forward book starts** and no row counts. That keeps the kill-review-week rule.
4. **Clean clock.** A book's clean clock starts at its first start on `mal-fast-0` after every precondition is met. Earlier rows do not count.
5. **Oracle's runner** keeps archive, training and the 9 books until they are settled at the kill review. Winding Oracle forward paper down is a separate decision.

## Consequences

- EXP-012's forward book (if any) starts after 10-05 05:00Z on `mal-fast-0`, and only once every §2 precondition is recorded. Several of them (inputs, feed trial, replay) are worker PRs that can start now.
- The 9 existing books' lag-breach windows still matter for the kill review and are handled by its procedure (stale rows, DEC-014).
- If the lag check (2.5) fails on `mal-fast-0` too, the backlog is a runner problem, not a host problem, and it gets fixed in the runner before any move.
