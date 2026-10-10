# Tip follower event V: deploy from its own source tree

Status: **not deployed.** This runbook is for the manager, after the PR that adds `--trade-event-v` is merged and every proof in "Before you start" is done.

Sources: the design `/data/mal/hunt-1008/c1nf-verify/EVENT-V-DESIGN-1010.md` (option A, steps 3–5) and quant-proof's 10-10 ruling ((a) gaps, (b) seals, (c) proofs 1–7).

## What the flag does

`tools/fast_tip_follower.py --trade-event-v` (or `MAL_TIP_TRADE_EVENT_V=1`; off by default):

- For every trade row of a block, after the rows from `resolve_unresolved` are appended, it decodes that tx's logs again with `records_from_logs(..., event_v=True)`. It copies `EVENT_V_KEYS` (`virtual_quote_reserves`, `ix_name`, `creator_fee_unclaimed`, `buyback_fee`, `fee_recipient_zero`) onto the row with the same `(signature, event_index)`.
- The re-decoded record must agree with the row on `venue`, `side`, `pool` and `sol_lamports`. If it does not, `event_v_mismatch` goes up and the row is not stamped.
- A print with no event-V tail is counted (`event_v_missing`; `event_v_missing_pumpswap` for PumpSwap rows without V) and left without the keys. **V is never carried from another print.**
- If the decode raises, the tx's rows are written without the keys and `event_v_errors` goes up. The feed keeps running.
- Nothing else in any row changes. Creates, migrations, observe, skipped slots and gaps are not touched. The singular `virtual_quote_reserve` is still stamped as before, in **both** flag states, with blob b6c0bb3's reading (route 1, quant-proof 10-10 r4): unsigned little-endian u64 at bytes 245..253 of the pool account when it has at least 253 bytes, else null. A pool with a negative stored V is stamped with that unsigned value (>= 2^63), as the running follower does today. Only the event-V keys are signed.
- `status.json` always carries `decoder_blobs` (git blob sha of the three decoder files the process imported) and `decoder_blobs_pinned`.

The stamp equals the V that forward-1002ev holds only when the tree carries **job #433's decoder blobs** (DEC-016:397):

| File | Blob (must equal) |
| --- | --- |
| `observe/trade_decode.py` | `238942a6b3c5425389eddfde4d11268c300acbec` |
| `observe/trade_store.py` | `ea4e11eddf9f034e3bc7318ce8743337d753f350` |
| `tools/pump_history_backfill.py` | `9a8bebb32adcf86de060b55f5a08110d11c0a550` |

`python -m tools.fast_tip_follower --check-decoder-pins` prints the imported blobs and exits 0 only if all three match. It makes no RPC call.

## Why its own tree

`mal-fast-tip-follower`, `mal-fast-forward-paper` and `mal-fast-grad-stream` all have `WorkingDirectory=/var/lib/mal/fast-forward/src`. Redeploying that tree would change the runner's code at its next 00:00Z restart, which is a runner change and needs the full md5 proof.

So:

- the follower gets its own tree, `/var/lib/mal/fast-tip-follower-src/<sha>`, through a systemd drop-in for `mal-fast-tip-follower` only;
- `/var/lib/mal/fast-forward/src` is **not** touched, and `install-fast-forward-paper.sh` is **not** run;
- the runner and the grad stream are **not** restarted.

The deployed follower today runs older code than main (quant-proof (c)):

The full delta between the deployed tree (`d0109f7`) and this PR's tree, for the files in `tools/` and `observe/` that the follower's import chain touches or that changed beside it (corrected per quant-proof 10-10 r4; (c) listed only the first three rows):

| File | Deployed blob (`d0109f7`) | This PR | Imported by the follower at this PR |
| --- | --- | --- | --- |
| `tools/fast_tip_follower.py` | 36dd10e | 61e8062 (route 1 fix) | entry point |
| `observe/trade_decode.py` | a10e0568 (no event V) | 238942a6 | yes |
| `tools/pump_history_backfill.py` | cea9783 | 9a8bebb3 | yes |
| `observe/trade_store.py` | b5eb3f82 | ea4e11ed | yes |
| `tools/backfill_verify.py` | 5274061e | f4308871 | yes, through `pump_history_backfill` |
| `tools/tape_lines.py` | (new file) | 810af3bc | yes, through `backfill_verify` |
| `tools/pumpswap_tx.py` | b6c0bb3 | 92560e3 | **no**: the running follower imports it for the single V; after route 1 the follower reads those 8 bytes itself |
| `observe/link_state.py` | (new file) | 026a9cd9 | no (imported by `observe/trade_source.py`) |
| `observe/trade_source.py` | 5a74d871 | 4684fc16 | no |

**The old follower stamps the unsigned value, not null.** Blob b6c0bb3 reads the pool tail as an unsigned u64, so the old `v >= 0` check never fires: a pool with a negative stored V gets `virtual_quote_reserve` >= 2^63 (about 1.8e19), not null. Quant-proof (c) said null; that was wrong (job #514). Blob 92560e3 reads V signed, which is why #554 at 7e0e60a changed 157 of 32,250 trade rows in P0 with the flag off. Route 1 keeps the b6c0bb3 reading for the single key in both flag states, so that difference is gone at this PR.

Fixing the single key's unsigned reading is a **runner-input change** (the runner at a25eb17 prices an int V >= 0 as vault + V and refuses a negative one). It is a separate PR, after the DEC-016 FINAL marker, with its own P2 and ruling, and it is disclosed in the FINAL report. After the marker: an outcome-blind count of forward-1002 decisions and positions on pools whose stamped single V is >= 2^63.

Moving the follower to a new tree therefore changes more than key presence. That is why proof 1 below exists.

## Before you start (all required)

| # | Proof (quant-proof 10-10 (c)) | State when this PR opened |
| --- | --- | --- |
| 1 | **P0, deploy-delta equality on recorded raw input.** Record raw getBlock responses and pool-account reads for one fixed slot range (September preferred, or 10-05T08; low rps; record credits). Run the deployed follower code (36dd10e / a10e0568 / cea9783) and the new follower with the flag off and on. Six streams, md5, event-V keys dropped from trades: all equal. If they differ: put each difference behind the flag, or treat it as a runner-input change (item 4 on these tapes, an outcome-blind count of differing rows, and a manager + quant-proof ruling). | NOT DONE |
| 2 | P1 tests, including (d), (e) and (f): `tools/test_fast_tip_follower_event_v.py`. | Done in the PR (fixtures) |
| 3 | Blob pin at deploy: steps 2 and 4 below, recorded in LAB_STATE. | Code done; deploy check NOT DONE |
| 4 | **P2, runner md5.** Runner `a25eb17`, `scripts/mal-fast/fast-forward-paper.json`, `ARM_HEAD=1`, on the item 1 tapes (old follower against the new one with the flag on). md5 of `decisions.jsonl`, `positions.jsonl`, intents, **`exp012-gate.jsonl`** and the grad-stream output: all equal. Print md5s only. One heavy MiScusi job. | NOT DONE |
| 5 | **DuckDB ledger fixture** in fast-0's ledger venv: `TIP_JSON_COLUMNS` read over rows that carry the new keys, including a negative `virtual_quote_reserves`. Arrays equal. | NOT DONE |
| 6 | Acceptance after the restart (step 7 below), including the outcome-blind P7 line-1 check. | NOT DONE |
| 7 | Shadow side (#503) before soak hours count: event-mode `v_of` / `v_lamports` / `q_lamports` use the mapped event V; event mode equals const mode by md5 on the 09-20 identity day; item 14(b) in event mode. | NOT DONE (separate PR) |

Seals (quant-proof (b)): no decoder or walker blob is edited; the runner tree is not redeployed; proof replays print md5s only and use September tape or 10-05T08; no forward-1002, forward-1002ev or walk-2 file is opened.

## Steps

Run on `mal-fast-0` from the manager's clone `~/MAL`. Get every timestamp from `date -u`.

### 1. Pick the commit

```sh
SHA=<merged sha of the event-V PR, or later main that passed proofs 1, 4 and 5>
DEST=/var/lib/mal/fast-tip-follower-src/$SHA
git -C ~/MAL fetch origin
git -C ~/MAL cat-file -e "$SHA^{commit}"
```

### 2. Check the blobs in git (dry run, no write)

```sh
for f in observe/trade_decode.py observe/trade_store.py tools/pump_history_backfill.py tools/fast_tip_follower.py; do
  printf '%s %s\n' "$(git -C ~/MAL rev-parse "$SHA:$f")" "$f"
done
```

The first three must equal the pin table above. Stop if any differs. Record `tools/fast_tip_follower.py`'s blob too.

### 3. Extract the tree (dry run first)

```sh
echo "DRY-RUN: git -C ~/MAL archive $SHA tools observe | sudo -u ubuntu tar -x -C $DEST.new"
sudo install -d -o ubuntu -g ubuntu -m 0755 /var/lib/mal/fast-tip-follower-src
sudo -u ubuntu mkdir "$DEST.new"
git -C ~/MAL archive "$SHA" tools observe | sudo -u ubuntu tar -x -C "$DEST.new"
sudo -u ubuntu mv "$DEST.new" "$DEST"
sudo chmod -R a-w "$DEST"
```

`tools` and `observe` are enough: the follower imports `observe.trade_decode`, `observe.trade_store`, `tools.pump_history_backfill`, `tools.backfill_verify` and `tools.tape_lines`. Since route 1 it no longer imports `tools.pumpswap_tx` (the deployed follower does).

### 4. Check the pins from the new tree, with the service's venv

```sh
cd "$DEST"
sudo -u ubuntu /var/lib/mal/fast-forward/venv/bin/python -B -m tools.fast_tip_follower --check-decoder-pins; echo "rc=$?"
sudo -u ubuntu /var/lib/mal/fast-forward/venv/bin/python -B -c 'import observe.trade_decode as m, tools.fast_tip_follower as f; print(m.__file__, f.__file__)'
```

The first command must print `"pinned": true` and `rc=0`. Both paths printed by the second must be under `$DEST`.

### 5. Drop-in for the follower only (dry run first)

Write it to a scratch file and read it through before installing:

```ini
# /etc/systemd/system/mal-fast-tip-follower.service.d/20-event-v.conf
[Service]
WorkingDirectory=/var/lib/mal/fast-tip-follower-src/<SHA>
ReadOnlyPaths=/var/lib/mal/fast-tip-follower-src
ExecStart=
ExecStart=/var/lib/mal/fast-forward/venv/bin/python -m tools.fast_tip_follower --out /var/lib/mal/sealed/fast-trades-tip --creates-out /var/lib/mal/sealed/fast-creates-tip --state-dir /var/lib/mal/fast-tip-follower --rps 15 --fetch-workers 8 --max-keep-days 3 --trade-event-v
```

- The `ExecStart=` line must equal the unit's current one plus `--trade-event-v`. Check with `systemctl show -p ExecStart mal-fast-tip-follower` first. Same venv, same flags.
- `ReadOnlyPaths=` adds to the unit's list. The unit keeps `ReadOnlyPaths=/var/lib/mal/fast-forward` (the venv).

```sh
sudo install -d -m 0755 /etc/systemd/system/mal-fast-tip-follower.service.d
sudo install -m 0644 <scratch>/20-event-v.conf /etc/systemd/system/mal-fast-tip-follower.service.d/20-event-v.conf
sudo systemctl daemon-reload            # does not restart anything
systemctl cat mal-fast-tip-follower
systemctl show -p WorkingDirectory -p ExecStart mal-fast-tip-follower
sudo systemd-analyze verify /etc/systemd/system/mal-fast-tip-follower.service
```

`mal-fast-forward-paper` and `mal-fast-grad-stream` must still show `WorkingDirectory=/var/lib/mal/fast-forward/src`.

### 6. Restart

When:

- as early as possible once proofs 1, 4 and 5 are done (quant-proof (c): P0, the P2 runner md5, the DuckDB fixture), inside 10:00–20:00Z;
- never 23:45–00:30Z (runner daily restart, 00:15Z archive and ledger jobs) or around 05:00Z (daily review);
- latest **10-13T12Z**, and in any case before 10-16T00:30Z (H5 reinstall) and 10-16T01 (walk 2 / CAP-PICK count). The restart re-reads the cached singular V of active pools and changes gate answers for them, so it must land while no CAP-PICK in-scope mint exists.

If A is not live by 10-13T12Z, C1-NF does not go live in event mode, and there is no silent switch to C.

Before:

```sh
date -u
df -h /var/lib/mal
systemctl show mal-forward.slice -p MemoryCurrent
systemctl show user-1002.slice -p MemoryCurrent
grep -c '"reason":"backlog_jump"' /var/lib/mal/sealed/fast-trades-tip/gaps.jsonl
grep -c '"reason":"unfetchable"' /var/lib/mal/sealed/fast-trades-tip/gaps.jsonl
```

These are counts only. Do not open trade, create or migration files.

```sh
sudo systemctl restart mal-fast-tip-follower
```

### 7. Accept (quant-proof (c) item 6)

All of these, read from `/var/lib/mal/fast-tip-follower/status.json` and the two `grep -c` counts:

- no new `backlog_jump` or `unfetchable` row;
- `lag_slots` back to 2–3 within 2 min;
- `trade_event_v` true, `decoder_blobs` equal to the pin table, `decoder_blobs_pinned` true;
- after 10 min: `event_v_stamped` > 0, `event_v_mismatch` = 0, `event_v_missing_pumpswap` = 0, `event_v_errors` = 0. (`event_v_missing` also counts bonding rows without `ix_name`. The PumpSwap Buy/Sell rule is `event_v_missing_pumpswap`.) In event mode one missing V marks a pool bad, so mismatch alone is not enough;
- the outcome-blind P7 line-1 check on a 1,000-print stride sample of stamped canonical-pool prints: at least 99% within 1 bp. Counts only. No C1-NF label, fill or P&L, and the sample is not keyed to C1-NF picks. (No tool for this is in the PR.)

### 8. Roll back if any check fails

```sh
sudo rm /etc/systemd/system/mal-fast-tip-follower.service.d/20-event-v.conf
sudo systemctl daemon-reload
sudo systemctl restart mal-fast-tip-follower
```

The follower goes back to the shared tree and its old code. Rows written after that lose only the extra keys. Keep `$DEST` for the audit.

A rollback is also a restart. It re-reads the cached singular V of active pools, the same as step 6, so it uses the same window as step 6: inside 10:00–20:00Z, never 23:45–00:30Z, never around 05:00Z. After 10-16T00:30Z a rollback needs a manager plus quant-proof ruling first.

### 9. Record

- LAB_STATE row 85 (the follower row): the `date -u` time, `$SHA`, the four blobs from step 2, the flag, the P0 / P2 / DuckDB job ids, and the first-hour counters. Note that the restart re-reads the cached singular V of active pools. Note the cut-over hour: archived tip hours before it have no event V, so the C1-NF shadow's 26 h bootstrap has none before it.
- MiScusi notebook entry (kind decision, refs to the P0 and P2 jobs).
- `data/console.json` event.
