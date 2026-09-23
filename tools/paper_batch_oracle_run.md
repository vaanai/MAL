# Paper-batch Oracle invocation shape (`mal-core-vnic`)

Proposed registration `paper-batch-oracle-sealed-day-incomplete-rpc-v0`. This note is the **operator-local command shape** only.

**No host run is recorded here.** Checked-in fixtures under `fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/` are synthetic calendar labels `2026-09-20` and `2026-09-21`. Cloud agents and CI cannot read host Oracle JSONL. Do not treat a merge of this registration as a sealed-book score.

**Hard caps:** paper only. `dual_read.sealed_book_rpc_slice` stays `incomplete`. `closed_book_claim` stays `false`. `measure.kind` stays `none`. No live capital, no trading keys, no PumpPortal trade API. No `observe/client.py` change. Graph stays cold. `global_95bps` and `launchlab_init` stay Proposed.

## Host paths (flags only)

| Path | Role |
| --- | --- |
| `/var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl` | Sealed observe day, read locally by an operator if they choose to run |
| `/var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl` | Sealed observe day, same rule |
| `/var/lib/mal/paper/paper-batch-oracle-sealed-day-incomplete-rpc-v0/` | Operator output directory. Not created by this registration |

The expectation files must still validate as **synthetic** scoreboard checklists with `sealed_book_rpc_slice=incomplete` and `closed_book_claim=false`. A host path does not turn that checklist into a closed book.

## Command shape

From a checkout on the host (no secrets in the command):

```bash
python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 batch \
  --fixture-origin sealed_row_projection \
  --jsonl /var/lib/mal/sealed/jsonl/observe-2026-09-20.jsonl \
  --jsonl /var/lib/mal/sealed/jsonl/observe-2026-09-21.jsonl \
  --expectation /var/lib/mal/paper/paper-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-20_expectation.json \
  --expectation /var/lib/mal/paper/paper-batch-oracle-sealed-day-incomplete-rpc-v0/sealed_day_2026-09-21_expectation.json
```

`--fixture-origin sealed_row_projection` sets `input.host_jsonl_read=true` on that process's stdout. It does not set `honesty.executed_host_sealed_book`, `honesty.scored_oracle_measure`, or `dual_read.closed_book_claim`. Basename of each `--jsonl` must be `observe-YYYY-MM-DD.jsonl`, and each row's `t_ws` UTC day must match that name.

Checked-in replay, synthetic origin, from the repo root:

```bash
python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 example --which two-day
python -m tools.paper_batch_oracle_sealed_day_incomplete_rpc_v0 batch \
  --jsonl fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-20.jsonl \
  --jsonl fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/observe-2026-09-21.jsonl \
  --expectation fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-20_expectation.json \
  --expectation fixtures/paper_batch_oracle_sealed_day_incomplete_rpc_v0/sealed_day_2026-09-21_expectation.json
```
