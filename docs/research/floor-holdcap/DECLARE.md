# FLOOR hold-cap screen: DECLARE (written before any run)

T1. PAPER only. In-sample research. Not a gate read. Nothing here says any book makes money.

Origin: the owner's Duet research channel suggested that shorter holds may earn more per SOL of capital while FLOOR is capital-short.

**The sample is small: about 7 days** (counted entries 2026-11-10T10Z to 2026-11-17T12Z, six complete exit days). The keep/ tape holds hours only from 2026-11-02T01Z, and the follower needs a seed at least 201 h before the start, so the earliest legal start is 2026-11-10T10Z.

## 1. Books

Three dry books, copies of manifest `/data2/mal/scratch/floor-open-sim/manifests/A.json` (osb-a: host lane, stakes 0.1/0.25/0.5/1.0, send_lamports 160000, tape_dir `.../keep`, lane gap_lookback_s 21600). Each copy changes only: book_id, title, out_root, `seed_from_hour` (2026-11-02T01), `start_hour` (2026-11-10T10) and `rule_overrides.HOLD_S`. Each runs `osb_follow.py --once --max-hours 194`, which ends at 2026-11-18T12:00Z (last processed hour 2026-11-18T11). No code change to the simulator: `osb_follow.py` already accepts `rule_overrides` `HOLD_S` (`apply_overrides`).

| book_id | HOLD_S override | hold cap | out_root |
| --- | --- | --- | --- |
| holdcap-control24 | absent | 24 h (as live) | /data2/mal/scratch/floor-open-sim/holdcap/holdcap-control24 |
| holdcap-h12 | 43200 | 12 h | /data2/mal/scratch/floor-open-sim/holdcap/holdcap-h12 |
| holdcap-h6 | 21600 | 6 h | /data2/mal/scratch/floor-open-sim/holdcap/holdcap-h6 |

Out roots are 0700 with `setfacl -b -k`, under `/data2/mal/scratch/floor-open-sim` so that `osb_follow.guard_paths` accepts them; manifests (0444) are in `holdcap/manifests/`, not in `floor-open-sim/manifests/`. Only `floor_holdcap_read.py` reads their ledgers (summary lines only).

`HOLD_S` also moves `HOLD_MS` and the lane host's `BLOCK_MS` (hold + cooldown + margin), so per-pool re-entry comes sooner as well. A variant therefore differs from the control in two ways, the exit time and the re-entry block; the screen does not separate them.

**Follower version.** SEALS-BRIEF line (k): `osb_follow.py` runs only from a clean detached worktree at the PASS commit 457795f4d0cb252656fd48f795d3abd617d5c380 (default `/data2/mal/wt/osb-457795f4`; the runner refuses any other HEAD, an attached branch, or a dirty tree). That commit already carries `apply_overrides` with HOLD_S and BLOCK_MS. It does not write decisions.md5; the runner prints the last `decision_md5` of md5-by-hour.jsonl instead. The reader runs from the PR's commit. The manager adds a SEALS-BRIEF line before the job: this job, the commit, the three manifest sha256s, the out roots, and that only `floor_holdcap_read.py` reads their ledgers.

## 2. Hours

In-sample only. Seed from 2026-11-02T01, start 2026-11-10T10, pricing end 2026-11-18T12:00Z. The end is before the brake-study E2 held-out window, which starts at 2026-11-18T12:00Z and is never touched. The reader prices with `--to-ms` 2026-11-18T12:00Z.

Counted trades: entry in [2026-11-10T10:00Z, 2026-11-17T12:00Z), so every counted trade can reach its 24 h exit before the pricing end (a control entry in the last few seconds before 11-17T12:00Z may still drop as open; opens are counted and printed). Booking day is the exit UTC day, as the board. Complete days (test iii): the fixed list of exit days 2026-11-11 to 2026-11-16; a day with no trades counts as 0.

## 3. Pricing

`scripts/research/floor_holdcap_read.py` prices each ledger with the pinned board pricer: `floor_nov_pnl` (rule total, latency 1p9, send 160000, ledger-send 160000) called as `osb_board.price_snapshot` does, trades loaded with `osb_board.book_trades`. Legs: `flat_1p9` (flat 15% fail) and `press_1p9` (pressure-fail, slope scale 1). No cost is re-implemented. "Strict 1p9 leg" means the strict (gap-excluding, `gap_exclusion` on, as osb-a) book at latency 1p9.

Per book x stake x leg the reader prints n, total net, mean per trade, ex-top-3, mean hold hours, SOL-days of capital used (sum of stake x hold days), net per SOL-day, per-day totals, and, from the counted window only, the number of entries still open at the end (`open_at_end`), unpriced entries, and the engine-excluded entries by reason name (gap_at_entry, gap_at_exit, missing_v, feed_gap, other). A variant that loses trades to exclusion is therefore visible (a longer hold catches more feed gaps).

## 4. Capital-constrained portfolio sim (`--wallet-sim`)

The counted trade list at the 0.25 cell, in (entry_ms, seq) order (seq = the ledger order of `book_trades`; never exit time, which would use the future), at stake 0.40 with FLOOR's live limits. Net per trade is scaled linearly from the 0.25 cell to 0.40: linear scale; impact not modelled. An entry is taken only if every limit allows it; capital returns at exit (an exit at or before an entry's instant frees its capital first).

- Budget, full wipe: realized - sum over open (stake + R) - (new stake + R) > -budget. R = `LANE_MIN_FEE_RESERVE` (3,860,000 lamports), budget = `LANE_BUDGET_MAX_LAMPORTS` (5.00 SOL, DEC-035 Am.14), both read from `tools/floor_lane_executor.py`.
- Wallet floor 0.75 SOL (DEC-035 live value, `--wallet-floor-sol`): start wallet + realized - sum open (stake + R) - (new stake + R) >= floor.
- Max open 16: the live value (DEC-035 Am.14 records "max_open 16 binds"); `--max-open`, default `LANE_MAX_OPEN` (16) from the executor.
- Daily stop 0.80 SOL (`FLOOR_LANE_DAILY_MAX_LAMPORTS`): modelled on the realized arm only: an entry is blocked when the realized net of trades exited on the entry's UTC day is at or below -0.80 SOL, until the next 00:00Z. `FloorLaneLedger.evaluate` also stops on marked P&L (`view.pnl - base`) and on cost (`view.cost - base`); those arms need marks that a replay does not have and are NOT computed. The realized arm alone is the optimistic one: the marked arm can only trigger earlier.
- Start wallet (required `--start-wallet-sol`, deciding value): **3.96808 SOL**, from DEC-035 Am.14 ("free wallet needed = 0.75 + slots x 0.40226 SOL", the about-8-slots no-top-up case: 0.75 + 8 x 0.40226). Test (i) is driven largely by this number.
- Sensitivity (report only, never decides): start wallet + 2.0 SOL, the owner's top-up (`--topup-sol`), printed as `sensitivity_topup_report_only_does_not_decide`.
- The sim charges stake + R = 0.40386 SOL per open against the floor; Am.14 uses 0.40226. The sim is slightly stricter.

Output per variant and leg: realized SOL/day (over the counted window, 7.083 days), entries taken, entries skipped for capital with the reason split (daily_stop, max_open, budget, wallet), net per SOL-day.

## 5. Kill/adopt line (fixed now, re-fixed on the 7-day window)

A shorter cap "wins clearly" only if, at the deciding cell (stake 0.25, strict 1p9 leg) under BOTH the flat 15% and the pressure legs, it holds all three:

1. Capital-constrained realized SOL/day (section 4, primary start wallet) is at least 1.3x the control's. If the control's figure is at or below 0, this reads: the variant is above 0 and at least control + 0.3 x |control|.
2. It stays above the control after dropping each variant's top 3 trades (ex-top-3 total, 0.25 cell; an absent value, fewer than 4 trades, fails).
3. A majority of the six complete days (section 2) are at or above the control's per-day total (ties count as at or above; a day with no trades is 0).

**NOT_DECIDABLE floor.** If any of the two compared books (variant or control) has fewer than 40 counted trades at the 0.25 cell, or fewer than 5 of the six complete exit days with a trade, the verdict is `NOT_DECIDABLE`, never "WINS CLEARLY". The runner also prints `INCOMPLETE` and does not run the reader if any book processed fewer than 194 hours; a book with fewer hours than the others is not compared.

Otherwise the verdict is "no clear win". The reader prints this verdict per variant; there is no other adopt path.

**A "WINS CLEARLY" authorises nothing live.** Live use of a cap needs all of: (a) a registered DEC-035 Am.9 forward open-sim book with that HOLD_S, with its own 48 h proof at its commit, read only on hours after its recorded start; (b) a DEC-035 exit-rule amendment with the owner's word (Am.9 §3), since HOLD_S changes the frozen l1-floor-v2 rule (a new rule version); (c) the md5 decision-equivalence replay proof of the runner change before deploy, and a Helm install. The result never counts toward the promotion gate or a DEC-035 §6 read.

## 6. Disclosures

- In-sample; about 7 days; not a gate read; no promotion claim.
- **Not blind.** Hours before 2026-11-16T01Z are inside Forward walk 2 and were already shown through open simulation book osb-a (book A) and the D0/C0 replays: "not blind: open simulation book osb-a". Entries from 2026-11-14T07:17Z to 2026-11-18T12:00Z are also brake-study E1 (not blind). Walk-2 and E1 are not blind for this read. E2 (from 2026-11-18T12:00Z) is untouched.
- **Multiplicity.** Best of 3 variants (control plus two caps), on top of the board's best-of-N = the figure the manager passes at the run instant (`--board-best-of`, printed in the output header); treat any figure as shrunk (winner's curse).
- `BLOCK_MS` also shortens re-entry, so the effect is exit timing plus re-entry.
- Both fail legs are reported; the verdict needs both.
- The wallet sim scales linearly from the 0.25 cell; impact at 0.40 is not modelled. The daily stop is the realized arm only (section 4). The per-open charge is slightly stricter than Am.14's.
- Test (iii) uses the fixed six-day list, not the days present.
- The Duet prior (about 2x per SOL at 6-12 h caps) came from Aug-Sep data, a different regime; the hours in this screen do not test it.
- The books are dry copies of osb-a on its keep/ tape; no live fills enter.

## 7. Run

`scripts/research/floor_holdcap_run.sh` as a MiScusi job on mal-research-0, by the manager, after the SEALS-BRIEF line of section 1. Env: `BOARD_BEST_OF` (required), `START_WALLET_SOL` (default 3.96808), `SIM_REPO` (default `/data2/mal/wt/osb-457795f4`), `HOLDCAP_BASE` (default `/data2/mal/scratch/floor-open-sim/holdcap`). Stdout carries only manifest sha256, hours processed and the last decision md5 per book, INCOMPLETE lines and the reader's summary lines.
