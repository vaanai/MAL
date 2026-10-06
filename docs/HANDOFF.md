# Manager handoff 2026-10-06 ~21:40Z (manager9 → next manager)

Replace this page at the next handoff; don't append. Read it first. Then read:
- [LAB_STATE.md](../LAB_STATE.md);
- DEC-016 Amendment 5 **§7** (the 10-16 (B) rules);
- DEC-019 (with the 10-06 §7 note);
- DEC-020 §7 and DEC-021;
- the [EXP-016 plan](../EXP/EXP-016-rug-veto-plan.md) §13;
- the EXP-017 and EXP-020 plans (sized re-sims in flight);
- these memory notes: `state-2026-10-04`, `feedback-real-layout-precount`, `feedback-realistic-primary`, `rug-filter-priority`, `pumpswap-virtual-reserve`, `profit-focus`.

manager8 stood down at about 19:40Z. Its crons are deleted.

## First things to do

1. **Become the inbox reader:** `miscusi_worker_start` with `inbox: true`.
2. **Recreate two session crons.** Crons die with the session.
   - **Hourly probe monitor at :17.** Job on `mal-fast-0`, role ops, 200 MB, 3 min. Use the same command as job #331 (or #300). Alert if realized ≤ −0.20, the executor isn't active, the build is not faa3192, a halt/stop file exists, a position is stuck or abandoned, or there are errors.
   - **Daily tip-tape archive at 03:23Z.** Job on `mal-fast-0`, 800 MB, 240 min. Use the same command as job #304. It zstd-compresses each closed hour, sha256-verifies it, and copies it to research-0 `/data/mal/tip-tape-archive/`. It deletes nothing. This must keep running: fast-0 retention starts deleting tape at 10-08 06Z.
3. **Check the jobs in flight** (below).
4. **Watch MiScusi memory on research-0.** It schedules on declared memory, not on use. Ollama is off (Helm, 10-06) and stays off as long as we need. Restart command: `cd /opt/miscusi && sudo docker compose start ollama`.


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
- **Settled (Helm and owner, 10-06):** the ceiling stays as merged. It counts only *unexplained* V0 moves; there is no ceiling on explained LP-rescale moves.

## Research state (10-06 evening, manager9)

**Reading so far.** Five filters on frozen EXP-012's picks have now failed at realistic costs:
- EXP-013;
- EXP-015;
- #191;
- **EXP-018**, causal wallet skill: job #325, paired p 0.46, W2 book flat −0.00102 SOL/trade (lab note `exp018-screen-2026-10-06.md`);
- **EXP-019**, post-migration momentum confirm at k8: job #328, paired −0.00053, p 0.97 (lab note `exp019-screen-2026-10-06.md`).

The wave-3 planner (notebook, about 21Z) says the most realistic profit path is **size and entry speed on the existing book**. At 0.05 SOL the book is about +1.1% of stake before fixed fees and −0.88% after. Fixed fees are 2.02% of stake at 0.05 SOL and 0.20% at 0.5. Capacity is about 87 trades/day. New families have priors ≤10%.

**In flight, in order of importance:**
1. **EXP-017** (#423 merged, 218dac9). Cells: H3 regime gate and H4 score sizing (Holm k=2), plus C0, report-only, at 0.10/0.25/0.5 SOL.
   - Re-sim **job #319** at f7d0ac1 writes a sealed 0400 cache to `/data/mal/exp017-resim-20261006T2004Z/sized_cache` and is unread. Its pre-`started` check compares the 0.05 control's hash with the EXP-015 cache.
   - Next: a plan amendment line `SIZED_MANIFEST_SHA256 = <sha from the job log>`, merged. Then run the screen once: `python -m tools.exp017_screen --sized-cache <dir>/sized_cache --out-dir /data/mal/exp017-screen --tries-log /data/mal/ops/tries-exp017-screen.jsonl`.
   - H4 counts only if it beats uniform 0.10 on both legs.
2. **EXP-020**, the report-only grid, k {2,3,4,6} × stake {0.25,0.5,1.0} (#429 merged, c27d9ef).
   - **Job #330** is queued after #319: precount, guards, then re-sim into `/data/mal/exp020-grid`.
   - Next: a plan line `GRID_MANIFEST_SHA256 = …`, then `--report`.
   - The entry bound is start-of-slot, so small-k gains are upper bounds.
3. **EXP-016 rug veto.**
   - #426 merged (9afa747): indexed BlockHistory plus forked cells. P1A cells used to take more than 60 min; they now finish in under 20.
   - **Precount #10 is job #326** at 5cb4e17.
   - Next, from the HANDOFF of 17:45Z, which still applies:
     - check `oof_without_cell` and `cutoff_clamped`;
     - the quant-proof LIMIT_OOF_NO_CELL split, with a §13 note;
     - pin `VMAP_EXP016_SHA256` = `1f3e772d12cedbdb2dd860f619361fc0fdc88872fd5fa68639a11f91945162ec`;
     - constancy sample, then the constancy job, guards-only, and precount with constancy;
     - the screen as one 56 GB job.
4. **EXP-014 v2**, the mig+15 selector (PR #430; #269 closed). Quant-proof review is in progress, with the builder's open questions on bars 2/3/6. Then precount, then screen (1 try).
5. **Entry guard #422 merged** (e93731e): `max_entry_k_slots` 8 plus signal age 5 s, in the DEC-020 trial config only. Before deploy: md5 replay; intents/arm-audit identical after stripping `migration_slot` and `migration_slot_src`; measure the `first_print` share.

**Unread confirmation blocks with no owner:** fresh-0828 and fresh-0808. fresh-0802 is being walked for EXP-016 (#248, #249, #250). The next likely use is a size-only confirmation if C0 at 0.5 SOL is positive.


## Live probe (real money; DEC-019)

- **Status:** 57/90 attempts, realized −0.167875 SOL, faa3192. Paused by the paper ledger's daily cap since 08:13Z; it resumes at 00:00Z. Hard end 10-12T00Z.
- **Measured today (notebook):**
  - live k_mig p50 5, p90 6 (job #285);
  - faa3192 is −0.014 SOL over 28 trips, which is +0.0143 before tx fees;
  - the true round-trip cost is ≈4.5% of size at 0.05 SOL, ≈2.9% at 0.25 and ≈2.65% at 0.5. Pool fees of about 2.4–2.5% are the floor. The 1,513,840 per buy is refundable ATA rent (jobs #305 and #306).
- **For the trial config (DEC-020 package):** `max_signal_age_s` is 120. Tighten it, and add a maximum-k guard. A signal decided 6.2 s late was traded.
- **Security: #415 is merged (`fd85b2f`) and INSTALLED by Helm on fast-0** (`probe_withdraw.py` sha 78ef8670…; the lookalike smoke test was refused). `probe_withdraw` only sends to the owner-confirmed `5ANMBJ8iun8MJvjDgJqVRgz4EsUFSUUQ8MpRXbk2eufi`. An address-poisoning dust transfer hit the wallet at 10-06T03:01:52Z.

## Ops (10-06)

- **fast-0 tip follower:** MemoryMax raised from 1G to 2G with `set-property`. The drop-in is persistent and the service did not restart (job #303).
- **Requests waiting on the owner/Helm** (notebook, ~17:20Z):
  - stop the Oracle attn book and observe.attention, and retire the V-less books;
  - research-0 cleanup: **DONE by Helm.** Disk went from 57% to 43%, about 126 G freed. Helm removed `kill-review-1005/snap`, `blocks/explore-0814`, `blocks/fresh-0903` and `raw/`, all verified against restic c7a5758b. The clean copies remain. **Anything that pointed at those raw paths must use `blocks-clean/` or `clean-view/` instead.**
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
