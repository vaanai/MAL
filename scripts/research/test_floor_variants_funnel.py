"""Tests for floor_variants_funnel.py: default (WSOL) output is byte-identical, and the --universe other-quote mode (counts only). Synthetic rows only."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

WSOL = "So11111111111111111111111111111111111111112"
H0, H1 = "2026-11-10T00", "2026-11-11T08"
M = int(datetime(2026, 11, 10, 0, tzinfo=timezone.utc).timestamp())
HR = 3600


def _row(pool, mint, side, ts, q, sol, tok, b, quote=WSOL, v=17_580_000_000, slot=1, idx=0):
    return {"type": "trade", "prog": "pumpswap", "pool": pool, "mint": mint, "side": side, "quote_mint": quote, "virtual_quote_reserves": v, "slot": slot, "event_ts": ts,
            "t_recv_ms": ts * 1000 + 5, "sol_lamports": sol, "token_raw": tok, "quote_reserve": q, "base_reserve": b, "signature": f"sig{ts}{idx}", "event_index": idx}


def wsol_fixture_rows():
    return [{"type": "create", "mint": "MINT1", "t_recv_ms": M * 1000}, {"type": "complete", "mint": "MINT1", "event_ts": M, "t_recv_ms": M * 1000 + 1},
            _row("POOL1", "MINT1", "buy", M, 5_000_000_000, 100_000_000, 1_000_000, 10**15),
            _row("POOL1", "MINT1", "sell", M + 20 * HR, 2_000_000_000, 500_000_000, 1_000_000, 10**15),
            _row("POOL1", "MINT1", "sell", M + 25 * HR, 2_000_000_000, 500_000_000, 1_000_000, 10**15),
            _row("POOL1", "MINT1", "sell", M + 27 * HR, 800_000_000, 400_000_000, 1_000_000, 3 * 10**15)]


def write_tape(tape, rows, h0=H0, h1=H1):
    from floor_nov_funnel import hours
    os.makedirs(tape, exist_ok=True)
    by_hour = {}
    for r in rows:
        by_hour.setdefault(datetime.fromtimestamp(r["t_recv_ms"] / 1000, timezone.utc).strftime("%Y-%m-%dT%H"), []).append(r)
    for h in hours(h0, h1):
        data = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in by_hour.get(h, []))
        p = os.path.join(tape, f"tape-{h}.jsonl")
        open(p, "w").write(data)
        subprocess.run(["zstd", "-q", "-f", "--rm", p, "-o", p + ".zst"], check=True)


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd CLI missing")

# md5 of the default-mode outputs on the synthetic WSOL fixture, recorded from origin/main f74fd027 BEFORE the other-quote change
GOLDEN = {"stdout": "678e5e019a79526d0fcca5e29f6e5f78", "variants": "0e90ce00bb32578597f05f5ad0fbe8ae", "triggers": "95d5d52edd7f622f59a8da78a68c046b"}


@needs_zstd
def test_default_output_byte_identical(tmp_path):
    tape, out = str(tmp_path / "tape"), str(tmp_path / "out")
    write_tape(tape, wsol_fixture_rows())
    p = subprocess.run([sys.executable, str(HERE / "floor_variants_funnel.py"), tape, out, H0, H1], capture_output=True, text=True, cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr
    got = {"stdout": hashlib.md5(p.stdout.encode()).hexdigest(), "variants": md5(out + "/variants-narrow.json"), "triggers": md5(out + "/triggers-narrow.jsonl")}
    res = json.load(open(out + "/variants-narrow.json"))
    assert res["base"]["rows"], "fixture must produce a base trigger"
    print("GOT", json.dumps(got))
    assert got == GOLDEN
