"""Tests for the tip-history source of tools/r4b_host_glue.py (QP-RULING-1031-r4b-history P-C1 (c) to (g)): slot ownership, tip gaps, fail-closed health,
twin rows, certification and the b3 status. SYNTHETIC rows, a fake tip reader and fake windows only: no network, no key, no tape, no tip file.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_r4b_tip_history.py -q
"""
from __future__ import annotations

import json
import zlib
from collections import Counter, deque
from pathlib import Path

import pytest

from tools import r4b_feed_adapter as fa
from tools import r4b_feed_merge as fm
from tools import r4b_host_glue as hg
from tools import r4b_live as lv
from tools import r4b_tip_reader as tr
from tools.test_r4b_host_glue import bd
from tools.test_r4b_tip_reader import trade

T = 1_793_000_400  # an hour mark (multiple of 3600)
WSOL = "So11111111111111111111111111111111111111112"


class Now:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


class FakeTip:
    """Stands in for TipReader: poll() pops scripted (items, released_D, caught_up)."""

    def __init__(self) -> None:
        self.h0, self.problem, self.caught_up, self.released_D, self.stats, self.script = T - 3600, None, True, None, Counter(), []

    def start_problem(self):
        return None

    def poll(self, limit: int = 0) -> list:
        if not self.script:
            return []
        items, D, caught = self.script.pop(0)
        self.released_D, self.caught_up = D, caught
        return items


class FW:
    def __init__(self, mark: int) -> None:
        self.mark, self.state = mark, fm.WindowState(mark)

    def complete(self) -> None:
        pass


def write_status(d: Path, now: int, **kw) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps({"ts_ms": now, "decoder_blobs": dict(tr.PINNED_BLOBS), "decoder_blobs_pinned": True, "trade_event_v": True, **kw}))


def make_source(tmp_path: Path, *, io_hooks=None, ionice_ok: bool = True) -> tuple[hg.TipLiveSource, Now, FakeTip]:
    now = Now(T * 1000 - 20_000)
    write_status(tmp_path / "state", now.ms)
    tip = FakeTip()
    cap = fm.DailyCreditCap(50_000, tmp_path / "cap.json")
    hooks = io_hooks or ((lambda: ionice_ok), (lambda: True))
    src = hg.TipLiveSource(tip=tip, tip_state_dir=tmp_path / "state", cap=cap, window_factory=lambda *a, **k: FW(T), spawn=lambda w: None, now_ms=now,
                           sleep=lambda s: None, require_ntp=False, io_hooks=hooks)
    return src, now, tip


def arm_window(src: hg.TipLiveSource, now: Now, n_tips: int = 11) -> FW:
    w = FW(T)
    st = w.state
    st.armed_ms = (T - 35) * 1000
    st.tips = [(st.armed_ms + 400 * i, 1000 + i) for i in range(n_tips)]  # first tip 1000 -> covered_from_slot 1001; tip 1005 = sd + 1 for sd 1004
    st.connected_ms = st.armed_ms - 100
    src._wins[T] = hg._Win(T, w, now.ms)
    return w


def helius_rows() -> list:
    return [bd(s, f"h{s}", ts=T + (s - 1000) // 3) for s in range(996, 1008)]


def deliver(src) -> list:
    out = []
    while src._pending:
        out.append(src._pending.popleft())
    return out


def tip_items(lo: int, hi: int) -> list:
    return [("trade", trade(s)) for s in range(lo, hi + 1)]


def ownership_scenario(tmp_path: Path) -> list[str]:
    """Certified mark: tip owns <= 1000, Helius (1000, 1004), tip delivers everything later. Returns the violations."""
    src, now, tip = make_source(tmp_path)
    src.start()
    arm_window(src, now)
    now.ms = T * 1000 + 300
    tip.script = [(tip_items(990, 1000), 1000, True), ([], 1000, True), ([], 1000, True)]
    src._q.put((T, helius_rows(), now.ms))
    bad, got = [], []
    for _ in range(5):
        c = src.mark_complete(T, 1004)
        got += deliver(src)
        if c.complete:
            break
    else:
        bad.append(f"never certified: {c.reason}")
    tip_rows = [r["w"]["slot"] for r in got if r.get("type") == "tip"]
    hel_rows = [r["slot"] for r in got if r.get("type") == "trade"]
    if hel_rows != [1001, 1002, 1003]:
        bad.append(f"helius-owned rows {hel_rows}")
    if tip_rows != list(range(990, 1001)):
        bad.append(f"tip rows {tip_rows}")
    tip.script = [(tip_items(1001, 1010), 1010, True)]  # later: the tip delivers the owned slots too
    src._ingest_tip_poll(now.ms)
    later = deliver(src)
    later_tip = [r["w"]["slot"] for r in later if r.get("type") == "tip"]
    if later_tip != list(range(1004, 1011)):
        bad.append(f"tip rows after the release {later_tip}")
    every = tip_rows + hel_rows + later_tip
    if len(every) != len(set(every)):
        bad.append(f"slot ingested twice: {sorted(s for s in set(every) if every.count(s) > 1)}")
    if every != sorted(every):
        bad.append("slot order broken")
    return bad


def test_slot_ownership_certified_mark(tmp_path):
    assert ownership_scenario(tmp_path) == []
    # (counted) re-run the pieces to read the counters
    src, now, tip = make_source(tmp_path / "b")
    src.start()
    arm_window(src, now)
    now.ms = T * 1000 + 300
    tip.script = [(tip_items(990, 1000), 1000, True), ([], 1000, True), ([], 1000, True), (tip_items(1001, 1010), 1010, True)]
    src._q.put((T, helius_rows(), now.ms))
    for _ in range(5):
        c = src.mark_complete(T, 1004)
        deliver(src)
        if c.complete:
            break
    src._ingest_tip_poll(now.ms)
    st = src.merger.stats
    assert st["helius_dropped_tip_owned"] == 5 and st["helius_dropped_ge_sd"] == 4 and st["helius_owned_ingested"] == 3 and st["tip_dropped_owned"] == 3
    assert src.merger.owned[T] == (1001, 1003, 1000, 1004)
    assert src._rel[T]["n_owned_slots"] == 3 and src.certs[T].complete


def test_mutation_ownership_off_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(hg.TipMerger, "tip_row", lambda self, row: True)
    monkeypatch.setattr(hg.TipMerger, "split_helius", lambda self, rows, D, sd: (list(rows), 0, 0))
    bad = ownership_scenario(tmp_path)
    assert any("twice" in b for b in bad)


def test_clock_only_rows_give_the_engine_sd_without_a_print_or_a_minute(tmp_path):
    src, now, tip = make_source(tmp_path)
    src.start()
    arm_window(src, now)
    src._q.put((T, helius_rows(), now.ms))
    src._drain_windows()
    clock_rows = [r for r in deliver(src) if r.get("type") == "tip_clock"]
    assert {r["block_time"] for r in clock_rows} == {T, T + 1, T + 2} and min(r["slot"] for r in clock_rows if r["block_time"] == T) == 1000
    eng = lv.Engine(universe_min_mbt=0, pda_fn=lambda m: "P", hash_fn=lambda s: zlib.crc32(str(s).encode()), score_fn=lambda b, X: X[:, 0])
    for r in clock_rows:
        fa.apply(eng, hg.TipAdapter().feed(r))
    assert eng.clock_point(T, provisional=True) == (1000, T)
    assert not eng.mkt and eng.max_slot == -1 and not eng.mints and eng.counters["clock_only"] == len(clock_rows)


def test_tip_not_caught_up_behind_or_gap_open_blocks_certification(tmp_path):
    for script, why in (([(tip_items(990, 1000), 1000, False)], "tip_not_caught_up"), ([(tip_items(990, 998), 998, True)], "tip_behind")):
        src, now, tip = make_source(tmp_path / why)
        src.start()
        arm_window(src, now)
        tip.script = script
        src._q.put((T, helius_rows(), now.ms))
        deliver(src)
        c = src.mark_complete(T, 1004)
        c = src.mark_complete(T, 1004) if c.reason == "rows_in_flight" else c
        assert not c.complete and c.reason in (why, "rows_in_flight"), c
        deliver(src)
        c = src.mark_complete(T, 1004)
        assert not c.complete and c.reason == why and T not in src._rel and not src.merger.owned


@pytest.mark.parametrize("reason", ["backlog_jump", "unfetchable", "skip_contradicted", "skip_contradicted_refetch", "unresolved_dropped", "tip_bad_line", "hour_file_missing", "unparsable"])
def test_a_tip_gap_of_each_reason_gives_a_gap_row_holds_the_seam_and_releases_at_1500_slots(tmp_path, reason):
    src, now, tip = make_source(tmp_path)
    src.start()
    arm_window(src, now)
    g = tr.TipGap(1100, 1120, reason, now.ms - 1000, 21)
    tip.script = [([("gap", g)] + tip_items(990, 1000), 1000, True)]
    src._q.put((T, helius_rows(), now.ms))
    src._ingest_tip_poll(now.ms)
    rows = deliver(src)
    gap_rows = [r for r in rows if r.get("type") == "gap"]
    assert len(gap_rows) == 1 and gap_rows[0]["kind"] == g.code and gap_rows[0]["data_loss"] is True
    m = fa.FeedAdapter().feed(gap_rows[0])[0]
    assert m.kind == "gap" and m.halts_buys  # lv.log_gap writes the declog gap row from this
    assert src.merger.open_tip_gaps() and src.merger.public_hw_slot() == 1099
    c = src.mark_complete(T, 1004)
    c = src.mark_complete(T, 1004) if c.reason == "rows_in_flight" else c
    assert not c.complete and c.reason == "tip_gap_open"
    src.merger.tip_progress(1120 + 1499)
    assert src.merger.open_tip_gaps()  # one slot short
    src.merger.tip_progress(1120 + 1500)
    assert not src.merger.open_tip_gaps() and src.merger.public_hw_slot() == 1120 + 1500


def test_old_catch_up_gaps_are_recorded_but_write_no_gap_row(tmp_path):
    src, now, tip = make_source(tmp_path)
    src.start()
    tip.script = [([("gap", tr.TipGap(5, 9, "backlog_jump", now.ms - 5 * 3_600_000, 5))], 1000, False)]
    src._ingest_tip_poll(now.ms)
    assert [r for r in deliver(src) if r.get("type") == "gap"] == [] and src.stats["tip_gap_history"] == 1 and src.merger.stats["tip_gaps"] == 1


@pytest.mark.parametrize("kw,code", [({"ts_ms": 1}, "status_stale"), ({"decoder_blobs_pinned": False}, "decoder_blobs_not_pinned"), ({"trade_event_v": False}, "event_v_off"),
                                     ({"decoder_blobs": {"observe/trade_decode.py": "0" * 40}}, "decoder_blobs_mismatch")])
def test_an_unhealthy_tip_fails_closed_at_start_and_at_every_mark(tmp_path, kw, code):
    src, now, tip = make_source(tmp_path)
    write_status(tmp_path / "state", kw.pop("ts_ms", now.ms), **kw)
    with pytest.raises(hg.TipUnhealthy, match=code):
        src.start()
    # healthy at start, unhealthy at the mark
    src, now, tip = make_source(tmp_path / "m")
    src.start()
    arm_window(src, now)
    write_status(tmp_path / "m" / "state", now.ms, **({"decoder_blobs_pinned": False} if code == "decoder_blobs_not_pinned" else {"trade_event_v": False}) if code != "status_stale" else {})
    if code == "status_stale":
        now.ms += 60_000
    tip.script = [(tip_items(990, 1000), 1000, True)]
    c = src.mark_complete(T, 1004)
    assert not c.complete and c.reason.startswith("tip:") and src.failed(T).startswith("tip_")
    assert src.public_hw_slot() is None  # the b3 path cannot be satisfied by an unhealthy tip: the mark is abandoned
    src._read_public(now.ms)
    assert tip.script  # no row of an unhealthy tip side was even read


def test_start_refuses_without_idle_io_and_without_history(tmp_path):
    src, _, _ = make_source(tmp_path, ionice_ok=False)
    with pytest.raises(hg.TipUnhealthy, match="ionice"):
        src.start()
    src, _, tip = make_source(tmp_path / "h")
    tip.start_problem = lambda: "history_start_missing"
    with pytest.raises(hg.TipUnhealthy, match="history_start_missing"):
        src.start()


def test_io_goes_back_to_normal_when_catch_up_is_done(tmp_path):
    calls = []
    src, now, tip = make_source(tmp_path, io_hooks=(lambda: calls.append("idle") or True, lambda: calls.append("normal") or True))
    src.start()
    tip.script = [(tip_items(1, 3), 3, False), (tip_items(4, 5), 5, True), ([], 5, True)]
    for _ in range(3):
        src._ingest_tip_poll(now.ms)
    assert calls == ["idle", "normal"]
    assert hg.ionice_cmd(7, True) == ["ionice", "-c3", "-p", "7"]


def test_a_tip_skip_of_a_helius_owned_slot_is_a_fork_and_a_contradicted_skip_withdraws_it(tmp_path):
    m = hg.TipMerger()
    m.own(T, 1000, 1004)
    m.tip_skip(1002)
    m.tip_skip(1010)  # not owned
    assert m.stats["fork_tip_skip"] == 1 and m.tip_forks == {1002: T}
    m.tip_gap(tr.TipGap(1002, 1002, "skip_contradicted", None, 1))
    assert m.stats["fork_withdrawn"] == 1 and not m.tip_forks


# ------------------------------------------------------------------------------------------------------------------------------------ (f) twin rows
def walker_stream() -> list:
    rows = [("create", {"type": "create", "mint": "M", "creator": "C", "slot": 10, "block_time": 1_000, "is_mayhem_mode": False})]
    rows += [("trade", {"venue": "pump_bonding", "mint": "M", "trader": f"t{s % 3}", "side": "buy" if s % 2 else "sell", "sol_lamports": 10**9 + s, "token_raw": 10**6, "quote_reserve": 30 * 10**9,
                        "base_reserve": 10**15, "pool": None, "slot": s, "signature": f"b{s}", "event_index": 0, "tx_index": s % 4, "block_time": 1_000 + s, "virtual_quote_reserves": None}) for s in range(12, 20)]
    rows += [("migration", {"type": "complete", "mint": "M", "slot": 20, "block_time": 1_020, "tx_index": 0, "event_index": 0, "pool": None, "quote_mint": None}),
             ("migration", {"type": "migration", "mint": "M", "slot": 20, "block_time": 1_020, "tx_index": 0, "event_index": 1, "pool": "POOL", "quote_mint": WSOL})]
    rows += [("trade", {"venue": "pumpswap", "mint": "M", "trader": f"t{s % 5}", "side": "buy" if s % 3 else "sell", "sol_lamports": 2 * 10**9 + s, "token_raw": 10**6, "quote_reserve": 85 * 10**9,
                        "base_reserve": 206 * 10**12, "pool": "POOL", "slot": s, "signature": f"p{s}", "event_index": 0, "tx_index": s % 3, "block_time": 1_000 + s,
                        "virtual_quote_reserves": 17_600_000_000}) for s in range(21, 41)]
    return rows


def state_of(eng) -> tuple:
    mints = {}
    for k, st in sorted(eng.mints.items()):
        mints[k] = (st.mslot, st.mbt, st.has_mig, st.mev_pool, st.mev_quote, st.ch, st.cslot, st.cbt, st.b_n, st.b_buy, st.b_sell, st.max_bt,
                    {f: bytes(a) for f, a in st.prints.items()})
    return (sorted(eng.clock.items()), sorted((k, tuple(v)) for k, v in eng.mkt.items()), sorted(eng.ncr.items()), mints, eng.max_slot)


def test_a_walker_row_and_its_tip_twin_give_byte_equal_engine_state_and_rows_md5():
    mk = lambda: lv.Engine(universe_min_mbt=0, pda_fn=lambda m: "POOL" if m == "M" else "P_" + m, hash_fn=lambda s: zlib.crc32(str(s).encode()), score_fn=lambda b, X: X[:, 0])  # noqa: E731
    a, b = mk(), mk()
    ad = hg.TipAdapter()
    for kind, row in walker_stream():
        {"trade": a.ingest_trade, "create": a.ingest_create, "migration": a.ingest_migration}[kind](row)
        twin = {**row, "t_recv_ms": (row["block_time"] + 2) * 1000 + 5, "source": "tip", **({"virtual_quote_reserve": None} if kind == "trade" else {})}
        fa.apply(b, ad.feed({"type": "tip", "tip_kind": kind, "w": twin}))
    assert state_of(a) == state_of(b) and a.mints["M"].mslot == 20 and len(a.mints["M"].prints["slot"]) == 20
    assert ad.pools["POOL"] == ("M", WSOL)
    for e in (a, b):  # two clock seconds at or after the mark, so the guard path can decide (no member has 10 minutes of age: the decision rows are empty on both sides)
        for i, bt in enumerate((3_700, 3_701)):
            e.ingest_trade({"venue": "pumpswap", "mint": "M", "trader": "z", "side": "buy", "sol_lamports": 1, "token_raw": 1, "quote_reserve": 1, "base_reserve": 1,
                            "pool": "POOL", "slot": 5000 + i, "signature": f"z{i}", "event_index": 0, "tx_index": 0, "block_time": bt, "virtual_quote_reserves": 17_600_000_000})
    x, y = a.decide(3600), b.decide(3600)
    assert lv.rows_md5(x.mints, x.T, x.sdv, x.X, x.score) == lv.rows_md5(y.mints, y.T, y.sdv, y.X, y.score) and x.sd == y.sd


# ------------------------------------------------------------------------------------------------------------------------------------ (g) b3 status and CLI
def test_the_b3_status_is_late_tip_for_a_tip_run_and_not_counted():
    from tools import r4b_live_host as host

    assert "late_tip" in host.LATE_STATUSES and "late_tip" in host.STATUS_NOT_COUNTED and hg.TIP_LATE_STATUS == "late_tip"
    with pytest.raises(lv.LiveRefused):
        host.DryHost(None, None, None, out_dir="/tmp/x", late_status="late_x")
    out = host.summarize([{"rec": "mark", "mark": T, "date": "2026-10-28", "status": "late_tip", "change_id": "c", "reason": "failed_tip_status_stale"}], change_id="c", require_host=None)
    assert out["n_late_tip"] == 1 and out["n_incomplete"] == 1 and out["n_counted"] == 0 and out["excluded"] == {"late_tip:failed_tip_status_stale": 1}


def test_cli_source_flag_and_tip_refusals(tmp_path):
    ap = hg.build_parser()
    a = ap.parse_args(["run", "--declog-root", "d", "--out", "o", "--change-id", "c", "--key-file", "k"])
    assert a.source == "mirror" and a.tape_dir is None and a.tip_dir == tr.DEFAULT_TIP_DIR and a.tip_state_dir == tr.DEFAULT_STATE_DIR
    a = ap.parse_args(["run", "--source", "tip", "--tip-dir", str(tmp_path), "--tip-state-dir", str(tmp_path), "--declog-root", "d", "--out", "o", "--change-id", "c", "--key-file", "k",
                       "--catch-up-hours", "54"])
    with pytest.raises(hg.Refused):
        hg.check_args(a)  # key file missing is checked first; the 54 h look-back is refused either way
    key = tmp_path / "k.env"
    key.write_text("HELIUS_API_KEY=abcdefghijklmnop\n")
    a.key_file = str(key)
    with pytest.raises(hg.Refused, match="at most 53"):
        hg.check_args(a)
    a.catch_up_hours = 52
    hg.check_args(a)
    a.source = "mirror"
    with pytest.raises(hg.Refused, match="tape-dir"):
        hg.check_args(a)
