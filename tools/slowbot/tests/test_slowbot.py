import json
import os

import pytest

from tools.slowbot import fixtures as fx, prescreen as ps, universe as uv, quotes as qt, scan
from tools.slowbot.sources import DexScreener, Gecko, HttpError, TokenBucket, get_json


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_bucket_limits_rate():
    c = Clock()
    b = TokenBucket(60, c.now, c.sleep)  # 1/s, burst 10
    for _ in range(10):
        b.acquire()
    assert c.slept == []
    b.acquire()
    assert c.t == pytest.approx(1.0)


def test_limits_declared():
    from tools.slowbot.sources import RATE_LIMITS_PER_MIN as L
    assert L["ds_pairs"] <= 250 and L["ds_profiles"] <= 55 and L["gecko"] <= 25


def test_retry_backoff_429_5xx_then_ok():
    c = Clock()
    seq = iter([(429, "", {"Retry-After": "5"}), (503, "", {}), (200, '{"a":1}', {})])
    out = get_json("http://x/y", TokenBucket(600, c.now, c.sleep), lambda u: next(seq), c.sleep)
    assert out == {"a": 1}
    assert c.slept[0] == 5.0 and c.slept[1] == 2.0


def test_retry_gives_up_and_4xx_fails_fast():
    c = Clock()
    with pytest.raises(HttpError):
        get_json("http://x/y", TokenBucket(600, c.now, c.sleep), lambda u: (500, "", {}), c.sleep)
    n = []
    with pytest.raises(HttpError):
        get_json("http://x/y", TokenBucket(600, c.now, c.sleep), lambda u: (n.append(1), (404, "", {}))[1], c.sleep)
    assert len(n) == 1


def test_snapshot_batches_of_30():
    calls = []

    def tr(url):
        calls.append(url)
        return 200, "[]", {}
    DexScreener(tr, lambda s: None).pool_snapshots([f"m{i}" for i in range(65)])
    assert len(calls) == 3
    assert [len(u.split("/solana/")[1].split(",")) for u in calls] == [30, 30, 5]


def test_event_lists_solana_only_and_gecko_mints():
    ds, gt = DexScreener(fx.transport, lambda s: None), Gecko(fx.transport, lambda s: None)
    assert ds.recent_boosts() == [fx.M["EVENT"]]
    assert ds.community_takeovers() == [fx.M["KNIFE"]]
    assert gt.new_pool_mints()[0] == fx.M["GOOD"]
    assert fx.M["BADQ"] in gt.trending_pool_mints()


def _rec(**kw):
    return uv.normalize(fx.pair(fx.M["GOOD"], **kw), fx.NOW_MS)


def test_universe_thresholds():
    assert uv.check(_rec()) is None
    assert uv.check(_rec(age_min=29)) == "age_too_young"
    assert uv.check(_rec(age_min=48 * 60 + 1)) == "age_too_old"
    assert uv.check(_rec(liq=39_999)) == "liq_low"
    assert uv.check(_rec(v1=19_999)) == "vol_1h_low"
    assert uv.check(_rec(tx24=399)) == "txns_24h_low"
    assert uv.check(fx.pair and uv.normalize(fx.pair(fx.M["GOOD"], dex="orca"), fx.NOW_MS)) == "venue_excluded"


def test_venue_classes():
    assert _rec()["venue_class"] == "trade"
    m = uv.normalize(fx.pair(fx.M["GOOD"], dex="meteora", labels=["DAMM v2"]), fx.NOW_MS)
    assert (m["venue"], m["venue_class"]) == ("meteora-damm v2", "report_only")
    assert uv.normalize(fx.pair(fx.M["GOOD"], dex="raydium"), fx.NOW_MS)["venue_class"] == "report_only"


def test_top10_flag_and_key_not_leaked(monkeypatch):
    monkeypatch.delenv("HELIUS_RPC_URL", raising=False)
    assert uv.top10_share("m") is None
    monkeypatch.setenv("HELIUS_RPC_URL", "https://rpc.example/?api-key=SECRETVALUE")

    def post(body):
        if body["method"] == "getTokenLargestAccounts":
            return {"result": {"value": [{"address": "pool", "amount": "500"}, {"address": "a", "amount": "200"},
                                         {"address": "b", "amount": "100"}]}}
        return {"result": {"value": {"amount": "1000"}}}
    assert uv.top10_share("m", post=post, exclude={"pool"}) == pytest.approx(0.3)

    def boom(body):
        raise RuntimeError("https://rpc.example/?api-key=SECRETVALUE")
    assert uv.top10_share("m", post=boom) is None
    r = _rec()
    r["top10_share"] = 0.31
    assert uv.check(r, flag_top_holders=True) == "top10_share_high"
    assert uv.check(r, flag_top_holders=False) is None


def test_prescreen_rules():
    r = _rec(v1=40_000, v6=60_000)  # prior avg 4000/h -> ratio 10
    assert "a" in ps.rules_hit(r)
    r = _rec(v1=10_000, v6=60_000, buys=300, sells=200)  # not a; buys/sells 1.5; held (all changes positive)
    assert ps.rules_hit(r) == ["b"]
    r = _rec(v1=10_000, v6=60_000, buys=100, sells=200)
    assert ps.rules_hit(r) == []
    assert ps.rules_hit(r, event_age_s=100) == ["c"]
    assert ps.rules_hit(r, event_age_s=3600) == []
    # b fails when price fell below 80% of an earlier point: chg_6h=+50 means 6h-ago price 0.667; chg_1h=-30 -> 1h-ago 1.43
    r = _rec(v1=10_000, v6=60_000, c1=-30.0, c6=50.0)
    assert "b" not in ps.rules_hit(r)


def test_prescreen_rejects():
    assert ps.reject_reason(_rec(c5=61.0)) == "chasing_5m"
    assert ps.reject_reason(_rec(c1=-51.0)) == "falling_knife_1h"
    assert ps.reject_reason(_rec()) is None


def test_arm_assigner_seeded_and_covers_arms():
    assert ps.assign_arm("m", "t") == ps.assign_arm("m", "t")
    seen = {ps.assign_arm(f"m{i}", "t") for i in range(60)}
    assert seen == {"T", "H", "R"}
    other = dict(ps.PRESCREEN, arm_seed=2)
    assert any(ps.assign_arm(f"m{i}", "t") != ps.assign_arm(f"m{i}", "t", other) for i in range(30))


def test_jupiter_round_trip_and_requotes():
    j = qt.Jupiter(fx.transport, lambda s: None)
    rt = j.round_trip(fx.M["GOOD"], now=lambda: 100.0)
    assert rt["ok"] and rt["quote_in_lamports"] == 25_000_000
    assert rt["quote_out_lamports"] == int(25_000_000 * 0.985)
    assert rt["roundtrip_bps"] == pytest.approx(150.0, abs=0.01)
    assert rt["impact_bps"] == pytest.approx(30.0)
    assert qt.paper_fill(rt)["in_lamports"] == 25_000_000
    t = [100.0]
    slept = []
    rows = qt.requotes(j, fx.M["GOOD"], 100.0, now=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    assert [r["delay_s"] for r in rows] == [2, 30] and slept == [2.0, 28.0]


def test_jupiter_failure_is_not_ok():
    j = qt.Jupiter(lambda u: (400, "", {}), lambda s: None)
    assert j.round_trip("m")["ok"] is False


def _read(p):
    return [json.loads(l) for l in open(p)]


def test_scan_dry_fixture_end_to_end(tmp_path):
    fc = fx.FakeClock()
    ds, gt, jup = DexScreener(fx.transport, fc.sleep, fc.now), Gecko(fx.transport, fc.sleep, fc.now), qt.Jupiter(fx.transport, fc.sleep, fc.now)
    c = scan.scan_once(ds, gt, jup, str(tmp_path), now_s=fx.NOW_MS / 1000.0, requote_delays=(0, 0), sleep=lambda s: None)
    assert c["pools_seen"] == 8 and c["candidates"] == 2 and c["report_only"] == 1 and c["quote_reject"] == 1
    assert c["prescreen_reject"] == 2
    files = sorted(os.listdir(tmp_path))
    assert files == ["candidates-2027-01-15.jsonl", "quotes-2027-01-15.jsonl", "rejects-2027-01-15.jsonl"]
    cands = _read(tmp_path / files[0])
    assert {x["mint"] for x in cands} == {fx.M["GOOD"], fx.M["EVENT"]}
    for x in cands:
        assert list(x) == ps.CANDIDATE_KEYS and x["arm"] in "THR"
    by = {x["mint"]: x for x in cands}
    assert by[fx.M["GOOD"]]["rule_hit"] == "ab"
    assert by[fx.M["EVENT"]]["rule_hit"] == "c"
    reasons = {r["reason"] for r in _read(tmp_path / files[2])}
    assert {"age_too_young", "liq_low", "chasing_5m", "falling_knife_1h", "quote_cost", "venue_report_only"} <= reasons
    assert len(_read(tmp_path / files[1])) == 4  # 2 candidates x 2 requotes


def test_cap_per_hour_and_append_only(tmp_path, monkeypatch):
    fc = fx.FakeClock()
    ds, gt, jup = DexScreener(fx.transport, fc.sleep, fc.now), Gecko(fx.transport, fc.sleep, fc.now), qt.Jupiter(fx.transport, fc.sleep, fc.now)
    monkeypatch.setitem(ps.PRESCREEN, "cap_per_hour", 3)
    kw = dict(now_s=fx.NOW_MS / 1000.0, do_requote=False, sleep=lambda s: None)
    scan.scan_once(ds, gt, jup, str(tmp_path), **kw)
    c2 = scan.scan_once(ds, gt, jup, str(tmp_path), **kw)
    assert c2["candidates"] == 1 and c2["cap_reject"] == 1
    assert len(_read(tmp_path / "candidates-2027-01-15.jsonl")) == 3


def test_cli_dry_fixtures(tmp_path, capsys):
    assert scan.main(["--once", "--dry-fixtures", "--out", str(tmp_path)]) == 0
    assert "candidates=2" in capsys.readouterr().out
