# EXP-029: floor-lane-1 real-fill gate read (DEC-035 §6 registration)

**Pre-registration.** It registers at its merge to main. The start instant S below must still be in the future at that merge; if it is not, this file does not register and a new PR with a later S is needed. Written 2026-11-08 (manager21, `date -u` 2026-11-08T10:5xZ), before any live send of `floor-lane-1`. At writing, the lane had run dry only, with 0 entries attempted. No fill, P&L, price or exit of any lane trade existed or was read.

Nothing in this file says or implies that FLOOR makes money. The read below is the only way lane trades at 0.03 SOL can count toward the promotion gate (CLAUDE.md; DEC-035 §6). A PASS is a necessary condition for any stake above 0.03 SOL, never a sufficient one.

| Field | Value |
| --- | --- |
| **ID** | `EXP-029-floor-lane-1-gate-read-prereg` |
| **Rules** | [DEC-035](../DEC/DEC-035-live-learning-lane.md) §2 (with Amendment 1), §5, §6, §7; CLAUDE.md "Promotion gate". This DEC changes no gate threshold, and neither does this file. |
| **Owner basis** | Owner in chat to manager21, 2026-11-08 ~00Z (MiScusi notebook n_j0z3_wYlJOMCaw): "Let's get started on Floor, you can ramp it up as you see it working ... just skip the forward testing and go to live." The ramp (0.03 → 0.10 → 0.25 SOL) is on evidence. DEC-035 §6 says what that evidence is: this read plus the owner's word at each step. |
| **Hypothesis** | Live `floor-lane-1` at 0.03 SOL per position has mean realized SOL per trade > 0 after all fees, on real fills. |
| **Expected outcome** | Unknown. There are 0 real-fill FLOOR trades at writing. Earlier live lane books (H5 lanes) lost money (lane total about −0.33 SOL, manager21 strategic review 2026-11-07). The prior is not favourable. |

## 1. Book (frozen)

- **bot_id** `floor-lane-1` (`tools/floor_lane_executor.py:44` `LANE_BOT_ID`), deciding on the book arm `l1-floor-v2` (FLOOR-OPTION v2, rule `tools/arena_rule_l1.py`, rule pins sha `5e12f8f597571d6d5191a7ba38125c67ad723b7d`, as in the 2026-11-08 HOLDOUT_LEDGER §7 entry).
- **Frozen sha:** main `3710e1af8cf434faca570046de896a7702464709` (PR #1279 merge). The executor runs from a clean checkout of this sha, or of any later main sha whose blobs for these files are identical. Git blob ids at 3710e1af:

| File | Blob |
| --- | --- |
| `tools/floor_lane_executor.py` | `2b0b109a6d6232e4de0feb16339ce13ccd4cd22f` |
| `tools/h5_lane_executor.py` | `c04b8e31362142cfe669b28b0506162d4bea990a` |
| `tools/floor_arena_executor.py` | `b05d4c92e1f91ec40f19e8d2ef4440b70d4e5d95` |
| `tools/h5_arena_executor.py` | `b96f023fe64eadd63a2d377e08a0d004a7038d20` |
| `tools/arena_live_bridge.py` | `06ec1813eb248c57b8eceeaa5166d5fcb28eade9` |
| `tools/floor_lane_host.py` | `a4387c24135f198f8b3ce7d75f810170fbace297` |
| `tools/arena_rule_l1.py` | `cf4cf1c4b08476b96162f735ce9ec730625b402a` |

- **Decision host.** `mal-floor-lane-host.service` was started 2026-11-08T08:32:01Z from main e89f15364c85. `git diff e89f15364c85 3710e1af` is empty for `tools/floor_lane_host.py`, `tools/arena_rule_l1.py` and the host's arena imports (`arena_rules`, `arena_declog`, `arena_paper`, `arena_pool_table`, `arena_rules_v2`, `arena_stack_v2`, `arena_state`). The host's start row records its frozen sha and file sha256; the read reports both.
- **Lane parameters** (executor config, DEC-035 §2 and Amendment 1): `stake_lamports` 30000000 (0.03 SOL); `max_open` 8; `budget_lamports` 300000000 (−0.30 SOL lifetime, cost-capped pre-send); `daily_loss_lamports` 100000000 (−0.10 SOL daily, a pre-entry check). Exits are the rule's own: tp at 1.5x, or 24 h plus a 1.9 s backstop.

## 2. Start instant and trade set

- **S = 2026-11-09T06:00:00Z.** The 08:32:01Z host start blocks all FLOOR entries for about 24 h (runbook, `scripts/mal-fast/README.md`), so no entry is possible before S.
- **In the read:** every live send of `floor-lane-1` with send instant ≥ S and < C (§3). No trade is dropped. A send that fails, or lands and fails, counts at its real cost, fees included (DEC-035 §6). Dry rows (`would_send`) are never in the read.
- **Trade P&L:** realized lamports = sell proceeds − buy cost − every fee and tip the ledger records for that position, including rent that is not recovered. A failed send counts as its fees paid, with 0 proceeds. A position still open at its read instant R (§3) counts as −stake. Sweeps that come later are not credited.
- **Trade day:** the UTC day of the send instant.

## 3. Read instant (one look, set by counts only)

- **C** is the first 00:00:00Z at or after S at which both hold for the sends in [S, C): at least 100 sends, and at least 5 distinct UTC send days. C depends only on send counts and days, never on any outcome.
- **R = C + 25 h.** Every position in the set has passed its 24 h + 1.9 s backstop by then. The read runs once, at or after R, on the full set.
- **Early ends.** If the lane budget stop fires (lane P&L ≤ −0.30 SOL, STOP file written by the executor) before C, then C is the first 00:00Z after the stop, R = C + 25 h, and the result is **FAIL**. If the manager or the owner stops the lane for another reason before C, or any §1 parameter or blob changes before C, the read is **NOT_DECIDABLE**. A new registration is then needed; this one never restarts.
- **Cap changes.** Any change to `max_open` or any other §1 parameter before C ends this read as in the paragraph above. Changes after C do not affect it.

## 4. Gate tests (CLAUDE.md; DEC-035 §6), on real fills only

All four tests run on the full trade set. Real-fill failures are observed, not modelled. The flat 15% and pressure-fail models apply only to simulated trades. Real and simulated trades are never pooled, and no simulated trade enters this read.

1. n ≥ 100 trades.
2. At least 5 distinct UTC days, and strictly more than half of them have a positive day total.
3. The lower 90% bound of mean SOL per trade is > 0: 1,000 bootstrap draws, resampling trades with replacement, `numpy.random.default_rng(1)`, 5th percentile of the resampled means.
4. The total is still > 0 after removing the 3 largest trades.

**PASS** only if all four hold; otherwise **FAIL**. Quant-proof rules on the read at its exact head.

## 5. What a PASS allows, and what it does not

- A PASS at 0.03 SOL does not show the edge holds at a larger stake (DEC-035 §6). Each scaling step (0.10, then 0.25 SOL) needs, in its own PR: the stake, a price-impact estimate at that stake on the PASS trade set's pools, and the owner's word for that step in chat. That PR amends DEC-035 for `floor-lane-1`.
- A FAIL ends `floor-lane-1` at 0.03 SOL under this registration. No retune and no re-read of the same set. A changed rule is a new book with a new bot_id and a new registration.

## 6. Reporting

- Daily, per DEC-035 §5: `python -m tools.lane_daily_report --config <executor json> --day <UTC day>` (PR #1284) goes to the MiScusi notebook and `data/console.json`, losses included. The daily reports describe; they are not reads of this registration, and none of them triggers a decision.
- The read output names every other bot_id that ran in the lane in [S, R), with its result (DEC-035 §6), and reports the host start row's frozen sha and file sha256.
- Seals: the DEC-035 §7 HOLDOUT_LEDGER entry of 2026-11-08 (PR #1275) covers the overlapped reads; each is reported "not blind: live-lane observation declared <floor-lane-1 first-send instant>".
