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
NOW = H0 + 100 * HR  # everything under test is closed unless a test says otherwise


def T(v, label="decision-input"):
    return {"value": v, "label": label}


THR = {"max_gap_s": T(30, "price-path"), "max_data_loss_count": T(0, "price-path"), "min_samples_per_hour": T(10), "max_sample_gap_s": T(900), "max_input_age_s": T(120, "price-path")}


def tr(h, a=0, b=3600, step=1, prog="pumpswap", typ="trade"):
    return [{"v": 1, "type": typ, "prog": prog, "t_recv_ms": h + s * 1000} for s in range(a, b, step)]


def both(h, a=0, b=3600, step=30):
    return tr(h, a, b, step, "pumpswap") + tr(h, a, b, step, "pumpfun")


def hb(h):
    return [{"type": "hb", "prog": "pumpswap", "t_recv_ms": h + i * 60000 + 500} for i in range(60)]


def write_hour(d, h, rows, zst=False):
    p = d / A.hour_name(h)
    p.write_text("".join(json.dumps(r) + "\n" for r in sorted(rows, key=lambda r: r["t_recv_ms"])))
    if zst:
        subprocess.run(["zstd", "-q", "--rm", str(p)], check=True)


def gap(kind, f, t, closed=True, at=None):
    return {"type": "gap", "prog": "pumpswap", "kind": kind, "data_loss": True, "closed": closed, "from_ms": f, "to_ms": t, "t_recv_ms": at if at is not None else t}


def samples_file(path, times, lag=1000):
    with open(path, "w") as f:
        for t in times:
            f.write(json.dumps({"schema": A.SAMPLE_SCHEMA, "t_ms": t, "feed": {"updated_ms": t - lag}}) + "\n")


def ev(tmp, hours=4, thr=THR, samples=True, now=NOW, programs=A.DEFAULT_PROGRAMS, prev=True):
    pf = tmp / "tape" / A.hour_name(H0 - HR)
    if prev and not pf.exists():  # R1: a known, healthy hour before the window, so the first hour's age is measurable
        write_hour(tmp / "tape", H0 - HR, both(H0 - HR))
    return A.evaluate(tmp / "tape", tmp / "s.jsonl" if samples else None, H0, H0 + (hours - 1) * HR, thr, now, programs)


def run(tmp, **kw):
    rows, nc = ev(tmp, **kw)
    return A.render(rows, "x", nc), rows


@pytest.fixture
def world(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, both(H0) + hb(H0))  # clean
    # H0+1 missing
    (d / A.hour_name(H0 + 2 * HR)).write_text("")  # zero size
    write_hour(d, H0 + 3 * HR, both(H0 + 3 * HR) + [gap("silence", H0 + 3 * HR + 100, H0 + 3 * HR + 61100)])
    samples_file(tmp_path / "s.jsonl", [H0 + i * 60_000 for i in range(60)] + [H0 + 3 * HR + i * 60_000 for i in range(60)])
    return tmp_path


def test_clean_missing_zero_dataloss(world):
    _, r = run(world)
    assert r[0]["excluded"] is False and r[0]["failed_checks"] == [] and r[0]["unmeasurable"] == [] and r[0]["failed_labels"] == {}
    assert "tape_file_missing" in r[1]["failed_checks"] and r[1]["excluded"]
    assert "tape_file_zero_size" in r[2]["failed_checks"] and r[2]["tape_size"] == 0
    assert r[3]["data_loss_rows"] == 1 and r[3]["data_loss_max_span_s"] == 61.0
    assert {"max_gap_s", "max_data_loss_count"} <= set(r[3]["failed_checks"])
    assert r[3]["gap_rows"] == [{"kind": "silence", "data_loss": True, "rows": 1}]


def test_E5_labels_printed(world):
    _, r = run(world)
    assert r[3]["failed_labels"]["max_gap_s"] == "price-path" and r[3]["failed_labels"]["max_data_loss_count"] == "price-path"
    assert r[1]["failed_labels"]["max_gap_s"] == "price-path"  # unmeasurable checks print their label too
    thr = {"max_gap_s": {"value": 1, "label": "bogus"}}
    p = world / "t.json"
    p.write_text(json.dumps(thr))
    with pytest.raises(ValueError):
        A.load_thresholds(p)
    p.write_text(json.dumps({"max_gap_s": 5}))  # old flat format
    with pytest.raises(ValueError):
        A.load_thresholds(p)


def test_sampler_gap_and_samples_required(world):
    samples_file(world / "s.jsonl", [H0 + i * 60_000 for i in range(5)] + [H0 + 50 * 60_000 + i for i in range(20)])
    _, r = run(world, hours=1)
    assert r[0]["excluded"] and "max_sample_gap_s" in r[0]["failed_checks"] and r[0]["failed_labels"]["max_sample_gap_s"] == "decision-input"
    thr = world / "thr.json"
    thr.write_text(json.dumps(THR))
    assert A.main(["--tape-dir", str(world / "tape"), "--from", "2026-10-23T00", "--to", "2026-10-23T00", "--thresholds", str(thr)]) == 2
    _, r2 = run(world, hours=1, thr={"max_gap_s": T(30)}, samples=False)
    assert r2[0]["excluded"] is False and r2[0]["samples_n"] is None


def test_E1_only_data_rows_count_heartbeats_never(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    # hb every minute but data only in the first 10 minutes: a 50 min data silence must fail
    write_hour(d, H0, both(H0, 0, 600) + hb(H0))
    write_hour(d, H0 + HR, both(H0 + HR))
    _, r = run(tmp_path, hours=2, thr={"max_input_age_s": T(120, "price-path")}, samples=False)
    assert r[0]["tape_input_age_max_s"] == 3030.0  # last data row at +570 s (step 30), next at the next hour start
    assert r[0]["excluded"] and r[0]["failed_labels"] == {"max_input_age_s": "price-path"}


@pytest.mark.parametrize("typ", ["hb", "start", "stop", "pool_info"])
def test_E1_non_data_types_never_count(tmp_path, typ):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, both(H0, 0, 60) + tr(H0, 60, 3600, 5, "pumpswap", typ) + tr(H0, 60, 3600, 5, "pumpfun", typ))
    _, r = run(tmp_path, hours=1, thr={"max_input_age_s": T(120)}, samples=False, now=H0 + 10 * HR)
    assert r[0]["excluded"]


@pytest.mark.parametrize("typ", ["trade", "create", "complete", "pool_create"])
def test_E1_data_types_count(tmp_path, typ):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, tr(H0, 0, 3600, 30, "pumpswap", typ) + tr(H0, 0, 3600, 30, "pumpfun", typ))
    _, r = run(tmp_path, hours=1, thr={"max_input_age_s": T(120)}, samples=False)
    assert r[0]["excluded"] is False and r[0]["tape_input_age_max_s"] <= 60


def test_E1_per_program(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, tr(H0, 0, 3600, 30, "pumpswap") + tr(H0, 0, 600, 30, "pumpfun"))
    thr = {"max_input_age_s": T(120)}
    _, r = run(tmp_path, hours=1, thr=thr, samples=False)
    assert r[0]["excluded"] and r[0]["tape_input_age_by_prog_s"]["pumpswap"] <= 60
    _, r = run(tmp_path, hours=1, thr=thr, samples=False, programs=("pumpswap",))
    assert r[0]["excluded"] is False
    _, r = run(tmp_path, hours=1, thr=thr, samples=False, programs=("pumpswap", "nosuch"))
    assert "max_input_age_s[tape]" in r[0]["unmeasurable"]  # a program with no data row at all is not measurable


def test_E2_gap_span_fails_every_overlapped_hour_and_dedups(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    f, t = H0 + 3_000_000, H0 + HR + 300_000  # outage 00:50 -> 01:05, feed process alive
    write_hour(d, H0, both(H0, 0, 3000) + hb(H0) + [gap("silence", f, f + 20_000, closed=False, at=f + 20_000)])
    write_hour(d, H0 + HR, [gap("silence", f, t)] + both(H0 + HR, 300, 3600) + hb(H0 + HR))
    thr = {"max_gap_s": T(30, "price-path"), "max_data_loss_count": T(0, "price-path")}
    _, r = run(tmp_path, hours=2, thr=thr, samples=False)
    for x in r:
        assert x["data_loss_rows"] == 1 and x["data_loss_max_span_s"] == 900.0 and x["excluded"]  # open+closed twin collapses to one span; both hours fail


def test_E2_open_gap_without_twin_counts(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, both(H0) + [gap("silence", H0 + 100_000, H0 + 140_000, closed=False)])
    _, r = run(tmp_path, hours=1, thr={"max_data_loss_count": T(0)}, samples=False)
    assert r[0]["data_loss_rows"] == 1 and r[0]["excluded"]


def test_E2_unreadable_gap_span_unmeasurable(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, both(H0) + [{"type": "gap", "kind": "silence", "data_loss": True, "t_recv_ms": H0 + 5}])
    _, r = run(tmp_path, hours=1, thr={"max_gap_s": T(30)}, samples=False)
    assert r[0]["excluded"] and "gap_span_unreadable" in r[0]["unmeasurable"]


def test_E3_silence_across_boundary_fails_both_hours(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0 + HR, tr(H0 + HR))
    write_hour(d, H0 + 2 * HR, tr(H0 + 2 * HR, 0, 3540))
    write_hour(d, H0 + 3 * HR, tr(H0 + 3 * HR, 90, 3600))
    thr = {"max_input_age_s": T(120, "price-path")}
    _, r = run(tmp_path, hours=4, thr=thr, samples=False, programs=("pumpswap",))
    assert r[2]["tape_input_age_max_s"] == 151.0 and r[3]["tape_input_age_max_s"] == 151.0
    assert r[2]["excluded"] and r[3]["excluded"]  # H0 and H0+1 files do not exist (failed for that reason)


def test_E3_exact_150s(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, tr(H0, 0, 3540) + [{"type": "trade", "prog": "pumpswap", "t_recv_ms": H0 + 3_540_000}])
    write_hour(d, H0 + HR, [{"type": "trade", "prog": "pumpswap", "t_recv_ms": H0 + HR + 90_000}] + tr(H0 + HR, 91, 3600))
    _, r = run(tmp_path, hours=2, thr={"max_input_age_s": T(120)}, samples=False, programs=("pumpswap",))
    assert [x["tape_input_age_max_s"] for x in r] == [150.0, 150.0] and all(x["excluded"] for x in r)


def test_E4_open_hours_not_tabled(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, both(H0))
    write_hour(d, H0 + HR, both(H0 + HR))
    now = H0 + 3 * HR + 299_000  # hour 02 ended 299 s ago: still inside the 300 s compaction grace, so hour 01 (needs h+1 closed) is not tabled
    lines, rows = run(tmp_path, hours=2, thr={"max_gap_s": T(30)}, samples=False, now=now)
    assert [x["hour"] for x in rows] == [A.iso(H0)]
    fin = json.loads(lines[-1])
    assert fin["hours"] == 1 and fin["not_closed_hours_not_tabled"] == [A.iso(H0 + HR)]
    _, rows = run(tmp_path, hours=2, thr={"max_gap_s": T(30)}, samples=False, now=H0 + 3 * HR + 300_000)
    assert len(rows) == 2


def test_E4_cli_now_flag(world, capsys):
    thr = world / "thr.json"
    thr.write_text(json.dumps({"max_gap_s": T(30)}))
    assert A.main(["--tape-dir", str(world / "tape"), "--from", "2026-10-23T00", "--to", "2026-10-23T03", "--thresholds", str(thr), "--now", "2026-10-23T01:00:00Z"]) == 0
    out = capsys.readouterr().out.strip().split("\n")
    assert len(out) == 1 and json.loads(out[0])["hours"] == 0


def test_input_age_from_tape_gap(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, [{"type": "trade", "prog": "pumpswap", "t_recv_ms": H0 + 10}, {"type": "trade", "prog": "pumpswap", "t_recv_ms": H0 + 600_000}])
    _, r = run(tmp_path, hours=1, thr={"max_input_age_s": T(120)}, samples=False, programs=("pumpswap",))
    assert r[0]["tape_input_age_max_s"] == 3000.0 and "max_input_age_s[tape]" in r[0]["failed_checks"]


def test_tape_without_feed_time_unmeasurable(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    write_hour(d, H0, [{"type": "trade", "prog": "pumpswap", "t_recv_ms": H0 + 1}][:0] + [{"type": "gap", "kind": "x", "data_loss": False, "t_recv_ms": H0}])
    _, r = run(tmp_path, hours=1, thr={"max_input_age_s": T(120)}, samples=False, prev=False)
    assert "max_input_age_s[tape]" in r[0]["unmeasurable"] and r[0]["excluded"]


def test_zst_hour(world):
    if not shutil.which("zstd"):
        pytest.skip("no zstd")
    write_hour(world / "tape", H0, both(H0), zst=True)
    _, r = run(world, hours=1)
    assert r[0]["tape_file"].endswith(".zst") and r[0]["excluded"] is False


def test_deterministic_sha(world):
    a, _ = run(world)
    b, _ = run(world)
    assert a == b
    fin = json.loads(a[-1])
    assert fin["sha256"] == hashlib.sha256("".join(x + "\n" for x in a[:-1]).encode()).hexdigest()
    assert fin["hours"] == 4 and fin["excluded_hours"] == 3


@pytest.mark.parametrize("bad", ["ledger", "decisions", "bots.json", "runner-status"])
def test_forbidden_paths_exit_2(world, bad):
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
    assert A.main(["--tape-dir", str(world / "tape"), "--samples", str(world / "s.jsonl"), "--from", "2026-10-23T00", "--to", "2026-10-23T03", "--thresholds", str(thr),
                   "--now", "2026-12-01T00:00:00Z"]) == 0
    out = capsys.readouterr().out.strip().split("\n")
    assert len(out) == 5 and json.loads(out[-1])["thresholds_sha256"] == hashlib.sha256(thr.read_bytes()).hexdigest()
    thr.write_text(json.dumps({"max_pnl": T(1)}))
    assert A.main(["--tape-dir", str(world / "tape"), "--from", "2026-10-23T00", "--to", "2026-10-23T00", "--thresholds", str(thr)]) == 2


def test_no_forbidden_fields_in_output(world):
    out = "\n".join(run(world)[0])
    for w in ("signature", "mint", "wallet", "price_sol", "pnl", "fill"):
        assert w not in out


def test_torn_lines_make_hour_unmeasurable(world):
    base = "".join(json.dumps(r) + "\n" for r in both(H0))
    p = world / "tape" / A.hour_name(H0)
    g = json.dumps(gap("silence", 1, 2))
    for body in (base + g[:-8], base + g[:-1] + "\n", base + '{"type":"trade","prog":"pumpswap","t_recv_ms":5}'):
        p.write_text(body)
        _, r = run(world, hours=1)
        assert r[0]["excluded"] and "tape_torn_lines" in r[0]["unmeasurable"] and r[0]["torn_lines"] >= 1 and r[0]["data_loss_rows"] is None


def test_missing_tape_dir_exit_2(world):
    thr = world / "thr.json"
    thr.write_text(json.dumps({"max_gap_s": T(1)}))
    assert A.main(["--tape-dir", str(world / "nope"), "--from", "2026-10-23T00", "--to", "2026-10-23T00", "--thresholds", str(thr)]) == 2


def test_both_jsonl_and_zst_ambiguous(world):
    (world / "tape" / (A.hour_name(H0) + ".zst")).write_bytes(b"x")
    _, r = run(world, hours=1)
    assert r[0]["excluded"] and "tape_ambiguous_files" in r[0]["unmeasurable"]


@pytest.mark.parametrize("bad_row", [{"feed": None}, {"feed": {"updated_ms": 10**15}}])
def test_sample_without_feed_time_or_negative_age(world, bad_row):
    with open(world / "s.jsonl", "w") as f:
        for i in range(30):
            f.write(json.dumps({"schema": A.SAMPLE_SCHEMA, "t_ms": H0 + i * 60_000, **(bad_row if i == 7 else {"feed": {"updated_ms": H0 + i * 60_000 - 1000}})}) + "\n")
    _, r = run(world, hours=1, thr={"max_input_age_s": T(120)})
    assert r[0]["excluded"] and "max_input_age_s[sampler]" in r[0]["unmeasurable"] and r[0]["samples_n"] == 30


def test_R3_hour_tabled_only_after_next_hour_closed_and_read(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    # the span's closed row is written in hour 01 and overlaps the tail of hour 00
    f, t = H0 + 3_500_000, H0 + HR + 60_000
    write_hour(d, H0, both(H0, 0, 3500))
    write_hour(d, H0 + HR, [gap("silence", f, t)] + both(H0 + HR, 60, 3600))
    thr = {"max_data_loss_count": T(0, "price-path")}
    _, rows = run(tmp_path, hours=1, thr=thr, samples=False, now=H0 + HR + 400_000)  # hour 00 closed, hour 01 not: nothing tabled
    assert rows == []
    _, rows = run(tmp_path, hours=1, thr=thr, samples=False, now=H0 + 2 * HR + 300_000)
    assert rows[0]["data_loss_rows"] == 1 and rows[0]["excluded"]  # the h+1 gap row re-decides nothing: it was read before tabling


def test_R1_previous_hour_unknown_makes_age_unmeasurable(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    base = "".join(json.dumps(r) + "\n" for r in both(H0 + HR, 0, 4000))
    (d / A.hour_name(H0 + HR)).write_text(base + '{"type":"trade","prog":"pump')  # torn tail: hour 01 unknown
    write_hour(d, H0 + 2 * HR, both(H0 + 2 * HR, 30, 3600))  # hour 02 itself is fine, data starts 30 s in
    _, r = run(tmp_path, hours=3, thr={"max_input_age_s": T(120, "price-path")}, samples=False, prev=False)
    assert r[2]["prev_hour_unknown"] is True and r[2]["tape_input_age_max_s"] is None
    assert "max_input_age_s[tape]" in r[2]["unmeasurable"] and r[2]["excluded"] and r[2]["failed_labels"] == {"max_input_age_s": "price-path"}
    # a known previous hour keeps it measurable
    write_hour(d, H0 + HR, both(H0 + HR, 0, 3600))
    _, r = run(tmp_path, hours=3, thr={"max_input_age_s": T(120, "price-path")}, samples=False)
    assert r[2]["prev_hour_unknown"] is False and r[2]["tape_input_age_max_s"] == 60.0 and r[2]["excluded"] is False


def _lab(r):
    return r["labels_failing"]


def test_R2_tape_level_failures_carry_both_labels(tmp_path):
    d = tmp_path / "tape"
    d.mkdir()
    thr = {"max_input_age_s": T(120, "decision-input")}  # only one label pinned
    g = json.dumps(gap("silence", 1, 2))
    # 00 missing; 01 zero; 02 torn; 03 ambiguous; 04 unreadable span
    (d / A.hour_name(H0 + HR)).write_text("")
    (d / A.hour_name(H0 + 2 * HR)).write_text(json.dumps(both(H0)[0]) + "\n" + g[:-5])
    write_hour(d, H0 + 3 * HR, both(H0 + 3 * HR))
    (d / (A.hour_name(H0 + 3 * HR) + ".zst")).write_bytes(b"x")
    write_hour(d, H0 + 4 * HR, [{"type": "gap", "kind": "s", "data_loss": True, "t_recv_ms": H0 + 4 * HR}])
    write_hour(d, H0 + 5 * HR, both(H0 + 5 * HR))  # healthy, previous hour unknown -> only the pinned label
    write_hour(d, H0 + 6 * HR, both(H0 + 6 * HR))  # healthy
    _, r = run(tmp_path, hours=7, thr=thr, samples=False, prev=False)
    names = ["tape_file_missing", "tape_file_zero_size", "tape_torn_lines", "tape_ambiguous_files", "gap_span_unreadable"]
    for x, n in zip(r[:5], names):
        assert n in x["tape_level_failures"] and _lab(x) == ["decision-input", "price-path"], n
    assert r[5]["tape_level_failures"] == [] and _lab(r[5]) == ["decision-input"]  # R1 unmeasurable carries the pinned label only
    assert r[6]["labels_failing"] == [] and r[6]["excluded"] is False


# ---- optional feed-status checks (HV4 L7) ----
FT = {"feed_writing_paused": T(0, "price-path"), "feed_pauses_delta": T(0, "price-path"), "feed_rows_dropped_delta": T(0, "price-path")}


def feed_samples(path, rows):
    """rows: [(t_ms, writing_paused, pauses, rows_dropped_total)] or (t_ms, None) for a null feed section."""
    with open(path, "w") as f:
        for r in rows:
            feed = None if r[1] is None else {"writing_paused": r[1], "pauses": r[2], "rows_dropped_total": r[3], "updated_ms": r[0] - 1000}
            f.write(json.dumps({"schema": A.SAMPLE_SCHEMA, "t_ms": r[0], "feed": feed}) + "\n")


def feed_world(tmp_path, rows, hours=2, thr=FT):
    d = tmp_path / "tape"
    d.mkdir()
    for k in range(-1, hours + 2):
        write_hour(d, H0 + k * HR, both(H0 + k * HR))
    feed_samples(tmp_path / "s.jsonl", rows)
    return run(tmp_path, hours=hours, thr=thr)[1]


def clean_rows(a=-1, b=4, extra=()):
    return [(H0 + k * HR + m * 300_000, False, 0, 0) for k in range(a, b) for m in range(12)] + list(extra)


def test_feed_clean_hours_pass(tmp_path):
    r = feed_world(tmp_path, clean_rows())
    assert all(not x["excluded"] and x["feed_writing_paused"] == 0 and x["feed_pauses_delta"] == 0 and x["feed_rows_dropped_delta"] == 0 for x in r)


def test_feed_writing_paused_fails_only_that_hour(tmp_path):
    rows = [(t, p or (H0 + HR + 600_000 == t), 0, 0) for t, p, _, _ in clean_rows()]
    r = feed_world(tmp_path, rows)
    assert not r[0]["excluded"] and r[1]["feed_writing_paused"] == 1 and "feed_writing_paused" in r[1]["failed_checks"]
    assert r[1]["failed_labels"] == {"feed_writing_paused": "price-path"} and r[1]["labels_failing"] == ["price-path"]


def test_feed_pauses_delta_carried_across_boundary(tmp_path):
    # the counter rises between the last sample of hour 00 and the first of hour 01: the interval overlaps BOTH hours
    rows = [(t, False, 1 if t > H0 + HR else 0, 0) for t, _, _, _ in clean_rows() if t != H0 + HR]  # no sample on the boundary: the pair straddles it
    r = feed_world(tmp_path, rows)
    assert r[0]["feed_pauses_delta"] == 1 and r[1]["feed_pauses_delta"] == 1 and r[0]["excluded"] and r[1]["excluded"]
    assert "feed_pauses_delta" in r[0]["failed_checks"] and r[0]["feed_rows_dropped_delta"] == 0


def test_feed_rows_dropped_delta_fails(tmp_path):
    rows = [(t, False, 0, 5 if t >= H0 + HR + 1_800_000 else 0) for t, _, _, _ in clean_rows()]
    r = feed_world(tmp_path, rows)
    assert not r[0]["excluded"] and r[1]["feed_rows_dropped_delta"] == 5 and "feed_rows_dropped_delta" in r[1]["failed_checks"]


def test_feed_too_few_samples_unmeasurable(tmp_path):
    rows = [x for x in clean_rows() if not (H0 + HR <= x[0] < H0 + 2 * HR)]  # no sample at all inside hour 01
    r = feed_world(tmp_path, rows)
    assert "feed_writing_paused" in r[1]["unmeasurable"] and r[1]["excluded"] and r[1]["feed_writing_paused"] is None
    assert not r[0]["excluded"] or "feed_writing_paused" not in r[0]["unmeasurable"]


def test_feed_delta_needs_coverage_before_and_after(tmp_path):
    r = feed_world(tmp_path, [x for x in clean_rows(a=0) if x[0] > H0])  # no sample at or before the first hour start: not covered
    assert r[0]["feed_pauses_delta"] is None and "feed_pauses_delta" in r[0]["unmeasurable"] and r[0]["feed_writing_paused"] == 0
    assert r[1]["feed_pauses_delta"] == 0 and not r[1]["excluded"]


def test_feed_null_sections_ignored_and_counter_reset_unmeasurable(tmp_path):
    rows = clean_rows(extra=[(H0 + 5, None)])
    assert not any(x["excluded"] for x in feed_world(tmp_path, rows))
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    rows = [(t, False, 9 if t < H0 + HR + 600_000 else 0, 0) for t, _, _, _ in clean_rows()]  # counter goes down: the feed restarted
    r = feed_world(tmp2, rows)
    assert "feed_pauses_delta" in r[1]["unmeasurable"] and r[1]["excluded"]


def test_feed_checks_need_samples_flag_and_validation(tmp_path):
    thr = tmp_path / "t.json"
    thr.write_text(json.dumps({"feed_pauses_delta": T(0, "price-path")}))
    (tmp_path / "tape").mkdir()
    assert A.main(["--tape-dir", str(tmp_path / "tape"), "--from", "2026-10-23T00", "--to", "2026-10-23T00", "--thresholds", str(thr)]) == 2
    assert set(A.FEED_KEYS) <= set(A.THRESHOLD_KEYS)


def test_feed_not_configured_leaves_rows_unchanged(world):
    _, r = run(world)
    assert not any(k in r[0] for k in A.FEED_KEYS)


def test_feed_max_nonnull_gap_carried_across_hours(tmp_path):
    # QP case: 30 min of sampler silence across 01:00 (last sample 00:45, next 01:15) fails BOTH hour 00 and hour 01 at 900 s
    rows = [x for x in clean_rows() if not (H0 + HR - 900_000 < x[0] < H0 + HR + 900_000)]
    d = tmp_path / "tape"
    d.mkdir()
    for k in range(-1, 4):
        write_hour(d, H0 + k * HR, both(H0 + k * HR))
    feed_samples(tmp_path / "s.jsonl", rows)
    r = run(tmp_path, hours=3, thr={"feed_max_nonnull_gap_s": T(900, "price-path")})[1]
    assert r[0]["feed_max_nonnull_gap_s"] == 1800.0 and r[1]["feed_max_nonnull_gap_s"] == 1800.0
    assert r[0]["excluded"] and r[1]["excluded"] and not r[2]["excluded"] and r[2]["feed_max_nonnull_gap_s"] == 300.0
    assert r[0]["failed_labels"] == {"feed_max_nonnull_gap_s": "price-path"}


def test_feed_max_nonnull_gap_null_samples_do_not_bridge_and_coverage(tmp_path):
    # null-feed samples are not non-null samples: 20 min of nulls is a 20 min non-null gap
    rows = [x for x in clean_rows() if not (H0 + 1_800_000 < x[0] < H0 + 3_000_000)] + [(H0 + 2_000_000, None), (H0 + 2_500_000, None)]
    GT = {"feed_max_nonnull_gap_s": T(900, "price-path")}
    r = feed_world(tmp_path, rows, thr=GT)
    assert r[0]["feed_max_nonnull_gap_s"] == 1200.0 and r[0]["excluded"]
    (tmp_path / "b").mkdir()
    r2 = feed_world(tmp_path / "b", [x for x in clean_rows(a=0) if x[0] > H0], thr=GT)
    assert r2[0]["feed_max_nonnull_gap_s"] is None and "feed_max_nonnull_gap_s" in r2[0]["unmeasurable"]
