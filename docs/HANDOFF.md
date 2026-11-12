# MAL manager handoff: manager22 → next manager (written 2026-11-12T20:5xZ–21:1xZ, from `date -u`)

**Update 2026-11-12T22:5xZ (manager23):**
- manager23 took over 21:25Z; state file `/data/mal/hunt-1008/ops/manager23/state-manager23.md`; crons `/data/mal/hunt-1008/ops/manager23/crons-manager23.json`.
- New fixed steps: 2026-11-13 10:37Z r4bt-a2 dry host install (`docs/runbooks/r4bt-a2-dry-host.md`); daily 00:23Z floor-forward-ab job; FLOOR host stage 1 install not before 2026-11-14T01:00Z (QP ruling condition 1).
- The section 4 owed-list items done today: records batch #1323, variants scripts #1324, DEC-035 nits #1328.

The owner asked for this handoff in chat on 2026-11-12 at about 20:5xZ, because they want to try a different model on MAL work. It replaces the manager21 → manager22 handoff (#1309).

**Read in this order:**
1. `CLAUDE.md`
2. This file, in full.
3. The tail of `/data/mal/hunt-1008/ops/manager22/state-manager22.md` (one line per event, newest last).
4. The newest MiScusi notebook entries.

**Then, before anything else:** re-create the session crons (§2) and run one hourly status job (§1).

**No book has cleared the promotion gate.** One bot trades real money: **FLOOR floor-lane-1**, the DEC-035 live-learning lane on mal-fast-0.

## 0. Do not break these (read first)
1. **Never restart `mal-floor-lane-host.service`** (PID 3484327, up since 11-08T08:32Z). A host restart is a data-loss span, and the host then refuses every pool for about 24 h. Only the **executor** (`mal-floor-lane.service`) is ever restarted, and only through Helm (HELM-ASK files, §1).
2. **The wallet key is root-only and Helm's.** No Claude session reads, prints or copies it. Only the manager starts a lane book. Workers never start, fund, configure or restart a lane executor.
3. **Never `miscusi_job_status`** on any of these: #1561 #1562 #1419 #1421 #1543 #1540 #1446 #1036 #1290, #1567 #1568 #1569, #1559 #1560, #2018 #2019, #2219 #2251, or any s1t / h5v2 / A3 / score / shadow / smoke / judge job. For their limits use `miscusi-agent job list`. This includes the A3 guards **#1896 and #1898**.
4. **Never open** `/var/lib/mal/sealed/*` or any `tape-*` file.
   - Readers of `/var/lib/mal-live/**` are manager jobs only, and they print counts and order ids only.
   - Paste `/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md` verbatim into every brief that touches tape.
5. **Merges.**
   - Every PR needs `QP VERDICT <head> OK` and `REVIEWER VERDICT <head> APPROVE`, both on the PR at the exact head. T1 PRs need separate agents; T0 PRs can use one combined agent.
   - Merge with `gh pr merge N --squash --match-head-commit <sha>`.
   - Workers never merge. Check that `baseRefName` is `main`.
6. **Hook word.** A hook blocks the word s-e-c-r-e-t-s in Bash text. Write "credential material" instead, and pass PR bodies with `--body-file`.
7. **Do not touch** the firewall, sshd, cloudflared, Cloudflare Access or `/opt/miscusi`. The Helius key and the DB password stay where they are.

## 1. FLOOR floor-lane-1 (the one live bot)
**What it does.**
- The host runs the frozen rule `l1-floor-v2` (`tools/arena_rule_l1.py`, frozen sha 5e12f8f5) on the fast-0 tip tape.
- When the host triggers, the executor buys.
- Exits are take-profit at 1.5x or a 24 h time stop.

**Live setup (DEC-035 Amendment 5, merged 320cccc5; installed by HELM-REPLY-1112-1).**
- **Executor:** `mal-floor-lane.service`, PID **1929135**, pin `320cccc5…`, tree `/usr/local/lib/mal-floor-lane-executor/320cccc5…`.
- **Config:** `/etc/mal-floor-lane/live-320cccc5f2ec81d40d18f8991f11b15d29b52f74.json` (sha256 aa6c355f…).
- **Limits:**
  - stake **0.10 SOL**;
  - max_open 16;
  - lifetime loss budget **1.20 SOL**;
  - daily stop **0.20 SOL**;
  - fee reserve 3,860,000 lamports.
- **Rollback to the 0.6 budget config:** `rm /etc/systemd/system/mal-floor-lane.service.d/zzz-pin-320c.conf && systemctl daemon-reload && systemctl restart mal-floor-lane.service`. This is an executor-only restart; it goes through Helm unless it is an emergency.
- **Wallet:** `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk` (public key only). It held **3,058,829,273 lamports** at 11-12T20:45Z. Check it with a public RPC `getBalance` via curl to api.mainnet-beta.solana.com.
  - The owner's top-up of +2.041 SOL landed 11-12 06:36–06:37Z.
  - Each buy costs stake + 1,513,840 lamports of token-account rent (refunded at the sell) + 160,000 lamports of send fees.

**Where it stands (11-12T20:45Z).**
- **Lifetime realized:** +0.015081595 SOL, from the 10 closed trades at 0.03 SOL.
- **At 0.10 SOL:** 0 closed, 1 open.
  - The open trade is order `e3b7cc9de20e65bd`: bought 14:06:15Z, basis 0.101673840.
  - It exits by about **11-13T14:06Z**.
  - The lane report's cost-capped P&L is −0.086592245, because it counts the open basis. Budget headroom is 1.113 of 1.20 SOL.
- **Entry blackout until about 11-12T22:04Z.**
  - A 763 ms tip-status gap at 16:04:39Z means the host refuses any Buy whose 6 h lookback overlaps the gap, and blocks that pool for 24 h. That is `gap_refusals`, at 12 now.
  - The cause was the tip follower's status file going stale. It is fixed by #1320/#1321 (deployed 17:58:35Z, follower PID **2254354**).
- **Entry signals come in bursts, so quiet hours are normal.** On 11-09 the host triggered 28 times. Only the lane budget limited buys then, and that budget has since been raised.

**Next step up: DEC-035 Amendment 6 (merged 8dec3a3b; NOT installed).**
- Am.6 raises the stake to **0.25**, the budget to **2.10** and the daily stop to **0.50**. The owner accepted this and the loss budget in chat on 11-12 ~06:4xZ (memory `owner-scale-1112`; the quote is in Am.6).
- **The gate, decided once:**
  - Take the **first 20 closed 0.10 trades** since 2026-11-12T00:46:04.584Z, in lane-ledger exit order.
  - Their realized P&L must be **≥ 0 after all fees**. This counts every lane-ledger row of those 20 positions, plus failed-buy fee rows up to and including the 20th exit instant.
  - **The fills check must print `FILLS_MATCH_MODEL yes` at trade 10 and at trade 20.** Run it as a manager job on fast-0 (sudo; it reads `/var/lib/mal-live`):

    `sudo -n $HOME/mal-venvs/r4b/bin/python -m tools.floor_fills_vs_model --config /etc/mal-floor-lane/live-320cccc5f2ec81d40d18f8991f11b15d29b52f74.json --since 2026-11-12T00:46:04.584Z`

    The tool has no `--ref` argument. Without `--since` it would also count the ten 0.03 trades.
  - **The other install preconditions, (a) to (f) of Am.6, must also hold.** Among them:
    - (d) the listed unit tests pass at the merge sha;
    - (e) the lane STOP file and the wallet-wide STOP and HALT files are absent, and lane cash and the open count are recorded;
    - (f) the wallet balance is recorded, and the wallet-floor capacity at that balance is at least 8 (otherwise the lower number goes in the ask).

    Read Am.6 in full before writing the ask.
- **If the gate passes:** write a HELM-ASK for the Am.6 install: stake 2.5e8, budget 2.1e9, daily 5e8, pin 8dec3a3b. Use `/data/mal/hunt-1008/ops/manager22/HELM-ASK-1112-1.DRAFT.md` for its format only. The new config must start from the **Am.5 live config** `live-320cccc5….json` (sha256 aa6c355f…), not the older fc6a config named in that draft, and change only the stake, budget and daily stop.
- **If it fails:** stay at 0.10 and tell the owner. The only re-decision is at 40 trades, and only with the owner's word.
- **After an install:** run the fills check at the 10th 0.25 trade. If it says no, write the lane STOP and revert to the Am.5 config.

**Hourly status.**
- Script: `/data/mal/hunt-1008/ops/manager22/hourly.sh` (copied from manager22's scratchpad).
- Run it as a MiScusi job:

  ```
  B=$(base64 -w0 hourly.sh); miscusi-agent job submit --name hourly --machine mal-fast-0 --role ops --mem 512M -- "echo $B | base64 -d > /tmp/h.\$\$.sh; sh /tmp/h.\$\$.sh"
  ```

  then `miscusi-agent job log <seq>`.
- **The bridge-event filter in the script starts at `ts_ms` 1794419100000 (11-11T17:45Z).** As the log grows, raise that constant to the current day.
- It prints:
  - the lane report;
  - both unit PIDs;
  - from the host checkpoint: gaps, hwm, log_seq and gap_refusals;
  - the follower's lag, jumps and retries;
  - disk;
  - the day's bridge events.
- **What healthy looks like:**
  - executor 1929135 and host 3484327 active;
  - hwm within the hour;
  - follower NRestarts 0, backlog_jumps 0, lag under ~50;
  - disk free above 22%.

**Helm protocol.**
- Requests are files `HELM-ASK-<MMDD>-<n>.md` in `/data/mal/hunt-1008/push-1016/hunt29/p1/`; replies are `HELM-REPLY-*` in the same folder.
- Helm acts on the owner's word given directly in Helm chat, so ask the owner to tell Helm too.
- Verify every reply with a read-only job before recording it.

## 2. Fixed steps
Session crons are in `/data/mal/hunt-1008/ops/manager22/crons-manager22.json`. Re-create them with CronCreate (with your own state-file name) and write your own crons file.

| UTC | Step |
|---|---|
| hourly :17 | HELM-REPLY-*, PR verdicts, miscusi_inbox, the FLOOR hourly job (§1), wallet; one state line |
| **11-13 ~14:06** | Open trade e3b7cc9de20e65bd exits (time stop) at the latest; count it toward the 20 |
| **11-13 09:13** | Successor for **#1952 watchdog** (ends ~11-14T04Z). Recover the A3 #1896/#1898 commands **without** job_status (notebook n_KAyq8dRg6ZzKRQ). |
| **11-14 01:00 (R)** | **EXP-029 read, procedural FAIL** (Erratum 1g). The event was the 0.10 install at 2026-11-12T00:46:04.584Z, so C = 11-13T00:00Z and R = C + 25 h. Run the read once, at or after R:<ul><li>the four §4 tests on trades in [S = 11-09T06:00Z, C), pooled and split by stake (0.03 / 0.10);</li><li>the pre-S sends listed separately (Erratum 1, item 4);</li><li>the report labelled **FAIL (procedural)**, never a market verdict;</li><li>QP before merge;</li><li>quote the Erratum 1e MAJOR 1 disclosure and the QP ruling.</li></ul>manager22 added this step to `crons-manager22.json` at the handoff (no cron covered it before). |
| 11-14 12:00 | Host check #2348 (pin) runs; read its exit code |
| **11-15 06:41** | **Tip hand-off check** (`docs/runbooks/tip-writer-handoff.md` §5/§6). Writer #2033 hits its limit ~06:09Z and push #2039 ~06:24Z (limit 335 h, from the job list elapsed time; the runbook's "~07Z" is approximate). The gates (#2501, deadline 08:08Z; #2502, deadline 08:23Z) release the queued successors (#2503 writer, #2504 push). Confirm:<ul><li>the gates exited 0;</li><li>the successors are running, resumed and caught_up, with no REFUSED;</li><li>a counts-only FLOOR host gap check.</li></ul>Expect one writer-restart hour. If a gate exits 4 or 5, follow the runbook by hand. |
| ~11-15 23:30 – 11-16 00:15 | #2055 pool_cache and #2064 hour-health staging reach their limits. They do tip health; start successors only if something FLOOR-side reads them. |
| 11-16 03:23 | vault1.0 out-of-sample paper check (§3); cron dd71b2fc |
| 11-16 06:13 | HELM-ASK-1108-1 reset window opens (06:00Z to 11-21T00Z). Nudge Helm. Use the **current** pin and config; only the one state field changes (ADDENDUM-1). |
| **11-17 09:13** | **A3 successors are required for FLOOR.** The executor checks the A3 gate in `/var/lib/mal-hv4-c0/a3/daily.jsonl`:<ul><li>#1896 deadman ends ~11-18T21:17Z;</li><li>#1898 A3 loop ends ~11-21T01:01Z.</li></ul>Confirm `run_utc` in daily.jsonl advances. Also check #2134's consumers (ends ~11-21T02:31Z). |
| 11-21 00:05 | #2353 reset check (exit 3 means not done; chase Helm) |
| 11-21 18:13 | Successors to END 2026-11-30T07Z for:<ul><li>#2290 daily report and #2291 health watch (both end ~11-22T13Z);</li><li>**#2266 FLOOR tip link-dir** (≈11-22T01:43Z);</li><li>#2062 follower health (~11-21T01Z).</li></ul> |

MiScusi refuses job limits above 14 days. `after` fires only on exit 0, never on a time-limit stop; that is why the tip hand-off uses gate jobs.

## 3. Research lines
- **FLOOR rule variants** (report `/data/mal/hunt-1008/ops/reports/2026-11-12/floor-variants.md`, notebook n_6eQ1B9vosJ7fvA). Paper, in-sample, 0.10 flat, against the same-send base (total 0.2716, ex-top-3 0.0682, n 50):

  | Variant | Change vs base | Positive days | Verdict |
  |---|---|---|---|
  | age12 | Δ −0.031 | 2/7 | REJECT |
  | vault0.75 | Δ −0.018 | 2/7 | REJECT |
  | vault1.0 (Q_STAR 0.5e9 → 1.0e9) | Δ +0.0625 | 4/7 | WEAK, not adopted |

  - vault1.0 is not monotonic across the cap values, and the result is in-sample.
  - Adopting it is not a config change (`ops/reports/2026-11-12/floor-v3-change-surface.md`). It would need:
    - a new frozen rule `l1-floor-v3`;
    - a second host and executor (floor-lane-2) under a DEC-035 amendment;
    - QP.
  - Decide only after ≥ 7 out-of-sample days (cron 11-16 03:23).
- **The Nov funnel and drought analysis:** `ops/reports/2026-11-12/floor-nov-funnel.md`. The rent-corrected November paper replay (flat leg, K = 16, 0.25 stake, n 50) gives a mean of +5.66% per trade. That figure is **in-sample**:
  - the median trade is −2.21%;
  - the subset after the deploy is −0.1035 SOL with the top 3 removed (Am.6 carries these caveats).

  Never quote the mean without them. The owner's target is about $9/day (≈0.108 SOL/day at $83.6/SOL); 0.25 SOL with K = 8–16 is the planned path.
- **All other lines are off:**
  - EXP-012 shadow: no regime to catch (11-09 study).
  - Haiku forge: stopped 11-09, about $60 of API budget left. The $200/month cap resets monthly.
  - slowbot, the HV books, h5 and s1t: retired 11-08.

## 4. Owed (not yet done)
- **Records batch PR** (`claude/records-1112`, T0 unless it touches DEC/EXP text):
  - LAB_STATE rows for the follower restarts:
    - 11-11T17:53:56Z (#1316);
    - 11-12T17:58:35Z (#1320/#1321; drop-in backup `30-inner-migration.conf.bak-879df0b3`, PID 1559764 → 2254354, resume slot 468617368).
  - The tip hand-off plan (excluded price-path hour ≈ 11-15T06Z).
  - The #1319 nits:
    - start-request vs journal instants;
    - "about 0.007" provenance;
    - the "Next" line;
    - the inclusive end of the failed-buy window.
  - The #1318 nits: 11-07 −0.0096 rounding; "impact" wording.
  - DEC-031 note: tip-source books pin no `--backlog-slots` (Am.23/Am.26 gapfill 50).
  - The #1652 let-end (11-12T00:30Z) and the #1814 let-end (11-11T07:20Z).
  - `data/console.json` update.
- **Variant script branch:** `/data2/mal/wt/floor-variants` (`scripts/research/floor_variants_price.py`). It is not yet in a PR.
- **Stale open PRs (#905–#1263):** parked or never-merge. Triage only if there is time.

## 5. Running jobs (`miscusi-agent job list --active`, 11-12T21Z)
- **FLOOR:**
  - #2290 daily report;
  - #2291 health watch;
  - #2266 tip link-dir;
  - #2348 and #2353 checks.
- **Tip:**
  - #2033 writer and #2039 push (they end ~11-15T06Z);
  - gates #2501/#2502, with successors #2503/#2504 queued behind them;
  - #2055, #2062, #2064 health jobs.
- **Guards:**
  - #1952 watchdog;
  - #2452 disk guard;
  - #1896 and #1898 A3 guards (job_list only);
  - #2214 → #2217 structure monitor;
  - #2134 venue tape.
- **fast-0 limits:** 8 CPU slots. Use `--mem` (required) and `--cpus 0.2` for status jobs. Jobs run under `sh`, with no bash-isms.

## 6. Suggested first tasks for a new model
These are low-risk; none of them touch the live bot:
1. The records batch PR (§4).
2. The EXP-029 procedural-FAIL read at R (§2), QP first.
3. Put the variants pricing script in a PR.

Leave the executor and the Am.6 install until you have run a few hourly cycles cleanly.

## 7. How to verify this handoff
- `git log --oneline -1` on main shows this PR or later.
- The hourly job shows executor 1929135 and host 3484327 active, the follower PID 2254354 (unless it was restarted and recorded), and the hwm current.
- The last line of `state-manager22.md` mentions this handoff.
