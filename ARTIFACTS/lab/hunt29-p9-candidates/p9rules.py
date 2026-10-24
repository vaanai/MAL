"""P9 rule: hunt29 P3 G4 v1 kit form, cell G0 ONLY, on fresh-0903 (stand-alone held-out read). PAPER ONLY, exploration screen, no profit claim.
Only change vs P3: the FLOOR universe module is the P7 copy (fresh-0903 aux key) and the cell/trigger list is restricted to G0. Declaration: DECLARE.md."""
import sys
import l1_rule_p7
sys.modules["l1_rule"] = l1_rule_p7  # p3rules does `import l1_rule`; swap in the P7 copy (adds ONLY the fresh-0903 aux key)
import numpy as np
import p3rules
from tools.arena_screen import Cell


class P9Rule(p3rules.P3Rule):
    name = "p9_g4_g0_v1"

    def cells(self):
        return [Cell("G0", cooldown_s=60.0)]

    def triggers(self, hour, state):
        sc = state.scratch
        self.l1.triggers(hour, state)  # maintains sc['uni']
        rate = float(hour.slots_per_s) if hour.n else 0.0
        return [t for t in self._g4(hour, sc, rate) if t[2].cell == "G0"]


def make_rule(aux_dir=None):
    return P9Rule(aux_dir)
