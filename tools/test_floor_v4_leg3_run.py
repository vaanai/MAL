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
from tools.test_osb_follow import (EK, N_HOURS, RT, START_HOUR, SEED, _env, link, make_world, manifest, run_once, shifted_rows, world)  # noqa: E402,F401

SPEC = importlib.util.spec_from_file_location("leg3_run", Path(__file__).resolve().parent.parent / "ARTIFACTS" / "lab" / "floor-v4-rule" / "leg3_run.py")
L3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(L3)

ALLOWED = re.compile(r"^(git_head: \S+|hours: first=\S+ last=\S+ count=\d+|seed_from_hour: \S+|B: [0-9a-f]{32}|A: ([0-9a-f]{32}|None)|result: (EQUAL|UNEQUAL)|"
                     r"rows: B_ledger_rows=\d+ A_ledger_rows=\d+ A_entry_rows=\d+|exit_code: [012]|SETUP ERROR: .*)$")
FORBIDDEN = ("price", "fill", "pnl", "exit_reason", "tp_1", "sl", "reason")


def book(world_, **over):
    """a CE1-style book (Q_STAR 1e9, TP 1.3) followed over the fixture by osb_follow itself, up to the last fixture hour"""
    tmp, src, names = world_
    link(src, tmp / "tape", names, N_HOURS)
    mp, m = manifest(tmp, rule_overrides={"Q_STAR": 1.0e9, "TP": 1.3}, **over)
    assert run_once(tmp, mp) == 0
    return tmp, mp, m


def go(tmp, mp, capsys, **kw):
    capsys.readouterr()  # drop the fixture book's own driver output
    code = L3.run(["--manifest", str(mp), "--work-root", str(tmp / "work")], roots=(str(tmp),), sleep_fn=lambda s: None, head="deadbeef", **kw)
    out = capsys.readouterr().out
    return code, out


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
    assert "rows: B_ledger_rows=%d A_ledger_rows=1 A_entry_rows=1" % len(list(O.decision_rows(Path(m["out_root"]) / "book" / "ledger"))) in lines


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


def test_end_to_end_v4_offline_run_on_the_fixture(world, capsys):
    """no stub: the real v4 host path over the fixture hours; the result is whatever it is (EQUAL or UNEQUAL), but the run completes, prints only allowed fields and cleans its work dir"""
    tmp, mp, m = book(world)
    code, out = go(tmp, mp, capsys, table_mode="first_print", engine_kw=EK)
    lines = check_fields(out)
    assert code in (0, 1), out
    assert any(ln.startswith("A: ") and ln != "A: None" for ln in lines)
    a_entries = int(re.search(r"A_entry_rows=(\d+)", out).group(1))
    assert a_entries > 0
    assert not list((tmp / "work").iterdir())  # the mkdtemp dir is removed


def test_run_v4_writes_only_under_its_workdir(world):
    tmp, mp, m = book(world)
    mm = O.load_manifest(mp)
    before = sorted(str(p) for p in Path(mm["out_root"]).rglob("*"))
    wd = tmp / "wd"
    led, ent = L3.run_v4(mm, O.H.parse_hour(mm["start_hour"]) + 6 * 3_600_000, wd, table_mode="first_print", engine_kw=EK)
    assert sorted(str(p) for p in Path(mm["out_root"]).rglob("*")) == before
    assert wd.is_dir() and isinstance(led, list) and isinstance(ent, list)
