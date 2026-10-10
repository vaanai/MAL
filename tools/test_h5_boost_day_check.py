"""Tests for tools/h5_boost_day_check.py: fixture hourly shadow files in a temp dir, a recording fake of subprocess.run for STOP.
No sudo, no network, no real shadow data."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import h5_boost_day_check as bdc

SCRIPT = Path(bdc.__file__).resolve()
SENTINEL = "SENTINEL_OUTCOME_VALUE_9137"


def pool(mint: str, sec: float | None, cls: str = "plain", sealed: bool = False, **extra) -> dict:
    """A pool-close record shaped like tools/h5_shadow.py _close(): `type` first, compact separators, outcome-ish keys present."""
    syn, src = {"plain": (False, "rpc"), "syn": (True, "ws"), "syn_rpc": (True, "rpc"), "uncl": (None, None)}[cls]
    rec = {"type": "pool", "reason": "horizon", "pool": "P" + mint, "mint": mint, "s0": 1000, "s0_block_time": 1_790_000_000,
           "boost_slices": 70, "boost_last_slice_s": sec, "boost_last_slice_s_blocktime": None, "boost_last_slice_s_recv": None,
           "min_q_pv_sol": 31.5, "min_q_fv_sol": 30.1, "triggered": ["pv"], "sealed": sealed, "synthetic": syn, "synthetic_src": src,
           "gap": False, "boost_src": "pda", "v": 1, "schema": "h5_shadow_v1", "t_ms": 1}
    rec.update(extra)
    return rec


def write_hour(d: Path, day: str, hour: int, recs: list[dict], raw_lines: list[str] = ()) -> None:
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"h5-shadow-{day}T{hour:02d}.jsonl", "a") as fh:
        for r in recs:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        for s in raw_lines:
            fh.write(s + "\n")


def many(prefix: str, n: int, sec: float, cls: str = "plain") -> list[dict]:
    return [pool(f"{prefix}{i}", sec, cls) for i in range(n)]


class FakeRun:
    def __init__(self, rc: int = 0):
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.rc)


def run(tmp_path: Path, capsys, *args: str, fake: FakeRun | None = None) -> tuple[int, dict]:
    fake = fake if fake is not None else FakeRun()
    argv = ["--shadow-dir", str(tmp_path / "shadow"), "--log", str(tmp_path / "log.jsonl"), *args]
    code = bdc.main(argv, run=fake)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    return code, json.loads(out[0])


DAY = ["--mode", "day", "--day", "2026-10-11", "--now-utc", "2026-10-12T00:05:00Z"]


# --- day mode -----------------------------------------------------------------------------------------------------------------------
def test_day_plain_only_below_337_halts(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 3, many("p", 31, 336.0, "plain") + many("s", 40, 345.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY)
    assert code == 3 and rec["halt_due"] is True and rec["verdict"] == "halt_due"
    assert rec["plain"] == {"n": 31, "median_s": 336.0}
    assert rec["all"]["n"] == 71 and rec["all"]["median_s"] >= 337
    assert "plain_median_lt_337" in rec["reasons"] and "all_median_lt_337" not in rec["reasons"]
    assert rec["stop"] is None  # no --place-stop: nothing placed


def test_day_all_only_below_337_halts(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 0, many("p", 30, 338.0, "plain"))
    write_hour(sh, "2026-10-11", 23, many("s", 40, 330.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY)
    assert code == 3 and rec["halt_due"] is True
    assert rec["plain"]["median_s"] == 338.0 and rec["all"]["median_s"] == 330.0 and rec["syn"]["n"] == 40
    assert "all_median_lt_337" in rec["reasons"] and "plain_median_lt_337" not in rec["reasons"]
    assert rec["hours_read"] == [0, 23] and len(rec["hours_missing"]) == 22


def test_day_both_at_or_above_337_no_halt(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 5, many("p", 30, 337.0, "plain") + many("s", 30, 350.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY)
    assert code == 0 and rec["halt_due"] is False and rec["evaluated"] is True and rec["verdict"] == "no_halt"
    assert rec["reasons"] == []


def test_day_median_is_exact_not_rounded(tmp_path, capsys):
    # 30 plain: 15 at 336.999 and 15 at 337.000 -> median 336.9995 < 337 (a 3-decimal rounding would read 337.0)
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 1, many("a", 15, 336.999) + many("b", 15, 337.0) + many("s", 30, 360.0, "syn"))
    code, rec = run(tmp_path, capsys, *DAY)
    assert code == 3 and rec["plain"]["median_s"] == 336.9995


@pytest.fixture
def evaluated_1010(tmp_path):
    write_hour(tmp_path / "shadow", "2026-10-10", 7, many("e", 30, 340.0) + many("f", 30, 340.0, "syn"))


def test_day_unevaluated_then_unevaluated_halts_via_log(tmp_path, capsys, evaluated_1010):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 2, many("p", 10, 340.0) + many("s", 40, 340.0, "syn"))  # plain n=10 < 30
    write_hour(sh, "2026-10-12", 2, many("q", 29, 340.0) + many("t", 40, 340.0, "syn"))
    code1, rec1 = run(tmp_path, capsys, *DAY)
    assert code1 == 0 and rec1["evaluated"] is False and rec1["halt_due"] is False and rec1["verdict"] == "unevaluated"
    assert "unevaluated_lt_30_pools" in rec1["reasons"]
    # 10-10 has no day-mode line in the log (day mode never runs on the first-strike day), so it is recomputed from its files
    assert rec1["prev_day"] == {"day": "2026-10-10", "evaluated": True, "source": "files"}
    code2, rec2 = run(tmp_path, capsys, "--mode", "day", "--day", "2026-10-12", "--now-utc", "2026-10-13T00:05:00Z")
    assert code2 == 3 and rec2["halt_due"] is True
    assert rec2["prev_day"] == {"day": "2026-10-11", "evaluated": False, "source": "log"}
    assert "two_consecutive_unevaluated_days" in rec2["reasons"]
    lines = (tmp_path / "log.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1]) == rec2


def test_day_unevaluated_after_evaluated_prev_from_files_no_halt(tmp_path, capsys, evaluated_1010):
    write_hour(tmp_path / "shadow", "2026-10-11", 2, many("p", 5, 300.0))
    code, rec = run(tmp_path, capsys, *DAY)
    assert code == 0 and rec["halt_due"] is False
    assert rec["prev_day"] == {"day": "2026-10-10", "evaluated": True, "source": "files"}


def test_day_unevaluated_prev_missing_from_log_and_files_halts(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 2, many("p", 5, 340.0))
    code, rec = run(tmp_path, capsys, *DAY)
    assert code == 3 and rec["prev_day"] == {"day": "2026-10-10", "evaluated": False, "source": "files"}


def test_day_log_takes_latest_line_for_prev_day(tmp_path, capsys, evaluated_1010):
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"tool": "h5_boost_day_check", "mode": "day", "day": "2026-10-10", "evaluated": True}) + "\n"
                   + "not json\n"
                   + json.dumps({"tool": "h5_boost_day_check", "mode": "day", "day": "2026-10-10", "evaluated": False}) + "\n"
                   + json.dumps({"tool": "other", "mode": "day", "day": "2026-10-10", "evaluated": True}) + "\n")
    write_hour(tmp_path / "shadow", "2026-10-11", 2, many("p", 5, 340.0))
    code, rec = run(tmp_path, capsys, *DAY)
    assert code == 3 and rec["prev_day"] == {"day": "2026-10-10", "evaluated": False, "source": "log"}


def test_day_no_files_is_unevaluated_input_error(tmp_path, capsys, evaluated_1010):
    code, rec = run(tmp_path, capsys, *DAY)  # 10-10 has files and is evaluated; 10-11 has none
    assert code == 2 and rec["error"] == "no_files" and rec["evaluated"] is False and rec["halt_due"] is False
    # the next day, also without files: two unevaluated days in a row -> halt, fail closed
    code2, rec2 = run(tmp_path, capsys, "--mode", "day", "--day", "2026-10-12", "--now-utc", "2026-10-13T00:05:00Z")
    assert code2 == 3 and rec2["halt_due"] is True and rec2["prev_day"]["source"] == "log"


def test_day_missing_dir_fails_closed(tmp_path, capsys):
    code, rec = run(tmp_path, capsys, *DAY)
    assert code == 3 and rec["error"] == "shadow_dir_missing" and rec["evaluated"] is False  # prev (10-10) unreadable too -> unevaluated


@pytest.mark.parametrize("day,now,err", [
    ("2026-10-10", "2026-10-12T00:00:00Z", "day_not_after_first_strike_day"),
    ("2026-10-09", "2026-10-12T00:00:00Z", "day_not_after_first_strike_day"),
    ("2026-10-11", "2026-10-11T23:59:59Z", "day_not_complete"),
])
def test_day_refuses_first_strike_day_and_incomplete_day(tmp_path, capsys, day, now, err):
    write_hour(tmp_path / "shadow", day, 0, many("p", 40, 300.0))
    code, rec = run(tmp_path, capsys, "--mode", "day", "--day", day, "--now-utc", now, "--place-stop")
    assert code == 2 and rec["error"] == err


def test_day_requires_day(tmp_path, capsys):
    code, rec = run(tmp_path, capsys, "--mode", "day", "--now-utc", "2026-10-12T00:05:00Z")
    assert code == 2 and rec["error"] == "day_required"


# --- running mode -------------------------------------------------------------------------------------------------------------------
RUN = ["--mode", "running", "--now-utc", "2026-10-11T12:00:00Z"]


def test_running_plain_below_335_halts(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 334.9) + many("s", 60, 345.0, "syn"))
    code, rec = run(tmp_path, capsys, *RUN)
    assert code == 3 and rec["reasons"] == ["plain_median_lt_335"]


def test_running_all_below_335_halts(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 10, 340.0) + many("s", 30, 330.0, "syn"))
    code, rec = run(tmp_path, capsys, *RUN)
    assert code == 3 and rec["reasons"] == ["all_median_lt_335"]  # plain n=10 is not judged


def test_running_under_30_not_judged_and_337_is_not_a_running_halt(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 29, 300.0) + many("s", 40, 336.0, "syn"))
    code, rec = run(tmp_path, capsys, *RUN)
    assert code == 0 and rec["halt_due"] is False and rec["all"]["median_s"] == 336.0


def test_running_day_must_be_today(tmp_path, capsys):
    code, rec = run(tmp_path, capsys, *RUN, "--day", "2026-10-10")
    assert code == 2 and rec["error"] == "running_day_not_today"


def test_running_no_files_is_input_error(tmp_path, capsys):
    (tmp_path / "shadow").mkdir()
    code, rec = run(tmp_path, capsys, *RUN)
    assert code == 2 and rec["error"] == "no_files" and rec["halt_due"] is False


# --- resume mode --------------------------------------------------------------------------------------------------------------------
RES = ["--mode", "resume", "--now-utc", "2026-10-11T00:10:00Z"]


def test_resume_ok(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-10", 0, many("p", 30, 337.0) + many("s", 30, 342.0, "syn"))
    fake = FakeRun()
    code, rec = run(tmp_path, capsys, *RES, "--place-stop", fake=fake)
    assert code == 0 and rec["resume_ok"] is True and rec["verdict"] == "resume_ok" and rec["day"] == "2026-10-10"
    assert fake.calls == [] and rec["stop"] is None


@pytest.mark.parametrize("recs,now,reason", [
    (many("p", 30, 336.4) + many("s", 30, 342.0, "syn"), "2026-10-11T00:10:00Z", "plain_median_lt_337"),
    (many("p", 30, 338.0) + many("s", 40, 330.0, "syn"), "2026-10-11T00:10:00Z", "all_median_lt_337"),
    (many("p", 29, 340.0) + many("s", 30, 342.0, "syn"), "2026-10-11T00:10:00Z", "plain_lt_30_pools"),
    (many("p", 30, 340.0) + many("s", 30, 342.0, "syn"), "2026-10-10T23:59:00Z", "day_not_complete"),
])
def test_resume_not_ok(tmp_path, capsys, recs, now, reason):
    write_hour(tmp_path / "shadow", "2026-10-10", 0, recs)
    fake = FakeRun()
    code, rec = run(tmp_path, capsys, "--mode", "resume", "--now-utc", now, "--place-stop", fake=fake)
    assert code == 4 and rec["resume_ok"] is False and reason in rec["reasons"] and fake.calls == []


def test_resume_only_first_strike_day(tmp_path, capsys):
    code, rec = run(tmp_path, capsys, *RES, "--day", "2026-10-09")
    assert code == 2 and rec["error"] == "resume_day_not_first_strike_day"


def test_resume_no_files_is_input_error(tmp_path, capsys):
    code, rec = run(tmp_path, capsys, *RES)
    assert code == 2 and rec["resume_ok"] is False and rec["error"] == "shadow_dir_missing"


# --- reading: allowlist, dedupe, sealed, seconds ------------------------------------------------------------------------------------
def test_field_allowlist_drops_outcome_like_keys():
    raw = json.dumps(pool("m1", 340.0, pnl_sol=SENTINEL, exit_px=SENTINEL, fills=[{"sol": SENTINEL}], nested={"type": "pool", "mint": "x"}),
                     separators=(",", ":")).encode()
    rec = bdc.parse_pool_line(raw)
    assert set(rec) <= bdc.ALLOWED_KEYS
    assert "min_q_pv_sol" not in rec and "triggered" not in rec and "gap" not in rec and "boost_src" not in rec
    assert SENTINEL not in json.dumps(rec)


def test_non_pool_records_are_counted_never_parsed_and_never_printed(tmp_path, capsys, monkeypatch):
    parsed: list[bytes] = []
    real = bdc.parse_pool_line

    def spy(raw):
        parsed.append(raw)
        return real(raw)

    monkeypatch.setattr(bdc, "parse_pool_line", spy)
    outcome = {"type": "outcome", "mint": "o1", "pnl_sol": SENTINEL, "boost_last_slice_s": 1.0, "sealed": False}
    trigger = {"type": "trigger", "mint": "t1", "q_trigger_sol": SENTINEL, "boost_last_slice_s": 1.0}
    excluded = {"type": "excluded", "mint": "x1", "reason": "synthetic", "boost_last_slice_s": 1.0}
    pools = many("p", 30, 340.0) + [pool("q", 341.0, pnl_sol=SENTINEL)]
    lines = [json.dumps(r, separators=(",", ":")) for r in (outcome, trigger, excluded, outcome)]
    # a pool line whose `type` is not the leading key is still parsed (through the allowlist hook)
    late_type = json.dumps({"mint": "late", "pnl_sol": SENTINEL, "type": "pool", "sealed": False, "synthetic": False,
                            "synthetic_src": "rpc", "boost_last_slice_s": 342.0}, separators=(",", ":"))
    write_hour(tmp_path / "shadow", "2026-10-11", 9, pools, raw_lines=lines + [late_type, "{broken"])
    code, rec = run(tmp_path, capsys, *RUN)
    assert rec["lines_by_type"] == {"excluded": 1, "outcome": 2, "pool": 32, "trigger": 1}
    assert rec["plain"]["n"] == 32 and rec["all"]["n"] == 32 and rec["skipped"]["bad_line"] == 1
    assert all(b'"type":"pool"' in r or r.startswith(b"{broken") for r in parsed) and len(parsed) == 33
    blob = (tmp_path / "log.jsonl").read_text()
    assert SENTINEL not in blob and "o1" not in blob and "t1" not in blob and "x1" not in blob
    assert "pnl" not in blob and "q_trigger" not in blob and "min_q" not in blob


def test_output_names_no_pool_or_mint(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 0, many("MINTNAME", 30, 340.0))
    code, rec = run(tmp_path, capsys, *RUN)
    assert "MINTNAME" not in json.dumps(rec) and "PMINTNAME" not in json.dumps(rec)


def test_dedupe_by_mint_first_valid_record(tmp_path, capsys):
    sh = tmp_path / "shadow"
    write_hour(sh, "2026-10-11", 1, [pool("dup", 2500.0)] + many("p", 29, 340.0))  # first record of dup: bad seconds, not kept
    write_hour(sh, "2026-10-11", 2, [pool("dup", 300.0), pool("dup", 400.0, "syn")])
    write_hour(sh, "2026-10-11", 3, [pool("dup", 500.0)])
    code, rec = run(tmp_path, capsys, *RUN)
    assert rec["plain"]["n"] == 30 and rec["all"]["n"] == 30 and rec["syn"]["n"] == 0
    assert rec["skipped"]["dup_mint"] == 2 and rec["skipped"]["bad_sec"] == 1


def test_sealed_and_no_mint_skipped(tmp_path, capsys):
    sh = tmp_path / "shadow"
    sealed = [pool(f"z{i}", 100.0, sealed=True) for i in range(40)]
    sealed_missing = [{k: v for k, v in pool("y", 100.0).items() if k != "sealed"}]
    write_hour(sh, "2026-10-11", 1, sealed + sealed_missing + [pool("", 100.0), {**pool("n", 100.0), "mint": None}] + many("p", 30, 340.0))
    code, rec = run(tmp_path, capsys, *RUN)
    assert code == 0 and rec["plain"] == {"n": 30, "median_s": 340.0}
    assert rec["skipped"]["sealed"] == 41 and rec["skipped"]["no_mint"] == 2 and rec["pool_records"] == 73


def test_seconds_choice_mirrors_executor():
    p = bdc.pick_seconds
    assert p({"boost_last_slice_s": 338.12345}) == 338.123
    assert p({"boost_last_slice_s": None, "boost_last_slice_s_blocktime": 339, "boost_last_slice_s_recv": 1.0}) == 339.0
    assert p({"boost_last_slice_s": True, "boost_last_slice_s_blocktime": None, "boost_last_slice_s_recv": 341.5}) == 341.5  # bool skipped
    assert p({"boost_last_slice_s": 0, "boost_last_slice_s_blocktime": 339}) is None  # first number out of range: no fall-through
    assert p({"boost_last_slice_s": 2000.0}) is None and p({"boost_last_slice_s": float("nan")}) is None
    assert p({"boost_last_slice_s": "340"}) is None and p({}) is None


def test_classes():
    assert bdc.pool_class({"synthetic": False, "synthetic_src": "rpc"}) == "plain"
    assert bdc.pool_class({"synthetic": False, "synthetic_src": "ws"}) == "other"
    assert bdc.pool_class({"synthetic": 0, "synthetic_src": "rpc"}) == "other"
    assert bdc.pool_class({"synthetic": True, "synthetic_src": "rpc"}) == "syn"
    assert bdc.pool_class({}) == "other"


def test_symlinked_hour_file_is_not_read(tmp_path, capsys):
    other = tmp_path / "elsewhere"
    write_hour(other, "2026-10-11", 0, many("p", 40, 300.0))
    sh = tmp_path / "shadow"
    sh.mkdir()
    (sh / "h5-shadow-2026-10-11T00.jsonl").symlink_to(other / "h5-shadow-2026-10-11T00.jsonl")
    code, rec = run(tmp_path, capsys, *RUN)
    assert code == 2 and rec["error"] == "no_files"


# --- STOP --------------------------------------------------------------------------------------------------------------------------
def test_place_stop_not_invoked_without_flag(tmp_path, capsys, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(subprocess, "run", boom)
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 330.0))
    argv = ["--shadow-dir", str(tmp_path / "shadow"), "--log", str(tmp_path / "log.jsonl"), *RUN]
    assert bdc.main(argv) == 3  # the default runner is subprocess.run, looked up at call time
    capsys.readouterr()
    fake = FakeRun()
    code, rec = run(tmp_path, capsys, *RUN, fake=fake)
    assert code == 3 and fake.calls == [] and rec["stop"] is None and rec["place_stop_flag"] is False


def test_place_stop_with_flag_touches_then_tests_never_removes(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 330.0))
    fake = FakeRun()
    code, rec = run(tmp_path, capsys, *RUN, "--place-stop", fake=fake)
    assert code == 3
    assert fake.calls == [["sudo", "-n", "touch", "/var/lib/mal-live/h5/STOP"], ["sudo", "-n", "test", "-e", "/var/lib/mal-live/h5/STOP"]]
    assert rec["stop"] == {"path": "/var/lib/mal-live/h5/STOP", "touch_rc": 0, "test_rc": 0, "present": True}


def test_place_stop_failure_is_logged_and_still_halt(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 330.0))
    code, rec = run(tmp_path, capsys, *RUN, "--place-stop", fake=FakeRun(rc=1))
    assert code == 3 and rec["stop"]["present"] is False and rec["stop"]["touch_rc"] == 1


def test_place_stop_flag_without_halt_does_nothing(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 340.0))
    fake = FakeRun()
    code, rec = run(tmp_path, capsys, *RUN, "--place-stop", fake=fake)
    assert code == 0 and fake.calls == []


def test_source_never_removes_stop():
    src = SCRIPT.read_text()
    assert '"rm"' not in src and "unlink" not in src and "os.remove" not in src and "rmtree" not in src


# --- log and process ---------------------------------------------------------------------------------------------------------------
def test_log_path_under_structure_monitor_refused(tmp_path, capsys):
    code = bdc.main(["--shadow-dir", str(tmp_path), "--log", "/data/mal/structure-monitor/x.jsonl", *RUN], run=FakeRun())
    assert code == 2 and json.loads(capsys.readouterr().out)["error"] == "log_path_forbidden"


def test_log_path_symlinked_into_structure_monitor_refused(tmp_path, capsys, monkeypatch):
    real = bdc.os.path.realpath
    monkeypatch.setattr(bdc.os.path, "realpath", lambda p: "/data/mal/structure-monitor/daily.jsonl" if p.endswith("evil.jsonl") else real(p))
    code = bdc.main(["--shadow-dir", str(tmp_path), "--log", str(tmp_path / "evil.jsonl"), *RUN], run=FakeRun())
    assert code == 2 and not (tmp_path / "evil.jsonl").exists()


def test_running_reports_which_groups_were_judged(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 29, 340.0) + many("s", 30, 340.0, "syn"))
    code, rec = run(tmp_path, capsys, *RUN)
    assert code == 0 and rec["judged"] == {"plain": False, "all": True}


def test_log_appends_one_line_per_run(tmp_path, capsys):
    write_hour(tmp_path / "shadow", "2026-10-11", 4, many("p", 30, 340.0))
    for _ in range(3):
        run(tmp_path, capsys, *RUN)
    lines = (tmp_path / "log.jsonl").read_text().splitlines()
    assert len(lines) == 3 and all(json.loads(x)["tool"] == "h5_boost_day_check" for x in lines)


def test_runs_isolated_stdlib_only(tmp_path):
    write_hour(tmp_path / "shadow", "2026-10-10", 0, many("p", 30, 338.0) + many("s", 30, 342.0, "syn"))
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), "--shadow-dir", str(tmp_path / "shadow"), "--mode", "resume",
                        "--log", str(tmp_path / "log.jsonl"), "--now-utc", "2026-10-11T00:01:00Z"],
                       capture_output=True, text=True, cwd=str(tmp_path), timeout=60, stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stderr
    rec = json.loads(p.stdout)
    assert rec["resume_ok"] is True and rec["plain"] == {"n": 30, "median_s": 338.0} and rec["all"]["median_s"] == 340.0
