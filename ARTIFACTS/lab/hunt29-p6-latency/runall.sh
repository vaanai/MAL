#!/bin/sh
# One job, sequential, resumable. Order: control legs first per rule, then faster.
J=/data/mal/hunt-1008/push-1016/hunt29/p6/one.sh
H=/data/mal/hunt-1008/push-1016/hunt
sh $J l4 1.9 /data/mal/hunt-1008/push-1016/hunt28/l4/l4rules.py:L4Rule ""
sh $J l4 0.3 /data/mal/hunt-1008/push-1016/hunt28/l4/l4rules.py:L4Rule ""
for L in 1.9 0.6 1.0 0.3; do
 sh $J l4 $L /data/mal/hunt-1008/push-1016/hunt28/l4/l4rules.py:L4Rule ""
 sh $J l1 $L /data/mal/hunt-1008/push-1016/hunt24/l4-audit/l1_rule_tpfix.py:L1Rule '{"aux_dir":"/data/mal/hunt-1008/push-1016/hunt24/l4-audit/work"}'
 sh $J w2quiet $L /data/mal/hunt-1008/push-1016/hunt23/w2-quiet/w2e_rule.py:Rule ""
 sh $J w2bstrand $L /data/mal/hunt-1008/push-1016/hunt23/w2b-strand/rule.py:StrandTable '{"table":"/data/mal/hunt-1008/push-1016/hunt23/w2b-strand/work/cell_table.npz"}'
done
echo ALLDONE $(date -u +%FT%TZ)
