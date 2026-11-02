import json
import os

import pytest

from tools.slowbot import fixtures as fx, paths, prescreen as ps, universe as uv, quotes as qt, scan
from tools.slowbot.sources import DexScreener, Gecko, HttpError, TokenBucket, get_json

NOW = fx.NOW_MS / 1000.0
DAY = "2027-01-15"


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def kit(transport=fx.transport):
    fc = fx.FakeClock()
    return (DexScreener(transport, fc.sleep, fc.now), Gecko(transport, fc.sleep, fc.now),
            qt.Jupiter(transport, fc.sleep, fc.now, per_min=60000), fc, (lambda: NOW + fc.t))


def run(tmp_path, transport=fx.transport, **kw):
    ds, gt, jup, fc, wall = kit(transport)
    kw.setdefault("warm_start", False)
    kw.setdefault("requote_delays", (0, 0))
    return scan.scan_once(ds, gt, jup, str(tmp_path), now_s=kw.pop("now_s", NOW), sleep=fc.sleep, clock=wall, log=lambda *a: None, **kw)


def _read(p):
    return [json.loads(l) for l in open(p)]


# ---- sources
def test_bucket_limits_rate():
    c = Clock()
    b = TokenBucket(60, c.now, c.sleep)
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


def test_non_json_200_is_http_error():
    c = Clock()
    with pytest.raises(HttpError):
        get_json("http://x/y", TokenBucket(600, c.now, c.sleep), lambda u: (200, "<html>cf</html>", {}), c.sleep)


def test_snapshot_batches_of_30():
    calls = []

    def tr(url):
        calls.append(url)
        return 200, "[]", {}
    DexScreener(tr, lambda s: None).pool_snapshots([f"m{i}" for i in range(65)])
    assert [len(u.split("/solana/")[1].split(",")) for u in calls] == [30, 30, 5]


def test_event_lists_solana_only_and_gecko():
    ds, gt, _, _, _ = kit()
    assert ds.recent_boosts() == [fx.M["EVENT"]]
    assert ds.community_takeovers() == [fx.M["KNIFE"]]
    assert gt.new_pool_mints()[0] == fx.M["GOOD"]
    assert gt.high_6h("P") == pytest.approx(1.1)
    assert Gecko(lambda u: (500, "", {}), lambda s: None).high_6h("P") is None


# ---- universe
def _rec(**kw):
    return uv.normalize(fx.pair(fx.M["GOOD"], **kw), fx.NOW_MS)


def test_universe_thresholds():
    assert uv.check(_rec()) is None
    assert uv.check(_rec(age_min=29)) == "age_too_young"
    assert uv.check(_rec(age_min=48 * 60 + 1)) == "age_too_old"
    assert uv.check(_rec(liq=39_999)) == "liq_low"
    assert uv.check(_rec(v1=19_999)) == "vol_1h_low"
    assert uv.check(_rec(tx24=399)) == "txns_24h_low"
    assert uv.check(uv.normalize(fx.pair(fx.M["GOOD"], dex="orca"), fx.NOW_MS)) == "venue_excluded"


def test_normalize_survives_bad_numerics():
    p = fx.pair(fx.M["GOOD"])
    p["pairCreatedAt"] = "garbage"
    p["liquidity"] = {"usd": None}
    p["txns"] = {"h1": {"buys": "x"}}
    r = uv.normalize(p, fx.NOW_MS)
    assert r["liq_usd"] == 0.0 and r["buys_1h"] == 0


def test_venue_classes():
    assert _rec()["venue_class"] == "trade"
    m = uv.normalize(fx.pair(fx.M["GOOD"], dex="meteora", labels=["DAMM v2"]), fx.NOW_MS)
    assert (m["venue"], m["venue_class"]) == ("meteora-damm v2", "report_only")
    assert uv.normalize(fx.pair(fx.M["GOOD"], dex="raydium"), fx.NOW_MS)["venue_class"] == "report_only"


def _helius(auth_none=True, va_owner="POOL"):
    def post(body):
        m = body["method"]
        if m == "getAccountInfo":
            info = {"mintAuthority": None, "freezeAuthority": None if auth_none else "X"}
            return {"result": {"value": {"data": {"parsed": {"info": info}}}}}
        if m == "getTokenLargestAccounts":
            return {"result": {"value": [{"address": "va", "amount": "500"}, {"address": "a1", "amount": "200"},
                                         {"address": "a2", "amount": "100"}]}}
        if m == "getTokenSupply":
            return {"result": {"value": {"amount": "1000"}}}
        owners = {"va": va_owner, "a1": "h1", "a2": "h2"}
        return {"result": {"value": [{"data": {"parsed": {"info": {"owner": owners[a]}}}} for a in body["params"][0]]}}
    return post


def test_holder_checks_exclude_pool_and_authority(monkeypatch):
    monkeypatch.delenv("HELIUS_RPC_URL", raising=False)
    assert uv.holder_checks("m")["auth_status"] == "auth_unknown"
    h = uv.holder_checks("m", "POOL", post=_helius())
    assert h["top10_share"] == pytest.approx(0.3) and h["auth_status"] == "revoked" and h["holder_status"] == "ok"
    assert uv.holder_checks("m", None, post=_helius())["top10_share"] == pytest.approx(0.8)  # vault counted w/o pool
    assert uv.holder_checks("m", "POOL", post=_helius(auth_none=False))["auth_status"] == "authority_present"


def test_holder_checks_failure_is_unknown_and_never_leaks(monkeypatch):
    monkeypatch.setenv("HELIUS_RPC_URL", "https://rpc.example/?api-key=SECRETVALUE")

    def boom(body):
        raise RuntimeError("https://rpc.example/?api-key=SECRETVALUE")
    h = uv.holder_checks("m", post=boom)
    assert h == {"holder_status": "holder_unknown", "top10_share": None, "auth_status": "auth_unknown"}
    assert "SECRETVALUE" not in json.dumps(h)


# ---- prescreen
def test_prescreen_rules():
    r = _rec(age_min=300, v1=40_000, v6=60_000)  # prior 4 h avg 5000 -> ratio 8
    assert ps.rules_hit(r) == ["a"] and ps.prior_hours(r) == 4.0
    assert ps.rules_hit(_rec(age_min=120, v1=40_000, v6=60_000)) == []  # prior 1 h avg 20000 -> ratio 2
    assert ps.rules_hit(_rec(age_min=100, v1=70_000, v6=71_000)) == []  # < 1 prior hour: rule a off
    r = _rec(v1=10_000, v6=60_000, buys=300, sells=200)  # price 1.0
    assert ps.rules_hit(r, high_6h=1.1) == ["b"]
    assert ps.rules_hit(r, high_6h=1.3) == []  # 1.0 < 0.8*1.3
    assert ps.rules_hit(r, high_6h=None) == []
    r = _rec(v1=10_000, v6=60_000, buys=100, sells=200)
    assert ps.rules_hit(r, event_age_s=100) == ["c"]
    assert ps.rules_hit(r, event_age_s=3600) == []
    assert ps.rules_hit(r, event_age_s=None) == []


def test_prescreen_rejects():
    assert ps.reject_reason(_rec(c5=61.0)) == "chasing_5m"
    assert ps.reject_reason(_rec(c1=-51.0)) == "falling_knife_1h"
    assert ps.reject_reason(_rec()) is None


def test_r_uniform_seeded():
    assert ps.r_uniform("m", "t") == ps.r_uniform("m", "t")
    assert 0 <= ps.r_uniform("m", "t") < 1
    assert ps.r_uniform("m", "t") != ps.r_uniform("m2", "t")
    assert ps.PRESCREEN["arms"] == ["T", "H", "R"] and ps.CANDIDATE_KEYS[-1] == "arms"


# ---- quotes
def test_jupiter_round_trip_fixed_costs():
    j = qt.Jupiter(fx.transport, lambda s: None, per_min=60000)
    rt = j.round_trip(fx.M["GOOD"], now=lambda: 100.0)
    assert rt["ok"] and rt["ts"] == 100.0 and rt["quote_in_lamports"] == 25_000_000
    assert rt["token_out"] == 25_000_000 * 1000
    assert rt["quote_out_lamports"] == int(25_000_000 * 0.985)
    assert rt["roundtrip_bps"] == pytest.approx(150.0, abs=0.01)
    assert qt.FIXED_COST_LAMPORTS == 2 * 300_000 + 2_040_000
    assert rt["net_sell_back_lamports"] == rt["quote_out_lamports"] - qt.FIXED_COST_LAMPORTS
    assert rt["roundtrip_bps_net"] == pytest.approx(150.0 + qt.FIXED_COST_LAMPORTS / 25_000_000 * 1e4, abs=0.01)


def test_jupiter_failure_is_not_ok():
    assert qt.Jupiter(lambda u: (400, "", {}), lambda s: None).round_trip("m")["ok"] is False


def test_paper_fill_takes_worse_of_0_and_2s():
    q0 = {"ok": True, "token_out": 1000, "quote_in_lamports": 25, "quote_out_lamports": 24, "delay_s": 0}
    q2 = {"ok": True, "token_out": 900, "quote_in_lamports": 25, "quote_out_lamports": 22, "delay_s": 2}
    f = qt.paper_fill(q0, q2, {"ok": True, "token_out": 800})
    assert f["token_out"] == 900 and f["fill_from_delay_s"] == 2 and f["sensitivity_30s_token_out"] == 800
    assert qt.paper_fill(dict(q0, token_out=500), q2)["token_out"] == 500
    assert qt.paper_fill(q0, {"ok": False}) is None and qt.paper_fill(q0, None) is None


def test_requotes_relative_to_each_decision_time():
    t = [100.0]
    j = qt.Jupiter(fx.transport, lambda s: None, per_min=60000)
    sl = lambda s: t.__setitem__(0, t[0] + s)
    rows = qt.schedule_requotes(j, [(fx.M["GOOD"], 90.0), (fx.M["EVENT"], 100.0)], now=lambda: t[0], sleep=sl)
    assert [(r["mint"][:4], r["delay_s"]) for r in rows] == [("GOOD", 2), ("EVEN", 2), ("GOOD", 30), ("EVEN", 30)]
    assert rows[0]["actual_offset_s"] == pytest.approx(10.0) and rows[0]["offset_ok"] is False  # late, flagged
    assert rows[1]["actual_offset_s"] == pytest.approx(2.0) and rows[1]["offset_ok"] is True


# ---- scan
def test_scan_dry_fixture_end_to_end(tmp_path):
    c = run(tmp_path, requote_delays=(2, 30))
    assert c["pools_seen"] == 8 and c["candidates"] == 2 and c["report_only"] == 1 and c["quote_reject"] == 1
    assert c["prescreen_reject"] == 2 and c["no_pair"] == 1 and c["not_best_pool"] == 1
    files = sorted(os.listdir(tmp_path))
    assert files == [f"{p}-{DAY}.jsonl" for p in ("candidates", "checks", "quotes", "rejects", "scans")] + ["state.json"]
    cands = _read(tmp_path / f"candidates-{DAY}.jsonl")
    assert {x["mint"] for x in cands} == {fx.M["GOOD"], fx.M["EVENT"]}
    for x in cands:
        assert list(x) == ps.CANDIDATE_KEYS and x["arms"] == ["T", "H", "R"]
    by = {x["mint"]: x for x in cands}
    assert by[fx.M["GOOD"]]["rule_hit"] == "ab" and by[fx.M["GOOD"]]["pool"] == "P" + fx.M["GOOD"][:10]
    assert by[fx.M["EVENT"]]["rule_hit"] == "c"
    rej = _read(tmp_path / f"rejects-{DAY}.jsonl")
    assert {r["reason"] for r in rej} >= {"age_too_young", "liq_low", "chasing_5m", "falling_knife_1h", "quote_cost",
                                          "venue_report_only", "no_pair_returned", "lower_liquidity_pool"}
    uni = [r for r in rej if r["stage"] == "universe"]
    assert all(r["pool"] and "liq_usd" in r and "age_min" in r and "txns_24h" in r for r in uni)
    q = _read(tmp_path / f"quotes-{DAY}.jsonl")
    assert [x["kind"] for x in q].count("paper_fill") == 2
    d0 = [x for x in q if x.get("delay_s") == 0 and x["kind"] == "quote"]
    assert len(d0) == 2 and all(x["token_out"] > 0 and x["route"] for x in d0)
    fills = [x for x in q if x["kind"] == "paper_fill"]
    assert all(not f["failed_entry"] and f["token_out"] > 0 and f["fixed_cost_lamports"] == qt.FIXED_COST_LAMPORTS for f in fills)
    chk = _read(tmp_path / f"checks-{DAY}.jsonl")
    assert all(c["holder_check_ran"] is False and c["authority_checked"] is False and 0 <= c["r_uniform"] < 1 for c in chk)
    sc = _read(tmp_path / f"scans-{DAY}.jsonl")
    assert sc[0]["counts"]["candidates"] == 2 and sc[0]["source_errors"] == {}


def test_t_dec_is_real_quote_time_and_requote_offsets(tmp_path):
    run(tmp_path, requote_delays=(2, 30))
    q = [x for x in _read(tmp_path / f"quotes-{DAY}.jsonl") if x["kind"] == "quote"]
    t0 = {x["mint"]: x["ts"] for x in q if x["delay_s"] == 0}
    for x in q:
        if x["delay_s"] in (2, 30):
            assert x["ts"] - t0[x["mint"]] == pytest.approx(x["actual_offset_s"])
            assert abs(x["actual_offset_s"] - x["delay_s"]) <= 1.0 and x["offset_ok"]


def test_dedupe_24h_persisted(tmp_path):
    run(tmp_path)
    c = run(tmp_path, requote_delays=(2, 30))
    assert c["candidates"] == 0 and c["dedupe_reject"] == 2
    assert any(r["reason"] == "seen_24h" for r in _read(tmp_path / f"rejects-{DAY}.jsonl"))
    c = run(tmp_path, now_s=NOW + 25 * 3600)
    assert c["dedupe_reject"] == 0


def test_rule_c_new_only_and_stale_and_warm(tmp_path):
    # stale: id first seen 1 h ago -> no rule c
    (tmp_path / "state.json").write_text(json.dumps({"events": {fx.M["EVENT"]: {"first_seen": NOW - 3600, "warm": False}}}))
    c = run(tmp_path, requote_delays=(2, 30))
    assert c["candidates"] == 1  # only GOOD
    # warm start on an empty state: listed ids are not fresh
    d = tmp_path / "w"
    c = run(d, warm_start=True)
    assert c["candidates"] == 1
    st = json.loads((d / "state.json").read_text())
    assert st["events"][fx.M["EVENT"]]["warm"] is True


def test_cap_checked_before_quote(tmp_path, monkeypatch):
    monkeypatch.setitem(ps.PRESCREEN, "cap_per_hour", 1)
    c = run(tmp_path, requote_delays=(2, 30))
    assert c["candidates"] == 1 and c["cap_reject"] == 2 and c["quote_reject"] == 0
    cap = [r for r in _read(tmp_path / f"rejects-{DAY}.jsonl") if r["stage"] == "cap"]
    assert all(r["source_rank"] for r in cap)


def test_holder_flag_checks_and_rejects(tmp_path):
    c = run(tmp_path, top_holders=True, helius_post=_helius(va_owner=next(iter(uv.KNOWN_AMM_OWNERS))))
    chk = _read(tmp_path / f"checks-{DAY}.jsonl")
    assert c["candidates"] == 2 and all(x["holder_check_ran"] and x["authority_checked"] and x["auth_status"] == "revoked" for x in chk)
    d = tmp_path / "x"
    c = run(d, top_holders=True, helius_post=_helius(auth_none=False, va_owner=next(iter(uv.KNOWN_AMM_OWNERS))))
    assert c["candidates"] == 0 and c["holder_reject"] == 3


def test_holder_unavailable_passes_and_is_logged(tmp_path):
    def post(body):
        raise RuntimeError("down")
    c = run(tmp_path, top_holders=True, helius_post=post)
    assert c["candidates"] == 2
    assert {x["auth_status"] for x in _read(tmp_path / f"checks-{DAY}.jsonl")} == {"auth_unknown"}


# ---- crash safety
def test_html_responses_do_not_crash_scan(tmp_path):
    c = run(tmp_path, transport=lambda u: (200, "<html>cf</html>", {}))
    assert c["source_errors"] >= 5 and c["candidates"] == 0
    assert _read(tmp_path / f"scans-{DAY}.jsonl")[0]["source_errors"]


def test_loop_survives_scan_exception(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise ValueError("x")
    monkeypatch.setattr(scan, "scan_once", boom)

    def stop(_):
        raise SystemExit(0)
    monkeypatch.setattr(scan.time, "sleep", stop)
    with pytest.raises(SystemExit):
        scan.main(["--loop", "--dry-fixtures", "--out", str(tmp_path)])
    assert "scan failed: ValueError" in capsys.readouterr().out
    assert scan.main(["--once", "--dry-fixtures", "--out", str(tmp_path)]) == 1


def test_cli_dry_fixtures(tmp_path, capsys):
    assert scan.main(["--once", "--dry-fixtures", "--out", str(tmp_path)]) == 0
    assert "candidates=1" in capsys.readouterr().out  # first run is warm start: boost id EVENT not fresh


# ---- reject forward path
def test_reject_paths_horizons_and_idempotent(tmp_path):
    ts = "2027-01-15T07:30:00Z"  # 2.5 h before NOW? NOW is 2027-01-15T08:00:00Z or similar; compute below
    import datetime as dt
    now_ts = dt.datetime.fromtimestamp(NOW, dt.timezone.utc)
    rt = ps.utc_iso(now_ts - dt.timedelta(minutes=150))
    day = rt[:10]
    ps.append_jsonl(str(tmp_path / f"rejects-{day}.jsonl"), {"ts_utc": rt, "stage": "prescreen", "reason": "chasing_5m",
                                                           "mint": fx.M["KNIFE"], "price_usd": 2.0})
    ps.append_jsonl(str(tmp_path / f"rejects-{day}.jsonl"), {"ts_utc": rt, "stage": "universe", "reason": "liq_low",
                                                           "mint": fx.M["LOWLIQ"], "price_usd": 2.0})
    ds, _, _, _, _ = kit()
    state = {}
    assert paths.run_paths(ds, str(tmp_path), NOW, state, lambda *a: None) == 2  # +30 m and +2 h, not +4 h
    rows = _read(tmp_path / f"rejects-path-{ps.utc_iso(now_ts)[:10]}.jsonl")
    assert sorted(r["horizon_min"] for r in rows) == [30, 120] and all(r["ratio"] == 0.5 for r in rows)
    assert paths.run_paths(ds, str(tmp_path), NOW + 60, state, lambda *a: None) == 0
    assert paths.run_paths(ds, str(tmp_path), NOW + 100 * 60, state, lambda *a: None) == 1  # +4 h now due
    assert paths.due({"last_paths_ts": NOW}, NOW + 1799) is False and paths.due({"last_paths_ts": NOW}, NOW + 1800)


def test_cli_dry_path_hooked(tmp_path):
    run(tmp_path)
    assert json.loads((tmp_path / "state.json").read_text())["last_paths_ts"] == NOW
