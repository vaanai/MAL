"""Latency pricing twins of the open simulation books (QP ruling 2026-11-14 osb latency twin, sections 1, 2, 4, 5): engine pricing option, manifest and driver, board pair view.
Synthetic fixtures only (tools/test_arena_paper._tape, tools/test_osb_follow world, tools/test_osb_board ledgers); no real tape, ledger, book or host path is opened.
Nothing here says any book makes money."""
import collections
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "research"))
import osb_board as B  # noqa: E402
import osb_follow as O  # noqa: E402
from tools.test_arena_paper import SC, _row, _tape, register_bot, tms  # noqa: E402
from tools.test_osb_board import CONTROL_ROWS, VARIANT_ROWS, START, ms, load, make_book, row, run, snap_root, tp  # noqa: E402,F401
from tools.test_osb_board_twin import excl, pbooks  # noqa: E402
from tools.test_osb_follow import (EK, FAST, _env, gworld, link, manifest, one_shot, run_once, tree_diff, world)  # noqa: E402,F401  (_env: the autouse env fixture of that module)
import floor_gap_range_engine as G  # noqa: E402

SPS, D = 0.215, 7  # the ruling's measured seconds per slot (A3 record 213-215 ms) and the engine's feed delay


@pytest.fixture(autouse=True)
def _arena_env(monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def mk(tmp, name, lat=None, sps=SPS, delay=D, **kw):
    d = tmp / name
    d.mkdir()
    reg = d / "bots.json"
    register_bot("botA", "abc1234", 0, reg, now_ms=0)
    eng = G.GapRangeEngine(d / "ledger", reg, SC(strand_rule="total", feed_delay_slots=delay), sps_fn=lambda: sps, code_state_fn=lambda: ("abc1234", False),
                           **({"pricing_lat_entry_s": lat} if lat is not None else {}), **kw)
    eng.now_fn = lambda: eng.hw_ms or 0
    return eng


def drive(eng, buy=100, sell=150):
    pid = None
    for r in _tape():
        eng.on_row(r)
        if r["slot"] == buy and pid is None:
            pid = eng.buy("botA", "P1", None, tms(buy), buy)
        if r["slot"] == sell and pid and eng.positions[pid].exit_slot is None:
            eng.sell("botA", pid, 1.0, tms(sell), sell)
    return pid


def rws(eng, *kinds):
    return [r for r in eng.ledger.read_bot("botA") if r["kind"] in kinds]


def plain(r):
    """a ledger row without the per-process fields and without the two latency-twin stack fields"""
    r = {k: v for k, v in r.items() if k not in ("written_ms", "engine_id")}
    r["stack"] = {k: v for k, v in r.get("stack", {}).items() if k not in ("pricing_lat_entry_s", "k_entry_slots")}
    return json.dumps(r, sort_keys=True)


# ------------------------------------------------------------------------------------------------------------------------------ engine: option off, strict-latency twin, k conversion
def test_k_twin_uses_the_measured_sps_and_the_feed_delay(tmp_path):
    e = mk(tmp_path, "a", lat=4.085)
    assert e._k_twin(0.215) == 12 and e._k(1.9, 0.215) == 12  # ceil(19.0 - 1e-9) - 7
    assert e._k(1.3, 0.215) == 7 and e._k(1.3, 0.215) == mk(tmp_path, "b")._k(1.3, 0.215)  # the 1.3 leg is the base class's
    assert e._k_twin(0.4) == 11 - 7  # another measured sps gives another k (nothing is hard-coded in slots): ceil(4.085 / 0.4) - 7
    assert mk(tmp_path, "c")._k(1.9, 0.215) == 9  # strict: ceil(1.9 / 0.215) = 9, landing 7 + 9 = 16 slots
    assert mk(tmp_path, "d", lat=0.5)._k_twin(0.215) == 0  # never negative


def test_twin_with_the_strict_latency_reproduces_strict_rows_byte_for_byte(tmp_path):
    """QP ruling L6: k_twin = 9 at sps 0.215 (total 16 slots = 3.44 s) reproduces the strict rows apart from the two new stack fields"""
    s, t = mk(tmp_path, "s"), mk(tmp_path, "t", lat=3.44)
    drive(s)
    drive(t)
    assert t._k_twin(SPS) == 9
    sr, tr_ = rws(s, "open", "trade", "excluded"), rws(t, "open", "trade", "excluded")
    assert len(rws(t, "trade")) == 3 and [plain(r) for r in sr] == [plain(r) for r in tr_]
    assert all(r["stack"]["pricing_lat_entry_s"] == 3.44 and r["stack"]["k_entry_slots"] == 9 for r in rws(t, "trade")) and "pricing_lat_entry_s" not in rws(s, "trade")[0]["stack"]


def test_off_path_rows_are_the_plain_engines_rows(tmp_path):
    a, b = mk(tmp_path, "a"), mk(tmp_path, "b", lat=None)
    drive(a)
    drive(b)
    assert [plain(r) for r in rws(a, "trade")] == [plain(r) for r in rws(b, "trade")]
    assert all("pricing_lat_entry_s" not in r["stack"] and "k_entry_slots" not in r["stack"] for r in rws(a, "trade", "excluded"))


@pytest.mark.parametrize("lat", [3.010, 4.085, 5.590])
def test_declared_values_price_the_deciding_leg_later_and_leave_everything_else(tmp_path, lat):
    s, t = mk(tmp_path, "s"), mk(tmp_path, "t", lat=lat)
    drive(s, sell=150)
    drive(t, sell=150)
    st, tt = rws(s, "trade"), rws(t, "trade")
    k = t._k_twin(SPS)
    assert len(st) == len(tt) == 3 and k == round(lat / SPS) - D
    for a, b in zip(st, tt):
        assert b["calc"]["1p9"]["landing_slot"] == b["effective_slot"]["entry"] + D + k and b["stack"]["k_entry_slots"] == k
        assert b["calc"]["1p9"]["landing_slot"] != a["calc"]["1p9"]["landing_slot"] or lat == 3.44
        # L4: the 1.3 leg is untouched
        assert (a["position_id"], a["stake_sol"], a["legs"]["total"]["flat_1p3"], a["legs"]["total"]["press_1p3"], a["calc"]["1p3"]) == \
               (b["position_id"], b["stake_sol"], b["legs"]["total"]["flat_1p3"], b["legs"]["total"]["press_1p3"], b["calc"]["1p3"])
        assert a["decision_ms"] == b["decision_ms"] and a["effective_slot"] == b["effective_slot"] and a["legs"]["total"]["flat_1p9"] != b["legs"]["total"]["flat_1p9"]
    assert O.decisions_md5_from_rows(rws(s, "open", "trade", "excluded")) == O.decisions_md5_from_rows(rws(t, "open", "trade", "excluded"))  # QP ruling L3: decisions unchanged


def test_exit_before_entry_landing_is_excluded_counted_never_priced_and_conserved(tmp_path):
    """sell decided at slot 110: strict refuses nothing (110 >= 100 + 9 + 1); exit landing 110 + 3 = 113 vs the twin's entry landing 100 + k"""
    out = {}
    for name, lat in (("s", None), ("t3", 3.010), ("t4", 4.085), ("t5", 5.590)):
        e = mk(tmp_path, name, lat=lat)
        drive(e, sell=110)
        out[name] = e
    assert len(rws(out["s"], "trade")) == 3 and len(rws(out["t3"], "trade")) == 3 and len(rws(out["t4"], "trade")) == 3  # k 7 -> entry landing 107, 12 -> 112 < 113: priced
    t5 = out["t5"]  # k 19: entry landing 119 >= exit landing 113
    ex = rws(t5, "excluded")
    assert rws(t5, "trade") == [] and len(ex) == 3 and {r["exclusion_reason"] for r in ex} == {"exit_before_entry_landing"}
    assert t5.counters.get("excluded_exit_before_entry_landing") == 1 and all(r["stack"]["k_entry_slots"] == 19 and r["stack"]["pricing_lat_entry_s"] == 5.59 and r["legs"] is None and r["calc"] is None for r in ex)
    # conservation per stake: priced + excluded of the twin = priced + excluded of strict over the same positions
    for stake in (0.25, 0.5, 1.0):
        n = lambda e: sorted((r["position_id"], r["stake_sol"]) for r in rws(e, "trade", "excluded") if r["stake_sol"] == stake)
        assert n(out["s"]) == n(t5) and len(n(t5)) == 1
    # decisions unchanged, also for the excluded twin
    assert O.decisions_md5_from_rows(rws(out["s"], "open", "trade", "excluded")) == O.decisions_md5_from_rows(rws(t5, "open", "trade", "excluded"))
    assert plain(rws(out["t4"], "trade")[0]) != plain(rws(out["s"], "trade")[0])
    # equality: exit landing exactly at the entry landing is excluded too ("at or below"); one slot later is priced
    eq = mk(tmp_path, "eq", lat=4.3)  # (not a declared value; the engine takes any positive number): k = 20 - 7 = 13, entry landing 113 = exit landing 110 + 3
    drive(eq, sell=110)
    assert {r["exclusion_reason"] for r in rws(eq, "excluded")} == {"exit_before_entry_landing"}


def test_sell_refusal_keeps_the_strict_k(tmp_path):
    """a sell decided before the strict slower entry leg lands (slot 109 < 100 + 9 + 1) is refused by both engines; slot 110 is accepted by both (no twin-only refusal: the host sees identical answers)"""
    from tools.arena_paper import ArenaRefused
    for name, lat in (("strict", None), ("twin", 5.590)):
        e, pid = mk(tmp_path, name, lat=lat), None
        for r in _tape():
            e.on_row(r)
            if r["slot"] == 100:
                pid = e.buy("botA", "P1", None, tms(100), 100)
            if r["slot"] == 109:
                with pytest.raises(ArenaRefused):
                    e.sell("botA", pid, 1.0, tms(109), 109)
            if r["slot"] == 110:
                e.sell("botA", pid, 1.0, tms(110), 110)
                break


def test_bonding_leg_inputs_follow_the_twin_landing(tmp_path):
    """the bonding venue builds its entry state from the landing slot too: the twin's 1p9 landing slot moves and its state is the last print at or before it; the 1p3 leg does not move"""
    rows = [dict(r, quote_reserve=40e9 + i * 1e8, base_reserve=7e14 - i * 1e10, pool=None, mint="M1") for i, r in enumerate(_tape(venue="pump_bonding", pool="P1", hi=230))]
    out = {}
    for name, lat in (("s", None), ("t", 4.085)):
        e = mk(tmp_path, name, lat=lat)
        pid = None
        for r in rows:
            e.on_row(r)
            if r["slot"] == 100 and pid is None:
                pid = e.buy("botA", "M1", None, tms(100), 100)
            if r["slot"] == 150 and pid:
                e.sell("botA", pid, 1.0, tms(150), 150)
        out[name] = rws(e, "trade")[0]
    s_, t_ = out["s"], out["t"]
    assert t_["calc"]["1p9"]["landing_slot"] == t_["effective_slot"]["entry"] + D + 12 and s_["calc"]["1p9"]["landing_slot"] == s_["effective_slot"]["entry"] + D + 9
    by_slot = {x["slot"]: x for x in rows}
    assert t_["calc"]["1p9"]["Qe"] == by_slot[t_["calc"]["1p9"]["landing_slot"]]["quote_reserve"]
    assert t_["calc"]["1p3"] == s_["calc"]["1p3"] and t_["calc"]["1p9"]["Qe"] != s_["calc"]["1p9"]["Qe"]


# ------------------------------------------------------------------------------------------------------------------------------ engine and manifest refusals (explicit raises: they hold under python -O)
@pytest.mark.parametrize("bad", [0, -1.0, True, "4.085", float("nan"), float("inf")])
def test_engine_refuses_a_bad_value(tmp_path, bad):
    with pytest.raises(ValueError):
        mk(tmp_path, "x", lat=bad)


def test_engine_refuses_the_hold_combination(tmp_path):
    with pytest.raises(ValueError):
        mk(tmp_path, "x", lat=4.085, gap_exclusion="hold")


def test_manifest_keys_and_values(world):
    tmp, src, names = world
    for v in (3.010, 4.085, 5.590, 3.01, 4):
        if v == 4:
            mp, _ = manifest(tmp, pricing_lat_entry_s=v, twin_of="strict-1")
            with pytest.raises(O.Refused):
                O.load_manifest(mp)
            continue
        mp, _ = manifest(tmp, pricing_lat_entry_s=v, twin_of="strict-1")
        assert O.load_manifest(mp)["pricing_lat_entry_s"] == v
    mp, _ = manifest(tmp, pricing_lat_entry_s=4.085, twin_of="strict-1", gap_exclusion="on")
    assert O.load_manifest(mp)
    for over in ({"pricing_lat_entry_s": 4.085}, {"pricing_lat_entry_s": 4.085, "twin_of": "x", "gap_exclusion": "hold"}, {"pricing_lat_entry_s": 4.085, "twin_of": "x", "gap_exclusion": "off"},
                 {"pricing_lat_entry_s": 3.0, "twin_of": "x"}, {"pricing_lat_entry_s": True, "twin_of": "x"}, {"pricing_lat_entry_s": "4.085", "twin_of": "x"}, {"pricing_lat_entry_s": None, "twin_of": "x"},
                 {"pricing_lat_entry_s": 4.085, "twin_of": "t-a"}):
        mp, _ = manifest(tmp, **over)
        with pytest.raises(O.Refused):
            O.load_manifest(mp)


def test_refusals_hold_under_python_dash_O(world, tmp_path):
    tmp, src, names = world
    for over in ({"pricing_lat_entry_s": 3.0, "twin_of": "x"}, {"pricing_lat_entry_s": 4.085}, {"pricing_lat_entry_s": 4.085, "twin_of": "x", "gap_exclusion": "hold"}):
        mp, _ = manifest(tmp, **over)
        p = subprocess.run([sys.executable, "-O", str(REPO / "scripts" / "research" / "osb_follow.py"), str(mp), "--once"], capture_output=True, text=True, cwd=str(REPO), timeout=120)
        assert p.returncode == O.EXIT_REFUSED and "refused" in p.stderr, p.stderr
    code = ("import sys; sys.path[:0] = [%r, %r]\nimport os; os.environ['MAL_ARENA_TEST'] = '1'\nfrom floor_gap_range_engine import GapRangeEngine\nfrom tools.test_arena_paper import SC, register_bot\n"
            "from pathlib import Path\nd = Path(%r)\nregister_bot('botA', 'abc1234', 0, d / 'bots.json', now_ms=0)\n"
            "try:\n    GapRangeEngine(d / 'ledger', d / 'bots.json', SC(strand_rule='total', feed_delay_slots=7), sps_fn=lambda: 0.215, code_state_fn=lambda: ('abc1234', False), pricing_lat_entry_s=4.085, gap_exclusion='hold')\n"
            "except ValueError:\n    print('REFUSED')\n") % (str(REPO), str(REPO / "scripts" / "research"), str(tmp_path / "oeng"))
    (tmp_path / "oeng").mkdir()
    p = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True, cwd=str(REPO), timeout=120)
    assert "REFUSED" in p.stdout, p.stderr[-600:]


def test_twin_manifest_diff_is_key_by_key():
    base = {"book_id": "S", "title": "s", "host": "lane", "rule_overrides": {}, "stakes_sol": [0.1], "send_lamports": 1, "start_hour": "2026-11-14T12", "seed_from_hour": "2026-11-05T00",
            "tape_dir": "/t", "out_root": "/o/s", "lane": {"gap_lookback_s": 21600}}
    twin = dict(base, book_id="T", title="t", out_root="/o/t", twin_of="S", pricing_lat_entry_s=4.085)
    assert O.twin_manifest_diff(twin, base) == []
    assert O.twin_manifest_diff(dict(twin, gap_exclusion="on"), base) == []  # the default is on on both sides
    for k, v in (("stakes_sol", [0.1, 0.25]), ("send_lamports", 2), ("start_hour", "2026-11-14T13"), ("rule_overrides", {"TP": 2.0}), ("tape_dir", "/u"), ("lane", {"gap_lookback_s": 7200}), ("host", "hostv2"),
                 ("gap_exclusion", "off"), ("seed_from_hour", "2026-11-04T00")):
        assert O.twin_manifest_diff(dict(twin, **{k: v}), base), k
    assert O.twin_manifest_diff({k: v for k, v in twin.items() if k != "lane"}, base) == ["lane"]  # a missing key differs
    assert O.twin_manifest_diff(twin, dict(base, book_id="Z")) and O.twin_manifest_diff(twin, dict(base, twin_of="Q")) and O.twin_manifest_diff(twin, dict(base, gap_exclusion="hold"))


def test_driver_start_check_against_the_strict_manifest(world):
    tmp, src, names = world
    mp_s, m_s = manifest(tmp, book_id="strict-1", rule_overrides=FAST, out_root=str(tmp / "out_s"))
    mp_t, m_t = manifest(tmp, book_id="twin-1", rule_overrides=FAST, out_root=str(tmp / "out_t"), twin_of="strict-1", pricing_lat_entry_s=4.085)
    mp_bad, _ = manifest(tmp, book_id="twin-2", rule_overrides={"HOLD_S": 3600}, out_root=str(tmp / "out_b"), twin_of="strict-1", pricing_lat_entry_s=4.085)
    link(src, tmp / "tape", names, 48)
    assert O.follow(str(mp_bad), once=True, _roots=(str(tmp),), strict_manifest=str(mp_s), _table_mode="first_print", _engine_kw=EK) == O.EXIT_REFUSED
    assert not (tmp / "out_b" / "manifest.sha256").exists()  # refused before the book started
    assert O.follow(str(mp_s), once=True, _roots=(str(tmp),), strict_manifest=str(mp_s), _table_mode="first_print", _engine_kw=EK) == O.EXIT_REFUSED  # a strict book is not given one


# ------------------------------------------------------------------------------------------------------------------------------ driver: byte identity with the option off, twin decisions
def _frozen_engine():
    """the GapRangeEngine of origin/main a455ca09 (frozen copy): the 'before this PR' code path of the byte-identity test"""
    import hashlib
    import types
    raw = (REPO / "tools" / "fixtures_osb" / "floor_gap_range_engine_a455ca09.py.txt").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "2b5b7a5a80b2fcfabc394656cbcdaf77b4c7d134264ce2297a5dc02a0c62045e"
    mod = types.ModuleType("floor_gap_range_engine_a455ca09")
    exec(compile(raw.decode(), "floor_gap_range_engine_a455ca09.py", "exec"), mod.__dict__)
    return mod.GapRangeEngine


@pytest.mark.parametrize("flag", ["on", "off", "hold"])
@pytest.mark.parametrize("use_gap_fixture", [False, True])
def test_strict_books_are_byte_identical_before_and_after_this_change(request, monkeypatch, flag, use_gap_fixture):
    """ledger md5 and decisions md5 over the fixture, option absent: this PR's engine against the frozen a455ca09 engine (the running strict books stay on 457795f4 and are not touched by this PR)"""
    tmp, src, names = request.getfixturevalue("gworld" if use_gap_fixture else "world")
    extra = {"twin_of": "strict-0"} if flag == "hold" else {}
    mp, m = manifest(tmp, rule_overrides=FAST, gap_exclusion=flag, **extra)
    new = one_shot(tmp, m, 48, run="new")
    monkeypatch.setattr(O, "GapRangeEngine", _frozen_engine())
    old = one_shot(tmp, m, 48, run="old")
    assert use_gap_fixture or O.ledger_rows(new / "ledger"), "fixture has no ledger rows"
    assert O.tree_md5(new / "ledger") == O.tree_md5(old / "ledger"), tree_diff(new / "ledger", old / "ledger")
    assert O.decision_md5(new) == O.decision_md5(old) and O.decisions_md5(new) == O.decisions_md5(old)


def test_latency_twin_book_has_the_strict_books_decisions_and_marks_its_rows(world):
    tmp, src, names = world
    link(src, tmp / "tape", names, 48)
    mp_s, m_s = manifest(tmp, book_id="strict-1", rule_overrides=FAST, out_root=str(tmp / "out_s"))
    mp_t, m_t = manifest(tmp, book_id="twin-1", rule_overrides=FAST, out_root=str(tmp / "out_t"), twin_of="strict-1", pricing_lat_entry_s=4.085)
    assert run_once(tmp, mp_s) == 0 and O.follow(str(mp_t), once=True, _roots=(str(tmp),), _table_mode="first_print", _engine_kw=EK, strict_manifest=str(mp_s)) == 0
    a, b = (tmp / "out_s" / "decisions.md5").read_text(), (tmp / "out_t" / "decisions.md5").read_text()
    assert a == b and len(a.strip()) == 32
    s_rows = [r for _, r in O.ledger_rows((tmp / "out_s" / "book").resolve() / "ledger") if r["kind"] in ("trade", "excluded", "open")]
    t_rows = [r for _, r in O.ledger_rows((tmp / "out_t" / "book").resolve() / "ledger") if r["kind"] in ("trade", "excluded", "open")]
    assert any(r["kind"] == "open" for r in t_rows) and O.decisions_md5_from_rows(s_rows) == O.decisions_md5_from_rows(t_rows)
    assert all("pricing_lat_entry_s" not in r.get("stack", {}) for r in s_rows) and all(r["stack"]["pricing_lat_entry_s"] == 4.085 for r in t_rows if r["kind"] in ("trade", "excluded"))
    assert (tmp / "out_s" / "manifest.sha256").read_text() != (tmp / "out_t" / "manifest.sha256").read_text()


# ------------------------------------------------------------------------------------------------------------------------------ board: pair view, same-value comparison, label
LAT_ROWS = CONTROL_ROWS[:-1] + excl("x1", ms(15, 3), "exit_before_entry_landing") + excl("x2", ms(15, 4), "exit_before_entry_landing")


def lat_world(tmp, with_control_twins=(4.085,), variant_twins=(4.085, 5.590)):
    a, ao = make_book(tmp, "A", CONTROL_ROWS)
    v, vo = make_book(tmp, "V", VARIANT_ROWS, ov={"Q_STAR": 1.0e9})
    specs, md5s = [(a, "control", None), (v, "variant", None)], {ao: "a", vo: "b"}
    for bid, strict, lat, rows, ov in [("A%d" % round(x), "A", x, LAT_ROWS, None) for x in with_control_twins] + [("V%d" % round(x), "V", x, VARIANT_ROWS, {"Q_STAR": 1.0e9}) for x in variant_twins]:
        mp, out = make_book(tmp, bid, rows, ov=ov, twin_of=strict, pricing_lat_entry_s=lat)
        specs.append((mp, "variant", strict))
        md5s[out] = "a" if strict == "A" else "b"
    for out, c in md5s.items():
        (out / "decisions.md5").write_text(c * 32 + "\n")
    return pbooks(tmp, specs)


def test_latency_pair_view_label_line_and_no_sum(tmp_path):
    rc, out = run(tmp_path, lat_world(tmp_path))
    assert rc == 0
    res, md = load(out), (out / "board.md").read_text()
    lp = [p for p in res["pairs"] if p.get("kind") == "latency"]
    assert {p["twin"] for p in lp} == {"A4", "V4", "V6"} and all(p["status"] == "pair" for p in lp)
    label = "latency twin: entry lands 4.085 s after the decision (chain time; live median 4.085 s on 12 buys, job #2702); exits and decisions as the strict book; take-profit mark unchanged, as live"
    assert B.LABEL_LAT % "4.085" == label and label in md and next(p for p in lp if p["twin"] == "A4")["label"] == label
    assert "latency twin: entry lands 5.590 s after the decision" in md
    assert "## Latency pair, stake 0.25: A4 (latency twin) beside A (strict)" in md
    assert "- exit before entry landed (A4): 2 (not counted as a trade)" in md and "- exit before entry landed (V4): 0 (not counted as a trade)" in md
    assert md.index("A4 (latency twin) beside A (strict)") < md.index("| window (flat / press (n closed)) | latency twin | strict |")
    assert row(res, "A4", 0.25)["excluded_counts"]["exit_before_entry_landing"] == 2 and "exit_before_entry_landing" not in row(res, "A", 0.25)["excluded_counts"]
    books = {b["id"]: b for b in res["books"]}
    assert books["A4"]["pricing_lat_entry_s"] == 4.085 and "pricing_lat_entry_s" not in books["A"]


def test_latency_twin_is_compared_only_with_the_control_twin_of_the_same_value(tmp_path):
    res = load(run(tmp_path, lat_world(tmp_path))[1])
    assert row(res, "V4", 0.25)["vs_control"]["control_book"] == "A4"  # same value
    v6 = row(res, "V6", 0.25)
    assert v6["vs_control"] is None and v6["disclosure"]["floor_holds"] is None  # no control twin of 5.590: n/a, never a strict or other-value control
    a4 = row(res, "A4", 0.25)
    assert a4["vs_control"] is None and a4["disclosure"]["floor_holds"] is None  # the control's own latency twin gets no comparison
    assert next(b for b in res["books"] if b["id"] == "A4")["role"] == "control (latency twin 4.085 s)"
    assert row(res, "V", 0.25)["vs_control"]["control_book"] == "A"  # strict with strict


def test_latency_twins_do_not_take_the_gap_twin_slot_and_rows_count_in_best_of_n(tmp_path):
    res = load(run(tmp_path, lat_world(tmp_path))[1])
    assert res["n_books_watched"] == 2 * (2 + 3) and "best of 10 book rows" in res["caveat"]  # every book x stake row of the strict and twin books
    plain_ = load(run(tmp_path, lat_world(tmp_path, with_control_twins=(), variant_twins=()))[1])
    assert plain_["n_books_watched"] == 4 and "pairs" not in plain_


def test_differing_decisions_show_the_latency_twin_as_a_separate_book(tmp_path):
    p = lat_world(tmp_path, variant_twins=())
    (tmp_path / "books" / "A4" / "decisions.md5").write_text("c" * 32 + "\n")
    res, md = load(run(tmp_path, p)[1]), (tmp_path / "board" / "board.md").read_text()
    pr = next(x for x in res["pairs"] if x["twin"] == "A4")
    assert pr["status"] == "differ" and "twin decisions differ: not a pricing-only twin" in md and "## Latency pair" not in md
    assert all("excluded_counts" not in r for b in res["books"] for r in b["rows"])


def test_label_and_line_pass_the_forbidden_word_check_and_the_check_still_bites():
    for L in ("3.010", "4.085", "5.590"):
        B.check_forbidden({"label": B.LABEL_LAT % L, "line": B.LINE_EXIT_BEFORE % ("T", 3)}, B.LABEL_LAT % L + "\n" + B.LINE_EXIT_BEFORE % ("T", 3))
    for w in B.FORBIDDEN:
        with pytest.raises(B.Refusal):
            B.check_forbidden({}, (B.LABEL_LAT % "4.085") + " " + w)


def test_board_refuses_a_twin_that_differs_beyond_the_allowed_keys(tmp_path):
    a, ao = make_book(tmp_path, "A", CONTROL_ROWS)
    t, to = make_book(tmp_path, "A4", LAT_ROWS, twin_of="A", pricing_lat_entry_s=4.085, send_lamports=1)
    with pytest.raises(B.Refusal):
        B.load_books_file(pbooks(tmp_path, [(a, "control", None), (t, "variant", "A")]), roots=(str(tmp_path),))
    t2, _ = make_book(tmp_path, "A5", LAT_ROWS, twin_of="A", pricing_lat_entry_s=4.085)
    assert B.load_books_file(pbooks(tmp_path, [(a, "control", None), (t2, "variant", "A")]), roots=(str(tmp_path),))
    with pytest.raises(B.Refusal):  # a latency twin whose BOOKS.json entry carries no twin_of
        B.load_books_file(pbooks(tmp_path, [(a, "control", None), (t2, "variant", None)]), roots=(str(tmp_path),))
    h, _ = make_book(tmp_path, "AH", LAT_ROWS, twin_of="A", gap_exclusion="hold")
    assert B.load_books_file(pbooks(tmp_path, [(a, "control", None), (h, "variant", "A")]), roots=(str(tmp_path),))  # the gap-in-hold twin is unchanged
