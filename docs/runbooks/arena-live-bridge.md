# Arena live bridge: DRY install pack (S6)

`tools/arena_live_bridge.py` (PR #776) turns an arena book's hash-chained decision log into orders. This page is what Helm installs so that the bridge runs **DRY** on `mal-fast-0` before any book clears the gate. **Nothing here has been run on a host.** No book has cleared the promotion gate; DRY mode sends nothing, signs nothing and reads no key. Live trading needs a canary DEC and the owner's own OK under [DEC-031](../../DEC/DEC-031-forward-paper-arena.md) (see "Owner's own OK" below). Nothing on this page is a profit claim.

Scope: this is the bridge only (HV4 Haiku bots, FLOOR l1-floor-v2 and other rule books). H5 books use the H5 executor ([h5-executor.md](h5-executor.md)). This page does not touch the probe executor, the H5 executor, `ufw`, `sshd`, `cloudflared`, Cloudflare Access, or `/opt/miscusi`.

## 1. What DRY does and does not do

| DRY does | DRY does not |
| --- | --- |
| Tail `<arena_root>/decisions/<bot_id>/*.jsonl` read-only and verify the hash chain row by row | Open an RPC connection, import the solana stack, or read any key |
| Apply the gates (STOP/HALT, decision age <= 5 s, caps, frozen_sha pin, wallet floor on a **configured** number) | Know the real wallet balance (`dry_balance_lamports` in the config is a number, not a read) |
| Write-ahead state (`bridge-state-dry.json`) so a restart replays nothing | Build or simulate a transaction (the tx build/sign path is covered only by the offline stub-RPC test, see section 7) |
| Log each order it WOULD send (`bridge-log-dry.jsonl`, `would_send: true`) | Share state with live (`bridge-state-live.json` is a different file and is never created in DRY) |

Live needs ALL of: config `mode: "live"`, the `--live` flag, root-owned `LIVE_OK` and `TIER` files under `/etc/mal-arena-live/`, an explicit `end_ms`, a pinned `frozen_sha`, a valid chain, and the key from a systemd credential. None of these exist in the DRY install, by design.

## 2. Paths, user, files

| Item | Value |
| --- | --- |
| Unit | `mal-arena-live-bridge-dry.service` (one unit per `bot_id`; use a template instance if more than one book) |
| User | `mal-arena-bridge`: a **new** unprivileged system user, no login shell, no home. Not `mal-live`: the DRY bridge must not be able to read anything `mal-live` owns |
| Code | root-owned pinned copy `/opt/mal-arena-bridge/<sha>/` (a `git archive` of the merge sha, with a sha256 manifest), the same pattern as the H5 executor install. Needs `python3` with `numpy` (`tools/arena_paper.py` imports it). It does **not** need `solders` in DRY |
| Config | `/etc/mal-arena-live/dry.json`, root:root 0644, directory root:root 0755 |
| State and log (only writable path) | `/var/lib/mal-arena-bridge-dry/` mal-arena-bridge 0700. Deliberately not under `/var/lib/mal-live/` |
| Arena root (read-only) | the directory that holds `decisions/<bot_id>/`; Helm sets the real path. Mounted read-only into the unit |
| STOP / HALT (DRY only) | `/var/lib/mal-arena-bridge-dry/STOP` and `.../HALT`, set in `dry.json`. In DRY they are unit-local, so they do **not** touch the probe's wallet-wide `/var/lib/mal-live/STOP`. (In live the paths are pinned in code and cannot be set in config.) |
| `LIVE_OK`, `TIER` | **Must not exist** in the DRY install (`/etc/mal-arena-live/LIVE_OK`, `/etc/mal-arena-live/TIER`) |

### Config template `/etc/mal-arena-live/dry.json`

```json
{
  "mode": "dry",
  "bot_id": "<bot_id exactly as in decisions/<bot_id>/>",
  "frozen_sha": "<64 hex: the sha the book's start row repeats>",
  "root": "<arena root>",
  "state_dir": "/var/lib/mal-arena-bridge-dry",
  "end_ms": <explicit end instant, epoch ms>,
  "max_hold_ms": <the book's own max hold, ms>,
  "dry_balance_lamports": 600000000,
  "stop_file": "/var/lib/mal-arena-bridge-dry/STOP",
  "halt_file": "/var/lib/mal-arena-bridge-dry/HALT"
}
```

`bot_id`, `frozen_sha`, `max_hold_ms` and `end_ms` come from the manager's ask, one per book. Limits (stake 0.02 SOL, `max_open` 2, 10 trades/day, loss caps) are the T0 canary numbers in code; the config can only tighten them.

### Unit template `/etc/systemd/system/mal-arena-live-bridge-dry.service`

No credential lines of any kind. There is no drop-in directory for this unit; if `systemctl cat` ever shows a drop-in, that is a FAIL.

```ini
[Unit]
Description=MAL arena live bridge (DRY: logs would-send orders, sends nothing)

[Service]
Type=simple
User=mal-arena-bridge
Group=mal-arena-bridge
WorkingDirectory=/opt/mal-arena-bridge/<sha>
ExecStart=/usr/bin/python3 -m tools.arena_live_bridge --config /etc/mal-arena-live/dry.json
Restart=on-failure
RestartSec=10
# No network at all: DRY makes no RPC. A DRY bridge that tries one fails loudly instead of sending.
PrivateNetwork=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadWritePaths=/var/lib/mal-arena-bridge-dry
ReadOnlyPaths=<arena root>
MemoryMax=512M
# This unit has no key directives, no environment settings, no drop-ins and no mode switch to live.
```

## 3. Install steps (Helm; every step has a check)

1. Create the user and dirs (no credential material is touched):
   `sudo useradd --system --no-create-home --shell /usr/sbin/nologin mal-arena-bridge`
   `sudo install -d -m 0700 -o mal-arena-bridge -g mal-arena-bridge /var/lib/mal-arena-bridge-dry`
   `sudo install -d -m 0755 -o root -g root /etc/mal-arena-live`
2. Install the pinned code at `/opt/mal-arena-bridge/<sha>/` from the merge sha the manager names; record `sha256sum` of `tools/arena_live_bridge.py`, `tools/arena_declog.py`, `tools/arena_paper.py` in a manifest and send it to the manager.
3. Write `/etc/mal-arena-live/dry.json` (root:root 0644) from the template with the values the manager gave.
4. Install the unit above. `sudo systemctl daemon-reload`. Do not enable yet.
5. Run the keyless checklist **as the bridge user** (so "not readable" means not readable by the bridge):
   `sudo -u mal-arena-bridge sh /opt/mal-arena-bridge/<sha>/scripts/ops/bridge-dry-checklist.sh`
   Expect `RESULT PASS`. Any `FAIL` line: stop, paste the output to the manager. The script prints only paths and PASS/FAIL, never file contents.
6. One-shot DRY smoke, then start: `sudo -u mal-arena-bridge python3 -m tools.arena_live_bridge --config /etc/mal-arena-live/dry.json --once` (from the pinned dir) prints `mode=dry`. Then `sudo systemctl enable --now mal-arena-live-bridge-dry`.

## 4. How to verify DRY (any time)

```sh
systemctl cat mal-arena-live-bridge-dry | grep -Eci '^[[:space:]]*(Load|Set|Import)Credential'   # expect 0
systemctl cat mal-arena-live-bridge-dry | grep -Eci '^[[:space:]]*(Environment|EnvironmentFile|PassEnvironment)[[:space:]]*='   # expect 0
systemctl cat mal-arena-live-bridge-dry | grep -c -- '--live'          # expect 0
systemctl cat mal-arena-live-bridge-dry | grep -c '\.service\.d/'      # expect 0 (no drop-ins)
sudo test ! -e /etc/mal-arena-live/LIVE_OK && echo "gate closed"
sudo test ! -e /var/lib/mal-arena-bridge-dry/bridge-state-live.json && echo "no live state"
sudo head -c 600 /var/lib/mal-arena-bridge-dry/bridge-log-dry.jsonl    # kind=buy_sent rows carry "would_send":true
journalctl -u mal-arena-live-bridge-dry -n 20 --no-pager              # first line: arena_live_bridge mode=dry bot=...
```

`ALERT latch=chain_break` or `frozen_sha_mismatch` in the journal means the bridge will never buy until the manager clears the latch with the bridge stopped; tell the manager, do not edit the state file.

## 5. How to stop

| Want | Do |
| --- | --- |
| No new would-send orders, keep running | `sudo -u mal-arena-bridge touch /var/lib/mal-arena-bridge-dry/STOP` (remove to resume) |
| Freeze everything | `sudo -u mal-arena-bridge touch /var/lib/mal-arena-bridge-dry/HALT` |
| Stop the process | `sudo systemctl stop mal-arena-live-bridge-dry` (state is write-ahead; a restart replays nothing) |
| Remove | `sudo systemctl disable --now mal-arena-live-bridge-dry`; keep `/var/lib/mal-arena-bridge-dry/` for the manager's read |

## 6. Owner's own OK

The following need the **owner's own OK**, given by the owner (not relayed by any agent message) and recorded verbatim in a canary DEC. They are not part of this install and Helm does not do them on an agent's say-so:

1. The canary DEC itself (limits, stops, label "execution measurement"), merged with QP OK on the exact head and a reviewer verdict. It must say that the stop-loss backstop is best effort and the time stop is the only stop that always fires. DEC-031 allows only a canary after a first screen pass; stake above the canary needs the confirmation rule (same `bot_id` unchanged, >= 100 fresh attempts on >= 5 further UTC dates).
2. Installing a key: placing wallet credential material under root, adding `LoadCredential=` to a live drop-in. The DRY unit never gets one.
3. Funding the wallet (canary size, with the wallet floor above open exposure).
4. Creating `/etc/mal-arena-live/LIVE_OK` and `/etc/mal-arena-live/TIER` (T0), root-owned, only after the manager's go.
5. Changing the unit to run with `--live` and config `mode: "live"` (a different unit or drop-in, a reviewed change, not an edit of this one).
6. Any tier step above T0, and any change to `end_ms`.

Still open before any of that (from the PR #776 reviews): the live port has never run against a real RPC; the dry run does not simulate either leg on chain; `STATE_ROOT_LIVE` is unused; `closed_unknown_pnl` books no P&L; S3 (close-blocked filter wired in), S4 (worst-in-slot) and S5 (live-vs-paper parity) are not built.

## 7. What the repo proves, and what it does not

- `python3 -m tools.arena_bridge_dry_proof` runs the DRY bridge on a synthetic arena-format log (written with `DecisionLog`) and prints a JSON list of checks: hash chain, tamper latch, restart replays nothing, cursor-loss does not double-buy, STOP/HALT, decision age 4 s ok / 6 s refused, caps, frozen_sha pin, `end_ms`, wallet floor on a fake balance.
- `tools/test_arena_bridge_dry_proof.py` also builds and signs a real buy and sell transaction through the live port over the probe tests' stub RPC (throwaway key, `sendTransaction` is a recorder). That shows the tx builder runs; it does **not** simulate on chain.
- Not measured: any real RPC, any real fill, latency, landing rate, a real decision log from a running book, any P&L.
