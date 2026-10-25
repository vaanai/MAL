"""drill_hv6_real_layout on a SYNTHETIC tape dir that mimics the real hour-file layout (tape-<YYYY-MM-DDTHH>.jsonl and .jsonl.zst, as tools/arena_feed writes them).
No real tape, no API, no RPC. Nothing here says anything about profit."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import drill_hv6_item6 as D
from tools import drill_hv6_real_layout as RL
from tools import hv6_cards as H

MIN = 60_000
HOUR = 3_600_000


def shifted_rows(day: str) -> list[dict]:
    delta = RL.day_ms(day) + 6 * HOUR - D.T0  # the fixture's T0 is an hour boundary; keep it on one
    rows = D.trade_rows()
    for r in rows:
        r["t_recv_ms"] += delta
        if "event_ts" in r:
            r["event_ts"] += delta // 1000
    return rows


def write_layout(d: Path, rows: list[dict], zst_hours: int = 2) -> None:
    d.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(D._hour_name(r["t_recv_ms"]), []).append(json.dumps(r))
    for i, (name, lines) in enumerate(sorted(by.items())):
        f = d / name
        f.write_text("\n".join(lines) + "\n")
        if i < zst_hours and shutil.which("zstd"):
            subprocess.run(["zstd", "-q", "--rm", str(f)], check=True)  # closed hours are compacted to .jsonl.zst, as arena_feed does


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    real = str(tmp_path.resolve())
    monkeypatch.setattr(H, "READ_ROOTS", (real,))
    monkeypatch.setattr(H, "WRITE_ROOTS", (real,))
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    tape = tmp_path / "tape"
    monkeypatch.setattr(RL, "REAL_TAPE", str(tape))
    return tape


def test_day_list_refusals():
    assert RL.parse_days("2026-10-21,2026-10-22") == ["2026-10-21", "2026-10-22"]
    for bad in ("", "2026-10-19", "2026-10-25", "2026-10-24,2026-10-25", "2026-10-21,2026-10-23", "2026-10-22,2026-10-21", "2026-10-21,2026-10-21", "10-21", "2026-10-1x",
                "2026-09-25", "2026-10-21; rm"):
        with pytest.raises(RL.Refused):
            RL.parse_days(bad)


def test_hour_scan_and_filter_and_refusals(tmp_path, _sandbox):
    tape = _sandbox
    write_layout(tape, shifted_rows("2026-10-21"))
    for junk in ("tape-2026-10-19T23.jsonl", "tape-2026-10-25T00.jsonl", "tape-2026-10-25T00.jsonl.zst", "tape-2026-10-22T00.jsonl", "notes.txt"):
        (tape / junk).write_text("{}\n")
    info = RL.hour_scan(tape, ["2026-10-21"])
    assert info["hours_expected"] == 24 and info["hours_present"] == 5 and info["hours_missing"] == 19
    base = tmp_path / "drill"
    base.mkdir()
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    rd = RL.RealDrill(base, tape, "a" * 40, ["2026-10-21"])
    keys = [k for k, _, _ in rd.new_run("f").state._hour_files()]
    assert keys and all(k.startswith("2026-10-21T") for k in keys)  # 10-19, 10-22 and 10-25 hours are never listed
    with pytest.raises(RL.Refused):
        RL.run_real(["2026-10-21"], "x", "a" * 40, tape_dir=str(tmp_path / "other"), base=base)
    with pytest.raises(RL.Refused):
        RL.run_real(["2026-10-21"], "../x", "a" * 40, base=base)


def test_two_runs_equal_md5_prints_counts_only(tmp_path, _sandbox, capsys):
    tape = _sandbox
    rows = shifted_rows("2026-10-21")
    write_layout(tape, rows)
    base = tmp_path / "drill" / "t1"
    base.mkdir(parents=True)
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    t0 = RL.day_ms("2026-10-21") + 6 * HOUR
    res = RL.run_real(["2026-10-21"], "t1", "a" * 40, base=base, _window=(t0 + 9 * MIN + 2000, t0 + 70 * MIN))
    out = capsys.readouterr().out
    assert res["result"] == "PASS" and res["md5_equal"] and res["run1_log_md5"] == res["run2_log_md5"]
    assert res["run1"]["cards"] > 0 and sum(res["run1"]["entries"].values()) > 0 and res["run1"]["scans"] > 0 and res["peak_rss_mb"] > 0
    assert res["run1"]["scan_ms_p95"] >= res["run1"]["scan_ms_p50"] >= 0
    pools = {r["pool"] for r in rows if "pool" in r} | {r["mint"] for r in rows if "mint" in r}
    assert not any(p in out for p in pools)  # no pool or mint ever printed
    for line in out.splitlines():
        json.loads(line)
