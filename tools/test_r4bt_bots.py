"""Tests of the r4-b-T per-bot table (tools/r4bt_bots.py), the pricer wrapper and the --bot-id flags. Synthetic only; no tape, no ledger, no network."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools import r4bt_a3 as A3
from tools import r4bt_attempts as ATT
from tools import r4bt_bots as B
from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools import r4bt_price_bot as PB
from tools import r4bt_read as R

REPO = Path(__file__).resolve().parent.parent
# blobs at origin/main 0982c931 of the files the C0 twin and Amendment 30 pin: this change must not move them
PINNED_BLOBS = {
    "tools/r4bt_gate.py": "2c094ad860c1b838399e4274ca7db3ae837f6d9e",
    "tools/r4bt_price.py": "7ed62b1f43ed58da862a56a5bd28a6167136cb39",
    "tools/r4bt_attempts.py": "fd7fccca08377f8e53e7050baf5cf690fba620c2",
}


def test_a1_entry_is_the_gate_constants():
    assert G.BOT_ID == "r4bt-a1" == B.DEFAULT_BOT_ID
    assert B.spec("r4bt-a1")["clock_ms"] == G.CLOCK_MS == 1_794_031_200_000
    assert B.p0_days_problem("r4bt-a1", ["2026-11-04", "2026-11-05", "2026-11-06"]) is None
    B.check_clock("r4bt-a1", G.CLOCK_MS)
    with pytest.raises(G.Refused):
        B.check_clock("r4bt-a1", 1_795_132_800_000)


def test_a2_entry():
    s = B.spec("r4bt-a2")
    assert s["p0_days"] == ("2026-11-16", "2026-11-17", "2026-11-18")
    assert s["clock_ms"] == 1_795_132_800_000 == B.day_ms("2026-11-20")
    assert s["p0_count_job_ms"] == B.day_ms("2026-11-19") + 10 * 60_000
    assert s["screen_boundary_min_ms"] == B.day_ms("2026-11-25")
    assert s["counted_end_h14_ms"] == s["clock_ms"] + 14 * B.DAY_MS == B.day_ms("2026-12-04")
    assert B.p0_days_problem("r4bt-a2", ["2026-11-16", "2026-11-17", "2026-11-18"]) is None
    assert B.p0_days_problem("r4bt-a2", ["2026-11-04", "2026-11-05", "2026-11-06"]) is not None  # a1's days are not a2's
    B.check_clock("r4bt-a2", 1_795_132_800_000)
    with pytest.raises(G.Refused):
        B.check_clock("r4bt-a2", G.CLOCK_MS)
    # the first P0 day is exactly 60 h after 2026-11-13T12:00Z, and the 3 days are consecutive
    assert B.day_ms("2026-11-16") - 60 * 3_600_000 == B.day_ms("2026-11-13") + 12 * 3_600_000
    assert [B.day_ms(d) for d in s["p0_days"]] == [B.day_ms("2026-11-16") + i * B.DAY_MS for i in range(3)]
    assert B.day_ms("2026-11-25") + B.T_MAX_S * 1000 == B.day_ms("2026-11-27") + 3_660_000  # 2026-11-27T01:01:00Z


def test_p0_floor_and_h_rule():
    assert B.p0_floor_ok({"a": 2, "b": 2, "c": 2})
    assert not B.p0_floor_ok({"a": 6, "b": 0, "c": 0})  # no zero day
    assert not B.p0_floor_ok({"a": 1, "b": 1, "c": 1})  # mean below 2.0
    assert not B.p0_floor_ok({})
    assert B.h_days(0, 3) is None
    assert B.h_days(33, 3) == 14 and B.h_days(32, 3) == 15  # H = 14 iff the 3-day total is at least 33
    assert B.h_days(1, 3) == 90  # capped
    for total in range(0, 400):  # the table's H is the pinned counter's H
        assert B.h_days(total, 3) == ATT.h_days(total, 3)
    assert B.P0_FLOOR_MEAN == ATT.P0_FLOOR_MEAN == 2.0
    assert (B.H_MIN_DAYS, B.H_MAX_DAYS, B.H_NUM) == (ATT.H_MIN_DAYS, ATT.H_MAX_DAYS, ATT.H_NUM)


def test_unknown_bot_refused(tmp_path):
    for bad in ("r4bt-a3", "", None, "r4bt-a1 "):
        with pytest.raises(G.Refused):
            B.spec(bad)
    assert PB.main(["--bot-id", "r4bt-a3"]) == 2
    assert PB.main(["--bot-id"]) == 2
    missing = str(tmp_path / "none")
    argv = ["apply", "--verdict", missing, "--registry", missing, "--applied-log", missing]
    assert A3.main(argv + ["--bot-id", "r4bt-a3"]) == 2


def test_read_parser_defaults_to_a1_and_takes_a2():
    base = ["--ledger-root", "l", "--registry", "r", "--restarts", "x", "--boundary-file", "b", "--boundary-sha256", "s",
            "--arena-paper-sha256", "p", "--arena-score-sha256", "q", "--health", "h", "--health-sha", "h", "--as-of-ms", "1"]
    assert R.build_parser().parse_args(base).bot_id == "r4bt-a1"
    assert R.build_parser().parse_args(base + ["--bot-id", "r4bt-a2"]).bot_id == "r4bt-a2"


def test_pricer_wrapper_binds_bot_id_and_restores(monkeypatch):
    seen = {}

    def fake_main(args, **kw):
        seen["args"] = list(args)
        seen["price"] = P.price
        return 0

    orig = P.price
    monkeypatch.setattr(P, "main", fake_main)
    assert PB.main(["--bot-id", "r4bt-a2", "--k-rounding", "ceil"]) == 0
    assert seen["args"] == ["--k-rounding", "ceil"]
    assert seen["price"].keywords == {"bot_id": "r4bt-a2"} and seen["price"].func is orig
    assert P.price is orig  # restored
    assert PB.main(["--k-rounding", "ceil"]) == 0
    assert seen["price"].keywords == {"bot_id": "r4bt-a1"}  # default is a1


def test_pinned_blobs_unchanged():
    """gate, pricer and attempts counter keep their blobs (the C0 twin and Amendment 30 pin them)."""
    for path, want in PINNED_BLOBS.items():
        got = subprocess.run(["git", "-C", str(REPO), "hash-object", path], capture_output=True, text=True).stdout.strip()
        assert got == want, path
