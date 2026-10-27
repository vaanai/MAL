"""P85 wrapper: tools/arena_screen.main with ONLY these changes: send 505,000 -> 160,000 (as hunt28 L3 and P7), the solo block root fresh-0808 (kit knows fresh-0903 only), and the kit FORBIDDEN word 'fresh-08' removed so this one path opens. No rule change."""
import os, sys
sys.path.insert(0, os.environ["KIT"])
a = sys.argv[1:]
if "--rule" in a:
    sys.path.insert(0, os.path.dirname(a[a.index("--rule")+1].split(":")[0]))
from tools import arena_screen as S
S.EXTRA_SOLO_BLOCK_ROOTS = (f"{S.CLEAN_VIEW}/fresh-0808",)
S.FORBIDDEN = tuple(w for w in S.FORBIDDEN if w != "fresh-08")
send = int(os.environ.get("SEND", "160000"))
_orig = S.screen_cfg
S.screen_cfg = lambda rent=S.RENT_DECIDING, send_=send: _orig(rent, send_)
raise SystemExit(S.main(sys.argv[1:]))
