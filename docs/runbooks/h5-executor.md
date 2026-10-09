# H5 executor runbook (DEC-024)

The executor is `tools/h5_executor.py` (PR #484). Unit: `mal-h5-executor` on `mal-fast-0`, user `mal-live`. It reuses the DEC-019 probe's custody pattern exactly: the key is `/etc/mal-probe/probe-wallet.json` (root:root 0400, in `/etc/mal-probe` root 0700) and reaches the process only through systemd `LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json` in the live drop-in; the code runs from a root-owned pinned copy at a sha with a manifest. Read [probe-executor.md](probe-executor.md) section 2b and [probe-wallet.md](probe-wallet.md) for the pattern; read [DEC-024](../../DEC/DEC-024-h5-live-canary.md) for the limits. **Nothing below has been run on the host.** Every step has a check; if a check does not give the stated result, stop and tell the manager.

**Which sha: only the one the manager names in a comment on PR #499**, taken after the executor branch (`claude/h5-executor`) has merged `origin/main` (which brings EXP-024 into the tree) and its review-fix batch, and after this PR is merged into it. Do not install the head of this PR or of the executor branch as it stands, and do not take a sha from any other message. The CLI flags this runbook uses (`--config`, `--live`, `--status`, `--clear-halt`, `--run-tool sell_and_close`) are checked against that sha. The executor's dry-run `--rpc-env` option is not used here (the unit takes the key from the environment), and a config override of `LIVE_OK`, `STOP`, `HALT` or `FINAL_WRITTEN` is refused by the executor in live, so the pinned configs set none.

## Who does what

| Step | Who |
| --- | --- |
| Name the sha (PR comment), make the manifest, set `end_ms` in the reviewed live config, say when the owner has funded and give the go | manager |
| Stop the probe, pre-flight, install the pinned tree, hash check, drop-ins, auditd, watchdog, enable and start | Helm (root) |
| **Create `/etc/mal-h5/LIVE_OK`** (root:root 0644), only after the hash check of the sha that will run, the watchdog test message, and the manager's go | Helm (root) |
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
| Watchdog units, config and state | `/etc/systemd/system/mal-h5-watch.{service,timer}`, `/etc/mal-h5-watch/watch.env` (root:root 0600), `/var/lib/mal-h5-watch/` |
| State dir (mal-live, 0700), the only writable path of the unit | `/var/lib/mal-live/h5/` |
| Live state, counters, ledger | `/var/lib/mal-live/h5/live/{state-live.json,h5-counters.json,h5-ledger.jsonl}` (dry run: `.../dryrun/`) |
| `LIVE_OK` (the gate the executor cannot create) | `/etc/mal-h5/LIVE_OK`: a regular file owned `root:root` with mode **exactly 0644** (not 0600 or tighter: `mal-live` must be able to open it read-only; not group or other writable), no symlink. Parent `/etc/mal-h5` is `root:root` 0755. Readable by the unit, not writable (`ProtectSystem=strict`, not in `ReadWritePaths`). The path is pinned in the executor code. |
| **H5's `STOP`, `HALT`, `FINAL_WRITTEN`** | **`/var/lib/mal-live/h5/STOP`, `/var/lib/mal-live/h5/HALT`, `/var/lib/mal-live/h5/FINAL_WRITTEN`** (the executor only tests that they exist) |
| The probe's own files, untouched and read-only to this unit | `/var/lib/mal-live/{state-live.json,probe-fills.jsonl,STOP}` |
| Intents path the executor reads, inside the unit | `/srv/mal-h5-shadow` (config value `intents_file` in both pinned configs). The shadow job behind it must be at #477 head `fe7eb43` or later. |
| Root-only Helius env, shared with the probe | `/etc/mal-probe-rpc/helius.env` (root:root 0600 in a root:root 0700 dir) |

**Kill switches, stated once so nobody guesses.** The executor (from 4f05e30) stops on a `STOP` or `HALT` in either place: H5's own files in `/var/lib/mal-live/h5/` and the wallet-wide ones in `/var/lib/mal-live/` (the probe's file names). `STOP` means no new buys; `HALT` freezes everything, sells included. `--status` prints only the H5 files, so look at the wallet-wide ones with `sudo test -e /var/lib/mal-live/STOP` and `.../HALT`. **The probe's permanent `/var/lib/mal-live/STOP` is therefore a wallet-wide STOP for H5: while it exists, H5 never buys.** It has to be removed for the canary to trade, which ends the probe's STOP record (the probe stays off because its unit is stopped and disabled, the installer and the daily check refuse or alert otherwise, and the daily check keeps the probe's state-file hash). That removal is **the manager's written decision, recorded in the PR comment that names the sha**; Helm does it at Step 11 and not before, and never on his own. The old daily check required that file to exist; the new one does not (it reports it, and calls it an idle-canary reason once the gate is open). The daily check and the watchdog alert if `/var/lib/mal-live/HALT` exists.

`intents_file` is a config value, so changing it means a new reviewed sha and a re-pin. The host-specific part is only the bind source in step 7, which is not in the pinned tree.

## Before Helm starts (manager)

1. The sha, named in a comment on PR #499 once the executor branch has merged `origin/main` and its review fixes and this PR is merged into it. It must contain `EXP/EXP-024-h5-boostfloor-part1-prereg.md` (the installer refuses a sha without it, and so does the executor), the live config, and this runbook. The shipped live config carries `end_ms` = 2026-10-16T00:30Z (1792110600000): the executor stops buying then, and a later end is a new reviewed sha. A live config without `end_ms` would install and then refuse to start (`ALERT startup_refused end_ms_missing`); the installer prints a NOTE when it sees that. The pinned configs set `intents_file` = `/srv/mal-h5-shadow` and `feed_heartbeat_max_age_ms` = 150000 (taken from the executor branch as they are), and none of `late_sell_min_n`, `sell_priority_lamports`, `escalated_priority_lamports` (code constants now: the executor refuses a config that sets them) or `live_ok_file`, `stop_file`, `halt_file`, `final_marker_file` (pinned in live).
2. The manifest, made in the manager's own clone, not Helm's:
   `scripts/mal-fast/make-h5-manifest.sh <40-char-sha> > h5-manifest.txt`. It has one `<sha256>  <repo path>` line per file the installer reads. Give it to Helm and keep a copy for every sha that is installed.
3. The job user's detector output directory (`~/data/h5-shadow` of the MiScusi job user, as an absolute path), and **the shadow job running at the reviewed #477 head, `fe7eb43` or later**: the head whose trigger records carry `s0_minus_announced_slots` and `base_breaks_unresolved`, which the executor reads and refuses without (`bad_intent:*`). A shadow job started on an older head must be restarted on that head before go-live. This is a manager step; the daily check and the watchdog alert `h5_feed_schema` if triggers are refused for those fields.
4. The probe's `/var/lib/mal-live/STOP` decision (see Kill switches): written in the sha comment, yes or no.
5. The Discord webhook for the watchdog and the total SOL deposited, for Helm's step 10 (the webhook goes to Helm over the usual private channel, never into a PR, a note or a file in a repo).

## Wind-down: the precondition for every stop of the live unit

Every step in this runbook that stops the live unit, switches it to the dry run, rolls it back, reinstalls it or turns it off (Step 1b, Hard stop, the Rollback rows, Sell-and-close, Off entirely) first needs this. **The only exception is an emergency `HALT`**, which freezes sells too and leaves positions open on purpose. A stopped or dry-run unit has no seller: the dry run keeps its own state in `dryrun/` and cannot see a live position, and nothing else reports one except the daily check's `h5_positions_unmanaged` and the watchdog.

```
sudo rm -f /etc/mal-h5/LIVE_OK                     # no new buys (and/or: sudo touch /var/lib/mal-live/h5/STOP)
S="sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --status"
$S | grep '^\[live\]'                              # repeat until: [live] attempts=... open=0/2 pending=0 ...
```

An open position exits on its timer at s0 + 330 s and the executor's own hard deadline is s0 + 400 s, so wait up to about 7 minutes. If `open` or `pending` is still not 0 after that, **do not stop the unit**: look at the journal and the alerts. If the executor cannot sell it, stop the unit and run Sell-and-close for EACH open mint. The open mints (public addresses) are listed by:

```
sudo python3 -I -c 'import json; print(*json.load(open("/var/lib/mal-live/h5/live/state-live.json"))["open"], sep="\n")'
```

Only when `--status` shows `open=0/2 pending=0` (or every open mint has been sold and closed) go on to the stop.

## Helm's steps, in order

Open one root login shell for Steps 3 to 5 (`sudo -i`) and stay in it; they run from `/root`. Steps 1, 2 and 6 to 11 use `sudo` per command.

**Step 1. Stop and disable the old probe unit first, so two processes never share the key.**

```
sudo systemctl stop mal-probe-executor
sudo systemctl disable mal-probe-executor
systemctl show -p ActiveState --value mal-probe-executor    # expect: inactive (or failed)
systemctl is-enabled mal-probe-executor       # expect: disabled
pgrep -af 'tools.probe_executor|probe_exec_launcher' || echo none     # expect: none
sudo test -e /var/lib/mal-live/STOP && echo probe-stop-present       # record whether the probe's STOP is there; it is NOT removed here (see Kill switches, Step 11)
sudo sha256sum /var/lib/mal-live/state-live.json                      # tell the manager; it must not change from here on
```

Leave `/etc/systemd/system/mal-probe-executor.service.d/live.conf` where it is (it is the probe's record). The H5 base unit has `Conflicts=` and `After=mal-probe-executor.service` (so the probe's stop is ordered before H5's start), the installer refuses unless the probe unit's ActiveState is inactive or failed and it is not enabled, and the daily check alerts if any `mal-probe-executor*` unit is active or enabled. Do not `systemctl mask` it: the base unit is a regular file in `/etc/systemd/system` and masking would need `--force`.

**Step 1b. On a reinstall only (an earlier install exists).**

1. Do the Wind-down above (gate closed, `open=0 pending=0`), then stop the unit: `sudo systemctl stop mal-h5-executor`, and check `systemctl show -p ActiveState --value mal-h5-executor` says `inactive`.
2. Move the old live drop-in away, so the install and the dry run of Step 8 are keyless: `sudo mv /etc/systemd/system/mal-h5-executor.service.d/live.conf /root/live.conf.prev && sudo systemctl daemon-reload`.

The installer refuses while `/etc/mal-h5/LIVE_OK` or `live.conf` exists, so every install starts with the gate closed and no key drop-in; `LIVE_OK` is created again only after that install's hash check, and `live.conf` is installed again in Step 9.

**Step 2. Provision, once. Nothing here prints a key.**

```
getent passwd mal-live                                                # uid 999, nologin
sudo stat -c '%U:%G %a %n' /etc/mal-probe /etc/mal-probe/probe-wallet.json /etc/mal-probe-rpc /etc/mal-probe-rpc/helius.env
#   expect: root:root 700 /etc/mal-probe | root:root 400 .../probe-wallet.json | root:root 700 /etc/mal-probe-rpc | root:root 600 .../helius.env
sudo install -d -m 0700 -o mal-live -g mal-live /var/lib/mal-live/h5
sudo install -d -m 0755 -o root -g root /srv/mal-h5-shadow             # the (empty) mount point for the shadow feed
sudo install -d -m 0755 -o root -g root /etc/mal-h5                    # holds LIVE_OK; root-owned so the executor cannot create it (the installer also does this)
sudo stat -c '%U:%G %a %F %n' /etc/mal-h5 /srv/mal-h5-shadow /var/lib/mal-live/h5
#   expect: root:root 755 directory /etc/mal-h5 | root:root 755 directory /srv/mal-h5-shadow | mal-live:mal-live 700 directory /var/lib/mal-live/h5
test ! -e /etc/mal-h5/LIVE_OK && echo "gate closed"                    # expect: gate closed. Do NOT create LIVE_OK yet.
```

Never `cat` or `less` the key file or the env file.

**Step 3. A fresh root-owned clone at the sha.** Never the agent checkout. In the root shell, from `/root`:

```
cd /root
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

Stop if any line says `Unknown key`, `Unknown assignment`, `Invalid`, `Failed to parse` or `not supported`, or names `ProtectHome`, `TemporaryFileSystem`, `RestartPreventExitStatus`, `LimitCORE`, `RestrictNamespaces`, `ProtectClock`, `UMask` or `Conflicts`.

*3b-3. The sandbox combination, in one transient unit with every mount-related property of the real one.* It runs as `mal-live` against the real shadow directory (read-only operations only; the one write attempt must be refused). It looks only at the detector's hourly files, `h5-shadow-YYYY-MM-DDTHH.jsonl`; the same directory also holds `h5-shadow-status.json` and `h5-shadow-errors.log`, which are not hourly files and are filtered out:

```
SHADOW=/home/<jobuser>/data/h5-shadow          # the absolute path the manager gave you: the detector job's output directory
P=(-q --wait --collect --pipe -p User=mal-live -p ProtectSystem=strict -p ProtectHome=tmpfs -p TemporaryFileSystem=/var/lib/mal:ro
   -p ReadWritePaths=/var/lib/mal-live/h5 -p "BindReadOnlyPaths=-$SHADOW:/srv/mal-h5-shadow" -p PrivateTmp=true -p ProtectProc=invisible)
sudo systemd-run "${P[@]}" /bin/sh -c '
echo "home:"; ls -A /home
echo "shadow:"; ls /srv/mal-h5-shadow | grep -E "^h5-shadow-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}[.]jsonl$" | tail -3
f=$(ls /srv/mal-h5-shadow | grep -E "^h5-shadow-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}[.]jsonl$" | tail -1)
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
| nothing or an error under `shadow:` | the bind is not visible, the detector has written no hourly file yet, `$SHADOW` is wrong or missing (`ls -ld "$SHADOW"`), or the `ProtectHome=tmpfs` plus bind combination does not work on this systemd | check `$SHADOW` and that the detector runs; if both are right, stop and report. The fallback is a detector output directory outside `/home` and a checker change, which is a new sha; the manager decides |
| `shadow-file-read: FAILED` | the detector's files are not readable by others (mode 600) | the manager fixes the detector's umask or modes |
| `shadow-write: SUCCEEDED (BAD)` | the bind is not read-only | `rm "$SHADOW/.preflight"` (as the job user), stop |
| `h5-dir-write: FAILED` | step 2 not done, or `/var/lib/mal-live/h5` not `mal-live` 0700, or `ReadWritePaths` not applied | fix step 2, rerun |
| `probe-dir-write: SUCCEEDED (BAD)` | the sandbox leaves the probe's directory writable | `sudo rm /var/lib/mal-live/.preflight`, stop |
| `etc-mal-h5-write: SUCCEEDED (BAD)` | the sandbox lets the process write where `LIVE_OK` lives | `sudo rm /etc/mal-h5/.preflight`, stop |
| `etc-mal-h5-read: FAILED` | the unit cannot read `/etc/mal-h5`, so it could not see `LIVE_OK` | check `stat /etc/mal-h5` (root:root 0755), stop |
| names under `var-lib-mal:` | `TemporaryFileSystem=/var/lib/mal:ro` was not applied | stop |

The real unit is checked the same way once more after it runs (Step 8, `nsenter`), and `systemd-analyze verify` runs again on the installed unit (Steps 7 and 9).

**Step 4. Install.** Run it from the root shell of Step 3, from `/root` (the installer itself does `cd /`, pins `PATH` to `/usr/sbin:/usr/bin:/sbin:/bin` and unsets `PYTHONPATH`, `PYTHONHOME` and `PYTHONSTARTUP`, and uses `python3 -I`, so nothing from your shell leaks in; keep to this anyway). The unit must be stopped. The installer refuses a dirty clone, a clone not at the sha, a missing or mismatching manifest entry, a base unit, drop-in or watchdog unit that is not identical to the intended text (allowlist check), a missing `/etc/mal-probe-rpc` pair, either unit whose ActiveState is not exactly `inactive` or `failed` (an `activating` unit waiting out a restart is not stopped; an unreadable state counts as not stopped), an enabled `mal-probe-executor`, a `/etc/mal-h5` that is not a real `root:root` 0755 directory, an existing `/etc/mal-h5/LIVE_OK`, and an existing `live.conf`. It provisions `/etc/mal-h5` (root 0755) and never creates `LIVE_OK`. It rolls back on any failure after the point of no return.

```
cd /root
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

The checker accepts only `/home/<user>/.../h5-shadow*` with no hidden path component. If the detector job is restarted into a new directory (new inode), restart `mal-h5-executor` too, because the bind pins the old one. A missing source is a silent no-op at the unit level (leading `-`); the executor then reports `signals_absent` and buys nothing, and the daily check and the watchdog alert on a stale feed.

**Step 8. Keyless dry run, at least 10 minutes. No key is involved.** This proves the pinned path, the sandbox, the state dir, the shadow bind and the RPC env on the real host before anything live. First prove that nothing can hand the process a key:

```
test ! -e /etc/systemd/system/mal-h5-executor.service.d/live.conf && echo "no live drop-in"     # expect: no live drop-in. If live.conf exists, STOP: this would be a live start. Do step 1b.
systemctl cat mal-h5-executor | grep -c LoadCredential                  # expect: 0
systemctl show mal-h5-executor -p ExecStart --value | grep -c -- '--live'      # expect: 0
sudo systemctl start mal-h5-executor
journalctl -u mal-h5-executor -n 30 --no-pager          # expect: h5_executor mode=dryrun ...; no traceback; no startup_refused
sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor.json --status
sudo ls -l /var/lib/mal-live/h5/dryrun/                 # state, counters, ledger exist, owned by mal-live
PID=$(systemctl show -p MainPID --value mal-h5-executor)   # the running unit's own mount namespace, while it is up
sudo nsenter -t "$PID" -m /bin/ls -A /home               # expect: nothing
sudo nsenter -t "$PID" -m /bin/ls /srv/mal-h5-shadow | grep -E '^h5-shadow-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}[.]jsonl$' | tail -3     # expect: the newest hourly files
sudo awk '$5 == "/home" || $5 == "/srv/mal-h5-shadow" {print $5, $6}' /proc/$PID/mountinfo
#   expect two lines, each with ro as the first mount option: "/home ro,..." and "/srv/mal-h5-shadow ro,..."
sudo systemctl stop mal-h5-executor
```

If the ledger shows `signals_absent` the whole time, the bind is wrong; fix step 7 before going on. The dry run holds no live position, so stopping it needs no Wind-down. Running `--status` as root only reads; never run the executor itself, or `--clear-halt`, as root (see Never).

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
#   expect the first two lines to read: stop_file=False halt_file=False live_ok=live_ok_missing (/etc/mal-h5/LIVE_OK) exp024_part1=True
#   live_ok_missing: the gate is closed. exp024_part1=False means this sha has no EXP-024: stop, it is the wrong sha.
sudo systemctl enable mal-h5-executor                                                  # WantedBy=multi-user.target; with no LIVE_OK it refuses at start (exit 2) and is not restarted
```

**Step 10. Auditd and the watchdog (both are required before the manager's go).**

*Auditd:* the rules in "Auditd" below.

*Watchdog (DEC-024 section 8):* the once-a-day check is not enough for a stuck position. The probe's own `mal-probe-watch` timer is Helm's host-local file and is not in the repo, so this is a new H5 one: `h5-watch.py` runs every 5 minutes as a hardened root oneshot from the pinned tree, runs the daily check's engine, and posts to Discord when an alert is new, every 6 h while it lasts, and when it clears. It watches: a stuck or abandoned position, any latched live halt, HALT files, STOP placed or removed, the unit restarting, the unit failed or idle, a stale shadow feed, positions with no live unit, and the wallet balance against the funded amount plus the H5 realized total. It holds no key and uses the public RPC for the balance.

```
sudo install -d -m 0700 -o root -g root /etc/mal-h5-watch
# Helm creates /etc/mal-h5-watch/watch.env as root:root 0600 with exactly three lines (the webhook is a secret: never print it, never paste it into a ticket):
#   H5_WATCH_DISCORD_WEBHOOK=<the webhook URL>
#   H5_WATCH_FUNDED_SOL=0.25                              # total SOL deposited, net of withdrawals; update it after a top-up or a withdrawal
#   H5_WATCH_SHADOW_DIR=/home/<jobuser>/data/h5-shadow    # the same $SHADOW as step 7
sudo stat -c '%U:%G %a %n' /etc/mal-h5-watch /etc/mal-h5-watch/watch.env     # expect: root:root 700 /etc/mal-h5-watch | root:root 600 /etc/mal-h5-watch/watch.env
python3 -I /usr/local/lib/mal-h5-exec/current/check-h5-unit.py --watch-service /usr/local/lib/mal-h5-exec/current/mal-h5-watch.service && echo watch service ok
python3 -I /usr/local/lib/mal-h5-exec/current/check-h5-unit.py --watch-timer /usr/local/lib/mal-h5-exec/current/mal-h5-watch.timer && echo watch timer ok
sudo install -m 0644 -o root -g root /usr/local/lib/mal-h5-exec/current/mal-h5-watch.service /etc/systemd/system/mal-h5-watch.service
sudo install -m 0644 -o root -g root /usr/local/lib/mal-h5-exec/current/mal-h5-watch.timer /etc/systemd/system/mal-h5-watch.timer
sudo systemctl daemon-reload
sudo systemd-run --wait --collect --pipe -p EnvironmentFile=/etc/mal-h5-watch/watch.env /usr/bin/python3 -I -B /usr/local/lib/mal-h5-exec/current/h5-watch.py --test-message
#   expect: "h5_watch: test message posted" on screen, and the line "[H5 watch] test message ..." in the Discord channel. Tell the manager to look.
sudo systemctl enable --now mal-h5-watch.timer
sudo systemctl start mal-h5-watch.service; journalctl -u mal-h5-watch.service -n 5 --no-pager      # expect: h5_watch: N alert(s), M line(s) posted; status=0/SUCCESS
systemctl is-enabled mal-h5-watch.timer; systemctl is-active mal-h5-watch.timer                  # expect: enabled, active
```

The first runs may post real alerts (a closed gate is not one; a stale feed or a missing `/var/lib/mal-live/STOP` is). Then tell the manager "steps 1 to 10 done" with the step 5 table and that the Discord test message was posted.

**Step 11. Create `LIVE_OK`, then start, once the manager has said go and the owner has funded the wallet** (see "Going live"). `LIVE_OK` is created by Helm, as root, only after the Step 5 hash check of the sha that will run.

```
sudo test ! -e /var/lib/mal-live/HALT && echo "no wallet-wide HALT"     # expect it
sudo test ! -e /var/lib/mal-live/STOP && echo "no wallet-wide STOP"     # expect it. If the probe's STOP is still there, remove it (sudo rm /var/lib/mal-live/STOP) ONLY on the manager's written OK in the sha comment; otherwise stop here.
sudo install -m 0644 -o root -g root /dev/null /etc/mal-h5/LIVE_OK
stat -c '%U:%G %a %F %n' /etc/mal-h5/LIVE_OK                 # expect exactly: root:root 644 regular empty file /etc/mal-h5/LIVE_OK
sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --status
#   expect the first two lines to read: stop_file=False halt_file=False live_ok=valid (/etc/mal-h5/LIVE_OK) exp024_part1=True
#   live_ok=live_ok_unsafe: the file or /etc/mal-h5 is not root-owned, or is group/other writable, or is a symlink: remove it, fix, recreate.
#   live_ok=live_ok_missing after the install above: this sha does not read /etc/mal-h5/LIVE_OK: remove the file, stop, tell the manager.
sudo systemctl start mal-h5-executor
journalctl -u mal-h5-executor -n 20 --no-pager          # expect: h5_executor mode=live user=<public key>   (5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk)
```

If it prints `ALERT startup_refused <why>` it exits 2 and stays stopped: `live_ok_missing`, `live_ok_unsafe`, `exp024_part1_missing`, `end_ms_missing`, `config_path_override`, `rpc_env ...` or `rpc_key_missing`. Fix the cause (the first four are the manager's or Helm's file facts), then `systemctl start` again.

## Going live (manager, then Helm)

Preconditions are DEC-024 section 8 and the PR #484 list; this runbook adds the mechanics. The gate is `/etc/mal-h5/LIVE_OK`, in a root-owned directory the executor cannot write (DEC-024 section 3). The executor accepts it only as a regular file owned `root:root` with mode exactly 0644, not a symlink, in a `root:root` 0755 parent, at that fixed path (a config override is refused in live). The manager does not create it; Helm does, only after the hash check.

1. Manager, after Helm reports steps 1 to 10 (the step 5 table matches; the **watchdog timer `mal-h5-watch.timer` is enabled and active and the manager has seen the Discord test message**), the owner has funded the wallet, the dry run of step 8 was clean, **the shadow job is running at the reviewed #477 head, fe7eb43 or later** (restarted on it if it was started earlier; its newest hourly file carries the two new trigger fields) and the probe-STOP decision is in the sha comment:

```
python3 -I scripts/mal-fast/h5-daily-check.py --funded-sol 0.25 --write-baseline --expect-sha256 <the sha256 Helm reported in Step 1>
#   once: records the probe state's sha256, and refuses if it is not the one Helm saw in Step 1. Expect no ALERT; LIVE_OK=no is right at this point.
```

2. Manager tells Helm "go". Going live does not happen without the watchdog: the daily check alerts `h5_watch_timer` if the timer is not enabled and active while the gate is open.
3. Helm runs step 11: creates `LIVE_OK` (root 0644), checks `--status` shows `live_ok=valid`, starts the unit.

Removing `LIVE_OK` stops new buys at once, like `STOP`; open positions still exit on the timer. The executor checks the file immediately before every live buy, not only at start. Helm or the manager may remove it (`sudo rm /etc/mal-h5/LIVE_OK`); only Helm creates it, and the installer refuses to run while it exists, so every install begins with the gate closed.

## Stop, halt, status

| Action | Command (manager, `sudo`) | Effect |
| --- | --- | --- |
| Stop new buys | `sudo touch /var/lib/mal-live/h5/STOP` | No new buys; open positions still exit on the timer. Remove with `sudo rm /var/lib/mal-live/h5/STOP`. |
| Freeze everything | `sudo touch /var/lib/mal-live/h5/HALT` | No buys, no sells, no rebroadcasts. Open positions stay open. Only if the executor or the wallet is wrong. This is the only stop that needs no Wind-down. |
| Stop new buys by closing the gate | `sudo rm /etc/mal-h5/LIVE_OK` (Helm or the manager) | Same as `STOP` for buys. Only Helm creates it again. |
| EXP-022 seal marker | `sudo touch /var/lib/mal-live/h5/FINAL_WRITTEN`, only after the DEC-016 FINAL is written | Same directory and same method as `STOP`; the seal rules around it are in #484. |
| Status (local files only, no key, no RPC) | `sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --status` | Kill files, `live_ok`, attempts, realized, open, pending, latched halts. |
| Hard stop | Wind-down first (above), then `sudo systemctl stop mal-h5-executor` | A restart resumes pending signatures without re-buying. Never stop it with `open` or `pending` above 0 unless every open mint is sold and closed first (Sell-and-close). |
| Daily check | `python3 -I scripts/mal-fast/h5-daily-check.py --funded-sol <total deposited>` | See below. |

**Either place works** (see "Kill switches" under "What is where"): H5's own files above, or the wallet-wide `/var/lib/mal-live/STOP` and `/var/lib/mal-live/HALT` that the probe runbook's `sudo touch /var/lib/mal-live/STOP` makes. A `STOP` left at the wallet-wide path stops H5 buys until it is removed, so remove the one you placed when you mean to resume, and look for both: `sudo ls /var/lib/mal-live/ /var/lib/mal-live/h5/ | grep -E '^(STOP|HALT)$'`.

**How the manager creates `STOP`, `HALT` and `FINAL_WRITTEN`:** `sudo touch <file>` on fast-0. The state dir is `mal-live` 0700, so the manager needs `sudo`. The executor only tests that these files exist (`probe_executor.check_stop_file` and `check_halt_file` are `Path.exists()` at the current head); it asks for no owner and no mode, so the `root:root` 0644 file that `sudo touch` makes is accepted. Check after touching: `--status` prints `stop_file=True` (or `halt_file=True`). A dangling symlink does not count as existing, so create a plain file, not a link. If a later sha adds owner or mode rules for these files, the Step 9 `--status` check is where it shows. A latched live-halt (BOOST end early, late sells, slow landing, stuck position) is cleared only by an offline, ledgered step, as `mal-live`, with the unit stopped (after the Wind-down):

```
sudo -u mal-live /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --live --clear-halt <NAME>
```

A halt is never followed by a retune. Do not clear one without telling the owner.

## Daily check (manager, 12:17Z cron) and the watchdog

`scripts/mal-fast/h5-daily-check.py` replaces the decommissioned-probe check (job #385's command). Read-only, no key, prints `INFO`/`OK`/`ALERT` lines and exits 1 on any alert. The watchdog (`h5-watch.py`, every 5 minutes) runs the same checks and posts the alerts to Discord; the daily run is the manager's own second look and also checks the probe's state.

- **Allowed now:** the `mal-h5-executor` unit (active or enabled), `/var/lib/mal-live/h5`, the pinned tree, `/etc/mal-h5/LIVE_OK`, the watchdog timer. The wallet is no longer expected to be 0.
- **Still alerts:** any `mal-probe-executor*` unit active or enabled; `/var/lib/mal-live/state-live.json` changed (sha256 against the baseline from `--write-baseline`, plus attempts <= 62, realized -0.210755 SOL, nothing open). (The old "`/var/lib/mal-live/STOP` must exist" alert is gone: the executor treats it as a wallet-wide STOP.)
- **Positions without a seller:** `h5_positions_unmanaged` when the live state has open or pending positions and the unit is not active with `--live` (stopped, failed, activating, not installed, or a dry-run ExecStart). This is the alert for a stop done without the Wind-down.
- **Idle canary:** `h5_feed_stale` (newest `h5-shadow-<hour>.jsonl` in `--shadow-dir`, default `~/data/h5-shadow`, older than 10 minutes, or none); once `LIVE_OK` exists, `h5_idle` (unit not active, not enabled, running ExecStart without `--live`, or a `STOP` present at either place), `h5_idle_ledger` (`LIVE_OK` older than 6 h and no buy, skip or decision row in the live ledger in 6 h) and `h5_watch_timer` (the watchdog timer not enabled and active).
- **Unit files:** the installed base unit and `FragmentPath` equal the pinned copies; the drop-in list is what systemd applies (`DropInPaths`, which includes prefix and top-level `.d` directories and `/run`) and may hold only `live.conf` (equal to the pinned drop-in) and a valid `10-shadow-feed.conf`.
- **Other:** the unit `failed`, a `HALT` file in the state dir or at `/var/lib/mal-live/HALT`, a latched live halt, a stuck or abandoned position, the H5 state dir not `mal-live:mal-live` 0700, `/etc/mal-h5` not a real `root:root` 0755 directory, a `LIVE_OK` that is a symlink, not a regular file, or not owned `root:root` with mode exactly 0644, a `LIVE_OK` in the state dir (it is not Helm's gate; find out who made it), the ledger naming another wallet, and **wallet balance vs funded + H5 realized - cost of open positions - buys in flight** outside a tolerance of 0.005 SOL plus 0.0021 SOL per open or pending position. Pass the total deposited, net of withdrawals, as `--funded-sol`; a top-up or withdrawal shows as a gap until you do. "H5 realized" is `realized_lamports` in the live state file, the number the loss stops use; the check does not sum the ledger.
- **Halts and refusals:** the alert text for a latched halt carries the halt's name and meaning, and the INFO line "refusals in the last 6 h" lists the `skip` reasons. The names are in "Halt and refusal names" below. `h5_s0_refusals` (3 or more `s0_recv_late`/`s0_unverifiable` in 6 h) and `h5_feed_schema` (a `bad_intent:missing_*` field, or 5 or more `s0_minus_announced_slots`/`base_breaks_unresolved` refusals with no decision in 6 h) are alerts; the sealed stub `bad_intent:suppressed` never is.
- **How it reads:** the state dir is read through fixed paths with `stat` first (a symlink, a hard-linked file or a non-regular file is refused) and then `O_NOFOLLOW` or `sudo -n /usr/bin/dd iflag=nofollow`, never `cat`. File content is never put into an alert. A failing `sudo -n` is `ALERT sudo_unavailable` or `sudo_failed`, never "absent". The RPC key is read from `HELIUS_API_KEY` or the paper env file for `getBalance` only (the watchdog uses the public RPC and no key) and is never printed.

### Halt and refusal names

Latched live halts (the ledger gets a `halt_latched` row; new buys stop; only `--clear-halt NAME` clears one, and a halt is never followed by a retune):

| Name | Meaning |
| --- | --- |
| `boost_median_lt_335` | UTC-day median of the BOOST last-slice time below 335 s |
| `boost_structure_lt_300_x3` | BOOST last slice below 300 s on three pools |
| `boost_median_lt_337_twice` | day median below 337 s on two days |
| `boost_before_sell_gt_15pct` | BOOST ended before our sell on more than 15% of sells |
| `stuck_position` | a position not sold by its deadline (s0 + 400 s) |
| `out_of_rule_entry` | a buy landed outside the rule's entry window |
| `landing_median_gt_3s` | median trigger-to-landing above 3 s |
| `late_sells_gt_5pct` | more than 5% of landed sells late |

Trigger refusals (`skip` rows with `reason=...`; a refusal costs a trade, not safety):

| Reason | Meaning |
| --- | --- |
| `s0_recv_late` | the detector says it received s0 more than 1.5 s after our own mapping of `s0_slot`: the feed was backlogged, or the claim is false |
| `s0_unverifiable` | s0 could not be mapped from our slot history or the print's block time |
| `bad_intent:s0_minus_announced_slots` | the pool's first print we call s0 came more than 2 slots after its CreatePool, or the field is missing or null |
| `bad_intent:base_breaks_unresolved` | the order-independent missed-print count is not 0, or the field is missing or null |
| `bad_intent:missing_<field>` | a field the executor reads is absent from the record: an older #477 head |
| `bad_intent:suppressed` | #477's sealed stub from 2026-10-16T01Z: expected, never an alert |
| other `bad_intent:*` (`v_missing`, `gap`, `sps_span`, `boost_spent`, ...) | data-quality refusals of one trigger, shown in the INFO line only |

## Sell-and-close an abandoned position (Helm or the owner, root)

Trigger: `ALERT sell_abandoned` or `stuck_position` (a position not sold by s0 + 400 s, after the executor's own emergency sell), or the daily check's `h5_stuck_position`, or `h5_positions_unmanaged`. The tool sells ALL tokens of one named mint on its canonical PumpSwap pool, then closes the token account and the wrapped-SOL account for the rent, in one transaction. It runs from the pinned tree, reads the key file directly like `probe-withdraw.sh` (root:root 0400 in a 0700 dir), applies the probe's pre-signing allowlist, and **simulates by default**. It prints the signature and the fill, never key material.

Do the Wind-down first (remove `LIVE_OK`), stop the unit, and run the tool once for EACH open mint (list them with the one-line command in the Wind-down section). Run it again for a mint only if the first run did not sell it.

```
sudo rm -f /etc/mal-h5/LIVE_OK
sudo systemctl stop mal-h5-executor
systemctl show -p ActiveState --value mal-h5-executor mal-probe-executor      # expect: inactive/failed for both (the tool checks this itself and refuses otherwise)
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

The tool refuses while either executor unit's ActiveState is anything but `inactive` or `failed` (an `activating` unit is not stopped), and refuses if systemd gives no answer. `min_out` is never 0. A tiny quote that would give 0 is refused (use `--emergency`). With a zero token balance the tool only closes the accounts. Send the manager the signature and the fill line.

After a `--send`, the position is still "open" in the executor's state. Book it with the executor's offline `--mark-closed`, with the unit stopped (it takes the lock and refuses while the unit runs). It fetches the transaction and checks that our wallet signed it, that it sold this mint through PumpSwap, that the token account is closed or empty and that the position exists; only then does it move the position from open to closed, book the realized result from the transaction meta, and write a `manual_close` ledger row. Any failed check refuses and changes nothing. It runs as `mal-live`, and gets the RPC key from the root-only env file through systemd, never through a command line:

```
sudo systemd-run --wait --collect --pipe -p User=mal-live -p EnvironmentFile=/etc/mal-probe-rpc/helius.env -p ProtectSystem=strict -p ReadWritePaths=/var/lib/mal-live/h5 -p UMask=0077 \
  /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --mark-closed <MINT> --sig <SIGNATURE>
#   expect: h5_executor --mark-closed: <MINT> closed by <SIGNATURE>; realized N lamports
sudo /usr/local/lib/mal-h5-exec/venv/bin/python -I -B -u /usr/local/lib/mal-h5-exec/current/launcher.py --config /usr/local/lib/mal-h5-exec/current/h5-executor-live.json --status | grep '^\[live\]'     # open=0/2 pending=0 once every open mint is booked
```

Until every open mint is booked and the stuck-position halt is cleared (`--clear-halt stuck_position`, above), the unit stays stopped and `LIVE_OK` stays removed. Send the manager the signature, the fill line and the `--mark-closed` output.

## Rollback

| To | Do |
| --- | --- |
| Stop trading, keep the unit | `sudo rm /etc/mal-h5/LIVE_OK` (closes the gate at once) and/or `sudo touch /var/lib/mal-live/h5/STOP`; positions exit on the timer. |
| Back to the keyless dry run | **Wind-down first** (gate closed, `open=0 pending=0`, or every open mint sold and closed with Sell-and-close). Then `sudo systemctl stop mal-h5-executor`, `sudo mv /etc/systemd/system/mal-h5-executor.service.d/live.conf /root/live.conf.prev`, `sudo systemctl daemon-reload`, `sudo systemctl start mal-h5-executor`; then repeat the three "no live drop-in" checks of Step 8. Without the drop-in nothing hands the process the key. |
| An earlier pinned sha | **Never a bare symlink swap** (it skips the hash check, keeps the gate open and keeps the newer sha's base unit and venv). Wind-down first. Then a full re-install of the old sha: stop the unit, Step 1b (move `live.conf` away), `sudo rm -rf /usr/local/lib/mal-h5-exec/<old-sha>` (the installer refuses an existing sha directory), run the installer for `<old-sha>` with ITS manifest (the manager keeps one per sha), then Steps 5, 9 and 11 again, so `LIVE_OK` is created only after the old tree's hash check. |
| A failed install | The installer rolls back by itself; if it prints "rollback FAILED", run the lines it prints, as root, then rerun. |
| Off entirely | **Wind-down first**, then `sudo systemctl disable --now mal-h5-executor mal-h5-watch.timer`, move `live.conf` away. To withdraw, follow [probe-wallet.md](probe-wallet.md) section 3 (`probe-withdraw.sh --to <OWNER_DEST>`). That tool checks only `mal-probe-executor` and the probe's state files, not H5, so first run `systemctl show -p ActiveState --value mal-h5-executor mal-probe-executor` (expect inactive/failed for both), `--status` (expect `open=0 pending=0`), and confirm no stranded token (it refuses non-zero token balances). |

Never bring the old `mal-probe-executor` back while this unit is installed. If the probe has to run again, Wind-down, stop and disable `mal-h5-executor` first.

## Auditd (Helm)

Add `/etc/audit/rules.d/mal-h5.rules`, then `sudo augenrules --load` and `sudo auditctl -l | grep malh5`:

```
-w /usr/local/lib/mal-h5-exec -p wa -k malh5-code
-w /etc/systemd/system/mal-h5-executor.service -p wa -k malh5-unit
-w /etc/systemd/system/mal-h5-executor.service.d -p wa -k malh5-unit
-w /var/lib/mal-live/h5 -p wa -k malh5-state
-w /etc/mal-h5 -p wa -k malh5-liveok
-w /etc/mal-probe-rpc -p rwa -k malprobe-rpc
-w /etc/mal-h5-watch -p wa -k malh5-watch
```

Every creation or removal of `LIVE_OK` shows up under `malh5-liveok` (`sudo ausearch -k malh5-liveok -i`). Expect exactly Helm's create, and any removal Helm or the manager did. Any other writer is a finding. There are no watches on systemd's prefix and top-level `.d` directories (an override meant for other `mal-*` units would also apply to this one): the daily check and the watchdog catch that through `DropInPaths` instead.

The key directory `/etc/mal-probe` already has Helm's watch from DEC-019; check with `sudo auditctl -l | grep -i mal-probe` and add `-w /etc/mal-probe -p rwa -k malprobe-key` only if it is missing (do not duplicate it). Expect a read event on the key file at each unit start: that is systemd's `LoadCredential`. Any other reader is a finding. The `malh5-state` rule logs every write by the executor too; keep it for the first days, then drop it if the volume is too high.

## Never

- Never print, copy, paste or commit `/etc/mal-probe/probe-wallet.json`, `/etc/mal-probe-rpc/helius.env`, `/etc/mal-h5-watch/watch.env` or the Discord webhook, or their contents.
- Never run `tools.h5_executor`, `--clear-halt` or anything else that writes the state dir as root: root-owned files in `/var/lib/mal-live/h5` make the start precheck fail closed. `--status` as root only reads.
- Never stop the live unit, switch it to the dry run, roll it back or turn it off with `open` or `pending` above 0 (the Wind-down), except by an emergency `HALT`.
- Never run the executor or the tool from a working tree or from `/var/lib/mal/fast-forward/src`.
- Never install the live drop-in from a working tree; install it from `/usr/local/lib/mal-h5-exec/current/`.
- Never create `/etc/mal-h5/LIVE_OK` with any mode but 0644 owned root:root, and never before the Step 5 hash check of the sha that will run, before the watchdog is enabled and its test message seen, or from the executor's side, and never put a `LIVE_OK` in `/var/lib/mal-live/h5` (the executor does not read it and the daily check alerts on it).
- Never install any sha but the one the manager names in the PR #499 comment, and never roll back by swapping the `current` link.
- Never remove the probe's `/var/lib/mal-live/STOP` on your own: it is the manager's written decision in the sha comment.
- Never raise a limit in config (config can only lower the code maxima), and never fund the wallet beyond what the owner decided.
- Canary and live fills are never a book and never count toward any gate (DEC-024).

## Not verified (written without host access)

The expected outputs in Steps 3b, 7, 8, 9, 10 and 11 are written from the systemd documentation and from the code, not observed: the wording of `systemd-analyze verify` and `systemd-run` messages can differ by version, so judge them by meaning (clean of `Unknown`, `Invalid`, `Failed`; the sandbox lines as listed). Also unverified: the installer on the real host (venv build, `systemctl`, paths), systemd's reading of the units on this host's version (including `RestrictNamespaces=true`, `ProtectClock=true` and `UMask=0077` on the executor with its Python and native extensions, and the watchdog running as root with only `CAP_DAC_READ_SEARCH` and talking to systemd over AF_UNIX), the `ProtectHome=tmpfs` plus `BindReadOnlyPaths` combination, the `Conflicts=`/`After=` ordering with the probe unit, the root `ExecStartPre`, the auditd rule syntax, the daily check's and the watchdog's parsing of real `systemctl` output and of `DropInPaths`, Discord accepting the watchdog's post, and the sell-and-close tool against the real chain. The tests use fakes for all of these.
