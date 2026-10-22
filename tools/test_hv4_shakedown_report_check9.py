"""Tests for the HV4 report-tool fix (QP-RULING-1022 ADDENDUM 2 item B5; SHAKEDOWN-CHECKS-4-NOTES-2 sections 2(a), 2(b), 2(c), 3).
Check 9: the sample-gap rule (above 900 s = UNMEASURED), a zero-size tape hour = FAIL, the counts-only tape gap scan grouped by (kind, data_loss). QP-M1 is informational.
Synthetic fixtures only; nothing here opens a real tape, ledger or bots file."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tools import hv4_shakedown_report as R
from tools.test_hv4_shakedown_report import HOUR, MIN, START, by_id, build, rewrite_samples, run, sub

CANARY_PRICE = "CANARY-PRICE-424242"


def hour_file(fx: dict, at_ms: int):
    return fx["tape"] / ("tape-" + R.iso(at_ms // HOUR * HOUR)[:13] + ".jsonl")


def add_tape_rows(fx: dict, at_ms: int, rows: list[dict]) -> None:
    with open(hour_file(fx, at_ms), "ab") as f:
        for r in rows:
            f.write(json.dumps(r).encode() + b"\n")


def gap(kind, data_loss, **kw) -> dict:
    return {"v": 1, "type": "gap", "prog": "pumpswap", "t_recv_ms": START + 5 * MIN, "kind": kind, "data_loss": data_loss, "price": CANARY_PRICE, "signature": CANARY_PRICE, **kw}


def set_gaps(fx: dict, last: int) -> None:
    """The status counter 'gaps' rises to `last` over the run (it is 3 in every fixture sample)."""
    rewrite_samples(fx, lambda r: r["feed"].update(gaps=3 if r["t_ms"] < fx["end"] - 30 * MIN else last))


def test_sample_gap_above_900_s_is_unmeasured_and_exactly_900_s_is_not(tmp_path):
    base = by_id(run(build(sub(tmp_path, "ok"))))["9"]
    assert base["status"] == R.PASS and base["numbers"]["feed_samples"]["max_gap_s_between_non_null_samples_inside_w"] == 600

    def shift(delta):
        t0 = START + 242 * MIN  # one sample in the middle moves later: its left gap grows to 600 s + delta, its right gap shrinks
        return lambda fx: rewrite_samples(fx, lambda r: r.update(t_ms=r["t_ms"] + delta) if r["t_ms"] == t0 else None)

    at = by_id(run(build(sub(tmp_path, "g900"), extra=shift(300_000))))["9"]
    assert at["status"] == R.PASS and at["numbers"]["feed_samples"]["max_gap_s_between_non_null_samples_inside_w"] == 900
    over = by_id(run(build(sub(tmp_path, "g901"), extra=shift(301_000))))["9"]
    assert over["status"] == R.UNM and any("above 900 s" in n for n in over["notes"])
    # three missed 300 s samples are a 20 minute gap: UNMEASURED (the old '3 consecutive misses' wording would have let it through)
    dropped = build(sub(tmp_path, "drop"), extra=lambda fx: rewrite_samples(fx, lambda r: r.update(feed=None) if START + 200 * MIN < r["t_ms"] < START + 222 * MIN else None))
    d = by_id(run(dropped))["9"]
    assert d["status"] == R.UNM and "above 900 s" in " ".join(d["notes"])


def test_feed_samples_must_cover_w_edges(tmp_path):
    late_first = build(sub(tmp_path, "a"), extra=lambda fx: fx["samples"].write_text("".join(x + "\n" for x in fx["samples"].read_text().splitlines()[2:])))  # first sample at START + 22 min
    a = by_id(run(late_first))["9"]
    assert a["status"] == R.UNM and any("do not cover W" in n for n in a["notes"])
    early_last = build(sub(tmp_path, "b"), extra=lambda fx: fx["samples"].write_text("".join(x + "\n" for x in fx["samples"].read_text().splitlines()[:-3])))
    assert by_id(run(early_last))["9"]["status"] == R.UNM


def test_zero_size_tape_hour_is_a_fail(tmp_path):
    fx = build(tmp_path)
    hour_file(fx, START + 5 * HOUR).write_bytes(b"")
    c = by_id(run(fx))["9"]
    assert c["status"] == R.FAIL and len(c["numbers"]["tape_hour_files_zero_size"]) == 1 and any("size 0" in n for n in c["notes"])
    assert c["numbers"]["tape_hour_sizes_bytes"][R.iso(START + 5 * HOUR)[:13]] == 0


def test_tape_gap_scan_is_counts_only_grouped_by_kind_and_data_loss(tmp_path):
    fx = build(tmp_path)
    add_tape_rows(fx, START + 5 * MIN, [gap("socket_reconnect", False)] * 3 + [gap("slot_jump", True)] * 2 + [gap("silence", True)] + [gap("decode_error", True, count=7)]
                  + [{"type": "trade", "t_recv_ms": START, "price": CANARY_PRICE}, gap(CANARY_PRICE, False)])
    add_tape_rows(fx, START + 7 * HOUR, [gap("socket_reconnect", False)])
    with open(hour_file(fx, START + 7 * HOUR), "ab") as f:
        f.write(b'{"type": "gap", "kind": "tor')  # a torn tail line
    set_gaps(fx, 3 + 10)
    rec = run(fx)
    c = by_id(rec)["9"]
    n = c["numbers"]
    assert c["status"] == R.PASS, c["notes"]
    assert {(r["kind"], r["data_loss"]): r["rows"] for r in n["tape_gap_scan"]["by_kind"]} == {("socket_reconnect", False): 4, ("slot_jump", True): 2, ("silence", True): 1, ("decode_error", True): 1, ("other", False): 1}
    assert n["gaps_total_tape"] == 9 and n["tape_gap_scan"]["gaps_data_loss_true"] == 4 and n["tape_gap_scan"]["data_loss_rows_count_field_sum"] == 7
    assert {r["kind"] for r in n["gaps_data_loss_true_by_kind"]} == {"slot_jump", "silence", "decode_error"}
    assert n["tape_gap_scan"]["torn_candidate_lines"] == 1
    assert n["feed_samples"]["data_loss_upper_bound_gaps_all_kinds_delta"] == 10
    assert CANARY_PRICE not in json.dumps(rec)  # kinds and counts only: no price, signature or other row field


def test_tape_data_loss_above_the_status_all_kinds_delta_is_unmeasured(tmp_path):
    fx = build(tmp_path)
    add_tape_rows(fx, START + 5 * MIN, [gap("slot_jump", True)] * 2)
    c = by_id(run(fx))["9"]  # the status counter did not move (delta 0)
    assert c["status"] == R.UNM and any("disagreement" in n for n in c["notes"])
    set_gaps(fx, 5)
    assert by_id(run(fx))["9"]["status"] == R.PASS


def test_data_loss_gap_of_kind_disk_reserve_is_the_paused_reserve_fail(tmp_path):
    fx = build(tmp_path)
    add_tape_rows(fx, START + 5 * MIN, [gap("disk_reserve", True, count=3)])
    set_gaps(fx, 9)
    assert by_id(run(fx))["9"]["status"] == R.FAIL


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd CLI not installed")
def test_tape_gap_scan_reads_zst_hours(tmp_path):
    fx = build(tmp_path)
    plain = hour_file(fx, START + 3 * HOUR)
    plain.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in [{"type": "hb", "t_recv_ms": START}, gap("silence", True), gap("socket_reconnect", False)]))
    subprocess.run(["zstd", "-q", "--rm", str(plain)], check=True)
    set_gaps(fx, 4)
    n = by_id(run(fx))["9"]["numbers"]
    assert {(r["kind"], r["data_loss"]): r["rows"] for r in n["tape_gap_scan"]["by_kind"]} == {("silence", True): 1, ("socket_reconnect", False): 1}
    bad = hour_file(fx, START + 4 * HOUR)
    bad.write_bytes(b"not zstd")
    bad.rename(bad.with_name(bad.name + ".zst"))
    c = by_id(run(fx))["9"]
    assert c["status"] == R.UNM and any("did not complete" in n for n in c["notes"])  # a truncated or corrupt hour is never a PASS


def test_qp_m1_is_informational_and_outside_the_verdict(tmp_path):
    rec = run(build(sub(tmp_path, "oom"), oom=1))
    m = by_id(rec)["QP-M1"]
    assert m["status"] == R.FAIL and m["informational"] is True and m["numbers"]["oom_kill_max"] == 1  # shown, numbers kept
    assert "QP-M1" not in rec["unmeasured_checks"] and "QP-M1" not in (rec.get("verdict_reason") or "")
    none = run(build(sub(tmp_path, "none")), samples=False)
    assert by_id(none)["QP-M1"]["status"] == R.UNM and "QP-M1" not in none["unmeasured_checks"]
    assert R.markdown(none)  # still renders


def test_sample_lines_written_by_the_sample_subcommand_are_parsed(tmp_path):
    """The sampler is unchanged: a line written by `sample` (the 9ed4c5f code path) carries the feed dict that check 9 reads."""
    fx = build(tmp_path)
    status = tmp_path / "feed-status.json"
    status.write_text(json.dumps({"writing_paused": False, "lines_written": 1000, "gaps": 3, "disk_free_pct": 40.5, "updated_ms": 1, "disk_reserve": {"pauses": 0, "rows_dropped_total": 0}}))
    out = tmp_path / "written.jsonl"
    assert R.main(["sample", "--dir", str(fx["base"]), "--out", str(out), "--feed-status", str(status), "--until", "2000-01-01T00:00:00Z", "--interval-s", "1"]) == 0
    lines = out.read_text().splitlines()
    assert len(lines) == 1
    parsed, errs, note = R.load_samples(out, [])
    assert errs == 0 and note is None and len(parsed) == 1
    feed = parsed[0]["feed"]
    assert set(feed) == {"writing_paused", "lines_written", "gaps", "pauses", "rows_dropped_total", "disk_free_pct", "updated_ms"} and feed["disk_free_pct"] == 40.5
    # a feed sample line expanded across W, every 300 s, built from the sampler's own line, is measured by the new check 9
    proto = json.loads(lines[0])
    expanded = []
    for k, t in enumerate(range(START, fx["end"] + 1, 300_000)):
        expanded.append({**proto, "t_ms": t, "feed": {**proto["feed"], "lines_written": 1000 + k * 10}})
    fx["samples"].write_text("".join(json.dumps(r) + "\n" for r in expanded))
    c = by_id(run(fx))["9"]
    assert c["status"] == R.PASS and c["numbers"]["feed_samples"]["max_gap_s_between_non_null_samples_inside_w"] == 300
    # a sampler line whose status file was unreadable has "feed": null and is not a feed sample
    status.write_text("{")
    R.main(["sample", "--dir", str(fx["base"]), "--out", str(out), "--feed-status", str(status), "--until", "2000-01-01T00:00:00Z", "--interval-s", "1"])
    parsed, _, _ = R.load_samples(out, [])
    assert parsed[-1]["feed"] is None
