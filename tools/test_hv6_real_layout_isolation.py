"""The real-layout drill runs run1 and run2 in separate processes (job #1596 held both in one heap and was OOM-killed). Fakes only: no real tape, no subprocess of the runner."""

from __future__ import annotations

import json

from tools import drill_hv6_real_layout as RL
from tools.test_hv6_real_layout import HOUR, MIN, _sandbox, shifted_rows, write_layout  # noqa: F401  (_sandbox is autouse)


def _st(p95):
    return {"p50": 1.0, "p95": p95, "max": p95 + 1, "n": 10}


def _fake(tag, rss, scan_p95=2.0, tick_p95=5.0, md5="m", cards="c"):
    return {"tick_ms": _st(tick_p95), "scan_tick_ms": _st(tick_p95), "fast_scan": tag != "real-c", "log_md5": md5, "cards_md5": cards, "entries": {"L": 1}, "closes": 1, "positions": 1, "open_at_end": 0, "refusals_by_class": {}, "counters": {}, "cards": 3,
            "scans": 5, "scan_ms_p50": 1.0, "scan_ms_p95": scan_p95, "peak_rss_mb": rss}


def test_main_path_isolates_each_run_and_reports_the_larger_peak(tmp_path, _sandbox, monkeypatch, capsys):
    write_layout(_sandbox, shifted_rows("2026-10-21"))
    base = tmp_path / "drill" / "iso"
    base.mkdir(parents=True)
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    tags = []
    monkeypatch.setattr(RL, "_isolated_run", lambda tag, days, did, sha, b, phantom=True: (tags.append(tag), _fake(tag, 1000.0 if tag == "real-a" else 2500.0))[1])
    monkeypatch.setattr(RL, "one_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the parent process must not hold a replay")))
    res = RL.run_real(["2026-10-21"], "iso", "a" * 40, base=base, isolate=True)
    assert tags == ["real-a", "real-b", "real-c"] and res["peak_rss_mb"] == 2500.0 and res["md5_equal"] and res["result"] == "PASS"
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


def _run(tmp_path, _sandbox, monkeypatch, capsys, fakes, name):
    if not _sandbox.exists() or not any(_sandbox.iterdir()):
        write_layout(_sandbox, shifted_rows("2026-10-21"))
    base = tmp_path / "drill" / name
    base.mkdir(parents=True)
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    monkeypatch.setattr(RL, "_isolated_run", lambda tag, days, did, sha, b, phantom=True: dict(fakes[tag]))
    res = RL.run_real(["2026-10-21"], name, "a" * 40, base=base, isolate=True)
    capsys.readouterr()
    return res


def test_run3_with_fast_scan_off_must_match_run1_md5s_and_its_timing_is_not_tested(tmp_path, _sandbox, monkeypatch, capsys):
    f = {"real-a": _fake("real-a", 1000.0), "real-b": _fake("real-b", 1000.0), "real-c": _fake("real-c", 1000.0)}
    assert _run(tmp_path, _sandbox, monkeypatch, capsys, f, "r3a")["result"] == "PASS"
    slow3 = {**f, "real-c": _fake("real-c", 1000.0, scan_p95=9999.0, tick_p95=9999.0)}   # run 3 is the slow path: its timing is printed, never tested
    res = _run(tmp_path, _sandbox, monkeypatch, capsys, slow3, "r3b")
    assert res["result"] == "PASS" and res["run3_timing_report_only"]["scan_ms_p95"] == 9999.0 and res["scan_p95_ok"]
    for bad in (_fake("real-c", 1000.0, md5="other"), _fake("real-c", 1000.0, cards="other")):
        res = _run(tmp_path, _sandbox, monkeypatch, capsys, {**f, "real-c": bad}, "r3c" + ("m" if bad["log_md5"] == "other" else "k"))
        assert res["result"] == "FAIL" and res["md5_equal_run3_fast_scan_off"] is False and res["md5_equal_run1_run2"] is True


def test_whole_tick_rule_binds_only_when_scan_p95_is_at_least_500(tmp_path, _sandbox, monkeypatch, capsys):
    slow_tick = lambda scan, tick: {t: _fake(t, 1000.0, scan_p95=scan, tick_p95=tick) for t in ("real-a", "real-b", "real-c")}  # noqa: E731
    r = _run(tmp_path, _sandbox, monkeypatch, capsys, slow_tick(499.0, 5000.0), "tk1")     # scan p95 < 500: the whole-tick figures are report-only
    assert r["result"] == "PASS" and r["tick_rule_applies"] is False and r["whole_tick_ms"]["run1"]["scan_tick"]["p95"] == 5000.0
    r = _run(tmp_path, _sandbox, monkeypatch, capsys, slow_tick(500.0, 1000.0), "tk2")     # scan p95 >= 500 and whole tick p95 >= 1000: FAIL
    assert r["result"] == "FAIL" and r["tick_rule_applies"] is True and r["scan_tick_p95_ok"] is False and r["scan_p95_ok"] is True
    r = _run(tmp_path, _sandbox, monkeypatch, capsys, slow_tick(500.0, 999.0), "tk3")      # scan p95 >= 500 and whole tick p95 < 1000: PASS
    assert r["result"] == "PASS" and r["tick_rule_applies"] is True and r["scan_tick_p95_ok"] is True
    assert "whole-tick p95" in RL.PASS_RULE and RL.SCAN_P95_TICK_TRIGGER_MS == 500.0 and RL.TICK_P95_CAP_MS == 1000.0


def test_one_run_times_every_tick_and_the_scan_minute_tick(tmp_path, _sandbox):
    write_layout(_sandbox, shifted_rows("2026-10-21"))
    base = tmp_path / "drill" / "tm"
    base.mkdir(parents=True)
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    rd = RL.RealDrill(base, _sandbox, "a" * 40, ["2026-10-21"])
    t0 = RL.day_ms("2026-10-21") + 6 * HOUR
    for tag, fast in (("real-a", True), ("real-c", False)):
        r = RL.one_run(rd, tag, t0 + 9 * MIN + 2000, t0 + 14 * MIN + 2000, fast=fast)
        assert r["tick_ms"]["n"] == 300 and r["scan_tick_ms"]["n"] == r["scans"] == 5 and r["fast_scan"] is fast
        assert r["tick_ms"]["max"] >= r["tick_ms"]["p95"] >= r["tick_ms"]["p50"] >= 0
    a = RL.one_run(rd, "real-a2", t0 + 9 * MIN + 2000, t0 + 14 * MIN + 2000)
    c = RL.one_run(rd, "real-c2", t0 + 9 * MIN + 2000, t0 + 14 * MIN + 2000, fast=False)
    assert a["log_md5"] == c["log_md5"] and a["cards_md5"] == c["cards_md5"]


def test_phantom_filter_value_is_passed_to_each_isolated_run(tmp_path, _sandbox, monkeypatch, capsys):
    if not _sandbox.exists() or not any(_sandbox.iterdir()):
        write_layout(_sandbox, shifted_rows("2026-10-21"))
    seen = []
    monkeypatch.setattr(RL, "_isolated_run", lambda tag, days, did, sha, b, phantom=True: (seen.append((tag, phantom)), _fake(tag, 1000.0))[1])
    for name, kw, want in (("ph_def", {}, True), ("ph_off", {"phantom": False}, False), ("ph_on", {"phantom": True}, True)):
        base = tmp_path / "drill" / name
        base.mkdir(parents=True)
        (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
        seen.clear()
        RL.run_real(["2026-10-21"], name, "a" * 40, base=base, isolate=True, **kw)
        capsys.readouterr()
        assert seen == [("real-a", want), ("real-b", want), ("real-c", want)]


def test_equivalence_switches_reach_each_isolated_run(tmp_path, _sandbox, monkeypatch, capsys):
    """HV6 fixes A/B/C drill switches: legacy constants and the concurrent path are handed to every child run; off, the call is the pre-switch call."""
    seen = []
    monkeypatch.setattr(RL, "_isolated_run", lambda tag, days, did, sha, b, phantom=True, **kw: (seen.append((tag, kw)), _fake(tag, 1000.0))[1])
    monkeypatch.setattr(RL, "seal_read_only", lambda base: None)
    monkeypatch.setattr(RL, "hour_scan", lambda src, days: {"hours_present": 24, "hours_expected": 24, "hours_missing": 0})
    monkeypatch.setattr(RL, "count_programs", lambda src, days: {})
    base = tmp_path / "drill" / "sw"
    base.mkdir(parents=True)
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    RL.run_real(["2026-10-21"], "sw", "a" * 40, base=base, isolate=True, legacy=True, concurrent=True)
    assert seen == [(t, {"legacy": True, "concurrent": True}) for t in ("real-a", "real-b", "real-c")]
    seen.clear()
    (base / "real-layout-result.json").unlink()
    RL.run_real(["2026-10-21"], "sw", "a" * 40, base=base, isolate=True)
    assert seen == [(t, {}) for t in ("real-a", "real-b", "real-c")]
    capsys.readouterr()
