# MAL manager handoff: manager23 → next manager (or manager23 after a context compaction), written 2026-11-13T01:3xZ, updated 2026-11-13T14:5xZ from `date -u`

This replaces the manager22 handoff (written 2026-11-12T20:5xZ–21:1xZ). manager23 took over at 2026-11-12T21:25Z. The state file is `/data/mal/hunt-1008/ops/manager23/state-manager23.md` (one line per event, newest last); the crons are `/data/mal/hunt-1008/ops/manager23/crons-manager23.json`. Main head before the 2026-11-13c records PR: 4aaf30fd (merged since 13b: #1351 aa39dd07, #1352 71a446b4, #1353 9c52f4fc, #1354 4aaf30fd). Merged since the 04:3xZ update: #1346 (07ef3302), #1347 (1670b44f), #1348 (4ef63d48), #1349 (128d7e27e32d80a693c89fdc393dcd05bf686002, 2026-11-13T08:50:27Z; ledger Forward walk 2 note and DEC-031 Amendment 23 readers record 2).

**Read in this order:**
1. `CLAUDE.md`
2. This file, in full.
3. The tail of `/data/mal/hunt-1008/ops/manager23/state-manager23.md`.
4. The newest MiScusi notebook entries.

**Before anything else:** run CronList and re-create the session crons from `crons-manager23.json` (§5), then run one hourly status job (§3). A new session has no crons, and fixed-instant steps must not rest on session crons alone (lesson of 2026-10-15).

**No book has cleared the promotion gate.** One bot trades real money: **FLOOR floor-lane-1**, the DEC-035 live-learning lane on mal-fast-0. Nothing below says it makes money.

## 1. STATE OF THE LIVE BOT (updated 2026-11-13T14:5xZ): the stage-1 host was installed at 14:44Z; the host refuses every pool until about 2026-11-14T14:47:40Z; 0 open; FLOOR made no money on 2026-11-13

**UPDATE 2026-11-13T14:5xZ (state file 14:22Z, 14:32Z, 14:51Z lines).** Open trade `e3b7cc9de20e65bd` closed on the 24 h time stop (sell sent 14:06:11Z, landed 14:06:13Z): realized **-0.004085219 SOL** after all fees. Lane realized today **-0.004718253 SOL** over two exits; 0 open; wallet **3,155,784,860 lamports**. **FLOOR made no money on 2026-11-13** (two small losing trades; entries locked out since 00:33Z). Helm then installed host stage 1 (cheap restart) at 14:44:02-03Z, host PID 2913148, executor PID 2625989 untouched (record in §3 and LAB_STATE 2026-11-13c). That install is a new host start: **the host refuses every pool until about 2026-11-14T14:47:40Z, and the 06:03Z dates below are superseded.** The text below up to "The open position" is the earlier incident record.

**UPDATE 2026-11-13T08:5xZ (state file 06:03Z, 06:3xZ, 08:41Z lines): both FLOOR units were restarted again at 2026-11-13T06:03:33Z, not by Helm and not by us.** `apt-daily-upgrade` upgraded libc6 at 06:03:18Z, systemd re-executed at 06:03:27Z, and the services restarted at 06:03:32-33Z. New PIDs: host **2625988** (was 2504565), executor **2625989** (was 1929135), tip follower **2625920** (was 2254354; NRestarts 0, lag 3, jumps 0, retries 4 at the 07:3xZ hourly). No unit or config changed. `lane_start`: down_ms 14796, since_hour null, checkpoint true, open 1, blocked_pools 3, carried_gaps 3. The bridge shows only `gap host_restart` at 06:03:35Z; no send, no exit. The new `host_down` span means **the host refuses every pool until about 2026-11-14T06:03Z** (it was 02:24Z). Helm answered HELM-ASK-1113-4 at 06:18Z (HELM-REPLY-1113-4): the cause was needrestart run from the unattended-upgrades dpkg hook; Helm wrote `/etc/needrestart/conf.d/50-mal-live.conf` (`override_rc = 0` for `^mal-floor-lane(-host)?\.service$` and `^mal-.*\.service$`); the FLOOR units are not restarted for it. A newer kernel 6.8.0-149 is pending; a reboot is never automatic here. Plan the reboot with the stage-1 install window (it would restart everything). At the 08:41Z hourly #2582: `gap_refusals` 53 (unchanged since 07:31Z), `log_seq` 128, 1 open (`e3b7cc9de20e65bd`), realized today -0.000633034, headroom 1.112774721, wallet 3,058,196,239, disk 44.68%.

**`mal-floor-lane-host.service` stopped itself at 2026-11-13T00:33:06Z; Helm started it again at 02:19:18Z (PID 2504565), HELM-ASK-1113-2 is done.** (That start's refuse window, to 02:24Z, is superseded by the 06:03:33Z restart above.) The executor `mal-floor-lane.service` (PID 1929135) was untouched until 06:03:33Z. (The text below up to "The open position" is the incident record as first written; the outcome is in "Resolution".)

**What happened.**
- **00:32:49Z:** the host saw a trigger and wrote its Buy. The executor bought order `73a31cf948fba66e` (sent 00:32:53Z, landed 00:32:56Z).
- The host's paper-ledger append was then refused by the DEC-031 §3.8 disk floor: fast-0 `/` was **24.0% free, the floor is 25%**. The executor unwound the position at once (`paper_refused`; sell landed 00:32:59Z). The round trip realized **−0.000633034 SOL**. This is the designed unwind.
- **00:32:50Z:** `floor_lane_host: HostFatal: LedgerWriteFailed`. **00:33:06Z:** the unit exited, status 2, NRestarts 0. The old host PID 3484327 is gone.
- **Cause:** `/` had sat at 23.9–24.4% free since at least 2026-11-11T16:47Z. manager22's "healthy above 22%" and the old disk guard (#2452, alerts under 24%) were both below the real 25% floor. manager23 reported 24% as healthy at 21:2x, 22:31, 23:31 and 00:31Z. That was a miss.

**Disk clean-ups (manager jobs).**
- **#2545 (00:40–00:43Z):** `/` from 23.888% to 28.004% free. It vacuumed the journal (948.1M), cleaned the apt cache and a pytest temp dir, and moved `/home/claude/data/c1nf-ledger` (158 files, 7,036,392,533 bytes; shelved line) to research-0 `/data2/mal/fast0-offload-1113/home-data-c1nf-ledger` (listing md5 `ffc8e6c3c84a7273de391468867f2b30` on both sides, then the source was removed).
- **#2548 (01:17–01:19Z):** removed the `seed-tmp` leftover with the host inactive: `/` from 24.531% to **28.218%** free.

**Helm's first start (HELM-ASK-1113-1, HELM-REPLY-1113-1).**
- The owner approved in Helm chat. Helm started the host at **01:15:20Z** (PID 2472716) and stopped it at **01:15:37Z**, as the ask's disk watch required (free fell to 24.92%, then 24.55%).
- The unit is `inactive` (Result=success), not failed. The executor was not touched.
- **Cause (job #2548):** the unit's `--since-hour 2026-10-29T11` sends every start through `rebuild_table_since`, which copies tape hours into `<root>/lane/seed-tmp` (`os.link` fails inside the unit). It copied 7,277 MB in 17 s; a full copy is about 28 GB.

**Resolution (HELM-ASK-1113-2, HELM-REPLY-1113-2; state file 02:25Z).**
- The owner approved in Helm chat at 02:19Z and pre-approved future FLOOR restarts on the same code and config (startup-only fixes; excludes new code, caps, keys, funds, the clock). Helm backed up the unit to `/root/mal-floor-lane-host.service.bak-20261113T0220Z`, removed `--since-hour 2026-10-29T11` from ExecStart, ran daemon-reload and started the host at **02:19:18Z, PID 2504565**. Same tree e89f1536, host.json sha256 `60f7a60a…`. df 28.112% to 28.053% over 5 minutes; no seed-tmp copy.
- Manager verification (job #2555): host active; journal `lane_start` {checkpoint true, down_ms 6391392, carried_gaps 2, blocked_pools 1}, `resumed` {open 1, rows 87}; no HostFatal/HostError/Traceback; `seed-tmp` empty; checkpoint gaps now include `host_down` 11-13T00:32:49 to 02:24:18, so every pool is refused until about 2026-11-14T02:24Z. Helm will not restart the host inside that window.
- Watch successor for #2291: submitted (seg2: host PID 2504565, executor PID 1929135, STOP, disk under 26%; hourly note named it #2558). #2558 exited 3 on the 06:03Z restart alert; the seg3 successor is **#2578** (host 2625988, executor 2625989). Guard #2546 alerts at 27%.
- **Disk now 44%:** after the walker B/C move (job #2562, 11-13T02:44:53Z and 02:49:28Z, 26,651,919,475 bytes in 579 files) fast-0 `/` was 44.166% free (hourly at 03:3xZ: 44.37%). Earlier: #2556 removed 82 stale job trees (28.0% to 31.570%). The three finished walker user units were disabled by Helm at 02:41Z (HELM-REPLY-1113-3). Still owed: tip retention, `public-logs.jsonl` (835 MB and growing).
- **Owner instructions of 2026-11-13 (chat ~02:3xZ, per the state file):** FLOOR restarts auto-approved from now on (Helm: same code and config only); r4-b forward test approved and FLOOR stays live; capital for r4-b if it does well ("i can bring in some more for it"); "make sure we're simulating real trading properly"; be proactive, finish things.
- **04:31Z observation (hourly #2571):** host `gap_refusals` went 13 -> 43 and `log_seq` 87 -> 117 between 03:31Z and 04:31Z = about 10 triggers refused (3 rows each) inside the 24 h refuse-all after the restart: a trigger burst is being missed (refuse-all ends ~11-14T02:24Z). Host UP (PID 2504565, hwm 04:30Z), executor 1929135 active, 1 open (`e3b7cc9de20e65bd`, ~14:06Z), headroom 1.112774721, daily stop ok, follower lag 2, jumps 0, retries 22.
- Wallet at #2571: **3,058,196,239 lamports**. Disk 44.27%. Am.6 gate at #2571 (before the QP count ruling, see §3): 0 of 20 clean closed trades at 0.10, plus 1 unwound round trip (`73a31cf948fba66e`). Today's realized -0.000633034 SOL.

**The open position (closed).** `e3b7cc9de20e65bd` (bought 2026-11-12T14:06:15Z) closed at 14:06:11Z on 2026-11-13, reason `time_24h`, with no host take-profit (the host was down 00:33:06-02:19:18Z). It counts as Am.6 trade 2 of 20; list-(ii) disclosure: host-down span, lost host take-profit, 06:03:33Z restart, exit 14:06:11Z vs buy landed 11-12T14:06:15Z.

**After any host start, the current code refuses every pool for about 24 h** (a `host_down` span; entries resume about 24 h after the start instant). That is by design. Positions open at the stop lose the host take-profit. The 06:03:33Z unattended restart counted as a host start: the refuse window now ends about 2026-11-14T06:03Z.

**After any host start, record it in LAB_STATE** (done for 00:33:06Z, 01:15:20Z/01:15:37Z and 02:19:18Z in the records PR of 11-13; the 06:03:33Z restart of host and executor is recorded in the records PR 2026-11-13b). Do not ask Helm to restart the host inside the refuse window above.

## 2. Do not break these (read first)
1. **The host `mal-floor-lane-host.service` is restarted only through Helm by HELM-ASK** (DEC-035, and Am.7's standing rule once stage 1 is installed; today's restart is on the v1 code, e89f1536 with host.json 60f7a60a, for an operational need). Any restart is a data-loss span and, today, a 24 h refuse-all. (The 06:03:33Z restart was unattended; Helm's needrestart exclusion of 06:18Z now keeps the mal-* units out of automatic restarts.) The executor `mal-floor-lane.service` is also restarted only through Helm. Once stage 1 is installed, Am.7 adds: the code and config must be the installed blobs and sha256; each restart is logged in LAB_STATE, the state file and the notebook; none while a kill-line check of the previous restart is pending.
2. **fast-0 `/` must stay at or above 27% free.** The host dies at its next trade under 25%. Disk guard job **#2546** alerts under 27%. The old guard **#2452** alerts under 24% and is not enough.
3. **Never start the host with `--since-hour` when a checkpoint exists.** It copies tape into `seed-tmp` and eats the disk.
4. **Job #2527's result file is read-only evidence** (`/data2/mal/scratch/floor-gap-range/gap.json`; sha256 in `/data/mal/hunt-1008/ops/reports/2026-11-13/floor-gap-range-job2527.sha256`, copy in `reports/2026-11-13/job2527/`). Do not overwrite or re-run into it.
5. **Code PRs are always T1** (separate QP and reviewer agents; DEC-013 Am.1 trigger 1). manager23 merged #1324 at T0 on one combined agent: a process miss, remedied by a post-merge QP.
6. **The wallet key is root-only and Helm's.** No Claude session reads, prints or copies it. Only the manager starts a lane book. Workers never start, fund, configure or restart a lane executor.
7. **Never `miscusi_job_status`** on any of these: #1561 #1562 #1419 #1421 #1543 #1540 #1446 #1036 #1290, #1567 #1568 #1569, #1559 #1560, #2018 #2019, #2219 #2251, or any s1t / h5v2 / A3 / score / shadow / smoke / judge job. For their limits use `miscusi-agent job list`. This includes the A3 guards **#1896 and #1898**.
8. **Never open** `/var/lib/mal/sealed/*` or any `tape-*` file.
   - Readers of `/var/lib/mal-live/**` are manager jobs only, and they print counts and order ids only.
   - Paste `/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md` verbatim into every brief that touches tape. manager23 added line (f) FLOOR LANE READERS at 11-12T22:55Z; lines (g) to (i) for the stage-1 proof and kill-line jobs are added (§3), and a FAST-0 TIP TAPE reader line for r4bt-a2 was added at 08:45Z (brief sha256 `3dc40a11…`).
9. **Merges.**
   - Every PR needs `QP VERDICT <head> OK` and `REVIEWER VERDICT <head> APPROVE`, both on the PR at the exact head. T1 PRs need separate agents.
   - Merge with `gh pr merge N --squash --match-head-commit <sha>`. Workers never merge. Check that `baseRefName` is `main`.
10. **Hook word.** A hook blocks the plural word for credential material in Bash text. Write "credential material", and pass PR bodies with `--body-file`.
11. **Do not delete before the stage-1 install record is written** (QP on #1341): the frozen copy `/data2/mal/scratch/floor-stage1-proof/tapecopy-1113` with its `.list` and `.sha256`, and the keeper dir `tapecopy-roll` (keeper job #2566).
12. **Do not touch** the firewall, sshd, cloudflared, Cloudflare Access or `/opt/miscusi`. The Helius key and the DB password stay where they are.

## 3. FLOOR floor-lane-1 (the one live bot)
**What it does.** The host runs the frozen rule `l1-floor-v2` (`tools/arena_rule_l1.py`, frozen sha 5e12f8f5) on the fast-0 tip tape. When the host triggers, the executor buys. Exits are take-profit at 1.5x or a 24 h time stop.

**Live config (unchanged from manager22; DEC-035 Amendment 5).**
- **Executor:** `mal-floor-lane.service`, PID **2625989** (since the 06:03:33Z unattended restart; untouched by the stage-1 install), pin `320cccc5…`, tree `/usr/local/lib/mal-floor-lane-executor/320cccc5…`.
- **Config:** `/etc/mal-floor-lane/live-320cccc5f2ec81d40d18f8991f11b15d29b52f74.json` (sha256 aa6c355f…).
- **Limits:** stake **0.10 SOL**; max_open 16; lifetime loss budget **1.20 SOL**; daily stop **0.20 SOL**; fee reserve 3,860,000 lamports.
- **Wallet:** `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk` (public key only). It held **3,155,784,860 lamports** at 2026-11-13T14:22Z after the e3b7 exit (3,058,196,239 from 00:3xZ to 13:31Z; 3,058,829,273 before the unwound round trip). Check (lamports in `result.value`): `curl -s https://api.mainnet-beta.solana.com -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"getBalance","params":["5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"]}'`.
- **Lifetime realized:** +0.015081595 SOL from the 10 closed trades at 0.03 SOL (lane report, job #2536). The unwound round trip (−0.000633034 SOL) is a separate figure.
- **Rollback to the 0.6 budget config:** `rm /etc/systemd/system/mal-floor-lane.service.d/zzz-pin-320c.conf && systemctl daemon-reload && systemctl restart mal-floor-lane.service` (executor only; through Helm).
- **Gap guard.** A tip-status gap makes the host refuse any Buy whose 6 h lookback overlaps it, and block that pool for 24 h. `gap_refusals` was 13 at 11-13T00:31Z and 53 at the 08:41Z hourly (unchanged since 07:31Z; the refuse-all after each restart counts them). The 11-12 gap (16:04:39Z, 763 ms, kind `tip_gap_status_unhealthy`, `missed_slots` 0) lost no row; one pool stays blocked to 11-13T20:48:22Z.

**Am.6 gate (DEC-035 Amendment 6, merged 8dec3a3b; NOT installed).** Am.6 raises the stake to 0.25, budget 2.10, daily stop 0.50 (owner accepted 11-12 ~06:4xZ, memory `owner-scale-1112`).
- **Take the first 20 closed 0.10 trades** since 2026-11-12T00:46:04.584Z, in lane-ledger exit order. Their realized P&L must be **≥ 0 after all fees** (every lane-ledger row of the 20 positions, plus failed-buy fee rows up to and including the 20th exit instant).
- **The fills check must print `FILLS_MATCH_MODEL yes` at trade 10 and at trade 20.** Run as a manager job on fast-0: `sudo -n $HOME/mal-venvs/r4b/bin/python -m tools.floor_fills_vs_model --config /etc/mal-floor-lane/live-320cccc5f2ec81d40d18f8991f11b15d29b52f74.json --since 2026-11-12T00:46:04.584Z`
- Preconditions (a) to (f) of Am.6 must also hold; read Am.6 in full before writing the ask. If the gate passes, write a HELM-ASK for the Am.6 install from the **Am.5 live config** `live-320cccc5….json` (sha256 aa6c355f…), **not the older fc6a config named in the draft**, and **change only** `stake_lamports` to 2.5e8, `budget_lamports` to 2.1e9 and `daily_loss_lamports` to 5e8 (the new values), plus the changed executor blob `tools/floor_lane_executor.py` at Am.6's merge sha 8dec3a3b (Am.6 Install). Use `/data/mal/hunt-1008/ops/manager22/HELM-ASK-1112-1.DRAFT.md` for its format only.
- **Count so far: 2 of 20** (73a31cf948fba66e -0.000633034 `paper_refused`; e3b7cc9de20e65bd -0.004085219 `time_24h`; sum -0.004718253) (QP ruling 2026-11-13, `/data/mal/hunt-1008/ops/QP-RULING-1113-am6-count.md` (sha256 `fb7974f2f5086e0b8e33558a77b161283566135a8417741899cc705408f589c7`)): the unwound round trip `73a31cf948fba66e` COUNTS as trade 1 of 20 (non-rule exit list; its -0.000633034 counts in the sum, budget, daily stop and fills check). `e3b7cc9de20e65bd` has now closed and counts (Am.7 list (ii); disclose the host-down span 00:33:06-02:19:18Z, the lost host take-profit and the 06:03:33Z restart; score flag_c conservatively). Count start 2026-11-12T00:46:04.584Z. Am.6 Erratum 4 is merged (#1351, aa39dd07).
- **Answered by QP (see Count so far):** the unwound round trip `73a31cf948fba66e` counts as trade 1 of 20.
- Am.7 adds: trades under the new host count toward the 20; a fills-check "no" caused only by positions open across a host restart goes to QP first.
- If the gate fails: stay at 0.10, tell the owner; the only re-decision is at 40 trades, with the owner's word.

**What the November paper evidence now says (DEC-035 Am.6 Erratum 3, merged 4f1e9128, #1338; paper, in-sample, send 160,000 lamports).** It changes no decision, limit, code or install condition. No claim that the book is positive.
- The earlier n 50 figures left out 29 positions (24 entered 11-09, 5 on 11-10) that the engine excluded for feed gaps. Priced through the holes they sum to **−0.6951137523 SOL at 0.25 flat** (−0.2845396379 at 0.10 flat). All 29 exit at the 24 h time stop; none shows a take-profit print. 26 of the 29 exits use the last known print (may be stale).
- With the 29 included, 0.25 flat: **uncapped n 79 total +0.0011937554** (ex-top-3 −0.506554); **K=16 total +0.412001** (ex-top-3 −0.095747); K=8 ex-top-3 +0.107295. 5 of 8 days positive. Post-deploy: K=16 +0.022404 (ex-top-3 −0.390959), uncapped −0.388403 (−0.801766). At 0.10 flat uncapped n 79: −0.0129396448.
- **One-position sensitivity:** one of the 29 (FU8vnN6g, −0.25219928 SOL at 0.25) is a strand-rule total loss on a 0.13% k fall, not an observed drain. Without it the 79-position total is **+0.2533930354**; with the other pricing rule +0.2978451033.
- Group A (the 50 normally priced): 0.10 flat K=16 total 0.2716, ex-top-3 0.0682; 0.25 flat K=16 total 0.696308, mean +5.5705%, median −2.2993%, ex-top-3 +0.188559.

**Stage-1 host change (DEC-035 Amendment 7): INSTALLED by Helm 2026-11-13, host stop 14:44:02.156Z, start 14:44:03.517Z** (HELM-ASK-1113-6, HELM-REPLY-1113-6; owner's word in the Helm chat 10:56Z; no rollback).
- Host PID 2625988 -> **2913148**; commit aa05923f (host blob ba7050e1, sha256 201f3d4a…); config `/etc/mal-floor-lane/host-aa05923f.json` sha256 `5ec10c69fb90edbab6bbb317b53544d0fd5947e72dce448aa5e28ae312a31bb4` (= old `host.json` 60f7a60a… + `cheap_restart: true`; `status_span_no_loss` off). Executor untouched (PID 2625989). Unit has no `--since-hour`; backup `/root/mal-floor-lane-host.bak-20261113T1443Z/`.
- Post-install check (a): since_hour null, table_rebuilt_rows 0; lane_start prev_end unknown, fallback `unknown_prev_end`; guard {cheap_restart true, status_span_no_loss false}; lane_restart_shadow row seq 131 shadow_complete false; seed-tmp empty; no HostFatal. **The host refuses every pool until about 2026-11-14T14:47:40Z (+BLOCK_MS); no host restart before about 2026-11-14T14:48Z.** The deploy restart itself is not cheap (by design). Owed: confirm no entry row before then.
- What it does: with `cheap_restart` true, a later restart blocks only the pools the rule bought during the downtime instead of refusing every pool for 24 h. Proofs: P0 PASS #2554 (rests on one decision row, thin); P2 PASS on the relaxed-seed route (#2573); what that does not exercise is in DEC-035 Am.7 Erratum 2 (`DEC/DEC-035-live-learning-lane.md` line 562: "Not exercised: triggers of older pools, and the pool-table age-drop path past 201 h."); tests 76 passed (#2580 and Helm).
- **Kill-line rollback trigger:** any live entry on a pool the unguarded reference holds after a cheap restart, or any HostFatal/HostError on the new paths; then set both flags false and restart the host on the old config through Helm. Rollback tree e89f1536 + `host.json` 60f7a60a… with the ExecStart in force (never the `/root/…bak-20261113T0220Z` unit backup, which carries `--since-hour`). First real kill-line check is owed after the first cheap restart (smoke #2569 only so far).
- **DEC-035 Am.8 (merged 9c52f4fc, #1353; Erratum 1 4aaf30fd, #1354):** part A = `status_span_no_loss` on (P1 PASS #2606; earliest ask after 2026-11-14T14:48Z; (c), (d), (f), (g) still owed). Part B = executor key `a3_floor_flag_list` (HELM-ASK-1113-7, **not installed, needs the owner's word**). The merged executor carries the Amendment 6 code ceilings; Amendment 6 config is not installed. P1 job #2602 crashed (manager error, tape-route deviation from SEALS-BRIEF line (g)); #2606 is the record (Erratum 1; LAB_STATE 2026-11-13c).

**Hourly status.**
- Script: `/data/mal/hunt-1008/ops/manager23/hourly.sh` (from manager22's). Run it as a MiScusi job:

  ```
  B=$(base64 -w0 /data/mal/hunt-1008/ops/manager23/hourly.sh); miscusi-agent job submit --name hourly --machine mal-fast-0 --role ops --mem 512M --cpus 0.2 -- "echo $B | base64 -d > /tmp/h.\$\$.sh; sh /tmp/h.\$\$.sh"
  ```

  then `miscusi-agent job log <seq>`. The bridge-event filter starts at `ts_ms` 1794419100000 (11-11T17:45Z); raise it as the log grows.
- It prints the lane report, both unit PIDs, host checkpoint fields (gaps, hwm, log_seq, gap_refusals), the follower's lag, jumps and retries, disk, and the day's bridge events.
- **Healthy means:**
  - executor 2625989 active, and the host active (PID 2625988) with hwm within the hour (restarted 06:03:33Z; the earlier PIDs 1929135 and 2504565 are gone);
  - follower PID 2625920, NRestarts 0, backlog_jumps 0, lag under ~50;
  - **disk free at or above 27%** (not 22%: 25% is the host's floor).
- Not yet confirmed: at 00:3xZ the lane report for the new UTC day printed `daily stop n/a (field not recorded)`; check it reads ok after the first event of a day.

**Helm protocol.** Requests are files `HELM-ASK-<MMDD>-<n>.md` in `/data/mal/hunt-1008/push-1016/hunt29/p1/`; replies are `HELM-REPLY-*` in the same folder. Helm acts on the owner's word given directly in Helm chat. Verify every reply with a read-only job before recording it. Open asks: **HELM-ASK-1108-1** (reset window opens 11-16T06:00Z, §5).

## 4. Second strategy: r4bt-a2 (paper)
- **DEC-031 Amendment 30 merged f090d5a8** (#1325): paper successor of r4-b-T on the fixed tip feed. **r4bt-a2 is NOT yet registered**: registration only follows a P0 PASS on 2026-11-16, 11-17 and 11-18 (Am.30 items 7, 9). The count job is **2026-11-19T00:10Z**; the clock is 2026-11-20T00:00Z; screen read by 2026-11-27T01:01Z; confirmation about 12-04. W′ = **3c12c5fd** (#1330, the per-bot table).
- **Dry host STARTED 2026-11-13T10:05:01Z (job #2591; paper; NOT registered until a P0 PASS).** Scope `r4b-dry-r4bt-a2-dry-1113a.scope`, host pid 2752773, install_head 3c12c5fddfa67384dca2e306a8bf6c529e85ebfc; M1 gates PASS (memory max 5,368,709,120, swap 0, nice 19); catch-up check #2599 at 10:57:45Z: sidecar LIVE, backlog_jumps 0, lag 0. Jobs: #2590 sidecar, #2592 stop check, #2593 P0 count 2026-11-19T00:10Z, #2594 day-sha loop. Reliable marks from 12:00Z. SEALS-BRIEF line (brief sha256 `3dc40a11…`), ledger note and readers record are in place. No entry count has been read.
- **Rule:** never read an entry count before the J4 count job. A stop after a seen count counts as a P0 miss.
- **Memory:** the dry host's scope is 5120M. Check `systemctl show user-1002.slice -p MemoryCurrent -p MemoryMax -p MemoryHigh` with FLOOR running and record the headroom (dry-long-1101a recorded `M1_SLICE FAIL`: slice MemoryMax 16106127360 below 5120M plus current). No replay job during the host's ~37 min catch-up.
- **Train/serve check (job #2531):** TRAIN = DISCLOSE (migration-row coverage of complete-row graduations 0.0337, 0.0417, 0.0098 on the three blocks); SERVE = PASS, descriptive only (11-08 to 11-11 ratios 1.0344, 1.0495, 0.9944, 0.9180). Am.30 carries the disclosure.
- **Owner question answered (2026-11-13 ~02:3xZ chat, per the state file):** the r4-b forward test is approved, FLOOR stays live, capital for r4-b if it does well. The r4bt-a2 paper book proceeds (dry host install 10:37Z).
- **Owed:** Am.30's heading on main still says "(DRAFT;" (T1 erratum).
- Tip-health successors for **#2055 and #2064** are needed before 2026-11-16T00:00Z (P0 days). The tip writer/push successors #2503/#2504 end about 11-29T06Z, inside the confirmation window.

## 5. Fixed steps (UTC)
Re-create the crons from `crons-manager23.json` with CronCreate.

| UTC | Step |
|---|---|
| hourly :17 | HELM-REPLY-*, PR verdicts, miscusi_inbox (manager23 is the registered inbox reader, worker d_11fM1Ytqx_o0Mg), the FLOOR hourly job (§3), wallet, disk (27%); one state line |
| 11-13 (done) | HELM-REPLY-1113-2 verified (job #2555); watch successor submitted |
| 11-13 (done) | The QP ruling on the vacuous P2 was followed: #1346, #1347, #1348 merged; P2 PASS #2573 (§3). |
| 11-13 (done) | Watchdog #1952 successor queued as **#2586** (main 4ef63d48, DURATION_S 1200000). A3 successor commands recovered: `/data/mal/hunt-1008/ops/manager23/A3-SUCCESSOR-COMMANDS.md` (#1896 ref 136cd98d 512 MB 0.1 CPU; #1898 ref 0ec7fae8 1 GB 1 CPU; both 18,720 min on fast-0), for the 11-17 step. |
| 11-13 (done) | r4bt-a2 dry host #2591 started 10:05:01Z (§4); stage 1 installed 14:44Z (§3); open trade `e3b7cc9de20e65bd` closed 14:06:11Z (time_24h). |
| **11-13 19:10 / 19:23** | A3 record run about 19:10Z; check at 19:23Z (session cron; `ops/manager23/a3chk.sh`, `a3detail.sh`). |
| **11-14 12:00** | Owner question q_h5R6dhNNtqnGyQ (HELM-ASK-1113-7) decide-by. |
| **11-13 19:23** | A3 record check (session cron; the A3 run is about 19:10Z): scripts `ops/manager23/a3chk.sh` and `a3detail.sh`; never edit the record. Notebook n_L5RQa1IeCA4Jlg. If the 19:10Z and the 11-14T07:10Z records both still halt, FLOOR stays paused after the refuse-all ends; then re-check (DEC-035 §4). |
| **11-14 01:00 (R)** | **EXP-029 read, procedural FAIL** (Erratum 1g; cron 01:07). The event was the 0.10 install at 2026-11-12T00:46:04.584Z, so C = 11-13T00:00Z and R = C + 25 h. Run the read once, at or after R:<ul><li>the four §4 tests on trades in [S = 11-09T06:00Z, C), pooled and split by stake (0.03 / 0.10);</li><li>the pre-S sends listed separately (Erratum 1, item 4);</li><li>the report labelled **FAIL (procedural)**, never a market verdict;</li><li>QP before merge;</li><li>quote the Erratum 1e MAJOR 1 disclosure and the QP ruling;</li><li>**disclose the host-down span from 2026-11-13T00:33:06Z** (the signal host made no entries) **and the unattended restart of host and executor at 2026-11-13T06:03:33Z**;</li><li>**disclose the lost host take-profit on the open pre-C position `e3b7cc9de20e65bd`** (it exits on the executor's 24 h clock).</li></ul> |
| **11-14 ~14:48** | FLOOR entries resume (stage-1 lockout ends about 14:47:40Z + BLOCK_MS), only if the newest A3 record is clean. Count closed 0.10 trades toward Am.6 (2 of 20). Am.8 part A ask earliest after this. |
| 11-14 12:00 | Host check #2348 (pin) runs; read its exit code. A restart through Helm's drop-in may change what it sees; not confirmed. |
| daily 00:23 | floor-forward-ab job on research-0 (`scripts/research/floor_forward_ab.sh <last closed hour>`). Check the job log for a path-guard stop. Job #2537 (end hour 2026-11-12T23, main aa05923f) was submitted 00:13Z (done), so no second job that day. |
| weekly | Clean stale job trees on fast-0 (disk; #2556 removed 82 on 11-13). |
| **11-15 06:41** | **Tip hand-off check** (`docs/runbooks/tip-writer-handoff.md` §5/§6). Writer #2033 hits its limit ~06:09Z, push #2039 ~06:24Z. Gates #2501 (deadline 08:08Z) and #2502 (08:23Z) release #2503/#2504. Confirm the gates exited 0; the successors are running, resumed and caught_up, with no REFUSED; a counts-only FLOOR host gap check. A gate exit of 4 or 5: follow the runbook by hand. |
| 11-15 12:05 | Latest r4bt-a2 dry host start (§4) |
| **by 2026-11-15T18:00** | **Start successors for #2055 and #2064** (they end ~11-15T23:30Z–11-16T00:15Z; the r4bt-a2 P0 days need them). Cron **326cd178** fires 2026-11-15T17:13Z for these successors (added 2026-11-13T01:29Z); the 11-15 06:41 check is the backstop. |
| ~11-15 23:30 – 11-16 00:15 | #2055 and #2064 reach their limits: **successors needed before 2026-11-16T00:00Z** (r4bt-a2 P0 needs the tip health). |
| 11-16 03:23 | vault1.0 out-of-sample paper check (cron). Rule in §6. |
| 11-16 06:13 | HELM-ASK-1108-1 reset window opens (06:00Z to 11-21T00Z). Nudge Helm; use the **current** pin and config, only the one state field changes (ADDENDUM-1). |
| 11-16, 11-17, 11-18 | r4bt-a2 P0 days; count job #2593 at 2026-11-19T00:10Z (§4) |
| **11-17 09:13** | **A3 successors are required for FLOOR and r4bt-a2.** The executor checks the A3 gate in `/var/lib/mal-hv4-c0/a3/daily.jsonl`: #1896 deadman ends ~11-18T21:17Z; #1898 A3 loop ends ~11-21T01:01Z. Confirm `run_utc` advances. Check #2134's consumers (ends ~11-21T02:31Z). **Re-create the hourly cron** (it expires after 7 days, ~11-19). |
| 11-21 00:05 | #2353 reset check (exit 3 = not done; chase Helm) |
| 11-21 18:13 | Successors to END 2026-11-30T07Z for #2290 daily report and #2291 health watch (both end ~11-22T13Z), **#2266 FLOOR tip link-dir** (~11-22T01:43Z), #2062 follower health (~11-21T01Z). |

MiScusi refuses job limits above 14 days. `after` fires only on exit 0, never on a time-limit stop.

## 6. Research lines and findings of 11-12 / 11-13 (all paper, in-sample; reports under `/data/mal/hunt-1008/ops/reports/`)
- **Library sweep** (`2026-11-12/stack-candidates-library.md`): no library idea reaches 0.108 SOL/day; FLOOR clones on other venues are clean negatives; r4-b is the only large candidate, with weak evidence. That led to r4bt-a2 (`2026-11-12/r4b-revival-scope.md`: the kill cause was the follower fault fixed 11-07).
- **Throughput levers** (`2026-11-12/floor-throughput-levers.md`): FLOOR alone is about 0.08 SOL/day at 0.25 in-sample; the gap guard is the biggest live leak. Guard v2 and cheap restart design: `2026-11-12/floor-gap-guard-v2-design.md` (became stage 1, §3).
- **Gap-range result** (`2026-11-12/floor-gap-range.md`, job #2527, QP-checked via #1336, merged 3b0a5c38): see the Erratum 3 figures in §3.
- **Burst study** (`2026-11-13/floor-burst-study.md`): 11-09 had 28 entries in one wave (10:46–12:34Z); at 0.25 flat that day: mean −7.40%, median −2.88%, sum −0.5179 SOL. One hole-priced position (FU8vnN6g) is 49% of the day's loss. Caps are not monotone (K=3 +0.211, K=5 +0.186, K=8 +0.615, K=12 +0.502, K=16 +0.412, uncapped +0.001). No limit is established; no config change.
- **Landing-vault study** (`2026-11-13/floor-landing-vault-study.md`): the landing vault is bimodal (38 at ≤0.5 SOL, 3 at 0.5–0.6, 38 at ≥0.87). 0.25 flat uncapped: ≤0.5 n 38 +0.091848 (ex-top-3 −0.258573) against >0.5 n 41 −0.090654 (ex-top-3 −0.579139). Not separable; n small. The live executor has no trigger-price or vault filter. No change; add `land_vault_sol` and `entry_over_mark0` bins to the daily forward A/B table (owed).
- **Rule variants** (`2026-11-12/floor-variants.md`, 0.10 flat, same-send base total 0.2716, n 50): age12 Δ −0.031 REJECT; vault0.75 Δ −0.018 REJECT; vault1.0 (Q_STAR 0.5e9 → 1.0e9) Δ +0.0625, 4 of 7 days, WEAK, not adopted. Adopting it needs a new frozen rule `l1-floor-v3`, a second host and executor under a DEC-035 amendment, and QP (`2026-11-12/floor-v3-change-surface.md`).
- **Forward A/B** (job **#2537** done; read its `ab.md`). The forward A/B counts COMPLETE forward days only; the newest forward day of the old job #2525 `ab.md` is not quotable (open positions mislabelled as feed_gap). **Decide vault1.0 after at least 7 complete forward days** (cron 11-16 03:23).
- **Other lines are off:** EXP-012 shadow, Haiku forge, slowbot, the HV books, h5 and s1t.

## 7. Owed (not done)
- **Durable disk plan for fast-0:** the c1nf-ledger (job #2545) and backfill-fast-b / -c (job #2562) moves are done (`/` 44% free); still owed: tip retention, `public-logs.jsonl` (835 MB and growing). Disk guard successor beyond #2546's limit.
- **Stage 1: INSTALLED 2026-11-13T14:44Z (§3).** Owed: the no-entry-row-before-lockout-end check, the first real kill-line check after a cheap restart, the SEALS-BRIEF line (g) note, the kernel reboot plan (6.8.0-149 pending; a reboot before about 2026-12-04 would end the r4bt-a2 paper host).
- **`set -f` MINOR** around the symlink-component loop in `scripts/research/floor_gap_range.sh`.
- **Forward A/B bins** for the landing vault (`land_vault_sol`, `entry_over_mark0`).
- **Records batch for 11-13:** PRs 2026-11-13, 13b (#1350) and 13c (this update) cover events to 14:51Z; later events go in the next daily batch.
- **Am.30 heading** "(DRAFT;" erratum (T1); r4bt-tip library row wording.
- **Small MINORs from the state file:** #1330 follow-ups (mutable table, wrapper clock check and provenance, `--bot-id=X` form); #1328 nits (attribution of nits to the #1319 QP, "about 0.007" wording); Erratum 3 report-wording fixes (host tick; 26 last-print exits; "pricing-rule total loss"); the 41 of 79 entries landing above 0.5 SOL real vault need a separate look.
- **From manager22:** the variants script branch `/data2/mal/wt/floor-variants` (#1324 merged the research scripts; check it is covered); the #1652 let-end (11-12T00:30Z) and #1814 let-end (11-11T07:20Z) LAB_STATE rows; the DEC-031 `--backlog-slots` note (tip-source books pin none); follower restart LAB_STATE rows (11-11T17:53:56Z; 11-12T17:58:35Z, PID 1559764 → 2254354).
- **Stale open PRs (#905–#1263):** parked or never-merge. Triage only if there is time.

## 8. Running jobs (from the state file, 11-13T14:5xZ; do not start anything from this list)
- **FLOOR:** #2290 daily report; #2291 health watch (exited 3 on the host-down alert); successor #2558 (seg2) exited 3 on the 06:03Z restart alert; seg3 #2578 exited 3 on the stage-1 PID change (expected); successor **#2614** (host 2913148, executor 2625989; running); **#2546** disk guard at 27% (running); **#2566** keeper `tapecopy-roll` (research-0, running); #2266 tip link-dir; #2348 and #2353 checks; #2452 old disk guard (24%; at its limit, superseded by #2546).
- **Done, with results:** #2537 daily forward A/B (done); #2554 P0 proof (PASS); #2563 P2 proof (exit 3, vacuous); #2567 P2 fallback (exit 3, vacuous); #2573 P2 relaxed-seed proof (PASS); #2580 stage-1 unit tests (76 passed); #2602 P1 (CRASHED, no result; see LAB_STATE 2026-11-13c); #2606 P1 re-run (PASS); #2605 Am.8 tests (294 passed); #2603/#2610 A3 count; #2568 kill-line smoke step A (done); #2569 kill-line smoke step B (exit 0, SMOKE); #2585 wide A3 probe; #2587 A3 trend; #2588 and #2589 r4bt-a2 §0 gate checks. **r4bt-a2 dry jobs (running or queued):** #2590 J1 sidecar, #2591 J2 dry host (starts 10:05Z), #2592 J3 stop check, #2593 J4 P0 count (11-19T00:10Z), #2594 J5 day-sha loop.
- **Tip:** #2033 writer and #2039 push (end ~11-15T06Z); gates #2501/#2502 with successors #2503/#2504 queued; #2055, #2062, #2064 health jobs.
- **Guards:** #1952 watchdog; #1896 and #1898 A3 guards (job_list only); #2214 → #2217 structure monitor; #2134 venue tape.
- **fast-0 limits:** 8 CPU slots; `--mem` required, `--cpus 0.2` for status jobs; jobs run under `sh`. One heavy replay at a time, at most 2 workers; check `systemctl show user-1002.slice -p MemoryCurrent` first.
- No open PR of manager23's matters right now (#1337, #1341, #1342 merged). The others are parked.

## 9. Exact next steps (priority order; updated 2026-11-13T14:5xZ)
1. **Owner's word for HELM-ASK-1113-7** (Am.8 part B: executor key `a3_floor_flag_list`, pin 9c52f4fc; executor-only restart, no lockout). Sent 14:22Z; owner question q_h5R6dhNNtqnGyQ (decide by 2026-11-14T12:00Z). `for_you` item on the Console. Not installed until the owner says OK to Helm.
2. **19:10Z A3 record, check at about 19:23Z** (`ops/manager23/a3chk.sh`, `a3detail.sh`; never edit the record). The 19:10Z and 11-14T07:10Z records both still halting means FLOOR stays paused after the lockout ends; then re-check (DEC-035 §4). A3 successors are due 2026-11-17 (§5).
3. **2026-11-14T01:00Z: EXP-029 R read** (procedural FAIL; disclose the host-down span 00:33:06-02:19:18Z, the 06:03:33Z unattended restart and the 14:44Z stage-1 restart; QP before merge).
4. **FLOOR entries resume about 2026-11-14T14:48Z** (stage-1 lockout ends about 14:47:40Z plus BLOCK_MS). Do the owed check: no entry row before then. No host restart before then. After it: first cheap restart gets the kill-line check (`docs/runbooks/floor-killcheck.md`; no restart while a check is pending).
5. **Am.8 part A ask** (`status_span_no_loss` on; P1 PASS #2606 met): earliest after 2026-11-14T14:48Z, and only with preconditions (c) status_failures + blobs check, (d) stage 1 installed and more than 24 h after its deploy restart, (f), (g) in hand (read Am.8 and Erratum 1 first). Same visit as the Am.6 0.25 step when the count reaches 20 (Am.6 gate: 2 of 20; Erratum 4 is merged; fills check at trades 10 and 20).
6. **Owed records:** SEALS-BRIEF line (g) dated note citing Am.8 Erratum 1 (QP minor m2 on #1354); delete `p1-1113` only after the part-A install record (keep read-only until then); LAB_STATE row for every host/executor restart.
7. **r4bt-a2 (paper, NOT registered):** dry host #2591 running since 10:05:01Z; day-1 dry check 11-15; successors for #2055/#2064 before 2026-11-16T00:00Z (cron 326cd178, 11-15T17:13Z); P0 days 11-16/17/18; P0 count job #2593 at 2026-11-19T00:10Z. Never read an entry count before it.
8. **Stage-1 watch #2614** (host 2913148, executor 2625989; `ops/manager23/floor-watch.sh`, limit 4320 min): read its alerts; fast-0 `/` at or above 27% free; weekly clean of stale job trees. Hourly job every hour.
Also: 11-15 06:41Z tip hand-off check; 11-17 09:13Z A3 successors (`A3-SUCCESSOR-COMMANDS.md`) and re-create the hourly cron; forward A/B (no vault decision before 7 complete forward days); tip retention and `public-logs.jsonl` (§7).

### A3 halt of 2026-11-13T07:10:05Z (Helm FYI HELM-REPLY-1113-5; answer HELM-ASK-1113-5: no action)
- The A3 record at 07:10:05Z has `halt.any=true`: boost_share_low (0 non-mayhem WSOL graduations of 20, InitBoost 0/8 WSOL) and synthetic_share_high (11/20); two boost rules not evaluated; pooled guard a3-applied verdict HALT.
- The FLOOR executor pauses NEW entries by itself while the newest A3 record has a set or not-evaluated flag (`a3_pause_tick` in `tools/floor_lane_executor.py`). It is not a latch: it clears when a later record is clean (next runs about 19:10Z and 11-14T07:10Z). Exits keep running. No `a3_pause` row yet, because the host refuses all pools until about 2026-11-14T06:03Z. No STOP file, no LIVE_OK change.
- Wide probe job #2585 (public RPC, n 80): WSOL 52/80 (non-mayhem 20, all InitBoost; mayhem 32), other quote 28/80 (11 quote mints; 26 InitBoost, 24 synthetic); mix over 120: wsol 86 / usdc 5 / other 29 (24%). Trend job #2587: other-quote share bursts to 20-25% of the 60-mix on 10-29/30, 11-01, 11-04, 11-08, 11-12, then 28/60 at 11-13T07:10Z, the largest so far. Reading: a burst of other-quote launches plus a quiet hour; not yet a regime change; decide on the 19:10Z record. **Cause (report `/data/mal/hunt-1008/ops/reports/2026-11-13/other-quote-graduations.md`, notebook n_eYfBbStvz_mpMw; public RPC, structure counts only):** of the 120 newest graduations 30 were other-quote over 12 quote mints; one creator account made 19 of the 30, all at 06:31-06:38Z, on two quote mints that are themselves pump-style coins (14 and 5); the other 11 coins have 11 creators. Reading: one account's 7-minute burst sat inside the 07:10Z sample of 20; not a platform change. Expect the 19:10Z record to clear unless it bursts again. Not measured: prices, sizes, tradeability, the two-week history by creator. If the 19:10Z and 11-14T07:10Z records both still halt, FLOOR stays paused after the refuse-all ends; then re-check (DEC-035 §4). Notebook n_L5RQa1IeCA4Jlg.

## 10. How to verify this handoff
- `git log --oneline -1` on main shows this PR or later.
- The last lines of `state-manager23.md` mention this handoff; the 2026-11-13T01:19Z line is the CAUSE + FIX entry for the host start and the 06:03Z line records the unattended restart.
- The hourly job shows the host active under PID 2625988 (unless restarted and recorded), executor 2625989 active, follower PID 2625920 (unless restarted and recorded), disk at or above 27% (44.68% at 08:41Z).
