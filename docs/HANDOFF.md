# HANDOFF: manager12 to the next manager (2026-10-18 ~05:55Z)

Read this first, then LAB_STATE "Now", CONSTITUTION, and DEC-031. The previous HANDOFF (10-12..10-17, H5 canary, seal window) is archived at `ARTIFACTS/lab/handoff-archive-2026-10-18.md`. Its section 6, "Rules that bite", still applies. No book has cleared the promotion gate. Paper only.

## 0. First 10 minutes
1. **CronList.** Session crons die with the session. Re-create them from `/data/mal/hunt-1008/ops/crons-1016-manager12.json` (`recurring` keys point at `recurring-crons-1011.json`; plus `oneshot`).
   - **Next one-shot: 2026-10-18T12:07Z, the EXP-025 Look 1 R8 run.** Run once after 12:00:00Z: the command in EXP-025 section "P5 record (2026-10-16)", as a MiScusi job, then a records PR.
   - **Fee-arm watch (`53 */2`):** exact command in `/data/mal/hunt-1008/ops/feearm-watch-1080.cmd`; stop after 2026-10-22T01:00Z.
   - **Tip-tape archive (`23 3`):** now 1990 MB, because fast-0 refuses jobs of 2 GB or more.
2. **Disk.** `/data/mal/c1nf-parity-venv/bin/python -c "import shutil;u=shutil.disk_usage('/data/mal/blocks/forward-1016');print(100*u.free/u.total)"`.
   - At 05:02Z it read 19.85%.
   - Walk 2 (job #1036) writes nothing below 20%. Its newest sealed hour was 2026-10-16T13 at 05:02Z and 2026-10-16T21 at 15:02Z.
   - Arena writes on research-0 need 25% (DEC-031 §3.8).
   - The owner was asked to run the earlier `rm` (~35 GB of finished-hunt tmp dirs) and/or have Helm archive forward-1002, forward-1002ev, exp024 and exp025 (~104 GB). Helm is handling backup via the owner's OneDrive.
   - **Correction (2026-10-18 ~15:15Z):** `forward-1002` must STAY, because it is on EXP-025's Look 1/2 allowlist. exp024, exp025 and forward-1002ev were already removed on 10-18 (see the EXP-025 HOLD below). Before naming any dir for archive, grep EXP/DEC/HOLDOUT_LEDGER/HANDOFF/crons for pending reads.
   - **15:02Z:** 22.42% free, falling about 0.05 points per hour (22.63 at 11:02Z). A straight line reaches 20.00% about 10-20T13Z. Walk 2's newest sealed hour is trades-2026-10-16T21; at about 1.5 sealed h per wall h it catches up to now only about 10-22T03Z, so it needs about 20 GB more freed before 10-20. The owner was told; notebook n_M2ZOqFGxFXWOMQ. The guard cron in `/data/mal/hunt-1008/ops/crons-1016-manager12.json` (host file, not in git) now triggers on "free < 21.5% or the walk-2 hour has not advanced".
   - The manager's own `rm` is denied by the permission check; do not work around it.

## 1. What the owner asked (2026-10-18 ~03:00Z) and what was built
- **Owner:** "Start to come up with a plan, and then execute on it." Run many strategies forward at once: revive BOOST (H5), a general Haiku trader with a scan→risk→execute→P&L→refine loop, Kalshi plus Grok, more SOL after about 5 days of forward testing. The gate is unchanged. Memory: `owner-arena-1018`.
- **Answer: the forward paper arena** (`/data/mal/hunt-1008/push-1016/hunt20/PLAN.md`, with a status log at its end). DEC-031 is merged (49a43ac), with quant-proof edits A–K and R1–R4.
  - **Book:** bot_id + frozen_sha + clock, registered before the clock.
  - **Read:** one per bot_id at an outcome-blind count instant (≥100 attempts over ≥5 UTC dates). Deciding cell: 1.9 s at the pinned stake, under both fail models and both strand rules.
  - **Pass path:** a first pass is a SCREEN, allowing a canary only. Stake above canary size needs a confirmation on later trades at p ≤ 0.05/c.
  - **EXP-027 isolation (§3):** no r4b, l3 or laya imports in arena processes; arena results never register as a push-family book.
  - **Seals:** the EXP-025 seal holds until "Look 2 refused". H5 v1 is under EXP-024 Am.2, and v2 (plus v1 after 11-06) under Am.9.

### Merged today
| PR | merge | what |
|---|---|---|
| #706 | 49a43ac | DEC-031 + EXP-024 Am.9 + ledger walk-2 arena sub-entry |
| #707 | 4676824 | h5_shadow `--fresh-forward ARENA`, arena H5 scorer and cost module (DEC-031 stack, refit at read, per-trigger chain-clock delay), arena-h5.sh |
| #708 | fe854ee | arena paper engine + gate scorer (vendored `arena_stack.py`, feed delay d required, write-instant latency) |
| #709 | f5b7bd7 | arena feed + state (public RPC, zstd compaction, 25% disk reserve, gap records, 360-min candles) |
| #710 / #711 / #712 | 47591ed / f5a9ca3 / 25596ca | Haiku v4 cards, chain facts, agents + playbooks |
| #713 | 3752bc5 | Kalshi candles bounded to each market's window |
| #714 | 7a319e3 | hv4 runner (13 arms, live hash-chained decision log, resume equivalence) |
| #715 | 6fe3bba | hv4 nightly reviewer (placebo, manifest chain, skip pricer) |
| #716 | c3dc98f | arena follow-ups (guard at call slot, HEAD binding, incidents, disk-failure halts, forced-boundary rules, d from p90) |
| #717 | 32f4f91 | hv4_cards candle_retention_min fix |

### Open PRs
- **#719 MERGED as 1f7a89c**, with quant-proof OK and reviewer APPROVE on 7b1a59e. It pins: code sha256 at c3dc98f, parity sets and their 78 source files, the engine references, d = 7, and the deployment conditions.
- **#718 (hv4-read, head 085d93a, READY, not draft).** Rebased on main and uses the real `boundary_ms` and `common_boundary`; 44 tests. **It still needs a reviewer AND quant-proof** (it reads gate results). Launch both fresh.

## 2. Next steps, in order
1. **H5 books: STARTED.**
   - #719 merged as 1f7a89c.
   - Registered by job #1157: `h5v1-boostfloor-a1` and `h5v2-boostclock-a1`, frozen 1f7a89c, clock 2026-10-18T06:00:00Z, deciding 1.9 s at 0.30 SOL. Notebook n_e-80oeFe6Ma0zw.
   - Shadow job **#1158** on mal-fast-0, 14-day limit, so it ends about 11-01T05:54Z. Before then, extend it or restart at the same sha; a restart on the same day needs a new out dir. The start record was verified by job #1160. Output: `$HOME/data/arena/h5-2026-10-18`.
   - The records copy is in `ARTIFACTS/lab/arena-records-2026-10-18.md` §5.
   - **Daily:**
     - `/usr/bin/python3 -m tools.arena_h5_score score --shadow-dir "$HOME/data/arena/h5-2026-10-18"` on fast-0 at git_ref 1f7a89c. It defaults `--arena-root` to `$HOME/data/arena` there. Counts only: no P&L to any channel before the read.
     - **Pass `--shadow-dir`.** Without it the default glob is `h5-shadow-*`, which does not match the shadow's `h5-<date>` dir, so it scores 0 attempts (job #1175).
     - Post the decision-log sha to the notebook.
     - Watch the gap rate. H5 flags pools only on a common-down of all sockets. If 413/429 rejections persist, propose cancelling the old shadow #817.
   - **A3 HALT:** `arena_h5_score halt --at-ms <flag instant>`.
   - **FIRST PRIORITY (found 2026-10-18 17:06Z, dry run #1176, counts only):** both books are on course for NOT_DECIDABLE.
     - Each has 13 attempts in about 11 h: 4 counted and 9 excluded, all `base_breaks` (base_breaks_unresolved_settled != 0). excluded_share 0.692 is above DEC-031's 5% cap.
     - This is the live executor's data-integrity refusal, and it was heavy before the arena too (10-10 canary: 13 refusals against 3 trades in 24 h).
     - Before more of the window passes, with quant-proof, decide whether base_breaks are executor refusals (not attempts, or their own counted class) or feed-quality exclusions. Change it only by amendment before any read, outcome-blind (only counts seen so far).
     - Also weigh the Helius-fed listener for the arena shadow: it now uses public websockets.
     - Notebook n_T59DybutlUqsKg.
   - **Before the first H5 READ** (not the clock), the condition from quant-proof comment 6246275907 on #719: a parity check of H5 entry/exit state selection on the pinned H5 hours against an independent extraction (for example the l3-simlib reader from #708). Timing and states only, 0 mismatches. A fix to `arena_h5_score.py` is a pinned-file change, so disclose it with quant-proof OK.
   - **Read:** at the outcome-blind instant (≥100 attempts over ≥5 UTC dates). At about 16 non-synthetic triggers a day, expect 1–3 weeks. The window ends at 2026-11-06T00Z.
2. **Haiku v4 shakedown, then start.** Blocked on research-0 disk being at least 25% free.
   - **Feed:** run the arena feed (`tools/arena_feed.py`; command in PR #709) as a MiScusi job on research-0.
   - **Runner:** run `scripts/research/hv4-runner-job.sh` from a frozen MiScusi job tree with `--feed-delay-slots 7`. Shakedown in stub mode first; check `cache_read_input_tokens` in real mode; budget $4.90/day, $160/month, per-arm ledgers.
   - **Nightly review:** `scripts/research/hv4-review-job.sh` at 00:05Z, after one stub-mode run on real tape (unmeasured on real data).
   - **Registration:** register the 13 arms with model ids before T0 (DESIGN: T0 ≥ 2026-10-21T00Z).
   - **Known gaps:**
     - BST is dormant until a synthetic-pool classifier is wired into `H5Signal(classifier=...)`.
     - holder_count is a wallets-net-long approximation and a neutral field.
**Update 2026-10-18 ~07:45Z (after the hand-off was prepared):**
- **A3 daily (job #1161, 07:11Z): HALT none.** All six flags were evaluated and clear, and the last slice median was 336.5 s (n 10). The H5 arena windows continue. Notebook n_DrI5e52IaymR4A. The slice watch is job #1162.
- **Fee-arm watch (job #1163): OK.**
  - **15:17Z (job #1172): still OK, but no new fills since 06:59Z.** Read-only checks #1173/#1174 (counts and mtimes only) show:
    - the runner is alive (decisions.jsonl written 15:16Z);
    - the executor's view of intents.jsonl is the same inode as the host file, so this is not the runbook §2c re-created-file case;
    - 0 intents after 06:58:53Z.
  - This fits the CEILING ledger's per-UTC-day entry gate (`tools/forward_paper.py:2233`, which resets at the day roll). 10-17 had the same shape. Which condition fired was deliberately not read (probe seal).
  - Expect intents after 2026-10-19T00:00Z; if none by about 02Z, tell the owner. Notebook n_0gM_cwhvVRpPwA.
- **Kalshi weather holdout: DONE, run once, and every tradeable rule FAILS.**
  - M2 −5.08c, M3 −5.17c; M4's CI lower bound and worst-3 both fail; T1 is negative.
  - The M1 maker pool is +0.67c, descriptive only: LAX-heavy, and simple quotes are adversely selected.
  - Report: `/data/mal/hunt-1008/push-1016/hunt17/kalshi-weather-mm/REPORT.md`. Notebook n_e-kK7eslvH63CQ.
  - Follow-ups:
    - fix the historical candle parser in `tools/kalshi_public.py` (it reads `close_dollars`/`volume_fp`, but the historical tier returns `close`/`volume`);
    - a forward book observer for LAX and NY;
    - an M4-multi forward paper rule, frozen first.
  - The weather-mm item below is therefore done.

**Update 2026-10-18 ~12:15Z: EXP-025 Look 1 R8 run is on HOLD (quant-proof).**
- **Disk:** free rose to 22.63% at 11:02Z, and walk 2 #1036 RESUMED (sealing 2026-10-16T15+, catching up).
- **What was removed:** `/data/mal/exp025`, `/data/mal/exp024` and `/data/mal/blocks/forward-1002ev`, presumably by the storage archive the manager asked Helm for. `forward-1002` is still present.
- **Why it matters:** exp025 held EXP-025's look ledger (`LOOK_READS.jsonl`), `look1/` and the pinned E0 records. The P5 record's stat-only precondition, that `/data/mal/exp025/look1` exists, FAILED at 12:07Z.
- **Before the R8 run** (`/data/mal/audit-1008/venv/bin/python tools/exp025_look.py run --look 1 --decoder-blobs none`):
  1. establish what removed it and when (ask the owner or Helm), and confirm from the MiScusi job history that no `exp025_look.py run` or `take_lock` ever ran;
  2. add a dated disclosure record to EXP-025 using quant-proof's sentence (notebook n_vuyyZ4nehgxh1g has the full text), with quant-proof OK;
  3. then run the P5 command.
- **Restore:** if the owner can, restore exp025, at least `LOOK_READS.jsonl` and `e0/`, **and `blocks/forward-1002ev`**: EXP-025's Look 1 allowlist pins forward-1002ev `[2026-10-09T00, 2026-10-16T01)`, and §10 item 3 reads that week's tape from it directly (forward-1002 is only the cross-check). Look 2's R8 (after 10-26T12:00Z) needs the same.
- **Lesson:** never list a dir for archive without checking pending steps that read it (memory `feedback-archive-list-check`).

3. **Kalshi.**
   - **#1146 (candles, 7 cities, bounded windows):** when it completes, message the weather-mm agent a0f680d593d71124d to run the holdout once (hunt17/kalshi-weather-mm).
   - **#1145 (markets + fin/pubnum trades continuation):** when done, resume the kalshi-pubnum agent a844210f7b7536009 and the universe agent aafd58ba225514325.
   - **Crypto trades continuation:** deferred.
   - **X-observable:** the press-lid and Elon-tweet series have 0 markets. Mention-market data is downloaded (#1141). The Grok prompt is on hold until a dev check of closed-press and cancel flags on mention markets (`hunt20/research/kalshi-x/REPORT.md`, with the manager update at its end).
4. **EXP-027 (r4-b).**
   - W0 2026-10-18T03, E 2026-11-07T03, read about 11-09.
   - It needs walk 2 sealing hours (disk), then the hourly count job (`tools.r4b_count`, audit venv + PYTHONPATH /data/mal/venv site-packages, `--through-hour` = latest contiguous verified hour).
   - Walk 2 #1036 limit is about 10-22T19:21Z: extend or resubmit with the SAME pinned blobs (EXP027_WALK2_JOB_2).
   - The 20% reserve is in a pinned blob and cannot be lowered.
5. **Other clocks.**
   - Job A (#1135) successor before ~10-23T23:20Z; hourly lag check, lock pid = running pid.
   - H5 canary end_ms 10-23T19:29:53Z.
   - C1-NF #658/#662 end ~10-24T21:4xZ.
   - EXP-025 Look 2 R8 after 10-26T12:00Z.
   - Fee-arm probe ends 10-21 and is NOT extended (owner and Lyra agree).
6. **Records owed.** LAB_STATE "Now" refresh (arena lane, merges), console.json, library rows for hunt10–20, the LAB_STATE mid-week runner restart line (10-17T19:45:24Z), and the Job A ledger lines.

## 3. Decisions made today, and why
- **Arena instead of one-at-a-time certification.** Speed and breadth, with the gate unchanged. Multiplicity is handled by screen → canary → corrected confirmation (quant-proof).
- **H5 on fast-0.** Different filesystem from walk 2, and it writes tiny output. Its scorer measures delay per trigger on the chain clock, so d does not bind it.
- **d = 7 from p90, not the median.** Under-correction favours P&L.
- **Guard reference = the feed slot at the call.** The bot's stamp cannot pick a favourable reference.
- **Comparators:** scored at the earlier natural instant. A later forced boundary is allowed only after a recorded non-PASS, because a PASS's later trades are its confirmation window. The D14 learning read is NOT_DECIDABLE for an arm that passed its screen.
- **Haiku v4 playbooks.** Only measured directions: dead/shallow pools lose the round trip; Mayhem under 24 h is excluded. holder_count, top10, creator-sold, sniper and same-slot are NEUTRAL. The source AUCs have no fixed direction, and a wide holder base went with MORE drains on fresh pools. The manager's earlier "more holders = fewer drains" was wrong and was caught before merge.
- **Budget $4.90/day; BST-R warm-up 5; a manifest refusal halts the whole persona group; the BIG trail peak is quantized to 1 point** (disclosed).

## 4. Agents that may still notify (resume with SendMessage; their final report is not shown to the owner)
- a082e9e337b878bbe (quant-proof, #719)
- aecc5920ec52995e1 (reviewer, #719)
- ace30284da0c26753 (builder, #718)
- a0f680d593d71124d (Kalshi weather-mm)
- a844210f7b7536009 (pubnum)
- aafd58ba225514325 (universe)

Builders stop at ~40 turns and reviewers at ~20. Agent writes to /data/mal are often refused, so they return text and the manager saves it.

## 5. Running jobs (MiScusi)
| Job | What |
|---|---|
| #1036 | walk 2 (stalled on disk) |
| #1135 | Job A meta-capture v4 |
| #1133 | Kalshi recorders |
| #1145 | Kalshi continuation (queued after #1141, which passed) |
| #1146 | Kalshi weather candles |
| #817, #729, #1034 | old H5 shadow, enforce loop, CAP-PICK exporter |
| #658, #662 | C1-NF soak and monitor |

Fast-0 tapes under `$HOME/data/arena`: the delay tape `tape-delaymeas-20261018T0402` and the smokes.
