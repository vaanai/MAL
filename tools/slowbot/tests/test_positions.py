import json

import pytest

from tools.slowbot import positions as P

T0 = 1_800_000_000.0


class Clock:
    t = T0

    def __call__(self):
        return self.t


class Quoter:
    """Sell-quote stub: ratio r of entry unit value; entry unit = 25_000_000/1_000_000 = 25 lamports per token unit."""
    def __init__(self):
        self.ratio, self.liq, self.fail = 1.0, 50_000.0, False

    def __call__(self, mint, amount):
        if self.fail:
            return None
        return {"out_lamports": int(amount * 25 * self.ratio), "liq_usd": self.liq}


def cand(mint="M1", arm="H"):
    return {"mint": mint, "pool": "P", "symbol": "S", "arm": arm, "liq_usd": 50_000.0,
            "quote_in_lamports": 25_000_000, "quote_out_lamports": 1_000_000}


@pytest.fixture
def env(tmp_path):
    c, q = Clock(), Quoter()
    c.t = T0
    mgr = P.PositionManager(tmp_path / "state.json", tmp_path / "log.jsonl", q, now_fn=c)
    return mgr, c, q, tmp_path


def rows(tmp_path):
    return [json.loads(x) for x in (tmp_path / "log.jsonl").read_text().splitlines()]


def adv(c, s=30):
    c.t += s


def test_stop(env):
    m, c, q, tp = env
    assert m.open(cand())[1] == "ok"
    q.ratio = 0.84
    adv(c)
    ex = m.tick()
    assert ex[0]["reason"] == "stop" and not m.open_positions()
    assert ex[0]["net_lamports"] == int(25_000_000 * 0.84) - 25_000_000 - 2 * P.FEE_LAMPORTS_PER_TX
    assert any(r["kind"] == "price" for r in rows(tp))


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
    q.ratio = 1.6  # exactly 0.8 * 2.0: trail fires
    adv(c)
    ex = m.tick()
    assert ex[0]["reason"] == "trail"
    # half at 1.4, half at 1.6 of entry value
    assert ex[0]["proceeds_lamports"] == int(500_000 * 25 * 1.4) + int(500_000 * 25 * 1.6)
    assert ex[0]["took_half"] is True
    legs = [r for r in rows(tp) if r["kind"] == "leg"]
    assert len(legs) == 1 and legs[0]["leg"] == "take_half"


def test_no_stop_after_take_only_trail(env):
    m, c, q, _ = env
    m.open(cand())
    q.ratio = 1.4
    adv(c)
    m.tick()
    q.ratio = 1.2  # below 0.8 * 1.4? 1.12 -> no
    adv(c)
    assert m.tick() == []


def test_time_stop(env):
    m, c, q, _ = env
    m.open(cand())
    q.ratio = 1.05
    c.t += P.TIME_STOP_S
    ex = m.tick()
    assert ex[0]["reason"] == "time_stop"


def test_liq_collapse(env):
    m, c, q, _ = env
    m.open(cand())
    q.liq = 30_000.0  # -40%
    adv(c)
    assert m.tick()[0]["reason"] == "liq_collapse"


def test_quote_miss_keeps_position(env):
    m, c, q, tp = env
    m.open(cand())
    q.fail = True
    adv(c)
    assert m.tick() == [] and m.open_positions()[0]["misses"] == 1
    assert rows(tp)[-1]["kind"] == "price_miss"


def test_max_3_per_arm_and_dupes(env):
    m, *_ = env
    for i in range(3):
        assert m.open(cand(f"M{i}"))[1] == "ok"
    assert m.open(cand("M9"))[1] == "arm_full"
    assert m.open(cand("M9"), arm="T")[1] == "ok"
    assert m.open(cand("M9"), arm="T")[1] == "already_open"
    assert m.open(cand("M10"), arm="X")[1] == "bad_arm"


def test_refuses_wrong_stake_and_bad_quote(env):
    m, *_ = env
    c1 = cand()
    c1["quote_in_lamports"] = 50_000_000
    assert m.open(c1)[1] == "quote_not_stake_size"
    c2 = cand()
    c2["quote_out_lamports"] = None
    assert m.open(c2)[1] == "bad_quote"


def test_restart_resumes(env):
    m, c, q, tp = env
    m.open(cand("A"))
    q.ratio = 1.4
    adv(c)
    m.tick()
    m2 = P.PositionManager(tp / "state.json", tp / "log.jsonl", q, now_fn=c)
    assert m2.open_positions()[0]["taken"] and m2.open_positions()[0]["tokens_left"] == 500_000
    m2.open(cand("B"))
    assert m2.state["seq"] == 2  # ids do not repeat after restart
    q.ratio = 0.5
    adv(c)
    assert {e["reason"] for e in m2.tick()} == {"trail", "stop"}
    assert len(m2.state["closed"]) == 2


def test_atomic_write_leaves_no_tmp_and_corrupt_state_refused(env):
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
    m.run(iterations=3, sleep=lambda s: (slept.append(s), setattr(c, "t", c.t + s)))
    assert slept == [30, 30]
