# R2b declaration: every-slice count on the other 25 post-upgrade pools

Written 2026-10-13T08:21:00Z (`date -u`), before any run of `r2_slices.py` on these pools. Quant-proof's delta ruling (PR #629 comment 6141362129, ruling (b)) allows it in place of a third option in the owner question: "all 25 with no selection".

- **Why.** The owner decides the "Slices per pool: 29–30" band. Exact keeper slice counts exist for 15 of the 40 post-upgrade pools (R2, job #913: 28 on 3, 29 on 6, 30 on 6). For the other 25 only the signature-list count exists (29 on 18, 30 on 7), an upper bound. This run counts them one by one so the question carries exact counts over all 40.
- **Pools.** All 25 eligible post-upgrade pools of `raw/s04_post_new.json` and `raw/s04_post_early.json` that are not in R2's list (eligible: migrate slot > 456132171, WSOL quote, InitBoost, not mayhem, profiled), ordered by migrate slot descending, none left out: `/data/mal/hunt-1008/h5-work/program-upgrade-1013-r12/r2b-pools.json`, sha256 `0fc3e3a5a7daa20b253e66d2133993e064c50ad1d803cc3e3c73c7bb6e54ae1d`.
- **Script.** `/data/mal/hunt-1008/h5-work/program-upgrade-1013/r2_slices.py`, sha256 `03a03614067882e8e31c5786c57c34377c17482a18c7807ef6546a2b69ae2cb3`, the same file as R2 (quant-proof ruled the runner acceptable, deviation recorded).
- **Reported.** Per pool as in R2. Then, over all 40 pools (R2's 15 and these 25): the number of pools at each exact keeper slice count, the number outside 29–30, and the median of the exact counts.
- **Test (the runner's `R2_TEST`).** Clean only if all 25 are fully fetched, no BoostBuyAndBurn transaction is not keeper-signed, and zero-fee equals keeper-signed. Not clean goes to the owner with the question.
- **No result of this run changes which reading governs.** The per-pool reading governs until the owner's dated line (quant-proof, comment 6140926919). The counts are reported as they come; no pool is added, dropped or replaced after the run.
- **Run.** One MiScusi job on mal-research-0, public RPC, fresh cache directory, `--max-calls 1500`. The job prints this file's sha256 before the runner starts.
- **If the run does not finish.** A run that stops before printing `R2_TEST` is not a result; one further run is allowed with `--max-calls 3000` and a new fresh cache.
- Outcome-blind: chain data only.
