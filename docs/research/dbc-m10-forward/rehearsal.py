"""Dress rehearsal of tools/dbc_fwd_read.py on the EXPLORATION hours 2026-10-14T00..10-17T00 (the only hours where D1 m10 may still be run): the window constants are patched, the
time gate is passed a late `now`, the marker is a temp file. Proves the full path (scan, config map, Swap2+Swap3 mix, price, verdict) end to end. NOT part of the registered read."""
import sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, ".")
from tools import dbc_fwd_read as R
from tools import dbc_price as L

L.FWD_START = datetime(2026, 10, 14, tzinfo=timezone.utc)
L.FWD_END = datetime(2026, 10, 17, tzinfo=timezone.utc)
out = Path(sys.argv[1])
mk = tempfile.mkdtemp() + "/marker"
rc = R.run(out, root="/data2/mal/venue-tape-backfill/meteora_dbc", now=datetime(2026, 11, 1, 1, tzinfo=timezone.utc), marker=mk, fetch=lambda ks: {})
sys.exit(rc)
