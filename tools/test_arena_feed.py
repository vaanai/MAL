"""tools/arena_feed.py: decode fixtures, reserve conventions, gap rules, lock, resolver, and the fake-websocket end to end. No network."""

from __future__ import annotations

import asyncio
import base64
import collections
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from observe import trade_source
from observe.fake_ws import FIXTURES, T0, fixture_line, logs_frame
from observe.trade_decode import (
    _program_data_bytes,
    b58encode,
    decode_program_data,
    records_from_logs,
)
from observe.trade_source import RawNotice
from tools import arena_feed as af
from tools.tape_lines import scan_file

WSOL = "So11111111111111111111111111111111111111112"
POOL = b58encode(bytes([7]) * 32)
MINT = b58encode(bytes([9]) * 32)
CURVE = b58encode(bytes([5]) * 32)
USER = b58encode(bytes([3]) * 32)
SIG = "S" * 88
DU = collections.namedtuple("DU", "total used free")


def roomy(_path) -> DU:
    """A disk with 50% free: the real one on this host is under the 25% reserve, and these tests are not about the reserve."""
    return DU(10**12, 5 * 10**11, 5 * 10**11)


def note(slot: int, sig: str, logs: list[str], t: int = T0, failed: bool = False) -> RawNotice:
    return RawNotice(slot=slot, signature=sig, failed=failed, logs=tuple(logs), t_recv_ms=t, commitment="confirmed", feed="public_rpc_logs")


def pd(raw: bytes) -> str:
    return "Program data: " + base64.b64encode(raw).decode()


def create_pool_blob(pool: str = POOL, mint: str = MINT, quote: str = WSOL) -> bytes:
    raw = bytearray(bytes.fromhex("b1310cd2a076a774") + bytes(300))
    from observe.trade_decode import b58decode

    raw[173:205], raw[50:82], raw[82:114] = b58decode(pool), b58decode(mint), b58decode(quote)
    return bytes(raw)


def create_event_blob(mint: str = MINT, creator: str = USER, name: str = "tok") -> bytes:
    from observe.trade_decode import b58decode

    def s(x: bytes) -> bytes:
        return len(x).to_bytes(4, "little") + x

    out = bytearray(bytes.fromhex("1b72a94ddeeb6376") + s(name.encode()) + s(b"SYM") + s(b"https://x"))
    out += b58decode(mint) + b58decode(CURVE) + b58decode(USER) + b58decode(creator)
    out += (1_790_318_000).to_bytes(8, "little", signed=True)
    out += (1_073_000_000_000_000).to_bytes(8, "little") + (30_000_000_000).to_bytes(8, "little") + (793_100_000_000_000).to_bytes(8, "little")
    out += (1_000_000_000_000_000).to_bytes(8, "little")
    out += bytes(32) + bytes([0, 0]) + bytes(32)  # token_program, is_mayhem, is_cashback, native-SOL quote
    return bytes(out)


def complete_event_blob(mint: str = MINT) -> bytes:
    from observe.trade_decode import b58decode

    return bytes.fromhex("5f72619cd42e9808") + b58decode(USER) + b58decode(mint) + b58decode(CURVE) + (1_790_318_100).to_bytes(8, "little", signed=True) + bytes(32)


# ---- decode ---------------------------------------------------------------------------------------------------
def test_bonding_fixture_row_and_extras() -> None:
    rows = af.rows_from_notice(note(100, SIG, [fixture_line("pump_trade_event.b64")]), {})
    assert [r["type"] for r in rows] == ["trade"]
    r = rows[0]
    assert (r["prog"], r["venue"], r["side"], r["slot"], r["t_recv_ms"], r["signature"], r["event_index"]) == ("pumpfun", "pump_bonding", "buy", 100, T0, SIG, 0)
    assert r["ix_name"] == "buy" and r["quote_reserve"] == 50288435297 and r["base_reserve"] == 640107411711759
    for dropped in ("feed", "commitment", "price_sol", "market_cap_sol", "t_recv", "sol", "token"):
        assert dropped not in r
    assert {"protocol_fee", "creator_fee", "creator", "real_quote_reserve", "real_base_reserve"} <= set(r)
    json.dumps(r, allow_nan=False)


def test_pumpswap_fixture_row_has_event_v() -> None:
    rows = af.rows_from_notice(note(101, SIG, [fixture_line("pumpswap_buy_event.b64")]), {})
    assert len(rows) == 1 and rows[0]["prog"] == "pumpswap" and rows[0]["side"] == "buy"
    assert rows[0]["virtual_quote_reserves"] == 17584505289 and rows[0]["mint"] is None and rows[0]["pool"]


def test_tape_field_names_are_trade_decode_keys_plus_prog_and_bonding_extras() -> None:
    legacy = set(records_from_logs([fixture_line("pumpswap_buy_event.b64")], slot=1, signature="x", t_recv_ms=1, commitment="c", feed="f", event_v=True)[0])
    row = af.rows_from_notice(note(1, SIG, [fixture_line("pumpswap_buy_event.b64")]), {})[0]
    assert set(row) <= legacy | {"prog"}


def test_lifecycle_rows_and_mint_resolution_in_one_tx() -> None:
    logs = [pd(create_pool_blob()), pd(create_event_blob()), fixture_line("pumpswap_buy_event.b64"), pd(complete_event_blob())]
    cache: dict = {}
    rows = af.rows_from_notice(note(300, SIG, logs), cache)
    assert [r["type"] for r in rows] == ["pool_create", "create", "trade", "complete"]
    pc, cr, tr, co = rows
    assert (pc["prog"], pc["pool"], pc["mint"], pc["quote_mint"]) == ("pumpswap", POOL, MINT, WSOL)
    assert (cr["prog"], cr["mint"], cr["creator"], cr["name"], cr["symbol"], cr["base_reserve"], cr["quote_reserve"]) == ("pumpfun", MINT, USER, "tok", "SYM", 1_073_000_000_000_000, 30_000_000_000)
    assert (co["prog"], co["mint"], co["bonding_curve"]) == ("pumpfun", MINT, CURVE)
    assert POOL in cache  # the CreatePool of the tx fills the cache for later prints


def test_failed_or_dataless_notice_gives_no_rows() -> None:
    assert af.rows_from_notice(note(1, SIG, [fixture_line("pumpswap_buy_event.b64")], failed=True), {}) == []
    assert af.rows_from_notice(note(1, SIG, ["Program log: hello"]), {}) == []


# ---- reserve conventions (the evidence behind tools/arena_tape_schema.md) -----------------------------------------
def test_pumpswap_reserves_are_pre_trade() -> None:
    """Constant product on the row's reserves (V included) reproduces base_out EXACTLY only if they are PRE-trade; the POST reading does not."""
    raw = base64.b64decode((FIXTURES / "pumpswap_buy_event.b64").read_text().strip())
    d = decode_program_data(raw, event_v=True)
    q, b, v, qin, out = d["quote_reserve"], d["base_reserve"], d["virtual_quote_reserves"], d["pool_quote_amount"], d["token_raw"]
    pre_resid = abs(out - b * qin // (q + v + qin)) / out
    # if the row were POST-trade, the pre-trade state would be (q + v - qin_total, b + out): the same formula on it must then fit
    post_resid = abs(out - (b + out) * qin // ((q + v - qin) + qin)) / out
    assert pre_resid < 1e-7 and post_resid > 1e-3  # measured 1.3e-9 vs 1.2e-2


def test_bonding_reserves_are_post_trade() -> None:
    doc = json.loads((Path(__file__).parent / "fixtures" / "exp003_mainnet_buy_v1.json").read_text())
    raw = next(r for r in (_program_data_bytes(ln) for ln in doc["meta"]["logMessages"]) if r is not None)
    d = decode_program_data(raw)
    vs, vt, dsol, tok = d["quote_reserve"], d["base_reserve"], d["sol_lamports"], d["token_raw"]
    post_resid = abs(tok - (vt + tok) * dsol / ((vs - dsol) + dsol)) / tok  # reading the row as POST: pre = (vs - dsol, vt + tok)
    pre_resid = abs(tok - vt * dsol / (vs + dsol)) / tok  # reading the row as PRE
    assert post_resid < 1e-5 < 1e-4 < pre_resid


# ---- URL, lock -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["wss://mainnet.helius-rpc.com", "wss://x.test/?api-key=abc", "wss://user:pw@x.test", "ws://x.test", "wss://x.test/v2/KEY",
                                 "https://api.mainnet-beta.solana.com", "wss://helius.example.com"])
def test_ws_url_refused(url: str) -> None:
    with pytest.raises(ValueError):
        af.check_ws_url(url)


def test_ws_url_default_accepted() -> None:
    assert af.check_ws_url(trade_source.DEFAULT_PUBLIC_WS) == trade_source.DEFAULT_PUBLIC_WS


@pytest.mark.parametrize("url", ["https://mainnet.helius-rpc.com", "https://x.test/v2/KEY", "https://x.test/?api-key=abc", "https://user:pw@x.test", "http://x.test",
                                 "https://api.mainnet-beta.solana.com/KEYSTRING", "https://x.test/helius", "https://x.test#k", "wss://api.mainnet-beta.solana.com"])
def test_rpc_url_gets_the_same_strict_rules_as_ws_url(tmp_path: Path, url: str) -> None:
    """Review item 9: a keyed path used to pass h5_shadow.check_rpc_url (host-only 'helius' test, path allowed)."""
    with pytest.raises(ValueError):
        af.check_rpc_url(url)
    with pytest.raises(ValueError):
        af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, rpc_url=url)


def test_rpc_url_default_and_bad_rpc_url_is_refused_before_anything_is_created(tmp_path: Path) -> None:
    assert af.check_rpc_url(af.DEFAULT_RPC) == af.DEFAULT_RPC
    out = tmp_path / "never"
    assert af.main(["--out-dir", str(out), "--rpc-url", "https://x.test/v2/KEY"]) == 2
    assert not out.exists()  # not even the directory or the lock file


def test_second_instance_is_refused_and_lock_is_released(tmp_path: Path) -> None:
    fd = af.acquire_lock(tmp_path)
    with pytest.raises(af.LockHeld):
        af.acquire_lock(tmp_path)
    assert af.main(["--out-dir", str(tmp_path), "--max-seconds", "1"]) == af.LOCK_HELD_EXIT
    os.close(fd)
    fd2 = af.acquire_lock(tmp_path)
    os.close(fd2)


# ---- gap rules ------------------------------------------------------------------------------------------------------
def test_slot_jump_silence_and_restart() -> None:
    w = af.ProgWatch("pumpswap", started_ms=0)
    assert w.on_notice(100, 1_000) == [] and w.on_notice(130, 2_000) == []  # +30 slots is not a gap
    g = w.on_notice(200, 3_000)
    assert [(x["kind"], x["data_loss"], x["from_slot"], x["to_slot"], x["missed_slots"]) for x in g] == [("slot_jump", True, 130, 200, 69)]
    assert w.on_notice(150, 3_500) == [] and w.hw_slot == 200  # arrival disorder is not a gap
    assert w.tick(3_000 + 19_000) == []
    s = w.tick(3_500 + 21_000)
    assert s[0]["kind"] == "silence" and s[0]["silent_ms"] == 21_000 and w.tick(3_500 + 40_000) == []  # once per episode
    w.on_notice(201, 70_000)
    assert w.tick(70_000 + 21_000)[0]["kind"] == "silence"
    assert w.feed_restart(80_000, "source_restart")["kind"] == "feed_restart" and w.hw_slot is None


def snap(reconnects: int, per_socket: list[int]) -> dict:
    return {"reconnects": reconnects, "closes": {"1006": reconnects}, "rejections": {}, "errors": {}, "per_socket": per_socket, "sockets": len(per_socket)}


def test_reconnect_gap_data_loss_only_when_every_socket_dropped() -> None:
    w = af.ProgWatch("pumpfun", 0)
    assert w.note_feed_stats(snap(0, [0, 0]), 1_000) == []
    one = w.note_feed_stats(snap(1, [1, 0]), 2_000)
    assert one[0]["kind"] == "socket_reconnect" and one[0]["data_loss"] is False and one[0]["per_socket_delta"] == [1, 0]
    both = w.note_feed_stats(snap(2, [1, 1]), 3_000)
    assert both[0]["data_loss"] is True
    w1 = af.ProgWatch("pumpfun", 0)
    w1.note_feed_stats(snap(0, [0]), 1_000)
    assert w1.note_feed_stats(snap(1, [1]), 2_000)[0]["data_loss"] is True  # a single socket always loses data


# ---- resolver --------------------------------------------------------------------------------------------------------
def test_resolver_fills_mint_and_writes_pool_info(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fetch(url: str, pools: list[str]) -> dict:
        calls.append(list(pools))
        return {p: (MINT, WSOL) for p in pools}

    feed = af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, wall=lambda: T0, rpc_url="https://api.mainnet-beta.solana.com", fetch=fetch,
                   disk_usage=roomy)
    assert feed.handle_notice("pumpswap", note(101, "a" * 88, [fixture_line("pumpswap_buy_event.b64")])) == 1
    assert len(feed.unresolved) == 1
    assert asyncio.run(feed.resolve_once()) == 1 and not feed.unresolved and len(calls) == 1
    # the next print of the same pool now carries the mint
    feed.handle_notice("pumpswap", note(102, "b" * 88, [fixture_line("pumpswap_buy_event.b64")], t=T0 + 5))
    feed.writer.close()
    rows = [json.loads(ln) for f in tmp_path.glob("tape-*.jsonl") for ln in f.read_text().splitlines()]
    kinds = [r["type"] for r in rows]
    assert kinds == ["trade", "pool_info", "trade"] and rows[1]["mint"] == MINT and rows[1]["quote_mint"] == WSOL
    assert rows[0]["mint"] is None and rows[2]["mint"] == MINT and rows[2]["quote_mint"] == WSOL


def test_resolver_gives_up_after_three_misses(tmp_path: Path) -> None:
    feed = af.Feed(tmp_path, [], {"pumpswap": 1, "pumpfun": 1}, wall=lambda: T0, rpc_url="https://api.mainnet-beta.solana.com", fetch=lambda u, p: {},
                   disk_usage=roomy)
    feed.handle_notice("pumpswap", note(101, "a" * 88, [fixture_line("pumpswap_buy_event.b64")]))
    for _ in range(3):
        asyncio.run(feed.resolve_once())
    assert not feed.unresolved and feed.counters["pools_unresolvable"] == 1
    feed.writer.close()


# ---- end to end on fake websockets ------------------------------------------------------------------------------------
class ProgNet:
    """Fake websockets.connect: the program named in the logsSubscribe payload picks the script; every socket plays its program's script."""

    def __init__(self, scripts: dict[str, list]) -> None:
        self.scripts, self.now_ms, self.connects = scripts, T0, 0

    def connect(self, url, *_a, **_k):
        net = self
        net.connects += 1

        class WS:
            steps: list = []

            async def send(self, payload: str) -> None:
                self.steps = list(net.scripts[json.loads(payload)["params"][0]["mentions"][0]])

            async def recv(self):
                while True:
                    if not self.steps:
                        await asyncio.sleep(3600)
                    step = self.steps.pop(0)
                    if step[0] == "sleep":
                        await asyncio.sleep(step[1])
                    else:
                        net.now_ms = step[1]
                        return json.dumps(step[2])

        class CM:
            async def __aenter__(self):
                return WS()

            async def __aexit__(self, *a):
                return False

        return CM()


def test_end_to_end_two_programs_two_sockets_rollover_gap_and_dedup(tmp_path: Path) -> None:
    swap_buy, swap_sell = [fixture_line("pumpswap_buy_event.b64")], [fixture_line("pumpswap_sell_event.b64")]
    bond = [fixture_line("pump_trade_event.b64")]
    life = [pd(create_event_blob()), pd(complete_event_blob())]
    next_hour = T0 + 3_600_000
    swap_script = [("msg", T0 + 10, logs_frame("sigA", 100, swap_buy)), ("sleep", 0.02), ("msg", T0 + 20, logs_frame("sigB", 101, swap_sell)),
                   ("sleep", 0.02), ("msg", T0 + 30, logs_frame("sigC", 200, swap_buy)),  # +99 slots: a gap
                   ("sleep", 0.02), ("msg", next_hour, logs_frame("sigH", 201, swap_sell))]
    fun_script = [("msg", T0 + 12, logs_frame("sigA", 100, swap_buy)),  # the same tx delivered by the other program: decoded once
                  ("sleep", 0.02), ("msg", T0 + 15, logs_frame("sigD", 100, bond)), ("sleep", 0.02), ("msg", T0 + 25, logs_frame("sigE", 101, life)),
                  ("sleep", 0.02), ("msg", T0 + 26, logs_frame("sigF", 101, bond, err={"InstructionError": [0, "x"]}))]
    net = ProgNet({af.PUMPSWAP_PROGRAM: swap_script, af.PUMP_BONDING_PROGRAM: fun_script})

    async def go() -> af.Feed:
        feed = af.Feed(tmp_path, ["wss://a.test", "wss://b.test"], {"pumpswap": 2, "pumpfun": 2}, wall=lambda: net.now_ms, stagger_s=0.0, rpc_url=None,
                       compress=False, disk_usage=roomy)  # compaction has its own tests (tools/test_arena_zst.py); here the two hour files must stay as written
        with patch.object(trade_source.websockets, "connect", net.connect), patch.object(trade_source, "time_ns_ms", lambda: net.now_ms):
            await feed.run(max_seconds=1.5)
        feed.writer.close()
        return feed

    feed = asyncio.run(go())
    files = sorted(tmp_path.glob("tape-*.jsonl"))
    assert len(files) == 2  # hour rollover
    rows = [json.loads(ln) for f in files for ln in f.read_text().splitlines()]
    for f in files:
        assert scan_file(f).bad == 0
    data = [r for r in rows if r["type"] in ("trade", "create", "complete", "pool_create")]
    keys = collections.Counter((r["signature"], r["type"], r["event_index"]) for r in data)
    assert max(keys.values()) == 1  # two sockets per program and two programs: still one row per event
    by = collections.Counter((r["prog"], r["type"]) for r in data)
    assert by == {("pumpswap", "trade"): 4, ("pumpfun", "trade"): 1, ("pumpfun", "create"): 1, ("pumpfun", "complete"): 1}  # failed sigF gives none
    gaps = [r for r in rows if r["type"] == "gap"]
    assert any(g["prog"] == "pumpswap" and g["kind"] == "slot_jump" and g["missed_slots"] == 98 for g in gaps)
    assert {r["prog"] for r in rows if r["type"] == "hb"} == {"pumpswap", "pumpfun"}
    status = json.loads((tmp_path / af.STATUS_NAME).read_text())
    assert status["schema"] == af.STATUS_SCHEMA and status["last_slot"]["pumpswap"] == 201 and status["gaps"] >= 1
    assert {"rows_per_min", "sockets", "gaps", "last_slot"} <= set(status) and len(status["sockets"]["pumpswap"]) == 2
    assert feed.counters["cross_dedup_dropped"] >= 1
