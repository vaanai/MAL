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
        for n in ("f.ret_15m", "f.vol_15m", "f.quote_sol", "f.quote_eff_sol", "f.impact_rt_025", "f.impact_rt_050", "f.impact_rt_100", "f.age_s", "f.dd_ath", "risk.quote_dd_peak"):
            assert n in names, n


def test_playbook_states_units():
    t = PLAYBOOK.read_text(encoding="utf-8")
    assert "FRACTION" in t and "-0.05 is -5%" in t and "SECONDS" in t
    for name in ("review_system.txt", "regime_system.txt"):
        s = (ROOT / "hv6_templates" / name).read_text(encoding="utf-8")
        assert "are not percent" in s and "ages are seconds, counts are counts" in s, name
        assert "fractions or SOL" not in s, name
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


def test_playbook_definitions(card):
    """Pin definitions that the name check cannot see (QP 2026-10-26 on #943)."""
    t = PLAYBOOK.read_text(encoding="utf-8")
    assert "non-negative fraction from" not in t and "also a non-negative" not in t
    assert "1 - quote/peak" in t and "last 65 minutes" in t and "negative when the quote is above that peak" in t
    assert "of the round trip" not in t and "FRACTION of the stake" in t
    assert "are null or plain numbers" in t and "null on HV6 cards except" not in t
    assert "risk.creator_sold_frac, risk.same_slot_supply and risk.sniper_share are always null" in t
    assert "risk.top10_frac is null unless the holder lookup succeeded" in t
    assert "f.creator_net_sol_life is the SOL the creator took out of the pool minus what it put in" in t and "not the on-chain holder count" in t
    assert "so f.ret_1m can cover only a few seconds" in t
    assert "counts are counts" in t and "fractions or SOL" not in t
    rev = (ROOT / "hv6_templates" / "review_system.txt").read_text(encoding="utf-8")
    assert "of the round trip" not in rev and "0.64% of the stake" in rev


def test_quote_dd_peak_can_be_negative(card):
    """quote_dd_peak = 1 - q/peak with peak a sampled minute-mark maximum, so a quote above it gives a negative value."""
    snap, prints = T.base_snap(), T.base_prints()
    q = card["f"]["quote_sol"]
    chain = dict(T.base_chain(), quote_sol_peak=q / 2.0)
    c = hc.build_card(snap, prints, chain, as_of_ms=T.T, as_of_slot=T.SLOT,
                      setup=hc.SetupTag(pool_id=snap["id"], type="PULLBACK", persona="CON", vol_15m=9.0, z=1.5))
    assert c["risk"]["quote_dd_peak"] == pytest.approx(-1.0, abs=1e-6)
    assert c["risk"]["quote_dd_peak"] < 0
    assert "negative when the quote is above that peak" in PLAYBOOK.read_text(encoding="utf-8")


def test_quote_history_peak_is_a_65_minute_minute_mark_max():
    mn = 60_000
    qh = R.QuoteHistory()
    assert qh.keep_ms == 65 * mn
    t0 = 1_000_000 * mn
    qh.add("P", t0, 5.0)  # old high, outside the 65-minute window at t0 + 70 min
    for i in range(1, 70):
        qh.add("P", t0 + i * mn, 2.0)
    now = t0 + 70 * mn + 30_000
    assert qh.peak("P", now) == 2.0  # the 5.0 is older than 65 minutes: not a lifetime peak


def test_hv6_chain_never_sets_three_neutral_fields_and_sets_top10_on_success():
    """Pins the playbook's definitions to ChainSource.compose: top1/top10 only from a successful holder call; the other three never."""
    mf = type("MF", (), dict(as_of_ms=1, as_of_slot=2, mint_authority=None, freeze_authority=None, token_program_id="p", extensions=[], ext_info={}))()
    mh = type("MH", (), dict(is_mayhem=False))()
    ok = type("HD", (), dict(ok=True, as_of_ms=5, top1_frac=0.1, top10_frac=0.4))()
    bad = type("HD", (), dict(ok=False, as_of_ms=None, top1_frac=None, top10_frac=None))()
    c_ok, c_bad = R.ChainSource.compose(mf, mh, ok), R.ChainSource.compose(mf, mh, bad)
    assert c_ok["top10_frac"] == 0.4 and "top10_frac" not in c_bad
    for c in (c_ok, c_bad):
        for k in ("creator_sold_frac", "same_slot_supply", "sniper_share"):
            assert k not in c
