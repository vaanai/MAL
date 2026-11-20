# Token signal recorder: start plan (JUP-ORGANIC step 0, THREAD-WAKE step 0)

Ideas 5 and 6 of `/data/mal/hunt-1008/ops/reports/2026-11-20/new-strategy-ideas.md`. Both need a forward snapshot recorder before any test can run. Tool: `tools/token_signal_recorder.py` (tests: `tools/test_token_signal_recorder.py`, fixtures: `fixtures/token_signal_recorder/`). This page does not start anything. The manager starts it.

## What it records
No lab data feeds it (QP B1 ruling): it records Jupiter's own lists, so no FLOOR/OSB candidate list is touched and no SEALS-BRIEF line is needed.
- `jupiter_list`: every cycle, the full `GET https://api.jup.ag/tokens/v2/toptrending/1h?limit=100` and `/toporganicscore/1h?limit=100`; one row per entry with list, rank, organicScore, label, isVerified, holderCount. Docs: https://developers.jup.ag/docs/tokens/token-information.md ("Get Category"); keyless 0.5 RPS: https://developers.jup.ag/llms.txt . Checked live 2026-11-20: keyless 200.
- `jupiter_token`: `GET /tokens/v2/search?query=<up to 100 mints>` for the union of both lists (and any optional mints file): organicScore, label, isVerified, tags, holderCount, numTraders/numOrganicBuyers/numNetBuyers (5m/1h/6h), updatedAt. **No price, liquidity, volume or priceChange field is kept** (QP B2).
- `recorder`: one header row per cycle (kind `cycle`): cycle_id, start/end ts, tool sha256 and git commit, mints-file sha256, list-ids sha256, counts, lists_ok, calls, capped, errors (QP B3). Gaps and failed cycles are visible there.
- `pump_coin` (off by default, only for optional mints-file mints): not available. On 2026-11-20 a plain client got a Cloudflare "Just a moment" challenge; pump.fun publishes no public docs or terms for it. The tool does not work around a challenge; any 403/429/5xx or HTML body counts a block, and after 3 in a row it stops the source and writes one `{"blocked": true}` row. THREAD-WAKE therefore has no usable free source today. Do not pass `--pump`.
- No Helius, no wallet, no key. User-Agent `token-signal-recorder/1.0 (research; polite)`. Min 2.5 s between calls (0.4 rps), 3 tries with backoff, Retry-After honoured, hard cap 400 calls/hour, clean exit on SIGTERM. Any per-cycle exception is logged in the header row and the loop continues.

## Mints file: off
The start plan passes no `--mints-file`. If one is ever used it must come from a non-sealed source; FLOOR/OSB candidates are not allowed (that needs a pinned extractor and a SEALS-BRIEF line first).

## MiScusi job (host mal-research-0; plain HTTP, no websocket; mem 256M, cpus 0.3, resumable true, time limit 140 h)
Setup once (the tool also creates the out dir): `mkdir -p /data2/mal/token-signals/out`. Command (sh, no bash-isms):

    cd /data2/mal/wt/token-signal-recorder && exec /data/mal/venv/bin/python tools/token_signal_recorder.py --out-dir /data2/mal/token-signals/out --interval-min 15 --max-hours 132

`--max-hours 132` ends the run by itself (5 days plus a 12 h margin; set the MiScusi time limit to 140 h as backstop). After merge, point `cd` at a main checkout instead of the worktree. It is stateless and append-only: a restart loses at most one cycle, and the cycle headers show the gap. Never run it on mal-fast-0.

## Expected load (no mints file)
Per 15 min cycle: 2 list calls + ceil(U/100) search calls, U = distinct mints in the two lists (at most 200), so 3 to 4 calls/cycle and 288 to 384 calls/day (at most 16/hour, far under the 400 cap). The pump source is off.
Rows per cycle: up to 200 `jupiter_list`, up to 200 `jupiter_token`, 1 header. A `jupiter_token` row is 577 bytes after dropping the price fields (measured on the trimmed fixture); a list row about 230 bytes (estimate). Worst case about 160 KB/cycle, 15 MB/day, about 77 MB over 5 days. Output is one file per UTC day, `signals-YYYYMMDD.jsonl`.

## What this does not do
It measures nothing about price or P&L. Idea 5's bar (>= 3 events/day on aged pump pools) is counted from these rows after the fact; idea 6's bar needs a source that this tool could not reach. The aged >= 24 h pump-graduate filter and the event definitions are applied later, inside the step-1 pinned reader (see the QP report's list of what a step-1 test must declare). Pricing any event is a later step through a pinned/approved reader after a QP ruling and a SEALS-BRIEF / HOLDOUT_LEDGER check. Snapshots have no history, so there is nothing to backtest.
