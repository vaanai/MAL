# REGISTRATION: DBC D1 m10, registered forward confirmation read (R1-R14 of QP-RULING-1024-dbc-m10-swap3.md)

Status: DRAFT until the tag `dbc-m10-forward-v1` is placed on the commit that holds this file after a reviewer verdict and a QP OK on PR #868 (Swap3 pricer path). A pins table (sha256 of every code
file, produced by `python3 docs/research/dbc-m10-forward/pins.py` at the tag commit) is appended in PINS.txt in the same commit. Registration sha256 and "Registered at" are recorded in
SHA256SUMS.txt by the manager at tagging (`date -u`). Ruling sha256 2a34dfc0... (QP-RULING-1024-dbc-m10-swap3.md).

## What this read is
CONFIRMATION of one cell on future hours, from a backfilled tape. It is NOT the promotion-gate forward-paper read and NOT a live licence. A PASS licenses starting a forward paper DBC book (own
registration, day-1 dry score, clean clock, not in a kill-review week). A FAIL or NOT_DECIDABLE ends m10 and every D1 variant on DBC: this is the last attempt. No sentence may say m10 makes money.

## History disclosed (multiplicity and prior)
- m10 came from the P14 screen on 10-14..10-16 (about 2.2 days of Swap2 era): flat +0.0212, pressure +0.0164 per attempt, 369 attempts, 12 cells, best of 12, 3 days.
- #1494 (one-look holdout 10-17..10-23): NOT_DECIDABLE (tool defect, 0 trigger prints because EvtSwap3 was labelled a hook); printed "FAIL no attempts" stays on record. Those hours are SPENT for m10 and all D1 variants.
- P17 screened 11 further cells (D2, D3, D4, new D6) on 10-17..10-23: all FAIL. Screened cells on this data family: 23; m10 is the only positive; the Swap3-era rehearsal below is disclosed. Registered cells for this read: ONE (m10) => k = 1, CI bound = the 5th percentile.

- Rehearsal #1507 (tools/dbc_fwd_read.py end to end on exploration hours 10-14..10-16 with a patched window; exploration data, Swap2 + Swap3 pricing mix, not part of the read), verbatim: 589 attempts, 398 fills,
  10 strands; flat +0.01358 per attempt (trade CI90 lo +0.00635), pressure +0.01051 (CI90 lo +0.00455); at a 2.5 s entry leg +0.0097 flat / +0.0078 pressure; 10-16 (83% Swap3 rows) 223 attempts at about
  +0.004 per attempt, about one-fifth of the Swap2-era mean. Expectations are LOWERED accordingly: expect well below the +0.021 of the Swap2-era screen.

## R1 Window
Fixed 7 UTC days [2026-10-25T00:00Z, 2026-11-01T00:00Z) (168 hours). If the tag and this registration are not both done before 2026-10-25T00:00Z, the window becomes the 7 days starting at the first 00Z
after both, stated in a dated addendum committed before that instant (the constants L.FWD_START/FWD_END, the expected names and MIN_READ_UTC are then edited in a pre-window commit and re-tagged
-v1b; nothing else). No extension, no early stop. Fewer than 100 fills at the end = NOT_DECIDABLE, which ends m10. The read refuses before window end + 10 min (2026-11-01T00:10Z).

## R2 Code (frozen at the tag)
- Tag `dbc-m10-forward-v1`. Pinned files and sha256: PINS.txt (dbc_price.py, dbc_screen.py, dbc_config.py, arena_stack.py, dbc_fwd_read.py, venue_backfill_dbc.py, plus decode, IDL, config map).
- Read tool: `python3 tools/dbc_fwd_read.py --confirm-one-look` (tools/dbc_fwd_read.py). Marker `/data/mal/hunt-1008/push-1016/hunt29/dbc/HOLDOUT_READ_DONE_v3` (atomic O_EXCL, outside --out),
  preflight (all 168 hour NAMES under the fresh root, newest mtime >= 10 min), output dir `/data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-v1` (must be empty or absent).
- Data guard: tools.dbc_price.enable_forward_window accepts only [2026-10-25T00, 2026-11-01T00) directly under /data2/mal/venue-tape-backfill/fwdv1/meteora_dbc; every earlier hour is refused.
- Legacy path: `tools/dbc_screen.py --cells m10` on 10-14..16 reproduces #1488 byte-for-byte (job id and head recorded in PINS.txt at the tag, as #1497 did at 882c72f6).
- Rehearsal (not part of the read): docs/research/dbc-m10-forward/rehearsal.py ran the full tool path on the exploration hours 10-14..16 (job id in PINS.txt).

## R3 Data
Helius backfill by tools/venue_backfill_dbc.py into the FRESH dir, ONLY after each UTC day closes (day D fetched on D+1 from 00:30Z), as MiScusi jobs (POSIX sh, flock slot, `--credit-cap` set):
`python3 tools/venue_backfill_dbc.py run --start <D>T00 --end <D+1>T00 --out-dir /data2/mal/venue-tape-backfill/fwdv1 --credit-cap 500000`  (7 jobs; D = 2026-10-25 ... 2026-10-31).
Write-only: no smoke, no count and no open of a window hour before the read; per-hour completeness markers are the only check (`backfill_hour_end`, no gap row); directory listings only. The manager
records each job id and its counted credits (about 14,050 per hour, 0.34M per day, 2.4M for the window; Helius autoscaling credits, reported per CLAUDE.md). More than 6 incomplete hours = NOT_DECIDABLE
(refetch of an incomplete hour is allowed only before the read, by the same tool, no content inspection).

## R4 Config map
Base map = docs/research/dbc-p17/data/cfg_summary.json (7,399 configs, fetched by tools/dbc_cfg_fetch.py, job #1504). The read tool, after the marker claim and BEFORE the first load, scans only the
`config` and quote-mint strings of window trade lines, fetches every config not in the map with getMultipleAccounts (sorted keys, 100 per call, finalized; deterministic, no content-based choice),
writes cfg_map_final.json to the output dir. Pools whose config cannot be decoded are excluded and counted; more than 5% of window SOL-quoted rows in unmapped configs = NOT_DECIDABLE.

## R5 Cell (frozen; no flag changes it)
D1 m10 exactly as docs/research/dbc-p14/IDEAS.md: BUY print with post progress p in [0.02, 0.35]; over the pool's last 60 s buys >= 10, net buy SOL >= 4.0, post spot / window-first pre-spot - 1 in
[+5%, +40%]. Exits: sl 0.90, trail 0.90 on the running peak from the decision spot, time 600 s; cooldown 600 s per pool. Universe: SOL-quoted; migration_threshold >= 5 SOL (config value for Swap3 rows);
0.25 SOL buy impact <= 2% at the signal state; p <= 0.85; curve not completed; not a bad (order-uncertain) row; one warm-up hour (no signal before 2026-10-25T01:00Z); attempts whose exit window
passes 2026-11-01T00:00Z are refused (block_edge).

## R6 Exclusions (counted per day, printed)
EvtSwap3 is NOT a hook. Excluded: real transfer-hook pools (event/ix ...WithTransferHook), configs with transfer_fee_basis_points > 0, pools of unmapped configs.

## R7 Pricing
Swap3 rows by the config-curve path (`enable_swap3_pricing`: Swap3 buy fee_on_base_token is the fee-side evidence; sell fee on the quote output; Swap2 rows by the legacy inference). Stake 0.25 SOL, guard 1.15 plus
migration room, 2 x 160,000-lamport sends, rent 0 (rent-charged line reported), no haircut. Order fix and bad-row refusal as coded; report-only "no_bad_row_exclusion" line.

## R8 Strand
PRIMARY: full loss (stake + buy send + 2,039,280 rent). Report-only: strand at the last on-curve price.

## R9 Latency
Deciding leg 1.9 s on both legs, k = round(1.9 / slot_s), slot_s per hour from the tape (printed). The fast-0 measurement (p50 1.1-1.3 s, sum of p90 1.9-2.1 s, one quiet 10-minute window, no DBC send ever made)
is not a reason to shorten it. REQUIRED robustness condition: flat AND pressure mean per attempt at a 2.5 s ENTRY leg (exit leg 1.9 s) > 0 (`entry_2p5s_mean_gt_0` in the checks).
Report-only: entry 1.0 / 1.3 / 3.4 s, exit 3.4 s, 1.0 SOL stake.

## R10 Fail models
Flat 15%, and pressure at slope scale 1 with the intercept refit on the cell's fills to mean p 0.289 (tools.arena_stack.fail_legs). Pump-fit, unvalidated on DBC. Both decide.

## R11 PASS (all, under BOTH fail models, primary line; 0 is a fail; anything else FAIL)
fills >= 100; >= 5 UTC days with attempts and strictly more than half of those days with total > 0; trade-level CI90 lower bound of mean SOL per attempt > 0 (tools.arena_stack.boot_lo, 1,000 draws, seed 1,
5th percentile, over all attempts incl. guard-unfilled); total after removing the top 3 trades > 0; total after removing the best day > 0; mean > 0; the R9 2.5 s condition. Implemented in
tools/dbc_fwd_read.py `verdict()` (tests: tools/test_dbc_fwd_read.py).

## R12 Multiplicity
m10 is the ONLY registered cell on this window (k = 1, 5th percentile). The 23 earlier screens are disclosed above. If another cell is ever registered on the same window, every registered cell's CI bound
moves to the (5/k)th percentile BEFORE the look.

## R13 Printed
Commit and file shas; hours present; attempts, fills, guard-unfilled, strands, refusals by reason; per-day n and SOL under both models; mean, total, ex-top-3, ex-best-day, CI90 lo (trade; date-clustered info);
fee paid buy/sell/round trip bps; slot_s per hour; Swap3 share, hook / transfer-fee / unmapped-config excluded share per day; attempts per day; config-map report; sensitivities (report only).
Output: fwd-m10.json + fwd-m10.md (sha256 in the md).

## R14 One look
The look is used at the marker claim: the tool prints "LOOK CONSUMED" immediately after claiming the marker, before any window file is opened. Any later failure (crash, missing key at fetch time, undecodable data,
anything) is NOT_DECIDABLE and ends m10. A FAIL or NOT_DECIDABLE ends m10 and every D1 variant on DBC. No re-tuning, no other D1 variant on these hours, no second read, no smoke on window hours.
Operational failures that can be checked beforehand (fetcher/key file/lock, out dir writable, time gate, preflight) are validated BEFORE the claim and exit without claiming.

## Before any live canary (QP section 3, not part of this read)
Forward paper DBC book passing the gate under both fail models plus owner approval; a DBC executor (does not exist); md5 decision-equivalence replay proof; trigger and send on mal-fast-0 with a processed
feed; stop if signal-to-landing p90 > 2.5 s; size stays 0.25 SOL.

## Addendum 2026-10-27T21:42:07Z (date -u): read tool, venue_backfill.py pins, read job chain, memory (records only)
Required by QP-RULING-1024-read-hardening.md item 2c (sha256 3e880055063e294d3b384a533b7e7a79974632688b7dc66b731ec1e520adebf2) and by the QP verdict on PR #890 (head 86b0e3d1, 2026-10-24T18:23Z).
Chain section A4 and memory section A5 updated 2026-10-27T21:51:12Z (date -u) after QP's OK at 36f203ad (comment 6454456703) and the chain resubmission.
Records only: no R or AM term, tagged file, constant or window changes. Nothing under fwdv1/ or the read output dir was opened or listed for this addendum. m10 has NOT cleared the promotion gate;
this read is CONFIRMATION only, and a PASS licenses a forward paper book (P18), nothing more.

### A1. Read tool (item 2c, verbatim)
"R2's read tool is tools/dbc_fwd_read_am1.py at tag dbc-m10-forward-v1-am1. tools/dbc_fwd_read.py is retained only as an imported library. Any invocation of it as a program or of its run() against FWD_ROOT is forbidden; a marker v3 claim by it ends m10 (AM6 class, licenses nothing)."
This supersedes the "Read tool" line of R2 above (`python3 tools/dbc_fwd_read.py --confirm-one-look`, output dir fwd-read-v1). The same line is already in docs/HANDOFF.md section 6 (11-01T00:10Z step) on main at 37bafde4, so this PR does not edit HANDOFF.

### A2. tools/venue_backfill.py: two pinned values, functionally identical (QP on #890, PIN finding)
- 15605f65894282f71d39dca5e2be1ba0935d5c9676a29c4be6415ac06ea23571 (sha256) is the file at tag dbc-m10-forward-v1 = c6d4c046bcca5459909fadece040e24762a2f897 (git blob 4fcb8810). PINS.txt records it. Fetch F (#1546) runs this version.
- 7185d58c2cc176f02acc0bb83899745d019317b9008a00c8ec46577beb3e2600 (sha256) is the file at tag dbc-m10-forward-v1-am1 = 7d5be9e51a39db1b8030843a273f747eb92a2967 (git blob 69268a16), and on main. tools/dbc_m10_read_ops.py pins this value for the read.
- How this was checked (2026-10-27T21:41Z, `git show <commit>:tools/venue_backfill.py` only):
  - `git diff c6d4c046 7d5be9e5 -- tools/venue_backfill.py` is one added line inside the module docstring (hunk `@@ -9,6 +9,7 @@`: the "Record, MiScusi job #1461: credits_used = 1,823,700 ..." LaunchLab note). The only commit between the two tags that touches the file is 5c8b355a (#856).
  - Python AST of both versions with the module docstring removed: identical (`ast.dump` md5 16e7d2af42c5859fcd9199056495afec for both). The same 29 definitions in the same order: CapReached, Credits, hour_floor, hour_label, parse_hour, hours_between, tx_notice, rows_from_tx, sort_key, make_url, read_key, Pacer, http_rpc, gtfa_params, fetch_hour, build_hour_rows, gap_rows, write_hour, load_cp, save_cp, run_hours, probe, main (plus methods __init__, charge, __init__, wait, call, progress).
  - Nothing in venue_backfill.py, venue_backfill_dbc.py, dbc_fwd_read.py or dbc_fwd_read_am1.py reads `__doc__` (grep at 7d5be9e5), so the docstring line cannot change behaviour.
- The fetch files F imports are identical at both tags and on main (QP on #890): venue_backfill_dbc.py c6f754fa, dbc_decode.py 10a52a52, arena_zst.py 4ddbf448.

### A3. Fetch F
F = MiScusi job #1546 (replaces #1510; only change `--credit-cap 3700000`, item 5), mal-research-0, in the jobtree at commit c6d4c046bcca5459909fadece040e24762a2f897 (tag dbc-m10-forward-v1), not in the am1 tag worktree.
Started 2026-10-24T18:28Z, limit 190 h (ends 2026-11-01T16:28Z). Source: the QP verdict on #890 and the manager's job record; this addendum did not open F's log.

### A4. Read job chain, as submitted (all mal-research-0, code vaanai/MAL @ 304bb9cd, POSIX sh, resumable false)
The first chain (#1785-#1793, serial: each G pass after the previous one, READ after pass 5) was cancelled before any of it ran. A G pass killed at its 3 h limit would have failed the chain and
blocked the READ (QP non-blocking note on PR #1068, comment 6454456703). It was resubmitted as below, so that no G pass can block the READ.
| Job | Role | after | Resources |
|---|---|---|---|
| #1784 | G gate g1: host-side sleep to 2026-10-28T06:00:00Z | none (running) | 64 MB, 10 h |
| #1797 | G gate g2, to 2026-10-29T06:00:00Z | #1784 | 64 MB, 25 h |
| #1799 | G gate g3, to 2026-10-30T06:00:00Z | #1797 | 64 MB, 25 h |
| #1801 | G gate g4, to 2026-10-31T06:00:00Z | #1799 | 64 MB, 25 h |
| #1796 / #1798 / #1800 / #1802 | G pass 1 / 2 / 3 / 4 | its own gate: #1784 / #1797 / #1799 / #1801 | 4 GB, 1 CPU, 3 h each |
| #1803 | G pass 5, FINAL | F #1546 only | 4 GB, 1 CPU, 3 h |
| #1804 | READ, Option A: `sh scripts/research/dbc-m10-read-job.sh A 10` | F #1546 and #1803 | 11 GB, 2 CPU, 600 min (10 h), priority urgent |
Cancelled, never ran: #1785-#1793.

- Gates: `T=$(date -u -d '<instant>' +%s); while [ "$(date -u +%s)" -lt "$T" ]; do sleep 60; done; date -u`. The gates are chained only to each other, and a gate cannot fail
  short of its time limit: each one sleeps at most about 24 h of its 25 h limit. The G passes are independent of each other. A failed pass stops only itself; the next gate and pass still run.
- Bounded wait, every G pass (1-5): check for a `venue_backfill_dbc` process every 5 min and proceed after 2 consecutive no-writer checks. After 20 checks (100 min) without that, print
  "G pass N skipped: writer still running after 100 min" and exit 0. This leaves at least 80 min of the 180 min limit for the pass. Then
  `python3 /data2/mal/wt/dbc-read-ops/tools/dbc_m10_gapfix.py pass --pass N --wt /data2/mal/wt/dbc-m10-read-am1 --budget 300000`.
- DISCLOSURE, rc mapping: every G pass maps the tool's rc 1 (some hours still gap) and rc 5 (G budget spent) to job exit 0. A skip also exits 0. So the job state does not show these outcomes,
  and the READ still starts. The real rc is printed in the job log ("G pass N rc=<rc>" or the skip line) and recorded in G-LOG.md (with G-STATE.json, kept with the read record).
  rc 10 (pins), 12 (refused) and any other rc fail only that pass. A failure of G pass 5 (#1803) means #1804 does not start (pre-claim, not a look): fix it, then resubmit the pass and the READ.
- Unpinned G worktree, disclosed: the G script runs from /data2/mal/wt/dbc-read-ops (HEAD 304bb9cd81727a7453a216dee055344522a4d387, `git status --porcelain` empty at 2026-10-27T21:51:12Z). Nothing pins that copy
  of dbc_m10_gapfix.py, which itself pin-checks only the tag worktree it fetches from. The worktree must stay untouched (no checkout, pull, edit or untracked file) until the read ends.
- READ #1804 runs at git_ref 304bb9cd (wrapper sha256 282c9d4a, launcher 04c98b97, ops 23776c67; the same on main). Its own time gate holds until 2026-11-01T00:10:00Z (1793491800), then pins (exit 10), memory (exit 13),
  completeness with up to 1 h wait for the 900 s mtime age (exit 11), guards (exit 12), then the Option A launcher. Every wrapper exit and every tool rc 2/3/5/6/7 is pre-claim, not a look, and goes to READ-ATTEMPTS.log.
  Once "LOOK CONSUMED" prints, anything other than rc 0 with fwd-m10-am1.json written is NOT_DECIDABLE and ends m10 and every D1 variant on DBC (item 1).

### A5. Memory (ruling item 1)
Item 1 says "On research-0, check free memory first. No other heavy job on the box." The second sentence cannot hold literally: the HV4 runner #1561 (to 2026-11-07T12:05Z) and the HV6 regime-block producer #1748
(from 2026-10-28T00:15Z, 14 days) run through the read instant, alongside smaller long-running jobs. Heavy queued jobs on research-0: #1775 (HV8 offline dump, 24 GB) and #1767 (P91 build, 16 GB), both waiting for room and able to start near the read,
and #1562 (HV4 runner seg2, after #1561, so not before 2026-11-07T12:05Z). Manager rule: no new heavy research-0 job from 2026-10-31T22:00Z until the read ends; #1775 and #1767 are held or deprioritised if they have not finished by then.
The check applied is the wrapper's MemAvailable floor: step 13, `dbc_m10_read_ops.py mem --min-gb 10`, run after the 00:10Z time gate and the pins and before the claim; MemAvailable < 10 GiB gives exit 13
(pre-claim, not a look). MiScusi also starts #1804 only when it can reserve 11 GB for it. The other jobs' memory is capped by their own MiScusi limits, and that capped coexistence is accepted in place of
"no other heavy job".
Sizing (manager's chain notes, not re-derived here): C2 (#1576) peak RSS 794.6 MiB x 8.4 x 1.5 = 9.78 GiB -> floor 10 GiB, job 11 GB. This rests on the single C2 run #1576 alone; no other measured peak backs it.
Residual risk, disclosed: the floor is a point-in-time check. It does not stop another job from growing after it; a post-claim OOM is NOT_DECIDABLE and ends m10.
