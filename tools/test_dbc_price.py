"""Tests for tools/dbc_price.py on REAL decoded DBC rows (tools/data/dbc_price_fixture.jsonl: two pools, 2026-10-14T12)."""
import json
from pathlib import Path

import numpy as np


from tools import dbc_price as L

import contextlib


@contextlib.contextmanager
def _raises(exc):
    try:
        yield
    except exc:
        return
    raise AssertionError("did not raise")


FIX = Path(__file__).parent / "data" / "dbc_price_fixture.jsonl"


def load(files=("dbc_price_fixture.jsonl", "dbc_price_fixture_mode1.jsonl")):
    rows = [json.loads(x) for f in files for x in (FIX.parent / f).read_text().splitlines()]
    pids, cfgs = {}, {}
    pid = np.array([pids.setdefault(r["pool"], len(pids)) for r in rows])
    cfg = np.array([cfgs.setdefault(r["config"], len(cfgs)) for r in rows])
    o = np.argsort(pid, kind="stable")
    rows = [rows[i] for i in o]
    pid, cfg = pid[o], cfg[o]
    a = L.rows_to_arrays(rows)
    iso = L.isolated(pid, a["slot"])
    d = L.derive(a, pid, cfg, iso)
    d["iso"] = iso
    perm, st, bad = L.order_fix(a, pid, d)
    a = {k: v[perm] for k, v in a.items()}
    pid, cfg = pid[perm], cfg[perm]
    d = L.derive(a, pid, cfg, ~bad[perm])
    return rows, a, d, pid, bad[perm], st


def test_curve_reproduces_real_rows():
    rows, a, d, pid, bad, st = load()
    v = L.validate(a, d, exclude=bad)
    for side in ("buy", "sell"):
        assert v[side]["n"] > 50
        assert v[side]["within_1pct"] > 0.9


def test_mode1_sell_fee_is_on_quote_output():
    rows, a, d, pid, bad, st = load()
    sell1 = (a["side"] == 0) & (d["mode"] == 1) & d["mode_known"] & (a["fee"] > 0) & ~bad
    assert sell1.sum() > 20  # the mode-1 fixture pool (Fmf9UXb2..., config with fee on input for buys)
    S0, Lq = 3.0e17, 4.0e31
    assert L.sell_fill(S0, Lq, 1e13, 0.01, 1)[0] == L.sell_fill(S0, Lq, 1e13, 0.01, 0)[0]
    r = d["rate"][sell1]
    assert np.allclose(r, a["fee"][sell1] / (a["aout"][sell1] + a["fee"][sell1]))
    assert np.median(r) > 1e-3  # not the ~0 bps of fee-lamports / base-atoms


def test_order_fix_reorders_some_slots():
    *_, st = load()
    assert st["groups_multi"] > 0


def test_buy_sell_round_trip_loses_the_fees():
    S0, Lq = 3.0e17, 4.0e31
    tok, S1, _ = L.buy_fill(S0, Lq, 0.25e9, 0.0025, 0)
    back, _ = L.sell_fill(S1, Lq, tok, 0.0025, 0)
    assert 0.25e9 * (1 - 0.0025) ** 2 * 0.999 < back < 0.25e9 * (1 - 0.0025) ** 2 * 1.001


def test_round_trip_pnl_includes_sends_and_guard():
    S0, Lq = 3.0e17, 4.0e31
    pnl, filled = L.round_trip(0.25e9, S0, Lq, L.spot(S0), 0.0025, 0, S0, Lq, 0.0025, quote_room=1e12)
    assert filled and pnl < 0 and pnl > -0.01e9
    pnl2, f2 = L.round_trip(0.25e9, S0, Lq, L.spot(S0) / 2, 0.0025, 0, S0, Lq, 0.0025)  # decision spot far below -> guard
    assert not f2 and pnl2 == -L.SEND_LAMPORTS
    pnl3, f3 = L.round_trip(0.25e9, S0, Lq, L.spot(S0), 0.0025, 0, S0, Lq, 0.0025, quote_room=1e6)  # threshold room too small
    assert not f3


def test_holdout_refused():
    with _raises(L.Refused):
        L.check_hour_path(L.BACKFILL_ROOT + "/tape-2026-10-17T00.jsonl.zst")
    with _raises(L.Refused):
        L.check_hour_path(L.BACKFILL_ROOT + "/tape-2026-10-23T05.jsonl.zst")
    assert L.check_hour_path(L.BACKFILL_ROOT + "/tape-2026-10-16T23.jsonl.zst").hour == 23


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
