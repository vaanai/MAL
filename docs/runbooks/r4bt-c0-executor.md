# r4-b-T C0 executor and twin on mal-fast-0: keyless install pack and Helm pre-install checklist

This page is what Helm installs so that the r4-b-T C0 executor (`tools/r4bt_c0_executor.py`, bot_id `r4bt-a1`, W `17e3b05a7696900cda7fcf93e4af4008d8fa0184`, change_id `r4bt-a1-1107a`) and its paper twin writer (`scripts/mal-fast/r4bt-c0-twin.sh`) exist on mal-fast-0 **keyless and DRY** before the screen read. Sources: QP-RULING-1106-r4bt-c0 section 3 ("PR #1176: revive on a new branch") and section 8 (by 2026-11-12T12Z), DEC-032 Amendment 9. Revived from PR #1176 (r4b, EXP-027), which stays untouched.

Pack files:
- `scripts/mal-fast/r4bt-c0/`: executor unit, twin unit, parity service and timer, dry config template, `live.conf.example` (live drop-in, NOT installed by the keyless install) and `r4bt-c0.live.json.example`, hashed requirements, synthetic smoke fixture;
- `scripts/ops/r4bt-c0-dry-checklist.sh` and `scripts/ops/r4bt-c0-twin-sandbox-check.sh`;
- tests: `tools/test_r4bt_c0_install_pack.py`.

**Nothing here has run on a host.** No book has cleared the promotion gate. r4-b-T has no read. Nothing on this page is a profit claim; a C0 is an execution measurement and never gate evidence. This page installs no key and no wallet, and enables nothing.

## 0. What this pack is, and what it does not do

- **A DRY config can never send.** `tools/r4bt_c0_executor` (live port PR #1257, `LIVE_BUILT` True) sends only when the config says mode `live` AND `--live` is given AND every live gate holds before each buy. `run` with a DRY config and `--live`, or a live config without it, exits 2 (`live_flag_mode_mismatch`); a DRY config never takes a live port. The keyless install has no live config, no `--live`, no credential and no drop-in, so it cannot send. The live drop-in is section 3b.
- **Declog.** r4-b-T's decision host writes `$HOME/mal-data/r4b-dry/r4bt-a1-1107a/declog/decisions/r4b-exp027/` from its clock, so that directory is **non-empty by construction**. The executor unit binds the declog root read-only. **The unit is not enabled or started against it until DEC-032 Am.9 (a4)**: the screen PASS has QP's OK on the read's exact files AND the amendment has merged. Until then the only start is the checklist's synthetic smoke, in a mktemp dir with `SYNmint*` rows. Nobody opens, lists or `cat`s a file inside the declog (Am.27 item 2; seals). The checklist is **names only**.
- **Executor CLI.** `python -m tools.r4bt_c0_executor run --config <config> [--live] [--once]` is the run loop; the bare `--config <file>` form is a config check (one JSON line, exit 0 or 2). The executor unit is `Type=simple` and runs the DRY config. A DRY run writes `health.dry.json` beside the config's `health_path`, never `health.json` (the watchdog's live file).
- **Twin files are root-owned.** The executor's twin gate accepts `twins.run.json` and `twins.jsonl` only when root-owned and not group/other writable, so the twin unit runs as root with an empty capability set (open question on the PR).
- **Not in this pack:** the live drop-in; the A3 carry job (a root-owned file from the research-0 monitor); the read-record file (written by the manager at the go); the watchdog entries; the daily tie-out; the wallet.

## 1. Paths

| Item | Path | Owner, mode | Notes |
| --- | --- | --- | --- |
| User | `mal-r4b-c0` | system user, `nologin`, no home | its own uid: one uid per wallet (DEC-026 note F2) |
| Pinned tree | `/opt/mal-r4b-c0/<sha>/`, symlink `current` | root, dirs 0755, files 0644 | manifest `/opt/mal-r4b-c0/<sha>.manifest`. **Must include the git object store** (section 3): the twin verifies every loaded `tools.*` module against its blob at W |
| Venv | `/opt/mal-r4b-c0/venv` | root | `requirements-r4bt-c0-executor.txt` (the same four pins as the HV4 bridge venv), Python 3.12 |
| Config | `/etc/mal-r4bt-c0/r4bt-c0.dry.json` | root:root 0644 | from `r4bt-c0.dry.json`; every `<AT-EVENT:...>` substituted |
| Gate dir | `/etc/mal-r4bt-c0/` and `/etc/mal-r4bt-c0/r4bt-a1/` | root:root 0755 | keyless: no `CANARY_DEC.md`, no `read-record.json`. `a3-carry.json` appears when the monitor job writes it |
| Per-bot live gates | `/etc/mal-arena-live/r4bt-c0/r4bt-a1/{LIVE_OK,TIER}` | root | keyless: no `LIVE_OK`. `TIER`, if present, reads exactly `T0` |
| Key dir | `/etc/mal-r4bt-c0-key/` | root:root 0700 | empty; live only, later |
| State | `/var/lib/mal-r4b-c0/state/` | `mal-r4b-c0:mal-r4b-c0` 0750 | executor and parity writable: order ledger, attempts, STOP, HALT. **Not group `claude`**: agent sessions must not read the live order ledger or parity state (SEALS, R4BT C0 TWIN READER). Root reads it by ACL `u:root:rX` (default ACL too) |
| Twin dir | `/var/lib/mal-r4b-c0/twin/` | `root:root` 0755, files 0644 | the twin unit's only writable dir: `twins.jsonl`, `twins.run.json`. Read-only to the executor and parity units (config `twin_dir`, parity `--twin-dir`), so the executor cannot forge twin files |
| Declog (bind source) | `/home/claude/mal-data/r4b-dry/r4bt-a1-1107a/declog` | the decision host's | read-only bind into the executor unit only. Written by the decision host, not by Helm |
| Derived tape copy | `/home/claude/mal-data/arena-tipfeed/tape` | the receiver's | read-only bind into the twin unit only |

## 2. Helm keyless pre-install checklist (root; no key is touched)

Do these in order. Record each result (path, uid, mode, sha) in the install record. Everything is done by **2026-11-12T12Z** (QP-RULING-1106-r4bt-c0 section 8).

1. **User.** `sudo useradd --system --no-create-home --shell /usr/sbin/nologin mal-r4b-c0`.
2. **Dirs.**
   ```
   sudo install -d -m 0755 -o root -g root /opt/mal-r4b-c0 /etc/mal-r4bt-c0 /etc/mal-r4bt-c0/r4bt-a1 /var/lib/mal-r4b-c0
   sudo install -d -m 0755 -o root -g root /etc/mal-arena-live/r4bt-c0 /etc/mal-arena-live/r4bt-c0/r4bt-a1
   sudo install -d -m 0700 -o root -g root /etc/mal-r4bt-c0-key
   sudo install -d -m 0750 -o mal-r4b-c0 -g mal-r4b-c0 /var/lib/mal-r4b-c0/state
   sudo install -d -m 0755 -o root -g root /var/lib/mal-r4b-c0/twin /var/lib/mal-r4b-c0/twin-live
   sudo install -d -m 0750 -o mal-r4b-c0 -g mal-r4b-c0 /var/lib/mal-r4b-c0/state-live      # the LIVE state dir: same owner and mode as state; DRY and live never share a state or twin dir
   sudo setfacl -m u:root:rX /var/lib/mal-r4b-c0/state-live && sudo setfacl -d -m u:root:rX /var/lib/mal-r4b-c0/state-live   # the live twin (capability-less root) reads the live ledger and attempts
   sudo install -d -m 0755 -o mal-r4b-c0 -g mal-r4b-c0 /var/lib/mal-r4b-c0/health
   sudo ln -s health/health.json /var/lib/mal-r4b-c0/health.json && sudo ln -s health/health.dry.json /var/lib/mal-r4b-c0/health.dry.json   # the watchdog's default paths
   sudo setfacl -m u:root:rX /var/lib/mal-r4b-c0/state && sudo setfacl -d -m u:root:rX /var/lib/mal-r4b-c0/state   # the twin (capability-less root) reads the ledger and attempts
   sudo stat -c '%U:%G %a %n' /opt/mal-r4b-c0 /etc/mal-r4bt-c0 /etc/mal-r4bt-c0-key /var/lib/mal-r4b-c0/state /var/lib/mal-r4b-c0/twin /var/lib/mal-r4b-c0/state-live /var/lib/mal-r4b-c0/twin-live
   #   expect: root:root 755, root:root 755, root:root 700, mal-r4b-c0:mal-r4b-c0 750, root:root 755, mal-r4b-c0:mal-r4b-c0 750, root:root 755
   test -z "$(sudo ls -A /etc/mal-r4bt-c0/r4bt-a1)" && test -z "$(sudo ls -A /etc/mal-r4bt-c0-key)" && echo "gate closed, no key"
   ```
   The per-bot `TIER` file (content exactly `T0`, root-owned) is created at the manager's go, not now. `LIVE_OK` and `CANARY_DEC.md` are never created in a keyless install.
3. **Tree with the git object store.** Section 3.
4. **Venv.** Section 3.
5. **Units and config.** Section 3. Do **not** enable or start either unit.
6. **Manifest.** Section 3 (`/opt/mal-r4b-c0/<sha>.manifest`, equal to the tree).
7. **Read ACL on the derived copy, for the twin.** The twin unit runs as root with an empty capability set, so it needs a named ACL entry on the tape dir (read and traverse only; no write). Record the `getfacl` output.
   ```
   T=/home/claude/mal-data/arena-tipfeed
   sudo setfacl -m u:root:rX $T
   # tape/ itself and everything under it EXCEPT .writer: recursive access ACL + default ACL on dirs (new tape files). .push/verified.json is replaced by rename, so .push gets the default too.
   sudo find $T/tape -path $T/tape/.writer -prune -o -exec setfacl -m u:root:rX {} +
   # Default ACLs OVERRIDE the writer's umask for new files. So the defaults restate the base entries explicitly and cap them with a mask, and nothing is widened: <g> and <o> are the
   # symbolic g and o bits of the dir's CURRENT mode (e.g. `stat -c %A $T/tape`), mask r-x. Record `stat -c '%a %n'` of one tape file and of .push/verified.json BEFORE, and of a NEW file after the next hour rolls: same modes, or STOP.
   # Chosen over a re-applying timer: a timer leaves new files unreadable for up to its period and adds a second root job on the derived copy; explicit defaults are static and checkable.
   sudo find $T/tape -path $T/tape/.writer -prune -o -type d -exec setfacl -d -m u:root:rX,g::<g>,o::<o>,m::r-x {} +
   ls -A $T/tape/.push        # NAMES ONLY: expect exactly this set (tools/arena_tip_push.py, the pusher's out dir): events.jsonl lock status.json verified.json, plus the fixed atomic-write temps verified.json.tmp and status.json.tmp (write_json_atomic)
   #   any other name: STOP and ask the manager before the grants. None of the four is sealed.
   # .writer: NO default ACL and no recursive grant. Access ACL on the dir and on counts.jsonl only (the writer appends to counts.jsonl in place, same inode).
   # state.json is replaced by rename on every save, so a deny on it would be lost and a default ACL would make the new file readable: the twin gets no entry for it.
   sudo setfacl -k $T/tape/.writer && sudo setfacl -m u:root:rX $T/tape/.writer $T/tape/.writer/counts.jsonl
   sudo getfacl -p $T/tape $T/tape/.writer $T/tape/.push $T/tape/.writer/counts.jsonl   # .writer must show no default: entries
   sudo sh /opt/mal-r4b-c0/current/scripts/ops/r4bt-c0-twin-sandbox-check.sh      # PASS lines from INSIDE the twin unit's sandbox (systemd-run, same properties); names only
   ```
   If the manager names another uid for the twin, the executor's root-owner rule changes first and this ACL names that uid. The twin never reads the live tip dir, the archive, `.writer/state.json` or the declog. If `counts.jsonl` is ever recreated by the writer, the twin refuses (fails closed) and the sandbox check shows it: re-grant the access ACL on the new file.
8. **Keep-verified dependency (>= 400 h).** The derived copy's writer run must keep verified hours **>= 400 h** (`--keep-verified-h 400`, the successor of the derived-copy writer, DEC-031 Am.27 item 25). Retention deletes closed hours; the twin refuses with `hour_deleted_by_retention` when the oldest present hour is later than the first hour the C0 needs. After the tree is installed, the manager runs `r4bt-c0-twin.sh --check-tape` as the twin's uid (prints the oldest present hour name against the needed first hour; names only). Helm records that the writer is configured with 400 h or more; the C0 does not start otherwise.
9. **Declog bind source exists** (names only): `test -d /home/claude/mal-data/r4b-dry/r4bt-a1-1107a/declog/decisions/r4b-exp027 && echo present`. Do not list its contents. The decision host creates it; if it is absent, the executor unit's start fails (no `-` prefix on the bind), which is the intended fail-closed.
10. **Run the checklist** (section 4).
11. **Measure the parity run time (QP item 8), before the parity timer is ever enabled.** In the DRY smoke on mal-fast-0, on a synthetic ledger and twins file at the full C0 size (14 days x 30 attempts), run `tools/r4bt_c0_parity` repeatedly as `mal-r4b-c0` and record p50 and p99 wall time. If 45 s + p99 > 55 s, lower `OnUnitActiveSec` in `mal-r4bt-c0-parity.timer` in a reviewed change before the start. Record the measurement and the final cadence in `FILL:c0_constants`. Never enable the timer before this is done and Am.9 (a4) holds.

## 3. Tree, venv, units (Helm, root)

The manager builds the manifest in his own clone at the install sha, over the **tracked tree only, `.git` excluded** (`cd clone && find . -path ./.git -prune -o -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum`). Two clones never agree byte for byte inside `.git` (index, logs, packs), so `.git` is never hashed; it is checked by content address instead (W's commit is in the installed object store; the twin verifies the blobs it loads). `<sha>` is the full 40-hex sha of the merge that carries this pack (and #1246 and the twin, which are already on main by then).

```
D=/opt/mal-r4b-c0; S=<sha>
git clone https://github.com/vaanai/MAL /root/mal-r4bt-src && git -C /root/mal-r4bt-src checkout --detach $S
test "$(git -C /root/mal-r4bt-src rev-parse HEAD)" = "$S" && test -z "$(git -C /root/mal-r4bt-src status --porcelain --untracked-files=all --ignored)" && echo "sha ok, clean"
sudo install -d -m 0755 -o root -g root $D/$S
git -C /root/mal-r4bt-src archive $S | sudo tar -x --no-same-owner -C $D/$S
# The twin needs the git object store (it verifies loaded modules against their blobs at W): copy the clone's .git into the tree, read-only for everyone but root.
sudo cp -a /root/mal-r4bt-src/.git $D/$S/.git
git -c safe.directory='*' --git-dir=$D/$S/.git cat-file -e 17e3b05a7696900cda7fcf93e4af4008d8fa0184^{commit} && echo "W is in the INSTALLED object store"
sudo chown -R root:root $D/$S && sudo find $D/$S -type d -exec chmod 0755 {} + && sudo find $D/$S -type f -exec chmod 0644 {} +
(cd $D/$S && find . -path ./.git -prune -o -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) | diff - /root/r4bt-c0-manifest.txt && echo "hashes match"   # any output: STOP
sudo install -m 0644 -o root -g root /root/r4bt-c0-manifest.txt $D/$S.manifest
sudo ln -sfn $S $D/current && readlink $D/current
sudo env -i PATH=/usr/bin:/bin /usr/bin/python3 -I -m venv $D/venv
sudo env -i PATH=/usr/bin:/bin $D/venv/bin/python -I -m pip install --quiet --require-hashes --only-binary=:all: --no-deps --no-cache-dir --disable-pip-version-check -r $D/$S/scripts/mal-fast/r4bt-c0/requirements-r4bt-c0-executor.txt
sudo $D/venv/bin/python -I -m pip freeze | sort      # expect exactly jsonalias==0.1.1, numpy==2.5.3, solders==0.29.0, typing_extensions==4.16.0
sudo install -m 0644 -o root -g root $D/$S/scripts/mal-fast/r4bt-c0/mal-r4bt-c0-executor.service /etc/systemd/system/mal-r4bt-c0-executor.service
sed -e 's#<AT-EVENT:feed_delay_slots>#<d>#' $D/$S/scripts/mal-fast/r4bt-c0/mal-r4bt-c0-twin.service | sudo tee /etc/systemd/system/mal-r4bt-c0-twin.service >/dev/null
sudo chmod 0644 /etc/systemd/system/mal-r4bt-c0-twin.service && grep -c AT-EVENT /etc/systemd/system/mal-r4bt-c0-twin.service   # expect 0
sudo install -m 0644 -o root -g root $D/$S/scripts/mal-fast/r4bt-c0/mal-r4bt-c0-parity.service $D/$S/scripts/mal-fast/r4bt-c0/mal-r4bt-c0-parity.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/mal-r4bt-c0-executor.service /etc/systemd/system/mal-r4bt-c0-twin.service /etc/systemd/system/mal-r4bt-c0-parity.service /etc/systemd/system/mal-r4bt-c0-parity.timer; echo "rc=$?"     # expect no output, rc=0
test ! -e /etc/systemd/system/mal-r4bt-c0-executor.service.d && test ! -e /etc/systemd/system/mal-r4bt-c0-twin.service.d && test ! -e /etc/systemd/system/mal-r4bt-c0-parity.service.d && echo "no drop-in"
```

`<d>` is the whole number of slots pinned in the registration record (`feed_delay_slots`); the manager gives it. The twin unit pins `tools/r4bt_c0_twin.py` by sha256 (a test fails if the tool changes without the unit). The wrapper also pins `tools/arena_paper.py` at `9d2fdb04…3514` and refuses on any other host.

The dry config is written with values that do not read any decision row. `frozen_sha` and `change_id` are literal in the template (W and `r4bt-a1-1107a`); `make_config` refuses anything else.

```
sed -e 's#"<AT-EVENT:end_ms>"#<end_ms>#' -e 's#"<AT-EVENT:slot_s>"#<slot_s>#' -e 's#"<AT-EVENT:funded_lamports>"#<funded>#g' \
    /opt/mal-r4b-c0/current/scripts/mal-fast/r4bt-c0/r4bt-c0.dry.json | sudo tee /etc/mal-r4bt-c0/r4bt-c0.dry.json >/dev/null
sudo chown root:root /etc/mal-r4bt-c0/r4bt-c0.dry.json && sudo chmod 0644 /etc/mal-r4bt-c0/r4bt-c0.dry.json && grep -c AT-EVENT /etc/mal-r4bt-c0/r4bt-c0.dry.json   # expect 0
```

| Placeholder | Source | Rule |
| --- | --- | --- |
| `end_ms` | the manager | integer ms. Keyless pre-install: any positive value, for example 1794700800000 (2026-11-15T00:00Z). At go it is set per Am.9 (at most 14 days from the first send, and no later than the host run end) |
| `slot_s` | the manager | provisional seconds per slot, in [0.1, 1.0]. For the pre-install use 0.4. At go, the decision host's per-day value |
| `funded_lamports` | the owner's funding | integer, 350,000,000 to 1,000,000,000. Pre-install: 350000000 |
| `feed_delay_slots` (twin unit) | the registration record | whole number of slots |

Fixed in the template (Am.9; DEC-032 section 3 C0 table; a change is refused by `make_config`, never clipped): stake 50,000,000 lamports, `max_open` 2, `max_trades_per_day` 30, `daily_loss_lamports` 100,000,000, `total_loss_lamports` 200,000,000; `send_fee_lamports` **505000** (505,000 lamports per send, the registered Am.9 section 8 figure; a lower value needs an explicit reviewed `fee_override`, Am.9 section 8; a higher one is always refused); the twin heartbeat bound `twin_heartbeat_max_ms` 900000 = 2 x `twin_interval_s` 300 + `twin_replay_s` 300 (the replay time is a declared placeholder until it is measured in the DRY smoke: if it is longer, raise `twin_replay_s` and the bound together in a reviewed change); per-bot `tier_path` `/etc/mal-arena-live/r4bt-c0/r4bt-a1/TIER` reading `T0`.

**After the go (manager only, not Helm's keyless step):** the manager adds `read_result_sha256` (64 hex) and `qp_comment_id` (digits) to the config, equal to the root-owned `/etc/mal-r4bt-c0/r4bt-a1/read-record.json` (`r4bt_read_record_v1`), and creates the per-bot `TIER` (`T0`). Without both, `make_config` refuses for EVERY root, synthetic ones included, so the keyless `/etc` config (which lacks them) never passes `--config`: that is the intended fail-closed, and the checklist's smoke uses a synthetic config that carries both keys. A start on a non-synthetic root is refused separately without the read record.

### 3a. Parity job, A3 carry, constants

- **Parity timer.** `mal-r4bt-c0-parity.timer` runs `mal-r4bt-c0-parity.service` every 45 s (30 to 60 s) as the **executor's user** `mal-r4b-c0`, keyless and with no network. It runs `python -m tools.r4bt_c0_parity --state-dir /var/lib/mal-r4b-c0/state --twin-dir /var/lib/mal-r4b-c0/twin --attempts /var/lib/mal-r4b-c0/state/r4bt-attempts.jsonl --now-ms <real clock>`; the clock is `$(date +%s) * 1000` read at each start, never a fixed value. It is also started after each fill (`systemctl start mal-r4bt-c0-parity.service`, by the manager or the run loop). Install both files like the executor unit; enable the timer only at the go.
- **A3 carry.** On research-0 `scripts/ops/r4bt-c0-a3-carry.sh` runs **hourly** as one MiScusi job (its header lists the env). Helm then copies the carry to the root-owned `a3_carry_path` on fast-0 **right after, at least hourly**: sha256 verified against the research-0 file, written to a temp name in the same dir and `rename`d atomically (no following of links), root:root 0644. **A missing, unreadable or invalid source on research-0 (no file, bad JSON, wrong schema or identity, stale `written_utc`) must REMOVE the fast-0 copy or replace it with a LATCHING carry** (verdict ERROR, `hard_halt` true): the copy step never leaves the previous good carry in place, never skips silently, and exits nonzero so the watchdog alerts. The 3 h `written_utc` bound is the backstop only, not the mechanism: if the whole copy job dies, the carry goes stale within 3 h and the executor latches `r4bt_a3`. The executor config sets `a3_carry_max_age_ms` **10800000** (3 h) on the carry's `written_utc`, leaving slack for one missed hour; stale latches `r4bt_a3`.
- **FILL:c0_constants** (each is stated in the confirmation read, none is tuned after seeing results): (1) twin replay time, `twin_replay_s`, a placeholder 300 s until it is measured in the DRY smoke; (2) twin heartbeat bound 900 s (`twin_heartbeat_max_ms` 900000 = 2 x 300 + 300); (3) parity cadence 45 s plus after each fill, and the measured parity run time (p50 and p99), both recorded before the timer is enabled (step 11); (4) `p_fail_press` 0.0 disclosure: the parity tool feeds `p_fail_press` 0.0 into P2, so the pressure term is zero and the flat `FLAT_FAIL` bound alone decides P2; (5) permission conditions: state dir 0750 or tighter owned by the executor user, twin files and carry root-owned and not group/other writable, gate dir root 0755, key dir root 0700.

### 3b. Run loop, restarts, and the LIVE drop-in

- **Restart policy.** `Restart=on-failure`, `RestartSec=30`, `StartLimitBurst=3`. A restart does **not** clear latches: they live in `<state_dir>/r4b-state-<mode>.json` and a restart only re-reads them. A start refusal (a pin, a gate, `other_mode_state_present`) exits nonzero and the start limit stops a refusal loop. Never delete the state file to "clear" something.
- **KeyboardInterrupt / SystemExit latch.** A latch written by a `KeyboardInterrupt` or `SystemExit` in the run loop must be **cleared by hand** by the manager after looking at why (the executor never self-clears it, and a systemd restart does not). Record the clearing in LAB_STATE.
- **`tieout_dir`.** The daily tie-out job (`tools/r4bt_c0_tieout.py`) reads a folder that holds **only tie-out outputs** (its own fills export and ledger copy, aggregates). Never point it at the state dir, the twin dir or the declog: those hold the live order ledger and sealed twin files that agents must not read before the confirmation read.
- **Health files.** The watchdog (user `claude`) must read them, and the state dir is `mal-r4b-c0:mal-r4b-c0` 0750, so they do NOT live in the state dir. Both configs set `health_path` to `/var/lib/mal-r4b-c0/health/health.json`: a dir `mal-r4b-c0:mal-r4b-c0` 0755 (Helm creates it, section 2) that is the executor's second `ReadWritePaths` entry. The watchdog should open `/var/lib/mal-r4b-c0/health/health.json` itself with `O_NOFOLLOW` and a regular-file/owner check (or `lstat` the resolved target), because the executor owns that dir and could swap the file for a symlink. The executor writes its file mode 0644 (tested in `test_r4bt_c0_live_port.py`) with only the pinned `HEALTH_KEYS`; DRY writes `health.dry.json` beside it, live writes `health.json`. To match the watchdog's default path `/var/lib/mal-r4b-c0/health.json`, Helm adds two root-owned symlinks in the root-owned parent: `ln -s health/health.json /var/lib/mal-r4b-c0/health.json` and `ln -s health/health.dry.json /var/lib/mal-r4b-c0/health.dry.json` (the executor never needs write access to the parent, so it cannot rename the twin or state dirs). The two modes never share a health file or a state dir. DRY state is read with `health.dry.json`.
- **Sandbox check after the first start of each mode.** After the first DRY start of the executor (never before the start gate, section 5), run `sudo sh /opt/mal-r4b-c0/current/scripts/ops/r4bt-c0-twin-sandbox-check.sh` again, so the ACLs are proven with the real files present, and confirm `health.dry.json` is fresh. After the live drop-ins are installed and the units restarted, run it for the live set: `sudo R4BTC0_TWIN_DROPIN=/etc/systemd/system/mal-r4bt-c0-twin.service.d/live.conf R4BTC0_STATE_DIR=/var/lib/mal-r4b-c0/state-live sh .../r4bt-c0-twin-sandbox-check.sh`.
- **LIVE set: three drop-ins, one switch.** `live.conf.example` (executor), `live-twin.conf.example` and `live-parity.conf.example` (companions) install together as `/etc/systemd/system/<unit>.service.d/live.conf`, because the live executor's gates read the LIVE state: the parity gate reads `state-live/r4bt-parity-state.json`, the twin must twin `state-live/r4bt-order-ledger.jsonl`, and the twin gate reads the live twin dir. The twin dir is **separate per mode** (`twin` for DRY, `twin-live` for live), so DRY-era `twins.jsonl` rows are never in the live gate's input and nothing needs rotating at the switch. `live.conf.example` is the only thing that hands the executor a key: `LoadCredential=probe-wallet:<Helm's wallet file>` (the name `tools.probe_live` reads, as in the probe and H5 arena live drop-ins; the source is `root:root 0400` in `/etc/mal-r4bt-c0-key`, a path Helm sets and no agent reads, prints or copies), `EnvironmentFile` for the RPC key (Helm's root-owned file), network on, `ExecStart` with the live config (`r4bt-c0.live.json`, from `r4bt-c0.live.json.example`, which sets no `stop_file`/`halt_file`: the executor refuses them in a live config) and `--live`. **Order, per DEC-032 Am.9 section 9 steps 5 and 6 (Helm acts; the manager restarts nothing on a host unit):** after the screen PASS with QP's OK, the amendment merged and the owner's yes (steps 1-4): (1) Helm installs the three drop-ins from the manifest-verified copy in the sha dir and runs `daemon-reload`, **no restart**; (2) Helm writes `LIVE_OK`, the per-bot `TIER` (`T0`) and the owner-confirmation line; (3) **Helm restarts** the executor, twin and parity units with their live drop-ins. A live start without those files exits 2 (`live_gate:*`) and `StartLimitBurst=3` stops it. The keyless checklist fails if any drop-in exists. Remove the drop-ins and have Helm restart to go back to DRY. **Helm confirms the RPC env file path in the drop-in** (`EnvironmentFile=`; copied from the probe lane; it must be the file Helm actually keeps, root-owned, outside any agent's reach) before installing it. Live still needs, before every buy, the config mode, `--live`, `LIVE_OK`, `TIER` T0, the owner line, the read record, a fresh A3 carry, the twin gate, the parity gate and the caps.

## 4. Keyless checklist

```
sudo -u mal-r4b-c0 env R4BTC0_MODE=preread sh /opt/mal-r4b-c0/current/scripts/ops/r4bt-c0-dry-checklist.sh
```

It prints only paths, labels and PASS/FAIL, never contents. Expect `RESULT PASS`. Any FAIL: start nothing and paste the output to the manager. It checks:
- it does not run as root;
- the unit text has no credential, environment-file, key-like environment, key or sealed bind, writable bind, `--live` or drop-in;
- `ExecStart` is the r4-b-T executor module, DRY, and the one bind is the change_id's declog root, read-only;
- `PrivateNetwork=true`; the only writable path is the state dir;
- the config is dry, `r4bt-a1`, W, `r4bt-a1-1107a`, the declog root of the change_id, all five C0 caps at or under the table, fee 505000 unless a `fee_override` is present, absolute paths, no placeholder, root:root 0644;
- no `LIVE_OK`, no `CANARY_DEC.md`; a `TIER` file, if present, reads `T0`; no `read-record.json` before the go (`postread` requires it);
- no key file is readable by this user;
- the tree is a root-owned 40-hex sha dir equal to its manifest, with W pinned, and the twin files are present;
- the venv holds exactly the four pins and imports the executor closure;
- the gate dir is root 0755, the key dir root 0700, the state dir 0750 or tighter and owned by the executor user;
- **declog, names only:** the bind source exists, and holds `decisions/r4b-exp027` (read-only to the executor by the unit's bind, which is checked on the unit text). The declog being non-empty is expected; no emptiness check exists and no name or content inside it is listed;
- the twin dir is root 0755 and not writable by this user; the state dir is owned by this user with group not `claude`; the twin's recursive read ACLs are proven by `r4bt-c0-twin-sandbox-check.sh` (root, from inside the twin unit's sandbox), not by this checklist;
- the synthetic smoke passes: the config check prints `"ok": true`; `run --live` on the DRY config exits 2 (`live_flag_mode_mismatch`); `run --once` prints `mode=dry` and writes `health.dry.json` in the mktemp dir;
- the unit is not enabled and not active.

`R4BTC0_MODE=postread` (after the go) additionally requires the read record (root 0644) and the two read keys in the config, and does not check unit state.

## 5. Start gate (manager's go only; not part of the keyless install)

Per DEC-032 Am.9 (a4) the executor is not started against the real declog before the screen PASS has QP's OK on the read's exact files AND the amendment has merged. At the go the manager:
1. writes the read record and the config keys (section 3), the A3 carry job runs, the per-bot `TIER` reads `T0`;
2. runs the `postread` checklist (expect `RESULT PASS`);
3. starts the twin unit, then waits for a fresh `twins.run.json` (the executor's twin gate refuses on a stale heartbeat), and re-runs the sandbox check after the executor's first DRY start (section 3b);
4. starts the executor unit. Its logs are names and counts only for marks before the confirmation read (Am.8 B1).

## 6. Stop

| Want | Do |
| --- | --- |
| No new would-buys | `<state_dir>/STOP`: DRY `sudo touch /var/lib/mal-r4b-c0/state/STOP`; **live** `sudo touch /var/lib/mal-r4b-c0/state-live/STOP` (remove it to resume). The live config sets no `stop_file`, so the live STOP is in `state-live`; touching the DRY dir does not stop live buys |
| Freeze | `<state_dir>/HALT` in the same place: DRY `/var/lib/mal-r4b-c0/state/HALT`; **live** `/var/lib/mal-r4b-c0/state-live/HALT` |
| Stop the twin | `sudo systemctl stop mal-r4bt-c0-twin` (the executor's twin gate then refuses buys when the heartbeat ages; in live mode the twin unit is the same unit with its live drop-in) |
| Take it out | `sudo systemctl disable --now mal-r4bt-c0-executor mal-r4bt-c0-twin`; keep the state dir for the manager |

## 7. Differences from PR #1176 (r4b C0, EXP-027)

- bind source `/var/lib/mal-r4b-c0/declog` -> `$HOME/mal-data/r4b-dry/r4bt-a1-1107a/declog` (item 6), read-only into the executor unit only;
- the `preread` "decision dir must be empty" check is gone (names only; the declog is non-empty by construction), and the unit must not start before Am.9 (a4);
- template and runbook pin W and the change_id instead of EXP-027 `frozen_sha` placeholders and the old long-DRY-host sha; the module is `tools.r4bt_c0_executor`, the bot is `r4bt-a1`;
- per-bot gate paths (`LIVE_OK`, `TIER` = `T0`, owner file, read record, A3 carry), the C0 caps and the 505000 fee are in the template and checked; the twin unit, the git-object-store requirement, the tape read ACL and the 400 h keep-verified dependency are new.

## 8. Live

The live port is built (PR #1257) and the drop-in is an example in this pack (section 3b), not installed. Live is the owner's call at the time: the screen PASS with QP's OK, the owner's yes, TIER `T0`, a dedicated wallet Helm makes (QP-RULING-1106-r4bt-c0 section 4), then Helm installs the drop-in. Nothing here funds, configures or starts a live executor; only the manager starts it.

No book has cleared the promotion gate.

## 9. Daily tie-out

The daily tie-out (DEC-032 section 6) reads the wallet and the chain, not the state or twin files. An aggregate it needs from the ledger goes through a root-run tool that prints aggregates only.
