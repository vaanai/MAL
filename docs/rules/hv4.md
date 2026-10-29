NON-BINDING INDEX of the binding text. On any conflict, the cited DEC/EXP text on main controls. Built 2026-10-29 at main e3a2090c; HANDOFF quotes re-cited at main 836b61ac. Any edit to this file is a T1 PR (DEC-013 Am.1).

# Rules for book HV4 (Haiku trader v4, the nine lane-G bots)

Each rule is a verbatim blockquote plus its citation `(path:line)` at the main sha above. Headings and the **Governs** lines are the index's words and bind nothing. docs/HANDOFF.md, the HV4 registration record and records notes are state documents, quoted only where no DEC text carries the fact. **The checker proves verbatim text only, not that a clause is in force or complete.** Check: `python3 scripts/ops/rules-quote-check.py --rev origin/main docs/rules/hv4.md`. No book has cleared the promotion gate; nothing here says any HV4 bot makes money. This file was written from repo text only: no HV4 output, ledger, decision row, card, fill, digest, job status or job log was opened.

**Source files.** The binding text is DEC/DEC-031-forward-paper-arena.md (Amendments 2 (HV4 coverage), 3 (item 6), 6, 7, 8 and 15, and the sections it cites; Amendment 4 appears only as text that Amendments 7 and 15 replace), the HV4 registration record `ARTIFACTS/lab/hv4-registration-1023.md` (its file name says 1023; it carries the 2026-10-25 clock; see Notes N2), the pinned DESIGN copy `ARTIFACTS/lab/hv4-registration-1023/DESIGN.md`, `ARTIFACTS/lab/qp-ruling-1022-hv4-am6.md`, and the pinned tools. Two HOST files are NOT in the repo (the checker cannot verify them; section 4b quotes the ruling as host text, sha c2343c9c, and the coordinator's QP pass verified that sha): `/data/mal/hunt-1008/push-1016/hunt30/hv4-read/QP-RULING-1025-hv4-read-rule.md` (full sha256 c2343c9c6ef96256419c4e7f4eec1b83584163bfd3a1aff97ca726cecdc50955; Amendment 15 is its copied text, and `ARTIFACTS/lab/c0-readiness-2026-10-25.md` section 2c restates it) and `/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md` (sha256 prefix 823cc062; quoted in section 7 only through the one line the task brief gave, as plain text the checker does not verify).

## 1. Identity, the nine bots, the frozen pins, T0 and END

**Governs:** Which books, which code, which clock, which window.

> Runner #1561, then #1562. NOTIFICATIONS ONLY. **Never call `miscusi_job_status` on #1561/#1562** (it is a seal view). | Read under DEC-031 Am.15 (in force; merged #915 2026-10-25T19:35:24Z) by `tools/hv4_am15_read.py --record`, at the B that `tools/hv4_read_instant.py` writes once (#1591, 01:30Z daily), at an as-of of at least B + 24 h (the wrapper refuses earlier). If B = 2026-10-30T00:00Z, the earliest read is 2026-10-31T00:00Z. One recorded read per bot. The attempts-vs-trades rule is QP-RULING-1025-hv4-read-rule R1, carried by Am.15. QP-RULING-1029-hv4-read-d1d4 (ops/manager17/, sha256 d5d7f115586bb5a0…) confirms D1–D4; this replaces the earlier 'needs its own QP ruling first' line. A days-only miss is FAIL, labelled "count-only, not a loss" (Am.15 item 2).

HANDOFF HV4 row at main 836b61ac (state document; PR #1081 replaced the earlier row text that said a QP ruling was owed). (docs/HANDOFF.md:22)

> `bot_id(arm) = f"hv4-{arm}"` (hv4_runner.py:103-104@9ed4c5f): `hv4-CON-L`, `hv4-CON-F`, `hv4-CON-P`, `hv4-CON-X`, `hv4-CON-R`, `hv4-BIG-L`, `hv4-BIG-F`, `hv4-BIG-X`, `hv4-BIG-R`. BST-L/F/X/R are not registered and get no read (Note N2).

Registration section 1, bot_ids. (ARTIFACTS/lab/hv4-registration-1023.md:73)

> `9ed4c5ff63935696b2a89150e13565e123047754` (origin/main, the merge of PR #774; SHAKEDOWN-CHECKS-4-NOTES-1 R-1 makes it `HV4_FROZEN_SHA`).

Registration section 1, frozen_sha. (ARTIFACTS/lab/hv4-registration-1023.md:74)

> `1792886400000` = **2026-10-25T00:00:00Z** (P7 constant; check with `date -u -d @1792886400`). **T0 rule: DEC-031 Am.6 item 1.4. T0 is the first 00:00Z after (a) the smoke has passed at `9ed4c5f` and (b) the registration record is notebooked. Never earlier (Am.3 item 1).**

Registration section 1, clock_start_ms and T0 rule (the row's later sentences about moving the clock to 10-26 are conditional; Am.15's own header gives the HV4 clock as 2026-10-25T00:00:00Z; see Notes N2). (ARTIFACTS/lab/hv4-registration-1023.md:75)

> 0.25 for every arm (`register_arms`, hv4_runner.py:122@9ed4c5f; `Runner.register_all` and `main --register` both come through it)

Registration section 1, deciding_stake. (ARTIFACTS/lab/hv4-registration-1023.md:76)

> **DONE** (E3, MiScusi job #1549, `j_1tTEq79ASP0Zpg`, git `9ed4c5ff`, `register_rc=0` at 2026-10-24T21:17:38Z (`date -u`), after the Am.2 merge 2026-10-19T17:57:08Z and before T0). Header `{"lane_h":false,"n_bots":9}`; `model_ids` as in the row above; `clock_start_ms` 1792886400000; stake 0.25; `frozen_sha` 9ed4c5ff63935696b2a89150e13565e123047754.

Registration section 1, registration lines (the nine lines themselves are on the same row). (ARTIFACTS/lab/hv4-registration-1023.md:80)

> **DONE:** the printed id is `["claude-haiku-5-5"]` on the L, F, P and R arms and `[]` on CON-X and BIG-X (E3 job #1549, notebook `n_UkN1hTGlEeQ8zA`; verbatim output `n_gISv0_7I1449FQ`).

Registration section 1, model_ids. (ARTIFACTS/lab/hv4-registration-1023.md:77)

> Thinking **disabled**; **no temperature sent** (the API default). `max_tokens`: entry 400, risk 400, recheck 120, review 4000

Registration section 1, sampling settings. (ARTIFACTS/lab/hv4-registration-1023.md:78)

> **Constants (QP ADDENDUM 1 P7).** `CLOCK_MS` 1792886400000 (2026-10-25T00:00:00Z). `END_ISO` 2026-11-08T13:05:00Z. Segment 1 END 2026-11-07T12:05:00Z. Entries at or after 1794096000000 are not counted. `H_read` 349 h. No review on 2026-10-25; first review 2026-10-26T00:05Z. Digest: first partial 2026-10-25T23:50Z, first closing 2026-10-26T00:15Z.

Registration section 0, constants (T0, END, H_read, review and digest days). (ARTIFACTS/lab/hv4-registration-1023.md:13)

> **H = 14 d by the window-end cap, for every rate. The two must match, else no clock.**

Registration section 0, Amendments (H pin). (ARTIFACTS/lab/hv4-registration-1023.md:19)

> For HV4, H = 14 days by Amendment 4 item 2.2's window-end cap (QP ruling P3). The `bots.json` line itself carries no H; that is the only difference from Amendment 4 item 2.2. Every other word of Amendment 4 item 2.2 applies unchanged: H is computed from the P0 rate by the same formula, capped by any end-seal or window end, and never extended.

Am.7 item 1 (H pin location and value). (DEC/DEC-031-forward-paper-arena.md:420)

> Scope: `hv4-CON-L`, `hv4-CON-F`, `hv4-CON-P`, `hv4-CON-X`, `hv4-CON-R`, `hv4-BIG-L`, `hv4-BIG-F`, `hv4-BIG-X`, `hv4-BIG-R`, at frozen sha 9ed4c5ff, clock 1792886400000; both their screen reads and their §2 step 4 confirmation reads.

Am.15 scope sentence. (DEC/DEC-031-forward-paper-arena.md:571)

> END_ISO `2026-11-08T13:05:00Z`; segment 1 END `2026-11-07T12:05:00Z` (14 d limit), segment 2 after-chained with a 26 h limit; declaration (to be pasted verbatim in the notebook copy): "The T0 process runs to 2026-11-08T13:05:00Z so that every attempt entered before 1794096000000 can close. Entries at or after 1794096000000 are outside the window and are not counted in any read.

Registration section 1, T0 job (END, declaration). (ARTIFACTS/lab/hv4-registration-1023.md:82)

> Both (i) and (ii) hold at `9ed4c5f`, so HV4 is covered: the deciding rent is 0 on a filled trip.

Registration section 2.3 (Am.2 coverage: rent 0 deciding). (ARTIFACTS/lab/hv4-registration-1023.md:132)

> **Read statement.** Rent 0 decides; the rent-charged leg is printed beside it (Am.2 items 7, 9).

Registration section 2.3. (ARTIFACTS/lab/hv4-registration-1023.md:136)

> **Never retroactive.** No registered or running book is covered: HV4 (frozen sha 9ed4c5f), H5 v1/v2 a2, H5-Q50, the books run by `tools/arena_rules_v2.py` (FLOOR) and every other book keep 505,000 for their whole counted window, with their pinned stacks and sheets unchanged.

Am.8 item 3 (HV4 keeps 505,000 per send). (DEC/DEC-031-forward-paper-arena.md:433)

> - **HV4.** HV4 is covered if and only if its registration record is after the merge. If HV4 registers before this amendment merges, it is not covered and Amendment 2 item 2's clean-24 h-stub condition stays for it. If it is covered, item 1.4 replaces Amendment 2 item 2's sentence that T0 is "the first 00:00Z after a clean 24 h stub run at the final sha and the registration merge".

Am.6 scope bullet for HV4. (DEC/DEC-031-forward-paper-arena.md:370)

> Am.6 status checked: squash-merge 90ab73dc, committer time 2026-10-22T12:28:47-07:00 = **2026-10-22T19:28:47Z** (the merge instant). QP OK and reviewer APPROVE are on PR #797 at 74644d84 (19:28:32Z / 19:28:35Z); `git diff 74644d84 90ab73dc -- DEC/` is empty. HV4 has no bots.json line and no notebook copy, so its registration record will be after 19:28:47Z: **HV4 is covered.**

QP ruling 1022, Am.6 status. (ARTIFACTS/lab/qp-ruling-1022-hv4-am6.md:5)

> `c9b5cf1d18ff2cddc3171b38d63ed1aeffc1d7c3577029c60c2551cf99efa421` | tools/arena_am6_cut.py |
> | `ef6e4555bc9e49272a5764bd75e1510f210af669a7a974cc68042b7eaaef32b3` | tools/arena_am6_read.py |
> | `dfcc5cd6af1655deff20484a1353e3e7a2a236663232068eb6c1bf122f85fcfb` | tools/arena_am6_exclusions.py |
> | `227f92be466fec0bd6c6502bcece6b2e7755d0a675a30d08a2dd79d6353903b5` | tools/arena_hour_health.py, **the pin for HV4's L6/L7** (PR #805 merged `0a5aef86`; adds the feed-status checks). The P0 gap check alone stays at `a238ab48` / sha256 `71a6142a12bea55cc5860eb1c651babb99f6443d4a73e5a6edc79f49123b648a` (PR #799), per notebook `n_qPU6rSVkg86cXw` |
> | `a508ed79ed1cc425af0aa7dcd08e3ef5f8599dd6a45cb5e47cc8a779c6a0cd34` | tools/arena_score.py (equals the section 2.2 pin) |
> | `3e2539d063af712e7b8db085f62c5543c91fa0e03233087a10ac71756a88c4c7` | tools/hv4_p0_count.py (merged `6cacadfe`, PR #803) |

Registration section 0, tool pins (read-path tools, the hour-health tool for L6/L7, arena_score and the P0 counter). (ARTIFACTS/lab/hv4-registration-1023.md:25)

> `9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514` | tools/arena_paper.py | unchanged since `d1dbcd6`. **Differs from the 2026-10-18 pin** (`9a23f2aa…`, arena-records-2026-10-18.md §1) by PR #745 (`trim_history()`, opt-in). Disclosed in every HV4 read (DEC-031 §1.4); QP ruled on #745 and #755. |
> | `a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778` | tools/arena_stack.py | unchanged; equals the 10-18 pin |
> | `8af345618e51ca480ee7538717aa001b4ad7ed5ef38941faaec812bbe217f639` | tools/arena_state.py | unchanged; equals the 10-18 pin |
> | `7a9d424b6584b097ee61e4c7b48bef6e43b317bdd438ba588074057b5828826f` | tools/arena_feed.py | unchanged; equals the 10-18 pin |
> | `a508ed79ed1cc425af0aa7dcd08e3ef5f8599dd6a45cb5e47cc8a779c6a0cd34` | tools/arena_score.py | unchanged; equals the 10-18 pin |
> | `048dd78901c52e0c14fe9b1611f354c49ac6d0bb37fad8e5be3e27c756e3e2af` | tools/arena_rules.py | unchanged (CHECKS-4-NOTES-1 R-2 lists it as byte-identical to `b240191`) |
> | `5649359077cf92020be613da8fc644165cad6bdc32e42f82fbb876f193b9574a` | tools/hv4_runner.py | **changed since `d1dbcd6`** (v2 observability rows, prefetch policy v2 wiring, scan speed-up, `register_arms`/`arm_names_for`, the `--register` branch of #774) |
> | `fb33f750d2ba270711aa37b86348505d93946b974f071b765ff317e36f64ce52` | tools/hv4_cards.py | **changed** (Note N4's `CHAIN_HOLDERS_MAX_AGE_MS_V2` and `holders_max_age_ms`; scan-speed helpers). The V-band filter (2.3) is the same check as at `d1dbcd6`, at new line numbers. |
> | `9dda6a88c2fce3faefe5115d2cf47ef80c6600d824ba005610f956223cd22cce` | tools/hv4_chain.py |

Registration section 2.2, code pins (first rows; the table continues to line 124). (ARTIFACTS/lab/hv4-registration-1023.md:108)

> `{"e1_floor_per_005":384022,"exit_lag_s":0.55,"feed_delay_slots":7,"guard":1.15,"h_in":0.002608,"h_out":0.0016,"k_rounding":"ceil","portal_ppm":0,"press_intercept_provisional":-1.4197242838379789,"rent":0,"send":505000,"strand_rule":"both"}`

Registration section 2.3, full StackConfig pin (sha256 on the next line, 135). (ARTIFACTS/lab/hv4-registration-1023.md:134)

> sha256 `1e611a5a636dfe6bd563ab5258f6b498cdb8906500ee8bf2f0264a2621621986`. The read compares every key on every start row and open row against this pin.

Registration section 2.3, config pin sha and use. (ARTIFACTS/lab/hv4-registration-1023.md:135)

> | `bcd26affadf1c9900852ee9f2f3489b46c418b5ee393f75d154d965ee6e176d8` | `DESIGN.md` (with Amendment A1 and Notes N1, N2, N3, N4) | `/data/mal/hunt-1008/push-1016/hunt20/haiku-v4/DESIGN.md` | the design; Note N4 sets the v2 chain rules and the CAP 2,000 |
> | `55fbba0559327c1e18728596506de11fdf0408ae9bedcee49a21966d9314a3af` | `SHAKEDOWN-CHECKS-3.md` | `.../haiku-v4/SHAKEDOWN-CHECKS-3.md` | pass checks 1-11, Rule M, run and T0 rule (revision 3; supersedes -2) |
> | `0f093e68140f021f53979daa7b6251f6b2561b0194e46f3576e77e44fbf1426c` | `SHAKEDOWN-CHECKS-3-NOTES-1.md` | `.../haiku-v4/SHAKEDOWN-CHECKS-3-NOTES-1.md` | Rule M guards N-2, check 5 counters N-3, N-5, F2, F3 (the corrected version of 2026-10-20T00:57:18Z; the superseded first version `be6df321...` is not cited) |
> | `a9b019ca9c8db38e4172a04ceb338c6edc03ef17333bc0ae3d462079bc7dd583` | `SHAKEDOWN-CHECKS-4.md` | `.../haiku-v4/SHAKEDOWN-CHECKS-4.md` | revision 4: DESIGN pin with N4, check 5 limits 2,000 an hour and 48,000 a day, new check 12 |
> | `3e8032b5b431293e31ea7e0969e48ff3645daf29cddf2e928d3e8b3a08ceebde` | `SHAKEDOWN-CHECKS-4-NOTES-1.md` | `.../haiku-v4/SHAKEDOWN-CHECKS-4-NOTES-1.md` | R-1 run sha `9ed4c5f`; R-3 proof #1278; R-5 fresh directory; R-6 rule M applies; R-7 conditions |

Registration section 2.1, document pins (first five rows). (ARTIFACTS/lab/hv4-registration-1023.md:90)

> Tool `arena_hour_health.py` sha256 `227f92be...`. One threshold per check per UTC hour; denominator = that hour; the pinned file is `ARTIFACTS/lab/hv4-thresholds-FINAL-1025.json`, a byte-identical copy of `l7-1025-out/hv4-thresholds-FINAL.json`, `THR_SHA256` `da13ccc28457eaab2906548f318f76ce5c4598ecf7c007affcc3d8e7bd8b2eed`.

Registration, L7 thresholds file. (ARTIFACTS/lab/hv4-registration-1023.md:48)

> PINNED_ARENA_SCORE_SHA256 = "a508ed79ed1cc425af0aa7dcd08e3ef5f8599dd6a45cb5e47cc8a779c6a0cd34"  # load_decisions read from here (blob at 9ed4c5ff, the HV4 frozen sha)
> PINNED_AM6_READ_SHA256 = "ef6e4555bc9e49272a5764bd75e1510f210af669a7a974cc68042b7eaaef32b3"
> # HV4 record section 0 pins (ARTIFACTS/lab/hv4-registration-1023.md section 2.1); the on-disk files must match or the tool refuses (QP, #915).
> PINNED_AM6_CUT_SHA256 = "c9b5cf1d18ff2cddc3171b38d63ed1aeffc1d7c3577029c60c2551cf99efa421"
> PINNED_AM6_EXCLUSIONS_SHA256 = "dfcc5cd6af1655deff20484a1353e3e7a2a236663232068eb6c1bf122f85fcfb"

tools/hv4_read_instant.py pins (code, lines 46-50). (tools/hv4_read_instant.py:46)

> - Only trades with entry decision time at or after `clock_start_ms` count. Any code or prompt change makes a new `bot_id` with a new clock. The old book keeps running or stops; it is never merged with the new one.

DEC-031 section 1.2. (DEC/DEC-031-forward-paper-arena.md:19)


## 2. What is counted: attempts versus trades

**Governs:** The words DEC-031 and its HV4 amendments use for the thing that is counted at each step. This section only collects the definitions; section 4 holds the rule that uses them and the open point.

> **When:** the first 00:00Z boundary at which the book has at least 100 counted attempts (every entry decision, guarded reverts included) over at least 5 UTC dates. The count uses decision timestamps only, by a tool that loads no fill, exit or P&L.

DEC-031 section 2, When (for the nine HV4 books, Am.15 item 1 replaces this sentence, see below). (DEC/DEC-031-forward-paper-arena.md:43)

> **What it covers:** the attempts decided before that boundary, once all have exited. Nothing else starts, delays or repeats the read. A FAIL or NOT_DECIDABLE is final for that `bot_id`.

DEC-031 section 2, What it covers. (DEC/DEC-031-forward-paper-arena.md:44)

> 1. **Read instant.** For these books, §2 "When" and Am.4 item 2.3 read: B is the first 00:00Z boundary with clock_start_ms < B <= 1794096000000 such that, among the attempts decided in [clock_start_ms, B) and not dropped by Amendment 6 as decided in an excluded hour or restart gap, the attempts of kind `open` (O) and kind `refused`, any reason (R), satisfy O - floor(0.05 x (O + R)) >= 100, and the decision dates of the O attempts cover at least 5 distinct UTC dates. The count uses decision timestamps and row kind only and loads no close, exclusion class, fill, exit, leg or P&L. No such B: the book ENDS NOT_DECIDABLE at 1794096000000, final, counts only.

Am.15 item 1. (DEC/DEC-031-forward-paper-arena.md:572)

> 2. **Trade set.** The gate's n is the attempts of kind `open` decided before B, not dropped, with a priced `trade` row at stake 0.25 and not excluded (§1.4, missing V, late-print gap, engine restart, Am.6 span or open-at-restart). Guarded reverts and strands are trades at their booked value. Refused buys are attempts, never trades. Unfilled landings are inside the fail-model blend and remove nothing. A count-only miss at B is final and labelled "count-only, not a loss"; there is no waiting to a later boundary.

Am.15 item 2. (DEC/DEC-031-forward-paper-arena.md:573)

> 3. **Confirmation.** §2 step 4's "at least 100 attempts on at least 5 further UTC dates" reads "item 1's margin condition over attempts decided after the screen read's instant, on at least 5 further UTC dates", within the same 1794096000000 cut-off.

Am.15 item 3. (DEC/DEC-031-forward-paper-arena.md:574)

> RULE. B is the first 00:00Z boundary with clock_start_ms < B <= END (1794096000000 = 2026-11-08T00:00:00Z = clock + 14 d) such that, among the attempts decided in
> [clock_start_ms, B) and not dropped by Amendment 6 (excluded hour or restart gap), the non-refused attempts O (kind `open`) and the refused buys R (kind `refused`,
> any reason) satisfy O - (O + R) // 20 >= 100 and the O decision dates (UTC) cover at least 5 distinct dates. No such B once --as-of-ms >= END: NOT_DECIDABLE,
> final, counts only. Before that: NOT_READY (nothing written). Confirmation (item 3): the same condition over attempts decided in [B1, B2), B1 = the screen
> record's instant, same END cut-off.

tools/hv4_read_instant.py docstring, RULE (code text; the lines in between are wrapped in the source). (tools/hv4_read_instant.py:7)

> INPUTS (the only fields used): the lines arena_score.load_decisions reads (files <root>/<bot>/trades-*.jsonl, lines starting '{"kind":"open"' or
> '{"kind":"refused"'; only the kind and decision_ms.entry are parsed); the notebooked Am.6 hour tables with their shas; the restart gaps (--restarts, required).
> No close, exclusion class, fill, exit, leg, price or P&L is ever parsed.

tools/hv4_read_instant.py docstring, INPUTS. (tools/hv4_read_instant.py:13)

> - **Feed gaps:** a trade whose decision-to-exit span holds a feed gap the feed logged (a disconnect or resubscribe), or a print the stack needs without V, is excluded by that rule alone and counted. If more than 5% of a book's trades are excluded this way at its read, the read is NOT_DECIDABLE.

DEC-031 section 1.4, feed gaps (5% of trades). (DEC/DEC-031-forward-paper-arena.md:32)

> - **Strand class:** an unpriced or invalid exit, or proceeds below one send, is a total loss of stake plus the buy send and rent. It is never dropped.

DEC-031 section 1.4, strands are trades. (DEC/DEC-031-forward-paper-arena.md:30)

> 4. **Excluded hour.** A UTC hour that fails any pinned check is EXCLUDED for every covered book that reads that host or feed. The decision is automatic, from the tool's output alone. Whole UTC hours are used.
>       - An attempt DECIDED in an excluded hour (either label) is excluded by decision instant. It neither counts (§2's 100 attempts on 5 UTC dates, the read boundary) nor is read, and it counts toward item 3's 10% cap only.
>       - An attempt decided in a healthy hour whose decision-to-exit span overlaps a price-path excluded hour is excluded under §1.4's feed-gap rule and counts toward §1.4's 5% cap only.
>       - No attempt counts in both caps.

Am.6 item 2.4 (excluded hours drop attempts by decision instant). (DEC/DEC-031-forward-paper-arena.md:398)

> 3. **Cap.** If more than 10% of a book's attempts are decided in excluded hours, the read is NOT_DECIDABLE, final for the `bot_id` (§2). The denominator is all attempts decided before the read boundary, counted by decision timestamp only, with §2's count tool. One denominator: there is no cap on hours. §1.4's 5% cap (trades excluded by the feed-gap rule, including the span exclusions of item 2.4 and the restart exclusions of item 4) stays separate, on its own denominator, and so do Amendment 1's and Amendment 4 item 1.13's. The first cap to trigger decides, and none widens, resets or replaces another.

Am.6 item 3 (10% cap, attempts; 5% cap, trades). (DEC/DEC-031-forward-paper-arena.md:405)

> 4. **Crash and restart.** A crash or restart of a covered book's process, restarted from a clean checkout at the same `frozen_sha`, creates no new clock and no new `bot_id`. The restart gap (from the last decision or heartbeat row before the stop to the first row after the restart) is excluded in whole UTC hours, rounded outward, as decision-input hours, and recorded in a notebook entry with the instants. For a book covered by this amendment, this item governs: positions open at a stop are §1.4 exclusions (process_restart / engine_restart) counted in its 5% cap. Amendment 3 item 8(j) is unchanged for every other book. A restart on any other sha is a new `bot_id` with a new clock (§1.2). An open attempt left unclosed for more than 24 h after a restart is an orphan and makes the read NOT_DECIDABLE (DEC-014 Amendment 1; §2), as now.

Am.6 item 4. (DEC/DEC-031-forward-paper-arena.md:406)

> 1. A trade whose sell was refused or delayed is kept and counted **at the fill it actually got**; never dropped or re-priced.

Registration section 3, Declaration 1. (ARTIFACTS/lab/hv4-registration-1023.md:150)

> 5. **C2 membership (QP, before T0, outcome-blind).** "A trade is in a catch-up window if its entry `call_ms` or its exit `call_ms` falls inside the window. Stamps are not used."

Registration section 3, Declaration 5. (ARTIFACTS/lab/hv4-registration-1023.md:154)

> 6. **D5, guard lag (QP, before T0, outcome-blind).** "Per open row, guard_lag_slots = `effective_slot.entry` - `call_feed_slot`. Every read reports, per book, its p50/p99/max and the number of entries the guard reverted. Trades with guard_lag_slots > 25 (10,000 ms at 0.4 s/slot, check 6's max) form a set. The verdict is stated with and without that set, beside C2's catch-up set, and with and without the union of the two. Fills are kept as booked (Declaration 1)."

Registration section 3, Declaration 6. (ARTIFACTS/lab/hv4-registration-1023.md:155)

> **NOT_DECIDABLE rule, pre-registered before T0 (QP on #745 item 4 (3)).** If any line of `engine-trim-counters.jsonl` has `rows_below_trim_floor` > 0 or `refused_history_trimmed` > 0, equivalence with an untrimmed engine is void for every book on that engine for that segment, and the read is NOT_DECIDABLE.

Registration section 4, trim NOT_DECIDABLE rule. (ARTIFACTS/lab/hv4-registration-1023.md:216)

> The read lists every catch-up window: the T0 start and each restart, by QP-RULING-1019-startup (a) (from the start row to the end of the first full minute with `tick_timing.exit_check_delay_ms` p99 <= 2,000 and max <= 10,000, capped at 10 min; a catch-up longer than 10 min fails check 6). Membership is by the wall-clock `call_ms` of the entry or the exit (section 3, item 5). Per book it reports the trades whose entry or exit decision falls inside a window.

Registration section 5, C2 (the rest of that paragraph, on the same line, gives the with-and-without statement). (ARTIFACTS/lab/hv4-registration-1023.md:251)


## 3. The read instant: Amendment 15, daily job #1591, write-once records

**Governs:** When the read happens and what writes the instant.

> 4. **Tools and seal.** No HV4 read, preview or count runs until a read-instant tool for item 1 and a wrapper that runs `arena_am6_read` (ef6e4555) and `arena_score` (a508ed79, or c4859323 disclosed) unchanged on disk with only the boundary replaced have merged, with QP OK on the exact head and a reviewer verdict, before 2026-10-30T00:00:00Z. A boundary that the unamended tools print is an attempt-boundary record, not the read instant. `tools/hv4_read.py book` is not a gate-read path. If this amendment is not merged by then, §2 applies as registered and this amendment lapses.

Am.15 item 4. (DEC/DEC-031-forward-paper-arena.md:575)

> 5. **Live consumer (DEC-032).** No process reads an HV4 decision row before that bot's read is recorded. After a recorded PASS, only CON-F, CON-X, BIG-F or BIG-X may feed a C0 bridge, under QP-RULING-1025-hv4-read-rule R2.2 (a) to (g), declared in the canary copy before the first send. The L, P and R arms are not canaried under this amendment.

Am.15 item 5. (DEC/DEC-031-forward-paper-arena.md:576)

> 6. **Worst-in-slot.** After a recorded PASS and before any canary proposal, one host-side job exports the scored trade set, runs `tools/arena_worst_gaps.py` and `arena_score.worst_in_slot_cells`, and writes `<bot_id>.wis.json` once. Any missing gap means "worst-in-slot not measured" and no proposal; a worst-in-slot mean <= 0 means no proposal. Report-only; it never changes the verdict.

Am.15 item 6. (DEC/DEC-031-forward-paper-arena.md:577)

> OUTPUT (stdout): NOT_READY prints only the status and the last boundary tested (no running O, R, margin, dates or sha: QP-VERDICT-915 item 3). READ_INSTANT and
> NOT_DECIDABLE print the status, boundary, O, R, margin, the count of distinct O dates and input shas. No P&L, no price, ever. --out writes the record ONCE
> (O_CREAT | O_EXCL, mode 0444)

tools/hv4_read_instant.py docstring, OUTPUT and write-once (first half). (tools/hv4_read_instant.py:17)

> and only for READ_INSTANT or NOT_DECIDABLE: the per-date O / R counts and the dropped-hour count appear ONLY in that record written at
> B (or at END), never before. A NOT_READY result writes no file (write_once refuses it), so no running count is ever on disk. Exit 0 status printed, 2 refused.

tools/hv4_read_instant.py docstring, OUTPUT (second half). (tools/hv4_read_instant.py:19)

> The CLI accepts only the nine HV4 bot_ids and clock 1792886400000. This tool fixes the instant only; it does not score (see tools/hv4_am15_read.py).

tools/hv4_read_instant.py docstring. (tools/hv4_read_instant.py:21)

> # hv4-instant-daily.sh - host-side daily HV4 read-instant job (DEC-031 Am.15 item 1; QP-VERDICT-915c). POSIX sh only (MiScusi runs jobs under sh). PAPER ONLY.
> # One run per UTC day D from FIRST (2026-10-30) to LAST (2026-11-08) at HM (01:30Z, after the D3 01:10Z FINAL). Before each run it waits for the D-1 FINAL
> # L8 D3 hour table (hour-table-<D-1>-final.jsonl). D3 writes <file>.tmp, then mv's it to the final name, so "final name exists and no .tmp beside it" is its
> # completion marker. The job then runs tools.hv4_read_instant --mode screen for each of the nine bots whose

scripts/ops/hv4-instant-daily.sh header (lines 2-5). (scripts/ops/hv4-instant-daily.sh:2)

> # <RECORD_DIR>/<bot>.instant.json does not yet exist (the record is write-once; an existing record is not a failure).

scripts/ops/hv4-instant-daily.sh header (line 6). (scripts/ops/hv4-instant-daily.sh:6)

> # Output: only the tool's own stdout. NOT_READY = status line only. On a written record: bot, the status line (B or END) and the record sha256, no counts.

scripts/ops/hv4-instant-daily.sh, Output line. (scripts/ops/hv4-instant-daily.sh:22)

> 00:05 HV4 reviewers; 00:10/00:15/23:50 digests; 00:20 Q50 score; 01:10 HV4 D3 table; 01:30 HV4 instant #1591

HANDOFF section 5, fixed instants (state document; the job id is not named in the script itself). (docs/HANDOFF.md:75)

> **10-30T00Z onward:** the Q50 read. **HV4:** its read comes at an as-of of at least B + 24 h, so 10-31T00Z at the earliest (Am.15; QP-RULING-1029-hv4-read-d1d4).

HANDOFF section 7 at main 836b61ac (state document; PR #1081 replaced the earlier line). (docs/HANDOFF.md:104)

> WHY. Amendment 15 item 1 moves HV4's read instant to the attempt-margin boundary B that tools/hv4_read_instant.py computes and writes once. The pinned read
> path (tools/arena_am6_read.py, sha256 ef6e4555; tools/arena_score.py) computes the UNAMENDED boundary with arena_score.blind_read_instant and refuses any other
> (the wrapper, the parity guard and score_bot all check it), and score_bot's own forced-boundary option is a comparator screen that record_read refuses.
> Item 6 allows the pin change of either tool, disclosed at the read. This wrapper leaves both files byte-identical and changes ONE thing,

tools/hv4_am15_read.py docstring, WHY. (tools/hv4_am15_read.py:7)

> CHECKS before the scorer is called (each failure is exit 2, nothing that looks like a score is printed):
>   1. The instant file is this tool family's write-once record (schema hv4_read_instant_v1), mode "screen", status READ_INSTANT, for --bot, with the clock of
>      the bot's registry line. A NOT_DECIDABLE instant file (no B by END_H) prints NOT_DECIDABLE and never calls the scorer. A confirm-mode file is refused:
>      the confirmation read (DEC-031 section 2 step 4: also a one-sided bootstrap p <= 0.05 / c) is not implemented here.
>   2. B is RECOMPUTED by hv4_read_instant.compute from the same ledger, hour tables and restarts at --as-of-ms and must equal the file's B (so a file that
>      does not follow from the inputs is refused). --as-of-ms must be >= B.
>   3. tools/arena_am6_read.py has sha256 ef6e4555... and tools/arena_score.py has one of the two known sha256 values (a508ed79..., the frozen-sha copy;
>      c4859323..., the copy on main after #815, report-only additions): the replacement relies on how those exact files call blind_read_instant.
> Printed and recorded ("am15" block): B, the instant file sha256, this tool's and hv4_read_instant's sha256, the arena_score / arena_am6_read sha256 in use,
> and the UNAMENDED boundary (arena_score.blind_read_instant over the same non-dropped decision instants, the first call of the read: an attempt-boundary record,
> not the read instant; Amendment 15 item 4). Run at or after B, once the attempts before B have settled (the scorer's PENDING rule applies unchanged).

tools/hv4_am15_read.py docstring, CHECKS (spans to line 26). (tools/hv4_am15_read.py:16)


## 4. The read rule and its verdicts

**Governs:** The gate, the deciding cell, the caps, the verdict words, and every clause that bears on the attempts-versus-trades question. The open point is listed under OPEN QUESTIONS FOR QP and is NOT resolved here.

> A book promotes only if all of these hold, under **both** the flat 15% fail model and the pressure-fail model at slope scale 1:
>
> - at least 100 out-of-sample trades
> - at least 5 distinct UTC days, with a majority of those days positive
> - lower 90% CI bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile)
> - total SOL still positive after removing the top 3 trades

CLAUDE.md, Promotion gate. (CLAUDE.md:39)

> - **The test:** CLAUDE.md's promotion gate, unchanged:
>     - a majority of days positive;
>     - the CI90 lower bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile);
>     - total positive after removing the top 3 trades;
>     - all of this under both the flat 15% and the pressure-fail model.

DEC-031 section 2, The test. (DEC/DEC-031-forward-paper-arena.md:45)

> - DEC-014 Amendments 1 and 2 apply: an unresolved restart orphan, a failed settlement, or a counted trade without its pressure-leg P&L makes the read NOT_DECIDABLE.

DEC-031 section 2. (DEC/DEC-031-forward-paper-arena.md:50)

> - **Deciding cell:** the 1.9 s leg at the stake pinned in `bots.json` before the clock (§1.2). The 1.3 s leg and the other stakes are reported beside it and never decide.

DEC-031 section 2, Deciding cell. (DEC/DEC-031-forward-paper-arena.md:51)

> - Ex-best-day is reported. A book whose total is not positive ex-best-day is not proposed for a canary.

DEC-031 section 2. (DEC/DEC-031-forward-paper-arena.md:52)

> - **Multiplicity.** Every read states the number of arena books in `bots.json` with a clock before the read (stopped ones included), their cells, and how many were read. A first pass is a **screen**: the best of that many, in-sample for selection, not a promotion. It allows only steps 1 to 3 below, with the canary an execution measurement at its DEC's small limits.

DEC-031 section 2, Multiplicity. (DEC/DEC-031-forward-paper-arena.md:53)

> - **A pass is not a profit claim.** The path after a screen pass:
>   1. quant-proof checks the read on its exact files;
>   2. the owner's OK;
>   3. a live canary under its own DEC, with small limits;
>   4. **stake above the canary's limits** needs a **confirmation**. The same `bot_id`, unchanged, must clear the same gate on its deciding cell over attempts decided after the screen read's instant: at least 100 attempts on at least 5 further UTC dates, read once at the outcome-blind instant above. **And** its one-sided bootstrap p (10,000 draws, seed 1; the larger p of the two fail models) must be at most 0.05/c. Here c is the number of arena books that have entered a confirmation under this DEC by that read, failed and NOT_DECIDABLE ones included (DEC-014 (b); Bonferroni for staggered reads, as in DEC-030 §1). Live fills must also match paper. Parity is required in addition to the confirmation and never replaces it.

DEC-031 section 2, path after a screen pass (step 4 "100 attempts on at least 5 further UTC dates" is amended for these nine books by Am.15 item 3, DEC-031:574). (DEC/DEC-031-forward-paper-arena.md:54)

> 7. **Unchanged:** the promotion gate and every threshold (n >= 100; >= 5 UTC dates with a majority positive; CI90 lower bound > 0, 1,000 draws, seed 1, 5th percentile; ex-top-3 > 0; flat 15% and pressure-fail at slope scale 1); the deciding cell (1.9 s, 0.25, rent 0 deciding, rent-charged leg beside it); the 5% and 10% caps and their denominators; DEC-014's NOT_DECIDABLE rules and the trim void rule; one read per bot_id; N and c; the frozen sha, clock, H = 14 and END; DESIGN A1 V1/V2 at B + 24 h.

Am.15 item 7. (DEC/DEC-031-forward-paper-arena.md:578)

> 8. **Disclosure.** Written after the clock, with about 18.7 h of the counted window elapsed and unseen. The change moves B only later and only by decision-side counts. It removes a FAIL set by count alone and cannot create a pass. Bias note: O and R are functions of the book's own past outcomes through its caps and loss stops, as under the registered rule. Am.6 drops cluster in busy hours, and the sign of that effect is unknown. Winner's curse: any HV4 pass is the best of the nine siblings and of N arena books, in-sample for that selection. No book has cleared the promotion gate.

Am.15 item 8. (DEC/DEC-031-forward-paper-arena.md:579)

> **R1. Attempts vs trades. "Amendment needed: YES".** DEC-031 Amendment 15 (draft text in the ruling) changes the read instant for the nine HV4 books to the Am.12 margin form: B is the first 00:00Z boundary with clock_start_ms < B <= 1794096000000 such that, among attempts decided in [clock_start_ms, B) and not dropped by Am.6, with O = kind `open` and R = kind `refused` (any reason), **O - floor(0.05 x (O + R)) >= 100** (integers: O - (O + R) // 20 >= 100) and the O attempts cover **at least 5 distinct UTC dates**. The count uses decision timestamps and row kind only. Defined there: attempt, trade (guarded reverts and strands are trades; refused buys never), the 5% and 10% caps unchanged, the gate unchanged. A count-only miss (fewer than 5 trade dates) is recorded as the scorer prints it, "count-only, not a loss", final, with no waiting. No B by 2026-11-08T00:00:00Z: ENDS NOT_DECIDABLE, final.

c0-readiness section 2c R1 (a lab note restating the host QP ruling; the ruling and Am.15 govern). (ARTIFACTS/lab/c0-readiness-2026-10-25.md:64)

> - **If not merged by then, or any HV4 count is seen first, "the registered rule applies literally": B is the attempt boundary and n < 100 after exclusions records "FAIL (n < 100)", final and not a loss. No later switch is allowed.**

c0-readiness section 2c R1 (fallback). OUT OF FORCE, LAPSED: Am.15 merged as #915 (305d9c0a) at 2026-10-25T19:35:24Z, before the 2026-10-30T00:00:00Z bar; per the QP pass on #1080, QP OK (comment 6402108455) and reviewer APPROVE (comment 6402206509) are both at 819ee4a7, and DEC-031, hv4_read_instant.py and hv4_am15_read.py are unchanged between 819ee4a7 and 305d9c0a. The ruling's other trigger, "any HV4 count is seen first", is not checked here and no sealed view may be opened to check it. (ARTIFACTS/lab/c0-readiness-2026-10-25.md:66)

> - `tools/hv4_read.py book` "is NOT a gate-read path" (it runs `arena_score` directly, forbidden by the HV4 record §0). Usable only for V1/V2/table steps after the gate read is recorded, and only after its own reviewed fix.

c0-readiness section 2c R1. (ARTIFACTS/lab/c0-readiness-2026-10-25.md:67)

> - Confirmation: the same margin condition over attempts after B1 on at least 5 further UTC dates, p <= 0.05/c. Entries at or after 1794096000000 do not count, so an HV4 confirmation is possible only if B1 <= 2026-11-03T00:00Z; otherwise NOT_DECIDABLE and stake above C0 is unreachable for that bot_id.

c0-readiness section 2c R1, confirmation. (ARTIFACTS/lab/c0-readiness-2026-10-25.md:68)

> NOT A GATE-READ PATH (DEC-031 Amendment 15 item 4; QP-RULING-1025-hv4-read-rule R1): `book` runs arena_score directly at the UNAMENDED attempt boundary, which the
> HV4 record section 0 forbids for a covered bot. The nine HV4 books' gate read is tools/hv4_am15_read.py at the instant tools/hv4_read_instant.py wrote. `book`, `v12` and
> `learning` are refused outside MAL_ARENA_TEST=1 until their own reviewed fix (Am.15 instants for V1/V2; every compared arm's read recorded). `table` (recorded reads only) is unaffected.

tools/hv4_read.py docstring. (tools/hv4_read.py:12)

> learning  one, at T0 + 14 d + 13:00Z (D14), covering entries before T0 + 14 d. It is a SEPARATE pre-declared comparison and NOT a gate read: it prints no gate
>             verdict, and its result.v1 gate ticks are false by construction. T0 is the common clock_start_ms of the 13 arms in bots.json.

tools/hv4_read.py docstring (learning screen is not a gate read). (tools/hv4_read.py:33)

> Any HV4 read, digest or write-up (the DEC-031 read, the Am.15 read, the attempts-vs-trades read) states, next to its result: "The frozen playbooks describe `ret_*` and `impact_*` as percent, describe `dd_ath` as the drawdown from the peak quote with no unit, and name `real_quote` and `age_h`; the card carries fractions, `dd_ath` as a fraction from the price ATH, and `quote_sol`, `age_s`, `impact_rt_*` (PR #712 item 10; disclosure ARTIFACTS/lab/hv4-disclosure-712-2026-10-26.md).

Disclosure 712, every HV4 read. (ARTIFACTS/lab/hv4-disclosure-712-2026-10-26.md:25)

> - **Cells declared: 25** = 22 (the L, F and X arms, Note N2) + 3 control books. The control books are **CON-P, CON-R and BIG-R**: each is a registered `bot_id` with `deciding_stake` 0.25 and so one book with one gate read (§1.2, §2). Their deciding cell is declared here: flat 0.25, 1.9 s fill, under both the flat 15% and the pressure-fail model.

Registration section 6, cells (the 25 includes cells that DEC-031 section 2 reads one per bot_id; DESIGN section 8 item A1.8 below sets the gate multiplicity). (ARTIFACTS/lab/hv4-registration-1023.md:258)

> - **A control's pass is confirmed automatically.** A control's screen pass enters confirmation automatically and counts in c (Am.3 item 2: nobody chooses). It is reported as a placebo alarm beside the L arms.

Registration section 6. (ARTIFACTS/lab/hv4-registration-1023.md:259)

> - **Sibling taint (Am.3 item 8 (e)).** L, F and P of one persona are near-identical, and R mirrors L. The best HV4 arm at the read was selected among nine siblings and is in-sample for that choice. Winner's curse applies to any single-arm pass.

Registration section 6. (ARTIFACTS/lab/hv4-registration-1023.md:260)

> 8. DEC-031 section 2 governs the gate: one gate read per bot_id, at its outcome-blind instant. The day-5/7/10/14 looks above are superseded for gate purposes, so the gate multiplicity is 25 cells × 1 read = 25 checks, disclosed with every other arena book.

DESIGN section 8, Amendment A1 item 8. (ARTIFACTS/lab/hv4-registration-1023/DESIGN.md:265)

> - **Promotion:** the CLAUDE.md gate unchanged, plus ex-best-day > 0 and a quant-proof pass. Then the owner's OK, then a small canary.

DESIGN section 8. (ARTIFACTS/lab/hv4-registration-1023/DESIGN.md:237)

> - **Power.** CON reaches 100 trades by about day 5–8; BIG may not (NOT_DECIDABLE).

DESIGN section 8, Power (the sentence says trades; pre-registration expectation, not a measurement). (ARTIFACTS/lab/hv4-registration-1023/DESIGN.md:238)

> 7. V1 and V2 for each L and F book are decided once, at the book's outcome-blind gate instant B plus 24 h. They are recorded write-once in the arena store as `<bot_id>.v12.json`, and a second decision is refused.

DESIGN section 8, Amendment A1 item 7. (ARTIFACTS/lab/hv4-registration-1023/DESIGN.md:258)

> - **No changes mid-window** except safety stops.

DESIGN section 8. (ARTIFACTS/lab/hv4-registration-1023/DESIGN.md:249)


## 4b. Host text of QP-RULING-1025-hv4-read-rule (Am.15 incorporates it by sha at DEC-031:571 and by item at :576)

**Governs:** What the ruling says on attempts versus trades, the residual count miss, the canary consumer and the order of worst-in-slot. HOST TEXT THE CHECKER DOES NOT VERIFY: the blocks below are copied from /data/mal/hunt-1008/push-1016/hunt30/hv4-read/QP-RULING-1025-hv4-read-rule.md (full sha256 c2343c9c6ef96256419c4e7f4eec1b83584163bfd3a1aff97ca726cecdc50955, prefix c2343c9c, verified by the QP pass on #1080). Where a block and Am.15 differ, the DEC text controls. A records PR should copy the ruling into ARTIFACTS/lab/ so these become checkable.

**C1. Ruling R1, Definitions (items 1 to 3, 5, 6)** (host text, unverified by the checker)

```text
1. **Attempt** = one `ArenaEngine.buy` call. It leaves a ledger row of kind `open`, or of kind `refused`. Its decision instant is `decision_ms.entry`, the engine write instant.
   - Not attempts: an LLM SKIP, WATCH or abstain, a code-gate SKIP (conviction, bounds, caps, cooldown, loss stop), a stale reply (>10 s), a parse failure, or a refusal by the risk agent. None of these calls the engine (hv4_runner.py:1162-1170@9ed4c5f). They are decision-log rows only.
   - An attempt whose decision hour is excluded by Am.6 item 2.4 (or that falls in a restart gap) is DROPPED. It is not in the boundary, not in n and not in the 5% denominator. It counts only toward the 10% cap of Am.6 item 3.
2. **Trade** (a member of the gate's n) = a non-dropped attempt of kind `open`, decided in [clock_start_ms, B), with a priced `trade` row at stake 0.25, that is not excluded. Excluded means any of: a §1.4 feed gap, `missing_v`, `late_print_gap`, `engine_restart`, or Am.6 `am6_span_overlap_pricepath` / `am6_open_at_restart`.
   - **Guarded revert** = a trade. Its leg state is `guarded`, and it costs one send (-505,000 lamports) on every leg. It is in n, the mean, the CI, ex-top-3 and its date's daily sum. Dropping it would select on the price move between call and landing, which is an outcome.
   - **Strand / close-blocked** (Am.2 item 6) = a trade at its total loss. It is never dropped.
   - **Unfilled** (a landing failure) is not a row class. It is inside every trade's value through the flat 15% and pressure blend. Nothing is removed for it, and "unfilled" never shrinks n.
3. **Refused buy** (kind `refused`) = an attempt, never a trade. It is not in n, the mean, the CI, ex-top-3 or the days. Data reasons (`no_prints`, which includes `history_trimmed`; `no_feed_rows`; `clock_not_ready`) go in the numerator of the 5% cap. Every refused buy, of any reason, is in its denominator.
5. **n, mean, CI, ex-top-3, days** are `arena_score.cell_stats` on the trades of item 2, per sub-cell:
   - days = the distinct UTC dates of the trades' entry write instants;
   - positive days = dates whose summed trade value is > 0, with a strict majority needed (`days_pos * 2 > days`).
6. **Unclosed** attempts: an attempt before B younger than 24 h is PENDING (no read yet). Older than 24 h, it is an orphan, and the read is NOT_DECIDABLE (DEC-014 Am.1).
```

**C2. Ruling R1, why it cannot create a pass, and the residual count miss** (host text, unverified by the checker)

```text
**Why it cannot create a pass.** Suppose the read is not NOT_DECIDABLE under the 5% cap. Then excluded + data-refused <= (O + R) // 20, so n = O - excluded >= 100 in every sub-cell. B is never earlier than the registered boundary: the margin implies O >= 100, and O's dates are a subset of the attempts' dates. So the change only removes a FAIL set by count alone. The gate thresholds do not move.

**Residual count miss, and what happens if the boundary is met but trades < 100.**
- Under the margin rule, n < 100 at B is possible only above the 5% cap. That is NOT_DECIDABLE, final.
- The one other count miss is fewer than 5 trade dates, when every open on a date is excluded. That is recorded as the scorer prints it: "FAIL", with misses that are count-only. It is labelled "count-only, not a loss" and is final.
- **There is no waiting to END after a count miss.** Waiting would mean choosing the instant after the scorer's output was seen.
- If no B exists by 2026-11-08T00:00:00Z, the book ENDS NOT_DECIDABLE at clock + H, final, counts only. It was already counted in N.

**If the amendment is not merged before 2026-10-30T00:00:00Z** (the earliest boundary of either rule), or if any HV4 count is seen first, the registered rule applies literally. B is then the attempt boundary, and n < 100 after exclusions records "FAIL (n < 100)", final and not a loss. No later switch is allowed. Under neither rule is there an extension, an early read or a second read.
```

Index note, not host text: the last paragraph above is the lapsed fallback. See the caption on the c0-readiness quote in section 4.

**C3. Ruling R2.2 (a) to (g), the canary consumer conditions (Am.15 item 5 binds them); R2.1** (host text, unverified by the checker)

```text
1. **Before the arm's own gate read is recorded:** no bridge process, dry or live, reads any HV4 decision row. A pre-read bridge test uses only synthetic fixtures, or a StubModel shakedown root, with rc and counts only and no row content printed. A DEC-032 canary needs a recorded PASS anyway (§1 item 1).
2. **After a recorded PASS**, a bridge may read decision rows only if all of these hold, each pre-declared in the canary DEC copy before the first send:
   - **(a) Arm.** The PASS book is **CON-F, CON-X, BIG-F or BIG-X**. The L arms are excluded: their playbook, knobs and vetoes change nightly, and DEC-032 §1 item 4 is read literally ("Any code or prompt change is a new `bot_id`"). They need a DEC-032 amendment first. CON-P and the R arms are controls, not promotable (DESIGN §5), and are never canaried.
   - **(b) Frozen.** bot_id, frozen_sha 9ed4c5ff63935696b2a89150e13565e123047754 and the config pin (sha256 1e611a5a…) are compiled into the bridge config. The `start` row must repeat them (`frozen_sha_mismatch` latch). For an F arm, the API-returned model id (deferred to read time, HV4 record §1) must be `claude-haiku-5-5`, or the same single id for the whole window. A different or changing id makes F ineligible.
   - **(c) Own directory only.** The bridge reads `decisions/<that bot_id>/` and nothing else: no other arm's directory, no ledger, no `bots.json`. The bind mount is read-only on that one directory.
   - **(d) Only decisions after the read.** Rows whose entry decision is at or after the instant the read was recorded become orders. Pre-read rows are skipped, and only their chain is verified.
   - **(e) Siblings untouched.** Until every HV4 sibling read is recorded or ended, no HV4 bot is stopped, restarted or re-pinned because of canary output. Only the pinned 2026-11-07T12:05Z handover and Am.6 item 4 crash restarts are allowed. Every sibling read discloses the canary's start instant (pools are shared across the lane-G stream).
   - **(f) Confirmation exposure.** The canary's live and twin figures are outcomes of the PASS arm's confirmation-window decisions. DEC-031 Am.3 item 6 ("No confirmation first") accepts this. Only these are allowed: halts acting on the book's canary, never a change to the paper book, its sha, its instant or its rule. The confirmation read discloses the exposure.
   - **(g) Universe.** The bridge's V band and WSOL check match `hv4_cards.filters` (V in [17.5e9, 17.7e9], WSOL first). Correction to PR #907 §7 item 5: the HV4 record §2.3 shows the V band HOLDS at 9ed4c5f (hv4_cards.py:458-465). The 77bdf7e text quoted in DEC-031 Am.2 item 2 (ii) is pre-freeze. No subset declaration is needed for the band. A size rule (C0 stake = min(card size, 0.05)) is a declared mapping, not a cap.
```

**C4. Ruling R3, instant and order of worst-in-slot** (host text, unverified by the checker)

```text
1. The gate read is recorded first, through the R1 wrapper at B. The worst-in-slot step never runs before that record exists.
  2. Then, in one host-side MiScusi job, at or after the recorded read and before any canary proposal, and only for a bot whose read is PASS:
  3. A FAIL or NOT_DECIDABLE read gets no worst-in-slot run. It is not needed and would be an extra outcome look.
```

**C5. DEC-031 Am.3 item 6 (canary prerequisites; the mint fetch names HV4).**

> 6. **Canary prerequisites** (ruling item 5). This amendment allows no live send (§4). A canary exists only under its own DEC (§2 step 3). Before one is proposed from a screen pass, all of these hold:
>    - quant-proof's check of the read on its exact files (§2 step 1);
>    - the owner's OK (§2 step 2);
>    - a canary DEC with small limits (§2 step 3);
>    - total positive ex-best-day (§2: a book whose total is not positive ex-best-day is not proposed for a canary);
>    - the worst-in-slot mean and its CI90 lower bound, quoted in the proposal (R7).

Am.3 item 6 (first part). (DEC/DEC-031-forward-paper-arena.md:207)

> - **Mint fetch** (Amendment 2 item 6: Fetch, Retries and the close-blocked class): the structure-only mint-account fetch of the book's traded mints runs before ANY canary proposal. For a book Amendment 2 does not cover (for example HV4 if it ends up rent-charged as built),

Am.3 item 6, mint fetch (the line continues; HV4 is named). (DEC/DEC-031-forward-paper-arena.md:215)

**C6. DEC-031 Am.2 HV4 coverage.**

> - **HV4** is covered only if, before its registration, both of these hold:
>      - (i) its host, `tools/hv4_runner.py`, passes `rent=0` explicitly. At origin/main 77bdf7e, line 1595 builds `stack_config(feed_delay_slots=...)` with the default, so HV4 is rent-charged as built;
>      - (ii) its frozen code enforces item 6's pool filter. At 77bdf7e, `tools/hv4_cards.py` `filters` (lines 399-400) checks PumpSwap and a WSOL quote only and has no V band, so a rent-0 HV4 as built could trade non-canonical WSOL pools.
>    - Coverage is tied to HV4's registered `clock_start_ms`: that and its registration must both be after the merge. T0 is not fixed here. R1 named 2026-10-21T00:00Z, which is only the earliest. T0 is the first 00:00Z after a clean 24 h stub run at the final sha and the registration merge, and was expected to be 2026-10-22T00:00Z when this text was revised.
>    - Items 5 to 9 apply to HV4 like any covered book.

Am.2 item 2 (HV4 coverage; the registration record section 2.3 states (i) and (ii) were met). (DEC/DEC-031-forward-paper-arena.md:112)

**C7. Code that implements C2** (CODE POINTERS, not quotes; the checker does not verify them). `tools/arena_score.py` at the frozen sha 9ed4c5ff (`git show 9ed4c5ff:tools/arena_score.py`; note main's copy is c4859323 and line numbers may differ there): line 84 `MAX_EXCLUDED_SHARE = 0.05`; line 422 the cap numerator `(sum(ex.values()) + len(data_refused)) / n_attempts`; lines 438-449 orphans, the over-cap share and ledger incidents are checked BEFORE any FAIL and give `NOT_DECIDABLE`; lines 450-454 `no priced trades at the deciding stake` labelled `FAIL (n < 100)`; lines 470-471 the `fail_label` set when every miss starts with `n `. On main: `tools/hv4_am15_read.py` lines 140-144 `is_count_only` (a fail_label, or misses starting `n ` or `days `; `days positive` is explicitly not a count miss), tested at `tools/test_hv4_am15_read.py` lines 245-249; `tools/hv4_am15_read.py` lines 195-196 print `FAIL: count-only, not a loss. Final (Am.15 item 2) ...`. Lines were checked by the writer against `git show`, not by the checker.


## 5. E5 reviewers, E6 digests and L8 tables

**Governs:** What these jobs are, and what is written about reading their output. The E5/E6/L8 labels are the registration record's and the task brief's.

> 3. **Self-improving books.** For a book whose own reviewer rewrites a playbook (Haiku trader v4):
>    - the frozen part is the code, the reviewer's instructions and the model IDs;
>    - each daily playbook version is hashed and logged before the first decision that uses it;
>    - a version may use only outcomes whose exit is before its write instant;
>    - the book is the whole system, versions included.
>
>    Only the book's own reviewer, at the frozen code, instructions and model IDs, writes playbook versions. Any other edit makes a new `bot_id`.

DEC-031 section 1.3 (E5: the reviewer is part of the book). (DEC/DEC-031-forward-paper-arena.md:20)

> - **Reviewer code and instructions.** `tools/hv4_review.py` sha256 `8f0dfaada9e77a7e2d6d76c574f70ee10d0c7af7433c96f15da8c2de32cc7325` at `9ed4c5f` (unchanged since `d1dbcd6`); playbook tree `tools/hv4_playbooks` `b39d0ad103ee0c02956251f0bcc37a21e5386d52`; template tree `tools/hv4_templates` `e45184be38fa1b742d3aac30e672c7f557208825`. Job script `scripts/research/hv4-review-job.sh` (a scheduled MiScusi job at 00:05Z; nothing is edited by hand).

Registration section 2.4, E5 reviewer pins. (ARTIFACTS/lab/hv4-registration-1023.md:140)

> - **Reviewer job ids** (00:05Z, one per learning arm: CON-L, BIG-L, CON-P; BST-L is left with lane H, DESIGN line 145 lists BST-L and it is not registered): **DONE.** Daily loops via `t0-1025/LOOP-daily.sh` (sha256 prefix `228ed0e0`), git `9ed4c5ff`, 4 GB, 0.5 cpu, 00:05Z daily: segA 2026-10-26..2026-11-07: CON-L #1564, BIG-L #1565, CON-P #1566; segB 2026-11-08: #1567, #1568, #1569 (each after its segA). These replace #1551-#1553 and #1556-#1558, which were cancelled at about 23:41Z on 2026-10-24 BEFORE any run, to make memory room for the runner (notebook entry "HV4 E5 reviewer loops resubmitted", `n_cG9TRCWNolMIFQ`). **Memory juggling disclosure:** to fit the 44 GB runner, change-radar #1547 and the E5 loops were cancelled before any run and resubmitted later (radar as #1573).

Registration section 2.4, E5 job ids. (ARTIFACTS/lab/hv4-registration-1023.md:143)

> - **Outcome cutoff rule, with code.** The bundle holds the arm's own closed trades with `close_ms` strictly before the cutoff 00:00:00.000Z of the review day, where `close_ms` is the exit decision instant plus the slower exit fill leg (`EXIT_FILL_MS` 1,900 ms) (hv4_review.py:10-12). In code: `load_trade_records` drops `close >= cutoff_ms` (hv4_review.py:308); `before_cutoff` keeps only `close_ms < cutoff_ms` (hv4_review.py:390-392); a manifest whose `max_input_close_ms` is at or after its cutoff, or that is written before its cutoff, is refused (`ManifestRefused`, hv4_review.py:45-48).

Registration section 2.4, reviewer outcome cutoff. (ARTIFACTS/lab/hv4-registration-1023.md:145)

> **DONE (E6 loops via `LOOP-daily.sh`, sha256 prefix `228ed0e0`): CLOSING segA #1554 (00:15Z daily, 2026-10-26..2026-11-07) and segB #1559 (2026-11-08..2026-11-09, after #1554); PARTIAL segA #1555 (23:50Z daily, 2026-10-25..2026-11-06) and segB #1560 (after #1555). The L8 table jobs D3 #1518 and #1520 are in section 0 (L8).** Under Am.6 item 2.6 it is the same daily job as the L8 table: one notebook entry per UTC day with the `--daily-digest` `decision_log_sha256` of the nine `bot_id`s and the per-hour table's output sha256 side by side.

Registration section 3, Declaration 8 (E6 digests and the L8 table jobs). (ARTIFACTS/lab/hv4-registration-1023.md:157)

> **Daily per-hour table (L8, QP pack item 3):** the table of record is the FINAL one only, written by the D3 job at 01:10Z with `--now (D+1)T01:10:00Z` (all 24 hours tabled; the tool tables hour h only once h+1 is closed); there is no provisional 00:20Z table. A failed table is re-run by hand with the same command and the same nominal `--now` that day and disclosed; a missing table is never backfilled silently.

Registration section 3, Declaration 8 (L8 FINAL table). (ARTIFACTS/lab/hv4-registration-1023.md:158)

> 6. **Daily table and changes.** The per-hour table and its output sha256 are written to a notebook entry at least once per UTC day, beside §1.2's daily decision-log sha256. A tool change (with §1.4's pin rule and quant-proof's OK; thresholds never change on a counted window, item 2.3) acts only on hours that start after the change is notebooked, and never re-decides an hour already tabled. A confirmation window (§2 step 4) uses the same pins as the screen.

Am.6 item 2.6. (DEC/DEC-031-forward-paper-arena.md:403)

> 7. **Report.** Every read of a covered book prints the per-hour table (counts only), the excluded hours with the failing check and its label, the report-only figures, the attempts excluded by each route, and the attempts kept per UTC date.

Am.6 item 2.7. (DEC/DEC-031-forward-paper-arena.md:404)

> 1. **Tool and forbidden inputs.** A pinned, outcome-blind report tool, pinned as path@sha256 in the registration record before the clock. It reads only feed-status files, tape-hour file names and sizes, sample files, and the books' heartbeat and scan rows as liveness (present or absent), by timestamp. It is forbidden to read: ledgers, fills, prices, exits, strand or position state, open-position counts, P&L, decision-row contents, and any count of decisions or per-position calls used as a threshold. It prints no mint, wallet or signature.

Am.6 item 2.1 (what the L8 table tool may read). (DEC/DEC-031-forward-paper-arena.md:391)

> L8. **A window sampler and the daily table, host-side MiScusi:** a 300 s feed-status sampler for the book (an hour with a missing sample FAILS and is excluded, Am.6 2.2), started before T0 - 15 min and after-chained through END + 15 min across the 14-day job limit. A daily table job writes the per-hour table and its output sha256, and the manager notebooks it every UTC day beside the decision_log_sha256 (Am.6 2.6). Job ids go in the record before T0. If the sampler is missing, the 10% cap of Am.6 item 3 makes the book NOT_DECIDABLE.

QP ruling 1022, L8. (ARTIFACTS/lab/qp-ruling-1022-hv4-am6.md:36)

> | L8 | 300 s window sampler and the daily table, host-side MiScusi | DONE (submitted 2026-10-24 12:35Z) | what: sampler job id (started before T0 - 15 min, after-chained through END + 15 min across the 14-day limit; an hour with a missing sample FAILS and is excluded) and the daily table job id (writes the per-hour table and its output sha256; the manager notebooks it every UTC day beside `decision_log_sha256`, Am.6 2.6). From: the manager's submits. By: running by 2026-10-24T23:45Z; ids in this record before T0. If the sampler is missing, the 10% cap of Am.6 item 3 makes the book NOT_DECIDABLE. Jobs prepared: `/data/mal/hunt-1008/push-1016/hunt20/haiku-v4/t0-1025/D1-sampler-seg1.sh`, `D2-sampler-seg2.sh`, `D3-daily-table-seg1.sh`, `D3-daily-table-seg2.sh` (index `L8-JOBS.md`; the 00:20Z table is provisional, the FINAL 24-hour table is written at 01:10Z because the tool tables hour h only when h+1 is closed). Ids: D1 sampler seg1 #1517, D2 sampler seg2 #1519 (after D1), D3 table seg1 #1518, D3 table seg2 #1520 (after); filled D3 scripts sha256 prefixes `b0f0c432` (seg1) and `b3d24fd5` (seg2); notebook entry "HV4 L8 jobs submitted 12:35Z" (`n_vd7HUrymccvnew`). |

Registration L8 row (ids). (ARTIFACTS/lab/hv4-registration-1023.md:45)

> D3 writes <file>.tmp, then mv's it to the final name, so "final name exists and no .tmp beside it" is its
> # completion marker.

scripts/ops/hv4-instant-daily.sh header. (scripts/ops/hv4-instant-daily.sh:4)

> optional override (default $RECORD_DIR/health-shas.txt). Kept by the JOB, append-only, write-once per date: on first sight of each complete FINAL
> #               table it appends "YYYY-MM-DD <TOOL_SHA> <FILE_SHA>". TOOL_SHA is the sha256 in the table's own last line (D3's tool_final_sha256), verified by the
> #               pinned arena_am6_exclusions.load_health; it is what --health-sha takes.

scripts/ops/hv4-instant-daily.sh header, SHAS_FILE (the file of table shas). (scripts/ops/hv4-instant-daily.sh:11)


## 6. API spend limits

**Governs:** Caps in the pinned code and DESIGN, and the owner's lines on record. Spend is not measured here.

> - **Hard caps,** through `tools/haiku_caller.py`:
>   - one ledger per arm per UTC day;
>   - $0.50 per LLM arm, $1.00 for risk, $0.10 for the reviewer: $4.70/day;
>   - a month guard refuses calls above $160;
>   - the owner's $200 console limit is the backstop

DESIGN section 6. (ARTIFACTS/lab/hv4-registration-1023/DESIGN.md:204)

> # DESIGN section 6: hard caps in USD per ledger per UTC day.
> # DESIGN says "$0.50 per LLM arm, $1.00 for risk, $0.10 for the reviewer: $4.70/day". 7 x 0.50 + 1.00 + 0.10 is 4.60, and with one
> # reviewer ledger per learning arm (4) at 0.10 it is 4.90. This module reads it as 0.10 per reviewer ledger (a reviewer call that
> # times out keeps its worst case charged, so a 0.025 split would starve it): 4.90/day at cap, 147/month, still under MONTH_GUARD_USD.
> LLM_ARM_DAY_CAP_USD = 0.50
> RISK_DAY_CAP_USD = 1.00
> REVIEWER_DAY_CAP_USD = 0.10
> MONTH_GUARD_USD = 160.0

tools/hv4_arms.py (pinned code, lines 18-25). (tools/hv4_arms.py:18)

> The hard stop is haiku_caller's: every call is made through a HaikuCaller whose budget_usd is the EFFECTIVE budget from preflight,
> min(day cap, day spent + month headroom). So the day cap and the month guard are both enforced inside the caller's own
> reserve-then-settle check.

tools/hv4_budget.py docstring. (tools/hv4_budget.py:12)

> owner's console limit is the backstop for anything beyond that.

tools/hv4_budget.py docstring. (tools/hv4_budget.py:16)

> - **Drop order.** It starts when a persona group passes 60% of its day budget before 12:00Z, or 80% at any time:
>   1. no board, at most 2 cards per call;
>   2. risk refresh only on a material change;
>   3. re-check cadence ×3;
>   4. the whole group halts entries, with code-only exits.
>   - Steps always hit L, F and P together. The reviewer is never dropped.

DESIGN section 6. (ARTIFACTS/lab/hv4-registration-1023/DESIGN.md:209)

> - **Settings:** thinking disabled; no temperature (non-default values return a 400); max_tokens 400 (entry and risk), 120 (re-check), 4,000 (reviewer).

DESIGN section 6. (ARTIFACTS/lab/hv4-registration-1023/DESIGN.md:191)

> 4. DESIGN.md (with Amendment A1 and Notes N1 to N4), SHAKEDOWN-CHECKS-3.md, -3-NOTES-1, -4.md, -4-NOTES-1 and `n_bots` 9 are pinned in sections 1 and 2.1, with prefetch policy v2 with N4, CAP 2,000 chain calls in any rolling hour and 48,000 a day, and the observability rows on (CHECKS-4 Declarations before T0).

Registration section 3, Declaration 4 (chain-call caps). (ARTIFACTS/lab/hv4-registration-1023.md:153)

> 9. **Credits (CLAUDE.md "Credits").** Helius credits per UTC day from the `chain_hour` rows are recorded in the stub report (section 7) and, for the run, in the daily note: which unit (the research-0 HV4 chain source and prefetcher, `MAL_HV4_RPC=helius`), how many credits, and what it made measurable.

Registration section 3, Declaration 9. (ARTIFACTS/lab/hv4-registration-1023.md:159)

> **10-28 (n_8Yfl--RzLCx2Dw):** API spend past 11-11 is OK. $192 is left before 11-11, after which the cap is $200 per month; the owner wants the $192 used before the reset. HV6's extra Helius credits are OK. Goal: **"really good self-improving general trading bots"**. API spend goes to the Haiku TRADER only (its decisions and their backtests), never to research or agents.

HANDOFF section 3, owner approvals (state document). (docs/HANDOFF.md:12)

> Earlier lines remain: HV6 ≤3 USD/day (K1), HV7 ≤0.30 USD/day (q_6_kzYJva9XXuYQ), and the 10-23 standing C0 OK.

HANDOFF section 3 (HV6 and HV7 lines; no separate HV4 line is on record there). (docs/HANDOFF.md:13)

> If a job uses Helius autoscaling (about 20M extra credits), the PR or the daily note records: which unit, how many credits, and what got faster or more complete. "I turned it up" without a number is not a report.

CLAUDE.md, Credits. (CLAUDE.md:124)


## 7. The seals

**Governs:** What is closed for HV4, and the no-retune rule.

> **The binding list is /data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md (sha256 prefix 823cc062).** Paste it verbatim into every tape-touching brief. It includes the HV4 no-retune rule, Q50 #817 and the canary, the Look-2 scope, D1 SPENT scope, the arena-join and in-sample rules, ledger D2/D3 (docs/HOLDOUT_LEDGER.md), and walk-2. The bullets below are a summary plus this session's additions, not a replacement.

HANDOFF section 2 (state document). (docs/HANDOFF.md:28)

> - **HV4:** outputs, ledgers and decision rows; the status view and log tail of #1561/#1562.

HANDOFF section 2 (state document). (docs/HANDOFF.md:29)

The SEALS-BRIEF line for HV4 (a HOST file, not in the repo, not verified by the checker; copied from the one line the task brief relied on): `HV4: never open ledgers, fills, prices, exits, positions, P&L, decision-row contents, bots.json, attempt counts (except the pinned count tool at its pinned use), score/preview/am6_read output, HV4 job #1561/#1562 status view or log tail. No pricing or retune of any HV4 rule on counted hours (>= 2026-10-25T00Z).` The common agent brief (also a host file) adds: never call job status or job log on a book runner, shadow or score job or their successors; use job list for state only.

> - No retune on a book's own counted days. A retuned rule is a new book.

DEC-031 section 2. (DEC/DEC-031-forward-paper-arena.md:59)

> **Freeze rule (QP-RULING-1024-779-feed, condition 6).** Any change to a rule sentence, a threshold value or a pin after this head needs QP again.

Registration section 3a. (ARTIFACTS/lab/hv4-registration-1023.md:201)

> If any original is edited before the clock, its hash above is stale and the change is disclosed in every read (QP stop rule: any /data pin hash change).

Registration section 2.1. (ARTIFACTS/lab/hv4-registration-1023.md:101)

> HV4's playbooks, templates and card builder are frozen at 9ed4c5ff (see `ARTIFACTS/lab/hv4-registration-1023.md`) and HV4 is counting. Nothing here edits a playbook, template, card field, validator or runner. **Changing any of them now would change the registered book mid-count, so nobody should.**

Disclosure 712, HV4 is frozen. (ARTIFACTS/lab/hv4-disclosure-712-2026-10-26.md:21)

> 3. **Thresholds and labels.** Each check has a numeric threshold per UTC hour (unit and denominator stated), pinned in the registration record before the clock and never changed on a counted window.

Am.6 item 2.3. (DEC/DEC-031-forward-paper-arena.md:397)

> - Any live send.

DEC-031 section 4 (first line of the list; the list continues). (DEC/DEC-031-forward-paper-arena.md:88)

> - Any read of a sealed path.

DEC-031 section 4. (DEC/DEC-031-forward-paper-arena.md:89)

> - Any change to a running job's command.

DEC-031 section 4. (DEC/DEC-031-forward-paper-arena.md:90)

> - Counting a trade decided before its book's clock.

DEC-031 section 4. (DEC/DEC-031-forward-paper-arena.md:91)

> *Provenance.* Text copied from the quant-proof ruling `/data/mal/hunt-1008/push-1016/hunt30/hv4-read/QP-RULING-1025-hv4-read-rule.md` (full sha256 `c2343c9c6ef96256419c4e7f4eec1b83584163bfd3a1aff97ca726cecdc50955`; also PR #907 comment 6401622729), draft Amendment 15 block. Tools: `tools/hv4_read_instant.py`, `tools/hv4_am15_read.py` (this PR, branch claude/hv4-read-rule-am). Item 6's trade export and `.wis.json` writer are not in this PR (owed, report-only, after a recorded PASS). PR #905's draft Amendment 15 is parked and unmerged; if it is revived it renumbers. No HV4 ledger, count, decision row or output was opened to write this. No book has cleared the promotion gate.

Am.15 provenance (names the owed tools and says nothing of an HV4 output was opened). (DEC/DEC-031-forward-paper-arena.md:581)


## 8. The jobs, by id

**Governs:** Every id below is read from the cited repo text; none was checked against the live job list (job list is state only and was not called). Statuses are not given.

> seg1 MiScusi job #1561 (`j_AJJNrr8PmD1YLg`), seg2 #1562 (after seg1). One process per `frozen_sha` (Am.3 item 1)

Registration section 1, T0 job (runner). (ARTIFACTS/lab/hv4-registration-1023.md:82)

> Ids: D1 sampler seg1 #1517, D2 sampler seg2 #1519 (after D1), D3 table seg1 #1518, D3 table seg2 #1520 (after); filled D3 scripts sha256 prefixes `b0f0c432` (seg1) and `b3d24fd5` (seg2); notebook entry "HV4 L8 jobs submitted 12:35Z" (`n_vd7HUrymccvnew`).

Registration L8 row (L8 jobs). (ARTIFACTS/lab/hv4-registration-1023.md:45)

> CLOSING segA #1554 (00:15Z daily, 2026-10-26..2026-11-07) and segB #1559 (2026-11-08..2026-11-09, after #1554); PARTIAL segA #1555 (23:50Z daily, 2026-10-25..2026-11-06) and segB #1560 (after #1555). The L8 table jobs D3 #1518 and #1520 are in section 0 (L8).**

Registration section 3, Declaration 8 (E6 digest jobs). (ARTIFACTS/lab/hv4-registration-1023.md:157)

> Daily loops via `t0-1025/LOOP-daily.sh` (sha256 prefix `228ed0e0`), git `9ed4c5ff`, 4 GB, 0.5 cpu, 00:05Z daily: segA 2026-10-26..2026-11-07: CON-L #1564, BIG-L #1565, CON-P #1566; segB 2026-11-08: #1567, #1568, #1569 (each after its segA).

Registration section 2.4 (E5 reviewer jobs). (ARTIFACTS/lab/hv4-registration-1023.md:143)

> E3 registration: MiScusi job #1549 (`j_1tTEq79ASP0Zpg`), git `9ed4c5ff`, `register_rc=0` at 2026-10-24T21:17:38Z, notebook `n_UkN1hTGlEeQ8zA`; the verbatim `--register` output is notebook `n_gISv0_7I1449FQ` (section 2, Registration lines). E3b genesis: job #1550 (`j_or7lxHz4aerzqQ`) at 2026-10-24T21:18:35Z, `genesis_rc` 0, `verify_rc` 0.

Registration L9 row (E3 registration and E3b genesis). (ARTIFACTS/lab/hv4-registration-1023.md:46)

**Id table (an index, not a quote; every id is cited above).**

| Role (task-brief label) | Job ids | Where cited |
| --- | --- | --- |
| T0 runner, segment 1 then segment 2 (notifications only; never status or log) | #1561, #1562 | registration section 1, T0 job; HANDOFF section 1 |
| E3 registration, E3b genesis | #1549, #1550 | registration L9 row |
| L8 sampler seg1, seg2; L8 D3 table seg1, seg2 (FINAL table at 01:10Z) | #1517, #1519; #1518, #1520 | registration L8 row |
| E6 digests: CLOSING (00:15Z) segA, segB; PARTIAL (23:50Z) segA, segB | #1554, #1559; #1555, #1560 | registration Declaration 8 |
| E5 reviewers (00:05Z): segA CON-L, BIG-L, CON-P; segB, each after its segA | #1564, #1565, #1566; #1567, #1568, #1569 | registration section 2.4 |
| Daily read-instant job (01:30Z, Am.15 item 1) | #1591 | HANDOFF section 5 |
| Cancelled before any run (replaced by the E5 ids above) | #1551-#1553, #1556-#1558 | registration section 2.4 |

The seal list in the task brief for this file also names #1564 to #1569, #1517 to #1520, #1554, #1555, #1559, #1560 and #1591 as job ids on which no status or log call is to be made. This index calls none.

## Rulings: QP-RULING-1029-hv4-read-d1d4

**Governs:** The QP ruling on D1-D4 of the PR #1080 QP pass. HOST TEXT THE CHECKER DOES NOT VERIFY: copied from /data/mal/hunt-1008/ops/manager17/QP-RULING-1029-hv4-read-d1d4.md (sha256 prefix d5d7f115586bb5a0, written 2026-10-29T16:28:56Z). The DEC text controls on any conflict. Nothing here says an HV4 bot makes money, and nothing in the ruling can create a PASS.

**D1 to D4 verdict lines, verbatim** (host text, unverified by the checker)

```text
## D1. Is Am.15 in force? RULING: YES, in force since 2026-10-25T19:35:24Z.
### Merge condition (Am.15 item 4: "merged, with QP OK on the exact head and a reviewer verdict, before 2026-10-30T00:00:00Z"): MET
### Seen-first trigger: NOT TRIPPED
### HANDOFF:22 and :104: STALE
## D2. Does "n < 100 at B only above the cap" hold in the pinned code? RULING: HOLDS, conditional on one engine invariant that this ruling does not verify.
## D3. Days unit. RULING: TRADE ENTRY DATES. CONFIRMED.
## D4. Label of record. RULING: the binding record is the one recorded read (the scorer's record as written by record_read: status "FAIL" with its deciding.misses), read together with Am.15 item 2. The read file needs no label field, and none may be added.
```

**HANDOFF:22 and :104 as the ruling judged them (those lines were replaced by PR #1081 at main 836b61ac; the new text is quoted in sections 1 and 3), and the correct state text, verbatim** (host text, unverified by the checker)

```text
- That ruling is QP-RULING-1025-hv4-read-rule R1 (c2343c9c). Am.15 carries it and is in force. This file closes the residual D1-D4.
- The correct state text: the HV4 read is made under DEC-031 Am.15 by tools/hv4_am15_read.py --record, at the B that tools/hv4_read_instant.py writes once (#1591), at an as-of of at least B + 24 h (the wrapper refuses earlier: hv4_am15_read.py "--as-of-ms is before B + 24 h"). If B = 2026-10-30T00:00Z, the earliest read is 2026-10-31T00:00Z, not 10-30. Correcting HANDOFF is a manager item.
```

**May the HV4 read proceed, verbatim** (host text, unverified by the checker)

```text
## Can the HV4 read proceed under Am.15 once #1591 writes B?
YES, for each of the nine bot_ids, under these conditions:
- the instant file is the write-once hv4_read_instant_v1 record with status READ_INSTANT;
- the read is tools/hv4_am15_read.py --record with the pinned arena_score (a508ed79, loaded from git at 9ed4c5ff) and arena_am6_read (ef6e4555);
- the as-of is at or after B + 24 h;
- it is one read per bot_id, with no preview and no unrecorded run.

The 10-28 header-only contact does not block it. The D2 residual (i) can change only the outcome class, never to PASS. If no B exists by 2026-11-08T00:00:00Z, the book ENDS NOT_DECIDABLE, final.
```

**Residual risks the ruling names, verbatim** (host text, unverified by the checker)

```text
- Residual, stated plainly: the text does not say what happens if a pre-merge count contact is discovered after the read is recorded. The trigger is a past fact, but "No later switch is allowed" also applies. That case would need its own ruling. No remedy is invented here.

- This holds if, and only if, every non-dropped open before B is closed by a `trade` or `excluded` row at stake 0.25 (condition (i)) and none is pending (condition (ii)).
- (i) is an engine property: every close writes a row at every registered stake, including 0.25. The HV4 engine code was not read here. The ruling R1 assumes the same thing.
- If (i) fails for some open (closed without a 0.25 row): n(0.25) can drop below 100 without raising the cap numerator. The read would then record "FAIL" with fail_label "FAIL (n < 100)" (:450-454 or :470-471) where the ruling expects NOT_DECIDABLE. Only the outcome class changes:

- Duty on every quote: a notebook entry, the Console, LAB_STATE, a HANDOFF or a PR that reports such a read must carry "count-only, not a loss" verbatim with "FAIL". It must never present the read as a loss or as a pending result.
```

In short, as the ruling states it: Am.15 is in force; the old HANDOFF:22 and :104 were stale (since replaced by #1081); the read may proceed once #1591 writes B, at an as-of of at least B + 24 h, one recorded read per bot_id. The two residual risks above stay open: the pre-merge-count-after-read case, and the 0.25-stake row assumption. D5 (unclosed attempts at B) was not asked of that ruling; its D2 part is stated in the host text above.


## Notes (non-binding; the writer's observations)
- N1. DEC-031 Amendment 15's heading line says "status: DRAFT until merge, QP OK and reviewer verdict". `git log` on main shows the amendment and its tools merged in one commit, `305d9c0a` "DEC-031 Am.15: HV4 read instant + wrapper (QP-RULING-1025) (#915)", committer time 2026-10-25T12:35:24-07:00 (2026-10-25T19:35:24Z), which is before the item 4 bar of 2026-10-30T00:00:00Z. Per the QP pass on #1080: QP OK (comment 6402108455) and reviewer APPROVE (comment 6402206509) are both at the PR's final head 819ee4a7, and the three files are unchanged between 819ee4a7 and the merge. The amendment is in force; the DRAFT heading is stale and the "registered rule applies literally" fallback has lapsed (this closes the former open question on Am.15 merge conditions).
- N2. The registration record is named `hv4-registration-1023.md` and its title says DRAFT for T0 2026-10-25; its section 0 and its DONE cells carry the retargeted values (clock 1792886400000 = 2026-10-25T00:00:00Z, frozen sha 9ed4c5ff). Older sentences in the same file still say clock 2026-10-21 or 2026-10-23, "first 00:00Z after a clean 24 h stub" and "Rent-charged as built" (the latter from DEC-031 Am.2's own conditions, which the record shows were met). The record's own section 1 table replaces the stub sentence with Am.6 item 1.4. The DESIGN copy is older again: "T0 = 2026-10-21T00:00Z at the earliest", "13 bots", looks at T0+5, +7, +10, +14 and "25 cells x 4 looks = 100 checks"; its Amendment A1 item 8 supersedes the looks for gate purposes, and Note N2 limits the registered bots to nine. `tools/hv4_read.py` carries the same stale "25 cells / 13 arms" text and is not a gate-read path.
- N3. The nine bot_ids are CON-L, CON-F, CON-P, CON-X, CON-R, BIG-L, BIG-F, BIG-X, BIG-R. The registration record calls CON-P, CON-R and BIG-R control books with one deciding cell each; the others are L, F and X arms. BST arms are not registered. Each registered bot_id has one gate read.
- N4. The model id registered is `claude-haiku-5-5` on seven arms and `[]` on the two X arms. The API-returned id is recorded as DEFERRED to read time in the registration record.
- N5. The registration record's TBD cells (total N, p, Holm rank, cells priced and killed) are read-time fields by its own text. Nothing in this file fills them.
- N6. The pinned code names `END` as 1794096000000 (2026-11-08T00:00:00Z) in tools/hv4_read_instant.py, and Am.15 uses the same number; the runner process runs to END_ISO 2026-11-08T13:05:00Z so that attempts entered before that cut-off can close.
- N7. `tools/hv4_arms.py` sets MONTH_GUARD_USD = 160.0 and its comment reads the daily cap as 4.90 at cap (147 a month), while DESIGN section 6 states 4.70 a day and a $127 month. The code is a pinned file at the frozen sha; the DESIGN text is the earlier statement. The owner's line on record (HANDOFF section 3) is a $200 monthly cap after 11-11 with $192 left before it; it names the Haiku trader and does not name HV4 by id.
- N8. "E5", "E6" and "L8" are the registration record's labels (E5 reviewers, E6 digest loops, L8 sampler and table, E3 registration, E4 runner). The task brief's phrase "sha lines only" for what may be read from E5, E6 and L8 outputs is an instruction, not a quoted rule; no repo text found says it in those words. The repo texts nearest to it are Am.6 items 2.1, 2.6 and 2.7 (the table tool is outcome-blind and its table and output sha256 are notebooked daily), the §1.2 daily decision-log sha256, and the SEALS-BRIEF line in section 7.
- N9. Am.15 item 5 cites QP-RULING-1025-hv4-read-rule R2.2 (a) to (g) for a canary consumer; the host text is reproduced in section 4b (C3). After a recorded PASS only CON-F, CON-X, BIG-F or BIG-X may feed a C0 bridge under Am.15 item 5.
- N10. Superseded or replaced text, collected: DEC-031 section 2 "When" and Am.4 item 2.3 are replaced for the nine books by Am.15 item 1 (Am.15's own words). Am.2 item 2's "T0 is the first 00:00Z after a clean 24 h stub run" is replaced by Am.6 item 1.4 (Am.6 and the registration section 1 say so). Am.4 item 2.2's "H is pinned in the bots.json line" is replaced for HV4 by Am.7 item 1 (notebook copy plus the records PR commit). DESIGN section 8's four looks and its 25 cells x 4 looks are replaced for gate purposes by DESIGN Amendment A1 item 8. Am.2 item 2's conditions (i) and (ii) for HV4 are recorded as met in registration section 2.3; that is the record's statement, not re-checked here.
- N12. Former open questions, classified by the QP pass on #1080 and closed here: (3) Am.15 merge conditions: resolved, see N1. (4) "sha lines only": not a conflict; the binding text is the SEALS-BRIEF HV4 line (no score, preview or am6_read output, decision-row contents or attempt counts); E5 bundles carry the arm's own closed trades (registration:145) so they are outcome-bearing and sealed beyond their sha lines; the E6 digest binds through its decision_log_sha256; the L8 table tool is outcome-blind by Am.6 2.1, and the brief's "sha lines only" is stricter and stands. (5) host-only rulings: process item, addressed by section 4b. (6) spend wording: disclosure only; at runtime the caps are the pinned hv4_arms.py and hv4_budget.py; DESIGN's $4.70 a day is an arithmetic slip (7 x 0.50 + 1.00 + 0.10 = 4.60; hv4_arms.py:19-21 discloses it); HANDOFF:12 applies to the Haiku trader, which includes HV4. None affects the read.
- N11. The pinned read tools may have changed on main since the registration pins were written (the registration pins arena_score at a508ed79 and Am.15 item 4 allows c4859323 disclosed). This file does not re-check any sha256 against disk.

## OPEN QUESTIONS FOR QP
Question 1 was the one the old HANDOFF lines named as needing a QP ruling. QP-RULING-1029-hv4-read-d1d4 has since decided it, and #1081 replaced those HANDOFF lines.

1. **Attempts versus trades: ruled.** D1-D4 are decided by QP-RULING-1029-hv4-read-d1d4 (section "Rulings"). The only residuals are the two the ruling names, kept in that section: a pre-merge count contact discovered after the read is recorded (no remedy ruled), and the engine invariant that every non-dropped open is closed by a `trade` or `excluded` row at stake 0.25 (unverified).

2. **Confirmation tooling (real gap, low).** tools/hv4_am15_read.py refuses a confirm-mode instant file; no DEC text names who builds the confirmation scorer, including the p <= 0.05/c step. It matters only after a recorded PASS and only for stake above C0 (confirmation is reachable only if B1 <= 2026-11-03T00Z). No effect on the screen read.

