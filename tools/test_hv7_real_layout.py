"""HV7 C3b tests: tools/drill_hv7_real_layout.py, tools/hv7_p0_count.py and the main() smoke. SYNTHETIC tape in a temp root, StubModel only: no API call, no key, no network,
no /data/mal path, no HV4/HV6/Q50 output, no real arena hour. Nothing here says HV7 makes money."""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from tools import arena_paper
from tools import drill_hv7_real_layout as D
from tools import hv6_agents as A
from tools import hv7_p0_count as P
from tools import hv7_runner as R
from tools.arena_paper import ArenaEngine, register_bot
from tools.test_hv4_runner import write_tape
from tools.test_hv7_runner import FEED_DELAY_SLOTS, HOUR, MIN, T0, T_E1, T_E2, _env, leader_first, tape_rows  # noqa: F401 - _env is an autouse fixture

DAY = "2026-09-25"          # T0 = 2026-09-25T06:00Z
START = T0 + HOUR
END = T_E2 + 5 * MIN
SHA = "ab" * 20


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch):
    def no_net(*a, **k):
        raise AssertionError("network used in a test")

    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)


@pytest.fixture()
def tape(tmp_path):
    d = tmp_path / "tape"
    write_tape(d, tape_rows())
    return d


# ------------------------------------------------------------------------------------------------------------------------ days and roots
@pytest.mark.parametrize("arg", ["", "2026-10-20", "2026-10-24", "2026-10-25", "2026-10-19,2026-10-21", "2026-10-21,2026-10-23", "2026-10-22,2026-10-21", "2026-10-21,2026-10-21",
                                 "2026-10-2x", "2026-10-21,2026-10-22,2026-10-23,2026-10-24"])
def test_days_outside_the_three_pre_clock_days_are_refused(arg):
    with pytest.raises(D.Refused):
        D.parse_days(arg)


def test_the_three_days_and_subsets_are_accepted():
    assert D.parse_days("2026-10-21,2026-10-22,2026-10-23") == ["2026-10-21", "2026-10-22", "2026-10-23"]
    assert D.parse_days("2026-10-22,2026-10-23") == ["2026-10-22", "2026-10-23"]
    assert D.END_EXCLUSIVE == "2026-10-24" and D.ALLOWED_DAYS[-1] < D.END_EXCLUSIVE


def test_run_real_refuses_other_tape_bad_id_and_a_non_drill_root(tmp_path):
    with pytest.raises(D.Refused, match="tape_dir_not_allowed"):
        D.run_real(["2026-10-21"], "x1", SHA, tape_dir=str(tmp_path))
    for bad in ("", "../x", "a/b", ".."):
        with pytest.raises(D.Refused, match="drill_id"):
            D.run_real(["2026-10-21"], bad, SHA)
    with pytest.raises(D.Refused, match="not_a_drill_root"):
        D.run_real(["2026-10-21"], "x1", SHA, base=tmp_path / "elsewhere")


def test_the_runner_has_no_fast_path_so_run_3_varies_the_hash_seed():
    src = Path(R.__file__).read_text()
    assert "fast_scan" not in src and "series" not in src
    assert len(set(D.HASHSEEDS.values())) == 2 and D.HASHSEEDS["real-a"] == D.HASHSEEDS["real-b"] != D.HASHSEEDS["real-c"]


# ------------------------------------------------------------------------------------------------------------------------ the replay
def test_two_runs_are_byte_identical_and_non_vacuous(tmp_path, tape):
    base = tmp_path / "drill"
    a = D.one_run(base, "real-a", SHA, [DAY], tape, start_ms=START, end_ms=END)
    b = D.one_run(base, "real-b", SHA, [DAY], tape, start_ms=START, end_ms=END)
    assert a["log_md5"] == b["log_md5"] and a["cards_md5"] == b["cards_md5"] and a["entries"] == b["entries"]
    assert a["periods_ge3"] >= 2 and all(a["entries"][arm] >= 2 for arm in R.ARM_ORDER)
    assert a["entries"]["R"] == a["entries"]["S"] == a["entries"]["L"] == a["periods_ge3"] and a["entries"]["U"] >= a["periods_ge3"]
    assert a["exclusions"] == 0 and a["gaps"] == 0 and a["hook_errors"] == 0
    assert a["period_timings"] >= 2 and a["ticks"] == (END - START) // 1000
    printed = json.dumps(a)
    assert "Pxx" not in printed and "Mint" not in printed     # no pool or mint id in anything the drill reports


def test_a_different_input_changes_the_md5(tmp_path, tape):
    a = D.one_run(tmp_path / "d", "real-a", SHA, [DAY], tape, start_ms=START, end_ms=END)
    c = D.one_run(tmp_path / "d", "real-c", SHA, [DAY], tape, start_ms=START, end_ms=T_E1 + MIN)
    assert a["log_md5"] != c["log_md5"]


def test_the_hour_filter_never_opens_other_days(tmp_path, tape, monkeypatch):
    opened = []
    orig_open, orig_stat = Path.open, Path.stat

    def spy_open(self, *a, **k):
        if self.parent == tape:
            opened.append(self.name)
        return orig_open(self, *a, **k)

    def spy_stat(self, *a, **k):
        if self.parent == tape:
            opened.append("stat:" + self.name)
        return orig_stat(self, *a, **k)

    monkeypatch.setattr(Path, "open", spy_open)
    monkeypatch.setattr(Path, "stat", spy_stat)
    D.one_run(tmp_path / "d", "real-a", SHA, ["2026-09-24"], tape, start_ms=START, end_ms=START + 10 * MIN)       # a day this tape has no hour for
    assert opened == []
    D.one_run(tmp_path / "d2", "real-a", SHA, [DAY], tape, start_ms=START, end_ms=START + 10 * MIN)
    assert opened and all(DAY in n for n in opened)


def test_run_real_in_process_passes_and_seals(tmp_path, tape, monkeypatch, capsys):
    monkeypatch.setattr(D, "DRILL_ROOT", tmp_path / "drill")
    monkeypatch.setattr(D, "REAL_TAPE", str(tape))
    monkeypatch.setattr(D, "seal_read_only", lambda base: None)         # a read-only temp tree would break pytest cleanup
    real_one = D.one_run
    monkeypatch.setattr(D, "one_run", lambda base, tag, sha, days, tp, **kw: real_one(base, tag, sha, days, tp, start_ms=START, end_ms=END))
    res = D.run_real([DAY], "t1", SHA, tape_dir=str(tape), isolate=False, rss_cap_mb=1e9)
    assert res["result"] == "PASS" and res["md5_equal"] and res["non_vacuous"]
    assert res["run1_log_md5"] == res["run2_log_md5"] == res["run3_log_md5"]
    out = capsys.readouterr().out
    assert '"hv7_real_layout":"start"' in out and '"hv7_real_layout":"sealed"' in out and "result_sha256" in out
    assert (tmp_path / "drill" / "t1" / "real-layout-result.json").exists()
    assert set(res["counts"]["entries"]) == set(R.ARM_ORDER)


CLEAN = {"log_md5": "m", "cards_md5": "c", "entries": {"L": 3, "R": 3, "S": 3, "U": 9}, "periods_ge3": 3, "period_ms_p95": 10.0, "tick_ms_p95": 1.0, "hook_errors": 0,
         "fallbacks": 0, "counters": {}}


def _v(**kw):
    return D.verdict({**CLEAN, **kw}, dict(CLEAN), dict(CLEAN), 100.0, 200.0)


def test_verdict_passes_a_clean_run_and_fails_on_md5_rss_period_tick_and_vacuous():
    base = CLEAN
    assert D.verdict(base, dict(base), dict(base), 100.0, 200.0)["result"] == "PASS"
    assert D.verdict(base, {**base, "log_md5": "x"}, dict(base), 100.0, 200.0)["result"] == "FAIL"
    assert D.verdict(base, dict(base), {**base, "log_md5": "x"}, 100.0, 200.0)["md5_equal_run3_other_hashseed"] is False
    assert D.verdict(base, dict(base), dict(base), 300.0, 200.0)["rss_ok"] is False
    assert D.verdict({**base, "period_ms_p95": 15000.0}, dict(base), dict(base), 1.0, 2.0)["period_p95_ok"] is False
    assert D.verdict(base, {**base, "tick_ms_p95": 1000.0}, dict(base), 1.0, 2.0)["tick_p95_ok"] is False
    assert D.verdict({**base, "periods_ge3": 0}, dict(base), dict(base), 1.0, 2.0)["result"] == "FAIL"
    slow3 = {**base, "period_ms_p95": 1e9, "tick_ms_p95": 1e9}
    assert D.verdict(base, dict(base), slow3, 1.0, 2.0)["result"] == "PASS"      # run 3 timing is report-only


@pytest.mark.parametrize("kw,name", [
    ({"hook_errors": 1}, "hook_errors"), ({"hook_errors": None}, "hook_errors"),
    ({"counters": {"leakage_refused": 1}}, "leakage_refused"), ({"counters": {"model_exception": 2}}, "model_exception"),
    ({"counters": {"ledger_unreadable": 1}}, "ledger_unreadable"), ({"counters": {"cost_mismatch": 1}}, "cost_mismatch"), ({"counters": {"late_reply": 1}}, "late_reply"),
    ({"fallbacks": 1}, "fallbacks"),
    ({"entries": {"L": 2, "R": 3, "S": 3, "U": 9}}, "entries"), ({"entries": {"L": 3, "R": 2, "S": 3, "U": 9}}, "entries"), ({"entries": {"L": 3, "R": 3, "S": 2, "U": 9}}, "entries"),
    ({"entries": {"L": 3, "R": 3, "S": 3, "U": 2}}, "entries"), ({"entries": {"L": 4, "R": 4, "S": 4, "U": 9}}, "entries"), ({"entries": {}}, "entries"),
    ({"periods_ge3": 0, "entries": {"L": 0, "R": 0, "S": 0, "U": 0}}, "entries")])
def test_every_stub_invariant_failure_fails_the_verdict(kw, name):
    v = _v(**kw)
    assert v["result"] == "FAIL" and v["invariants_ok"] is False and name in v["invariants_broken"]["run1"]
    # the same breakage in run 2 or run 3 also fails (identical broken runs are not a proof)
    broken = {**CLEAN, **kw}
    assert D.verdict(CLEAN, dict(broken), dict(CLEAN), 1.0, 2.0)["result"] == "FAIL"
    assert D.verdict(CLEAN, dict(CLEAN), dict(broken), 1.0, 2.0)["result"] == "FAIL"
    assert D.verdict(broken, dict(broken), dict(broken), 1.0, 2.0)["result"] == "FAIL"


def test_u_may_exceed_the_period_count_but_not_fall_below_it():
    big = {**CLEAN, "entries": {"L": 3, "R": 3, "S": 3, "U": 24}}
    assert D.verdict(big, dict(big), dict(big), 1.0, 2.0)["result"] == "PASS"


def test_summary_counts_buy_side_refusals_only_and_prints_an_allow_list_of_counters(tmp_path, tape):
    base = tmp_path / "d"
    run = D.new_run(base, "real-a", SHA, [DAY], tape, start_ms=START, end_ms=END)
    run.run(START, END)
    run.log.append("L", START, {"kind": "order_refused", "order_id": "x", "side": "sell", "reason": "arena_refused", "error_class": "E"})
    run.log.append("L", START, {"kind": "order_refused", "order_id": "y", "side": "buy", "reason": "arena_refused", "error_class": "E"})
    for k in ("sell_refused", "shutdown_in_flight", "restart_after_t_e", "late_reply", "card_refused:age_under_2h", "phantom_drop_x", "model_exception", "zzz"):
        run._count(k)
    s = D.summarize(run)
    assert s["order_refused"] == 1
    assert set(s["counters"]) == {"late_reply", "card_refused:age_under_2h", "phantom_drop_x", "model_exception"} | (set(s["counters"]) & D.PRINT_COUNTERS)
    for banned in ("sell_refused", "shutdown_in_flight", "restart_after_t_e", "zzz"):
        assert banned not in s["counters"] and banned not in json.dumps(s)
    assert "sell" not in json.dumps(s).replace("order_refused", "")


def test_the_hour_scan_cutoff_is_hv7s(tmp_path):
    d = tmp_path / "t"
    d.mkdir()
    (d / "tape-2026-10-24T00.jsonl").write_text("")
    assert D.hour_scan(d, ["2026-10-23"])["hours_present"] == 0          # not a chosen day: only listed, never judged
    with pytest.raises(D.Refused, match="hour_at_or_after_cutoff"):
        D.hour_scan(d, ["2026-10-24"])


# ------------------------------------------------------------------------------------------------------------------------ P0
def test_p0_counts_hours_with_three_candidates_and_has_no_outcome(tmp_path, tape):
    res = P.count_days([DAY], tmp_path / "p0", tape)
    d = res["per_day"][DAY]
    assert d["hours"] == 24 and d["half_hours"] == 48
    assert 1 <= d["hours_ge3"] <= 24 and d["hours_ge3"] <= d["hours_ge3_raw"]
    assert d["half_hours_ge3"] >= d["hours_ge3"]
    root = tmp_path / "p0" / "v7" / "decisions"
    assert not any(list((root / a).glob("*.jsonl")) for a in R.ARM_ORDER if (root / a).exists())      # no entry, no rank row, no sell in any arm log
    assert not (tmp_path / "p0" / "v7" / "engine-ledger").exists()


def test_p0_matches_the_replay_on_the_same_tape(tmp_path, tape):
    """The P0 hourly count and the replay's periods_ge3 come from the same screen: the number of hourly screens that are not no-trade must agree on a common window."""
    a = D.one_run(tmp_path / "d", "real-a", SHA, [DAY], tape, start_ms=T0 - 6 * HOUR, end_ms=T0 + 6 * HOUR)
    res = P.count_days([DAY], tmp_path / "p0", tape)["per_day"][DAY]
    assert a["periods_ge3"] <= res["hours_ge3"]          # the P0 pass covers the whole day, the replay a window of it


def test_p0_rule_switches_below_10_7_and_flags_a_day_under_5():
    hi = {f"d{i}": {"hours_ge3": 12, "half_hours_ge3": 20} for i in range(3)}
    r = P.rule(hi)
    assert r["hourly_mean_lt_10_7"] is False and r["chosen_period"] == "hourly" and r["chosen_meets_10"] and not r["hourly_any_day_lt_5"]
    lo = {"a": {"hours_ge3": 4, "half_hours_ge3": 9}, "b": {"hours_ge3": 10, "half_hours_ge3": 18}, "c": {"hours_ge3": 12, "half_hours_ge3": 20}}
    r = P.rule(lo)
    assert r["hourly_mean_lt_10_7"] is True and r["chosen_period"] == "30min" and r["hourly_any_day_lt_5"] and r["chosen_mean"] == pytest.approx(47 / 3)
    assert r["chosen_any_day_lt_5"] is False and r["hourly_mean_excl_first_day"] == 11
    edge = {"a": {"hours_ge3": 10, "half_hours_ge3": 0}, "b": {"hours_ge3": 11, "half_hours_ge3": 0}}
    assert P.rule(edge)["hourly_mean"] == 10.5 and P.rule(edge)["hourly_mean_lt_10_7"] is True
    assert P.rule({"a": {"hours_ge3": 11, "half_hours_ge3": 0}})["hourly_mean_lt_10_7"] is False


def test_p0_refuses_other_tape_days_and_ids(tmp_path):
    with pytest.raises(D.Refused, match="tape_dir_not_allowed"):
        P.run_p0(["2026-10-21"], "p1", tape_dir=str(tmp_path))
    with pytest.raises(D.Refused):
        P.run_p0(["2026-10-21"], "../p1")
    with pytest.raises(D.Refused, match="not_a_drill_root"):
        P.run_p0(["2026-10-21"], "p1", root=tmp_path / "elsewhere")
    with pytest.raises(D.Refused):
        D.parse_days("2026-10-20")


# ------------------------------------------------------------------------------------------------------------------------ main() smoke
def _registry(path: Path, *, clock: int = T0 - 60 * MIN, halt: int = END + HOUR) -> None:
    for arm in R.ARM_ORDER:
        register_bot(R.bot_id(arm), SHA[:13], clock, str(path), deciding_stake=0.25, model_ids=[A.MODEL_ID] if arm == "L" else [], now_ms=T0 - 61 * MIN, verify_sha=False)
        arena_paper._append_line(path, {"kind": "hv7_halt", "bot_id": R.bot_id(arm), "entries_halt_ms": halt})


def _argv(tmp: Path, reg: Path, ref: str, *, clock: int = T0 - 60 * MIN, halt: int = END + HOUR) -> list[str]:
    return ["--tape", str(tmp / "tape"), "--root", str(tmp / "v7"), "--frozen-sha", SHA, "--clock-start-ms", str(clock), "--entries-halt-ms", str(halt),
            "--feed-delay-slots", str(FEED_DELAY_SLOTS), "--registry", str(reg), "--owner-cost-line-ref", ref]


def _stub(tmp: Path) -> dict:
    eng = ArenaEngine(tmp / "eng", tmp / "reg.json", cfg=R.stack_config(FEED_DELAY_SLOTS), sps_fn=lambda: 0.4)
    return {"engine": eng, "model": A.StubModel(leader_first, guard=R.Hv7Guard(tmp / "v7"), latency_ms=50), "chain": __import__("tools.test_hv4_runner", fromlist=["FakeChain"]).FakeChain(),
            "start_ms": START, "end_ms": END}


def test_main_smoke_refused_ref_accepted_ref_and_registry_mismatch(tmp_path, monkeypatch, capsys):
    write_tape(tmp_path / "tape", tape_rows())
    reg = tmp_path / "bots.json"
    _registry(reg)
    _registry(tmp_path / "reg.json")        # the engine's own registry (same four books)

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("built before the refusal")

    with monkeypatch.context() as m:
        m.setattr(R, "Hv7Guard", Boom)
        # (1) a refused owner ref: returns 2 before anything is built
        assert R.main(_argv(tmp_path, reg, "free text"), _stub=_stub_never()) == 2
        assert "refused" in capsys.readouterr().err
        # (2) a registry mismatch (the clock differs, then the halt differs, then an empty registry): refused, nothing built
        assert R.main(_argv(tmp_path, reg, "q_6_kzYJva9XXuYQ", clock=T0 - 59 * MIN), _stub=_stub_never()) == 2
        assert R.main(_argv(tmp_path, reg, "q_6_kzYJva9XXuYQ", halt=END + 2 * HOUR), _stub=_stub_never()) == 2
        assert R.main(_argv(tmp_path, tmp_path / "none.json", "q_6_kzYJva9XXuYQ"), _stub=_stub_never()) == 2
    assert not (tmp_path / "v7").exists()
    # (3) an accepted ref with a matching registry: the simulated run goes through with the stub model and writes the logs
    assert R.main(_argv(tmp_path, reg, "n_-pn6x44cliJfQg"), _stub=_stub(tmp_path)) == 0
    log = R.Hv7DecisionLog(tmp_path / "v7")
    scan = log.rows("SCAN")
    assert scan[0]["kind"] == "start" and scan[0]["ms"] == START and scan[0]["live"] is False
    assert sum(1 for r in scan if r.get("kind") == "period") >= 2
    for arm in R.ARM_ORDER:
        assert sum(1 for r in log.rows(arm) if r.get("kind") == "entry") >= 2
    assert any(r.get("kind") == "rank" and r.get("fallback") is False for r in log.rows("L"))


def _stub_never() -> dict:
    return {"engine": None, "model": None, "chain": None, "start_ms": 0, "end_ms": 0}


# ------------------------------------------------------------------------------------------------------------------------ the subprocess path
def test_isolated_runs_use_three_processes_with_the_declared_hash_seeds(tmp_path, tape, monkeypatch):
    """Drives _isolated_run / child_one_run for real: each replay is a separate python process whose PYTHONHASHSEED is the declared one. The child is started with -c so
    the constants (drill root, tape, write roots, window) can be pointed at the temp tree; nothing else differs from `-m tools.drill_hv7_real_layout --one-run`."""
    import subprocess
    import sys

    monkeypatch.setattr(D, "DRILL_ROOT", tmp_path / "drill")
    monkeypatch.setattr(D, "REAL_TAPE", str(tape))
    monkeypatch.setattr(D, "seal_read_only", lambda base: None)
    base_tmp = str(tmp_path.parent)
    code = (
        "import sys; from pathlib import Path\n"
        "from tools import drill_hv7_real_layout as D, hv7_runner as R\n"
        f"R.READ_ROOTS = R.WRITE_ROOTS = ({base_tmp!r},)\n"
        f"D.DRILL_ROOT = Path({str(tmp_path / 'drill')!r}); D.REAL_TAPE = {str(tape)!r}\n"
        "D.ALLOWED_DAYS = ('2026-09-25',)\n"
        "o = D.one_run\n"
        f"D.one_run = lambda b, t, s, d, tp, **k: o(b, t, s, d, tp, start_ms={START}, end_ms={END})\n"
        "raise SystemExit(D.main(sys.argv[1:]))\n")
    real_run, calls = subprocess.run, []

    def fake_run(argv, **kw):
        calls.append(kw["env"]["PYTHONHASHSEED"])
        assert argv[1:4] == ["-B", "-m", "tools.drill_hv7_real_layout"] and "--one-run" in argv
        return real_run([argv[0], "-B", "-c", code, *argv[4:]], **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = D.run_real([DAY], "iso1", SHA, tape_dir=str(tape), isolate=True, rss_cap_mb=1e9)
    assert calls == ["0", "0", "1234567"]
    seeds = [json.loads((tmp_path / "drill" / "iso1" / f"{t}.json").read_text())["hashseed"] for t in D.RUNS]
    assert seeds == ["0", "0", "1234567"]
    assert res["result"] == "PASS" and res["peak_rss_mb"] > 0


# ------------------------------------------------------------------------------------------------------------------------ as-of mutation
def test_a_planted_future_print_raises_leakage_error_and_the_runner_refuses_the_card(tmp_path, tape):
    from tools import hv4_cards as C

    run = D.new_run(tmp_path / "d", "real-a", SHA, [DAY], tape, start_ms=START, end_ms=END)
    T = T_E1 - 45_000
    run.poll_to(T)
    pool = sorted(p.id for p in run.state.pools_wsol(lambda p: p.prog == "pumpswap"))[0]
    snap, prints = run.state.snapshot(pool), list(run.state.pool(pool).events)
    feats = H_scan_feats(run, snap, prints, T)
    ok = run._card(pool, T, snap, prints, feats)
    assert ok is not None and run.counters.get("leakage_refused", 0) == 0                   # control: the same pool at the same instant builds a card
    planted = prints + [(T + 5000, 1_000_000_000, True, "planted")]                          # a print stamped AFTER T
    with pytest.raises(C.LeakageError):
        C.assert_as_of(snap, T, prints=planted, chain={}, as_of_slot=run.slot)
    assert run._card(pool, T, snap, planted, feats) is None
    assert run.counters["leakage_refused"] == 1
    with pytest.raises(C.LeakageError):
        C.assert_as_of(snap, T, prints=prints + [(T, 1, True, "x")], chain={}, as_of_slot=run.slot)      # a print AT T is refused too (strict t < T)


def H_scan_feats(run, snap, prints, T):
    from tools import hv4_cards as C
    from tools import hv6_cards as H

    res = H.scan(iter([(snap, prints)]), as_of_ms=T, limiter=C.SetupLimiter(), board_on=False)
    return res.feats[snap["id"]]


# ------------------------------------------------------------------------------------------------------------------------ HV6 card equivalence
@pytest.fixture()
def hv6_cards(tmp_path, tape, monkeypatch):
    """A real HV6 runner on the same synthetic tape: its cards/ files are the 'HV6 side'."""
    from tools import hv6_cards as H6
    from tools import hv6_runner as R6
    from tools.test_hv6_runner import make as make6

    monkeypatch.setattr(H6, "READ_ROOTS", (str(tmp_path.parent),))
    monkeypatch.setattr(H6, "WRITE_ROOTS", (str(tmp_path.parent),))
    monkeypatch.delenv("MAL_HV4_CHAIN_STATE", raising=False)
    write_tape(tmp_path / "h6" / "tape", tape_rows())          # the HV6 runner replays the SAME tape contents as the HV7 side
    run = make6(tmp_path / "h6")
    run.run(START, END)
    d = tmp_path / "h6" / "v6" / "cards"
    assert list(d.glob("*.jsonl"))
    return d, R6


@pytest.fixture()
def _restore_consts():
    from tools import hv7_card_equiv as E

    saved = (E.REAL_TAPE, D.DRILL_ROOT)
    yield
    E.REAL_TAPE, D.DRILL_ROOT = saved


def _md5(d: Path, days) -> str:
    import hashlib

    h = hashlib.md5()
    for x in days:
        h.update(f"cards/{x}.jsonl".encode() + b"\0" + (d / f"{x}.jsonl").read_bytes() + b"\0")
    return h.hexdigest()


def test_hv7_cards_equal_hv6_cards_at_hv6s_instants(tmp_path, tape, hv6_cards, monkeypatch, _restore_consts):
    from tools import hv7_card_equiv as E

    d, _ = hv6_cards
    monkeypatch.setattr(E, "MIN_PAIRS", 3)
    _eq_ok(E, tape, tmp_path)
    res = E.run_equiv([DAY], "eq1", _md5(d, [DAY]), cards_dir=d, tape_dir=str(tape), root=tmp_path / "drill" / "eq1", window=(0, 1 << 62))
    assert res["hv7_card_equiv"] == "PASS" and res["pairs"] >= 3 and res["unequal"] == 0 and res["equal"] == res["pairs"] and res["hv6_sha_self_mismatch"] == 0, res
    assert res["outcome_free"] is True and res["api_calls"] == 0


def _eq_ok(E, tape, tmp_path) -> None:
    """Point the module constants at the temp tree for this test (restored by the autouse fixture below)."""
    E.REAL_TAPE = str(tape)
    D.DRILL_ROOT = tmp_path / "drill"


def test_equivalence_fails_on_a_changed_card_and_refuses_a_wrong_md5_or_window(tmp_path, tape, hv6_cards, monkeypatch, _restore_consts):
    from tools import hv7_card_equiv as E

    d, _ = hv6_cards
    _eq_ok(E, tape, tmp_path)
    monkeypatch.setattr(E, "MIN_PAIRS", 3)
    with pytest.raises(E.Refused, match="pinned"):
        E.run_equiv([DAY], "eq2", "0" * 32, cards_dir=d, tape_dir=str(tape), root=tmp_path / "drill" / "eq2", window=(0, 1 << 62))
    with pytest.raises(E.Refused, match="outside_window"):
        E.run_equiv([DAY], "eq3", _md5(d, [DAY]), cards_dir=d, tape_dir=str(tape), root=tmp_path / "drill" / "eq3", window=(0, 1000))
    with pytest.raises(E.Refused, match="tape_dir"):
        E.run_equiv([DAY], "eq4", "0" * 32, cards_dir=d, tape_dir=str(tmp_path), root=tmp_path / "drill" / "eq4")
    # change one HV6 card's field (and its sha, so the file is self-consistent): the comparison must count it as unequal and FAIL
    f = d / f"{DAY}.jsonl"
    lines = f.read_text().splitlines()
    r = json.loads(lines[0])
    r["card"]["f"]["vol_60m"] = (r["card"]["f"]["vol_60m"] or 0) + 1.0
    r["sha"] = E.p4.card_sha256(r["card"])
    lines[0] = json.dumps(r)
    f.write_text("\n".join(lines) + "\n")
    res = E.run_equiv([DAY], "eq5", _md5(d, [DAY]), cards_dir=d, tape_dir=str(tape), root=tmp_path / "drill" / "eq5", window=(0, 1 << 62))
    assert res["hv7_card_equiv"] == "FAIL" and res["unequal"] >= 1


def test_equivalence_reads_only_the_three_pinned_card_files():
    from tools import hv7_card_equiv as E

    src = Path(E.__file__).read_text()
    assert E.HV6_CARDS_DIR == Path("/data/mal/arena/haiku-v6/drill/real-5071e29a-r16g-20261026/real-a/v6/cards")
    for banned in ("iterdir", ".glob(", "rglob", "listdir", "os.walk", "scandir", ".stat("):
        assert banned not in src.split('"""', 2)[2], banned
    assert E.WINDOW_MS == (1792540800000, 1792800000000)


# ------------------------------------------------------------------------------------------------------------------------ HV6 reference drill: pre-filter vs phantom-ON
def _phantom_tape_rows():
    """The synthetic tape plus all-ones-signature copies of some trades (simulated rows): a phantom-ON build drops them, a phantom-OFF build counts them."""
    rows = tape_rows()
    extra = []
    for i, r in enumerate(rows):
        if r["type"] == "trade" and i % 7 == 0:
            c = dict(r)
            c["signature"] = "1" * 64
            c["sol_lamports"] = r["sol_lamports"] * 5
            extra.append(c)
    assert extra
    return sorted(rows + extra, key=lambda r: (r["t_recv_ms"], r["signature"]))


def _hv6_on(tmp_path, monkeypatch, rows, phantom: bool):
    from tools import hv6_cards as H6
    from tools.test_hv6_runner import make as make6

    monkeypatch.setattr(H6, "READ_ROOTS", (str(tmp_path.parent),))
    monkeypatch.setattr(H6, "WRITE_ROOTS", (str(tmp_path.parent),))
    monkeypatch.delenv("MAL_HV4_CHAIN_STATE", raising=False)
    write_tape(tmp_path / "h6" / "tape", rows)
    run = make6(tmp_path / "h6", cfg_kw={"phantom_filter": phantom})
    run.run(START, END)
    return tmp_path / "h6" / "v6" / "cards"


def test_pre_filter_hv6_cards_differ_only_when_phantom_rows_are_present(tmp_path, monkeypatch, _restore_consts):
    from tools import hv7_card_equiv as E

    monkeypatch.setattr(E, "MIN_PAIRS", 3)
    # (a) no phantom rows: HV6 phantom-OFF cards equal HV7 phantom-ON cards, so the filter alone is not a difference
    clean = tmp_path / "clean"
    clean.mkdir()
    write_tape(clean / "tape", tape_rows())
    d0 = _hv6_on(clean, monkeypatch, tape_rows(), phantom=False)
    _eq_ok(E, clean / "tape", clean)
    r0 = E.run_equiv([DAY], "eq6", _md5(d0, [DAY]), cards_dir=d0, tape_dir=str(clean / "tape"), root=clean / "drill" / "eq6", window=(0, 1 << 62))
    assert r0["hv7_card_equiv"] == "PASS" and r0["unequal"] == 0, r0
    # (b) phantom rows present: the phantom-OFF reference is UNEQUAL to HV7's (phantom-ON) card; the field breakdown names fields and counts only
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    rows = _phantom_tape_rows()
    write_tape(dirty / "tape", rows)
    d1 = _hv6_on(dirty, monkeypatch, rows, phantom=False)
    _eq_ok(E, dirty / "tape", dirty)
    r1 = E.run_equiv([DAY], "eq7", _md5(d1, [DAY]), cards_dir=d1, tape_dir=str(dirty / "tape"), root=dirty / "drill" / "eq7", window=(0, 1 << 62), field_diff=True)
    assert r1["hv7_card_equiv"] == "FAIL" and r1["unequal"] > 0, r1
    fc = r1["unequal_field_counts"]
    assert fc and all(isinstance(v, int) and v > 0 for v in fc.values()) and all(isinstance(k, str) for k in fc)
    # (c) the same dirty tape with a phantom-ON HV6 reference is equal again
    ok = tmp_path / "ok"
    ok.mkdir()
    write_tape(ok / "tape", rows)
    d2 = _hv6_on(ok, monkeypatch, rows, phantom=True)
    _eq_ok(E, ok / "tape", ok)
    r2 = E.run_equiv([DAY], "eq8", _md5(d2, [DAY]), cards_dir=d2, tape_dir=str(ok / "tape"), root=ok / "drill" / "eq8", window=(0, 1 << 62), hv6_drill_id=E.HV6_DRILL_ID_P3_ON)
    assert r2["hv7_card_equiv"] == "PASS" and r2["hv6_drill_id"] == "p3-on-8cd8fe54", r2


def test_hv6_drill_id_allowlist_is_exactly_two_and_paths_are_constructed():
    from tools import hv7_card_equiv as E

    assert E.HV6_DRILL_IDS == ("real-5071e29a-r16g-20261026", "p3-on-8cd8fe54")
    for ok in E.HV6_DRILL_IDS:
        assert E.hv6_cards_dir(ok) == Path("/data/mal/arena/haiku-v6/drill") / ok / "real-a" / "v6" / "cards"
    for bad in ("", "../x", "p3-on-8cd8fe5", "real-5071e29a-r16g-20261027", "p3-on-8cd8fe54/", "x"):
        with pytest.raises(E.Refused, match="hv6_drill_id_not_allowed"):
            E.hv6_cards_dir(bad)
    with pytest.raises(E.Refused, match="hv6_drill_id_not_allowed"):
        E.run_equiv(["2026-10-21"], "eqx", "0" * 32, hv6_drill_id="p3-on-8cd8fe54/../x")
    with pytest.raises(SystemExit):
        E.main(["--drill-id", "e", "--expect-cards-md5", "0" * 32, "--hv6-drill-id", "other"])


def test_diff_fields_reports_names_and_counts_never_values():
    from tools import hv7_card_equiv as E

    a = {"id": "x", "f": {"vol_60m": 1.5, "n": 3}, "chain": {"k": 1}, "setup": None}
    b = {"id": "x", "f": {"vol_60m": 2.5, "n": 3}, "chain": {"k": 1, "extra": 2}, "setup": None, "Weird Key": 1}
    out = E.diff_fields(a, b)
    assert out == {"f.vol_60m": 1, "chain.extra(one_side_only)": 1, "<other>(one_side_only)": 1}
    assert "2.5" not in json.dumps(out) and "1.5" not in json.dumps(out)


# ------------------------------------------------------------------------------------------------------------------------ report-only trim counters
EXPECTED_PASS_RULE = (
    "PASS iff md5_equal (decision-log md5 and cards md5 of run1 == run2 == run3, and equal entry counts) AND in each run: hook_errors == 0, leakage_refused / "
    "model_exception / ledger_unreadable / cost_mismatch / late_reply == 0, fallbacks == 0, entries L == R == S == periods_ge3 and U >= periods_ge3, periods_ge3 > 0 "
    "AND peak_rss_mb (max of the three "
    "processes) < rss_cap_mb AND period_ms_p95 (max of runs 1 and 2; tick at t_d + tick at t_e) < 15000 AND tick_ms_p95 (max of runs 1 and 2) < 1000; run 3 "
    "(other PYTHONHASHSEED; hv7_runner has no fast path to switch off) is tested for md5 only, its timing is report-only")


def test_pass_rule_text_is_unchanged_and_report_fields_do_not_affect_the_verdict():
    assert D.PASS_RULE == EXPECTED_PASS_RULE
    bad = {**CLEAN, "engine_trim_safety_ok": False, "engine_trim_exercised": False, "engine_rows_below_trim_floor": 5}
    v = D.verdict(bad, dict(CLEAN), dict(CLEAN), 100.0, 200.0)
    assert v["result"] == "PASS" and v["pass_rule"] == EXPECTED_PASS_RULE


def test_trim_report_flags():
    assert D.trim_report({}) == {**{k: 0 for k in D.TRIM_COUNTER_KEYS}, "engine_trim_safety_ok": True, "engine_trim_exercised": False}
    r = D.trim_report({"engine_hist_trimmed_prints": 3, "engine_trims": 2, "quote_pools_swept": 4})
    assert r["engine_trim_safety_ok"] and r["engine_trim_exercised"] and r["quote_pools_swept"] == 4
    assert not D.trim_report({"engine_rows_below_trim_floor": 1})["engine_trim_safety_ok"]
    assert not D.trim_report({"engine_refused_history_trimmed": 1})["engine_trim_safety_ok"]


def test_one_run_copies_the_runner_counters_and_exit_reasons(tmp_path, tape, monkeypatch):
    seen = {}
    orig = R.Hv7Runner.sync_engine_counters

    def spy(self):
        orig(self)
        seen["run"] = self

    monkeypatch.setattr(R.Hv7Runner, "sync_engine_counters", spy)
    res = D.one_run(tmp_path / "d", "real-a", SHA, [DAY], tape, start_ms=START, end_ms=END)
    run = seen["run"]
    for k in D.TRIM_COUNTER_KEYS:
        assert res[k] == run.counters.get(k, 0) and isinstance(res[k], int)
    assert res["engine_trim_safety_ok"] is (res["engine_rows_below_trim_floor"] == 0 and res["engine_refused_history_trimmed"] == 0)
    assert res["engine_trim_exercised"] is (res["engine_hist_trimmed_prints"] > 0)
    ecr = res["exit_reason_counts"]
    assert set(ecr) == set(R.ARM_ORDER)
    rows = {a: {r["position_id"]: r["exit_reason"] for r in run.engine.ledger.read_bot(R.bot_id(a)) if r.get("kind") == "trade"} for a in R.ARM_ORDER}
    for a in R.ARM_ORDER:
        assert sum(ecr[a].values()) == len(rows[a])
        for why, n in ecr[a].items():
            assert n == sum(1 for w in rows[a].values() if w == why)


def test_run_real_report_has_per_run_trim_report_and_stdout_omits_exit_reasons(tmp_path, tape, monkeypatch, capsys):
    monkeypatch.setattr(D, "DRILL_ROOT", tmp_path / "drill")
    monkeypatch.setattr(D, "REAL_TAPE", str(tape))
    monkeypatch.setattr(D, "seal_read_only", lambda base: None)
    real_one = D.one_run
    monkeypatch.setattr(D, "one_run", lambda base, tag, sha, days, tp, **kw: real_one(base, tag, sha, days, tp, start_ms=START, end_ms=END))
    res = D.run_real([DAY], "t2", SHA, tape_dir=str(tape), isolate=False, rss_cap_mb=1e9)
    assert res["pass_rule"] == EXPECTED_PASS_RULE
    rep = res["engine_trim_report"]
    assert set(rep) == {"run1", "run2", "run3"}
    for x in rep.values():
        assert {*D.TRIM_COUNTER_KEYS, "engine_trim_safety_ok", "engine_trim_exercised", "exit_reason_counts"} <= set(x)
    on_disk = json.loads((tmp_path / "drill" / "t2" / "real-layout-result.json").read_text())
    assert on_disk["engine_trim_report"] == rep
    printed = capsys.readouterr().out
    assert "engine_trim_safety_ok" in printed and "exit_reason_counts" not in printed
