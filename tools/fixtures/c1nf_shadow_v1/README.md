# C1-NF shadow streams, as #503 writes them

Written by `tools/c1nf_shadow.py` from PR #503 (`origin/claude/c1nf-shadow` at 8f12a9a) with its own code: `JsonlSink` (file names, envelope), `PICK_EXAMPLE` / `validate_pick` / `executor_refusal` (each pick asserted valid; the pre-window line asserted `pre_window`), `round_trip` / `pnl_variants` (the outcome's canary twin), and the record shapes of its `_resolve`, `_gap` and `heartbeat`. `contract_503.json` holds its PICK_FIELDS, OUTCOME_FIELDS, EVENT_TYPES, PREFIX, OUTCOME_START_MS, CANARY_STAKE_LAMPORTS, SEND_FEES and PRIMARY_LAT; `pick_example_503.json` its PICK_EXAMPLE.

Synthetic values (the H5 test mint and pool, the fake chain's reserves); no tape, no outcome of any real pick. Regenerate when #503's contract changes; `tools/test_c1nf_executor.py` checks #503's live module against `contract_503.json` once both are on one tree.
