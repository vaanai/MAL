# HV5: the next Haiku 5.5 trader (design, 2026-10-25)

Design worker, records only. **Nothing here says the trader makes money.** No book has cleared the promotion gate. Paper only. No API
call, no job, no tape read and no HV4/Q50 output was used to write this. Inputs: HV4 DESIGN (hunt20/haiku-v4), the P23 radar docs
(PR #889/#900), P24 PLAN, DEC-031/032, SEALS-BRIEF (hunt30). All numbers below are design targets or arithmetic, flagged as such.

## 0. What HV5 is, and what it is not

Owner's vision: one long-running general trader that trades the same pools as everyone, is smarter, keeps improving, and when a regime
changes can be told to capitalise faster than a new strategy can be built and tested.

Priors that stay (HV4 section 0): every universe measured loses before selection (fresh-pool longs -24.09%/attempt; generic 0.25 SOL
entries -27.96%; survivors >= 24 h -8.27% per 24 h; cost floor about -2.4% per round trip, -3.52% with rent); the v2 ranker did not hold;
v3 learning was about equal to a placebo (P 0.38) and its gain was a numeric dead-pool veto. **The prior that HV5 passes is low.**
HV5 is built so that a loss is learned from fast and cleanly, not because an edge is expected.

Inherited and not relaxed: every numeric filter sits in the shared scanner and no arm is credited for it; LLM proposes, code executes,
default is SKIP; comparisons are L-vs-controls, never a profit claim.

| Change from HV4 | Reason |
|---|---|
| ONE persona "GEN" (CON and BIG merged; wider knob bounds) | HV4 split trades over CON/BIG/BST, so no arm gets a clean 15 trades/day. One deciding stream. |
| No lane H (no H5 trigger, no BST) | Lane H feeds the EXP-024 Look-2 seal; HV4 also dropped it in its N2. |
| Risk facts in code, no separate LLM risk call | HV4's risk LLM was 0.88 USD/day at cap; facts and hard vetoes are unchanged and go on the card. |
| Regime channel (section 2) | The new thing under test. |
| Weekly frozen releases (section 7) | The learning procedure is the unit under test. |
| Intraday outcome-blind regime review | The radar digest is daily; waiting for the 00:05Z review adds up to 17 h. |

## 1. Universe, scanner, cadence (deciding arm >= 15 trades/day)

**Universe = HV4 lane G, reused unmodified**: canonical PumpSwap pools of graduated pump.fun mints, WSOL quote, PRE-trade reserves plus V
(about 17.58 SOL). Filters are HV4 section 1 with the same values: age since first PumpSwap print **2 h to 7 d** (2-24 h capped at 0.25 SOL
and half the slots); real quote >= 30 SOL; last 15 min >= 20 prints, >= 8 signers, last print <= 120 s; last 60 min >= 10 SOL volume;
round-trip impact at stake <= 3.0% else the size drops, a breach at 0.25 SOL is a veto. Hard vetoes 1-7 of HV4 section 2 (authorities,
Token-2022 extensions, Mayhem < 24 h, V* >= 0.20, sell-route check, quote -30% in 15 min) fail closed. Direction facts are unchanged:
top10_frac and creator_sold_frac are inverted in the lab; holder_count is neutral; the prompt never calls concentration bad.

**Setups** (screens only; the families are dead as rules): PULLBACK, BREAKOUT, SECOND_WAVE as HV4. One setup per pool per 30 min.

**Cadence.** Scan each minute at :02. Board every 15 min: top 24 uncarded pools by 60-min volume plus every new setup. Every LLM arm gets
byte-identical cards (section 3). Entry calls: <= 4 cards per call, <= 12 calls/h/arm.

**Code caps (not the LLM's):** GEN opens <= 3 entries/h, <= 6 open, day loss stop -1.5 SOL (realized plus marked), 30 min cooldown per pool,
hard stop -40%, max hold 12 h. Knob bounds (reviewer moves inside them; v0 in brackets): stop -4..-30% (-8); target +6..+150% (+20); trail
20-40% after +50% (30); time 10-360 min (60); min_conviction 3-5 (3, lowered from HV4's 4 for volume).

**Trades/day (design target, NOT measured).** HV4's Aug count run (explore-0814, Aug 17-27): 156 pools pass per 5-min scan, 46 newly passing
per hour, setups/h PULLBACK 51, BREAKOUT 22, SECOND_WAVE 65. The repo says only "October is quieter"; I assume 40% of Aug, about 55 setups/h,
about 24 cards/h after ranking and cooldown, ~575 cards/day. A SKIP-default model taking 3-5% gives **L about 17-29 trades/day; planning
figure 20 (range 10-35).** X (top-ranked setups at a 2/h cap, max 48/day) about 36/day. The count-only shakedown (section 10, needs a QP
counts ruling for arena tape) replaces these guesses; if gated setups are < 25/day, widen the X cap and the board, never the age floor (the
age floor is also the EXP-024 seal guard, section 8). **Deciding arm = L.** If its rate is under 15/day the registration says so and the read
waits for 100 trades inside a 14-day window.

## 2. Regime channel (the new component)

**Principle.** Radar alerts reach the bot only as a pre-declared, machine-written block of numbers and enums (`hv5_regime.v1`), built by
frozen code (`tools/hv5_regime_block.py`, new) and placed in the reviewer's input. No human edits the prompt, block or template after any
outcome is seen; a human may only release a new version (section 7), which gets new bot_ids and a new clock.

**Source.** Only the P23 daily `digest-<date>.json` and `radar.jsonl` of the out-dir created at the merge of #900
(`/data/mal/change-radar-arena1`), read by a fixed-field extractor that rejects an unknown schema. Radar runs RPC stages plus arena counts
(`--venue-tape ''`, r6), so **no DBC/LaunchLab venue-tape field enters.** QP C4 of #900: radar output never decides or excludes an hour or
pauses a book unless that book's own pre-registered rule names it. HV5's registration names the radar as a **reviewer evidence input only**
(and the review trigger in section 2 item 2); the block never changes the scanner, a veto, a cap or a fill. This needs the QP ruling in section 10.

**Fields that enter** (per event, window = last 168 h at block time):

| Field | Type | Rule |
|---|---|---|
| `id` | `RG-` + first 8 hex of sha256(kind, program, change slot or day) | citation handle |
| `kind` | enum: program_upgrade, config_hash, new_key, gone_key, shift, share, monitor_halt, monitor_warn | radar W1/W3/W4/W5/W6/W7 |
| `sev` | HIGH / MED / LOW | the radar's own, not re-graded |
| `tier` | core / venue / router | from the radar watch file |
| `program` | enum of the 14 watch-file names, else `OTHER` | |
| `relevance` | `direct` if program in {pump, pumpswap, pump_fees} or kind is monitor_*; else `adjacent` | fixed code rule |
| `change_utc`, `hours_since` | time, int | W1 deploy time or the counted UTC day |
| `n_writes` | int or null | W2 |
| `ratio` | float or null | W5 rate / baseline, shift and share only |
| `key_class` | enum: swap, create, complete, pool_create, other | fixed mapping table from radar vocab |

Block summary: `n_high_core_7d`, `hours_since_last_high_direct`, `radar_status` (ok / partial / stale / absent; stale = digest > 36 h old).
**Excluded:** every free-text field (`detail`, `title`), account hashes, authority addresses, raw instruction/event names, signer data,
gap/hb/process counts (never counted, QP r2), venue-tape counts, any price, outcome or P&L. With no free text there is no injection surface
through the block. Same input bytes give the same block bytes (md5 test).

**How it is used (reviewer only; cards stay byte-identical across arms):**
1. **Nightly review** (00:05Z): the L and P reviewers get HV4's bundle plus one section "REGIME EVIDENCE". L-nr's bundle omits it. Nothing else differs.
2. **Intraday regime review**, code-triggered when the radar writes a new HIGH event with relevance `direct`: one call each for L and P with ONLY
   the block, current playbook/knobs and the change log. **No new outcomes** (the outcome bundle stays at its 00:01Z freeze). Cap 2/day. The new
   version is in force 30 min later. This turns the observed 29-35 h reaction into about 2 h after the digest.
3. New change type `regime_rule` beside playbook_edit / knob / local_veto, same schema, with `evidence_ids` of the form `RG-...`.

**Validator for `regime_rule` (frozen code).**
- Must cite >= 1 `RG-` id present in the block, else rejected.
- **Tightening is always allowed** (smaller size, tighter stop or time, a local veto on one setup type), TTL <= 72 h, then it expires unless re-earned.
- **Loosening only as a bounded probe:** stake fixed 0.25, <= 1 knob step inside bounds, <= 1 extra entry/h, TTL <= 72 h, tag `regime_probe` on every
  trade it enables. The 10-match outcome rule is waived for TTL changes only, since a new regime has no prior outcomes. After the TTL, a change
  needs the ordinary 10-match rule on post-change trades (probe trades included).
- Cannot touch: scanner, vetoes, risk facts, code limits, fills, costs, scorer, gate, stakes above 0.25, model, reviewer prompt, embargo, controls.
- At most 3 changes per review including regime ones; at most 2 regime-sourced local vetoes at a time.

**What the channel can and cannot show.** Pump-family HIGH events: 3 in about 3 weeks (10-12 fees, 10-13 pump, 10-19 pump/PumpSwap). A 14-day
window may hold 0-2, so L - L-nr **will probably be NOT_DECIDABLE**. The honest outputs are (a) process metrics: hours from radar event to the
first accepted `regime_rule`, rejects and why, trades carrying `regime_probe`; (b) the sign-flip test on regime days if >= 6 non-zero pairs exist.
A pre-launch **drill** (stub model, then a few real calls at the smoke) runs the channel on a synthetic block to prove plumbing, validator
and latency. The drill is a process check, not evidence of value.

## 3. Arms

One stream, one scanner, byte-identical cards at the same instant for all LLM arms. Only the prefix and the nonce sha256(arm|kind|card_sha|seq)
differ; arms never share a cached reply; a restart replays from cache.

| Arm | Definition | Promotable | LLM calls |
|---|---|---|---|
| **L** | GEN + nightly reviewer + regime channel | yes | entry, re-check, reviewer, regime review |
| **L-nr** | GEN + nightly reviewer, **no regime block**, no regime review | yes | entry, re-check, reviewer |
| **F** | GEN with playbook v0 frozen, no reviewer | yes | entry, re-check |
| **P** | L's procedure incl. the regime block; outcomes in the bundle shuffled (seeded sha256(arm\|date), HV4 tuple shuffle) | no | entry, re-check, reviewer, regime review |
| **X** | same scanner and vetoes; top-ranked setups at the 2/h cap; v0 default exit (-8/+20/60 min); no LLM | yes (rule book) | none |

Random control R is **not a live arm**: a 200-seed null replayed at the registered frozen sha after B (HV4 A1 item 7 pattern, manifest of
seed-file hashes, seed 1 must reproduce the live-style run). It costs no API money and removes one arm.

Contrasts, all at stake 0.25, on daily differences, exact one-sided sign-flip test of HV4 Amendment A1 (no day-block bootstrap):

| Question | Contrast | Needs |
|---|---|---|
| Does the LLM beat the rules? | L - X, F - X | >= 7 UTC days (A1 item 4); p <= 0.05/6 |
| Does learning add value? | L-nr - F, L-nr - P | >= 6 days, p <= 0.025, week-2 >= week-1 |
| Does the regime channel add value? | L - L-nr on regime days (72 h after each direct HIGH event) | >= 6 non-zero pairs, else NOT_DECIDABLE |
| Does outcome information matter, same regime treatment? | L - P | p <= 0.025 |

P also gets the real block so L - P isolates outcome information. A time-shifted-block placebo would be a sixth LLM arm and is not run; the
read reports how many days had a non-empty block and reads L - L-nr only on those.

## 4. Gate read (gate unchanged) and cells

Each promotable arm (L, L-nr, F, X) is one bot_id with one DEC-031 gate read at its outcome-blind instant B. The CLAUDE.md gate applies
unchanged, under both flat 15% fail and pressure-fail slope 1: >= 100 OOS trades; >= 5 UTC days with a majority positive; CI90 lower bound of
mean SOL/trade > 0 (1,000 draws, seed 1, 5th percentile); total positive after removing the top 3. Plus ex-best-day > 0 and a quant-proof
pass. Cells: 4 books x 3 stakes (0.25 primary, 0.5, 1.0) x 2 fail legs = 24, one read each, disclosed with every other arena book
(multiplicity c). Winner's curse applies to the best cell. A screen pass only earns the canary path (section 9) and then 100 more attempts on
5 more dates.

**Speed.** At L = 20/day, 100 trades arrive on day 5 and the 5-day rule binds; 7 closed UTC days (A1 item 4) give about 140 trades. **Earliest
gate read: B+24 h with B = end of day 7** (my proposal; the registration fixes B), so a 10-30T00Z clock reads 2026-11-07. X at about 36/day
clears 100 earlier but is read the same day.

**What the numbers must look like (arithmetic).** CI90 lower bound > 0 needs mean > 1.28 x sd / sqrt(n). With per-trade sd of net return
8-12% (assumption), n = 100 needs a mean of 1.0-1.5% of stake after all costs, n = 140 needs 0.9-1.3%: at 0.25 SOL, 0.0026-0.0038 SOL per
trade. A big-win/small-loss book must also survive removing its top 3.

## 5. Capacity line

Round-trip impact versus mid at both ends is about 2 x s / R_eff, R_eff = real quote + V. At the quote floor Q = 30: R_eff = 47.6 SOL; at
Q = 100: 117.6 SOL. Constant-product arithmetic, fees separate (fee floor about 2.4%, 3.52% with rent); the engine's own impact is the authority.

| Stake s | Impact at Q 30 | at Q 100 | Gross edge to break even (Q 30, floor 2.4%) |
|---|---|---|---|
| 0.25 | 1.05% | 0.43% | about 3.5% (4.6% with rent) |
| 0.5 | 2.10% | 0.85% | about 4.5% (5.6%) |
| 1.0 | 4.20% (breaches the 3.0% cap; size drops to about 0.7) | 1.70% | about 6.6% or more |

SOL/day = trades/day x stake x net edge:

| Trades/day | Stake | Net edge | SOL/day |
|---|---|---|---|
| 20 | 0.25 | +1% | 0.05 |
| 20 | 0.25 | +3% | 0.15 |
| 20 | 0.50 | +3% | 0.30 (impact is higher, so the edge is lower) |
| 30 | 0.25 | +5% | 0.375 |

At 0.25 SOL and 20 trades/day, a USD 20-50/day target needs a net edge far above the lab's priors. HV5 is a research-speed and
regime-reaction book first. Stake grows only after the confirmation and the DEC-032 C0 to C1 capacity re-price.

## 6. Budget (claude-haiku-5-5 only; HV4 section 6 price table)

Per MTok: input 0.10, cache read 0.01, cache write 0.125, output 0.50. Thinking off, no temperature; max_tokens 400 entry, 120 re-check,
4,000 reviewer. One 5-min cache marker on the system block. Refusal or parse failure = abstain.

| Call | cached / new / out tokens (assumed; measured at the smoke) | USD/call | Cap | USD/day at cap |
|---|---|---|---|---|
| Entry (<= 4 cards) | 2,500 / 3,200 / 300 | 0.000495 | 12/h x 4 arms | 0.57 |
| Re-check | 2,500 / 900 / 60 | 0.000145 | 24/h x 4 arms | 0.33 |
| Nightly reviewer (L, L-nr, P) | 0 / 60,000 / 4,000 | 0.0080 | 3 per night | 0.024 |
| Regime review (L, P; block + playbook only) | 0 / 8,000 / 1,200 | 0.0014 | 2/day x 2 | 0.006 |

Check: entry 2,500x0.01 + 3,200x0.10 + 300x0.50 per M = 0.000025 + 0.00032 + 0.00015 = 0.000495; re-check 0.000025 + 0.00009 + 0.00003 =
0.000145; reviewer 0.006 + 0.002 = 0.008. Total at call caps **0.93 USD/day**; expected 0.4-0.7.

**Hard caps** through `tools/haiku_caller.py`, HV5's own ledger per arm per UTC day: 0.50 per LLM arm, 0.15 reviewers, 0.05 regime review
(2.20), and a **global HV5 cap of 3.00 USD/day** (room for a second release, section 7). Drop order as HV4 (fewer cards per call, re-check
cadence x3, then halt entries), always hitting L, L-nr, F and P together. Shared pot: HV4 hard cap 4.70/day. Worst case 10-25 to 11-11: HV4
17 d x 4.70 = 80; HV5 from 10-30, 12 d x 3.00 = 36; total 116 of 200. HV5 does **not** read HV4's spend ledger (it would leak HV4 activity); the
manager sums the two daily and the owner's console cap is the backstop. Helius: HV5's own chain cache and a 1,000 calls/h cap; credits are
reported per job.

## 7. Self-improvement: weekly frozen releases

Nightly playbook edits stay. Beyond them, **release vN = one frozen bundle** (code sha, reviewer prompt, validator thresholds, bundle spec,
regime-block spec, playbook seed). Each release is forward-registered with its own bot_ids (`hv5.0-L`, `hv5.1-L`, ...), its own clock and its
own gate read. The learning procedure is the unit tested.

v5.(N+1) is produced by a frozen script `tools/hv5_release.py`:
1. Inputs are **process metrics only** from the previous release: invalid-reply rate, validator reject reasons, change acceptance rate,
   regime-event latency, token cost, block coverage. **No P&L, no arm outcome, no L-F gap**: outcome-driven edits are post-selection and
   would make the previous release's days in-sample for the next design.
2. It may change only a declared registry of procedure parameters (min-match 10 in {8, 10, 15}, bundle skip count, max changes/night, TTL <= 72 h,
   evidence window). Anything else is a human-written release PR with QP review.
3. Data-only releases (manifest, thresholds) need a manifest pin (about 24-36 h with the P24 pack generator). Any code change keeps the md5
   decision-equivalence replay. Mid-week restarts are recorded in LAB_STATE.
4. When v5.(N+1) starts, v5.N drops to F and X (plus L only until its own B read) so the 3.00 USD/day cap holds.
5. Cross-release claim ("the procedure improves"): pre-registered pooling of L - F per release, each release a trial in the multiplicity. With
   <= 3 releases by 11-11 it has **no power**. It gives operational improvement and candidate generation, not proof.

Risks: every release adds a trial and resets a clock, so the best release is a winner's-curse draw; reviewer edits can learn noise (v3), so P
stays in every release; frequent releases dilute 5-day gate windows; an author who looks at outcomes breaks independence (rule 1 forbids it);
two releases at once strain the API and chain budgets.

## 8. Seals (SEALS-BRIEF, line by line)

1. **HV4.** HV5 never opens HV4 ledgers, fills, prices, exits, positions, P&L, decision rows, bots.json, attempt counts, score/preview/am6_read
   output, or HV4 jobs #1561/#1562 status or log tail. HV5's runner, reviewer and readers use a `safe_path` denylist with tokens `hv4`, `haiku-v4`,
   `q50`, `decision`, `score`, `bots.json`, `ledger`, plus HV4's list (forward-*, truth-*, fresh-08*, exp011-*, tip-tape-archive,
   /var/lib/mal/sealed, shadow/canary/probe, meta-capture, blocks/forward-1016), and an import guard. HV5 writes only to
   `/data/mal/arena/haiku-v5/`. It reuses HV4 **code** (`tools/hv4_cards`, `hv4_chain`) by importing unmodified files; any change goes to an
   `hv5_*` copy, so HV4 (a frozen sha, one process per sha) is untouched. Nothing in HV5 is tuned on anything priced on or after 2026-10-25T00Z.
2. **Q50 / EXP-024 Look-2** (no H5 outcome on any October hour of a V-range pool in its first 360 s, from any source). Excluded **by
   construction**: the universe needs pool age >= 2 h, so no HV5 entry or outcome falls in a pool's first 360 s; no H5 trigger, no lane H, no BST;
   a hard code check refuses any card with pool age < 2 h at the scan instant; the reviewer bundle holds only HV5 trades; no classified H5 feed is read.
3. **DBC.** No venue tape, DBC row or fwdv1; the radar runs with `--venue-tape ''` so the block has no DBC/LaunchLab field. 10-17..10-24 is spent: nothing priced there.
4. **Arena tape.** HV5 consumes it only as the live arena feed in its forward window. Research reads of it are counts and creates only under a QP
   ruling (section 10). Nothing is joined to HV4/Q50 output before their reads. A rule priced on in-clock archive rows (>= 10-25T00Z) is in-sample,
   never OOS: v5.0 is not tuned on them and the shakedown output is counts.
5. **Ledger D2/D3 check** before pricing any October pool, tip tape included. The md5 replay uses ledger-free hours chosen by the manager from
   `docs/HOLDOUT_LEDGER.md`. I verified no hour here.
6. **Gate unchanged.** Paper only.

## 9. Live path: DEC-032 C0 canary, so nothing is missing later

DEC-032 is a template, not in force. For HV5 specifically:

| Need | Status / action |
|---|---|
| `frozen_sha` = executor code; any code or prompt change = new bot_id (DEC-032 s1.4) | **Conflict:** L's playbook changes nightly, so L cannot be a canary as is. Default: canary only a **frozen** arm (F, X, or a frozen snapshot of L registered as a new bot_id with its own forward clock). Counting a data-evolved playbook inside a frozen procedure as one bot_id needs a DEC amendment, a QP ruling and the owner; HV5 does not assume it. |
| Parity tool `arena_live_parity` (S5), tie-out tool, paper twin repriced at S = min(stake, 0.05) with the executor's actual per-send fees | Must exist and run dry before any canary. Not built here; the decision log carries the fields a twin needs (card sha, plan, size, decision_ms) so no LLM re-run is needed. |
| Worst-in-slot mean and CI90 lower bound (S4) | Quoted in the §2 paragraph; computed from the HV5 decision log. Scan-triggered, but the cohort rule applies: wis mean <= 0 is not proposed. |
| Executor for GEN | New: LLM-in-the-loop send. Hash-chained decision log; API outage or invalid reply = no buy, code-only exits; stale-card 10 s rule; landing p50 counted from the reply in hand (<= 1.9 s); sell builder for arbitrary PumpSwap pools; mint fetch and close-blocked count (Am.2 item 6). |
| Fail closed (DEC-032 s3) | committed-loss file, balance, twin, parity state, STOP/LIVE_OK/TIER and chain check, each tested in the executor. |
| Halts P1-P7 incl. A3/redeploy | HV5's A3-style guard names the radar's direct HIGH events as a **halt-for-review** in the registration (QP C4 allows it only if pre-registered). It triggers the regime review and a human look; it does not auto-stop paper. |
| C0 limits | stake <= 0.05, 2 open, 0.10 SOL daily stop, 0.20 total stop, wallet floor, owner's standing OK (Am.1 item 3). Fixed cost is 2.0% of stake at 505,000 lamports/send; impact at the deciding stake is not tested at S. |
| Keys | A canary host needs an Anthropic key on the executor host: a new placement the owner and Helm must approve. Nothing is done here. |
| Confirmation | 100 more attempts on 5 more dates, p <= 0.05/c, independent of the canary; labelled "unpromoted execution measurement". |

## 10. What must be built, by whom, in what order, earliest clock

Assumes acceptance today. A day of slip moves the clock a day (clock = first 00:00Z after the record).

| # | Item | Who | Depends on | Done by |
|---|---|---|---|---|
| 0 | QP design ruling: radar as reviewer-evidence input (C4), arena-tape counts ruling for the shakedown, `regime_rule` validator, 24 cells, section 8 | manager + quant-proof | this PR | 10-26 |
| 1 | `tools/hv5_cards.py`: GEN scanner config over unmodified hv4 cards, 2 h age guard, byte-identity test | builder (Sonnet) | 0 | 10-27 |
| 2 | `tools/hv5_agents.py`: GEN playbook v0, templates, validators incl. `regime_rule`, per-arm ledgers, 3.00 global cap, StubModel | builder | 0 | 10-27 |
| 3 | `tools/hv5_regime_block.py`: fixed-field extractor, enum allowlists, staleness, md5 determinism, injection and no-free-text tests, path guard | builder (can start now) | 0 | 10-27 |
| 4 | `tools/hv5_runner.py`: 5 arms, code exits, X, log chain, import guard, intraday regime trigger, resumable MiScusi job | builder | 1-3 | 10-28 |
| 5 | `tools/hv5_review.py` and `hv5_release.py`: bundle, placebo shuffle, manifest chain, release registry | builder | 2, 3 | 10-28 |
| 6 | md5 decision-equivalence replay on ledger-free hours; drill with a synthetic block | manager (MiScusi job) | 4, 5 | 10-28 |
| 7 | Real-model smoke (Am.6, no 24 h stub): small spend; measures tokens, latency, scan p99, memory | manager | 6 | 10-29 |
| 8 | Registration pack via `tools/fasttrack_book.py` (manifests, pins at the frozen sha, v0 manifests, cap and seal pins); QP OK on the exact head; reviewer verdict on the PR | manager + QP + reviewer | 7 | 10-29 |
| 9 | **Clock T0** | manager | 8 | **2026-10-30T00:00Z** (fallbacks 10-31, 11-01) |
| 10 | `tools/hv5_read.py`: sign-flip, V1/V2, 24 cells, regime-day table, R-null manifest, result.v1, Console rows | builder | needed before B+24 h, not the clock | 11-05 |
| 11 | Live readiness: parity tool S5, tie-out, GEN executor spec | builder + manager | after a screen pass | later |

Owner: the HV5 spend line (<= 3.00 USD/day inside the shared 200 USD to 11-11). The Anthropic key placement only if a canary is ever asked for.
Manager checks before registering: LAB_STATE for any kill-review week that forbids new forward books; the ledger for the replay hours.
First gate read 2026-11-07T00Z (10-30 clock); learning and regime reads at D14, 2026-11-13. HV4 is never touched and never read.

## 11. Biggest risks

1. **No edge may exist.** Every measured universe loses before selection and v3 learning was about placebo. The good outcome may be a fast, clean FAIL or NOT_DECIDABLE.
2. **The regime channel is untestable in one window** (0-2 events), and the canary path conflicts with a nightly-changing playbook (section 9).
3. **Contamination and overlap.** HV4 and HV5 trade the same pools while HV4 outcomes are sealed; one accidental HV4 read, or one threshold tuned on in-clock data, makes HV5 in-sample.

**Nothing in this document says the trader makes money.**
