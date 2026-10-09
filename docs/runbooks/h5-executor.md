# H5 executor runbook (DEC-024)

The executor is `tools/h5_executor.py` (PR #484). Unit: `mal-h5-executor` on `mal-fast-0`, user `mal-live`. It reuses the DEC-019 probe's custody pattern exactly: the key is `/etc/mal-probe/probe-wallet.json` (root:root 0400, in `/etc/mal-probe` root 0700) and reaches the process only through systemd `LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json` in the live drop-in; the code runs from a root-owned pinned copy at a sha with a manifest. Read [probe-executor.md](probe-executor.md) section 2b and [probe-wallet.md](probe-wallet.md) for the pattern; read [DEC-024](../../DEC/DEC-024-h5-live-canary.md) for the limits. **Nothing below has been run on the host.** Every step has a check; if a check does not give the stated result, stop and tell the manager.

## Who does what

| Step | Who |
| --- | --- |
| Pick the sha, make the manifest, set `end_ms` in the reviewed live config, say when the owner has funded | manager |
| Stop the probe, install the pinned tree, hash check, drop-ins, auditd, enable and start | Helm (root) |
| Create `LIVE_OK` (after Helm reports steps 1 to 10 done and the wallet is funded) | manager, `sudo` |
| `STOP`, `HALT`, status, daily check | manager, `sudo` |
| Root sell-and-close of an abandoned position, withdraw | Helm or the owner |

## What is where

| Thing | Path |
| --- | --- |
| Pinned tree (root:root, immutable per sha) | `/usr/local/lib/mal-h5-exec/<sha>/`, `current` -> `<sha>`, venv at `/usr/local/lib/mal-h5-exec/venv` |
| Base unit (keyless dry run), written only by the installer | `/etc/systemd/system/mal-h5-executor.service` |
| Live drop-in (the only thing that hands over the key) | `/etc/systemd/system/mal-h5-executor.service.d/live.conf` |
| Shadow-feed drop-in (host path, Helm writes it) | `/etc/systemd/system/mal-h5-executor.service.d/10-shadow-feed.conf` |
| State dir (mal-live, 0700), the only writable path of the unit | `/var/lib/mal-live/h5/` |
| Live state, counters, ledger | `/var/lib/mal-live/h5/live/{state-live.json,h5-counters.json,h5-ledger.jsonl}` (dry run: `.../dryrun/`) |
| `LIVE_OK`, `STOP`, `HALT` | `/var/lib/mal-live/h5/LIVE_OK`, `STOP`, `HALT` |
| Intents path the executor reads, inside the unit | `/srv/mal-h5-shadow` (config value `intents_file` in both pinned configs) |
| The probe's own files, untouched and read-only to this unit | `/var/lib/mal-live/{state-live.json,probe-fills.jsonl,STOP}` |
| Root-only Helius env, shared with the probe | `/etc/mal-probe-rpc/helius.env` (root:root 0600 in a root:root 0700 dir) |

`intents_file` is a config value, so changing it means a new reviewed sha and a re-pin. The host-specific part is only the bind source in step 7, which is not in the pinned tree.

## Before Helm starts (manager)

1. A merge commit sha on `main` that contains `EXP/EXP-024-h5-boostfloor-part1-prereg.md` (the installer refuses a sha without it, and so does the executor), the live config with an explicit `end_ms`, and this runbook. A config without `end_ms` installs fine and then refuses to go live (`ALERT startup_refused end_ms_missing`); the installer prints a NOTE when it sees that.
2. The manifest, made in the manager's own clone, not Helm's:
   `scripts/mal-fast/make-h5-manifest.sh <40-char-sha> > h5-manifest.txt`. It has one `<sha256>  <repo path>` line per file the installer reads. Give it to Helm and keep a copy.
3. The job user's detector output directory (`~/data/h5-shadow` of the MiScusi job user, as an absolute path), and the detector job running.

## Helm's steps, in order

**Step 1. Stop and disable the old probe unit first, so two processes never share the key.**

```
sudo systemctl stop mal-probe-executor
sudo systemctl disable mal-probe-executor
systemctl is-active mal-probe-executor        # expect: inactive (or failed)
systemctl is-enabled mal-probe-executor       # expect: disabled
pgrep -af 'tools.probe_executor|probe_exec_launcher' || echo none     # expect: none
test -e /var/lib/mal-live/STOP && echo stop-in-place                  # expect: stop-in-place
sudo sha256sum /var/lib/mal-live/state-live.json                      # tell the manager; it must not change from here on
```

Leave `/etc/systemd/system/mal-probe-executor.service.d/live.conf` where it is (it is the probe's record). The H5 base unit has `Conflicts=mal-probe-executor.service`, the installer refuses while the probe unit is active or enabled, and the daily check alerts if any `mal-probe-executor*` unit is active or enabled. Do not `systemctl mask` it: the base unit is a regular file in `/etc/systemd/system` and masking would need `--force`.

**Step 2. Provision, once. Nothing here prints a key.**

```
getent passwd mal-live                                                # uid 999, nologin
sudo stat -c '%U:%G %a %n' /etc/mal-probe /etc/mal-probe/probe-wallet.json /etc/mal-probe-rpc /etc/mal-probe-rpc/helius.env
#   expect: root:root 700 /etc/mal-probe | root:root 400 .../probe-wallet.json | root:root 700 /etc/mal-probe-rpc | root:root 600 .../helius.env
sudo install -d -m 0700 -o mal-live -g mal-live /var/lib/mal-live/h5
sudo install -d -m 0755 -o root -g root /srv/mal-h5-shadow             # the (empty) mount point for the shadow feed
```

Never `cat` or `less` the key file or the env file.

**Step 3. A fresh root-owned clone at the sha.** Never the agent checkout.

```
sudo -i
git clone https://github.com/vaanai/MAL /root/mal-h5-src
git -C /root/mal-h5-src checkout --detach <FULL_SHA>
test "$(git -C /root/mal-h5-src rev-parse HEAD)" = "<FULL_SHA>" && echo sha ok
test -z "$(git -C /root/mal-h5-src status --porcelain --untracked-files=all --ignored)" && echo clean
less /root/mal-h5-src/scripts/mal-fast/install-h5-executor-pinned.sh     # read it before running it
```

Put the manager's `h5-manifest.txt` at `/root/h5-manifest.txt`. As a consistency check only (the manager's list is the authority): `/root/mal-h5-src/scripts/mal-fast/make-h5-manifest.sh <FULL_SHA> | diff - /root/h5-manifest.txt` gives no output.

**Step 4. Install.** The unit must be stopped. The installer refuses a dirty clone, a clone not at the sha, a missing or mismatching manifest entry, a base unit or drop-in that is not identical to the intended text (allowlist check), a missing `/etc/mal-probe-rpc` pair, an active `mal-h5-executor`, and an active or enabled `mal-probe-executor`. It rolls back on any failure after the point of no return.

```
/root/mal-h5-src/scripts/mal-fast/install-h5-executor-pinned.sh <FULL_SHA> /root/h5-manifest.txt | tee /root/h5-install.log
```

**Step 5. Hash check.** The installer re-hashes every installed file against the manifest and prints the table in manifest format. Compare it with the manager's list by eye or by diff:

```
sed -n '/^BEGIN-MANIFEST/,/^END-MANIFEST/p' /root/h5-install.log | sed '1d;$d' | sort -k2 > /root/h5-installed.txt
sort -k2 /root/h5-manifest.txt | diff - /root/h5-installed.txt && echo hashes match
sha256sum /etc/systemd/system/mal-h5-executor.service        # equals the manifest line for scripts/mal-fast/mal-h5-executor.service
readlink /usr/local/lib/mal-h5-exec/current                  # <FULL_SHA>
python3 -I /usr/local/lib/mal-h5-exec/current/check-h5-unit.py --base /etc/systemd/system/mal-h5-executor.service && echo base unit ok
```

Send the manager the printed table and the two `sha256sum` results. Stop here if anything differs.

**Step 6. Check the key and env facts the unit relies on (stat only).** Repeat the `stat` line of step 2. The base unit's root `ExecStartPre` fails the start if `/etc/mal-probe-rpc` is not `0:0:700` or `helius.env` is not `0:0:600`.

**Step 7. Point the unit at the shadow detector's output, read-only.** The detector (`tools/h5_shadow.py`) is a MiScusi job that writes hourly `h5-shadow-<UTC hour>.jsonl` files into a directory under the job user's home. The unit cannot see `/home` (`ProtectHome=tmpfs`), so exactly that directory is bound read-only onto `/srv/mal-h5-shadow`. `mal-live` gets no write access: the bind is read-only and the directory must not be group or world writable.

```
SHADOW=/home/<jobuser>/data/h5-shadow                         # the absolute path the manager gave you
stat -c '%U:%G %a %n' "$SHADOW"                               # expect: not group/world-writable, readable by others (755 is fine)
ls -l "$SHADOW" | head -3                                     # files are readable by others (644)
sed "s#__SHADOW_DIR__#$SHADOW#" /usr/local/lib/mal-h5-exec/current/mal-h5-executor-shadow-feed.conf \
  | sudo tee /etc/systemd/system/mal-h5-executor.service.d/10-shadow-feed.conf >/dev/null
python3 -I /usr/local/lib/mal-h5-exec/current/check-h5-unit.py --shadow-feed /etc/systemd/system/mal-h5-executor.service.d/10-shadow-feed.conf && echo feed ok
sudo systemctl daemon-reload
# prove it as mal-live: it reads, and it cannot write
sudo systemd-run --wait --collect --pipe -p User=mal-live -p ProtectHome=tmpfs -p "BindReadOnlyPaths=$SHADOW:/srv/mal-h5-shadow" /bin/ls -l /srv/mal-h5-shadow
sudo systemd-run --wait --collect --pipe -p User=mal-live -p ProtectHome=tmpfs -p "BindReadOnlyPaths=$SHADOW:/srv/mal-h5-shadow" /bin/touch /srv/mal-h5-shadow/x
#   expect: the first lists the hourly files; the second fails with "Read-only file system"
```

The checker accepts only `/home/<user>/.../h5-shadow*` with no hidden path component. If the detector job is restarted into a new directory (new inode), restart `mal-h5-executor` too, because the bind pins the old one. A missing source is a silent no-op at the unit level (leading `-`); the executor then reports `signals_absent` and buys nothing.

**Step 8. Keyless dry run, at least 10 minutes. No key is involved.** This proves the pinned path, the sandbox, the state dir, the shadow bind and the RPC env on the real host before anything live.

```
sudo systemctl start mal-h5-executor
journalctl -u mal-h5-executor -n 30 --no-pager          # expect: h5_executor mode=dryrun ...; no traceback; no startup_refused
sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor.json --status
sudo ls -l /var/lib/mal-live/h5/dryrun/                 # state, counters, ledger exist, owned by mal-live
sudo systemctl stop mal-h5-executor
```

If the ledger shows `signals_absent` the whole time, the bind is wrong; fix step 7 before going on. Running `--status` as root only reads; never run the executor itself, or `--clear-halt`, as root (see Never).

**Step 9. Install the live drop-in from the verified copy, and do not start.**

```
sudo install -D -m 0644 /usr/local/lib/mal-h5-exec/current/mal-h5-executor-live-pinned.conf /etc/systemd/system/mal-h5-executor.service.d/live.conf
python3 -I /usr/local/lib/mal-h5-exec/current/check-h5-unit.py --dropin /etc/systemd/system/mal-h5-executor.service.d/live.conf && echo dropin ok
sudo systemctl daemon-reload
systemctl cat mal-h5-executor | grep -E '^(LoadCredential|ExecStart)='
#   expect: LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json, ExecStart= (empty), ExecStart=/usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u .../launcher.py --config .../h5-executor-live.json --live
sudo systemctl show mal-h5-executor -p ExecStart --value | grep -c fast-forward      # expect 0
sudo systemctl enable mal-h5-executor                                                  # WantedBy=multi-user.target; with no LIVE_OK it refuses at start (exit 2) and is not restarted
```

**Step 10. Auditd** (see below). Then tell the manager "steps 1 to 10 done" with the step 5 table.

**Step 11. Start, once the manager has created `LIVE_OK` and the owner has funded the wallet** (see "Going live").

```
sudo systemctl start mal-h5-executor
journalctl -u mal-h5-executor -n 20 --no-pager          # expect: h5_executor mode=live user=<public key>   (5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk)
```

If it prints `ALERT startup_refused <why>` it exits 2 and stays stopped: `live_ok_missing`, `exp024_part1_missing`, `end_ms_missing`, `rpc_env ...` or `rpc_key_missing`. Fix the cause (the first three are the manager's), then `systemctl start` again.

## Going live (manager)

Preconditions are DEC-024 section 8 and the PR #484 list; this runbook adds the mechanics. Only after Helm reports steps 1 to 10, the owner has funded the wallet, and the dry run of step 8 was clean:

```
python3 -I scripts/mal-fast/h5-daily-check.py --funded-sol 0.25 --write-baseline   # once: records the probe state's sha256; wallet vs funded must be OK
sudo touch /var/lib/mal-live/h5/LIVE_OK                                              # the executor sends only while this file exists
```

Then ask Helm for step 11. Removing `LIVE_OK` stops new buys like `STOP`. The executor checks it immediately before every live buy, not only at start.

## Stop, halt, status

| Action | Command (manager, `sudo`) | Effect |
| --- | --- | --- |
| Stop new buys | `sudo touch /var/lib/mal-live/h5/STOP` | No new buys; open positions still exit on the timer. Remove with `sudo rm /var/lib/mal-live/h5/STOP`. |
| Freeze everything | `sudo touch /var/lib/mal-live/h5/HALT` | No buys, no sells, no rebroadcasts. Open positions stay open. Only if the executor or the wallet is wrong. |
| Stop new buys by removing the gate | `sudo rm /var/lib/mal-live/h5/LIVE_OK` | Same as `STOP` for buys. |
| Status (local files only, no key, no RPC) | `sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --status` | Kill files, `live_ok`, attempts, realized, open, pending, latched halts. |
| Hard stop | `sudo systemctl stop mal-h5-executor` | A restart resumes pending signatures without re-buying. Stop with 0 open positions when you can. |
| Daily check | `python3 -I scripts/mal-fast/h5-daily-check.py --funded-sol <total deposited>` | See below. |

The files are created by root with `touch`; the executor only tests that they exist. The state dir is `mal-live` 0700, so the manager needs `sudo` for all of it. A latched live-halt (BOOST end early, late sells, slow landing, stuck position) is cleared only by an offline, ledgered step, as `mal-live`, with the unit stopped:

```
sudo -u mal-live /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --live --clear-halt <NAME>
```

A halt is never followed by a retune. Do not clear one without telling the owner.

## Daily check (manager, 12:17Z cron)

`scripts/mal-fast/h5-daily-check.py` replaces the decommissioned-probe check (job #385's command). Read-only, no key, prints `INFO`/`OK`/`ALERT` lines and exits 1 on any alert.

- **Allowed now:** the `mal-h5-executor` unit (active or enabled), `/var/lib/mal-live/h5`, the pinned tree. The wallet is no longer expected to be 0.
- **Still alerts:** any `mal-probe-executor*` unit active or enabled; `/var/lib/mal-live/state-live.json` changed (sha256 against the baseline from `--write-baseline`, plus attempts <= 62, realized -0.210755 SOL, nothing open); `/var/lib/mal-live/STOP` missing.
- **New:** installed unit files differ from the pinned copies, an unexpected drop-in, the unit `failed`, a `HALT` file, a latched live halt, a stuck or abandoned position, the H5 state dir not `mal-live:mal-live` 0700, the ledger naming another wallet, and **wallet balance vs funded + H5 realized - cost of open positions** outside a tolerance of 0.005 SOL plus 0.0021 SOL per open or pending position. Pass the total deposited, net of withdrawals, as `--funded-sol`; a top-up or withdrawal shows as a gap until you do. "H5 realized" is `realized_lamports` in the live state file, the number the loss stops use; the check does not sum the ledger.
- It reads `/var/lib/mal-live` with `sudo -n /usr/bin/cat|stat|test` on fixed paths. The RPC key is read from `HELIUS_API_KEY` or the paper env file for `getBalance` only and is never printed.

## Sell-and-close an abandoned position (Helm or the owner, root)

Trigger: `ALERT sell_abandoned` or `stuck_position` (a position not sold by s0 + 400 s, after the executor's own emergency sell), or the daily check's `h5_stuck_position`. The tool sells ALL tokens of one named mint on its canonical PumpSwap pool, then closes the token account and the wrapped-SOL account for the rent, in one transaction. It runs from the pinned tree, reads the key file directly like `probe-withdraw.sh` (root:root 0400 in a 0700 dir), applies the probe's pre-signing allowlist, and **simulates by default**. It prints the signature and the fill, never key material.

```
sudo systemctl stop mal-h5-executor           # the tool refuses while mal-h5-executor or mal-probe-executor is active (--force overrides; do not)
P="sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --run-tool sell_and_close"
$P --mint <MINT>                              # dry run: quote, min_out, simulation. Nothing sent.
$P --mint <MINT> --send                       # sells everything at min_out = 0.85 x quote, closes both accounts
```

| Flag | Meaning |
| --- | --- |
| (none) | `min_out` = 0.85 x the V-priced quote (the executor's first sell level). Refused if there is no quote. |
| `--slippage-bps N` | Lowers the guard: `min_out` = quote x (1 - N/10000), N 0 to 9500. |
| `--emergency` | `min_out` = 1 lamport: accept any price. Exclusive with `--slippage-bps`. Only if the position is otherwise lost. |
| `--priority-lamports N` | 1 to 150000 (default 150000). |
| `--send` | Send. Without it nothing is sent. |

`min_out` is never 0. A tiny quote that would give 0 is refused (use `--emergency`). With a zero token balance the tool only closes the accounts. After a `--send`, the position is still "open" in the executor's state: the unit stays stopped until the manager has reconciled it from the printed signature and cleared the `stuck_position` halt (`--clear-halt stuck_position` above). The executor will otherwise report `zero_token_balance` for it. Send the manager the signature and the fill line.

## Rollback

| To | Do |
| --- | --- |
| Stop trading, keep the unit | `sudo touch /var/lib/mal-live/h5/STOP`; positions exit on the timer. |
| Back to the keyless dry run | `sudo systemctl stop mal-h5-executor && sudo rm /etc/systemd/system/mal-h5-executor.service.d/live.conf && sudo systemctl daemon-reload && sudo systemctl start mal-h5-executor`. Without the drop-in nothing hands the process the key. |
| An earlier pinned sha | Stop the unit, then `sudo ln -s <old-sha> /usr/local/lib/mal-h5-exec/.current.tmp && sudo mv -T /usr/local/lib/mal-h5-exec/.current.tmp /usr/local/lib/mal-h5-exec/current`, start. The venv is shared; if the requirements changed between the two shas, remove that sha's dir and rerun the installer for it. |
| A failed install | The installer rolls back by itself; if it prints "rollback FAILED", run the lines it prints, as root, then rerun. |
| Off entirely | Stop with 0 open and 0 pending (`--status`), `sudo systemctl disable --now mal-h5-executor`, remove `live.conf`. To withdraw, follow [probe-wallet.md](probe-wallet.md) section 3 (`probe-withdraw.sh --to <OWNER_DEST>`); that tool checks the probe's state only, so confirm H5 has no open position and no stranded token first (it refuses non-zero tokens). |

Never bring the old `mal-probe-executor` back while this unit is installed. If the probe has to run again, stop and disable `mal-h5-executor` first.

## Auditd (Helm)

Add `/etc/audit/rules.d/mal-h5.rules`, then `sudo augenrules --load` and `sudo auditctl -l | grep malh5`:

```
-w /usr/local/lib/mal-h5-exec -p wa -k malh5-code
-w /etc/systemd/system/mal-h5-executor.service -p wa -k malh5-unit
-w /etc/systemd/system/mal-h5-executor.service.d -p wa -k malh5-unit
-w /var/lib/mal-live/h5 -p wa -k malh5-state
-w /etc/mal-probe-rpc -p rwa -k malprobe-rpc
```

The key directory `/etc/mal-probe` already has Helm's watch from DEC-019; check with `sudo auditctl -l | grep -i mal-probe` and add `-w /etc/mal-probe -p rwa -k malprobe-key` only if it is missing (do not duplicate it). Expect a read event on the key file at each unit start: that is systemd's `LoadCredential`. Any other reader is a finding. The `malh5-state` rule logs every write by the executor too; keep it for the first days, then drop it if the volume is too high.

## Never

- Never print, copy, paste or commit `/etc/mal-probe/probe-wallet.json`, `/etc/mal-probe-rpc/helius.env`, or their contents.
- Never run `tools.h5_executor`, `--clear-halt` or anything else that writes the state dir as root: root-owned files in `/var/lib/mal-live/h5` make the start precheck fail closed. `--status` as root only reads.
- Never run the executor or the tool from a working tree or from `/var/lib/mal/fast-forward/src`.
- Never install the live drop-in from a working tree; install it from `/usr/local/lib/mal-h5-exec/current/`.
- Never raise a limit in config (config can only lower the code maxima), and never fund the wallet beyond what the owner decided.
- Canary and live fills are never a book and never count toward any gate (DEC-024).

## Not verified (written without host access)

The installer on the real host (venv build, `systemctl`, paths), systemd's reading of both units on this host's version, the `ProtectHome=tmpfs` plus `BindReadOnlyPaths` combination, the `Conflicts=` behaviour with the probe unit, the root `ExecStartPre`, the auditd rule syntax, the daily check's `systemctl` parsing, and the sell-and-close tool against the real chain. The tests use fakes for all of these.
