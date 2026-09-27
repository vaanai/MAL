---
cursor:
  subagentId: "bc-eb9a2a4a-a407-521f-aec7-70cc5035329a"
---

# Attention layer ($0) — first-seen tape + paper score

Paper only. Goal is profit vs humans who react to DexScreener / pump.fun / GeckoTerminal surfaces, not vs the on-chain tape alone. Trade-tape recorder and `mal-trade-tape.service` were not modified. Postgres was not opened. No paid APIs, no accounts.

PR: https://github.com/vaanai/MAL/pull/82 (`cursor/attention-layer-329a`, stacked on #73). Scoring used PR #76 simulator modules on the host (`tools.paper_tape_scoreboard`).

Host: `mal-core-vnic`. Fingerprint check in `agent-ssh.sh` passed. Poller is `mal-attention.service` (systemd --user, Nice=10). Output: `/var/lib/mal/attention`. Scoreboard: `/var/lib/mal/paper/attention/`. Daily job: `mal-attention-daily.timer` **04:45 UTC** (after LAYA 04:15), Nice=19. Trade-tape unit stayed **active**.

## Headline

**Nothing to promote.** The first score was almost entirely the startup snapshot — coins already on the page when the poller came up at 15:26Z, hours after the first tape print. That book is “buy whoever is still featured,” not “buy the arrival.”

Buy-every-create bar on this window (hourly tapes **2026-09-25 07:48:49Z–15:30:03Z**, 0.05 SOL, fail 0, rugs kept, PR #76 fill/exit grid): **n=7100, median −0.002580 SOL, mean −0.011783, win 17.8%, total −83.66 SOL**. Same fee-loss shape as #76/#77.

Genuine arrivals (first-seen after poller start, not in the initial snapshot) are what the daily job scores. Promotion is the project-wide rule: **n≥100** out-of-sample trades, token-bootstrap 90% CI of mean SOL > 0 (1000 draws, seed 1), total still positive after dropping the best trade, majority of UTC days positive. Books with n≥30 are **watch**, not promote.

## 1. Verified free endpoints (2026-09-25, keyless)

| Surface | URL | Auth | Limit (observed or docs) | Poll |
| --- | --- | --- | --- | --- |
| Dex paid profiles | `GET https://api.dexscreener.com/token-profiles/latest/v1` | none | docs 60/min; `Cache-Control: max-age=60` | 60s |
| Dex boosts latest | `.../token-boosts/latest/v1` | none | same | 60s |
| Dex boosts top | `.../token-boosts/top/v1` | none | same | 60s |
| Dex ads | `.../ads/latest/v1` | none | same | 60s |
| Dex paid-at | `.../orders/v1/solana/{mint}` | none | same; returns `tokenProfile` / ads with `paymentTimestamp` | 2s gap, new mints only |
| pump livestreams | `GET https://frontend-api-v3.pump.fun/coins/currently-live` | none | `x-ratelimit-limit: 60` / 60s | 15s |
| pump hot-coin (KOTH successor) | `.../coins/hot-coin` | none | 60/min; often `{"hotCoin": null}` | 30s |
| pump graduating fold | `.../coins/boards/graduating` | none | 60/min; **rank 0 = human KOTH stand-in** | 15s |
| pump movers / great-coins / top-runners | `.../coins/boards/movers`, `.../coins/great-coins`, `.../coins/top-runners` | none | 60 / **20** / 50 per min | 30–60s |
| GeckoTerminal trending | `GET https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?include=base_token` | none | public **10/min**; `max-age=30` | 60s |

**Gone / not used:** `GET /coins/king-of-the-hill` is **404** (v3 treats the path as a mint). `GET /replies/{mint}` is 404. Coin rows already carry `reply_count`, `twitter`, `telegram`, `website`. `king_of_the_hill_timestamp` still exists on the DTO and is stored when present. `livestream-api.pump.fun` has `/health` only; livestreams are `currently-live`.

Backoff: 5s × 2^errors, cap 120s, honors `Retry-After`. UA identifies the repo. Solana-only; WSOL dropped.

## 2. Poller

`python -m observe.attention` → hourly `attention-YYYY-MM-DDTHH.jsonl`, zstd on hour roll, disk hold under 20% free (no deletes; volume is tiny). One row per `(kind, mint)` first-seen. systemd user unit `mal-attention.service`. Does not write under `/var/lib/mal/sealed/trades`.

Live at **2026-09-25 15:26:03Z** (`poller_start.json`). After four minutes: **333 first-seen rows, 177 KB**, polls=74, http_429=0, free_ratio=0.933. Restarted at 15:47Z with snapshot reconstruction: **334 startup keys, 80–86 genuine arrivals by 15:48Z**, mark_first_polls=0 (restart did not re-stamp the live lists). Heartbeat after restart: `written=2 genuine=2 snapshot=0`. CPU Nice=10, idle I/O class. Trade-tape stayed **active**.

Startup snapshot (so later scores are not that backlog):

- `poller_start.json` — first start unix-ms (reconstructed as min `t_first_ms` if the file was missing).
- `startup_snapshot.jsonl` — `(kind, mint)` from the first poll of each source. On a tape that predated the flag, keys with `t_first_ms` within 5 minutes of start (covers staggered first polls + Dex orders drain).
- Each stored row has `snapshot: true|false`. Genuine = `t_first_ms` after start and `(kind, mint)` not in the snapshot set.
- Restarts do **not** re-stamp the live lists as snapshot.

Native event clock `event_t_ms` when the payload has it: Dex `paymentTimestamp`, pump `playlist_updated_at` / `thumbnail_updated_at` (stream), `king_of_the_hill_timestamp`, Gecko `pool_created_at`. Buy clock stays **our** first-seen + 1s (using the event stamp as a signal would look ahead).

## 3. Paper score (PR #76 simulator) — startup snapshot

Signal = max(first-seen, first tape print) + 1s. Size 0.05 SOL, real fees, rugs kept, fail 0. Hourly `trades-2026-09-25T07`…`T15` only (leftover daily `trades-2026-09-25.jsonl.zst` omitted to avoid double-count; that is why the window starts **07:48Z**, not 07:00). Creates from `observe-2026-09-25.jsonl`. `nice -n 19`. Tape lines 7,104,494, kept prints 3,240,188, bad JSON 0.

Hold_30s:

| book | signals | n | median | mean | win | total SOL | miss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| buy_every_create | 8617 creates | 7100 | −0.002580 | −0.011783 | 17.8% | **−83.66** | 1517 |
| dex_paid_profile (seen now) | 53 | 33 | −0.001829 | −0.000917 | 18.2% | −0.030 | 20 |
| dex_boost | 28 | 20 | −0.001829 | −0.002230 | 5.0% | −0.045 | 8 |
| pump_live | 61 | 49 | −0.001829 | −0.002844 | **0%** | −0.139 | 12 |
| pump_koth (graduating #1) | 1 | 1 | −0.000333 | −0.000333 | 0% | −0.000 | 0 |
| pump_featured | 75 | 61 | −0.001829 | −0.002118 | 3.3% | −0.129 | 14 |
| gecko_trending | 20 | 12 | −0.001779 | −0.001954 | 0% | −0.023 | 8 |
| koth_or_live | 62 | 50 | −0.001829 | −0.002794 | 0% | −0.140 | 12 |
| **dex_paid_at_clock** | 53 | **22** | −0.001829 | **+0.000401** | 31.8% | **+0.0088** | 31 |

`−0.001829` is the fee atom (same cluster as #76/#77). Livestreams currently on the page are a **0% win** book at our first-seen — the crowd already moved.

**dex_paid_at_clock** (struck as a buy book) used Dex `orders.paymentTimestamp` as the entry clock. Red-team: 13/33 of those fills were **before the first tape print**. That book is time travel and is no longer simulated. `paymentTimestamp` stays a lag feature; entry is max(our first-seen, first tape print) + 1s. The +0.0004 mean on n=22 is not a result.

## 4. First-seen vs on-chain (snapshot)

Lag = poller `t_first_ms` − first tape print. Create-lag is not reliable on this pass: mints missing from observe creates were stubbed at `t_first`, so create-lag collapses to 0.

| book | n with print | median lag vs first print | p10 | p90 | before first print |
| --- | ---: | ---: | ---: | ---: | ---: |
| pump_live | 49 | **6.4 h** | 2.2 h | 7.5 h | 0 |
| pump_koth | 1 | 7.5 h |  |  | 0 |
| dex_boost | 20 | 6.6 h | 0.9 h | 6.9 h | 0 |
| dex_paid_profile (now) | 33 | 2.7 h | 0.46 h | 6.8 h | 0 |
| dex_paid_at_clock | 33 | **8.7 min** | (negative: paid before this tape) | 51 min | 13 |

We are **not** seeing these before the crowd on this first snapshot. List endpoints return whoever is still featured; that is hours after the first print.

## 5. Daily genuine-arrival job

`python -m tools.paper_attention_daily` via `mal-attention-daily.timer` at **04:45 UTC** (LAYA is 04:15; this waits until after). `Nice=19`, idle I/O. Does not touch the trade-tape recorder.

- **Filter:** genuine arrivals only, per event `kind`.
- **Lag:** first-seen minus the event's own timestamp (Dex `paymentTimestamp`, stream start / playlist, KOTH stamp, Gecko `pool_created_at`), plus first-print lag as a side column.
- **Sim:** PR #76, buy at max(first-seen, first tape print) + 1s, never a vendor stamp, 0.05 SOL, hourly tapes only, rugs kept.
- **Promotion:** project-wide rule on hold_30s realized/no-exit fills — bootstrap CI, drop-best, majority days, **min n=100**. n≥30 is **watch**, not promote. `promote` is false until all four pass.
- Output: `/var/lib/mal/paper/attention/daily/YYYY-MM-DD/{scoreboard.json,scoreboard.md,genuine.jsonl,laya_join.jsonl}`.

Until the timer has run on a full UTC day of watching, do not read the snapshot table in §3 as an arrival edge.

First genuine pass **2026-09-25 15:48–15:51Z**, hourly tape 07:48:49Z–15:50:51Z, 86 genuine / 334 snapshot, tape lines 7,435,000, kept prints 3,644,258. **Nothing promotes** (every attention book fails min n=100; none even reach watch n=30 yet).

Hold_30s genuine arrivals:

| book | signals | n | median | mean | mean 90% CI | total | ex best | promote | blockers |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- | --- |
| buy_every_create | 9078 | 7514 | −0.002596 | −0.011768 | −0.0125 .. −0.0111 | −88.42 | −89.34 | no | mean_ci90,drop_best,majority_days |
| pump_live | 26 | 13 | −0.001829 | −0.005826 | −0.0136 .. −0.0018 | −0.076 | −0.074 | no | min_n,mean_ci90,drop_best,majority_days |
| pump_graduating | 19 | 18 | −0.001843 | −0.003722 | −0.0066 .. −0.0015 | −0.067 | −0.073 | no | min_n,mean_ci90,drop_best,majority_days |
| pump_movers | 11 | 7 | −0.000934 | −0.000033 | −0.0016 .. +0.0017 | −0.000 | −0.006 | no | min_n,mean_ci90,drop_best,majority_days |
| dex_profile | 8 | 3 | −0.005095 | −0.012449 | −0.0216 .. −0.0033 | −0.037 | −0.035 | no | min_n,… |
| pump_koth | 5 | 4 | −0.003515 | −0.007113 | −0.0130 .. −0.0016 | −0.028 | −0.028 | no | min_n,… |
| dex_boost | 3 | 2 | +0.000474 | +0.000474 | −0.0018 .. +0.0028 | +0.001 | −0.002 | no | min_n,mean_ci90,drop_best |

Print lag on genuine arrivals is **minutes**, not the snapshot’s hours: pump_graduating median **2.4 min** vs first print, pump_live **8.2 min**, dex_profile **16 min**. Native event clocks are still sparse on live (no playlist stamp on rows written before that field was stored); Dex `paymentTimestamp` on a newly listed mint is often days-old and is not an arrival clock.

Timer next: **2026-09-26 04:45 UTC**. Trade-tape stayed active for the whole rescore.

## 6. LAYA join JSONL

`/var/lib/mal/paper/attention/laya_join.jsonl` (copied from the daily out dir). One row per first-seen event (snapshot and genuine, flagged). Schema `attention_laya_join_v1`. Host emit **2026-09-25 15:48Z**: **420 rows, 86 genuine, 334 snapshot**.

```json
{"v":1,"schema":"attention_laya_join_v1","mint":"...","t_ms":1758...,"kind":"pump_live","source":"...","snapshot":false,"genuine":true,"event_t_ms":1758...,"lag_ms":1234,"rank":0}
```

Join by `mint` where **`t_ms` ≤ decision time**. `t_ms` is our first-seen **only** — never `event_t_ms` / `paid_at_ms`. Those stamps can be earlier than we knew the mint. Sorted by `(t_ms, mint, kind)` so a merge-join works. Includes `paid_at_ms` / `reply_count` when present as feature values. Reconstructed backlog rows are `snapshot: true` even if the original JSONL predated the flag.

## Honesty / sample

- First score: one UTC day, survivorship, first-seen = now. That is the startup snapshot.
- Genuine book starts after 15:26Z and excludes that snapshot set (5-minute grace on files that predate the flag).
- n=1 KOTH, n=22 paid-at on the snapshot pass. Totals are independent 0.05 SOL fills, not a bankroll.
- Hourly tape starts 07:48Z; ~07:00–07:48 lives only in the leftover daily zst and was not merged.
- `hotCoin` was null the whole time; KOTH book is graduating rank 0.
- Promotion min n=100 matches the project-wide out-of-sample floor. n≥30 books are watch only. Tiny positive books are not a promote.

## What to do with it

Keep the poller running. Let the 04:45 job accumulate genuine n. Feed LAYA the join JSONL as features (`kind`, `source`, `t_ms`, `event_t_ms`, `lag_ms`, `rank`, `reply_count`); do not hard-code “buy Dex paid” or “buy livestream.”
