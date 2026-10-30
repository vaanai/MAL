# DRAFT HELM-ASK: HV4 C0 bridge on mal-fast-0 (NOT SENT, NOT IN THE HELM CHANNEL)

**DRAFT, written 2026-10-30 (`date -u` 15:3xZ) by the hv4-c0-ready lane for manager18. Do not copy it into `/data/mal/hunt-1008/push-1016/hunt29/p1/` or relay it until the manager has filled every `<FILL>` and checked the preconditions of its part.** Helm acts only on Vaan's own word, so the owner relays it. Nothing here asks for a firewall, sshd_config, cloudflared or Cloudflare Access change, or touches `/opt/miscusi`. No key, wallet address or `.env` is named or opened. No book has cleared the promotion gate; a C0 canary is an "unpromoted execution measurement" (DEC-032 §0).

Authority: the install pack runbook `docs/runbooks/hv4-c0-bridge.md` (PR `claude/hv4-c0-install-pack`, not merged at writing). Where this draft and the merged runbook differ, the runbook controls. Host: mal-fast-0 unless a step says otherwise.

---

## PART 1 — NOW, before any HV4 read: keyless DRY install on a SYNTHETIC root

**Send when:** the install pack PR and the code PRs it depends on have merged with QP OK and a reviewer verdict, and the manager names the sha Y. Those are the start gate, the tape-gap relay and carry, the twin carry, the send rows, the orders proof and the gap hold (#1090). If only the install pack has merged, Part 1 can still run at that sha, because the synthetic dry path exists on main today. **Best case:** Y already carries the one-line `LIVE_REFUSAL_ACTIVE = False` flip, after its own QP pass and after the DEC records (Am.5, owner-file rule) merge. Then nothing in Part 2 needs a re-install.

**Why now:** it moves the install, venv, unit and file-permission work off the critical path after a PASS. QP-RULING-1025 R2.1 allows a pre-read bridge test only on synthetic fixtures, so Part 1 never touches an HV4 decision row.

1. **User and directories** (runbook "Provision"): the unprivileged user `mal-arena-bridge` (no shell, no home), the state, parity and tie-out dirs, `/etc/mal-arena-live/` root:root 0755, `/etc/mal-hv4-c0/` and `/etc/mal-hv4-c0/gap-clear/` root:root 0755, `/var/lib/mal-hv4-c0/window/` with the owner and mode the runbook gives. **Do not create** `/etc/mal-arena-live/LIVE_OK`, `/etc/mal-arena-live/TIER`, any `CANARY_DEC.md`, or any key directory content.
2. **Pinned tree and venv** at Y: `/opt/mal-arena-bridge/<Y>/` from `git archive`, plus the manifest `sha256sum -c` (0 failures), plus the venv from the pack's requirements file. Send the manifest sha256.
3. **Unit and binds** from the pack: the dry instance unit, with no credential, environment-file or `--live` line (the pack's checks). `daemon-reload`, do not enable.
4. **Synthetic rehearsal, bot id `hv4-SYN-X` ONLY.** The bridge writes a write-once 14-day window file per bot at its first start, DRY included (QP #965 N-C). A rehearsal under `hv4-BIG-X`, `hv4-CON-X`, `hv4-BIG-F` or `hv4-CON-F` would start that arm's real 14-day clock before any PASS. Run the pack's keyless checklist script as `mal-arena-bridge`. Expect `RESULT PASS`. Then run one `--once` dry start on the pack's synthetic marker root. It must print `mode=dry`. Send both outputs. The script prints paths and PASS/FAIL only.
5. **Read-record directory, exact:** `sudo install -d -m 0755 -o root -g root /data/mal/arena /data/mal/arena/reads`. Both directories are root:root 0755. `/data/mal/arena` must exist because the bridge's sealed-alias check fails closed without it. `reads` must be exactly that realpath (`hv4_live_c0.READS_DIR_CANON`). Leave it empty until Part 2. A start without it refuses `read_record_dir_absent`, and the refusal message names this command (PR #1100). The pack's keyless checklist checks both directories (`arena_prefix_root_0755`, `reads_dir_root_0755`).
6. **Report back:** that `/var/lib/mal-live/STOP` and `/var/lib/mal-live/HALT` are still absent (job #1837, 2026-10-30T15:10Z: absent). These are the bridge's default stop files.

Part 1 takes about 1 to 2 h of Helm time. It needs no owner money and no key.

---

## PART 2 — at the manager's signal, ONLY after an HV4 arm's read is RECORDED as PASS and its DEC copy has merged (install sha X)

**Send when ALL hold (the manager checks; Helm is asked after, not before):**
1. The arm's Am.15 read record exists with status PASS. QP has checked the read on its exact files, including the seal statement. The worst-in-slot record is measured with mean > 0. Ex-best-day is > 0 on every deciding sub-cell. The mint fetch is done (DEC-031 Am.3 item 6; Am.15 items 5 and 6).
2. The arm's DEC copy (`DEC/DEC-0NN-hv4-<arm>-c0-canary.md`, from `ARTIFACTS/lab/c0-drafts/hv4/DEC-copy-<arm>-STAGE2-SKELETON.md`) has merged with QP OK and a reviewer verdict at its head, carrying the bare `OWNER_CANARY_CONFIRMED: bot_id=<arm>` line. The PR is records only, with no code change, so the installed sha X stays as it is, unless X lacks the `LIVE_REFUSAL_ACTIVE = False` flip: then step 1 re-installs at the sha that has it.
3. The md5 orders-equal-rows proof at the installed sha X is EQUAL (`tools/hv4_c0_orders_proof.py`). It can run before the read, as soon as X is installed.
4. The owner has, in his own message, told Helm to proceed and named the funding. The standing line covers the C0 OK (DEC-032 Am.1 item 3). It does not cover the funding transfer or the install.

**Steps:**
1. **Install check:** the installed tree is the sha X the manager names, and the manifest check shows 0 failures. A re-install is needed only if Part 1's Y lacks the flip. Send the manifest sha256.
2. **Read record copy:** `ssh mal-research-0 cat /data/mal/arena/reads/<arm>.json` to a temp file on fast-0, then `sha256sum`. It must equal the read job's `RECORD … sha256` line, which the manager gives as `<FILL:record_sha256>`. Then `install -m 0444 -o root -g root <tmp> /data/mal/arena/reads/<arm>.json`. Write it once. The bridge pins the record's (dev, ino, sha256), and a later rewrite halts it for good (QP N9). The manager can do this step with sudo on fast-0 if Helm prefers.
3. **Owner record, on the owner's word:** `git show <DEC merge sha>:DEC/DEC-0NN-hv4-<arm>-c0-canary.md`, installed root:root 0644 at `/etc/mal-hv4-c0/<arm>/CANARY_DEC.md`. That is the per-bot path from the start-gate PR; at main c92f23e3 the code still reads the shared `/etc/mal-hv4-c0/CANARY_DEC.md` with a compiled pin. The file must contain exactly one line `OWNER_CANARY_CONFIRMED: bot_id=<arm>`. Send its `sha256sum`. No code pin is involved.
4. **Streams** (the manager runs these as MiScusi jobs; Helm only confirms that the bind paths exist and are read-only to the unit):
   - the decision forwarder pull for `<arm>` into the forwarded root;
   - the twin relay client;
   - the gap relay job on research-0 and its carriage;
   - the daily tie-out job.
   Phase 2 (later, Helm): a dedicated forwarder user and forced-command key lines in research-0's `authorized_keys`, as in `docs/research/p46-transport/DESIGN.md` §5 item 2.
5. **Dry run at X, at least 1 h, real arm id, keyless:** the dry instance with `<arm>` on the forwarded root. This is allowed now that the read is recorded (R2.1). It starts the arm's 14-day window (N-C), which is accepted. Report 0 build errors, the startup line and the refusal/latch counts by kind only. No row contents.
6. **Wallet**, only after step 5 passes: generate a fresh canary wallet key as for the probe wallet (`docs/runbooks/probe-wallet.md`), root-owned, in the location the live drop-in loads it from. Report the PUBLIC key only. The owner funds `<FILL:fund_sol>` SOL (0.35 to 1.00; the lane recommends 0.35) from his own address. Withdraw stays with Helm. Record the funding tx and the starting balance.
7. **live config** from the pack template: `funded_lamports` = `<FILL>` and `end_ms` = `<FILL:end_ms>`. `end_ms` must not exceed min(first send + 14 d, 1794143100000 = 2026-11-08T13:05:00Z); the code refuses more than 14 d from the first start. Send the config `sha256sum`.
8. **Live drop-in** from the manifest-verified copy (wallet credential, RPC key file `/var/lib/mal/fast-listener/helius.env`, never printed). `daemon-reload`, do not start.
9. **Gate files**, root:root 0644, on the owner's say-so:
   - `/etc/mal-arena-live/TIER` = `T1`, exactly;
   - `LIVE_OK` containing exactly `<arm>` at the path the code names.
   Then run the bridge `--once` in live config. It must not print `startup_refused`. Send the line.
10. **Start on the manager's explicit go:** `systemctl start` of the live instance. Report the startup line (`arena_live_bridge mode=live bot=<arm> stake=50000000 floor=…`).

## Stop / kill (standing)
- `STOP` = no new buys.
- `HALT` = freeze.
- Removing `LIVE_OK` routes new entries to DRY at once.
The manager takes none of these on a host unit; Helm does.

## Not asked
No ufw, sshd_config, cloudflared or Cloudflare Access change. No `/opt/miscusi` change. No C1 files. No second wallet unless the manager asks for a second HV4 arm, which needs its own DEC copy, wallet and funding.
