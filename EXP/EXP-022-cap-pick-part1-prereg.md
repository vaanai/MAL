# EXP-022 Part 1: CAP-PICK walk-2 read, pre-registration

**Written and merged before the first counted hour.** No row under `/data/mal`, no forward-walk file and no runner file was opened to write this file. The one exception is the sha256 of the probe fills ledger in section 8, which is a hash of that file only. This file is docs only. The read tool, the pick-set replay and the integrity code are separate PRs (sections 2.1, 10 and 12). Nothing in this file says or implies that any book is positive. A fail is the expected outcome (section 14).

| Field | Value |
| --- | --- |
| **ID** | `EXP-022-cap-pick-part1-prereg` |
| **Status** | **planned**. Option Y: counting starts at 2026-10-16T01 (section 0). |
| **Declared (UTC)** | 2026-10-08 |
| **Owner decision** | The owner approved the audit recommendations on 2026-10-08, including O2 and O3 (MiScusi notebook `n_vS9qHGmF7-jinQ`; `ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md:457-458`). O2 is [DEC-021 Amendment 1](../DEC/DEC-021-champion-challenger.md), in this PR. O3 included the pre-approved fallback "count from 10-16T01 if the Part 1 can't merge in time"; the manager chose that fallback (section 0). |
| **Parent** | The 10-08 audit: `capv_JUDGE.md` §4 items 1–10 (binding) and §5; `SYNTHESIS.md` A1–A3, A8, A9, A11, A12 and D5. [EXP-012](EXP-012-migrate-entry-model-refreeze-prereg.md) (the frozen selector). [DEC-021](../DEC/DEC-021-champion-challenger.md) Amendment 1. [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md). [DEC-016](../DEC/DEC-016-exp012-forward-on-chain-hours.md) Am.2, Am.3 and Am.6. [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md). |
| **Hypothesis** | The CAP-PICK book has mean SOL per attempt > 0 at a 0.1 SOL stake on post-upgrade October chain hours, under both fail models, at a reachable latency, after the live−sim correction. The book is: frozen EXP-012 picks as the live gate makes them, an on-chain min_out at seed × 1.15, a 300 s wall-clock exit, and 55,000 lamports per send. |
| **Kill condition** | No look passes (section 7) by day 21; or a structure halt (section 11); or a precondition fails (section 10). One read with three looks. No retune, no re-read. |
| **Expected outcome** | **FAIL.** P(pass) is about 5–7% with the day-level p (section 14). A fail leads to A12, the cost wind-down, which the owner has pre-agreed. |
| **Tools** | **Read:** a sealed read mode of `tools/cap_pick_score.py` (draft #461), which must merge with quant-proof before the day-7 look (section 12). **Picks:** `tools/cap_pick_gate_replay.py` (draft #462). **Halt:** `tools/pump_structure_monitor.py` (#456). **Calibration:** `tools/probe_sim_calibration.py` (#463). |

**Labels.**
- **[measured]**: copied from a cited file that computed it. Simulations on exploration rows are measured arithmetic, not evidence of edge.
- **[inferred]**: reasoned, by the cited author or here.
- **[pinned]**: a design value this file fixes. Not evidence.

**Citation shorthand.**
- Draft-PR code is cited at a commit: `cap_pick_score.py@ebb77f4` or `@2bab07b` (#461), and `cap_pick_gate_replay.py@8fd49e5` (#462).
- `JUDGE` is `ARTIFACTS/lab/audit-2026-10-08/capv_JUDGE.md`.
- `SYN` is `ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md`.

## 0. Counting start (the one pinned line)

```
EXP022_COUNT_START: 2026-10-16T01
```

- **Format.** The line must match the regex `^EXP022_COUNT_START: 2026-10-16T01$` exactly once in this file. The read tool refuses unless it does, and unless this file is clean against HEAD. The value is never changed after merge.
- **Manager decision (2026-10-08): Option Y.** The owner pre-approved this fallback (SYN:458: "Yes, if the Part 1 can merge before 10-10T00Z; otherwise count from 10-16T01"; notebook `n_vS9qHGmF7-jinQ`; SYN:458).
- **Why not Option X** (`[2026-10-10T00, 2026-10-31T00)`, considered and rejected), for two reasons:
  - Under X, the 10-16 FINAL, its book (B) and the DEC-016 Am.3(a) re-score would price EXP-012's entered mints on `[10-10T00, 10-16T00)`. Those are mostly CAP-PICK's counted picks, so look 1 would not be blind on 6 of its 7 dates.
  - E0, the read tool and the seal exceptions could not be built and reviewed before 2026-10-10T00Z.
- **Deadline.** E0 (section 2.1) and P2 (section 10) must be met before 2026-10-16T01:00Z. Otherwise CAP-PICK is withdrawn. There is no later start without a new dated amendment and the owner.
- **Final head.** After the manager's final commit on this branch, quant-proof re-OKs that final head before merge.

| | Option Y |
| --- | --- |
| Counted window | `[2026-10-16T01, 2026-11-06T01)`, all on walk 2 |
| Data at looks 1 / 2 / 3 (days 7 / 14 / 21) | `[start, 2026-10-23T01)`, `[start, 2026-10-30T01)`, `[start, 2026-11-06T01)` |
| Day-level t clusters at looks 1 / 2 / 3 | 7 / 14 / 21 blocks of 24 h, each starting at 01:00Z, from 2026-10-16T01 (section 7.2) |
| UTC dates for the gate's day count, at looks 1 / 2 / 3 | 8 / 15 / 22. The first and last dates are partial. |
| A11 October check | Report-only, binding for spending only (section 7.5), on `[2026-10-06T00, 2026-10-16T00)`. It needs its own DEC-016 amendment, merged before 2026-10-16T00:00Z. That amendment is a separate manager PR and is not written here. |
| E1 calibration runs after | the 10-16 FINAL is written and the A11 read is done (`docs/HANDOFF.md:93`) |

## 1. The book: every parameter, in ms and SOL

| Item | Value | Source | Label |
| --- | --- | --- | --- |
| Selection | The frozen EXP-012 model `ARTIFACTS/exp012/model.txt` (md5 `a1810d219ed61db64a396f40dc302ce5`) at threshold 0.8030766588450794, with features computed as the live gate computes them (section 2) | `EXP/EXP-012-migrate-entry-model-refreeze-prereg.md:183-184`; `ARTIFACTS/exp012/FROZEN.md5:2`; JUDGE:177-181 | [pinned] |
| Universe | Canonical PumpSwap pool, WSOL quote, non-mayhem, V0 in [17,500,000,000, 17,700,000,000] lamports, one attempt per mint (section 3) | SYN:271 | [pinned] |
| Entry | 1,300 ms after the pool-create slot. k = ceil(1,300 / that hour's ms per slot). END bound. | JUDGE:193-197; SYN:273 | [pinned] |
| Binding latency leg | 1,900 ms, same rule | JUDGE:194 | [pinned] |
| Guard | min_out at seed × 1.15, gross basis, floored to base units | JUDGE:202-206 | [pinned] |
| Exit | tp +50% / sl −30% on post-trade spot against the post-buy mark. A 300 s wall-clock cap from the landing print's block_time, sold by a timer. | SYN:275; JUDGE:207-209 | [pinned] |
| Exit lag | 550 ms primary, as ceil to slots per hour, on every sell, the deadline sell included. 1,350 ms is report-only, also on every sell. | JUDGE:198-201 | [pinned] |
| Stake | 0.1 SOL (100,000,000 lamports) | SYN:260, :277 | [pinned] |
| Cost per send | 55,000 lamports | SYN:276 | [pinned] |
| Live−sim correction | Per filled attempt: the larger of DEC-021 §1's live haircut and the E1 penalty (section 8) | DEC-021:39, :54 | [pinned] |
| Rent | 2,039,280 lamports on a filled attempt whose sell cannot fill its full balance; otherwise 0. The "always" leg is report-only. | JUDGE:215 | [pinned] |
| Fail legs | flat 15% and pressure at slope scale 1, both binding; live 1/62 report-only | SYN:278; CLAUDE.md promotion gate | [pinned] |
| Statistic | Mean SOL per attempt | JUDGE:211 | [pinned] |

## 2. Pick set: the live gate, replayed on the walk tape

The read's picks are the mints that the live EXP-012 gate decides `pick` on the walk tape (JUDGE:177-181). The gate is `Exp012Online` (`tools/forward_exp012_gate.py`) with `ForwardEngine._exp012_pass` (`tools/forward_paper.py`), applied by `tools/cap_pick_gate_replay.py`. The replay reuses the runner's own functions; it does not copy them (`cap_pick_gate_replay.py@8fd49e5:1-22`). It behaves as the runner does:
- **Truncation.** Feature recording stops exactly 32 min after the create (module note 4, `tools/forward_exp012_gate.py:35-41`).
- **60-minute skip.** A mint that migrates more than 60 min after its create gets `no_features` (note 5, `:42-44`; `DROP_AFTER_CREATE_MS`, `:82`).
- **Daily restart at 00:00Z.** State is dropped and creator history is rebuilt from the creates of the previous and current UTC day. A mint created before the restart gets no decision (note 7, `:48-59`; note 8, `:60`).
  - The replay boots at 00:00Z on every UTC day of the window. The first boot is 2026-10-16T00Z.
  - Only decisions at or after 2026-10-16T01 count.
  - The first boot's preload and the 10-16T00 hour are inside the disclosed walk-2 feature buffer (section 9).
- **Receive time.** The walk tape has no receive time, so `t_recv_ms` is taken as `block_time × 1000`, a zero receive lag (`cap_pick_gate_replay.py@8fd49e5:55`; disclosure 11).
- **Uptime.** The replay assumes 100% runner uptime. The live runner's downtime skips are not modelled (disclosure 19).

Only `pick` decisions can be attempts. `below`, `no_features`, `no_bond_history` and dead (pre-restart) mints are not attempts.

**Exploration figures, restated on the live-tradable set** [measured, JUDGE:58-60]. These are the live leg at 0.5 SOL, on the exploration blocks the book's design was chosen on (JUDGE:16). They are in-sample for the book, and they are not evidence of edge.

| Scope | Mean per attempt | Date-cluster CI90 lower bound | Days positive | Ex-best-day |
| --- | ---: | ---: | ---: | ---: |
| P2–P4 | +3.492% | +1.50 | 20/28 | +27.60 SOL |
| P1 | +1.419% | −0.96 | 5/7 | −0.29 SOL |
| oracle | −0.985% | −2.69 | 2/4 | — |

The all-picks figure, +3.819% (JUDGE:58), includes the 188 P2–P4 picks (8.0%) that the live gate cannot trade. It is never used as the effect size.

### 2.1 E0: md5 decision-equivalence, before 2026-10-16T01:00Z

Before the first counted hour, a dated amendment to this file records all four items below:

1. **Replay span.** Exactly one UTC day of exploration tape, pinned by its `VIEW.sha256`. Never forward-walk or walk-2 data. Both sides boot at 00:00Z of that day.
2. **Runner equivalence.**
   - A = the runner's own replay path, `tools/forward_paper.py` `replay_rows` (`:2848`), with the frozen gated book, on that day.
   - B = `tools/cap_pick_gate_replay.py replay` on the same day. It must be the **read-ready version**: the file and commit that will read the walk-2 hours.
   - Each side gives a canonical decision list: one line per mint, sorted by mint, `mint<TAB>decision<TAB>mig_ms<TAB>repr(score)` (an empty score when there is none).
   - md5(A) must equal md5(B).
3. **Scorer equivalence.** C = the mint list that the read-ready `tools/cap_pick_score.py --book picks` counts as attempts when given B's decisions on that day. C must equal, by md5, B's `pick` mints restricted to the section 3 universe.
4. **Code pins.** The git blob shas of `tools/forward_exp012_gate.py`, `tools/forward_paper.py`, `tools/exploration_entry_model.py` and `tools/cap_pick_gate_replay.py` at the E0 commit. Also the md5 of `ARTIFACTS/exp012/FROZEN.md5` (`a01f05dfb1e622f78b2bba55d174be09`, `EXP/EXP-012-migrate-entry-model-refreeze-prereg.md:183`).
   - Each look runs in a worktree at a recorded commit where those four blobs match.
   - The read tool hashes the modules it actually imports, records them, and refuses if a pinned module differs.
   - Later changes on `main` (for example A5's re-pointing of the paper runner) do not reach the read.

**If E0 is missing, or any md5 differs,** CAP-PICK is withdrawn before counting (JUDGE:251). Nothing is fixed and retried after the window starts.

## 3. Universe

One attempt per mint. A mint is an attempt only if all of these hold:
- the gate decided `pick` (section 2);
- its canonical pool is the pool its migrate tx created: the `pool` field of its `migration` event (`tools/pump_history_backfill.py:275-292`);
- the quote mint is WSOL: `quote_mint` on that event (`:292`);
- it is non-mayhem: `is_mayhem_mode` is false on its create event (`:233-255`);
- V0, as defined below, is in [17,500,000,000, 17,700,000,000] lamports (SYN:271; `cap_pick_score.py@ebb77f4:116`). The missing-V0 rule below covers a pool with no V0;
- the block_time of s0 (the decision print, defined in section 4.1) is in the counted window;
- s0's block_time is at most 80 s after the block_time of the mint's `complete` event. This restates G's "s0 − complete slot ≤ 400 slots" (`cap_pick_score.py@ebb77f4:115`) in ms, at 200 ms. The count of mints this excludes is reported.

**Synthetic-migration pools** that meet these conditions are attempts. If one opens above the guard, it is a reject at −55,000 lamports (section 4.3). It is never dropped.

**InitBoost is not applied.** SYN:271 lists it, but the walk tape cannot tell, at decision time, whether the migrate tx carried InitBoost:
- the walker decodes the create, complete and migration events and trades, but not the InitBoost instruction (`tools/pump_history_backfill.py:207-292`);
- the A8 decoder list does not add it (SYN:389).

BOOST health is covered instead by the A3 monitor's `boost_share_low` rule, which halts when InitBoost is on fewer than 80% of sampled non-mayhem WSOL graduations (`tools/pump_structure_monitor.py:113`, `:1060-1072`). Pools without BOOST stay in the book. In exploration, BOOST was detected on 94–99% of V>0 graduations [measured, single investigator; SYN:50].

**V0.**
- **Source.** V0 is the `virtual_quote_reserves` that the A8 event-V decoder (SYN:389) gives for the pool at the decision print. The decoder's pre-/post-trade convention is fixed by its fixture tests.
- **Not an account read.** For v2 pools, V moves per trade, so V0 can differ from what a pool-account read at that instant would return. On 10-04..10-08, 1.2% of trades moved V, with at most 1.22 bp of drift in a fresh pool's first 30 min [measured, single investigator; SYN:105].
- **Pricing.** PumpSwap tape rows carry pre-trade reserves, and price = (quote vault + V) / base (`cap_pick_score.py@ebb77f4:67-73`; `docs/HANDOFF.md:71`). The path is priced with V0 held constant over the hold.
- **Missing V0** (a pick that meets every other universe condition but whose pool has no decodable V0). Such attempts are not excluded:
  - If any of them is in the top 3 attempts of either binding leg, or they are more than 1% of the look's attempts, the look is NOT_DECIDABLE. This follows the DEC-016 Am.4 §3 rule.
  - Otherwise each is scored at V0 = 17,500,000,000 and at 17,700,000,000, and takes the lower P&L per leg.

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
- **Report-only lag.** 1,350 ms (7 slots at 200 ms). It applies to every sell, the deadline sell included (JUDGE:200).
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

- **Deadline.** The deadline print D is the first print whose block_time ≥ the landing block_time + 300 s. The landing block_time is that of the last print at slot ≤ X (`cap_pick_score.py@ebb77f4:42-48`).
- **Deadline sell.** It fills at the first print with slot ≥ D + L + 1. Live, it is a pre-signed timer.
- **No day-mean fallback.** A counted attempt whose path has no usable block_time is a refusal. Every walk row carries block_time (`tools/pump_history_backfill.py:352`).

### 4.5 Costs, rent, stake

- **Costs.** 55,000 lamports per send (`cap_pick_score.py@ebb77f4:189`). A filled round trip pays two sends, and a reject pays one.
- **Rent, primary.** 2,039,280 lamports, the lab constant (JUDGE:215), is charged on any filled attempt whose sell cannot fill its full balance. That means there is no print at or after D + L + 1 in covered hours, or base is left over. The count is reported.
- **Rent otherwise is 0.** The token-account rent is refunded when the sell closes the account (`tools/probe_executor.py:1304`). On the probe, the mean rent per round trip was 1,513,840 lamports charged and 1,513,840 refunded, net 0, over 61 round trips [measured, `ARTIFACTS/lab/probe-final-2026-10-07.md:53`].
- **Rent, report-only "always" leg.** 2,039,280 lamports on every fill.
- **Stake.** 0.1 SOL. The 0.05, 0.25 and 0.5 SOL lines are report-only projections (SYN:279).

## 5. Fail legs and pressure

- **Flat 15%.** A filled attempt i is 0.85 × pnl_i + 0.15 × (−55,000) (`cap_pick_score.py@ebb77f4:468`).
- **Pressure, slope scale 1.**
  - A filled attempt i is (1 − p_i) × pnl_i + p_i × (−55,000) (`cap_pick_score.py@ebb77f4:469`).
  - p_i = sigmoid(c + 0.8 × log1p(same_slot_buys) + 0.35 × log1p(nearby SOL)) (`tools/latency_curve.py:39-40`; `DEC/DEC-016-exp012-forward-on-chain-hours.md:341`).
  - The intercept c is fitted by `tools.latency_curve.fit_curve` so that mean p = 0.289 over the read's own fills of that cell (`tools/latency_curve.py:41`, `:90-94`). It is fitted separately for every cell (1.3 s, 1.9 s, and each report-only leg) and at every look.
  - same_slot_buys is the number of buys in the landing slot.
  - nearby is the buy lamports in the round(2,000 / ms_per_slot(h)) slots up to and including the landing slot (`cap_pick_score.py@ebb77f4:395`).
- **pnl_i** is after the section 8 correction and the section 4.5 rent.
- **Live 1/62.** Report-only. It was measured at 500k priority, not 55k (JUDGE:225).
- **Guard rejects** are −55,000 on every leg and are not mixed.
- **Disclosed.** Shorter slots mean fewer same-slot buys (DEC-016:341). Because the intercept is refitted to mean p = 0.289 at each look, only the cross-section of p moves.

## 6. Unit and accounting

- **Attempt.** A mint in the universe with a `pick` (sections 2–3). It is one row: either a filled round trip, or a guard reject at −55,000. Unscored or skipped mints are not attempts (JUDGE:211).
- **Deciding statistic.** Mean SOL per attempt at the 0.1 SOL stake, after the section 8 correction.
- **Fills.** Attempts that passed the guard. The ≥ 100 count is fills (JUDGE:212). The per-fill mean is reported.
- **Dates.** The UTC date of the block_time of s0, used for the gate's day count, ex-best-day and the report-only date-cluster CI90.
- **Blocks.** The 24 h block from 2026-10-16T01 that holds the block_time of s0, used for the day-level t (section 7.2).

## 7. Looks and the decision rule

### 7.1 Looks

There are three looks, at days 7, 14 and 21 of the counted window, each on cumulative data (section 0). Each runs once, and only when all of these hold:
- (a) every hour of `[start, look end + 1 h)` is sealed, verified and checked (section 10);
- (b) E1 is recorded with n ≥ 20 (section 8);
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
  - clusters are the 24 h blocks from 2026-10-16T01 (7 / 14 / 21 blocks). A block with no attempts is dropped; W is the number of blocks with at least one attempt (reported), and df = W − 1;
  - m_b is the mean SOL per attempt in block b; sd is the sample SD of the W block means (ddof 1); t = mean(m_b) / (sd / √W); p = P(T_{W−1} ≥ t), one-sided (`cap_pick_score.py@ebb77f4:551-554`, there on UTC dates);
  - α is 0.005 at day 7, 0.008 at day 14 and 0.012 at day 21 (SYN:252, :280);
  - the larger of the flat and pressure p decides;
- (vii) the **1.9 s leg** has mean SOL per attempt > 0 and total SOL ex-top-3 > 0, after the section 8 correction. **Binding** (JUDGE:194).

Items (i)–(iv) are CLAUDE.md's promotion gate, unchanged.

**Two kinds of day.** The gate's "≥ 5 distinct UTC days, majority positive" counts UTC dates (8 / 15 / 22, partial dates included). The day-level t uses the 24 h blocks, so that a 1-hour date such as 10-23T00 at look 1 does not get full weight.

### 7.3 Reported at every look, never deciding

- The trade-level one-sided bootstrap p: the share of 10,000 seed-1 bootstrap means ≤ 0, under both legs (DEC-021:57; JUDGE:192).
- The draft already uses 10,000 draws for this p: `--boot-p-draws`, default 10,000, report-only (`cap_pick_score.py@2bab07b:128`). The CI90 keeps 1,000 draws.

### 7.4 Outcome

- **PASS.** The read ends at the first look that passes.
- **FAIL.** No look passes. The result is FAIL after look 3.
- **NOT_DECIDABLE look.** It is not a pass, and its alpha is not carried forward. The read continues to the next look.
- **Halt.** See section 11.

### 7.5 Futility (binding for spending only)

- **Day 7.** If the flat mean ≤ 0 and the pressure mean ≤ 0 at the primary cell:
  - A5, A6 and A7 spending pauses;
  - the owner is told that failure is likely;
  - the read continues (SYN:252, :427; JUDGE:248).
- **A11 October check.** It applies only if a separate DEC-016 amendment registers it before 2026-10-16T00Z.
  - It covers `[2026-10-06T00, 2026-10-16T00)` and is report-only.
  - It is read after the FINAL is written.
  - Its spending rule: if the flat and pressure means ≤ 0 and ex-best-day ≤ 0, spending pauses (SYN:417-424).
  - It never decides this read, and it is never gate evidence.

### 7.6 Family α

0.005 + 0.008 + 0.012 = 0.025, which is DEC-021's family α at k = 1 (DEC-021 Am.1 §5). The Bonferroni bound holds whatever the futility rule does.

## 8. Live−sim correction (DEC-021 §1 and §4; named set: faa3192 via E1)

**Rule.** Every filled attempt in every binding cell (1.3 s and 1.9 s) loses the **larger** of (a) and (b), before rent and the fail mix:

- **(a) DEC-021 §1's live haircut** (DEC-021:39; job #175): sell −16 bps of proceeds and entry +26.08 bps.
  - In lamports: H = P × (1 − (1 − 0.002608) × (1 − 0.0016)).
  - P is the sell proceeds after the pool fee and before send fees.
  - This is the lab's formula (`tools/exp012_backcheck.py:495-505`).
- **(b) The §4 penalty:** max(0, −2 × r̄). The 2 scales r̄ linearly from the probe's 0.05 SOL to 0.1 SOL. If r̄ ≥ 0, (b) is 0 (DEC-021:54; SYN:282).

It is the larger of the two, not their sum, because both estimate the same live−sim gap. This is conservative: it honours DEC-021 §1, and it can only lower the book. The book with only (a) and the book with only (b) are each reported, as report-only lines.

**E1, pinned.**
- **Command,** run at `c745411` (#463), with default flags only, no `--build`:
  ```
  python -m tools.probe_sim_calibration --fills /data/mal/probe-final/probe-fills-20261007.jsonl --tape-dir /data/mal/tip-tape-archive/fast-trades-tip --out-dir <a new, empty directory>
  ```
- **Fills sha256:** `8c0567ff872e2e62e79d53f8eb4adce88e236cdfa8b8e7d8674a7cd3dd78fdbc` [measured, `sha256sum` of the fills ledger only, 2026-10-08].
- **Tape dir:** pinned by path. It was not read to write this file.
- **r̄** = `aggregate["faa3192"]["pnl_gap_lamports_live_minus_sim"]["mean"]` in `calibration.json` (`tools/probe_sim_calibration.py:611`, `:702`). Its n is the `"n"` of the same object.
- **n < 20.** If n < 20, condition (c) is NOT_DECIDABLE, and no look runs (DEC-021 §4).
- **Reported:** n; the faa3192 trips excluded from n (of 33 [measured, `ARTIFACTS/lab/probe-final-2026-10-07.md:42`]), each with its reason; and the median.
- **Not used:** the 55-trip pooled figure (`docs/HANDOFF.md:93`).
- **Timing.** E1 runs after the 10-16 FINAL is written, because of DEC-016's seal extension (`DEC/DEC-016-exp012-forward-on-chain-hours.md:162`). It also runs after the A11 read.
- **Record.** Its MiScusi job id, commit, output sha256, n and r̄ go in a dated amendment before the first look.

**Context.**
- On 2026-10-06, the faa3192 set was n = 8, mean −384,022 and median −209,670 lamports [measured, DEC-021:54].
- The audit's corrected transfer estimate of live − sim is +0.84% [measured, single investigator; SYN:282], with CI90 −1.37% to +3.20% (`docs/HANDOFF.md:92`).
- faa3192 traded a different book: a 30-minute hold, a fresh-quote min_out and 500k priority (SYN:217). So both (a) and (b) carry over to CAP-PICK by assumption [inferred].

## 9. Data, ledger and seal

- **Hours.** Walk 2 only: `/data/mal/blocks/forward-1016` (planned; ledger row "Forward walk 2", owner EXP-022). Every opened hour needs a `backfill_verify --content` OK line and a sha256 line (DEC-016:26).
- **No overlap with the FINAL.** The counted window starts after the FINAL's window `[2026-10-06T00, 2026-10-16T00)` ends, so the FINAL, its book (B) and its Am.3(a) re-score price no counted pick. The A11 check is report-only and lies outside the counted window.
- **Feature reads on forward-1002, no outcome.** At the first boot, the gate replay reads the creates of `[2026-10-15T00, 2026-10-16T01)` and the prints of hour `2026-10-16T00`. Both are inside the walk-2 buffer `[2026-10-14T01, 2026-10-16T01)` already disclosed by DEC-021 §3 and EXP-012 Amendment 1.
- **The A11 read** of `[2026-10-06T00, 2026-10-16T00)`, if its DEC-016 amendment registers it, is disclosed in [EXP-012 Amendment 2](EXP-012-migrate-entry-model-refreeze-prereg.md) (DEC-014(a)).
- **Nothing before the FINAL.** No CAP-PICK process reads any forward-1002 hour, buffer included, before EXP-012's FINAL is written. The gate-decision-count monitoring never runs on forward-1002 hours before the FINAL.
- **Seal between looks.** No person, agent or job does any of these, except the sealed look itself:
  - opens a CAP-PICK outcome row of a counted hour;
  - prints a P&L, fill, exit, reject count, mean, CI or day sign for one;
  - prices the counted picks from any source.

  Monitoring may print only hour counts (sealed, verified, bad) and gate decision counts. This follows DEC-016 Am.2 (`DEC/DEC-016-exp012-forward-on-chain-hours.md:85-94`).
- **Paper twin.** The fast-0 paper twin's CAP-PICK fields stay sealed until the read ends: pnl, exit, fill, reject and daily totals, wherever they appear (positions.jsonl, runner-status, reports, Console). This is the same rule as DEC-016 Am.3's seal extension (`:155-164`). Health checks print lag, heartbeat and counts only.
- **A breach** is recorded here, dated, and the read is reported compromised. It cannot then support a live request.

## 10. Preconditions and integrity

**Before 2026-10-16T01:00Z, the first counted hour.** If any of these is not met, CAP-PICK is withdrawn before counting (JUDGE:251):
- **P0.** This file is merged, with quant-proof OK on its final head.
- **P1.** E0 is recorded with equal md5s (section 2.1).
- **P2.** The A3 monitor's last daily run before the first counted hour has no halt, and no core rule went unevaluated on two consecutive days (section 11).
- **P3.** The A8 integrity items are merged, and the walk-2 job starts on a commit that has them (SYN:389-392):
  - the event-V decoder;
  - the walker's `JsonlSink` refuses to resume when the file is shorter than its checkpoint;
  - the gate readers refuse bad lines;
  - the scorer has per-hour slot_ms.

**Before the first look** (a look without them does not run):
- **P4.** The read tool is merged, with quant-proof OK, before the day-7 look, 2026-10-23T01 (section 12). No CAP-PICK outcome is read before then.
- **P5.** E1 is recorded, with n ≥ 20 (section 8).
- **P6.** The A2 kill check has passed (SYN:292-308). A2 runs after E1, because it uses the section 8 correction.
  - Its first bar, reproduction to ≤ 0.01 pp, was met in phase 1: job #386 matched P2–P4 on 19,234 of 19,234 attempts to the lamport [measured, MiScusi notebook result for job #386, draft #461].
  - It runs on exploration rows only:
    - books: the live-tradable picks from the #462 full replay, scored by the #461 scorer with every section 4–5 and section 8 parameter at its pinned value;
    - success: the P2–P4 flat mean ≥ +1.0% per attempt, with the date-cluster CI90 lower bound > 0 on the flat leg and mean > 0 at 1.9 s (SYN:307);
    - P1 and oracle are reported lines (JUDGE:228);
    - a kill withdraws CAP-PICK (SYN:308) with no walk outcome read.

**At every look:**
- **Bad lines.** The readers refuse bad lines (a NUL byte, or a line that is not a JSON object), and the hour that holds one is bad. Today #461 counts bad lines and #462 skips them (`cap_pick_score.py@ebb77f4:780`; `cap_pick_gate_replay.py@8fd49e5:339-341`). The read mode must refuse.
- **Re-walks.** A bad or unverified walk-2 hour is re-walked once, outcome-blind, before the next look; the result stands.
- **Attempt coverage.** An attempt counts only if every hour is good, from its mint's create hour through the hour that holds the landing block_time + 300 s + 60 s. Excluded attempts are counted and reported.
- **Bad-hour ceiling.** If more than 5% of the look's window hours are bad, the look is NOT_DECIDABLE.
- **Missing V0.** See section 3.
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

The read tool is a sealed read mode of `tools/cap_pick_score.py`. It must merge with quant-proof OK before the day-7 look (2026-10-23T01). Until then, no CAP-PICK outcome of a counted hour is read. It must:
- refuse unless:
  - the section 0 line matches its regex exactly once, and this file is clean against HEAD;
  - `FROZEN.md5` hashes to `a01f05dfb1e622f78b2bba55d174be09`;
  - it runs in a worktree at a recorded commit where E0's four blobs match, and the modules it imports hash to their recorded values;
  - the E1 amendment is merged, with n ≥ 20;
- read only walk-2 hours in `[2026-10-16T01, look end + 1 h)`; hours from 2026-11-06T01 on are read only for the exits of attempts already counted. Plus the forward-1002 buffer hours of section 9 for features, and refuse every other forward-1002 hour. It reads no forward-1002 hour before EXP-012's FINAL is written;
- take an O_EXCL lock per look before the first outcome row;
- append `started`, `completed` or `aborted` for each look to an external ledger, `/data/mal/exp022/LOOK_READS.jsonl`;
- refuse a second read of a look, a look before its conditions (section 7.1), and any look after a PASS or a halt;
- implement sections 2–8 with the deciding parameters as constants. There is no CLI override for any of them;
- use 10,000 draws for the trade-level p (section 7.3; `cap_pick_score.py@2bab07b:128`);
- apply section 10's refusals;
- print the verdict and the report to stderr before writing any file;
- report both p-values and every section 13 line;
- reproduce E0's C md5 at its merge commit.

**Catalog guard (conditional).** `mal_catalog.check_read` denies EXP-022 on forward-1002 hours, whose owner of record is EXP-012. This matters only if the read tool (or the A11 leg) guards its reads with `check_read` as EXP-022. In that case, a disclosed non-owner read allowance in `mal_catalog` (PR #465, `claude/catalog-second-owner`, adapted) must merge first, limited to the section 9 buffer hours and the A11 range. It grants no ownership; EXP-012 stays the sole owner of every forward-1002 hour. Otherwise the tool allowlists exactly those hours, as above.

**Commits.**
- The read tool's merge commit is recorded in a dated amendment.
- After the first look, the tool may change only to fix a defect, by a dated amendment before the next look.
- That fix must reproduce the previous look's per-attempt output byte for byte on that look's window: the rows sha256, on every binding leg. Otherwise it is not applied.
- A fix never changes a parameter.

## 13. Report-only (never deciding)

- Legs:
  - live 1/62;
  - the 1,350 ms exit lag, on every sell;
  - the rent "always" leg;
  - 0.05 / 0.25 / 0.5 SOL;
  - the book with only section 8 (a), and with only (b).
- Statistics:
  - per-fill means;
  - the date-cluster CI90 on UTC dates (1,000 date resamples, seed 1);
  - the trade-level bootstrap p (section 7.3);
  - the count of rent-charged fills (section 4.5).
- Judge item 11 (JUDGE:219-222):
  - pick − exposure-matched control (guard-passing, organic, slower non-picks; fill share ≈ 0.88);
  - pick − guarded baseline;
  - the paired cap − 30-min hold on picks;
  - first-half and second-half means;
  - the exit-type split (tp / sl / deadline), and the fill quality of deadline exits.
- The BOOST-90% exit by `boost_vault` (SYN:248, :279), where the tape carries BOOST events.
- Diagnostics:
  - k, L and ms per slot per hour;
  - s0 − complete;
  - the guard-reject share and the count of synthetic pools;
  - V0 against event V over the hold;
  - missing-V0 attempts;
  - excluded hours and attempts;
  - decisions by label.

## 14. Honest expectation, power and framing

**On exploration rows, as an approximation of this book** [measured, JUDGE:92-96]:
- Cell: 0.1 SOL, live-tradable picks, lag 2 slots, day-mean k with round, no live−sim correction.
- It is not this file's pinned per-hour ceil, the 550 ms lag, the gross guard, the block_time cap or the section 8 correction, so it is close to the pinned book but not the same.

| Leg, entry | P2–P4 | P1 (OOF) | oracle |
| --- | --- | --- | --- |
| flat, 1.3 s | +2.93 [date lo +1.21] 20/28, ex-best-day +4.616 SOL | +1.24 [−0.82] 5/7, ex-best-day −0.034 SOL | −0.87 [−2.39] 2/4 |
| flat, 1.9 s | +2.71 [+0.96] 19/28 | −0.37 [−2.28] 3/7 | −2.72 [−3.79] 0/4 |
| pressure, 1.3 s | +2.43 [+0.98] 19/28 | +1.39 [−0.42] 5/7 | −0.61 |
| pressure, 1.9 s | +2.16 [+0.71] | −0.10 [−1.86] 3/7 | −2.32, 0/4 |

**Best honest estimate** [inferred, by the judge]:
- August–September: about +1.5% per attempt on the live leg, range +0.5 to +2.5 (JUDGE:144); about +1.3 flat and +1.1 pressure at 0.1 SOL (JUDGE:145).
- October: about +0.3% to +0.5% on the live leg, flat about +0.3. The true mean is −1.5 to +2.0 at 80%, and P(≤ 0) is about 40% (JUDGE:146).
- Section 8's correction lowers the scored book further. DEC-021 §1's haircut alone is about 0.4% of stake per fill [inferred, from 26.08 + 16 bps of proceeds].

**A selection haircut, not an execution one.**
- The step from +3.49 to about +1.5 is a design-selection haircut of 1.8–2.1 pp (JUDGE:144). It sits in the expectation, not in the scoring.
- D6's "no pre-registered haircut" covers execution only (JUDGE:187; SYN:253).
- The scoring applies only section 8's measured live−sim correction.

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
- These figures were simulated with a binding day-7 futility and day clusters (JUDGE:115), without the section 8 correction. Here futility does not bind the read.
- **Power at +0.5% flat:** "about 6%" (JUDGE:185). The judge does not say which p that figure uses. Under the day-level p it is at most the 5.1% at +1.0% [inferred here].

**P(pass)** [inferred, by the judge]:
- About 10% (5–20%) with the trade-level p and the binding 1.9 s leg (JUDGE:133, :244).
- About 5–7% with the day-level p used here (JUDGE:133).
- Section 8 can only lower these.
- The synthesis's 25–30% (SYN:225, :543) is superseded and not used.

**Other figures** [inferred]:
- A 21-day realized mean has an SE of about 1.05 pp on the flat leg, so about ±1.7 pp at 90% (JUDGE:156).
- About 77 live-tradable pick attempts per day in exploration (JUDGE:159).

**A fail is the expected outcome.** It leads to A12, the cost wind-down the owner has pre-agreed (SYN:426-436). A pass would be a surprise, and it would still be paper only.

## 15. Multiplicity

- **k = 1.** One arm, no Holm. The family α is 0.025, spread over the three looks by Bonferroni. DEC-021 §8's cross-walk accounting is unchanged.
- **No sealed block.** EXP-022 is a retune of EXP-012 (cap, guard, exit, size, priority), so it belongs to EXP-012's lineage. It can never claim a sealed block (`DEC/DEC-014-holdout-ledger-and-multiplicity.md:106`, `:109`).
- **m.**
  - The audit read CAP-PICK outcomes on the 27 non-P1 dates: P2–P4, 28 day-units (JUDGE:58; DEC-014:114). The A2 check reads them again.
  - DEC-014 counts distinct EXP numbers (`:134`), so EXP-022 adds one from this merge: m ≥ 13. The ledger notes this.
  - The audit's own reads (SYN §11.2) are unlisted readers, so 13 is a floor. This only tightens. The manager recounts at the next claim.
- **No overlap with the 10-16 FINAL.** The counted window starts after the FINAL window (section 9). The A11 check overlaps the FINAL window, but it is report-only and never gate evidence.

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
14. **The day-level t** assumes roughly normal block means, with W from 7 to 21.
15. **Unpriced regime changes:** the 10-02 redeploy, 200 ms slots, per-trade V, synthetic migration, and BOOST cadence set off-chain (JUDGE:152; SYN:102-106). Every counted hour is **expected** to fall after the 200 ms step. That step is a forecast for epoch 1053, about 2026-10-09T14:30Z (DEC-016 Am.6, `:320`), not a measurement.
16. **Walk integrity.** Walk 2 runs with the A8 resume guard (P3). A truncated hour of about 10.5k–16k slots still passes verify (DEC-016:336).
17. **Partial UTC dates.** The gate's date count includes the partial first and last dates; the day-level t does not use them (section 7.2).
18. **InitBoost** is not applied (section 3).
19. **Runner uptime.** The replay assumes 100% runner uptime. The downtime skips in JUDGE:178 are not modelled.
20. **A11 before the read tool.** The A11 report-only check gives October CAP-PICK outcomes on `[2026-10-06T00, 2026-10-16T00)` before the read tool must merge (2026-10-23T01). Those hours are not counted, and every parameter of the counted read is pinned in this file before then.
21. **Section 8 formula.** Per filled attempt i in each binding cell: pnl_i ← pnl_i − max(H_i, max(0, −2·r̄)), where H_i is the DEC-021 §1 haircut of attempt i.
22. **E1 tape files.** The E1 record also gives the sha256 of every tape file E1 opens.

## 18. Out of scope

- Any tool code: the read mode, the A8 items, E0, E1, the A2 run and the catalog non-owner read allowance each come separately.
- The executor's min_out replacement (A5), the canaries (A6), and the live-trial terms (A7, O4/O5).
- A11's DEC-016 amendment: a separate manager PR, to merge before 2026-10-16T00Z.
- No `data/tries.jsonl` line. The lab writes tries lines when a try is spent, not at registration. No earlier pre-registration PR added one.

## Amendments

### Amendment 1 (2026-10-08, before any counted hour): the E0 comparison set (section 2.1 item 2)

No CAP-PICK outcome was computed or read to make this amendment.

**Finding [measured].** A dry run was made with the E0 harness `tools/cap_pick_e0.py` (PR #473, head 71d03c2) on explore-0814, 2026-08-17. That is not the E0 day.
- Of the mints that migrate at most 60 min after their create, 857 of 857 match exactly on mint, decision, mig_ms and repr(score).
- All 56 mismatching lines are mints that migrate 3,734 to 49,214 s after their create. B marks them `no_features`, as section 2 defines (the 60-minute skip; `tools/forward_exp012_gate.py:42-44` and `:82`). A scores them (42 `below`, 14 `pick`).
- Item 3 matched: C = B's picks in the universe, 122 mints, md5 fa77cc835084c4ec08ce2b43caa488fc.

**Cause [inferred from code].**
- `replay_rows` (`tools/forward_paper.py:2848`) runs without a live clock: its `LatencyMeter` has no `now_ms` (`:2872`). The engine then prunes only when its print count equals a multiple of 50,000 at the end of a time step (`tools/forward_paper.py:1609-1611`). On second-resolution exploration time steps that rarely happens, so A seldom applies the 60-minute drop.
- The live runner has a live clock. It checks a multiple of 5,000 at millisecond steps (same lines), and `Exp012Online.prune` rate-limits itself to once per 60 s (`PRUNE_EVERY_MS`, `tools/forward_exp012_gate.py:85`; `prune`, `:395-399`; called from `ForwardEngine._prune`, `tools/forward_paper.py:2704`). Live therefore drops these mints at the first prune after the 60-minute mark (≥ 60 s apart; not measured), as B does at its own cadence.
- Live receive lag can prune a mint that migrates just under 60 min before its migration print arrives; that removes live picks and never adds one. Past 60:00, live and B can differ on the boundary, and E0 cannot test that; it is disclosed.
- The live gate's own rows stay sealed until the DEC-016 FINAL, so this is not measured on live output.

**Amended item 2 [pinned].** md5(A) = md5(B) is required over the canonical lists restricted to mints with `mig_ms − create_ms ≤ DROP_AFTER_CREATE_MS` (3,600,000 ms, `tools/forward_exp012_gate.py:82`) on either side, plus every mint B decides `pick`.
- Each side uses its own create time: B's `create_ms`; A's `mig_ms − 1000 × time_to_migrate_s`, else the fed CreateSignal's `t_signal_ms`. A disagreement on a mint's create time counts as a mismatch.
- The full-list md5s, and an A label × B label crosstab for the older mints, are reported and never decide.
- A mint that migrates more than 60 min after create is `no_features` once `Exp012Online.prune` runs past its 60-minute mark, as section 2 says; before that, B and live can still pick it. This amendment changes no book rule.

**E0 day [pinned].** explore-0814, 2026-08-20, both sides booting at 2026-08-20T00:00Z. 2026-08-17 was used only for the dry run.

**A-side construction, disclosed.**
- (a) A's creates are built with the gate replay's `create_signal_from_row`, a shared input, so E0 tests the gate decision path, not create parsing.
- (b) A is fed only the trade rows of mints that have a first create row in the day at or before the row's hour. This is a memory bound, since `replay_rows` buffers other rows in `engine.early`. B never decides a mint without a create either. The fed row counts differ, though: A was fed 8,697,138 trade rows and B imputed 8,701,079 on 2026-08-17 [measured, dry-run e0.json]. So the inputs are not shown to be identical; any decision effect of that difference is caught by the md5. The count of dropped rows is recorded in `e0.json`.
- (c) A's preload runs without `tape_dir`, as B's does.
- (d) Labels map as follows: `entered` → `pick`; reason `below_threshold` → `below`; any other reason verbatim; null → `unknown`.
- (e) A needs about 9–10 GB (builder's report; not in `e0.json`) for one explore-0814 day, so the official run is a MiScusi job with 16 GB.

**Unchanged:** items 1, 3 and 4; the book; the pick rule and threshold; the looks; the gate.

### Amendment 2 (2026-10-08, before any counted hour): EXP-024 (H5-BOOSTFLOOR) and walk 2

No CAP-PICK outcome was computed or read to make this amendment. EXP-022's thresholds, counted window, pinned counting-start line, seal (section 9), looks, correction and gate are unchanged.

[EXP-024](EXP-024-h5-boostfloor-part1-prereg.md) is a separate DEC-014 family that takes DEC-021's second α slot ([DEC-021 Amendment 2](../DEC/DEC-021-champion-challenger.md), [DEC-023](../DEC/DEC-023-h5-family.md)). H5 prices the same V-band pools in the same first 330 s as CAP-PICK, so its outcomes overlap CAP-PICK's counted picks wherever the mints coincide. To keep section 9, these rules apply:
1. **EXP-024 Look 2 waits.** It reads walk-2 hours `[2026-10-16T01, 2026-11-06T01)` only after EXP-022's read has ended: a PASS at some look, look 3 done or NOT_DECIDABLE, a halt, or a withdrawal. Its tool refuses a walk-2 hour until EXP-022's `LOOK_READS.jsonl` shows a terminal state, and it reads that state only. EXP-024's Look 1 reads forward-1002 hours and counts no walk-2 hour.
2. **H5 trades exclude CAP-PICK picks.** From 2026-10-16T01 to the end of EXP-022's read, no H5 trade, live (DEC-024) or paper, is taken on a mint the EXP-022 gate picked, and no H5 record is joined to a CAP-PICK pick. No per-pool H5 P&L is produced before each CAP-PICK look. The gate's decisions are online, so the set is known in real time. The H5 side reads only the `mint` field of the decision-time intents, into memory, and writes no CAP-PICK field into any H5 record. If the pick feed is missing or stale for more than 60 s, H5 buys halt (fail closed; DEC-024 section 6). A breach is recorded here and the H5 read is reported compromised.
3. **Walk 2 runs to at least 2026-11-06T02**, whatever EXP-022's status, so that EXP-024's Look 2 has its last hour. This does not extend EXP-022's counted window `[2026-10-16T01, 2026-11-06T01)`; hour 2026-11-06T01 stays read only for the exits of counted attempts.
4. **Disclosure.** EXP-024's reads of walk 2 are non-owner reads of an EXP-022 block (ledger rule 3), made only after EXP-022's read has ended.

### Amendment 3 (2026-10-08): E0 record (section 2.1)

No outcome of a counted hour was opened, priced or printed to make this amendment. No parameter, rule, threshold, window, look, correction, gate or seal of this file changes. Two places where this record corrects section 9 or is stricter than section 2.1 are labelled (item 3 and item 5(b)).

Every value below is copied from `/data/mal/exp022/e0-official/e0.json` (sha256 `83e26d70ad52d4aa8afdefc9f3d18a6057005861e66e281c8c898f61d889a03a`) or from the dry-run record named in item 5(a). The scorer inside E0 prices the picks of an exploration day and writes the P&L to its own `rows.csv`; the harness reads only the `mint`, `status` and `reason` columns, and no P&L of that run was read to write this amendment. Labels: **[measured]** is copied from a run record; **[pinned]** is a rule this amendment fixes; **[inferred]** is reasoned.

**1. Result: E0 PASSED [measured].** `ok` is true, `dry_run` is false, `schema` is `cap_pick_e0_v1`, `e0_criterion` is `le60_either_plus_B_picks`.

| Field | Value |
| --- | --- |
| Job and commit | MiScusi job #397, run 2026-10-08 on main `6b9b4fc14bbfb69f04ee1bf2b2c50b1d3cab1572` (the merge commit of #480; `origin_branches` is `["origin/main"]`; the harness refuses a dirty tree). `run` for the pinned view and day, no `--dry-run`. Job facts from the MiScusi job record, relayed by the manager: declared limit 28 GB (Amendment 1(e) had planned 16 GB) and 2 CPU; submitted and started 2026-10-08T20:50:17Z, ended 2026-10-08T21:17:34Z (27 min); MiScusi-reported peak 15 GB (cgroup, includes page cache); exit 0. `check` also exited 0. None of these is in `e0.json`. |
| Record and log | `/data/mal/exp022/e0-official/e0.json`; log `/data/mal/exp022/e0-official.log` |
| View and day | `explore-0814`, `2026-08-20`; both sides boot at 2026-08-20T00:00Z (`boot_ms` `1787184000000`) |
| `VIEW.sha256` | `view_sha256` is the sha256 of each root's `VIEW.sha256` file (table below) |
| Deciding set D | `n_decide_set` `889`; `n_A_decide` `889`; `n_B_decide` `889`; `n_create_ms_disagree` `0`; `n_diff_decide` `0` |
| A = B on D | `md5_A_decide` = `md5_B_decide` = `bed92c12663932ea3696d7a26c8f7b18`; `equal_decide` true |
| C = B picks in U | `md5_C` = `md5_Bpicks_U` = `196cbbd8597c771b6f781cda7657d983`; `n_C` `98`; `n_universe` `772`; `n_Bpicks_in_U` `98`; `n_Bpicks_not_in_U` `0`; `n_picks` (B) `98`; `equal_C` true |
| Universe exclusions (none is a B pick) | `v_outside_band` `258`, `mayhem_unknown_no_create_event` `33`, `v_null_in_vmap` `1`; `excluded_picks_by_reason` `{}`; `picks_not_attempts` `{}` |
| Scorer data counts | `migrations` `1064`; `mints_with_canonical_pool` `805`; `hours` `24`; `bad_reserves` `0`; `bad_json` `0`; `censored` `0`; `tape_coverage_short` `0`; `n_missing_v0` `0`; `skipped_incomplete_migrations` `0`; `scorer_days_skipped` `[]` |
| Input row counts | A: `rows_fed` `10348158`, `create_rows` `40715`, `engine_gate_rows` `938`, `history_rows` `48606`, `staged_files` `27`. B: `trade_rows_t_recv_imputed` `10355611`, `create_rows_t_recv_imputed` `40715`, `history_rows` `48606`, `staged_files` `27`. As in Amendment 1(b), A is fed only the trade rows of mints that have a create in the day, so the two fed counts differ; any decision effect would show in the md5 (`n_diff_decide` `0`). |
| Wall (s) | A `891.4`; B `667.3`; C `73.1`; total `1632.2` |

`view_sha256` (explore-0814):

| Root | sha256 of `VIEW.sha256` |
| --- | --- |
| `w1` | `918051f8e656421ed401a39b2e2e76344ef67c04d9216bbdfa5f76f66e86a016` |
| `w2` | `e8feee31bb8b8cc699aba0dadc95af61ec6341456a7f3db916a5b190e091b23f` |
| `w3` | `09e4d3672fe68c481398c2871068f23dc4653eb6cd6a0cb0f5bfcc0f3c7d9091` |
| `w4` | `f60027edd8a07533a5e030ab276bda5abd31f578cf742df5e5f0e6d88cbe9d22` |
| `w5` | `ac006f35bcbb7c8620da800c6187d958a2304468f27c86ceadbec9583ce4e530` |
| `w6` | `2602e241a038965c5f6793323d8620bd89a8df40b27504d25d522e5f0802b170` |
| `w7` | `346024b50d58e632a122cf138dc788bdd22231cc8e4b269442da463bb3638317` |

- **Check flags, all true [measured].**
  - In `e0.json` `checks`: `A_log_rows_equal_engine_rows`, `boot_history_equal`, `equal_C`, `equal_decide`, `imported_modules`, `no_unexpected_pick_exclusions`, `nonempty`, `pins`, `scorer_picks_in_input`.
  - `python -m tools.cap_pick_e0 check e0.json` exits 0 (reported by the manager after the job). It was re-run for this amendment from a worktree at `ca556aae035a463b6d2cfec4978a41568fe08bae`, which has no diff to `tools/` or `ARTIFACTS/exp012/` against `6b9b4fc`. It exited 0 with `ok` true, and its recomputed parts all true: `criterion`, `equal_C`, `equal_decide`, `n_C_positive`, `no_import_mismatch`, `no_unexpected_pick_exclusions`, `not_a_dry_run`, `sanity`, `scope`, `scorer_mode`. `frozen_md5` ok, `imported_modules` ok with `n` 16, and each of the four pinned modules ok.
- **Report only, never deciding [measured].** `equal_full` is false.
  - Full lists: `n_A_full` `938`, `n_B_full` `938`, `md5_A_full` `d45c9093d00f64a7caab68f456eacb94`, `md5_B_full` `ccec85be7bd7ce351831011cb6f6d740`.
  - Full-list mismatches: `n_gt60` `49` mints, all migrating more than 60 min after create on either side. `n_gt60_in_decide` is `0`. Also `n_picks_A` `103` against B's `98`, and `n_dead_B` `904` (mints created before the restart).
  - `crosstab_gt60` (A label, then B label): A `below` and B `no_features` `44`; A `pick` and B `no_features` `5`.
  - This is the artifact Amendment 1 describes: `replay_rows` runs without a live clock, so A seldom applies the 60-minute drop. The 5 A-only picks are not B picks and are outside D.

**2. Code pins (section 2.1 item 4) [measured].** Blob shas are `git rev-parse 6b9b4fc:<path>`, and each equals the blob recorded in `e0.json` (checked for all 16 modules below when this amendment was written).

- **The four pinned blobs at `6b9b4fc14bbfb69f04ee1bf2b2c50b1d3cab1572`:**

  | Path | Blob |
  | --- | --- |
  | `tools/forward_exp012_gate.py` | `c6868cb731d21123f002b3c2c8d3b51d60f7d5c0` |
  | `tools/forward_paper.py` | `49cdfc61cfef264adb055821ba3b7a9bb33eb54d` |
  | `tools/exploration_entry_model.py` | `6e3d33b1a710ace149afbccc0e823b6a12e70ec0` |
  | `tools/cap_pick_gate_replay.py` | `7d208874c0306ae880b847c38daabffe9f8086b3` |

- **`ARTIFACTS/exp012/FROZEN.md5`:** md5 `a01f05dfb1e622f78b2bba55d174be09` (expected `a01f05dfb1e622f78b2bba55d174be09`; `ok` true).
- **Every `tools.*` module the run imported** (`imported_module_blobs`; `n_imported_modules` `16`; `imported_module_mismatches` `[]`; `scorer_imported_module_blobs` `{}`, because the scorer ran from the same tree):

  | Module | Blob at the E0 commit | Imported by | Read-tool rule |
  | --- | --- | --- | --- |
  | `tools.cap_pick_e0` | `6d8d29383e1253b67f9ac955a7cd33d6f0df14b0` | A and B | recorded; harness only, not imported by the read |
  | `tools.cap_pick_gate_replay` | `7d208874c0306ae880b847c38daabffe9f8086b3` | A and B | pinned (section 2.1 item 4) |
  | `tools.cap_pick_score` | `b8e37774c4968e60345eb24a91ab2fefc4990464` | scorer | recorded; the read mode edits this file by design (6.1) |
  | `tools.exploration_entry_model` | `6e3d33b1a710ace149afbccc0e823b6a12e70ec0` | A and B | pinned (section 2.1 item 4) |
  | `tools.exploration_exits` | `42097768137131a7dd90a2240c8d3349676a72c1` | A and B | pinned |
  | `tools.forward_exp012_gate` | `c6868cb731d21123f002b3c2c8d3b51d60f7d5c0` | A and B | pinned (section 2.1 item 4) |
  | `tools.forward_paper` | `49cdfc61cfef264adb055821ba3b7a9bb33eb54d` | A and B | pinned (section 2.1 item 4) |
  | `tools.funding_graph` | `2cb8c030497db37ddee95262042738cea482c199` | A and B | pinned |
  | `tools.graduated_swing` | `2f7ee02bf7fc03f00bb3687bc9a4bf0ec1fd9f8a` | A and B | pinned |
  | `tools.latency_curve` | `c194134b199d2d772a57d5a6a6cb3c5c3d25d669` | A, B and scorer | pinned |
  | `tools.laya_v0` | `1f61d5d408e8a14bea39dda82beca47c6d2baae6` | A and B | pinned |
  | `tools.paper_curve_math` | `42daf5adcdc1532cb29a6f5079997d25b10c86ea` | A, B and scorer | pinned |
  | `tools.paper_fail_pressure` | `2a0d11e4462531e6e947a14f39d3afbad847caf5` | A and B | pinned |
  | `tools.paper_price_path` | `8f551535acaa048996f110f4f30004d37b629b3b` | A, B and scorer | pinned |
  | `tools.paper_tape_scoreboard` | `1955f45c4146e628ba0208cc3317a0f4f147aaf0` | A and B | pinned |
  | `tools.tape_lines` | `810af3bc7520225bf861c70258fcfaf5914bab08` | A, B and scorer | pinned |

- **Environment [measured].** Python `3.12.3` (`e0.json` `python`; `/data/mal/venv`, base interpreter `/usr/bin/python3.12` per `pyvenv.cfg`), lightgbm `4.7.0`, numpy `2.5.3`. The harness does not record the two package versions. They were read from `/data/mal/venv` with `python -I` on 2026-10-08 after the run. `site-packages` was last modified 2026-10-04T03:02:37Z, before the run, and the lightgbm and numpy `dist-info` directories date from 2026-10-01.
- **Read-tool rules [pinned].** Section 12's "modules it imports hash to their recorded values" means these:
  1. Each look runs in a worktree at a recorded commit where the four blobs, the ten other modules marked "pinned" and the md5 of `FROZEN.md5` equal the values above, both at `HEAD` and as the file Python loaded. The read tool refuses otherwise, and refuses a pinned module that is untracked or outside the worktree.
  2. `tools.cap_pick_score` is the read tool's own file, so its blob differs from E0's by design. The read tool's merge-commit blob goes in the dated amendment that section 12 ("Commits") requires, and E0's blob `b8e37774c4968e60345eb24a91ab2fefc4990464` is the reference for 6.1. `tools.cap_pick_e0` is the harness; the read does not import it. Both are recorded as references and are not pinned for the read: `tools.cap_pick_score` because the read mode is an edit to that file, and `tools.cap_pick_e0` because the read never loads it.
  3. A `tools.*` module the read imports that is not in this table is recorded with its blob in that same amendment. Each look and the read tool run in a worktree at a recorded commit where every pinned module's blob equals the value recorded here. The read tool refuses if any differs. Changes on `main` after the E0 commit `6b9b4fc` do not reach the read (section 2.1 item 4). This applies to all 14 pinned modules: the four section 2.1 blobs and the ten others marked "pinned".
  4. The read tool records the Python, lightgbm and numpy versions at every look and refuses if any differs from the values above. A venv change needs a dated amendment, and an E0 re-run in the new environment, before the next look. The decision lists include `repr(score)`, so a library change can alter an md5. [This is a fail-closed choice made by this amendment; section 10's "Decision stability" would catch a drift only after the fact.]
  5. The model md5 is read from the `FROZEN.md5` file at run time, and module constants can be edited at run time (notebook `n_XhHuUT1hSpMCkw`). Rules 1 and 4 are what stop either from changing silently.

**3. Boot-staging hours for walk 2's first boot [read from the code and the #462 body and notebook `n_XhHuUT1hSpMCkw`; no forward-1002 file was opened; corrects the text of section 9].**

Section 9 says the first boot reads "the creates of `[2026-10-15T00, 2026-10-16T01)` and the prints of hour `2026-10-16T00`". The real list starts three hours earlier:
- **Staged creates.** `stage_creates` stages 27 forward-1002 creates hours for the 2026-10-16T00 boot: **2026-10-14T21, 2026-10-14T22, 2026-10-14T23, then 2026-10-15T00 to 2026-10-15T23.** The window is `boot - HIST_KEEP_MS - 1 h`, with `HIST_KEEP_MS` 25 h 5 min.
  - `Exp012Online.preload` opens 26 of them (2026-10-14T22 to 2026-10-15T23). The 2026-10-14T21 file is staged, hashed and scanned, but none of its rows reach the history, because preload keeps rows from 2026-10-14T22:55.
  - The E0 day's boots also stage 27 files (`staged_files` `27` on both sides), so the count is exercised on the exploration layout.
- **Feed hour 2026-10-16T00.** The replay also feeds this forward-1002 hour: its creates (so mints created in it are in the library and get a decision) and its trades. The creates grant is `[2026-10-14T21, 2026-10-16T01)`. The trades grant is the single hour 2026-10-16T00.
- **Second boot.** The 2026-10-17T00 boot reads forward-1002 creates 2026-10-15T21 to 2026-10-16T00 (the rest is walk 2). From the 2026-10-18T00 boot on, no forward-1002 file is read.
- **Code.** `_staging_hours` and the grant constants `FWD1002_CREATES_HOURS` `("2026-10-14T21", "2026-10-16T01")`, `FWD1002_TRADES_HOURS` `("2026-10-16T00",)` and `FWD1002_BUFFER` `("2026-10-14T01", "2026-10-16T01")` in `tools/cap_pick_gate_replay.py` (blob `7d208874c0306ae880b847c38daabffe9f8086b3`). `HIST_KEEP_MS` in `tools/forward_exp012_gate.py` is `CREATOR_LOOKBACK_MS` (24 h, from `tools/exploration_entry_model.py`) plus `DROP_AFTER_CREATE_MS` (60 min) plus 5 min.
- **All of these hours are inside the disclosed buffer `[2026-10-14T01, 2026-10-16T01)`** (DEC-021 section 3, EXP-012 Amendment 1). They are creates, plus the trades of hour 2026-10-16T00, read in grant mode, which refuses until the FINAL marker exists (`require_final`, #462).
- **They are read at the 10-16 and 10-17 boots and at every look,** because each look re-derives every decision from the window start (section 10, "Decision stability").

**4. Where the E0 C path (exploration adapter) differs from the read (walk2 adapter) [from #479's body, merge commit `d6b32af`; the scorer blob at E0 is `b8e37774c4968e60345eb24a91ab2fefc4990464`].** E0's C step runs `--exp022 --exp022-source exploration`. The read runs `walk2`.

| # | Item | E0 C path (exploration) | The read (walk2) |
| --- | --- | --- | --- |
| 1 | Canonical pool | No migration event on the tape: the earliest V-band pool by s0, ties by pool name (G's rule) | The `pool` of the mint's `migration` event (inner-event rows included). The two differ when a mint has an earlier out-of-band pool and a later in-band one, or when the event's pool is not the earliest-printing pool |
| 2 | WSOL | The pool's V is in `[17.5e9, 17.7e9]` in the static map (a proxy). A non-WSOL pool with V in the band would be an attempt (tested: `MintUsdc`) | The event's `quote_mint` is WSOL |
| 3 | V0 | The static map `pool_v_0909.json` (an account read from 2026-10-06), not the V at the decision print | `virtual_quote_reserves` of the pool's first print (within-slot order slot, tx_index, event_index), the tape's pre-trade convention |
| 4 | Missing V0 | Cannot occur. A pool with a null V in the map is excluded (`v_null_in_vmap`) because WSOL cannot be shown for it | Kept and scored at 17.5e9 and 17.7e9, taking the lower P&L per leg. `exp022.universe.missing_v0` reports the inputs of the look rule |
| 5 | Counted window | None | s0 block_time in `[2026-10-16T01, 2026-11-06T01)` (a constant; `window` is a function argument for a narrower look end) |
| 6 | Tape coverage (s0 + 6,900 slots) | Still drops (`censored_tape_coverage`, G's rule). Here the count is `0` | Report-only (`exp022.universe.report_only.tape_coverage_short`), because section 3 does not list it. So the E0 universe is smaller than the read's at the end of a day |
| 7 | Pinned inputs | `--vmap` must be `/data/mal/pumpswap-virtual/pool_v_0909.json` (sha256 `70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e`, `pools_in_band` `46534`); `--hour-sph-json` must be absent, so every hour is measured on its own tape rows and an unmeasurable hour refuses the run | No pin yet: the sealed slot-span source is the read tool's. The adapter is fixture-only today |

  - **Same on both adapters:** the 80 s rule from the complete event's block_time replaces the draft's 400-slot rule (inclusive: 80 s in, 81 s out); mayhem is taken from the create (a mint with no create row is excluded as `mayhem_unknown_no_create_event`, and creates are read for the day and the two hours before it); `day` is the UTC date of s0's block_time; a mint whose s0 or complete block_time is missing is excluded as `block_time_missing`, an exclusion and not a refusal.
  - **Bad-reserves picks** stay `status=attempt` in `universe.csv` with `priced=false` and `unpriced_reason=bad_reserves` on both adapters. They have no row in `rows.csv`, so the harness's C md5 would differ. `bad_reserves` was `0` on the E0 day.

**5. Disclosures.**

- **(a) Same-day dry-run precount.** A count-only dry run (`dry_run` true) on the E0 view and day was made before the official run. It is a second look at the E0 day, beyond Amendment 1's 2026-08-17 dry run. It is the only other run on 2026-08-20 that this amendment's sources record. It passed with the values below, so it is not a failed E0 followed by a retry. The code differences between the two runs add pins, columns, record fields and a guard; they change no deciding parameter and no A, B or C decision path [inferred from the diffs of `tools/cap_pick_score.py`, `86355b3` to `6b9b4fc`, and `tools/cap_pick_e0.py`, `5485459` to `6b9b4fc`].

  | | Precount | Official |
  | --- | --- | --- |
  | Harness commit | `54854590e28645c53b0a65fa59bbb3fbe074b6f8` | `6b9b4fc14bbfb69f04ee1bf2b2c50b1d3cab1572` |
  | Scorer commit | `86355b3083fde710e20c324eac15839881e71142` (from a separate clean checkout via `--scorer-repo`) | `6b9b4fc14bbfb69f04ee1bf2b2c50b1d3cab1572` (same tree) |
  | Scorer blob | `42d48c04b7f6513034a49cd7a952bdd04782c1a9` | `b8e37774c4968e60345eb24a91ab2fefc4990464` |
  | `md5_A_decide` = `md5_B_decide` | `bed92c12663932ea3696d7a26c8f7b18` | `bed92c12663932ea3696d7a26c8f7b18` |
  | `md5_C` = `md5_Bpicks_U` | `196cbbd8597c771b6f781cda7657d983` | `196cbbd8597c771b6f781cda7657d983` |
  | `n_C` / `n_universe` / `n_picks` / `n_picks_A` | `98` / `772` / `98` / `103` | `98` / `772` / `98` / `103` |
  | `md5_A_full` / `md5_B_full` | `d45c9093d00f64a7caab68f456eacb94` / `ccec85be7bd7ce351831011cb6f6d740` | `d45c9093d00f64a7caab68f456eacb94` / `ccec85be7bd7ce351831011cb6f6d740` |
  | `crosstab_gt60`, `n_gt60` | `{"below": {"no_features": 44}, "pick": {"no_features": 5}}`, `49` | `{"below": {"no_features": 44}, "pick": {"no_features": 5}}`, `49` |
  | Wall (s) A / B / C / total | `1057.2` / `714.6` / `81.6` / `1853.8` | `891.4` / `667.3` / `73.1` / `1632.2` |

  - **Every deciding value equals the official one.** The decide md5, the C md5, `n_C`, `n_universe`, the full-list md5s and the crosstab are the same.
  - **What differed.**
    - The scorer. Between its blobs the only change to `tools/cap_pick_score.py` is commit `be7cdb2` (quant-proof edits 1 and 2 on #479, comment 6068166437). It pins `--vmap` and `--hour-sph-json` for the exploration source and adds the `priced` and `unpriced_reason` columns to `universe.csv`. The precount already used the same map (path and sha256 `70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e`) and had `bad_reserves` `0`, so no value moved on this day.
    - The harness. Per #480's body, the later commits add record fields, a merge of `main` and the pick-exclusion guard (item (b)). The A, B and C code paths are unchanged. The precount's `imported_module_blobs` equal the official ones, except `tools.cap_pick_e0` and `tools.cap_pick_score`.
    - `picks_sha256` of the B file: `22ef9459ea97e3d349085cef388654de76953eb6b9a7ac9733b0e97e619e0ead` for the precount and `15b822648061e7ce63ce47a88234f265fda298262513885a2e87ca3e83ff0c5a` for the official run. The two B files differ only in `wall_s` of the meta line (`714.6` against `667.3`); the other 1,842 records were compared line by line when this amendment was written and are identical.
  - The precount's `e0.json` is in a scratch directory and is not an E0 record. The official run is.
- **(b) The empty B-pick allowlist is a TIGHTENING of section 2.1 item 3 [pinned by #480].** Item 3 compares C with B's picks restricted to the section 3 universe, so a pick that section 3 excludes would drop out of both sides. The harness's `PICKS_NOT_ATTEMPT_ALLOWLIST` is empty. A B pick that is not an attempt, for any reason (a `picks_not_attempts` entry, or an excluded row of `universe.csv`), fails E0, and `mayhem_unknown_no_create_event` fails even if it were ever added. B decides only mints that have a create that day, so such a pick would be a scorer or layout error. `e0.json` records `picks_not_attempt_allowlist` `[]`, `forbidden_pick_reasons` `["mayhem_unknown_no_create_event"]`, `excluded_picks_by_reason` `{}` and `unexpected_pick_exclusions` `{}`. This is stricter on the E0 day only. In the read, a pick that section 3 excludes is still excluded, counted and reported.
- **(c) The harness pins the scorer mode.** `SCORER_FLAGS` is `--exp022 --exp022-source exploration --book picks`, a module constant that is not a CLI option. A `--scorer-arg` that names or abbreviates `--exp022`, `--exp022-source`, `--book`, `--picks`, `--only-day` or `--out-dir` is refused, and `--scorer-arg` and `--scorer-repo` need `--dry-run`. In this run `scorer_extra_args` is `[]`, and the `e0.json` fields `scorer_flags` and `scorer_summary.mode` (`exp022`) and `.source` (`exploration`) are recomputed by `check`. The C command ran the seven `explore-0814` roots, `--only-day 2026-08-20`, with `-X importtime`.
- **(d) The `pick_oracle` and the H5 seal during counting (Amendment 2 item 2; DEC-024 section 6).** From 2026-10-16T01 to the end of EXP-022's read, no H5 trade (live or paper) is taken on a mint the EXP-022 gate picked, and no H5 record is joined to a pick. H5 reads only the `mint` of the gate's decision-time intents into an in-process set. If the pick feed is missing or stale for more than 60 s, H5 buys halt. Per #484's PR body [from PR body; code not read] (the H5 executor; a draft, open at head `e436b28`, not on `main`), its seal rule refuses all buys from 2026-10-16T01:00Z until a boolean pick oracle exists, and the oracle reads only after the DEC-016 FINAL (the `FINAL_WRITTEN` marker file). It also refuses a mint on an oracle error, a non-boolean answer or an undecided mint; `pick_oracle(mint) -> bool` returns a boolean only, and a pick is refused. `docs/HANDOFF.md` says no boolean picks exporter exists yet, so live refuses every buy in the window until one does. E0 does not test this. It binds H5, not the EXP-022 read, and is listed here because Amendment 2 item 2 makes it part of CAP-PICK's seal.
- **(e) What E0 does not show.**
  - It runs the exploration adapter only. The walk2 adapter, the event-V decode and the migration events have never run on real data (item 4, row 7).
  - No B pick was excluded by section 3, so E0 does not test what the scorer does with a pick that section 3 excludes. #479's fixture tests do.
  - A and B both impute `t_recv_ms` as `block_time * 1000` (zero receive lag, disclosures 11 and 12). E0 compares two replays of one gate path, not live runner output, which stays sealed until the FINAL.
  - The 60-minute boundary past 60:00 is untested (Amendment 1). One view and one day were run.
  - E0 is an equivalence check. It says nothing about edge.

**6. What the read tool (P4) must do before 2026-10-23T01 [pinned].** These add to section 12 and loosen nothing. Quant-proof on the read tool's PR checks each one. A look does not run without them (section 10, P4).

- **6.1 Reproduce E0's C md5 at its merge commit.** At the read tool's merge commit, run E0's C step again on E0's inputs: the seven `explore-0814` roots with the `VIEW.sha256` hashes in item 1, `--only-day 2026-08-20`, the B file `/data/mal/exp022/e0-official/B.jsonl` (sha256 `15b822648061e7ce63ce47a88234f265fda298262513885a2e87ca3e83ff0c5a`), the map in item 4 row 7, and `--exp022 --exp022-source exploration --book picks`. Expected: the sorted attempt-mint list has md5 `196cbbd8597c771b6f781cda7657d983`, `n_C` `98`, `n_universe` `772`, exclusions `v_outside_band` `258`, `mayhem_unknown_no_create_event` `33` and `v_null_in_vmap` `1`, and no B pick outside U. The result goes in the read tool's PR and in the amendment that records its merge commit. This tests that the shared code did not regress; it does not test the walk2 adapter.
- **6.2 Open items from #479's body ("Open for the read tool (P4)", comment 6068166437):**
  1. Section 10: an unmeasurable hour must be EXCLUDED and counted against the 5% bad-hour ceiling, not refused. Its fallback must come from the sealed verify line. `--hour-sph-json` is unpinned for walk2 and sets k and the lag.
  2. walk2 `tape_coverage_short`: exclude under section 10's coverage rule. Today such an attempt is priced at the last state plus rent.
  3. A missing s0 block_time should refuse (section 4.4). Today it is an exclusion (`block_time_missing`).
  4. Bad JSON lines are counted, not refused (section 10). `bad_reserves` has no exclusion or refusal rule yet; it is only flagged (`priced=false`).
  5. The missing-V0 top-3 check ranks the min variant. It should rank the higher variant, so that NOT_DECIDABLE is not weakened. Fitting the pressure intercept on the lower-P&L variant is acceptable (ssb and nearby do not depend on V; only a guard flip changes membership). The read tool's amendment pins that choice.
  6. The walk2 source allowlist: a section 12 hour allowlist, a source flag and a pin of the walk2 inputs. The cutoff (2026-10-02T10) and the "forward" path guard refuse walk-2 hours, so the walk2 adapter is fixture-only today. It also needs an outcome-blind, real-layout, count-only precount.
  7. Conflicts #479 raised: refuse versus exclude for an unmeasurable hour (item 1 above), and V0 "at the decision print" read strictly (if the pool's first print carries no V, V0 is missing even when a later print has one).
  8. Not built in #479, and section 12's own list: the section 8 correction (needs E1, n at least 20); the 24 h block clusters for the day-level t (7.2 vi); looks, alphas, futility, attempt coverage by hour, the bad-hour ceiling, bad-line refusal, decision stability and every section 12 refusal and hash; the section 13 report-only lines; the section 4.1 second source parsed from the verify line.
- **6.3 Open items from notebook `n_XhHuUT1hSpMCkw` (the #462 merge, quant-proof comment 6066526609):** Each is implemented in the read tool. `tools/cap_pick_gate_replay.py` stays at blob `7d208874c0306ae880b847c38daabffe9f8086b3` (rule 1).
  1. Turn a `Refused` into exit 3, and treat any other exception as an aborted look. Exit code 3 comes only from the CLI; grant mode is called from Python.
  2. Refuse when `final_ledger_overridden` is set.
  3. Pass days sorted, unique and contiguous (a repeated day raises `FileExistsError`).
  4. Count `replayed_hours_not_in_grant`, `replayed_hours_without_creates` and `boots[].staging_hours_not_in_grant` as bad hours under section 10.
  5. Require good creates hours across `[create - 24h, create)` for attempt coverage, because `creator_prior_mints_24h` depends on them.
  6. Hash its imports and record the Python, lightgbm and numpy versions (item 2, rules 1 and 4).
  7. Also from #462's body: read `meta.get("lenient_lines_skipped", 0)` (the key is absent when 0); and check the FINAL marker predicate against the real ledger entry once it exists, because it was only checked against the writer's code.
- **6.4 From this amendment:** the pin rules in item 2, and the hours in item 3 as the exact forward-1002 allowlist (creates `[2026-10-14T21, 2026-10-16T01)` and the trades of hour 2026-10-16T00 only), with every other forward-1002 hour refused.

**Unchanged:** the book; the pick rule and threshold; the counted window and looks; the seal; the correction; the gate; items 1 to 3 of section 2.1 as written (item 5(b) is stricter on the E0 day only); and the pinned line in section 0.

### Amendment 4 (2026-10-09, before any counted hour): EXP-025 (C1-NF) reads walk 2 while EXP-022 counts

No CAP-PICK outcome was computed or read to make this amendment. EXP-022's thresholds, counted window, pinned counting-start line, seal (section 9), looks, correction and gate are unchanged.

[EXP-025](EXP-025-c1nf-part1-prereg.md) (C1-NF, [DEC-025](../DEC/DEC-025-c1nf-family.md)) is a separate DEC-014 family in its own alpha slot. If DEC-025 is merged, it reads walk-2 chain tape `[2026-10-16T01, 2026-10-24T02)` for its counted decisions `[2026-10-16T00, 2026-10-24T00)` **while EXP-022 is counting**. Ledger rule 3 requires this disclosure. To keep section 9:
1. **Pick exclusion.** From 2026-10-16T01 to the end of EXP-022's read, every mint whose canonical pool first prints at or after 2026-10-16T01 is looked up with the boolean `pick_oracle(mint)` of Amendment 2 item 2 and Amendment 3 (d). A pick is removed from C1-NF's universe before its pass A runs, so no grid, candidate or outcome row is ever computed for it. The oracle's source is the union of the live intents and EXP-022's replay pick decisions at E0 commit `6b9b4fc14bbfb69f04ee1bf2b2c50b1d3cab1572` (Amendment 3), held as booleans only. C1-NF never prices a CAP-PICK pick, writes no CAP-PICK field into any C1-NF record, and joins none.
2. **Fail closed.** A missing, stale, erroring, non-boolean or undecided oracle makes C1-NF's read NOT_DECIDABLE. It does not change anything in EXP-022.
3. **No outcome opened.** C1-NF opens no CAP-PICK outcome row, no paper-twin field, and no `LOOK_READS.jsonl` field other than what the oracle contract defines. It reads chain tape only.
4. **No effect on EXP-022.** EXP-022's picks, looks, gate and the walk-2 run to at least 2026-11-06T02 are unchanged. The exclusion costs C1-NF volume, not EXP-022 anything.
5. **Order.** C1-NF has two looks. Its Look 1 reads walk-2 chain tape `[2026-10-16T01, 2026-10-17T02)` about 2026-10-17T03Z, before EXP-022's look 1 (day 7, 2026-10-23T01). Its Look 2, only if Look 1 did not pass, reads to 2026-10-24T02 about 2026-10-24T03Z, between EXP-022's look 1 and look 2. Neither prints a CAP-PICK field, and neither report is an input to any CAP-PICK look.

### Amendment 5 (2026-10-09, before any counted hour and before the 2026-10-10T06:41Z daily A3 run): P2 is read as the last run; an erratum on the BOOST pin citation; the synthetic handling is not amended here

No CAP-PICK outcome was computed or read to make this amendment, and no row of walk 2, forward-1002, forward-paper or a runner was opened. EXP-022's thresholds, counted window, pinned counting-start line, seal (section 9), looks, correction and gate are unchanged. **Line numbers below are those of this file on main at `ecdc7af`, before this amendment.**

**Context (disclosed).** The daily A3 run of 2026-10-09T07:11:06Z (job #445) halted on `pins_changed` alone: the pump, PumpSwap and fees programs were redeployed at 2026-10-08T16:20Z. PR #517 (merged as `82cd674`) re-pins them and pins the three programdata hashes. At that run `synthetic_share_high` read 6/19 = 0.316, below the 0.35 line. One dry check, at 07:38:11Z, read 7/19 = 0.368 and halted on that flag. **The dry check is not the P2 run:** it used the unmerged pins of PR #517 and a scratch `--out`. It is disclosed here as it is in [EXP-024](EXP-024-h5-boostfloor-part1-prereg.md) Amendment 4, and it is not counted as an A3 run.

**1. Clarification: P2 is the last run (not a change).** P2 (section 10, line 320) names the test: "The A3 monitor's **last** daily run before the first counted hour has no halt, and no core rule went unevaluated on two consecutive days." Section 11 (line 358, "Before counting. A halt withdraws CAP-PICK") is the consequence of that test. It is not a second, stricter test that every earlier run must pass. So:
- **A reviewed re-pin plus a clean last run cures `pins_changed`.** The halt of 2026-10-09T07:11:06Z is cured by PR #517 (merged as `82cd674`) if the last run before 2026-10-16T01:00Z shows no halt.
- **The reading cuts both ways.** A halt in the last run before the first counted hour withdraws CAP-PICK, whatever ran clean earlier.
- The core-rule clause (two consecutive daily runs, line 357) is unchanged.
- This is quant-proof's ruling of 2026-10-09, as the manager relayed it. It is recorded here before the 2026-10-10T06:41Z daily run. EXP-024 Amendment 4 records the same reading for its P2.

**2. Erratum: the BOOST pins are cited by key.** Section 11, line 356 cites the BOOST pins at `tools/pump_structure_pins.json:41-42`. After PR #517 adds the six programdata hash lines, the same two pins sit at lines 47-48. **The values are unchanged: 17.585 SOL and 29 slices.** Read line 356 as citing the keys `boost.budget_sol` (17.585) and `boost.slices` (29) of `tools/pump_structure_pins.json`, whatever their line numbers. This is a citation fix with no change of value, and it holds on main from `82cd674`.

**3. Not amended here: the synthetic handling. It is open.** This file keeps `synthetic_share_high` (above 0.35) among the halt flags (line 354), treats a synthetic pool that meets the conditions as an attempt (line 121, a reject at -55,000 lamports if it opens above the guard) and reports the count of synthetic pools (line 420). Quant-proof ruled that EXP-022 needs **its own separate amendment**, not a copy of EXP-024 Amendment 4, and that it must merge before the 2026-10-15T06:41Z daily run. **That amendment is open and is not made here.** Until it merges, those lines stand as written.

### Amendment 6 (2026-10-10, before any counted hour; the manager's decision): the pick feed as built, and the replay-only residual

**Written** 2026-10-10T09:01:46Z (`date -u`). That is before 2026-10-16T01:00Z, and before the exporter job or a reinstalled H5 executor is deployed: PR #509 (head `3a6dae3`) and PR #540 (head `e7312eb`) are open and unmerged at this instant. The text is quant-proof's ruling on those two heads (2026-10-10). Implemented and cleared by quant-proof at #509 `8b3ce7c` and #540 `4feec6d`.

No CAP-PICK outcome, paper-twin field or gate-log row was read to make this amendment. EXP-022's book, pick rule (section 2), counted window, looks, correction, gate and seal (section 9) are unchanged.

1. **The feed.** Amendment 2 item 2 and Amendment 3 (d) say H5 reads only the `mint` of the decision-time intents. The feed as built is described in DEC-024 Amendment 6, from PR #509 and PR #540:
   - An exporter reads the `mint` and `entered` of the frozen book's gate rows and the `mint` of its intents. It does so only after the DEC-016 FINAL marker exists and 2026-10-16T02:00:00Z, and it writes booleans.
   - H5 and C1-NF read the booleans, with a staleness limit of no more than 60 s.
   - False needs a scored gate row. Anything else is undecided and is refused (fail closed).
   - The exporter prints no count that includes intents-file lines.
2. **Meaning of "picked" in Amendment 2 item 2.** A mint the oracle answers True for at the H5 decision. The read's picks (section 2) are the walk-2 replay's picks and differ from the online set both ways (section 17 item 12). An H5 trade or shadow record on a replay-only pick, made under item 1, is a declared residual and not a breach.
3. **No join before a look.** Before each look, no person, agent or job joins H5 records or the H5 wallet history to the replay's picks, or counts the overlap. A join is a breach under section 9.
4. **Report-only count (section 13; never deciding).** After each look is written, report two numbers: the look's counted picks with an H5 live fill, and those with an H5 shadow outcome record. The report also says that H5's live fills on those mints are on the tape the read prices.

### Amendment 7 (2026-10-10, before any counted hour and before the 2026-10-15T06:41Z daily A3 run; DRAFT, the owner's decision is OPEN): synthetic-migration pools stay in the universe, `synthetic_share_high` becomes report-only, and the class is sealed to counts

```
OWNER_SYNTHETIC_DECISION_EXP022: OPEN
```

**The owner line is OPEN.** The owner made both earlier synthetic decisions, one family at a time, and they went opposite ways. H5 excludes synthetic pools (MiScusi notebook `n_xtknDqL-ychBNg`; [EXP-024](EXP-024-h5-boostfloor-part1-prereg.md) Amendment 4). C1-NF keeps them (`OWNER_SYNTHETIC_DECISION_EXP025` in [EXP-025](EXP-025-c1nf-part1-prereg.md) Amendment 2). Neither decision covers EXP-022. Item 0 gives the question to put to the owner. Until the line above records the owner's answer, nothing in this amendment applies.

**Written** 2026-10-10T11:03Z (`date -u`). That is before 2026-10-16T01:00Z, so no counted hour exists. **Line numbers below are those of this file on main at `13fa26c`, before this amendment.**

**This amendment is blind to every CAP-PICK outcome. It is not outcome-blind.**
- No CAP-PICK outcome of a counted hour exists, because counting starts at 2026-10-16T01 (section 0).
- To write it, the author opened no row, report or scratch file of walk 2, forward-1002, forward-1002ev, forward-paper, the paper twin, a runner, the pick oracle, the H5 shadow or canary, or C1-NF. The author computed no CAP-PICK, H5 or C1-NF outcome.
- **Inputs:**
  - this file;
  - EXP-024 Amendment 4 and its Clarification 1;
  - EXP-025 Amendment 2;
  - DEC-026 §9.3;
  - `tools/pump_structure_monitor.py` and `tools/synthetic_class.py`;
  - `docs/HANDOFF.md`;
  - the audit (SYN, JUDGE);
  - the MiScusi notebook entries on the synthetic decisions;
  - the four records of `/data/mal/structure-monitor/daily.jsonl`. Of those records, only `run_utc` and `halt.flags.synthetic_share_high` were read.
- **The pick set was not classified.** No CAP-PICK pick on any October hour was classified, counted by class or opened. Nobody knows what share of CAP-PICK's picks is synthetic.

**What was in view (disclosed)** [measured; `/data/mal/structure-monitor/daily.jsonl`, from the `synthetic_share_high` reason string]

| `run_utc` | Job | `synthetic_share_high` | Under line 354 |
|---|---|---|---|
| 2026-10-08T10:25:10Z | #383 | 0/16 = 0.000 | clear (before the 2026-10-08T16:20Z redeploy) |
| 2026-10-09T07:11:06Z | #445 | 6/19 = 0.316 | clear |
| 2026-10-09T19:23:02Z | #449 (EXP-024's official P2 run) | **6/17 = 0.353** | **fired** |
| 2026-10-10T07:11:07Z | #478 | 4/18 = 0.222 | clear |

- **The 19:23:02Z run fired `synthetic_share_high`.** Under line 354 that run was a halt. It does not withdraw CAP-PICK, because P2 reads only the last run before 2026-10-16T01:00Z (Amendment 5 item 1). It is disclosed here because the pre-10-16 checklist lists only the 0.316 and 0.222 readings.
- The dry check of 2026-10-09T07:38:11Z read 7/19 = 0.368. It is not an A3 run (Amendment 5).
- **H5's structure measurement** (counts only; `/data/mal/hunt-1008/h5-work/synthetic-1009/REPORT.md`, notebook `n_KqwGD1bzt_lbpg`; this author read the notebook summary):
  - 35.0% of 160 graduations after the redeploy were synthetic.
  - On the same set, the monitor's 10-signature window reads 28.6%, so the monitor undercounts.

#### 0. The choice, and the question for the owner

**Why a choice is needed** [inferred]
- As written, this file keeps synthetic pools in the book (line 121: "attempts … never dropped") and halts on their share (line 354).
- With the share near the 0.35 line, the halt mostly measures sampling noise. The monitor samples 16 to 19 graduations, so one more synthetic pool moves the reading by about 0.06.
- **Binomial estimate** [inferred, not a forecast]. Assume each run is an independent draw at a fixed share:

  | True share in the monitor's sample | One run fires (n = 16–19) | At least one of 21 daily runs fires |
  |---|---|---|
  | 0.20 | 0.05–0.11 | 0.67–0.90 |
  | 0.25 | 0.14–0.24 | 0.96–1.00 |
  | 0.30 | 0.28–0.40 | about 1.00 |

- **Before counting:** if the flag fires on the last run (the 2026-10-15 run), CAP-PICK is withdrawn (P2).
- **After counting starts:** if the flag fires on any daily run, counting is suspended and every later look is NOT_DECIDABLE (lines 359-365).

**Where the halt came from (disclosed).**
- The audit's A3 rule set says "synthetic share >35% for 2 days" (SYN:258, D11). Line 354 adopted the monitor's single-run flag instead.
- The audit's risk table says what to do if synthetic migration is adopted: "The guard rejects more and whales vanish from the decoded tape. Re-specify the guard against the pool-open price, as a new EXP" (SYN:554).
- **Only option (c) below follows every line of this file and the audit's plan.** Options (a) and (b) both drop the halt, after the readings above were seen.

**The options**

| | (a) Keep the pools; the flag is report-only; class-count seal | (b) Exclude the pools with the classifier | (c) No amendment |
|---|---|---|---|
| Line 121 ("attempts … never dropped") | Stands as written | Replaced: synthetic and unclassified pools are dropped | Stands |
| JUDGE §4 item 6, binding (JUDGE:205, "Synthetic-migration pools above the seed count as rejects at −fee") | Kept | Overridden | Kept |
| Line 354 halt | Report-only | Report-only (the class is out of the book) | A halt, as written |
| Book = live-gate picks + on-chain min_out (section 1, section 4.3) | Unchanged | Gains a filter that neither the gate nor `tools/probe_executor.py` has. A live CAP-PICK would first need a decision-time classifier with its own equivalence proof, as H5's executor got (#519) | Unchanged |
| New refusal | None | NOT_DECIDABLE if unclassified attempts are more than 1% of the look's attempts (EXP-024 Am.4 B3) | None |
| Read tool (P4, due 2026-10-23T01) | Classifies for counts only. A failed fetch prints `unclassified` | Classifies every attempt before scoring. `getTransaction` and the signature walks are on the deciding path | No change |
| Attempts | Unchanged | Fall by the synthetic share of picks (not known) | Unchanged |
| Population read | October's, including a class that the exploration evidence never contained (0 of 61 graduations before the redeploy) | Matches the exploration evidence (0 synthetic pools) | October's, until the near-certain halt |
| Likely end | A verdict at a look | A verdict, or NOT_DECIDABLE on unclassified pools | Withdrawn at P2, or halted after counting starts. Then A12, or a new EXP (SYN:554) |

**Of (a) and (b), (a) fits EXP-022's text.**
1. **Line 121 already answers the question.** It was merged on 2026-10-08 at 15:14Z (#464, `30aafef`), about an hour before the 16:20Z redeploy. At that time synthetic migration was "deployed but unused (0/10, 0/37). If adopted, pools open above the seed" (SYN:104). The file chose then to keep such pools as attempts, to price them through the guard, and never to drop one. That choice carries out binding JUDGE §4 item 6. Option (b) reverses both. Option (a) keeps both.
2. **The book is the live gate's picks with an on-chain min_out** (section 1, section 4.3), and neither the gate nor the executor takes a class input. Under (a), the paper book stays what a live CAP-PICK would send. Under (b), it does not.
3. **(a) is the smaller change.** It touches the halt list only. (b) also rewrites the universe, adds a refusal and puts RPC classification on the read's deciding path.

**What (a) gives up, stated plainly.** Line 354 was the file's halt for a rise in the synthetic share, and the audit's plan for adoption was to halt and re-specify (SYN:554). Option (a) removes that halt after the readings above were seen. That is a loosening of a halt, not a clarification. It is made blind to CAP-PICK outcomes, but not blind to the share.

**The owner's earlier decisions point to (a), but they do not decide it.** Each earlier decision matched the family's live path:
- **H5 excluded the pools,** and its executor refuses the class (#519).
- **C1-NF kept the pools,** and its canary trades them (DEC-026 §9.3). The owner's C1-NF basis had three parts:
  - no A3 halt that a rising share could fire;
  - the cost in n;
  - the live path trades the same population.

CAP-PICK has no live path today (the probe stopped 10-07), and its book has no class input. That is the C1-NF pattern, so (a) applies here. Of the three parts of the C1-NF basis:
- **No A3 halt:** this holds for CAP-PICK only after item A2 below.
- **The cost in n:** this applies, because P(pass) is about 5–7% (section 14).
- **Same population:** this applies.

The owner has not decided EXP-022, so the line stays OPEN.

**Question for the owner** (suggested; the manager asks it):
> "CAP-PICK (EXP-022) starts counting at 10-16T01. Its own text keeps synthetic pools in the book, priced through the guard. But its A3 rules also halt the test whenever one daily sample of about 18 graduations reads above 35% synthetic. At today's share, a halt on one of the 21 daily runs is near-certain, and the 10-15 run alone could withdraw it. Keep the pools in and make the share report-only (Recommended), exclude them as H5 does, or leave EXP-022 as written? The decision must merge before 10-15T06:41Z."

#### A. What option (a) sets

**A1. The universe is unchanged.** Line 121 stands as written. A synthetic pool that meets section 3 is an attempt. If it opens above the guard, it is a reject at −55,000 lamports (section 4.3). It is never dropped. "Synthetic" in this file means the class defined in item A3.

**A2. The A3 halt list has five flags.**
- **The five flags:** `pins_changed`, `boost_disabled`, `boost_share_low`, `boost_last_slice_early` and `boost_budget_or_slices_changed`. A core rule left unevaluated on two consecutive daily runs is also still a halt (line 357, unchanged).
- **Where "halt" means only these:**
  - P2 (line 320);
  - section 7.1 (d) (line 216);
  - "Before counting" (line 358);
  - "After counting starts" (lines 359-365).
- **How a run is read.** The five flags are read one by one from the record's `halt.flags`, never from `halt.any`, which also counts the sixth flag. A run on which `synthetic_share_high` is the only flag that fired counts as a run with no halt.
- **`synthetic_share_high` is recorded and reported, and never decides.** Each look prints every daily run's reading in its window as n_syn/n_chk, report-only.
- **The monitor does not change.** `tools/pump_structure_monitor.py` stays blob `1ca0a88cecf0853d94336ea046ba1a910b79f198` and the pins stay blob `7486f57f372e79c7d852f9cb70991d20043d4a8f` (on main at `13fa26c`). The monitor still computes the flag.

**A3. The class count (section 13, line 420, now defined).**
- **The classifier:**
  - EXP-024 Amendment 4 B1: the PostCompleteBuyEvent discriminator alone, in the curve-completing tx or the migrate tx;
  - B2: a tape `post_complete_buy` row can only mark a pool synthetic, and only a located and read tx settles non-synthetic;
  - Clarification 1 B4: the transaction search, with its caps.
- **The code:** `tools/synthetic_class.py` `classify_pool`, blob `930c8caa5d55a68e7886828b0e171acf107a2af1` on main at `13fa26c`. The read tool records the blob it imports.
- **What is printed.** At each look, by UTC date of s0, the number of attempts that are synthetic, non-synthetic and unclassified. Counts only.
- **Unclassified attempts stay in the book.** A failed fetch, or any unclassified share, never makes a look NOT_DECIDABLE or late. If classification cannot finish, the affected attempts print as `unclassified` and the look runs on schedule.
- **No item reads the counts.** Nothing in sections 7, 10, 11 or 12 reads them. Each look's report records the Helius credits that classification used (CLAUDE.md, Credits).

**A4. The class-count seal (adds to section 9).**
- **The breach.** Before the final verdict of the read is written, joining synthetic class to any CAP-PICK outcome is a breach of section 9.
  - "The final verdict" means look 3, or the last look that runs, or a pass or halt that ends the read.
  - The class may come from any source: item A3, the A3 monitor's sample, the H5 shadow or executor, `tools/h5_synthetic_audit.py`, the C1-NF class stream, or the tape.
  - An outcome is a fill, reject, guard-reject count, exit, P&L, mean, CI, day sign, win or loss, or any field derived from one.
- **Between looks:**
  - No person, agent or job computes the class of a counted CAP-PICK pick or attempt.
  - No one joins any class stream to the pick set or to the pick oracle's True mints, even for counts.
  - Only the sealed look does this, and it prints counts by class and date only.
  - The reason: under line 121, an attempt's class may say something about its guard result, so a class count of counted picks could stand in for an outcome count [inferred].
- **Inside a look.** The report prints the guard-reject share (line 420) and the class counts as separate totals. It prints no cross-tab of class against reject, fill, exit or P&L. The read tool writes no per-attempt class field into any file that carries an outcome field.
- **When there is a breach,** it is recorded here, dated, and the read is reported compromised (section 9).
- **After the final verdict** is written and recorded, a split by class may be printed, labelled report-only. It never decides, never re-scopes a look and never seeds a re-read.

**A5. What the read then measures.**
- The verdict is CAP-PICK's result on October's population, synthetic pools included. The exploration evidence and the A2 kill check (P6, on exploration rows) contain none. Every look report says so.
- Whether the gate's features on a synthetic mint include the PostCompleteBuy buy is not checked here [not measured]. The book is the gate as it runs on the walk-2 tape, under E0's decision equivalence (section 2.1).
- Section 14's power arithmetic is not re-run.

**A6. Multiplicity.** Unchanged. This amendment adds no arm, cell, look or α.

#### B. If the owner answers (b) or (c)

- **(b), exclude.** This amendment does not take effect. A replacement Amendment 7 must be drafted and get quant-proof's OK before 2026-10-15T06:41Z. It would need to:
  - supersede line 121's "never dropped";
  - apply the item A3 classifier to every attempt before scoring, and exclude synthetic and unclassified attempts from every leg;
  - make unclassified attempts above 1% of the look's attempts a NOT_DECIDABLE condition (section 10, "At every look");
  - use the A2 five-flag halt list, the A3 per-date counts and the A4 seal;
  - state that the book then differs from the live gate and executor.
- **(c), as written.** This amendment does not take effect. Lines 121, 354 and 420 stand (Amendment 5 item 3).

#### C. Conditions for this amendment to take effect

All of these must hold:
1. The owner line above reads (a), with the date and the notebook reference. The manager records it there.
2. Quant-proof's OK is posted on the final head.
3. It merged before the 2026-10-15T06:41Z daily A3 run started.

If it merges after that run has started, it does not take effect, and lines 121, 354 and 420 stand. When it takes effect, it closes Amendment 5 item 3.

**Not changed.**
- The book, the pick rule and E0.
- The universe conditions of section 3, including line 121.
- V0 and the missing-V0 rule.
- The guard, entry, exit, cap, costs, rent, stake, fail legs and pressure.
- The section 8 correction.
- The gate statistics, the looks, futility, α and multiplicity.
- The counting-start line (section 0) and the windows.
- The seal of section 9, apart from item A4.
- Every NOT_DECIDABLE condition.
- The core-rule clause.
- The monitor and pins blobs.
- Amendments 1 to 6.

## Sources

- The audit: `ARTIFACTS/lab/audit-2026-10-08/capv_JUDGE.md` and `ARTIFACTS/lab/audit-2026-10-08/SYNTHESIS.md`.
- Decisions: [DEC-021](../DEC/DEC-021-champion-challenger.md) Amendment 1; [DEC-014](../DEC/DEC-014-holdout-ledger-and-multiplicity.md); [DEC-016](../DEC/DEC-016-exp012-forward-on-chain-hours.md).
- Ledger and experiments: [HOLDOUT_LEDGER](../docs/HOLDOUT_LEDGER.md); [EXP-012](EXP-012-migrate-entry-model-refreeze-prereg.md).
- Lab notes: `ARTIFACTS/lab/probe-final-2026-10-07.md`; `docs/HANDOFF.md` (2026-10-08).
- Tools on main: `tools/forward_exp012_gate.py`, `tools/forward_paper.py`, `tools/pump_history_backfill.py`, `tools/pump_structure_monitor.py`, `tools/pump_structure_pins.json`, `tools/latency_curve.py`, `tools/probe_executor.py`, `tools/exp012_backcheck.py`, `tools/probe_sim_calibration.py` (#463).
- Drafts: #461 (`ebb77f4`, `2bab07b`) and #462 (`8fd49e5`).
