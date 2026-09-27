# Forward-paper memory growth: measurement status, 2026-09-27

Status: **in progress, not complete**. This note records what was measured,
what was only calibrated, and what is still a static-analysis hypothesis, so
the next session does not have to redo the setup.

## What this was for

The live runner on Oracle (`mal-core-0`, PID 37795, `python -m tools.forward_paper
serve --config /var/lib/mal/paper/forward-paper/forward-paper.json`, 9 books)
has grown to ~5.1 GB RSS + 2 GB swap after ~15.3 h (started 06:58Z, checked at
22:19Z the same day). Static analysis pointed at cross-mint containers that
PR #120 did not touch: `self.library`, `self.tracks`, `self.wallets.wallets`
(and its per-mint sub-dicts), `self.by_creator`. PR #120 already bounds
`book.flow` / `book.path.prints` (the per-mint print history) down to at most
3 anchor prints once a mint has been idle 45 minutes, so those two containers
are *not* the leak PR #120 left behind.

Confirmed live, read-only, via `ssh mal-core-0`:

```
ubuntu   37795  56.2 20.7 6154492 5103012 ?  DNs  06:58 519:08 .../python -m tools.forward_paper serve --config ...
```

## Data pulled for an offline replay (read-only, done)

Oracle's tape and creates are sealed JSONL, tailed by `serve()` from
`tape_dir`/`creates_dir` in the config. Copied to this box (never written back,
`mal-core-0` untouched):

- `/home/claude/profile-data/trades/trades-2026-09-27T{14,15,16,17}.jsonl.zst`
  (~1.0-1.2M print rows/hour, ~700 MB compressed total)
- `/home/claude/profile-data/jsonl/observe-2026-09-27.jsonl` (today's creates,
  29,643 distinct mints by 22:20Z -- roughly 1,300-1,400 new mints/hour)
- `/home/claude/profile-data/attention/attention-2026-09-27T{14..17}.jsonl.zst`
- LAYA/graduated-swing model artifacts (`entry_model.txt`, `scoreboard.json`,
  `barrier_hit_100_30.txt`, `mig15_model.txt`)
- A local copy of the config with paths rewritten to the above
  (`/home/claude/profile-data/forward-paper-local.json`)

Total under 1 GB, well inside the 20 GB budget. A Python 3.12 venv at
`/home/claude/profile-data/venv` has `requirements-laya.txt` (lightgbm,
scikit-learn, numpy) plus `pympler` installed; the tape's `.zst` files are read
via the `zstd` CLI exactly as `tools.paper_price_path.open_text` does in
production, no extra Python zstd binding needed.

## Harness (committed)

`tools/forward_paper_mem_profile.py` drives the *exact* `ForwardEngine` class
`serve()` uses -- `push_create` / `push_print` / `push_attention` /
`drain_until` / `_prune`, same holdback -- over a fixed file slice instead of
a live tail. No sockets, no writes outside `--output-dir`. At each checkpoint
(every N print rows) it takes:

- RSS (`ru_maxrss`)
- `len()` of every long-lived container the static analysis named
- a real `pympler.asizeof` of the containers that scale with **distinct
  mints** (`library`, `tracks`, `by_creator`, `seen`, `attention`, `early`,
  `mint_order`) -- cheap, since mint count is ~1,300/hour, not print volume
- an *estimated* size for `wallets` (which scales with **print volume**,
  ~1.1M rows/hour), using constants measured once via `pympler.asizeof` on
  real `_Wallet` objects, then multiplied by cheap `len()` counts at each
  checkpoint. The first and last checkpoint also take a real, full
  `asizeof(engine.wallets)` so the estimate's accuracy is checked against
  ground truth rather than assumed.
- a `tracemalloc` snapshot (`nframe=1`, to keep its own overhead down)

Measured (real pympler, not guessed) calibration constants for `_Wallet`
(`tools/laya_v0.py`, `__slots__`-based, one instance per unique trader):

| Quantity | Measured bytes |
| --- | --- |
| Empty `_Wallet` | 856 |
| Marginal cost of one more `(wallet, mint)` pair (adds to `mints`, `pos_cost`, `pos_open_t`, `pos_invested`, `mint_pnl` together) | ~202 |
| Marginal cost of one more closed-position `holds` entry | ~40 |

For comparison, also measured directly (not the leak, since PR #120 already
bounds these, but useful as a per-mint floor): a `MintBook` pruned down to 3
anchor prints (both `flow` and `path.prints`) is ~2.3 KB including its
`CreateSignal`; an empty `_Track` (six per-mint sets from
`@dataclass` defaults) is ~2.0 KB. At ~1,300-1,400 new mints/hour that is only
~6 MB/hour from `library` + `tracks` combined -- nowhere near the observed
~450 MB/hour, which is why `self.wallets` (scales with the ~1.1M
prints/hour, not the ~1,300 mints/hour) is the leading hypothesis, not yet
the measured conclusion.

## What is NOT done yet -- the actual gap

**The engine replay itself looked far slower than tape real-time on this
box, and the cause was found (measured, not guessed) but not yet fixed.**
A `tools.forward_paper replay` smoke test on a 5-minute *span* of tape
completed in 12s wall, which wrongly suggested ~150s/hour -- misleading,
since `--span-min` cuts the file by a time window, not a fixed fraction of
rows. Reading a full hour (~1.1-1.2M rows) through all 9 books took over
25 minutes of wall time and had *not reached the first 200,000-row
checkpoint*.

A `cProfile -s cumulative` run on a 5,000-row sample (`sample5k.jsonl`,
`/home/claude/profile-data/out/prof5k.txt`) found the actual cause, and it
is **a harness bug, not an engine or LightGBM problem**: `_sizer`
(`pympler/asizeof.py:1837`) alone accounts for 39.5s of tottime and is
called **2,841,390 times** for just 16 `asizeof()` calls -- i.e. the deep
`pympler.asizeof` walk, not the engine, dominates wall time. The reason:
this harness's `load_creates(creates_paths)` loaded the **entire day's**
`observe-2026-09-27.jsonl` (29,643 distinct mints, 00:00Z-22:20Z) and
pushed all of them into the engine before processing a single print row
from the 1-hour tape slice, instead of windowing creates to the slice's own
time range the way `run_replay_files` does with `window_creates()`. That
put ~29,643 `MintBook`/`_Track` objects into `library`/`tracks`/`by_creator`
at checkpoint 0 (vs. the ~1,300-1,400/hour that would actually be relevant
to a 1-hour slice), and every subsequent `asizeof()` call on those
containers had to walk all of them -- an unrepresentative, and needlessly
slow, baseline. This is a fixable one-line bug in the harness
(`load_creates(...)` needs `window_creates()` applied against the tape's
own `[first_t_ms, last_t_ms]`, as `run_replay_files` already does), not a
finding about the runner. It was found but not yet fixed or re-run before
this session's budget ran out.

**No checkpoint table, no bytes/hour table, and no confirmation that the
sum reproduces the observed 0.45 GB/h growth exist yet.** The `wallets`
dominance above is architecture-plus-calibration reasoning (print volume vs.
mint-creation volume, with measured per-entry costs), not yet a measured
per-hour byte curve from a real replay.

## Recommended next step

1. Fix `tools/forward_paper_mem_profile.py`: window `load_creates(...)`'s
   result to the tape slice's own `[first_t_ms - pad, last_t_ms]` (reuse
   `tools.forward_paper.window_creates`), the same way `run_replay_files`
   already does for the `replay` subcommand. This alone should make both the
   checkpoints and the wall time representative of what serve() actually
   holds after N hours, since it removes the ~28,000 out-of-window `MintBook`
   objects that were inflating both.
2. Rerun over a 30-60 minute slice first (fast feedback), confirm the
   checkpoint table looks sane (`wallets_estimated` vs.
   `wallets_asizeof_true` at checkpoint 0 and final should be close), then
   extend to the full 4-hour slice already staged in `/home/claude/profile-data/`.
3. Build the container -> entries -> bytes -> bytes/hour -> @15h -> @7d table
   from the checkpoints, and check it against the ~0.45 GB/h observed on
   Oracle.
4. Only then design the safe bound (candidate: once a mint's `book.flow` has
   been collapsed to its 3 anchor prints for 45+ minutes and it is not
   `busy`/`mig15_waiting`, drop it from `self.wallets`'s per-mint sub-dicts
   for wallets that only ever touched that mint and have no open position in
   it -- `fill_funding_features`/`creator_features` read `book.flow`,
   `by_creator`, and `graph.funder_index(library)` for cross-mint outcomes,
   not `wallets.wallets[trader].pos_cost[mint]` directly, so this looks
   decision-neutral, but that has to be checked against every read site, not
   assumed) and prove it with `ParityTests` plus a before/after decision diff
   on the staged slice.

## Files

- Harness: `tools/forward_paper_mem_profile.py`
- Local config: `/home/claude/profile-data/forward-paper-local.json` (not
  committed -- points at this box's local paths, not Oracle's)
- Staged data: `/home/claude/profile-data/` (trades, creates, attention,
  model files -- not committed, ~1 GB)
