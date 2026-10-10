"""Tests for tools/cap_pick_oracle.py on synthetic decision records. Nothing here reads /data/mal, /var/lib/mal or a real row."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools import cap_pick_oracle as co

REPO = Path(__file__).resolve().parents[1]
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
SCORE, PNL, PRICE, FEAT = "0.987654321", "-123456789", "4.2424242e-08", "777.5551"
SENTINELS = (SCORE, PNL, PRICE, FEAT, "score", "pnl", "price", "features", "positions", "outcome")


def mint(i: int) -> str:
    return ("P" + "".join(B58[(i >> (5 * k)) % len(B58)] for k in range(3))).ljust(44, "x")


def gate_line(m: str, entered: bool, reason: str | None = None, **extra) -> str:
    """Same keys as tools/forward_exp012_gate.gate_row, written like the runner's JsonlLog (compact separators)."""
    row = {"schema": "forward_paper_exp012_gate_v1", "book": "exp012_migrate_tp50_sl30", "mint": m, "mig_ms": 1_792_112_400_000,
           "decision_t_ms": 1_792_112_400_123, "score": float(SCORE), "threshold": 0.8030766588450794, "entered": entered,
           "reason": reason if reason is not None else (None if entered else "below_threshold"), "error": None,
           "time_fallbacks": {"create_sig_match": 1}, "features": {"f1": float(FEAT), "price_return_pre": float(PRICE)}}
    row.update(extra)
    return json.dumps(row, separators=(",", ":"))


def intent_line(m: str) -> str:
    return json.dumps({"schema": "forward_paper_intent_v1", "book": "exp012_migrate_tp50_sl30", "ledger": "ceiling", "mint": m, "creator": "C" * 44,
                       "decision_t_ms": 1, "written_ms": 2, "trigger": "migrate", "score": float(SCORE), "runner_kill": False,
                       "migration_slot": 5, "migration_slot_src": "migrate_tx"}, separators=(",", ":"))


def arm_line(m: str) -> str:
    return json.dumps({"schema": "forward_paper_arm_v1", "book": "b", "mint": m, "score": float(SCORE)}, separators=(",", ":"))


def replay_line(m: str, entered: bool, kind: str = "decision") -> str:
    if kind == "dead":
        return json.dumps({"schema": "cap_pick_gate_replay_v1", "kind": "dead", "view": "walk2", "mint": m, "first_pumpswap_ms": 1, "day": "2026-10-16",
                           "decision": "pre_restart"})
    return json.dumps({"schema": "cap_pick_gate_replay_v1", "kind": "decision", "view": "walk2", "mint": m, "day": "2026-10-16", "mig_ms": 1,
                       "score": float(SCORE), "decision": "pick" if entered else "below", "entered": entered, "error": None, "time_fallbacks": None})


def write(p: Path, lines: list[str]) -> Path:
    p.write_text("".join(x + "\n" for x in lines))
    return p


T_OPEN = 1_792_117_800_000  # 2026-10-16T02:30:00Z: after the exporter's FINAL clock floor (02:00Z)


class Clock:
    def __init__(self, t: int = T_OPEN):
        self.t = t

    def __call__(self) -> int:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += int(s * 1000)


@pytest.fixture
def marker(tmp_path: Path) -> Path:
    """The FINAL marker, present (the manager writes it after the DEC-016 FINAL)."""
    m = tmp_path / "FINAL_WRITTEN"
    m.write_text("")
    return m


def rows(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines()]


# --- the narrow parser ---------------------------------------------------------------------------------------------------


def test_extract_gate_reads_only_mint_and_entered():
    a, b = mint(1), mint(2)
    assert co.extract("gate", gate_line(a, True)) == (a, True)
    assert co.extract("gate", gate_line(b, False)) == (b, False)


def test_extract_gate_error_other_schema_and_ambiguous_lines_are_not_decisions():
    a = mint(3)
    assert co.extract("gate", gate_line(a, False, reason="gate_error", error="ValueError")) is None  # undecided, fail closed
    assert co.extract("gate", arm_line(a)) is None
    assert co.extract("gate", gate_line(a, True, other={"entered": False})) is None  # two flags on one line: not trusted
    assert co.extract("gate", gate_line(a, True, extra_mint={"mint": mint(4)})) is None  # two mints on one line
    assert co.extract("gate", '{"schema":"forward_paper_exp012_gate_v1","mint":"%s","ent' % a) is None  # half-written


def test_extract_intents_are_picks_and_arm_rows_are_ignored():
    a = mint(5)
    assert co.extract("intents", intent_line(a)) == (a, True)
    assert co.extract("intents", arm_line(a)) is None


def test_extract_replay_decision_and_dead():
    a, b, c = mint(6), mint(7), mint(8)
    assert co.extract("replay", replay_line(a, True)) == (a, True)
    assert co.extract("replay", replay_line(b, False)) == (b, False)
    assert co.extract("replay", replay_line(c, False, kind="dead")) == (c, False)
    assert co.extract("replay", json.dumps({"schema": "cap_pick_gate_replay_v1", "kind": "meta", "view": "w", "days": ["2026-10-16"]})) is None


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        co.extract("positions", "{}")


def test_decision_row_takes_only_a_bool_and_a_base58_mint():
    assert json.loads(co.decision_row(mint(1), True, 5)) == {"mint": mint(1), "pick": True, "t_ms": 5}
    for bad in (1, 0, "true", None, 0.9):
        with pytest.raises(ValueError):
            co.decision_row(mint(1), bad, 5)
    with pytest.raises(ValueError):
        co.decision_row("not a mint", True, 5)


# --- exporter -----------------------------------------------------------------------------------------------------------


def test_no_non_boolean_field_ever_leaves_the_exporter(tmp_path, marker):
    a, b, c, d = mint(1), mint(2), mint(3), mint(4)
    gate = write(tmp_path / "g.jsonl", [gate_line(a, True), gate_line(b, False), gate_line(c, False, reason="gate_error", error="X")])
    ints = write(tmp_path / "i.jsonl", [intent_line(a), intent_line(d), arm_line(b)])
    out = tmp_path / "picks.jsonl"
    clk = Clock()
    co.run_export([("gate", gate), ("intents", ints)], out, final_marker=marker, once=True, now_ms=clk)
    got = rows(out)
    for r in got:
        assert set(r) == {"mint", "pick", "t_ms"}
        assert isinstance(r["pick"], bool) and isinstance(r["t_ms"], int)
    assert {(r["mint"], r["pick"]) for r in got} == {(a, True), (b, False), (d, True)}  # c: gate_error, undecided; b stays false (arm ignored)
    blob = out.read_text()
    for s in SENTINELS + ("decision_t_ms", "mig_ms", "reason", "entered", "creator", "book"):
        assert s not in blob


def test_sources_with_hostile_extra_fields_still_export_booleans_only(tmp_path, marker):
    a = mint(9)
    line = gate_line(a, True, pnl_sol=float(PNL), positions=[1, 2], outcome="won", fill_price=float(PRICE))
    gate = write(tmp_path / "g.jsonl", [line])
    out = tmp_path / "picks.jsonl"
    co.run_export([("gate", gate)], out, final_marker=marker, once=True, now_ms=Clock())
    assert rows(out) == [{"mint": a, "pick": True, "t_ms": T_OPEN}]
    for s in SENTINELS:
        assert s not in out.read_text()


def test_idempotent_restart_and_sticky_true(tmp_path, marker):
    a, b = mint(1), mint(2)
    gate = write(tmp_path / "g.jsonl", [gate_line(a, False), gate_line(a, False), gate_line(b, True)])
    out = tmp_path / "picks.jsonl"
    co.run_export([("gate", gate)], out, final_marker=marker, once=True, now_ms=Clock())
    assert len(rows(out)) == 2  # the repeated false is not written twice
    co.run_export([("gate", gate)], out, final_marker=marker, once=True, now_ms=Clock())  # restart: re-reads the source from the start
    assert len(rows(out)) == 2
    with gate.open("a") as fh:
        fh.write(gate_line(a, True) + "\n" + gate_line(b, False) + "\n")  # a flips to a pick; b was a pick and stays one
    co.run_export([("gate", gate)], out, final_marker=marker, once=True, now_ms=Clock())
    assert [(r["mint"], r["pick"]) for r in rows(out)] == [(a, False), (b, True), (a, True)]


def test_heartbeat_only_when_every_source_is_readable(tmp_path, marker):
    a = mint(1)
    gate = write(tmp_path / "g.jsonl", [gate_line(a, False)])
    out = tmp_path / "picks.jsonl"
    clk = Clock()
    n = co.run_export([("gate", gate)], out, final_marker=marker, hb_s=5, poll_s=1, max_seconds=12, now_ms=clk, sleep=clk.sleep)
    hb = [r for r in rows(out) if "hb" in r]
    assert n["heartbeats_written"] == len(hb) == 3  # t = 0, 5, 10 s
    assert all(set(r) == {"hb", "t_ms"} and r["hb"] is True for r in hb)
    out2 = tmp_path / "picks2.jsonl"
    clk2 = Clock()
    n2 = co.run_export([("gate", gate), ("intents", tmp_path / "missing.jsonl")], out2, final_marker=marker, hb_s=1, poll_s=1, max_seconds=5,
                       now_ms=clk2, sleep=clk2.sleep)
    # a lost source never beats: the reader goes stale. An intents error is not counted (quant-proof E1): no printed count is intents-derived
    assert n2["heartbeats_written"] == 0 and n2["source_errors"] == 0
    clk3 = Clock()
    n3 = co.run_export([("gate", tmp_path / "missing-gate.jsonl")], tmp_path / "picks3.jsonl", final_marker=marker, hb_s=1, poll_s=1,
                       max_seconds=3, now_ms=clk3, sleep=clk3.sleep)
    assert n3["heartbeats_written"] == 0 and n3["source_errors"] > 0
    assert [r for r in rows(out2) if "hb" in r] == []


def test_once_and_replay_only_runs_write_no_heartbeat(tmp_path, marker):
    rep = write(tmp_path / "r.jsonl", [replay_line(mint(1), True), replay_line(mint(2), False)])
    out = tmp_path / "picks-replay.jsonl"
    n = co.run_export([("replay", rep)], out, final_marker=marker, once=True, now_ms=Clock())
    assert n["heartbeats_written"] == 0 and n["decisions_written"] == 2
    assert all("hb" not in r for r in rows(out))


def test_source_rotation_and_a_half_written_line(tmp_path):
    a, b = mint(1), mint(2)
    gate = tmp_path / "g.jsonl"
    gate.write_text(gate_line(a, True) + "\n" + gate_line(b, False)[:40])
    t = co.Tailer(gate)
    lines, reset = t.read_new()
    assert len(lines) == 1 and not reset
    with gate.open("a") as fh:
        fh.write(gate_line(b, False)[40:] + "\n")
    lines, reset = t.read_new()
    assert len(lines) == 1 and co.extract("gate", lines[0]) == (b, False)
    gate.unlink()
    gate.write_text(gate_line(a, False) + "\n")
    lines, reset = t.read_new()
    assert reset and len(lines) == 1


def test_output_lines_match_the_executors_two_regexes():
    """tools/h5_executor.py JsonlPickOracle reads a row only when it has exactly one mint match and one pick match."""
    mint_re = re.compile(r'"mint"\s*:\s*"([1-9A-HJ-NP-Za-km-z]{32,44})"')
    pick_re = re.compile(r'"pick"\s*:\s*(true|false)')
    d = co.decision_row(mint(1), True, 1)
    h = co.heartbeat_row(1)
    assert len(mint_re.findall(d)) == 1 and len(pick_re.findall(d)) == 1
    assert mint_re.findall(h) == [] and pick_re.findall(h) == []  # a heartbeat is invisible to the executor's reader


# --- reader -------------------------------------------------------------------------------------------------------------


def picks_file(p: Path, decisions: list[tuple[str, bool]], hb_t: int | None, t0: int = 1_792_112_400_000) -> Path:
    lines = [co.decision_row(m, f, t0) for m, f in decisions]
    if hb_t is not None:
        lines.append(co.heartbeat_row(hb_t))
    return write(p, lines)


def test_reader_true_false_and_undecided(tmp_path):
    clk = Clock()
    a, b, c = mint(1), mint(2), mint(3)
    live = picks_file(tmp_path / "picks.jsonl", [(a, True), (b, False)], clk.t)
    o = co.PickOracle([live], now_ms=clk)
    assert o(a) is True
    assert o(b) is False
    assert o(c) is None  # undecided is never False
    assert o.suppress(a) is True and o.suppress(b) is False and o.suppress(c) is True and o.suppress(None) is True


def test_reader_is_sticky_true_across_later_false_rows_and_rotation(tmp_path):
    clk = Clock()
    a = mint(1)
    live = picks_file(tmp_path / "picks.jsonl", [(a, True)], clk.t)
    o = co.PickOracle([live], now_ms=clk)
    assert o(a) is True
    with live.open("a") as fh:
        fh.write(co.decision_row(a, False, clk.t) + "\n")
    assert o(a) is True  # a later false never undoes a pick
    live.unlink()
    picks_file(live, [(mint(2), False)], clk.t)  # rotated file that no longer holds the pick
    assert o(a) is True and o(mint(2)) is False


def test_reader_union_of_live_and_replay(tmp_path):
    clk = Clock()
    a, b, c, d = mint(1), mint(2), mint(3), mint(4)
    live = picks_file(tmp_path / "picks.jsonl", [(a, False), (b, True), (c, False)], clk.t)
    rep = write(tmp_path / "picks-replay.jsonl", [co.decision_row(a, True, 1), co.decision_row(b, False, 1), co.decision_row(d, False, 1)])
    o = co.PickOracle([live], [rep], now_ms=clk)
    assert o(a) is True  # the replay picked what the live gate called below: the union is a pick
    assert o(b) is True  # live pick stays a pick whatever the replay says
    assert o(c) is False
    assert o(d) is False  # decided only by the replay: not a pick
    assert o(mint(5)) is None


def test_reader_replay_file_may_be_absent_or_grow_later(tmp_path):
    clk = Clock()
    a = mint(1)
    live = picks_file(tmp_path / "picks.jsonl", [(a, False)], clk.t)
    rep = tmp_path / "picks-replay.jsonl"
    o = co.PickOracle([live], [rep], now_ms=clk)
    assert o(a) is False  # no replay file yet: the live answer stands
    write(rep, [co.decision_row(a, True, 1)])
    assert o(a) is True


def test_stale_feed_answers_none_except_for_a_known_pick(tmp_path):
    clk = Clock()
    a, b = mint(1), mint(2)
    live = picks_file(tmp_path / "picks.jsonl", [(a, True), (b, False)], clk.t)
    o = co.PickOracle([live], now_ms=clk, stale_s=60)
    assert o(b) is False and o.staleness_s() == 0.0
    clk.t += 59_000
    assert o(b) is False
    clk.t += 2_000  # 61 s since the last heartbeat
    assert o.staleness_s() == 61.0
    assert o(b) is None and o(mint(9)) is None
    assert o(a) is True  # a pick cannot flip, so it stays True
    with live.open("a") as fh:
        fh.write(co.heartbeat_row(clk.t) + "\n")
    assert o(b) is False  # a fresh heartbeat revives the feed


def test_no_heartbeat_at_all_is_stale_and_a_missing_live_file_is_none(tmp_path):
    clk = Clock()
    a = mint(1)
    live = picks_file(tmp_path / "picks.jsonl", [(a, False)], None)
    o = co.PickOracle([live], now_ms=clk)
    assert o.staleness_s() is None and o(a) is None
    gone = co.PickOracle([tmp_path / "nope.jsonl"], now_ms=clk)
    assert gone(a) is None and gone.staleness_s() is None and gone.suppress(a) is True


def test_a_heartbeat_from_the_future_is_a_clock_fault(tmp_path):
    clk = Clock()
    a = mint(1)
    live = picks_file(tmp_path / "picks.jsonl", [(a, False)], clk.t + 60_000)
    assert co.PickOracle([live], now_ms=clk)(a) is None
    live2 = picks_file(tmp_path / "picks2.jsonl", [(a, False)], clk.t + 3_000)  # within the 5 s skew allowance
    assert co.PickOracle([live2], now_ms=clk)(a) is False


def test_every_live_source_must_be_fresh(tmp_path):
    clk = Clock()
    a = mint(1)
    l1 = picks_file(tmp_path / "p1.jsonl", [(a, False)], clk.t)
    l2 = picks_file(tmp_path / "p2.jsonl", [], clk.t - 120_000)
    assert co.PickOracle([l1, l2], now_ms=clk)(a) is None


def test_final_marker_gates_everything_and_nothing_is_opened_before_it(tmp_path):
    clk = Clock()
    a = mint(1)
    live = picks_file(tmp_path / "picks.jsonl", [(a, False)], clk.t)
    marker = tmp_path / "FINAL_WRITTEN"
    o = co.PickOracle([live], now_ms=clk, final_marker=marker)
    assert o(a) is None and o.staleness_s() is None
    marker.write_text("")
    assert o(a) is False


def test_reader_never_raises_and_never_holds_a_score_or_pnl(tmp_path):
    clk = Clock()
    a = mint(1)
    live = tmp_path / "picks.jsonl"
    # a richer file than the exporter writes: the reader still keeps only the booleans
    live.write_text(json.dumps({"mint": a, "pick": True, "score": float(SCORE), "pnl_sol": int(PNL), "positions": [1]}) + "\n" + co.heartbeat_row(clk.t) + "\n")
    o = co.PickOracle([live], now_ms=clk)
    assert o(a) is True
    held = repr(vars(o)) + "".join(repr(vars(s)) for s in o._src)
    for s in (SCORE, PNL, "positions"):
        assert s not in held
    assert o(12345) is None  # type error inside the call: fail closed, no raise
    d = co.PickOracle([tmp_path], now_ms=clk)  # a directory is not a file
    assert d(a) is None


def test_lines_with_two_flags_or_two_mints_are_not_trusted(tmp_path):
    clk = Clock()
    a, b = mint(1), mint(2)
    live = tmp_path / "picks.jsonl"
    live.write_text(json.dumps({"mint": a, "pick": True, "x": {"pick": False}}) + "\n" + json.dumps({"mint": b, "nested": {"mint": a}, "pick": True}) + "\n"
                    + co.heartbeat_row(clk.t) + "\n")
    o = co.PickOracle([live], now_ms=clk)
    assert o(a) is None and o(b) is None


# --- environment oracle ---------------------------------------------------------------------------------------------------


def test_from_env_without_a_marker_or_a_live_file_is_closed(tmp_path):
    clk = Clock()
    live = picks_file(tmp_path / "picks.jsonl", [(mint(1), False)], clk.t)
    assert co.from_env({"CAP_PICK_LIVE": str(live)})(mint(1)) is None  # no CAP_PICK_FINAL_MARKER: closed
    assert co.from_env({})(mint(1)) is None
    assert co.from_env({}).suppress(mint(1)) is True and co.from_env({}).staleness_s() is None


def test_default_oracle_reads_the_environment_lazily(tmp_path, monkeypatch):
    live = picks_file(tmp_path / "picks.jsonl", [(mint(1), False), (mint(2), True)], int(__import__("time").time() * 1000))
    rep = write(tmp_path / "r.jsonl", [co.decision_row(mint(1), True, 1)])
    marker = tmp_path / "FINAL_WRITTEN"
    marker.write_text("")
    monkeypatch.setenv("CAP_PICK_LIVE", str(live))
    monkeypatch.setenv("CAP_PICK_REPLAY", str(rep))
    monkeypatch.setenv("CAP_PICK_FINAL_MARKER", str(marker))
    o = co._LazyEnvOracle()
    assert o(mint(1)) is True and o(mint(2)) is True and o(mint(3)) is None
    assert o.staleness_s() is not None and o.staleness_s() < 60
    assert o.suppress(mint(3)) is True
    assert isinstance(co.default_oracle, co._LazyEnvOracle)  # `--pick-oracle tools.cap_pick_oracle:default_oracle`


# --- CLI and the sh wrapper -----------------------------------------------------------------------------------------------


def test_cli_export_once_and_check(tmp_path, capsys, marker, monkeypatch):
    a = mint(1)
    gate = write(tmp_path / "g.jsonl", [gate_line(a, True)])
    out = tmp_path / "picks.jsonl"
    with pytest.raises(SystemExit):
        co.main(["export", "--gate-log", str(gate), "--out", str(out), "--once"])  # argparse: --final-marker is required
    capsys.readouterr()
    monkeypatch.setattr(co, "EXPORT_EARLIEST_MS", 0)  # test only: the code floor is 2026-10-16T02:00Z, after this test's wall clock
    assert co.main(["export", "--gate-log", str(gate), "--out", str(out), "--final-marker", str(marker), "--once"]) == 0
    counts = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert counts["decisions_written"] == 1
    assert a not in capsys.readouterr().out  # counts only
    assert co.main(["export", "--out", str(out), "--final-marker", str(marker)]) == 2  # no source
    assert co.main(["export", "--gate-log", str(tmp_path / "nope.jsonl"), "--out", str(out), "--final-marker", str(marker)]) == 2
    capsys.readouterr()
    assert co.main(["check", "--live", str(out)]) == 1  # --once wrote no heartbeat: stale
    chk = json.loads(capsys.readouterr().out)
    assert chk["fresh"] is False and chk["decided_mints"] == 1 and a not in json.dumps(chk)


def test_cli_once_refuses_before_the_final_gate_and_reads_nothing(tmp_path, capsys, marker, monkeypatch):
    gate = write(tmp_path / "g.jsonl", [gate_line(mint(1), True)])
    out = tmp_path / "picks.jsonl"
    opened: list[str] = []
    monkeypatch.setattr(co.Tailer, "read_new", lambda self, *a, **k: opened.append(str(self.path)) or ([], False))
    # no marker: refused whatever the clock says
    assert co.main(["export", "--gate-log", str(gate), "--out", str(out), "--final-marker", str(tmp_path / "absent"), "--once"]) == 3
    # a marker, but the clock is before the floor (patched to the far future so this holds whenever the suite runs)
    monkeypatch.setattr(co, "EXPORT_EARLIEST_MS", 10**15)
    assert co.main(["export", "--gate-log", str(gate), "--out", str(out), "--final-marker", str(marker), "--once"]) == 3
    assert opened == [] and not out.exists()
    assert "refusing" in capsys.readouterr().err


def _floor_shim(tmp_path: Path) -> Path:
    """A CAP_PICK_PYTHON stand-in for the wrapper tests: drops `-m tools.cap_pick_oracle`, lowers the FINAL clock floor (test only), runs the CLI."""
    shim = tmp_path / "py-shim"
    shim.write_text("#!/bin/sh\nshift 2\nexec \"$REAL_PY\" -c 'import sys; from tools import cap_pick_oracle as c; c.EXPORT_EARLIEST_MS = 0; "
                    "sys.exit(c.main(sys.argv[1:]))' \"$@\"\n")
    shim.chmod(0o755)
    return shim


def test_wrapper_is_posix_sh_and_runs_end_to_end(tmp_path, marker):
    sh = REPO / "scripts" / "research" / "cap-pick-oracle.sh"
    assert subprocess.run(["sh", "-n", str(sh)]).returncode == 0
    text = sh.read_text()
    assert text.startswith("#!/bin/sh") and "${PIPESTATUS" not in text and "[[" not in text
    a, b = mint(1), mint(2)
    gate = write(tmp_path / "exp012-gate.jsonl", [gate_line(a, True), gate_line(b, False)])
    env = {**os.environ, "CAP_PICK_OUT": str(tmp_path / "out"), "CAP_PICK_GATE_LOG": str(gate), "CAP_PICK_INTENTS": str(tmp_path / "no-intents.jsonl"),
           "CAP_PICK_PYTHON": str(_floor_shim(tmp_path)), "REAL_PY": sys.executable, "CAP_PICK_FINAL_MARKER": str(marker)}
    r = subprocess.run(["sh", str(sh), "--max-seconds", "0.3", "--poll-s", "0.05", "--hb-s", "0.1"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = rows(tmp_path / "out" / "picks.jsonl")
    assert {(x["mint"], x["pick"]) for x in got if "mint" in x} == {(a, True), (b, False)}
    assert any("hb" in x for x in got)
    assert a not in r.stdout + r.stderr  # no mint is printed
    # a missing gate log is refused before anything starts
    env2 = {**env, "CAP_PICK_GATE_LOG": str(tmp_path / "missing.jsonl")}
    assert subprocess.run(["sh", str(sh), "--once"], env=env2, capture_output=True, text=True).returncode == 2
    # no FINAL marker variable: refused before python starts
    env3 = {k: v for k, v in env.items() if k != "CAP_PICK_FINAL_MARKER"}
    r3 = subprocess.run(["sh", str(sh), "--once"], env=env3, capture_output=True, text=True)
    assert r3.returncode == 2 and "CAP_PICK_FINAL_MARKER" in r3.stderr
    # the real interpreter (no shim) with a marker that is not there: exit 3, nothing written
    env4 = {**env, "CAP_PICK_PYTHON": sys.executable, "CAP_PICK_OUT": str(tmp_path / "out4"), "CAP_PICK_FINAL_MARKER": str(tmp_path / "absent")}
    r4 = subprocess.run(["sh", str(sh), "--once"], env=env4, capture_output=True, text=True, timeout=60)
    assert r4.returncode == 3, r4.stderr
    assert not (tmp_path / "out4" / "picks.jsonl").exists()


def test_wrapper_replay_mode_converts_a_decision_list_to_booleans(tmp_path, marker):
    sh = REPO / "scripts" / "research" / "cap-pick-oracle.sh"
    a, b, c = mint(1), mint(2), mint(3)
    rep = write(tmp_path / "replay.jsonl", [replay_line(a, True), replay_line(b, False), replay_line(c, False, kind="dead")])
    env = {**os.environ, "CAP_PICK_OUT": str(tmp_path / "out"), "CAP_PICK_REPLAY_IN": str(rep), "CAP_PICK_PYTHON": str(_floor_shim(tmp_path)),
           "REAL_PY": sys.executable, "CAP_PICK_FINAL_MARKER": str(marker)}
    r = subprocess.run(["sh", str(sh)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = rows(tmp_path / "out" / "picks-replay.jsonl")
    assert {(x["mint"], x["pick"]) for x in got} == {(a, True), (b, False), (c, False)}
    assert all(set(x) == {"mint", "pick", "t_ms"} for x in got)
    blob = (tmp_path / "out" / "picks-replay.jsonl").read_text()
    for s in SENTINELS:
        assert s not in blob


# --- the FINAL gate (EXP-022 s9 "Nothing before the FINAL"; DEC-016:95) ----------------------------------------------------------


def test_final_gate_constant_is_2026_10_16T02Z():
    import datetime as dt

    assert co.EXPORT_EARLIEST_MS == 1_792_116_000_000
    assert dt.datetime.fromtimestamp(co.EXPORT_EARLIEST_MS / 1000, dt.timezone.utc).isoformat() == "2026-10-16T02:00:00+00:00"


def test_exporter_opens_nothing_and_creates_nothing_before_the_final_gate(tmp_path, monkeypatch):
    gate = write(tmp_path / "g.jsonl", [gate_line(mint(1), True)])
    out = tmp_path / "o" / "picks.jsonl"
    marker = tmp_path / "FINAL_WRITTEN"

    def no_read(self, *a, **k):
        raise AssertionError("a source was opened before the FINAL gate")

    monkeypatch.setattr(co.Tailer, "read_new", no_read)
    # (1) no marker, clock after the floor; (2) a marker, clock still before the floor when the 60 s budget ends; (3) no marker given at all
    for t, make, given in ((T_OPEN, False, marker), (co.EXPORT_EARLIEST_MS - 120_001, True, marker), (T_OPEN, True, None)):
        if make:
            marker.write_text("")
        elif marker.exists():
            marker.unlink()
        clk = Clock(t)
        n = co.run_export([("gate", gate)], out, final_marker=given, hb_s=1, poll_s=1, max_seconds=60, now_ms=clk, sleep=clk.sleep)
        assert n["decisions_written"] == n["heartbeats_written"] == n["lines"] == 0 and n["final_wait_s"] >= 60
        assert not out.exists() and not out.parent.exists()
        with pytest.raises(co.FinalNotWritten):
            co.run_export([("gate", gate)], out, final_marker=given, once=True, now_ms=Clock(t))
    assert not co.final_gate_open(tmp_path, T_OPEN) and not co.final_gate_open(tmp_path / "absent", T_OPEN)  # a directory is not the marker


def test_a_waiting_exporter_starts_when_the_marker_appears(tmp_path):
    a = mint(1)
    gate = write(tmp_path / "g.jsonl", [gate_line(a, True)])
    out = tmp_path / "picks.jsonl"
    marker = tmp_path / "FINAL_WRITTEN"
    clk = Clock(co.EXPORT_EARLIEST_MS - 30_000)  # 30 s before the floor, no marker yet
    waits: list[float] = []

    def sleep(s: float) -> None:
        waits.append(s)
        if len(waits) == 2:
            marker.write_text("")  # the manager writes it after the FINAL
        clk.sleep(s)

    n = co.run_export([("gate", gate)], out, final_marker=marker, hb_s=5, poll_s=1, max_seconds=120, now_ms=clk, sleep=sleep)
    assert waits[:3] == [co.FINAL_WAIT_POLL_S] * 3  # stat-only waits until both the marker and the floor hold (t = -30, -20, -10 s)
    assert n["decisions_written"] == 1 and n["heartbeats_written"] >= 1 and n["final_wait_s"] == 30
    assert [(r["mint"], r["pick"]) for r in rows(out) if "mint" in r] == [(a, True)]
    assert all(r["t_ms"] >= co.EXPORT_EARLIEST_MS for r in rows(out))


def test_tailer_does_not_open_an_unchanged_file_and_refuses_a_non_regular_one(tmp_path, monkeypatch):
    f = write(tmp_path / "p.jsonl", [co.heartbeat_row(1)])
    t = co.Tailer(f)
    assert len(t.read_new()[0]) == 1
    real_open = Path.open

    def guarded(self, *a, **k):
        if self == f:
            raise AssertionError("opened an unchanged file")
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", guarded)
    assert t.read_new() == ([], False)
    monkeypatch.setattr(Path, "open", real_open)
    with f.open("a") as fh:
        fh.write(co.heartbeat_row(2) + "\n")
    assert len(t.read_new()[0]) == 1
    with pytest.raises(OSError):
        co.Tailer(tmp_path).read_new()


# --- review fixes on #509: frozen book only, scored-row-only False, no intents-derived count, staleness cap ----------------------


OTHER_BOOK = "exp012_other_book"


def test_rows_of_another_book_are_rejected():
    a = mint(40)
    assert co.CAP_PICK_BOOK == "exp012_migrate_tp50_sl30"
    assert co.extract("gate", gate_line(a, False, book=OTHER_BOOK)) is None
    assert co.extract("gate", gate_line(a, True, book=OTHER_BOOK)) is None
    other_intent = intent_line(a).replace('"book":"exp012_migrate_tp50_sl30"', '"book":"%s"' % OTHER_BOOK)
    assert '"book":"%s"' % OTHER_BOOK in other_intent
    assert co.extract("intents", other_intent) is None
    no_book = json.loads(gate_line(a, False))
    del no_book["book"]
    assert co.extract("gate", json.dumps(no_book, separators=(",", ":"))) is None
    # two book keys (one of them the frozen book) are not trusted
    assert co.extract("gate", gate_line(a, False, nested={"book": OTHER_BOOK})) is None
    assert co.extract("intents", intent_line(a)[:-1] + ',"x":{"book":"%s"}}' % OTHER_BOOK) is None


def test_a_second_books_not_pick_never_answers_false_for_the_frozen_books_pick(tmp_path, marker):
    a = mint(41)
    gate = write(tmp_path / "exp012-gate.jsonl", [gate_line(a, False, book=OTHER_BOOK)])
    out = tmp_path / "picks.jsonl"
    clk = Clock()
    n = co.run_export([("gate", gate)], out, final_marker=marker, once=True, now_ms=clk)
    assert n["decisions_written"] == 0 and n["rejected"] == 1
    with out.open("a") as fh:
        fh.write(co.heartbeat_row(clk.t) + "\n")
    o = co.PickOracle([out], now_ms=clk)
    assert o(a) is None  # undecided, never False
    with gate.open("a") as fh:
        fh.write(gate_line(a, True) + "\n")
        fh.write(gate_line(a, False, book=OTHER_BOOK) + "\n")
    co.run_export([("gate", gate)], out, final_marker=marker, once=True, now_ms=clk)
    with out.open("a") as fh:
        fh.write(co.heartbeat_row(clk.t) + "\n")
    assert o(a) is True
    assert all(r.get("pick") is not False for r in rows(out) if "mint" in r)


def test_a_gate_not_pick_needs_a_computed_score():
    a = mint(42)
    assert co.extract("gate", gate_line(a, False)) == (a, False)
    assert co.extract("gate", gate_line(a, False, score=-0.25)) == (a, False)
    assert co.extract("gate", gate_line(a, False, score=1e-05)) == (a, False)
    for reason in ("no_features", "no_bond_history", "below_threshold"):
        assert co.extract("gate", gate_line(a, False, reason=reason, score=None)) is None  # undecided: offline may still pick it
    assert co.extract("gate", gate_line(a, False, score=float("nan"))) is None
    assert co.extract("gate", gate_line(a, False, score=float("inf"))) is None
    no_score = json.loads(gate_line(a, False))
    del no_score["score"]
    assert co.extract("gate", json.dumps(no_score, separators=(",", ":"))) is None
    assert co.extract("gate", gate_line(a, False, score=None, x={"score": 0.5})) is None  # a nested score does not count
    assert co.extract("gate", gate_line(a, False, x={"score": 0.5})) is None  # two score keys: not trusted
    assert co.extract("gate", gate_line(a, True, score=None)) == (a, True)  # a pick needs no score (True always refuses)


def test_no_printed_count_includes_an_intents_line(tmp_path, marker, capsys):
    a, b = mint(43), mint(44)
    gate = write(tmp_path / "g.jsonl", [gate_line(a, True), gate_line(b, False)])
    intents = write(tmp_path / "i.jsonl", [intent_line(a), arm_line(a), arm_line(b), "junk", intent_line(mint(45))])
    n1 = co.run_export([("gate", gate)], tmp_path / "p1.jsonl", final_marker=marker, once=True, now_ms=Clock())
    n2 = co.run_export([("gate", gate), ("intents", intents)], tmp_path / "p2.jsonl", final_marker=marker, once=True, now_ms=Clock())
    for k in ("lines", "rejected", "source_errors"):
        assert n1[k] == n2[k], k
    assert n2["lines"] == 2 and n2["rejected"] == 0
    with pytest.raises(ValueError):
        co.run_export([("intents", intents)], tmp_path / "p3.jsonl", final_marker=marker, once=True, now_ms=Clock())
    assert not (tmp_path / "p3.jsonl").exists()
    rc = co.main(["export", "--intents", str(intents), "--out", str(tmp_path / "p4.jsonl"), "--final-marker", str(marker), "--once"])
    assert rc == 2 and not (tmp_path / "p4.jsonl").exists()
    assert "--intents needs --gate-log" in capsys.readouterr().err


def test_from_env_caps_staleness_at_60_s(tmp_path):
    live = write(tmp_path / "p.jsonl", [co.heartbeat_row(1)])
    base = {"CAP_PICK_LIVE": str(live), "CAP_PICK_FINAL_MARKER": str(tmp_path / "m")}
    assert co.from_env(base).stale_s == 60.0
    for raw, want in (("10", 10.0), ("60", 60.0), ("61", 60.0), ("3600", 60.0), ("", 60.0)):
        assert co.from_env({**base, "CAP_PICK_STALE_S": raw}).stale_s == want, raw
    for raw in ("inf", "-inf", "nan", "abc"):  # a configuration error: from_env raises, the lazy C1-NF oracle answers None (refuse)
        with pytest.raises(ValueError):
            co.from_env({**base, "CAP_PICK_STALE_S": raw})
        lazy = co._LazyEnvOracle()
        with pytest.MonkeyPatch.context() as mp:
            for k, v in {**base, "CAP_PICK_STALE_S": raw}.items():
                mp.setenv(k, v)
            assert lazy(mint(47)) is None and lazy.suppress(mint(47)) is True and lazy.staleness_s() is None


def test_a_nan_staleness_limit_fails_closed(tmp_path):
    a = mint(46)
    clk = Clock()
    live = picks_file(tmp_path / "p.jsonl", [(a, False)], hb_t=clk.t)
    assert co.PickOracle([live], now_ms=clk)(a) is False
    assert co.PickOracle([live], now_ms=clk, stale_s=float("nan"))(a) is None


def test_wrapper_creates_nothing_before_the_final_gate(tmp_path):
    src = (REPO / "scripts/research/cap-pick-oracle.sh").read_text()
    assert "mkdir" not in "".join(ln for ln in src.splitlines(keepends=True) if not ln.lstrip().startswith("#"))
    assert "ProtectHome=tmpfs" in src and "/srv/mal-h5-shadow/cap-pick/picks.jsonl" in src
    assert "the readers use the same path" not in src and "pick_file points at it" not in src
