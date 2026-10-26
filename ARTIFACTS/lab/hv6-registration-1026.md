# HV6 registration pack, DRAFT for T0 2026-10-30T00:00:00Z at frozen sha `<FINAL_SHA_AFTER_943>` (the merge commit of PR #943; written 2026-10-26, rebased 2026-10-26 onto main at #939, sha moved to the #943 merge by the owner's playbook/template fix on 2026-10-26; nothing is registered, no clock exists)

**Sha placeholder.** `<FINAL_SHA_AFTER_943>` stands for the 40-hex merge commit of PR #943 (HV6 playbook/template field-name fix). It is not known yet. After #943 merges, the manager fills every occurrence with one command: `sed -i 's/<FINAL_SHA_AFTER_943>/<40-hex merge sha>/g' ARTIFACTS/lab/hv6-registration-1026.md`. `5071e29a` (the #939 merge, this PR's base) stays in this file only as the label of the superseded item-6 figures and as history.

**No book has cleared the gate. This is a registration pack, not a result.** HV6 has 0 out-of-sample trades. Nothing here says HV6 makes money. Paper only.

Written by a worker in its own worktree from: `docs/research/hv6/DESIGN.md` (main at 5071e29a), QP-RULING-1025-hv5-design (original, ADDENDA 1-3), and the QP and reviewer comments on PRs #911 #914 #917 #918 #921 #923 #930 #936 (read with `gh`). Not opened: anything under `/data/mal/arena`, `bots.json`, any HV4/Q50 output, any job status or log, `/data/mal/meta-capture/`. Attestation by this worker: no HV4 output, Console or notebook P&L, attempt count, shakedown or smoke report was seen. The manager attests the same for the manager before registering (DESIGN section 2, G5 paragraph).

Items marked **UNMET** below block registration until fixed or ruled by QP (none is open since #939; QP's route ruling on #940 is recorded in section 12). Items marked **MANAGER** are steps this pack cannot do.

## 1. Constants and pins (QP #914 R4)

| Item | Value |
|---|---|
| Frozen (final) sha | `<FINAL_SHA_AFTER_943>` = the merge commit of #943 on main (#943 sits on #939's merge 5071e29a; the earlier label "(main, #936)" was wrong, QP #940 change 5). Every job below runs from the clean detached checkout `/data2/mal/wt/hv6-final` at this sha (QP #940 route condition (a)). This PR adds only scripts, tools for items 7a/7b/7c, tests and this file; none of them is imported by the runner. |
| Blob shas | `tools/hv4_cards.py` `747fd996fcb8a665a8a2dcf994dc92ff3ad011c7`; `tools/hv4_chain.py` `95556d4360695f47c8a76d92d34e13accc2cfb4f`; `tools/arena_paper.py` `5eef156bb617138a4c04fca6779f9ff4c5215b13`; `tools/hv4_prefetch.py` `1fb4d2f4c6cd3f9a59954f403b1f31beddc8ff57` (checked with `git hash-object` at 5071e29a; #943 changes none of these four files; the manager re-checks them at `<FINAL_SHA_AFTER_943>`) |
| Clock T0 | 2026-10-30T00:00:00Z = `1793318400000` ms (`date -u -d 2026-10-30T00:00:00Z +%s` = 1793318400). Rule: DEC-031 Am.6 item 1.4, the first 00:00Z after the smoke has passed and this record is notebooked. |
| Bot ids | `hv6.0-L`, `hv6.0-L-nr`, `hv6.0-F`, `hv6.0-P`, `hv6.0-X`; `deciding_stake` 0.25; model ids `["claude-haiku-5-5"]` for L, L-nr, F, P and `[]` for X |
| D | 12 days, fixed before the clock, never changed or extended. Window [T0, T0 + 12 d) = [2026-10-30T00Z, 2026-11-11T00Z) |
| H | pinned H = min(14, days from T0 to 2026-11-11T00Z) = 12 for a 10-30 clock (DESIGN section 2 and S5; QP Addendum 3). If L's real rate is under about 8.3/day (100 / 12) the gate read is NOT_DECIDABLE at T0 + H. |
| RPC provider | `MAL_HV4_RPC=helius` (pinned; QP #918 (c)). The holder block needs Helius. HV6 limit 1,000 calls/h and 24,000/day (HV4: 2,000/h and 48,000/day). |
| Feed delay | `--feed-delay-slots 7` (the HV4 Am.2 value). **MANAGER:** confirm 7 is the registered stack value; the smoke and the runner job must use the same number. |
| Spend limits | 0.50 USD per LLM arm (L, L-nr, F, P); REV-L, REV-L-nr, REV-P 0.05 each; RGM-L, RGM-P 0.025 each (total 2.20); global 3.00 USD/day; month guard 40.0 USD. Call-cap arithmetic 0.93 USD/day (DESIGN section 6). |
| params v6.0 (QP #921 C4) | min_matches 10, skip_count 60, max_changes 3, ttl_h_max 72, evidence_days 7; `params_sha256` `d5d41e9dd82015ace620b64fb607eaf805a1579fa0ae0adcd88a7c13ad2ccb02`; registry min {8,10,15}, skip {20,40,60}, max changes {1,2,3}, TTL cap {24,48,72} h, evidence days {3,5,7} |
| Spec shas (QP #921 C4; moved by #943, section 5 row 33; QP #943 verdict (b)(ii)) | **TODO pins, computed at `<FINAL_SHA_AFTER_943>` and filled by the manager; every one moves with #943, whose fixer is still changing the prompt text (QP #943 CHANGES, comment 6422223387), so no value at a pre-merge head is registered.** Each pin is computed in the clean checkout `/data2/mal/wt/hv6-final` at `<FINAL_SHA_AFTER_943>` with no API call: `sha256sum tools/hv6_playbooks/GEN_v0.txt` = TODO-PIN(GEN_v0 sha256); `hv6_agents.load_playbook('v0').header_sha256` = TODO-PIN(playbook_header_sha256); `sha256sum tools/hv6_templates/review_system.txt` = TODO-PIN(review_system sha256); `sha256sum tools/hv6_templates/regime_system.txt` = TODO-PIN(regime_system sha256); `review_user.txt` and `regime_user.txt` = TODO-PIN(each; expected unchanged, `efeb050a…` and `56fdb667…`); `hv6_review.reviewer_sha256()` = TODO-PIN(reviewer_sha256); `hv6_review.regime_template_sha256()` = TODO-PIN(regime_template_sha256); `hv6_release.bundle_spec_sha256()` = TODO-PIN(bundle_spec_sha256; expected unchanged `82848deb4a534ac501604f77c06deb7fdd9e2c85a2915627f91675955c07fd4c`, it hashes schema names and limits, no template); `hv6_release.regime_spec_sha256()` = TODO-PIN(regime_spec_sha256; it includes `regime_template_sha256`, so it moves); per arm, `hv6_release.procedure(params)[arm]` for L, L-nr and P (the dict of reviewer, params, bundle, playbook_seed and, for L and P, regime hashes that decides whether an arm changed) and the sha256 of its canonical JSON = TODO-PIN(procedure L), TODO-PIN(procedure L-nr), TODO-PIN(procedure P). For reference only (not registered): at #943 head b8d5640d (the head QP ruled CHANGES on) GEN_v0 `0dcd3041…`, playbook header `80e19b04…`, review_system `3d86302a…`, regime_system `b05c4944…`, reviewer `c5f0bfe0…`, regime template `91ea5d7d…`, regime spec `843b9bee172708a0008ee0e8fff9f3512018700a48b133011410fa54454282e8` (this worker computed it at b8d5640d), bundle spec `82848deb…`. Superseded values at 5071e29a: GEN_v0 `9ee29f48…`, playbook header `d2a98906…`, reviewer `de25aaa0…`, regime template `6580f923…`, regime spec `daefba88…`. |
| Manifest rules (C4) | nightly `effective_from` = max(cutoff + 30 min, written + 60 s); intraday `effective_from` = written + 30 min; append rules: wall < `effective_from`, `written <= wall + 60 s`; `max_tokens` review 4000, regime 1200; freeze 60,000 ms (00:01Z); intraday cap 2 per arm per day; ledger root `<HV6 root>/engine-ledger`; release whitelist: invalid_reply_rate, schema_rejects, bounds_rejects, token_cost_usd, latency_ms_p50, latency_ms_p95, block_coverage (veto_cap and unknown_evidence_id excluded) |
| Radar pins (DESIGN section 3) | out-dir `/data/mal/change-radar-arena1`; `tools/change_radar_seals.json` sha256 `5a2f8979c8493f7a3b200dd879f6f54c92a3bf383054af432c2fc35a8e10ca61`; `--venue-tape ''`. radar code `tools/change_radar.py` sha256 `0f5279b3b7276da10a73438dbd5d663139e8ef8e7671828e9ec1dbbd0a418331` and watch file `tools/change_radar_watch.json` sha256 `23838d1cf20d02a9c048855c99981a968a43e8e2014ec65874decc7ec55241cc` (both equal at b4a0f0d5 and at this PR's base; radar job **#1577** runs `python -m tools.change_radar --out-dir /data/mal/change-radar-arena1 --venue-tape ''` from a checkout at `b4a0f0d5cc71`, after `sha256sum -c` of the seals file `5a2f8979...ca61`; its successor is job **#1647**, section 9b P1). The manager verified these three hashes. The producer pins a STABLE worktree, not the job tree (section 9b, steps P1-P3). |
| HV6 root | `/data/mal/arena/haiku-v6/` (decision log `decisions/<arm>/<day>.jsonl`, `engine-ledger/`, `regime/`, `manifests/`, `chain-credits.json`, `chain-prefetch-spend.json`) |
| Registry route | (a), append-only into `/data/mal/arena/bots.json`, a1-a7, through `tools/hv6_register.py` |

## 2. Read instants (10-30 clock; DESIGN section 4 and 10, QP K2)

| Read | Instant | Basis |
|---|---|---|
| Window start T0 | 2026-10-30T00:00Z (1793318400000) | |
| V1/V2 | 2026-11-07T00Z (T0 + 8 d, 1794009600000) over [T0, T0 + 7 d) | calendar |
| Gate read (B) | the first 00:00Z boundary with >= 100 counted attempts on >= 5 UTC dates (about 2026-11-04T00Z at 20/day), run once after those attempts have exited (max hold 12 h) | mechanical; NOT_DECIDABLE if not reached by T0 + H = 2026-11-11T00Z |
| Entries halt, window end | 2026-11-11T00Z (1794355200000); code exits (stop/target/time, no LLM) run to completion | V2-1. Implemented by #939: the required `--entries-halt-ms 1794355200000` stops entries and LLM calls for all five arms at that tick; code exits run to completion (`tools/test_hv6_runner_endgate.py`). |
| Learning / regime | **2026-11-11T13:00:00Z** = 1794402000000 ms = T0 + D + 13 h (or the R-null job's end if later). `hv6_read` refuses to decide earlier (build item 10, before 11-07). | K2 |
| First nightly review | 2026-10-31T00:05Z (none on 10-30: no closed trade before T0) | follows HV4's pattern |
| Last nightly review | **2026-11-10T00:05Z**: 11 nights, 2026-10-31 to 2026-11-10, 33 reviewer jobs (3 arms x 11) | QP #940 change 2: a version written at 11-11T00:05Z would take effect after the entries halt (11-11T00:00Z), so no decision would ever use it; it would be spend outside the window and a version after D. No review runs on 11-11. |

Fallback-clock rule (DESIGN section 10, reviewer item 3), recorded before the clock: if the clock is after 10-30, D = 12 would end past 11-11. Option (a): quote an owner spend line covering T0 + 12 d verbatim; then D = 12, the window ends at T0 + 12 d, H = min(14, 12) = 12, and the read instant moves with it. Option (b): declare learning, outcome-information and regime contrasts NOT_DECIDABLE in advance, run only the gate read and V1/V2 (window [T0, T0 + 7 d)). D is never changed after the clock. **Chosen for the registration: ______ (MANAGER; for a 10-30 clock neither applies).**

## 3. K1: the owner's spend line (QP Addenda 2 and 3, K1; #914 R1)

**The owner's line, in full.** Notebook `n_dTJO1xRvneLeUA` ("OWNER 2026-10-25 ~20:5xZ: HV6 spend approved (K1); Kalshi may be removed entirely (meme coins supersede)"), owner, in chat, verbatim (full text from `miscusi_recall`, which returns the whole entry; the notebook search truncates it):

> "I'm good with the HV6 spend. You've got until nov 11 to use up the 200 dollars before it resets for the next month so if you're only going to be at around 120 (including the 4 dollars already spent) that gives you around 80ish to do more testing with. Just saw your 10-30 risk thing. You can do whatever is needed with the kalshi stuff. The meme coin supersedes it by a long shot and if you even have to just get rid of that stuff, get rid of it. I'm not even working on it right now."

The same entry records the manager's reading, verbatim: "K1 is met: HV6 is approved within the $200 Anthropic API budget to 11-11, at a hard cap of ≤ $3.00/day per design. About $4 has been spent to date, per the owner."

**The manager's request (the text the owner answered): NOT RECOVERABLE.** The id given for it, `n_rlKSXVDAwRj6TQ`, is not found by the notebook search (searched for the id and for "HV6 spend", "K1", "3.00 USD/day"), nor by `miscusi_recall`, nor by `grep -rl rlKSXVD` over the repo and `/data/mal/hunt-1008/ops` (the only hit is the hand-off note `/data/mal/hunt-1008/ops/manager16/HANDOFF-1026.md`, which names the id but does not quote the text). This pack does not reconstruct it. What stands instead: manager request text not recoverable; the owner's line quoted in full; the scope ≤ 3.00 USD/day, D = 12, to 11-11T00Z is the manager's reading, confirmed by the owner's "until nov 11". **QP to rule whether that suffices for K1** (QP #940 change 3 asked for the request text itself).

With change 2 the last LLM calls are the 2026-11-10T00:05Z reviews and the entry calls before the halt at 2026-11-11T00:00Z, all inside "until nov 11"; T0 + 12 d = 11-11T00Z exactly, with no day to spare. A lower cap would be applied in code before the final sha, and P0 and the smoke would re-run. No `register_bot` call before this line is quoted in the registration notebook entry.

## 4. K4 standing items (QP Addendum 2; each recorded before the clock)

| K4 item | Status | Where |
|---|---|---|
| #814 merged with a QP OK on its exact head before `hv6_agents.py` copied its wording | MET | #814 merged `a591e4d0` (2026-10-25T18:56:14Z), QP verdict at `a591e4d01c4863d33380a57b3bef047c980ecd28`: OK (QP #917 comment 6401912621 and its second comment) |
| LAB_STATE kill-review check line | **MANAGER** | Write: "no kill-review week in force on 2026-10-26; checked LAB_STATE at <sha>". (QP Addendum 1 takes this as given from the manager.) |
| Addendum 1 section 4 ledger confirmation and walk-2-hour disclosure | MET (confirmed by QP); **MANAGER** records the P0 days and the item-6 replay days against `docs/HOLDOUT_LEDGER.md` | notebook "HV6 item 6 final runs ... ledger check recorded"; the P0 day list goes in the fixed-first entry (section 7a) |
| P0 at the final sha under S1-S7 | **MANAGER runs** | section 7a |
| Am.6 smoke and the real-model reviewer smoke | **MANAGER runs** | sections 7b and 7c |
| Registration route (a) under a1-a7 | MET in code | section 6 |

Also recorded per QP #917 and DESIGN section 8: #814's evidence is HV4 pre-clock smoke notes (decision counts, no prices: "0 LLM-arm entries in 4 h", risk invalid 33/144) and "no entry rate was targeted".

## 5. G5 changed-values table (against HV4; every source is a design choice or arithmetic with no data at or after 2026-10-25T00Z)

| # | Value | HV4 | HV6 v0 | Source / effect | Where ruled |
|---|---|---|---|---|---|
| 1 | min_conviction | 4 | 3 (bounds 3-5) | arithmetic: more entries for the 100-attempt boundary; #814 over-skip | DESIGN s2; QP #917 |
| 2 | knob bounds stop / target / time / trail | -7..-10 / +6..+30 / 10-120 / n.a. | -4..-30% / +6..+150% / 10-360 min / 20-40% after +50% | merge of CON and BIG bounds | DESIGN s2 |
| 3 | hard stop, max hold | -15% / 120 min and -40% / 12 h | -40%, 12 h | BIG limits | DESIGN s2 |
| 4 | board / entries per h / open / day-loss stop | 10 / 4 / 4 / -1.0 | 24 / 3 / 6 / -1.5 SOL | cards supply; BIG limits | DESIGN s2 |
| 5 | X cap, X exit | 4/h CON-X | 2/h, -8 / +20 / 60 min, no trail (`X_EXIT`; trail inert) | prior design choice | DESIGN s2; QP #917 change 1 |
| 6 | Setup order under one GEN cooldown | per persona | PULLBACK > BREAKOUT > SECOND_WAVE; a pool with both a CON and a BIG tag drops the BIG one | new rule, HV4 order kept, no data | QP #911 reading 1 |
| 7 | Top-K per scan | 8 per persona (16) | 8 in total (halves the card supply per scan) | changed value | QP #911 reading 2 |
| 8 | Persona tag | CON/BIG | cards keep HV4's `setup.persona` and `hv4-card-1` bytes; GEN is only the limiter key; prompts do not contradict the label | | QP #911 reading 3 |
| 9 | X cap form | 4/h | at most 2 entries in the trailing 60 min, from the top of the mixed-type ranking; "uncarded" = not tagged in this scan | | QP #911 reading 4 |
| 10 | Skip-pricing plan | n.a. | `SKIP_PRICING_PLAN` -25 / +100 / 30 / 360 (BIG-style), skip pricing only; GEN_v0's example BUY (-25/+100/30/360, conviction 4) anchors toward the wide plan | reading, stated | QP #917 |
| 11 | Budget constants | HV4 caps | per-ledger 0.50 / 0.05 / 0.025 (totals 2.20), global 3.00, month guard 40.0 | arithmetic | QP #917 readings 1-2 |
| 12 | Re-check cadence | CON 5, BIG 15 min | 15 min | inside the 24/h re-check cap | QP #917 reading 3 |
| 13 | Probe steps | n.a. | conviction 1, stop 5 pts, target 10 pts, trail 5 pts, time 30 min; stake 0.25 | design choice, no data; conviction cannot loosen at v0 | QP #917 reading 4 |
| 14 | Regime tightenings | n.a. | only stop_pct, time_min, local_veto are always allowed; target, trail, conviction tightenings refused; extra entries/h are a total (<= 1 across one review and active probes) | conformed | QP #917 change 3 |
| 15 | Regime max_tokens | n.a. | 1,200 (equal to the assumed output; a truncation is an abstain) | | QP #917 |
| 16 | Guard root deny tokens; `day_report` | n.a. | root with a section 8.2 deny token refused; summary and cap flags only by default | | QP #917 change 4 |
| 17 | WATCH | HV4 path | implemented as HV4's (cap 10/arm, TTL from T, fresh card, one more call); additions: a second WATCH on a fired watch is a skip, a fired-watch call counts in that arm's 12/h cap, the fired card is per-arm (not byte-identical across arms, inherent), watches pause while entries are halted; watched pools are in the prefetcher watch set | | QP #918 (b) |
| 18 | Scan prescreen | `lite_snapshot`/`prescreen` | same speed path, now `fast_scan` | **decision-neutral**, by tools/test_hv6_scan_speed.py plus real-layout run 3 (fast_scan=False) md5 equality. Not a changed value. | QP #936 |
| 19 | Creator / sniper tape facts | `creator_sold_frac`, `same_slot_supply`, `sniper_share`, `sniper_still_holding` (None where unknown) | `holder_count` only; `sell_route` check (b) counts ALL sellers (looser than HV4 lane G); logged on sells only, gates nothing | changed veto input | QP #918 (8) |
| 20 | GEN cooldown | burned by a tag whose card is refused | NOT burned; recorded only for a tag that got a card. P0 is unaffected (every P0 tag counts as carded) | changed rule | QP #918 |
| 21 | Prefetch rate | 2,000 calls/h, 48,000/day (Note N4) | 1,000/h and 24,000/day, policy v2 passed explicitly | HV6 can serve staler facts and refuse more cards (`chain_stale`) than HV4. Frozen design value: reported, never tuned. | QP #918 (c) |
| 22 | Chunk decision instant | arm order | every member of a chunk validated and stamped at one common instant (max receive); one slow reply makes the whole chunk STALE for all four arms, symmetrically; pessimistic against a single-arm deployment; biases no contrast toward a pass | | QP #918 (a) |
| 23 | Version loading | n.a. | versions apply at the minute tick (up to 60 s after `effective_from`; drill `switch_lag_ms` 2000); L, L-nr, P only; F and X never read a manifest; a refused version halts entries for ALL five arms (written stop: manifest integrity) | | QP #923 (2), reviewer #923 note |
| 24 | Reviewer-moved plan knob, probe value | n.a. | replaces the model's value; a key the model omitted takes it | | QP #923 ruling (1) |
| 25 | Tightening rule | n.a. | the tighter of the model's value and the rule's (stop = max, time = min); new entries only; open positions keep their plan (rechecks may still tighten them) | | QP #923 change 1 |
| 26 | `WATCH_MAX_POOLS` | n.a. | 2,000. Kept set: tagged, board, top-25 by 60-min volume, every pool with an open/pending/retrying position in any arm, every pool with an active arm watch. Only cold candidates can be cut. **Real-layout replay at 5071e29a, job #1643 (days 2026-10-21..23): `scans_cap_bound` and `max_candidates_per_scan` to fill when it finishes (MANAGER). At 646f61a6 (job #1640, superseded baseline) `scans_cap_bound` was 0, i.e. the cap was inert on those days; the registration quotes only the #1643 figures.** If `scans_cap_bound` > 0 the cap is a disclosed changed value, not a neutral bound. Each read reports the count of SCAN rows with `watch_capped > 0` (sealed until the read). | | QP #930 binding 1 |
| 27 | Housekeeping | n.a. | 5-tape-minute quote sweep and engine `trim_history`: decision-neutral (`tools/test_hv6_runner_mem.py`: `test_bounds_change_no_decision_on_the_hv4_style_tape`, `test_streaming_scan_changes_no_decision`, `test_bounds_change_no_decision_on_a_scaled_synthetic_tape`, `test_quote_history_sweep_keeps_held_and_recent_pools`). ArenaState's 6 h eviction is kept and `engine.prune()` is not called, as HV4. | | QP #930 binding 2 |
| 28 | Replay PASS rule | n.a. | md5 equal (run1 == run2 and run3 == run1) AND peak RSS < 12,288 MB AND scan p95 (runs 1-2) < 1,000 ms AND (if scan p95 >= 500 ms: :02 tick p95 < 1,000 ms). Extension fixed 2026-10-26, before any outcome at the merged sha. | | QP #936 |
| 29 | Smoke limits (pre-registered here) | n.a. | smoke FAIL if STALE > 5% of LLM entry verdicts or chunk-latency p95 > 8 s (QP #918 6402258796); **proposed here, QP confirms on this head:** FAIL if as_of_refusal or chain-staleness/unavailability card refusals exceed 5% of cards (QP #914 says "a small fraction"); INCOMPLETE under 30 error-free LLM calls | | QP #914, #918 |
| 30 | STALE route in reads | n.a. | STALE > 10% of LLM entry verdicts in a read window makes that read NOT EVALUABLE (same as invalid replies). Each read reports STALE per arm and per chunk (sealed until the read). | | QP #918 (a) |
| 31 | Written stops (R8) | n.a. | budget, memory/OOM, seal breach, feed integrity, **`ledger_unreadable`** (QP #917), **manifest refused** (QP #923). No HV6 arm is stopped at anyone's discretion. | | QP #917, #923 |
| 32 | Regime-day rule | n.a. | QP Addendum 1 section 3 (E, t_e, regime days, placebo line, write-once file whose sha256 is notebooked before any HV6 outcome is opened) | | DESIGN s4 |

## 6. Registration procedure and the exact command (a1-a7; manager runs ONCE, after smoke PASS)

Order: (1) P0 count (section 7a) and read of its counts; (2) smoke (7b) PASS and its counts read by QP; (3) K1 line and this pack's blanks filled; (4) QP OK on this pack's exact head and a reviewer verdict on the PR (R6); (5) the registration notebook entry with the command below verbatim and its `date -u` instant (R5, a4); (6) run the command; (7) notebook copy of the five lines and the records PR within 24 h (R5); (8) `genesis` for L, L-nr, P (section 8); (9) submit the runner job (section 9). The regime producer (section 9b) is started before step 2, so the smoke sees blocks.

```
cd <clean checkout at 5071e29afe473b6c9ff1f753bf045345e722b0f6> && git status --porcelain   # must print nothing
python3 -m tools.hv6_register --sha 5071e29afe473b6c9ff1f753bf045345e722b0f6 --clock-start-ms 1793318400000 --live-registry --confirm REGISTER-HV6-FIVE-BOOKS
```

stdout is five lines, one per bot id, each only the dict `register_bot` returned for that id. On any exception only `<ExceptionClass> <hv6 bot id>` goes to stderr and the exit code is 1 (a2). A partial run is possible (ids are registered one at a time); a retry with the identical command is a no-op for ids already registered (a7); a refused changed line means a new bot id, never an edit. `register_bot` verifies the sha on origin (`verify_sha_on_origin`, untested live per reviewer #914): run it from a host with network access to origin, and expect it to refuse a clock earlier than the registration instant (T0 is days ahead, so it does not).

**a6 sentence (QP #914 R2, #918 7; the manager copies it into the record):** "`/data/mal/arena/bots.json` was read in-process by (1) the pinned unmodified `tools/arena_paper.register_bot` (blob 5eef156b) called by `tools/hv6_register.py`, and (2) the runner's `ArenaEngine`, which calls `load_registry` on every `buy` (pinned unmodified engine code). No line other than HV6's own left either process: the registration prints only HV6's returned dicts (tests: `tools/test_hv6_register.py::test_foreign_and_stop_lines_never_leave`, `test_malformed_line_prints_class_name_only`, `test_error_in_register_bot_prints_class_only`), and the runner's `order_refused` rows log the exception class and fixed flags only (`tools/test_hv6_runner_qp918.py::test_buy_refusal_logs_class_only_and_leaves_no_entry`); the runner never opens bots.json (`tools/test_hv6_runner.py::test_runner_source_imports_and_registry`) and never calls `start_all()`. This is not a seal contact; a later-found failure is, and is disclosed in every read it touches."

## 7. Items 7a and 7b: exact job commands (nothing run by this worker)

### 7a. P0 count (S1-S7)

Tool `tools/hv6_p0_count.py`, wrapper `scripts/ops/hv6-p0-count.sh`. It imports the rule code from a clean checkout at the final sha (`hv6_cards.scan`, the runner's prescreen helpers, `arena_state`); no engine, ledger, registry, RPC, model, price or fill. Counts per UTC day and hour (S3), pre-chain-veto upper bounds labelled as such, no mint/pool/price/per-pool row (S4); X would-be attempts at its 2/h cap; floor (>= 2.0/day per arm, no zero day) and the gap rule (pumpswap data_loss <= 1 h); prints `sha256` over the report and the fixed-first text sha256 in the header. P0 decides only registration and H; no scanner, board or cap value changes. If X is under 25/day the verdict line says so and the registration states it and revises X's planning figure; nothing is changed.

S1 day list (the manager fixes it in the notebook BEFORE the job and never extends or swaps it): whole closed UTC days in [2026-10-20T00Z, job start), at least 3, not 10-19, preferably the latest 4-5. Today is 10-26, so the latest closed days are 2026-10-22, -23, -24, -25 (in-clock days >= 10-25 are allowed for structure counts, S1; they overlap the item-6 replay days 10-21..23 as counts-only, see disclosure D5). Start the list at 10-21 or later: the 6 h warm-up is clipped at 2026-10-20T00Z (nothing earlier is ever read).

Fixed-first notebook entry (S7), before the job: the QP ruling's sha256, the fixed-first text, the day list, the command, the job id. Save the text to `FF=/data/mal/arena/haiku-v6/p0/fixed-first-<id>.txt` (it must name every day string; its sha256 is printed).

```
git worktree add --detach /data2/mal/wt/hv6-final 5071e29afe473b6c9ff1f753bf045345e722b0f6      # once; clean checkout at the final sha
# MiScusi job, memory >= 8 GB (MemoryMax), resumable false, on the host that holds /data/mal/arena, one heavy job at a time:
sh scripts/ops/hv6-p0-count.sh /data/mal/arena/haiku-v6/p0/fixed-first-<id>.txt 2026-10-22,2026-10-23,2026-10-24,2026-10-25 /data2/mal/wt/hv6-final
```

Output: stdout and `/data/mal/arena/haiku-v6/p0/p0-5071e29afe47-<stamp>.jsonl`; exit 0 all days OK, 1 a day FAILED (tape hours missing), 2 refused. Afterwards: output sha256 in the notebook, counts in this record (S7). Tool sha256 at this PR's head: see `sha256sum tools/hv6_p0_count.py` (pin it in the fixed-first entry).

### 7b. Real-model smoke (Am.6 style)

Tool `tools/hv6_smoke.py`, wrapper `scripts/ops/hv6-smoke.sh`. Runs the final-sha runner with the real caller (claude-haiku-5-5; key read by the caller from `ANTHROPIC_API_KEY`, which the wrapper takes from `/var/lib/mal/anthropic/anthropic.env` into the child's environment only, never printed), `MAL_HV4_RPC=helius` pinned, a global API cap of 0.50 USD (BudgetGuard cap, lowered only), the live arena tape read-only, for `HOURS` (default 2, at most 4). It writes ONLY under `/data/mal/arena/haiku-v6/smoke/<TAG>/` (fresh dir; `root/`, `engine-ledger/`, `smoke-registry.json` made by the unmodified `register_bot` via `hv6_register.register_all` with the next whole minute + 1 as clock, `report.json`); never the production root and never `/data/mal/arena/bots.json`. The engine ledger is never opened; decision rows are read through a whitelist projection. Report: take / WATCH / STALE (and PARSE_ERROR, NO_OUTPUT, BELOW_MIN) counts per arm, pooled STALE share, chunk-latency p50/p95/max, call latency, tokens and USD from the budget ledgers, chain calls/h (max and mean against 1,000) and Helius credits (the chain counter snapshot, numbers only, and `chain-credits.json`), card-refusal reason counts, watches added/fired/expired, runner counters. Verdict: FAIL if STALE > 5% or chunk p95 > 8 s (QP #918 6402258796) or refusals above 5% of cards (proposed, section 5 row 29) or a run error or spend above the cap; INCOMPLETE under 30 error-free LLM calls; else PASS. Exit 0 PASS, 1 FAIL/INCOMPLETE, 2 refused. A FAIL is fixed by code at a new sha (and P0 and item 6 re-run), never by tuning a limit.

Pinned notebook entry before the smoke (Am.6 L5 pattern): blocking list, sha256 of `tools/hv6_smoke.py`, instant, month-to-date API spend (owner console), root `…/smoke/<TAG>`, the registration instant. The smoke is never counted, is not in N, never an input to any read. The smoke links `<smoke>/root/regime` read-only to the producer's `/data/mal/arena/haiku-v6/regime/` (`--regime-dir`, set by the wrapper), so L and P see real blocks and the report prints `regime_blocks_visible` (a count; None = no producer). The producer writes only its own dir; the smoke never writes there. Disclosure to record: the smoke prices paper fills on live tape hours before T0 (including [2026-10-25T00Z, T0)); no HV6 rule is priced on them and its ledger is never opened.

```
# MiScusi job, memory >= 8 GB (MemoryMax; the runner peaked at 2.7 GB in the 646f61a6 replay #1640), resumable false, after the P0 job AND with the regime producer (section 9b) already running, one heavy job at a time:
FEED_DELAY_SLOTS=7 sh scripts/ops/hv6-smoke.sh /data2/mal/wt/hv6-final smoke-1026a 2
```

Not smoked by this script: the nightly reviewer and the intraday regime review (HV4's smoke ran the reviewer once per learning arm). They are covered by the drills (job #1642) and by section 8's `genesis`/`verify`. **MANAGER/QP:** say whether a one-night reviewer smoke on the smoke root is required (it would need `hv6_review genesis`/`run` on that root with a dated `--clock-start-ms` and a ~0.015 USD call per arm).

## 8. Nightly reviewer and genesis job specs

Reviewers exist for L, L-nr and P (`ag.reviewer_arms()`); F and X have none. Memory: at least 4 GB per job (HV4's smoke reviewer peaked at 2.0 GB of a 2 GB cap, QP Addendum 4). They are host-side MiScusi jobs scheduled by instant (not session crons), 00:05Z, three separate jobs per night started together (a nightly line written after 00:29Z must finish its model call within 60 s or it fails closed, QP #921 C3; at 00:05Z the margin is ample).

```
# once, before T0, after registration (manifest chain genesis; prints only the sha chain):
python3 -m tools.hv6_review genesis --arm L    --clock-start-ms 1793318400000 --commit 5071e29afe473b6c9ff1f753bf045345e722b0f6
python3 -m tools.hv6_review genesis --arm L-nr --clock-start-ms 1793318400000 --commit 5071e29afe473b6c9ff1f753bf045345e722b0f6
python3 -m tools.hv6_review genesis --arm P    --clock-start-ms 1793318400000 --commit 5071e29afe473b6c9ff1f753bf045345e722b0f6

# nightly at 00:05Z from 2026-10-31 to 2026-11-11 (cutoff = that day 00:00Z; --day defaults to today UTC); one job per arm:
sh -c 'cd /data2/mal/wt/hv6-final && set -a && . /var/lib/mal/anthropic/anthropic.env && set +a && exec python3 -m tools.hv6_review run --arm <L|L-nr|P> --commit 5071e29afe473b6c9ff1f753bf045345e722b0f6'
# verify the chain any time (no key):
python3 -m tools.hv6_review verify --arm <arm> --commit 5071e29afe473b6c9ff1f753bf045345e722b0f6
```

Run from the clean checkout at the final sha, cwd = that checkout, default `--root /data/mal/arena/haiku-v6`. `--stub` and `--now-ms` are refused outside a drill root. The CLI prints only the manifest sha256 chain and the decision-log sha256; those two are what is notebooked nightly with the Am.6 hour table (no daily P&L, G3). Regime blocks come from the producer job in section 9b (started before the smoke and before T0). Without blocks the runner and reviewer run with `radar_status = absent` and the regime test is NOT_DECIDABLE (L equals L-nr in information).

## 9. Runner job spec

```
# MiScusi job on the host that holds /data/mal/arena, memory MemoryMax >= 6 GB (the 646f61a6 replay peaked at 2.7 GB; #1643 at 5071e29a gives the figure to cite; 4 GB is the stated floor), resumable true (it resumes from its own hash-chained log),
# time limit < 14 d (one segment: T0 + 12 d window plus the 12 h exit tail is about 12.6 d), submitted after registration, before 2026-10-30T00Z (it waits for the clock):
sh -c 'cd /data2/mal/wt/hv6-final && set -a && . /var/lib/mal/anthropic/anthropic.env && set +a && MAL_HV4_RPC=helius exec python3 -m tools.hv6_runner --frozen-sha 5071e29afe473b6c9ff1f753bf045345e722b0f6 --clock-start-ms 1793318400000 --entries-halt-ms 1794355200000 --feed-delay-slots 7'
```

SIGTERM stops it cleanly. `MAL_HV4_CHAIN_STATE` must be unset (the runner refuses to start otherwise). Any restart is recorded in LAB_STATE. Helius credits are reported per job from `chain-credits.json` and the run summary's `chain_credits` (also the daily ops line: calls/h, served_none, stale-served and `chain_stale` counts). Never open the decision rows, ledgers or report files before each bot id's read (G3).

`--entries-halt-ms` is REQUIRED (no default; the runner refuses without it and refuses a value not after `--clock-start-ms`): 1794355200000 = 2026-11-11T00:00:00Z. At that tick entries and LLM calls stop for all five arms and code exits (stop/target/time, no LLM) run to completion. The runner writes a `start` row (frozen sha, pinned provider, `entries_halt_ms`) before its first decision, and counts `watch_added.<arm>`, `watch_fired.<arm>`, `watch_expired.<arm>` and `watch_no_card.<arm>` (all from #939).

### 9b. Regime-block producer job (`scripts/ops/hv6-regime-block-job.sh`, from #939)

A MiScusi job (`resumable: true`), **started before T0** (and before the smoke, so the smoke sees blocks), on the host that holds `/data/mal/arena` and `/data/mal/change-radar-arena1`, run from the clean checkout at the final sha. It polls every 60 s: a nightly block with `as_of` = 00:02:00Z of each UTC day (before the 00:05Z reviews) and an intraday block whenever `radar.jsonl` has a new record, writing `/data/mal/arena/haiku-v6/regime/block-<as_of_ms>.json` and `radar-<as_of_ms>.jsonl` (the exact bytes read). It reads only the radar out-dir and the pinned radar files; it never reads `drill/`, HV4/Q50 output or any tape.

Rules for the job:
- never set `HV6_BLOCK_TEST`, `HV6_BLOCK_NOW`, `HV6_BLOCK_NOW_FILE` or `HV6_BLOCK_ONCE` (they are test-only; `HV6_BLOCK_TEST` lifts the directory pins);
- **no restart between 00:00Z and 00:10Z** (the nightly block is write-once at 00:02:00Z; a restart in that window risks a late or missing nightly block that the 00:05Z reviews would use). A restart at another time is safe (the loop is idempotent) and is recorded in LAB_STATE;
- the registered pins are written once to `/data/mal/arena/haiku-v6/regime/pins.json` (read-only). A later change of a pinned file or env is never a refusal: the builder then writes `absent` blocks with `pin_mismatch` from that instant, which the read discloses (DESIGN section 3).

**Required pre-clock steps (manager; added after the manager's check of the radar pins):**

**P1. Radar successor job (GAP: #1577 is "daily 07:40Z x7", ending about 2026-11-01, but HV6 runs to 2026-11-11).** Without radar records after 11-01 the producer writes stale/`absent` blocks for the last 10 days. Submit a successor MiScusi job at the SAME commit `b4a0f0d5cc71`, with the SAME command and out-dir (`/data/mal/change-radar-arena1`), covering through at least 2026-11-12, chained `after` #1577 (or started by a sleep gate that waits for #1577 to end). It runs from the stable worktree of P2 so it does not depend on the job tree. 12 runs from the first 07:40Z after #1577's last run cover 11-02 .. 11-13; if #1577 ends earlier or later, count so the last run is on or after 2026-11-12 (the daily loop skips to the next 07:40Z, so there is no double run):

```
# MiScusi job, code vaanai/MAL @ b4a0f0d5cc71, 2 GB, 1 CPU, limit >= 300h, `after` #1577:
cd /data2/mal/wt/radar-b4a0f0d5 && echo '5a2f8979c8493f7a3b200dd879f6f54c92a3bf383054af432c2fc35a8e10ca61  tools/change_radar_seals.json' | sha256sum -c - || exit 1; i=0; while [ $i -lt 12 ]; do now=$(date -u +%s); t=$(date -u -d "$(date -u +%F) 07:40" +%s); [ $now -ge $t ] && t=$((t+86400)); sleep $((t-now)); out=$(/data/mal/venv/bin/python -m tools.change_radar --out-dir /data/mal/change-radar-arena1 --venue-tape '' 2>&1); rc=$?; echo "$out" | tail -20; echo "radar_rc=$rc"; if echo "$out" | grep -q '^CHANGES' && ! echo "$out" | grep -q '^CHANGES none'; then echo "RADAR ALERT: changes found"; exit 3; fi; i=$((i+1)); done
```

This is #1577's command verbatim except the `cd` into the stable worktree and 12 iterations instead of 7. #1577 exits 3 on a CHANGES alert, which would end it early and leave a gap; the successor behaves the same, so the manager checks that a radar run exists for each date and resubmits after an alert (a missing run makes the block `stale` after 36 h, which the read discloses). The radar record carries no code sha, so the pins in P3 (and the unchanged shas of the files both jobs run) are the evidence that the successor ran the same code.

**P2. Stable checkout instead of the job tree.** The job tree `/home/claude/.miscusi/jobtrees/vaanai__MAL@b4a0f0d5cc71/` may vanish when #1577 ends. Create a worktree from the commit once (same file shas as the job tree, verified by the manager: change_radar.py `0f5279b3...`, seals `5a2f8979...`, watch `23838d1c...`):

```
git -C /home/claude/.miscusi/repos/vaanai/MAL worktree add --detach /data2/mal/wt/radar-b4a0f0d5 b4a0f0d5cc71
sha256sum /data2/mal/wt/radar-b4a0f0d5/tools/change_radar.py /data2/mal/wt/radar-b4a0f0d5/tools/change_radar_seals.json /data2/mal/wt/radar-b4a0f0d5/tools/change_radar_watch.json   # must print 0f5279b3b727..., 5a2f8979c849..., 23838d1cf20d...
```

Never check out another commit in it and never edit it (a change makes the producer's builder emit `absent` blocks with `pin_mismatch`).

**P3. Producer job (started before T0 and before the smoke), with the stable paths:**

```
# MiScusi job, resumable true, memory 1 GB:
sh -c 'cd /data2/mal/wt/hv6-final && HV6_RADAR_CODE=/data2/mal/wt/radar-b4a0f0d5/tools/change_radar.py HV6_RADAR_CODE_SHA256=0f5279b3b7276da10a73438dbd5d663139e8ef8e7671828e9ec1dbbd0a418331 HV6_RADAR_SEALS=/data2/mal/wt/radar-b4a0f0d5/tools/change_radar_seals.json HV6_RADAR_WATCH=/data2/mal/wt/radar-b4a0f0d5/tools/change_radar_watch.json HV6_RADAR_WATCH_SHA256=23838d1cf20d02a9c048855c99981a968a43e8e2014ec65874decc7ec55241cc exec sh scripts/ops/hv6-regime-block-job.sh'
# the seals sha 5a2f8979c8493f7a3b200dd879f6f54c92a3bf383054af432c2fc35a8e10ca61 is pinned inside the script itself.
```

The pins are the files the radar jobs (#1577 and its successor) run, by sha256; the paths in `pins.json` are the stable worktree paths. The registration notebook entry records the successor job id, the producer job id, its command above, the shas and `pins.json`'s sha256.

## 10. Disclosures (record verbatim in the registration)

- **D1 Residual information P keeps** (QP #921 C3): which trades appear in P's bundle on a night depends on the real close time; `open_positions` is P's real count; a skip appears only if its replay closed before the cutoff. These push L - P and L-nr - P toward 0. The deferred rules are enforced at the frozen sha in: the post-TTL 10-match rule in `tools/hv6_review.py` (change validator, `regime_rule` through `tools/hv6_agents.py::validate_regime_rule`); the cross-process card-identity check (V2-3) belongs to build item 10 and the release code `tools/hv6_release.py` (v6.1 only; C1 below). **MANAGER:** confirm the exact function names with the author before notebooking (not looked up here).
- **D2 Nightly timing:** the nightly review runs at 00:05Z; a nightly written after 00:29Z must finish its model call within 60 s or it fails closed (QP #921 C3).
- **D3 Walk-2-hour information:** per `docs/HOLDOUT_LEDGER.md` arena results count for no walk-2 read and are walk-2-hour information (DEC-030 section 2 R1; QP Addendum 1 section 4). HV6's P0 counts and every later HV6 result are such information; any future Forward walk 2 registration discloses that they existed. Forward-1016 and arena tape are walk-2-hour information (SEALS-BRIEF).
- **D4 Walk-2-hour / in-clock hours:** hours before T0, including [2026-10-25T00Z, T0), are never OOS for HV6 and never counted; no HV6 rule is priced on them (G6). The smoke and any drill use such hours or synthetic tape; outputs are md5 and counts only.
- **D5 Replay and P0 day overlap:** the real-layout replay (job #1643 at 5071e29a; the same days were used by the superseded #1640 at 646f61a6) used arena-tape days 2026-10-21..23 (72 of 72 hours present, StubModel, fake chain stamped before each scan, md5 and counts only); the P0 day list overlaps them (counts only). Job #1596 (OOM at 7 GB) is a NON-RESULT, its drill root sealed by hand; job #1616 FAILED on scan speed only at 6669c4cc and is recorded as a FAIL at its sha (code fix #936, md5s not comparable); #1594/#1597/#1615 and, since #939, #1639/#1640 (both PASS at 646f61a6) are earlier-sha baselines, never cited for registration.
- **D6 Drill root never read:** `<HV6_ROOT>/drill/` is never an input to any count, read or regime-day list; `hv6_read`, the Am.6 hour table and the decision-log sha256 read only production paths; drill lines are never notebooked as HV6 versions (QP #921 C2). The drill output (including C2: the real-layout drill roots holding priced pre-clock hours) has never been opened for price, fill, exit or P&L.
- **D7 `scans_cap_bound` 0 on 10-21..23:** `WATCH_MAX_POOLS` was inert on the replay days (QP #930 binding 1). Each read reports the count of SCAN rows with `watch_capped > 0`.
- **D8 HV4 sibling (G7):** HV6 GEN/X and HV4 CON/BIG (L, F, X) share universe, setups, scanner code and model on overlapping days (DEC-031 Am.3 8(e)); the best of them at any read is in-sample. Each HV6 read lists HV4's 9 arms beside it (verdicts once read, counts only before) and the internal siblings L, L-nr, P, and never treats an HV4 pass and an HV6 pass on the same days as independent confirmations. Siblings enter N.
- **D9 bots.json in-process readers:** see the a6 sentence in section 6 (the pinned `register_bot` and the runner's `ArenaEngine`).
- **D10 Helius env file outside the guard:** `hv4_chain` (blob 95556d43, imported unmodified) reads the Helius key from its default env files (`/var/lib/mal/fast-listener/helius.env`), outside the HV6 path guard (reviewer #918 note 5). The key is never printed; credits per job are reported.
- **D11 Regime evidence R7:** change-to-version latency from the digest record is about 30-45 min; from the on-chain change instant up to about one radar cycle plus that. The 13.4 h figure (10-19 deploy 17:47:02Z, flagged 10-20T07:11Z) is a **structure-monitor stand-in, not a radar measurement** (the P23 radar did not run then); source `docs/research/p23-change-radar/DESIGN.md` section 0 row 13, to be re-cited from the radar record or notebook id (K3). "About 2 h" is withdrawn.
- **D12 Expected outcomes:** the regime test and learning are expected NOT_DECIDABLE or not shown in one 12-day window (0-2 events); a fast, clean FAIL or NOT_DECIDABLE is a good outcome. The prior that HV6 passes is low.
- **D13 Partial registration:** ids are registered one at a time; a failure on id k leaves ids 1..k-1 registered; the registration discloses it if it happens (QP #914).
- **D14 Seal state:** HV6 touches no HV4/Q50 output, no meta-capture, no DBC/fwdv1, no LaunchLab holdout, no D1; the radar out-dir is read by the block builder only (DESIGN section 8). This worker's own PR read none of them.

## 11. Checklist of every "before registration / registration must" condition

Status: MET (where) / MANAGER (a step for the manager, with the spot in this pack) / UNMET (blocks registration or needs a QP ruling) / LATER (binds a later build item or a read, listed so it is not lost).

### QP-RULING-1025 Addenda 1-3

| Cond. | Status | Where |
|---|---|---|
| K1 owner spend line verbatim with notebook id before the a4 call | MANAGER | section 3 (paste full text; confirm 3.00/day and D = 12 are covered) |
| K2 learning/regime earliest 2026-11-11T13:00Z; pin the single computed instant; `hv6_read` refuses to decide before it | pinned in section 2; `hv6_read` is LATER (item 10, before 11-07) | section 2 |
| K3 R7 wording as a structure-monitor stand-in; re-cite source | text MET in D11; re-cite the source row/notebook id: MANAGER | D11 |
| K4 standing items (#814, LAB_STATE check, ledger confirmation, P0, smoke, route a) | see section 4 | section 4 |
| a1 unmodified `register_bot`, hard-coded `hv6.` ids | MET | `tools/hv6_register.py`, blob 5eef156b, section 1 |
| a2/a3 print only HV6 dicts; synthetic registry test | MET | `tools/test_hv6_register.py` (tests named in section 6) |
| a4 manager runs it once after the smoke; command verbatim in the notebook with `date -u` | MANAGER | section 6 |
| a5 runner never opens bots.json; later tools read the notebook copy | MET for the runner; `hv6_read` counts N from notebook copies: LATER | section 6 |
| a6 sentence names both in-process readers with test names | text provided | section 6 |
| a7 idempotent retry; a changed line is a new id | MET | `test_retry_is_idempotent` |
| V2-1 windows and exits: decide at T0 + D + 13 h; D fixed; entries halt 11-11T00Z with code-only exits; H capped by 11-11 and pinned | MET: D, H, instant pinned (sections 1-2); the runner takes the required `--entries-halt-ms 1794355200000` (#939, `tools/test_hv6_runner_endgate.py`) | sections 2, 9 |
| V2-2 self-seal scope (versions, replies, change log, validator results, regime_probe lists, per-arm spend sealed until each read; manager sees summed spend and cap flags; sha chains notebooked nightly) | MET in code (`day_report` default, CLIs print sha chains only); procedure MANAGER | sections 8-9 |
| V2-3 releases: v6.(N+1) only if F/X keep running; cross-process card identity; > 1% mismatch NOT_DECIDABLE | LATER (v6.1 only, does not block v6.0); QP #921 C1: no v6.N with N >= 1 registers until the runner takes the release from the registration and runs `releases/v6.N/`, the test is updated and QP OKs | n/a for v6.0 |
| V2-8 R7 source, X as-sized cell (16 cells), #814 evidence disclosure, spend line | text in sections 3, 4, 10; cells are `hv6_read` (LATER) | |
| Regime-day rule, placebo line, write-once file notebooked before any HV6 outcome is opened | rule in section 5 row 32; the file is LATER (`hv6_read`) | |
| T9/T10/G1-G8, S1-S7, R1-R8, C1-C6 from the original ruling | built (items 1-5, 6); S1-S7 = section 7a; C1-C6 (canary) = DESIGN section 9, not in play (L is not canaried) | |

### Per-PR QP conditions

| PR | Condition | Status | Where |
|---|---|---|---|
| #911 | Pin readings 1-4 in the G5 table, source "HV4 constant or order kept, no data" | MET | section 5 rows 6-9 |
| #911 C1 | runner passes its own `RpcClient` state path under the HV6 root and refuses `MAL_HV4_CHAIN_STATE` | MET (QP #918 OK) | `live_chain_source`; spec section 9 keeps it unset |
| #911 C2 | all I/O via `check`/`guarded_open`; G2 test at the frozen sha on real path names; no name contains probe/shadow/canary | MET | `tools/test_hv6_cards.py` (real path names, line 240+), `test_all_file_io_is_allowlisted`. This PR's new smoke/P0 paths contain none of the tokens (`smoke/<TAG>` refuses "probe"). |
| #911 C3 | refuse `arena_score`, `arena_am6_read`, `arena_report_legs`, `arena_worst_gaps` in runner/agent/review code | MET | `ARENA_READER_MODULES`; AST test |
| #911 C4 | young-tier half slots, cards-per-call, 12 entry calls/h enforced and tested | MET (QP #918 OK) | `test_cards_per_call_4_and_2_degraded`, `test_entry_and_recheck_call_caps_per_hour`, `test_young_tier_half_slots_entry_rate_and_x_cap` |
| #914 R1 | K1 first; K4 items all recorded incl. the fallback option | MANAGER | sections 2-4 |
| #914 R2 | a6 names both readers with test names | text provided | section 6 |
| #914 R3 | G5 table carries #911 readings 1-4, #917 (X_EXIT, SKIP_PRICING_PLAN, budget constants, probe steps, regime max_tokens), #918 change 8 (WATCH, prescreen, creator/sniper, cooldown) | MET | section 5 |
| #914 R4 | pins: frozen sha, blob shas (hv4_cards, hv4_chain, arena_paper, hv4_prefetch), radar pins, D and H | MET (radar code sha and watch sha MANAGER) | section 1 |
| #914 R5 | command verbatim in the notebook with `date -u`; notebook copy of the five lines and records PR within 24 h | MANAGER | section 6 |
| #914 R6 | QP OK on this pack's exact head; reviewer verdict on the PR | MANAGER | this PR |
| #914 | after the rebase, a test that `HV6_MODELS` equals `hv6_agents.MODEL_ID` for every arm with `ARMS[a].llm` and `[]` for X | **MET by this PR** | `tools/test_hv6_register_models.py` |
| #914 | first live registration only after the smoke; partial run disclosed | MANAGER; D13 | |
| #914 Item 6 (drill/replay at a sha with every #918 change; free hours or synthetic; md5 and counts only; two runs equal; planted future print raises LeakageError; ChainSource + prefetcher not FakeChain; restart equal; arm permutation equal; regime drill) | **PENDING (MANAGER fills)** | at the final sha 5071e29a: 9-drill job **#1642** (placeholder: exit code, log md5, peak RSS, output sha256 once it finishes) and real-layout replay **#1643** (placeholder: PASS/FAIL under the PASS rule in row 28, 3 runs md5 equal incl. fast_scan off, peak RSS, scan p95, :02 tick p95, `max_candidates_per_scan`, `scans_cap_bound`, 72/72 hours, output sha256). #1639/#1640 passed at 646f61a6 and are superseded by #939. A FAIL is a code fix at a new sha, never a limit change. |
| #914 Item 7 | P0 at the final sha under S1-S7, day list fixed first, MiScusi job with MemoryMax; later scanner/board/cap change = new sha and P0 again | MANAGER | section 7a |
| #914 Item 7 | smoke reports take, WATCH and STALE counts, token and latency figures, chain calls/h vs 1,000, Helius credits; never names pools; pricing off or its ledger never opened; as_of_refusal or chain_stale above a small fraction = failed smoke | tool built | section 7b |
| #917 | registration carries every constant in #917 (incl. regime max_tokens 1,200 = assumed output) and the "example BUY anchors toward the wide plan" note | MET | section 5 rows 10-16 |
| #917 | list `ledger_unreadable` among the written stops (R8) | MET | section 5 row 31 |
| #917 R-a | group policy once per tick for all five arms | MET (QP #918 OK) | |
| #917 R-b | notebook the day's max level and halt instant as a cap-state flag; the V2 read lists days at level >= 1 and >= 3 beside L - X and F - X | flag logging MET (`day_level` rows); the read listing is LATER (item 10) | |
| #917 R-c, R-d | malformed probe grants nothing; preflight exception = deny | MET (QP #918 OK) | |
| #917 | all values in the changes and readings in the table with source "design choice / arithmetic, no data at or after 2026-10-25T00Z" | MET | section 5 |
| #918 (a) | registration pre-registers STALE > 10% of LLM entry verdicts in a read window = NOT EVALUABLE, per-arm and per-chunk STALE reported (sealed until the read), states the coupling | MET (text) | section 5 rows 22 and 30 |
| #918 (a) | smoke FAILS if STALE > 5% of LLM entry verdicts (pooled) or chunk-latency p95 > 8 s | MET in the tool | section 7b |
| #918 (b) | watched pools in the prefetcher's watch set; count watches added / fired / expired / no-card in the smoke | in-set MET; #939 adds the counters `watch_added/fired/expired/no_card.<arm>`, which the smoke report prints in `runner_counters` | section 7b |
| #918 (b) | HV6 additions in the G5 table (second WATCH, fired call in the 12/h cap, per-arm fired card, watches pause while halted) | MET | section 5 row 17 |
| #918 (c) | registration pins `MAL_HV4_RPC=helius`, the command and its environment verbatim | MET (text) | sections 1, 9 |
| #918 (c) | the runner writes the provider in a start row | MET by #939 (`start` row: frozen sha, pinned provider, `entries_halt_ms`) | section 9 |
| #918 (c) | G5 row: HV4 prefetched at 2,000/h and 48,000/day, HV6 at 1,000/h and 24,000/day | MET | row 21 |
| #918 (c) | smoke and each daily ops line report calls/h, served_none, stale-served, `chain_stale` and Helius credits from `chain-credits.json` | smoke MET; daily ops line MANAGER | sections 7b, 9 |
| #918 | cooldown rule changed (a refused card does not burn the cooldown); P0 unaffected | MET | row 20 |
| #918 7 | a6 sentence names the runner's engine; `order_refused` logs class only; no `start_all` | MET | section 6 |
| #918 (reviewer) | G2 path test at the frozen sha; Helius env file outside the guard disclosed | MET; D10 | |
| #921 C1 | no v6.N (N >= 1) registers until the runner takes the release from the registration and runs `releases/v6.N/` | LATER (v6.0 not blocked) | |
| #921 C2 | `<HV6_ROOT>/drill/` never an input to any count/read/regime-day list | MET (text) | D6 |
| #921 C3 | P's residual information; the file at the frozen sha that enforces each deferred rule; nightly at 00:05Z | text provided; function names MANAGER to confirm | D1, D2, section 8 |
| #921 C4 | the values table | MET | section 1 |
| #923 | G5 rows: reviewer-moved knob replaces the model's value; tightening takes the tighter; refused version halts all five arms (written stop); versions apply at the minute tick; list manifest-refused days beside L - X and F - X in each read | rows MET; the read listing LATER | rows 23-25, 31 |
| #923 | the 9-drill job re-runs at the final registration sha; the real-layout replay runs at that same sha; registration cites both job ids, the sha and the output sha256s | PENDING: jobs #1642 (drills) and #1643 (real-layout) at 5071e29a; MANAGER cites job ids, sha and output sha256s once they finish | |
| #930 | G5 row `WATCH_MAX_POOLS` with kept set and the replay's `max_candidates_per_scan` / `scans_cap_bound`; each read reports the count of SCAN rows with `watch_capped > 0` | text MET; `scans_cap_bound` is pending #1643; both figures from job #1643 (MANAGER pastes them) | row 26, D7 |
| #930 | quote sweep and engine trim listed as decision-neutral with test names; 6 h eviction and no `engine.prune()` stated | MET | row 27 |
| #930/#936 | both item-6 jobs re-run at the merged final sha (9 drills; real-layout at 16 GB, 3 runs) | MET at 5071e29a | |
| #936 | `fast_scan` recorded as decision-neutral by test names plus run 3 md5 equality; PASS-rule extension with its 2026-10-26 fixing date; full PASS rule quoted verbatim | MET | rows 18, 28 |
| #936 | #1616's FAIL stays recorded as a FAIL at its sha | MET | D5 |

## 12. Unmet items (stop-and-decide list)

None of the earlier code gaps is open. #939 (final sha 5071e29a) closed them:
- **U1 (V2-1) CLOSED:** required `--entries-halt-ms` (1794355200000 = 2026-11-11T00:00Z); entries and LLM calls stop for all five arms, code exits run to completion.
- **U2 (QP #918 (c)) CLOSED:** the `start` row carries the frozen sha, the pinned provider and `entries_halt_ms` before the first decision.
- **U3 CLOSED:** `watch_added/fired/expired/no_card.<arm>` counters; the smoke prints them.

**Still open, not code:** the item-6 results at 5071e29a (jobs #1642 and #1643, section 11), the radar successor job (9b P1) and the stable radar worktree (9b P2), LAB_STATE check line, fallback-clock choice line, full K1 text, feed-delay confirmation, QP OK on this head and a reviewer verdict.

**OPEN QUESTION FOR QP (tooling sha).** The runner registers at the final sha `5071e29afe473b6c9ff1f753bf045345e722b0f6`. The P0 count tool, the smoke tool, their wrappers and this pack live in PR #938, which adds files only and no runner code (a diff of this branch against 5071e29a shows only new tools, scripts, tests and this pack, plus the smoke wrapper and tool pins on `scripts/ops/hv6-*.sh` and `tools/hv6_smoke.py`/`hv6_p0_count.py`, all new in this PR). Both tools take the rule code from a clean checkout at 5071e29a (`--repo`) and refuse any other sha, so the P0 counts and the smoke exercise exactly the registered runner code; the tools themselves only count (P0) or wrap the same `Runner` (smoke). Question: is it acceptable that the runner registers at 5071e29a while P0 and the smoke run from this PR's head (tools pinned by sha256 in the fixed-first and smoke notebook entries), or must #938 merge first and the registration sha become that merge commit? The second route moves the sha: item 6 (#1642/#1643), P0 and the smoke would re-run at the merge commit and every sha in this pack changes again. Until QP rules, this pack assumes the first route.

No edge claim. HV6 has 0 out-of-sample trades and no clock. No book has cleared the promotion gate. Paper only.
