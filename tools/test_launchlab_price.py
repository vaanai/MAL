"""launchlab_price: curve reproduction on REAL decoded rows (fixtures/venue_tape/notices.jsonl, captured 2026-10-24 by the live tape), round trip, data guards."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as N

import numpy as np
import pytest

from tools import launchlab_price as L
from tools import venue_tape as vt

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "venue_tape" / "notices.jsonl"


def fixture_trades() -> dict[str, dict]:
    out = {}
    for line in FIX.read_text().splitlines():
        c = json.loads(line)
        if c["venue"] != "launchlab":
            continue
        n = N(slot=c["slot"], signature=c["signature"], failed=c["err"] is not None, logs=c["logs"], t_recv_ms=1_790_000_000_000)
        for r in vt.rows_for_venue("launchlab", n):
            if r["type"] == "trade":
                out[c["case"]] = r
    return out


def test_buy_row_reproduces_exactly():
    r = fixture_trades()["ll_buy"]
    c = L.check_trade_row(r)
    assert c["base_out_ok"] and c["quote_ok"]
    assert abs(c["rate"] - 0.015) < 1e-4  # 0.25% protocol + 1.25% platform


def test_sell_row_reproduces_exactly():
    r = fixture_trades()["ll_sell"]
    c = L.check_trade_row(r)
    assert c["gross_ok"] and c["out_ok"] and c["base_ok"]
    assert abs(c["rate"] - 0.0105) < 2e-4  # 0.25% + 0.75% + 0.05% of the gross output


def test_float_curve_matches_integer_within_rounding():
    r = fixture_trades()["ll_buy"]
    rate = L.row_fees(r) / r["amount_in"]
    bo, _ = L.buy_out(r["virtual_quote"], r["virtual_base"], r["real_quote_before"], r["real_base_before"], r["amount_in"], rate)
    assert abs(bo / r["amount_out"] - 1) < 1e-9
    s = fixture_trades()["ll_sell"]
    rate = L.row_fees(s) / (s["amount_out"] + L.row_fees(s))
    pr, _ = L.sell_out(s["virtual_quote"], s["virtual_base"], s["real_quote_before"], s["real_base_before"], s["amount_in"], rate)
    assert abs(pr / s["amount_out"] - 1) < 2e-6  # the program floors lamports


def test_spot_is_marginal_price():
    r = fixture_trades()["ll_buy"]
    sp = L.spot(r["virtual_quote"], r["virtual_base"], r["real_quote_before"], r["real_base_before"])
    tiny, _ = L.buy_out(r["virtual_quote"], r["virtual_base"], r["real_quote_before"], r["real_base_before"], 1000.0, 0.0)
    assert abs((1000.0 / tiny) / sp - 1) < 1e-4


STATE = dict(vq=30_000_852_951.0, vb=1_073_025_605_596_382.0, rq=20e9, rb=5.0e14)


def trip(stake=0.25e9, fee_in=0.015, fee_out=0.0105, rq_x=None, rb_x=None, **kw):
    s = L.spot(STATE["vq"], STATE["vb"], STATE["rq"], STATE["rb"])
    return L.round_trip(np.array([stake]), STATE["vq"], STATE["vb"], STATE["rq"], STATE["rb"], s, fee_in,
                        STATE["rq"] if rq_x is None else rq_x, STATE["rb"] if rb_x is None else rb_x, fee_out, **kw)


def test_round_trip_flat_market_loses_fees_impact_and_sends():
    pnl, filled = trip()
    assert filled[0]
    # no price change: loss = fees both sides (~2.5%) + own impact both ways (small at 0.25 SOL on a 50 SOL curve) + 2 sends
    assert -0.25e9 * 0.04 < pnl[0] < -0.25e9 * 0.02 - 2 * 160_000 + 1e6


def test_zero_fee_tiny_stake_round_trip_is_only_sends():
    pnl, _ = trip(stake=1e6, fee_in=0.0, fee_out=0.0, send=0, rent=0)
    assert abs(pnl[0]) < 5  # lamports: only float noise (own impact is second order at 0.001 SOL)


def test_guard_not_filled_pays_one_send():
    pnl, filled = L.round_trip(np.array([0.25e9]), STATE["vq"], STATE["vb"], STATE["rq"], STATE["rb"], 1e-9, 0.015, STATE["rq"], STATE["rb"], 0.0105)
    # decision spot far below the exec price -> ratio > 1.15 -> not filled
    assert not filled[0] and pnl[0] == -160_000


def test_ended_curve_is_stranded():
    pnl, filled, st = trip(ended=np.array([True]), return_stranded=True)
    assert filled[0] and st[0] and pnl[0] == -0.25e9 - 160_000 - L.RENT_STRANDED


def test_price_up_makes_pnl_better_than_flat():
    base, _ = trip()
    up, _ = trip(rq_x=STATE["rq"] + 5e9, rb_x=STATE["rb"] + 1e14)
    assert up[0] > base[0]


def test_rent_leg_costs_exactly_rent():
    a, _ = trip()
    b, _ = trip(rent=L.RENT_REPORT)
    assert abs((a[0] - b[0]) - L.RENT_REPORT) < 1e-6


def test_holdout_hours_refused(tmp_path):
    root = tmp_path / "launchlab"
    root.mkdir()
    ok = root / "tape-2026-10-16T23.jsonl.zst"
    ho = root / "tape-2026-10-17T00.jsonl.zst"
    late = root / "tape-2026-10-23T23.jsonl.zst"
    early = root / "tape-2026-09-23T23.jsonl.zst"
    for p in (ok, ho, late, early):
        p.write_bytes(b"")
    assert L.check_hour_path(ok, root=str(root)).hour == 23
    for p in (ho, late, early):
        with pytest.raises(L.Refused):
            L.check_hour_path(p, root=str(root))
    with pytest.raises(L.Refused):
        L.check_hour_path(tmp_path / "tape-2026-10-01T00.jsonl.zst", root=str(root))  # not directly under root


def test_symlink_to_holdout_refused(tmp_path):
    root = tmp_path / "launchlab"
    root.mkdir()
    ho = root / "tape-2026-10-20T00.jsonl.zst"
    ho.write_bytes(b"")
    link = root / "tape-2026-10-01T00.jsonl.zst"
    link.symlink_to(ho)
    with pytest.raises(L.Refused):
        L.check_hour_path(link, root=str(root))


def test_read_hour_rows_refuses_holdout_before_opening(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: called.append(a))
    with pytest.raises(L.Refused):
        list(L.read_hour_rows(f"{L.BACKFILL_ROOT}/tape-2026-10-20T05.jsonl.zst"))
    assert not called


def test_count_files_lists_only(tmp_path):
    root = tmp_path / "launchlab"
    root.mkdir()
    for n in ("2026-09-24T00", "2026-09-24T01", "2026-10-17T00", "2026-10-23T23"):
        (root / f"tape-{n}.jsonl.zst").write_bytes(b"")
    (root / "checkpoint.json").write_text("{}")
    c = L.count_hour_files(str(root))
    assert c["exploration_hours"] == 2 and c["holdout_hours"] == 2 and c["exploration_hours_needed"] == 23 * 24


def test_rows_to_arrays_and_fee_rate():
    t = fixture_trades()
    rows = [t["ll_buy"], t["ll_sell"]]
    a = L.rows_to_arrays(rows)
    assert list(a["side"]) == [1, 0] and a["slot"][0] == rows[0]["slot"]
    fr = L.trade_fee_rate(a["side"], a["ain"], a["aout"], a["fees"])
    assert abs(fr[0] - 0.015) < 1e-4 and abs(fr[1] - 0.0105) < 2e-4
