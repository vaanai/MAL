"""Prints the sha256 table the REGISTRATION pins (R2). Run at the tag commit: python3 docs/research/dbc-m10-forward/pins.py"""
import hashlib, subprocess
from pathlib import Path
root = Path(__file__).resolve().parents[3]
print("commit", subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=root).stdout.strip())
for f in ("tools/dbc_price.py", "tools/dbc_screen.py", "tools/dbc_config.py", "tools/arena_stack.py", "tools/dbc_fwd_read.py", "tools/venue_backfill_dbc.py", "tools/dbc_decode.py", "tools/venue_backfill.py",
          "docs/research/dbc-p17/data/cfg_summary.json", "tools/data/meteora_dbc_idl_v0.2.2.json"):
    print(hashlib.sha256((root / f).read_bytes()).hexdigest(), f)
