# DEC-036: Rotation lane (new books live at small size inside a standing envelope)

| Field | Value |
| --- | --- |
| **Status** | **DRAFT, not in effect until owner approval.** Drafted 2026-11-18 (`date -u` 18:08Z) on mal-research-0 at origin/main f74fd027, for manager24. It authorises nothing: no wallet, unit, instance, stake or send, until the owner approves it, sets every number marked [owner to set], and this PR merges with QP OK and REVIEWER APPROVE at its exact head. |
| **Decider** | Vaan (owner). No agent or manager message is the owner's consent. |
| **Builds on** | [DEC-035](DEC-035-live-learning-lane.md) (live-learning lane: §2 limits, §3, §4 redeploys, §5 reporting, §6 gate registration, §7 seals; Am.9 open simulation books and their honesty rules). |
| **Amends (on approval)** | DEC-035 for rotation-lane `bot_id`s only: §2 item 2 (stake 0.02-0.03) is replaced by the §5 ladder here; §2 item 5 (one wallet for the lane) gains a second, separate rotation wallet; §6 (above 0.03 only through the gate) is replaced by this envelope up to 0.25 SOL. |
| **Does not amend** | floor-lane-1: its config, ledger, wallet, limits, rule, host and every DEC-035 amendment for it. The promotion gate and its thresholds. Any seal, read, registration or clock. Hosts, firewall, SSH, Cloudflare. |

No book has cleared the promotion gate. Nothing here says any book makes money.

## 1. Context and the owner's direction
FLOOR (floor-lane-1, rule `l1-floor-v2`) went live under DEC-035 and is the one book trading live now (no gate read; nothing here says it has an edge); registered paper reads stalled. Each new live book has so far needed its own DEC-035 amendment and a Helm install, so trying a second book takes days. This DEC sets one standing envelope in which the manager can put a new book live at small size, with a method chosen for that book, without a fresh amendment each time. FLOOR stays first priority and untouched.

Owner direction, 2026-11-18 (MiScusi chat, as relayed by manager24 in the drafting brief; the manager quotes the original message with its instant before the owner approves):
> "going live is after a sim, one that we believe in. If a sim is negative but we still believe in it, we can just leave it running for a bit longer."

> "every strategy and thing we do needs a different method for it, we can't have a one size fits all approach"

> "Everything is risky, but we shouldn't be dumb."

Capital is the bottleneck: a 2 SOL top-up is pending, and FLOOR at 0.25 with 15 open needs about 3.8 SOL against a wallet of about 3.2 SOL after the top-up (review, host file `/data/mal/hunt-1008/ops/reports/2026-11-18/rotation-plan-review.md`). Hence §5 item 10: the rotation wallet is funded only after FLOOR's room.

Basis: that review (checked against the repo); DEC-035 §2, §6, Am.4 item 7, Am.6 "Capacity at 0.25" and "Price impact", Am.9 §1-§3, Am.12; `tools/forge/dsl_lane_executor.py` (header and `DslLaneLedger`); `tools/floor_decay_monitor.py`; QP-RULING-1118-floor-brake (host `/data/mal/hunt-1008/ops/`, sha256 4f9333c322fce3ced980c0165e0038117b2c0775f6f969319b31b12ea0c6418b at drafting); `ARTIFACTS/lab/probe-final-2026-10-07.md`.

## 2. Scope
1. **Rotation books.** A rotation book is a frozen rule with a live executor path, run under this DEC with its own `bot_id` (never `floor-lane-1`, `h5-lane`, `l1-floor-v2` or an `hv4-` id). DEC-035 §3 holds: a code or prompt change is a new `bot_id`.
2. **Separate wallet and key.** All rotation books trade from one rotation wallet, separate from FLOOR's. Helm creates it and places its key root-only at the path the rotation unit binds. No Claude session reads, prints or copies it. The owner funds it.
3. **Separate ledger and limits.** Rotation books share one rotation ledger and lock, at paths that are not FLOOR's, and are limited only by the §5 constants. No rotation book can take FLOOR's wallet room, count in FLOOR's ledger or trip FLOOR's budget, daily stop, open cap or STOP file.
4. **FLOOR unchanged.** floor-lane-1's config, ledger, wallet, limits, decision host, executor tree, decay monitor (QP-RULING-1118-floor-brake §A) and every DEC-035 amendment for it are unchanged by this DEC.
5. **Shared stops.** Rotation executors honour the wallet-wide STOP Helm uses (`/var/lib/mal-live/STOP`) and A3 pauses (DEC-035 §4), as FLOOR does. The rotation lane STOP file and per-book STOP files stop rotation books only. Any session may create them (DEC-035 §2 item 6 pattern); only the manager removes them, after a notebook note.
6. **Hosts.** Executors run on mal-fast-0 only. DEC-035 §7 (seal overlap declaration) applies to each rotation book before its first live send.

## 3. One method card per book
Each book has a method card, `docs/rotation/books/<bot_id>.md`, copied from [docs/rotation/METHOD-CARD-TEMPLATE.md](../docs/rotation/METHOD-CARD-TEMPLATE.md). The card is a checklist of questions, not one bar. It answers, for that book:
1. **Mechanism:** who loses money to it, what would stop it, how we would see that.
2. **Which sim:** open simulation book (DEC-035 Am.9 §1), replay, or a FLOOR twin or sibling; and why that sim fits.
3. **What makes the sim believable:** fill, latency and cost assumptions matched to FLOOR's live reconciliation (and, while FLOOR's control book is SIM OFF, the haircut used for the measured live-minus-sim bias); V-priced PumpSwap math; a per-token fee check against landed fills; both fail legs; the trial count logged and the estimate shrunk for it; ex-top-3 and per-day totals; net basis at today's cost table (§8).
4. **Believe-the-sim criteria** for that book, set from its mechanism (a rare-event book needs more days, a frequent one more trades).
5. **"Keep running longer"** if the sim is negative but still plausible: what makes it plausible, how much longer (fixed in advance, one extension unless a new card version says why), what ends it early. Parameters stay fixed; a change is a new book.
6. **Kill and retire rule:** its loss cap inside §5, its decay calibration (§6), anything mechanism-specific that stops it at once.
7. **Decision host path:** DSL-expressible (FORGE DSL spec on the rotation executor, no new code) or own code (host and port as a T1 code PR with an md5 decision-equivalence replay proof).
8. **Ladder plan** and the evidence for its 0.25 step.

DEC-035 Am.9 §2 honesty rules apply to every sim cited on a card.

## 4. Go-live condition
A rotation book goes live only when all of these hold:
1. Its method card is complete, and its sim has run as the card says.
2. The manager has written a **belief record** in the book's file: the card version, the sim results (labelled in-sample or forward, both fail legs, ex-top-3, per day, "best of n; treat as shrunk"), why the believe-the-sim criteria are met, the first rung, and the instance config sha256.
3. Quant-proof has posted **OK on that record** at the exact head of the PR that adds it (tier set by the manager; QP OK is required whatever the tier).
4. The §5 envelope has room (wallet funded, books live < cap, monthly budget not spent).

Never straight from replay: a replay alone is not a sim we believe in unless the card argues why a forward sim adds nothing for this mechanism and QP accepts that argument. Live rotation trades are never pooled with sim trades. Anything beyond the envelope (above 0.25, a gate claim, a larger budget) still needs the CLAUDE.md promotion gate on a DEC-035 §6 registration, or an amendment with the owner's word.

## 5. Envelope (all numbers are PROPOSALS [owner to set])
1. **Stake ladder.** First rung 0.10 SOL. Second rung 0.25 SOL after at least 20 closed live trades at 0.10 with realized SOL after all fees ≥ 0 at that stake, no decay warning active, live entry slip inside the card's sim assumption, and a QP OK on the step record [owner to set]. Decay-only rungs: 0.125 and 0.05 (§6). Above 0.25: not in this envelope; it needs the owner's word and a price-impact estimate at that stake.
2. **Per-book loss cap.** Lifetime realized loss 0.40 SOL while the book has never traded above 0.10; 0.75 SOL once a 0.25 step is recorded [owner to set]. Enforced as a full-wipe pre-send (DEC-035 §2 item 1 and Am.3 pattern): book realized − (open stakes + new stake + one fee reserve per position) must stay above −cap. At the cap the book's STOP file is written and the book retires (§6).
3. **Per-book open cap.** At most 4 open positions [owner to set].
4. **Lane-wide daily stop across rotation books.** 0.30 SOL [owner to set], measured as in `FloorLaneLedger.evaluate` (marked and cost-basis arms) over rotation books only, checked before an entry, so one day can lose more (bound: 0.30 plus the positions admitted before the stop). Correlated dumps hit several books at once; this is the guard.
5. **Total open exposure.** Sum of open rotation stakes ≤ 1.00 SOL [owner to set] (10 at 0.10, or 4 at 0.25).
6. **Exploration budget.** Realized loss across rotation books in one UTC calendar month ≤ 1.50 SOL [owner to set], enforced as a full-wipe pre-send. At it the rotation lane STOP is written until the next month's 00:00Z, or the owner's word. Basis: retiring a dead book at 50-100 trades costs about 0.4-1.0 SOL at 0.10 (review), so this is about two to three failed tryouts a month.
7. **Lifetime budget.** 3.00 SOL rotation lifetime realized loss [owner to set]; at it the rotation lane STOP is written and only a recorded owner budget change removes it (DEC-035 §2 item 1 pattern).
8. **Wallet slice per book.** One book's open stakes plus reserves ≤ 40% of the rotation wallet's funded amount [owner to set]; wallet floor 0.25 SOL held once [owner to set].
9. **Books live at once.** At most 3 [owner to set] (review load and the ≤5 active lanes board).
10. **Funding order.** FLOOR first. The rotation wallet is funded only from SOL the owner earmarks for it, after FLOOR's wallet covers its Am.12 capacity, unless the owner says otherwise. Proposed first funding 1.5 SOL [owner to set].

## 6. Decay and retirement
1. **Data.** Per closed live trade, r = realized SOL after all fees, nothing dropped (as `tools/floor_decay_monitor.py` derives it from the ledger), and x = r / s with s the stake of that trade. A stake change starts a new CUSUM population; the retire check uses all live trades of the book.
2. **Winsorize.** w = clip(x, L, U), with L and U the 2.5th and 97.5th percentiles of x in the book's own sim [card may set others, with reason]. Reason: profit in these books comes from rare large wins (FLOOR: one missed take-profit, +0.5503, was 87% of a +0.6356 missed list); raw-SOL rules alarm on normal losing runs and trust lucky runs.
3. **CUSUM (one-sided, down).** μ0 = the card's shrunk sim mean of w; μ1 = 0 (no edge after costs), or μ0 − δ with δ on the card if μ0 is small; κ = (μ0 − μ1) / 2; S_0 = 0, S_t = max(0, S_{t−1} + (μ0 − κ) − w_t).
4. **Calibration per book.** h_w and h_a from 10,000 bootstrap paths of 100 trades drawn from the book's sim w (whole UTC days resampled as blocks, seed 1), set so that, in control, P(S reaches h_w within 100 trades) = 20% and P(S reaches h_a within 100 trades) = 5% [owner to set; QP to confirm]. The calibration run's path and sha256 go on the card before go-live.
5. **Actions** (manager, as records, §7):
   - warning (S ≥ h_w): stake halved (0.25 → 0.125, 0.10 → 0.05);
   - alarm (S ≥ h_a): stake to the minimum 0.05;
   - alarm at 0.05, or any FLOOR-style STOP trigger the card names: book STOP, retire to paper.
   - S resets at each stake change. No automatic step up: going back up needs the §5 item 1 evidence at the lower stake and a QP OK on the step record.
6. **Retire to paper.** From closed live trade 50, at each close, the shrunk mean m = (n · mean(w) + n0 · μp) / (n + n0), with prior mean μp = −c (no edge: the book loses its round-trip cost c in stake units at its stake, §8) and n0 from the card (default 25 [owner to set]). If m < 0 at any check from trade 50 on (so at the latest by trade 100), or the per-book loss cap is hit, the book's instance stops and it goes back to an open sim. Re-live only with a new belief record naming what is new.
7. **Not adopted here.** Market-heat and bad-hour brakes (SOL stress, entry clustering, rolling realized, exposure cap) are not rules of this DEC. They go through the registered overlay study of QP-RULING-1118-floor-brake §B (deciding set E2 entries in [2026-11-18T12:00Z, 2026-11-26T00:00Z), read at or after 2026-11-27T02:00Z). A passing overlay may later be proposed for rotation books by its own record with QP OK.
8. **Power.** These rules are brakes, not tests. At 20 to 100 trades they detect a collapse, not the difference between a small edge and none, and they say nothing about whether a book has an edge.

## 7. Records, not amendments
Inside the envelope these are **records**, each a state-file line (manager's state file), a MiScusi notebook entry (kind decision, with the instance config sha256) and a LAB_STATE row, plus `data/console.json`:
- loading a book (after §4), starting or stopping its instance;
- a ladder step up (with its QP OK) or a decay step down;
- a retirement, a STOP written or removed.

These need an **amendment** to this DEC (T1, separate QP and reviewer, merged, owner's word where §5 says [owner to set]): any envelope number, a second rotation wallet, a stake above 0.25, any change to §2.

Any **code change** (executor, host, port, monitor) is T1: separate QP and reviewer at the exact head, unit tests, and an md5 decision-equivalence replay proof before deploy (CLAUDE.md). Records go in the daily records batch unless they carry a T1 trigger; a method card or belief record carries kill and read rules, so its PR tier is the manager's call (QP OK on it is required either way).

## 8. Costs (checked figures; model figures labelled)
- **Send fee:** 160,000 lamports per send (FLOOR live; DEC-035 capacity blocks: reserve 3,860,000 = rent 2,100,000 + send 160,000 + sell reserve 1,600,000). A round trip has at least two sends: 0.32% of stake at 0.10, 0.128% at 0.25.
- **Pool fee:** about 1.2% of stake per leg (probe-final-2026-10-07: the buy row's logged `pool_fee_est_lamports`, about 600,000 at 0.05; the sell leg is assumed equal), about 2.4% round trip. Per-token fee check on each card (§3).
- **Rent:** measured refundable at 1,513,840 lamports (about 1.5% at 0.10). Both executors close the token accounts; unreclaimed rent must be 0.
- **Price impact (model, V + q ≈ 18 SOL constant product, not fills):** round trip about 1.1% of stake at 0.10 (about 0.0011 SOL, DEC-035 Am.4 item 7 / Am.6) and about 2.8% at 0.25 (about 0.007 SOL, Am.6 "Price impact"). In SOL per trade it scales with the stake squared. Deeper pools have less.
- **All-in round trip (sum of the lines above; arithmetic, not a measurement):** about 3.8% at 0.10, about 5.3% at 0.25, before any failed-send cost. This is c in §6 item 6 unless the card measures a different number.
- **The probe's "2.59% at 0.5" is a fee-only projection** that scales the fixed fees; the probe note states price impact is not in it and is not a measurement of deeper pools. The probe's own inference with impact is about 3.62% at 0.5.

## 9. Executor change: SPEC only (no code in this PR)
1. **Rotation class.** A rotation-lane executor (a new module or a `lane_class: "rotation"` path beside `tools/forge/dsl_lane_executor.py`; the code PR picks) that reuses the bridge mechanics (ATA close, STOP/HALT, A3 pause, LIVE_OK, gap hold, backstop) and follows FORGE DSL host rows; own-code books plug in their own decision host and port.
2. **Stake.** Rotation stakes must be one of `ROTATION_STAKE_RUNGS_LAMPORTS = (50_000_000, 100_000_000, 125_000_000, 250_000_000)`, inside bounds (50_000_000, 250_000_000) [owner to set]; the executor refuses to start otherwise. This replaces `DSL_LANE_STAKE_BOUNDS_LAMPORTS` (20_000_000, 30_000_000) for rotation-class books only; DEC-035-class DSL books keep it unless the owner says to raise it in place.
3. **Own limits.** `ROTATION_BOOK_LOSS_CAP_MAX_LAMPORTS` 750_000_000, `ROTATION_BOOK_MAX_OPEN` 4, `ROTATION_DAILY_MAX_LAMPORTS` 300_000_000, `ROTATION_OPEN_EXPOSURE_MAX_LAMPORTS` 1_000_000_000, `ROTATION_MONTH_BUDGET_MAX_LAMPORTS` 1_500_000_000, `ROTATION_LIFETIME_BUDGET_MAX_LAMPORTS` 3_000_000_000, `ROTATION_WALLET_SLICE_MAX_BPS` 4000, `ROTATION_WALLET_FLOOR_MIN_LAMPORTS` 250_000_000, `ROTATION_MAX_LIVE_BOOKS` 3: code ceilings set to the owner's §5 numbers; config may only lower them.
4. **Isolated ledger.** One rotation ledger and lock at a path that is not FLOOR's; per-book limits from that book's rows, lane-wide limits from all rotation rows. Evaluation reads only `ROTATION_*` constants, never `floor_lane_executor`'s `LANE_*` or `FLOOR_LANE_*` at call time (today's DSL executor imports them so "the lane rules move together"; rotation books must not).
5. **Refusals at start.** `bot_id` reserved or invalid; ledger path or STOP path equal to FLOOR's (a pinned list); wallet public key equal to floor-lane-1's (public key in a pinned constant); a key path set in the instance config (only the unit binds the key).
6. **Unit.** A systemd template unit `mal-rotation-lane@.service`, installed once by Helm: binds the rotation key root-only, the pinned executor tree, and `<instance>.json` from a root-owned config dir. The manager writes instance configs (sudo on mal-fast-0) and starts or stops instances within the envelope; a new executor tree is a HELM-ASK after its T1 code PR.
7. **Monitor.** A per-book form of `tools/floor_decay_monitor.py` (`--bot <bot_id>`, rotation ledger, §6 calibration read from the card's pinned file), read-only, run as a manager MiScusi job. FLOOR's monitor and its constants are unchanged.
8. **Proof.** The code PR changes none of `tools/floor_lane_executor.py`, `tools/arena_live_bridge.py`, `tools/floor_arena_executor.py` (stated with `git diff --stat`), and carries an md5 decision-equivalence replay proof that DEC-035-class DSL executor rows are unchanged on a replay fixture.

**Tests that must pin each constant** (names indicative):
- `test_rotation_stake_rungs_pinned`, `test_rotation_stake_not_a_rung_refuses_start`, `test_dsl_lane_stake_bounds_unchanged_for_dec035_class`;
- `test_rotation_book_loss_cap_presend_refuses_and_writes_book_stop`, `test_rotation_book_max_open`;
- `test_rotation_daily_stop_spans_books_and_resets_at_00z`, `test_rotation_open_exposure_cap`;
- `test_rotation_month_budget_presend_and_lane_stop_until_month_end`, `test_rotation_lifetime_budget_stop_survives_restart`;
- `test_rotation_wallet_slice`, `test_rotation_wallet_floor_held_once`, `test_rotation_max_live_books`;
- `test_rotation_limits_independent_of_floor_constants` (monkeypatch every `LANE_*`/`FLOOR_LANE_*`; rotation decisions identical);
- `test_rotation_refuses_floor_ledger_stop_path_and_pubkey`, `test_rotation_refuses_key_path_in_config`, `test_rotation_reserved_bot_ids`;
- `test_rotation_honours_wallet_wide_stop_and_a3_pause`, `test_rotation_book_stop_does_not_stop_other_books`;
- `test_rotation_decay_cusum_matches_card_calibration`, `test_rotation_decay_warning_halves_alarm_minimum` (monitor flags only; the monitor writes nothing).

## 10. Who does what
- **Owner:** approves this DEC and sets every [owner to set] number; funds the rotation wallet; gives the word for anything above 0.25 or any envelope change; may stop anything at any time.
- **Helm:** creates the rotation wallet and places its key root-only; installs the template unit once and each executor tree on a HELM-ASK. The firewall, sshd, cloudflared and Cloudflare fences are unchanged.
- **Manager:** owns method cards and belief records; gets QP OK; is the only one who starts, stops, configures or restarts a rotation instance, within the envelope; takes ladder and decay steps; writes and removes STOP files; keeps the §7 records; reports each book daily (DEC-035 §5).
- **Workers:** builders write code PRs, sims and card drafts; quant-proof rules on belief records, ladder steps and calibrations; reviewers review diffs; host-ops reads only. Workers never start, fund, configure or restart a lane executor and never read a wallet key. Workers do not merge.

## 11. Open points for the manager and owner
1. Every [owner to set] number in §5, §6 and §9.
2. Whether DEC-035-class DSL books keep 0.02-0.03 or `DSL_LANE_STAKE_BOUNDS_LAMPORTS` is raised in place (§9 item 2).
3. Same-pool rule: a rotation book and FLOOR could hold the same pool and add impact to each other. Option: the rotation executor reads floor-lane-1's open pools read-only (no lock) and refuses `floor_pool_open`. That makes it a reader of FLOOR's ledger; QP and the owner decide.
4. Whether a method card plus belief record PR is T1 (separate QP and reviewer) or T0 combined with a required QP OK line.
