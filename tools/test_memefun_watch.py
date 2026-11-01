"""memefun_watch: decode of captured public-RPC logs (fixtures/memefun_watch/notices.jsonl, captured 2026-10-26) and synthetic edge cases. No network."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace as N

from tools import memefun_watch as mw

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "memefun_watch" / "notices.jsonl"
IDL = Path(__file__).resolve().parent.parent / "docs" / "research" / "p45-memefun-watch" / "idl.json"


def cases() -> dict[str, dict]:
    return {c["case"]: c for c in (json.loads(l) for l in FIX.read_text().splitlines())}


def note(c: dict, t: int = 1_793_000_000_000, failed: bool | None = None) -> N:
    return N(slot=c["slot"], signature=c["signature"], failed=(c["err"] is not None) if failed is None else failed, logs=c["logs"], t_recv_ms=t)


def test_discriminators_match_anchor_hash_and_idl():
    idl = json.loads(IDL.read_text())
    ev = {e["name"]: bytes(e["discriminator"]) for e in idl["events"]}
    for name, d in (("TradeEvent", mw.TRADE_DISC), ("CreateEvent", mw.CREATE_DISC), ("ClaimEvent", mw.CLAIM_DISC)):
        assert hashlib.sha256(f"event:{name}".encode()).digest()[:8] == d == ev[name]
    assert idl["address"] == mw.PROGRAM


def test_idl_instruction_names():
    idl = json.loads(IDL.read_text())
    assert {"buy", "sell", "swap", "create_token"} <= {i["name"] for i in idl["instructions"]}


def test_buy_fixture_matches_balances():
    c = cases()["buy"]
    rows = mw.rows_for_notice(note(c))
    assert len(rows) == 1
    r = rows[0]
    assert r["type"] == "trade" and r["side"] == "buy" and r["venue"] == "memefun" and r["event_index"] == 0
    assert r["ix"][0] == "Buy" and "failed" not in r and "via_cpi" not in r
    assert r["ev_slot"] == c["slot"] and abs(r["ts"] - c["blockTime"]) <= 1
    assert r["sol_amount"] > 0 and r["token_amount"] > 0
    assert r["real_token_reserves"] == r["virtual_token_reserves"] or r["real_token_reserves"] > 0
    json.dumps(r, allow_nan=False)


def test_sell_fixture():
    r = mw.rows_for_notice(note(cases()["sell"]))[0]
    assert r["type"] == "trade" and r["side"] == "sell" and r["ix"][0] == "Sell"


def test_buy_with_new_ata_still_one_trade_row():
    rows = mw.rows_for_notice(note(cases()["buy_newata"]))
    assert [r["type"] for r in rows] == ["trade"] and rows[0]["side"] == "buy"


def test_create_fixture():
    c = cases()["create"]
    rows = mw.rows_for_notice(note(c))
    assert [r["type"] for r in rows] == ["create", "trade"]  # create_token, then the creator's own buy in a second instruction
    cr, tr = rows
    assert cr["name"] == "YiLong Ma" and cr["symbol"] == "YILONGMA" and cr["uri"].startswith("https://ipfs.io/ipfs/")
    assert cr["mint"].endswith("meme") and cr["mint"] == tr["mint"]
    assert cr["real_token_reserves"] == 10**15 and cr["virtual_token_reserves"] == 10**15
    assert cr["ev_slot"] == c["slot"]
    assert cr["ix"][0] == "CreateToken" and "Buy" in cr["ix"]
    assert tr["trader"] == cr["creator"]


def test_failed_tx_without_event_is_ix_row():
    c = dict(cases()["buy"])
    logs = [l for l in c["logs"] if not l.startswith("Program data: ")]
    rows = mw.rows_for_notice(N(slot=1, signature="sigF", failed=True, logs=logs, t_recv_ms=5))
    assert len(rows) == 1 and rows[0]["type"] == "ix" and rows[0]["failed"] is True and rows[0]["ix"] == ["Buy"]


def test_program_only_in_account_list_gives_no_rows():
    logs = ["Program 11111111111111111111111111111111 invoke [1]", "Program 11111111111111111111111111111111 success"]
    assert mw.rows_for_notice(N(slot=1, signature="s", failed=False, logs=logs, t_recv_ms=1)) == []


def test_cpi_and_truncated_flags():
    logs = [f"Program Other111 invoke [1]", f"Program {mw.PROGRAM} invoke [2]", "Program log: Instruction: Swap", "Log truncated"]
    r = mw.rows_for_notice(N(slot=1, signature="s", failed=False, logs=logs, t_recv_ms=1))[0]
    assert r["type"] == "ix" and r["via_cpi"] is True and r["logs_truncated"] is True and r["ix"] == ["Swap"]


def test_unknown_disc_and_bad_lengths_are_undecoded_never_raise():
    cases_ = [
        base64.b64encode(bytes(8) + b"xyz").decode(),
        base64.b64encode(mw.TRADE_DISC + bytes(10)).decode(),
        base64.b64encode(mw.CREATE_DISC + bytes(40)).decode(),
        base64.b64encode(mw.CLAIM_DISC + bytes(3)).decode(),
        "!!!not-base64",
        "",
    ]
    for b in cases_:
        d = mw.decode_event(b)
        assert d["type"] == "event_undecoded", b


def test_trade_bool_must_be_zero_or_one():
    raw = bytearray(mw.TRADE_DISC + bytes(mw.TRADE_LEN - 8))
    raw[56] = 7
    assert mw.decode_event(base64.b64encode(bytes(raw)).decode())["reason"] == "trade_bool"


def test_create_string_overrun_is_undecoded():
    raw = mw.CREATE_DISC + bytes(96) + struct.pack("<I", 10_000) + bytes(60)
    assert mw.decode_event(base64.b64encode(raw).decode())["reason"] == "create_str_overrun"


def test_claim_event_roundtrip():
    raw = mw.CLAIM_DISC + bytes([1]) * 32 + bytes([2]) * 32 + struct.pack("<Q", 55) + struct.pack("<q", 1_793_000_000)
    d = mw.decode_event(base64.b64encode(raw).decode())
    assert d["type"] == "claim" and d["amount"] == 55 and d["ts"] == 1_793_000_000


def test_two_events_get_event_index():
    c = cases()["buy"]
    data = [l for l in c["logs"] if l.startswith("Program data: ")]
    logs = list(c["logs"])
    i = max(k for k, l in enumerate(logs) if l.startswith("Program data: "))
    logs.insert(i + 1, data[0])
    rows = mw.rows_for_notice(N(slot=1, signature="s", failed=False, logs=logs, t_recv_ms=1))
    assert [r["event_index"] for r in rows] == [0, 1]


class FakeWriter:
    def __init__(self):
        self.rows = []

    def write_all(self, body):
        self.rows.append(body)


def test_monitor_silence_gap_once_and_reconnects_only_counted():
    w = FakeWriter()
    m = mw.Monitor(w, silence_s=10, now_ms=0)
    m.tick(5_000, {0: 3, 1: 4})
    assert w.rows == [] and m.reconnects == 7  # reconnects never write gap rows
    m.tick(11_000, {0: 4, 1: 4})
    assert [r["reason"] for r in w.rows] == ["silence"] and m.gaps == 1
    m.tick(12_000, {0: 4, 1: 4})
    assert len(w.rows) == 1  # one gap row per silence
    m.saw(13_000)
    assert w.rows[-1]["type"] == "gap_end" and w.rows[-1]["start_ms"] == 0


def test_lock_is_exclusive(tmp_path):
    a = mw.acquire_lock(tmp_path)
    assert a is not None
    assert mw.acquire_lock(tmp_path) is None
    a.close()
    assert mw.acquire_lock(tmp_path) is not None


def test_keyed_ws_urls_are_refused():
    assert mw.bad_ws(["wss://x.helius-rpc.com/?api-key=1"])
    assert mw.bad_ws(["wss://a/b?k=1"])
    assert not mw.bad_ws(mw.DEFAULT_WS)


def test_writer_writes_hourly_jsonl(tmp_path):
    from tools.venue_tape import TapeWriter
    rows = mw.rows_for_notice(note(cases()["buy"], t=1_793_034_487_000))
    t = 1_793_034_487_000
    w2 = TapeWriter(tmp_path, [mw.VENUE], free_fn=lambda p: 0.9, clock=lambda: t / 1000 + 5)
    assert w2.write(mw.VENUE, rows[0])
    w2.close()
    files = list((tmp_path / mw.VENUE).glob("tape-*.jsonl"))
    assert len(files) == 1 and files[0].name == "tape-2026-10-26T17.jsonl"
    assert json.loads(files[0].read_text().splitlines()[0])["type"] == "trade"
