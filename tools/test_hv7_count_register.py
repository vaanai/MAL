"""HV7 C3a tests: tools/hv7_count.py and tools/hv7_register.py. Synthetic rows and registries only; the real bots.json and decision logs are never opened."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from tools import arena_paper
from tools import hv7_count as K
from tools import hv7_register as G
from tools import hv7_runner as R
from tools.arena_declog import DecisionLog
from tools.test_hv7_resume import mod_env  # noqa: F401
from tools.test_hv7_runner import _env  # noqa: F401

DAY1, DAY2 = 1_790_000_000_000, 1_790_000_000_000 + 86_400_000
END = DAY2 + 86_400_000

@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch):
    """The drill is 0 API: any socket connect in these tests fails the test (own guard, independent of the runner test file's fixture)."""
    import socket

    def no_net(*a, **k):
        raise AssertionError("network used in a drill test")

    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)


def test_sockets_are_blocked_in_this_file():
    import socket

    with pytest.raises(AssertionError):
        socket.socket().connect(("127.0.0.1", 9))



def fill(root: Path):
    log = R.Hv7DecisionLog(root)
    for ms in (DAY1, DAY1 + 3_600_000, DAY2):
        for arm in R.ARM_ORDER:
            log.append(arm, ms, {"kind": "entry", "order_id": f"{arm}{ms}", "pool": "P", "est_ret": 0.5})
    # L rank rows: 1 ok, 2 fallback, 1 excluded (restart)
    log.append("L", DAY1, {"kind": "rank", "fallback": False})
    log.append("L", DAY1 + 3_600_000, {"kind": "rank", "fallback": True})
    log.append("L", DAY2, {"kind": "rank", "fallback": True})
    log.append("L", DAY2 + 1, {"kind": "rank", "fallback": True, "excluded": True})
    log.append("R", DAY2, {"kind": "sell", "pid": "x", "est_ret": -9.0})
    return log


def test_count_attempts_fallbacks_excluded_and_days(tmp_path, mod_env):
    fill(tmp_path / "v7")
    c = K.count(tmp_path / "v7", clock_start_ms=0, clock_end_ms=END)
    assert c["attempts"] == {"L": 3, "R": 3, "S": 3, "U": 3}
    assert (c["rank_rows"], c["fallbacks"], c["excluded_periods"]) == (3, 2, 1)
    assert c["fallback_share"] == pytest.approx(2 / 3) and c["fallback_share_gt_10pct"] is True
    assert c["nonzero_days"] == {"any": 2, "L": 2, "R": 2, "S": 2, "U": 2}
    assert c["torn_lines"] == 0 and c["a_l"] == 3 and c["a_l_lt_20"] is True
    assert c["excluded_share"] == pytest.approx(1 / 4) and c["excluded_gt_10pct"] is True and c["not_evaluable"] is True
    assert K.count(tmp_path / "v7", clock_start_ms=0, clock_end_ms=DAY2)["attempts"]["L"] == 2          # the end is exclusive
    assert K.count(tmp_path / "v7", clock_start_ms=DAY2, clock_end_ms=END)["attempts"]["L"] == 1


def test_count_ignores_the_late_reply_counter_and_reads_no_outcome_field(tmp_path, mod_env):
    fill(tmp_path / "v7")
    src = Path(K.__file__).read_text()
    code = src.split('"""', 2)[2]
    assert "late_reply" not in code and "est_ret" not in code and "counters" not in code and "pnl" not in code.lower()
    out = io.StringIO()
    assert K.main(["--root", str(tmp_path / "v7"), "--clock-end-ms", str(END)], out=out) == 0
    printed = json.loads(out.getvalue())
    assert set(printed) == {"attempts", "a_l", "a_l_lt_20", "rank_rows", "fallbacks", "fallback_share", "fallback_share_gt_10pct", "excluded_periods", "excluded_share",
                            "excluded_gt_10pct", "not_evaluable", "nonzero_days", "torn_lines", "clock_start_ms", "clock_end_ms"}
    with pytest.raises(SystemExit):
        K.main(["--root", str(tmp_path / "v7")], out=io.StringIO())      # --clock-end-ms is required


def test_a_cut_off_call_counts_as_a_fallback_not_an_exclusion(tmp_path, mod_env):
    log = R.Hv7DecisionLog(tmp_path / "v7")
    for i in range(10):
        log.append("L", DAY1 + i, {"kind": "rank", "fallback": i == 0, "cut_off": i == 0})
    c = K.count(tmp_path / "v7", clock_start_ms=0, clock_end_ms=END)
    assert (c["fallbacks"], c["excluded_periods"], c["rank_rows"]) == (1, 0, 10) and c["fallback_share_gt_10pct"] is False and c["not_evaluable"] is False


def test_count_counts_a_torn_line_and_does_not_raise(tmp_path, mod_env):
    log = fill(tmp_path / "v7")
    f = log.files("U")[-1]
    with open(f, "ab") as fh:
        fh.write(b'{"kind":"ent')
    assert K.count(tmp_path / "v7", clock_start_ms=0, clock_end_ms=END)["torn_lines"] == 1


def test_count_empty_root_is_all_zero_and_no_share(tmp_path, mod_env):
    c = K.count(tmp_path / "none", clock_start_ms=0, clock_end_ms=END)
    assert c["attempts"] == {a: 0 for a in R.ARM_ORDER} and c["fallback_share"] is None and c["fallback_share_gt_10pct"] is None


# ------------------------------------------------------------------------------------------------------------------------ register
NOW, CLOCK, HALT, SHA = 1_000_000, 1793664000000, 1794873600000, "a" * 40      # 2026-11-03T00Z and its H = clock + 14 d (pins revised 2026-10-30)


def run_reg(argv, reg):
    out, err = io.StringIO(), io.StringIO()
    rc = G.main(["--sha", SHA, "--clock-start-ms", str(CLOCK), "--entries-halt-ms", str(HALT), *argv], out=out, err=err, _now_ms=NOW, _verify_sha=False)
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    monkeypatch.setenv(arena_paper.TEST_ENV, "1")


def test_bot_ids_and_models_are_pinned():
    assert G.HV7_BOTS == ("hv7.0t-L", "hv7.0t-R", "hv7.0t-S", "hv7.0t-U")
    assert G.HV7_MODELS["hv7.0t-L"] and all(G.HV7_MODELS[b] == [] for b in G.HV7_BOTS[1:])


def test_dry_run_is_the_default_and_writes_nothing(tmp_path):
    reg = tmp_path / "bots.json"
    rc, out, err = run_reg(["--registry", str(reg)], reg)
    assert rc == 0 and "DRY RUN" in out and not reg.exists()
    for pin in (SHA, str(CLOCK), str(HALT), "hv7.0t-U", "0.3", "3.0", G.confirm_phrase(SHA, CLOCK, HALT)):
        assert pin in out


def test_execute_registers_four_books_and_rerun_is_a_noop(tmp_path):
    reg = tmp_path / "bots.json"
    rc, out, err = run_reg(["--registry", str(reg), "--execute"], reg)
    assert rc == 0 and sorted(arena_paper.load_registry(reg)) == list(G.HV7_BOTS)
    before = reg.read_bytes()
    assert run_reg(["--registry", str(reg), "--execute"], reg)[0] == 0 and reg.read_bytes() == before


def test_live_registry_needs_the_exact_confirm_and_the_arena_path_is_refused_without_it(tmp_path, monkeypatch):
    rc, out, err = run_reg(["--live-registry", "--execute"], None)
    assert rc == 2 and err.strip() == "Refused"
    rc, out, err = run_reg(["--live-registry", "--execute", "--confirm", G.confirm_phrase(SHA, CLOCK, HALT + 1)], None)
    assert rc == 2                                           # a phrase for other pins does not work
    assert run_reg(["--registry", "/data/mal/arena/bots.json", "--execute"], None)[0] == 2
    assert run_reg([], None)[0] == 2                          # no registry and no --live-registry
    monkeypatch.setattr(arena_paper, "REGISTRY_PATH", str(tmp_path / "live.json"))
    monkeypatch.setattr(G.arena_paper, "REGISTRY_PATH", str(tmp_path / "live.json"))
    rc, *_ = run_reg(["--live-registry", "--execute", "--confirm", G.confirm_phrase(SHA, CLOCK, HALT)], None)
    assert rc == 0 and sorted(arena_paper.load_registry(tmp_path / "live.json")) == list(G.HV7_BOTS)


@pytest.mark.parametrize("bad", [["--sha", "xyz"], ["--sha", "a" * 7], ["--sha", "a" * 39], ["--sha", "a" * 41], ["--clock-start-ms", "500"], ["--clock-start-ms", "1793664000001"],
                                 ["--clock-start-ms", "1794096000001"], ["--entries-halt-ms", "2000000"], ["--entries-halt-ms", "1794873600001"],
                                 ["--clock-start-ms", "1793491200000"], ["--clock-start-ms", "1793577600000"],          # the old 11-01 / 11-02 clocks
                                 ["--clock-start-ms", "1794182400000"],                                                  # 11-09: not pinned
                                 ["--entries-halt-ms", "1794355200000"],                                                 # the old H 2026-11-11T00Z
                                 ["--entries-halt-ms", "1794960000000"],                                                 # the H of the 11-04 clock
                                 ["--clock-start-ms", "1793750400000"]])                                                 # 11-04 clock with 11-03's H
def test_bad_pins_are_refused(tmp_path, bad):
    argv = ["--sha", SHA, "--clock-start-ms", str(CLOCK), "--entries-halt-ms", str(HALT), "--registry", str(tmp_path / "r.json"), "--execute"]
    for i in range(0, len(bad), 2):
        argv[argv.index(bad[i]) + 1] = bad[i + 1]
    out, err = io.StringIO(), io.StringIO()
    assert G.main(argv, out=out, err=err, _now_ms=NOW, _verify_sha=False) == 2 and not (tmp_path / "r.json").exists()


def test_a_changed_value_for_a_registered_id_is_refused_and_masked(tmp_path):
    reg = tmp_path / "bots.json"
    assert run_reg(["--registry", str(reg), "--execute"], reg)[0] == 0
    out, err = io.StringIO(), io.StringIO()
    c2 = G.CLOCKS_MS[1]
    rc = G.main(["--sha", SHA, "--clock-start-ms", str(c2), "--entries-halt-ms", str(G.halt_for(c2)), "--registry", str(reg), "--execute"], out=out, err=err, _now_ms=NOW, _verify_sha=False)
    assert rc == 1 and err.getvalue().strip() == "ArenaRefused hv7.0t-L"        # the other allowed clock is still a different value for a registered id


def test_register_all_refuses_a_foreign_id(tmp_path):
    with pytest.raises(G.Refused):
        G.register_all(SHA, CLOCK, tmp_path / "r.json", bot_ids=("hv6.0-L",), now_ms=NOW, verify_sha=False)


def test_clocks_and_halt_are_the_pinned_instants():
    from datetime import datetime, timezone

    ms = lambda y, m, d: int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)
    assert G.CLOCKS_MS == tuple(ms(2026, 11, d) for d in range(3, 9)) and G.H_DAYS == 14 and G.DECIDING_STAKE == 0.25
    assert [G.halt_for(c) for c in G.CLOCKS_MS] == [ms(2026, 11, d) for d in range(17, 23)]
    assert all(G.halt_for(c) - c == 14 * 86_400_000 for c in G.CLOCKS_MS) and not hasattr(G, "HALT_MS")
    for c in (ms(2026, 11, 1), ms(2026, 11, 2), ms(2026, 11, 9), G.CLOCKS_MS[0] + 1):
        with pytest.raises(G.Refused):
            G.halt_for(c)


def test_every_pinned_clock_registers_with_its_own_halt_and_no_other(tmp_path):
    for c in G.CLOCKS_MS:
        G.check_pins(SHA, c, G.halt_for(c), now_ms=NOW)
        for other in G.CLOCKS_MS:
            if other != c:
                with pytest.raises(G.Refused):
                    G.check_pins(SHA, c, G.halt_for(other), now_ms=NOW)
    with pytest.raises(G.Refused):                       # a pinned clock that has already passed is still refused (no backdating)
        G.check_pins(SHA, G.CLOCKS_MS[0], G.halt_for(G.CLOCKS_MS[0]), now_ms=G.CLOCKS_MS[0] + 1)
    out, err = io.StringIO(), io.StringIO()
    c = G.CLOCKS_MS[-1]
    assert G.main(["--sha", SHA, "--clock-start-ms", str(c), "--entries-halt-ms", str(G.halt_for(c)), "--registry", str(tmp_path / "r.json")], out=out, err=err,
                  _now_ms=NOW, _verify_sha=False) == 0
    assert G.confirm_phrase(SHA, c, G.halt_for(c)) in out.getvalue() and "1795305600000" in out.getvalue()


def test_halt_is_stored_in_the_registry_and_the_runner_main_checks_it(tmp_path, monkeypatch, capsys):
    reg = tmp_path / "bots.json"
    assert run_reg(["--registry", str(reg), "--execute"], reg)[0] == 0
    assert sorted(arena_paper.load_registry(reg)) == list(G.HV7_BOTS)           # the extra lines do not disturb the loader
    assert all(v == {"clock_start_ms": CLOCK, "entries_halt_ms": HALT} for v in R.registered_pins(reg).values())
    assert run_reg(["--registry", str(reg), "--execute"], reg)[0] == 0           # idempotent: one hv7_halt line per book
    assert sum(1 for x in reg.read_text().splitlines() if '"hv7_halt"' in x) == 4
    import tools.hv6_agents as A

    def boom(*a, **k):
        raise AssertionError("built")

    monkeypatch.setattr(A, "CallerModel", boom)
    monkeypatch.setattr(R, "Hv7Guard", boom)
    base = ["--tape", str(tmp_path), "--root", str(tmp_path / "v7"), "--frozen-sha", SHA, "--feed-delay-slots", "2", "--owner-cost-line-ref", "q_6_kzYJva9XXuYQ", "--registry", str(reg)]
    assert R.main(base + ["--clock-start-ms", str(CLOCK), "--entries-halt-ms", str(HALT + 1)]) == 2        # H differs from the registry
    assert R.main(base + ["--clock-start-ms", str(CLOCK + 1), "--entries-halt-ms", str(HALT)]) == 2
    assert R.main(base[:-2] + ["--registry", str(tmp_path / "none.json"), "--clock-start-ms", str(CLOCK), "--entries-halt-ms", str(HALT)]) == 2     # nothing registered
    assert "refused" in capsys.readouterr().err
