# Fast-entry trigger: early arm vs processed `migrate` (2026-10-07)

This is a latency measurement only. It contains no P&L and no edge claim.

The early-arm read is P&L-free. It reads only `arm-audit.jsonl`, which carries no P&L, position or exit field. It reports labels and timing only, and lumps risk/kill skip reasons together under the DEC-016 Am.2 seal extension. No `intents.jsonl`, `positions.jsonl`, decisions or `runner-status` row was opened.

## 1. Early-arm shadow, fast-0 runner (job #368)

Source: `/var/lib/mal/paper/fast-forward-paper/arm-audit.jsonl`, from the 2026-10-05T17:44:41Z deploy (`a25eb17`) to about 2026-10-07T18:10Z.
- 0 evaluation errors.
- 49 hourly summary rows.

| Day (UTC) | armed | fail | skipped_migrated | skipped_risk_or_kill |
|---|---:|---:|---:|---:|
| 2026-10-05 (from 17:44Z) | 45 | 197 | 132 | 0 |
| 2026-10-06 | 52 | 142 | 543 | 557 |
| 2026-10-07 (to ~18Z) | 57 | 365 | 405 | 76 |

- **Evaluation delay** (eval clock − `complete` receive): p50 0 ms, p90 0 ms, max 241 ms. The arm answers the gate as soon as the tip follower delivers `complete`.
- **`skipped_migrated` is 1,080 of 2,571 audit rows (42%).** In those cases the runner had already seen the migration when `complete` arrived. The tip follower reads confirmed blocks, so `complete`, `migrate` and the first PumpSwap swap often arrive in the same block.
- **The arm→migrate lead cannot be measured from the fast-0 tape.** Its migrations files hold `complete` rows and almost no `migration` rows: 4 of 858 evaluated mints have one. The migrate tx usually carries no decodable event. Section 2 therefore uses the first PumpSwap print as the reference, which is the runner's `migrate` trigger.

## 2. Tip `complete` vs processed `migrate` stream, same window (research-0 files)

Files: `/data/mal/ops/grad-t70/` (job #253).
- `stream/`: `tools/fast_grad_stream.py`, processed `transactionSubscribe`.
- `tip/`: tip follower.
- Window: 2026-10-06T00:41Z–06:41Z. The reference set is graduations whose first PumpSwap print falls at least 60 s inside the window, n = 284.

| Signal | Lead over the tip's first PumpSwap print (ms) | Coverage |
|---|---|---|
| Tip `complete` (today's early arm) | p10 0, **p50 0**, p90 1,515; 50.4% ≤ 0 | 284/284 |
| Processed `migrate` (stream) | p10 715, **p50 1,012**, p90 1,504; 0 ≤ 0 | 262/284 (92.3%) |
| Earlier of the two | p10 602, **p50 996**, p90 1,666 | 284/284 |

The stream arrives before the tip's `complete` on 84.0% of covered mints. By that measure its lead is p10 −56, p50 605, p90 1,090 ms.

At the lab's measured slot time of about 268 ms, the p50 lead is about 3.7 slots; at 400 ms it is about 2.5.

**The 22 stream misses are all fast graduations.** On every one, `complete` and the first PumpSwap print are 0 slots apart (17 of them) or 1 slot apart (5).
- 13 of the 22 produced no stream notification at all.
- 9 produced only `trade`, `complete` or `other` rows, with no decodable `migrate`.

One hypothesis, not tested: a migrate tx that loads the migration account through an address lookup table is not matched by `accountInclude`. The request already sets `maxSupportedTransactionVersion: 0`, and the decoder merges `loadedAddresses`. On these 22 the tip `complete` fallback gives no lead either; it covers them only.

Earlier this read reproduced the handoff figure (264/287, p50 about 1 s lead, job #253) as 262/284 with the 60 s edge rule.

## 3. What this means

- **Early arm alone is not a faster entry.** Its median lead is 0, because it only moves the gate evaluation, and the tip delivers `complete` and the first swap together half the time.
- **The processed `migrate` stream is the lead,** about 1 s at p50. Where the stream misses, the tip `complete` arm is the fallback.
- **Context, report-only and not gate evidence:** EXP-020's END-bound grid puts the paired k2 − k6 at 0.5 SOL at +1.023% of stake on the flat leg (CI90-date [0.109, 1.851]) and +0.747% on the pressure leg ([0.183, 1.276]). Every kept book is negative without 2026-08-21. Live k p50 is 5 today.
- **Speed is a relative lever, not profit by itself (DEC-020 §9).** The faster path is worth building because each later candidate needs it, not because it is positive on its own.
- **A landing k can only be measured live.** This note measures signal lead, not landing slot.

## Not measured

- Landing slot or fill under the stream trigger. That needs a live build and the owner's go.
- Whether a buy sent on a processed `migrate` lands before the first swap.
- Stream behaviour over more than 6 h, or under reconnects at scale.
- Helius credit cost of a permanent stream. The 6 h test used about 33k credits by the stream's own estimate (3 per 100 KB, assumed).
