# Runbook: r4bt-a2 fresh dry host, P0 and decision host (DEC-031 Amendment 30)

Manager-only checklist, mal-fast-0. **Paper only.** No key, wallet, lane executor or C0 unit is involved. Workers do not start, stop or restart anything here.
Rules: DEC-031 Amendment 30 (items cited as "Am.30 n") and Amendment 27. Where this file and the DEC differ, the DEC controls.
Written 2026-11-12 from documents and code only. Nothing on mal-fast-0 was checked: no SSH, no job view. Every "check" below is for the manager to do at install.

## 0. Gates before anything starts (Am.30 items 6 and 8)

- [ ] Amendment 30 merged (QP OK on the exact head, reviewer verdict).
- [x] **EXP-027 A2 C4 merged:** `1312baca96d17985502b2bf0c73e044fb3724d1f` (#1326). It came from the late C3 job #2530; #2053 had been cancelled 11-08T00:34Z. QP Addendum 3 accepted the lateness.
- [x] **EXP-027 A2 C5 merged:** `757e045f003cfcc3d118791da42147c2b4ba461f` (#1329). EXP-027's §11 seal ended at this merge, so r4bt-a2 needs no Option Z.
- [ ] Dry host start no later than **2026-11-15T12:05Z** (Am.30 item 6 shows the derivation). A later start, or a host not caught up at 2026-11-16T00:00Z, makes P0 NOT_DECIDABLE (the first operational ND, Am.30 item 14).
- [ ] SEALS-BRIEF reader line added (text in the Amendment 30 PR body). Add the ledger Forward walk 2 note line and the Am.23 item 9 pricer reader line before the dry host's first read.
- [ ] `systemctl show user-1002.slice -p MemoryCurrent -p MemoryMax -p MemoryHigh`. The host's scope is 5120M (M3: count 5 GB before admitting any other fast-0 job). dry-long-1101a's M1 recorded `M1_SLICE FAIL` (slice MemoryMax 16106127360 below 5120M + current), and FLOOR now also runs on this box. Re-check the headroom and record it.
- [ ] fast-0 rule: one heavy replay at a time, at most 2 workers. No replay job runs while the host's catch-up runs (about 37 min, budget 50).
- [ ] `ps -ww -eo pid,args | grep '[r]4b_host_glue'` is empty (the wrapper's M5 refusal checks this too).
- [ ] Record the venv hash: `$HOME/mal-venvs/r4b/bin/python -m pip freeze | sha256sum`. **The venv is shared with FLOOR tools: no install into it until the last r4bt-a2 read.**

## 1. Trees and pins

| Use | Tree | Sha |
|---|---|---|
| Fresh dry host (Am.30 item 6) | new clean tree, e.g. `$HOME/mal-install/r4bt-a2-dry-Wp` (clone or `git worktree add --detach`; `git status --porcelain` empty) | W′ = `3c12c5fddfa67384dca2e306a8bf6c529e85ebfc` (#1330 merge; host closure byte-identical to W `17e3b05a`) |
| Decision host (Am.30 item 10) | new clean tree, e.g. `$HOME/mal-install/r4bt-a2-Wp` | W′ = `3c12c5fd…` (the registered `frozen_sha`; a W″ only on a clock slip, Am.30 item 9) |
| Pricer, read, confirm, A3 apply | clean checkout at W′ (the pricer refuses unless HEAD = the registry's `frozen_sha` and the tree is clean) | W′ |

Do not reuse `$HOME/mal-install/r4bt-W` (r4bt-a1's tree). Am.30 needs a fresh clean tree.

Verify in each tree before starting:
- `git rev-parse HEAD:tools/r4b_tip_reader.py` = `78ad185744147173d4e1036f35268012fcdcb066` and `sha256sum tools/r4b_tip_reader.py` = `8d0bf2d11ec6ac61e702937b2c55c88ec646c72f4a99a08c39ef1ad161d9537f`.
- `git rev-parse HEAD:scripts/research/r4b-dry-long.sh` = `8a0c7628a0bab648e19d3e2b0fdfb9b115368ad1`; `r4b-dry-sidecar.sh` `3659842e…`; `r4b-dry-stop.sh` `1cfe12c4…`.
- W′ tools (Am.30 item 4): `tools/r4bt_bots.py` `f109187aa8ae18d1cbafb06b6e1b7a2c2174c747`, `tools/r4bt_price_bot.py` `6347b0dfbce36dce24a7b5efe112a20f0f2a5a29`, `tools/r4bt_read.py` `679de3b5622dd7e2bf1deed8cccae079a188f241`, `tools/r4bt_read_confirm.py` `667446c40e5871242ccae90e9c929fc87411fc4e`, `tools/r4bt_a3.py` `9f1b6745ef7d668f2d8ab1e638e27d81b83cfc7b`; unchanged `tools/r4bt_gate.py` `2c094ad8…`, `tools/r4bt_price.py` `7ed62b1f…`, `tools/r4bt_attempts.py` `fd7fccca…`.
- Host closure blobs as Am.30 item 4: `r4b_live` `0ef1ce3c`, `r4b_host_glue` `99f46595`, `r4b_live_host` `6dedddeb`, `r4b_features` `4efe39ba`, `r4b_score` `385ffd36`, `r4b_book` `408b397c`, `r4b_frozen` `5ee8db83`, `r4b_layout` `4c2a4c24`, `r4b_feed_adapter` `1f0eda3e`.
- `sha256sum models/r4b/*` equal to EXP-027 §2.2 (Am.30 item 4 lists them).
- The follower's `/var/lib/mal/fast-tip-follower/status.json` shows `decoder_blobs_pinned` true, the three EXP027_DECODER_REF blobs (`238942a6`, `ea4e11ed`, `9a8bebb3`) and `trade_event_v` true. Read only this file and `checkpoint.json`, never the tip dir.

## 2. Jobs (all host-side MiScusi jobs on mal-fast-0, plain `sh`, no bash-isms)

This is how r4bt-a1 / dry-long-1101a ran: sidecar job #2018, then host job #2019 30 min later, both MiScusi jobs on fast-0 running the install tree's wrappers. The host job's reservation was 5,312 MiB, at or above the scope cap, with the M2 job-name text. dry-long-1101a ran at install `bc65c088`. r4bt-a1's planned decision host (`r4bt-a1-1107a`, tree `$HOME/mal-install/r4bt-W`, limit 20,160 min) never started. The same form applies here. No `systemd --user` unit, nohup or tmux.

| # | When (UTC) | Job | Form |
|---|---|---|---|
| J1 | dry start − ≥ 30 min, e.g. 2026-11-13T11:35Z | dry sidecar | `sh -c 'cd $HOME/mal-install/r4bt-a2-dry-Wp && R4B_RUN_END_EPOCH=1795046700 sh scripts/research/r4b-dry-sidecar.sh <dry change_id> <hours>'`; 256 MB; hours ≥ (run end − start) + 3; limit ≥ 60 × hours + 60 min (max 20,160) |
| J2 | an HH:05Z, at or after §0, no later than **2026-11-15T12:05Z**; planned 2026-11-13T12:05Z | dry host | `sh -c 'cd $HOME/mal-install/r4bt-a2-dry-Wp && sh scripts/research/r4b-dry-long.sh <dry change_id> <max_marks>'`, with `<max_marks>` = the number of hour marks from its first live mark through 2026-11-19T00:00Z inclusive (132 for a 2026-11-13T12:05Z start, first live mark 13:00Z), so it exits 0 by itself after the 11-18 day closes; reservation ≥ 5,312 MiB; **cpus ≥ 1.0** (MiScusi cpus is a hard cpu.max; a feed-type reader below 0.5 CPU throttles, as in the 11-04 lane3 lesson); limit 20,160 min. `<dry change_id>` = `r4bt-a2-dry-<MMDD><letter>`, e.g. `r4bt-a2-dry-1113a` |
| J3 | `after:` J2 (exit 0, about 2026-11-19T00:01Z) | dry stop check | `sh -c 'cd $HOME/mal-install/r4bt-a2-dry-Wp && sh scripts/research/r4b-dry-stop.sh <dry change_id>'` (M5 check: no `r4b_host_glue` left, PASS line). If J2 has not exited by 2026-11-19T00:30Z, follow the M5 order by hand: cancel the J2 MiScusi job, then run this script, then verify with `ps` (a cancel leaves `systemd-run --scope` children alive) |
| J4 | 2026-11-19T00:10Z (scheduled, submitted before 2026-11-16T00:00Z) | **P0 count** | `sh -c 'cd <clean checkout at W′> && nice -n 19 $HOME/mal-venvs/r4b/bin/python -I tools/r4bt_attempts.py p0 --declog-root $HOME/mal-data/r4b-dry/<dry change_id>/declog --arm r4b-exp027 --days 2026-11-16,2026-11-17,2026-11-18 --out <new file>'`; the counter's blob is `fd7fccca`, sha256 `3ff873a3…10cf3` |
| J5 | daily 00:10Z from the dry start | declog day sha | `tools/r4bt_attempts.py day-sha --declog-root …` (the §1.2 daily values the pricer verifies later); notebook each value |
| J6 | daily | A3 / structure monitor and r4bt decode-check chain | Amendment 27 item 12 (research-0 `tools/pump_structure_monitor.py` daily, then the r4bt classifier). The 11-17 A3 successors in docs/HANDOFF.md §2 must also cover r4bt-a2 |
| J7 | continuous, through the confirmation's last exit + 2 h | follower-state 60 s sampler, writer counts log, hour-health staging | hard-gap rule (ii) and (iii) and the feed bar need them over the P0 days. **#2055 and #2064 end about 2026-11-15T23:30Z to 11-16T00:15Z. Start their successors before 2026-11-16T00:00Z** (HANDOFF says "only if FLOOR-side reads them"; r4bt-a2 reads them) |
| J8 | 2026-11-15 06:41Z | tip writer/push hand-off check (`docs/runbooks/tip-writer-handoff.md`) | the price path; the successors #2503/#2504 end about 11-29T06Z, so plan the next hand-off |
| J9 | after the registration record; an HH:05Z leaving the catch-up done before the clock; planned **2026-11-19T21:05Z** | decision host + its sidecar (≥ 30 min earlier) | as J1/J2 at the W′ tree with `<change_id>` `r4bt-a2-<MMDD><letter>` and `--max-marks` (second wrapper arg) set to exit 0 before the 14-day limit; `after:` J3 (fires only on exit 0) |
| J10 | from the clock | count jobs (`r4bt_attempts.py count --clock-ms 1795132800000 --h-days <H> --hour-table …`), hourly or daily | boundary detection only; outcome-blind. The counter takes no `--bot-id` (unchanged pinned blob) |
| J11 | daily, from the registration | A3 apply for a2 | `tools/r4bt_a3.py apply --bot-id r4bt-a2 --verdict … --registry … --applied-log …` at W′ |
| J12 | after the screen boundary, last exit and every item 13 entry | screen pricer, then read | `$HOME/mal-venvs/r4b/bin/python -m tools.r4bt_price_bot --bot-id r4bt-a2 <r4bt_price args, incl. --h-days, --feed-delay-slots d, --k-rounding ceil>` from a clean checkout at W′ (never `tools/r4bt_price.py` directly: it would run as `r4bt-a1` and refuse); then `tools/r4bt_read.py --bot-id r4bt-a2 …` and, after a screen PASS, `tools/r4bt_read_confirm.py --bot-id r4bt-a2 …`. Both refuse if the registry `clock_start_ms` is not the table's 1795132800000. Use the `--bot-id r4bt-a2` form (two tokens): the wrapper does not parse `--bot-id=…` |

`R4B_RUN_END_EPOCH=1795046700` is 2026-11-19T00:05:00Z. The P0 days and every fixed instant above are host-side jobs scheduled or chained with `after`, never session crons alone. At session start, also run CronList and re-create crons from the crons file.

## 3. Day-1 dry check (counts only; memory "day-1 dry score")

On the first closed UTC day after the dry host is caught up (2026-11-14 if J2 runs 11-13), run as a manager job:
- `tools/r4bt_attempts.py day-sha` (file names, sha256, chain head, chain verified);
- `tools/r4bt_attempts.py count --declog-root … --clock-ms <00:00Z of that day> --h-days 14` (entry counts per UTC date only).

Pass shape:
- chain verified;
- root and arm exist (`decisions/r4b-exp027/`);
- day files present;
- `start` and `gap` rows only where expected;
- **entries per day well above 0** (a figure near the 2026-11-04..06 values of 1/0/0 means the universe is still empty: stop and ask QP before the P0 days).

The counts are a seen count. The fixed-first P0 text (written before 2026-11-16T00:00Z) states them ("A seen count is disclosed, not disqualifying"). Do not open declog rows, `out/`, `host.log` or the sidecar beyond names, counts and timings. Never print exit timing per day.

## 4. Fixed-first P0 text (notebook, before 2026-11-16T00:00:00Z)

The text names:
- the P0 days 2026-11-16, 2026-11-17 and 2026-11-18;
- the tool path@sha256;
- the root and the arm;
- the J4 output path;
- whether any per-day entry count of the dry host for those days was seen;
- the ruling §2.2 (ii) disclosure: jobs #2223 and #2225, their output sha256, and entries 2/7/1/1/0/0 for 11-01..11-06.

Floor: a mean of at least 2.0 a day and no day at 0. Hard-gap rule (i) to (iii) as Am.30 item 7.

## 5. Before the registration record (Am.30 item 13)

Tick (a) to (o). Most are records. The non-record ones:
- (c) W′ is done (`3c12c5fd`, #1330). Record the closure comparison by `git rev-parse` over the 88-file closure at W and W′;
- (f) the train/serve check is done (job #2531, train DISCLOSE, serve PASS descriptive). Record QP's choice between Am.30 alternatives (A) and (B) by a one-line erratum, or carry both;
- (g) the #1173 envelope tool on the dry sidecar: `$HOME/mal-venvs/r4b/bin/python -I scripts/research/r4b_c0_envelope.py --sidecar $HOME/mal-data/r4b-dry/<dry change_id>/sidecar.jsonl …`;
- (h) d from at least 24 h of the 60 s sampler;
- (i) the feed bar over the 24 closed hours before the registration hour.

Registration by 2026-11-19T23:59:59Z gives clock 2026-11-20T00:00:00Z (1795132800000).

## 6. Records owed at each step

- A LAB_STATE row and a MiScusi notebook entry at:
  - each start, stop and restart (dry host, decision host, sidecars);
  - the P0 result;
  - the registration;
  - any follower restart inside the window.
- `data/console.json` events. No outcome anywhere.
- Helius: the mark window uses about 42k credits a day under the 50,000 daily cap. Dry 11-13 to 11-19 plus decision host 11-19 to about 12-03 is about 20 days, about 0.85M credits. Record the actual figure in the daily note (CLAUDE.md "Credits").

## 7. Out of scope here

- Any live step:
  - a DEC-032 amendment (Amendment 9 and the C0 code are `r4bt-a1` only);
  - or a DEC-035 amendment (stake bound, open cap, budget);
  - the real-layout twin run;
  - the owner's word.
- The paper book models 0.25 SOL × 10 = 2.5 SOL peak open. No SOL is involved.
