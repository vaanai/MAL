"""Tests for tools/dbc_fwd_feed.py. Real-tx fixture (85 DBC txs, 2026-10-14T12, getTransaction json) and an EvtSwap3 transaction rebuilt byte-for-byte from a real decoded
Swap3 row (tools/data/dbc_swap3_fixture.json). Fake socket only: no network, no key."""
from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import struct
from pathlib import Path

from tools import dbc_decode as DD
from tools import dbc_fwd_feed as FF

HERE = Path(__file__).parent
FIX = HERE / "fixtures" / "dbc_txs_2026-10-14T12.json.gz"
FAKE_KEY = "kkkkkkkk-1111-2222-3333-kkkkkkkkkkkk"


def txs():
    return json.load(gzip.open(FIX))


def test_subscribe_request_is_program_filtered_processed_full():
    r = FF.subscribe_request()
    flt, opt = r["params"]
    assert r["method"] == "transactionSubscribe" and flt["accountInclude"] == [DD.PROGRAM] and flt["failed"] is False and flt["vote"] is False
    assert opt["commitment"] == "processed" and opt["transactionDetails"] == "full" and opt["encoding"] == "json"


def test_notification_rows_equal_backfill_decode_except_receive_time():
    n = 0
    for tx in txs():
        t = int(tx["blockTime"]) * 1000 + 700
        live = FF.rows_from_notification(FF.notification_of(tx), t)
        ref = DD.decode_tx(tx)
        assert len(live) == len(ref)
        for a, b in zip(live, ref):
            assert a.pop("t_recv_ms") == t and a.pop("bt_src") == "recv"
            b.pop("t_recv_ms")
            assert a == b
            n += a["type"] == "trade"
    assert n == 77


def test_non_notifications_are_ignored():
    assert FF.rows_from_notification({"jsonrpc": "2.0", "result": 7, "id": 1}, 0) == []
    assert FF.tx_from_notification({"method": "transactionNotification", "params": {"result": {"slot": "x"}}}, 0) is None


# ------------------------------------------------------------------------------------------------------------------------------ Swap3 (current venue) through the live path
_SWAP3_DISC = bytes([187, 119, 248, 13, 175, 255, 64, 63])
_SWAP2_IX = bytes([65, 75, 63, 76, 235, 91, 91, 136])


def swap3_tx(r: dict, slot: int, bt: int) -> dict:
    """A transaction whose DBC Swap2 instruction emits EvtSwap3 with the values of real decoded row `r` (layout from the IDL as in tools/dbc_decode._SWAP3)."""
    keys = ["Payer1111111111111111111111111111111111111", "PoolAuth11111111111111111111111111111111111", r["config"], r["pool"], "InAcct1111111111111111111111111111111111111",
            "OutAcct111111111111111111111111111111111111", "BaseVau1t11111111111111111111111111111111111", "QuoteVau1t1111111111111111111111111111111111",
            "BaseMint1111111111111111111111111111111111", "So11111111111111111111111111111111111111112", DD.PROGRAM]
    side = 1 if r["side"] == "buy" else 0
    p = DD.b58d(r["pool"]).rjust(32, b"\0") + DD.b58d(r["config"]).rjust(32, b"\0")
    p += bytes([side, r["swap_mode"], 1 if r["has_referral"] else 0, 1 if r["fee_on_base_token"] else 0])
    p += struct.pack("<QQQQQQQ", r["amount_in"], r["amount_in_excl_fee"], r["amount_out_incl_transfer_fee"], r["amount_out"], r["trading_fee"], r["protocol_fee"], r["referral_fee"])
    p += int(r["next_sqrt_price"]).to_bytes(16, "little") + struct.pack("<QQ", r["quote_reserve_post"], bt)
    ev = DD.EVENT_IX_TAG + _SWAP3_DISC + p
    ix = {"programIdIndex": 10, "accounts": [1, 2, 3, 4, 5, 6, 7, 8, 9, 0], "data": DD.b58(_SWAP2_IX + bytes(17))}
    inner = {"index": 0, "instructions": [{"programIdIndex": 10, "accounts": [1], "data": DD.b58(ev), "stackHeight": 2}]}
    bal = lambda idx, amt: {"accountIndex": idx, "uiTokenAmount": {"amount": str(amt)}}  # noqa: E731
    meta = {"err": None, "innerInstructions": [inner], "preTokenBalances": [bal(6, r["base_vault_pre"]), bal(7, r["quote_vault_pre"])],
            "postTokenBalances": [bal(6, r["base_vault_post"]), bal(7, r["quote_vault_post"])], "loadedAddresses": {"writable": [], "readonly": []}}
    return {"slot": slot, "blockTime": bt, "transaction": {"signatures": [r["signature"]], "message": {"accountKeys": keys, "instructions": [ix]}}, "meta": meta, "version": 0}


def test_swap3_rows_decode_through_the_live_path():
    fx = json.load(open(HERE / "data" / "dbc_swap3_fixture.json"))["rows"]
    picked = [r for r in fx if r["event"] == "EvtSwap3"][:40]
    assert any(r["side"] == "buy" for r in picked) and any(r["side"] == "sell" for r in picked)
    for r in picked:
        msg = FF.notification_of(swap3_tx(r, r["slot"], 1_760_000_000))
        rows = FF.rows_from_notification(msg, 1_760_000_000_400)
        assert len(rows) == 1, rows
        d = rows[0]
        assert d["type"] == "trade" and d["event"] == "EvtSwap3" and d["ix"] == "Swap2" and d["quote_mint"] == "So11111111111111111111111111111111111111112"
        for k in ("pool", "config", "side", "swap_mode", "fee_on_base_token", "next_sqrt_price", "trading_fee", "protocol_fee", "referral_fee", "amount_in", "amount_in_excl_fee",
                  "amount_out", "amount_out_incl_transfer_fee", "quote_reserve_post", "base_vault_pre", "base_vault_post", "quote_vault_pre", "quote_vault_post", "signature", "slot"):
            assert d[k] == r[k], (k, d[k], r[k])
        assert "migration_threshold" not in d  # Swap3 rows take the threshold from the config map (dbc_price.CFG_THR)


# ------------------------------------------------------------------------------------------------------------------------------ socket loop (fake)
class FakeConn:
    def __init__(self, script, stop, log):
        self.script, self.stop, self.log = list(script), stop, log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, s):
        self.log.append(json.loads(s))

    async def recv(self):
        if self.script:
            return self.script.pop(0)
        await asyncio.sleep(3600)


def fake_connect(scripts, stop, sent):
    it = iter(scripts)

    @contextlib.asynccontextmanager
    async def connect(url):
        try:
            sc = next(it)
        except StopIteration:
            stop.set()
            raise OSError(f"no more scripts for {url}")
        if sc == "fail":
            raise OSError(f"handshake failed for {url}")
        yield FakeConn(sc, stop, sent)
    return connect


class Clock:
    def __init__(self):
        self.t = 1_760_000_000_000

    def __call__(self):
        self.t += 1000
        return self.t


def _run(scripts, cap=None, silence_s=90.0):
    stop = asyncio.Event()
    out, sent = [], []
    clock = Clock()
    meter = FF.Meter(cap)

    async def nosleep(_s):
        clock.t += 200_000  # backoff sleeps move the receive clock: a reconnect is also a silence
        return None
    rc = asyncio.run(FF.run_feed(FF.ws_url(FAKE_KEY), out.append, stop, connect=fake_connect(scripts, stop, sent), meter=meter, gaps=FF.Gaps(silence_s),
                                 clock_ms=clock, sleep=nosleep, recv_timeout_s=0.01))
    return rc, out, sent, meter


def test_feed_rows_reconnect_gap_and_subscription():
    frames = [json.dumps(FF.notification_of(t)) for t in sorted(txs(), key=lambda t: t["slot"])]
    rc, out, sent, meter = _run([frames[:40], "fail", frames[40:]])
    assert rc == 0
    assert all(s["method"] == "transactionSubscribe" for s in sent) and len(sent) == 2
    kinds = [r["type"] for r in out]
    assert kinds.count("trade") == 77
    g = [r for r in out if r["type"] in ("gap", "gap_end")]
    assert [x["type"] for x in g][:2] == ["gap", "gap_end"] and g[0]["reason"] in ("silence", "socket_reconnect")
    i_gap, i_end = kinds.index("gap"), kinds.index("gap_end")
    assert all(r.get("bt_src") == "recv" for r in out[:i_gap] if r["type"] == "trade") and i_end > i_gap
    assert meter.msgs == 85 and meter.connections == 4 and meter.bytes > 400_000


def test_credit_cap_stops_with_exit_5():
    frames = [json.dumps(FF.notification_of(t)) for t in txs()]
    rc, out, _sent, meter = _run([frames], cap=5.0)
    assert rc == FF.EXIT_CAP == 5
    assert out[-1]["type"] == "stop" and out[-1]["reason"] == "credit_cap" and meter.credits() > 5.0
    assert sum(1 for r in out if r["type"] == "trade") < 77


def test_key_never_in_logs_or_rows():
    rec: list[str] = []

    class H(logging.Handler):
        def emit(self, record):
            rec.append(record.getMessage())
    h = H()
    FF.log.addHandler(h)
    FF.log.setLevel(logging.DEBUG)
    try:
        rc, out, _s, _m = _run(["fail", "fail", [json.dumps(FF.notification_of(txs()[0]))]])
    finally:
        FF.log.removeHandler(h)
    assert rc == 0 and rec and any("api-key=***" in m for m in rec)
    assert not any(FAKE_KEY in m for m in rec)
    assert FAKE_KEY not in json.dumps(out)
    assert FF.redact("wss://x/?api-key=" + FAKE_KEY + "&a=1") == "wss://x/?api-key=***&a=1"


def test_ws_url_refuses_unsafe_key():
    for bad in ("", "a b", "a&b", "a/b", "a?b"):
        try:
            FF.ws_url(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_fixture_mode_end_to_end(tmp=None):
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        res = FF.run_fixture(str(FIX), td)
        assert res["txs"] == 85 and res["engine"]["rows"] == 77 and res["engine"]["completes"] == 2 and res["decisions"] == 0
        assert res["declog_head"][1] == 0  # the start row only
    assert FF.main(["--live", "--out", "/tmp/never-x"]) == 2


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
