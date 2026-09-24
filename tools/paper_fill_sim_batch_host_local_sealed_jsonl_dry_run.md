# Fill-sim batch host-local sealed JSONL dry-run shape (`mal-core-vnic`)

Proposed registration `paper-fill-sim-batch-host-local-sealed-jsonl-dry-run-incomplete-rpc-v0`. Operator-local command shape for a dry-run receipt around the merged **fill-sim** parent CLI ([PR #59](https://github.com/vaanai/MAL/pull/59)).

**No host run is recorded here.** Checked-in files under `fixtures/paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0/` are receipts only. This module does not SSH and does not open `/var/lib/mal` (including `//var/lib/mal` and relative `var/lib/mal/...` before any filesystem touch).

**Hard caps:** paper only. `dual_read.sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays `false`. `measure.kind` stays `none`. `input.marks_joined` stays `false`. Graph stays cold. DEC-007 both arms unchanged. `observe/client.py` untouched.

## Host paths (flags only)

| Path | Role |
| --- | --- |
| `/var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl` | Sealed observe day for operator **parent** CLI |
| `/var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl` | Same |
| `/var/lib/mal/paper/paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-20_expectation.json` | Synthetic incomplete checklist |
| `/var/lib/mal/paper/paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-21_expectation.json` | Same for day 21 |

## Operator command shape (parent CLI, on the host)

```bash
python -m tools.paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0 batch \
  --fixture-origin sealed_row_projection \
  --jsonl /var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl \
  --jsonl /var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl \
  --expectation /var/lib/mal/paper/paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-20_expectation.json \
  --expectation /var/lib/mal/paper/paper-fill-sim-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-21_expectation.json
```

Do not commit that process stdout.

## Receipt CLI (this stamp)

```bash
python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which synthetic-replay
python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which projection-on-synthetic
python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 example --which operator-declared
python -m tools.paper_fill_sim_batch_host_local_sealed_jsonl_dry_run_incomplete_rpc_v0 receipt \
  --fixture-origin sealed_row_projection \
  --jsonl fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-20.jsonl \
  --jsonl fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-21.jsonl \
  --expectation fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json \
  --expectation fixtures/paper_fill_sim_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json
```
