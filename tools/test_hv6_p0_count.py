"""tools/hv6_p0_count.py on synthetic tapes (the HV4 runner test's own pools). No network, no model, no key, no host file.

  * counts: the pullback pool is counted per scan; setups, tags, board and X would-be attempts appear as counts only;
  * as-of: a tape cut at T - 1 gives the same counts up to T; a state that does not stop at T - 1 raises AsOfError;
  * prescreen on and off give the same counts;
  * S1: fewer than 3 days, 10-19, a day before 10-20 and a day not closed at job start are refused;
  * the report prints no mint, pool or trader, and is deterministic (two runs, same sha256 line);
  * the fixed-first text must name every day and its sha256 is in the header; the CLI refuses a dirty or wrong-sha repo and test overrides without MAL_ARENA_TEST.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import arena_state as AS
from tools import hv4_cards as C
from tools import hv6_cards as H
from tools import hv6_p0_count as P
from tools import hv6_runner as RN
from tools.test_hv4_runner import MINTS, POOLS, T0, trade_rows, write_tape

HOUR = 3_600_000
DAY0 = T0 - 6 * HOUR
DAY = P.day_label(DAY0)
R = SimpleNamespace(AS=AS, C=C, H=H, RN=RN, repo=str(Path(__file__).resolve().parent.parent))


def fill_hours(d: Path, start: int, end: int) -> None:
    h = start - start % HOUR
    while h < end:
        p = d / f"tape-{P.hour_key(h)}.jsonl"
        if not p.exists():
            p.write_text("")
        h += HOUR


def make_tape(tmp: Path, rows=None, days: int = 1) -> Path:
    d = tmp / "tape"
    write_tape(d, trade_rows() if rows is None else rows)
    fill_hours(d, DAY0 - 365 * 60_000, DAY0 + days * 24 * HOUR)
    return d


def count(tape: Path, end_ms: int = DAY0 + 24 * HOUR, prescreen: bool = True):
    P._GAP_EXTRA = tuple(AS.GAP_EXTRA)
    st = P.make_state_class(AS)(str(tape), start_ms=DAY0 - 365 * 60_000)
    return P.count_day(R, st, P.Counter6(R), DAY, DAY0, end_ms, prescreen=prescreen, tape_dir=str(tape))


def test_counts_pullback_pool(tmp_path):
    rows, day = count(make_tape(tmp_path))
    assert day["status"] == "OK" and day["scans"] == 1440 and len(rows) == 24
    assert day["tags_total"] >= 1 and day["setups_by_type"]["PULLBACK"] >= 1
    assert day["x_would_be_attempts"] >= 1 and day["attempts_ub"]["X"] == day["x_would_be_attempts"]
    assert day["attempts_ub"]["L"] == day["tags_total"] + day["board_cards_total"]
    assert day["hour_files_missing"] == 0 and sum(r["scans"] for r in rows) == 1440
    assert day["board_age_2_24h"] + day["board_age_ge_24h"] == day["board_cards_total"]


def test_x_cap_two_per_hour(tmp_path):
    rows, _ = count(make_tape(tmp_path))
    assert max(r["x_would_be_attempts"] for r in rows) <= 2


def test_prescreen_equivalence(tmp_path):
    t = make_tape(tmp_path)
    _, a = count(t, prescreen=True)
    _, b = count(t, prescreen=False)
    assert a == b


def test_as_of_cut_tape(tmp_path):
    cut_ms = DAY0 + 12 * HOUR
    full = make_tape(tmp_path / "full")
    cut = make_tape(tmp_path / "cut", [r for r in trade_rows() if r["t_recv_ms"] < cut_ms])
    r1, _ = count(full, end_ms=cut_ms)
    r2, _ = count(cut, end_ms=cut_ms)
    assert r1[:12] == r2[:12]


def test_state_that_does_not_stop_raises(tmp_path):
    t = make_tape(tmp_path)
    P._GAP_EXTRA = tuple(AS.GAP_EXTRA)
    st = P.make_state_class(AS)(str(t), start_ms=DAY0 - 365 * 60_000)
    st.poll = lambda until_ms=None: setattr(st, "now_ms", until_ms + 5)
    with pytest.raises(P.AsOfError):
        P.scan_once(R, st, DAY0 + 2000, P.Counter6(R), P.HourCounts())


def test_projection_drops_identifying_fields():
    r = P.project_row({"type": "trade", "prog": "pumpswap", "t_recv_ms": 5, "signature": "SIG", "name": "N", "symbol": "S", "pool": "p", "mint": "m", "event_index": 3})
    assert set(r) == {"type", "prog", "t_recv_ms", "pool", "mint"}


@pytest.mark.parametrize("days,why", [
    (["2026-10-21", "2026-10-22"], "need_at_least_3"),
    (["2026-10-19", "2026-10-20", "2026-10-21"], "before_2026-10-20"),
    (["2026-10-21", "2026-10-22", "2026-10-26"], "not_closed"),
    (["2026-10-21", "2026-10-21", "2026-10-22"], "need_at_least_3"),
])
def test_s1_refusals(days, why):
    now = P.parse_day("2026-10-26") + 3 * HOUR
    with pytest.raises(P.DaysError, match=why):
        P.check_days(days, now)


def test_s1_accepts_closed_days_sorted():
    now = P.parse_day("2026-10-26") + 3 * HOUR
    assert P.check_days(["2026-10-25", "2026-10-22", "2026-10-23"], now) == ["2026-10-22", "2026-10-23", "2026-10-25"]


def _cli(tmp_path, monkeypatch, tape, days, fixed_text):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr(P, "P0_FLOOR_MS", DAY0)
    monkeypatch.setattr(P, "verify_repo", lambda repo, sha: sha)
    monkeypatch.setattr(P, "load_rules", lambda repo: R)
    ff = tmp_path / "fixed-first.txt"
    ff.write_text(fixed_text)
    out = tmp_path / "out.jsonl"
    argv = ["--repo", "x", "--tape-dir", str(tape), "--days", days, "--fixed-first", str(ff), "--out", str(out), "--warmup-min", "360", "--expect-sha", "a" * 40]
    return argv, out, ff


def test_cli_report_is_deterministic_counts_only(tmp_path, monkeypatch, capsys):
    tape = make_tape(tmp_path, days=3)
    d1, d2 = P.day_label(DAY0 + 24 * HOUR), P.day_label(DAY0 + 48 * HOUR)
    argv, out, ff = _cli(tmp_path, monkeypatch, tape, f"{DAY},{d1},{d2}", f"days {DAY} {d1} {d2}")
    now = DAY0 + 4 * 24 * HOUR
    rc1 = P.main(argv, now_ms=now)
    t1 = capsys.readouterr().out
    rc2 = P.main(argv, now_ms=now)
    t2 = capsys.readouterr().out
    assert rc1 == rc2 and rc1 in (0, 1) and t1 == t2
    body, last = t1.rstrip().rsplit("\n", 1)
    assert last == "sha256 " + hashlib.sha256((body + "\n").encode()).hexdigest()
    rows = [json.loads(x) for x in body.split("\n")]
    assert rows[0]["fixed_first_sha256"] == hashlib.sha256(ff.read_bytes()).hexdigest()
    assert rows[-1]["kind"] == "verdict" and "upper bound" in rows[-1]["disclosure"]
    for secret in list(MINTS.values()) + list(POOLS.values()):
        assert secret not in t1
    assert out.read_text() == t1


def test_cli_refuses_fixed_first_missing_a_day(tmp_path, monkeypatch, capsys):
    tape = make_tape(tmp_path, days=3)
    d1, d2 = P.day_label(DAY0 + 24 * HOUR), P.day_label(DAY0 + 48 * HOUR)
    argv, _, _ = _cli(tmp_path, monkeypatch, tape, f"{DAY},{d1},{d2}", f"days {DAY} {d1}")
    assert P.main(argv, now_ms=DAY0 + 4 * 24 * HOUR) == 2
    assert "fixed_first_text_does_not_name_every_day" in capsys.readouterr().err


def test_cli_refuses_a_bad_sha_and_a_test_clock_without_env(monkeypatch, capsys):
    monkeypatch.delenv("MAL_ARENA_TEST", raising=False)
    assert P.main(["--repo", "x", "--days", "2026-10-21,2026-10-22,2026-10-23", "--fixed-first", "f", "--expect-sha", "abc"]) == 2
    assert "40 hex" in capsys.readouterr().err
    assert P.main(["--repo", "x", "--days", "2026-10-21,2026-10-22,2026-10-23", "--fixed-first", "f", "--expect-sha", "a" * 40], now_ms=1) == 2
    assert "MAL_ARENA_TEST" in capsys.readouterr().err


def test_missing_day_tape_fails_exit_1(tmp_path, monkeypatch, capsys):
    tape = make_tape(tmp_path, days=1)
    d1, d2 = P.day_label(DAY0 + 24 * HOUR), P.day_label(DAY0 + 48 * HOUR)
    argv, _, _ = _cli(tmp_path, monkeypatch, tape, f"{DAY},{d1},{d2}", f"{DAY} {d1} {d2}")
    assert P.main(argv, now_ms=DAY0 + 4 * 24 * HOUR) == 1
    out = capsys.readouterr().out
    assert '"status":"FAIL"' in out and '"registration_ok":false' in out


def test_verify_repo_refuses_dirty_and_wrong_sha(tmp_path):
    def g(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (tmp_path / "a").write_text("1")
    g("add", "a")
    g("commit", "-qm", "x")
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert P.verify_repo(str(tmp_path), head) == head
    with pytest.raises(P.RepoError):
        P.verify_repo(str(tmp_path), "0" * 40)
    (tmp_path / "a").write_text("2")
    with pytest.raises(P.RepoError):
        P.verify_repo(str(tmp_path), head)


def test_sha_is_a_required_argument():
    assert not hasattr(P, "FINAL_SHA")
    with pytest.raises(SystemExit):
        P.main(["--repo", "x", "--days", "a", "--fixed-first", "f"])
