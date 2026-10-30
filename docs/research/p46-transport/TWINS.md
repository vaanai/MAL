# P46b: HV4 C0 twin carry, trips up and twins down

Written 2026-10-30 (`date -u` 15:35Z). Builder lane `hv4-c0-twin-carry`. Companion to [DESIGN.md](DESIGN.md) (decision rows, research-0 to fast-0). Tool: `tools/hv4_c0_twin_relay.py`; tests: `tools/test_hv4_c0_twin_relay.py`.

This is a build, not gate evidence. No book has cleared the promotion gate. Everything below was run on SYNTHETIC fixtures. No HV4 ledger, decision row, `bots.json`, tape or job output was opened.

## 1. The gap

The bridge's twin gate, `LiveGates.twin_refusal` (`tools/h5_arena_gates.py:412`), needs two files on mal-fast-0, where the bridge runs:

- `cfg.twins_file`, one priced twin row per live trip;
- `cfg.twins_file + ".run.json"`, the writer's run record, which carries the pins and the freshness stamp (`twin_pin_problem`, `twin_run_stamp`).

A trip past its exit without a priced twin row blocks every new buy (`twin_gate:past_exit_untwinned`). An open trip without one holds the open cap at 1. No record means no buy at all (`twin_gate:run_record_unreadable`).

The twin is made by `tools/hv4_c0_twin.py` (pinned wrapper over `tools/arena_twin_writer.py`) from two inputs that live on different hosts: the bridge's `parity_dir/trips.jsonl` (fast-0) and the bot's paper ledger `/data/mal/arena/ledger/<bot_id>` (research-0, readable only after the bot's recorded PASS). Nothing moved trips up or twins down, so a live canary would stop buying after its first exit. research-0 has no credential to reach fast-0, so fast-0 pulls (the P46 pattern).

## 2. Data flow

```
fast-0                                                           research-0
trips.jsonl  --(client: validate, now_ms, sha256)--> ssh mal-research-0 'twin <now_ms>' + trips on stdin
                                                       forced command: hv4_c0_twin_relay serve --from-env
                                                         validate trips (schema, size) -> private 0700 tmp dir
                                                         python -m tools.hv4_c0_twin  (UNCHANGED, pinned)
                                                            --trips tmp --ledger-dir <arena>/ledger/<bot> --reads-dir <arena>/reads
                                                            --bot-id <bot> --now-ms <request instant>
                                                            (its read-record gate decides if the ledger may be opened)
twins_file, twins_file.run.json  <--(one reply line: base64 + len + sha256 of each)--+
  client verifies EVERYTHING, then tmp+fsync+rename (twins first, then the record)
```

Client checks before any file is touched: one line, `\n`-terminated, schema, `ok`, bot id, `now_ms` echo, sha256 of the trips it sent, base64 / length / sha256 of both blobs, then the pair itself: `run_ms == now_ms`, `covered_trip_ids ==` exactly the trips sent, `twins_sha256`, `twin_rows`, `config_hash` and `code_sha256` equal to `hv4_live_c0.twin_pins()` (a wrong pin would latch `twin_pin_mismatch` in the bridge for good, so it is refused here instead), every twin row for a sent trip, once, and for this bot. Any failure leaves the old pair in place. The bridge's stamp then ages out and the gate fails closed.

The serve side runs the same pair check on the wrapper's output before it replies.

## 3. Seal argument

- **Paths never come from stdin.** `--bot-id`, `--ledger-dir` and `--reads-dir` are flags compiled into the forced command. stdin carries only trip rows, each checked against an allowlist of the recorder's keys and types (trip ids limited to `[A-Za-z0-9_.-]`), at most 1 MiB.
- **No second path to the ledger.** serve never opens a ledger file and adds no gate of its own. It runs `tools.hv4_c0_twin` unchanged, which accepts a ledger dir under `/data/mal/arena` only if it is `<arena>/ledger/<bot_id>` for THAT bot and `hv4_live_c0.read_record_problem` passes on the reads dir (canonical dir, write-once record, PASS, `frozen_sha`, owner and mode). QP-RULING-1025 R2.1 therefore holds exactly as it does for the bridge. Before the PASS record exists every cycle is refused (`twin_refused:ledger_dir_sealed:read_record_missing`) and nothing is opened. The test `test_sealed_ledger_is_not_opened_without_a_recorded_pass` checks the refusal codes with the arena only as a string.
- **Only twin rows come back.** The wrapper's stdout and stderr are captured and dropped (its notes name trips that have no paper row; its refusal text can name ledger files or trade ids). A refusal crosses the wire as a code from a fixed vocabulary (`CODE_RE`). The reply holds the twins file and the run record, and the client rejects a twin row for any trip it did not send. fast-0 receives no decision row, no other mint, no tape and no ledger line.
- **What a twin row is.** The paper twin of a trip the live bridge itself traded, re-priced at the canary stake. After the PASS this is the quantity DEC-032 s5 compares live fills against. It leaves research-0 only for those trips.
- **Direction.** trips go up (live fills to the lab host, which holds no key); the key host gains no credential. In Phase 2 (below) its key can run only this command.

## 4. Freshness and latency budget

The bridge treats the twin stamp (`run_ms`) as stale after 60 s, as in the future after 5 s, and as invalid if older than the last landed tx (`arena_live_parity.state_problems`, `STALE_MS`).

- **Stamp.** The client takes `now_ms` before it reads the trips file, serve passes it as `--now-ms`, and the client accepts the reply only if `run_ms` equals it. The stamp is on the bridge host's own clock (no cross-host skew), is never later than the snapshot it covers, and an old reply cannot be replayed. serve refuses a request more than 15 s from its own clock (`request_clock_skew`).
- **Bound.** `--interval-s` is refused above 20 and `--timeout-s` above 25. A healthy loop's stamp is at most interval plus one cycle old when the next pair lands, so at most 45 s at the loosest settings, 15 s inside the 60 s bound (asserted at import and tested through `parity_state` + `state_problems`). At the defaults (10 s, 20 s) a healthy stamp is about 10 s plus a cycle.
- **Cycle (estimate, not measured on the real path).** From P46 [DESIGN §4](DESIGN.md): ssh cold connect plus serve start 1.25 to 1.4 s (jobs #1657, #1672). serve compute on this host, synthetic, real wrapper and writer, three runs each: 0 to 10 trips 0.165 to 0.237 s; 50 trips against a 1.56 MB day file 1.38 to 1.43 s. So a cycle should be about 1.5 to 2 s. The writer re-reads three ledger day files per trip, so cost grows with trips times day-file size; the real day-file size is unknown before the PASS.
- **After a landed tx** the first parity run fails P7 by design (the new trip is not yet covered, or the stamp predates the tx), and `LiveGates` then waits `PARITY_RETRY_AFTER_FAIL_MS` = 15 s before it retries for a buy (`h5_arena_gates.py:34`, `:393-409`). The client sends as soon as the trips file differs from the bytes last sent (poll 1 s, at least 2 s after the last start), so coverage arrives in about poll plus cycle, inside that 15 s. The buy-gate dead time after each landed tx is therefore the bridge's 15 s, not the relay interval. With `--poll-s 0` it would be interval plus cycle, close to 15 s at the defaults.
- **Twin row after an exit** appears at the first cycle after the paper row is in the ledger: at most one interval plus a cycle.
- **Failure.** After a failed cycle the next try comes after 2, 4, 8 s, then the interval. Each failure is a stale-clock cost to the bridge; a standing refusal (before the PASS) is logged once, then every 30th try.

## 5. Install

**Phase 1: a MiScusi job on fast-0 as `claude`, over the existing `ssh mal-research-0` path** (as the P46 hop measurement did). No Helm change.

```
python3 -u tools/hv4_c0_twin_relay.py client --bot-id <ARM> --trips-file <parity_dir>/trips.jsonl --twins-file <twins_file> \
  --ssh-host mal-research-0 --remote-template "cd <PINNED_INSTALL> && SSH_ORIGINAL_COMMAND='{request}' PYTHONPATH=. <PYTHON_WITH_NUMPY> -u tools/hv4_c0_twin_relay.py serve --from-env --bot-id <ARM> --ledger-dir /data/mal/arena/ledger/<ARM> --reads-dir /data/mal/arena/reads"
```

- `<PINNED_INSTALL>` is an export on research-0 at the bridge install sha (the wrapper refuses unless the writer's sha equals `TWIN_WRITER_SHA256`). `<twins_file>` must equal the bridge's `cfg.twins_file`; its directory must be writable by the job's user and read-only to the bridge's user (so the bridge cannot write its own twin).
- Light: stdlib only, under 50 MB. `flock` on `<twins_file>.relay.lock` keeps it single. Status: `<twins_file>.relay-status.json`. Stateless, so a restart is safe.
- **Posture cost.** claude's key on fast-0 runs an arbitrary shell as `claude` on research-0 (that path already exists and the manager uses it). The job adds no new capability, but the bridge's host then holds a credential worth the whole research host for as long as the live path depends on it. That is the cost Phase 2 removes.

**Phase 2: a forced command (Helm's, via the owner).** One line in research-0's `authorized_keys` per arm that goes live, on a dedicated key for the fast-0 relay user (a forced command is fixed per key, so two live arms need two keys):

```
restrict,command="cd <PINNED_INSTALL> && PYTHONPATH=. <PYTHON_WITH_NUMPY> -u tools/hv4_c0_twin_relay.py serve --from-env --bot-id <ARM> --ledger-dir /data/mal/arena/ledger/<ARM> --reads-dir /data/mal/arena/reads" ssh-ed25519 <relay public key> hv4-c0-twin-relay-<ARM>
```

The client then runs `client --ssh-host <alias for that key> ...` with the default template (the remote command is just `twin <now_ms>`, read by serve as `SSH_ORIGINAL_COMMAND`). The account that runs it must be able to read `/data/mal/arena/ledger/<ARM>` and `/data/mal/arena/reads/<ARM>.json`, which must be owned by root or that account and not group or other writable (`read_record_problem`). Pair it with the decision forwarder's line (DESIGN §5 item 2); do not place copies or hard links of HV4 files under any other root.

## 6. Not measured, open

- **Real-path cycle time.** Not measured. A keyless dry run with a SYNTHETIC ledger dir in research-0 scratch (as job #1657) would give the cycle without touching the arena.
- **The real ledger dir.** The wrapper requires every entry of the ledger dir to be a regular file with `st_nlink` 1. That cannot be checked before the PASS without listing the sealed dir. If something else lives there, the first post-PASS cycle shows `twin_refused:ledger:<reason>` in the status file and the bridge stays closed.
- **Writer cost on the real day files** (section 4).
- **Pair atomicity.** Two renames, back to back, twins first. A parity run between them sees a hash mismatch (`twin_run_stamp` None), costs one soft P7, and cannot hard-latch.
- **Not on QP's removal list** (#959 comment 6423545454, #969 comment 6424766650): found 2026-10-30. It does not discharge a listed item. Item 4 (a trip-bearing synthetic end-to-end through the bridge) and the install pack still need to name this tool and Phase 1 or 2.
- This PR touches no runner, bridge or pinned file, so no md5 decision-equivalence replay is owed by it; the install pack must pin this file's sha beside the forwarder's.
