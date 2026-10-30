# PREREG: KCURVE-BOOST-RIDE (library row `eh1030-kcurve-boost-ride`), text for the QP pre-check

**Status: frozen text, NOT declared, nothing priced.** Written by lane kcurve-boost on 2026-10-30 (`date -u` 2026-10-30T21:36Z), branch `claude/kcurve-boost-prereg` from main `afc876ad`. It finalises the skeleton `docs/research/edge-hunt-1030/PREREG-KCURVE.md` of PR #1084 (head `2f63206f`). No return, fill, exit, P&L or any price after a pool's first slot was computed for this file. The only data touched is the structure probe in section 1.2. It parsed tape rows (see 1.2 for exactly which) but computed and printed only counts, completion-tx fields, and each pool's first-slot prints (its s0 opening state); no field of a later print entered any computation or output. It is declared (sha256 in a MiScusi notebook entry) only after quant-proof's pre-check, and priced only after that (section 12).

Exploration only, on blocks the lab has already read many times. A pass is in-sample and is never gate evidence. No book has cleared the promotion gate.

## 1. Data validity: can August/September tape price this mechanism?

**Answer: the ride yes, the October seat only as an upper-bound proxy. So the K-1 cell can KILL the idea but can never support it.**

### 1.1 What changed on 10-07/10-08
- Pump docs `SYNTHETIC_MIGRATION.md` (repo `pump-fun/pump-public-docs`, commit of 2026-10-07): a `buy_v3` / `buy_exact_quote_in_v3` that empties a non-Mayhem curve buys the rest of the curve at curve prices, then any extra amount "from the tokens that would have gone into the PumpSwap pool, at the price that pool would charge". Both parts pay the bonding fee schedule. After it, every curve trade fails until `migrate_v2`. Mayhem coins are excluded.
- On chain: the pump program was redeployed at slot 454,596,459 (2026-10-08T16:20:17Z), per the A3 structure-monitor record (`ARTIFACTS/lab/a3-repin-2026-10-13-step3-record.jsonl`, `programs.pump.deploy_utc`). Synthetic graduations appear after that instant. Their sampled share is about 0.2 (structure monitor, `/data/mal/structure-monitor/daily.jsonl`, about 19 graduations a day; 0.000 to 0.474 per day, 10-08..10-30, copied from the edge-hunt NOTE).
- **Before 10-08 the seat existed, but capped:** the buy that crossed the curve's limit bought at most the curve's remainder (a larger buy failed with `NotEnoughTokensToBuy` or stopped at the remainder with `partial_fill`). That buyer held tokens bought at curve prices up to the curve's end price, ahead of every pool print. The October change removes the size cap. It also removes the race with `migrate_v2` for any *extra* size.

### 1.2 Structure probe (exploration blocks, counts and s0 states only)
Scripts and their printed outputs: `docs/research/kcurve-boost/probe/` (structure_probe.py run on explore-0814/w1, 2026-10-30T21:25-21:32Z; universe_probe.py on 6 hours of three blocks, about 21:40Z; both under a MemoryMax=8G scope, no MiScusi job). What was parsed: structure_probe.py parsed every row of the completed mints in the completion hour's trades file and the next one (bonding and PumpSwap rows, later prints included) and used only the bonding rows up to the completing trade, each pool's first print slot and the prints of that slot; universe_probe.py parsed every WSOL PumpSwap row of the sampled hours and kept only each pool's first-slot prints, plus the blocks' create rows. No price, reserve or side of any print after a pool's first slot entered a computation or an output.

| Measure | explore-0814 w1 (48 h, 08-26T12..08-28T12) | fast-pool 6 h (09-20T00..05) | oracle-live 6 h (09-26T00..05) |
| --- | --- | --- | --- |
| WSOL `complete` rows (48 h) | 2,870 (about 1,435 a day) | not run | no `migrations/` files in this block |
| of those, with a V-band pool printing in the same or next hour file (48 h) | 1,937; a unique first-slot root on 1,931 | | |
| V-band pools whose first-slot root has the seed base reserve 206,900,000,000,000 (6 h: 08-27T00..05, 09-20T00..05, 09-26T00..05) | 219 | 174 | 197 |
| of those with a create row in the block: Mayhem (6 h) | 0 of 204 | 0 of 173 | 0 of 152 |
| of those: organic, s0 minus create time >= 60 s (6 h) | 92 of 204 | 44 of 173 | 42 of 152 |
| seed pools with no create row in the block (6 h; w1's creates only for explore) | 15 of 219 | 1 of 174 | 45 of 197 (the pumpportal create feed of this block misses mints) |
| other root base reserves among first-seen V-band pools (6 h) | pools that opened before the sample (not seed) | | |
| opening effective price (q0 + V) / b0 against the completing trade's post-trade curve price, percent | p5 -0.0244, p50 -0.0244, p95 +0.2081 (n 1,931) | | |
| opening quote, median: real / effective (with V) | 67.4059 / 84.9904 SOL | | |
| completing curve buy, SOL (all completes, instant launches included) | p10 0.0092, p25 0.0517, p50 0.9037, p75 24.7584, p90 85.0054; share >= 0.25 SOL 0.5979 | | |
| completer wallets | 1,623 distinct; top 5 hold 24.32% of completions | | |
| curve buys in the completion slot / in the 6 slots up to it (medians) | 1 / 3 | | |
| first pool print slot minus completion slot | 0: 931; 1: 148; 2: 167; 3: 292; 4: 197; 5: 86; 6..9: 85; >= 10: 31 | | |
| the completer is the pool's first printer | 1.35% | | |

What this settles:
- **Entry price.** The pool opens at the curve's end price to within -0.0244% (median and p5..p75), so a 0.25 SOL buy on the opening reserves (q0 + V, b0) is the price a synthetic completer pays for its pool part in October, before its own impact (applied in the pricing). The October pool-part formula (docs) prices on the total quote the migration deposits; the measured opening effective quote 84.99 SOL = real 67.41 + V 17.58 matches that total. So the proxy needs no adjustment for V.
- **Fee.** The engine's fee at the opening state is the [0, 420) SOL market-cap tier (opening market cap about 410.8 SOL), 12,500 ppm. The completer pays the bonding schedule, 125 bps in total (A3 pin `bonding_fee_config` tier0_total 125). They are equal.
- **The ride.** Every input after the seat (the pool's prints, V, BOOST's 17.585 SOL TWAP, live since 2026-07-21 per `docs/research/p95/CENSUS.md` row 1) is on the August/September tape, as in October.
- **Volume.** About 1,435 WSOL graduations a day in late August, of which roughly 25-45% are organic (6-hour samples). The skeleton's "about 20 a day" was the structure monitor's sample size, not the graduation count. Capacity is set by how many completions MAL can win, not by how many exist.

What Aug/Sep cannot price (so K-1 is an upper bound):
1. **P(win).** Whether MAL's completing buy lands first. October competition for the completing buy is not on this tape: in August there was little contention (median 1 curve buy in the completion slot), but the seat was worth less then (capped size).
2. **Selection.** Which completions MAL would win. If MAL wins mainly the quiet curves, its pools are not the average pool priced here.
3. **Flow response.** When a completer already holds a pool part at open, the first snipers pay more; the pricing holds other traders' reserve deltas fixed (the lab's method).
4. **Regime.** October flows (PumpSwap volume x0.42-0.50 on 10-28/29 against 10-20..23, edge-hunt NOTE) and the decay of BOOST-family edges by block (H5 block means, in-sample exploration figures: +26.7 / +11.7 / +5.4 / +6.8%).

### 1.3 The cheapest valid tests of the October seat itself (proposals, not run)
- **T1, structure counts on released October hours (no outcome).** The ledger's F1 release (2026-10-15 entry; in effect once the EXP-012 FINAL marker existed: job #1039 printed FINAL_OK at 2026-10-16T04:18:53Z) opens domain D0 (structure) of the Forward walk row `forward-1002` `[2026-10-02T10, 2026-10-16T01)` for exploration. Its allowed list includes "synthetic class and PostCompleteBuy from the curve-completing tx", identities and event counts. The catalog role `oct-structure-d0` (`tools/mal_catalog_october.py`, merged #660/#670) enforces it. The synthetic-era part is `[2026-10-08T16:20:17Z, 2026-10-16T01)`, about 7.4 days. Measures: synthetic share of organic non-Mayhem completions per UTC day; distinct completers and the top-5 completer share (the `trader` of the completing trade row, kept by the D0 mask); `complete` to pool-create slot gap (D0 `slot_gap`). Not checked here: whether forward-1002's decoder wrote `post_complete_buy` rows for those hours (the D0 module lists the type; the walk was not opened). Not available under the current mask: the pool-part `quote_in` (kept only on a row signed by the mint's migrate tx). Kill lines as `p95-synthetic-completer`: top-5 completers >= 80% of synthetic completions (a held seat), or synthetic share < 10%. Zero Helius, no price. Needs QP's yes (question Q6). Not run here: this lane's brief says "no October data".
- **T2, pricing October synthetic pools.** Not before EXP-024's end state (first possible 2026-11-14T00:00Z; EXP-024 §3 Look-2 seal on V-range pools in their first 360 s). On forward-1002 hours it also needs an owner ledger edit releasing D2/D3 ("released hours never confirm", so exploration only).
- **Gate evidence** comes only from a forward book registered at or after the Look-2 end state.

## 2. Question and rule groups
A buyer holds a canonical pool's tokens from the seat and sells into BOOST's TWAP at s0 + 330 s. What does it earn, net of the arena cost stack, by seat?
- **Group A, the seat:** K-1 (the synthetic completer's seat, proxied by the opening reserves).
- **Group B, the latency curve:** K0, K1, K2, K3, K5, K8, K12 (landed slot m + k). It answers whether a paid speed path (2-4 slots reachable, edge-hunt NOTE section 1.3) could be worth money on migrations.
Eight cells in all (at most 12 per agent).

## 3. Data
- **Deciding blocks** (`tools/arena_screen.py` `BLOCK_ROOTS`): `/data/mal/clean-view/explore-0814` (w1..w7, one block) and the three September blocks pooled as ONE period (`oracle-insample-2026-09-22_25`, `fast-pool-2026-09-18T23_2026-09-22T00`, `oracle-live-2026-09-25_27`), hunt23 Amendment 1 (b).
- **Report-only:** `/data/mal/clean-view/fresh-0903`, priced alone, never in a verdict (the kit's `EXTRA_SOLO_BLOCK_ROOTS`).
- **Not used:** any October hour of any source; fresh-08*, exp011-*, forward-*, truth-*, arena tape, tip tape, DBC, LaunchLab, meta-capture, walk-2, Q50/s1/HV4 data.
- **V:** the kit's `VMap` over `/data/mal/pumpswap-virtual/pool_v_0814.json` then `pool_v_0909.json` (sha256 pins in section 12); the first non-null wins; unknown V = outside.

## 4. Universe U (structure only, fixed before COUNTS)
A pool is in U if all hold:
1. **Canonical.** WSOL-quoted PumpSwap pool, V in [17.5e9, 17.7e9] lamports (DEC-031 Am.2 rent universe; `boost-universe-3`: BOOST budget = V).
2. **Opens in the block.** Let m be the slot of the pool's first print in the block. Slot m has exactly one root print: a print whose pre-trade base reserve is not the post-trade base reserve (pre-trade base minus `token_raw` for a buy, plus for a sell) of any other print of slot m. Its pre-trade base reserve equals 206,900,000,000,000 (the canonical seed). The opening state is (q0 + V, b0), the root's pre-trade reserves. This needs no within-slot order, so it holds on oracle-live, which has no `tx_index`. s0 = the root's block time (`block_time`, else `event_ts`).
3. **Non-Mayhem.** The mint's create row in the same block has `is_mayhem_mode` false (oracle-live: `ws_payload.is_mayhem_mode`). A pool whose mint has no create row in the block is excluded and counted (for oracle-live this is a feed gap; reported).
4. **Organic.** s0 minus the create time >= 60 s,. Create time = `block_time` (backfill blocks) or `t_ws` (oracle-live; a processed receive time, disclosed). Instant launches are excluded because their launcher's bundle holds the completion, and in October it can also take the synthetic seat.
5. **Block edge.** A trip whose exit state would need slots past the block's end is excluded and counted (the kit's rule; above 5% of a cell's attempts makes the cell not decidable).
Report-only: the same cells on U without item 4.

## 5. Cells (entry seat)
Stake 0.25 SOL deciding; 1.0 SOL report-only. One trip per pool per cell.

| Cell | Entry state the buy lands on (pre-trade, Q includes V) | Pressure inputs (ssb, nearby) | Guard reference |
| --- | --- | --- | --- |
| K-1 | the opening state (q0 + V, b0): ahead of every print | buys in slot m; buy lamports over slots [m - round(2 / s), m] | opening price (q0 + V) / b0 |
| Kk, k in {0, 1, 2, 3, 5, 8, 12} | END of slot m + k: the pre-trade state of the first print at slot >= m + k + 1 (or the state derived after the last print), i.e. `arena_stack.mint_states` with decision slot m + k and latency 0 | `mint_states` at x = m + k | opening price |

- s = seconds per slot of the hour of m, from the hour's own prints (the kit's `slots_per_s`).
- The 1.15 guard (a buy reverts and pays one send if its execution price is above 1.15 x the reference) uses the opening price for every cell: no cell buys more than 15% above the price the seat saw. For K-1 it never binds (own impact about 0.3%).
- Report-only: START of slot m + k for k >= 1 (the pre-trade state of slot m + k's first print in tape order); each k also in seconds (k x s, per block).

## 6. Exit (every cell)
- Exit decision slot xs = m + rint(330 x slots per second of the hour of m) (the kit's `_exit_clock` with hold 330 s from m, so the exit is tied to s0, not to the entry).
- The sell lands on the END state of slot xs + 7 + exit_lag (exit_lag = ceil(0.55 s / s) >= 2 slots): `arena_screen.pool_states` exactly (feed delay 7, `F.exit_lag_vec`). This is the realistic lag-2-or-more exit (memory note `feedback-realistic-primary`), plus the arena's 7-slot feed delay.
- Late prints and the block edge: as the kit's `_price_store` (`need` = xs + 7 + lag + 1; a trip whose first print at or after `need` is at or after `need` + 10,000 slots is re-derived on the history before `need`; no print and `need` + 10,000 past the block's last slot = excluded).
- Report-only exits: s0 + 300 s and s0 + 360 s.

## 7. Costs and the pricing tool
- **Stack:** `arena_screen.screen_cfg(rent=0, send=505_000)` through `arena_screen.legs` (the kit's vectorised `arena_paper.price_leg`: fee tier on (Q incl. V, B), haircuts 0.2608% in / 0.16% out, E1 floor 384,022 lamports per 0.05 SOL, 505,000 lamports per send, 1.15 guard, own buy re-injected at the exit, strand rules "total" (deciding) and "sc").
- **Fail models:** flat 15%, and pressure at slope scale 1 with the intercept refit on each cell's own fills (mean p 0.289), both via `arena_paper.book_fail_legs` inside `legs`. At K-1 the October fail is losing the completion race; that is not modelled beyond these two models (section 1.2, item 1).
- **Report-only legs:** rent-charged (2,039,280 lamports per filled trip); send 160,000 (`screen_cfg(send=160_000)`, DEC-031 Am.8 stack); 1.0 SOL stake with entry impact net_in / Qe at 0.25 and 1.0 SOL.
- **Tool: an adapter, not a copy.** `docs/research/kcurve-boost/kcurve_price.py` (to be written after the pre-check, its sha256 pinned in the DECLARE). It loads hours with the kit's own `discover`, `check_block_path`, `iter_hours` and `VMap` (so the kit's path guards apply), builds each trip's state dict (`ref`, `Qe`, `Be`, `ssb`, `nearby`, `Qx`, `Bx` and the worst-in-slot `wQe`, `wBe`, `wQx`, `wBx`) with `arena_stack.mint_states`, `arena_screen.pool_states` and `arena_report_legs.state_arrays` / `slot_states` / `worst_index`, and then calls the kit's own `legs`, `stat`, `gate_row`, `kill_verdict`, `worst_in_slot_legs` and `screen_p_cell`. Every pricing and statistics function is imported, never copied. What differs from `tools/arena_screen.py`: the entry slot is the seat (sections 5 and 6) instead of decision + 7 + k(latency), and the kit's 360-s pool-age refusal is not applied (QP question Q1).
- **Parity before pricing:** (i) fixture tests: for hand-built pools, `legs` on the adapter's states equals `arena_paper.price_leg` trip by trip to 0 lamports (at least 200 cases), and the Kk entry state equals the kit's `pool_states` entry for a decision slot chosen so that its landing slot is m + k; (ii) at pricing time, the kit's END-consistency check (`w_mismatch`) must report 0 trips, or worst-in-slot is "not measured" and every cell is PROVISIONAL.
- **Outcome-blind precount first** (`--precount`): a full pass over every deciding block on the real layout that prints per-block counts and refusals and no price (memory note `feedback-real-layout-precount`).

## 8. Statistics and kill lines (deciding: 0.25 SOL, rent 0, send 505,000, pooled over the four blocks)
A cell is KILLED if `arena_screen.kill_verdict` returns kill on its four sub-cells {flat, pressure} x {total, sc}, with `min_att_per_day` 20 (RULES step 2) and `min_blocks_positive` 3. That is, any of:
- (a) mean, total ex-top-3 or total ex-best-day <= 0 in any sub-cell;
- (b) the gate in-sample, pooled: n < 100, < 5 UTC dates, the trade-level CI90 lower bound <= 0 (1,000 draws, seed 1, 5th percentile, `arena_stack.boot_lo`), the UTC-date-clustered CI90 lower bound <= 0, or no majority of dates positive;
- (c) fewer than 3 of the 4 canonical blocks positive in all four sub-cells;
- (d) mean <= 0 in any sub-cell on explore-0814 alone or on the September blocks pooled;
- (e) block-edge exclusions above 5% of the cell's attempts;
- (f) **worst-in-slot (QP R7):** the lower of the worst-in-slot and no-guard-rescue legs (`worst_in_slot_legs`) has mean <= 0 in any sub-cell. Every cell is event-triggered (the migration). For K-1 the entry state set is the opening state only (the seat precedes every print); its exit uses the exit slot's worst state like every cell. Not measured = PROVISIONAL, never a survivor.
- **Survivors:** at most one per group, picked by the highest pooled pressure-model "total" mean at 0.25 SOL, rent 0. This statistic is fixed here. At most two in all.
- Screen p (EXP-028 kit statistic) is reported and never read by the verdict.

## 9. What each verdict means
- **K-1 KILLED:** `eh1030-kcurve-boost-ride` becomes `explored-no-survivor`; `p95-synthetic-completer` loses its pricing route; T1 is not run for it; no live completion probe is designed. The upper bound failing kills the October seat too, unless a new pool universe or exit is named.
- **K-1 survives:** it licenses only (1) T1 (section 1.3), and (2) after T1 passes, a design note for a live completion probe (`buy_exact_quote_in_v3` sized to the curve remainder plus stake), which needs the owner's go and DEC amendments. (3) A new pre-registration that prices October synthetic pools, not before EXP-024's end state. No forward book and no live trade on this proxy alone.
- **Group B all KILLED:** paid speed for migration entries stays NO-GO. A Group B survivor at k >= 2 licenses only a costed speed plan (edge-hunt NOTE section 1.3), not a book.

## 10. Lineage and multiplicity (disclosed)
- Migration-entry family, read many times on these blocks: EXP-012 (migrate entry; its frozen migrate-direct cell `lib-c-Q2.12` FAILED its forward read 2026-09-28), the unselected migration book (0 of 1,008 cells robust at probe fees; `d05_thesis_migration.md`), `speed-route-3` (migrate_v2 + buy bundle, parked), `lib-a-A1` (pre-/mid-BOOST entry, no survivor), H5 (`lib-c-K1`; this file reuses its s0 + 330 s exit).
- New here: the seat at the opening reserves ahead of every print (K-1), the exit tied to BOOST's end on every migration (not a drained-pool trigger), and the kit's full V-priced cost stack with worst-in-slot.
- Eight cells, in-sample, on blocks read by many families. A survivor is a hypothesis.

## 11. Seals (pasted verbatim from `/data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md`, sha256 `823cc062f25415fa4bd4a248646d77c5eafff89b024a9b7437f59a4d365ed6b4`)

> # Active seals (copy into every tape-touching brief) — from docs/HANDOFF.md §2 at 12282a82, 2026-10-25
> - HV4: never open ledgers, fills, prices, exits, positions, P&L, decision-row contents, bots.json, attempt counts (except the pinned count tool at its pinned use), score/preview/am6_read output, HV4 job #1561/#1562 status view or log tail. No pricing or retune of any HV4 rule on counted hours (>= 2026-10-25T00Z).
> - Q50: same output classes; never open shadow #1419/#1421/#817 or canary outputs. EXP-024 §3 Look-2 seal to its end state (first 2026-11-14T00:00Z): no H5 outcome on any October hour of a V-range pool in its first 360 s, from any source.
> - DBC: forward window [2026-10-25T00Z, 2026-11-01T00:10Z) and fwdv1/ are sealed. [2026-10-17T00, 2026-10-24T00) is SPENT permanently for m10 and every D1 variant (no count, screen, engine or price there; decode-only per read-hardening item 6).
> - Arena tape (/data/mal/arena/tape): counts and creates only under QP rulings; never join to HV4/Q50 outputs before their reads; a rule priced on in-clock archive rows (>= 2026-10-25T00Z) is in-sample, never OOS for any book.
> - Check the ledger (D2/D3 domain seals) before pricing any October pool, tip tape included.
> - Gate unchanged. No book has cleared the promotion gate. Paper only.
> - META-CAPTURE (walk-2 Job A, /data/mal/meta-capture/ incl. creates/, capture/, img/, arena-backfill/): SEALED per /data/mal/meta-capture/SEALED.txt + /data/mal/hunt-1008/ops/QP-RULING-1017-meta-capture.md. Nobody lists, reads, copies or fetches from these files before the owner's per-domain walk-2 release in docs/HOLDOUT_LEDGER.md. Counts/hashes only, and only by pinned/QP-ruled tools. For tests use exploration uris (see SEALED.txt). (added 2026-10-25 after seal contact n_?; manager16)
> - WALK-2 hours in general: forward-1016 and arena tape are walk-2-hour information (DEC-030 §2 R1); any result built on them must be disclosed as such.

How this test meets them:
- No October hour of any source is read or priced; the fresh-08* reserve (fresh-0802, the last unspent block) is not touched. Neither EXP-024 Look-2, D2/D3, the DBC windows, the arena tape nor meta-capture is involved.
- Every cell trades V-range pools in their first 360 s, which is allowed on August/September exploration blocks (H5 itself was priced there in its first 300 s). It conflicts with hunt23 RULES Amendment 1 item 3 and the kit's mandatory `MIN_POOL_AGE_S` refusal, which protect EXP-024 Look-2 for rules that become arena books. This is a proxy study: no forward clock of any rule of this family starts before EXP-024's end state (first 2026-11-14T00Z), and any later registration is its own pre-registration. QP question Q1.
- C1-NF (EXP-025 §4; DEC-031 §3.7) decides from graduation + 10 min to + 24 h; every cell here decides within 12 slots of s0 and exits at s0 + 330 s, so no cell matches its grid.
- DEC-031 §3.1: no r4b, l3 or LAYA model or feature.

## 12. Process and pins
1. Quant-proof pre-check of this text (questions in the PR body). Pricing waits for its OK.
2. Adapter PR (`kcurve_price.py` + tests, section 7 parity), QP + reviewer.
3. DECLARE: this file's sha256, the adapter's and the kit's (`tools/arena_screen.py` b37282c7d23d3bfca310f2574b8bc2b9b0981e0e73ee2537e118f779244fbb57, `tools/arena_stack.py` a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778, `tools/arena_paper.py` 9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514, `tools/arena_report_legs.py` 65f4d471e3ad19308a038c4d6563f22322134013f09c0afe2d64c648964a0d8b, V maps `pool_v_0814.json` 2506f7d2d8475e44ca70a8c536dbb7405930b1092edca331dbbe611236b4d2f8 and `pool_v_0909.json` 70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e), in a MiScusi notebook entry with its instant, before the first price.
4. COUNTS.md from `--precount` (attempts per day per cell per block; organic share; root uniqueness; missing creates; Mayhem; block-edge exclusions; the share of K-1 trips whose opening fee tier is not 12,500 ppm; completing-buy sizes where `migrations/` exists). Pre-declared refusals, checked before pricing: a canonical block with fewer than 100 U pools; root not unique on more than 2% of a block's seed-opening pools; V unknown on more than 5% of a block's U candidates.
5. Price: one MiScusi job on mal-research-0 under `systemd-run --user --scope -p MemoryMax=12G`, at most 2 workers, after checking `systemctl show user-1002.slice -p MemoryCurrent`.
6. REPORT.md (at most 1,200 words), library rows updated, quant-proof before any sentence about money.

## 13. Questions for quant-proof (pre-check)
- **Q1 (360 s).** May this exploration-only proxy price pools in their first 360 s on the four Aug/Sep blocks, outside the kit's mandatory refusal (hunt23 Amendment 1 item 3), given that no October hour is read and no forward clock of the family can start before EXP-024's end state?
- **Q2 (tool).** Is the adapter (section 7: seat states built from `mint_states` / the root index; every pricing and statistics function imported from the kit) acceptable in place of `tools/arena_screen.py` (hunt23 Wave-2 rule 3)? Is the parity standard enough?
- **Q3 (validity).** Is K-1 on the opening state a valid KILL-ONLY upper bound for the synthetic completer's seat, given the measured continuity (-0.0244% median) and the four gaps of section 1.2? Is a K-1 pass correctly limited to section 9?
- **Q4 (universe).** Root-chain opening plus the exact seed base reserve; Mayhem from the create row (0 Mayhem among 529 sampled V-band seed pools with a create row); organic by create time (t_ws on oracle-live); missing create rows excluded (23% of oracle-live's sample: its create feed has gaps).
- **Q5 (K-1 fail).** Slot-m pressure inputs for the completion seat, with the race itself unmodelled and disclosed.
- **Q6 (T1).** May a D0 read of forward-1002 `[2026-10-08T16:20:17Z, 2026-10-16T01)` through `oct-structure-d0` count synthetic share and completer concentration now (no outcome; EXP-024 Am.4 item D1 seals joining the synthetic class to an H5 outcome, which T1 does not do)? Does the pool-part `quote_in` need a mask extension?
- **Q7 (guard).** The opening price as the 1.15 guard reference for every cell.
