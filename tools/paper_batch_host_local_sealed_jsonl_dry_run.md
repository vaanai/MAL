# Host-local sealed JSONL dry-run shape (`mal-core-vnic`)

Proposed registration `paper-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0`. This note is the **operator-local command shape** for a dry-run receipt around the merged parent CLI.

**No host run is recorded here.** No host stdout is checked in. Checked-in files under `fixtures/paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/` are receipts. Cloud agents and CI do not read host Oracle JSONL. This module does not SSH and does not open `/var/lib/mal`.

**Hard caps:** paper only. `dual_read.sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays `false`. `measure.kind` stays `none`. `host_jsonl_read=true` on a receipt does not close the book. No live capital, no trading keys, no PumpPortal trade API. No `observe/client.py` change. Graph stays cold. DEC-007 both arms unchanged. `global_95bps` and `launchlab_init` stay Proposed.

Parent flag note, not rewritten: [paper_batch_oracle_run.md](paper_batch_oracle_run.md).

## Host paths (flags only)

| Path | Role |
| --- | --- |
| `/var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl` | Sealed observe day, read locally by an operator if they choose to run the **parent** CLI |
| `/var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl` | Sealed observe day, same rule |
| `/var/lib/mal/paper/paper-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-20_expectation.json` | Operator expectation checklist. Must stay a synthetic incomplete checklist |
| `/var/lib/mal/paper/paper-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-21_expectation.json` | Same rule for day 21 |

The receipt CLI records those strings on `example --which operator-declared` and refuses to open them. A host path does not turn the receipt into a closed book.

## Operator command shape (parent CLI, on the host)

From a checkout on the host (no secrets in the command). This is the parent CLI. It is not invoked by CI in this registration:

```bash
python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 batch \
  --fixture-origin sealed_row_projection \
  --jsonl /var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl \
  --jsonl /var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl \
  --expectation /var/lib/mal/paper/paper-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-20_expectation.json \
  --expectation /var/lib/mal/paper/paper-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-21_expectation.json
```

`--fixture-origin sealed_row_projection` is what the parent stamp defines as `input.host_jsonl_read=true` for **that** process. It does not set `honesty.executed_host_sealed_book`, `honesty.scored_oracle_measure`, or `dual_read.closed_book_claim`. Do not commit that process's stdout.

## Receipt CLI (this stamp)

Checked-in replay, from the repo root. Prints a receipt, not a parent batch body:

```bash
python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which synthetic-replay
python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which projection-on-synthetic
python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which operator-declared
python -m tools.paper_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 receipt \
  --fixture-origin sealed_row_projection \
  --jsonl fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-20.jsonl \
  --jsonl fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-21.jsonl \
  --expectation fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json \
  --expectation fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json
```

`projection-on-synthetic` sets `host_jsonl_read=true` by calling the parent on the synthetic fixtures. The book stays `incomplete`. `operator-declared` stores the host path strings with `parent_invoked=false` and `var_lib_mal_opened=false`.
