---
cursor:
  subagentId: "bc-62f43346-aca0-54a5-86d6-bc6dbeacf527"
---

# Latency curve (holdout, 2026-09-27)

**No holdout cell has a positive net mean under both fail models.** Gross on buy-every-create, hold 30s, end-of-slot, direct, 0.05 SOL moves from **−20.3% at slot+4 to −19.7% at slot+1** (+0.56 pp, about **+0.19 pp per slot**). That is under the fee floor (~2.5% venue round trip at 0.5 SOL, ~7.5% portal plus 0.001 priority at 0.05 SOL). **The curve is flat.**

Rules are the pre-registration in `latency-curve-prereg.md`. Full grid: `latency-curve-2026-09-27.json`.

## Best cell

`migrate` × `tp50_sl30` × slot+1 **start** (optimistic), direct, 0.05 SOL, priority 0.001 SOL/side.

| | |
| --- | ---: |
| n | 2,947 (of 2,950 migrations; 3 censored) |
| day buckets | 4, of which 0 have a positive total |
| gross mean | +1.88% |
| net mean, flat 15% | −1.95% |
| net mean, pressure scale 1 | −2.16% |
| bootstrap 90% CI lower (flat) | **−2.78%** (−0.00139 SOL/trade) |
| ex-top-3 flat sum | −3.34 SOL (full sum −2.87 SOL) |

Same cell at priority 0.0001 SOL/side is the closest miss: flat net **+0.47%**, pressure net **−0.013%**. Still not both. No route, size, or priority in the grid is positive on both models (972 cells).

## Holdout table

End-of-slot bound, direct, 0.05 SOL, priority 0.001, flat 15%. Cell is net mean % (gross mean %). `skip_fresh_rug` equals `buy_all`: the funding graph’s earliest `first_seen_ms` is after the holdout cut, so unknown does not skip. Start-of-slot on buy-all hold is 0.3 pp less negative at slot+1 (−19.4% gross) and does not change the sign.

| strategy × exit | slot+1 | slot+2 | slot+3 | slot+4 | measured 1.387s |
| --- | ---: | ---: | ---: | ---: | ---: |
| buy_all × hold_30s | −22.0 (−19.7) | −22.2 (−20.0) | −22.2 (−20.1) | −22.3 (−20.3) | −22.4 (−20.4) |
| buy_all × tp50_sl30 | −34.3 (−33.8) | −34.3 (−33.8) | −34.4 (−34.1) | −34.6 (−34.3) | −34.8 (−34.6) |
| buy_all × ladder_2x_t30 | −34.8 (−34.2) | −34.7 (−34.2) | −34.8 (−34.4) | −35.0 (−34.6) | −35.3 (−35.0) |
| migrate × hold_30s | −4.5 (−1.1) | −4.4 (−1.1) | −4.6 (−1.3) | −4.0 (−0.7) | −4.5 (−1.2) |
| migrate × tp50_sl30 | −2.5 (1.2) | −2.5 (1.2) | −2.6 (0.9) | −2.4 (1.2) | −2.5 (1.1) |
| migrate × ladder_2x_t30 | −3.0 (0.9) | −2.9 (0.9) | −3.3 (0.4) | −2.7 (1.1) | −2.8 (1.0) |

Buy-all hold end-of-slot gross by slot: +4 −20.29%, +3 −20.15%, +2 −20.00%, +1 −19.73%, measured −20.39%. n ≈ 90,600 sends-plus-misses. Migrate gross does not rise as the slot gets earlier (tp50 end-of-slot +0.06 pp from slot+4 to slot+1).

## What was scored

Sealed backfill hours **2026-09-22T10 through 2026-09-25T06**, `block_time_end` ≤ 2026-09-25T06:58:00Z. Sep 23 and Sep 24 are full days. Sep 22 starts at 10:00Z. Sep 25 stops at the cut. 90,871 creates, 2,950 migrations, 73.3M trade lines. Sep 25–27 live tape was not scored. The forward void was not in this slice.

`laya_0.6`, `laya_0.7`, `t30_top1_hold_30s`, and `buyers8_top5_ladder_2x` were not slot-rescored. The feature builder does not fit in the 8G cap, and the pre-registration forbids approximating them. `migrate_hold_30s` and `migrate_tp50_sl30` are the migrate rows above.

Pressure intercept was fit once on buy-all / measured / hold_30s / direct / 0.05 sends to mean p = 0.289 (intercept −1.455). One curve for every cell.

## Scope

Paper only. Live fills, promotion, and risk ceilings are unchanged. Scorer: [PR 107](https://github.com/vaanai/MAL/pull/107) (`cursor/latency-curve-f527`), draft, no CI checks reported, not merged. The Oracle job stayed inside MemoryMax 8G, CPUQuota 150%, Nice 19, IO idle, and paused while `mal-forward-paper` `lag_ms` was above 5000. Bootstrap of the finished attempts file was computed off that box.
