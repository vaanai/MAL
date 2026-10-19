# DEC-031: Forward paper arena lane (many frozen bots on one live paper tape, each screened on the promotion gate)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-18; revision 2 applies quant-proof's edits A to K (PR #706 comment 6245150476).** It takes effect on merge, after quant-proof's OK on the exact final head and a reviewer verdict on the PR. Nothing here allows a live send. |
| **Decider** | Vaan (owner) set the direction in chat at about 03:00Z on 2026-10-18: "Start to come up with a plan, and then execute on it"; more SOL after "something like 5 days of forward testing before going live". The Claude manager runs the lane. |
| **Date** | 2026-10-18 |
| **Builds on** | CLAUDE.md promotion gate; [DEC-014](DEC-014-holdout-ledger-and-multiplicity.md) (b) and Amendments 1 and 2; [DEC-021](DEC-021-champion-challenger.md) line 54 and Amendment 7; [DEC-030](DEC-030-walk2-push-slot.md) §1, §1a, §2 R1 and §3; [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md) (Forward walk 2 row); [EXP-024](../EXP/EXP-024-h5-boostfloor-part1-prereg.md) §3 and Amendments 2, 4, 8 and 9; [EXP-025](../EXP/EXP-025-c1nf-part1-prereg.md) §4; [EXP-027](../EXP/EXP-027-r4b-walk2-prereg.md) §0.5 and §11; plan `/data/mal/hunt-1008/push-1016/hunt20/PLAN.md` |
| **Does not amend** | The promotion gate and its thresholds. EXP-027: its window, stop rule, k, Window record and seals. DEC-030. EXP-024 and EXP-025 seals. The ledger's Forward walk and Forward walk 2 owners, hours and domain seals D0 to D4. |

## 0. Why this exists
Up to now each candidate has been certified one at a time on a sealed block. Edges are thin against their variance, so each read takes 2 to 3 weeks, and few lines are tested at once. This DEC adds a second lane. Many bots trade forward on paper at the same time from one live tape. Each bot is frozen before its own clock starts and read once, on the unchanged gate. A first pass is a screen, not a promotion (§2): scaling above a canary needs a corrected confirmation on later trades. The lane does not replace sealed reads.

## 1. The lane
1. **Tape.** One live tape, `/data/mal/arena/tape`, written by `tools/arena_feed.py` from public RPC. It covers PumpSwap and pump.fun events and spends 0 Helius credits. It is new data with its own path. It is not Forward walk 2 data. No arena process opens `/data/mal/blocks/forward-1016` or any path the ledger or the manager's seal list keeps closed. A book may read another live feed instead of this tape (for example an arena H5 book on mal-fast-0 reading its own public-RPC logsSubscribe stream through `tools/h5_shadow.py`, with the tip union off). Its `bots.json` line then names that feed and its pinned decoder blobs; that feed's own gap log (for `tools/h5_shadow.py`, its `gap` records) is its §1.4 gap source; and its parity set is in that feed's format. The H5 books run on mal-fast-0, a different host and filesystem from the walk-2 walker, and write their own small output there (§3.8, "unless it writes to a different filesystem").
2. **Book.** A book is a `bot_id`, a `frozen_sha` (the commit of all code and prompts it runs) and a `clock_start_ms`.
   - Before `clock_start_ms`, the book's line goes in `/data/mal/arena/bots.json` and is copied to a MiScusi notebook entry. The line holds: `bot_id`, `frozen_sha`, `clock_start_ms`, the deciding stake of §2, and the exact model IDs and sampling settings of any LLM it calls. `frozen_sha` is a commit on origin. A records PR within 24 h copies every line. No line is ever removed: a stopped book stays listed with its stop instant and counts in every multiplicity disclosure.
   - A counted decision is written live, at its decision instant and before its fill is priced. It is never generated, regenerated or replayed afterwards. Each book's decision-log sha256 goes into a MiScusi notebook entry at least once per UTC day.
   - Only trades with entry decision time at or after `clock_start_ms` count. Any code or prompt change makes a new `bot_id` with a new clock. The old book keeps running or stops; it is never merged with the new one.
3. **Self-improving books.** For a book whose own reviewer rewrites a playbook (Haiku trader v4):
   - the frozen part is the code, the reviewer's instructions and the model IDs;
   - each daily playbook version is hashed and logged before the first decision that uses it;
   - a version may use only outcomes whose exit is before its write instant;
   - the book is the whole system, versions included.

   Only the book's own reviewer, at the frozen code, instructions and model IDs, writes playbook versions. Any other edit makes a new `bot_id`.
4. **Costs and fills.** `tools/arena_paper.py` prices fills at the lab's deciding stack: 505,000 lamports per send, rent 2,039,280 lamports per filled trip, +26.08/−16 bps, PRE-trade PumpSwap reserves with V, fee tiers, own impact, flat 15% and pressure-fail legs, and 1.3 s / 1.9 s latency legs. It must match the stack to the lamport on a committed parity set before any book's clock starts.
   - **E1 floor.** Per filled trip, the stack also carries the larger of the haircut and 384,022 lamports per 0.05 SOL of stake. This is the pre-seal n = 8 live-minus-sim figure (DEC-021:54), scaled linearly as in DEC-021 Amendment 7 option B.
   - **Exit lag:** ceil(0.55 s / slot_s) slots, at least 2.
   - **Strand class:** an unpriced or invalid exit, or proceeds below one send, is a total loss of stake plus the buy send and rent. It is never dropped.
   - **Latency start:** the latency legs start at the decision instant (when the decision record is written, mapped to the tape's slot clock), never at the triggering print.
   - **Feed gaps:** a trade whose decision-to-exit span holds a feed gap the feed logged (a disconnect or resubscribe), or a print the stack needs without V, is excluded by that rule alone and counted. If more than 5% of a book's trades are excluded this way at its read, the read is NOT_DECIDABLE.
   - **Pins:** these go in the records PR before the first clock:
     - the parity reference (module path@sha256);
     - the parity set (exploration-pool hours in the arena tape's format, path@sha256);
     - the sha256 of `tools/arena_feed.py`, `tools/arena_paper.py` and `tools/arena_score.py`.

     Any later change to them is disclosed in every read it touches, with quant-proof's OK on the change.
5. **Stakes.** One book's decisions are also priced at 0.25, 0.5 and 1.0 SOL (H5: 0.10, 0.25, 0.30), with impact at each size. Only the stake pinned in `bots.json` decides (§2).

## 2. Gate read
- **One read per `bot_id`, at an outcome-blind instant.**
  - **When:** the first 00:00Z boundary at which the book has at least 100 counted attempts (every entry decision, guarded reverts included) over at least 5 UTC dates. The count uses decision timestamps only, by a tool that loads no fill, exit or P&L.
  - **What it covers:** the attempts decided before that boundary, once all have exited. Nothing else starts, delays or repeats the read. A FAIL or NOT_DECIDABLE is final for that `bot_id`.
  - **The test:** CLAUDE.md's promotion gate, unchanged:
    - a majority of days positive;
    - the CI90 lower bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile);
    - total positive after removing the top 3 trades;
    - all of this under both the flat 15% and the pressure-fail model.
  - DEC-014 Amendments 1 and 2 apply: an unresolved restart orphan, a failed settlement, or a counted trade without its pressure-leg P&L makes the read NOT_DECIDABLE.
- **Deciding cell:** the 1.9 s leg at the stake pinned in `bots.json` before the clock (§1.2). The 1.3 s leg and the other stakes are reported beside it and never decide.
- Ex-best-day is reported. A book whose total is not positive ex-best-day is not proposed for a canary.
- **Multiplicity.** Every read states the number of arena books in `bots.json` with a clock before the read (stopped ones included), their cells, and how many were read. A first pass is a **screen**: the best of that many, in-sample for selection, not a promotion. It allows only steps 1 to 3 below, with the canary an execution measurement at its DEC's small limits.
- **A pass is not a profit claim.** The path after a screen pass:
  1. quant-proof checks the read on its exact files;
  2. the owner's OK;
  3. a live canary under its own DEC, with small limits;
  4. **stake above the canary's limits** needs a **confirmation**. The same `bot_id`, unchanged, must clear the same gate on its deciding cell over attempts decided after the screen read's instant: at least 100 attempts on at least 5 further UTC dates, read once at the outcome-blind instant above. **And** its one-sided bootstrap p (10,000 draws, seed 1; the larger p of the two fail models) must be at most 0.05/c. Here c is the number of arena books that have entered a confirmation under this DEC by that read, failed and NOT_DECIDABLE ones included (DEC-014 (b); Bonferroni for staggered reads, as in DEC-030 §1). Live fills must also match paper. Parity is required in addition to the confirmation and never replaces it.
- No retune on a book's own counted days. A retuned rule is a new book.

## 3. Isolation from EXP-027 and the sealed reads
1. Until EXP-027's read is written, no arena book uses, and no arena process opens, any of these:
   - the r4-b boosters (`models/r4b/`);
   - any model fitted on the 63 features of r4-b's `features.txt` (the S1 / l3 LightGBM line, any seed or variant);
   - `tools/r4b_*.py` and `02_build.py`;
   - `/data/mal/r4b/`;
   - r4-b's development outputs under `/data/mal/hunt-1008/laya-opt/`.
2. Arena results never change anything in EXP-027. Its window, stop rule, k and read are fixed by its registration.
3. Arena books are not push-family books. They take no DEC-030 slot and do not change EXP-027's k. Arena results are walk-2-hour information under DEC-030 §2 R1 ("a book's design must not use them"). No book designed, tuned or selected with any arena result registers as a push-family book on walk 2.
4. After EXP-027's read, the manager records the following in the ledger's EXP-027 sub-entry and beside the read's report, never in EXP-027's file (its Window record takes only its closed key list, EXP-027 §0.5):
   - the arena's existence;
   - the number of arena books;
   - the count of arena trades, paper and any live canary send, on pools that r4-b held or attempted in the same hour. The count is computed after the read.
5. **H5 seals.**
   - The CAP-PICK seal is moot. EXP-022 was withdrawn before counting on 2026-10-15, which is an end state, so EXP-022's read has ended (EXP-024 §3, "to the end of EXP-022's read").
   - The H5 Look-2 seal is not moot. EXP-024 §3's seal and Amendment 4 item D1 stay in force until EXP-024's end state, the first listed being 2026-11-14T00:00Z (EXP-024 A3 halt record; DEC-030 §3). They cover H5 outcomes on any October hour of a V-range pool in its first 360 s, from any source, RPC and paper runners included.
   - **v1 is covered by EXP-024 Amendment 2.** An arena H5 v1 book whose decisions come from `tools/h5_shadow.py`'s decision path (md5 decision-equivalence on the default path), run keyless with no orders, is the shadow detector of Amendment 2. Its outcomes for pools with s0 in `[2026-10-16T00, 2026-11-06T00)` are under that declared observation.
   - **v2 is not covered by Amendment 2** (EXP-024 Amendment 8 (e)). An arena v2 book counts only pools with s0 at or after 2026-11-06T00, unless a dated EXP-024 record declaring the real-time observation of its outcomes merges with quant-proof's OK before its clock starts. EXP-024 Amendment 9, proposed in the same PR as this DEC, is that record.
   - **Synthetic pools.** Before 2026-11-14T00:00Z no arena H5 record carries an outcome for a synthetic pool or splits an outcome by class. The book's universe excludes synthetic and unclassified pools before any outcome exists, as EXP-024 Amendment 4 A2 and B3 do (Amendment 4 A3 and D1).
   - An arena H5 book counts only pools with s0 at or after its clock start. It never opens the outputs of shadow job #817 or the live canary.
6. The domain seals D2, D3 and D4 of the Forward walk row cover the hours `[2026-10-02T10, 2026-10-16T01)` and the tip tape. The arena opens no hour of `[2026-10-02T10, 2026-10-16T01)` from any source, the tip tape included; a book that reads another live feed uses only data from its own clock on.
7. **C1-NF (EXP-025).** EXP-025 §4's seal covers C1-NF labels, fills, exits and P&L on any October row from any source, RPC and paper runners included. The seal on `[2026-10-10T00, 2026-10-24T02)` holds until EXP-025's end state "Look 2 refused" is recorded on main, which is not before the R8 run after 2026-10-26T12:00:00Z (EXP-025 P5 record). Until then:
   - no arena book is, or reproduces, the C1-NF book or its pass-A labels (C1 `RULE.md`, `ARTIFACTS/c1nf_model/`);
   - no arena process opens the outputs of jobs #658 or #662.
8. **Walk 2 keeps its room.** No arena write takes free space on the walk-2 walker's filesystem below 25%. The walker stops below its 20% reserve, and PLAN.md stream E records walk 2 stalled for that reason. The feed checks before each hourly write and stops, logging a gap, rather than write below 25%, unless it writes to a different filesystem.

## 4. Not allowed under this DEC
- Any live send.
- Any read of a sealed path.
- Any change to a running job's command.
- Counting a trade decided before its book's clock.
- Opening the outputs of jobs #817, #729, #658 or #662, or of the H5 executor.

## 5. Amendments

### Amendment 1 (written 2026-10-19T03:31:35Z, after the H5 books' clock of 2026-10-18T06:00:00Z and before any read instant)
DEC-031 Amendment 1 (before any H5 arena read instant; outcome-blind). For h5v1-boostfloor-a1 and h5v2-boostclock-a1, a pv trigger record for which `tools.h5_executor.parse_shadow_trigger(row, "pv", None)` at blob 03995837317f returns a `bad_intent` refusal is the frozen bot's own pre-send refusal. It is not an entry decision. It is not an attempt for §2's count or read instant, and it is never priced. Its count by reason is reported beside every count and the read. A `bad_intent:missing_*` result is a schema fault and stops the scorer. Every other exclusion stays under the 5% cap, computed over non-refused attempts. Only the counts of jobs #1175 and #1176 were in view. No arena H5 outcome, fill, exit, strip, ledger row or P&L was opened. Code, frozen sha, clock, window, stake, deciding cell and gate are unchanged. `tools/arena_h5_score.py` changes from sha256 a3dd6b31c38081b535cb965223623ae5797341b7374b0d3b3196799e79e65c27 to dfe7f018601633921f06668fd1af3aaff7b87bf00f321d1540e10b41f8ab6006.
