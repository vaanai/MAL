"""scratch registration copied from scripts/research/floor_replay_proof.py (branch claude/floor-replay-proof, 20e02a2c), unchanged."""
from tools import arena_rules as R
from tools import floor_lane_host as H
from tools.arena_rules_v2 import load_rules_v2
BOT = H.BOT_ID

def scratch_register(registry: str, from_ms: int) -> None:
    """Register l1-floor-v2 in a SCRATCH registry under the output root, with the registration instant set just before the replayed clock (a replay cannot register in the real
    clock's past; the test-only override is allowed only when MAL_ARENA_TEST=1, which is set here for this scratch registry alone; the real registry is never opened)."""
    import os
    if R._inside(registry, R.ARENA_OUTPUT_ROOT):
        raise R.HostError("refusing to register inside the arena output root")
    os.environ["MAL_ARENA_TEST"] = "1"
    try:
        R.register_rules(load_rules_v2([BOT]), registry, H.FROZEN_SHA, from_ms, now_ms=from_ms - 1, verify_sha=False)
    finally:
        os.environ.pop("MAL_ARENA_TEST", None)

