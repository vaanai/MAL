# Q50 C0 X-TIME CHECKLIST (DEC-032 s7-s9 + Am.2 s2 + Am.3 s5-s9), prepared 2026-10-26 22:55Z (`date -u`)

Records only. Nothing here is a profit claim; no book has cleared the promotion gate. Outcome-blind: no HV4 or Q50 output, ledger, fill, decision row or P&L was opened, and the status or logs of jobs #1419/#1421/#1561/#1562 were not viewed. EXP-024 Look-2 seal runs to 2026-11-14. X = the merge sha of Q50's canary DEC copy (not yet merged; DEC copy desk file: `hunt29/p1/DEC-copy-h5v1q50-boostfloor-a1.md`).

Status words: DONE (evidence cited), OWED (action named), BLOCKED-ON (owner / Helm / gate PASS), NOT CHECKED (could not be verified from the repo or without opening sealed material).

## A. The four X-time items from the brief
| # | Item | Status | Evidence or action |
| --- | --- | --- | --- |
| a | Install manifest shows exactly FOUR changed paths vs the reviewed install (`tools/h5_arena_executor.py` plus `tools/pumpswap_tx.py`, `tools/pumpswap_simulate.py`, `tools/probe_executor.py`, the last three for the mayhem fee recipient fix (notebook n_aIm_21YbvT68_A, PR #1027)) | tool DONE, run at X OWED | `sh scripts/ops/q50-xtime-manifest.sh X` on mal-fast-0 in ~/MAL. Dry-tested here only against main a8070b41 (pinned set: 43 paths, list sha256 244f9e85...; at that time one changed path, `tools/h5_arena_executor.py`, blob 70d789ff = 4646e542, sha256 ef8ae931...; the script as of #1027 allows exactly four changed paths, the executor plus the three mayhem files, the other 39 identical, the three mayhem files each pinned by exact blob and sha256 (DEC-033 Am.4 item 1); probe `sh scripts/ops/test_q50_xtime_manifest.sh`; not run against X). It exits 3 `PINNED_SET_CHANGED` on any other result, or if one of the four is missing from the pinned list. Steps 1, 2, 4, 5 of QP-PRERULING-1026-n2-n4 (c). Step 3 (canary record check) is not in the tool: it is its own checklist step, row s8.3a in section C. |
| b | HELM-SHEET s.A re-pinned to sha256 ef8ae931 | patch DONE, applied to the sheet OWED (manager) | `ARTIFACTS/lab/q50-xtime/HELM-SHEET-A-repin.patch` (11 lines; applies cleanly to hunt29/p1/HELM-SHEET.md sha256 0dc7597e...). Only the executor hash line changes; the patch passes `git apply --check -p0` on a copy of the sheet. **Never apply it alone**: after it the sheet still names install 0526d5cd, '#1481 EQUAL at this sha', the 0526d5cd manifest (1576 lines, sha256 5299a3c4...) and 0526d5cd in the §C/§F commands, and a 0526d5cd tree has executor sha256 c451a615, not ef8ae931. The whole sheet is re-pinned to X in ONE records PR. The sheet's other `0526d5cd`, manifest path and manifest sha256 lines (s.A item 2/3, s.C, s.F) move to X when X exists (QP N2 (c) "Also re-pin to X"); the manifest sha256 comes from the tool in (a). |
| b' | **(2026-10-30, DEC-032 Amendment 5 DRAFT; QP report qp-1085-gap-hold condition 1)** Row b's ef8ae931 is superseded for an install at X that contains PR #1085: the executor sha256 is `9cb7ac22acc75247d8788044054349019c7656a656036421529e7da4d97f1fde` (blob `b96f023fe64eadd63a2d377e08a0d004a7038d20`: #1085 + #1091 + #1106, moved last by #1106 from `7e6d2f0e` / `8369ab21`); fifth path `tools/arena_live_parity.py` blob `989e3de6…` sha256 `d22aff3c…` (#1106) | patch re-pinned in this records PR; applying it to the sheet is OWED (manager), and so is the sheet's move to X | `ARTIFACTS/lab/q50-xtime/HELM-SHEET-A-repin.patch` now re-pins `c451a615...` to `9cb7ac22...` (#1085 + #1091 + #1106). If #1085's executor changes in review, this row, the patch, DEC-032 Am.5 item 7 and the manifest pin all move together. A later executor change (#1091, #1106) moves them to the combined blob in a records change merged with or before that PR, with md5 EQUAL at the combined blob; at X the records name the manifest's executor blob, or no `mode=live`. No install at a sha containing blob 53337e08 starts `mode=live` before Am.5 is in force (QP condition 1). |
| c | md5 replay reproduces #1481: A 137/137, B 101/101, synthetic_unconfirmed 0/0 | scripts DONE, run OWED | Job command in section D. The wrapper makes a fresh copy of the 24-hour 2026-09-20 shadow replay, verifies hour-file sha256, runs the replay script from X's own tree, and exits 4 `DIFFERENT_OR_EMPTY` on any other count. No job #1481 is in the MiScusi job list this session can see, so its original command was reconstructed from `hunt29/p1/jobs/md5-replay-q50-install0526.sh` (SHA256SUMS e54bc996...) and not compared with the job record. |
| d | 1 h dry run at X with the A3 guard read; pass rule includes **0 build errors** (Am.2 §2 item 3) | OWED, Helm + manager | Section E. Not a MiScusi job: it is Helm's keyless unit (HELM-SHEET s.E). `dry.json` already carries `a3_record_file`. |

## B. Static facts to re-verify at X (OWED, manager, cheap)
1. Tests 1 to 17 and 17b green at X on mal-fast-0's Python (Am.3 s8 item 3). Not run here (the PR touches no code). QP opened a different set: 454 passed at 791b82f9 on this host.
2. 5b diff confinement printed on PR #972 (per-function source identity, `decide_row` and callees identical). Evidence: QP OK 6425036235, reviewer APPROVE 6424980798 at a55b690a. NOT CHECKED here that item (b)'s printed list is on the PR.
3. Bind mount covers the guard output (`BindReadOnlyPaths=__A3_DIR__:/srv/mal-h5-arena/a3`). Evidence: `scripts/mal-fast/mal-h5-arena-executor-binds.conf` is in the pinned set and unchanged vs 0526d5cd. The dry run must show the guard read succeed (`a3_guard_unreadable` = STOP).

## C. DEC-032 s7 (confirmation track and size ladder) and s8 (order of acts)
QP's #979 nit: the "Still open" list was shorter than s7-s9. This table is the complete list.

| Ref | Precondition | Status | Evidence or owed action |
| --- | --- | --- | --- |
| s7 | Confirmation read PASS at p <= 0.05/c on the SAME bot_id, >= 100 attempts after the screen instant on >= 5 further UTC dates, one read at the outcome-blind instant | BLOCKED-ON the read | Not read here (sealed). Kill/clock dates: Q50 read per DEC-031 Am.3; EXP-024 Look-2 seal to 2026-11-14. |
| s7 | A canary may run inside the confirmation window; live results never enter the paper read; label "unpromoted execution measurement" on every report, note, Console entry | OWED (labels) | Apply to every canary record. |
| s7 | Concurrent-observation disclosure: the confirmation report carries it (wallet is public and watched in real time; results can influence later owner choices, never the formal read) | OWED (report text) | |
| s7 | A confirmation FAIL or NOT_DECIDABLE ends the book's canary at the next 00:00Z (owner may extend for execution measurement only) | KNOWN rule; OWED to be quoted in the copy's owner notice | |
| s7 | Step C0 to C1 needs read PASS, "live fills match paper" (>= 100 eligible trips, CI90 lo of mean d_i >= -100 bps, landing p50 <= 1.9 s, P7 off), no unresolved halt, >= 5 signed daily tie-outs with residual 0, impact re-price at C1 stake, unchanged sell builder, owner's dated line, wallet funded to 1.00 SOL, Helm writes `TIER` | OUT OF SCOPE for C0 (C1 locked: `CONFIRMATION_C` unset in code, HELM-SHEET s.A item 2). Listed so nothing is read as pre-approved. |
| s8.1 | Manager and scorer: read PASS, multiplicity block, ex-best-day, worst-in-slot | BLOCKED-ON gate read | |
| s8.2 | quant-proof on the read, seals, taints, c; forced verdict as a plain agent | BLOCKED-ON gate read | |
| s8.3 | Builder: copy template to the numbered DEC, fill s9, PR with QP OK on head and reviewer verdict | OWED | Desk copy exists (hunt29/p1). QP N2 item 5: the merged file must be named `DEC/DEC-0NN-...canary....md` (no "template" in the name; the desk name `DEC-copy-h5v1q50-boostfloor-a1.md` does not match the `DEC-*canary*.md` glob and a live start would refuse `canary_record_for_bot_id_missing`) and carry the bare line `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1` (no bullet, no backticks, nothing after the id). Also `desk copy`: `ARTIFACTS/lab/c0-drafts/DEC-copy-q50-corrections.md` exists; its application status NOT CHECKED. |
| s8.3a | Canary record check at X (QP N2 (c) step 3): exactly one `DEC/DEC-*canary*.md` without 'template' in git at X (mode 100644) holding the bare `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1` line, no `<FILL` left; print its path and blob sha256 (= `FILL:dec_copy_sha`) | OWED at X, manager. Tool: `sh scripts/ops/q50-canary-record-check.sh X` (added 2026-10-27; test `scripts/ops/test_q50_canary_record_check.sh`) | Run and record the result in the copy's records PR. **Correction 2026-10-27 (QP-PRERULING-1027-xorder Q3):** the `<FILL` grep above is VOID as a guard (0 hits on an unfilled copy; 53 lines with the bounded regex) and "exactly one DEC-*canary*.md" fails on a correct tree (DEC-024/026/028/033 all match the glob). Replaced by `sh scripts/ops/q50-canary-record-check.sh X` (test `scripts/ops/test_q50_canary_record_check.sh`; `--after-r` after records PR R): the executor's own `canary_record_refusal` from X's tree returns None; DEC-033 is the only file whose owner line names the book; 0 open `<FILL` / `FILL:gate-read|review|owner`; the `POSTX:` set equals the DEC-033 table (after R: none outside the table); positive control (mandatory, full id 1dd08c837660c778c5c26315f1b607faa8ce2f49) fails on the unfilled copy; path and blob sha256 printed. The executor (`tools/h5_arena_executor.py`) is in the pinned set and is not changed. |
| s8.4 | Mint fetch of traded mints, sell-builder pin and simulate output | OWED / NOT CHECKED | QP N4 (same pre-ruling): #821 does NOT meet C7 as recorded; rerun with mints from outside the Look-2 class. `FILL:mint_fetch`, `FILL:simulate_ref`, `FILL:builder_path@sha256`. Chain proofs: `hunt29/p1/CHAIN-PROOFS.md` (not re-read here). |
| s8.5 | Owner ask through `miscusi_ask` (s2 text), only after items 1 to 5 | BLOCKED-ON items 1-4 | Pre-owner standing OK for a C0 canary after a PASS exists (owner 10-23 15:00Z); keys, wallet and go-live need the owner's word at the time. |
| s8.6 | Parity and tie-out tools; executor installed DRY; 1 h dry run; md5 replay | OWED at X | Items (c) and (d) above. Tools: `tools/arena_live_parity.py`, `tools/arena_tieout.py` in the pinned set. `tieout-daily.sh` and `twin-writer-loop.sh` re-pin to X (new SHA256SUMS lines; QP N2 (c)). Scheduled reconciliation job id = `FILL:tie_out_job`, OWED. |
| s8.7 | Owner yes/no, wallet funding (0.35 to 1.00 SOL), own instruction to Helm; Helm: pinned root install from the named sha, manifest, key via credential, drop-ins, watchdog, `TIER` T1 (ARENA_LIVE_TIER; /etc/mal-h5-arena/TIER) and `LIVE_OK` | BLOCKED-ON owner + Helm | HELM-SHEET s.F order. `LIVE_OK` and `TIER` are not for the dry install. |
| s8.8 | Manager reviews dry output, sets `end_ms` (<= 2026-11-06T00Z, at most 14 days), gives go; manager restarts no host unit | OWED | |

## D. DEC-032 s9 FILL fields (every one needs a value in the merged copy)
Status of each is OWED unless noted. The 10-25 readiness note (`ARTIFACTS/lab/c0-readiness-2026-10-25.md`) holds the earlier mapping; this list adds the X-time values.
- `dec_number`, `bot_id`, `frozen_sha` (a5f413704eb120c8f529101a6186e3a95de7f83e per HELM-SHEET), `clock_start_ms`: from `bots.json` (not opened here: sealed class).
- `read_instant`, `n`, `d`, `mean`, `ci_lo`, `p`, `ex_best_day`, `wis_mean`, `wis_lo`, `N`, `holm`: BLOCKED-ON the screen read.
- `qp_comment`, `reviewer_comment`, `head_sha`: the copy PR.
- `install_sha` = X; `executor` = config sha256 (`sudo sha256sum /etc/mal-h5-arena/*.json` from Helm) + manifest (tool (a)); `c0_constants` path:line at X (values 50,000,000 / 2 / 30 / 100,000,000 / 200,000,000 lamports are compiled in code, readiness note item 13).
- `seal_status`, `universe`, `venue_exit_rule`, `feed_gap_source`, `a3_source`: from the copy.
- `book_stake`, `canary_stake` (S = min(book stake, 0.05 SOL)), `fund`, `start_balance`, `floor` (max(0.05, funded - 0.25)), `worst_case` (0.25), `wallet_address`, `withdraw_holder` (Helm), `daily_stop` 0.10, `total_stop` 0.20, `days`, `end_ms`: BLOCKED-ON owner funding.
- `fee`, `twin_tool`, `twin_leg` ("nofail, 1.9 s"), `twin_e1` ("E1 excluded decides"), `builder_path@sha256`, `simulate_ref`, `mint_fetch` (job id, counts, close-blocked share, credits used), `tie_out_tool`, `tie_out_job`, `alert_target`.
- `dryrun_job`, `md5_proof`: the results of (d) and (c) at X. `md5_proof` must state the source of the required counts: HELM-SHEET line 18 and `ARTIFACTS/lab/c0-readiness-2026-10-25.md:42`, because the #1481 job record was not found; and that the replay script was read from X's own tree (blob id printed by the wrapper).
- `owner_line`: only from the owner's own message.
- `a3_guard_source` (Am.3 s6): `dirname(a3_source)/a3-applied.jsonl` on mal-fast-0, the loop job id and its pin, time limits and the successor chain past the later `end_ms`, alert job.

## E. Am.3 s7 guard-loop lifetime (preconditions before the first send; the ids are the manager's facts, not verified here)
Looking up job status of the A3 chain jobs is not sealed, but this session did not do it. Done by the manager from the job list:
1. Record loop #1446 (ends about 2026-11-06T18Z; Q50 `end_ms` <= 2026-11-06T00Z, margin about 18 h), successor #1447, alert #1540 (ends about 2026-11-08T13Z), dead-man #1319 and #1333 with pins and real limits.
2. 48 h rule: any chain job whose actual limit is before E is extended or given a successor at least 48 h before its limit. Q50 alone is covered by #1446; loop job 3 / alert extension / dead-man job 3 are HV4 preconditions (latest submit about 2026-11-17T18Z, 2026-11-06T13Z, 2026-11-14T21Z).
3. Nothing the guard depends on (loop, alert, dead-man, `a3-books.json`, arena registry) is stopped or emptied while any canary runs.

## F. Am.3 s8 / s9 (Q50 code rides the X re-install)
| Ref | Item | Status |
| --- | --- | --- |
| s8.1 | Exactly four changed pinned blobs (executor 5b + the three mayhem files, the mayhem fee recipient fix (notebook n_aIm_21YbvT68_A, PR #1027)), list sha256 244f9e85..., re-pins in the Q50 copy's records PR | tool + patch DONE here; records PR OWED |
| s8.2 | #1481 counts reproduced at X | OWED (section D) |
| s8.3 | The replay does not exercise any 5b line (decide_row only) | KNOWN. Proof of 5b is tests 17/17b and the confinement list on PR #972, not the replay. |
| s8.3 | QP OK on the 5b PR head + reviewer verdict + (a) test 17b + (b) diff confinement | QP OK 6425036235 and APPROVE 6424980798 at a55b690a on #972 (from the #979 review; not re-opened here) |
| s8.4 | Dry run shows guard read through the bind; `a3_guard_unreadable` = STOP | OWED (E) |
| s8.5 | Dry run with `a3_record_file` set; report counts of `a3_guard_pending` refusals and any A3 latch | OWED (E) |
| s8.6 | No switch mid-run; if X lacks 5b the canary runs the raw rule for its whole run | KNOWN |
| s9 | QP (b)'s other owed items: link and owner checks on `a3_record_file`, dependencies in the install pack (precondition 14), equivalence test (test 13) | OWED / NOT CHECKED |
| s9 | HV4 install pack must bind the whole A3 directory, not the single file | HV4 lane, not Q50 |

## G. Commands for the manager (nothing was submitted or run heavy by this lane)
(c) md5 replay, MiScusi job on mal-research-0 (the 09-20 shadow replay dirs and the audit tape are on this host; #1481's host was not confirmed). The replay script and input are on this host. Run only after X exists and `git fetch` has X and the pack branch:
- machine: mal-research-0; memory 8 GB (one heavy job at a time); no network; resumable false; run under sh.
- git_ref: X (full 40-hex). The wrapper reads the replay script from X's own tree and REFUSES if X lacks `scripts/ops/q50-xtime-md5-replay.sh` (no fallback): this pack must be merged before X. It prints the script's blob and sha256. A failed run removes its own copy and `-rpc` sibling; after a hard kill, the cleanup `rm -rf` line is in the wrapper header.
- command: `sh /home/claude/.miscusi/repos/vaanai/MAL/scripts/ops/q50-xtime-md5-job.sh <X>` (needs this PR merged into the checkout, or substitute the path of a worktree at X).
- pass: last line `md5-replay VERDICT: EQUAL` plus the line `#1481 counts required: A 137/137, B 101/101 ...`. Any other output: STOP, no install.
- The refusal "dir contains an October date or arena" must not trip: the copy path is `.../shadow-2026-09-20-q50-a5f4137-rolling-install<X8>`.

(a) manifest, mal-fast-0 in ~/MAL (never Helm's clone), after `git pull --ff-only` on a ref at X:
- job: `sh scripts/ops/q50-xtime-manifest.sh <X>`; memory 1 GB; git_ref X. Writes `~/h5-arena-manifest-<X>.txt` and prints its sha256 for Helm (sheet s.A item 3 / s.C).

(d) 1 h dry run: Helm's s.E sequence with `/etc/mal-h5-arena/dry.json` as in sheet s.D; the sheet's counts command, plus these two counts for the guard read: `journalctl -u mal-h5-arena-executor --no-pager | grep -c a3_guard_unreadable` (must be 0), zero `build_error` / `simulate_error` alerts in the journal (0 build errors, Am.2 §2.3), and the ledger counts by (kind, reason) filtered to reasons starting `a3_` (report `a3_guard_pending` and any latch as counts only; open no outcome field). The manager sends QP the table. This is not a MiScusi job unless the manager wraps Helm's output collection.

## H. What this pack does not show
No replay, dry run, install or manifest at X has run. No claim about any book's results. Gate unchanged.

## I. QP go-live blocker list, verbatim (QP comment 6429311048 on PR #990, at d38e215d)

**Go-live blockers (none of them blocks this merge).**
1. The DEC-031 gate read PASS on the deciding cell, with the multiplicity block and QP on the read (§8.1–8.2, Am.2 §2.1).
2. The DEC copy merged as `DEC/DEC-0NN-…canary….md` with the bare `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1` line, every FILL filled, QP OK on its head and a reviewer verdict. The canary record check at X is run and recorded.
3. The N4 mint rerun with mints from outside the Look-2 class, plus the sell-builder pin and simulate output.
4. The manifest tool run at X (rc 0, four changed paths, list 244f9e85…). Its manifest sha256 is given to Helm, and the whole HELM-SHEET is re-pinned to X in one records PR.
5. The md5 replay at X prints `VERDICT: EQUAL` with A 137/137, B 101/101 and B synthetic_unconfirmed 0/0, the script is taken from X's own tree (gap 2), and the source of the counts is disclosed (gap 1).
6. Tests 1–17 and 17b green at X on mal-fast-0's Python.
7. A 1 h dry run at X with `a3_record_file` set: 0 build errors, `a3_guard_unreadable` = 0, and `a3_guard_pending`/latch counts reported.
8. The Am.3 §7 guard-chain facts recorded from the job list (#1446/#1447/#1540/#1319/#1333, with their pins and limits).
9. The owner ask (§2), the owner's yes and funding, and Helm's install, `TIER` T1 (ARENA_LIVE_TIER; the arena reads its own /etc/mal-h5-arena/TIER) and `LIVE_OK`. (Corrected from the verbatim QP text, which said T0: the code requires T1.)
10. The manager reviews the dry run and sets `end_ms` ≤ 2026-11-06T00Z.
11. The "unpromoted execution measurement" label on every canary record.
