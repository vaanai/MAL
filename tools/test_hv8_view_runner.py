"""HV8 C1: the tape-only view built through the real Hv7Runner._card. SYNTHETIC tape in a temp root, StubModel only: no API call, no key, no
network, no real arena hour. Nothing here says HV8 makes money.
Run: /data/mal/venv/bin/python -B -m pytest tools/test_hv8_view_runner.py -q"""
from __future__ import annotations

import json

from tools import hv8_rate as H
from tools import hv7_runner as R
from tools.drill_hv6_item6 import FakeChain
from tools.test_hv7_real_layout import DAY, END, SHA, START, H_scan_feats, _no_sockets, tape  # noqa: F401 - fixtures
from tools.test_hv7_real_layout import D
from tools.test_hv7_runner import T_E1, _env  # noqa: F401 - autouse fixture

PLANT = {"holder_count": 9999, "quote_sol_15m_ago": 777.0, "quote_sol_peak": 888.0}


class PlantedChain(FakeChain):
    """A chain that PLANTS values for the three risk inputs the runner must take from tape (plus RPC fields that differ from FakeChain's)."""

    def chain_part(self, pool_id, mint, now_ms, slot):
        part = super().chain_part(pool_id, mint, now_ms, slot)
        part.update(PLANT)
        part.update({"top1_frac": 0.91, "top10_frac": 0.99, "sniper_share": 0.7, "creator_sold_frac": 0.8, "mayhem": False})
        return part


def build(run, T):
    pool = sorted(p.id for p in run.state.pools_wsol(lambda p: p.prog == "pumpswap"))[0]
    snap, prints = run.state.snapshot(pool), list(run.state.pool(pool).events)
    feats = H_scan_feats(run, snap, prints, T)
    card = run._card(pool, T, snap, prints, feats)
    assert card is not None
    return card


def test_view_equal_when_the_chain_plants_holder_count_and_quote_history(tmp_path, tape):
    T = T_E1 - 45_000
    cards = []
    for tag, chain in (("fake", FakeChain()), ("planted", PlantedChain())):
        run = D.new_run(tmp_path / tag, tag, SHA, [DAY], tape, start_ms=START, end_ms=END)
        run.chain = chain
        run.poll_to(T)
        cards.append(build(run, T))
    a, b = cards
    assert a["risk"]["top1_frac"] != b["risk"]["top1_frac"]          # control: the RPC fields really differ in the raw cards
    va, vb = H.tape_view(a), H.tape_view(b)
    assert all(va["risk"][k] is not None for k in H.KEPT_RISK), va["risk"]       # control: tape history exists, so the runner overwrote all three
    assert json.dumps(va, sort_keys=True) == json.dumps(vb, sort_keys=True)
    assert H.view_sha256(a) == H.view_sha256(b)
    for k, v in PLANT.items():
        assert v not in (vb["risk"].get(k), vb["risk"].get("holder_count"), vb["risk"].get("quote_chg_15m"), vb["risk"].get("quote_dd_peak"))
    assert H.build_rate_request(a).request_sha256() == H.build_rate_request(b).request_sha256()
