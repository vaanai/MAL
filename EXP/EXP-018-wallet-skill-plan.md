# EXP-018 plan (exploration stage): causal wallet skill at migration, layered on the frozen EXP-012 book

| Field | Value |
| --- | --- |
| **Status** | **Exploration pre-registration of a screen.** It fixes the definitions, the two cells, the costs, the bars and the refusals before any EXP-018 feature or net is read. It is not an edge claim and not a confirmation plan. |
| **Date** | 2026-10-06 |
| **Hypothesis** | At the EXP-012 decision time (cutoff = the universe row's `mig_ms`, the boundary of EXP-012's own features), the curve SOL held by wallets with a profitable realized track record **before that cutoff** predicts migrate-entry P&L. A veto of low-skill entries, or a filter requiring skill above a level, improves the frozen EXP-012 book, paired per migration. |
| **Frozen book it is layered on** | EXP-012 as frozen: model md5 `a1810d219ed61db64a396f40dc302ce5`, threshold 0.8030766588450794, `migrate` trigger, `tp50_sl30`. Nothing frozen is edited. Each cell can only remove entries. |
| **Tool** | `tools/exp018_wallet_skill.py`, tests `tools/test_exp018_wallet_skill.py` |
| **Measured by this file** | Nothing. No row under `/data/mal` was opened. Only repo code and docs were read, and directory listings of the views to confirm the file layout. |
| **Prior (my estimate, not a measurement)** | Screen pass about 5%. Reasons in section 9. |

## 1. Prior work, and what is different here

- [signal-scan](../ARTIFACTS/lab/signal-scan.md): a copy-follow book built from a `noisy_v0` leaderboard lost. It had three flaws that this plan removes by construction. The window was 1.5 h of tape (49 min for the board itself). The board was scored on the same window it was built on (its own words: "lookahead"). Entry was late (copyable means more than 2 slots after create; median buyer rank 74). EXP-018 does not copy anyone. It uses skill as a **property of the pool's holders at the decision cutoff (`mig_ms`)**, from state that exists strictly before the cutoff, over 12 to 14 contiguous days.
- [wallet-leaderboard](../ARTIFACTS/lab/wallet-leaderboard.md) and `tools/wallet_leaderboard.py`: FIFO realized P&L with a 5,000 lamport fee per trade. EXP-018 reuses the accounting. The constants `TX_FEE_LAMPORTS` and `DUST_TOKEN_RAW` are imported. A round trip's net P&L is the sum over its sells of (proceeds minus matched cost) minus the fee per trade, which is `match_sell`'s total. It is the same whatever the lot order, because a closed round trip consumes every lot. A test checks it against `match_sell` on random trips. The leaderboard's vetoes (farm, wash, sniper) and its rank boards are **not** used.
- [smart-wallet-research](../ARTIFACTS/lab/smart-wallet-research.md): web-only. The external tools (GMGN `smart_degen`, Kolscan, Axiom Trader Scan, Solana Tracker) rank on FIFO realized PnL, win rate and minimum trades. EXP-018's skill score is the same idea computed on our own tape, causally. The external tags are opaque and not used.
- [EXP-016](EXP-016-rug-veto-plan.md) (features d1 `serial_launch_held`, d3 `prior_dumper_held`) asks about **rug risk**: wallets that sold into earlier dumps, or that were launch buyers on at least 3 other mints. EXP-018 asks about **positive skill**: wallets whose closed round trips net positive in SOL. The two sets overlap only by accident. A wallet can be skilled and also a serial launch buyer. EXP-018 uses no dump step, no launch-buyer definition and no rug label. A skilled holder is not read as "safe". If EXP-016 later passes, a skill feature that only re-labels its vetoes would show up as no paired gain once EXP-016's vetoes are applied. This plan does not test that stack.

## 2. Skill score (fixed now)

Per wallet, from the clean-view trade tape, in event order `(t_recv_ms, slot, tx_index, event_index)` (`block_time * 1000` when a row has no `t_recv_ms`, as the EXP-015/016 loaders do):

- A **trade row** is admitted by `tools.wallet_leaderboard.parse_trade`'s rules: a trade row, `buy` or `sell`, venue `pump_bonding`, or `pumpswap` with `quote_is_wsol == True`, a resolved mint, positive integer `sol_lamports` and `token_raw`. Both venues count for skill, so a wallet that exits on PumpSwap closes its trip.
- A **position** is one wallet on one mint. Buys add tokens and cost. A sell is clamped to the inventory (the unmatched part is dropped, as `match_sell` does). Realized P&L accrues per sell. The position **closes** when its inventory falls to dust (`DUST_TOKEN_RAW`). The closed trip's **net** is `realized - 5,000 * trades_in_trip` lamports. Open positions never count (no mark-to-market).
- **Fees (R2).** Bonding rows' `sol_lamports` is **pre-fee** (the pool's quote-reserve change over `sol_lamports` has median exactly 1.0 on the tape), while PumpSwap rows are user-side. For a bonding row the wallet's cash flow is charged pump.fun's bonding fee per side, `BONDING_FEE_PPM = 12,500` (1.25%, `tools/paper_curve_math.BONDING_FEE_PPM`, the rate the simulator uses): a buy costs `sol * 1.0125`, a sell returns `sol * 0.9875`. PumpSwap rows are not adjusted. **Priority fees and tips are not on the tape and are excluded**; skill is therefore slightly overstated for wallets that tip heavily. (`tools/wallet_leaderboard.py` line 1169 says curve fees are "inside" the tape cash flow; that comment is wrong for bonding rows, and is corrected in this PR. The leaderboard's own P&L is not re-run.)
- **Skill state** of wallet w at a cutoff C: `trips(w)` = trips closed with the closing sell at `t_recv_ms` **< C**, and `pnl(w)` = their summed net.
- **Skilled** at C: `trips(w) >= N` **and** `pnl(w) > 0`, with **N = 5**. No other threshold, no rank, no percentile, no win-rate filter.
- **Hours are resolved through EXP-015's own loaders and guards, never by file name.** P2 via `e15.guard_p2` and `MultiViewHours`; P3 via `e15.guard_p3` and `make_p3_hours` (files are `trades-<hour>.deduped.jsonl.zst`, with the dedupe manifests checked); P4 via `e15.guard_p4` (VIEW.sha256, tiling). Every hour of both series must resolve to an existing file, or the run refuses.
- **Series and carry.** State carries across the walkers of one contiguous series in time order. Two series are scored:
  - `S_P2`: `explore-0814/w1..w7`, `[2026-08-14T12, 2026-08-28T12)`, 14 contiguous days (`w7` first, as the hour list orders them).
  - `S_P34`: `fresh-0903/w1..w3` then `exp011-0909/b,c`, `[2026-09-03T12, 2026-09-15T12)`, 12 contiguous days (P3 ends at the hour P4 begins).
  - State never crosses between the two series, and an hour present in two views of one series is a refusal.
  - P1 sources (the P1A/P1C/P1B tapes, which also carry a derived migration clock for P1B) are **not scored**. They are report-only in EXP-015's bars, and the cache's P1 rows are not used here.
- **Warm-up.** The first **72 h** of each series (to `2026-08-17T12` and `2026-09-06T12`) is excluded from scoring: a migration with `mig_ms` before that is dropped from the frozen book and from both cells. The state is still built during the warm-up.
- **Eviction (memory).** A mint's positions are dropped after 24 tape hours without a row, unless that mint still has a pending snapshot. A trip that would have closed after that is lost. The count of dropped mints and open positions is reported by `--precount`.
- **Reader.** `zstd -dc` exiting non-zero refuses. Rows whose `block_time` lies outside the file's hour are dropped and counted (as `strict_hours` does for creates); a row with no integer `block_time` is kept. Counts are printed in `precount.json`.
- **Late rows.** A row with `t_recv_ms` below an already-taken snapshot's cutoff (hour files out of time order across an hour edge) is applied and counted. It cannot leak: the snapshot was already taken.
- **Own-mint trips.** A wallet's closed trips on the migrating mint itself, before the cutoff, count in its skill. This is causal, and rare (a wallet must buy, fully sell and buy again). It is disclosed, not removed.

## 3. Mint features at the cutoff

Cutoff `C` = the universe row's **`mig_ms`** (milliseconds). This is EXP-012's actual feature cutoff: `tools/exploration_entry_model.py` computes `causal_events(feat.events, mint.mig_ms)`, which keeps events with `t_recv_ms` **strictly before** `mig_ms` (see its docstring: "the single causal boundary a decision-time feature must respect"). The wallet feature copies that boundary exactly (time based, strict `<`, no slot arithmetic), so it sees the same information the frozen model sees. Skill uses all rows with `t_recv_ms < C`. The holder and window features count **bonding-venue rows only**. (EXP-016 cut by slot, strictly before the migration slot; EXP-018 does not.) No migration-row file is read: `mig_ms` comes from the EXP-015 cache rows.

For each mint, at the snapshot (the state after all rows with `t_recv_ms < C`):

| Feature | Definition |
| --- | --- |
| `skilled_holder_lamports` | Sum, over wallets that bought the mint on the curve, are skilled at C and still hold more than dust, of the position's **open cost basis** (SOL bought and still held, at average cost). |
| `skilled_buyer_count` | Distinct wallets that bought the mint on the curve before C and are skilled at C (held or not). |
| `skilled_share` | Skilled wallets' bonding **buy volume** in `[C - 60,000 ms, C)` divided by all bonding buy volume in the same window. 0 when the window has no buy. |
| Reported only | `skilled_holder_count`, `n_buyers`, `n_holders`, `n_wallets`, `n_skilled_wallets`. |

Skill is the wallet's state **at C**, including for the window buys (a wallet that was skilled at C counts for the whole window).

## 4. The two cells (a family of 2, so 2 tries)

- **W1, veto:** frozen EXP-012 selection minus entries with `skilled_holder_lamports == 0`.
- **W2, level filter:** frozen EXP-012 selection restricted to `skilled_share >= tau_d`. For a scored date d, `tau_d` is the **median of `skilled_share` over the frozen-selected scored rows on every other scored date**, with rows whose `mig_ms` lies within 35 min of date d (before its start, or after its end) dropped. This is a nested leave-one-day-out choice. It reads **no outcome**: the median is of a feature, and the scored date's own rows never enter it. The cell's threshold is therefore never fitted on the date it is scored on.

No other cell, threshold, N, window or feature is run. A change is a new plan and a new try count. The tries log gets one `started` line per cell on the universe bookkeeping block, then one `completed` line per cell per pool group.

## 5. Deciding costs (same as EXP-015 / EXP-017)

k = 6 slots, exit lag 2, haircut, 505,000 lamports per side, V pin `pool_v_0909` (sha256 `70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e`), both fail models (flat 15% and pressure-fail at slope scale 1), stake 0.05 SOL. A cell takes the cached EXP-015 net of each migration it keeps, so no fill is re-simulated.

**Nets come from the EXP-015 cache**: `/data/mal/exp015-screen/scratch/cache/v_P*.rows.jsonl`, schema `exp015_tape_cache_v1`, the 12-file manifest (rows plus manifest.json) with sha256 `72b9bd1a355953c114835a77fd7ea2f2273e75b21393cf5be2d48a16523ba31b`, each manifest's head `cc366d4c7d8597d6429575c165f164cce35ce39d` and V-map sha as above. **Reuse is by copy**: the loader, manifest check, paired test, Holm and bootstrap are copied from `tools/exp017_screen.py` at PR #423 (head `1bf5f8bf314da34d3607c725777de28f9a49e8b4`), because #423 is not merged. Each copied function is marked `# from exp017` in `tools/exp018_wallet_skill.py`. The universe is built by `tools.exp015_screen.build_universe`. When #423 merges, a follow-up may replace the copies by an import; it must not change a number.

## 6. Bars (EXP-015 bars 1 to 6, on the scored non-P1 dates; Holm at k = 2)

Scope: the non-P1 rows (P2, P3, P4) with a feature row and past warm-up. The frozen book is restricted to the same rows.

- **Dates after warm-up.** The 27 non-P1 dates are 14 P2 dates (08-15..08-28) and 13 P3/P4 dates (09-03..09-15). The scored dates are those with at least one scored migration. The majority-of-days rule uses **that count**, not 27. The count is printed by `--precount` before `started`.
- **B1 gate** (EXP-015 gate): at least 100 trades, at least 5 dates, majority of dates positive, lower 90% CI bound of the mean above 0 under both resamplers, total above 0 after removing the top 3 trades. Both fail legs.
- **B2 paired vs frozen:** `x_m = cell net - frozen net` per migration (0 where the cell does not enter): mean above 0, date-cluster CI90 lower bound above 0, ex-top-3 of the sum above 0, both legs, **and Holm significance** at family alpha 0.05 over the 2 cells on `p = max over legs` of the one-sided date-cluster bootstrap p (10,000 draws, seed 1).
- **B3 concentration:** no date above 20% of the positive total, ex-best-date total above 0, both legs.
- **B4** P2 + P4: mean above 0, both legs. **B5** P2 (August): mean above 0 and a majority of its scored dates positive, both legs; no level is fitted **on the scored date** (W2's `tau_d` uses the other scored dates only, which include other August dates, so B5 is not an out-of-month replication). **B6** P3 + P4 (September): mean above 0, both legs.
- **All six bars and Holm** for a cell to pass. **PASS = "worth one read".**

## 7. Pre-declared refusals (before `started`: nothing is logged, no try is spent)

1. The cache manifest sha256, a cache head, or the V map sha256 differs from the pin.
2. A prior `exp018` line exists in the tries log (a second run is refused).
3. `--features` missing, or its sha256 differs from the one `--precount` recorded.
4. Any view path matches a reserved fragment (`e15.refuse_reserved`).
5. Two views of one series hold the same hour.
6. A cell, or the frozen book, selects no scored migration.
7. A `RUN.lock` exists (`O_EXCL`); a lock with a started record is final.
8. Planned for the first real `--precount`, **stated before it runs** and applied before `started`: a scored source with feature coverage below 90% (migrations with a snapshot, over universe migrations), or fewer than 100 frozen-selected scored migrations, or no mint with a skilled holder in a scored source. A refusal here is reported, never "fixed" by relaxing N, the window or the cutoff.
9. **Missing tape (R1).** Any hour of a series has no trades file, or a series' guard (VIEW.sha256, tiling, dedupe manifest) fails: refuses in `--precount` and in screen mode. Coverage counts a snapshot only when the series had at least one row before the cutoff.
10. **Tries log.** `--precount` neither resolves nor reads a tries log. Screen mode refuses any `--tries-log` other than the canonical one (`resolve_tries_path(None)`).
11. **W2 degenerate (stated before it runs).** `--precount` reports, blind on outcomes, `tau_d` per scored date. If `tau_d` is 0 (or undefined) on **every** date, `skilled_share >= tau_d` is always true and W2 equals the frozen book: W2 is **dropped before the read**, the family is W1 alone (Holm k = 1, one `started` try).
12. **Tries logs (as EXP-016).** Screen mode writes two logs: an absolute ops log `--tries-log /data/mal/ops/tries-exp018-screen.jsonl` (refused if missing or relative) and the canonical repo `data/tries.jsonl` of the job checkout (`--canonical-tries`, default that path). It refuses if the canonical log does not exist or holds no `exp015_` line, and if **either** log already holds an `exp018` line (checked at the start and again right before the lock). A later sync PR copies the ops lines into the repo, as #408 did for EXP-015.
13. **W2 flag.** Screen mode recomputes `w2_degenerate` from its own masks and refuses if it disagrees with `precount.json`.

After `started`, a failure is reported as `aborted_after_read`; the tries are spent.

## 8. What a PASS earns, and what runs

- A PASS earns **one** confirmation read on an **unread reserved block**, under a later pre-registration that fixes the block, the model and N. It is **not** an edge claim and not a promotion. The promotion gate stays as in CLAUDE.md. A FAIL closes this family: no new N, window, feature or threshold on these pools.
- **Modes.** `--precount` is outcome-blind (cache nets are dropped at parse time by an object hook; `cell_nets` is never called). It streams the two series (2 processes, one per series), writes `features.jsonl` and `precount.json` (per-source rows, wallets, mints with at least one skilled holder, coverage, warm-up effect on n, evictions, late rows). The screen then needs `--features features.jsonl`. The screen writes `screen.json`, `screen.md` and one `result.v1` per cell.
- **Expected cost (an estimate; the real layout has not been read).** The pass is Python JSON parsing of about 53 GB of compressed explore-0814 plus the September series. At an assumed 40k to 80k rows per second per process this is hours, with the two series in parallel; the longer series sets the wall time. Memory is small by design: one hour of compact tuples (about 150 B per row), plus per-wallet arrays (16 B per wallet, plus about 120 B per interned wallet string) and positions of mints active in the last 24 h. The target is at most 40 GB RSS at 4 workers; this tool uses 2 processes, so the budget is generous. `systemctl show user-1002.slice -p MemoryCurrent` is checked before a run. It runs as a MiScusi job on a host with the clean views.
- **The first real `--precount` is the first test against the real layout.** Fixtures cover the row shape and causality, not the loader. The precount may refuse for reasons the tests cannot see; that is the point of it.

## 8b. The exact precount command (run by the manager as a MiScusi job; the worker does not run it)

```
TS=$(date -u +%Y%m%dT%H%MZ)
cd ~/MAL && /data/mal/venv/bin/python -m tools.exp018_wallet_skill --precount --workers 2 \
  --scratch /data/mal/exp015-screen/scratch --vmap /data/mal/pumpswap-virtual/pool_v_0909.json \
  --out-dir /data/mal/exp018-precount-$TS
```

Views are the defaults in `SERIES` (`explore-0814/w1..w7`; `blocks-clean/fresh-0903/w1..w3`; `clean-view/exp011-0909/b,c`). `--workers 2` is one process per series (the tool caps it at 2). The precount writes `features.jsonl` and `precount.json` into the out-dir; the screen run then takes `--features /data/mal/exp018-precount-$TS/features.jsonl --out-dir /data/mal/exp018-precount-$TS`.

**Screen command** (after the precount, run from the job checkout so `data/tries.jsonl` is the repo's):

```
cd ~/MAL && /data/mal/venv/bin/python -m tools.exp018_wallet_skill \
  --scratch /data/mal/exp015-screen/scratch --vmap /data/mal/pumpswap-virtual/pool_v_0909.json \
  --features /data/mal/exp018-precount-<TS>/features.jsonl --out-dir /data/mal/exp018-precount-<TS> \
  --tries-log /data/mal/ops/tries-exp018-screen.jsonl
```

## 9. What it cannot show, and the honest prior

- Skill is trade-flow on one address. SPL transfers and wallet rotation are not on the tape, and skilled wallets can be sniper bots whose realized P&L is a latency edge that is not copyable at slot + 6.
- Skill needs history. On a freshly launched ecosystem most wallets have fewer than 5 closed trips, so `skilled_holder_lamports == 0` may be true for most entries, which makes W1 a coarse filter. The precount shows how many selected rows have a skilled holder.
- The skilled holders in a pool are partly the same wallets that front-run the migration. Their presence may mean the entry is already crowded.
- The frozen cell is best-of-many; a filter on top inherits that. The 27 dates are the dates EXP-015 already read, so the pair of cells is a further look, not an independent sample.
- **Shared seed.** The date-cluster bootstrap uses seed 1, as EXP-015 and EXP-017 do, so the draws share their resampling pattern with those screens' on the same dates. The p-values are not independent evidence across screens.
- Prior of a screen pass about 5%, mostly for the reasons above. A pass would still be one read of an unread block.

## 10. Revisions before the pin

2. 2026-10-06, quant-proof on #424 at f5e0dae: P3/P4 hours now resolve through EXP-015's guards and loaders (the first draft's `trades-<h>.jsonl.zst` lookup missed P3's `.deduped` names); bonding fee charged per side (R2); missing-hour, tries-log and W2-degenerate refusals added; precount prints the skilled-holder and skilled-share shares and `tau_d` per date; zstd failure refuses; out-of-hour rows dropped.
1. 2026-10-06, manager ruling on #424: the cutoff is EXP-012's `causal_events(feat.events, mig_ms)` boundary (time based, strict `<` on `t_recv_ms`), replacing an earlier draft that used migration slot + 1. The 60 s window is 60,000 ms.
