import datetime as dt
import hashlib
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
REAL_DEFAULT_CTX = w.default_ctx  # mk() must not see tests that patch w.default_ctx

NOW = dt.datetime(2026, 10, 29, 12, 23, tzinfo=dt.timezone.utc)
HDR = "#seq   status         name                                     memory   time    metrics\n"


def jl(*rows):
    rows = rows or ((1, "Passed", "filler"),)
    return HDR + "".join("#%d  %-14s %-40s 4.0 GB   1h 2m\n" % r for r in rows)


PS = "  PID   RSS COMMAND\n  11 1000000 python -m tools.meta_capture --x\n"


def mk(tmp, jobs, ps=PS, free=0.5, helm=(), cfg=None, now=NOW):
    (tmp / "walk").mkdir(parents=True, exist_ok=True)
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

    ctx = REAL_DEFAULT_CTX(now)
    ctx["run"] = run
    ctx["disk_usage"] = lambda p: types.SimpleNamespace(total=100, used=int(100 - free * 100 / 1) if False else 50, free=int(free * 100))
    ctx["read_meminfo"] = lambda: "MemAvailable:  %d kB\n" % (64 * 1048576)
    ctx["calls"] = calls
    real_ld = ctx["listdir"]
    ctx["listdir"] = lambda d: ["trades-2026-10-29T11.jsonl.zst"] if d.endswith("/trades") else real_ld(d)
    return c, ctx


def passn(tmp, c, ctx, **kw):
    return w.run_pass(c, str(tmp / "st"), ctx, **kw)


def test_parse_fixture():
    t = jl((1814, "Running", "meta-capture v6 restart (Job A, c3f9e"), (12, "Passed", "x y"), (13, "Failed", "z"))
    assert {k: v[0] for k, v in w.parse_job_list(t).items()} == {1814: "Running", 12: "Passed", 13: "Failed"}


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


def walker_ctx(tmp, names, jobs):
    c, ctx = mk(tmp, jobs)
    real = ctx["listdir"]
    ctx["listdir"] = lambda d: list(names) if d.endswith("/trades") else real(d)
    return c, ctx


def test_walker_stale_only_when_running_zst(tmp_path):
    old = ["trades-2026-10-29T01.jsonl.zst", "trades-2026-10-28T23.jsonl", "junk"]
    c, ctx = walker_ctx(tmp_path, old, jl((1036, "Passed", "w")))
    line, fresh = passn(tmp_path, c, ctx)
    assert "walker:stale" not in {a["key"] for a in fresh}
    assert line["checks"]["walker"]["walker_newest_hour"] == "2026-10-29T01"
    c, ctx = walker_ctx(tmp_path / "b", old, jl((1290, "Running", "w")))
    assert "walker:stale" in {a["key"] for a in passn(tmp_path / "b", c, ctx)[1]}
    c, ctx = walker_ctx(tmp_path / "c", ["trades-2026-10-29T11.jsonl.zst"], jl((1036, "Running", "w")))
    assert "walker:stale" not in {a["key"] for a in passn(tmp_path / "c", c, ctx)[1]}


def test_walker_no_names_is_check_error(tmp_path):
    c, ctx = walker_ctx(tmp_path, ["notes.txt", "trades-2026-10-29T11.jsonl.tmp"], jl((1036, "Running", "w")))
    assert "check_error:walker" in {a["key"] for a in passn(tmp_path, c, ctx)[1]}


def test_walker_allowlist_exact():
    ok = "/data/mal/blocks/forward-1016/trades"
    assert w.refuse_sealed(ok, listing=True) == ok
    for bad in ("/data/mal/blocks/forward-1016", ok + "/../x", "/data/mal/blocks/forward-1017/trades",
                ok + "/sub", ok + "/trades-2026-10-29T11.jsonl", "/data/mal/blocks/forward-*/trades"):
        with pytest.raises(ValueError):
            w.refuse_sealed(bad, listing=True)
    with pytest.raises(ValueError):
        w.refuse_sealed("/data/mal/meta-capture/trades", listing=True)


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
    for g in ("/data/mal/meta-*", "/data/mal/meta-capture*/x", "/data/mal/*/creates"):
        with pytest.raises(ValueError):
            w.refuse_sealed(g)
    with pytest.raises(ValueError):
        w.refuse_sealed("/data/mal/blocks/forward-*/trades/*", listing=True)
    with pytest.raises(ValueError):
        w.refuse_sealed("/data/mal/blocks/forward-1016/other", listing=True)
    w.refuse_sealed("/data/mal/blocks/forward-1016")  # statvfs use is allowed


def test_refuse_symlink(tmp_path, monkeypatch):
    link = tmp_path / "ln"
    link.symlink_to("/data/mal/meta-capture")
    with pytest.raises(ValueError):
        w.refuse_sealed(str(link))
    monkeypatch.setattr(w.os.path, "realpath", lambda p: "/data/mal/meta-capture/z")
    with pytest.raises(ValueError):
        w.refuse_sealed("/tmp/anything")
    ctx = w.default_ctx(NOW)
    with pytest.raises(ValueError):
        ctx["listdir"]("/data/mal/meta-capture/")


def test_refuse_config_path(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"spill_globs": ["/data/mal/meta-capture/*"]}))
    with pytest.raises(ValueError):
        w.load_config(str(p))


def test_missing_config_fails(tmp_path):
    with pytest.raises(OSError):
        w.load_config(str(tmp_path / "nope.json"))


def test_empty_or_garbled_job_list_alerts(tmp_path):
    for bad in ("", "Error: not logged in\n", "1814 Running x\n", HDR):
        c, ctx = mk(tmp_path / str(abs(hash(bad))), bad)
        line, fresh = passn(tmp_path / str(abs(hash(bad))), c, ctx)
        assert "check_error:jobs" in {a["key"] for a in fresh}, bad
        assert line["checks"]["jobs"] == "error"


def test_unknown_status_any_job(tmp_path):
    c, ctx = mk(tmp_path, jl((5, "Lost", "a"), (6, "Cancelled", "b"), (7, "Errored", "c")))
    assert {a["key"] for a in passn(tmp_path, c, ctx)[1]} == {"job:5:Lost", "job:7:Errored"}


def at(h):
    return NOW + dt.timedelta(hours=h)


def test_starting_and_pass_unwatched_are_silent(tmp_path):
    j = jl((1, "Starting", "a"), (2, "PASS", "b"), (5, "pass", "e"), (6, "STARTING", "f"))
    for h in (0, 1):
        c, ctx = mk(tmp_path, j, now=at(h))
        line, fresh = passn(tmp_path, c, ctx)
        assert fresh == [] and line["alert_keys"] == []


def test_unwatched_fail_and_interrupted_alert_once_like_failed(tmp_path):
    base = jl((1, "FAIL", "old"), (2, "Interrupted", "old"), (3, "Failed", "old"))
    c, ctx = mk(tmp_path, base)
    assert passn(tmp_path, c, ctx)[1] == []  # first pass baselines all three
    new = jl((1, "FAIL", "old"), (2, "Interrupted", "old"), (3, "Failed", "old"),
             (4, "FAIL", "new"), (5, "Interrupted", "new"), (6, "Failed", "new"), (7, "fail", "lc"),
             (8, "INTERRUPTED", "uc"))
    c, ctx = mk(tmp_path, new, now=at(1))
    fresh = passn(tmp_path, c, ctx)[1]
    assert sorted((a["key"], a["severity"]) for a in fresh) == sorted([
        ("job:4:FAIL", "medium"), ("job:5:Interrupted", "medium"), ("job:6:Failed", "medium"),
        ("job:7:fail", "medium"), ("job:8:INTERRUPTED", "medium")])
    assert all(a["msg"].startswith("job #") and "newly" in a["msg"] for a in fresh)
    for h in (2, 8, 20):  # once: never re-alerted, inside or past the dedupe window
        c, ctx = mk(tmp_path, new, now=at(h))
        assert passn(tmp_path, c, ctx)[1] == []


def test_unwatched_fail_interrupted_baselined_on_first_pass_after_deploy(tmp_path):
    # state written by the previous version: failed_seen exists, ended_seen does not
    st = tmp_path / "st"
    st.mkdir()
    (st / "state.json").write_text(json.dumps({"failed_seen": []}))
    j = jl((1, "FAIL", "listed before deploy"), (2, "Interrupted", "listed before deploy"))
    c, ctx = mk(tmp_path, j)
    assert passn(tmp_path, c, ctx)[1] == []  # no burst of old jobs
    c, ctx = mk(tmp_path, jl((1, "FAIL", "a"), (2, "Interrupted", "b"), (9, "FAIL", "new")), now=at(1))
    assert [a["key"] for a in passn(tmp_path, c, ctx)[1]] == ["job:9:FAIL"]


def test_watched_fail_interrupted_not_double_alerted_by_unwatched_rule(tmp_path):
    cfg = {"watch": [1, 2]}
    c, ctx = mk(tmp_path, jl((7, "Running", "x")), cfg=cfg)
    passn(tmp_path, c, ctx)  # baseline
    c, ctx = mk(tmp_path, jl((1, "FAIL", "w"), (2, "Interrupted", "w"), (7, "Running", "x")), cfg=cfg, now=at(1))
    fresh = passn(tmp_path, c, ctx)[1]
    assert [(a["key"], a["severity"]) for a in fresh] == [("job:1:FAIL", "info"), ("job:2:Interrupted", "medium")]


def test_states_are_case_insensitive_when_watched(tmp_path):
    j = jl((1, "STARTING", "a"), (2, "RUNNING", "b"), (3, "Paused", "c"), (4, "PASS", "d"))
    c, ctx = mk(tmp_path, j, cfg={"watch": [1, 2, 3, 4], "expected_done_ok": [4]})
    line, fresh = passn(tmp_path, c, ctx)
    assert fresh == [] and any("job #4" in i for i in line["info"])


def test_watched_pass_alerts_like_passed_unless_expected(tmp_path):
    c, ctx = mk(tmp_path, jl((6, "PASS", "b")), cfg={"watch": [6]})
    assert [a["key"] for a in passn(tmp_path, c, ctx)[1]] == ["job:6:PASS"]


def test_watched_fail_is_info_once(tmp_path):
    j = jl((3, "FAIL", "verdict"))
    c, ctx = mk(tmp_path, j, cfg={"watch": [3]})
    fresh = passn(tmp_path, c, ctx)[1]
    assert [(a["key"], a["severity"]) for a in fresh] == [("job:3:FAIL", "info")]
    for h in (7, 14):  # a verdict is final: not re-alerted after the dedupe window
        c, ctx = mk(tmp_path, j, cfg={"watch": [3]}, now=at(h))
        assert passn(tmp_path, c, ctx)[1] == []


def test_watched_interrupted_is_medium_and_re_alerts(tmp_path):
    j = jl((4, "Interrupted", "lost"))
    c, ctx = mk(tmp_path, j, cfg={"watch": [4]})
    fresh = passn(tmp_path, c, ctx)[1]
    assert [(a["key"], a["severity"]) for a in fresh] == [("job:4:Interrupted", "medium")]
    c, ctx = mk(tmp_path, j, cfg={"watch": [4]}, now=at(7))
    assert [a["key"] for a in passn(tmp_path, c, ctx)[1]] == ["job:4:Interrupted"]


def test_watched_done_job_aged_out_is_info_once(tmp_path):
    for k, st in enumerate(("Passed", "PASS", "Done", "FAIL")):
        sub = tmp_path / ("s%d" % k)
        cfg = {"watch": [5], "expected_done_ok": [5]}
        c, ctx = mk(sub, jl((5, st, "a")), cfg=cfg)
        passn(sub, c, ctx)
        c, ctx = mk(sub, jl(), cfg=cfg, now=at(1))
        line, fresh = passn(sub, c, ctx)
        assert fresh == [] and line["alert_keys"] == [], st
        assert line["info"] == ["job #5 aged out of the list (last seen %s)" % st.lower()]
        c, ctx = mk(sub, jl(), cfg=cfg, now=at(2))
        line, fresh = passn(sub, c, ctx)
        assert fresh == [] and line["info"] == [], st  # recorded once only
        c, ctx = mk(sub, jl(), cfg=cfg, now=at(20))
        assert passn(sub, c, ctx)[1] == [], st  # and not re-raised after the window


def test_watched_missing_never_seen_done_stays_high(tmp_path):
    # never seen at all
    c, ctx = mk(tmp_path, jl(), cfg={"watch": [8]})
    assert [(a["key"], a["severity"]) for a in passn(tmp_path, c, ctx)[1]] == [("job:8:missing", "high")]
    # seen Running (still alive), then vanished: a lost job, not a finished one
    sub = tmp_path / "r"
    c, ctx = mk(sub, jl((9, "Running", "a")), cfg={"watch": [9]})
    assert passn(sub, c, ctx)[1] == []
    c, ctx = mk(sub, jl(), cfg={"watch": [9]}, now=at(1))
    assert [(a["key"], a["severity"]) for a in passn(sub, c, ctx)[1]] == [("job:9:missing", "high")]
    # seen Cancelled (watched Cancelled alerts), then vanished: still high
    sub = tmp_path / "c"
    c, ctx = mk(sub, jl((10, "Cancelled", "a")), cfg={"watch": [10]})
    assert [a["key"] for a in passn(sub, c, ctx)[1]] == ["job:10:Cancelled"]
    c, ctx = mk(sub, jl(), cfg={"watch": [10]}, now=at(7))
    assert [a["key"] for a in passn(sub, c, ctx)[1]] == ["job:10:missing"]


def test_job_list_error_keeps_watch_seen(tmp_path):
    cfg = {"watch": [5], "expected_done_ok": [5]}
    c, ctx = mk(tmp_path, jl((5, "PASS", "a")), cfg=cfg)
    passn(tmp_path, c, ctx)
    c, ctx = mk(tmp_path, "Error: not logged in\n", cfg=cfg, now=at(1))
    assert "check_error:jobs" in {a["key"] for a in passn(tmp_path, c, ctx)[1]}
    c, ctx = mk(tmp_path, jl(), cfg=cfg, now=at(2))  # memory survived the failed read
    line, fresh = passn(tmp_path, c, ctx)
    assert "job:5:missing" not in line["alert_keys"]
    assert line["info"] == ["job #5 aged out of the list (last seen pass)"]


def test_watch_seen_state_pruned_and_tolerates_junk(tmp_path):
    cfg = {"watch": [5]}
    c, ctx = mk(tmp_path, jl((5, "Running", "a"), (6, "Running", "b")), cfg=cfg)
    passn(tmp_path, c, ctx)
    st = json.loads((tmp_path / "st" / "state.json").read_text())
    assert st["watch_seen"] == {"5": "running"}
    for junk in (["junk"], {"5": ["unhashable"]}, {"5": {"a": 1}}, {"5": 7}):
        st["watch_seen"] = junk  # a damaged value must not crash the check
        (tmp_path / "st" / "state.json").write_text(json.dumps(st))
        c, ctx = mk(tmp_path, jl(), cfg=cfg, now=at(1))
        line, fresh = passn(tmp_path, c, ctx)
        assert line["checks"]["jobs"] != "error", junk
        assert "job:5:missing" in line["alert_keys"], junk


def test_only_job_list_and_ps_are_run(tmp_path):
    c, ctx = mk(tmp_path, jl((5, "PASS", "a"), (6, "Interrupted", "b")), cfg={"watch": [5, 6, 7]})
    passn(tmp_path, c, ctx)
    assert {tuple(x[1:]) for x in ctx["calls"] if x[0] != "ps"} == {("job", "list")}
    assert {x[0] for x in ctx["calls"]} <= {w.MISCUSI, "ps"}


def test_corrupt_state_alerts_and_backs_up(tmp_path):
    st = tmp_path / "st"
    st.mkdir()
    (st / "state.json").write_text("{not json")
    c, ctx = mk(tmp_path, jl())
    assert "state:unreadable" in {a["key"] for a in passn(tmp_path, c, ctx)[1]}
    assert list(st.glob("state.json.corrupt-*"))
    assert json.loads((st / "state.json").read_text())


def test_safe_pass_survives_exception(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(w, "run_pass", boom)
    args = types.SimpleNamespace(state_dir=str(tmp_path), dry_run=False)
    w.safe_pass({}, args)
    assert "WATCHDOG_PASS_FAILED" in capsys.readouterr().err


def sh_wait(sd, *args):
    import subprocess
    r = subprocess.run(["sh", str(_p.parent / "watchdog-wait.sh"), str(sd), *args],
                       capture_output=True, text=True, timeout=30, env=dict(os.environ, WAIT_POLL_S="1"))
    return r.returncode, r.stdout, r.stderr


def test_wait_cursor_persists_and_ack(tmp_path):
    sd = tmp_path
    (sd / "heartbeat").write_text(NOW.strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")
    (sd / "heartbeat").write_text(dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")
    (sd / "alerts.jsonl").write_text('{"a":1}\n')
    import subprocess
    # first launch seeds cursor at 1 line; wait with --since-lines 0 to see it
    rc, out, _ = sh_wait(sd, "--since-lines", "0")
    assert rc == 0 and '{"a":1}' in out
    assert sh_wait(sd, "--ack")[1].startswith("ACKED 1")
    # alert written while no waiter runs is not lost
    with open(sd / "alerts.jsonl", "a") as f:
        f.write('{"a":2}\n')
    rc, out, _ = sh_wait(sd)
    assert out.strip() == '{"a":2}'
    # not advanced without ack: same line again
    assert sh_wait(sd)[1].strip() == '{"a":2}'
    sh_wait(sd, "--ack")


def test_wait_no_args_and_stale(tmp_path):
    import subprocess
    r = subprocess.run(["sh", str(_p.parent / "watchdog-wait.sh")], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr
    (tmp_path / "heartbeat").write_text("2000-01-01T00:00:00Z\n")
    assert sh_wait(tmp_path)[1].startswith("HEARTBEAT_STALE")


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
    t = NOW.replace(hour=22)
    c, ctx = mk(tmp_path, jl(), now=t)
    passn(tmp_path, c, ctx)
    assert not (tmp_path / "st" / "daily.jsonl").exists()
    for h in (2, 3):  # next day 00:22 (before 00:30: no emit) handled by 01:22
        c, ctx = mk(tmp_path, jl(), now=t + dt.timedelta(hours=h))
        passn(tmp_path, c, ctx)
    d = (tmp_path / "st" / "daily.jsonl").read_text().splitlines()
    assert len(d) == 1 and "passes" in json.loads(d[0])


# ---- config reload on every pass (the loop used to read the config once) ----

SHA = w.SHA_PREFIX


def write_cfg(path, c, **over):
    """Write a config file from the mk() config dict; return its full sha256."""
    d = dict(c)
    d.update(over)
    path.write_text(json.dumps(d))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def loop_pass(tmp, rl, monkeypatch, jobs, h, dry_run=False):
    """One safe_pass at NOW+h hours with fake command output; returns the status line."""
    _, ctx = mk(tmp, jobs, now=at(h))
    monkeypatch.setattr(w, "default_ctx", lambda n=None: ctx)
    w.safe_pass(rl.cfg, types.SimpleNamespace(state_dir=str(tmp / "st"), dry_run=dry_run), rl)
    p = tmp / "st" / "status.jsonl"
    return json.loads(p.read_text().splitlines()[-1]) if p.exists() else None


def alert_rows(tmp, key):
    p = tmp / "st" / "alerts.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    return [r for r in rows if r["key"] == key]


def test_reload_picks_up_new_watch_entry_and_drops_removed_one(tmp_path, monkeypatch):
    j = jl((5, "Running", "a"), (6, "Cancelled", "b"))
    c, _ = mk(tmp_path, j)
    cp = tmp_path / "config.json"
    sha0 = write_cfg(cp, c, watch=[5])
    rl = w.ConfigReloader(str(cp))
    s0 = loop_pass(tmp_path, rl, monkeypatch, j, 0)
    assert s0["alert_keys"] == [] and s0["info"] == [] and s0["config_sha256"] == sha0[:SHA]
    sha1 = write_cfg(cp, c, watch=[5, 6])  # edited after start: job 6 is now watched
    s1 = loop_pass(tmp_path, rl, monkeypatch, j, 1)
    assert s1["new_alerts"] == ["job:6:Cancelled"]  # watched Cancelled alerts: the new entry is live
    assert s1["info"] == ["config changed: sha256 %s -> %s" % (sha0[:SHA], sha1[:SHA])]
    assert s1["config_sha256"] == sha1[:SHA] and rl.cfg["watch"] == [5, 6]
    sha2 = write_cfg(cp, c, watch=[5])  # removed again (the 10-30 23:23Z case)
    s2 = loop_pass(tmp_path, rl, monkeypatch, j, 8)  # past the 6h window: a stale config would re-alert
    assert "job:6:Cancelled" not in s2["alert_keys"] and s2["new_alerts"] == []
    assert s2["info"] == ["config changed: sha256 %s -> %s" % (sha1[:SHA], sha2[:SHA])]
    s3 = loop_pass(tmp_path, rl, monkeypatch, j, 9)  # unchanged file: no change line
    assert s3["info"] == [] and s3["config_sha256"] == sha2[:SHA]


def test_bad_json_keeps_old_config_and_alerts_once(tmp_path, monkeypatch, capsys):
    ok, cancelled = jl((5, "Running", "a")), jl((5, "Cancelled", "a"))
    c, _ = mk(tmp_path, ok)
    cp = tmp_path / "config.json"
    sha0 = write_cfg(cp, c, watch=[5])
    rl = w.ConfigReloader(str(cp))
    assert loop_pass(tmp_path, rl, monkeypatch, ok, 0)["alert_keys"] == []
    cp.write_text('{"watch": [], BAD-MARKER')
    s = loop_pass(tmp_path, rl, monkeypatch, cancelled, 1)
    # job 5 Cancelled alerts only because the OLD config (watch=[5]) is still in use
    assert sorted(s["new_alerts"]) == ["config:reload_failed", "job:5:Cancelled"]
    assert rl.cfg["watch"] == [5] and rl.sha == sha0 and s["config_sha256"] == sha0[:SHA]
    assert s["checks"]["jobs"] == {"n_jobs": 1}  # the pass itself still ran
    row = alert_rows(tmp_path, "config:reload_failed")[0]
    assert row["severity"] == "high" and sha0[:SHA] in row["msg"] and "JSONDecodeError" in row["msg"]
    assert "BAD-MARKER" not in row["msg"]  # error text never echoes file contents
    s = loop_pass(tmp_path, rl, monkeypatch, cancelled, 2)  # still broken: no second alert line
    assert s["new_alerts"] == [] and "config:reload_failed" in s["alert_keys"]
    assert len(alert_rows(tmp_path, "config:reload_failed")) == 1
    sha1 = write_cfg(cp, c, watch=[5], disk_used_alert_pct=91)  # fixed with a different file
    s = loop_pass(tmp_path, rl, monkeypatch, cancelled, 3)
    assert "config:reload_failed" not in s["alert_keys"]
    assert s["info"] == ["config reload recovered, sha256 %s" % sha1[:SHA],
                         "config changed: sha256 %s -> %s" % (sha0[:SHA], sha1[:SHA])]
    cp.write_text("not json")  # broken again inside the dedupe window: a new episode alerts again
    assert "config:reload_failed" in loop_pass(tmp_path, rl, monkeypatch, cancelled, 4)["new_alerts"]
    assert len(alert_rows(tmp_path, "config:reload_failed")) == 2
    assert "WATCHDOG_PASS_FAILED" not in capsys.readouterr().err


@pytest.mark.parametrize("name,write,err", [
    ("missing", lambda cp: cp.unlink(), "FileNotFoundError"),
    ("garbage", lambda cp: cp.write_text("{nope"), "JSONDecodeError"),
    ("not_object", lambda cp: cp.write_text("[]"), "ValueError"),
    ("empty", lambda cp: cp.write_text(""), "JSONDecodeError"),
    ("bad_type", lambda cp: cp.write_text('{"check_minute": "23"}'), "ValueError"),
    ("sealed_path", lambda cp: cp.write_text('{"spill_globs": ["/data/mal/meta-capture/*"]}'), "ValueError"),
])
def test_failed_reload_keeps_last_good_config_never_defaults(tmp_path, monkeypatch, capsys, name, write, err):
    j = jl((5, "Running", "a"))
    c, _ = mk(tmp_path, j)
    cp = tmp_path / "config.json"
    sha0 = write_cfg(cp, c, watch=[5], check_minute=41)
    rl = w.ConfigReloader(str(cp))
    write(cp)
    s = loop_pass(tmp_path, rl, monkeypatch, j, 1)
    assert s["new_alerts"] == ["config:reload_failed"], name
    msg = alert_rows(tmp_path, "config:reload_failed")[0]["msg"]
    assert err in msg and sha0[:SHA] in msg, name
    assert rl.cfg["watch"] == [5] and rl.cfg["check_minute"] == 41 and rl.sha == sha0, name
    assert s["checks"]["jobs"] == {"n_jobs": 1}
    assert "WATCHDOG_PASS_FAILED" not in capsys.readouterr().err


def test_missing_or_bad_config_at_start_is_still_an_error(tmp_path, monkeypatch):
    def no_pass(*a, **k):
        raise AssertionError("a pass must not run without a valid config at start")

    monkeypatch.setattr(w, "run_pass", no_pass)
    monkeypatch.setattr(w, "safe_pass", no_pass)
    nope, st = str(tmp_path / "nope.json"), tmp_path / "st"
    with pytest.raises(FileNotFoundError):
        w.ConfigReloader(nope)
    for mode in ("--once", "--loop"):
        for extra in ([], ["--dry-run"]):
            with pytest.raises(FileNotFoundError):
                w.main(["--config", nope, "--state-dir", str(st), mode] + extra)
    bad = tmp_path / "bad.json"
    for body, exc in (("{nope", ValueError), ("[]", ValueError), ('{"watch": "1814"}', ValueError)):
        bad.write_text(body)
        for mode in ("--once", "--loop"):
            with pytest.raises(exc):
                w.main(["--config", str(bad), "--state-dir", str(st), mode])
    assert not st.exists()  # nothing written, no silent defaults


def test_validate_config_rejects_wrong_types(tmp_path):
    good = dict(w.DEFAULTS)
    cp = tmp_path / "c.json"
    for k, v in (("check_minute", "23"), ("check_minute", 60), ("check_minute", -1), ("check_minute", True),
                 ("watch", "1814"), ("watch", [True]), ("watch", ["5"]), ("expected_done_ok", {"a": 1}),
                 ("realert_hours", "6"), ("mem_avail_alert_gib", None), ("walker_job_seq", 1.5),
                 ("walker_dir", 5), ("helm_dir", None), ("disk_paths", "/"), ("spill_globs", [3])):
        cp.write_text(json.dumps(dict(good, **{k: v})))
        with pytest.raises(ValueError, match=k):
            w.read_config(str(cp))
    cp.write_text(json.dumps(dict(good, check_minute=0, realert_hours=0.5, walker_free_alert_pct=21.5, _note="x")))
    cfg, sha = w.read_config(str(cp))
    assert cfg["check_minute"] == 0 and "_note" not in cfg and sha == hashlib.sha256(cp.read_bytes()).hexdigest()


def test_example_config_still_loads():
    ex = Path(__file__).resolve().parent.parent / "scripts" / "ops" / "mal-watchdog.config.example.json"
    assert w.load_config(str(ex))["watch"]


def test_dry_run_reloads_and_writes_nothing(tmp_path, monkeypatch, capsys):
    j = jl((5, "Running", "a"))
    c, _ = mk(tmp_path, j)
    cp = tmp_path / "config.json"
    sha0 = write_cfg(cp, c, watch=[5])
    rl = w.ConfigReloader(str(cp))
    assert loop_pass(tmp_path, rl, monkeypatch, j, 0, dry_run=True) is None  # no status.jsonl in dry-run
    sha1 = write_cfg(cp, c, watch=[5, 6])
    loop_pass(tmp_path, rl, monkeypatch, j, 1, dry_run=True)
    printed = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert printed[1]["info"] == ["config changed: sha256 %s -> %s" % (sha0[:SHA], sha1[:SHA])]
    assert printed[1]["alert_keys"] == ["job:6:missing"]  # the new entry is live in dry-run too
    cp.write_text("{nope")
    loop_pass(tmp_path, rl, monkeypatch, j, 2, dry_run=True)
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert "config:reload_failed" in last["new_alerts"] and "job:6:missing" in last["alert_keys"]  # old cfg kept
    assert not (tmp_path / "st").exists()


def test_main_once_dry_run_unchanged(tmp_path, monkeypatch, capsys):
    j = jl((5, "Running", "a"))
    c, ctx = mk(tmp_path, j)
    cp = tmp_path / "config.json"
    sha0 = write_cfg(cp, c, watch=[5, 8])
    monkeypatch.setattr(w, "default_ctx", lambda n=None: ctx)
    assert w.main(["--once", "--dry-run", "--config", str(cp), "--state-dir", str(tmp_path / "st")]) == 0
    line = json.loads(capsys.readouterr().out)
    assert line["alert_keys"] == ["job:8:missing"] and line["info"] == [] and line["config_sha256"] == sha0[:SHA]
    assert not (tmp_path / "st").exists()


def test_main_loop_rereads_config_on_every_pass(tmp_path, monkeypatch):
    j = jl((5, "Running", "a"), (6, "Cancelled", "b"))
    c, _ = mk(tmp_path, j)
    cp = tmp_path / "config.json"
    sha0 = write_cfg(cp, c, watch=[5])
    clock = types.SimpleNamespace(t=NOW.timestamp(), shas=[sha0])
    edits = [dict(watch=[5, 6]), dict(watch=[5])]  # applied between pass 1-2 and pass 2-3

    def sleep(sec):
        clock.t += sec
        if edits:
            clock.shas.append(write_cfg(cp, c, **edits.pop(0)))

    monkeypatch.setattr(w, "time", types.SimpleNamespace(time=lambda: clock.t, sleep=sleep))
    monkeypatch.setattr(w, "next_check", lambda now, minute: dt.datetime.fromtimestamp(clock.t + 3600, dt.timezone.utc))
    monkeypatch.setattr(w, "default_ctx", lambda n=None: mk(tmp_path, j, now=dt.datetime.fromtimestamp(clock.t, dt.timezone.utc))[1])
    rc = w.main(["--loop", "--duration-s", "9000", "--config", str(cp), "--state-dir", str(tmp_path / "st")])
    assert rc == 0 and not edits
    lines = [json.loads(x) for x in (tmp_path / "st" / "status.jsonl").read_text().splitlines()]
    assert [x["alert_keys"] for x in lines] == [[], ["job:6:Cancelled"], []]
    s = [x[:SHA] for x in clock.shas]
    assert [x["info"] for x in lines] == [[], ["config changed: sha256 %s -> %s" % (s[0], s[1])],
                                          ["config changed: sha256 %s -> %s" % (s[1], s[2])]]
    assert [x["config_sha256"] for x in lines] == s
