"""Tests for tools/arena_hour_health.py: synthetic tape hours only; no real arena data."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena_hour_health as A  # noqa: E402

H0 = A.parse_hour("2026-10-23T00")
HR = A.HOUR_MS


def trade_rows(h, step_s=60):
    return [{"v": 1, "type": "trade", "t_recv_ms": h + i * step_s * 1000} for i in range(0, 3600 // step_s)]


def write_hour(d, h, rows, zst=False):
    p = d / A.hour_name(h)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    if zst:
        subprocess.run(["zstd", "-q", "--rm", str(p)], check=True)


def samples_file(path, times, updated_lag_ms=1000):
    with open(path, "w") as f:
        for t in times:
            f.write(json.dumps({"schema": A.SAMPLE_SCHEMA, "t_ms": t, "feed": {"updated_ms": t - updated_lag_ms}}) + "\n")


THR = {"max_gap_s": 30, "max_data_loss_count": 0, "min_samples_per_hour": 10, "max_sample_gap_s": 900, "max_input_age_s": 120}


def run(tmp, hours=4, thr=THR, samples=True):
    t = tmp / "thr.json"
    t.write_text(json.dumps(thr))
    return A.render(A.evaluate(tmp / "tape", tmp / "s.jsonl" if samples else None, H0, H0 + (hours - 1) * HR, thr), "x")


@pytest.fixture
def world(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, trade_rows(H0))  # clean
    # H0+1 missing
    (d / A.hour_name(H0 + 2 * HR)).write_text("")  # zero size
    rows = trade_rows(H0 + 3 * HR) + [{"type": "gap", "kind": "silence", "data_loss": True, "from_ms": 1, "to_ms": 61001, "t_recv_ms": H0 + 3 * HR + 5}]
    write_hour(d, H0 + 3 * HR, rows)
    samples_file(tmp_path / "s.jsonl", [H0 + i * 60_000 for i in range(60)] + [H0 + 3 * HR + i * 60_000 for i in range(60)])
    return tmp_path


def hours(lines):
    return [json.loads(x) for x in lines[:-1]]


def test_clean_missing_zero_dataloss(world):
    r = hours(run(world))
    assert r[0]["excluded"] is False and r[0]["failed_checks"] == [] and r[0]["unmeasurable"] == []
    assert "tape_file_missing" in r[1]["failed_checks"] and r[1]["excluded"]
    assert "tape_file_zero_size" in r[2]["failed_checks"] and r[2]["tape_size"] == 0
    assert r[3]["data_loss_rows"] == 1 and r[3]["data_loss_max_span_s"] == 61.0
    assert {"max_gap_s", "max_data_loss_count"} <= set(r[3]["failed_checks"])
    assert r[3]["gap_rows"] == [{"kind": "silence", "data_loss": True, "rows": 1}]


def test_sampler_gap_and_missing_samples_unmeasurable(world):
    samples_file(world / "s.jsonl", [H0 + i * 60_000 for i in range(5)] + [H0 + 50 * 60_000 + i for i in range(20)])
    r = hours(run(world, hours=1))[0]
    assert r["excluded"] and "max_sample_gap_s" in r["failed_checks"]
    # no sample file but sampler thresholds pinned: refused at the CLI (exit 2)
    thr = world / "thr.json"
    thr.write_text(json.dumps(THR))
    assert A.main(["--tape-dir", str(world / "tape"), "--from", "2026-10-23T00", "--to", "2026-10-23T00", "--thresholds", str(thr)]) == 2
    # only tape thresholds: fine without samples
    r2 = hours(run(world, hours=1, thr={"max_gap_s": 30}, samples=False))[0]
    assert r2["excluded"] is False and r2["samples_n"] is None


def test_input_age_from_tape_gap(world):
    write_hour(world / "tape", H0, [{"type": "trade", "t_recv_ms": H0 + 10}, {"type": "trade", "t_recv_ms": H0 + 600_000}])
    r = hours(run(world, hours=1))[0]
    assert r["tape_input_age_max_s"] == 3000.0 and "max_input_age_s[tape]" in r["failed_checks"]


def test_tape_without_feed_time_unmeasurable(world):
    write_hour(world / "tape", H0, [{"type": "trade"}])
    r = hours(run(world, hours=1))[0]
    assert "max_input_age_s[tape]" in r["unmeasurable"] and r["excluded"]


def test_zst_hour(world):
    if not shutil.which("zstd"):
        pytest.skip("no zstd")
    write_hour(world / "tape", H0, trade_rows(H0), zst=True)
    r = hours(run(world, hours=1))[0]
    assert r["tape_file"].endswith(".zst") and r["excluded"] is False


def test_deterministic_sha(world):
    a, b = run(world), run(world)
    assert a == b
    fin = json.loads(a[-1])
    assert fin["sha256"] == hashlib.sha256("".join(x + "\n" for x in a[:-1]).encode()).hexdigest()
    assert fin["hours"] == 4 and fin["excluded_hours"] == 3


@pytest.mark.parametrize("bad", ["ledger", "decisions", "bots.json", "runner-status"])
def test_forbidden_paths_exit_2(world, bad, capsys):
    thr = world / "thr.json"
    thr.write_text(json.dumps(THR))
    args = ["--from", "2026-10-23T00", "--to", "2026-10-23T00", "--thresholds", str(thr)]
    bd = world / bad
    bd.mkdir(exist_ok=True)
    assert A.main(["--tape-dir", str(bd)] + args) == 2
    f = world / (bad + "-x.jsonl")
    f.write_text("")
    assert A.main(["--tape-dir", str(world / "tape"), "--samples", str(f)] + args) == 2
    t2 = world / (bad + "-thr.json")
    t2.write_text(json.dumps(THR))
    assert A.main(["--tape-dir", str(world / "tape"), "--from", "2026-10-23T00", "--to", "2026-10-23T00", "--thresholds", str(t2)]) == 2
    with pytest.raises(A.Refused):
        A.guard(world / bad / "x")


def test_main_cli_ok_and_bad_thresholds(world, capsys):
    thr = world / "thr.json"
    thr.write_text(json.dumps(THR))
    assert A.main(["--tape-dir", str(world / "tape"), "--samples", str(world / "s.jsonl"), "--from", "2026-10-23T00", "--to", "2026-10-23T03", "--thresholds", str(thr)]) == 0
    out = capsys.readouterr().out.strip().split("\n")
    assert len(out) == 5 and json.loads(out[-1])["thresholds_sha256"] == hashlib.sha256(thr.read_bytes()).hexdigest()
    thr.write_text(json.dumps({"max_pnl": 1}))
    assert A.main(["--tape-dir", str(world / "tape"), "--from", "2026-10-23T00", "--to", "2026-10-23T00", "--thresholds", str(thr)]) == 2


def test_no_forbidden_fields_in_output(world):
    out = "\n".join(run(world))
    for w in ("signature", "mint", "wallet", "price", "pnl"):
        assert w not in out


def test_torn_lines_make_hour_unmeasurable(world):
    base = "".join(json.dumps(r) + "\n" for r in trade_rows(H0))
    p = world / "tape" / A.hour_name(H0)
    gap = json.dumps({"type": "gap", "kind": "silence", "data_loss": True, "from_ms": 1, "to_ms": 2})
    for body in (base + gap[:-8], base + gap[:-1] + "\n", base + '{"type":"trade","t_recv_ms":5}'):  # torn tail, cut gap line, no newline
        p.write_text(body)
        r = hours(run(world, hours=1))[0]
        assert r["excluded"] and "tape_torn_lines" in r["unmeasurable"] and r["torn_lines"] >= 1 and r["data_loss_rows"] is None


def test_missing_tape_dir_exit_2(world):
    thr = world / "thr.json"
    thr.write_text(json.dumps({"max_gap_s": 1}))
    assert A.main(["--tape-dir", str(world / "nope"), "--from", "2026-10-23T00", "--to", "2026-10-23T00", "--thresholds", str(thr)]) == 2


def test_both_jsonl_and_zst_ambiguous(world):
    (world / "tape" / (A.hour_name(H0) + ".zst")).write_bytes(b"x")
    r = hours(run(world, hours=1))[0]
    assert r["excluded"] and "tape_ambiguous_files" in r["unmeasurable"]


@pytest.mark.parametrize("rows", [[{"feed": None}], [{"feed": {"updated_ms": 10**15}}]])
def test_sample_without_feed_time_or_negative_age(world, rows):
    with open(world / "s.jsonl", "w") as f:
        for i in range(30):
            f.write(json.dumps({"schema": A.SAMPLE_SCHEMA, "t_ms": H0 + i * 60_000, **(rows[0] if i == 7 else {"feed": {"updated_ms": H0 + i * 60_000 - 1000}})}) + "\n")
    r = hours(run(world, hours=1, thr={"max_input_age_s": 120}))[0]
    assert r["excluded"] and "max_input_age_s[sampler]" in r["unmeasurable"] and r["samples_n"] == 30
