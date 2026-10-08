# Manager handoff 2026-10-08 ~18:00Z (manager9 → next manager, or manager9 after compaction)

Replace this page at the next handoff; don't append. Read it first. Then read:
- [EXP-022 CAP-PICK Part 1](../EXP/EXP-022-cap-pick-part1-prereg.md), §0, §2.1, §9, §12 and §17, **Amendment 1 (the E0 deciding set)**, plus [DEC-021](../DEC/DEC-021-champion-challenger.md) Amendment 1;
- the 10-08 audit note [ARTIFACTS/lab/audit-2026-10-08/](../ARTIFACTS/lab/audit-2026-10-08/README.md), the judge (`capv_JUDGE.md`) first;
- [LAB_STATE.md](../LAB_STATE.md);
- DEC-016 Amendments 2, 5 §7 and 6;
- DEC-014's 2026-10-07 block-budget amendment;
- memory notes `owner-goal-october`, `feedback-fan-out`, `audit-2026-10-08`, `exp022-cap-pick`, `owner-plan-1008`, `feedback-reserve-convention-brief`, `feedback-pinned-exit-gates`, `feedback-seal-outcome-counts`;
- MiScusi notebook entries from 10-08: n_yBVJLKQFcVuUTg, n_20cYMHbO0UyGzA, n_1fzsuRLAhvkgqg, n_6FKDVJWrbaoGYg, n_vS9qHGmF7-jinQ, n_JESu6g8jAbyesA.

## Where things stand

### Owner mandate (10-08 ~17:00Z; memory `owner-goal-october`, `feedback-fan-out`)

These are the owner's instructions and targets. They are not measurements.
- **Goal:** real income of **$1000 by 10-31**, **$400 minimum**. The wallet goes to **7 SOL from a 1 SOL start**, funded once a book clears the gate.
- **Full autonomy.** Fan out many parallel strategy tests.
- **The gate is kept.** Nothing goes live before a book clears the promotion gate on both fail models.
- Earlier on 10-08 (memory `owner-plan-1008`): the sniper / same-slot route is closed for now, and the extra budget is $0 for two weeks. The mandate does not say it changes either; treat both as unchanged until the owner says otherwise.

### Owner decisions earlier on 10-08

- **Audit delivered.** It is the claude.ai artifact "MAL Profit Audit". The fast-entry retail thesis is refuted at MAL's ~1.3–1.6 s landing.
- **Owner approved the recommendations**, notebook n_vS9qHGmF7-jinQ:
  - O1: the EXP-021 screen is paused. The freeze pins are in #458; fresh-0802 stays unread.
  - O2: DEC-021 Amendment 1.
  - O3: taken with its fallback. **CAP-PICK counts from 2026-10-16T01 (Option Y).**
  - O6: the cost wind-down is pre-agreed if CAP-PICK fails.
  - O4/O5: deferred.
- **CAP-PICK is EXP-022, pre-registered** (#464, 30aafef; quant-proof OK on the final head).
  - Counted window: walk-2 hours `[2026-10-16T01, 2026-11-06T01)`. Looks at 10-23T01, 10-30T01 and 11-06T01.
  - Deciding rule: day-level t on 24 h blocks at p ≤ 0.005 / 0.008 / 0.012, plus the full gate on both legs, plus ex-best-day > 0, plus the binding 1.9 s leg.
  - P(pass) about 5–7% [inferred]. **A fail is the expected outcome** and leads to the wind-down.
- **Research results** (notebook n_1fzsuRLAhvkgqg):
  - 16 alternative meme strategies: 11 killed, 5 weakened, none survives.
  - Same-slot: 94.5% / 97.5% of first-follower value sits in the token's creation slot. Outside it: first follower +2.10% / +0.29% (bundle-like co-landing excluded) [measured, Aug/Sep exploration, first seat only], second −1.21% / −2.11%. MAL lands a median 5–6 slots late; break-even needs the first seat on 36.6–68.9% (Aug) / 87.9–93.9% (Sep) of tries.
  - Reports: `/data/mal/audit-1008/work/edge-scan-1008/REPORT.md` and `/data/mal/audit-1008/work/speed-recon-1008/PLAN.md`.
- **Data finding** (notebook n_6FKDVJWrbaoGYg): 95.35% of canonical migrations have no `migration` row on the walker tape, because migrate-tx logs go over the 10 KB cap. 24,224 of 24,272 have a `complete` row (99.80%), and 23,099 of the 23,143 missing a migration row do (99.81%). So the CAP-PICK and EXP-012 universes are fine [inferred]. #467 recovers the rows under `--event-v`.
- **Still with the owner:** O8 (the monthly bill split) and O9 (Helm excluding `/data/mal/audit-1008` from the nightly backup).

### Merged today since the last handoff

- **#470** T1 pump.fun change watch plus the EXP-023 USDC-BOOST tripwire plan (quant-proof OK on a8ab1c3).
- **#472** walk-2 wrapper guards: an empty Helius key exits 6, and the test hook inside a job exits 2. (The wrapper itself, #469, merged before it.)
- **#474** EXP-022 Amendment 1, the E0 deciding set: mints with mig − create ≤ 60 min on either side, plus every B pick. E0 day is explore-0814, 2026-08-20. Quant-proof OK on 3ece9c0.

### Job #387 result: NOT a kill

The gate-replay kill check (#462 at 8fd49e5) passed. The ≤ 32 min pick sets match. The P2–P4 online-pick book is **+3.278% live [CI lo +1.17]**, **+2.334% press**, positive on 19 of 29 days. This is exploration, in-sample, and not gate evidence. The known 60-min skip is not a kill; Amendment 1 sets the E0 set at ≤ 60 min. Output is in `/data/mal/cap-pick-score/gate-replay/` (compare.md, compare.json).

### A11: not adopted (notebook n_JESu6g8jAbyesA)

There is no A11 October report-only check. So #465 stays parked unless the read tool guards its reads through `check_read`.

### Strategy hunts (exploration tape only)

Protocol for every hunt: Aug discovery → `RULE.md` sha256 → one Sep confirmation → adversarial verify → judge. Results go to `/data/mal/hunt-1008/JUDGE*.md`, and reports are copied to `/data/mal/hunt-reports`. A hunt is exploration. It is not gate evidence, and a survivor still has to clear the promotion gate.

| Hunt | Workflow | State |
| --- | --- | --- |
| hunt-1 (H1–H6) | wf_1b74c222-805 | Running |
| hunt-3 LAYA (L1–L3) | wf_4f61363d-351 | Running |
| LLM-trader test: Claude agents as the trader | wf_3b8d9808-907 | Running |
| LLM-trader OpenRouter arm | `/data/mal/hunt-1008/llm-trader/openrouter` | Ready. Free model `nvidia/nemotron-3-ultra-550b-a55b:free`, paid `deepseek/deepseek-chat`. **Budget hard stop $1.50.** Key at `/var/lib/mal/openrouter/openrouter.env`, never printed. |
| hunt-2 (12 theses) | wf_84831245-793 (task w9v7nomhy) | **RESUMED at about 18:05Z** under the new disk rules, at most 4 at once, same run id. Each investigation continues from its existing files. It uses the shared layer if `/data/mal/hunt-shared/README.md` exists, and otherwise queries the tape directly with no copies. |
| hunt-4 (cascades + strategy tree) | wf_0d1a560e-ebe | **STOPPED for disk.** Resumes after hunt-1 and hunt-3 finish. |

`/data/mal/hunt-shared` is the shared zstd data layer, built as a MiScusi job. Hunt-2 uses it once `/data/mal/hunt-shared/README.md` exists and queries the tape directly (no copies) until then. Resume hunt-4 under the disk rules below after hunt-1 and hunt-3 finish.

### Disk incident 10-08

- research-0 reached **81%** at about +100 GB/h, from the hunts' per-branch extracts.
- **Fixed by:** stopping 2 hunts (hunt-2, hunt-4; hunt-2 was resumed at about 18:05Z, see above); Helm's cleanup, which freed 56 GB (now **74%**); and `.nobackup` on `hunt-1008` and `audit-1008/{tape,work,tmp}`.
- **New rules for all hunts:**
  - use the shared layer only, with no per-branch tape copies;
  - zstd Parquet or `savez_compressed`;
  - a 5 GB disk budget each;
  - heavy steps as MiScusi jobs, at most 4–5 at once.
- **Background watchdogs:** the disk watchdog warns at 86% and SIGSTOPs hunt processes at 90%. The CPU watchdog renices hunt processes above load 52.

## EXP-022 deadlines (withdrawn if a P1–P3 item is missed)

| By | What | State at handoff |
| --- | --- | --- |
| Before 2026-10-16T01Z | **P1 E0** (EXP-022 §2.1, all four items, recorded in a dated amendment). The deciding set is now Amendment 1 (#474): mints with mig − create ≤ 60 min on either side, plus every B pick, on E0 day explore-0814 2026-08-20. Items: (1) one exploration UTC day pinned by `VIEW.sha256`, both sides booting at 00:00Z; (2) runner equivalence, md5 of `forward_paper.replay_rows` = md5 of the **read-ready** gate replay (#462 plus strict lines); (3) scorer equivalence, the read-ready `cap_pick_score.py --book picks` attempt list = B's picks inside the universe, by md5. **A read-ready scorer is needed before 10-16T01, not only by 10-23.** (4) code pins: the blob shas of `forward_exp012_gate.py`, `forward_paper.py`, `exploration_entry_model.py` and `cap_pick_gate_replay.py`, plus `FROZEN.md5` = `a01f05dfb1e622f78b2bba55d174be09` | Amendment 1 merged. The harness is #473 (draft). **The E0 record amendment is still to write**, see below |
| Before 2026-10-16T01Z | **P2:** a clean A3 monitor run | Daily cron |
| Before 2026-10-16T01Z | **P3:** A8: decoder, sink resume guard, gate readers that refuse bad lines, per-hour slot_ms in the scorer. Walk 2 must start on a commit that has all four | **Partly done**: #466 (sink resume guard; opt-in strict lines) and #467 (decoder) are merged. Still open: the gate replay refusing bad lines (#462, changes in progress) and per-hour slot_ms in the scorer (#461 has `--k-mode hour`; it must merge) |
| Job must START before 2026-10-16T01:00Z (submit about 00:30Z) | **Walk 2**: `scripts/research/forward-walk2.sh` (merged: #469, with the #472 guards). MiScusi job on research-0, command `bash scripts/research/forward-walk2.sh`, params `{"start":"2026-10-16T01"}`, resumable, 10080 min, about 6 GB. It uses `--event-v` and `--strict-lines`, the dir `/data/mal/blocks/forward-1016` (as EXP-022 §9 and the ledger name it), and treats walker exit 3 as fatal. Credit cap 11,825,000 = 430,000 × 22 × 1.25 | Wrapper merged; the job is not submitted |
| Before 2026-10-23T01Z | **P4:** the read tool (sealed read mode of `tools/cap_pick_score.py`: lock, `LOOK_READS.jsonl`, look schedule, strict lines, refusals) merged with quant-proof | Not started |
| Before 2026-10-23T01Z | **P5:** E1 recorded (after the FINAL; cron 3d374658 at 10-16 06:13Z), with n ≥ 20; otherwise condition (c) is NOT_DECIDABLE and no look runs (EXP-022:288). It is recorded in a dated amendment before the first look. **P6:** the A2 check, after E1 | Patched tool merged (#463). A11 is not adopted, so E1 does not wait on it |

### E0 record amendment, still to write before 10-16T01Z

- Pin `tools/tape_lines.py` too.
- Record the first-boot staging hours: forward-1002 creates **2026-10-14T21 through 10-15T23** (27 staged, 26 opened), plus feed hour **10-16T00**. All of them sit inside the buffer `[10-14T01, 10-16T01)`.
- The official E0 run is a **16 GB MiScusi job**.

## Open PRs

- **#461 `claude/cap-pick-score`** (draft). The CAP-PICK scorer; phases 1 and 2 are done, head 2bab07b.
  - Phase-1 reproduction #386: 24,272 attempts, P2–P4 exact to the lamport.
  - The phase-2 review was OK, and the hardening (path guard, 10,000-draw p) is done.
  - **Merge it after job #389 (full-book phase-2 regression at 04d2d1f) is exact on P2–P4.** rows.csv is unchanged by 2bab07b.
  - EXP-022 pins it at `ebb77f4`. The read tool builds on it.
- **#462 `claude/cap-pick-gate-replay`** (draft). The live-gate replay, with a ReadGrant mode and strict lines.
  - The reviewer's verdict was **CHANGES**. Fixes are in progress.
  - Its blob is pinned at E0.
- **#471 T2 B90 exit** (draft). The full run is job #391, after #389. Its pre-declaration must be committed before any scoring.
- **#473 E0 harness** (draft). It is implementing the Amendment 1 deciding set. A dry run on 08-17 matched 857/857 at ≤ 60 min, with C equal at 122 mints.
- **#465 `claude/catalog-second-owner`**: parked. Merge it only if the read tool guards its reads through `check_read`, and adapt it to a non-owner read allowance (EXP-022 §12).
- **#90**: old Cursor draft, keep.

## First things to do after a compaction or new session

1. **Become the inbox reader:** `miscusi_worker_start` with `inbox: true`, name `manager9`.
2. **Check the crons with CronList.** Recreate any that are missing (they die with the session, not with a compaction).
   - **Daily structure monitor, 06:41Z.** Research-0, ops, 300 MB, 20 min: `/data/mal/venv/bin/python -m tools.pump_structure_monitor --out /data/mal/structure-monitor/daily.jsonl`.
     - Read `halt.flags`, `warn.flags` and `halt.all_evaluated`.
     - A halt before counting means withdraw EXP-022. During counting it suspends counting the same day.
     - Two days with a core rule not evaluated count as a halt.
     - Expect `ms_per_slot_moved` around 10-09T14:30Z.
   - **Daily decommissioned-probe check, 12:17Z.** Fast-0, the job #385 command. Alert on: executor or timer active or enabled, STOP missing, attempts > 62, or realized ≠ −0.210755.
   - **Daily tip-tape archive, 03:23Z.** Fast-0, the job #352 command; expect `mismatched=0`.
   - **One-shots:**
     - 10-09 15:13Z: slot step;
     - 10-13 09:17Z: EXP-022 readiness;
     - 10-14 10:07Z: (e′) dry run;
     - 10-14 12:41Z: probe rent audit, job #363 command;
     - 10-16 06:13Z: E1.
3. **Forward walk #382 has a 7-day limit and ends about 10-15T11Z.** Resubmit it, or `miscusi_job_extend` it, **before then**, so forward-1002 reaches 10-16T01 for the FINAL. Use the same command and params `{"start":"2026-10-02T15"}`; it is idempotent. It runs on a pinned jobtree at 2bd45f1.
4. **Heavy jobs go on research-0 only**, one at a time (hunts: at most 4–5 at once, under the disk rules above). fast-0 jobs stay ≤ 1.9 GB.
5. **Check the hunts' watchdogs and `df` on research-0** before starting anything heavy. Hunt-2 is running (resumed about 18:05Z); resume hunt-4 only after hunt-1 and hunt-3 finish.

## Running jobs at handoff

| Job | What | Notes |
| --- | --- | --- |
| #382 | Forward walk 1 (forward-1002) from 10-02T15, on 2bd45f1 | Through 10-08T13 and 1,926,575 credits cumulative at the 16:00Z handoff. Never open its outputs before the FINAL. **Extend or resubmit before about 10-15T11Z.** |
| #389 | Phase-2 full-book regression of the scorer (04d2d1f) | Pass = P2–P4 exact, like #386. Then #461 merges. |
| #391 | T2 B90 exit full run (#471) | Queued after #389. |
| #371 | DEC-022 Phase A stream | Ends about 10-08T20:30Z. Do not extend it; Phase B is stopped. |
| shared-layer build | `/data/mal/hunt-shared`, the shared zstd data layer for the hunts | MiScusi job. Hunt-2 uses it once `/data/mal/hunt-shared/README.md` exists; hunt-4 waits on hunt-1 and hunt-3 as well. |

#387 is done (see above).

## Rules that bite

- **The 10-16 FINAL** runs as written at about 10-16T02Z (DEC-016 Am.5 §7).
  - It will be reported compromised (Am.2).
  - There is no live support through Am.3 (a) (Am.6 (g) D).
  - Run `exp012_forward` score and verify **without** `--strict-lines`. #466 kept them byte-identical to main by default.
  - The (e′) dry run is required before 10-15T23:00Z. Record its merge meta sha256 and commit in DEC-016. If it still fails at 10-15T23:00Z, record the failure and the counts; the read proceeds as written (DEC-016:300-305).
- **Seal.**
  - Before the FINAL, runner side files are read only through `tools/runner_timing_read.py`.
  - Never open forward-walk or forward-paper P&L, positions, decisions or intents.
  - For EXP-022 (§9): no person, agent or job opens, prints or prices a CAP-PICK outcome of a counted hour, from any source, except the sealed look itself run by the merged read tool. This holds before day 7 and between looks. Monitoring prints only hour counts and gate decision counts. No CAP-PICK process reads any forward-1002 hour, buffer included, before the FINAL is written. The fast-0 paper twin's CAP-PICK P&L fields stay sealed until the read ends.
- **Hunts stay on exploration tape.** No hunt reads forward-1002, forward-walk or forward-paper output.
- **Sealed blocks:** fresh-0802, fresh-0808 and fresh-0828.
- **Reserve convention:** PumpSwap rows are PRE-trade; bonding rows are POST-trade.
- **BOOST:** pump.fun's keeper `HTVZ…S2r` buys 17.585 SOL in about 29 slices, ending about 337–345 s after migrate. CAP-PICK depends on it.
- **Multiplicity:** EXP-022 makes the DEC-014 family count m ≥ 13.

## Live probe (DEC-019): stopped and decommissioned

The final result was 62/90 attempts, −0.210755 SOL. The wallet is at 0 lamports (withdrawn to the owner on 10-07). Helm does every restart step, and only after a gated book, a reviewed build and the owner's yes.
- **Canary wallet** (explained to the owner on 10-08): a Helm-held key, funded with ≤ 0.05 SOL, under DEC-019-style limits. It is only relevant after a CAP-PICK pass (O4). The 10-08 ~17:00Z mandate funds the wallet only once a book clears the gate.

## Clocks

| When | What |
| --- | --- |
| ~10-08T20:30Z | #371 ends |
| ~10-09T14:30Z | 200 ms step (epoch 1053) |
| 10-13 | EXP-022 readiness check |
| before ~10-15T11Z | Extend or resubmit #382 |
| before 10-15T23:00Z | (e′) dry run |
| before 10-16T01Z | E0 and its record amendment, a clean monitor run, walk 2 submitted |
| ~10-16T02Z | FINAL (compromised, as written) |
| after the FINAL | E1 |
| before 10-23T01Z | Read tool merged; E1; A2 |
| 10-23T01 / 10-30T01 / 11-06T01 | EXP-022 looks |
| 2026-10-31 | Owner's income goal date ($1000; $400 minimum) |
| 2026-12-08 | T1 tripwire kill date if nothing fires |

## Ops

- **Waiting on the owner or Helm:** O8, O9; the Oracle attn book and observe.attention stop; retiring the V-less books; runner-restarts.jsonl access.
- **GitHub hiccups:** use `timeout 60 … </dev/null` and retry. Pushes sometimes fail transiently.
- **Subagent turn limits:** builders stop at about 40 turns, reviewers at about 20. Resume them with SendMessage and ask for "verdict now". Workflow `agent()` calls with a schema can fail on a turn limit, so wrap them in try/catch.
