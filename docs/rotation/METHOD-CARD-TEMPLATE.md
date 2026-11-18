# Rotation lane method card: `<bot_id>`

Template for [DEC-036](../../DEC/DEC-036-rotation-lane.md) §3. Copy to `docs/rotation/books/<bot_id>.md` and answer every question. A question that does not apply is answered "n/a, because ...", never deleted. This is a checklist of questions, not one fixed bar: each book sets its own answers, and quant-proof checks that the answers fit the mechanism.

Nothing on a card says the book makes money. In-sample numbers are labelled in-sample.

| Field | Value |
| --- | --- |
| bot_id | `<bot_id>` (never `floor-lane-1`, `h5-lane`, `l1-floor-v2` or an `hv4-` id) |
| Card version / date | v1, `<date -u>` |
| Library row | `docs/library/` id(s) this book comes from |
| Card author | manager / worker (the manager owns it) |

## 1. Mechanism
1. In two or three sentences: who loses money to this book, and why they keep doing it.
2. What would make the mechanism stop (a program change, a competitor, a fee change, a regime)? How would we see it?
3. Is this a FLOOR twin or sibling (same host, other config)? If yes, how much does it overlap FLOOR's entries (same pools, same minutes)?

## 2. Which sim it needs
Pick one or more, and say why that sim fits this mechanism:
- [ ] Open simulation book on arriving tape (DEC-035 Am.9 §1), from a recorded start instant.
- [ ] Replay on closed hours (in-sample unless the hours are held out; say which).
- [ ] Twin of FLOOR (pricing-only twin or sibling config on FLOOR's host decisions).
- [ ] Other (describe).

Start instant, tape source and its seal line (SEALS-BRIEF), simulator version or commit:

## 3. What makes this sim believable
Answer each with the number used and its source. A blank is a reason not to believe the sim.
1. **Fills and latency.** Entry landing delay and exit delay used, and where they come from: FLOOR's live reconciliation (landing p50/p90, entry slip, exit timing). If the FLOOR control book is SIM OFF at the time, say so, and state the haircut applied for the measured live-minus-sim bias.
2. **Costs.** 160,000 lamports per send (more sends if the exit needs retries); the pool fee charged on that pool; rent reclaimed (unreclaimed rent 0, or the cost if not); price impact at the book's stake (DEC-036 §8).
3. **PumpSwap pricing.** Is every PumpSwap price V-priced (virtual reserve)? Any number priced before the 2026-10-04 V correction is unverified until re-priced.
4. **Per-token fee check.** Is the fee taken per pool or token as actually charged (creator, protocol and LP parts), checked against landed fills, not one flat rate?
5. **Fail legs.** Results shown under the flat 15% and the pressure-fail model at slope scale 1, until real fills of this book measure failures.
6. **Trial count.** How many rules, parameters and variants were tried before this one (count every cell, including dropped ones)? How is the estimate shrunk for it (e.g. best of n, treat as shrunk)?
7. **Concentration.** Total after dropping the top 3 trades; per-day totals; share of the total from one mint or one day.
8. **Basis.** Are all figures net of costs at today's cost table? A gross figure is labelled gross.

## 4. Believe-the-sim criteria for this book
What this book's sim must show before the manager writes a belief record. Set these from the mechanism, not from a generic bar. Examples of things to state: minimum forward trades or days the mechanism needs to show itself (a rare-event book needs more days, a frequent one more trades); the sign and size of the ex-top-3 total; per-day pattern; what the matched FLOOR control book shows on the same days.

- Criteria:
- Why these fit this mechanism:

## 5. Negative but plausible: what "keep running longer" means
The owner's rule: a negative sim we still believe in may run longer. Answer:
1. What makes a negative result still plausible here (a known cause, too few rare wins seen yet, a regime the mechanism names)?
2. How much longer, fixed now: extra trades or days, and one extension at most unless a new card version says why.
3. What ends the extension early (a broken mechanism sign, a cost found to be higher, a seal problem).
4. Parameters stay fixed during an extension. A change is a new book with a new start instant (DEC-035 Am.9 §2 item 1).

## 6. Live kill and retire rule
1. Per-book loss cap at each rung (inside DEC-036 §5).
2. Decay monitor calibration (DEC-036 §6): winsorization bounds from the sim distribution; CUSUM reference values; warning and alarm thresholds with their false-alarm rates over 100 trades; the sim run used to calibrate them (path and sha256).
3. Retire check: prior weight n0 and prior mean for the shrunk mean; check points (closed trade 50 to 100).
4. Anything mechanism-specific that should stop the book at once (e.g. the program change named in §1.2).

## 7. Decision host path
- [ ] DSL-expressible: the FORGE DSL spec path and its `spec_sha256`; it runs on the rotation executor template (DEC-036 §9) with no new code.
- [ ] Own code: the decision host and its executor port; the code PR (T1, separate QP and reviewer) and the md5 decision-equivalence replay proof required before install.

Frozen sha:

## 8. Ladder plan
- First rung (0.10 unless the card argues for less):
- What evidence moves it to 0.25 (DEC-036 §5 item 1 minimum, plus anything this book needs, e.g. live fill slip within its sim's assumption):
- Above 0.25: not in the envelope (owner's word and an impact estimate).

## 9. Records
- Belief record (DEC-036 §4): path, date, QP OK link.
- Load, ladder, decay and retire records: state-file line, notebook entry, LAB_STATE row (DEC-036 §7).
