# C1-NF executor runbook (DEC-026)

The C1-NF live measurement canary: 0.05 SOL per trade, on a **second wallet**, on `mal-fast-0`, unit `mal-c1nf-executor`, user `mal-live`. Its design follows the H5 canary's ([h5-executor.md](h5-executor.md), DEC-024). Read that runbook for the reasons behind each check. This one gives C1-NF's paths, the second-wallet steps, and the ways C1-NF differs from H5.

**Status of this runbook.** It was written without host access, from DEC-026 and the H5 runbook (see "Not verified" at the end). The executor module and its two configs (`tools/c1nf_executor.py`, `c1nf-executor.json`, `c1nf-executor-live.json`) come from the executor PR #530 (branch `claude/c1nf-executor-v2`, reference sha `32265af`), which was not merged when this was written. The launcher (`c1nf_exec_launcher.py`), the base unit (`mal-c1nf-executor.service`), the live and shadow-feed drop-ins, `check-c1nf-unit.py` and the rescue tool `tools/c1nf_sell_and_close.py` are in this runbook's PR (#531), built from H5's. The installer needs both PRs in one sha. **Which sha: only the one the manager names in a PR comment after DEC-026 section 11 item 9 is merged.** Until then the installer refuses (`<file> is missing or empty at <sha>`).

**Two wallets, one host.** Nothing here reads, writes, stops or restarts anything of H5's: not its unit, wallet, key, `/etc/mal-h5`, `/var/lib/mal-live/h5`, pinned tree, `TIER` or watchdog. **Do not remove the wallet-wide `/var/lib/mal-live/STOP` for C1-NF's sake.** While it is there, this unit cannot send (DEC-026 section 5).

## Who does what

| Step | Who |
| --- | --- |
| Pin Helm's public address and the model sha256 in `tools/c1nf_executor.py` (reviewed PRs), name the sha (PR comment), make the manifest, give the go after DEC-026 section 11 items 1 to 23 | manager |
| Create the second keypair and give the **public** address only; keep the withdraw address | Helm (root) |
| Install the key, the pinned tree, the hash check, drop-ins, auditd, the watchdog, the keyless dry run | Helm (root) |
| Fund the wallet with 0.5 SOL (O-1) | owner |
| **Create `/etc/mal-c1nf/TIER` = `T1` and `/etc/mal-c1nf/LIVE_OK`** (root:root 0644), only on the manager's written go | Helm (root) |
| Remove `LIVE_OK` (stops new buys at once) | Helm or the manager (`sudo rm`) |
| Edit `TIER` | Helm, only on the manager's written ask (DEC-026 section 10). `T2` also needs the owner's dated line |
| `STOP`, `HALT`, `FINAL_WRITTEN`, status, daily check, `--clear-halt` | manager (`sudo` on fast-0) |
| Root sell-and-close of an abandoned position (`--run-tool sell_and_close`), withdraw | Helm or the owner |

## What is where

| Thing | Path |
| --- | --- |
| Second wallet key (never printed, never in a repo) | `/etc/mal-c1nf-key/c1nf-wallet.json`, root:root 0400, in `/etc/mal-c1nf-key` root:root 0700 |
| How the key reaches the unit | Only the live drop-in's `LoadCredential=c1nf-wallet:/etc/mal-c1nf-key/c1nf-wallet.json`. No other unit has this credential. This unit never gets `probe-wallet` (H5's) |
| The wallet's **public** address, as code | `C1NF_WALLET_PUBKEY` in `tools/c1nf_executor.py`. `None` until a reviewed PR pins Helm's address. Live refuses to start without it (`startup_refused wallet_unpinned`) and refuses any other key, H5's above all (`wallet_is_h5`, `wallet_not_pinned_c1nf`). The rescue tool checks the same pin |
| The model's sha256, as code | `C1NF_MODEL_SHA256` in `tools/c1nf_executor.py` (DEC-026 section 11 item 12). Empty: live refuses to start (`model_unpinned`). A pick or shadow heartbeat naming another model latches `model_sha_mismatch` |
| Pinned tree (root:root, one per sha, never changed) | `/usr/local/lib/mal-c1nf-exec/<sha>/`, `current` -> `<sha>`, venv at `/usr/local/lib/mal-c1nf-exec/venv` |
| Base unit (keyless dry run), written only by the installer | `/etc/systemd/system/mal-c1nf-executor.service` |
| Live drop-in (the only thing that hands over the key) | `/etc/systemd/system/mal-c1nf-executor.service.d/live.conf` |
| Shadow-feed drop-in (host path, Helm writes it) | `/etc/systemd/system/mal-c1nf-executor.service.d/10-shadow-feed.conf`, one line `BindReadOnlyPaths=-<shadow dir>:/srv/mal-c1nf-shadow` |
| CAP-PICK oracle drop-in (host path, Helm writes it; DEC-026 Amendment 1 item B) | `/etc/systemd/system/mal-c1nf-executor.service.d/20-cap-pick.conf`, one line `BindReadOnlyPaths=-<CAP_PICK_OUT>:/srv/mal-cap-pick`, from the template `mal-c1nf-executor-cap-pick.conf` |
| Watchdog units, config and state | `/etc/systemd/system/mal-c1nf-watch.{service,timer}`, `/etc/mal-c1nf-watch/watch.env` (root:root 0600 in a root:root 0700 dir), `/var/lib/mal-c1nf-watch/` |
| State dir (mal-live 0700), the only path the unit can write | `/var/lib/mal-live/c1nf/` |
| Live state, counters, ledger | `/var/lib/mal-live/c1nf/live/{state-live.json,h5-counters.json,h5-ledger.jsonl,c1nf-extra.json}` (v2's names at `32265af`: the C1-NF executor reuses H5's counters and ledger classes; `c1nf-extra.json` holds its cooldown clock, the fill-selection table, the late-sell window and count-only refusals; dry run in `.../dryrun/`) |
| `LIVE_OK` (the gate the executor cannot create) | `/etc/mal-c1nf/LIVE_OK`: a regular file, root:root, mode **exactly 0644**, no symlink, parent `/etc/mal-c1nf` root:root 0755 |
| `TIER` | `/etc/mal-c1nf/TIER`, the same checks, content exactly `T1` or `T2`. Missing, invalid or inactive (`T2` today) means T1 (the lowest). The C1-NF table, not H5's |
| C1-NF's `STOP`, `HALT`, `FINAL_WRITTEN` | `/var/lib/mal-live/c1nf/STOP`, `/var/lib/mal-live/c1nf/HALT`, `/var/lib/mal-live/c1nf/FINAL_WRITTEN` (the executor only tests that they exist) |
| Wallet-wide `STOP`, `HALT` (shared with H5) | `/var/lib/mal-live/STOP`, `/var/lib/mal-live/HALT`. The executor honours both. The `STOP` there stays until H5's Step 11 |
| Picks the executor reads, inside the unit | `/srv/mal-c1nf-shadow` (config `intents_file`): the C1-NF shadow's output directory (#503), three hourly streams `c1nf-picks-*`, `c1nf-events-*`, `c1nf-outcomes-*` |
| CAP-PICK oracle file (from 2026-10-16T01Z) | Config `pick_file` = `/srv/mal-cap-pick/picks.jsonl` in the live config (DEC-026 Amendment 1 item B; not in the keyless dry-run config). Inside the unit it is the exporter's `$CAP_PICK_OUT/picks.jsonl`, bound read-only by `20-cap-pick.conf`. Without a readable, fresh file every pick in the seal window is refused |
| Root-only Helius env, shared with the probe and H5 | `/etc/mal-probe-rpc/helius.env` (root:root 0600 in a root:root 0700 dir). No Jito, Sender, LaserStream or paid RPC |

**Limits (DEC-026 section 6, code constants, config may only lower).** Stake 0.05 SOL (code ceiling 0.10, lowered by the live config). At most 2 open. At most 30 buy attempts a UTC day. Daily stop 0.20 SOL realized. Total stop min(0.30, 35% of the wallet at tier start), which is **0.175 SOL at 0.5 SOL**. The exposure test is kept: realized loss plus open exposure plus the new stake must stay inside the stop. Priority 505,000 lamports per send. `end_ms` 2026-10-24T00:30Z. A pick without the decision-time state (`q_lamports`, `base_reserve`, `v_lamports`, `state_slot`) gets no buy. From 2026-10-16T01Z the CAP-PICK seal fails closed: no `pick_file`, an oracle heartbeat older than 60 s, an oracle error, an undecided mint or no `FINAL_WRITTEN` means no buy.

**Synthetic pools are traded** (EXP-025 Amendment 2). `synthetic_share_high` is alert-only for C1-NF. **No one splits C1-NF outcomes by synthetic class before the final look**: no Discord line, daily-note row, Console entry or message. The daily check and the watchdog print nothing by class (`c1nf-daily-check.py`, CLASS-BLIND).

## The CAP-PICK exporter for C1-NF (`CAP_PICK_OUT`)

DEC-026 Amendment 1 item B. The executor reads the oracle at the fixed in-unit path `/srv/mal-cap-pick/picks.jsonl` (live config `pick_file`). The file is written by #509's exporter (`scripts/research/cap-pick-oracle.sh`, `tools/cap_pick_oracle.py`), a MiScusi job on fast-0 (`resumable: true`, small memory, POSIX `sh`). #509 is open at this writing; the names below are from its branch and follow it if they change before merge.

- **`CAP_PICK_OUT` is set explicitly**, never left to the script's `$HOME` default: `CAP_PICK_OUT=/home/<jobuser>/data/cap-pick-oracle`, an absolute path. The exporter writes `$CAP_PICK_OUT/picks.jsonl` there. The name must start with `cap-pick` and sit under `/home/<user>/` with no hidden component, or `check-c1nf-unit.py --cap-pick` refuses the bind.
- **Create the directory before the unit starts**, as the job user: `install -d -m 0755 /home/<jobuser>/data/cap-pick-oracle`. The bind is made at unit start; a directory created later is not seen until a restart (the leading `-` makes a missing source a silent no-op, and the executor then refuses every buy in the seal window).
- **Readable by `mal-live`, writable by nobody else.** The directory is 0755 (not group- or world-writable) and `picks.jsonl` must be world-readable (0644): the bind gives read access only through the file modes. Check after the exporter's first write (after 2026-10-16T02:00Z): `stat -c '%a %n' /home/<jobuser>/data/cap-pick-oracle/picks.jsonl` shows `644`.
- **The same file serves both executors' seals and both shadows.** H5's wiring (its own `pick_file` and bind) is H5's runbook, not this one; this section changes nothing of H5's.
- **It reads nothing early.** Per #509, the exporter opens no source and writes nothing until its `CAP_PICK_FINAL_MARKER` exists and the clock passes 2026-10-16T02:00Z, so it may be submitted before then. Between 2026-10-16T01Z and the first heartbeat the C1-NF seal refuses every pick (fail closed).
- **Smoke, counts only** (#509): `python -m tools.cap_pick_oracle check --live /home/<jobuser>/data/cap-pick-oracle/picks.jsonl` reports `fresh: true` and a rising `decided_mints`. Never print a mint.

## Before Helm starts (manager)

Helm's Step 2 (the keypair) comes first: the public address must be in the code before the sha is named.

1. **The wallet pin.** After Helm gives the public address (Step 2), a reviewed PR sets `C1NF_WALLET_PUBKEY` in `tools/c1nf_executor.py` to it. Check it differs from H5's `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk`.
2. **The model pin** (DEC-026 section 11 item 12): a reviewed PR sets `C1NF_MODEL_SHA256` to the pinned model's sha256, the one the shadow writes as `model_sha`.
3. **The CAP-PICK inputs, before 2026-10-16T01Z:** #509 merged; its exporter running as a MiScusi job with an explicit `CAP_PICK_OUT` (see "The CAP-PICK exporter for C1-NF" below), and that directory created before the unit starts; Helm's `20-cap-pick.conf` (Step 5) binding it read-only at `/srv/mal-cap-pick`, where the live config's `pick_file` (`/srv/mal-cap-pick/picks.jsonl`) reads it; and `FINAL_WRITTEN` (see "Stop, halt, status"). Without them the canary pauses at 10-16T01Z; it does not trade blind. Give Helm the absolute `CAP_PICK_OUT` path with the shadow directory (item 6).
4. **The sha**, in a PR comment, after the executor PR and this PR are both merged. It must contain `EXP/EXP-025-c1nf-part1-prereg.md`, and its `c1nf-executor-live.json` must hold the stake, priority, `end_ms` and `state_dir` of DEC-026 section 6. The installer refuses otherwise.
5. **The manifest**, made in the manager's own clone: `scripts/mal-fast/make-c1nf-manifest.sh <40-char-sha> > c1nf-manifest.txt`. Give it to Helm and keep a copy.
6. The C1-NF shadow's output directory (absolute path of the MiScusi job user's `~/data/c1nf-shadow`), with the shadow running at the reviewed #503 head on the pinned model.
7. The Discord webhook for the watchdog (to Helm over the private channel, never into a repo), the funded total (0.5), the wallet's public address and, if the A3 monitor's JSONL is on fast-0, its path.

## Wind-down: before every stop of the live unit

The only exception is an emergency `HALT`.

```
sudo rm -f /etc/mal-c1nf/LIVE_OK                 # no new buys (or: sudo touch /var/lib/mal-live/c1nf/STOP)
S="sudo /usr/local/lib/mal-c1nf-exec/venv/bin/python -I -B -u /usr/local/lib/mal-c1nf-exec/current/launcher.py --config /usr/local/lib/mal-c1nf-exec/current/c1nf-executor-live.json --status"
$S | grep '^\[live\]'                            # repeat until: [live] attempts=... open=0/2 pending=0 ...
```

A C1-NF position exits 300 s after its buy lands (the sell lands 0.55 s later). The sell plan escalates at landing + 315 s and sends its emergency sell at landing + 370 s, so wait up to about 7 minutes. If `open` or `pending` is still above 0, **do not stop the unit**: read the journal and the alerts (a position not closed by landing + 600 s latches `stuck_position`). If the executor cannot sell it, stop the unit and run Sell-and-close (below) for each open mint. The open mints (public addresses) are listed by:

```
sudo dd iflag=nofollow status=none if=/var/lib/mal-live/c1nf/live/state-live.json | python3 -I -c 'import json, sys; print(*json.load(sys.stdin)["open"], sep="\n")'
```

**With `LIVE_OK` removed the live unit does not start again** (`c1nf_executor ALERT startup_refused live_ok_missing`, exit 2, no restart). A stopped unit has no seller. So never stop or restart the live unit while `open` or `pending` is above 0, unless every open mint is sold and booked with Sell-and-close first. The daily check's `c1nf_positions_unmanaged` and the watchdog report a live position with no live unit.

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

Use whatever key tool Helm prefers. The result must be a JSON keypair file the executor loads, as the probe key is. **Check that the address differs from the H5 wallet `5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk`.** Keep the withdraw address with Helm (DEC-019 section 6 item 6). Then wait for the manager's sha: it must carry this address as `C1NF_WALLET_PUBKEY` ("Before Helm starts", item 1).

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

Refusals and what they mean: `the live config differs from DEC-026 sections 5-6 in: ...` (wrong sha or config: stop and tell the manager); `/etc/mal-c1nf/LIVE_OK exists` (remove it first; it is created again only after this install's hash check); `.../live.conf exists` (move it to `/root/disabled/` first, so the install and the dry run are keyless); `/etc/mal-c1nf-key must be a real root:root 0700 directory` or `c1nf-wallet.json must be a regular root:root 0400 file` (fix the modes from Step 2).

**Step 5. Directories and the shadow feed.**

```
install -d -m 0755 -o root -g root /etc/mal-c1nf /srv/mal-c1nf-shadow /srv/mal-cap-pick
install -d -m 0700 -o mal-live -g mal-live /var/lib/mal-live/c1nf
install -d -m 0755 -o root -g root /etc/systemd/system/mal-c1nf-executor.service.d
sed "s#__SHADOW_DIR__#<shadow dir from the manager>#" /usr/local/lib/mal-c1nf-exec/current/mal-c1nf-executor-shadow-feed.conf \
  > /etc/systemd/system/mal-c1nf-executor.service.d/10-shadow-feed.conf
/usr/bin/python3 -I /usr/local/lib/mal-c1nf-exec/current/check-c1nf-unit.py --shadow-feed /etc/systemd/system/mal-c1nf-executor.service.d/10-shadow-feed.conf
sed "s#__CAP_PICK_DIR__#<CAP_PICK_OUT from the manager>#" /usr/local/lib/mal-c1nf-exec/current/mal-c1nf-executor-cap-pick.conf \
  > /etc/systemd/system/mal-c1nf-executor.service.d/20-cap-pick.conf
/usr/bin/python3 -I /usr/local/lib/mal-c1nf-exec/current/check-c1nf-unit.py --cap-pick /etc/systemd/system/mal-c1nf-executor.service.d/20-cap-pick.conf
systemctl daemon-reload
stat -c '%U:%G %a %F %n' /etc/mal-c1nf /srv/mal-c1nf-shadow /srv/mal-cap-pick /var/lib/mal-live/c1nf
#   expect: root:root 755 directory /etc/mal-c1nf | root:root 755 directory /srv/mal-c1nf-shadow | root:root 755 directory /srv/mal-cap-pick
#           | mal-live:mal-live 700 directory /var/lib/mal-live/c1nf
stat -c '%a %F %n' <CAP_PICK_OUT from the manager>
#   expect: 755 directory (it must exist before the unit starts; not group- or world-writable)
```

(The `__SHADOW_DIR__` placeholder is the H5 convention, and `__CAP_PICK_DIR__` follows it. The templates are `mal-c1nf-executor-shadow-feed.conf` and `mal-c1nf-executor-cap-pick.conf` in the pinned tree. Both checks exit 0 with no output. The `/srv/mal-cap-pick` on the host stays an empty root-owned mount point; inside the unit it shows the exporter's directory, read-only.)

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
D="sudo /usr/local/lib/mal-c1nf-exec/venv/bin/python -I -B -u /usr/local/lib/mal-c1nf-exec/current/launcher.py --config /usr/local/lib/mal-c1nf-exec/current/c1nf-executor.json --status"
$D
#   expect the second line to show stop_file=False halt_file=False live_ok=live_ok_missing (/etc/mal-c1nf/LIVE_OK) exp025_part1=True
journalctl -u mal-c1nf-executor --since -1h | grep -c simulate_error   # expect 0
```

Report to the manager: minutes run, restarts, any startup refusal, simulated round trips, simulate errors.

**Step 8. Watchdog.**

```
install -d -m 0700 -o root -g root /etc/mal-c1nf-watch
install -m 0600 -o root -g root /dev/null /etc/mal-c1nf-watch/watch.env
# write four lines, and the optional fifth (an editor as root; never echo the webhook into shell history):
#   C1NF_WATCH_DISCORD_WEBHOOK=<webhook>
#   C1NF_WATCH_FUNDED_SOL=0.5
#   C1NF_WATCH_SHADOW_DIR=<shadow dir from the manager>
#   C1NF_WATCH_WALLET=<the PUBLIC address from Step 2>
#   C1NF_WATCH_A3_FILE=<absolute path of the A3 monitor's JSONL on this host, only if the manager gives one>
install -m 0644 -o root -g root /usr/local/lib/mal-c1nf-exec/current/mal-c1nf-watch.service /usr/local/lib/mal-c1nf-exec/current/mal-c1nf-watch.timer /etc/systemd/system/
systemctl daemon-reload
systemd-run --wait --pipe -p EnvironmentFile=/etc/mal-c1nf-watch/watch.env /usr/bin/python3 -I -S -B -u /usr/local/lib/mal-c1nf-exec/current/c1nf-watch.py --test-message
#   expect: "c1nf_watch: test message posted", and "[C1-NF watch] test message ..." in Discord. Tell the manager to look.
systemctl enable --now mal-c1nf-watch.timer
```

This is C1-NF's own watchdog. Do not edit H5's `watch.env` or timer.

**Step 9. Sudoers for the manager's daily check.** The manager prints the exact lines with `python3 -I scripts/mal-fast/c1nf-daily-check.py --print-sudoers`. Helm installs them as `/etc/sudoers.d/mal-c1nf-check` (root:root 0440) after `visudo -cf`. There is no wildcard. They allow `stat` and `dd iflag=nofollow` of fixed C1-NF state paths only (the `FINAL_WRITTEN` marker and `c1nf-extra.json` among them), never the key.

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
$S | head -2
#   expect: rule=... stake_sol=0.050 max_open=2 max_trades_per_day=30
#           stop_file=False halt_file=False live_ok=valid (/etc/mal-c1nf/LIVE_OK) exp025_part1=True
journalctl -u mal-c1nf-executor --since -5min | grep -c startup_refused     # expect 0 (wallet_unpinned, model_unpinned, wallet_is_h5 ... stop the start)
```

While the wallet-wide `/var/lib/mal-live/STOP` is there the unit still sends nothing, and the daily check says `c1nf_idle` ("the wallet-wide STOP exists"). That is expected until H5's Step 11. It is not Helm's to remove for C1-NF.

## Tier steps (DEC-026 section 10)

T1 is the canary at 0.05 SOL. A step to 0.10 SOL needs profits and the owner's dated line. T2 is inactive until the owner's dated line (a reviewed code change gives it numbers), and it cannot be funded from a 0.5 SOL wallet. Helm edits `/etc/mal-c1nf/TIER` only on the manager's written ask, with the same owner and mode (root:root 0644). The daily check alerts `c1nf_tier_t2` whenever the file says `T2`, so the dated line is checked each day, and `c1nf_tier_unapplied` when the running executor holds another tier than the file applies for more than 15 minutes.

## Stop, halt, status

| Action | Command (manager, `sudo`) | Effect |
| --- | --- | --- |
| Stop new buys | `sudo touch /var/lib/mal-live/c1nf/STOP` | No new buys; open positions exit on the timer |
| Freeze everything | `sudo touch /var/lib/mal-live/c1nf/HALT` | No buys, no sells. Only if the executor or the wallet is wrong |
| Close the gate | `sudo rm /etc/mal-c1nf/LIVE_OK` | Same as `STOP` for buys. Only Helm creates it again. A live restart then refuses to start (Wind-down) |
| Status (local files only) | `$S` from Wind-down | Counters, limits, kill files, the gate, the fill-selection monitor |
| Hard stop | Wind-down first, then `sudo systemctl stop mal-c1nf-executor` | A restart resumes pending signatures without buying again |
| EXP-022 seal marker (item 11) | The manager, on fast-0: `sudo touch /var/lib/mal-live/c1nf/FINAL_WRITTEN`, only after the DEC-016 FINAL is written and **never before 2026-10-16T02:00Z**, in the same step as `/var/lib/mal-live/h5/FINAL_WRITTEN` (H5's method, `h5-executor.md`). See "Item 11: the FINAL markers" below | From 2026-10-16T01Z the inherited seal refuses every buy until it exists. The daily check alerts `c1nf_seal_final_missing` while `LIVE_OK` is present and it is not |
| A3 structure halt (rule 6: `pins_changed`, `program_changed`, ms per slot outside [150, 450]) | `sudo touch /var/lib/mal-live/c1nf/STOP`, and tell the owner | The executor does not read A3. The daily check with `--a3-file` (and the watchdog with `C1NF_WATCH_A3_FILE`) raises `c1nf_a3_halt`; otherwise the manager's daily A3 run is the check |
| Clear a latched halt | `sudo -u mal-live /usr/local/lib/mal-c1nf-exec/venv/bin/python -I -B -u /usr/local/lib/mal-c1nf-exec/current/launcher.py --config /usr/local/lib/mal-c1nf-exec/current/c1nf-executor-live.json --live --clear-halt <NAME>`, with the unit stopped (after the Wind-down) | Never followed by a retune of the rule, model, cap or threshold. Tell the owner first |

**Item 11: the FINAL markers (DEC-026 section 11 item 11; Amendment 1 item C).** Who: the manager, nobody else. When: only after the DEC-016 FINAL is written, and never before 2026-10-16T02:00Z (the oracle's earliest instant). How: one step for both executors, H5's method (`h5-executor.md`, "How the manager creates `STOP`, `HALT` and `FINAL_WRITTEN`"), recorded once with its `date -u` instant in the daily note:

```
date -u    # at or after 2026-10-16T02:00:00Z, and the DEC-016 FINAL is written
sudo touch /var/lib/mal-live/h5/FINAL_WRITTEN /var/lib/mal-live/c1nf/FINAL_WRITTEN
sudo stat -c '%U:%G %a %F %n' /var/lib/mal-live/h5/FINAL_WRITTEN /var/lib/mal-live/c1nf/FINAL_WRITTEN
#   expect two lines: root:root 644 regular empty file <path>
```

A plain file, never a symlink (a dangling link does not count as existing). The executor only tests that it exists. It is never written early "to be ready": before the FINAL it would open the seal window to an oracle that must not be read yet. The exporter's own marker (`CAP_PICK_FINAL_MARKER`, #509) is a separate file the job user can read; it is not this one (`/var/lib/mal-live/c1nf` is `mal-live` 0700).

Latched live halts (the executor at `32265af`): `fill_selection_adverse` (rule 1), `landing_p50_gt_1_9s` and `out_of_rule_entry` (rule 3), `stuck_position` (rule 4: a position not closed by its buy's landing + 600 s; the emergency sell at landing + 370 s is only ledgered, `emergency_deadline_passed`), `model_sha_mismatch` (rule 7). Late sells (rule 5) are an alert, never a halt. Rule 2's twin divergence is not built.

The wallet-wide `/var/lib/mal-live/{STOP,HALT}` also stop C1-NF. They stop H5 too, so the C1-NF files are the ones to use.

## Daily check (manager) and the watchdog

`scripts/mal-fast/c1nf-daily-check.py`. It is read-only and holds no key. It prints `INFO`/`OK`/`ALERT` lines and exits 1 on any alert. The watchdog (`c1nf-watch.py`, every 5 minutes, as root from the pinned tree) runs the same engine and posts new alerts, repeats every 6 h, `RESOLVED`, restarts, `STOP` placed or removed, tier changes, and one wallet line a day to Discord.

```
/usr/bin/python3 -I /home/claude/MAL/scripts/mal-fast/c1nf-daily-check.py --wallet <public address> --funded-sol 0.5 --public-rpc \
  --window-hours 24 --shadow-dir /home/<jobuser>/data/c1nf-shadow [--a3-file <the A3 monitor's JSONL on this host>]
```

What it checks is in the file's header. In short: the unit files against the pinned copies; the credential (only `c1nf-wallet`, never H5's); `LIVE_OK` and `TIER`; the pinned live config against DEC-026 section 6; the seal inputs (`pick_file` from 24 h before 2026-10-16T01Z, `FINAL_WRITTEN` from 10-16T01Z); halts, `HALT` files, stuck, unmanaged or over-cap positions; the effective total stop and how much of it is used; the wallet against funded + realized; the executor's tier against the file; late sells (rule 5, the executor's own window of 20); a stale heartbeat or feed bind; an idle canary; the watchdog's health; budget stops; executor `ALERT` rows; refusals by reason name; the fill-rate alert (rule 8, over buy attempts); missing or invalid pick fields; the CAP-PICK oracle pause from 2026-10-16T01Z; and, with `--a3-file`, rule 6's A3 halts.

**Class-blind.** It never opens a shadow picks or outcomes file, nor the per-pick table in `c1nf-extra.json`. It reads ledger rows and `c1nf-extra.json` only through fixed keys. It never prints a name that names the synthetic class, except the A3 structure alert `synthetic_share_high`. A line that would still name the class is withheld and raises `class_blind_withheld`, and the watchdog refuses to post it. Do not add a by-class line to any report before the final C1-NF look (EXP-025 Amendment 2 item 5). A breach is recorded and the read is reported compromised.

## Sell-and-close an abandoned position (Helm or the owner, root)

**Never run H5's tool (`tools/h5_sell_and_close.py`, or H5's launcher `--run-tool sell_and_close`) against the C1-NF wallet.** Its guard checks only `mal-h5-executor` and `mal-probe-executor`, so it would not refuse while `mal-c1nf-executor` runs, and two processes would move one wallet. Never use the H5 key for a C1-NF mint, or the C1-NF key for an H5 mint.

C1-NF's tool is `tools/c1nf_sell_and_close.py`, run through the C1-NF launcher's `--run-tool sell_and_close` (the only tool that launcher runs). It is H5's sell-and-close code with C1-NF's custody: it loads only `/etc/mal-c1nf-key/c1nf-wallet.json` (there is no `--keyfile`), refuses unless that key is the `C1NF_WALLET_PUBKEY` pinned in the same tree (and never H5's wallet), and refuses while `mal-c1nf-executor` is anything but `inactive` or `failed`. H5's units are not looked at. Like H5's, it sells ALL tokens of one named mint on its canonical PumpSwap pool and closes the token account and the wrapped-SOL account in one transaction, applies the probe's pre-signing allowlist, and **simulates by default**. It prints the signature and the fill, never key material.

Trigger: the `stuck_position` halt, an executor `ALERT sell_abandoned`, or the daily check's `c1nf_stuck_position` or `c1nf_positions_unmanaged`. Do the Wind-down first (remove `LIVE_OK`), stop the unit, and run the tool once for EACH open mint (listed by the one-line command in the Wind-down section). Run it again for a mint only if the first run did not sell it.

```
sudo rm -f /etc/mal-c1nf/LIVE_OK
sudo systemctl stop mal-c1nf-executor
systemctl show -p ActiveState --value mal-c1nf-executor      # expect: inactive or failed (the tool checks this itself and refuses otherwise)
P="sudo /usr/local/lib/mal-c1nf-exec/venv/bin/python -I -B -u /usr/local/lib/mal-c1nf-exec/current/launcher.py --run-tool sell_and_close"
$P --mint <MINT>                              # dry run: wallet (C1-NF), quote, min_out, simulation. Nothing sent.
$P --mint <MINT> --send                       # sells everything at min_out = 0.85 x quote, closes both accounts
```

The flags are H5's (`--slippage-bps N`, `--emergency`, `--priority-lamports N` up to 150,000, `--send`, `--force`); see the table in `h5-executor.md`, "Sell-and-close". `--force` is **Helm only**: it skips the tool's own unit-state check, and is used only after verifying by hand that `mal-c1nf-executor`'s ActiveState is `inactive`. `min_out` is never 0.

After a `--send`, the position is still "open" in the executor's state. Book it with the executor's own offline `--mark-closed` (H5's code, on the C1-NF state dir), with the unit stopped (it takes the lock and refuses while the unit runs). It checks that our wallet signed the transaction, that it sold this mint through PumpSwap and that the token account is closed or empty, then moves the position to closed, books the realized result (which the loss stops read) and writes a `manual_close` ledger row. It runs as `mal-live`, with the RPC key from the root-only env file through systemd, never on a command line:

```
sudo systemd-run --wait --collect --pipe -p User=mal-live -p EnvironmentFile=/etc/mal-probe-rpc/helius.env -p ProtectSystem=strict -p ReadWritePaths=/var/lib/mal-live/c1nf -p UMask=0077 \
  /usr/local/lib/mal-c1nf-exec/venv/bin/python -I -B -u /usr/local/lib/mal-c1nf-exec/current/launcher.py --config /usr/local/lib/mal-c1nf-exec/current/c1nf-executor-live.json --mark-closed <MINT> --sig <SIGNATURE>
#   expect: h5_executor --mark-closed: <MINT> closed by <SIGNATURE>; realized N lamports   (the message is H5's code)
$S | grep '^\[live\]'     # open=0/2 pending=0 once every open mint is booked
```

Until every open mint is booked and the `stuck_position` halt is cleared (`--clear-halt stuck_position`, above, after telling the owner), the unit stays stopped and `LIVE_OK` stays removed. Send the manager the signature, the fill line and the `--mark-closed` output.

## Rollback

Wind-down, `systemctl stop mal-c1nf-executor`, then `ln -sfn <previous sha> /usr/local/lib/mal-c1nf-exec/current` only if that sha was hash-checked when it was installed, then run the installer's hash table again by hand (`sha256sum` of the tree against the manifest you kept). Otherwise reinstall from the manager's named sha. Never edit a file under `/usr/local/lib/mal-c1nf-exec/<sha>/`.

## Never

- `ufw` or firewall, `sshd`, cloudflared or Cloudflare changes (CLAUDE.md).
- A key, a webhook or a withdraw address in a repo file, a PR, a note or a chat line.
- The H5 key for C1-NF, or the C1-NF key for H5. One credential per unit.
- H5's sell-and-close tool against the C1-NF wallet (see Sell-and-close).
- Removing the wallet-wide `STOP` for C1-NF's sake.
- `LIVE_OK` before the manager's written go, or `TIER` = `T2` without the owner's dated line.
- Restarting the live unit with `open` or `pending` above 0 after `LIVE_OK` is removed: it will not start, and nothing sells.
- Running the executor, `--clear-halt` or `--mark-closed` as root (root-owned files in the state dir fail the start precheck). `--status` as root only reads.
- A by-class split of any C1-NF outcome before the final look.
- A retune after a halt.

## Not verified (written without host access)

- The executor's state file names (`h5-counters.json`, `h5-ledger.jsonl`, `c1nf-extra.json` under `/var/lib/mal-live/c1nf/live/`), its credential name (`c1nf-wallet`), its `intents_file` (`/srv/mal-c1nf-shadow`, three hourly streams), its live-config keys, its `--status` lines, its start refusals and its halt names were read from `claude/c1nf-executor-v2` at `32265af`, not from a merged executor or a run. If the merged executor differs, the unit files, the checker, the daily check and this runbook follow it before the install.
- The live-config keys the installer and the daily check test (`stake_lamports`, `buy_priority_lamports`, `end_ms`, `state_dir`, `jito_enabled`, `jito_tip_lamports`, `entry_tolerance_bps`, `feed_heartbeat_max_age_ms`, `max_open`, `max_trades_per_day`, `daily_loss_lamports`, `total_loss_lamports`, `max_pick_age_s`, `wallet_floor_lamports`, and `pick_file` for the seal) are v2's at `32265af`.
- The refusal names the daily check counts are v2's at `32265af`: guard inputs `bad_pick:ref_state_missing`, `bad_pick:ref_state`, `bad_pick:model_sha` (and the `bad_pick:missing_*` / `bad_intent:missing_*` forms), `feed_stale`, `feed_gap`, `stale_pick`; rule 8's attempts are `pick_status` rows that are `filled`, or `unfilled` as `buy_failed` / `buy_expired`. Refusals are counted from `skip` rows only. The CAP-PICK seal is never ledgered per mint: the check reads the count-only `seal_skips`, so it cannot tell "no oracle" from "sealed pick".
- **`pick_file` and its bind (DEC-026 Amendment 1 item B)** were written without a run: the bind `20-cap-pick.conf` was checked by `check-c1nf-unit.py` in tests only, not under systemd; the exporter's file modes (the job user's umask) and the #509 interface were not checked on fast-0, and #509 is not merged. If the bind or the file is missing, from 2026-10-16T01Z every pick is refused (fail closed) and the canary is paused. The daily check checks the drop-in when present; it does not alert when `20-cap-pick.conf` is absent.
- **`stuck_position`** latches at landing + 600 s in v2 at `32265af` (DEC-026 section 7 rule 4), read from the code and its tests, not run. The Wind-down timing (exit at landing + 300 s, emergency sell at + 370 s) follows v2.
- **`c1nf-extra.json` is guarded** in v2 at `32265af`: if it is gone while the counters show the executor ran, live refuses to start (`c1nf_extra_missing`; a dry run alerts `c1nf_extra_reset`). Never delete it; the daily check alerts `c1nf_extra_missing`.
- **Rule 6 (A3) is not wired into the executor or the unit.** The daily check and the watchdog read the A3 monitor's JSONL only when given its path on fast-0; where that file lives on fast-0 (a sync from the monitor's host, or the monitor run there) is the manager's decision. Until then the manager's daily A3 run is the check, and the manager places `STOP`.
- **The rescue tool** (`tools/c1nf_sell_and_close.py`) was tested offline only, with a fake RPC and throwaway keys (its sell path is H5's tested code). It was never run against the chain or as root. It needs the security review of DEC-026 section 11 item 9 with the executor.
- The base unit hides H5's state dir, `/etc/mal-h5`, `/etc/mal-probe` and H5's pinned tree (`InaccessiblePaths=`). That the C1-NF executor needs none of them was read from v2's code (it repoints `LIVE_OK` and `TIER` to `/etc/mal-c1nf`), not run in the sandbox. The keyless dry run under the unit is the check.
- The shadow-feed checker accepts a source directory named `c1nf-shadow*` under `/home/<user>/`. The real #503 output directory name was not checked; if it differs, the checker's pattern follows it.
- The key-tool commands in Step 2 and the sandbox values in Step 6 are proposals; Helm owns the exact install (DEC-026 section 5).
- The pinned model file itself (DEC-026 section 11 item 12) is not installed by this installer; the executor pins only its sha256 (`C1NF_MODEL_SHA256`) and checks the shadow's `model_sha`.
