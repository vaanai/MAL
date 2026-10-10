# C1-NF wallet ledger: nightly rollup on mal-fast-0

Tool: `tools/c1nf_wallet_ledger.py` (PR #502). Paper tooling: no key, no network, no order path.
It builds `daily/wl-<D>.npz` from the tip follower's trade files and then `asof/asof-<D+1>/`, the
cumulative wallet ledger that the C1-NF features read through `AsofLedger.passa_matrix`.

**Nothing in this runbook has been run on mal-fast-0.** Every number below says where it was measured.

## 1. Preconditions on mal-fast-0 (all unverified)

| Item | Check | Why |
| --- | --- | --- |
| A venv of its own, for example `/home/claude/venvs/c1nf-ledger` | `python3 -m venv /home/claude/venvs/c1nf-ledger && /home/claude/venvs/c1nf-ledger/bin/pip install -r requirements-c1nf-ledger.txt` | The follower's `/var/lib/mal/fast-forward/venv` belongs to the follower; do not install into it. |
| duckdb is 1.5.6 | `$PY -c 'import duckdb, numpy; print(duckdb.__version__, numpy.__version__)'` prints `1.5.6 ...` | The wallet key is duckdb `hash(trader)`. Another duckdb whose `hash()` differs is refused at connect, so every night would fail (exit 3). |
| One dry build | `$PY tools/c1nf_wallet_ledger.py day --day "$(date -u +%F)" --adapter tip --tip-dir /var/lib/mal/sealed/fast-trades-tip --out-root "$LEDGER_ROOT/dryrun" --allow-open-day --allow-missing-hours --threads 3 --mem-gb 6 --tmp-dir "$LEDGER_ROOT/tmp_duck"`, then delete `$LEDGER_ROOT/dryrun` | Proves the venv, the hash pin and read access in one step. |
| Read access to `/var/lib/mal/sealed/fast-trades-tip` | the dry build above | The follower writes as `ubuntu` with `UMask=0077`. The job user needs the same read access the daily tip archive job (#304) uses. |
| `$LEDGER_ROOT` with a `.nobackup` file | `ls "$LEDGER_ROOT/.nobackup"` | Re-buildable data. |
| `zstd` CLI | `zstd --version` | Only for `--verify-sha` on archive `.zst` files. |

Set in the job command: `PY=/home/claude/venvs/c1nf-ledger/bin/python` and `LEDGER_ROOT=<path>`.

## 2. Before the first night: what the root starts with (manager decision, open)

The PR does not decide this. Record the choice before the rollup is scheduled.

- **A. Tip days only.** The root starts empty and grows from the first rollup. Then `ndays`, `n`, the known-wallet share and the
  skill features differ from training and from EXP-025's read, whose wl/ is P2's 36 exploration days plus October
  (EXP-025 s11.2 item 3). This matters for the DEC-026 item 13 parity check.
- **B. Seed with the exploration days.** Copy the 36 exploration daily files (byte-equal to the pinned EXP-025 wl/, see the PR)
  and the tip days 10-06 and 10-07 from research-0 `/data/mal/hunt-1008/c1nf-ledger/out/all/daily/` (`wl-*.npz` plus
  `wl-*.manifest.json`, 76 files) into `$LEDGER_ROOT/daily/`:
  1. `sha256sum` the 76 files on research-0 and on fast-0 after the copy; the two lists must be equal.
  2. `$PY tools/c1nf_wallet_ledger.py check --out-root "$LEDGER_ROOT" --daily` (exit 0: every file matches its manifest).
  3. Build the tip days from 10-08 to yesterday in date order (section 4), then `asof --day <today>`.
  The snapshot then carries the known 238 h hole `[09-25T07, 10-05T05)`: wallets active only in the hole are unknown.

## 3. The nightly job

A MiScusi job on mal-fast-0 (`miscusi_job_submit`, `machine: mal-fast-0`, `cpus: 3`, `mem_gb: 8`, `time_limit_min: 30`),
submitted daily at **00:15Z** by the manager's daily trigger (as for the tip archive job). The command is POSIX `sh`:

```sh
PY=/home/claude/venvs/c1nf-ledger/bin/python; LEDGER_ROOT=<path>
D=$(date -u -d yesterday +%F)
$PY tools/c1nf_wallet_ledger.py rollup --day "$D" --require-prev-day \
  --tip-dir /var/lib/mal/sealed/fast-trades-tip --out-root "$LEDGER_ROOT" \
  --threads 3 --mem-gb 6 --max-temp-gb 8 --tmp-dir "$LEDGER_ROOT/tmp_duck" --keep-asof 3 \
&& $PY tools/c1nf_wallet_ledger.py check --out-root "$LEDGER_ROOT" --asof-day "$(date -u +%F)" --max-wallets 30000000
```

- Day D is closed when the `D+1 T00` trade file is non-empty (about 00:01Z). Before that the job refuses.
- `--require-prev-day`: a missed night makes every later night refuse until it is caught up (section 4). This is on purpose.
- `mem_gb: 8` is the hard limit (the job is killed above it). `--mem-gb 6` limits only duckdb. Check `systemctl show user-1002.slice -p MemoryCurrent` before the first submit, as for any job there.
- Output: one JSON line with `day`, `asof` (`wallets`, `mode`, `saturated`), `pruned_asof` and `peak_rss_mb`. Copy `peak_rss_mb` and `asof.wallets` into the daily note for the first week, then lower `mem_gb` if the measured peak allows.

**Exit codes.** 0 ok. 3 refused, nothing written: open day, missing hour files, bad sha sidecar, missing previous day,
NULL-trader rows, hash pin, lock held by another build, or the `check` failed. 4: an output exists and differs (an as-of
built from other inputs, or `day --recheck` whose rebuild has other content). A rerun of a finished night does not rebuild
anything: it prints `"status": "exists"` and exits 0.

**Alert on the first refused night.** A non-zero exit fails the MiScusi job. The manager's daily check treats a failed
ledger job, or `check --asof-day <today>` exiting 3 after 00:45Z, as an alert the same day: it is not left for the next
night, because the live dir keeps only 72 hours (section 4).

**Consumers.** A decision on day D must use `asof-<D>` (the days strictly before D). `AsofLedger.open_for_day(root, D)`
refuses (`StaleLedger`) until the night's rollup has built it, about 00:17Z; the shadow and features (#503, #506) must
refuse picks in that window, or when the job failed, and never fall back to `asof-<D-1>` silently.

## 4. Catching up after a missed night

The follower runs with `--max-keep-days 3` and removes, every hour, the hour files older than now - 72 h. Day D's first hour
is gone at about D+3 00:00Z, so the live dir can rebuild D until then.

- **One or two nights missed, inside 72 hours.** Run the rollup command once per missed day, oldest first, with
  `--day <that day>` in place of yesterday. Each one needs the day before.
- **Older days, or hour files already deleted.** Build those days on mal-research-0 from the daily archive
  `/data/mal/tip-tape-archive/fast-trades-tip` (zstd, `.sha256` sidecar = sha256 of the decompressed file; the archive job runs
  daily about 03:23Z, so the newest hours may not be there yet):
  ```sh
  $PY tools/c1nf_wallet_ledger.py day --day <D> --adapter tip --verify-sha \
    --tip-dir /data/mal/tip-tape-archive/fast-trades-tip --out-root <research-0 root> --threads 3 --mem-gb 6 --tmp-dir <tmp>
  ```
  Copy `daily/wl-<D>.npz` and `daily/wl-<D>.manifest.json` to `$LEDGER_ROOT/daily/` on fast-0, compare sha256 at both ends,
  run `check --daily`, then `asof --day <today>` (a fold; it does not need the missing incremental base), then resume the
  nightly job.
- A live build and an archive build of the same day give the same bytes (tested: `.zst` archive vs plain live). To compare a
  day built from the live dir with the archive: `day --day <D> --adapter tip --recheck --verify-sha --tip-dir <archive>`
  (exit 4 if they differ; nothing is written).
- A day with missing hour files (a follower outage) is refused. Whether to build it with `--allow-missing-hours` is a
  manager decision for that day; the manifest records `hours_missing`.

## 5. Memory, disk and growth

Measured with the code before the review fixes (research-0, real data; PR body and review 1):
- nightly job 91.8 s, peak RSS 4.9 GB at 15.2M wallets; as-of fold of 36 days 4,378 MB at 13.9M wallets; one-day incremental
  4,330 MB (about 3.3x the on-disk snapshot);
- one exploration day build (09-20, 26.5M rows, 3 threads): peak RSS 2.56 GB.

Measured with this code (research-0, **synthetic** data only, `systemd-run --scope -p MemoryMax=6G`; interpreter baseline 72 MB):

| Run | Wallets | Old code peak RSS | This code peak RSS | Content hash |
| --- | ---: | ---: | ---: | --- |
| one-day incremental (base 3.0M + day 0.5M) | 3,115,290 | 1,105 MB | 175 MB | equal |
| fold of 10 days x 0.6M | 2,678,010 | 1,016 MB | 176 MB | equal |

- Not measured on the real 15.2M-wallet snapshot. A linear extrapolation of the synthetic run (103 MB above baseline at
  3.1M wallets) gives about 0.5 GB at 15M and about 0.85 GB at 25M. The duckdb day build is then the larger step.
- Growth: about 0.46-0.49M new wallets per full day (median 493,809 over the last 10 full exploration days, review 1).
- Disk: 12 columns x 8 bytes, about 96 bytes per wallet per snapshot: 1.4 GB at 15.2M wallets, plus about 46 MB a day.
  `--keep-asof 3` keeps three snapshots; the one just built and its incremental base are never deleted. Daily files are
  about 20-25 MB each and are kept. duckdb spill is capped at `--max-temp-gb 8`.
- Alert threshold: `check --max-wallets 30000000` (about 2.9 GB per snapshot). With option B that is about 30 nights after
  10-08. Raise it only after a measured nightly `peak_rss_mb`.

`buy_lamports` and `sell_lamports` in the as-of saturate at int64 max: on research-0 the largest cumulative `buy_lamports`
over 38 days was already 0.728 of int64 max (most likely non-WSOL pools whose amounts are summed as lamports, kept from C1;
not checked per wallet). `MANIFEST.json` `saturated`
counts the rows at the cap. `passa_matrix` does not read these columns.
