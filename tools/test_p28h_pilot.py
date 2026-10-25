import numpy as np

from tools import p28h_pilot as p


def test_auc_and_rank_ties():
    y = np.array([0, 0, 1, 1.0])
    assert abs(p.auc(np.array([1, 2, 3, 4.0]), y) - 1.0) < 1e-9
    assert abs(p.auc(np.array([1, 1, 1, 1.0]), y) - 0.5) < 1e-9


def test_kept_rate_weighted_veto():
    y = np.array([0, 0, 1, 1.0])
    w = np.ones(4)
    s = np.array([1, 2, 3, 4.0])
    assert abs(p.kept_rate(s, y, w, 0.5) - 1.0) < 1e-9
    assert abs(p.kept_rate(s, y, w, 0.0) - 0.5) < 1e-9


def test_parse_obj_and_split():
    assert p.parse_obj('```json\n{"score": 12}\n```')["score"] == 12
    assert p.parse_obj('{"score": 101}') is None and p.parse_obj("nope") is None
    assert p.split_prompt("SYSTEM\nabc\n\nUSER (x)\nfoo") == "abc"


def test_user_text_second_call_reorders():
    card = {"id": "p1", **{k: k + "v" for k in p.FIELDS}}
    a, b = p.user_text(card, False), p.user_text(card, True)
    assert a != b and a.index("name:") < a.index("website:") and b.index("website:") < b.index("name:")
