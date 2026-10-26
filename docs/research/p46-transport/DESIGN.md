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
| Seal fit | serves exactly one `bot_id` dir. Refuses a root under `/data/mal/arena` before `--after-read-ms` (R2.1). Siblings never leave research-0 (R2.2(c)) | R2.2(c) bind on the same host | not the registered book |

**Recommendation: (a), in pull form.** fast-0 dials research-0 over the ssh path that already exists (claude@fast-0 → `ssh mal-research-0`, Cloudflare Access) and runs `decision_forwarder serve` there.

- The key host makes the connection. research-0 gets no credential to the key host. I checked: research-0's `~/.ssh` holds only `authorized_keys`, with no Access token for `ssh-fast`. A push from research-0 would need that credential to be created, and would let a research host log into the key host.
- The hop costs tens of ms against a 1.9 s leg and a 5 s age cap (§4). Removing it (b) is not worth putting a key on research-0.
- (c) fails on two counts: the row identity it needs, and memory.

## 3. What is built (`tools/decision_forwarder.py`)

The forwarder is pure stdlib. `canon` and `sha256_hex` are copied, and a test proves `canon` is byte-equal to `tools.arena_paper.canon`, so the forwarder does not need numpy on fast-0.

**`serve`** runs on research-0. In production it is the forced command.

- It tails `<root>/decisions/<bot_id>/*.jsonl`. Files are taken in sorted order, as the bridge reads them.
- It emits only complete lines, as JSON frames:
  - `row`: file, byte offset, line text;
  - `hello`;
  - `hb` every 500 ms when idle.
- It polls every 10 ms.
- It refuses:
  - a bad `bot_id`;
  - a root under `/data/mal/arena` unless `--after-read-ms` is given and has passed (R2.1);
  - in `--from-env` mode, any `SSH_ORIGINAL_COMMAND` other than `resume <YYYY-MM-DD.jsonl> <offset>`.

**`pull`** runs on fast-0.

- It runs the serve command and checks every row with the DecisionLog chain rule.
- It checks that the frame's offset equals the local file size, then appends the same bytes plus a newline (O_APPEND, flock, fsync).
- The local log therefore verifies with `DecisionLog.verify` and passes `guarded_read`. Its md5 and day sha256 equal the source's, so tie-out tools see one log.
- Side files go under `<dst>/transport/`, never under `decisions/`:
  - `<bot_id>.status.json`: state (`connected` / `stalled` / `disconnected` / `stopped_mismatch`), the last frame instant, the last remote heartbeat, seq, file and offset;
  - `<bot_id>.gaps.jsonl`: one row per `stall` (no frame for 2,000 ms), `resume` (with `gap_ms`), `disconnect`, `reconnect`, `mismatch` and `local_torn_trimmed`.
- A chain, offset, unreadable-frame or remote-refusal mismatch stops the puller with exit 2. Nothing unverified is appended, so the bridge sees no new rows. Open positions then close only by the bridge's time-stop and stop-loss backstops, as after a `chain_break`.
- On a disconnect it reconnects with backoff of 0.5, 1, 2 and then 5 s. It resumes at (last local file, local size).
- On restart it re-verifies the local log first. Only its own torn tail is trimmed; the source resends that row.

**`synth`** and **`measure`** exist only for the hop test (§4). `synth` refuses a sealed root, and every row carries `"synthetic": true`.

**Tests:** `tools/test_decision_forwarder.py`, 18 tests, all pass (`/data/mal/venv/bin/python -m pytest -q tools/test_decision_forwarder.py`, 7.58 s). They cover:

- byte identity over two day files;
- `DecisionLog.verify` and `md5()` equal on both ends;
- `guarded_listdir`/`guarded_read` accepting the forwarded files;
- live tail;
- restart resume with no duplicate;
- tamper → mismatch stop with nothing appended;
- offset mismatch;
- torn-tail trim;
- stall → `stall` + `resume` gap rows;
- disconnect → reconnect;
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

**Results**

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

**Bar: added p90 ≤ 300 ms.** It was chosen as under 1/6 of the 1.9 s leg and 6% of the 5 s age cap. Measured: p90 21.33 to 36.22 ms, and max ≤ 118.08 ms on either clock method. **The bar is met with a wide margin.** Against the 500 ms warm exit poll that READINESS suggests for HV4, the hop adds about 1/14 to the exit lag at p90.

**Not measured:**

- longer runs, other times of day, Access or tunnel outages and their length;
- a real arena row size (synthetic rows are 259 bytes each);
- the bridge's own 50 ms poll and send path;
- research-0's send latency, which option (b) would need.

One 10-minute sample is not a service level. The `status.json` and gap rows exist so that live can see a stall when it happens.

## 5. Open items before any live use (not done here)

1. **Bridge reads `transport/<bot_id>.status.json` as a feed-gap halt** (READINESS N6 item 10). With state not `connected`, or `last_frame_ms` older than `stall_ms`, it refuses new buys, and exits keep running. This is a pinned-code change to the bridge, so it needs its own tests and md5 replay. The 5 s age cap already refuses stale entries, so a stall cannot cause a late entry. A stall only delays exits.
2. **Forced-command key (Helm or owner).** This needs a dedicated key for the fast-0 forwarder user, with one line in research-0's `authorized_keys`: `restrict,command="python3 -u <pinned path>/decision_forwarder.py serve --from-env --root /data/mal/arena/haiku-v4 --bot-id <arm> --after-read-ms <read instant>"`. The measurement used claude's existing full-shell key, which is fine for a test but not the production posture. SSH access config is Helm's, so it was not touched.
3. **systemd unit on fast-0.** It runs `pull` with `--dst` under a root outside `/data/mal/arena` (for example `/var/lib/mal-arena-feed/hv4`). The bridge's `root` points at that root. It belongs in the N9 HELM-SHEET. Start it only after the arm's read is recorded, never before (R2.1). A keyless dry run can use a synthetic root now.
4. **Pinned install of the forwarder** at the bridge install sha, with the md5 "orders equal rows" proof covering the forwarded log. Because the bytes are identical, the proof can compare against the research-0 log directly.
5. **HV6:** the same forwarder with `--root /data/mal/arena/haiku-v6` and the `hv6_c0` bot ids (N10).

Files: `tools/decision_forwarder.py`, `tools/test_decision_forwarder.py`, `scripts/research/p46-hop-measure.sh`.
