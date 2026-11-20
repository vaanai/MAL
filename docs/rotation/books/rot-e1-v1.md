# Rotation lane method card: `rot-e1-v1` (DRAFT, with a belief record draft)

Method card under [DEC-036](../../../DEC/DEC-036-rotation-lane.md) §3, from [the template](../METHOD-CARD-TEMPLATE.md), for a live rotation form of the open sim book `osb-e1`. **DRAFT:** it loads nothing, starts nothing and is not a belief record. `TODO-QP-*` tokens are numbers or rulings only quant-proof can supply.

**No book has cleared the promotion gate. Nothing on this card says E1, FLOOR or any book makes money.** Board figures are open-simulation paper (DEC-035 Am.9 §1), "SIM OFF: not decision-grade".

**Headline finding (item 1.3): E1's entries are FLOOR's entries.** Under DEC-036 §9 item 6 (same-pool refusal ON), a rotation E1 can trade only the pools FLOOR does not hold open. So it is close to a no-trade book, and its sim is not the board's osb-e1. The route that fits E1's mechanism is a take-profit change on floor-lane-1 (a DEC-035 amendment under Am.9 §3, with the owner's word), not a rotation book. This card is drafted as asked. The manager decides whether to withdraw it.

| Field | Value |
| --- | --- |
| bot_id | `rot-e1-v1` (paper book `osb-e1`) |
| Card version / date | v0 draft, 2026-11-20 (`date -u` 16:40:41Z), at main 02afa4fc |
| Library row | floor-siblings-design.md §2 row S3 (TP 1.3); DEC-035 Am.9 §4 "E1 from design §1" |
| Card author | worker draft for manager24; the manager owns it |
| Sources | DEC-035 Am.9 §4; manifest `/data2/mal/scratch/floor-open-sim/manifests/E1.json` (file sha256 13d3411b2d71fdd579df38952d26820de0c1c5f706e366e319c25aa4fa57c7c3); `board/BOOKS.json` (osb-e1 start_instant_utc 2026-11-14T07:17:23Z); `~/data/open-sim/board.md` generated 2026-11-20T16:30:58Z (sha256 c276aebe44fb453ee1b05f627717979858004e7222583e95cb0b84f4e3859896; header and summary rows only); floor-siblings-design.md |

## 1. Mechanism
1. **Who loses, and why.** The same as FLOOR (l1-floor-v2: a drained-vault dip in a 24-200 h WSOL PumpSwap graduate, vault ≤ 0.5 SOL, spot −20% against 6 h ago), but it takes profit at +30% on the post-buy mark instead of +50%, else it sells at 24 h, with no stop. The idea: more rebounds reach +30% than +50%, so more trades end at a profit, but the rare large win is cut off.
2. **What would stop it.** FLOOR's stops (a redeploy, a cohort change, crowding), plus a payoff mix in which the lost +50% wins outweigh the extra +30% exits. The large-win share matters: FLOOR's one missed TP of +0.5503 was 87% of a +0.6356 missed list (DEC-036 §6 item 2). Seen through the TP share and the per-day totals.
3. **FLOOR twin or sibling? Overlap.** **A twin in entries.** E1 has the same rule, the same V band, vault cap, age and ratio filters, and the same lane gap guard as FLOOR. Its manifest differs only by `TP 1.3` (and `status_span_no_loss` false where floor-lane-1 runs true). Every E1 entry decision is a floor-lane-1 entry decision on the same print. Under the same-pool refusal, `rot-e1-v1` would trade only (i) triggers FLOOR's executor refused for its own limits (the recon's sim-only `executor:lane_budget 12, executor:wallet_floor 7` on book A from 2026-11-11T12:00:00Z), and (ii) a pool re-triggering after FLOOR exits (rare: E1 sells earlier than FLOOR, so FLOOR usually still holds). **Race:** on the shared print both executors act, and the refusal sees FLOOR only after FLOOR's `send_intent` row, so some entries can be double-bought. That is the opposite of §9 item 6's purpose. `TODO-QP-E1-OVERLAP-BASIS`: QP rules whether any believable sim of the non-overlap slice exists (expected: too few trades), or whether E1 belongs on the FLOOR route.

## 2. Which sim it needs
- [x] Open simulation book: `osb-e1`, start instant **2026-11-14T07:17:23Z** (BOOKS.json; the manifest's `start_hour` is 2026-11-11T12, and the board counts as forward only entries at or after the BOOKS.json instant: "Forward = entry instant at or after the book's start instant"), `osb_follow.py` at 457795f4 (code 5ee17b2ce229).
- [ ] Replay: no (in-sample).
- [x] Twin of FLOOR: in entries, yes (item 1.3). So the natural sim of a TP change is osb-e1 against control osb-a on the same days, which belongs to the FLOOR route, not to a separate wallet.

## 3. What makes this sim believable
1. **Fills and latency.** As rot-ce1-v1 §3.1: strict books price entry at 1.9 s; FLOOR live p50 about 3.1 s; recon FAIL (S1 and S2 INSUFFICIENT n 8, S4 FAIL n 20), SIM OFF. No E1 latency twin. `TODO-QP-LATENCY-HAIRCUT`.
2. **Costs.** Send 160,000 per send (manifest), the engine's PumpSwap fee model, rent refunded, V-priced impact. §8 c at 0.10 = 3.825%.
3. **PumpSwap pricing.** V-priced.
4. **Per-token fee check.** Recon S3 PASS (n 16) for the control book (same engine). There are no live E1 fills. A TP-1.3 exit is a FLOOR-style sell at a different mark.
5. **Fail legs.** Flat 15% and pressure slope 1, both shown.
6. **Trial count.** E1 is one of the design §1 books. It was picked on 11-03..11-11 data (QP-RULING-1115-ce1-prereg P5: "E1 from the same design work"). The board says "best of 32 book rows; treat as shrunk". `TODO-QP-E1-SHRINK`.
7. **Concentration.** Ex-top-3 and per-day totals are needed. The board's summary rows do not carry them (the per-trade tables were not read).
8. **Basis.** Net, no limit layer applied (board header). Unpriced 17 at 0.10 (summary row) against 35 closed and 6 open (all entries). `TODO-QP-E1-UNPRICED`.

**Board summary rows, quoted unrounded (board.md 2026-11-20T16:30:58Z; flat / pressure net SOL (n closed); paper; SIM OFF: not decision-grade; best of 32 book rows, treat as shrunk):**
- osb-e1 0.10 forward: +0.4922 / +0.4626 (30); all: +0.5546 / +0.5217 (35), of which 5 closes are in-sample (entries before 2026-11-14T07:17:23Z); open 6; unpriced 17. QP floor column: "holds; not decision-grade (recon NOT COMPARABLE): no live step rests on it".
- osb-e1 0.25 forward: +1.2366 / +1.1627 (30).
- Control osb-a 0.10 forward (same start instant 2026-11-14T07:17:23Z): +0.4425 / +0.4154 (26).
- All of these are over every E1 entry, which are FLOOR's entries. None is a sim of `rot-e1-v1` under the same-pool refusal. Forward complete days: 6 (11-14..11-19).

## 4. Believe-the-sim criteria for this book
- Shrunk net mean > 0 under the flat leg (`TODO-QP-E1-SHRUNK-MEAN-FLAT`) and the pressure leg (`TODO-QP-E1-SHRUNK-MEAN-PRESS`), and μ0 at 0.10 > 0 (`TODO-QP-E1-MU0-0.10`), on the item 1.3 basis, at the deciding cell (`TODO-QP-LATENCY-HAIRCUT`).
- Criteria (proposed): ≥ 7 complete forward days and ≥ 20 closed trades **on the non-overlap slice** at 0.10; ex-top-3 > 0 under both legs; a majority of days positive. If the slice cannot reach 20 trades in 14 days, the book is not a rotation candidate (item 1.3).
- Why these fit: the payoff is a trade of large wins for frequent smaller ones, so trimmed totals and day counts matter more than the mean.

## 5. Negative but plausible
1. Plausible only if the slice is small and not yet negative after ex-top-3.
2. One extension of 7 days; a second only by a new card version.
3. Ends early: the slice has < 1 trade per day over 7 days (no rotation book exists in practice); a FLOOR rule change.
4. Parameters fixed; a change is a new book.

## 6. Live kill and retire rule
1. Cap: DEC-036 §5 item 2 (0.40 SOL full-wipe at 0.10).
2. Calibration: `docs/rotation/books/rot-e1-v1.calibration-100000000.draft.json` (tokens `TODO-QP-E1-WINSOR-LO/HI`, `TODO-QP-E1-MU0-0.10`, `TODO-QP-E1-MU0-0.05`, `TODO-QP-E1-MU1-0.10` with δ `TODO-QP-E1-DELTA`, `TODO-QP-E1-HW-0.10`, `TODO-QP-E1-HA-0.10`, `TODO-QP-E1-SIM-SOURCE-SHA256`). 6 complete forward days, fewer than 10, so trades are resampled one by one. Final sha256: `TODO-QP-E1-CALIBRATION-SHA256`.
3. Retire: DEC-036 §6 item 6, n0 25, μp from the §8 table.
4. Stops at once: a PumpSwap redeploy (A3); any entry that both books bought (race) before a code fix; floor-lane-1 adopting TP 1.3 (then this book has no purpose).

## 7. Decision host path
- [ ] DSL: no (as rot-ce1-v1 §7).
- [x] Own code (not built): a frozen rule with TP 1.3 (no TP setting in `tools/floor_lane_host.py`; l1-floor-v3 pins Q_STAR only), a keyless host instance with a decision root outside `/home`, and a rotation port for FLOOR-host decision rows. Each is T1 with an md5 replay proof. Frozen sha: `TODO-CODE-E1-HOST-SHA`.

## 8. Ladder plan
First rung 0.10; DEC-036 §5 item 1 up-step minimum, plus a race count of 0 and slip inside the deciding cell. Above 0.25: not in the envelope.

## 9. Records
Belief record §10 (draft, T1). Load and ladder records per DEC-036 §7.

## 10. Belief record (DRAFT; not in force; T1)
- **Book:** `rot-e1-v1`, card v0.
- **What it is:** FLOOR's rule with take-profit 1.3x at 0.10 SOL from the rotation wallet, trading only pools floor-lane-1 does not hold.
- **Sim results:** `TODO-QP-E1-BELIEF-FIGURES` (non-overlap slice, both legs, ex-top-3, per day, best of 32, shrunk).
- **Why we believe it:** `TODO-QP-E1-BELIEF-WHY`. A worker cannot propose a reason before the item 1.3 ruling: the board's figures are FLOOR's own entries with a different exit.
- **What live result makes us stop believing it:** any §6 trigger; any race double-buy; slip beyond the deciding cell's assumption (`TODO-QP-E1-SLIP-BOUND`); fewer than 1 live entry per day over 7 days (book STOP: no slice to trade).
- **First rung:** 100,000,000 lamports.
- **Instance config:** as rot-ce1-v1 §10 with `bot_id` rot-e1-v1; sha256 `TODO-MANAGER-E1-CONFIG-SHA256`.
- **Verdicts:** QP OK and REVIEWER APPROVE at the exact head.

Paper and records only. No book has cleared the promotion gate.
