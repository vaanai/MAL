# Probe wallet runbook (DEC-019)

Who: **Helm or the owner**, as **root** on `mal-fast-0`. The manager and agents never run these scripts and never see the key. Probe live sends do not start until every item in DEC-019 section 6 is done.

Root never runs code from the repo checkout (agents can write to it). Root runs only copies installed from a pinned commit into `/usr/local/lib/mal-probe/` (root-owned), with a root-owned venv. The scripts refuse to run from anywhere else, and ignore every `MAL_LIVE_*` override (those exist only for the offline tests).

## 0. Install the pinned tools (fresh root-owned clone, never the agent checkout)

The manager gives you the full 40-character sha of the reviewed merge commit and, ideally, a manifest of sha256 hashes for the four installed files (`scripts/mal-fast/make-probe-wallet.sh`, `scripts/mal-fast/probe-withdraw.sh`, `tools/probe_withdraw.py`, `scripts/mal-fast/requirements-probe-tools.txt`; one `<sha256>  <repo path>` per line).

```
sudo -i
git clone https://github.com/vaanai/MAL /root/mal-probe-src
git -C /root/mal-probe-src checkout --detach <FULL_SHA>
test "$(git -C /root/mal-probe-src rev-parse HEAD)" = "<FULL_SHA>" && echo sha ok
less /root/mal-probe-src/scripts/mal-fast/install-probe-tools.sh      # read it before running it
/root/mal-probe-src/scripts/mal-fast/install-probe-tools.sh <FULL_SHA> [/root/manifest.txt]
```

The installer refuses unless its own clone and every parent directory are root-owned and not group/world-writable, and `HEAD` equals the sha. It reads each file with `git show <sha>:<path>` (no archive attributes), checks the manifest if given (any mismatch refuses), installs into `/usr/local/lib/mal-probe/` (root-owned), and always rebuilds the venv from hashed wheels (`--require-hashes --only-binary=:all:`). It prints the sha256 of each installed file. `probe-withdraw.sh` prints the sha256 of `probe_withdraw.py` every time it starts. Never run the installer or these scripts from `/var/lib/mal/fast-forward/src` or any agent-writable checkout. Do not set `safe.directory '*'`.

## 1. Create the wallet

```
sudo /usr/local/lib/mal-probe/make-probe-wallet.sh --dry-run   # prints the plan only
sudo /usr/local/lib/mal-probe/make-probe-wallet.sh
```

- Creates (or verifies) the `mal-live` system user (nologin, no extra groups) and `/var/lib/mal-live` (0700, mal-live). That directory holds executor state only (`state-live.json`, `probe-fills.jsonl`), never the key.
- Creates `/etc/mal-probe` (`root:root` 0700) and verifies owner and mode. It refuses a symlinked `/var/lib/mal-live` or `/etc/mal-probe`, and refuses unless `/var/lib`, `/etc` and every ancestor is root-owned and not group/world-writable.
- Writes `/etc/mal-probe/probe-wallet.json` (`root:root` 0400, never chowned to `mal-live`), fsyncs it and its directory, reads it back and checks the public key, owner and mode before printing. It refuses if the file exists, or if a legacy `/var/lib/mal-live/probe-wallet.json` exists. Never delete or replace it by hand while the wallet holds funds.
- The executor gets the key only through systemd `LoadCredential=` (a root-run unit hands it a private copy); `mal-live` has no read access to `/etc/mal-probe`. The unit change (live drop-in `LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json`) is in PR #290. The runtime copy appears under `/run/credentials/mal-probe-executor.service/`; the deny lines cover `/run/credentials` too.
- Helm applies the same deny lines as the repo `.claude/settings.json` (Read/Edit/Write on `//etc/mal-probe/**`, Bash reads of `/etc/mal-probe`, `systemd-creds`) to fast-0's user-level and checkout Claude settings, and runs an auditd watch on `/etc/mal-probe`. Absolute file paths in those rules need the `//` prefix.
- Prints two lines: the **public key** and `Fund with 0.5 SOL. Never share the file.`

If it prints an error instead, nothing was created. Do not fund anything.

**Send back to the manager: only the public key.** Nothing else.

## 2. Fund

The owner sends **0.5 SOL** to the public key from their own wallet. Check the balance on an explorer by public key.

## 3. Withdraw (end of probe, or when the owner asks) — root only

Withdraw is **root-only**. It refuses unless euid is 0, and refuses unless the key file is `root:root` 0400 in a `root:root` 0700 directory (default `--keyfile /etc/mal-probe/probe-wallet.json`). It reads the key file directly. It reads `state-live.json` and `probe-fills.jsonl` from `/var/lib/mal-live`. The RPC key file is not readable by `mal-live` and must not be.

Preconditions: the owner has named the destination address; `mal-probe-executor` is stopped (`systemctl stop mal-probe-executor`); no open positions.

```
sudo /usr/local/lib/mal-probe/probe-withdraw.sh --to 5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi --dry-run
sudo /usr/local/lib/mal-probe/probe-withdraw.sh --to 5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi
```

OWNER_DEST = `5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi`

Never copy a withdraw address from transaction history (address-poisoning dust exists on this wallet); the tool only sends to OWNER_DEST.  `--to` is required and is compared to OWNER_DEST with exact string equality (no flag abbreviations, no override, same in `--dry-run` and with `--yes`). Any other value, including a lookalike with the same first and last 4 characters, is refused before the key is read.

- It refuses if the executor is active, if `state-live.json` shows open positions, or if `state-live.json` is missing/unreadable while `probe-fills.jsonl` has live rows. `--force` overrides; only use it if you understand why.
- It refuses if any non-zero token balance (other than wrapped SOL) remains, because draining SOL would leave no fee to move it. Sell or move those tokens first. `--allow-stranded` overrides.
- It closes zero-balance token accounts and wrapped-SOL accounts (rent and wrapped SOL return to the wallet), then sends all remaining SOL minus the fee to `--to`, leaving 0. If a close fails it retries accounts one by one and lists any that could not be closed; `--skip-close` skips closing entirely so the SOL can always be recovered (wrapped SOL left in an unclosed account still counts as stranded and needs `--allow-stranded`).
- It asks you to type the **full** destination address. Type or paste it from the owner's own message, not from the script output. Read any `WARNING` about the destination (off-curve, or owned by a program) before continuing. `--yes` skips the prompt; do not use it unattended.
- The RPC key is read from `/etc/mal-probe-rpc/helius.env` and is never printed.
- Send back to the manager: the printed signatures (public).

## Never

- Never print, copy, email, paste, commit or screenshot `/etc/mal-probe/probe-wallet.json`, or its contents in any form.
- Never pass the key through a command line, env var, ticket, chat or MiScusi.
- Never run the repo-checkout copies of these scripts as root, and never set `MAL_LIVE_TEST` (the scripts refuse it as root).
- Never reuse this wallet for anything else, and never fund it beyond 0.5 SOL.
- Never run these against a wallet that is not the probe wallet.
- Never open ports or edit `ufw`, `sshd` or cloudflared for this.
