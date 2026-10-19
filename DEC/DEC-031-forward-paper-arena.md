# DEC-031: Forward paper arena lane (many frozen bots on one live paper tape, each screened on the promotion gate)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-18; revision 2 applies quant-proof's edits A to K (PR #706 comment 6245150476).** It takes effect on merge, after quant-proof's OK on the exact final head and a reviewer verdict on the PR. Nothing here allows a live send. Amendments 1 and 2 are in §5; Amendment 2 sets the deciding rent of §1.4 to 0 for the books it covers. |
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

Disclosure (added before merge). After the paragraph above was written at 2026-10-19T03:31:35Z, and before it merged, the counts-only dry run of job #1191 (2026-10-19T03:43:26Z, scorer at 49fd6c5, the quant-proof pre-merge condition) was seen. For each book it showed 23 triggers seen, 12 refused (all `bad_intent:base_breaks_unresolved_settled`), 11 attempts, 11 counted, 0 excluded, 0 open, and not_decidable false. The merge was decided with those counts in view. No rule text changed after them, and no arena H5 outcome, fill, exit, strip, ledger row or P&L was opened. The disclosure instant in the read sentence stays 2026-10-19T03:31:35Z, the instant the rule was fixed.

Bias note. Missed prints rise with print count and feed load, so the kept (non-refused) set tilts toward quieter pools and quieter periods; the sign of that effect on P&L is unknown. The refusal share also moved in the counts seen: 9 of 13 triggers in the first count, then 3 of the next 10. With `tipauth=None` pinned, any integer `base_breaks_unresolved_settled` of 1 or more is a refusal.

### Amendment 2 (written 2026-10-19T17:27:15Z from `date -u` on mal-research-0; revised 2026-10-19T17:40:45Z after the quant-proof and reviewer comments on PR #740 at 94e2ed6; before the clock of any book it covers and before any arena read instant; outcome-blind)
DEC-031 Amendment 2: the arena's deciding rent is refunded. For the books in item 2 it is 0 lamports on every trip whose leg state is `filled` (the selector of item 7). This turns quant-proof's ruling R1 of 2026-10-19 into DEC text and does not change the ruling's substance. Where quant-proof's comment 6273356030 on PR #740 corrects the ruling's own R1 (e), (f), (g) and (h) wording, this text follows the comment (items 1, 6, 7, 8 and 10).
- Ruling, pinned in the repo: `ARTIFACTS/lab/qp-ruling-1019-cohort2.md` (sha256 `2288b71cbf1c315c46b6f1f111a1a3e6aa2972a27d9c093a79113a0cdc423ea9`). It is `/data/mal/hunt-1008/push-1016/hunt21/QP-RULING-1019-cohort2.md` as last modified 2026-10-19T17:36:28Z (sha256 `1583c4d7a8b8194bd60356dd027bbc436d56c8250617a1a17543b41dd31cb8cc`) with a header note added. The `/data` file is mutable; the repo copy is the cited text.
- The version quant-proof checked at 94e2ed6 had sha256 `c6f0a5e3ed022b6aa319238738e97a245d681de7daa138bb08abad342dbefe81` (mtime 2026-10-19T17:23:17Z). R1 and R8 item 7 were re-read in the later version and are unchanged. Sections R9 and R9(f) are not used here.
- Items 1 to 11 carry its R1 conditions (a) and (c) to (h) and its R8 item 7; item 12 carries condition (b); item 13 carries its basis.
- Like the rest of DEC-031 it takes effect on merge, after quant-proof's OK on the exact final head and a reviewer verdict on the PR, and it must merge before the first clock it touches. A config ruling alone is not enough: `tools/arena_paper.py`'s docstring cannot override merged DEC text, and §1.4 names 2,039,280 lamports per filled trip.

1. **What changes.** §1.4 prices rent at 2,039,280 lamports per filled trip "at the lab's deciding stack". For a covered book the deciding rent is 0 on every trip in state `filled` (item 7). Everything else in §1.4 is unchanged: 505,000 lamports per send, +26.08/-16 bps, PRE-trade PumpSwap reserves with V, fee tiers, own impact, the flat 15% and pressure-fail legs, the latency legs, exit lag, the E1 floor (the larger of the haircut and 384,022 lamports per 0.05 SOL of stake, a max and not a sum), the strand class and the feed-gap rule.
2. **Scope.** The amendment covers every book whose `clock_start_ms` and whose registration (§1.2: its `bots.json` line and notebook copy) are both after the merge. The merge instant is the committer time of the squash-merge commit on origin/main. The rule is uniform. No book chooses its rent.
   - **HV4** is covered only if, before its registration, both of these hold:
     - (i) its host, `tools/hv4_runner.py`, passes `rent=0` explicitly. At origin/main 77bdf7e, line 1595 builds `stack_config(feed_delay_slots=...)` with the default, so HV4 is rent-charged as built;
     - (ii) its frozen code enforces item 6's pool filter. At 77bdf7e, `tools/hv4_cards.py` `filters` (lines 399-400) checks PumpSwap and a WSOL quote only and has no V band, so a rent-0 HV4 as built could trade non-canonical WSOL pools.
   - Coverage is tied to HV4's registered `clock_start_ms`: that and its registration must both be after the merge. T0 is not fixed here. R1 named 2026-10-21T00:00Z, which is only the earliest. T0 is the first 00:00Z after a clean 24 h stub run at the final sha and the registration merge, and was expected to be 2026-10-22T00:00Z when this text was revised.
   - Items 5 to 9 apply to HV4 like any covered book.
   - (i) and (ii) are code changes before HV4's freeze and are not part of this amendment's PR. If either does not happen, HV4 decides at rent-charged as built and this amendment covers the cohort-2 books only.
3. **Where rent 0 is set.** Each covered host passes `rent=0` in its `StackConfig`. `tools/arena_paper.py`'s default (`RENT_LAMPORTS`, 2,039,280) is unchanged, and so is its sha256, which is a §1.4 pin.
4. **Strand rent stays 2,039,280.** A strand (an unpriced or invalid exit, or proceeds below one send) still loses stake, the buy send and 2,039,280 (`F.RENT_STRANDED`), and is never dropped. It is not re-based down to chain rent. Disclosed: 2,039,280 is 34,800 below EXP-027's Token-2022 convention (2,074,080) and 525,440 above the chain's 1,513,840 (2026-10-16, 170-byte Token-2022 ATA). One-off accounts such as the PumpSwap `user_volume_accumulator` are paid once per wallet, not per trip. They are disclosed here and are not charged per trip.
5. **Canary carry-over (EXP-027 condition (i) mirrored).** A live canary from a rent-0 book must sell through a builder that sells and closes the base ATA in one transaction under the mint's token program, pinned as path@sha256 (today `tools/h5_executor.py` and `tools/probe_live.py` do). Otherwise the rent-charged leg of item 7 is the paper comparator for that canary.
6. **Pool universe and close-blocking class (EXP-027 (iii) and item 4 mirrored).**
   - **Universe.** A covered book trades only canonical pump pools: quote_mint == WSOL (read before the V test) and V in [17.5e9, 17.7e9] on the deciding row (QP ruling R2 item 3 and R9 (b)). Each covered book's own frozen code enforces this at decision time, so a covered book has no out-of-band trip to price. For the cohort-2 books it is a registration precondition. The records PR names the check as path:line@frozen_sha.
   - **Fetch.** After the read boundary, one structure-only mint-account fetch of the book's traded mints is run. It records counts, and the credits it uses are recorded.
   - **Retries.** A transport failure is retried up to 3 times, all within 24 h of the first attempt, by the same rule for every mint. A mint that still fails then is a failed fetch. The fetch reads current chain state, so a freeze authority revoked after the trade is missed. This is disclosed, not corrected.
   - **Close-blocked trip.** A freeze authority, an extension outside {metadataPointer, tokenMetadata}, or a failed fetch makes that trip close-blocked in every sub-cell. It is priced the way `arena_stack.round_trip` prices a caller-stranded trip (`stranded=True`): pnl_nofail = -stake - send - 2,039,280. Under "total" it bypasses the fail blend. Under "sc" it is blended the way "sc" blends `entry_invalid` and `exit_invalid` rows. Its `filled` flag stays True, so p_i and the pressure refit do not change. The reclassification is applied before item 7, and a close-blocked trip leaves state `filled`.
   - This is pre-declared here. Census prior (EXP-027 allowlist census): 295 of 300 Token-2022 with exactly those two extensions, 5 SPL, 0 freeze.
7. **Rent-charged leg beside every deciding figure.** Every read of a covered book prints the rent-charged leg beside each deciding figure, as an exact transform of the same rows. The selector is the leg state, not the `filled` and `strand` flags: a trip i is transformed under a strand rule only if `calc[lat].by_rule[rule].state == "filled"`, after item 6's reclassification. For those trips:
   - flat leg: minus 0.85 x 2,039,280 lamports;
   - pressure leg: minus (1 - p_i) x 2,039,280 lamports, with p_i the cell's own refit probability for that trip.

   Rows in state `guarded`, `stranded`, `entry_invalid` or `exit_invalid` are unchanged under both rules. Under "sc", `entry_invalid` and `exit_invalid` rows have `filled` True and `strand` False, but their pnl already carries 2,039,280 of strand rent whatever `cfg.rent` is, so selecting on the flags would charge them twice. The transform is computed with a pinned report tool, or with a scorer change that carries quant-proof's OK and has its sha256 disclosed (§1.4 pins). Any pass sentence quotes it. A book that passes only at rent 0 is described in those words.
8. **Config pin and read check.** The records PR pins each covered book's `StackConfig`: `rent` 0, `e1_floor_per_005` 384022, `strand_rule` "both", `k_rounding`, `feed_delay_slots`, `gap_slots` 150 and `gap_silence_ms` 60000.
   - **Check.** The read compares every key of `StackConfig.as_dict()` on every engine start row and every open row with the pin. It is counts-only and runs before any outcome of the book is opened. Per-trade `sps_entry` and `sps_exit` on open rows are ignored, as `CONFIG_KEYS_IGNORED` ignores them. A mismatch on any pinned field makes the read NOT_DECIDABLE, and that is final for the `bot_id`. Nothing is re-priced, and item 7's transform is not used to fall back to the pin. `tools/arena_score.py` refuses only MIXED configs inside a bot (lines 300-302) and never checks values, so without this check a wrong config would pass silently.
   - **The two gap fields.** R1 (g) implied that `as_dict()` emits `gap_slots` and `gap_silence_ms`. At 77bdf7e it does not (it emits `rent`, `e1_floor_per_005`, `strand_rule`, `k_rounding` and `feed_delay_slots`, among others), as quant-proof's comment 6273356030 states. Their pin is therefore static, from the host's `StackConfig` call at the host's pinned frozen sha. It binds only if all three of these hold:
     - (i) the scorer ties every counted open row to a clean engine start at the frozen commit (`tools/arena_score.py` lines 287-296);
     - (ii) `StackConfig` is frozen (`frozen=True`);
     - (iii) no runtime input (CLI flag, env var, config file, `**kw` passthrough) reaches either field.
   - **Records PR.** It states (iii). It also states either that the host's `StackConfig` call at frozen_sha passes neither field (the defaults of 150 and 60,000 in `tools/arena_paper.py` apply, at its §1.4-pinned sha256) or that it passes them as literals. If a runtime input reaches either field, the book is not registered until the engine logs the effective values.
9. **Read statement (QP R8 item 7).** Every arena read, covered or not, states the rent-charged and rent-0 legs side by side, which one decides, and why. For a covered book, rent 0 decides and the reason is this amendment. For an H5 book, rent-charged decides (item 10).
10. **H5 books are unchanged.** h5v1-boostfloor-a1 and h5v2-boostclock-a1 (clock 2026-10-18T06:00:00Z) keep deciding rent 2,039,280 (`tools/arena_h5_cost.py` `RENT`). Every H5 read discloses this amendment. It may print a rent-0 leg: +0.85 x 2,039,280 lamports on the flat leg and +(1 - p_i) x 2,039,280 lamports on the pressure leg, on exactly the trips item 7's selector picks (`by_rule[rule].state == "filled"`), labelled "report-only, added after the clock, never deciding". The H5 leg must not credit `entry_invalid` or `exit_invalid` rows under "sc". A post-clock switch in the known-favourable direction is a researcher degree of freedom. That is why it is not allowed, even though rent 0 is the truer cost.
11. **No retroactive switch.** If this amendment does not merge before a book's clock or registration, that book decides at rent-charged as built.
12. **Outcome-blind statement (QP R1 (b)).** The shift is deterministic and per trade, and the ruling rests on code and pre-arena evidence. R1 (b) asks which arena, HV4-shakedown or replay P&L figures the chooser had in view.
    - **The manager's statement**, recorded in MiScusi notebook entry n_x_CjdirQZfaC5w, with the supplement entry n_bg2-0svirXsC_g that the manager added to the notebook after the quant-proof comment:
      - the manager has seen no arena P&L of any book;
      - H5 arena books: counts only (dry-run counts; no fills or P&L opened);
      - the Haiku v4 shakedowns ran the StubModel (every answer abstains, so no trades), and the manager read cards, decision-log and timing counts only, never the engine ledger;
      - the cohort-2 scope and the H2 split used Aug/Sep exploration outputs priced at the old 55,000-lamport stack, not arena outputs;
      - the ruling rests on code (`tools/h5_executor.py:2524-2525`, `tools/probe_live.py:759-760`) and pre-arena evidence (EXP-027 §5.2 / QP-FU §1a, 2026-10-16).
    - **Replay P&L figures in view of the chooser when choosing:**
      - `COHORT2-SCOPE.md` (`/data/mal/hunt-1008/push-1016/hunt21/`, sha256 `3b9ef3182f972d3dc2184295423dce59894b550dfd893bd1071f8c61d8432190`). It holds Aug/Sep replay figures at the 55,000-lamport, no-rent stack and the scope's own estimates at the arena stack. Its line 6: "Every non-H5 survivor was +1.6% to +4.5% at 55,000. They land near zero or below" (extra drag "about 1.9 pp of stake at 0.25 SOL"). Its table: H2-PS "about 0 to +0.5%", G4 "about 0.0 / -0.3 after drag" (both "my arithmetic" in the scope).
      - `COUNTS-1019.md` (same directory, sha256 `0db2ccafc31a5a9794591b652170ab52c33a45bfcb19a457a4daceeb11cfc368`, generated 2026-10-19T17:18Z). Its §B1 holds Book A's September P&L by venue, including the PumpSwap subset in h2ps-a1's own deciding cell (1.9 s, 0.25 SOL): 528 attempts, +1.70% flat and +1.25% pressure per attempt, on the 55,000-lamport no-rent stack, in-sample. It was in view of the chooser. Quant-proof did not have it for R1.
    - **Direction.** The switch was adopted knowing that it favours the books about to be registered. The rent term is 0.816 pp of stake at 0.25 SOL, which is about 0.8 of the scope's ~1.9 pp extra arena drag at 0.25 (quant-proof's check of the scope's arithmetic, not a measurement), and it is about what decides the sign of their Aug/Sep figures at the arena stack. The ruling stands because the switch is uniform, comes before every covered clock and registration, and rests on code. Item 7's rent-charged leg is the guard.
    - The ruling's own header adds that quant-proof opened no arena tape, ledger, `bots.json`, shakedown directory, sealed path, tip tape or shadow, canary, probe or walk output, and that its probe figures (61 of 61 refunds, 1,513,840 chain rent) come from EXP-027 §5.2's pinned text.
13. **Basis (code and pre-arena evidence only).**
    - Both live sell paths put the sell and the base-ATA close in one transaction under the mint's own token program: `tools/h5_executor.py:2524-2525` and `tools/probe_live.py:759-760`. `tools/pumpswap_tx.sell_instructions` on its own closes only the WSOL ATA, so a builder that used it without the extra close would leave the base-ATA rent locked. Item 5 guards that.
    - The close is atomic with the sell. If it cannot run (tokens left behind, a close-blocking extension), the whole sell reverts and the position sticks. That is strand behaviour, not rent lost on a good sell.
    - Probe evidence: 61 of 61 probe trips refunded, net 0 (EXP-027 §5.2, QP-FU §1a). Chain rent on 2026-10-16 was 1,513,840, so 2,039,280 is 35% above what the chain charges even when rent is lost.
    - The lab's other deciding stack already prices rent at 0 (`arena_stack.RENT_DECIDING`, vendored from `r4b_frozen`; `EXP027_RENT_DECIDING` under EXP-027 §5.2 (i) to (iii)). §1.4 says "at the lab's deciding stack" and then lists 2,039,280; the two statements disagreed.
    - The E1 floor absorbs no rent in either direction. `probe_sim_calibration.pnl_of` builds the sim P&L as proceeds minus spend minus the live fees of both sides minus the failed-sell cost minus the same trip's net rent (rent charged minus rent refunded), and the live P&L contains that same net rent. Rent and fees cancel in live minus sim by construction, so the 384,022 per 0.05 SOL gap (n = 8) has no rent in it. Rent 0 drops no cost that E1 absorbed, and rent-charged does not double count with E1. The floor and the haircut stay a max.
    - Rent changes no other term of the deciding stack. The auto-strand threshold (proceeds below one send), the E1 extra (pnl0 minus pnl, where rent cancels) and the pressure refit (ssb, nearby, filled) do not depend on it. With item 7's selector, that makes the transform exact.
    - Size, quant-proof's arithmetic and not a measurement: 2,039,280 lamports per filled trip is 0.816% of stake at 0.25 SOL, 0.408% at 0.5 and 0.204% at 1.0. That is about 0.8 of the scope's ~1.9 pp extra arena drag at 0.25. Keeping a cost the executor does not pay is a mislabelled haircut. Margin the lab wants belongs in a named haircut, not in rent.
    - What rent 0 must not hide: a strand (item 4), a close-blocking mint (item 6), a canary through a builder without the in-tx close (item 5) and one-off accounts (item 4).

Unchanged by this amendment: the promotion gate and its thresholds; §2's deciding cell (1.9 s at the stake pinned in `bots.json`, flat and pressure, strand rules sc and total as in the ruling's R8 item 2) and its one read per `bot_id`; the E1 floor; the H5 books; every clock; §3; Amendment 1. A covered book that passes is still a screen (§2), not a profit claim.
