"""hv4_memreplay --compare-trim (QP precondition 3 on #745): the trim ON vs OFF runner-path comparison. Synthetic tape only: no network, no key, no real tape.

`compare_trim` is checked on hand-made results (what makes two runs `identical`, what voids the equivalence), and `--compare-trim` is run end to end as the
harness runs it on the real hours: two child processes over a small synthetic tape written as closed `.jsonl.zst` hours.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import hv4_memreplay as M
from tools.test_hv4_memory_bound import churn_rows
from tools.test_hv4_runner import H, MIN, T0, X, hour_name, trade_rows


@pytest.fixture(scope="module", autouse=True)
def _arena_test_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAL_ARENA_TEST", "1")
        yield


def result(*, md5="m", led="l", led_nm="n", trim=True, dup=5, hook=None, below=0, refused=0, rss=(100, 120)) -> dict:
    ec = {"rows_dup": dup, "v_missing": 3}
    tf = None
    if trim:
        ec.update(hist_trimmed_prints=900, rows_below_trim_floor=below)
        if refused:
            ec["refused_history_trimmed"] = refused  # the engine counts a key only when it happens
        tf = {"lines": 12, "trim_ms_sum": 6.0, "trim_ms_max": 1.0, "trim_ms_mean": 0.5, "monotone": True, "last": {}, "engine_ids": 1}
    return {"done": True, "decision_log_md5": md5, "ledger_digest": led, "ledger_digest_no_mint": led_nm, "engine_counters": ec, "hook_errors": hook or {}, "trim_file": tf,
            "v_filter": {"scans_v_missing": 40, "scans_v_band": 7, "pools_checked": 90, "pools_v_missing": 12},
            "peak_rss_mb": max(rss), "hwm_mb": max(rss) + 2, "samples": [{"sim": f"10-19T{8 + i}:00", "rss_mb": r, "rows": 1000 * (i + 1), "engine_pools": 5, "engine_hist_rows": 10 * r,
                                                                             "engine_hist_seen": 11 * r, "wall_s": 10 * (i + 1)} for i, r in enumerate(rss)]}


def test_compare_trim_is_identical_only_when_every_digest_and_counter_agrees():
    c = M.compare_trim(result(), result(trim=False))
    assert c["identical"] and c["trim_actually_ran"] and not c["equivalence_void"]
    assert [h["sim"] for h in c["hourly"]] == ["10-19T8:00", "10-19T9:00"] and c["engine_counters"]["hist_trimmed_prints"] == {"on": 900, "off": 0}
    assert not M.compare_trim(result(md5="x"), result(trim=False))["identical"]  # a different decision log
    assert not M.compare_trim(result(led="x"), result(trim=False))["identical"]  # a different ledger (mint included)
    assert not M.compare_trim(result(led_nm="x"), result(trim=False))["identical"]  # a different ledger without the mint field
    assert not M.compare_trim(result(dup=6), result(trim=False))["identical"]  # rows_dup moved: a below-floor row was not a duplicate in the untrimmed engine
    assert not M.compare_trim(result(hook={"housekeep:RuntimeError": 1}), result(trim=False))["identical"]  # a hook error in either run
    assert not M.compare_trim(result(refused=1), result(trim=False))["identical"]  # a buy refused for a trimmed history
    assert not M.compare_trim(result(trim=False), result(trim=False))["identical"]  # the trim did not run: nothing was compared
    moved = result()
    moved["v_filter"] = dict(moved["v_filter"], pools_v_missing=13)
    assert not M.compare_trim(moved, result(trim=False))["identical"]  # the V-universe counters are decisions-level: they must not move with the trim
    assert c["v_filter"]["on"]["pools_checked"] == 90 and c["v_filter"]["equal"]


def test_compare_trim_flags_a_void_equivalence_even_when_the_md5s_match():
    """arena_paper TRIM_KEEP_SLOTS: a nonzero rows_below_trim_floor voids the equivalence for a live engine, whatever this tape's digests say."""
    c = M.compare_trim(result(below=2), result(trim=False))
    assert c["identical"] and c["equivalence_void"]
    assert M.compare_trim(result(refused=1), result(trim=False))["equivalence_void"]


def write_zst_tape(d: Path, rows: list[dict]) -> list[str]:
    d.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(hour_name(r["t_recv_ms"]), []).append(json.dumps(r))
    for name, lines in by.items():
        p = d / name
        p.write_text("\n".join(lines) + "\n")
        subprocess.run(["zstd", "-q", "-f", "--rm", str(p)], check=True)
    return sorted(n[len("tape-"):-len(".jsonl")] for n in by)


@pytest.mark.skipif(shutil.which("zstd") is None, reason="the zstd CLI is needed to write a closed .jsonl.zst hour")
def test_compare_trim_end_to_end_on_a_synthetic_closed_tape(tmp_path):
    rows = sorted(trade_rows() + churn_rows(20, X - 60 * MIN, every_ms=2 * MIN), key=lambda r: (r["t_recv_ms"], r["signature"]))
    hours = write_zst_tape(tmp_path / "tape", rows)
    fmt = "%Y-%m-%dT%H:%M:%S"
    t_from = datetime.fromtimestamp((X - 40 * MIN) / 1000, tz=timezone.utc).strftime(fmt)
    t_to = datetime.fromtimestamp((X + 85 * MIN) / 1000, tz=timezone.utc).strftime(fmt)
    out = tmp_path / "cmp.json"
    scratch = tmp_path / "scratch"
    r = subprocess.run([sys.executable, str(Path(M.__file__).resolve()), "--compare-trim", "--fail-on-diff", "--tape-dir", str(tmp_path / "tape"), "--hours", f"{hours[0]}..{hours[-1]}",
                        "--start", t_from, "--end", t_to, "--scratch", str(scratch), "--sample-min", "30", "--compare-out", str(out)],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    c = json.loads(out.read_text())
    assert c["identical"] and c["decision_log_md5"]["equal"] and c["ledger_digest"]["equal"] and c["ledger_digest_no_mint"]["equal"]
    assert c["trim_actually_ran"] and c["engine_counters"]["hist_trimmed_prints"]["on"] > 0 and c["engine_counters"]["hist_trimmed_prints"]["off"] == 0
    assert c["engine_counters"]["rows_dup"]["on"] == c["engine_counters"]["rows_dup"]["off"] and not c["equivalence_void"]
    assert c["trim_file_on"]["lines"] >= 20 and c["trim_file_on"]["monotone"] and c["trim_file_off"] is None
    assert c["hourly"] and (scratch / "on" / "hv4" / "engine-trim-counters.jsonl").exists() and not (scratch / "off" / "hv4" / "engine-trim-counters.jsonl").exists()
    # NOT asserted: that this small synthetic tape trades under the harness's mock chain (it may not: the ledger digest can be the empty-file digest). The decision and
    # fill equality WITH trades is test_hv4_memory_bound.test_trim_on_and_off_give_the_same_decision_md5_ledger_digest_and_state; this test pins the harness plumbing.
    # and the comparison of these very two results flags a different decision md5
    bad = M.compare_trim({**json.loads((scratch / "on-result.json").read_text()), "decision_log_md5": "x"}, json.loads((scratch / "off-result.json").read_text()))
    assert not bad["identical"]
