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
