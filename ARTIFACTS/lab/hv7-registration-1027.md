# HV7 registration pack, DRAFT for T0 2026-11-01T00:00:00Z (written 2026-10-27T13:14Z from `date -u`; records only)

**Records only. No result. No book has cleared the promotion gate.** HV7 has no clock, no attempt, no outcome and no frozen sha yet. Nothing here says any book makes money. No tape, drill output, job output, card or HV4/HV6/Q50 output was opened to write this pack. Sources: `docs/research/hv7/DESIGN.md` (sections 2 to 7 and 10 are binding; sections 2, 4 and 10 are quoted below byte for byte from the file, and section 3 is quoted in full in section 3b, so its fallback rule and its prompt-sentence replacements are verbatim; sections 5, 6 and 7 are summarised in the sections of this pack that carry their numbers and are incorporated by reference at `docs/research/hv7/DESIGN.md` at `HV7_FINAL_SHA`), the HV6 pack (structure only), DEC-031 section 1, section 2 and Amendments 3, 4 and 6, and the names of constants and flags in `tools/hv7_register.py`, `tools/hv7_count.py` and `tools/hv7_runner.py`.

## 0. Placeholders still open (the manager fills these; nothing is guessed)

| Placeholder | Meaning | Status |
|---|---|---|
| `HV7_FINAL_SHA` | the 40-hex frozen sha of all HV7 code and prompts | OPEN |
| `RESULT_EQUIV_1D` | md5 decision-equivalence proof, jobs #1739 and #1743, plan n_SJvHB7-DU2QY9w | OPEN |
| `RESULT_F_3D` | 3-day full run, job #1744 | OPEN |
| `RESULT_E1` | resume/restart drill (`scripts/ops/hv7-e1-drill.sh`; not a production-halt drill, see section 3c) | FILLED: PASS, job #1749 at `687c77c64a777b91aba2514744287304ae1969ac`, 62 tests, 0 API calls, notebook n_c2uZR1cPRF8I7A. Valid only if `HV7_FINAL_SHA` = `687c77c6`; otherwise re-run at the final sha (0 API) |
| `OWNER_LINE_VERBATIM` | the owner's HV7 cost line, quoted in section 5 | FILLED (chat line quoted in section 5; see the note there on what it does and does not state) |
| `RESULT_UNIT` | unit tests green at `HV7_FINAL_SHA` (Am.6 item 1.2; section 3d) | OPEN (MANAGER) |
| `RESULT_SMOKE` | live real-model smoke of at least 2 h at `HV7_FINAL_SHA` (Am.6 item 1.3; section 3d) | OPEN (MANAGER) |
| `E1_CHECK_JOB` | host-side MiScusi job that computes the E1 count at 1793664000000 (section 3c) | OPEN (MANAGER-owed, scheduled before T0) |
| `REGISTRATION_NOTEBOOK_ID` | notebook entry that carries the `bots.json` lines (DEC-031 section 1.2), H = 10, the dry-run output with the exact confirm phrase, and the quant-proof and reviewer verdict comment ids on this PR (a verdict on the exact head cannot be written into the file it judges) | OPEN |

## 1. Book list and arms (DESIGN section 3; constants in `tools/hv7_runner.py`)

Four bot ids, `BOT_PREFIX = "hv7.0-"`, `ARM_ORDER = ("L", "R", "S", "U")`: `hv7.0-L`, `hv7.0-R`, `hv7.0-S`, `hv7.0-U`. Deciding stake `STAKE_SOL = 0.25`. Exit for every arm: HV6's X rule (`EXIT = dict(A.X_EXIT)`: stop -8%, target +20%, time 60 min).

| Arm | Pick | Model call | Promotable |
|---|---|---|---|
| L | Haiku's rank 1 (k = 1) of the shuffled set; model `claude-haiku-5-5` (`hv6_agents.MODEL_ID`) | 1 per period | yes |
| R | uniform random pick by the sha256 index into the screen order | none | no (placebo) |
| S | candidate 1 of the screen order (`f.vol_60m`) | none | yes (rule book) |
| U | all N candidates at 0.25 SOL each, per-period return the mean over N (`MAX_OPEN_U = R.N_MAX`) | none | no (report-only null) |

L-mem (v7.1) is not in v7.0. 4 cells, 2 promotable (L and S).

## 2. Universe, screen and period

Universe, screen, 2 h guard, vetoes, cooldown and exit are DESIGN section 2, copied byte for byte from `docs/research/hv7/DESIGN.md` lines 44 to 80 (the whole of section 2, through the entry-rate paragraph). Period: hourly, `t_d = H:02:00.000Z` (`T_D_OFFSET_MS`), `t_e = H:02:45.000Z` (`T_E_OFFSET_MS`), N = 8 (`n`), `N_MIN = 3`.

**Entry-rate measurement.** P0 #1711 gave a mean of 23.33 periods per day with at least 3 candidates, minimum 22; decision rule n_iFuATT5Ci0AsKw. Hourly periods stay primary. The DESIGN section 2 switch to 30-min periods fires only if the P0 mean is under 10.7 attempts per day; 23.33 is above that, so it does not fire. P0 counts periods with at least 3 candidates; it is a structure count and carries no outcome.

<!-- BEGIN DESIGN SECTION 2 (verbatim) -->
## 2. Universe, screen, period

**Universe = HV6's** (itself HV4 lane G): canonical PumpSwap pools of graduated pump.fun mints, WSOL quote, PRE-trade reserves plus V (about 17.58
SOL); age 2 h to 7 d with the hard 2 h guard (`hv6_cards.age_guard`); HV4 filters and hard vetoes 1-7 fail closed (imported unmodified through
`tools/hv6_cards.py`); the phantom-row filter (`tools/hv6_phantom.PhantomFilter`, sig_ones / signer / repeat_slot); the running-max head slot
(QP feed-lag ruling on #968: HV6 must be PumpSwap-only and pass the head slot). No pump.fun bonding-curve pools (feed lag HIGH there), no H5
entries, no pool younger than 2 h (keeps HV7 outside the EXP-024 Look-2 seal: no V-range pool in its first 360 s).

**Period.** Hourly. Decision instant `t_d = H:02:00.000Z` (the HV6 scan minute). Fixed entry instant for every arm `t_e = H:02:45.000Z`.

**Screen at t_d (code, causal, identical for all arms).**
1. Run `hv6_cards.scan` at `as_of_ms = t_d` over the current state (filters, 2 h guard, hard vetoes 1-7, setup tags as card fields).
2. Candidates = pools that passed, ordered by the screen score `f.vol_60m` (60-min SOL volume; the `hv6_cards.select_board` key) descending, ties by pool id.
   This list is the **screen order**; R's index and S's pick refer to it, never to L's display order.
   Take the first N = 8 (realised N is 3 to 8; every function takes the realised N). Build the HV6 card for each at `t_d` (`hv6_cards.build_card`, byte-identical across arms; `card_sha256` logged).
3. HV7 pre-listed code vetoes (fail closed, applied before the set is fixed, never by the model): HV4 hard vetoes 1-7 (already in the screen);
   round-trip impact at 0.25 SOL > 3.0% (HV6 section 2); optionally the P62 creator fee-share rug label **only if it merges before HV7's freeze**,
   with its own QP OK. A vetoed pool is replaced by the next by `f.vol_60m`.
4. If fewer than `N_MIN = 3` candidates remain, the period has **no trade in any arm** (so every counted attempt is a real choice; with 1-2 candidates
   L, R and S would coincide and add n without selection).

**Cooldown.** None per arm (a per-arm cooldown would make candidate sets differ by arm and break the pairing). Exits come before entries at the same
instant, so a 60-min position closes at `H+1:02` before the next set is fixed. The same pool can be picked in consecutive hours; this clustering is
disclosed and the contrasts use daily sign-flips (section 4), which absorb within-day clustering.

**Exit (code only, identical for every arm).** HV6's X exit `X_EXIT = {stop -8%, target +20%, time 60 min}` (`tools/hv6_agents.py` line 71). No
LLM re-check. One exit rule for every arm makes every contrast a pure selection contrast. Max hold 60 min, so each read can run 61 min after its
boundary.

**Entry rate (assumption, to be replaced by P0).** Max 24 attempts/day/arm at k = 1. Rate = 24 x q, q = share of hours with >= 3 candidates after
vetoes. The HV6 drill produced about 520 cards/day (1,560 over 10-21..23, QP ruling (a)), about 21.7 per hour on average, so q is probably high,
but the hourly distribution (quiet hours) is **not measured**. Needed: >= 8.4/day for n = 100 in 12 days, **>= 10/day for 10 days** (the clock in
section 6 gives D = 10). So q >= 0.42 is needed at hourly periods. If P0 gives 24 x q < 10.7 (H rule boundary), the registration switches,
before the clock and by a rule written now, to **30-min periods** (`t_d = :02 and :32`, `t_e = +45 s`, 60-min exit capped at 30 min so no arm
holds across its next decision). Either way the rate is chosen from structure counts only, never from outcomes. QP notes the 30-min period
would raise n/day and therefore contrast power, and that it changes s (the 30-min cap); making it primary is allowed only on P0 structure counts,
is optional, and adds no arm or cell.
<!-- END DESIGN SECTION 2 (verbatim) -->

## 3. Clock

- T0 = 2026-11-01T00:00:00Z = `1793491200000` (`CLOCKS_MS[0]` in `hv7_register.py`; the fallback clock `1793577600000` = 2026-11-02T00:00:00Z is not registered by this pack).
- Entries halt (H) = `1794355200000` = 2026-11-11T00:00:00Z (`HALT_MS`).
- `--clock-start-ms` must be passed explicitly (it is `required=True` in `hv7_register.py` and `hv7_runner.py`; `hv7_count.py` takes it with default 0, so pass it explicitly there too, and it additionally requires `--clock-end-ms`).
- Kill-review check (DESIGN section 6): LAB_STATE and docs/HANDOFF.md list no kill-review week after 2026-10-05 (manager17, relayed 2026-10-27, PR #996). To be re-checked on the registration date.

## 4. Reads (DESIGN section 4, quoted exactly)

The block below is copied programmatically from `docs/research/hv7/DESIGN.md` lines 156 to 233 (section 4 through its Disclosures paragraph). It includes the L gate under DEC-031 section 2 at B, the L - U read once at T0 + H with the rule of at least 8 non-zero days, the fallback checks, the Holm-ranked secondaries, the multiplicity paragraph, the power table and the MDE line.

**Promotion gate, verbatim from CLAUDE.md** (it holds under both fail models):

> A book promotes only if all of these hold, under **both** the flat 15% fail model and the pressure-fail model at slope scale 1:
>
> - at least 100 out-of-sample trades
> - at least 5 distinct UTC days, with a majority of those days positive
> - lower 90% CI bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile)
> - total SOL still positive after removing the top 3 trades

DEC-031 section 2 "When" and "The test" are unchanged by HV7 (Amendment 16 states this). Ex-best-day is reported and a book whose total is not positive ex-best-day is not proposed for a canary.

<!-- BEGIN DESIGN SECTION 4 (verbatim) -->
## 4. Reads (pre-registration sketch)

All under DEC-031 section 2 and its amendments as merged at registration; quoted exactly in the registration text, not paraphrased here.

**Gate read (absolute, per promotable arm L and S).** B = the first 00:00Z boundary with >= 100 counted attempts on >= 5 UTC dates by the
outcome-blind decision-timestamp tool (`hv7_count.py`), read once after every attempt decided before B has exited (61 min). Counted attempt =
every entry at `t_e`, fallback rows included. Deciding cell: the 1.9 s leg at stake 0.25, in all four sub-cells (flat 15% and pressure scale 1,
each under strand rules sc and total). **Leg mapping:** the decision instant passed to the engine is `t_e` for every arm (decision_slot = the
head slot at `t_e`); the 1.9 s leg is the engine's own leg definition from there (`arena_paper` LATS (1.3, 1.9): the entry lands at sd + d + k,
d the feed delay, k the 1.9 s leg's slots), i.e. a fill priced about 1.9 s after `t_e`, never after L's reply time. C2 verifies this against the
engine code and the registration states it. The CLAUDE.md gate is unchanged: >= 100 out-of-sample trades; >= 5 distinct UTC days with a majority
positive; CI90 lower bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile); total SOL positive after removing the top 3;
under both fail models. Ex-best-day > 0 is required before any canary proposal (DEC-032 section 1). If B has not come by T0 + H, NOT_DECIDABLE.
Beside the per-trade gate bootstrap, a **day-block bootstrap** of L and S (resample UTC days, 1,000 draws, seed 1, 5th percentile of mean SOL per
trade) is reported. Report-only; it never changes the gate; it addresses the sticky `f.vol_60m` leaders (risk 3).

**Horizon.** H = min(90, max(14, ceil(150 / rate))) (DEC-031 Am.4 item 2.2), capped by the window end. The window end is the API horizon
2026-11-11T00:00Z (owner's 200 USD cap runs to 11-11). For a 2026-11-01T00Z clock, H = 10. At about 20/day, B is about T0 + 5 d (2026-11-06T00Z).

**Selection-skill contrast (the paired test; read ONCE at the window end T0 + H, QP (a)1).**
- Why not at B: an exact one-sided sign-flip on D daily differences has a minimum p of 2^-D. At B (about 5 dates) that is 2^-5 = 0.03125 > 0.025,
  so power at B is exactly 0; at D = 6 only all-6-positive passes (0.015625).
- Window: all attempts with `t_e` in [T0, T0 + H) (D = 10 for an 11-01 clock, 9 for 11-02), read once after the last of them has exited.
- Primary: **L - U**, daily sums of per-period SOL differences at stake 0.25; A1's exact one-sided sign-flip on daily differences; p <= 0.025 in
  each of the four sub-cells; **>= 8 non-zero days**, else NOT_DECIDABLE (reason `contrast_days`).
- Secondary (report-only, Holm-ranked, same window and day rule): L - R, L - S, S - U, R - U (fault check), rank IC of L's full order against
  U-priced returns. L - S shows a ranker that just follows the `f.vol_60m` leader.
- **A skill pass is not a promotion.** L - U > 0 with L's own gate FAIL means "Haiku selects better than random from this screen, and the screen
  loses more than the selection adds". It licenses a v7.1 on a better screen; it never licenses a canary.

**Power (QP simulation, comment 6431175846 (b), corrected by 6431180577; arithmetic, not a measurement).** Assumptions as above: s = sd of 60-min
net return 8-12%, sd(L - U) = 1.06 s, zero cross-pool correlation, normal returns, about 20 trades/day, 0.25 SOL; exact one-sided sign-flip at
p <= 0.025 on daily sums; 1,500 draws per cell, seed 1. Power by per-trade lift (first column = null rejection rate), s = 8% / 12%:

| D (n) | lift 0 | 0.5% | 1.0% | 1.5% | 2.0% | 3.0% |
|---|---|---|---|---|---|---|
| 5 (100) | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| 10 (200) | 0.030 / 0.031 | 0.100 / 0.073 | 0.313 / 0.153 | 0.617 / 0.331 | 0.835 / 0.528 | 0.991 / 0.834 |

(QP's table also has D = 6: 0.013-0.726 / 0.015-0.386 and D = 7: 0.026-0.911 / 0.027-0.624 across the same lifts.)

Lift from rank IC (normal approximation): top-1-of-8 lift = IC x s x E[max of 8 N(0,1)], E[max of 8] = 1.4264. IC 0.05 gives 0.571% / 0.856%;
IC 0.10 gives 1.141% / 1.712%.

- **MDE (80% power at D = 10): a per-trade lift of about 1.9% (s = 8%) or about 3.0% (s = 12%), i.e. a rank IC of about 0.17.**
- At IC <= 0.05, the range the lab's earlier Haiku work points to (v3: learning about equal to placebo, gain = dead-pool filter), power is about
  0.07-0.15. The likely result is "not significant".
- **A non-pass of L - U is not evidence of no skill below the MDE.** It is reported as "no skill detected; MDE IC about 0.17".
- No extra arms or promotable cells are added to raise power (QP). For L's absolute gate, the mean must also exceed 1.645 x s / sqrt(n)
  (1.32-1.97% net at n = 100), so the selection lift must cover the screen's own loss (unknown; priors say several % per round trip) plus that bar.

**Early abort E1 (outcome-blind, host-side MiScusi job scheduled before T0).** At T0 + 2 d (2026-11-03T00:00Z for an 11-01 clock), run
`hv7_count.py` once and notebook its output sha256. A_L = counted attempts of L in [T0, T0 + 2 d); F_L = fallback rows of L.
- A_L < 20 (= 2 days x the 10/day needed for n in D = 10): every HV7 gate read and contrast is NOT_DECIDABLE (reason `E1_entry_rate`), written once
  before any HV7 outcome is opened; entries halt for all arms at the next 00:00Z boundary; code exits run to completion. Because the rate is set
  by design, E1 firing means a pipeline fault (feed gaps, screen starvation), not model choice.
- F_L / A_L > 0.10: L's reads are NOT EVALUABLE (`E1_fallback`); R, S, U continue as rule books to B or T0 + H. The same rule is checked again at
  B and at T0 + H over all attempts (section 3).
- E1 also prints the display-slot histogram of L's picks (decision data, no outcome). Report-only; no action (bias reduces power, not validity).
- E1 never yields PASS or FAIL. No other count is read before B, except the fallback check at B (section 3). The halt mechanism is the restart-with-entries-halt path drilled at the final sha
  on synthetic tape before T0 (HV6's E1 drill #1701 pattern); without the drill E1 is declaration-only.

**Kill lines (before the clock, structure only).** Not registered if: the offline rehearsal (section 7) fails its pre-declared bars; P0 gives fewer
than 10.0 attempts/day at the chosen period or any P0 day under 5; the md5 decision-equivalence proof fails; any seal question is open.

**Multiplicity (DEC-031 Am.3 item 3).** HV7 v7.0 registers 4 bot ids (`hv7.0-L`, `-R`, `-S`, `-U`), one deciding stake (0.25), no as-sized cell:
4 cells, 2 promotable. Each read carries N = every book with a clock before the read (HV4's 9, Q50, the H5 books, hunt23/cohort books, HV6's 5,
HV7's 4, stopped and NOT_DECIDABLE included, counted from notebook copies), the plain line 1 - 0.95^N, and the report-only p (10,000 draws, seed 1,
larger of the two fail models) with its Holm rank.

**Siblings (Am.3 8(e)).** HV7 shares universe, scanner, cards and model with HV6 (GEN, X) and HV4 lane G on overlapping days. The best of them at
any read is in-sample; an HV6 pass and an HV7 pass on the same days are never independent confirmations. HV7's S arm and HV6's X arm differ (S
takes the f.vol_60m leader of the board each hour; X takes ranked setups at 2/h) but share the universe.

**Disclosures for the registration.** Walk-2-hour information (DEC-030 section 2 R1): the rehearsal cards are 10-21..23. The rehearsal cards
overlap the 240 + 192 HV6 rehearsal cards (no rate is targeted, so no fitting by (i)-(ii) of QP-RULING-1026-entry-rate; disclosed anyway).
HV7 was designed after HV4's digests showed LLM arms abstaining (the digest sha only; no HV4 decision content, count or outcome was seen by the
author) and after HV6's rehearsal counts; neither carries an outcome.
<!-- END DESIGN SECTION 4 (verbatim) -->

## 5. API cost (DESIGN section 5)

- HV7 cap: 0.30 USD/day, inside the K1 3.00 USD/day shared with HV6: 2.20 (HV6 v6.0) + 0.00 (HV6 v6.1, parked) + 0.30 (HV7 v7.0) = 2.50 <= 3.00. Against the owner's 200 USD cap to 2026-11-11.
- **Owner OK.** The owner approved HV7 <= 0.30 USD/day on 2026-10-27 at about 04:01Z; MiScusi question `q_6_kzYJva9XXuYQ`. This is the value for `--owner-cost-line-ref` (`hv7_runner.py` pattern `OWNER_REF_RE = (?:n|q)_[A-Za-z0-9_-]{12,20}`; the runner refuses before building a model on any string that does not match `OWNER_REF_RE`; this is a format check and does not prove that the ref is the owner's line). The owner's chat line, 2026-10-27 about 04:01Z, verbatim (supplied by the manager): "You've got my oks on all of this stuff for everything you needed it for. It all looked good. For the Haiku stuff you can even do some research on how other people have been making bots, maybe there's some not bad ideas we can take a look at and make better. Keep it pushing!" Note for the reader and the QP re-check: this line states no USD figure, no date range and no smoke. The 0.30 USD/day figure and the window 2026-11-01..11-11 come from the question `q_6_kzYJva9XXuYQ` and the earlier K1 line (200 USD cap to 2026-11-11); the writer did not open the question text. The smoke (section 3d) is inside the 0.30 USD/day cap. The manager quotes the owner's reply to `q_6_kzYJva9XXuYQ` with the numbers it covers in the registration notebook entry.
- **Enforcement as the code implements it (DESIGN section 5, choice (a)).** `tools/hv7_runner.py` has `HV7_DAY_CAP_USD = 0.30`, `K1_DAY_CAP_USD = 3.00` and `cap_sum_usd()`, which is `math.fsum([A.caps_total_usd(), HV7_DAY_CAP_USD])` (HV6's registered caps from `tools.hv6_agents.caps_total_usd()` plus HV7's constant; config only, no ledger or spend). The K1 sum test lives in `test_hv7_runner.py` and `hv7_register.pins()` prints `hv7_day_cap_usd`, `k1_day_cap_usd` and `cap_sum_usd`. At run time a thin `Hv7Guard` (the HV7 stand-in for `hv6_agents.BudgetGuard`) does reserve-before-call through `haiku_caller`'s ledger on one ledger, `RANK_LEDGER = "RANK-L"`, capped at `HV7_DAY_CAP_USD`; it refuses a `day_cap_usd` above the constant. A budget stop is a FALLBACK counted toward the 10% line. There is no shared guard across the HV6 and HV7 ledgers (option (b) was not built); a sum above 3.00 is caught by the test, not at reserve time.

## 6. Proofs table (filled later by the manager)

| Proof | Reference | Result |
|---|---|---|
| Card-equivalence | #1729, n_Qx7z50OvtPwppg | PASS 1560/1560. Stated limit: it proves the card builder at HV6's H:MM:02 instants with setup None. |
| P0 entry-rate count | #1711, rule n_iFuATT5Ci0AsKw | mean 23.33 periods/day with >= 3 candidates; minimum 22 |
| RESULT_EQUIV_1D | jobs #1739 and #1743, plan n_SJvHB7-DU2QY9w | OPEN |
| RESULT_F_3D | job #1744 | OPEN |
| RESULT_E1 | `scripts/ops/hv7-e1-drill.sh` at `HV7_FINAL_SHA` | OPEN |
| Offline rehearsal | n_jDI1FRScLVaBEA | PASS: 140/140 valid, slot-1 share 0.1214, A/B agreement 0.70 against a bar of 0.2080 |
| HV7_FINAL_SHA | | OPEN |

Rehearsal caveats: the subagent calls were not independent, and the agents mistyped ids and self-corrected in 5 of 28 groups. The rehearsal measures parse, position bias and A/B agreement under random display order. It is not a skill test (no outcome is joined). The bar is a kill-only floor (DESIGN section 7).

## 7. Seal and disclosure block

- The rehearsal and proof days 2026-10-21..23 are walk-2-hour information (DEC-030 section 2 R1). Ledger checks: n_bjZWYzCNL60ayw and n_t3I6p9TsBfvdbA.
- No outcome is ever joined to the rehearsal sets: no rank IC and no offline skill check, now or later.
- Sibling overlap with HV6 (and HV4 lane G): the same days give no independent confirmation; the best of the siblings at any read is in-sample (DEC-031 Am.3 item 8(e)).
- Memory-housekeeping caveat for revived pools (the `#1018` config comment in `tools/hv7_runner.py`): a pool revived after more than the state's 6 h eviction horizon idle has had its `QuoteHistory` swept, so its `quote_sol_15m_ago` and `quote_sol_peak` facts are computed from the quotes since the revival only; the sweep follows the state's own eviction and is not exact against a never-swept history. `tools/test_hv7_runner_mem.py::test_revived_pool_after_sweep` pins the behaviour.
- Sealed areas not touched by this work: HV4 and Q50 outputs, meta-capture, DBC fwdv1, FLOOR G1, LaunchLab holdout.
- Disclosed prompt changes (DESIGN section 3): the BASE RATE closing sentences replaced and MEASURED DIRECTIONS using the QP clause, as written there.

## 8. Registration command (from `tools/hv7_register.py`; run once by the manager)

Flags: `--sha` (required, 40-hex), `--clock-start-ms` (required), `--entries-halt-ms` (required), `--registry` (required unless `--live-registry`), `--live-registry`, `--execute`, `--confirm`. Dry run by default. The confirm phrase is `REGISTER-HV7-FOUR-BOOKS:<sha>:<clock_start_ms>:<entries_halt_ms>`, so a changed pin changes the phrase.

```
# dry run first; it prints the plan and the exact --confirm phrase and writes nothing
python -m tools.hv7_register --sha HV7_FINAL_SHA --clock-start-ms 1793491200000 --entries-halt-ms 1794355200000 --live-registry
# then, once, with the printed phrase
python -m tools.hv7_register --sha HV7_FINAL_SHA --clock-start-ms 1793491200000 --entries-halt-ms 1794355200000 --live-registry --execute \
  --confirm REGISTER-HV7-FOUR-BOOKS:HV7_FINAL_SHA:1793491200000:1794355200000
```

(`HV7_FINAL_SHA` is replaced by the 40-hex lower-case sha in both places.) Runner flags that carry the same pins: `--frozen-sha`, `--clock-start-ms`, `--entries-halt-ms`, `--feed-delay-slots`, `--tape`, `--root`, `--registry`, `--owner-cost-line-ref q_6_kzYJva9XXuYQ`; the runner refuses a clock or halt that differs from the registry. Count tool: `python -m tools.hv7_count --root ROOT --clock-start-ms 1793491200000 --clock-end-ms <instant>`. Whether a flag value in the runner command is right is checked by the manager against the registry before submit.

## 9. Order before the clock (DESIGN section 6, unchanged)

Final sha frozen, proofs of section 6 filled, 24 h stub at the candidate sha (Am.6 pattern), records PR with QP OK on the exact head and a reviewer verdict, DEC-031 Amendment 16 merged, registration, clock. Amendment 16 must merge before the clock (Am.6 scope rule).

## 10. Risks (DESIGN section 10, quoted)

1. **The screen loses and Haiku cannot select enough to cover it.** Expected outcome of L's gate read: FAIL. HV7's real deliverable is the paired
   skill number (L - U) and a decided book, not a pass.
2. **Under-powered contrast.** MDE IC about 0.17 at D = 10; at IC <= 0.05 power is about 0.07-0.15. A non-pass says little (section 4).
3. **Positional or noisy ranking.** Shuffling keeps validity; the rehearsal's consistency bar stops a ranker that is no better than R.
4. **Hour-to-hour pool repeats.** The f.vol_60m leaders are sticky; L, S and U may hold the same pools often, which inflates per-trade n relative to
   independent information. Daily sign-flips absorb this for the contrast; the gate's per-trade bootstrap does not (disclosed; a day-block bootstrap is
   reported beside it, report-only).
5. **Short window.** D = 10 to the API horizon; a slipped clock reduces it further. 30-min periods are the pre-written lever.
6. **Engine/runner limits for U** (8 open positions for one bot) not verified; cross-arm pricing independence is a hard C2 test.
7. **Sibling overlap** with HV6/HV4 lane G: no independent confirmation from the same days.
