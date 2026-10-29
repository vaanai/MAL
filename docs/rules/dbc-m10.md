NON-BINDING INDEX of the binding text. On any conflict, the cited DEC/EXP text on main controls. Built 2026-10-29 at main aa9a6364. Any edit to this file is a T1 PR (DEC-013 Am.1).

# Rules for book DBC m10, forward v1 (confirmation read; P18 B' on PASS)

Each rule is a verbatim blockquote plus its citation `(path:line)` at the main sha above. Headings and the **Governs** lines are the index's words and bind nothing. docs/HANDOFF.md and records notes are state documents, quoted only where no DEC/EXP/registration text carries the fact. **The checker proves verbatim text only, not that a clause is in force or complete.** Check: `python3 scripts/ops/rules-quote-check.py --rev origin/main docs/rules/dbc-m10.md`. No book has cleared the promotion gate; nothing here says m10 makes money. m10 is not an arena book and not a promotion-gate read: it is one confirmation read; a PASS licenses a forward paper book (P18) and nothing more.

**Source files.** The registration documents are in `docs/research/dbc-m10-forward/` (REGISTRATION.md, AMENDMENT-1.md, READ-HARDENING.md, PINS.txt). The quant-proof ruling QP-RULING-1024-read-hardening.md is a HOST FILE, not in the repo: `/data/mal/hunt-1008/push-1016/hunt29/dbc/QP-RULING-1024-read-hardening.md` (sha256 3e880055063e294d3b384a533b7e7a79974632688b7dc66b731ec1e520adebf2, as recorded in READ-HARDENING.md and the REGISTRATION addendum). It was not opened for this file; its items are quoted only through the repo copies. The same holds for QP-RULING-1024-dbc-m10-swap3.md (sha256 2a34dfc0..., R1-R14) and QP-RULING-1024-m10-lookahead.md (sha256 ca92ed6a423027e45b526832de653dd3aa5eff716d60b574117a633ce5104b32, AM1-AM6). The one QP ruling that IS in the repo is `ARTIFACTS/lab/qp-ruling-1026-p33-p18.md` (used in section 6).


## 1. Identity, registration, the frozen pins and the forward window

**Governs:** What the read is, and what its status line says.

> REGISTRATION: DBC D1 m10, registered forward confirmation read (R1-R14 of QP-RULING-1024-dbc-m10-swap3.md)

REGISTRATION title. (docs/research/dbc-m10-forward/REGISTRATION.md:1)

> CONFIRMATION of one cell on future hours, from a backfilled tape. It is NOT the promotion-gate forward-paper read and NOT a live licence. A PASS licenses starting a forward paper DBC book (own
> registration, day-1 dry score, clean clock, not in a kill-review week). A FAIL or NOT_DECIDABLE ends m10 and every D1 variant on DBC: this is the last attempt. No sentence may say m10 makes money.

REGISTRATION, What this read is. (docs/research/dbc-m10-forward/REGISTRATION.md:8)

> Status: DRAFT until the tag `dbc-m10-forward-v1` is placed on the commit that holds this file after a reviewer verdict and a QP OK on PR #868 (Swap3 pricer path). A pins table (sha256 of every code
> file, produced by `python3 docs/research/dbc-m10-forward/pins.py` at the tag commit) is appended in PINS.txt in the same commit. Registration sha256 and "Registered at" are recorded in
> SHA256SUMS.txt by the manager at tagging (`date -u`). Ruling sha256 2a34dfc0... (QP-RULING-1024-dbc-m10-swap3.md).

REGISTRATION status line as written. Stale status text; see Notes N1. (docs/research/dbc-m10-forward/REGISTRATION.md:3)

**Governs:** The window.

> Fixed 7 UTC days [2026-10-25T00:00Z, 2026-11-01T00:00Z) (168 hours). If the tag and this registration are not both done before 2026-10-25T00:00Z, the window becomes the 7 days starting at the first 00Z
> after both, stated in a dated addendum committed before that instant (the constants L.FWD_START/FWD_END, the expected names and MIN_READ_UTC are then edited in a pre-window commit and re-tagged
> -v1b; nothing else). No extension, no early stop. Fewer than 100 fills at the end = NOT_DECIDABLE, which ends m10. The read refuses before window end + 10 min (2026-11-01T00:10Z).

REGISTRATION R1. (docs/research/dbc-m10-forward/REGISTRATION.md:21)

> `[2026-10-25T00, 2026-11-01T00)` (7 UTC days, 168h; the read refuses before 2026-11-01T00:10Z, ten minutes after the window end, so the read instant is not an hour boundary)

HOLDOUT_LEDGER, Meteora DBC m10 forward window row (Hours cell). See Notes N2. (docs/HOLDOUT_LEDGER.md:70)

**Governs:** The frozen code, the tag and the pins.

> Tag `dbc-m10-forward-v1`. Pinned files and sha256: PINS.txt (dbc_price.py, dbc_screen.py, dbc_config.py, arena_stack.py, dbc_fwd_read.py, venue_backfill_dbc.py, plus decode, IDL, config map).

REGISTRATION R2. (docs/research/dbc-m10-forward/REGISTRATION.md:26)

> Read tool: `python3 -m tools.dbc_fwd_read_am1 --confirm-one-look` (default out /data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-v1-am1). Same time gate, preflight, setup-before-claim,
> marker v3 (`tools.dbc_fwd_read.MARKER`, O_CREAT|O_EXCL: once either tool claims it the other refuses), "LOOK CONSUMED" before the window guard opens, R4 config map, bad-hour rule.

AMENDMENT-1, Implementation (the read tool; supersedes the R2 read-tool line, see section 3). (docs/research/dbc-m10-forward/AMENDMENT-1.md:30)

> 4c7953b6979fc25ac464d7ea98a304b0e658727c6aae0cfd4ba58ec5aa000b8b  tools/dbc_fwd_read_am1.py
> b43e79558d997bb1852a0245d5a0d05220be57c6ea88b83548ad431f051b43f6  tools/dbc_fwd.py
> 403eae4868260f19d9990d00641885d92b5272ee9c148d1d61277e1d4b0dabdf  tools/dbc_fwd_sec3.py
> ccffd905942cb0a071f981478e27aba7b90b8e3ff5ec1b6b0ed52a89515bb183  tools/dbc_fwd_read.py
> 7db0afdd7845c0d159f73ec438632dfb2ddc7ef45ab3ef971569625e1335a7eb  tools/dbc_price.py
> 607e3173cae997b30972aef1f863c9355014bb01cee6f6622e352b42b4363b66  tools/dbc_screen.py
> a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778  tools/arena_stack.py
> 04a015e0c42548549457d4b1b96f2eccda62022dfe1a740cf73db26d9c72d2c1  tools/dbc_config.py
> 10a52a52ee382a56e13e03b3fa76c1a36cd690b414b83e2f91c8438f000f8535  tools/dbc_decode.py
> b4b67f8d203722a16b2d5238f4a0f2b38776995130cd4b5691ee649c043a0783  tools/arena_declog.py
> 913c27256dbfa11762c039957d5977a4a01fcaae5e41ac1ce4b730ef0988ca07  docs/research/dbc-p17/data/cfg_summary.json

AMENDMENT-1, AM5 pins (the 11 sha256 values; sha256 at the amendment head). (docs/research/dbc-m10-forward/AMENDMENT-1.md:44)

> dbc_price.py / dbc_screen.py / arena_stack.py are unchanged (7db0afdd / 607e3173 / a39b2ba6). dbc_fwd_read.py is unchanged (ccffd905), and so is dbc_fwd.py (b43e7955, merged in #878).

AMENDMENT-1, AM5 pins note. (docs/research/dbc-m10-forward/AMENDMENT-1.md:56)

> Both-oracle replay at the amendment head: PENDING. It needs one MiScusi job (Swap2 hours only): `python3 -m tools.dbc_fwd --replay --start 2026-10-14T00 --end 2026-10-16T04 --out <dir>`.

AMENDMENT-1, AM5 (stale text: the AM5 values are carried by `docs/research/dbc-p18-build1/equivalence-swap2-1014T00-1016T04.json`, md5_triggers 6965028e46dba86f7293f4d27284a321, md5_entries 074a827303e94e8049284f67aafe7471, per QP pass on #1079; dbc_fwd.py is unchanged, b43e7955). See Notes N10. (docs/research/dbc-m10-forward/AMENDMENT-1.md:57)

> 7185d58c2cc176f02acc0bb83899745d019317b9008a00c8ec46577beb3e2600 (sha256) is the file at tag dbc-m10-forward-v1-am1 = 7d5be9e51a39db1b8030843a273f747eb92a2967 (git blob 69268a16), and on main. tools/dbc_m10_read_ops.py pins this value for the read.

REGISTRATION addendum A2 (tools/venue_backfill.py, the am1 value, pinned by the read ops tool). (docs/research/dbc-m10-forward/REGISTRATION.md:104)

> 15605f65894282f71d39dca5e2be1ba0935d5c9676a29c4be6415ac06ea23571 (sha256) is the file at tag dbc-m10-forward-v1 = c6d4c046bcca5459909fadece040e24762a2f897 (git blob 4fcb8810). PINS.txt records it. Fetch F (#1546) runs this version.

REGISTRATION addendum A2 (the v1 value, which Fetch F runs). (docs/research/dbc-m10-forward/REGISTRATION.md:103)

> 15605f65894282f71d39dca5e2be1ba0935d5c9676a29c4be6415ac06ea23571 tools/venue_backfill.py

PINS.txt (venue_backfill.py at tag v1; see A2 above for the two values). (docs/research/dbc-m10-forward/PINS.txt:9)

> PINS.txt was written at tag dbc-m10-forward-v1 and records tools/venue_backfill.py = 15605f65.... At the am1 tag that file is 7185d58c2cc176f0...:
> #856 (5c8b355a) added one docstring line.

READ-HARDENING, Pin finding. (docs/research/dbc-m10-forward/READ-HARDENING.md:33)

**Governs:** Disclosed history (multiplicity and prior), as registered.

> - m10 came from the P14 screen on 10-14..10-16 (about 2.2 days of Swap2 era): flat +0.0212, pressure +0.0164 per attempt, 369 attempts, 12 cells, best of 12, 3 days.

REGISTRATION, History disclosed. (docs/research/dbc-m10-forward/REGISTRATION.md:12)

> #1494 (one-look holdout 10-17..10-23): NOT_DECIDABLE (tool defect, 0 trigger prints because EvtSwap3 was labelled a hook); printed "FAIL no attempts" stays on record. Those hours are SPENT for m10 and all D1 variants.

REGISTRATION, History disclosed. (docs/research/dbc-m10-forward/REGISTRATION.md:13)

> P17 screened 11 further cells (D2, D3, D4, new D6) on 10-17..10-23: all FAIL. Screened cells on this data family: 23; m10 is the only positive; the Swap3-era rehearsal below is disclosed. Registered cells for this read: ONE (m10) => k = 1, CI bound = the 5th percentile.

REGISTRATION, History disclosed. (docs/research/dbc-m10-forward/REGISTRATION.md:14)


## 2. What is counted

**Governs:** The cell, its universe, its exclusions, its pricing, strand and latency legs (REGISTRATION R5 to R9; the deciding set is replaced by AM1).

> D1 m10 exactly as docs/research/dbc-p14/IDEAS.md: BUY print with post progress p in [0.02, 0.35]; over the pool's last 60 s buys >= 10, net buy SOL >= 4.0, post spot / window-first pre-spot - 1 in
> [+5%, +40%]. Exits: sl 0.90, trail 0.90 on the running peak from the decision spot, time 600 s; cooldown 600 s per pool. Universe: SOL-quoted; migration_threshold >= 5 SOL (config value for Swap3 rows);
> 0.25 SOL buy impact <= 2% at the signal state; p <= 0.85; curve not completed; not a bad (order-uncertain) row; one warm-up hour (no signal before 2026-10-25T01:00Z); attempts whose exit window
> passes 2026-11-01T00:00Z are refused (block_edge).

REGISTRATION R5 (frozen; no flag changes it). (docs/research/dbc-m10-forward/REGISTRATION.md:46)

> EvtSwap3 is NOT a hook. Excluded: real transfer-hook pools (event/ix ...WithTransferHook), configs with transfer_fee_basis_points > 0, pools of unmapped configs.

REGISTRATION R6. (docs/research/dbc-m10-forward/REGISTRATION.md:52)

> Swap3 rows by the config-curve path (`enable_swap3_pricing`: Swap3 buy fee_on_base_token is the fee-side evidence; sell fee on the quote output; Swap2 rows by the legacy inference). Stake 0.25 SOL, guard 1.15 plus
> migration room, 2 x 160,000-lamport sends, rent 0 (rent-charged line reported), no haircut. Order fix and bad-row refusal as coded; report-only "no_bad_row_exclusion" line.

REGISTRATION R7. (docs/research/dbc-m10-forward/REGISTRATION.md:55)

> PRIMARY: full loss (stake + buy send + 2,039,280 rent). Report-only: strand at the last on-curve price.

REGISTRATION R8. (docs/research/dbc-m10-forward/REGISTRATION.md:59)

> Deciding leg 1.9 s on both legs, k = round(1.9 / slot_s), slot_s per hour from the tape (printed). The fast-0 measurement (p50 1.1-1.3 s, sum of p90 1.9-2.1 s, one quiet 10-minute window, no DBC send ever made)
> is not a reason to shorten it. REQUIRED robustness condition: flat AND pressure mean per attempt at a 2.5 s ENTRY leg (exit leg 1.9 s) > 0 (`entry_2p5s_mean_gt_0` in the checks).
> Report-only: entry 1.0 / 1.3 / 3.4 s, exit 3.4 s, 1.0 SOL stake.

REGISTRATION R9. (docs/research/dbc-m10-forward/REGISTRATION.md:62)

**Governs:** The deciding set (AMENDMENT-1, copied by it verbatim from the lookahead ruling; supersedes the as-registered screen set for the verdict).

> AM1 Deciding set: the live-policy decisions of tools/dbc_fwd.Engine, CAUSAL (no oracle):
> - fed the window tape in tape (slot) order;
> - first_ok_bt = 2026-10-25T01:00Z;
> - ss_live exactly as in dbc_fwd.replay.
> Each sealed decision is priced by the frozen pricer: dbc_screen.resolve(cell, P, [i], D, comp_slot) on the full-window load_exploration D, with i = the row of the same key
> (pool|slot|post sqrt price), then dbc_screen.price_cell.

AMENDMENT-1 AM1. (docs/research/dbc-m10-forward/AMENDMENT-1.md:8)

> AM2 Refusal of a SEALED decision. Never dropped silently; each count is printed.
> - bad_state: priced on D with bad_row zeroed (as the registered no_bad_row_exclusion line).
> - completed_before_entry and no_state: -160,000 lamports (one send).
> - block_edge and bad_hour: excluded (window artifacts).
> - key not found in D: full loss (stake + send + 2,039,280 rent). If more than 1% of decisions are not found: NOT_DECIDABLE.

AMENDMENT-1 AM2. (docs/research/dbc-m10-forward/AMENDMENT-1.md:14)

> AM4 The as-registered screen set is printed REPORT-ONLY, with the overlap counts and per-model means of the screen-only and causal-only entries. It never decides; "PASS if either" is forbidden.

AMENDMENT-1 AM4. (docs/research/dbc-m10-forward/AMENDMENT-1.md:20)

**Governs:** Data and config map.

> Helius backfill by tools/venue_backfill_dbc.py into the FRESH dir, ONLY after each UTC day closes (day D fetched on D+1 from 00:30Z), as MiScusi jobs (POSIX sh, flock slot, `--credit-cap` set):
> `python3 tools/venue_backfill_dbc.py run --start <D>T00 --end <D+1>T00 --out-dir /data2/mal/venue-tape-backfill/fwdv1 --credit-cap 500000`  (7 jobs; D = 2026-10-25 ... 2026-10-31).
> Write-only: no smoke, no count and no open of a window hour before the read; per-hour completeness markers are the only check (`backfill_hour_end`, no gap row); directory listings only. The manager
> records each job id and its counted credits (about 14,050 per hour, 0.34M per day, 2.4M for the window; Helius autoscaling credits, reported per CLAUDE.md). More than 6 incomplete hours = NOT_DECIDABLE
> (refetch of an incomplete hour is allowed only before the read, by the same tool, no content inspection).

REGISTRATION R3 (the fetch command and cap: see READ-HARDENING item 5 in section 3 and Notes N3). (docs/research/dbc-m10-forward/REGISTRATION.md:34)

> Base map = docs/research/dbc-p17/data/cfg_summary.json (7,399 configs, fetched by tools/dbc_cfg_fetch.py, job #1504). The read tool, after the marker claim and BEFORE the first load, scans only the
> `config` and quote-mint strings of window trade lines, fetches every config not in the map with getMultipleAccounts (sorted keys, 100 per call, finalized; deterministic, no content-based choice),
> writes cfg_map_final.json to the output dir. Pools whose config cannot be decoded are excluded and counted; more than 5% of window SOL-quoted rows in unmapped configs = NOT_DECIDABLE.

REGISTRATION R4. (docs/research/dbc-m10-forward/REGISTRATION.md:41)

> m10 is the ONLY registered cell on this window (k = 1, 5th percentile). The 23 earlier screens are disclosed above. If another cell is ever registered on the same window, every registered cell's CI bound
> moves to the (5/k)th percentile BEFORE the look.

REGISTRATION R12. (docs/research/dbc-m10-forward/REGISTRATION.md:75)

> Commit and file shas; hours present; attempts, fills, guard-unfilled, strands, refusals by reason; per-day n and SOL under both models; mean, total, ex-top-3, ex-best-day, CI90 lo (trade; date-clustered info);
> fee paid buy/sell/round trip bps; slot_s per hour; Swap3 share, hook / transfer-fee / unmapped-config excluded share per day; attempts per day; config-map report; sensitivities (report only).
> Output: fwd-m10.json + fwd-m10.md (sha256 in the md).

REGISTRATION R13 (the output file name is fwd-m10-am1.json under the am1 tool, see Notes N4). (docs/research/dbc-m10-forward/REGISTRATION.md:79)


## 3. The ONE read: job #1804, Option A, confirmation only

**Governs:** One look, and what ends m10.

> The look is used at the marker claim: the tool prints "LOOK CONSUMED" immediately after claiming the marker, before any window file is opened. Any later failure (crash, missing key at fetch time, undecodable data,
> anything) is NOT_DECIDABLE and ends m10. A FAIL or NOT_DECIDABLE ends m10 and every D1 variant on DBC. No re-tuning, no other D1 variant on these hours, no second read, no smoke on window hours.

REGISTRATION R14. (docs/research/dbc-m10-forward/REGISTRATION.md:84)

**Governs:** The read tool (supersedes the R2 read-tool line).

> "R2's read tool is tools/dbc_fwd_read_am1.py at tag dbc-m10-forward-v1-am1. tools/dbc_fwd_read.py is retained only as an imported library. Any invocation of it as a program or of its run() against FWD_ROOT is forbidden; a marker v3 claim by it ends m10 (AM6 class, licenses nothing)."

REGISTRATION addendum A1 (the QP item 2c text, verbatim). (docs/research/dbc-m10-forward/REGISTRATION.md:99)

> This supersedes the "Read tool" line of R2 above (`python3 tools/dbc_fwd_read.py --confirm-one-look`, output dir fwd-read-v1).

REGISTRATION addendum A1 (supersedes note). (docs/research/dbc-m10-forward/REGISTRATION.md:100)

> Read tool: `python3 tools/dbc_fwd_read.py --confirm-one-look` (tools/dbc_fwd_read.py). Marker `/data/mal/hunt-1008/push-1016/hunt29/dbc/HOLDOUT_READ_DONE_v3` (atomic O_EXCL, outside --out),
> preflight (all 168 hour NAMES under the fresh root, newest mtime >= 10 min), output dir `/data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-v1` (must be empty or absent).

REGISTRATION R2, the line that A1 supersedes (kept so the output-dir text is visible; NOT in force as a command). See Notes N4. (docs/research/dbc-m10-forward/REGISTRATION.md:27)

> Data guard: tools.dbc_price.enable_forward_window accepts only [2026-10-25T00, 2026-11-01T00) directly under /data2/mal/venue-tape-backfill/fwdv1/meteora_dbc; every earlier hour is refused.

REGISTRATION R2. (docs/research/dbc-m10-forward/REGISTRATION.md:29)

**Governs:** The old-tool lock (item 2a), the credit cap (item 5), job specs (as recorded in the repo copy of the ruling).

> Path: `/data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-v1/README-OLD-TOOL-DISABLED.txt`. Exact content (one line, then a newline):
> ```
> R2 read tool is tools/dbc_fwd_read_am1.py (tag dbc-m10-forward-v1-am1). tools/dbc_fwd_read.py must never run; see QP-RULING-1024-read-hardening.md
> ```
> Then run `chmod a-w` on the file, and then on the directory. The read job's guards check that the README exists, that it names the am1 tool, and that the file and the directory have no write bit (exit 12 otherwise).

READ-HARDENING item 2a (repo copy; the ruling is a host file). (docs/research/dbc-m10-forward/READ-HARDENING.md:9)

> Declared <date -u>: the fwdv1 fetch (out dir /data2/mal/venue-tape-backfill/fwdv1) runs with ONE fixed cumulative cap, `--credit-cap 3700000` (1.5 x the 2.465M of the Swap3 holdout week; QP minimum 3.7M).

READ-HARDENING item 5 credit cap declaration. See Notes N3 (relation to the per-day `--credit-cap 500000` of R3). (docs/research/dbc-m10-forward/READ-HARDENING.md:23)

> Procedure G has a separate cumulative budget of 300,000 credits over all passes. Neither is adjusted day by day against the meter. If the cap binds (rc 5), the missing names make the read refuse before the claim (not a look).

READ-HARDENING item 5. (docs/research/dbc-m10-forward/READ-HARDENING.md:24)

> READ (item 1): `sh /data2/mal/wt/dbc-read-ops/scripts/research/dbc-m10-read-job.sh A <GiB>` (Option A needs a reviewer verdict and a QP OK on this PR by 2026-11-01T00:00Z; otherwise use `B`).
> after: F and the final G pass (if any). Time limit 10 h (>= 8 h; the job also waits for 00:10Z and for the 900 s mtime). resumable false.

READ-HARDENING, Job specs (READ). (docs/research/dbc-m10-forward/READ-HARDENING.md:58)

**Governs:** The read job as submitted: #1804, its gate, its pins, memory and exit codes.

> | #1804 | READ, Option A: `sh scripts/research/dbc-m10-read-job.sh A 10` | F #1546 and #1803 | 11 GB, 2 CPU, 600 min (10 h), priority urgent |

REGISTRATION addendum A4 (job table row). (docs/research/dbc-m10-forward/REGISTRATION.md:126)

> READ #1804 runs at git_ref 304bb9cd (wrapper sha256 282c9d4a, launcher 04c98b97, ops 23776c67; the same on main). Its own time gate holds until 2026-11-01T00:10:00Z (1793491800), then pins (exit 10), memory (exit 13),
> completeness with up to 1 h wait for the 900 s mtime age (exit 11), guards (exit 12), then the Option A launcher. Every wrapper exit and every tool rc 2/3/5/6/7 is pre-claim, not a look, and goes to READ-ATTEMPTS.log.
> Once "LOOK CONSUMED" prints, anything other than rc 0 with fwd-m10-am1.json written is NOT_DECIDABLE and ends m10 and every D1 variant on DBC (item 1).

REGISTRATION addendum A4. (docs/research/dbc-m10-forward/REGISTRATION.md:139)

> # One MiScusi job on mal-research-0, resumable false, time limit >= 8 h, MemoryMax from item 6, after: the fetch job that writes day 2026-10-31 AND the final gap-refetch
> # pass G (if any). Exit codes of this wrapper (all PRE-CLAIM, not a look; each is appended to READ-ATTEMPTS.log; resubmit later):
> #   10 pins (tag worktree HEAD / clean / sha256, or a launcher/ops file sha256)   11 completeness (names, .part, mtime >= 900 s, writer running)
> #   12 guards (old-tool README, am1 out dir, old-tool command text, bad mode, test override after 2026-10-31)          13 memory
> # Otherwise it exits with the read tool's rc. Only the tool's printed "LOOK CONSUMED" line marks the look as used (item 1).

Job script header (code text, not a rule; shows the exit-code classes). (scripts/research/dbc-m10-read-job.sh:7)

> Item 1 says "On research-0, check free memory first. No other heavy job on the box." The second sentence cannot hold literally: the HV4 runner #1561 (to 2026-11-07T12:05Z) and the HV6 regime-block producer #1748
> (from 2026-10-28T00:15Z, 14 days) run through the read instant, alongside smaller long-running jobs. Heavy queued jobs on research-0: #1775 (HV8 offline dump, 24 GB) and #1767 (P91 build, 16 GB), both waiting for room and able to start near the read,
> and #1562 (HV4 runner seg2, after #1561, so not before 2026-11-07T12:05Z). Manager rule: no new heavy research-0 job from 2026-10-31T22:00Z until the read ends; #1775 and #1767 are held or deprioritised if they have not finished by then.
> The check applied is the wrapper's MemAvailable floor: step 13, `dbc_m10_read_ops.py mem --min-gb 10`, run after the 00:10Z time gate and the pins and before the claim; MemAvailable < 10 GiB gives exit 13
> (pre-claim, not a look). MiScusi also starts #1804 only when it can reserve 11 GB for it. The other jobs' memory is capped by their own MiScusi limits, and that capped coexistence is accepted in place of
> "no other heavy job".
> Sizing (manager's chain notes, not re-derived here): C2 (#1576) peak RSS 794.6 MiB x 8.4 x 1.5 = 9.78 GiB -> floor 10 GiB, job 11 GB. This rests on the single C2 run #1576 alone; no other measured peak backs it.
> Residual risk, disclosed: the floor is a point-in-time check. It does not stop another job from growing after it; a post-claim OOM is NOT_DECIDABLE and ends m10.

REGISTRATION addendum A5 (memory). (docs/research/dbc-m10-forward/REGISTRATION.md:144)

**Governs:** The G passes and the writer checks.

> - Bounded wait, every G pass (1-5): check for a `venue_backfill_dbc` process every 5 min and proceed after 2 consecutive no-writer checks. After 20 checks (100 min) without that, print
> "G pass N skipped: writer still running after 100 min" and exit 0. This leaves at least 80 min of the 180 min limit for the pass. Then

REGISTRATION addendum A4. (docs/research/dbc-m10-forward/REGISTRATION.md:131)

> `python3 /data2/mal/wt/dbc-read-ops/tools/dbc_m10_gapfix.py pass --pass N --wt /data2/mal/wt/dbc-m10-read-am1 --budget 300000`.

REGISTRATION addendum A4. (docs/research/dbc-m10-forward/REGISTRATION.md:133)

> - DISCLOSURE, rc mapping: every G pass maps the tool's rc 1 (some hours still gap) and rc 5 (G budget spent) to job exit 0. A skip also exits 0. So the job state does not show these outcomes,
> and the READ still starts.

REGISTRATION addendum A4. (docs/research/dbc-m10-forward/REGISTRATION.md:134)

> rc 10 (pins), 12 (refused) and any other rc fail only that pass. A failure of G pass 5 (#1803) means #1804 does not start (pre-claim, not a look): fix it, then resubmit the pass and the READ.

REGISTRATION addendum A4. (docs/research/dbc-m10-forward/REGISTRATION.md:136)

> - Gates: `T=$(date -u -d '<instant>' +%s); while [ "$(date -u +%s)" -lt "$T" ]; do sleep 60; done; date -u`. The gates are chained only to each other, and a gate cannot fail
> short of its time limit: each one sleeps at most about 24 h of its 25 h limit. The G passes are independent of each other.

REGISTRATION addendum A4. (docs/research/dbc-m10-forward/REGISTRATION.md:129)

> - Unpinned G worktree, disclosed: the G script runs from /data2/mal/wt/dbc-read-ops (HEAD 304bb9cd81727a7453a216dee055344522a4d387, `git status --porcelain` empty at 2026-10-27T21:51:12Z). Nothing pins that copy
> of dbc_m10_gapfix.py, which itself pin-checks only the tag worktree it fetches from. The worktree must stay untouched (no checkout, pull, edit or untracked file) until the read ends.

REGISTRATION addendum A4. (docs/research/dbc-m10-forward/REGISTRATION.md:137)

> G pass N (only when `python3 /data2/mal/wt/dbc-read-ops/tools/dbc_m10_gapfix.py identify` lists labels; only after a day's fetch has ended):
> `python3 /data2/mal/wt/dbc-read-ops/tools/dbc_m10_gapfix.py pass --pass N --wt /data2/mal/wt/dbc-m10-read-am1 --budget 300000`. Time limit 3 h, MemoryMax 4G, resumable false.
> It refuses while any venue_backfill_dbc process runs. The last pass must end before the read starts.

READ-HARDENING, Job specs (G). (docs/research/dbc-m10-forward/READ-HARDENING.md:49)


## 4. The read rule and its verdicts

**Governs:** PASS (REGISTRATION R11) and the R9 robustness condition; AM3 applies them to the AM1 deciding set.

> fills >= 100; >= 5 UTC days with attempts and strictly more than half of those days with total > 0; trade-level CI90 lower bound of mean SOL per attempt > 0 (tools.arena_stack.boot_lo, 1,000 draws, seed 1,
> 5th percentile, over all attempts incl. guard-unfilled); total after removing the top 3 trades > 0; total after removing the best day > 0; mean > 0; the R9 2.5 s condition. Implemented in
> tools/dbc_fwd_read.py `verdict()` (tests: tools/test_dbc_fwd_read.py).

REGISTRATION R11. (docs/research/dbc-m10-forward/REGISTRATION.md:70)

> Flat 15%, and pressure at slope scale 1 with the intercept refit on the cell's fills to mean p 0.289 (tools.arena_stack.fail_legs). Pump-fit, unvalidated on DBC. Both decide.

REGISTRATION R10. (docs/research/dbc-m10-forward/REGISTRATION.md:67)

> AM3 R11 and the R9 2.5 s condition are applied to this set, under BOTH fail models. Every other part of R1-R14 is unchanged: window, data, config map, k = 1, one look, marker v3.

AMENDMENT-1 AM3. (docs/research/dbc-m10-forward/AMENDMENT-1.md:19)

> REQUIRED robustness condition: flat AND pressure mean per attempt at a 2.5 s ENTRY leg (exit leg 1.9 s) > 0 (`entry_2p5s_mean_gt_0` in the checks).

REGISTRATION R9. (docs/research/dbc-m10-forward/REGISTRATION.md:63)

**Governs:** NOT_DECIDABLE, and what a FAIL ends.

> A FAIL or NOT_DECIDABLE ends m10 and every D1 variant on DBC: this is the last attempt. No sentence may say m10 makes money.

REGISTRATION, What this read is. (docs/research/dbc-m10-forward/REGISTRATION.md:9)

> Fewer than 100 fills at the end = NOT_DECIDABLE, which ends m10. The read refuses before window end + 10 min (2026-11-01T00:10Z).

REGISTRATION R1. (docs/research/dbc-m10-forward/REGISTRATION.md:23)

> More than 6 incomplete hours = NOT_DECIDABLE
> (refetch of an incomplete hour is allowed only before the read, by the same tool, no content inspection).

REGISTRATION R3. (docs/research/dbc-m10-forward/REGISTRATION.md:37)

> AM6 Deadline: the tag should be placed before 2026-10-25T00:00Z. It is admissible up to 2026-11-01T00:00Z ONLY if ALL of these hold at the tag instant:
> - no window hour has been opened, counted or smoked (R3 listings and completeness markers only);
> - no DBC decision log, live-feed output or credit meter beyond the R3 counts has been produced or read for any instant >= 10-25T00Z;
> - the amendment text equals AM1-AM5.
> Otherwise the read runs unamended. Its PASS is then recorded as PASS-SCREEN-LOOKAHEAD, which licenses nothing and ends m10 (NOT_DECIDABLE by design for licensing).

AMENDMENT-1 AM6. See Notes N5. (docs/research/dbc-m10-forward/AMENDMENT-1.md:23)

> Fewer than 100 fills, more than 6 incomplete hours, or more than 5% of window SOL-quoted rows in unmapped configs, among others (R1, R3, R14; R14 covers a failed one-look), is NOT_DECIDABLE and ends m10 and every D1 variant on DBC.

HOLDOUT_LEDGER, forward window row. (docs/HOLDOUT_LEDGER.md:70)

**Governs:** What the read licenses.

> A PASS licenses starting a forward paper DBC book (own
> registration, day-1 dry score, clean clock, not in a kill-review week).

REGISTRATION, What this read is. (docs/research/dbc-m10-forward/REGISTRATION.md:8)

> Forward paper DBC book passing the gate under both fail models plus owner approval; a DBC executor (does not exist); md5 decision-equivalence replay proof; trigger and send on mal-fast-0 with a processed
> feed; stop if signal-to-landing p90 > 2.5 s; size stays 0.25 SOL.

REGISTRATION, Before any live canary (not part of this read). (docs/research/dbc-m10-forward/REGISTRATION.md:89)


## 5. The read-hardening ruling items (QP-RULING-1024-read-hardening)

**Governs:** The ruling is the host file named at the top; the items below are quoted from the repo copies (READ-HARDENING.md and the REGISTRATION addendum). Item 1 (the read job, memory), 2 (old tool disabled), 2a (README), 2c (addendum), 3 (Option A launcher), 4 (procedure G), 5 (credit cap), 6 (the C1 and C2 checks).

> Ruling: /data/mal/hunt-1008/push-1016/hunt29/dbc/QP-RULING-1024-read-hardening.md, sha256 3e880055063e294d3b384a533b7e7a79974632688b7dc66b731ec1e520adebf2.

READ-HARDENING header. (docs/research/dbc-m10-forward/READ-HARDENING.md:3)

> Gate status is unchanged. m10 has NOT cleared the promotion gate. The read is a CONFIRMATION only.

READ-HARDENING header. (docs/research/dbc-m10-forward/READ-HARDENING.md:5)

> Option A needs a reviewer verdict and a QP OK on this PR by 2026-11-01T00:00Z; otherwise use `B`

READ-HARDENING, Job specs (READ). See Notes N6. (docs/research/dbc-m10-forward/READ-HARDENING.md:58)

> Chain section A4 and memory section A5 updated 2026-10-27T21:51:12Z (date -u) after QP's OK at 36f203ad (comment 6454456703) and the chain resubmission.

REGISTRATION addendum header (QP OK on PR #1068, the records addendum; NOT the Option A OK, see the note after READ-HARDENING job specs below). (docs/research/dbc-m10-forward/REGISTRATION.md:94)

> Required by QP-RULING-1024-read-hardening.md item 2c (sha256 3e880055063e294d3b384a533b7e7a79974632688b7dc66b731ec1e520adebf2) and by the QP verdict on PR #890 (head 86b0e3d1, 2026-10-24T18:23Z).

REGISTRATION addendum header. (docs/research/dbc-m10-forward/REGISTRATION.md:93)

> Records only: no R or AM term, tagged file, constant or window changes. Nothing under fwdv1/ or the read output dir was opened or listed for this addendum. m10 has NOT cleared the promotion gate;
> this read is CONFIRMATION only, and a PASS licenses a forward paper book (P18), nothing more.

REGISTRATION addendum header. (docs/research/dbc-m10-forward/REGISTRATION.md:95)

> C1 decode-only check (item 6, SPENT hours):
> `python3 /data2/mal/wt/dbc-read-ops/tools/dbc_m10_decode_check.py --root /data2/mal/venue-tape-backfill/meteora_dbc_holdout --start 2026-10-17T00 --end 2026-10-24T00 --out /data/mal/hunt-1008/push-1016/hunt29/dbc/decode-check-1017-1024`
> Time limit 4 h, MemoryMax 2G, resumable false. The root is the #1494 holdout root (L.HOLDOUT_ROOT). Confirm it before you submit.

READ-HARDENING, Job specs (C1). (docs/research/dbc-m10-forward/READ-HARDENING.md:52)

> C2 engine-equivalence check (item 6, in-sample [2026-10-16T04, 2026-10-17T00)):
> `PYTHONDONTWRITEBYTECODE=1 python3 /data2/mal/wt/dbc-read-ops/tools/dbc_m10_engine_equiv.py --tag-root /data2/mal/wt/dbc-m10-read-am1 --out /data/mal/hunt-1008/push-1016/hunt29/dbc/sw3-engine-equiv`
> Time limit 2 h, MemoryMax 24G, resumable false. Exit codes: 0 PASS, 2 UNINFORMATIVE, 1 FAIL (the read must not run as tagged), 3 refused. The output records peak RSS and wall time.

READ-HARDENING, Job specs (C2). (docs/research/dbc-m10-forward/READ-HARDENING.md:55)

> Time limit 2 h, MemoryMax 24G, resumable false. Exit codes: 0 PASS, 2 UNINFORMATIVE, 1 FAIL (the read must not run as tagged),

READ-HARDENING, Job specs (C2): the rule that a C2 FAIL blocks the read as tagged. (docs/research/dbc-m10-forward/READ-HARDENING.md:57)

**Host record, not in the repo (not a blockquote, not checked by the checker):** C2 engine-equivalence #1576. Recorded by the manager in the MiScusi notebook entry "DBC m10 C2 engine-equivalence #1576 = PASS" and not opened for this file. Facts as the manager gave them: job #1576 exitCode 0; `/data/mal/hunt-1008/push-1016/hunt29/dbc/sw3-engine-equiv/equivalence-sw3.json`, sha256 ed87d3cb4d28ec5f866c9513805dc3da470b2ad51151036d3f7d0cfe1c9d25fc; verdict PASS; window [2026-10-16T04, 2026-10-17T00); tag_commit 7d5be9e5..., equal to the `dbc-m10-forward-v1-am1` tag; taken_utc 2026-10-25T12:29:20Z. Per the READ-HARDENING:57 rule quoted above, a PASS means the precondition for the read to run as tagged is met. The only other repo mention of #1576 is its peak RSS (REGISTRATION.md:150).

> Tests: `python3 -m tools.test_dbc_m10_read_hardening` (synthetic only).

READ-HARDENING Files. NEVER RUN (seal; see section 7). (docs/research/dbc-m10-forward/READ-HARDENING.md:44)

**Option A conditions met (QP pass on #1079, comment 6501961906; correction of the first draft).** READ-HARDENING:58 asks for a reviewer verdict and a QP OK on PR #890 by 2026-11-01T00:00Z. Both exist on #890 at head 86b0e3d1: QP "QP OK, with conditions" (comment 6387880346, 2026-10-24T18:23:16Z) and reviewer "APPROVE" (comment 6397758841, 2026-10-25T12:40:43Z). #890 merged 2026-10-24T18:24:00Z, merge commit 304bb9cd, which is #1804's git_ref. The comment 6454456703 quoted above is the QP OK on PR #1068, not the Option A OK. Source: PR comments, not opened for this file.

**Binding lines that are only in the host ruling (not in either repo copy; not a blockquote; not checked).** Source: `/data/mal/hunt-1008/push-1016/hunt29/dbc/QP-RULING-1024-read-hardening.md` (sha256 3e880055063e294d3b384a533b7e7a79974632688b7dc66b731ec1e520adebf2), as quoted in the QP pass `/data/mal/hunt-1008/ops/reports/2026-10-29/pr1079-qp.md`. Ruling (1): `rc 4 (marker exists): not a new look. The earlier claim governs.` Ruling (2)d: `No agent, cron or job other than the read job may launch either module.` Ruling (6), last bullet: `A program-upgrade lookup (getAccountInfo on the DBC ProgramData, last deploy slot) is allowed pre-claim, REPORT-ONLY. It may not cancel or delay the read. No discretionary withdrawal on window information.` Ruling (6): `On a FAIL the read must not run as tagged.` and `The AM5 equivalence (#878) covers only [10-14T00, 10-16T04)`. Ruling (4)5 and (4)4 (G limits): `At most 3 passes per hour, at least 30 min apart.` and `Never replace a non-gap hour.` These stay unquoted at line level until the ruling is copied to ARTIFACTS/lab/ (Notes N11).

**Disclosed residual risks of the #890 QP OK (PR #890 comment 6387880346, state text, UNCHECKED):** a None RPC result in the R4 fetch raises TypeError, which is not retried; a leftover uncompressed tape-H.jsonl name is not refused by the completeness check. Both are post-claim NOT_DECIDABLE routes.

**Drift (C6).** Ruling (2)c says to add the A1 line to docs/HANDOFF.md; A1 (REGISTRATION.md:100) says it was in HANDOFF section 6 at 37bafde4. The current docs/HANDOFF.md has no such line; HOLDOUT_LEDGER.md:70 carries it, and the README guard and wrapper exit 12 enforce it mechanically.


## 6. What happens on PASS: P18 B' (DEC-031 Amendment 20)

**Governs:** Status and scope of the amendment. The heading on main still says DRAFT; the merge is recorded in Notes N7.

> ### Amendment 20 (DRAFT; number = the next free DEC-031 amendment at merge: Amendment 16 is held by open PR #1019,

DEC-031 Am.20 heading. (DEC/DEC-031-forward-paper-arena.md:814)

> DEC-031 Amendment 20: **B'**, one declared departure from Amendment 3 item 1 and Amendment 4 item 2.1 (the P0 source) for the DBC P18 forward paper book, the copy of m10 specified in PR #950 `docs/research/p33-dbc-p18/STAGING.md` §1 (head `2ac6fca00ad0dc36f9cfec7b36f7fd1e5e58e6cc`). Under B', P18's P0 is the DBC m10 confirmation read's own R13 per-UTC-day attempt counts, not 3 new closed days of the live process (Plan B). That moves P18's earliest clock from 2026-11-06T00:00Z (Plan B) to 2026-11-04T00:00Z.

DEC-031 Am.20 opening. (DEC/DEC-031-forward-paper-arena.md:815)

> **Nothing is registered by this amendment:** no `bot_id`, `bots.json` line, N increment, P0 run or registration record (ruling Q1 item 4). No book has cleared the promotion gate. m10 has 0 out-of-sample trades and P18 has none. Nothing here is a profit claim.

DEC-031 Am.20 opening. (DEC/DEC-031-forward-paper-arena.md:815)

> - **Takes effect on merge**, after quant-proof's OK on the exact final head and a reviewer verdict on the PR. The merge instant is the committer time of the merge commit on origin/main (Amendment 2 item 2).

DEC-031 Am.20. (DEC/DEC-031-forward-paper-arena.md:816)

> - **Deadline (ruling Q2(a)).** B' is available only if the merge instant is before **2026-11-01T00:10:00Z**, the earliest instant at which the read can print LOOK CONSUMED (REGISTRATION R1 and R14; the read job waits for 00:10Z). This is stricter than "before LOOK CONSUMED" on purpose: it can be checked from the merge commit alone, with no job-log timestamp. Target: merged by 2026-10-31T00:00Z. If the merge instant is later, this amendment has no effect and Plan B applies (item 4).

DEC-031 Am.20. (DEC/DEC-031-forward-paper-arena.md:817)

**Governs:** Item 1: applies only on a PASS.

> 1. **Applies only on a PASS.** B' applies only if the registered read (`python3 -m tools.dbc_fwd_read_am1 --confirm-one-look` at tag `dbc-m10-forward-v1-am1`, run by `scripts/research/dbc-m10-read-job.sh`) writes `verdict` PASS in `fwd-m10-am1.json`, and the manager has recorded that verdict in a notebook entry. On FAIL, NOT_DECIDABLE, PASS-SCREEN-LOOKAHEAD, any other verdict or no verdict, **nothing is registered**: no P0 is taken from the read, the P18 process stops at once, and Plan B does not apply either (STAGING §0; ruling Q1, "After the read"). N does not change.

DEC-031 Am.20 item 1. (DEC/DEC-031-forward-paper-arena.md:819)

**Governs:** Item 2: the P0 source (the departure).

>    1. **The field.** The key path `deciding.attempts_by_day` of `/data/mal/hunt-1008/push-1016/hunt29/dbc/fwd-read-v1-am1/fwd-m10-am1.json`: R13's "attempts per day" of the AM1 deciding set. In code (`tools/dbc_fwd_read_am1.py` line 163 at main `a31d9864`) it counts every sealed causal decision of `tools/dbc_fwd.Engine` (the list `c1`, appended at trigger time and never removed) by `tools/dbc_screen.day_of` of its block time. The line runs after pricing in code order, but its value comes from the sealed decision list alone and holds no priced value. Only this field gives the counts. **Not used:** `per_day_n`, `per_day` or any other field under `primary_1p9s`, `entry_2p5s` or `screen_report_only`. Those are priced lines and sit next to SOL figures. `fwd-m10-am1.md` does not print `attempts_by_day`, so the JSON is the source.

DEC-031 Am.20 item 2.1. (DEC/DEC-031-forward-paper-arena.md:821)

>    2. **When and how.** The field is copied only after LOOK CONSUMED and after the verdict is recorded (item 1). It is copied verbatim into the registration notebook entry and the records PR by a key-path extraction (for example `jq -c '.deciding.attempts_by_day'`), never by hand. The same entry cites the JSON's sha256 as printed in the last line of `fwd-m10-am1.md`.

DEC-031 Am.20 item 2.2. (DEC/DEC-031-forward-paper-arena.md:822)

>    3. **Days.** Exactly the 7 UTC dates 2026-10-25 to 2026-10-31. A date missing from the field counts as 0. A key outside those 7 dates makes B' NOT_DECIDABLE (item 4).

DEC-031 Am.20 item 2.3. (DEC/DEC-031-forward-paper-arena.md:823)

>    4. **Fixed first.** The rule text is items 2 to 5 of this amendment, merged before the look. The registration notebook entry cites it by merge commit before the counts are copied into it.

DEC-031 Am.20 item 2.4. (DEC/DEC-031-forward-paper-arena.md:824)

**Governs:** Item 3: floor, bar and gap rule.

>    1. **Floor (Amendment 4 item 2.1, unchanged):** a mean of at least 2.0 would-be attempts a day, and no day at 0.

DEC-031 Am.20 item 3.1. (DEC/DEC-031-forward-paper-arena.md:826)

> B' passes only if the mean of the 7 day counts is **at least 20** and **each of the 7 days is at least 10**. That bar implies the floor.

DEC-031 Am.20 item 3.2. (DEC/DEC-031-forward-paper-arena.md:827)

> B' passes only if the read's own `hours_present` equals `hours_needed` (168) and `bad_hours` is empty. Any missing or incomplete window hour makes B' NOT_DECIDABLE.

DEC-031 Am.20 item 3.3. (DEC/DEC-031-forward-paper-arena.md:828)

>    4. **Day 1 is a lower bound.** R5's warm-up hour (`FIRST_OK` 2026-10-25T01:00Z) gives 2026-10-25 23 eligible hours, so its count is a lower bound for a full day. That is the conservative direction for an "at least" bar. It is not adjusted.

DEC-031 Am.20 item 3.4. (DEC/DEC-031-forward-paper-arena.md:829)

**Governs:** Item 4: below the bar, or a gap.

> 4. **Below the bar, or a gap: NOT_DECIDABLE for B', not a drop.** P18 then goes to Plan B: P0 = the live process's own would-be attempts on 3 full closed UTC days at the registration-candidate sha (Amendment 4 item 2.1, its floor and gap rule; the Amendment 5 live-P0 route), earliest clock 2026-11-06T00:00Z. There is no re-count, no other field, no other window and no second use of the read's output for P0. Plan B applies the same way if this amendment misses its deadline, and if the B' clock would fall after 2026-11-06T00:00:00Z (item 6, expiry).

DEC-031 Am.20 item 4. (DEC/DEC-031-forward-paper-arena.md:830)

**Governs:** Item 5: horizon. Item 6: clock and expiry, and the pre-clock items (a) to (g).

> 5. **Horizon (ruling Q2(d)).** `rate` = the sum of the 7 day counts divided by 7, exact. `H = min(90, max(14, ceil(150 / rate)))` (Amendment 4 item 2.2).

DEC-031 Am.20 item 5. (DEC/DEC-031-forward-paper-arena.md:831)

> At the earliest clock the window is [2026-11-04T00:00Z, 2026-11-18T00:00Z).

DEC-031 Am.20 item 5. (DEC/DEC-031-forward-paper-arena.md:831)

> 6. **Clock.** **2026-11-04T00:00:00Z (`clock_start_ms` 1793750400000)** if every pre-clock item below lands in time. Amendment 3 item 1 makes the clock the first 00:00Z after the registration record (the `bots.json` line and its notebook copy), so the record must be in before 2026-11-04T00:00Z. **If any item is late, the clock is the next 00:00Z after the last item.** Nothing is cut to keep a date. B' waives no item.

DEC-031 Am.20 item 6. (DEC/DEC-031-forward-paper-arena.md:832)

>    - **Expiry.** B' applies only if the clock is at or before **2026-11-06T00:00:00Z (1793923200000)**, Plan B's earliest clock. If any pre-clock item would put it later, B' lapses mechanically and item 4 (Plan B, live P0) applies. Nobody chooses.

DEC-031 Am.20 item 6 (Expiry). (DEC/DEC-031-forward-paper-arena.md:833)

>    - (a) This amendment merged before the deadline above.

DEC-031 Am.20 item 6 (a). (DEC/DEC-031-forward-paper-arena.md:834)

>    - (b) The read's PASS recorded; the field copied; the bar and the gap rule met (items 1 to 3).

DEC-031 Am.20 item 6 (b). (DEC/DEC-031-forward-paper-arena.md:835)

>    - (c) The live P18 process at the registration-candidate sha started after the read printed LOOK CONSUMED (ruling Q1 item 3), early enough that 2026-11-02 is a full closed UTC day for it (in practice before 2026-11-01T23:00Z, with one warm-up hour).

DEC-031 Am.20 item 6 (c). (DEC/DEC-031-forward-paper-arena.md:836)

>    - (d) The day-1 dry score of that live process on 2026-11-02 passes every item, with the bars fixed before the process starts: STAGING §4 items 1 to 5, ruling Q4.3 (item 6, L90 <= 2.5 s) and ruling Q6 (item 7, RSS; item 8, credits). That includes item 4, the live-vs-backfill cross-check after 2026-11-02's backfill, which is the only link between the proxy and the process (ruling Q2(e)). One process per sha. A sha change after the dry score needs a new dry score.

DEC-031 Am.20 item 6 (d). (DEC/DEC-031-forward-paper-arena.md:837)

>    - (e) B1 to B3 merged, each with its own QP OK and reviewer verdict (ruling, "Conditions for this OK"), with the registration pack: frozen sha and sha256 list, the A3-style guard of DESIGN §3 item 4, the parity statement and the multiplicity text of item 7.

DEC-031 Am.20 item 6 (e). (DEC/DEC-031-forward-paper-arena.md:838)

>    - (f) A kill-review week check line in the registration notebook entry (ruling Q6). A kill-review week stops the registration.

DEC-031 Am.20 item 6 (f). (DEC/DEC-031-forward-paper-arena.md:839)

>    - (g) The `bots.json` line and its notebook copy, with H pinned.

DEC-031 Am.20 item 6 (g). (DEC/DEC-031-forward-paper-arena.md:840)

**Governs:** Item 7 multiplicity; item 8 the read is not changed; item 10 unchanged.

> 7. **Multiplicity.** **N + 1** at P18's clock (Amendment 4 item 2.5). The registration and every P18 read state: m10 is the best of 23 screened DBC cells; the Swap3-era rehearsal day (2026-10-16) came out at about one-fifth of the Swap2-era mean; the receive-time differences of STAGING §1; and that P0 came from the read under this departure.

DEC-031 Am.20 item 7. (DEC/DEC-031-forward-paper-arena.md:841)

> The read's own trades never count toward P18's gate: P18 counts only attempts decided at or after its `clock_start_ms` (§1.2).

DEC-031 Am.20 item 7. (DEC/DEC-031-forward-paper-arena.md:841)

> 8. **The read is not changed in any way.** REGISTRATION R1 to R14, AMENDMENT-1 AM1 to AM6, READ-HARDENING, the tag `dbc-m10-forward-v1-am1`, every pin (the 12 paths of ruling Q1 item 2, `tools/dbc_m10_read_ops.py` and `scripts/research/dbc-m10-read-job.sh`), the marker v3, the output directory, the verdict function and every bar stay as they are.

DEC-031 Am.20 item 8. (DEC/DEC-031-forward-paper-arena.md:842)

> The read does not read, import or depend on this amendment. B' only names one field of the read's output (and the completeness fields of item 3.3) to be copied after its verdict.

DEC-031 Am.20 item 8. (DEC/DEC-031-forward-paper-arena.md:842)

> 10. **Unchanged:** the promotion gate and every threshold (n >= 100; >= 5 UTC dates with a majority positive; CI90 lower bound > 0, 1,000 draws, seed 1, 5th percentile; ex-top-3 > 0; flat 15% and pressure-fail at slope scale 1); §2's one read per `bot_id`, its deciding cell and its outcome-blind read instant; Amendment 3 item 1 and Amendment 4 item 2 for every other book, and for P18 in every part except the P0 source; Amendments 5 to 19; every registered book; ruling Q1 items 1 to 6 (no live DBC process, probe, decision log or credit meter before LOOK CONSUMED; no heavy P18 job on research-0 from 2026-10-31T22:00Z until the read ends) and ruling Q3, Q4 and Q6; every seal.

DEC-031 Am.20 item 10. (DEC/DEC-031-forward-paper-arena.md:851)

**Governs:** The quant-proof ruling that Am.20 turns into DEC text (repo copy), Q2 and the conditions that bind P18 before and after the read.

> (a) A DEC-031 amendment carrying the departure must get a QP OK and a reviewer verdict, and must MERGE BEFORE the read's LOOK CONSUMED. Otherwise B' is unavailable and Plan B applies.

QP-RULING-1026-p33-p18 Q2 (a). (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:39)

> (b) The count is R13's per-UTC-day attempt n of the AM1 deciding set for 10-25 through 10-31, quoted verbatim. Only that field is used. It is copied after the verdict is recorded.

QP-RULING-1026-p33-p18 Q2 (b). (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:40)

> (c) The proxy is not conservative. It is a backfill with blockTime; the live process uses receive time and can drop rows, so it is an upper bound. Pass bar: mean >= 20 per day and no day < 10, as in Am.5 item 1.3.

QP-RULING-1026-p33-p18 Q2 (c). (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:41)

> (d) H = min(90, max(14, ceil(150/rate))). A PASS implies >= 100 fills in 7 days, about 14.29 a day at least, so H = 14 is effectively fixed. Pin it before the clock.

QP-RULING-1026-p33-p18 Q2 (d). (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:42)

> (e) The day-1 dry score on the LIVE process at the registration-candidate sha is still required. That includes item 4, the live-vs-backfill cross-check (the only link between proxy and process), and Q4's latency item. One process per sha.

QP-RULING-1026-p33-p18 Q2 (e). (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:43)

> - On FAIL, NOT_DECIDABLE or PASS-SCREEN-LOOKAHEAD, stop the process at once. Register nothing. Do not reuse the code for a sibling cell.

QP-RULING-1026-p33-p18 Q1, After the read. (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:35)

> - On PASS, P18's rule, pins and bars are exactly those merged before the look. Any later change to deciding text is a new registration with the change disclosed, and the clock moves.

QP-RULING-1026-p33-p18 Q1, After the read. (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:36)

> 3. Start any live DBC process before the read. That covers a Helius transactionSubscribe connection, the 10-min probe, a P18 decision log, or a credit meter.

QP-RULING-1026-p33-p18 Q1 item 3. (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:29)

> 5. Run a heavy P18 job on research-0 from 10-31T22:00Z until the read finishes (its MemoryMax is about 10 GiB, exit 13 guard).

QP-RULING-1026-p33-p18 Q1 item 5. (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:31)

> 4. Create a bot_id, a bots.json line, an N increment, a P0 run or a registration record before a recorded PASS.

QP-RULING-1026-p33-p18 Q1 item 4. (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:30)

**Governs:** State document: the DBC row.

> | **DBC m10** | Forward window [10-25, 11-01T00:10Z). Read chain submitted (§5). | ONE read via job #1804, a confirmation only. If it PASSes, P18 B' (DEC-031 Am.20 #1066): P0 = the read's attempts_by_day, clock 11-04T00Z, expiry 11-06T00Z. P18 code is ready: #1067 and the wire md5 PASS #1805. Owed for P18: fast-0 staging (venv, worktree, gate jobs J1–J4 per `docs/research/p18-prebuild/gate_jobs.md`), probe, dry score, registration pack and guard.

docs/HANDOFF.md section 1 (state document, not binding text). (docs/HANDOFF.md:25)


## 7. The seals

**Governs:** D1 SPENT hours and the forward window, as the ledger records them.

> **SPENT 2026-10-24 by MiScusi job #1494, the one-look m10 holdout read: NOT_DECIDABLE (tool defect, 0 trigger prints because `EvtSwap3` was labelled a hook); the printed "FAIL no attempts" stays on record.**

HOLDOUT_LEDGER, Meteora DBC holdout row. (docs/HOLDOUT_LEDGER.md:69)

> Allowed touch: the decode-only check only (read-hardening item 6, [READ-HARDENING.md](../docs/research/dbc-m10-forward/READ-HARDENING.md), job C1 `tools/dbc_m10_decode_check.py`; C1 MiScusi job #1574 PASS, notebook n_fVBsdSlwY-bFfA, per HANDOFF). HANDOFF section 2: no count, screen, engine or price there. Never a confirmation block again.

HOLDOUT_LEDGER, Meteora DBC holdout row. (docs/HOLDOUT_LEDGER.md:69)

> **Sealed, unread.** One read, the job script `scripts/research/dbc-m10-read-job.sh` with argument `A` or `B`, which runs `python3 -m tools.dbc_fwd_read_am1 --confirm-one-look` at tag `dbc-m10-forward-v1-am1`, at or after 2026-11-01T00:10Z, **confirmation only: not a promotion-gate read, a PASS licenses a forward paper book (P18) and nothing more, no C0 path** (HANDOFF section 1).

HOLDOUT_LEDGER, Meteora DBC m10 forward window row. (docs/HOLDOUT_LEDGER.md:70)

> **Forbidden:** `tools/dbc_fwd_read.py` is retained only as an imported library; any invocation of it as a program, or of its `run()` against `FWD_ROOT`, is forbidden, and a marker v3 claim by it ends m10 (HANDOFF 11-01 step; READ-HARDENING item 2c).

HOLDOUT_LEDGER, forward window row. (docs/HOLDOUT_LEDGER.md:70)

> Do not run `python3 -m tools.test_dbc_m10_read_hardening` or any other #890 test file on or after 2026-10-31T00:00:00Z (HANDOFF).

HOLDOUT_LEDGER, forward window row. See Notes N8. (docs/HOLDOUT_LEDGER.md:70)

> **Readers:** the single read job (`dbc-m10-read-job.sh`, Option A needs a reviewer verdict and a QP OK on PR #890 by 2026-11-01T00:00Z, otherwise Option B); the daily fetch and the G gap passes write only and take directory listings and completeness markers (R3). No other reader, no count, no smoke.

HOLDOUT_LEDGER, forward window row. (docs/HOLDOUT_LEDGER.md:70)

> Write-only: no smoke, no count and no open of a window hour before the read; per-hour completeness markers are the only check (`backfill_hour_end`, no gap row); directory listings only.

REGISTRATION R3. (docs/research/dbc-m10-forward/REGISTRATION.md:36)

> - **DBC:** the forward window and fwdv1/. Never run any #890 test file, nor `tools.test_dbc_m10_read_hardening`. LaunchLab [10-17, 10-24) is SPENT.

docs/HANDOFF.md section 2 (state document). The binding list is `/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md` (sha256 prefix 823cc062), outside the repo. (docs/HANDOFF.md:32)

> 1. Open, list beyond R3 markers, count, replay, smoke or test on fwdv1/ or any DBC hour >= 2026-10-17T00Z. Proofs and tests may use only [10-14T00, 10-17T00) and synthetic data. This includes [10-17, 10-24) (SPENT; C1 decode-only is the only allowed touch, and it is done).

QP-RULING-1026-p33-p18 Q1 item 1. (ARTIFACTS/lab/qp-ruling-1026-p33-p18.md:27)

Other seals that cover the same period (pointers only; see `docs/HOLDOUT_LEDGER.md` before pricing any block): D2/D3 hours `[2026-10-02T10, 2026-10-16T01)` and the tip tape; EXP-024 Look-2 to 2026-11-14; Q50 and HV4 outputs; `/data/mal/meta-capture/`. The Q50 rules file `docs/rules/q50-a1.md` section 8 quotes the DEC text for those.


## 8. The jobs, by id

**Do not open, list or status-view the read output directory, `fwdv1/`, READ-ATTEMPTS.log or any chain job log before the read ends; never call `miscusi_job_status` on sealed book-runner jobs.** Use `miscusi_job_list` (state only). #1784 to #1804 are the read-chain jobs of the registration addendum; this file did not open their logs or outputs.

**Governs:** Roles as recorded in the registration addendum A3 and A4.

> F = MiScusi job #1546 (replaces #1510; only change `--credit-cap 3700000`, item 5), mal-research-0, in the jobtree at commit c6d4c046bcca5459909fadece040e24762a2f897 (tag dbc-m10-forward-v1), not in the am1 tag worktree.
> Started 2026-10-24T18:28Z, limit 190 h (ends 2026-11-01T16:28Z). Source: the QP verdict on #890 and the manager's job record; this addendum did not open F's log.

REGISTRATION addendum A3 (Fetch F, #1546). (docs/research/dbc-m10-forward/REGISTRATION.md:112)

> | #1784 | G gate g1: host-side sleep to 2026-10-28T06:00:00Z | none (running) | 64 MB, 10 h |

REGISTRATION addendum A4 table. (docs/research/dbc-m10-forward/REGISTRATION.md:120)

> | #1797 | G gate g2, to 2026-10-29T06:00:00Z | #1784 | 64 MB, 25 h |

REGISTRATION addendum A4 table. (docs/research/dbc-m10-forward/REGISTRATION.md:121)

> | #1799 | G gate g3, to 2026-10-30T06:00:00Z | #1797 | 64 MB, 25 h |

REGISTRATION addendum A4 table. (docs/research/dbc-m10-forward/REGISTRATION.md:122)

> | #1801 | G gate g4, to 2026-10-31T06:00:00Z | #1799 | 64 MB, 25 h |

REGISTRATION addendum A4 table. (docs/research/dbc-m10-forward/REGISTRATION.md:123)

> | #1796 / #1798 / #1800 / #1802 | G pass 1 / 2 / 3 / 4 | its own gate: #1784 / #1797 / #1799 / #1801 | 4 GB, 1 CPU, 3 h each |

REGISTRATION addendum A4 table. (docs/research/dbc-m10-forward/REGISTRATION.md:124)

> | #1803 | G pass 5, FINAL | F #1546 only | 4 GB, 1 CPU, 3 h |

REGISTRATION addendum A4 table. (docs/research/dbc-m10-forward/REGISTRATION.md:125)

> | #1804 | READ, Option A: `sh scripts/research/dbc-m10-read-job.sh A 10` | F #1546 and #1803 | 11 GB, 2 CPU, 600 min (10 h), priority urgent |

REGISTRATION addendum A4 table. (docs/research/dbc-m10-forward/REGISTRATION.md:126)

> Cancelled, never ran: #1785-#1793.

REGISTRATION addendum A4 table. (docs/research/dbc-m10-forward/REGISTRATION.md:127)

> The first chain (#1785-#1793, serial: each G pass after the previous one, READ after pass 5) was cancelled before any of it ran. A G pass killed at its 3 h limit would have failed the chain and
> blocked the READ (QP non-blocking note on PR #1068, comment 6454456703).

REGISTRATION addendum A4. (docs/research/dbc-m10-forward/REGISTRATION.md:116)

> (daily fetch after each UTC day closes, job #1546, cap 3.7M credits) | reserved: DBC m10 forward book, tag `dbc-m10-forward-v1-am1` (7d5be9e5); not an EXP number, so the catalog reads `reserved` and denies every role | **Sealed, unread.** One read, the job script `scripts/research/dbc-m10-read-job.sh` with argument `A` or `B`, which runs `python3 -m tools.dbc_fwd_read_am1 --confirm-one-look` at tag `dbc-m10-forward-v1-am1`, at or after 2026-11-01T00:10Z, **confirmation only: not a promotion-gate read, a PASS licenses a forward paper book (P18) and nothing more, no C0 path** (HANDOFF section 1). Fewer than 100 fills, more than 6 incomplete hours, or more than 5% of window SOL-quoted rows in unmapped configs, among others (R1, R3, R14; R14 covers a failed one-look), is NOT_DECIDABLE and ends m10 and every D1 variant on DBC. **Forbidden:** `tools/dbc_fwd_read.py` is retained only as an imported library; any invocation of it as a program, or of its `run()` against `FWD_ROOT`, is forbidden, and a marker v3 claim by it ends m10 (HANDOFF 11-01 step; READ-HARDENING item 2c). Do not run `python3 -m tools.test_dbc_m10_read_hardening` or any other #890 test file on or after 2026-10-31T00:00:00Z (HANDOFF). Source: [REGISTRATION.md](../docs/research/dbc-m10-forward/REGISTRATION.md) R1-R3 and R14, [AMENDMENT-1.md](../docs/research/dbc-m10-forward/AMENDMENT-1.md) (causal deciding set), [READ-HARDENING.md](../docs/research/dbc-m10-forward/READ-HARDENING.md), HANDOFF section 1 and 2 (seal: `fwdv1/` is not opened). **Readers:** the single read job (`dbc-m10-read-job.sh`, Option A needs a reviewer verdict and a QP OK on PR #890 by 2026-11-01T00:00Z, otherwise Option B); the daily fetch and the G gap passes write only and take directory listings and completeness markers (R3). No other reader, no count, no smoke. |

HOLDOUT_LEDGER, forward window row (Host cell). (docs/HOLDOUT_LEDGER.md:70)

**Governs:** State document: the chain and the feed handover as the manager recorded them.

> - **DBC chain** (all at 304bb9cd on research-0):
> - Gates g2 #1797 (10-29T06Z), g3 #1799, g4 #1801.
> - G passes #1798/#1800/#1802 are independent, each with a bounded 100 min wait.
> - G5 FINAL #1803 runs after F #1546, then **READ #1804** (Option A, 11 GB) runs after F and #1803.
> - G pass 1 #1796 finished rc 0 with no gaps. #1785–#1793 were cancelled and never ran.
> - If #1803 is killed, #1804 never starts; resubmit both.

docs/HANDOFF.md section 5 (state document). (docs/HANDOFF.md:77)

> - **Feed handover:** H' #1652 is armed and sleeps until 11-07T11:50Z (n_NglnQrS8iEMa1Q); post-check C #1585.

docs/HANDOFF.md section 5 (state document). #1652 H' is not a DBC read-chain job (#1804 runs after F #1546 and #1803 only). (docs/HANDOFF.md:83)

> - 10-31 21:07Z: check research-0 room for DBC READ #1804, which needs about 11 GB;

docs/HANDOFF.md section 6 (state document; the action for the OQ12 risk). (docs/HANDOFF.md:95)

> Manager rule: no new heavy research-0 job from 2026-10-31T22:00Z until the read ends; #1775 and #1767 are held or deprioritised if they have not finished by then.

REGISTRATION addendum A5 (the rule for the OQ12 risk). (docs/research/dbc-m10-forward/REGISTRATION.md:146)


## Notes (non-binding; QP classification of the first-draft questions, QP pass on #1079)
- N1. REGISTRATION.md:3 and AMENDMENT-1.md:3 status lines are stale text; the registration is in force. `dbc-m10-forward-v1` is a lightweight tag at c6d4c046; `dbc-m10-forward-v1-am1` is an annotated tag (object e3b2f686) with tagger date 2026-10-24T15:39:46+00:00, 7d5be9e5 behind it.
- N2. The data window is R1 [2026-10-25T00, 2026-11-01T00). The seal adds the read margin to the earliest read instant 00:10Z (HOLDOUT_LEDGER.md:70, Hours cell). 00:10Z is not data.
- N3. Per QP, ruling (5) controls the fetch cap (one cumulative cap, raising it is operational); the R3 shape "7 jobs ... 500000" is out of force; R3's after-close and write-only terms stay. No effect on #1804.
- N4. Per QP, A1 supersedes the whole R2 "Read tool" line; out dir is `fwd-read-v1-am1` and the file `fwd-m10-am1.json` (AMENDMENT-1.md:30; A4).
- N5. The am1 tag was placed 2026-10-24T15:39:46Z, before 2026-10-25T00:00Z, so AM6's late-admissibility branch never engaged. The remaining route to PASS-SCREEN-LOOKAHEAD is a marker v3 claim by the old tool.
- N6. See "Option A conditions met" in section 5.
- N7. The Am.20 heading is stale; the amendment is in force. #1066 merged 2026-10-27T21:42:07Z (d740e064); QP OK (6454308322) and reviewer APPROVE (6454305913) are both at 4801f096.
- N8. The #890 test-file bar date comes from a #890 QP condition (10-31T00:00Z); HANDOFF and the brief say never, which is stricter. Obey "never".
- N9. #1652 H' is not a DBC read-chain job.
- N10. AM5 PENDING line: see section 1.
- N11. A later records PR should copy the host ruling to ARTIFACTS/lab/ (as was done for qp-ruling-1026). Not blocking.


## OPEN QUESTIONS FOR QP
Per the QP pass on #1079, these are not resolved here. None affects read #1804's rule, gate, pins or verdict.

1. **SHA256SUMS record gap (real conflict, low).** REGISTRATION.md:3 says the registration sha256 and "Registered at" are recorded in SHA256SUMS.txt by the manager at tagging, and READ-HARDENING.md:34 cites SHA256SUMS-AM1.txt. Neither file is in the repo at either tag or on main, nor at hunt29/dbc/ (per QP). The wrapper pins by `git show` of the am1 tag values, not by SHA256SUMS, so there is no effect on #1804 or P18.
2. **Q2(b) vs Am.20 item 3.3 (real textual tension, low).** QP-RULING-1026-p33-p18 Q2(b) says "Only that field is used". Am.20 item 3.3 adds `hours_present` and `bad_hours`. QP OK on the exact merged head 4801f096; the DEC text controls. The addition only adds NOT_DECIDABLE routes for B'. Affects P18 B' only (Am.20 item 8: the read does not depend on the amendment).
3. **Disclosed risk, not a text conflict.** REGISTRATION A5 says "a post-claim OOM is NOT_DECIDABLE and ends m10". It could change #1804's outcome class but not its rule. The actions are the A5 rule and the HANDOFF 10-31 21:07Z step, both quoted above.

