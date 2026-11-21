# (c2') live-send evidence rule — declared before any fetch (manager24, 2026-11-21T07:45:21Z)

Authority: /data/mal/hunt-1008/ops/QP-RULING-1121-a3-repin-triple.md, sha256 d704d78608de3e9e26a9f193a37094c2accebf2a7c6e604aafaf952703684392, Q2.

**Rule.** One manager MiScusi job on mal-fast-0 runs `c2prime-count-1121.py` (this directory, sha256 b7b41ba882d9247ecd0b5c118e54db059dc1af87b454260e92ec10082be0ab6e). The job reads only FLOOR bridge rows of kinds buy_sent, buy_landed, buy_failed, sell_sent, sell_landed and sell_failed with ts_ms at or after 2026-11-20T19:28:12.000Z.

**Bar (c2').** All of the following must hold:
- at least 3 landed buys and at least 3 landed sells;
- every landed row has landed_slot greater than 471854493;
- at least 3 distinct pools (mints) on each side;
- 0 failed rows whose label carries an instruction, account or constraint word.

**Disclosed deviation from Q2's form.** Bridge rows carry no signatures, so no getSignatureStatuses or getTransaction call is made. Instead:
- `meta.err` null comes from the code at the installed tree 5fc473b5. In `tools/arena_live_bridge.py` (FloorLanePort `_finish_buy` / `_finish_sell`, around lines 1500–1522), the port appends `buy_landed` only when the position opened and `sell_landed` only when it closed.
- A landed transaction with an error is logged as `*_failed` with label `landed_failed`. The h5_lane_executor `_finish_buy` / `_finish_sell` check `m["err"] is not None`.
- The slot comes from each landed row's `landed_slot` (the confirmed transaction's slot).
QP rules on this form in the review.

**Output.** Counts only: no amounts, balances, prices, P&L, exit_reason, pool or mint ids, order ids or signatures.
