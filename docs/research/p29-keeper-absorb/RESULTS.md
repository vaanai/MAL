# P29 RESULTS: keeper-absorb tag rule and fresh-0903 counts. COUNTS ONLY. No pricing, no P&L.

Rule and kill rules were frozen in DECLARE.md (commit before any count). Raw output: counts.json (same folder). Jobs: #1607 (pager, public RPC, 1,163 getSignaturesForAddress calls, 0 getTransaction, 0 Helius), #1614 (counts). Code: tools/p29_keeper_pager.py, tools/p29_keeper_counts.py, tools/test_p29_keeper.py.
Data touched: keeper public signature history 2026-09-03 to 2026-10-25; fresh-0903 clean trades (exploration pool). Not touched: HV4, Q50, DBC forward, arena tape, meta-capture, any tape hour >= 2026-10-25T00Z.

## 1. What fresh-0903 stores
Trade rows keep `signature`, `slot`, `trader` (event user), venue, reserves; they do not keep fee payer, signer list or outer program. Tagging is therefore by signature join against the keeper's public history (DECLARE tag rule), not by signer on the tape.

## 2. The tag exists back to 2026-09-03
- The keeper address 64fcNU... has public signature history back to before 2026-09-03T12Z (1,163,000 signatures paged, 1,163 calls, oldest blockTime 1788430643 = 2026-09-03T10:17Z). Same address, memo `pump-limit:v3:` on every create in the whole range (only ver_3 seen), no `other_kind`.
- Keeper activity (all UTC days): about 6-16k signatures/day in early to mid September, 21-25k late September, 28-37k/day in October. Fill:create ratio is about 0.6-0.7 in September (some creates have no same-slot non-memo keeper tx in the list) and above 1.0 in October for SL (multiple fill txs per slot).
- Failed keeper txs: 28-137/day in September, 0-4/day in October (not tagged).
- Per-hour create/fill counts for the 48 most recent fully closed hours before 2026-10-25T00Z are in counts.json `keeper_per_hour_last48_closed` (structure only).

## 3. Tagged sells on fresh-0903 (rule 3; full UTC days 09-04..09-08)
| Day | tagged sells | SL | TP | mixed slot | PumpSwap | bonding | SL60 clusters | pass 2% vault-only | pass 2% vault+V |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 09-04 | 1,868 | 1,590 | 258 | 20 | 1,427 | 441 | 96 | 95 | 95 |
| 09-05 | 2,216 | 1,871 | 334 | 11 | 1,504 | 712 | 150 | 148 | 150 |
| 09-06 | 3,139 | 2,645 | 475 | 19 | 1,767 | 1,372 | 227 | 225 | 227 |
| 09-07 | 4,231 | 3,396 | 807 | 28 | 2,836 | 1,395 | 276 | 270 | 276 |
| 09-08 | 4,881 | 4,020 | 828 | 33 | 3,451 | 1,430 | 356 | 354 | 356 |
Partial days (window starts 09-03T12Z, ends 09-09T12Z): 09-03 1,782 tagged / 117 clusters / 110 pass; 09-09 1,789 / 123 / 121.
- Median full day: **3,139 tagged sells, 227 SL60 clusters, 225 passing the vault-only 2% bar** (227 with V). Minimum full day: 1,868 tagged, 96 clusters, 95 passing.
- PumpSwap share of tagged sells on the five full days: 10,985 of 16,335 (67.2%); of SL60 clusters: 796 of 1,105 (72.0%).
- Cross-check: of all tape rows carrying a fill signature, only 2 are buys (09-08, PumpSwap); the rest are sells, so contamination is negligible. Tagged sell rows per day are 0.89-0.96 of keeper fill txs (rule 2) on the full days.
- The secondary ANY10 definition (P26 shape, 3 tagged sells, any kind, 10 s, 30 s refractory) gives 82-335 clusters/day, 81-334 passing.

## 4. Revive trigger
Trigger (library row): tagged sells >= 100/day AND >= 20 clusters/day under the 2% bar at 0.25 SOL. Numbers above: **MET** on every full day, by a factor of 18 or more on tagged sells and 4 or more on clusters, vault-only and with V.

## 5. What this does not show
- Counts only. It says nothing about price after a cluster. A met trigger is a licence to freeze a pricing rule, not evidence of an edge. No book has cleared the promotion gate.
- Cluster definition counts sell ROWS, not distinct fill txs. A multi-hop Jupiter fill can put several rows in one tx. The pricing rule below counts distinct signatures.
- The 2% bar uses V = 17.584 flat on PumpSwap rows as an upper bound (per-pool V varies; no pool map read); the vault-only variant is the conservative one, and both pass.
- Tagged cluster is visible only after the fill lands; entry latency from the last tagged sell is the binding constraint (the same-slot Jito bundle means the sell is already in the pool when we can see it).
- Fills with no create in the slot are not tagged (lower bound). Memo-version and address were verified only via the memo itself; the on-chain fee payer claim comes from Helm.
- Slot-level matching of create to fill was not done (no getTransaction); SL/TP kind is per slot, with a `mixed` bucket.
- The days are the exploration pool. Any rule priced here is in-sample; the out-of-sample set must be days not used to choose the rule (October keeper days are in-clock for sealed books; use non-sealed hours only per the ledger).

## 6. Frozen pricing rule to run next (proposal, not run)
See the PR body: a new DECLARE before any price path is read.

## 7. Disclosure (QP item 14)
`counts.json` `keeper_per_day` and the keeper-history totals run to 2026-10-25, which lies inside the HV4 counted hours (>= 2026-10-25T00Z) and the DBC forward window, and the per-day rows for 2026-10-02..10-25 include days inside the D2/D3 sealed hours [10-02T10, 10-16T01). They are PUBLIC keeper signature counts (signatures, memo kinds, slots, errors) from public RPC only. They were never joined to any tape, price or outcome, and no October keeper count may be joined to a tape or used to tune any rule. The tape join in section 3 used September hours only (fresh-0903). The coverage dry pass of PRICING-DECLARE section 9 loads keeper rows only before 2026-09-25T00Z.
