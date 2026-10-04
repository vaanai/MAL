# Probe executor runbook (DEC-019)

The executor is `tools/probe_executor.py` (keyless dry run) plus `tools/probe_live.py` (live). Unit: `mal-probe-executor` on `mal-fast-0`, user `mal-live`. The key is created by Helm or the owner: see [probe-wallet.md](probe-wallet.md) (PR #289). The manager never sees it.

## Who does what

| Step | Who |
| --- | --- |
| Install and start the dry-run unit | manager |
| Create the key, fund 0.5 SOL, name the withdraw address | Helm or owner |
| Install the live drop-in and restart | Helm or owner (after the DEC-019 section 6 checklist is all done) |
| Stop, STOP file, status | anyone with sudo on `mal-fast-0` |
| Withdraw | Helm or owner |

## 1. Dry run (default)

`scripts/mal-fast/mal-probe-executor.service` runs `probe-executor.json` (`"mode": "dryrun"`), no `--live`, no key. It builds and simulates buys and sells and logs `mode="dryrun"` rows to `/var/lib/mal-live/probe-fills.jsonl`. First line of the journal: `probe_executor mode=dryrun ...`.

Live needs BOTH switches. With only one, the process runs the dry run and prints `WARNING live requested but ... is missing: running DRY RUN`.

| Switch | Where |
| --- | --- |
| `"mode": "live"` | `scripts/mal-fast/probe-executor-live.json` |
| `--live` | `scripts/mal-fast/mal-probe-executor-live.conf` (systemd drop-in) |

## 2. Switch to live

Preconditions (DEC-019 section 6): reviewer and security review merged; 6 h of dry run with 0 build errors; paper runner up after the kill review; key created and funded 0.5 SOL; withdraw address named.

```
sudo install -D -m 0644 scripts/mal-fast/mal-probe-executor-live.conf /etc/systemd/system/mal-probe-executor.service.d/live.conf
sudo systemctl daemon-reload
sudo systemctl restart mal-probe-executor
journalctl -u mal-probe-executor -n 20 --no-pager   # expect: mode=LIVE user=<public key>
```

At start live mode sets RLIMIT_CORE 0 and PR_SET_DUMPABLE 0, then loads `/var/lib/mal-live/probe-wallet.json`. It refuses (exit, no key text in the message) unless the file is a regular file, mode 0400 or 0600, owned by the running uid, and every parent directory above `/var/lib/mal-live` is root-owned and not group/world-writable (the leaf dir may be owned by `mal-live`, not group/world-writable). Only the public key is printed.

Live state is `/var/lib/mal-live/state-live.json`, separate from the dry run's `state-dryrun.json`. Deleting it does not reset the budget: with live rows in the fill log and no state file, live refuses to start.

Back to dry run: `sudo rm /etc/systemd/system/mal-probe-executor.service.d/live.conf && sudo systemctl daemon-reload && sudo systemctl restart mal-probe-executor`.

## 3. Stop

- Soft: `touch /var/lib/mal-live/STOP`. No new buys, and no new sells or rebroadcasts. Txs already sent are still tracked to confirmation. **Open positions stay open** until the file is removed or the owner sells them by hand.
- Hard: `sudo systemctl stop mal-probe-executor`. A restart resumes any pending signature without re-buying.
- Automatic: 30 buy attempts, 0.25 SOL realized loss, 4 days from the first attempt. Each halts new buys only; open positions are still sold.

## 4. Status

```
cd /var/lib/mal/fast-forward/src
/var/lib/mal/fast-forward/venv/bin/python -m tools.probe_executor --config scripts/mal-fast/probe-executor-live.json --status
```

Prints stop-file presence, attempts out of 30, realized SOL, open and pending positions, and fill-row counts by kind. It reads local files only: no key, no URL, no RPC. (Run as `mal-live` or root, since `/var/lib/mal-live` is 0700.)

## 5. What each halt means

| Log or skip reason | Meaning | Action |
| --- | --- | --- |
| `limit:stop_file` | STOP file exists | remove it to resume |
| `limit:max_attempts` | 30 buys attempted (counts every send, landed or not) | probe is done |
| `limit:loss_cap` | realized loss at or past 0.25 SOL | probe is done; review |
| `limit:max_days` | 4 days since first attempt | probe is done |
| `limit:max_open` | 3 positions open or in flight | normal |
| `limit:sell_stuck` and `ALERT sell_stuck` | a sell failed or expired 5 times (config `sell_retries`); buys halted, the sell retries every 30 s | look at the sell rows (`fail_class`); STOP and sell by hand if it persists |
| `balance_guard` | wallet below size + 0.02 SOL | fund it or stop |
| `ALERT unpriced_position` | no V-priced quote for an open position; the executor never sells without a min_out | check the RPC and pool |
| `ALERT zero_token_balance` | the token account is empty but the position is open (a sell may have landed unseen) | reconcile by hand from the signatures in the fill log |
| `ALERT send_error` | `sendTransaction` failed; the loop keeps rebroadcasting the same signed tx | usually transient |
| `ALERT meta_missing` | tx confirmed but `getTransaction` had no meta for 60 s | check RPC; the position stays pending |
| `ALERT signature_mismatch` | RPC returned a different signature than we signed | stop and investigate |

Fill rows with `fail_class`: `expired` (blockhash passed, nothing landed, no cost), `slippage_exceeded`, `insufficient_funds`, `other`. A landed failure costs its fee and is booked into realized P&L.

## 6. Withdraw

Per [probe-wallet.md](probe-wallet.md) section 3: stop the unit, confirm no open positions in `--status`, then Helm or the owner runs `probe-withdraw.sh --to <OWNER_ADDRESS>`.

## Never

Never print, copy or commit the key file. Never raise a limit in config: config can only lower the DEC-019 maxima. Probe fills are never a book and never count toward any gate.
