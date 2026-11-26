# Leg (iii): v4 host decision md5 vs osb_follow CE1 decision md5 (NOT RUN in this PR)

Run later by the manager as a MiScusi job, at a tree that includes 8288ba12 (host pool-prune), on real recorded hours. This PR commits no script for it and reads no declog, ledger, tape, out/ dir or md5-by-hour file.

What it compares. The rule is FIXED here, before any run, and is implemented by `tools/floor_v4_leg3_compare.py` (committed, unit-tested on synthetic fixtures, NOT run). It is reviewed (T1, QP + REVIEWER) before the job runs. No mapping may be chosen after any result is seen; a failed leg (iii) is never re-run under another normalization.

- B = osb_follow CE1 `decisions.md5` (`osb_follow.decisions_md5_from_rows`, scripts/research/osb_follow.py:290), whole span, read as a value only. Per-hour `md5-by-hour.jsonl` is NOT a source unless the comparator recomputes both sides under this one normalization.
- A = the same function applied to the v4 host's engine ledger rows over the same span, after remapping each `position_id` to `position_id("l1-floor-v2", pool, entry decision_ms, decision_slot)` (tools/arena_rules.py:299 hashes the bot id; osb_follow runs bot `l1-floor-v2`), with pool / decision_ms / decision_slot taken from the v4 decision log's entry row for that order id.
- PASS iff A == B. Same hours, same `seed_from_hour`, at the exact install tree (including 8288ba12). A ledger row with no matching entry row makes the result UNEQUAL.
- The md5 covers (position_id, entry decision_ms, exit decision_ms). The TP reason label is not in it (osb_follow overrides `L.TP` but not `L.REASON_TP`, so its CE1 take-profit exits are labelled `tp_1.5x`, v4 at 1.3 says `tp_1.3x`), so no label mapping is applied. Entry meta (rule id, `q_star_lamports`) is not in it either.
- Report: the hours covered, A and B, equal or not. No price, fill, P&L, exit_reason or tp/sl mix.

Leg (iii) is OWED. It blocks any v4 config, Helm install or restart until it PASSES at the exact install tree, with legs (i)/(ii) re-run at that tree.

Legs (i) and (ii) (v2 and v3 byte-identical, base vs head) are in `equiv_v2_v3.py` and `result_*.json` here.
