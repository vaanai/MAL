import datetime as dt
import importlib.util
import json
import os
import types
from pathlib import Path

import pytest

_p = Path(__file__).resolve().parent.parent / "scripts" / "ops" / "mal_watchdog.py"
_s = importlib.util.spec_from_file_location("mal_watchdog", _p)
w = importlib.util.module_from_spec(_s)
_s.loader.exec_module(w)

NOW = dt.datetime(2026, 10, 29, 12, 23, tzinfo=dt.timezone.utc)
HDR = "#seq   status         name                                     memory   time    metrics\n"


def jl(*rows):
    return HDR + "".join("#%d  %-14s %-40s 4.0 GB   1h 2m\n" % r for r in rows)


PS = "  PID   RSS COMMAND\n  11 1000000 python -m tools.meta_capture --x\n"


def mk(tmp, jobs, ps=PS, free=0.5, helm=(), trades=("trades-2026-10-29T11.jsonl",), cfg=None, now=NOW):
    tr = tmp / "walk" / "trades"
    tr.mkdir(parents=True, exist_ok=True)
    for n in trades:
        (tr / n).write_text("x")
    hd = tmp / "helm"
    hd.mkdir(exist_ok=True)
    for n in helm:
        (hd / n).write_text("SECRET-BODY")
    c = dict(w.DEFAULTS)
    c.update(watch=[], walker_dir=str(tmp / "walk"), helm_dir=str(hd), disk_paths=["/"])
    c.update(cfg or {})
    calls = []

    def run(argv):
        calls.append(list(argv))
        return ps if argv[0] == "ps" else jobs

    ctx = w.default_ctx(now)
    ctx["run"] = run
    ctx["disk_usage"] = lambda p: types.SimpleNamespace(total=100, used=int(100 - free * 100 / 1) if False else 50, free=int(free * 100))
    ctx["read_meminfo"] = lambda: "MemAvailable:  %d kB\n" % (64 * 1048576)
    ctx["calls"] = calls
    return c, ctx


def passn(tmp, c, ctx, **kw):
    return w.run_pass(c, str(tmp / "st"), ctx, **kw)


def test_parse_fixture():
    t = jl((1814, "Running", "meta-capture v6 restart (Job A, c3f9e"), (12, "Passed", "x y"), (13, "Failed", "z"))
    assert w.parse_job_list(t) == {1814: "Running", 12: "Passed", 13: "Failed"}


def test_expected_done_vs_alert(tmp_path):
    j = jl((5, "Passed", "a"), (6, "Passed", "b"), (7, "Running", "c"))
    c, ctx = mk(tmp_path, j, cfg={"watch": [5, 6, 7, 8], "expected_done_ok": [5]})
    line, fresh = passn(tmp_path, c, ctx)
    keys = {a["key"] for a in fresh}
    assert keys == {"job:6:Passed", "job:8:missing"}
    assert any("job #5" in i for i in line["info"])


def test_unknown_status_alerts(tmp_path):
    c, ctx = mk(tmp_path, jl((5, "Wobbly", "a")), cfg={"watch": [5]})
    assert {a["key"] for a in passn(tmp_path, c, ctx)[1]} == {"job:5:Wobbly"}


def test_newly_failed(tmp_path):
    c, ctx = mk(tmp_path, jl((9, "Failed", "old")))
    assert passn(tmp_path, c, ctx)[1] == []  # seeded
    c, ctx = mk(tmp_path, jl((9, "Failed", "old"), (10, "Failed", "new")))
    assert [a["key"] for a in passn(tmp_path, c, ctx)[1]] == ["job:10:Failed"]
    c, ctx = mk(tmp_path, jl((9, "Failed", "old"), (10, "Failed", "new")))
    assert passn(tmp_path, c, ctx)[1] == []


def test_dedupe_window(tmp_path):
    c, ctx = mk(tmp_path, jl(), cfg={"watch": [5]})
    assert len(passn(tmp_path, c, ctx)[1]) == 1
    c, ctx = mk(tmp_path, jl(), cfg={"watch": [5]}, now=NOW + dt.timedelta(hours=5))
    assert passn(tmp_path, c, ctx)[1] == []
    c, ctx = mk(tmp_path, jl(), cfg={"watch": [5]}, now=NOW + dt.timedelta(hours=7))
    assert len(passn(tmp_path, c, ctx)[1]) == 1
    lines = (tmp_path / "st" / "alerts.jsonl").read_text().splitlines()
    assert len(lines) == 2 and set(json.loads(lines[0])) == {"ts", "key", "severity", "msg"}
    assert (tmp_path / "st" / "heartbeat").read_text().strip().endswith("Z")


def test_helm_seed_then_alert(tmp_path):
    c, ctx = mk(tmp_path, jl(), helm=["HELM-REPLY-1.md", "other"])
    assert passn(tmp_path, c, ctx)[1] == []
    c, ctx = mk(tmp_path, jl(), helm=["HELM-REPLY-1.md", "HELM-REPLY-2.md"])
    assert [a["key"] for a in passn(tmp_path, c, ctx)[1]] == ["helm:HELM-REPLY-2.md"]
    assert "SECRET-BODY" not in (tmp_path / "st" / "status.jsonl").read_text()


def test_walker_stale_only_when_running(tmp_path):
    old = ("trades-2026-10-29T01.jsonl",)
    c, ctx = mk(tmp_path, jl((1036, "Passed", "w")), trades=old)
    assert "walker:stale" not in {a["key"] for a in passn(tmp_path, c, ctx)[1]}
    c, ctx = mk(tmp_path / "b", jl((1290, "Running", "w")), trades=old)
    assert "walker:stale" in {a["key"] for a in passn(tmp_path / "b", c, ctx)[1]}


def test_walker_disk_and_mem_and_meta(tmp_path):
    ps = "PID RSS ARGS\n 1 3000000 python -m tools.meta_capture\n"
    c, ctx = mk(tmp_path, jl((1814, "Running", "m")), ps=ps, free=0.1)
    ctx["read_meminfo"] = lambda: "MemAvailable: 1048576 kB\n"
    keys = {a["key"] for a in passn(tmp_path, c, ctx)[1]}
    assert {"walker:disk", "mem:low", "meta:rss"} <= keys
    c, ctx = mk(tmp_path / "b", jl((1814, "Running", "m")), ps="PID RSS ARGS\n")
    assert "meta:absent" in {a["key"] for a in passn(tmp_path / "b", c, ctx)[1]}


def test_refuse_meta_capture():
    with pytest.raises(ValueError):
        w.refuse_sealed("/data/mal/meta-capture")
    with pytest.raises(ValueError):
        w.refuse_sealed("/data/mal/meta-capture/x/../y")
    ctx = w.default_ctx(NOW)
    with pytest.raises(ValueError):
        ctx["listdir"]("/data/mal/meta-capture/")


def test_refuse_config_path(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"spill_globs": ["/data/mal/meta-capture/*"]}))
    with pytest.raises(ValueError):
        w.load_config(str(p))


def test_runner_refuses_other_commands():
    with pytest.raises(ValueError):
        w.run_cmd([w.MISCUSI, "job", "status", "1561"])
    with pytest.raises(ValueError):
        w.run_cmd([w.MISCUSI, "job", "log", "1"])


def test_check_error(tmp_path):
    c, ctx = mk(tmp_path, jl())
    ctx["read_meminfo"] = lambda: (_ for _ in ()).throw(OSError("boom"))
    assert "check_error:mem" in {a["key"] for a in passn(tmp_path, c, ctx)[1]}
    c, ctx = mk(tmp_path / "b", jl())

    def bad(argv):
        raise RuntimeError("cli down")

    ctx["run"] = bad
    keys = {a["key"] for a in passn(tmp_path / "b", c, ctx)[1]}
    assert {"check_error:jobs", "check_error:meta"} <= keys


def test_dry_run_writes_nothing(tmp_path):
    c, ctx = mk(tmp_path, jl(), cfg={"watch": [5]})
    line, fresh = passn(tmp_path, c, ctx, dry_run=True)
    assert fresh and not (tmp_path / "st").exists()


def test_daily_summary_once(tmp_path):
    t = NOW.replace(hour=0, minute=40)
    c, ctx = mk(tmp_path, jl(), now=t)
    passn(tmp_path, c, ctx)
    c, ctx = mk(tmp_path, jl(), now=t + dt.timedelta(hours=1))
    passn(tmp_path, c, ctx)
    d = (tmp_path / "st" / "daily.jsonl").read_text().splitlines()
    assert len(d) == 1 and "passes" in json.loads(d[0])
