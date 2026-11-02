# MAL manager handoff: manager18 → next manager (written 2026-11-02 ~18:3xZ)

Read this file first. Then read:
1. `/data/mal/hunt-1008/ops/manager18/state-manager18.md`: from "COMPACT-READY SUMMARY #4" to the end. It holds the full detail, with ids and comment numbers.
2. `/data/mal/hunt-1008/ops/manager18/PUSH-PLAN-1101.md`: the STATUS table, the common lane rules and the Log.

**No book has cleared the promotion gate.** Profit so far is 0. No real SOL is being traded. Paper only.

## 0. Owner direction (current)
- **2026-11-01 ~22:3xZ.** The owner is frustrated by the run of bad news. Be proactive: solve blockers yourself, push hard, and use every resource. Withdraw anything that will hit the same problem. Helm requests go into `/data/mal/hunt-1008/push-1016/hunt29/p1/HELM-ASK-<date>-<n>.md`; the owner tells Helm to check hourly. Ask the owner only for keys, money or a live go. (Memory: owner-push-1101.)
- **Standing approvals still in force:** C0 canary pre-approval after a gate PASS, DEC-032 preconditions (10-23). API spend past 11-11 (10-28). Decide-and-tell for rule, DEC and lane decisions (10-22). The promotion gate is unchanged.
- **The weekly usage limit hit 2026-11-02 ~01:2xZ** and resets 2026-11-06T13:00Z. Subagents fail with 429 until then.

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

## 2. Open PRs: verify verdicts at the exact head, then merge
| PR | Head | State | Merge |
|---|---|---|---|
| #1169 (Am.23 Erratum 1) | a4759075 | reviewer APPROVE; QP agent said "posted"; verify | squash, first |
| #1170 HV7 U2/READ_ROOTS/registration draft | 59990fbd | reviewer APPROVE; QP test job #2066 PASS, verdict not posted | after #1169, update from main |
| #1172 HV7 tip P0 counter | 80808625 | reviewer APPROVE; QP #2067 PASS, verdict not posted | after #1170 |
| #1189 HV7 F1 tip proofs | ee949bbd | reviewer APPROVE; QP #2071 PASS, verdict not posted | after #1172 |
| #1197 r4-b-T decode parity / writes | 03d22a31 | QP OK; reviewer verdict owed | **merge commit**, before r4-b-T registration |
| #1198 r4-b-T confirmation read | 87272bef | QP OK; reviewer verdict owed | **merge commit**, before the screen read |
| #1182 gap-fill (Am.26) | f92029c1 | QP CHANGES F1/F2 | not on the critical path |
| #1194 gap-fill proofs | 8f7cd596 | QP CHANGES R7 | after #1182 |
| #1173, #1176 (r4b C0 memory/install) | — | parked | reuse for any C0 |

DEC-031 merges have been append-only. On a conflict, merge main into the branch with the new amendment block byte-identical and appended at the end, then get both verdicts re-confirmed by diff.

## 3. Clocks (re-plan on resume; the pause cost the 11-07 dates)
- **s1t:** the 11-07 path is missed (watcher ≤ 11-04T11:30Z, smoke 11-05T11:45Z). Slip clock **2026-11-10T00Z**: P0 days 11-06..11-08 through the Am.23 item 9 pinned P0 counter; slip smoke form (a); S_cap 11-20; H = 21 d. Side-feed probe #2065 passed (rpc.solanatracker.io, 410 msg/s). Watcher ≥ 50 h, started ≥ 24 h before the smoke. The launcher stops the smoke if the watcher exits 4, 5 or 6 or goes away.
- **HV7:** needs #1169–#1189 merged, the F1 proofs run (md5 equivalence plus the real P0, pins: days 11-03/04/05 and H0 2026-10-29T09 — these days must be re-picked if the registration moves), registration, then the clock. The 11-07 date has likely slipped; pick the next honest 00:00Z.
- **r4-b-T:** clock 11-07T06Z only if registered by 11-06T23:59:59Z, else **11-08T00Z**. Before registration:
  - #1197 merged;
  - a dry run of decode fetch/parity on a real sample;
  - a fresh evaluated A3 run within 24 h, pinned with the pins file;
  - `--writes` with `--last-clean-run-utc`;
  - dry-long-1101a stopped at 11-07T03:00Z, then a new bc65c088 host run after registration.

  All rules are in QP-RULING-1102-r4bt (`2d329e4b`) and its ADDENDUM-1 (`50c76ae0`).
- **HV4-T:** 2026-11-10T00Z (Am.25).
- **Q50:** waiter #1886 and read #1887 are still queued on its count.
- **Walk 2:** stop after hour 11-07T05 is verified (manager decision, n_5l_k53yo2XeSMg). J7 gate command is in `ops/reports/2026-11-02/L6-exp027-protect.md` §8; test cancelling a job from inside a job first. Never call job_status on #1036 or #1290.

## 4. Host jobs running (job_list state only; sealed status views stay sealed)
- r4b DRY #2019 and sidecar #2018, until 11-07T03Z.
- Tip writer #2033 and push #2039, ending about 11-15T07Z. The successor needs a gate job, because `after` fires only on exit 0.
- Staging #2064, ending about 11-16; it needs a successor.
- Pool-cache #2055.
- Sampler chain #2048 → #2062. The last 30 h window had zero new gap rows (#2022).
- A3 monitor loop #2072, until 11-07.
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
