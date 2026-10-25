# FLOOR revival ops sheet (DRAFT), 2026-10-25

Written 2026-10-25T18:45Z from `date -u`. Records only. Companion to DEC-031 Amendment 15 (draft). FLOOR has 0 out-of-sample trades and has not cleared the promotion gate; this sheet starts nothing. No tape, job status or outcome was opened to write it. Recompute every date from `date -u` at the real merge instant M.

Seals to obey the whole way: HV4 (ledgers, fills, prices, exits, positions, P&L, decision rows, bots.json, attempt counts, #1561/#1562 status or log), Q50 (same classes, shadow #1419/#1421/#817, canary; EXP-024 Look-2 seal to 2026-11-14T00Z), DBC forward window [2026-10-25T00Z, 2026-11-01T00:10Z), arena tape counts only under QP rulings, no join to HV4/Q50 outputs, and Amendment 15 item 9 (own seal from M to G1).

## Step order

| # | Step | Earliest | Owner |
|---|---|---|---|
| 0 | Draft PR; quant-proof OK on the exact head; reviewer verdict on the PR (f1); manager merges (workers do not). This is M. | M | manager |
| 1 | Notebook M with `date -u`: "arena feed health was open to the manager from 2026-10-25T12:18:48Z to this merge"; start Amendment 15 item 9 seal; radar windows go live with the merge | M | manager |
| 2 | Fresh P0 days: d+1, d+2, d+3 (d = UTC date of M); all three must be OK | d+1 00:00Z to d+4 00:00Z | none (waiting) |
| 3 | Write the v4 P0 job script (copy of `A2-p0count-v3.sh`, days d+1..d+3, gate `rate_att_lo >= 6.0`); sha256-pin it in the records PR; ask QP for the counts-only ruling | before step 4 | worker |
| 4 | A1 gap check on the three days, then A2 P0 count job (once; no re-count) | d+4 00:10Z | manager via MiScusi |
| 5 | Verdict: `rate_att_lo >= 6.0` and 3 OK days, else STOP (no registration, N unchanged). Else H = ceil(150 / rate_att_lo), clipped to 14..25 | after step 4 | manager |
| 6 | md5 decision-equivalence check (item 5): QP rules the slice (before 2026-10-25T00Z, no sealed window), job runs, result notebooked | before E3 | QP then manager |
| 7 | Restart budget (item 6): list planned process restarts in [clock, END_BOOK); merge restarts within 600 s; at most 1. Pick the clock and HO. If a second feed hand-off is needed, arm it first (owed runbook, separate PR) | before E3 | manager |
| 8 | Pre-clock checks over [clock - 75 h, clock - 3 h): G1 gap precondition (0 rows), DR1 dry run (<= 5%), thresholds file (Erratum 2, unchanged bytes), X = 600 s; counts to QP only, by an agent not on FLOOR's path | clock - 3 h | independent agent |
| 9 | Records PR: H, HO, END_H, END_BOOK, window, slip formula, script shas; notebook copy of the `bots.json` line with the same H | before E3 | worker then manager |
| 10 | E3 register (deadline clock - 15 min). N is spent here, not before | clock - 1 h to clock - 15 min | manager |
| 11 | E4 segment 1 (submit in [clock - 6 h, clock - 300 s], limit <= 14 d, END_MS = HO) and segment 2 (`after` segment 1, END_MS = END_BOOK, limit between END_BOOK - HO + 6 h and 14 d); host-side check job at HO + 15 min | clock - 6 h | manager |
| 12 | Hand-off at HO (feed restart and runner hand-off in the same minutes if HO is a feed restart); notebook the instants; LAB_STATE mid-week restart line | HO | manager |
| 13 | Read at the first 00:00Z boundary B with O - floor(0.05 (O + R)) >= 100 on >= 5 UTC dates (Amendment 12 item 1), tool merged with QP OK and reviewer verdict first; END_H = clock + H, no extension | B <= END_H | manager + QP |

## Dates computed from `date -u` (2026-10-25T18:45Z now)

Assume the earliest realistic M: 2026-10-26 (anywhere in the day).

- P0 days: 2026-10-27, 10-28, 10-29. They close at 2026-10-30T00:00Z. A1 and A2 can run from 2026-10-30T00:10Z. Allow up to 6 h for A2 (its pinned limit): verdict by about 2026-10-30T08Z.
- Evidence window for a clock of 2026-10-31T00:00Z: [2026-10-27T21:00Z, 2026-10-30T21:00Z). It starts after M, as f2 needs. G1 runs at or after 2026-10-30T21:00Z. E3 in [2026-10-30T23:00Z, 2026-10-30T23:45Z). Earliest clock = M + 75 h rounded up to the next 00:00Z, then pushed by the P0 count, which is the last step here: **2026-10-31T00:00Z**. Slip: 2026-11-01T00:00Z, window [2026-10-28T21:00Z, 2026-10-31T21:00Z).
- If M is later than 2026-10-26, every date above moves by whole days. If M is after 2026-10-26T23:59Z the pinned radar windows no longer cover the P0 days and evidence windows: lengthen them in the merging PR.

By H (the horizon from the fresh rate), END_H = clock + H d, END_BOOK = END_H + 25 h. Feed restarts known: 2026-11-07T11:50-11:58Z (pinned). A second feed restart exists inside any window that outlasts the successor feed's 14-day limit (owed).

| rate_att_lo | H | Clock | END_H | END_BOOK | Restarts in window | Notes |
|---|---|---|---|---|---|---|
| >= 10.72 | 14 | 2026-10-31 | 2026-11-14 | 2026-11-15T01Z | 11-07 only (HO = 11-07T11:5xZ) | seg 1 ~7.5 d, seg 2 ~7.5 d |
| 7.5 | 20 | 2026-10-31 | 2026-11-20 | 2026-11-21T01Z | 11-07, plus a second feed restart if the successor was submitted early | seg 2 ~13.6 d: inside 14 d with 6 h margin only if HO is 11-07 |
| 6.0 to 7.14 | 21 to 25 | 2026-11-08 | 2026-11-29 to 2026-12-03 | 2026-11-30T01Z to 2026-12-04T01Z | the second feed restart (about 2026-11-21, pinned by the owed runbook) is HO | clock moves to 11-08 so the 11-07 restart is before it |
| < 6.0 | no registration | | | | | N unchanged |

Where a row needs a second feed hand-off, it needs the owed feed runbook (new dates, same script pattern as `docs/runbooks/feed-handover-1107.md`, plus a QP ruling like `QP-RULING-1023-feed-handover.md`) armed before E3. It is not in the PR for Amendment 15.

## Capacity arithmetic (why 6.0 and why one hand-off)

- Segment 1: submit in [clock - 6 h, clock - 300 s], limit 14 d: covers HO up to clock + 13 d 18 h.
- Segment 2: starts at HO, limit 14 d: covers END_BOOK up to HO + 13 d 18 h. With HO = clock + 12 d 12 h: END_BOOK up to clock + 26 d 6 h, so H <= 25 d (END_BOOK = clock + 26 d 1 h). H = 26 and over needs a third segment.
- Restart cost: every restart excludes the positions open at it. With a 24 h hold, about 1 day of attempts is open, so about 1/H of the book: 4.0% at H = 25, 5.0% at H = 20, 7.1% at H = 14. The §1.4 cap is 5%. One restart at H = 25 uses most of it.

## Not done here (owed)

v4 P0 job script; E4 segment scripts; md5-check ruling and job; second feed hand-off runbook; the records PR entries. A worker can write scripts after the amendment merges; nothing in this repo runs a job.
