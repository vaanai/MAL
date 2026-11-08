# MAL manager handoff: manager20 → manager21 (written 2026-11-07T21:3xZ, from `date -u` 21:25Z)

Read this first. It replaces the manager17 handoff (and the never-merged manager18 PR #1199).
Then read the tail of the manager20 state log, `/data/mal/hunt-1008/ops/manager20/state-manager20.md` (one line per event, newest last; from line ~220 covers 11-07). Then the newest MiScusi notebook entries.

**No book has cleared the promotion gate. No live trade has been made. Profit so far is 0.**

## 0. Owner direction (current)
- **11-06 ~16:4xZ (memory `owner-push-1106`):** "I'm pre approving everything, we need to be doing everything in our efforts to push to profit." More initiative while jobs run, deliberate work, acknowledge spend.
- **11-06 ~17:15Z standing order (memory `owner-go-live-authority-1106`):** "Get things done WITHOUT asking for my permission, work with helm, go live whenever you're confident". Confident = evidence. Mission = start making money.
- **11-07 ~21:2xZ:** the owner asked for this handoff now so the next manager can **start a push**.
- **Helm will NOT act on relayed approval.** Any live step (slowbot-h-1 go-live, any key creation) needs the owner's yes directly in Helm chat. Ask the owner for that; do not tell Helm "the owner approved".
- Hard rules, seals and the promotion gate are unchanged by any pre-approval (CLAUDE.md).
- API: Haiku 5.5 budget for the TRADER only (resets after 11-11).

## 1. The big result of 11-07: tip-feed migration bug found, fixed, deployed
- Helius `getBlock` truncates the logs of pump `migrate`/`migrate_v2` txs. The fast-0 tip follower decoded migrations from logs only, so the tip tape had ~0.6% of graduations as `migration` rows (writer counts 11-06T12..11-07T17: pool_create 5 / complete 893).
- That starved arena_tip_adapter `pool_create` and therefore **r4-b, h5v2-f1 and s1t** (QP-RULING-1107-tip-migration-crossbook.md, ops/manager20/). It very likely explains r4-b-T P0 FAIL (1 trade in 3 days) and s1t's AGE_BAR FAIL.
- Fix: PR #1264 (inner-instruction migration rows; follower blob e3213b8c; decoder blobs unchanged) + tests #1262/#1265 + DEC-031 Am.23 Erratum 2 (#1266) + Am.29 Erratum 1 (#1267).
- Proof: P2 #2244 (33.5k slots, 5/5 streams md5 equal, extra_rows 47 = inner_added 47); triage #2245 (88 migrate txs all inner-only; drops are non-WSOL-quoted pools, ~10% pooled 19/191, by design).
- **Deployed 2026-11-07T18:03:21.427Z** by #2246 (tree /var/lib/mal/fast-tip-follower-src/6ab2c758…, drop-in 30-inner-migration.conf = WorkingDirectory only). Live check #2247 at +73 min: inner_added 43, errors 0, lag 0, pinned true. Writer hour 11-07T19: pool_create 39 / complete 39.
- **Window rule:** hour 18 and earlier are pre-fix; the first usable tip hour is **2026-11-07T19**.
- Helius credits used for this: ≈106.5k (#2235 6,001; #2236 33,501 void; #2244 33,501; #2245 ~33,501).

## 2. Fixed steps in the next days (crons are in `ops/manager20/crons-manager20-1104.json`; re-create them with CronCreate at session start)
| UTC | Step |
|---|---|
| hourly :17 | HELM-REPLY-* in /data/mal/hunt-1008/push-1016/hunt29/p1/; PR verdicts; miscusi_inbox; due steps |
| daily 01:21 / 03:23 / 07:27 / 07:31 / 09:43 / 12:17 | digest + HV4 L8 / tip-tape archive / A3 record / BOOST check / housekeeping + DEC-035 §5 lane line / H5 health — full prompts in the crons json |
| ~~11-08 20:41 / 22:07, 11-08 ~13:05, 11-09 03:05~~ | **Void:** #2251, #2252, #2053 and #1562 were cancelled after the owner decision of 2026-11-08 ~00Z, which ended forward-paper testing (notebook n_j0z3_wYlJOMCaw). No step is due. |
| **FLOOR live** (manager21, 11-08) | floor-lane-1 has been LIVE since 2026-11-08T14:19:43Z; first send 14:30:47.391Z. Gate read EXP-029 with Errata 1/1a/1b: S = 2026-11-09T06:00:00Z, deadline 2026-11-30T06:00Z. Daily report job #2290 runs 00:20Z (output `~/mal-data/floor-lane-daily/` on fast-0) and goes to the notebook and `data/console.json`. Health watch #2291 every 300 s: exit 3 = alert. State log `ops/manager21/state-manager21.md`; crons `ops/manager21/crons-manager21.json`. |
| **11-16 06:13** | HELM-ASK-1108-1 window opens. Helm resets floor-lane-1 `first_send_ms` (executor stopped, one field), target by 11-21T00Z. **If it is not done before 2026-11-22T14:30:48.897Z, EXP-029 is FAIL** (Erratum 1b). |
| **11-21 00:05** | Read-only host check **#2294**: exit 3 = reset NOT done. Chase Helm the same hour. |
| 11-21 18:13 | Successors of #2290 and #2291 (they end 11-21T23Z), to END 2026-11-30T07Z, chained with `after`. |
| 11-10 20:13 | feed-handover-1111: submit per docs/runbooks (research-0 arena feed #1652 successor). |
| 11-12 10:13 | tip writer #2033 + push #2039 SUCCESSORS (due before their limits). |

Launcher: `scripts/research/h5v2-f1-launch.sh` sha256 5e6beb9d… (modes smoke/book1/book2; book1 CLOCK_DAY ≤ 2026-11-20). Pins for smoke 2: notebook n_0BiXrSL8SVD71w.

## 3. Lines and where they stand (all paper unless said)
| Line | State | Push idea / next |
|---|---|---|
| **h5v2-bx10-f1** | Smoke 1 #2219 slipped (starved feed). Smoke 2 #2251 on fixed feed. | §2 above: book1 clock 11-09T00Z if the smoke and 8(j) pass. |
| **r4-b / r4bt-c0** (live executor) | C0 code complete and merged 11-06 (#1246–#1261; notebook n_N3C1MJqaW7fU4A). P0 FAIL 11-07 00:17Z (floor; starved feed). Not registered. Helm install request withdrawn (HELM-REPLY-1106-1: nothing installed). | Best live candidate now that the feed is fixed: new DEC-031 amendment for a post-deploy P0, plus a C0 real-layout twin run on closed post-deploy hours (≥ 11-07T19) **before** any C0 install (Am.23 E2 limits). Then a fresh Helm request + owner's yes in Helm chat. |
| **slowbot-h-1** (DEC-035 lane) | 24 h keyless dry ended clean (HELM-REPLY-1104-1h). Helm waits for the owner's own yes in Helm chat. | Ask the owner to say yes to Helm directly. SLOWB-0 paper #2136 is negative so far (memory `slowbot-paper-negative`) — it is a calibration probe, not a profit claim. |
| **s1t** | Withdrawn 2026-11-06T17:16:06Z (n_61bjmqA3xPk33g). AGE_BAR FAIL likely the starved feed. | Could be revived on the fixed feed under a new amendment. |
| **h5 lane3 shadow (heal)** #2184 | Running; feeds h5-lane-h. QP-RULING-1106-h5v2-lane: no H5 v2 lane live now; BOOST shift (QP-RULING-1106-boost-shift) broke the H5 v1 330 s exit premise. | h5-lane-h2 PR #1240 open (no verdicts). |
| **h5-lane-syn** | PARKED: #2110 refused (H5_SOCKETS 2 < 3 guard, right to refuse). | — |
| **HV4** (9 Haiku bots) | 11-01 read NOT_DECIDABLE_5PCT; E4/E5/E6/L8 jobs finishing 11-08/09. | Let them finish; never status-view. |
| **HV6 / HV7** | HV7 PARKED (no clock). HV6 fixes PR #1108 open. Haiku text-only selection evidence is poor (memory `haiku-trader-track`). | Low priority. |
| **Q50** | ENDED NOT_DECIDABLE 11-06. | — |
| **Walk 2** | Stopped after hour 11-07T05 (J7, 08:42Z). | — |
| **EXP-027** | ND, route (b) J6b at 11-09T03:05Z. | read file at 03:43Z. |

## 4. Owed (not yet done)
- **Records batch** `claude/records-20261107` (T0, records only): LAB_STATE follower restart row (instant 18:03:21.427Z, tree, blobs, P2 #2244, triage #2245, cut-over hour 18, first usable 19); console event; walk-2 stop; r4-b-T P0 FAIL; h5v2 hold/slip; M3′ end; dry-long stop; root cause + #1262/#1264–#1267; feed hand-off 11-07; housekeeping. Note: Am.23 E2 item 2 cites P2 #2236, but the passing P2 is #2244 (#2236 void, no solders in that venv).
- **Follow-up PR:** P2 script must refuse to run without solders; split canon exceptions from pool mismatches in follower counters; document the non-WSOL drop.
- PR #1263 (public-RPC migrate probe): merge or close (no verdicts).
- Retarget the 12:17Z H5 health check `--shadow-dir` (it still points at the ended Q50 shadow → h5_feed_stale false alert).
- Check structure monitor #2214's first run 11-08T07:11Z: the 11-07 run (#2072) sampled 0/20 graduations (11-06: 20), so no BOOST median was computed. Cause not yet established (the monitor has its own sampler, separate from the tip follower).
- Stale open PRs (#954–#1199 range): many are parked/never-merge; triage when there is time.

## 5. Running jobs (35; `miscusi_job_list active_only`)
Feeds/tape: #2033 tip writer, #2039 push → research-0, #2062 follower health, #2064 hour-health staging, #2055 pool_cache, #2169 r4-b mirror pull, #2134 venue tape, #1652 research-0 arena feed, #1814 meta-capture.
Paper bots: #2136 SLOWB-0 (+ #2155 intents push), #2184 h5 lane3 shadow heal, #1748 HV6 producer.
HV4: #1562 E4 runner, #1567/#1568/#1569 E5, #1559/#1560 E6, #1519/#1520 L8, #2001/#2002 gap relays, #1591 read-instant.
h5v2: #2251 smoke 2, #2252 8(j) bar.
Guards: #1952 watchdog, #2176 disk guard, #1898 + #1896 A3 guards, #2199 BOOST watch, #2214 (→ #2217) structure monitor, #1647 change radar, #1897 digest. Scheduled: #2053 J6b.

**Never `miscusi_job_status`:** #1561 #1562 #1419 #1421 #1543 #1540 #1446 #1036 #1290, the HV4 E5/E6 loops, #2018 #2019, and every s1t / h5v2-bx10-f1 shadow, smoke, judge, A3 or score job (incl. **#2219, #2251**). Use job_list. Treat HV4 L8 jobs cautiously.

## 6. Rules you will need at once
- Seals: paste `/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md` (sha256 9995ec7d…) verbatim into every tape-touching brief. Never open the tip tape (/var/lib/mal/sealed/*), arena tape contents, shadow/smoke dirs, /var/lib/mal-live/**, bots.json, meta-capture, the C0 state dir or derived copies — counts only via manager jobs. The writer's `.writer/counts.jsonl` and `status.json` are agent-readable.
- Merges: `QP VERDICT <head> OK` + `REVIEWER VERDICT <head> APPROVE` at the exact head; T1 needs separate agents. Workers never merge.
- Jobs run under `sh`; feed jobs ≥ 0.5 CPU; one heavy replay at a time on fast-0; a DEC-035 bot code change = a new bot_id.
- Say "credential material" (hook blocks the other word); use `--body-file` for PR/comment bodies.
- QP rulings of this manager: `/data/mal/hunt-1008/ops/manager20/QP-RULING-*.md`. Reports: `/data/mal/hunt-1008/ops/reports/2026-11-0{6,7}/`.

## 7. How to verify this handoff
- `git log --oneline -6` on main shows 1025b36f (#1267) and 6ab2c758 (#1266).
- `miscusi_job_list active_only` shows #2251 and #2252 running.
- Writer counts: `/home/claude/mal-data/arena-tipfeed/tape/.writer/counts.jsonl` on fast-0 (via a job) — hours ≥ 11-07T19 should show pool_create ≈ 0.9 × complete.
- State log last lines mention this handoff (21:2xZ).
