# r4-b C0 executor on mal-fast-0: keyless install pack

This page is what Helm installs so that the r4-b executor (`tools/r4b_arena_executor.py`, bot_id `r4b-exp027`) exists on mal-fast-0 **keyless and DRY** before EXP-027's read. Once a read PASS and every DEC-032 precondition are in place, only the late steps remain. Those are the steps that cannot be done the same day; DEC-032 §0 says to do everything else early.

Pack files:
- `scripts/mal-fast/r4b-c0/`: unit, dry config template, hashed requirements, synthetic smoke fixture;
- `scripts/ops/r4b-c0-dry-checklist.sh`;
- tests: `tools/test_r4b_c0_install_pack.py`.

**Nothing here has run on a host.** No book has cleared the promotion gate. r4-b has zero out-of-sample trades. Nothing on this page is a profit claim. This page installs no key and no wallet, and enables nothing.

## 0. Status at this pack's sha, and what it does not do

- **`tools/r4b_arena_executor.LIVE_BUILT` is False.** `make_config` refuses any mode but `dry`, and `R4bBridge` refuses a live port. So no config, file or flag can make this tree send. The live path is a later, separately reviewed code change (section 7).
- **Before EXP-027's read is recorded, no r4-b decision row is readable here.**
  - The decision dir `/var/lib/mal-r4b-c0/declog` stays EMPTY.
  - The unit stays disabled and inactive.
  - The only start is the checklist's synthetic smoke, which runs in a mktemp dir with `SYNmint*` rows written by the pinned tree's own `tools.arena_declog`.
  - Source: EXP-027 §11, and DEC-032 Am.8 Erratum 4 item 7: "names and counts only until the read".
- **Conditional (manager, 2026-11-02).** EXP-027 §7.1 item 6 makes the read NOT_DECIDABLE if an A3 structure-monitor halt fires between W0 and E. QP-RULING-1102-r4b-a3-record is open on that.
  - The install is still worth doing: the same keyless provisioning (user, dirs, tree, venv, unit) serves any later DEC-032 canary on the r4-b engine.
  - The real-root steps (section 5) wait for a recorded PASS.
- **Not in this pack (owed; see the L3 report `/data/mal/hunt-1008/ops/reports/2026-11-02/L3-r4b-c0.md`):**
  - the r4-b twin writer and parity tool (DEC-032 §1 item 6, QP N7);
  - the daily tie-out job;
  - the A3 gate, read-record gate and owner-file gate in the executor;
  - per-bot LIVE_OK and TIER paths (today the bridge's default profile reads the shared `/etc/mal-arena-live/LIVE_OK` and `TIER`, which HV4 also uses);
  - the live drop-in;
  - a watchdog.

## 1. Paths

| Item | Path | Owner, mode | Notes |
| --- | --- | --- | --- |
| User | `mal-r4b-c0` | system user, `nologin`, no home | its own uid: one uid per wallet (DEC-026 note F2) |
| Pinned tree | `/opt/mal-r4b-c0/<sha>/`, symlink `current` | root, dirs 0755, files 0644 | manifest `/opt/mal-r4b-c0/<sha>.manifest`. Separate from `/opt/mal-arena-bridge` so that HV4's `current` is never moved |
| Venv | `/opt/mal-r4b-c0/venv` | root | `requirements-r4b-c0-executor.txt` (the same four pins as the HV4 bridge venv), Python 3.12 |
| Config | `/etc/mal-r4b-c0/r4b-c0.dry.json` | root:root 0644 | from `r4b-c0.dry.json`; every `<AT-EVENT:...>` substituted |
| Gate dir | `/etc/mal-r4b-c0/` | root:root 0755 | empty in a keyless install: no LIVE_OK, TIER or CANARY_DEC.md |
| Key dir | `/etc/mal-r4b-c0-key/` | root:root 0700 | empty; live only, later |
| State | `/var/lib/mal-r4b-c0/state/` | `mal-r4b-c0:claude` 3770 | the unit's only writable dir |
| Decision root | `/var/lib/mal-r4b-c0/declog/` | `claude:claude` 0755 | read-only bind for the unit; **empty until the read** |

## 2. Provision (Helm, root, once). No key is touched.

```
sudo useradd --system --no-create-home --shell /usr/sbin/nologin mal-r4b-c0
sudo install -d -m 0755 -o root -g root /opt/mal-r4b-c0 /etc/mal-r4b-c0 /var/lib/mal-r4b-c0
sudo install -d -m 0700 -o root -g root /etc/mal-r4b-c0-key
sudo install -d -m 3770 -o mal-r4b-c0 -g claude /var/lib/mal-r4b-c0/state
sudo install -d -m 0755 -o claude -g claude /var/lib/mal-r4b-c0/declog
sudo stat -c '%U:%G %a %n' /opt/mal-r4b-c0 /etc/mal-r4b-c0 /etc/mal-r4b-c0-key /var/lib/mal-r4b-c0/state /var/lib/mal-r4b-c0/declog
#   expect: root:root 755, root:root 755, root:root 700, mal-r4b-c0:claude 3770, claude:claude 755
test -z "$(sudo ls -A /etc/mal-r4b-c0)" && test -z "$(sudo ls -A /etc/mal-r4b-c0-key)" && test -z "$(ls -A /var/lib/mal-r4b-c0/declog)" && echo "gate closed, no key, no decision row"
```

## 3. Tree, venv, unit (Helm, root)

The manager builds the manifest in his own clone at the install sha and sends its `sha256sum`. `<sha>` is the full 40-hex sha of the merge that carries this pack.

```
D=/opt/mal-r4b-c0; S=<sha>
git clone https://github.com/vaanai/MAL /root/mal-r4b-src && git -C /root/mal-r4b-src checkout --detach $S
test "$(git -C /root/mal-r4b-src rev-parse HEAD)" = "$S" && test -z "$(git -C /root/mal-r4b-src status --porcelain --untracked-files=all --ignored)" && echo "sha ok, clean"
sudo install -d -m 0755 -o root -g root $D/$S
git -C /root/mal-r4b-src archive $S | sudo tar -x --no-same-owner -C $D/$S
sudo chown -R root:root $D/$S && sudo find $D/$S -type d -exec chmod 0755 {} + && sudo find $D/$S -type f -exec chmod 0644 {} +
(cd $D/$S && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) | diff - /root/r4b-c0-manifest.txt && echo "hashes match"   # any output: STOP
sudo install -m 0644 -o root -g root /root/r4b-c0-manifest.txt $D/$S.manifest
sudo ln -sfn $S $D/current && readlink $D/current
sudo env -i PATH=/usr/bin:/bin /usr/bin/python3 -I -m venv $D/venv
sudo env -i PATH=/usr/bin:/bin $D/venv/bin/python -I -m pip install --quiet --require-hashes --only-binary=:all: --no-deps --no-cache-dir --disable-pip-version-check -r $D/$S/scripts/mal-fast/r4b-c0/requirements-r4b-c0-executor.txt
sudo $D/venv/bin/python -I -m pip freeze | sort      # expect exactly jsonalias==0.1.1, numpy==2.5.3, solders==0.29.0, typing_extensions==4.16.0
sudo install -m 0644 -o root -g root $D/$S/scripts/mal-fast/r4b-c0/mal-r4b-c0-executor.service /etc/systemd/system/mal-r4b-c0-executor.service
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/mal-r4b-c0-executor.service; echo "rc=$?"     # expect no output, rc=0
test ! -e /etc/systemd/system/mal-r4b-c0-executor.service.d && echo "no drop-in"
```

Do **not** enable or start the unit.

The config file is written with values that do not read any decision row. `frozen_sha` is the decision host's install sha, which today is the long DRY host's `bc65c088793753954c86b82d0e5eff74aa038e0b`. A C0 host at another sha needs its own config line later.

```
sed -e 's#<AT-EVENT:frozen_sha>#bc65c088793753954c86b82d0e5eff74aa038e0b#' -e 's#"<AT-EVENT:end_ms>"#<end_ms>#' -e 's#"<AT-EVENT:slot_s>"#<slot_s>#' \
    -e 's#"<AT-EVENT:funded_lamports>"#<funded>#g' /opt/mal-r4b-c0/current/scripts/mal-fast/r4b-c0/r4b-c0.dry.json | sudo tee /etc/mal-r4b-c0/r4b-c0.dry.json >/dev/null
sudo chown root:root /etc/mal-r4b-c0/r4b-c0.dry.json && sudo chmod 0644 /etc/mal-r4b-c0/r4b-c0.dry.json && grep -c AT-EVENT /etc/mal-r4b-c0/r4b-c0.dry.json   # expect 0
```

| Placeholder | Source | Rule |
| --- | --- | --- |
| `frozen_sha` | the manager | the r4-b decision host's install sha. The host's `start` row repeats it; a mismatch latches `frozen_sha_mismatch` |
| `end_ms` | the manager | integer ms. Keyless pre-install: any positive value, for example 1794700800000 (2026-11-15T00:00Z). The real canary's end is set at go (at most 14 days from the first send) |
| `slot_s` | the manager | the provisional seconds per slot, in [0.1, 1.0]. For the pre-install use 0.4. At go, the decision host's per-day value |
| `funded_lamports` | the owner's funding | integer, 350,000,000 to 1,000,000,000. Pre-install: 350000000. The wallet floor is derived as funded − 0.25 SOL, never below 0.05 SOL |

## 4. Keyless checklist (pre-read; allowed now)

```
sudo -u mal-r4b-c0 env R4BC0_MODE=preread sh /opt/mal-r4b-c0/current/scripts/ops/r4b-c0-dry-checklist.sh
```

It prints only paths, labels and PASS/FAIL, never contents. Expect `RESULT PASS`. Any FAIL: start nothing and paste the output to the manager.

It checks:
- it does not run as root;
- the unit text has no credential, environment-file, key-like environment, key or sealed bind, writable bind, `--live` or drop-in;
- `ExecStart` is the executor's dry `run`;
- `PrivateNetwork=true`;
- the only writable path is the state dir;
- the config is dry, `r4b-exp027`, has a 40-hex sha and only the template's keys, uses absolute paths, has no placeholder left, and is root:root 0644;
- no r4-b LIVE_OK, TIER or owner file exists;
- no key file is readable by this user;
- the tree is a root-owned 40-hex sha dir equal to its manifest, with `LIVE_BUILT = False`;
- the venv holds exactly the four pins and imports the executor closure;
- the gate dir is root 0755, the key dir root 0700, the state dir 3770;
- the decision dir is not writable by this user and is **empty**;
- the synthetic smoke passes: `replay-check` gives `"ok":true`, and `run --once` prints `r4b_arena_executor mode=dry bot=r4b-exp027`;
- the unit is not enabled and not active.

## 5. After the read (manager's go only; not part of the keyless install)

1. EXP-027's read is recorded, with quant-proof's OK on its exact files. The A3-record ruling QP-RULING-1102-r4b-a3-record must also have allowed the read.
2. The decision host's root is bound into `/var/lib/mal-r4b-c0/declog` read-only. Either the C0 decision host writes there (`--declog-root /var/lib/mal-r4b-c0/declog`), or root bind-mounts the host's own root there. The manager names which. That decision comes from the C0-host ruling of the L3 report.
3. Run `sudo -u mal-r4b-c0 env R4BC0_MODE=postread sh .../r4b-c0-dry-checklist.sh`. Expect `RESULT PASS`.
4. Run `sudo systemctl start mal-r4b-c0-executor`. It must stay DRY for at least 1 h with 0 build errors (DEC-032 §2 checklist). Its logs are a derived copy of the decision log: names and counts only for marks from 2026-11-07T06Z until B (Am.8 B1).

## 6. Stop

| Want | Do |
| --- | --- |
| No new would-buys | `sudo -u claude touch /var/lib/mal-r4b-c0/state/STOP` (remove it to resume) |
| Freeze | `HALT` in the same place |
| Stop the process | `sudo systemctl stop mal-r4b-c0-executor` |
| Take it out | `sudo systemctl disable --now mal-r4b-c0-executor`; keep the state dir for the manager |

## 7. Live (NOT built; listed so that nobody improvises it)

Live needs a reviewed code change with its own QP pass, and none of the following exists at this pack's sha:
- `LIVE_BUILT` flipped behind a refusal constant that its own diff removes (the HV4 `hv4_c0_incomplete` pattern);
- per-bot LIVE_OK and TIER, and the owner-confirmation gate (`OWNER_CANARY_CONFIRMED: bot_id=r4b-exp027`, DEC-032 Am.1 item 3);
- the EXP-027 read-record gate;
- the A3 halt gate (DEC-032 §5 item 1, Am.3);
- the r4-b twin, parity and tie-out gates (DEC-032 §1 item 6, §5, §6; QP N7);
- the live drop-in (`LoadCredential` from `/etc/mal-r4b-c0-key/`, `EnvironmentFile` for the Helius RPC, network on);
- the wallet, which Helm makes on the owner's word (`docs/runbooks/probe-wallet.md`).

Helm acts on each live step only on the owner's word.
