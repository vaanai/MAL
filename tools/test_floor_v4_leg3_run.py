"""leg3_run.py (the leg (iii) runner), synthetic fixtures only (the osb_follow test world: a tiny fake tape, a fake CE1-style book followed by osb_follow itself). No real tape, ledger, declog or out/ dir.
Checks: only the allowed fields are printed; exit 0 equal, 1 unequal, 2 on an unresolvable span / seed / pin; an end-to-end v4 offline run on the fixture hours. Nothing here says any book makes money."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import osb_follow as O  # noqa: E402
from tools import floor_lane_host as H  # noqa: E402
from tools.arena_rules_testkit import hour_name  # noqa: E402
from tools.test_osb_follow import T0  # noqa: E402
from tools.test_osb_follow import (EK, N_HOURS, RT, START_HOUR, SEED, _env, link, make_world, manifest, run_once, shifted_rows, world)  # noqa: E402,F401

SPEC = importlib.util.spec_from_file_location("leg3_run", Path(__file__).resolve().parent.parent / "ARTIFACTS" / "lab" / "floor-v4-rule" / "leg3_run.py")
L3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(L3)

ALLOWED = re.compile(r"^(git_head: \S+|hours: first=\S+ last=\S+ count=\d+|seed_from_hour: \S+|B: [0-9a-f]{32}|A: ([0-9a-f]{32}|None)|result: (EQUAL|UNEQUAL)|"
                     r"positions: B_distinct_positions=\d+ A_distinct_positions=\d+ A_entry_rows=\d+|exit_code: [012]|SETUP ERROR: .*)$")
FORBIDDEN = ("price", "fill", "pnl", "exit_reason", "tp_1", "sl", "reason", "ledger_rows")


def book(world_, **over):
    """a CE1-style book (Q_STAR 1e9, TP 1.3) followed over the fixture by osb_follow itself, up to the last fixture hour"""
    tmp, src, names = world_
    link(src, tmp / "tape", names, N_HOURS)
    mp, m = manifest(tmp, **{"book_id": "osb-ce1", "rule_overrides": {"Q_STAR": 1.0e9, "TP": 1.3}, **over})
    assert run_once(tmp, mp) == 0
    # empty files for the seed hours the fixture tape lacks (10-22 .. 10-31), added AFTER the book ran: ArenaState globs the tape dir on every tick, so 240 extra names make the driver run very slow
    for i in range(int((T0 - H.parse_hour(SEED)) // 3_600_000)):
        (tmp / "tape" / hour_name(H.parse_hour(SEED) + i * 3_600_000)).touch()
    return tmp, mp, m


def go(tmp, mp, capsys, **kw):
    capsys.readouterr()  # drop the fixture book's own driver output
    code = L3.run(["--manifest", str(mp), "--work-root", str(tmp / "work")], roots=(str(tmp),), work_allowed=(str(tmp),), manifest_pin=O.manifest_sha256(O.load_manifest(mp)), sleep_fn=lambda s: None, head="deadbeef", **kw)
    cap = capsys.readouterr()
    assert cap.err == "", cap.err  # the job log captures both streams
    return code, cap.out


def check_fields(out):
    lines = out.strip().splitlines()
    assert lines and all(ALLOWED.match(ln) for ln in lines), out
    low = out.lower()
    assert not any(w in low for w in FORBIDDEN), out
    return lines


def test_equal_returns_0_and_prints_only_allowed_fields(world, capsys, monkeypatch):
    tmp, mp, m = book(world)
    b = O.book_decisions_md5(m["out_root"])
    monkeypatch.setattr(L3.C, "normalized_md5", lambda led, ent, md5_fn=None: b)
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([{"kind": "open"}], [{"kind": "entry"}]))
    lines = check_fields(out)
    assert code == 0 and "result: EQUAL" in lines and "exit_code: 0" in lines
    assert "B: %s" % b in lines and "A: %s" % b in lines
    assert "hours: first=%s last=%s count=%d" % (START_HOUR, "2026-11-03T11", 34) in lines  # start 26 h into a 60 h fixture
    assert "seed_from_hour: %s" % SEED in lines and "git_head: deadbeef" in lines
    nb = len({r["position_id"] for r in O.decision_rows(Path(m["out_root"]) / "book" / "ledger") if r.get("kind") in ("open", "trade", "excluded")})
    assert "positions: B_distinct_positions=%d A_distinct_positions=0 A_entry_rows=1" % nb in lines  # the stub's rows carry no position_id


def test_unequal_returns_1(world, capsys, monkeypatch):
    tmp, mp, m = book(world)
    monkeypatch.setattr(L3.C, "normalized_md5", lambda led, ent, md5_fn=None: "0" * 32)
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([], []))
    lines = check_fields(out)
    assert code == 1 and "result: UNEQUAL" in lines and "exit_code: 1" in lines


def test_unmatched_ledger_row_is_unequal_and_prints_none(world, capsys):
    tmp, mp, m = book(world)
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([{"kind": "open", "position_id": "zz", "decision_ms": {"entry": 1}}], []))
    lines = check_fields(out)
    assert code == 1 and "A: None" in lines


def test_no_status_is_exit_2(world, capsys):
    tmp, mp, m = book(world)
    (Path(m["out_root"]) / "status.json").unlink()
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([], []))
    check_fields(out)
    assert code == 2 and "exit_code: 2" in out and "result:" not in out


def test_catching_up_book_is_exit_2(world, capsys):
    tmp, mp, m = book(world)
    sp = Path(m["out_root"]) / "status.json"
    st = json.loads(sp.read_text())
    st["catching_up_to_stored_hours"] = True
    sp.write_text(json.dumps(st))
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([], []))
    check_fields(out)
    assert code == 2


def test_unresolvable_seed_is_exit_2(world, capsys):
    tmp, src, names = world
    mp, m = manifest(tmp, seed_from_hour="2026-11-02T00")  # less than 201 h before start_hour: osb_follow refuses the manifest
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([], []))
    check_fields(out)
    assert code == 2


def test_manifest_pin_mismatch_is_exit_2(world, capsys):
    tmp, mp, m = book(world)
    (Path(m["out_root"]) / "manifest.sha256").write_text("0" * 64 + "\n")
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([], []))
    check_fields(out)
    assert code == 2


def test_status_disagreeing_with_rows_is_exit_2_after_retries(world, capsys):
    tmp, mp, m = book(world)
    sp = Path(m["out_root"]) / "status.json"
    st = json.loads(sp.read_text())
    st["decisions_md5"] = "1" * 32
    sp.write_text(json.dumps(st))
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([], []))
    check_fields(out)
    assert code == 2


def test_missing_tape_hour_is_exit_2(world, capsys):
    tmp, mp, m = book(world)
    (tmp / "tape" / sorted(p.name for p in (tmp / "tape").glob("tape-*"))[30]).unlink()
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([], []))
    check_fields(out)
    assert code == 2


def test_ce1_shaped_fixture_is_EQUAL_with_exit_0(world, capsys):
    """parity, no stub: a CE1-shaped osb_follow book (Q_STAR 1e9, TP 1.3, with entries and exits) vs the real v4 offline run. Must be EQUAL: a structural mismatch would otherwise first show on real data."""
    tmp, mp, m = book(world)
    rows = list(O.decision_rows(Path(m["out_root"]) / "book" / "ledger"))
    assert any(r.get("kind") == "open" for r in rows) and any(r.get("kind") in ("trade", "excluded") for r in rows)  # at least one entry and one exit
    code, out = go(tmp, mp, capsys, table_mode="first_print", engine_kw=EK)
    lines = check_fields(out)
    assert code == 0 and "result: EQUAL" in lines and "exit_code: 0" in lines, out
    a, b = [ln.split(": ")[1] for ln in lines if ln.startswith(("A: ", "B: "))]
    assert a == b
    assert int(re.search(r"A_entry_rows=(\d+)", out).group(1)) > 0
    assert not list((tmp / "work").iterdir())  # the mkdtemp dir is removed


@pytest.mark.parametrize("over", [{"book_id": "osb-x"}, {"host": "hostv2", "lane": None}, {"rule_overrides": {"Q_STAR": 1.0e9}}, {"rule_overrides": {"Q_STAR": 1.0e9, "TP": 1.5}},
                                  {"gap_exclusion": "hold", "twin_of": "osb-ce1x"}, {"gap_exclusion": "off"}, {"twin_of": "osb-ce1x", "gap_exclusion": "hold"}, {"pricing_lat_entry_s": 3.010, "twin_of": "osb-ce1x"}])
def test_manifest_not_ce1_shaped_is_exit_2(world, capsys, over):
    tmp, src, names = world
    mm = {"book_id": "osb-ce1", "rule_overrides": {"Q_STAR": 1.0e9, "TP": 1.3}}
    mm.update(over)
    if mm.get("lane", 1) is None:
        mm.pop("lane")
    mp, m = manifest(tmp, **mm)
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([], []))
    check_fields(out)
    assert code == 2  # our shape check, or the manifest loader's own refusal


def test_manifest_sha_pin_mismatch_is_exit_2(world, capsys):
    tmp, mp, m = book(world)
    capsys.readouterr()
    code = L3.run(["--manifest", str(mp), "--work-root", str(tmp / "work")], roots=(str(tmp),), work_allowed=(str(tmp),), manifest_pin="0" * 64, sleep_fn=lambda s: None, head="d", v4_runner=lambda *a, **k: ([], []))
    out = capsys.readouterr().out
    assert code == 2 and "canonical manifest sha256" in out


def test_missing_seed_hour_is_exit_2(world, capsys):
    tmp, mp, m = book(world)
    (tmp / "tape" / hour_name(H.parse_hour(SEED) + 5 * 3_600_000)).unlink()
    code, out = go(tmp, mp, capsys, v4_runner=lambda *a, **k: ([], []))
    check_fields(out)
    assert code == 2 and "result:" not in out


def test_real_ce1_pin_constant():
    assert L3.CE1_MANIFEST_SHA == "5fe6053311e4dce37f8affa21b7bb9bed9d4cf930ed513d935da7ffaf6c7767a"


def test_each_shape_check_is_named(world):
    base = {"book_id": "osb-ce1", "host": "lane", "rule_overrides": {"Q_STAR": 1.0e9, "TP": 1.3}}
    L3.check_ce1_shape(base, O.manifest_sha256(base))
    for k, v in (("book_id", "x"), ("host", "hostv2"), ("rule_overrides", {}), ("gap_exclusion", "hold"), ("pricing_lat_entry_s", 3.01), ("twin_of", "y")):
        with pytest.raises(L3.SetupError, match="manifest shape"):
            L3.check_ce1_shape({**base, k: v}, O.manifest_sha256(base))


def test_work_root_guard(world, capsys):
    tmp, mp, m = book(world)
    mm = O.load_manifest(mp)
    for bad in ("/var/lib/x", "/etc", mm["out_root"], str(Path(mm["out_root"]) / "w"), mm["tape_dir"], "/tmp/not-allowed-root-x"):
        with pytest.raises(L3.SetupError):
            L3.check_work_root(bad, mm, allowed=(str(tmp),))
    L3.check_work_root(str(tmp / "work"), mm, allowed=(str(tmp),))
    capsys.readouterr()
    code = L3.run(["--manifest", str(mp), "--work-root", "/var/lib/mal-x"], roots=(str(tmp),), work_allowed=(str(tmp),), manifest_pin=O.manifest_sha256(O.load_manifest(mp)), sleep_fn=lambda s: None, head="d", v4_runner=lambda *a, **k: ([], []))
    assert code == 2 and "exit_code: 2" in capsys.readouterr().out


def test_host_error_prints_class_name_only(world, capsys):
    tmp, mp, m = book(world)

    def boom(*a, **k):
        raise L3.R.HostFatal("secret-looking engine text 12345")
    code, out = go(tmp, mp, capsys, v4_runner=boom)
    assert code == 2 and "SETUP ERROR: HostFatal" in out and "12345" not in out


def test_run_v4_writes_only_under_its_workdir(world):
    tmp, mp, m = book(world)
    mm = O.load_manifest(mp)
    before = sorted(str(p) for p in Path(mm["out_root"]).rglob("*"))
    wd = tmp / "wd"
    led, ent = L3.run_v4(mm, O.H.parse_hour(mm["start_hour"]) + 6 * 3_600_000, wd, table_mode="first_print", engine_kw=EK)
    assert sorted(str(p) for p in Path(mm["out_root"]).rglob("*")) == before
    assert wd.is_dir() and isinstance(led, list) and isinstance(ent, list)
