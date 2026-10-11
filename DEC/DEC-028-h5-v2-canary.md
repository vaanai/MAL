# DEC-028: H5-BOOSTCLOCK v2, paper first or an owner-override canary (draft, for the owner)

| Field | Value |
| --- | --- |
| **Status** | **Draft, 2026-10-10. The owner chose on 2026-10-11 (section 3.5, lines in sections 3.1 and 9): Option A now, plus a pre-authorized Option B that starts only after v1 is halted by a DEC-024 §5.1 BOOST-timing halt.** No live send happens under this file until section 3.5's conditions all hold. It takes effect on merge, with quant-proof's OK on its final head and the owner's dated line (section 9). Option B's override line and the owner's confirmation of section 3.1's two sentences are both recorded in section 3.1 (the second at 2026-10-11T05:26:36Z). Revised 2026-10-10 for quant-proof's CHANGES review of 62cc971 (items 10-13 applied, and Option B's seal consequence for EXP-026 made explicit in sections 3.1 and 7). |
| **Decider** | Vaan (owner) |
| **Date** | 2026-10-10 |
| **Builds on** | [EXP-026](../EXP/EXP-026-h5-boostclock-v2-prereg.md) (the v2 rule and its read), [DEC-024](DEC-024-h5-live-canary.md) (the v1 canary: §4 limits, §5 halts, §6 seals, §7 override, Amendments 1–6, halt record, and [Amendment 7](DEC-024-h5-live-canary.md#amendment-7-2026-10-11-the-owners-decisions-written-before-exp-024s-look-1-read-and-blind-to-its-outcome-no-canary-ledger-shadow-outcome-record-or-look-1-statistic-was-read-to-write-it-a-look-1-fail-keeps-t0-for-execution-measurement-the-resume-after-a-benign-program-upgrade-and-a-pointer-to-the-v2-pre-authorization), merged in PR #595), [DEC-027](DEC-027-h5-champion-challenger.md) (§0, §3(b), §7), [DEC-018](DEC-018-live-trial-readiness.md), [DEC-019](DEC-019-execution-probe.md), [DEC-020](DEC-020-size-step-proposal.md), [DEC-021](DEC-021-champion-challenger.md) §7, [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) §3, §3.1, Amendments 2 and 4, Amendment 8 (G-v2, draft), [DEC-029](DEC-029-exp026-alpha-slot.md) (draft, EXP-026's α slot), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) §9. |
| **Amends** | **Option A:** nothing until EXP-026 passes. **Option B only:** DEC-024 §4's "Rule" row, so that the `h5` executor profile on the DEC-024 probe wallet may trade H5-BOOSTCLOCK v2 instead of v1, for the v2 canary in section 3 and nothing else. |
| **Does not amend** | The promotion gate for any book. EXP-024 (rule, looks, α, seal, read tool) and EXP-026 (rule, window, bar, read). EXP-022 and its CAP-PICK seal. DEC-027's switching rule and its k cap. **v1's STOP under DEC-024 §5.1 and its resume rules (Amendments 4 and 5).** DEC-024 §4's 14-day duration. |

## 0. Why this exists (2026-10-10)

- **v1's canary is STOPPED.** DEC-024 §5.1 fired at 2026-10-10T07:11:07Z, and STOP was placed at 07:15:10Z (DEC-024 halt record). At STOP: 3 trades at 0.02 SOL, +0.005199 SOL realized, 3 sells landed, 0 late. That is not evidence (DEC-024 §2, §4).
- **BOOST ends earlier in October.** On 10-10 the plain-pool median BOOST last slice was 336.435 s over 159 pools, 00–07Z (partial day), and the share of plain pools whose BOOST ended before 329.5 s was 0.151 (DEC-024 Amendment 5). v1 exits at a fixed s0 + 330 s, so on those pools it sells after BOOST and into the cliff.
- **Research round 2 found C10** (`/data/mal/hunt-1008/iter-r2/h5-boostclock-exit/REPORT.md`, with quant-proof's verification): exit at the projected last BOOST slice minus 10 s, using only slices seen at least 1.35 s earlier, capped at 330 s.
  - Non-inferior to v1 unstressed (paired +0.2788 / +0.2620 pp, flat / pressure) [measured, exploration].
  - Robust to an earlier BOOST end: v1 loses about 2.9 pp at an 8 s shift and C10 does not [measured, exploration]. Quant-proof: that robustness is true by construction (it follows the slices), and 89.5% of the S8 gain is v1's own loss.
  - **Not a DEC-027 challenger:** it misses the +3.0 pp pressure floor at +2.9425 pp. Quant-proof recommended parking it as a challenger.
- **The owner (10-10)** asked to "try to make H5 work", keep moving fast, and keep the bar.
- **v1's 14-day cap is not extended.** It ends 14 days after the canary's first send, about **2026-10-23T19:30Z**.
- **Update 2026-10-11.** v1 resumed at **2026-10-11T00:15:06.586Z** under DEC-024 Amendment 4 item 4 (resume record there). The executor's `end_ms` is now **2026-10-23T19:29:53Z**, the 14-day cap (owner, 2026-10-11; PR #591, named sha `f873aac`). The owner chose Option A plus a pre-authorized Option B (section 3.5).

**EXP-026** pre-registers v2 (v1's trigger, entry and fills with C10's exit) and its one forward paper read against the promotion gate. This DEC is about what may trade **live** in the meantime.

## 1. What this DEC is not

- **Not a retune answering v1's halt.** DEC-024 §5: "A halt is never followed by a retune." v1's halts and resumes are governed only by DEC-024's own rules (Amendments 4 and 5); under them v1 resumed at 2026-10-11T00:15:06Z (section 0). v2 is a **new rule** (EXP-024 §11, line 333) with its own read (EXP-026).
- **Disclosed plainly:** v2 was developed after the halt and because of it. Option B would put it live in the regime that halted v1, before its read. That is why Option B needs a new override in the owner's own words, and why Option A is the lab's normal path.
- **Not gate evidence.** No v2 canary result is a book, a pass, or EXP-026 evidence. No report, note, Console entry or message may say a v2 canary result shows a profit or an edge.
- **Not two rules live at once.** The executor trades one exit rule at a time. If v1 is resumed under DEC-024 Amendment 4, Option B stops first (section 3.4).

## 2. Option A: paper first (the lab's normal path)

- **Paper only until EXP-026 passes the promotion gate.** The shadow runs v2 beside v1 from EXP-026's W0 (merge + 24 h, at the earliest). v2's paper outcomes are withheld until EXP-026's single read (EXP-026 §3.3).
- **Build the live side keyless in parallel** (section 5), so a PASS does not wait on engineering. Nothing sends.
- **After a PASS:** a gated live canary of v2 at T0 0.02 SOL on the H5 wallet, through the DEC-018/DEC-019/DEC-020 path:
  - md5 decision-equivalence of the live executor's v2 exits against the EXP-026 read tool;
  - EXP-026's no-live conditions all clear (EXP-026 §7);
  - DEC-024 §4's limits and section 4's halts below;
  - the owner's yes, in a dated line. Size above T0 follows DEC-020 and the owner's ladder.
- **After a FAIL:** v2 is retired (EXP-026 §7). No live v2.
- **Timeline** [est]. Merge about 10-11; W0 about 10-12; 100 trades by about 10-18 at 16 plain triggers a day, or about 10-25 at 8 a day, **only if the CAP-PICK pick oracle (#509) is wired in by 10-16T01** (otherwise trades stop accruing then and the read at about 10-26 fails on n). The read follows within 48 h. A live v2 under Option A would start about 10-20 to 10-28 at the earliest.
- **Odds** [EXP-026 §11, quant-proof resampling]: P(pass) about 0.005–0.03 (M2; ≤ 0.01 under M1, the default). At a true +4–6% the read has about 1–6% power, so a FAIL is expected whether or not v2 is positive, and a PASS would be weak evidence. **Under Option A, the likely result is no live v2 in October.**
- **Cost:** builder time and reviews only. $0 extra, 0 Helius credits.

## 3. Option B: an owner-override live canary of v2 at T0 0.02 SOL

### 3.1 The override

- **What it is.** A live measurement canary of H5-BOOSTCLOCK v2 at 0.02 SOL per entry, **unpromoted**, starting once the executor supports the C10 exit and every section 6 item is done. It measures how v2's exit executes on chain: the slice stream's latency, the decision-to-send time, and where the sell lands relative to the pool's last BOOST slice. It does not test whether v2 makes money, and its size could not tell.
- **It needs a new override.** DEC-024 §7's `OWNER_OVERRIDE_CONFIRMED` was given for "this one strategy", v1. DEC-027 §0 already reads it as not extending to a new rule. A v2 canary before an EXP-026 pass is an owner override of the CLAUDE.md promotion rule for **H5-BOOSTCLOCK v2's live canary only**. It requires the owner's explicit written confirmation, recorded in this file by a dated line before any v2 send. The promotion gate is unchanged for every other book, and EXP-026's read stays binding as written. The canary is labelled an unpromoted trial in every report, and no report of it says the book passed or is positive. The first line below holds the owner's confirmation. Nothing else fills it, and until a line is there, there is no v2 send.

  ```
  OWNER_OVERRIDE_V2_CONFIRMED: 2026-10-11T03:35:22Z (owner, in session, asked by the manager). Pre-authorized, conditional (section 3.5). Question: "H5 v2 (same buys, sells about 10 s before boost's projected end) can run on paper only (A), or live on H5's wallet at 0.02 SOL (B, an override of the promotion gate). Recommendation: paper now, and pre-authorize the switch to live v2 at 0.02 SOL automatically if v1 gets halted by the boost-timing rule. Which?" Answer: "Paper now + auto-live (Recommended)". MiScusi notebook n_soP9VudXSnG4Jw.
  ```

  The form to fill: `<date> (owner, in session, asked by <manager>). Question: "<verbatim>". Answer: "<verbatim>". MiScusi notebook <id>.`

  The question shown to the owner must include, verbatim: "H5-BOOSTCLOCK v2 has not passed the promotion gate, its own read is expected to fail, and it would trade real money in the BOOST regime that halted v1. CLAUDE.md says live trading does not start until a book clears the promotion gate and the owner approves; this answer overrides that for v2's T0 canary only." It must also include, verbatim: "Under Option B, the v2 canary's outcomes are watched in real time on most of the pools EXP-026 counts, so EXP-026's read is labelled partly observed, and it is reported compromised if that label, the traded share or the no-withdrawal rule is missing." A line whose question lacks either sentence does not count.

  The second line below holds the owner's yes to those two sentences, shown verbatim (quant-proof's ruling at `2f8c94a`: a relay of the Helm request does not by itself meet this section). Nothing else fills it, and without it there is no v2 send either.

  ```
  OWNER_OVERRIDE_V2_SENTENCES_CONFIRMED: 2026-10-11T05:26:36Z (owner, in session, asked by the manager). Question: "H5 v2's rules require you to confirm these two statements word for word before it can ever trade live under the switch you pre-authorized. (1) "H5-BOOSTCLOCK v2 has not passed the promotion gate, its own read is expected to fail, and it would trade real money in the BOOST regime that halted v1. CLAUDE.md says live trading does not start until a book clears the promotion gate and the owner approves; this answer overrides that for v2's T0 canary only." (2) "Under Option B, the v2 canary's outcomes are watched in real time on most of the pools EXP-026 counts, so EXP-026's read is labelled partly observed, and it is reported compromised if that label, the traded share or the no-withdrawal rule is missing." Do you confirm both, so v2 can go live at 0.02 SOL if v1 halts on boost timing?" Answer: "Yes, I confirm both". MiScusi notebook n_fZNH1URVIkXIJQ.
  ```

  **Disclosure and how it was closed (2026-10-11).** The first question (the `OWNER_OVERRIDE_V2_CONFIRMED` line) named B "an override of the promotion gate" and stated the stake and the trigger. It carried neither required sentence, so it did not say that v2's own read is expected to fail, that B trades in the BOOST regime that halted v1, or what B costs EXP-026's read. Quant-proof's check of `2f8c94a` ruled that the owner must be shown both sentences verbatim and answer yes before any v2 send. The manager then asked the second question, which contains both required sentences character for character, and the owner answered "Yes, I confirm both" (the `OWNER_OVERRIDE_V2_SENTENCES_CONFIRMED` line). **This section's requirement is met; no open question remains here.**
  - **The instant** 2026-10-11T05:26:36Z is the manager's `date -u` read just after the answer, as the manager directed. The session transcript stamps the answer itself 2026-10-11T05:26:18Z (the question was put at 04:21:28Z). The notebook entry's title says about 04:35Z; that was a guess, corrected in a later MiScusi note to about 05:25Z. A string check against the session transcript confirms the recorded question and answer character for character.
  - **The option text shown with the answer:** "Recorded word for word in DEC-028 §3.1. v2 may then go live at T0 (0.02 SOL, about 0.10 SOL total loss cap) only if v1 halts on boost timing and the reinstall checks pass. Nothing else changes."
  - The confirmation adds nothing to B's scope: B still starts only on section 3.5's trigger and after every item of section 3.5's "What B still needs".
- **What B costs EXP-026 (the seal consequence).** The v2 canary (2 open positions, 30 attempts a day) trades most of the 8–16 plain triggers a day that EXP-026 counts, and its fills, exits and wallet deltas are watched in real time (section 7). So under Option B, v2's own book is largely observed before EXP-026's read, and the store cannot keep it blind. **Without quant-proof's fix, that read would be compromised.** The fix (EXP-026 §3.3, §8, G-v2 items (a)(iii) and (g)) is all of: the observation is declared in G-v2 before any v2 send; the read reports the share of counted pools the canary traded; it labels its verdict "v2 outcomes partly observed before the read"; and EXP-026 is not withdrawn, re-scoped or delayed after W0 for any reason other than an EXP-026 §7 NOT_DECIDABLE condition. If any of these fails, EXP-026's read is reported compromised, and a compromised read cannot support a live request. Under Option A none of this applies: v2's outcomes stay withheld until the read.
- **What the override does not cover.** Any step above T0. A scale-up of v2 before an EXP-026 PASS would need a further, separate dated line, which this DEC does not propose. The owner's 10-09 ladder and DEC-024 Amendment 3's T1 terms were given for v1 and do not carry over.

### 3.2 Limits (code constants; config may only lower them)

| Limit | Value |
| --- | --- |
| Rule | H5-BOOSTCLOCK v2 as frozen in EXP-026 §2 (rule block sha256 as merged): v1's trigger on tape quote + V (the same quantity as v1), one trade per pool, plain pools only, the C10 exit, **v1's s0 + round(330 s / sps) timer still armed as the latest exit**, full-balance sell plus ATA close |
| Tier | **T0 only: 0.02 SOL** per entry |
| Concurrent | at most **2** open positions |
| Attempts | at most **30** buy attempts per UTC day, counting guard reverts |
| Daily stop | realized loss of **0.08 SOL** in a UTC day |
| Total stop | min(0.12 SOL, 35% of the wallet when T0 started), about **0.1045 SOL**. **The counters continue from v1's run** (DEC-027 §7 item 6: a new rule never starts with a fresh allowance) |
| Priority, guard, sell ladder | DEC-024 §4 unchanged: 55,000 lamports per send; buy `min_out` at the trigger print's post-trade price ÷ 1.15; sell `min_out` 0.85, then 0.65 after 2 reverts or once s0 + 345 s has passed; never `min_out` 0 |
| Duration | **ends at DEC-024's 14-day end** (14 days from the canary's first send, about 2026-10-23T19:30Z), and at the executor's `end_ms` if that is earlier (`end_ms` is 2026-10-23T19:29:53Z from PR #591). **Never extended past that end under this DEC.** Any v2 live trading after it needs a new DEC (and, before an EXP-026 PASS, a new override) |
| Files | `LIVE_OK`, `STOP`, `HALT`, `TIER` as DEC-024 §4. The exit mode is a compiled-in constant of the pinned install, or DEC-027 §7's `VARIANT` file if that selector is built first. Only Helm changes either |
| Wallet | the DEC-019 probe wallet `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk` on `mal-fast-0`, held by Helm. The manager never sees the key (DEC-019 §5) |
| Funding | what the wallet holds now, about 0.3039 SOL (HANDOFF, STATE 10-10 ~07:30Z). The +0.5 SOL T1 top-up of DEC-024 Amendment 3's addendum is **not** sent under Option B |

**Wallet floor, hard worst case.** About 0.3039 − 0.1045 (total stop) − 0.04 (two open positions go to zero) − about 0.005 (fees and rent on them) ≈ **0.154 SOL**.

**Trial-size effect.** At 0.02 SOL, two 55,000-lamport sends are 0.55% of the stake (0.11% at 0.1 SOL). The ATA rent is refunded on close, but a stuck position strands 7.6% of the stake.

**Expected money.** At 0.02 SOL, 8–16 plain triggers a day (fewer after the 2-open and 30-a-day limits) and an assumed net +2 to +3% per trade before the fixed-cost term [est, EXP-026 §11], about 0.002–0.01 SOL a day. **Option B is a measurement, not income.**

### 3.3 When it would start

- After the owner's override line, after EXP-026 is merged (so the rule is frozen by sha256 before any v2 send), after G-v2 (EXP-026 §3.4) is merged, and after every section 6 item.
- Never while v1 is live, and never while the A3 monitor shows one of the five H5 halt flags.
- **Earliest** [est]: about 10-14 to 10-16, if the shadow and executor builds and reviews go cleanly. That leaves about 7–9 days before the 10-23 end. The pre-10-16 reinstall that wires the CAP-PICK oracle (#509) and extends `end_ms` is the natural install to carry it. A separate install is possible but costs a second Helm visit.

### 3.4 v1 and v2 never both live

- The executor trades one exit rule. Option B replaces v1's exit mode on the `h5` profile; it does not add a second rule.
- If the owner resumes v1 under DEC-024 Amendment 4 or 5, Option B stops at the next 00:00Z with 0 open positions and Helm reinstalls or rewrites the mode to v1.
- A return to v1 is a fail-safe, not a switch, and DEC-027's one-switch limit does not count it.

### 3.5 The owner's choice (2026-10-11): Option A now, and a pre-authorized Option B

**What the owner chose.** Option A from now on: v2 runs on paper beside v1, and its live sell code is built and reviewed keyless (section 5). On top of that, the owner pre-authorized Option B, which starts on one trigger only. The owner's line is in section 3.1. The option text was: "v2 runs on paper beside v1 now. Its live sell code is built and reviewed in the meantime. If v1 halts on boost timing, v2 goes live at 0.02 SOL on the same wallet and loss caps (about 0.10 SOL total), until 10-23. That needs one Helm reinstall then."

**The trigger.** v1 is halted (`STOP` placed) under DEC-024 §5.1, as amended by DEC-024 Amendment 4 item 2 and Amendment 5 item 1, by one of its BOOST-timing clauses:
- the day-median lines: the executor's or the plain-pool median below 335 s, or the second completed day below 337 s;
- rule (b): BOOST over before our sell on more than 15% of at least 20 paired sells;
- rule (c): a last slice below 300 s on 3 pools in a day;
- the A3 backstop: an A3 run whose median last slice after migrate is below 330 s.

**What is not a trigger** [manager's reading, for quant-proof]:
- §5.1's "two consecutive unevaluated days" clause. It halts on missing data, not on BOOST timing.
- Any other DEC-024 §5 rule (late sells, slow landing, divergence, a stuck position, structure, including `program_changed`, `pins_changed` and every A3 halt flag), and any DEC-024 stop: `toggle_boost`, a seal breach, the 14-day end.
- A STOP that the owner, Helm or the manager places for any other reason.
- A §5.1 halt that fires together with any of the above. **"Fires together"** means any such halt or stop, or any section 4 halt or stop of this DEC, that fires or is active at any time from v1's §5.1 halt to the first v2 send ("What B still needs" below). The other halt's own rules govern, and B does not start.

**What B is, once triggered.** Section 3's v2 canary, unchanged:
- T0 0.02 SOL only, on the same wallet, with section 3.2's limits. The counters continue from v1's run, so the total stop is about 0.10 SOL over both rules (the option text's "about 0.10 SOL total").
- It ends at the 14-day end, **2026-10-23T19:29:53Z** (`end_ms` 1792783793000, PR #591, named sha `f873aac`). It is never extended under this DEC.
- T1 stays blocked. The +0.5 SOL top-up is not sent.

**What B still needs before its first send.** The trigger does not skip anything in sections 3.3 and 6:
- EXP-026 and G-v2 (EXP-024 Amendment 8) merged;
- v2's live sell code (section 5 items 1–6) built, with a `reviewer` pass, a security review and quant-proof on the md5 proof (section 5 item 7);
- v1 STOPPED with 0 open positions, and no A3 halt flag showing;
- **no other halt or stop in the interval** [quant-proof, stricter only]. From v1's §5.1 halt to the first v2 send, no other DEC-024 §5 rule, no DEC-024 stop (`toggle_boost`, a seal breach, a threat to EXP-022's 2026-10-16T01 preconditions, the 14-day end) and no section 4 halt or stop of this DEC has fired or is active. Any such halt in that interval counts as firing together with v1's halt: that halt's own rules govern, and B does not start. This mirrors DEC-024 Amendment 4 item 4's "no other section 5 rule or stop has fired";
- **one Helm pinned reinstall** (section 5 item 8) with the v2 exit mode, checked as in "The switch, in order" below;
- the owner's two section 3.1 lines, `OWNER_OVERRIDE_V2_CONFIRMED` and `OWNER_OVERRIDE_V2_SENTENCES_CONFIRMED` (both recorded 2026-10-11).
If v2's code is not reviewed and installed when v1 halts, B waits for it, up to the 14-day end. Nothing is rushed past a review because the trigger fired.

**The switch, in order** [quant-proof]. v1's `STOP` stays in place until step 3. Removing it earlier would resume v1 outside DEC-024 Amendments 4 and 5.
1. Helm does the pinned reinstall (section 5 item 8).
2. The install is verified to carry the v2 exit-mode constant: the pinned manifest (sha256 of each installed file) matches the reviewed v2 install sha, and that sha's compiled-in exit mode is v2. `LIVE_OK` is set only after this check. If the check fails, `STOP` stays and the install is redone.
3. Only then does the manager remove `STOP`, in a MiScusi job. That instant is **the switch instant**. The manager records it in a "v2 switch record" section added to this file, with the install sha, the manifest, the MiScusi job id and the confirmation that every item above held, and tells the owner (MiScusi notebook entry and the Console).

**If v1's own resume rules are met first** [quant-proof, explicit order]. If DEC-024 Amendments 4 and 5 allow v1's resume after Helm's v2 install but before step 3, section 3.4 governs: v1's resume takes precedence, B does not start, and the mode goes back to v1 by reinstall. `STOP` is never removed on the v2 install to resume v1.

**Never while v1 is live.** This matches section 3.3. B is a replacement after v1's halt, not a second rule (section 3.4). If v1 is later resumed under DEC-024's own resume rules, section 3.4 applies, and B stops at the next 00:00Z with 0 open positions.

**EXP-026's seal from the switch.** Until the switch, Option A's rules of EXP-026 §3.3 apply, and v2's decision records stay withheld. From the switch instant, Option B's rules apply: the shadow restarts with `--h5v2-live-exit DEC-028-B`, the canary's own outcomes are observed (G-v2 item (a)(iii)), and EXP-026's report gives the switch instant, the traded share and the label (section 3.1, "What B costs EXP-026"). The trigger depends on v1's BOOST-timing halt and not on any v2 outcome, so choosing B ahead of time moves no choice onto v2's book.

**EXP-024 Look 1.** Under [DEC-024 Amendment 7](DEC-024-h5-live-canary.md#amendment-7-2026-10-11-the-owners-decisions-written-before-exp-024s-look-1-read-and-blind-to-its-outcome-no-canary-ledger-shadow-outcome-record-or-look-1-statistic-was-read-to-write-it-a-look-1-fail-keeps-t0-for-execution-measurement-the-resume-after-a-benign-program-upgrade-and-a-pointer-to-the-v2-pre-authorization) item 1 (merged in PR #595), a Look 1 FAIL keeps H5 at T0 through 10-23 for execution measurement only. That covers whichever H5 rule the profile then trades, v1 or v2 under this section (the manager's reading, accepted by quant-proof). Neither is ever a book. **It waives only the EXP-024 Look 1 FAIL stop** of DEC-024 §5. For v2 the halts are this DEC's section 4 (DEC-024 §5.1 is report-only for v2), and an EXP-026 FAIL still ends v2 at the next 00:00Z after the read (section 4's stops).

## 4. Live halts under Option B (DEC-024 §5, adapted to v2)

Any of these halts new buys at once (`STOP`). Open positions still exit by v2's rule, or by the 330 s timer. A halt is never followed by a retune.

**Why §5.1 changes.** DEC-024 §5.1 exists because v1's fixed 330 s sell lands after BOOST, and into the cliff, when BOOST ends early. v2 follows the observed slices, so an earlier BOOST end is what it is built for. A median line at 335/337 s would halt v2 in exactly the regime it is meant for. **What replaces it** watches the two things v2 still depends on: that its sells land before BOOST ends, and that BOOST still looks like the 17.585 SOL TWAP the projection assumes.

1. **v2 exit quality (replaces §5.1).**
   - (a) **Sell after BOOST.** Over at least 20 landed sells paired with their pool's BOOST last slice, and then a rolling 20, more than **15%** landed at or after the last slice. (This is v1's rule (b), now the primary check. iter-r2 had 0.98% unstressed and 7.94% under the 8 s shift [measured, exploration].)
   - (b) **Outside the tested range.** A completed UTC day whose plain-pool BOOST last-slice median is below **330 s**, over at least 30 plain pools (DEC-024 Amendment 5's filter). iter-r2's dose-response reached a 12 s shift (September medians of about 343–347 s, so about 331–335 s); a lower median is outside what was tested.
   - (c) **Structure floor (kept):** last slice below 300 s on 3 pools in a UTC day.
   - (d) **Projection inputs.** A completed UTC day, over at least 30 plain pools whose BOOST completed, on which the median BOOST total is outside [17.5, 17.7] SOL, or the median slice interval is outside [6, 24] s (September's median was 12.06 s). E assumes a 17.585 SOL budget and a steady cadence.
   - **Report-only for v2:** DEC-024 §5.1's 335 s and 337 s day-median lines and Amendment 4's 330 s A3 backstop. They are reported every day.
2. **Late sells (adapted).** More than 5% of sells land more than 3 s after their v2 exit trigger, or after s0 + 335 s.
3. **Slow landing (unchanged).** Landing p50 above 3.0 s over the first 20 landed buys, then a rolling 20.
4. **Divergence (unchanged in form).** Needs at least 100 fills. At T0 limits Option B cannot reach 100 fills before its 10-23 end [inferred], so this rule is not built for Option B and no v2 twin outcome is opened for it (EXP-026 §3.3).
5. **Stuck position (unchanged).** Not closed by s0 + 600 s.
6. **Structure (unchanged).** Any of the five H5 A3 halt flags, above all `boost_budget_or_slices_changed`; `boost_enabled` false; a plain V-range WSOL pool with no BOOST vault; ms per slot outside [150, 450]; `program_changed`; a synthetic-audit disagreement (DEC-024 Amendment 2, Clarification 1).
7. **New: slice-stream health.** For the canary's own positions, the BOOST slice stream is stale (no slice seen for more than 45 s while the vault still has budget and the pool is open) on more than 2 positions in a UTC day, or the median slice receive lag (receive time minus block time) over the day's traded pools exceeds 1.35 s. Either means the live decision is not the rule's decision.

**Stops that end the v2 canary:** BOOST off or changed (`toggle_boost`); a seal breach; EXP-026 FAIL (new buys stop at the next 00:00Z after the read); the 14-day end; v1 resumed (section 3.4).

## 5. Engineering for the live executor (needed under B; built keyless under A)

**Today.** The shadow tracks each pool's BOOST buys from the PumpSwap feed and identifies the BOOST wallet through the vault (`boost_src` "pda" or "event_authority"). The executor receives only each closed pool's last-slice time (`on_boost_row`), after the pool closes, for its halt rules. Its exit is a slot plan bounded by wall-clock stages, anchored at s0 + 330 s (`HOLD_S`, `tools/h5_executor.py`).

1. **The live BOOST slice stream.**
   - The shadow writes a `boost_slice` record for every BOOST buy in a tracked pool: pool, mint, slot, tx_index, event_index, SOL, block time, receive time, and the pool-clock time t_i. It is structure and carries no price.
   - The executor keeps, per open position, the list of slices received. Slices received before the buy lands are kept too, since the projection uses all slices since s0.
2. **C10 at decision time.**
   - One implementation, `tools/h5_boostclock.py`, a port of `bc_rule.project_E` and `cad_exit`, used by the shadow and the executor alike. Unit tests pin it to `bc_rule.py` on fixtures and on the 0.01 s brute-force grid (EXP-026 P3).
   - **Who decides.** The shadow evaluates the rule on each new slice and on a timer, and writes an `exit_decision` record (rule id, pool, τ\*, exit trigger slot, k, P_k, the inputs) the moment τ\* is reached. The executor sells on that record. It also recomputes the decision from its own slice list as a cross-check, and logs any disagreement. If neither has fired by v1's slot C (or the wall-clock stage for C), v1's timer sells.
   - **Live clock.** The pool clock is slots × the measured slot rate, with the executor's existing wall-clock bounds. A slice is used once it has been **received** and t_i + 1.35 ≤ τ. This live decision can differ from the rule's when a slice arrives late; the difference is logged per decision and feeds halt 7.
3. **Exit mode and rule id.** `rule_id` is stamped on the trigger, intent, pending buy and position. An open position leaves by the rule it was opened under. The exit mode is a compiled-in constant pinned at install (or DEC-027 §7's `VARIANT` file, if built first), never a runtime config.
4. **Halts and counters** of section 4: the sell-after-slice tally exists (`bvs_n`, `bvs_before`); add the plain-pool day check at 330 s, the projection-input day check, the slice-stream health check, and the late-sell rule measured against the v2 trigger.
5. **Logging** per decision: `boost_done` (pool, slot, block time, receive time, seconds from s0, cumulative spend), the projection inputs, τ\*, the decision-to-send ms and send-to-land ms (DEC-027 §5.3 item 7).
6. **Proofs.** The EXP-026 P2 md5 on 2026-09-20 for v2's exit decisions, with v1's `75cb0b0c585bc2479137cae31330e73e` unchanged. A structure-only fixture of live slice sequences. A keyless executor dry run on the real feed with at least 5 complete simulated round trips on v2 exits and 0 simulate errors (DEC-024 Amendment 1).
7. **Reviews.** A `reviewer` pass on the shadow and executor changes, a **security review** (the executor holds a key), and quant-proof on the md5 proof and the final head.
8. **Install.** Helm's pinned, root-owned reinstall (manifest and sha256), ideally the pre-10-16 one that also wires the CAP-PICK oracle and extends `end_ms`. The watchdog gets the new halt alerts.

**Costs and limits.**
- **Money:** $0 extra. Public WebSocket and public RPC only, no Jito, no Sender, no LaserStream, no paid RPC (owner plan 10-08). **0 Helius credits.**
- **Effort** [est]: one shadow PR and one or two executor PRs, about 1–2 builder days, plus reviews, about half a day to a day. One Helm install.
- **Latency limit.** The rule assumes a slice is usable 1.35 s after its slot. The v1 canary landed in about 0.87 s from the trigger print (HANDOFF, STATE 10-10). Whether the public feed delivers BOOST slices that fast on 200 ms slots is **not measured**. EXP-026 §9 reports a 2.45 s lag leg; iter-r2 found it cost about 0.05 pp unstressed [measured, exploration].
- **Memory:** the shadow already keeps each pool's prints; slice lists add little. No new heavy job.

## 6. What must happen before the first v2 live send (Option B)

**Owner:**
- Choose Option B, and write `OWNER_OVERRIDE_V2_CONFIRMED` and `OWNER_OVERRIDE_V2_SENTENCES_CONFIRMED` in section 3.1 (both done 2026-10-11, as a pre-authorized Option B, section 3.5).
- Confirm the section 3.2 limits, and that the 14-day end is not extended (this DEC cannot extend it).
- Decide the open questions in section 9.

**Helm:**
- Confirm the wallet balance and that v1 is STOPPED with 0 open.
- The pinned reinstall of section 5 item 8; `LIVE_OK` only after the install is checked; the watchdog re-targeted to the new halts.

**Manager and builders:**
- EXP-026 merged, G-v2 merged, EXP-026 P2 and P3 recorded.
- Section 5 items 1–7, with the md5, reviews, security review and dry runs.
- A keyless v2 shadow running the merged build for at least 6 h on live triggers with 0 build errors (EXP-026 P5).

## 7. Seals (both options)

- **EXP-024 Look 1 and Look 2** are read as written whatever v2, its shadow or its canary shows (G-v2 item b). A v2 canary halt stops only the v2 canary.
- **Declared observation.** Under Option B the v2 canary's own fills, exits and wallet deltas for pools with s0 in Look 1's or Look 2's window are observed in real time, by the readers DEC-024 §6 names. This is declared in G-v2 item (a)(iii) before any v2 send. The wallet is public on chain, so anyone can compute its P&L. The v2 canary cannot trade a pool with s0 at or after 2026-11-06T00, because it ends about 10-23.
- **EXP-026's withheld paper book** stays withheld under both options (EXP-026 §3.3). The v2 canary trades most of the same pools, so under Option B v2's own book is largely observed before EXP-026's read. EXP-026 reports that share, labels its verdict accordingly, and cannot be withdrawn after W0 (EXP-026 §3.3). If any of that is missing, the read is reported compromised (section 3.1, "What B costs EXP-026").
- **CAP-PICK.** From 2026-10-16T01 to the end of EXP-022's read: no v2 trade on a pick, no v2 record joined to a pick, the oracle fails closed (EXP-022 §9, DEC-024 §6).
- **D1.** No synthetic class is joined to any v2 outcome before Look 2 is read. The v2 canary trades plain pools only, and its records carry the class only as the pre-buy filter.

## 8. Recommendation

- **Option A is the lab's path.** v2's evidence is exploration only. Its unstressed gain does not replicate on discovery, its stressed gain is true by construction, and EXP-026 is expected to fail: P(pass) about 0.005–0.03 (quant-proof resampling). At a true +4–6% the read has about 1–6% power, so a FAIL is expected whether or not v2 is positive.
- **Option B is a measurement buy, not an income step.** It is worth choosing only if the owner wants live data on the slice-timed exit before 10-23 (stream latency, decision-to-send time, sells landing before BOOST ends), and accepts that a v2 rule goes live in the regime that halted v1, before its read. Its worst case is the total stop, about 0.1045 SOL, plus at most 0.04 SOL open.
- **Either way**, the live-side build of section 5 is the same work, and building it keyless now costs no SOL.

## 9. Open for the owner

**Answered 2026-10-11 (owner, in session, 2026-10-11T03:35:22Z, MiScusi notebook n_soP9VudXSnG4Jw).** Items 1, 2 and 4 are answered below. Item 3 is answered by section 3.5: v1 keeps running under its own rules, and v2 replaces it only after a §5.1 BOOST-timing halt.

```
OWNER_DEC028_CHOICE: 2026-10-11T03:35:22Z (owner, in session, asked by the manager). Option A now, plus Option B pre-authorized on a DEC-024 §5.1 BOOST-timing halt of v1 (section 3.5). Answer: "Paper now + auto-live (Recommended)". MiScusi notebook n_soP9VudXSnG4Jw.
```

1. **Option A or Option B.**
2. **If B:** the override line in section 3.1, in your own words, and the section 3.2 limits. B stays at T0, and it ends at the 14-day end, about 10-23; this DEC does not extend it.
3. **v1** (question as drafted 2026-10-10). v1 stays STOPPED unless DEC-024 Amendments 4 and 5 allow a resume. Under B, v1 and v2 never run together (section 3.4). Do you prefer v1's resume, if it becomes allowed, or v2 under B?
4. **EXP-026's α route** (EXP-026 §10, [DEC-029](DEC-029-exp026-alpha-slot.md)). **M1 is the default** (quant-proof): a binding day-level t at α 0.025, in DEC-021 §8's fourth slot, opened by DEC-029 with your dated line; the October bound goes from 0.075 to 0.10. M2 (the gate only, a PASS labelled "gate-only, outside DEC-021 §8") only if you refuse that slot, by your dated line in DEC-029 stating that the October promotion-eligible error then exceeds DEC-021 §8's bound. EXP-026 does not merge without one of the two lines.

## Sources

- `/data/mal/hunt-1008/iter-r2/h5-boostclock-exit/REPORT.md` (with quant-proof's verification) and `FREEZE.md`.
- quant-proof's CHANGES review of #542 at 62cc971 (10-10).
- [EXP-026](../EXP/EXP-026-h5-boostclock-v2-prereg.md), [DEC-029](DEC-029-exp026-alpha-slot.md), [DEC-024](DEC-024-h5-live-canary.md) (§4–§8, Amendments 1–5, halt record), [DEC-027](DEC-027-h5-champion-challenger.md), [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md), [EXP-022](../EXP/EXP-022-cap-pick-part1-prereg.md) §9, DEC-018, DEC-019, DEC-020, DEC-021 §7.
- `tools/h5_shadow.py` and `tools/h5_executor.py` (`HOLD_S`, `on_boost_row`, `bvs_n`, `bvs_before`, the wall-clock exit stages), read as code only.
- `docs/HANDOFF.md`, STATE 10-10 ~07:30Z (wallet, canary at STOP, landing time).
- No sealed data, forward-1002, forward-1002ev, walk 2, forward-paper P&L, or canary or shadow outcome record was opened to write this file.
