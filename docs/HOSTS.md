# Hosts

Paper only. Three machines. Agents reach them through Cloudflare Access, not a public port 22. **Stop on a host-key mismatch.** Do not continue, do not accept a new key, do not set `StrictHostKeyChecking=no`.

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
| Claude access | Read-only account. May `sudo -n systemctl --user -M ubuntu@ restart\|reload\|try-restart` ubuntu's `mal-*` user units directly (2026-09-28). Cannot write files as `ubuntu` — code updates and everything else still go through the owner or Helm. |
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
| `mal-forward-paper.service` | Paper books. Code `/var/lib/mal/paper/forward-paper`. Status `.../forward-paper/runner-status.json`. **Current code `d7485d2`**, live since restart #3, **2026-09-29T00:00:20Z**. Restarts **daily at 00:00:00Z** via the claude timer `mal-runner-daily-restart` on `mal-fast-0` ([#137](https://github.com/vaanai/MAL/pull/137), [#155](https://github.com/vaanai/MAL/pull/155)), logged to `/home/claude/reports/runner-restarts.jsonl`. Every restart resets in-memory `WalletState` `bots`/`snipers`/`leaders`/`creators` and `by_creator` history to empty — cross-mint veto/creator features run cold until re-observed; the funding graph (external `funding-*.jsonl`) is unaffected; dropped open positions settle offline (`tools/forward_paper_settle_orphans.py`). Memory-leak fix chain (wallets container [#129](https://github.com/vaanai/MAL/pull/129), `TxOrder` prune [#134](https://github.com/vaanai/MAL/pull/134)) cut RSS growth to about **300 MB/h**; `tx_order_entries` plateaus around 800k. History: [ARTIFACTS/daily/2026-09-28-manager-session.md](../ARTIFACTS/daily/2026-09-28-manager-session.md), [ARTIFACTS/lab/forward-paper-memory-2026-09-27.md](../ARTIFACTS/lab/forward-paper-memory-2026-09-27.md). Cgroup `MemoryHigh` 10G / `MemoryMax` 12G. Claude's Oracle read-only account may `sudo -n systemctl --user -M ubuntu@ restart mal-forward-paper` (and the other `mal-*` user units) directly, but still cannot write files as `ubuntu` — code updates still fast-forward `.../forward-paper/src` via the owner or Helm. |
| `mal-pump-backfill.service` | Helius `getBlock` for **2026-09-22T00Z–2026-09-25T07Z**. Env `/var/lib/mal/backfill/helius.env`. CPU cap 50% of one core. Log `.../logs/pump-backfill.log`. |
| `mal-pump-backfill-resume.service` | Starts backfill again after LAYA exits. |
| `mal-laya-v0.timer` | 04:15 UTC. **Disabled until 2026-10-05** (runner lag still spiked to about 12.5 s). If re-enabled, [#106](https://github.com/vaanai/MAL/pull/106) runs `tools.laya_frozen_nightly` under `/var/lib/mal/paper/laya-v0`: cached pre-freeze fit, forward and backward holdout append, scoreboard. Exploratory retrain and mig15 deploy stay skipped. |
| `mal-migrate-direct-oos.timer` | **01:20 UTC** oneshot. **Disabled by Helm, 2026-09-28.** The frozen migrate-direct cell is dead (formal FAIL); the timer kept scoring it and competing with the runner for CPU. Output was `/var/lib/mal/paper/migrate-direct-oos` and `.../migrate-direct-forward`. |
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
| `mal-fast-create.service` | **up** | PumpPortal `subscribeNewToken`. Out `/var/lib/mal/sealed/fast-create`. Log `.../logs/fast-create.log`. Was crashing about every 4h (2026-09-27, uncaught `websockets` `InvalidStatusCode` HTTP 502 on reconnect, 9–12 s create gaps); [#117](https://github.com/vaanai/MAL/pull/117) fixed `/var/lib/mal/eng/fast_create_listener.py` (backup `.bak-20260927-pre117`), unit restarted 22:55Z. |
| `mal-fast-public-logs.service` | **up** (installed on the host 2026-09-27; the unit template is not a separate file under `scripts/mal-core/`) | Public create logs. Out `/var/lib/mal/sealed/fast-public/`. |
| `mal-fast-pre-create.service` | **up** | Helius preprocessed mint-authority. Daily cap 10,000. Env `/var/lib/mal/fast-listener/helius.env`. Out `/var/lib/mal/sealed/fast-pre-create`. State `.../fast-listener/pre-create-credits.json`. |
| `mal-fast-early-trade.service` | **installed, disabled** | Paid curve subscribe. Too expensive. Do not enable. |
| `mal-fast-trade-tape.service` | **installed, disabled** | Full public tape. Median lead was under 150 ms. Out would be `/var/lib/mal/sealed/fast-trades`. |
| `mal-fast-backfill.service` (walker 1) | **enabled, the backward walk** | Re-floored at **2026-09-15T12** (the lower bound of the EXP-009 block) via a drop-in `range.conf` (`MAL_FAST_BACKFILL_HOURS=156`, cap raised 2.0M → 2.3M). `CPUQuota=400%`, Nice 10, MemoryMax 6G. Out `/var/lib/mal/backfill-fast`. Working dir `/home/ubuntu/mal-oos`. 1,307,207 credits used, 93 hours sealed, oldest 2026-09-18T03 at 05:03Z 2026-09-29. |
| `mal-fast-backfill-b.service` (walker B) | **enabled** | Covers `[2026-09-12T12, 2026-09-15T12)`. Cap 1.1M credits. Out `/var/lib/mal/backfill-fast-b`. |
| `mal-fast-backfill-c.service` (walker C) | **enabled** | Covers `[2026-09-09T12, 2026-09-12T12)`. Cap 1.1M credits. Out `/var/lib/mal/backfill-fast-c`. |
| `mal-fast-oos-score.service` | **stopped, 2026-09-28T19:00:50Z** | Scored the now-dead frozen migrate-direct cell into `/var/lib/mal/paper/migrate-direct-oos-fast/report.json`. Stopped after the formal FAIL verdict; it had also read two hours (2026-09-18T23, 2026-09-19T00) inside EXP-009's holdout window before the floor was added — see EXP-009 Amendment 3. |

### Claude schedules (user `claude`, separate from user `ubuntu` above)

[#119](https://github.com/vaanai/MAL/pull/119), `ops/claude-schedules/`: `mal-daily-review.timer` (05:00 UTC), systemd `--user` timer for the `claude` account. Headless `claude -p`, no shell tools, read-only fact gathering in the wrapper script; reports under `/home/claude/reports/daily-review/`. `mal-oos-check.timer` was a **one-shot** for the 2026-09-28 21:00Z frozen-cell OOS read; it ran once (`/home/claude/reports/oos-check/2026-09-28.md`) and is not recurring. `mal-runner-daily-restart.timer` ([#137](https://github.com/vaanai/MAL/pull/137), [#155](https://github.com/vaanai/MAL/pull/155)) restarts `mal-forward-paper` on Oracle daily at 00:00:00Z and logs to `/home/claude/reports/runner-restarts.jsonl`. Detail: `ops/claude-schedules/README.md`.

Claude's own memory limits on this box: `user-1002.slice` (claude) `MemoryMax` 15G, no `MemoryHigh`; `claude-remote` `MemoryMin` 1G; heavy claude jobs do not auto-restart. One heavy replay job at a time, at most 2 workers — see [CLAUDE.md](../CLAUDE.md) Operating notes.

### Health, logs, paper books

There is no `mal-healthcheck.timer` on this box. Check:

- `systemctl --user is-active` on `mal-fast-create`, `mal-fast-public-logs`, `mal-fast-pre-create`, `mal-fast-backfill`, `mal-fast-backfill-b`, `mal-fast-backfill-c`. (`mal-fast-oos-score` is stopped — the frozen cell is dead.)
- Disk free stays at or above 30% or the fast backfill refuses to start. Directory cap 40 GiB.
- Credit counter for the mint-authority listener: `/var/lib/mal/fast-listener/pre-create-credits.json`.
- Logs under `/var/lib/mal/logs/fast-*.log`.
- `user-1000.slice` (ubuntu/backfills) `MemoryMax` 8G.

### Safe to restart

Safe when a manager has asked, and only if you are not in the middle of a sealed hour you still need: `mal-fast-create`, `mal-fast-public-logs`.

`mal-fast-pre-create` spends Helius credits (cap 10,000/day). `mal-fast-backfill`, `mal-fast-backfill-b`, and `mal-fast-backfill-c` each spend their own credit cap. Restart those only with a manager's yes, and report credits used.

Do **not** restart `cloudflared`, `sshd`, or anything under `/opt/miscusi`. Do not enable `mal-fast-early-trade` or `mal-fast-trade-tape` without a new measurement that clears the credit and latency bars already recorded in [ARTIFACTS/lab/fast-listener-2026-09-27.md](../ARTIFACTS/lab/fast-listener-2026-09-27.md).

## mal-research-0 (heavy research)

All heavy research runs here: exploration and scoring jobs, training, sweeps, and later the backfill walkers. It's paired into the MAL space in MiScusi. Paper only.

| | |
| --- | --- |
| Name | `mal-research-0` |
| Hardware | Ryzen 5950X (32 threads), 125 GB RAM, 2×1 TB NVMe in RAID1 (`/dev/md1`). No swap, on purpose. |
| SSH | `ssh mal-research-0` as `claude` from mal-fast-0, via Cloudflare Access at `ssh-research.tradervaan.com` (`~/.local/bin/cf-research-proxy.sh`, service token in `~/.config/cf-access/research.env`, mode 600). No public port. Root SSH is disabled. |
| Host key | ED25519 `SHA256:X6kDkiRNXSWQ0X8iLV9+MnkPhPDF3+NRIN2Tw1r+xtk`, pinned in `~/.ssh/known_hosts_research` with strict checking. |
| Claude's key | `~/.ssh/id_ed25519_research` (`SHA256:T+Qwl3erW/cKnsL8CCfGKA27gtoQ/6YS8DrXpGq6GEM`) |
| Data | `/data/mal`, owned by claude, 868 GB free (2026-09-30). rsync 3.2.7 on both ends: `rsync -a --checksum … mal-research-0:/data/mal/…`, with sha256 manifests. |
| Memory | MiScusi jobs run in claude's user slice (MemoryMax 112 GiB). `research-jobs.slice` (104 GB) is for manual `systemd-run` jobs. |
| Backups | A nightly encrypted backup runs at 03:17 UTC. Put re-downloadable bulk data in a folder containing a `.nobackup` file. `mal-archive <path>` moves cold data to the 1 TB backup space. |
| Owner of admin | Helm (firewall, SSH, Access, the Helius key placement when the walkers move). |

**Sync rule:** confirmation and holdout blocks are copied only after their one-shot read, or into a location that only the one-shot scorer role reads, per the data catalog. Never sync an unread confirmation block into the exploration area.

## Both hosts

| Rule | Why |
| --- | --- |
| Paper only | No send, no signer, no wallet. |
| No trading, wallet, or X keys | Phase 0 fence. |
| Postgres localhost | Oracle only. Never publish 5432. |
| Port 22 is never public | Access TCP is the agent path. Owner break-glass is not an invitation to open `:22` to the world. |
| Stop on host-key mismatch | The pins above are the check. A mismatch means stop and escalate. |
