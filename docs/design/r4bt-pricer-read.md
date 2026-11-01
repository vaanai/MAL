# r4-b-T pricer, read wrapper and A3 entry (design note)

Records and code for the arena book `r4bt-a1` under DEC-031 Amendment 25 (DRAFT, PR #1187). Paper only. Synthetic tests only: no r4-b decision log, declog, host `out/`, tip file, derived-copy tape, arena tape or ledger of a real book was opened to write this. No book has cleared the promotion gate; nothing here says r4-b or r4-b-T makes money.

## Files

| File | Role | Amendment 25 item |
| --- | --- | --- |
| `tools/r4bt_gate.py` | EXP-027 "read written" gate (G8), closed roots, engine and scorer pins, closure walk and blob comparison | 2, 4, 13, 25 |
| `tools/r4bt_price.py` | Pricer: `ArenaEngine` over the derived copy, rent 0, call clock = the decision row's `ms`, counts only | 24, 25 |
| `tools/r4bt_read.py` | Read wrapper over `arena_am6_read.run`: boundary cross-check against `r4bt_attempts count`, config pin check, rent-charged leg, multiplicity, disclosures | 27 |
| `tools/r4bt_a3.py` | A3 entry: classify the daily record, stop the book at a hard halt | 12 |

## Which files must be blob-identical to bc65c088

`bc65c088793753954c86b82d0e5eff74aa038e0b` is the install sha (M'). The decision host's closure is the files a static `tools.*` import walk reaches from `tools/r4b_host_glue.py`, `r4b_live.py`, `r4b_live_host.py`, `r4b_tip_reader.py`, `r4b_feed_adapter.py`, `r4b_feed_lag_probe.py` and `r4b_feed_merge.py` (`r4bt_gate.static_closure`; 75 files at bc65c088, all blob-identical at origin/main 25e49f91), plus `models/r4b/` (7 files) and the three wrappers `scripts/research/r4b-dry-long.sh` (8a0c7628), `r4b-dry-sidecar.sh` (3659842e), `r4b-dry-stop.sh` (1cfe12c4). QP's recorded closure is 88 files; the walk is a subset check (it sees no non-Python file), so QP's list stays the authority and `blob_identity` takes any list.

The closure includes `tools/arena_paper.py`, `arena_declog.py`, `arena_stack.py`, `arena_zst.py`, `arena_feed.py` and `h5_shadow.py`. **Any merge to main that changes one of them before W breaks identity**, and with it the carry-over of dry-long-1101a's smoke, P0 and P-C2 (Q8). Open PR #1182 changes `tools/arena_paper.py` (the gap-fill engine, Q1): if it merges before W, W is not blob-identical and Q8's carry-over needs QP's separate ruling. This PR changes none of the closure files; the new files (`r4bt_*.py`, their tests, this note) are outside it.

## W plan

1. W = the merge commit of this PR (plus the PRs QP requires before the clock, none of which may touch the closure). Cut W as late as the other pre-clock merges allow, once.
2. At W, record `blob_identity(repo, bc65c088, W, closure)` (identical, none missing) and the equal blobs of the three wrappers and `models/r4b/`. Command: `python -c "from tools import r4bt_gate as G; cl=G.static_closure(lambda r: G.git_show('.', W, r)); print(G.blob_identity('.', G.FROZEN_SHA_M_PRIME, W, cl))"`.
3. `register_bot("r4bt-a1", W, 1794031200000, deciding_stake=0.25, model_ids=[])`; the host and the pricer both run from clean checkouts at W (the scorer refuses any open row or engine start whose `code_head` is not the registered frozen_sha, `tools/arena_score.py:328-340`).
4. The pricer is given the engine and scorer sha256 from the registration record (`9d2fdb04...3514`, `c4859323...f27f3` at 25e49f91, unchanged by this PR).

## Assumptions made while QP-RULING-1102-r4bt (Q1 to Q8) was not written

- Q1: no price-path gap-fill. Declog `gap` rows with `data_loss` not false and the derived copy's gap rows are the item 7 union, both fed to the engine; each excludes the trades whose span it overlaps, under the scorer's 5% cap. If QP adopts #1182's mechanism, `r4bt_price` gets a small adapter and W must be re-cut (see above).
- Q3/Q4: the V-band check is the frozen host's (`tools/r4b_live.py:1029`); the pricer adds none. The pricer feeds no follower state.
- Q5: `k_rounding` and `feed_delay_slots` are required arguments with no default in the pricer and the read; the read compares them with every start and open row. The pricing route is the `ArenaEngine` one, not `r4b_book.round_trip`.
- Q2: the decode-change check is supplied as input (`--decode-checks`, the per-write verdict of `tools/a3_redeploy_check.py`). Without a result for a program redeploy the verdict is `NEEDS_CHECK` and the window continues. No dead-man rule (a3_halt's 48 h) is included.
- G8: the gate is a manager-written JSON record pinned as path@sha256 (schema `exp027_read_written_v1`, route a or b). It is not the ND determination record (#1181). Nothing opens `/data/mal/r4b`.
- Attempt set: the pricer prices the entries before the boundary of `r4bt_attempts count` (#1188, a JSON the pricer and the read take by sha256, so there is no import of #1188 code) and refuses if its own count of those entries differs from the file's counted plus excluded-hour attempts. The read refuses if the ledger's boundary or attempt count differs from the file's.
- A registry stop before the boundary refuses the pricer (an A3 halt before the boundary makes the read NOT_DECIDABLE); a stop after it does not.

## Left

The confirmation read (DEC-031 section 2 step 4) is not built. Hour-health tool (G4), the "read written" record writer, the sampler chain, the Q1 adapter if QP wants it, and the envelope and registration steps are the manager's or later PRs'. The pricer's memory on a full 8 to 12 day derived copy is not measured; it drops idle bonding curves and calls `trim_history` each hour, and a real-layout count-only pass is owed before the first run (feedback: real-layout precount).
