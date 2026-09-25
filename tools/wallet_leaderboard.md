# Tape L2 wallet leaderboard (paper-only)

Operator note. **No secrets. Does not touch the trade-tape recorder or its unit.**

| | |
| --- | --- |
| CLI | `python -m tools.wallet_leaderboard` |
| Tests | `python3 -m unittest tools.test_wallet_leaderboard` |
| Host helper | [`scripts/mal-core/wallet-leaderboard.sh`](../scripts/mal-core/wallet-leaderboard.sh) (`nice -n 19`) |
| Inputs | `/var/lib/mal/sealed/trades/trades-YYYY-MM-DD.jsonl` (PR #73 tape) |
| Host outputs | `/var/lib/mal/paper/wallet-leaderboard/` (gitignored) |

## What it does

Per wallet, FIFO realized PnL in SOL from tape cash flows (buy spend − sell proceeds; curve fees already inside those amounts) minus 5_000 lamports/trade. Unrealized inventory is not ranked.

Vetoes: sniper/bundler (Δslot ≤ 2 or same signature as the mint's first print), creator / same-create-tx counterparties, bots (median hold < 10s, sub-second lots, ≥10 trades/min, same-tx round trips), wash, follower-farm (organic-early buy, sell into ≥8 later unique buyers), transfer-in sells, one-hit PnL, extreme win rate.

Two boards:

- **strict** — research floors: ≥20 closed mints, WR 35–65%, median hold 2–30 min, ≤40% PnL from one mint, invested ≥1 SOL, no vetoes, cap 50.
- **noisy_v0** — same job, short-tape floors (≥3 closed mints, WR 30–75%, hold ≥15s, ≤70% one-mint, invested ≥0.05 SOL). Use this until the window is 7–30 days.

Follow-signal JSONL (`follow-signals-<board>.jsonl`) is the hook for the paper fill simulator:

```json
{"v":1,"type":"follow_signal","mint":"...","signal_t_ms":...,"slot":...,"wallet":"...","features":{}}
```

`signal_t_ms` is tape `t_recv_ms` of that wallet's **first buy** of the mint. Follower-wave JSONL counts unique other wallets and SOL that pile in at 0.4s / 1s / 2s / 5s / 10s / 30s — the copy-edge metric. Copy fills still owe ~3.5% round trip + lag (curve already in the tape; portal 0.5%/side is the extra haircut field).

CreateEvent is **not** on this feed. `create_slot` is the first print of the mint unless `--creates` overlay JSONL is passed. SOL transfers are not on the tape.

## Host

Do not add a systemd unit. One-shot, modest CPU:

```bash
scripts/mal-core/wallet-leaderboard.sh
```

Or locally after copying JSONL off the box:

```bash
python -m tools.wallet_leaderboard \
  --input /path/to/trades \
  --output-dir data/observe/_wallet_leaderboard \
  --profile both
```

Thresholds are CLI-tunable (`--min-closed-mints`, `--min-win-rate`, …).
