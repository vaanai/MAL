# DEC-015 §2.1: every input the forward-paper runner reads, and a plan for each on mal-fast-0

Date: 2026-10-01. Docs and analysis only. Nothing was installed, started, restarted or written on either host. Oracle was read through the read-only `ssh mal-core-0` account (no sudo, no row contents beyond field names). `mal-fast-0` was read locally. Code reference is `tools/forward_paper.py` on `main` at `a788cc1`; the Oracle runner is code `d7485d2` per HOSTS.md. I did not diff those two builds, so a line number here could be off by a few lines on Oracle's copy. The Oracle `forward-paper.json` and `forward-paper.sh` have the same md5 as `scripts/mal-core/forward-paper.json` and `.sh` in this repo (`4e2c52cf…`, `6322d2c0…`), so the config below is the live one.

## Summary table

"Added latency" is the extra delay the plan puts between the producer stamp and the runner. The runner drops any row whose stamp is more than 5,000 ms behind wall clock (`STALE_ACTION_MS`, `forward_paper.py:496`, drop at `:3326-3332`), so a shipped feed has a hard budget.

| # | Input | Needed by | On fast-0 today | Plan | Added latency | Flag |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Trade tape `trades-YYYY-MM-DDTHH.jsonl` | every book | `mal-fast-trade-tape` installed, disabled; trial output has the **same 18 field names** as Oracle | Produce on fast-0: enable that unit into a dedicated dir (this is the §2.2 feed trial) | 0 | Disk about 0.65 GB/h raw; RAM about 0.3 GB |
| 2 | Creates `observe-YYYY-MM-DD.jsonl` | every book | **No compatible file.** `fast-create`, `fast-public`, `fast-pre-create` all lack `t_ws`, creator and reserves, so every row is dropped | Produce on fast-0: a new unit running the unmodified `observe` module (same PumpPortal socket, $0) | 0 (PumpPortal is about 59-70 ms behind Oracle, measured 2026-09-27) | Second PumpPortal connection from one IP is unmeasured |
| 3 | Pre-boot create scan (yesterday and today's `observe-*.jsonl[.zst]`) | every book, at boot | n/a until #2 exists | Same dir as #2; empty on first boot is safe | 0 | Cold first day |
| 4 | Attention `attention-YYYY-MM-DDTHH.jsonl` | **swing books only** | absent | If a swing book is wanted: produce on fast-0 (keyless HTTP pollers). Otherwise leave `attention_dir` unset | Cannot ship (see below) | Whether DexScreener / pump.fun / GeckoTerminal serve the OVH IP is untested |
| 5 | `poller_start.json`, `startup_snapshot.jsonl` | swing books | absent | Created by the fast attention poller on its first start | 0 | Defines "genuine arrival"; fast and Oracle sets differ |
| 6 | Funding graph `funding-YYYY-MM-DD.jsonl` | laya and swing books | absent | **Decision needed.** Ship from Oracle by rsync pull, or run a local enricher | Ship: poll interval plus transfer, unmeasured, proposed 5 s | **Over 1 s if shipped. Paid (Helius) if produced locally** |
| 7 | Model files (`entry_model.txt`, `barrier_hit_100_30.txt`, `mig15_model.txt`) | laya, swing | absent | Ship once from Oracle with an md5 manifest. EXP-012's model is already in git | 0 at run time | None |
| 8 | Model meta `scoreboard.json` (rule id) | laya | absent | Ship once with the models | 0 | 655 KB |
| 9 | Config JSON, `KILL` file | all | absent | New host config in git; `KILL` path under the fast output dir | 0 | None |
| 10 | Runner state it reads back (`offsets.json`, `guard-live.json`, `positions.jsonl`) | all | absent | Fresh on fast-0; do **not** copy Oracle's | 0 | Copying would import Oracle's clean clock |
| 11 | Code, venv, `zstd` CLI | all | system `python3` has lightgbm 4.7.0 and numpy; `/usr/bin/zstd` present; the listener venv has no lightgbm | Build the runner venv on fast-0 (x86_64) | 0 | Oracle venv is aarch64 and cannot be copied |
| 12 | Wall clock | all | NTP synchronized | Producers and runner on the same host | 0 | Cross-host plans add a clock offset (+1 to +20 ms measured) |

Latency flags in one line each:

- Over 1 s: **#6 only**, and only on the ship option. #4 would be over 1 s on any ship option, which is why it is produced locally or skipped.
- Needs a paid feed: **#6 only**, and only on the local-enricher option (Helius RPC, a rolling cumulative of about 412k credits on Oracle). Nothing else needs one: #1, #2 and #4 are $0.

## Which inputs a book actually needs

From `books_from_config` (`forward_paper.py:957`) and the call sites. This decides how much of the above is real work for the first fast-0 book.

| Book kind | Tape | Creates | Models | Attention | Funding graph |
| --- | --- | --- | --- | --- | --- |
| `baseline` | yes | yes | no | no | no |
| `migrate` | yes (the migration time is taken from the tape, `:1567`) | yes | no | no | features are filled but no model reads them |
| `laya` | yes | yes | entry or barrier model | no | yes, `packet_at(graph=...)` at `:1739` |
| `swing` | yes | yes | swing model, or none | **yes** (`_swing_features`, `:1676`) | yes (`:1690`) |

EXP-012's frozen feature list (`ARTIFACTS/exp012/features.json`, `frozen_feature_names`) has 18 names and **none** is an `f_funder_*` or attention feature. So by feature list the EXP-012 book does not need #4, #5, #6 or #8. I did **not** check how the runner would load that model (it takes its feature names from the booster, `ModelSlot.score`, `:1235`) or which `kind` the book would be configured as. That is for the runner-equivalence work (§2.3).

## Inventory

### Env vars and config

The runner module reads **no environment variables** (grep of `tools/forward_paper.py`, `tools/paper_price_path.py`, `tools/paper_tape_scoreboard.py`, `tools/graduated_swing.py`, `tools/paper_curve_math.py`; the only hits are in `tools/laya_v0.py:477-488`, `MAL_YIELD_*`, which belong to the LAYA training job, not `serve`). Everything comes from one JSON file passed as `serve --config`:

| Key | Default in code | Oracle value | Cite |
| --- | --- | --- | --- |
| `tape_dir` | `/var/lib/mal/sealed/trades` | same | `:3223` |
| `creates_dir` | `/var/lib/mal/sealed/jsonl` | same | `:3224` |
| `output_dir` | `/var/lib/mal/paper/forward-paper` | same | `:3225` |
| `kill_file` | `<output_dir>/KILL` | same | `:3226` |
| `model_path`, `model_meta`, `barrier_model`, `swing_model` | none | `/var/lib/mal/paper/laya-v0/out/entry_model.txt`, `.../scoreboard.json`, `.../barrier_hit_100_30.txt`, `/var/lib/mal/paper/graduated-swing/out/mig15_model.txt` | `:3227-3230` |
| `attention_dir` | none (feed off) | `/var/lib/mal/attention` | `:3231` |
| `graph_dir` | `/var/lib/mal/graph` | not set, default used | `:3281-3283` |
| `holdback_ms`, `slippage_cap`, `size_sol`, `swing_freeze_*`, `books` | see `scripts/mal-core/forward-paper.json` | | `:3232-3233` |

The config file is re-read when its mtime changes, checked every 30 s (`:3365-3381`). Books can be tightened live; the kill switch cannot be cleared (`guard_kill_switch`, `:912`).

Launcher `scripts/mal-core/forward-paper.sh` sets `MAL_FORWARD_ROOT`, `MAL_FORWARD_PYTHON`, `MAL_FORWARD_CONFIG` (shell only), `PYTHONPATH=<root>/src`, and single-thread `OMP/OPENBLAS/MKL/NUMEXPR` limits. Unit `scripts/mal-core/mal-forward-paper.service` is `Nice=19`, `Restart=on-failure`.

### 1. Trade tape

| | |
| --- | --- |
| Reader | `DirectoryTail._ensure` opens only the **current UTC hour's plain** file `trades-YYYY-MM-DDTHH.jsonl` (`:3101-3113`). A sealed `.jsonl.zst` is never followed (`_Follower` docstring, `:3032`). Reads at most 2 MiB per poll (`:527`, `:3047`), cuts at the last newline (`:3050`), polls every 25 ms when idle (`:3406`). Resumes from `offsets.json`, or from current EOF if there is none (`:3112`). Replay mode globs `trades-*.jsonl[.zst|.gz]` (`:3444`). |
| Oracle path and producer | `/var/lib/mal/sealed/trades/`, `mal-trade-tape.service` (`python -m observe.trade_tape`, public RPC `logsSubscribe`, confirmed, pump.fun and PumpSwap). Unit `scripts/mal-core/mal-trade-tape.service`. |
| Format | JSONL, one trade per line. Field names on Oracle (one line, 2026-10-01): `v, venue, mint, trader, side, sol_lamports, token_raw, quote_reserve, base_reserve, price_sol, pool, slot, signature, event_index, t_recv_ms, event_ts, quote_mint, quote_is_wsol`. Parsed by `flow_from_tape_row` (`:767`) then `print_from_trade_row` (`paper_price_path.py:471`): needs `mint, venue, t_recv_ms, quote_reserve, base_reserve`, and `quote_is_wsol` for PumpSwap. Optional: `price_sol, market_cap_sol, sol_lamports, slot, event_index, side, token_raw, trader, signature, tx_index, pool_quote_amount, lp_fee, protocol_fee, creator_fee`. `event_ts` (unix seconds) feeds the chain-to-recv latency median (`:757`, `LatencyMeter.note_print`). |
| Freshness | The runner's clock for a trade row is `t_recv_ms`, the **producer's** receive stamp (`row_clock_ms`, `:673-680`). Older than 5,000 ms against the runner's `time.time()` and it is dropped and counted in `stale_dropped`. `lag_ms` in `runner-status.json` is `now - newest t_recv_ms` (`:733-739`). Watermark holdback is `holdback_ms` = 300 (`:3361`). |
| Cadence and size (Oracle, measured) | 299,363 rows in the 05:00:00 to 05:19:53Z hour (about 251 rows/s, 599 B/row). File grew 3,608,320 B in 20 s (about 180 KB/s, 0.65 GB/h, 15.6 GB/day raw). Sealed hours are 108 to 179 MB `.zst` (seven consecutive hours 2026-09-30T22 to 10-01T04: 123.7, 139.3, 107.9, 136.6, 137.2, 148.7, 132.1 MB), about 3.2 GB/day. Directory is 19 GB, 295 files. Hour file is sealed at hh:59:59. |
| Fast-0 today | `/var/lib/mal/sealed/fast-trades/` holds the 2026-09-27 trial (`trades-2026-09-27T11.jsonl`, 270 MB for 30 min = 9 MB/min, same as Oracle). Field names are **identical** to Oracle's, in the same order. Also writes `pool-mints-*.jsonl[.zst]` and `stats-*.jsonl`, which the runner does not glob. |
| Plan | **Produce on fast-0.** Enable `mal-fast-trade-tape.service` with its output set to a dedicated dir, and point `tape_dir` there. Latency added: 0 (same host, same clock). The 2026-09-27 trial measured 99.98% coverage and a +128 ms median lead over Oracle's tape on a 30-minute window; DEC-015 §2.2 requires a fresh trial before any of that is relied on. Resource note: trial RSS about 308 MB (`MemoryMax=1G`); `keep_days=7` would be about 23 GB sealed plus 0.65 GB live on a disk with 112 GiB free. This unit is a new 1G entry in the "listeners at most 3G" budget of DEC-015 §2.4, so that line needs a 4G figure. |

### 2. Creates

| | |
| --- | --- |
| Reader | `DirectoryTail._ensure` follows **today's** `observe-YYYY-MM-DD.jsonl` only (`:3102-3104`); it never re-opens a prior day, and a day rollover opens the new file from offset 0 only if no offset is saved for it (`:3112`). Parsed by `create_from_observe_row` (`paper_price_path.py:166-203`). |
| Oracle path and producer | `/var/lib/mal/sealed/jsonl/`, `mal-observe.service` (`python -m observe`, PumpPortal `subscribeNewToken` and `subscribeMigration`, $0). `observe/client.py:83-104`, row built by `observe/regime.py:166`. |
| Format | Field names (one line): `schema_version, type, t_ws, t_event, stream, source, commitment, stage, regime_id, txType, signature, mint, knowable_at_t, ws_payload, ws_fields_unknown, traderPublicKey, name, symbol, uri, bondingCurveKey, vTokensInBondingCurve, vSolInBondingCurve, marketCapSol`. The runner **requires** `mint`, a parseable `t_ws`, and either `stream == "subscribeNewToken"` or `txType == "create"`. It uses `traderPublicKey` (creator), `signature`, `vSolInBondingCurve`, `vTokensInBondingCurve`, `marketCapSol`, `initialBuy`, `solAmount` (top level or inside `ws_payload`). Migration rows are discarded by this function. |
| Freshness | Clock is `t_ws`, an ISO string stamped at websocket receive (`row_clock_ms`, `:674-675`; `observe/client.py:102`). Same 5 s stale cap. |
| Cadence and size (Oracle, measured) | 9,220 rows from 00:00Z to 05:19:53Z (about 0.48 rows/s, 1.8 KB/row). File grew 12,151 B in 20 s. Day files 60 to 82 MB (2026-09-24 to 09-30). Day files stay plain; none are `.zst`. Dir 562 MB. |
| Fast-0 today | **No file the runner can use.** Three create-ish outputs exist, none with `t_ws`: |

| Fast-0 dir | Field names | Missing against the runner's needs |
| --- | --- | --- |
| `sealed/fast-create/fast-create-YYYY-MM-DD.jsonl` (`mal-fast-create`, PumpPortal, `fast_create_v0`) | `schema, source, stream, mint, slot, vendor_time, t_recv_ms, tx_type` | `t_ws` (it has `t_recv_ms`, an int; the parser needs the ISO `t_ws`), `traderPublicKey`, `signature`, `vSolInBondingCurve`, `vTokensInBondingCurve`, `marketCapSol`, `initialBuy`, `solAmount`. `tools/fast_create_listener.py:133-151` deliberately writes only mint and stamps. `stream` and `tx_type` do carry `subscribeNewToken` and `create`. `slot` and `vendor_time` are null. |
| `sealed/fast-public/public-logs.jsonl` (`mal-fast-public-logs`) | `stream, signature, mint, slot, t_recv_ms, instr` | `t_ws`, creator, reserves, all create amounts. One growing file (44 MB), not daily. |
| `sealed/fast-pre-create/fast-pre-create-YYYY-MM-DD.jsonl` (`mal-fast-pre-create`, Helius preprocessed) | `schema, source, stream, mint, bonding_curve, slot, signature, t_recv_ms, instr` | `t_ws`, creator, reserves, all create amounts. Spends Helius credits (cap 10,000/day). |

With `fast-create` as `creates_dir` today, `create_from_observe_row` returns `None` on every row (no `t_ws`), so no mint is ever registered and no book trades. The creator is also needed by the creator cooldown and the funding graph.

Plan: **produce on fast-0, a new unit running the unmodified `observe` module** (`python -m observe --output-dir <fast dir>`), same code and schema as Oracle, $0, no key. The `mal-fast-create` listener is not touched. Added latency: 0 against the fast tape. PumpPortal creates on fast-0 were 59 to 70 ms behind Oracle's (2026-09-27 note, medians −59 and −70 ms), which is the price of the free feed. A faster create path (preprocessed, +109 ms) does not carry the creator or the reserves, so it cannot replace this feed without an extra transaction decode. Two points to measure in the §2.2 trial rather than assume: (a) whether PumpPortal accepts a second concurrent connection from the same IP alongside `mal-fast-create`, and (b) where creates now land in time relative to fast-0's tape, which on a 30-minute window ran ahead of Oracle's by a median of 128 ms. The runner's inbox is ordered by event time and drained at `watermark - 300 ms` (`:3361`), so a create that arrives more than that after its first prints is handled by the buffer for createless mints (`EARLY_BUFFER_DEAD_MS`, 10 min), not dropped; I have not tested that on a mixed-host pair.

### 3. Pre-boot create scan

`_safe_preboot_dead_mints` at boot reads yesterday's and today's `observe-<day>.jsonl[.zst]` from `creates_dir` (`:2969-3003`, `:3236`). It builds the set of mints whose create predates the boot, so later prints for them are dropped. It is read-only and failure-safe (returns an empty set on any error, `:3024-3028`). On fast-0, with a fresh #2 dir, the first boot has nothing to scan, which is safe: rule B (10-minute buffer give-up) covers it. It will, however, buffer prints for old, already-migrated mints for up to 10 minutes after first start. Nothing to ship.

### 4 and 5. Attention (swing books only)

| | |
| --- | --- |
| Reader | `attention-YYYY-MM-DDTHH.jsonl` for the current hour, only if `attention_dir` is set (`:3106-3107`). At boot and every 30 s, `bind_attention` reads `poller_start.json` (`t_start_ms`) and `startup_snapshot.jsonl` (`kind`, `mint` pairs) (`:3207-3213`, `observe/attention.py:647-692`). A row is a "genuine arrival" only if `t_first_ms > t_start_ms`, not in the snapshot, and `snapshot` is not true (`is_genuine_arrival`, `observe/attention.py:781`). |
| Oracle path and producer | `/var/lib/mal/attention/`, `mal-attention.service` (`python -m observe.attention`; keyless HTTP polls of DexScreener, pump.fun frontend API, GeckoTerminal at 15 to 60 s per source; `ARTIFACTS/lab/attention.md`). |
| Format | Field names: `v, type, kind, source, mint, chain, t_first_ms, t_first, t_seen_ms, rank, name, symbol, twitter, reply_count, usd_market_cap, market_cap, created_timestamp, is_currently_live, complete, boost_mode, last_trade_timestamp, event_t_ms, snapshot`. Used: `mint`, `kind`, `t_first_ms`, `rank`, `snapshot`. Snapshot file rows are `kind, mint, t_first_ms`. |
| Freshness | Clock is `t_first_ms`, the poller's own first-seen stamp; same 5 s stale cap (`:3326-3332`). |
| Cadence and size (Oracle, measured) | Bursty: 44 rows in the hour through 05:16:39Z, 26.8 KB (about 600 B/row); sealed hours are 28 to 40 KB `.zst`; dir 3.9 MB, 137 files. `poller_start.json` is from 2026-09-25T15:26:03Z; `startup_snapshot.jsonl` is 36.4 KB and was last appended 2026-09-29T16:40Z. |
| Fast-0 today | Nothing (`/var/lib/mal/attention` does not exist). |
| Plan | **Only if a swing book is wanted.** For EXP-012 it is not needed (see above); then leave `attention_dir` unset and `DirectoryTail` and `bind_attention` skip it. If wanted: **produce on fast-0** with a new unit running `observe.attention` ($0, no key). **Shipping from Oracle does not work:** any file-copy delay above 5 s makes every row stale and the runner drops all of them, and a hand-built streaming ship would couple fast-0's books to Oracle's poller. A fast poller creates its own `poller_start.json` and snapshot, so its "genuine" set differs from Oracle's, and the book is not comparable to Oracle's swing books. Not tested: whether the three HTTP services serve the OVH Frankfurt IP at the same rate limits (pump.fun sends browser-style `Origin` and `Referer`, `observe/attention.py:456-457`). I made no outbound calls to them. |

### 6. Funding graph

| | |
| --- | --- |
| Reader | `ForwardEngine._maybe_reload_graph` globs `funding-*.jsonl` in `graph_dir` at most every 5 s and reloads the whole graph when the newest file's `(mtime_ns, size)` changes (`:1704-1726`; `FundingGraph.load`, `tools/funding_graph.py:300-305`). If `graph_dir` is not a directory at boot the graph is never loaded (`:3282-3283`) and funding columns are filled with their empty defaults. Visibility rule: a wallet record is used only if `first_seen_ms <= decision time` (`funding_graph.py:307-311`). |
| Oracle path and producer | `/var/lib/mal/graph/`, `mal-funding-graph.service` (`python -m tools.funding_graph serve`). It tails Oracle's own tape and creates dirs, enqueues creators and early buyers, and resolves each wallet's funder with `getSignaturesForAddress` and `getTransaction`. Public RPC at 1 rps, or **Helius at 5 rps** when the key file `/var/lib/mal/backfill/helius.env` exists (`funding_graph.py:49-52`, `:162-193`). Unit and launcher: `scripts/mal-core/mal-funding-graph.service`, `funding-graph.sh`. |
| Format | JSONL, append-only, one wallet per line. Field names: `schema, v, type, wallet, funder, amount_lamports, funded_at_ms, wallet_first_tx_ms, first_seen_ms, exchange, exchange_name, history_capped, role_hint, rpc_pages, status`. Sidecars `tail.json` (10 KB, enricher cursor), `helius-credits.json`, `.writer.lock` are not read by the runner. |
| Important property | `first_seen_ms` is the **wall-clock time the enricher resolved the wallet**, not the wallet's first transaction (`funding_graph.py:545, 609, 624, 678`: `first_seen_ms=now_ms`). So what the runner sees at a given decision time depends on how fast the enricher worked. A different enricher (on fast-0) produces different visibility than Oracle's, even with the same code. A copy of Oracle's rows can be used causally (a row only appears on fast after it exists, and its stamp is in the past), but a book run against copied rows is not identical to one run against a local enricher. |
| Freshness | No stale cap on this feed. A late row means a missing feature, not a dropped decision. |
| Cadence and size (Oracle, measured) | Irregular: the 2026-10-01 file had no append between 04:56:01Z and my first check at about 05:19Z, and had grown to 2.06 MB by 07:01:56Z. Daily files 5.1 to 8.9 MB, 20,042 rows for 2026-09-30; 43 MB total in 7 files. Process RSS 391 MB. |
| Credits (Oracle, measured) | `helius-credits.json`: 412,548 used of a 2,000,000 cap at 04:56:10Z. Log line: `resolved=86983 calls=207630 limited=108 credits=412521/2000000 cached=109201`, so about 2.4 calls and 4.7 credits per resolved wallet. The counter starts at the graph dir's creation (`.writer.lock` is from 2026-09-25T16:30Z), so about 75k credits/day averaged over about 5.5 days. That figure is my arithmetic from those two numbers, not a reported daily meter. |
| Fast-0 today | Nothing (`/var/lib/mal/graph` does not exist). |
| Plan options | **A. Ship from Oracle (recommended for the probation period).** One-time seed of the 43 MB, then a pull every 5 s of the newest file with `rsync --append` from fast-0 (`ssh mal-core-0`, read-only account, no change on Oracle). The files are append-only, so this is safe. Added latency is the poll interval plus the SSH transfer, **over 1 s by design (proposed 5 s, so up to about 5 to 10 s; the transfer time was not measured)**. It is tolerable because the feed has no stale cap and creators are resolved at 1 to 5 rps anyway, but it should be measured, not assumed. Costs $0 extra; keeps Oracle's single enricher. Weakness: fast-0 books then depend on Oracle's enricher, and Oracle's tape, for their features. **B. Local enricher on fast-0** (same `tools.funding_graph serve`, pointed at the fast tape and creates). Zero ship delay, features follow the fast feeds, matches what live would run. It **needs a paid feed**: Helius at about 75k credits/day by the Oracle rate above (public RPC at 1 rps was rate limited 108 times on the Oracle log and cannot be assumed to keep up). That is a plan and credit decision for the manager and owner, to be reported per the CLAUDE.md credits rule. The key stays in the mode-600 file `/var/lib/mal/fast-listener/helius.env`; `MAL_HELIUS_ENV_FILE` selects the file (`funding_graph.py:173`). **C. No graph** (leave `graph_dir` absent) for a book whose model has no funding features, which is the case for EXP-012 by the feature list. This is the only option with zero latency, zero credits and no Oracle dependency, and it is available only for that book. |

### 7 and 8. Models and meta

| | |
| --- | --- |
| Reader | `ModelSlot.maybe_reload` (`:1191-1235`) loads a LightGBM `Booster` (`tools/laya_v0.py:2037`, `:2084`) at boot (`:3259-3264`) and re-checks the file mtime every 30 s (`:3365-3368`). A missing file means no model and the book skips with `no_model`. The meta JSON is read for `entry.deploy.rule_id` (`_read_rule`, `:1223`). |
| Oracle paths | `/var/lib/mal/paper/laya-v0/out/entry_model.txt` (149 KB, 2026-09-26 04:46Z), `barrier_hit_100_30.txt` (148 KB, 04:56Z), `scoreboard.json` (655 KB, 04:56Z); `/var/lib/mal/paper/graduated-swing/out/mig15_model.txt` (141 KB, 2026-09-26 05:09Z). Producers: `mal-laya-v0.timer` (**disabled until 2026-10-05**) and `graduated-swing-train.sh`. All are static since 2026-09-26. |
| Fast-0 today | Nothing. Python `lightgbm 4.7.0` and numpy are importable by the `claude` user's system `python3`; the listener venv has no lightgbm. |
| Plan | **Ship once.** `rsync -a --checksum` from Oracle with a sha256 manifest into a fast-0 model dir (about 0.5 MB total, plus 655 KB meta). Latency: 0 at run time. No ongoing sync is needed while the LAYA timer is disabled; if it is re-enabled, a model refresh would change books mid-run and is a decision, not plumbing. For EXP-012, `ARTIFACTS/exp012/model.txt` (md5 `a1810d219ed61db64a396f40dc302ce5` per the EXP-012 file) is already in git and needs no shipping. |

### 9 and 10. Config, kill file, state files

| File | Reader | Plan |
| --- | --- | --- |
| Config JSON | `serve` at boot and on mtime change (`:3218`, `:3374`) | New fast-0 file, committed to git, different `output_dir`, `tape_dir`, `creates_dir`. Books go in only after the 2026-10-05T05:00Z kill review (DEC-015 §2.3). |
| `KILL` | checked on every entry (`:1906`, `:2006`) | Under the fast output dir, set by the config. Absent means running. |
| `offsets.json` | boot (`:3243-3251`), written every 60 s (`:3394`) | Start empty on fast-0. With no offset each file starts at current EOF (`:3112`), so the first boot does not replay history. |
| `guard-live.json`, `invalid-for-promotion.json` | `load_guard_live_ms` (`:683`), `ensure_guard_live` (`:696`) | Created by the runner at its first caught-up poll (`:3333-3339`). It writes `VOID_FROM` constants from the code. Do not copy Oracle's, which holds Oracle's live instant (2026-09-27T06:58:12Z). |
| `positions.jsonl` | read back on every summary (`summary`, `:2433-2445`, `iter_position_rows`, `:584`) | Starts empty. On Oracle it is 280 MB and `decisions.jsonl` is 1.1 GB, and `summary` re-reads `positions.jsonl` every 60 s (`:3386`); that is cost the fast disk and CPU must carry, and it grows through the week. |
| Output (not inputs) | `decisions.jsonl`, `pnl-daily.jsonl`, `latency.jsonl`, `mem-census.jsonl`, `runner-status.json` | New dir on fast-0. |

### 11. Code, venv, binaries

The runner imports `observe.attention`, `tools.funding_graph`, `tools.graduated_swing`, `tools.laya_v0`, `tools.paper_curve_math`, `tools.paper_price_path`, `tools.paper_tape_scoreboard` (`:29-80`); it needs the repo `tools/` and `observe/` trees on `PYTHONPATH` at the code floor (at least `d7485d2`). LightGBM and numpy are imported lazily (`laya_v0.py:2047, 2084`). The `zstd` CLI is used only for replay and for the pre-boot scan if it meets a `.zst` day file (`paper_price_path.py:229-236`); it is installed on fast-0. Oracle's runner venv is aarch64 (`/var/lib/mal/paper/laya-v0/venv`) and cannot be copied; build a venv on fast-0. Not verified: that the system-python lightgbm 4.7.0 produces the same predictions as Oracle's pinned version. That belongs to the md5 equivalence replay (DEC-015 §2.3).

### 12. Clock

Every freshness test compares a producer stamp (`t_recv_ms`, `t_ws`, `t_first_ms`) to `time.time()` in the runner process. Running producers and runner on one host removes any cross-host offset. Measured offsets in the 2026-09-27 note: fast-0 +12.7, +19.7 and +1.1 ms against NTP in three windows, Oracle between −67 µs and +20 µs. That is small next to a 5,000 ms cap, but any shipped feed also pays its transfer time against the same cap.

## Oracle runner state during these checks

Single samples, not a study. `runner-status.json` `lag_ms`: 12,613 at 05:18:54Z; 6,343 at 07:01:58Z; 5,913 at 07:02:19Z. `stale_dropped`: 377,828, then 552,250, then 554,026 (cumulative since the 00:00Z restart). Runner RSS 1.89 GB at 7 h uptime (about 270 MB/h, matching HOSTS.md). Oracle load average about 1.0 on 4 cores at 07:02Z. Per-process RSS on Oracle: observe 50 MB, trade tape 169 MB, attention 103 MB, funding graph 391 MB. These are for sizing fast-0 units; fast-0's own trade-tape trial measured about 308 MB.

## Open items for the manager

1. Plan 6 needs a decision: ship (A), local Helius enricher (B), or no graph for EXP-012 (C). B is the only one with credits, A the only one over 1 s.
2. Input 2 needs a small new unit and a PumpPortal concurrency check; the unit file and install script do not exist yet (`scripts/mal-core/` has one for `fast-create`, `fast-trade-tape`, `fast-pre-create`, no `observe` equivalent for fast-0).
3. The DEC-015 §2.4 memory budget lists listeners at 3G. Units for #1, #2 (and #4 if used) add 1G each at their `MemoryMax`, so the budget line needs updating before the sum check.
4. Inputs 1 and 2 define what "same feeds as live" means; §2.2 should measure creates and trades together, not separately.

## Not checked

- No row contents were read beyond field names and types. No values were compared between hosts. Field differences are by name only.
- No outbound request to PumpPortal, Helius, DexScreener, pump.fun or GeckoTerminal. Whether they serve fast-0 for a second connection or the attention pollers is untested.
- Transfer time and delay for any Oracle-to-fast ship (`rsync` over Cloudflare Access) was not measured. The 5 s figure is a proposal.
- Oracle's running code was not diffed against `main`. Config and launcher md5s match the repo. The `src` checkout could not be queried (git "dubious ownership" for the read-only user).
- Whether the runner can load the EXP-012 model and which book `kind` it will use was not checked.
- Oracle's systemd units were read from the repo copies in `scripts/mal-core/`, not from the host. No sudo was used on Oracle.
- No equivalence replay, no feed trial, no memory measurement. Those are DEC-015 §2.2 to §2.4.
