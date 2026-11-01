# HV7 forced-rank: a Haiku ranker that must pick (design v0, 2026-10-27)

Desk design, lane P63 (manager17). Records only. **Nothing here says HV7 makes money.** HV7 has 0 trades, no sha, no clock. No book has
cleared the promotion gate. Paper only. No API call, job, tape read, card read, or HV4/Q50/HV6 output was used. Every rate, token, sd and
cost figure below is an assumption or arithmetic, not a measurement, unless a source is named. Times from `date -u` (2026-10-27T00:58Z).

**v1 (2026-10-27, after QP CHANGES on #996, comments 6431175846 and 6431180577).** Changes: the L - U contrast is read once at T0 + H with >= 8
non-zero days (section 4); the power claim is replaced by QP's simulation and an MDE (section 4); the 10% fallback rule is checked at E1, B and
T0 + H; cross-arm pricing independence is a hard C2 test; a report-only day-block bootstrap; the 1.9 s leg mapping; QP seal conditions (c)1-5
for the rehearsal; the card field is `f.vol_60m`; the cost section needs HV7's own owner OK and a cap table; QP (d)1-4 are carried into C1;
the manager's kill-review confirmation is recorded.

**v2 (2026-10-30, after real-model smoke #1779 FAILED on the fallback line, 31/31; before any HV7 clock; no outcome seen).** Structure of the 31
cached replies only (stop_reason, output_tokens, length, fence, JSON validity, item and id counts; no id, why text or order): 30 stopped at
max_tokens 600 with 6 or 7 of 8 items written (2 of them inside a ```json fence that never closed), 1 ended after 4 items with one id twice.
Changes: max_tokens 600 -> 2000 (section 3; measured 78-206 chars per item and 1.412-1.620 chars per output token, so a full 8-item reply is at
most about 1,167 tokens); ONE markdown code fence around the whole reply is removed before the permutation check (section 3; prose, a second
fence or an unclosed fence stay FALLBACK); the cost row at the new max_tokens (section 5); the clock moves to one of the rolling clocks
2026-11-03..11-08T00Z with H = 14 days (sections 4 and 6; the owner extended the API horizon past 11-11 on 2026-10-28, so H follows the Am.4
formula, and the registration must quote that line before the clock). The prompt bytes (RANK_v0, rank.txt) are unchanged.

Sources read: `docs/research/hv6/DESIGN.md` (sections 1, 2, 4, 5, 6), `/data/mal/hunt-1008/push-1016/hunt30/hv6/QP-RULING-1026-entry-rate.md`,
the manager17 state log (HV6 lines only), DEC-031 Amendments 3, 4 and 6 (grep), library rows `web-llm-traders-1/2/4/5/7/8` and `haiku-v3-4`,
`tools/hv6_*.py`, `tools/hv4_prompts.py`, `tools/haiku_caller.py` (price table), and the P58 rehearsal's `NOTES.txt`, `build_prompts.py` and
prompt byte sizes (keys and lengths only; no card or reply contents opened).

## 1. Problem and idea

The lab's Haiku traders abstain, so their books never reach n:
- HV4's LLM arms: no buys in their first 2 days (digest `decision_log_sha256` None, registered abstain behaviour, QP-RULING-1026-hv4-liveness).
- HV6's entry prompt: 0 BUY in 240 pre-clock rehearsal cards (QP-RULING-1026-entry-rate: BUY 0/192, WATCH 24/192, SKIP 166/192, PARSE_ERROR 2/192 on the
  192 set; 0 BUY on the 48 set). v6.1's one-sentence fix: 0 BUY / 16.1% WATCH / 83.3% SKIP on 192 fresh cards (state log 23:27Z). The QP ruling's
  expected gate outcome for HV6 L, L-nr, F is NOT_DECIDABLE.
- Cause (QP ruling (b)): honest prompts ("Every universe measured so far loses before any selection at this arena's costs ... Do not buy to fill a
  quota"; "The tag is never a reason to buy") plus a SKIP option make abstention the dominant reply. The QP ruling also forbids softening those
  statements to raise the BUY count.

**HV7 removes the choice to abstain, not the truth.** Each period a fixed causal code screen yields N candidates. Haiku must order them; the book
trades the top k. The entry rate is set by the design (periods/day x share of periods with a full candidate set), not by the model. The prompt
keeps every measured BASE RATE sentence byte-equal to GEN_v0; only the closing buy/skip sentences are replaced, with "The book trades your first choice and pays the round trip cost before any move, so rank by what the card fields support. Judge each pool on its fields alone." (section 3); MEASURED DIRECTIONS ends with the QP clause. It also says: "You must rank. The book trades your top pick. Your job is relative selection: which of these is
least bad / most likely to do better than the others over the next 60 minutes."

The consequence must be said up front: **a forced book trades the screen's base rate plus whatever Haiku's selection adds.** Every universe the lab has
measured loses before selection (HV6 section 1: survivors >= 24 h -8.27% per 24 h; cost floor about -2.4% per round trip, -3.52% with rent). So the
pre-registered expectation for L's absolute gate read is **FAIL**, not PASS. That is still progress: HV7 reaches a decision (PASS or FAIL) and
measures selection skill with a paired null, where HV4/HV6 reach NOT_DECIDABLE and measure nothing.

Starting rows: `web-llm-traders-5` (Haiku hourly re-ranker of LightGBM top-10; not run), `haiku-v3-4` (same idea, not built because "outcomes of
non-selected ranks are not in any cache"; arm U below fixes that going forward), `web-llm-traders-2/4` (BUY/SKIP and veto designs that
starved: 3 of 150, 36 of 36 vetoed), `web-llm-traders-1/7` (frozen playbook, nightly reflection; used only for the optional L-mem in v7.1),
`web-llm-traders-8` (veto layer; not used: HV7 has no model veto).

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

**Model and no-abstain rule.** `claude-haiku-5-5`, thinking disabled, no temperature, max_tokens 2000 (600 until v2: smoke #1779 cut 30 of 31 replies at 600), one 5-min cache marker on the system block
(`hv4_prompts.CACHE_MARKER`). The reply must be a JSON list that is a full permutation of the shown candidate ids (exactly N, no duplicates, no
others), each with `why` <= 20 words naming >= 1 card field; one markdown code fence around the whole list (``` or ```json, only whitespace
outside it) is removed first (v2), anything else around the list is not a JSON list. There is **no SKIP, WATCH or VETO token**. A reply that is not a full permutation, a
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
v2: the owner extended the API horizon past 11-11 on 2026-10-28, so at the rolling clocks 2026-11-03..11-08T00Z H = max(14, ceil(150 / 23.33)) = 14
(`hv7_register.halt_for`), D = 14, window end 2026-11-17..22T00Z.

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

## 5. API cost (repo price table)

Prices: `tools/haiku_caller.py` `PRICES["claude-haiku-5-5"]` = input 0.10, cache_write 0.125, cache_read 0.01, output 0.50 USD per million tokens
(the lab's table; not re-checked against Anthropic's price page in this lane). `haiku_caller` refuses a cache TTL other than 5 min (line 46), so
hourly calls never read the cache from the previous hour: price every L call as a cache **write** plus uncached input.

Token sizes from the P58 prompts (chars, not tokens; 4-card call: system 8,622 chars, user 12,980 chars). At a conservative 3 chars/token (JSON
with numbers; not measured): system about 2,874 tokens, about 1,082 tokens per card, about 400 tokens of ranking instructions.

| Call | Input | Output | USD/call | Calls/day | USD/day |
|---|---|---|---|---|---|
| L rank, N = 8 | 2,874 cache-write + 9,056 new | 400 | 0.001465 | 24 | 0.0352 |
| L rank, N = 8, worst (max_tokens 600, before v2) | same | 600 | 0.001565 | 24 | 0.0376 |
| L rank, N = 8, worst (max_tokens 2000, v2) | same | 2,000 | 0.002265 | 24 | 0.0544 |
| L rank, N = 12 | 2,874 cache-write + 13,384 new | 400 | 0.001898 | 24 | 0.0455 |
| 30-min periods, N = 8 | as row 1 | 400 | 0.001465 | 48 | 0.0703 |
| v7.1 L-mem (same instant as L, cache read, +2,000-token memory) | 2,874 read + 11,056 new | 400 | 0.001334 | 24 | 0.0320 |
| v7.1 nightly reviewer (HV6's figure) | 60,000 new | 4,000 | 0.0080 | 1 | 0.0080 |

Check, row 1: (2,874 x 0.125 + 9,056 x 0.10 + 400 x 0.50) / 1e6 = (359.25 + 905.6 + 200) / 1e6 = 0.00146485. Retries (`MAX_ATTEMPTS = 3`) can
triple a call's worst case; the reserve/settle ledger charges the worst case when usage is unknown.

**Owner OK (QP cost ruling).** The owner's K1 approval ("I approved everything", n_XpA1PK4KfXvB1Q, answering q_NLvnMwJHKryStQ, recorded as
"K1 HV6 <= 3.00 USD/day 10-30..11-11") covers HV6 only. **HV7 is new API spend and needs its OWN owner line**, quoted verbatim in the
registration, along the lines of: "HV7 <= 0.30 USD/day, 11-01..11-11, inside K1's 3.00 USD/day and the 200 USD to 11-11". No HV7 API call (smoke
included) before that line exists. The offline rehearsal (section 7) uses Claude-plan subagents and spends no API budget.

**Cap table (USD/day, hard caps, not expectations).**

| Book | Cap | Source |
|---|---|---|
| HV6 v6.0 | 2.20 | sum of its per-ledger caps: 4 LLM arms x 0.50 + reviewers 0.15 + regime review 0.05 (HV6 DESIGN section 6) |
| HV6 v6.1 | 0.00 | PARKED (#993 draft, not registered); takes no share. If it is ever registered it needs its own line in this table and the sum must still hold |
| HV7 v7.0 | 0.30 | HV7's own ledger (this design) |
| **Sum** | **2.50** | <= the K1 3.00 |

**Enforcement (one of the two, fixed in the registration).** Either (a) **pinned caps with a sum test**. C1 carries only the arithmetic on
constants (2.20 + 0.00 + 0.30 <= 3.00); that is not enforcement. The enforcement test goes in **C2/C4**: at HV7's frozen sha it reads
`tools.hv6_agents.caps_total_usd()` plus HV7's cap constant (config only, no ledger, spend or output of any book) and asserts the sum <= 3.00;
registering another book under K1 without a new term in that test fails CI. Or (b) **one shared guard** that enforces 3.00
across the HV6 and HV7 ledgers at reserve time. (a) is preferred: it needs no cross-book ledger read and no change to HV6. "The manager sums
daily" is a report, not enforcement. HV6's global 3.00 in `hv6_agents` counts only HV6's own ledgers.

Window cost at cap: 10 d x 0.30 = 3.00 USD; expected about 0.35-0.70 USD. QP's worst case with 3 retries at 48 calls/day is about 0.225 USD/day,
under 0.30. Against the 200 USD cap to 11-11: HV6 DESIGN's worst case 116 of 200 becomes 119. A budget stop is a FALLBACK (section 3), counted
toward the 10% NOT EVALUABLE line, so a budget fault cannot raise L.

## 6. Clock

- Kill-review week: CLAUDE.md says never add forward books during one. **Manager's confirmation (manager17, relayed 2026-10-27; also posted on
  PR #996): LAB_STATE and docs/HANDOFF.md list no kill-review week after 2026-10-05, so the 11-01 clock is not blocked by one.** The registration
  repeats this check line on its own date.
- Order: C1 (rank module) -> offline rehearsal (section 7) and C2 (runner) in parallel -> C3 (count tool, register, drills, md5 proof, P0 on 3 full
  closed pre-clock days) -> 24 h stub at the candidate sha (Am.6 pattern) -> records PR with QP OK on the exact head and a reviewer verdict ->
  registration -> clock. HV6's v6.0 registration has priority to its 2026-10-29T12:00Z fallback deadline and holds the 16 GB research-0 replay slot
  (P2 #1683 / P3 #1684); HV7's replays queue behind it.
- **Earliest realistic clock: 2026-11-01T00:00:00Z** (freeze about 10-30, stub 10-30..10-31). Fallback 2026-11-02T00:00Z. Window end 2026-11-11T00Z
  (API horizon), so D = 10 (or 9). A slip to 11-02 leaves the L - U read at D = 9; >= 8 non-zero days is still reachable, with less power. Needed rate 10/day (11.1/day for 11-02). Not assumed: any API budget past 11-11. If the owner extends the horizon,
  H follows the Am.4 formula only if the extension is in the registration before the clock (never after).
- **v2 (2026-10-30):** the 11-01 and 11-02 clocks are lost (smoke #1779 FAILED). `hv7_register` now pins six rolling clocks 2026-11-03..11-08T00Z,
  each with H = 14 days (2026-11-17..22T00Z), after the owner's 2026-10-28 extension of the API horizon past 11-11. Window cost at cap:
  14 d x 0.30 = 4.20 USD. The tape producer and every MANAGER-owed job must cover the new window end.
- The clock also needs the owner's HV7 cost line (section 5).

## 7. Offline rehearsal before any registration (Claude-plan Haiku subagents, no API spend)

Purpose: parse rate, position bias and rank consistency, outcome-blind. Not a skill test: no outcome is joined, and none may be (arena tape is
counts only; in-clock rows are in-sample).

- **Sets.** 72 hourly sets, one per hour 2026-10-21T00Z..2026-10-23T23Z (pre-clock), each up to N = 8, built **only** from the three pinned HV6
  real-layout drill card files (condition 1 below): per hour, cards stamped in [H:00, H:15), first 8 by the card's `f.vol_60m` field (the field name
  taken from `hv6_cards.select_board` in code, not by inspecting card contents first). This approximates the HV7 screen and is disclosed as such.
- **Calls.** Each set twice, with two seeded shuffles (`hv7-perm|A|...`, `hv7-perm|B|...`): 144 calls, prompt frozen in C1 before any call. One run,
  no prompt edit between calls, no variant chosen by result (QP fitting rules (i)-(iii)). A format-only fix (e.g. a JSON key name) is allowed once,
  disclosed, on the full 144 again. The prompt is frozen in a merged or pushed C1 sha before the first call.
- **Bars (pre-declared now).**
  1. Parse: >= 137/144 calls (95%) are valid full permutations.
  2. Position bias: share of rank-1 picks at display slot 1 <= 0.25 (chance 0.125; at n = 144 the 99% binomial upper point is about 0.196), and no
     display slot above 0.25. Chi-square over the 8 slots, report-only.
  3. Consistency: top-1 agreement between shuffles A and B of the same set >= 0.20 (chance 0.125 if picks are random). Below 0.20 the ranking is
     no better than position or noise, L would be R at API cost, and HV7 is **not registered** with Haiku as the ranker. >= 0.30 is read as
     "content-driven" (report-only). Kendall tau between the two full orders, report-only.
- **Seal conditions (QP comment 6431175846 (c), adopted verbatim in substance).** Same class as P58 (pre-clock causal card inputs, model
  decisions only, no outcome joined; QP-RULING-1026-entry-rate (b)).
  1. Read only `/data/mal/arena/haiku-v6/drill/real-5071e29a-r16g-20261026/real-a/v6/cards/2026-10-2{1,2,3}.jsonl`, the files P58 read. Pin the
     drill id and the cards md5 in the manifest. Do not list, read or stat anything else under the drill root: no decision logs, fills, exits,
     llm rows, counters or other drill ids.
  2. Refuse any card row with as_of outside [2026-10-21T00Z, 2026-10-24T00Z). Join no later card of the same pool, and no mark, exit, price path
     or outcome.
  3. Freeze the prompt in a merged or pushed C1 sha before the first call. One run, no variant chosen by result. QP fitting rules (i)-(iii) apply.
  4. **No outcome is ever computed on these 72 sets**, now or later: no rank IC and no "offline skill check". They are walk-2-hour information
     (DEC-030 section 2 R1) and overlap the HV6 240 + 192 cards; both are disclosed.
  5. The card field is `f.vol_60m` (the `hv6_cards.select_board` key), taken from code, not by inspecting card contents first.
- **AMENDMENT 2026-10-27 (QP 6432750602; supersedes the Sets, Bars and Seal-condition 1 text above; Calls and the other seal conditions stand).**
  - **Sets.** The sets are built from HV7's OWN screen via `tools/hv7_dump_requests.py` (`Hv7Runner.screen`: `hv6_cards.build_card` at t_d = H:02:00Z, N <= 8 by
    `f.vol_60m`, hours with fewer than 3 candidates skipped), NOT from the HV6 card files. Reason (a structure count, no outcome): only 20 of the 72 HV6 sets had
    N >= 3, mostly N = 3-4. The requests are the exact `hv7_rank.build_rank_request` bytes (A and B shuffles). One run into an empty directory; the tool refuses a
    non-empty `--out`, a dirty tree, more than 24 sets per day, or calls != 2 x sets (at most 72 sets / 144 calls), and records the git head in its manifest.
  - **Orders.** The A, B and live L display orders are three independent sha256-seeded shuffles of the same screen-order cards; none depends on card content. The
    L-order request is never rehearsed, so no rehearsal reply exists for the exact live L request. The bars measure position bias and A/B agreement under random
    display order.
  - **Bars (QP's rulings, word for word from comment 6432750602 (c); sums run over the valid-permutation calls i, with N_i the set size and n the number of calls).**
    1. **Parse:** valid full permutations >= ceil(0.95 x calls). This is 137/144 when calls = 144.
    2. **Position, slot 1:** share of rank-1 picks at display slot 1 <= 2 x mean_i(1/N_i). This is 0.25 when every N = 8 (the original bar: twice chance), and it is never below 0.25.
    3. **Position, other slots:** for each display slot j, let E_j = sum over calls with N_i >= j of 1/N_i, and O_j = the rank-1 picks at slot j. Bar: O_j <= 2 x E_j for every slot with E_j >= 9. Slots with E_j < 9 are report-only. Chi-square stays report-only, with expected counts E_j.
    4. **Consistency:** top-1 A/B agreement over the S sets >= max(0.20, (sum_s 1/N_s + 2.1 x sqrt(sum_s (1/N_s)(1 - 1/N_s))) / S). With S = 72 and all N = 8 this gives 0.207, so 0.20 is the original bar, kept as the floor.
    5. **"Content-driven" (report-only):** agreement >= 0.30, or >= 2.4 x mean(1/N_s) if that is larger.
    6. **Minimum sample:** if S < 30, the rehearsal is NOT_DECIDABLE at hourly periods. A P0 hourly mean under 10.7 would also switch the registration to 30-min. In that case the dump is re-run once, with the :32 instants added (`--half-hour`), under the same caps doubled (144 sets, 288 calls). That is DESIGN's pre-written 30-min lever, chosen on structure only.
    The validator is `tools/hv7_rehearsal_validate.py` (tested); "S" in bar 4 is the number of sets with both shuffles valid, and "S < 30" in bar 6 is the number of sets in the prompts.
  - **Report-only chi-square.** Computed over the display slots whose expected count E_j is >= 5 only; never a bar. The validator also refuses its inputs unless the prompts equal `manifest.json` (each file's sha256 and call count, totals, a 40-hex `git_head`) and refuses a duplicate reply key; a half-hour manifest under 30 sets is NOT_DECIDABLE with no further re-run.
  - **30-min periods and `seq`.** The dump uses `seq = t_d // 1 h` at :02 and `t_d // 1 h + 100000` at :32 (`hv7_dump_requests.seq_for`); `seq` enters only the nonce, an opaque token in the user text. The runner today uses `seq = T // HOUR_MS`. If registration uses 30-min periods, the runner must adopt `seq_for` exactly, with a dump-vs-runner byte-equality test at :32; otherwise the registration discloses that the :32 rehearsal requests differ from live requests in the nonce only.
  - **Cap rule.** Total <= 288 calls, including the single format-only re-run allowed above (a 144-call hourly run plus one 144-call re-run, or one 288-call half-hour run). A 30-min re-run under bar 6 replaces the hourly run; it is not added to it.
  - **Seal conditions (replace condition 1).** The tape side is HV7's own screen and `_card` on arena tape 2026-10-21..23, outcome-free (same class as the card-equivalence and P0 tools; QP C3b ruling), then model decisions only. (i) The ledger checks n_bjZWYzCNL60ayw and n_t3I6p9TsBfvdbA are on record before the job. (ii) Conditions 2, 4 and 5 carry over: no card after t_d, no later card of the same pool, no mark, exit or price path; **no outcome is ever computed on or joined to these sets, now or later: no rank IC, no offline skill check** (the dump writes pool ids and t_d, which would make such a join easy; this is the binding condition). (iii) This section is the amendment and is merged or pushed before the dump runs. (iv) The sets are walk-2-hour information (DEC-030 section 2 R1) and are disclosed as such. (v) The scratch decision log under the HV7 drill root is never read. The HV6 drill decision log is never read.
- QP reads the bars as kill-only floors: the 0.20 agreement bar at 72 pairs is about z = 2.1 over chance (9 expected, sd 2.81). Passing shows
  consistency, not skill; a model that always picks the `f.vol_60m` leader passes and gives L about equal to S (L - S shows it).

## 8. Code reuse

**Reused as is (imported, not edited).**
- `tools/hv6_cards.py`: `scan`, `build_card`, `age_guard`, `select_board` key, `guarded_open`, `forbidden_imports`; through it HV4's filters,
  features, setup predicates and hard vetoes (`hv4_cards`, `hv4_chain`).
- `tools/hv6_phantom.py`: `PhantomFilter` (row hook).
- `tools/hv4_prompts.py`: `Request`, `card_sha256`, `CACHE_MARKER`, `request_sha256`, (`check_cards` is NOT used for the rank request: it caps at
  `ENTRY_MAX_CARDS = 4`; HV7 has its own local card check for N = 3..8, QP (d)4).
- `tools/hv4_model.py` / `tools/hv6_agents.py`: `CallerModel`, `StubModel`; `tools/haiku_caller.py` (reserve/settle ledger, price table, 5-min
  cache rule).
- `tools/arena_state.py` (ArenaState), `tools/arena_paper.py` (ArenaEngine pricing, `register_bot`).
- From `tools/hv6_runner.py`: `GuardedDecisionLog`, `CardSink`, `refuse_hv4_chain_state`, `chain_state_path`, `ChainSource` /
  `live_chain_source`, `QuoteHistory`, `mark_ret`, `stack_config`. **Not** the `Runner` class (5 HV6 arms, watch/re-ask, regime channel, probes):
  HV7 gets a small hourly loop instead of a subclass.
- `tools/hv6_agents.BudgetGuard`: reused if it takes ledger names and caps as parameters; its HV6 ledger table is module-level, so C2 checks this
  and otherwise adds a thin HV7 guard with the same reserve-before-call contract.

**Patterns copied into new HV7 files (HV6 files untouched).** `hv6_register.py` (hard-coded ids, `--live-registry --confirm`, stderr hygiene) ->
`hv7_register.py`; `drill_hv6_real_layout.py` + `scripts/ops/hv6-real-layout-replay.sh` -> HV7 drill and replay script; HV6's E1 restart drill.

**Not used in v7.0.** `hv5_validate` (BUY/SKIP/WATCH validator), `hv6_review` and `hv6_release` (only for v7.1 L-mem), the regime channel.

**New.**
- `tools/hv7_rank.py` (pure): `screen_set(passed, vol_60m, n=8, n_min=3, vetoed=frozenset())` (`passed` = ids that passed the scan; `vol_60m` = id -> `f.vol_60m`), `display_perm(arm, t_d, card_shas)`, `build_rank_request`,
  `validate_rank(reply, shown_ids) -> RankVerdict` (full permutation or FALLBACK with a reason code), `pick_top_k(verdict, k=1)`, `r_pick(t_d,
  card_shas)`, `s_pick(set)`, `u_set(set)`, `fallback_pick`.
- `tools/hv7_playbooks/RANK_v0.txt`, `tools/hv7_templates/rank.txt`.
- `tools/hv7_runner.py`: hourly loop (exits, then screen at `t_d`, L call, entries for L/R/S/U at `t_e`, code exits), decision log, own budget ledger.
- `tools/hv7_count.py`: P0 counter, outcome-blind decision-timestamp count for B and E1, slot histogram.
- `tools/hv7_register.py`; `tools/hv7_read.py` (gate + contrasts; owed before the read, not before the clock).

## 8a. Resume and restart (C3a, rule accepted by the manager 2026-10-27; for QP to rule on)

The hourly loop resumes from the hash-chained decision log and the engine ledger (`Hv7Runner.resume`). Stated rules:

1. A torn last log line is truncated to the last full line and a `gap` row (`torn_last_line`, dropped bytes, sha256) is appended to the SCAN log. A broken hash chain is not repaired: the runner refuses to start.
2. Open positions are rebuilt from the entry rows and the engine's `recover()`. An entry row with no engine position, no sell row and no refused row gets a `gap` row (`entry_not_in_engine`) and is never re-sent. No entry is ever made after t_e.
3. A pending period resumed at or before t_e is re-screened at t_d (the screen ids and card shas must equal the logged ones, else the period is excluded) and L is re-asked through the replay cache with the same request sha256, so no second spend. If the budget ledger holds a reserve row for that request and the replay cache has no file for it, the call was cut off: it is never re-asked. L takes a counted FALLBACK (R's pick, rank row `cut_off` true, `model_error` call_cut_off) and every arm still enters at t_e.
4. **An L call cut off by a shutdown or a crash is an L FALLBACK, not an exclusion, and counts in the 10% fallback rule.** A period resumed after t_e, or whose rebuild does not match the log, is EXCLUDED in all four arms (SCAN `period_excluded` row and an L `rank` row with `excluded` true). Exclusions have their own cap: over 10% of periods (excluded / (non-excluded rank rows + excluded)), all four arms are NOT EVALUABLE. E1's A_L < 20 rule keeps counting attempts only, so excluded periods are out of A_L. `tools/hv7_count.py` reads only the arm logs (entry and L rank rows), takes a required `--clock-end-ms`, and prints `fallback_share`, `excluded_share`, `not_evaluable` and `a_l_lt_20`.
5. A period whose rank row exists but whose entries are incomplete (a kill during entry) gets a `gap` row per missing (arm, pool) entry. No late entry.
6. The resume event goes to `<root>/resume.jsonl`, not the decision log, so a resume with no gap and no exclusion leaves the decision-log md5 equal to an uninterrupted run.

## 9. PR plan (builder chunks, each <= 40 turns, WIP commit by turn 28)

| Chunk | Branch | Scope | Tests |
|---|---|---|---|
| **C1** | `claude/hv7-rank-core` | `hv7_rank.py`, `RANK_v0.txt`, `rank.txt` | permutation validator (dup, missing, extra, wrong N, SKIP text, refusal, empty -> FALLBACK; long or field-less `why` -> accepted + `why_defect`), shuffle determinism, `r_pick` indexes the screen order (not the display order) with determinism and uniformity over 10,000 synthetic sets (chi-square), `s_pick` tie order, realised N 3..8 in `display_perm`/`r_pick`/`u_set`, `n_min` rule, HV7-local card check, byte-compare with GEN_v0 of UNTRUSTED TEXT, CARDS, HONESTY and SCREEN, NOT REASON whole, plus BASE RATE and MEASURED DIRECTIONS up to the changed sentence (BASE RATE's closing buy/skip sentences replaced, section 3), MEASURED DIRECTIONS uses the QP (c) clause, request bytes identical across two builds, no outcome field allowed in a request (guard test), price arithmetic of section 5, K1 cap-sum **arithmetic only** on constants (the enforcement test is C2/C4, section 5) |
| C2 | `claude/hv7-runner` | `hv7_runner.py`, HV7 budget ledger, fixed `t_e`, 8-position U (or U1..U8), fallback path | **hard: byte-identical entry and exit prices (all legs, both fail models) for L, R, S and U1..U8 buying one pool at one `t_e`; failure = design invalid**; 1.9 s leg lands at sd + d + k from `t_e`; StubModel drill on synthetic tape: every period with >= 3 candidates gives exactly one entry per arm at `t_e`; exits before entries; fallback on timeout; budget stop -> fallback; two runs byte-identical |
| C3 | `claude/hv7-count-register` | `hv7_count.py`, `hv7_register.py`, real-layout drill + replay script, E1 restart drill | md5 decision-equivalence: HV7 card md5s equal HV6's at the same instants on 10-21..23 (screen reuse proof); two seed-1 replays byte-identical; as-of mutation test (planted future print raises LeakageError); P0 counts |
| R | (no PR; `/data2/mal/hv7-rank-rehearsal`) | rehearsal prompts + manager-run Haiku subagents + validator | the section 7 bars, after QP OK on the card source |
| C4 | `claude/hv7-registration` | registration pack, DEC-031 amendment (sibling, budget split, multiplicity), records | QP OK on exact head + reviewer verdict |
| C5 | `claude/hv7-read` | `hv7_read.py` (gate at B with day-block bootstrap beside it; L - U and secondaries once at T0 + H with the >= 8 non-zero-day rule; fallback checks at B and T0 + H; Holm block; MDE line) | synthetic books with known answers; refuses the gate before B and the contrast before T0 + H + exit; D = 5 sign-flip min p 0.03125 asserted |

**First builder chunk (C1) brief, ready to send.** "Worktree `/data2/mal/wt/hv7-rank-core` from origin/main, branch `claude/hv7-rank-core`.
Write `tools/hv7_rank.py` (pure: no I/O, no clock, no network, no key; `forbidden_imports` test as HV6), `tools/hv7_playbooks/RANK_v0.txt` (system
text: every measured BASE RATE sentence byte-equal to GEN_v0, the closing buy/skip sentences replaced with "The book trades your first choice and pays the round trip cost before any move, so rank by what the card fields support. Judge each pool on its fields alone."
(section 3); byte-compare tests against `tools/hv6_playbooks/GEN_v0.txt` for UNTRUSTED TEXT, CARDS, HONESTY and SCREEN, NOT REASON whole and for
BASE RATE and MEASURED DIRECTIONS up to the changed sentence,
plus the QP (c) MEASURED DIRECTIONS clause and the RANKING TASK paragraph of docs/research/hv7/DESIGN.md section 3; no conviction or size
sentence), `tools/hv7_templates/rank.txt`, and `tools/test_hv7_rank.py` with the C1 tests in DESIGN section 9 (QP (d)1-4 included). Requests are built with `tools.hv4_prompts.Request` and carry one 5-min cache marker. Do not edit any hv4_/hv5_/hv6_
file. No tape, card file, drill output or job output is read. Commit after every file; push; open a draft PR; do not merge."

## 10. Biggest risks

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
