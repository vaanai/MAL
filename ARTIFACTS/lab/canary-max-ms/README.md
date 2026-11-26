# canary_max_ms: md5 decision-equivalence (key absent)
Script: equiv_canary.py (fixtures only). result_base_704c75fe.json and result_head.json are byte-identical (md5 2f591f6e5d3a777860d385461640e999): v2, v3 q500/750/1000 and v4@1.3 host decisions plus the executor 14-day canary trip.
result_prekey_38d38ae9^.json is the tree before the key was merged (38d38ae9, PR on main earlier): every arm equals base except the v4 arms, which are "skipped_tree_has_no_v4" (that tree has no v4 rule); the executor canary leg is identical (effective_ms 1209600000).
