"""r4bt_writes: the --writes producer. Synthetic record and a stubbed enumeration: no RPC, no network."""
from __future__ import annotations

import json

import pytest

from tools import a3_redeploy_check as ARC
from tools import r4bt_a3 as A
from tools import r4bt_writes as W

RUN = "2026-11-08T07:11:00Z"
PINS = {"programs": {"pump": {"deploy_slot": 100}, "pumpswap": {"deploy_slot": 200}, "fees": {"deploy_slot": 300}}, "accounts": {}, "pinned_utc": "2026-11-01T00:00:00Z"}


def rec(pump=100, pumpswap=200, fees=300):
    mk = lambda n, s: {"deploy_slot": s, "deploy_utc": "2026-11-08T05:00:00Z", "programdata": f"PD{n}"}  # noqa: E731
    return {"run_utc": RUN, "programs": {"pump": mk("pump", pump), "pumpswap": mk("pumpswap", pumpswap), "fees": mk("fees", fees)}, "accounts": {},
            "halt": {"flags": {"pins_changed": {"halt": True}}}}


class Client:
    pass


def writes_for(table):
    def fake(client, name, programdata, pinned_slot, newest_slot):
        w = table[name]
        if isinstance(w, Exception):
            raise w
        return [{"kind": "program", "name": name, "old": pinned_slot, "new": s, "deploy_utc": u, "sig": "x"} for s, u in w]

    return fake


def test_lists_every_write_back_to_the_pinned_slot_oldest_first(monkeypatch):
    monkeypatch.setattr(ARC, "list_upgrade_writes", writes_for({"pump": [(150, "2026-11-08T03:46:00Z"), (180, "2026-11-08T05:47:00Z")]}))
    out, failed = W.produce(rec(pump=180), PINS, Client())
    assert failed == {} and out == [{"name": "pump", "slot": 150, "at_ms": ARC.utc_ms("2026-11-08T03:46:00Z")}, {"name": "pump", "slot": 180, "at_ms": ARC.utc_ms("2026-11-08T05:47:00Z")}]
    assert A.earliest_writes(out)["pump"] == ARC.utc_ms("2026-11-08T03:46:00Z")  # the classifier takes the FIRST write, not the record's deploy_utc (05:00Z)


def test_no_difference_is_an_empty_list_and_no_rpc_call(monkeypatch):
    monkeypatch.setattr(ARC, "list_upgrade_writes", lambda *a: pytest.fail("no enumeration without a difference"))
    assert W.produce(rec(), PINS, Client()) == ([], {})


def test_programs_outside_the_list_are_not_enumerated(monkeypatch):
    p = dict(PINS, programs=dict(PINS["programs"], other={"deploy_slot": 1}))
    r = rec()
    r["programs"]["other"] = {"deploy_slot": 2, "deploy_utc": RUN, "programdata": "PDo"}
    monkeypatch.setattr(ARC, "list_upgrade_writes", lambda *a: pytest.fail("not for other"))
    assert W.produce(r, p, Client()) == ([], {})


def test_a_failed_enumeration_is_named_by_class_and_the_program_is_absent(monkeypatch):
    monkeypatch.setattr(ARC, "list_upgrade_writes", writes_for({"pump": ARC.CheckError("pinned slot not reached secret-url"), "fees": [(310, "2026-11-08T04:00:00Z")]}))
    out, failed = W.produce(rec(pump=190, fees=310), PINS, Client())
    assert failed == {"pump": "CheckError"} and [w["name"] for w in out] == ["fees"]


def test_an_enumeration_not_ending_at_the_records_slot_or_empty_is_a_failure(monkeypatch):
    monkeypatch.setattr(ARC, "list_upgrade_writes", writes_for({"pump": [(150, "2026-11-08T03:46:00Z")]}))
    out, failed = W.produce(rec(pump=180), PINS, Client())
    assert out == [] and failed == {"pump": "CheckError"}
    monkeypatch.setattr(ARC, "list_upgrade_writes", writes_for({"pump": []}))
    assert W.produce(rec(pump=180), PINS, Client())[1] == {"pump": "CheckError"}
    monkeypatch.setattr(ARC, "list_upgrade_writes", writes_for({"pump": [(90, "2026-11-08T03:46:00Z"), (180, "2026-11-08T05:47:00Z")]}))
    assert W.produce(rec(pump=180), PINS, Client())[1] == {"pump": "CheckError"}  # a write at or before the pinned slot


def test_a_record_without_programdata_fails_closed_for_that_program(monkeypatch):
    r = rec(pump=180)
    del r["programs"]["pump"]["programdata"]
    monkeypatch.setattr(ARC, "list_upgrade_writes", lambda *a: pytest.fail("never called"))
    assert W.produce(r, PINS, Client()) == ([], {"pump": "CheckError"})


def test_main_writes_the_list_file_never_overwrites_and_exit_codes(tmp_path, monkeypatch, capsys):
    daily, pins, out = tmp_path / "daily.jsonl", tmp_path / "pins.json", tmp_path / "w.json"
    daily.write_text(json.dumps(rec(pump=180)) + "\n")
    pins.write_text(json.dumps(PINS))
    monkeypatch.setattr(ARC, "list_upgrade_writes", writes_for({"pump": [(180, "2026-11-08T05:47:00Z")]}))
    argv = ["--daily", str(daily), "--monitor-pins", str(pins), "--out", str(out)]
    assert W.main(argv, client=Client()) == 0
    assert json.loads(out.read_text()) == [{"name": "pump", "slot": 180, "at_ms": ARC.utc_ms("2026-11-08T05:47:00Z")}]
    assert W.main(argv, client=Client()) == 2  # exists: refused
    assert W.main(["--daily", str(daily), "--monitor-pins", str(pins), "--run-utc", "2020-01-01T00:00:00Z"], client=Client()) == 2
    monkeypatch.setattr(ARC, "list_upgrade_writes", writes_for({"pump": ARC.CheckError("x")}))
    assert W.main(["--daily", str(daily), "--monitor-pins", str(pins)], client=Client()) == 3
    assert "PARTIAL" in capsys.readouterr().err


def test_the_classifier_dates_a_pin_change_to_the_earliest_write_from_this_producers_file(monkeypatch):
    monkeypatch.setattr(ARC, "list_upgrade_writes", writes_for({"fees": [(310, "2026-11-08T03:00:00Z"), (320, "2026-11-08T06:00:00Z")]}))
    ws, _ = W.produce(rec(fees=320), PINS, Client())
    v = A.classify(rec(fees=320), PINS, now_ms=ARC.utc_ms(RUN) + 1000, writes=ws)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == ARC.utc_ms("2026-11-08T03:00:00Z")
