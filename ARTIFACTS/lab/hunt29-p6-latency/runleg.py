"""P6 wrapper: arena_screen.main with send=160,000 and the deciding entry leg LEG (seconds). Nothing else changes.
LATS and LAT_DECIDING are module globals read at call time; the hour loaders are forked, so they inherit them."""
import os, sys
sys.path.insert(0, os.environ["KIT"])
a = sys.argv[1:]
if "--rule" in a:
    sys.path.insert(0, os.path.dirname(a[a.index("--rule") + 1].split(":")[0]))
from tools import arena_screen as S
leg = float(os.environ["LEG"])
S.LATS = (leg,)
S.LAT_DECIDING = leg
_orig = S.screen_cfg
S.screen_cfg = lambda rent=S.RENT_DECIDING, send_=160000: _orig(rent, send_)
raise SystemExit(S.main(sys.argv[1:]))
