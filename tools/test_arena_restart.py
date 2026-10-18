"""Review item 1: a process restart or crash leaves a process_restart data_loss gap row. Includes a real kill -9. No network."""

from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools import arena_feed as af
from tools.arena_state import ArenaState
from tools.tape_lines import scan_file

REPO = Path(__file__).resolve().parent.parent
T = 1_790_316_000_000
NAME = af.TapeWriter.name_for(T)  # the hour file of T


def line(**kw) -> str:
    return json.dumps({"v": 1, "prog": "pumpfun", "slot": 1, "signature": None, "event_index": None, **kw}) + "\n"


def tape_rows(d: Path) -> list[dict]:
    out = []
    for f in sorted(d.glob("tape-*.jsonl")):
        for ln in f.read_text().splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                out.append({"type": "BAD"})
    return out


# ---- read_previous_run ------------------------------------------------------------------------------------------------
def test_first_run_has_no_previous(tmp_path: Path) -> None:
    assert af.read_previous_run(tmp_path) is None


def test_previous_run_from_the_tape_tail(tmp_path: Path) -> None:
    (tmp_path / NAME).write_text(line(type="start", t_recv_ms=T) + line(type="hb", t_recv_ms=T + 60_000))
    assert af.read_previous_run(tmp_path) == {"last_ms": T + 60_000, "clean_stop": False, "torn_tail": False, "prev_pid": None}
    with (tmp_path / NAME).open("a") as fh:
        fh.write(line(type="stop", t_recv_ms=T + 90_000))
    assert af.read_previous_run(tmp_path)["clean_stop"] is True


def test_a_torn_last_line_is_skipped_and_flagged(tmp_path: Path) -> None:
    (tmp_path / NAME).write_text(line(type="hb", t_recv_ms=T + 5) + '{"v":1,"type":"tr')
    r = af.read_previous_run(tmp_path)
    assert r["last_ms"] == T + 5 and r["torn_tail"] is True and r["clean_stop"] is False


def test_the_status_file_counts_when_it_is_newer(tmp_path: Path) -> None:
    (tmp_path / NAME).write_text(line(type="hb", t_recv_ms=T))
    (tmp_path / af.STATUS_NAME).write_text(json.dumps({"updated_ms": T + 30_000, "pid": 4242}))
    assert af.read_previous_run(tmp_path) == {"last_ms": T + 30_000, "clean_stop": False, "torn_tail": False, "prev_pid": 4242}
    only_status = tmp_path / "x"
    only_status.mkdir()
    (only_status / af.STATUS_NAME).write_text(json.dumps({"updated_ms": T}))  # every hour already compacted: only the status file is left
    assert af.read_previous_run(only_status)["last_ms"] == T


def test_the_writer_ends_a_torn_line_instead_of_fusing_it_with_the_next_row(tmp_path: Path) -> None:
    (tmp_path / NAME).write_text(line(type="hb", t_recv_ms=T) + '{"v":1,"type":"tr')
    w = af.TapeWriter(tmp_path)
    assert w.write({"v": 1, "type": "hb", "prog": "all", "slot": 0, "t_recv_ms": T + 1000, "signature": None, "event_index": None}) is True
    w.close()
    c = scan_file(tmp_path / NAME)
    assert (c.lines, c.bad, c.not_json) == (3, 1, 1) and w.torn_lines == 1  # one bad line (the torn one); the new row is intact


# ---- run_live in process: clean stop, then a simulated crash -------------------------------------------------------------------
def run_once(d: Path, monkeypatch: pytest.MonkeyPatch, rows: int = 1) -> None:
    async def fake_run(self, max_seconds=None):
        for i in range(rows):
            self.emit_safe(af._row("trade", "pumpfun", 1, self.wall(), f"s{i}", 0, {}))

    monkeypatch.setattr(af.Feed, "run", fake_run)
    args = af.build_parser().parse_args(["--out-dir", str(d), "--no-resolve", "--no-compress", "--min-free-pct", "0"])
    assert asyncio.run(af.run_live(args)) == 0


def test_every_restart_writes_a_process_restart_gap_and_a_torn_tail_is_repaired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_once(tmp_path, monkeypatch)
    assert [r["type"] for r in tape_rows(tmp_path)] == ["start", "trade", "stop"]  # a first run: no previous, no gap
    run_once(tmp_path, monkeypatch)
    types = [r["type"] for r in tape_rows(tmp_path)]
    assert types == ["start", "trade", "stop", "start", "gap", "trade", "stop"]
    g = [r for r in tape_rows(tmp_path) if r["type"] == "gap"][0]
    assert g["kind"] == "process_restart" and g["prog"] == "all" and g["data_loss"] is True and g["clean_stop"] is True and g["torn_tail"] is False
    assert g["from_ms"] <= g["to_ms"] and g["count"] == 1
    # a crash: the last process wrote no stop row and died in the middle of a line
    f = next(tmp_path.glob("tape-*.jsonl"))
    kept = f.read_text().splitlines(keepends=True)[:-1]  # drop the stop row
    f.write_text("".join(kept) + '{"v":1,"type":"tr')
    run_once(tmp_path, monkeypatch)
    gaps = [r for r in tape_rows(tmp_path) if r.get("type") == "gap"]
    assert len(gaps) == 2 and gaps[1]["clean_stop"] is False and gaps[1]["torn_tail"] is True
    c = scan_file(f)
    assert c.bad == 1  # only the torn line itself
    st = ArenaState(tmp_path, start_ms=0)
    st.poll()
    assert st.gaps_total["all"] == 2 and st.counters["bad_lines"] == 1


# ---- a real kill -9 of a real feed process -------------------------------------------------------------------------------------
def spawn(d: Path) -> subprocess.Popen:
    # A closed local port as the endpoint: no network is used; the sockets just fail and back off. min-free-pct 0: this host is under the real reserve.
    return subprocess.Popen([sys.executable, "-m", "tools.arena_feed", "--out-dir", str(d), "--ws-url", "wss://127.0.0.1:9", "--no-resolve", "--no-compress",
                             "--min-free-pct", "0"], cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for(pred, timeout: float = 30.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_kill_9_mid_hour_then_a_second_feed_writes_the_gap(tmp_path: Path) -> None:
    p1 = spawn(tmp_path)
    try:
        assert wait_for(lambda: any(r.get("type") == "start" for r in tape_rows(tmp_path))), "first feed never wrote its start row"
        p1.send_signal(signal.SIGKILL)
        p1.wait(timeout=10)
    finally:
        if p1.poll() is None:
            p1.kill()
    f = next(tmp_path.glob("tape-*.jsonl"))
    with f.open("a") as fh:
        fh.write('{"v":1,"type":"tr')  # the kill landed mid-line
    t_kill = int(time.time() * 1000)
    p2 = spawn(tmp_path)
    try:
        assert wait_for(lambda: any(r.get("kind") == "process_restart" for r in tape_rows(tmp_path))), "second feed wrote no process_restart gap"
        p2.send_signal(signal.SIGTERM)
        assert p2.wait(timeout=30) == 0
    finally:
        if p2.poll() is None:
            p2.kill()
    rows = tape_rows(tmp_path)
    (gap,) = [r for r in rows if r.get("kind") == "process_restart"]
    first_start = next(r for r in rows if r.get("type") == "start")
    assert gap["data_loss"] is True and gap["clean_stop"] is False and gap["torn_tail"] is True
    assert gap["from_ms"] == first_start["t_recv_ms"] and first_start["t_recv_ms"] <= gap["to_ms"] and gap["to_ms"] >= t_kill - 1000
    assert [r["type"] for r in rows].count("start") == 2 and rows[-1]["type"] == "stop" and scan_file(f).bad == 1
