# Resume step 3: declaration of the A3 run after the re-pin

Written 2026-10-13T08:21:00Z (`date -u`), before the run. DEC-024 Amendment 7 item 2, resume step 3; acceptance test from quant-proof, PR #629 comment 6140926919 section 5, as changed by comment 6141362129 section 5.

- **Commit.** main `ddc7e1f306e22925eb231be987c0d32236efb38c` (the #629 merge). Monitor blob `1ca0a88cecf0853d94336ea046ba1a910b79f198`, pins blob `cc79c0366e4b3ec3076d60ce9f31811d7fca9bf6`; the job prints both before the run.
- **Command.** `/data/mal/venv/bin/python -m tools.pump_structure_monitor --n 40 --max-calls 900 --out /data/mal/structure-monitor/resume-step3-1013.jsonl`. `--n 40` because at the default 20 `boost.n_profiled` was below 15 in 3 of the 7 rows of `daily.jsonl`.
- **`--out` is a scratch file, not the official `daily.jsonl`.** The daily series keeps one record per daily run; EXP-022's P2 reads that series. This run is not a daily run and is not the P2 run.
- **One run.** A run in which any of the five flags or `program_changed` fired is a halt, not a failed attempt: no re-run to clear it. A run that fails only lines 4 to 6 below, or has an unevaluated flag, does not count; one further run is allowed with a larger `--n` declared before it starts. Every run is recorded in LAB_STATE.
- **The run counts as step 3 only if every line holds, read from its own record:**
  1. The job starts after the #629 merge, at the merge commit, with the two blobs above.
  2. For each of `pins_changed`, `boost_disabled`, `boost_share_low`, `boost_last_slice_early`, `boost_budget_or_slices_changed`: `halt.flags[f].evaluated` is true and `halt.flags[f].halt` is false, read one by one.
  3. `program_changed`: evaluated true and warn false.
  4. `graduations.slot_min` > 456132171.
  5. `graduations.boost_denominator.n_nonmayhem_wsol` >= 15, `boost.n_profiled` >= 15 and `boost.last_slice_after_migrate_s.n` >= 15. `boost.n_pools_skipped` and `boost.last_slice_unverified` are recorded.
  6. `boost_share_low`'s reason starts "InitBoost on" (the non-mayhem basis).
  7. `boost.last_slice_after_migrate_s.median` >= 330; ms per slot inside [150, 450]; `n_init_boost` equals `n_nonmayhem_wsol` (if not: no resume, report to quant-proof).
  8. `synthetic_share_high` is recorded and reported; it does not decide for the canary and is still a halt flag for EXP-022.
- **Report-only, copied into LAB_STATE:** `boost.slices` and `boost.last_slice_after_migrate_s` (n, min, median, max); count B (pools with a verified last slice later than 400 s; 0 if the record's last-slice max is 400 or less).
- **This run does not remove STOP.** STOP stays until every item of quant-proof's pre-removal checklist holds, the owner's dated line on the slices band included.
