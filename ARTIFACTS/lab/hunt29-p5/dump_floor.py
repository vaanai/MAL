"""P5 STACK: run tools/arena_screen (kit at KIT, send 160000) for FLOOR v2 and dump per-trade arrays + pnl at several stakes. Exploration blocks only."""
import os, sys, numpy as np
sys.path.insert(0, os.environ["KIT"])
sys.path.insert(0, os.path.dirname(sys.argv[sys.argv.index("--rule")+1].split(":")[0]))
from tools import arena_screen as S
OUT = os.environ["P5OUT"]
_o = S.screen_cfg
S.screen_cfg = lambda rent=S.RENT_DECIDING, send_=160000: _o(rent, send_)
_legs = S.legs
STAKES = [0.05, 0.1, 0.15, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
done = [False]
def legs(st, stake, lat, rule, cfg, intercept=None):
    r = _legs(st, stake, lat, rule, cfg, intercept)
    if not done[0] and lat == 1.9 and rule == "total" and abs(stake - 0.25e9) < 1 and cfg.rent == 0:
        done[0] = True
        d = {k: st[k] for k in ("T", "xs", "sd", "why", "pool", "blk") if k in st}
        for s in STAKES:
            x = _legs(st, s * 1e9, lat, rule, cfg, None)
            d[f"flat_{s}"] = x["flat"]; d[f"press_{s}"] = x["press"]; d[f"filled_{s}"] = x["filled"]; d[f"strand_{s}"] = x["strand"]
        d["Qe"] = st["Qe"][lat]
        np.savez_compressed(os.path.join(OUT, "floor_trades.npz"), **d)
        print("dumped", len(st["T"]), flush=True)
    return r
S.legs = legs
raise SystemExit(S.main(sys.argv[1:]))
