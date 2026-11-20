# Rotation lane method card: `osb-boost330`

Method card under [DEC-036](../../../DEC/DEC-036-rotation-lane.md) §3, from [the template](../METHOD-CARD-TEMPLATE.md). It registers a **paper forward book** (fresh forward, DEC-031 stack). It starts no live instance and writes no belief record. **No book has cleared the promotion gate. Nothing here says osb-boost330 or the H5 family makes money.** Every figure below is in-sample and from a spent block.

| Field | Value |
| --- | --- |
| bot_id | `osb-boost330` (paper book id; the rule is `H5-BOOSTFLOOR v1`; never `h5-lane`, `floor-lane-1` or an `hv4-` id) |
| Card version / date | v1, 2026-11-20 (`date -u` 17:39Z), prep PR `claude/osb-old-boost330-prep` |
| Library row | `l3-boost-h5q50-1` and `h5v1-boostfloor` (rotation-candidates.md R1; the owner retired the H5 line 2026-11-08, so a start needs the owner's word, see start plan) |
| Card author | worker (the manager owns it) |

## 1. Mechanism
1. The pump.fun BOOST agent is a price-insensitive TWAP buyer: after every non-mayhem graduation it spends 17.585 SOL in about 29-30 slices over about 345 s, whatever the price. PumpSwap prices at (real quote + V) / base with V about 17.58 SOL, so sells cannot push Q = real quote + V below V. When early dumping has drained a fresh pool close to that floor while BOOST still has budget, we buy and hold to just before BOOST's last slice. Who loses: early sellers dumping into a pool that a scheduled buyer is about to lift.
2. Stops if pump.fun changes the BOOST amount, slice schedule or the 345 s span, or the program change moves V. Seen in `boost_slice` records (slice count, last-slice time, budget). Job #3151 (2026-11-20, public RPC, 155 graduations over 6.1 h) measured: about 25 graduations/h; about 49% of non-mayhem WSOL pools get InitBoost (about 300 BOOST pools/day); budget 17.586 SOL over about 30 slices, last slice about 327 s after migrate. That is a capacity and schedule check, not an outcome.
3. Not a FLOOR sibling. Entries at 0-300 s after migration vs FLOOR's 24-200 h; pool overlap is structurally about 0 by age.

## 2. Which sim it needs
- [x] Other: the frozen H5 shadow detector on the live public PumpSwap feed (`tools/h5_shadow.py --fresh-forward ARENA`), priced by the arena H5 scorer. Not an osb_follow book (osb_follow replays FLOOR host code on tape; the BOOST rule needs slice-level live ingest of its own).
Why: the entry is a 1.3-1.9 s race; only a forward feed measures the feed delay (the scorer measures it per trade, landing = age + latency).
**Frozen rule, exactly (variant `H5-BOOSTFLOOR v1`, Q\* 40):** best-evidenced H5/Q50 variant, because (a) Q\* 40 is the discovery best (cells {40, 50, 60}, monotone, smaller Q\* better, EXP/EXP-024-h5-boostfloor-part1-prereg.md lines 98 and 102) and the only one with a Sept confirmation figure (docs/research/edge-hunt-1030/NOTE.md line 34: 0.1 SOL, 1.3 s flat +13.208%, 1.9 s +12.455%, in-sample, decaying by block +26.7 to +6.8%); (b) Q\* 50 was "a discarded discovery cell chosen for attempts/day, not for edge" and "No Q50 edge exists and none is claimed" (EXP/EXP-024-h5-boostfloor-part1-prereg.md line 1292); Q\* 60 is the same family with no better discovery. Rule text: `/data/mal/hunt-1008/h5-flows/RULE.md`, sha256 `c66b1a5990468080d56a97d67619c035cd81e2782eac89c48b94b8c9c9abe56c` (tools/h5_shadow.py line 190). Trigger: V in [17.5, 17.7] SOL, non-mayhem; first non-BOOST sell at t in [0, 300] s with post-trade Q <= 40 SOL and BOOST spent < 0.999 x 17.585 SOL; one trade per pool. Entry landing slot + ceil(1.3 s / sps) (binding leg ceil(1.9 s / sps)). Exit at s0 + round(330 s / sps), sell lands ceil(0.55 s / sps) later. 55,000 lamports priority on the frozen read leg; the deciding stack is the arena stack of DEC-031 rev 2 (505,000 per send).
Start instant, tape source, seal line: see the start plan (set by the manager). Simulator: `tools/h5_shadow.py` blob f23cf492 at main 02afa4fc (default Q\* path byte-identical to the one that ran h5v1).

## 3. What makes this sim believable
1. **Fills and latency.** Entry legs 1.3 s and 1.9 s (deciding 1.9 s); we land about 3.1 s live (about 8 slots). EXP-012: edge gone by k >= 12 slots under V, so the margin is thin. The scorer measures feed delay per trade. FLOOR's recon does not apply (different rule); so the sim has no live-minus-sim bias measurement for this rule. Real live evidence: the H5 live canary lost about -0.33 SOL in total (manager21 strategic review), 0 OOS positives.
2. **Costs.** 505,000 lamports per send plus rent 2,039,280 per filled trip, haircut with E1 floor, spot guard 1.15, strand as total loss (arena stack).
3. **PumpSwap pricing.** Variant `pv` (per-print V) is the traded variant; `fv` is report-only. Priced after the 2026-10-04 V correction.
4. **Per-token fee check.** Canonical PumpSwap fee tier on the landing state's market cap; creator fee tier in the stack; not re-checked against landed fills for this rule.
5. **Fail legs.** Flat 15% and pressure slope scale 1, pressure intercept refit at the read.
6. **Trial count.** The H5 line carries "best of 413 variants" (rotation-candidates R1); discovery cells 3 x 5 exits; EXP-024 / Q50-a1 NOT_DECIDABLE. Treat as shrunk.
7. **Concentration.** In-sample: 5.78% of trades carry 110% of P&L (h5-flows); report ex-top-3 and per-day at the read.
8. **Basis.** The in-sample +13.208% is the flat, frozen 55k stack, not the deciding arena stack; labelled in-sample, not a forward number.

## 4. Believe-the-sim criteria
- Deciding latency cell: 1.9 s leg at 0.10 SOL (stake pinned at the registration).
- Criteria, declared now: at least 100 forward trades and 5 distinct UTC days (the gate counts) before any belief record; shrunk mean per trade under flat 15%, pressure and mu0 at 0.10 all > 0; ex-top-3 > 0; edge not decaying (second half of days not below zero when the first half is positive). Trigger rate is unknown at Q\* 40 (Q\* 50 raw was 36/31/21 per day on 10-19..21; fewer at 40; volume halved 10-28/29): the first number to fetch is the count in the first 24 h; below 5 triggers/day at day 3 the book is not decidable in the window.
- Why: a frequent rule, so trades bind, plus a day bar against one-day concentration (EXP-017 precedent).

## 5. Negative but plausible
1. The in-sample edge decayed by block and the canary lost; a negative forward result is expected from regime decay, not necessarily a broken rule; BOOST schedule could still be intact (check slice records).
2. One extension of 7 days, fixed now.
3. Ends early: BOOST schedule/amount changed (job #3151 check repeated), seal problem, feed gaps over 20% of hours.
4. Parameters fixed; a change is a new bot id.

## 6. Live kill and retire rule
1. No live step is proposed. If ever: per-book loss cap DEC-036 §5 default.
2. Decay monitor calibration: n/a, because no forward sim distribution exists.
3. Retire check: DEC-036 §6 item 6 defaults.
4. Immediate stop: BOOST schedule change; pump/PumpSwap redeploy (DEC-035 disclosure line applies).

## 7. Decision host path
- [x] Own code, already pinned and unchanged: `tools/h5_shadow.py` (no edit in this PR, so no md5 equivalence proof is owed). Scorer: OPEN, see start plan section 5.
Frozen sha: set by the manager at the start (clean checkout of main at the merge commit).

## 8. Ladder plan
First rung 0.10; nothing above 0.25 without the owner's word and an impact estimate.

## 9. Records
Start line, notebook entry, LAB_STATE row (DEC-036 §7).
