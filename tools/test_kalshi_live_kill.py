import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pytest

import kalshi_live_kill as kk
import kalshi_live_log as kl


class Env:
    def __init__(self, tmp_path, fail_cancels=0):
        self.t = 1_000_000.0
        self.cancels = 0
        self.fail = fail_cancels
        self.stop = tmp_path / "STOP"
        self.hb = tmp_path / "heartbeat.json"
        self.log = kl.EventLog(tmp_path / "ev.jsonl", clock=lambda: self.t)
        self.ks = kk.KillSwitch(self.stop, self.hb, self.cancel, self.log, clock=lambda: self.t)

    def cancel(self):
        self.cancels += 1
        if self.fail > 0:
            self.fail -= 1
            raise RuntimeError("exchange down")

    def beat(self):
        kk.write_heartbeat(self.hb, clock=lambda: self.t)

    def events(self, name=None):
        evs = self.log.read()
        return [e for e in evs if name is None or e["event"] == name]


def test_fresh_heartbeat_and_no_stop_allows(tmp_path):
    e = Env(tmp_path)
    e.beat()
    assert e.ks.check() == kk.Verdict(True) and e.cancels == 0 and e.events() == []


def test_stop_file_cancels_all_immediately_then_refuses_without_resending(tmp_path):
    e = Env(tmp_path)
    e.beat()
    e.stop.write_text("")
    v = e.ks.check()
    assert (v.allowed, v.reason, v.cancel_ok) == (False, "stop_file", True) and e.cancels == 1
    assert e.ks.check().allowed is False and e.cancels == 1          # no repeated cancel-all
    assert [x["event"] for x in e.events()][:3] == ["kill_trip", "cancel_all_ok", "refusal"]
    assert e.events("kill_trip")[0]["reason"] == "stop_file"


def test_stop_beats_a_fresh_heartbeat_and_new_stop_is_a_new_episode(tmp_path):
    e = Env(tmp_path)
    e.beat()
    e.stop.write_text("")
    e.ks.check()
    e.stop.unlink()
    assert e.ks.check().allowed is True
    e.stop.write_text("")
    assert e.ks.check().reason == "stop_file" and e.cancels == 2


def test_stop_file_works_even_if_it_is_a_dangling_symlink(tmp_path):
    e = Env(tmp_path)
    e.beat()
    e.stop.symlink_to(tmp_path / "nowhere")
    assert e.ks.check().reason == "stop_file"


def test_stale_heartbeat_over_90s_cancels_all_boundary_inclusive(tmp_path):
    e = Env(tmp_path)
    e.beat()
    e.t += 90.0
    assert e.ks.check().allowed is True and e.cancels == 0          # exactly 90 s is not older than 90 s
    e.t += 0.5
    v = e.ks.check()
    assert (v.allowed, v.reason, v.cancel_ok) == (False, "heartbeat_stale", True) and e.cancels == 1
    assert e.events("kill_trip")[0]["age"] == 90.5
    e.ks.check()
    assert e.cancels == 1
    e.beat()                                                         # watchdog recovers
    assert e.ks.check().allowed is True
    e.t += 120
    assert e.ks.check().reason == "heartbeat_stale" and e.cancels == 2


@pytest.mark.parametrize("content,reason", [(None, "heartbeat_missing"), ("garbage", "heartbeat_invalid"),
                                            ('{"ts": "now"}', "heartbeat_invalid"), ('{"ts": true}', "heartbeat_invalid"),
                                            ('{"x": 1}', "heartbeat_invalid")])
def test_missing_or_bad_heartbeat_fails_closed(tmp_path, content, reason):
    e = Env(tmp_path)
    if content is not None:
        e.hb.write_text(content)
    v = e.ks.check()
    assert (v.allowed, v.reason) == (False, reason) and e.cancels == 1


def test_future_dated_heartbeat_is_invalid(tmp_path):
    e = Env(tmp_path)
    e.hb.write_text(json.dumps({"ts": e.t + 3600}))
    assert e.ks.check().reason == "heartbeat_invalid"
    e.hb.write_text(json.dumps({"ts": e.t + 2}))                     # small skew tolerated
    assert e.ks.check().allowed is True


def test_failed_cancel_is_retried_until_it_succeeds_and_still_refuses(tmp_path):
    e = Env(tmp_path, fail_cancels=2)
    e.beat()
    e.stop.write_text("")
    assert e.ks.check().cancel_ok is False and e.ks.check().cancel_ok is False
    v = e.ks.check()
    assert (v.allowed, v.cancel_ok, e.cancels) == (False, True, 3)
    assert len(e.events("cancel_all_failed")) == 2 and len(e.events("cancel_all_ok")) == 1
    assert e.events("cancel_all_failed")[0]["error"] == "RuntimeError"
    assert len(e.events("kill_trip")) == 1                           # one trip event per episode


def test_every_refusal_and_cancel_is_in_the_jsonl_log(tmp_path):
    e = Env(tmp_path)
    e.ks.check()                                                     # missing heartbeat
    e.beat()
    e.stop.write_text("")
    e.ks.check()
    lines = [json.loads(x) for x in (tmp_path / "ev.jsonl").read_text().splitlines()]
    assert [(x["event"], x.get("reason") or x.get("code")) for x in lines] == [
        ("kill_trip", "heartbeat_missing"), ("cancel_all_ok", "heartbeat_missing"), ("refusal", "heartbeat_missing"),
        ("kill_trip", "stop_file"), ("cancel_all_ok", "stop_file"), ("refusal", "stop_file")]


def test_watchdog_tick_is_the_same_check(tmp_path):
    e = Env(tmp_path)
    assert e.ks.watchdog_tick().reason == "heartbeat_missing" and e.cancels == 1


def test_heartbeat_roundtrip_is_atomic_and_readable(tmp_path):
    p = tmp_path / "hb.json"
    assert kk.read_heartbeat(p) == (None, "heartbeat_missing")
    kk.write_heartbeat(p, clock=lambda: 42.5)
    assert kk.read_heartbeat(p) == (42.5, None) and not (tmp_path / "hb.json.tmp").exists()
