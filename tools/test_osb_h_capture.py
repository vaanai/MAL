"""Tests for scripts/ops/osb_h_capture.py on synthetic fixtures only."""
import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ops" / "osb_h_capture.py"
spec = importlib.util.spec_from_file_location("osb_h_capture", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PREFIX = "osb_board.py=0343d90f02fde0a79c2bdc62a5c316c5ea97f511"
FROM = "2026-12-02T02:00:00Z"
UNTIL = "2026-12-02T12:00:00Z"
PAIRS = "osb-ce1:osb-a,osb-c:osb-a"
CANARIES = ("SECRET_ROWS_CANARY", "SECRET_PERDAY_CANARY", "SECRET_WINDOWS_CANARY")


def book(bid, state="running", today="2026-12-02"):
    return {"id": bid, "state": state, "today_day": today,
            "last_hour": "2026-12-02T01", "last_hour_end_utc": "2026-12-02T02:00:00Z",
            "wall_time_utc": "2026-12-02T02:01:00Z",
            "rows": "SECRET_ROWS_CANARY", "per_day": ["SECRET_PERDAY_CANARY"],
            "windows": {"w": "SECRET_WINDOWS_CANARY"}}


def write_board(d, gen, books=None, version=PREFIX + ";x=1", md_gen=None):
    d = Path(d)
    d.mkdir(exist_ok=True)
    if books is None:
        books = [book("osb-ce1"), book("osb-a"), book("osb-c")]
    doc = {"generated_utc": gen, "board_version": version,
           "tape_through_utc": gen, "books": books,
           "rows": "SECRET_ROWS_CANARY", "per_day": "SECRET_PERDAY_CANARY"}
    (d / "board.json").write_text(json.dumps(doc))
    (d / "board.md").write_text("# T\n\nx\n\n- Generated %s; foo\n- more\n" % (md_gen or gen))


def run(tmp, now="2026-12-02T03:00:00Z", once=True, pairs=PAIRS, until=UNTIL):
    argv = ["--board-dir", str(tmp / "b"), "--out-dir", str(tmp / "o"),
            "--from", FROM, "--until", until, "--pairs", pairs,
            "--expect-board-version-prefix", PREFIX, "--poll-s", "1",
            "--now-utc", now]
    if once:
        argv.append("--once")
    return mod.main(argv)


def out(tmp, name):
    return tmp / "o" / name


G1 = "2026-12-02T02:15:00Z"
G2 = "2026-12-02T02:30:00Z"


def test_capture_bytes_sha_modes(tmp_path, capsys):
    write_board(tmp_path / "b", G1)
    assert run(tmp_path) == 0
    j = out(tmp_path, "board-H-2026-12-02T021500Z.json")
    m = out(tmp_path, "board-H-2026-12-02T021500Z.md")
    assert j.read_bytes() == (tmp_path / "b" / "board.json").read_bytes()
    assert m.read_bytes() == (tmp_path / "b" / "board.md").read_bytes()
    cap = out(tmp_path, "CAPTURE-osb-ce1_osb-a.txt").read_text()
    assert hashlib.sha256(j.read_bytes()).hexdigest() in cap
    assert hashlib.sha256(m.read_bytes()).hexdigest() in cap
    assert "BOARD_VERSION_DIFFERS" not in cap
    for p in (j, m, out(tmp_path, "CAPTURE-osb-ce1_osb-a.txt")):
        assert stat.S_IMODE(p.stat().st_mode) == 0o444
    assert "CAPTURED osb-ce1_osb-a" in capsys.readouterr().out
    assert not [p for p in (tmp_path / "o").iterdir() if p.name.startswith(".poll-")]


def test_first_wins(tmp_path):
    write_board(tmp_path / "b", G1)
    run(tmp_path)
    first = out(tmp_path, "board-H-2026-12-02T021500Z.json").read_bytes()
    write_board(tmp_path / "b", G2)
    run(tmp_path, now="2026-12-02T04:00:00Z")
    assert out(tmp_path, "board-H-2026-12-02T021500Z.json").read_bytes() == first
    assert not out(tmp_path, "board-H-2026-12-02T023000Z.json").exists()


def test_before_from(tmp_path):
    write_board(tmp_path / "b", "2026-12-02T01:59:59Z")
    run(tmp_path)
    assert not list((tmp_path / "o").glob("CAPTURE-*"))


@pytest.mark.parametrize("state", ["CATCHING UP", "STALE", "starting", "pending",
                                   "ERROR", "REFUSED"])
def test_bad_state(tmp_path, state):
    write_board(tmp_path / "b", G1, [book("osb-ce1", state=state), book("osb-a"),
                                     book("osb-c")])
    run(tmp_path)
    assert not out(tmp_path, "CAPTURE-osb-ce1_osb-a.txt").exists()
    assert out(tmp_path, "CAPTURE-osb-c_osb-a.txt").exists()


def test_old_today_day(tmp_path):
    write_board(tmp_path / "b", G1, [book("osb-ce1", today="2026-11-30"), book("osb-a"),
                                     book("osb-c")])
    run(tmp_path)
    assert not out(tmp_path, "CAPTURE-osb-ce1_osb-a.txt").exists()


def test_missing_book(tmp_path):
    write_board(tmp_path / "b", G1, [book("osb-a"), book("osb-c")])
    run(tmp_path)
    assert not out(tmp_path, "CAPTURE-osb-ce1_osb-a.txt").exists()
    assert out(tmp_path, "CAPTURE-osb-c_osb-a.txt").exists()


def test_one_build_two_pairs(tmp_path):
    write_board(tmp_path / "b", G1)
    run(tmp_path)
    assert len(list((tmp_path / "o").glob("board-H-*.json"))) == 1
    assert len(list((tmp_path / "o").glob("board-H-*.md"))) == 1
    assert len(list((tmp_path / "o").glob("CAPTURE-*.txt"))) == 2
    assert out(tmp_path, "CAPTURE-osb-c_osb-a.txt").exists()


def test_md_json_mismatch(tmp_path, capsys):
    write_board(tmp_path / "b", G1, md_gen=G2)
    run(tmp_path)
    assert "md_json_mismatch" in capsys.readouterr().out
    assert not list((tmp_path / "o").glob("CAPTURE-*"))
    assert not list((tmp_path / "o").glob("board-H-*"))


def test_not_decidable_only_missing_pair(tmp_path):
    write_board(tmp_path / "b", G1, [book("osb-ce1", state="STALE"), book("osb-a"),
                                     book("osb-c")])
    run(tmp_path, now="2026-12-02T12:00:01Z")
    nd = out(tmp_path, "NOT_DECIDABLE-osb-ce1_osb-a.txt")
    assert nd.exists() and not out(tmp_path, "NOT_DECIDABLE-osb-c_osb-a.txt").exists()
    t = nd.read_text()
    assert "no qualifying build by %s (QP-RULING-1127 R4)" % UNTIL in t
    assert "state=STALE" in t
    assert stat.S_IMODE(nd.stat().st_mode) == 0o444
    assert out(tmp_path, "CAPTURE-osb-c_osb-a.txt").exists()


def test_no_not_decidable_before_until(tmp_path):
    write_board(tmp_path / "b", G1, [book("osb-a")])
    run(tmp_path, now="2026-12-02T05:00:00Z")
    assert not list((tmp_path / "o").glob("NOT_DECIDABLE-*"))


def test_version_differs(tmp_path, capsys):
    write_board(tmp_path / "b", G1, version="osb_board.py=deadbeef")
    run(tmp_path)
    assert "BOARD_VERSION_DIFFERS" in out(tmp_path, "CAPTURE-osb-ce1_osb-a.txt").read_text()
    assert "BOARD_VERSION_DIFFERS" in capsys.readouterr().out


def test_field_filter(tmp_path, capsys):
    write_board(tmp_path / "b", G1)
    run(tmp_path)
    write_board(tmp_path / "b", "2026-12-02T01:00:00Z")
    run(tmp_path, now="2026-12-02T12:30:00Z")
    text = capsys.readouterr().out
    text += out(tmp_path, "state-log.jsonl").read_text()
    for p in (tmp_path / "o").glob("CAPTURE-*"):
        text += p.read_text()
    for p in (tmp_path / "o").glob("NOT_DECIDABLE-*"):
        text += p.read_text()
    for c in CANARIES:
        assert c not in text
    for k in ("per_day", "windows", "rows", "vs_control"):
        assert k not in text
    # r7_fields drops everything else
    top, books = mod.r7_fields({"generated_utc": "g", "rows": 1,
                                "books": [{"id": "x", "rows": 1, "state": "s"}]})
    assert set(top) == set(mod.TOP_FIELDS) and set(books[0]) == set(mod.BOOK_FIELDS)


def test_state_log_dedup_and_pair_books_only(tmp_path):
    write_board(tmp_path / "b", "2026-12-02T01:00:00Z",
                [book("osb-a"), book("other")])
    run(tmp_path)
    run(tmp_path, now="2026-12-02T03:05:00Z")
    lines = out(tmp_path, "state-log.jsonl").read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert [b["id"] for b in rec["books"]] == ["osb-a"]
    assert rec["poll_utc"] == "2026-12-02T03:00:00Z"


def test_once_exits_after_one_poll(tmp_path, capsys):
    write_board(tmp_path / "b", "2026-12-02T01:00:00Z")
    assert run(tmp_path, once=True, now="2026-12-02T03:00:00Z") == 0
    assert capsys.readouterr().out.count("poll ") == 1


def test_loop_ends_when_all_captured(tmp_path, capsys):
    write_board(tmp_path / "b", G1)
    assert run(tmp_path, once=False) == 0
    assert capsys.readouterr().out.count("poll ") == 1


def test_bad_args_and_unreadable_dir(tmp_path):
    assert mod.main(["--board-dir", "x"]) == 2
    assert run(tmp_path) == 2  # no board dir
    write_board(tmp_path / "b", G1)
    assert run(tmp_path, pairs="nonsense") == 2
