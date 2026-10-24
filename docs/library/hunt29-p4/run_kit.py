"""Run arena_screen with send=160,000 (DEC-031 Am.8). Usage: python run_kit.py <kit args>. The kit is imported from the p4-screens worktree (origin/main 91544819)."""
import sys
sys.path.insert(0, "/data2/mal/wt/p4-screens")
sys.path.insert(0, "/data/mal/hunt-1008/push-1016/hunt29/p4")
import tools.arena_screen as S
_orig = S.screen_cfg
S.screen_cfg = lambda rent=S.RENT_DECIDING, send=160_000: _orig(rent, send)
if __name__ == "__main__":
    sys.argv[0] = "arena_screen"
    raise SystemExit(S.main(sys.argv[1:]))
