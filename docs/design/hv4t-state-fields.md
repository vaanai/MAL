# DESIGN: HV4-T state fields (DEC-031 Amendment 23 item 8, "State fields")

Status: records plus a test, written 2026-11-01 (`date -u`). Nothing deployed. No tape, tip file, mirror, derived copy, ledger, `bots.json` line or decision row was opened. Every row below is synthetic. No book has cleared the promotion gate. This note measures no book and says nothing about profit.

HV4-T means an HV4 arm re-registered on the fast-0 tip source (Amendment 23). HV4's frozen sha is `9ed4c5ff63935696b2a89150e13565e123047754`. On main, `tools/arena_state.py`, `tools/arena_paper.py`, `tools/hv4_cards.py` and `tools/hv4_runner.py` are byte-equal to it (checked by `git diff --stat 9ed4c5ff origin/main -- <the four files>`, empty, and again by `test_main_equals_the_frozen_hv4_files`, which compares blob ids).

The test is `tools/test_hv4t_tip_fields.py`. It builds one small market, writes it twice, and reads each with the real `ArenaState`:

- **Feed:** public-feed-shaped rows. The `FEED_ONLY_KEYS` are present. `tx_index` and `block_time` are absent.
- **Tip:** the same walker rows through the real `ArenaTipAdapter`. The `FEED_ONLY_KEYS` are absent. `tx_index` and `block_time` are added.

The common keys of both come from the adapter's own row mapping, so the two sources differ only by the documented delta. Chain facts (RPC, not tape) are the same dict on both sides.

## Answer

- **Row keys always absent on the tip:** 11 distinct names, the 10 in `FEED_ONLY_KEYS` plus `supply_raw` (the PumpSwap five: `pool_quote_amount`, `lp_fee`, `protocol_fee`, `creator_fee`, `mint_source`; the bonding seven: `real_quote_reserve`, `real_base_reserve`, `protocol_fee_bps`, `protocol_fee`, `creator`, `creator_fee_bps`, `creator_fee`; `protocol_fee` and `creator_fee` are on both).
- **Of those, the HV4 modules read three:** `pool_quote_amount` and `lp_fee` (price view) and the bonding `creator` (creator). The other eight names are not read. This is shown by running the state (ablation, 13 program-and-key cases) and by a static scan of the four frozen files.
- **State fields (snapshot keys and `hv4_cards.features` fields) that are None on the tip in every scenario: none.** The intersection over the three scenarios is empty, and the test pins that. A field is None on the tip only when the history lacks the row that sets it.
- **Two decision changes (NEED), both shown in `hv4_cards` and in the real `Runner`:**
  1. `creator` family, scenario S2 with three sellers: the tip enters where the feed vetoes (`sell_route`).
  2. Age family, scenario S3: the tip fails `age_min` where the feed passes.
- **The constant-product price view** changes values but no decision at the non-edge points tested. Per Amendment 23 item 8 it is not a need. One test shows the edge: it can flip a check only when the feed's value is within one `lp_fee` of the threshold.

## Scenarios

| name | what differs between the two sources |
|---|---|
| S1 | The mint's `create` row is in the history and the pool comes from a migration. Only the feed-only keys differ. |
| S2 | The mint's `create` row is outside the history; the curve's bonding trades are inside. The feed learns the creator from the bonding trade's `creator`. The tip cannot. |
| S2 three sellers | S2 with exactly three distinct sellers in the last 60 min, one of them the creator. |
| S3 | The pool was created outside a migration. The feed has a `pool_create` row (CreatePool). The walker emits none, so the tip has none. |

## The listing

Classes: **same** (present and equal on both), **None only on tip** (feed has a value, tip is None), **differs** (both have a value, the values differ). "Read by" cites main (and so the frozen sha). `test_the_cited_read_sites_are_on_those_lines` fails if a cited line stops containing the cited text.

### State keys the HV4 rule reads

| key | read by (file:line on main) | public feed | tip source | decision effect |
|---|---|---|---|---|
| `prog`, `quote_wsol`, `now_ms`, `last_ms`, `last_slot`, `reserves.v_sol`, `windows.1m/5m/15m/60m`, `top_net`, `candle_retention_min`, `gaps_since_first_seen`, `ath_ms` | `hv4_cards.py:457` (`prog`), `:459` (`quote_wsol`), `:409` (`now_ms`), `:479` (`last_ms`), `:371` (`last_slot`), `:429` (`v_sol`), `:474` (windows), `:672` (`top_net`), `:682` (retention), `:352` (`ath_ms`, a stamp check); `hv4_runner.py:378` (`gaps_since_first_seen`) | value | same | none |
| `price`, `drawdown_from_ath`, `reserves.quote_sol`, `reserves.quote_eff_sol`, `candles` (o, h, l, c) | `hv4_cards.py:658` (`price`), `:671` (`drawdown_from_ath`), `:471` (`reserves`), `:619` (`candles`); `hv4_runner.py:682` (`Pool.q_real` into QuoteHistory, veto 7) | exact post-trade price from `pool_quote_amount + lp_fee` | differs: constant product on the base move, re-anchored at every print | none at the points tested (source's view, not a need, Am.23 item 8); can flip a check within one `lp_fee` of a threshold (edge test) |
| `creator` | `hv4_cards.py:703` (features), `:873` (`sell_route`) | value (create row, or bonding trade `creator`) | S1: same. S2: **None only on tip** | **NEED** (S2 three sellers) |
| `creator_net_sol` | `hv4_cards.py:676` | value | S1: same. S2: **None only on tip** | follows `creator`; no gate reads it, shown on the card |
| `created_ms` | `hv4_cards.py:352` (a stamp check in `assert_as_of`); set by `arena_state.py:394` | value | S1: same. S3: **None only on tip** | **NEED** (S3), through `age_s` and `created_known` below |
| `created_kind` | nothing in the frozen files (set by `arena_state.py:394`) | value | S1: same. S3: **None only on tip** | none (unread) |
| `age_s` | `hv4_cards.py:416`, `:651` | value | S1: same. S3: **None only on tip** (the `_age` helper falls back to the lower bound) | **NEED** (S3) |
| `created_known` | `hv4_cards.py:417`; `hv4_runner.py:378` | True | S1: same. S3: **differs** (False) | **NEED** (S3) |
| `age_since_first_seen_s` | `hv4_cards.py:419`; `hv4_runner.py:378` | value | S3: **differs** (a lower bound that starts at the first print) | **NEED** (S3) |
| `Pool.net` (card `holder_count`) | `hv4_runner.py:923` | value | same | none |

Not read by any HV4 module, and shown here only so the listing is complete: `ath`, `creator_net_sol_curve`, `unique_traders`, `idle_s`, `last_trade` values (only its `t_ms` is read, by `assert_as_of`). `ath` differs on the tip (price view). `creator_net_sol_curve` is None on both sources in every scenario, because the curve key is evicted after 6 h idle and the pools under test are older than that. `name`, `symbol` and `complete_ms` are None on both sources for a pool key (they belong to the curve key). `complete_ms` and `origin_create_ms` are stamp checks in `assert_as_of` (`hv4_cards.py:352`); `origin_create_ms` is the curve's create time: the same on both when the create row is in the history (S1), None on both in S2.

### `hv4_cards.features` fields (`FEATURE_FIELDS`, 47 fields)

| field | feed vs tip | decision effect |
|---|---|---|
| `price`, `quote_sol`, `quote_eff_sol`, `impact_rt_025`, `impact_rt_050`, `impact_rt_100`, `ret_1m`, `ret_5m`, `ret_15m`, `ret_60m`, `dd_ath`, `hi_6h`, `lo_6h`, `rebound`, `close_vs_hi_6h` | differs (price view), in every scenario | none at the points tested; edge note above |
| `creator_net_sol_life`, `creator_net_sol_60m` | S2: **None only on tip** | feed the card only; no filter, veto or setup reads them. Follow `creator`. |
| `noncreator_sellers_60m` | S2: **differs** (the creator is counted as a seller) | **NEED** through `sell_route` (below) |
| `age_s` | S3: **differs** (lower bound) | **NEED** through `filters` `age_min` (below) |
| `avg_vol_15m_6h`, `vol_6h` (and `hi_6h`, `lo_6h`, `rebound`, `close_vs_hi_6h` in S3) | S3: **None only on tip**: `hv4_cards._covered` needs a known age or 360 min of watching | none for the setups tested (PULLBACK reads `ret_*` and `net_flow_60m`); BREAKOUT and SECOND_WAVE read the 6 h block and are not exercised (no BIG tag fired in the runs here) |
| `ret_240m` | None on both | not exercised: the market is 170 min long |
| the other 31 fields (`age_s` in S1/S2, `last_print_s`, `n_prints_15m`, `signers_15m`, `vol_5m/15m/60m`, `net_flow_*`, `n_buys_*`, `n_sells_*`, `unique_buyers_15m`, `unique_sellers_15m`, `new_wallet_buy_share_15m`, `top_buyer_net_sol`, `top_seller_net_sol`, `vstar`, `wash_gross_15m_sol`, `top3_signer_share_60m`, `net_sell_25slots_sol`, `max_stake_impact`, and the rest) | same | none |

### The feed-only row keys, one at a time

Method: delete one key from the rows of one program of the feed-shaped S2 market and compare the state with the full feed rows (`test_ablating_one_feed_only_key_at_a_time_shows_which_ones_the_state_reads`).

| row key | read by | effect of deleting it |
|---|---|---|
| `pool_quote_amount`, `lp_fee` (PumpSwap) | `arena_state.py:426` (`pumpswap_post`) | price family differs (exactly the lists above); no key becomes None |
| `creator` (bonding) | `arena_state.py:434` | creator family becomes None (S2) |
| `protocol_fee`, `creator_fee`, `mint_source`, `supply_raw` (PumpSwap); `real_quote_reserve`, `real_base_reserve`, `protocol_fee_bps`, `protocol_fee`, `creator_fee_bps`, `creator_fee` (bonding) | nothing in the four frozen files | nothing changes (10 cases) |

`arena_paper.py` (the engine) reads none of them. It reads `tx_index` and `block_time` (added on the tip) for its order key and clock. A PumpSwap row's `creator` is not read by `ArenaState` on main: the pool's `creator` is copied once from the curve at link time (`arena_state.py:407`).

## Decision effect, shown

All comparisons use the same chain dict on both sources (`chain_facts`).

| scenario | `hv4_cards` (filters, features, setups, code_vetoes, build_card) | real `Runner` (CON-X entries) |
|---|---|---|
| S1 | same entry. Only the card sha differs (6-digit numbers move with the price view). | same two entries, same size and slot. Card shas differ. |
| S2, four sellers | same entry. `sell_route_sellers` is 3 on the feed and 4 on the tip. Card sha differs. | not run |
| S2, three sellers | **NEED.** Feed: `sell_route` veto, `CardRefused(["sell_route"])`, no entry. Tip: 3 non-creator sellers, no veto, entry. The tip is the more permissive side. | feed: no CON-X entry (card refused, `sell_route`). Tip: two entries. |
| S2, create row in history, three sellers | same on both: the creator is known on both, 2 non-creator sellers, veto on both. | not run |
| S3 | **NEED.** Feed: filters pass (age 3 h 10 min). Tip: `age_min` fails (first print 90 min ago, a lower bound). The tip is the stricter side. | feed: two entries. Tip: none; first scan has 0 pools through filters. |

What a NEED asks of the registration (this note does not choose):

- **`creator`:** the registration states how HV4-T treats a pool whose creator is unknown (for example, skip it, or require the create row in the history). Without that, the tip source admits pools the feed vetoes.
- **Age:** this is the Amendment 23 item 8 "Age" bullet. HV4's `_age`, `filters` and the rule's age bounds read a lower bound on this source when `created_known` is false. The registration states its handling.

## Commands run

```
git fetch origin
git diff --stat 9ed4c5ff origin/main -- tools/arena_state.py tools/arena_paper.py tools/hv4_cards.py tools/hv4_runner.py   # empty
/data/mal/venv/bin/python -m pytest -q tools/test_hv4t_tip_fields.py tools/test_arena_tip_adapter.py
```

Result at head `a882a7e76ab6b476556fe5fb7ed91f9ece5a36f8` (the test file; the next commit adds only this document), run 2026-11-01T23:01Z:

```
87 passed in 26.48s
```

## Not measured

- No real tip row, public-tape row, mirror, derived copy, ledger or decision row was read. The market is synthetic: a PumpSwap pullback path from `tools/test_hv4_runner.py`, 170 minutes, two pools.
- How often a real pool has no create row in the writer's history, or is created outside a migration. These notes show that the decision can change, not how often it does. Nothing here estimates a count.
- Real `t_recv_ms` behaviour. The tip follower stamps one `t_recv_ms` on every row of a slot (adapter docstring); the public feed stamps its own receive time. Both sides of the test use the same instants, so timing is not part of the delta shown.
- The tip's gap rows and the engine's feed-gap exclusions (Amendment 23 item 3). No gap row is in the market.
- Exit-side marks. `mark_ret`, `exit_reason` and the exit `sell_route` flag read the same price view; the tests compare entry decisions only.
- BREAKOUT and SECOND_WAVE (BIG persona) setups, `ret_240m`, the 6 h block under a real 6 h history, and the LLM arms (the risk agent, `_material_change`, prompts). Cards differ in bytes on the tip in every scenario; an X arm does not read the card, an LLM arm does.
- Lane H (BST, the H5 signal). It reads other rows.
- The bonding feed-only key names were not checked against a decoder record. The PumpSwap names were (`test_the_synthetic_feed_key_names_are_names_the_real_decoder_writes`, with the repo's own fixture). The bonding names are those in `FEED_ONLY_KEYS` and `tools/arena_tape_schema.md`.
- ArenaEngine pricing and fills on the tip source. The engine reads reserves, V, `tx_index` and `block_time` and none of the feed-only keys (static scan); no fill is compared here.
- Any P&L, edge, or promotion-gate quantity.
