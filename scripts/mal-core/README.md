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
| [wallet-leaderboard.sh](wallet-leaderboard.sh) | host (one-shot, `nice -n 19`) | Tape L2 FIFO wallet board + follow-signal JSONL. Does **not** touch the recorder. |
| [paper-signal-scan.sh](paper-signal-scan.sh) | host (one-shot, `nice -n 19`) | Follow / crowd / curve / clean-launch fill scan on the PR #76 book. Does **not** touch the recorder. |
| [laya-v0.sh](laya-v0.sh) | host | Daily LAYA v0 retrain into `/var/lib/mal/paper/laya-v0` (nice/ionice, not the recorder) |
| [mal-laya-v0.timer](mal-laya-v0.timer) | host (systemd --user) | 04:15 UTC paper retrain |
| [attention.sh](attention.sh) | host | `python -m observe.attention` → `/var/lib/mal/attention` |
| [mal-attention.service](mal-attention.service) | host (systemd --user) | DexScreener / pump.fun / GeckoTerminal attention first-seen |
| [attention-daily.sh](attention-daily.sh) | host | genuine-arrival rescore + LAYA join JSONL (Nice=19) |
| [mal-attention-daily.timer](mal-attention-daily.timer) | host (systemd --user) | 04:45 UTC daily, after LAYA 04:15 |
| [forward-paper.sh](forward-paper.sh) | host | Tail the tape into paper books (`/var/lib/mal/paper/forward-paper`). No keys. Does not edit the recorder |
| [mal-forward-paper.service](mal-forward-paper.service) | host (systemd --user) | Long-running forward paper service |
| [forward-paper.json](forward-paper.json) | host | Books: buy-all, LAYA 0.6 / 0.7, migrate tp50/sl30 |

Runbook: [tools/oracle_ssh_smoke.md](../../tools/oracle_ssh_smoke.md). On-host note: `/var/lib/mal/eng/BOOTSTRAP.md`.
