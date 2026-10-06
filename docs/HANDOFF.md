# Manager handoff 2026-10-06 ~17:45Z (manager8 → next manager)

Replace this page at the next handoff; don't append. Read it first. Then read:
- [LAB_STATE.md](../LAB_STATE.md), as of 17:15Z;
- DEC-016 Amendment 5 **§7** (the 10-16 (B) rules);
- DEC-019 (with the 10-06 §7 note);
- DEC-020 §7 and DEC-021;
- the [EXP-016 plan](../EXP/EXP-016-rug-veto-plan.md) §13, items 1–13;
- these memory notes: `state-2026-10-04`, `feedback-real-layout-precount`, `feedback-realistic-primary`, `rug-filter-priority`, `pumpswap-virtual-reserve`.

## First things to do

1. **Become the inbox reader:** `miscusi_worker_start` with `inbox: true`.
2. **Recreate two session crons.** Crons die with the session.
   - **Hourly probe monitor at :17.** Job on `mal-fast-0`, role ops, 200 MB, 3 min. Use the same command as job #300. Alert if realized ≤ −0.20, the executor isn't active, the build is not faa3192, a halt/stop file exists, a position is stuck or abandoned, or there are errors.
   - **Daily tip-tape archive at 03:23Z.** Job on `mal-fast-0`, 800 MB, 240 min. Use the same command as job #304. It zstd-compresses each closed hour, sha256-verifies it, and copies it to research-0 `/data/mal/tip-tape-archive/`. It deletes nothing. This must keep running: fast-0 retention starts deleting tape at 10-08 06Z, and the DEC-019 §7 lab note needs every probe hour.
3. **Check #417 and job #307** (EXP-016, below).

## Owner direction (10-06)

- **Push to profit fast, with no setbacks.**
  - Every critical-path merge goes through quant-proof.
  - **Every try-spending run first gets a count-only pass on the real data layout.** Today that pass caught 4 loader bugs that fixtures missed.
- **Challengers: the manager's answer, given to the owner.**
  - Widen the cheap screen stage to 6–10 ideas per batch.
  - Keep confirmation one-shot.
  - Keep forward races at 3 or fewer.
  - Buy more fresh confirmation blocks with Helius credits.
  - New edge comes from new information, not retrains.
- **The owner's external reviewers are Helm, Lyra and Warden.** Their reports have been right. Verify their claims, then act.

## 10-16 FINAL read: every tool is merged

- **The rule.** DEC-016 Am.5 §7 is complete: #400, #403, #407, #409, plus the records #410 and #412.
  - V0 changes only on LP Deposit/Withdraw: V0 ← floor(V0·S_after/S_before).
  - Each trade is priced at its entry-slot V0, taking the worse of entry and exit when an LP event falls inside the hold.
  - Candidates are per trade. The entered set is (A)'s. This applies at every k.
  - Unexplained or unresolved pools above 0.1% → refuse.
- **The tools.**
  - #402 LP history and merge (`4c5da8c`);
  - #405 vbook (`5da3349`);
  - #411 sensitivity at k(p50)/k(p90) (`3355ad1`).
- **Still to do before 10-16:**
  - **(e′) dry-run merge before 10-15T23:00Z.** Use `merge --dry-run` with the latest snapshot standing in for the final; it needs `--snapshot-fetch` for snapshots #2 and #3.
  - **More V snapshots** every 1–2 days until 10-15. Use the same command as job #286. New snapshots carry `.fetch.json` sidecars.
  - **DEC-016 Am.3 inputs:** write in k(p50)/k(p90) and the slot_ms export sha, and the runner downtime file.
- **The 10-16 order is in DEC-016 Am.5 §7** and in the previous version of this file (#413).
  - lphist-entered runs once; only the first completed run counts.
  - vbook and sensitivity both take `--vmap-merge-meta --final-fetch-map --snapshot … --lphist …`.
- **One decision is the owner's** (in the Console's for_you). The ceiling counts only *unexplained* V0 moves. The owner had asked for a refusal "if too many move". The owner may add a ceiling on explained moves before 10-16.

## EXP-016 rug veto (the owner's priority): in flight

- **Merged:** #395, #404, #406, #408 (tries sync), #414 (ignorable listener keys).
- **PR #417 (open, under quant-proof).** The loader read only `migration` rows: P1A kept 134 of 3,468 migrated mints. #417 changes it:
  - the migrated set is now the `complete` rows, as in EXP-015;
  - complete-only mints get `canonical_pool(mint)` as their pool;
  - two new limits: cells ≥ 90% of migrated mints, and with-create ≥ 80%;
  - the slot rule is explicit (feature cutoff = migration-row/complete slot; simulator clock = first print on the pool).
  - **Quant-proof at 880797e: CHANGES.** Sent to the builder at ~17:50Z.
    - **Required:** for complete-only mints, cutoff = complete slot + 1, clamped to the simulator clock. Otherwise the curve-completing buy is dropped, against plan §3. Disclose it in item 13.
    - **New counts:** mints with a `migration` row but no `complete` row; OOF-scored P1 mints with no EXP-016 cell, as a limit (more than 5% → refuse).
    - **Optional:** NO_SIM and foreign-first split by complete-only vs migration-row.
    - **Fixed at `1f9ae8f`** (~18:00Z, 232 tests passed): cutoff = complete slot + 1, `migration_no_complete`, LIMIT_OOF_NO_CELL 5% pooled over P1, origin split, and guards progress lines. Quant-proof re-review was requested. **If there's no OK verdict on the PR, rerun quant-proof on 1f9ae8f, then merge.** Job #307's precount runs on 880797e. It has the old cutoff, but the counts are still valid. Rerun the precount on the merged head before the pin.
- **Job #307: `--precount` at 880797e (#417 head).** Real layout, all sources, no lock and no tries. Log: `/data/mal/ops/exp016-precount7.log`. Counts land in `/data/mal/exp016-precount-*/precount.json`.
  - Memory is now about 3–3.5 GB per P1 source: compact rows at 245 B per row, 0 rows stored whole.
  - Check every per-source count and `would_refuse`. Where a data-quality limit doesn't fit honest counts, record a dated §13 note before the pin.
- **Next steps, in order:**
  1. Merge #417 after quant-proof OK.
  2. Get a clean precount (no would-refuse, counts plausible against EXP-015's per-source universe).
  3. Pin `VMAP_EXP016_SHA256` = `1f3e772d12cedbdb2dd860f619361fc0fdc88872fd5fa68639a11f91945162ec` (`/data/mal/pumpswap-virtual/pool_v_exp016.json`, job #289) in a reviewed commit.
  4. Run `--emit-constancy-sample`.
  5. Run the `tools.exp016_constancy` job at 5 rps or less.
  6. Run `--guards-only`, then a precount with the constancy file.
  7. Run the screen as one 48 GB job (6 tries).
- **Expected constancy refusal on honest data:** about 0.1%.
- **Confirmation block fresh-0802:** walkers #248 and #249 are running and #250 is queued. It is reserved for EXP-016.

## Live probe (real money; DEC-019)

- **Status:** 57/90 attempts, realized −0.167875 SOL, faa3192. Paused by the paper ledger's daily cap since 08:13Z; it resumes at 00:00Z. Hard end 10-12T00Z.
- **Measured today (notebook):**
  - live k_mig p50 5, p90 6 (job #285);
  - faa3192 is −0.014 SOL over 28 trips, which is +0.0143 before tx fees;
  - the true round-trip cost is ≈4.5% of size at 0.05 SOL, ≈2.9% at 0.25 and ≈2.65% at 0.5. Pool fees of about 2.4–2.5% are the floor. The 1,513,840 per buy is refundable ATA rent (jobs #305 and #306).
- **For the trial config (DEC-020 package):** `max_signal_age_s` is 120. Tighten it, and add a maximum-k guard. A signal decided 6.2 s late was traded.
- **Security: #415 is merged (`fd85b2f`).** `probe_withdraw` only sends to the owner-confirmed `5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi`. **Helm must reinstall the pinned tool with a new manifest.** An address-poisoning dust transfer hit the wallet at 10-06T03:01:52Z.

## Ops (10-06)

- **fast-0 tip follower:** MemoryMax raised from 1G to 2G with `set-property`. The drop-in is persistent and the service did not restart (job #303).
- **Requests waiting on the owner/Helm** (notebook, ~17:20Z):
  - stop the Oracle attn book and observe.attention, and retire the V-less books;
  - a research-0 retention plan that frees about 135 G: kill-review snap, raw copies of spent blocks, `raw/` P1. Delete nothing without the owner/Helm OK.
- **Heavy jobs go on research-0 only.** fast-0 takes ≤1.9 GB. Decline fast-0 "idle" notices.
- **GitHub hiccups.** `gh pr merge` and `git fetch` hung twice today. Use `timeout 60 … </dev/null` and retry.

## Clocks

| When | What |
|---|---|
| By 10-09T15Z | Resubmit forward walk #71 with params `{"start":"2026-10-02T15"}`, resumable, 10080 min. |
| ~10-07T18Z | Early-arm shadow read. Also evaluate the processed `migrate` trigger (264/287, about 1 s lead), and fix its tx-v0 request. |
| 10-12T00Z | Probe end, then the DEC-019 §7 lab note. Group by build, flag the hour-of-day bias, include the fee split. |
| Before 10-15T23Z | (e′) dry run. |
| ~10-16T02Z | FINAL. |
