# P46: decision-row transport, mal-research-0 to mal-fast-0

Written 2026-10-26 (about 18:05Z by `date -u`). Task N7 of [P43 READINESS](../p43-golive/READINESS.md) (PR #956), gap 2 (rank 2 in §4).

This is a build and a measurement. It is not gate evidence. No book has cleared the promotion gate. Everything below used SYNTHETIC rows only. No HV4 or HV6 decision row, ledger or job output was opened.

## 1. The problem

- The HV4 runner writes its hash-chained decision log on mal-research-0, under `/data/mal/arena/haiku-v4/decisions/<bot_id>/<day>.jsonl` (written by `DecisionLog`). HV6 writes to `/data/mal/arena/haiku-v6/`.
- The live C0 bridge (`tools/arena_live_bridge.py` with the `hv4_c0` profile of `tools/hv4_live_c0.py`, PRs #908 and #913) is built for mal-fast-0, the host that would hold the executor key.
- `docs/HOSTS.md` says research-0 holds no keys.
- Nothing moves rows between the two hosts today.

The constraints any transport must meet:

- **Age.** The bridge refuses entry rows older than 5 s (`MAX_DECISION_AGE_MS = 5_000`). The match test needs landing p50 ≤ 1.9 s from the decision instant. Exit rows go through the same hop, so a slow hop adds to exit lag. That is the leak class of the 10-05 probe, whose exit fills leaked −326 to −780 bps.
- **Chain.** The bridge checks every row: `prev`, `seq + 1`, and `sha256(prev + canon(row))`. Any foreign row in the decision file latches `chain_break`. So a "gap row" cannot go into the decision file.
- **Guard.** The bridge reads only through `hv4_live_c0.guarded_read`. That guard refuses a realpath under `/data/mal/arena`, symlinks, hard links (`st_nlink != 1`) and inode swaps. So on fast-0 the rows must be regular files in a root outside `/data/mal/arena`.
- **Seal (QP-RULING-1025).**
  - R2.1: no process reads an HV4 decision row before that arm's read is recorded.
  - R2.2(c): a read-only bind of `decisions/<bot_id>/` only, with sibling arms untouched.

## 2. Options

| | (a) Forwarder: tail on research-0, append on fast-0 | (b) Bridge on research-0 | (c) Runner duplicated on fast-0 |
|---|---|---|---|
| Key placement | fast-0 only (as designed) | a wallet key on research-0. HOSTS.md says no keys there. Needs an owner and Helm policy change. research-0 runs LLM agents and most lab jobs, so the exposure is wider | fast-0 only |
| Added latency | the hop. Measured in §4: p90 21.33 ms (offset method) to 36.22 ms (NTP method) | no hop. But research-0's send path to the RPC/validators is unmeasured, and fast-0 (Frankfurt) is the host that was chosen for that path | none for rows. But the runner's own feed and model calls run on a smaller box |
| The read's book = the live book | yes. Bytes are identical (md5 equal, §4), so "orders equal rows" ties out against the counted log | yes | **no.** A second runner sees its own feed timing and makes its own model calls, so it writes different rows from the registered book. The read would license one log, and live would trade another |
| Capacity | trivial (59 MB peak, 1 CPU, §4) | the bridge is small | HV4 is registered with MemoryMax 45,000 MB (`ARTIFACTS/lab/hv4-registration-1023.md`). fast-0 has ~23 GiB RAM and MiScusi caps a job there at 17 GB, "light jobs only". F arms would also need the Anthropic key on fast-0 |
| Install | a forwarder unit on fast-0, plus a forced-command ssh key on research-0 (Helm or owner, §5) | Helm installs the bridge, probe executor and key on research-0. Policy change | a new frozen-sha install, its own md5 replay, and a feed on fast-0 |
| Seal fit | serves exactly one `bot_id` dir. Refuses a root under `/data/mal/arena` (or an inode alias of it) until the bot's recorded PASS read passes the bridge's own gate, `hv4_live_c0.read_record_problem` (R2.1). Siblings never leave research-0 (R2.2(c)) | R2.2(c) bind on the same host | not the registered book |

**Recommendation: (a), in pull form.** fast-0 dials research-0 over the ssh path that already exists (claude@fast-0 → `ssh mal-research-0`, Cloudflare Access) and runs `decision_forwarder serve` there.

- The key host makes the connection. research-0 gets no credential to the key host. I checked: research-0's `~/.ssh` holds only `authorized_keys`, with no Access token for `ssh-fast`. A push from research-0 would need that credential to be created, and would let a research host log into the key host.
- The hop costs tens of ms against a 1.9 s leg and a 5 s age cap (§4). Removing it (b) is not worth putting a key on research-0.
- (c) fails on two counts: the row identity it needs, and memory.

## 3. What is built (`tools/decision_forwarder.py`)

The forwarder is pure stdlib, except for the read gate it imports for a sealed root. `canon` and `sha256_hex` are copied, and a test proves `canon` is byte-equal to `tools.arena_paper.canon`, so the puller does not need numpy on fast-0.

**`serve`** runs on research-0. In production it is the forced command.

- It tails `<root>/decisions/<bot_id>/*.jsonl`, taking files in sorted order as the bridge reads them. It starts a later file only when the earlier one is fully sent.
- It emits only complete lines, as JSON frames:
  - `row`: file, byte offset, line text;
  - `hello` and `hb` (every 500 ms when idle): the remote's position (file, bytes sent).
- It polls every 10 ms. One pass reads at most 1 MiB (`MAX_PASS_BYTES`), so a long catch-up runs over several passes with bounded memory.
- It refuses, before opening any decision file:
  - a bad `bot_id`;
  - a root under `/data/mal/arena`, or a path with an ancestor that has the arena's inode (a bind mount or alias). The exception is when `--reads-dir` is given and the bot's **recorded** PASS read passes `tools.hv4_live_c0.read_record_problem`, the bridge's own gate from #965, with its provenance rules: canonical reads dir, write-once record, nlink 1, owner/mode, frozen_sha, PASS.
    - No instant is configured by hand, so nothing can be pre-staged with a planned read time (QP #967 item 4).
    - The gate is hv4-only, so an HV6 root is refused until HV6 has its own gate (N10).
    - If the gate cannot be imported, serve refuses (`read_record_gate_unavailable`).
  - in `--from-env` mode, any `SSH_ORIGINAL_COMMAND` other than empty, `resume` (start at the beginning: what the puller sends from an empty local dir), or `resume <YYYY-MM-DD.jsonl> <offset>`.
- It stops with a loud `bad` frame on each of these, and the puller stops on it:
  - a source file that is not regular, has `st_nlink != 1` (a hard-linked copy of arena files placed under another root), or is swapped between lstat and open;
  - a source file that shrinks below what was already sent (the log is append-only);
  - a single line longer than 1 MiB;
  - a line that is not UTF-8.

**`pull`** runs on fast-0.

- **Production command:** `pull --ssh-host mal-research-0 --dst <root> --bot-id <arm>`. This runs `ssh -o BatchMode=yes -o ServerAliveInterval=5 -o ServerAliveCountMax=3 mal-research-0 {resume}`, so ssh itself drops a dead tunnel after about 15 s. `--cmd-json` exists for tests only.
- **Single writer:** it holds an exclusive non-blocking `flock` on `<dst>/transport/<bot_id>.lock` for its whole life. A second puller on the same destination exits refused (`dst_locked`). This guards against the 10-17 triple-writer class.
- **Row check:** it checks every row with the DecisionLog chain rule. It checks that the frame's offset equals the local file size, then appends the same bytes plus a newline (O_APPEND, flock, fsync). A new day file also gets a directory fsync. The chain head (`prev`, `seq`) in memory moves only after the fsync.
- **Position check:** it compares every `hello`/`hb` position with the local (file, offset). Frames are ordered, so any difference is a mismatch.
- **Bounded receive:** the receive buffer is capped at 8 MiB without a newline (`frame_too_long`).
- **Tie-out:** the local log verifies with `DecisionLog.verify` and passes `guarded_read`. Its md5 and day sha256 equal the source's, so tie-out tools see one log.
- **Side files** go under `<dst>/transport/`, never under `decisions/`:
  - `<bot_id>.status.json`, format `decision_forwarder_v1` (read by #969). The keys are unchanged: `schema, bot, state, ms, last_frame_ms, last_remote_ms, seq, f, o, rows, gaps, stall_ms, pid`. It is rewritten every 250 ms while connected. The state is one of `init`, `connecting`, `connected`, `stalled`, `disconnected`, `stopped`, `stopped_mismatch`, or `stopped_error` (new in this revision: an unexpected exception). SIGTERM and SIGINT end with `stopped`, so the file never stays `connected` after the puller is gone. #969 treats every state except `connected` as a gap, so the new value needs no change there.
  - `<bot_id>.gaps.jsonl`, with one row per event:
    - `stall` (no frame for 2,000 ms);
    - `resume` (with `gap_ms`);
    - `disconnect` (with `why`: `eof`, `connect_timeout`, or `silent_reconnect`);
    - `reconnect`;
    - `mismatch`;
    - `local_torn_trimmed`;
    - `error`.
- **Mismatch:** a chain, offset, position, unreadable-frame, oversize-frame or remote-refusal mismatch stops the puller with exit 2. Nothing unverified is appended, so the bridge sees no new rows. Open positions then close only by the bridge's time-stop and stop-loss backstops, as after a `chain_break`.
- **Half-open connection:** a connected feed that is silent for 10,000 ms (`--reconnect-silence-ms`; serve heartbeats every 500 ms) is killed and reconnected (`silent_reconnect`). This does not wait for ssh to notice.
- **Reconnect:** on a disconnect it reconnects with backoff of 0.5, 1, 2 and then 5 s. It resumes at (last local file, local size).
- **Restart:** it re-verifies the local log first. Only its own torn tail is trimmed; the source resends that row.

**`synth`** and **`measure`** exist only for the hop test (§4). `synth` refuses a sealed root, and every row carries `"synthetic": true`. `measure` shell-quotes every remote string, keeping `~/` expansion. It runs serve in the forced-command form (`serve --from-env`, `SSH_ORIGINAL_COMMAND={resume}`) and removes both scratch dirs in a `finally` block.

**Tests:** `tools/test_decision_forwarder.py` has 35 tests, all passing (`/data/mal/venv/bin/python -m pytest -q tools/test_decision_forwarder.py`). They also pass when run inside a tree of current main, which has #965's gate. They cover:

- byte identity over two day files;
- `DecisionLog.verify` and `md5()` equal on both ends;
- `guarded_listdir`/`guarded_read` accepting the forwarded files;
- live tail;
- restart resume with no duplicate;
- tamper: a mismatch stop with nothing appended;
- offset mismatch;
- torn-tail trim;
- stall: `stall` + `resume` gap rows;
- disconnect, then reconnect;
- the forced-command form (`--from-env`) from an empty dst, through a dropped connection and a reconnect that sends `resume <file> <offset>`;
- a silent half-open connection: `stall`, then `disconnect` (`silent_reconnect`), then `reconnect`;
- two pullers on one dst: the second is refused, in-process and over the CLI;
- heartbeat position mismatch;
- the bounded and ordered serve pass;
- line too long;
- shrinking source;
- hard-linked source;
- inode-alias detection of the sealed prefix;
- the read gate wiring, mocked: refused without `--reads-dir`, refused when the gate fails or cannot be imported;
- the capped receive buffer;
- the chain head moving only after the fsync;
- the directory fsync on a new day file;
- SIGTERM leaving status `stopped`;
- an unexpected error leaving `stopped_error` with the v1 keys unchanged;
- the sealed-root refusal over the CLI and remotely;
- resume-string parsing;
- the percentile and clock-offset helpers;
- a local end-to-end `measure`, which also checks cleanup.

## 4. Hop measurement (synthetic, real path)

**Setup**

| | |
|---|---|
| Job | MiScusi #1657 (`j_snLwRrh-F798QA`), on mal-fast-0 as `claude`, code `ea413aad`, script `scripts/research/p46-hop-measure.sh` |
| Ran | 2026-10-26T17:54:43Z to 18:04:28Z |
| Peak memory | 59 MB |
| Path | `ssh -o BatchMode=yes mal-research-0`, which is Cloudflare Access to `ssh-research.tradervaan.com` with the pinned host key. This is the same path a pull forwarder would use |
| Synthetic log | `synth` on research-0 appended 570 rows with `DecisionLog` to `~/scratch/p46-transport-hop/root` (seed 1): 400 rows with exponential gaps of mean 500 ms, 5 bursts of 10 rows back to back (exit waves), then 120 rows with gaps of mean 3,000 ms (latency after idle) |
| Puller | ran on fast-0 into `~/scratch/p46-transport-hop/dst` |
| Clock | a persistent ssh echo channel sampled about once a second (564 samples) |

**Latency definition.** The latency is from the moment `DecisionLog.append` returned on research-0 (after its fsync) to the moment the same line was fsynced into the fast-0 file. That is "visible" to the bridge, which then polls every 50 ms.

**Results** (at code `ea413aad`, flag form `--start-file/--start-offset` on claude's full-shell key. The review fixes since then change no hop step: the same ssh pipe, one frame per line, one fsync per row. A re-run at `31f816f2` in the forced-command form is below, under **Re-run**.)

| Measure | p50 | p90 | p99 | max | n |
|---|---|---|---|---|---|
| append → visible on fast-0, clock offset from the echo channel (−14.891277 ms, ±13.598327 ms) | 17.110386 ms | 21.332367 ms | 25.85584 ms | 103.186371 ms | 570 |
| same, trusting both NTP clocks as equal (offset 0: the row above shifted by +14.891277 ms) | 32.001663 ms | 36.223644 ms | 40.747117 ms | 118.077648 ms | 570 |
| row `ms` (decision instant stamped before append) → visible, echo offset | 21.913062 ms | 26.169315 ms | 30.940589 ms | 112.251614 ms | 570 |
| echo RTT over the persistent ssh channel (clock-free) | 28.847326 ms | 33.93405 ms | 59.778139 ms | 1242.66191 ms* | 564 |

\* The max echo sample is most likely the first one. Its send was timed before the remote python had started, so it includes connect time. RTT min was 26.482527 ms.

**Other results**

- **Integrity:** 570 of 570 rows forwarded. Puller exit code 0. md5 of the day file equal on both hosts (`e39dab2d698b148b013267f9071b5705`). Gap rows: none.
- **Connect costs:** `ssh` cold connect plus file copy took 804 ms. Serve connect (Popen to first frame) took 1,246 ms. A dropped tunnel therefore costs at least ~1.25 s, plus the 0.5 s first backoff, before rows flow again.
- **Clocks:** both hosts use systemd-timesyncd against ntp.ubuntu.com. research-0 showed offset +1.715 ms and root distance 4.783 ms. fast-0 (job #1664) showed offset −4.741 ms and root distance 5.644 ms. The two clocks agree to within about ±10 ms, so the true one-way figure lies between the two latency rows above.
- **Cleanup:** `~/scratch/p46-transport-hop` was removed on both hosts and no forwarder process is left. I checked research-0 by hand and fast-0 in the job log.

**Re-run** (job #1672, `j_bvEAIag4fzXoKw`, code `31f816f2`, 2026-10-26T18:42:21Z to 18:52:07Z, same plan and seed):

- **Path:** serve ran in the forced-command form (`serve --from-env`, `SSH_ORIGINAL_COMMAND` = the puller's `{resume}`, starting from an empty dst).
- **Integrity:** 570 of 570 rows forwarded, puller exit code 0, md5 equal (`638e69f8f0c7e359807cb7477e15faac`), no gap rows.
- **Connect:** serve connect took 1,392 ms.
- **Cleanup:** the cleanup check printed `no-remote-process` and no dirs.

| Measure | p50 | p90 | p99 | max | n |
|---|---|---|---|---|---|
| append → visible, echo offset (−0.603104 ms, ±13.890929 ms) | 17.365253 ms | 22.753946 ms | 48.161801 ms | 123.538018 ms | 570 |
| same, NTP clocks taken as equal (+0.603104 ms) | 17.968357 ms | 23.35705 ms | 48.764905 ms | 124.141122 ms | 570 |
| row `ms` → visible, echo offset | 23.24704 ms | 27.499891 ms | 53.802957 ms | 126.804129 ms | 570 |
| echo RTT (clock-free) | 29.685462 ms | 35.171889 ms | 68.872953 ms | 1275.715615 ms* | 564 |

The two runs' echo offsets differ by 14.288173 ms. That is within the stated ±13.6 to ±13.9 ms bounds of each run, so the one-way p50 is only bracketed, not pinned. Taking both runs and both clock methods, p90 lies between 21.332367 and 36.223644 ms.

**Bar: added p90 ≤ 300 ms.** It was chosen as under 1/6 of the 1.9 s leg and 6% of the 5 s age cap. Measured over both runs and both clock methods: p90 21.33 to 36.22 ms, and max ≤ 124.14 ms. **The bar is met with a wide margin.** Against the 500 ms warm exit poll that READINESS suggests for HV4, the hop adds about 1/14 to the exit lag at p90.

**Not measured:**

- longer runs, other times of day, Access or tunnel outages and their length;
- a real arena row size (synthetic rows are 259 bytes each);
- the bridge's own 50 ms poll and send path;
- research-0's send latency, which option (b) would need.

One 10-minute sample is not a service level. The `status.json` and gap rows exist so that live can see a stall when it happens.

## 5. Open items before any live use (not done here)

1. **Bridge feed-gap halt on `transport/<bot_id>.status.json`.** This is in #969, which needs its own QP pass and md5 replay.
   - #969 halts new buys on any state other than `connected`. It also halts on a status older than 10,000 ms (`FEED_STATUS_MAX_AGE_MS`), or a `last_frame_ms` older than 5,000 ms (`FEED_FRAME_MAX_MS`).
   - In practice the halt fires first when the puller flips the state to `stalled`, after 2,000 ms without a frame (`STALL_MS_DEFAULT`). The 5,000 ms frame cap is the backstop.
   - Exits keep running. The 5 s entry age cap already refuses stale entries, so a stall cannot cause a late entry; it only delays exits.
2. **Forced-command key (Helm or owner).** This needs a dedicated key for the fast-0 forwarder user, with one line in research-0's `authorized_keys`: `restrict,command="cd <pinned install> && PYTHONPATH=. python3 -u tools/decision_forwarder.py serve --from-env --root /data/mal/arena/haiku-v4 --bot-id <arm> --reads-dir /data/mal/arena/reads"`.
   - Serve then opens nothing until `/data/mal/arena/reads/<arm>.json` is a recorded PASS for that arm. Nobody types an instant.
   - The measurement used claude's existing full-shell key, which is fine for a test but not the production posture.
   - SSH access config is Helm's, so it was not touched.
   - **HELM-SHEET note:** do not place copies or hard links of HV4 day files under any other root. Serve refuses hard links (nlink > 1), but a byte copy under a non-arena root would not be gated.
3. **systemd unit on fast-0.**
   - It runs `pull --ssh-host mal-research-0 --dst <root> --bot-id <arm>`, with `--dst` under a root outside `/data/mal/arena` (for example `/var/lib/mal-arena-feed/hv4`). The bridge's `root` points at that root.
   - Run one instance. The lock enforces this.
   - It belongs in the N9 HELM-SHEET. Start it only after the arm's read is recorded (serve refuses before that anyway).
   - A keyless dry run can use a synthetic root now.
4. **Pinned install of the forwarder** at the bridge install sha, with the md5 "orders equal rows" proof covering the forwarded log. Because the bytes are identical, the proof can compare against the research-0 log directly.
5. **HV6:** a recorded-read gate for HV6 bot ids, then the same forwarder with `--root /data/mal/arena/haiku-v6` (N10). Today's gate is hv4-only, so serve refuses an HV6 root.
6. **Runner liveness for exits (QP).** Heartbeats prove that serve is alive, not that the runner is. A dead runner leaves the status `connected` with no exit rows. Entries are covered by the 5 s age cap, but exits need a bridge-side guard: the time stop, plus a runner-liveness check.
7. **A longer measurement** that includes a real Access drop, and the forced-command form on the revised code.

Files: `tools/decision_forwarder.py`, `tools/test_decision_forwarder.py`, `scripts/research/p46-hop-measure.sh`.

## 6. Tape-gap stream carry (added 2026-10-30; QP #969 P7-c and the carriage half of P7-e)

`tools/hv4_gap_relay.py` writes `<out-dir>/transport/<bot_id>.tapegaps.jsonl` on research-0 (rows `{"kind","ms"}`, the whole file rewritten atomically on every pass). The bridge on mal-fast-0 reads `<root>/transport/<bot_id>.tapegaps.jsonl`. The forwarder now carries that one file. This is a build and a synthetic test. It is not installed, and precondition 7 stays open (see "Not done" below).

**serve** takes an optional `--tapegaps-file <abs path>` (also in the `--from-env` form; the flag belongs to the pinned forced command in `authorized_keys`, never to `SSH_ORIGINAL_COMMAND`, which `parse_resume` still restricts to `resume [<file> <offset>]`).

- The path must be absolute, free of `..`, and named exactly `<bot_id>.tapegaps.jsonl`.
- A new frame `{"k":"tg","mt":<source mtime, ms>,"n":<bytes>,"sha":<sha256>,"b":<the text>}` goes out at connect and whenever the file's (inode, size, mtime) changes. The relay replaces the file on every pass, so a pass with identical bytes still sends a frame: the source mtime is the relay's heartbeat.
- The file is opened with `O_NOFOLLOW | O_NONBLOCK` and judged by the open fd, so the relay's own atomic replace between a stat and the open is not an error.
- Sent: only a complete stream. Empty is fine. Every line is an object with exactly `kind` (str) and `ms` (int, `0 < ms <= 4,102,444,800,000`), UTF-8, at most `TAPEGAPS_MAX_BYTES` (8,000,000 = `hv4_live_c0.GAP_LOG_MAX_BYTES`, a test pins it).
- Not sent, quietly: a missing file, a torn tail, or a file that changes while it is read. The bridge-side copy then ages out and the bridge refuses buys.
- Loud `bad` frame and exit (the puller stops): a symlink, FIFO, directory or hard-linked file; an oversize, non-UTF-8 or malformed file; a frame that would not fit the puller's receive cap.
- Seal: if the tapegaps file resolves under `/data/mal/arena`, or it or an ancestor has the arena's inode, the same recorded-read gate as for a sealed root applies. The relay out-dir is meant to be outside the arena (for example `/data/mal/hv4-c0/relay`).

**pull** checks every `tg` frame and trusts nothing from serve. The size, sha and every line are re-validated. The new bytes must start with the bytes already accepted. A shrink, or a change of an already-seen prefix, is a mismatch: exit 2, state `stopped_mismatch`, a `mismatch` row in `gaps.jsonl`, and nothing written. Otherwise it writes `<dst>/transport/<bot_id>.tapegaps.jsonl` as tmp (O_EXCL) + write + `os.utime(source mtime)` + fsync + rename + directory fsync (regular file, nlink 1), so the copy's mtime is the SOURCE pass instant. A dead relay therefore shows stale on mal-fast-0 whatever the wall clock does. Nothing is ever written under `decisions/`. The `decision_forwarder_v1` status keys are unchanged. After a restart the accepted file is the baseline; a damaged local copy is a hard stop (removing it re-baselines, which is the manager's action after review).

**bridge** (`hv4_c0` profile only): `tools/hv4_live_c0.tape_gap_stream_problem` refuses NEW buys on a missing stream (`tape_gap_stream_missing`), one whose mtime is more than `TAPE_GAP_STREAM_MAX_AGE_MS` = 180,000 ms old (`tape_gap_stream_stale`), more than `FEED_SKEW_MS` in the future (`tape_gap_stream_future`, so a forged stamp cannot never age out), or not a plain file (`tape_gap_stream_not_regular`). It is a self-clearing refusal like `transport_stall`: no latch, counted (`tape_gap_stream_refusal`), logged by the entry refusal, and exits keep running. Only a DRY bridge on a `SYNTHETIC_FIXTURE_ROOT` may run without the file; LIVE never.

**Why 180,000 ms** (a declared choice, not a measurement): the relay must complete a pass well inside the 120,000 ms the arena compactor waits before it deletes a closed hour's `.jsonl` (`arena_feed.COMPACT_GRACE_MS`, P7-b), so its cadence is pinned under 120 s. 180 s is 1.5 times that ceiling, which leaves room for one slow pass, and the hop adds tens of ms (§4). Too tight only costs buys; too loose lets a dead relay hide a gap for that long. The relay's real pass time over a full day of tape is NOT measured. It is an acceptance item for the install pack, and this bound must be re-checked against it.

**Synthetic end-to-end delay** (`tools/test_hv4_tapegap_carry.py`, serve subprocess + Puller thread + Bridge on the wall clock, all in one host, bridge polled every ~1 ms, serve `--poll-ms 5`): from the relay's file replace to `gap_tick` noting the carried gap row, 30 samples over 5 runs: per-run min 14 to 20 ms, per-run median 17 to 23.5 ms, per-run max 31 to 45 ms. This is an in-process figure on one host, not a measurement of the research-0 to mal-fast-0 path (§4 has that hop, p90 21.33 to 36.22 ms) and not of the bridge's real 50 ms tick.

**Not done (precondition 7 stays open):**

- Install: the relay on research-0 (unit, cadence, out-dir), the forced-command line with `--tapegaps-file`, the puller unit on fast-0. The ask text goes to the manager for Helm.
- The relay's own P7-a (slot jump / `gap_slots`), P7-b (hour roll and compaction), P7-d (engine cross-test).
- A relay that legitimately drops rows (for example, a tape day file removed or compacted away before P7-b is fixed) makes the next pass a prefix change: the puller stops with exit 2 and the decision feed stops with it, so exits fall to the bridge's time-stop and stop-loss backstops. That is fail closed, and the manager restarts after review.
- A new relay row that sorts BEFORE an already-carried row (same `ms`, or a late-arriving earlier gap record) is also a prefix change and stops the pull. The relay sorts by (ms, kind); this is rare, and it is declared rather than relaxed.
- The first carried stream will contain every historical gap row the relay finds, and the bridge treats each new gap instant as a gap record. The manager's review file must name the newest one before any live start.
