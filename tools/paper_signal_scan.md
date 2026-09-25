# Paper signal scan (follow / crowd / curve / clean-launch)

Operator note. **No secrets. Does not touch the trade-tape recorder or its unit.**

| | |
| --- | --- |
| CLI | `python -m tools.paper_signal_scan` |
| Tests | `python3 -m unittest tools.test_paper_signal_scan` |
| Host helper | [`scripts/mal-core/paper-signal-scan.sh`](../scripts/mal-core/paper-signal-scan.sh) (`nice -n 19`, `ionice -c3`) |
| Inputs | PR #73 tape + observe creates + PR #75 `follow-signals-noisy_v0.jsonl` |
| Fills | PR #76 scoreboard (1s default latency, 0.05 SOL, real fees, rugs kept, full exit grid) |
| Host outputs | `/var/lib/mal/paper/signal-scan/` (gitignored) |

## Families

Reusable scorer modules (features are `f_*` / `f_sig_*` on labels and `signal_features.jsonl` for a later model):

- `tools.paper_signal_follow` — buy when a noisy_v0 leaderboard wallet buys. Latency grid 0.5/1/2/5s. Copyable slice is `delta_slot_from_create > 2`.
- `tools.paper_signal_crowd` — ≥N distinct non-vetoed wallets buy within W seconds. Grid N=3,5,8 × W=1,2,5s. Sniper prints (Δslot ≤ 2) and sniper/bot wallets excluded.
- `tools.paper_signal_curve` — bonding-curve progress 30/50/70/90% and first PumpSwap print.
- `tools.paper_signal_clean` — low sniper/bundle SOL share in first slots at T+1s, plus creator with no prior 60s dump on this tape.

One position per mint per book. Best exit is chosen on the first half of mints by signal time and reported on the second half.

## Host

```bash
scripts/mal-core/paper-signal-scan.sh
```
