"""P3 wrapper: arena_screen.main with (1) send=160,000 (DEC-031 Am.8), (2) the p3exits exit dispatcher, (3) a per-trade dump (npz per cell) written before the kit's results.
Env: KIT = kit worktree, P3 = this directory, P3_OUT = dump directory."""
import os, sys
sys.path.insert(0, os.environ["KIT"])
sys.path.insert(0, os.environ["P3"])
import numpy as np
from tools import arena_screen as S
import p3exits

send = int(os.environ.get("SEND", "160000"))
_orig_cfg = S.screen_cfg
S.screen_cfg = lambda rent=S.RENT_DECIDING, send_=send: _orig_cfg(rent, send_)
p3exits.install()

_orig_results = S.Screen.results


def results(self):
    out = os.environ.get("P3_OUT")
    if out and not self.count_only:
        os.makedirs(out, exist_ok=True)
        for ci, cell in enumerate(self.cells):
            if not self.acc[ci]:
                continue
            st = S._cat(self.acc[ci])
            L = S.legs(st, S.STAKE_SOL * 1e9, S.LAT_DECIDING, "total", self.cfg[S.RENT_DECIDING])
            np.savez_compressed(f"{out}/trades_{cell.name}.npz", flat=L["flat"], press=L["press"], filled=L["filled"], strand=L["strand"], blk=st["blk"], T=st["T"],
                                sd=st["sd"], xs=st["xs"], why=st["why"], pool=st["pool"])
    return _orig_results(self)


S.Screen.results = results
raise SystemExit(S.main(sys.argv[1:]))
