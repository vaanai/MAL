# DEC-019 probe: keyless dry-run evidence and live-file hashes, 2026-10-05

Evidence for DEC-019 §6 item 3, for Helm's check before enabling live. **No key was used, and nothing was signed or sent.**

## Deployed commit and live files

The deployed commit is in `/var/lib/mal/fast-forward/src/SOURCE_COMMIT` on mal-fast-0: **`8a6849b4a9dba468c2a1c32cc9106aeba9ea1dc4`**.

| File | Absolute path on fast-0 | sha256 (commit = disk, job #135) |
| --- | --- | --- |
| Live config, `scripts/mal-fast/probe-executor-live.json` | `/var/lib/mal/fast-forward/src/scripts/mal-fast/probe-executor-live.json` (installed by `install-fast-forward-paper.sh`) | `9f3cc530e3838b4aea925968c016e36d563622c00910a82aa8b6aa37abf68f2a` |
| Live drop-in, `scripts/mal-fast/mal-probe-executor-live.conf` | **Planned:** `/etc/systemd/system/mal-probe-executor.service.d/live.conf`. Not installed: `DropInPaths=` is empty and the directory does not exist (job #135). Helm installs it from a root clone at the commit above. | `f7f02ffff15daf882f978735d987380f8d62de07b38c5f7b2b1eca4805e3e062` |
| Dry-run config (running now) | `/var/lib/mal/fast-forward/src/scripts/mal-fast/probe-executor.json` | `82d1416ac8b7d4056cbf545dd9ecd33ec34defc815b9250a0c4054e1ce90d509` |
| Dry-run unit (running now) | `/etc/systemd/system/mal-probe-executor.service` | `5c81b8564ee9d24242a86a7014e78d38ba5f988078f5579c1484fec5ad84de2a` |

The live drop-in adds `LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json`, `LimitCORE=0`, and an `ExecStart` with `--config …/probe-executor-live.json --live`. Live needs both switches: `"mode": "live"` in that JSON, and `--live`.

## Dry run

- **Unit.** `mal-probe-executor` (User `mal-live`), `ActiveEnterTimestamp` 2026-10-05 05:35:47 UTC, `NRestarts=0`. 0 journal lines matching error or traceback since 05:30Z (job #135).
- **6 h mark** (job #130, 11:41Z):
  - 10 buys and 10 sells (7 tp, 3 sl);
  - 0 rows with an error;
  - `live_validate_err` null on all 10 buys;
  - simulated tokens vs V-priced quote: p50 +1.44 bps, max +1.56 bps, min −195.56 bps (n = 10).
- **Through 14:37:38Z** (job #136, all 40 rows):
  - 20 buys and 20 sells (13 tp, 7 sl), all `mode: dryrun`;
  - 0 rows with an error;
  - 0 buys with `live_validate_err`.
- **Stage latency** (job #130, n = 10, ms):

| Stage | p50 | p90 |
| --- | ---: | ---: |
| Decision → seen | 2,055.0 | 2,502.8 |
| Seen → state | 62.5 | 77.3 |
| State → built | 3.0 | 9.4 |
| Built → simulated | 31.0 | 55.5 |
| Runner `applied_latency_ms` | 1,869.0 | 1,980.4 |

- **Evidence on disk** (root or `mal-live` can read; dir `/var/lib/mal-live` is 0700 mal-live). sha256 at 14:37Z (job #135):
  - `/var/lib/mal-live/probe-fills.jsonl`: `94346321924d3b4edc8404614cdea6f24403a7fca8bdd8d48cc895145e98e02b`, 40 lines. This file keeps growing while the dry run runs.
  - `/var/lib/mal-live/state-dryrun.json`: `6184848f0d67d7668150d3ba3a714a8a2d3fd2a6bf9b5d2075c6c3c21a18a0ca`.

## How to re-check (as root on fast-0)

```
cat /var/lib/mal/fast-forward/src/SOURCE_COMMIT
sha256sum /var/lib/mal/fast-forward/src/scripts/mal-fast/probe-executor-live.json
git -C <root clone> show 8a6849b4a9dba468c2a1c32cc9106aeba9ea1dc4:scripts/mal-fast/mal-probe-executor-live.conf | sha256sum
python3 -c "import json;R=[json.loads(l) for l in open('/var/lib/mal-live/probe-fills.jsonl') if l.strip()];B=[r for r in R if r.get('kind')=='buy'];print(len(R),'rows',len(B),'buys',sum(1 for r in R if r.get('err')),'err',sum(1 for r in B if r.get('live_validate_err') is not None),'validate_err')"
cd /var/lib/mal/fast-forward/src && sudo -u mal-live /var/lib/mal/fast-forward/venv/bin/python -m tools.probe_executor --config scripts/mal-fast/probe-executor.json --latency-report
```

## Addendum (2026-10-05, after the owner's PR checker)

**Code under the running dry run.** The dry-run process started 2026-10-05T05:35:47Z on code from `ff9cacb`. The src tree was later swapped under it twice by `install-fast-forward-paper.sh`: to `20d72bb` (09:29Z, job #123) and to `8a6849b` (job #127). Across all three commits, the executor's whole import closure and both probe configs have **0 diff lines**. The files are `tools/probe_executor.py`, `tools/probe_live.py`, `tools/pumpswap_tx.py`, `tools/pumpswap_simulate.py`, `tools/paper_curve_math.py`, `tools/paper_price_path.py`, `tools/paper_tape_scoreboard.py`, `tools/__init__.py`, `scripts/mal-fast/probe-executor.json` and `probe-executor-live.json`. So the dry-run evidence holds for the code Helm verified.

Guards against a repeat:
- the installer refuses while a live drop-in exists ([#305](https://github.com/vaanai/MAL/pull/305));
- the root-owned pinned executor ([#305](https://github.com/vaanai/MAL/pull/305));
- Helm's `malprobe-code` auditd watch on src and venv.

**Executor vs paper runner timing** (job #130, n = 10, ms, p50):

| Quantity | Value |
| --- | ---: |
| Paper runner `applied_latency_ms` (on chain → its booked entry) | 1,869 |
| Executor decision → seen | 2,055 |
| Executor seen → pool state | 62.5 |

The executor reads pool state about **2.12 s** after the trigger, roughly **250 ms after the paper runner's entry**, before it sends or lands anything. The §7 live-versus-paper note counts this gap as latency against paper, not as slippage.

**Simulated fills short of the V quote.** In 3 of the 20 buys through 14:37Z (job #139) the simulated buy received fewer tokens than the V-priced quote:

| Mint | bps vs quote |
| --- | ---: |
| `5eGi…yCz2` | −121.16 |
| `H8oe…q8C7U` | −195.56 |
| One further buy | −163.96 |

The other buys are within about ±10 bps (p50 +1.44 at 6 h).
- **Cause.** The quote uses pool state read at `confirmed` commitment (executor config `commitment: confirmed`). `simulateTransaction` runs at `processed` (`tools/pumpswap_simulate.py`), 1–2 slots later. In those slots other buyers moved the price, right after migration. This is not a pricing bug: the V math matched within 2 bps whenever the pool did not move.
- **What it measures.** The same latency cost, seen from the price side.
- **Live sends.** `min_out` is computed from the confirmed-state quote with the 15% slippage cap, so these moves fill rather than fail.
