# Probe executor runbook (DEC-019)

The executor is `tools/probe_executor.py` (keyless dry run) plus `tools/probe_live.py` (live). Unit: `mal-probe-executor` on `mal-fast-0`, user `mal-live`. The key is created by Helm or the owner (see [probe-wallet.md](probe-wallet.md), PR #289, for the creation steps; the key location is now `/etc/mal-probe`, below). The manager never sees it.

## Who does what

| Step | Who |
| --- | --- |
| Install and start the dry-run unit | manager |
| Create the key, fund 0.5 SOL, name the withdraw address | Helm or owner |
| Install the live drop-in and restart | Helm or owner (after the DEC-019 section 6 checklist is all done) |
| Install the pinned copy (section 2b) and swap the drop-in | Helm or owner |
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

Key custody: the key is `/etc/mal-probe/probe-wallet.json`, root:root 0400, in `/etc/mal-probe` (root 0700). The executor never opens that path. The live drop-in has `LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json`, systemd gives the service a private copy, and the executor loads ONLY `$CREDENTIALS_DIRECTORY/probe-wallet` (it refuses if `CREDENTIALS_DIRECTORY` is unset or the file is missing; there is no path override in live mode, and a `key_path` in the config is refused). The dry-run unit has no `LoadCredential`. State, fill log, STOP and HALT stay in `/var/lib/mal-live` (mal-live, 0700). Helm sets an auditd watch on `/etc/mal-probe`.

At start live mode sets RLIMIT_CORE 0 and PR_SET_DUMPABLE 0, then loads the credential. It refuses (exit, no key text in the message) if the credential is missing or malformed. Only the public key is printed.

Live state is `/var/lib/mal-live/state-live.json`, separate from the dry run's `state-dryrun.json`. Deleting it does not reset the budget: with live rows in the fill log and no state file, live refuses to start.

Back to dry run: `sudo rm /etc/systemd/system/mal-probe-executor.service.d/live.conf && sudo systemctl daemon-reload && sudo systemctl restart mal-probe-executor`.

## 2b. Live from a root-owned pinned copy (replaces live.conf)

`live.conf` runs code from `/var/lib/mal/fast-forward/src` with the ubuntu-owned venv, both writable by agents. The pinned variant runs from `/usr/local/lib/mal-probe-exec`, all `root:root` (dirs 0755, files 0644), same pattern as `/usr/local/lib/mal-probe` ([probe-wallet.md](probe-wallet.md)). Installed files: the import closure of `tools.probe_executor` and `tools.probe_live` inside `tools/` (8 modules; a test recomputes it), `launcher.py`, `probe-executor-live.json`, the hashed `requirements-probe-exec.txt`. Venv: `solders`, `jsonalias`, `typing_extensions` only, `--require-hashes --only-binary=:all: --no-deps` (hashes checked against pypi.org JSON on 2026-10-05, same as `requirements-probe-tools.txt`). The unit runs `python -I -B -u launcher.py`. `-I` ignores `PYTHONPATH` and the working directory, so the root-owned launcher puts only its own resolved directory on `sys.path` and refuses if `tools` came from anywhere else. The drop-in sets no `PYTHONPATH`.

Switch (Helm or the owner, as root, from a fresh root-owned clone detached at the reviewed merge commit):

```
# 1. wind down: STOP file, wait for 0 open and pending positions (--status, section 4), then stop the unit
sudo touch /var/lib/mal-live/STOP
sudo systemctl stop mal-probe-executor
# 1b. BEFORE the install (Helm, once): the root-only key dir. The installer refuses without it.
sudo install -d -m 0700 -o root -g root /etc/mal-probe-rpc
#     create /etc/mal-probe-rpc/helius.env as root:root 0600 with ONLY the HELIUS_API_KEY= line (key never printed)
# 2. install (refuses without the manifest argument, if /etc/mal-probe-rpc is not root:root 0700 with a root:root 0600 helius.env, if the unit is active, a clone dir is not root-owned, HEAD != sha, or a manifest entry mismatches)
sudo scripts/mal-fast/install-probe-executor-pinned.sh <40-char-sha> <manifest>   # prints sha256 of every installed file; compare with the manager's list
# 3. swap the drop-in, reload, remove STOP, start
sudo install -D -m 0644 scripts/mal-fast/mal-probe-executor-live-pinned.conf /etc/systemd/system/mal-probe-executor.service.d/live.conf
sudo systemctl daemon-reload
sudo rm -f /var/lib/mal-live/STOP
sudo systemctl start mal-probe-executor
journalctl -u mal-probe-executor -n 20 --no-pager   # expect: mode=LIVE user=<public key>
systemctl show mal-probe-executor -p ExecStart --value | grep -c fast-forward   # expect 0
```

The installer also writes the base unit `/etc/systemd/system/mal-probe-executor.service` (from the manifest-checked blob, staged as `.new` and moved into place with `daemon-reload` BEFORE the `current` pointer switch); it refuses any unit that is not identical (allowlist) to the intended one.

The unit must be stopped for the install because the venv is replaced. The new venv is built as `venv.<sha>.new` and swapped in only after the pip install and a smoke import both succeed, so a failed install leaves the old venv (and a rollback to an older pinned sha) intact; a failed run also removes its staging dirs. A restart with open positions resumes pending signatures without re-buying, but do the swap at 0 open positions.

Installer fix (2026-10-05): the first version's final check (`find ... ! -user root -o -perm /022`) matched every venv symlink (`bin/python`, `python3`, `python3.x`, `lib64`; lstat mode 0777), so it always exited with an error after moving `<sha>/` and the venv into place and before `current` existed; a rerun then refused because `<sha>/` existed. The check now lives in `scripts/mal-fast/check-probe-exec-tree.sh`: everything (symlinks included) must be root-owned, no non-symlink may be group/world-writable, and every symlink must resolve inside the checked dir or to the system python (`/usr/bin/python3*`, `/usr/lib/python3*`). It runs on the staged tree and staged venv BEFORE any move (a failure installs nothing) and again after the moves; if the post-move check or final smoke import fails, the installer rolls back (removes `<sha>/` and the new venv, restores `venv.old`) and, if the rollback itself fails, prints exactly what to remove by hand.

Recovering from a half-finished install made with the OLD installer (as root, unit stopped): `rm -rf /usr/local/lib/mal-probe-exec/<sha> /usr/local/lib/mal-probe-exec/venv.<sha>.new /usr/local/lib/mal-probe-exec/venv.<sha>`, then rerun the installer from a clone at the fixed commit. The old installer had already swapped `venv` in (and deleted `venv.old`) before failing; the rerun rebuilds `venv` from the hashed requirements, so nothing else needs fixing. `current` never existed for that sha.

Before switching live to pinned: Helm runs the pinned launcher once KEYLESS in dry-run mode on fast-0, to prove the pinned path and the edited `tools/pumpswap_simulate.py` run on the real host. The dry-run config is copied with a scratch state dir so the real `/var/lib/mal-live` state and fill log are not touched; no wallet key is involved (dry-run mode holds none). The Helius env file is passed via `EnvironmentFile` (as the real unit does) because the executor needs the RPC URL; systemd reads it as root, so `mal-live` need not read it, and the scratch dir is a fresh `mktemp -d` under `/run`, not a predictable path. Expect the launcher to start, print its dry-run mode line, and run until the 90 s limit without a traceback:

```
D=$(sudo mktemp -d /run/pinned-dry.XXXXXX) && sudo chown mal-live:mal-live "$D"
sudo sed -e "s#\"state_dir\": *\"[^\"]*\"#\"state_dir\": \"$D\"#" \
         -e "s#\"fill_log\": *\"[^\"]*\"#\"fill_log\": \"$D/probe-fills.jsonl\"#" \
         -e "s#\"stop_file\": *\"[^\"]*\"#\"stop_file\": \"$D/STOP\"#" \
         scripts/mal-fast/probe-executor.json | sudo -u mal-live tee "$D/dry.json" >/dev/null
sudo systemd-run --wait --collect --pipe -p RuntimeMaxSec=90 -p User=mal-live -p NoNewPrivileges=yes -p ProtectSystem=strict -p ReadWritePaths="$D" \
  -p EnvironmentFile=/etc/mal-probe-rpc/helius.env \
  /usr/local/lib/mal-probe-exec/venv/bin/python -I -B -u /usr/local/lib/mal-probe-exec/current/launcher.py --config "$D/dry.json"
sudo rm -rf "$D"
```

systemd-run reporting that the unit hit its 90 s runtime limit (result `timeout`) with no traceback is a pass (`RuntimeMaxSec` stops the unit itself); an import error or a `tools` path refusal is a fail, so do not switch.

Update to a new commit: same steps. Each `<sha>` dir is immutable and the installer refuses to overwrite one; `current` moves only after a smoke import from the final location succeeds.

Rollback to the old agent-deployed path: stop the unit, `sudo install -D -m 0644 scripts/mal-fast/mal-probe-executor-live.conf /etc/systemd/system/mal-probe-executor.service.d/live.conf`, daemon-reload, start. Rollback to an earlier pinned commit: stop, then `sudo ln -s <old-sha> /usr/local/lib/mal-probe-exec/.current.tmp && sudo mv -T /usr/local/lib/mal-probe-exec/.current.tmp /usr/local/lib/mal-probe-exec/current`, start (the venv is shared; if requirements changed between the two commits, remove that sha's dir and re-run the installer for it instead). Back to dry run: remove the drop-in as in section 2.

Status in pinned mode (local files only, no key; run as root or `mal-live`):

```
sudo /usr/local/lib/mal-probe-exec/venv/bin/python -I -B -u /usr/local/lib/mal-probe-exec/current/launcher.py --config /usr/local/lib/mal-probe-exec/current/probe-executor-live.json --status
```

Helm provisions once, before live: `/etc/mal-probe-rpc` (`root:root`, mode 0700) holding `helius.env` (`root:root`, mode 0600, only the `HELIUS_API_KEY=...` line). The base unit loads that file as its `EnvironmentFile`, never `/var/lib/mal/fast-listener/helius.env`, because that one is `ubuntu:ubuntu` and the paper side must keep writing it: systemd reads an `EnvironmentFile` as root, so any ubuntu-writable file there could inject `LD_PRELOAD` or other variables into the key-holding process. Two checks keep it that way. A root-run inline `ExecStartPre=+/usr/bin/env -i /bin/sh -c ...` (no file-based code; `env -i` and absolute paths because `EnvironmentFile` also applies to the `+` command, so a `PATH=` or `LD_PRELOAD=` in that file could otherwise redirect `stat`; `+` lifts the unit's sandbox for that one command, so `SystemCallFilter`, the empty `CapabilityBoundingSet=` and `NoNewPrivileges` do not apply to it) tests the dir is `0:0:700` and the file `0:0:600`. The executor itself refuses live start with `ALERT startup_refused rpc_env ...` (exit 2) before loading the key if `/etc/mal-probe-rpc` is not a root:root 0700 directory (it can stat the dir but not the 0700 file inside, hence the root check for the file); in dry-run it only warns. Live also refuses with `ALERT startup_refused rpc_key_missing` (exit 2, before the wallet key loads) if `HELIUS_API_KEY` is not in the environment, and in live mode no env-file fallback is ever read (dry-run keeps the old fallback).

`install-fast-forward-paper.sh` refuses to run (every mode except `--dry-run`) while a NON-pinned live drop-in (`live.conf` whose ExecStart runs `${FWD}/src`) exists, so a routine reinstall cannot swap code or units under a live key-holder. A pinned drop-in (`live-pinned.conf`, or the pinned conf installed as `live.conf`: its ExecStart runs `/usr/local/lib/mal-probe-exec/current/launcher.py`) does not block the runner reinstall; the installer prints a note, because the pinned code and config are root-owned and change only through `install-probe-executor-pinned.sh`. The pinned drop-in inherits `User=`, `Environment=`, `EnvironmentFile=` and all hardening from the base unit, so base-unit changes for the probe go only through `install-probe-executor-pinned.sh` (from a root clone, with the MANDATORY manifest, which includes `scripts/mal-fast/mal-probe-executor.service` and `scripts/mal-fast/check-probe-base-unit.py`) and a Helm restart. The installer refuses without a manifest. It runs the manifest-verified allowlist checker on the unit text (the unit must equal the intended unit line for line) before moving anything, installs the unit (staged as `.new`, previous kept as `.old`, `daemon-reload`) BEFORE switching `current`, and rollback restores the previous unit. INT, TERM and HUP between the point of no return and the pointer switch run the same rollback (the traps are cleared right after the pointer switch; after that nothing is half done). A SIGKILL or power loss cannot be trapped: if it happens, check `current`, the `.old`/`.new` unit files and `venv.old` by hand, as the "rollback FAILED" message lists. The drop-in scan covers every drop-in dir name systemd merges for the unit (`mal-probe-executor.service.d`, `mal-probe-executor-.service.d`, `mal-probe-.service.d`, `mal-.service.d`, top-level `service.d`) under system.control, transient, `/etc`, `/run`, `/usr/local/lib`, `/usr/lib`, `/lib` and the generator dirs; documented gap: a drop-in reachable only through a unit alias or another unit that pulls this one in is not scanned (the installer cannot see it); verdict `none` means none of them sets `LoadCredential` or an `ExecStart` (the key only arrives via `LoadCredential`). While the pinned drop-in is present the paper installer skips `mal-probe-executor.service` and prints a NOTE if the installed file differs from the repo copy. Note the base unit's signals bind (`intents.jsonl`) and the pinned `probe-executor-live.json` (`signals_file`) must change together: re-pin after this change.

State, fill log, STOP/HALT, the credential and the signals bind are unchanged from the base unit. The dry-run unit still uses the agent-deployed path (it holds no key).

## 2b-dec020. Re-pin for the DEC-020 size step (0.25 SOL). NOT done until the owner approves after the 10-16 forward read

The code is in the pinned set but selected only by config: `limits_profile` is `dec019` when absent (today's limits, state file `state-live.json`, fill rows unchanged), or `dec020` (size 0.25 SOL, max open 2, 40 attempts, loss cap 0.35 SOL, 500,000 priority, 7 days, an end instant the owner sets in code (none yet); config can lower any of these, never raise). Any other value refuses at startup, before the key loads. A dec020 run:

- uses its own state file `state-live-dec020.json` (attempts and realized loss never pool with the probe's) and its own fill log `probe-fills-dec020.jsonl`, whose rows carry `"limits_profile":"dec020"`; the dec019 fill rows are byte-identical to before (no new key);
- on its first start copies the dec019 `bought` list (never re-buy) from `state-live.json`, read-only;
- refuses to start (every start) if `state-live.json` shows any open or pending position (DEC-020 3b);
- refuses while `DEC020_END_MS` in `tools/probe_executor.py` is `None` ("owner end instant not set in code; DEC-020 section 3 and 7"). There is no invented ceiling: the owner-approved end is written into that constant, and into the config's `end_ms`, in a reviewed commit, which makes a new sha. The config `end_ms` must be explicit and positive and is clamped to the constant. The shipped config has `"end_ms": 0` on purpose. Root-owned pinned files cannot be edited in place;
- reads the dec019 state only from the FIXED path `/var/lib/mal-live/state-live.json` (never from the config) and refuses if that file is missing. Its `state_dir` must be `/var/lib/mal-live`; its `fill_log` must not be a symlink, must resolve under `/var/lib/mal-live`, and must not be `probe-fills.jsonl`;
- all of these refusals run keyless in `main()` and at the top of `run_live`, before the wallet key is loaded. `--status` prints `limits_profile=...` and then `precheck=ok` or `precheck=REFUSED <reason>` as its last lines;
- **dec020-only automatic stops** (`DEC020_STOPS`, DEC-018 Am.1; new buys only, exits and in-flight sells continue): `limit:divergence_entry` (after 10 closed trades, mean `entry_vs_quote_bps` below -200), `limit:divergence_exit` (same, mean `exit_vs_quote_bps` on landed sells below -200), `limit:landing_fail` (after 10 resolved buy attempts, more than 30% did not land: on-chain failure or expired). Negative bps is worse for both. Latched in `state-live-dec020.json` (`dec020` key) and shown by `--status` (`dec020_stops ...`). A buy that lands with zero tokens counts as a FAILED attempt for landing-fail and enters the entry mean at once as -10000 bps (and counts as a closed trade; there is no sell). A buy whose token balance was still pending (tokens are the quote estimate, so its bps would be a fake 0) is EXCLUDED from the entry mean; its close still counts toward the 10 and toward the exit mean, and `--status` shows the count as `entry_excluded_balance_pending`. Config `dec020_stops` may only be stricter. dec019 is unaffected. A latched stop is cleared only by a reviewed state edit by the manager with the owner told;
- **manager check, not executor:** every 5 dec020 trades the manager runs `tools/probe_sim_calibration.py`. If the live-sim P&L residual is below -0.0075 SOL per trade after 10 closed trades, the manager places the STOP file under DEC-018 Am.1 (owner-delegated stops) and tells the owner and Helm the same hour;
- rollback guard: the dec019 profile (live) refuses to start while `/var/lib/mal-live/state-live-dec020.json` shows any open or pending position, so a rollback cannot orphan dec020 positions.

Files added to the pinned set (installer `EXTRA`): `scripts/mal-fast/probe-executor-live-dec020.json` (installed as `<sha>/probe-executor-live-dec020.json`) and `scripts/mal-fast/mal-probe-executor-live-pinned-dec020.conf` (installed as `<sha>/mal-probe-executor-live-pinned-dec020.conf`, so its sha256 is verified). **Manifest: 13 lines become 15 (8 modules, 5 EXTRA, base unit, checker).** `tools/probe_executor.py` and `tools/probe_live.py` change hash. The drop-in differs from the current pinned drop-in only in `--config .../probe-executor-live-dec020.json`. `tools/probe_live.py` also gains a spend check in the pre-sign whitelist (see below). The installer's code path is unchanged; the current pin (`faa3192`) is not touched by merging this.

Steps for Helm, only after the 0.05 probe has ended, at 0 open and 0 pending (`--status` on the dec019 config shows `open=0/3 pending=0`):

```
# 1. STOP, confirm nothing open, stop the unit (as section 2b step 1)
sudo touch /var/lib/mal-live/STOP
sudo /usr/local/lib/mal-probe-exec/venv/bin/python -I -B -u /usr/local/lib/mal-probe-exec/current/launcher.py --config /usr/local/lib/mal-probe-exec/current/probe-executor-live.json --status
sudo systemctl stop mal-probe-executor
# 2. install the new sha with a 15-line manifest (as section 2b step 2). Nothing else about the installer changes.
sudo scripts/mal-fast/install-probe-executor-pinned.sh <40-char-sha> <manifest>
# 3. keyless check of the new config: it must REFUSE if end_ms is still 0, and print limits_profile=dec020 once the owner's end instant is in
sudo /usr/local/lib/mal-probe-exec/venv/bin/python -I -B -u /usr/local/lib/mal-probe-exec/current/launcher.py --config /usr/local/lib/mal-probe-exec/current/probe-executor-live-dec020.json --status
# 4. swap the drop-in to the dec020 one, remove STOP, start
sudo install -D -m 0644 /usr/local/lib/mal-probe-exec/<sha>/mal-probe-executor-live-pinned-dec020.conf /etc/systemd/system/mal-probe-executor.service.d/live.conf   # the verified root-owned copy, not a working tree
sudo systemctl daemon-reload
sudo rm -f /var/lib/mal-live/STOP
sudo systemctl start mal-probe-executor
journalctl -u mal-probe-executor -n 20 --no-pager   # expect: mode=LIVE ... profile='dec020' size_lamports=250000000
```

**Precondition for any switch between the two profiles, in either direction (including this rollback): 0 open and 0 pending in BOTH `state-live.json` and `state-live-dec020.json`** (read both with `--status`; the executor also refuses to start otherwise). Rollback to the dec019 drop-in: STOP, confirm that precondition, stop the unit, install `mal-probe-executor-live-pinned.conf` as `live.conf`, daemon-reload, start. The watchdog (`LOSS_ALERT_SOL`, DEC-020 section 7) must read `state-live-dec020.json` for the step. Review, security review, the replay check below and the owner's approval come first; none of this is installed by merging the code.

Spend check (all profiles, including the running dec019 probe): `validate_message` now also refuses before signing when the SOL moved by the buy's system transfer(s), or the swap instruction's spend field, exceeds `limits.size_lamports`. The margin is 0 (`probe_live.SPEND_MARGIN_LAMPORTS`), derived from `tx.buy_instructions`: with `exact_quote_in=True`, the only shape the executor builds, the wrap transfer equals the spend exactly and no rent or fee is added to it (ATA creates are ATA-program instructions). A tx shape that adds a transfer must raise that constant in a reviewed change. The 0.05 SOL buy passes unchanged (tested); 50,000,001 and the `exact_quote_in=False` shape refuse.

**Pre-deploy replay check (before the re-pin, by the manager or Helm, no key, nothing live):**

1. Dry-run replay, old sha vs new sha, same recorded `intents.jsonl`, the dec019 dry-run config for both, each into its own scratch state dir and fill log (as in the keyless pinned dry run above). Compare with `python3 scripts/mal-fast/compare_executor_fills.py OLD.jsonl NEW.jsonl`. It drops every `*_ms` key and `latency`, `signature`, `blockhash`, `lvbh` and the slot fields, md5s the remaining rows, and prints the names (never values) of any other field that differs. Expected: the same rows and identical md5 apart from the dropped timing and signature fields. The dry run reads live RPC pool state, so price fields can differ between two runs at different times; any such difference is reported and must be explained, not waved through. The new sha must add no key to dec019 rows (`limits_profile` appears only for dec020). The script has only been tested on synthetic rows; it has not been run on real fills.
2. `--status` diff on a COPY of `state-live.json` (copy it into a scratch state dir, point a copy of the dec019 config at it): old sha vs new sha. Expected difference: only the added `limits_profile=` and `precheck=` lines at the end.
3. A dec020 dry run (config `mode` dryrun, `limits_profile` dec020, scratch dirs, a temporary build where the end constant is set): size 0.25, own `state-dryrun-dec020.json`, rows tagged `limits_profile: dec020`. The shipped build must instead refuse with the end-instant message.

## 2c. Signals file: start order and what the executor does when it is missing

The executor reads `intents.jsonl` (config `signals_file`), which the RUNNER creates (`intents_file: true`). The unit's bind of that file is optional (`-`) so a rotation cannot wedge systemd, which also means a missing file is silent at the unit level. The executor therefore checks it itself:

- Start or restart the runner FIRST, then confirm `test -s /var/lib/mal/paper/fast-forward-paper/intents.jsonl` (non-empty, or at least `test -e`) before starting the executor. After a runner restart that recreated the file, restart the executor too (the bind pins an inode).
- Live: if the file does not exist or is not readable at startup, the executor prints `ALERT startup_refused ...` and exits 2 before loading the key. systemd restarts it, `NRestarts` rises, and the monitor alerts.
- Running: if the file disappears or stays absent, it prints `ALERT signals_file_missing path=...` at most once a minute (dry run: `WARNING`, same rate, never exits).
- The runner's intent write cannot stop the paper runner. A failed write is counted in `runner-status.json` as `intent_write_errors` and logged to stderr at most once a minute. A non-zero count means the executor may have missed signals.

### What intents change about the live-vs-paper comparison

With `signals_file: intents.jsonl` the live executor acts on every CEILING-ledger migrate decision at decision time, including mints the paper runner later skips (kill switch, missed slippage at its simulated fill time, no price). Live fills are therefore NOT a subset of paper fills. They are bounded by the executor's own limits, STOP/HALT files and `already_bought`. The DEC-019 section 7 comparison joins live vs paper by mint and reports both the matched set and the live-only set.

## 3. Stop

- Paper runner KILL file: NOT a reliable stop for live. The runner checks KILL before it writes an intent, so a KILL present at decision time already means no intent. `runner_kill: true` in an intent row (the executor refuses it with reason `runner_kill`) covers ONLY the race between the runner's risk check and the intent write. A KILL touched while an earlier intent is in flight does not reach the executor. The executor cannot see the KILL file itself (its unit hides the runner dir except `intents.jsonl`), so `/var/lib/mal-live/STOP` is the real stop: touch it to stop live buys.
- STOP: `sudo touch /var/lib/mal-live/STOP`. No new buys. Exits, sells and rebroadcasts of in-flight txs KEEP running, so positions do not strand. Normal way to wind the probe down.
- HALT (emergency): `sudo touch /var/lib/mal-live/HALT`. Freezes everything: no buys, no sells, no rebroadcasts (status polling only). Open positions stay open until the file is removed. Use only if something is wrong with the executor or the wallet.
- Hard: `sudo systemctl stop mal-probe-executor`. A restart resumes any pending signature without re-buying.
- Automatic: 30 buy attempts, 0.25 SOL realized loss, 4 days from the first attempt. Each halts new buys only; open positions are still sold.

## 4. Status

```
cd /var/lib/mal/fast-forward/src
/var/lib/mal/fast-forward/venv/bin/python -m tools.probe_executor --config scripts/mal-fast/probe-executor-live.json --status
```

Prints stop-file presence, open exposure (cost of open and in-flight buys; the DEC-019 loss cap counts realized loss only, so worst case is the 0.25 SOL cap plus the open positions, still under the 0.5 SOL deposit), attempts out of 30, realized SOL, open and pending positions, and fill-row counts by kind. It reads local files only: no key, no URL, no RPC. (Run as `mal-live` or root, since `/var/lib/mal-live` is 0700.)

## 5. What each halt means

| Log or skip reason | Meaning | Action |
| --- | --- | --- |
| `limit:stop_file` | STOP file exists | remove it to resume buys |
| `limit:halt_file` | HALT file exists (buys, sells and rebroadcasts frozen) | investigate, then remove it |
| `limit:max_attempts` | 30 buys attempted (counts every send, landed or not) | probe is done |
| `limit:loss_cap` | realized loss at or past 0.25 SOL | probe is done; review |
| `limit:max_days` | 4 days since first attempt | probe is done |
| `limit:divergence_entry`, `limit:divergence_exit`, `limit:landing_fail` | dec020 only: live fills worse than quote, or too many buys not landing (see 2b-dec020). Latched | STOP stays on, review the fill rows, tell the owner; exits keep running |
| `limit:max_open` | 3 positions open or in flight | normal |
| `limit:sell_stuck` and `ALERT sell_stuck` | a sell failed or expired 5 times (config `sell_retries`); buys halted, the sell retries every 30 s | look at the sell rows (`fail_class`); STOP and sell by hand if it persists |
| `ALERT sell_abandoned` | 10 sell attempts on one position all failed (cap, config can only lower it). No more sells for it and buys stay halted. Retries before that back off 30 s, 60 s, ... up to 10 min; every landed failed sell's fees are already in realized loss | sell by hand or accept; STOP the unit |
| `limit:clock_backwards` | the system clock stepped back more than 60 s since the highest reading (persisted) | fix the clock; buys resume once it is past the old reading |
| `already_bought` | the mint was attempted before (kept in state; never re-bought, whatever the tailer does) | normal |
| `ALERT unsafe_tx_refused` | the pre-signing whitelist refused a tx (foreign program id, wrong mint/quote/token program, non-derived vault, priority over cap, extra signer). Nothing was signed | investigate the RPC |
| `ALERT meta_malformed` | `getTransaction` returned something unparseable or for a different signature; retried every step, balance fallback after 60 s | check RPC |
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
