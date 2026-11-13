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
| Pricer, read, confirm, A3 apply | clean checkout at W′ (the pricer refuses unless HEAD = the registry's `frozen_sha` and the tree is clean); the registered pricer's checkout is on mal-research-0 (section 8) | W′ |

Do not reuse `$HOME/mal-install/r4bt-W` (r4bt-a1's tree). Am.30 needs a fresh clean tree.

Verify in each tree before starting:
- `git rev-parse HEAD:tools/r4b_tip_reader.py` = `78ad185744147173d4e1036f35268012fcdcb066` and `sha256sum tools/r4b_tip_reader.py` = `8d0bf2d11ec6ac61e702937b2c55c88ec646c72f4a99a08c39ef1ad161d9537f`.
- `git rev-parse HEAD:scripts/research/r4b-dry-long.sh` = `8a0c7628a0bab648e19d3e2b0fdfb9b115368ad1`; `r4b-dry-sidecar.sh` `3659842e…`; `r4b-dry-stop.sh` `1cfe12c4…`.
- W′ tools (Am.30 item 4): `tools/r4bt_bots.py` `f109187aa8ae18d1cbafb06b6e1b7a2c2174c747`, `tools/r4bt_price_bot.py` `6347b0dfbce36dce24a7b5efe112a20f0f2a5a29`, `tools/r4bt_read.py` `679de3b5622dd7e2bf1deed8cccae079a188f241`, `tools/r4bt_read_confirm.py` `667446c40e5871242ccae90e9c929fc87411fc4e`, `tools/r4bt_a3.py` `9f1b6745ef7d668f2d8ab1e638e27d81b83cfc7b`; unchanged `tools/r4bt_gate.py` `2c094ad8…`, `tools/r4bt_price.py` `7ed62b1f…`, `tools/r4bt_attempts.py` `fd7fccca…`.
- Host closure blobs as Am.30 item 4: `r4b_live` `0ef1ce3c`, `r4b_host_glue` `99f46595`, `r4b_live_host` `6dedddeb`, `r4b_features` `4efe39ba`, `r4b_score` `385ffd36`, `r4b_book` `408b397c`, `r4b_frozen` `5ee8db83`, `r4b_layout` `4c2a4c24`, `r4b_feed_adapter` `1f0eda3e`.
- `sha256sum models/r4b/*` equal to EXP-027 §2.2 (Am.30 item 4 lists them).
- The follower's `/var/lib/mal/fast-tip-follower/status.json` shows `decoder_blobs_pinned` true, the three EXP027_DECODER_REF blobs (`238942a6`, `ea4e11ed`, `9a8bebb3`) and `trade_event_v` true. Read only this file and `checkpoint.json`, never the tip dir.

## 2. Jobs (all host-side MiScusi jobs on mal-fast-0, plain `sh`, no bash-isms)

Exception: J12, the registered pricer, runs on mal-research-0 (section 8; DEC-031 Amendment 30 Errata 2 and 3).

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
| J9 | after the registration record; an HH:05Z leaving the catch-up done before the clock; planned **2026-11-19T21:05Z** | decision host + its sidecar (≥ 30 min earlier) | as J1/J2 at the W′ tree with `<change_id>` `r4bt-a2-<MMDD><letter>` and `--max-marks` (second wrapper arg) set to exit 0 before the 14-day limit; **submitted only after the registration record**, as a scheduled job at its HH:05Z (planned 2026-11-19T21:05Z), with J3's exit 0 checked first. Never `after: J3` alone: J3 exits about 11-19T00:0xZ, about 21 h before the planned start and before the registration record that item 10 requires |
| J10 | from the clock | count jobs (`r4bt_attempts.py count --clock-ms 1795132800000 --h-days <H> --hour-table …`), hourly or daily | boundary detection only; outcome-blind. The counter takes no `--bot-id` (unchanged pinned blob) |
| J11 | daily, from the registration | A3 apply for a2 | `tools/r4bt_a3.py apply --bot-id r4bt-a2 --verdict … --registry … --applied-log …` at W′ |
| J12 | after the screen boundary, last exit and every item 13 entry | screen pricer, then read | `$HOME/mal-venvs/r4b/bin/python -m tools.r4bt_price_bot --bot-id r4bt-a2 <r4bt_price args, incl. --h-days, --feed-delay-slots d, --k-rounding ceil>` from a clean checkout at W′ (never `tools/r4bt_price.py` directly: it would run as `r4bt-a1` and refuse); then `tools/r4bt_read.py --bot-id r4bt-a2 …` and, after a screen PASS, `tools/r4bt_read_confirm.py --bot-id r4bt-a2 …`. Both refuse if the registry `clock_start_ms` is not the table's 1795132800000. Use the `--bot-id r4bt-a2` form (two tokens): the wrapper does not parse `--bot-id=…` **Paths and host: see §8** (DEC-031 Amendment 30 Errata 2 and 3): `--tape-dir /data2/mal/scratch/r4b-price-keep`, `--declog-root /data2/mal/r4b-preview/declog-copy/<change_id>/`, run on mal-research-0, after the `zst_sha` pre-check and the read-time copier pass. |

`R4B_RUN_END_EPOCH=1795046700` is 2026-11-19T00:05:00Z. The P0 days and every fixed instant above are host-side jobs scheduled or chained with `after`, never session crons alone. At session start, also run CronList and re-create crons from the crons file.

## 3. Day-1 dry check (names and structural counts only; no entry count)

On the first closed UTC day after the dry host is caught up (2026-11-14 if J2 runs 11-13), run as a manager job only:
- `tools/r4bt_attempts.py day-sha --declog-root $HOME/mal-data/r4b-dry/<dry change_id>/declog --arm r4b-exp027 --out <new file>`. It prints names and hashes only: the arm, the as-of instant and the sha256 of each closed day file.

Pass shape (structure only):
- root and arm exist (`decisions/r4b-exp027/`);
- a closed day file exists for that day and has a sha256;
- the host job is running (job list state, not the status view), and the sidecar shows the scope and the catch-up end (names, counts and timings).

**Do not run `r4bt_attempts.py count` or `p0` on the dry host before the J4 instant, and do not look at entry counts in any other way.** No entry count of an r4bt-a2 host is printed, read or cited before 2026-11-19T00:10Z.

**The preview's output is the one path where this ban gives way (DEC-031 Amendment 31 item 4).** The ban on printing, reading or citing an r4bt-a2 entry count before 2026-11-19T00:10Z gives way for the r4-b preview's output only. It stands for every other path, including the copier's status file and any listing of declog or declog-copy file sizes (day names and byte lengths are entry-count proxies; Amendment 31 Erratum 2 item 3).

**Am.30 item 7 (verbatim):** "A stop, non-start or non-run of the item 6 host or of the P0 count job, decided after any per-day entry count of an r4bt-a2 host was seen, is scored as the P0 miss kill line (item 14), not as an operational NOT_DECIDABLE. Only a cause recorded from names, timings or job state, with no entry count cited, is operational."

Do not open declog rows, `out/`, `host.log` or the sidecar beyond names, counts and timings.

## 4. Fixed-first P0 text (notebook, before 2026-11-16T00:00:00Z)

The text names:
- the P0 days 2026-11-16, 2026-11-17 and 2026-11-18;
- the tool path@sha256;
- the root and the arm;
- the J4 output path;
- whether any per-day entry count of the dry host for those days was seen (none should be: §3 prints no entry count);
- the ruling §2.2 (ii) disclosure: jobs #2223 and #2225, their output sha256, and entries 2/7/1/1/0/0 for 11-01..11-06.

Floor: a mean of at least 2.0 a day and no day at 0. Hard-gap rule (i) to (iii) as Am.30 item 7.

## 5. Before the registration record (Am.30 item 13)

Tick (a) to (o). Most are records. The non-record ones:
- (c) W′ is done (`3c12c5fd`, #1330). Record the closure comparison by `git rev-parse` over the 88-file closure at W and W′;
- (f) the train/serve check is done (job #2531, train DISCLOSE, serve PASS descriptive). QP ruled (A), qualified, in the Am.30 review, and Am.30 item 2 (4) carries the sentence. Add no guard or count for it. Every read prints the writer counts log's per-day pool_create/complete over the P0 days and the counted dates, labelled descriptive;
- (g) the #1173 envelope tool on the dry sidecar: `$HOME/mal-venvs/r4b/bin/python -I scripts/research/r4b_c0_envelope.py --sidecar $HOME/mal-data/r4b-dry/<dry change_id>/sidecar.jsonl --origin-epoch 1795125300 --run-end-epoch 1796346060 --host-job-start-epoch 1795122300 --cap-mib 5120`.
  - `--origin-epoch` is when the decision host's state holds 52 h of history, which is its catch-up end. Planned: J9 start 2026-11-19T21:05Z (1795122300) + the 50 min catch-up budget = 2026-11-19T21:55:00Z = 1795125300. Use the real `catch_up_done` instant if J9 has run.
  - `--run-end-epoch` is the last instant the host must still run: the confirmation read's worst case, boundary 2026-12-02T00:00Z + T_max 176,460 s = 2026-12-04T01:01:00Z = 1796346060.
  - `--host-job-start-epoch` is the J9 start, 1795122300.
  - Expected: the 14-day lifetime check FAILS by about 4 h, since 1795122300 + 1,209,600 = 1796331900 = 2026-12-03T21:05Z. A FAIL of the 14-day check is recorded, not repaired: per Am.30 item 10 no successor feeds r4bt-a2, and a confirmation that needs an exit after the host's end is an operational NOT_DECIDABLE.
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

## 8. Preview (DEC-031 Amendment 31, Errata 1 and 2) and the registered pricer's paths (Amendment 30 Errata 2 and 3)

Sources: DEC-031 Amendment 31 Erratum 1 item 2 and Erratum 2, Amendment 30 Errata 2 and 3, and the header comments of `scripts/ops/r4b_price_keeper.sh` and `scripts/ops/r4b_declog_copy.sh`. No command below is invented: where a job form is not in those headers, it is not given here.

**Three manager MiScusi jobs, each from a clean detached tree at its pinned commit. Agents never run them.**

| Job | Host | When | Form |
|---|---|---|---|
| r4-b price keeper | mal-research-0 | links closed hv7 hours from 2026-11-15T22; start by 2026-11-16T12Z | `scripts/ops/r4b_price_keeper.sh` (see its header comment; suggested job in it: mem 64 MB, 0.1 CPU, no time limit). Exit 5 (a missing hour, or the receiver silent for over 72 h) ends the job after a full pass; the manager restarts it after fixing the cause. The missing-hour alert runs from its start (Amendment 30 Erratum 2). |
| declog copier | mal-fast-0 | before the preview's first run | `scripts/ops/r4b_declog_copy.sh` with `--series <change_id>=<declog dir>` flags (see its header comment; suggested job in it: mem 128 MB, 0.2 CPU, no time limit). **Exactly ONE copier job at a time: it takes no lock.** An r4bt-a3 series needs `--quiet-until <its own P0 count instant, ISO Z>`. |
| r4-b preview pricer | mal-research-0 | daily at 03:20Z with `as_of` = that day 00:00Z; first run 2026-11-17 | see `scripts/research/r4b-preview.sh` once PR #1370 merges. PR #1370 merged as `8d19d9711cbadfc7bd48953ecfe5fb713ef75b42`. Command, quoted from the script's header at that commit: `sh scripts/research/r4b-preview.sh <manifest> <manifest_sha256> <as_of ISO on the hour, e.g. 2026-11-17T00:00:00Z>`. The header says: run by the manager as a MiScusi job on mal-research-0, from a clean detached tree at the manifest's pin_commit; one run a day at about 03:20Z with as_of = that day 00:00Z; as_of must be at least about 3 h before now (the tool refuses until the tape hour of as_of + 2 h is closed). |

The schedule (03:20Z, `as_of` = that day 00:00Z) is not enforced in code. An `as_of` whose run would finish before 2026-11-19T00:10Z is fine under Amendment 31 item 4, because the preview's output is the one path where the entry-count ban gives way (section 3).

**Owed before the preview's first run (all):**
- the smoke judgement of the Amendment 30 item 6 host (Amendment 30 item 13 (e)), written and notebooked and not revised afterwards;
- d, measured once over [2026-11-15T00:00Z, 2026-11-16T00:00Z) of the 60 s sampler with the pinned d tool, and notebooked (Amendment 31 Erratum 1 item 3); the preview and r4bt-a2's registration use that one value;
- the second P0 notebook entry, before 2026-11-16T00:00:00Z (section 4 and Amendment 31 item 4);
- the declaration notebook entry, "r4-b preview declared <instant> (Amendment 31)" (Amendment 31 item 3);
- the copier and preview pins recorded (the keeper's pin is #1367 `50469181a6ce4820c8890d0157bf2809fa50e7d3`);
- the SEALS-BRIEF lines (Amendment 31 Erratum 1 item 6, Erratum 2 item 4);
- PR #1370 (the preview pricer) merged with QP OK and a reviewer verdict (Amendment 31 item 2);
- the preview pricer's commit and blob recorded by a later Amendment 23 readers record (add, never rewrite);
- Amendment 23 readers record 3 merged (before the keeper's first link);
- done: the HOLDOUT_LEDGER line (#1368, merged 2026-11-13T23:05:33Z).

**Owed before the copier's first run:** the Am.30 item 12 readers addition (Amendment 30 Erratum 3 item 3) and the R4-B DECLOG COPY SEALS line.

**The registered pricer's paths (J12; Amendment 30 Errata 2 and 3).** These replace J12's price path and declog root; J12 is otherwise unchanged.
- `--tape-dir` is the keeper dir `/data2/mal/scratch/r4b-price-keep`.
- `--declog-root` is the declog copy `/data2/mal/r4b-preview/declog-copy/<change_id>/`. Both are on mal-research-0, so the registered pricer runs there. The registration record pins both paths.
- Before pricing, the `zst_sha` pre-check of Amendment 30 Erratum 3 item 1 runs as its own pinned step. `tools/r4bt_price.py` is not edited. A failed check means the pricer is not run.
- Read-time copier procedure (Amendment 30 Erratum 3 item 2): after the host stops or the last needed day has closed, one copier pass with `--loop 0`; every needed day `unchanged` with copy length and sha equal to the original's newline-cut prefix; only then the `r4bt_attempts day-sha` list from the originals on fast-0. Every copied day file must equal that list, or the pricer refuses.

**Pins (DEC-031 Amendment 31 Erratum 3 item 1).**
- Preview pricer: `tools/r4b_preview.py` at `8d19d9711cbadfc7bd48953ecfe5fb713ef75b42`, blob `8cbe33d3c6472669d4003a6713989902c0807c47`; `tools/test_r4b_preview.py` blob `9c0068adc22813cb572ae16865d573f3910eb668`; `scripts/research/r4b-preview.sh` blob `188f1caec849a1d7f874ae03a5f82c0ab3346601`. Output `/data2/mal/r4b-preview/out/`, work dir `/data2/mal/r4b-preview/work/`.
- Declog copier: `scripts/ops/r4b_declog_copy.py` blob `7a468ef5f48623c6ed7c2c806c72a5b423714829`, `.sh` blob `36b359c41455a37b1b0e7f111ce349f1185959dc`, at `04e9716b6c0c6475b2a1e43855b30297073c15a9`.
- Price keeper: `scripts/ops/r4b_price_keeper.py` blob `c2f3f1c279e4e5323847528fa5bfe565ca94153c`, `.sh` blob `c3ce833b05e62a6e4a053761a69450d15d5379db`, at `50469181a6ce4820c8890d0157bf2809fa50e7d3` (manager job #2636).
- The copier job is #2642 at `04e9716b6c0c6475b2a1e43855b30297073c15a9`. Exactly one copier runs (it takes no lock).
- Copier exit 3 is a perm-fail (a copied file or its dir has the wrong mode or an ACL entry): the job stops and the manager records a seal incident before any restart. The keeper's exit 3 is a different thing (log unreadable); do not mix them.
