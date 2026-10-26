# Runbook: P35 v2 feed takeover, rehearsal (A-P1) and the production outline

Written 2026-10-26 from 17:42Z (`date -u`). Records and tooling only. **No book has cleared the promotion gate. Nothing here says a book makes money.**

- Design: [docs/research/p35-longbook-handoff/DESIGN.md](../research/p35-longbook-handoff/DESIGN.md), sections 2 and 4.1.
- QP: `QP-RULING-1026-p35-design.md` (sha256 `2e53ed9a...`).
- Code:
  - `tools/arena_feed_v2.py`
  - `tools/feed_handover_proof.py`
  - `tools/p35_rehearsal.py`

## 1. Rehearsal A-P1 (now; mal-fast-0 only)

**Why mal-fast-0.** The rehearsal opens public websockets: 4 sockets per feed, up to 3 feeds at once. On mal-research-0 they would share the
public-RPC IP of HV4's and HV6's in-clock feed. So `p35_rehearsal.py` refuses mal-research-0 before 2026-11-11T14:00Z (DESIGN 2.4).

**What it does** (`tools/p35_rehearsal.py run`, defaults):

| Phase | Span | What |
|---|---|---|
| 1 | start to +3 h | Two independent continuous v1 feeds, `ctrl/` and `ho/`. At +3 h, `rates` over [start + 20 min, start + 3 h) gives the disagreement rate per program in 120 s windows and the rows per window. It proposes T_p = max(0.001, 2 x p99) and MIN_OVERLAP_ROWS = floor(0.5 x p05). |
| 2 | +3 h to +9 h | A v2 standby on `ho/` (with those thresholds) takes over from the v1 writer at +3 h 20 min (seam 1). A second v2 standby takes over from the v2 writer 2 h later (seam 2). Everything stops at +9 h. |
| proofs | after +9 h | `feed_handover_proof seam` on [seam - 10 min, seam + 10 min] of each seam, and `parity` `ho/` vs `ctrl/` on [seam - 1 h, seam + 1 h], split seam (+-10 min) / control. Result: `<root>/summary.json`. |

**A-P1 PASS:**
- both seams PASS;
- 0 data_loss or hard gap rows in either seam window;
- `seam_miss_rate_not_above_control` is true for both seams.

The thresholds it proposes are **inputs for QP**. They are pinned in the FLOOR-SLOW amendment, never taken from a production seam.

**Job spec** (manager submits; a MiScusi job, `sh` command; not resumable, because a resumed run would restart the feeds with new instants):

| Field | Value |
|---|---|
| machine | `mal-fast-0` |
| repo / git_ref | `vaanai/MAL` / the head of this PR (or its merge sha) |
| command | `<PY> -m tools.p35_rehearsal run --root <ROOT>` |
| PY | a python on mal-fast-0 with `websockets>=12,<14`, `certifi`, `numpy` (the listener venv; confirm the path before submit) |
| ROOT | a scratch dir on mal-fast-0 outside `/data/mal`, e.g. `$HOME/p35-rehearsal/r1`, with >= 10 GB free (two tapes of 9 h, compacted per hour) |
| memMb | 6000 (up to three feeds of <= 2 GB each, then one 2 h parity index per tape). Check `systemctl show user-1002.slice -p MemoryCurrent` first (fast-0 rule: one heavy job at a time) |
| timeLimitSec | 36000 (9 h run + proofs) |
| resumable | false |

**Before submitting**, run `<PY> -m tools.p35_rehearsal run --root <ROOT> --plan-only` on the host. It prints the instants and creates nothing.

**After the run.** Read `<ROOT>/summary.json`: counts, verdicts, thresholds and instants only. Notebook it (kind result) and give it to QP.
The tapes under `<ROOT>` are rehearsal tapes, not production. Delete them once QP has ruled.

## 2. Sampler and table successors (QP 1025 risk 5)

The D1 sampler and the D3 table are 14-day jobs too. Their hand-off is an **overlap**: the successor job starts before the old one ends and
writes its own file, and the reader takes the union. No sample instant is lost. A missing sample fails an hour (Am.6 2.2). The exact job
specs depend on the book's registered sampler command, so they go in the FLOOR-SLOW records PR (PR 7), pinned by job id.

## 3. Production takeover: outline (the script is owed, PR 3b, after A-P1 and its QP ruling)

These pins are not known yet, so no production script is in this PR:
- the v2 code sha;
- T_p and MIN_OVERLAP_ROWS;
- the takeover instant inside [HV6's last in-clock instant + 1 h, H' #1652's end - 1 h], about [2026-11-11T14:00Z, 2026-11-11T23:30Z].

Steps, modelled on feed-handover-1107:
1. A host-side MiScusi job on mal-research-0, submitted at least 30 min before the instant: `python -m tools.arena_feed_v2 --standby --out-dir
   /data/mal/arena/tape --takeover-at-ms <pinned> --takeover-deadline-ms <pinned + 20 min> --max-disagree-rate <pinned> --min-overlap-rows
   <pinned>`, at the pinned git_ref, with memMb 2253 and the 14 d limit.
   - Before 2026-11-11T14:00Z the module refuses the production tape.
   - It refuses unpinned thresholds.
   - It stops only the single lock holder whose argv has `tools.arena_feed[_v2]` and `--out-dir /data/mal/arena/tape`.
2. A post-check job at the instant + 25 min runs `feed_handover_proof seam --tape /data/mal/arena/tape --expect-takeover --qp-ruling <the ruling
   naming the window> ...` over [instant - 10 min, instant + 10 min]. It prints counts only.
3. If the standby aborts (exit 5, not ready, or exit 3, a lock race), the old feed is untouched or another writer holds the tape. Then H' runs
   to its limit, and the manager runs the plain f5bd26e7 fallback (DESIGN 4.1) before H' ends.
4. Notebook, LAB_STATE mid-week line, and both feed shas plus the switch instant into every in-clock book's records.
