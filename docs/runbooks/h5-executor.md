# H5 executor runbook (DEC-024)

The executor is `tools/h5_executor.py` (PR #484). Unit: `mal-h5-executor` on `mal-fast-0`, user `mal-live`. It reuses the DEC-019 probe's custody pattern exactly: the key is `/etc/mal-probe/probe-wallet.json` (root:root 0400, in `/etc/mal-probe` root 0700) and reaches the process only through systemd `LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json` in the live drop-in; the code runs from a root-owned pinned copy at a sha with a manifest. Read [probe-executor.md](probe-executor.md) section 2b and [probe-wallet.md](probe-wallet.md) for the pattern; read [DEC-024](../../DEC/DEC-024-h5-live-canary.md) for the limits. **Nothing below has been run on the host.** Every step has a check; if a check does not give the stated result, stop and tell the manager.

**Which sha: only the one the manager names in a comment on PR #499**, taken after the executor branch (`claude/h5-executor`) has merged `origin/main` (which brings EXP-024 into the tree) and its review-fix batch, and after this PR is merged into it. Do not install the head of this PR or of the executor branch as it stands, and do not take a sha from any other message. The CLI flags this runbook uses (`--config`, `--live`, `--status`, `--clear-halt`, `--run-tool sell_and_close`) are checked against that sha; the executor's dry-run `--rpc`/`--rpc-env` option is not used here (the unit takes the key from the environment).

## Who does what

| Step | Who |
| --- | --- |
| Name the sha (PR comment), make the manifest, set `end_ms` in the reviewed live config, say when the owner has funded and give the go | manager |
| Stop the probe, pre-flight, install the pinned tree, hash check, drop-ins, auditd, enable and start | Helm (root) |
| **Create `/etc/mal-h5/LIVE_OK`** (root:root 0644), only after the hash check of the sha that will run and the manager's go | Helm (root) |
| Remove `LIVE_OK` (stops new buys at once) | Helm or the manager (`sudo rm`) |
| `STOP`, `HALT`, `FINAL_WRITTEN`, status, daily check | manager (`sudo` on fast-0) |
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
| `LIVE_OK` (the gate the executor cannot create) | `/etc/mal-h5/LIVE_OK`: root-owned regular file, not group/other writable, no symlink. Parent `/etc/mal-h5` is `root:root` 0755. Readable by the unit, not writable (`ProtectSystem=strict`, not in `ReadWritePaths`). |
| `STOP`, `HALT`, `FINAL_WRITTEN` | `/var/lib/mal-live/h5/STOP`, `HALT`, `FINAL_WRITTEN` (the executor only tests that they exist) |
| Intents path the executor reads, inside the unit | `/srv/mal-h5-shadow` (config value `intents_file` in both pinned configs) |
| The probe's own files, untouched and read-only to this unit | `/var/lib/mal-live/{state-live.json,probe-fills.jsonl,STOP}` |
| Root-only Helius env, shared with the probe | `/etc/mal-probe-rpc/helius.env` (root:root 0600 in a root:root 0700 dir) |

`intents_file` is a config value, so changing it means a new reviewed sha and a re-pin. The host-specific part is only the bind source in step 7, which is not in the pinned tree.

## Before Helm starts (manager)

1. The sha, named in a comment on PR #499 once the executor branch has merged `origin/main` and its review fixes and this PR is merged into it. It must contain `EXP/EXP-024-h5-boostfloor-part1-prereg.md` (the installer refuses a sha without it, and so does the executor), the live config with an explicit `end_ms`, and this runbook. A config without `end_ms` installs fine and then refuses to go live (`ALERT startup_refused end_ms_missing`); the installer prints a NOTE when it sees that.
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

**Step 1b. On a reinstall only: close the gate first.** The installer refuses while `/etc/mal-h5/LIVE_OK` exists, so each install starts with the gate closed and `LIVE_OK` is created again only after that install's hash check. Removing it stops new buys of a running executor at once; open positions still exit on the timer.

```
sudo rm -f /etc/mal-h5/LIVE_OK
sudo touch /var/lib/mal-live/h5/STOP             # optional belt and braces while it winds down; remove it again when you restart
```

**Step 2. Provision, once. Nothing here prints a key.**

```
getent passwd mal-live                                                # uid 999, nologin
sudo stat -c '%U:%G %a %n' /etc/mal-probe /etc/mal-probe/probe-wallet.json /etc/mal-probe-rpc /etc/mal-probe-rpc/helius.env
#   expect: root:root 700 /etc/mal-probe | root:root 400 .../probe-wallet.json | root:root 700 /etc/mal-probe-rpc | root:root 600 .../helius.env
sudo install -d -m 0700 -o mal-live -g mal-live /var/lib/mal-live/h5
sudo install -d -m 0755 -o root -g root /srv/mal-h5-shadow             # the (empty) mount point for the shadow feed
sudo install -d -m 0755 -o root -g root /etc/mal-h5                    # holds LIVE_OK; root-owned so the executor cannot create it (the installer also does this)
stat -c '%U:%G %a %F %n' /etc/mal-h5 /srv/mal-h5-shadow /var/lib/mal-live/h5
#   expect: root:root 755 directory /etc/mal-h5 | root:root 755 directory /srv/mal-h5-shadow | mal-live:mal-live 700 directory /var/lib/mal-live/h5
test ! -e /etc/mal-h5/LIVE_OK && echo "gate closed"                    # expect: gate closed. Do NOT create LIVE_OK yet.
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

**Step 3b. Pre-flight on fast-0's systemd, before anything is installed.** The design relies on `ProtectHome=tmpfs` leaving one read-only bind of a home subdirectory visible while the rest of `/home` is empty, together with `ProtectSystem=strict`, `TemporaryFileSystem=/var/lib/mal:ro` and a single `ReadWritePaths`. That is documented systemd behaviour (`ProtectHome=tmpfs` since v238), but it has not been run on this host. Run these three checks in order and stop at the first output that differs; do not install on a guess. They need Steps 2 and 3 done and the detector job running. They touch no key, no env file and none of the probe's files. The only writes are `.preflight` files that the checks name: three are expected to be refused, and the one that must succeed removes itself.

*3b-1. systemd version.*

```
systemctl --version | head -1       # expect: systemd NNN (...) with NNN >= 250. Put the number in your report.
```

`ProtectHome=tmpfs` needs 238; `LoadCredential=` and `ProtectProc=` need 247 (the probe unit already uses both on this host).

*3b-2. `systemd-analyze verify` of the base unit from the clone (directive support).* It loads the unit from the path and reports unknown keys, bad values and missing executables:

```
sudo systemd-analyze verify /root/mal-h5-src/scripts/mal-fast/mal-h5-executor.service; echo "rc=$?"
```

Expect exactly one line, because the pinned tree is not installed yet (wording and the `rc` value can vary by version):

```
Command /usr/local/lib/mal-h5-exec/venv/bin/python is not executable: No such file or directory
```

Stop if any line says `Unknown key`, `Unknown assignment`, `Invalid`, `Failed to parse` or `not supported`, or names `ProtectHome`, `TemporaryFileSystem`, `RestartPreventExitStatus`, `LimitCORE` or `Conflicts`.

*3b-3. The sandbox combination, in one transient unit with every mount-related property of the real one.* It runs as `mal-live` against the real shadow directory (read-only operations only; the one write attempt must be refused):

```
SHADOW=/home/<jobuser>/data/h5-shadow          # the absolute path the manager gave you: the detector job's output directory
P=(-q --wait --collect --pipe -p User=mal-live -p ProtectSystem=strict -p ProtectHome=tmpfs -p TemporaryFileSystem=/var/lib/mal:ro
   -p ReadWritePaths=/var/lib/mal-live/h5 -p "BindReadOnlyPaths=-$SHADOW:/srv/mal-h5-shadow" -p PrivateTmp=true -p ProtectProc=invisible)
sudo systemd-run "${P[@]}" /bin/sh -c '
echo "home:"; ls -A /home
echo "shadow:"; ls /srv/mal-h5-shadow | tail -3
f=$(ls /srv/mal-h5-shadow | tail -1)
test -n "$f" && test -r "/srv/mal-h5-shadow/$f" && echo "shadow-file-read: ok" || echo "shadow-file-read: FAILED"
(: > /srv/mal-h5-shadow/.preflight) 2>/dev/null && echo "shadow-write: SUCCEEDED (BAD)" || echo "shadow-write: refused"
(: > /var/lib/mal-live/h5/.preflight) 2>/dev/null && rm -f /var/lib/mal-live/h5/.preflight && echo "h5-dir-write: ok" || echo "h5-dir-write: FAILED"
(: > /var/lib/mal-live/.preflight) 2>/dev/null && echo "probe-dir-write: SUCCEEDED (BAD)" || echo "probe-dir-write: refused"
(: > /etc/mal-h5/.preflight) 2>/dev/null && echo "etc-mal-h5-write: SUCCEEDED (BAD)" || echo "etc-mal-h5-write: refused"
test -r /etc/mal-h5 && ls -A /etc/mal-h5 >/dev/null && echo "etc-mal-h5-read: ok" || echo "etc-mal-h5-read: FAILED"
echo "var-lib-mal:"; ls -A /var/lib/mal
'; echo "rc=$?"
```

Expected output, line for line (the two file names are up to three of the newest hourly files; the dates are examples):

```
home:
shadow:
h5-shadow-2026-10-09T13.jsonl
h5-shadow-2026-10-09T14.jsonl
shadow-file-read: ok
shadow-write: refused
h5-dir-write: ok
probe-dir-write: refused
etc-mal-h5-write: refused
etc-mal-h5-read: ok
var-lib-mal:
rc=0
```

| If the output shows | It means | Do |
| --- | --- | --- |
| names under `home:` | `/home` is not an empty tmpfs: `ProtectHome=tmpfs` was not applied | stop, report the systemd version |
| nothing or an error under `shadow:` | the bind is not visible: wrong or missing `$SHADOW` (`ls -ld "$SHADOW"`), or the `ProtectHome=tmpfs` plus bind combination does not work on this systemd | stop, report. The fallback is a detector output directory outside `/home` and a checker change, which is a new sha; the manager decides |
| `shadow-file-read: FAILED` | the detector's files are not readable by others (mode 600) | the manager fixes the detector's umask or modes |
| `shadow-write: SUCCEEDED (BAD)` | the bind is not read-only | `rm "$SHADOW/.preflight"` (as the job user), stop |
| `h5-dir-write: FAILED` | step 2 not done, or `/var/lib/mal-live/h5` not `mal-live` 0700, or `ReadWritePaths` not applied | fix step 2, rerun |
| `probe-dir-write: SUCCEEDED (BAD)` | the sandbox leaves the probe's directory writable | `sudo rm /var/lib/mal-live/.preflight`, stop |
| `etc-mal-h5-write: SUCCEEDED (BAD)` | the sandbox lets the process write where `LIVE_OK` lives | `sudo rm /etc/mal-h5/.preflight`, stop |
| `etc-mal-h5-read: FAILED` | the unit cannot read `/etc/mal-h5`, so it could not see `LIVE_OK` | check `stat /etc/mal-h5` (root:root 0755), stop |
| names under `var-lib-mal:` | `TemporaryFileSystem=/var/lib/mal:ro` was not applied | stop |

The real unit is checked the same way once more after it runs (Step 8, `nsenter`), and `systemd-analyze verify` runs again on the installed unit (Steps 7 and 9).

**Step 4. Install.** The unit must be stopped. The installer refuses a dirty clone, a clone not at the sha, a missing or mismatching manifest entry, a base unit or drop-in that is not identical to the intended text (allowlist check), a missing `/etc/mal-probe-rpc` pair, an active `mal-h5-executor`, an active or enabled `mal-probe-executor`, a `/etc/mal-h5` that is not a real `root:root` 0755 directory, and an existing `/etc/mal-h5/LIVE_OK`. It provisions `/etc/mal-h5` (root 0755) and never creates `LIVE_OK`. It rolls back on any failure after the point of no return.

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
test ! -e /etc/mal-h5/LIVE_OK && echo "gate still closed"      # expect: gate still closed
```

Send the manager the printed table and the two `sha256sum` results. Stop here if anything differs. `LIVE_OK` is created only after this step passes (see "Going live").

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
sudo systemd-analyze verify /etc/systemd/system/mal-h5-executor.service; echo "rc=$?"          # expect: no output, rc=0 (the pinned tree is installed now)
systemctl show mal-h5-executor -p ProtectHome -p ProtectSystem -p TemporaryFileSystem -p BindReadOnlyPaths -p ReadWritePaths -p RestartPreventExitStatus --no-pager
#   expect (any order): ProtectHome=tmpfs | ProtectSystem=strict | TemporaryFileSystem=/var/lib/mal:ro | ReadWritePaths=/var/lib/mal-live/h5 | RestartPreventExitStatus=2
#   | a BindReadOnlyPaths line naming $SHADOW as the source and /srv/mal-h5-shadow as the destination
```

The read and the refused write were already proved as `mal-live` in step 3b-3, with the same `$SHADOW`; use the same path here.

The checker accepts only `/home/<user>/.../h5-shadow*` with no hidden path component. If the detector job is restarted into a new directory (new inode), restart `mal-h5-executor` too, because the bind pins the old one. A missing source is a silent no-op at the unit level (leading `-`); the executor then reports `signals_absent` and buys nothing.

**Step 8. Keyless dry run, at least 10 minutes. No key is involved.** This proves the pinned path, the sandbox, the state dir, the shadow bind and the RPC env on the real host before anything live.

```
sudo systemctl start mal-h5-executor
journalctl -u mal-h5-executor -n 30 --no-pager          # expect: h5_executor mode=dryrun ...; no traceback; no startup_refused
sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor.json --status
sudo ls -l /var/lib/mal-live/h5/dryrun/                 # state, counters, ledger exist, owned by mal-live
PID=$(systemctl show -p MainPID --value mal-h5-executor)   # the running unit's own mount namespace, while it is up
sudo nsenter -t "$PID" -m /bin/ls -A /home               # expect: nothing
sudo nsenter -t "$PID" -m /bin/ls /srv/mal-h5-shadow | tail -3     # expect: the newest hourly files
sudo awk '$5 == "/home" || $5 == "/srv/mal-h5-shadow" {print $5, $6}' /proc/$PID/mountinfo
#   expect two lines, each with ro as the first mount option: "/home ro,..." and "/srv/mal-h5-shadow ro,..."
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
sudo systemd-analyze verify /etc/systemd/system/mal-h5-executor.service; echo "rc=$?"   # expect: no output, rc=0 (both drop-ins are merged in)
sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --status
#   expect: live_ok=False (the gate is closed) and exp024_part1=True. If exp024_part1 is False this sha has no EXP-024: stop, it is the wrong sha.
sudo systemctl enable mal-h5-executor                                                  # WantedBy=multi-user.target; with no LIVE_OK it refuses at start (exit 2) and is not restarted
```

**Step 10. Auditd** (see below). Then tell the manager "steps 1 to 10 done" with the step 5 table.

**Step 11. Create `LIVE_OK`, then start, once the manager has said go and the owner has funded the wallet** (see "Going live"). `LIVE_OK` is created by Helm, as root, only after the Step 5 hash check of the sha that will run.

```
sudo install -m 0644 -o root -g root /dev/null /etc/mal-h5/LIVE_OK
stat -c '%U:%G %a %F %n' /etc/mal-h5/LIVE_OK                 # expect: root:root 644 regular empty file /etc/mal-h5/LIVE_OK
sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --status
#   expect: live_ok=True. If it still says False the executor at this sha is not reading /etc/mal-h5/LIVE_OK: remove the file, stop, tell the manager.
sudo systemctl start mal-h5-executor
journalctl -u mal-h5-executor -n 20 --no-pager          # expect: h5_executor mode=live user=<public key>   (5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk)
```

If it prints `ALERT startup_refused <why>` it exits 2 and stays stopped: `live_ok_missing`, `exp024_part1_missing`, `end_ms_missing`, `rpc_env ...` or `rpc_key_missing`. Fix the cause (the first three are the manager's), then `systemctl start` again.

## Going live (manager, then Helm)

Preconditions are DEC-024 section 8 and the PR #484 list; this runbook adds the mechanics. The gate is `/etc/mal-h5/LIVE_OK`, in a root-owned directory the executor cannot write (DEC-024 section 3). The executor accepts it only as a regular file, root-owned, not group or other writable, not a symlink, in a `root:root` 0755 parent. The manager does not create it; Helm does, only after the hash check.

1. Manager, after Helm reports steps 1 to 10 (the step 5 table matches), the owner has funded the wallet, and the dry run of step 8 was clean:

```
python3 -I scripts/mal-fast/h5-daily-check.py --funded-sol 0.25 --write-baseline   # once: records the probe state's sha256. Expect no ALERT; LIVE_OK=no is right at this point.
```

2. Manager tells Helm "go".
3. Helm runs step 11: creates `LIVE_OK` (root 0644), checks `--status` shows `live_ok=True`, starts the unit.

Removing `LIVE_OK` stops new buys at once, like `STOP`; open positions still exit on the timer. The executor checks the file immediately before every live buy, not only at start. Helm or the manager may remove it (`sudo rm /etc/mal-h5/LIVE_OK`); only Helm creates it, and the installer refuses to run while it exists, so every install begins with the gate closed.

## Stop, halt, status

| Action | Command (manager, `sudo`) | Effect |
| --- | --- | --- |
| Stop new buys | `sudo touch /var/lib/mal-live/h5/STOP` | No new buys; open positions still exit on the timer. Remove with `sudo rm /var/lib/mal-live/h5/STOP`. |
| Freeze everything | `sudo touch /var/lib/mal-live/h5/HALT` | No buys, no sells, no rebroadcasts. Open positions stay open. Only if the executor or the wallet is wrong. |
| Stop new buys by closing the gate | `sudo rm /etc/mal-h5/LIVE_OK` (Helm or the manager) | Same as `STOP` for buys. Only Helm creates it again. |
| EXP-022 seal marker | `sudo touch /var/lib/mal-live/h5/FINAL_WRITTEN`, only after the DEC-016 FINAL is written | Same directory and same method as `STOP`; the seal rules around it are in #484. |
| Status (local files only, no key, no RPC) | `sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --status` | Kill files, `live_ok`, attempts, realized, open, pending, latched halts. |
| Hard stop | `sudo systemctl stop mal-h5-executor` | A restart resumes pending signatures without re-buying. Stop with 0 open positions when you can. |
| Daily check | `python3 -I scripts/mal-fast/h5-daily-check.py --funded-sol <total deposited>` | See below. |

**How the manager creates `STOP`, `HALT` and `FINAL_WRITTEN`:** `sudo touch <file>` on fast-0. The state dir is `mal-live` 0700, so the manager needs `sudo`. The executor only tests that these files exist (`probe_executor.check_stop_file` and `check_halt_file` are `Path.exists()` at the current head); it asks for no owner and no mode, so the `root:root` 0644 file that `sudo touch` makes is accepted. Check after touching: `--status` prints `stop_file=True` (or `halt_file=True`). A dangling symlink does not count as existing, so create a plain file, not a link. If a later sha adds owner or mode rules for these files, the Step 8/9 `--status` check is where it shows. A latched live-halt (BOOST end early, late sells, slow landing, stuck position) is cleared only by an offline, ledgered step, as `mal-live`, with the unit stopped:

```
sudo -u mal-live /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --live --clear-halt <NAME>
```

A halt is never followed by a retune. Do not clear one without telling the owner.

## Daily check (manager, 12:17Z cron)

`scripts/mal-fast/h5-daily-check.py` replaces the decommissioned-probe check (job #385's command). Read-only, no key, prints `INFO`/`OK`/`ALERT` lines and exits 1 on any alert.

- **Allowed now:** the `mal-h5-executor` unit (active or enabled), `/var/lib/mal-live/h5`, the pinned tree, `/etc/mal-h5/LIVE_OK`. The wallet is no longer expected to be 0.
- **Still alerts:** any `mal-probe-executor*` unit active or enabled; `/var/lib/mal-live/state-live.json` changed (sha256 against the baseline from `--write-baseline`, plus attempts <= 62, realized -0.210755 SOL, nothing open); `/var/lib/mal-live/STOP` missing.
- **New:** installed unit files differ from the pinned copies, an unexpected drop-in, the unit `failed`, a `HALT` file, a latched live halt, a stuck or abandoned position, the H5 state dir not `mal-live:mal-live` 0700, `/etc/mal-h5` not a real `root:root` 0755 directory, a `LIVE_OK` that is a symlink, not a regular file, not root-owned or group/other writable, a `LIVE_OK` in the state dir (it is not Helm's gate; find out who made it), the ledger naming another wallet, and **wallet balance vs funded + H5 realized - cost of open positions** outside a tolerance of 0.005 SOL plus 0.0021 SOL per open or pending position. Pass the total deposited, net of withdrawals, as `--funded-sol`; a top-up or withdrawal shows as a gap until you do. "H5 realized" is `realized_lamports` in the live state file, the number the loss stops use; the check does not sum the ledger.
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
| Stop trading, keep the unit | `sudo rm /etc/mal-h5/LIVE_OK` (closes the gate at once) and/or `sudo touch /var/lib/mal-live/h5/STOP`; positions exit on the timer. |
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
-w /etc/mal-h5 -p wa -k malh5-liveok
-w /etc/mal-probe-rpc -p rwa -k malprobe-rpc
```

Every creation or removal of `LIVE_OK` shows up under `malh5-liveok` (`sudo ausearch -k malh5-liveok -i`). Expect exactly Helm's create, and any removal Helm or the manager did. Any other writer is a finding.

The key directory `/etc/mal-probe` already has Helm's watch from DEC-019; check with `sudo auditctl -l | grep -i mal-probe` and add `-w /etc/mal-probe -p rwa -k malprobe-key` only if it is missing (do not duplicate it). Expect a read event on the key file at each unit start: that is systemd's `LoadCredential`. Any other reader is a finding. The `malh5-state` rule logs every write by the executor too; keep it for the first days, then drop it if the volume is too high.

## Never

- Never print, copy, paste or commit `/etc/mal-probe/probe-wallet.json`, `/etc/mal-probe-rpc/helius.env`, or their contents.
- Never run `tools.h5_executor`, `--clear-halt` or anything else that writes the state dir as root: root-owned files in `/var/lib/mal-live/h5` make the start precheck fail closed. `--status` as root only reads.
- Never run the executor or the tool from a working tree or from `/var/lib/mal/fast-forward/src`.
- Never install the live drop-in from a working tree; install it from `/usr/local/lib/mal-h5-exec/current/`.
- Never create `/etc/mal-h5/LIVE_OK` before the Step 5 hash check of the sha that will run, never from the executor's side, and never put a `LIVE_OK` in `/var/lib/mal-live/h5` (the executor does not read it and the daily check alerts on it).
- Never install any sha but the one the manager names in the PR #499 comment.
- Never raise a limit in config (config can only lower the code maxima), and never fund the wallet beyond what the owner decided.
- Canary and live fills are never a book and never count toward any gate (DEC-024).

## Not verified (written without host access)

The expected outputs in Steps 3b, 7, 8, 9 and 11 are written from the systemd documentation and from the code, not observed: the wording of `systemd-analyze verify` and `systemd-run` messages can differ by version, so judge them by meaning (clean of `Unknown`, `Invalid`, `Failed`; the sandbox lines as listed). Also unverified: the installer on the real host (venv build, `systemctl`, paths), systemd's reading of both units on this host's version, the `ProtectHome=tmpfs` plus `BindReadOnlyPaths` combination, the `Conflicts=` behaviour with the probe unit, the root `ExecStartPre`, the auditd rule syntax, the daily check's `systemctl` parsing, and the sell-and-close tool against the real chain. The tests use fakes for all of these.
