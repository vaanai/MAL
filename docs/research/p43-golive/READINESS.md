# P43 GO-LIVE READINESS: from "read = PASS" to "first live C0 send", per book

Written 2026-10-26 (`date -u` 17:44:11Z at start) on mal-research-0 for manager17. Base: main `6a72511c` (#941).
**Desk only: records, no code change.** Nothing was sent and no owner or Helm text was relayed. Sources read: `DEC/DEC-032-arena-canary-template.md` (with Am.1 and Am.2), `DEC/DEC-031-forward-paper-arena.md` Am.15, `docs/HANDOFF.md` on main and on PR #942, PR #907's files (`ARTIFACTS/lab/c0-readiness-2026-10-25.md`, `ARTIFACTS/lab/c0-drafts/*`), PRs #908, #913, #915, #950 and #624, `docs/research/hv6/DESIGN.md` §9, `docs/runbooks/arena-live-bridge.md`, `docs/HOSTS.md`, Helm's channel `hunt29/p1/` (HELM-SHEET `0dc7597e…`, HELM-REPLY-1024, HELM-REPLY-1024-2, HELM-ASK-1024a, HELM-ASK-1025-1), the desk DEC copy `hunt29/p1/DEC-copy-h5v1q50-boostfloor-a1.md` (sha256 `87e462b6…`, 298 lines, 69 `<FILL at PASS>`), MiScusi notebook entries, and code in `tools/h5_arena_executor.py`, `tools/h5_executor.py`, `tools/arena_live_bridge.py`, `tools/hv4_live_c0.py`, `tools/arena_live_parity.py`, `tools/hv4_am15_read.py`, and `tools/probe_executor.py`.
**Not opened:** no Q50 or HV4 output, ledger, shadow dir, decision row, `bots.json` or job log (#1419, #1421, #1561, #1562); nothing in the DBC forward window, fwdv1/ or meta-capture; no wallet or key material.

**No book has cleared the promotion gate. No sentence here says any book makes money.** A C0 canary is an "unpromoted execution measurement" (DEC-032 §0). Every elapsed time below is an estimate (est) unless a source is cited. "Owner-bound" and "Helm-bound" steps run as fast as those people are online. Nothing here can make them faster.

---

## 0. Summary

| Book | Earliest PASS instant | Live path today | Best case, PASS → first send, if the §5 "now" items are done first | Same, if they are NOT done | Live window left |
|---|---|---|---|---|---|
| **Q50** `h5v1q50-boostfloor-a1` | B ≥ 2026-10-30T00Z (Am.11 count tool's write-once file) | **Built and dry-run** (`tools/h5_arena_executor.py`, install `0526d5cd`, Helm dry run PASS 10-24) | **~7–11 h** | ~11–17 h | to 2026-11-06T00Z (`SEAL_END_MS`), so ≤ ~6 days |
| **HV4** X/F arms (CON-X, BIG-X, CON-F, BIG-F only) | read recorded ≥ B + 24 h; #1591 runs daily at 01:30Z from 10-30, so the earliest is ~2026-10-31T01:30Z | **Not live-capable.** `hv4_c0` profile refuses every live start (`hv4_c0_incomplete`, `tools/hv4_live_c0.py:244`); adapter items 6–8 and 10–13 are not built; **no WIS writer**; **no decision-row transport to fast-0** | ~10–14 h | **3–5 days** | to HV4 END 2026-11-08T13:05Z (entries ≥ 11-08T00Z do not count) |
| **DBC m10 → P18** | m10 PASS ≥ 2026-11-01T00:10Z is a **confirmation that licenses only the P18 paper book**. P18's own gate read is 2026-11-09T00Z at the earliest (#950 plan B′), 11-11 (B), 11-13 (A) | **None.** No DBC buy/sell builder. DEC-032 §1 item 5 says "No third path is built", so this needs a DEC-032 amendment | not before ~mid/late November | same | P18 H = 14 from its clock |
| **HV6** F/X arms (L is not canaried, DESIGN §9) | not registered yet; T0 target 10-30; B ≥ 2026-11-04T00Z, read after the 12 h exits, so ≥ ~11-04T12Z | **None.** Would be the bridge with a new `hv6_c0` profile, plus the same transport gap as HV4 | ~1–2 days after a PASS if built from the HV4 adapter | 4–6 days | runner halt 1794355200000 = 2026-11-11T00Z |

**The five gaps that would delay a live start most after a PASS (§4 ranks them all):**
1. **HV4 has no live path.** The adapter is 2 of ~5 PRs. It needs 3–5 builder-days, and the HV4 window closes 11-08. Fix: build items 6–8 and 10–13 now, on synthetic fixtures only (QP R2.1 allows that before a read).
2. **HV4 decision rows live on mal-research-0, and the bridge runs on mal-fast-0** (`docs/runbooks/arena-live-bridge.md:3,27`; `scripts/research/hv4-runner-job.sh`). Nothing moves rows between the hosts. A slow hop also adds latency to both entries and exits, which is the same class of leak as the 10-05 probe. Fix: a chain-preserving forwarder with measured latency, or the owner and Helm agree to a bridge on research-0.
3. **The Q50 executor reads the owner line from `DEC/` in its *installed* tree** (`h5_arena_executor.canary_record_refusal(book.bot_id, h.repo_root())`, `tools/h5_arena_executor.py:308,418`; `repo_root()` = the install dir, `tools/h5_executor.py:911`). The installed `0526d5cd` tree can never contain a DEC copy that merges after the read, so a live start refuses with `canary_record_for_bot_id_missing`. PR #907's draft Helm ask says 0526d5cd "qualifies". **It does not.** The fix needs a re-install at the DEC-copy merge sha, with its own md5 EQUAL replay (QP C2) and a 1 h dry run, which adds 3–6 h on the critical path. Pre-stage it, or get a QP pre-ruling now (§5 N2).
4. **HV4 worst-in-slot is unbuilt.** DEC-031 Am.15 item 6's trade export and `<bot_id>.wis.json` writer were left out of #915 ("owed as a follow-up"). Without them the read says "worst-in-slot not measured", and **no HV4 canary can be proposed at all**. Fix: build it now (synthetic tests), ideally before 10-30T00Z as QP-RULING-1025 R3 placed it.
5. **Owner and Helm presence, plus a stale wallet-wide STOP.** Funding, the fresh wallet key, `/etc/mal-h5/LIVE_OK` (needs Vaan's own word), the arena gate files and the decision on the old `/var/lib/mal-live/STOP` (H5 health #1645: `STOP=yes`) are all owner- or Helm-bound. Fix: send the "pre-stage" ask in §6.0 now, so that the key exists unfunded and Helm is on call for 10-30.

---

## 1. Q50 `h5v1q50-boostfloor-a1` (frozen `a5f41370`, clock 2026-10-25T00Z, deciding stake 0.30 SOL)

Context:
- Seal: COVERED by the EXP-024 Note under Am.10. QP-RULING-1025-q50-seal-live (sha256 `962d1e51…`) applies, under its conditions C1–C8.
- Fee: compiled default, 55,000 buy / 55,000 sell / 150,000 escalated priority plus the 5,000 base fee (`tools/h5_arena_executor.py:148-150`).
- The standing owner OK (DEC-032 Am.1 item 3, notebook `n_ecHJTWJ5nmrttQ`) covers the C0 OK. It does **not** cover funding or Helm's install.
- Risk: the A3 guard. QP survival to 10-30 is ~0.38 (HANDOFF §3). A HALT ends Q50, and the successor S* is a new clock.

| # | Step | Who | What exists now (file @ commit) | MISSING | Elapsed (est) |
|---|---|---|---|---|---|
| Q1 | B instant set | host job (automatic) | Am.11 count tool + read tools, #884 merged `659f33e8`. Daily score #1543 at 00:20Z (HANDOFF) | Manager: confirm before 10-30T00Z that the count-tool job is scheduled host-side, not by a session cron (CLAUDE.md lesson 10-15). Use job status only, never output | 0 (00:00Z) |
| Q2 | Read: PASS on the deciding cell, multiplicity block, ex-best-day (`tools/arena_h5_legs.py`), worst-in-slot (#884 "DEC-032 worst-in-slot figure") | manager + scorer | tools on main | nothing known | 1–2 h (DEC-032 §8.1) |
| Q3 | QP forced verdict on the exact read files. It includes the seal sentence (template: ruling §4), taints and N | QP (plain agent, tool budget, forced verdict) | ruling text for the seal sentence | the verdict itself | 2–4 h (§8.2) |
| Q4 | Mint fetch of traded mints; close-blocked count (Am.2 item 6; required for H5) | builder or manager, MiScusi job, Helius | `tools/arena_close_blocked.py` (main) | **a pre-written job script** with a credit cap and a notebook template (§5 N3) | 1–3 h, parallel with Q3 |
| Q5 | Sell builder pin + simulate ref | — | `SELL_BUILDER_PIN`, `sell_pin=ok` (HELM-REPLY-1024). Simulate records #821 `486444ef`, `ARTIFACTS/lab/arena-sell-simulate-2026-10-23/` (Token 12rgUrX6…, Token-2022 EU1MHvQv…; holder full sell ok). QP OK'd it as `FILL:simulate_ref` on 10-23 | **QP C7 (10-25) was written after them:** the mints must come from outside the Look-2 class. Nobody has confirmed that these two do. Fix: a QP check now, or a rerun on two September graduations (§5 N4) | 0 if done now; 1 h if not |
| Q6 | Numbered DEC copy PR: every FILL filled, standing line quoted, `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1` on its own line; QP OK on the head + reviewer verdict; manager merges | builder, QP, reviewer, manager | desk copy (298 lines, 69 FILLs) + `ARTIFACTS/lab/c0-drafts/DEC-copy-q50-corrections.md` (@36ab58e1) | One consolidated, pre-reviewed draft. Today, item 8 still says "`FEED_NOTE_MERGED` is False on main" (stale: it is True), and item 9 must be replaced from the corrections file. **The twin double-count §11 declaration with its bps** (~40 bps per round trip in live's favour, QP estimate) is not yet in the copy. It must not sit in `DEC/` before the read: the executor globs `DEC/DEC-*canary*.md` (§5 N1) | 2–4 h → **~1 h** if N1 is done |
| Q7 | **Re-install at the DEC-copy merge sha X** (needed because of gap 3): manifest at X, md5 EQUAL replay at X (C2), Helm reinstall (sheet §C/§D), 1 h dry run (Am.2 §2.3) | manager (jobs), Helm | md5 scripts `hunt29/p1/jobs/md5-replay-q50-*.sh`; manifest pattern (#1482). The executor files have **no diff** between `0526d5cd` and main `6a72511c` (only `scripts/mal-fast/a3-applied-alert.sh` changed) | A sheet revision naming X; either a QP pre-ruling that an X whose diff from 0526d5cd over the installed code files is empty inherits #1481's EQUAL, or the replay pre-staged to run on the merge (§5 N2) | **+3–6 h**; ~2 h with N2 (manifest + Helm + dry run) |
| Q8 | Owner notice (Am.1 item 3: the filled §2 paragraph) + one-line "standing OK used", plus the asks for funding and a Helm instruction | manager → owner (`miscusi_ask`) | §2 paragraph pre-filled in the desk copy; §6.1 below | owner availability | minutes to owner-bound |
| Q9 | Wallet: Helm generates a fresh key (public key only reported). The owner funds 0.35–1.00 SOL and records the tx and starting balance | Helm, owner | runbook `docs/runbooks/probe-wallet.md`; HELM-SHEET §F.1 | the key (can be made **unfunded now**, §6.0) | owner-bound + ~15 min |
| Q10 | `live.json` filled (`funded_lamports`, `end_ms` ≤ 1793923200000) and installed; live drop-in; watchdog re-enabled with wallet + `ARENA_WATCH_FUNDED_SOL` | manager (values), Helm (install) | draft `ARTIFACTS/lab/c0-drafts/live.json.DRAFT-NOT-INSTALLED` @36ab58e1; HELM-SHEET §F.2, §G | Helm's HELM-REPLY-1024-2 still says "p155 fee pin". The ask must correct it to the default pin (§6.2) | ~30 min |
| Q11 | Twin writer + daily tie-out as MiScusi jobs on fast-0, re-pinned to X. The twin writer writes `twins.jsonl.run.json` before the first buy | manager | `hunt29/p1/jobs/twin-writer-loop.sh`, `tieout-daily.sh` (pinned to 0526d5cd) | re-pin to X (minutes) | 15–30 min |
| Q12 | Gate files (root 0644): `/etc/mal-h5-arena/TIER`=`T1`, `/etc/mal-h5/LIVE_OK` (empty), `/etc/mal-h5-arena/LIVE_OK`=bot_id. Written decision on the old `/var/lib/mal-live/STOP`/`HALT`. `--status` must print `live_start=none … c0_guard=ok sell_pin=ok` | Helm (owner's word for LIVE_OK) | HELM-SHEET §F.5–6 | the owner's word; the manager's written STOP decision (§6.2 item 8) | ~15 min |
| Q13 | Manager pre-go: SEALS-BRIEF and HANDOFF wording "canary outputs are never opened" changed to name DEC-024's canary (ruling C8). Without that, the manager may not read the parity or tie-out files of the canary they run. Then a notebook entry and console line | manager | corrections file "Follow-up edit at the first send" | the edit | 10 min |
| Q14 | Go: `systemctl start mal-h5-arena-executor`. Startup lines `mode=live … user=<canary pubkey>` and `feed_start=verified` | manager go, Helm start | unit installed | — | minutes |

**Critical path**, if Q1–Q3 PASS at 10-30T00Z: Q2 → Q3, with Q4 and Q6 running alongside Q3. Then Q6 merge → Q7, with Q8–Q10 running alongside Q7 → Q11–Q14. With N1, N2 and N4 done: about **7–11 h**, if the owner and Helm are online. 10-30T00Z is 10-29 17:00 PT. Without them: about 11–17 h. Each hour lost costs about 1/144 of the remaining Q50 window.

**Probe exit leak (asked):** the 10-05 leak (stop-loss fired at −31% to −74%, fills −326 to −780 bps) came from a 5 s exit poll plus a ~1 s sell build. The fast exit path landed in **#315** (merged 2026-10-05T17:23Z): opt-in `exit_poll_ms` (floor 200 ms), batched vault read, processed quote, sell of the buy-meta amount (`tools/probe_executor.py:74,1231-1236`). The DEC-032 sell ladder (min_out 0.85 then 0.65) is in the H5 path (DEC-032 §3/§4).
- **Q50 does not use a poll-triggered exit.** v1 exits on a pre-armed slot timer at s0 + 330 s. The sell is built and signed `arm_lead_ms` = 2,000 ms ahead and rebroadcast every 400 ms (`tools/h5_executor.py:101,333-334`).
- The `poll_s: 5.0` in the draft `live.json` appears only in the start record (`tools/h5_executor.py:1035`), not in the exit loop.
- The P6 halt (exit gap worse than −300 bps over the first 10 stop-loss exits) and the exit-gap writer are the live checks.
- **HV4 (bridge) uses the fast path** (`arena_live_bridge.py:961` passes `exit_poll_ms = warm_ms`, default 1,000 ms, clamp 500–2,000). But its exits are *decision rows written on research-0* (gap 2), so the transport hop adds to exit lag. Set `warm_ms` 500 and measure the hop before any HV4 send.

---

## 2. HV4: X or F arm (frozen `9ed4c5ff`, clock 2026-10-25T00Z, deciding stake 0.25 SOL, END 2026-11-08T13:05Z)

Context:
- Only CON-F, CON-X, BIG-F and BIG-X may be canaried (QP-RULING-1025 R2.2(a); `hv4_live_c0` refuses -L/-P/-R).
- The L arms placed no buys on day 1 (HANDOFF #942: "the X arms carry the read").
- No bridge process reads an HV4 decision row before that arm's read is recorded (R2.1). Every pre-read build and test is synthetic or a StubModel root.

| # | Step | Who | What exists now | MISSING | Elapsed (est) |
|---|---|---|---|---|---|
| H1 | B instant + read recorded (Am.15) | host job #1591 (daily 01:30Z from 10-30); manager runs `tools/hv4_am15_read.py --record` at ≥ B + 24 h | #915 `305d9c0a`, #920 `413f25a6` | — | read ≥ ~10-31T01:30Z; 1–2 h |
| H2 | QP forced verdict + seal statement (no H5/Look-2, C1-NF or D2–D4 class; lane H off) | QP | ruling R1–R3 | — | 2–4 h |
| H3 | Worst-in-slot (Am.15 item 6): export the scored trade set, run `arena_worst_gaps`, then `worst_in_slot_cells`, then write `<bot_id>.wis.json` once | host MiScusi job | `tools/arena_worst_gaps.py`, `arena_score.worst_in_slot_cells` (main) | **the export + writer + job script (not in #915, "owed as a follow-up")**. Without them: "not measured", no canary (§5 N5) | 0.5–1 day to build; 1–3 h to run |
| H4 | Mint fetch (report-only for HV4) | MiScusi job | `tools/arena_close_blocked.py` | job script (same as N3) | 1–3 h ∥ H2 |
| H5 | Live adapter: items 6 twin adapter at S (55/55/150 schedule, nofail 1.9 s, E1 excluded decides); 7 parity wiring P1–P7 into the bridge gate; 8 tie-out exporter for bridge state; 10 feed-gap + A3 halts; 12 systemd unit + install pack; 13 md5 "orders equal rows" proof on a synthetic log. Plus the DEC-032 §3 sell ladder (0.85 → 0.65, never 0, as compiled constants inside the pin); the exact `OWNER_CANARY_CONFIRMED: bot_id=` match; the F-arm model-id check (`claude-haiku-5-5`, R2.2(b)); removal of `hv4_c0_incomplete` | builders (4–5 PRs), QP + reviewer each | items 1–5, 9 merged: #908 `e2830203`, #913 `a23bd7ab` (C0 table, S rule, fee pin, frozen_sha pin, universe, sell-builder pin, escalation, sealed-path read guard). Scope table: `ARTIFACTS/lab/c0-readiness-2026-10-25.md` §7 | everything in the step column (§5 N6) | **3–5 days** of builds |
| H6 | **Decision-row transport** research-0 → fast-0, or a bridge on research-0 | builder + manager; owner + Helm if a key goes on research-0 | nothing. The runbook says Helm "sets the real path" of the arena root on fast-0. `docs/HOSTS.md` still says "No send, no signer, no wallet" for both hosts | a design and a measured hop latency (§5 N7). The bridge refuses entries older than 5 s (`MAX_DECISION_AGE_MS`), and the match test needs landing p50 ≤ 1.9 s from the decision instant | 1 day to build |
| H7 | HV4 DEC-032 copy for the passing arm, with R2.2 (a)–(g) declarations: read-only bind of `decisions/<bot_id>/` only, rows only after the read instant, siblings untouched, confirmation exposure. Fee: 55/55/150 with escalation after 2 reverts or 345 s. Bind-mount residual (from #913). Universe = the read's | builder, QP, reviewer | none (only the Q50 desk copy exists) | a pre-written arm template (§5 N8) | 2–4 h → ~1 h with N8 |
| H8 | Helm: bridge install on fast-0 (`/opt/mal-arena-bridge/<sha>/`, `/etc/mal-arena-live/`), 1 h dry run, md5 proof. Dry run before the read: synthetic root only (R2.1) | Helm, manager | runbook `docs/runbooks/arena-live-bridge.md` (DRY only) | a HELM-SHEET for the bridge (§5 N9). A keyless synthetic-root dry install can be done **before** the read | 2–3 h, or ~1 h if pre-installed |
| H9 | Wallet (a 2nd fresh wallet; the owner OK'd 2 wallets on 10-16), funding 0.35–1.00, gate files under `/etc/mal-arena-live/` (`LIVE_OK`, `TIER`=`T1`), the wallet-wide STOP decision (the bridge's default `stop_file` is `/var/lib/mal-live/STOP`, `arena_live_bridge.py` Config) | owner, Helm | — | as Q9/Q12 | owner-bound |
| H10 | Owner notice (§6.1 with HV4 fields), go, start | manager, Helm | — | — | minutes |

With H3 and H5–H9 built and pre-installed before 10-31: about 10–14 h after the read is recorded. That means a first send around 10-31 afternoon at the earliest, and ~8 days of live before END. Without them: 3–5 days, so a first send around 11-03 to 11-05 and 3–5 days of live. A confirmation (C0 → C1) is possible only if B1 ≤ 2026-11-03T00Z (ruling R1).

---

## 3. DBC m10 → P18, and HV6

**DBC.** The m10 read (≥ 2026-11-01T00:10Z, `scripts/research/dbc-m10-read-job.sh A`) is a **confirmation only**. "A PASS licenses a forward paper book (P18), nothing more. It does not open the C0 canary path" (HANDOFF §1).

| # | Step | Who | Exists | MISSING | Elapsed (est) |
|---|---|---|---|---|---|
| D1 | m10 read PASS | manager | tag `dbc-m10-forward-v1-am1` (7d5be9e5) | — | 11-01 |
| D2 | P18 build B1 live process (pruning), B2 scorer, B3 registration pack + A3-style guard | builders + QP + reviewer | staging PR #950 (draft, head `2ac6fca0`): DECLARE, MEASURE, STAGING | B1–B3 (plan B: built before 10-31 on synthetic and exploration hours only) | 3 PRs |
| D3 | P18 clock, P0, dry score, registration | manager | plan B: clock 2026-11-06T00Z, B′ 11-04 | — | — |
| D4 | P18 **own** gate read PASS | manager + QP | — | — | ≥ 11-09 (B′) / 11-11 (B) / 11-13 (A) |
| D5 | **DEC-032 amendment for a third executor path** (DBC + DAMM v2 strand exit). §1 item 5 says "No third path is built" | manager, QP, reviewer | none | the amendment text | 0.5 day |
| D6 | DBC live executor: buy/sell builder for the DBC program, Token/Token-2022, sell + close ATA, the strand action on DAMM v2, simulate, twin/parity/tie-out wiring, install pack | builders | `tools/dbc_tx_probe.py` is read-only getTransaction; no builder | all of it | **1–2 weeks** |
| D7 | DEC copy, owner notice, wallet, Helm | as Q6–Q14 | — | — | as Q50 |

First DBC live C0: not before ~11-20 even if everything passes. **Recommendation:** do not start D5/D6 before the m10 PASS. m10 is in-sample, the best of 23 cells, on about 2.2 Swap2-era days. Write only a 1-page D6 scope after 11-01.

**HV6.**
- Not registered. Its final sha waits on #943 (playbook fields), then the item 6 re-run and the #940 pack.
- Deciding arm = L, which is **not** canaried (DESIGN §9: a nightly playbook is a prompt change). F and X are eligible after their own PASS. A frozen L snapshot is a new bot_id with its own clock (C1–C6), so it cannot go live before mid-November.
- Path: the same bridge with a new `hv6_c0` profile. `hv4_live_c0` pins HV4's frozen sha and the `hv4-` arms, so it cannot be reused unchanged. The decision root is `/data/mal/arena/haiku-v6/` on research-0, so gap 2 applies as well.
- Cost line: DESIGN §9 C6 quotes 2 × 505,000-lamport sends. The bridge pin is 55/55/150, so the fee gap must be declared.
- Earliest live: about 11-05 to 11-06 if the HV4 adapter and transport exist and `hv6_c0` is built after HV6's final sha is known (~1 day). The window ends 11-11T00Z.

---

## 4. Critical gaps, ranked by delay to a live start after a PASS

| Rank | Gap | Books | Delay if open at PASS | Fix | Who |
|---|---|---|---|---|---|
| 1 | HV4 adapter items 6–8, 10, 12, 13 + sell ladder + owner-line match + F model-id check; `hv4_c0_incomplete` still refuses | HV4, HV6 | **3–5 days** (window ends 11-08) | N6 now, synthetic only | builders, QP, reviewer |
| 2 | No decision-row transport research-0 → fast-0 (or bridge host decision) | HV4, HV6 | ≥ 1 day + an unmeasured latency risk to the 1.9 s leg and exit lag | N7 now: forwarder that ships chain-verified lines, plus a latency test on a synthetic log; or the owner + Helm put a bridge on research-0 | builder; owner/Helm for the host choice |
| 3 | Am.15 item 6 WIS export + writer missing. Without them HV4 is "not measured", so there is no proposal at all | HV4 | 0.5–1 day, and a build-after-verdict appearance | N5 now, before 10-30T00Z | builder, QP |
| 4 | Executor reads the owner line from `DEC/` in the installed tree, so a re-install at the DEC-copy merge sha is needed, with md5 EQUAL + 1 h dry run | Q50 (and the HV4 design, if built the same way) | 3–6 h | N2: QP pre-ruling on md5 inheritance + pre-staged manifest/replay jobs + sheet text; the HV4 adapter should read the owner line from a root-owned file instead | manager, QP, Helm |
| 5 | Owner/Helm-bound acts: fresh key, funding, `/etc/mal-h5/LIVE_OK` (Vaan's word), gate files, the stale wallet-wide `/var/lib/mal-live/STOP` | all | 0 h to a day (night, travel) | §6.0 pre-stage ask now: key generated unfunded; Helm on call for 10-30 00–06Z; the STOP decision written now | manager → owner → Helm |
| 6 | No numbered DEC copy draft that is consolidated and pre-reviewed (desk copy stale on item 8 and item 9; twin double-count bps not declared) | Q50; HV4 has none | 2–3 h | N1 (Q50), N8 (HV4) | builder, QP, reviewer |
| 7 | HV4/HV6 bridge has no Helm sheet or keyless install on fast-0 | HV4, HV6 | 2–3 h | N9: sheet now; synthetic-root dry install before the read | manager, Helm |
| 8 | Mint-fetch job not pre-written | Q50, HV4 | 1 h | N3 | builder |
| 9 | Sell-simulate mints not checked against C7 | Q50 | 1 h | N4 | QP |
| 10 | DBC third path (DEC-032 §1.5) + DBC builder | DBC | weeks | after 11-01 only | manager |

---

## 5. Build items that can start NOW without touching any seal

All of these are synthetic, records-only, or use exploration or public chain data. None opens a Q50/HV4 output, a ledger, a decision row, the DBC forward window, fwdv1/ or meta-capture. Sizes are estimates.

| Id | Item | Size | Seal note |
|---|---|---|---|
| N1 | Q50 DEC copy, consolidated: desk copy + corrections file + the twin double-count §11 declaration (bps from QP ruling §6, labelled an estimate) + item 8 "FEED_NOTE_MERGED True at 0526d5cd" + Am.2 owner deposit addresses field. Kept at `ARTIFACTS/lab/c0-drafts/` (not `DEC/`, so the executor glob never sees a placeholder). QP + reviewer pre-pass on the text, so that only read figures are left at PASS | S: 2–3 h builder + reviews | records only |
| N2 | Re-install plan for the DEC-copy merge sha: (a) a QP pre-ruling on whether an install sha X whose diff from `0526d5cd` touches only `DEC/`, docs and files outside the manifest's code set inherits #1481's EQUAL (or must re-run); (b) the manifest and md5 replay job scripts parameterised by X, ready to submit on merge; (c) HELM-SHEET §F revision with the re-install step. Alternative (M, ~1 day + reinstall now): the executor reads the canary record from a root-owned `/etc/mal-h5-arena/CANARY_DEC.md` whose sha256 must equal the merged git blob. This is a pinned-code change needing its own md5 replay. Only if QP prefers it | S (a–c) / M (alt) | records; replay uses the 09-20 exploration input already used by #1481 |
| N3 | Post-PASS mint-fetch job script over `tools/arena_close_blocked.py` (Helius credit cap, notebook template with job id, counts and credits) for H5 and arena books; tested on a synthetic mint list | S: 1–2 h | runs only after a PASS |
| N4 | QP check of the #821 sell-simulate mints against C7, or a rerun of `tools/arena_sell_simulate.py` on one Token and one Token-2022 September graduation (public RPC, 0 Helius) | S: 1 h | QP decides; chain facts only, no H5 outcome |
| N5 | HV4 Am.15 item 6: trade-set export + `.wis.json` writer + host job script; synthetic tests; QP + reviewer before 2026-10-30T00Z | M: ~300 lines + tests, 0.5–1 day | synthetic only (R2.1/R3) |
| N6 | HV4 adapter PRs 3–5: (3) twin adapter + parity wiring + sell ladder; (4) tie-out exporter + feed-gap/A3 halts + owner-line match (root-file source) + F model-id check; (5) unit + install pack + md5 "orders equal rows" proof; remove `hv4_c0_incomplete` last | L: ~1,500–2,000 lines + ~1,000 test lines, 3 builder PRs, 3–5 days | synthetic fixtures / StubModel root only (R2.1) |
| N7 | Decision-row transport: design note + forwarder (tail on research-0 → fast-0 append, chain verified by the bridge, a gap row on a stall) + a hop-latency measurement on a synthetic log over the real Cloudflare Access path. Or: a written owner/Helm question on a bridge on research-0 (HOSTS.md says no keys there) | S design 2 h; M build ~1 day | synthetic only |
| N8 | HV4 arm DEC-copy template (X and F variants) with R2.2 (a)–(g), fee/escalation, bind residual, universe-equal text | S: 3–4 h | records only |
| N9 | HELM-SHEET for the bridge on fast-0 (as the Q50 sheet: provision, pinned install, binds, keyless 1 h dry run on a synthetic root) | S: 2 h + Helm 1–2 h | synthetic root |
| N10 | `hv6_c0` profile design (frozen-sha pin from HV6's final sha, F/X arms only, decision root) | S design now; M build after HV6's sha is final | records only |
| N11 | Manager housekeeping text: wallet-wide STOP decision, alert target (watchdog Discord), SEALS-BRIEF edit wording (applied at the first send), owner pre-stage ask (§6.0) | XS: 30 min | — |

Not recommended now: D5/D6 (DBC executor), before the m10 PASS.

---

## 6. Pre-drafted texts. NOT SENT. Send only when the listed preconditions hold.

### 6.0 Pre-stage ask to the owner (can be sent now; no read result needed)

> Vaan, getting ready so a passing book can go live the same day. No book has passed; the first reads are 10-30 (Q50) and 10-31 (HV4). Three things would save hours later. (1) May Helm generate a fresh canary wallet key on mal-fast-0 now, unfunded, as for the probe wallet (public key only reported to us; we never see the key)? (2) Could you or Helm be reachable on 10-30 from 00:00Z to about 10:00Z (10-29 5 PM to 3 AM PT) for funding and the install steps? (3) The old probe wallet's `/var/lib/mal-live/STOP` is still in place and would also block the new canary. OK for Helm to move it to `/root/disabled/` when we go live (the old executors stay keyless and disabled)? Nothing is funded or sent by any of this.

### 6.1 Owner notice and OK request (at PASS; Q50 values pre-filled, `<…>` filled from the read and the merged DEC copy)

Precondition: Q2–Q7 done (PASS, QP OK, DEC copy merged with the line `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1`, re-install dry run at X clean).

> Vaan, using your standing OK of 2026-10-23 for book `h5v1q50-boostfloor-a1`. It passed the arena screen read at `<read_instant>` on its deciding cell (0.30 SOL stake, 1.9 s leg). All figures here are IN-SAMPLE and selected: mean `<mean>` SOL per trade, CI90 lower bound `<ci_lo>`, one-sided p `<p>`, ex-best-day total `<ex_best_day>` SOL, worst-in-slot mean `<wis_mean>` with CI90 lower bound `<wis_lo>`, `<n>` attempts on `<d>` days. This is a screen, the best of `<N>` books. If no book had an edge, the chance of at least one screen pass would be up to 1 − 0.95^N. It is not a profit claim and it is not confirmed (the confirmation needs at least 100 more attempts on at least 5 more UTC dates).
> What runs is a small live **execution measurement**: 0.05 SOL per trade, at most 2 open, at most 30 buys a day. It stops itself at 0.10 SOL realized loss in a day and at 0.20 SOL total loss (open positions counted as lost). The worst case is 0.25 SOL lost; the wallet never goes below funded − 0.25 SOL. The fee per send is 55,000 + 5,000 lamports (150,000 + 5,000 on an escalated sell), from `tools/h5_executor.py:267-268` at install `<X>`. It halts itself on a pump program redeploy, a feed gap, a decision-log break, missing or stale loss/balance/twin/parity state, or live fills drifting from paper. It ends by 2026-11-06T00:00Z at the latest. It never raises the stake by itself beyond the C0/C1 ladder you pre-approved, and C1 needs the confirmation read and 100 matched live trades (not reachable before 11-06 in practice). The live wallet is public and watched in real time; that will be disclosed in the confirmation read.
> Two things only you can do: (1) fund the canary wallet `<public key from Helm's reply>` with 0.35–1.00 SOL (we suggest 0.50; instructions below); (2) tell Helm to carry out HELM-ASK-`<date>`-q50-c0-live.md. Reply "no" at any time to stop it.

For an HV4 arm, replace the book, the deciding stake (0.25 SOL), the fee citation (`hv4_live_c0.PRIORITY_PIN` and `arena_live_bridge.BridgeExecutor._priority` at `<X>`), the end (≤ first send + 14 d and not after 2026-11-08T13:05Z), and drop the ladder sentence (Am.2 covers Q50 only).

### 6.2 Helm install request (HELM-ASK format; file `HELM-ASK-<MMDD>-q50-c0-live.md` in `hunt29/p1/`)

Precondition: 6.1 sent and the owner has told Helm to act.

```
# HELM-ASK-<MMDD>-q50-c0-live — Q50 C0 canary: re-install at X, wallet, live config, gate files (from manager17, <date -u>)

Vaan approved this in his own message (<quote, time>, notebook <id>). Sheet: HELM-SHEET.md sha256 <new sheet sha> (section F, revised for the re-install).
Host: mal-fast-0 only. Nothing here touches ufw, sshd_config, cloudflared, Cloudflare Access or /opt/miscusi. Never print a key.

## 1. Re-install at the install sha X (needed: the executor reads the canary DEC from its own installed tree)
- Install sha: <X> (merge of the Q50 DEC copy, PR #<n>). The executor code is unchanged from 0526d5cd (diff empty over the code files).
- Manifest: /home/claude/h5-arena-manifest-<X>.txt, <lines> lines, sha256 <...>. md5 decision-equivalence at X: EQUAL, job #<...> (or QP pre-ruling <ref>).
- Please redo sections C and D at X (new $D/<X> dir, `current` symlink, venv, unit, binds). Confirm an empty manifest diff and the unit sha256.
- Run the 1 h keyless dry run (section E) and send back the counts table and the `--status` line.

## 2. Wallet (Vaan's instruction)
- Generate a fresh canary key as for the probe wallet (docs/runbooks/probe-wallet.md): /etc/mal-h5-arena-key/canary-wallet.json, root:root 0400. Report the PUBLIC key only. (Skip if already made unfunded per the pre-stage ask.)
- Vaan funds it from his own address. Please record the funding tx signature and the starting balance (finalized) in your reply. Withdraw stays with you.

## 3. live.json (root:root 0644) from ARTIFACTS/lab/c0-drafts/live.json.DRAFT-NOT-INSTALLED at X
- Fill: funded_lamports = <funded, lamports>; end_ms = <ms, <= 1793923200000 = 2026-11-06T00:00Z>. Remove the three "_" notes.
- **Fee pin correction:** priority_pin stays {"profile":"default","buy":55000,"sell":55000,"escalated":150000}. NOT p155 (your 1024-2 note): any other value refuses the start (priority_pin_mismatch). This is the manager's decision of 10-25, QP-RULING-1025 section 5.
- Send back `sha256sum /etc/mal-h5-arena/*.json`.

## 4. Live drop-in
`sudo install -D -m 0644 $P/<X>/scripts/mal-fast/mal-h5-arena-executor-live-pinned.conf /etc/systemd/system/mal-h5-arena-executor.service.d/live.conf && sudo systemctl daemon-reload`. Do not start.

## 5. Watchdog
watch.env: ARENA_WATCH_WALLET=<public key>, ARENA_WATCH_FUNDED_SOL=<funded>. `systemctl enable --now mal-h5-arena-watch.timer`. Send one test alert.

## 6. Twin writer and tie-out
The manager submits both as MiScusi jobs on fast-0, pinned to X. Please only confirm that /srv/mal-h5-arena/{twins,tieout} are bound read-only into the unit.

## 7. Gate files, root:root 0644 (Vaan's word covers these)
- /etc/mal-h5-arena/TIER = T1
- /etc/mal-h5/LIVE_OK (empty)
- /etc/mal-h5-arena/LIVE_OK = h5v1q50-boostfloor-a1 (one line)
Then `--status` with live.json must print: live_start=none arena_live_ok=valid shared_live_ok=valid arena_tier=T1 c0_guard=ok sell_pin=ok. Send the line.

## 8. Wallet-wide STOP/HALT
Manager's written decision: <move /var/lib/mal-live/STOP (and HALT if present) to /root/disabled/ with a date suffix | keep>. The old probe and H5 executors stay keyless and disabled. Report `ls -l --time-style=full-iso /var/lib/mal-live/` before and after.

## 9. Start, on the manager's go only
`sudo systemctl start mal-h5-arena-executor`. Send the two startup lines (mode=live ... user=<public key>, feed_start=verified) and NRestarts after 10 min.

## Stop / kill (standing)
STOP (no new buys), HALT (freeze), or `rm /etc/mal-h5-arena/LIVE_OK`. The manager takes none of these on a host unit; you do.

## Not asked
No firewall, sshd, cloudflared or Cloudflare change. No C1 files (TIER ceiling / LADDER_UNLOCK). No change to /opt/miscusi.
Reply as HELM-REPLY-<MMDD>-q50-c0-live.md in this folder, pointer in MALsession1.
```

The HV4 variant is the same shape with these differences: `/opt/mal-arena-bridge/<X>/`, `/etc/mal-arena-live/{live.json,LIVE_OK,TIER}`, a **second** fresh wallet, the read-only bind of `decisions/<bot_id>/` only (R2.2(c)) on whichever host N7 decides, and no ladder files. It cannot be finalised until N6, N7 and N9 exist.

### 6.3 Wallet funding instructions for the owner (Phantom)

1. Fund only after Helm's reply file names the canary **public key**. Copy the address from Helm's own message or the HELM-REPLY file, never from Phantom's recent-activity list. Address-poisoning dust looks like a near match.
2. Check the whole address: first 6 and last 6 characters against Helm's message, read aloud or side by side.
3. Amount: one canary, **0.50 SOL** (range 0.35–1.00; floor = funded − 0.25, so the worst case is 0.25 SOL). Two canaries (Q50 + an HV4 arm): **0.45 SOL each**, leaving ~0.1 SOL in Phantom for fees. The Q50 C1 rung needs deposits of 1.00 SOL, but it is not reachable before 11-06, so do not fund for it now.
4. Send from your own Phantom address. That address is written (public) into the DEC copy as the owner deposit address: under DEC-032 Am.2 only transfers from pinned owner addresses count as deposits. Do not send from an exchange.
5. Optionally send a 0.001 SOL test first and wait for Helm or the manager to confirm it arrived, then send the rest. Both transfers are recorded as deposits.
6. Paste the transaction signature in the chat. Helm records the starting balance (finalized) before the first send.
7. Never share a seed phrase or private key with anyone, including Helm, the manager or any agent. MAL holds no keys. To end the canary early, tell Helm "STOP the canary". To get the funds back, ask Helm to withdraw to your Phantom address; withdraw stays with Helm.

---

## 7. Not measured, and open questions

- Not measured: any live fill, landing, hop latency (research-0 → fast-0), md5 replay runtime at a new sha, mint-fetch credits, or the fill of the Q50 sell ladder. The twin double-count (~40 bps per round trip) is QP's estimate, not a measurement.
- Open for the manager:
  - N2 route (QP pre-ruling vs a code change);
  - N7 host choice (forwarder vs a bridge on research-0, which needs owner + Helm);
  - whether to build N6 before any HV4 read (the earlier HANDOFF line "build only if a read PASSes" is superseded in practice by #908/#913, but not by a written decision);
  - the wallet-wide STOP decision;
  - closing PR #624. It is superseded: its pre-send age limit is on main via #622 (`9b470c36`, `tools/h5_executor.py:243-244`), which the arena executor inherits, and it targets the stopped DEC-024 install base `f873aac`.
- Seal questions: none arose. N4 asks QP whether the #821 mints are outside the Look-2 class; this note did not look up their pools.
