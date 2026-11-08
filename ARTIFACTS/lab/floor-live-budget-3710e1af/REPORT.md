# FLOOR live fee / lifetime budget — independent review

**Commit reviewed:** `3710e1af8cf434faca570046de896a7702464709` (DEC-035 Amendment 1). Send/budget files are byte-identical on later `main` as of this write (checked vs `origin/main`).  
**Host note:** `e89f15364c85c85dd3fa8eaa1ea486e60e7c3b19` is an ancestor; this sha adds the cost-capped pre-send. Not re-read from a host.  
**Scope:** read + adversarial tests in this workspace. No deploy, SSH, keys, trades, merges, or production source edits. Dry 1 SOL floor (`dry_balance` 0.60 vs derived 0.75) is out of scope.

**Verdict: GO for fee / lifetime safety**, bounded on the production send path at this sha, under the assumptions below. Not a profit claim. No book has cleared the promotion gate.

## GO, in one line

Eight max-stake positions that each pay ATA rent + one buy + ten p155 sells, then wipe to zero, cost **270,880,000 lamports**, which is **under** the 300,000,000 lifetime budget. The live sell path (`FloorLanePort` → `make_live_port` `BridgeExecutor` → `probe_live._start_sell`) does not send an 11th sell: 100 landed-failed sells produce 10 distinct sell signatures, then `abandoned`. Same for expiry. Rebroadcasts reuse the signature and do not increment the attempt count.

## What is a fixed limit vs an assumption

| Kind | Fact |
| --- | --- |
| **Fixed in code** | `LANE_MAX_OPEN=8`, `LANE_BUDGET_MAX_LAMPORTS=300_000_000`, stake `[20e6, 30e6]`, `pl.SELL_MAX_ATTEMPTS=10` (config can only lower), `FloorLanePort` overwrites executor priority to **155,000** (not the bridge default 500,000), sell tx includes `close_token_account`, lane STOP on `pnl <= -budget`, cost-capped pre-send (`min(fresh mark, stake)`; stale/missing = 0). |
| **Assumption (holds at this sha, not the same name)** | Fee reserve `3,860,000 = 2,100,000 rent + 160,000 buy + 10×160,000 sell` uses `F.SELL_RESERVE_SENDS`, **not** `pl.SELL_MAX_ATTEMPTS`. They are both 10 here. H5 binds the two; FLOOR does not. A later probe_live bump would desync FLOOR unless someone notices. |
| **Assumption** | Actual ATA rent ≤ 2,100,000 (fixture/observed Token ATA 2,039,280). Token-2022 extensions could exceed. Failed swap txs are atomic (no stranded WSOL rent on a failed sell). Cluster `lastValidBlockHeight` means an “expired” signature cannot later land and pay a second fee. |
| **Not a lifetime limit** | The **0.10 SOL daily stop** is a **pre-entry marked P&L / cost-basis check**, not a hard daily cash-loss ceiling. FLOOR has no worst-case daily pre-send (H5 still does). A day can lose more than 0.10 (a 5×0.03 wipe is 0.15) and still stay inside the 0.30 lifetime bound. Naive `0.10 + 8×0.03 ≈ 0.34` exceeds 0.30; the **lifetime** cost-capped pre-send is what binds, not the daily stop. Docs’ “~0.37 net day following gains” is a day-delta, not a license to go through −0.30 lifetime. |

## Sell retry path (does it unbound fees?)

Production tick: `FloorLanePort.poll` → `LiveExecutorPort.poll` → `advance_pending` + `BridgeExecutor.poll_positions`.

1. Bridge sets `exit_reason` and calls `poll_positions`. Base `_exit_candidates` **skips `abandoned` and in-flight pending**.
2. `_start_sell` builds a **new** tx (fresh blockhash on retry) and sendTransaction `maxRetries: 0`. Confirm loop rebroadcasts the **same** signed bytes every 2 s until land or expiry.
3. Landed-fail or expiry → `_sell_failed`: `sell_attempts += 1`; at 5 → `stuck` (new buys halt); at `sell_max_attempts` (10) → `abandoned` (no more sells).
4. `_start_sell` itself does **not** read `sell_max_attempts` or `abandoned`. The cap is the flag in `_exit_candidates`. Clearing `abandoned` in memory sends an 11th sell (test). That is not a production path: the flag is persisted in executor state and survives reload.

FLOOR live `pcfg` does not set `sell_max_attempts`; the executor default is `min(10, max(sell_retries, 10)) = 10`. Measured on the wrapped port: `ex.sell_max_attempts == 10` and every sell’s signed compute-budget total is 155,000.

Existing `tools/test_floor_lane_executor.py` uses `FakeInner` (no cap). Existing `test_probe_live.py::test_100_failed_sells_capped_and_abandoned` uses base `LiveExecutor.poll_positions`, not `BridgeExecutor`. Those 105 FLOOR tests still pass here; they were not this proof.

## Lifetime arithmetic (8 × 0.03)

```
per position = stake 30,000,000 + reserve 3,860,000 = 33,860,000
8 positions  = 270,880,000
budget       = 300,000,000
headroom     =  29,120,000  ≈ 182 extra p155 sells across the book (~22/position)
```

11 sells/position still fits. 33 sells/position does not. Unbounded retries would eventually breach; the abandoned cap stops that.

Cost-capped pre-send (this sha, the unrealized-gain fix): a +100% mark on 7 fills does **not** admit an 8th whose full wipe would go through −0.30. Property-tested: every accepted entry satisfies `wipe_pnl > -budget + reserve×(open+1)`.

Failed-buy fees hit cash and then the budget stop; they do not bypass it. Paid sell fees are booked as ledger `fee` rows (position stays open); the pre-send still reserves a **full** 10 per open position (conservative).

## Risks that are not a NO-GO at this sha

- **Maintenance desync:** FLOOR reserve is not wired to `pl.SELL_MAX_ATTEMPTS`.
- **Cap mechanism:** abandoned flag, not a send-time guard inside `_start_sell`. State corruption that clears the flag would pay more; restart with the real state file does not.
- **Rent / CU:** reserve is 2.1M rent and ≤155k+1 priority (validate allows +1 lamport). Slippage is a stake wipe, already in the pre-send, not extra fee.
- **Abandoned inventory:** tokens and ATA rent stay; slot stays occupied; new buys halt while any position is stuck/abandoned. That is capital lock, not an unbounded fee loop.
- **Daily:** do not label 0.10 as a hard cash-loss ceiling.

## Tests

```
PYTHONPATH=. python3 -m pytest ARTIFACTS/lab/floor-live-budget-3710e1af/test_floor_live_budget_safety.py -q
# 15 passed (this review)
PYTHONPATH=. python3 -m pytest tools/test_floor_lane_executor.py tools/test_floor_arena_executor.py -q
# 105 passed (existing; not this proof)
```

Logs: `pytest.out`, `pytest-existing-floor.out`.
