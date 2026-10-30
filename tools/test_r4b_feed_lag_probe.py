"""Offline tests for tools/r4b_feed_lag_probe.py: synthetic notifications only, no socket, no key."""
from __future__ import annotations

import json

import pytest

from tools import r4b_feed_lag_probe as P

M = 1_793_400_000  # a multiple of 600


@pytest.fixture(autouse=True)
def fake_decode(monkeypatch):
    """records_from_logs stand-in: a log line 'ts=<event_ts>' gives one trade with that event_ts."""
    def rec(logs, **kw):
        return [{"event_ts": int(x[3:])} for x in logs if x.startswith("ts=")]
    monkeypatch.setattr(P.td, "records_from_logs", rec)


def slot_note(slot):
    return json.dumps({"jsonrpc": "2.0", "method": "slotNotification", "params": {"result": {"slot": slot, "parent": slot - 1, "root": slot - 32}, "subscription": 1}})


def logs_note(slot, ts, err=None):
    return json.dumps({"jsonrpc": "2.0", "method": "logsNotification",
                       "params": {"result": {"context": {"slot": slot}, "value": {"signature": "x", "err": err, "logs": [f"ts={ts}"]}}, "subscription": 2}})


def test_analyse_lags_in_slots_and_ms():
    w = P.Window(M, "processed")
    w.connect_ms = (M - 30) * 1000
    t = (M - 2) * 1000
    for i, s in enumerate(range(100, 120)):  # tip advances one slot per 400 ms from 2 s before M
        w.handle(slot_note(s), t + 400 * i)
    w.handle(logs_note(104, M - 1), t + 400 * 4 + 900)   # a pre-sd print (event_ts in [M-5, M)), seen late: tip then is 106
    w.handle(logs_note(105, M), t + 400 * 5 + 600)       # sd = 105 (first slot with event_ts >= M), seen when the tip is 106
    w.handle(logs_note(107, M), t + 400 * 7 + 100)
    w.handle(logs_note(106, M, err={"x": 1}), t + 400 * 8)  # failed tx: ignored
    r = P.analyse(w)
    assert r["sd_found"] and r["pre_sd_prints"] == 1
    assert r["sd_lag_slots"] == 106 - 105
    assert r["ready_lag_slots"] == 106 - 105  # ready = max(sd seen 2600, pre-sd seen 2500) -> tip 106
    assert r["sd_lag_ms"] == (t + 2600) - (t + 2000)
    assert r["sd_seen_after_mark_ms"] == 600 and r["first_note_after_connect_ms"] == 28_000
    assert r["prints"] == 3 and r["logs_notes"] == 4 and r["event_lag_slots_max"] == 2  # the pre-sd print 104 seen at tip 106


def test_late_pre_sd_print_moves_ready():
    w = P.Window(M, "confirmed")
    t = (M - 1) * 1000
    for i, s in enumerate(range(200, 230)):
        w.handle(slot_note(s), t + 400 * i)
    w.handle(logs_note(203, M), t + 1300)        # sd = 203, tip 203
    w.handle(logs_note(201, M - 2), t + 4100)    # a pre-sd print arriving 7 slots later (tip 210)
    r = P.analyse(w)
    assert r["sd_lag_slots"] == 0 and r["ready_lag_slots"] == 210 - 203
    assert r["ready_lag_ms"] == (t + 4100) - (t + 1200)


def test_no_print_after_mark_is_not_found_and_no_crash():
    w = P.Window(M, "processed")
    w.handle(slot_note(1), M * 1000)
    w.handle(logs_note(1, M - 3), M * 1000 + 5)
    w.handle("not json", M * 1000 + 6)
    r = P.analyse(w)
    assert r["sd_found"] is False and r["errors"] == 1


def test_summary_credits_follow_probe_1891_pricing():
    rows = [{"mark": M, "hour_mark": False, "commitment": "processed", "bytes": 2_000_000, "prints": 5, "sd_found": True, "sd_lag_slots": 2, "ready_lag_slots": 3,
             "sd_lag_ms": 500, "ready_lag_ms": 900, "ready_after_mark_ms": 1500, "event_lag_slots_p50": 1, "event_lag_slots_p90": 2, "first_note_after_connect_ms": 300},
            {"mark": M + 600, "hour_mark": True, "commitment": "processed", "bytes": 4_000_000, "prints": 7, "sd_found": False, "event_lag_slots_p50": 1,
             "event_lag_slots_p90": 3, "first_note_after_connect_ms": 400}]
    s = P.summarise(rows, 1200)["by_commitment"]["processed"]
    assert s["windows"] == 2 and s["windows_sd_found"] == 1 and s["hour_marks"] == 1
    assert s["credits_at_2_per_0.1MB"] == 120.0 and s["credits_at_3_per_0.1MB"] == 180.0  # 6 MB = 60 x 0.1 MB
    assert s["credits_per_window_at_2"] == 60.0 and s["credits_per_day_24_marks_at_2"] == 1440.0
    assert s["ready_lag_slots"]["p50"] == 3 and s["event_lag_slots_p90"]["n"] == 2


def test_requests_cover_both_programs_and_the_tip():
    r = P.requests("processed")
    assert r[0]["method"] == "slotSubscribe"
    assert {q["params"][0]["mentions"][0] for q in r[1:]} == {P.td.PUMPSWAP_PROGRAM, P.td.PUMP_BONDING_PROGRAM}
    assert all(q["params"][1]["commitment"] == "processed" for q in r[1:])


def test_key_reading_and_usage(tmp_path):
    f = tmp_path / "k.env"
    f.write_text("export HELIUS_API_KEY='abcd-1234-efgh'\n")
    assert P.read_key(str(f)) == "abcd-1234-efgh"
    f.write_text("HELIUS_API_KEY=bad key!\n")
    assert P.read_key(str(f)) is None
    assert P.main(["--key-file", str(f), "--seconds", "60"]) == 2
    assert P.main(["--key-file", str(f), "--interval", "700"]) == 2
    assert P.main(["--key-file", str(f)]) == 11


def test_no_content_in_window_output():
    w = P.Window(M, "processed")
    w.handle(slot_note(5), M * 1000)
    w.handle(logs_note(5, M), M * 1000 + 10)
    r = P.analyse(w)
    assert "signature" not in json.dumps(r) and set(r) >= {"bytes", "sd_lag_slots", "commitment"}
