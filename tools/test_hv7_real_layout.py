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
    orig = R.ArenaState._hour_files

    def spy(self):
        got = orig(self)
        opened.extend(t[0] for t in got)
        return got

    monkeypatch.setattr(R.ArenaState, "_hour_files", spy)
    D.one_run(tmp_path / "d", "real-a", SHA, ["2026-09-24"], tape, start_ms=START, end_ms=START + 10 * MIN)     # a day that has no hour file in this tape
    # the filter hands the reader no hour of 2026-09-25 when the chosen day is 2026-09-24 (the spy sees the unfiltered list; the filtered one is empty)
    run = D.new_run(tmp_path / "d2", "real-a", SHA, ["2026-09-24"], tape, start_ms=START, end_ms=END)
    assert run.state._hour_files() == []
    run = D.new_run(tmp_path / "d3", "real-a", SHA, [DAY], tape, start_ms=START, end_ms=END)
    assert run.state._hour_files() != []


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


def test_verdict_fails_on_md5_rss_period_tick_and_vacuous():
    base = {"log_md5": "m", "cards_md5": "c", "entries": {"L": 1}, "periods_ge3": 3, "period_ms_p95": 10.0, "tick_ms_p95": 1.0}
    assert D.verdict(base, dict(base), dict(base), 100.0, 200.0)["result"] == "PASS"
    assert D.verdict(base, {**base, "log_md5": "x"}, dict(base), 100.0, 200.0)["result"] == "FAIL"
    assert D.verdict(base, dict(base), {**base, "log_md5": "x"}, 100.0, 200.0)["md5_equal_run3_other_hashseed"] is False
    assert D.verdict(base, dict(base), dict(base), 300.0, 200.0)["rss_ok"] is False
    assert D.verdict({**base, "period_ms_p95": 15000.0}, dict(base), dict(base), 1.0, 2.0)["period_p95_ok"] is False
    assert D.verdict(base, {**base, "tick_ms_p95": 1000.0}, dict(base), 1.0, 2.0)["tick_p95_ok"] is False
    assert D.verdict({**base, "periods_ge3": 0}, dict(base), dict(base), 1.0, 2.0)["result"] == "FAIL"
    assert D.verdict(base, dict(base), {**base, "run3": 1, "period_ms_p95": 1e9, "tick_ms_p95": 1e9}, 1.0, 2.0)["result"] == "PASS"      # run 3 timing is report-only


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
    with pytest.raises(D.Refused):
        P.run_p0(["2026-10-21"], "p1", tape_dir=str(tmp_path))
    with pytest.raises(D.Refused):
        P.run_p0(["2026-10-21"], "../p1")
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
