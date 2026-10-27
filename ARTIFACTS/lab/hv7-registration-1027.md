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

- T0 = 2026-11-01T00:00:00Z = `1793491200000` (`CLOCKS_MS[0]` in `hv7_register.py`; the fallback clock `1793577600000` = 2026-11-02T00:00:00Z is not registered by this pack; a slip to that clock needs a pack revision with H = 9, a needed rate of 11.1/day, and the confirm phrase with the new ms, DESIGN section 6).
- Entries halt (H) = `1794355200000` = 2026-11-11T00:00:00Z (`HALT_MS`).
- `--clock-start-ms` must be passed explicitly (it is `required=True` in `hv7_register.py` and `hv7_runner.py`; `hv7_count.py` takes it with default 0, so pass it explicitly there too, and it additionally requires `--clock-end-ms`).
- Kill-review check (DESIGN section 6): LAB_STATE and docs/HANDOFF.md list no kill-review week after 2026-10-05 (manager17, relayed 2026-10-27, PR #996). To be re-checked on the registration date.

### 3a. Pin block: H and the read instants (Am.4 item 2.2, Am.7)

| Pin | Value |
|---|---|
| H | 10 days (Am.4 item 2.2: 14, capped by the 2026-11-11 window end); 9 for the 11-02 fallback clock |
| T0 + H | `1794355200000` = 2026-11-11T00:00:00Z = `HALT_MS` |
| E1 instant | 2026-11-03T00:00:00Z = `1793664000000`, over [T0, T0 + 2 d) (section 3c) |
| Gate read | B <= T0 + H, else NOT_DECIDABLE; read not before B + 61 min |
| Contrast window and read | `t_e` in [`1793491200000`, `1794355200000`); read not before 2026-11-11T01:01:00Z = `1794358860000` |
| Where H lives (Am.7) | the `hv7_halt` registry line carries the halt instant; the notebook copy (`REGISTRATION_NOTEBOOK_ID`) carries H = 10 too |
| C5 read tool | `tools/hv7_read.py`, PR #1021 merged to main (checked against `origin/main` at `67f2e398`). CLI: `python tools/hv7_read.py {gate,contrast} [--arm {L,S}] [--root ROOT] [--ledger-root LEDGER_ROOT] [--registry REGISTRY] --n-books N [--other-ps JSON]`; `--arm` defaults to `L`, `--root` to `/data/mal/arena/haiku-v7`, `--registry` to the real registry, `--n-books` is required (every book with a clock before the read, from notebook copies), `--other-ps` is `{name: report-only p}`. `--now-ms` and `--record-dir` are TEST ONLY (`MAL_ARENA_TEST=1`). There is no `--horizon-days` and no `--rate`: H, the clock and the deciding stake are read from the registered `hv7_halt` / `register` lines and are not overridable by any flag. The ledger is `/data/mal/arena/haiku-v7/engine-ledger`, strictly inside `--root` (`resolve_ledger_root`; PR #1033, pending, on this point); `hv7_read` refuses the shared arena ledger and any path not inside the HV7 root. |
| Binding read (QP re-check of #1021, 6443512785) | `hv7_read`'s once-only record is THE binding gate read for `hv7.0-L` and `hv7.0-S`. Any `arena_score --record` read of those bots is not a registered read and is never quoted. |
| Outage blind spot (same re-check) | A runner outage of whole hours writes no rows, so DESIGN's 10% excluded-period cap cannot see it (the cap counts `period_excluded` rows, and an outage writes none). Disclosed. The read prints a report-only coverage block that separates outage hours (no SCAN row and no arm row) from no-trade hours (runner up, arm has no entry row), per arm. Both are report-only and gate nothing. |
| Runbook rule: refusals that never resolve (QP rounds 3 and 4 on C5, 6444391481, 6444894434) | ANY `hv7_read` refusal still holding at the deadline (T0 + H + 61 min; for the gate, B + 24 h) makes that read NOT_DECIDABLE. Examples, not a closed list: a `frozen_sha` mismatch, a duplicated `trade_id`, mixed stack configs, the internal n mismatch, a dirty-tree open row, a flat-leg mismatch, a missing stamp, U with no trades. The manager records the read as NOT_DECIDABLE in the notebook and in LAB_STATE, citing the refusal text. The manager never fixes the data in order to get a read, and never re-runs with a different `--root`, `--registry` or `--ledger-root` (or any other input) to obtain a read. |
| Gate worst-case line (same round) | The gate prints a report-only worst-case line that treats lost attempts as total losses, because a gate PASS triggers the owner's standing C0 proposal (10-23 approval). The line gates nothing. |

### 3b. DESIGN section 3, quoted in full (fallback rule, `why` rule, display permutation, prompt-sentence replacements)

The block below is copied programmatically from `docs/research/hv7/DESIGN.md` lines 82 to 155 (the whole of section 3 through its last line). It carries the FALLBACK rule and its 10% checks at E1, B and T0 + H, the no-abstain rule, the `why` rule, the position-bias control, the disclosed BASE RATE replacement ("Do not buy to fill a quota ..." replaced by "The book trades your first choice ...") and the MEASURED DIRECTIONS clause replacing GEN_v0 line 31, and the RANKING TASK paragraph. Both prompt-sentence replacements are disclosed here as the registration (section 7).

<!-- BEGIN DESIGN SECTION 3 (verbatim) -->
## 3. Arms

Cards are byte-identical at `t_d` for every arm. Every arm buys 0.25 SOL at `t_e` (so R, S and U do not get a latency head start over L's API
call) and exits by the same code rule.

| Arm | Pick | Model call | Promotable |
|---|---|---|---|
| **L** | Haiku's rank 1 (k = 1) of the shuffled set | 1 per period | yes |
| **R** | uniform random pick: index into the **screen order** (section 2) = `int(sha256("hv7-R|" + t_d_iso + "|" + ",".join(sorted(card_shas))).hexdigest(), 16) mod N` | none | no (placebo) |
| **S** | screen-score pick: candidate 1 of the screen order (`f.vol_60m`) | none | yes (rule book) |
| **U** | **all N** candidates, 0.25 SOL each, per-period return = mean over N | none | no (report-only null) |
| L-mem (v7.1, not in v7.0) | L plus a nightly reviewer memory note | 1 per period + 1/night | deferred |

**Why U.** R's single seeded draw is an unbiased estimate of "random selection from the same set", with draw noise. U is that expectation exactly
(the mean over all N), so L - U has no draw noise. Under independent per-pool returns with sd s, var(L - R) is about 2 s^2 and var(L - U) about
s^2 (1 + 1/N) (assumption: zero cross-pool correlation; positive correlation helps both). At N = 8 that is sd 1.41 s against 1.06 s, so L - U needs
about 0.56x the sample of L - R for the same power. U also prices every non-selected rank, which gives the rank IC (Spearman of L's full order vs
realised 60-min return, report-only) and the data `haiku-v3-4` lacked. R is kept because it is the literal placebo the owner asked for and is a
check on U (R - U must be mean zero; a non-zero R - U is a pipeline fault, reported).

**Engine load for U.** Up to 8 concurrent 0.25 SOL paper positions per hour for one bot id. `arena_paper.ArenaEngine.buy` takes one order at a
time; whether the engine and runner allow 8 open positions for one bot is **not verified** (C2 checks it). If not, U is computed by the read tool
from 8 shadow bot ids `hv7.0-U1..U8` (slot by screen order), which is the same arithmetic.

**Cross-arm pricing independence (hard C2 test, QP (a)4).** L's pick is always one of U's N, and R's and S's picks often are too. `arena_paper`
prices each fill on tape state with only the bot's own buy re-injected (header line 13), which suggests independence, but it is not verified.
C2 must show **byte-identical entry and exit prices** (every leg, both fail models) for 2 bots, and for L + U1..U8 (+ R + S), buying one pool at
one `t_e`. If that test fails, the design is invalid (not just noisier) and HV7 is not registered until it passes.

**Model and no-abstain rule.** `claude-haiku-5-5`, thinking disabled, no temperature, max_tokens 600, one 5-min cache marker on the system block
(`hv4_prompts.CACHE_MARKER`). The reply must be a JSON list that is a full permutation of the shown candidate ids (exactly N, no duplicates, no
others), each with `why` <= 20 words naming >= 1 card field. There is **no SKIP, WATCH or VETO token**. A reply that is not a full permutation, a
refusal, a timeout, a budget stop, or no reply by `t_e` is **FALLBACK**: L trades **R's pick** that period, the row is logged `fallback=true`, and
the attempt counts in n. Fallback rows contribute 0 to L - R (given the C2 pricing-independence test) and R - U noise to L - U, so a failing model pulls the contrast
toward the null, never toward a pass. **Fallback rule (QP (a)3):** F_L / A_L > 0.10, over all counted attempts of L, makes the L gate read and
every L contrast NOT EVALUABLE. It is checked by the outcome-blind count tool at E1 (attempts in [T0, T0 + 2 d)), at B (attempts before B) and at
T0 + H (all attempts with `t_e` in [T0, T0 + H)), each time written once before any outcome is opened.

**`why` rule (frozen, QP (d)3).** The order is the decision. A full permutation whose `why` for some item is over 20 words or names no card field
is **accepted and logged** (`why_defect` count), not FALLBACK. Only a reply that is not a full permutation of the shown ids (or a refusal,
timeout, budget stop, no reply by `t_e`) is FALLBACK. The rehearsal parse bar counts by this rule and reports `why_defect` beside it.

**Position bias control.** The display order is a seeded permutation, `perm = argsort(sha256("hv7-perm|L|" + t_d_iso + "|" + card_sha))` per
card, logged with the request. Because the order is random, a model that always picks display slot 1 is a random picker: position bias costs power,
never validity (L then equals R in distribution). Bias is measured offline (section 7) and, in the live run, only by the pinned outcome-blind tool
at E1 (section 4).

**Prompt (sketch; frozen text goes in C1 and is not edited after the rehearsal).** System block = HV6's GEN playbook header facts that are true
for all arms. Every measured BASE RATE sentence is byte-equal to GEN_v0; the closing buy/skip sentences are replaced with
"The book trades your first choice and pays the round trip cost before any move, so rank by what the card fields support. Judge each pool on its fields alone." (disclosed below). MEASURED DIRECTIONS ends with the QP clause (below). C1's tests byte-compare
with `tools/hv6_playbooks/GEN_v0.txt`: UNTRUSTED TEXT, CARDS, HONESTY and SCREEN, NOT REASON whole; BASE RATE up to and including "...a learning
loop did no better than a shuffled one."; MEASURED DIRECTIONS up to the changed sentence.

**Disclosed replacement in BASE RATE (made in C1, found by QP's #997 review).** GEN_v0 line 25 ends: "Do not buy to fill a quota and do not skip
to stay safe: a BUY pays the round trip cost before any move, a SKIP forgoes whatever a good card offers. Judge each card on its fields alone."
RANK_v0 replaces those two sentences with: "The book trades your first choice and pays the round trip cost before any move, so rank by what the
card fields support. Judge each pool on its fields alone." Reason: the GEN_v0 sentences address a BUY/SKIP choice that a forced rank does not
have (there is no quota to fill and no SKIP to take), so kept as written they would describe options the model does not have. The replacement
keeps the one factual claim in them (the round trip is paid before any move) and the "fields alone" instruction. Every measured sentence before
it (universes lose before selection; round trip about 2.4%, 3.5% with rent; fresh pools -24.09% per attempt; survivors -8.27% per 24 h hold; a
random pick among ten -2.88%; earlier Haiku versions added nothing) stays byte-equal to GEN_v0. The registration discloses this replacement
beside the MEASURED DIRECTIONS change. MEASURED DIRECTIONS uses QP-RULING-1026-entry-rate (c)'s clause instead of GEN_v0 line 31 ("do not claim that
one of them improves the odds", which contradicts a forced ranker): "you may argue from them, but say it is a hypothesis, not a lab result".
GEN_v0's conviction and size sentences are dropped (HV7 has neither). Both changes are disclosed in the registration. Plus:

> RANKING TASK. You are shown N pools that passed the lab's code screen at the same instant. The book WILL buy exactly one of them, your first
> choice, and sell it by a fixed code rule (stop -8%, target +20%, or 60 minutes). You cannot skip. The base rate above still holds: the average
> of these pools is expected to lose after costs. Your job is relative selection: order them from the one you expect to do best over the next
> 60 minutes to the one you expect to do worst. Argue from card fields; say "hypothesis" where the lab has no measured direction. The order shown
> is random and carries no information.

User block = the N cards in the shuffled order, with ids, checked by an HV7-local card check (`hv4_prompts.check_cards` caps at
`ENTRY_MAX_CARDS = 4`; no hv4_/hv6_ file is edited). No outcome, no prior trade, no P&L, no other arm's decision is ever in a request.

<!-- END DESIGN SECTION 3 (verbatim) -->

Status line under the quoted engine-load and independence text (the quote stays as written): the C2 tests that answer "not verified" are in section 6 (`test_one_entry_per_arm_at_t_e_and_u_holds_all_n`: U holds all N open positions; `test_pricing_independence_*`: byte-identical pricing across arms), at the final sha.

### 3c. E1 halt: declaration-only, with a manual action (QP item 2, manager decision)

E1 is checked at 2026-11-03T00:00:00Z (`1793664000000`) by the outcome-blind count tool (`tools/hv7_count.py`) over [T0, T0 + 2 d). If fewer than 20 L attempts are counted in those two days, E1 FIRES: every HV7 read is NOT_DECIDABLE. The "entries halt for all arms at the next 00:00Z boundary" of DESIGN section 4 is **declaration-only**; there is no CLI route for it (`hv7_runner.main` refuses any `--entries-halt-ms` other than the registered `hv7_halt`, and `hv7_register.check_pins` refuses any halt other than `HALT_MS`). The production action is manual: the manager cancels the HV7 runner job, which stops API spend, and records it in LAB_STATE; open positions are then handled as Am.6 item 4 restart exclusions. A host-side MiScusi check job, scheduled before T0 for `1793664000000`, computes the E1 count at that instant and writes counts only. **That job is MANAGER-owed** (placeholder `E1_CHECK_JOB` in section 0; section 9).

`RESULT_E1` (section 6) is a resume/restart drill: `scripts/ops/hv7-e1-drill.sh` runs `tools/test_hv7_resume.py` and `tools/test_hv7_count_register.py`. It is not a production-halt drill. `test_restart_with_entries_halt_enters_nothing_after_h_and_ends_flat` drives `Hv7Runner` directly, not `main()`.

### 3d. Amendment 6 coverage (HV7 registers after Am.6; item 1 applies)

| Am.6 item | Evidence | Status |
|---|---|---|
| 1.1 pins unchanged: frozen sha, rule, deciding cell, read rule; Am.2 config pin; parity and engine pins | sections 1, 3a, 3e | pinned here; `HV7_FINAL_SHA` OPEN |
| 1.2 unit tests green at the frozen sha | `RESULT_UNIT` | OPEN (MANAGER): cite the run at `HV7_FINAL_SHA` |
| 1.3 live smoke of at least 2 h, real model, scratch root, never counted | `RESULT_SMOKE` | OPEN (MANAGER); limits pinned below |
| 1.3(e) md5 decision-equivalence proof (port) | `RESULT_EQUIV_1D`, `RESULT_F_3D` (section 6) | OPEN |
| 2 data-integrity tool, thresholds, labels | below | pinned here |

**Smoke (item 1.3(f)).** The smoke calls the real model `claude-haiku-5-5` at the pinned sampling settings (DESIGN section 3), the single ranker call, in a fresh scratch root that is not `HV7_ROOT` and not `/data/mal/arena/bots.json`; its ledger is never opened for price, fill or P&L; counts, timings and flags only. A StubModel run does not meet it. Its cost is inside the 0.30 USD/day cap: smoke API cap **0.10 USD** (reserve-before-call guard, lowered only), on a smoke ledger that is not `RANK-L`. The error limits are pinned now, before it runs, and are HV6's smoke limits (`git show origin/claude/hv6-registration-b:ARTIFACTS/lab/hv6-registration-1026.md`, section 7b: FAIL if STALE > 5% or chunk p95 > 8 s or card refusals above 5% of cards attempted or a run error or spend above the cap; INCOMPLETE under 30 error-free LLM calls; else PASS) adapted to the single ranker call:

- FAIL if the share of ranker calls that become a FALLBACK (API error, timeout, refusal, a reply that is not a full permutation of the shown ids; why-defects are report-only, DESIGN section 3) is above 5% of calls;
- FAIL if the ranker call latency p95 is above 8 s (HV6's p95 limit; the live deadline is `t_e`, 45 s after `t_d`);
- FAIL if card refusals (as_of, chain stale or unavailable, holders stale) exceed 5% of cards attempted (built + refused);
- FAIL on any run error, traceback or restart, or if spend exceeds 0.10 USD;
- INCOMPLETE under 30 error-free ranker calls;
- else PASS.

A FAIL is fixed by code at a new sha, never by tuning a limit. The pinned notebook entry (blocking list, tool sha256s, this PR's head sha, instant, month-to-date API spend) precedes the job.

**Smoke tool (resolves the open point).** `tools/hv7_smoke.py` (sha256 `8f7e1d57caae63ecc92be64abb64fb5e4152142f78372f18e5bfe665086f3753`) and wrapper `scripts/ops/hv7-smoke.sh` (sha256 `d7584dc4d77cda26a6062bfd18ddfb39f989b2959fbe3ed9abf47b05301034d6`), both from this branch (checked with `sha256sum`; the pin is void if either file changes; the wrapper sha256 changed at the head after c5942efa because its wall-time comment now says 32-33 h; the tool sha256 changed again at the fix for job #1776, see the setup-crash disclosure below). **Hourly design, no period compression, no prefetch override.** `Hv7Runner.tick` fires the screen only at `T % HOUR_MS == T_D_OFFSET_MS` and `Hv7Config` has no period parameter, so the tool does not call `tick`; it drives the public `poll_to`, `exits`, `watch_tick`, `screen(T)` and `enter(T)` in `tick`'s order at the runner's real instants: `t_d` = H:02:00 UTC, one per hour, 31 periods (30 h from the first t_d to the last, last t_e 45 s later). The first t_d is the first H:02 at least 8 min (warm-up) + 14 min (one prefetch window) + 1 h after the start: the first H:48-H:02 window is the cold fill and is not screened (a conservative, pre-declared skip: in the synthetic harness the first screen after a start is warm at 58 of 60 start minutes; the two bad start minutes, H:00 and H:01, spend the 500/h budget in a start-up burst and the next screen refuses, 67/75 at K=300 for H:00), the next is. Wall time about 32-33 h ( the start to the last t_e). The chain source is the runner's own `hv7_runner.live_chain_source(root)` (HV4Chain, policy-v2 prefetcher gated to the real window [H:48, H:02), 500 calls/h, WATCH_MAX 200), state under the smoke dir; the prefetcher is started as `hv7_runner.main` starts it (`chain.prefetch.start()`, hv7_runner.py line 1016) and fed by the runner's own `Hv7Runner.watch_tick`. The only test-only knobs (`period_ms`, `anchor_h02` of `run_smoke`) are refused whenever the real chain is built, and the CLI has no period or window option. Each call is the runner's own `_ask` on the real RANK_v0 request bytes. Spend: a hard stop at 0.10 USD over all RANK-L day files before each call, plus the frozen guard's per-day cap lowered to 0.10 (the caller's worst-case reserve is the last fence). The tool prints counts only (calls, valid permutations, FALLBACK, model refusals, latency p50/p95/max, tokens, USD from the cost ledger); it never opens the engine ledger and names no pool or price. Tests: `tools/test_hv7_smoke.py` (24 tests, fake caller, no API) and `tools/test_hv7_smoke_phase.py` (3 tests, synthetic).

**Chain calls (synthetic, `python -m tools.test_hv7_smoke_phase`; real gated prefetcher, 500/h, model market, no RPC; all 60 start minutes x K = 100/300/1000, 31 screens each).** Full screens (8 fresh mint cards) 31/31 at every start minute and K. Card-refusal share over cards attempted (tried until 8 fresh): worst cell 0.0040, mean 0.0001-0.0002, against the pinned 5%. At most one screen per run lacked the holders block (a v2 holders absence is not a card refusal). Chain calls per run 14,243-14,956 (the window spends about 458 of the 500/h budget each hour, not 14/60 of it); max in any rolling hour 458 (cap 500); max in one UTC day 8,918-9,044 (cap `HV7_RPC_CALLS_PER_DAY` 12,000). The run spans 2-3 UTC days. Credit cost per call is not measured here; the report's `helius_credits` field records the actual figure.

**Setup-crash disclosure (job #1776, smoke ID smk-1027a, #1019 head 8aaca1ec, repo at c8fcf761).** The first real smoke job crashed about 3 s in, at setup, with 0 API calls and 0 chain calls: `hv7_smoke.run_smoke` called `register_all(..., now_ms=now, verify_sha=verify_sha)`, and `arena_paper.register_bot` refuses `now_ms` and `verify_sha=False` unless `MAL_ARENA_TEST=1` (the wrapper unsets it). Under the pinned "run error" line that is a FAIL. It was fixed by code at a new sha: the production path now registers the four scratch-registry bots with the real clock and `verify_sha=True` (`verify_sha_on_origin` against origin/main from the repo at the frozen sha); `now_ms` / `verify_sha=False` are passed only when a unit test asks for them. The unit tests missed it because they ran with the test overrides allowed; new tests run the setup with `MAL_ARENA_TEST` unset against a local bare origin (real sha verification, line `time_source` wall_clock, `verified_sha` set) and assert the production refusal is active for the overrides. No other `MAL_ARENA_TEST`-gated path remains in the real run (the ledger free-space floor of 20% applies in production and this host has 33% free). No limit changed; the next run uses a fresh ID.

**Disclosure (QP ruling on PR #1019, 2026-10-27).** The smoke screens once an hour at H:02 UTC (31 screens), the same instants as the live runner, with the live prefetch window of [H:48, H:02). The smoke's chain-prefetch regime is the live regime, at the live instants, over 31 hourly screens of whatever the live tape holds. A live start or restart inside [H:48, H:02), above all [H:00, H:02), can starve the next H:02 screen (see the Runner start instant paragraph in section 8), and the smoke never measures that (its first window is an unscreened cold fill); its card-refusal share does not measure any future live H:02 refusal share. Live H:02 fact freshness is also shown by the synthetic `tools/test_hv7_prefetch_phase.py` (no RPC) and is first observed in the live hours. `report.json` field `prefetch_window` records the regime used (`runner_H48_H02_live_regime`).

Exact job command (run only after the notebook pin; `<ID>` fresh, `<REPO>` a clean checkout at `HV7_FINAL_SHA`; memory >= 8 GB):

`HV7_FINAL_SHA=<HV7_FINAL_SHA> FEED_DELAY_SLOTS=7 sh scripts/ops/hv7-smoke.sh <REPO> <ID>`

which execs `/data/mal/venv/bin/python -I tools/hv7_smoke.py --repo <REPO> --smk /data/mal/arena/haiku-v7/smoke/<ID> --cap-usd 0.10 --expect-sha <HV7_FINAL_SHA> --periods 31 --warmup-min 8 --feed-delay-slots 7` with `MAL_HV4_RPC=helius` and the key sourced from `/var/lib/mal/anthropic/anthropic.env` into the child only.

`RESULT_SMOKE`: PLACEHOLDER (not run).

**Data-integrity tool (item 2).** `tools/arena_hour_health.py`, sha256 `227f92be466fec0bd6c6502bcece6b2e7755d0a675a30d08a2dd79d6353903b5` (checked with `sha256sum` on this branch; PR #805, merged `0a5aef86`; the same pin as HV4's L6/L7). HV6's pack names the tool and thresholds only by reference to HV4 L7; HV7 pins the HV4 L7 FINAL thresholds file `ARTIFACTS/lab/hv4-thresholds-FINAL-1025.json`, sha256 `da13ccc28457eaab2906548f318f76ce5c4598ecf7c007affcc3d8e7bd8b2eed` (verified with `sha256sum`), one value per check per UTC hour:

| check | value | label |
|---|---|---|
| tape-hour file present, size > 0 | required | price-path (tape-level, both labels) |
| `max_gap_s` | 3600 | price-path |
| `max_sample_gap_s` | 900 | price-path |
| `min_samples_per_hour` | 12 | price-path |
| `feed_max_nonnull_gap_s` | 900 | price-path |
| `feed_writing_paused` | 0 | price-path |
| `feed_pauses_delta` | 0 | price-path |
| `feed_rows_dropped_delta` | 0 | price-path |
| `max_input_age_s` | 120 | decision-input |

HV7 reads the PumpSwap pools of the arena tape (`READ_ROOTS = (hv6_cards.TAPE_ROOT, HV7_ROOT)`); the manager confirms before the clock that these HV4 values (including `max_input_age_s` 120) are the ones HV7 is pinned to, or supplies a different file with its sha256. Report-only, never excluding: scan and decision latency, RSS, call budgets, decision counts (Am.6 item 2.2). The 300 s sampler job and the daily hour-table job are MANAGER-owed (section 9). The tool is pinned as path@sha256 here; the thresholds are never changed on a counted window.

### 3e. Config pins as the code sets them (read-only, clean detached checkout at `687c77c64a777b91aba2514744287304ae1969ac`, `/data/mal/venv/bin/python -B`, 2026-10-27)

| Pin | Value |
|---|---|
| `--feed-delay-slots` | 7 (as HV4's record, `ARTIFACTS/lab/hv4-registration-1023.md`, and HV6's pack) |
| `StackConfig.as_dict()` of `tools.hv6_runner.stack_config(7)` (the call `hv7_runner` makes) | `{"e1_floor_per_005": 384022, "exit_lag_s": 0.55, "feed_delay_slots": 7, "guard": 1.15, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil", "portal_ppm": 0, "press_intercept_provisional": -1.4197242838379789, "rent": 0, "send": 505000, "strand_rule": "both"}` |
| sha256 of that dict (`json.dumps(d, sort_keys=True)`, UTF-8) | `b9de8e6f460bcf40c1a25b48ab0eb05b2d193930713201c496c533d20b9212ce` |
| send | 505,000 lamports (`hv6_runner.STACK_FIELDS`); HV7 is not Am.8-covered |
| engine blobs (`git hash-object`) | `tools/arena_feed.py` `d6969658f9d2f23f45a29b3ee785fc0d7b746774`; `tools/arena_paper.py` `5eef156bb617138a4c04fca6779f9ff4c5215b13`; `tools/arena_score.py` `f3adcd69c7ef62392eafc511b40d93a0dc31f13c` |
| engine file sha256 | `tools/arena_feed.py` `7a9d424b6584b097ee61e4c7b48bef6e43b317bdd438ba588074057b5828826f`; `tools/arena_paper.py` `9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514`; `tools/arena_score.py` `c4859323622794f5f97a131eb32256c905ca2ab6c1adf8ab21568fc2a98f27f3` |
| `--tape` | `/data/mal/arena/tape` (`hv6_cards.TAPE_ROOT`) |
| `--root` | `/data/mal/arena/haiku-v7` (`HV7_ROOT`) |

These are re-checked by the same commands at `HV7_FINAL_SHA` (the engine files and `hv6_runner` are expected unchanged since 687c77c6; `git diff 687c77c6 <final> -- tools scripts` must be empty or the values are recomputed).

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
| RESULT_E1 | `scripts/ops/hv7-e1-drill.sh`, job #1749, notebook n_c2uZR1cPRF8I7A | PASS. Job #1749, `sh scripts/ops/hv7-e1-drill.sh` at 687c77c64a777b91aba2514744287304ae1969ac, 62 tests, 0 API calls. A resume/restart drill, not a production-halt drill (section 3c). Valid only if `HV7_FINAL_SHA` = `687c77c6`; otherwise re-run at the final sha |
| RESULT_UNIT | unit tests at `HV7_FINAL_SHA` (Am.6 item 1.2) | OPEN (MANAGER) |
| RESULT_SMOKE | real-model smoke of at least 2 h (section 3d) | OPEN (MANAGER) |
| RESULT_PRE_T0 | pre-T0 counts check by 2026-11-01T01:00Z (section 8, Pre-T0 counts check) | OPEN (MANAGER) |
| C2 pricing independence, one pool, one instant, all bots (every leg, both fail models, all stakes) | `tools/test_hv7_runner.py::test_pricing_independence_one_pool_one_instant_all_bots` | test exists at 687c77c6; green run at the final sha is `RESULT_UNIT` |
| C2 pricing independence inside the loop | `tools/test_hv7_runner.py::test_pricing_independence_inside_the_loop` | as above |
| C2 U holds all N open positions (8), one entry per arm at `t_e` | `tools/test_hv7_runner.py::test_one_entry_per_arm_at_t_e_and_u_holds_all_n` | as above |
| C2 1.9 s leg mapping from `t_e` (landing slot = corrected slot + `engine._k(1.9, 0.4)`, same for every bot, decision instant `t_e`) | `tools/test_hv7_runner.py::test_pricing_independence_one_pool_one_instant_all_bots` (the 1.9 s leg assertions) | as above; DESIGN section 4 says the registration states it, and this row is that statement |
| Offline rehearsal, run 2 (of record) | n_jDI1FRScLVaBEA; `/data2/mal/hv7-rehearsal/run2-judged/` | PASS (`tools.hv7_rehearsal_validate`): 70 sets, 140 calls (not 72/144), valid 140/140 (needed 133), slot-1 share 0.12142857142857143 (bar 0.25), every other slot ok, A/B top-1 agreement 0.7 over 70 sets (bar 0.20800978857941996), chi-square 5.828571428571429 (report only), `content_driven_report_only` true (report-only), `outcome_free` true. Manifest sha256 `547fb525ff5a8fce8bc1f3f7bb2fabb7760785bdaa4d439cb0f53e7cf4d92c9e` |
| Offline rehearsal, run 1 | `/data2/mal/hv7-rehearsal/run1-4c69462c/` | **VOID** (section 7) |
| HV7_FINAL_SHA | | OPEN |

Rehearsal caveats (run 2; run 1 is void, section 7): the subagent calls were not independent, and the agents mistyped ids and self-corrected in 5 of 28 groups. The rehearsal measures parse, position bias and A/B agreement under random display order. It is not a skill test (no outcome is joined). The bar is a kill-only floor (DESIGN section 7).

## 7. Seal and disclosure block

- The rehearsal and proof days 2026-10-21..23 are walk-2-hour information (DEC-030 section 2 R1). Ledger checks: n_bjZWYzCNL60ayw and n_t3I6p9TsBfvdbA.
- No outcome is ever joined to the rehearsal sets: no rank IC and no offline skill check, now or later.
- Sibling overlap with HV6 (and HV4 lane G): the same days give no independent confirmation; the best of the siblings at any read is in-sample (DEC-031 Am.3 item 8(e)).
- Memory-housekeeping caveat for revived pools (the `#1018` config comment in `tools/hv7_runner.py`): a pool revived after more than the state's 6 h eviction horizon idle has had its `QuoteHistory` swept, so its `quote_sol_15m_ago` and `quote_sol_peak` facts are computed from the quotes since the revival only; the sweep follows the state's own eviction and is not exact against a never-swept history. `tools/test_hv7_runner_mem.py::test_revived_pool_after_sweep` pins the behaviour.
- Sealed areas not touched by this work: HV4 and Q50 outputs, meta-capture, DBC fwdv1, FLOOR G1, LaunchLab holdout.
- Disclosed prompt changes (DESIGN section 3, quoted in full in section 3b). (1) BASE RATE: GEN_v0 line 25's closing "Do not buy to fill a quota and do not skip to stay safe: a BUY pays the round trip cost before any move, a SKIP forgoes whatever a good card offers. Judge each card on its fields alone." is replaced by "The book trades your first choice and pays the round trip cost before any move, so rank by what the card fields support. Judge each pool on its fields alone." (2) MEASURED DIRECTIONS: GEN_v0 line 31 ("do not claim that one of them improves the odds") is replaced by the clause "you may argue from them, but say it is a hypothesis, not a lab result" (QP-RULING-1026-entry-rate (c)); GEN_v0's conviction and size sentences are dropped. Every measured BASE RATE sentence before the replaced ones is byte-equal to GEN_v0.
- **Offline rehearsal disclosure (QP item 1).** Run 1 (`/data2/mal/hv7-rehearsal/run1-4c69462c`, git_head `4c69462c`) is **VOID**: all 8 agents wrote scoring scripts, so A equalled B by construction (agreement 1.0). Its replies file is `replies-all-SCRIPTED-VOID.jsonl`, sha256 `147780b65c91e12d908e0949c7000627a155dc78d82f9bec0e5372f37cc6a821`. Running the validator on it (the validator is outcome-free, run by this writer on 2026-10-27 for this disclosure only) gives PASS on every bar (parse 140/140, slot-1 share 0.11428571428571428, other slots ok, agreement 1.0): run 1 failed no bar, and the re-run was triggered by a protocol breach (a scripted ranker), not by a bar result. The brief change for run 2: scripting forbidden (BRIEF.md), and the A and B shuffles of a set went to different agents, so no agent saw both shuffles of a set. Run 2 re-ran the same 140 prompts (prompt bytes and manifest unchanged: shared manifest sha256 `547fb525ff5a8fce8bc1f3f7bb2fabb7760785bdaa4d439cb0f53e7cf4d92c9e`, git_head `4c69462c`; replies `replies-all-judged.jsonl` sha256 `c1a5e011caac64ddc7bc290074ba254ae27f8f236b24a331e7497c7226bedefb`). Sizes: 70 sets, 140 calls (not 72/144). Cumulative calls across both runs: 280 <= 288 (the cap rule of DESIGN section 7). Run 2 is the rehearsal of record. The bars are kill-only floors: a pass shows consistency, not skill (DESIGN section 7; a model that always picks the `f.vol_60m` leader passes and gives L about equal to S). `content_driven_report_only` is true and is report-only.
- Seal block additions (QP item 10). (a) Ledger D2/D3 check for the counted window 2026-11-01..11-11 (PumpSwap pools): MANAGER-owed, recorded before the clock (open). (b) HV7 reads only `hv6_cards.TAPE_ROOT` and its own root (`READ_ROOTS` in `tools/hv7_runner.py`), so it touches no DBC `fwdv1` and no H5 pool in its first 360 s. (c) P0 days are 2026-10-21..23 (the same walk-2-hour days as the rehearsal); P0 counts periods after the impact veto (`Hv7Runner.screen`). (d) The P62 creator fee-share veto is **not in v7.0**: there is no P62 code in `tools/hv7_*.py` at 687c77c6; if it merges before the freeze, its QP OK is cited instead.
- Seal-condition contact (manager, 2026-10-27, notebook entry n_QQbWEgy5MdYNWA, 'SEAL-CONDITION CONTACT (manager, disclosed)'): on the 10-21 real-layout replay #1739 (StubModel arms, pre-clock walk-2-hour day) the manager read pooled engine-ledger exit_reason counts (609 trade rows), which QP (PR #1049) rules is an outcome summary beyond 'md5 and counts only'. No price, pnl or return was read; no HV7 forward day, rehearsal set or holdout block was touched. QP to rule the impact before registration.

## 8. Registration command (from `tools/hv7_register.py`; run once by the manager)

Run from a clean checkout at `HV7_FINAL_SHA` with `/data/mal/venv/bin/python` (the dry run also calls `verify_sha_on_origin`). Flags: `--sha` (required, 40-hex), `--clock-start-ms` (required), `--entries-halt-ms` (required), `--registry` (required unless `--live-registry`), `--live-registry`, `--execute`, `--confirm`. Dry run by default. The confirm phrase is `REGISTER-HV7-FOUR-BOOKS:<sha>:<clock_start_ms>:<entries_halt_ms>`, so a changed pin changes the phrase.

```
# dry run first; it prints the plan and the exact --confirm phrase and writes nothing
/data/mal/venv/bin/python -B -m tools.hv7_register --sha HV7_FINAL_SHA --clock-start-ms 1793491200000 --entries-halt-ms 1794355200000 --live-registry
# then, once, with the printed phrase
/data/mal/venv/bin/python -B -m tools.hv7_register --sha HV7_FINAL_SHA --clock-start-ms 1793491200000 --entries-halt-ms 1794355200000 --live-registry --execute \
  --confirm REGISTER-HV7-FOUR-BOOKS:HV7_FINAL_SHA:1793491200000:1794355200000
```

(`HV7_FINAL_SHA` is replaced by the 40-hex lower-case sha in both places.) The runner command, from the same clean checkout, every value spelled: `/data/mal/venv/bin/python -B -m tools.hv7_runner --frozen-sha HV7_FINAL_SHA --clock-start-ms 1793491200000 --entries-halt-ms 1794355200000 --feed-delay-slots 7 --tape /data/mal/arena/tape --root /data/mal/arena/haiku-v7 --owner-cost-line-ref q_6_kzYJva9XXuYQ` (`--registry` is left at its default, the real registry; the module entry point is checked at the final sha); the runner refuses a clock or halt that differs from the registry. Count tool: `/data/mal/venv/bin/python -B -m tools.hv7_count --root ROOT --clock-start-ms 1793491200000 --clock-end-ms <instant>`.

**Runner start instant (QP ruling on PR #1019).** The live runner starts in [2026-10-31T22:05Z, 2026-10-31T22:45Z], outside the prefetch window. A start (or restart) inside [H:48, H:02), and above all in [H:00, H:02), lets a start-up burst spend the rolling 500/h budget and starves the next H:02 screen (synthetic harness `tools/test_hv7_smoke_phase.py` with the first screen at the first H:02 after the start: start minutes H:00/H:01 refuse 67/75 and 272/280 cards at K=300 on the next screen; every other start minute refuses 0-2 of 8-10). The 2026-10-31T23:02Z screen runs before T0: L makes one API call, the arms' buys are refused by the engine (`before_clock`, arena_paper.py lines 1088-1089: the write instant precedes the registered clock 1793491200000, checked before any ledger row is written), no position is opened, and every HV7 count and read filters to [T0, ...) (`hv7_count.count`, `blind_read_instant`, `reconcile_ids`, the L rank rows, `coverage_check`, E1, `fallback_check`), so these rows are neither counted nor lost. The first counted screen is 2026-11-01T00:02Z. A mid-week restart avoids [H:48, H:02) where it can; each restart and its minute go in LAB_STATE. If a start ever slips into the hazard band, that screen's population shifts to lower-volume pools (a selection effect on the book's mean); it is avoided by choosing the start instant, never repaired after the fact.

**Pre-T0 counts check (MANAGER-owed, outcome-blind, counts only, by 2026-11-01T01:00Z).** In each arm's decision log, count the rows with ms < 1793491200000 by kind (`entry`, `order_refused`, `rank`); each `entry` should have one `order_refused` beside it. Each HV7 ledger should hold no row of kind open, refused, trade or excluded stamped below 1793491200000; only `start` rows are expected there. Record the counts in the registration notebook. If any such row exists, stop and re-rule before the 2026-11-02 clock. (The `before_clock` refusal is tested only in the engine tests; no HV7 test drives a pre-clock `enter`, so this check is the HV7-level evidence.)

**Owner line for pre-11-01 spend (MANAGER confirms).** The manager confirms that the owner's line on `q_6_kzYJva9XXuYQ` covers spend before 2026-11-01: the one RANK-L call at 2026-10-31T23:02Z (about 1/31 of the smoke spend; the smoke report gives the measured USD per call; under the guard's 0.30 USD daily cap) and the smoke itself.

## 9. Order before the clock (DESIGN section 6, unchanged)

Final sha frozen, proofs of section 6 filled (including `RESULT_UNIT`), the Am.6 real-model smoke of at least 2 h at the final sha with its limits pinned in a notebook entry before it runs (section 3d; Am.6 replaces the 24 h stub), the hourly data-integrity tool and thresholds pinned (section 3d), the 300 s sampler and daily hour-table jobs, the **E1 host-side MiScusi check job** scheduled before T0 for `1793664000000` (MANAGER-owed, section 3c), the D2/D3 ledger check, records PR with QP OK and a reviewer verdict on the exact head (comment ids go in the registration notebook entry), DEC-031 Amendment 16 merged, registration, clock. Amendment 16 must merge before the clock: if it has not merged before HV7's clock, HV7 gets no clock. Re-check the C5 flags after PR #1021 merges.

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
   *Status (pack, not part of the quote):* the C2 tests exist at 687c77c6 (section 6 rows `test_pricing_independence_*` and `test_one_entry_per_arm_at_t_e_and_u_holds_all_n`); a green run at the final sha is `RESULT_UNIT`.
7. **Sibling overlap** with HV6/HV4 lane G: no independent confirmation from the same days.
