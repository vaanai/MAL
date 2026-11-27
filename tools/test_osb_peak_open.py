"""osb_peak_open: counts-only peak open count. Synthetic fixtures only (fake board object, fake pnl positions); no real ledger, book or host path is opened.
Nothing here says any book makes money."""
import json
import re
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "research"))
import osb_peak_open as PO  # noqa: E402
import osb_board as RB  # noqa: E402

H = 3_600_000
D0 = PO.day_start_ms("2026-11-20")
D1 = D0 + PO.DAY_MS
LINE = re.compile(r"^(day=\d{4}-\d{2}-\d{2} book=[a-z0-9-]+ stake=[0-9.]+ peak_open=\d+ positions_opened=\d+ excluded=\d+|max_peak_open=\d+)$")


def pos(t0, t1, status="closed", stake="0.25"):
    return {"pid": "p%d" % t0, "t0": t0, "t1": t1, "status": {str(float(stake)): status}}


class FakeBoard:
    LAG = 90 * 60_000

    def __init__(self, positions, end, found=True, torn=False):
        self.positions, self.end, self.found, self.torn = positions, end, found, torn

    def load_books_file(self, path):
        return ([{"m": {"book_id": "osb-ce1", "stakes_sol": [0.1, 0.25]}}] if self.found else [{"m": {"book_id": "other"}}]), None

    def inspect_book(self, b, now_ms, roots):
        if self.torn:
            raise RuntimeError("book link moved while reading")
        return {"pnl": {"positions": self.positions}, "end": self.end}

    def complete_last_day(self, end):
        return PO.day_str((end - self.LAG) // PO.DAY_MS * PO.DAY_MS - PO.DAY_MS)


def go(capsys, board, first="2026-11-20", last="2026-11-22", book="osb-ce1", stake="0.25"):
    rc = PO.main([book, "--books", "x.json", "--stake", stake, "--from", first, "--to", last], board=board)
    return rc, capsys.readouterr()


def test_day_counts_overlap_carry_in_and_open_end():
    spans = [(D0 - H, D0 + H),        # carried in, exits in the day
             (D0 + H, D0 + 3 * H),    # opens in the day, overlaps the carried one
             (D0 + 2 * H, D0 + 4 * H),  # overlaps the previous one
             (D0 + 3 * H, D0 + 5 * H),  # starts exactly when the second ends: no overlap
             (D1 - H, None)]          # open to the end of data
    assert PO.day_counts(spans, D0) == (2, 4)
    # next day: the open-to-end position is carried in
    assert PO.day_counts(spans, D1) == (1, 0)


def test_carry_in_counts_toward_peak_and_open_end_stays_open():
    spans = [(D0 - 5 * H, D1 + 5 * H), (D0 - H, D0 + H), (D0 + 30 * 60_000, None)]
    assert PO.day_counts(spans, D0) == (3, 1)
    assert PO.day_counts(spans, D1) == (2, 0)


def test_cli_counts_and_format(capsys):
    end = PO.day_start_ms("2026-11-23") + 2 * H
    b = FakeBoard([pos(D0 + H, D0 + 2 * H), pos(D0 + 90 * 60_000, D1 + H), pos(D1 + 2 * H, None, "open_at_tape_end"),
                   pos(D0 + H, D0 + 2 * H, stake="0.1"), pos(D0 + H, None, "engine_excluded")], end)
    rc, cap = go(capsys, b)
    assert rc == 0 and cap.err == ""
    lines = cap.out.splitlines()
    assert all(LINE.match(x) for x in lines)
    assert lines[0] == "day=2026-11-20 book=osb-ce1 stake=0.25 peak_open=2 positions_opened=2 excluded=1"
    assert lines[1] == "day=2026-11-21 book=osb-ce1 stake=0.25 peak_open=1 positions_opened=1 excluded=0"
    assert lines[2] == "day=2026-11-22 book=osb-ce1 stake=0.25 peak_open=1 positions_opened=0 excluded=0"
    assert lines[3] == "max_peak_open=2"


def test_incomplete_day_skipped(capsys):
    end = D1 + 2 * H  # data ends 2h into 11-21: 11-20 is not yet complete (needs 90 min past its end -> it is), 11-21 is not
    rc, cap = go(capsys, FakeBoard([pos(D0 + H, D0 + 2 * H)], end), "2026-11-20", "2026-11-21")
    assert rc == 0
    assert "day=2026-11-21" not in cap.out


@pytest.mark.parametrize("first,last", [("2026-11-26", "2026-11-26"), ("2026-11-25", "2026-11-26"), ("2026-12-01", "2026-12-01"), ("2026-11-20", "2026-12-05")])
def test_ceiling_refused_no_count(capsys, first, last):
    rc, cap = go(capsys, FakeBoard([pos(D0, D0 + H)], D0 + 30 * PO.DAY_MS), first, last)
    assert rc == 2 and cap.out == ""
    assert cap.err.strip() == "osb_peak_open: refused (Refusal)"


def test_ceiling_day_itself_allowed(capsys):
    rc, cap = go(capsys, FakeBoard([pos(D0, D0 + H)], D0 + 30 * PO.DAY_MS), "2026-11-25", "2026-11-25")
    assert rc == 0 and cap.out.splitlines()[0].startswith("day=2026-11-25 ")


@pytest.mark.parametrize("kw", [dict(found=False), dict(torn=True)])
def test_setup_errors_exit_2_class_name_only(capsys, kw):
    rc, cap = go(capsys, FakeBoard([pos(D0, D0 + H)], D0 + 5 * PO.DAY_MS, **kw))
    assert rc == 2 and cap.out == ""
    assert re.fullmatch(r"osb_peak_open: refused \(\w+\)", cap.err.strip())


def test_bad_day_exit_2(capsys):
    rc, cap = go(capsys, FakeBoard([], D0), "20261120", "2026-11-21")
    assert rc == 2 and cap.out == ""


def test_zero_length_span_counts_as_open_at_its_instant():
    spans = [(D0 + H, D0 + H), (D0 + H, D0 + 2 * H)]
    assert PO.day_counts(spans, D0) == (2, 2)
    assert PO.day_counts([(D0 + H, D0 + H)], D0) == (1, 1)
    assert PO.day_counts([(D0, D0)], D0) == (1, 1)


def test_closed_without_exit_is_setup_error(capsys):
    rc, cap = go(capsys, FakeBoard([pos(D0 + H, None)], D0 + 5 * PO.DAY_MS))
    assert rc == 2 and cap.out == "" and cap.err.strip() == "osb_peak_open: refused (Refusal)"


@pytest.mark.parametrize("stake", ["nan", "inf", "1e-1", "0.250", "0.3", "0.4", "0.2500", ".25", "x"])
def test_stake_must_be_exact_manifest_rung(capsys, stake):
    rc, cap = go(capsys, FakeBoard([pos(D0, D0 + H, stake="0.25")], D0 + 5 * PO.DAY_MS), stake=stake)
    assert rc == 2 and cap.out == ""


def test_excluded_not_open_but_counted(capsys):
    b = FakeBoard([pos(D0 + H, D0 + 2 * H), pos(D0 + H, None, "engine_excluded"), pos(D0 + 2 * H, None, "engine_excluded")], D0 + 5 * PO.DAY_MS)
    rc, cap = go(capsys, b, last="2026-11-20")
    assert cap.out.splitlines()[0] == "day=2026-11-20 book=osb-ce1 stake=0.25 peak_open=1 positions_opened=1 excluded=2"


def test_holdout_events_cannot_change_ceiling_day(capsys):
    d25 = PO.day_start_ms("2026-11-25")
    end = d25 + 5 * PO.DAY_MS
    base = [pos(d25 - H, d25 + H), pos(d25 + 20 * H, d25 + 30 * H), pos(d25 + 21 * H, d25 + 22 * H)]
    alt = [pos(d25 - H, d25 + H), pos(d25 + 20 * H, d25 + 24 * H), pos(d25 + 21 * H, d25 + 22 * H)]  # exit exactly at the ceiling
    alt2 = [pos(d25 - H, d25 + H), pos(d25 + 20 * H, None, "open_at_tape_end"), pos(d25 + 21 * H, d25 + 22 * H), pos(d25 + 25 * H, d25 + 26 * H)]  # status change / entry after it
    outs = []
    for ps in (base, alt, alt2):
        rc, cap = go(capsys, FakeBoard(ps, end), "2026-11-24", "2026-11-25")
        assert rc == 0
        outs.append(cap.out)
    assert outs[0] == outs[1] == outs[2]
    assert "day=2026-11-25 book=osb-ce1 stake=0.25 peak_open=2 positions_opened=2 excluded=0" in outs[0]


def test_day_before_data_start_refused(capsys):
    rc, cap = go(capsys, FakeBoard([pos(D0 + H, D0 + 2 * H)], D0 + 5 * PO.DAY_MS), "2026-11-18", "2026-11-20")
    assert rc == 2 and cap.out == ""


def test_real_inspect_book_path_with_running_book(tmp_path, monkeypatch, capsys):
    """real osb_board.inspect_book / book_state / stored_hours; only the pricer, the path guard and the BOOKS.json loader are replaced"""
    from datetime import datetime, timezone
    out = tmp_path / "out"
    (out / "runs" / "r1").mkdir(parents=True)
    (out / "book").symlink_to(out / "runs" / "r1")
    end = D0 + 5 * PO.DAY_MS
    (out / "md5-by-hour.jsonl").write_text(json.dumps({"hour": "h", "end_ms": end}) + "\n")
    (out / "status.json").write_text(json.dumps({"wall_time_utc": datetime.now(timezone.utc).isoformat(), "run": "r1"}))
    (out / "manifest.sha256").write_text("abc\n")
    book = {"m": {"book_id": "osb-ce1", "stakes_sol": [0.25], "out_root": str(out), "send_lamports": 1}, "sha": "abc", "role": "control", "start_ms": D0, "twin_of": None}
    monkeypatch.setattr(RB, "load_books_file", lambda path, roots=None: ([book], None))
    monkeypatch.setattr(RB.TP, "refusal", lambda *a, **k: None)
    monkeypatch.setattr(RB, "price_snapshot", lambda b, run_dir, to_ms: {"positions": [pos(D0 + H, D0 + 2 * H)]})
    rc, cap = go(capsys, RB, last="2026-11-20")
    assert rc == 0 and cap.err == ""
    assert cap.out.splitlines() == ["day=2026-11-20 book=osb-ce1 stake=0.25 peak_open=1 positions_opened=1 excluded=0", "max_peak_open=1"]
