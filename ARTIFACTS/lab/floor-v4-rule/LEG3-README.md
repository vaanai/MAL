# Leg (iii): v4 host decision md5 vs osb_follow CE1 decision md5 (NOT RUN in this PR)

Run later by the manager as a MiScusi job, at a tree that includes 8288ba12 (host pool-prune), on real recorded hours. This PR commits no script for it and reads no declog, ledger, tape, out/ dir or md5-by-hour file.

What it compares (names and md5s only; SEALS (k)/(m) readers):

- A: the `l1-floor-v4` lane host (`tools/floor_lane_host.py replay`, config `rule_id` `l1-floor-v4`, `tp` 1.3 (CE1) or 1.5, `q_star_lamports` absent or 1000000000) over the recorded hours:
  `floor_lane_host.decision_digest(root, bot_id="l1-floor-v4")`, the md5 of the canonical entry/exit rows.
- B: osb_follow's CE1 decision md5 on the same hours: `decisions.md5` / `md5-by-hour.jsonl` of the CE1 cell (TP 1.3, vault 1.0, HOLD_S unchanged).
- Match rule: equal md5 per hour. Expected normalization before hashing, if the two row formats differ: drop chain fields (`prev`, `sha`, `order_id`) and map the rule id; the mapping used must be written next to the result.
- Report: the hours covered, the two md5s per hour, the count of equal / unequal hours. No price, fill, P&L, exit_reason or tp/sl mix.

Legs (i) and (ii) (v2 and v3 byte-identical, base vs head) are in `equiv_v2_v3.py` and `result_*.json` here.
