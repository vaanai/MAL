# Fast-entry trigger: early arm vs processed `migrate` (2026-10-07)

This is a latency measurement only. It contains no P&L and no edge claim.

The early-arm read uses only `arm-audit.jsonl`, which carries no P&L, position or exit field. No `intents.jsonl`, `positions.jsonl`, decisions or `runner-status` row was opened.

## 1. Early-arm shadow, fast-0 runner (job #368)

Source: `/var/lib/mal/paper/fast-forward-paper/arm-audit.jsonl`, from the 2026-10-05T17:44:41Z deploy (`a25eb17`) to about 2026-10-07T18:10Z.
- 0 evaluation errors.
- 49 hourly summary rows.

**Seal (DEC-016 Am.2 extension).** Per-day outcome counts are not reported until the 10-16 read. These include the armed counts and the risk/kill skip counts, and the risk reasons include `daily_loss_cap`. See the disclosure at the end.

- **Evaluation delay** (eval clock − `complete` receive): p50 0 ms, p90 0 ms, max 241 ms. The arm answers the gate as soon as the tip follower delivers `complete`.
- **`skipped_migrated` (1,080 rows).** The runner had already seen the migration when `complete` arrived. The tip follower reads confirmed blocks, so `complete`, `migrate` and the first PumpSwap swap often arrive in the same block. This check runs before the gate and the risk check.
- **The arm→migrate lead cannot be measured from the fast-0 tape.** Its migrations files hold `complete` rows and almost no `migration` rows: 4 of the evaluated mints have one. The migrate tx usually carries no decodable event. Section 2 therefore uses the first PumpSwap print as the reference, which is the runner's `migrate` trigger.

## 2. Tip `complete` vs processed `migrate` stream, same window (research-0 files)

Files: `/data/mal/ops/grad-t70/` (job #253).
- `stream/`: `tools/fast_grad_stream.py`, processed `transactionSubscribe`.
- `tip/`: tip follower.
- Window: 2026-10-06T00:41Z–06:41Z (6.0 h).
- Reference set: the 361 tip `complete` mints whose first tip PumpSwap print falls at least 60 s inside the stream window, n = 284.
- The edge rule does not move the result: with 0–30 s it is 285/263, p50 1,012; with 300 s it is 276/255, p50 1,000.
- Quantiles are nearest-rank (index n//2 for p50). Other methods move them by at most 11 ms.
- The computation is [fast-entry-trigger-2026-10-07.py](fast-entry-trigger-2026-10-07.py); run it as `python3 ARTIFACTS/lab/fast-entry-trigger-2026-10-07.py /data/mal/ops/grad-t70`. Quant-proof reproduced it independently on PR #451.

| Signal | Lead over the tip's receive of the first PumpSwap print (ms) | Coverage |
|---|---|---|
| Tip `complete` (today's early arm) | p10 0, **p50 0**, p90 1,515; 50.4% ≤ 0 | 284/284 |
| Processed `migrate` (stream) | p10 715, **p50 1,012**, p90 1,504; 0 ≤ 0 (min 188) | 262/284 (92.3%) |
| Earlier of the two | p10 602, **p50 996**, p90 1,666 | 284/284 |

The stream arrives before the tip's `complete` on 84.0% of covered mints. By that measure its lead is p10 −56, p50 605, p90 1,090 ms.

**What the lead is.** It is a lead over the runner's current trigger time, which is the tip's receive of the first print. It is not a lead over the first swap on chain.
- On 162 of the 262 covered mints, the first PumpSwap print is in the same slot as the processed `migrate`; on 51 more it is 1 slot later.
- Most of the ~1 s is confirmation and getBlock delay that the processed stream avoids.
- In wall-clock terms the p50 lead is about 3.7 slots at the measured 268 ms/slot, but it does not lower landing k one for one.
- On the probe, read-to-land was 2 slots at p50. The probe note infers a best case of k ≈ 3–4 with a processed trigger ([probe-final-2026-10-07.md](probe-final-2026-10-07.md)).

**Stream misses: 22 of 284 (7.7%).**
- **When they happen.** Every miss is among the 157 graduations whose first print is 0–1 slot after `complete`; there are none among the 127 slower ones. So the covered-set quantiles lean slightly high, and the earlier-of row (all 284) is the unbiased one. On the 22 misses the gap is 0 slots for 17 and 1 slot for 5.
- **What the stream produced.** 13 of the 22 produced no stream notification at all. The other 9 produced only `trade`, `complete` or `other` rows, with no decodable `migrate`.
- **Fallback.** The tip `complete` fallback gives no lead on 17 of the misses, and 94–836 ms on the other 5.
- **Hypothesis, not tested:** a migrate tx that loads the migration account through an address lookup table is not matched by `accountInclude`. The request already sets `maxSupportedTransactionVersion: 0`, and the decoder merges `loadedAddresses`.

The handoff's figure (264/287, p50 about 1 s) is consistent with this. It is not a reproduction, and the 264/287 source is not in the repo.

## 3. What this means

- **Early arm alone is not a faster entry.** Its median lead is 0: it only moves the gate evaluation, and half the time the tip delivers `complete` and the first swap together.
- **The processed `migrate` stream gives about 1 s at p50 over today's trigger time.** Where it misses, the tip `complete` arm is the fallback.
- **Speed is a relative lever, not profit by itself (DEC-020 §9).** The faster path is worth building because each later candidate needs it, not because it is positive on its own.
- **A landing k can only be measured live.** This note measures signal lead, not landing slot.

## Not measured

- Landing slot or fill under the stream trigger. That needs a live build and the owner's go.
- Whether a buy sent on a processed `migrate` lands before the first swap.
- Stream behaviour over more than 6 h, or under reconnects at scale. This run had 0 reconnects and 0 decode errors.
- Helius credit cost of a permanent stream. The 6 h test used 32,923 credits by the stream's own estimate (assumed 3 per 100 KB), about 130k/day.

## Disclosure: seal exposure (2026-10-07)

- **What was exposed.** The first version of this note (head `a59ff80`), the PR #451 body and MiScusi notebook entry n_6W8jWsyz1d8aXw gave per-day early-arm outcome counts for 10-05 to 10-07: armed, fail, skipped_migrated, and a lumped `skipped_risk_or_kill`.
- **Why it matters.** The risk reasons include `daily_loss_cap`, which the DEC-016 Am.2 seal extension covers. A per-day change in that lumped count, or in armed counts, can hint at a day's P&L sign inside the sealed window.
- **What was not exposed.** No P&L, cost, exit or loss-cap-specific count was read or printed. The read lumped all risk reasons by design.
- **Effect on the read.** The 10-16 FINAL read is fully pre-registered (model, threshold, window, fail models, tools), so this cannot change it. It is recorded here and in LAB_STATE so the reader can weigh it.
- **Clean-up.** The counts were removed from this note and from the PR body. The squash merge keeps them off `main`. The notebook entry is superseded by a correction entry.
