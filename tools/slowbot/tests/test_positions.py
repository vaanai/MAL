import json

import pytest

from tools.slowbot import positions as P

T0 = 1_800_000_000.0
TX = P.TX_COST_LAMPORTS
ENTRY_FEES = TX + P.ATA_RENT_LAMPORTS


class Clock:
    t = T0

    def __call__(self):
        return self.t


class Quoter:
    """Sell-quote stub: ratio r of entry unit value (entry unit = 25 lamports/token). A queue overrides ratio once per call."""
    def __init__(self):
        self.ratio, self.liq, self.fail, self.raises, self.queue = 1.0, 50_000.0, False, False, []

    def __call__(self, mint, amount):
        if self.raises:
            raise TimeoutError("boom")
        if self.fail:
            return None
        r = self.queue.pop(0) if self.queue else self.ratio
        return {"out_lamports": int(amount * 25 * r), "liq_usd": self.liq}


def cand(mint="M1", arm="H"):
    return {"mint": mint, "pool": "P", "symbol": "S", "arm": arm, "liq_usd": 50_000.0,
            "quote_in_lamports": 25_000_000, "quote_out_lamports": 1_000_000, "quote_ts": T0}


@pytest.fixture
def env(tmp_path):
    c, q = Clock(), Quoter()
    c.t = T0
    mgr = P.PositionManager(tmp_path / "state.json", tmp_path / "log.jsonl", q, now_fn=c)
    return mgr, c, q, tmp_path


def rows(tp, kind=None):
    r = [json.loads(x) for x in (tp / "log.jsonl").read_text().splitlines()]
    return [x for x in r if kind is None or x["kind"] == kind]


def adv(c, s=30):
    c.t += s


def test_stop_fills_at_requote(env):
    m, c, q, tp = env
    assert m.open(cand())[1] == "ok"
    q.queue = [0.84, 0.80]  # trigger quote, then the fresh fill quote (worse)
    adv(c)
    ex = m.tick()
    assert ex[0]["reason"] == "stop" and not m.open_positions()
    assert ex[0]["trigger_out_lamports"] == int(25_000_000 * 0.84) and ex[0]["fill_out_lamports"] == 20_000_000
    assert ex[0]["fill_basis"] == "requote"
    assert ex[0]["net_lamports"] == 20_000_000 - 25_000_000 - ENTRY_FEES - TX
    assert rows(tp, "open")[0]["ata_closed_assumed"] is False and rows(tp, "open")[0]["quote_age_s"] == 0.0


def test_fill_fallback_one_percent(env):
    m, c, q, _ = env
    m.open(cand())
    seq = iter([{"out_lamports": 20_000_000, "liq_usd": 50_000.0}, None])
    m.quote_fn = lambda mint, amt: next(seq)
    adv(c)
    ex = m.tick()
    assert ex[0]["fill_basis"] == "trigger_less_1pct" and ex[0]["fill_out_lamports"] == int(20_000_000 * 0.99)


def test_no_exit_inside_band(env):
    m, c, q, _ = env
    m.open(cand())
    q.ratio = 0.9
    adv(c)
    assert m.tick() == [] and len(m.open_positions()) == 1


def test_take_half_then_trail(env):
    m, c, q, tp = env
    m.open(cand())
    q.ratio = 1.4
    adv(c)
    assert m.tick() == []
    pos = m.open_positions()[0]
    assert pos["taken"] and pos["tokens_left"] == 500_000
    q.ratio = 2.0
    adv(c)
    assert m.tick() == []
    q.queue = [1.6, 1.6]
    adv(c)
    ex = m.tick()
    assert ex[0]["reason"] == "trail"
    assert ex[0]["proceeds_lamports"] == int(500_000 * 25 * 1.4) + int(500_000 * 25 * 1.6)
    leg = rows(tp, "leg")[0]
    assert leg["leg"] == "take_half" and leg["fill_basis"] == "requote" and leg["trigger_ratio"] == 1.4


def test_take_fill_below_trigger_logged(env):
    m, c, q, tp = env
    m.open(cand())
    q.queue = [1.45, 1.38]
    adv(c)
    m.tick()
    leg = rows(tp, "leg")[0]
    assert leg["trigger_ratio"] > leg["fill_ratio"]


def test_no_stop_after_take_only_trail(env):
    m, c, q, _ = env
    m.open(cand())
    q.ratio = 1.4
    adv(c)
    m.tick()
    q.ratio = 1.2
    adv(c)
    assert m.tick() == []


def test_time_stop(env):
    m, c, q, _ = env
    m.open(cand())
    q.ratio = 1.05
    c.t += P.TIME_STOP_S
    assert m.tick()[0]["reason"] == "time_stop"


def test_liq_collapse(env):
    m, c, q, _ = env
    m.open(cand())
    q.liq = 30_000.0
    adv(c)
    assert m.tick()[0]["reason"] == "liq_collapse"


def test_liq_unknown_logged_and_exit_after_3(env):
    m, c, q, tp = env
    m.open(cand())
    q.liq = None
    for i in range(2):
        adv(c)
        assert m.tick() == []
    adv(c)
    assert m.tick()[0]["reason"] == "liq_unknown_exit"
    assert len(rows(tp, "liq_unknown")) == 3


def test_liq_unknown_streak_resets(env):
    m, c, q, tp = env
    m.open(cand())
    for liq in (None, None, 50_000.0, None, None):
        q.liq = liq
        adv(c)
        assert m.tick() == []


def test_entry_liq_required(env):
    m, *_ = env
    c1 = cand()
    c1["liq_usd"] = None
    assert m.open(c1)[1] == "no_entry_liq"


def test_quote_miss_then_writeoff(env):
    m, c, q, tp = env
    m.open(cand())
    q.fail = True
    adv(c)
    assert m.tick() == [] and m.open_positions()[0]["misses"] == 1
    assert rows(tp)[-1]["kind"] == "price_miss"
    c.t += P.NO_QUOTE_WRITEOFF_S
    ex = m.tick()
    assert ex[0]["reason"] == "no_quote_writeoff" and ex[0]["proceeds_lamports"] == 0
    assert ex[0]["net_lamports"] == -25_000_000 - ENTRY_FEES - TX and not m.open_positions()


def test_good_quote_resets_miss_clock(env):
    m, c, q, _ = env
    m.open(cand())
    q.fail = True
    adv(c, 400)
    m.tick()
    q.fail = False
    adv(c, 30)
    m.tick()
    q.fail = True
    adv(c, 400)
    assert m.tick() == []  # 400 s since the new first miss < 600


def test_quote_exception_is_a_miss_not_a_crash(env):
    m, c, q, tp = env
    m.open(cand("A"))
    m.open(cand("B"))
    q.raises = True
    adv(c)
    assert m.tick() == []
    assert [r["error_class"] for r in rows(tp, "price_miss")] == ["TimeoutError", "TimeoutError"]
    q.raises = False
    m.run(iterations=2, sleep=lambda s: adv(c, s))  # loop alive


def test_max_3_per_arm_dupes_refusals_logged(env):
    m, _, _, tp = env
    for i in range(3):
        assert m.open(cand(f"M{i}"))[1] == "ok"
    assert m.open(cand("M9"))[1] == "arm_full"
    assert m.open(cand("M9"), arm="T")[1] == "ok"
    assert m.open(cand("M9"), arm="T")[1] == "already_open"
    assert m.open(cand("M10"), arm="X")[1] == "bad_arm"
    ref = rows(tp, "refused")
    assert [(r["arm"], r["reason"], r["mint"]) for r in ref] == [("H", "arm_full", "M9"), ("T", "already_open", "M9"),
                                                                 ("X", "bad_arm", "M10")]


def test_refuses_wrong_stake_bad_and_stale_quote(env):
    m, c, *_ = env
    c1 = cand()
    c1["quote_in_lamports"] = 50_000_000
    assert m.open(c1)[1] == "quote_not_stake_size"
    c2 = cand()
    c2["quote_out_lamports"] = None
    assert m.open(c2)[1] == "bad_quote"
    c3 = cand()
    del c3["quote_ts"]
    assert m.open(c3)[1] == "stale_quote"
    c.t += 6
    assert m.open(cand())[1] == "stale_quote"


def test_paired_open_all_each_arm_own_entry_quote(tmp_path):
    c = Clock()
    calls = []

    def buy(mint, lamports, arm):
        calls.append(arm)
        return {"in_lamports": lamports, "out_lamports": 1_000_000 + 1000 * len(calls), "liq_usd": 40_000.0}

    m = P.PositionManager(tmp_path / "s.json", tmp_path / "l.jsonl", Quoter(), buy_quote_fn=buy, now_fn=c)
    res = m.open_all({"mint": "M", "arms": ["T", "H", "R"]})
    assert calls == ["T", "H", "R"] and all(v[1] == "ok" for v in res.values())
    assert sorted(p["tokens"] for p in m.open_positions()) == [1_001_000, 1_002_000, 1_003_000]
    assert {p["arm"] for p in m.open_positions()} == {"T", "H", "R"}
    assert [v[1] for v in m.open_all({"mint": "N", "arm": "T"}).values()] == ["ok"]


def test_buy_quote_failure_refused(tmp_path):
    def buy(mint, lamports, arm):
        raise TimeoutError

    m = P.PositionManager(tmp_path / "s.json", tmp_path / "l.jsonl", Quoter(), buy_quote_fn=buy, now_fn=Clock())
    assert m.open({"mint": "M", "arm": "T"})[1] == "no_entry_quote"


def test_restart_resumes(env):
    m, c, q, tp = env
    m.open(cand("A"))
    q.ratio = 1.4
    adv(c)
    m.tick()
    m2 = P.PositionManager(tp / "state.json", tp / "log.jsonl", q, now_fn=c)
    assert m2.open_positions()[0]["taken"] and m2.open_positions()[0]["tokens_left"] == 500_000
    c3 = cand("B")
    c3["quote_ts"] = c.t
    m2.open(c3)
    assert m2.state["seq"] == 2
    q.ratio = 0.5
    adv(c)
    assert {e["reason"] for e in m2.tick()} == {"trail", "stop"}
    assert len(m2.state["closed"]) == 2


def test_atomic_write_and_corrupt_state_refused(env):
    m, c, q, tp = env
    m.open(cand())
    assert not list(tp.glob("*.tmp"))
    (tp / "state.json").write_text('{"x": 1}')
    with pytest.raises(ValueError):
        P.PositionManager(tp / "state.json", tp / "log.jsonl", q, now_fn=c)


def test_run_polls_n_times(env):
    m, c, q, _ = env
    m.open(cand())
    slept = []
    m.run(iterations=3, sleep=lambda s: (slept.append(s), adv(c, s)))
    assert slept == [30, 30]


def test_price_rows_carry_proceeds_and_fees(env):
    m, c, q, tp = env
    m.open(cand())
    q.ratio = 1.4
    adv(c)
    m.tick()
    q.fail = True
    adv(c)
    m.tick()
    pr = [r for r in rows(tp) if r["kind"] in ("price", "price_miss")]
    assert all("proceeds_lamports" in r and "fees_lamports" in r for r in pr)
    assert pr[-1]["kind"] == "price_miss" and pr[-1]["proceeds_lamports"] > 0 and pr[-1]["fees_lamports"] == ENTRY_FEES + TX
