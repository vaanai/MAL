# HV4 C0 bridge on mal-fast-0: install pack

The `hv4_c0` profile of `tools/arena_live_bridge.py` replaced the generic T0 profile that [arena-live-bridge.md](arena-live-bridge.md) still describes. This page is what Helm installs so that one bridge instance per HV4 canary arm runs **DRY** first and, only at the owner's word, live. Pack files: `scripts/mal-fast/hv4-c0/` (instance unit, live drop-in, A3 carry units, config templates, hashed requirements, smoke fixture) and `scripts/ops/hv4-c0-dry-checklist.sh`. Tests: `tools/test_hv4_c0_install_pack.py`.

**Nothing here has run on a host.** No book has cleared the promotion gate. Nothing on this page is a profit claim. This page installs no key and no wallet. Live trading needs a recorded PASS read for the arm, the canary DEC, funding, and the owner's own OK (DEC-032 Am.1 item 3 is the standing C0 OK; funding and the install are not covered by it).

## 0. Status at this head, and what this pack does not do

- `LIVE_REFUSAL = "hv4_c0_incomplete"` still refuses every live start, and `start_refusal` still refuses every dry start except one on a root that carries the `SYNTHETIC_FIXTURE_ROOT` marker. So at today's code head the only start that works is the **synthetic** one (section 5). The units, configs and jobs below are for the install sha that comes after the removal diff (QP removal item 15, its own reviewed diff with a fresh QP pass).
- **Real rows only after the arm's recorded PASS** (QP-RULING-1025 R2.1): no process, dry or live, reads an HV4 decision row before that arm's read is recorded. The pre-read rehearsal (section 5) uses the synthetic bot `hv4-SYN-X` and reads no row.
- Owned by other builders and **not** in this pack: the gap relay and the `tapegaps.jsonl` carry (QP #969 P7-a..P7-e), the twin carry, the send-row fields (`last_valid_block_height`, the priority actually signed), the md5 orders-equal-rows proof, and the removal diff. This pack fixes only where their outputs must land.
- Open in this pack: section 12.

## 1. Findings that shaped the pack (each is checked by a test or the checklist)

| # | Finding | What the pack does |
| --- | --- | --- |
| F1 | The bridge refuses an A3 record that is not owned by root or by its own user (`a3_halt_problem`, `a3_record_owner_mode`). The A3 loop (`scripts/ops/a3-daily.sh`, a MiScusi job) writes as the job user, so the bridge user would latch `a3_pins_changed` on every start. | `mal-hv4-c0-a3-carry.{service,timer}` + `hv4-c0-a3-carry.sh`: a root oneshot copies `a3-applied.jsonl` FIRST, then `daily.jsonl`, into the root-owned `/var/lib/mal-hv4-c0/a3/`. The other order could read "guard ahead", a hard latch. The unit binds that whole directory read-only (DEC-032 Am.3 s8 item 4). |
| F2 | `Bridge.__init__` creates the write-once window file `/var/lib/mal-hv4-c0/window/<bot_id>.json` in DRY too (QP #965 N-C). A rehearsal with a real bot_id starts that arm's 14-day clock. | The window dir is root-owned and **read-only to the unit** (a deliberate deviation from "ReadWritePaths includes the window dir"). Root creates the file at the chosen first start (N-B). The rehearsal and the checklist use only `hv4-SYN-X`, and the checklist refuses a real arm id in rehearsal mode. |
| F3 | The reads gate demands `realpath(reads_dir) == /data/mal/arena/reads`, and `_alias_of_sealed` fails closed when `/data/mal/arena` does not exist. | On fast-0 root creates `/data/mal/arena/reads` (root 0755) and puts a root-owned 0444 byte copy of the arm's record there. The unit binds only that directory (`TemporaryFileSystem=/data:ro` + `BindReadOnlyPaths`). |
| F4 | In live, `stop_file` and `halt_file` are pinned to the wallet-wide `/var/lib/mal-live/STOP` and `HALT` (config cannot set them), and the parity tool writes STOP there on a halt. The unit cannot write that directory. | **Open, section 12 item 1.** The unit does get search-only access (an ACL, as C1-NF step 3b) so it can test the files. |
| F5 | The live start precheck reads the old probe's state files under `/var/lib/mal-live` (`h5_precheck`): untraversable or unreadable = fail closed. | Same ACL, once per host, live only: `setfacl -m u:mal-hv4-c0:--x /var/lib/mal-live`. |
| F6 | One uid per wallet (DEC-026 note 2026-10-10 F2): systemd makes `/run/credentials/<unit>/` readable by the unit's `User=`. | The pack ships `User=mal-hv4-c0`. **Two live instances need two users** (a per-instance drop-in with `User=`/`Group=` and owner changes on that instance's dirs). The manager decides before a second wallet exists. |
| F7 | The twin wrapper needs the paper ledger (research-0) and the trips file (fast-0). | Not solved here (twin carry). The output must land in `/var/lib/mal-hv4-c0/twins/<bot_id>/`. |

## 2. Paths

One bridge instance per `bot_id`; the instance name is the bot_id (`mal-hv4-c0-bridge@hv4-BIG-X`). `<bot>` below is the bot_id.

| Item | Path | Owner, mode | Notes |
| --- | --- | --- | --- |
| User | `mal-hv4-c0` | system user, `nologin`, no home | not `mal-live`, not `claude` |
| Pinned tree | `/opt/mal-arena-bridge/<sha>/`, symlink `current` | root, dirs 0755, files 0644 | manifest `/opt/mal-arena-bridge/<sha>.manifest` |
| Venv | `/opt/mal-arena-bridge/venv` | root | `requirements-hv4-c0-bridge.txt`, Python 3.12 |
| Configs | `/etc/mal-arena-live/hv4-c0-<bot>.dry.json`, `.live.json` | root:root 0644 | templates `hv4-c0.dry.json`, `hv4-c0.live.json` |
| Gate dir | `/etc/mal-arena-live/` | root:root 0755 | `TIER`, `LIVE_OK` live only |
| `TIER` | `/etc/mal-arena-live/TIER` | root:root 0644 | content exactly `T1`; live only; both layouts |
| `LIVE_OK` today | `/etc/mal-arena-live/LIVE_OK` | root:root 0644 | one line: the bot_id; live only |
| `LIVE_OK` after PR `claude/hv4-c0-start-gate` | `/etc/mal-arena-live/hv4-c0/<bot>/LIVE_OK` | root:root 0644, dir root:root 0755 | content exactly the bot_id |
| Owner confirmation today | `/etc/mal-hv4-c0/CANARY_DEC.md` | root:root 0644 | the code at the install sha decides how it is verified |
| Owner confirmation after that PR | `/etc/mal-hv4-c0/<bot>/CANARY_DEC.md` | root:root 0644, regular file | exactly one line `OWNER_CANARY_CONFIRMED: bot_id=<bot>`; Helm writes it only on the owner's word |
| Gap-clear review | `/etc/mal-hv4-c0/gap-clear/<bot>.json` | root:root 0644 | `{"bot_id": ..., "reviewed_gap_ms": N}`, N equal to the newest gap instant; written by the manager's review, never by the bridge |
| Window dir | `/var/lib/mal-hv4-c0/window/` | root:root 0755 | `<bot>.json` root 0444, one hard link |
| State | `/var/lib/mal-hv4-c0/state/<bot>/` | `mal-hv4-c0:claude` 3770 | the only writable dir besides parity |
| Parity | `/var/lib/mal-hv4-c0/parity/<bot>/` | `mal-hv4-c0:claude` 3770 | `trips.jsonl`, `attempts.jsonl`, `state.json`, `parity-result.json` |
| Twins | `/var/lib/mal-hv4-c0/twins/<bot>/` | `claude:claude` 0755 | `twins.jsonl` + `twins.jsonl.run.json`; read-only to the unit |
| Tie-out | `/var/lib/mal-hv4-c0/tieout/<bot>/` | `claude:claude` 0755 | `LEDGER-<day>.json`, `tieout-<day>.json`, `EXCLUDED-TRIPS-<day>.json`; read-only to the unit |
| Forwarded feed (config `root`) | `/var/lib/mal-arena-feed/hv4/<bot>/` | `claude:claude` 0755 | the puller's `--dst`; `decisions/<bot>/` and `transport/` under it; read-only to the unit |
| A3 carry | `/var/lib/mal-hv4-c0/a3/` | root:root 0755 | `daily.jsonl`, `a3-applied.jsonl` (root 0644 copies) |
| Reads copy | `/data/mal/arena/reads/<bot>.json` | dir root:root 0755, file root:root 0444 | byte copy; sha256 equals the read job's RECORD line |
| Wallet (live only) | `/etc/mal-hv4-c0-key/<bot>.json` | dir root 0700, file root 0400 | `LoadCredential=probe-wallet:` in the live drop-in; never printed |
| RPC key (live only) | `/var/lib/mal/fast-listener/helius.env` | existing file | `EnvironmentFile=` in the live drop-in; Helm checks only that a `HELIUS_API_KEY=` line exists (`grep -c`), never prints it |
| Wallet-wide STOP/HALT (live) | `/var/lib/mal-live/STOP`, `/var/lib/mal-live/HALT` | probe's dir | pinned in code; they stop this bridge too |
| STOP/HALT (dry) | `/var/lib/mal-hv4-c0/state/<bot>/STOP`, `HALT` | | set in `dry.json` |

Until PR `claude/hv4-c0-start-gate` merges, the code uses the single shared `LIVE_OK` and `CANARY_DEC.md` paths above, so only one HV4 canary can go live at a time. Everything else in the table is per bot already.

**Window file, N-B and N-C.** The window file is shared by dry and live of the same bot_id, and the 14-day cap counts from its `first_start_ms`. A dry start with a real bot_id starts that clock. In this pack the bridge cannot create it (read-only dir), so there is exactly one way to start the clock: Helm writes the file, at an instant the manager names, immediately before the first start of that arm (dry or live). `end_ms` in the config may not exceed that instant plus 14 days. A bridge start without the file refuses with `window_file_unwritable` (the text says MANUAL RECOVERY; here it only means "root has not created the file yet"). Never delete or recreate a window file to get a start. Recipe (the file is write-once; `ln` fails if it exists):

```
BOT=hv4-BIG-X
sudo sh -c 'umask 022; d=/var/lib/mal-hv4-c0/window; t="$d/.$1.tmp"; printf "{\"bot_id\": \"%s\", \"first_start_ms\": %s}\n" "$1" "$(date +%s%3N)" > "$t" && chmod 0444 "$t" && ln "$t" "$d/$1.json" && rm -f "$t"' sh "$BOT"
sudo stat -c '%U:%G %a %h %n' /var/lib/mal-hv4-c0/window/"$BOT".json     # expect root:root 444 1
```

## 3. Provision (Helm, root, once). No key is touched.

```
sudo useradd --system --no-create-home --shell /usr/sbin/nologin mal-hv4-c0
sudo install -d -m 0755 -o root -g root /opt/mal-arena-bridge /etc/mal-arena-live /etc/mal-hv4-c0 /etc/mal-hv4-c0/gap-clear
sudo install -d -m 0700 -o root -g root /etc/mal-hv4-c0-key                       # live only; stays empty for a dry install
sudo install -d -m 0755 -o root -g root /var/lib/mal-hv4-c0 /var/lib/mal-hv4-c0/window /var/lib/mal-hv4-c0/a3 /var/lib/mal-hv4-c0/state /var/lib/mal-hv4-c0/parity /var/lib/mal-hv4-c0/twins /var/lib/mal-hv4-c0/tieout
sudo install -d -m 0755 -o root -g root /var/lib/mal-arena-feed /var/lib/mal-arena-feed/hv4
sudo install -d -m 0755 -o root -g root /data/mal/arena /data/mal/arena/reads          # fast-0 only; the directory must exist for the reads gate (F3)
# rehearsal first: BOT=hv4-SYN-X. Real arms after their read: BOT=hv4-BIG-X, then hv4-CON-X.
for BOT in hv4-SYN-X; do
  sudo install -d -m 3770 -o mal-hv4-c0 -g claude /var/lib/mal-hv4-c0/state/$BOT /var/lib/mal-hv4-c0/parity/$BOT
  sudo install -d -m 0755 -o claude -g claude /var/lib/mal-hv4-c0/twins/$BOT /var/lib/mal-hv4-c0/tieout/$BOT /var/lib/mal-arena-feed/hv4/$BOT
done
sudo stat -c '%U:%G %a %n' /var/lib/mal-hv4-c0/window /var/lib/mal-hv4-c0/a3 /etc/mal-arena-live /etc/mal-hv4-c0 /etc/mal-hv4-c0-key /data/mal/arena/reads
#   expect: root:root 755 for all but /etc/mal-hv4-c0-key (root:root 700)
test ! -e /etc/mal-arena-live/LIVE_OK && test ! -e /etc/mal-arena-live/TIER && test -z "$(sudo ls -A /etc/mal-hv4-c0-key)" && echo "gate closed, no key"
```

## 4. Install the pinned tree, venv and units (Helm, root)

The manager builds the manifest in his own clone at the install sha (never in Helm's clone) and sends its `sha256sum`. `<sha>` is the full 40-hex install sha.

```
D=/opt/mal-arena-bridge; S=<sha>
cd /root && git clone https://github.com/vaanai/MAL /root/mal-bridge-src && git -C /root/mal-bridge-src checkout --detach $S
test "$(git -C /root/mal-bridge-src rev-parse HEAD)" = "$S" && test -z "$(git -C /root/mal-bridge-src status --porcelain --untracked-files=all --ignored)" && echo "sha ok, clean"
sudo install -d -m 0755 -o root -g root $D/$S
git -C /root/mal-bridge-src archive $S | sudo tar -x --no-same-owner -C $D/$S
sudo chown -R root:root $D/$S && sudo find $D/$S -type d -exec chmod 0755 {} + && sudo find $D/$S -type f -exec chmod 0644 {} +
(cd $D/$S && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) | diff - /root/hv4-c0-manifest.txt && echo "hashes match"   # any output: STOP
sudo install -m 0644 -o root -g root /root/hv4-c0-manifest.txt $D/$S.manifest
sudo ln -sfn $S $D/current && readlink $D/current
sudo env -i PATH=/usr/bin:/bin /usr/bin/python3 -I -m venv $D/venv
sudo env -i PATH=/usr/bin:/bin $D/venv/bin/python -I -m pip install --quiet --require-hashes --only-binary=:all: --no-deps --no-cache-dir --disable-pip-version-check -r $D/$S/scripts/mal-fast/hv4-c0/requirements-hv4-c0-bridge.txt
sudo $D/venv/bin/python -I -m pip freeze | sort       # expect exactly jsonalias==0.1.1, numpy==2.5.3, solders==0.29.0, typing_extensions==4.16.0
$D/venv/bin/python -V                                 # expect Python 3.12.x
P=$D/$S/scripts/mal-fast/hv4-c0
sudo install -m 0644 -o root -g root "$P/mal-hv4-c0-bridge@.service" /etc/systemd/system/mal-hv4-c0-bridge@.service
sudo install -m 0644 -o root -g root "$P/mal-hv4-c0-a3-carry.service" "$P/mal-hv4-c0-a3-carry.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/mal-hv4-c0-bridge@.service; echo "rc=$?"     # expect no output, rc=0
test ! -e /etc/systemd/system/mal-hv4-c0-bridge@.service.d && echo "no template drop-in"
```

Configs (per instance, root:root 0644, from the manager's values). Substitute every `<AT-EVENT:...>`; nothing else changes:

```
BOT=hv4-SYN-X; FUNDED=<AT-EVENT:funded_lamports>; END=<AT-EVENT:end_ms>; HOLD=<AT-EVENT:max_hold_ms>
sed -e "s#<AT-EVENT:bot_id>#$BOT#g" -e "s#\"<AT-EVENT:funded_lamports>\"#$FUNDED#g" -e "s#\"<AT-EVENT:end_ms>\"#$END#g" -e "s#\"<AT-EVENT:max_hold_ms>\"#$HOLD#g" \
  $P/hv4-c0.dry.json | sudo tee /etc/mal-arena-live/hv4-c0-$BOT.dry.json >/dev/null
sudo chown root:root /etc/mal-arena-live/hv4-c0-$BOT.dry.json && sudo chmod 0644 /etc/mal-arena-live/hv4-c0-$BOT.dry.json
grep -c AT-EVENT /etc/mal-arena-live/hv4-c0-$BOT.dry.json        # expect 0
```

| Placeholder | Source | Rule |
| --- | --- | --- |
| `bot_id` | the manager | `hv4-<CON or BIG>-<F or X>`; the L, P and R arms are refused by the code |
| `funded_lamports` | the owner's funding | integer, 350,000,000 to 1,000,000,000; the wallet floor is derived (funded minus 0.25 SOL, at least 0.05 SOL) |
| `end_ms` | the manager | integer; **at most the window file's `first_start_ms` plus 14 days**; the bridge's own cap also |
| `max_hold_ms` | the arm's own card (`tools/hv4_arms.py` `code_limits.max_hold_min` times 60,000) | the bridge's time stop is this plus 60 s |

For the rehearsal, `funded_lamports` is any in-range number, `end_ms` is `first_start_ms` plus 1 day, `max_hold_ms` is 7,200,000.

## 5. Pre-read keyless rehearsal (allowed now; reads no decision row, starts no real clock)

Root creates only the synthetic arm's window file, which is harmless and write-once (section 2 recipe with `BOT=hv4-SYN-X`). Then, as the bridge user:

```
sudo -u mal-hv4-c0 env HV4C0_BOT=hv4-SYN-X HV4C0_MODE=rehearsal sh /opt/mal-arena-bridge/current/scripts/ops/hv4-c0-dry-checklist.sh
```

It prints only paths, labels and PASS/FAIL, never contents. Expect `RESULT PASS`. Any FAIL: do not start anything, paste the output to the manager. It checks: not root; the rehearsal bot is `hv4-SYN-X` (a real arm id is refused: `real_arm_id_in_rehearsal`); the unit text has no credential, environment-file, `--live` or drop-in line and the window dir is not writable; the config is dry, `hv4_c0`, the frozen sha, only the profile's keys, absolute paths, root:root 0644; no `LIVE_OK`, `TIER` or owner-confirmation file (either layout); no readable key file; the tree is a 40-hex sha dir, root-owned, equal to its manifest; the venv holds exactly the four pins and imports the bridge closure; the gate dirs are root 0755; the synthetic window file; the **`--once` dry start on a SYNTHETIC marker root prints `mode=dry`** (a fixture built in a mktemp dir by `hv4-c0-smoke-fixture.py`, no real file read); A3 liveness (section 7); and, when `HV4C0_PARITY_RESULT` is set, the parity rule of section 11.

Then the unit itself (still synthetic: the instance's config points at a synthetic marker root, not the forwarded feed):

```
sudo systemctl start mal-hv4-c0-bridge@hv4-SYN-X
journalctl -u mal-hv4-c0-bridge@hv4-SYN-X -n 30 --no-pager      # first line: arena_live_bridge mode=dry bot=hv4-SYN-X ...
systemctl show mal-hv4-c0-bridge@hv4-SYN-X -p User -p PrivateNetwork -p ProtectSystem -p ReadWritePaths -p BindReadOnlyPaths -p NRestarts --no-pager
sudo systemctl stop mal-hv4-c0-bridge@hv4-SYN-X
```

The rehearsal config for the unit needs `root`, `reads_dir`, and the parity paths to point at synthetic dirs bound the same way; the manager provides that variant if he wants the unit (not only the checklist smoke) rehearsed. The checklist smoke is the required step.

## 6. Feeds (MiScusi jobs on mal-fast-0; the owner and the Console see only MiScusi jobs)

All of these are **jobs**, session `s_y1y4vFh5TCjy8A`, run under `sh`, under 2 GB, resumable where they checkpoint. fast-0 reaches research-0 with `ssh mal-research-0` (Cloudflare Access); research-0 has no credential to reach fast-0, so every stream is PULLED.

1. **Decision pull** (one job per arm, started only after that arm's read is recorded; `serve` refuses before that anyway). Phase 1 uses claude's existing ssh key, in the form `decision_forwarder measure` already proved (`run_measure`): a `--cmd-json` argv of `ssh` plus the remote command `SSH_ORIGINAL_COMMAND='{resume}' python3 -u <RESEARCH0_PINNED_INSTALL>/tools/decision_forwarder.py serve --from-env --root <HV4_ARENA_ROOT> --bot-id <bot> --reads-dir <READS_DIR_ON_RESEARCH0>`, and locally `python -m tools.decision_forwarder pull --cmd-json '<that argv>' --dst /var/lib/mal-arena-feed/hv4/<bot> --bot-id <bot>`. `--cmd-json` is documented as the test form; the manager decides whether phase 1 may use it. One puller per arm (an exclusive lock). `--dst` is outside the arena. Do not place copies or hard links of HV4 day files under any other root.
2. **Phase 2 (Helm):** a forced-command key (one `authorized_keys` line on research-0, `serve --from-env`) and the unit `pull --ssh-host mal-research-0 --dst /var/lib/mal-arena-feed/hv4/<bot> --bot-id <bot>`. Same pinned install as the bridge sha, with the md5 orders-equal-rows proof covering the forwarded log. Not part of this pack.
3. **Gap streams:** `transport/<bot>.gaps.jsonl` comes from the puller. `transport/<bot>.tapegaps.jsonl` comes from `tools/hv4_gap_relay.py` on research-0 and needs the same carry (QP #969 P7-e, other PR). Until that carry runs, precondition 7 is NOT met for tape gaps.
4. **Twin client:** `python -m tools.hv4_c0_twin --trips /var/lib/mal-hv4-c0/parity/<bot>/trips.jsonl --ledger-dir <paper ledger of the arm> --bot-id <bot> --out /var/lib/mal-hv4-c0/twins/<bot>/twins.jsonl --reads-dir /data/mal/arena/reads`, run in a loop; its first pass must write `twins.jsonl.run.json` before the first buy (the pins `1e611a5a...` config and the writer sha are checked by the bridge, latch `twin_pin_mismatch`). Where it runs (it needs trips from fast-0 and the paper ledger from research-0) is the twin-carry PR's design (F7).
5. **Daily tie-out:** `python -m tools.hv4_c0_tieout --fills <state>/probe/fills.jsonl --state <state>/bridge-state-live.json --out-dir /var/lib/mal-hv4-c0/tieout/<bot> --day <closed UTC day> --trips <parity>/trips.jsonl`, then the arena tie-out check into the same directory, once per UTC day after 00:30Z. The wrapper job script for HV4 is not in the repo yet (section 12).
6. **A3 loop** (`scripts/ops/a3-daily.sh`, existing MiScusi jobs) and the **A3 carry** (units, section 3 of the install): `sudo systemctl enable --now mal-hv4-c0-a3-carry.timer`.

## 7. A3 liveness acceptance (QP removal item 14, DEC-032 Am.3)

How the bridge gets `a3_record_file`: the A3 loop writes `daily.jsonl` and `a3-applied.jsonl` into `/home/claude/data/structure-monitor` (`A3_OUT`). The carry copies both into `/var/lib/mal-hv4-c0/a3/` (root-owned, F1), and every config sets `a3_record_file` to `/var/lib/mal-hv4-c0/a3/daily.jsonl`; the applied path is derived from the same directory.

Acceptance (the checklist runs all three; a live start needs them true):

1. The bridge's **own** `hv4_live_c0.a3_problem` returns `(None, None)` on the carried files: the record is under 36 h old, shows no halt flag other than `synthetic_share_high`, and the guard's newest line has the same `run_utc` with verdict `NONE` or `PASS`. The checklist prints `a3_bridge_check_clear` and the record's age in hours, or the latch or refusal label (`a3_record_stale`, `a3_guard_pending`, `a3_guard_unreadable`, `a3_guard:HALT`, ...). It prints no flag content.
2. `mal-hv4-c0-a3-carry.timer` is active (a dead carry shows up as a stale record after 36 h; the carry never refreshes a copy from a torn or empty source).
3. **The guard loop outlives the canary's `end_ms`** (DEC-032 Am.3 s7: the loop must cover the LATER canary end). The manager reads the loop chain from the MiScusi job list (never a sealed job's status) and passes the latest end of the chain as `HV4C0_A3_COVER_UNTIL_MS`; the checklist FAILs when it is not later than the config's `end_ms`, and in `install` mode a missing value is a FAIL. As the manager gave the facts on 2026-10-30: guard #1446 ends 2026-11-07T00:57Z and HV4's END is 2026-11-08T13:05Z, so **the chain does not cover HV4 yet**: its successor (#1447, `after` #1446) and the 48-hour rule of Am.3 s7 apply (loop job 3 submitted before the first HV4 send, the alert #1540 extended or given a successor, the dead-man chain likewise). Not looked up here; the manager records the real job ids and limits in the canary DEC's `FILL:a3_guard_source`.
4. `a3-books.json` and the arena registry stay intact while any canary runs (Am.3 s3 F3), and the A3 loop writes record and applied log into one directory.

## 8. Dry install of a real arm (only after that arm's recorded PASS)

1. The arm's read is recorded and the manager has the RECORD line. The manager stages the record bytes on fast-0 (outside `/data`, readable by Helm). Helm installs the copy as a new root-owned inode and compares:
   `sudo install -m 0444 -o root -g root /home/claude/stage/<bot>.json /data/mal/arena/reads/<bot>.json && sudo sha256sum /data/mal/arena/reads/<bot>.json` must equal the RECORD line's sha256. Never edit or replace it later: a rewrite, even with identical bytes, latches `read_record` for good (QP N9).
2. Per-bot dirs (section 3 loop with the real bot_id), the dry config (section 4), and the manager-named instant for the window file (section 2 recipe).
3. The puller job (section 6.1) is running and `transport/<bot>.status.json` shows `connected` before the bridge starts; otherwise every buy is refused `transport_stall:*`.
4. `sudo -u mal-hv4-c0 env HV4C0_BOT=<bot> HV4C0_MODE=install HV4C0_A3_COVER_UNTIL_MS=<ms> sh .../hv4-c0-dry-checklist.sh` expects `RESULT PASS`.
5. `sudo systemctl enable --now mal-hv4-c0-bridge@<bot>`. Journal first line `arena_live_bridge mode=dry bot=<bot> ...`; no `startup_refused`, no `ALERT latch=`. With the parity paths configured, a dry entry is refused until the twin writer's run record exists (`twin_gate:*`): that is the rehearsal of the chain, not a fault.

Dry checks at any time:

```
systemctl cat mal-hv4-c0-bridge@<bot> | grep -Eci '^[[:space:]]*(Load|Set|Import)Credential'                                  # expect 0
systemctl cat mal-hv4-c0-bridge@<bot> | grep -Eci '^[[:space:]]*(EnvironmentFile|PassEnvironment)[[:space:]]*='              # expect 0
systemctl cat mal-hv4-c0-bridge@<bot> | grep -c -- '--live'                                                                    # expect 0
test ! -e /etc/systemd/system/mal-hv4-c0-bridge@<bot>.service.d && echo "no drop-in"
sudo test ! -e /var/lib/mal-hv4-c0/state/<bot>/bridge-state-live.json && echo "no live state"
```

## 9. Live (the owner's own word, each step; none of it is part of a dry install)

Preconditions, all true and recorded in the canary DEC copy: the arm's recorded PASS read; QP's OK and a reviewer verdict on a code head from which `hv4_c0_incomplete` has been removed by its own reviewed diff; the DEC copy merged with the owner's confirmation; the A3 acceptance (section 7) including the guard chain past `end_ms`; the twin, tie-out and relay carries running; a watchdog alerting on the instance (not in this pack, section 12); funding.

Order (Helm, each step only on the owner's instruction):
1. Wallet: generate the arm's key as for the probe wallet (`docs/runbooks/probe-wallet.md`), root:root 0400 at `/etc/mal-hv4-c0-key/<bot>.json`; report the PUBLIC key only. The owner funds from his own address.
2. `sudo setfacl -m u:mal-hv4-c0:--x /var/lib/mal-live && getfacl -p /var/lib/mal-live` (once per host; if `setfacl` is missing, stop and tell the manager; do not `chmod` that directory).
3. `/etc/mal-arena-live/hv4-c0-<bot>.live.json` from `hv4-c0.live.json` with the same substitutions (`mode` is `live`; `rps` stays 2.0, the Helius plan is shared with the listener).
4. Live drop-in from the manifest-verified copy: `sudo install -D -m 0644 /opt/mal-arena-bridge/<sha>/scripts/mal-fast/hv4-c0/mal-hv4-c0-bridge-live-pinned.conf /etc/systemd/system/mal-hv4-c0-bridge@<bot>.service.d/live.conf && sudo systemctl daemon-reload`. Do not start. `grep -c '^HELIUS_API_KEY=' /var/lib/mal/fast-listener/helius.env` prints a count, never the line.
5. Gate files, root:root 0644, only on the owner's say-so: `/etc/mal-arena-live/TIER` containing exactly `T1`; the arm's `LIVE_OK` (one line, the bot_id; per-bot path after the start-gate PR); the owner-confirmation file with its one `OWNER_CANARY_CONFIRMED: bot_id=<bot>` line. `sudo stat -c '%U:%G %a %n'` each: `root:root 644`; parent dirs root 0755.
6. Report whether `/var/lib/mal-live/STOP` or `HALT` exist (they stop this bridge too); the manager decides in writing whether to remove them.
7. On the manager's explicit go: `sudo systemctl start mal-hv4-c0-bridge@<bot>`. The journal must show `mode=live` and no `startup_refused`.

Never in a live drop-in: a `User=` change for a second wallet without the owner change in F6, a `BindPaths` (writable), an edit of the base unit. Never print the wallet file, the Helius line, or a credentials directory.

## 10. Stop and kill

| Want | Do |
| --- | --- |
| No new buys, keep running | dry: `sudo -u claude touch /var/lib/mal-hv4-c0/state/<bot>/STOP` (group claude may add it; remove to resume). Live: the wallet-wide `/var/lib/mal-live/STOP` (stops every executor on that wallet dir), or remove the arm's `LIVE_OK` |
| Freeze everything | `HALT` in the same places |
| Stop the process | `sudo systemctl stop mal-hv4-c0-bridge@<bot>` (state is write-ahead; a restart replays nothing; open positions keep the time-stop and stop-loss backstops only while the bridge runs, so stop with positions open only for an emergency and then use the rescue path of the live-port runbook) |
| Take the instance out | `sudo systemctl disable --now mal-hv4-c0-bridge@<bot>`; keep `/var/lib/mal-hv4-c0/state/<bot>` for the manager's read |

A latch (`chain_break`, `read_record`, `feed_break`, `a3_pins_changed`, `stake_mismatch`, `tieout_mismatch`, ...) stays until the manager clears it with the bridge stopped; tell the manager, do not edit the state file. `feed_break` clears only after 10 gap-free minutes counted from the end of the gap AND the manager's root-owned review file naming exactly the newest gap instant.

## 11. Parity: rc 0 alone is never "no hard halt" (QP #970 ruling 1)

`arena_live_parity` exits 0 while a bridge hard latch (`stake_mismatch`, `tieout_mismatch`, `twin_pin_mismatch`, ...) is open. The parity result carries it in `unresolved_hard_halts`, and then `match` is false. **Nothing downstream may read the exit code alone.** Every check, watchdog and C0 read looks at `parity-result.json` for **`unresolved_hard_halts == []` AND `match` is true**; anything else is "parity not shown". `HV4C0_PARITY_RESULT=/var/lib/mal-hv4-c0/parity/<bot>/parity-result.json` makes the checklist judge exactly that, printing only a count of halts, never their names or any statistic. `match` is also false early in a canary (fewer eligible trips than the tool's minimum), which is "not shown yet", not a fault, but it is still not a pass.

## 12. Open (not done in this pack)

1. **Parity STOP in live (F4).** The parity tool writes the stop file it is given; in live that is `/var/lib/mal-live/STOP`, which this unit cannot write. The tool then returns its "STOP not written" code instead of 11, the `parity_hard_halt` latch is only set on 11, and a hard halt could stop blocking once its cause clears, without the manager's written review. This needs a code change (give the parity tool a per-instance stop file the unit can write, and read both), which is outside this pack. Recommended as an added removal precondition.
2. **Runner dead-man (QP item 14).** A host-side dead-man on the HV4 runner job and on SCAN-log `tick_timing` staleness (the runner is on research-0), and a C0 read that flags every backstop-closed trade during runner silence. Not built.
3. **Watchdog for the bridge instance** (as `mal-h5-arena-watch`): alerts on a latch, a stale feed status, a stale A3 carry, an unreadable parity result. Not built.
4. **HV4 daily tie-out job script** (export, fetch, check, one UTC day after 00:30Z) and the twin job's placement (F7).
5. **Second live instance** needs its own uid (F6) and the per-bot gate files of PR `claude/hv4-c0-start-gate`.
6. **#967 `write_status` cross-test** and the md5 orders-equal-rows replay on a synthetic log: other builders.
7. **Real-host verification.** The `systemd-analyze verify`, the sandbox pre-flight (as `docs/runbooks/h5-executor.md` Step 3b-3: writes to the state dir succeed; writes to the feed, twins, tie-out, A3, window, reads, `/etc` and `/var/lib/mal-live` are refused), `RuntimeDirectory`-free start, and the bind-mount creation under `TemporaryFileSystem=/data:ro` have not been run: no host was touched.

## 13. What the repo proves, and what it does not

- `python -m pytest -q tools/test_hv4_c0_install_pack.py` parses the unit, drop-in and configs and asserts the invariants (no credential directive in the base unit, the drop-in has exactly the credential, environment-file and network keys, the config keys equal the profile's keys and pass `Config.from_dict`, TIER is `T1`, paths absolute, binds read-only, only state and parity writable, the pins equal the H5 arena venv, the bridge closure imports only numpy and the solders set). It runs the checklist on fake trees, the A3 carry on temp dirs, and the synthetic `--once` dry start in-process with the window dir and the sealed prefix pointed at scratch dirs.
- Not measured: any real host, systemd behaviour, RPC, fill, latency, landing rate, a real decision log, a real A3 record, a real read record, any P&L. Nothing here is gate evidence.
