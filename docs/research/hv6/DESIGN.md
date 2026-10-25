# HV6: a general Haiku 5.5 trader with a regime channel (design v3, 2026-10-25)

Design worker, records only. **Nothing here says the trader makes money.** HV6 has 0 out-of-sample trades and no clock. No book has cleared
the promotion gate. Paper only. No API call, job, tape read or HV4/Q50 output was used. Every trades/day, token, sd and edge figure below
is an assumption or arithmetic, not evidence. v3 applies QP-RULING-1025-hv5-design (CHANGES, items 1-12), its ADDENDUM 1 (V2-1 to V2-8; G1 route (a); regime-day rule; S2 widening struck) and ADDENDUM 2 (OK with K1-K4) plus the reviewer's 5 items; the ruling's "R/S/G/T/C" ids are cited.

## 0. Name, and how this relates to #777 and #814

This book was first drafted as "HV5". Two older, still-open PRs already use that name for a different line, so it is **HV6** here (files
`docs/research/hv6/`, `tools/hv6_*`, root `/data/mal/arena/haiku-v6/`). The git branch stays `claude/hv5-design` because renaming it would close PR #904.

| PR | What it is | Relation to HV6 |
|---|---|---|
| #777 `hv5: DECLARE + counts-only cohort builder` (2026-10-21) | "V5-A delta-veto / V5-B exit manager": a model that may only veto/halve/manage entries that a **code rule book** already made (BASE-1 H5 BOOSTFLOOR, BASE-2 HV4 PULLBACK), priced on explore-0814. Counts only so far. | Different book. HV6's model chooses entries among scanner cards; it is not a veto layer. #777 stays "HV5". HV6 uses none of its cells, and no H5 entries (those are < 360 s, section 8). |
| #814 `hv5: validator + prompt fixes` (2026-10-23) | New files `hv5_validate.py`, `hv5_prompts.py`, `hv5_playbooks/`, `hv5_templates/` fixing two HV4 defects: risk "why" word count (33/144 invalid) and entry over-skip (0 LLM entries in 4 h; SKIP anchors in the prompt; parse/stale/below-min all labelled SKIP). Not wired into a runner. | HV6 **adopts those fixes** (symmetric SKIP wording, PARSE_ERROR / NO_OUTPUT / STALE / BELOW_MIN actions, `wanted_rate`, soft why limit) by copying into `hv6_*` only after #814 merges with a QP OK on its exact head (the registration discloses that #814's evidence is HV4 pre-clock smoke notes: decision counts, no prices, "0 LLM-arm entries in 4 h", risk invalid 33/144, and that "no entry rate was targeted"); until then HV6 cannot reach the take rate it assumes. HV6 has no risk LLM call, so the risk fixes are moot. |

**Real counts from #777** (COUNTS.md; explore-0814, Aug 14-28, 1-minute bars, setups through HV4 predicates, **before ranking, entry caps and
any veto; signers are upper bounds; in-sample; not HV6's GEN definitions**): CON_PULLBACK 6,718 attempts = 488.6/day (min day 47, worst 7-day
2,547); BIG_BREAKOUT 1,730 = 125.8/day (min 2, worst 7-day 507); BIG_SECOND_WAVE 6,764 = 491.9/day (min 42, worst 7-day 2,697); H5F not used.
Reading: in August the setup supply was not the limit; the board, the caps and the model's take rate are. October is unmeasured (section 10, P0).

## 1. Idea, priors, differences from HV4

Owner's vision: a long-running general trader that trades the same pools as everyone, is smarter, keeps improving, and when a regime changes
can be told to capitalise faster than a new strategy can be built and tested.

Priors (HV4 section 0): every universe measured loses before selection (fresh-pool longs -24.09%/attempt; generic 0.25 SOL entries -27.96%;
survivors >= 24 h -8.27% per 24 h; cost floor about -2.4% per round trip, -3.52% with rent); the v2 ranker did not hold; v3 learning was
about equal to a placebo (P 0.38) and its gain was a numeric dead-pool veto. **The prior that HV6 passes is low.** Every numeric filter sits
in the shared scanner; LLM proposes, code executes, default is SKIP (wording per #814).

| Change from HV4 | Reason |
|---|---|
| ONE persona "GEN" (CON and BIG merged, wider knobs) | one deciding stream instead of three |
| No lane H, no BST | lane H feeds the EXP-024 Look-2 seal |
| Risk facts in code, no separate risk LLM call | HV4's risk LLM was 0.88 USD/day at cap; facts and vetoes unchanged |
| Regime channel (section 2) | the new thing under test |
| Release cycle (section 7) | learning procedure as the unit under test |

## 2. Universe, scanner, cadence

**Universe = HV4 lane G.** Canonical PumpSwap pools of graduated pump.fun mints, WSOL quote, PRE-trade reserves plus V (about 17.58 SOL). Filters
(values as HV4 section 1): age since first PumpSwap print **2 h to 7 d** (2-24 h capped at 0.25 SOL and half the slots); real quote >= 30 SOL;
last 15 min >= 20 prints, >= 8 signers, last print <= 120 s; last 60 min >= 10 SOL; round-trip impact <= 3.0% else the size drops (a breach at
0.25 SOL is a veto). HV4 hard vetoes 1-7 fail closed. The claim "universe unmodified" is backed by a test at HV6's frozen sha asserting the
blob shas of `tools/hv4_cards.py` (747fd996fcb8) and `tools/hv4_chain.py` (95556d436069), identical to HV4's 9ed4c5f today (G8); any
difference is disclosed and the claim withdrawn. Setups (screens only): PULLBACK, BREAKOUT, SECOND_WAVE under HV4's predicates, one per pool per 30 min.

**Cadence.** Scan each minute at :02; board every 15 min: top 24 uncarded pools by 60-min volume plus new setups; <= 4 cards per call, <= 12
entry calls/h/arm; byte-identical cards for all LLM arms.

**Values changed against HV4, with source (G5).** All frozen before P0 (S2). Source for each is priors or arithmetic; none uses data at or
after 2026-10-25T00Z. The registration repeats this table and attests that the author and the manager saw no HV4 output, Console or notebook
P&L, attempt count, shakedown or smoke report of HV4.

| Value | HV4 | HV6 v0 | Source |
|---|---|---|---|
| min_conviction | 4 | 3 | arithmetic: more entries for the 100-attempt boundary; #814 over-skip finding |
| knob bounds stop / target / time / trail | -7..-10 / +6..+30 / 10-120 / n.a. | -4..-30% / +6..+150% / 10-360 min / 20-40% after +50% | merge of CON and BIG bounds (HV4 section 3) |
| hard stop, max hold | -15% / 120 min and -40% / 12 h | -40%, 12 h | BIG limits |
| board / entries per h / open / day loss stop | 10 / 4 / 4 / -1.0 | 24 / 3 / 6 / -1.5 SOL | cards supply for the boundary; BIG limits |
| X cap, X exit | 4/h CON-X | 2/h, -8/+20/60 min | prior design choice, no data |

**Trades/day (assumption).** With #777's August setup supply, the board (<= 576 cards/day) is binding, not supply. A SKIP-by-default model
taking 3-5% of cards gives **L about 17-29/day; planning figure 20, range 10-35**. X (2/h cap, <= 48/day) is cap-bound if October has >= 2
gated setups/h. P0 replaces these guesses (section 10) and changes no value. **Deciding arm = L.** S5: for LLM arms P0's would-be attempt is the card shown (an
upper bound), so H = 14 by the formula (Am.4 2.2: H = min(90, max(14, ceil(150/rate)))), **capped by the window end: the pinned H = min(14, days from T0 to 2026-11-11T00Z) = 12 for a 10-30 clock** (section 4). L's real rate is unknown; if it is under about 7/day, L ends
NOT_DECIDABLE at T0 + H. X is judged on its own P0 rate and floor.

## 3. Regime channel

**Principle.** Radar alerts reach the bot only as a pre-declared, machine-written block of numbers and enums (`hv6_regime.v1`) built by frozen
code (`tools/hv6_regime_block.py`) into the **reviewer's input**. Nobody edits the prompt, block or template after any outcome is seen. QP C4
of #900 is met by naming the radar in HV6's own pre-registration as reviewer evidence only; the block never reaches the scanner, a veto, a
cap, a fill, hour exclusion or another book.

**Source and pins (R3, R4).**
- Source = append-only `radar.jsonl` records with `run_utc` strictly before the block's `as_of_ms`, from the out-dir created at the merge of #900
  (`/data/mal/change-radar-arena1`). `digest-<date>.json` is rewritten in place on a same-day re-run, so it is **not** an input.
- The builder copies the exact bytes it read into `/data/mal/arena/haiku-v6/regime/` and puts their sha256 and `as_of_ms` in the version manifest.
  The runner refuses a block whose `as_of_ms` is at or after the version's write instant.
- Registration pins: the radar code sha the daily job runs; `tools/change_radar_seals.json` sha256 `5a2f8979c8493f7a3b200dd879f6f54c92a3bf383054af432c2fc35a8e10ca61`;
  the watch-file sha; the out-dir; `--venue-tape ''` (no DBC/LaunchLab field). If any pin changes during a window, the builder emits
  `radar_status = absent` from the change instant, the read discloses it, and the changed radar is never consumed.

**Real radar kinds (R1).** Pinned to the radar at b4a0f0d5 with a mapping table and a test against `change_radar.py`'s literals:
program_upgrade, program_gone, authority_change, account_closed, config_change, new_vocab, count_shift, new_key, gone_key, share_shift,
monitor_halt, monitor_warn. An unknown kind or schema sets `radar_status = partial` and counts the dropped event; it is never silently
skipped. A whole-record schema failure gives `absent`. Stale = latest record > 36 h before `as_of_ms`.

**Fields that enter**, per event, window 168 h:

| Field | Type |
|---|---|
| `id` | `RG-` + first 8 hex of sha256(kind, program, at_utc or day) |
| `kind` | the 12 real kinds above, mapped one to one |
| `sev` | HIGH/MED/LOW, the radar's own |
| `tier` | core/venue/router from the watch file |
| `program` | enum of the 14 watch-file names, else `OTHER` |
| `relevance` | `direct` if program in {pump, pumpswap, pump_fees} or kind is monitor_*, else `adjacent` (fixed code rule) |
| `change_utc`, `hours_since` | from the structured `at_utc` / `day` |
| `key_class` | enum swap, create, complete, pool_create, other, mapped from `keys` by a fixed table |

**Subject mapping (V2-4).** For new_key, gone_key, count_shift and share_shift the key sits only in the structured `subject` string
(`"<src> <prog>/<type>"`). `program` and `key_class` come from an exact-match table over that string; an unknown string maps to `OTHER` / `other`
and is counted; `subject` itself is never emitted. Tested against `change_radar.py`. (Kinds whose mapping gives `OTHER` are `adjacent` and never
trigger an intraday review.)

Summary: `n_high_core_7d`, `hours_since_last_high_direct`, `radar_status`. **`ratio` and `n_writes` are dropped from v1 (R2):** they exist only
inside the free-text `detail`, and no regex on `detail` is allowed. A later release may add them only after a radar PR makes them structured
(new pin, QP OK). **Excluded (R5):** `detail`, `title`, `subject` (raw key/instruction names), `keys` (only mapped to `key_class`),
`rpc_host`, `rpc_calls`, `errors`, `stages`, account hashes, authority addresses, signer data, gap/hb/process counts, venue-tape counts, any
price, outcome or P&L. No free text means no injection surface; same input bytes give the same block bytes (md5 test).

**Use (reviewer only; cards stay byte-identical).**
1. Nightly review at 00:05Z: L and P get a bundle built to HV4 section 4's spec from HV6's own trades and skips with entry at or after HV6's T0
   (G4), plus a "REGIME EVIDENCE" section. L-nr gets the same bundle without it. Nothing from the smoke, warm-up or any pre-T0 hour enters.
2. Intraday regime review: code-triggered when a new radar record holds a HIGH event with relevance `direct`. One call each for L and P with ONLY
   the block, current playbook/knobs and change log. Outcome inputs stay at that day's 00:01Z freeze. Cap 2/day.
3. Change type `regime_rule` (beside playbook_edit / knob / local_veto) citing `RG-` ids present in the block.

**Intraday manifest rule (R6, frozen).** Outcome inputs at or before the day's 00:01Z freeze; block `as_of_ms` before the write instant; each
version hashed and logged before its first decision (DEC-031 section 1.3); the manifest-chain test covers intraday versions (HV4's
`max_input_close_ms` rule is replaced for HV6 by these three).

**Latency, honestly (R7).** The radar is a daily job (about 07:11Z). Change-to-version latency = radar cadence + digest + the review run + 30
min. From the digest record: about 30-45 min. From the on-chain change instant: up to about one radar cycle plus that (a **structure-monitor stand-in, not a radar measurement**: the P23 radar did not run then; the 2026-10-19 pump
redeploy, deploy 17:47:02Z, was flagged by the structure monitor's daily A3 run at 10-20T07:11Z, 13.4 h after; source: `docs/research/p23-change-radar/DESIGN.md` section 0, to be
re-cited from the radar record or notebook id in the registration). Registration states both numbers; "about 2 h" is withdrawn.

**Validator for `regime_rule`.** Must cite a present `RG-` id. Tightening is always allowed (stop, time, local veto; TTL <= 72 h). Note: a
"smaller size" tightening is a **no-op in the flat-stake deciding cell**; stop, time and local-veto changes do act there (R8). Loosening only as a
bounded probe: stake 0.25, <= 1 knob step inside bounds, <= 1 extra entry/h, TTL <= 72 h, every trade tagged `regime_probe`; the 10-match rule is
waived for TTL changes only; after the TTL a change needs the 10-match rule on post-change trades. Cannot touch scanner, vetoes, risk facts,
code limits, fills, costs, scorer, gate, stakes above 0.25, model, reviewer prompt, embargo, controls. <= 3 changes per review.

**Halt-for-review (R8).** The "human look" after a direct HIGH event may read only the radar event and ops/health. It reads no HV6 outcome and
changes nothing; any edit is a new bot_id. **No HV6 arm is stopped at anyone's discretion.** The only stops are the written safety stops: budget,
memory/OOM, seal breach, feed integrity. A stop the rules did not name, after a regime event, would select on the regime.

**What it can show.** Pump-family HIGH events: 3 in about 3 weeks. A 12-day window holds 0-2, so **NOT_DECIDABLE or not shown is the expected
outcome for the regime test, and for learning (section 4)**. A pre-launch drill on a synthetic block (stub, then a few real calls at the smoke)
checks plumbing, validator and latency only.

## 4. Arms, tests and reads

Cards byte-identical at the same instant; only prefix and nonce sha256(arm|kind|card_sha|seq) differ; arms never share a cached reply.

| Arm | Definition | Promotable |
|---|---|---|
| **L** | GEN + nightly reviewer + regime channel | yes |
| **L-nr** | GEN + nightly reviewer, no regime block, no regime review | yes |
| **F** | GEN, playbook v0 frozen | yes |
| **P** | L's procedure incl. the real block; outcomes in the bundle shuffled (seeded sha256(arm\|date)) | no |
| **X** | same scanner and vetoes; top-ranked setups at 2/h; v0 default exit; no LLM | yes (rule book) |

**Gate read (T1, T3).** B is DEC-031 section 2's mechanical boundary: the first 00:00Z boundary with >= 100 counted attempts on >= 5 UTC dates by
the outcome-blind decision-timestamp tool; the read runs once after all those attempts have exited (max hold 12 h). Nothing else starts,
delays or repeats it. At 20/day the boundary is T0 + 5 d (2026-11-04T00Z for a 10-30 clock). If it has not come by T0 + H, the book is
NOT_DECIDABLE. "Attempts" means every entry decision, guarded reverts included. The deciding cell is the 1.9 s leg at deciding_stake 0.25
(pinned in the registry), decided in **all four sub-cells**: flat 15% and pressure scale 1, each under strand rules sc and total (deciding
on "total" alone is taint 8(a)). The CLAUDE.md gate is unchanged: >= 100 attempts, >= 5 UTC dates with a majority positive, CI90 lower bound of
mean SOL per attempt > 0 (1,000 draws, seed 1, 5th percentile), total positive after removing the top 3, both fail models. Ex-best-day > 0 is
required before any canary proposal. NOT_DECIDABLE / NOT EVALUABLE routes: DEC-014 Am.1-2 (orphans, settlements, missing pressure leg); the
5% feed-gap cap (section 1.4); Am.6's 10% excluded-hour cap; > 10% invalid replies (NOT EVALUABLE); > 20% flagged.

**Comparison windows (T2), calendar terms, write-once (`<bot_id>.v12.json` pattern), independent of B.** T0 = clock (target 2026-10-30T00Z).
- V1/V2: window [T0, T0 + 7 d), decided at T0 + 8 d (2026-11-07T00Z).
- Learning and regime: window [T0, T0 + D), **D = 12** (ends 2026-11-11T00Z = the API horizon), 6/6 week split. Decided after every attempt decided in
  the window has exited: **at the earliest T0 + D + 13 h** (max hold 12 h), or when the R-null job ends if later.
- **D is fixed in the registration before the clock.** A later owner line never changes D and never extends a running window. At 2026-11-11T00Z
  entries halt and code exits (stop/target/time, no LLM, since the budget drop order ends re-checks) run to completion.
- The window end 2026-11-11T00Z caps H (Am.4 2.2, "capped by any window end"). The capped H is pinned with D in the notebook copy and the records PR
  (Am.7). A boundary not reached by then is NOT_DECIDABLE.

**Contrasts.** Each is A1's exact one-sided sign-flip on daily differences at stake 0.25 (A1 items 1-6; a day with no trade in either arm counts as 0), in all four sub-cells, on both legs. A1 item 6 (a rare large loss can pass a symmetric test) is quoted in the registration.

| Question | Contrast | Threshold, minimum days |
|---|---|---|
| V2: LLM beats rules | L - X, F - X | p <= 0.05/6 (the /6 kept from HV4 for conservatism), >= 7 days |
| V1 | primary ex-top-3 of L, L-nr, F above the p95 of the 200-seed R null | null must verify (below) |
| Learning (a conjunction) | (L-nr - F, p <= 0.025) AND (L-nr - P, p <= 0.025) AND week-2 >= week-1 | >= 6 days |
| Outcome information | L - P | p <= 0.025, >= 6 days |
| Regime | L - L-nr on regime days (rule below) | p <= 0.025 one-sided in each of the four sub-cells; >= 6 non-zero regime days in each sub-cell, else NOT_DECIDABLE |

**Regime-day rule (QP Addendum 1 section 3; fixed in the registration text, computed by frozen `hv6_read` code).**
- **E (events):** every event in HV6's own pinned regime byte-copies (`/data/mal/arena/haiku-v6/regime/`, the bytes L actually saw) with sev HIGH and
  relevance `direct`, from a record whose `radar_status` is ok or partial (never absent or stale), deduplicated by `RG-` id.
- **t_e:** the `run_utc` of the first radar record carrying that `RG-` id, i.e. the first instant the block could hold it. Not the on-chain
  `at_utc`, which is reported beside it.
- e counts if t_e is in [T0, T0 + D). **Regime days** are the UTC dates d in [T0, T0 + D) such that [d 00:00Z, d + 24 h) overlaps [t_e, t_e + 72 h)
  for some e in E, as a union, each date once.
- Report-only placebo line: the same L - L-nr statistic on the non-regime days. If it is as large as on regime days, the read says the difference
  is not attributable to the regime channel.
- The E list (RG id, t_e, at_utc) and the regime-day list are written once to a write-once file whose sha256 is notebooked **before any HV6 outcome
  is opened**. Days with the block absent or stale for more than 12 h are listed but do not change the set.

**R null (T7).** Not a live arm: 200 seeds replayed at the registered sha; R's instants and sizes come from L's hash-chained decision log,
verified against its daily notebooked sha256. Anchors replacing "seed 1 reproduces the live R arm": (i) two independent seed-1 replays are
byte-identical; (ii) an as-of mutation test (a planted future print raises LeakageError in the replay path); (iii) the log verification above.
Without a verifying manifest V1 is NOT_DECIDABLE.

**Budget halts vs X (T8).** The drop order hits L, L-nr, F, P together; **X halts entries in step with the group**, so L - X and F - X are never
read on days where only X kept trading.

**Learning is testable only in principle (T9).** v3 learning was about placebo (P 0.38); day-1 L-nr minus F is pure nonce noise; 12 days give
little power. Pre-registered expectation: **NOT_DECIDABLE or not shown.** If learning is not shown the reviewer stays off the next window.

**Cells and multiplicity (T4).** Counted by HV4's N2 convention (book x stake; fail legs and strand rules are sub-cells): 4 books x 3 stakes
(0.25 primary, 0.5, 1.0) = 12, plus 4 **as-sized** cells (L, L-nr, F and X; the impact size-drop and the 2-24 h cap apply to X too) = 16. Each read carries the Am.3
item 3 block: N = every book with a clock before the read (HV4's 9, Q50, the H5 books, hunt23/cohort books, HV6's own 5 incl. P, stopped and
NOT_DECIDABLE included, counted from notebook copies), the total cells, the plain line 1 - 0.95^N, and the report-only p (10,000 draws, seed
1, larger of the two fail models) with its Holm rank. c (0.05/c) applies only to a later confirmation.

**Siblings (G7).** HV6 GEN/X and HV4 CON/BIG (L, F, X) share universe, setups, scanner code and model on overlapping days (DEC-031 Am.3 8(e)):
the best of them at any read is in-sample. Each HV6 read lists HV4's 9 arms beside it (verdicts once read, counts only before) and the
internal siblings L, L-nr, P, and never treats an HV4 pass and an HV6 pass on the same days as independent confirmations.

**What the numbers must look like (T5, corrected).** The 5th percentile is z = 1.645 (1.28 is the 10th). CI90 lower bound > 0 needs mean >
1.645 x sd / sqrt(n). At sd of net return 8-12%: n = 100 needs 1.32-1.97% of stake (0.0033-0.0049 SOL at 0.25); n = 140 needs 1.11-1.67%
(0.0028-0.0042 SOL). v1's 1.0-1.5% understated the bar by about 22%. 8-12% fits the v0 knobs only: with stops to -30%, targets to +150% and
12 h holds the sd can be several times larger and the bar scales with it. A big-win/small-loss book must also survive removing its top 3.

## 5. Capacity line

Round-trip impact versus mid is about 2 x s / R_eff, R_eff = real quote + V: 47.6 SOL at the 30 SOL floor, 117.6 at Q = 100. Constant-product
arithmetic, fees separate (floor about 2.4%, 3.52% with rent); the engine's own impact is the authority.

| Stake | Impact, Q 30 | Impact, Q 100 | Gross edge to break even (Q 30) |
|---|---|---|---|
| 0.25 | 1.05% | 0.43% | about 3.5% (4.6% with rent) |
| 0.5 | 2.10% | 0.85% | about 4.5% (5.6%) |
| 1.0 | 4.20% (over the 3.0% cap; size drops to about 0.7) | 1.70% | about 6.6% or more |

SOL/day = trades/day x stake x net edge: 20 x 0.25 x 1% = 0.05; 20 x 0.25 x 3% = 0.15; 20 x 0.5 x 3% = 0.30 (edge lower at that impact); 30 x 0.25 x 5%
= 0.375. At 0.25 SOL a USD 20-50/day target needs a net edge far above the lab's priors. HV6 is a research-speed and regime-reaction book first.

## 6. Budget (claude-haiku-5-5 only; HV4 section 6 price table)

Per MTok: input 0.10, cache read 0.01, cache write 0.125, output 0.50. Thinking off, no temperature; max_tokens 400 entry, 120 re-check,
4,000 reviewer; one 5-min cache marker; refusal or parse failure = abstain.

| Call | cached / new / out tokens (assumed) | USD/call | Cap | USD/day at cap |
|---|---|---|---|---|
| Entry (<= 4 cards) | 2,500 / 3,200 / 300 | 0.000495 | 12/h x 4 arms | 0.57 |
| Re-check | 2,500 / 900 / 60 | 0.000145 | 24/h x 4 arms | 0.33 |
| Nightly reviewer (L, L-nr, P) | 0 / 60,000 / 4,000 | 0.0080 | 3/night | 0.024 |
| Regime review (L, P) | 0 / 8,000 / 1,200 | 0.0014 | 2/day x 2 | 0.006 |

Check: 2,500x0.01 + 3,200x0.10 + 300x0.50 per M = 0.000495; re-check 0.000145; reviewer 0.006 + 0.002 = 0.008. Total at call caps **0.93
USD/day**, expected 0.4-0.7. Hard caps in HV6's own ledger: 0.50 per LLM arm, 0.15 reviewers, 0.05 regime review (2.20) and a **global 3.00
USD/day**. Drop order as HV4. Shared pot: HV4 hard cap 4.70/day; worst case 10-25 to 11-11 is HV4 17 d x 4.70 = 80 plus HV6 12 d x 3.00 = 36 =
116 of 200. HV6 does not read HV4's spend ledger; the manager sums the two daily; the owner's console cap is the backstop. HV6 has its own
chain cache and a 1,000 calls/h cap; Helius credits are reported per job.

## 7. Releases (T10)

A release = one frozen bundle (code sha, reviewer prompt, validator thresholds, bundle spec, regime-block spec, playbook seed), forward-registered
under its own bot_ids with its own clock, P0, smoke and gate read.
1. **No v6.(N+1) clock starts before v6.N's comparison window ends.** v6.N keeps all five arms to the end of D. With D = 12 the earliest v6.1 clock
   is T0 + 12 d; releases are therefore about fortnightly, not weekly.
2. (V2-3) A v6.(N+1) is registered only if v6.N's F and X keep running through its window, which needs an owner spend line past 11-11 for F's LLM
   calls; otherwise its L/L-nr/P contrasts against F and X are NOT_DECIDABLE by rule. Each card's sha is logged in both processes (one process per
   frozen_sha) and a cross-process card-identity check is a precondition of every cross-release contrast; a mismatch rate above 1% of cards makes it
   NOT_DECIDABLE. This blocks v6.1 only, not v6.0.
   A release registers **only the arms whose procedure changed** (L, L-nr, P as applicable). F and X are not re-registered (a second frozen copy
   is forbidden, DEC-031 Am.3 item 5); the new arms are compared with the continuing v6.0 F and X on the same stream.
3. Release inputs (frozen `tools/hv6_release.py`) are **non-outcome metrics only**: invalid-reply rate, schema/bounds rejects, token cost,
   latency, block coverage. Not allowed: P&L, any arm gap, "effect of claimed sign not met" rejects and the change-acceptance rate (they depend
   on outcomes through the 10-match rule). Changeable registry: min-match in {8, 10, 15}, bundle skip count, max changes/night, TTL <= 72 h,
   evidence window. Anything else is a human-written PR with QP review.
4. Data-only releases need a manifest pin (about 24-36 h with the P24 pack generator); code changes keep the md5 decision-equivalence replay;
   mid-week restarts are recorded in LAB_STATE.
5. Each release adds to N; the best release is a winner's-curse draw. With <= 2 releases by 11-11 the cross-release claim has **no power**; it
   yields operational improvement and candidates, not proof.

## 8. Seals and the guard (SEALS-BRIEF line by line; G1-G8)

1. **HV4.** HV6 never opens HV4 ledgers, fills, prices, exits, positions, P&L, decision rows, bots.json, attempt counts, score/preview/am6_read
   output, or HV4 jobs #1561/#1562 status or log tail. HV6 reuses HV4 **code** only by importing unmodified files; changes go to `hv6_*` copies.
2. **Guard (G2): allowlists, then a denylist.** Reads: `/data/mal/arena/tape`, the pinned radar out-dir, HV6's own root
   `/data/mal/arena/haiku-v6/` (its engine ledger, decision log, spend ledger, regime copies, read outputs) and HV6's own chain cache. Writes:
   `/data/mal/arena/haiku-v6/` only. Everything else is denied, plus the tokens `hv4`, `haiku-v4`, `q50`, `bots.json`, `shadow`, `canary`,
   `forward-*`, `truth-*`, `fresh-08*`, `exp011-*`, `tip-tape-archive`, `/var/lib/mal/sealed`, `meta-capture`, `blocks/forward-1016`. The bare
   tokens `ledger`, `decision`, `score` are **not** used (they would block HV6's own files and force a mid-window code change). No file is
   named with "probe". Tested at the frozen sha with HV6's real path names; an import guard blocks modules that read outputs.
3. **Registry (G1): route (a), decided by QP Addendum 1.** Append-only registration into `/data/mal/arena/bots.json` (DEC-031 section 1.2's single
   registry) through `tools/hv6_register.py`, under a1-a7:
   - a1: `tools/arena_paper.register_bot` unmodified at its pinned sha (editing a shared engine file while books run is taint 8(i)). The wrapper
     registers exactly HV6's five hard-coded bot_ids, each starting `hv6.` (`hv6.0-L`, `-L-nr`, `-F`, `-P`, `-X`, or the registered spelling).
   - a2: it prints and returns only the dicts `register_bot` returns for those ids. On any exception it prints only the exception class and the
     HV6 bot_id, never the message text, and exits non-zero. It never prints, logs or writes any other line, count or id from the file.
   - a3: a test on a synthetic registry with foreign register lines, a foreign stop line and a malformed line asserts that stdout, stderr, the
     return value and every file written contain no foreign bot_id, sha, clock, stake, model id or stop instant.
   - a4: the manager runs it once, after the smoke passes at the final sha (Am.6 1.4); the command goes verbatim in the registration notebook
     entry with its `date -u` instant; the notebook copy of the five lines (section 1.2) and the records PR within 24 h follow.
   - a5: the runner never opens bots.json (the guard token stays). Later HV6 tools read their own clock or stake from the notebook copy or the
     records PR; if one must use the registry it goes through a view function with the a2/a3 properties and the same test. `hv6_read` counts N from notebook copies.
   - a6: the registration states "bots.json was read in-process by pinned unmodified `register_bot`; no line other than HV6's own left the process
     (test name)". That is not a seal contact; a later-found failure of a2 or a3 is, and is disclosed in every read it touches.
   - a7: retries are allowed; `register_bot` is idempotent for an identical line and refuses a changed one, which then means a new bot_id, never an edit.
4. **Self-seal (G3).** Until each bot_id's read, HV6's ledgers, fills, P&L, decision rows, attempt counts (except the pinned count tool) and score
   output are sealed from the manager, the release author and the owner-facing Console. Also sealed until each read (V2-2): playbook and knob versions, reviewer
   and regime-review replies, the change log and its measured effects, validator accept/reject results, `regime_probe` trade lists, and per-arm
   API spend (the manager sees only HV6's summed daily spend and cap-state flags). **Only the decision-log sha256, the manifests' sha256 chain and the
   Am.6 hour table are notebooked nightly.** No daily P&L to console.json or the notebook (this overrides the HV4 section 4 pattern).
5. **Q50 / EXP-024 Look-2** (no H5 outcome on any October V-range pool in its first 360 s): excluded by construction. Universe age >= 2 h, a hard
   code check refuses any card under 2 h, no H5 trigger, no lane H, no classified H5 feed, bundles hold only HV6 trades. #777's H5F entries
   (< 360 s) are not used.
6. **DBC.** No venue tape, no DBC row, no fwdv1; 10-17..10-24 is spent: nothing priced there.
7. **Arena tape.** Live arena feed in the forward window only; research reads are counts and creates only (P0). Nothing is joined to HV4/Q50
   output. Hours before T0, including [10-25T00Z, T0), are never OOS for HV6 and never counted; **no HV6 rule is priced on [10-25T00Z, T0)** (G6).
   Disclosure (DEC-030 section 2 R1; QP Addendum 1 section 4): per HOLDOUT_LEDGER, arena results count for no walk-2 read and are walk-2-hour
   information. HV6's P0 counts and every later HV6 result are such information; any future Forward walk 2 registration discloses that they existed.
8. **Pre-clock replays (G6).** The md5 decision-equivalence replay and the drill use arena-tape hours before 2026-10-25T00:00Z that the manager
   confirms are free in `docs/HOLDOUT_LEDGER.md` (D2/D3 check before pricing any October pool), or synthetic tape; outputs md5 and counts only.
9. **OOS answer.** Counted hours at or after T0 are forward and OOS for each HV6 bot_id as a whole system (versions included), provided G1-G8
   hold. A frozen value fit on in-clock prices makes that value's book in-sample for those hours ("not OOS").
10. Gate unchanged. Paper only.

## 9. Live path: DEC-032 C0 canary

DEC-032 section 1.4 ("any code or prompt change is a new bot_id") and DEC-031 section 1.3 ("a self-improving book is the whole system, versions
included") leave open whether a reviewer-written playbook version is a prompt change. Until a DEC-032 amendment, a QP ruling and the owner
settle it, **L itself is not canaried.** F and X are eligible if their own gate read passes.

**Frozen-snapshot canary (C1-C6).**
- C1: the snapshot is a new bot_id with its own registration, P0, smoke, clock, H and gate read; only its own PASS opens DEC-032 section 1; it never borrows L's pass; it counts in N.
- C2: which version is frozen is set by a mechanical rule in HV6's registration before T0 (example: the L playbook in force at 00:30Z after L's DEC-031 boundary instant, whatever L's read returns). Never picked by looking at any version's, arm's or HV4's outcomes.
- C3: the snapshot was learned from L's outcomes on HV6's counted hours, so those hours are in-sample for it. Its clock is after the snapshot instant and after the last exit of any trade in the bundle that produced it.
- C4: at freeze every TTL `regime_rule` and `regime_probe` is stripped or kept with its absolute expiry; the registration says which. The snapshot runs no reviewer and no regime review.
- C5: L and the snapshot are siblings (8(e)); the best of them is in-sample. The owner's standing C0 OK (DEC-032 Am.1) covers the snapshot only after its own PASS.
- C6: the C0 cost line at S = 0.05 SOL quotes all three parts: sends 2 x 505,000 lamports = 2.02%; the E1 floor 384,022 lamports per 0.05 SOL = 0.77% in the paper twin; the ATA rent 2,039,280 lamports = 4.08% of 0.05 SOL when the close is blocked, with the close-blocked count from the mint fetch.

Also needed before any canary (owner/Helm items stay theirs): parity tool `arena_live_parity` (S5) and tie-out tool, dry run, paper twin at S with
the executor's actual per-send fees; worst-in-slot mean and CI90 lower bound (S4) from the decision log; an LLM-in-the-loop executor with a
hash-chained log, no buy on API outage or invalid reply, stale-card 10 s rule, landing p50 counted from the reply in hand (<= 1.9 s), a sell
builder for arbitrary PumpSwap pools, the mint fetch and close-blocked count; the fail-closed list of DEC-032 section 3; halts P1-P7 (the
radar's direct HIGH event is a halt-for-review only if pre-registered, and with R8's limits); an Anthropic key on the executor host (owner and
Helm approve; nothing is done here). Confirmation (100 attempts on 5 more dates, p <= 0.05/c) stays independent; reports are labelled
"unpromoted execution measurement".

## 10. P0 shakedown (S1-S7) and the build, in order

**P0 = counts only, DEC-031 Am.3 item 1 / Am.4 item 2.1** (>= 3 full closed UTC days, floor 2.0/day, no zero day; hard-gap or data_loss > 1 h = NOT_DECIDABLE).
- S1: whole closed UTC days of `/data/mal/arena/tape` only, inside [2026-10-20T00:00Z, job start). In-clock days (>= 10-25) are allowed for structure counts. Not 10-19 (partial tape, redeploy day), nothing earlier, no other directory (never a shakedown, smoke, venue, forward, holdout, meta-capture or haiku-v4 path). The day list (>= 3, preferably the latest 4-5) is written in the fixed-first notebook entry before the job and never extended or swapped.
- S2: runs at HV6's registration-candidate sha, counts-only: the GEN scanner, the 2 h guard, setups, cooldown/board, X's 2/h cap. No engine, exit, LLM or paper fill. Counts are pre-chain-veto upper bounds and are labelled so. Any later scanner/board/cap change is a new sha and P0 re-runs. **P0 decides only registration (Am.4 2.1 floor, >= 2.0/day, no zero day, gap rule) and H.** Every scanner, board and cap value stays at the frozen v0 (the
  count-to-value widening rule is struck, QP Addendum 1 section 3). If P0 shows X below 25/day, the registration states it and revises X's planning figure;
  nothing is changed.
- S3 prints, per UTC day and hour, counts only: filter-pass pools per scan (p50/min/max), newly passing pools/h, setups by GEN type, board cards after cooldown (age 2-24 h vs >= 24 h), X would-be attempts at 2/h, hour files present/missing, gap and data_loss seconds.
- S4 never prints a mint, pool, signature, trader or wallet, a price, reserve, return, volume level or price-derived distribution, a per-pool row, or anything run with HV4's persona configs (that would rebuild sealed HV4-X would-be attempts). Nothing is joined to HV4/Q50 output.
- S5: H = 14 by formula for LLM arms, pinned as min(14, days to the window end) = 12 for a 10-30 clock (sections 2 and 4). S6: the Am.6 smoke reports take counts and token/latency figures, never which pools; pricing off or its ledger never opened; any changed value is a new sha and smoke. S7: before the job a notebook entry with the QP ruling's sha256, the fixed-first text, day list, command and job id; a MiScusi job with MemoryMax; afterwards the output sha256 in the notebook and counts in the registration record.

| # | Item | Who | Depends on | Done by |
|---|---|---|---|---|
| 0 | QP OK on the exact head (V2-1..V2-8 in text; G1 route (a) decided); K1-K4 recorded before the clock | manager + QP | this PR | 10-26 |
| 1 | `hv6_cards.py` (GEN scanner over unmodified hv4 cards, 2 h guard, blob-sha test) | builder | 0 | 10-27 |
| 2 | `hv6_agents.py` (playbook v0 from #814 wording, validators incl. `regime_rule`, per-arm ledgers, 3.00 cap, Stub) | builder | 0, #814 merged with a QP OK on its exact head | 10-27 |
| 3 | `hv6_regime_block.py` (real kinds, radar.jsonl as-of source, byte copy, exclusions, determinism, injection tests) | builder | 0 | 10-27 |
| 3b | `tools/hv6_register.py` + its a3 synthetic-registry test (a1-a7) | builder | 0 | 10-27 |
| 4 | `hv6_runner.py` (5 arms, X halts in step, allowlist guard, log chain, intraday trigger, MiScusi job) | builder | 1-3 | 10-28 |
| 5 | `hv6_review.py`, `hv6_release.py` (bundle, placebo, manifest chain incl. intraday rule) | builder | 2, 3 | 10-28 |
| 6 | md5 replay + drill (pre-10-25 free hours or synthetic) | manager | 4, 5 | 10-28 |
| 7 | **P0 count job at the final sha** (S1-S7), then real-model smoke (Am.6) | manager | 6 | 10-29 |
| 8 | Registration pack (`fasttrack_book.py`), G1 registration, QP OK on exact head, reviewer verdict | manager + QP + reviewer | 7 | 10-29 |
| 9 | **Clock T0** | manager | 8 | **2026-10-30T00:00Z** only (see fallback rule) |
| 10 | `hv6_read.py` (sign-flip, V1/V2, 16 cells, N block, regime-day table, R-null manifest) | builder | before 11-07 | 11-05 |
| 11 | Live readiness (parity, tie-out, executor spec) | builder + manager | after a screen pass | later |

**Fallback-clock rule (reviewer item 3; honest option).** D = 12 with the window ending by 2026-11-11T00Z holds **only for a 10-30 clock**. For a
10-31 or 11-01 clock, D = 12 would end 11-12 / 11-13, past the API horizon, and cutting to 11-11 gives D = 11 / 10, which breaks the 6/6 split and the
>= 6-day learning minimum. So for a later clock the registration, before the clock, either (a) quotes an owner spend line covering T0 + 12 d
verbatim (then D = 12, the window ends at T0 + 12 d, pinned H = 12, and the read instant moves with it), or (b) declares learning, outcome-information
and regime contrasts NOT_DECIDABLE in advance and runs only the gate read and V1/V2 (window [T0, T0 + 7 d), unaffected). D is never changed after the clock.
Dates assume zero slip; a slipped day moves the clock a day. Owner: the HV6 spend line (<= 3.00 USD/day inside the shared 200 USD to 11-11), recorded verbatim in the registration before the clock.
**OWNER SPEND LINE: pending (manager asking 2026-10-25).** Manager checks before registering: LAB_STATE for a kill-review week that forbids new forward books; the ledger for replay hours.
Earliest reads for a 10-30 clock: V1/V2 2026-11-07T00Z; learning/regime 2026-11-11T13:00Z at the earliest (T0 + D + 13 h; T0 + 12 d = 11-11T00Z); gate at its mechanical boundary (about 11-04 at 20/day).

## 11. Biggest risks

1. **No edge may exist.** Every measured universe loses before selection and v3 learning was about placebo. A fast, clean FAIL or NOT_DECIDABLE is a good outcome.
2. **Regime channel and learning are expected NOT_DECIDABLE** in one 12-day window (0-2 events), and the canary path conflicts with a nightly-changing playbook.
3. **Contamination and siblings.** HV4 and HV6 trade the same pools while HV4 outcomes are sealed; one HV4 read, one value tuned on in-clock data, or a registry/guard slip makes HV6 in-sample.

**Nothing in this document says the trader makes money.**
