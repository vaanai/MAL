# MAL manager handoff: manager23 → next manager (or manager23 after a context compaction), written 2026-11-13T01:3xZ, rewritten 2026-11-14T15:3xZ from `date -u` (state file through the 15:31Z hourly #2741)

This replaces the manager22 handoff (written 2026-11-12T20:5xZ–21:1xZ). manager23 took over at 2026-11-12T21:25Z. The state file is `/data/mal/hunt-1008/ops/manager23/state-manager23.md` (one line per event, newest last); the crons are `/data/mal/hunt-1008/ops/manager23/crons-manager23.json`. Main head when this was written: 6061edb7 (`git rev-parse origin/main`; #1393, the last merge of 2026-11-14; this records PR comes after it). Merged on 11-14: #1376 to #1393 (see LAB_STATE "Now additions, 2026-11-14"); on the evening of 11-13: #1356 to #1375.

**Read in this order:**
1. `CLAUDE.md`
2. This file, in full.
3. The tail of `/data/mal/hunt-1008/ops/manager23/state-manager23.md`.
4. The newest MiScusi notebook entries.

**Before anything else:** run CronList and re-create the session crons from `crons-manager23.json` (§5), then run one hourly status job (§3). A new session has no crons, and fixed-instant steps must not rest on session crons alone (lesson of 2026-10-15).

**No book has cleared the promotion gate.** One bot trades real money: **FLOOR floor-lane-1**, the DEC-035 live-learning lane on mal-fast-0. Nothing below says it makes money.

## 1. STATE OF THE LIVE BOT (updated 2026-11-14T15:3xZ): host restarted by Helm at 15:01Z with `status_span_no_loss` on (the first cheap restart); 0 open; no entry yet since the lock-out ended at ~14:47:40Z; kill-line check owed

- **Host:** `mal-floor-lane-host.service` PID **3750092** (stopped 2026-11-14T15:01:23.585Z, started 15:01:34.202Z by Helm; HELM-REPLY-1114-2, done, no rollback). ExecStart `--config /etc/mal-floor-lane/host-aa05923f-ssnl.json`, sha256 `dc833d79d388729e1c32f34e7a12f1d7b77abb73ccd9206a995969497316c975` (= `host-aa05923f.json` 5ec10c69… plus `"status_span_no_loss": true`; nothing else). Tree aa05923f, host sha256 `201f3d4a…`. No `--since-hour`. Backup `/root/mal-floor-lane-host.bak-20261114T1501Z/`.
- **First cheap restart worked** (my check job #2738, 15:06:48Z; names and counts only): `lane_start` checkpoint true, `prev_end` "signal", `shadow_active` true, `shadow_fallback` null, down_ms 30,746, carried_gaps 1, resumed open 0, dropped 0; start row 15:01:35.772Z (guard cheap_restart true, status_span_no_loss true); `lane_restart_shadow` row 15:05:03.371Z. **No new 24 h lock-out.** The old lock-out (from the 11-13T14:44Z install) ended ~14:47:40Z with 66 gap refusals and 0 entries.
- **Executor:** `mal-floor-lane.service` PID **3018006** (Am.8 part B install 2026-11-13T17:40:26Z; untouched since), tree `/usr/local/lib/mal-floor-lane-executor/9c52f4fc…`, blob fd7e36c1, sha256 `9c8fa0b3…`, config `/etc/mal-floor-lane/live-9c52f4fcfdccfb0775736eec050f8023c5e9a381.json` sha256 `40241dc6a78e63ce35610dcadb837da09feb369ed3c795b503bd255d247e56db` (old config plus `a3_floor_flag_list` true; FLOOR ignores `synthetic_share_high` and the boost-share flags, DEC-035 Am.8 B).
- **Counts at 15:31Z (hourly #2741; a count, not a gate read):** 0 open; 11-14 realized 0 (0 sends); wallet **3,155,784,860 lamports**; lane cost-capped pnl +0.010363342 SOL over 12 round trips; **Amendment 6 gate count 2 of 20**; log_seq 146; gap_refusals 66; follower lag 1, jumps 0; disk 45.10% free. FLOOR made no new trade on 2026-11-14 so far.
- **A3 guard: FLOOR is no longer blocked.** The pump program redeploy (2026-11-13T20:12:28Z) halted the A3 record of 11-14T07:10:05Z on `pins_changed`; review #1383, pin commit P `c687753b8d220b4de3efb379741a8178e15ba58a` (tag `a3-guard-pin-1114`), #1898 cancelled, successor loop **#2708** at P; the resume run of **2026-11-14T09:17:44Z** met every pre-declared condition (QP-RULING-1114-a3-repin-pump.md and ADD1). The root copy `/var/lib/mal-hv4-c0/a3/daily.jsonl` equals the source (carry timer active). Pre-resume check #2732 at 14:30:35Z clean. **First-candidate evidence is still owed:** at the first candidate after ~14:47:40Z read, counts and instants only, 0 `a3_pause` rows plus a `buy_sent` or an `entry_refused` whose why does not start with `a3_pause` (QP ADD1). If no `a3_resume` row appears within 15 min of a good copy, report to QP; no executor restart.
- **Kill-line check owed:** after the first live entry following the 15:01Z restart, run the kill-line check (`docs/runbooks/floor-killcheck.md`). **No further host restart (Helm or us) before it passes**; Helm agreed in HELM-REPLY-1114-2. Rollback trigger: any live entry on a pool the unguarded reference holds after a cheap restart, or any HostFatal/HostError on the new paths; then both flags false and restart the host on the old config through Helm (rollback tree e89f1536 + `host.json` 60f7a60a… with the ExecStart in force, never a unit backup that carries `--since-hour`).
- **After any host start, record it in LAB_STATE** (done up to 15:01Z in "Now additions, 2026-11-14"). A host start is cheap now (blocks only pools bought during the downtime); the executor is restarted only through Helm.
- **Earlier incidents** (11-13 00:33Z host stop on the 25% disk floor; Helm starts 01:15Z and 02:19Z; 06:03:33Z unattended libc6 restart and Helm's needrestart exclusion; 14:44Z stage-1 install; trade 2 close at 14:06:11Z) are recorded in LAB_STATE "Now" (the 2026-11-13, 13b and 13c blocks). One lesson stays here: fast-0 `/` must stay at or above 27% free (see §2).
- **Open trades:** none. Both counted trades are closed: `73a31cf948fba66e` -0.000633034 SOL (`paper_refused`), `e3b7cc9de20e65bd` -0.004085219 SOL (`time_24h`; host-down span, lost host take-profit and 06:03:33Z restart disclosed).

## 2. Do not break these (read first)
1. **The host `mal-floor-lane-host.service` is restarted only through Helm by HELM-ASK** (DEC-035). Stage 1 and Am.8 part A are installed (§1). **No host restart before the kill-line check of the 15:01Z restart passes** (Am.7 rule; Helm agreed). The executor `mal-floor-lane.service` is also restarted only through Helm. Each restart is logged in LAB_STATE, the state file and the notebook.
2. **fast-0 `/` must stay at or above 27% free.** The host dies at its next trade under 25%. Disk guard job **#2546** alerts under 27%. The old guard **#2452** is superseded.
3. **Never start the host with `--since-hour` when a checkpoint exists.** It copies tape into `seed-tmp` and eats the disk.
4. **Job #2527's result file is read-only evidence** (`/data2/mal/scratch/floor-gap-range/gap.json`; sha256 in `/data/mal/hunt-1008/ops/reports/2026-11-13/floor-gap-range-job2527.sha256`). Do not overwrite or re-run into it.
5. **Code PRs are always T1** (separate QP and reviewer agents; DEC-013 Am.1 trigger 1).
6. **The wallet key is root-only and Helm's.** No Claude session reads, prints or copies it. Only the manager starts a lane book. Workers never start, fund, configure or restart a lane executor.
7. **Never `miscusi_job_status`** on any of these: #1561 #1562 #1419 #1421 #1543 #1540 #1446 #1036 #1290, #1567 #1568 #1569, #1559 #1560, #2018 #2019, #2219 #2251, or any s1t / h5v2 / A3 / score / shadow / smoke / judge job. For their limits use `miscusi-agent job list`. This includes the A3 guards **#1896 and #2708** (#1898 was cancelled). Logs of RUNNING fast-0 jobs are readable only on fast-0.
8. **Never open** `/var/lib/mal/sealed/*` or any `tape-*` file.
   - Readers of `/var/lib/mal-live/**` are manager jobs only, and they print counts and order ids only.
   - Paste `/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md` verbatim into every brief that touches tape (brief sha256 prefix `c9d659ed` as of 2026-11-14T12:47Z; lines (j) to (o) cover the open-sim board, follow driver, keeper, board, EXP-029 tool and recon; R4-B PREVIEW, PRICE KEEPER, DECLOG COPY lines). Never list sizes or mtimes of declog copies or originals before 2026-11-19T00:10Z (entry-count proxy).
9. **Merges.**
   - Every PR needs `QP VERDICT <head> OK` and `REVIEWER VERDICT <head> APPROVE`, both on the PR at the exact head. T1 PRs need separate agents.
   - Merge with `gh pr merge N --squash --match-head-commit <sha>`. Workers never merge. Check that `baseRefName` is `main`.
10. **Hook word.** A hook blocks the plural word for credential material in Bash text. Write "credential material", and pass PR bodies with `--body-file`.
11. **Do not delete before the part-A install record is written:** the frozen copy `/data2/mal/scratch/floor-stage1-proof/tapecopy-1113` with its `.list` and `.sha256`, the keeper dir `tapecopy-roll` (keeper job #2566), and `p1-1113`. The open-sim keeper (#2630) reads `tapecopy-roll`.
12. **Do not touch** the firewall, sshd, cloudflared, Cloudflare Access or `/opt/miscusi`. The Helius key and the DB password stay where they are.
13. **research-0 `/data2/mal` has a default ACL** (group mal-ro = Helm's read-only account `lyra-ro`, r-x; Helm confirmed it is intended, HELM-REPLY-1114-2). Every new sealed dir needs explicit `chmod` and `setfacl -b -k` before the first write; umask is not enough.
14. **One board process only** (`osb_board`, job #2701); a BOOKS.json change means a board restart. One copier job only (#2642). Books run only from `/data2/mal/wt/osb-457795f4`; twin books from the worktree of their own commit (`osb-3c3d1920`, `osb-861952ee`).

## 3. FLOOR floor-lane-1 (the one live bot)
**What it does.** The host runs the frozen rule `l1-floor-v2` (`tools/arena_rule_l1.py`, frozen sha 5e12f8f5) on the fast-0 tip tape. When the host triggers, the executor buys. Exits are take-profit at 1.5x or a 24 h time stop.

**Live config (DEC-035 Amendment 5; executor config updated by Am.8 part B).**
- **Executor:** PID **3018006**, pin `9c52f4fc…`; config `live-9c52f4fcfdccfb0775736eec050f8023c5e9a381.json` sha256 `40241dc6…` (§1). Drop-in `zzzz-pin-9c52.conf`. Rollback of Am.8 part B: remove the drop-in, `systemctl daemon-reload`, restart the executor (through Helm; backup `/root/mal-floor-lane.bak-20261113T1740Z/`).
- **Limits:** stake **0.10 SOL**; max_open 16; lifetime loss budget **1.20 SOL**; daily stop **0.20 SOL**; fee reserve 3,860,000 lamports. The merged executor carries the Amendment 6 code ceilings; Amendment 6 config is not installed.
- **Wallet:** `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk` (public key only), **3,155,784,860 lamports** at 2026-11-14T15:31Z. Check (lamports in `result.value`): `curl -s https://api.mainnet-beta.solana.com -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"getBalance","params":["5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"]}'`.
- **Lifetime realized:** +0.015081595 SOL from the 10 closed trades at 0.03 SOL (lane report, job #2536); lane cost-capped pnl over all 12 round trips +0.010363342 SOL. The unwound round trip is counted inside the 12.
- **Gap guard.** A tip-status gap makes the host refuse any Buy whose 6 h lookback overlaps it, and block that pool for 24 h. With `status_span_no_loss` on, a status-only span is not counted as a loss span (Am.8 A). `gap_refusals` 66 at the 15:31Z hourly (the refuse-all after each restart counted most of them).

**EXP-029: closed 2026-11-14 (FAIL, procedural).** One look at 01:00:18.350Z (job #2654): 6 trades in [S, C), all negative, total -8,691,510 lamports; the four tests fail. Record #1376, ledger line #1377. Any later registration keeps S = 2026-11-09T06:00Z.

**Am.6 gate (DEC-035 Amendment 6, merged 8dec3a3b; NOT installed).** Am.6 raises the stake to 0.25, budget 2.10, daily stop 0.50 (owner accepted 11-12 ~06:4xZ, memory `owner-scale-1112`).
- **Take the first 20 closed 0.10 trades** since 2026-11-12T00:46:04.584Z, in lane-ledger exit order. Their realized P&L must be **≥ 0 after all fees**.
- **The fills check must print `FILLS_MATCH_MODEL yes` at trade 10 and at trade 20.** Run as a manager job on fast-0 with the CURRENT live config: `sudo -n $HOME/mal-venvs/r4b/bin/python -m tools.floor_fills_vs_model --config /etc/mal-floor-lane/live-9c52f4fcfdccfb0775736eec050f8023c5e9a381.json --since 2026-11-12T00:46:04.584Z` (the old `live-320cccc5…` path in earlier versions of this file is stale; confirm the tool accepts it before relying on it).
- Preconditions (a) to (f) of Am.6 must also hold; read Am.6 in full before writing the ask. If the gate passes, write a HELM-ASK for the Am.6 install from the **live config above**, change only `stake_lamports` to 2.5e8, `budget_lamports` to 2.1e9 and `daily_loss_lamports` to 5e8, plus the executor blob at Am.6's merge sha (Am.6 Install; bundle with Am.10 if the owner said yes). Use `/data/mal/hunt-1008/ops/manager22/HELM-ASK-1112-1.DRAFT.md` for format only.
- **Count so far: 2 of 20** (QP ruling `/data/mal/hunt-1008/ops/QP-RULING-1113-am6-count.md`): `73a31cf948fba66e` -0.000633034 and `e3b7cc9de20e65bd` -0.004085219; sum -0.004718253. Count start 2026-11-12T00:46:04.584Z. Am.6 Erratum 4 merged (#1351).
- **Stake step recommendation to the owner (notebook n_DcWfWa7aKmpvAw, 2026-11-14T03:41Z):** no early step; decide at trade 10 on (a) a clean fills check and (b) open-sim book A matching live entries and exits (the recon, §3 below); otherwise keep 20. Any change needs the owner's word and a DEC-035 amendment.
- If the gate fails: stay at 0.10, tell the owner; the only re-decision is at 40 trades, with the owner's word.

**What the November paper evidence says (DEC-035 Am.6 Erratum 3, #1338; paper, in-sample, send 160,000 lamports).** It changes no decision, limit, code or install condition.
- 29 positions (24 entered 11-09, 5 on 11-10) were excluded by the engine for feed gaps; priced through the holes they sum to **−0.6951137523 SOL at 0.25 flat** (−0.2845396379 at 0.10 flat). With them, 0.25 flat uncapped n 79 total +0.0011937554 (ex-top-3 −0.506554); K=16 +0.412001 (ex-top-3 −0.095747). At 0.10 flat uncapped n 79: −0.0129396448. One of the 29 (FU8vnN6g, −0.25219928 SOL at 0.25) is a strand-rule total loss on a 0.13% k fall; without it the 79-position total is +0.2533930354.
- Group A (the 50 normally priced): 0.10 flat K=16 total 0.2716, ex-top-3 0.0682; 0.25 flat K=16 total 0.696308, ex-top-3 +0.188559. Any older "0.06 SOL a day" figure left these 29 out and was too high.

**Open simulation books (DEC-035 Am.9, Errata 1 to 3; paper; in-sample until forward n grows; owner direction 2026-11-13: open real-time sim books, every trade visible, sim vs live fills is the accuracy test).**
- **Proof:** #2632 (48 h) VACUOUS; rerun #2662 PASS (2026-11-14T07:16Z; `QP-RULING-1114-osb-proof.md`). **Six strict books started 2026-11-14T07:17:23Z to 07:19:00Z** (code `457795f4`): A #2670, D1 #2672, C #2673, E1 #2674, E2 #2675, D0 #2677 (D0 #2671 void). Start instants in `ops/manager23/osb-book-starts.txt`; notebook n_AnUyixJnMpExLA.
- **Board #2701** (hourly, `~/data/open-sim`, worktree `/data2/mal/wt/board-c47c95fd`), **live extract #2696** (fast-0, hourly :15), **recon #2700** (hourly :50). First recon failed on a tool bug and on the order unwound at the 11-13 host crash; after the fix the hourly recon still reads FAIL by the non-rule cap (matched 2, sim_only 3 `restart_lockout`; `live_nonrule_exit` 1 of 2 matched); S1 to S4 INSUFFICIENT. Live landing 6,118 / 7,251 ms vs the model's 1,900 ms. No book is decision-grade.
- **Latency:** strict books land an entry 16 slots (~3.44 s) after the decision; live median 19 slots (~4.09 s), slowest 26 (~5.59 s) (notebook n_y6gjrWNefHB6Wg).
- **Twins:** gap-in-hold twin A-gh: proof #2669 PASS, twin check #2729 (C2 unsatisfiable) then **#2731 OVERALL PASS** under Erratum 3 (C2r); scratch run **#2730** since 14:22:56Z, NOT on the board. Next: add `A-gh.json` to BOOKS.json (twin_of osb-a) and restart the single board job; **re-run C3/C4/C5 daily** until >= 20 positions priced in both and >= 5 gap_in_hold rows, or 7 days. Latency twins A-lt-a / -b / -c (3.010 / 4.085 / 5.590 s, declaration n_HTprEwK--pptQA): proofs **#2723** (running), **#2724** (running), **#2725** (queued); none starts before an L2r PASS (`osb_twin_check` at `/data2/mal/wt/twincheck-6061edb7`, `MAL_ARENA_TEST` unset, finished legs only, `--must-cover-from 2026-11-11T12 --must-cover-hours 48`). Wider-rule twin proofs **#2726** (C strict) and **#2727** (C + C-gh) queued.

**DEC-035 Am.10 (executor key `rpc_rps`; merged #1385 459973b0, code #1384 e5fd255b; NOT installed).** Live buys wait ~2.05 s decision to send (12 buys: 2,048 to 2,105 ms on 11, 2,739 once) because the lane RPC limiter is 2 rps with 5 serial pre-send reads. Am.10: top-level key `rpc_rps` 4.0; install pinned to `tools/floor_lane_executor.py` blob 1807db6a; "worked" = bridge `decision_to_send_ms` median <= 1,300 ms, > 1,700 ms = no effect; rollback if > 1 counted RPC-error refusal row in the window; end point 7 days; pre-change baseline job #2715: 0 counted rows over 12 buys. **Owner question q_9XkUow_sEaMEvw (key DEC-035-Am10-rpc-rps): install at the next Helm visit / only with the 2026-11-16..11-21 reset / no; decide by 2026-11-15T12:00Z.** On yes, write the install HELM-ASK (new executor tree containing e5fd255b, tests re-run at that tree, config = current live config + rpc_rps 4.0, rollback config pre-staged with its sha256, stops check, baseline 0 of 12, executor-only restart). Am.10 Erratum 1 (#1389): the simulator lands an entry ~3.44 s after the decision, not 1.9 s.

**Hourly status.**
- Script: `/data/mal/hunt-1008/ops/manager23/hourly.sh`. Run it as a MiScusi job:

  ```
  B=$(base64 -w0 /data/mal/hunt-1008/ops/manager23/hourly.sh); miscusi-agent job submit --name hourly --machine mal-fast-0 --role ops --mem 512M --cpus 0.2 -- "echo $B | base64 -d > /tmp/h.\$\$.sh; sh /tmp/h.\$\$.sh"
  ```

  then `miscusi-agent job log <seq>`. The bridge-event filter starts at `ts_ms` 1794419100000 (11-11T17:45Z); raise it as the log grows.
- It prints the lane report, both unit PIDs, host checkpoint fields (gaps, hwm, log_seq, gap_refusals), the follower's lag, jumps and retries, disk, and the day's bridge events. The state-file hourly line also carries: HELM-REPLY check, inbox, the owner question, open-sim books (last_hour), board / recon / extract times, copier pass count and copy-dir mode counts (4 d 700 / 2 f 600 / ACL 0; never sizes or mtimes), A3 loop #2708 + dead-man #1896 + monitor #2710 Running, proofs.
- **Healthy means:** executor 3018006 active; host active (PID 3750092, since 15:01:34Z) with hwm within the hour; follower PID 2625920, NRestarts 0, backlog_jumps 0, lag under ~50; **disk free at or above 27%**; A3 root copy last line = source and the newest `run_utc` advancing (next record ~19:10Z).

**Helm protocol.** Requests are files `HELM-ASK-<MMDD>-<n>.md` in `/data/mal/hunt-1008/push-1016/hunt29/p1/`; replies are `HELM-REPLY-*` in the same folder. Helm acts on the owner's word given directly in Helm chat. Verify every reply with a read-only job before recording it. Done on 11-13/14: HELM-ASK-1113-7 (executor, part B; REPLY-1113-7), HELM-ASK-1114-1 (FYI on the A3 redeploy), HELM-ASK-1114-2 (host, part A; REPLY-1114-2). Open: **HELM-ASK-1108-1** (reset window 11-16T06:00Z to 11-21T00Z, §5); the Am.10 install ask is written only after the owner says yes.

## 4. Second strategy: r4bt-a2 (paper) and the r4-b open preview
- **DEC-031 Amendment 30 merged f090d5a8** (#1325): paper successor of r4-b-T on the fixed tip feed. **r4bt-a2 is NOT yet registered**: registration only follows a P0 PASS on 2026-11-16, 11-17 and 11-18 (Am.30 items 7, 9). The count job is **#2593 at 2026-11-19T00:10Z**; the clock is 2026-11-20T00:00Z; screen read by 2026-11-27T01:01Z; confirmation about 12-04. W′ = **3c12c5fd**.
- **Dry host STARTED 2026-11-13T10:05:01Z (job #2591; paper).** Host pid 2752773, install_head 3c12c5fd. Jobs: #2590 sidecar, #2592 stop check (its own end ~2026-11-19T00:05Z, 134 h from 10:05Z, earlier than QP's 20:30Z stop: check it), #2593 P0 count, #2594 day-sha loop. **Smoke judgement written 2026-11-14T11:00:31Z (notebook n_EP0sbIiyaLtBxA): (a) PASS, (b) PASS, (c) PASS, (d) PASS**, disclosed: the start entry was written after the start, and the redeploy split (smoke 11:00 to 20:12:28Z on the old binary, then the new; catch-up all pre; P0 days post). Not to be revised. The A3/pump redeploy classification (C14) follows the research-0 monitor's 11-15T07:11Z run.
- **Rule:** never read an entry count before the J4 count job. A stop after a seen count counts as a P0 miss.
- **r4-b open preview (DEC-031 Am.31 and Errata 1 to 3; QP-RULING-1113-open-sims, -ADD1, QP-RULING-1114-r4b-preview-steps and ADD1).** Preview priced from r4bt-a2's own declogs at 0.10/0.25/0.5/1.0 SOL, entries from 2026-11-16T01:00:00Z, first run **2026-11-17T03:20Z**, on research-0 (price keeper **#2636**, limit 333 h, ends ~11-27T20Z; declog copier **#2642** on fast-0; pricer `tools/r4b_preview.py` blob 8cbe33d3 at `/data2/mal/wt/r4b-preview-8d19d971`). Tools merged: #1367, #1370, #1372, #1387 (keeper pre-check, chain with `&&` before `r4bt_price_bot`), #1388 (d measure). In-sample ladder job #2625 (IN-SAMPLE; fresh-0903 negative at every stake; see LAB_STATE). **Owed before the first preview run:** second P0 note before 2026-11-16T00:00Z (cite n_YQz7bJG2Ysxolg), d measurement from 2026-11-16T00:05Z (window [11-15T00Z, 11-16T00Z); confirm the staged sampler segment is written through 11-16T00:05Z, the successor's SAMPLERS list holds it and exactly one staging process runs; pass every staged file that can hold window samples), manifest v1 + declaration by ~2026-11-16T18Z, sealed-dir type/mode counts, clean-tree check. **Owed before registration (record by 2026-11-19T23:59:59Z):** both copier commits (50469181 for #2638/#2640, 04e9716b from #2642), the 11-13T23:18Z permissions-incident disclosure line, the hv7 / lyra-ro line with Helm's answer, the keeper pre-check commit and blob, readers record, and a repeat of the "(k)" no-kill-review-week check against LAB_STATE Now and this table.
- **Memory:** the dry host's scope is 5120M. Check `systemctl show user-1002.slice -p MemoryCurrent -p MemoryMax -p MemoryHigh` with FLOOR running. No replay job during a catch-up.
- Tip-health successors for **#2055 and #2064** are needed before 2026-11-16T00:00Z (P0 days). The tip writer/push successors #2503/#2504 end about 11-29T06Z, inside the confirmation window.
- **Research-0 structure monitor:** pin commit P2 `9a7cd01c050190d84e17c0dc727615d0653b03d6` (tag `structure-monitor-pin-1114`); #2710 running (ends 11-20), #2711 after it. First-run check owed 2026-11-15 ~07:2xZ.

## 5. Fixed steps (UTC)
Re-create the crons from `crons-manager23.json` with CronCreate.

| UTC | Step |
|---|---|
| hourly | HELM-REPLY-*, PR verdicts, miscusi_inbox (manager23 is the registered inbox reader, worker d_11fM1Ytqx_o0Mg), the FLOOR hourly job (§3), wallet, disk (27%); one state line (the 11-14 lines are stamped :31Z) |
| 11-13, 11-14 (done) | Am.8 part B installed 11-13T17:40Z; EXP-029 read 11-14T01:00:18Z (FAIL procedural); A3 re-pin and resume run 11-14T09:17:44Z; lock-out ended ~14:47:40Z; Am.8 part A installed 11-14T15:01Z; r4bt-a2 smoke judgement 11-14T11:00:31Z. |
| **now** | **Kill-line check** (`docs/runbooks/floor-killcheck.md`) after the first live entry following the 15:01Z restart; no host restart before it passes. **First-candidate A3 evidence** at the first candidate after ~14:47:40Z (§1). |
| **next A3 record ~19:10Z** | A3 record run (~07:10Z/19:10Z daily); fences :04-:25 of 07/13/19Z and 12:10-12:25Z; dead-man 13:10Z. Check the record is clean (`a3chk.sh`, `a3detail.sh`; never edit the record). |
| daily 00:53 (nightly) | Open-sim nightly board / forward A/B job is retired once books C and D0 have run 2 days (design §2). Daily twin re-check: `osb_twin_check` C3/C4/C5 on the live A-gh pair (§3). |
| daily | floor-forward-ab on research-0 (`scripts/research/floor_forward_ab.sh <last closed hour>`; check the log for a path-guard stop; link step needs the tapecopy dir current: it failed once on 11-14 and was refreshed). |
| **11-15 05:00 to 09:00** | **Tip hand-off window** (`docs/runbooks/tip-writer-handoff.md` §5/§6). Writer #2033 hits its limit ~06:09Z, push #2039 ~06:24Z; gates #2501 (deadline 08:08Z) and #2502 (08:23Z) release #2503/#2504. Check at ~06:41Z: gates exited 0; successors running, resumed and caught_up, no REFUSED; counts-only FLOOR host gap check. Gate exit 4 or 5: follow the runbook by hand. |
| **11-15 ~07:2x** | **Research-0 monitor first-run check:** `/data/mal/structure-monitor/daily.jsonl` newest line (the 07:11Z run of #2710): `pins_changed` and `program_changed` clear. Then classify C14 for r4bt-a2. |
| **by 11-15 09:17** | **T1 A3 resume record PR** (DEC-035 section 4 resume record; DEC-031 guard-pin line naming P / #2708 / P2 / #2710; LAB_STATE lines; first-candidate evidence if available); owner notice already sent (n_e1H57fMKRD7y1g). Update `A3-SUCCESSOR-COMMANDS.md`. |
| **11-15 12:00** | Owner question **q_9XkUow_sEaMEvw** (Am.10 install) decide-by. |
| 11-15 12:05 | Latest r4bt-a2 dry host start (§4) |
| **by 11-15 18:00** | **Start successors for #2055 and #2064** (they end ~11-15T23:30Z to 11-16T00:15Z). Cron **326cd178** fires 2026-11-15T17:13Z; before the hand-off confirm which sampler segment is written through 11-16T00:05Z (current staged segment tip-health-1107), the successor's SAMPLERS list holds it, exactly one staging process after. |
| **before 11-16 00:00** | **Sampler/staging successors in place; second P0 note** disclosing the r4-b preview (cite n_YQz7bJG2Ysxolg; confirm #2593 queued). |
| **11-16 00:05** | **d measurement** (`tools/r4bt_d_measure.py`, one measurement, clean checkout, window [11-15T00Z, 11-16T00Z); fallback [11-16T00Z, 11-17T00Z) only on a coverage refusal). Needs a staging pass finished after 00:05Z. |
| 11-16 01:00 | r4-b preview entries start (Am.31); no stop, restart or slip of a2 on preview results. |
| 11-16 03:23 | vault1.0 paper check (cron). The forward A/B has 7 complete forward days only ~11-20, not 11-16: no vault decision before that. |
| **11-16 ~18:00** | **Manifest v1 + declaration** for the preview (notebook entry before the first run). |
| **11-16 to 11-21** | **HELM-ASK-1108-1 reset window** (opens 11-16T06:00Z, closes 11-21T00Z; nudge Helm at 06:13Z; use the **current** pin and config `live-9c52f4fc…json`, only the one state field changes, ADDENDUM-1). **A possible Am.10 install rides with it** (if the owner said yes). Bundle any Am.6 install. |
| 11-16, 11-17, 11-18 | r4bt-a2 P0 days |
| **11-17 03:20** | **First r4-b preview run** (job on research-0 from the clean tree at 8d19d971; keeper pre-check chained first). |
| **before ~11-18 21:00** | **Dead-man #1896 successor** (it ends ~11-18T21:17Z; the executor checks the A3 gate in `/var/lib/mal-hv4-c0/a3/daily.jsonl`). Command: `A3-SUCCESSOR-COMMANDS.md` (#1896 ref 136cd98d, 512 MB, 0.1 CPU, 18,720 min). Confirm `run_utc` advances. |
| **11-19 00:05 to 00:10** | Dry host ends ~00:05Z (check job #2592); **P0 count #2593 at 00:10Z**. The (k) check (no kill-review week in LAB_STATE Now or here) repeats before the record. |
| **11-19** | **Re-create the hourly cron** (it expires after 7 days, ~11-19). **Registration record by 2026-11-19T23:59:59Z** (§4). |
| 11-20 | #2710 monitor reaches its end; #2711 follows. r4bt-a2 clock 11-20T00:00Z if registered. Forward A/B reaches 7 complete forward days (~11-20). |
| 11-21 00:05 | #2353 reset check (exit 3 = not done; chase Helm) |
| 11-21 18:13 | Successors to END 2026-11-30T07Z for #2290 daily report and #2291 health watch (both end ~11-22T13Z), **#2266 FLOOR tip link-dir** (~11-22T01:43Z), #2062 follower health (~11-21T01Z). Check #2134's consumers (~11-21T02:31Z). |
| **~11-22 14:30** | **Executor canary end** (FLOOR executor probe-limit clock; job #2348 alert of 11-14T12:00Z was a false alarm: stale blob allow-list, the running blob fd7e36c1 carries the fix lines; any such check must test for the fix lines, not a blob list). |
| **before ~11-27 09:17** | **A3 loop #2708 successor** at pin P `c687753b` (limit 18,720 min, ends ~11-27T09:17Z). |
| **11-27 18:00** | **Price keeper #2636 successor** (ends ~11-27T20Z). |
| weekly | Clean stale job trees on fast-0 (disk). |

MiScusi refuses job limits above 14 days. `after` fires only on exit 0, never on a time-limit stop.

## 6. Research lines and findings (all paper, in-sample; reports under `/data/mal/hunt-1008/ops/reports/`)
- **Library sweep** (`2026-11-12/stack-candidates-library.md`): no library idea reaches 0.108 SOL/day; FLOOR clones on other venues are clean negatives; r4-b is the only large candidate, with weak evidence. That led to r4bt-a2.
- **Throughput levers** (`2026-11-12/floor-throughput-levers.md`): FLOOR alone is about 0.08 SOL/day at 0.25 in-sample; the gap guard is the biggest live leak.
- **Gap-range result** (`2026-11-12/floor-gap-range.md`, job #2527, merged 3b0a5c38): see the Erratum 3 figures in §3.
- **Burst study** (`2026-11-13/floor-burst-study.md`): 11-09 had 28 entries in one wave; at 0.25 flat that day mean −7.40%, median −2.88%, sum −0.5179 SOL. Caps are not monotone (K=3 +0.211, K=5 +0.186, K=8 +0.615, K=12 +0.502, K=16 +0.412, uncapped +0.001). No limit is established.
- **Landing-vault study** (`2026-11-13/floor-landing-vault-study.md`): bimodal; 0.25 flat uncapped ≤0.5 n 38 +0.091848 (ex-top-3 −0.258573) against >0.5 n 41 −0.090654 (ex-top-3 −0.579139). Not separable. The forward A/B now has `land_vault_sol` and `entry_over_mark0` bins (#1356).
- **Rule variants** (`2026-11-12/floor-variants.md`, 0.10 flat, n 50): age12 REJECT; vault0.75 REJECT; vault1.0 Δ +0.0625, WEAK, not adopted. Adopting needs a new frozen rule `l1-floor-v3`, a second host and executor under a DEC-035 amendment, and QP. The v3 fast route plan: `2026-11-13/floor-v3-fast-route.md` (route R2; forward A/B reaches 7 complete days ~11-20; the reset window 11-16..11-21 is where a hand-over to floor-lane-2 would happen).
- **Open-sim board v0 and books (§3):** board v0 numbers left out the 29 gap-excluded positions (correction in §3). Strict books carry only in-sample closed trades; the first forward entries (11-13 00:32Z, 03:39Z, 06:36Z) are small n.
- **r4-b in-sample ladder** (job #2625, QP stack, pressure leg 1.3 s): pooled training blocks positive at 0.10/0.25/0.5; fresh-0903 negative at every stake (0.10 -2.2414%, 0.25 -0.9173%, 0.5 -0.5211%, 1.0 -0.4250%). Size fixes fees on fitted blocks, not the sign on fresh-0903; the forward preview decides.
- **Other lines are off:** EXP-012 shadow, Haiku forge, slowbot, the HV books, h5 and s1t.

## 7. Owed (not done)
- **Kill-line check** after the first live entry (§1); **first-candidate A3 evidence** (§1); **T1 A3 resume record** by 11-15T09:17Z (§5).
- **Am.10 owner answer** by 11-15T12:00Z; install only with Helm and the owner's word.
- **Open sims:** add A-gh to BOOKS.json after the scratch run catches up; latency twins after proofs #2723-#2725 and an L2r PASS; C-gh after #2726/#2727; follow-up minors (twin-md5 compare only at the same last hour; cached-vs-computed md5 refusal m5; hour-format check in the keeper pre-check; `st_nlink` in the d tool; torn-md5 repair with tmp+fsync+os.replace; SIGTERM handler); pre-existing test interaction `tools/test_floor_gap_range.py` after the osb set (needs `MAL_ARENA_TEST=1`).
- **Later T1 pins PR** for `disc_len` drift and DepositEvent (A3 review open items); r4bt-a2 registration disclosures (§4).
- **Durable disk plan for fast-0:** tip retention, `public-logs.jsonl` (835 MB and growing); disk guard successor beyond #2546's limit. Kernel reboot plan (6.8.0-149 pending; a reboot before about 2026-12-04 would end the r4bt-a2 paper host).
- **SEALS-BRIEF line (g)** dated note citing Am.8 Erratum 1 (QP minor m2 on #1354); delete `p1-1113` only after the part-A install record (now installed: write the record first).
- **`set -f` MINOR** around the symlink-component loop in `scripts/research/floor_gap_range.sh`.
- **Small MINORs from the state file:** #1330 follow-ups; #1328 nits; Erratum 3 report-wording fixes; the 41 of 79 entries landing above 0.5 SOL real vault need a separate look.
- **Records batch:** this PR covers events through the 15:31Z hourly of 11-14; later events go in the next daily batch.
- **Stale open PRs (#905–#1263):** parked or never-merge.

## 8. Running jobs (from the state file, 11-14T15:31Z; do not start anything from this list)
- **FLOOR:** watch **#2739** (host 3750092, executor 3018006, 72 h; earlier watches #2614/#2623 ended on PID changes as expected); **#2546** disk guard at 27%; **#2566** keeper `tapecopy-roll` (research-0); #2290 daily report, #2291 health watch (exited 3 on earlier alerts; successors in place), #2266 tip link-dir, #2348 (false alarm of 11-14T12:00Z) and #2353 checks.
- **A3 and monitors:** loop **#2708** (pin P c687753b, ends ~11-27T09:17Z), dead-man **#1896** (ends ~11-18T21:17Z); research-0 monitor **#2710** (then #2711); watchdog **#2586**; carry timer `mal-hv4-c0-a3-carry` (root).
- **Open sims (research-0 unless noted):** books A #2670, D1 #2672, C #2673, E1 #2674, E2 #2675, D0 #2677; board **#2701**; recon loop **#2700**; live extract loop **#2696** (fast-0); osb keeper **#2630**; scratch twin run **#2730** (A-gh); proofs **#2723** and **#2724** running, **#2725**, **#2726**, **#2727** queued. Done: #2662 (PASS), #2632 (VACUOUS), #2669 (PASS), #2729/#2731 twin checks.
- **r4-b:** price keeper **#2636**; declog copier **#2642** (fast-0; exactly one); r4bt-a2 dry jobs #2590, #2591, #2592 (stop check, queued), #2593 (P0 count, queued), #2594.
- **Tip:** #2033 writer and #2039 push (end ~11-15T06Z); gates #2501/#2502 with successors #2503/#2504 queued; #2055, #2062, #2064 health jobs.
- **Done, with results:** #2654 EXP-029 read (FAIL procedural); #2624 Am.8 B check; #2738/#2740 part A checks; #2713 A3 dry decision; #2715 Am.10 baseline; #2627/#2659 board runs; #2625 ladder.
- **fast-0 limits:** 8 CPU slots; `--mem` required, `--cpus 0.2` for status jobs; jobs run under `sh`. One heavy replay at a time, at most 2 workers; check `systemctl show user-1002.slice -p MemoryCurrent` first. Max 8 running jobs per session.
- No open PR of manager23's at the time of writing; this records PR is the only one.

## 9. Exact next steps (priority order; updated 2026-11-14T15:3xZ)
1. **First live entry after 15:01Z:** run the kill-line check (`docs/runbooks/floor-killcheck.md`); read the first-candidate A3 evidence (0 `a3_pause` rows plus a `buy_sent` or non-a3 `entry_refused`). No host restart until the check passes.
2. **Owner answer to q_9XkUow_sEaMEvw** (Am.10 install) by 2026-11-15T12:00Z; on yes write the install HELM-ASK (§3) and bundle it with the 11-16..11-21 reset or the 0.25 step.
3. **T1 A3 resume record PR** by 2026-11-15T09:17Z; update `A3-SUCCESSOR-COMMANDS.md`; research-0 monitor first-run check ~11-15T07:2xZ.
4. **Tip hand-off 11-15 05:00 to 09:00Z**, successors for #2055/#2064 and the sampler/staging check before 11-16T00:00Z; second P0 note; d measurement from 11-16T00:05Z; manifest v1 + declaration by ~11-16T18Z; first preview run 11-17T03:20Z.
5. **Open sims:** A-gh onto the board; daily C3/C4/C5 re-check; latency twins after #2723 to #2725 and an L2r PASS; C-gh after #2726/#2727; read the hourly recon (it is FAIL by the non-rule cap; S1 to S4 INSUFFICIENT). Show every trade to the owner; compare sim vs live fills.
6. **Am.6:** count 2 of 20; decide the stake step at trade 10 on a clean fills check and book A matching live (owner's word and a DEC-035 amendment needed).
7. **Dead-man #1896 successor** before ~11-18T21:00Z; A3 loop #2708 successor before ~11-27T09:17Z; keeper #2636 successor 11-27T18:00Z; re-create the hourly cron before ~11-19; registration record by 11-19T23:59:59Z.
8. **Watch #2739:** read its alerts; fast-0 `/` at or above 27% free; weekly clean of stale job trees; hourly job every hour.
Also: record every restart in LAB_STATE; forward A/B (no vault decision before 7 complete forward days ~11-20); tip retention and `public-logs.jsonl` (§7).

## 10. How to verify this handoff
- `git log --oneline -1` on main shows this PR or later (main was 6061edb7 before it).
- The last lines of `state-manager23.md` mention this handoff; the 2026-11-14T15:07Z and 15:31Z lines record the part A install and its verification.
- The hourly job shows the host active under PID 3750092 (unless restarted and recorded), executor 3018006 active, follower PID 2625920 (unless restarted and recorded), disk at or above 27% (45.10% at 15:31Z).
