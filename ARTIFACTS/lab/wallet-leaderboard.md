---
cursor:
  subagentId: "bc-e0585fa5-a2bf-5941-a9b2-db19112d784f"
---

# Tape L2 wallet leaderboard (first noisy run)

Paper-only. Own-tape smart-wallet list (research L2 / lab follow-set). Not EXP-004 graph, not EXP-005 L3 packet, not a promote.

**PR:** https://github.com/vaanai/MAL/pull/75 (`cursor/wallet-leaderboard-784f`, stacked on #73). Recorder and `mal-trade-tape.service` not modified. `LAB_STATE.md` not edited.

## Job

`python -m tools.wallet_leaderboard` — FIFO realized PnL (tape cash flows already net of curve fees, minus 5k lamports/trade), vetoes, ranked boards, follow-signal JSONL `{mint, signal_t_ms, slot, wallet, features}`, follower-wave JSONL.

- **strict:** ≥20 closed mints, WR 35–65%, median hold 2–30 min, ≤40% PnL from one mint, invested ≥1 SOL, no vetoes, cap 50. Built for 7–30d.
- **noisy_v0:** ≥3 closed mints, WR 30–75%, hold ≥15s, ≤70% one-mint, invested ≥0.05 SOL. Short-tape floor only.

Copy fills still owe ~3.5% round trip + lag (portal 0.5%/side is `copy_haircut_pnl_sol` only). CreateEvent is not on this feed: `create_slot` = first print. SOL transfers are not on this feed.

Tests: `python3 -m unittest tools.test_wallet_leaderboard` — **21 OK**.

## Oracle run

`mal-core-vnic` 2026-09-25. Fingerprint check in `agent-ssh.sh` passed. `nice -n 19`, ~84s, no systemd unit, no Postgres, no :22, no Tunnel/Access changes. Host outputs: `/var/lib/mal/paper/wallet-leaderboard/`.

| | |
| --- | --- |
| Window | 2026-09-25 **06:58:37.797Z–07:48:01.572Z** (49.4 min, 0.034d) |
| Parsed trades | **480,151** / 68,962 wallets / 3,707 mints |
| Closed-mint wallets | 24,039 |
| **strict** | **0** (window too short; concentration + hold also bind) |
| **noisy_v0** | **50** (cap) |
| Follow signals | 638 (first buy per leader×mint) |
| Lines listed ~07:47 | 759,745 (602 MB). Day file was zstd-rotated by another worker ~07:48 to `trades-2026-09-25.jsonl.zst`; current hour is `trades-2026-09-25T07.jsonl`. We did not rotate or touch the recorder. Parsed count is ~61% of listed lines (unresolved/zero/non-wsol rows dropped). |

Vetoes on **all** wallets (not just the board): bot 20,284 · transfer_in 22,922 · creator 1,172 · sniper_bundler 1,129 · one_hit 4,393 · wr_extreme 3,084 · wash 510 · follower_farm 40 · creator_linked 15.

Raw-PnL leaders are **not** copyable. Top unfiltered: `BwWK17cb…` +14.4 SOL / 214 closed / 2.5s hold / 90% WR (creator+bot); `3F9iH41q…` +286 SOL / 2 mints (sniper+one-hit). Those are vetoed.

## Follower wave (core edge metric)

On 638 noisy_v0 **entry** signals:

| Window | Mean unique other wallets | Mean SOL |
| --- | ---: | ---: |
| 0.4s | 1.8 | 0.84 |
| 1s | 3.1 | 1.30 |
| **2s** | **4.8** (p50 = 3) | 2.18 |
| 5s | 9.1 | 4.21 |
| **10s** | **14.9** (p50 = 9) | 6.70 |
| 30s | 32.5 | 14.8 |

Median time to first other buyer: **180 ms**. The pile-in is already on by the public-RPC copy window. Fill-sim should treat `delta_slot_from_create ≤ 2` as uncopyable even when the wallet is on the board (first sample signal was Δslot=2 on PumpSwap). `signal_t_ms` = tape `t_recv_ms`.

## noisy_v0 board (cap 50)

Median closed mints **8**, WR **65%**, hold **52s**. Sum realized **+52.2 SOL**; after portal haircut **+45.4 SOL**. **0** of 50 go copy-PnL negative. Hold bands: 11 <30s, 31 in 30s–2m, 8 in 2–10m. **No 2–30 min + ≥20 closed + ≤40% one-mint wallet** on this window.

This is a **scalper/late-buyer** board, not a 30d smart list. Median buyer rank **74**, median Δslot **630**, median organic-early **12.5%**. Copying rank-74 fills at +2s is often exit liquidity.

| R | Wallet | Closed | WR | PnL SOL | Copy PnL | Hold | 1-mint | Org early |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `67LwNGrukVFFcA1XZQ9U8ddZGCBuPvPHgMxw81AeRtQ7` | 43 | 67.4% | +1.57 | +1.44 | 29s | 16% | 24% |
| 2 | `64hP97Bwr5PubotcTeGgfhkFrGiLVVxT2kVo9M9b4AEz` | 23 | 60.9% | +4.73 | +3.69 | 17s | 37% | 8% |
| 3 | `D1G7aNgHjQsZ3EWp2nABRZ3gPsusHxGQVTCQguhEMX4D` | 7 | 71.4% | +3.64 | +3.48 | 179s | 42% | 14% |
| 4 | `88887QrRZPZmsstEXsoXXB8E7nbmUDde4Gp5s7jG3ENu` | 50 | 44.0% | +4.20 | +3.45 | 32s | 42% | 34% |
| 5 | `SQHK48QT8SY1vYN44iXji7wQ6CJek8AjfX6mBp47TZq` | 17 | 58.8% | +5.06 | +4.27 | 25s | 44% | 5% |
| 6 | `BxMo47JcFjZFM9bpNCibdapL67PVBgTk6JJa6WVU7qwk` | 22 | 45.5% | +1.70 | +1.26 | 120s | 43% | 0% |
| 7 | `BBqziMQ3UhLeSRp3Hwm3z56awyV8cLdQrmmay1PFxvsf` | 13 | 61.5% | +1.71 | +1.45 | 25s | 35% | 4% |
| 8 | `F4cUjuWkXP7ptAVg13EUeCNPgPQYeuiLSUYNdZgYtW5w` | 15 | 66.7% | +1.26 | +0.97 | 45s | 34% | 0% |
| 9 | `6sViQ1oyvmb6cqB3Emh5Zs9LwV3f6cvVdbg16LHJNvWV` | 8 | 62.5% | +2.25 | +2.13 | 60s | 70% | 0% |
| 10 | `DTzth8MyhtQcRKfMavQfVh1R6QfyKJa2s9YwVh2HZ5EX` | 8 | 75.0% | +1.18 | +1.11 | 27s | 43% | 52% |
| 11 | `2KuarGFvD5tG85UYLcDwCWLRKPH5Rxm2E5sLWJnVtuo8` | 11 | 45.5% | +1.14 | +1.09 | 44s | 50% | 33% |
| 12 | `9keCU8mgA23XV8LCgSCJEo4Lspmo9fRBA3MyxJmZKQSp` | 24 | 70.8% | +1.71 | +1.17 | 47s | 54% | 19% |
| 13 | `4VfKyL8JiSoymQ1Fb6ewcv3VWNv2c5fWP4xuE8MXQdCN` | 16 | 62.5% | +0.38 | +0.36 | 58s | 31% | 39% |
| 14 | `jo16sMMc3JaUesFWBb6WmmzugTw8fPUFq6g8Kk9Me3p` | 14 | 64.3% | +1.26 | +1.18 | 59s | 59% | 12% |
| 15 | `2inXEuL8eR9CqiXFfJq6FCaBeGxWjb6KRfRANjk1rDVp` | 3 | 66.7% | +1.22 | +1.16 | 77s | 53% | 0% |
| 16 | `Da1Erpm3FoS2f3Sto7FfVVnd8S2acvQyxY52VDmpH2iQ` | 29 | 62.1% | +0.51 | +0.47 | 58s | 45% | 44% |
| 17 | `J9k6JCLG3PyvxFw7gDvBXNKMPsWHxRyo5qXKrj8WFsry` | 8 | 62.5% | +0.58 | +0.54 | 103s | 49% | 5% |
| 18 | `6gzhX4M9Si9EaiX7R8vsfoNFFJad2i9ZnrunyRbE2FmQ` | 15 | 53.3% | +0.42 | +0.40 | 26s | 49% | 52% |
| 19 | `GJDebnzmLsScNdWTTYLUZoBD1qtHRSqKThga646fb9vF` | 4 | 75.0% | +0.68 | +0.59 | 38s | 43% | 0% |
| 20 | `6G8Cu53PRgm5aPHxMaZRguYHJfaNxmnmgoR129cKMvJk` | 3 | 66.7% | +1.25 | +1.15 | 365s | 66% | 0% |
| 21 | `G9tBh7m1rnT95unrThTYvzVkud3Y9ZYctMjbx7AZH8Gh` | 7 | 71.4% | +0.64 | +0.63 | 241s | 57% | 100% |
| 22 | `8w8G2FTEES5oRi3kn5CP27J2m4Ys2b6kXzdWoJtKhDBy` | 6 | 66.7% | +0.41 | +0.38 | 23s | 30% | 100% |
| 23 | `ELxcKrDwPRKcKJNEMkqdUgHRtXhMtQsbT8KbtRh51SCC` | 20 | 60.0% | +0.39 | +0.36 | 58s | 43% | 43% |
| 24 | `9g2gAUg2jUqUfbQkK8ziaXfFiVzgihn8r3HqG2Los3mS` | 4 | 75.0% | +1.40 | +1.27 | 378s | 47% | 1% |
| 25 | `633MvGp9QEuTLsDZKnwYxrqfHGDCfth3dv7y9FdKigh5` | 7 | 71.4% | +0.46 | +0.41 | 36s | 46% | 0% |
| 26 | `DJkHv8mQW1oGs1YMSkWaAXquUijNKCJDA4vrkbeERenH` | 4 | 75.0% | +1.03 | +0.96 | 31s | 68% | 0% |
| 27 | `2n1NgNTa2f48RguLoYw5BdXDzLihHLybUU68mGeuEP4f` | 4 | 75.0% | +1.14 | +0.87 | 154s | 0% | 0% |
| 28 | `H8iWY1AChZhGidxbdJyq53Zk9dcZ7r6ed5ReX8veVE3N` | 12 | 58.3% | +0.41 | +0.38 | 34s | 45% | 72% |
| 29 | `7a4XEWA6utPnJSrBBMPkSVVaNTcWhkheocX2koBz85Fc` | 5 | 40.0% | +0.69 | +0.68 | 80s | 61% | 33% |
| 30 | `GQdkCGxqLxuec4XREq4WbZHzVnNwzHJkMDv9SpitQje9` | 4 | 75.0% | +0.54 | +0.50 | 164s | 45% | 40% |
| 31 | `GG5ATPW7bxGm5y4aGa2uWWZV1JvjETiM2Rabc2fT8Y7f` | 6 | 50.0% | +0.38 | +0.29 | 73s | 38% | 13% |
| 32 | `iR8cenDgNiKdLniX5UnfF5sNFUfRsQFEZu4SUQZSJSY` | 6 | 50.0% | +0.68 | +0.60 | 34s | 57% | 33% |
| 33 | `BLhTTLZCHh8bYebLkpurHMzzJMFD74dAKW4dHLCoU4Xs` | 4 | 75.0% | +0.59 | +0.56 | 45s | 56% | 9% |
| 34 | `7taFZn3AD9Gi52thXRQbyLirwRjipoYdE7Live5rbzG5` | 7 | 71.4% | +0.32 | +0.30 | 46s | 42% | 48% |
| 35 | `CxkxCQYLWVRStkWwdCcsAX6BWcPnMeKGQ3zm2m6jVjV8` | 36 | 36.1% | +0.55 | +0.43 | 81s | 56% | 13% |
| 36 | `6BAzPvVFm6KSXPkQzNnCYozEyMVHUyeZQoG9Cppvpy4C` | 8 | 50.0% | +0.43 | +0.35 | 118s | 42% | 7% |
| 37 | `FwfmXRSoiAh9BkgKfp3ua82h1Ar79W7ACsoD9FG9Xcy5` | 4 | 75.0% | +0.34 | +0.33 | 54s | 35% | 0% |
| 38 | `BZ9UNKCxDaTd37fjGBmuL4fsYq8fQPM4MvZEPC5U3wkQ` | 3 | 66.7% | +0.78 | +0.70 | 77s | 69% | 0% |
| 39 | `Fgp4apbKzLst8Xprx35yEUTQtidMkFWwjvpjXXngmRf8` | 21 | 52.4% | +0.41 | +0.38 | 300s | 67% | 3% |
| 40 | `4FCjKaFTStAaEBF1REL6QFHx5VfJTDR46CfnPaFGPcp1` | 7 | 71.4% | +0.77 | +0.62 | 60s | 55% | 0% |
| 41 | `8onxzPhFeCTkwELU1d4Y3qaT4VXVNZ1zrTYtevqkPKse` | 14 | 64.3% | +0.55 | +0.53 | 94s | 64% | 100% |
| 42 | `ERz6BQuNXxNmwLLoCfMKPwHF5xJSo2qhawN9Fqi7pQv9` | 5 | 60.0% | +0.27 | +0.26 | 50s | 46% | 100% |
| 43 | `6BQdXT3fyv68gvXH6UzHHHMc4gFF8eBJzfYncjeuXLHm` | 8 | 75.0% | +0.44 | +0.40 | 21s | 45% | 20% |
| 44 | `EfazTzxttRWzx5aF5vqBLzsNAKG9C3sSFxFovYsGn2qc` | 20 | 65.0% | +0.35 | +0.31 | 29s | 69% | 48% |
| 45 | `2uaouZ5tENtkaKRVTqJGhjAjfjHcPjG9yLwN2g85uP92` | 11 | 63.6% | +0.30 | +0.29 | 31s | 50% | 20% |
| 46 | `3W2eGgXg1U6pJsFGswWdQ5D1q6isbHBfFY5Siqg14A4a` | 5 | 60.0% | +0.24 | +0.21 | 18s | 51% | 0% |
| 47 | `8emnM6nfVM2ePrYDmZcMkwy8qzDhRFZ5EefCkMsmgByX` | 4 | 75.0% | +0.31 | +0.26 | 67s | 59% | 20% |
| 48 | `CpumDaeBKcYKLbbddLPSafU2qRivUG6ycTj87HPh53nN` | 9 | 55.6% | +0.49 | +0.44 | 101s | 69% | 0% |
| 49 | `DqESKiUSpFp8VASoUdG2o8mooKRjASevwN9NJmth1FZL` | 8 | 75.0% | +0.33 | +0.23 | 47s | 0% | 6% |
| 50 | `MQeFoA3pmUFsmtmpvHMaUyCDvtXtuhX9X4bsVh28ZKx` | 14 | 71.4% | +0.17 | +0.15 | 16s | 54% | 86% |

## Copy-shaped slice (still noisy)

Heuristic on this board: organic-early ≥30%, sniper <25%, hold ≥30s, median buyer rank ≤40. **Five** wallets. Prefer these for the fill-sim hook over the raw top-10:

| R | Wallet | Closed | WR | Hold | Buyer rank | Δslot | Org | PnL |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | `88887QrRZPZmsstEXsoXXB8E7nbmUDde4Gp5s7jG3ENu` | 50 | 44% | 32s | 27 | 428 | 34% | +4.20 |
| 13 | `4VfKyL8JiSoymQ1Fb6ewcv3VWNv2c5fWP4xuE8MXQdCN` | 16 | 63% | 58s | 26 | 129 | 39% | +0.38 |
| 16 | `Da1Erpm3FoS2f3Sto7FfVVnd8S2acvQyxY52VDmpH2iQ` | 29 | 62% | 58s | 40 | 162 | 44% | +0.51 |
| 23 | `ELxcKrDwPRKcKJNEMkqdUgHRtXhMtQsbT8KbtRh51SCC` | 20 | 60% | 58s | 35 | 129 | 43% | +0.39 |
| 28 | `H8iWY1AChZhGidxbdJyq53Zk9dcZ7r6ed5ReX8veVE3N` | 12 | 58% | 34s | 32 | 51 | 72% | +0.41 |

Rank 21/22/41/42 are 100% organic-early but mid-pack PnL and (for 21/22) short n. Do not clone; paper-fill with 1s lag + 3.5% RT.

## Plug-in for the fill-sim worker

Host: `/var/lib/mal/paper/wallet-leaderboard/follow-signals-noisy_v0.jsonl` (473 KB, 638 rows). Schema:

```json
{"v":1,"type":"follow_signal","mint":"...","signal_t_ms":1790319520649,"slot":450278562,"wallet":"...","features":{}}
```

`features.signal_kind` is `entry` (first buy of that mint). Filter `features.delta_slot_from_create > 2` before simulating a copy. Follower waves: `follower-waves-noisy_v0.jsonl`.

## Next (not this worker)

Re-run daily until window ≥7d; strict should start to fill. Optional `--creates` overlay when CreateEvent exists. Fill-sim worker owns paper fills/exits on these signals.
