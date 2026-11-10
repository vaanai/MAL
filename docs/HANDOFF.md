# MAL manager handoff: manager21 → manager22 (written 2026-11-10T02:0xZ, from `date -u` 02:00:46Z)

The owner asked for this handoff (chat, 2026-11-10 ~02:0xZ). It replaces the manager20 handoff.
Read this first. Then the tail of `/data/mal/hunt-1008/ops/manager21/state-manager21.md` (one line per event, newest last). Then the newest MiScusi notebook entries.

**No book has cleared the promotion gate.** One book trades real money: **FLOOR floor-lane-1** (DEC-035 live-learning lane, mal-fast-0). Realized so far +0.016928927 SOL over 4 closed trades (all bought before the gate read's start S), 5 positions still open at a cost of 0.158369200 SOL. Their market value is not read, so the true total is unknown; the wallet is ~0.14 SOL below its start until those 5 sell.

## 0. Owner direction (current)
- **11-08 (memory `owner-golive-1108`):** skip forward paper, go live. **FLOOR stake ramp ladder:** 0.03 → 0.10 after 10 closed trades with fills matching the model → 0.25 after 30 with net > 0; each step needs a DEC-035 amendment. The loss cap is never above 0.6 SOL without the owner. All paper reads were retired that night.
- **Standing:** lane cap raises are the manager's call (memory `owner-floor-cap-1108`). Act without asking and go live when the evidence supports it (`owner-go-live-authority-1106`). Never sit idle: always run parallel work toward revenue (`feedback-never-idle-1108`).
- **11-09 ~16:1xZ (memory `owner-1109-shadow-iterate`; paraphrase, not verbatim):** FLOOR is going fine as long as the fixes are in and it keeps making good trades. Stay proactive. Study the EXP-012 fast-0 shadow's positive vs negative periods and iterate. Haiku/API credits are the manager's call; the owner suggested a Haiku bot modelled on the shadow's successes.
- **Helm acts on the owner's word given directly in Helm chat**, not on relayed approval. Funding goes only through the owner and Helm. No Claude session reads a wallet key.
- Hard rules, seals and the promotion gate are unchanged (CLAUDE.md).

## 1. FLOOR floor-lane-1 (the one live book)
**Setup.**
- **Live config:** `/etc/mal-floor-lane/live-f68cf56fcce8b1127e7159d8069506f0988eb95d.json`, sha256 24bcdeb9… It is byte-identical to the older live-3710e1af… and live-e8790f93… files.
- **Limits:** stake 0.03 SOL; max_open 8; lifetime budget 0.30 SOL; daily stop 0.10; fee reserve 3,860,000; executor end 1796688000000 (2026-12-08T00:00:00Z).
- **Executor:** `mal-floor-lane.service`, PID **101816**, pin f68cf56f (blob tools/floor_lane_executor.py 556d1562), started 2026-11-09T14:26:01.071Z (HELM-REPLY-1109-3).
- **Host:** `mal-floor-lane-host.service`, PID **3484327**, since 11-08T08:32:01Z, uncapped paper book l1-floor-v2, exits tp_1.5x or time_24h. **Never restart the host** (a restart blocks entries 24 h). Executor-only restarts go through Helm (HELM-ASK files).
- **Wallet:** 5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk (public key only). A buy costs 31,673,840 lamports; the sell closes the ATA, so rent comes back.

**Fixes this manager shipped.**
- **#1297/#1298 (pin e8790f93):** lifted the inner probe's 3-open / 60-attempt / 7-day clamps.
- **#1303 + DEC-035 Am.3 + EXP-029 Erratum 1e (#1304), pin f68cf56f:** the pre-send budget check double-counted open-position loss and refused 16 entries between 10:46 and 12:33Z on 11-09. It is now the full-wipe bound: `wipe_floor − (stake + reserve) ≤ −budget`, where wipe_floor = cash − Σ stake of unfilled intents. Buys resumed after install.
- **#1308:** forge DSL tests updated to match.

**Still binding: the wallet floor.** `floor_arena_executor.floor_problem` refuses when `bal < held + stake + fees + (open+1)×sell reserve + 750,000,000`; open stakes are counted twice on purpose. At a ~0.96 SOL balance this allows about **5 open**, and each 0.03 SOL of realized loss costs one more slot. It refused buys at 15:35:25Z and 16:38:43Z on 11-09. `committed_loss_stop` is checked first.

**OPEN OWNER QUESTION `q_Ehou-Xil3LPdQw`** (Helm top-up ~0.25 SOL; deadline **2026-11-11T16:32:39Z**).
- Manager recommendation given: yes. The 0.30 lifetime budget does not change; the top-up adds slots only.
- **On yes**, per EXP-029 Erratum 1f item 2 (#1307): write a HELM-ASK for exactly 250,000,000 lamports; the transfer fee is extra. Make no config change: `funded_lamports` stays 1,000,000,000, and there is no restart. Never ask for a different amount or a second top-up in this read. Helm acts only on the owner's own word in Helm chat (§0), so ask the owner to tell Helm directly too. Record the instant (the transfer's block time), the amount, and the wallet_floor refusal counts before and after. Close the question with `miscusi_ask_close`.
- **On no, or no answer by the deadline:** no top-up in this read. Close the question.

**Gate read EXP-029** (Errata 1, 1a–1f merged).
- S = 2026-11-09T06:00:00Z; deadline 2026-11-30T06:00Z; needs ≥100 trades over ≥5 UTC days.
- **0 of 100 so far:** all 4 exits were entries made before S.
- wallet_floor and committed_loss refusals are pauses, not FAILs (Erratum 1f).
- The read report must quote the Erratum 1e MAJOR 1 disclosure and the QP ruling.

**14-day canary reset (HELM-ASK-1108-1).** The owner told Helm yes on 11-09; Helm acknowledged ("it's on it").
- **Window:** 2026-11-16T06:00Z to 11-21T00:00Z.
- **Hard limit:** before 2026-11-22T14:30:48.897Z, or EXP-029 is FAIL (Erratum 1b).
- Check job **#2353** runs at 11-21T00:05Z; exit 3 means not done, so chase Helm that hour.
- The ask text named the old pin and config (3710e1af). **HELM-ASK-1108-1-ADDENDUM-1** (written 2026-11-10T02:01Z) tells Helm to keep the current pin f68cf56f and config live-f68cf56f… and to change only the one state field. Point Helm at it when you nudge.

**Hourly status (read-only MiScusi job on mal-fast-0, cpus 0.2).**
- It runs `tools.lane_daily_report --config <live config> --day <UTC day>`, plus `systemctl show` for both units, plus the stat of `/var/lib/mal-live/floor-lane/decisions-root/lane/checkpoint.json` (mtime = host hwm; it should be within a minute).
- It also reads the bridge events since the last check from `/var/lib/mal-live/floor-lane/state/bridge-log-live.jsonl` (via `sudo -n tail`; counts and order ids only).
- The last copy of the command is in job #2370 (`miscusi-agent job status 2370`). Pass the script straight to `miscusi-agent job submit … --`, without an `sh -c` wrapper.

**Quiet spells are normal.** No entry signal arrived 17:48Z–02:00Z with the feed live (gaps 0). Entry signals come in bursts.

## 2. Fixed steps (session crons: `ops/manager21/crons-manager21.json`; re-create with CronCreate at session start)
| UTC | Step |
|---|---|
| hourly :17 | HELM-REPLY-* in /data/mal/hunt-1008/push-1016/hunt29/p1/; PR verdicts; miscusi_inbox; FLOOR hourly status (§1); one state line |
| daily 00:20 | #2290 writes `~/mal-data/floor-lane-daily/<day>.{txt,json}` on fast-0 → notebook (+ console.json when there is news) |
| every 300 s | #2291 health watch (exit 3 = alert) |
| **11-11 16:32:39** | Deadline of the owner top-up question (§1) |
| **11-11 22:00–22:08** | research-0 arena feed #1652: this is the runbook's fallback hand-off window (`docs/runbooks/feed-handover-1111.md`); the job's own limit ends ~11-12T00:30Z. Its consumers (Haiku v4, forge) are retired or stopped. **First check whether anything still reads `/data/mal/arena/tape`** (memory `feedback-archive-list-check`). If nothing does, let it end and record that. FLOOR does not use it. The cron "11-10 20:13" is this step. |
| **by ~11-15T06Z** | **Tip writer #2033 (~06:08Z) + push #2039 (~06:23Z) reach their 14-day limits.** FLOOR's host reads the tip tape (via link-dir #2266), so a writer stop starves FLOOR. Follow `docs/runbooks/tip-writer-handoff.md` (resumable writer; `after` does not fire on a time-limit stop). Expect one writer-restart hour: confirm with a counts-only check that the FLOOR host sees no gap or refusal storm afterwards. The cron is "11-12 10:13". |
| **job limits (computed at 02:05Z as limit − ran)** | Before each end, start a successor or record "let end (reason)":<ul><li>#1814 meta-capture ~11-11T15:20Z: its consumers (HV books) are retired; let it end unless something still reads it.</li><li>#2169 r4-b mirror ~11-14T21:23Z: r4-b is not active; probably let it end.</li><li>**#1952 watchdog ~11-14T04:17Z: successor.**</li><li>**#2276 fast-0 disk guard ~11-15T08:35Z: successor.**</li><li>**#2055 pool_cache ~11-15T23:30Z, #2064 hour-health staging ~11-16T00:15Z, #2062 follower health ~11-21T01:01Z:** tip health. The job names say "END 11-25", but the 336 h limit stops them first. Successor if anything FLOOR-side uses them.</li><li>**#1896 A3 deadman ~11-18T21:17Z, #1898 A3 guard ~11-21T01:01Z:** the FLOOR executor checks the A3 gate (`/var/lib/mal-hv4-c0/a3/daily.jsonl`; Helm's replies show "A3 gate clear"). Confirm what a stale A3 record does to FLOOR, then start successors in time.</li><li>#2134 venue tape ~11-21T02:31Z: check its consumers.</li></ul>MiScusi refuses totals above 14 days, and `after` does not fire on a time-limit stop. |
| 11-14 12:00 | #2348 pin check (accepts blob 6f353716 or 556d1562) |
| 11-16 06:13 | Nudge cron: HELM-ASK-1108-1 window open (check only; the owner already said yes) |
| 11-21 00:05 | #2353 reset check (exit 3 = not done → chase Helm) |
| **11-21 18:13** | Successors to END 2026-11-30T07Z for: #2290 daily report and #2291 health watch (end 11-22), **and #2266 FLOOR tip link-dir (limit ≈ 11-22T01:43Z)**. (#2290/#2291 end ~11-22T13:3xZ.) |
| 11-30 06:00 | EXP-029 deadline: write the read per the prereg + errata (QP first) |

## 3. Other lines (all off unless said)
- **EXP-012 fast-0 shadow (`mal-fast-forward-paper`, book exp012_migrate_tp50_sl30):** studied 11-09 (`ops/reports/2026-11-09/exp012-regime-{plan,study}.md`, QP `exp012-shadow-qp.md`).
  - 2,334 trades over 35 days: 6 positive days.
  - **Holdout:** ungated −3.4% per trade, and the best gates were worse (−4.8%, −7.2%).
  - No autocorrelation or hour structure. Live cost is ~5.8% per trade.
  - **Verdict:** no regime to catch. The "positive since Thursday" stretch (11-08 +0.156) was followed by 11-09 −0.176 (partial day). Do not model a Haiku bot on it.
- **Haiku strategy forge:** STOPPED 11-09 16:11Z (#2279 cancelled). best_fit was flat at 0.190 for ~2 days; 232,616 specs; shortlist 0; all VAL reads failed (ex-top-3 negative or CI90 lower bound < 0); $99.68 of $160 spent. About $60 is left; spend it only on an idea with a testable edge. The $200/month API cap resets after 11-11 (memory `owner-approvals-1028`). Code: `tools/forge/`, spec `ops/manager21/FORGE-SPEC.md`. The DSL live path is merged (#1277/#1282) but no DSL bot was ever started.
- **slowbot-h-1:** stopped 11-08 00:11Z with 0 sends. HV4/HV6/HV7, h5v2, h5 lane3, BOOST watch and EXP-027 were all retired 11-08 ~00:34Z (owner decision; notebook n_j0z3_wYlJOMCaw). r4-b and s1t are not active.

## 4. Owed (not yet done)
- **Records PR** (records-only, T0 unless it touches EXP/DEC text):
  - cite #2344 for the 06:06:50Z send;
  - seq 13–30 = 16 entries + 2 exits;
  - "wallet_floor/committed_loss refusals never start a (b) window";
  - "60 attempts" wording; the #1300 date; the latch citation;
  - the HANDOFF §2 header was stale (fixed here).
- **Proactive next items (manager21's plan):**
  - (1) A read-only valuation of FLOOR's open positions (fresh marks), so the owner sees true P&L and not just the cost-capped view.
  - (2) FLOOR real fills vs the host paper book: latency and slippage cost. First confirm that no EXP-029 seal blocks it.
  - (3) A test that exercises the DSL ledger's cost-capped second pass (the #1308 reviewer note).
- Stale open PRs (#952–#1263): mostly parked or never-merge; triage when there is time.

## 5. Running jobs (`miscusi-agent job list`)
- **FLOOR:** #2290 daily report, #2291 health watch, #2266 tip link-dir, #2348 / #2353 checks.
- **Tip / feeds:** #2033 writer, #2039 push, #2062, #2064, #2055, #2169 r4-b mirror, #2134 venue tape, #1652 research-0 arena feed, #1814 meta-capture.
- **Guards:** #1952 watchdog, #2276 disk guard, #1898 + #1896 A3 guards, #2214 (→ #2217) structure monitor.

**Never `miscusi_job_status`** (carried word for word from the manager20 handoff): #1561 #1562 #1419 #1421 #1543 #1540 #1446 #1036 #1290, the HV4 E5/E6 loops (#1567 #1568 #1569, #1559 #1560), #2018 #2019, and every s1t / h5v2-bx10-f1 shadow, smoke, judge, A3 or score job (incl. **#2219, #2251**). Use job_list. Treat HV4 L8 jobs (#1519 #1520) cautiously. Many of these were cancelled 11-08, but the rule still holds. For the A3 guards #1896/#1898, read limits from job_list, not job status. (The #1309 review agent ran job status on #1896/#1898 filtered to the name, limit and ran lines only, with no log lines shown; this is disclosed in its report `ops/reports/2026-11-10/pr1309-handoff-t0-review.md`.)

## 6. Rules you will need at once
- Paste `/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md` (sha256 now dce6104b…, after the EXP-012 runner reader line of 11-09T04:41Z; AGENT-BRIEF-COMMON still cites 9995ec7d) verbatim into every tape-touching brief. Never open /var/lib/mal/sealed/*, tape contents, or shadow/smoke dirs. Readers of /var/lib/mal-live/** are manager jobs only, counts and order ids only.
- **Merges:** `QP VERDICT <head> OK` + `REVIEWER VERDICT <head> APPROVE` at the exact head, then `gh pr merge N --squash --match-head-commit <sha>`. T1 needs separate agents; T0 can use one combined agent. Workers never merge.
- **MiScusi jobs:**
  - fast-0 has 8 CPU slots and they fill up; use cpus 0.2 for status jobs and 0.1 for sleepers.
  - The CLI has no `--after`, and MCP `job_submit` misparses raw blobs, so use the CLI.
  - Jobs run under `sh`.
- Say "credential material" (a hook blocks the other word); use `--body-file`.
- Any DEC-035 bot code change means a new bot_id, unless it is an executor-only fix under an amendment (as with 1c/1e).

## 7. How to verify this handoff
- `git log --oneline -3` on main shows 1bf0d735 (#1308) or later.
- The FLOOR hourly job shows exec 101816 and host 3484327 active, open 5 (or later events in the bridge log), and checkpoint mtime current.
- `state-manager21.md` last line mentions this handoff.
