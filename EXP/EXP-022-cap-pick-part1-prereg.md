# EXP-022 Part 1: CAP-PICK walk-2 read, pre-registration

**Written and merged before the first counted hour.** No row under `/data/mal`, no forward-walk file and no runner file was opened to write this file. It is docs only. The read tool, the pick-set replay and the integrity code are separate PRs (sections 2.1, 10 and 12). Nothing in this file says or implies that any book is positive. A fail is the expected outcome (section 14).

| Field | Value |
| --- | --- |
| **ID** | `EXP-022-cap-pick-part1-prereg` |
| **Status** | **planned**. Counting starts at the `EXP022_COUNT_START` line in section 0, which the manager sets at merge. |
| **Declared (UTC)** | 2026-10-08 |
| **Owner decision** | The owner approved the audit recommendations on 2026-10-08, including O2 and O3 (MiScusi notebook `n_vS9qHGmF7-jinQ`; `ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md:457-458`). O2 is [DEC-021 Amendment 1](../DEC/DEC-021-champion-challenger.md), in this PR. O3 is the early count (Option X), which needs this file merged with quant-proof OK before 2026-10-10T00:00Z. |
| **Parent** | The 10-08 audit: `capv_JUDGE.md` §4 items 1–10 (binding) and §5; `SYNTHESIS.md` A1–A3, A8, A9, A11, A12 and D5. [EXP-012](EXP-012-migrate-entry-model-refreeze-prereg.md) (the frozen selector). [DEC-021](../DEC/DEC-021-champion-challenger.md) Amendment 1. [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md). [DEC-016](../DEC/DEC-016-exp012-forward-on-chain-hours.md) Am.2, Am.3 and Am.6. [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md). |
| **Hypothesis** | The CAP-PICK book has mean SOL per attempt > 0 at a 0.1 SOL stake on post-upgrade October chain hours, under both fail models, at a reachable latency. The book is: frozen EXP-012 picks as the live gate makes them, an on-chain min_out at seed × 1.15, a 300 s wall-clock exit, and 55,000 lamports per send. |
| **Kill condition** | No look passes (section 7) by day 21; or a structure halt (section 11); or a precondition fails (section 10). One read with three looks. No retune, no re-read. |
| **Expected outcome** | **FAIL.** P(pass) is about 5–7% with the day-level p (section 14). A fail leads to A12, the cost wind-down, which the owner has pre-agreed. |
| **Tools** | **Read:** a sealed read mode of `tools/cap_pick_score.py` (draft #461), which must merge with quant-proof before the day-7 look (section 12). **Picks:** `tools/cap_pick_gate_replay.py` (draft #462). **Halt:** `tools/pump_structure_monitor.py` (#456). **Calibration:** `tools/probe_sim_calibration.py` (#463). |

**Labels.**
- **[measured]**: copied from a cited file that computed it. Simulations on exploration rows are measured arithmetic, not evidence of edge.
- **[inferred]**: reasoned, by the cited author or here.
- **[pinned]**: a design value this file fixes. Not evidence.

Draft-PR code is cited at a commit: `cap_pick_score.py@ebb77f4` (#461) and `cap_pick_gate_replay.py@8fd49e5` (#462). `JUDGE` is `ARTIFACTS/lab/audit-2026-10-08/capv_JUDGE.md`, and `SYN` is `ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md`.

## 0. Counting start (the one pinned line)

```
EXP022_COUNT_START: PENDING
```

- **Who sets it.** The manager replaces `PENDING` in a commit on this branch before merging. There are only two allowed values:
  - `2026-10-10T00` (Option X);
  - `2026-10-16T01` (Option Y).
- **When X is allowed.** `2026-10-10T00` is allowed only if this file merges, with quant-proof OK on its final head, before 2026-10-10T00:00Z. Otherwise the value is `2026-10-16T01` (JUDGE:230; SYN:251, :529-532).
- **While it reads `PENDING`,** nothing counts and nothing is read. The read tool refuses unless:
  - the line appears exactly once, with an allowed value;
  - this file is clean against HEAD.
- **It is never changed after merge.** Everything below follows from it.

| | Option X (`2026-10-10T00`) | Option Y (`2026-10-16T01`) |
| --- | --- | --- |
| Counted window | `[2026-10-10T00, 2026-10-31T00)` | `[2026-10-16T01, 2026-11-06T01)` |
| Part on forward-1002 (walk #382) | `[2026-10-10T00, 2026-10-16T01)`. EXP-022 is the second owner (O3 ledger exception), and these hours stay sealed for it until the 10-16 FINAL is written. | none |
| Part on walk 2 | `[2026-10-16T01, 2026-10-31T00)` | the whole window |
| Data at looks 1 / 2 / 3 (days 7 / 14 / 21) | `[start, 2026-10-17T00)`, `[start, 2026-10-24T00)`, `[start, 2026-10-31T00)` | `[start, 2026-10-23T01)`, `[start, 2026-10-30T01)`, `[start, 2026-11-06T01)` |
| UTC dates at looks 1 / 2 / 3 | 7 / 14 / 21 | 8 / 15 / 22 (the first and last dates are partial) |
| A11 October check | not run (SYN:257) | report-only, binding for spending only (section 7.5). It needs its own DEC-016 amendment, merged before the FINAL. |
| E1 calibration runs after | the FINAL is written | the FINAL is written and the A11 read is done (`docs/HANDOFF.md:93`) |

Option X includes hour `2026-10-16T00`, because its window is continuous. O3's text says `[10-10, 10-16)` (SYN:458). The ledger edit names `[2026-10-10T00, 2026-10-16T01)`.

## 1. The book: every parameter, in ms and SOL

| Item | Value | Source | Label |
| --- | --- | --- | --- |
| Selection | The frozen EXP-012 model `ARTIFACTS/exp012/model.txt` (md5 `a1810d219ed61db64a396f40dc302ce5`) at threshold 0.8030766588450794, with features computed as the live gate computes them (section 2) | `EXP/EXP-012-migrate-entry-model-refreeze-prereg.md:183-184`; `ARTIFACTS/exp012/FROZEN.md5:2`; JUDGE:177-181 | [pinned] |
| Universe | Canonical PumpSwap pool, WSOL quote, non-mayhem, V0 in [17,500,000,000, 17,700,000,000] lamports, one attempt per mint (section 3) | SYN:271 | [pinned] |
| Entry | 1,300 ms after the pool-create slot. k = ceil(1,300 / that hour's ms per slot). END bound. | JUDGE:193-197; SYN:273 | [pinned] |
| Binding latency leg | 1,900 ms, same rule | JUDGE:194 | [pinned] |
| Guard | min_out at seed × 1.15, gross basis, floored to base units | JUDGE:202-206 | [pinned] |
| Exit | tp +50% / sl −30% on post-trade spot against the post-buy mark. A 300 s wall-clock cap from the landing print's block_time, sold by a timer. | SYN:275; JUDGE:207-209 | [pinned] |
| Exit lag | 550 ms primary, as ceil to slots per hour, on every sell including the deadline sell. 1,350 ms is report-only. | JUDGE:198-201 | [pinned] |
| Stake | 0.1 SOL (100,000,000 lamports) | SYN:260, :277 | [pinned] |
| Cost per send | 55,000 lamports | SYN:276 | [pinned] |
| Rent | 0 primary; 2,039,280 lamports per fill report-only | JUDGE:215 | [pinned] |
| Fail legs | flat 15% and pressure at slope scale 1, both binding; live 1/62 report-only | SYN:278; CLAUDE.md promotion gate | [pinned] |
| Statistic | Mean SOL per attempt | JUDGE:211 | [pinned] |

## 2. Pick set: the live gate, replayed on the walk tape

The read's picks are the mints that the live EXP-012 gate decides `pick` on the walk tape (JUDGE:177-181). The gate is `Exp012Online` (`tools/forward_exp012_gate.py`) with `ForwardEngine._exp012_pass` (`tools/forward_paper.py`), applied by `tools/cap_pick_gate_replay.py`. The replay reuses the runner's own functions; it does not copy them (`cap_pick_gate_replay.py@8fd49e5:1-22`). It behaves as the runner does:
- **Truncation.** Feature recording stops exactly 32 min after the create (module note 4, `tools/forward_exp012_gate.py:35-41`).
- **60-minute skip.** A mint that migrates more than 60 min after its create gets `no_features` (note 5, `:42-44`; `DROP_AFTER_CREATE_MS`, `:82`).
- **Daily restart at 00:00Z.** State is dropped and creator history is rebuilt from the creates of the previous and current UTC day. A mint created before the restart gets no decision (note 7, `:48-59`; note 8, `:60`). The replay boots at 00:00Z on every UTC day of the window, the first day included.
- **Receive time.** The walk tape has no receive time, so `t_recv_ms` is taken as `block_time × 1000`, a zero receive lag (`cap_pick_gate_replay.py@8fd49e5:55`; disclosure 11).

Only `pick` decisions can be attempts. `below`, `no_features`, `no_bond_history` and dead (pre-restart) mints are not attempts.

**Exploration figures, restated on the live-tradable set** [measured, JUDGE:58-60]. These are the live leg at 0.5 SOL, on the exploration blocks the book's design was chosen on (JUDGE:16). They are in-sample for the book, and they are not evidence of edge.

| Scope | Mean per attempt | Date-cluster CI90 lower bound | Days positive | Ex-best-day |
| --- | ---: | ---: | ---: | ---: |
| P2–P4 | +3.492% | +1.50 | 20/28 | +27.60 SOL |
| P1 | +1.419% | −0.96 | 5/7 | −0.29 SOL |
| oracle | −0.985% | −2.69 | 2/4 | — |

The all-picks figure, +3.819% (JUDGE:58), includes the 188 P2–P4 picks (8.0%) that the live gate cannot trade. It is never used as the effect size.

### 2.1 E0: md5 decision-equivalence, before the first counted hour

Before `EXP022_COUNT_START` begins in real time, a dated amendment to this file records all four items below:

1. **Replay span.** At least one full UTC day of exploration tape, pinned by its `VIEW.sha256`. Never forward-walk or walk-2 data.
2. **Runner equivalence.**
   - A = the runner's own replay path, `tools/forward_paper.py` `replay_rows` (`:2848`), with the frozen gated book, on that span.
   - B = `tools/cap_pick_gate_replay.py replay` on the same span.
   - Each side gives a canonical decision list: one line per mint, sorted by mint, `mint<TAB>decision<TAB>mig_ms<TAB>repr(score)` (an empty score when there is none).
   - md5(A) must equal md5(B).
3. **Scorer equivalence.** C = the mint list that `tools/cap_pick_score.py --book picks` counts as attempts when given B's decisions on that span. C must equal, by md5, B's `pick` mints restricted to the section 3 universe.
4. **Code pins.** The git blob shas of `tools/forward_exp012_gate.py`, `tools/forward_paper.py`, `tools/exploration_entry_model.py` and `tools/cap_pick_gate_replay.py`, and the md5 of `ARTIFACTS/exp012/FROZEN.md5` (`a01f05dfb1e622f78b2bba55d174be09`, `EXP/EXP-012-migrate-entry-model-refreeze-prereg.md:183`). Every look refuses if any of them differs.

**If E0 is missing, or any md5 differs,** CAP-PICK is withdrawn before counting (JUDGE:251). Nothing is fixed and retried after the window starts.

## 3. Universe

One attempt per mint. A mint is an attempt only if all of these hold:
- the gate decided `pick` (section 2);
- its canonical pool is the pool its migrate tx created: the `pool` field of its `migration` event (`tools/pump_history_backfill.py:275-292`);
- the quote mint is WSOL: `quote_mint` on that event (`:292`);
- it is non-mayhem: `is_mayhem_mode` is false on its create event (`:233-255`);
- V0, as defined below, is in [17,500,000,000, 17,700,000,000] lamports (SYN:271; `cap_pick_score.py@ebb77f4:116`);
- the block_time of s0 (the decision print, defined in section 4.1) is in the counted window;
- s0's block_time is at most 80 s after the block_time of the mint's `complete` event. This restates G's "s0 − complete slot ≤ 400 slots" (`cap_pick_score.py@ebb77f4:115`) in ms, at 200 ms. The count of mints this excludes is reported.

**Synthetic-migration pools** that meet these conditions are attempts. If one opens above the guard, it is a reject at −55,000 lamports (section 4.3). It is never dropped.

**InitBoost is not applied.** SYN:271 lists it, but the walk tape cannot tell, at decision time, whether the migrate tx carried InitBoost:
- the walker decodes the create, complete and migration events and trades, but not the InitBoost instruction (`tools/pump_history_backfill.py:207-292`);
- the A8 decoder list does not add it (SYN:389).

BOOST health is covered instead by the A3 monitor's `boost_share_low` rule, which halts when InitBoost is on fewer than 80% of sampled non-mayhem WSOL graduations (`tools/pump_structure_monitor.py:113`, `:1060-1072`). Pools without BOOST stay in the book. In exploration, BOOST was detected on 94–99% of V>0 graduations [measured, single investigator; SYN:50].

**V0: the pool's V at decision time.** This is the value a pool-account read would return at the decision print.
- **Walk-2 hours:** V0 comes from the A8 event-V decoder (SYN:389). It is the V in force at the decision print, with the decoder's pre-/post-trade convention fixed by its fixture tests.
- **Forward-1002 hours (Option X only):** V0 comes from a pool → V0 map built by `tools/exp012_forward_vmap.py`. In that map V0 is constant except at LP events (`tools/exp012_forward_vmap.py:15-17`).
  - It is built after the FINAL is written, from the canonical pools of the counted forward-1002 hours. It may reuse the FINAL's merged map where that map covers the pool.
  - A dated amendment pins its sha256 before the day-7 look.
  - The 10-08 synthesis (SYN:259, D12) calls for the V map on pre-10-16 hours.
- **Pricing.** PumpSwap tape rows carry pre-trade reserves, and price = (quote vault + V) / base (`cap_pick_score.py@ebb77f4:67-73`; `docs/HANDOFF.md:71`). The path is priced with V0 held constant over the hold.
- **Disclosed.** Per-trade V since 09-30: 1.2% of trades moved V on 10-04..10-08, with at most 1.22 bp of drift in a fresh pool's first 30 min [measured, single investigator; SYN:105].

## 4. Execution (paper)

### 4.1 Entry

- **s0** is the slot of the canonical pool's first PumpSwap print: the pool-create slot. The leakage verifier measured s0 − migrate slot at p50 0 and p99 2 [measured, JUDGE:196].
- **Hour h** is the UTC hour that holds the attempt's `complete` event (`cap_pick_score.py@ebb77f4:24`).
- **ms_per_slot(h)** is measured on the walk tape, from the block_time of the hour's trade rows (`cap_pick_score.py@ebb77f4:23-31`).
  - The read uses no day-table fallback.
  - If the rows cannot measure the hour, it uses 3,600,000 / the hour's sealed slot span from its verify line.
  - If neither exists, the hour is bad (section 10).
- **k** = ceil(1,300 / ms_per_slot(h) − 1e-9) slots.
- **Landing slot** X = s0 + k, END bound: the state after every print in slot X.
- **Examples** [arithmetic]: 1.3 s is k = 7 at 200 ms, and 1.9 s is k = 10.
- **Binding latency leg.** The same entry at 1,900 ms (section 7.2 (vii)).

### 4.2 Exit

- **Triggers.** tp and sl are checked on every print after the landing and before the deadline (`cap_pick_score.py@ebb77f4:17-18`).
- **Exit lag** L = ceil(550 / ms_per_slot(h) − 1e-9) slots, which is 3 at 200 ms.
- **Fill.** A triggered sell fills at the state before the first print whose slot is ≥ trigger slot + L + 1 (END).
- **Deadline sell.** The same lag applies to the deadline sell.
- **Report-only lag.** 1,350 ms (7 slots at 200 ms).
- **Source of the lag values.** 550 ms and 1,350 ms are about the lab's lag 2 and lag 5 at 267 ms (`ARTIFACTS/lab/probe-final-2026-10-07.md:198`; JUDGE:199) [inferred].

### 4.3 Guard (gross, as the executor would send it)

- **Seed price.** seed_p = (67,405,853,863 + V0) / 206,900,000,000,000 (`cap_pick_score.py@ebb77f4:34`, `:110-111`).
- **min_out** = floor(100,000,000 / (1.15 × seed_p)) base units.
- **tokens_out** = floor(B × net / (Q + V0 + net)) at the landing state.
  - net = 100,000,000 × (1 − f).
  - f is the pool fee tier at landing, from `PUMPSWAP_SOL_FEE_TIERS` on (Q + V0, B): 125 bps per leg below 420 SOL mcap (`docs/HANDOFF.md:72`).
- **Rule.** The buy executes iff tokens_out ≥ min_out. Otherwise it is a reject at −55,000 lamports.
- **Code gap.** The #461 draft computes the gross min_out with ceil (`cap_pick_score.py@ebb77f4:37`, `:406`). The read mode must use floor, as pinned here.
- **Executor, out of scope.** Today the executor takes min_out from its fresh quote (`tools/probe_executor.py:988-1007`, `:1210`, `:1224`). Replacing that with this formula needs md5 equivalence before any live use (JUDGE:206).

### 4.4 Cap

- **Deadline.** The deadline print is the first print whose block_time ≥ the landing block_time + 300 s. The landing block_time is that of the last print at slot ≤ X (`cap_pick_score.py@ebb77f4:42-48`).
- **Deadline sell.** It fills at the first print with slot ≥ D + L + 1. Live, it is a pre-signed timer.
- **No day-mean fallback.** A counted attempt whose path has no usable block_time is a refusal. Every walk row carries block_time (`tools/pump_history_backfill.py:352`).

### 4.5 Costs, rent, stake

- **Costs.** 55,000 lamports per send (`cap_pick_score.py@ebb77f4:189`). A filled round trip pays two sends, and a reject pays one.
- **Rent, primary 0.** The token-account rent is refunded when the sell closes the account (`tools/probe_executor.py:1304`). The probe measured 1,513,840 lamports charged and 1,513,840 refunded, net 0, over 61 round trips [measured, `ARTIFACTS/lab/probe-final-2026-10-07.md:53`].
- **Rent, report-only "always" leg.** 2,039,280 lamports per fill, the lab constant (JUDGE:215).
- **Stake.** 0.1 SOL. The 0.05, 0.25 and 0.5 SOL lines are report-only projections (SYN:279).

## 5. Fail legs and pressure

- **Flat 15%.** A filled attempt is 0.85 × pnl + 0.15 × (−55,000) (`cap_pick_score.py@ebb77f4:468`).
- **Pressure, slope scale 1.**
  - p = sigmoid(c + 0.8 × log1p(same_slot_buys) + 0.35 × log1p(nearby SOL)) (`tools/latency_curve.py:39-40`; `DEC/DEC-016-exp012-forward-on-chain-hours.md:341`).
  - The intercept c is fitted by `tools.latency_curve.fit_curve` so that mean p = 0.289 over the read's own fills of that cell (`tools/latency_curve.py:41`, `:90-94`). It is fitted separately for every cell (1.3 s, 1.9 s, and each report-only leg) and at every look.
  - same_slot_buys is the number of buys in the landing slot.
  - nearby is the buy lamports in the 2 s up to and including the landing slot. 2 s is converted to slots with the hour's ms per slot (`cap_pick_score.py@ebb77f4:395`).
- **Live 1/62.** Report-only. It was measured at 500k priority, not 55k (JUDGE:225).
- **Guard rejects** are −55,000 on every leg and are not mixed.
- **Disclosed.** Shorter slots mean fewer same-slot buys (DEC-016:341). Because the intercept is refitted to mean p = 0.289 at each look, only the cross-section of p moves.

## 6. Unit and accounting

- **Attempt.** A mint in the universe with a `pick` (sections 2–3). It is one row: either a filled round trip, or a guard reject at −55,000. Unscored or skipped mints are not attempts (JUDGE:211).
- **Deciding statistic.** Mean SOL per attempt at the 0.1 SOL stake, after the section 8 adjustment.
- **Fills.** Attempts that passed the guard. The ≥ 100 count is fills (JUDGE:212). The per-fill mean is reported.
- **Dates.** The UTC date of the block_time of s0.

## 7. Looks and the decision rule

### 7.1 Looks

There are three looks, at days 7, 14 and 21 of the counted window, each on cumulative data (section 0). Each runs once, and only when all of these hold:
- (a) every hour of `[start, look end + 1 h)` is sealed, verified and checked (section 10);
- (b) E1 is recorded (section 8);
- (c) the read tool is merged (section 12);
- (d) no halt has fired (section 11).

**A late look.** A look whose conditions are not met before the next look's data end is NOT_DECIDABLE (not a pass). For look 3, the limit is 7 days after the window end.

### 7.2 Pass at look L

The deciding cell is section 1's book. A look passes iff every condition below holds under **both** the flat 15% and the pressure scale-1 legs:

- (i) n_fills ≥ 100;
- (ii) at least 5 distinct UTC dates, with a majority positive (daily total SOL > 0);
- (iii) the lower 90% CI bound of mean SOL per attempt is > 0 (1,000 bootstrap draws over attempts, seed 1, 5th percentile);
- (iv) total SOL is > 0 after removing the top 3 attempts;
- (v) total SOL is > 0 after removing the best UTC date. **Binding** (JUDGE:213);
- (vi) the **day-level p** is ≤ α_L:
  - it is a one-sided t on the UTC-date means of SOL per attempt: t = mean of date means / (sd / √W), with W − 1 df;
  - α is 0.005 at day 7, 0.008 at day 14 and 0.012 at day 21 (SYN:252, :280);
  - the larger of the flat and pressure p decides;
- (vii) the **1.9 s leg** has mean SOL per attempt > 0 and total SOL ex-top-3 > 0. **Binding** (JUDGE:194).

Items (i)–(iv) are CLAUDE.md's promotion gate, unchanged.

### 7.3 Reported at every look, never deciding

The trade-level one-sided bootstrap p: the share of 10,000 seed-1 bootstrap means ≤ 0, under both legs (DEC-021:57; JUDGE:192).

### 7.4 Outcome

- **PASS.** The read ends at the first look that passes.
- **FAIL.** No look passes. The result is FAIL after look 3.
- **NOT_DECIDABLE look.** It is not a pass. The read continues to the next look.
- **Halt.** See section 11.

### 7.5 Futility (binding for spending only)

- **Day 7.** If the flat mean ≤ 0 and the pressure mean ≤ 0 at the primary cell:
  - A5, A6 and A7 spending pauses;
  - the owner is told that failure is likely;
  - the read continues (SYN:252, :427; JUDGE:248).
- **Option Y only.** The A11 rule applies too: the flat and pressure means ≤ 0 and ex-best-day ≤ 0 on `[2026-10-06, 2026-10-16)` (SYN:423). This applies only if a DEC-016 amendment registers A11 before the FINAL.

### 7.6 Family α

0.005 + 0.008 + 0.012 = 0.025, which is DEC-021's family α at k = 1 (DEC-021 Am.1 §5). The Bonferroni bound holds whatever the futility rule does.

## 8. DEC-021 §4 calibration (named set: faa3192 via E1)

- **Named set.** Build faa3192's closed round trips: 33 trips [measured, `ARTIFACTS/lab/probe-final-2026-10-07.md:42`]. They are priced by E1: `tools/probe_sim_calibration.py` at #463 (`c745411`), the reserve-convention patch, on the tip tape of those trips.
- **r̄** = mean (live − sim) lamports per closed trip, at the probe's 0.05 SOL stake.
- **Applied.** Every filled attempt in every binding cell (1.3 s and 1.9 s) gets pnl += min(0, 2 × r̄) before the fail mix. The 2 scales linearly from 0.05 to 0.1 SOL. If r̄ ≥ 0, there is no adjustment (DEC-021:54; SYN:282).
- **Timing.** E1 runs after the 10-16 FINAL is written, because of DEC-016's seal extension (`DEC/DEC-016-exp012-forward-on-chain-hours.md:162`). Under Option Y it also runs after the A11 read (`docs/HANDOFF.md:93`).
- **Record.** Its job id, commit, fills sha256 and r̄ go in a dated amendment before the first look. No look runs without it.
- **Context.**
  - On 2026-10-06 the faa3192 set was n = 8, mean −384,022 and median −209,670 lamports [measured, DEC-021:54].
  - The audit's corrected transfer estimate of live − sim is +0.84% [measured, single investigator; SYN:282], with CI90 −1.37% to +3.20% (`docs/HANDOFF.md:92`).
  - faa3192 traded a different book: a 30-minute hold, a fresh-quote min_out and 500k priority (SYN:217). So r̄ carries over to CAP-PICK by assumption [inferred].

## 9. Data, ledger and seal

- **Hours.**
  - Forward-1002 (`/data/mal/blocks/forward-1002`, MiScusi job #382 on `2bd45f1`; `docs/HANDOFF.md:51`), Option X only.
  - Walk 2 (`/data/mal/blocks/forward-1016`, planned; ledger row "Forward walk 2").
  - Every opened hour needs a `backfill_verify --content` OK line and a sha256 line (DEC-016:26).
- **Ledger** (this PR).
  - Forward walk 2: owner EXP-022.
  - Forward walk: the Option X second-owner exception and the feature reads, disclosed in [EXP-012 Amendment 2](EXP-012-migrate-entry-model-refreeze-prereg.md) under DEC-014(a).
- **Option X second owner.** Nothing reads forward-1002 hours for CAP-PICK before the 10-16 FINAL is written. DEC-016 Am.2 and Am.3 are unchanged.
- **Feature reads, no outcome.**
  - Option X: the creates of `[2026-10-09T00, 2026-10-10T00)` (forward-1002), for creator history at the first boot.
  - Option Y: within the already disclosed walk-2 buffer `[2026-10-14T01, 2026-10-16T01)` (DEC-021 §3; EXP-012 Amendment 1).
- **Seal between looks.** No person, agent or job does any of these, except the sealed look itself:
  - opens a CAP-PICK outcome row of a counted hour;
  - prints a P&L, fill, exit, reject count, mean, CI or day sign for one;
  - prices the counted picks from any source.

  Monitoring may print only hour counts (sealed, verified, bad) and gate decision counts. A breach is recorded here, dated, and the read is reported compromised; it cannot then support a live request. This follows DEC-016 Am.2 (`DEC/DEC-016-exp012-forward-on-chain-hours.md:85-94`).

## 10. Preconditions and integrity

**Before the first counted hour.** If any of these is not met, CAP-PICK is withdrawn before counting (JUDGE:251):
- **P0.** This file is merged with quant-proof OK, and the section 0 line is set.
- **P1.** E0 is recorded with equal md5s (section 2.1).
- **P2.** The A3 monitor's last daily run before the first counted hour has no halt, and no core rule went unevaluated on two consecutive days (section 11).

**Before 2026-10-16T01 (both options).**
- **P3.** The A8 integrity items are merged, and the walk-2 job starts on a commit that has them (SYN:390-392):
  - the walker's `JsonlSink` refuses to resume when the file is shorter than its checkpoint;
  - the gate readers refuse bad lines;
  - the scorer has per-hour slot_ms.
- If P3 fails, CAP-PICK is withdrawn. Under Option X this happens before any outcome is read.

**Before the first look** (a look without them does not run):
- **P4.** The read tool is merged (section 12).
- **P5.** E1 is recorded (section 8).
- **P6.** Option X only: the forward-1002 V0 map is pinned (section 3).
- **P7.** The A2 kill check has passed (SYN:292-308). It runs on exploration rows only:
  - books: the live-tradable picks from the #462 full replay, scored by the #461 scorer with every section 4–5 parameter at its pinned value;
  - success: the P2–P4 flat mean ≥ +1.0% per attempt, with the date-cluster CI90 lower bound > 0 on the flat leg and mean > 0 at 1.9 s (SYN:307);
  - P1 and oracle are reported lines (JUDGE:228);
  - a kill withdraws CAP-PICK (SYN:308) with no walk outcome read.

**At every look:**
- **Bad lines.** The readers refuse bad lines (a NUL byte, or a line that is not a JSON object), and the hour that holds one is bad. Today #461 counts bad lines and #462 skips them (`cap_pick_score.py@ebb77f4:780`; `cap_pick_gate_replay.py@8fd49e5:339-341`). The read mode must refuse.
- **Re-walks.** A bad or unverified walk-2 hour may be re-walked once, outcome-blind, before the look. Forward-1002 hours are not re-walked, because they are EXP-012's FINAL inputs.
- **Attempt coverage.** An attempt counts only if every hour from its mint's create hour to its exit hour is good. Excluded attempts are counted and reported.
- **Bad-hour ceiling.** If more than 5% of the look's window hours are bad, the look is NOT_DECIDABLE.
- **V0 ceiling (Option X).** Pools without a V0 in the pinned map are excluded, outcome-blind. If they exceed 1% of the canonical pools of counted forward-1002 hours, the look is NOT_DECIDABLE.
- **Decision stability.** Each look re-derives every decision from the window start. For hours an earlier look read, the decisions must match that look's record by md5, or the look refuses.

## 11. Structure halt (A3)

- **Source.** The daily `tools/pump_structure_monitor.py` run at 06:41Z (`docs/HANDOFF.md:33-37`). It has six halt flags (`tools/pump_structure_monitor.py:1027-1130`):
  - `pins_changed`;
  - `boost_disabled`;
  - `boost_share_low` (below 0.80);
  - `boost_last_slice_early` (median below 315 s);
  - `boost_budget_or_slices_changed` (more than 20% from the pins: 17.585 SOL and 29 slices);
  - `synthetic_share_high` (above 0.35).

  Thresholds: `:113-116`. Pins: `tools/pump_structure_pins.json:41-42`.
- **Also a halt.** A core rule (`boost_share_low` or `boost_last_slice_early`, `docs/HANDOFF.md:36`) that is not evaluated on two consecutive daily runs. A day with no run counts as not evaluated.
- **Before counting.** A halt withdraws CAP-PICK (JUDGE:251).
- **After counting starts:**
  - counting and any live trading are suspended the same day;
  - the trigger hour is the UTC hour in which the halting run started. For a `pins_changed` program redeploy, it is the hour of the new deploy slot if that is earlier;
  - every hour at or after the trigger is excluded;
  - no later look runs. The read is NOT_DECIDABLE unless it had already passed;
  - there is no retune, no re-read, and no resumption if the monitor later clears (SYN:281, :325, :552);
  - the halt leads to A12 (SYN:427).
- **Not adopted for the counted read:** LAB_STATE.md:235's "until BOOST is checked by hand".
- **Measured** [`docs/HANDOFF.md:39`]. The first run (MiScusi job #383) had no halt, InitBoost on 12/12, a median last slice of 341.5 s and a budget of 17.586 SOL.
- **Disclosed.** The monitor cannot see a slow erosion of the BOOST-end effect; only the read can (JUDGE:84).

## 12. Read tool (precondition for the first look)

The read tool is a sealed read mode of `tools/cap_pick_score.py`. It must merge with quant-proof OK before the day-7 look. Until then, no CAP-PICK outcome of a counted hour is read. It must:
- refuse unless:
  - this file is clean against HEAD and section 0 is set;
  - `FROZEN.md5` hashes to `a01f05dfb1e622f78b2bba55d174be09`;
  - E0's code pins match;
  - the E1 amendment (and, under Option X, the V0-map amendment) is merged;
- take an O_EXCL lock per look before the first outcome row;
- append `started`, `completed` or `aborted` for each look to an external ledger, `/data/mal/exp022/LOOK_READS.jsonl`;
- refuse a second read of a look, a look before its conditions (section 7.1), and any look after a PASS or a halt;
- implement sections 2–8 with the deciding parameters as constants. There is no CLI override for any of them;
- apply section 10's refusals;
- print the verdict and the report to stderr before writing any file;
- report both p-values and every section 13 line;
- reproduce E0's C md5 at its merge commit.

**Commits.** Its merge commit is recorded in a dated amendment. After the first look, it may change only to fix a defect, by a dated amendment before the next look, with the E0 and fixture md5s unchanged. A fix never changes a parameter.

## 13. Report-only (never deciding)

- Legs: live 1/62, the 1,350 ms exit lag, the rent "always" leg, and 0.05 / 0.25 / 0.5 SOL.
- Statistics: per-fill means; the date-cluster CI90 (1,000 date resamples, seed 1); the trade-level bootstrap p (section 7.3).
- Judge item 11 (JUDGE:219-222):
  - pick − exposure-matched control (guard-passing, organic, slower non-picks; fill share ≈ 0.88);
  - pick − guarded baseline;
  - the paired cap − 30-min hold on picks;
  - first-half and second-half means;
  - the exit-type split (tp / sl / deadline), and the fill quality of deadline exits.
- The BOOST-90% exit by `boost_vault` (SYN:248, :279), where the tape carries BOOST events (walk-2 hours with the A8 decoder).
- Diagnostics:
  - k, L and ms per slot per hour;
  - s0 − complete;
  - the guard-reject share and the count of synthetic pools;
  - V0 against event V over the hold;
  - excluded hours and attempts;
  - decisions by label.

## 14. Honest expectation, power and framing

**On exploration rows, as an approximation of this book** [measured, JUDGE:92-96]:
- Cell: 0.1 SOL, live-tradable picks, lag 2 slots, day-mean k with round.
- It is not this file's pinned per-hour ceil, the 550 ms lag, the gross guard or the block_time cap, so it is close to the pinned book but not the same.

| Leg, entry | P2–P4 | P1 (OOF) | oracle |
| --- | --- | --- | --- |
| flat, 1.3 s | +2.93 [date lo +1.21] 20/28, ex-best-day +4.616 SOL | +1.24 [−0.82] 5/7, ex-best-day −0.034 SOL | −0.87 [−2.39] 2/4 |
| flat, 1.9 s | +2.71 [+0.96] 19/28 | −0.37 [−2.28] 3/7 | −2.72 [−3.79] 0/4 |
| pressure, 1.3 s | +2.43 [+0.98] 19/28 | +1.39 [−0.42] 5/7 | −0.61 |
| pressure, 1.9 s | +2.16 [+0.71] | −0.10 [−1.86] 3/7 | −2.32, 0/4 |

**Best honest estimate** [inferred, by the judge]:
- August–September: about +1.5% per attempt on the live leg, range +0.5 to +2.5 (JUDGE:144); about +1.3 flat and +1.1 pressure at 0.1 SOL (JUDGE:145).
- October: about +0.3% to +0.5% on the live leg, flat about +0.3. The true mean is −1.5 to +2.0 at 80%, and P(≤ 0) is about 40% (JUDGE:146).

**A selection haircut, not an execution one.** The step from +3.49 to about +1.5 is a design-selection haircut of 1.8–2.1 pp (JUDGE:144). D6's "no pre-registered haircut" covers execution only (JUDGE:187; SYN:253). No haircut enters the scoring except section 8's min(0, r̄).

**Running cost** [inferred]:
- About 0.11 SOL/day (SYN:284).
- Break-even is about 1.4% at 0.1 SOL and 0.6% at 0.25 SOL (JUDGE:160).
- At the October central, CAP-PICK does not pay the running cost at 0.1–0.25 SOL (JUDGE:161, :244).

**Power** [measured, simulation; JUDGE:119-121]. Between-day SD at the template's 3.09 pp:

| | day-level p | trade-level p |
| --- | ---: | ---: |
| False pass | 0.8% | 2.8% |
| Power at +1.0% flat | 5.1% | 15.4% |
| Power at +1.9% flat | 22.2% | 45.3% |

- At 1.5× and 2× the template SD, the day-level false pass stays at 0.9% and 1.0%, and power at +1.9% falls to 15.2% and 10.4%.
- The judge's simulation used a binding day-7 futility (JUDGE:115). Here futility does not bind the read.
- **Power at +0.5% flat:** "about 6%" (JUDGE:185). The judge does not say which p that figure uses. Under the day-level p it is at most the 5.1% at +1.0% [inferred here].

**P(pass)** [inferred, by the judge]:
- About 10% (5–20%) with the trade-level p and the binding 1.9 s leg (JUDGE:133, :244).
- About 5–7% with the day-level p used here (JUDGE:133).
- The synthesis's 25–30% (SYN:225, :543) is superseded and not used.

**Other figures** [inferred]:
- A 21-day realized mean has an SE of about 1.05 pp on the flat leg, so about ±1.7 pp at 90% (JUDGE:156).
- About 77 live-tradable pick attempts per day in exploration (JUDGE:159).

**A fail is the expected outcome.** It leads to A12, the cost wind-down the owner has pre-agreed (SYN:426-436). A pass would be a surprise, and it would still be paper only.

## 15. Multiplicity

- **k = 1.** One arm, no Holm. The family α is 0.025, spread over the three looks by Bonferroni. DEC-021 §8's cross-walk accounting is unchanged.
- **No sealed block.** EXP-022 is a retune of EXP-012 (cap, guard, exit, size, priority), so it belongs to EXP-012's lineage. It can never claim a sealed block (`DEC/DEC-014-holdout-ledger-and-multiplicity.md:106`, `:109`).
- **m.** The audit read CAP-PICK outcomes on the 27 non-P1 dates (JUDGE:5, :12), and the A2 check reads them again. DEC-014 counts distinct EXP numbers (`:134`), so EXP-022 is counted from this merge, and the next block claim uses m = max(13, the count at claim). This only tightens. The manager confirms the count at that claim.
- **Overlap with the 10-16 FINAL (Option X).** Hours `[10-10, 10-16)` are read by both. The two books differ, and the FINAL cannot by itself support live (`DEC/DEC-016-exp012-forward-on-chain-hours.md:91-93`). So the overlap cannot give two live supports from one stretch of data.

## 16. After the read

- **PASS** leads only to the DEC-018/DEC-019 path:
  - DEC-021 §7's pre-live checks at CAP-PICK's own operating point: runner-vs-scorer md5 equivalence, the 1.3 s and 1.9 s legs, and V-priced fills (SYN:282; DEC-021:70);
  - landing canaries and a live calibration at 55k: A6, a DEC-019 amendment, owner decision O4 (DEC-021:71);
  - a reviewed build carrying the section 4.3 min_out under md5 equivalence (A5);
  - the owner's yes;
  - the 1 SOL.

  The live terms (O4/O5) are deferred. A paper pass is not live evidence (DEC-021:74).
- **FAIL, NOT_DECIDABLE or halt.** CAP-PICK is retired, with no retune and no re-read. A12 follows, and the owner decides within 24 h (SYN:435).
- **Withdrawal before counting.** A ledger edit returns the "Forward walk 2" owner to reserved.

## 17. Disclosures

1. **Design chosen in-sample.** The cap, the guard and the picks × short-exit pairing were chosen on P2–P4 after about 20,000 cell-block reads (JUDGE:16).
2. **Untradable picks.** The live gate cannot trade 8.0% of the P2–P4 picks, and those are the best ones (+7.571%) [measured, JUDGE:17-19, :58].
3. **The latest regime is about zero.** The 1.9 s leg is the likeliest failure point: P1 is −0.37% flat (3/7 days), and oracle is −2.72% (0/4) [measured, JUDGE:22-23, :93, :195].
4. **Falling daily mean.** On the tradable set: Spearman −0.347, p 0.0409 [measured, JUDGE:24, :108].
5. **The cap's gain decays,** from +3.3 to +0.2 pp [measured, JUDGE:84].
6. **Own-buy constant offset.** The approximation is about +0.1 to +0.2 pp per attempt optimistic [inferred, JUDGE:224].
7. **1/62 fail rate.** It was measured at 500k, not 55k (JUDGE:225).
8. **October BOOST.** The sample is n = 10 (JUDGE:226; SYN:103).
9. **P1.** P1 is out-of-fold on adjacent days, and its threshold was cut on those same scores (JUDGE:227).
10. **A2 by construction.** A2's P2–P4 check passes by construction; P1 is a disclosed line (JUDGE:98, :228).
11. **Zero receive lag.** The replay assumes zero receive lag (`cap_pick_gate_replay.py@8fd49e5:55`). Live decisions come seconds later.
12. **The pick sets differ both ways.** On the #462 smoke day (2026-08-15, one day), the online and offline pick sets differed in both directions: jaccard 0.516, with 21 online-only picks absent from the offline table [measured, PR #462 body]. The 32–60 min band was not measured by the judge (JUDGE:65, :255).
13. **Per-trade V** (SYN:105). V0 is held constant over the hold.
14. **The day-level t** assumes roughly normal date means, with W from 7 to 22.
15. **Unpriced regime changes:** the 10-02 redeploy, 200 ms slots from about 10-09T14:34Z, per-trade V, synthetic migration, and BOOST cadence set off-chain (JUDGE:152; SYN:102-106). Every counted hour is after the 200 ms step, and after the 10-05..10-07 seal-exposure days (SYN:251).
16. **Walk integrity.** Option X counts forward-1002 hours walked before the A8 resume guard. The reader-side refusal catches bad lines only. A truncated hour of about 10.5k–16k slots passes verify (DEC-016:336), on both walks.
17. **Partial dates.** Under Option Y, the first and last dates of each look are partial days.
18. **InitBoost** is not applied (section 3).

## 18. Out of scope

- Any tool code: the read mode, the A8 items, E0, E1, the V0 map and the A2 run each come separately.
- The executor's min_out replacement (A5), the canaries (A6), and the live-trial terms (A7, O4/O5).
- A11's DEC-016 amendment (Option Y only).
- No `data/tries.jsonl` line. The lab writes tries lines when a try is spent, not at registration. No earlier pre-registration PR added one.

## Sources

`ARTIFACTS/lab/audit-2026-10-08/capv_JUDGE.md`; `ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md`; [DEC-021](../DEC/DEC-021-champion-challenger.md) Amendment 1; [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md); [DEC-016](../DEC/DEC-016-exp012-forward-on-chain-hours.md); [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md); [EXP-012](EXP-012-migrate-entry-model-refreeze-prereg.md); `ARTIFACTS/lab/probe-final-2026-10-07.md`; `docs/HANDOFF.md` (2026-10-08); `tools/forward_exp012_gate.py`, `tools/forward_paper.py`, `tools/pump_history_backfill.py`, `tools/pump_structure_monitor.py`, `tools/pump_structure_pins.json`, `tools/latency_curve.py`, `tools/probe_executor.py`, `tools/exp012_forward_vmap.py`, `tools/probe_sim_calibration.py` (#463); drafts #461 (`ebb77f4`) and #462 (`8fd49e5`).
