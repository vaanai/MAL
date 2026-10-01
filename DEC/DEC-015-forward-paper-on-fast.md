# DEC-015 — New forward-paper books run on mal-fast-0

| Field | Value |
| --- | --- |
| **Status** | Active (plan). Nothing moves before the 2026-10-05T05:00Z kill review |
| **Decider** | Vaan (owner, 2026-10-01: "forward trading should be happening on the fast server … because of the latency issues"), with the Claude manager |
| **Date** | 2026-10-01 |
| **Does not amend** | Promotion gate, paper-only fence, DEC-014 (holdout ledger, single read at the kill review), "never add forward books during a kill-review week" |

## Context

- **Where it runs today.** The 9 forward books run on Oracle `mal-core-0` (`mal-forward-paper.service`, code `d7485d2`).
- **The lag problem.** Oracle's forward-paper lag breaches its 5,000 ms stale cap.
  - 2026-10-01 daily review: **33 breaches in ~24 h**, live `lag_ms` **5,869** at 04:59:54Z. The prior daily counts were 85 → 83 → 48 → 15.
  - Pre-restart lag at the 00:00Z restart was **2,688 ms**.
  - Paper results only predict live ones if the paper runs with the latency live will have.
- **Where live will run.** Live trading will run from `mal-fast-0` (OVH Frankfurt). The Console moves off `mal-fast-0` before any live key lands there.

## Decision

1. **Which books move.** Every forward-paper book **started after** the 2026-10-05T05:00Z kill review runs on `mal-fast-0`. The first is EXP-012's book, if its one-shot read passes. The 9 existing books stay on Oracle untouched until the kill review has been read. No book moves mid-measurement.
2. **Preconditions on `mal-fast-0`, in order, each recorded in LAB_STATE before the first book starts:**
   - **Trade feed.** A live trade tape exists. `mal-fast-trade-tape` (installed, disabled; median lead under 150 ms when measured) is enabled only after a fresh measurement of credits per day and lead/lag vs Oracle's tape. It stays disabled if it fails the credit bar in HOSTS.md.
   - **Equivalence proof.** The runner on `mal-fast-0` passes an md5 decision-equivalence replay against Oracle's runner on the same recorded tape (CLAUDE.md: every runner change).
   - **Memory cap.** `mal-fast-0` has ~23 GiB and an OOM history (2026-09-29). The runner gets `MemoryMax=6G` and the existing daily 00:00Z restart. Oracle's runner reaches ~4.5 GiB RSS by restart time. Heavy jobs stay on `mal-research-0`.
   - **Latency measured.** The 24 h lag-breach count is measured on `mal-fast-0` and must be well under Oracle's before the first book counts toward the gate.
3. **Clean clock.** A book's clean clock starts at its first start on `mal-fast-0`. Rows before any precondition is met do not count.
4. **Oracle's runner.** Oracle keeps archive, training and the existing runner until those books are settled at the kill review. After that, Oracle forward paper is wound down by a separate decision.

## Consequences

- EXP-012's forward book (if any) starts after 10-05 05:00Z, on `mal-fast-0`.
- Before then: measure the fast trade tape (credits, lead), build the equivalence replay, and add the unit with a memory cap. These are worker PRs.
- Oracle lag spikes stop mattering for new books. They still matter for reading the 9 existing books at the kill review: lag-breach windows must be handled as the kill-review procedure already says (stale rows, DEC-014).
