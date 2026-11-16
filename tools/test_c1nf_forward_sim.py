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


def test_decider_returns_an_entry_decided_before_the_crossing_row_with_the_previous_rows_instant():
    d, eng = mk_decider(T.only_minute(10))
    rows = t_rows()
    ents = d.decide(rows, S.RunState(hour="2026-09-04T12", warmup=False, day="2026-09-04"))
    assert len(ents) == 1
    e = ents[0]
    assert e.pool == T.POOL and e.exit_after_s == S.EXIT_S_DEFAULT and 0 < e.at_row < len(rows)
    assert (e.decision_ms, e.decision_slot) == (rows[e.at_row - 1]["t_recv_ms"], rows[e.at_row - 1]["slot"])
    assert rows[e.at_row]["block_time"] // 60 * 60 == T.BT0 + 600  # the row that crossed into minute 10
    assert e.meta["pred"] == 0.05 and e.meta["h_top1"] == 0.1


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
    eng_a = T.WalletEngine(T.only_minute(10, 15), True)
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
    d, _ = mk_decider(T.only_minute(10, 15), oracle_bypass=True)
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
    assert S.follow(manifest(tmp_path, start_hour="2026-10-15T23", pick_oracle=ORACLE), once=True, _roots=roots(tmp_path), decider_factory=lambda m: None) == 2
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
        return S.follow(manifest(tmp_path, **kw), once=True, _roots=roots(tmp_path), decider_factory=lambda m: None)
    assert f(tape_dir=str(tmp_path / "elsewhere")) == 2
    assert f(out_root=str(tmp_path / "keep" / "x")) == 2
    assert f(out_root=str(tmp_path / "other")) == 2
    for bad in ("/var/lib/mal/sealed/fast-trades-tip", "/data2/mal/arena-tip-feed/hv7", "/data2/mal/tip-tape-archive", "/home/claude/mal-data/arena-mirror/tape", "/data2/mal/fast0-offload-1113",
                "/data/mal/blocks/forward-1016", "/data/mal/r4b", "/data/mal/meta-capture"):
        assert S.follow(manifest(tmp_path, tape_dir=bad), once=True, _roots=(bad, str(tmp_path / "sim")), decider_factory=lambda m: None) == 2
        assert S.follow(manifest(tmp_path, out_root=str(tmp_path / "sim") + bad), once=True, _roots=roots(tmp_path), decider_factory=lambda m: None) == 2


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
        self.calls.append((state.hour, state.warmup, len(rows)))
        if state.warmup or state.hour != HOUR0:
            return []
        prev = rows[self.at - 1]
        return [S.Entry(pool="P1", mint="MP1", at_row=self.at, decision_ms=prev["t_recv_ms"], decision_slot=prev["slot"], exit_after_s=300.0, meta={"pred": 0.05})]


def run(tmp, m, dec, **kw):
    kw.setdefault("engine_kw", SPS)
    return S.follow(m, once=True, _roots=roots(tmp), decider_factory=lambda mm: dec, ledger_builder=lambda *a: None, **kw)


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
    write_keep(tmp_path, 2, warm=2)
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
            return [S.Entry("P1", "MP1", 5, rows[4]["t_recv_ms"], rows[4]["slot"])]
    assert run(tmp_path, manifest(tmp_path), Bad()) == 2


def test_an_entry_decided_before_s_is_refused(tmp_path):
    write_keep(tmp_path, 2)

    class Early:
        def decide(self, rows, state):
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
    assert S.follow(m, once=True, _roots=roots(tmp_path), decider_factory=lambda mm: StubDecider(), ledger_builder=lambda d, s, r: built.append(d), engine_kw=SPS) == 0
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
    assert (out / "manifest.sha256").read_text().strip() == S.manifest_sha256(m)
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
