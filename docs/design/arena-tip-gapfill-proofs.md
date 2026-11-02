# Arena tip gap-fill: the three md5 proofs (job commands; NOT RUN)

Owed by DEC-031 Amendment 26 item 11 (quant-proof and reviewer on PRs #1182 and #1194). **None of these has been run.** No covered book may name Amendment 26 until P1, P2 and P3 have run and are notebooked. They run as MiScusi jobs (`miscusi_job_submit`; `sh`, no bash-isms), on **explore-0814 hours only** (hunt23 RULES.md), after the PR heads are QP-OK. Each prints md5s, field names and counts only. A min-rows (P1, P3) or min-slots (P2) floor is notebooked before each job starts, and the job reports credits. No job opens the tip tape, the arena tape, the mirror or the writer's derived copy (SEALS-BRIEF 01ee029d).

The harness is `tools/arena_tip_gapfill_proofs.py` (PR #1194, stacked on #1182). It enforces explore-0814 = [2026-08-14T00, 2026-08-28T00) **before anything is read or fetched** (the constant is vendored from `arena_restart_equiv.EXPLORATION["explore-0814"]`, PR #1183): P1 and P3 refuse any input row whose `block_time` (`t_recv_ms` when absent) is outside the window or missing; P2 checks its walker rows the same way and refuses, before its first fetch, unless [lo - margin, hi + margin] lies inside the slot span of those walker rows. Every directory and output argument also passes the sealed-path refusal of the fill tool. A refusal exits 3, a failed proof exits 2.

It reads CONVERTED ARENA ROWS (JSONL, slot order) that the pinned writer step produced from explore-0814 hours; that conversion is a separate step this PR does not contain. `EXP0814_HOURS` below is the directory of those converted rows named in hunt23 `RULES.md`; `EXP0814_WALKER` is the walker rows of the same hours; `OLD_SHA` is main `2fa2e4bc`. The engine under test is the working tree of the checkout the job runs in, whose head is printed in the result.

Common job fields: `resumable: false`; `nice 19`; `systemd-run MemoryMax=20G` on research-0 (one heavy replay at a time; at most 2 workers on fast-0, check `systemctl show user-1002.slice -p MemoryCurrent` first). `--scratch-dir /data2/mal/scratch` on research-0 (P2's warm follower writes about two hours of trade rows; on fast-0 check the disk first). The Helius key only through `--helius-env /var/lib/mal/fast-listener/helius.env` on fast-0 (read by path, never printed; no key is a refusal, never a fall back to the public RPC).

## P1: the F2 engine is inert without a marker, with the engine's own gap detection off and on

- Question: does `ArenaEngineF2` (`tools/arena_paper_f2.py`, over `tools/arena_paper.py`) write the same ledger as `ArenaEngine` of `tools/arena_paper.py` at `OLD_SHA` when the stream has no filled marker? It is run twice: with `gap_slots` / `gap_silence_ms` off and at the stack defaults (150 and 60,000), so the lines F2 changed in `on_row` are exercised.
- Where: research-0, memory 20G.
- Command:
  `sh -c 'cd $MAL_CHECKOUT && python3 -m tools.arena_tip_gapfill_proofs p1 --hours-dir "$EXP0814_HOURS" --old-sha 2fa2e4bc --repo . --book pinned-deterministic --min-rows "$MIN_ROWS" --scratch-dir /data2/mal/scratch --out "$HOME/data/results/gapfill-p1.json"'`
- Pass: in BOTH runs, per bot, the ledger md5 is equal in order and as a set (and the gap counts). The output holds the sha256 of the OLD engine source and of the NEW files, both md5 pairs, the row counts, the harness head and `rows_dup` only.

## P2: a cold fill equals a warm follower and the walker

- Question: does the fill tool, started cold on [lo, hi], reproduce a warm `TipFollower` run by getBlock over [lo - margin, hi + margin] of an exploration span, and the walker's rows for the same slots (EXP027 decoder)? The fields not compared are pinned in the code before any run (`DROP_P2`: `t_recv_ms`, `source`, `virtual_quote_reserve`); a mapping is never chosen after a mismatch is seen. A mismatch prints the field-name sets of the three sources and the names of the fields that differ, never a row.
- Where: fast-0 at 0.5 CPU (it fetches), with backoff (`time.sleep`) on retries. The span, the range and the margin are named in the notebook entry before the run.
- Cost, to be written in the notebook entry before the job: planned getBlock calls = 2 * margin_slots + 2 * (hi - lo + 1) + 1, where margin_slots = 9,000 * `--warm-margin-hours` (1 h: 18,000 calls on top of the fill's own); at 1 to 2.6 credits a call (LAB_STATE row 115 figures) that is about 18,000 to 47,000 credits for the default margin. The result prints `planned_getblock_calls` and the credits the RPC layer counted.
- Command:
  `sh -c 'cd $MAL_CHECKOUT && python3 -m tools.arena_tip_gapfill_proofs p2 --lo "$LO" --hi "$HI" --warm-margin-hours 1 --walker-hours-dir "$EXP0814_WALKER" --helius-env /var/lib/mal/fast-listener/helius.env --rps 5 --min-slots "$MIN_SLOTS" --scratch-dir /data2/mal/scratch --out "$HOME/data/results/gapfill-p2.json"'`
- Pass: per-slot row md5 equal over every field except `DROP_P2`, `tx_index` presence included, between the fill, the warm follower and the walker rows; `rows_no_tx_index` equal across the three.

## P3: end-to-end splice

- Question: with ranges deleted from the explore-0814 rows (every fillable reason; an `unresolved_dropped` slot loses ONE pool's prints and keeps the others on both sides of the gap row), filled from the deleted rows, spliced and run through the F2 engine with a pinned deterministic book, is every position changed exactly as Amendment 26 item 6 says, and nothing else? Four cells: gap detection off / on, and pre-gap rows with `t_recv_ms` trailing the chain by a lag rising to B = 50 slots before a `backlog_jump` (applied in both streams) off / on.
- Where: research-0, memory 20G (the fill step reads the deleted rows instead of calling RPC, so it costs no credits; P2 is the proof that the fetch path equals the walker).
- Command:
  `sh -c 'cd $MAL_CHECKOUT && python3 -m tools.arena_tip_gapfill_proofs p3 --hours-dir "$EXP0814_HOURS" --reasons backlog_jump,unfetchable,skip_contradicted,skip_contradicted_refetch,unresolved_dropped --book pinned-deterministic --min-rows "$MIN_ROWS" --scratch-dir /data2/mal/scratch --out "$HOME/data/results/gapfill-p3.json"'`
- Pass, in every cell: the expected class of every position, COMPUTED from the unbroken run's legs and the plan (`fill_hit` when an entry leg is in [lo - B, hi] or the exit leg in [lo - B, lo); `mark_through` when the exit leg is in [lo, hi] and nothing hits; else `unchanged` with equal rows), equals the actual class exactly (so an `excluded_missing_v`, or a `feed_gap` from an engine slot jump the fill failed to suppress, fails); the fill inserted exactly the removed rows and dropped exactly the twins; `rows_dup` 0; no mixed (pool, slot) group; the filled run has no gap the unbroken run lacks. The output prints the late-print counts of both runs by kind (fill-margin flag, `gap_overlap` true, `gap_overlap` false) and the number of positions that span a filled range with their legs clear of it and are priced unchanged (the amendment's claimed benefit).

## After the three

Each result is a `result.v1` record copied to `~/data/results/`, a MiScusi notebook entry (kind result, refs to the job) and a `data/console.json` update. Until P1, P2 and P3 are notebooked no covered registration may name Amendment 26.
