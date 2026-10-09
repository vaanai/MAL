# C1-NF executor runbook (DEC-026)

The C1-NF live measurement canary: 0.05 SOL per trade, on a **second wallet**, on `mal-fast-0`, unit `mal-c1nf-executor`, user `mal-live`. Its design follows the H5 canary's ([h5-executor.md](h5-executor.md), DEC-024). Read that runbook for the reasons behind each check. This one gives C1-NF's paths, the second-wallet steps, and the ways C1-NF differs from H5.

**Status of this runbook.** It was written without host access, from DEC-026 and the H5 runbook (see "Not verified" at the end). The executor itself (`tools/c1nf_executor.py`, its launcher, configs, base unit, drop-ins and `check-c1nf-unit.py`) comes from the executor PR (branch `claude/c1nf-executor-v2`), which was not merged when this was written. **Which sha: only the one the manager names in a PR comment after DEC-026 section 11 item 9 is merged.** Until then the installer refuses (`<file> is missing or empty at <sha>`).

**Two wallets, one host.** Nothing here reads, writes, stops or restarts anything of H5's: not its unit, wallet, key, `/etc/mal-h5`, `/var/lib/mal-live/h5`, pinned tree, `TIER` or watchdog. **Do not remove the wallet-wide `/var/lib/mal-live/STOP` for C1-NF's sake.** While it is there, this unit cannot send (DEC-026 section 5).

## Who does what

| Step | Who |
| --- | --- |
| Name the sha (PR comment), make the manifest, give the go after DEC-026 section 11 items 1 to 23 | manager |
| Create the second keypair and give the **public** address only; keep the withdraw address | Helm (root) |
| Install the key, the pinned tree, the hash check, drop-ins, auditd, the watchdog, the keyless dry run | Helm (root) |
| Fund the wallet with 0.5 SOL (O-1) | owner |
| **Create `/etc/mal-c1nf/TIER` = `T1` and `/etc/mal-c1nf/LIVE_OK`** (root:root 0644), only on the manager's written go | Helm (root) |
| Remove `LIVE_OK` (stops new buys at once) | Helm or the manager (`sudo rm`) |
| Edit `TIER` | Helm, only on the manager's written ask (DEC-026 section 10). `T2` also needs the owner's dated line |
| `STOP`, `HALT`, status, daily check | manager (`sudo` on fast-0) |
| Root sell-and-close of an abandoned position, withdraw | Helm or the owner |

## What is where

| Thing | Path |
| --- | --- |
| Second wallet key (never printed, never in a repo) | `/etc/mal-c1nf-key/c1nf-wallet.json`, root:root 0400, in `/etc/mal-c1nf-key` root:root 0700 |
| How the key reaches the unit | Only the live drop-in's `LoadCredential=c1nf-wallet:/etc/mal-c1nf-key/c1nf-wallet.json`. No other unit has this credential. This unit never gets `probe-wallet` (H5's) |
| Pinned tree (root:root, one per sha, never changed) | `/usr/local/lib/mal-c1nf-exec/<sha>/`, `current` -> `<sha>`, venv at `/usr/local/lib/mal-c1nf-exec/venv` |
| Base unit (keyless dry run), written only by the installer | `/etc/systemd/system/mal-c1nf-executor.service` |
| Live drop-in (the only thing that hands over the key) | `/etc/systemd/system/mal-c1nf-executor.service.d/live.conf` |
| Shadow-feed drop-in (host path, Helm writes it) | `/etc/systemd/system/mal-c1nf-executor.service.d/10-shadow-feed.conf`, one line `BindReadOnlyPaths=-<shadow dir>:/srv/mal-c1nf-shadow` |
| Watchdog units, config and state | `/etc/systemd/system/mal-c1nf-watch.{service,timer}`, `/etc/mal-c1nf-watch/watch.env` (root:root 0600 in a root:root 0700 dir), `/var/lib/mal-c1nf-watch/` |
| State dir (mal-live 0700), the only path the unit can write | `/var/lib/mal-live/c1nf/` |
| Live state, counters, ledger | `/var/lib/mal-live/c1nf/live/{state-live.json,h5-counters.json,h5-ledger.jsonl}` (#504's names: the C1-NF executor reuses H5's counters and ledger classes; dry run in `.../dryrun/`) |
| `LIVE_OK` (the gate the executor cannot create) | `/etc/mal-c1nf/LIVE_OK`: a regular file, root:root, mode **exactly 0644**, no symlink, parent `/etc/mal-c1nf` root:root 0755 |
| `TIER` | `/etc/mal-c1nf/TIER`, the same checks, content exactly `T1` or `T2`. Missing or invalid means T1 (the lowest). The C1-NF table, not H5's |
| C1-NF's `STOP`, `HALT` | `/var/lib/mal-live/c1nf/STOP`, `/var/lib/mal-live/c1nf/HALT` (the executor only tests that they exist) |
| Wallet-wide `STOP`, `HALT` (shared with H5) | `/var/lib/mal-live/STOP`, `/var/lib/mal-live/HALT`. The executor honours both. The `STOP` there stays until H5's Step 11 |
| Picks the executor reads, inside the unit | `/srv/mal-c1nf-shadow` (config `intents_file`): the C1-NF shadow's output directory (#503) |
| Root-only Helius env, shared with the probe and H5 | `/etc/mal-probe-rpc/helius.env` (root:root 0600 in a root:root 0700 dir). No Jito, Sender, LaserStream or paid RPC |

**Limits (DEC-026 section 6, code constants, config may only lower).** Stake 0.05 SOL (code ceiling 0.10, lowered by the live config). At most 2 open. At most 30 buy attempts a UTC day. Daily stop 0.20 SOL realized. Total stop min(0.30, 35% of the wallet at tier start), which is **0.175 SOL at 0.5 SOL**. The exposure test is kept: realized loss plus open exposure plus the new stake must stay inside the stop. Priority 505,000 lamports per send. `end_ms` 2026-10-24T00:30Z. A pick without the decision-time `q_lamports` and `base_reserve` gets no buy. From 2026-10-16T01Z the CAP-PICK oracle (#509) fails closed: no working oracle means no buy.

**Synthetic pools are traded** (EXP-025 Amendment 2). `synthetic_share_high` is alert-only for C1-NF. **No one splits C1-NF outcomes by synthetic class before the final look**: no Discord line, daily-note row, Console entry or message. The daily check and the watchdog print nothing by class (`c1nf-daily-check.py`, CLASS-BLIND).

## Before Helm starts (manager)

1. The sha, in a PR comment, after the executor PR is merged. It must contain `EXP/EXP-025-c1nf-part1-prereg.md`, and its `c1nf-executor-live.json` must hold the stake, priority, `end_ms` and `state_dir` of DEC-026 section 6. The installer refuses otherwise.
2. The manifest, made in the manager's own clone: `scripts/mal-fast/make-c1nf-manifest.sh <40-char-sha> > c1nf-manifest.txt`. Give it to Helm and keep a copy.
3. The C1-NF shadow's output directory (absolute path of the MiScusi job user's `~/data/c1nf-shadow`), with the shadow running at the reviewed #503 head, and its pinned model hash (DEC-026 section 11 item 12).
4. The Discord webhook for the watchdog (to Helm over the private channel, never into a repo), the funded total (0.5), and the wallet's public address once Helm has given it.

## Wind-down: before every stop of the live unit

The only exception is an emergency `HALT`.

```
sudo rm -f /etc/mal-c1nf/LIVE_OK                 # no new buys (or: sudo touch /var/lib/mal-live/c1nf/STOP)
S="sudo /usr/local/lib/mal-c1nf-exec/venv/bin/python -I -B -u /usr/local/lib/mal-c1nf-exec/current/launcher.py --config /usr/local/lib/mal-c1nf-exec/current/c1nf-executor-live.json --status"
$S | grep '^\[live\]'                            # repeat until: [live] ... open=0/2 pending=0 ...
```

A C1-NF position exits 300 s after landing. The sell plan escalates at landing + 315 s and its deadline is landing + 370 s, so wait up to about 7 minutes. If `open` or `pending` is still above 0, **do not stop the unit**: read the journal and the alerts (stuck position: "Sell-and-close" below).

## Helm's steps, in order

Steps 3 to 5 run in one root shell (`sudo -i`) from `/root`. The others use `sudo` per command.

**Step 1. Do not touch H5.** Leave its unit, wallet, key, `/etc/mal-h5`, `/var/lib/mal-live/h5`, watchdog and `TIER` alone. Leave the wallet-wide `/var/lib/mal-live/STOP` in place.

**Step 2. Create the second keypair.** Never print it. Never put it in a repo.

```
sudo install -d -m 0700 -o root -g root /etc/mal-c1nf-key
sudo sh -c 'umask 077; solana-keygen new --no-bip39-passphrase --silent --outfile /etc/mal-c1nf-key/c1nf-wallet.json'
sudo chmod 0400 /etc/mal-c1nf-key/c1nf-wallet.json
sudo solana-keygen pubkey /etc/mal-c1nf-key/c1nf-wallet.json     # the PUBLIC address: give this, and only this, to the manager
sudo stat -c '%U:%G %a %n' /etc/mal-c1nf-key /etc/mal-c1nf-key/c1nf-wallet.json
#   expect: root:root 700 /etc/mal-c1nf-key | root:root 400 /etc/mal-c1nf-key/c1nf-wallet.json
```

Use whatever key tool Helm prefers. The result must be a JSON keypair file the executor loads, as the probe key is. **Check that the address differs from the H5 wallet `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk`.** Keep the withdraw address with Helm (DEC-019 section 6 item 6).

**Step 3. Auditd watch on the new key** (as for the probe key):

```
auditctl -w /etc/mal-c1nf-key/c1nf-wallet.json -p rwa -k mal-c1nf-key
echo '-w /etc/mal-c1nf-key/c1nf-wallet.json -p rwa -k mal-c1nf-key' > /etc/audit/rules.d/mal-c1nf-key.rules
#   expect: auditctl -l | grep mal-c1nf-key prints the rule
```

**Step 4. Pinned install** from a fresh root-owned clone at the named sha, with the manager's manifest. The unit must be stopped. The installer never touches H5.

```
git clone https://github.com/vaanai/MAL.git /root/mal-c1nf-<short> && cd /root/mal-c1nf-<short> && git checkout --detach <sha>
scripts/mal-fast/install-c1nf-executor-pinned.sh <sha> /root/c1nf-manifest.txt
#   expect: "manifest verified", then "installed commit <sha> into /usr/local/lib/mal-c1nf-exec/<sha>", then a BEGIN-MANIFEST ... END-MANIFEST table
diff <(sed -n '/^BEGIN-MANIFEST/,/^END-MANIFEST/p' <install log> | sed '1d;$d') <(sort -k2 /root/c1nf-manifest.txt)
#   expect: no output
```

Refusals and what they mean: `the live config differs from DEC-026 section 6 in: ...` (wrong sha or config: stop and tell the manager); `/etc/mal-c1nf/LIVE_OK exists` (remove it first; it is created again only after this install's hash check); `.../live.conf exists` (move it to `/root/disabled/` first, so the install and the dry run are keyless); `/etc/mal-c1nf-key must be a real root:root 0700 directory` or `c1nf-wallet.json must be a regular root:root 0400 file` (fix the modes from Step 2).

**Step 5. Directories and the shadow feed.**

```
install -d -m 0755 -o root -g root /etc/mal-c1nf /srv/mal-c1nf-shadow
install -d -m 0700 -o mal-live -g mal-live /var/lib/mal-live/c1nf
install -d -m 0755 -o root -g root /etc/systemd/system/mal-c1nf-executor.service.d
sed "s#__SHADOW_DIR__#<shadow dir from the manager>#" /usr/local/lib/mal-c1nf-exec/current/mal-c1nf-executor-shadow-feed.conf \
  > /etc/systemd/system/mal-c1nf-executor.service.d/10-shadow-feed.conf
/usr/bin/python3 -I /usr/local/lib/mal-c1nf-exec/current/check-c1nf-unit.py --shadow-feed /etc/systemd/system/mal-c1nf-executor.service.d/10-shadow-feed.conf
systemctl daemon-reload
stat -c '%U:%G %a %F %n' /etc/mal-c1nf /srv/mal-c1nf-shadow /var/lib/mal-live/c1nf
#   expect: root:root 755 directory /etc/mal-c1nf | root:root 755 directory /srv/mal-c1nf-shadow | mal-live:mal-live 700 directory /var/lib/mal-live/c1nf
```

(The `__SHADOW_DIR__` placeholder is the H5 convention. Use whatever the executor PR's feed drop-in names.)

**Step 6. Sandbox and credential checks** (read-only; they change nothing of H5's):

```
systemctl show mal-c1nf-executor -p ProtectHome -p ProtectSystem -p TemporaryFileSystem -p ReadWritePaths -p RestartPreventExitStatus
#   expect: ProtectHome=tmpfs | ProtectSystem=strict | TemporaryFileSystem=/var/lib/mal:ro | ReadWritePaths=/var/lib/mal-live/c1nf | RestartPreventExitStatus=2
systemctl cat mal-c1nf-executor | grep -c '^LoadCredential'       # expect 0 now: the base unit is keyless
systemctl cat mal-h5-executor | grep -c 'mal-c1nf-key'           # expect 0: H5's unit never names the second wallet
```

**Step 7. Keyless dry run** on the real feed (DEC-026 section 11 item 16): at least 5 complete simulated round trips, 0 simulate errors.

```
systemctl start mal-c1nf-executor
$S                       # (S from Wind-down, with c1nf-executor.json for the dry run)
#   expect the first lines to show stop_file=False halt_file=False live_ok=live_ok_missing (/etc/mal-c1nf/LIVE_OK)
journalctl -u mal-c1nf-executor --since -1h | grep -c simulate_error   # expect 0
```

Report to the manager: minutes run, restarts, any startup refusal, simulated round trips, simulate errors.

**Step 8. Watchdog.**

```
install -d -m 0700 -o root -g root /etc/mal-c1nf-watch
install -m 0600 -o root -g root /dev/null /etc/mal-c1nf-watch/watch.env
# write exactly four lines (an editor as root; never echo the webhook into shell history):
#   C1NF_WATCH_DISCORD_WEBHOOK=<webhook>
#   C1NF_WATCH_FUNDED_SOL=0.5
#   C1NF_WATCH_SHADOW_DIR=<shadow dir from the manager>
#   C1NF_WATCH_WALLET=<the PUBLIC address from Step 2>
install -m 0644 -o root -g root /usr/local/lib/mal-c1nf-exec/current/mal-c1nf-watch.service /usr/local/lib/mal-c1nf-exec/current/mal-c1nf-watch.timer /etc/systemd/system/
systemctl daemon-reload
systemd-run --wait --pipe -p EnvironmentFile=/etc/mal-c1nf-watch/watch.env /usr/bin/python3 -I -S -B -u /usr/local/lib/mal-c1nf-exec/current/c1nf-watch.py --test-message
#   expect: "c1nf_watch: test message posted", and "[C1-NF watch] test message ..." in Discord. Tell the manager to look.
systemctl enable --now mal-c1nf-watch.timer
```

This is C1-NF's own watchdog. Do not edit H5's `watch.env` or timer.

**Step 9. Sudoers for the manager's daily check.** The manager prints the exact lines with `python3 -I scripts/mal-fast/c1nf-daily-check.py --print-sudoers`. Helm installs them as `/etc/sudoers.d/mal-c1nf-check` (root:root 0440) after `visudo -cf`. There is no wildcard. They allow `stat` and `dd iflag=nofollow` of fixed C1-NF state paths only, never the key.

**Step 10. Funding.** The owner sends 0.5 SOL to the public address. Helm confirms the finalized balance and the signature to the manager.

**Step 11. On the manager's written go only** (after DEC-026 section 11 items 1 to 23):

```
install -m 0644 -o root -g root /usr/local/lib/mal-c1nf-exec/current/mal-c1nf-executor-live-pinned.conf \
  /etc/systemd/system/mal-c1nf-executor.service.d/live.conf
/usr/bin/python3 -I /usr/local/lib/mal-c1nf-exec/current/check-c1nf-unit.py --dropin /etc/systemd/system/mal-c1nf-executor.service.d/live.conf
systemctl cat mal-c1nf-executor | grep '^LoadCredential'
#   expect exactly: LoadCredential=c1nf-wallet:/etc/mal-c1nf-key/c1nf-wallet.json
printf 'T1\n' > /etc/mal-c1nf/TIER && chown root:root /etc/mal-c1nf/TIER && chmod 0644 /etc/mal-c1nf/TIER
install -m 0644 -o root -g root /dev/null /etc/mal-c1nf/LIVE_OK
systemctl daemon-reload && systemctl enable mal-c1nf-executor && systemctl restart mal-c1nf-executor
$S | head -3
#   expect: live_ok=valid (/etc/mal-c1nf/LIVE_OK), tier T1, stake_sol=0.050 max_open=2 max_trades_per_day=30
```

While the wallet-wide `/var/lib/mal-live/STOP` is there the unit still sends nothing, and the daily check says `c1nf_idle` ("the wallet-wide STOP exists"). That is expected until H5's Step 11. It is not Helm's to remove for C1-NF.

## Tier steps (DEC-026 section 10)

T1 is the canary at 0.05 SOL. A step to 0.10 SOL needs profits and the owner's dated line. T2 is inactive until the owner's dated line, and it cannot be funded from a 0.5 SOL wallet. Helm edits `/etc/mal-c1nf/TIER` only on the manager's written ask, with the same owner and mode (root:root 0644). The daily check alerts `c1nf_tier_t2` whenever the file says `T2`, so the dated line is checked each day.

## Stop, halt, status

| Action | Command (manager, `sudo`) | Effect |
| --- | --- | --- |
| Stop new buys | `sudo touch /var/lib/mal-live/c1nf/STOP` | No new buys; open positions exit on the timer |
| Freeze everything | `sudo touch /var/lib/mal-live/c1nf/HALT` | No buys, no sells. Only if the executor or the wallet is wrong |
| Close the gate | `sudo rm /etc/mal-c1nf/LIVE_OK` | Same as `STOP` for buys. Only Helm creates it again |
| Status (local files only) | `$S` from Wind-down | Counters, limits, kill files, the gate |
| Hard stop | Wind-down first, then `sudo systemctl stop mal-c1nf-executor` | A restart resumes pending signatures without buying again |
| Clear a latched halt | `--clear-halt <name>` as in the H5 runbook, with the C1-NF paths | Never followed by a retune of the rule, model, cap or threshold. Tell the owner first |

The wallet-wide `/var/lib/mal-live/{STOP,HALT}` also stop C1-NF. They stop H5 too, so the C1-NF files are the ones to use.

## Daily check (manager) and the watchdog

`scripts/mal-fast/c1nf-daily-check.py`. It is read-only and holds no key. It prints `INFO`/`OK`/`ALERT` lines and exits 1 on any alert. The watchdog (`c1nf-watch.py`, every 5 minutes, as root from the pinned tree) runs the same engine and posts new alerts, repeats every 6 h, `RESOLVED`, restarts, `STOP` placed or removed, tier changes, and one wallet line a day to Discord.

```
/usr/bin/python3 -I /home/claude/MAL/scripts/mal-fast/c1nf-daily-check.py --wallet <public address> --funded-sol 0.5 --public-rpc \
  --window-hours 24 --shadow-dir /home/<jobuser>/data/c1nf-shadow
```

What it checks is in the file's header. In short: the unit files against the pinned copies; the credential (only `c1nf-wallet`, never H5's); `LIVE_OK` and `TIER`; the pinned live config against DEC-026 section 6; halts, `HALT` files, stuck, unmanaged or over-cap positions; the effective total stop and how much of it is used; the wallet against funded + realized; a stale heartbeat or feed bind; an idle canary; the watchdog's health; budget stops; executor `ALERT` rows; refusals by reason name; the fill-rate alert (rule 8); late sells (rule 5); missing guard inputs; and the CAP-PICK oracle pause from 2026-10-16T01Z.

**Class-blind.** It never opens a shadow picks or outcomes file. It reads ledger rows only through fixed keys. It never prints a name that names the synthetic class, except the A3 structure alert `synthetic_share_high`. A line that would still name the class is withheld and raises `class_blind_withheld`, and the watchdog refuses to post it. Do not add a by-class line to any report before the final C1-NF look (EXP-025 Amendment 2 item 5). A breach is recorded and the read is reported compromised.

## Sell-and-close an abandoned position (Helm or the owner, root)

As in the H5 runbook's "Sell-and-close", with the C1-NF state dir and the C1-NF key: the credential is `c1nf-wallet` from `/etc/mal-c1nf-key/c1nf-wallet.json`. Never use the H5 key for a C1-NF mint, or the C1-NF key for an H5 mint. Wind-down first. Then `--mark-closed` with the C1-NF config, and report the signature to the manager.

## Rollback

Wind-down, `systemctl stop mal-c1nf-executor`, then `ln -sfn <previous sha> /usr/local/lib/mal-c1nf-exec/current` only if that sha was hash-checked when it was installed, then run the installer's hash table again by hand (`sha256sum` of the tree against the manifest you kept). Otherwise reinstall from the manager's named sha. Never edit a file under `/usr/local/lib/mal-c1nf-exec/<sha>/`.

## Never

- `ufw` or firewall, `sshd`, cloudflared or Cloudflare changes (CLAUDE.md).
- A key, a webhook or a withdraw address in a repo file, a PR, a note or a chat line.
- The H5 key for C1-NF, or the C1-NF key for H5. One credential per unit.
- Removing the wallet-wide `STOP` for C1-NF's sake.
- `LIVE_OK` before the manager's written go, or `TIER` = `T2` without the owner's dated line.
- A by-class split of any C1-NF outcome before the final look.
- A retune after a halt.

## Not verified (written without host access)

- The executor PR's file names (launcher, configs, drop-ins, `check-c1nf-unit.py`, the `--status` text and the `__SHADOW_DIR__` placeholder) and its state file names (`h5-counters.json`, `h5-ledger.jsonl` under `/var/lib/mal-live/c1nf/live/`) are #504's. They must be checked against the merged executor.
- The live-config key names the installer and the daily check test (`stake_lamports`, `buy_priority_lamports`, `end_ms`, `state_dir`, `max_open`, `max_trades_per_day`, `daily_loss_lamports`, `total_loss_lamports`, `max_pick_age_s`, `wallet_floor_lamports`) are #504's.
- The skip-reason names the daily check counts (`oracle_*`, `seal*`/`cap_pick*`, `feed_stale`, `pick_stale`, `bad_intent:missing_q_lamports`, `bad_intent:missing_base_reserve`) and the halt names are #504's or DEC-026's wording. Unknown names are still counted and shown if they are name-shaped and do not name the class.
- The key-tool commands in Step 2 and the sandbox values in Step 6 are proposals; Helm owns the exact install (DEC-026 section 5).
- The pinned model file and its sha256 (DEC-026 section 11 item 12) are not installed by this installer. Their path and check belong to the executor PR.
