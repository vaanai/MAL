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
| `confirmation-oneshot` | owner == the job's own `exp_id` (e.g. `EXP-011`), passed explicitly -- never guessed; or, for the hours of its range only, a [second owner](#second-owners) of the block |
| `ops` | nothing historical -- ops jobs don't read research data |

`unassigned` and `kill-review` blocks deny every role. An unrecognized role
or owner also denies -- the guard fails closed.

## Precedence

An explicit single-hour row (a disclosed exception, e.g. the "Fast EXP-009
exclusion" row) overrides the broader block that contains it for exactly
those hours. Two ranged blocks may never overlap on the same host --
`parse_ledger` raises if the ledger ever implies one hour has two owners.

## Second owners

A ledger row's Status cell may carry machine markers that disclose a second
reader of part of an `EXP-###`-owned block (ledger rule 3):

```
SECOND-OWNER: EXP-022 [2026-10-09T00, 2026-10-16T01)
```

The form is exact: one space after the colon and after the id, a half-open
UTC-hour range. Several markers per cell are allowed. The block's `owner`
does not change; the owner of record keeps its access. For
`role=confirmation-oneshot` with exactly that `exp_id`, the hours of the range
(and only those, and only on that block) pass `check_read`. `exploration`,
`ops`, every other `exp_id` and every hour outside the range are denied as
before. An explicit single-hour row inside the range still overrides the block,
so the marker does not apply to that hour.

`parse_ledger` raises, and the ledger is refused, if the token `SECOND-OWNER`
(any case) appears anywhere in a Status cell without being a well-formed
marker, if a range is empty or outside the block's own Hours, if the block is
not owned by an `EXP-###`, or if the second owner is the owner itself. Keep the
token out of Status prose.

The guard is by hour only. It does not model "sealed until X's FINAL is
written" or "one read": the tool that opens the hours enforces those.
`data/catalog.json` gets a `second_owners` list on a block only when the block
has one.

## What the catalog is for

The Console's Data screen reads `data/catalog.json` to show block owners,
hour ranges and walker progress. It is a snapshot, rebuilt on demand; it is
never the thing a job runner asks permission from live -- that's
`check_read` against a freshly parsed ledger, so a ledger edit takes effect
immediately without a rebuild step in between.

## data/console.json

`data/console.json` (`console.v1`) is the other file the Console reads from this repo. It's maintained by the manager session only. It holds the review windows (which drive server-side P&L blinding), the upcoming events, the Edge ladder, and the Claude stream. It records decisions that are already in GitHub, and it never grants data access.
