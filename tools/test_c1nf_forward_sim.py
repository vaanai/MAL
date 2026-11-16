"""c1nf_forward_sim: adapter field mapping, plug-in decider (warm-up, missing ledger), oracle-bypass refusal before 2026-10-16T01Z, forward ledger steps, 300 s exit through the
arena engine, book-file shape vs osb_follow, restart md5 check. Synthetic fixtures only; no real tape, ledger, model, fill or P&L is opened. Nothing here says any book makes money."""
import collections
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import c1nf_forward_sim as S  # noqa: E402
import osb_follow as O  # noqa: E402
from tools import c1nf_shadow as cs  # noqa: E402
from tools import test_c1nf_shadow as T  # noqa: E402

HOUR0 = "2026-11-17T00"
H0_MS = S.parse_hour(HOUR0)
STEP_MS = 2000       # one row per 2 s, 5 slots apart (0.4 s per slot)
SLOT0 = 1_000_000
V = 17_584_505_288
SPS = dict(sps_fn=lambda: 0.4)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(S, "MIN_WARMUP_H", 1)  # the real minimum is 26 h; fixtures stay small
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def hv7_row(i, t_ms, pool="P1"):
    """an arena_tape_v1 PumpSwap trade row as tools/arena_tip_adapter writes it (prog, no venue)"""
    return {"v": 1, "type": "trade", "prog": "pumpswap", "pool": pool, "mint": "M" + pool, "trader": "w%d" % (i % 5), "side": "buy" if i % 3 else "sell", "sol_lamports": 1_000_000_000,
            "token_raw": 10 ** 11, "quote_reserve": 60 * 10 ** 9 + i * 10 ** 8, "base_reserve": 10 ** 15 - i * 10 ** 11, "slot": SLOT0 + (t_ms - H0_MS) // 400, "tx_index": 1,
            "event_index": i, "signature": "sig%d" % i, "t_recv_ms": t_ms, "block_time": t_ms // 1000, "virtual_quote_reserves": V}


def hour_rows(hour_ms):
    return [hv7_row(hour_ms // STEP_MS + k, hour_ms + k * STEP_MS) for k in range(3_600_000 // STEP_MS)]


def write_hours(tape, first_ms, n):
    tape.mkdir(parents=True, exist_ok=True)
    for h in range(n):
        ms = first_ms + h * S.HOUR_MS
        (tape / ("tape-%s.jsonl" % S.hour_key(ms))).write_text("".join(json.dumps(r) + "\n" for r in hour_rows(ms)))


# ---------------------------------------------------------------------------------------------------------------------------------- adapter
def test_adapter_trade_maps_prog_to_venue_and_keeps_the_walker_fields():
    r = hv7_row(1, H0_MS)
    kind, out = S.arena_row_to_c1nf(r)
    assert kind == "trades" and out["venue"] == "pumpswap" and out is not r and "venue" not in r
    for k in ("pool", "mint", "trader", "side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "slot", "block_time", "virtual_quote_reserves"):
        assert out[k] == r[k]
    assert cs.event_v_of(out) == V
    r2 = dict(r, prog="pumpfun")
    assert S.arena_row_to_c1nf(r2)[1]["venue"] == "pump_bonding"
    assert S.arena_row_to_c1nf(dict(r, prog="other")) is None


def test_adapter_block_time_falls_back_to_event_ts_then_receive_time_and_missing_slot_is_dropped():
    r = hv7_row(1, 1_790_000_123_456)
    del r["block_time"]
    assert S.arena_row_to_c1nf(r)[1]["block_time"] == 1_790_000_123
    r["event_ts"] = 1_790_000_100
    assert S.arena_row_to_c1nf(r)[1]["block_time"] == 1_790_000_100
    del r["slot"]
    assert S.arena_row_to_c1nf(r) is None


def test_adapter_create_complete_pool_create_and_gaps():
    base = {"v": 1, "prog": "pumpfun", "slot": 5, "t_recv_ms": 5000, "block_time": 5, "mint": "M"}
    k, c = S.arena_row_to_c1nf(dict(base, type="create", creator="C", name="n", symbol="s", is_mayhem_mode=False, trader="x"))
    assert k == "creates" and c == {"mint": "M", "creator": "C", "name": "n", "symbol": "s", "slot": 5, "block_time": 5, "is_mayhem_mode": False}
    assert S.arena_row_to_c1nf(dict(base, type="complete")) == ("migrations", {"type": "complete", "mint": "M", "slot": 5, "block_time": 5})
    k, p = S.arena_row_to_c1nf(dict(base, type="pool_create", prog="pumpswap", pool="P"))
    assert k == "migrations" and p["pool"] == "P" and p["quote_mint"] == S.WSOL and p["type"] == "pool_create"
    k, g = S.arena_row_to_c1nf({"type": "gap", "prog": "all", "kind": "tip_gap_backlog_jump", "data_loss": True, "from_slot": 3, "to_slot": 9, "t_recv_ms": 1})
    assert k == "gap" and (g["slot_from"], g["slot_to"]) == (3, 9)
    assert S.arena_row_to_c1nf({"type": "gap", "kind": "slow", "data_loss": False, "slot": 4}) is None
    assert S.arena_row_to_c1nf({"type": "start", "slot": 1}) is None


# ---------------------------------------------------------------------------------------------------------------------------------- decider
def t_rows(first_s=0.0, last_s=1000.0):
    """arena rows on the C1-NF shadow test clock (block_time grows 0.4 s per slot), event V on every print"""
    out = []
    for slot in range(T.slot_of_sec(first_s), T.slot_of_sec(last_s)):
        r = T.mk_row(slot, side="buy" if slot % 2 else "sell")
        r.update(type="trade", prog="pumpswap", virtual_quote_reserves=int(T.V0), t_recv_ms=T.bt_of(slot) * 1000)
        del r["venue"], r["virtual_quote_reserve"]
        out.append(r)
    return out


def mk_decider(decide, wallet_ok=True, **kw):
    eng = T.WalletEngine(decide, wallet_ok)
    eng.v_source = "event"
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": T.__file__, "sha256": T.sha_of(T.__file__)}], loader=lambda p: T.StubModel(0.05))
    d = S.C1nfDecider(eng, models, **kw)
    d.shadow.clock.hour_sps[cs.hour_of(T.BT0)] = T.SPS
    d.shadow.clock.hour_sps[cs.hour_of(T.BT0 + 3600)] = T.SPS
    return d, eng


def test_decider_stamps_the_entry_at_the_row_that_crossed_the_minute_never_the_row_before():
    d, eng = mk_decider(T.only_minute(10))
    rows = t_rows()
    ents = d.decide(rows, S.RunState(hour="2026-09-04T12", warmup=False, day="2026-09-04"))
    assert len(ents) == 1
    e = ents[0]
    assert e.pool == T.POOL and e.exit_after_s == S.EXIT_S_DEFAULT and 0 < e.at_row < len(rows)
    cross = rows[e.at_row]
    assert rows[e.at_row - 1]["block_time"] // 60 * 60 < T.BT0 + 600 <= cross["block_time"]  # the row that crossed into minute 10
    assert (e.decision_ms, e.decision_slot) == (cross["t_recv_ms"], cross["slot"]) and e.meta["SD_slot"] == cross["slot"]
    assert e.decision_ms > rows[e.at_row - 1]["t_recv_ms"]
    assert e.meta["pred"] == 0.05 and e.meta["h_top1"] == 0.1


def test_a_pick_in_the_first_minute_of_the_first_forward_hour_is_stamped_at_or_after_s():
    d, _ = mk_decider(T.only_minute(10))
    assert d.decide(t_rows(0.0, 600.0), S.RunState(hour="w", warmup=True, day="d")) == []
    ents = d.decide(t_rows(600.0, 700.0), S.RunState(hour="f", warmup=False, day="d"))  # the first forward row IS the crossing row
    assert len(ents) == 1 and ents[0].at_row == 0 and ents[0].decision_ms >= (T.BT0 + 600) * 1000


def test_warmup_hours_decide_nothing_and_run_no_outcome_path():
    d, eng = mk_decider(T.only_minute(10))
    st = S.RunState(hour="2026-09-04T12", warmup=True, day="2026-09-04")
    assert d.decide(t_rows(), st) == []
    assert eng.asked == [] and eng.blocks and d.shadow.pending == {}  # rows fed (engine warm), no feature call, no pending outcome
    assert not st.counts.get("picks")
    # the first forward call turns decisions on
    d2 = d.decide(t_rows(1000.0, 1100.0), S.RunState(hour="2026-09-04T12", warmup=False, day="2026-09-04"))
    assert d.shadow.decide_enabled and isinstance(d2, list)


def test_missing_wallet_ledger_is_counted_as_a_refusal_and_no_pick_is_faked():
    d, eng = mk_decider(T.only_minute(10), wallet_ok=False)
    st = S.RunState(hour="2026-09-04T12", warmup=False, day="2026-09-04")
    assert d.decide(t_rows(), st) == []
    assert st.counts["no_wallet_ledger"] == 1 and not st.counts.get("picks")


def test_decider_outcome_path_is_off_even_for_a_pick():
    d, _ = mk_decider(T.only_minute(10))
    d.decide(t_rows(), S.RunState(hour="2026-09-04T12", warmup=False, day="2026-09-04"))
    assert d.shadow.pending == {} and d.sink.of("c1nf_outcome") == []


def test_oracle_bypass_flag_sets_the_seal_start_off():
    assert mk_decider(T.only_minute(10), oracle_bypass=True)[0].shadow.seal.start_ms is None
    assert mk_decider(T.only_minute(10))[0].shadow.seal.start_ms == cs.SEAL_START_MS


# ---------------------------------------------------------------------------------------------------------------------------------- parity proof (ruling 2.8 (a))
def picks_md5(picks):
    import hashlib
    keep = [{k: v for k, v in p.items() if k not in ("t_ms",)} for p in picks]
    return hashlib.md5(S.canon(keep).encode()).hexdigest()


def test_parity_decisions_equal_the_tiptail_shadow_path_on_a_synthetic_tip_tape():
    """the same decisions (md5 of the pick records) from (a) a Shadow fed the raw tip rows, as TipTail yields them, and (b) the runner's decider fed the SAME rows after the pinned
    arena_tip_adapter.trade_row conversion. Synthetic stub engine and model; the real-layout dry run on keep hours is the manager's (ruling 2.8 (c))."""
    from tools import arena_tip_adapter as A
    raw = []
    for slot in range(T.slot_of_sec(0), T.slot_of_sec(1500)):
        w = T.mk_row(slot, side="buy" if slot % 2 else "sell")
        w.update(virtual_quote_reserves=int(T.V0), t_recv_ms=T.bt_of(slot) * 1000)
        for k in ("sol_lamports", "token_raw", "quote_reserve", "base_reserve"):
            w[k] = int(w[k])
        w["signature"] = "sig%d" % slot  # the tip's cached singular `virtual_quote_reserve` stays on the raw row: the pinned adapter drops it, event mode never reads it
        raw.append(w)
    # (a) shadow path on the raw rows
    eng_a = T.WalletEngine(T.only_minute(10, 20), True)
    eng_a.v_source = "event"
    sink = cs.MemorySink()
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": T.__file__, "sha256": T.sha_of(T.__file__)}], loader=lambda p: T.StubModel(0.05))
    sh = cs.Shadow(eng_a, models, sink, replay=False, wall=lambda: 0, seal_start_ms=None)
    sh.clock.hour_sps[cs.hour_of(T.BT0)] = T.SPS
    for w in raw:
        sh.feed(w, "trades")
    # (b) the runner's decider on the adapter's arena rows
    arena = []
    for w in raw:
        a = A.trade_row(w)
        a["prog"], a["type"] = "pumpswap", "trade"
        arena.append(a)
    assert all("virtual_quote_reserve" not in a for a in arena) and all(a["block_time"] == w["block_time"] for a, w in zip(arena, raw))
    d, _ = mk_decider(T.only_minute(10, 20), oracle_bypass=True)
    ents = d.decide(arena, S.RunState(hour="2026-09-04T12", warmup=False, day="2026-09-04"))
    picks_b = [{"pool": e.pool, "decision_T_ms": e.meta["decision_T_ms"], "SD_slot": e.meta["SD_slot"], "pred": e.meta["pred"], "feature_hash": e.meta["feature_hash"], "model_sha": e.meta["model_sha"]}
               for e in ents]
    picks_a = [{k: p[k] for k in ("pool", "decision_T_ms", "SD_slot", "pred", "feature_hash", "model_sha")} for p in sink.of("c1nf_pick")]
    assert len(picks_a) == 2 and picks_md5(picks_a) == picks_md5(picks_b)


# ---------------------------------------------------------------------------------------------------------------------------------- manifest, seal, paths
def manifest(tmp, **kw):
    m = {"book_id": "t-book", "title": "t", "decider": "c1nf", "stakes_sol": [0.1, 0.25, 0.5, 1.0], "send_lamports": 505000, "start_hour": HOUR0, "warmup_hours": 1, "tape_dir": str(tmp / "keep"),
         "out_root": str(tmp / "sim" / "t-book")}
    m.update(kw)
    return m


def roots(tmp):
    return (str(tmp / "keep"), str(tmp / "sim"))


ORACLE = "none: EXP-022 withdrawn 2026-10-15, DEC-031 section 3 item 5"


def test_pick_oracle_skip_is_a_manifest_key_valid_only_from_s(tmp_path):
    with pytest.raises(S.Refused):
        S.check_seal(manifest(tmp_path, start_hour="2026-10-16T00", pick_oracle=ORACLE))
    assert S.follow(manifest(tmp_path, start_hour="2026-10-15T23", pick_oracle=ORACLE), fixture=True, once=True, _roots=roots(tmp_path), decider_factory=lambda m: None) == 2
    S.check_seal(manifest(tmp_path, start_hour="2026-10-16T01", pick_oracle=ORACLE))
    S.check_seal(manifest(tmp_path))  # no key: nothing to refuse (the shadow keeps its seal and withholds)
    with pytest.raises(S.Refused):
        S.check_manifest(manifest(tmp_path, pick_oracle="yes"))
    with pytest.raises(S.Refused):
        S.check_manifest(manifest(tmp_path, oracle_bypass_forward=True))  # the old flag is gone


def test_there_is_no_oracle_or_no_seal_cli_flag():
    with pytest.raises(SystemExit):
        S.main(["x.json", "--oracle-bypass-forward"])
    with pytest.raises(SystemExit):
        S.main(["x.json", "--no-seal"])


def test_warmup_window_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MIN_WARMUP_H", 26)
    m = manifest(tmp_path, start_hour="2026-11-20T00")
    del m["warmup_hours"]
    warm, start = S.hours_needed(m)
    assert len(warm) == 336 and warm[-1] == "2026-11-19T23" and warm[0] == "2026-11-06T00"  # S - 336 h
    m["start_hour"] = "2026-11-10T00"  # 2026-11-03T03 is later than S - 336 h
    warm, _ = S.hours_needed(m)
    assert warm[0] == "2026-11-03T03" and len(warm) == 6 * 24 + 21
    m["start_hour"] = "2026-11-04T00"  # 21 h available < 26: S moves later
    with pytest.raises(S.Refused, match="S moves later"):
        S.hours_needed(m)
    with pytest.raises(S.Refused):
        S.check_manifest(manifest(tmp_path, warmup_hours=400))


def test_manifest_checks(tmp_path):
    for bad in ({"decider": "x"}, {"stakes_sol": [0.5, 0.25]}, {"stakes_sol": [3.0]}, {"pricing_lat_entry_s": 2.0}, {"warmup_hours": 0}, {"start_hour": "x"}, {"bogus": 1}, {"exit_s": 0},
                {"send_lamports": 55000}):
        with pytest.raises(S.Refused):
            S.check_manifest(manifest(tmp_path, **bad))
    m = S.check_manifest(manifest(tmp_path))
    assert m["pricing_lat_entry_s"] == 4.085  # 19 slots deciding
    for lat in (3.010, 5.590):
        S.check_manifest(manifest(tmp_path, pricing_lat_entry_s=lat))
    S.check_manifest(manifest(tmp_path, send_lamports=160000))


def test_path_guard(tmp_path):
    def f(**kw):
        return S.follow(manifest(tmp_path, **kw), fixture=True, once=True, _roots=roots(tmp_path), decider_factory=lambda m: None)
    assert f(tape_dir=str(tmp_path / "elsewhere")) == 2
    assert f(out_root=str(tmp_path / "keep" / "x")) == 2
    assert f(out_root=str(tmp_path / "other")) == 2
    for bad in ("/var/lib/mal/sealed/fast-trades-tip", "/data2/mal/arena-tip-feed/hv7", "/data2/mal/tip-tape-archive", "/home/claude/mal-data/arena-mirror/tape", "/data2/mal/fast0-offload-1113",
                "/data/mal/blocks/forward-1016", "/data/mal/r4b", "/data/mal/meta-capture"):
        assert S.follow(manifest(tmp_path, tape_dir=bad), fixture=True, once=True, decider_factory=lambda m: None) == 2  # default roots
        assert S.follow(manifest(tmp_path, out_root=str(tmp_path / "sim") + bad), fixture=True, once=True, _roots=roots(tmp_path), decider_factory=lambda m: None) == 2


def test_default_roots_are_the_keeper_and_the_c1nf_open_sim_root():
    assert S.KEEP_ROOT == "/data2/mal/scratch/floor-open-sim/keep" and S.SIM_ROOT == "/data2/mal/scratch/c1nf-open-sim"


def test_sample_manifest_is_valid():
    m = json.loads((Path(S.__file__).parent / "manifests-sample" / "c1nf-fwd-sample.manifest.json").read_text())
    S.check_manifest(dict(m, warmup_hours=1))
    assert m["tape_dir"] == S.KEEP_ROOT and m["out_root"].startswith(S.SIM_ROOT) and m["send_lamports"] == 505000 and m["pricing_lat_entry_s"] == 4.085
    S.check_seal(m)


# ---------------------------------------------------------------------------------------------------------------------------------- forward ledger
def test_ledger_step_builds_a_day_only_when_all_24_hours_were_processed(tmp_path):
    built = []
    ls = S.LedgerStep(tmp_path / "src", tmp_path / "wl", builder=lambda day, src, root: built.append((day, sorted(p.name for p in Path(src).iterdir()), str(root))))
    for h in range(24):  # a complete day
        ls.note_hour("2026-11-16T%02d" % h, [{"trader": "w"}])
    assert [b[0] for b in built] == ["2026-11-16"] and len(built[0][1]) == 24 and ls.incomplete == []
    for h in range(2, 24):  # a day that starts 2 h late: not built, counted
        ls.note_hour("2026-11-17T%02d" % h, [])
    assert [b[0] for b in built] == ["2026-11-16"] and ls.incomplete == ["2026-11-17"]


# ---------------------------------------------------------------------------------------------------------------------------------- runner
class StubDecider:
    """entry at row `at` of the first forward hour on pool P1; records what it was asked"""

    def __init__(self, at=100):
        self.at, self.calls = at, []

    def decide(self, rows, state):

        rows = list(rows)  # the runner streams: a decider gets an iterator
        self.calls.append((state.hour, state.warmup, len(rows)))
        if state.warmup or state.hour != HOUR0:
            return []
        prev = rows[self.at - 1]
        return [S.Entry(pool="P1", mint="MP1", at_row=self.at, decision_ms=prev["t_recv_ms"], decision_slot=prev["slot"], exit_after_s=300.0, meta={"pred": 0.05})]


def run(tmp, m, dec, **kw):
    kw.setdefault("engine_kw", SPS)
    return S.follow(m, fixture=True, once=True, _roots=roots(tmp), decider_factory=lambda mm: dec, ledger_builder=lambda *a: None, **kw)


def ledger_rows(out_root):
    rows = []
    for f in sorted(Path(out_root, "book", "ledger").rglob("trades-*.jsonl")):
        rows += [json.loads(ln) for ln in f.read_text().splitlines()]
    return rows


def write_keep(tmp, n_forward, warm=1):
    write_hours(tmp / "keep", H0_MS - warm * S.HOUR_MS, warm + n_forward)


def test_entry_is_priced_through_the_arena_engine_and_exits_at_landing_plus_300_s(tmp_path):
    write_keep(tmp_path, 3)
    m = manifest(tmp_path)
    dec = StubDecider()
    assert run(tmp_path, m, dec) == 0
    rows = ledger_rows(m["out_root"])
    kinds = collections.Counter(r["kind"] for r in rows)
    assert kinds["open"] == 1 and kinds["trade"] == 4 and kinds.get("refused", 0) == 0  # one position, one trade row per stake
    assert sorted(r["stake_sol"] for r in rows if r["kind"] == "trade") == [0.1, 0.25, 0.5, 1.0]
    op = next(r for r in rows if r["kind"] == "open")
    tr = next(r for r in rows if r["kind"] == "trade")
    assert tr["decision_ms"]["exit"] - tr["decision_ms"]["entry"] == 300_000 + 4085 and op["decision_ms"]["entry"] == tr["decision_ms"]["entry"]
    assert op["meta"] == {"pred": 0.05} and tr["stack"]["send"] == 505000 and tr["stack"]["pricing_lat_entry_s"] == 4.085
    assert tr["v_src"]["entry"] == "row" and tr["v_src"]["exit"] == "row"  # the print's own event V, never a cached or fallback V
    assert set(tr["legs"]["total"]) == {"flat_1p3", "flat_1p9", "press_1p3", "press_1p9"}  # both fail models are on every row
    assert [c[0] for c in dec.calls] == ["2026-11-16T23", HOUR0, "2026-11-17T01"]  # the third file only closes hour 01


def test_latency_twin_and_fee_beside_reach_the_priced_rows(tmp_path):
    write_keep(tmp_path, 2)
    m = manifest(tmp_path, pricing_lat_entry_s=5.590, send_lamports=160000)
    assert run(tmp_path, m, StubDecider()) == 0
    rows = ledger_rows(m["out_root"])
    tr = next(r for r in rows if r["kind"] == "trade")
    assert tr["stack"]["pricing_lat_entry_s"] == 5.590 and tr["stack"]["send"] == 160000
    assert next(r for r in rows if r["kind"] == "trade")["decision_ms"]["exit"] - next(r for r in rows if r["kind"] == "open")["decision_ms"]["entry"] == 300_000 + 5590


def test_warmup_hours_are_listed_feed_the_decider_and_write_no_ledger_rows(tmp_path):
    write_keep(tmp_path, 3, warm=2)
    m = manifest(tmp_path, warmup_hours=2)
    dec = StubDecider()
    assert run(tmp_path, m, dec) == 0
    assert [(c[0], c[1]) for c in dec.calls] == [("2026-11-16T22", True), ("2026-11-16T23", True), (HOUR0, False), ("2026-11-17T01", False)]
    meta = json.loads(Path(m["out_root"], "meta.json").read_text())
    assert meta["warmup_hours"] == ["2026-11-16T22", "2026-11-16T23"] and meta["first_processed_hour"] == "2026-11-16T22"
    assert meta["disclosure"].startswith("warm-up W = [2026-11-16T22, S), decisions off, no outcome code path") and "pools first seen in warm-up: 1" in meta["disclosure"]
    stored = [json.loads(ln) for ln in Path(m["out_root"], "md5-by-hour.jsonl").read_text().splitlines()]
    assert [(s["hour"], s["warmup"]) for s in stored] == [("2026-11-16T22", True), ("2026-11-16T23", True), (HOUR0, False), ("2026-11-17T01", False)]
    counts = [json.loads(ln) for ln in Path(m["out_root"], "counts.jsonl").read_text().splitlines()]
    assert counts[0]["decider"]["rows"] == 1800 and counts[0]["book"] == {}  # warm-up hour: counts only, nothing priced
    assert all(r["decision_ms"]["entry"] >= H0_MS for r in ledger_rows(m["out_root"]) if r["kind"] == "open")


def test_a_decider_that_returns_entries_in_a_warmup_hour_is_refused(tmp_path):
    write_keep(tmp_path, 2)

    class Bad:
        def decide(self, rows, state):
            rows = list(rows)  # the runner streams: a decider gets an iterator
            return [S.Entry("P1", "MP1", 5, rows[4]["t_recv_ms"], rows[4]["slot"])]
    assert run(tmp_path, manifest(tmp_path), Bad()) == 2


def test_an_entry_decided_before_s_is_refused(tmp_path):
    write_keep(tmp_path, 2)

    class Early:
        def decide(self, rows, state):
            rows = list(rows)  # the runner streams: a decider gets an iterator
            if state.warmup or state.hour != HOUR0:
                return []
            return [S.Entry("P1", "MP1", 5, H0_MS - 1000, rows[4]["slot"])]
    assert run(tmp_path, manifest(tmp_path), Early()) == 2


def test_the_runner_reads_through_symlinks_under_its_own_root(tmp_path):
    write_keep(tmp_path, 2)
    m = manifest(tmp_path)
    assert run(tmp_path, m, StubDecider()) == 0
    links = list(Path(m["out_root"], "tapelink").iterdir())
    assert len(links) == 3 and all(p.is_symlink() and str(p.resolve()).startswith(str((tmp_path / "keep").resolve())) for p in links)


def test_ledger_builder_sees_warmup_and_forward_hours_of_complete_days_only(tmp_path):
    write_hours(tmp_path / "keep", S.parse_hour("2026-11-16T00"), 26)  # all of 11-16, 11-17T00 and T01
    built = []
    m = manifest(tmp_path, warmup_hours=24)
    assert S.follow(m, fixture=True, once=True, _roots=roots(tmp_path), decider_factory=lambda mm: StubDecider(), ledger_builder=lambda d, s, r: built.append(d), engine_kw=SPS) == 0
    assert built == ["2026-11-16"]  # 24 warm-up hours = a complete past day, declared in meta.json; 11-17 is still open


def test_not_closed_hour_is_not_processed(tmp_path):
    write_hours(tmp_path / "keep", H0_MS - S.HOUR_MS, 1)  # the warm-up hour has no successor file yet
    m = manifest(tmp_path)
    dec = StubDecider()
    assert run(tmp_path, m, dec) == 0 and dec.calls == []


def test_book_files_have_osb_follows_layout_and_the_board_readers_work(tmp_path):
    write_keep(tmp_path, 3)
    m = manifest(tmp_path)
    assert run(tmp_path, m, StubDecider()) == 0
    out = Path(m["out_root"])
    for f in ("manifest.sha256", "code.sha256", "status.json", "md5-by-hour.jsonl", "decisions.md5", "meta.json", "counts.jsonl"):
        assert (out / f).is_file(), f
    assert (out / "book").is_symlink() and (out / "book").resolve() == (out / "runs" / "0001").resolve()
    run_dir = out / "runs" / "0001"
    for f in ("bots.json", "variant.json"):
        assert (run_dir / f).is_file()
    assert list((run_dir / "ledger").glob("*/trades-2026-11-17.jsonl"))
    assert (out / "manifest.sha256").read_text().strip() == S.manifest_sha256(S.check_manifest(dict(m)))
    st = json.loads((out / "status.json").read_text())
    assert {"book_id", "manifest_sha256", "code_sha256", "run", "stakes_sol", "last_hour", "last_hour_end_ms", "active", "wall_time_utc"} <= set(st)
    assert st["stakes_sol"] == [0.1, 0.25, 0.5, 1.0] and st["last_hour"] == "2026-11-17T01" and st["active"] is True
    stored = [json.loads(ln) for ln in (out / "md5-by-hour.jsonl").read_text().splitlines()]
    assert all({"hour", "end_ms", "decision_md5", "ledger_md5"} <= set(s) for s in stored)
    assert re.fullmatch(r"[0-9a-f]{32}", (out / "decisions.md5").read_text().strip())
    assert O.book_decisions_md5(out) == (out / "decisions.md5").read_text().strip()  # the same definition osb_board / osb_recon read
    assert O.read_stored(out).keys() == {s["hour"] for s in stored}


def test_restart_replays_into_a_new_run_and_checks_every_stored_hour(tmp_path):
    write_keep(tmp_path, 3)
    m = manifest(tmp_path)
    assert run(tmp_path, m, StubDecider()) == 0
    assert run(tmp_path, m, StubDecider()) == 0
    out = Path(m["out_root"])
    assert (out / "book").resolve() == (out / "runs" / "0002").resolve()
    assert run(tmp_path, m, StubDecider(at=101)) == 4  # another decision: md5 mismatch, `book` untouched
    assert (out / "book").resolve() == (out / "runs" / "0002").resolve()


def test_changed_manifest_is_a_new_book(tmp_path):
    write_keep(tmp_path, 2)
    assert run(tmp_path, manifest(tmp_path), StubDecider()) == 0
    assert run(tmp_path, manifest(tmp_path, exit_s=299), StubDecider()) == 2


def test_a_pick_at_minute_s_does_not_kill_the_runner_and_a_restart_repeats_it(tmp_path):
    write_keep(tmp_path, 3)

    class AtS(StubDecider):
        def decide(self, rows, state):
            rows = list(rows)  # the runner streams: a decider gets an iterator
            self.calls.append((state.hour, state.warmup, len(rows)))
            if state.warmup or state.hour != HOUR0:
                return []
            return [S.Entry(pool="P1", mint="MP1", at_row=0, decision_ms=rows[0]["t_recv_ms"], decision_slot=rows[0]["slot"])]
    m = manifest(tmp_path)
    assert run(tmp_path, m, AtS()) == 0
    assert run(tmp_path, m, AtS()) == 0  # the restart replays the same hour without the same exit
    rows = ledger_rows(m["out_root"])  # the pricing engine has no prints yet at row 0 of S: a counted, logged refusal (no_prints), not a crash and not an entry before S
    assert [r["reason"] for r in rows if r["kind"] == "refused"] == ["no_prints"] and all(r["decision_ms"]["entry"] >= H0_MS for r in rows)


def test_a_decision_before_s_that_reaches_the_book_stays_fatal(tmp_path):
    write_keep(tmp_path, 2)

    class Early(StubDecider):
        def decide(self, rows, state):
            rows = list(rows)  # the runner streams: a decider gets an iterator
            return [S.Entry("P1", "MP1", 0, H0_MS - 1, rows[0]["slot"])] if state.hour == HOUR0 else []
    assert run(tmp_path, manifest(tmp_path), Early()) == 2


def test_duckdb_pin_key_is_checked_at_start(tmp_path):
    write_keep(tmp_path, 2)
    pin = {"version": "1.5.6", "sha256": "ab" * 32}
    S._DUCKDB_PROBE[0] = lambda: ("1.5.6", "ab" * 32)
    try:
        assert run(tmp_path, manifest(tmp_path, duckdb_pin=pin), StubDecider()) == 0
        S._DUCKDB_PROBE[0] = lambda: ("1.5.7", "ab" * 32)
        assert run(tmp_path, manifest(tmp_path, duckdb_pin=pin, book_id="t2", out_root=str(tmp_path / "sim" / "t2")), StubDecider()) == 2
        S._DUCKDB_PROBE[0] = lambda: ("1.5.6", "cd" * 32)
        assert run(tmp_path, manifest(tmp_path, duckdb_pin=pin, book_id="t3", out_root=str(tmp_path / "sim" / "t3")), StubDecider()) == 2
    finally:
        S._DUCKDB_PROBE[0] = None
    for bad in ({"version": "1"}, {"version": "1", "sha256": "xx"}, "1.5.6"):
        with pytest.raises(S.Refused):
            S.check_manifest(manifest(tmp_path, duckdb_pin=bad))


def test_an_hour_file_that_resolves_outside_the_keeper_dir_is_refused(tmp_path):
    write_hours(tmp_path / "elsewhere", H0_MS - S.HOUR_MS, 2)
    keep = tmp_path / "keep"
    keep.mkdir()
    for f in (tmp_path / "elsewhere").iterdir():
        (keep / f.name).symlink_to(f)  # a symlink, not a hard link
    assert run(tmp_path, manifest(tmp_path), StubDecider()) == 2
    assert not Path(manifest(tmp_path)["out_root"], "tapelink").exists() or not list(Path(manifest(tmp_path)["out_root"], "tapelink").iterdir())


# ---------------------------------------------------------------------------------------------------------------------------------- pre-S guards (follow-up to #1415)
def prod(tmp, **kw):
    """a production-shaped manifest: both pins present (model manifest = the repo's pinned one)"""
    import hashlib
    mm = S.MODEL_DIR / "manifest.json"
    return manifest(tmp, duckdb_pin={"version": "1.5.6", "sha256": "ab" * 32}, model_manifest_sha256=hashlib.sha256(mm.read_bytes()).hexdigest(), **kw)


def test_production_manifest_requires_the_duckdb_pin_and_the_model_manifest_pin(tmp_path):
    for drop in ("duckdb_pin", "model_manifest_sha256"):
        m = prod(tmp_path)
        del m[drop]
        with pytest.raises(S.Refused, match=drop):
            S.check_production_pins(m)
        S.check_production_pins(m, fixture=True)  # the fixture seam may skip the requirement
    S.check_production_pins(prod(tmp_path))
    assert S.follow(manifest(tmp_path), once=True, _roots=roots(tmp_path), decider_factory=lambda m: None) == 2  # a production start without pins is refused
    sample = json.loads((Path(S.__file__).parent / "manifests-sample" / "c1nf-fwd-sample.manifest.json").read_text())
    with pytest.raises(S.Refused):
        S.check_production_pins(sample)  # the sample carries no pins on purpose


def test_model_manifest_sha_mismatch_and_path_are_refused(tmp_path):
    import hashlib
    m = prod(tmp_path)
    m["model_manifest_sha256"] = "0" * 64
    with pytest.raises(S.Refused, match="pinned"):
        S.check_production_pins(m)
    other = tmp_path / "manifest.json"
    other.write_text("{}")
    m2 = prod(tmp_path, model_manifest=str(other))
    m2["model_manifest_sha256"] = hashlib.sha256(b"{}").hexdigest()
    with pytest.raises(S.Refused, match="inside"):
        S.check_production_pins(m2)  # right sha, wrong place
    S.check_production_pins(m2, fixture=True)


def test_wallet_ledger_root_must_sit_under_out_root(tmp_path):
    out = tmp_path / "sim" / "t-book"
    S.check_production_pins(prod(tmp_path, wallet_ledger_root=str(out / "wallet-ledger")))
    for bad in (str(tmp_path / "elsewhere"), str(out / ".." / "other"), str(out / "r4b-ledger")):
        with pytest.raises(S.Refused):
            S.check_production_pins(prod(tmp_path, wallet_ledger_root=bad))


def test_rent_mode_defaults_to_stranded_and_reaches_the_rows(tmp_path):
    write_keep(tmp_path, 2)
    m = manifest(tmp_path)
    assert run(tmp_path, m, StubDecider()) == 0
    tr = next(r for r in ledger_rows(m["out_root"]) if r["kind"] == "trade")
    assert tr["stack"]["rent"] == 2_039_280
    m2 = manifest(tmp_path, rent_mode="refunded", book_id="t2", out_root=str(tmp_path / "sim" / "t2"))
    assert run(tmp_path, m2, StubDecider()) == 0
    assert next(r for r in ledger_rows(m2["out_root"]) if r["kind"] == "trade")["stack"]["rent"] == 0
    with pytest.raises(S.Refused):
        S.check_manifest(manifest(tmp_path, rent_mode="x"))


def test_wallet_ledger_md5_per_day_and_twin_comparison(tmp_path):
    first = S.parse_hour("2026-11-16T00")
    outs = []
    for i, content in enumerate(("same", "same", "other")):
        d = tmp_path / ("t%d" % i)
        write_hours(d / "keep", first, 26)
        m = manifest(d, warmup_hours=24, tape_dir=str(d / "keep"), out_root=str(d / "sim" / "b"))
        m["wallet_ledger_root"] = str(d / "sim" / "b" / "wallet-ledger")

        def builder(day, src, root, c=content):
            Path(root).mkdir(parents=True, exist_ok=True)
            (Path(root) / ("asof-%s.txt" % day)).write_text(c)
        assert S.follow(m, fixture=True, once=True, _roots=(str(d / "keep"), str(d / "sim")), decider_factory=lambda mm: StubDecider(), ledger_builder=builder, engine_kw=SPS) == 0
        outs.append(d / "sim" / "b")
    lines = [json.loads(ln) for ln in (outs[0] / "wallet-ledger-md5.jsonl").read_text().splitlines()]
    assert [r["day"] for r in lines] == ["2026-11-16"] and re.fullmatch(r"[0-9a-f]{32}", lines[0]["md5"])
    ok = S.compare_books(outs[0], outs[1])
    assert ok["decision_md5_first_mismatch"] is None and ok["wallet_md5_first_mismatch"] is None and ok["days_compared"] == 1 and ok["hours_compared"] >= 2
    bad = S.compare_books(outs[0], outs[2])
    assert bad["wallet_md5_first_mismatch"] == "2026-11-16" and bad["decision_md5_first_mismatch"] is None
    rows = [json.loads(ln) for ln in (outs[2] / "md5-by-hour.jsonl").read_text().splitlines()]
    for r in rows:
        if r["hour"] >= "2026-11-17T00":
            r["decision_md5"] = "x"
    (outs[2] / "md5-by-hour.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert S.compare_books(outs[0], outs[2])["decision_md5_first_mismatch"] == "2026-11-17T00"


def test_feature_overlap_file_is_the_generated_one_and_lists_names_only():
    import c1nf_feature_overlap as FO
    text = (S.REPO / "ARTIFACTS" / "c1nf_model" / "feature_overlap_r4b.txt").read_text()
    assert text == FO.render()
    assert "c1nf_features: 107" in text and "r4b_features: 63" in text
    assert FO.render(["a", "b", "c"], ["b", "z"]).split("## in both")[1].startswith("\nb\n")
    assert "r4b_subset_of_c1nf: yes" in FO.render(["a", "b"], ["b"])


def test_a_real_zst_file_is_read_through_a_real_symlink_with_the_zstd_cli(tmp_path):
    """no fakes: the keeper's .jsonl.zst made by the zstd CLI, the runner's tapelink symlink to it, read_hour_rows on the link (zstd itself refuses a symlink: rc 1)"""
    import shutil
    import subprocess
    if shutil.which("zstd") is None:
        pytest.skip("no zstd CLI")
    keep = tmp_path / "keep"
    keep.mkdir()
    plain = tmp_path / "x.jsonl"
    rows = hour_rows(H0_MS)[:50]
    plain.write_text("".join(json.dumps(r) + "\n" for r in rows))
    z = keep / ("tape-%s.jsonl.zst" % HOUR0)
    subprocess.run(["zstd", "-q", str(plain), "-o", str(z)], check=True)
    link = tmp_path / "link" / z.name
    link.parent.mkdir()
    link.symlink_to(z)
    assert subprocess.run(["zstd", "-t", str(link)], capture_output=True).returncode != 0  # the failure this guards against
    assert S.read_hour_rows(link) == rows
    # and through the runner's own link_hour (guard first, then the read)
    m = manifest(tmp_path)
    got = S.link_hour(m, tmp_path / "out", H0_MS)
    assert got.is_symlink() and S.read_hour_rows(got) == rows


# ---------------------------------------------------------------------------------------------------------------------------------- memory fix (job #2912 OOM): streaming, bounded memory, old-vs-new equality
# GOLDEN values: produced by the pre-fix code (main 1d7695c7, whole-hour lists) on exactly these fixtures; the streaming code must reproduce them (QP ADD1 (c)1: decision md5, wallet-ledger md5, book ledger).
GOLD_DECIDER_ENTRIES_MD5 = "8c2fd85a59dd283771df65cc4191502e"
GOLD_RUN_HOURS = {"2026-11-16T23": "d751713988987e9331980363e24189ce", HOUR0: "92c8493f3d2c959af21425ec976d05e4", "2026-11-17T01": "d751713988987e9331980363e24189ce"}
GOLD_RUN_DECISIONS_MD5, GOLD_RUN_TRADE_MD5 = "c7174b4aa2cface8569618b48862954d", "c65bc93c7436b71bd404fd9f2a585080"
GOLD_MULTI = {"hour_md5s": "459eb481586a3288b1d80ac75f05e568", "wallet": [("2026-11-16", "2026-11-16T23", "0118f94670ff0e7f65ba0ce40a90c168")], "decisions": "d751713988987e9331980363e24189ce",
              "ledger": "a40fe17099f8eeae795cfaf7bcabbc7b"}
TIP_FIELDS = ("venue", "mint", "trader", "side", "sol_lamports", "signature", "event_index")


def test_old_vs_new_decider_entries_md5_is_unchanged():
    import hashlib
    d, _ = mk_decider(T.only_minute(10, 20))
    ents = d.decide(iter(t_rows(0.0, 1500.0)), S.RunState(hour="2026-09-04T12", warmup=False, day="2026-09-04"))  # an iterator, as the runner streams
    assert len(ents) == 2 and hashlib.md5(S.canon([e.as_dict() for e in ents]).encode()).hexdigest() == GOLD_DECIDER_ENTRIES_MD5


def test_old_vs_new_runner_decision_and_book_ledger_md5_on_the_fixture_hours(tmp_path):
    import hashlib
    write_keep(tmp_path, 3)
    m = manifest(tmp_path)
    assert run(tmp_path, m, StubDecider()) == 0
    out = Path(m["out_root"])
    got = {r["hour"]: r["decision_md5"] for r in map(json.loads, (out / "md5-by-hour.jsonl").read_text().splitlines())}
    assert got == GOLD_RUN_HOURS and (out / "decisions.md5").read_text().strip() == GOLD_RUN_DECISIONS_MD5
    tr = sorted([(r["stake_sol"], r["legs"], r["decision_ms"]) for r in ledger_rows(m["out_root"]) if r["kind"] == "trade"], key=lambda t: t[0])
    assert hashlib.md5(S.canon(tr).encode()).hexdigest() == GOLD_RUN_TRADE_MD5


def test_old_vs_new_multi_hour_day_boundary_minute_s_pick_wallet_ledger_and_book_ledger_md5(tmp_path):
    import hashlib
    write_hours(tmp_path / "keep", S.parse_hour("2026-11-16T00"), 27)

    def builder(day, src, root):  # depends only on the columns the real tip reader selects, so old (all fields) and new (TIP_FIELDS only) scratch rows give the same ledger
        h = hashlib.md5()
        for f in sorted(Path(src).iterdir()):
            for ln in f.read_text().splitlines():
                r = json.loads(ln)
                h.update(json.dumps({k: r.get(k) for k in TIP_FIELDS}, sort_keys=True).encode())
        Path(root).mkdir(parents=True, exist_ok=True)
        (Path(root) / ("asof-%s.txt" % day)).write_text(h.hexdigest())

    class AtS(StubDecider):
        def decide(self, rows, state):
            rows = list(rows)
            if state.warmup or state.hour != HOUR0:
                return []
            return [S.Entry(pool="P1", mint="MP1", at_row=0, decision_ms=rows[0]["t_recv_ms"], decision_slot=rows[0]["slot"])]  # a pick at minute S
    m = manifest(tmp_path, warmup_hours=24)
    m["wallet_ledger_root"] = str(Path(m["out_root"]) / "wallet-ledger")
    assert S.follow(m, fixture=True, once=True, _roots=roots(tmp_path), decider_factory=lambda mm: AtS(), ledger_builder=builder, engine_kw=SPS) == 0
    out = Path(m["out_root"])
    assert hashlib.md5("".join(r["decision_md5"] for r in map(json.loads, (out / "md5-by-hour.jsonl").read_text().splitlines())).encode()).hexdigest() == GOLD_MULTI["hour_md5s"]
    assert [(r["day"], r["hour"], r["md5"]) for r in map(json.loads, (out / "wallet-ledger-md5.jsonl").read_text().splitlines())] == GOLD_MULTI["wallet"]
    assert (out / "decisions.md5").read_text().strip() == GOLD_MULTI["decisions"]
    rows = [{k: v for k, v in r.items() if k not in ("code_head", "engine_id", "frozen_sha")} for r in ledger_rows(m["out_root"]) if r["kind"] != "start"]  # code_head and frozen_sha are the runner file's own hash
    assert hashlib.md5(S.canon(sorted(rows, key=lambda r: r["trade_id"])).encode()).hexdigest() == GOLD_MULTI["ledger"]
    assert not (out / "ledger-src" / "2026-11-16").exists()  # a built day's scratch rows are released


def test_scratch_rows_hold_only_the_tip_columns_and_incomplete_days_are_released_too(tmp_path):
    seen = {}

    def builder(day, src, root):
        seen["cols"] = {k for f in Path(src).iterdir() for ln in f.read_text().splitlines() for k in json.loads(ln)}
    ls = S.LedgerStep(tmp_path / "src", tmp_path / "wl", builder=builder)
    for h in range(24):
        ls.note_hour("2026-11-16T%02d" % h, [dict(hv7_row(1, H0_MS), trader="w", extra_big_field="x" * 100)])
    assert seen["cols"] == set(TIP_FIELDS)
    for h in range(2, 24):
        ls.note_hour("2026-11-17T%02d" % h, [{"trader": "w"}])
    assert ls.incomplete == ["2026-11-17"] and not (tmp_path / "src" / "2026-11-17").exists() and "2026-11-17" not in ls.hours


def test_a_large_hour_is_streamed_with_bounded_memory(tmp_path):
    """150,000 rows (about 60 MB of plain jsonl, several hundred MB as Python dicts) pass through the whole runner loop (decoder pass, counting pass, scratch ledger rows) with a tracemalloc
    peak under 30 MB: nothing holds the hour. The bound is the test's own; the runner's real peak RSS is the dry run's to record."""
    import tracemalloc
    keep = tmp_path / "keep"
    keep.mkdir()
    n = 150_000
    for h, ms in enumerate((H0_MS - S.HOUR_MS, H0_MS)):
        with open(keep / ("tape-%s.jsonl" % S.hour_key(ms)), "w") as fh:
            for k in range(n if h == 0 else 10):
                fh.write(json.dumps(hv7_row(k, ms + (k % 3_600_000))) + "\n")

    class Count:
        def __init__(self):
            self.n = 0

        def decide(self, rows, state):
            for _ in rows:
                self.n += 1
            return []
    dec = Count()
    m = manifest(tmp_path)
    tracemalloc.start()
    try:
        assert S.follow(m, fixture=True, once=True, _roots=roots(tmp_path), decider_factory=lambda mm: dec, ledger_builder=lambda *a: None, engine_kw=SPS) == 0
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert dec.n == n and peak < 30_000_000, peak


def test_zstd_failure_is_a_refusal_not_a_short_hour(tmp_path):
    import shutil
    if shutil.which("zstd") is None:
        pytest.skip("no zstd CLI")
    bad = tmp_path / "tape-x.jsonl.zst"
    bad.write_bytes(b"not zstd data")
    with pytest.raises(S.Refused, match="exited"):
        list(S.iter_hour_rows(bad))
    # a closed-early iterator leaves no process behind and raises nothing
    import subprocess
    plain, z = tmp_path / "p.jsonl", tmp_path / "tape-y.jsonl.zst"
    plain.write_text("".join(json.dumps({"i": i}) + "\n" for i in range(1000)))
    subprocess.run(["zstd", "-q", str(plain), "-o", str(z)], check=True)
    it = S.iter_hour_rows(z)
    assert next(it) == {"i": 0}
    it.close()


def test_duckdb_builder_gets_a_memory_limit_and_a_spill_dir_under_out_root(tmp_path, monkeypatch):
    from tools import c1nf_wallet_ledger as W
    calls = {}
    monkeypatch.setattr(W, "build_day", lambda day, adapter, root, **kw: calls.update(kw=kw, day=day))
    monkeypatch.setattr(W, "build_asof", lambda root, day, **kw: calls.update(asof=day))
    out = tmp_path / "sim" / "b"
    S.default_ledger_builder("2026-11-16", out / "ledger-src" / "2026-11-16", out / "wallet-ledger")
    kw = calls["kw"]
    assert kw["mem_gb"] <= 4.0 and kw["threads"] <= 2 and Path(kw["tmp_dir"]).parent == out and Path(kw["tmp_dir"]).name == "duckdb-tmp" and calls["asof"] == "2026-11-17"


def test_the_duckdb_spill_dir_is_deleted_when_the_runner_stops(tmp_path):
    write_keep(tmp_path, 2)
    m = manifest(tmp_path)
    spill = Path(m["out_root"]) / "duckdb-tmp"

    def builder(*a):
        spill.mkdir(parents=True, exist_ok=True)
        (spill / "x.tmp").write_text("spill")
    S.follow(m, fixture=True, once=True, _roots=roots(tmp_path), decider_factory=lambda mm: StubDecider(), ledger_builder=builder, engine_kw=SPS)
    spill.mkdir(parents=True, exist_ok=True)
    (spill / "y.tmp").write_text("left behind")
    assert S.follow(m, fixture=True, once=True, _roots=roots(tmp_path), decider_factory=lambda mm: StubDecider(), ledger_builder=builder, engine_kw=SPS) in (0, 4)
    assert not spill.exists()


# ---------------------------------------------------------------------------------------------------------------------------------- bounded state (job #2914 OOM at 131 warm-up hours): engine.expire on the stream clock + dead-pool pruning
# A long synthetic stream on the REAL FeatureEngine (event V, tools.test_c1nf_features generators): one pool graduates every `gap_s`, each with 80 prints over 2 h.
def _long_rows(n_pools, gap_s, late_pool0_s=0):
    import tools.test_c1nf_features as TF
    G0, SPSX = TF.G0, TF.SPS
    rows = []
    for k in range(n_pools):
        dt = k * gap_s
        mint, pool, creator = "M%d" % k, "P%d" % k, "C%d" % (k % 7)

        def base(typ, prog, bt, slot, **kw):
            return dict(type=typ, prog=prog, slot=slot, block_time=bt, t_recv_ms=bt * 1000 + (slot % 7), signature="s%s%d" % (typ, slot), event_index=0, tx_index=1, **kw)
        bt_c = G0 + dt - 600
        s0 = int((bt_c - G0) * SPSX) + 100
        rows.append(base("create", "pumpfun", bt_c, s0, mint=mint, creator=creator, name="n%d" % k, symbol="s", is_mayhem_mode=False, trader=creator))
        for j, (tr, buy, sol) in enumerate((("A%d" % k, True, 10 ** 9), ("B%d" % k, False, 5 * 10 ** 8))):
            rows.append(base("trade", "pumpfun", bt_c + 100 + 100 * j, s0 + 250 + 250 * j, mint=mint, trader=tr, side="buy" if buy else "sell", sol_lamports=sol, token_raw=10 ** 12,
                             quote_reserve=31 * 10 ** 9, base_reserve=10 ** 15))
        sg = int(dt * SPSX) + 2000
        rows.append(base("complete", "pumpfun", G0 + dt, sg, mint=mint, trader="T", quote_mint=S.WSOL))
        rows.append(base("pool_create", "pumpswap", G0 + dt, sg, mint=mint, pool=pool, quote_mint=S.WSOL))
        pr = TF.make_event_prints(1000 + k, n=80, span_s=2 * 3600, n_traders=15)
        for i, r in enumerate(pr):
            rows.append(base("trade", "pumpswap", r["bt"] + dt, r["slot"] + int(dt * SPSX) + 2000 + i % 2, pool=pool, mint=mint, trader=r["trader"], side="buy" if r["isb"] else "sell",
                             sol_lamports=int(r["sol"]), token_raw=int(r["tok"]), quote_reserve=r["vault"], base_reserve=r["b"], virtual_quote_reserves=r["v_event"]))
        if k == 0 and late_pool0_s:  # an old pool that keeps printing after its engine life (graduation + 25 h) ended
            for i in range(5):
                r = pr[i]
                rows.append(base("trade", "pumpswap", r["bt"] + dt + late_pool0_s + i * 60, r["slot"] + int(dt * SPSX) + int(late_pool0_s * SPSX) + 3000 + i * 150, pool=pool, mint=mint,
                                 trader=r["trader"], side="buy", sol_lamports=int(r["sol"]), token_raw=int(r["tok"]), quote_reserve=r["vault"], base_reserve=r["b"], virtual_quote_reserves=r["v_event"]))
    rows.sort(key=lambda r: (r["slot"], r["t_recv_ms"]))
    return rows


class _Spy:
    """the REAL engine behind the shadow; every features_at answer is recorded per (pool, T) as an md5 of the vector, stage1, wallet_ok, h_top1"""

    def __init__(self, eng):
        import hashlib
        self.eng, self.per, self._h = eng, {}, hashlib

    def __getattr__(self, k):
        return getattr(self.eng, k)

    def features_at(self, pool, T, sd=None):
        import numpy as np
        r = self.eng.features_at(pool, T, sd)
        self.per[(pool, T)] = None if r is None else self._h.md5(np.asarray(r.vec, dtype=np.float64).tobytes() + repr((r.stage1, r.wallet_ok, r.h_top1)).encode()).hexdigest()
        return r


class _Zero:
    def predict(self, X):
        import numpy as np
        return np.zeros(len(X))


def _run_long(rows, hours, stream_expire_s, prune, probes=()):
    import hashlib
    import tracemalloc
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    eng = _Spy(cs.build_engine(None, v_source="event"))
    models = cs.ModelSet([{"from_day": "0000-00-00", "file": __file__, "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}], loader=lambda p: _Zero())
    d = S.C1nfDecider(eng, models, oracle_bypass=True, stream_expire_s=stream_expire_s, prune=prune)
    t0 = (rows[0]["block_time"] // 3600) * 3600
    probe = {}
    try:
        for h in range(hours):
            d.decide(iter([r for r in rows if t0 + h * 3600 <= r["block_time"] < t0 + (h + 1) * 3600]), S.RunState(hour="h%d" % h, warmup=False, day="d"))
            if h + 1 in probes:
                probe[h + 1] = dict(mb=(tracemalloc.get_traced_memory()[0] - base) / 1e6, pools=len(eng.eng._pools), info=len(eng.eng._info), first_ms=len(d.shadow.first_ms),
                                    last_print=len(d.shadow.last_print), rejected=len(eng.eng._rejected), mk=len(eng.eng._mk))
    finally:
        tracemalloc.stop()
    return eng.per, probe, d


def test_long_stream_features_are_identical_with_expiry_and_pruning_and_the_state_is_bounded():
    """40 pools graduate one per hour; 70 hours. OLD = the previous decider (no expiry: nothing is ever freed). NEW = the live shadow's stream expiry + dead-pool pruning. Every (pool, T) feature answer (22,932 of
    them) is md5-equal; the engine's pool / info / prints / market tables and the shadow's per-pool tables stop growing once the first pools age out, where the old ones grow with the hours."""
    rows = _long_rows(40, 3600)
    old, po, _ = _run_long(rows, 70, None, False, probes=(35, 70))
    new, pn, d = _run_long(rows, 70, "live", True, probes=(35, 50, 70))
    assert len(old) > 20_000 and old == new
    assert d.pruned["pools"] > 0 and d.shadow.c["stream_expires"] > 0   # pruning and expiry really ran
    assert po[70]["pools"] == 40 and pn[70]["pools"] <= 26 and pn[70]["first_ms"] <= 27 and pn[70]["mk"] < po[70]["mk"] / 5 and pn[70]["rejected"] > 0
    assert abs(pn[70]["pools"] - pn[50]["pools"]) <= 2 and abs(pn[70]["first_ms"] - pn[50]["first_ms"]) <= 2 and pn[70]["mk"] <= pn[50]["mk"] + 5   # flat between hour 50 and 70
    assert pn[70]["mb"] < 0.8 * po[70]["mb"] and pn[70]["mb"] <= 1.05 * pn[50]["mb"]                                                           # python memory flat once the window is full


def test_known_deviation_prints_after_a_pools_engine_life_are_ignored_with_expiry():
    """KNOWN DEVIATION, a question for QP (PR body): a pool older than graduation + 25 h that prints again is ignored by the expiring engine (documented in FeatureEngine.expire; this is what the live shadow always did)
    but was counted into the engine's market tables by the old never-expiring decider. Everything else is equal; the differing answers are exactly the later pools whose market window the late prints touch."""
    rows = _long_rows(40, 3600, late_pool0_s=30 * 3600)
    old, _, _ = _run_long(rows, 60, None, False)
    new, _, _ = _run_long(rows, 60, "live", True)
    diff = [k for k in set(old) | set(new) if old.get(k, "x") != new.get(k, "x")]
    assert 0 < len(diff) < 50 and {k[0] for k in diff} <= {"P%d" % i for i in range(30, 40)}
