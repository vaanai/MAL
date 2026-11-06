"""Tests for tools/r4bt_feed_bar.py: synthetic counts and sampler files only. No tip row, no tape, no real writer output."""
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r4bt_feed_bar as F  # noqa: E402

TH = F.TH
HR = F.HOUR_MS
H0 = TH.parse_hour("2026-11-03T00")
R0 = H0 + 24 * HR  # first R with 24 counts hours behind it
PINS = {"adapter_sha256": "a" * 64, "reader_sha256": "b" * 64, "writer_sha256": "c" * 64}
TMP = Path(tempfile.mkdtemp(prefix="r4btfb"))
PINSF = str(TMP / "pins.json")
Path(PINSF).write_text(json.dumps(PINS))
N = 60  # counts hours H0 .. H0+59


def crow(h, gap_rows=None, pins=None):
    return {"schema": TH.SCHEMA_COUNTS, "hour": TH.hour_key(h), "bytes": 100, "lines": 10, "sha256": "ab" * 32, "run_id": "r1", "runs": ["r1"], "started_ms": H0 - 10 * HR,
            "pins": pins or PINS, "rows": {"pumpswap|trade": 600}, "gap_rows": gap_rows or {}}


def counts_file(name, over=None, hours=range(N)):
    over = over or {}
    rows = [crow(H0 + i * HR, **over.get(i, {})) for i in hours]
    p = TMP / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(p)


def health_file(name, t0=H0 - 2 * HR, t1=H0 + N * HR, step=60, over=None, drop_key=None, extra=None):
    """Constant sampler rows; over = {t_seconds: {key: value}} is a step that holds from that sample on."""
    over = over or {}
    rows, cur = [], {"s_gaps": 3, "s_backlog_jumps": 0, "s_unresolved_dropped": 0}
    for s in range(t0 // 1000, t1 // 1000, step):
        cur.update(over.get(s, {}))
        r = {"t": s, **cur, "s_skipped": 5, "c_gaps": 9}
        r.update(extra or {})
        if drop_key and s >= drop_key[0]:
            r.pop(drop_key[1], None)
        rows.append(r)
    p = TMP / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(p)


def run(counts, health, r_ms, capsys, pins=PINSF):
    rc = F.main(["check", "--counts", counts, "--health-glob", health, "--pins", pins, "--reg-hour", TH.iso(r_ms)])
    return rc, capsys.readouterr().out


def test_clean_24h_passes(capsys):
    rc, out = run(counts_file("c_clean.jsonl"), health_file("tip-health-clean.jsonl"), R0, capsys)
    assert rc == 0
    assert out.strip().splitlines()[-1].endswith("verdict=PASS")
    assert len(out.strip().splitlines()) == 25


@pytest.mark.parametrize("prog", ["pumpswap", "all"])
def test_data_loss_row_fails_next_24_and_clean_at_25(prog, capsys):
    x = 30  # counts index of hour X
    c = counts_file(f"c_dl_{prog}.jsonl", over={x: {"gap_rows": {f"data_loss|{prog}": 1}}})
    h = health_file("tip-health-dl.jsonl")
    X = H0 + x * HR
    for k in range(1, 25):
        assert run(c, h, X + k * HR, capsys)[0] == 1, k
    assert run(c, h, X + 25 * HR, capsys)[0] == 0
    assert run(c, h, X, capsys)[0] == 0  # R = X: hour X is not yet in the window
    rc = F.main(["scan", "--counts", c, "--health-glob", h, "--pins", PINSF, "--from-hour", TH.iso(X + 1 * HR), "--to-hour", TH.iso(X + 29 * HR)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip().splitlines()[-1] == f"earliest_clean_R {TH.iso(X + 25 * HR)}"
    assert f"data_loss|{prog}=1" in out


def test_other_program_row_not_counted(capsys):
    c = counts_file("c_other.jsonl", over={30: {"gap_rows": {"data_loss|pumpfun": 5}}})
    assert run(c, health_file("tip-health-o.jsonl"), H0 + 40 * HR, capsys)[0] == 0


@pytest.mark.parametrize("key", F.KEYS)
def test_follower_step_between_hours_fails(key, capsys):
    # one step held from a single sample in the middle of hour 30 (a per-hour counter range never sees a step that lands between two hours' samples)
    s = (H0 + 30 * HR + 17 * 60_000) // 1000
    h = health_file("tip-health-step.jsonl", over={s: {key: 4}})
    c = counts_file("c_step.jsonl")
    base = (s * 1000) // HR * HR
    fails = [k for k in range(-2, 40) if run(c, h, base + k * HR, capsys)[0] == 1]
    assert fails and fails == list(range(fails[0], fails[0] + len(fails))) and len(fails) <= 24
    assert f"moved={key}" in run(c, h, base + fails[0] * HR, capsys)[1]


def test_follower_reset_to_lower_value_counts(capsys):
    t = (H0 + 30 * HR) // 1000
    h = health_file("tip-health-reset.jsonl", over={t - 60 * 5: {"s_gaps": 50}, t: {"s_gaps": 2}})
    rc, out = run(counts_file("c_reset.jsonl"), h, H0 + 31 * HR, capsys)
    assert rc == 1 and "moved=s_gaps" in out
    # the reset alone (value goes down once, no earlier rise inside the window)
    h = health_file("tip-health-reset2.jsonl", t0=H0 - 2 * HR, over={t: {"s_gaps": 1}})
    assert run(counts_file("c_reset.jsonl"), h, H0 + 31 * HR, capsys)[0] == 1


def test_follower_move_before_baseline_is_not_a_move(capsys):
    R = H0 + 40 * HR
    t = (R - 30 * HR) // 1000
    h = health_file("tip-health-old.jsonl", over={t: {"s_gaps": 99}})
    assert run(counts_file("c_old.jsonl"), h, R, capsys)[0] == 0


def test_follower_boundary_samples(capsys):
    R = H0 + 40 * HR
    start = (R - 24 * HR) // 1000
    c = counts_file("c_b.jsonl")
    # a change AT R-24h is inside (the baseline is the last sample strictly before R-24h): move
    assert run(c, health_file("tip-health-b1.jsonl", over={start: {"s_gaps": 8}}), R, capsys)[0] == 1
    # a change at the baseline sample (last before R-24h) is outside
    assert run(c, health_file("tip-health-b2.jsonl", over={start - 60: {"s_gaps": 8}}), R, capsys)[0] == 0
    # a change AT R is outside
    assert run(c, health_file("tip-health-b3.jsonl", over={R // 1000: {"s_gaps": 8}}), R, capsys)[0] == 0
    # a change at the last sample before R is inside
    assert run(c, health_file("tip-health-b4.jsonl", over={R // 1000 - 60: {"s_gaps": 8}}), R, capsys)[0] == 1


def test_writer_boundary_hours(capsys):
    R = H0 + 40 * HR
    h = health_file("tip-health-wb.jsonl")
    lo = counts_file("c_wlo.jsonl", over={16: {"gap_rows": {"data_loss|pumpswap": 1}}})  # hour R-24h: inside
    hi = counts_file("c_whi.jsonl", over={40: {"gap_rows": {"data_loss|pumpswap": 1}}})  # hour R: outside
    below = counts_file("c_wbelow.jsonl", over={15: {"gap_rows": {"data_loss|pumpswap": 1}}})  # hour R-25h: outside
    assert run(lo, h, R, capsys)[0] == 1
    assert run(hi, h, R, capsys)[0] == 0
    assert run(below, h, R, capsys)[0] == 0


def test_missing_key_is_incomplete(capsys):
    R = H0 + 40 * HR
    c = counts_file("c_mk.jsonl")
    h = health_file("tip-health-mk.jsonl", drop_key=((R - 5 * HR) // 1000, "s_backlog_jumps"))
    assert run(c, h, R, capsys)[0] == 3
    h = health_file("tip-health-mk2.jsonl", extra={"s_gaps": "x"})  # a non-int value is a missing key
    assert run(c, h, R, capsys)[0] == 3


def test_no_sample_is_incomplete(capsys):
    R = H0 + 40 * HR
    c = counts_file("c_ns.jsonl")
    assert run(c, str(TMP / "tip-health-none-*.jsonl"), R, capsys)[0] == 3
    h = health_file("tip-health-early.jsonl", t0=H0, t1=R - 25 * HR)  # only before the window
    assert run(c, h, R, capsys)[0] == 3
    h = health_file("tip-health-late.jsonl", t0=R - 24 * HR, t1=R)  # only inside the window: no baseline
    assert run(c, h, R, capsys)[0] == 3


def test_missing_counts_hour_is_incomplete_and_fail_dominates(capsys):
    R = H0 + 40 * HR
    c = counts_file("c_mc.jsonl", hours=[i for i in range(N) if i != 30])
    assert run(c, health_file("tip-health-mc.jsonl"), R, capsys)[0] == 3
    h2 = health_file("tip-health-mc2.jsonl", over={(R - 10 * HR) // 1000: {"s_gaps": 7}})
    assert run(c, h2, R, capsys)[0] == 1  # an observed move is final even while a counts hour is missing


def test_sha_mismatch_refusal(tmp_path):
    copy = tmp_path / "tip_hour_health.py"
    shutil.copy(F.WRITER_PATH, copy)
    assert F.load_writer(path=copy) is not None
    copy.write_bytes(copy.read_bytes() + b"\n# x\n")
    with pytest.raises(F.Refused):
        F.load_writer(path=copy)
    with pytest.raises(F.Refused):
        F.load_writer(expected="0" * 64)
    assert hashlib.sha256(F.WRITER_PATH.read_bytes()).hexdigest() == F.WRITER_SHA256
    F.load_writer()  # leave the genuine module registered


def test_pins_handling(capsys):
    R = H0 + 40 * HR
    c, h = counts_file("c_p.jsonl"), health_file("tip-health-p.jsonl")
    for bad in ("auto", ""):
        assert run(c, h, R, capsys, pins=bad)[0] == 2
    assert run(c, h, R, capsys, pins=str(TMP / "nope.json"))[0] == 2
    other = TMP / "pins_other.json"
    other.write_text(json.dumps({k: "d" * 64 for k in PINS}))
    assert run(c, h, R, capsys, pins=str(other))[0] == 1  # writer pins differ in the window
    drift = counts_file("c_pd.jsonl", over={35: {"pins": {k: "e" * 64 for k in PINS}}})
    assert run(drift, h, R, capsys)[0] == 1
    badp = TMP / "pins_bad.json"
    badp.write_text(json.dumps({"adapter_sha256": "zz"}))
    assert run(c, h, R, capsys, pins=str(badp))[0] == 2


def test_input_errors(capsys):
    c, h = counts_file("c_e.jsonl"), health_file("tip-health-e.jsonl")
    assert F.main(["check", "--counts", c, "--health-glob", h, "--pins", PINSF, "--reg-hour", "2026-11-05T10:30:00Z"]) == 2
    assert F.main(["check", "--counts", c, "--health-glob", h, "--pins", PINSF, "--reg-hour", "garbage"]) == 2
    assert F.main(["scan", "--counts", c, "--health-glob", h, "--pins", PINSF, "--from-hour", "2026-11-05T10", "--to-hour", "2026-11-05T09"]) == 2


def test_unregistered_sampler_keys_ignored_and_output_shape(capsys):
    R = H0 + 40 * HR
    h = health_file("tip-health-ign.jsonl", extra={"s_skipped": 5, "c_gaps": 9, "s_retries": 1})
    rc, out = run(counts_file("c_ign.jsonl"), h, R, capsys)
    assert rc == 0
    for ln in out.strip().splitlines():
        assert ln.startswith(("hour ", "R "))
    # a change in an unregistered key is not a move
    p = Path(h)
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    for r in rows:
        if r["t"] > (R - 10 * HR) // 1000:
            r["c_gaps"] = 1234
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert run(counts_file("c_ign.jsonl"), h, R, capsys)[0] == 0


def test_scan_prints_each_hour_once_and_none_in_range(capsys):
    c = counts_file("c_sc.jsonl", over={i: {"gap_rows": {"data_loss|pumpswap": 1}} for i in range(24, 50)})
    h = health_file("tip-health-sc.jsonl")
    rc = F.main(["scan", "--counts", c, "--health-glob", h, "--pins", PINSF, "--from-hour", TH.iso(R0 + 2 * HR), "--to-hour", TH.iso(R0 + 4 * HR)])
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0 and out[-1] == "earliest_clean_R none_in_range"
    assert sum(1 for ln in out if ln.startswith("hour ")) == 24 + 2
    assert sum(1 for ln in out if ln.startswith("R ")) == 3


def test_dead_sampler_tail_is_incomplete(capsys):
    R = H0 + 40 * HR
    h = health_file("tip-health-dead.jsonl", t0=R - 25 * HR, t1=R - 23 * HR)
    rc, out = run(counts_file("c_dead.jsonl"), h, R, capsys)
    assert rc == 3 and "sampler_hour_empty" in out


def test_empty_middle_hour_is_incomplete(capsys):
    R = H0 + 40 * HR
    p = Path(health_file("tip-health-mid.jsonl"))
    lo, hi = (R - 12 * HR) // 1000, (R - 11 * HR) // 1000
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows if not lo <= r["t"] < hi))
    rc, out = run(counts_file("c_mid.jsonl"), str(p), R, capsys)
    assert rc == 3 and "sampler_hour_empty" in out


def _ending_at(name, last, R):
    """Full-coverage sampler whose last sample before R is exactly `last` (seconds)."""
    p = Path(health_file(name))
    rows = [r for r in (json.loads(x) for x in p.read_text().splitlines()) if r["t"] < last]
    rows.append({**rows[-1], "t": last})
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(p)


def test_last_sample_staleness_boundary(capsys):
    R = H0 + 40 * HR
    c = counts_file("c_stale.jsonl")
    r_s = R // 1000
    rc, out = run(c, _ending_at("tip-health-st2.jsonl", r_s - 300, R), R, capsys)
    assert rc == 0, out
    rc, out = run(c, _ending_at("tip-health-st3.jsonl", r_s - 301, R), R, capsys)
    assert rc == 3 and "sampler_stale" in out


def test_fail_dominates_stale_sampler(capsys):
    R = H0 + 40 * HR
    c = counts_file("c_fs.jsonl", over={30: {"gap_rows": {"data_loss|pumpswap": 1}}})
    h = health_file("tip-health-fs.jsonl", t0=R - 25 * HR, t1=R - 23 * HR)
    assert run(c, h, R, capsys)[0] == 1


def test_conflicting_duplicate_t_is_incomplete(capsys):
    R = H0 + 40 * HR
    c = counts_file("c_dup.jsonl")
    health_file("tip-health-dup1.jsonl")
    health_file("tip-health-dup2.jsonl", t0=R - 5 * HR, over={(R - 5 * HR) // 1000: {"s_gaps": 9}})
    rc, out = run(c, str(TMP / "tip-health-dup*.jsonl"), R, capsys)
    assert rc == 3 and "conflicting_duplicate_t" in out
    # identical duplicates are harmless
    health_file("tip-health-same1.jsonl")
    health_file("tip-health-same2.jsonl", t0=R - 5 * HR)
    assert run(c, str(TMP / "tip-health-same*.jsonl"), R, capsys)[0] == 0


def test_sha_mismatch_cli_exits_2(tmp_path):
    import subprocess
    shutil.copy(Path(F.__file__), tmp_path / "r4bt_feed_bar.py")
    th = tmp_path / "tip_hour_health.py"
    shutil.copy(F.WRITER_PATH, th)
    th.write_bytes(th.read_bytes() + b"\n# x\n")
    r = subprocess.run([sys.executable, "-I", str(tmp_path / "r4bt_feed_bar.py"), "check", "--counts", "x", "--health-glob", "y", "--pins", "z", "--reg-hour", "2026-11-05T10"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "refused" in r.stderr and "Traceback" not in r.stderr
