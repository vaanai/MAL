# Forward-paper memory growth: measurement, 2026-09-27 → 2026-09-28

Status: **measurement done, one proven fix shipped separately, one bound
explicitly not attempted**. This note records the harness fixes, the
measured container table, the restart-semantics answer, and what is left.
The fix itself (GC-pause mitigation + `_Wallet` compaction + a live
mem-census) is in **PR #123** (`claude/forward-paper-gc-and-wallet-fix`,
based on `origin/main`), kept separate from this PR (#122, harness + this
note) per the manager's routing call.

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
- **Not attempted: a bound on `self.early`.** It has no live-window cap
  (unlike `book.flow`'s `LIVE_IDLE_RETAIN_MS`) and only fully evicts a mint
  after 45 minutes of *total* silence (`_prune_early`). A size/time cap on
  an *active* createless mint's buffer cannot be proven decision-neutral
  the way the two fixes above can: `_flush_early` replays the **entire**
  buffered history into the book the instant a late create finally
  arrives, and `_prune`'s anchor-keeping logic wants the print at-or-before
  the create's `t_signal_ms` and at-or-before `t_signal_ms + 30s` -- both
  unknowable at buffering time, since the create (and therefore t0) hasn't
  arrived yet. Trimming the buffer early risks silently discarding exactly
  the print a late create's anchor logic would need, for mints where a
  create genuinely is just delayed rather than absent forever. Given the
  table above shows this as the single largest identified container,
  **this is the most valuable next fix**, but it needs either (a) a
  product/quant decision that createless-mint history beyond some window is
  acceptable to lose (an explicit, acknowledged risk, not a
  "decision-neutral" claim), or (b) a design that doesn't require choosing
  between the two -- e.g. capping only mints old enough that no plausible
  create could still be in flight, if such a bound exists in the creates
  pipeline's own latency characteristics (not investigated this session).

## Files

- Harness: `tools/forward_paper_mem_profile.py` (this PR)
- Fix PR: `claude/forward-paper-gc-and-wallet-fix` (#123, based on
  `origin/main`) -- GC mitigation, `_Wallet` compaction, live mem-census.
- Local config: `/home/claude/profile-data/forward-paper-local.json` (not
  committed -- points at this box's local paths, not Oracle's)
- Staged data: `/home/claude/profile-data/` (trades, creates, attention,
  model files -- not committed, ~1 GB)
- Checkpoint JSON behind the table above:
  `/home/claude/profile-data/out/slice4h/mem-profile.json` (not committed,
  local to this box)
