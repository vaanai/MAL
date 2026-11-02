"""r4bt_sample_keys: the REAL sampler key names (t, s_ts_ms, s_decoder_blobs_pinned, s_trade_event_v, s_gaps, ..., c_gaps, c_skipped), synthetic values. No file of the host is read."""
from __future__ import annotations

import json

import pytest

from tools import a3_redeploy_check as ARC
from tools import r4bt_a3 as A
from tools import r4bt_sample_keys as K

T0 = 1_794_000_000  # epoch seconds
RUN = "2026-11-08T07:11:00Z"
NOW = ARC.utc_ms(RUN) + 1000


def line(t, **over):
    r = {"t": t, "s_ts_ms": t * 1000 - 1000, "s_decoder_blobs_pinned": True, "s_trade_event_v": True, "s_gaps": 3, "s_skipped": 5, "s_backlog_jumps": 0, "s_unresolved_dropped": 0,
         "s_block_lag_ms_p90": 900, "s_retries": 0, "s_breaker_trips": 0, "c_gaps": 3, "c_skipped": 5}
    r.update(over)
    return json.dumps(r)


def healthy(n=5):
    return [line(T0 + 60 * i) for i in range(n)]


def test_the_unmapped_sampler_lines_halt_the_classifier_at_instant_0_which_is_the_blocker():
    raw = [json.loads(x) for x in healthy()]
    assert A.decoder_failure_ms(raw) == 0  # the keys it reads are absent: every line "fails" with no usable time


def test_mapped_healthy_lines_are_accepted_by_the_classifier_and_carry_the_sampler_instant():
    rows = K.map_samples([healthy()])
    assert [r["ts_ms"] for r in rows] == [(T0 + 60 * i) * 1000 for i in range(5)]
    assert all(set(r) == {"ts_ms", "decoder_blobs_pinned", "trade_event_v"} for r in rows)  # no decoder_blobs list, no counter, no s_ts_ms
    assert A.decoder_failure_ms(rows) is None


def test_the_first_failing_sample_halts_at_its_own_instant():
    src = healthy()
    src[2] = line(T0 + 120, s_decoder_blobs_pinned=False)
    src[3] = line(T0 + 180, s_trade_event_v=None)
    rows = K.map_samples([src])
    assert A.decoder_failure_ms(rows) == (T0 + 120) * 1000
    v = A.classify({"run_utc": RUN, "programs": {}, "accounts": {}, "halt": {"flags": {"pins_changed": {"halt": False}}}}, {"programs": {}, "accounts": {}}, now_ms=NOW,
                   follower_samples=rows)
    assert v["verdict"] == "HALT" and v["halt_at_ms"] == (T0 + 120) * 1000 and "follower decoder pin failing" in v["reasons"][0]


def test_a_missing_flag_stays_missing_and_fails_closed_at_that_samples_time():
    src = healthy()
    d = json.loads(src[1])
    del d["s_trade_event_v"]
    src[1] = json.dumps(d)
    rows = K.map_samples([src])
    assert "trade_event_v" not in rows[1] and A.decoder_failure_ms(rows) == (T0 + 60) * 1000


def test_never_pass_decoder_ref_with_this_file_every_sample_would_fail():
    rows = K.map_samples([healthy()])
    assert A.decoder_failure_ms(rows, ["a", "b", "c"]) == T0 * 1000  # documented in the module: the sampler holds no blob list


@pytest.mark.parametrize("bad, key", [
    ('{"s_decoder_blobs_pinned": true}', "`t`"),  # missing t
    (json.dumps({"t": True, "s_trade_event_v": True}), "`t`"),
    (json.dumps({"t": 1794000000.5}), "`t`"),
    (json.dumps({"t": 1794000000000}), "`t`"),  # milliseconds, not seconds
    ('[1, 2]', "not a JSON object"),
    ('not json', "not JSON"),
    (json.dumps({"t": T0, "ts_ms": 1}), "classifier key 'ts_ms'"),
    (json.dumps({"t": T0, "decoder_blobs": ["a"]}), "classifier key 'decoder_blobs'"),
    (json.dumps({"t": T0, "password": "hunter2"}), "key 'password'"),
])
def test_the_whole_file_is_refused_with_a_line_number_and_a_key_name_never_a_value(bad, key):
    with pytest.raises(K.MapError) as e:
        K.map_samples([healthy(2) + [bad]], labels=["f.jsonl"])
    msg = str(e.value)
    assert key in msg and "f.jsonl line 3" in msg and "hunter2" not in msg


def test_other_sampler_namespace_keys_are_dropped_and_an_empty_window_is_refused():
    rows = K.map_samples([[line(T0, s_new_counter=7, c_other=1)]])
    assert rows == [{"ts_ms": T0 * 1000, "decoder_blobs_pinned": True, "trade_event_v": True}]
    with pytest.raises(K.MapError, match="no sample in the window"):
        K.map_samples([healthy()], since_s=T0 + 10_000)
    with pytest.raises(K.MapError, match="no sample"):
        K.map_samples([[]])
    assert len(K.map_samples([healthy()], since_s=T0 + 120)) == 3


def test_duplicate_t_across_merged_segments_keeps_the_worse_copy():
    a, b = [line(T0)], [line(T0, s_decoder_blobs_pinned=False)]
    rows = K.map_samples([a, b])
    assert len(rows) == 1 and rows[0]["decoder_blobs_pinned"] is False
    rows = K.map_samples([b, a])
    assert rows[0]["decoder_blobs_pinned"] is False
    assert K.map_samples([a, a])[0]["decoder_blobs_pinned"] is True
    d = json.loads(line(T0))
    del d["s_decoder_blobs_pinned"]
    assert "decoder_blobs_pinned" not in K.map_samples([a, [json.dumps(d)]])[0]  # one copy lacks it: absent, so failing


def test_cli_maps_files_refuses_an_existing_out_and_feeds_the_classifier_cli(tmp_path, capsys):
    f1, f2, out = tmp_path / "tip-health-1.jsonl", tmp_path / "tip-health-2.jsonl", tmp_path / "samples.jsonl"
    f1.write_text("\n".join(healthy(3)) + "\n")
    f2.write_text("\n".join(healthy(5)[2:]) + "\n\n")  # overlaps at t = T0 + 120
    argv = ["--in", str(f1), "--in", str(f2), "--out", str(out)]
    assert K.main(argv) == 0
    assert "STATUS=OK n_samples=5" in capsys.readouterr().err and len(out.read_text().splitlines()) == 5
    assert K.main(argv) == 2  # exists
    assert K.main(["--in", str(tmp_path / "missing.jsonl")]) == 2
    rec, pins, v = tmp_path / "rec.json", tmp_path / "pins.json", tmp_path / "v.json"
    rec.write_text(json.dumps({"run_utc": RUN, "programs": {}, "accounts": {}, "halt": {"flags": {"pins_changed": {"halt": False}}}}))
    pins.write_text(json.dumps({"programs": {}, "accounts": {}}))
    assert A.main(["classify", "--record", str(rec), "--pins", str(pins), "--follower-samples", str(out), "--out", str(v)], now_ms=NOW) == 0
    assert json.loads(v.read_text())["verdict"] == "NONE"  # not a HALT at instant 0
    capsys.readouterr()
    assert K.main(["--in", str(f1), "--since-s", str(T0 + 10_000)]) == 2
    assert "no sample in the window" in capsys.readouterr().err
