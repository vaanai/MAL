import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import a3_applied_alert as m

NOW = m._ms("2026-10-24T12:00:00Z")


def put(out, name, rows):
    (out / name).write_text("".join(json.dumps(r) + "\n" for r in rows))


def ap(run, verdict="NONE", ended=None):
    return {"run_utc": run, "verdict": verdict, "applied_ms": 1, "ended": ended or {}}


def setup(tmp_path, applied, verdicts=None):
    out = tmp_path / "o"
    out.mkdir()
    put(out, "a3-applied.jsonl", applied)
    put(out, "a3-verdicts.jsonl", verdicts if verdicts is not None else [{"run_utc": a["run_utc"], "verdict": a["verdict"]} for a in applied])
    return out, tmp_path / "s" / "state.json"


def test_baseline_then_quiet_then_new(tmp_path):
    out, st = setup(tmp_path, [ap("2026-10-24T07:10:05Z")])
    r = m.check(out, st, NOW)
    assert not any(x.startswith("ALERT") for x in r) and any("A3_BASELINE" in x for x in r)
    assert not any(x.startswith("ALERT") for x in m.check(out, st, NOW + 600_000))
    put(out, "a3-applied.jsonl", [ap("2026-10-24T07:10:05Z"), ap("2026-10-24T11:30:00Z", "HALT", {"deadman": {"x": 1}})])
    r = m.check(out, st, NOW + 1_200_000)
    assert any(x.startswith("ALERT A3_APPLIED_NEW verdict=HALT") and "ended_books=1" in x for x in r)
    assert not any(x.startswith("ALERT") for x in m.check(out, st, NOW + 1_800_000))  # reported once


def test_new_none_is_ok_not_alert(tmp_path):
    out, st = setup(tmp_path, [ap("2026-10-24T07:10:05Z")])
    m.check(out, st, NOW)
    put(out, "a3-applied.jsonl", [ap("2026-10-24T07:10:05Z"), ap("2026-10-24T11:50:00Z")])
    r = m.check(out, st, NOW + 60_000)
    assert any(x.startswith("A3_OK A3_APPLIED_NEW") for x in r) and not any(x.startswith("ALERT") for x in r)


def test_baseline_hard_verdict_alerts(tmp_path):
    out, st = setup(tmp_path, [ap("2026-10-24T07:10:05Z", "FAIL")])
    assert any(x.startswith("ALERT A3_APPLIED_NEW verdict=FAIL") for x in m.check(out, st, NOW))


def test_stale_after_13h_and_throttle(tmp_path):
    out, st = setup(tmp_path, [ap("2026-10-23T22:00:00Z")])  # 14 h old
    r = m.check(out, st, NOW)
    assert any(x.startswith("ALERT A3_GUARD_STALE") and "deadman_in_h=34.0" in x for x in r)
    assert not any("A3_GUARD_STALE" in x for x in m.check(out, st, NOW + 600_000))  # throttled 1 h
    assert any("A3_GUARD_STALE" in x for x in m.check(out, st, NOW + 3_700_000))


def test_not_stale_at_12h(tmp_path):
    out, st = setup(tmp_path, [ap("2026-10-24T00:10:00Z")])
    assert not any("STALE" in x for x in m.check(out, st, NOW))


def test_missing_and_malformed_files_alert(tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    r = m.check(out, tmp_path / "st.json", NOW)
    assert sum("A3_FILE_PROBLEM" in x for x in r) >= 2
    (out / "a3-applied.jsonl").write_text("{bad\n")
    (out / "a3-verdicts.jsonl").write_text("")
    assert any("not JSON" in x for x in m.check(out, tmp_path / "st.json", NOW))


def test_truncated_log_alerts(tmp_path):
    out, st = setup(tmp_path, [ap("2026-10-24T01:00:00Z"), ap("2026-10-24T07:00:00Z")])
    m.check(out, st, NOW)
    put(out, "a3-applied.jsonl", [ap("2026-10-24T07:00:00Z")])
    assert any("shorter than last pass" in x for x in m.check(out, st, NOW + 1))


def test_latest_verdict_alert_once_and_absent_ok(tmp_path):
    out, st = setup(tmp_path, [ap("2026-10-24T07:10:05Z")])
    m.check(out, st, NOW)
    (out / "a3-verdict-latest.json").write_text(json.dumps({"run_utc": "2026-10-24T11:55:00Z", "verdict": "HALT"}) + "\n")
    assert any("A3_VERDICT_LATEST" in x for x in m.check(out, st, NOW + 1000))
    assert not any("A3_VERDICT_LATEST" in x for x in m.check(out, st, NOW + 2000))
    (out / "a3-verdict-latest.json").unlink()
    assert not any("A3_FILE_PROBLEM" in x for x in m.check(out, st, NOW + 3000))


def test_cli_exit_code_and_stamp(tmp_path, capsys):
    out, st = setup(tmp_path, [ap("2026-10-23T22:00:00Z")])
    rc = m.main(["--out", str(out), "--state", str(st), "--now-ms", str(NOW)])
    cap = capsys.readouterr().out
    assert rc == 1 and "2026-10-24T12:00:00Z ALERT A3_GUARD_STALE" in cap


def test_sh_loop_runs_once(tmp_path):
    # The shell loop reads the REAL clock (no --now-ms). The fixed 2026-10-24T07:10:05Z run went older than STALE_S (13 h) on 2026-10-24T20:10Z, so the loop began to exit 3
    # with A3_GUARD_STALE (time-bomb in the fixture since #881, not a code change). Use a run 1 h before the real now so "no ALERT, exit 0" holds on any date.
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, st = setup(tmp_path, [ap(fresh)])
    repo = Path(__file__).resolve().parent.parent
    sh = repo / "scripts/mal-fast/a3-applied-alert.sh"
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "A3_REPO": str(repo), "A3_PY": sys.executable, "A3_ALERT_UNTIL": "0"}
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "structure-monitor").symlink_to(out)
    r = subprocess.run(["sh", str(sh)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "A3_BASELINE" in r.stdout and "A3_ALERT_JOB end reached" in r.stdout


def test_sh_loop_exits_3_on_first_alert(tmp_path):
    out, st = setup(tmp_path, [ap("2026-10-24T07:10:05Z", "HALT")])
    repo = Path(__file__).resolve().parent.parent
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "A3_REPO": str(repo), "A3_PY": sys.executable, "A3_ALERT_UNTIL": "99999999999", "A3_ALERT_EVERY": "1"}
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "structure-monitor").symlink_to(out)
    r = subprocess.run(["sh", str(repo / "scripts/mal-fast/a3-applied-alert.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 3 and "ALERT A3_APPLIED_NEW verdict=HALT" in r.stdout
