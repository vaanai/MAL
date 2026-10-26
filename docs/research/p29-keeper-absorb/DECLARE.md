# P29 DECLARE: p25-pump-order-keeper-absorb, tag rule + counts. COUNTS ONLY. Written before any count.

Declared at (date -u): 2026-10-25T21:25Z. Lane P29 (hunt29/p1 follow-up). Exploration, no pricing, no P&L, no returns. 0 Helius credits (public RPC only).
Branch claude/p29-keeper-absorb. Parent: P26 DECLARE (docs/research/p26-change-screens/keeper-absorb/, PR #901), status BLOCKED-ON-TAG.

## What was done before this file
- Read HELM-REPLY-1025-1.md (public chain reads, ~20 order/fill pairs, 2026-10-25 20:00-21:05Z, no wallet).
- Read the tape writer/decoder (/data/mal/code/walker/observe/trade_decode.py, trade_store.py) and one 20,001-row sample of trades-2026-09-03T12 for FIELD NAMES ONLY (no count kept).
- One public getSignaturesForAddress(keeper, limit 3) probe call on 2026-10-25 to see the response shape. Each entry carries `slot`, `blockTime`, `err`, `transactionIndex` and the memo string (e.g. `[46] pump-limit:v3:stop_loss:55567:1000000000000000; [8] bypass:0`). No counts kept.

## What fresh-0903 stores (verified in code)
A trade row keeps: venue, mint, `trader` (the Buy/SellEvent `user` field, not the fee payer), side, sol_lamports, token_raw, quote_reserve, base_reserve, pool, slot, **signature**, event_index, tx_index, block_time. It does NOT keep fee payer, the signer list, or the outer program. So "signer = keeper" cannot be read from the tape. The signature IS kept, which is enough (tag rule below).

## Tag rule (frozen now)
The keeper `64fcNUtAAockLpMJZ1DudHCMHJhLRBmgrmGvbrwmQzQw` is fee payer of both txs of an order fill (Helm: HIGH confidence), so its public signature history (getSignaturesForAddress) lists every fill tx, with the memo.
1. **Create tx**: keeper-history entry whose memo contains `pump-limit:v<N>:<take_profit|stop_loss>:<mcap USD>`. Gives (slot, kind in {SL, TP}). Another kind string is counted as `other_kind`.
2. **Fill (ExecuteLimitOrder) tx**: keeper-history entry with err == null, in the SAME slot as at least one create, whose memo does NOT contain `pump-limit:`.
3. **Tagged sell**: a fresh-0903 tape row, side == sell (pump_bonding or pumpswap), whose `signature` is in the fill-tx set. Its kind = kind of the create(s) in that slot; a slot holding both kinds is `mixed` (counted, not assigned). Creates and fills sharing a slot are not matched pair by pair (needs getTransaction; not done).
4. Failed keeper txs (err != null) are counted separately and never tagged.
Weak spots, stated now: (a) Helm verified the rule only on 2026-10-25; in September the keeper address, memo version (`v3`) or program may differ, so memo-version counts per day are reported and a day with 0 creates is reported as "no keeper activity found", not as zero fills; (b) a fill routed through a path the tape decoder does not see is missing from the tagged count (lower bound); (c) a same-slot non-order keeper tx would be mis-tagged, so tagged sells with no create of the matching kind are reported as a contamination rate; (d) public RPC may not serve history back to 2026-09-03: the pager stops and reports its oldest reachable blockTime, and counts cover only reachable days (never extrapolated).

## Counts to make (counts only)
A. Keeper history (public RPC, structure only): signatures per day and per hour; creates by kind and memo version per day; fills per day; err count. Pages back from now to blockTime < 2026-09-03T12:00Z or until unreachable. Cap: 2,500 getSignaturesForAddress calls, 0 getTransaction calls. Also per-hour create/fill counts for the most recent fully closed hours before 2026-10-25T00Z (structure only, from the same pages).
B. Join to fresh-0903 clean trades (/data/mal/blocks-clean/fresh-0903/w{1,2,3}/trades; ledger row "Fresh confirmation block" = exploration pool, spent by EXP-012, "never a confirmation holdout again"; no October hour, no arena tape, no meta-capture, no sealed path): per UTC day tagged sells by kind and venue, distinct mints.
C. Clusters (frozen): >= 3 tagged SL sells in one mint within 60 s (block_time), 60 s refractory per mint. Also the P26 definition restricted to tagged sells (>= 3 tagged, any kind, 10 s, 30 s refractory).
D. Impact bar at 0.25 SOL: 2 x 0.25 / R_eff <= 2% (same form as P26). R_eff two ways: (i) vault only; (ii) vault + flat V = 17.584 SOL on PumpSwap rows (V is per pool on the account, ~17.58 on migrated pump pools and ~0 on 321 of 561 checked pools per tools/pumpswap_virtual.py, so flat V is a capacity upper bound; no pool V map is read). Reserve convention (verified in decoder + memory): PumpSwap tape reserves are PRE-trade, so post-cluster reserve = pre-last-sell quote minus that row's sol_lamports; bonding reserves are POST-trade, used as is.
Not made: any price path after a cluster, any exit, any P&L, any join to HV4/Q50 outputs.

## Kill rules (frozen)
1. Revive trigger (library row): tagged sells >= 100/day AND >= 20 clusters/day under the 2% bar, median full reachable day. Judged on (i) vault-only first, (ii) shown.
2. Keeper history unreachable for fresh-0903 days, or no day with creates: trigger UNTESTABLE on fresh-0903 (status stays BLOCKED, new blocker named), not dead and not met.
3. Median full day below 100 tagged sells or below 20 clusters: DEAD for fresh-0903 volume.
4. A met trigger is NOT evidence of an edge. It only authorises writing a frozen pricing rule.

## Strategy hypothesis to price LATER (not priced here)
Keeper SL sells are price-triggered, not informed. After a cluster of tagged SL sells in one pool, buy 0.25 SOL at the first row at least L slots after the cluster's last tagged sell (pre-declared L = 2 and L = 12; L = 2 is the realistic primary), exit on partial reversion (price back to X% of the pre-cluster price) or a time cap, with fees and PumpSwap V in the quote. A TP-cluster mirror is a separate family. Pricing needs a new frozen DECLARE with the entry/exit grid fixed, in-sample and out-of-sample day sets chosen before any return is seen, and the promotion gate. The fill lands in the same slot as the user's create, so a cluster is visible only after the fact: entry latency is the binding risk.

## Seals obeyed (hunt30 SEALS-BRIEF)
HV4, Q50, DBC forward window, arena tape and meta-capture not touched. No tape hour >= 2026-10-25T00Z read. Public chain history is not sealed information.
