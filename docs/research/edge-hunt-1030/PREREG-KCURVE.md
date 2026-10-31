# PREREG SKELETON: KCURVE-BOOST-RIDE (library row `eh1030-kcurve-boost-ride`)

**Superseded (2026-10-31):** the registered text is `docs/research/kcurve-boost/PREREG.md` (merged #1125); the result is `docs/research/kcurve-boost/REPORT.md` (all 8 cells KILLED, QP-RULING-1031-kcurve-price). This skeleton is kept unchanged below as the original edge-hunt draft.

**Status: SKELETON. Not registered, not declared, and nothing has been priced.** Written by lane edge-hunt on 2026-10-30 (`date -u` 2026-10-30T15:19Z), on branch `claude/edge-hunt-1030`, from main c92f23e3. Before anything is priced, it becomes a DECLARE.md with its sha256 in a MiScusi notebook entry, per the hunt23 screen protocol. The open items in section 9 are settled before that, in the DECLARE, not after.

Exploration only, on blocks the lab has already looked at. A pass here is in-sample and is never gate evidence. No book has cleared the promotion gate.

## 1. Question

Take a buyer who holds a canonical PumpSwap pool's tokens from the pool's opening price and sells into pump.fun's BOOST TWAP. What does that buyer earn, net of the arena cost stack, as a function of entry slot k = -1, 0, 1, 2, 3, 5, 8, 12 after migration?

- **k = -1** is the seat that synthetic migration (pump docs `SYNTHETIC_MIGRATION.md`, live since 2026-10-07) gives the buy that completes a non-Mayhem curve: priced on the reserves the pool will open with, before `migrate_v2` and before any pool print. On August and September tape the seat did not exist, so this is a **proxy**: the opening reserves are taken as what a completer would have seen.
- **k >= 0** is the latency curve of the same ride. It answers whether any paid speed path (2-4 slots reachable, NOTE.md section 1.3) could be worth money on migrations.

## 2. Data (exploration blocks only; hunt23 RULES.md)

- `/data/mal/clean-view/explore-0814` (w1..w7, 2026-08-14 to 08-28): deciding block for "explore alone".
- `/data/mal/clean-view/oracle-insample-2026-09-22_25`, `/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00`, `/data/mal/clean-view/oracle-live-2026-09-25_27`: pooled as ONE September period (Amendment 1 (b)).
- V maps: `/data/mal/pumpswap-virtual/pool_v_0814.json`, then `pool_v_0909.json` as fallback (the `tools/arena_screen.py` default).
- No October hour of any source. No fresh-08*, exp011-*, forward-*, truth-*, arena tape, DBC, LaunchLab or meta-capture path.

## 3. Universe (structure only; fixed before COUNTS)

A mint is in the universe if all of the following hold:
1. It has a pump `complete` row and a canonical PumpSwap first print in the block.
2. It has a WSOL quote, and V is in [17.5e9, 17.7e9] lamports (DEC-031 Am.2 rent universe; the V-range proxy for a BOOSTed canonical pool: `boost-universe-3`).
3. Its create row has `is_mayhem_mode` false. Mayhem coins get no synthetic migration.
4. It is **organic**: complete block_time minus create block_time >= 60 s, and the create tx is not the complete tx. Instant launches are excluded because their launcher's own bundle holds the completion; in October it can also take the synthetic seat.

Report-only: the same cells on the universe without rule 4.

s0 = block_time of the pool's first PumpSwap print. m = the slot of that print.

## 4. Cells (8, at most 12 per agent)

| Cell | Entry | Entry fee |
| --- | --- | --- |
| K-1 | buy 0.25 SOL on the opening reserves (pre-trade reserves of the first print, plus V), ahead of every print | bonding schedule 1.25% (the completer pays the curve schedule; the same rate as the PumpSwap [0, 420) tier) |
| K0, K1, K2, K3, K5, K8, K12 | buy at the END of slot m + k (after every print of that slot; the worst position in the slot for a buy); START reported for k >= 1 | PumpSwap tier fee on (quote + V, base) |

- **Exit (all cells):** sell at the first print with block_time >= s0 + 330 s, at the arena exit lag (`ceil(0.55 s / sps)`, at least 2 slots, `tools/arena_paper.py`). Report-only exits: s0 + 300 s and s0 + 360 s.
- **Own trade applied.** Other traders' reserve deltas are held fixed, which is the lab's method; it understates impact in a draining pool (H5 REPORT caveat). Report the impact at 1.0 SOL.
- **Cost stack:** price with the engine's own functions (`tools/arena_paper.StackConfig`/`price_leg`, `tools/arena_stack.mint_states`/`round_trip`; template `/data/mal/hunt-1008/push-1016/hunt21/screen-1019/`). Send cost 505,000 lamports per send (deciding, DEC-031 §1.4); 160,000 reported. Rent 0 deciding (Am.2 universe), and rent-charged legs (2,039,280 lamports) reported.
- **Fail models:** flat 15% and pressure at slope 1. Strand rules "total" and "sc": **four deciding sub-cells** (Amendment 1 item 1).
- **Latency in seconds:** report every k also as k x the block hour's measured seconds per slot. MAL's best realistic path (about 0.5-0.8 s) is about k 2 on August slots.

## 5. Pre-stated kill lines

COUNTS.md comes first, then pricing. A cell under 20 attempts per day on explore-0814 is dropped without pricing (protocol step 2).

**K-1, the synthetic-completer door.** It is KILLED if any of the following holds:
- (a) the mean, the ex-top-3 total or the ex-best-day total is <= 0 on any of the four deciding sub-cells, pooled;
- (b) the mean is <= 0 under either fail model on explore-0814 alone, or on September pooled;
- (c) the in-sample gate fails on any deciding sub-cell, pooled. The gate here means: CI90 lower bound of mean SOL per trade > 0 (1,000 bootstrap draws, seed 1, 5th percentile), a strict majority of UTC dates positive, ex-top-3 > 0 and ex-best-day > 0.

There is no worst-in-slot (R7) issue at K-1, because the cell is first in the pool by construction. For k >= 0, END is the worst position in the slot.

If K-1 is killed:
- `eh1030-kcurve-boost-ride` becomes `explored-no-survivor`;
- `p95-synthetic-completer` loses its pricing route: no counts test is run for it;
- no live completion probe is designed.

**Paid speed (`eh1030-paid-speed-track`).** If every cell K2..K12 fails (a)-(c), the paid speed track for migration entries stays NO-GO. If a cell at k >= 2 passes, the next step is a costed speed plan (NOTE.md section 1.3), not a book.

**Survivor pick (Amendment 1 (d)).** At most one survivor in this rule group: the passing cell with the highest pooled pressure-model "total" mean. This statistic is fixed here.

## 6. What a PASS licenses (and nothing more)

1. The `p95-synthetic-completer` counts test: the top-5 signer share and the median pool-part `quote_in` of October synthetic completions. Its enumerator needs a QP ruling first (QP ruling on #1055, R1/R2).
2. A design for a live completion probe: `buy_exact_quote_in_v3` sized to the curve remainder plus stake, on organic curves with <= 0.25 SOL left. It needs the owner's go and a DEC-019 amendment.
3. A new pre-registration that prices October synthetic pools, **not before 2026-11-14T00:00Z**, the first possible EXP-024 Look-2 end state.

It licenses no forward book and no live trade on this proxy alone.

## 7. Seals (pasted verbatim from /data/mal/hunt-1008/push-1016/hunt30/SEALS-BRIEF.md, sha256 823cc062f25415fa4bd4a248646d77c5eafff89b024a9b7437f59a4d365ed6b4)

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
- It uses no October hour, so neither EXP-024 Look-2, D2/D3, the DBC windows nor the arena tape is touched.
- Every cell trades V-range pools in their first 360 s. That is allowed on August and September exploration blocks. It is the reason no forward clock of any rule from this family can start before the Look-2 end state (first 2026-11-14T00Z).
- hunt23 Amendment 1 item 3 (pool age >= 360 s) is written for rules that become arena books. This test is a proxy study, not a registered rule, and it says so. If a registration follows, it is dated at or after the Look-2 end state.
- C1-NF (EXP-025 §4; DEC-031 §3.7): C1-NF decides from grad + 10 min to grad + 24 h. Every cell here decides within 12 slots of the first print, so no cell matches C1-NF's grid.
- DEC-031 §3.1: no r4b, l3 or LAYA model or feature is used.

## 8. Process

1. Write DECLARE.md (this skeleton with section 9 settled) and DECLARE.sha256.
2. Post a MiScusi notebook entry with the sha256 and its instant, before the first price.
3. Write COUNTS.md: attempts per day per cell per block, the organic share and the Mayhem share.
4. Price as a MiScusi job on mal-research-0, under `systemd-run --user --scope -p MemoryMax=12G`. Check `systemctl show user-1002.slice -p MemoryCurrent` first.
5. Write REPORT.md (at most 1,200 words), update the library rows, and hand to quant-proof before any sentence about money.

## 9. Open items, settled in the DECLARE before any count or price

- **Adapter parity.** The K-1 leg and the slot-indexed END entry use an adapter on `round_trip`. Before pricing, it must reproduce `arena_paper.price_leg` to 0 lamports on at least 200 cases at a matched latency (as screen-1019 did on 800).
- **Mayhem flag.** Check that `is_mayhem_mode` is present in every block's create rows. If a block lacks it, count that block's universe both ways and declare which one decides.
- **Organic filter.** Confirm 60 s / not-same-tx on counts only. The threshold does not change after COUNTS.
- **s0 for pools with a missing V.** The QP missing-V rule (lower P&L of pending 0 and 3.83%) applies, or the pool is dropped. Declare which before pricing.
