"""Synthetic-fixture tests for tools/arena_tape_dupcount.py (no real tape is read)."""

import json
import shutil
import subprocess

import pytest

from tools import arena_tape_dupcount as D


def _tr(sig, ei, recv, slot=10, sol=5, mint=None):
    return {"v": 1, "type": "trade", "prog": "pumpswap", "slot": slot, "t_recv_ms": recv, "signature": sig, "event_index": ei,
            "venue": "pumpswap", "mint": mint, "pool": "P1", "trader": "W", "side": "buy", "sol_lamports": sol, "token_raw": 7,
            "quote_reserve": 1, "base_reserve": 2, "event_ts": 3}


def _write(path, rows, tail=""):
    path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n" + tail)


@pytest.fixture
def tape(tmp_path):
    h0 = [{"v": 1, "type": "hb", "prog": "pumpswap", "slot": 0, "t_recv_ms": 1, "signature": None, "event_index": None},
          _tr("a", 0, 1), _tr("a", 0, 2), _tr("a", 0, 3, mint="M"), _tr("b", 0, 4), _tr("b", 0, 5, slot=11), _tr("c", 1, 6)]
    _write(tmp_path / "tape-2026-10-01T00.jsonl", h0, "{torn\n")
    _write(tmp_path / "tape-2026-10-01T01.jsonl", [_tr("c", 1, 7), _tr("d", 0, 8), _tr("c", 2, 9)])
    return tmp_path


def _rows(out):
    return {ln.split("\t")[0]: ln.split("\t") for ln in out.splitlines() if ln.startswith("2026-")}


@pytest.mark.parametrize("workers", [1, 2])
def test_counts(tape, capsys, workers):
    assert D.main(["--tape-dir", str(tape), "--workers", str(workers), "--window", "W=2026-10-01T00,2026-10-01T02"]) == 0
    out = capsys.readouterr().out
    r = _rows(out)
    # hour 0: 8 lines, 1 torn, 6 trades; a0 x3 (2 receive-time repeats, 1 of them byte-equal but for t_recv_ms), b0 key collision kept
    assert r["2026-10-01T00"][1:9] == ["8", "1", "6", "3", "2", "1", "0", "0"]
    # hour 1: c1 repeats hour 0's c1 (cross-hour), c2 is a different key
    assert r["2026-10-01T01"][1:9] == ["3", "0", "3", "0", "0", "0", "1", "1"]
    assert any(ln.startswith("W\t") and ln.endswith("OVER") for ln in out.splitlines())


def test_open_hour_skipped(tape, capsys):
    (tape / "tape-2999-01-01T00.jsonl").write_text(json.dumps(_tr("z", 0, 1)) + "\n")
    D.main(["--tape-dir", str(tape), "--workers", "1"])
    assert "2999-01-01T00" not in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd CLI missing")
def test_zst_read_and_jsonl_wins(tape, capsys):
    subprocess.run(["zstd", "-q", "-k", str(tape / "tape-2026-10-01T01.jsonl")], check=True)
    D.main(["--tape-dir", str(tape), "--workers", "1"])
    assert _rows(capsys.readouterr().out)["2026-10-01T01"][1:4] == ["3", "0", "3"]
    (tape / "tape-2026-10-01T01.jsonl").unlink()
    D.main(["--tape-dir", str(tape), "--workers", "1"])
    assert _rows(capsys.readouterr().out)["2026-10-01T01"][1:4] == ["3", "0", "3"]


def test_output_has_no_values(tape, capsys):
    D.main(["--tape-dir", str(tape), "--workers", "1"])
    out = capsys.readouterr().out
    for value in ('"P1"', "\tP1", "\tW\t", "\tM\t"):
        assert value not in out
