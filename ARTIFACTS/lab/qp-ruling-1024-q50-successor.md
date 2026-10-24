# QP ruling 2026-10-24 (written from 18:20Z, `date -u`): Q50 successor playbook R1-R10 and the 11-06 base-sha finding

Subject: PR #891, head `ce4e084dea2f41d501aea6a6dd46ca52d5ac4cbc`, `docs/research/q50-successor/PLAYBOOK.md` (read through `git show`, no checkout).
Also read: DEC-031 body, Am.4, Am.5, Am.10, Am.11, Am.13 (origin/main `ecd70ba5`); DEC-032 Am.2 items 1, 2, 10-12; EXP-024 section 3, 3.1, 8.1, Am.2, A3 halt record, Am.10 and its Note; `ARTIFACTS/lab/arena-records-2026-10-24-q50.md`; `QP-RULING-1024-redeploy.md`.
Code checked: `tools/h5_shadow.py` at F50 `a5f41370` (lines 2208-2216, 3240-3248, 258); `tools/a3_redeploy_check.py` at guard pin `0ec7fae8` (`frozen_guard` 681-698, `failclosed_bound_ms` 968-990, `FROZEN_FILES` 1138); `tools/a3_halt.py` `BOOKS_KEYS`/`validate_books` (main); `tools/arena_h5_score.py:620-640`; `tools/arena_h5_q50_instant.py:37`; `tools/arena_h5_score_q50.py:38,111`; `tools/h5_arena_executor.py:73,173-175`.
NOT opened: any Q50 shadow output, decision log, ledger, bots.json, score output, A3 daily record or verdict, gap row or hour-health for [2026-10-22T21Z, 2026-10-26T21Z), count, fill, exit or P&L.

Gate status: no book has cleared the promotion gate. Q50 and any successor have 0 counted out-of-sample trades. Q* = 50 is a discarded discovery cell (best and monotone was Q* = 40); the only family edge figure (+4.939%/attempt, best of 413, p 0.1953) is Q40 and in-sample. Winner's curse applies to any later pass: best of N and in-sample against its siblings. Nothing here is a profit claim, and nothing here moves a threshold.

---

## R0. Precondition found in review (blocking for every fast path): commit the successor policy before outcomes are in view

EXP-024 Am.10 (a) lets anyone watch Q50's outcomes in real time. A successor decision made at halt time, with Q50's running P&L visible, is an optional stopping/continuation choice made with outcomes in view. That is selection. The decision must be mechanical and fixed in advance.

**Required:** the text below merges (records PR, QP OK on exact head, reviewer verdict) **before 2026-10-25T00:00:00Z**. If it merges later, it still binds, but every successor read must state "the successor policy was fixed after Q50's clock, with Q50 outcomes observable", and the manager states whether any Q50 outcome was looked at. Nobody opens a Q50 outcome before it merges.

> **Q50 successor policy (fixed before any Q50 halt).** If `h5v1q50-boostfloor-a1` ends by an A3 or dead-man end before any read instant, and its halt class (QP-RULING-1024-successor R1) is H1, H2, H3-resolved, H4a, H5a or H6, a successor IS prepared and registered by the playbook path, without regard to any Q50 outcome. Only these reasons may stop it, each recorded with its instant before the stop: H7 or H4b/H5b class; a kill-review week in [registration, read]; the re-pin, decoder proofs or guard tests not green by D0 + 72 h; a fast-0 memory refusal; a ruling that an item cannot be met. A Q50 outcome, P&L, fill or trade count is never a reason to register or not register a successor, or to pick its class, sha or clock.

## R1. Halt-class map. YES for H1, H2, H3 (once resolved), H4a, H5a, H6. NO for H4b, H5b, H7.

General conditions (all classes):
1. A successor exists only if Q50 ended **before any read instant** and its end is NOT_DECIDABLE (Am.11 count tool, counts only, below 100 counted trades or below 5 dates at its window end). If Q50's truncated window still reaches a read (PASS or FAIL), there is no successor of this rule by the fast path. A re-registration after a FAIL is re-filing a failed rule; that question returns to QP.
2. The class is assigned from structure fields only: the A3 verdict `reasons`, `halt_at_ms`, the flags, and one `--observe` run on the new deploy. It is notebooked with its instant **before** any successor PR opens. A later reclassification may only go stricter (H1 -> H2 -> research).
3. Q50's end is final (Am.4 1.6). Nothing done for a successor touches Q50's window end, state file or records.

Per class:
- **H1 layout-only: YES, re-pin only** — conditions in R2.
- **H2 decode break: YES, new sha** — conditions in R5.
- **H3 ERROR / timeout / missed run: not a class of its own.** Run `--observe` on every write since the old pinned slot (enumeration must reach it; Helius through `A3_ENUM_ENV_FILE` if the file exists, credits reported). Then reclassify: unchanged -> H1 path with an empty layout diff; changed -> H1 or H2. If enumeration still cannot reach the pinned slot, there is NO successor until it can (an unenumerated write is unreviewed). An ERROR with no program change (crash, RPC failure) -> H6 path.
- **H4 fees-program redeploy.** H4a (successor at the same sha) YES only if all hold: the review doc follows the 2026-10-13 fees-program template; the `pump_fees` IDL diff touches no fee-tier instruction, account or field, or it is unchanged; the three account sha256 values equal their pins (if any differs, it is H5); on the `--observe` log sample, **every** in-universe WSOL PumpSwap trade and SOL bonding trade has fee bps equal to the pinned tier for its market cap (`tools/paper_curve_math.PUMPSWAP_SOL_FEE_TIERS` and the bonding fee), 0 exceptions, with the count printed. If any of these fails it is H4b: a stack change, NO successor (research plus new DEC text).
- **H5 account pin changed.** H5a YES only if the changed account is decoded field by field, the changed field(s) are named, and each is shown to enter neither the rule nor the deciding stack (fee tiers, V band [17.5, 17.7], rent, send cost). Otherwise H5b: NO. A field that the stack does not price but that plausibly changes trader behaviour (for example a creator-fee change) is disclosed at the successor's read as a regime change.
- **H6 dead-man / two-run unevaluated.** YES, same sha, after: the root cause is written (operational, with job ids); a fresh monitor run plus check on the current state shows NONE, or every pending write is classified H1 to H5 first; and for a "core rule unevaluated on two runs" end, the first fully evaluated run shows all four BOOST flags clear. If any BOOST flag is set at that run, the class is H7. The dead-man end is final for Q50 (Am.4 1.6(c)).
- **H7 thesis flags (`boost_*`, pooled `synthetic_share_high`): NO, always.** The premise of the rule is in question. Any new H5 book after H7 is a new design with its own P0, notebook and DEC, never a successor.

## R2. Same decision code across a layout-only change (H1): YES, with conditions

The unchanged decision code (F50, or S* of R7) may run under re-pinned A3 pins only if **all** of these hold on the new deploy's `--observe` samples (log 500 txs and migrate K = 40, the pinned sample rule; thresholds unchanged):
1. (c) holds in full through the frozen decoders: 0 undecodable, 0 unknown discriminators of the read types, 0 prefix-parity inequalities, every read event decoded field by field equal to the independent IDL decode.
2. Every (d) difference is appended bytes after the IDL's last field, or a length change that the IDL accounts for exactly. Any field inserted, moved, resized or retyped is H2.
3. **C3' standard on the new bytes** (the #780 precedent): on every in-universe WSOL PumpSwap trade and SOL bonding trade in both samples, any newly appended field is zero, or its meaning is established from the IDL and shown not to enter the rule or stack. A non-zero value of unknown meaning inside the universe is not H1 (H2 or research).
4. IDL diff (check (f), not (d) as the playbook says in section 0): no change to any instruction, event or field the rule or stack reads. A new discriminator the rule does not read goes into `ignored_discs` by name, in the review doc.
5. The Token-2022 base-mint check of C3'(d) is repeated on the new sample.
6. The re-pin changes only what the new deploy forces (slots, `deploy_utc`, trailing set, `disc_len`, IDL sha256, `reviewed_programdata`). The judgement pins (`max_capped_share`, `min_init_boost_in_migrate_sample`, BOOST +/-20%, N, K, offset, `CHECK_WINDOW_S`) are NOT changed. Changing them is a rule change and needs a full review, not a re-pin.
If any item fails, the class is H2 (patch) or research. There is no "close enough".

## R3. Reusing Q50's P0: YES, disclosed. No post-halt count is required. Q50's own records are not opened for it.

1. Reuse of job #1413 (2026-10-21..23, deciding cell 46/25/35, mean 35.333333333333336, H = 14) is allowed for any fast class, H2 included, at S* or S*+patch. Reason: P0 decides only the floor and H. H = 14 at any rate of at least 150/14, and the end caps it anyway. A wrong P0 can only produce NOT_DECIDABLE, which costs N + 1. It cannot produce a false pass. The binding constraint is the read instant (100 counted trades on 5 dates before the end).
2. Disclosure (template B item 2, verbatim from the arena records): proxy counter on arena tape, a different feed; an upper bound (synthetic and unclassified pools included); pre-redeploy days; day 1 undercount, day 3 lower bound; the old Am.3 bar; the trigger rate after the redeploy is unmeasured.
3. A post-halt count of Q50's trigger records is NOT required. It is NOT allowed by opening Q50's shadow directory. If anyone wants it, it runs only through a merged, QP-OK'd, counts-only tool that loads decision fields only, and its result may not decide registration (R0 already decides that).
4. Reuse expires: if the successor's clock is more than 14 days after 2026-10-23T23:59Z (later than 2026-11-07T00:00Z), a new P0 is required. That is a fixed staleness limit, set now.

## R4. Re-pin after a FAILed write is not an override: YES, with conditions. A NEW state file is mandatory.

1. Q50's FAIL and end stay final. Its state file `~/data/structure-monitor/a3-redeploy-state.jsonl` is never edited, truncated or moved. It is kept as Q50's record.
2. **A new state file per successor guard pin is REQUIRED, not optional.** At the guard pin, `failclosed_bound_ms` (`tools/a3_redeploy_check.py:968-990`) lowers the halt bound of **any later** enumeration failure for a program to the `deploy_utc` of any sticky non-PASS entry of that program. With Q50's state file, the successor's first PumpSwap enumeration failure would end it at Q50's old redeploy instant, before its own clock. The successor's loop passes `--state <new path>`. The path and an empty-at-start sha256 go in the records PR.
3. The new pins judge only books whose clock is after the re-pin's merge commit time. Q50 stays listed in books v3 (`new_rule_bot_ids`). An end never moves later, so this is harmless.
4. Required evidence for the re-pin: the `--observe` output (all of checks a-h evaluated; every FAIL reason explained); a review doc to the #780 standard (C3' (a)-(d), including the Token-2022 base-mint check); the IDL diff; `deploy_utc` on every program entry of `tools/pump_structure_pins.json` (Am.4 1.6 B-1; a pins PR without it is refused); the R2 items; QP OK on the exact head and a reviewer verdict on the PR, before the successor's registration.
5. Guard pin: base plus exactly the check tool, its tests and both pins files. Tests green at the pin on fast-0 as a job. For a successor at S* or S*+patch, the base is the successor sha (see R5.4), not 136cd98.

## R5. Decoder patch (H2): YES, with conditions

1. **Fixture capture is outside the EXP-024 section 3 seal only if** it is the A3 check's own pinned sample (the first 500 txs from deploy + 150 slots, migrate K = 40), stored as event-level raw bytes (discriminator, length, base64), at most 20 events per discriminator in committed fixtures; no mint- or pool-keyed time series is assembled; no trigger, entry, exit or price path is computed; and no shadow or Engine run is made on the captured transactions. It reads the same fields the A3 check is allowed to read.
2. Tests (a) to (d) of playbook section 2.4: required.
3. **One proof added:** the md5 on 2026-09-20 (Runs A and B) exercises only the OLD layout. Also required: the 2026-09-20 events **re-encoded into the new layout** (new fields set to zero, or to the observed constant) and run through S*+patch, giving an md5 EQUAL to the original Run A and Run B. That shows that the new-layout decode yields the same decisions on the same economic content. Plus an AST identity test showing that no decision-path function outside the decoder files changed from S*.
4. Guard (h): YES. `frozen_sha`/`frozen_blobs` in `tools/a3_redeploy_pins.json` = S*+patch. Because `frozen_guard` compares the guard checkout's own blobs and the check imports the decoders from that checkout, the guard pin's base must contain S*+patch's `FROZEN_FILES`. This also closes the audit MINOR (the guard checked 1f7a89c, not the book's sha).
5. QP reviews the patch in full (it is not a fill).

## R6. Fill-only review: YES, with conditions. The playbook's FILL list is incomplete as written.

1. Defect: the templates carry 24 FILL tokens, but section 2.3 lists 10, with naming mismatches (`clock` vs `clock_start_ms`, `halt_ms` vs `halt`). The playbook's own R6 makes every token outside 2.3 a full review. Fix before the template round: section 2.3 lists every token. Tokens fixable now are filled now: `succ_end` (= the S* constant), `read_tool`, `id_test`, `identity` and `diff_summary` (for S*), `qp_ruling` (this file), `n` (the next free DEC-031 amendment number at merge; a renumber is a fill).
2. QP OKs templates A and B, the P9 manifest, the P8 book table and the P4c proposer **now**, at exact heads, with each file's sha256 in the OK.
3. Post-halt: one QP round is enough for the fill diff only if a script substitutes the FILL values into the OK'd template and `diff` against the PR text is empty. Any other changed byte means a full review.
4. NOT fills (each gets a full QP review in the same round): the halt-class record (R1), the re-pin and its review doc (R4), the H2 patch and proofs (R5), the H4a/H5a evidence.

## R7. The successor's end and coverage

- **(a) Confirmed.** At F50, fresh-forward mode builds the Engine with `h5_look2_start_ms=H5_LOOK2_END_MS, h5_look2_observed=False` (`h5_shadow.py:3247`). `_h5_sealed_now` returns True for every s0 >= 2026-11-06T00Z (`:2215`), whatever the wall time. The scorer counts those triggers as `sealed_decision_only`, not part of the book (`arena_h5_score.py:633`). A same-sha successor can never score a pool with s0 >= 11-06. **DEC-032 Am.2 item 12 is internally inconsistent:** 12.1 "the same `frozen_sha` as Q50" cannot coexist with 12.2(a)'s "`SEAL_END_MS` code change ... at its sha" (the seal is inside the frozen sha), and 12.2(b) (clock >= 11-14 at F50) yields 0 scorable trades. Replacement text is below; it must be in the draft before DEC-032 Am.2 merges, and in any case before any 11-06 successor registers.
- **(b) Pre-building base S* now: ALLOWED.** It is outcome-blind (no Q50 output is needed), and a code change is a new `bot_id` anyway (DEC-031 §1.2). Conditions:
  1. **The diff is confined to the fresh-forward branch.** `H5_LOOK2_END_MS` and the non-fresh path stay byte-identical. Trap: `_h5_sealed_now` compares against the module constant `H5_LOOK2_END_MS`. Raising that constant would widen EXP-024 Am.2's `--h5-look2-observed` window for non-fresh processes past 11-06, which is a seal change. That is refused. Required form: a new constant `SUCC_END_MS` used only when `fresh_start_ms` is set (an Engine parameter), plus the fresh call site.
  2. The executor's `SEAL_END_MS` is NOT part of S*. The executor runs on main, not at `frozen_sha`. Its change goes by the S17 path (its own EXP-024 executor note and md5 replay at the flip commit).
  3. Proofs at S*, all before any successor relies on it: md5 decision-equivalence on 2026-09-20, Run A (default) and Run B (`--q-star-sol 50`), EQUAL (the #1456 pattern). **This alone is not enough**, because 09-20 never reaches either end constant and so does not exercise the changed line. So also: a unit test on synthetic 2026 times, where in fresh mode s0 in [11-06, SUCC_END) gives outcome records and s0 >= SUCC_END gives decision-only; in non-fresh mode, with and without the observed flag, behaviour for s0 in [10-16, 11-06) and >= 11-06 is identical to F50; an AST identity test showing every function other than the changed lines byte-identical to F50; and decode identity against 1f7a89c (the G6 pattern), or S*'s own blobs pinned in guard (h) (preferred).
  4. S* is **tagged** on origin (`q50-succ-base-1`), not just a branch head, so that a deleted or force-pushed branch cannot lose the `frozen_sha`. Not merged to main.
  5. `SUCC_END_MS` is chosen once, before the md5 job, and never edited. A different value is a new sha with new proofs. Allowed values: at or after 2026-11-14T00:00:00Z (EXP-024's latest end state; no H5 seal applies after it). **Recommended: 2026-12-01T00:00:00Z**, so that one S* also serves the DEC-032 Am.2 item-12 successor (clock >= 11-06, H = 14). The book's effective end is always min(clock + H, SUCC_END_MS). 2026-11-14T00Z is acceptable but leaves an 11-06 successor 8 dates.
  6. S* runs nothing until template A (coverage to `SUCC_END_MS`) and the section 3.5 text below have merged.
- **(c) Pools with s0 in [11-06, 11-14).** The records conflict. DEC-031 §3.5 lets v2 count from 11-06 without a note. EXP-024 Am.10's gating line, Am.10 (g) and DEC-032 Am.2 12.2(b) treat Q50-rule outcomes before 11-14 as needing a declaration. **The stricter reading binds:** a successor counts pools with s0 in [11-06, 11-14) only under an explicit declared observation (template A) that merges, with QP OK and a reviewer verdict, before the successor's process starts. Pools >= 11-14 are outside every H5 seal, and template A states that.
- **(d) DEC-031 §3.5 end-seal successor text** (merge in template B or its own amendment, before the successor's process start):
  > "§3.5, successor end-seal for `H5-BOOSTFLOOR-Q50 v1` successors. For a `bot_id` registered as a successor of `h5v1q50-boostfloor-a1` at `frozen_sha` S (tag `q50-succ-base-k`, or S plus a QP-ruled decoder patch), the arena end-seal is `SUCC_END_MS` = <value> as pinned in S's code. Its declared observation is the EXP-024 Note under Amendment 10 (successor) for that `bot_id`. Pools with s0 in [2026-11-06T00Z, 2026-11-14T00Z) are covered only by that note. The book's window is [clock, min(clock + H, SUCC_END_MS)). The 2026-11-06T00Z end-seal is unchanged for every other book, for the Q50 process at F50, and for the executor until its own note and md5 proof merge."
- **DEC-032 Am.2 item 12.1 replacement** (in the draft before it merges): "A new `bot_id` with the same `rule_id` as Q50, at `frozen_sha` = the QP-ruled successor base S (QP-RULING-1024-successor R7), not F50; F50 cannot score any pool with s0 >= 2026-11-06T00Z." Delete 12.2(b) or mark it "yields no scorable trade at F50".

## R8. Identity, N, c and siblings: YES, with conditions

1. `h5v1q50-boostfloor-s<k>` keeps the three Am.5 2.1 properties (it starts `h5v1`; `h5v1q` is not `h5v1-boostfloor-`; it starts `h5`). This is shown by a committed test against `set_window_end`'s prefixes and `a3_halt.validate_books`.
2. N + 1 at the successor's clock. Q50 stays in N as ended (with its counts only and its end class). c is unchanged until a screen pass, and a successor screen pass adds 1 to c. No carry-over from Q50: no trades pooled, no rung, no confirmation credit, no P0 except R3's disclosed reuse.
3. Every successor read prints, side by side and counts only: Q50 (end class, counted trades at end), `h5v1-boostfloor-a2` and `h5v2-boostclock-a2` (ended, 0 attempts), and every earlier successor, with the shared-pool count (Am.3 8(e)). A successor pass is best of N and in-sample against Q50.
4. A successor of a successor is allowed by the same path, at +1 N each, only after a pre-read A3/dead-man end (R1.1). Never after any read.
5. The successor gets its own wrapper/read-tool entry. `tools/arena_h5_score_q50.py` (sha256 `f5a479ad…`) and `tools/arena_h5_q50_instant.py` (`END_SEAL_MS` hard-coded to 11-06 at line 37) are not edited for Q50. The P8 book table carries the end per book.

## R9. Fees-program check deployment

1. **Default rule: deploy only after Q50 has ended**, as part of the successor's guard pin, with the successor listed under the new key.
2. Earlier deployment while Q50 runs is allowed only with: Q50 not listed under the fees key; a replay of every daily record line on both hosts through the old and new pins, giving byte-identical verdicts, `halt_at_ms` and reasons for every Q50 list (0 changes; the PR #840 C5 pattern); a fixture showing that a fees-program-only change with Q50 in `new_rule_bot_ids` still HALTs at the flag instant as before; and **`tools/a3_halt.py` changes are covered too**. `validate_books` requires exactly the two `BOOKS_KEYS`, so a third key changes `a3_halt.py`, which Am.10 pinned byte-identical and on which the dead-man jobs' byte-identity rests. That replay therefore also covers `apply` and `deadman`, and the dead-man jobs move to the same pin. Without all of that, refused.
3. Schema: `{"legacy_bot_ids", "new_rule_bot_ids", "fees_check_bot_ids"}`. `fees_check_bot_ids` is a subset of `new_rule_bot_ids`, and an empty list is valid (unlike the other two). Tests cover it.
4. H4 with unchanged tiers allows a same-sha successor: YES, under R1 H4a. If the fees check is not deployed by the successor's clock, its registration discloses that a second fees-program deploy ends it with no check.

## R10. Clock mechanics

1. **The first official run after a loop redeploy counts as the pre-clock green run** (Am.10 5(4) precedent) **only if it runs on books v3 with the successor already registered** (Am.5 2.2: "one full green a3-daily run on the new books file"). The playbook's section 3 order (loop redeploy at 8-9 h, registration and books swap at 9-10 h) does not meet this. Required order: re-pin merged -> guard pin tests green -> **cancel the old loop's queued successor, then the old loop, ps check** -> registration with the books v3 swap in the same command -> start the new loop at the new pin with the new state file (its first run is official) -> that run green (all RCs 0, verdict NONE, six flags evaluated) -> shadow start (after `registered_at_ms`) -> clock. The old loop must be gone before the books swap: an old-pin run with Q50's state file and old pins would evaluate the successor and could end it before its clock (R4.2). Forbidden minutes (:04-:25 of 07/13/19; 12:10-12:25) apply to the cancel, the swap and the start.
2. **Minimum gap registration -> clock: none fixed.** Required before the clock: the bots.json line and notebook copy; the green run of item 1; the successor's shadow running with its start record written and at least 1 h of clean log (no restart loop) before the clock; the day-1 dry-score job and the halt-alert job submitted; the kill-review line. The records PR follows within 24 h.
3. **Q50 shadow #1419 after Q50's end:** keeping it is allowed (its outcomes stay inside EXP-024 Am.10 (a)'s coverage to 11-06, and the scorer skips them after the window end). **Recommended: cancel it**, queued #1421 first, then #1419, with a ps check. Its post-end outputs are dead data and fast-0 memory is capped. Its output directory is kept as Q50's record and never deleted. Post-end Q50 outputs are never used for anything, including P0, sanity checks or a successor's design.

---

## Deadlines for text that must merge in advance

| Text | By when |
|---|---|
| R0 successor policy (records PR) | before 2026-10-25T00:00:00Z; if later, the disclosure of R0 applies |
| DEC-032 Am.2 item 12.1/12.2(b) correction (R7) | in the draft before DEC-032 Am.2 merges; before any 11-06 successor registration |
| PLAYBOOK section 2.3 complete FILL list + pre-filled tokens (R6) | before the template QP round; templates OK'd before any halt (not merged) |
| S* built, tagged, md5 + seal unit tests + identity (R7b) | before any halt (no rule deadline; P(halt by 10-30) about 0.57) |
| DEC-031 §3.5 successor text, template A (R7c/d) | merge before the successor's process start |
| Fees check (R9) | no deadline; default deploy with the successor guard pin |
| Am.11 read tool with book table and per-book end (R8.5) | 2026-10-30T00:00:00Z (Am.11 item 5) |

## What this ruling did not do
It ran nothing on any host, opened no sealed or Q50 output, and measured no trade count, rate or outcome. The survival figures are QP's earlier guesses from 5 deploy days. Unverified: whether `--observe` succeeds for a PumpSwap deploy on public RPC; fast-0 memory with two H5 shadows; the md5 run time at S*.
