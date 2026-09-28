# Forward-paper memory growth: measurement, 2026-09-27 → 2026-09-28

Status: **measured, three fixes merged, two live restarts observed**. This
note records the harness fixes, the measured container table, the
restart-semantics answer, and what is left. The fixes themselves (GC-pause
mitigation + `_Wallet` compaction + a live mem-census in **#123**; periodic
`gc.freeze()` in **#124**; early-buffer bounding in **#125**) are in
separate PRs, kept apart from this PR (#122, harness + this note) per the
manager's routing call. See "Outcome" below for what each did and what the
live runner showed after each restart.

## Outcome: #123, #124, #125, and two live restarts

- **#123** (`claude/forward-paper-gc-and-wallet-fix`, merged): GC-pause
  mitigation (`install_gc_mitigation()`, raised thresholds + a startup
  `gc.freeze()`), `_Wallet` compaction (`pos_tokens`/`pos_cost`/
  `pos_open_t`/`pos_invested` merged into one `pos` dict), and a live
  `mem-census.json`/`.jsonl` (per-container `len()`s every 5 min) so Oracle
  could report its own real breakdown instead of relying on this harness's
  approximation.
- **#124** (`claude/forward-paper-periodic-freeze`, merged): `#123`'s
  `gc.freeze()` only ran once at startup, so objects allocated afterward
  stayed in the cyclic-collector's scan set and the thresholds fix's benefit
  decayed over an uptime of hours. `maybe_gc_freeze()` now re-freezes the
  live heap every 10 minutes from inside `serve()`'s main loop, not just at
  boot.
- **#125** (`claude/forward-paper-early-bound`, merged): bounded `self.early`
  (the ~115 MB/h leading contributor identified in the table below), the one
  container this note originally left unfixed as needing a product/quant
  call. Two rules, both logged: rule A scans each observe file at boot and
  marks any mint with a create dated before a safety margin before boot
  time as dead, so restart-cold prints for genuinely createless mints are
  dropped, not buffered, from the first print; rule B is a fallback 10-minute
  createless timeout, catching mints rule A's pre-boot scan misses. A 3.8h
  replay proof showed both rules are decision-neutral: `decisions.jsonl` md5
  `6b2f7786f8372c84a87ae13b6e273756` (217,266 rows) and `positions.jsonl` md5
  `9b1ce744ef42222fe3a8cc352d35e3a0` (72 rows) identical with the rules on vs.
  off. Measured early-buffer growth dropped from 83.69 MB/h to 1.46 MB/h.
  Root cause found while validating: **~58% of tape mints have no create in
  retained history at all**, mostly old pumpswap-traded tokens whose
  original `subscribeNewToken` create predates every retained observe file
  -- not a create-pipeline latency problem, a retention-window problem, so
  rule A's dead-mint declaration is the correct fix, not a race with a
  late-arriving create.

**Live, two restarts on Oracle since:**

- **Restart #1**, on `d3015ba` (#123 only): RSS grew 145 MB → 4.4 GB over
  10.5h (~405 MB/h) -- better than the pre-#123 ~460 MB/h but still a clear
  leak, consistent with `self.early` (not yet bounded at this point) being
  the dominant remaining contributor. `early_prints_buffered` reached ~426k
  within the first 2h alone. GC max pause dropped to ~2.0 s (still present,
  since `install_gc_mitigation()` raises thresholds but does not eliminate
  a full `gc.collect(2)` cost when one does run) with 3 `runner_lag`
  breaches in the first 2h.
- **Restart #2**, at 2026-09-28T14:25:02Z, on `f687fad` (#124 + #125): the
  boot-time pre-boot dead-mint scan found 50,747 dead mints in 1.1 s;
  `runner_lag` was 1 ms at 14:26:50Z. No `runner_lag` breach observed in the
  immediate post-boot window this note tracked.
- **Remaining known growth**: `wallets` (per-wallet per-mint entries --
  `mint_pnl`/`mints` persist by design, per the table below and #123's
  `_Wallet` compaction notes) is now the largest container this note has not
  bounded. Not yet fixed; a candidate for the next PR if live growth after
  restart #2 stays material.
- **Harness-vs-live discrepancy**: unresolved, kept below as originally
  written -- the offline harness's own container-byte sum and raw RSS
  growth still do not cleanly match the live runner's measured rate, before
  or after #123/#124/#125. The live `mem-census` #123 added is now the
  source of truth for Oracle's real per-container numbers, not this
  harness.

## What this was for

The live runner on Oracle (`mal-core-0`, 9 books) leaked after PR #120
(which only bounded `book.flow`/cooldowns/orphan-early-prints): ~0.45 GB/h,
5 GB RSS + 2 GB swap after 15h, restarted 22:48Z 2026-09-27. Post-restart,
on bc7a0c6 (includes #120), the leak continued unchanged: **139 MB at
22:49Z → 2,118 MB at 03:13Z, ~460 MB/h**. Separately, 10 of the last 40
healthchecks (~3.3h, inside the clean week) showed `runner_lag` with
**zero** swap use -- a processing stall, not a memory-pressure symptom,
which turned out to have a separate, more urgent cause (see below).

## Two more harness bugs found beyond the known one

The session inherited one known bug (creates loaded unwindowed) and a
harness that had never produced a real measurement. Fixing it took three
rounds:

1. **Creates unwindowed** (the known bug). `load_creates()` pushed the
   entire day's ~29,643 creates before replaying a 1-hour slice, giving
   `pympler.asizeof` ~28,000 irrelevant `MintBook`/`_Track` objects to walk
   at every checkpoint. Fixed with a cheap tape-time-bounds prescan
   (`_tape_time_bounds`) plus the existing `window_creates()`, the same way
   `run_replay_files` already does it.

2. **`LatencyMeter()` with no `now_ms` also disables `serve()`'s live
   `book.flow` cap.** `_prune()`'s rolling `LIVE_IDLE_RETAIN_MS` (3-minute)
   truncation of `book.flow` is gated on `self.latency.now_ms is not None`.
   `replay_rows()` (ParityTests, promotion backtests) correctly passes no
   `now_ms` -- those tools want full history for offline re-scoring. This
   harness copied that, which is right for decision parity but wrong for a
   *memory* measurement: `serve()`'s wall clock gives it the 3-minute cap
   for free, and the harness had nothing. Measured effect: a 1-hour replay
   with `LatencyMeter()` (no `now_ms`) showed `library`/`by_creator`/`seen`
   ballooning to ~200 MB with the **mint count unchanged** (1,341 the whole
   hour) -- `book.flow` growing unbounded for any mint still receiving
   occasional prints. Fixed by giving the harness a synthetic `now_ms`
   pinned to the tape's own simulated clock (a mutable box updated on every
   print), which is the same value already being passed to `_prune()`
   explicitly, so it changes nothing about *when* prune runs, only whether
   its live-window branch is reachable.

3. **A 1-hour-only creates window simulates a restart, not the continuous
   process.** Oracle's runner booted at 06:58:12Z; the staged tape starts at
   14:00Z. Windowing creates to only the tape slice's own range (fix #1)
   correctly matches "restart exactly at 14:00Z," but the real process had
   ~7 hours of prior creates already sitting in `library` by 14:00Z. Any
   mint created in that gap that was still trading at 14:00-18:00Z had no
   matching create in the narrow window, so all its prints buffered in
   `self.early` instead of a normal, prunable `MintBook` -- inflating
   `self.early` to ~154 MB in one simulated hour, on its own, before this
   fix. Added `--creates-since-ms` so a mid-run replay can window creates
   from the runner's actual boot instant instead. Used `1790492292000`
   (2026-09-27T06:58:12Z) for every measurement below.

All three are harness-only changes (`tools/forward_paper_mem_profile.py`).
No production code changed in #122.

## Measured table (4-hour slice, 14:00-18:00Z, creates windowed from boot)

13,081 creates loaded (boot → tape end), 4,593,157 tape rows read,
2,885,548 prints applied. Container sizes are real `pympler.asizeof()` for
everything except `wallets` (a calibrated estimate at every checkpoint,
cross-checked against a real deep `asizeof(engine.wallets)` at the first
and last checkpoint only, since a full wallets walk is the expensive part).

| Container | Δ entries (0→4h) | Δ bytes (0→4h) | MB/h | @15h (from 0) | @7d (from 0) |
| --- | --- | --- | --- | --- | --- |
| `library` | 0 (13,081 the whole run) | +57.2 MB | 14.3 | 214 MB | 2.4 GB |
| `tracks` | 0 (13,081) | +53.4 MB | 13.4 | 200 MB | 2.2 GB |
| `by_creator`\* | 0 (5,116) | +57.2 MB | 14.3 | 214 MB | 2.4 GB |
| `seen` | +12,955 | +47.6 MB | 11.9 | 179 MB | 2.0 GB |
| **`early`** | +3,599 mints | **+459.7 MB** | **114.9** | **1,724 MB** | **19.3 GB** |
| `attention` | +560 | +0.35 MB | 0.09 | 1 MB | 15 MB |
| `wallets` (estimated, cheap per-checkpoint) | +140,489 | +176.9 MB | 44.2 | 663 MB | 7.4 GB |
| `wallets` (real `asizeof`, final checkpoint only) | — | +327.2 MB | 81.8 | 1,227 MB | 13.7 GB |
| `grids_housekeeping` | ~flat | **-0.26 MB** (shrank) | ~0 | flat | flat |

\* `by_creator` holds references to the *same* `MintBook` objects `library`
does, so its bytes overlap with `library`'s almost entirely -- don't sum
both when totaling.

**De-duplicated total** (library + tracks + seen + early + wallets-real +
attention, `by_creator` excluded as a `library` duplicate, `grids` excluded
as flat): **≈ 236 MB/h** → ≈ 3.5 GB @15h, ≈ 39.7 GB @7d.

**Raw process RSS** in this same replay grew 177 MB → 9,639 MB over the 4
simulated hours: **≈ 2,365 MB/h** -- about 10x the de-duplicated container
total above, and about 5x the real Oracle rate.

## Verdict: does this reproduce the observed ~450-460 MB/h?

**No, not cleanly, in either direction, and the manager's instruction was
to stop chasing an exact match and record the discrepancy rather than keep
spending session time on it. Recorded here:**

- The **container-level sum (~236 MB/h) is about half** the real ~460 MB/h.
  `self.early` alone (~115 MB/h) is the single largest identified
  container -- bigger than `wallets` by either estimate -- and its count
  was *still climbing* at the final checkpoint (not leveling off), so 4
  simulated hours likely understates its steady-state rate. This points at
  `self.early` as a real, previously-unidentified (not in the original
  static-analysis suspect list) contributor at least as large as `wallets`,
  possibly larger over longer horizons.
- The **harness's own raw RSS growth (~2,365 MB/h) is about 5x the real
  rate**, and doesn't match its own container-byte sum either. Likely
  causes, not yet isolated: repeated `pympler.asizeof()` walks at every
  checkpoint are themselves allocation-heavy and can leave the allocator
  more fragmented than steady per-print allocation would; the harness
  processes 4 tape files through separate `zstd` subprocess pipes that
  production's continuous tailing wouldn't all hold open at once; and
  `asizeof()` measures retained object-graph size, not malloc's actual
  chunk/arena overhead, which is a different (and typically smaller)
  number than RSS for a workload doing this much small-object churn.
- Net: **`self.early` is flagged as the leading candidate for a real,
  under-addressed contributor**, `self.wallets` is a confirmed, real,
  now-partially-fixed contributor (see PR #123), and the harness's absolute
  RSS number should not be read as a literal prediction of Oracle's RSS --
  only the relative container ranking and per-hour orders of magnitude are
  trustworthy from this measurement.

## Restart semantics: does a `serve` restart change subsequent decisions?

**Yes.** Confirmed by reading `serve()`/`DirectoryTail`/`WalletState`, not
by a live A/B (no restart was performed on Oracle for this).

- `serve()` builds a brand-new `ForwardEngine()` on every process start:
  `library`, `by_creator`, `wallets` (`WalletState()`), `tracks` all start
  empty. Nothing is replayed from history at boot.
- `offsets.json` only stores `DirectoryTail`'s byte offsets into the
  *currently open* `trades-{hour}.jsonl` / `observe-{day}.jsonl` /
  `attention-{hour}.jsonl` files. On a brand-new key it defaults to
  `path.stat().st_size` (skip straight to EOF), not 0
  (`DirectoryTail._ensure`). A restart therefore resumes tailing from
  wherever it left off -- it does **not** re-read the day's earlier
  creates or prints.
- Consequence: `WalletState.bots`/`snipers`/`leaders`/`creators` (the sets
  `fill_features`'s clean-buyer veto and `f_leader_*` features read) and
  `by_creator`'s serial-creator/rug history reset to empty and only rebuild
  from prints/creates observed *after* the restart. A wallet or creator
  flagged bad before the restart gets a clean slate until re-observed
  misbehaving. `laya_v0.packet_at`'s `creator_features(book, t_ms,
  by_creator)` and `wallets.fill_features(...)` both read exactly this
  reset-to-empty state.
- One exception: `self.graph` (the funding graph) is reloaded from
  `graph_dir`'s `funding-*.jsonl` files (`FundingGraph.load`, an externally
  persisted artifact, not built purely from this process's own observed
  prints), so `fill_funding_features`'s funder/rug-veto columns are **not**
  affected by a restart.
- `forward_paper.py`'s own comment at `_register_create` ("this dedupe
  check alone still catches a duplicate create replay after a restart")
  confirms a restart's effect on in-memory state is a known, previously
  accepted property of this design, not a new finding.

**Therefore: periodic restarts are not a free or decision-neutral
stopgap.** Each restart temporarily degrades exactly the cross-mint safety
features (clean-buyer veto, serial-creator/rug detection) the system
depends on, for as long as it takes those sets to rebuild from fresh
observations. This is a real cost to weigh, not a reason to avoid restarts
outright -- see below.

## A separate, more urgent finding: GC-pause stalls (fixed in PR #123)

While chasing the ~460 MB/h number, a standalone probe (grow `WalletState`
to realistic sizes, time `gc.collect(2)`) found that a single full garbage
collection over the kind of heap this runner reaches after several hours
(~2 GB, ~4.5M tracked objects) takes **~1.6 seconds of wall time**, scaling
close to linearly with live object count:

| wallets | tracked objects | RSS | `gc.collect(2)` |
| --- | --- | --- | --- |
| 20,000 | 318,585 | 45 MB | 30.5 ms |
| 100,000 | 318,581 | 133 MB | 110.0 ms |
| 700,000 | 2,118,581 | 794 MB | 707.8 ms |
| 1,600,000 | 4,818,581 | 1,772 MB | 1,617.8 ms |

A multi-hundred-ms to multi-second stop-the-world pause is a strong,
directly-measured match for `runner_lag` with zero swap use (swap pressure
would show as a memory symptom; a GC pause shows only as a processing
stall). None of `_Wallet`/`WalletState`/`MintBook`/`_Track` has a
`__del__`, a `weakref`, or a back-reference that could form a reference
cycle -- ordinary refcounting already frees every entry the instant it is
popped, cyclic collector or not -- so running the cyclic collector far less
often is safe. **PR #123** raises `gc`'s thresholds (`(50_000, 40, 40)` vs.
the default `(700, 10, 10)`, ~280x fewer stop-the-world scans), freezes the
static startup heap (`gc.freeze()`, cannot cause a leak since refcounting
still frees a frozen object immediately if it becomes garbage), and logs a
`gc_stats`/`mem-census` line every 5 minutes so this is verifiable live
after the planned restart.

## What's fixed, what isn't (PR #123, not this PR)

- **Fixed, proven decision-neutral** (92→93 unit tests green across two
  commits; a `tools.forward_paper replay` on the staged 60k-row slice
  before/after produced byte-identical `decisions.jsonl`
  (`acf2a855dcae7ce5ed97eea023fa9659`) and `positions.jsonl`
  (`aa405a182a58609cd1bcbb8d37618f85`) both times):
  - GC-pause mitigation (`install_gc_mitigation()`), the likely
    `runner_lag` fix.
  - `_Wallet` compaction: `pos_tokens`/`pos_cost`/`pos_open_t`/
    `pos_invested` (four dicts, always created/popped together) merged
    into one `pos: dict[str, list[int]]` -- one hash-table slot and one
    copy of the mint string per open (wallet, mint) pair instead of four.
    Only external read site was `_leader_ok`'s `mint not in
    self.pos_tokens`, updated to `self.pos`. Does **not** bound `mint_pnl`
    or `mints`, which persist by design (read by `_leader_ok`/`is_bot`) --
    per the table above, this alone does not fully explain the observed
    rate.
  - A live `mem-census.json`/`.jsonl` (every 5 min): per-container `len()`s
    plus a wallets sub-container breakdown, since this offline harness
    could not cleanly reproduce the live number -- so Oracle can now report
    its own, real container breakdown after the restart.
- **Now fixed, in #125** (was: "not attempted" -- see "Outcome" above for
  the full account): a bound on `self.early`. The concern raised here still
  stands as the reason this needed care rather than a blind size/time cap --
  `_flush_early` replays the **entire** buffered history into the book the
  instant a late create finally arrives, and `_prune`'s anchor-keeping logic
  wants the print at-or-before the create's `t_signal_ms` and at-or-before
  `t_signal_ms + 30s`, both unknowable at buffering time. #125's rule A
  (pre-boot dead-mint scan) sidesteps this by only declaring a mint dead
  when a create *already exists* in retained history dated safely before
  boot -- never guessing about a create that might still be in flight. Rule
  B (a 10-minute createless timeout) is the deliberately-accepted risk for
  the mints rule A cannot see: an in-flight create delayed past 10 minutes
  would now be misclassified dead. The 3.8h replay proof (identical
  `decisions.jsonl`/`positions.jsonl` md5s, see "Outcome") found this risk
  did not materialize in the tested window, and the root-cause finding
  (~58% of tape mints have no create in *any* retained history) means rule A
  alone, not rule B's timeout, handles the large majority of cases.

## Files

- Harness: `tools/forward_paper_mem_profile.py` (this PR)
- Fix PRs (all merged): `claude/forward-paper-gc-and-wallet-fix` (#123) --
  GC mitigation, `_Wallet` compaction, live mem-census;
  `claude/forward-paper-periodic-freeze` (#124) -- periodic `gc.freeze()`
  every 10 min; `claude/forward-paper-early-bound` (#125) -- pre-boot
  dead-mint scan (rule A) + createless timeout (rule B) bounding
  `self.early`.
- Local config: `/home/claude/profile-data/forward-paper-local.json` (not
  committed -- points at this box's local paths, not Oracle's)
- Staged data: `/home/claude/profile-data/` (trades, creates, attention,
  model files -- not committed, ~1 GB)
- Checkpoint JSON behind the table above:
  `/home/claude/profile-data/out/slice4h/mem-profile.json` (not committed,
  local to this box)
