# Probe wallet runbook (DEC-019)

Who: **Helm or the owner**, as **root** on `mal-fast-0`. The manager and agents never run these scripts and never see the key. Probe live sends do not start until every item in DEC-019 section 6 is done.

Root never runs code from the repo checkout (agents can write to it). Root runs only copies installed from a pinned commit into `/usr/local/lib/mal-probe/` (root-owned), with a root-owned venv. The scripts refuse to run from anywhere else, and ignore every `MAL_LIVE_*` override (those exist only for the offline tests).

## 0. Install the pinned tools

The manager gives you the full 40-character commit sha of the reviewed PR merge.

```
sudo /var/lib/mal/fast-forward/src/scripts/mal-fast/install-probe-tools.sh <FULL_SHA>
```

(That script only does `git archive <sha>` of three files and installs them 0500/0400 root:root; read it first if you want. It creates `/usr/local/lib/mal-probe/venv` with `solders==0.29.0` if absent.) It prints the sha256 of each installed file; compare them with the hashes the manager posts. `probe-withdraw.sh` prints the sha256 of `probe_withdraw.py` every time it starts.

## 1. Create the wallet

```
sudo /usr/local/lib/mal-probe/make-probe-wallet.sh --dry-run   # prints the plan only
sudo /usr/local/lib/mal-probe/make-probe-wallet.sh
```

- Creates (or verifies) the `mal-live` system user (nologin, no extra groups) and `/var/lib/mal/live` (0700, mal-live). It refuses a symlinked directory.
- Writes `/var/lib/mal/live/probe-wallet.json` (0400, mal-live), fsyncs it, reads it back and checks the public key before printing. It refuses if the file exists. Never delete or replace it by hand while the wallet holds funds.
- Prints two lines: the **public key** and `Fund with 0.5 SOL. Never share the file.`

If it prints an error instead, nothing was created. Do not fund anything.

**Send back to the manager: only the public key.** Nothing else.

## 2. Fund

The owner sends **0.5 SOL** to the public key from their own wallet. Check the balance on an explorer by public key.

## 3. Withdraw (end of probe, or when the owner asks) — root only

Run it as root, not as `mal-live`: the RPC key file is not readable by `mal-live` and must not be.

Preconditions: the owner has named the destination address; `mal-probe-executor` is stopped (`systemctl stop mal-probe-executor`); no open positions.

```
sudo /usr/local/lib/mal-probe/probe-withdraw.sh --to <OWNER_ADDRESS> --dry-run
sudo /usr/local/lib/mal-probe/probe-withdraw.sh --to <OWNER_ADDRESS>
```

- It refuses if the executor is active, if `state-live.json` shows open positions, or if `state-live.json` is missing/unreadable while `probe-fills.jsonl` has live rows. `--force` overrides; only use it if you understand why.
- It refuses if any non-zero token balance (other than wrapped SOL) remains, because draining SOL would leave no fee to move it. Sell or move those tokens first. `--allow-stranded` overrides.
- It closes zero-balance token accounts and wrapped-SOL accounts (rent and wrapped SOL return to the wallet), then sends all remaining SOL minus the fee to `--to`, leaving 0. If a close fails it retries accounts one by one and lists any that could not be closed; `--skip-close` skips closing entirely so the SOL can always be recovered.
- It asks you to type the **full** destination address. Type or paste it from the owner's own message, not from the script output. Read any `WARNING` about the destination (off-curve, or owned by a program) before continuing. `--yes` skips the prompt; do not use it unattended.
- The RPC key is read from `/var/lib/mal/fast-listener/helius.env` and is never printed.
- Send back to the manager: the printed signatures (public).

## Never

- Never print, copy, email, paste, commit or screenshot `probe-wallet.json`, or its contents in any form.
- Never pass the key through a command line, env var, ticket, chat or MiScusi.
- Never run the repo-checkout copies of these scripts as root, and never put `MAL_LIVE_TEST` in a real run.
- Never reuse this wallet for anything else, and never fund it beyond 0.5 SOL.
- Never run these against a wallet that is not the probe wallet.
- Never open ports or edit `ufw`, `sshd` or cloudflared for this.
