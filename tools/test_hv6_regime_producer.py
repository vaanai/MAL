"""Regime-block producer (QP 939 changes 1-3): torn radar lines, pin changes never stop the job, block age at use. Synthetic radar records only."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import hv6_regime_block as RB
from tools import hv6_review as RVW
from tools.test_hv4_runner import T0
from tools.test_hv6_regime_block import jl, rec
from tools.test_hv6_runner import _env, make, rows_of  # noqa: F401  (autouse: path allowlist for tmp dirs)
from tools.test_hv6_runner_endgate import REPO, SCRIPT, job_env, run_job, sha

H1 = 3_600_000


def test_job_refuses_only_missing_values_and_unpinned_directories(tmp_path):
    now = int(datetime(2026, 10, 30, 7, 15, tzinfo=timezone.utc).timestamp())
    for over, why in (({"HV6_BLOCK_TEST": "0"}, "out_dir_not_pinned"), ({"HV6_RADAR_CODE": ""}, "missing_HV6_RADAR_CODE"), ({"HV6_PYTHON": "/nonexistent/python"}, "python_missing")):
        r = run_job(job_env(tmp_path, now, **over))
        assert r.returncode == 2 and why in r.stdout, (why, r.stdout, r.stderr)
    text = SCRIPT.read_text()
    assert "/data/mal/venv/bin/python" in text and "MISCUSI JOB" in text


def test_a_pin_change_never_stops_the_job_it_writes_absent_blocks(tmp_path):
    day = datetime(2026, 10, 30, tzinfo=timezone.utc)
    (tmp_path / "radar-out").mkdir()
    (tmp_path / "radar-out" / "radar.jsonl").write_bytes(jl(rec(day + timedelta(hours=7, minutes=11), [])))
    now1 = int((day + timedelta(hours=7, minutes=15)).timestamp())
    assert run_job(job_env(tmp_path, now1, STABLE_S="1")).returncode == 0
    reg = tmp_path / "regime"
    assert json.loads((reg / f"block-{now1 * 1000}.json").read_text())["radar_status"] != "absent"
    code2 = tmp_path / "change_radar.py"  # a new radar deploy: other path, other bytes, other sha
    code2.write_text((REPO / "tools/change_radar.py").read_text() + "\n# redeployed\n")
    now2 = now1 + 3600
    r = run_job(job_env(tmp_path, now2, STABLE_S="1", HV6_RADAR_CODE=str(code2), HV6_RADAR_CODE_SHA256=sha(code2)))
    assert r.returncode == 0 and "pins_differ_from_registered" in r.stdout, r.stdout + r.stderr          # never a refusal to start
    blk = json.loads((reg / f"block-{now2 * 1000}.json").read_text())
    assert blk["radar_status"] == "absent" and "radar_code_path" in blk["pin_mismatch"] and blk["events"] == []   # absent from the change instant
    code3 = tmp_path / "change_radar3.py"
    code3.write_text("# something else\n")
    r = run_job(job_env(tmp_path, now2 + 60, STABLE_S="1", HV6_RADAR_CODE=str(code3), HV6_RADAR_CODE_SHA256="0" * 64))
    assert r.returncode == 0 and json.loads((reg / f"block-{(now2 + 60) * 1000}.json").read_text())["radar_status"] == "absent"
    # the registered pins are the FIRST ones written: a wrong value in a later environment is reported, and the block follows the registered pins (files unchanged: ok)
    r = run_job(job_env(tmp_path, now2 + 120, STABLE_S="1", HV6_RADAR_WATCH_SHA256="0" * 64))
    assert r.returncode == 0 and "pins_differ_from_registered" in r.stdout and "watch_sha_differs" in r.stdout
    assert json.loads((reg / f"block-{(now2 + 120) * 1000}.json").read_text())["radar_status"] != "absent"


def test_a_torn_radar_line_builds_nothing_until_the_line_is_complete(tmp_path):
    day = datetime(2026, 10, 30, tzinfo=timezone.utc)
    now = int((day + timedelta(hours=7, minutes=15)).timestamp())
    out = tmp_path / "radar-out"
    out.mkdir()
    good = jl(rec(day + timedelta(hours=7, minutes=11), []))
    (out / "radar.jsonl").write_bytes(good[:-20])                                          # the radar's append is caught mid-line: no trailing newline
    reg = tmp_path / "regime"
    r = run_job(job_env(tmp_path, now, STABLE_S="1"))
    assert r.returncode == 0 and "radar_file_not_stable" in r.stdout and not list(reg.glob("block-*.json")), r.stdout   # no nightly and no intraday block
    (out / "radar.jsonl").write_bytes(good)
    r = run_job(job_env(tmp_path, now, STABLE_S="1"))
    assert r.returncode == 0 and (reg / f"block-{now * 1000}.json").is_file()
    assert json.loads((reg / f"block-{now * 1000}.json").read_text())["radar_status"] != "absent"
    grow = tmp_path / "grow"                                                               # a file that keeps growing during the wait is not built either
    (grow / "radar-out").mkdir(parents=True)
    (grow / "radar-out" / "radar.jsonl").write_bytes(good)
    p = subprocess.Popen(["sh", str(SCRIPT)], cwd=str(REPO), env=job_env(grow, now + 5, STABLE_S="3"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)
    with open(grow / "radar-out" / "radar.jsonl", "ab") as f:
        f.write(jl(rec(day + timedelta(hours=7, minutes=12), [])))
    o, _ = p.communicate(timeout=60)
    assert "radar_file_not_stable" in o                                                    # the growing file was not built while it grew ...
    built = json.loads((grow / "regime" / f"block-{(now + 5) * 1000}.json").read_text())
    assert built["input"]["n_records"] == 2 and built["radar_status"] != "absent"           # ... and the block built afterwards holds both complete records


def test_a_parse_absent_block_is_removed_and_retried(tmp_path):
    day = datetime(2026, 10, 30, tzinfo=timezone.utc)
    now = int((day + timedelta(hours=7, minutes=15)).timestamp())
    out = tmp_path / "radar-out"
    out.mkdir()
    (out / "radar.jsonl").write_bytes(jl(rec(day + timedelta(hours=7, minutes=11), [])) + b"{not json}\n")   # a complete but unparseable line: the builder says absent
    r = run_job(job_env(tmp_path, now, STABLE_S="1"))
    assert r.returncode == 0 and "absent_parse" in r.stdout and not (tmp_path / "regime" / f"block-{now * 1000}.json").exists() and not (tmp_path / "regime" / f"radar-{now * 1000}.jsonl").exists()


# ------------------------------------------------------------------------------------------------------------------------ block age at use (36 h)
def block_file(d: Path, ms: int, status="ok", events=()):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"block-{ms}.json").write_text(json.dumps({"schema": "hv6_regime.v1", "as_of_ms": ms, "radar_status": status, "events": list(events), "input": {"sha256": None}}))


def test_runner_treats_a_block_older_than_36h_as_stale_and_does_not_trigger(tmp_path_factory):
    run = make(tmp_path_factory.mktemp("r6ega"))
    d = Path(run.cfg.root) / "regime"
    ev = [{"id": "RG-00000001", "sev": "HIGH", "relevance": "direct", "kind": "program_upgrade"}]
    block_file(d, T0 - 35 * H1, events=ev)
    assert run.regime.current(T0)["radar_status"] == "ok"
    (d / f"block-{T0 - 35 * H1}.json").unlink()
    block_file(d, T0 - 37 * H1, events=ev)
    assert run.regime.current(T0)["radar_status"] == "stale"                              # built ok, but 37 h old at this tick
    assert run.regime_round(T0) == [] and not rows_of(run, "SCAN", "regime_trigger")      # a stale block never triggers


def test_reviewer_reads_a_block_older_than_36h_as_stale(tmp_path_factory):
    d = tmp_path_factory.mktemp("r6egb") / "regime"
    block_file(d, T0 - 40 * H1)
    assert RVW.latest_block_file(d, T0)[0]["radar_status"] == "stale"
    d2 = tmp_path_factory.mktemp("r6egc") / "regime"
    block_file(d2, T0 - 30 * H1)
    assert RVW.latest_block_file(d2, T0)[0]["radar_status"] == "ok"


def test_regime_days_spans_end_at_36h_so_a_producer_gap_counts():
    t0 = datetime(2026, 10, 30, tzinfo=timezone.utc)
    blocks = [(int(t0.timestamp() * 1000), {"radar_status": "ok"})]                       # one ok block at 00:00 and then nothing
    assert RB.degraded_days(blocks, t0, 4) == ["2026-11-01", "2026-11-02"] or RB.degraded_days(blocks, t0, 4) == ["2026-11-02", "2026-11-01"] or RB.degraded_days(blocks, t0, 4) == ["2026-11-02"]
    assert "2026-11-02" in RB.degraded_days(blocks, t0, 4) and "2026-10-31" not in RB.degraded_days(blocks, t0, 4)
    blocks2 = blocks + [(int((t0 + timedelta(hours=30)).timestamp() * 1000), {"radar_status": "ok"})]
    assert "2026-11-02" not in RB.degraded_days(blocks2, t0, 3)                           # a newer block before the 36 h mark: no gap that day
    assert RB.block_for_use({"as_of_ms": 0, "radar_status": "ok"}, 36 * H1 + 1)["radar_status"] == "stale"
    assert RB.block_for_use({"as_of_ms": 0, "radar_status": "ok"}, 36 * H1)["radar_status"] == "ok"
    assert RB.block_for_use({"as_of_ms": 0, "radar_status": "absent"}, 10 ** 12)["radar_status"] == "absent"
