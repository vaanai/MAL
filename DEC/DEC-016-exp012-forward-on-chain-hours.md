# DEC-016: EXP-012's forward book is scored on forward getBlock hours, by the read's own code

| Field | Value |
| --- | --- |
| **Status** | **Proposed** (2026-10-02). **Nothing starts before the owner's OK.** It spends paid Helius credits on forward data, and the owner reserved paid-feed changes to himself (via Helm, 2026-10-02). With the clean clock of Amendment 1 (2026-10-06T00:00:00Z), the walk must start by **2026-10-04T00Z**: 48 h of buffer, so every counted mint (created up to 24 h before its migration) also has 24 h of creator history. |
| **Decider** | Claude manager (`mal-research-0`). Owner confirmation asked 2026-10-02. |
| **Date** | 2026-10-02 |
| **Amends** | [DEC-015](DEC-015-forward-paper-on-fast.md) §1, only for **how EXP-012's forward book is measured**. DEC-015's runner track on `mal-fast-0` continues as the live-readiness track (§3). |
| **Does not amend** | The promotion gate, the paper-only fence, DEC-014 (ledger, single read), "never add forward books during a kill-review week" (nothing counts before 2026-10-05T05:00:00Z), and EXP-012 §9 (forward paper, never direct promotion). |

## Context

1. **The live tapes miss trades.** On 2026-10-01T17–19 the fast public tape held 84.759% of chain trades and Oracle's tape 66.241% ([note](../ARTIFACTS/lab/tape-coverage-chain-verdict-2026-10-02.md), DEC-015 §2.2 FAIL).
2. **EXP-012 was frozen and read on getBlock walks**, which are chain-complete. A forward book on an incomplete tape measures a different input from the one the read passed on.
3. **The runner cannot run EXP-012 as pre-registered** (review of `tools/forward_paper.py` on `main` at `e67b387`):
   - It has no model gate on `migrate` books.
   - Its fill model is time-based (receive time plus median chain latency, portal route, 0.001 SOL priority, a random 15% fail draw). The read's fill model is slot-based (slot+1 start, direct route, 0.0005 SOL per side, both fail models as expected values). Most of EXP-012's lift is fill prediction (98.9% vs 28.0% fill), so this difference matters.
   - Its hard size ceiling is 0.05 SOL (`HARD_MAX_POSITION_LAMPORTS`). EXP-012 is 0.5 SOL.
   - Some features cannot be rebuilt online exactly: the 32-minute watch-list truncation, and `creator_prior_mints_24h` after each 00:00Z restart.
4. **There is a precedent.** The migrate-direct forward book was scored offline on sealed forward hours at 0.5 SOL (`tools/migrate_direct_oos.py forward`).

## Decision

1. **Forward walk (on the owner's OK).** A getBlock follower on `mal-research-0`, run as a MiScusi job:
   - It walks each complete UTC hour about 5 minutes after it ends, into `/data/mal/blocks/forward-1002`, with the same walker code the training walks use.
   - Every sealed hour gets `backfill_verify --content` (0 flagged, 0 duplicates) and a sha256 line before any scorer reads it.
   - Cost: about 13.4k credits per hour, about 0.32M/day, about 9.7M/month, inside the ~40M/month budget.
   - Rate: `--rps 8` (`scripts/research/forward-walk.sh`), so an hour of about 13.4k slots seals in about 28 minutes and leaves room to catch up after downtime.
   - Helius slot: the walk holds **one of the 4 shared Helius lock slots** (10 rps each) for its whole life, about 14 days from 2026-10-04T00Z through the read. The backfill walkers drop to 3 concurrent slots for that time. Total stays within the ~40 rps share.
   - The same hours are the chain truth for further tape-coverage checks.
2. **Measurement (on owner confirmation).** EXP-012's forward book is the frozen model, threshold and execution of EXP-012 §2, scored by a forward scorer (`tools/exp012_forward.py`) that reuses `tools/exp012_score.py`'s table build and scoring path unchanged, on sealed forward hours only.
   - It checks the frozen artifacts' md5 (`ARTIFACTS/exp012/FROZEN.md5`) on every run.
   - Its rows are append-only, keyed by mint and migration time.
   - The gate is computed by `tools.exp011_score.compute_gate`, under both fail models.
3. **Clean clock: 2026-10-05T05:00:00Z.** Only migrations at or after it count. Hours before it are feature buffer only. The scorer's pool starts 48 h before the clean clock: `buffer_hours=24` for create-to-migration, plus 24 h of creator history (`creator_prior_mints_24h`). It reads no hour before 2026-10-03T05.
4. **Gate:** exactly CLAUDE.md's, under both fail models. A pass here is **forward simulated paper**, not live evidence. It earns the owner's decision on a small live trial, with the live-readiness track (§3) as a precondition.

## 3. The live-readiness track (DEC-015, unchanged in purpose)

Before any live order, the fast-0 runner must show that the replay's fill and latency assumptions hold on a real feed:
- an EXP-012 model gate inside the runner, with feature parity measured against the forward scorer;
- a gate-grade feed (redundant sockets or a getBlock tip follower, decided by the truth-1002 re-check);
- md5 equivalence of the existing books;
- its own memory slice;
- lag probation.

Its paper rows are compared with the forward scorer's rows for the same mints, to measure fill and entry-timing agreement. They are not a second gate.

## Consequences

- If confirmed, EXP-012's forward measurement no longer waits on the fast tape, the runner redesign or lag probation. It can count from 2026-10-05T05:00:00Z.
- Until the owner answers, a builder writes and tests `tools/exp012_forward.py`. It is needed either way, because it can also score a forward tape.
- About 9.7M credits/month go to the forward walk while it runs. Report: unit (MiScusi job id), credits per day, and what it enables (forward book plus continuous chain truth).
- If the owner declines, no forward walk runs, and EXP-012's forward book waits for the runner track and a gate-grade feed, as DEC-015 planned.

## Amendment 1 (2026-10-02): one pre-registered read, a full-day clock, and what a PASS does and does not support

This follows a `quant-proof` review of the design. It is fixed before any forward hour is walked.

1. **Clean clock: 2026-10-06T00:00:00Z.** It replaces 2026-10-05T05:00:00Z in §2.3, so the clock starts after the kill-review week and on a full UTC day. The pool starts 48 h earlier, at 2026-10-04T00Z.
2. **One read, at a fixed end: 10 full UTC days, `[2026-10-06T00, 2026-10-16T00)`.**
   - The read runs once, after the last migration in the window has had its 30-minute exit cap and its hour is sealed and verified, at about 2026-10-16T02Z.
   - Any `report` before then is **INTERIM**: it shows counts, and **no verdict, mean, CI or day sign**.
   - The end date is not moved by what interim counts show.
   - Why 10 days: about 75 trades/day (451 over 6 days at the read) gives about 750 trades. The read's CIs imply a per-trade spread of about 0.21 SOL (flat) and 0.12 SOL (pressure). At n ≈ 750 the pressure CI lower bound stays above 0 even if the mean halves (0.0103 − 1.645 × 0.0044 ≈ 0.003). A 5-day book (n ≈ 375) would be a coin flip at a halved mean.
3. **Gate:** CLAUDE.md's promotion gate, under both fail models, k = 1 (a single pre-registered primary book). If another book is a live candidate at the same read, DEC-014 Holm at 10,000 draws applies.
4. **Report-only context (no gate role), recorded per day:**
   - the unfiltered migrate-direct baseline on the same hours;
   - the selected fraction;
   - the fill rate.
   These separate the model from the market.
5. **What a PASS supports.** Only asking the owner for a small live trial, and only after both of these:
   - (a) **Latency and size sensitivity** on the same rows: re-scored at entry slot + k for the measured fast-0 p50 and p90 latency, and at the trial's size and priority fee. **A PASS that turns negative at measured latency or at trial size does not support live.** The rule is stated now, before any forward data exists.
   - (b) The **live-readiness comparison** (§3): runner paper rows against scorer rows for the same mints, with tolerances fixed before the read. It covers entered-set overlap, fill agreement, entry slot and price difference, realized fail rate against the pressure model by bucket, and exit price difference.
6. **Not changed:** the frozen model, threshold, execution, size (0.5 SOL in the scorer) and both fail models.

## Amendment 2 (2026-10-02): files that hold P&L before the read are not opened

`tools/exp012_forward.py` (#220, #223) enforces the pinned window, INTERIM-only reports, the `final_read.lock` and an external FINAL ledger at `/data/mal/exp012-forward/FINAL_READS.jsonl`. Three things still hold per-row nets at rest during the window, because the scorer needs them:

- `OUT/rows.jsonl`
- `OUT/scratch/*.jsonl`
- a FINAL `report.json` or `report.md`, which only exists after the read

**Rule:** before the FINAL read, no person, agent or job opens or prints these files, or any `flat`, `press`, `*_sol` or `gross` field from them. Only `tools/exp012_forward.py score`, `report` and `export-decisions` read them. `export-decisions` writes only the allowlisted keys `mint, mig_ms, score, entered, day`, never a net, SOL, gross, status or fill field, and that export may be used before the read for the runner-vs-scorer comparison (§3; added 2026-10-02 with #228). Monitoring uses INTERIM `report` output and the `runs.jsonl` counts only. A breach is recorded here, dated, and the read is reported as compromised.

**Recorded seal exposure, 2026-10-07 (manager9).** Job #368 read the fast-0 runner's early-arm `arm-audit.jsonl` and printed per-day early-arm outcome counts for 10-05 to 10-07: armed, fail, skipped_migrated, and a risk/kill skip count.
- The job lumped the risk reasons in-process before printing, so no `daily_loss_cap`-specific count was printed. The lumped count still includes `daily_loss_cap`, so it falls under the Amendment 3 seal extension ("daily_loss_cap reasons and counts").
- No P&L, cost or exit field was read.
- **Where the counts went:** PR #451's first head `a59ff80` (still readable in the PR's commit list), the PR body's edit history, MiScusi notebook n_6W8jWsyz1d8aXw, and the first quant-proof review of `a59ff80`, which restated them. They are not on `main`, and later notes do not repeat them.
- **Effect:** under the Amendment 2 rule, the 10-16 FINAL read **will be reported as compromised**.
  - Its result cannot, by itself, support a live request. A PASS first needs confirmation on a later fresh sealed window or block under DEC-014.
  - The pre-registered computation is unchanged and still runs as written. The FINAL report carries this disclosure.
  - *Corrected 2026-10-07 after a review by the owner's reviewer: an earlier wording left the label to the owner, which relaxed a fixed consequence after the breach. Any different consequence needs a new dated amendment that gives its reason.*
- **From now until the read,** runner-side reads go only through `tools/runner_timing_read.py`, which allowlists timing fields and refuses per-day counts. This is enforced in code.

## Amendment 3 (2026-10-03): the latency/size rule, the runner-vs-scorer tolerances, the runner seal, and live preconditions, fixed before any runner row exists

Written before the fast-0 runner is installed. The installer refuses before 2026-10-05T05:00Z, so no runner row exists yet. No forward P&L has been opened (Amendment 2).

This fills in Amendment 1 §5 (a) and (b). It was revised after a `quant-proof` review of #256, applying edits E1–E8. Neither part can turn a FAIL into a PASS. Both are checked only after a FINAL PASS, apart from the P&L-free rows marked "before read".

### (a) Latency and size sensitivity

1. **Measured latency.**
   - **Decisions used.** The fast-0 runner's EXP-012 **shadow-ledger** `enter` decisions with `decision_t_ms` in [runner clean clock, 2026-10-16T00Z). The runner clean clock is the end of the DEC-015 §2.5 probation, as recorded in LAB_STATE.
   - **Per-decision latency.** For each decision, L_i = `decision_t_ms` + `recv_to_decision_ms` − (gate-row `mig_ms` + 500).
   - **Slot offset.** k_i = 1 + ceil(L_i / slot_ms).
     - slot_ms = 3,600,000 / the mean slots per sealed forward-walk hour over the same window. The lab measurement is about 268 ms, not 400.
     - A `stale_recv` drop of an EXP-012 migrate counts as k_i = +∞.
   - **Percentiles.** k(p50) and k(p90) are the non-interpolating percentiles of k_i (`_pct` convention), with a minimum of 1.
   - **Minimum sample.** With fewer than 100 decisions, the check is **not decidable**.
   - **Send and inclusion.** The "1 +" is the only allowance for send and inclusion. It is an assumption, not a measurement.
2. **Source.**
   - Use only an allowlisted latency export: `mint, mig_ms, decision_t_ms, recv_to_decision_ms, ledger, action, stale` from `exp012-gate.jsonl` and `decisions.jsonl`. Never `positions.jsonl` or `runner-status`.
   - k(p50), k(p90), slot_ms, n and the export's sha256 are written into this DEC **before** the FINAL read.
3. **Re-score.**
   - **The tool.** A forward sensitivity tool, merged with fixture tests and its commit recorded here, before 2026-10-16T00Z. It re-runs the FINAL window's sealed hours through `exp012_forward`'s scoring path.
   - **Reproduction first.** Before any other output is read, it must reproduce the FINAL rows' `flat` and `press` byte for byte at k = 1, bound `start`, 0.5 SOL, 500,000 lamports, no cap.
   - **Then it scores the FINAL `entered` set with:**
     - entry at slot + k, bound `end`;
     - every exit fill delayed by k − 1 more slots;
     - the trial's size, priority, tip and maximum concurrent positions, applied in decision order.
   - **Post-read fields.** It also writes the per-row entry and exit slot, pre-fee spot, exit reason and pressure probability, for use after the read only.
   - `tools/exp012_latency_sensitivity.py` is not used, because it refuses the forward root.
   - **Trial terms.** The owner names size, priority, tip and maximum concurrent positions in writing before 2026-10-16T00Z. If none are named: 0.5 SOL, 500,000 lamports, no tip, max concurrent 3.
4. **Rule.** Live is supported only if both of these hold:
   - (i) the trial book at k(p50) clears the **full promotion gate** under both fail models: n ≥ 100; at least 5 UTC days with a majority positive; 90% CI lower bound > 0; ex-top-3 > 0;
   - (ii) the trial book at k(p90) has mean > 0 and ex-top-3 > 0 under both fail models.

   Each is computed once. A later runner fix or a new latency measurement does not re-open (a) on these rows. That needs a new forward window and a new read.

### (b) Runner-vs-scorer live-readiness comparison

- **Population:** mints whose migration chain time is in [runner clean clock, 2026-10-16T00Z) and in runner-up minutes.
  - Runner-up means the heartbeat is less than 60 s old, excluding each restart from stop until 10 min after the first heartbeat.
  - "Both-seen" means the runner wrote an `exp012-gate` row and the scorer has a row.
- **Minimum:** at least 200 mints entered by both, over at least 5 UTC days. Below that the check is **not decidable**.

| # | Quantity | Tolerance | When |
| --- | --- | --- | --- |
| 0 | Coverage: share of scorer-entered population mints with a runner gate row and no `stale_recv` drop | ≥ 95% | before read |
| 1 | Jaccard of entered sets, both-seen mints | ≥ 0.90 | before read |
| 2 | \|runner score − scorer score\|, both-seen mints | p95 ≤ 0.02, p99 ≤ 0.05; max reported only | before read |
| 3 | Curve-fill agreement on mints entered by both. The runner's fill is taken **before** the flat 15% landing draw: `missed_landing` with `counterfactual_fill` counts as filled. | ≥ 95% agree, and runner fill rate ≥ scorer fill rate − 3 pp | after read |
| 4 | Entry pre-fee spot: runner `entry_spot_sol` vs the scorer state price at the runner's landing slot, mints filled by both | median ≤ 0.5%, p90 ≤ 2% | after read |
| 5 | Exit-reason agreement on mints filled by both. Where the reasons match, pre-fee exit spot vs the scorer state price at the runner's exit slot | ≥ 90% agree; median ≤ 0.5%, p90 ≤ 2% | after read |
| 6 | Scorer book limited to runner-attempted mints (scorer-entered, runner gate pass, not stale), at k(p50) and the trial terms | mean > 0, both fail models | after read |

- **Landing failure.** The runner's fail outcome is a flat 15% draw (`DEFAULT_FAIL_RATE`), so no paper row tests landing failure. That test moves to (c).
- **Tooling.** `tools/forward_exp012_replay.py` is updated before the window to compute rows 0–2 as defined here.
- **Before the window.** The reachability of rows 1–2 is checked P&L-free on a recorded tape. If it falls short, the runner is fixed, not the tolerance.
- **Rule.** Live is supported only if every row holds. Any row that fails, or is not decidable, blocks the request to the owner until it is fixed and re-measured on a later, fresh window. A fix to the runner never changes the scorer or the FINAL verdict.

### Seal extension (adds to Amendment 2's rule)

Until the FINAL read, the seal also covers the fast-0 runner's EXP-012 book:
- every `pnl`, `cost` or `exit` field in `positions.jsonl`;
- `day_pnl_sol` and any totals in `runner-status.json` or runner reports for that book;
- `daily_loss_cap` reasons and counts;
- any Console or brief line built from them;
- pricing the exported entered mints from any outside source.

Health checks print lag, heartbeat and counts only, never the whole of `runner-status`.

### (c) Before the first live order

This is a separate owner decision and DEC. Today's rule of no trading keys stays until then. It needs:
- key custody, and a wallet funded with only the trial bankroll;
- the send path and its tip, which are part of the trial fee in (a);
- size, maximum concurrent positions, a daily loss cap and a total-loss stop, written down;
- every live fill logged with slot, pre-fee spot and fees, next to the scorer's simulated fill for that mint;
- a landing-fail stop: after 30 live attempts, halt if realized failures exceed the pressure model's expected failures on those same attempts by more than 10 pp (one-sided).

### Trial terms, named by the owner (2026-10-03)

On 2026-10-03, before any runner row existed and well before the FINAL read, the owner chose the defaults in the manager session ("defaults seem good to me"), answering question `q_Wy3S6eK9bN74Ng`. For Amendment 3 (a), the trial terms are:
- **0.5 SOL** per entry;
- **3** maximum concurrent positions;
- **500,000 lamports** priority per side;
- **no tip**.

They are fixed here, and they do not change after the read.

## Amendment 4 (2026-10-04): the live-support book is priced on vault + V

This is fixed before any runner row exists and before any forward P&L is opened (Amendment 2). Text drafted by `quant-proof`.

**Context.** PR #280 and PR #281 show that PumpSwap swaps price on quote vault + V, where V is the pool's virtual quote reserve, and that the frozen paper path prices on the vault alone.
- At migration, vault + V reproduces the bonding curve's final price (ratio 0.9998). The vault alone is 20.7% low.
- On the spent read block (correction analysis, not a new read), V-correction lowered the flat mean from 0.03486 to 0.02526 SOL per trade. The gate's flat CI90 lower bound fell from 0.01874 to 0.00928 SOL.

1. **Two books, one FINAL run.** The FINAL read at about 2026-10-16T02Z computes two books in the same run:
   - **(A)** the pre-registered book, unchanged (frozen pricing). Its verdict is recorded as the EXP-012 FINAL verdict.
   - **(B)** the V-corrected book: the same entered set, asserted identical mint by mint, re-priced by `tools/pumpswap_virtual_adapter.py` with `mcap_mode="v"`. The adapter commit is recorded here before 2026-10-16T00Z.
     **Recorded 2026-10-04:** adapter `tools/pumpswap_virtual_adapter.py` at commit `bbcaeb64b347d4b4f8063ead802994d23eb35009` (in main since #281), and driver `tools/exp012_virtual_rescore.py` at main `0a3020c` (#284: every pool per mint, for §3). r2 (driver at `79b34c2`, before #284) reproduced r1's gate numbers exactly (`ARTIFACTS/lab/exp012-virtual-rescore-r2-2026-10-04.md`). r2 did not run the #284 driver.

   The unpatched pass must reproduce (A)'s `flat` and `press` byte for byte before (B) is read.
2. **Live support requires (B).** Amendment 1 §5 and Amendment 3 (a) are evaluated on (B), not (A):
   - at k = 1, and at k(p50) with the trial terms: the full gate under both fail models;
   - at k(p90): mean > 0 and ex-top-3 > 0.

   If (A) passes and (B) fails, live is not supported. If (A) fails, live is not supported, whatever (B) shows. V-correction can only remove support; it never adds it.
3. **V map.**
   - After 2026-10-16T00Z and before the read, V is fetched with `getMultipleAccounts` for **every** PumpSwap pool of every migrated mint in the window, not one pool per mint.
   - The map's sha256 and its null count are recorded here.
   - A pool with no readable V is never treated as V = 0.
   - If any of the top 3 trades, or more than 1% of entered trades, touch a null-V pool, (B) is **not decidable**, and live is not supported.
4. **Fee tier.** (B) is also computed with `mcap_mode="vault"`, for report only. The rule in §2 applies to `mcap_mode="v"`. If the two modes disagree on any gate condition, live is not supported until the tier rule is resolved on chain.
5. **Validation, after the read and before the live decision.** On the window's sealed hours, both of these must hold, otherwise (B) is not decidable:
   - V-corrected sell residuals for V > 0 pools: median |error| < 1 bps and at least 75% within 1 bps. The spent block measured 79.9%.
   - Buy implied-fee residual: at least 90% within 1 bps.
6. **Runner rows.** Amendment 3 (b) rows 4–5 compare the runner's spot with the scorer's **V-corrected** state price. A runner that prices on the vault alone fails rows 4–5 by construction, and must be fixed before any live request.
7. **Not changed:** the model, threshold, features, execution, size, both fail models, the window, the read date, the no-peek seal and the trial terms.

## Amendment 5 (2026-10-06, pre-read): how the book (B) V map is built, and the (B) tools

Written before any forward P&L file was opened, and before 2026-10-16T00Z. It tightens Amendment 4 §3 and names the (B) tools. No bar, threshold, model, window, read date, fail model, trial term or seal changes.

**Why.**
- The EXP-012 back-check on explore-0814 (job #207) refused after its read because 1 primary trade sat on a pool with no V. 29 pools were absent from the map and 1 was a closed account.
- Job #235 (pool and mint fields only) found the cause. The map was built by `tools/exp012_virtual_rescore.py pools`, which keeps only the pools of mints with a `complete` row in the same view. Every one of the 29 absent pools belonged to a mint that traded in the view but had not migrated in it.
- Amendment 4 §3's wording ("every PumpSwap pool of every migrated mint in the window") invites the same gap.
- Also, a pool closed by 10-16 reads null, while V is constant per pool. Evidence: #197 checked 10 pools over 08-14..08-25. Across the `pool_v_0814` → `pool_v_0909` refetch two days apart, all 34,892 non-null values were unchanged.

**1. Pool set.** The (B) map covers **every PumpSwap pool printed in any trade file of the hours (B) scores**: the FINAL run's pool hours, buffer hours included, from the walk start. It is collected by `tools/exp012_forward_vmap.py pools`, which uses a real JSON parse and no migration filter, and only on hours that pass `exp012_forward.hour_problems`; each file's sha256 is re-checked against its verify line after the scan. For the FINAL map the hours are taken from the FINAL run's `runs.jsonl` (`pool_from`, `to_exclusive`), not typed by hand. This is a superset of Amendment 4 §3's set.

**2. Early snapshots (outcome-blind).** Before 2026-10-16T00Z the manager may:
- run `pools` and `fetch` over sealed forward hours;
- freeze the result with `snapshot`, which writes a read-only copy and a line in `snapshots.jsonl` (utc, sha256, n, n_null).

Only trade-file pool ids and on-chain pool accounts are read; no row, report or scratch file under Amendment 2 is opened. Snapshot shas go in the run log and the FINAL report. **No join before the read:** null-V pool ids are written to files only, never printed, and are never matched against decisions, exported decisions or entered mints before the FINAL read (a pool that closed early is outcome information).

**3. The FINAL map.**
- After 2026-10-16T00Z, V is fetched into a **new, empty** map file for the §1 pool set (`fetch --new`, which refuses an existing file). This is Amendment 4 §3's fetch and stays primary. Each null is recorded as `closed` (no account) or `unreadable` (account present, V not parseable).
- Then `merge` fills only pools of the §1 set that are `closed` or absent in that map, from snapshots listed in `snapshots.jsonl` with a matching sha. It refuses unless the final map's `fetch.json` shows `new = true`, a fetch start at or after 2026-10-16T00:00Z, and a pool-set sha equal to the §1 set given to merge (so every pool in the set went through the post-cutoff fetch). It refuses any null pool with no recorded reason. It never fills an `unreadable` pool (that may be a program or layout change, a real pricing problem). It refuses if two non-null values for a pool ever differ; a refusal makes (B) NOT_DECIDABLE.
- The merge record, embedded in the (B) report, gives: n pools, nulls before the merge, pools filled from a snapshot (with ids, in files only), nulls after, the fresh-fetch fields, and every input and output sha (snapshots, final map, reasons file, fetch record, pool set, output).
- A pool still null after the merge is never V = 0. Amendment 4 §3's rule (top 3, or > 1% of entered trades) applies to it unchanged.

**4. (B) tools.** These are recorded here before 2026-10-16T00Z, at the commits that merge:
- `tools/exp012_forward_vbook.py`: book (B) on the FINAL's own entered set. It refuses without the FINAL (A) marker, and binds to the merge record of §3 (its output sha must equal the map's). It reproduces (A) byte for byte under frozen pricing before computing (B), refuses if the mode-v entered set differs from (A)'s, applies the null-V rule, and uses `exp012_forward.build_report`'s gate. A vault-mode entered-set difference or any vault/v gate disagreement does not refuse; it is listed in `live_blockers` (Amendment 4 §4 already blocks live on it).
  - **(B) is single-use per window.** Checks that read no V-priced row (seal, merge binding, map sha, frozen checks, hours, and the frozen reproduction, which shows only (A)) do not spend the window. Immediately before the first V-priced pass, a STARTED line goes into `VBOOK_RUNS.jsonl` beside the FINAL out dir (window, FINAL rows sha, map sha, merge-record sha). On the pinned window that path cannot be overridden, in vbook or in the sensitivity re-score. A second STARTED line for the same window is refused. After STARTED, every orderly exit writes a terminal line (DONE with the verdict, or REFUSED_AFTER_READ) before any report file. A refusal after STARTED, or a STARTED line with no terminal line (a hard kill), makes (B) NOT_DECIDABLE.
- `tools/exp012_forward_vmap.py`: §1–§3, plus §5 validation through `validate`.
- The Amendment 3(a) sensitivity re-score on (B): `tools/exp012_forward_sensitivity.py` with V pricing.

Commits (merged 2026-10-06, after four quant-proof rounds; the last round was OK on every PR):
- `tools/exp012_forward_vmap.py`: #374, merge `5aaeb700a2bf49d8c258817a37963a323a541ad9`.
- `tools/exp012_forward_vbook.py`: #375, merge `fe8f6dde78829b8e8e7cffd1000551e9d9f0b8b1`.
- `tools/exp012_forward_sensitivity.py` with V pricing (and vbook's `tracked_call` refactor): #378, merge `7253e0700255080ff28bb400db8f95b39420fc11`.

The FINAL (B) runs use `main` at or after `7253e07`, and its commit is recorded in each report.

**5. Definitions fixed before the read.**
- **Top 3 (Amendment 4 §3):** the union of the top 3 entered trades by `flat` and the top 3 by `press`, since each gate leg drops its own top 3.
- **Touch:** a trade touches a pool if its mint printed on that pool anywhere in the scoring worker's tape, after the exit included. This over-counts contact; it can only make (B) NOT_DECIDABLE, never PASS.
- **V = 0:** a pool whose chain account reads V = 0 is priced on the vault alone, which is what the chain does. It is not null. The count of entered trades touching a V = 0 pool is reported, but it is not a blocker.
  - **Wording fixed 2026-10-06, pre-read, after #384:** read "V = 0" here as **V ≤ 0**. Stored V = V0 − A − B (pending counters, signed), so a V0 = 0 pool reads as a small negative value. The adapter prices any V ≤ 0 on the vault alone, and the V = 0 report counts V ≤ 0. A pool "still null" means one whose V cannot be decoded at all; it is not a V0 = 0 pool.
- **Amendment 4 §5 sample:** `exp012_forward_vmap validate --final-out-dir`, which takes the window from the FINAL run and fixes the sample (no override): 12 hours evenly spaced over the window's sealed hours, the first 60,000 PumpSwap rows of each hour, with the §5 thresholds as written.
- **Amendment 3(a) on (B):** the sensitivity re-score on the pinned window refuses before its claim unless it is given the V map and the window's single DONE vbook run on the same map sha and FINAL rows, with (B) = PASS (a NOT_DECIDABLE or FAIL (B) already means no live support, and the single-use window is kept). Every (B)-only refusal it can compute without P&L runs before the claim. Its headline verdict is (B)'s, and (A)'s is report-only. The vault/v agreement of Amendment 4 §4 is assessed by vbook at k = 1; at k(p50) and k(p90) only mode v is computed.

**6. Effect.** This changes no bar, threshold, model, window or fail model. Filling nulls can make a NOT_DECIDABLE (B) decidable, in either direction. Every filled value is a pool the chain priced, checked for constancy against the post-cutoff fetch wherever both are non-null. Amendment 4 §2 still needs both (A) and (B).

**7. V0 moves with LP supply (added 2026-10-06, pre-read; revised after quant-proof the same day).** Written before any forward P&L, decision or entered-mint file was opened, and before 2026-10-16T00Z. Evidence: [pumpswap-v0-lp-law-2026-10-06.md](../ARTIFACTS/lab/pumpswap-v0-lp-law-2026-10-06.md) (jobs #278, #282–#284; pool fields and transaction logs only), re-derived independently by quant-proof.
- **Finding.** V0 (`v_base` = V + A + B) is not constant per pool. At each PumpSwap `Deposit` or `Withdraw` it becomes `floor(V0 × S_after / S_before)`, where S is the pool's LP mint supply. S_before is the event's `lp_mint_supply`, and S_after = S_before ± the LP amount. This reproduced all 6 V0 moves in job #278, 16 LP operations, with 0 lamports residual; 3 later operations checked against #282 also matched. §3's "any v_base difference refuses" (#383) would make (B) NOT_DECIDABLE on ordinary LP activity, so it is replaced below. Nothing else in §1–§6 changes.
- **(0) Deadline.** The §7 tools must be merged, with quant-proof OK and their merge commits recorded here, **before 2026-10-16T00:00Z**. If they are not, §3's strict rule (#383) applies unchanged.
- **(a) LP history.**
  - **What is read.** For a set of pools and a time range, the tool reads each pool's LP mint and current LP supply from its account. It then lists the PumpSwap `Deposit`/`Withdraw` events on that pool, using the LP mint's signatures and `getTransaction` at `maxSupportedTransactionVersion` 1. Each event records slot, block time, signature, kind, S_before and the signed LP delta.
  - **When a pool is unresolved.** Any of these makes a pool **unresolved**:
    - the account is missing, or the signature paging does not reach the start of the range;
    - a transaction fetch fails;
    - a transaction has truncated logs, unless its event can be decoded from the program's self-CPI event instruction;
    - the S sequence does not chain, meaning an event's S_after differs from the next event's S_before;
    - the last S_after differs from the LP supply read in the same run, at or after the latest fetch.
  - **Retries.** Every pool unresolved by a failed RPC read (an error or no response on the account, LP supply, signature-paging or transaction call) gets the same up to 3 further passes. *(Wording widened 2026-10-06, pre-read, after quant-proof on #402: account and supply reads are RPC reads like the others.)* A read that succeeds but returns a missing or unparseable account is not a failed read. Pools unresolved by it, or by a chain, truncation or supply mismatch, get no retry. Every attempt is logged per pool, with its pass number and reason.
  - **Output.** The output file's sha256 is recorded wherever it is used.
  - **For (B):** one run over the pools of all entered trades. The first completed run for the window is the one used, and its sha256 goes into the vbook STARTED line. A later run for the same window is not used.
- **(b) Merge.**
  - **V0 of an account with no pending counters (clarified 2026-10-06, pre-read, after the dry-run smoke, job #290).** §7's V0 is V + A + B. An account shorter than 287 bytes has no A or B fields (its detail record has `pending` and `v_base` both null), so its V0 is its stored V. A read taken before an account extension is the same. This corrects a tool defect under (e′): the tool counted such reads as unresolved for lack of a `v_base`. It is not a change to the rule or the ceiling. A pool whose V0 values computed this way differ is still judged by the LP rule. In the smoke, 53 such pools had a null `v_base` in both snapshots with identical stored V, and 1 more was extended between fetches with V0 unchanged. All 54 had been counted as unresolved (0.099% of the set). None of them moved.
  - **Consistent pools.** Two V0 values of a pool (a snapshot and a later snapshot, or a snapshot and the final map) are **consistent** if they are equal. They are also consistent if replaying the pool's LP events between the two fetches reproduces the later value within 1 lamport per event. An event inside a fetch's own time span may be placed on either side, and any placement that reproduces the value counts.
  - **Other pools.** The rest are **unexplained** (the rule fails) or **unresolved** (per (a)).
  - **Ceiling.** **Merge refuses if unexplained plus unresolved pools exceed 0.1% of the §1 pool set.** It never fills a null from such a pool.
  - **What the ceiling covers.** It applies to moves the rule does not explain, not to all moves. The owner asked for a refusal when "too many pools move". The cause of the moves is now known and they are priced exactly, so only unexplained moves can corrupt (B). The merge record still reports the count of explained moved pools. The (B) report gives the count of entered trades on LP-moved pools.
  - **Ids.** The ids of moved, unexplained and unresolved pools go to files only. They are never printed, and never joined with decisions or entered mints before the read (§2).
- **(c) Pricing in (B).** Each entered trade's pool is priced at its **V0 at the entry fill slot, with pending from the final map**: stored V = V0(entry) − final-map pending.
  - V0(entry) is the final map's V0 with the pool's LP events after the entry fill slot, up to the final fetch, inverted. The inversion takes the smallest V0_before with floor(V0_before × S_after / S_before) = V0_after.
  - For a pool filled from a snapshot, the anchor is that snapshot's fetch, and events between that fetch and the entry slot are applied forward.
  - An LP event in the entry or exit fill slot counts as before the fill if its transaction precedes the tape row the fill is priced from, and after it otherwise. If that cannot be determined, the trade is priced both ways and takes the lower P&L.
  - An event inside the final fetch's span is computed both ways. If the two results differ, the pool is unresolved.
  - **LP event inside the hold.** A trade with a PumpSwap LP event after its entry fill slot and at or before its exit fill slot is priced at both its entry-slot V0 and its exit-slot V0. **The primary uses the lower P&L of the two, taken separately for `flat` and for `press`, and separately at each k (1, k(p50), k(p90)).** A trade with no exit fill uses its last priced slot as the exit slot. The count of such trades is reported.
  - A trade whose pool is unexplained or unresolved is treated as a **null-V pool** under Amendment 4 §3: the top-3 union, or > 1% of entered trades, gives NOT_DECIDABLE.
- **(c′) Candidate passes (clarified 2026-10-06, pre-read, after quant-proof on #405).**
  - **Entered-set changes.** The entered set of (B) is (A)'s; a candidate pass (pricing at an exit-slot, same-slot or ambiguous-target V0) never adds or removes an entered trade. If a candidate pass would drop an entered trade's entry or move its entry or exit fill slot, that trade's pools are treated as null-V under Amendment 4 §3. A mint entered only in a candidate pass is not added. Neither case ever refuses after STARTED. Both counts are reported.
  - **Candidates are per trade.** A trade's candidate values for a pool are only those from its own entry and exit slots (and their same-slot placements), never another trade's.
  - **Several affected pools.** A trade with more than one affected pool is priced at every combination of its own candidate values, up to 64 combinations, and takes the lower P&L per leg. Above 64 combinations its pools are null-V, and the count is reported.
  - **Every k.** (c) and (c′) apply at every k where (B) or the Amendment 3(a) sensitivity re-score is computed: 1, k(p50) and k(p90), with the lower P&L taken separately per leg and per k. The sensitivity tool must use the same candidate pricing. If that tool change is not merged to `main` with quant-proof OK, its merge commit recorded here, before 2026-10-16T00:00Z, then Amendment 3(a) on (B) is NOT_DECIDABLE and live is not supported. (B)'s own verdict at k = 1 is unaffected.
- **(d) Sensitivity lines.**
  - (B) is recomputed with every LP-moved entered pool priced at its final-map V0 instead of (c). **If the verdict differs from (c)'s, this is listed in `live_blockers`** (the Am.4 §4 pattern); (B)'s verdict stays (c)'s.
  - (B) is also recomputed with pending = 0, report only.
- **(e) Recorded from now on:** every fetch, the FINAL map included, records each pool's LP supply and the fetch's context slots. Snapshots #2 (job #259) and #3 (job #286) lack them; (b) covers them through LP history.
- **(e′) Dry run before the read (added 2026-10-06, pre-read).** Before 2026-10-15T23:00Z, `merge` is dry-run on the ledgered snapshots, with the latest snapshot standing in for the final map. The dry run uses a mode that cannot write a FINAL-usable output. Its inputs are pool fields and LP histories only: no FINAL map, decision, entered-mint, forward-tape outcome or P&L file. Pool ids stay in files.
  - **What it must show:** every snapshot's fetch span binds through its ledger `detail_sha256`, and unexplained plus unresolved pools are at or under the 0.1% ceiling.
  - **What may be fixed if it fails:** only tool or binding defects, and re-running RPC reads that failed under (a)'s retry rule. The rule, the ceiling, the pool set and the snapshots used do not change. An lphist run whose pools were judged and found unexplained is not re-run to change that result.
  - **If it still fails at 10-15T23:00Z:** the failure and its counts are recorded here, and the read proceeds under the rules as written.
  - **Not the (B) run:** the dry run's lphist output is not the (B) run of (a) and is not used by vbook.
  - **Recorded here:** the dry run's merge meta sha256 and its commit.
- **§7 tool commits (recorded 2026-10-06, pre-read, as (0) requires).**
  - #402: LP history, `lphist`/`diffs`, LP-law merge with the ceiling, dry-run mode, LP supply and slots on fetch. Merge `4c5da8c`. Quant-proof OK at `957ea1f`.
  - #405: vbook (c), (c′) and (d), plus `lphist-entered`. Merge `5da3349`. Quant-proof OK at `e9ea702`.
  - DEC text: #400 (`8879b37`), #403 (`2269ad2`), #407 (`49c1033`), #409 (`304dded`).
  - #411: Amendment 3(a) sensitivity on (B), with the §7(c)/(c′) candidate pricing at k(p50) and k(p90), bound to vbook's LP inputs. Merge `3355ad1`. Quant-proof OK at `5601f2f`. **All §7 tool requirements are now met, before 2026-10-16T00:00Z.**
- **Effect.** No bar, threshold, model, window, fail model or trial term changes.
  - (c) moves (B) by the V0 the chain used. Where an LP event falls inside a hold, it takes the worse case.
  - Unexplained or unresolved moves can only make (B) NOT_DECIDABLE.
  - The tool PRs get quant-proof before merge, and their merge commits are recorded here.

## Amendment 6 (2026-10-08): slot-span verify bound

Outcome-blind, written before 2026-10-16T00Z. No forward outcome was read to make this change: no runner row, no scored P&L, no `rows.jsonl`, and no file Amendment 2 keeps closed was opened. The reason is a change in the chain's slot time, not in any result.

**Reason.** Solana slot time is 267.2 ms today, about 13,473 slots per UTC hour. SIMD-0525 (200 ms slots) activated at the start of epoch 1052, and on past steps slot time changed one epoch after activation. So expect about 200-215 ms from epoch 1053 (slot 454,896,000, about 2026-10-09T14:30Z), which is 16,700-18,000 slots per hour. The per-hour verify (`tools.exp012_forward.verify_line`, run by `scripts/research/forward-walk.sh` for every hour) flagged any slot span above 14,000 as `implausible_slot_span`, and the walker refused to seal such an hour (`bad_slot_span`).

**Change.** Only the upper slot-span bound, from 14,000 to 19,500.
- It is now one constant, `tools.backfill_verify.MAX_SLOTS_PER_HOUR`. The per-hour verify (`verify_line`), the `backfill_verify` CLI default and the walker's seal check and CLI default (`tools/pump_history_backfill.py`) all read it. The walker is included because an hour it refuses to seal never reaches the verify.
- 19,500 is 18,000 plus margin (slots down to about 185 ms).
- What the bound is for: it guards against `slot_for_time` boundary errors. The 20-30k values seen in the 2026-09 stats were resume-inflated `slots_done` counters, not spans, and the unchanged `slots_done` above the span check (`resumed: duplicate risk`) catches those.
- The lower bounds do not change: 10,500 for the walker, 9,000 for the verify. See (e).

**(a) Measured state at the amendment.** 135 verified forward hours, 2026-10-02T15 to 2026-10-08T05. Slot spans 13,269-13,545, and none was ever flagged `implausible_slot_span`. All 134 adjacent pairs tile exactly (the next hour's start slot equals this hour's end slot). So no existing hour changes status, and their `verify.jsonl` lines are not rewritten. A span at or below 14,000 gets the same result under both bounds. These are slot-range figures; no outcome was read to get them.

**(b) Scoring code.** No scoring code changes. The scoring inputs listed in (g) change meaning at epoch 1053, inside the window; they are disclosed here before the read. The model, threshold, features, execution, size, both fail models, the window, the read date, the seal and the trial terms are as written. The spent-block scorer `tools/exp012_score.py` keeps its own 9,000 / 14,000 call: that block's hours are September hours of about 13.5k slots.

**(c) What the old bound would have broken.** From the switch on, every hour would have had no OK line, and the read refuses an unverified hour. A second consequence: the runner latency export needs at least 24 clean hours per window day (`tools/exp012_runner_latency_export.py:176-177`), so it would also have gone NOT_DECIDABLE. Hours with a span over 14,000 now get an OK line and so enter the export's `slot_ms` as measured chain data, which (g) D discloses.

**(d) Fallback.** If spans pass 19,500 (slots under about 185 ms) or the step comes late, the bound is raised again only by a new dated, outcome-blind amendment before 2026-10-16T00Z. Until then, unverified hours refuse the read.

**(e) Known weakness: the lower bounds.** The lower bounds weaken in relative terms after the switch. 10,500 (walker) and 9,000 (verify) are 58% and 50% of an 18,000-slot hour, against 78% and 67% of today's 13,473. There is no cross-hour contiguity check in `tools/backfill_verify.py` (`hour_metadata` tests each hour's span alone), so a truncated hour of about 10.5k-16k slots would pass. This is disclosed as a known weakness and is not fixed here. The exact tiling in (a) is a measurement of past hours, not a check.

**(f) Stale figures elsewhere in this DEC.** The 268 ms figure in Amendment 3 (a) and the cost lines in the Decision (about 13.4k credits per hour, about 0.32M per day, about 9.7M per month, an hour sealing in about 28 minutes at `--rps 8`) describe the chain before the switch. After it: about 18k credits per hour, about 0.43M per day (about 13M per month, inside the same budget line), about 37.5 minutes per hour at `--rps 8`. The 268 ms is a lab figure, not an input: the latency export computes `slot_ms` from the window's verified spans. Those earlier sections are not edited.

**(g) Slot-time effects on the FINAL path, disclosed before the read.** Outcome-blind; no computation changes.
- **A. Pressure fail model.** It is per-slot with a frozen intercept: p = sigmoid(-1.4549 + 0.8 * log1p(same_slot_buys) + 0.35 * log1p(nearby SOL)) (`tools/latency_curve.py:39,61-66`; `tools/exploration_exits.py:126,131-132`). `same_slot_buys` counts buys in the entry state's slot (`tools/latency_curve.py:185-198`), so shorter slots mean fewer same-slot buys, a lower fail probability p, and a pressure leg tilted toward profit after the switch. The flat 15% leg is unaffected.
- **B. Frozen model features.** They are not slot-based. `same_slot_buys` and `nearby_buy_sol` were dropped as lookahead (`ARTIFACTS/exp012/features.json`); the rest are time- or count-based.
- **C. k = 1 entries and trigger exits.** A slot+1 entry or a trigger exit means about 200 ms instead of 267 ms after the switch (`tools/exploration_entry_model.py:380-383`; `tools/exploration_exits.py:206-216`). The (A)/(B) books at k = 1 assume a tighter latency budget for about 6.4 of the 10 window days.
- **D. Latency export and the Amendment 3 sensitivity.** The export uses one window-mean `slot_ms` = 3,600,000 / mean span (`tools/exp012_runner_latency_export.py:107-136`) and k_i = 1 + ceil(L_i / slot_ms) (`:139-143`). One k(p50) and one k(p90), in slots, are applied to every trade, and the time-cap lag uses (k - 1) * `slot_ms` (`tools/exp012_forward_sensitivity.py:231,312`). Across the switch this can lean optimistic by up to about one slot. **Pinned rule, written now and outcome-blind:** the Amendment 3 sensitivity reports both the window-mean result (as written) and a conservative variant that uses, for each of k(p50) and k(p90), the worse (larger) of the window-mean k and the post-switch-hours k. The variant does not replace the as-written result. Live support requires the as-written result and the conservative variant to pass. This rule can only remove support, never add it.
- **E. `SLOT_MS = 400`** (`tools/latency_curve.py:98`) is on the FINAL path. In `_delayed`, `t_exit = t_recv + k * 400` (`:270-276`) is used for tape-end censoring (`tools/exploration_exits.py:266-272`); that is inert here, because tape coverage runs to read end + 1 h. It is also the trigger-exit `exit_ms` in the max-concurrent-3 trial book (`tools/exp012_forward_sensitivity.py:44-51`), where hold ends are overstated by k * (400 - `slot_ms`). Disclosed, not changed.
- **F. `SensHours.slot_ms = 268.0`** (`tools/exp012_forward_sensitivity.py:204`) is a dataclass default and is inert on the real window, because `slot_ms` comes from the latency summary (`:561-593`).
- **G. Not affected,** all in milliseconds: the runner's 5 s stale cap (`tools/forward_paper.py:502`), the export's 500 ms block-time offset, the 30-minute exit cap and the 2 s nearby window.
- **H. Report-only, cannot change the verdict.** After the read, report each gate leg split by migrations before versus after the first 200 ms-era slot.

**Tool PR:** `claude/slot-span-200ms`, merged as #454, merge commit `2bd45f1981551693463f370c87438bc4e0874ff8` (2026-10-08). The forward walk was resubmitted on that commit as MiScusi job #382.

**(g) D tool: not planned (manager, 2026-10-08).** No merged tool computes the conservative variant, and the manager does not plan one. If no variant tool is merged and its commit recorded here before 2026-10-16T00:00Z, the variant is not computed and Amendment 3 (a) gives no live support for this window. This only removes support. The read still runs as written, and it will be reported as compromised under Amendment 2.

The 10-16 FINAL remains reported compromised under Amendment 2, and this amendment does not change that.

## Amendment 7 (2026-10-08, before any counted hour): EXP-024 reads forward-1002 only after the FINAL

No forward outcome was read to make this amendment: no runner row, no scored P&L, no `rows.jsonl`, and no file Amendment 2 keeps closed. Nothing in the FINAL's window, read, verdict or seal changes.

- **A second counted reader.** [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) (H5-BOOSTFLOOR, [DEC-023](DEC-023-h5-family.md)) also reads forward-1002 `[2026-10-09T23, 2026-10-16T01)`, with its own tool, **only after the FINAL (A) report is written**. Its counted s0 window is `[2026-10-10T00, 2026-10-16T00)`. Hour 2026-10-09T23 is read for universe membership only. This is disclosed in [EXP-012 Amendment 3](../EXP/EXP-012-migrate-entry-model-refreeze-prereg.md) (DEC-014(a)) and in the ledger.
- **Its own V fetch.** It does not use the (B) V-map files (Amendment 5). It fetches V0 and V(t) by `getTransaction` after the FINAL (EXP-024 §4).
- **Closed files stay closed.** EXP-024 opens none of `OUT/rows.jsonl`, `OUT/scratch/*.jsonl` or a FINAL `report.json` / `report.md`. Its tool checks only the FINAL marker, the entry in the external FINAL ledger `/data/mal/exp012-forward/FINAL_READS.jsonl`.
- **The H5 seal applies** to these hours from this merge: no H5 trigger outcome, fill, exit or P&L is computed, opened or printed before the look (EXP-024 §3). This does not relax Amendment 2 or Amendment 3's seal extension. The one carve-out is EXP-024 §3.1: the real-time observation of the live canary's and the shadow detector's outcomes for pools in Look 1's window. It opens no forward-1002 file.
- **Unchanged.** The FINAL's pre-registered computation, window, tools and verdict. It remains **reported compromised** under Amendment 2. The E1 run and the A11 check keep their order, and EXP-024's Look 1 needs E1.

## Amendment 8 (2026-10-09, before the first hour of forward-1002ev and before any forward value is read): forward-1002ev, a sealed second copy of the last week with event-V

Outcome-blind. To write it, no row, report or scratch file of forward-1002 or forward-1002ev was opened, and no file Amendment 2 keeps closed. Only file lists, sizes, the walker and decoder source, and the repo docs were read. Nothing in the FINAL's window, read, verdict, tools or seal changes.

**Why.** Job #382 walks forward-1002 without `--event-v` and keeps no raw blocks, so its PumpSwap rows carry no virtual quote reserve V. [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) Look 1 reads forward-1002 `[2026-10-09T23, 2026-10-16T01)` and needs V0 and V(t) (Amendment 7 gave it a `getTransaction` fetch at about 2 calls per trade). The event-V decoder exists since #467 (c080cbb). A second getBlock walk of the same hours with that decoder gives V on the tape, and gives Look 1 the same decoder Look 2 (walk 2) uses.

1. **The walk.** `scripts/research/forward-walk-ev.sh` (a MiScusi job on mal-research-0, POSIX sh, resumable) walks `[2026-10-09T00, 2026-10-16T01)` (169 hours; the last hour is 2026-10-16T00) into `/data/mal/blocks/forward-1002ev`, with `--event-v` and `verify --strict-lines` on every hour, a `.nobackup` marker, and one of the four shared Helius flock slots at `--rps 8`. It ends itself after hour 2026-10-16T00 is verified. `START` and `STOP` are pins in the script; any other value is refused. It does not touch forward-1002, job #382 or `forward-walk.sh`.
   - **Hours before 2026-10-09T23** are slack and pool-cache warm-up (the walker keeps a pool-mint cache under `pools/` that fills as CreatePool events pass), and give the hourly integrity check in step 3 an early read of whether the two walks agree. EXP-024 reads none of them (its Hour 2026-10-09T23 rule is unchanged).
   - **Credits.** Cap 3,600,000. Arithmetic (15 h x 13.4k + 154 h x 18k) is about 2.97M; quant-proof estimates about 3.3M. The owner approved about 3.0M; the cap above that is the manager's number, and the job's progress note shows the spend against it each hour. Actual spend is recorded in the PR that closes this walk. No Helius autoscaling credit beyond the owner's approval is assumed.
   - **An hour that is not walked is a bad hour.** If the cap, a refusal (walker exit 3) or a failure stops the walk, the hours not walked are bad hours for the join (step 3), not empty hours.
2. **Sealed exactly as forward-1002 is sealed.** forward-1002ev holds the same chain hours as forward-1002 and is **one more copy of a block that already has its owner**, not a block. Nothing reads its values before the FINAL (A) report is written: no person, agent or job opens, prints, hashes for a purpose other than the integrity check below, joins, or computes anything from its rows or the walker's per-row output. Amendments 2 and 3 (closed files, seal extension) and Amendment 7 (EXP-024 reads only after the FINAL) apply to it word for word. Allowed before the FINAL, as for forward-1002: hour counts (sealed, verified, bad), the walker's and `verify`'s counts, and the `--hash-only` check of step 3. A breach is recorded here, dated, and the FINAL is reported compromised, as Amendment 2 says. The ledger records this in the existing Forward walk row (a second row for the same hours on the same host fails `tools/mal_catalog.py`'s overlap check), and no new reader or owner is created. EXP-024's use of the joined V is inside the read Amendment 7 and EXP-012 Amendment 3 already disclose.
   - **Not a FINAL input.** The FINAL (A) and book (B) read forward-1002 only. No EXP-012 tool opens forward-1002ev, and Amendment 5's (B) V map is unchanged.
3. **The join tool** `tools/forward_v_join.py` carries V from forward-1002ev onto forward-1002 rows, on the raw JSONL, by (slot, signature, event_index). Per hour, in code:
   - **Bad hours.** An hour of either walk is usable only if it is sealed in `checkpoint.json`, has a last OK line in `verify.jsonl` and the file bytes hash to that line's sha256 (ev: no bad lines either). Not walked, not sealed, not verified or changed since: a bad hour. A bad hour's V is never used.
   - **1:1 match.** A pair matches when the key is on both sides once, the venue is equal, and `sol_lamports`, `token_raw`, `quote_reserve` and `base_reserve` are equal.
   - **99.5%.** The 1:1 match rate is matched / max(rows in forward-1002, rows in ev), over all trade rows and over the PumpSwap rows. An hour with either rate below 99.5%, a duplicate key, a bad line or no rows is **refused**: none of its V is used.
   - **md5.** The md5 of the ev rows with the five V keys (`observe.trade_decode.EVENT_V_KEYS`) dropped is compared with the md5 of the forward-1002 rows. It is reported per hour. The decision is the 99.5% rule.
   - **Fallback list.** For a refused or bad hour, every PumpSwap row of the forward-1002 hour; for a usable hour, each PumpSwap row that did not match, differed in a field, or matched an ev row with no V. One line per row: hour, slot, signature, event_index, why. These are the rows whose V is rebuilt by `getTransaction` (EXP-024 Amendment 1).
   - **The seal is in code.** `check --hash-only` is the only mode that runs before the FINAL. It prints, per hour, the md5 verdict, row counts, the two rates, the decision and a reason from a fixed list; no row value, signature or pool id, and it writes no file. `check` without it, and `join`, refuse unless the EXP-012 FINAL marker is in the external FINAL ledger. There is no flag that skips this. Outputs go to new files outside both walk dirs and are never overwritten.
   - **A hash-only result may trigger a re-walk of an ev hour** (a manager decision, recorded here with the hour and the reason code). It may not trigger any read.
4. **The decoder is pinned, and it is walk 2's.** The walk, and walk 2 (`forward-walk2.sh`), run `python -m tools.pump_history_backfill ... --event-v` on the job's checkout. The decoder is three files, pinned by git blob sha (main at a3e923c): `observe/trade_decode.py` `238942a6b3c5425389eddfde4d11268c300acbec`, `observe/trade_store.py` `ea4e11eddf9f034e3bc7318ce8743337d753f350`, `tools/pump_history_backfill.py` `9a8bebb32adcf86de060b55f5a08110d11c0a550`. The join tool refuses to run if its checkout differs (`python3 -m tools.forward_v_join pins`). **Precondition for walk 2:** the walk-2 job's git ref has the same three blobs (`pins` exits 0 there). If a decoder change must land first, a dated amendment says so before Look 2 reads, and says what Look 1 used.
5. **EXP-025.** Per the manager, EXP-025 (PR #501, branch `claude/exp025-c1nf-prereg`) will cite this same source, forward-1002ev through the join tool, in its P6. That branch is not edited here. This amendment does not register EXP-025 as a reader: its own pre-registration and ledger edit do, and until they merge no EXP-025 process reads forward-1002ev values.
6. **Unchanged.** The FINAL's window, clock, model, tools and verdict (it remains **reported compromised** under Amendment 2); Amendment 3's seal extension; Amendment 5's (B) map; Amendment 6; Amendment 7's reader, window and order. The per-print source order for EXP-024 is in its Amendment 1.
