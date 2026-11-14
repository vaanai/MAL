"""r4bt_d_measure: synthetic fixtures in a temp dir only. No file of the host is read."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import arena_tip_adapter as AD
from tools import r4bt_d_measure as M
from tools import r4bt_gate as G

FROM, TO = "2026-11-15T00:00:00Z", "2026-11-16T00:00:00Z"
F_S = G.utc_ms(FROM) // 1000
T_S = G.utc_ms(TO) // 1000
SLOT_S = 0.4  # seconds per slot in the fixture: slot = (t - F_S) * 2.5


def row(t, lag=1000, slot=None, **over):
    r = {"t": t, "s_block_lag_ms_p90": lag, "s_last_done_slot": int((t - F_S) * 2.5) if slot is None else slot, "s_gaps": 1}
    r.update(over)
    return r


def lines(rows):
    return ("\n".join(json.dumps(r) for r in rows) + "\n").encode()


def day(n=1440, lagf=lambda i: 800 + (i * 37) % 900):
    return [row(F_S + 60 * i, lag=lagf(i)) for i in range(n)]


def test_formula_equals_ceil_p90_over_slot_s_by_the_adapter_percentile():
    rows = day()
    rec = M.measure([("a.jsonl", lines(rows))], FROM, TO)
    lags = sorted(r["s_block_lag_ms_p90"] for r in rows)
    p90 = lags[min(len(lags) - 1, max(0, math.ceil(0.9 * len(lags)) - 1))]
    slot_s = (rows[-1]["t"] - rows[0]["t"]) / (rows[-1]["s_last_done_slot"] - rows[0]["s_last_done_slot"])
    assert rec["p90_ms"] == p90
    assert rec["slot_s"] == pytest.approx(slot_s)
    assert rec["d"] == math.ceil(p90 / (slot_s * 1000) - 1e-9)
    assert rec["d"] == AD.feed_delay_slots([{"block_lag_ms_p90": r["s_block_lag_ms_p90"]} for r in rows], slot_s, ingest_lag_ms=0.0)["d"]
    assert rec["n_kept"] == 1440 and rec["n_dropped"] == 0 and rec["max_gap_s"] == 60


def test_output_has_only_the_pinned_fields_and_the_blobs():
    rec = M.measure([("a.jsonl", lines(day()))], FROM, TO)
    assert set(rec) == {"window", "inputs", "n_kept", "n_dropped", "max_gap_s", "p50_ms", "p90_ms", "slot_s", "d", "tool_blob", "adapter_blob", "commit"}
    assert rec["inputs"][0]["name"] == "a.jsonl" and len(rec["inputs"][0]["sha256"]) == 64
    assert len(rec["tool_blob"]) == 40 and len(rec["adapter_blob"]) == 40 and len(rec["commit"]) == 40


def test_two_files_merge_and_sort_by_t():
    rows = day()
    rec = M.measure([("b", lines(rows[700:])), ("a", lines(rows[:700]))], FROM, TO)
    assert rec["n_kept"] == 1440 and [i["name"] for i in rec["inputs"]] == ["b", "a"]


def test_window_edges_t_equal_to_is_excluded_and_t_equal_from_is_included():
    rows = day() + [row(T_S, lag=999999)]  # t == to: excluded (its huge lag would show)
    rec = M.measure([("a", lines(rows))], FROM, TO)
    assert rec["n_kept"] == 1440 and rec["p90_ms"] < 999999
    early = [row(F_S - 1, lag=999999)] + day()
    rec = M.measure([("a", lines(early))], FROM, TO)
    assert rec["n_kept"] == 1440  # t just before from: out of window, neither kept nor dropped
    assert rec["n_dropped"] == 0
    only_from = M.parse_samples(lines([row(F_S)]), F_S, T_S)[0]
    assert len(only_from) == 1


def test_bad_fields_are_dropped_and_counted():
    rows = day(1440 + 5)[:1440]
    bad = [row(F_S + 30, lag="x"), row(F_S + 31, lag=True), {"t": F_S + 32, "s_last_done_slot": 5}, {"t": F_S + 33, "s_block_lag_ms_p90": 5}, row(F_S + 34, slot=None)]
    bad[4].pop("s_last_done_slot")
    data = lines(rows + bad) + b"not json\n[1]\n"
    rec = M.measure([("a", data)], FROM, TO)
    assert rec["n_kept"] == 1440 and rec["n_dropped"] == 5 + 2


def refused(files, match):
    with pytest.raises(G.Refused, match=match):
        M.measure(files, FROM, TO)


def test_refuses_fewer_than_1296_kept():
    refused([("a", lines(day(1295)))], "1296")
    assert M.measure([("a", lines(day(1296)))], FROM, TO)["n_kept"] == 1296


def test_refuses_fewer_than_100_samples():
    refused([("a", lines(day(99)))], "adapter's floor")


def test_refuses_a_gap_above_1800_s_but_not_exactly_1800():
    rows = day(1500)
    ok = rows[:700] + [dict(r, t=r["t"] + 1740) for r in rows[700:]]  # a 1800 s gap exactly
    for r in ok[700:]:
        r["s_last_done_slot"] = int((r["t"] - F_S) * 2.5)
    assert M.measure([("a", lines(ok))], FROM, TO)["max_gap_s"] == 1800
    bad = rows[:700] + [dict(r, t=r["t"] + 1741, s_last_done_slot=int((r["t"] + 1741 - F_S) * 2.5)) for r in rows[700:]]
    refused([("a", lines(bad))], "gap")


def test_refuses_a_decreasing_slot():
    rows = day()
    rows[800]["s_last_done_slot"] = rows[799]["s_last_done_slot"] - 1
    refused([("a", lines(rows))], "non-decreasing")


def test_refuses_a_slot_that_did_not_advance():
    refused([("a", lines([row(F_S + 60 * i, slot=7) for i in range(1440)]))], "did not advance")


def test_refuses_bad_window():
    with pytest.raises(G.Refused):
        M.measure([("a", lines(day()))], TO, FROM)
    with pytest.raises(G.Refused):
        M.measure([("a", lines(day()))], "2026-11-15", TO)


def test_cli_writes_once_and_refuses_an_existing_out(tmp_path, capsys):
    f = tmp_path / "s.jsonl"
    f.write_bytes(lines(day()))
    out = tmp_path / "d.json"
    args = ["--sample-file", str(f), "--from", FROM, "--to", TO, "--out", str(out)]
    assert M.main(args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert json.loads(out.read_text()) == printed
    before = out.read_bytes()
    assert M.main(args) == 2
    assert out.read_bytes() == before


def test_write_once_is_o_excl(tmp_path):
    p = tmp_path / "x"
    M.write_once(p, "a")
    with pytest.raises(FileExistsError):
        M.write_once(p, "b")
    assert p.read_text() == "a"


def test_cli_refuses_a_failed_coverage_with_exit_2_and_writes_nothing(tmp_path, capsys):
    f = tmp_path / "s.jsonl"
    f.write_bytes(lines(day(500)))
    out = tmp_path / "d.json"
    assert M.main(["--sample-file", str(f), "--from", FROM, "--to", TO, "--out", str(out)]) == 2
    assert not out.exists()


@pytest.mark.parametrize("root", ["/data/mal/r4b", "/var/lib/mal/sealed", "/data/mal/meta-capture", "/data/mal/blocks", "/data/mal/arena"])
def test_path_refusals_for_input_and_out(root, tmp_path, capsys):
    good = tmp_path / "s.jsonl"
    good.write_bytes(lines(day()))
    assert M.main(["--sample-file", root + "/s.jsonl", "--from", FROM, "--to", TO, "--out", str(tmp_path / "o")]) == 2
    assert M.main(["--sample-file", str(good), "--from", FROM, "--to", TO, "--out", root + "/o.json"]) == 2
    assert not (tmp_path / "o").exists()


def test_tip_tape_archive_mark_refused(tmp_path):
    d = tmp_path / "tip-tape-archive"
    d.mkdir()
    assert M.main(["--sample-file", str(d / "s.jsonl"), "--from", FROM, "--to", TO, "--out", str(tmp_path / "o")]) == 2


def test_the_keys_it_reads_are_the_samplers_names():
    from tools import r4bt_sample_keys as K
    assert set(M.USED_KEYS) <= K.REAL_KEYS


def test_a_slot_time_of_0_215_s_and_the_old_slip_1_over_slot_s_is_refused_by_the_adapter():
    rows = [row(F_S + 60 * i, lag=800 + (i * 37) % 900, slot=int(60 * i / 0.215)) for i in range(1440)]
    rec = M.measure([("a", lines(rows))], FROM, TO)
    slot_s = (rows[-1]["t"] - rows[0]["t"]) / (rows[-1]["s_last_done_slot"] - rows[0]["s_last_done_slot"])
    assert slot_s == pytest.approx(0.215, rel=1e-3)
    assert rec["d"] == math.ceil(rec["p90_ms"] / (slot_s * 1000) - 1e-9)
    samples = [{"block_lag_ms_p90": r["s_block_lag_ms_p90"]} for r in rows]
    with pytest.raises(ValueError):  # the ruling's 1/slot_s (slots per second) is outside the adapter's 0.05 < sps < 2.0 seconds-per-slot range
        AD.feed_delay_slots(samples, 1 / slot_s, ingest_lag_ms=0.0)
    with pytest.raises(ValueError):
        AD.feed_delay_slots(samples, 1 / SLOT_S, ingest_lag_ms=0.0)


def test_python_O_safe_no_assert_in_the_tool():
    import ast
    tree = ast.parse(Path(M.__file__).read_text())
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    r = subprocess.run([sys.executable, "-O", "-m", "tools.r4bt_d_measure", "--sample-file", "/data/mal/r4b/x", "--from", FROM, "--to", TO, "--out", "/nonexistent-dir/o"],
                       capture_output=True, text=True, cwd=str(Path(M.__file__).resolve().parent.parent))
    assert r.returncode == 2 and "REFUSED" in r.stderr
