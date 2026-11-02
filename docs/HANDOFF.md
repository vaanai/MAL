# MAL manager handoff: manager18 → next manager (written 2026-11-02 ~18:3xZ, updated ~18:5xZ)

Read this file first. Then read:
1. `/data/mal/hunt-1008/ops/manager18/state-manager18.md`: from "COMPACT-READY SUMMARY #4" to the end. It holds the full detail, with ids and comment numbers.
2. `/data/mal/hunt-1008/ops/manager18/PUSH-PLAN-1101.md`: the STATUS table, the common lane rules and the Log.

**No book has cleared the promotion gate.** Profit so far is 0. No real SOL is being traded. Paper only.

## 0. Owner direction (current)
- **2026-11-01 ~22:3xZ.** The owner is frustrated by the run of bad news. Be proactive: solve blockers yourself, push hard, and use every resource. Withdraw anything that will hit the same problem. Helm requests go into `/data/mal/hunt-1008/push-1016/hunt29/p1/HELM-ASK-<date>-<n>.md`; the owner tells Helm to check hourly. Ask the owner only for keys, money or a live go. (Memory: owner-push-1101.)
- **Standing approvals still in force:** C0 canary pre-approval after a gate PASS, DEC-032 preconditions (10-23). API spend past 11-11 (10-28). Decide-and-tell for rule, DEC and lane decisions (10-22). The promotion gate is unchanged.
- **The weekly usage limit** hit 11-02 ~01:2xZ; the owner reset it at ~18:1xZ on 11-02, and subagents work again.

## 1. What changed on 11-01 and 11-02
- **Diagnosis:** most of October's NOT_DECIDABLE results were self-inflicted: the public feed lost data, A3 halted on redeploys, crons were lost, smokes failed. Lane L4 audit: 30 events, only 4 of them market verdicts (`ops/reports/2026-11-02/L4-nd-audit.md`).
- **EXP-027 (r4-b on walk 2) is NOT_DECIDABLE as registered.** EXP-027 §7.1 item 6 fired on A3 halts at 10-20 and 10-21 (pins_changed) and 10-29 (synthetic). The rescue was refused (QP-RULING-1102-r4b-a3-record, Addenda 1–2, sha `7dcdafaf`). Route (b): ND is written at the deadline. #1181 is merged (ND determination). J6b job #2053 runs `--check-window` at 2026-11-09T03:05Z. The manager then files the `EXP027_DEADLINE_ND` squash PR plus the ledger end record with QP OK by 11-09T12Z (texts in `ops/reports/2026-11-02/L6-exp027-route-b-J6b.md`). There is no r4-b C0.
- **The HV4 successors' API spend is withdrawn** (owner, q_Za07 closed). #1175 is merged. No successor job ever existed.
- **New books on the complete fast-0 tip feed** (DEC-031 Am.23 route B):
  - **s1t:** Am.24, #1186. Tag `q50-succ-tip-1` → afa4971d. EXP-024 note #1190. Smoke tools #1193.
  - **HV4-T:** Am.25, #1178. Nine arms at 9ed4c5ff, clock 2026-11-10T00Z. Smoke only after #1562 ends at 11-08T13:05Z.
  - **r4-b-T:** Am.27, #1187. W merged with a merge commit, b17c0a8e (#1196).
  - **HV7:** PRs below.
- **Infrastructure merged:**
  - #1183, Am.28: restart continuity.
  - #1191: tip hour-health, plus the fast-0 staging job #2064 that copies inputs to research-0 `/data2/mal/tip-health/fast-inputs`.
  - #1171, #1174: library and Console.
  - #1188, #1195: r4bt counter.
- **SEALS-BRIEF** is now sha `bb11a222`: S1T READERS final line plus TIP HOUR-HEALTH STAGED INPUTS. The AGENT-BRIEF pointer is updated. The HV4-T READERS line (QP-NOTE-1102-tip-readers Note 2, 8 fixes) is NOT applied yet.

## 2. Open PRs (state at 2026-11-02T18:4xZ): verify verdicts at the exact head, then merge
**Merged since the first draft of this file:**
- r4-b-T: #1173 (memory check), #1197 (decode parity + writes, merge commit), #1198 (confirmation read, merge commit 98b9314f), #1200 (sample key-mapping, merge commit f180e40a).
- **r4-b-T FROZEN SHA = 17e3b05a7696900cda7fcf93e4af4008d8fa0184** (head of the bc65c088-lineage branch; QP-RULING-1102-r4bt-ADDENDUM-2, sha 308e20f0). The registration record pins it, plus the sampler command and tools/r4bt_sample_keys.py.
- HV7: #1170 (U2 + READ_ROOTS + registration draft; #1102 closed as superseded).
- Gap-fill: #1182 (Am.26), #1194 (P1–P3 proof harness), #1201 (splice fix + Am.26 Erratum 1). No book may name Am.26 until P1–P3 have run on explore-0814 and been notebooked, and the live wiring, scorer pin and SEALS entry exist. Open reviewer notes: fill() has zero backoff on 429; segment load trusts the manifest's "filled" flags.

| PR | Head | State | Merge |
|---|---|---|---|
| #1169 (Am.23 Erratum 1) | df5d674c | reviewer APPROVE; QP test job #2073 PASS, QP verdict not posted yet | squash; DEC-031 append-only (re-merge main if it conflicts) |
| #1172 HV7 tip P0 counter | 41080f22 | reviewer APPROVE; QP verdict owed | squash, after #1169 |
| #1189 HV7 F1 tip proofs | 94246150 | reviewer APPROVE; QP verdict owed (QP to rule: hv7.0- prefix during the EQUIV_RC leg) | after #1172; update from main first |
| #1202 s1t book tools | moving | reviewer CHANGES (drop whole_lines from digest); QP asked to rule on the research-0 A3 loop + a3-guard-pin-s1t tag | then a follow-up PR: reader wiring in arena_h5_books/legs, A3 alert + daily score job scripts, guard tag command |
| #1199 (this handoff) | — | T0 owed | squash |
| #1176 (r4b C0 install pack) | f9ac946f | parked | reuse for any C0 |

DEC-031 merges are append-only. On a conflict, merge main into the branch with the amendment block byte-identical and appended at the end, then get both verdicts re-confirmed by diff.

## 3. Clocks (after the limit reset at ~18:1xZ on 11-02, the 11-07 paths are back on)
- **s1t, clock 2026-11-07T00Z.**
  - Watcher **#2074 is running** (S = 2026-11-05T11:45Z, 100 h).
  - Smoke launcher: submit between 11-05T09:00Z and 11:30Z (`/data/mal/hunt-1008/ops/reports/2026-11-02/s1t-run-commands.md` §2).
  - Notebook pins before the smoke: plan hours 578, cap 3,500 MiB.
  - Shadow seg1 at 11-06T22:55Z needs: registration notebooked, smoke PASS, feed bar recorded, A3 loop running, plus #1202 and its follow-up merged.
  - Slip: 11-10T00Z.
- **HV7, clock 2026-11-07T00Z.** Path: merge #1169→#1172→#1189 → run the F1 proof jobs (`scripts/ops/hv7-tip-equiv.sh`; EQUIV_RC; F_3D) → P0 job (`scripts/ops/hv7-tip-p0.sh`; days 11-03/04/05; H0 2026-10-29T09; it runs after 11-05T23 closes, ~11-06T00:30Z; pass 150/14 a day) → registration by 11-06T22:05Z. Pins and floors: QP report `ops/reports/2026-11-02/qp-hv7-1169-1170-1172-1189.md`.
- **r4-b-T, clock 2026-11-07T06Z** if the registration record is written by 2026-11-06T23:59:59Z, else 11-08T00Z. Runbook: `ops/reports/2026-11-02/r4bt-registration-runbook.md`. Owed before registration:
  - decode fetch/parity dry run on a real post-redeploy sample;
  - a fresh evaluated A3 run within 24 h (loop #2072 runs 07:11Z daily);
  - the #1173 memory check run from its merge commit (its verdict binds);
  - P0 from dry-long-1101a entry counts;
  - stop dry-long-1101a at 11-07T03:00:30Z, then a new host run at 17e3b05a after registration.
- **HV4-T:** 2026-11-10T00Z (Am.25); smoke only after #1562 ends at 11-08T13:05Z.
- **Q50:** waiter #1886 and read #1887 still queued on its count.
- **Walk 2:** stop after hour 11-07T05 is verified (n_5l_k53yo2XeSMg). J7 gate: see `ops/reports/2026-11-02/L6-exp027-protect.md` §8; test cancelling a job from inside a job first. Never job_status #1036/#1290.
- **EXP-027:** J6b #2053 runs at 11-09T03:05Z; the DEADLINE_ND squash PR and ledger end record are due by 11-09T12Z.

## 4. Host jobs running (job_list state only; sealed status views stay sealed)
- r4b DRY #2019 and sidecar #2018, until 11-07T03Z.
- Tip writer #2033 and push #2039, ending about 11-15T07Z. The successor needs a gate job, because `after` fires only on exit 0.
- Staging #2064, ending about 11-16; it needs a successor.
- Pool-cache #2055.
- Sampler chain #2048 → #2062. The last 30 h window had zero new gap rows (#2022).
- A3 monitor loop #2072, until 11-07.
- s1t watcher #2074 (S = 11-05T11:45Z).
- J6b #2053, at 11-09T03:05Z.
- Watch #2043 and watchdog #1952.

## 5. Owed and known issues
- **QP note owed on M3′(a):** small fast-0 ops jobs are about 108 MiB over the 7,936 MiB reservation rule while the r4b DRY runs. M3′(b) has gigabytes of room.
- **Tip-tape archive:** not run since 11-01, to respect M3′. Raw tip hours older than 72 h will be pruned before it can resume after 11-07T03Z.
- **Before the first daily tip hour table** (before 11-08T01:15Z): one daily job per program set, with a registered pins file; a dry `table` run first.
- **Records:** LAB_STATE rows (r4b DRY start, writer start, EXP-027 ND, walk-2 stop); a Console update for 11-02; HV4-T READERS SEALS line.
- **Capital:** live trading at 0.25 SOL per position needs about 2.8–3.0 SOL; the owner has 1.29 SOL. Only relevant after a gate PASS and its confirmation.

## 6. How to verify quickly
- `gh pr view N --comments | grep -E '^(QP|REVIEWER) VERDICT <head>'` before every merge. Match the head sha. For W-family PRs, use `gh pr merge --merge`.
- Tip writer health: `/data2/mal/arena-tip-feed/hv7/.mirror/writer_status.json` (status fields only).
- Watchdog: `/data/mal/hunt-1008/ops/watchdog/status.jsonl`.
- A3: `/data/mal/structure-monitor/daily.jsonl` (read only `run_utc` and `halt`).
