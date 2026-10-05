# Kill review runbook, 2026-10-05T05:00:00Z

Single read of the 9 forward-paper books (DEC-014 with Amendments 1-3; ledger row in [HOLDOUT_LEDGER](../HOLDOUT_LEDGER.md)). Two scripts, two MiScusi jobs. Both refuse before 2026-10-05T05:00:00Z. Nobody reads the live `positions.jsonl` earlier; do not run `--help`-style dry runs against Oracle files either.

Code: `scripts/research/kill-review-1005-snapshot.sh` (mal-fast-0), `scripts/research/kill-review-1005-score.sh` (mal-research-0), shared `kill-review-1005-common.sh`. Tests: `python3 -m unittest tools.test_kill_review_scripts`.

## What the tools read (from the docs and code, not checked on the hosts)

| File | Where | Used by |
| --- | --- | --- |
| `positions.jsonl` | Oracle `/var/lib/mal/paper/forward-paper/` (live, growing) | all three tools |
| `forward-paper.json` (book configs, `slippage_cap`) | Oracle `/var/lib/mal/paper/forward-paper/` (the path `scripts/mal-core/forward-paper.sh` uses; the repo copy may differ from the live one) | all three |
| trade tape `trades-YYYY-MM-DDTHH.jsonl[.zst]` | Oracle `/var/lib/mal/sealed/trades` (`tape_dir`) | settle orphans, pressure stamp |
| creates `observe-YYYY-MM-DD.jsonl[.zst]` | Oracle `/var/lib/mal/sealed/jsonl` (`creates_dir`) | settle orphans, pressure stamp |
| `runner-restarts.jsonl` | mal-fast-0 `/home/claude/reports/` | kill_review (day annotations) |
| `settlements.jsonl`, `pressure.jsonl` | produced by steps 2 and 3 | kill_review |

Not copied, on purpose: `guard-live.json`, `invalid-for-promotion.json`, `offsets.json`, `pnl-daily.jsonl`. `kill_review` passes `--window-start` (2026-09-28) as the void-window `live_at_ms` to `position_row_counts_for_promotion`, so the void window is applied by constants (`VOID_FROM_MS`, `VOID_UNTIL`) and none of those files is read. There is no `settlements*` file on Oracle; step 2 creates it.

Sizes checked by the coordinator on 2026-10-03 (metadata only): `positions.jsonl` 405,510,671 bytes; `forward-paper.json` 2,904 bytes; Oracle sealed trades 26 GB in 391 hourly files (09-27..10-03 subset 20 GB); research-0 has 600 GB free. The newest tape hour is a plain `.jsonl` while open and the rest are `.jsonl.zst`; the snapshot handles both suffixes (a plain file gets its partial last line trimmed).

Tape copied: hours 2026-09-27T00 through 2026-10-05T04 (a day of margin before the window; later hours only hold post-instant events). Size: about 3.6 GB/day compressed (`ARTIFACTS/lab/trade-tape.md`), so about 30 GB, plus a current plain hour. `positions.jsonl` size is not in the docs. Creates files are small by comparison (size not documented). The scoring host needs `zstd` on PATH.

## Order

1. At or after 05:00:00Z, submit the snapshot job on mal-fast-0.
2. Read the snapshot job output and copy the line `MANIFEST_SHA256=<hex>` (also published as `snapshot-MANIFEST.sha256.sha256`). The job fails if any tape hour in [2026-09-28T00, 2026-10-05T04] or any creates day 09-28..10-05 is missing. `KR_ALLOW_MISSING=1` is the only override, only on the manager's decision, and it is recorded in `snap/MISSING.txt` and `snapshot-missing.txt`. Missing margin hours (09-27) only warn.
3. Submit the score job on mal-research-0 `after` the snapshot job with `KR_EXPECT_MANIFEST_SHA256=<that hex>` in its environment (required; a mismatch refuses). It verifies the manifest, then runs settle orphans, pressure stamp and `tools.kill_review --window-start 2026-09-28T00:00:00Z --window-end 2026-10-05T05:00:00Z --pressure-from-ms 1790640000000 --holm-draws 10000`.
4. Manager review (below).

## Job commands

Snapshot (`miscusi_job_submit`):
- machine `mal-fast-0`, command `bash scripts/research/kill-review-1005-snapshot.sh`
- memory under 2 GB (streaming; fast-0 refuses 2 GB or more), time limit 180 min, not resumable (it refuses to overwrite an existing snapshot; if it fails mid-copy, a rerun refuses on the non-empty `snap/` until you clear it with `chmod -R u+w snap && rm -rf snap`, run in `/data/mal/kill-review-1005` on research-0).
- Needs: `ssh mal-core-0` and `ssh mal-research-0` from the job user, and `/home/claude/reports/runner-restarts.jsonl` on fast-0.

Score:
- machine `mal-research-0`, command `bash scripts/research/kill-review-1005-score.sh` with `KR_EXPECT_MANIFEST_SHA256=<hex>`. PYTHONPATH defaults to the repo root derived from the script path, and python is `/data/mal/venv/bin/python`.
- memory 32 GB, time limit 360 min, not resumable (single read; it refuses when `out/kill_review.json` exists). Memory and time are estimates, not measurements.
- All three tools return 0 on every path, so a nonzero exit (from a tool or the script) is only ever a real error, never a verdict. The script stops at the first one and always publishes whatever logs and outputs exist (EXIT trap). `RC=` is printed and written to `kill_review.rc`.

Layout on research-0: `/data/mal/kill-review-1005/snap` (read-only after the copy, with `MANIFEST.sha256`) and `/data/mal/kill-review-1005/out`.

Published to `$MISCUSI_OUTPUT_DIR`: snapshot step: `snapshot-MANIFEST.sha256`, `snapshot-MANIFEST.sha256.sha256`, `snapshot-missing.txt` (only if inputs in the window were missing). Score step, whatever exists on success or failure: `kill_review.json`, `kill_review.md`, `OUTPUTS.sha256` (settlements, pressure, json, md), `snapshot-MANIFEST.sha256`, `settlements.jsonl`, `pressure.jsonl`, `settle.log`, `pressure.log`, `kill_review.log`, `kill_review.rc`.

## Manager checks afterwards

1. `settle.log`: orphans, settled, failed counts. Any `settle_failed` makes that book NOT_DECIDABLE.
2. `pressure.log`: `pressure_error` counts. Missing tape hours also show up here.
3. Every book's status in `kill_review.md`. List the NOT_DECIDABLE books and why (fewer than 5 pressure days, unmatched open, pressure gap). NOT_DECIDABLE is not a pass and not a measured KILL.
4. Holm results at k = 9, 10,000 draws, seed 1, under both fail models. A promote needs the base gate and Holm.
5. Run `quant-proof` on any sentence that says a book made money, then update LAB_STATE.md (numbers copied from the files, not rounded up). Add the notebook entry and `data/console.json`.
6. Set `review_windows: []` in `data/console.json`.
7. Record the snapshot manifest hash and the job ids in LAB_STATE.

## Pressure stamp memory (`KR_PRESSURE_MINT_CHUNKS`)

The pressure stamp builds a price path per mint across all tape hours; books that touch nearly every mint OOMed at 32 GB. `KR_PRESSURE_MINT_CHUNKS=N` (default 1, the reviewed behaviour) passes `--mint-chunks N` to `tools.forward_paper_pressure_stamp`: the needed mints are split by sorted index modulo N, the tape is streamed once per chunk, and only that chunk's paths are held. Output rows, order and counts are identical for any N (tested). Tape reads scale N times; try 4-8 after an OOM.
