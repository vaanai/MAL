# Exploration pool deduplicated on mal-research-0 (2026-10-01)

Exact-duplicate rows were removed from the three exploration-pool blocks before any further exploration run reads them. Source: MiScusi job #1 `j_1JgjH_GG0uL02A` on mal-research-0, code `a763923`, `tools/backfill_verify.py --content --dedupe-out` per block; the Oracle live tape's daily `creates/observe-<day>.jsonl` deduplicated with `awk '!seen[$0]++'`. Raw input: `/data/mal/raw/<block>` (sha256-verified copies, `SOURCE.sha256`). Output: `/data/mal/clean/<block>`, each with `MANIFEST.sha256`. Per-hour JSON: [dedupe-exploration-pool-2026-10-01/](dedupe-exploration-pool-2026-10-01/).

## Totals (rows, unique, removed)

| Block | Hours | Trades | Creates | Migrations |
| --- | ---: | --- | --- | --- |
| Fast pool `[2026-09-18T23, 2026-09-22T00)` | 73 | 82,608,216 → 80,140,807 (−2,467,409) | 102,227 → 99,127 (−3,100) | 3,699 → 3,602 (−97) |
| Oracle in-sample `[2026-09-22T00, 2026-09-25T07)` | 79 | 83,604,548 → 83,604,548 (0) | 110,942 (0) | 3,625 (0) |
| Oracle live tape `[2026-09-25T07, 2026-09-28T00)` | 65 | 60,394,256 → 60,389,526 (−4,730) | daily observe files: 0 removed (31,722 / 37,389 / 34,199 / 30,931) | no migrations dir |

Fast duplicates sit in exactly three hours, all resumed hours: 2026-09-19T16 (−973,996 trades, −1,128 creates, −37 migrations), T17 (−475,686, −622, −15), T20 (−1,017,727, −1,350, −45). These match the census in `/home/claude/data/dup-census/census.json` (~974k / 476k / 1.02M). Oracle live loses 5–326 trade rows in every one of its 65 hours.

`backfill_verify` exit codes: fast 1 (the 3 duplicate hours), in-sample 0, live 1 (every hour has a few duplicates). The flagged hours are the ones deduplicated, not missing data: no hour in any block reported a missing or unsealed file.

## What this means for earlier results

The 97 duplicated fast migrations matter for every migrate-entry study that read pool A (B2, B3, the 972-cell grid's fast hours): a duplicated migration can be scored as a second entry. Earlier pool-A numbers are therefore on dirty data. The B3 lane is being re-run on the clean pool with its original settings (`--max-workers 3`, same chunking, same pre-stated screen); the comparison goes in its own note. Nothing here is a performance claim.

## Manifests (sha256 of each `MANIFEST.sha256`)

- `fast-pool-2026-09-18T23_2026-09-22T00`: `7c3c707174d91a154d36aa0b5b0345aaf00af526b608d92705b0ce266abe24dd` (220 files)
- `oracle-insample-2026-09-22_25`: `8cbe2acc395254fcc32bb9a96fea606fb782dd0dc61d5de1c893b7892cc48a15` (238 files)
- `oracle-live-2026-09-25_27`: `1e7d3fecbd003bcbc7e5de57a472986e9278d5dd307729658ce7426cc19c9d51` (70 files)
