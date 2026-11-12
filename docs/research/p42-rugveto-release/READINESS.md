# P42: release readiness of the funding-graph rug veto (library rows lib-c-G3, lib-c-H1)

Written 2026-10-26 (`date -u` read 2026-10-26T17:35:33Z) by a manager17 worker on mal-research-0. Desk only.

No book has cleared the promotion gate. The rug veto is a veto, never a book, and nothing in this file is gate evidence.

**What I read:**
- the DECLARE and FREEZE files of the four rug-veto leaves, plus `RELEASE*.json.template`, `run_oct.sh` and the loader/guard source (code only);
- the 10-10 QP rulings in `/data/mal/hunt-1008/qp-rulings-1010/` (records);
- DEC-016, EXP-025, `docs/HOLDOUT_LEDGER.md`, `docs/HANDOFF.md` and the open handoff PR #942;
- the P32 audit (PR #937), HV6 `DESIGN.md`, LAB_STATE, and `ARTIFACTS/lab/funding-graph.md`.

**What I did not open:**
- the funding-graph store, or any funding row;
- any tape or forward-1002 file;
- `FINAL_READS.jsonl`: stat only, 434 bytes;
- the contents of `/data/mal/exp025/look*`: existence only;
- any REPORT, `out/`, `work/` or `logs/` file of the leaves;
- any HV4, Q50, DBC, meta-capture or arena output.

0 Helius credits.

## 0. Which frozen files are current (the library rows are stale)

The library rows cite r7 and r8 only. Two later amendments, both dated 2026-10-10, supersede them:

| Leaf | Role now | Pin |
|---|---|---|
| `hunt-r7/r7-funding-graph-rug-veto` | Original stage-1 DECLARE (K1–K4, splits, release items s9). Superseded for running. | DECLARE `cab30d39…dad365` |
| `hunt-r8/r8-rugveto-release` | Stage-1 amendment (f_insider_hold, K3 at 1.9 s WORST on both legs) and stage-2 DECLARE-S2. Superseded for running. | DECLARE `6f94658c…c7389`, DECLARE-S2 `d71535b8…a13f` |
| `hunt-r9/r9-freeze-amendments/rugveto` | **Stage 1 runs only here.** Adds the layout and convention dry step, muted counts, and a FINAL marker pinned by line sha256. | FREEZE `170cb983 3c1d7850…` (r9 FREEZE.md:5-8, s5) |
| `hunt-r10/r10-s2-seal-fix/rugveto` | **Stage 2 runs only here.** Option (b) means no October row file is ever written. Its stage-1 `run_oct.sh` is a stub that refuses. | FREEZE `2b6b5858…3b08` (r10 FREEZE.md:6-7, s8) |

The kill rules are unchanged from r7 (r7 DECLARE.md:89-97; r8 FREEZE.md:92). Stage 1 also decides K3 at 1.9 s WORST on the flat **and** the pressure leg (r8 FREEZE.md:42-47).
- K4: coverage below 0.20 is NOT_SCREENABLE.
- K1: AUC(S1) below 0.65 kills.
- K2: AUC(S1) − AUC(S0) below +0.02 kills.
- K3: vetoed rows that do not lose more than kept rows kill.

## 1. Preconditions: met, unmet or impossible

### Stage 1 (D1, pre-graduation, r9 leaf)

| # | Item | Status | Evidence (file:line) |
|---|---|---|---|
| 1 | DEC-016 FINAL (A) marker on main | **MET.** It is labelled COMPROMISED, but that does not block. | DEC-016:414 "FINAL (A) written", job #1038, `VERDICT: FAIL`, `FINAL_READS.jsonl` has 1 line. DEC-016:415 "Reported COMPROMISED (Amendment 2)". DEC-016:95 is the Amendment 2 exposure. Why it does not block: ledger:33 makes the DEC-016 end state "FINAL (A) written", and ledger:39 says "A breach does not end a seal. A compromised read still runs". The F1 text ties effect only to the marker existing (ledger:99). r9's `release_ok` pins the marker by path and line sha256 (DECLARE-R9.md:38-42). The manager computes that sha; I did not open the file. Because (A) failed, `dec016_final_a = fail` and no (B) record is needed. |
| 2 | Owner's F1 ledger edit (D0 and D1 on forward-1002) | **MET**, with one gap (item 2b) | ledger:99-106 ("domains D0 and D1 … are released for exploration", effective at the marker and not before 2026-10-16T02:00Z), merged by #652 `3d97a70c`. The Forward walk row cell reverses "Never exploration" for D0/D1 (ledger:64), as FUNDING-D0 §3.2 requires. The DEC-021 §1 carve-out is DEC-021:157 (Am.5). The catalog roles `oct-structure-d0` and `oct-pregrad-d1` are merged (#655 `b9169d1e`; LAB_STATE:36). |
| 2b | `owner_ok_ref` names the funding snapshot as a D0 input | **UNMET** | FUNDING-D0.md:70 says "`owner_ok_ref` should name the funding snapshot as a D0 input." The owner OK quoted at ledger:99 (10-12, n_OLnV5a4MFslEHw) covers forward-1002 D0/D1 and does not name the Oracle funding rows. Draft text is in §3.1. |
| 3 | QP ruling naming the 10 funding columns | **MET** (the library row is stale) | `/data/mal/hunt-1008/qp-rulings-1010/FUNDING-D0.md`, sha256 `1f40ee29d654eefb0cf9ab5ce70025b0f1b1147ae110aa8e53e6f246dcb9203f`. Verdict "YES-WITH-CONDITIONS" (:3). The ten columns (:11-24) are wallet, funder, amount_lamports, wallet_first_tx_ms, funded_at_ms, exchange, history_capped, status, first_seen_ms and role_hint. Window [09-25T16:30Z, 10-16T01:00Z) (:30). Use is limited to the r9 reads (:32-34). The file name and text pass `check_qp_funding_ruling`'s name rules (r8 fgv_guard.py:60-61). It was written after r9's freeze, so it is not on the deny list. r10 FREEZE.md:55-56 states that it passes. |
| 4 | Read-only snapshot with a sha256 manifest, plus the enricher's restart and credit-cap times | **UNMET** (nobody has made it) | r7 FREEZE.md:73-76; FUNDING-D0.md:66. Copy `funding-2026-09-25.jsonl` … `funding-2026-10-16.jsonl` from mal-core-0 `/var/lib/mal/graph/`, read-only, by host-ops. Only paths, hashes, line counts and the event times may be printed. Whether those files are still complete on Oracle is unknown from the desk. |
| 5 | One MiScusi job | **UNMET** (not run) | r9 `scripts/run_oct.sh` header: role exploration, mem_gb 9, cpus 2. r9 `out/` holds explore files only, so no October read has happened. No `RELEASE.json` exists in r7, r8, r9 or r10 (`find` on hunt-1008). |
| 6 | **Not in the library row:** the frozen loader must survive the real forward-1002 layout | **Likely UNMET.** An amendment is needed. | Job #1061 (the oct_d1_layer precount) refused at `creates-2026-10-12T04.jsonl.zst: line 308 is not JSON` (ARTIFACTS/lab/push-2026-10-16-round2.md:69). The fix for that layer was #667, a counted-skip rule. The frozen r9 loader passes creates files whole (`GREP['creates'] = None`, r9 fgv_load.py:42) to DuckDB `read_json` without `ignore_errors` (:84-85). Hour 10-12T04 is inside the early split [10-09T15, 10-13). I expect the job to fail closed at the dry step, before any label, so no outcome would be spent. It still cannot finish as frozen. It needs a dated, outcome-blind r11 amendment (a counted skip with a declared refusal limit) and QP OK. |
| 7 | **Not in the library row:** "reads only through the roles" | **OPEN (QP question)** | ledger:102 and LAB_STATE:36 say October D0/D1 reads run "only as MiScusi jobs through the roles". The r9 leaf enforces its own D1 masks (`fgv_guard`, its own column allowlist and deny list). It does not call `tools/mal_catalog_october.check_read`. QP should rule whether `fgv_guard` counts as the role or whether the runner must call `check_read`. If it must call `check_read`, that is part of r11. |
| 8 | K4 coverage (computed in-job, outcome-blind) | **RISK** (cannot be checked from the desk) | The 2M Helius cap at about 4,370 credits/h would be reached around 10-14/15. That is inferred (r7 FREEZE.md:74-76), and the late split is [10-13, 10-16). The 09-27 health report shows 51,206 of 53,575 log lines as RPC error −32015, with resolved 8,994 against dropped_stale 23,315 (ARTIFACTS/lab/mal-report-2026-09-27.md:64). October coverage is unknown. Below 0.20 the read ends NOT_SCREENABLE, which spends nothing else. |

### Stage 2 (D2, post-graduation, r10 leaf)

| # | Item | Status | Evidence |
|---|---|---|---|
| S2-1 | Every stage-1 release item | Same as above | DECLARE-S2.md:98-100. r10 needs a byte-identical copy of r9's `RELEASE.json` (r10 FREEZE.md:102-103). |
| S2-2 | EXP-025 end state on main | **UNMET today; expected soon** | ledger:31 lists the end states (PASS at Look 1; Look 2 run; Look 2 refused). EXP-025:1192 and :1221 say that after the R8 run of Look 2 (job #1287, 10-26T12:05Z), "Look 2 refused" is recorded on main. It is not on origin/main at `6a72511c`. |
| S2-3 | DEC-016 (B) and Am.3(a) | **NOT NEEDED** | Required only if (A) passed (DECLARE-S2.md:101). (A) is FAIL (DEC-016:414). |
| S2-4 | Owner's D2 ledger edit | **UNMET** | DECLARE-S2.md:101-102. D2 is "Not released" (ledger:104). |
| S2-5 | QP OK on DECLARE-S2 + FREEZE | **MET** | `qp-rulings-1010/RUGVETO-S2-QP-R10.md` (sha256 `77da224d…26a2`), line 2 `QP-S2-VERDICT: OK`, citing r10 FREEZE `2b6b5858…`. |
| S2-6 | Pool source = EXP-025's look-directory tape (`<look>/hunt-shared/tokens.parquet` + `tape/trades/<hour>.parquet` for 10-09T00..10-16T00). "No other October pool source and no new adapter: if that tape is missing, stage 2 is NOT_RUNNABLE (no fallback)." | **IMPOSSIBLE as frozen** | DECLARE-S2.md:106-108 and r10 FREEZE.md:106. Both EXP-025 looks ended NOT_DECIDABLE (R8, P5) without building a tape (EXP-025:1192). `/data/mal/exp025` was archived and deleted on 10-18 with only `e0/` content, and the R8 path creates only the look directory and `LOOK_READS.jsonl` (EXP-025:1217). forward-1002ev, the event-V source the adapter needed, was deleted on 10-18 (`/data/mal/blocks/ARCHIVED-forward-1002ev.README`; a tar is in archive). |
| S2-7 | D3 (EXP-024 Look-2 seal) | **Holds to its end state** (first 2026-11-14T00:00Z) | SEALS-BRIEF and HANDOFF §2. Stage-2 masks M1/M2 were built for this seal, but a revival is a new pre-registration in any case (S2-6). |

**Bottom line:**
- Stage 1 has three of its five listed items met (marker, F1 edit, QP funding ruling). It is missing the owner line (2b), the snapshot (4) and the job (5). Two blockers the library row does not list are the malformed-line amendment (6) and the role question (7).
- Stage 2 is NOT_RUNNABLE as frozen. Its pinned pool source was never built, and the data it would have been built from was deleted.

## 2. Which book could the veto apply to today?

The model is defined on a fixed population:
- Stage 1: pump.fun bonding-curve mints, decision 60–900 s after create, progress ≥ 0.5, hold 300 s (r7 DECLARE.md:38-48).
- Stage 2: V-range PumpSwap pools, decision 600–3,600 s after s0, hold 300 s (DECLARE-S2.md:51-61).

The funding rows exist only for wallets the Oracle enricher enrolled from pump.fun creates and curve buys (FUNDING-D0.md:26).

| Book | Venue and stage | Population match | Can the veto apply? |
|---|---|---|---|
| **FLOOR-SLOW** (future; "usable after HV4 END 11-08", HANDOFF #942 :49) | PumpSwap, post-graduation; old low-cap tokens after a 6 h drawdown, 24 h hold (P32 AUDIT.md lane 1) | No. It is outside stage 1 (not pre-graduation) and outside the stage-2 grid (T ≤ 3,600 s, H = 300 s). | Not as frozen. The funding features (creator funder, fan-out, early-buyer links) are definable for its mints if the enricher saw the create. A model on FLOOR's population would be a new leaf. |
| **Q50** (frozen a5f41370, clock 10-25, end-seal 11-06) | PumpSwap, H5 pools in their first 360 s | No. That window is exactly what M1 masks, and it is D3 (sealed to 11-14). | **No.** It is frozen mid-clock, and any overlay is a new book. |
| **DBC m10 / P18** | Meteora DBC, a different venue | No | **The funding graph is not defined there.** The enricher enrols only pump.fun creators and first curve buyers (FUNDING-D0.md:26). DBC mints get rows only if the same wallet also touched pump.fun. S0 is pump-curve features. It would need a new enricher feed and a new model. |
| **HV6** (registration target clock 10-30T00Z) | PumpSwap, HV4 lane G: pool age 2 h to 7 d (hv6 DESIGN.md:42-43) | No. It is outside the stage-2 grid. | **No.** Once registered it is frozen, and its universe is "unmodified" by test (DESIGN.md:45). |
| HV4 (frozen, END 11-08) | PumpSwap lane G | No | No |

**No current or planned book uses the stage-1 population.** The bonding-curve domain was shelved at MAL latency on 10-16 (push-2026-10-16-round2.md:5, :28), and P27's bonding-curve entries were killed (HANDOFF #942 :40).

**Earliest entry point.** A new registration, pre-registered as a **report-only veto shadow column**: vetoed-vs-kept counts and P&L that never change an entry. The earliest candidate book is FLOOR-SLOW, at the earliest HV4 END (11-08) plus the revival terms (new amendment, clock ≥ merge + 75 h), so about 11-11. Even then, three things are needed first:
- (a) a veto model trained on that population, which is a new leaf with its own DECLARE/FREEZE, and nothing frozen covers it;
- (b) a live funding feed at decision time: the Oracle enricher shipped to research-0. This is the DEC-015 "ship from Oracle" option (ARTIFACTS/lab/dec015-runner-inputs-2026-10-01.md:124), not built for any current book;
- (c) a QP ruling for funding rows first seen ≥ 2026-10-16T01:00Z, which FUNDING-D0 does not cover (FUNDING-D0.md:94).

A deciding (entry-changing) veto would first need a stage-1 or stage-2 VETO_CANDIDATE plus a paired confirmation on unread hours (r7 DECLARE.md:98-99; r10 FREEZE.md:44-45). Neither path exists today.

## 3. Minimal path to a decisive stage-1 read (if the manager chooses GO)

### 3.1 Owner text (draft; to be appended as a dated ledger changelog line under the Forward walk row, and pinned as `owner_ok_ref`)

> - 2026-10-2X (W = `<date -u>`; written outcome-blind; no funding row, hour, forward, walk, runner, canary or shadow file was opened; inputs: the owner's line below, `/data/mal/hunt-1008/qp-rulings-1010/FUNDING-D0.md` sha256 `1f40ee29d654eefb0cf9ab5ce70025b0f1b1147ae110aa8e53e6f246dcb9203f`, and the F1 entry of 2026-10-15): **Owner's OK (`<notebook id>`, owner's words: "`<verbatim>`"): the Oracle funding-graph snapshot — the files `funding-2026-09-25.jsonl` through `funding-2026-10-16.jsonl` copied read-only from mal-core-0 `/var/lib/mal/graph/`, rows with `first_seen_ms` before 2026-10-16T01:00:00Z only, listed by sha256 in a manifest — is a D0 input to the F1 release of the Forward walk row, for the r9 rug-veto stage-1 read (`hunt-r9/r9-freeze-amendments/rugveto/scripts/run_oct.sh`) only, under FUNDING-D0's conditions 1–7.** Rows first seen before 2026-10-02T10:00Z are identity history only. Nothing else is released: not D2, D3 or D4, not walk 2, not any funding row first seen at or after 2026-10-16T01:00Z, and not any other enricher output. Released hours never confirm; a survivor is a veto candidate only and never changes a book.

The owner's answer should be one line, for example: "Yes, the Oracle funding snapshot (09-25..10-16) may be used as D0 input for the rug-veto stage-1 research read."

`RELEASE.json` then quotes the existing F1 sentence as `ledger_release_quote`, verbatim from ledger:99: `domains D0 and D1 of the Forward walk row` … `are released for exploration`. Its `ledger_commit` is the merge that carries the new owner line.

### 3.2 Questions for QP (before any release file is written)

1. **r11 malformed-line amendment.** Is a counted-skip rule for unparseable walker lines an outcome-blind amendment of the frozen r9 stage 1? It would mirror #667's rule for `oct_d1_layer`: a skip count printed, and refusal above a declared limit fixed before any October byte. What limit? Must the build re-run the explore-0814 equality before re-freeze?
2. **Roles.** Does the r9 leaf's own `fgv_guard` (D1 column allowlist, path deny list, hour window) satisfy ledger:102 and LAB_STATE:36 ("reads only … through the roles")? Or must the runner call `tools.mal_catalog_october.check_read` with role `oct-pregrad-d1`, and must the MiScusi job role be `oct-pregrad-d1` rather than the frozen "exploration"?
3. **Day-16 file.** `funding-2026-10-16.jsonl` holds rows first seen after 10-16T01:00Z, which FUNDING-D0 does not cover. The loader parses them and then drops them (`funding_max_ms`). Is parse-then-drop inside the job acceptable, or must host-ops truncate that file before copying, and how without reading row content?
4. **Owner text.** Does the §3.1 owner line satisfy FUNDING-D0 §3.2 (`owner_ok_ref` names the snapshot)?
5. **Disclosure of other D1 use.** Since 2026-10-16, other exploration has read forward-1002 D1 hours, including the late split [10-13, 10-16): the `oct_d1_layer` precount and the bonding-curve push work. None of it fed this leaf, which was frozen on 10-10. What disclosure line must the read's report carry?
6. **Coverage times.** Confirm that the enricher's restart and credit-cap times (counts and times only) may be printed before the read. Confirm that a K4 failure is NOT_SCREENABLE as frozen and spends nothing further.
7. **Crash rule.** Confirm that r10's B2 text binds stage 1 procedurally: after a crash of `fgv_build oct_d1`, nobody opens `work/oct_rows_*` (r10 FREEZE.md:37-41).
8. **Stage 2.** Confirm stage 2 is NOT_RUNNABLE under DECLARE-S2.md:106-108, and that any revival on another pool source is a new pre-registration, not an amendment.

### 3.3 Job spec (after r11 freezes and every release item verifies)

- `RELEASE.json` is written by the manager from `hunt-r9/r9-freeze-amendments/rugveto/RELEASE.json.template` (or the r11 leaf's copy), into that leaf only. Fields:
  - `final_marker_path` = `/data/mal/exp012-forward/FINAL_READS.jsonl`;
  - `final_marker_line_sha256`;
  - `ledger_commit` (an ancestor of origin/main; fetch first);
  - `ledger_release_quote`;
  - `qp_funding_d0_ruling_path` = `/data/mal/hunt-1008/qp-rulings-1010/FUNDING-D0.md` and its sha256 `1f40ee29…203f`;
  - `funding_manifest_path`;
  - `owner_ok_ref`.
- Pre-check: no `RELEASE*.json` or October output in the r7 or r8 leaves (`check_superseded`). True today.
- Snapshot (host-ops): `/data/mal/hunt-1008/hunt-r9/funding-snapshot-<date>/funding-2026-09-25.jsonl … funding-2026-10-16.jsonl`, `chmod a-w`, plus a manifest `<sha256>  <abs path>` and an event-times file. Print paths, hashes and line counts only.
- MiScusi `miscusi_job_submit`:
  - session `s_y1y4vFh5TCjy8A`, host mal-research-0;
  - command `sh /data/mal/hunt-1008/hunt-r9/r9-freeze-amendments/rugveto/scripts/run_oct.sh` (or the r11 path);
  - role `exploration` (or `oct-pregrad-d1` per QP Q2), mem_gb 9 (r9 FREEZE §4), cpus 2;
  - not resumable; time limit 6 h.
- Steps (frozen): pins → release gate → 43 + 9 tests → count-only dry (hours, bonding rows, funding rows, POST-trade convention) → rows → screen once. A second read is refused.
- Check `systemctl show user-1002.slice -p MemoryCurrent` (or the research-0 equivalent) first. Do not run it alongside the HV6 registration jobs (16 GB replay) or the 11-01 DBC read (~10 GiB).
- **Run time** is unmeasured for October. The explore-0814 build took 1,035 s on 15 August dates (r9 FREEZE.md:56). October from 10-02T10 is about 14 days at 200 ms slots, so expect more than 1 h.

### 3.4 Date

- r11 amendment (builder about half a day) → QP and reviewer → merge: 10-27 to 10-28.
- Owner line: async; one answer.
- Snapshot: about 1 h of host-ops once Oracle access is confirmed.
- **Earliest run: 2026-10-28.** Realistic: 10-28/29, before the 10-30T00Z HV6 clock takes memory and attention. If Q2 forces a runner change through `check_read`, add about a day.
- Stage 2: no date (NOT_RUNNABLE).

## 4. Honest value

Base rates come from the frozen files only; no new data was used.
- Stage 1 strict label at H = 300 s: **2,079 / 20,819 = 9.99 %** on explore-0814 (r7 FREEZE.md:58).
- grad_in_hold: 7.29 %. Guard-fail: 3.94 %.
- Stage 2: **611 / 9,092 = 6.72 %** (r8 FREEZE-S2.md:58).
- The frozen veto removes the top 20 % of rows by S1 hazard (80th-percentile threshold; r7 DECLARE.md:95).

Under a binormal model (my arithmetic, `scipy`; an approximation, not a measurement), a 20 % veto at a given AUC catches this share of strict dumps:

| AUC(S1) | Dumps caught (recall), stage 1 p = 9.99 % | Share of vetoed rows that are not dumps | Dumps caught, stage 2 p = 6.72 % |
|---|---|---|---|
| 0.60 | 30.0 % | 85.0 % | 30.5 % |
| **0.65** | **35.9 %** | **82.1 %** | **36.7 %** |
| **0.70** | **42.4 %** | **78.8 %** | **43.6 %** |
| 0.75 | 49.5 % | 75.3 % | 51.2 % |

What this means:
1. **At most about 36–42 % of strict-dump losses** are removed at AUC 0.65–0.70. That is an upper bound on dump loss avoided. The cost is skipping about 18 % of the non-dump rows (FPR 0.182 / 0.175), whose P&L is forfeited. The net effect is recall × p × (dump loss) − FPR × (1 − p) × (non-dump mean). Its sign is exactly what K3 tests, and I cannot compute it without P&L.
2. **The prior on that sign is poor.**
   - r3-dump-hazard's veto was killed by K3's predecessor: vetoed rows **+36.5627 %** vs kept **+6.3818 %** flat. High-hazard rows were also the high-upside rows (r7 DECLARE.md:11-12).
   - The 09-25 one-day funding veto changed SOL by −0.000063 / −0.000084 (r7 DECLARE.md:15). P32 lists the left-tail rug veto as DEAD (AUDIT.md "Filters on existing books").
3. **AUC 0.65–0.70 would be a K2 kill in practice.** On r3's held-out pool the tape-only S0 already reached AUC 0.8459, and S1 0.8542: a +0.0083 cluster gain (r7 DECLARE.md:11-12; that population is post-graduation, not this D1 one). The question is whether funding adds ≥ +0.02 over S0, and K2's bar is about 1.4 SE at August-like volume (r7 FREEZE.md:86-88).
4. **Value to any book today: 0 SOL/day.** Stage 1's population has no book (§2). A stage-1 survivor is a veto candidate that still needs a paired read on unread D1 hours, and no such lane exists while walk 2 is "reserved".

## 5. Recommendation: **PARK** stage 1; record stage 2 as **NOT_RUNNABLE**

- **Stage 1: PARK.** It is one MiScusi job away once the owner line, snapshot and r11 amendment exist. But the read would test a pre-graduation veto for a domain the lab has shelved, with a negative prior on K3 and weak power on K2. A pass changes no book.
  - Spend the 10-27..10-31 attention on the 10-30 reads, the HV6 registration and the 11-01 DBC read.
  - **Revive trigger:** a registered or planned book whose universe is pump.fun pre-graduation, or young PumpSwap pools inside the stage-2 grid. Or the owner explicitly wants the research read; then follow §3.
- **Stage 2: NOT_RUNNABLE as frozen** (S2-6). Record it in the library rows. Any post-graduation rug veto, for example as a FLOOR-SLOW shadow column, is a **new leaf** with three needs:
  - its own DECLARE and FREEZE on that book's population;
  - a QP ruling for funding rows first seen after 10-16T01:00Z;
  - a live funding feed.
  It should be scoped only once FLOOR-SLOW is drafted (no earlier than 11-08).
- **Library rows** lib-c-G3 / lib-c-H1 need correcting:
  - the QP funding ruling exists (FUNDING-D0.md, YES-WITH-CONDITIONS);
  - the current leaves are r9 (stage 1) and r10 (stage 2);
  - the F1 edit and FINAL marker are met;
  - stage 2 is NOT_RUNNABLE;
  - stage 1 has two extra blockers (§1 items 6-7).

## 6. Not measured

- No funding row, no October tape byte, no coverage figure: K4 is unknown.
- Whether the Oracle files are still complete.
- Whether the creates file 10-12T04 still holds the malformed line under the frozen grep path. I infer that it does from #1061's refusal and the frozen code, and did not test it.
- October run time and memory.
- The §4 table is a binormal approximation from base rates. It is not a P&L estimate, and no SOL figure in this file comes from a measurement.
