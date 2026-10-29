# MAL Lab State

Compact reload for managers. Hot file only: current-state records, the decisions index, the promotion gate. Older history was moved, not deleted, on 2026-10-29. Current state and deadlines: [docs/HANDOFF.md](docs/HANDOFF.md). No book has cleared the promotion gate.

## Now (2026-10-24)

- **Mid-week restart record 2026-10-24 (manager15; records only).** Planned arena feed restart: job #1207 cancelled 12:10:03Z, SIGTERM 12:10:17Z, #1516 started 12:10:24.638Z; `process_restart` span 5,193 ms (from_ms 1792843819445, to_ms 1792843824638), no `feed_restart` row (expected by the code, QP-RULING-1024-779-feed (B)); #1516 limit 1,209,600 s (14 d), so one more feed restart is pinned for 2026-11-07T11:50-11:58Z. Before HV4's clock 2026-10-25T00:00Z. Notebook "Planned feed restart DONE" n_e0fsG-6YopjbOw.
- **Records 2026-10-24 (manager15; records only, no profit claim; no book has cleared the promotion gate).** **H5 arena a2 ended NOT_DECIDABLE (final), both books** (`h5v1-boostfloor-a2`, `h5v2-boostclock-a2`): window_end_ms 1792696205000 (2026-10-22T19:10:05Z), before their clock of 2026-10-23T00:00:00Z, on the A3 `synthetic_share_high` flag at 8/19 = 0.421 (MiScusi notebook n_5ob4j6rvho4xuQ). Empty window, 0 counted attempts, counted in N (+2). The halt was noticed about 29 h late (second missed A3 halt after 2026-10-20). Pooled replacement rule and Q50 scope: DEC-031 Amendment 10 (DRAFT, PR #840 code); ruling `/data/mal/hunt-1008/push-1016/hunt29/a3fix/QP-RULING-1024-a3-synthetic.md`. Q50 (`h5v1q50-boostfloor-a1`, clock 2026-10-25T00:00:00Z) is covered only if the fix merges and the guard is redeployed before the clock.
- **Records 2026-10-21 (manager13; records only, no profit claim; no book has cleared the promotion gate).**
  - **Mid-week restart record: walk 2 #1036 resumed after the 2026-10-20 reboot** (EXP-027 section 4.4 walk-2 coverage; resumable, same `forward-walk2.sh`, git_ref `578a63eac199c9175438e1ec651d1537944a89c5`, checkpoint resume). New limit **2026-11-03T17:51Z** (the prior limit was 2026-10-29T19:21:21Z). The host-side successor gate is now **#1289 -> #1290**, replacing the #1214 -> #1215 chain. Job ids, limit and gate are from the manager's brief and were not re-read from MiScusi. The EXP-027 `EXP027_WALK2_JOB_2` Window record line is not written by this record (closed key list, one commit per line, manager only).
- **Walk 2 running.** Job #1036 (mal-research-0, `forward-walk2.sh`, start 2026-10-16T01, resumable, 6144 MB, 10080 min), git_ref `578a63eac199c9175438e1ec651d1537944a89c5`; pins exit 0 with the three decoder blobs (DEC-016 Log); credit cap 11,825,000; submit #1035 failed at setup on a malformed git_ref, nothing ran. **#382 (forward-1002) cancelled** at about 2026-10-16T04:29:50Z after the FINAL ledger row: 324 hours sealed (2026-10-02T15 .. 2026-10-16T02), credits_used 4,847,780. **#433 (forward-1002ev) done**: exit 0, 169 hours sealed [2026-10-09T00 .. 2026-10-16T00], credits_used 2,754,540 of the 3.6M cap. Exporter job #1034 is running.
- Jobs, deadline table, what is owed and the rules that bite: [docs/HANDOFF.md](docs/HANDOFF.md).
- Older "Now" bullets (2026-10-12 to 2026-10-21 records, arena 10-18, push rounds, outage, H5 stop, DEC-016 FINAL, markers, F1) moved verbatim on 2026-10-29 to [ARTIFACTS/lab/lab-state-archive-2026-10.md](ARTIFACTS/lab/lab-state-archive-2026-10.md) under "Moved from LAB_STATE 2026-10-29", section "Now: older records".

- "Objective" and "Hard fences" (the live rules are in [CLAUDE.md](CLAUDE.md) "Hard rules") moved verbatim on 2026-10-29 to [ARTIFACTS/lab/lab-state-archive-2026-10.md](ARTIFACTS/lab/lab-state-archive-2026-10.md) under "Moved from LAB_STATE 2026-10-29", section "Objective and Hard fences".

## Hosts

| Host | Role |
| --- | --- |
| `mal-core-0` | Oracle, aarch64, archive + training + forward paper. |
| `mal-fast-0` | OVH Frankfurt, x86_64. Fast listeners + trade-tape trial. **Main focus.** |

Detail, units, logs, memory limits, and what is safe to restart: [docs/HOSTS.md](docs/HOSTS.md).

## Decisions index

| DEC | Topic |
| --- | --- |
| [DEC-001](DEC/DEC-001-lean-four-override.md) | Lean four seats |
| [DEC-002](DEC/DEC-002-memory-first-no-db-local.md) | Memory-first; JSONL spine |
| [DEC-003](DEC/DEC-003-regime-at-ingest-v0.md) / [DEC-004](DEC/DEC-004-regime-id-encoding.md) | Regime-at-ingest v0 |
| [DEC-006](DEC/DEC-006-detect-decode-evaluate-runners.md) | detect → decode → evaluate → runners |
| [DEC-007](DEC/DEC-007-full-detect-book-anti-selection-bias.md) | Full detect book |
| [DEC-008](DEC/DEC-008-stack-phase-gates.md) | Stack phase gates |
| [DEC-009](DEC/DEC-009-oracle-always-free-phase0-host.md) / [DEC-010](DEC/DEC-010-oracle-phase0-handoff-autonomy.md) | Oracle host and autonomy |
| [DEC-011](DEC/DEC-011-cursor-oracle-access-cf-tunnel.md) | Cloudflare Tunnel + Access (**LIVE**) |
| [DEC-012](DEC/DEC-012-tool-neutral-manager-workers.md) | Manager plans, workers open PRs, tool-neutral workflow |
| [DEC-013](DEC/DEC-013-claude-manager-merges.md) | Claude manager merges (not Helm); Helm keeps ufw/sshd/tunnel/Access/Oracle admin |
| [DEC-014](DEC/DEC-014-holdout-ledger-and-multiplicity.md) | Holdout ledger (one owner per historical block); Holm–Bonferroni multiplicity correction; single read at kill review; pressure-leg start instant (Amendment 3) |
| [DEC-015](DEC/DEC-015-forward-paper-on-fast.md) | New forward-paper books (after the 10-05 kill review) run on `mal-fast-0`; preconditions: all four runner inputs, trade-tape coverage, equivalence replay, own memory slice, lag probation; runner-as-service exception owner-confirmed |
| [DEC-016](DEC/DEC-016-exp012-forward-on-chain-hours.md) | EXP-012 forward book: one FINAL read over `[10-06T00, 10-16T00)`; Am.3 live support at measured k; **Am.4 live support judged on the V-priced book (B)** |
| [DEC-018](DEC/DEC-018-live-trial-readiness.md) | Live trial readiness. **Am.1 (10-06):** size ladder 0.05 → 0.25 (after the 10-16 read) → 0.5. Each rung is an owner yes; stop levels delegated to the manager |
| [DEC-019](DEC/DEC-019-execution-probe.md) | Live execution probe, 0.05 SOL. Am.1/1a: 90 attempts, loss cap 0.25, 500k priority, end 2026-10-12T00Z |
| [DEC-020](DEC/DEC-020-size-step-proposal.md) | Size step to 0.25: **Option A (owner, 10-06)**. Waits for the 10-16 FINAL read; no new funding before then. Same-day package in §7 |
| [DEC-021](DEC/DEC-021-champion-challenger.md) | Champion/challenger: **approved (10-06); the switch is delegated to the manager** when §6(a)–(e) and §7 hold. Re-pin is Helm's |
| [DEC-022](DEC/DEC-022-fast-entry-build.md) | Fast-entry build (proposal). Status: **Proposed 2026-10-07** (manager9). Nothing in it is live. Phase A is paper only and needs no key. Phase B needs the owner's go, and Helm for custody and host changes. |
| [DEC-023](DEC/DEC-023-h5-family.md) | H5-BOOSTFLOOR is a new DEC-014 family on forward hours (EXP-024). Status: **Proposed 2026-10-08.** It takes effect on merge, with quant-proof OK on its final head, before 2026-10-10T00:00Z. The manager asks the owner for an OK on this DEC and on DEC-021 Amendment 2 by 2026-10-09T20:00Z. If the owner has not answered by then, the manager decides under the owner's 2026-10-08 mandate (`docs/HANDOFF.md`, "Owner mandate": full autonomy, the gate kept) and writes the decision to the MiScusi notebook and to this file. |
| [DEC-024](DEC/DEC-024-h5-live-canary.md) | H5 live measurement canary (0.02 SOL). Status: **Proposed 2026-10-08; the owner approved the canary in principle the same day (the owner's words are in section 1).** The limits below are the manager's terms for the owner's confirmation. It takes effect on merge, with quant-proof OK on its final head. **No live send** until every item in section 8 is done, and never before [EXP-024](EXP/EXP-024-h5-boostfloor-part1-prereg.md) Part 1 is merged. |
| [DEC-025](DEC/DEC-025-c1nf-family.md) | C1-NF is a new DEC-014 family on forward hours, with its own alpha slot, two looks and a second counted reader (EXP-025). Status: **Proposed 2026-10-09.** It takes effect on merge, with quant-proof OK on its final head, before 2026-10-10T00:00Z. The owner's answers on O1, O2 and O3 were confirmed by the manager the same day (`OWNER_DECISION_CONFIRMED` below). The alpha pair is (0.005, 0.020), quant-proof's recommendation. Quant-proof's 12 required edits on 95a0cc6 are applied in this version. |
| [DEC-026](DEC/DEC-026-c1nf-live-canary.md) | C1-NF live measurement canary (0.05 SOL, second wallet). Status: **DRAFT 2026-10-09, updated about 17:50Z** for quant-proof's NOT OK on e570954 and the owner's 17:30Z decisions (stake 0.05 SOL, synthetic pools kept in). The owner's words are in the `OWNER_CANARY_CONFIRMED` line and its addendum in section 1. Two earlier defaults still apply unless he objects. The manager's terms are marked. It takes effect on merge, with quant-proof OK on its final head. **No live send** until every item in section 11 is done. Appendix A is draft text for a separate EXP-025 amendment, not an edit to EXP-025. **[Note 2026-10-10, QP-1010 (b1) item 1.]** #522 merged this file at 2026-10-09T18:00:11Z with the DRAFT label above. No quant-proof OK on a final head is recorded in this file, so the second condition of "takes effect on merge, with quant-proof OK on its final head" is not met. QP-1010 calls this ambiguous; read as written, the DEC is **not yet in effect**. The OK, its head and its instant go in section 11 item 3 when given. No live send before section 11 is done, either way. |
| [DEC-027](DEC/DEC-027-h5-champion-challenger.md) | H5 champion-challenger plan (paper variants beside the live v1, one switch file). Status: **Proposed 2026-10-09; revision 2 after quant-proof's review of 975d8db (NOT OK), which this revision applies.** Paper only until a switch is approved. The champion is H5-BOOSTFLOOR v1, live under [DEC-024](DEC/DEC-024-h5-live-canary.md). This file freezes no challenger and starts no trade. It takes effect on merge, with the owner's OK and quant-proof OK on its final head. **Gating item G1** (section 5.4): no multi-rule shadow starts until the companion EXP-024 amendment has merged. |
| [DEC-028](DEC/DEC-028-h5-v2-canary.md) | H5-BOOSTCLOCK v2, paper first or an owner-override canary (draft, for the owner). Status: **Draft, 2026-10-10. The owner chose on 2026-10-11 (section 3.5, lines in sections 3.1 and 9): Option A now, plus a pre-authorized Option B that starts only after v1 is halted by a DEC-024 §5.1 BOOST-timing halt.** No live send happens under this file until section 3.5's conditions all hold. It takes effect on merge, with quant-proof's OK on its final head and the owner's dated line (section 9). Option B's override line and the owner's confirmation of section 3.1's two sentences are both recorded in section 3.1 (the second at 2026-10-11T05:26:36Z). Revised 2026-10-10 for quant-proof's CHANGES review of 62cc971 (items 10-13 applied, and Option B's seal consequence for EXP-026 made explicit in sections 3.1 and 7). [moot for EXP-026: EXP-026 withdrawn 2026-10-17 before W0 (owner); DEC-028 itself unchanged] |
| [DEC-029](DEC/DEC-029-exp026-alpha-slot.md) | EXP-026's alpha slot (DEC-021 §8's fourth slot, M1), or the owner's M2 line (draft). Status: **Draft, 2026-10-10. The owner chose M1 on 2026-10-11 (section 3). Quant-proof ruled at `2f8c94a` that the line counts (section 3, "What follows"). The route is M1, fixed at EXP-026's merge and never changed after it; at merge the manager informs the owner of section 3's required text and the binding-item correction.** It takes effect on merge, with quant-proof's OK on its final head and the owner's line. [EXP-026](EXP/EXP-026-h5-boostclock-v2-prereg.md) does not merge without it. No hour was read to write it. [slot 4 unused: EXP-026 withdrawn 2026-10-17 before W0 (owner); see the DEC-029 Note] |
| [DEC-030](DEC/DEC-030-walk2-push-slot.md) | Walk 2's push slot (DEC-021 §8's fifth slot, alpha 0.025, up to 3 books) and the DEC-021 §2 route R1 or R2. Status: **Owner's line recorded 2026-10-17 (route R1); takes effect on merge with quant-proof's OK on the exact final head and a reviewer verdict.** No candidate is named and no hour was read to write it. |
| Owner, 2026-10-21 | API $200 cap to 2026-11-11; low-frequency strategies welcome (P0 20 a day no longer a kill, floor about 2 a day, pending a DEC-031 amendment). Direction recorded; until the amendment merges DEC-031 Am.3 item 1 (20 a day P0) and the current A3 rule (a pins_changed flag ends counted windows at the flag instant; no manual PASS) still apply; work with pump redeploys. Notebook n_VXxa1CEWcslpaA, correction n_Mt_dg3k_cZP3eA. |

Moved verbatim to [ARTIFACTS/lab/lab-state-archive-2026-10.md](ARTIFACTS/lab/lab-state-archive-2026-10.md) on 2026-10-15T19:27:08Z (docs-slimming PR).

- "What is running" table (as of 2026-10-05; current jobs are in docs/HANDOFF.md) moved verbatim on 2026-10-29 to [ARTIFACTS/lab/lab-state-archive-2026-10.md](ARTIFACTS/lab/lab-state-archive-2026-10.md) under "Moved from LAB_STATE 2026-10-29", section "What is running".

- "Current research state" moved verbatim on 2026-10-29 to [ARTIFACTS/lab/lab-state-archive-2026-10.md](ARTIFACTS/lab/lab-state-archive-2026-10.md) under "Moved from LAB_STATE 2026-10-29", section "Current research state".

## Promotion gate

At least **100** out-of-sample trades, at least **5** distinct UTC days with a majority of those days positive, lower **90%** CI bound of mean SOL per trade **> 0**, and total SOL still positive after removing the top 3 trades. The book must clear that bar under **both** the flat 15% fail rate and the pressure-fail model at slope scale 1. Bootstrap: 1,000 draws, seed 1. The lower bound is the 5th percentile of those means.

**Multiplicity ([DEC-014](DEC/DEC-014-holdout-ledger-and-multiplicity.md)):** when k ≥ 2 candidate books or cells are read together at one review (e.g. the 9 forward books on 2026-10-05), a book promotes only if it also passes a Holm–Bonferroni step-down at family α = 0.05 on the one-sided bootstrap p-value (share of bootstrap means ≤ 0), under both fail models: the smallest p is compared to α/k (0.05/9 ≈ 0.0056 for k = 9), the next to α/(k−1), and so on. Multiplicity-tested reads use **10,000** bootstrap draws, seed 1. Reference cells are not in k, and a single pre-registered primary cell is k = 1.

Live bar, still required after the gate: about 7 days of forward paper, then tiny size, and an explicit owner yes. The owner's dollar target is not evidence. See [CLAUDE.md](CLAUDE.md).

## Holdout ledger

[docs/HOLDOUT_LEDGER.md](docs/HOLDOUT_LEDGER.md), adopted by [DEC-014](DEC/DEC-014-holdout-ledger-and-multiplicity.md): one owner per historical block, non-owner reads disclosed in the owner's `EXP-###` file, rows written before hours are sealed or read.

Moved verbatim to [ARTIFACTS/lab/lab-state-archive-2026-10.md](ARTIFACTS/lab/lab-state-archive-2026-10.md) on 2026-10-15T19:27:08Z (docs-slimming PR).

## Open PRs

[#90](https://github.com/vaanai/MAL/pull/90): keep. `JobQueue` on `main` is still unbounded; the stale-drop and credit cap in this draft are not in the tree.

- "Next work" (authoritative list is the deadline table in docs/HANDOFF.md) moved verbatim on 2026-10-29 to [ARTIFACTS/lab/lab-state-archive-2026-10.md](ARTIFACTS/lab/lab-state-archive-2026-10.md) under "Moved from LAB_STATE 2026-10-29", section "Next work".

## Pointers

- Hosts: [docs/HOSTS.md](docs/HOSTS.md)
- Manager handoff (current): [docs/HANDOFF.md](docs/HANDOFF.md)
- Claude handoff: [CLAUDE.md](CLAUDE.md), [docs/MIGRATION-TO-CLAUDE.md](docs/MIGRATION-TO-CLAUDE.md)
- Research options: [docs/research/](docs/research/)
- Daily briefs: [ARTIFACTS/daily/](ARTIFACTS/daily/)
- Lab notes: [ARTIFACTS/lab/](ARTIFACTS/lab/)
- SSH: [scripts/mal-core/agent-ssh.sh](scripts/mal-core/agent-ssh.sh), [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md)
