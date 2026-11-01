# Arena tip gap-fill: the three md5 proofs (job commands; NOT RUN)

Owed by DEC-031 Amendment 26 item 11 (quant-proof T1 on PR #1182). **None of these has been run.** They run as MiScusi jobs (`miscusi_job_submit`; `sh`, no bash-isms), on **explore-0814 hours only** (hunt23 RULES.md), after the PR's head is QP-OK. Each prints md5s, field names and counts only. A min-rows floor is notebooked before each job starts, and the job reports credits. No job opens the tip tape, the arena tape, the mirror or the writer's derived copy (SEALS-BRIEF 01ee029d).

The harness `tools/arena_tip_gapfill_proofs.py` (subcommands `p1`, `p2`, `p3`) is **not in PR #1182**: it needs the pinned writer's explore-0814 conversion path, which a worker may not open. It is a follow-up PR (builder, then QP and reviewer) that must exist, with its own tests on synthetic fixtures, before the jobs below are submitted. The commands pin its arguments so the proofs cannot drift. `EXP0814_HOURS` is the explore-0814 hour directory named in hunt23 `RULES.md`; `NEW_SHA` is the head of `claude/arena-tip-gapfill` after QP OK; `OLD_SHA` is main `2fa2e4bc`.

Common job fields: `resumable: false`; `nice 19`; stream rows to disk; `systemd-run MemoryMax=20G` on research-0 (one heavy replay at a time; at most 2 workers on fast-0, check `systemctl show user-1002.slice -p MemoryCurrent` first); the Helius key only through `--helius-env /var/lib/mal/fast-listener/helius.env` on fast-0 (read by path, never printed).

## P1: the engine is inert without a marker

- Question: does the engine at `NEW_SHA` write the same ledger as `tools/arena_paper.py` at `OLD_SHA` when the stream has no filled marker?
- Where: research-0, memory 20G.
- Command:
  `sh -c 'cd $MAL_CHECKOUT && python3 -m tools.arena_tip_gapfill_proofs p1 --hours-dir "$EXP0814_HOURS" --old-sha 2fa2e4bc --new-sha "$NEW_SHA" --book pinned-deterministic --min-rows "$MIN_ROWS" --out "$HOME/data/results/gapfill-p1.json"'`
- Pass: per bot, the ledger md5 is equal in order and as a set; the output holds the two md5s, the row counts, the bot ids and `rows_dup` only.

## P2: a cold fill equals a warm follower and the walker

- Question: does the fill tool, started cold on [lo, hi], reproduce a warm `TipFollower` run by getBlock over [lo - 1 h, hi + 1 h] of an exploration span, and the walker's rows for the same slots (EXP027 decoder)?
- Where: fast-0 at 0.5 CPU (it fetches), Helius credits reported; the span and the range are named in the notebook entry before the run.
- Command:
  `sh -c 'cd $MAL_CHECKOUT && python3 -m tools.arena_tip_gapfill_proofs p2 --lo "$LO" --hi "$HI" --warm-margin-hours 1 --walker-hours-dir "$EXP0814_HOURS" --helius-env /var/lib/mal/fast-listener/helius.env --rps 5 --min-slots "$MIN_SLOTS" --out "$HOME/data/results/gapfill-p2.json"'`
- Pass: per-slot row md5 equal over every field except `t_recv_ms`, `source` and the singular `virtual_quote_reserve`, `tx_index` presence included, between the fill, the warm follower and the walker rows; `rows_no_tx_index` equal across the three. The output holds the per-slot md5 digest of digests, the slot count, the credits and the counts of rows.

## P3: end-to-end splice

- Question: with ranges deleted from explore-0814 hour files (every fillable reason, including a `backlog_jump` whose pre-gap rows have rising lag and an `unresolved_dropped` slot with follower rows on both sides), filled, spliced and run through the engine with a pinned deterministic book, is the ledger the unbroken stream's?
- Where: research-0, memory 20G (the fill step reads the walker's rows for the deleted slots instead of calling RPC, so it costs no credits; P2 is the proof that the fetch path equals the walker).
- Command:
  `sh -c 'cd $MAL_CHECKOUT && python3 -m tools.arena_tip_gapfill_proofs p3 --hours-dir "$EXP0814_HOURS" --reasons backlog_jump,unfetchable,skip_contradicted,skip_contradicted_refetch,unresolved_dropped --book pinned-deterministic --backlog-slots 50 --min-rows "$MIN_ROWS" --out "$HOME/data/results/gapfill-p3.json"'`
- Pass: ledger md5 equal to the unbroken stream's except the rows Amendment 26 item 6 changes by design (marked-through trades and positions with a leg in the margin), which are listed by count and reason; `rows_dup` 0; no mixed (pool, slot) group.

## After the three

Each result is a `result.v1` record copied to `~/data/results/`, a MiScusi notebook entry (kind result, refs to the job) and a `data/console.json` update. Until P1, P2 and P3 are notebooked no covered registration may name Amendment 26.
