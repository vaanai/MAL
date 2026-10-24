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


def test_holdout_switch_default_refuses_and_enabled_accepts_only_holdout():
    from tools import dbc_holdout_read as H
    assert L.HOLDOUT_READ_ENABLED is False
    with _raises(L.Refused):
        L.check_hour_path(L.HOLDOUT_ROOT + "/tape-2026-10-20T05.jsonl.zst")
    with _raises(L.Refused):
        L.check_hour_path(L.HOLDOUT_ROOT + "/tape-2026-10-20T05.jsonl.zst", L.HOLDOUT_ROOT)
    L.HOLDOUT_READ_ENABLED = True  # names only: check_hour_path opens nothing
    try:
        assert L.check_hour_path(L.HOLDOUT_ROOT + "/tape-2026-10-20T05.jsonl.zst").hour == 5
        with _raises(L.Refused):
            L.check_hour_path(L.BACKFILL_ROOT + "/tape-2026-10-15T05.jsonl.zst")  # exploration refused while enabled
        with _raises(L.Refused):
            L.check_hour_path(L.HOLDOUT_ROOT + "/tape-2026-10-16T05.jsonl.zst")
        with _raises(L.Refused):
            L.check_hour_path(L.HOLDOUT_ROOT + "/tape-2026-10-24T00.jsonl.zst")
        hrs = L.exploration_hours()
        assert len(hrs) == 168 and hrs[0].day == 17 and hrs[-1].day == 23
    finally:
        L.HOLDOUT_READ_ENABLED = False
    assert len(L.exploration_hours()) == 72


def test_holdout_read_refuses_without_flag_and_when_output_exists(tmp_path=None):
    import tempfile
    from tools import dbc_holdout_read as H
    assert H.main(["--out", "/tmp/never-used-dbc-holdout"]) == 2
    d = Path(tempfile.mkdtemp())
    (d / "holdout-m10.json").write_text("{}")
    assert H.run(d) == 3  # refused before the switch is touched
    assert L.HOLDOUT_READ_ENABLED is False


def _fake_root(n_missing=0, age_s=3600):
    import os, tempfile, time
    from tools import dbc_holdout_read as H
    d = Path(tempfile.mkdtemp())
    names = H.expected_names()
    assert len(names) == 168 and names[0] == 'tape-2026-10-17T00.jsonl.zst' and names[-1] == 'tape-2026-10-23T23.jsonl.zst'
    for n in names[n_missing:]:
        (d / n).write_bytes(b'')
    t = time.time() - age_s
    for p in list(d.iterdir()) + [d]:
        os.utime(p, (t, t))
    return d


def test_v2_preflight_and_marker_rules():
    import os, tempfile, time
    from tools import dbc_holdout_read as H
    assert H.preflight(str(_fake_root()))[0] is True
    assert H.preflight(str(_fake_root(n_missing=1)))[0] is False  # missing hour
    d = _fake_root(age_s=60)  # too fresh
    ok, why = H.preflight(str(d))
    assert not ok and 'writer' in why
    d2 = _fake_root()
    (d2 / 'checkpoint-x.json').write_text('{}')  # a fresh non-tape entry also counts as a writer
    assert not H.preflight(str(d2))[0]
    old_m, old_m1 = H.MARKER, H.MARKER_V1
    tmp = Path(tempfile.mkdtemp())
    try:
        H.MARKER, H.MARKER_V1 = str(tmp / 'v2'), str(tmp / 'v1')
        Path(H.MARKER_V1).write_text('old')  # old marker present: v2 is still allowed to claim
        assert H.claim_marker(H.MARKER) is True
        assert H.claim_marker(H.MARKER) is False  # v2 present: refuse
        # a failed preflight never claims the marker (run with a root that lacks the files)
        H.MARKER = str(tmp / 'v2b')
        fresh = Path(tempfile.mkdtemp()) / 'out'
        import tools.dbc_holdout_read as HH
        orig = L.HOLDOUT_ROOT
        assert HH.run(fresh, root=str(_fake_root(n_missing=3))) == 5
        assert not Path(H.MARKER).exists() and not fresh.exists()
        assert L.HOLDOUT_READ_ENABLED is False
    finally:
        H.MARKER, H.MARKER_V1 = old_m, old_m1


def test_one_look_marker_is_global_and_atomic():
    import tempfile
    from tools import dbc_holdout_read as H
    d = Path(tempfile.mkdtemp())
    m = str(d / "HOLDOUT_READ_DONE")
    assert H.claim_marker(m) is True
    assert H.claim_marker(m) is False
    old = H.MARKER
    H.MARKER = m
    try:
        fresh = Path(tempfile.mkdtemp()) / "fresh-out"  # a NEW --out must not allow a second look
        assert H.run(fresh, root=str(_fake_root())) == 4  # preflight ok, existing v2 marker refuses
        assert L.HOLDOUT_READ_ENABLED is False and not fresh.exists()
    finally:
        H.MARKER = old
    # Path check only. Until 2026-10-24 this also asserted the real v2 marker did not exist; #1494 has since consumed the one look, so the real marker now exists (P17, QP swap3 ruling).
    assert old.endswith('HOLDOUT_READ_DONE_v2')


def test_verdict_p5():
    from tools import dbc_holdout_read as H

    def side(mean=0.01, days=5, pos=3, ci=0.001, ex3=1.0, exday=1.0):
        return {"n": 120, "mean": mean, "days": days, "days_pos": pos, "ci90_lo_trade": ci, "ex_top3": ex3, "ex_best_day": exday}

    good = {"filled": 120, "flat": side(), "press": side()}
    assert H.verdict(good)["verdict"] == "PASS"
    assert H.verdict({**good, "filled": 99})["verdict"] == "FAIL"
    assert H.verdict({**good, "press": side(ci=0.0)})["verdict"] == "FAIL"  # 0 is a fail
    assert H.verdict({**good, "flat": side(days=4)})["verdict"] == "FAIL"
    assert H.verdict({**good, "flat": side(days=4, pos=2)})["verdict"] == "FAIL"
    assert H.verdict({**good, "flat": side(days=6, pos=3)})["verdict"] == "FAIL"  # needs strictly more than half
    assert H.verdict({**good, "flat": side(exday=-0.1)})["verdict"] == "FAIL"
    assert H.verdict({**good, "press": side(ex3=0.0)})["verdict"] == "FAIL"


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
