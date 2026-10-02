# DEC-017 secondary candidates: all three fail their pre-stated screens (exploration), 2026-10-02

The owner asked for more models scored on the forward walk next to EXP-012. [DEC-017](../../DEC/DEC-017-forward-secondary-family.md) set the derivation screens before any candidate was computed. All three candidates were computed on the exploration pool only, and every try is logged in [data/tries.jsonl](../../data/tries.jsonl) (18 lines; the refit run is counted below). **None passes. No secondary is registered.** EXP-012 stays the only forward book, at k = 1.

## (b) Exit variant: FAIL

Full note: [exp012-exit-sensitivity-2026-10-02.md](exp012-exit-sensitivity-2026-10-02.md) (job #76, 17 variants).

- **Screen:** the nested leave-one-day-out advantage of choosing the exit, against `tp50_sl30`, must be > 0 under both fail models.
- **Result:** pressure −0.00412 [−0.00739, −0.00102], flat −0.00640 [−0.01103, −0.00185].
- **Paired increments:** no variant has a CI lower bound > 0.

## (a) Expanded-pool refit: FAIL

Run `9day-w1-20261002`: job #75, code from #239, the 9 days plus `explore-0814/w1`, 11,301 rows. Outputs are in [exp013-candidate-9day-w1/](exp013-candidate-9day-w1/) (screens, per-day LODO, manifest with the sha256 of every input view). This was one refit run.

DEC-017 §5 requires the September-only **and** fast-only screens to pass. "Fast-only" was defined in the tool before this run as pool A alone: the 3 fast-listener days, own LODO, own p90.

| Screen | n | selected | press mean | press CI90 | press ex-top-3 |
| --- | ---: | ---: | ---: | --- | ---: |
| september_only | 8801 | 881 | +0.0232 | [+0.0154, +0.0309] | +19.0392 |
| **fast_only (pool A)** | 3092 | 310 | **+0.0005** | **[−0.0137, +0.0137]** | **−1.0103** |
| getblock_only (August w1) | 2500 | 251 | +0.0165 | [+0.0018, +0.0303] | +3.1732 |
| august → september | 8801 | 1026 | +0.0116 | [+0.0051, +0.0181] | +10.6818 |
| september → august | 2500 | 257 | +0.0128 | [−0.0029, +0.0277] | +2.0323 |

- **The fast-only screen fails.** It is weak: each fold trains on 2 days, about 2,000 rows. Its definition was fixed before the result, though, so it is not reinterpreted now.
- **The refit route is closed.** Adding more August days cannot change pool A, so no refit can pass this screen.
- **Overlap with EXP-012:** the candidate's selected set is 74.27% inside EXP-012's (Jaccard 0.5878).
- **August days are weaker than September** in the per-day LODO (pressure +0.0084, +0.0017, +0.0187), which supports the period-regime caution.

## (c) Looser threshold: FAIL

Pre-committed before computing:
- `t_loose` = the 80th percentile of EXP-012's pooled OOF scores, using the same non-interpolating rule as the frozen 90th. That gives `0.7313079140081313`.
- The tested quantity is the band `[t_loose, t_frozen)` on its own.
- One try, from the latency run's k = 1 rows and the stored OOF scores. Script and output are in [exp012-band/](exp012-band/).

| Book | n | flat mean | flat CI90 | press mean | press CI90 | press ex-top-3 |
| --- | ---: | ---: | --- | ---: | --- | ---: |
| **band [p80, p90)** | 880 | **−0.00415** | [−0.01461, +0.00669] | **−0.00312** | [−0.01022, +0.00426] | −4.0708 |
| full ≥ p80 | 1761 | +0.01647 | [+0.00913, +0.02427] | +0.01003 | [+0.00512, +0.01519] | +16.2320 |
| EXP-012 as frozen (≥ p90) | 881 | +0.03707 | [+0.02577, +0.04845] | +0.02315 | [+0.01543, +0.03088] | +19.0392 |

The marginal band loses on average. Widening to the 80th percentile **lowers** total pressure SOL, from 20.397 to 17.655, so the frozen threshold already sits where the extra trades stop paying.

## What this says

- **EXP-012's frozen recipe holds up on all three axes tried here**, all on exploration data: its exit, its threshold, and (on September data) its training pool. Variants of it don't add value that survives an honest screen.
- **Using the forward data more fully needs a genuinely different signal**, such as another trigger or feature family, derived on the expansion pool. Variations of EXP-012 don't qualify. Any such model misses the 2026-10-05T23:59Z deadline, so per DEC-017 §2 it needs a new walk window and a new DEC.
