# Rotation lane method card: `rot-ce1-v1` (DRAFT, with a belief record draft)

Method card under [DEC-036](../../../DEC/DEC-036-rotation-lane.md) §3, from [the template](../METHOD-CARD-TEMPLATE.md), for a live rotation form of the open sim book `osb-ce1`. **DRAFT:** it loads nothing, starts nothing and is not a belief record. Every `TODO-QP-*` token is a number that only a quant-proof calibration or ruling can supply. No figure here was invented to fill one.

**No book has cleared the promotion gate. Nothing on this card says CE1, FLOOR or any book makes money.** Every board figure below is paper from an open simulation (DEC-035 Am.9 §1), not a gate read. The board marks it "SIM OFF: not decision-grade".

| Field | Value |
| --- | --- |
| bot_id | `rot-ce1-v1` (paper book `osb-ce1`; never `floor-lane-1`, `h5-lane`, `l1-floor-v2` or an `hv4-` id) |
| Card version / date | v0 draft, 2026-11-20 (`date -u` 16:40:41Z), at main 02afa4fc |
| Library row | floor-siblings-design.md §2 rows S1 (vault 1.0) + S3 (TP 1.3); library row floor-vault-1p0-forward-ab |
| Card author | worker draft for manager24; the manager owns it |
| Sources | DEC-035 Am.9 book-list record osb-ce1 (2026-11-16); QP-RULING-1115-ce1-prereg (sha256 d985b5e7c2926c1b7c2a9b1d37749a9d36a0c5f797c2f6af84ce3a3ec35da4f7); manifest `/data2/mal/scratch/floor-open-sim/manifests/CE1.json` (file sha256 ad64618bf4694c95e8bd0d313b529ca62d71c3225edb6b48115c41978436139b); `~/data/open-sim/board.md` generated 2026-11-20T16:30:58Z (file sha256 c276aebe44fb453ee1b05f627717979858004e7222583e95cb0b84f4e3859896; header and summary rows only were read); `/data/mal/hunt-1008/ops/reports/2026-11-18/floor-siblings-design.md`; HELM-REPLY-1119-1 |

## 1. Mechanism
1. **Who loses, and why.** CE1 is FLOOR's dip-buy rule (l1-floor-v2) with the drained-vault cap widened from 0.5 to 1.0 SOL (`Q_STAR 1.0e9`) and the take-profit lowered from 1.5x to 1.3x (`TP 1.3`), under the lane gap guard. After a SELL print in a 24-200 h old WSOL PumpSwap graduate (V in [17.5, 17.7] SOL) whose real vault is ≤ 1.0 SOL, whose spot fell more than 20% against 6 h ago and which is still alive, it buys. It sells at +30% on the post-buy mark, else at 24 h, with no stop (floor-siblings-design.md §1). The people who lose are late sellers dumping a quiet, drained graduate near its low, to a buyer who holds into the next wave of attention. The lower TP takes smaller rebounds more often.
2. **What would stop it.** (a) A PumpSwap program or fee change (DEC-035 §4 redeploy; A3 pauses); (b) the graduate cohort changing (V band, creator mix, synthetic completions); (c) more dip-buyers on the same pools, which lifts entry prices. Two are specific to this book: (d) FLOOR switching to vault 1.0 (the manager's plan after the CE1 read, state 2026-11-20T16:36:09Z), which would make most CE1 pools FLOOR's (item 3); and (e) the TP 1.3 cut-off giving up the large wins (FLOOR's +0.5503 missed TP was 87% of a missed list, DEC-036 §6 item 2). How we would see it: daily entry counts against the board, the `floor_pool_open` refusal count, the TP share, and live slip against the sim.
3. **FLOOR twin or sibling? Overlap.** A sibling with **heavy pool overlap**. Every FLOOR entry trigger (vault ≤ 0.5) is also a CE1 trigger (vault ≤ 1.0), on the same print and the same host logic. With DEC-036 §9 item 6's same-pool refusal ON, `rot-ce1-v1` refuses any pool that floor-lane-1 holds open. So the live book trades mainly (i) the vault (0.5, 1.0] slice and (ii) FLOOR-trigger pools that FLOOR did not enter (its own limits refused them: the recon's sim-only `executor:lane_budget 12, executor:wallet_floor 7`), and also (iii) a pool FLOOR already exited, if it re-triggers. **Race:** both hosts decide on the same print, and the rotation refusal sees FLOOR's position only once FLOOR's `send_intent` row is in its ledger. So on a shared trigger both books may send, which adds impact to each other, the very thing §9 item 6 is there to prevent. **The board's osb-ce1 totals are over all entries, not over the slice the live book would trade.** `TODO-QP-CE1-OVERLAP-BASIS`: QP rules which per-trade subset is the sim of `rot-ce1-v1` (proposed: exclude entries whose pool had an open floor-lane-1 position at the decision instant, from the live extract of SEALS line (o); and price the slice alone), and whether the race needs a code fix (for example a short delay with a re-check, or refusing pools that are FLOOR triggers) before go-live. If FLOOR moves to vault 1.0, the slice is about empty and this book is withdrawn.

## 2. Which sim it needs
- [x] Open simulation book on arriving tape (DEC-035 Am.9 §1): `osb-ce1`, start instant **2026-11-17T00:00:00Z**, `scripts/research/osb_follow.py` at 457795f4d0cb252656fd48f795d3abd617d5c380 (code.sha256 5ee17b2ce2296bc3a71bdc371803843b3a1486f9ecb533ba0e28a3ecec6c6690), tape = osb keeper links (SEALS-BRIEF (k)), board (m). Why it fits: FLOOR-style holds of up to 24 h on a low-rate trigger need forward days, and the rule constants were picked on 11-03..11-16 (in-sample).
- [ ] Replay on closed hours: no (in-sample; QP-RULING-1115-ce1-prereg P5 forbids 11-03..11-16 figures as support).
- [ ] Twin of FLOOR: no, because the exit and the vault cap differ.
- [ ] Other: a latency twin of osb-ce1 does not exist (item 3.1).

## 3. What makes this sim believable
1. **Fills and latency.** The strict board books price entry at 1.9 s after the decision (`osb_board.py` header, "latency 1p9") and exit lag 0.55 s (`arena_stack.EXIT_LAG_S`). FLOOR's live landing p50 is about 3.1 s (osb-oq-v1 card §3 item 1). The control-book recon at board 2026-11-20T16:30:58Z: **FAIL**, "S1 INSUFFICIENT (n 8 < 10); S2 INSUFFICIENT (n 8 < 10); S3 PASS (n 16); S4 FAIL (n 20). SIM OFF: not decision-grade". So the live-minus-sim bias is not measured. The only latency evidence is osb-a beside its 3.010 s twin osb-a-lt-a (forward 0.10 pressure +0.4154 vs +0.4057, n 26 each; summary rows). `TODO-QP-LATENCY-HAIRCUT`: the haircut (or a CE1 latency twin at 3.010 / 4.085 s) that sets the deciding cell.
2. **Costs.** Send 160,000 lamports per send (manifest `send_lamports`; FLOOR live). Pool fee from the pinned engine's PumpSwap fee model. Rent refunded (deciding stack, `RENT_DECIDING` 0). Impact from V-priced reserves at the simulated stake. DEC-036 §8's arithmetic c at 0.10 is 3.825% (monitor `COST_FRAC`), used for the retire prior.
3. **PumpSwap pricing.** V-priced (the lane host and engine price with V included; nothing here predates the 2026-10-04 V correction).
4. **Per-token fee check.** Recon S3 PASS (n 16): live fees per trade ≤ model + 0.5 pp of stake for the control book (same engine and fee model). There are no live CE1 fills.
5. **Fail legs.** The board shows flat 15% / pressure (slope scale 1) for every row. Both decide.
6. **Trial count and shrink.** CE1 is "the best-looking of 40 board rows" at its pick (QP-RULING-1115-ce1-prereg P5), on top of the earlier variant screens (floor_variants_funnel / floor_variants_price). The board now says "best of 32 book rows; treat as shrunk". Shrink: DEC-036 §6 item 6 form, m = (n · mean(x) + n0 · μp) / (n + n0), with n0 = 25 and μp = −c̄. `TODO-QP-CE1-SHRINK`: whether n0 must grow for the trial count (≥ 40 rows).
7. **Concentration.** The read reports ex-top-3, per-day totals and TP share (P4).
8. **Basis.** Board figures are net of sends, fees and impact under each fail leg, at the simulated stake, with **no limit layer** ("No limit layer (max_open, budget, daily stop) is applied to any book"). The rotation per-book open cap (3 open at 0.10 under the 0.40 cap, DEC-036 §5 item 2) will drop entries the board counts. **Unpriced:** the summary row shows `unpriced 23` against 26 closed and 7 open at 0.10 (entries with no priced exit: engine-excluded or closed without an exit instant). `TODO-QP-CE1-UNPRICED`: whether this exclusion share leaves the sim believable (P4's gap-excluded bar is ≤ 10% at 0.25).

**Board summary rows, quoted unrounded (board.md generated 2026-11-20T16:30:58Z, tape through 16:00Z; flat / pressure net SOL, (n closed); paper; SIM OFF: not decision-grade; best of 32 book rows, treat as shrunk):**
- osb-ce1 0.10 forward: +0.4920 / +0.4635 (26); open 7; unpriced 23; day in progress +0.0170 / +0.0163 (7).
- osb-ce1 0.25 forward: +1.2348 / +1.1636 (26).
- Control osb-a 0.10 forward: +0.4425 / +0.4154 (26) (osb-a started 2026-11-14T07:17:23Z, osb-ce1 on 2026-11-17, so these are not the same window and not a paired figure).
- These are totals over every CE1 entry, before the item 1.3 overlap basis and before any latency haircut. Arithmetic only, not a sim result for `rot-ce1-v1`: 0.4920 / 26 = 0.018923 SOL per closed trade, flat, at 0.10. Forward days: 3 complete (11-17..11-19).

## 4. Believe-the-sim criteria for this book
- **Go-live tests (DEC-036 §4 item 2):** shrunk net sim mean per trade > 0 under the flat leg (`TODO-QP-CE1-SHRUNK-MEAN-FLAT`) and the pressure leg (`TODO-QP-CE1-SHRUNK-MEAN-PRESS`), and μ0 at 0.10 > 0 (`TODO-QP-CE1-MU0-0.10`). All three are on the item 1.3 overlap basis, at the deciding cell.
- **Deciding latency cell:** `TODO-QP-LATENCY-HAIRCUT` (proposed: no faster than 3.010 s, FLOOR's post-Am.10 expectation; 1.9 s only with QP's stated haircut).
- **Criteria (proposed):** (1) CE1's pre-registered read (QP-RULING-1115-ce1-prereg P4: first read 2026-11-24T00:00:00Z, needs ≥ 10 closed at 0.25; NOT_DECIDABLE at 2026-12-01T00:00:00Z) is a PASS; (2) on the overlap basis, ≥ 7 complete forward days and ≥ 20 closed slice trades at 0.10, ex-top-3 > 0 under both legs, and a majority of days positive under both legs; (3) unpriced / exclusion share inside QP's bound (`TODO-QP-CE1-UNPRICED`); (4) `TODO-QP-CE1-ROUTE` (checklist row 15): QP rules that a rotation start is allowed under the pre-registration. P4 says "A pass leads only to an l1-floor-v3 draft plus a DEC-035 amendment".
- **Why these fit:** a rare-large-win mechanism needs days and a trimmed total (ex-top-3), not only a mean. The overlap slice is new, so it has to show itself on its own.

## 5. Negative but plausible: "keep running longer"
1. Plausible if the slice is negative only because the slice is still small (< 20 trades), with no mechanism stop from §1.2.
2. How much longer: one extension of 7 days of osb-ce1 (same book, same manifest), read by the same rule. A second extension only by a new card version.
3. Ends early: §1.2 (d) FLOOR moves to vault 1.0 (the slice disappears); a program or fee change; an osb_follow defect (a fix is a new book).
4. Parameters stay fixed. A change is a new book (DEC-035 Am.9 §2 item 1).

## 6. Live kill and retire rule
1. Per-book loss cap: DEC-036 §5 item 2 (0.40 SOL full-wipe at 0.10, R = 3,860,000 per position; 0.75 after a 0.25 start row).
2. Decay calibration: `docs/rotation/books/rot-ce1-v1.calibration-100000000.draft.json` (tokens `TODO-QP-CE1-WINSOR-LO/HI`, `TODO-QP-CE1-MU0-0.10`, `TODO-QP-CE1-MU0-0.05`, `TODO-QP-CE1-MU1-0.10` with δ `TODO-QP-CE1-DELTA`, `TODO-QP-CE1-HW-0.10`, `TODO-QP-CE1-HA-0.10`, `TODO-QP-CE1-SIM-SOURCE-SHA256`). osb-ce1 has 3 complete forward days, fewer than 10, so trades are resampled one by one, and the rates understate clustering. Final file path and sha256 go here before go-live: `TODO-QP-CE1-CALIBRATION-SHA256`.
3. Retire check: DEC-036 §6 item 6, n0 = 25, μp = −c̄ from the §8 table (0.03825 at 0.10), from closed trade 50.
4. Stops at once: a PumpSwap redeploy (A3 pause, DEC-035 §4); FLOOR switching to vault 1.0 (book STOP, and the card is withdrawn); live entry slip beyond the deciding cell's assumption over the first 10 fills (`TODO-QP-CE1-SLIP-BOUND`).

## 7. Decision host path
- [ ] DSL-expressible: **no.** FORGE cannot express the vault, V band, 6 h spot ratio or TP on the post-buy mark (floor-siblings-design.md §2).
- [x] **Own code (not built).** Needed: (a) a frozen rule with TP 1.3 and Q_STAR 1.0e9 (l1-floor-v3, PR #1454, pins Q_STAR only; no TP setting exists in `tools/floor_lane_host.py`); (b) a keyless host instance for `rot-ce1-v1` on mal-fast-0, with its own decision root outside `/home` (the unit has `ProtectHome=tmpfs`) and its own tape reader line; (c) a rotation port that follows that host's decision log (the merged `RotationLaneBridge` subclasses `DslLaneBridge` and requires `spec_sha256`). Each is a T1 code PR with an md5 decision-equivalence replay proof against osb-ce1's decisions. Then a new executor tree (HELM-ASK).

Frozen sha: `TODO-CODE-CE1-HOST-SHA` (after (a)-(c)).

## 8. Ladder plan
- First rung 0.10. 0.125 and 0.05 are decay rungs only.
- Up 0.10 → 0.25: the DEC-036 §5 item 1 minimum, plus live slip inside the deciding cell's assumption, plus the overlap race count (both books sent into one pool) = 0 over those 20 trades. A step condition, not edge evidence.
- Above 0.25: not in the envelope.

## 9. Records
- Belief record: §10 below (draft). T1, QP OK and REVIEWER APPROVE at the exact head.
- Load, ladder, decay, retire records: DEC-036 §7.

## 10. Belief record (DRAFT; not in force; T1)
- **Book:** `rot-ce1-v1`, card v0 → v1 when the tokens are filled.
- **What it is:** FLOOR's l1-floor-v2 dip-buy rule with vault cap 1.0 SOL and take-profit 1.3x, live at 0.10 SOL from the rotation wallet. It trades pools FLOOR does not hold.
- **Sim results** (forward, paper; both legs; ex-top-3; per day; "best of 32 book rows; treat as shrunk"): `TODO-QP-CE1-BELIEF-FIGURES` (on the §1.3 overlap basis, at the deciding cell). The all-entry board rows of §3 are context, not this figure.
- **Why we believe it:** `TODO-QP-CE1-BELIEF-WHY`. Proposed text: the P4 pre-registered read PASSED; the slice meets §4; the mechanism (§1.1) is FLOOR's, with live fills on the control book; the costs are FLOOR's live costs (recon S3 PASS).
- **What live result makes us stop believing it:** any §6 trigger (decay alarm or warning ladder, retire check m < 0 from trade 50, the 0.40 SOL cap); live slip beyond `TODO-QP-CE1-SLIP-BOUND`; a share of entries with both books in one pool (race) above 0 after the code fix; 20 closed live trades at 0.10 with realized < 0 (no up-step, and a card review); FLOOR moving to vault 1.0.
- **First rung:** 100,000,000 lamports.
- **Instance config** (`/etc/mal-rotation-lane/rot-ce1-v1.json`, manager-written; keys from `rotation_lane_executor.TOP_KEYS` / `LANE_KEYS`): `mode` live; `bot_id` rot-ce1-v1; `frozen_sha` = the installed tree; `spec_sha256` / `max_hold_s` per the own-code port (86,400 + leg); `root`, `state_dir` under `/var/lib/mal-live/rotation-lane`; `end_ms`; `wallet_pubkey` B6r9nd3cwc45yVJsVtYScDhH8cdiC97Geq8SZv2Eye6A; `floor_ledger_read_path` /var/lib/mal-live/h5-lane/lane-ledger.jsonl; `funded_lamports` = the owner's funding; `lane.stake_lamports` 100000000; the other limits default to the code ceilings. sha256: `TODO-MANAGER-CE1-CONFIG-SHA256`.
- **Verdicts:** QP OK and REVIEWER APPROVE at the exact head of the PR that fills this record.

Paper and records only. No book has cleared the promotion gate.
