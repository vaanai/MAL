# Token signal recorder: start plan (JUP-ORGANIC step 0, THREAD-WAKE step 0)

Ideas 5 and 6 of `/data/mal/hunt-1008/ops/reports/2026-11-20/new-strategy-ideas.md`. Both need a forward snapshot recorder before any test can run. Tool: `tools/token_signal_recorder.py` (tests: `tools/test_token_signal_recorder.py`, fixtures: `fixtures/token_signal_recorder/`). This page does not start anything. The manager starts it.

## What it records
- `jupiter_token`: Jupiter Tokens API v2 `GET https://api.jup.ag/tokens/v2/search?query=<up to 100 mints>`. Keeps organicScore, label, isVerified, tags, holderCount, liquidity, usdPrice and short stats5m/1h/6h. Docs: https://developers.jup.ag/docs/tokens/token-information.md ; keyless 0.5 RPS stated at https://developers.jup.ag/llms.txt . Checked live 2026-11-20: keyless call returns 200 and these fields.
- `jupiter_rank`: `GET /tokens/v2/toptrending/1h?limit=100` and `/toporganicscore/1h?limit=100`; one row per mint with its rank or null. Same docs page ("Get Category").
- `pump_coin` (off by default): `GET https://frontend-api-v3.pump.fun/coins/<mint>`. Not available: on 2026-11-20 a plain HTTP client got a Cloudflare "Just a moment" challenge. pump.fun publishes no public docs or terms for this host. The tool does not work around a challenge. It stops the source after 3 blocks and writes one `{"blocked": true}` row. THREAD-WAKE therefore has no usable free source today; do not run with `--pump` unless the owner finds a documented public endpoint. The livestream list (`/coins/currently-live`) was also challenged.
- No Helius, no wallet, no key. User-Agent `token-signal-recorder/1.0 (research; polite)`. Rate: min 2.5 s between calls (0.4 rps), 3 tries with backoff, Retry-After honoured, hard cap 400 calls/hour (default), clean exit on SIGTERM.

## Mints file
One mint per line, `#` comments, re-read every cycle, so the manager can refresh it from the FLOOR-candidate pools without a restart. Jupiter may not index every young pool; those rows say `{"found": false}`, which is itself data.

## MiScusi job (host mal-research-0; plain HTTP, no websocket; mem 256M, cpus 0.3, resumable false)
Command (runs under sh; no bash-isms). Replace the mints file with the manager's list:

    cd /data2/mal/wt/token-signal-recorder && exec /data/mal/venv/bin/python tools/token_signal_recorder.py --mints-file /data2/mal/token-signals/mints.txt --out-dir /data2/mal/token-signals/out --interval-min 15

After merge, point `cd` at a main checkout instead of the worktree. It appends only; a restart loses at most one cycle. Never run it on mal-fast-0 (headroom is for the tip follower and listeners).

## Expected load
Let M = mints in the file. Per 15 min cycle: 2 rank calls + ceil(M/100) search calls.
- M <= 100: 3 calls/cycle, 288 calls/day (peak 12/hour, far under the 400 cap).
- M = 500: 7 calls/cycle, 672 calls/day.
- With `--pump` (not recommended): + M calls per cycle, e.g. M=100 gives 9,888/day and would hit the cap; do not.

Rows: 2 per mint per cycle (one `jupiter_token`, one `jupiter_rank`). Measured on the trimmed fixture: a `jupiter_token` row is 737 bytes; a rank row is about 170 bytes (estimate). About 0.9 KB per mint-cycle.
- M=100: 192 rows/cycle x 96 = 19,200 rows/day, about 8.7 MB/day (about 43 MB over the 5-day step 0).
- M=500: about 43 MB/day.
Output is one file per UTC day, `signals-YYYYMMDD.jsonl`.

## What this does not do
It measures nothing about price or P&L. Idea 5's bar (>= 3 events/day on aged pump pools) is counted from these rows after the fact; idea 6's bar needs a source that this tool could not reach. Pricing any event is a later step through a pinned/approved reader after a QP ruling and a SEALS-BRIEF / HOLDOUT_LEDGER check. Snapshots have no history, so there is nothing to backtest.
