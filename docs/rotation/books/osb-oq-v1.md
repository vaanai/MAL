# Rotation lane method card: `osb-oq-v1`

Method card under [DEC-036](../../../DEC/DEC-036-rotation-lane.md) §3, from [the template](../METHOD-CARD-TEMPLATE.md), plus this book's registration: manifest (§10), read rule (§11), seal lines (§12) and day-1 dry check (§13). It registers a **paper open simulation book** (DEC-035 Am.9 §1). It does not load a rotation book, start a live instance or write a belief record.

**No book has cleared the promotion gate. Nothing on this card says osb-oq-v1, FLOOR or any book makes money.** Every count quoted here is in-sample (a screen); no outcome of any other-quote trade has been computed by anyone before this book's start instant S.

| Field | Value |
| --- | --- |
| bot_id | `osb-oq-v1` (paper book id; rule id `l1-floor-oq-v1`). A later live book gets its own rotation `bot_id`, never `floor-lane-1`, `h5-lane`, `l1-floor-v2` or an `hv4-` id. |
| Card version / date | v1, 2026-11-18 (`date -u` 20:16:22Z at drafting), at main eae236ec |
| Library row | `floor-sibling` Card 2 (S6, custom-pair FLOOR-style): host file `/data/mal/hunt-1008/ops/reports/2026-11-18/floor-siblings-design.md` §2 row S6 and §4 Card 2. Older neighbours: boost-custom-v2-1, boost-universe-2 (other-quote universes, different rule). |
| Card author | worker draft for manager24; the manager owns it |
| Sources | QP-RULING-1118-other-quote-counts (host `/data/mal/hunt-1008/ops/`, sha256 `b2107e999241b6c4bd71a7318adbd47a28e94c83b26d141c1c7e9dcd7d807f47`); counts job #3048 (MiScusi notebook "Other-quote FLOOR-style counts"); PR #1456 QP VERDICT d31815db OK and REVIEWER VERDICT d31815db APPROVE, and the QP verdicts at bd723e9d and 85ae4c33 on the same PR (declared gaps, D2/D3 prerequisites); QP-RULING-1118-u2-v2 (sha256 `d79c319db4d304e37160a6d7f4c6e8cf3236dcd7069f82908941001198876f30`) §5 (interim wording); DEC-035 Am.9 and Errata 1-4. |

## 1. Mechanism
1. **Who loses, and why.** The rule is FLOOR's dip-buy (l1-floor-v2 logic) moved to PumpSwap graduates whose quote mint is not WSOL (USDC, pump-class tokens, other tokens): after a SELL print in a 24-200 h old pool whose quote vault is drained to <= 0.5 SOL-equivalent, whose spot fell more than 20% against 6 h ago and which is still alive (previous print <= 6 h, 24 h volume >= 1 SOL-equivalent), buy; take profit at +50% on the post-buy mark, else sell at 24 h; no stop. Sellers who dump a drained, quiet pool near its low lose to a buyer who holds through the rebound that new buyers bring. Holders keep doing it because these pools are thin and attention-driven: a small seller moves the spot a lot, and the sellers are mostly the creator's or early holders' exits. Other-quote pools add a second asset: the position is bought through a conversion leg (SOL → quote → token, and back), so the quote asset's own moves and fees are part of the result.
2. **What would stop it.** (a) A PumpSwap program or fee change (DEC-035 §4 redeploy); (b) a fee schedule on these pools that differs from the table's `pfee` (QP-RULING-1118-other-quote-counts notes 28 of 30 sampled other-quote graduations are synthetic completions with InitBoost; boosted fees possible); (c) the cohort ending: the 11-13 sample had 19 of 30 other-quote graduations from one creator in a 7 min burst; the counts show 11-11..11-13 USDC-only and pump-class pools from 11-14; (d) a quote collapse or depeg (pump-class quotes are memecoins). How we see it: daily trigger count from the driver and engine counters (status.json), `fee_audit` counts, `entry_refused_*` (no quote state, no pool fee), and the drop-top-creator / drop-top-quote-mint lines at each read.
3. **FLOOR twin or sibling?** A sibling, not a twin: same decision logic, disjoint universe. The OQ host admits only pools with a known, non-WSOL quote mint (`tools/arena_oq_engine.oq_in_universe`); FLOOR trades only WSOL pools with V in [17.5, 17.7] SOL. **Pool overlap with FLOOR: zero by construction.** Minute overlap (same market hours, correlated dumps) is likely and is not measured; creators may overlap and are not measured.

## 2. Which sim it needs
- [x] **Open simulation book on arriving tape** (DEC-035 Am.9 §1), from the recorded start instant S. Why: the mechanism has no live fills and no priced history in this universe, the trigger rate is low (median 8 pools/day, an upper bound), and the conversion leg needs quote states as they arrive; a replay on November hours would be in-sample (FLOOR research and the counts job already used them). The forward book is the believable test (design Card 2).
- [ ] Replay on closed hours: not used (in-sample; no held-out other-quote hours exist).
- [ ] Twin of FLOOR: n/a, because FLOOR has no other-quote decisions to twin.
- [x] Other: pricing twins of this book itself: gap-in-hold twin `osb-oq-v1-gh` beside the deciding strict book, and latency twins `osb-oq-v1-lat3010` / `osb-oq-v1-lat5590` (§10).

Start instant, tape source, seal line, simulator version:
- **S** = `start_hour` of the manifest, set at start: the next full UTC hour after this PR merges, the §13 dry check has passed and the keeper shows `keep/` contiguous over [S − 201 h, S). Recorded in the start notebook entry and the DEC-035 Am.9 record (§14).
- **Tape**: closed hv7 hours hard-linked by the osb keeper under `/data2/mal/scratch/floor-open-sim/keep/` (SEALS-BRIEF line (k) keeper), read only by the driver.
- **Simulator**: `scripts/research/osb_follow_oq.py` (front end of `scripts/research/osb_follow.py`) with `tools/arena_rule_l1_oq.py`, `tools/arena_oq_engine.py`, read by `tools/arena_oq_score.py`, at main **eae236ec** (merge of PR #1456). Blob shas at eae236ec: `osb_follow_oq.py` 4ebaaa68, `arena_rule_l1_oq.py` 1055faab, `arena_oq_engine.py` e258136d, `arena_oq_score.py` 6f041ef8, `osb_follow.py` 3340a534. Any other commit is a new simulator version and a new book.
- **Seal line**: a new SEALS-BRIEF line, proposed in §12; the manager adds it before the first run (workers do not edit SEALS-BRIEF).

## 3. What makes this sim believable
1. **Fills and latency.** Deciding cell: entry priced at the first print at or after decision + **4.085 s** (`LAT_PRICING_DECIDING_S`; DEC-035 Am.9 Erratum 2 latency set 3.010 / 4.085 / 5.590 s). FLOOR's live landing p50 is about 3.1 s (design report §4; DEC-035 Am.9 Erratum 2), so 4.085 s is slower than FLOOR's live median; that margin is the haircut for the live-minus-sim bias. The conversion leg is read at the same landing print (its own earlier landing is not modelled, declared gap 3). Exit at the first print at or after the exit decision + 0.55 s (`EXIT_LAG_MS`). The FLOOR control book's reconciliation status at drafting is "SIM OFF: not decision-grade" (LAB_STATE Now: recon through 2026-11-15T15:00Z, h3 FAIL, s1-s4 INSUFFICIENT); the manager states the status at S in the start record. Live fills have recently been better than the sim; no credit is taken for that.
2. **Costs.** `send_lamports` **505,000** per send, 4 sends per trip (quote buy, token buy, token sell, quote sell) = 2,020,000 lamports per trip, 2.02% of a 0.10 stake. 505,000 is the WSOL deciding stack's send-plus-priority figure (`SEND_FEE_DECIDING`), above FLOOR's live 160,000; QP on PR #1456 (bd723e9d) requires send >= 505,000 for parity, met. Pool fee: each pool's own fee from the table (`pfee`), on the token legs and, at the quote's own WSOL pool fee, on the conversion legs. Rent: report-only (`rent_report_lamports`, 2,039,280 per ATA, two ATAs), as RENT_REPORT in the WSOL stack; the deciding stack treats it as refunded. Price impact at the book's stake from V-priced reserves (below), at 0.10 and 0.25.
3. **PumpSwap pricing.** Every price is from the print's PRE-trade reserves with V included in Q, in the pool's quote units, converted through the quote's WSOL pool state at that print (`arena_oq_engine` header). Nothing on this card was priced before the 2026-10-04 V correction.
4. **Per-token fee check.** Per pool (`pfee` from the table), not one flat rate. Not checked against landed fills: there are no live fills in this universe. `buyback_fee` is not on the stored tape row and is unmodelled; a missing `creator_fee` counts as 0; both are counted in `fee_audit` and reported at every read (declared gaps 5, 6).
5. **Fail legs.** Both, by `tools/arena_oq_score.fail_legs`, which calls `arena_stack.fail_legs` unchanged (QP d31815db): flat 15% `(1-f)P - f·send` and pressure slope scale 1 with the intercept refit on this set. **The flat leg is the gate leg** (QP 85ae4c33: a book positive under pressure but not under flat FAILS; never quote a pressure-only positive).
6. **Trial count.** On other-quote outcomes: 0 cells priced before S. The rule constants are FLOOR's l1-floor-v2 constants (picked on WSOL data 2026-11-03..11-11, itself after a variant search: winner's curse carries over). The OQ-specific constants (V scaled band, vault and volume in SOL-equivalent, quote staleness 10 min USDC / 2 min other, exit wait cap 1 h, stale-exit haircut 0.5) were fixed in code (PRs #1450, #1455, #1456) before any outcome. Card 2 was ranked 2nd of 9 siblings on in-sample counts. This book watches 8 rows (2 stakes × 4 cells). Shrink: every figure is "best of <n_books_watched> rows; treat as shrunk", and the shrunk mean of §4 below.
7. **Concentration.** Every read shows: total after dropping the top 3 trades, per-UTC-day totals (day of the entry decision), total after dropping the top creator (trades with no creator are one group "unknown", counted) and after dropping the top quote mint, under both legs.
8. **Basis.** All scorer figures are net of sends, pool fees and conversion legs (the engine's P), then the fail blend. `filled_no_fail` is shown beside and labelled as having no fail blend.

**Declared gaps (on every read and every figure from this book).**
1. Priority fee beyond `send_lamports` is not modelled; 505,000 per send carries priority as the WSOL deciding stack does.
2. A fee-tier change between the quote leg and the token leg is not modelled (second order; the landing-row fee is used).
3. The conversion leg's own earlier landing is not modelled: both legs read the same landing print.
4. **G1:** `_stale_exit` (an exit capped after 1 h without a usable quote state) and `_score_unfilled` (an entry that never landed in 24 h) apply no gap test in either gap mode and never set `gap_in_hold`. In the strict book a capped stale exit whose hold spans a gap is still scored, and the strict share misses it. The read reports `stale_exit_capped*` and `entry_unfilled_*` counts next to the strict share.
5. `buyback_fee` is not on the tape row: unmodelled; `fee_audit.buyback_fee_missing` is reported.
6. A missing `creator_fee` counts as 0; `fee_audit.creator_fee_missing` is reported.
7. **Static vs live quote prices.** The counts screen (job #3048) converted quote units with a static quote price file (sha 2678b75c); this book converts with the quote's own WSOL pool state at the print (post-trade approximation 1.25%, `quote_post_fallback` counted). Trigger counts in the book can therefore differ from the screen's.
8. **Undercount from unpriced quote mints.** A pool whose quote has no resolvable WSOL pool state is dropped at the rule's `quote_price_known` stage (no decision, so not in the excluded share), and a landing without a quote state is refused (`refused` row, counted in the board share). The book sees only quote mints with a live WSOL pool.
9. The stranded-quote leg (token buy fails after the quote buy lands) is not inside the read; the scorer's `sensitivity_stranded_quote_leg` gives it at 0 (= the read) and at full loss (upper bound), both legs (QP D3).
10. The unfilled round trip prices both legs at the decision-time quote state; quote drift over 24 h is not modelled. The pressure inputs of the conversion legs are the token pool's.
11. Latency twins share ENTRY decisions only: a take-profit decided before a twin's entry lands is refused (SELL_EARLY) and retried, so twin P&L differences include exit-timing changes, not pricing alone; no decision-md5 equality across twins is claimed.
12. **Code pin coverage.** `osb_follow`'s `code.sha256` covers osb_follow.py, its helpers and floor_lane_host pins, NOT the OQ files (`osb_follow_oq.py`, `arena_rule_l1_oq.py`, `arena_oq_engine.py`). Compensation: the book runs only from the clean detached worktree at eae236ec, and every start first checks the sha256 of those three files against §10's list (refuse on a difference); the per-hour `ledger_md5` restart check still catches any pricing change in replayed hours.
13. The keeper's tape had 13 broken and 5 missing hours around 2026-11-08 and before (QP-RULING-1118-other-quote-counts); with S − 201 h after 2026-11-09 they are outside the seed, but a pool whose history runs through them can have broken `prev_le_6h` / `vol24` continuity in the first days.
14. No live control book exists for other-quote pools: under DEC-035 Am.9 §3 every figure is "NOT COMPARABLE: not decision-grade" for any live step.

## 4. Believe-the-sim criteria for this book
The deciding read is §11's look (L1, or L2 when L1 has too few trades). All of these must hold on the **strict book `osb-oq-v1` at 0.10 SOL**, entries with decision in [S, L):

- **Shrunk net sim mean per trade** under the flat leg and the pressure leg, and **μ0 at 0.10** (pressure leg, winsorized at the sim's own 2.5th / 97.5th percentiles of x, net at 0.10, at the deciding cell): all three > 0 (DEC-036 §4 item 2). Shrinkage, declared now: m = (n · mean(x) + n0 · μp) / (n + n0), x = SOL per trade / stake, n0 = 25, μp = −c̄ with c̄ = max(0.05525, measured mean round-trip cost per trade / stake). 0.05525 is arithmetic, not a measurement: DEC-036 §8 c at 0.10 (3.825%) with the send part 0.32% replaced by this book's 4 × 505,000 = 2.02%; it leaves out the conversion legs' pool fees, so the measured cost is used when larger (needs read prerequisite P2, §11).
- **Deciding latency cell:** 4.085 s (the strict book's default; the twins are reported beside it, never instead of it).
- **Criteria:**
  1. Minimum evidence: **>= 7 complete UTC days AND >= 40 closed trades** at 0.10 in the window. At <= 8 trigger pools/day (an upper bound from the screen) with 24 h holds and one position per pool, 100 trades take at least 2 weeks; 40 is the floor below which no belief is written, not a gate count.
  2. Flat leg: total > 0 and total ex-top-3 > 0. Pressure leg: total > 0 and total ex-top-3 > 0.
  3. Flat leg: total after dropping the top creator > 0 and total after dropping the top quote mint > 0 (the 11-13 one-creator burst; class concentration in the counts). Pressure-leg values reported.
  4. Flat leg: a majority of the UTC days with trades are positive.
  5. Exclusions: the board-style share ((excluded + refused) / requested) <= 5% AND the strict any-gap-in-hold share <= 5%; either above 5% → **NOT_DECIDABLE** (not BELIEVE, not RETIRE). G1 counts are reported next to the strict share; if `stale_exit_capped` + `entry_unfilled` together exceed 5% of positions, the read is also NOT_DECIDABLE (they bypass the gap test).
  6. Reported, not criteria: the stranded-quote full-loss sensitivity (the belief record must say whether the flat total survives it), the gap-in-hold twin, the 3.010 / 5.590 twins, the 0.25 cell, `fee_audit`, `pending`, and the CLAUDE.md gate items under both legs (n >= 100, >= 5 days with a majority positive, CI lower bound of mean SOL/trade > 0 at 1,000 draws seed 1 5th percentile, ex-top-3 > 0), labelled "gate items reported; an open simulation is not a gate read (DEC-035 Am.9 §1)".
- **Why these fit this mechanism:** the edge, if any, comes from rare take-profits on thin pools, so concentration lines (top 3, creator, quote mint) matter more than the mean; the universe is new and small, so a trade floor (40) and a day floor (7) guard against one burst; the flat leg decides because the pressure model can turn correlated losers into a positive (QP 85ae4c33); the conversion leg makes costs larger than FLOOR's, so the prior cost is this book's.
- **What a pass licenses:** only that the manager may draft a DEC-036 belief record (T1, QP OK and REVIEWER APPROVE at the exact head), after the own-code path of §7 exists. It is not a gate pass, a live step or a stake step.

## 5. Negative but plausible: what "keep running longer" means
1. **Plausible when** the deciding read is not NOT_DECIDABLE, no §1.2 stop has fired, and either (a) the flat-leg total before conversion-leg costs is > 0 at 0.10 (the quote legs explain the loss: design Card 2's reason; needs P2), or (b) n < 40 at L2 but at least 14 trades (>= 1/day): too few trades seen for a rare-win mechanism.
2. **How much longer:** one extension, fixed now: **L3 = S + 28 days** (entries in [S, S + 28 d), same book, same manifest, no restart on another commit), read at L3 + 27 h by the same rule. A second extension only by a new card version that says why.
3. **Ends early:** a §1.2 mechanism stop (program or fee change, quote depeg, cohort gone: < 1 trigger pool/day over 7 consecutive complete days by the driver's counts); a cost found higher than modelled (e.g. a fee field the table misses, ruled by QP); a seal problem; a simulator defect (a fix is a new book with a new S). Interim figures never end it.
4. Parameters stay fixed during the extension. A change is a new book with a new start instant (DEC-035 Am.9 §2 item 1).

## 6. Live kill and retire rule
**Paper book retire rule** (applies now): at the deciding read the book retires to the library (process stopped; rows kept) when it is neither BELIEVE (§4) nor plausible (§5), or when at L2 n < 14 (< 1 trade/day: design Card 2 retire "< 1 trigger/day"), or after L3 when §4 still fails. NOT_DECIDABLE licenses nothing: a fix is a new book.

For a later live rotation book (not in force until a belief record):
1. Per-book loss cap: DEC-036 §5 item 2 (0.40 SOL without a 0.25 start row, 0.75 after; full-wipe with R = 3,860,000 per position). The rotation executor's R must also cover two extra conversion sends per position (proposal for the code PR: R + 2 × send).
2. Decay calibration (DEC-036 §6 items 2-4): computed for the belief record from this book's strict 0.10 rows (L, U, μ0 and δ per rung, h_w, h_a from 10,000 day-block paths re-centred on μ0; trades resampled one by one if fewer than 10 sim days have trades, and said so). Path and sha256 go on card v2.
3. Retire check: DEC-036 §6 item 6, n0 = 25, μp = −c̄ as in §4.
4. Mechanism stops at once: a PumpSwap program or fee change (DEC-035 §4), a quote whose WSOL pool state is stale beyond the declared bound at an entry (refuse), a USDC depeg, a conversion-leg failure rate above 15% over the first 20 live conversion sends.

## 7. Decision host path
- [ ] DSL-expressible: no. FORGE cannot express the vault, V-band, 6 h spot ratio, take-profit on the post-buy mark or a quote conversion (design report §2).
- [x] **Own code.** Before any live step: (a) a live decision host for `l1-floor-oq-v1` on mal-fast-0's tape; (b) a **new quote-conversion send path** (SOL → quote through the quote's WSOL pool, quote → token, and back, with ATA handling for both accounts); (c) the DEC-036 §9 rotation executor with that port; each a T1 code PR with separate QP and reviewer and an md5 decision-equivalence replay proof against this book's decisions. FLOOR's executor cannot be reused (it builds SOL-quoted buys).

Frozen sha: rule `tools/arena_rule_l1_oq.py` blob 1055faab at main eae236ec (sha256 in §10).

## 8. Ladder plan
- First rung 0.10 (DEC-036 §5 item 1); 0.125 and 0.05 are decay rungs only.
- Up-step 0.10 → 0.25: the DEC-036 §5 item 1 minimum, plus live entry slip and live conversion-leg cost inside this card's sim assumptions, and the measured conversion-leg failure rate. A step condition, not edge evidence.
- Above 0.25: not in the envelope.

## 9. Records
- Belief record: none. Written only after §4 passes and §7 exists.
- This registration: §14 (DEC-035 Am.9 book-list record, HOLDOUT_LEDGER line). Start, twin starts and stops: state-file line, notebook entry (kind decision, manifest sha256s, S), LAB_STATE row, `data/console.json`.

## 10. Manifest (proposal)
Files in this directory: `osb-oq-v1.manifest.json` (strict, deciding), `osb-oq-v1-gh.manifest.json` (gap-in-hold twin), `osb-oq-v1-lat3010.manifest.json`, `osb-oq-v1-lat5590.manifest.json` (latency twins). The manager fills `start_hour` = S and `seed_from_hour` = S − 201 h (the same two values in all four), copies them to `/data2/mal/scratch/floor-open-sim/manifests/`, `chmod 0444`, and records each file sha256 and canonical sha256 (`osb_follow.manifest_sha256`) in the start record.

| Key | Value | Why |
| --- | --- | --- |
| book_id | `osb-oq-v1` (twins `-gh`, `-lat3010`, `-lat5590`) | |
| universe / host | `pumpswap_other_quote` / `hostv2` | required by osb_follow_oq |
| rule | `l1-floor-oq-v1` (`tools/arena_rule_l1_oq.BOT_ID`), `rule_overrides` {} | the FLOOR constants belong to the rule; overrides are refused |
| stakes_sol | [0.1, 0.25] | 0.10 decides (DEC-036 first rung); 0.25 reported |
| send_lamports | 505,000 | QP parity with the WSOL deciding stack |
| start_hour | `<set at start: next full hour after merge + warm-up>` | S |
| seed_from_hour | `<set at start: start_hour minus 201 h>` | |
| gap_exclusion | `on` (strict, deciding); twin `-gh`: `hold` + `twin_of` | QP d31815db: "on" is the WSOL default |
| latency | strict: no key (engine default 4.085 s); twins `pricing_lat_entry_s` 3.010 / 5.590 + `twin_of` | osb_follow refuses `pricing_lat_entry_s` without `twin_of` |
| tape_dir | `/data2/mal/scratch/floor-open-sim/keep` | keeper links (line (k)) |
| out_root | `/data2/mal/scratch/floor-open-sim/books/osb-oq-v1` (twins `books/osb-oq-v1-gh`, `-lat3010`, `-lat5590`) | see note |

**Out root note.** The brief named `/data2/mal/scratch/c1nf-open-sim/osb-oq-v1`. `osb_follow.guard_paths` refuses any tape_dir or out_root outside `OSB_ROOT = /data2/mal/scratch/floor-open-sim` (no CLI override; checked: "out_root: refused path (not under an allowed root ...)"), so the out roots sit under `floor-open-sim/books/` as the WSOL osb books do.

**Warm-up (how).** osb_follow_oq has no `engine_warmup_h` key (that is c1nf_forward_sim's, used by osb-v2-u2). Its warm-up is the seed window: at every start the driver rebuilds the pool table from `seed_from_hour` (>= 201 h before S, the lane host's `SEED_MIN_H`; a 200 h seed is refused, checked) to S − 365 min, then feeds S − 365 min .. S through the host state (`WARM_MIN_DEFAULT` 365 min), decisions off before S (`clock_start_ms` = S). Quote states for the conversion legs come from the same rows. The book starts only when `keep/.keeper-status.json` shows keep contiguous over [S − 201 h, S) (line (k)), recorded in the start notebook entry.

**Code pin check at every start** (gap 12): sha256 of the files at eae236ec:
- `scripts/research/osb_follow_oq.py` c042036677357f5a33cc5b3122864cc3968705b271f521d69eb6ed29fa0db24b
- `tools/arena_rule_l1_oq.py` 558651b35a59d99d77508f65c84458b17cf72b5267f60a8c56c6c73613e80684
- `tools/arena_oq_engine.py` b9bd8a08b7a65a87f2ca04368acb1c5050a10b9aa1f4ef7d7dec4b373152300a
- `tools/arena_oq_score.py` f79a3fa8e3cd3803675c02c2abf08f08f5156b80c2b4f82db64474028824893e

**Run.** One manager MiScusi job per book on mal-research-0, from a clean detached worktree at eae236ec (proposed `/data2/mal/wt/osb-oq-eae236ec`), `/data/mal/venv/bin/python scripts/research/osb_follow_oq.py <manifest>`, under `systemd-run --user --scope -p MemoryMax=8G` (design: one hostv2 book about 3-8 GB; lower it to the §13 dry-check peak RSS × 1.5 if that is below 8G, never above 8G without a new record). Python version and `pip freeze` sha256 recorded at start. Order: strict + `-gh` at S; the latency twins start when memory allows (they replay from S, so a later start loses nothing). Before a latency twin starts, the manager runs `osb_follow.twin_manifest_diff(twin, strict)` and requires `[]` (osb_follow_oq's CLI does not expose `--strict-manifest`; checked on the proposals: both twins `[]`). Restarts replay from S into a new run dir and compare every stored hour's decision and ledger md5 (exit 4 on a mismatch). Paper only.

**Out-root permissions** (research-0 default ACL, memory note research0-default-acl): before the first write, `mkdir -p <out_root> && chmod 700 <out_root> && setfacl -b -k <out_root>`, then check `getfacl` shows no default entries; the same for each twin root and the dry root.

## 11. Read rule
**Look instants** (fixed now; S from the start record):
- **L1 = S + 7 days.** Window: trades whose entry decision is in [S, L1). **Read instant R1 = L1 + 27 h** (24 h hold + 1 h exit-wait cap + 90 min tape lag, rounded up), so every window entry has exited, been capped or been scored as an unfilled round trip; any still `pending` is reported.
- **L2 = S + 14 days**, window [S, L2), R2 = L2 + 27 h: the deciding read when L1 has fewer than 40 trades or fewer than 7 complete days with the tape (§4 criterion 1). When L1 meets the minimum evidence, L1 decides and L2 is reported beside it as a later view of the same book (not a second chance: a BELIEVE at L1 is not re-tested at L2; a RETIRE at L1 ends the book).
- **L3 = S + 28 days**, R3 = L3 + 27 h: only under §5's one extension.

**What is read** (manager MiScusi job, from the pinned worktree, while the follower is running so status.json is under 7,200 s old; the CLI refuses otherwise):
`/data/mal/venv/bin/python -m tools.arena_oq_score <out_root>/book/ledger --stake 0.1` and `--stake 0.25`, for the strict book and each twin, with the engine's `status.json` next to the ledger. Outputs used: per leg (`flat`, `press`, and `filled_no_fail` labelled "no fail blend") n, total, mean, `ci90_lower_mean_sol`, days and positive days, `total_sol_ex_top3`, `drop_top_creator`, `drop_top_quote_mint`, `creator_unknown`; `flags` (unfilled round trips, unfilled_unpriced, stale_exit_capped, stale_unpriced, excluded and refused rows, rent report total, `buyback_fee_modelled` false); `excluded` (`share`, `strict_any_gap_in_hold_share`, both verdicts); `sensitivity_stranded_quote_leg`; and from status.json `pending`, `fee_audit`, `quote_post_fallback`, `stale_exit_capped*`, `entry_unfilled_*` (G1). The verdict word is one of BELIEVE (§4), EXTEND (§5), RETIRE (§6), NOT_DECIDABLE.

**Read prerequisites (code, T1, before R1; if not merged by R1 the read waits and says so):**
- **P1.** `arena_oq_score` has no window argument: it scores every row in the ledger. A flag restricting trades to an entry-decision window [S, L) (and days to that window) is needed, or the read would include entries after L.
- **P2.** The sum of `entry_conv_cost_lamports` + `exit_conv_cost_lamports` and the measured mean round-trip cost per trade, printed by the scorer (for §4's c̄ and §5 item 1(a)).
- **P3.** The scorer output carries the top creator and top quote mint keys (addresses). It stays in the book's 0700 out root; a Console or notebook copy carries the totals only, without those keys.

**Interim figures** (allowed only after P1, complete days only, the strict book at 0.10 under both legs, n, total and ex-top-3, days positive / days, excluded shares with G1 counts, the best-of-n sentence), with this first line, verbatim from QP-RULING-1118-u2-v2 §5:
> "Interim, complete days to <date>, before the look instant L1 = <instant>: not a read, not a GO or NO-GO, and not evidence of an edge."

No action on interim figures (no stop, retune, S or L move, live step). A day is complete when the tape runs at least 90 min past its end.

**Never pooled** with any other book (WSOL osb books, FLOOR live fills, its own twins summed with it, osb-v2-u2, U1, U4, C1-NF, any later OQ book) or with any later read's data. Twins are shown beside the strict book only.

**Disclosures (verbatim on every figure from this book, brackets filled unrounded):**
1. "osb-oq-v1 has no measured outcome of any kind before this book: the rule is FLOOR's l1-floor-v2 logic carried to other-quote PumpSwap pools, with constants fixed in code (PRs #1450, #1455, #1456) before any outcome was computed; the only prior data are in-sample trigger counts (job #3048: 3, 0, 6, 9, 21, 8, 13 pools per day on 2026-11-11..11-17, an upper bound). Figures are forward paper from S = <S> at 4.085 s entry, 4 sends of 505,000 lamports per trip, under both fail legs; they are not a gate read and not out-of-sample evidence of an edge. No live control book exists for other-quote pools: NOT COMPARABLE: not decision-grade. Treat any good number as shrunk (best of <n_books_watched> rows)."
2. "Strict gap rule: any feed gap from the entry decision to the exit fill removes the trade, so long holds (time exits) are excluded more often than quick take-profits. Capped stale exits (<a>) and unfilled round trips (<b>) get no gap test. Excluded <n> of <N> positions (board share <x>, strict share <y>)."
3. "Declared gaps (card §3): priority beyond 505,000 per send, fee-tier change between legs, the conversion leg's own landing, buyback_fee (<k> rows), missing creator_fee (<j> rows), quote state at the print vs the screen's static file, unpriced quote mints not seen, stranded-quote leg (full-loss bound <z> SOL flat)."
4. Twins: "Latency twins share entry decisions only; their differences include exit-timing changes."

## 12. Seal lines (proposal for the manager's SEALS-BRIEF line; workers do not edit SEALS-BRIEF)
> (t) OQ OPEN SIM osb-oq-v1 (DEC-035 Am.9 record 2026-11-18 osb-oq-v1, PR #<n>; tool PRs #1455, #1456, main eae236ec; QP VERDICT d31815db OK, REVIEWER VERDICT d31815db APPROVE): `scripts/research/osb_follow_oq.py` runs only as a manager MiScusi job on mal-research-0 from the clean detached worktree at eae236ec, after the four-file sha256 check of card §10, with a manifest under /data2/mal/scratch/floor-open-sim/manifests/ named osb-oq-v1*.json or dry-osb-oq-v1.json. It reads ONLY tape hours hard-linked under /data2/mal/scratch/floor-open-sim/keep/ by the keeper, from seed_from_hour (S − 201 h) on, and writes only under its out_root (/data2/mal/scratch/floor-open-sim/books/osb-oq-v1, -gh, -lat3010, -lat5590, or dry-osb-oq-v1). Agents never open, parse or copy a tape-* file (names and sizes only). Agents MAY read: the driver's status.json, md5-by-hour.jsonl, decisions.md5, manifest.sha256, code.sha256; the engine's `book/ledger/status.json` (pending counts, counters, fee_audit; no P&L field); keep/.keeper-status.json; job exit codes, the seed line (`table_seeded_rows`) and RSS. Agents NEVER open `book/ledger/oq-rows.jsonl`, the decision log or bots.json of these books; before R1 these are read only by `tools.arena_oq_score` (at a look, or for an interim figure under card §11), run by the manager. The scorer's output stays in the out root; copies carry no pool id, mint, creator, signature, price or reserve. Paper only; an open simulation, not a gate read.

Active seals that apply to anyone briefed on this book: every line of SEALS-BRIEF and AGENT-BRIEF-COMMON §2 (DBC forward window, EXP-024 Look-2, D2/D3, FLOOR lane readers, the fast-0 tip tape, the arena tip writer's derived copy, the offload archives). The seed and forward hours are November hv7 hours (>= 2026-11-09), outside D2/D3 and the DBC window.

## 13. Day-1 dry check (counts only; the real-layout precount)
Before S, the manager runs the follower once on the real layout and reads counts only:
1. Manifest `dry-osb-oq-v1.json` = the strict manifest with `book_id` `dry-osb-oq-v1`, `out_root` `/data2/mal/scratch/floor-open-sim/books/dry-osb-oq-v1` (0700, `setfacl -b -k` first), and `start_hour` = the latest closed hour H0 that the keeper holds, `seed_from_hour` = H0 − 201 h. Hours before S are in-sample hours already open to FLOOR research; the dry outputs are never read for P&L and never join this book.
2. Run `osb_follow_oq.py dry-osb-oq-v1.json --max-hours 1` as a MiScusi job under `systemd-run --user --scope -p MemoryMax=8G`, from the pinned worktree, after the four-file sha256 check.
3. Read ONLY: exit code (0 expected); the stdout seed line (`table_seeded_rows`, `seed_from_hour`); the driver's status.json (`book_id`, `manifest_sha256`, `code_sha256`, `run`); md5-by-hour.jsonl (one row, md5s only); the engine's status.json counters (`pending`, `entry_refused_*`, `excluded_*`, `entries_filled`, `quote_post_fallback`, `fee_audit`, `lat_entry_s` = 4.085, `gap_mode` = "on"); peak RSS and wall seconds from the scope. Never the ledger or the decision log.
4. PASS when: exit 0; `table_seeded_rows` > 0; `lat_entry_s` 4.085 and `gap_mode` "on"; RSS below 8G with room for the 14-28 day book (state the extrapolation, as the osb-v2-u2 record did); no refusal from the path guard. Zero entries in one hour is not a failure (median 8 pools per day). A second `--max-hours 1` run into the same dry root must PASS its restart md5 check (exit 0, not 4).
5. Record the counts in the start notebook entry, then delete the dry root.

## 14. Records carried by this PR
- DEC-035 Amendment 9 book-list record "osb-oq-v1" (appended to `DEC/DEC-035-live-learning-lane.md`).
- HOLDOUT_LEDGER changelog line (top of the changelog in `docs/HOLDOUT_LEDGER.md`).

Paper only. No book has cleared the promotion gate. Nothing here says osb-oq-v1 or any book makes money.
