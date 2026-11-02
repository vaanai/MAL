# DESIGN: s1 on the tip source (`h5v1q50-boostfloor-s1t`)

Written 2026-11-01T23:24Z, revised 2026-11-01T23:3xZ on QP comment 6565588204 and reviewer comment 6565593139 (`date -u` on mal-research-0) by lane L1 of the 2026-11-01 push. Records and design only. Nothing was started, registered, priced or spent. **No book has cleared the promotion gate. Nothing here says any book makes money.** The DEC text is the DRAFT Amendment 24 appended to `DEC/DEC-031-forward-paper-arena.md` in the same PR. On any conflict the DEC text controls. This was split from PR #1178 on reviewer note N5, because it must merge before Q50's read verdict exists.

## 1. Why an alternative, not an addition
- **The decision.** The manager decided (PUSH-PLAN-1101 L1; L5 §5 item 2) that s1 is not run on the public websocket. That feed's data loss ended both HV4 X-arm reads as NOT_DECIDABLE_5PCT, and the Q50 executor saw a gap-held share of about 1.0 on 10-28/29.
- **Why not both.** DEC-031 Am.3 item 5 forbids a second frozen copy beside the parent. So the tip book replaces s1; it does not run beside it.
- **Am.17 is AMENDED (QP report §5), not "met".** The amendment rests on feed health only, adds no condition that depends on Q50, and keeps the lapses. Quant-proof found it admissible.

## 2. What changes
| Item | s1 (Am.17, 18, 22) | s1t |
|---|---|---|
| bot_id | `h5v1q50-boostfloor-s1` (never registered) | `h5v1q50-boostfloor-s1t` (new; keeps Am.5 item 2.1's prefix properties; id test owed) |
| code | S\* `5c2ab7c6` | S\*' = tag `q50-succ-tip-1` (tag-only; #1185 closed unmerged by design). `tools/h5_shadow.py` and its whole transitive first-party import set are byte-identical to S\*'s; S\*' adds `tools/h5_shadow_tip.py`, `tools/h5_shadow_tip_equiv.py` and tests |
| feed / host | public `logsSubscribe` + second provider, mal-fast-0 | derived copy `/data2/mal/arena-tip-feed/hv7`, mal-research-0 (transport B) |
| EXP-024 coverage | `EXP/EXP-024-note-s1-successor.md` (blob 7015abc9…; S\* blob 7f859b12…, s1 only) | its own note in the Part A form, owed with S\*' (it pins S\*''s blob), merged before the smoke |
| P0 | Q50's #1413 reused (35.333333333333336 a day) | the same for the 11-07 clock (valid at or before 11-07T00Z). For the 11-10 slip: a tip P0 on 11-06, 11-07 and 11-08 (all after Q50's window) before a registration on 11-09; there is no 11-08 or 11-09 clock |
| H / end | E = 2026-12-01T00Z (Am.22 8.1) | the same E, so H = 24 d from 11-07 and 21 d from 11-10 |
| reads | N_s 200, S_cap clock + 10 d, N_c 300, E_c = E | the same, with s1t's clock |
| smoke | ≥ 6 h websocket smoke (Am.22 item 11) | ≥ 24 h on the tip. Early pricing-off form (QP-accepted): start by 11-05T12Z, H5 Look-2 seal on every pool (trigger records written; no outcome, strip or ladder), counts printed as flags only; gives the 11-07 clock. QP's later form (fresh start ≥ 11-06T00Z) gives the 11-10 slip |
| clock | 11-06T00Z (slip 11-07) | **11-07T00Z** with the early pricing-off smoke; first slip **11-10T00Z** (1794268800000), S_cap 11-20T00Z (1795132800000) |

## 3. Latency and the trigger-age cap
- **Where the lag comes from.** The tip shows a print at block time plus about 2 s (p90 block lag 1,697-1,995 ms in the r4-b sidecar, 10-31/11-01). The writer adds its ingest lag (`lag_ms` 485 in the writer status at 2026-11-01T22:39Z), and transport adds 23 ms (receiver p90).
- **The landing is charged, not free.** The scorer prices the landing from `t_detect_ms` (`tools/arena_h5_score.py:15-18`, `:211`, `:473`). S\*' must stamp `t_detect_ms` from the engine host's own clock. A row's `t_recv_ms` would under-charge the writer and transport lag, a bias toward P&L.
- **The age cap.** `MAX_TRIGGER_AGE_MS` = 3,500 (`:97`, `:458`) excludes older triggers, and that exclusion counts in the 5% cap. Its share on the tip is unknown, so the smoke measures it. The proposed bar is at most 2.5% (half the cap). It is timing only.
- **Prior (in-sample, not a measurement of s1t).** EXP-012's latency note says the edge under V was gone by k ≥ 12 slots. A tip book lands about d slots later than a websocket book.

## 4. The smoke's hours (QP decides between two forms)
- **The problem.** A smoke from 2026-11-05T12Z covers Q50-counted hours (Q50's window ends 2026-11-06T00Z). At S\*, fresh-forward computes outcomes for every pool whose s0 is after the process start. So a normal smoke would price the Q50 rule on Q50-counted pools.
- **The s1 precedent.** The s1 note covered that for s1's own process (never counted, never opened).
- **The stricter choice for s1t.** The wrapper seals every pool through S\*'s frozen H5 Look-2 seal (`h5_look2_start_ms` ≤ the fresh start, `h5_look2_observed` False), as DEC item 6 says. The smoke then writes trigger, pool and gap records only. The `suppress_outcome` hook is not used: it also withholds the trigger record, and with `seal_start_ms` None it is never consulted.
- **What still judges it.** The blocking list (counts, timings and flags), Am.23 item 8's reports and the trigger-age bar need no outcome. The outcome path is proved by the md5 equivalence on explore-0814 hours.
- **QP's stated form (report §5 (c)).** Fresh start ≥ 1793923200000 (11-06T00Z), counts and timings only. Fresh-forward then never tracks a Q50-counted pool. A 24 h smoke ends at or after 11-07T00Z, so the clock is the 11-10 slip (S_cap 11-20T00Z, 1795132800000).

## 5. Dated steps to the 11-07 clock (UTC)
| By | Step | Owner |
|---|---|---|
| 11-02 06Z | This PR: QP OK and reviewer verdict at the exact head; merge before any Q50 read verdict exists | QP, reviewer, manager |
| 11-02 12Z | QP rulings: Am.17 amended (accepted); P0 reuse for 11-07 and a tip P0 for the 11-10 slip; guard (h) for the tip path; the pricing-off smoke (accepted); trigger-age bar; the multi-sig re-rule (Am.23 item 3 trigger; the writer status shows 1,283 over 85 h) | QP |
| 11-03 18Z | S\*' built: tip input mode, the `t_detect_ms` stamp test, the identity test, the md5 equivalence (S\*' tip mode on writer output from explore-0814 hours against S\* replay on the same walker rows), the s1t id test, and the s1t EXP-024 note pinning S\*''s blob | builder |
| 11-05 12Z | **Freeze deadline**: S\*' tagged with QP OK and reviewer verdict at its exact head, and the note merged. Otherwise the amendment lapses and s1 runs under Am.17, 18 and 22 with Am.22 Errata 1 and 2 (at most one second provider) | QP, reviewer, manager |
| 11-05 12Z | Counts-only smoke starts at S\*' on research-0, scratch root, ≥ 24 h; blocking list and trigger-age bar notebooked first | manager |
| 11-06 12Z | Smoke judged; A3 books entry and loop; SEALS reader line; memory check | manager, QP |
| 11-06 20Z | Registration record (notebook + `bots.json` line), kill-review check, N + 1 line | manager |
| 11-06 23:00Z | Shadow start at S\*', at least 1 h before the clock | manager |
| 11-07 00Z | Clock, if Am.23 item 6's line holds over [11-06T00Z, 11-07T00Z) (≤ 6 data_loss rows at T_max 333 s) | — |

## 6. Code (now PR #1185, tag-only `q50-succ-tip-1`; QP K1 to K3 owed there)
- **Input.** Tail the derived copy's `tape-<hour>.jsonl[.zst]` (the arena layout; reuse `tools/arena_state.py`'s file reader or the receiver's verified-offset rule). Refuse every other dir except test tmp dirs, and refuse `/var/lib/mal/sealed`, `tip-tape-archive` and `/data/mal/arena/tape`.
- **Row mapping:**
  - `pool_create` → the CreatePool announce (pool, mint, quote_mint);
  - `complete` → the bonding complete;
  - `trade` → the print, with event V from `virtual_quote_reserves`;
  - a data_loss `gap` → the engine's gap record.
- **Stamp.** `t_detect_ms` is the host wall clock at the decision. In replay it comes from a fixed offset given by the harness, and the offset is recorded.
- **Unchanged from S\*.** The RPC classifier stays as at S\*. The fresh-forward Look-2 handling stays as at S\* (the start not applied; the end at `SUCC_END_MS`).
- **Smoke switch.** It seals every pool through the H5 Look-2 seal (`h5_look2_start_ms` ≤ the fresh start, `h5_look2_observed` False), never `suppress_outcome`, and is refused outside a scratch root. A test shows trigger records with `t_detect_ms` and `block_time`, and no outcome, strip or ladder record.
- **Tests.** Identity of the decision functions with S\*; the stamp; the refusals; and the md5 harness, with a minimum-triggers floor notebooked first.

## 7. Not measured
The trigger-age distribution on the tip, d, the tip's gap rate over a fixed window, the per-hour `pool_slots_multi_sig`, s1t memory, and any P0, smoke, trigger or outcome. No tape, tip file, derived-copy `tape-*` file, counts log, ledger, `bots.json`, shadow output or sealed job view was opened.
