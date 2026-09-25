# mal-core-0 scripts (paper-only)

Host bootstrap helpers. **No secrets in this tree.**

| Script | Where it runs | Purpose |
| --- | --- | --- |
| [agent-ssh.sh](agent-ssh.sh) | Cursor Cloud Agent | Access TCP + SSH; cleanup key/`cloudflared` |
| [bootstrap_mal_core.sh](bootstrap_mal_core.sh) | `mal-core-0` as `ubuntu` | Dirs, schema, venv, user unit, health |
| [apply-schema.sh](apply-schema.sh) | host | `001_ops_state_stubs.sql` via postgres peer |
| [healthcheck.sh](healthcheck.sh) | host | hostname / disk / pg_isready / JSONL writable |
| [observe-jsonl.sh](observe-jsonl.sh) | host | `python -m observe` → `/var/lib/mal/sealed/jsonl` |
| [mal-observe.service](mal-observe.service) | host (systemd --user) | Same observe path, linger-enabled |
| [trade-tape.sh](trade-tape.sh) | host | `python -m observe.trade_tape` → `/var/lib/mal/sealed/trades` |
| [mal-trade-tape.service](mal-trade-tape.service) | host (systemd --user) | Public RPC logsSubscribe tape (pump.fun + PumpSwap) |
<<<<<<< HEAD
| [wallet-leaderboard.sh](wallet-leaderboard.sh) | host (one-shot, `nice -n 19`) | Tape L2 FIFO wallet board + follow-signal JSONL. Does **not** touch the recorder. |
| [paper-signal-scan.sh](paper-signal-scan.sh) | host (one-shot, `nice -n 19`) | Follow / crowd / curve / clean-launch fill scan on the PR #76 book. Does **not** touch the recorder. |
=======
| [attention.sh](attention.sh) | host | `python -m observe.attention` → `/var/lib/mal/attention` |
| [mal-attention.service](mal-attention.service) | host (systemd --user) | DexScreener / pump.fun / GeckoTerminal attention first-seen |
>>>>>>> 054b473 (Add a $0 attention tape for DexScreener, pump.fun, and GeckoTerminal.)

Runbook: [tools/oracle_ssh_smoke.md](../../tools/oracle_ssh_smoke.md). On-host note: `/var/lib/mal/eng/BOOTSTRAP.md`.
