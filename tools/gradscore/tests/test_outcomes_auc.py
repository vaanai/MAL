"""Outcome tracker v1 and auc read rule. Offline, synthetic data only."""
import json
import os
import random
from types import SimpleNamespace

from tools.gradscore import auc as au, discover as dv, outcomes as oc, run as rn
from tools.gradscore.tests.test_gradscore import FakeCaller, FakeDisc, gecko_doc, mk, rows, NOW

T0 = 1_800_000_100.0  # not a multiple of 300: floor is 1_800_000_000 - 0 ... see below
BASE = int(T0 // 300) * 300


def cd(ts, hi, close, lo=None):
    return (ts, close, hi, lo if lo is not None else close, close)


def doc_of(cs):
    return {"data": {"attributes": {"ohlcv_list": [[c[0], c[1], c[2], c[3], c[4]] for c in sorted(cs, reverse=True)]}}}


# ---- item 1: pre-sight high never in the label
def test_pre_sight_candle_high_excluded_from_max_but_disclosed():
    cs = [cd(BASE, 4.0, 1.0), cd(BASE + 300, 1.1, 1.0), cd(BASE + 600, 1.2, 1.1)]  # covering candle starts before T0
    assert BASE < T0 < BASE + 300
    s = oc.summarise(cs, T0, 60, 1.0)
    assert s["max_mult"] == 1.2 and s["first_candle_high"] == 4.0


# ---- item 2: price from the last candle <= horizon; dead tokens stay in
def test_dead_token_keeps_last_close_and_max():
    cs = [cd(BASE + 300, 10.0, 5.0), cd(BASE + 600, 2.0, 0.2)]  # 10x at +5m then silence to the +24h horizon
    s = oc.summarise(cs, T0, 1440, 1.0)
    assert s["max_mult"] == 10.0 and s["price_native"] == 0.2 and s["price_candle_ts"] == BASE + 600 and abs(s["ret"] - (-0.8)) < 1e-9


def test_no_data_only_without_a_candle_at_or_after_first_sight():
    assert oc.summarise([cd(BASE, 4.0, 1.0)], T0, 60, 1.0) is None  # only the pre-sight candle
    assert oc.summarise([], T0, 60, 1.0) is None
    assert oc.summarise([cd(BASE + 300, 1.0, 1.0)], T0, 60, 0) is None


# ---- item 3: versioned tracker
def test_scheduling():
    d = {"m1": {"first_seen_s": NOW, "pool": "p", "is_pump_suffix": True}}
    assert oc.due_work(d, {}, NOW + 14 * 60) == []
    assert oc.due_work(d, {}, NOW + 15 * 60 + oc.GRACE_S) == [("m1", 15)]
    assert [h for _, h in oc.due_work(d, {}, NOW + 24 * 3600 + 200)] == [15, 60, 240, 1440]
    assert [h for _, h in oc.due_work(d, {"m1|15": 1}, NOW + 3800)] == [60]


def _disc_file(out, rows_):
    (out / "discoveries-2027-01-15.jsonl").write_text("\n".join(json.dumps(r) for r in rows_) + "\n")


def test_tracker_v1_files_sha_and_v0_rows_untouched(tmp_path):
    _disc_file(tmp_path, [{"mint": "m1", "pool": "p", "first_seen_s": T0, "price_native": 1.0, "is_pump_suffix": True},
                          {"mint": "m2", "pool": "p2", "first_seen_s": T0, "price_native": 1.0, "is_pump_suffix": False}])
    v0 = tmp_path / "outcomes-2027-01-15.jsonl"
    v0.write_text(json.dumps({"mint": "m1", "horizon_min": 15, "status": "ok", "max_mult": 9.0}) + "\n")
    (tmp_path / "outcomes-state.json").write_text(json.dumps({"done": {"m1|15": 1}}))
    before = v0.read_bytes()
    doc = doc_of([cd(BASE + 300, 1.5, 1.2), cd(BASE + 600, 1.4, 1.3)])
    urls = []

    def tr(u):
        urls.append(u)
        return 200, json.dumps(doc), {}
    gecko = SimpleNamespace(bucket=SimpleNamespace(acquire=lambda: None))
    tk = oc.Tracker(str(tmp_path), gecko, clock=lambda: T0 + 3800, sleep=lambda s: None, transport=tr)
    assert tk.step()["rows"] == 2  # +15m and +1h recomputed from first sight although a v0 row says m1|15 was done
    assert len(urls) == 2 and all("currency=token" in u and "/pools/p/ohlcv" in u for u in urls)
    out = [json.loads(l) for f in sorted(os.listdir(tmp_path)) if f.startswith("outcomes-v1-2") for l in open(tmp_path / f)]
    assert {r["horizon_min"] for r in out} == {15, 60} and all(r["mint"] == "m1" and r["tracker_sha"] == oc.tracker_sha() for r in out)
    assert v0.read_bytes() == before and not [f for f in os.listdir(tmp_path) if f.startswith("scores")]
    assert os.path.exists(tmp_path / "outcomes-v1-state.json")
    assert tk.step()["rows"] == 0
    tk2 = oc.Tracker(str(tmp_path), gecko, clock=lambda: T0 + 3800, sleep=lambda s: None, transport=tr)  # resume from v1 rows only
    os.remove(tmp_path / "outcomes-v1-state.json")
    assert oc.Tracker(str(tmp_path), gecko, clock=lambda: T0 + 3800, sleep=lambda s: None, transport=tr).step()["rows"] == 0
    assert tk2 is not None


def test_failures_become_no_data_after_three_attempts(tmp_path):
    _disc_file(tmp_path, [{"mint": "m1", "pool": "p", "first_seen_s": T0, "price_native": 1.0, "is_pump_suffix": True}])
    gecko = SimpleNamespace(bucket=SimpleNamespace(acquire=lambda: None))
    tk = oc.Tracker(str(tmp_path), gecko, clock=lambda: T0 + 1100, sleep=lambda s: None, transport=lambda u: (404, "{}", {}))
    for _ in range(3):
        tk.step()
    o = [json.loads(l) for f in os.listdir(tmp_path) if f.startswith("outcomes-v1-2") for l in open(tmp_path / f)]
    assert len(o) == 1 and o[0]["status"] == "no_data"


# ---- items 6, 7
def test_in_poll_duplicate_mint_handled_once(tmp_path):
    doc = gecko_doc(2)
    doc["data"][1]["relationships"]["base_token"]["data"]["id"] = doc["data"][0]["relationships"]["base_token"]["data"]["id"]
    cl = FakeCaller()
    col = mk(tmp_path, cl, docs=[doc])
    assert col.poll_once() == 1 and len(cl.calls) == 2 and len(rows(tmp_path, "discoveries")) == 1


def test_quote_mint_recorded_and_pages_deduped():
    p = dv.parse_new_pools(gecko_doc(1), NOW, 15)[0]
    assert p["quote_mint"] == au.WSOL
    calls = []

    class D(dv.Discovery):
        pass
    d = D(None, None, None, None)
    import tools.gradscore.discover as m
    orig = m.get_json
    m.get_json = lambda url, *a, **k: (calls.append(url), gecko_doc(1))[1]
    try:
        out = d.new_pools(NOW, 15, pages=2)
    finally:
        m.get_json = orig
    assert len(out) == 1 and len(calls) == 2 and "page=2" in calls[1]


# ---- auc
def test_auc_refuses_without_flag_and_without_start(tmp_path, capsys):
    assert au.main(["--dir", str(tmp_path), "--start", "2026-11-04T17:00:00Z"]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert au.main(["--dir", str(tmp_path), "--allow-outcomes"]) == 2


def test_auc_math():
    assert au.auc([1, 2, 3, 4], [0, 0, 1, 1]) == 1.0
    assert au.auc([1, 1, 1, 1], [0, 1, 0, 1]) == 0.5
    assert au.auc([1, 2], [1, 1]) is None


def _write_world(tmp_path, n=360, days=5, signal=True, dup_mint=None, v0_trap=True):
    rng = random.Random(7)
    t_start = 1_794_000_000.0
    disc, sc, o = [], [], []
    for i in range(n):
        t = t_start + (i % days) * 86400 + i
        m = f"m{i}pump"
        runner = (i % 10 == 0)
        disc.append({"mint": m, "pool": f"p{i}", "first_seen_s": t, "is_pump_suffix": True, "quote_mint": au.WSOL, "mcap_usd": rng.random() * 1e5,
                     "liq_usd": rng.random() * 1e4, "vol_5m": rng.random() * 1e3, "buys_5m": rng.randint(0, 50), "sells_5m": rng.randint(0, 50)})
        for v in "AB":
            p = (20 + rng.random() * 5 if runner else rng.random() * 10) if signal else rng.random() * 30
            sc.append({"mint": m, "variant": v, "action": "SCORED", "first_seen_s": t, "runner_prob_pct": p, "narrative": rng.randint(1, 5),
                       "rug_risk": 1 if runner else rng.randint(2, 5), "skip_reason": None})
        o.append({"mint": m, "horizon_min": 1440, "status": "ok", "max_mult": 4.0 if runner else 1.2})
        o.append({"mint": m, "horizon_min": 240, "status": "ok", "ret": 0.1 if runner else -0.2})
    if dup_mint:
        disc.append(dict(disc[0], first_seen_s=disc[0]["first_seen_s"] + 5))
    sc.append({"mint": "m0pump", "variant": "A", "action": "SCORED", "first_seen_s": disc[0]["first_seen_s"], "runner_prob_pct": 0.0,
               "narrative": 1, "rug_risk": 5, "skip_reason": None})  # later row must NOT overwrite the first
    (tmp_path / "discoveries-2026-11-05.jsonl").write_text("\n".join(json.dumps(r) for r in disc))
    (tmp_path / "scores-2026-11-05.jsonl").write_text("\n".join(json.dumps(r) for r in sc))
    (tmp_path / "outcomes-v1-2026-11-06.jsonl").write_text("\n".join(json.dumps(r) for r in o))
    if v0_trap:  # a v0 outcome file that would flip every label; must never be read
        (tmp_path / "outcomes-2026-11-06.jsonl").write_text("\n".join(json.dumps(dict(r, max_mult=0.1)) for r in o))
    return t_start


def _run(tmp_path, capsys, extra=()):
    t = _write_world(tmp_path, **dict(extra))
    capsys.readouterr()
    au.main(["--dir", str(tmp_path), "--allow-outcomes", "--start", "2026-11-04T00:00:00Z", "--end", "2027-01-01T00:00:00Z", "--draws", "1000"])
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    return t, lines


def test_read_rule_pass_path_direction_first_row_and_v1_only(tmp_path, capsys):
    _, (head, a, b) = _run(tmp_path, capsys)
    assert a["status"] == "READ" and a["n"] == 360 and a["positives"] == 36 and a["days"] == 5
    cells = {c["score"]: c for c in a["cells"]}
    assert set(cells) == {"runner_prob_pct", "narrative", "-rug_risk"}
    assert cells["runner_prob_pct"]["auc"] == 1.0 and cells["-rug_risk"]["auc"] > 0.9  # rug_risk entered negated; v0 trap not read
    assert cells["runner_prob_pct"]["placebo_p"] < au.ALPHA and cells["runner_prob_pct"]["pass"]  # first row kept (later 0.0 row ignored)
    assert not cells["narrative"]["pass"] and a["baseline"]["name"] in au.BASELINES
    for k in ("discovered_per_day", "cap_skip_rows_per_day", "parse_skip_rate", "no_data_rate_24h", "excluded", "days_in_window"):
        assert k in head
    assert "walk-2-hour information" in head["header"] and "best of 6" in head["header"]


def test_multi_discovery_excluded_and_counted(tmp_path, capsys):
    _, (head, a, b) = _run(tmp_path, capsys, extra={"dup_mint": True}.items())
    assert head["excluded"]["multi_discovery"] == 1 and a["n"] == 359


def test_not_decidable_without_minimums(tmp_path, capsys):
    _, (head, a, b) = _run(tmp_path, capsys, extra={"n": 100}.items())
    assert a["status"] == "NOT_DECIDABLE" and "cells" not in a


def test_placebo_p_formula_and_noise_is_not_pass():
    rows_ = [{"day": f"d{i % 4}", "label": i % 7 == 0, "x": float(i % 13)} for i in range(400)]
    obs = au.auc([r["x"] for r in rows_], [r["label"] for r in rows_])
    p = au.perm_p(rows_, "x", obs, draws=200, seed=1)
    assert 1 / 201 <= p <= 1.0
    assert au.perm_p(rows_, "x", 2.0, draws=50, seed=1) == 1 / 51  # nothing reaches 2.0: p = (1+0)/(50+1)


def test_ret4_is_report_only(tmp_path, capsys):
    _, (head, a, b) = _run(tmp_path, capsys)
    assert "report_only_auc_ret_4h_gt_0" in a["cells"][0] and "ret_4h" not in json.dumps(a["baseline"])
