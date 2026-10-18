# DEC-031: Forward paper arena lane (many frozen bots on one live paper tape, each scored on the promotion gate)

| Field | Value |
| --- | --- |
| **Status** | **Proposed 2026-10-18.** It takes effect on merge, after quant-proof's OK on the exact final head and a reviewer verdict on the PR. Nothing here allows a live send. |
| **Decider** | Vaan (owner) set the direction on 2026-10-18 at about 03:00Z, in chat. The owner's words: "Start to come up with a plan, and then execute on it"; more SOL is possible after "something like 5 days of forward testing before going live". The Claude manager runs the lane. |
| **Date** | 2026-10-18 |
| **Builds on** | CLAUDE.md promotion gate; [DEC-014](DEC-014-holdout-ledger-and-multiplicity.md); [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md) (Forward walk 2 row); [EXP-027](../EXP/EXP-027-r4b-walk2-prereg.md); plan `/data/mal/hunt-1008/push-1016/hunt20/PLAN.md` |
| **Does not amend** | The promotion gate and its thresholds. EXP-027, including its window, stop rule, k and seals. DEC-030. The ledger's Forward walk and Forward walk 2 rows, and their domain seals D0–D4. |

## 0. Why this exists
Up to now each candidate has been certified one at a time on a sealed block. Edges are thin against their variance, so each read takes 2–3 weeks, and only a few lines are tested at once. This DEC adds a second lane. Many bots trade forward on paper at the same time from one live tape. Each bot is frozen before its own clock starts, and each is scored on the unchanged gate as soon as it has enough trades. A forward window that starts after a bot is frozen is out-of-sample by construction. The lane does not replace sealed reads. It adds breadth and speed.

## 1. The lane
1. **Tape.** One live tape, `/data/mal/arena/tape`, written by `tools/arena_feed.py` on mal-research-0 from public RPC. It has PumpSwap and pump.fun events and spends 0 Helius credits. The tape is new data with its own path. It is not Forward walk 2 data. No arena process opens `/data/mal/blocks/forward-1016` or any path the ledger or the manager's seal list keeps closed.
2. **Book.** A book is a `bot_id`, a `frozen_sha` (the commit of all code and prompts it runs) and a `clock_start_ms`.
   - All three are appended to `/data/mal/arena/bots.json` before `clock_start_ms`. A dated copy goes in a records PR within 24 h.
   - Only trades with entry decision time ≥ `clock_start_ms` count.
   - Any code or prompt change makes a new `bot_id` with a new clock. The old book keeps running or stops; it is never merged with the new one.
3. **Self-improving books.** For a book whose own reviewer rewrites a playbook (Haiku trader v4):
   - the frozen part is the code plus the reviewer's instructions;
   - each daily playbook version is hashed and logged before the first decision that uses it;
   - a version may use only outcomes whose exit is before its write instant.
   The book is the whole system, versions included.
4. **Costs and fills.** `tools/arena_paper.py` prices fills at the lab's deciding stack: 505,000 lamports per send, rent 2,039,280 lamports per filled trip, +26.08/−16 bps, PRE-trade PumpSwap reserves with V, fee tiers, own impact, flat 15% and pressure-fail legs, and 1.3 s / 1.9 s latency legs. It must match the stack to the lamport on a committed parity set before any book's clock starts.
5. **Stakes.** One book's decisions are also priced at 0.25, 0.5 and 1.0 SOL (H5: 0.10, 0.25, 0.30), with impact at each size. Each stake is a separate cell of the same book.

## 2. Gate read
- A book may be read once it has at least 100 counted trades over at least 5 UTC days. The read applies CLAUDE.md's promotion gate unchanged: a majority of days positive; CI90 lower bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile); total positive after removing the top 3 trades. It must hold under both the flat 15% and the pressure-fail model.
- **Deciding cell:** the 1.9 s leg at the stake to be traded live. The 1.3 s leg is reported beside it.
- Ex-best-day is reported, and a book whose total is not positive ex-best-day is not proposed for live.
- **Multiplicity disclosure:** every read states the number of arena books and cells that existed at the read, and how many were read.
- A pass is not a profit claim. The path after a pass:
  1. quant-proof checks the read on its exact files;
  2. the owner's OK;
  3. a live canary under its own DEC with small limits;
  4. stake increases only while live fills match paper.
- No retune on a book's own counted days. A retuned rule is a new book.

## 3. Isolation from EXP-027 and the sealed reads
1. No arena book uses the l3 model, r4-b's rank or score, or any r4-b feature file, until EXP-027's read is written.
2. Arena results never change anything in EXP-027. Its window, stop rule, k and read are fixed by its registration.
3. Arena books are not push-family books. They take no DEC-030 slot and do not change EXP-027's k.
4. At EXP-027's read, the manager discloses the arena's existence and the count of arena trades on pools that r4-b also attempted in the same hour. That count is computed after the read.
5. **H5 seals:**
   - the H5 Look-2 seal is moot because EXP-024 ended NOT_DECIDABLE on 2026-10-13;
   - the CAP-PICK seal is moot because EXP-022 was withdrawn on 2026-10-15 and the FINAL was written on 2026-10-16;
   - so an arena H5 book records outcomes for pools with s0 at or after its clock start. It never opens the outputs of shadow job #817 or the live canary.
6. The domain seals D2, D3 and D4 cover the Forward walk row's hours `[2026-10-02T10, 2026-10-16T01)` and the tip tape. The arena starts after them and opens neither.

## 4. Not allowed under this DEC
- Any live send.
- Any read of a sealed path.
- Any change to a running job's command.
- Counting a trade decided before its book's clock.
- Opening the outputs of jobs #817, #729 or the H5 executor.
