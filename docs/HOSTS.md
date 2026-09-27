# Hosts

Paper only. Two machines. Agents reach them through Cloudflare Access, not a public port 22. **Stop on a host-key mismatch.** Do not continue, do not accept a new key, do not set `StrictHostKeyChecking=no`.

Postgres stays on localhost. No trading keys, wallet keys, or X keys on either host. The Helius data key lives only in a mode-600 env file on the host and is never committed.

Helper: [`scripts/mal-core/agent-ssh.sh`](../scripts/mal-core/agent-ssh.sh) `--host core|fast`. Runbook: [`tools/oracle_ssh_smoke.md`](../tools/oracle_ssh_smoke.md).

## mal-core-0 (Oracle)

Archive and training. Not the place to add new listeners.

| | |
| --- | --- |
| Name | `mal-core-0` (remote hostname `mal-core-vnic`) |
| Where | Oracle, aarch64. Provisioned as Always Free 2 OCPU / 12 GB; resized to **4 OCPU / ~24 GB** (2026-09-26). |
| SSH | `ssh.tradervaan.com` via Access. User `ubuntu`. Also reachable as `ssh mal-core-0` with Claude's own key and service token. |
| Host key | ED25519 `SHA256:Hy68mL6wisJ2t+z/JDcSNATPlyA8sudv4Za7A2Y8Ejs` |
| Claude access | Read-only account. Restarts and other changes go through the owner or Helm. |
| Data root | `/var/lib/mal` |
| Checkout | `~/mal` for collectors. Paper jobs also run from `/var/lib/mal/paper/.../src`. |

### Connect

```bash
scripts/mal-core/agent-ssh.sh --host core --dry-run
scripts/mal-core/agent-ssh.sh --host core hostname
ssh mal-core-0
```

Expect `mal-core-vnic`, user `ubuntu`, `uname -m` = `aarch64`. `ssh mal-core-0` is Claude's own read-only account, over Cloudflare Access with its own key and service token.

### Units and timers (systemd --user)

| Unit | Role |
| --- | --- |
| `mal-observe.service` | PumpPortal creates + migrations → `/var/lib/mal/sealed/jsonl`. Log `/var/lib/mal/logs/observe.log`. |
| `mal-trade-tape.service` | Public RPC `logsSubscribe` → `/var/lib/mal/sealed/trades`. Log `.../logs/trade-tape.log`. |
| `mal-attention.service` | Attention poller → `/var/lib/mal/attention`. Log `.../logs/attention.log`. **Stays up.** |
| `mal-attention-daily.timer` | 04:45 UTC. **Disabled until 2026-10-05.** Output `/var/lib/mal/paper/attention/`. Log `.../logs/attention-daily.log`. |
| `mal-funding-graph.service` | Funder enricher → `/var/lib/mal/graph`. Log `.../logs/funding-graph.log`. Reads `/var/lib/mal/backfill/helius.env` when present. |
| `mal-forward-paper.service` | Paper books. Code `/var/lib/mal/paper/forward-paper`. Status `.../forward-paper/runner-status.json`. |
| `mal-pump-backfill.service` | Helius `getBlock` for **2026-09-22T00Z–2026-09-25T07Z**. Env `/var/lib/mal/backfill/helius.env`. CPU cap 50% of one core. Log `.../logs/pump-backfill.log`. |
| `mal-pump-backfill-resume.service` | Starts backfill again after LAYA exits. |
| `mal-laya-v0.timer` | 04:15 UTC. **Disabled until 2026-10-05** (runner lag still spiked to about 12.5 s). If re-enabled, [#106](https://github.com/vaanai/MAL/pull/106) runs `tools.laya_frozen_nightly` under `/var/lib/mal/paper/laya-v0`: cached pre-freeze fit, forward and backward holdout append, scoreboard. Exploratory retrain and mig15 deploy stay skipped. |
| `mal-migrate-direct-oos.timer` | **01:20 UTC** oneshot. **Under test; may move to mal-fast-0.** Frozen cell only. Output `/var/lib/mal/paper/migrate-direct-oos` and `.../migrate-direct-forward`. MemoryMax 8G, CPUQuota 150%, Nice 19. |
| `mal-healthcheck.timer` | Every 5 minutes. Runs `/var/lib/mal/eng/healthcheck.sh`. |

### Health, logs, paper books

- Health: `/var/lib/mal/eng/healthcheck.sh`. Latest JSON: `/var/lib/mal/logs/health-latest.json` and `health.jsonl`.
- Checks disk on `/var/lib/mal`, Postgres on `127.0.0.1:5432`, sealed JSONL writable, and forward-paper lag against the cap in `runner-status.json`.
- Paper books: `/var/lib/mal/paper/forward-paper/` (`decisions.jsonl`, `positions.jsonl`, `pnl-daily`, `runner-status.json`, `guard-live.json`).
- Postgres data dir: `/var/lib/mal/postgresql/16/main`. Database `meme_core`, role `mal_app`. Peer auth. Never print the password.

### Safe to restart

Safe, one at a time, when a manager has asked: `mal-observe`, `mal-trade-tape`, `mal-attention`, `mal-forward-paper`. They are `Restart=on-failure` or `Restart=always` and do not hold the Helius credit budget by themselves.

Do **not** restart without a manager: `cloudflared`, `sshd`, `postgresql`, `mal-pump-backfill` (spends credits), `mal-laya-v0` (disabled until 2026-10-05; the frozen job still spiked runner lag to about 12.5 s). Do not enable `mal-laya-v0.timer` or `mal-attention-daily.timer` before 2026-10-05. Do not edit `ufw`, `sshd_config`, or the cloudflared config.

## mal-fast-0 (OVH Frankfurt)

Fast listeners and the backward backfill. **This is the main focus.**

| | |
| --- | --- |
| Name | `mal-fast-0` |
| Where | OVH Frankfurt, x86_64, Ubuntu 24.04. About 8 cores, ~23 GiB RAM, ~193 GiB disk. |
| SSH | `ssh-fast.tradervaan.com` via Access. User `ubuntu`. |
| Host key | ED25519 `SHA256:q5o6Bf1Vo83LtQhwdSQfIL5D4mkRMjELjaL//Y8XXzQ` |
| Claude access | Full sudo. Never touching `ufw`/iptables/nft, `sshd`, `cloudflared`, or Cloudflare — those and the Oracle server's admin are Helm's, via the owner. |
| Repo checkout used by backfill | `/home/ubuntu/mal-oos` |
| Listener venv | `/var/lib/mal/fast-listener/.venv` |

**`/opt/miscusi` is an unrelated app. Do not touch it.** Do not restart its containers, and do not deploy MAL into that tree.

### Connect

```bash
scripts/mal-core/agent-ssh.sh --host fast --dry-run
scripts/mal-core/agent-ssh.sh --host fast hostname
```

Expect `mal-fast-0`, user `ubuntu`, `uname -m` = `x86_64`.

### Units (systemd --user)

| Unit | State to expect | Paths |
| --- | --- | --- |
| `mal-fast-create.service` | **up** | PumpPortal `subscribeNewToken`. Out `/var/lib/mal/sealed/fast-create`. Log `.../logs/fast-create.log`. |
| `mal-fast-public-logs.service` | **up** (installed on the host 2026-09-27; the unit template is not a separate file under `scripts/mal-core/`) | Public create logs. Out `/var/lib/mal/sealed/fast-public/`. |
| `mal-fast-pre-create.service` | **up** | Helius preprocessed mint-authority. Daily cap 10,000. Env `/var/lib/mal/fast-listener/helius.env`. Out `/var/lib/mal/sealed/fast-pre-create`. State `.../fast-listener/pre-create-credits.json`. |
| `mal-fast-early-trade.service` | **installed, disabled** | Paid curve subscribe. Too expensive. Do not enable. |
| `mal-fast-trade-tape.service` | **installed, disabled** | Full public tape. Median lead was under 150 ms. Out would be `/var/lib/mal/sealed/fast-trades`. |
| `mal-fast-backfill.service` | **the backward walk** | From 2026-09-21T23Z downward. `CPUQuota=400%`, Nice 10, MemoryMax 6G, +2,000,000 credit cap. Out `/var/lib/mal/backfill-fast`. Working dir `/home/ubuntu/mal-oos`. |
| `mal-fast-oos-score.service` | **scores sealed fast hours** | Rewrites `/var/lib/mal/paper/migrate-direct-oos-fast/report.json`. Nice 19, MemoryMax 4G. |

### Health, logs, paper books

There is no `mal-healthcheck.timer` on this box. Check:

- `systemctl --user is-active` on `mal-fast-create`, `mal-fast-public-logs`, `mal-fast-pre-create`, `mal-fast-backfill`, `mal-fast-oos-score`.
- Disk free stays at or above 30% or the fast backfill refuses to start. Directory cap 40 GiB.
- Credit counter for the mint-authority listener: `/var/lib/mal/fast-listener/pre-create-credits.json`.
- OOS book: `/var/lib/mal/paper/migrate-direct-oos-fast/report.json`.
- Logs under `/var/lib/mal/logs/fast-*.log`.

### Safe to restart

Safe when a manager has asked, and only if you are not in the middle of a sealed hour you still need: `mal-fast-create`, `mal-fast-public-logs`.

`mal-fast-pre-create` spends Helius credits (cap 10,000/day). `mal-fast-backfill` spends the +2M cap. Restart those only with a manager's yes, and report credits used.

Do **not** restart `cloudflared`, `sshd`, or anything under `/opt/miscusi`. Do not enable `mal-fast-early-trade` or `mal-fast-trade-tape` without a new measurement that clears the credit and latency bars already recorded in [ARTIFACTS/lab/fast-listener-2026-09-27.md](../ARTIFACTS/lab/fast-listener-2026-09-27.md).

## Both hosts

| Rule | Why |
| --- | --- |
| Paper only | No send, no signer, no wallet. |
| No trading, wallet, or X keys | Phase 0 fence. |
| Postgres localhost | Oracle only. Never publish 5432. |
| Port 22 is never public | Access TCP is the agent path. Owner break-glass is not an invitation to open `:22` to the world. |
| Stop on host-key mismatch | The pins above are the check. A mismatch means stop and escalate. |
