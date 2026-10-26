"""Every card field named in the HV6 playbook and the HV6 review templates exists in a real hv4_cards.build_card output, and the playbook's
WATCH example parses and evaluates with the WATCH parser the HV6 runner uses (hv4_validate.parse_watch_predicate + hv6_runner._OPS)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import tools.hv4_cards as hc
import tools.hv6_agents as A
import tools.hv6_runner as R
from tools import test_hv4_cards as T

ROOT = Path(__file__).resolve().parent
PLAYBOOK = ROOT / "hv6_playbooks" / "GEN_v0.txt"
TEMPLATES = sorted((ROOT / "hv6_templates").glob("*.txt"))
BANNED = ("real_quote", "age_h", "impact_025", "impact_05 ", "impact_10", "impact_<")
FIELD = re.compile(r"(?<![\w.])((?:f|risk|setup|tops)\.[a-z][a-z0-9_]*)")


@pytest.fixture(scope="module")
def card():
    return hc.build_card(T.base_snap(), T.base_prints(), T.base_chain(), as_of_ms=T.T, as_of_slot=T.SLOT,
                         setup=hc.SetupTag(pool_id=T.base_snap()["id"], type="PULLBACK", persona="CON", vol_15m=9.0, z=1.5))


def _paths(node, prefix=""):
    out = set()
    for k, v in node.items():
        out.add(prefix + k)
        if isinstance(v, dict):
            out |= _paths(v, prefix + k + ".")
    return out


def test_no_old_names():
    for p in [PLAYBOOK, *TEMPLATES]:
        text = p.read_text(encoding="utf-8")
        for b in BANNED:
            assert b not in text, (p.name, b)


@pytest.mark.parametrize("path", [PLAYBOOK, *TEMPLATES], ids=lambda p: p.name)
def test_every_named_field_is_on_a_real_card(path, card):
    have = _paths(card)
    names = set(FIELD.findall(path.read_text(encoding="utf-8")))
    for n in names:
        assert n in have, f"{path.name} names {n}, not on a build_card card"
    if path == PLAYBOOK:  # the playbook must name the fields it describes
        for n in ("f.ret_15m", "f.vol_15m", "f.quote_sol", "f.quote_eff_sol", "f.impact_rt_025", "f.impact_rt_050", "f.impact_rt_100", "f.age_s"):
            assert n in names, n


def test_playbook_states_units():
    t = PLAYBOOK.read_text(encoding="utf-8")
    assert "FRACTION" in t and "-0.05 is -5%" in t and "SECONDS" in t
    for name in ("review_system.txt", "regime_system.txt"):
        assert "fractions or SOL, not percent" in (ROOT / "hv6_templates" / name).read_text(encoding="utf-8"), name
    assert "net percent" in (ROOT / "hv6_templates" / "review_system.txt").read_text(encoding="utf-8")


def _watch_example() -> str:
    m = re.search(r"^WATCH: (\{.*\})$", PLAYBOOK.read_text(encoding="utf-8"), re.M)
    assert m
    return json.loads(m.group(1))["watch"]["when"]


def test_watch_example_parses_and_evaluates(card):
    when = _watch_example()
    assert when == "f.ret_15m > 0.05 AND f.vol_15m > 20"
    nums = A.v4.numeric_fields(card)
    terms = A.v4.parse_watch_predicate(when, nums)
    assert [t[0] for t in terms] == ["f.ret_15m", "f.vol_15m"]  # dotted names kept, not rewritten
    assert not all(R._OPS[op](nums[f], n) for f, op, n in terms)  # base card: ret_15m 0.0, vol_15m 9.0
    hot = {**card, "f": {**card["f"], "ret_15m": 0.08, "vol_15m": 25.0}}
    nh = A.v4.numeric_fields(hot)
    assert all(R._OPS[op](nh[f], n) for f, op, n in A.v4.parse_watch_predicate(when, nh))
    # the same predicate through the runner's own local-veto function
    assert R.local_veto([when], hot) == when
    assert R.local_veto([when], card) is None
