# BOOST watch (P66): early warning for the Q50 canary

Tool: `tools/boost_watch.py` (tests: `tools/test_boost_watch.py`). Paper/ops only: public RPC, no key, no websocket. Library rows `w2-pump-idl-1`, `w2-pump-idl-2`.

## What it watches

Every poll (60 s): the PumpSwap GlobalConfig account (`ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw`): `boost_enabled` (byte 939), `boost_authority` (907), `admin`, whole-account sha256; new signatures on the config account, the admin and the boost authority, each fetched and scanned (top-level and inner instructions) for toggle_boost / set_boost_authority / update_admin / update_fee_config / disable and the other admin instructions listed in the tool. One JSON line per poll in `~/mal-data/boost-watch/boost_watch.log`; state in `state.json` beside it.

Exit 3 = change (boost_enabled not true, layout/hash/admin/authority differs from baseline, a watched instruction, a new transaction in which the config account is writable; swaps list it read-only and do not alert). Exit 4 = reads failing 10 polls in a row (blind, not a change). The alert is latched: after exit 3 every later run exits 3 until the manager runs `--ack` after review (an unknown or false BOOST state needs the owner's line first).

Measured: unit tests on the recorded mainnet GlobalConfig (boost_enabled true at the 2026-10-08 recording) and constructed transactions. Not measured: a real toggle_boost transaction was never recorded, so the instruction scan is tested on constructed data only. Live result so far: MiScusi job #1715 on mal-fast-0 (public RPC, first version, about 14 calls per poll) ran 19 minutes and exited 4 (READ_FAIL RpcError x10): the public endpoint rate-limited it. That was a blind exit, not an alert. The call budget below and the Helius option are the fix; they are untested over days.

## Call budget and credits

Per poll: 1 getAccountInfo (config), 3 getSignaturesForAddress (admin, boost authority, config; limit 10 each), and at most 3 getTransaction calls in total (admin and authority first; the newest three config signatures at most, since swaps list the config read-only and the hash and byte checks cover them; a backlog of admin/authority signatures beyond the budget is fetched on the next poll). A quiet poll is 4 calls; the budget is at most 7; plan on about 5 calls/min. Retries after a 429/403/5xx/network error (exponential backoff, 1 s base, 20 s cap, jitter) happen inside the call and are counted in `calls_per_poll` in the log line; a poll counts as failed only when the retries are used up.

Credits (CLAUDE.md credits rule): on Helius each of these methods is 1 credit, so about 5 calls/min x 1,440 min = about 7.2k credits per day, against the roughly 20M extra credits the owner set aside. Unit: the fast-listener key at /var/lib/mal/fast-listener/helius.env (read only through `--rpc-env`). What gets faster: the watch stops hitting the public endpoint's limit; the measured effect is not yet known and goes in the job's first-day report.

RPC choice: public RPC is the default. `--rpc-env FILE` reads the Helius URL (or the API key, which becomes a Helius URL) from an env file; the first variable whose value is an http(s) URL wins, else the first variable with KEY in its name. The URL and key are never printed, logged or committed; the log carries only `rpc=helius` or `rpc=public`.

This does not replace the A3 daily check (`tools/pump_structure_monitor.py`, already a hard halt on a GlobalConfig hash change or a BOOST flag); it shortens the detection time from up to a day to about a minute.

## MiScusi loop job (mal-fast-0, 256 MB)

Submit with `miscusi_job_submit` (host `mal-fast-0`, memory 256 MB, `resumable: false`). The command runs under sh:

```
cd ~/MAL && exec python3 -m tools.boost_watch --loop --interval 60 --max-fail 10 --rpc-env /var/lib/mal/fast-listener/helius.env --log-dir ~/mal-data/boost-watch
```

The tool is the loop (one poll per minute), so there is no shell loop to crash. Exit 3 ends the job as failed with the alert line on stdout: that is the stop-and-report signal. Exit 4 also ends it (blind): report it as "watch blind", not as BOOST off. After a stop, restarting the job alerts again at once (latched) until `python3 -m tools.boost_watch --ack` is run by the manager after review (owner's line first for a false or unknown BOOST state). `git -C ~/MAL pull --ff-only` first, so the job runs the merged tool. Do not stack two copies (they share one state file).

## DEC-032 pre-live note: BOOST-off alert and a Q50 canary

Proposal for the pre-live checklist of the Q50 canary (DEC-032 section 5 and section 8); not an amendment until the manager and owner adopt it.

1. **Before C0:** the job above runs for at least 24 h with no READ_FAIL streak and its baseline shows `boost_enabled` true. An absent or dead watch job is a P7-type condition (missing state): no buy until it is running. Fail closed.
2. **On exit 3 (soft halt):** NOT BUILT YET: exit 3 does not create `STOP` today; the job just ends failed. OWED before C0: wire the alert into the executor's `STOP` path. Once wired it creates `STOP` (no new buys) through the same alert path as the other soft halts. Open positions exit on their own rules. No retune of the book.
3. **Manager review (same day):** read the alert line. Classify: (a) `boost_enabled` false or a `toggle_boost` with `enabled` false: BOOST is off; (b) other config write, fee change, authority change, layout change: BOOST state unknown. The manager checks the account directly and the A3 record. A harmless fee-config write may be cleared with a written note and `--ack`; a false or unknown BOOST state may not.
4. **Then the owner:** a BOOST-off finding is a hard halt (O) like A3: resume only on the owner's own dated line after a re-read that shows the book's edge survives without BOOST, which is a new pre-registered read, never this canary's fills. Until then the canary stays stopped and the paper book keeps running for the record (its rows after the alert instant are labelled "BOOST off").
5. **Exit 4 (blind):** soft halt, manager restarts the watch; if blind for more than 30 minutes while positions are open, the manager reads the account by hand each 10 minutes until it is back.
6. Every report on the canary after an alert carries the alert instant, and trades after it are never pooled with trades before it.

## Owed

- Wire a watch alert (job failure or log line) into the canary's `STOP` creation. Until then exit 3 only stops the job and notifies via MiScusi.
