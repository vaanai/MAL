import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pytest

import kalshi_live_net as kn


class Clock:
    def __init__(self):
        self.t = 100.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def bucket(rate=2.0, burst=2.0):
    c = Clock()
    return kn.TokenBucket(rate, burst, clock=c, sleep=c.sleep), c


def test_burst_then_paced():
    b, c = bucket()
    assert [b.acquire() for _ in range(2)] == [0.0, 0.0]   # burst of 2 is free
    assert b.acquire() == pytest.approx(0.5)               # 2 tokens/s -> 0.5 s per token
    assert b.acquire() == pytest.approx(0.5)
    assert c.slept == [pytest.approx(0.5), pytest.approx(0.5)]


def test_refills_while_idle_but_not_above_burst():
    b, c = bucket()
    b.acquire(); b.acquire()
    c.t += 100
    assert b.acquire() == 0.0 and b.acquire() == 0.0
    assert b.acquire() > 0  # only burst=2 was stored


def test_cost_and_validation():
    b, _ = bucket(rate=10, burst=10)
    assert b.acquire(10) == 0.0
    assert b.acquire(5) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        b.acquire(11)
    for bad in [dict(rate=0), dict(rate=-1), dict(rate=1, burst=0.5)]:
        with pytest.raises(ValueError):
            kn.TokenBucket(**bad)


def test_backoff_exponential_capped_with_jitter_after_cap():
    no = lambda: 0.0
    assert [kn.backoff_delay(a, rng=no) for a in range(4)] == [1, 2, 4, 8]
    assert kn.backoff_delay(10, cap=60, rng=no) == 60
    assert kn.backoff_delay(10, cap=60, jitter=0.25, rng=lambda: 1.0) == 75  # jitter after the cap
    assert kn.backoff_delay(0, rng=no, retry_after=30) == 30                # Retry-After is a floor
    assert kn.backoff_delay(5, rng=no, retry_after=1) == 32


def test_parse_retry_after():
    assert kn.parse_retry_after({"Retry-After": "7"}) == 7.0
    assert kn.parse_retry_after({"retry-after": " 2.5 "}) == 2.5
    assert kn.parse_retry_after({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert kn.parse_retry_after({"Retry-After": "-3"}) is None
    assert kn.parse_retry_after({}) is None and kn.parse_retry_after(None) is None
