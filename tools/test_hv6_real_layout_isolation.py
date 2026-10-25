"""The real-layout drill runs run1 and run2 in separate processes (job #1596 held both in one heap and was OOM-killed). Fakes only: no real tape, no subprocess of the runner."""

from __future__ import annotations

import json

from tools import drill_hv6_real_layout as RL
from tools.test_hv6_real_layout import _sandbox, shifted_rows, write_layout  # noqa: F401  (_sandbox is autouse)


def _fake(tag, rss):
    return {"log_md5": "m", "cards_md5": "c", "entries": {"L": 1}, "closes": 1, "positions": 1, "open_at_end": 0, "refusals_by_class": {}, "counters": {}, "cards": 3,
            "scans": 5, "scan_ms_p50": 1.0, "scan_ms_p95": 2.0, "peak_rss_mb": rss}


def test_main_path_isolates_each_run_and_reports_the_larger_peak(tmp_path, _sandbox, monkeypatch, capsys):
    write_layout(_sandbox, shifted_rows("2026-10-21"))
    base = tmp_path / "drill" / "iso"
    base.mkdir(parents=True)
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    tags = []
    monkeypatch.setattr(RL, "_isolated_run", lambda tag, days, did, sha, b: (tags.append(tag), _fake(tag, 1000.0 if tag == "real-a" else 2500.0))[1])
    monkeypatch.setattr(RL, "one_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the parent process must not hold a replay")))
    res = RL.run_real(["2026-10-21"], "iso", "a" * 40, base=base, isolate=True)
    assert tags == ["real-a", "real-b"] and res["peak_rss_mb"] == 2500.0 and res["md5_equal"] and res["result"] == "PASS"
    capsys.readouterr()


def test_isolated_run_spawns_a_child_with_the_one_run_flag(tmp_path, monkeypatch):
    base = tmp_path / "drill" / "iso2"
    base.mkdir(parents=True)
    seen = {}

    class R:
        returncode = 0

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        (base / "real-a.json").write_text(json.dumps(_fake("real-a", 7.0)))
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    out = RL._isolated_run("real-a", ["2026-10-21"], "iso2", "a" * 40, base)
    assert "--one-run" in seen["cmd"] and "real-a" in seen["cmd"] and seen["cmd"][1:3] == ["-m", "tools.drill_hv6_real_layout"] and out["peak_rss_mb"] == 7.0


def test_child_failure_is_a_class_only_error(tmp_path, monkeypatch):
    base = tmp_path / "drill" / "iso3"
    base.mkdir(parents=True)

    class R:
        returncode = 3

    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: R())
    try:
        RL._isolated_run("real-b", ["2026-10-21"], "iso3", "a" * 40, base)
    except RuntimeError as e:
        assert str(e) == "child_real-b_rc3"
    else:
        raise AssertionError("a failing child must fail the drill")
