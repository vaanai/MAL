"""hv4_read: the 25 cells, the pre-declared instants, write-once, V1 / V2 / learning on constructed books with known answers, seeds, the disclosure, result.v1,
Console drafts, isolation. Synthetic ledgers only; no network, no API call."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tools import arena_score as AS
from tools import hv4_read as R
from tools.arena_paper import TEST_ENV, Ledger, book_fail_legs, canon, load_registry, register_bot as _register_bot
from tools.arena_stack import fail_legs
from tools.hv4_arms import ARMS

DAY = 86_400_000
DAY0 = 1_789_000_000_000 - (1_789_000_000_000 % DAY)  # a UTC midnight: T0
SEND = 505_000
STACK = {"send": SEND, "rent": 2_039_280, "h_in": 0.002608, "h_out": 0.0016, "k_rounding": "ceil", "exit_lag_s": 0.55, "guard": 1.15, "portal_ppm": 0,
         "press_intercept_provisional": -1.42, "strand_rule": "both", "e1_floor_per_005": 384_022, "sps_entry": 0.4, "sps_exit": 0.4}
VSRC = {"entry": "row", "exit": "row", "span": {"row": 5}}
REPO = Path(__file__).resolve().parent.parent
N_SEEDS = 6  # tests run a short null; the production constant is pinned separately


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    monkeypatch.setenv(TEST_ENV, "1")  # the registry's fake clock / sha overrides are honoured inside a test only


# ------------------------------------------------------------------------------------------------------------------------------ synthetic books
def _leg(pnl, rule):
    fn = book_fail_legs if rule == "total" else (lambda p, f, s, a, b, c: fail_legs(p, f, a, b, c))
    flat, press, _, _ = fn(np.array([pnl]), np.array([True]), np.array([False]), np.array([0.0]), np.array([0.0]), SEND)
    return float(flat[0]), float(press[0])


def trade_row(bot, pid, ms, pnl, stake=0.25, sol_in=0.25):
    legs = {r: {} for r in ("total", "sc")}
    calc = {}
    for lat in ("1p3", "1p9"):
        calc[lat] = {"landing_slot": 1, "Qe": 1.0, "Be": 1.0, "spot": 1.0, "ssb": 0.0, "nearby_lamports": 0.0, "Qx": 1.0, "Bx": 1.0, "by_rule": {}}
        for rule in ("total", "sc"):
            flat, press = _leg(pnl, rule)
            legs[rule][f"flat_{lat}"] = {"state": "filled", "net_lamports": flat}
            legs[rule][f"press_{lat}"] = {"state": "filled", "net_lamports": press}
            calc[lat]["by_rule"][rule] = {"pnl_nofail": float(pnl), "filled": True, "state": "filled", "strand": False, "e1_extra": 0.0, "p_press_provisional": 0.2}
    return {"schema": "arena_trade_v1", "kind": "trade", "trade_id": f"{pid}-s{int(stake * 1000)}", "position_id": pid, "bot_id": bot, "frozen_sha": "abc1234",
            "decision_ms": {"entry": ms, "exit": ms + 5000}, "stake_sol": stake, "bot_sol_in": sol_in, "prog": "pumpswap", "state_src": "tape", "stack": dict(STACK), "legs": legs,
            "calc": calc, "v_src": VSRC}


def open_row(bot, pid, ms):
    return {"schema": "arena_trade_v1", "kind": "open", "trade_id": f"{pid}-open", "position_id": pid, "bot_id": bot, "frozen_sha": "abc1234",
            "decision_ms": {"entry": ms, "exit": None}, "stack": dict(STACK), "code_head": "abc1234", "code_dirty": False, "engine_id": "eng1"}  # the frozen code bound to the process


def start_row(bot):
    """The engine's start record (clean, on the registered sha): arena_score refuses open rows whose engine did not start clean on the frozen sha."""
    return {"schema": "arena_trade_v1", "kind": "start", "trade_id": f"start-eng1-{bot}", "engine_id": "eng1", "bot_id": bot, "frozen_sha": "abc1234",
            "decision_ms": {"entry": DAY0 + 1000, "exit": None}, "code_head": "abc1234", "code_dirty": False, "stack": dict(STACK)}


def book_rows(bot, daily, stakes=(0.25,), sol_in=None, scale=None):
    """daily: list (one per UTC day from T0) of lists of pnl lamports (one per position). One open row and one trade row per stake per position.
    sol_in: a callable (day, i) -> the position's own size (default 0.25). scale: stake -> multiplier of the pnl."""
    rows, k = [start_row(bot)], 0
    for d, pnls in enumerate(daily):
        for p in pnls:
            pid, ms = f"{bot}-p{k:05d}", DAY0 + d * DAY + 3_600_000 + k * 1000
            rows.append(open_row(bot, pid, ms))
            for st in stakes:
                rows.append(trade_row(bot, pid, ms, p * ((scale or {}).get(st, 1.0)), stake=st, sol_in=(sol_in(d, k) if sol_in else 0.25)))
            k += 1
    return rows


def noisy(mu, days, per_day, seed, sd=2e6):
    rng = np.random.default_rng(seed)
    return [list(rng.normal(mu, sd, per_day)) for _ in range(days)]


def const(v, days, per_day):
    return [[v] * per_day for _ in range(days)]


class World:
    """A registry with the 13 arms (clock T0, deciding stake 0.25), a ledger root and an out dir."""

    def __init__(self, tmp_path):
        self.root, self.reg, self.out, self.null = str(tmp_path / "ledger"), str(tmp_path / "bots.json"), str(tmp_path / "out"), str(tmp_path / "null")
        self.reads = str(tmp_path / "arena_reads")  # arena_score's store of recorded gate reads (the only one)
        for a in ARMS:
            _register_bot(a, "abc1234", DAY0, self.reg, deciding_stake=0.25, model_ids=["m"] if ARMS[a].llm else [], now_ms=0, verify_sha=False)
        self.led = Ledger(self.root, min_free_pct=0)  # the free-space floor (DEC-031 3.8) is a production guard; tests may set 0

    def add(self, rows):
        for r in rows:
            self.led.append(r)

    def seeds(self, persona, mu, days, per_day, n=N_SEEDS, first=1):
        d = Path(self.null) / f"{persona}-R"
        d.mkdir(parents=True, exist_ok=True)
        for s in range(first, first + n):
            (d / f"seed-{s:04d}.jsonl").write_text("\n".join(json.dumps(r) for r in book_rows(f"{persona}-R", noisy(mu, days, per_day, 1000 + s), )) + "\n")

    def book(self, bot, **kw):
        kw.setdefault("now_ms", DAY0 + 30 * DAY)
        kw.setdefault("n_seeds", N_SEEDS)
        return R.read_book(bot, ledger_root=self.root, registry=self.reg, out_dir=self.out, reads_dir=self.reads, null_dir=self.null, **kw)

    def mark(self, arm, status, boundary=DAY0 + 10 * DAY):
        """Stand-in for a gate read recorded in arena_score's store: arena_score's late-force check wants the bot_id and the read boundary (the arm's own instant)."""
        p = Path(self.reads) / f"{arm}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(canon({"bot_id": arm, "status": status, "read_boundary_ms": boundary}) + "\n")

    def learning(self, **kw):
        kw.setdefault("now_ms", DAY0 + R.LEARN_LOOK_OFFSET_MS)
        return R.read_learning(ledger_root=self.root, registry=self.reg, out_dir=self.out, reads_dir=self.reads, **kw)


def con_world(tmp_path, l_mu=4e6, x_mu=1e6, null_mu=0.0):
    w = World(tmp_path)
    w.add(book_rows("CON-L", noisy(l_mu, 5, 20, 11)))
    w.add(book_rows("CON-X", noisy(x_mu, 5, 20, 12)))
    w.seeds("CON", null_mu, 5, 20)
    return w


# ------------------------------------------------------------------------------------------------------------------------------ the cells
def test_cell_count_is_25_and_matches_the_design():
    cells = R.gate_cells()
    assert len(cells) == 25 == len(set(cells))
    assert len(ARMS) == 13
    arms = {a for a, _ in cells}
    assert arms == {n for n, a in ARMS.items() if a.promotable and a.role in ("L", "F", "X")}  # P and R are reference arms, never gate cells
    by = {s: [a for a, st in cells if st == s] for s in (0.25, 0.5, 1.0, R.AS_SIZED)}
    assert sorted(by[0.25]) == sorted(["CON-L", "CON-F", "CON-X", "BIG-L", "BIG-F", "BIG-X", "BST-L", "BST-F", "BST-X"])
    assert sorted(by[0.5]) == sorted(by[1.0]) == sorted(["CON-L", "CON-F", "CON-X", "BIG-L", "BIG-F", "BIG-X"])
    assert sorted(by[R.AS_SIZED]) == ["BIG-F", "BIG-L", "CON-F", "CON-L"]
    flat_con_big = [c for c in cells if c[1] in (0.25, 0.5, 1.0) and not c[0].startswith("BST")]
    assert (len(flat_con_big), len([c for c in cells if c[0].startswith("BST")]), len(by[R.AS_SIZED])) == (18, 3, 4)
    assert R.NULL_SEEDS == 200 and R.V2_ALPHA == 0.05 / 6 and R.LEARN_ALPHA == 0.025 and R.V2_DRAWS == 2000 and R.BOOT_SEED == 1


def test_window_clock_refuses_unregistered_arms_other_clocks_and_other_stakes(tmp_path):
    reg = tmp_path / "b.json"
    for a in list(ARMS)[:-1]:
        _register_bot(a, "abc1234", DAY0, reg, deciding_stake=0.25, model_ids=[], now_ms=0, verify_sha=False)
    with pytest.raises(R.Refused, match="not registered"):
        R.window_clock(load_registry(reg))
    _register_bot(list(ARMS)[-1], "abc1234", DAY0 + DAY, reg, deciding_stake=0.25, model_ids=[], now_ms=0, verify_sha=False)
    with pytest.raises(R.Refused, match="one clock_start_ms"):
        R.window_clock(load_registry(reg))
    reg2 = tmp_path / "b2.json"
    for a in ARMS:
        _register_bot(a, "abc1234", DAY0, reg2, deciding_stake=0.5 if a == "CON-L" else 0.25, model_ids=[], now_ms=0, verify_sha=False)
    with pytest.raises(R.Refused, match="deciding_stake"):
        R.window_clock(load_registry(reg2))


# ------------------------------------------------------------------------------------------------------------------------------ instants and write-once
def test_refused_before_the_instant_without_reading_any_outcome(tmp_path):
    w = World(tmp_path)
    w.add(book_rows("CON-L", noisy(4e6, 5, 20, 11)))
    p = next((Path(w.root) / "CON-L").glob("trades-*.jsonl"))
    with open(p, "a") as f:
        f.write('{"kind":"trade", this is not json and no outcome line may be parsed\n')  # an unreadable outcome line cannot block or move the instant
    b = DAY0 + 5 * DAY
    with pytest.raises(R.Refused, match="pre-declared instant"):
        w.book("CON-L", now_ms=b - 1)
    assert not (Path(w.out) / "reads").exists() and not Path(w.reads).exists()  # nothing written, and nothing stored
    w2 = World(tmp_path / "short")
    w2.add(book_rows("CON-L", noisy(4e6, 5, 19, 11)))  # 95 attempts: no boundary at all
    with pytest.raises(R.Refused, match="pre-declared instant"):
        w2.book("CON-L")


def test_second_run_is_refused_and_the_files_do_not_change(tmp_path):
    w = con_world(tmp_path)
    w.book("CON-L")
    files = sorted((Path(w.out) / "reads").glob("CON-L*"))
    assert [f.name for f in files] == ["CON-L.console.json", "CON-L.hv4.json", "CON-L.result.json"]  # the out dir holds no gate record
    stored = Path(w.reads) / "CON-L.json"
    files.append(stored)
    before = {f.name: f.read_bytes() for f in files}
    with pytest.raises(R.Refused, match="second run"):
        w.book("CON-L", now_ms=DAY0 + 31 * DAY)
    assert {f.name: f.read_bytes() for f in files} == before
    assert R.main(["book", "--bot", "CON-L", "--ledger-root", w.root, "--registry", w.reg, "--out-dir", w.out, "--arena-reads-dir", w.reads, "--now-ms", str(DAY0 + 31 * DAY)]) == 3


def test_now_ms_override_is_test_only(tmp_path, monkeypatch):
    w = World(tmp_path)
    monkeypatch.delenv(TEST_ENV)
    with pytest.raises(R.Refused, match="test-only"):
        w.book("CON-L", now_ms=DAY0 + 30 * DAY)


def test_learning_is_refused_before_d14_and_run_once(tmp_path):
    w = World(tmp_path)
    look = DAY0 + R.LEARN_LOOK_OFFSET_MS
    assert look == DAY0 + 14 * DAY + 13 * 3_600_000
    with pytest.raises(R.Refused, match="D14 only"):
        w.learning(now_ms=look - 1)
    assert not (Path(w.out) / "reads").exists()
    w.learning()  # all arms empty: NOT_DECIDABLE, but it is the one read
    with pytest.raises(R.Refused, match="second run|already recorded"):
        w.learning()


def test_hv4_read_assigns_no_attribute_of_another_module_and_never_calls_setattr():
    import ast

    tree = ast.parse((REPO / "tools" / "hv4_read.py").read_text())
    for n in ast.walk(tree):
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, (ast.AugAssign, ast.AnnAssign)) else []
        assert not any(isinstance(t, ast.Attribute) for t in targets), ast.dump(n)[:200]
        assert not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("setattr", "delattr")), "no monkeypatching"


def test_a_forced_boundary_is_a_comparator_screen_and_a_later_one_needs_a_recorded_non_pass_read(tmp_path):
    w = con_world(tmp_path)
    reg = load_registry(w.reg)
    rows = w.led.read_bot("CON-L")
    now = DAY0 + 30 * DAY
    nat = AS.score_bot("CON-L", reg, rows, now)
    ok = R.score_at("CON-L", reg, rows, DAY0 + 5 * DAY, now)  # at the bot's own instant: always allowed
    assert ok["read_boundary_ms"] == DAY0 + 5 * DAY and ok["boundary_source"] == "forced" and ok["gate_read"] is False
    assert ok["status"] == AS.FORCED_PREFIX + nat["status"] and R.base_status(ok) == nat["status"] and R.base_status(nat) == nat["status"]
    assert canon(ok["books"]) == canon(nat["books"])
    with pytest.raises(AS.Refused, match="not recorded yet"):
        R.score_at("CON-L", reg, rows, DAY0 + 6 * DAY, now)  # later than its own instant, and its own read is not recorded
    with pytest.raises(AS.Refused, match="recorded PASS"):
        R.score_at("CON-L", reg, rows, DAY0 + 6 * DAY, now, comparator_read={"bot_id": "CON-L", "status": "PASS", "read_boundary_ms": DAY0 + 5 * DAY})
    late = R.score_at("CON-L", reg, rows, DAY0 + 6 * DAY, now, comparator_read={"bot_id": "CON-L", "status": "FAIL", "read_boundary_ms": DAY0 + 5 * DAY})
    assert late["past_own_instant"] is True and late["comparator_recorded_status"] == "FAIL"
    with pytest.raises(AS.Refused, match="comparator score"):
        AS.record_read(w.reads, ok)  # a forced read is never the bot's one read


def test_v1_is_not_decidable_when_an_r_seed_has_an_earlier_own_instant_than_the_book(tmp_path):
    w = World(tmp_path)
    w.add(book_rows("CON-L", noisy(4e6, 5, 20, 11)))
    w.add(book_rows("CON-X", noisy(1e6, 5, 20, 12)))
    w.seeds("CON", 0.0, 5, 19)  # 95 attempts: the R seeds never reach their own instant
    r = w.book("CON-L")
    assert r["v1"]["status"] == "NOT_DECIDABLE" and len(r["v1"]["null"]["unusable"]) == N_SEEDS
    assert r["v2"]["status"] == "PASS" and r["adds_value"] is False


def test_v2_window_ends_at_the_earlier_of_the_two_instants(tmp_path):
    w = World(tmp_path)
    w.add(book_rows("CON-L", noisy(4e6, 6, 17, 11)))  # 102 attempts: its own instant is day 6
    w.add(book_rows("CON-X", noisy(1e6, 5, 20, 12)))  # 100 attempts: X reaches its instant on day 5
    w.seeds("CON", 0.0, 6, 17)
    r = w.book("CON-L")
    v2 = r["v2"]
    assert r["instant_ms"] == DAY0 + 6 * DAY and v2["book_instant_ms"] == DAY0 + 6 * DAY and v2["comparator_instant_ms"] == DAY0 + 5 * DAY
    assert v2["window_end_ms"] == DAY0 + 5 * DAY == AS.common_boundary("CON-L", "CON-X", w.root, w.reg) and v2["days"] == 5 and v2["boundary_source"] == "forced"
    assert v2["status"] == "PASS" and all(v["p"] == 1 / 2001 for v in v2["subcells"].values())
    assert r["v1"]["status"] == "PASS"  # V1 stays on the book's own window (day 6)


def test_pending_attempt_is_not_recordable_and_an_old_orphan_is_not_decidable(tmp_path):
    w = World(tmp_path)
    rows = book_rows("CON-L", noisy(4e6, 5, 20, 11))
    last = max(i for i, r in enumerate(rows) if r["kind"] == "trade")
    ms = rows[last]["decision_ms"]["entry"]
    w.add(rows[:last])  # the last position has an open row and no close
    b = DAY0 + 5 * DAY
    with pytest.raises(R.Refused, match="PENDING|pending"):
        w.book("CON-L", now_ms=b + 3_600_000)  # younger than the 24 h settle window: not a verdict
    assert not (Path(w.out) / "reads").exists() and not Path(w.reads).exists()
    r = w.book("CON-L", now_ms=ms + 25 * 3_600_000)  # older: a restart orphan, NOT_DECIDABLE, final
    assert r["arena_status"] == "NOT_DECIDABLE" and r["arena_score_read"]["restart_orphans"] == 1
    assert r["v1"]["status"] == "NOT_DECIDABLE" and r["adds_value"] is False


def test_an_incident_before_the_boundary_makes_the_gate_read_not_decidable_and_it_is_still_the_one_read(tmp_path):
    w = con_world(tmp_path)
    inc = Path(w.root) / "CON-L" / "incidents.jsonl"
    inc.write_text(json.dumps({"kind": "buy_refused", "at_ms": DAY0 + 2 * DAY, "bot_id": "CON-L", "row_decision_ms": DAY0 + 2 * DAY}) + "\n")
    r = w.book("CON-L")
    assert r["arena_status"] == "NOT_DECIDABLE" and r["arena_score_read"]["incidents"]["before_boundary"] == 1
    assert r["v1"]["status"] == "NOT_DECIDABLE" and r["adds_value"] is False
    assert json.loads((Path(w.reads) / "CON-L.json").read_text())["status"] == "NOT_DECIDABLE"  # stored: a NOT_DECIDABLE is final for the bot_id
    with pytest.raises(R.Refused, match="second run"):
        w.book("CON-L")


NUMBER = re.compile(r"[+-]\d+\.\d{6}\b")  # the Console draft prints every cell number as a signed 6-decimal figure


def outputs(w, bot="CON-L"):
    rd = Path(w.out) / "reads"
    return tuple(json.loads((rd / f"{bot}.{k}.json").read_text()) for k in ("hv4", "result", "console"))


def assert_claims_nothing(w, status, bot="CON-L"):
    """A read that is not PASS or FAIL is final and claims nothing (DEC-031 section 2): no gate tick, no pass_both, no cell number in result.v1 or the Console draft."""
    hv4, rec, con = outputs(w, bot)
    assert hv4["status"] == status and hv4["decidable"] is False
    assert rec["config"]["status"] == status and rec["gate"]["pass_both"] is False and rec["stage"] == "forward-paper"
    assert not any(rec["gate"]["flat"].values()) and not any(rec["gate"]["pressure_s1"].values())
    for leg in ("flat", "pressure_s1"):
        assert rec["metrics"][leg]["n_trades"] == 0 and rec["metrics"][leg]["total_sol"] is None and rec["metrics"][leg]["ex_top3_sol"] is None
    dec = next(c for c in hv4["cells"] if c["decides"])
    assert dec["subcells"] == {} and dec["report_1p3"] == {} and dec["ex_best_day_min_sol"] is None
    text = con["stream"][0]["text"]
    assert "Deciding cell" not in text and not NUMBER.search(text) and "claims nothing" in text
    assert "NOT PROPOSED FOR CANARY" not in text and "claims nothing" in rec["notes"]


def test_a_not_decidable_book_whose_cells_would_pass_reports_no_gate_tick_and_no_number(tmp_path):
    # (a) a ledger incident before the boundary: the cells at 4e6 per trade would all pass
    w = con_world(tmp_path / "inc")
    (Path(w.root) / "CON-L").mkdir(parents=True, exist_ok=True)
    (Path(w.root) / "CON-L" / "incidents.jsonl").write_text(json.dumps({"kind": "buy_refused", "at_ms": DAY0 + 2 * DAY, "bot_id": "CON-L", "row_decision_ms": DAY0 + 2 * DAY}) + "\n")
    r = w.book("CON-L")
    assert r["arena_status"] == "NOT_DECIDABLE"
    assert r["arena_score_read"]["books"]["0.25"]["cells"]["flat_1p9_total"]["pass"] is True  # the raw arena cell would have passed: this is the bug's precondition
    assert_claims_nothing(w, "NOT_DECIDABLE")
    # (b) a restart orphan older than the settle window
    w2 = World(tmp_path / "orphan")
    rows = book_rows("CON-L", noisy(4e6, 5, 20, 11))
    last = max(i for i, x in enumerate(rows) if x["kind"] == "trade")
    w2.add(rows[:last])
    w2.book("CON-L", now_ms=rows[last]["decision_ms"]["entry"] + 25 * 3_600_000)
    assert_claims_nothing(w2, "NOT_DECIDABLE")
    # (c) a bonding row: no deciding verdict (REPORT_ONLY)
    w3 = World(tmp_path / "bonding")
    rows = book_rows("CON-L", noisy(4e6, 5, 20, 11))
    for x in rows:
        if x["kind"] == "trade":
            x["prog"] = "pump_bonding"
    w3.add(rows)
    assert w3.book("CON-L")["arena_status"] == "REPORT_ONLY"
    assert_claims_nothing(w3, "REPORT_ONLY")


def test_a_pass_that_rests_on_its_best_day_is_not_proposed_for_canary(tmp_path):
    w = World(tmp_path)
    day = lambda v: [v] * 20  # noqa: E731
    w.add(book_rows("CON-L", [day(20e6), day(0.5e6), day(0.5e6), day(-2e6), day(-2e6)]))
    r = w.book("CON-L")
    dec = next(c for c in r["cells"] if c["decides"])
    assert r["arena_status"] == "PASS" and dec["ex_best_day_positive"] is False and dec["ex_best_day_min_sol"] < 0
    assert r["not_proposed_for_canary"] is True
    rec = json.loads((Path(w.out) / "reads" / "CON-L.result.json").read_text())
    assert "Not proposed for canary" in rec["notes"]
    assert "NOT PROPOSED FOR CANARY" in json.loads((Path(w.out) / "reads" / "CON-L.console.json").read_text())["stream"][0]["text"]


# ------------------------------------------------------------------------------------------------------------------------------ the one store of gate reads
def test_the_gate_read_is_recorded_in_arena_scores_store_and_the_out_dir_keeps_no_record(tmp_path):
    w = con_world(tmp_path)
    r = w.book("CON-L")
    store = Path(w.reads) / "CON-L.json"
    assert r["arena_record"] == {"path": str(store), "sha256": hashlib.sha256(store.read_bytes()).hexdigest(), "source": "recorded"}
    fresh = AS.score_all(w.root, w.reg, "CON-L", DAY0 + 30 * DAY)["bots"]["CON-L"]
    assert canon(json.loads(store.read_text())) == canon(fresh)  # exactly what `arena_score --bot CON-L --record` stores
    assert sorted(f.name for f in (Path(w.out) / "reads").iterdir()) == ["CON-L.console.json", "CON-L.hv4.json", "CON-L.result.json"]
    hv4 = json.loads((Path(w.out) / "reads" / "CON-L.hv4.json").read_text())
    assert "arena_score_read" not in hv4 and hv4["arena_record"]["sha256"] == hashlib.sha256(store.read_bytes()).hexdigest()
    with pytest.raises(AS.Refused, match="already recorded"):
        AS.record_read(w.reads, fresh)  # arena_score's own store is write-once


def test_a_gate_read_already_stored_by_arena_score_is_used_when_it_matches_and_is_never_rewritten(tmp_path):
    w = con_world(tmp_path)
    AS.record_read(w.reads, AS.score_all(w.root, w.reg, "CON-L", DAY0 + 30 * DAY)["bots"]["CON-L"])  # what `arena_score --record` does
    store = Path(w.reads) / "CON-L.json"
    before = store.read_bytes()
    r = w.book("CON-L")
    assert r["arena_record"]["source"] == "existing" and store.read_bytes() == before
    assert (Path(w.out) / "reads" / "CON-L.hv4.json").exists()


def test_a_stored_read_that_differs_from_the_fresh_scoring_is_refused_and_nothing_is_written(tmp_path):
    w = con_world(tmp_path)
    res = AS.score_all(w.root, w.reg, "CON-L", DAY0 + 30 * DAY)["bots"]["CON-L"]
    AS.record_read(w.reads, dict(res, attempts=res["attempts"] + 1))
    with pytest.raises(R.Refused, match="differs from the fresh scoring"):
        w.book("CON-L")
    assert not (Path(w.out) / "reads").exists()


def test_the_learning_screen_reads_arena_scores_store_and_never_the_out_dir(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 1e6 + 2e5 * d)  # the store says FAIL for the three arms
    stale = Path(w.out) / "reads" / "CON-L.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(canon({"bot_id": "CON-L", "status": "PASS", "arena_status": "PASS"}) + "\n")  # a stray file in the out dir is not a record
    lr = w.learning()["learning"]
    assert lr["status"] == "PASS" and lr["arm_screens"]["CON-L"]["rule"] == "later_allowed" and lr["arm_screens"]["CON-L"]["recorded_status"] == "FAIL"
    assert R.recorded_status(w.out + "/reads", "CON-L") == "PASS" and R.recorded_status(w.reads, "CON-L") == "FAIL"  # the function only looks where it is told


# ------------------------------------------------------------------------------------------------------------------------------ V1 / V2 with known answers
def test_book_read_pass_case_v1_v2_pass(tmp_path):
    w = con_world(tmp_path, l_mu=4e6, x_mu=1e6, null_mu=0.0)
    r = w.book("CON-L")
    assert r["instant_ms"] == DAY0 + 5 * DAY and r["arena_status"] == "PASS"
    assert r["v1"]["status"] == "PASS" and r["v2"]["status"] == "PASS" and r["v1_and_v2"] is True and r["adds_value"] is True
    assert r["not_proposed_for_canary"] is False
    assert set(r["v1"]["subcells"]) == set(R.SUBCELLS) == set(r["v2"]["subcells"])
    assert all(v["above"] for v in r["v1"]["subcells"].values())
    assert all(v["p"] == 1 / 2001 and v["p"] <= R.V2_ALPHA for v in r["v2"]["subcells"].values())  # five days, every diff positive: no draw has a mean <= 0
    assert r["v2"]["comparator"] == "CON-X"
    dec = next(c for c in r["cells"] if c["decides"])
    assert dec["arm"] == "CON-L" and dec["stake"] == 0.25 and dec["status"] == "PASS" and len(dec["subcells"]) == 4


def test_v1_fail_when_the_random_arm_is_better(tmp_path):
    w = con_world(tmp_path, l_mu=4e6, x_mu=1e6, null_mu=6e6)  # the R null is richer than the LLM book
    r = w.book("CON-L")
    assert r["arena_status"] == "PASS"  # the book itself clears the arena gate ...
    assert r["v1"]["status"] == "FAIL" and not any(v["above"] for v in r["v1"]["subcells"].values())
    assert r["adds_value"] is False  # ... but the LLM adds nothing above the random arm


def test_v2_fail_when_the_book_equals_the_x_arm(tmp_path):
    w = World(tmp_path)
    days = noisy(4e6, 5, 20, 11)
    w.add(book_rows("CON-L", days))
    w.add(book_rows("CON-X", days))  # identical pnl: every daily difference is 0
    w.seeds("CON", 0.0, 5, 20)
    r = w.book("CON-L")
    assert r["v2"]["status"] == "FAIL" and all(v["p"] == 1.0 for v in r["v2"]["subcells"].values())
    assert r["v2"]["note"] and "X goes to the canary first" in r["v2"]["note"]  # X also clears the arena gate at this instant
    assert r["v1"]["status"] == "PASS" and r["adds_value"] is False


def test_v1_v2_not_decidable_when_the_null_or_the_comparator_is_missing(tmp_path):
    w = World(tmp_path)
    w.add(book_rows("CON-L", noisy(4e6, 5, 20, 11)))  # no CON-X rows, no null directory
    r = R.read_book("CON-L", ledger_root=w.root, registry=w.reg, out_dir=w.out, null_dir=None, now_ms=DAY0 + 30 * DAY, n_seeds=N_SEEDS)
    assert r["v1"]["status"] == "NOT_DECIDABLE" and "no --null-dir" in " ".join(r["v1"]["reasons"])
    assert r["v2"]["status"] == "NOT_DECIDABLE" and r["adds_value"] is False
    w2 = con_world(tmp_path / "two")
    d = Path(w2.null) / "CON-R"
    (d / "seed-0003.jsonl").unlink()  # a missing seed
    r2 = w2.book("CON-L")
    assert r2["v1"]["status"] == "NOT_DECIDABLE" and r2["v1"]["null"]["unusable"].keys() == {"3"}
    assert r2["v2"]["status"] == "PASS"  # V2 does not need the null


def test_a_null_shorter_than_200_seeds_is_not_decidable_by_default(tmp_path):
    w = World(tmp_path)
    w.add(book_rows("CON-L", noisy(4e6, 5, 20, 11)))
    w.add(book_rows("CON-X", noisy(1e6, 5, 20, 12)))
    w.seeds("CON", 0.0, 5, 20, n=2)
    r = R.read_book("CON-L", ledger_root=w.root, registry=w.reg, out_dir=w.out, null_dir=w.null, now_ms=DAY0 + 30 * DAY)  # n_seeds defaults to 200
    assert r["v1"]["status"] == "NOT_DECIDABLE" and r["v1"]["null"]["n_seeds"] == 200 and len(r["v1"]["null"]["unusable"]) == 198


def test_seed_reproducibility(tmp_path):
    # the bootstrap
    d = [0.01, -0.02, 0.03, 0.005, 0.0, 0.02]
    assert R.day_block_p(d) == R.day_block_p(d) == R.day_block_p(d, 2000, 1)
    assert R.day_block_p(d, 2000, 1) != R.day_block_p(d, 2000, 2) or True  # a different seed may differ; same seed never does
    assert R.day_block_p([0.1] * 5) == 1 / 2001 and R.day_block_p([0.0] * 5) == 1.0 and R.day_block_p([]) is None
    # the null and the whole read, from the same inputs, into two out dirs
    w = con_world(tmp_path)
    reg = load_registry(w.reg)
    n1 = R.r_null("CON", reg, w.null, DAY0 + 5 * DAY, DAY0 + 30 * DAY, N_SEEDS)
    n2 = R.r_null("CON", reg, w.null, DAY0 + 5 * DAY, DAY0 + 30 * DAY, N_SEEDS)
    assert n1["p95"] == n2["p95"] and n1["values"] == n2["values"] and n1["seeds_used"] == N_SEEDS
    for s in R.SUBCELLS:
        assert n1["p95"][s] == pytest.approx(float(np.percentile(n1["values"][s], 95)))
    a = w.book("CON-L")
    first = json.loads((Path(w.out) / "reads" / "CON-L.hv4.json").read_text())
    stored_a = (Path(w.reads) / "CON-L.json").read_bytes()
    w.out, w.reads = str(tmp_path / "out2"), str(tmp_path / "reads2")
    b = w.book("CON-L")
    second = json.loads((Path(w.out) / "reads" / "CON-L.hv4.json").read_text())
    assert (Path(w.reads) / "CON-L.json").read_bytes() == stored_a  # the stored gate read is byte-identical
    for d in (first, second):  # only the two paths and the store file hash's location differ
        d.pop("arena_record")
        d["inputs"].pop("arena_reads_dir")
    assert canon(first) == canon(second)
    assert a["v1"]["null_sha256"] == b["v1"]["null_sha256"]
    # another set of null files gives another null
    w3 = con_world(tmp_path / "other", null_mu=1e6)
    n3 = R.r_null("CON", load_registry(w3.reg), w3.null, DAY0 + 5 * DAY, DAY0 + 30 * DAY, N_SEEDS)
    assert n3["p95"] != n1["p95"]


# ------------------------------------------------------------------------------------------------------------------------------ the learning read
def con_learning_world(tmp_path, l_gain, p_n=10, base=-8e6, status="FAIL", p_days=14):
    """Three CON arms over 14 days, 10 positions a day (own instant: day 10, before D14). All arms lose by default, so that their own screens failed; the three
    book reads are recorded as `status` (the screen rule: only a recorded PASS blocks a later boundary)."""
    w = World(tmp_path)
    w.add(book_rows("CON-L", [[base + l_gain(d)] * 10 for d in range(14)]))
    w.add(book_rows("CON-F", const(base, 14, 10)))
    w.add(book_rows("CON-P", const(base, p_days, p_n)))
    for a in ("CON-L", "CON-F", "CON-P"):
        w.mark(a, status)
    return w


def test_learning_pass_when_l_beats_f_and_p_and_the_gain_grows(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 1e6 + 2e5 * d)
    r = w.learning()
    lr = r["learning"]
    assert lr["status"] == "PASS" and "learning shown" in lr["statement"]
    for sub in R.SUBCELLS:
        assert lr["L_minus_F"]["subcells"][sub]["p"] == 1 / 2001 <= R.LEARN_ALPHA
        assert lr["L_minus_F"]["subcells"][sub]["week2_ge_week1"] is True
        assert lr["L_minus_P"]["subcells"][sub]["p"] == 1 / 2001
    assert lr["L_minus_F"]["days"] == 14 and r["instant_ms"] == DAY0 + 14 * DAY
    assert lr["supporting_only"]["BIG"]["status"] == "NOT_DECIDABLE"  # supporting arms have no rows; they never decide the CON verdict


def test_learning_fail_when_the_week2_gain_is_below_the_week1_gain(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 1e6 + 2e5 * (13 - d))  # L beats F every day, but the gain shrinks
    lr = w.learning()["learning"]
    assert lr["status"] == "FAIL" and "reviewer stays off" in lr["statement"]
    assert all(v["p_ok"] for v in lr["L_minus_F"]["subcells"].values())
    assert not any(v["week2_ge_week1"] for v in lr["L_minus_F"]["subcells"].values())


def test_learning_fail_when_l_equals_f(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 0.0)
    lr = w.learning()["learning"]
    assert lr["status"] == "FAIL" and all(v["p"] == 1.0 for v in lr["L_minus_F"]["subcells"].values())


def test_learning_not_decidable_with_too_few_trades_or_a_missing_arm(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 1e6 + 2e5 * d, p_n=5)  # 70 priced trades on the placebo arm
    lr = w.learning()["learning"]
    assert lr["status"] == "NOT_DECIDABLE" and any("CON-P" in x and "fewer than 100 attempts" in x for x in lr["reasons"])  # arena_score cannot force: no instant
    w2 = World(tmp_path / "empty")
    assert w2.learning()["learning"]["status"] == "NOT_DECIDABLE"


def test_a_learning_arm_whose_instant_is_after_d14_is_read_but_needs_100_priced_trades_before_it(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 1e6 + 2e5 * d, p_n=7, p_days=15)  # CON-P: 7 a day for 15 days: 98 attempts before D14, its own instant is day 15
    lr = w.learning()["learning"]
    assert lr["arm_screens"]["CON-P"]["rule"] == "not_later" and lr["arm_screens"]["CON-P"]["own_instant_ms"] == DAY0 + 15 * DAY
    assert lr["status"] == "NOT_DECIDABLE" and any("CON-P" in x and "98 priced trades" in x and "< 100" in x for x in lr["reasons"])


def test_learning_is_refused_while_a_con_arm_has_an_attempt_inside_the_settle_window(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 1e6 + 2e5 * d)
    w.add([open_row("CON-L", "CON-L-open-only", DAY0 + 14 * DAY - 3_600_000)])  # decided 1 h before D14, never closed; 14 h old at the look
    with pytest.raises(R.Refused, match="settle window"):
        w.learning()
    assert not (Path(w.out) / "reads").exists()


def test_learning_is_not_a_gate_read_in_any_output(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 1e6 + 2e5 * d)
    r = w.learning()
    assert r["cells"][0]["status"] == "NOT_A_GATE_READ"
    rec = json.loads((Path(w.out) / "reads" / "LEARNING.result.json").read_text())
    assert R.validate_result(rec) == [] and rec["config"]["gate_evaluated"] is False and rec["gate"]["pass_both"] is False
    assert not any(rec["gate"]["flat"].values()) and not any(rec["gate"]["pressure_s1"].values()) and "NOT A GATE READ" in rec["notes"]
    assert rec["stage"] == "forward-paper"
    frag = json.loads((Path(w.out) / "reads" / "LEARNING.console.json").read_text())
    assert "NOT a gate read" in frag["stream"][0]["text"] and "NOT a gate read" not in frag["ladder"][0]["title"] and "not a gate read" in frag["ladder"][0]["title"]
    assert "25 cells x 1 read each" in frag["stream"][0]["text"] and "learning shown" in frag["stream"][0]["text"]
    w2 = con_learning_world(tmp_path / "fail", lambda d: 0.0)
    w2.learning()
    assert json.loads((Path(w2.out) / "reads" / "LEARNING.result.json").read_text())["stage"] == "forward-paper"  # a learning FAIL is not a failed book


def test_learning_reads_an_arm_past_its_own_instant_only_if_its_screen_did_not_pass(tmp_path):
    for i, st in enumerate(("FAIL", "NOT_DECIDABLE", "REPORT_ONLY")):
        lr = con_learning_world(tmp_path / f"s{i}", lambda d: 1e6 + 2e5 * d, status=st).learning()["learning"]
        assert lr["status"] == "PASS"
        for a in ("CON-L", "CON-F", "CON-P"):
            sc = lr["arm_screens"][a]
            assert sc["rule"] == "later_allowed" and sc["recorded_status"] == st and sc["own_instant_ms"] == DAY0 + 10 * DAY and sc["cut_ms"] == DAY0 + 14 * DAY


def test_learning_is_not_decidable_and_never_loads_the_rows_of_an_arm_that_passed_its_screen(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 1e6 + 2e5 * d, base=3e6)  # L now wins money, so its screen passes
    (Path(w.reads) / "CON-L.json").unlink()  # replace the stand-in by the real book read
    rb = w.book("CON-L")  # recorded in arena_score's store through record_read
    assert rb["instant_ms"] == DAY0 + 10 * DAY and rb["arena_status"] == "PASS"
    last = sorted((Path(w.root) / "CON-L").glob("trades-*.jsonl"))[-1]
    with open(last, "a") as f:
        f.write('{"kind":"trade", this line is corrupt: it can only be hit if the passed arm\'s rows are loaded\n')
    lr = w.learning()["learning"]
    assert lr["arm_screens"]["CON-L"]["rule"] == "passed" and lr["arm_screens"]["CON-L"]["recorded_status"] == "PASS"
    assert lr["status"] == "NOT_DECIDABLE" and any("CON-L" in x and "PASSED its screen" in x for x in lr["reasons"])
    assert lr["L_minus_F"]["status"] == "NOT_DECIDABLE" and lr["L_minus_P"]["status"] == "NOT_DECIDABLE" and "nothing is claimed" in lr["statement"]
    assert not lr["L_minus_F"]["subcells"] and not lr["L_minus_P"]["subcells"]  # no number about L is computed


def test_learning_is_refused_while_a_con_arms_book_read_is_not_recorded(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 1e6 + 2e5 * d)
    (Path(w.reads) / "CON-F.json").unlink()
    with pytest.raises(R.Refused, match="not in arena_score's store"):
        w.learning()
    assert not (Path(w.out) / "reads").exists()  # nothing written: the one read has not been used
    w.mark("CON-F", "FAIL")
    assert w.learning()["learning"]["status"] == "PASS"


def test_a_supporting_arm_that_passed_or_is_unrecorded_is_not_decidable_without_stopping_the_read(tmp_path):
    w = con_learning_world(tmp_path, lambda d: 1e6 + 2e5 * d)
    w.add(book_rows("BIG-L", const(-1e6, 14, 10)))
    w.add(book_rows("BIG-F", const(-1e6, 14, 10)))
    w.mark("BIG-L", "PASS")  # BIG-F has no record
    lr = w.learning()["learning"]
    assert lr["status"] == "PASS"
    assert lr["supporting_only"]["BIG"]["status"] == "NOT_DECIDABLE"
    assert lr["arm_screens"]["BIG-L"]["rule"] == "passed" and lr["arm_screens"]["BIG-F"]["rule"] == "unrecorded"
    assert lr["arm_screens"]["BST-L"]["rule"] == "not_later"  # no rows: no instant, so the cut is not later than it


# ------------------------------------------------------------------------------------------------------------------------------ disclosure, ex-best-day, as-sized
def test_disclosure_block_states_books_cells_read_screen_wording_and_ex_best_day(tmp_path):
    w = con_world(tmp_path)
    r = w.book("CON-L")
    d = r["disclosure"]
    assert d["books_in_bots_json"] == 13 and d["books_with_clock_before_read"] == 13 and d["books_stopped"] == 0
    assert d["hv4_gate_cells"] == 25 and d["hv4_reads_per_bot_id"] == 1 and d["hv4_gate_checks"] == 25 and d["hv4_arms"] == 13
    assert d["arena_cells_existed"] == 13 * 3 * 2 * 2
    assert d["books_read"] == 1 + 1 and d["cells_read"] == d["books_read"] * 4  # the 0.25 stake book and the as-sized book
    t = d["screen_not_promotion"]
    for needle in ("SCREEN", "not a promotion", "best of 13 arena books", "stopped ones included", "winner's curse", "0.05/c", "NOT_DECIDABLE ones included", "canary"):
        assert needle in t
    assert set(d["ex_best_day_sol"]) == {"CON-L@0.25", "CON-L@0.5", "CON-L@1.0", "CON-L@as_sized"}
    gm = d["gate_multiplicity"]
    assert "25 cells x 1 read each = 25 gate checks" in gm and "DEC-031 section 2 governs" in gm and "superseded for gate purposes" in gm
    assert "separate pre-declared comparison, not a gate read" in gm
    assert "100" not in gm and not {"hv4_looks_in_design", "hv4_checks_disclosed"} & set(d)  # the old 25 x 4 = 100 text is gone
    # a stopped book still counts
    with open(w.reg, "a") as f:
        f.write(json.dumps({"kind": "stop", "bot_id": "BIG-R", "stopped_at_ms": DAY0 + 2 * DAY}) + "\n")
    reg = load_registry(w.reg)
    d2 = R.disclosure(reg, DAY0 + 30 * DAY, 1, 4, {})
    assert d2["books_with_clock_before_read"] == 13 and d2["books_stopped"] == 1
    assert R.disclosure(reg, DAY0 - 1, 1, 4, {})["books_with_clock_before_read"] == 0  # a clock after the read does not count


def test_ex_best_day_is_reported_per_cell_and_is_total_minus_the_best_day(tmp_path):
    w = con_world(tmp_path)
    r = w.book("CON-L")
    dec = next(c for c in r["cells"] if c["decides"])
    for sub, c in dec["subcells"].items():
        daily = r["arena_score_read"]["books"]["0.25"]["cells"][sub]["daily"]
        assert c["ex_best_day_sol"] == pytest.approx(c["total_sol"] - max(v["sol"] for v in daily.values()))
    assert dec["ex_best_day_min_sol"] == min(c["ex_best_day_sol"] for c in dec["subcells"].values())
    assert dec["ex_best_day_positive"] is (dec["ex_best_day_min_sol"] > 0)
    assert r["disclosure"]["ex_best_day_sol"]["CON-L@0.25"] == dec["ex_best_day_min_sol"]


def test_as_sized_cell_uses_each_positions_own_size(tmp_path):
    w = World(tmp_path)
    sizes = {0: 0.25, 1: 0.5, 2: 1.0}
    daily = noisy(4e6, 5, 20, 11)
    rows = book_rows("CON-L", daily, stakes=(0.25, 0.5, 1.0), sol_in=lambda d, k: sizes[k % 3], scale={0.25: 1.0, 0.5: 2.0, 1.0: 3.0})
    w.add(rows)
    r = w.book("CON-L")
    asz = next(c for c in r["cells"] if c["stake"] == R.AS_SIZED)
    assert asz["decides"] is False and asz["status"] in ("PASS", "FAIL") and asz["subcells"]["flat_1p9_total"]["n"] == 100
    want = sum(x["legs"]["total"]["flat_1p9"]["net_lamports"] for x in rows if x["kind"] == "trade" and x["stake_sol"] == x["bot_sol_in"]) / 1e9
    assert asz["subcells"]["flat_1p9_total"]["total_sol"] == pytest.approx(want)
    assert r["arena_score_as_sized"]["as_sized_unmatched"] == []
    assert [c["stake"] for c in r["cells"]] == [0.25, 0.5, 1.0, R.AS_SIZED]  # the arm's four DESIGN cells
    # a position whose own size has no priced row cannot be built
    w2 = World(tmp_path / "bad")
    w2.add(book_rows("CON-L", noisy(4e6, 5, 20, 11), stakes=(0.25, 0.5, 1.0), sol_in=lambda d, k: 0.3))
    asz2 = R.score_as_sized("CON-L", load_registry(w2.reg), w2.led.read_bot("CON-L"), DAY0 + 30 * DAY)
    assert asz2["status"] == "NOT_DECIDABLE" and len(asz2["as_sized_unmatched"]) == 100


def test_reference_arms_are_scored_but_are_not_gate_cells(tmp_path):
    w = World(tmp_path)
    w.add(book_rows("CON-R", noisy(0.0, 5, 20, 5)))
    r = w.book("CON-R")
    assert len(r["cells"]) == 1 and r["cells"][0]["gate_cell"] is False and r["v1"] is None and r["v2"] is None and r["adds_value"] is None


def test_a_reference_arm_that_clears_its_cells_is_labelled_not_promotable_everywhere_and_has_no_canary_sentence(tmp_path):
    for i, arm in enumerate(("CON-R", "CON-P", "BIG-R", "BST-R")):
        assert not ARMS[arm].promotable and R.is_reference(arm)
        w = World(tmp_path / arm)
        w.add(book_rows(arm, noisy(4e6, 5, 20, 31 + i)))  # positive: every cell clears the arena thresholds
        r = w.book(arm)
        assert r["arena_status"] == "PASS" and r["reference_arm"] is True and r["not_proposed_for_canary"] is False and r["adds_value"] is None
        hv4, rec, con = outputs(w, arm)
        label = R.REFERENCE_LABEL
        assert label == "reference arm, not promotable"
        # result.v1: labelled, no gate tick, no pass_both, never "failed"
        assert rec["config"]["reference_arm"] is True and rec["config"]["label"] == label and rec["config"]["gate_evaluated"] is False
        assert rec["gate"]["pass_both"] is False and not any(rec["gate"]["flat"].values()) and not any(rec["gate"]["pressure_s1"].values())
        assert rec["stage"] == "forward-paper" and label in rec["notes"] and R.validate_result(rec) == []
        # the disclosure
        d = hv4["disclosure"]
        assert d["reference_arm"] is True and d["reference_label"] == label and label.capitalize() in d["screen_not_promotion"]
        # the Console draft: label in the stream, the ladder title and its note, and no canary sentence anywhere
        text = con["stream"][0]["text"]
        assert label in text and "reference read" in text
        assert label in con["ladder"][0]["title"] and "reference read" in con["ladder"][0]["title"] and "gate read" not in con["ladder"][0]["title"]
        assert label.capitalize() in con["ladder"][0]["note"] and con["ladder"][0]["stage"] == "forward-paper"
        blob = json.dumps([rec, con, d]).lower()
        for banned in ("a pass allows", "live canary at", "best of", "winner's curse", "screen, not a promotion"):
            assert banned not in blob, banned
        assert "no canary path" in text
    # a reference arm that fails is not a failed book either
    w = World(tmp_path / "failing")
    w.add(book_rows("CON-R", noisy(-4e6, 5, 20, 5)))
    assert w.book("CON-R")["arena_status"] == "FAIL"
    assert outputs(w, "CON-R")[1]["stage"] == "forward-paper" and outputs(w, "CON-R")[2]["ladder"][0]["stage"] == "forward-paper"
    # a promotable arm is not labelled
    w2 = con_world(tmp_path / "promotable")
    w2.book("CON-L")
    hv4, rec, con = outputs(w2)
    assert rec["config"]["reference_arm"] is False and hv4["disclosure"]["reference_arm"] is False and "live canary" in con["stream"][0]["text"] and R.REFERENCE_LABEL not in con["stream"][0]["text"]


# ------------------------------------------------------------------------------------------------------------------------------ outputs
def test_result_v1_record_matches_the_schema_and_never_says_passed(tmp_path):
    w = con_world(tmp_path)
    w.book("CON-L")
    rec = json.loads((Path(w.out) / "reads" / "CON-L.result.json").read_text())
    assert R.validate_result(rec) == []
    schema = json.loads((REPO / "schemas" / "result.v1.schema.json").read_text())
    assert set(schema["required"]) <= set(rec)
    assert rec["schema_version"] == "result.v1" and rec["stage"] == "forward-paper" and rec["role"] == "exploration"
    assert rec["metrics"]["flat"]["n_trades"] == 100 and rec["gate"]["pass_both"] is True
    assert rec["tries"]["of_m"] == 13 and 1 <= rec["tries"]["variant_n"] <= 13
    assert "not a promotion" in rec["notes"]
    bad = dict(rec)
    del bad["gate"]
    assert R.validate_result(bad)  # the local validator really rejects
    try:
        import jsonschema
    except ImportError:
        return
    jsonschema.validate(rec, schema)


def test_result_v1_stage_is_failed_for_a_final_fail(tmp_path):
    w = World(tmp_path)
    w.add(book_rows("CON-L", noisy(-4e6, 5, 20, 11)))
    r = w.book("CON-L")
    assert r["arena_status"] == "FAIL"
    rec = json.loads((Path(w.out) / "reads" / "CON-L.result.json").read_text())
    assert rec["stage"] == "failed" and rec["gate"]["pass_both"] is False and R.validate_result(rec) == []


def test_console_rows_are_a_draft_fragment_and_console_json_is_untouched(tmp_path):
    console = REPO / "data" / "console.json"
    before = hashlib.sha256(console.read_bytes()).hexdigest()
    w = con_world(tmp_path)
    w.book("CON-L")
    frag = json.loads((Path(w.out) / "reads" / "CON-L.console.json").read_text())
    assert set(frag) == {"stream", "ladder", "events"}
    s = frag["stream"][0]
    assert s["from"] == "claude" and s["kind"] == "status" and s["id"] and s["atUtc"].endswith("Z")
    assert "SCREEN" in s["text"] and "not a promotion" in s["text"] and "V1 PASS" in s["text"] and "V2 PASS" in s["text"]
    assert not any(bad in s["text"].lower() for bad in ("made money", "profitable", "cleared the promotion gate"))
    assert frag["ladder"][0]["stage"] == "forward-paper" and "No book has cleared the promotion gate" in frag["ladder"][0]["note"]
    assert hashlib.sha256(console.read_bytes()).hexdigest() == before


def test_the_console_draft_shows_all_four_sub_cells_and_a_failed_book_never_prints_v1_pass(tmp_path):
    # a book that clears the flat legs and misses the pressure legs: QP's probe (CON-F, 6 days x 20, FAIL)
    w = World(tmp_path)
    w.add(book_rows("CON-F", noisy(2.0e5, 6, 20, 21, sd=1.0e5)))
    w.seeds("CON", 0.0, 6, 20)
    r = w.book("CON-F")
    assert r["status"] == "FAIL"
    subs = r["cells"][0]["subcells"]
    assert subs["flat_1p9_total"]["total_sol"] > 0 > subs["press_1p9_total"]["total_sol"]  # the most favourable leg is positive, the pressure leg is not
    text = outputs(w, "CON-F")[2]["stream"][0]["text"]
    for k, label in R.SUBLABEL.items():  # all four sub-cells are shown, each with total, ex-top-3, CI90 lo and its verdict
        i = text.index(label)
        seg = text[i:i + 220]
        assert f"n {subs[k]['n']}" in seg and R._f(subs[k]["total_sol"]) in seg and R._f(subs[k]["ex_top3_sol"]) in seg and R._f(subs[k]["ci90_lo_sol"]) in seg
        assert ("clears" if subs[k]["pass"] else "misses") in seg
    assert "pressure fail (slope scale 1)" in text and "decides only if all four sub-cells clear" in text and "Smallest total over the four" in text
    assert R._f(min(c["total_sol"] for c in subs.values())) in text
    assert "V1 PASS" not in text and "V2 PASS" not in text and "comparison only" in text
    assert re.search(r"V[12] (PASS|FAIL|NOT_DECIDABLE)", text) is None  # no V verdict for a book that did not clear the gate
    # a book that clears everything still shows both legs and its V statuses
    w2 = con_world(tmp_path / "pass")
    w2.book("CON-L")
    t2 = outputs(w2)[2]["stream"][0]["text"]
    assert all(label in t2 for label in R.SUBLABEL.values()) and "V1 PASS" in t2 and "V2 PASS" in t2


def test_table_lists_25_cells_and_only_recorded_reads(tmp_path):
    w = con_world(tmp_path)
    t0 = R.build_table(w.out, w.reg, DAY0 + 30 * DAY, w.reads)
    assert t0["cell_count"] == 25 and all(c["status"] == "NOT_READ_YET" for c in t0["cells"]) and t0["learning"] == "NOT_READ_YET"
    w.book("CON-L")
    t = R.build_table(w.out, w.reg, DAY0 + 30 * DAY, w.reads)
    got = {(c["arm"], c["stake"]): c["status"] for c in t["cells"]}
    assert got[("CON-L", 0.25)] == "PASS" and got[("CON-L", R.AS_SIZED)] in ("PASS", "FAIL") and got[("BIG-L", 0.25)] == "NOT_READ_YET"
    assert t["arms_read"] == ["CON-L"] and len(t["arms_not_read"]) == 12
    assert "SCREEN" in R.render_table(t)
    out = tmp_path / "t.json"
    assert R.main(["table", "--out-dir", w.out, "--arena-reads-dir", w.reads, "--registry", w.reg, "--json", str(out), "--now-ms", str(DAY0 + 30 * DAY)]) == 0
    assert R.main(["table", "--out-dir", w.out, "--arena-reads-dir", w.reads, "--registry", w.reg, "--json", str(out), "--now-ms", str(DAY0 + 30 * DAY)]) == 3  # never overwrites
    # the gate status of an arm is the store's: a disagreement is flagged
    store = Path(w.reads) / "CON-L.json"
    store.write_text(canon(dict(json.loads(store.read_text()), status="FAIL")) + "\n")
    bad = {(c["arm"], c["stake"]): c["status"] for c in R.build_table(w.out, w.reg, DAY0 + 30 * DAY, w.reads)["cells"]}
    assert bad[("CON-L", 0.25)] == "STORE_MISMATCH"


# ------------------------------------------------------------------------------------------------------------------------------ isolation
def test_no_r4b_l3_or_laya_module_is_imported_by_hv4_read():
    code = (f"import sys, json; sys.path.insert(0, {str(REPO)!r}); import tools.hv4_read; "
            "print(json.dumps(sorted(m for m in sys.modules if any(p.startswith('r4b') or p == 'l3' or 'laya' in p for p in m.split('.')))))")
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=REPO, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


def test_module_imports_no_network_api_or_forbidden_module():
    import ast

    tree = ast.parse((REPO / "tools" / "hv4_read.py").read_text())
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            names.add(n.module or "")
            names |= {f"{n.module}.{a.name}" for a in n.names}
    assert names, "no imports parsed"
    for m in names:
        parts = m.split(".")
        assert not any(p.startswith("r4b") or p == "l3" or "laya" in p or p in ("requests", "urllib", "http", "socket", "anthropic", "haiku_caller", "mal_result") for p in parts), m
