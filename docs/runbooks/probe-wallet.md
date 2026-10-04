# Probe wallet runbook (DEC-019)

Who: **Helm or the owner**, as root on `mal-fast-0`. The manager and agents never run these scripts and never see the key. Probe live sends do not start until every item in DEC-019 section 6 is done.

## 1. Create the wallet

```
sudo scripts/mal-fast/make-probe-wallet.sh --dry-run   # prints the plan only
sudo scripts/mal-fast/make-probe-wallet.sh
```

- Creates the `mal-live` system user (no home, no shell) and `/var/lib/mal/live` (0700, mal-live).
- Writes `/var/lib/mal/live/probe-wallet.json` (0400, mal-live). It refuses if that file exists. Never delete or replace it by hand while the wallet holds funds.
- Prints two lines: the **public key** and `Fund with 0.5 SOL. Never share the file.`

**Send back to the manager: only the public key.** Nothing else.

## 2. Fund

The owner sends **0.5 SOL** to the public key from their own wallet. Check the balance on an explorer by public key.

## 3. Withdraw (end of probe, or when the owner asks)

Preconditions: the owner has named the destination address; `mal-probe-executor` is stopped (`systemctl stop mal-probe-executor`); no open positions.

```
sudo scripts/mal-fast/probe-withdraw.sh --to <OWNER_ADDRESS> --dry-run
sudo scripts/mal-fast/probe-withdraw.sh --to <OWNER_ADDRESS>
```

- The script closes zero-balance token accounts (rent returns to the wallet), lists non-zero token balances without touching them (sell or move those separately), then sends all remaining SOL minus the fee to `--to`, leaving 0.
- It asks you to type the first 6 characters of the destination. Check the full address against what the owner gave you before typing. `--yes` skips the prompt; do not use it unattended.
- It refuses if the executor is active or `/var/lib/mal/live/state-live.json` shows open positions. `--force` overrides; only use it if you understand why.
- The RPC key is read from `/var/lib/mal/fast-listener/helius.env` and is never printed.
- Send back to the manager: the printed signatures (public) and the destination's final balance, if the owner wants a record.

## Never

- Never print, copy, email, paste, commit or screenshot `probe-wallet.json`, or its contents in any form.
- Never pass the key through a command line, env var, ticket, chat or MiScusi.
- Never reuse this wallet for anything else, and never fund it beyond 0.5 SOL.
- Never run these against a wallet that is not the probe wallet.
- Never open ports or edit `ufw`, `sshd` or cloudflared for this.
