"""L3 wrapper: run tools/arena_screen.main with ONLY the send changed (SEND env, default 160000). No rule change."""
import os, sys
sys.path.insert(0, os.environ["KIT"])
a=sys.argv[1:]
if "--rule" in a:
    sys.path.insert(0, os.path.dirname(a[a.index("--rule")+1].split(":")[0]))
from tools import arena_screen as S
send = int(os.environ.get("SEND", "160000"))
_orig = S.screen_cfg
S.screen_cfg = lambda rent=S.RENT_DECIDING, send_=send: _orig(rent, send_)
raise SystemExit(S.main(sys.argv[1:]))
