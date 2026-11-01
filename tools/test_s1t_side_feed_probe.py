"""Tests of tools/s1t_side_feed_probe.py: the refusals and the summary arithmetic with a fake message source. No network, no websockets import."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tools import s1t_side_feed_probe as pr

ACK = json.dumps({"jsonrpc": "2.0", "result": 123, "id": 1})


def note(n: int, marker: str = "CONTENT_MARKER_QQ") -> str:
    body = '{"jsonrpc":"2.0","method":"logsNotification","params":{"x":"' + marker
    return body + "a" * (n - len(body) - 4) + '"}}'


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.now += s


class FakeClosed(Exception):
    def __init__(self, code=None):
        if code is not None:
            self.rcvd = SimpleNamespace(code=code)


class FakeWS:
    """A script of (dt, payload | Exception) events; the clock advances by dt on each recv. An exhausted script runs the clock to the deadline."""

    def __init__(self, clock: Clock, script: list, deadline: float) -> None:
        self.clock, self.script, self.deadline, self.sent = clock, list(script), deadline, []

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def recv(self):
        if not self.script:
            self.clock.now = self.deadline
            raise asyncio.TimeoutError
        dt, item = self.script.pop(0)
        self.clock.now += dt
        if isinstance(item, Exception):
            raise item
        return item


class Conn:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *a):
        return False


def run(scripts: list, seconds: float = 600.0):
    clock = Clock()
    deadline = clock.now + seconds
    wss = []

    def connect():
        if not scripts:
            raise OSError("no more")
        s = scripts.pop(0)
        if isinstance(s, Exception):
            raise s
        ws = FakeWS(clock, s, deadline)
        wss.append(ws)
        return Conn(ws)

    out = asyncio.run(pr.run_probe(connect, seconds, host="rpc.example.org", clock=clock, sleep=clock.sleep))
    return out, wss


def test_summary_arithmetic():
    n = 1000
    out, wss = run([[(0.25, ACK), (10, note(n)), (10, note(n)), (10, note(n))]])
    assert out["connects"] == 1 and out["connect_errors"] == 0 and out["closes"] == {}
    assert out["ack_ms"] == 250
    assert out["messages"] == 3
    assert out["elapsed_s"] == 600.0
    assert out["msgs_per_s"] == round(3 / 600, 3)
    assert out["MB_per_h"] == round((len(ACK) + 3 * n) / 1e6 / (600 / 3600), 3)
    assert out["max_silence_s"] == round(600 - 30.25, 3)  # the tail after the last notification is the longest span
    assert len(wss[0].sent) == 1


def test_subscription_message_is_pump_logs_confirmed():
    out, wss = run([[]], seconds=5)
    sub = json.loads(wss[0].sent[0])
    assert sub["method"] == "logsSubscribe"
    assert sub["params"] == [{"mentions": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"]}, {"commitment": "confirmed"}]


def test_max_silence_is_the_longest_span():
    out, _ = run([[(0.1, ACK), (1, note(300)), (50, note(300)), (2, note(300))]], seconds=60)
    assert out["messages"] == 3 and out["max_silence_s"] == 50.0  # tail 60-53.1 = 6.9 < 50


def test_reconnects_closes_errors_and_first_ack_only():
    err = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Invalid param api-key=AAAABBBBCCCCDDDDEEEEFFFFGGGG " + "z" * 300}})
    out, wss = run([[(0.5, ACK), (1, note(300)), (1, FakeClosed(1011))],  # first connection: ack 500 ms, then a close with code
                    OSError("refused"),  # a failed connect
                    [(3.0, ACK), (1, note(300)), (1, FakeClosed())],  # a second ack is not the first subscribe's; a close without a frame is 1006
                    [(0.1, err)]], seconds=120)
    assert out["connects"] == 3 and out["connect_errors"] == 1
    assert out["closes"] == {"1006": 1, "1011": 1}
    assert out["ack_ms"] == 500
    assert out["messages"] == 2
    assert out["sub_errors"][0]["code"] == -32602
    msg = out["sub_errors"][0]["msg"]
    assert len(msg) <= 120 and "AAAABBBB" not in msg and "api-key=<redacted>" in msg


def test_summary_never_carries_notification_content():
    out, _ = run([[(0.2, ACK), (1, note(500, "CONTENT_MARKER_QQ"))]], seconds=30)
    assert "CONTENT_MARKER_QQ" not in json.dumps(out) and out["messages"] == 1


def test_close_code_variants():
    assert pr.close_code(FakeClosed(1001)) == 1001
    assert pr.close_code(FakeClosed()) == 1006
    e = Exception()
    e.code = 4000
    assert pr.close_code(e) == 4000


def test_redact_cuts_after_redacting():
    s = pr.redact("x " * 30 + "token=abcdef " + "Q" * 40 + " " + "y" * 200)
    assert len(s) <= 120 and "Q" * 24 not in s


@pytest.mark.parametrize("url,why", [
    ("ws://rpc.example.org", "wss"), ("https://rpc.example.org", "wss"), ("rpc.example.org", "wss"), ("", "wss"),
    ("wss://rpc.example.org/?api-key=abc", "query"), ("wss://rpc.example.org/#frag", "query"),
    ("wss://user:pw@rpc.example.org", "userinfo"), ("wss://user@rpc.example.org", "userinfo"),
    ("wss://rpc.example.org/ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", "keylike"), ("wss://rpc.example.org/api-key/x", "keylike"),
    ("wss://api.mainnet-beta.solana.com", "arena feed"), ("wss://API.mainnet-beta.solana.com/", "arena feed"),
    ("wss://foo.solanavibestation.com", "arena feed"), ("wss://SolanaVibeStation.example.org/ws", "arena feed")])
def test_refusals(url, why):
    with pytest.raises(ValueError) as e:
        pr.check_url(url)
    assert "refused" in str(e.value) and why in str(e.value)


@pytest.mark.parametrize("url", ["wss://rpc.example.org", "wss://rpc.example.org/", "wss://fra.rpc.example.org/ws", "wss://api.devnet.solana.com"])
def test_accepts_keyless_wss(url):
    assert pr.check_url(url) == url


def test_main_refuses_before_any_connection(capsys):
    for url in ("ws://rpc.example.org", "wss://api.mainnet-beta.solana.com", "wss://x.solanavibestation.com", "wss://h.example/?k=1"):
        assert pr.main(["--url", url]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "refused" in cap.err
    assert pr.main(["--url", "wss://rpc.example.org", "--seconds", "0"]) == 2
