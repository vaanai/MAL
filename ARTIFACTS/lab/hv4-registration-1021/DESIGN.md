# HAIKU TRADER v4 (HV4): design

Design lead (Opus), 2026-10-18. A design for paper trading in the hunt20 arena (PLAN.md stream B), not a result. Nothing here says the trader makes money. No book has cleared the promotion gate.

## 0. Premise
- **Every universe measured so far loses before selection, at MAL costs:**
  - fresh-pool longs: −24.09% per attempt; 41.0% of entries drain, to −93.3% (hunt4/drain-escape);
  - generic entries at 0.25 SOL: −27.96% (research/rug-signals);
  - survivors aged 24 h or more: −8.27% per 24 h hold; a random-10 pick: −2.88% (hunt3/survivors);
  - cost floor: about −2.4% per round trip, or −3.52% with rent.
- **Haiku has added nothing on its own yet:**
  - v2's ranker beat R1 on August and lost to it on September;
  - v2's researcher failed its null;
  - v3's learning was about equal to a placebo (P 0.38), and its gain was a numeric dead-pool veto.
- **So every numeric filter sits in the shared scanner.** No arm gets credit for one. The LLM is measured only above the filters, against arms that share its stream. LLM proposes, code executes, and the default is to abstain.

## 1. Universe and scanner (code: `tools/hv4_cards.py`)
- **Venue:** canonical PumpSwap pools of graduated pump.fun mints, with WSOL quote. Prices use PRE-trade reserves plus V.
  - No bonding curve: A16, A5, I3, G1 and L2 are dead at MAL latency, and the arena has no curve-parity simulator.
- **Lane G** (the CON and BIG personas): established pools.
- **Lane H** (the BST persona): H5 triggers only (section 5).

**Lane G filters,** all as of the last slot before scan instant T:

| Filter | Value |
|---|---|
| Age since first PumpSwap print | 2 h to 7 d. At 2–24 h: capped at 0.25 SOL and at most half of an arm's slots. |
| Real quote | ≥ 30 SOL (subsumes v3's dead-pool veto) |
| Last 15 min | ≥ 20 prints, ≥ 8 unique signers, last print ≤ 120 s ago |
| Last 60 min | ≥ 10 SOL gross volume |
| Round-trip impact at the intended stake | ≤ 3.0%; otherwise the size drops; a breach at 0.25 SOL is a veto |

**Cadence.**
- `poll()` every 1 s; scanner tick each minute at second :02.
- A board every 15 min: the top 10 uncarded watchlist pools by 60-min volume.
- Lane H is evaluated per print.

**Setups.** At most one per pool, per persona, per 30 min. Ranked by the z-score of vol_15m within the setup type.
- **CON PULLBACK:** ret_60m ≥ +10%, ret_15m in [−15%, −3%], net_flow_60m ≥ +3 SOL.
- **BIG BREAKOUT:** close ≥ 0.98 × 6 h high, vol_15m ≥ 3 × the 6 h average, age ≥ 6 h.
- **BIG SECOND_WAVE:** 6 h low ≤ 0.4 × 6 h high, close ≥ 1.4 × 6 h low, ret_15m ≥ +10%.
- These families are dead as rules (A9, C4, C5, C7); here they only screen.

**Expected counts.** From a count-only run on explore-0814 bars, Aug 17–27 (`haiku-v4/counts/count.py`, `setups.py`):
- 156 pools pass the filters per 5-min scan;
- 46 newly passing pools per hour;
- setups per hour: PULLBACK 51, BREAKOUT 22, SECOND_WAVE 65.
- Signer counts are upper bounds.

October is quieter; the shakedown counts it (no outcomes). Call caps bound cost.

## 2. Risk agent (shared by every arm; frozen at v0 for the window)
**Computed in code** (RPC in `tools/hv4_chain.py`):
- **Mint facts** from `getAccountInfo`, cached per mint: mint and freeze authority, token program, Token-2022 TLV extensions.
- **Holders** from `getTokenLargestAccounts`, every 15 min and at card time, excluding the pool vault, the curve and burn addresses: top1 and top10 share.
- **holder_count** from the tape, with a coverage flag.
- **Creator:** net SOL over 60 min and over life; creator_sold_frac.
- **Launch structure:** same_slot_supply and sniper share in create slots +0 to +3, and sniper_still_holding share. Null if the create is off the tape.
- **Wash:** V* = the share of 15-min gross volume from round-trip wallets (both sides, |net| < 5% of their gross); also top3_signer_share_60m.
- **Flow:** net_sell_25slots; drawdown from peak quote; Mayhem flag.
- **Sell route** (paper simulation of a live check), before the buy and again at exit:
  - token program and extensions allow a transfer;
  - at least 3 distinct non-creator sellers in the last 60 min;
  - a simulated sell of the stake returns ≥ 90% of mark.
  - At exit a failure is logged as `sell_route_flag`, and the paper fill still happens.

**V* threshold, corrected.** The hunt20 rug-signals summary says "≥0.7 over 5 min". The source says ≥ 0.20 over 15 min (t-volume-campaign REPORT line 64; JUDGE-4 5a), and this design uses the source.

**Hard vetoes, in code, fail closed** (an RPC error means no card):
1. mint or freeze authority non-null;
2. Token-2022 TransferHook, TransferFee > 0, PermanentDelegate, NonTransferable, DefaultAccountState=Frozen or Pausable;
3. Mayhem with age < 24 h (an unknown flag counts as Mayhem);
4. quote below the floor, or the impact cap breached at 0.25 SOL;
5. V* ≥ 0.20;
6. the sell-route check fails;
7. real quote down ≥ 30% in 15 min.

**Not vetoes.**
- top10_frac and creator_sold_frac are **inverted** in the lab (AUC 0.237 and 0.349). The LLM sees them with the measured sign, and the prompt never says concentration is bad.
- holder_count is the strongest single drain predictor on fresh pools (AUC 0.815 at T30), but the DIRECTION is that a wider holder base went with MORE drains (drain-predictor REPORT line 25: "concentrated holder base means survival, wide base means drain"), measured on fresh pools at T6/T30, not on this universe. It is a neutral field for the LLM. [corrected by manager12 2026-10-18]

**Card.** JSON, about 700 tokens, every field stamped with `as_of_slot` and `as_of_ms`:
- the section 1 fields;
- returns over 1/5/15/60/240 min, dd_ath, rebound;
- volume and net flow over 5/15/60 min; buy and sell counts; unique buyers and sellers; new-wallet buy share;
- the top net buyer and seller;
- the risk facts;
- impact at 0.25, 0.5 and 1.0 SOL;
- 20 one-minute and 12 five-minute candles;
- the setup tag;
- name and symbol, sanitized to `[A-Za-z0-9 ]{0,24}` and marked untrusted;
- `x: null`.

**Risk call.** Made on watchlist entry, every 30 min, and at once on a material change (creator sells ≥ 1 SOL; top10 moves ≥ 5 points; quote −20% in 5 min).
- Output: `{"id","grade":"pass|caution|reject","flags":[CREATOR_SELLING|SNIPER_OVERHANG|WASH_SUSPECT|THIN_EXIT|DRAIN_RISK|FLOW_FADING|COPYCAT|UNKNOWN_COVERAGE|OTHER],"max_size":0.25|0.5|1.0,"why":"<=30 words"}`.
- A reject hides the pool from traders for 60 min. A caution caps the stake at 0.25. An invalid reply holds the pool until the next check.
- The X arms ignore this agent. Its marginal value is measured by a report-only replay, "X+RISK".

## 3. Trader agent
**Per-card output** (SKIP is the default):
`{"id","action":"BUY|WATCH|SKIP","size":0.25|0.5|1.0,"conviction":1-5,"plan":{"target_pct","stop_pct","time_min","trail_pct"},"watch":{"when":"<predicate>","ttl_min":<=60},"why":"<=25 words naming >=2 card fields"}`

- **WATCH** lets the model pick its own entry time: when the predicate fires, code re-cards the pool once. An arm holds at most 10 watches.
- **Predicates:** `field op number`, joined by AND, at most 4 terms, over numeric card fields. Parsed by code, never by `eval`.
- **Code gates on a BUY:**
  - the id must be one sent in this request;
  - conviction ≥ `min_conviction`;
  - the plan must lie within bounds; out of bounds means SKIP, never clipping;
  - size = min(trader, risk max_size, impact cap, age cap);
  - slot, rate and loss-stop limits;
  - a 30-min cooldown per pool.

| Persona | Knobs (bounds, v0) | Code limits (fixed) |
|---|---|---|
| CON | stop −4..−10% (−7); target +6..+30% (+12); time 10–120 min (45); min_conviction 3–5 (4) | hard stop −15%; max hold 120 min; 4 open; 4 entries/h; day loss stop −1.0 SOL |
| BIG | stop −12..−30% (−25); target +40..+400% (+100); trail 20–40% after +50% (30); time 30–720 min (360); min_conviction 3–5 (4) | hard stop −40%; max hold 12 h; 6 open; 3 entries/h; day loss stop −1.5 SOL |
| BST | min_conviction 2–5 (3); stake 0.25 | exit at s0+330 s only (exit ladders lost to the timer); 3 open; day loss stop −0.75 SOL |

**Open positions.**
- Code checks stop, target, trail and time on every print. The mark is the sell quote at the stake.
- The LLM re-checks every 5 min (CON) or 15 min (BIG). It also re-checks on an event: the mark has moved half the distance to the stop or target, or a risk flag has changed.
- Re-check output: `{"id","action":"HOLD|EXIT|TIGHTEN","new_stop_pct","new_target_pct","why"}`. Stops only tighten.
- The day loss stop counts realized plus marked P&L. Once hit, entries stop until 00:00Z.

**Timestamps feed the fill.**
- `decision_ms` is when the validated reply is in hand; `decision_slot` is the arena's latest slot at that moment. `engine.buy/sell(...)` fills at +1.3 s and +1.9 s, so LLM latency is charged.
- For a code-triggered exit, decision_ms is the detection instant.
- A reply more than 10 s after `as_of_ms` is discarded as stale.

**Decision log.** Per arm and day, hash-chained JSONL at `/data/mal/arena/haiku-v4/decisions/`. Each row holds:
- the card and its sha256;
- request and response sha256;
- model;
- template, playbook and manifest sha256;
- the ledger reservation id and latency;
- every gate check with its value;
- the order id.

No tool loop at trade time: rug checks, lab research and the H5 signal are precomputed into the card or playbook.

## 4. Daily P&L and nightly reviewer
- **Daily P&L** comes from arena_score, per arm, stake and fail leg: n, total, ex-top-3, ex-best-day, positive days, CI90 lo.
  - It goes to a MiScusi notebook entry and to console.json.
  - Viewing is safe because arms are frozen; an arm added or edited after a look is a counted trial.
- **Reviewer:** a host-side MiScusi job at 00:05Z, one per learning arm (CON-L, BIG-L, BST-L, CON-P).
- **Input bundle,** frozen by code at 00:01Z:
  - the arm's closed trades with exit before 00:00Z and not shown before: entry card, decision, exit reason, hold, and net % at 0.25 on the flat leg only (pressure is never shown);
  - open positions as a count only;
  - up to 60 skipped cards from day n, each with the v0 default plan's outcome replayed in arena_paper, if that exit finished before 00:00Z;
  - the current playbook and knobs;
  - a 7-day change log with each change's measured effect.
  - Nothing from any other arm.
- **CON-P** gets the same bundle with the (net, exit reason, hold) tuples shuffled, seeded by sha256(arm|date).
- **Output:** at most 3 changes, each `{"type":"playbook_edit|knob|local_veto","target","new","predicate","evidence_ids","why"}`.
- **The harness accepts a change only if:**
  - knobs stay within bounds;
  - its predicate matches at least 10 trades or skips in days ≤ n, with an effect of the claimed sign;
  - the arm keeps at most 8 local vetoes, and they only tighten;
  - the rules text stays at or under 1,200 words (2× v0).
- **The reviewer may not change:** the scanner, shared vetoes, risk agent, code limits, fills, costs, scorer, gate, stakes, model, its own prompt, the embargo, the controls, or the playbook's frozen header.
- **Versions.** v(n+1) is in force from 00:30Z of day n+1 for 24 h. Its manifest is appended to `/data/mal/arena/haiku-v4/manifests/<arm>.jsonl` and holds:
  - the sha256 of the playbook, knobs, templates, bundle and response;
  - commit and model;
  - max_input_close_ms;
  - prev_manifest_sha256.
- **The runner refuses a manifest** if max_input_close_ms is at or after 00:00Z of its day, or if the chain is broken. Each night's hashes go into a notebook entry.

## 5. Personas and controls (13 bots, one stream)
- **CON:** liquid pullbacks with steady inflow; small targets.
- **BIG:** asymmetric runners on established pools; 0.25 SOL by default; trailing exits.
- **BST:** on lane H, `tools/arena_h5_signal.py` runs the decision half of h5_shadow on the arena tape and emits trigger facts only. BST never reads stream A's out-dir. Its cards add BOOST spent so far, slices, Q and launch rug facts. BST takes or skips; code fixes the exit.
- **Playbook v0:** at most 600 words per persona, priors only (section 0, the dead families, the measured feature signs). Cached as system text.
- **Grok X features** arrive by file drop at `/data/mal/arena/x-features/`. They are features and flags only and never create a card. They enter at a new window's registration only.

| Arm | Definition | Promotable |
|---|---|---|
| CON-L, BIG-L, BST-L | LLM plus nightly reviewer | yes |
| CON-F, BIG-F, BST-F | the same, with playbook v0 frozen | yes |
| CON-P | CON-L with a shuffled-outcome reviewer | no |
| CON-X, BIG-X | the same setups and vetoes; top-ranked setups at the entry cap; v0 default exit; no LLM | yes (rule book) |
| BST-X | H5 v1 plus the same vetoes; exit 330 s; 0.25 | yes |
| CON-R, BIG-R, BST-R | random, matched to the -L arm | no |

**Matching.**
- L, F and P of a persona get byte-identical cards at the same instant. Only the prefix and the deterministic nonce sha256(arm|kind|card_sha|seq) differ. A restart replays from cache, and arms never share a cached reply (v2's defect).
- On day 1, L and F have the same playbook, so L−F that day is the noise floor.
- **R arms:** when L opens a position, R opens a random risk-cleared watchlist pool at the same instant and size, and closes it when L closes. BST-R takes triggers at BST-L's take rate to date.
- R runs live with seed 1; a 200-seed null is replayed after the window.

## 6. Budget (`claude-haiku-5-5` only)
- **Settings:** thinking disabled; no temperature (non-default values return a 400); max_tokens 400 (entry and risk), 120 (re-check), 4,000 (reviewer).
- **Caching:** one 5-min `cache_control` marker on the system block (header plus playbook). Card and nonce follow it.
- **Prices per MTok:** input $0.10, cache read $0.01, cache write $0.125, output $0.50.
- A refusal or a parse failure counts as abstain.

| Call | Tokens: cached / new / out (assumed; measured in shakedown) | USD | Cap | USD/day at cap |
|---|---|---|---|---|
| Entry (≤ 4 cards) | 2,500 / 3,200 / 300 | 0.000495 | 30/h × 7 LLM arms | 2.49 |
| Re-check | 2,500 / 900 / 60 | 0.000145 | CON 60/h × 3; BIG 30/h × 2 | 0.84 |
| Risk | 2,000 / 1,500 / 150 | 0.000245 | 150/h | 0.88 |
| Reviewer | 0 / 50,000 / 3,000 | 0.0065 | 4 per night | 0.03 |

- **Totals:** $4.24/day at cap, $127/month; about $2/day expected.
- **Hard caps,** through `tools/haiku_caller.py`:
  - one ledger per arm per UTC day;
  - $0.50 per LLM arm, $1.00 for risk, $0.10 for the reviewer: $4.70/day;
  - a month guard refuses calls above $160;
  - the owner's $200 console limit is the backstop.
- **Drop order.** It starts when a persona group passes 60% of its day budget before 12:00Z, or 80% at any time:
  1. no board, at most 2 cards per call;
  2. risk refresh only on a material change;
  3. re-check cadence ×3;
  4. the whole group halts entries, with code-only exits.
  - Steps always hit L, F and P together. The reviewer is never dropped.
- **Helius:** at most about 40k standard calls a day at 2 rps. Credits are reported per job.

## 7. Failure modes
| Failure | Caught by |
|---|---|
| Feed gap | gap records pause cards; code holds positions; flagged trades are kept and counted; above 20% flagged, the read is NOT_DECIDABLE |
| Hallucinated fields | ids must be ones sent; strict enums and bounds; code supplies every executed number; invalid output means abstain; above 10% invalid over the window, the arm is NOT EVALUABLE |
| Injection (names, X text) | sanitizer; code limits; a test with injection strings |
| Overtrading | SKIP default; min_conviction; caps; cooldown; loss stop; the reviewer cannot raise caps; take rate above 40% is flagged |
| Learning noise | frozen and placebo twins; predicates with ≥ 10 matches; at most 3 changes a night; size cap; the day-1 noise floor |
| Look-ahead | as-of cut tested by mutation (a planted future print raises LeakageError); the embargo; perturbation invariance (scramble outcomes after X, and every earlier bundle stays byte-identical); no pressure leg or cross-arm data |
| Isolation | safe_path denies forward-*, truth-*, fresh-08*, exp011-*, tip-tape-archive, /var/lib/mal/sealed, shadow/canary/probe outputs, meta-capture, /data/mal/blocks/forward-1016; an import guard blocks laya-opt and l3; overlap with r4-b is disclosed at EXP-027's read |

## 8. Gate read
- **Window.** T0 = 2026-10-21T00:00Z at the earliest. Before it: the registration commit (code plus v0 manifests), the DEC-031 ruling by quant-proof, and a 24 h count-only shakedown. The window runs 14 days.
- **Looks** at 13:00Z on T0+5, +7, +10 and +14 d. A look counts entries before 00:00Z of its day.
- **Cells,** at the 1.9 s fill, under both the flat 15% and the pressure fail model:
  - flat 0.25/0.5/1.0 for CON-L/F/X and BIG-L/F/X (18 cells);
  - flat 0.25 for BST-L/F/X (3);
  - as-sized for CON-L/F and BIG-L/F (4);
  - **25 cells × 4 looks = 100 checks,** disclosed with every other arena bot.
  - Flat 0.25 is each book's primary.
- **Promotion:** the CLAUDE.md gate unchanged, plus ex-best-day > 0 and a quant-proof pass. Then the owner's OK, then a small canary.
- **Power.** CON reaches 100 trades by about day 5–8; BIG may not (NOT_DECIDABLE).
- **"LLM adds value"** for a book means both, on both legs:
  - **V1:** primary ex-top-3 above the p95 of the R arm's 200-seed null;
  - **V2:** daily (book − X) at 0.25 with a one-sided day-block bootstrap (2,000 draws, seed 1) P ≤ 0.05/6.
  - If V2 fails and the X book also passes the gate, X goes to the canary first.
- **"Learning works"** is read at D14 only. For CON it needs:
  - L − F with P ≤ 0.025, and L − P with P ≤ 0.025, on both legs;
  - and the mean L − F over D8–D14 at least the mean over D1–D7.
  - BIG and BST L − F are supporting evidence only.
  - If learning is not shown, the reviewer stays off next window.
  - NOT_DECIDABLE is the expected outcome.
- **No changes mid-window** except safety stops.

**Amendment A1 (2026-10-19, before T0, no arena outcome seen; quant-proof ruling on PR #718).** This amendment replaces the test named in the "LLM adds value" (V2) and "Learning works" bullets above. Nothing else in this section changes, and the promotion gate does not change.
1. V2 and every learning comparison (CON L − F, CON L − P, and the supporting BIG and BST L − F) use an exact one-sided sign-flip test. Each one compares the two arms' daily differences in SOL at stake 0.25, one per calendar UTC day of the window. A day with no trade in either arm counts as 0.
2. The statistic is the sum of the daily differences. p is the share of the 2^k sign patterns of the k non-zero days whose sum is at least the observed sum, and ties count against the book. Above 20 non-zero days, p = (1 + hits) / (1 + 100,000) over 100,000 random patterns with seed 1.
3. The thresholds do not change. V2 needs p ≤ 0.05/6, and each learning comparison needs p ≤ 0.025, in each of the four deciding sub-cells (1.9 s; flat 15% and pressure scale 1; strand rules total and sc). Learning also keeps its week-2 ≥ week-1 condition.
4. This test cannot give a p below 2^-n on n days. V2 therefore needs a window of at least 7 UTC days, and a learning comparison needs at least 6. A shorter window is NOT_DECIDABLE by rule, whatever the numbers.
5. This test replaces the day-block bootstrap (2,000 draws, seed 1). In simulation (PR #718), that bootstrap rejected a true null 4.3 to 7.9 times as often as 0.05/6 on windows of 5 to 8 days. The sign-flip test ran at 0.66 to 1.05 times its nominal level for symmetric daily differences on 7, 8 and 14 days.
6. The test assumes that, with no effect, the daily difference is symmetric. If the difference is frequent small gains with a rare large loss, a 7 to 14 day window may hold no loss day, and the test can pass when the true mean is 0. With the loss on 10% of days, the simulated false pass rate was 0.48 at 7 days and 0.23 at 14. V1, V2 and learning are comparisons and never a profit claim, and stake above a canary still needs DEC-031's confirmation.
7. V1 and V2 for each L and F book are decided once, at the book's outcome-blind gate instant B plus 24 h. They are recorded write-once in the arena store as `<bot_id>.v12.json`, and a second decision is refused.
   - If the comparator X has no outcome-blind instant by B + 24 h, V2 is NOT_DECIDABLE.
   - The 200-seed R null is replayed through B at the registered frozen sha, as a MiScusi job whose id is recorded. Its MANIFEST.json pins every seed file's sha256, and seed 1 must reproduce the live R arm.
   - Without a manifest that verifies, V1 is NOT_DECIDABLE.
   - The V1/V2 step runs at B + 24 h, or when the null job finishes if that is later. Its answer does not depend on that time.
   - V1 is recorded NOT_DECIDABLE without a null only when the null job failed, and the record cites that job.
   - This supersedes "a 200-seed null is replayed after the window" in section 5.
8. DEC-031 section 2 governs the gate: one gate read per bot_id, at its outcome-blind instant. The day-5/7/10/14 looks above are superseded for gate purposes, so the gate multiplicity is 25 cells × 1 read = 25 checks, disclosed with every other arena book.
Process conditions (QP ruling (a)): the `v12` step runs as a host-side MiScusi job at B + 24 h, chained `after` the R-null replay job if that finishes later, never driven by a session cron alone; `--no-null` only when the null job failed or its manifest does not verify, citing that job id, never after anyone has looked at the null's scores; the null replay runs at the registered frozen sha and its job id goes into the notebook and records next to the manifest sha.

**Note N1 (2026-10-19, before registration, no arena outcome seen; quant-proof ruling on PR #739).** Section 2's holders "every 15 min and at card time" and mint facts "cached per mint" are implemented as the newest read stamped strictly before the scan instant T and at a slot not after the arena's latest slot (a read made at card time is stamped after T and is refused by the as-of cut): a background refresher re-reads mint facts every 10 min for every filter-passing pool, re-reads holders every 4 min only for pools that are tagged, on the board or in the top 25 by 60-min volume (for 15 min after last seen), reads the Mayhem flag once per pool, and spends at most 1,500 chain calls in any rolling hour; a card whose newest mint read is older than 15 min is refused as chain_stale, a holders read older than 5 min is left out (null holders block, not a veto), and a pool with no mint read before T is refused as chain_unavailable. These rules are frozen with the registered sha for the window, and the shakedown and the gate read report the chain_unavailable, chain_stale and null-holders-block counts per persona.

**Note N2 (2026-10-19, before registration, no arena outcome seen; manager12, taking the lane-H option quant-proof gave on PR #739; quant-proof to confirm at the registration records PR).** Lane H is not run in window 1. The research-0 runner has no as-of-strict, classified H5 trigger source (`tools/arena_h5_signal.H5Signal` emits no trigger without a classifier, EXP-024 Am.4, and the classified feed is on mal-fast-0), so `main()` runs with lane H off and registers only the 9 lane-G arms (CON-L, CON-F, CON-P, CON-X, CON-R, BIG-L, BIG-F, BIG-X, BIG-R); BST-L, BST-F, BST-X and BST-R are not registered and get no read. The cells are 22 (flat 0.25/0.5/1.0 for CON-L/F/X and BIG-L/F/X = 18, plus as-sized for CON-L/F and BIG-L/F = 4), and Amendment A1 item 8's gate multiplicity is 22 checks. BST may enter a later window only as new bot_ids with their own registration. Before the first read, the read tool's 25-cell expectation must be changed to 22 in a reviewed PR. Nothing else in sections 1-8 changes.

## 9. Build plan (Sonnet builders, WIP pushes)
1. **`claude/hv4-cards`** (start now):
   - `filters`, `setups`, `code_vetoes`, `build_card`, `Predicate`, `sanitize_name`, over a `SnapshotLike` protocol that mirrors `ArenaState.snapshot`;
   - tests: every threshold at its boundary, the as-of mutation, the grammar rejecting code, injection strings, V*.
2. **`claude/hv4-chain`** (now):
   - `mint_facts` (Token-2022 TLV parse, authorities), `largest_holders` (vault exclusion), the Mayhem flag, cache, token bucket, credit counter;
   - tests on recorded account bytes for each extension type; fail-closed behaviour.
3. **`claude/hv4-agents`** (now):
   - playbooks v0, templates, request builder (cache marker, nonce), validators, ledgers plus the month guard, group degrade and halt, StubModel;
   - tests: hallucinated ids, bounds, nonce separation, symmetric halts, refusal leading to abstain.
4. **`claude/hv4-runner`** (after arena-feed and arena-paper merge):
   - the loop, 13 arms, code exits, R matching, the log chain, `arena_h5_signal.py`, a resumable MiScusi job;
   - tests: deterministic md5 of the decision log on a recorded tape with the stub; trigger equivalence against h5_shadow; the latency-to-fill path; caps.
5. **`claude/hv4-review`** (after 4):
   - bundle builder, skip pricing, placebo, change validator, manifest chain, scheduled job;
   - tests: a trade closing at exactly 00:00:00.000 is excluded; perturbation invariance; the placebo keeps the marginals; chain refusal.
6. **`claude/hv4-read`** (after arena_score merges):
   - the R null, V1/V2/learning, the 25-cell table, disclosure, result.v1, Console rows;
   - tests: synthetic books with known answers; seed reproducibility; the cell count equals 25.

**Then the manager registers** (pins, v0 manifests, quant-proof ruling), runs the shakedown, and starts T0.
