"""P59 wrapper around tools.arena_screen.main. VARIANT env: base | a40 | guardoff. Send 160000. No other change. See PREREG.md."""
import os, sys
KIT = os.environ["KIT"]
sys.path.insert(0, KIT)
sys.path.insert(0, "/data/mal/hunt-1008/push-1016/hunt24/l4-audit")
from tools import arena_screen as S
from tools import arena_stack as F
V = os.environ["VARIANT"]
assert V in ("base", "a40", "guardoff")
_cfg = S.screen_cfg
S.screen_cfg = lambda rent=S.RENT_DECIDING, send_=160000: _cfg(rent, send_)
_load = S.load_rule
def load_rule(spec, kw=None):
    r = _load(spec, kw)
    if V == "a40":
        r.spec = S.ExitSpec(300.0, tp=1.5)   # A40: 300 s wall-clock cap, tp 1.5 kept
    return r
S.load_rule = load_rule
if V == "guardoff":
    _rt = F.round_trip
    F.round_trip = lambda *a, **k: _rt(*a, **{**k, "guard_ratio": float("inf")})
raise SystemExit(S.main(sys.argv[1:]))
