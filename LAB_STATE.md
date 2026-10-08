# MAL Lab State

Compact reload for managers. **As-of:** 2026-10-08 ~16:00Z (manager9). `main` through #467 plus this PR. **Read [docs/HANDOFF.md](docs/HANDOFF.md) first.** **The 2026-10-08 profitability audit ([ARTIFACTS/lab/audit-2026-10-08/](ARTIFACTS/lab/audit-2026-10-08/README.md)) supersedes older strategy text here:** the fast-entry retail thesis is refuted at MAL's latency, and CAP-PICK is the only remaining candidate. Owner decisions O1, O2, O3 (with its fallback; the manager chose Option Y) and O6 are made. O4/O5 are deferred. O7: the sniper route is closed for now. O8 and O9 are still with the owner. HANDOFF also holds the 10-16 FINAL procedure (DEC-016 Am.5 §7), the EXP-021 path and the block-budget rule (DEC-014, 2026-10-07). **Paper research only. The DEC-019 live probe is STOPPED** (owner, 2026-10-07T01:10:50Z): final 62/90, −0.210755 SOL realized, 0 open. The wallet tied to the lamport on chain at 0.298774 SOL (#445). Helm withdrew 298,768,781 lamports to the owner's address 5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi at 2026-10-07T15:48Z (tx 3h1Fw9SpZ1H4YQZJhTiY38w9ZdBeVycomw7AWLmywRRwZu2UxC6ef8bGViooq9v8aXRww454HyyCb4ELN2BGiMDg). 298,768,781 sent + 5,000 fee = 298,773,781, the whole balance. **On chain the wallet is 0 lamports** with no token accounts (job #363, read-only, public address). The executor is inactive and disabled, mal-probe-watch.timer is disabled, and the STOP file stays in place (manager check, job #362). It never counts toward any gate. Corrected record: fixed tx fees do not explain faa3192's loss (−23,557,764 before tx fees over 33 trips), and size alone is not a profit path (DEC-020 §9). Two hosts: Oracle `mal-core-0` and OVH `mal-fast-0`. How to reach them: [docs/HOSTS.md](docs/HOSTS.md). History that used to live here is in [ARTIFACTS/daily/2026-09-28-manager-session.md](ARTIFACTS/daily/2026-09-28-manager-session.md) and [ARTIFACTS/daily/2026-09-28.md](ARTIFACTS/daily/2026-09-28.md).

## Objective

**Profit.** North star: durable **SOL after fees** on Pump.fun / Solana meme flow.

Path: full trade tape → honest simulator → signals → forward paper → gated live. Live starts only after a book clears the promotion gate **and** the owner approves.

Edge is information plus modest latency versus humans and copy-traders, not MEV / Jito / colocated snipers. *(2026-10-08 audit: at MAL's ~1.3–1.6 s event-to-landing this premise is refuted for the bonding curve, unselected migrations and KOL/app-follow. See the audit note.)*

## Hard fences

- Paper only, except the DEC-019 execution probe: its key is Helm's, root-owned on fast-0, and reaches the executor only through systemd `LoadCredential`. No agent reads it. **No other trading keys, wallet keys, or X keys** on either host.
- Postgres **localhost-only**. Never guess or commit the DB password.
- **Port 22 is never public.** SSH for agents is Cloudflare Access. **Stop on a host-key mismatch.**
- `/opt/miscusi` on `mal-fast-0` is a separate project, `vaanai/MiScusi`, led by the same Claude manager. Do not modify it from MAL work.
- Sealed **JSONL** is the provenance spine. Postgres is ops/state only ([DEC-002](DEC/DEC-002-memory-first-no-db-local.md)).
- GitHub is the source of truth for lab notes (`docs/`, `ARTIFACTS/lab/`, `ARTIFACTS/daily/`).

Non-negotiables: [CONSTITUTION.md](CONSTITUTION.md). Workflow: [DEC-012](DEC/DEC-012-tool-neutral-manager-workers.md), [DEC-013](DEC/DEC-013-claude-manager-merges.md).

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

## Latest confirmation result

**EXP-012 one-shot read: gate PASS (2026-10-01), backward simulated holdout only.**
- n = 451 over 7 UTC days (two half-days), 6/7 positive under both fail models.
- Flat mean 0.0349 SOL/trade (CI90 lower bound 0.0187); pressure 0.0205 SOL/trade (CI90 lower bound 0.0110).
- Ex-top-3: +14.04 SOL flat, +8.22 SOL pressure.
- **Not money made, not live-eligible.**
- Caveats:
  - The block is favourable to the unfiltered base trade (flat +1.45%, CI lo +0.42%, against −0.09% in the migrate-direct OOS fail).
  - Most of the lift is predicted fills (98.9% vs 28.0%), and the lift among fills is not significant.
  - Returns fall toward the present day.
- Next: an EXP-012 forward-paper book on `mal-fast-0` after the 2026-10-05T05:00:00Z kill review ([DEC-015](DEC/DEC-015-forward-paper-on-fast.md)). It must clear the gate on its own forward data, and then get owner approval, before live. Details: [EXP-012](EXP/EXP-012-migrate-entry-model-refreeze-prereg.md) Result.
- **V correction (2026-10-04, [#281](https://github.com/vaanai/MAL/pull/281), [#285](https://github.com/vaanai/MAL/pull/285); a correction analysis, not a new read).**
  - PumpSwap prices on quote vault + V, the virtual quote reserve (about 17.58 SOL on about 69% of pools). Our paper pricing ignored it.
  - Re-priced on the **spent** read block (already read; a post-hoc re-pricing, not fresh out-of-sample and not gate evidence), the same 451 entries still meet every gate condition numerically. Flat mean 0.02526; flat CI90 lower bound 0.00928; pressure CI90 lower bound 0.00604; 5/7 days positive on both legs.
  - Live support is judged on the V book (DEC-016 Amendment 4).
- **Entry latency under V (exploration, [#292](https://github.com/vaanai/MAL/pull/292)).** Pressure mean / CI90 lower bound by entry slot k:
  - k = 1: 0.01866 / 0.01114
  - k = 8: 0.00769 / 0.00117
  - k ≥ 12: every lower bound is below 0.
  - Entry speed is the largest measured sensitivity.
  - On the 9-day exploration pool the recipe was tuned on (winner's curse); exits are still at k = 1, so live decay is at least this.

## What is running

As of 2026-10-05 ~09:45Z.

| System | Where | Status |
| --- | --- | --- |
| EXP-012 forward walk (MiScusi #382, resubmitted 10-08 on `2bd45f1` / #454, slot-span bound 19,500) | `mal-research-0` | DEC-016. Through 10-08T10 at 10-08 ~12:45Z; 1,872,724 credits cumulative on the forward-1002 walk since 10-02T15 (includes #71, which ended before it). |
| getBlock tip follower `mal-fast-tip-follower` | `mal-fast-0`, system unit | The runner's feed (DEC-015 2.2, owner option A). Parallel fetch ([#297](https://github.com/vaanai/MAL/pull/297)), 8 workers, rps 15. Restarted 10-05 ~05:27Z on `d0109f7`. Since then: lag 2–3 slots, block-lag p50 about 1.7–1.9 s, 0 backlog jumps. Every PumpSwap row carries `virtual_quote_reserve` ([#288](https://github.com/vaanai/MAL/pull/288)). **Coverage against chain: 100.000%** over `[10-05T06, 10)` (job #131; Oracle 88.972%), so DEC-015 §2.2 passes. **About 560k Helius credits/day measured** (getSlot polling is about half). |
| Fast-0 paper runner `mal-fast-forward-paper` (EXP-012 book) | `mal-fast-0`, system unit | **Started 2026-10-05T05:31:40Z, on probation (rows do not count).** Reads the tip tape (`pumpswap_virtual: require`, [#296](https://github.com/vaanai/MAL/pull/296)). Heartbeat ok. Daily-restart and heartbeat timers enabled. Mid-week start, recorded here. **Mid-week restart 2026-10-05T17:44:41Z on `a25eb17`** (job #162). This deploy is #314 (`runner_kill` on intents), #316 and #317 (early arm on: `forward_paper_arm_v1` rows to `intents.jsonl`, audit to `arm-audit.jsonl`), and #315 (executor only). md5 proof: job #161, exact sha, fast config, arm and intents on, decisions and positions identical, EQUIVALENT. Also #159 (#316) and #154 (#314, Oracle config). Probation continues. |
| DEC-019 probe executor `mal-probe-executor` | `mal-fast-0`, user `mal-live` | **STOPPED 2026-10-07T01:10:50Z (owner, Helm STOP): final 62/90 attempts, 61 round trips + 1 failed buy, realized −0.210755 SOL, 0 open, build faa3192. Closing note [probe-final-2026-10-07.md](ARTIFACTS/lab/probe-final-2026-10-07.md): RT cost 4.44% / 2.79% / 2.59% of stake at 0.05 / 0.25 / 0.5 SOL; exit lag 2 slots (stops p50 2, p90 2, max 2); a measurement, not edge evidence.** Rows below are history as of 10-06. **2026-10-06T05:32Z (job #233): 45/90 attempts, realized −0.065655 SOL, 0 open, healthy.** faa3192 exits agree with the sim 16/16 (job #230); live−sim mean −640,641, median +34,540 lamports per trade. #348 (dec020 limits) and #358 (rung-2 stops) are merged but NOT installed (they wait for the 10-16 PASS, owner funding, and a Helm re-pin). **LIVE on pinned `faa319227eee420319eed06e85774f64dd2273b1` since 2026-10-05T23:09:56Z.** Root-owned `/usr/local/lib/mal-probe-exec`, Helm re-pin, 13/13 hashes. It runs the mark from our own buy tx (#331) and log-only drift (#332). Limits (DEC-019 Am.1 + 1a): 90 attempts, 0.05 SOL, max 3 open, loss cap 0.25 SOL, priority 500k, end 2026-10-12T00:00Z. At resume: 28/90, −0.153362 SOL, 0 open. Build history, never pooled: `8a6849b` (14:46Z), `a25eb17` (17:53Z, #307/#314/#315), `7004b16` (20:39Z, #324 own-buy double count), `faa3192` (23:09Z). Live-vs-sim execution check: [probe-calibration-2026-10-05.md](ARTIFACTS/lab/probe-calibration-2026-10-05.md). Helm runs a durable Discord watchdog (`mal-probe-watch.timer`). Size step: DEC-020 (proposal). |
| Migration stream probe (MiScusi #127) | `mal-fast-0`, transient unit | 6 h measurement of a processed `transactionSubscribe` on the pump migration authority against the tip follower. First 3 events: about 0.7–1.5 s earlier on the same slot. |
| Oracle forward-paper runner | Oracle `mal-forward-paper.service` | Code `d7485d2`, daily 00:00Z restart via the claude timer on `mal-fast-0`. Its 9 books were read at the kill review (below). |
| Fast listeners / two-socket public tape trial | `mal-fast-0` | Superseded as the runner's feed by the tip follower. The public tape failed coverage on 10-03 (93.869%). |
| Claude schedules | `mal-fast-0`, user `claude` | `mal-daily-review` 05:00 UTC, `mal-runner-daily-restart` 00:00 UTC. |
| Healthcheck | Oracle `mal-healthcheck.timer` every 5 min | `/var/lib/mal/eng/healthcheck.sh` |

## Current research state

- **10-08 afternoon (manager9): owner decisions and EXP-022.**
  - **Owner decisions:** the owner approved the audit plan (notebook n_vS9qHGmF7-jinQ).
    - The EXP-021 screen is paused.
    - CAP-PICK is pre-registered as **EXP-022** ([#464](EXP/EXP-022-cap-pick-part1-prereg.md), with DEC-021 Amendment 1). It counts walk-2 hours `[2026-10-16T01, 2026-11-06T01)`, with looks at 10-23T01, 10-30T01 and 11-06T01. P(pass) is about 5–7% [inferred], and a fail leads to the pre-agreed wind-down.
    - Counting starts 10-16T01 (Option Y), not 10-10. Under the early start, the 10-16 FINAL would have priced most of the counted picks.
    - The sniper / same-slot route is closed for now, and the extra budget for two weeks is $0.
  - **Merged:** #458 (EXP-021 freeze pins), #459 (DEC-016 Am.6 record), #460 (audit note), #463 (probe calibration reserve fix), #464, #466 (walk-2 integrity: refuses silent holes; FINAL path byte-identical by default) and #467 (walk-2 event-V decoder).
  - **Scorer port** (draft #461): reproduces the audit book on 24,272 attempts, P2–P4 exact to the lamport (job #386). The phase-2 spec switches are reviewed.
  - **Edge search:** 16 alternatives, 11 killed and 5 weakened; none survives (notebook n_1fzsuRLAhvkgqg).
  - **Same-slot speed:** 94.5% (Aug) / 97.5% (Sep) of first-follower value is in the token's creation slot. Outside it, the first follower makes +2.10% / +0.29% with bundle-like co-landing excluded [measured, Aug/Sep exploration, first seat only]. MAL lands a median 5–6 slots late, and break-even needs the first seat on 36.6–68.9% (Aug) / 87.9–93.9% (Sep) of tries.
  - **Tape finding:** 95.35% of canonical migrations lack a `migration` row, because migrate-tx logs go over 10 KB. 24,224 of the 24,272 have a `complete` row (99.80%), and 23,099 of the 23,143 that lack a migration row do (99.81%) (notebook n_6FKDVJWrbaoGYg). So the universes are unaffected [inferred]. #467 recovers the rows for walk 2.
  - **Next:** see docs/HANDOFF.md for the EXP-022 deadlines. Before 10-16T01: E0, which needs the read-ready gate replay AND the read-ready scorer (#461, #462 with strict lines); the rest of A8; walk 2 started; a clean monitor run. Before 10-23T01: the full read tool, then E1 (n ≥ 20), then A2.
- **10-08 (manager9): owner-requested profitability audit** ([note](ARTIFACTS/lab/audit-2026-10-08/README.md); exploration pools only, not gate evidence).
  - **Thesis refuted at MAL's ~1.3–1.6 s event-to-landing:**
    - bonding curve: 0 of 3,168 tradeable cells positive;
    - unselected migrations: 0 of 1,008 robust at probe fees;
    - KOL copier at +5 slots: 36 of 36 negative;
    - FOMO retail: 0.79% / 0.81% of buy SOL by 10 s.
  - **The 10-01 EXP-012 PASS was an operating-point artifact:** +6.973% at k1 START without V, against +0.145% flat at k6 END, V, lag 2 and haircut, on the same 451 rows.
  - **CAP-PICK** (EXP-012 picks + seed×1.15 min_out + 300 s cap + 55k per send):
    - the judge's verdict is HOLDS_WEAKER; judge's inferred estimate: Aug–Sep ≈ +1.5% per attempt, live leg (≈ +1.3 flat / ≈ +1.1 pressure at 0.1 SOL); October ≈ +0.3 to +0.5% (flat ≈ +0.3), P(≤0) about 40%, below running cost at 0.1–0.25 SOL;
    - P(pass) about 10% (5–20%) with DEC-021's trade-level p; about 5–7% with the day-level p proposed in O2 [inferred, judge §2.5];
    - Top-day concentration: per-block top-day shares are 63–78%. On P1, the latest pre-October block (out-of-fold on adjacent days, its threshold cut on the same scores), ex-best-day is −0.29 SOL (live leg, tradable picks) and −0.034 SOL at the deciding cell; oracle is −0.985%;
    - it is the only candidate; the plan is a kill test whose read costs 0 credits (the walk-2 tape is expected at about 18k credits per hour).
  - **Merged:** #454 (slot-span bound), #455 (v1 transactions), #456 (daily pump structure monitor), #457 (Console), #458 (EXP-021 freeze pins), #459 (DEC-016 Am.6 record).
  - **EXP-021 freeze #376 done:** RUG md5 `3a0e9e76d94fb6c0b1a9cd22be00970a`, CONTROL `98472502dfbc448fbecb6115f8f62ab1`. The screen is **not run**; owner decision O1 (pause) is pending.
- **10-07 (manager9):** EXP-020 size x entry-slot grid, report-only (jobs #347 start bound at `8bce9ed`, #350 END bound at `a51000c`; not gate evidence, no edge claim): start bound k2 at 0.25 SOL and above looked positive with ex-best-date > 0 (flat 0.5 SOL k2 mean 0.010803 SOL/trade, total 25.3754, ex-best-date +15.1177). Quant-proof: the selector is causal at k2 (all 18 features from before migration); the start bound flatters fills and 2026-08-21 carries 40 to 43 % of the k2 flat totals. END bound: the paired k2 - k6 gain stays positive (0.5 SOL flat +1.023 % of stake, CI90-date [0.109, 1.851]; pressure +0.747 %, [0.183, 1.276]) but every k2 kept book has ex-best-date < 0 (flat 0.25 -2.0312, 0.5 -1.3473; pressure 0.25 -2.0854, 0.5 -2.2641). Faster entry is a robust relative lever, not profit on its own; real fills land between the two bounds, depending on slot position. These 27 dates have had 100+ tries. Next: combine with EXP-021 in one pre-registration with an END-bound co-primary, and calibrate live k2 landing. No `result.v1` record. See [exp020-grid-2026-10-07.md](ARTIFACTS/lab/exp020-grid-2026-10-07.md).
- **10-07 (manager9):** EXP-014 v2 (mig+15 PumpSwap selector) FAILED its one-shot screen (job #338, code `cef474a`): SCREEN NONE, family closed (plan item 14). Every gating bar fails on both exit legs (lag 2 and pinned lag = d), Holm p 1.0; bar 1 flat n 2,496, mean -0.0012437746370192308 SOL/trade, CI90 [-0.0017183903787860576, -0.0007326262378004808], total -3.104461494, 9 of 27 dates positive; report-only at 0.5 SOL mean -0.004047897272035256. Exploration, no edge claim; not a `result.v1` record. See [exp014-v2-screen-2026-10-07.md](ARTIFACTS/lab/exp014-v2-screen-2026-10-07.md).
- **10-06 night (manager9):** EXP-017 batch 1 (H3 regime gate, H4 score sizing on frozen EXP-012) FAILED its screen (job #337, code `200cad0`): SCREEN NONE, nothing goes to confirmation. H3 n 621, paired p 0.48605139486051396; H4 n 2,349, paired p 0.3955604439556044; B1 to B5 fail on both cells and B6 on H4 passed on the flat leg only (mean +0.000609639238425926, n 864, 7 of 13 dates), so no cell passed all bars; H4 did not beat uniform 0.10 SOL (flat -0.757406444 vs +0.208016013; pressure -1.104652902 vs -0.725846069). C0 (report-only, 27 already-read dates, 7 families have scoped them, EXP-017 is the 4th to read outcomes after EXP-015, EXP-018 and EXP-019; per-pool prior tries 81/18/10/10): 0.5 SOL flat mean +0.0036088916219667944 SOL/trade, CI90 [-0.002717209018454662, +0.010848846253746275], total +8.47728642, ex-top-3 +5.635029087, 13 of 27 dates positive; pressure mean +0.001149852399318859, CI90 [-0.0029114594565134095, +0.005465274088761175], total +2.701003286, ex-top-3 +0.969480593, 13 of 27. No stake clears the gate (CI lower bound below 0, positive days not a majority); 0.5 SOL is the only stake with ex-top-3 positive on both legs, but the C0 size result rests on one date: dropping 2026-08-21 turns every positive C0 total negative (0.5 flat +8.47728642 alone-date +9.187248662 without -0.7099622419999996; 0.5 pressure +2.701003286, +5.534053771, -2.833050485; 0.25 flat without -0.9453552730000001; 0.25 pressure without -2.013247782). Input to DEC-020 sizing: size improves the per-trade sign from fixed-fee dilution; not evidence of edge. Exploration, no edge claim. See [exp017-screen-2026-10-06.md](ARTIFACTS/lab/exp017-screen-2026-10-06.md).
- **10-06 evening (manager9):** EXP-014 v2 (mig+15 PumpSwap selector screen, exploration, no edge claim): plan merged (Amendment 7, [#430](https://github.com/vaanai/MAL/pull/430)), precount clean, **screen pending**, no try spent, no outcome read. A pre-read addendum (owner's external reviewer) makes the pinned lag = d exit gate alongside lag 2, so the precount digest changed and `--precount` is rerun before the screen. It is the 7th family scoped to the 27 non-P1 dates: 3 have spent their try (EXP-015, EXP-018, EXP-019, all failed) and 3 have not read an outcome yet (EXP-016, EXP-017, EXP-020 report-only).
- **10-06 evening (manager8):** EXP-019 (post-migration flow confirmation at k8) FAILED its one-shot screen (job #328): SCREEN NONE, family closed. Cell A paired vs frozen k6: flat mean x -0.0005277499177288448 SOL, p 0.9715028497150285; A book flat mean -0.0013183461738122827 SOL/trade over 1,726 trades, 9 of 27 dates positive; not MISS-driven; B1 to B6 all FAIL. Exploration, no edge claim. See [exp019-screen-2026-10-06.md](ARTIFACTS/lab/exp019-screen-2026-10-06.md).
- **10-06 evening (manager8):** EXP-018 (causal wallet skill at migration) FAILED its one-shot screen (job #325): SCREEN NONE, family closed. W2 paired vs frozen EXP-012: flat mean x +0.000004146408 SOL, p 0.46275; W2 book flat mean -0.0010193 SOL/trade, 6 of 22 dates positive; B1 to B6 all FAIL. Exploration, no edge claim. See [exp018-screen-2026-10-06.md](ARTIFACTS/lab/exp018-screen-2026-10-06.md).
- **10-06 afternoon (manager8):**
  - **V0 LP law.** V0 changes only on PumpSwap Deposit/Withdraw: V0 ← floor(V0·S_after/S_before) (#400). The 10-16 (B) rules are in DEC-016 Am.5 §7, and the tools are merged (#402, #405, #411).
  - **EXP-016.** Pre-read fixes merged (#406, #414). The real-layout `--precount` exposed two memory problems: an OOM at 48 GB, then whole-row fallback. Both are fixed; the V-map pin waits on a clean precount.
  - **Stale wording.** The "first positive" cell in older docs is the migrate-direct cell: best of 972 in-sample, and it FAILED out of sample ([migrate-direct-oos.md](ARTIFACTS/lab/migrate-direct-oos.md)).
  - **Frozen EXP-012 at realistic costs.** At 0.05 SOL it is −0.000439 SOL/trade flat on 27 non-P1 dates (exploration). The threshold is not moved before the FINAL.

- **10-06 update (exploration; nothing here is gate evidence):**
  - **EXP-012 back-check on explore-0814 (#342/#346): REFUSED_AFTER_READ** (job #207). 1 primary trade was on a pool with no V: 30 no-V pools, 29 absent from `pool_v_0814` plus 1 closed account. No reading and no rerun; 6 tries logged (explore-0814 count 7). [Result](EXP/EXP-012-backcheck-0814.md).
  - **Root cause:** every V map was built only from the pools of mints migrating in a view. The same gap refused **EXP-013 #225** at its pre-pass (1.59% / 675 pools; no outcome and no try spent).
  - **Fix:** `pool_v_0909.json` (job #232) covers every PumpSwap pool printed in all research views: 226,073 pools, 321 null under the old parser (V0 = 0 pools, not closed; [#384](https://github.com/vaanai/MAL/pull/384)), sha `70914a16…b42e`. All 23,477 non-null V values from 0814 are unchanged. Pinned for EXP-013 and EXP-015 in [#371](https://github.com/vaanai/MAL/pull/371).
  - **EXP-015** (pooled retrain, plan v2 [#352](https://github.com/vaanai/MAL/pull/352), tooling #356/#361/#363) is ready to screen once the pin lands. It is a DEC-021 challenger only if it passes fresh-0808 and registers before 10-16T01.
- **EXP-012:** one-shot PASS (replay), as above; *the 10-08 audit found it an operating-point artifact (see the 10-08 entry). The Console ladder marks EXP-012 retired.* Forward book per DEC-016: one FINAL read over `[2026-10-06T00, 2026-10-16T00)`, at about 10-16T02Z. No peeking (Amendment 2).
  - **Amendment 3 ([#256](https://github.com/vaanai/MAL/pull/256))** fixes the live-support rules before any runner row exists. Live is supported only if the re-score at the measured fast-0 latency k(p50), with the owner's trial terms, clears the full gate under both fail models; at k(p90), mean > 0 and ex-top-3 > 0 are required. Runner-vs-scorer rows 0–6 must also hold.
  - Tooling merged: [#257](https://github.com/vaanai/MAL/pull/257) (latency export, replay rows 0–2), [#258](https://github.com/vaanai/MAL/pull/258) (forward sensitivity re-score), [#259](https://github.com/vaanai/MAL/pull/259) (heartbeat sampler and downtime).
  - The owner was asked for the trial terms (due 10-15T12Z). Defaults: 0.5 SOL, 3 concurrent, 500k lamports, no tip.
- **DEC-017:** no secondaries. All three candidates failed ([#242](https://github.com/vaanai/MAL/pull/242)).
- **EXP-013 graduation classifier (exploration):** [plan](EXP/EXP-013-graduation-classifier-plan.md), Amendments 1–5. Amendment 5 ([#252](https://github.com/vaanai/MAL/pull/252), quant-proof reviewed) fixes how each screen item is computed.
  - Tooling merged: [#244](https://github.com/vaanai/MAL/pull/244), [#251](https://github.com/vaanai/MAL/pull/251) (model plus nested LODO), [#253](https://github.com/vaanai/MAL/pull/253) (screen), [#254](https://github.com/vaanai/MAL/pull/254) (run script).
  - The screen runs **exactly once**, after 2026-10-04T12:00Z, on the 9-day pool plus every explore-0814 view verified by then (`scripts/research/exp013-screen-run.sh`).
  - The debug table build (#88, table only, no model) took 51 min for 9 days plus w1: 59,821 rows.
  - A PASS leads to a pre-registration on the backup block `[2026-08-28T12, 2026-09-03T12)` with k = 1. A FAIL closes the family. The honest prior is about 20% or less.
- **Frozen migrate-direct cell is dead** (formal FAIL, 2026-09-28T21:00Z one-shot, both fail models, every CI lower bound < 0). Not refit. [Detail](ARTIFACTS/lab/migrate-direct-oos.md).
- **EXP-011: closed NOT_DECIDABLE (2026-09-30).** The read aborted on a sealed-but-empty hour; holdout spent. Walker integrity bugs were the cause. [Result](EXP/EXP-011-migrate-entry-model-prereg.md).
- **EXP-009 (creator gate)** is a screen, k = 1 ([#154](https://github.com/vaanai/MAL/pull/154)); paused by the owner while the Console is built.
- **Exploration entry-model B3** ([#156](https://github.com/vaanai/MAL/pull/156)): exploration only, not a promote. Clean-data re-check: [note](ARTIFACTS/lab/exploration-entry-model-b3-clean-2026-10-01.md).
- **Exits are dead** ([#151](https://github.com/vaanai/MAL/pull/151)); **fee tiers are a dead end** ([#142](https://github.com/vaanai/MAL/pull/142)).
- **Lesson:** the typical migrate entry loses; entry selection is the lever. Always check ex-top-3.

## Kill review — 2026-10-05T05:00:00Z

Runbook: [docs/runbooks/kill-review-2026-10-05.md](docs/runbooks/kill-review-2026-10-05.md) ([#255](https://github.com/vaanai/MAL/pull/255)): snapshot job on mal-fast-0 → manifest sha → score job on research-0.

Single read, once, at or after that instant, on a snapshot — never on a live, growing `positions.jsonl`. Scorer: `tools/kill_review.py`, with `tools/forward_paper_settle_orphans.py` for restart-dropped opens and `tools/forward_paper_pressure_stamp.py` for the pressure leg.

- Flat leg counts full UTC days from the 2026-09-28T00:00:00Z clean clock.
- Pressure leg counts full UTC days from **2026-09-29T00:00:00Z** (first daily restart on code ≥ `d7485d2`, DEC-014 Amendment 3).
- Each leg must clear the gate on its own; fewer than 5 eligible days on a leg is NOT_DECIDABLE, not a pass.
- If the pressure stamp can't cover a book (missing pnl field, a `pressure_error`, an unresolved restart-orphan, or `settle_failed`), that book is NOT_DECIDABLE (DEC-014 Amendment 2).
- Holm–Bonferroni across the 9 books (DEC-014). No new forward books during the kill-review week.

### Result (read 2026-10-05, single read)

**VERDICT: PROMOTE = none, KILL = none, NOT_DECIDABLE = all 8 Holm-family books.**
- `buy_all` is the reference book, not a candidate.
- Holm k = 8, 10,000 draws, seed 1.
- Every book is INCOMPLETE: open orphans with no close or settlement, and some `settle_failed`. Under DEC-014 Amendment 2 that is NOT_DECIDABLE, not a measured KILL.

**The point estimates are one-sided.** Every book has a negative mean with the CI90 entirely below 0 under both fail models, and 0 positive flat days out of 8. Values are floored, so a loss is never shown smaller.

| Book | n (flat) | flat mean | flat CI90 | press mean | press CI90 | days + (flat) | orphans | settle failed | status |
| --- | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | --- |
| laya_0.6 | 135,542 | −0.00350 | [−0.00369, −0.00329] | −0.00311 | [−0.00329, −0.00293] | 0/8 | 478 | 0 | NOT_DECIDABLE |
| laya_0.7 | 54,395 | −0.00357 | [−0.00380, −0.00333] | −0.00319 | [−0.00337, −0.00299] | 0/8 | 465 | 2 | NOT_DECIDABLE |
| migrate_hold_30s | 5,747 | −0.00256 | [−0.00318, −0.00188] | −0.00227 | [−0.00268, −0.00184] | 0/8 | 502 | 0 | NOT_DECIDABLE |
| migrate_tp50_sl30 | 5,746 | −0.00331 | [−0.00384, −0.00277] | −0.00262 | [−0.00295, −0.00226] | 0/8 | 412 | 2 | NOT_DECIDABLE |
| attn_first_hold_60m | 3,123 | −0.02250 | [−0.02459, −0.02048] | −0.01904 | [−0.02054, −0.01739] | 0/8 | 112 | 20 | NOT_DECIDABLE |
| t30_top1_hold30 | 2,439 | −0.00320 | [−0.00377, −0.00263] | −0.00288 | [−0.00331, −0.00246] | 0/8 | 416 | 0 | NOT_DECIDABLE |
| buyers8_top5_ladder2x | 1,775 | −0.00719 | [−0.00857, −0.00578] | −0.00541 | [−0.00651, −0.00427] | 0/8 | 162 | 3 | NOT_DECIDABLE |
| mig15_top20_tp50_sl30 | 998 | −0.00563 | [−0.00699, −0.00426] | −0.00516 | [−0.00624, −0.00405] | 0/8 | 259 | 3 | NOT_DECIDABLE |

**Provenance:**
- Snapshot manifest sha256: `399bf566f15a228360f59129f2ddf38a99658151da5ed0ea5f5b1ea5ceaa4e1c` (209 files: 197 tape hours, 9 creates days, 3 state files; nothing missing).
- Jobs:
  - #108: snapshot.
  - #118: score, out of memory at 32 GB in the pressure stamp, before the read.
  - #120: blocked by a read-only leftover; its partial output was kept at `/data/mal/kill-review-1005/out.oom-118`.
  - **#121**: the read. Pressure stamp in 4 mint chunks, [#299](https://github.com/vaanai/MAL/pull/299); its output is identical for any chunk count by test.
- Settlements are byte-identical across #118 and #121.
- `kill_review.json` sha256 `c66d6a5a798d77fe6fe0563a9319efeb7aa22a8ef5f9d76615f6f9cd66fdfde3`. Totals: 329 settled offline, 30 settle failures, 2,806 open orphans.

**Settle failures.** `settle.log`: 470 orphan settle failures (all books incl. buy_all, any time). By `decision_t_ms`: 424 before the void end 09-27T06:58:12Z (394 `no_tape_for_mint`, 25 `missed_no_state`, 5 `tokens_mismatch`; the snapshot tape starts 09-27T00 by design); 4 `tokens_mismatch` between the void end and the window start; 32 inside the window [09-28T00, 10-05T05): 19 `censored_tape_too_short` (entries from 10-05T04:04Z, exits past the tape end), 11 `tokens_mismatch`, 2 `missed_slippage`; 10 after 05:00Z (6 `tokens_mismatch`, 2 censored, 2 `venue_mismatch`). The scorer counts 30 settle_failed across the 8 candidates.

**Caveat: all 9 books are priced without V.** The PumpSwap books (migrate_tp50_sl30, migrate_hold_30s, mig15_top20_tp50_sl30) cannot be promoted on these numbers in any case.

**Manager reading.** None of the 8 shows an edge. They get no more work; the formal status stays NOT_DECIDABLE. EXP-012 remains the only candidate. *(Superseded 2026-10-08: CAP-PICK, which re-uses EXP-012's picks, is now the only candidate. See the audit note.)*

## Promotion gate

At least **100** out-of-sample trades, at least **5** distinct UTC days with a majority of those days positive, lower **90%** CI bound of mean SOL per trade **> 0**, and total SOL still positive after removing the top 3 trades. The book must clear that bar under **both** the flat 15% fail rate and the pressure-fail model at slope scale 1. Bootstrap: 1,000 draws, seed 1. The lower bound is the 5th percentile of those means.

**Multiplicity ([DEC-014](DEC/DEC-014-holdout-ledger-and-multiplicity.md)):** when k ≥ 2 candidate books or cells are read together at one review (e.g. the 9 forward books on 2026-10-05), a book promotes only if it also passes a Holm–Bonferroni step-down at family α = 0.05 on the one-sided bootstrap p-value (share of bootstrap means ≤ 0), under both fail models: the smallest p is compared to α/k (0.05/9 ≈ 0.0056 for k = 9), the next to α/(k−1), and so on. Multiplicity-tested reads use **10,000** bootstrap draws, seed 1. Reference cells are not in k, and a single pre-registered primary cell is k = 1.

Live bar, still required after the gate: about 7 days of forward paper, then tiny size, and an explicit owner yes. The owner's dollar target is not evidence. See [CLAUDE.md](CLAUDE.md).

## Holdout ledger

[docs/HOLDOUT_LEDGER.md](docs/HOLDOUT_LEDGER.md), adopted by [DEC-014](DEC/DEC-014-holdout-ledger-and-multiplicity.md): one owner per historical block, non-owner reads disclosed in the owner's `EXP-###` file, rows written before hours are sealed or read.

## Forward-paper stale-fill void and clean clock

Rows with a decision time in **2026-09-25T19:00:00Z → 2026-09-27T06:58:12Z** do not count for promotion (the runner was behind the tape; [#102](https://github.com/vaanai/MAL/pull/102) drops those fills and charges a flat 15% miss).

Clean clock: **2026-09-28T00:00:00Z**. Kill review: **2026-10-05T05:00:00Z**.

**Seal exposure disclosed (2026-10-07, manager9; recorded in DEC-016 under Amendment 2).** The early-arm latency read (job #368) printed per-day early-arm outcome counts for 10-05 to 10-07: armed, fail, skipped_migrated, and a lumped risk/kill skip count. The risk reasons include `daily_loss_cap`, which the DEC-016 Am.2 seal extension covers.
- **Not exposed:** no P&L, cost or exit field was read. Job #368 lumped the risk reasons before printing, so no loss-cap-specific count was printed.
- **Where the counts went:** PR #451's first head `a59ff80` (still readable in the PR's commit list), the PR body's edit history, MiScusi notebook n_6W8jWsyz1d8aXw, and the first quant-proof review of `a59ff80`, which restated them. They are not on `main`, and later notes do not repeat them.
- **Effect:** under the Amendment 2 rule, the 10-16 FINAL read **will be reported as compromised**.
  - Its result cannot, by itself, support a live request. A PASS first needs confirmation on a later fresh sealed window or block under DEC-014.
  - The pre-registered computation is unchanged and still runs as written. The FINAL report carries this disclosure.
  - *Corrected 2026-10-07 after a review by the owner's reviewer: an earlier wording left the label to the owner, which relaxed a fixed consequence after the breach. Any different consequence needs a new dated amendment that gives its reason.*
- **Rule from here:** early-arm reads report timing only, with no per-day outcome counts, until the read. See [fast-entry-trigger-2026-10-07.md](ARTIFACTS/lab/fast-entry-trigger-2026-10-07.md).

## Open PRs

[#90](https://github.com/vaanai/MAL/pull/90): keep. `JobQueue` on `main` is still unbounded; the stale-drop and credit cap in this draft are not in the tree.

## Next work

**As of 2026-10-08 ~16:00Z, the authoritative list is the EXP-022 deadline table in [docs/HANDOFF.md](docs/HANDOFF.md).** The 10-08 ~13:00Z list below is kept for the record. O1–O3 and O6 are now decided; O8 and O9 are still with the owner.

**As of 2026-10-08 ~13:00Z (supersedes the 10-06 list below):**
1. **Owner decisions O1–O9** (Console for_you `fy-20261008-1029-audit`). They gate everything else here. Numbering: Console O1–O5 = SYNTHESIS O1–O5; Console O6 (wind-down) = SYNTHESIS/judge O7; SYNTHESIS O6 (close EXP-009) is not in the Console list; Console O7–O9 are new.
   - O1: pause the EXP-021 screen.
   - O2: a DEC-021 amendment for a CAP-PICK walk-2 arm with a day-level p.
   - O3: count `[10-10T00, 10-31T00)`, sealed until the 10-16 FINAL is written (second owner of forward-1002 hours `[10-10, 10-16)`); the CAP-PICK Part 1 must merge before 10-10T00Z.
   - O6: pre-agree a wind-down.
   - O8: the bill split.
   - O9: Helm excludes `/data/mal/audit-1008` from backup.
2. **CAP-PICK scorer port** `tools/cap_pick_score.py` (branch `claude/cap-pick-score`, a draft). It must reproduce the audit's book to ≤0.01 pp. Then come the judge's 10 spec fixes (`capv_JUDGE.md` §4). The Part 1 pre-registration merges only after the owner's O2/O3.
3. **10-16 FINAL** runs as written (about 10-16T02Z). It will be reported compromised (Am.2), and gives no live support through Am.3 (a) (Am.6 (g) D). Per the audit, no new FINAL tooling. The (e′) dry run is still required before 2026-10-15T23:00Z.
4. **Daily pump structure monitor** (06:41Z). If a halt rule fires, CAP-PICK counting and any live trading are suspended the same day; hours after the trigger are excluded and the read can end NOT_DECIDABLE, with no retune and no re-read (SYNTHESIS A3, line 281). Before counting starts, a halt means withdraw (judge §5). Two consecutive days with a core rule not evaluated count as a halt until BOOST is checked by hand.

*Older list, as of 2026-10-06 ~05:50Z (EXP-012-era critical path, superseded):* EXP-012 FINAL → fresh sealed-block confirmation under DEC-014 → book (B) → Am.3 at measured k → DEC-020 package → owner.

1. **Book (B) tooling, critical path.** No code computed DEC-016 Am.4's V-priced forward book. Two builders started 10-06:
   - `claude/exp012-forward-vbook`: book (B) from the FINAL's own entered set; frozen reproduction, null-V rule, vault report;
   - `claude/exp012-forward-vmap`: the forward-window V map from every printed pool, plus early snapshots so closed pools keep a V.
   Both must merge, with quant-proof, before 10-16T00Z. The Am.3 sensitivity re-score on (B) comes next.
2. **V map pin [#371](https://github.com/vaanai/MAL/pull/371):** `pool_v_0909.json`, sha `70914a16…b42e`, 226,073 pools, 321 null. Then the EXP-013 rerun (3 tries, none spent), then EXP-015 `--guards-only`, then the EXP-015 run. EXP-015 must register before 2026-10-16T01 to join DEC-021 walk 2.
3. **Probe:** hourly monitor (session cron at :17; Helm's `mal-probe-watch.timer` is the durable alarm). Calibrate at ≥53 attempts. Hard end 2026-10-12T00Z, then the DEC-019 §7 lab note by build, then quant-proof.
4. **#205** grad-stream at threshold 0.70: run the compare on research-0 after it ends.
5. **10-07T18Z:** early-arm shadow read. **By 10-09T15Z:** resubmit forward walk #71.
6. **Fee vs landing v2 (#216, lab note [#372](https://github.com/vaanai/MAL/pull/372)):** cheap no-tip buys land early most often. No fee change.

## Pointers

- Hosts: [docs/HOSTS.md](docs/HOSTS.md)
- Manager handoff (current): [docs/HANDOFF.md](docs/HANDOFF.md)
- Claude handoff: [CLAUDE.md](CLAUDE.md), [docs/MIGRATION-TO-CLAUDE.md](docs/MIGRATION-TO-CLAUDE.md)
- Research options: [docs/research/](docs/research/)
- Daily briefs: [ARTIFACTS/daily/](ARTIFACTS/daily/)
- Lab notes: [ARTIFACTS/lab/](ARTIFACTS/lab/)
- SSH: [scripts/mal-core/agent-ssh.sh](scripts/mal-core/agent-ssh.sh), [tools/oracle_ssh_smoke.md](tools/oracle_ssh_smoke.md)
