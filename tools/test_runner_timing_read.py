import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner_timing_read as m  # noqa: E402

T0 = 1_790_000_000_000
H = 3_600_000
OUTCOMES = ["armed", "skipped_risk:daily_loss_cap", "skipped_stale", "error", "no_pass"]
N_ROWS = 60


def _fixtures(tmp_path):
    a = tmp_path / "arm-audit.jsonl"
    rows = []
    for i in range(N_ROWS):
        o = OUTCOMES[i % len(OUTCOMES)]
        rows.append({"schema": m.SCHEMA_AUDIT, "mint": f"MINT{i}", "book": "bk", "complete_slot": i,
                     "complete_t_recv_ms": T0 + i * H // 2, "eval_clock_ms": T0 + i * H // 2 + 100 + i,
                     "candidates": [], "pass_any": False, "pass_all": False, "outcome": o,
                     "error": "ValueError" if o == "error" else None})
    rows.append({"schema": "forward_paper_arm_audit_summary_v1", "eval_clock_ms": T0, "counts": {"skipped_risk": 3}})
    a.write_text("\n".join(json.dumps(r) for r in rows))
    h = tmp_path / "heartbeat.jsonl"
    h.write_text("\n".join(json.dumps({"sampled_ms": T0 + i * 60_000, "status_ts_ms": T0, "lag_ms": 10 * i, "pid": 1, "ok": i % 7 != 0}) for i in range(50)))
    return a, h


def _run(tmp_path, capsys, extra, end=T0 + 48 * H):
    a, h = _fixtures(tmp_path)
    rc = m.main(["--arm-audit", str(a), "--heartbeat", str(h), "--start", m._iso(T0), "--end", m._iso(end), *extra])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_no_sealed_strings_and_allowlisted_keys(tmp_path, capsys):
    rc, out, _ = _run(tmp_path, capsys, [])
    assert rc == 0
    low = out.lower()
    for bad in OUTCOMES + ["risk", "kill", "loss", "mint", "outcome", "book", "day", "hour"]:
        assert bad not in low
    d = json.loads(out)
    assert set(d) <= m.ALLOWED_OUTPUT_KEYS
    assert "hb_ok_false_count" in d and "eval_delay_ms_p50" in d


def test_per_day_refused(tmp_path, capsys):
    for flag in ("--per-day", "--group-by", "--by-outcome", "--whatever"):
        rc, out, err = _run(tmp_path, capsys, [flag])
        assert rc == 2 and out == ""
        assert "sealed until the EXP-012 FINAL read (DEC-016 Am.2/Am.3)" in err


def test_short_window_omits_n(tmp_path, capsys):
    rc, out, _ = _run(tmp_path, capsys, [], end=T0 + 6 * H)
    d = json.loads(out)
    assert rc == 0 and "eval_delay_ms_n" not in d and "eval_delay_ms_p50" in d


def test_n_is_delay_rows_only_and_long_window(tmp_path, capsys):
    rc, out, _ = _run(tmp_path, capsys, [])
    # rows i with i*30min < 48h -> i < 96, so all 60 delay rows; the summary row is excluded
    assert json.loads(out)["eval_delay_ms_n"] == N_ROWS


def test_constants():
    assert m.SEAL_UNTIL == "2026-10-16T02:00:00Z"
    assert "eval_delay_ms_n" in m.ALLOWED_OUTPUT_KEYS
