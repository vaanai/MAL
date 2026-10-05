# DEC-019 live probe: per-buy data, executor build 8a6849b (before #307), 2026-10-05

**This is an execution measurement at 0.05 SOL, not a book result and not gate evidence.** Probe trades never count toward any gate.

**Run:**
- Unit `mal-probe-executor`, live since 2026-10-05T14:46:17Z.
- Executor build: deployed src `8a6849b4a9dba468c2a1c32cc9106aeba9ea1dc4`, non-pinned `live.conf`.
- Wallet `5n95…Sugk`, funded 509,528,770 lamports.
- Sources: read-only jobs #146 and #147 (`/var/lib/mal-live/probe-fills.jsonl`, live rows only); tip-follower `migrations-*.jsonl` (`complete` events); the migration-stream probe (job #127, `migrate_v2` slots, ran until about 15:38Z).

## Per buy

k = buy landed slot − reference slot.
- **migrate** = slot of the pump migration tx (processed stream; blank after the stream stopped).
- **complete** = slot of the bonding curve's `complete` event (the runner's trigger, tip follower).

| # | Mint | complete slot | migrate slot | landed slot | k (migrate) | k (complete) | decision → send ms | Exit | Realized lamports |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 1 | BVHA3HGh… | 453610968 | 453610971 | 453610986 | 15 | 18 | 2,802 | sl | −20,640,226 |
| 2 | 6eaDDsqt… | 453616949 | 453616952 | 453616966 | 14 | 17 | 2,377 | sl | −18,862,159 |
| 3 | 8JqER8jd… | 453618293 | 453618303 | 453618314 | 11 | 21 | 2,081 | sl | −38,188,369 |
| 4 | ATTZADCb… | 453625399 | — | 453625418 | — | 19 | 2,894 | sl | −24,656,936 |
| 5 | DGCKDdd5… | 453626992 | — | 453627014 | — | 22 | 3,158 | tp | +20,282,051 |
| 6 | 74WKumqJ… | 453627116 | — | 453627137 | — | 21 | 3,326 | sl | −18,470,xxx (derived, see below) |

**Totals.** The executor's `--status` at about 16:20Z (job #147) shows 6/30 attempts, `realized_sol=-0.100536`, 0 open, no halts. Buy 6's realized value is that total minus buys 1–5. The status rounds to 6 decimal places, so it carries ±1,000 lamports.

**Buy cost.** Each buy spent 52,018,840 lamports, except buy 1 at 53,365,040. That covers 50,000,000 spend, the 505,000-lamport fee (500k priority plus base), and account rent that is refunded on close. Each sell's fee was 505,000.

**Every buy landed.** None expired or failed. Each landed 2 slots after the executor's pool-state read.

## Reading

- **Latency.** k from the curve's `complete` event was 17–22 slots on every buy. From the migration tx it was 11–15 slots where that slot is known.
- **Exploration curve.** #292 measured k from the migration slot (EXP-012's entry is slot+1 after migration). Its CI lower bound is below 0 at k ≥ 12.
- **Cause, diagnosed.** The executor waits for the paper runner's post-simulated-latency `enter` row, so latency is counted twice. PR #307 makes the runner write intents at decision time; the expected saving is about 6 slots.
- **n = 6 is far too small to estimate an edge.** Realized P&L here is reported only as execution data.

## For the DEC-019 §7 note

Attempts made under different executor builds have different latency and must never be pooled. Report one group per executor build:
- this file is group **`8a6849b` (pre-#307)**;
- after #307 deploys, a separate group keyed by the new build sha.

Key every fill row to the build that made it, using the deployed SOURCE_COMMIT or the pinned `<sha>` at the time of the row.
