"""Tests for tools/exp027_a3_record.py on SYNTHETIC monitor lines (no host file is read).

  /data/mal/venv/bin/python -m pytest tools/test_exp027_a3_record.py
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from tools import exp027_a3_record as ar
from tools import r4b_read as rr

PIN_REASON = ("program_pump deploy_slot 456494698 -> 458921440 (2026-10-19T17:47:02Z); "
              "program_pumpswap deploy_slot 454596406 -> 458921517 (2026-10-19T17:47:20Z)")


def flags(**over):
    f = {n: {"halt": False, "evaluated": True, "reason": "ok"} for n in ar.SIX_FLAGS}
    for k, v in over.items():
        f[k] = {**f[k], **v}
    return f


def line(t: str, **over) -> str:
    return json.dumps({"schema": "x", "run_utc": t, "run_unix": 0, "halt": {"any": False, "flags": flags(**over)}, "warn": {}})


def daily(start=datetime(2026, 10, 17, 7, 11, 5, tzinfo=timezone.utc), days=22, special=None) -> list[str]:
    out = []
    for i in range(days):
        t = ar.iso(start + timedelta(days=i))
        out.append(line(t, **(special or {}).get(t[:10], {})))
    return out


def write(tmp_path, lines):
    p = tmp_path / "daily.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_ruling_halts_are_listed_and_load_a3_sees_them(tmp_path):
    lines = daily(special={"2026-10-29": {"synthetic_share_high": {"halt": True, "reason": "PostCompleteBuyEvent on 9/19 = 0.474 of graduations (halt above 0.35)"}}})
    lines.insert(3, line("2026-10-20T07:11:27Z", pins_changed={"halt": True, "reason": PIN_REASON}))
    lines.insert(5, line("2026-10-21T17:55:29Z", pins_changed={"halt": True, "reason": PIN_REASON}))
    src = write(tmp_path, lines)
    out = tmp_path / "rec" / "exp027-a3-record.json"
    sha, rec = ar.run(src, out)
    ats = [(h["at"], h["reason"].split(":")[0]) for h in rec["halts"]]
    assert ("2026-10-20T07:11:27Z", "pins_changed") in ats and ("2026-10-21T17:55:29Z", "pins_changed") in ats
    assert ("2026-10-29T07:11:05Z", "synthetic_share_high") in ats
    assert rec["halts"][0]["reason"] == "pins_changed: " + PIN_REASON
    assert rec["checked_from"] == "2026-10-18T03:00:00Z" and rec["checked_through"] == "2026-11-07T07:11:05Z"
    assert rec["schema"] == "r4b_a3_v1" and rec["source"].endswith("@" + __import__("hashlib").sha256(src.read_bytes()).hexdigest())
    assert not (os.stat(out).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    probs: list[str] = []
    inside = rr.load_a3({"EXP027_A3_RECORD": f"{out}@{sha}"}, ar.W0, ar.E, probs)
    assert probs == [] and len(inside) == len(rec["halts"]) >= 3


def test_clean_window_has_no_halts(tmp_path):
    sha, rec = ar.run(write(tmp_path, daily()), tmp_path / "r.json")
    assert rec["halts"] == [] and len(rec["runs_in_window"]) == 20  # 10-18 .. 11-06 at 07:11Z; 10-17 is before W0, 11-07 after E


def test_runs_outside_the_window_do_not_count(tmp_path):
    lines = daily(special={"2026-10-17": {"pins_changed": {"halt": True, "reason": "before W0"}}})
    lines.append(line("2026-11-08T07:11:05Z", synthetic_share_high={"halt": True, "reason": "after E"}))
    _, rec = ar.run(write(tmp_path, lines), tmp_path / "r.json")
    assert rec["halts"] == []


def test_two_consecutive_unevaluated_core_dates_halt(tmp_path):
    unev = {"boost_share_low": {"evaluated": False, "reason": "only 3 sampled"}}
    _, rec = ar.run(write(tmp_path, daily(special={"2026-10-24": unev, "2026-10-25": unev})), tmp_path / "r.json")
    assert [h["at"] for h in rec["halts"]] == ["2026-10-25T07:11:05Z"]
    assert rec["halts"][0]["reason"].startswith("core_rule_unevaluated: boost_share_low")


def test_one_unevaluated_date_is_not_a_halt(tmp_path):
    unev = {"boost_last_slice_early": {"evaluated": False, "reason": "x"}}
    _, rec = ar.run(write(tmp_path, daily(special={"2026-10-24": unev, "2026-10-26": unev})), tmp_path / "r.json")
    assert rec["halts"] == []


def test_a_missing_date_counts_as_unevaluated(tmp_path):
    lines = [x for x in daily() if '"run_utc": "2026-10-27' not in x]
    unev = {"boost_share_low": {"evaluated": False, "reason": "x"}}
    lines = [line("2026-10-26T07:11:05Z", **unev) if '"run_utc": "2026-10-26' in x else x for x in lines]
    _, rec = ar.run(write(tmp_path, lines), tmp_path / "r.json")
    assert [h["at"] for h in rec["halts"]] == ["2026-10-27T23:59:59Z"]


def test_first_run_after_e_adds_a_deploy_instant_inside_the_window(tmp_path):
    lines = daily(days=21)  # through 11-06
    lines.append(line("2026-11-07T07:11:05Z", pins_changed={"halt": True, "reason": "program_pump deploy_slot 1 -> 2 (2026-11-07T01:30:00Z)"}))
    _, rec = ar.run(write(tmp_path, lines), tmp_path / "r.json")
    assert [h["at"] for h in rec["halts"]] == ["2026-11-07T01:30:00Z"]


def test_refuses_without_a_run_after_e(tmp_path):
    with pytest.raises(ar.Refused, match="no monitor run at or after E"):
        ar.run(write(tmp_path, daily(days=21)), tmp_path / "r.json")


@pytest.mark.parametrize("bad", ["{not json", json.dumps({"run_utc": "2026-10-20", "halt": {"flags": {}}}), json.dumps({"run_utc": "2026-10-20T07:11:27Z"}),
                                 json.dumps({"run_utc": "2026-10-20T07:11:27Z", "halt": {"flags": {"pins_changed": {"halt": "yes"}}}})])
def test_fail_closed_on_a_bad_line(tmp_path, bad):
    lines = daily()
    lines.insert(4, bad)
    out = tmp_path / "r.json"
    with pytest.raises(ar.Refused):
        ar.run(write(tmp_path, lines), out)
    assert not out.exists()


def test_write_once(tmp_path):
    out = tmp_path / "r.json"
    out.write_text("{}")
    with pytest.raises(ar.Refused, match="write-once"):
        ar.run(write(tmp_path, daily()), out)


def test_cli_prints_the_window_value(tmp_path, capsys):
    out = tmp_path / "r.json"
    assert ar.main(["--src", str(write(tmp_path, daily())), "--out", str(out)]) == 0
    assert f"EXP027_A3_RECORD value: {out.resolve()}@" in capsys.readouterr().out
    assert ar.main(["--src", str(write(tmp_path, daily())), "--out", str(out)]) == 2


def _interim(tmp_path, halts):
    p = tmp_path / "interim.json"
    p.write_text(json.dumps({"schema": "r4b_a3_v1", "halts": halts}))
    return p


def test_interim_entries_must_be_present_unchanged(tmp_path):
    lines = daily()
    lines.insert(3, line("2026-10-20T07:11:27Z", pins_changed={"halt": True, "reason": PIN_REASON}))
    src = write(tmp_path, lines)
    good = _interim(tmp_path, [{"at": "2026-10-20T07:11:27Z", "reason": "pins_changed: " + PIN_REASON}])
    _, rec = ar.run(src, tmp_path / "a.json", interim=good)
    assert rec["interim"].endswith(__import__("hashlib").sha256(good.read_bytes()).hexdigest())
    changed = _interim(tmp_path, [{"at": "2026-10-20T07:11:27Z", "reason": "pins_changed: something else"}])
    with pytest.raises(ar.Refused, match="missing or changed"):
        ar.run(src, tmp_path / "b.json", interim=changed)
    assert not (tmp_path / "b.json").exists()
