"""Tests for tools/r4b_insample_ladder.py on synthetic rows only (no picks table, no cache, no tape)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from tools import r4b_book as bk
from tools import r4b_frozen as F
from tools import r4b_insample_ladder as L

H = 3600
Q0, B0 = 77e9, 2.5e14


def ep(s):
    return L._ep(s)


BLOCK_HOURS = {
    "explore-0814": "2026-08-21T00",
    "fresh-0903": "2026-09-05T00",
    "exp011-0909": "2026-09-10T00",
    "fast-pool-0918": "2026-09-19T00",
    "oracle-insample-0922": "2026-09-23T00",
}


def synth(n_per=6, qx_mult=1.05, q=Q0, blocks=tuple(BLOCK_HOURS), seed=0):
    rng = np.random.default_rng(seed)
    T0 = []
    for b in blocks:
        base = ep(BLOCK_HOURS[b])
        T0 += [base + i * H for i in range(n_per)]
    T0 = np.array(T0, np.int64)
    n = len(T0)
    P = {
        "T0": T0, "T1": T0 + 3 * H,
        "Qe13": np.full(n, q) * (1 + 0.01 * rng.random(n)), "Be13": np.full(n, B0),
        "Qe19": np.full(n, q) * (1 + 0.01 * rng.random(n)), "Be19": np.full(n, B0),
        "Qx": np.full(n, q) * qx_mult * (1 + 0.02 * rng.random(n)), "Bx": np.full(n, B0),
        "y_p_ssb": rng.integers(0, 6, n).astype(float), "y_b_ssb": rng.integers(0, 6, n).astype(float),
        "y_p_nearby": rng.random(n) * 3e9, "y_b_nearby": rng.random(n) * 3e9,
    }
    P["spot"] = P["Qe13"] / P["Be13"]
    return P


def prepared(**kw):
    P, dropped = L.prepare(synth(**kw), include_exp011=False)
    P["_flow"] = None
    return P, dropped


# ------------------------------------------------------------------------------------------------------------------ guards
def test_cutoff_constant():
    assert L.CUTOFF == 1790812800


def test_hour_guard():
    a = ep("2026-09-25T06") + 3599
    L.guard_rows([a], [a + 1])                                              # last second the cache covers: passes (oracle-insample)
    with pytest.raises(L.Refusal):
        L.guard_rows([L.CUTOFF], [L.CUTOFF + 10])                           # T0 at the cutoff
    with pytest.raises(L.Refusal):
        L.guard_rows([ep("2026-09-23T00")], [L.CUTOFF])                     # T1 at the cutoff
    with pytest.raises(L.Refusal):
        L.guard_rows([L.CUTOFF - 1], [L.CUTOFF + 5])
    P = synth(blocks=("oracle-insample-0922",))
    P["T0"][0] = L.CUTOFF - 1
    with pytest.raises(L.Refusal):
        L.prepare(P, include_exp011=False)                                  # one second earlier is oracle-live-0925: not in the cache, refused


def test_block_allow_list_and_gaps():
    P = synth(blocks=("explore-0814",))
    P["T0"][0] = ep("2026-09-26T00")                                        # oracle-live-0925
    P["T1"][0] = P["T0"][0] + H
    with pytest.raises(L.Refusal, match="allow-list"):
        L.prepare(P, include_exp011=False)
    with pytest.raises(L.Refusal):
        L.block_of(ep("2026-08-30T00"))                                     # a gap hour: no cached state
    assert L.block_of(ep("2026-09-25T06")) == "oracle-insample-0922" and L.block_of(ep("2026-09-25T07")) == L.ORACLE_LIVE
    assert L.block_of(ep("2026-09-09T11")) == "fresh-0903" and L.block_of(ep("2026-09-09T12")) == "exp011-0909"


def test_exp011_dropped_with_count_never_priced():
    P, dropped = L.prepare(synth(n_per=6), include_exp011=False)
    assert dropped == 6 and len(P["T0"]) == 24 and "exp011-0909" not in set(P["_block"].tolist())
    P2, dropped2 = L.prepare(synth(n_per=6), include_exp011=True)
    assert dropped2 == 6 and len(P2["T0"]) == 30


def test_path_guards(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    assert L.check_path(str(root / "picks.parquet"), str(root), "--picks")
    for bad in (tmp_path / "other.parquet", "/data/mal/blocks/forward-1016/x.parquet", "/var/lib/mal-live/x", root / ".." / "x.parquet"):
        with pytest.raises(L.Refusal):
            L.check_path(str(bad), str(root), "--picks")
    (root / "tip-tape").mkdir()
    with pytest.raises(L.Refusal):
        L.check_path(str(root / "tip-tape" / "a.parquet"), str(root), "--picks")
    (root / "ln.parquet").symlink_to(tmp_path / "other.parquet")
    with pytest.raises(L.Refusal):
        L.check_path(str(root / "ln.parquet"), str(root), "--picks")        # a symlink out of the root is refused
    assert L.PICKS_ROOT == "/data/mal/hunt-1008/push-1016/hunt9/r4b-depth/out" and L.CACHE_ROOT == "/data/mal/hunt-1008/laya-opt/cache/l3"


def test_main_refusals(tmp_path, capsys):
    root = tmp_path / "out"
    root.mkdir()
    res = tmp_path / "res"
    res.mkdir()
    out = res / "ladder.json"
    out.write_text("{}")
    base = ["--picks", str(root / "p.parquet")]
    assert L.main(base + ["--out", str(out)], picks_root=str(root)) == 2                                                         # --out exists
    assert L.main(["--picks", str(tmp_path / "p.parquet"), "--out", str(res / "n.json")], picks_root=str(root)) == 2            # picks outside the root
    assert L.main(base + ["--cache", str(tmp_path / "c")], picks_root=str(root), cache_root=str(root / "cache")) == 2           # cache outside the root
    assert L.main(base, picks_root=str(root)) == 2                                                                               # nothing to do
    md_only = res / "m.json"
    md_only.with_suffix(".md").write_text("x")
    assert L.main(base + ["--out", str(md_only)], picks_root=str(root)) == 2                                                     # the .md exists
    assert "REFUSED" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------------------------ pricing
def test_price_rung_equals_price_cell_without_e1():
    P, _ = prepared()
    for lat in L.LATS:
        for stake in (1e8, 2.5e8, 1e9):
            c = L.price_rung(P, stake, lat, 0.0, 0)
            Qe, Be, spot, Qx, Bx, ssb, near = L._arrays(P, lat)
            ref = bk.price_cell(Qe, Be, spot, Qx, Bx, ssb, near, stake=stake, send=505_000, rent=0)
            for k in ("nofail", "flat", "press", "filled", "stranded"):
                assert np.array_equal(c[k], ref[k]), k


def test_e1_equals_price_variant():
    rr = pytest.importorskip("tools.r4b_read")
    P, _ = prepared()
    n = len(P["T0"])
    tr = {"ent": {1.3: [P["Qe13"], P["Be13"], P["y_p_ssb"], P["y_p_nearby"]]}, "spot": P["spot"], "Qx": P["Qx"], "Bx": P["Bx"],
          "strand_class": np.zeros(n, bool), "rent_t22": np.zeros(n, bool)}
    for stake in (100_000_000, 250_000_000, 1_000_000_000):
        mine = L.price_rung(P, float(stake), "13", L.E1_FIXED, 0)
        ref = rr.price_variant(tr, 1.3, send=505_000, rent=0, auto=True, e1_penalty=float(L.E1_FIXED), stake=stake)
        for k in ("nofail", "flat", "press"):
            assert np.array_equal(mine[k], ref[k]), (stake, k)
    mine = L.price_rung(P, 2.5e8, "13", L.E1_FIXED, F.RENT_REPORT)
    ref = rr.price_variant(tr, 1.3, send=505_000, rent=F.RENT_REPORT, auto=True, e1_penalty=float(L.E1_FIXED), stake=250_000_000)
    assert np.array_equal(mine["press"], ref["press"])


def test_e1_two_versions():
    P, _ = prepared()
    assert L.E1_PROP_FRAC * F.STAKE_LAMPORTS == pytest.approx(L.E1_FIXED, abs=1e-6)
    assert L.e1_versions(1e9)["fixed"] == L.E1_FIXED and L.e1_versions(1e9)["prop"] == pytest.approx(4 * L.E1_FIXED)
    assert L.e1_versions(1e8)["fixed"] == L.E1_FIXED and L.e1_versions(1e8)["prop"] == pytest.approx(0.4 * L.E1_FIXED)
    # at 0.25 the two versions price identically
    v = L.e1_versions(F.STAKE_LAMPORTS)
    a = L.price_rung(P, 2.5e8, "13", v["fixed"], 0)
    b = L.price_rung(P, 2.5e8, "13", v["prop"], 0)
    assert np.allclose(a["nofail"], b["nofail"], atol=1e-3)
    for stake in (1e8, 1e9):
        e = L.e1_versions(stake)
        fx = L.price_rung(P, stake, "13", e["fixed"], 0)
        pr = L.price_rung(P, stake, "13", e["prop"], 0)
        base = L.price_rung(P, stake, "13", 0.0, 0)
        f = base["filled"] & ~base["stranded"]
        assert f.any()
        hair = bk.round_trip(np.full(len(f), stake), P["Qe13"], P["Be13"], P["spot"], P["Qx"], P["Bx"], 505_000, 0, h_in=0.0, h_out=0.0)[0] - base["nofail"]
        # per filled trip the haircut plus the E1 extra is max(haircut, E1)
        assert np.allclose((base["nofail"] - fx["nofail"])[f], np.maximum(0, e["fixed"] - hair[f]))
        assert np.allclose((base["nofail"] - pr["nofail"])[f], np.maximum(0, e["prop"] - hair[f]))
    assert L.price_rung(P, 1e8, "13", L.e1_versions(1e8)["fixed"], 0)["nofail"].mean() < L.price_rung(P, 1e8, "13", L.e1_versions(1e8)["prop"], 0)["nofail"].mean()
    assert L.price_rung(P, 1e9, "13", L.e1_versions(1e9)["fixed"], 0)["nofail"].mean() > L.price_rung(P, 1e9, "13", L.e1_versions(1e9)["prop"], 0)["nofail"].mean()


def test_fixed_fee_falls_and_impact_rises_with_stake():
    P, _ = prepared(q=Q0)
    prev_imp, prev_bps, prev_send = 0.0, 0.0, None
    for stake in (1e8, 2.5e8, 5e8, 1e9):
        d = L.decompose(P, "13", stake)
        assert d["send_pct"] == pytest.approx(-2 * 505_000 / stake * 100, rel=1e-9)     # 1.01 % of 0.10 ... 0.101 % of 1.0 (no guards or strands here)
        if prev_send is not None:
            assert abs(d["send_pct"]) < prev_send
        prev_send = abs(d["send_pct"])
        assert d["impact_pct"] < prev_imp                                                # own impact grows with stake
        prev_imp = d["impact_pct"]
        bps = L.impact_bps(P, "13", stake)["median"]
        assert bps > prev_bps
        prev_bps = bps
        tot = d["gross_pct"] + d["impact_pct"] + d["haircut_pct"] + d["send_pct"] + d["strand_rent_pct"]
        assert tot == pytest.approx(d["nofail_noE1_pct"], abs=1e-9)                       # telescoping identity


def test_guard_share_rises_in_a_thin_pool():
    P, _ = prepared(q=20e9)
    sh = [1 - L.price_rung(P, s, "13", 0.0, 0)["filled"].mean() for s in (1e8, 1e9, 4e9)]
    assert sh[0] <= sh[1] <= sh[2] and sh[2] > sh[0]


def test_open_stats():
    T0 = np.array([0, 0, 3600, 3600, 7200], np.int64)
    T1 = np.array([3600, 7200, 7200, 10800, 10800], np.int64)
    o = L.open_stats(T0, T1)
    assert o["peak"] == 3 and o["mean_at_marks"] == pytest.approx(7 / 3)


def test_days():
    d = L.test_days()
    assert len(d) == 28 and "2026-09-11" not in d and d[0] == "2026-08-21"
    de = L.days_equivalent(L.ALLOWED_BLOCKS)
    assert 0 < de < 28


# ------------------------------------------------------------------------------------------------------------------ ladder, output
def test_run_ladder_structure_net_identity_and_determinism():
    P, _ = prepared()
    t1 = L.run_ladder(P, L.STAKES, days_eq=20.0)
    t2 = L.run_ladder(P, L.STAKES, days_eq=20.0)
    assert json.dumps(t1, sort_keys=True) == json.dumps(t2, sort_keys=True)
    assert t1["label"] == L.LABEL and set(t1["groups"]) == {"train3", "fresh-0903", "pooled_report_only"}
    r1 = t1["groups"]["pooled_report_only"]
    for s in L.STAKES:
        rg = r1["rungs"][str(s)]
        assert rg["peak_open_sol"] == pytest.approx(r1["open_positions_by_intervals"]["peak"] * s)
        for lat in L.LATS:
            lr = rg["lat"][lat]
            d = lr["decomp"]
            for vn in ("fixed", "prop"):
                v = lr["variants"][vn]
                for leg in ("flat", "press"):
                    assert v[leg]["mean_pct"] == pytest.approx(d["nofail_noE1_pct"] - v["e1_pct"] - v[leg]["fail_cost_pct"], abs=1e-9)
                    assert v[leg]["n"] == len(P["T0"])
                    assert set(v[leg]["per_block_sol"]) == set(L.ALLOWED_BLOCKS)
                    assert v[leg]["net_pct_plus_drift"]["50"] == pytest.approx(v[leg]["mean_pct"] - 0.5)
                    assert v[leg]["net_pct_plus_drift"]["100"] == pytest.approx(v[leg]["mean_pct"] - 1.0)
            assert "fixed_rent_charged" in lr["variants"]
            assert lr["share_stake_gt_60s_flow"] is None            # flow table not given: "not computed"
    assert r1["rungs"]["0.25"]["lat"]["13"]["variants"]["fixed"]["retune_20x"]["label"] == "retune, in-sample, best of 2"
    assert r1["rungs"]["0.25"]["lat"]["13"]["variants"]["fixed"]["retune_20x"]["n_variants"] == 2


def test_flow_share():
    P, _ = prepared()
    flow = np.full(len(P["T0"]), 3e8)
    r = L.run_ladder(P, (0.10, 0.5), days_eq=20.0, flow=flow)["groups"]["pooled_report_only"]
    assert r["rungs"]["0.1"]["lat"]["13"]["share_stake_gt_60s_flow"] == 0.0
    assert r["rungs"]["0.5"]["lat"]["13"]["share_stake_gt_60s_flow"] == 1.0


def test_break_even_and_stake_dependence_of_e1():
    P, _ = prepared(qx_mult=1.06)
    r = L.run_ladder(P, L.STAKES, days_eq=20.0)["groups"]["pooled_report_only"]
    be = r["break_even"]
    assert len(be) == 8
    for b in be.values():
        pos = [s for s in L.STAKES if b["net_pct"][str(s)] > 0]
        assert b["smallest_rung_net_above_0"] == (min(pos) if pos else "none in range")
        for s in L.STAKES:
            assert b["net_pct_plus_50bps_drift"][str(s)] == pytest.approx(b["net_pct"][str(s)] - 0.5)
            assert b["net_pct_plus_100bps_drift"][str(s)] == pytest.approx(b["net_pct"][str(s)] - 1.0)
    # fixed E1 costs the small rungs more, proportional E1 costs the big rungs more
    f, p = be["fixed|press|1.3 s"]["net_pct"], be["prop|press|1.3 s"]["net_pct"]
    assert p["0.1"] > f["0.1"] and p["1.0"] < f["1.0"]
    # no edge at all (exit state = entry state): no rung is positive
    P2, _ = prepared(qx_mult=1.0)
    r2 = L.run_ladder(P2, L.STAKES, days_eq=20.0)["groups"]["pooled_report_only"]
    assert all(b["smallest_rung_net_above_0"] == "none in range" for b in r2["break_even"].values())


def test_break_even_picks_smallest_positive_rung():
    stakes = (0.1, 0.25)
    res = {"rungs": {str(s): {"lat": {lat: {"variants": {vn: {leg: {"mean_pct": x} for leg in ("flat", "press")} for vn in ("fixed", "prop")}} for lat in L.LATS}}
                     for s, x in ((0.1, -0.2), (0.25, 0.3))}}
    be = L.break_even(res, stakes)
    assert be["fixed|press|1.3 s"]["smallest_rung_net_above_0"] == 0.25
    assert be["fixed|press|1.3 s"]["net_pct_plus_50bps_drift"]["0.25"] == pytest.approx(-0.2)


def test_label_in_every_table_and_files():
    P, dropped = prepared()
    res = L.run_ladder(P, (0.10, 0.25), days_eq=20.0)
    md = L.render_md(res, {"code_blob": "x", "dropped_exp011": dropped})
    lines = md.split("\n")
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    assert len(heads) >= 8
    for i in heads:
        assert lines[i + 2] == L.LABEL
    assert "# Appendix: retune, in-sample, best of 2" in md and md.count("retune, in-sample, best of 2: 20x depth filter") == 3 and "not computed" in md and "oracle-live-0925 is not in the cache" in md
    assert md.startswith("# r4-b in-sample stake ladder\n\n" + L.LABEL)
    assert L.LABEL.startswith("IN-SAMPLE (r4-b training/selection days; the model was fitted on these rows). Not evidence and not a forecast. Upper bound")


# ------------------------------------------------------------------------------------------------------------------ self-check
def test_selfcheck_compare_with_fixture():
    exp = {"n": 10, "mean_pct": 1.0, "total_sol": 2.0}
    tol = {"mean_pct": 0.0005, "total_sol": 0.0005}
    assert L.selfcheck_compare({"n": 10, "mean_pct": 1.0004, "total_sol": 1.9996}, exp, tol)
    assert not L.selfcheck_compare({"n": 11, "mean_pct": 1.0, "total_sol": 2.0}, exp, tol)
    assert not L.selfcheck_compare({"n": 10, "mean_pct": 1.001, "total_sol": 2.0}, exp, tol)
    assert not L.selfcheck_compare({"n": 10, "mean_pct": 1.0, "total_sol": 2.001}, exp, tol)
    assert L.EXPECTED == {"n": 2879, "mean_pct": 1.2150, "total_sol": 8.7453, "mean_pct_19": 1.2755, "mean_pct_05": 1.6745, "total_sol_05": 24.104, "mean_pct_05_19": 1.7349}
    assert L.TOL["total_sol_05"] == 0.0006 and L.TOL["mean_pct_19"] == 0.0005
    full = dict(L.EXPECTED)
    assert L.selfcheck_compare(dict(full))
    for k in full:                                              # every one of the seven targets must match
        bad = dict(full)
        bad[k] = bad[k] + (1 if k == "n" else 0.01)
        assert not L.selfcheck_compare(bad), k
    assert not L.selfcheck_compare({"n": 2879, "mean_pct": 1.2150, "total_sol": 8.7453})   # missing keys fail


def test_require_selfcheck(tmp_path):
    f = tmp_path / "selfcheck.json"
    with pytest.raises(L.Refusal):
        L.require_selfcheck(f, "abc")
    f.write_text(json.dumps({"pass": True, "code_blob": "other"}))
    with pytest.raises(L.Refusal):
        L.require_selfcheck(f, "abc")
    f.write_text(json.dumps({"pass": False, "code_blob": "abc"}))
    with pytest.raises(L.Refusal):
        L.require_selfcheck(f, "abc")
    f.write_text(json.dumps({"pass": True, "code_blob": "abc"}))
    L.require_selfcheck(f, "abc")


def test_main_selfcheck_gate_and_ladder(tmp_path, monkeypatch, capsys):
    root = tmp_path / "out"
    root.mkdir()
    P = synth(n_per=6)
    P["_flow"] = None
    monkeypatch.setattr(L, "load_picks", lambda path, flow=None: dict(P))
    base = ["--picks", str(root / "p.parquet"), "--stakes", "0.10,0.25"]
    res_dir = tmp_path / "res"
    out = res_dir / "ladder.json"
    sc = res_dir / "selfcheck.json"
    assert L.main(base + ["--out", str(out)], picks_root=str(root)) == 2                    # no PASS file: refused
    assert not out.exists()
    assert L.main(base + ["--self-check", "--out", str(out)], picks_root=str(root)) == 1   # synthetic n is not 2,879: FAIL, no file
    assert "SELF-CHECK FAIL" in capsys.readouterr().out and not sc.exists() and not out.exists()
    Pf, _ = L.prepare(dict(P), include_exp011=True)                                         # the self-check keeps the exp011 rows
    got = L.selfcheck_numbers(Pf)
    assert got["n"] == 30
    monkeypatch.setattr(L, "EXPECTED", dict(got))
    assert L.main(base + ["--self-check", "--out", str(out)], picks_root=str(root)) == 0    # PASS, file written, ladder runs in the same invocation
    text = capsys.readouterr().out
    assert "SELF-CHECK PASS n=30" in text and "exp011-0909 rows dropped, not priced: 6" in text
    res = json.loads(out.read_text())
    assert res["label"] == L.LABEL and res["groups"]["pooled_report_only"]["n_picks"] == 24 and set(res["groups"]["train3"]["rungs"]) == {"0.1", "0.25"}
    assert res["meta"]["selfcheck"]["pass"] is True and res["meta"]["selfcheck_statement"] == L.SELFCHECK_STATEMENT and "bridge_0.25_rent_charged_no_E1_1.3s_press" in res
    assert "SELF-CHECK" not in out.with_suffix(".md").read_text() and "Self-check result" in out.with_suffix(".md").read_text()
    assert out.with_suffix(".md").read_text().startswith("# r4-b in-sample stake ladder")
    out2 = res_dir / "ladder2.json"
    assert L.main(base + ["--out", str(out2)], picks_root=str(root)) == 0                   # the PASS file of the same code blob is reused
    assert json.loads(out2.read_text())["groups"] == res["groups"]
    sc.write_text(json.dumps({"pass": True, "code_blob": "old"}))
    assert L.main(base + ["--out", str(res_dir / "l3.json")], picks_root=str(root)) == 2    # another code blob: refused


def test_selfcheck_uses_hunt9_stack_without_e1_and_with_exp011():
    P, _ = L.prepare(synth(n_per=4), include_exp011=True)
    got = L.selfcheck_numbers(P)
    Qe, Be, spot, Qx, Bx, ssb, near = L._arrays(P, "13")
    ref = bk.price_cell(Qe, Be, spot, Qx, Bx, ssb, near, stake=F.STAKE_LAMPORTS, send=505_000, rent=2_039_280, auto_stranded=False, k_tol=None)
    assert got["n"] == 20 and got["total_sol"] == pytest.approx(ref["press"].sum() / 1e9)


# ------------------------------------------------------------------------------------------------------------------ QP / reviewer round 1
def test_groups_fresh_alone():
    P = synth(n_per=6)
    fresh = np.array([L.block_of(int(t)) == "fresh-0903" for t in P["T0"]])
    P["Qx"] = np.where(fresh, P["Qx"] * 0.5, P["Qx"])                      # a fresh-0903 loss
    Pp, _ = L.prepare(P, include_exp011=False)
    r = L.run_ladder(Pp, (0.25,), days_eq=10.0)
    g = r["groups"]
    tr, fr, po = (g[k]["rungs"]["0.25"]["lat"]["13"]["variants"]["fixed"]["flat"] for k in ("train3", "fresh-0903", "pooled_report_only"))
    assert tr["n"] == 18 and fr["n"] == 6 and po["n"] == 24
    assert fr["total_sol"] < 0 < tr["total_sol"]
    assert tr["per_block_n"]["fresh-0903"] == 0 and fr["per_block_n"]["fresh-0903"] == 6 and sum(fr["per_block_n"].values()) == 6
    assert po["total_sol"] == pytest.approx(tr["total_sol"] + fr["total_sol"])      # flat leg is per-row: the pooled row is only the sum
    assert g["train3"]["blocks"] == list(L.TRAIN_BLOCKS) and g["fresh-0903"]["blocks"] == ["fresh-0903"]
    assert g["train3"]["break_even"] and g["fresh-0903"]["break_even"]
    assert g["train3"]["days_equivalent"] == 10.0                           # explicit days honoured; default is computed per group
    d = L.run_ladder(Pp, (0.25,))["groups"]
    assert d["train3"]["days_equivalent"] == L.days_equivalent(L.TRAIN_BLOCKS)
    assert d["fresh-0903"]["days_equivalent"] == L.days_equivalent(("fresh-0903",)) != d["train3"]["days_equivalent"]
    md = L.render_md(r, {"code_blob": "x", "dropped_exp011": 0})
    assert "# Group: fresh-0903 alone (its own row)" in md and "pooled incl. fresh-0903 (report only)" in md and "net SOL/day (28 days)" in md


def subset_train(P):
    return L.subset(P, np.isin(P["_block"], L.TRAIN_BLOCKS))


def test_rent_charged_rows_in_md_and_bridge():
    P, _ = prepared()
    r = L.run_ladder(P, (0.10, 0.25), days_eq=20.0)
    md = L.render_md(r, {"code_blob": "x", "dropped_exp011": 0})
    assert md.count("report-only, rent 2,039,280 charged per filled trip") >= 3
    assert all(r["groups"][g]["rungs"]["0.1"]["lat"]["13"]["variants"]["fixed_rent_charged"]["rent_lamports"] == 2_039_280 for g in r["groups"])
    b = r["bridge_0.25_rent_charged_no_E1_1.3s_press"]
    assert b["n"] == 18 and set(b["ladder_exit_invalid_by_cause"]) == {"C2", "C7", "C8"} and "Bridge, training blocks pooled" in md
    assert b["selfcheck_settings"]["total_sol"] == pytest.approx(L.selfcheck_numbers(subset_train(P))["total_sol"])
    assert L.SELFCHECK_STATEMENT in md and "Self-check result" in md


def wide_rows():
    """Normal, guarded (spot far below exec), auto-stranded (price collapse: proceeds below one send), C2 (tokens >= Bx; NaN Qx) and C8 (k fall) rows, per latency."""
    n = 15
    rng = np.random.default_rng(5)
    Qe = np.full(n, Q0) * (1 + 0.02 * rng.random(n))
    Be = np.full(n, B0)
    spot = Qe / Be
    Qx = Qe * 1.03
    Bx = np.full(n, B0)
    spot[3:5] *= 0.5                      # guarded
    Bx[5:8] = B0 * 1e4                    # auto-stranded at small stakes
    Bx[8:10] = 1.0                        # C2: tokens >= Bx
    Qx[10] = np.nan                       # C2: unpriced exit
    Qx[11:13] = Qe[11:13] * 0.3           # k fall
    ent = {lat: [Qe * (1 + 0.003 * k), Be, rng.integers(0, 6, n).astype(float), rng.random(n) * 3e9] for k, lat in enumerate((1.3, 1.9))}
    tr = {"ent": ent, "spot": spot, "Qx": Qx, "Bx": Bx, "strand_class": np.zeros(n, bool), "rent_t22": np.zeros(n, bool)}
    P = {"Qe13": ent[1.3][0], "Be13": Be, "Qe19": ent[1.9][0], "Be19": Be, "spot": spot, "Qx": Qx, "Bx": Bx,
         "y_p_ssb": ent[1.3][2], "y_p_nearby": ent[1.3][3], "y_b_ssb": ent[1.9][2], "y_b_nearby": ent[1.9][3]}
    return P, tr


def test_e1_equals_price_variant_wide_rows():
    rr = pytest.importorskip("tools.r4b_read")
    P, tr = wide_rows()
    seen = {"guard": 0, "strand": 0, "xinv": 0}
    for lat, key in (("13", 1.3), ("19", 1.9)):
        for stake in (100_000_000, 250_000_000, 1_000_000_000):
            for vn in ("fixed", "prop"):
                e1 = L.e1_versions(float(stake))[vn]
                for rent in (0, F.RENT_REPORT):
                    mine = L.price_rung(P, float(stake), lat, e1, rent)
                    ref = rr.price_variant(tr, key, send=505_000, rent=rent, auto=True, e1_penalty=e1, stake=stake)
                    for k in ("nofail", "flat", "press", "filled", "stranded"):
                        assert np.array_equal(mine[k], ref[k]), (lat, stake, vn, rent, k)
                    seen["guard"] += int((~mine["filled"]).sum())
                    seen["strand"] += int((mine["stranded"] & ~mine["exit_invalid"]).sum())
                    seen["xinv"] += int(mine["exit_invalid"].sum())
    assert all(v > 0 for v in seen.values()), seen                         # every row class was exercised


# ------------------------------------------------------------------------------------------------------------------ input robustness
def test_input_robustness(tmp_path):
    with pytest.raises(L.Refusal):
        L.guard_rows([ep("2026-08-21T00")], [float("nan")])                 # NaN T1 does not pass the hour guard
    P = synth(n_per=2, blocks=("explore-0814",))
    with pytest.raises(L.Refusal):
        L.prepare({k: (v[:0] if isinstance(v, np.ndarray) else v) for k, v in P.items()}, include_exp011=False)    # empty frame: a refusal, not a KeyError
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    base = {c: [1.0, 2.0] for c in L.REQUIRED}
    f = tmp_path / "p.parquet"
    pd.DataFrame({**base, "T0": pd.to_datetime([1_790_000_000, 1_790_003_600], unit="s"), "T1": [1_790_003_600, 1_790_007_200]}).to_parquet(f)
    with pytest.raises(L.Refusal):
        L.load_picks(str(f))                                                # datetime T0
    pd.DataFrame({**base, "T0": [1_790_000_000, 1_790_003_600], "T1": [1_790_003_600, float("nan")]}).to_parquet(f)
    with pytest.raises(L.Refusal):
        L.load_picks(str(f))                                                # NaN T1 refused before any cast
    pd.DataFrame({**base, "T0": [1_790_000_000, 1_790_003_600], "T1": [1_790_003_600, 1_790_007_200], "key": [1, 2], "exit_reason": ["tp", "sl"]}).to_parquet(f)
    assert "exit_reason" not in L.load_picks(str(f))                        # only the needed columns are read
    fl = tmp_path / "flow.parquet"
    pd.DataFrame({"key": [1, 1], "T0": [1_790_000_000] * 2, "tot": [1.0, 2.0]}).to_parquet(fl)
    with pytest.raises(L.Refusal):
        L.load_picks(str(f), str(fl))                                       # duplicate flow rows


def test_out_paths_barred_and_code_blob():
    with pytest.raises(L.Refusal):
        L.check_out_path("/var/lib/mal-live/x.json", "--out")
    with pytest.raises(L.Refusal):
        L.check_out_path("/tmp/forward-x/ladder.json", "--out")
    L.check_out_path("/data/mal/hunt-1008/ops/reports/2026-11-13/r4b-ladder/ladder.json", "--out")
    import hashlib
    h = hashlib.sha256()
    for p in (L.__file__, bk.__file__, F.__file__):
        h.update(hashlib.sha256(open(p, "rb").read()).digest())
    assert L.code_blob() == h.hexdigest()                                   # includes r4b_frozen.py
