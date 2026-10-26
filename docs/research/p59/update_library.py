"""P59: append a manager_note to lib-a-A40 and lib-a-A41 in docs/library/ideas.jsonl (no other change). Run from the repo root."""
import json
P = "docs/library/ideas.jsonl"
N = {
 "lib-a-A40": "2026-10-26 P59 (exploration, not gate evidence; PREREG docs/research/p59/PREREG.md written before pricing): applied to FLOOR (l1_rule_tpfix, 160,000 per send, 4 Aug/Sep blocks, 300 s cap with tp 1.5 kept) it is DROP. Confirmation (Sep, n 25 base vs 135 with cap): mean flat -2.1287% vs base +5.6212% (delta -7.7500 pp), pressure -1.7964% vs +4.7240% (delta -6.5203 pp), 0 of 7 days positive; discovery (Aug) delta -6.6429 / -5.3851 pp, 5 of 14 days positive vs 14 of 14. tp hits fall from 35 to 10 of the pooled trades; attempts rise 332 to 786 (shorter hold shortens the cooldown). The library evidence is on the g_reachable BOOST-window population (300-420 s holds), not FLOOR's 24 h pool book; the cap turns FLOOR into a different strategy. Control: base reproduces L3 REPRICE l1 to the digit (n 332, flat 6.420621465162983%).",
 "lib-a-A41": "2026-10-26 P59 (exploration, not gate evidence): already part of FLOOR's priced path. tools/arena_stack.py:27 GUARD = 1.15 (buy reverts and pays one send if exec price > 1.15 x decision spot, the same rule as h4-boostmid RULE.md:26). Adding it to FLOOR-SLOW changes nothing: DROP as REDUNDANT. Diagnostic guard-OFF ablation on FLOOR's 4 blocks: pooled flat 6.4206% -> 6.3281%, pressure 5.1417% -> 5.0637% (the guard is worth about +0.09 pp / +0.08 pp there, and 0 on Sep). Docs: docs/research/p59/.",
}
out = []
for line in open(P):
    if line.strip():
        d = json.loads(line)
        if d["id"] in N:
            d["manager_note"] = N[d["id"]]
            line = json.dumps(d, ensure_ascii=False) + "\n"
    out.append(line)
open(P, "w").write("".join(out))
