# DRAFT HELM-ASK: Q50 C0 canary install (NOT SENT, NOT IN THE HELM CHANNEL)

**DRAFT. Written 2026-10-25 from `date -u` (18:3xZ). Do not copy this file into `/data/mal/hunt-1008/push-1016/hunt29/p1/` or relay it until every `<FILL>` is filled and the preconditions below are true.** Nothing here asks for a firewall, sshd, cloudflared or Cloudflare change. No key, no wallet address, no `.env` is named or opened by this draft.

Sheet: `HELM-SHEET.md` (hunt29/p1) section F, which is the authority; this draft only lists what the manager sends. Host: mal-fast-0 only. The Q50 keyless dry run PASSED at 0526d5cd (HELM-REPLY-1024).

## Send only when ALL of these are true (the manager checks; Helm is asked after, not before)
1. The Q50 DEC-031 gate read is a PASS, with QP's OK on the exact files, and the Q50 DEC-032 copy has merged with QP OK and a reviewer verdict on head `<FILL:head_sha>`, containing `OWNER_CANARY_CONFIRMED: bot_id=h5v1q50-boostfloor-a1` (the executor refuses a live start without it in `DEC/DEC-*canary*.md`).
2. QP's seal statement for the EXP-024 Look-2 seal is in the OK (see readiness note section 2).
3. The owner has, in his own message: said yes to the C0 ask, funded the wallet, and told Helm to act. The standing line (DEC-032 Am.1 item 3) covers the OK; it does not cover the funding transfer or the install.
4. Install sha `<FILL:install_sha>` = a main merge that has the executor, ladder (#861/#862) and the FEED_NOTE flip; md5 replay EQUAL at that sha; fee decided: the compiled default 55,000 / 55,000 / 150,000 (readiness note §2b item 1; QP ruling section 5), and the install sha X is the merge of the DEC copy (QP-PRERULING-1026-n2-n4: 0526d5cd with job #1481 is the reference and does NOT qualify as the install, because the executor reads the owner line from `DEC/` in its installed tree). X needs its own EQUAL replay reproducing #1481's counts (C2) and its own 1 h dry run.

## Ask (to Helm, through the owner)
Please, on mal-fast-0, in this order, each step only on the owner's instruction:
1. **Re-install** the pinned tree at `<FILL:install_sha>` (manifest diff must be empty), as in HELM-REPLY-1024 section 1. Report `sha256sum` of the manifest and the unit.
2. **Wallet (ORDER REVISED, QP-PRERULING-1026-n2-n4 N2 (a) item 4).** Do this step only AFTER the 1 h keyless dry run at the new install sha X has passed (step 9a below): HELM-SHEET section E expects 0 files in `/etc/mal-h5-arena-key` during the dry run. The owner may fund in parallel with the dry run. Generate a fresh canary wallet key as for the probe wallet (`docs/runbooks/probe-wallet.md`), root:root 0400 under `/etc/mal-h5-arena-key/`. Report the PUBLIC key only. The owner funds `<FILL:fund_sol>` SOL (0.35 to 1.00) from his own address; withdraw stays with Helm (`FILL:withdraw_holder`). Record the funding tx and starting balance.
3. **live.json.** Write `/etc/mal-h5-arena/live.json` (root:root 0644) from `ARTIFACTS/lab/c0-drafts/live.json.DRAFT-NOT-INSTALLED` with these two values filled and the three `_`-prefixed notes removed: `funded_lamports` = `<FILL>`, `end_ms` = `<FILL:end_ms>` (at most first send + 14 days, never later than 1793923200000 = 2026-11-06T00:00Z). Send back `sha256sum /etc/mal-h5-arena/*.json` (the DEC copy's `FILL:executor` config sha256).
4. **Live drop-in** from the manifest-verified copy: `mal-h5-arena-executor-live-pinned.conf` to `/etc/systemd/system/mal-h5-arena-executor.service.d/live.conf`, then `daemon-reload`. Do not start.
5. **Watchdog** timer re-enabled (it was stopped after the dry run): `systemctl enable --now mal-h5-arena-watch.timer`, with `ARENA_WATCH_WALLET=<public key>` and `ARENA_WATCH_FUNDED_SOL` set in watch.env; test message sent. Alert target: `<FILL:alert_target>`.
6. **Tie-out** daily job and **twin writer** job: the manager submits both as MiScusi jobs; Helm only confirms `/srv/mal-h5-arena/{twins,tieout}` binds exist and are read-only to the unit.
7. **Gate files**, as root 0644, only on the owner's say-so: `/etc/mal-h5-arena/TIER` = `T1`; `/etc/mal-h5/LIVE_OK` (empty; the old one is in `/root/disabled/`, restore only with Vaan's word); `/etc/mal-h5-arena/LIVE_OK` = `h5v1q50-boostfloor-a1` (one line). Then `--status` with live.json must print `live_start=none arena_live_ok=valid shared_live_ok=valid arena_tier=T1 c0_guard=ok sell_pin=ok`; send the line.
8. **Wallet-wide files.** Report whether `/var/lib/mal-live/STOP` or `HALT` exist (they stop this executor too). The manager decides in writing whether to remove them.
9a. **Dry run at X first (moved before step 2).** After step 1, run the keyless 1 h dry run of HELM-SHEET section E at X (0 build errors, `/etc/mal-h5-arena-key` empty). The md5 replay at X runs as a manager job in parallel with step 1. Only then step 2 (key).
9. On the manager's explicit go (after a 1 h dry run on the live.json minus key, if the manager asks for one): `systemctl start mal-h5-arena-executor`. Report the two startup lines (`mode=live ... user=<public key>`, `feed_start=verified`).
10. **Ladder log ownership** check after the first start (HELM-SHEET section G): `mal-live:claude 2750` dir, `mal-live:claude 640` file. For a later C1 step only: `TIER ceiling` and `LADDER_UNLOCK` as root (DEC-032 Am.2 item 9); not part of this ask.

## Stop / kill (standing)
`STOP` (no new buys), `HALT` (freeze), or `rm /etc/mal-h5-arena/LIVE_OK` stops new buys at once. The manager takes none of these on a host unit; Helm does.

## Not asked
No ufw, sshd_config, cloudflared or Cloudflare Access change. No change to `/opt/miscusi`. No C1 files.
