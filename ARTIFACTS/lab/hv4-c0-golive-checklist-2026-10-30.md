# HV4 C0 go-live checklist: from "the read prints PASS" to the first live buy

Written 2026-10-30 (`date -u` 15:34Z) on mal-research-0 by the hv4-c0-ready lane for manager18. Base: main `c92f23e3`.

This is a desk inventory plus builds. **No book has cleared the promotion gate.** A C0 canary is an "unpromoted execution measurement" (DEC-032 §0). It is not a profit claim.

**Not opened:** any HV4 ledger, decision row, `bots.json`, read record, attempt count, score/am6 output, or job #1561/#1562/#1829/#1830 status or log. Every test below ran on synthetic fixtures.

**Reads in question:** jobs #1829 (hv4-BIG-X) and #1830 (hv4-CON-X). Each sleeps until 2026-11-01T01:35Z, then runs the Am.15 read at as-of 2026-10-31T00:00Z (HV4-READ-PACK-1031). A read may wait up to 26 h for #1591's 10-31 table line.

## 0. Summary

### Earliest realistic first live buy after a PASS
- **About 4 to 7 h after the read record, so 2026-11-01 ~06:00 to 09:00Z at the earliest**, if all of these are true by 11-01T01:35Z:
  - the code PRs of §1c are merged;
  - the one-line `LIVE_REFUSAL_ACTIVE = False` flip is merged;
  - the DEC-032 records are merged: Am.5 (gap hold) and the owner-file rule, from the hv4-amend lane;
  - Helm's keyless Part 1 install is done on mal-fast-0.
- **A realistic target is 11-01 ~08:00 to 16:00Z.** 01:35Z on 11-01 is 18:35 PT on Saturday 10-31, so it depends on when the owner and Helm are online to fund and to write the root files.
- **If the code is not merged and installed before the read: 11-02 to 11-03.** That means seven code PRs, each with QP and reviewer cycles, then a Helm install and an md5 proof, all after the PASS.
- **The first buy itself** is the arm's next entry row after the start. The arm's entry rate was not opened (it is sealed), so no figure is given.

### Blocking today, found by reading code at c92f23e3
1. A live `hv4_c0` start refuses (`hv4_c0_incomplete`, `tools/hv4_live_c0.py:455`, via `start_refusal` :1123-1135). Every dry start on a non-synthetic root refuses too. So **even after a recorded PASS, nothing can start on the real rows.**
2. `CANARY_DEC_SHA256 = ""` (:465). A live start refuses `owner_dec_pin_unset`, and only a code PR at event time could fix that. **Manager decision 10-30:** the owner record becomes a root-owned file, with no pin in code.
3. QP's pre-removal list is still open. The sources are #959 comment 6423545454 (15 items), #969 (6424766650, P7-a to P7-e) and #970 (the 894cd2f7 and 06558b52 verdicts). The open items are:
   - **P7-a to P7-e, tape gaps:**
     - slot gaps;
     - hour roll and compaction;
     - a dead relay must refuse buys;
     - the engine cross-test;
     - carrying the stream to fast-0.
   - **Precondition 8 residuals:**
     - the signed priority and `last_valid_block_height` on every send row (otherwise the tie-out never signs off);
     - "rc 0 is not enough".
   - **Precondition 14:**
     - install pack;
     - md5 orders-equal-rows proof;
     - runner dead-man;
     - A3 liveness.
   - **Precondition 15:** the removal itself.
4. **The twin gate is not covered by any QP list.**
   - `h5_arena_gates.LiveGates.twin_refusal` (:412) blocks every new buy once a trip is past its exit without a twin row.
   - The twin is priced on mal-research-0 from the HV4 ledger (`tools/hv4_c0_twin.py`), while the trips file and the gate are on mal-fast-0.
   - Nothing moves trips up or twins down. **The canary would stop after its first exit.**
5. **No HV4 bridge sell simulate exists.**
   - `tools/arena_sell_simulate.py` drives the H5 builder (`H5Executor._arm_sell`, :1-3), not the bridge's pinned `probe_live` builder.
   - DEC-032 §2 checklist and §4 need `FILL:simulate_ref` for the book's own builder.
6. **Nothing of the HV4 bridge is installed on mal-fast-0.** Job #1837 (2026-10-30T15:10Z, names only) found absent:
   - `/opt/mal-arena-bridge`, `/etc/mal-arena-live`, `/etc/mal-hv4-c0`;
   - `/var/lib/mal-hv4-c0`, `/var/lib/mal-arena-feed`;
   - any `mal-arena*` or `mal-hv4*` unit;
   - the `mal-arena-bridge` user.
   It also found `/var/lib/mal-live/STOP` and `HALT` absent (good).
7. **One canary per host only.** `/etc/mal-arena-live/LIVE_OK` must name exactly one bot_id (`live_ok_problem` :428-431), and the owner-DEC path is single. Two HV4 PASSes could not both go live. The fix is in the start-gate PR (per-bot paths).
8. **The audit's "transport not built" (PR #956, 10-26) is stale.**
   - `tools/decision_forwarder.py` merged in #967 (98e62743, 2026-10-26). Its hop was measured at p90 21.33 to 36.22 ms on a synthetic log (`docs/research/p46-transport/DESIGN.md` §4).
   - What was missing is the tape-gap and twin carriage (items 3 and 4), now in build.

### PRs opened by this lane (all draft, base main, none merged)
See §1c. The records PR carrying this file is `claude/hv4-c0-ready`.

## 1. Inventory

### 1a. Merged on main (code read at c92f23e3)
| Piece | Where | PR | State |
|---|---|---|---|
| C0 limits, S rule, fee pin 55k/55k/150k | `tools/hv4_live_c0.py` | #908 | merged |
| frozen_sha pin, universe, sell pin, escalation, sealed-path read guard | same + `tools/arena_live_bridge.py` | #913 | merged |
| sell ladder 0.85/0.65, twin pins, parity/twin/tie-out wiring | + `tools/hv4_c0_parity.py`, `tools/hv4_c0_twin.py` | #959 | merged |
| read-record gate, owner line (sha pin), S, per-trade loss latch, 14-day window | `hv4_live_c0.read_record_problem` :636-716, `owner_line_problem` :717-747 | #965 | merged |
| exit snapshot inside the pin, feed-gap and A3 halts (consumer), gap relay (producer, not installed) | `tools/hv4_gap_relay.py` | #969 | merged |
| tie-out exporter, trip-bearing end-to-end | `tools/hv4_c0_tieout.py` | #970 | merged |
| decision-row forwarder research-0 → fast-0 | `tools/decision_forwarder.py` | #967 | merged |
| worst-in-slot export + `.wis.json` writer + PASS-only job | `tools/hv4_wis_export.py`, `scripts/ops/hv4-wis-job.sh` | #960 | merged |
| Am.15 read wrapper and instant tool | `tools/hv4_am15_read.py`, `tools/hv4_read_instant.py` | #915, #920 | merged |
| DEC-032 template + Am.1 (standing owner line) + Am.3 (pooled A3 guard) | `DEC/DEC-032-arena-canary-template.md` | #971 and earlier | merged (template not in force; Am.3 in force) |
| mint fetch tool | `tools/arena_close_blocked.py` (`fetch`) | — | merged |

### 1b. Installed where
| Host | HV4 C0 pieces installed |
|---|---|
| mal-research-0 | HV4 runner #1561/#1562 (sealed); daily instant job #1591; read jobs #1829/#1830 (sleeping). Ledger, decisions and arena tape live here. Not running: the gap relay, a forwarder `serve`, the twin relay. |
| mal-fast-0 | **Nothing for HV4** (job #1837). The H5/Q50 executor install at c8fcf761 is separate and stopped (HELM-REPLY-1029-1). research-0 has no credential to reach fast-0. fast-0 reaches research-0 with `ssh mal-research-0`. |

### 1c. Built today (draft PRs from this lane)
| Branch | Discharges | State at writing |
|---|---|---|
| `claude/hv4-c0-gap-hold` | Manager item 3. DEC-032 Am.5 (DRAFT) 60 s hold on data-loss gaps; transport events exempt; behind `FEED_GAP_MANUAL_REVIEW` | **PR #1090**, head 10613d1e, 402 tests pass |
| `claude/hv4-c0-start-gate` | Manager items 2 and 4, QP #959 items 3/15. Recorded-read start gate behind a one-line `LIVE_REFUSAL_ACTIVE` flip; owner record = root-owned per-bot file, no code pin; per-bot `LIVE_OK` | builder running |
| `claude/hv4-gap-relay-v2` | P7-a (slot gaps), P7-b (hour roll, .zst), P7-d (engine cross-test), relay loop + job script | builder running |
| `claude/hv4-tapegap-carry` | P7-c (missing or stale stream refuses buys), P7-e carriage in the forwarder, #967 write_status cross-test | builder running |
| `claude/hv4-c0-twin-carry` | Twin carry (trips up, twins down) for the twin gate (blocker 4) | builder running |
| `claude/hv4-c0-send-rows` | Precondition 8 residuals (signed priority, lvbh), on the hv4_c0 path only (probe_live untouched, to protect Q50's Am.4 four-blob X install) | builder running |
| `claude/hv4-c0-install-pack` | Precondition 14 install pack: units, binds, configs, keyless checklist (rehearsal bot id `hv4-SYN-X`), runbook, A3 liveness | builder running |
| `claude/hv4-c0-orders-proof` | Precondition 14: md5 orders-equal-rows proof, C0 read check (rc 0 is not enough), runner dead-man | builder running |
| `claude/hv4-c0-sell-simulate` | DEC-032 §4 simulate_ref through the bridge's pinned builder | builder running |
| `claude/hv4-c0-ready` (this) | This checklist; stage-2 DEC copy skeletons for BIG-X and CON-X; HELM-ASK draft | records |

The manager's final report lists the PR numbers and heads of the builder PRs when they open.

## 2. The checklist

Legend:
- **When:** NOW = do before the read. PASS = only after the arm's read is recorded PASS. X = after the install sha is set.
- **Who:** M = manager; QP = quant-proof; R = reviewer; B = builder; H = Helm; O = owner.
- Times are estimates unless a source is cited.

### 2a. NOW (before 2026-11-01T01:35Z): get the critical path off the post-PASS clock
| # | Step | Citation | Who | Command / PR | Time |
|---|---|---|---|---|---|
| N1 | Review and merge the seven code PRs (§1c) | QP removal list #959/#969/#970; DEC-013 Am.1 (T1: QP + reviewer) | QP, R, M | PRs on the branches above | 1-2 h each, in parallel |
| N2 | Merge the DEC records: DEC-032 Am.5 (gap hold) and the owner-file rule (no code pin) | DEC-032 status line; manager 10-30 | hv4-amend lane, QP, R, M | hv4-amend lane PR | 2-3 h |
| N3 | Merge the one-line `LIVE_REFUSAL_ACTIVE = False` flip, after N1 and N2, with a fresh QP pass at its head | QP #959 item 15 | B, QP, M | follow-up to `claude/hv4-c0-start-gate` | 1 h |
| N4 | Helm Part 1: keyless install at the merged sha Y, synthetic rehearsal with **bot id `hv4-SYN-X` only**. A real arm id would start that arm's 14-day window: the window file is written at the first start, DRY included (`arena_live_bridge.py:385`, QP #965 N-C) | runbook from the install pack; R2.1 | M → O → H | `ARTIFACTS/lab/c0-drafts/hv4/HELM-ASK-hv4-c0-DRAFT.md` Part 1 | 1-2 h of Helm time |
| N5 | md5 orders-equal-rows proof at Y (synthetic log) | CLAUDE.md (md5 replay before deploy); QP #959 item 14 | M | `scripts/ops/hv4-c0-orders-proof-job.sh <Y>` (MiScusi job) | 15 min |
| N6 | Bridge sell simulate at Y, one Token and one Token-2022 public September mint, both rungs, job on mal-fast-0 | DEC-032 §2 checklist, §4 | M | `scripts/ops/hv4-c0-sell-simulate-job.sh` | 30 min |
| N7 | Start the gap relay on research-0 (kind, instant and slot of tape gap rows only; QP #969 P7-a) | DEC-032 §5 item 2; P7-e | M | `scripts/ops/hv4-gap-relay-job.sh` (MiScusi job, research-0) | 10 min |
| N8 | Owner pre-stage message (one message): (a) fund a fresh HV4 canary wallet with 0.35 SOL on a PASS (§3); (b) the fee line naming 55,000 / 55,000 / 150,000 + 5,000 base per send (§4 F2), unless QP rules it is not needed; (c) be ready to tell Helm "go" on 11-01 | DEC-032 Am.1 item 3 ("Does not cover" funding/install); §4 | M → O | `miscusi_ask` | owner-bound |
| N9 | Check that the A3 guard chain covers the canary `end_ms` (≤ 2026-11-08T13:05Z): loop #1446 ends 11-07T00:57:39Z, with #1447 queued `after` it (DEC-033 §9a) | DEC-032 Am.3 §7 | M | `miscusi_job_list` (state only) | 5 min |
| N10 | Pre-fill the stage-2 skeletons. They are done for BIG-X and CON-X; F arms are not needed for 11-01 | DEC-032 §8 item 3 | lane | `ARTIFACTS/lab/c0-drafts/hv4/DEC-copy-<arm>-STAGE2-SKELETON.md` | done |

### 2b. AT PASS: from the read's RECORD line to the first live buy
T0 = the `RECORD /data/mal/arena/reads/<arm>.json sha256 …` line in #1829/#1830's final lines. Read only the final lines, never job_status (PUSH-BRIEF rule 5).

| # | Step | Citation | Who | Command / PR | Time (from T0) |
|---|---|---|---|---|---|
| P1 | Notebook the verdict line only; tell the owner in one line | HV4-READ-PACK §4, §6 | M | `miscusi_notebook_add` | +10 min |
| P2 | Worst-in-slot job, PASS-only; "not measured" or a mean ≤ 0 means no canary | DEC-031 Am.15 item 6 (:577); QP-RULING-1025 R3 | M | `BOT=<arm> TAPE_DIR=/data/mal/arena/tape HEALTH_LIST=… RESTARTS=… AS_OF_MS=1793404800000 sh scripts/ops/hv4-wis-job.sh` (research-0, 4 GB, 2 h) | +0.5-1 h |
| P3 | Mint fetch of the traded mints (report-only for HV4; Helius credits reported) | DEC-031 Am.3 item 6 (:215) | M | `python -m tools.arena_close_blocked fetch --bot <arm> --decisions-root /data/mal/arena/haiku-v4 --out-dir … --clock-ms 1792886400000 --boundary-ms <B> --ledger-root /data/mal/arena/ledger --ledger-bot <arm>` (research-0 job) | +0.5-1 h, parallel with P2 |
| P4 | QP checks the read on its exact files. Covers: seal statement; N and Holm; ex-best-day > 0 on every deciding sub-cell (`not_proposed_for_canary` false); the 14 read-pack disclosures; the winner's curse; WIS quoted | DEC-031 §2 step 1, Am.3 item 6; DEC-032 §1 item 2, §8 item 2 | QP | forced verdict file | +2-4 h |
| P5 | Copy the read record to fast-0: `ssh mal-research-0 cat /data/mal/arena/reads/<arm>.json` to a temp file; its sha256 must equal the RECORD line; then `install -m 0444 -o root -g root` to `/data/mal/arena/reads/<arm>.json`. Write it once (QP N9) | `hv4_live_c0.READS_DIR_CANON`, `read_record_problem` | M (sudo on fast-0) or H | HELM-ASK Part 2 step 2 | +15 min |
| P6 | Start the streams on fast-0: forwarder `pull` for `<arm>`, twin relay client, tie-out schedule. Serve refuses until the recorded PASS exists (`decision_forwarder` doc) | P46 DESIGN §5; R2.2 (c) | M | MiScusi jobs on fast-0 (< 2 GB) | +15 min |
| P7 | Keyless real-root DRY run, ≥ 1 h, real arm id (allowed after the record, R2.1; starts the 14-day window, accepted) | DEC-032 §2 checklist (dry run ≥ 1 h, 0 build errors) | H | HELM-ASK Part 2 step 5 | +1.5-2.5 h (parallel with P4) |
| P8 | Stage-2 DEC copy PR, records only: copy the skeleton to `DEC/DEC-0NN-hv4-<arm>-c0-canary.md`, fill the `FILL:gate-read/review` markers, and add the bare owner line (standing line, Am.1 item 3); QP OK on the exact head + reviewer verdict; merge | DEC-032 status line, §8 item 3; Am.15 item 5 (R2.2 declared before the first send) | B, QP, R, M | from `ARTIFACTS/lab/c0-drafts/hv4/` | +3-6 h (after P4) |
| P9 | Owner notice: the filled §2 paragraph (in-sample figures, best of N, worst case 0.25 SOL). Any "no" stops it | DEC-032 Am.1 item 3 ("Notice before the first send") | M → O | `miscusi_ask` / message | minutes |
| P10 | Owner: fund the fresh wallet (0.35-1.00 SOL) and tell Helm to act | DEC-032 §8 item 7; Am.1 item 3 ("Does not cover") | O | — | owner-bound |
| P11 | Helm: key (public key only reported), live config (`funded_lamports`, `end_ms` ≤ min(first send + 14 d, 1794143100000)), live drop-in, owner file `/etc/mal-hv4-c0/<arm>/CANARY_DEC.md` (exactly one owner line), `TIER`=`T1`, per-bot `LIVE_OK`=`<arm>`; `--once` live check with no `startup_refused` | DEC-032 §3 Files, §8 item 7; `hv4_live_c0.TIER_NAME`, `live_ok_problem`, `owner_line_problem` | H | HELM-ASK Part 2 steps 6-9 | +30-60 min after P10 |
| P12 | Stage-3 records PR R: fill the `POSTX:` values (install sha, manifest, config sha256, wallet public key, start balance, end_ms, dry-run job, md5 proof, A3 guard source, alert target, owner record sha256) before the first send | DEC-033 pattern (QP-PRERULING-1027-xorder) | B, M | records PR | 30 min (can be just before go) |
| P13 | Manager go: `systemctl start` of the live instance; read the startup line `mode=live bot=<arm> stake=50000000` | DEC-032 §8 item 8 | M → H | — | minutes |
| P14 | First live buy: the arm's next entry row after the start, when it passes every gate (age ≤ 5 s, caps, floor, twin/parity, A3, feed) | DEC-032 §3, §5 | — | — | not estimable (entry rate sealed) |

Critical path: P4 → P8 (QP, then DEC PR review) ≈ 3 to 6 h, running alongside P5 → P7 (≈ 2 h), then P10 → P11 (owner and Helm) → P13. With §2a done: about 4 to 7 h, plus however long it takes the owner and Helm to respond.

### 2c. Not needed for a C0 first buy (do not let these block)
- The confirmation read (DEC-031 Am.3 item 6, "No confirmation first"). It is reachable for B = 10-30 because B ≤ 11-03T00Z (c0-readiness §2c R1).
- V1/V2 (DESIGN item 7). These are for L and F books, at B + 24 h, and are not a canary precondition.
- The other seven siblings' reads. Each discloses this canary's first send instant (R2.2 (e)).

## 3. Funding and budget (owner capital memory; DEC-032 §3)
- **Capital on record:**
  - 1.29 SOL total (owner, 2026-10-09).
  - The H5 wallet (Helm) holds 0.298688847 SOL.
  - The owner said on 10-21: "We have the 1 sol coming in to be used to start."
  - Q50's pre-approval names 0.5 SOL of funding for Q50 (HANDOFF §0, 10-27).
- **Per HV4 canary:**
  - a fresh wallet (DEC-032 §9 `FILL:wallet_address`);
  - funded 0.35 to 1.00 SOL;
  - floor = funded − 0.25, never below 0.05;
  - worst case 0.25 SOL. The committed-loss cap is 0.20; the slack is 0.05.
- **Recommendation: fund each HV4 canary at the 0.35 SOL minimum.** The worst case is the same (0.25) at any funding, and C0 needs no more.
- **Fits the 1 SOL:**
  - one HV4 canary (0.35) plus the Q50 canary (0.5) = 0.85 SOL;
  - combined worst case 0.50 SOL.
- **Does not fit:** two HV4 canaries plus Q50 = 1.20 SOL > 1.0. That needs the owner's word on more SOL, or a choice made before any outcome is seen. The H5 wallet's 0.2987 is below the 0.35 minimum on its own.
- **Never plan beyond capital** (memory `owner-capital`). The canary stake is S = 0.05 SOL whatever the card size (0.25 deciding stake), so impact at 0.25 is not tested (DEC-032 §3).

## 4. Rules on this path that delay go-live without protecting money or validity (flags; the promotion gate is unchanged)
| # | Rule (citation) | Delay it causes | What it protects | Proposed change |
|---|---|---|---|---|
| F1 | DEC-032 §5 item 2 as built: every gap row latches `feed_break`, including forwarder transport `stall`/`disconnect`/`reconnect` (`hv4_live_c0.GAP_BREAK_KINDS` :820). The clear needs 10 min AND a root-owned review file naming the exact gap instant (`gap_clear_problem` :876-901) | Hours of no buys per Access blip, until the manager writes `/etc/mal-hv4-c0/gap-clear/<bot>.json` | Nothing beyond what the 5 s entry-age bound and the self-clearing `transport_stall` already protect. Gap trips are excluded from parity either way | **Done in code behind a constant: PR #1090** (60 s hold on data-loss gaps, transport exempt, as Q50's #1085). Needs the DEC-032 Am.5 text (hv4-amend / q50-c0-feed lanes) |
| F2 | DEC-032 §4: an owner line naming the fee when the priority differs from "the executor's installed one" (the PR #958 draft read the bridge's 500,000 default as installed) | One more owner round trip at event time | Owner-informed fee choice (the 10-05 lesson: lower priority can land slower) | QP ruling that for `hv4_c0` the compiled `PRIORITY_PIN` IS the installed priority (config refuses any other), so the clause does not trigger, and the fee goes in the owner notice. Or ask the owner for the line NOW (N8). Either way it leaves the critical path |
| F3 | DEC-032 §2 checklist: a dry run of ≥ 1 h at the install sha. Post-read it must run on real rows, because R2.1 forbids them before the read | ~1 h on the critical path | Config/stream errors on the real root | QP ruling: the ≥ 1 h dry run may be the pre-read synthetic-root run at the same sha (N4), plus a 15 min real-root dry smoke after the record. Saves ~45 min |
| F4 | QP #959 item 15: the removal of `hv4_c0_incomplete` only as its own diff with a fresh QP pass (read as "after everything, at event time") | A code PR + re-install after the PASS | Nothing after the read-record gate exists. The gate refuses any root without a recorded PASS | Merge the one-line flip BEFORE the read (N3), once N1/N2 have merged. Keep the separate diff and the fresh QP pass |
| F5 | `CANARY_DEC_SHA256` code pin (`hv4_live_c0.py:465`) | A code PR + re-install at event time | Same as the root-owned owner file | **Removed per the manager (start-gate PR)**: root-owned file, presence and exact line |
| F6 | QP pre-removal monitoring items that do not gate money: the runner dead-man, the backstop-exit flag in the C0 read, the measured gap-to-latch delay (#969 P7-e), A3 liveness acceptance (#970) | They can block the flip if they slip | Report quality. Money is bounded by the C0 caps, the floor and the bridge's own time-stop backstop | Allow them to land within 24 h after the first send, with a hard STOP if not, rather than block the flip. They are being built anyway (orders-proof and install-pack PRs) |
| F7 | One `LIVE_OK` and one owner file per host (`live_ok_problem` :428-431, `OWNER_DEC_PATH` :464) | A second PASS cannot go live at the same time | Nothing | Per-bot paths (start-gate PR) |
| F8 | Per-trade loss latch, 30% of S, manager clear (`PER_TRADE_LOSS_PCT`; a draft declaration). A BIG gap-through to −40% trips it | Hours of no buys until the manager acts | ≤ 0.02 SOL per trade, already inside the 0.10 daily stop | Low priority. Let it auto-clear at the next 00:00Z, or keep it and have the manager on call |

## 5. Tests and dry checks run by this lane (2026-10-30, research-0, /data/mal/venv)
- **Unit tests at main c92f23e3**, 13 files:
  - `test_hv4_live_c0*.py` (7 files), `test_arena_live_bridge.py`, `test_arena_bridge_dry_proof.py`, `test_bridge_dry_checklist.py`, `test_dec_golive_check.py`, `test_hv4_gap_relay.py`, `test_arena_live_parity.py`;
  - result: **463 passed, 1 failed, 137 subtests**.
  - The one failure is `test_dec_golive_check.py::test_dec033`. It predates this lane and is Q50-only: `tools/dec_golive_check.py` is hard-coded to Q50, and the test expects more than 0 open `FILL:owner` values in DEC-033, which now has 0 (DEC-033 was edited by the 10-27 stage-1 records).
- **`python -m tools.arena_bridge_dry_proof`**: `all_ok: true`, **20/20 checks**. These include:
  - chain;
  - tamper latch;
  - restart replays nothing;
  - cursor loss;
  - STOP/HALT;
  - decision age 4 s ok / 6 s refused;
  - caps;
  - frozen_sha pin;
  - end_ms;
  - floor on a fake balance;
  - no live state file.
  It is the generic profile, not `hv4_c0`.
- **Read-record compatibility (new, synthetic):**
  - **What ran:** the read pack's own fixture builder and `hv4-read.sh` in test mode, in this lane's scratch, giving case B: `VERDICT … STATUS PASS` on a synthetic book, RECORD sha256 b314ec9e…, equal to the pack's.
  - **What it showed:** the pinned scorer's write-once record carries every key the bridge's gate needs (`bot_id`, `status`, `read_boundary_ms` int, `attempts`, `model_ids` [], `frozen_sha`, `am15` dict, no `fail_label`, `boundary_source` natural, `pending` 0). `hv4_live_c0.read_record_problem` accepts it (with the canonical dir and frozen sha patched to the fixture's), and a second check with the pinned identity passes.
  - **The scope:** a real PASS record will not be refused for its format. Unpatched, it refuses `read_record_dir_not_canonical`, as designed off-host.
- **PR #1090 tests:** 402 passed, 140 subtests (§1c).
- **fast-0 presence check:** job #1837 (names only, §0 item 6).

## 6. Not measured, open questions
- **Not measured:** any HV4 outcome, entry rate, gap rate on the arena tape, real-root forwarder behaviour, landing, fill, P&L or gate statistic. Every time in §2 is an estimate.
- **Open:**
  - QP on F2/F3/F6;
  - who sends the HELM-ASK and when;
  - the capital decision if both arms PASS;
  - whether the hv4-amend lane's Am.5 text matches #1090's constants (60 s, transport kinds).
- **Seal contacts by this lane: none.** The fixture run used only synthetic paths under `/data2/mal/scratch/hv4-c0-ready-1030/`. Job #1837 listed path names only.

No book has cleared the promotion gate.
