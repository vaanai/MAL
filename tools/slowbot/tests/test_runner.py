"""Tests-only end to end for tools/slowbot/runner.py: fixtures, a stubbed model and stubbed prices. No network, no key."""
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from tools.slowbot import decide as dc, fixtures as fx, prescreen as ps, runner as rn
from tools.slowbot.quotes import Jupiter
from tools.slowbot.sources import DexScreener, Gecko
from tools.slowbot.tests.test_slowbot import NOW

GOOD = fx.M["GOOD"]
DAY = "2027-01-15"


class Clock(fx.FakeClock):
    def sleep(self, s):  # a floor keeps the token bucket from stalling on float absorption (t + 1e-16 == t)
        self.t += max(s, 1e-6)


class Price:
    """Sell-side multiplier after a fake-clock instant; buy quotes are untouched."""
    def __init__(self, fc, after=100.0, mult=0.5):
        self.fc, self.after, self.mult = fc, after, mult

    def __call__(self, url):
        code, body, h = fx.transport(url)
        if "lite-api.jup.ag" in url and f"inputMint={fx.WSOL}" not in url and self.fc.t > self.after:
            d = json.loads(body)
            d["outAmount"] = str(int(int(d["outAmount"]) * self.mult))
            body = json.dumps(d)
        return code, body, h


class FakeCaller:
    def __init__(self, text='{"action":"BUY","conviction":4,"catalyst":"volume","risk":"none"}'):
        self.text, self.n, self.cards = text, 0, []

    def call(self, **kw):
        self.n += 1
        self.cards.append(kw["messages"][0]["content"])
        return SimpleNamespace(text=self.text, usage={}, cost_usd=0.001, request_sha256="x" * 64, replayed=False)


def seed_for_r_open():
    """A seed whose R draw (rate 0.5, no history) does not skip the GOOD candidate at the fixture scan instant."""
    cid = f"{GOOD}:{DAY}T08:00:00Z"
    return next(s for s in range(1000) if not dc.r_skip(s, cid, 0.5))


def mk(tmp_path, price=False, caller=None, haiku_on=True, like=None, like_fc=None, **kw):
    fc = Clock()
    tr = Price(fc) if price else fx.transport
    ds, gt, jup = DexScreener(tr, fc.sleep, fc.now), Gecko(tr, fc.sleep, fc.now), Jupiter(tr, fc.sleep, fc.now, per_min=60000)
    if like is not None:  # reuse the HTTP clients (their rate buckets) and the virtual clock; all run state is reloaded from disk
        ds, gt, jup, fc = like.ds, like.gt, like.jup, like_fc
    r = rn.Runner(str(tmp_path), ds=ds, gt=gt, jup=jup, caller=caller, haiku_on=haiku_on, clock=lambda: NOW + fc.t,
                  sleep=fc.sleep, log=lambda *a: None, seed=kw.pop("seed", seed_for_r_open()), **kw)
    return r, fc


def rows(path):
    return rn._read_jsonl(str(path))


def iso(t):
    return ps.utc_iso(dt.datetime.fromtimestamp(t, dt.timezone.utc))


def test_end_to_end_two_scans_exit_restart_report(tmp_path):
    caller = FakeCaller()
    r, fc = mk(tmp_path, price=True, caller=caller)
    r.run(duration=60)  # first scan only: 1 candidate, T/H/R each open one position
    pos = rows(tmp_path / "positions.jsonl")
    assert sorted(x["arm"] for x in pos if x["kind"] == "open") == ["H", "R", "T"]
    assert not [x for x in pos if x["kind"] == "exit"]
    assert caller.n == 1
    dec = rows(tmp_path / "decisions.jsonl")
    assert [x["skipped"] for x in dec if x.get("kind") == "r_draw"] == [False]
    assert [x for x in dec if "action" in x][0]["h_buy"] is True

    # crash and restart: a fresh Runner on the same dir resumes the 3 open positions, the clock continues
    st = json.load(open(tmp_path / "state.json"))
    st["admitted"] = {}  # as if the kill came before the scanner saved its admitted map
    json.dump(st, open(tmp_path / "state.json", "w"))
    r2, fc2 = mk(tmp_path, price=True, caller=caller, like=r, like_fc=fc)  # same clients and virtual clock; state from disk
    assert len(r2.pm.open_positions()) == 3
    assert r2.restored >= 1  # dedupe rebuilt from candidates-*.jsonl
    r2.run(duration=700)  # second scan (deduped: no new candidate); the price halves after t=100 -> stops
    assert caller.n == 1  # no repeated model call for the same candidate
    pos = rows(tmp_path / "positions.jsonl")
    assert len([x for x in pos if x["kind"] == "open"]) == 3
    exits = [x for x in pos if x["kind"] == "exit"]
    assert sorted(x["arm"] for x in exits) == ["H", "R", "T"] and {x["reason"] for x in exits} == {"stop"}
    sc = rows(next(tmp_path.glob("scans-*.jsonl")))
    assert len(sc) >= 2 and sc[-1]["counts"]["candidates"] == 0 and sc[-1]["counts"]["dedupe_reject"] >= 1

    # daily report was written (fixture time is 08:00Z, past 00:05Z) and carries both rent cases
    assert (tmp_path / "reports" / f"arms-{DAY}.json").exists()
    con = json.load(open(tmp_path / "reports" / "console-slowbot.json"))
    assert con["paper_only"] is True and con["live_path"] is False
    rep = r2.make_report(DAY)
    for a in "THR":
        s = rep["rent_refunded"]["arms"][a]
        assert s["trades"] == 1
        assert s["net_sol"] == pytest.approx(rep["arms"][a]["net_sol"] + 2_039_280 / 1e9)
        assert s["unrefunded_net_sol"] == rep["arms"][a]["net_sol"]
    assert "rent refunded" in open(tmp_path / "reports" / f"arms-{DAY}.txt").read()


def test_no_haiku_logs_h_unavailable_and_h_opens_nothing(tmp_path):
    r, fc = mk(tmp_path, haiku_on=False)
    r.run(duration=40)
    dec = rows(tmp_path / "decisions.jsonl")
    assert [x["reason"] for x in dec if x.get("kind") == "h_unavailable"] == ["no_haiku"]
    assert sorted(x["arm"] for x in rows(tmp_path / "positions.jsonl") if x["kind"] == "open") == ["R", "T"]
    assert r.rs["counters"]["h_unavailable"] == 1 and r.rs["counters"]["h_calls"] == 0


def test_missing_key_caller_none_is_h_unavailable(tmp_path):
    r, fc = mk(tmp_path, caller=None, haiku_on=True)
    r.run(duration=40)
    assert [x["reason"] for x in rows(tmp_path / "decisions.jsonl") if x.get("kind") == "h_unavailable"] == ["no_api_key"]


def test_h_skip_opens_t_and_r_only(tmp_path):
    skip = '{"action":"SKIP","conviction":2,"catalyst":"none","risk":"rug"}'
    r, fc = mk(tmp_path, caller=FakeCaller(skip))
    r.run(duration=40)
    assert sorted(x["arm"] for x in rows(tmp_path / "positions.jsonl") if x["kind"] == "open") == ["R", "T"]


def test_failed_and_late_q2_counted_and_failed_refuses_entry(tmp_path):
    r, fc = mk(tmp_path, haiku_on=False)
    c = {"mint": "MX", "ts_utc": f"{DAY}T08:00:00Z"}

    def q(**k):
        return dict({"ts_utc": c["ts_utc"], "mint": "MX", "kind": "quote", "delay_s": 2, "ok": True, "offset_ok": True}, **k)
    fill = {"ts_utc": c["ts_utc"], "mint": "MX", "kind": "paper_fill", "failed_entry": False, "in_lamports": 25_000_000,
            "token_out": 7}
    assert r._entry_from_quotes(c, [q(ok=False), dict(fill, failed_entry=True)]) is None
    assert r._entry_from_quotes(c, [q(offset_ok=False), fill])["out_lamports"] == 7  # late: kept and counted
    assert r._entry_from_quotes(c, []) is None
    ct = r.rs["counters"]
    assert (ct["q2_total"], ct["q2_failed"], ct["q2_late"]) == (3, 2, 1)


def test_dedupe_rebuilt_from_today_and_yesterday_candidates(tmp_path):
    for ts, m in ((iso(NOW - 86400 + 3600), "MY"), (iso(NOW - 10), "MT"), (iso(NOW - 86400 - 7200), "MOLD")):
        ps.append_jsonl(ps.day_file(str(tmp_path), "candidates", ts), {"ts_utc": ts, "mint": m})
    assert rn.rebuild_admitted(str(tmp_path), NOW) == 2
    assert set(json.load(open(tmp_path / "state.json"))["admitted"]) == {"MY", "MT"}


def test_stale_unprocessed_candidate_after_restart_is_not_opened(tmp_path):
    r, fc = mk(tmp_path, haiku_on=False)
    ts = iso(NOW - 3600)
    ps.append_jsonl(ps.day_file(str(tmp_path), "candidates", ts), {"ts_utc": ts, "mint": "MS", "liq_usd": 5e4})
    r.process_new_candidates()
    assert r.rs["counters"]["missed_restart"] == 1 and not r.pm.open_positions()
    assert rows(tmp_path / "decisions.jsonl")[0]["kind"] == "missed_restart"


def test_x_feed_file_max5_ticker_match_and_absent(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("\n".join(json.dumps({"mint": "MX" if i % 2 else "OTHER", "text": f"t{i}"}) for i in range(20)) + "\n"
                 + json.dumps({"ticker": "AbC", "text": "by ticker"}) + "\n")
    xf = rn.XFeed(str(p))
    assert len(xf.snippets("MX", "zzz")) == 5
    assert xf.snippets("NONE", "abc") == ["by ticker"]
    assert xf.snippets("NONE", "zzz") is None

    def boom(u):
        raise OSError()
    assert rn.XFeed("http://127.0.0.1:1/x", fetch=boom).snippets("a", "b") is None
    caller = FakeCaller()
    r, fc = mk(tmp_path / "o", caller=caller)  # no --x-feed: the card has no x_context
    r.run(duration=40)
    assert "x_context" not in caller.cards[0]
    caller2 = FakeCaller()
    q = tmp_path / "x2.json"
    q.write_text(json.dumps([{"ticker": fx.M["GOOD"][:4], "text": "gm"}]))
    r2, _ = mk(tmp_path / "o2", caller=caller2, x_feed=rn.XFeed(str(q)))
    r2.run(duration=40)
    assert '"x_context":["gm"]' in caller2.cards[0]


def test_quote_exception_in_poll_is_a_miss_not_a_crash(tmp_path):
    r, fc = mk(tmp_path, haiku_on=False)
    r.run(duration=40)

    def boom(*a, **k):
        raise TimeoutError()
    r.jup.quote = boom
    r.poll_once()
    assert any(x["kind"] == "price_miss" for x in rows(tmp_path / "positions.jsonl"))


def test_heartbeat_writes_progress(tmp_path):
    pp = tmp_path / "prog.json"
    r, fc = mk(tmp_path / "o", haiku_on=False, progress_path=str(pp))
    r.run(duration=40)
    d = json.load(open(pp))
    assert d["pct"] == 100 and "slowbot paper" in d["note"]
