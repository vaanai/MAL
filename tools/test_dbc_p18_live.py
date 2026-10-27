"""Tests for tools/dbc_p18_live.py, dbc_p18_gate.py and dbc_p18_wire_equiv.py (P18 pre-build). Fake sockets, fake fetchers, injected clocks, synthetic tape and the committed
exploration fixture only. No network, no key, no real tape hour, nothing under fwdv1/ or [2026-10-17, ...)."""
from __future__ import annotations

import asyncio
import base64
import builtins
import gzip
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tools import dbc_config as C
from tools import dbc_fwd as F
from tools import dbc_fwd_feed as FF
from tools import dbc_p18_gate as G
from tools import dbc_p18_live as W
from tools import dbc_p18_run as P
from tools import dbc_p18_wire_equiv as WE
from tools import dbc_price as L
from tools import dbc_screen as S
from tools.arena_declog import DecisionLog
from tools.test_dbc_fwd import END, START, T0, _raises, synth_rows, tape_root

D = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
FIX = Path(__file__).parent / "fixtures" / "dbc_txs_2026-10-14T12.json.gz"
OPEN = D(2026, 11, 2)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _instant(_s: float) -> None:
    await asyncio.sleep(0)


class WS:
    def __init__(self, msgs, tail="eof"):
        self.msgs, self.tail, self.sent = list(msgs), tail, []

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def recv(self):
        await asyncio.sleep(0)
        if self.msgs:
            return self.msgs.pop(0)
        if self.tail == "eof":
            raise ConnectionError("eof")
        await asyncio.sleep(3600)


class Conn:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *a):
        return False


def slot_msg(s):
    return json.dumps({"jsonrpc": "2.0", "method": "slotNotification", "params": {"subscription": 2, "result": {"slot": s, "parent": s - 1, "root": s - 32}}})


# ------------------------------------------------------------------------------------------------------------------------------ 1. slot signal
def test_parse_slot_notification_and_signal_monotonic():
    assert W.parse_slot_notification(slot_msg(100)) == 100
    for bad in ('{"jsonrpc":"2.0","result":7,"id":1}', "junk", '{"method":"slotNotification","params":{"result":{"slot":true}}}', b"[]"):
        assert W.parse_slot_notification(bad) is None
    s = W.SlotSignals(2)
    assert [s.feed(x) for x in (100, 101, 101, 99, 105)] == [98, 99, None, None, 103]


def test_slot_feed_fake_socket_subscribes_counts_bytes_and_reconnects():
    got, stats = [], {}
    meter = FF.Meter(None)
    stop = asyncio.Event()
    socks = [WS([slot_msg(10), slot_msg(11), "{}"]), WS([slot_msg(12)])]
    urls = []

    def connect(url):
        urls.append(url)
        return Conn(socks.pop(0))

    def on_slot(s):
        got.append(s)
        if s == 12:
            stop.set()

    run(W.run_slot_feed("wss://x/?api-key=K", on_slot, stop, connect=connect, meter=meter, stats=stats, sleep=_instant))
    assert got == [10, 11, 12] and stats["notifications"] == 3 and stats["reconnects"] >= 1 and meter.connections == 2 and meter.bytes > 0
    assert len(urls) == 2


def test_slot_feed_logs_never_leak_the_key(caplog):
    stop = asyncio.Event()

    def connect(url):
        class Boom:
            async def __aenter__(self):
                stop.set()
                raise OSError("cannot reach wss://h/?api-key=SECRETKEY")

            async def __aexit__(self, *a):
                return False

        return Boom()

    with caplog.at_level("WARNING", logger="dbc_fwd_feed"):
        run(W.run_slot_feed("wss://h/?api-key=SECRETKEY", lambda s: None, stop, connect=connect, meter=FF.Meter(None), stats={}, sleep=_instant))
    assert "SECRETKEY" not in caplog.text and "api-key=***" in caplog.text


def test_wall_clock_slots_never_run_ahead_of_the_fastest_row():
    w = W.WallClockSlots(lag=2)
    assert w.tick(10.0) is None
    for i in range(50):  # rows 1 s behind the chain on every slot, 0.4 s per slot
        slot = 1000 + i
        w.observe(slot, slot * 0.4 + 1.0)
    now = 1049 * 0.4 + 1.0 + 0.4 * 5  # five slot times after the last row
    assert w.tick(now) == 1049 + 5 - 2
    assert w.tick(now) is None  # strictly increasing
    assert w.tick(now + 0.8) == 1049 + 7 - 2


# ------------------------------------------------------------------------------------------------------------------------------ 2. config fetch
def _enc(t, v, out):
    if isinstance(t, str):
        n = C._PRIM[t]
        out += int(v or 0).to_bytes(n, "little", signed=t.startswith("i")) if t != "pubkey" else bytes(n)
    elif "array" in t:
        et, n = t["array"]
        for i in range(n):
            _enc(et, (v or [None] * n)[i], out)
    else:
        for f in C._T[t["defined"]["name"]]["type"]["fields"]:
            _enc(f["type"], (v or {}).get(f["name"]), out)


def account_b64(thr: int, xfer_bps: int = 0, mode: int = 0) -> str:
    C._load()
    disc = next(k for k, n in C._ACC.items() if n == "PoolConfig")
    out = bytearray(disc)
    _enc({"defined": {"name": "PoolConfig"}}, {"migration_quote_threshold": thr, "transfer_fee_basis_points": xfer_bps, "collect_fee_mode": mode}, out)
    return base64.b64encode(bytes(out)).decode()


def sol(cfg, **kw):
    return {"type": "trade", "config": cfg, "quote_mint": L.WSOL, **kw}


class Maps:
    """Save / restore the dbc_price config maps around a test."""

    def __enter__(self):
        self.s = (dict(L.CFG_THR), set(L.CFG_XFER), set(L.CFG_UNMAPPED))
        return self

    def __exit__(self, *a):
        L.CFG_THR.clear(), L.CFG_XFER.clear(), L.CFG_UNMAPPED.clear()
        L.CFG_THR.update(self.s[0]), L.CFG_XFER.update(self.s[1]), L.CFG_UNMAPPED.update(self.s[2])


def test_account_encoder_round_trips_through_the_reads_decoder():
    m = C.summary_map({"c": {"data_b64": account_b64(85_000_000_000, 25, 1)}})["c"]
    assert m["thr"] == 85_000_000_000 and m["transfer_fee_bps"] == 25 and m["collect_fee_mode"] == 1 and m["kind"] == "PoolConfig"


def test_first_sight_fetches_once_caches_and_maps():
    calls = []

    def fetch(cfg):
        calls.append(cfg)
        return {"data_b64": account_b64(1234)}

    with Maps():
        g = W.ConfigGate(fetch)
        ev = g.check(sol("NEWCFG"))
        assert ev["result"] == "mapped" and ev["thr"] == 1234 and ev["xfer"] is False and L.CFG_THR["NEWCFG"] == 1234
        assert g.check(sol("NEWCFG")) is None and calls == ["NEWCFG"]
        assert g.check({"type": "trade", "config": "NONSOL", "quote_mint": "OtherMint"}) is None and g.check({"type": "curve_complete", "config": "CC", "quote_mint": L.WSOL}) is None
        assert calls == ["NEWCFG"]
        assert g.check({"type": "trade"}) is None and g.check({"type": "trade", "config": None}) is None
        assert g.stats["sights"] == 1 and g.stats["mapped"] == 1 and g.stats["rpc_calls"] == 1


def test_first_sight_transfer_fee_config_is_excluded_like_the_static_map():
    with Maps():
        g = W.ConfigGate(lambda c: {"data_b64": account_b64(99, 50)})
        ev = g.check(sol("XF"))
        assert ev["xfer"] is True and "XF" in L.CFG_XFER and L.CFG_THR["XF"] == 99


def test_first_sight_absent_undecodable_and_failing_configs_are_unmapped_with_a_reason():
    n = [0]

    def boom(cfg):
        n[0] += 1
        raise RuntimeError("rpc down wss://h/?api-key=SECRETKEY")

    with Maps():
        a = W.ConfigGate(lambda c: None).check(sol("ABSENT"))
        b = W.ConfigGate(lambda c: {"data_b64": "AAAA"}).check(sol("JUNK"))
        gate = W.ConfigGate(boom, attempts=2)
        c = gate.check(sol("DOWN"))
        assert (a["result"], a["reason"], a["attempts"]) == ("unmapped", "absent", 1)
        assert (b["result"], b["reason"]) == ("unmapped", "undecodable")
        assert (c["result"], c["reason"], c["attempts"]) == ("unmapped", "rpc_error", 2) and n[0] == 2 and "SECRETKEY" not in json.dumps(c)
        assert {"ABSENT", "JUNK", "DOWN"} <= L.CFG_UNMAPPED and "ABSENT" not in L.CFG_THR
        assert gate.check(sol("DOWN")) is None and n[0] == 2  # a negative result is cached too


def test_account_fetch_request_shape_and_responses():
    seen = {}

    class Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.body).encode()

    def opener(req, timeout):
        seen["body"], seen["timeout"] = json.loads(req.data), timeout
        return Resp(seen["resp"])

    f = W.make_account_fetch("https://h/?api-key=K", urlopen=opener)
    seen["resp"] = {"result": {"value": {"data": ["QUJD", "base64"]}}}
    assert f("CFG") == {"data_b64": "QUJD"}
    assert seen["body"]["method"] == "getAccountInfo" and seen["body"]["params"] == ["CFG", {"encoding": "base64", "commitment": "finalized"}] and seen["timeout"] == W.CFG_TIMEOUT_S
    seen["resp"] = {"result": {"value": None}}
    assert f("CFG") is None
    seen["resp"] = {"error": {"message": "x"}}
    with _raises(RuntimeError):
        f("CFG")


# ------------------------------------------------------------------------------------------------------------------------------ runner / equivalence
def _fixture_rows():
    return [r for m, t in FF.fixture_notifications(FIX) for r in FF.rows_from_notification(m, t)]


def _decision_view(rows):
    return [(r["type"], r.get("key"), r.get("fire_slot"), r.get("exit_slot")) for r in rows if r["type"] in ("decision", "exit")]


def test_wired_runner_on_the_fixture_matches_the_plain_runner_when_all_configs_are_mapped():
    rows = _fixture_rows()
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        base = P.Runner(a, bt_src="recv")
        gate = W.ConfigGate(lambda c: (_ for _ in ()).throw(AssertionError("fetch")))
        wired = W.WiredRunner(b, gate=gate, slot_source="ws", bt_src="recv")
        async def feed():
            for r in rows:
                base.on_row(r)
                wired.ingest(r)
                wired.on_slot_notified(int(r["slot"]) + 5)

        run(feed())
        base.finish()
        wired.finish()
        rb, rw = base.log.rows(F.BOOK), wired.log.rows(F.BOOK)
        assert gate.stats["sights"] == 0
        assert [r["type"] for r in rw if r["type"] == "wire"] == ["wire"]
        w = next(r for r in rw if r["type"] == "wire")
        assert w["close_lag_slots"] == W.CLOSE_LAG_SLOTS and set(w["code_sha256"]) == set(W.CODE_FILES) and w["cfg_first_sight_fetch"] is True
        assert _decision_view(rb) == _decision_view(rw)
        assert base.eng.stats["rows"] == wired.eng.stats["rows"] and base.eng.stats["sol_rows"] == wired.eng.stats["sol_rows"]
        assert wired.closer.stats["signals_advanced"] > 0
        assert wired.tick()["wire"]["slot_source"] == "ws"


def test_wired_runner_maps_a_config_missing_from_the_static_map_the_same_way_the_map_would():
    rows = _fixture_rows()
    cfgs = sorted({r["config"] for r in rows if r.get("config")})
    with Maps():
        L.load_config_thresholds(str(Path(__file__).resolve().parent.parent / "docs/research/dbc-p17/data/cfg_summary.json"))
        thr = {c: L.CFG_THR[c] for c in cfgs}
        gone = cfgs[0]
        with tempfile.TemporaryDirectory() as td:
            gate = W.ConfigGate(lambda c: {"data_b64": account_b64(thr[c])})
            run_ = W.WiredRunner(td, gate=gate, bt_src="recv", cfg_summary=None)
            L.CFG_THR.pop(gone), L.CFG_XFER.discard(gone)  # the Runner just loaded the static map; drop one config
            async def feed():
                for r in rows:
                    run_.ingest(r)
                while run_.hold or run_.fifo:
                    await asyncio.sleep(0.01)

            run(feed())
            assert L.CFG_THR[gone] == thr[gone] and gate.stats["mapped"] == 1
            ev = [r for r in run_.log.rows(F.BOOK) if r["type"] == "cfg_fetch"]
            assert len(ev) == 1 and ev[0]["config"] == gone and ev[0]["result"] == "mapped" and ev[0]["t_end_ms"] >= ev[0]["t_start_ms"] and ev[0]["attempts"] == 1 and not run_.hold


def _run_tape(feed_cls=None, lag=2):
    L.enable_swap3_pricing()
    with tempfile.TemporaryDirectory() as td:
        log = DecisionLog(td)
        live_ss: dict = {}
        eng = P.PrunedEngine(T0 + 3600.0, ss_live=lambda hk, m=live_ss: S.slot_s_for(m, hk), log=log, meta={"bt_src": "tape_block_time"})
        log.append(F.BOOK, T0 * 1000, {"type": "start", "book": F.BOOK})
        closer = P.SlotCloser(eng, log) if feed_cls else None
        if feed_cls:
            feed_cls.lag = lag
        feed = feed_cls(closer, 2) if feed_cls else eng
        F.stream_hours(feed, START, END, None, live_ss, log)
        return log.md5(), log.rows(F.BOOK), (closer.stats if closer else None), feed


def test_wire_feed_leaves_the_decision_log_byte_identical_on_synthetic_tape():
    with tape_root(), Maps():
        for r in synth_rows():  # the proof's premise: every tape config is already in the map (rows carry their own threshold, so this changes no decision)
            if r.get("config"):
                L.CFG_THR.setdefault(r["config"], 85 * 10**9)
        m0, r0, _, _ = _run_tape()
        m1, r1, st1, f1 = _run_tape(WE.WiredFeed, 2)
        m2, r2, st2, f2 = _run_tape(WE.WiredFeed, 1)
    assert len([r for r in r0 if r["type"] == "decision"]) >= 3
    assert m0 == m1 == m2 and r0 == r1 == r2
    assert st1["late_after_close"] == 0 and st2["late_after_close"] == 0 and st1["signals_advanced"] > 0
    assert f1.n_notifications > 0 and f1.gate.stats["sights"] == 0 and f1.gate.stats["rpc_calls"] == 0


def test_wire_equiv_refuses_windows_outside_the_exploration_hours():
    with tempfile.TemporaryDirectory() as td:
        assert WE.main(["run", "--out", str(Path(td) / "x"), "--root", "/data2/mal/venue-tape-backfill/fwdv1/meteora_dbc"]) == 3


# ------------------------------------------------------------------------------------------------------------------------------ 3. probe
def test_feed_lag_report_piecewise_anchors_and_floor_correction():
    # chain: block time = 0.4 * slot (exact), anchors are floored seconds; rows arrive 1.25 s after their true time
    slots = list(range(1000, 2000, 7))
    rows = [(s, int((0.4 * s + 1.25) * 1000)) for s in slots]
    anchors = [(s, int(0.4 * s)) for s in range(1000, 2001, 150)]
    rep = W.feed_lag_report(rows, anchors)
    # floor(0.4 s) + 0.5 is within 0.5 s of the truth, so the p90 is within 0.5 s of 1.25 s
    assert rep["n_anchors"] == 7 and rep["n_rows_used"] > 100 and abs(rep["p90_s"] - 1.25) <= 0.6 and rep["p50_s"] <= rep["p90_s"] <= rep["p99_s"] and rep["resid_sd_s"] is not None
    assert W.feed_lag_report(rows, anchors[:1])["p90_s"] is None and W.feed_lag_report([], anchors)["p90_s"] is None
    far = [(s + 10_000_000, t) for s, t in rows]
    assert W.feed_lag_report(far, anchors)["n_rows_used"] == 0


def _fixture_messages(n_per_slot_step=3):
    txs = json.loads(gzip.open(FIX, "rt").read())
    out = []
    for k in range(300):  # stretch the 85-tx fixture over 1500 slots so that anchors >= 150 slots old exist
        tx = dict(txs[k % len(txs)])
        tx["slot"] = 1_000_000 + k * 5
        out.append(json.dumps(FF.notification_of(tx)))
    return out


def test_probe_fake_socket_writes_bytes_credits_and_lag_and_makes_no_decision_log():
    msgs = _fixture_messages()
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        return int(params[0] * 0.4)

    async def sleep(s):
        await asyncio.sleep(s / 2000.0)

    clock = [1_700_000_000_000]

    def clock_ms():
        clock[0] += 120
        return clock[0]

    socks = [WS(msgs, tail="eof")]

    def connect(url):
        return Conn(socks.pop(0) if socks else WS([], tail="eof"))

    with tempfile.TemporaryDirectory() as td:
        res = run(W.run_probe("wss://h/?api-key=K", rpc, Path(td), 50_000.0, minutes=10, connect=connect, clock_ms=clock_ms, sleep=sleep, anchor_every_s=1.0))
        assert json.loads((Path(td) / "probe.json").read_text())["bytes"] == res["bytes"]
        assert not (Path(td) / "declog").exists()
    assert res["kind"] == "p18-probe" and res["bytes"] > 0 and res["bytes_per_s"] > 0 and res["msgs"] == len(msgs)
    assert res["credits_est_meter"] > 0 and res["credits_est_total"] >= res["credits_est_meter"] and "not billed" in res["credits_basis"]
    assert all(m == "getBlockTime" for m, _ in calls) and res["getBlockTime_calls"] == len(calls) and res["day_bar_credits"] == 255_000
    assert res["getBlockTime_calls"] >= 1 and res["feed_lag"]["n_anchors"] >= 1
    assert set(res["feed_lag"]) >= {"p50_s", "p90_s", "p99_s", "n_anchors", "resid_sd_s"}


def test_probe_needs_a_credit_cap_and_stops_on_it():
    async def go(cap):
        return await W.run_probe("wss://h/?api-key=K", lambda m, p: None, Path(tempfile.mkdtemp()), cap, minutes=0.5, connect=lambda u: Conn(WS(_fixture_messages())), sleep=_instant)

    with _raises(ValueError):
        run(go(None))
    res = run(go(0.5))  # the first message already crosses the cap
    assert res["exit"] == FF.EXIT_CAP


# ------------------------------------------------------------------------------------------------------------------------------ 4. gate
def test_marker_probe_uses_lexists_only_never_opens_and_polls_to_the_deadline(monkeypatch):
    def no_open(*a, **k):
        raise AssertionError("the marker must never be opened")

    monkeypatch.setattr(builtins, "open", no_open)
    t = [0.0]
    sleeps = []
    clock = lambda: t[0]  # noqa: E731

    def sleep(s):
        sleeps.append(s)
        t[0] += s

    seen = []
    assert G.marker_probe(lexists=lambda p: seen.append(p) or False, deadline_s=90, poll_s=30, clock=clock, sleep=sleep) == {"marker_exists": False, "polls": 4, "waited_s": 90.0}
    t[0] = 0.0
    n = [0]
    r = G.marker_probe(lexists=lambda p: (n.__setitem__(0, n[0] + 1) or n[0]) >= 3, deadline_s=600, poll_s=30, clock=clock, sleep=sleep)
    assert r["marker_exists"] is True and r["polls"] == 3 and set(seen) == {P.MARKER}
    with _raises(ValueError):
        G.marker_probe(path="/etc/passwd")


def test_arm_refused_before_the_time_floor_and_for_bad_input_then_writes_a_valid_flag():
    with tempfile.TemporaryDirectory() as td:
        flag = str(Path(td) / "p18" / "FLAG")
        with _raises(P.StartRefused):
            G.arm(flag, "j_abc", D(2026, 11, 1, 0, 9))
        for bad in ("", "a b", "x/y"):
            with _raises(P.StartRefused):
                G.arm(flag, bad, OPEN)
        with _raises(P.StartRefused):
            G.arm(P.MARKER, "j_abc", OPEN)
        assert not Path(flag).exists() and not G.flag_valid(flag)
        body = G.arm(flag, "j_abc", OPEN)
        assert G.flag_valid(flag) and body["marker_opened"] is False and body["marker_path"] == P.MARKER
        d = json.loads(Path(flag).read_text())
        for k, v in (("kind", "other"), ("marker_opened", True), ("probe_job", ""), ("armed_utc", "2026-10-30T00:00:00Z"), ("marker_path", "/x")):
            Path(flag).write_text(json.dumps({**d, k: v}))
            assert not G.flag_valid(flag), k
        Path(flag).write_text("not json")
        assert not G.flag_valid(flag)


def test_flag_equal_to_the_marker_is_lstat_only(monkeypatch):
    def no_open(*a, **k):
        raise AssertionError("opened")

    monkeypatch.setattr(builtins, "open", no_open)
    monkeypatch.setattr(G.os.path, "lexists", lambda p: p == P.MARKER)
    assert G.flag_valid(P.MARKER) is True


def test_start_guard_semantics_are_unchanged_flag_stands_in_for_the_marker_time_floor_stays():
    with tempfile.TemporaryDirectory() as td:
        flag = str(Path(td) / "FLAG")
        with _raises(P.StartRefused):
            P.start_guard(live=True, test=False, now=OPEN, marker=flag, marker_exists=G.flag_valid)  # no flag yet
        G.arm(flag, "j_abc", OPEN)
        P.start_guard(live=True, test=False, now=OPEN, marker=flag, marker_exists=G.flag_valid)
        with _raises(P.StartRefused):  # a valid flag does not move the 11-01T00:10Z floor
            P.start_guard(live=True, test=False, now=D(2026, 11, 1, 0, 9), marker=flag, marker_exists=G.flag_valid)


def test_gate_cli(capsys):
    with tempfile.TemporaryDirectory() as td:
        flag = str(Path(td) / "F")
        assert G.main(["check", "--flag", flag]) == 1
        assert G.main(["arm", "--flag", flag, "--probe-job", "j_x"]) in (0, 2)  # 2 before the floor, 0 after it
        capsys.readouterr()


# ------------------------------------------------------------------------------------------------------------------------------ live entry
def test_cli_refuses_without_flag_and_never_writes():
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "o")
        assert W.main(["--live", "--out", out]) == 2
        assert W.main(["--live", "--probe", "--env-file", "/nonexistent", "--look-flag", str(Path(td) / "nope"), "--out", out]) == 2
        assert W.main(["--probe", "--env-file", "/nonexistent", "--look-flag", str(Path(td) / "nope"), "--out", out]) == 2  # no cap
        assert W.main(["--probe", "--env-file", "/nonexistent", "--credit-cap", "5000", "--look-flag", str(Path(td) / "nope"), "--out", out]) == 2  # no flag
        assert W.main(["--live", "--env-file", "/nonexistent", "--look-flag", str(Path(td) / "nope"), "--out", out]) == 2
        assert not Path(out).exists()


def test_live_loop_on_fake_sockets_wires_slots_and_config_fetch():
    msgs = _fixture_messages()
    sent = []
    fetched = []

    def connect(url):
        class C_:
            async def __aenter__(self_):
                self_.ws = WS(msgs if False else [], tail="block")
                return self_

            async def __aexit__(self_, *a):
                return False

            async def send(self_, s):
                req = json.loads(s)
                sent.append(req["method"])
                if req["method"] == "transactionSubscribe":
                    self_.ws.msgs = list(msgs)
                else:
                    self_.ws.msgs = [slot_msg(1_000_000 + 5 * k + 4) for k in range(300)]

            async def recv(self_):
                return await self_.ws.recv()

        return C_()

    def fetch(cfg):
        fetched.append(cfg)
        return {"data_b64": account_b64(85_000_000_000)}

    with tempfile.TemporaryDirectory() as td, Maps():
        env = Path(td) / "h.env"
        env.write_text("HELIUS_API_KEY=testkey\n")
        flag = str(Path(td) / "FLAG")
        G.arm(flag, "j_abc", OPEN)
        summ = json.loads((Path(__file__).resolve().parent.parent / "docs/research/dbc-p17/data/cfg_summary.json").read_text())
        used = sorted({r["config"] for r in _fixture_rows() if r.get("config")})
        summ.pop(used[0])  # one config the static map does not know -> first-sight fetch
        (Path(td) / "cfg.json").write_text(json.dumps(summ))
        out = Path(td) / "out"
        total = sum(len(m) for m in msgs)
        rc = run(asyncio.wait_for(W.run_live_wired(str(out), str(env), total / 100_000.0 * 2 * 0.9, flag, now=OPEN, connect=connect, fetch=fetch, cfg_summary=str(Path(td) / "cfg.json")), timeout=60))
        st = json.loads((out / "status.json").read_text())
        rows = DecisionLog(str(out / "declog")).rows(F.BOOK)
    assert rc == FF.EXIT_CAP and set(sent) == {"transactionSubscribe", "slotSubscribe"}
    assert fetched == [used[0]] and st["wire"]["cfg_gate"]["mapped"] == 1 and st["wire"]["cfg_unmapped"]["ok"] is True and st["slot_close"]["signals"] > 0
    kinds = [r["type"] for r in rows]  # (the test clock is real time while start_ts is 11-02, so file order is not asserted)
    assert {"start", "wire", "cfg_fetch", "stop"} <= set(kinds) and sorted(r["seq"] for r in rows) == list(range(len(rows)))
    assert "testkey" not in json.dumps(rows) and "testkey" not in json.dumps(st)


def test_live_entry_refuses_before_the_guard_opens():
    with tempfile.TemporaryDirectory() as td:
        flag = str(Path(td) / "FLAG")
        G.arm(flag, "j_abc", OPEN)
        with _raises(P.StartRefused):
            run(W.run_live_wired(str(Path(td) / "o"), "/nonexistent", None, flag, now=D(2026, 10, 30)))
        assert not (Path(td) / "o").exists()


# ------------------------------------------------------------------------------------------------------------------------------ fetch is off the loop
def test_slow_fetch_does_not_delay_receive_stamps_and_rows_wait_in_order():
    import threading
    import time as _t

    release = threading.Event()
    order = []

    def fetch(cfg):
        order.append(("fetch_start", cfg))
        assert release.wait(5)
        return {"data_b64": account_b64(1234)}

    stamps = []

    async def go():
        with Maps(), tempfile.TemporaryDirectory() as td:
            rows = []
            run_ = W.WiredRunner(td, gate=W.ConfigGate(fetch), bt_src="recv", start_ts=1_700_000_000.0)
            run_.on_row = lambda r: (rows.append(r), order.append(("row", r["signature"])))[0]  # record delivery order only
            # the feed stamps t_recv BEFORE the sink: emulate run_feed (recv -> stamp -> sink) with a real clock
            def deliver(sig, cfg):
                r = sol(cfg, signature=sig, slot=1, t_recv_ms=_t.time_ns() // 1_000_000, bt_src="recv")
                stamps.append((sig, r["t_recv_ms"]))
                run_.ingest(r)

            deliver("a", "NEWCFG")  # starts the fetch (blocked until `release`)
            await asyncio.sleep(0.05)
            t0 = _t.monotonic()
            for i in range(50):  # the loop is free: 50 more rows are received and stamped while the fetch is stuck
                deliver(f"b{i}", "NEWCFG")
                await asyncio.sleep(0.001)
            loop_time = _t.monotonic() - t0
            assert run_.hold and not rows and len(run_.fifo) == 51
            assert run_.on_slot_notified(100) is False and len(run_.fifo) == 52  # signals queue behind the fetch too
            release.set()
            while run_.hold or run_.fifo:
                await asyncio.sleep(0.01)
            return rows, loop_time, run_

    rows, loop_time, run_ = run(go())
    assert [r["signature"] for r in rows] == ["a"] + [f"b{i}" for i in range(50)]  # FIFO order kept
    ts = [t for _, t in stamps]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    assert max(gaps[1:]) < 500 and loop_time < 2.0  # stamps follow the real arrival (a blocked loop would show one multi-second gap)
    assert order[0] == ("fetch_start", "NEWCFG") and run_.fetch_waits == 1


def test_blocking_call_on_the_loop_would_have_been_visible_to_the_same_check():
    import time as _t

    async def go():
        t0 = _t.monotonic()
        _t.sleep(0.3)  # what the old code did
        await asyncio.sleep(0)
        return _t.monotonic() - t0

    assert run(go()) >= 0.3  # the timing method above does see a blocked loop


def test_non_sol_rows_never_fetch_and_unmapped_share_is_split_by_reason():
    calls = []

    def fetch(cfg):
        calls.append(cfg)
        return None if cfg == "GONE" else {"data_b64": "AAAA"}

    async def go():
        with Maps(), tempfile.TemporaryDirectory() as td:
            run_ = W.WiredRunner(td, gate=W.ConfigGate(fetch), bt_src="recv", start_ts=1_700_000_000.0)
            L.CFG_THR["KNOWN"] = 1
            run_.closer = type("Stub", (), {"on_row": lambda self, r: None})()  # engine not under test here
            seq = [sol("KNOWN", slot=1), sol("GONE", slot=1), sol("GONE", slot=1), sol("JUNK", slot=1),
                   {"type": "trade", "config": "OTHERQ", "quote_mint": "X", "slot": 1}]
            for r in seq:
                run_.ingest({**r, "signature": "s", "t_recv_ms": 1_700_000_000_000, "bt_src": "recv"})
                while run_.hold:
                    await asyncio.sleep(0.01)
            return run_

    run_ = run(go())
    rep = run_.unmapped_report()
    assert calls == ["GONE", "JUNK"]  # one fetch per config, none for the non-SOL pool
    assert rep["configs_by_reason"] == {"absent": 1, "undecodable": 1} and rep["sol_trade_rows"] == 4 and rep["unmapped_rows"] == 3 and rep["ok"] is False and rep["bar"] == 0.05


def test_stall_report_counts_fetches_and_decisions_received_during_one():
    rows = [{"type": "cfg_fetch", "t_start_ms": 1000, "t_end_ms": 1400}, {"type": "cfg_fetch", "t_start_ms": 5000, "t_end_ms": 5100},
            {"type": "decision", "t_recv_ms": 1200}, {"type": "decision", "t_recv_ms": 3000}, {"type": "decision", "t_recv_ms": 1000}, {"type": "exit", "t_recv_ms": 1200}]
    assert W.stall_report(rows) == {"fetches": 2, "fetch_ms_total": 500, "fetch_ms_max": 400, "decisions_with_stamped_trigger": 3, "decisions_received_during_a_fetch": 2}


def test_probe_credit_figure_is_labelled_a_lower_bound():
    res = run(W.run_probe("wss://h/?api-key=K", lambda m, p: None, Path(tempfile.mkdtemp()), 0.5, minutes=0.5, connect=lambda u: Conn(WS(_fixture_messages())), sleep=_instant))
    assert res["credits_est_is_lower_bound"] is True and "LOWER BOUND" in res["getBlockTime_note"]


def test_wire_equiv_check_applies_the_qp_pass_criteria():
    here = Path(WE.__file__).resolve().parent
    good = {"declog": {"md5": WE.BASELINE_MD5, "head": [WE.BASELINE_HEAD_SHA, WE.BASELINE_HEAD_SEQ]}, "hours_streamed": 72, "decisions_live": 551, "stats": {"late_rows": 0},
            "slot_close": {"late_after_close": 0, "signals_advanced": 5},
            "wire": {"notifications": 9, "cfg_gate": {"sights": 0, "rpc_calls": 0}, "code_sha256": {f: F.sha256_file(here / f) for f in W.CODE_FILES}}}

    def chk(r):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "equiv.json").write_text(json.dumps(r))
            return WE.check([Path(td)])

    assert chk(good)["ok"] is True
    for path, val, name in ((("declog", "md5"), "x", "md5"), (("hours_streamed",), 71, "hours"), (("decisions_live",), 550, "decisions"), (("slot_close", "late_after_close"), 1, "late_after_close"),
                            (("slot_close", "signals_advanced"), 0, "signals_advanced"), (("wire", "notifications"), 0, "notifications"), (("wire", "cfg_gate", "sights"), 1, "cfg_sights"),
                            (("wire", "code_sha256"), {}, "code_sha256"), (("stats", "late_rows"), 2, "late_rows")):
        r = json.loads(json.dumps(good))
        d = r
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = val
        out = chk(r)
        assert not out["ok"] and any(f.endswith(name) for f in out["fails"]), name


def test_wall_tick_during_a_slow_fetch_queues_behind_the_rows():
    import threading

    release = threading.Event()
    seen = []

    def fetch(cfg):
        assert release.wait(5)
        return {"data_b64": account_b64(1234)}

    async def go():
        with Maps(), tempfile.TemporaryDirectory() as td:
            run_ = W.WiredRunner(td, gate=W.ConfigGate(fetch), slot_source="wall", bt_src="recv", start_ts=1_700_000_000.0)
            run_.on_row = lambda r: seen.append(("row", r["signature"]))
            run_._signal = lambda sig: seen.append(("sig", sig)) or True
            run_.wall.observe(1000, 100.0)
            run_.ingest(sol("NEWCFG", signature="a", slot=1000, t_recv_ms=100_000, bt_src="recv"))  # starts the fetch, holds
            await asyncio.sleep(0.05)
            run_.ingest(sol("NEWCFG", signature="b", slot=1001, t_recv_ms=100_400, bt_src="recv"))
            assert run_.hold and run_.wall_tick(100.0 + 0.4 * 10) is False and seen == [] and [k for k, _ in run_.fifo] == ["row", "row", "sig"]
            release.set()
            while run_.hold or run_.fifo:
                await asyncio.sleep(0.01)

    run(go())
    assert seen == [("row", "a"), ("row", "b"), ("sig", seen[-1][1])] and seen[-1][0] == "sig"


def test_live_cli_refuses_wall_and_none_slot_sources_and_other_lags():
    with tempfile.TemporaryDirectory() as td:
        flag = str(Path(td) / "FLAG")
        G.arm(flag, "j_abc", OPEN)
        for extra in (["--slot-source", "wall"], ["--slot-source", "none"], ["--close-lag", "1"]):
            assert W.main(["--live", "--env-file", "/nonexistent", "--look-flag", flag, "--out", str(Path(td) / "o"), *extra]) == 2
        assert not (Path(td) / "o").exists()
