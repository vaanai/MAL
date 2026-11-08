# FLOOR dry-to-live initialization — independent review

**Commit reviewed:** `3710e1af8cf434faca570046de896a7702464709`. Send/state files byte-identical vs that blob (test pin). Host pin `e89f15364c85c85dd3fa8eaa1ea486e60e7c3b19` is an ancestor; these files are unchanged since. Not re-read from a host.

**Scope:** findings + adversarial tests only. No production source edit, merge, deploy, SSH, keys, or trades. Real wallet inventory/conflicts stay with the parent. Do not restart `floor_lane_host` (any host restart blocks FLOOR entries ~24 h).

**Verdict: GO for one documented state procedure; NO-GO for every naive cutover the pinned runbook leaves open.** Not a profit claim. Not a carry of paper inventory onto the chain.

## GO, in one line

Stop the dry executor (not the host). From that frozen `bridge-state-dry.json`, write a **new** `bridge-state-live.json` that copies only `{prev, seq, cursor, start_seen}` with `mode=live` and **empty** `orders` / `bought` / `realized` / `latches`. Seed `floor-state-live.json` with dry `starts_seen` and `first_send_ms=null`. Leave dry files untouched. Start live against that. Paper positions stay paper; real holds start empty.

## Files the live executor actually expects

Same `state_dir` (example `/var/lib/mal-live/floor-lane/state`). Mode is in the filename. DRY never creates the live files (`docs/runbooks/arena-live-bridge.md`).

| Path | Who writes it | First live start if missing |
| --- | --- | --- |
| `bridge-state-live.json` | live `BridgeState` | **seq=-1, cursor={}, start_seen=false → rereads the whole decision log and buys** |
| `bridge-log-live.jsonl` | live `_log` | created on first log line |
| `floor-state-live.json` | `FloorBridge` sidecar | `starts_seen=0`, `first_send_ms=null` |
| `probe/state-live.json` | `make_live_port` → `probe_live` | **fresh is correct** (no dry probe state exists) |
| `probe/fills.jsonl`, `sig/` | live port | created as needed |
| lane ledger (`lane.ledger_path`) | live only (`FloorLaneBridge.lane`) | dry never reads or writes it |
| `{ledger}.bot.floor-lane-1.lock` | live `take_bot_lock` | **dry does not take this lock** |

Dry leftovers (keep; do not rename onto the live names):

- `bridge-state-dry.json` — today: seq 3, one `live=false` open, two historical entries never stored (wallet_floor `_refuse` does not insert `orders`)
- `bridge-log-dry.jsonl`, `floor-state-dry.json`

`BridgeState.load` refuses a file whose `mode` field ≠ run mode. Copying the dry JSON to the live path **without** rewriting `mode` is fail-closed. Copying it **with** `mode=live` is not.

## Naive paths (NO-GO, tested)

1. **Config `mode: live` + `--live` with no live state.** Empty `BridgeState`. `read_new_rows` from seq -1. The two wallet_floor paper refusals and the dry-bought open all become `live=true` buys and `send_intent` rows. FakeRpc: a throwaway key **signs** `sendTransaction` for the paper open.
2. **Copy dry state wholesale, rewrite `mode`.** The virtual open stays `live=false`. Clock backstop (`decision_ms + HOLD_MS`) sells via `DryPort`. `FloorLaneBridge.apply_events` books that dry `sell_landed` (no `_row`) into the **real** lane ledger as an estimated exit and latches `ata_rent_not_refunded` (new live entries stop). The live port is not asked to sell (no nonexistent-token send on this path). Flipping `live` to true on that order would be worse (a real sell of a mint the wallet does not hold); this review does not treat that as a procedure.
3. **Copy `bought` without the order.** Future re-entry of that mint is `already_bought` forever (FLOOR only releases a mint on `sell_landed`).
4. **Leave dry running.** Bot lock is live-only. Dry and live will both tail the same log. Stop dry first.

There is **no** defensible way, in existing code, to turn a paper position into a real hold. Carry = do not carry. Host paper book (3 open) is independent of the executor. Their later `exit` rows are `exit_unknown_order` on a seeded live book.

## Defended procedure (existing `BridgeState.load`/`save`; no source change)

Parent/Helm only. This agent does not touch the host.

1. Confirm `floor_lane_host` stays up. Do not restart it. Do not reset its checkpoint.
2. Stop the **dry executor** only. Wait until it is not running so the dry state file is not mid-write.
3. Abort if `bridge-state-dry.json` `latches` is nonempty, or if any order has `live: true`, or if `bridge-state-live.json` already exists, or if the lane ledger already has `floor-lane-1` open intents.
4. Abort unless dry `seq` equals the decision-log head seq for `l1-floor-v2` (today: 3) and `start_seen` is true.
5. Write `bridge-state-live.json` with the existing dataclass (sort_keys JSON, same as `BridgeState.save`):

```python
dry = BridgeState.load(state_dir / "bridge-state-dry.json", "dry")
BridgeState(mode="live", prev=dry.prev, seq=dry.seq, cursor=dict(dry.cursor), start_seen=dry.start_seen).save(
    state_dir / "bridge-state-live.json"
)
```

   Copy **prev, seq, cursor as one triple** from that one snapshot. Empty `orders`, `bought`, `attempts`, `realized_lamports`, `days`, `latches`. Do not copy dry P&L.

6. Write `floor-state-live.json`:

```json
{"exits_not_followed":{},"first_send_ms":null,"gap_hold_until_ms":<from dry or 0>,"gaps":[],"late_exits":[],"schema":"floor_executor_state_v1","starts_seen":<from dry, must be >= 1>}
```

   `first_send_ms=null` so the 14-day canary starts at the first **live** send, not the paper send.

7. Do not create or copy `probe/`. Fresh probe state is the real-hold ledger; it must not see simulated inventory.
8. Flip unit config to `mode: live` with `--live` and `LIVE_OK` exactly `floor-lane-1`. `check --config … --live` then start. Dry files stay on disk.

`prev`/`seq`/`cursor` inconsistent with each other fail-closes (`chain_break`) on the next row rather than replaying. Replay requires resetting **all three** to an earlier consistent head (the empty default).

## After cutover

| Event | Live book | Dry files (stopped) |
| --- | --- | --- |
| Host `exit` for the two never-bought paper oids | `exit_unknown_order` | if dry is later resumed: also unknown (never in `orders`) |
| Host `exit` / 24 h for the one dry-bought oid | `exit_unknown_order`; no sell | resume dry later: `DryPort.sell`, paper log only; live ledger unchanged |
| New `entry` after the seeded cursor | real buy if gates pass | if dry still stopped: unread until dry resumes (then would-send only) |
| Live executor restart | resumes from `bridge-state-live.json`; no replay | untouched |
| Next **host** restart | executor `starts_seen` 1→2 → 10 min `gap_hold`; **host** still 24 h-blocks entries | n/a — do not restart the host for this cutover |

## Residual assumptions (not proved here)

- Dry latches empty and the executor is caught up to the log head (parent confirms on the host).
- Real wallet inventory / conflicts / funded floor: parent, not this review.
- Lane ledger has no `floor-lane-1` orphans from a prior aborted live start.
- No existing live state to overwrite.
- Host source at `e89f…` matches these blobs (ancestor; files unchanged).
- Operator copies the cursor triple from a **stopped** dry snapshot; a stale copy that is a consistent earlier head **will** replay the delta.
- `LIVE_OK`, credential, A3 path, STOP/HALT: Helm/manager. This review does not install them.
- FLOOR 14-day canary (`first_send_ms`) still exists on the live subclass; leaving it null starts that clock at first live send.

## Tests

```
PYTHONPATH=. python3 -m pytest ARTIFACTS/lab/floor-live-budget-3710e1af/test_floor_live_init_cutover.py -q
```

16 tests: naive empty replay (FakeInner + FakeRpc/throwaway key), wholesale copy ledger-poison + `ata_rent_not_refunded`, mode mismatch refuse, bought-without-order block, cursor seed (no replay, paper exits ignored, fresh live buy, restart, later dry paper exit isolated, `starts_seen` gap_hold vs missing sidecar, inconsistent seq fail-close). Existing fee/lifetime file still 15 passed.

## Source fix? Not required for this GO

A `--seed-cursor-from-dry` CLI would only encode the same `BridgeState.save`. It is not needed to make the procedure true. Do **not** add a path that copies `orders`. If someone later wants a fail-closed host tool, the minimal change is that CLI plus “refuse if live state exists / dry latches / live=true orders,” not a carry of paper inventory.
