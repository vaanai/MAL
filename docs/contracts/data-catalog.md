# Data catalog contract

The catalog (`data/catalog.json`) and the read guard are generated code, not
a second source of truth. **[docs/HOLDOUT_LEDGER.md](../HOLDOUT_LEDGER.md)**,
edited only by the manager, is the only place a block's owner is decided.
`tools/mal_catalog.py` parses that file; it never grants a read the ledger
doesn't already grant. If the two disagree, fix the parser.

## Build

```
python3 -m tools.mal_catalog build --ledger docs/HOLDOUT_LEDGER.md --out data/catalog.json \
  [--checkpoint walker_b=/var/lib/mal/backfill-fast-b --checkpoint walker_c=/var/lib/mal/backfill-fast-c]
```

Without `--checkpoint`, `walkers` is empty. Walker progress is host state
(the checkpoint files), not ledger state -- the committed `data/catalog.json`
carries none, on purpose.

## Read guard

Every job runner reads a block through `check_read(blocks, role, host,
start_hour, end_hour_exclusive, exp_id=None) -> (ok, reasons)` before it
opens a single sealed-hour file. Any hour not covered by a ledger block is
denied ("not in ledger") -- there is no implicit allow.

```
python3 -m tools.mal_catalog check --role exploration --host fast --start 2026-09-20T00 --end 2026-09-21T00
```

## Access table

| Role | May read |
| --- | --- |
| `exploration` | owner == `exploration-pool` only |
| `confirmation-oneshot` | owner == the job's own `exp_id` (e.g. `EXP-011`), passed explicitly -- never guessed |
| `ops` | nothing historical -- ops jobs don't read research data |

`unassigned` and `kill-review` blocks deny every role. An unrecognized role
or owner also denies -- the guard fails closed.

## Precedence

An explicit single-hour row (a disclosed exception, e.g. the "Fast EXP-009
exclusion" row) overrides the broader block that contains it for exactly
those hours. Two ranged blocks may never overlap on the same host --
`parse_ledger` raises if the ledger ever implies one hour has two owners.

## What the catalog is for

The Console's Data screen reads `data/catalog.json` to show block owners,
hour ranges and walker progress. It is a snapshot, rebuilt on demand; it is
never the thing a job runner asks permission from live -- that's
`check_read` against a freshly parsed ledger, so a ledger edit takes effect
immediately without a rebuild step in between.
