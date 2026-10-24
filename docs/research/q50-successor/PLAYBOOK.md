# Q50 successor playbook: from an A3 redeploy halt to a running successor book in hours, gate and seals unchanged

Written 2026-10-24 from 18:12Z (`date -u` on mal-research-0) for manager15. Records only. No book has cleared the promotion gate, and nothing here says any book makes money. Q50 (`h5v1q50-boostfloor-a1`) is a discarded discovery cell with no edge figure of its own (DEC-031 Am.5 item 4.4). A successor inherits that.

**What was read.** DEC-031 (body, Am.4, Am.5, Am.10 to Am.13), DEC-032 (body, Am.1, Am.2 items 1, 10 to 12), EXP-024 Am.9, the Note under Am.9, Am.10, the rule block and the Note under Am.10, `ARTIFACTS/lab/arena-records-2026-10-24-q50.md`, the redeploy ruling `/data/mal/hunt-1008/push-1016/hunt29/audit-1024/QP-RULING-1024-redeploy.md` (sha256 `073c4955…`), `Q50-AUDIT.md` (sha256 `c5be6d6a…`), PR #885 (`docs/research/p24-fast-track/PLAN.md`, `tools/fasttrack_book.py` header), and the source of `tools/a3_redeploy_check.py`, `tools/a3_halt.py`, `scripts/ops/a3-daily.sh`, `tools/h5_shadow.py`, `observe/trade_decode.py`, `tools/arena_h5_score_q50.py` and `tools/h5_arena_executor.py` at origin/main `a1cb5230`. The fees-program check branch `claude/a3-fees-program-check` had no commit past main when this was written.

**What was not opened.** No shadow output, trigger or decision log, ledger, `bots.json`, score output, A3 daily record or verdict, count, fill, exit or P&L. Every count or time below is a precedent from the records named, or an estimate, and is labelled.

**Status after the quant-proof ruling (2026-10-24, `ARTIFACTS/lab/qp-ruling-1024-q50-successor.md`, R0-R10).** The ruling is binding on this file; where an older paragraph below disagrees, this block and the sections it names win.
- **R0/R1, policy and halt-class map: merged.** DEC-031 Amendment 14 (main `093b8afc`, #893) fixes the successor policy before any Q50 halt and copies the ruling's R1 halt-class map by reference. Section 0's table is a summary only; R1 decides. Class YES: H1, H2, H3-resolved, H4a, H5a, H6. NO: H4b, H5b, H7. A Q50 outcome, P&L, fill or count is never a reason to register or not register.
- **R4, new state file: mandatory** (section 1, S6).
- **R6, FILL list: complete** (section 2.3 lists all 24 tokens of Appendices A and B).
- **R7, base sha S\* and end:** S\* is being built on branch `claude/q50-successor-sstar` (not yet on origin when this was written) and is tagged `q50-succ-base-1` on origin once proven. `SUCC_END_MS` = **2026-12-01T00:00:00Z**, chosen once and never edited (section 2.2). The executor's `SEAL_END_MS` is not part of S\*.
- **R9, fees-program check: merged as #894 (`666502a3`).** It is deployed only after Q50's A3 loop and dead-man jobs have moved off the old `tools/a3_halt.py` (section 2, P11).
- **R10, order:** registration and the books v3 swap come before the new loop's first green run (section 3).

**The problem.** QP estimates about 57 to 62% that an A3 redeploy halt ends Q50 before 2026-10-30 (redeploy ruling (3); about 0.28 deploys a day, a guess from 5 deploy days). The last comparable path, from the 2026-10-19 redeploy to a merged re-pin (#780, `b1a4997`, 2026-10-21T18:44Z), took about 51 h. Q50 itself took 54.6 h from idea to registration (P24 PLAN 1.1). This playbook moves everything that does not depend on the redeploy's own bytes to before the halt. The post-halt work then becomes: classify the halt, re-pin, fill, merge, register.

---

## 0. Halt classes: which halts allow a fast successor

The A3 verdict names its reasons. Those are structure fields and may be read. They are not outcomes. The first hour after a halt is spent putting it in one class. The class decides the path.

| Class | What ended Q50 (DEC-031 Am.4 item 1.2 / 1.6) | Successor sha | Fast path? |
|---|---|---|---|
| **H1 layout-only** | A pump/PumpSwap program redeploy whose check FAILed on (d): trailing bytes, discriminator length, or the IDL hash. The frozen decoders still decode every read event, with 0 undecodable, 0 unknown discriminators of the read types and 0 prefix-parity inequalities. Past PumpSwap deploys looked like this (redeploy ruling (3)). | **F50** `a5f41370` (same code) or the pre-built base S\* (§2.2) | Yes. Re-pin only |
| **H2 decode break** | The check FAILed on (c): undecodable or unknown events of the read types, or prefix parity broken (a field moved, a discriminator changed) | New sha = base + decoder patch | Yes, plus about 4 to 8 h for the patch and its proofs |
| **H3 no decision** | ERROR, timeout or no run. Causes: PumpSwap enumeration failing closed on public RPC (Am.4 1.14), the Am.13 env file missing, a crash, a missed run (24 h deadline) | Depends on what the `--observe` run of the new deploy shows: H1, H2 or "unchanged" | Yes. Re-pin only if unchanged |
| **H4 fees program** | A fees-program redeploy (always hard for Q50; no check) | Same sha if the fee tiers and the fee-config bytes are unchanged | Yes if the tiers are unchanged. **No** if they changed (H4b) |
| **H5 account pin** | `bonding_fee_config`, `pumpswap_fee_config` or `pumpswap_global_config` sha256 changed | Same as H4: is the fee or V model the stack prices still true? | Yes only if the paper stack is unchanged |
| **H6 dead-man** | No applied A3 decision for 48 h (`a3_halt.py` deadman) | Same sha, after the guard is healthy | Yes. Operational fix first |
| **H7 thesis** | `boost_disabled`, `boost_share_low`, `boost_last_slice_early`, `boost_budget_or_slices_changed`, or the pooled `synthetic_share_high` z-test | None. BOOST's behaviour changed, so the rule's premise is in question | **No.** Research, not a successor |

H4b and H5 with a changed fee or V model are a **stack change**. `tools/paper_curve_math.PUMPSWAP_SOL_FEE_TIERS`, the V band [17.5, 17.7] and DEC-031 §1.4's stack would all need new text, and that is days, not hours. The playbook stops there and the manager plans that case separately.

---

## 1. What a successor needs (who, what, how long)

"Pre" means it can be done before any halt (§2). "Post" means it can only be done after the halt. Times are estimates from the precedents named, not measurements.

| # | Item | Source rule | Who | Pre / post | Post-halt time |
|---|---|---|---|---|---|
| S1 | **New `bot_id`**. It keeps Am.5 item 2.1's three properties: it starts `h5v1` (the scorer prices the 330 s exit), does not start `h5v1-boostfloor-` (legacy `set_window_end` can never end it), and starts `h5` (`a3_halt` requires it listed). Proposed: `h5v1q50-boostfloor-s1`, then `-s2`, and so on. A test shows the three properties | DEC-031 §1.2, Am.5 2.1, Am.10 item 6 | builder | Pre (name and test) | 0 |
| S2 | **Frozen sha.** H1/H3/H4/H5/H6: F50, or base S\* (§2.2). H2: S\* plus the decoder patch (§2.4). It is a commit on origin, and any code change is a new `bot_id` | §1.2; Am.5 5 | builder, manager | Pre (S\*, branch `claude/q50-successor-sstar`, tag `q50-succ-base-1`); post (patch) | 0 (S\*) / 4 to 8 h (patch) |
| S3 | **Decoder proofs at the new sha** (H2 only). Fixture tests on the new layout from the A3 samples' raw event bytes. Backward-compatible decode on the old fixtures. md5 decision-equivalence of `h5_shadow` (default and `--q-star-sol 50` paths) on the 2026-09-20 exploration day. Identity proof that every non-decoder function on the decision path is byte-identical to F50 (the `test_h5_shadow_qstar_identity.py` pattern) | Am.5 5.2; CLAUDE.md runner-change rule | builder, job | Post | 2 to 4 h (md5 job about 1 h, estimate) |
| S4 | **A3 re-pin.** (a) Monitor pins `tools/pump_structure_pins.json`: the new deploy slot(s) with `deploy_utc` on every program entry (Am.4 1.6 B-1), and the account sha256 values for H4/H5. (b) Check pins `tools/a3_redeploy_pins.json`: trailing set, `disc_len`, IDL sha256, `reviewed_programdata`, BOOST pins if moved, and `frozen_sha`/`frozen_blobs` = the successor sha. Evidence comes from one `--observe` run on the new deploy, plus a review doc from the #780 template | Am.4 1.5, 1.7, 1.11; #780 pattern | job, builder, QP | Post (needs the new bytes) | 4 to 8 h (precedent 51 h) |
| S5 | **Guard pin and loop redeploy.** A guard pin = the base commit plus exactly the check tool and pins of S4 (the `a3-guard-pin-1024` pattern). Tests green at the pin on fast-0 (the #1442 pattern). Then the order of ruling R10.1 (section 3): cancel the old loop's queued successor first, then the old loop; a ps check; registration with the books v3 swap in the same command; the new loop at the pin with the new state file; its first official run green (all RCs 0, verdict NONE, six flags evaluated). The old loop must be gone before the books swap. Not within :04 to :25 of 07/13/19 UTC, and not in 12:10 to 12:25Z | Am.10 items 5 and 6 | manager (jobs) | Post | 1 to 2 h |
| S6 | **A3 state.** The successor must not inherit Q50's FAIL. The re-pin moves the pinned slot to the new deploy, so enumeration starts after the failed write. **A NEW state file is mandatory for every successor guard pin (ruling R4.2), not optional.** At the guard pin, `failclosed_bound_ms` lowers the halt bound of any later enumeration failure to the `deploy_utc` of any sticky non-PASS entry in the state file, so Q50's file would end the successor at Q50's old redeploy instant. Path convention: `$HOME/data/structure-monitor/a3-redeploy-state-<succ_bot_id>.jsonl` (for example `a3-redeploy-state-h5v1q50-boostfloor-s1.jsonl`), created empty, passed to the new loop as `A3_STATE` (`scripts/ops/a3-daily.sh` line 15) and to the check as `--state`. The default `$A3_OUT/a3-redeploy-state.jsonl` is Q50's record: never edited, truncated, moved or reused. The path and the sha256 of the empty file go in the records PR. Q50's end stays as it is | Am.4 1.5 / 1.6 (no override) | manager | Post | in S5 |
| S7 | **Books file v3.** `{"legacy_bot_ids": [...], "new_rule_bot_ids": [..., "<succ>"]}`, plus `fees_check_bot_ids` (merged in #894; optional, a subset of `new_rule_bot_ids`; deployed per R9, section 2 P11). It is swapped in the same command as registration, and that happens **before** the new loop's first official run (R10.1), away from :04 to :25 of 07/13/19. Its sha256 and a verbatim copy go in the records PR | Am.4 1.10; Am.5 2.2 | manager | Pre (script); post (run) | 0.25 h |
| S8 | **P0.** Either Q50's P0 reused and disclosed (job #1413, 2026-10-21..23, rate 35.33/day, H = 14), or a new P0 on 3 full closed UTC days. The second option costs at least 3 days plus feed health on the new layout | Am.3 item 1; Am.4 2.1; Am.5 1; Am.10 item 6 ("the P0 proxy reuse disclosed") | QP ruling R3 | Pre (ruling) | 0 with reuse; at least 3 d without |
| S9 | **EXP-024 declared observation** for the successor: rule `H5-BOOSTFLOOR-Q50 v1`, the successor's `frozen_sha`, coverage from its process start record `start_ms`, and its end (§1.1). Am.10 (a) covers only the Q50 process at F50, so it does not reach a successor | EXP-024 Am.10 (a), (d); Note under Am.9 pattern | builder, QP, reviewer | Pre (template A); post (fill) | 1 to 2 h (one fill round) |
| S10 | **DEC-031 amendment** for the successor: the registration path (Am.5 pattern), the P0 ruling, the A3 rule, the Am.11-style read instant, N, siblings and disclosures | Am.5, Am.10 item 6, Am.11 | builder, QP, reviewer | Pre (template B); post (fill) | 1 to 2 h |
| S11 | **Read instant, Am.11 style.** The first 00:00Z boundary B with clock < B ≤ the successor's end and at least 100 counted trades on at least 5 dates. Otherwise NOT_DECIDABLE at the end, final. Confirmation is the same over trades after the screen read | Am.11 items 1 to 5 | in S10 | Pre | 0 |
| S12 | **Scoring and read tools.** The Q50 wrapper hard-codes `Q50_ID` (`tools/arena_h5_score_q50.py:32`, refusal at :104). The successor needs a wrapper entry and the B1 read tool with the successor in its book list. The score loop must take the successor's dir (audit M3) | Am.11 item 5; audit B1, M3 | builder | Pre (book table); post (one-line fill) | 0.5 h |
| S13 | **Registration** (G8 pattern): `register_q50` generalised to the successor's table entry. `bots.json` line, notebook copy before the clock, records PR within 24 h | §1.2; Am.5 2 | manager (job) | Pre (script); post (run) | 0.25 h |
| S14 | **Shadow process** at the successor sha, from a clean checkout, `--fresh-forward ARENA --q-star-sol 50`, bx10 off. Started after `registered_at_ms` under its own `MemoryMax`, with `MemoryCurrent` checked first. Q50's shadow #1419 and its queued successor #1421 are cancelled first (queued one first) | Am.5 2.5; Am.10 item 6 ("a new shadow after registration") | manager (job) | Post | 0.25 h |
| S15 | **Day-1 dry score** (counts only) and the halt-alert job on every applied A3 entry (audit M4) | P24 PM05, PM12 | manager (jobs) | Pre (job specs) | 0.1 h |
| S16 | **Kill-review check.** No kill-review week in [registration, read]. The line goes in the registration notebook | CLAUDE.md; Am.5 4.5 | manager | Post | 0.1 h |
| S17 | **Executor for the canary** (not on the clock's critical path). Add the successor's `Book` row to the `BOOKS` table and `FEED_NOTE_BOOKS` (`tools/h5_arena_executor.py:82,172`), with its own EXP-024 executor note (the Note under Am.10 pattern). md5 decision-equivalence replay (`tools/h5_arena_exec_replay.py`, the #1456 pattern) at the flip commit. Its own DEC-032 copy. Q50 never reached a rung if it ended before its read, so the successor starts at C0 only after its **own** gate PASS (DEC-032 §1 item 1; Am.2 item 12.3 needs Q50's confirmation, which a halted Q50 never has) | DEC-032 §1, §8, Am.2 item 12 | builder, Helm, owner | Pre (branch); post (fill) | Days after the clock; does not move the clock |
| S18 | **Clock.** The next 00:00Z after the last of S4 to S16. Nothing is cut to keep a date (Am.10 item 6) | Am.5 4.1; Am.10 item 6 | manager | — | — |

### 1.1 The successor's end and why "same sha past 11-06" does not work

- `tools/h5_shadow.py` at F50 (and at main) runs fresh-forward mode with `h5_look2_start_ms=H5_LOOK2_END_MS` and `h5_look2_observed=False` (lines 3242 to 3247). `_h5_sealed_now` (lines 2208 to 2216) seals every pool with s0 at or after 2026-11-06T00:00Z.
- **A process at F50 therefore writes no outcome for any pool with s0 at or after 11-06, at any date.** `tools/h5_arena_executor.py:73` clamps `SEAL_END_MS` to the same instant.
- Result: a successor at F50 has the window [its clock, 2026-11-06T00Z). A clock on or after about 11-01 leaves fewer than 5 dates, and its read is then NOT_DECIDABLE by construction.
- DEC-032 Am.2 item 12.1 ("the same `frozen_sha` as Q50") and 12.2(b) (a clock on or after 11-14) cannot produce a scored trade at F50. A successor that runs past 11-06 needs a code change (a new sha). Ruling R7 asks QP to confirm this and to rule on coverage of [11-06, 11-14).
- **Recommendation.** Pre-build base S\* = F50 plus one change: a new constant `SUCC_END_MS` used only when `fresh_start_ms` is set (an Engine parameter) plus the fresh call site. `H5_LOOK2_END_MS` and the non-fresh path stay byte-identical (raising `H5_LOOK2_END_MS` would widen EXP-024 Am.2's window for non-fresh processes, which is refused, ruling R7b.1). The executor's `SEAL_END_MS` is **not** part of S\*: the executor runs on main, and its change goes by the S17 path. `SUCC_END_MS` = 2026-12-01T00:00:00Z. Run its md5 decision-equivalence on 2026-09-20 now, before any halt. S\* then serves every class (H1 to H6) and also the 11-06 successor of DEC-032 Am.2 item 12. A successor at S\* is not at "the same `frozen_sha`" as Q50. That is consistent with §1.2: a code change makes a new `bot_id`, and the successor is one anyway.
- S\* runs nothing and covers nothing until template A (the declared observation up to `SUCC_END_MS`) and the DEC-031 §3.5 end-seal successor text merge.

---

## 2. What can be pre-built now

Each item below is independent of the redeploy's bytes. Together they reduce post-halt work to S4 (re-pin), the fills and the jobs.

| # | Pre-build | Form | Effort (estimate) | Merge before a halt? |
|---|---|---|---|---|
| P1 | **Successor id scheme and test.** `h5v1q50-boostfloor-s1` with a test of Am.5 2.1's three properties against `arena_h5_score.set_window_end`'s prefixes and `a3_halt.validate_books` | test file | 0.5 h | Yes (tests only) |
| P2 | **Base sha S\*** (§1.1): F50 plus `SUCC_END_MS` (2026-12-01T00:00:00Z) in the fresh-forward branch only (not the executor), on branch `claude/q50-successor-sstar`, tagged `q50-succ-base-1` on origin once proven, with: md5 decision-equivalence on 2026-09-20, Run A default and Run B `--q-star-sol 50`, EQUAL required (job, the #1456 pattern), plus the fresh-mode and non-fresh unit tests on synthetic 2026 times (09-20 never reaches either end constant, so the md5 alone does not exercise the changed line); an identity test (every decision-path function other than the seal constant byte-identical to F50); and the G6-style decode-identity proof vs `1f7a89c` | branch on origin, not merged to main | 3 to 4 h incl. jobs | **No.** Commit on origin only. It is pinned as a sha and a tag, as F50 was, so that a deleted branch cannot lose the `frozen_sha`. Merging is not needed for a `frozen_sha` |
| P3 | **Decoder-update procedure (H2)**, §2.4: a fixture-capture job spec, a test skeleton and a patch checklist | doc + test skeleton + job spec | 2 h | Skeleton yes (it tests the current fixtures) |
| P4 | **Re-pin kit.** (a) A job spec for `python -m tools.a3_redeploy_check --observe` on the new deploy (public RPC; Helius only through `A3_ENUM_ENV_FILE`, credits reported). (b) A review-doc template from `ARTIFACTS/lab/a3-repin-2026-10-21-program-upgrade-review.md` (sections, the C3' standard, the Token-2022 base-mint check). (c) A small proposer that turns `--observe` output into the pins-file diff (proposed tool `tools/a3_repin_propose.py`; it writes a proposal, never a pin). For H4/H5, the fees review template from `a3-repin-2026-10-13-fees-program-upgrade-review.md` | doc + tool + tests | 3 to 4 h | Tool and template yes (no pin changes) |
| P5 | **Guard-pin builder.** A script that makes the pin commit (base + exactly the check tool, its tests and both pins files), runs the tests at the pin on fast-0 as a job, and prints the blob and sha256 lines the records PR needs. It also holds the loop-redeploy job sequence of Am.10 item 5 as a sheet with the forbidden minutes computed | script + sheet | 2 h | Yes (no effect until run) |
| P6 | **Template A: EXP-024 "Note under Amendment 10 (successor)"**, Appendix A, with FILLs. QP and the reviewer review the template now. After a halt only the fill diff is reviewed (ruling R6) | text | done here, as a draft | Template reviewed, not merged |
| P7 | **Template B: DEC-031 Amendment N (Q50 successor)**, Appendix B, with FILLs: registration path, P0, A3 rule and fees list, read instant, end, N and siblings, disclosures | text | done here, as a draft | Template reviewed, not merged |
| P8 | **Scoring wrapper and read tool with a book table.** Build the audit-B1 read tool (owed before 10-30 in any case, Am.11 item 5) with a compiled-in table `{bot_id: (frozen_sha, clock, end, rule, rule_sha256, q_star)}` that holds Q50 now and takes a successor by a one-line PR. Do the same for the wrapper and `register_q50`. **Q50's own pins must not move**: Q50 keeps `arena_h5_score_q50.py` sha256 `f5a479ad…` (any change is disclosed at its read, Am.11 item 5), and the successor gets a new file or the B1 tool | code + tests | 4 to 6 h (largely shared with B1) | Yes |
| P9 | **fasttrack_book H5 profile** (P24 "next builds" item 4): profile `h5_shadow` (entry, env, cadence, base pins: `h5_shadow.py`, `arena_h5_score*.py`, `arena_h5_cost.py`, `a3_*`, `a3-daily.sh`) and a successor manifest with every value derived except the post-halt fills (frozen sha, clock, guard pin). `render` then emits register.sh, run.sh, jobs.json and REGISTRATION.md, with PRECONDITION files for `repin`, `guard_green`, `p0`, `note`, `dec`, `md5`, `qp` and `reviewer` | code + manifest | 3 to 4 h | Yes (after #885) |
| P10 | **Job specs**, written and sh-linted now (jobs run under sh): `--observe`; guard tests at the pin; loop redeploy; books-file v3 swap; registration; shadow start; score loop with both dirs (audit M3); day-1 dry score; halt alert (audit M4). They live in the P9 pack | specs | in P9 | — |
| P11 | **Fees-program check** (branch `claude/a3-fees-program-check`, redeploy ruling follow-up (2)): trigger = only the fees program deploy slot changed; PASS needs (a) to (h), the three account sha256 values equal their pins, the `pump_fees` IDL unchanged, every in-universe WSOL trade event's fee bps equal to the pinned tier for its market cap, and enumeration ERROR if it fails or does not reach the pinned slot. A third books-file key (proposed `fees_check_bot_ids`) in `a3_halt.validate_books` and `BOOKS_KEYS` | code + tests | builder lane (already started) | Merge yes. Merged as #894 (`666502a3`); schema `fees_check_bot_ids`, optional and a subset of `new_rule_bot_ids`. **Deploy only after Q50 has ended and its A3 loop and dead-man jobs have moved off the old `tools/a3_halt.py`** (R9.1), as part of the successor's guard pin with the successor listed under the new key. An earlier deploy needs the full R9.2 replay (daily lines on both hosts, old vs new pins, byte-identical verdicts; `apply` and `deadman` covered) and is not planned |
| P12 | **`A3_ENUM_ENV_FILE` readiness** (Am.13): the env file is `/var/lib/mal/fast-listener/helius.env` (Am.13 Erratum 1, #892; not `helius-a3.env`), readable by the loop user. Am.13 is inactive: enumeration failed (job #1545). Needed: one `--enum-only` PumpSwap run reaching the pinned slot. Without it, a successor gets the same false ERROR halts on every PumpSwap deploy | owner → Helm | — | Ask now |
| P13 | **Pre-halt S2 for the 11-06 successor.** P2, P6 and P7 also serve DEC-032 Am.2 item 12. If Q50 survives to 11-06, the same pack registers its successor with a clock of 11-06T00Z or later, with R7 answered | — | — | — |

### 2.1 Order of the pre-builds (critical first)
1. **Rulings R1 to R10** (§4; issued 2026-10-24, binding). Without R3 (P0 reuse), R4 (re-pin is not an override) and R6 (fill-only review), nothing post-halt gets under 3 days.
2. P8 (shared with B1, owed by 10-30 anyway), then P2 (S\* and its md5), P4 and P5.
3. P9 and P10, then P6 and P7 review rounds.
4. P11 after the fees check is reviewed. P12 runs in parallel through the owner.

### 2.2 Base sha S\* (P2) in detail
- **Diff vs F50:** a new `SUCC_END_MS`, used only in fresh-forward mode, pinned in code. Nothing else; `H5_LOOK2_END_MS`, the non-fresh path and the executor are untouched. A test pins the constant.
- **Value (ruling R7b.5): 2026-12-01T00:00:00Z**, chosen once, before the md5 job, never edited; a different value is a new sha with new proofs. The book's effective end is always min(clock + H, `SUCC_END_MS`). Pools with s0 in [11-06, 11-14) count only under template A's declared observation merged before the successor's process starts (R7c); pools at or after 11-14 are outside every H5 seal.
- **Proofs now:** md5 EQUAL on 2026-09-20 for Run A and Run B, an identity test vs F50, and the G6 decode-identity vs `1f7a89c`. These carry to any successor at S\*. The H2 path re-runs only the md5 on S\*+patch.
- **Q50 untouched:** S\* is a separate commit. Q50 keeps F50, its shadow, its wrapper and its pins.

### 2.3 Template fills (what is left to type after a halt; complete, ruling R6)
The templates carry exactly 24 FILL tokens. **Every token is listed here with the template's own name.** Any token not listed here, or any other changed byte, is a full review (R6.3). After a halt a script substitutes the values into the OK'd template and `diff` against the PR text must be empty.

| # | Token | A / B | Value | Known now? |
|---|---|---|---|---|
| 1 | `FILL:succ_bot_id` | A, B | `h5v1q50-boostfloor-s<k>`; first successor `h5v1q50-boostfloor-s1` (R8.1) | pre-filled `h5v1q50-boostfloor-s1`; `s<k>` for a successor of a successor |
| 2 | `FILL:date_u` | A, B | `date -u` at writing | post |
| 3 | `FILL:halt_ms` | A, B | Q50's `window_end_ms` (the A3 `halt_at_ms`) | post |
| 4 | `FILL:class` | A, B | H1, H2, H3-resolved, H4a, H5a or H6 (R1), notebooked before any successor PR | post |
| 5 | `FILL:reasons` | A | the A3 verdict `reasons` (structure only) | post |
| 6 | `FILL:guard_run` | A | instant of the guard run that applied the halt | post |
| 7 | `FILL:job` | A | MiScusi job id of that run | post |
| 8 | `FILL:frozen_sha` | A, B | S\* (commit of tag `q50-succ-base-1`) or S\*+patch (H2) | S\* when built; patch post |
| 9 | `FILL:succ_end` | A, B | `SUCC_END_MS` = 2026-12-01T00:00:00Z (the S\* constant) | **pre-filled** |
| 10 | `FILL:n` | B | next free DEC-031 amendment number at merge (a renumber is a fill) | at merge (15 or later) |
| 11 | `FILL:qp_ruling` | B | `ARTIFACTS/lab/qp-ruling-1024-q50-successor.md` (sha256 of `hunt29/audit-1024/QP-RULING-1024-successor.md`: `615f31e7...`, as in Am.14) | **pre-filled** |
| 12 | `FILL:id_test` | B | the committed P1 test file (Am.5 2.1 properties against `set_window_end` prefixes and `a3_halt.validate_books`) | pre-filled when P1 merges |
| 13 | `FILL:diff_summary` | B | for S\*: "a new `SUCC_END_MS` used only in fresh-forward mode; nothing else"; for S\*+patch add the decoder diff | **pre-filled** for S\*; patch post |
| 14 | `FILL:md5` | B | S\* Runs A and B EQUAL on 2026-09-20 (job ids); H2 adds the re-encoded-layout md5 (R5.3) | S\* pre-filled when its job ends; H2 post |
| 15 | `FILL:identity` | B | the S\* AST identity test and decode identity (guard (h) blobs) | **pre-filled** when S\* is proven |
| 16 | `FILL:P0` | B | the job id: `#1413` if the clock is before 2026-11-07T00:00Z (R3.4; the R3.2 disclosure and the limit are fixed template text), else `new P0 job <id>` | pre-filled (reuse); staleness check post |
| 17 | `FILL:fees_list` | B | the `fees_check_bot_ids` entries (the successor, if the check is deployed by its clock; else the R9.4 disclosure) | post |
| 18 | `FILL:books_v3_sha256` | B | sha256 of the books file v3 (verbatim in the records PR) | post |
| 19 | `FILL:guard_pin` | B | commit, check-tool blob and sha256, both pins blobs and sha256, new state file path and its empty-file sha256 (R4.2) | post |
| 20 | `FILL:repin_review` | B | path and sha256 of the review doc (R4.4) | post |
| 21 | `FILL:green_run` | B | the official green run, **on books v3 with the successor already registered** (R10.1) | post |
| 22 | `FILL:read_tool` | B | the Am.11 read tool with the book table and per-book end (R8.5; due 2026-10-30T00:00Z) | pre-filled when it merges |
| 23 | `FILL:earliest_B` | B | first 00:00Z boundary B with clock < B (Am.11) | post |
| 24 | `FILL:clock` | B | the next 00:00Z after the last pre-clock item (also the state-file and shadow `clock_start_ms`) | post |

Not fills (each gets a full QP review in the same round, R6.4): the halt-class record (R1), the re-pin and its review doc (R4), the H2 patch and proofs (R5), the H4a/H5a evidence.

### 2.4 Decoder-update procedure (H2)
1. **Capture.** Run the A3 check in `--observe` mode on the new deploy; its log sample (500 txs) and migrate sample (K = 40) are the evidence. A capture job writes only raw event bytes (`Program data` base64 and emit_cpi inner-instruction data) with discriminators and lengths: no mint, wallet, price or outcome, the same fields the check already handles. A pool's trigger, entry or exit is never computed. Ruling R5 confirms that this is outside the EXP-024 seal (structure, not an H5 outcome).
2. **Classify each changed event:** appended bytes only (H1: the prefix decoders in `observe/trade_decode.py` already accept a longer `raw`, since they check only `len(raw) < N`), a field inserted or moved (H2), or a new or changed discriminator (H2).
3. **Patch** only `observe/trade_decode.py` (and the decode helpers `h5_shadow.py` imports, if any), keyed by discriminator plus length, so the old layout still decodes byte-for-byte as before.
4. **Tests:** (a) new fixtures: decoded fields equal the check's independent IDL decode; (b) old fixtures unchanged; (c) any other length fails closed (no silent mis-decode); (d) the trailing set as pinned.
5. **Proofs:** md5 decision-equivalence on 2026-09-20 (old layout) at S\*+patch, Runs A and B EQUAL; an identity test that no decision-path function outside the decoder changed vs S\*.
6. **Guard:** `frozen_sha` and `frozen_blobs` in the check pins = S\*+patch, so check (h) guards the successor's own decoders. This also ends the Q50 audit MINOR, where the guard pinned 1f7a89c, not Q50's sha.

---

## 3. Timeline: halt instant to successor clock

**Instants.** R = the redeploy (Q50's halt instant, retroactive). D0 = the A3 run that applies the halt (07:10Z or 19:10Z, or the check's deadline). A = the halt alert notebooked; audit M4's alert job makes A ≈ D0. The successor's clock is the first 00:00Z after its last pre-clock item.

| T from D0 | Step (critical path in bold) | Who |
|---|---|---|
| 0 to 0.5 h | **Alert. Read the verdict reasons (structure only). Classify H1 to H7 (§0).** H7: stop, no successor. Notebook the class | manager |
| 0 to 0.5 h | Cancel Q50's queued successor #1421 first, then shadow #1419 (or keep it; ruling R10) to free fast-0 memory. Q50's end record (counts only), N+1 | manager |
| 0.5 to 2.5 h | **`--observe` job on the new deploy (P4a); capture job (H2)** | job |
| 2.5 to 4 h | **Re-pin proposal (P4c) + review doc from the template (P4b).** H2: decoder patch starts in parallel (§2.4) | builder ×2 |
| 2.5 to 4 h | Fill templates A and B, wrapper/B1 table line, books v3, manifest (P9). P0 = reuse (R3) | builder |
| 4 to 7 h | **QP fast round on re-pin + fills (one round, R6); reviewer verdicts.** H2: md5 job + tests (2 to 4 h) | QP, reviewer, job |
| 7 to 8 h | **Manager merges the re-pin and fill PRs. Guard pin built, tests at the pin green (job)** | manager |
| 8 to 9 h | **Cancel the old loop's queued successor first, then the old loop; ps check (the old loop must be gone before the books swap, R4.2/R10.1). Registration with the books v3 swap in the same command, notebook copy.** | manager (jobs) |
| 9 to 10 h | **Start the new loop at the new pin with the new state file; its first official run is the pre-clock green run (all RCs 0, verdict NONE, six flags evaluated) only because it runs on books v3 with the successor registered. Then shadow start (after `registered_at_ms`), at least 1 h of clean log before the clock; score loop with both dirs; day-1 dry score; halt-alert job; kill-review line** | manager (jobs) |
| next 00:00Z | **Clock** | — |

**What this gives (estimates):**

| D0 | Class | Last item about | Clock | D0 → clock |
|---|---|---|---|---|
| 07:10Z | H1 / H3-unchanged / H4-H5 same tiers / H6 | about 17:00Z | next 00:00Z | **about 17 h** (target met) |
| 07:10Z | H2 | about 21:00 to 23:00Z | next 00:00Z | about 17 h; no slack. A slip costs a day: 41 h |
| 19:10Z | H1 etc. | about 05:00Z next day | the 00:00Z after | about 29 h. The day boundary binds, not the work |
| 19:10Z | H2 | about 09:00Z next day | the 00:00Z after | about 29 h |
| any | H4b / H5 stack change, H7 | — | — | days; out of scope |

- The ≤ 24 h target holds for halts applied at the 07:10Z run, for every fast class.
- For a 19:10Z halt, the next 00:00Z is 4.8 h away, and the QP round, guard redeploy and first official run do not fit in that. So the clock is the 00:00Z after, about 29 h.
- Only a pre-approved fill (R6) with the guard steps done by about 22:00Z would get a 19:10Z halt under 24 h. That is not planned for.

**Critical path (R10.1):** classify → `--observe` → re-pin review → QP round → merge → guard pin tests → cancel old loop → register + books v3 swap → new loop with the new state file → first green official run → shadow start (at least 1 h clean log) → clock. Minimum gap registration to clock: none fixed (R10.2); the records PR follows within 24 h. Q50 shadow #1419: recommended to cancel after its end, queued #1421 first, output directory kept (R10.3). Forbidden minutes (:04 to :25 of 07/13/19; 12:10 to 12:25Z) apply to the cancel, the swap and the start. The guard steps are mechanical but take about 2 to 3 h of wall time and have forbidden minutes. Every other item runs beside them.

**The successor's window** at F50 or a seal-unchanged sha is [clock, 2026-11-06T00Z). At S\* it is [clock, min(clock + H, `SUCC_END_MS`)) with `SUCC_END_MS` = 2026-12-01T00:00Z.
- A halt on 10-26 gives a clock of about 10-27, so 10 dates to 11-06 (F50), or to 12-01 (S\*) the cap is far away; the effective count is 14 dates (H = 14).
- A halt on 10-30 gives a clock of about 10-31, so 6 dates to 11-06. That is tight for 100 counted trades. The proxy P0 rate of 35/day is an upper bound on a different feed, and the trade count is unmeasured.
- So S\* (P2) is what keeps a late successor decidable.

---

## 4. Decisions asked of quant-proof (ruled 2026-10-24; answers in `ARTIFACTS/lab/qp-ruling-1024-q50-successor.md`, R1 and R0 merged as DEC-031 Am.14)

Each item is a question. The manager briefs QP on the code and this file, not on a conclusion.

| # | Question | Why it is on the critical path |
|---|---|---|
| **R1** | **Halt-class map.** Which halt classes (§0, H1 to H7) allow a successor of the same rule, and on what evidence is each class assigned? Is H7 always excluded? Is a dead-man end (H6) a fast-successor case? | Decides in the first 30 minutes whether a successor exists at all |
| **R2** | **Same decision code across a layout change (H1).** If the frozen decoders show 0 undecodable, 0 unknown discriminators of the read types and 0 prefix-parity inequalities on the new deploy's samples, may the successor run the unchanged decision code (F50 or S\*) under re-pinned A3 pins? Or does any FAIL require a decoder patch? | Decides H1 vs H2: 0 vs 4 to 8 h |
| **R3** | **P0.** May the successor reuse Q50's P0 (job #1413, 2026-10-21..23, rate 35.33/day, H = 14), disclosed, as Am.10 item 6 allows for a pre-clock revival? Does that hold when the halt is a redeploy and possibly a regime change, and at a new sha that differs from F50 only in decoders or the seal constant? Is a post-halt count of Q50's own trigger records (counts only) allowed or required as a check? | Without reuse, the clock is at least D0 + 4 days |
| **R4** | **Re-pin after a FAILed write is not an override.** Q50 stays ended (Am.4 1.6: nobody turns a FAIL into a PASS). The new pins judge only books whose clock is after the re-pin's merge. What evidence suffices for the re-pin (the `--observe` output, the review doc of the #780 standard, the C3' standard, the Token-2022 base-mint check)? Is a new state file per guard pin required? | Without it, the successor inherits Q50's FAIL and ends at the redeploy instant, before its clock |
| **R5** | **Decoder patch (H2).** Is the fixture source (raw event bytes from the A3 samples; no trigger, price or outcome) outside the EXP-024 seal? Are §2.4's tests, md5 on 2026-09-20 and the identity proof sufficient? Does check (h) then pin the successor sha's blobs? | Without it, H2 waits for a full review |
| **R6** | **Fill-only review.** QP OKs templates A and B and the P9 manifest now. After a halt, is one round on the fill diff (the §2.3 fields only) plus the re-pin enough? Is any FILL outside §2.3 a full review? | Turns 3 to 10 QP rounds (Q50: about 10) into 1 |
| **R7** | **The successor's end and coverage.** (a) Confirm that F50 writes no outcome for s0 ≥ 2026-11-06 (`h5_shadow.py:2215`, `:3247`), so DEC-032 Am.2 item 12.1 "same `frozen_sha`" cannot score past 11-06. (b) Which end may S\* carry: 2026-11-14T00Z? (c) What covers pools with s0 in [11-06, 11-14)? DEC-031 §3.5 lets v2 count from 11-06 without a note, while EXP-024 Am.10 gating and DEC-032 Am.2 12.2(b) say 11-14 for Q50. (d) The DEC-031 §3.5 end-seal successor text | Decides whether a late successor can reach a read at all, and whether P2 is built |
| **R8** | **Identity, N, c and siblings.** Are the `bot_id` scheme (`h5v1q50-boostfloor-s<k>`) and its three-property test enough? N+1 at the successor's clock, and Q50 counted as ended. Must the successor's read print Q50 (counts only) and the a2 siblings side by side (Am.3 8(e))? Is the successor a new book for c, with no carry-over from Q50? | Wording in template B |
| **R9** | **Fees-program check deployment.** The check must not change Q50's guard decisions after Q50's clock (Am.4 preamble, Am.10 item 3). Is deploying it only after Q50 ends the rule? Or may it deploy earlier with a replay proof of 0 decision changes for Q50's lists? What is the books-file schema (third key)? Does an H4 halt with unchanged tiers allow a same-sha successor? | Without it, every fees-program deploy also ends the successor |
| **R10** | **Clock mechanics.** Does the first official run after a loop redeploy (Am.10 5(4)) count as the "one full green a3-daily run before the clock"? Is there a minimum gap between registration and clock (Q50 had about 24 h; the plan here has 6 to 14 h)? May Q50's shadow #1419 keep running after its end (memory), given that its outcomes are covered but never read? | Decides whether a 07:10Z halt makes the same-day 00:00Z |

Not for QP; these are the manager's and the owner's:
- the kill-review week check;
- P12 (Helm's env file);
- the owner's notice that a successor replaced Q50. That notice is not an approval request; live still needs the successor's own PASS and the DEC-032 steps.

---

## 5. What this playbook does not do

- It does not register, re-pin, deploy or change any running job. It amends nothing. Templates A and B are drafts for review.
- It does not estimate any edge or trade count for a successor. P0's 35.33/day is a proxy upper bound on a different feed.
- It does not cover H4b, H5 with a stack change, or H7. Those are research.
- Not measured: the time of any post-halt step (estimates only); whether `--observe` succeeds on public RPC for a PumpSwap deploy (Am.4 1.14 says enumeration fails closed there); fast-0 memory with two H5 shadows; the md5 job's run time.

---

## Appendix A: template, EXP-024 "Note under Amendment 10 (successor `FILL:succ_bot_id`)" (DRAFT; not for merge as is)

> **Note under Amendment 10 (successor) (written `FILL:date_u` from `date -u`; before the successor's process starts and before its clock; outcome-blind; text only): declared real-time observation of `FILL:succ_bot_id`'s outcomes.**
>
> **Gating.** Merges with quant-proof's OK and a reviewer verdict on its exact final head, before the successor's shadow process starts. Otherwise the successor is not registered.
>
> **What was in view.** Q50's end record (`h5v1q50-boostfloor-a1`, `window_end_ms` `FILL:halt_ms`, class `FILL:class`, verdict reasons `FILL:reasons`, guard run `FILL:guard_run`, job `FILL:job`): structure fields only. Not opened: any Q50 or successor shadow output, decision log, ledger, `bots.json`, score output, count, fill, exit or P&L.
>
> - **(a) Scope.** `rule_id` `H5-BOOSTFLOOR-Q50 v1` (block sha256 `b3f8ecbf8731dc322a035a0b3996b7ed3f0b8c508cc6947014e3de1aa9524fd4`), computed only by `tools/h5_shadow.py` at `FILL:frozen_sha`, started with `--fresh-forward ARENA --q-star-sol 50`, for `FILL:succ_bot_id` only. It covers pools with s0 at or after that process's start record `start_ms` and before the book's end `min(FILL:clock + 14 d, FILL:succ_end)`, where `FILL:succ_end` (2026-12-01T00:00:00Z) is only the scoring cap pinned in S\*'s code (ruling R7). A process at any other code is not covered.
> - **(b) No revival.** As Amendment 10 (b). Q50's end is final for its `bot_id`. Nothing here re-scopes Q50 or EXP-024.
> - **(c) bx10, synthetic class, Look-2.** Amendment 10 (e), (f) and (g) bind the successor word for word, with "Q50" read as `FILL:succ_bot_id`. Pools with s0 in [2026-11-06T00Z, 2026-11-14T00Z) are covered only by this note. Pools with s0 at or after 2026-11-14T00Z are outside every H5 seal. Amendment 10 (d) does not carry over to this `bot_id`; its end is `FILL:succ_end` as a cap, and never later than clock + 14 d.
> - **(d) Disclosures.** The successor is a new `bot_id` and adds 1 to N. Its pools are the same rule's pools after Q50's end. Q50 had 0 counted trades read, and its end is disclosed at the successor's read. Q* = 50 is a discarded discovery cell, and no edge is claimed. Amendment 2's clause on outcomes in view applies.

## Appendix B: template, DEC-031 Amendment `FILL:n` (Q50 successor registration path) (DRAFT; not for merge as is)

> **DEC-031 Amendment `FILL:n`** (written `FILL:date_u`; records only; before the successor's clock; outcome-blind). The registration path of `FILL:succ_bot_id`, successor of `h5v1q50-boostfloor-a1` after its A3 end at `FILL:halt_ms` (class `FILL:class`; ruling `FILL:qp_ruling`).
> 1. **Book.** `FILL:succ_bot_id` (Am.5 2.1 properties shown by test `FILL:id_test`), rule `H5-BOOSTFLOOR-Q50 v1`, `frozen_sha` `FILL:frozen_sha` (diff vs F50: `FILL:diff_summary`; md5 `FILL:md5`; identity proof `FILL:identity`). Deciding cell 1.9 s at 0.30 SOL, rent charged. Stake, stack and legs as Q50.
> 2. **P0.** Reuse of job #1413 (2026-10-21..23; 46/25/35; mean 35.333333333333336; H = 14): a proxy counter on arena tape (a different feed), an upper bound (synthetic and unclassified pools included), pre-redeploy days, day 1 an undercount and day 3 a lower bound, the old Am.3 bar; the post-redeploy trigger rate is unmeasured. Valid only for a clock before 2026-11-07T00:00:00Z (ruling R3.4). `FILL:P0` is the job id (`#1413`), or `new P0 job <id>`.
> 3. **A3.** `new_rule_bot_ids` (and `FILL:fees_list`). Books file v3 sha256 `FILL:books_v3_sha256`, verbatim in the records PR. Guard pin `FILL:guard_pin`. Re-pin review `FILL:repin_review`. One official green run before the clock: `FILL:green_run`. Q50's FAIL stays final. The re-pin judges only books with a clock after its merge (ruling R4).
> 4. **Read instant.** DEC-031 Amendment 11 items 1 to 5 apply word for word, with `h5v1q50-boostfloor-a1` read as `FILL:succ_bot_id`, its clock, and the book's end `min(FILL:clock + 14 d, FILL:succ_end)` in place of 2026-11-06T00:00Z. `FILL:succ_end` is only the scoring cap; H is never extended (Am.4 2.2/2.3). The read tool is `FILL:read_tool` (QP OK and reviewer verdict before the earliest boundary `FILL:earliest_B`).
> 5. **Window and H.** [`FILL:clock`, the book's end), the book's end = `min(FILL:clock + 14 d, FILL:succ_end)`. H = 14 (Am.4 2.2, capped by the end). Pools with s0 in [2026-11-06T00Z, 2026-11-14T00Z) count only under the Note under Amendment 10 (successor) for this `bot_id`, which must merge before the process starts.
> 6. **N, c, siblings.** N + 1 from the clock. Q50 is listed as ended. The read prints Q50 (counts only) and the successor side by side (Am.3 8(e)). c is unchanged until a screen pass; a successor screen pass adds 1 to c, with no carry-over from Q50 (no trades pooled, no rung, no confirmation credit). The read also prints `h5v1-boostfloor-a2` and `h5v2-boostclock-a2` (ended, 0 attempts) and every earlier successor, counts only, with the shared-pool count (Am.3 8(e)). A successor pass is best of N and in-sample against Q50.
> 7. **Unchanged.** The promotion gate and every threshold; the 5% cap; Am.6; DEC-014's NOT_DECIDABLE rules; one read per `bot_id`; DEC-032 (C0 still needs this book's own PASS; no rung carries over from Q50, which never had one).
> 8. **Disclosure.** In view: Q50's end record (structure only), this playbook, the rulings. Not opened: any outcome. Winner's curse: a discarded discovery cell, best of N. Not a profit claim.
> 9. **DEC-031 §3.5, successor end-seal text** (merges in this amendment or its own, before the successor's process starts): "§3.5, successor end-seal for `H5-BOOSTFLOOR-Q50 v1` successors. For a `bot_id` registered as a successor of `h5v1q50-boostfloor-a1` at `frozen_sha` S (tag `q50-succ-base-k`, or S plus a QP-ruled decoder patch), the arena end-seal is `SUCC_END_MS` = 2026-12-01T00:00:00Z as pinned in S's code. Its declared observation is the EXP-024 Note under Amendment 10 (successor) for that `bot_id`. Pools with s0 in [2026-11-06T00Z, 2026-11-14T00Z) are covered only by that note. The book's window is [clock, min(clock + H, `SUCC_END_MS`)). The 2026-11-06T00Z end-seal is unchanged for every other book, for the Q50 process at F50, and for the executor until its own note and md5 proof merge."
