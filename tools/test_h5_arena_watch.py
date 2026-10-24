"""Tests for scripts/mal-fast/h5-arena-watch.py (the arena canary watchdog) and its unit and timer. A fake host: no systemd, no network, no key,
no file outside the test's tmp dir, nothing posted anywhere."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MF = REPO / "scripts" / "mal-fast"
_spec = importlib.util.spec_from_file_location("h5_arena_watch", MF / "h5-arena-watch.py")
W = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(W)  # type: ignore[union-attr]

HOOK = "https://discord.com/api/webhooks/123/abcDEF_-"
NOW = 1_793_000_000.0  # 2026-10-31T... UTC
FEED, TWINS, TIEOUT = Path("/home/claude/data/arena/h5-q50"), Path("/home/claude/data/canary/twins.jsonl"), Path("/home/claude/data/canary/tieout")
ENV = {"ARENA_WATCH_DISCORD_WEBHOOK": HOOK, "ARENA_WATCH_FEED_DIR": str(FEED), "ARENA_WATCH_TWINS_FILE": str(TWINS), "ARENA_WATCH_TIEOUT_DIR": str(TIEOUT)}
STATE = W.STATE_DIR / "live" / "state-live.json"
COUNTERS = W.STATE_DIR / "live" / "h5-counters.json"


class FakeHost:
    def __init__(self, active="active", restarts=0, files=None, feed_age=10.0, balance=500_000_000, systemctl_rc=0):
        self.active, self.restarts, self.rc = active, restarts, systemctl_rc
        self.files: dict[Path, bytes | Exception] = dict(files or {})
        self.feed = [("h5-shadow-2026-10-31T10.jsonl", NOW - feed_age - 3600), ("h5-shadow-2026-10-31T11.jsonl", NOW - feed_age), ("h5-shadow-status.json", NOW)] \
            if feed_age is not None else []
        self.bal = balance
        self.balance_calls: list[str] = []

    def systemctl(self, *args):
        if self.rc == "raise":
            raise OSError("no systemd")
        return self.rc, f"ActiveState={self.active}\nNRestarts={self.restarts}\n"

    def read(self, p):
        v = self.files.get(p)
        if isinstance(v, Exception):
            raise v
        return v

    def exists(self, p):
        return p in self.files

    def listdir(self, p):
        if p != FEED:
            raise OSError("no such dir")
        return list(self.feed)

    def balance(self, wallet):
        self.balance_calls.append(wallet)
        if isinstance(self.bal, Exception):
            raise self.bal
        return self.bal


def js(obj) -> bytes:
    return json.dumps(obj).encode()


def alerts(host, env=None, now=NOW):
    a, _obs = W.collect(host, env or ENV, now)
    return a


def test_a_quiet_dry_install_has_no_alert():
    assert alerts(FakeHost()) == {}


def test_config_problems():
    for env, why in (({**ENV, "ARENA_WATCH_FEED_DIR": ""}, "absolute"), ({k: v for k, v in ENV.items() if k != "ARENA_WATCH_TIEOUT_DIR"}, "ARENA_WATCH_TIEOUT_DIR"),
                     ({**ENV, "ARENA_WATCH_TWINS_FILE": "/a/../b"}, "absolute"), ({**ENV, "ARENA_WATCH_WALLET": "not-base58!"}, "base58"),
                     ({**ENV, "ARENA_WATCH_WALLET": "11111111111111111111111111111111"}, "FUNDED"), ({**ENV, "ARENA_WATCH_WALLET": "11111111111111111111111111111111", "ARENA_WATCH_FUNDED_SOL": "99"}, "range")):
        a = alerts(FakeHost(), env)
        assert set(a) == {"watch_config"} and why in a["watch_config"], (env, a)


def test_systemd_problems():
    assert "systemctl_failed" in alerts(FakeHost(systemctl_rc=1))
    assert "systemctl_failed" in alerts(FakeHost(systemctl_rc="raise"))
    assert "unit_failed" in alerts(FakeHost(active="failed"))


def test_positions_and_halts():
    st = {"open": {"MintAAAAAAAA": {"stuck": True}, "MintBBBBBBBB": {}}, "pending": {}}
    a = alerts(FakeHost(active="inactive", files={STATE: js(st), COUNTERS: js({"halts": {"feed_break": {}, "a3_pins_changed": {}}})}))
    assert "positions_unmanaged" in a and "2 open" in a["positions_unmanaged"]
    assert "position_stuck" in a and "MintAAAA(stuck)" in a["position_stuck"]
    assert a["halts_latched"] == "latched: a3_pins_changed, feed_break"
    for flag in ("abandoned", "strand", "close_blocked"):
        assert "position_stuck" in alerts(FakeHost(files={STATE: js({"open": {"M": {flag: True}}})}))
    assert "halt_file" in alerts(FakeHost(files={W.STATE_DIR / "HALT": b""}))
    assert "halt_file" in alerts(FakeHost(files={W.WALLET_DIR / "HALT": b""}))


def test_unreadable_state_is_an_alert_not_a_pass():
    assert "state_unreadable" in alerts(FakeHost(files={STATE: OSError("symlink")}))
    assert "state_unreadable" in alerts(FakeHost(files={STATE: b"{not json"}))


def test_feed_staleness():
    assert "feed_stale" in alerts(FakeHost(feed_age=W.FEED_STALE_S + 1))
    assert "feed_stale" in alerts(FakeHost(feed_age=None))
    assert "feed_stale" not in alerts(FakeHost(feed_age=W.FEED_STALE_S - 1))


def live_files(extra=None):
    f = {W.LIVE_DROPIN: b"", W.ETC / "LIVE_OK": b"h5v1q50-boostfloor-a1\n"}
    f.update(extra or {})
    return f


def test_gate_open_with_idle_unit():
    assert "gate_open_unit_idle" in alerts(FakeHost(active="inactive", files=live_files()))
    assert "gate_open_unit_idle" not in alerts(FakeHost(active="inactive", files={W.ETC / "LIVE_OK": b"x"}))  # no live drop-in: a dry install


def test_twin_record_freshness_only_live_and_active():
    run = Path(str(TWINS) + ".run.json")
    assert "twin_record_stale" in alerts(FakeHost(files=live_files()))
    assert "twin_record_stale" in alerts(FakeHost(files=live_files({run: js({"run_ms": int((NOW - W.TWIN_STALE_S - 1) * 1000)})})))
    assert "twin_record_stale" not in alerts(FakeHost(files=live_files({run: js({"run_ms": int((NOW - 5) * 1000)})})))
    assert "twin_record_stale" in alerts(FakeHost(files=live_files({run: OSError("x")})))
    assert "twin_record_stale" not in alerts(FakeHost())  # dry install


def test_tieout_missing_after_0200z_once_a_send_happened():
    day0 = int(NOW // 86400 * 86400)
    at = day0 + W.TIEOUT_AFTER_S + 60
    from datetime import datetime, timezone
    prev = datetime.fromtimestamp(day0 - 86400, timezone.utc).strftime("%Y-%m-%d")
    st = {STATE: js({"open": {}, "pending": {}, "first_attempt_ms": (day0 - 3600) * 1000})}
    assert "tieout_missing" in alerts(FakeHost(files=live_files(st)), now=at)
    assert "tieout_missing" not in alerts(FakeHost(files=live_files({**st, TIEOUT / f"tieout-{prev}.json": b"{}"})), now=at)
    assert "tieout_missing" not in alerts(FakeHost(files=live_files(st)), now=day0 + 60)  # before 02:00Z
    late = {STATE: js({"open": {}, "first_attempt_ms": (day0 + 60) * 1000})}
    assert "tieout_missing" not in alerts(FakeHost(files=live_files(late)), now=at)  # first send today: no closed day yet


def test_wallet_balance_optional_and_public():
    env = {**ENV, "ARENA_WATCH_WALLET": "11111111111111111111111111111111", "ARENA_WATCH_FUNDED_SOL": "0.5"}
    h = FakeHost(balance=249_999_999)
    assert "wallet_below_floor" in alerts(h, env)
    assert h.balance_calls == ["11111111111111111111111111111111"]
    assert "wallet_below_floor" not in alerts(FakeHost(balance=250_000_000), env)
    assert "wallet_unreadable" in alerts(FakeHost(balance=RuntimeError("x")), env)
    h2 = FakeHost()
    alerts(h2)
    assert h2.balance_calls == []  # no wallet configured: no call


def test_decide_new_repeat_resolved_restart_and_stop_events():
    lines, st = W.decide({"feed_stale": "x"}, {"restarts": 1, "stop": False}, {}, NOW)
    assert lines == ["ALERT feed_stale: x"]
    lines, st = W.decide({"feed_stale": "x"}, {"restarts": 1, "stop": True}, st, NOW + 60)
    assert lines == ["EVENT STOP placed: new buys are stopped (open positions still exit)"]
    lines, st = W.decide({"feed_stale": "x"}, {"restarts": 3, "stop": True}, st, NOW + W.REPEAT_S + 61)
    assert "ALERT feed_stale: x" in lines and any(x.startswith("ALERT unit_restarted") for x in lines)
    lines, st = W.decide({}, {"restarts": 3, "stop": False}, st, NOW + W.REPEAT_S + 120)
    assert set(lines) == {"RESOLVED feed_stale", "RESOLVED unit_restarted", "EVENT STOP removed"}


def test_run_posts_and_keeps_state_on_a_failed_post(tmp_path):
    posted = []
    sp = tmp_path / "state.json"
    assert W.run(FakeHost(feed_age=None), ENV, lambda hook, c: posted.append((hook, c)), sp, NOW) == 0
    assert posted and posted[0][0] == HOOK and posted[0][1].startswith(W.POST_PREFIX) and "feed_stale" in posted[0][1]

    def boom(hook, c):
        raise RuntimeError("post failed (URLError)")
    sp2 = tmp_path / "s2.json"
    assert W.run(FakeHost(feed_age=None), ENV, boom, sp2, NOW) == 1
    assert not sp2.exists()


def test_bad_webhook_refuses_and_is_never_printed(capsys):
    assert W.run(FakeHost(), {**ENV, "ARENA_WATCH_DISCORD_WEBHOOK": "https://evil.example/x"}, lambda *a: None, Path("/nonexistent/s.json"), NOW) == 2
    out = capsys.readouterr()
    assert "evil" not in out.out + out.err


def test_main_removes_the_webhook_from_the_environment(monkeypatch):
    monkeypatch.setenv(W.WEBHOOK_VAR, "https://evil.example/x")
    import os
    assert W.main(["--test-message"]) == 1
    assert W.WEBHOOK_VAR not in os.environ


def test_units_are_keyless_and_fenced():
    svc = (MF / "mal-h5-arena-watch.service").read_text()
    assert "LoadCredential" not in svc and "--live" not in svc
    assert "InaccessiblePaths=-/etc/mal-h5-arena-key -/etc/mal-probe -/etc/mal-probe-rpc -/run/credentials" in svc
    assert "ExecStart=/usr/bin/python3 -I -S -B -u /usr/local/lib/mal-h5-arena-exec/current/scripts/mal-fast/h5-arena-watch.py" in svc
    assert "OnUnitActiveSec=5min" in (MF / "mal-h5-arena-watch.timer").read_text()
    src = (MF / "h5-arena-watch.py").read_text()
    imports = {line.split()[1].split(".")[0] for line in src.splitlines() if line.startswith(("import ", "from "))}
    assert imports <= {"__future__", "json", "os", "re", "stat", "subprocess", "sys", "time", "urllib", "datetime", "pathlib", "typing"}, imports  # stdlib only (-S)
