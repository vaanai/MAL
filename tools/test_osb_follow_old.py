"""osb_follow_old: manifest refusals, AGE_DROP_S set only for the call and restored, manifest template passes osb_follow's own checks. Synthetic only; no tape, ledger or key is opened.
Nothing here says any book makes money."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import osb_follow as O  # noqa: E402
import osb_follow_old as OLD  # noqa: E402
from tools import arena_pool_table as PT  # noqa: E402
from tools import arena_rule_l1 as L  # noqa: E402

MANIFEST = Path(__file__).resolve().parent.parent / "docs" / "rotation" / "books" / "osb-old.manifest.json"


def start_manifest(tmp_path, **kw):
    d = json.loads(MANIFEST.read_text())
    d["start_hour"] = "2026-11-23T02"
    d.update(kw)
    p = tmp_path / "m.json"
    p.write_text(json.dumps(d))
    return p


def test_template_loads_through_the_original_checks(tmp_path):
    m = OLD.load_manifest(str(start_manifest(tmp_path)))
    assert m["rule_overrides"] == {"AGE_LO": 720000, "AGE_HI": 1814400} and m["host"] == "lane"


def test_constants():
    assert OLD.AGE_LO_S == 200 * 3600 == 720000 and OLD.AGE_HI_S == 21 * 86400 == 1814400
    assert OLD.AGE_DROP_OLD_S == OLD.AGE_HI_S + 3600 and OLD.AGE_DROP_OLD_S > PT.AGE_DROP_S


def test_seed_shorter_than_505h_is_refused_by_the_original_check(tmp_path):
    with pytest.raises(O.Refused):
        OLD.load_manifest(str(start_manifest(tmp_path, start_hour="2026-11-23T01")))  # 504 h after 2026-11-02T01
    OLD.load_manifest(str(start_manifest(tmp_path, start_hour="2026-11-23T02")))  # exactly 505 h


@pytest.mark.parametrize("kw", [{"rule_overrides": {}}, {"rule_overrides": {"AGE_LO": 720000, "AGE_HI": 1814400, "TP": 1.3}}, {"rule_overrides": {"AGE_LO": 86400, "AGE_HI": 1814400}},
                                {"host": "hostv2"}, {"gap_exclusion": "off"}, {"gap_exclusion": "hold", "twin_of": "osb-a"}])
def test_other_manifests_are_refused(tmp_path, kw):
    with pytest.raises(O.Refused):
        OLD.load_manifest(str(start_manifest(tmp_path, **kw)))


def test_age_drop_is_set_during_the_call_and_restored(tmp_path, monkeypatch):
    seen = {}

    def fake(path, **kw):
        seen["drop"], seen["loader"] = PT.AGE_DROP_S, O.load_manifest
        return 0
    before, orig = PT.AGE_DROP_S, O.load_manifest
    monkeypatch.setattr(O, "follow", fake)
    assert OLD.follow(str(tmp_path / "x.json"), once=True) == 0
    assert seen["drop"] == 21 * 86400 + 3600 and seen["loader"] is OLD.load_manifest
    assert PT.AGE_DROP_S == before and O.load_manifest is orig


def test_age_drop_is_restored_on_error(tmp_path, monkeypatch):
    def boom(path, **kw):
        raise RuntimeError("x")
    before = PT.AGE_DROP_S
    monkeypatch.setattr(O, "follow", boom)
    with pytest.raises(RuntimeError):
        OLD.follow(str(tmp_path / "x.json"))
    assert PT.AGE_DROP_S == before and L.AGE_HI == 200 * 3600  # stock rule constants untouched by this front end itself


def test_title_passes_the_boards_forbidden_words():
    import osb_board
    t = json.loads(MANIFEST.read_text())["title"].lower()
    assert not [w for w in osb_board.FORBIDDEN if w in t]
