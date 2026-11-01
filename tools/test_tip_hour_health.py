"""Tests for tools/tip_hour_health.py: synthetic counts rows, samples and receiver logs only. No tip row, no tape, no real writer output."""
import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tip_hour_health as T  # noqa: E402

HR = T.HOUR_MS
H0 = T.parse_hour("2026-11-03T00")
PINS = {"adapter_sha256": "a" * 64, "reader_sha256": "b" * 64, "writer_sha256": "c" * 64}
THR_BYTES = (Path(__file__).resolve().parent / "data" / "tip_hour_health_thresholds.json").read_bytes()
THR = T.load_thresholds(THR_BYTES)
N_HOURS = 8  # counts rows H0 .. H0+7
NOW = H0 + 20 * HR  # everything closed unless a test says otherwise
MID = H0 + 3 * HR  # a middle hour: its neighbours are known, so every chain is measurable


def crow(h, run="r1", started=H0 - 10 * HR, pins=None, spans=(), gap_rows=None, first=1000, last=HR - 1000, s_start=1.0, s_end=1.0, hb=(), spans_dropped=0, progs=("pumpswap",),
         mixed=0, extra=None):
    r = {"schema": T.SCHEMA_COUNTS, "hour": T.hour_key(h), "bytes": 100, "lines": 10, "sha256": "ab" * 32, "closed_ms": h + HR + 130_000, "run_id": run, "runs": [run], "started_ms": started,
         "pins": pins or PINS, "rows": {"pumpswap|trade": 600, "pumpswap|gap": 1}, "gap_rows": gap_rows or {}, "gap_missed_slots": 0, "gap_spans": [list(s) for s in spans],
         "gap_spans_dropped": spans_dropped, "data_t_first_ms": {p: h + first for p in progs}, "data_t_last_ms": {p: h + last for p in progs},
         "max_data_silence_s": {p: s_start for p in progs}, "max_data_silence_ending_s": {p: s_end for p in progs},
         "no_tx_index": {"rows_no_tx_index": 40, "pool_slots_no_tx_index": 20, "pool_slots_multi_sig": 15, "pool_slots_mixed": mixed},
         "dropped_rows": {}, "skips": 2, "status_bad_samples": 0, "late_rows": 0, "hb_gaps": [list(g) for g in hb]}
    r.update(extra or {})
    return r


def counts_blob(rows):
    return ("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)).encode()


def base_rows(**kw):
    return [crow(H0 + i * HR, **kw) for i in range(N_HOURS)]


def samples_blob(t0=H0 - HR, t1=H0 + (N_HOURS + 1) * HR, step=60, **over):
    rows = []
    for s in range(t0 // 1000, t1 // 1000, step):
        r = {"t": s, "s_ts_ms": s * 1000 - 1000, "s_decoder_blobs_pinned": True, "s_trade_event_v": True, "s_gaps": 3, "s_skipped": 5, "s_backlog_jumps": 0, "s_unresolved_dropped": 0,
             "s_block_lag_ms_p90": 900, "s_retries": 0, "s_breaker_trips": 0, "c_gaps": 3, "c_skipped": 5}
        r.update(over.get(str(s), {}))
        rows.append(r)
    return ("".join(json.dumps(r) + "\n" for r in rows)).encode()


def events_blob(hours=None, delay=150_000):
    hs = hours if hours is not None else [H0 + i * HR for i in range(N_HOURS)]
    return ("".join(json.dumps({"h": T.hour_key(h), "kind": "closed", "ms": h + HR + delay, "dec_sha": "d" * 64}) + "\n" for h in hs)).encode()


def arrival_blob(t0=H0 - HR, t1=H0 + (N_HOURS + 1) * HR, skip=(), stale_from=None):
    rows = []
    for m in range(t0, t1, 60_000):
        if m in skip:
            continue
        if stale_from is not None and m >= stale_from[0] and m < stale_from[1]:
            continue
        rows.append({"bytes": 9, "frames": 148, "minute": T.iso(m)[:-4] + "Z", "newest_pumpswap_arrival_ms": m + 59_900, "newest_pumpswap_t_recv_ms": m + 59_850, "newest_source_write_ms": m + 59_890})
    return ("".join(json.dumps(r) + "\n" for r in rows)).encode()


def run(counts=None, *, samples="default", events="default", arrival="default", silence=None, pool_cache=None, lag=None, thr=None, now=NOW, programs=("pumpswap",), pins="auto",
        h0=H0, h1=H0 + 6 * HR):
    rows, summ = T.evaluate(counts=counts_blob(base_rows()) if counts is None else counts, silence=silence, samples=[samples_blob() if samples == "default" else samples],
                            events=events_blob() if events == "default" else events, arrival=arrival_blob() if arrival == "default" else arrival, lag=lag, pool_cache=pool_cache,
                            thr=THR if thr is None else thr, h0=h0, h1=h1, now_ms=now, programs=programs, pins_spec=pins)
    return {r["hour"]: r for r in rows}, summ


def mid(rows):
    return rows[T.iso(MID)]


# ------------------------------------------------------------------------------------------------------------------------------ healthy, tabling, thresholds file
def test_healthy_middle_hours_are_not_excluded():
    rows, summ = run()
    for k in (1, 2, 3, 4, 5):
        r = rows[T.iso(H0 + k * HR)]
        assert r["excluded"] is False, (k, r["failed_checks"], r["unmeasurable"])
        assert r["failed_checks"] == [] and r["unmeasurable"] == [] and r["labels_failing"] == []
    assert summ["pins_source"] == "auto"


def test_first_known_hour_is_unmeasurable_for_silence():
    rows, _ = run()
    r = rows[T.iso(H0)]
    assert r["max_data_silence_s"] is None and "max_data_silence_s" in r["unmeasurable"] and r["excluded"] is True


def test_hour_is_tabled_only_when_it_and_the_next_are_closed():
    now = H0 + 3 * HR + HR + 299_000  # hour 3 ended 299 s ago... hour 3 is not closed, so hour 2 (needs hour 3) is not tabled
    rows, summ = run(now=now)
    assert T.iso(H0 + 2 * HR) not in rows and T.iso(H0 + HR) in rows
    assert T.hour_key(H0 + 2 * HR) in summ["not_tabled_hours"] and T.hour_key(H0 + 3 * HR) in summ["not_tabled_hours"]
    rows2, _ = run(now=now + 1_000)  # hour 3 closes (end + 300 s)
    assert T.iso(H0 + 2 * HR) in rows2


def test_thresholds_file_is_the_canonical_qp_bytes():
    qp = ('{"max_data_silence_s":{"label":"price-path","value":60},"max_input_age_s":{"label":"decision-input","value":30},"max_loss_span_s":{"label":"price-path","value":120},'
          '"max_sample_gap_s":{"label":"price-path","value":180},"min_samples_per_hour":{"label":"price-path","value":50},"status_bad_samples":{"label":"price-path","value":0},'
          '"writer_hour_complete":{"label":"price-path","value":1}}').encode()
    assert THR_BYTES == qp
    assert hashlib.sha256(THR_BYTES).hexdigest() == "5ee9a3361078d4f342ef7d608226c086ce9eed59f6d44838ffb5094e8469996d"
    assert json.dumps(json.loads(THR_BYTES), sort_keys=True, separators=(",", ":")).encode() == THR_BYTES
    assert {k: v["label"] for k, v in THR.items()}["max_input_age_s"] == "decision-input"
    assert all(v["label"] == "price-path" for k, v in THR.items() if k != "max_input_age_s")


def test_threshold_loader_refuses_unknown_key_label_and_negative():
    for bad in ({"nope": {"value": 1, "label": "price-path"}}, {"max_loss_span_s": {"value": 1, "label": "x"}}, {"max_loss_span_s": {"value": -1, "label": "price-path"}}, [1]):
        with pytest.raises(ValueError):
            T.load_thresholds(json.dumps(bad).encode())


# ------------------------------------------------------------------------------------------------------------------------------ counts row, gap spans, writer_hour_complete
def test_missing_counts_row_fails_and_is_structural_with_both_labels():
    rs = [r for r in base_rows() if r["hour"] != T.hour_key(MID)]
    rows, _ = run(counts_blob(rs))
    r = mid(rows)
    assert r["excluded"] and "counts_row_missing" in r["failed_checks"] and "counts_row_missing" in r["structural_failures"]
    assert r["labels_failing"] == ["decision-input", "price-path"]
    assert rows[T.iso(MID - HR)]["excluded"] and "next_counts_row_missing" in rows[T.iso(MID - HR)]["unmeasurable"]


def test_loss_span_over_120s_excludes_and_119s_does_not():
    f = MID + 600_000
    for dur, excluded in ((121_000, True), (120_000, False), (119_000, False)):
        rs = base_rows()
        rs[3] = crow(MID, spans=[("tip_gap_backlog_jump", "all", 10, 20, f, f + dur)], gap_rows={"tip_gap_backlog_jump|all": 1})
        rows, _ = run(counts_blob(rs))
        r = mid(rows)
        assert r["max_loss_span_s"] == dur / 1000.0
        assert ("max_loss_span_s" in r["failed_checks"]) is excluded and r["excluded"] is excluded
        assert r["data_loss_rows_overlapping"] == 1 and r["backlog_jump_rows"] == 1


def test_span_of_another_program_is_not_counted_but_prog_all_is():
    f = MID + 1000
    rs = base_rows()
    rs[3] = crow(MID, spans=[("tip_gap_bad_row_fields", "pumpfun", 1, 2, f, f + 500_000)])
    assert mid(run(counts_blob(rs))[0])["excluded"] is False
    rs[3] = crow(MID, spans=[("tip_gap_unresolved_dropped", "pumpswap", 1, 2, f, f + 500_000)])
    assert mid(run(counts_blob(rs))[0])["excluded"] is True
    rs[3] = crow(MID, spans=[("tip_gap_writer_hole", "all", 1, 2, f, f + 500_000)])
    r = mid(run(counts_blob(rs))[0])
    assert r["excluded"] and r["writer_hole_rows"] == 0  # gap_rows not given in this row: the by-kind report comes from the log, the span from gap_spans
    # a pumpfun-trading book does count the pumpfun span
    rs[3] = crow(MID, spans=[("tip_gap_bad_row_fields", "pumpfun", 1, 2, f, f + 500_000)], progs=("pumpswap", "pumpfun"))
    rs = [crow(r_h, progs=("pumpswap", "pumpfun"), **({"spans": [("tip_gap_bad_row_fields", "pumpfun", 1, 2, f, f + 500_000)]} if r_h == MID else {})) for r_h in [H0 + i * HR for i in range(N_HOURS)]]
    assert mid(run(counts_blob(rs), programs=("pumpswap", "pumpfun"))[0])["excluded"] is True


def test_span_filed_in_the_next_hour_counts_in_the_hour_it_overlaps():
    # a gap that began in MID but whose row was written in MID+1 (the writer files a row by its write hour)
    f = MID + HR - 30_000
    rs = base_rows()
    rs[4] = crow(MID + HR, spans=[("tip_gap_backlog_jump", "all", 1, 2, f, f + 200_000)])
    rows, _ = run(counts_blob(rs))
    assert mid(rows)["max_loss_span_s"] == 200.0 and mid(rows)["excluded"] is True
    assert rows[T.iso(MID + HR)]["max_loss_span_s"] == 200.0  # unclipped, and it overlaps the next hour too


def test_a_span_that_ends_exactly_at_the_hour_start_does_not_overlap():
    f = MID - 130_000
    rs = base_rows()
    rs[3] = crow(MID, spans=[("tip_gap_backlog_jump", "all", 1, 2, f, MID)])
    assert mid(run(counts_blob(rs))[0])["data_loss_rows_overlapping"] == 0


def test_truncated_or_unreadable_spans_are_unmeasurable():
    rs = base_rows()
    rs[3] = crow(MID, spans_dropped=3)
    r = mid(run(counts_blob(rs))[0])
    assert r["excluded"] and "gap_spans_truncated" in r["unmeasurable"] and r["max_loss_span_s"] is None
    rs[3] = crow(MID, spans=[("tip_gap_backlog_jump", "all", 1, 2, None, None)])
    r = mid(run(counts_blob(rs))[0])
    assert r["excluded"] and "counts_row_unreadable" in r["unmeasurable"]


def test_writer_restart_pins_heartbeat_and_verification_each_fail_writer_hour_complete():
    def fails(rs=None, **kw):
        r = mid(run(counts_blob(rs or base_rows()), **kw)[0])
        return r["excluded"], r["writer_hour_reasons"], r["failed_checks"]

    # restart: two runs wrote rows in the hour
    rs = base_rows()
    rs[3] = crow(MID, extra={"runs": ["r1", "r2"]})
    ex, why, fc = fails(rs)
    assert ex and "writer_restart_in_hour" in why and "writer_hour_complete" in fc
    # restart: a later run started inside the hour (run_id changes)
    rs = [crow(H0 + i * HR, run="r1" if i < 3 else "r2", started=H0 - 10 * HR if i < 3 else MID + 100_000) for i in range(N_HOURS)]
    ex, why, _ = fails(rs)
    assert ex and "writer_restart_in_hour" in why
    # pins differ in the hour
    rs = base_rows()
    rs[3] = crow(MID, pins={**PINS, "writer_sha256": "d" * 64})
    ex, why, _ = fails(rs)
    assert ex and "pins_differ" in why
    # heartbeat gap: 61 s fails, 60 s does not
    for gap, bad in ((61_000, True), (60_000, False)):
        rs = base_rows()
        rs[3] = crow(MID, hb=[(MID + 100_000, MID + 100_000 + gap)])
        ex, why, _ = fails(rs)
        assert ex is bad and ("heartbeat_gap_over_60s" in why) is bad
    # verification: 15 min passes, 15 min + 1 s fails, missing fails
    for delay, bad in ((900_000, False), (901_000, True)):
        ex, why, _ = fails(events=events_blob(delay=delay))
        assert ex is bad and ("verified_after_15min" in why) is bad
    ex, why, _ = fails(events=events_blob([H0 + i * HR for i in range(N_HOURS) if H0 + i * HR != MID]))
    assert ex and "hour_not_verified" in why


def test_registered_pins_file_and_missing_pins_fail_closed(tmp_path):
    pf = tmp_path / "pins.json"
    pf.write_text(json.dumps(PINS))
    rows, summ = run(pins=str(pf))
    assert mid(rows)["excluded"] is False and summ["pins_source"] == "file"
    pf.write_text(json.dumps({**PINS, "adapter_sha256": "e" * 64}))
    assert "pins_differ" in mid(run(pins=str(pf))[0])["writer_hour_reasons"]
    rows, _ = run(pins=None)
    assert mid(rows)["excluded"] and "pins_not_given" in mid(rows)["writer_hour_reasons"]
    pf.write_text(json.dumps({"adapter_sha256": "x"}))
    with pytest.raises(ValueError):
        T.load_pins(str(pf), {})


# ------------------------------------------------------------------------------------------------------------------------------ data silence
def test_intra_hour_silence_over_60s_excludes():
    for sil, bad in ((61.0, True), (60.0, False)):
        rs = base_rows()
        rs[3] = crow(MID, s_start=sil)
        r = mid(run(counts_blob(rs))[0])
        assert r["data_silence_by_prog_s"]["pumpswap"] == sil and r["excluded"] is bad


def test_silence_across_the_hour_boundary_counts_in_both_hours_unclipped():
    # last data of MID-1h at -30 s before its end; first data of MID+? : a 90 s boundary silence between hour 2 and hour 3
    rs = [crow(H0 + i * HR) for i in range(N_HOURS)]
    rs[2] = crow(H0 + 2 * HR, last=HR - 30_000)
    rs[3] = crow(MID, first=60_000)
    rows, _ = run(counts_blob(rs))
    assert rows[T.iso(H0 + 2 * HR)]["data_silence_by_prog_s"]["pumpswap"] == 90.0
    assert mid(rows)["data_silence_by_prog_s"]["pumpswap"] == 90.0
    assert rows[T.iso(H0 + 2 * HR)]["excluded"] and mid(rows)["excluded"]


def test_a_silent_hour_is_one_long_silence_over_every_hour_it_covers():
    rs = [crow(H0 + i * HR) for i in range(N_HOURS)]
    rs[3] = crow(MID, extra={"data_t_first_ms": {}, "data_t_last_ms": {}})
    rows, _ = run(counts_blob(rs))
    assert mid(rows)["data_silence_by_prog_s"]["pumpswap"] >= 3600.0 and mid(rows)["excluded"]


def test_silence_jsonl_line_for_an_already_closed_hour_is_used():
    sil = json.dumps({"hour": T.hour_key(MID), "prog": "pumpswap", "from_ms": MID + 1000, "to_ms": MID + 200_000, "silence_s": 199.0, "run_id": "r1"}).encode() + b"\n"
    r = mid(run(silence=sil)[0])
    assert r["data_silence_by_prog_s"]["pumpswap"] == 199.0 and r["excluded"]


def test_missing_counts_row_breaks_the_silence_chain_for_the_next_hour():
    rs = [r for r in base_rows() if r["hour"] != T.hour_key(MID)]
    rows, _ = run(counts_blob(rs))
    nxt = rows[T.iso(MID + HR)]
    assert nxt["max_data_silence_s"] is None and "max_data_silence_s" in nxt["unmeasurable"]


# ------------------------------------------------------------------------------------------------------------------------------ samples
def test_sample_checks():
    rows, _ = run(samples=samples_blob(step=120))  # 30 per hour: under 50
    assert "min_samples_per_hour" in mid(rows)["failed_checks"]
    rows, _ = run(samples=samples_blob(step=60))
    assert mid(rows)["samples_n"] == 60
    # a hole of 190 s in the middle hour
    full = samples_blob().decode().splitlines()
    cut = [ln for ln in full if not (MID // 1000 + 600 < json.loads(ln)["t"] < MID // 1000 + 790)]
    rows, _ = run(samples=("\n".join(cut) + "\n").encode())
    r = mid(rows)
    assert r["sample_gap_max_s"] > 180 and "max_sample_gap_s" in r["failed_checks"]
    cut = [ln for ln in full if not (MID // 1000 + 600 < json.loads(ln)["t"] < MID // 1000 + 770)]  # 180 s exactly between kept samples
    rows, _ = run(samples=("\n".join(cut) + "\n").encode())
    assert "max_sample_gap_s" not in mid(rows)["failed_checks"]


def test_status_bad_samples_pinned_event_v_and_age():
    s = MID // 1000 + 300
    for key, bad in ((f"{s}", {"s_decoder_blobs_pinned": False}), (f"{s}", {"s_trade_event_v": None}), (f"{s}", {"s_ts_ms": s * 1000 - 31_000}), (f"{s}", {"s_ts_ms": None})):
        rows, _ = run(samples=samples_blob(**{key: bad}))
        assert mid(rows)["status_bad_samples"] == 1 and "status_bad_samples" in mid(rows)["failed_checks"] and mid(rows)["excluded"]
    rows, _ = run(samples=samples_blob(**{str(s): {"s_ts_ms": s * 1000 - 30_000}}))  # exactly 30 s old is allowed
    assert mid(rows)["status_bad_samples"] == 0


def test_no_sample_file_makes_the_sample_checks_unmeasurable():
    rows, _ = run(samples=None)
    r = mid(rows)
    assert r["excluded"] and {"min_samples_per_hour", "max_sample_gap_s", "status_bad_samples"} <= set(r["unmeasurable"])


def test_sample_files_merge_and_deduplicate_by_t():
    a, b = samples_blob(H0 - HR, H0 + 4 * HR), samples_blob(H0 + 3 * HR, H0 + 9 * HR)
    rows, _ = T.evaluate(counts=counts_blob(base_rows()), silence=None, samples=[a, b], events=events_blob(), arrival=arrival_blob(), lag=None, pool_cache=None, thr=THR, h0=H0, h1=H0 + 6 * HR,
                         now_ms=NOW, programs=("pumpswap",), pins_spec="auto")
    r = {x["hour"]: x for x in rows}[T.iso(MID)]
    assert r["samples_n"] == 60 and r["excluded"] is False


# ------------------------------------------------------------------------------------------------------------------------------ input age (transport B)
def test_input_age_30s_boundary_at_second_2_of_each_minute():
    rows, _ = run()
    assert 2.0 < mid(rows)["max_input_age_s"] < 2.5
    # arrival rows stop for 40 minutes of the middle hour: the newest known t_recv falls behind
    rows, _ = run(arrival=arrival_blob(stale_from=(MID + 600_000, MID + 3_000_000)))
    r = mid(rows)
    assert r["max_input_age_s"] > 30 and "max_input_age_s" in r["failed_checks"] and r["labels_failing"] == ["decision-input"]
    # a single missing minute: the age at :02 of the next minute is ~62 s, which also fails (the check is per minute)
    rows, _ = run(arrival=arrival_blob(skip={MID + 600_000}))
    assert 60 < mid(rows)["max_input_age_s"] < 65


def test_input_age_uses_only_rows_that_had_arrived_by_the_instant():
    arr = T.Arrival(json.dumps({"newest_pumpswap_arrival_ms": 10_000, "newest_pumpswap_t_recv_ms": 9_900}).encode() + b"\n")
    assert arr.newest_at(9_999) is None and arr.newest_at(10_000) == 9_900


def test_input_age_unmeasurable_without_an_arrival_log_or_for_another_program():
    rows, _ = run(arrival=None)
    assert "max_input_age_s" in mid(rows)["unmeasurable"] and mid(rows)["excluded"]
    rs = [crow(H0 + i * HR, progs=("pumpswap", "pumpfun")) for i in range(N_HOURS)]
    rows, _ = run(counts_blob(rs), programs=("pumpswap", "pumpfun"))
    r = mid(rows)
    assert r["input_age_by_prog_s"]["pumpfun"] is None and "max_input_age_s" in r["unmeasurable"]


# ------------------------------------------------------------------------------------------------------------------------------ report-only fields and tripwires
def test_shared_slot_counts_are_report_only_and_never_exclude():
    rows, _ = run()
    r = mid(rows)
    assert r["no_tx_index"]["pool_slots_multi_sig"] == 15 and r["excluded"] is False and r["tripwire_mixed_group"] is False
    assert r["rows_total"] == 601 and r["rows_per_min"] == round(601 / 60, 3)


def test_mixed_group_sets_the_tripwire_but_does_not_exclude():
    rs = base_rows()
    rs[3] = crow(MID, mixed=2)
    r = mid(run(counts_blob(rs))[0])
    assert r["tripwire_mixed_group"] is True and r["excluded"] is False


def test_pool_cache_180k_tripwire_and_nrestarts_delta_are_report_only():
    pc = "\n".join([
        json.dumps({"utc": "2026-11-03T00:30:00Z", "pool_cache": 100_000, "backlog_jumps": 0, "decoder_blobs_pinned": True}), "NRestarts=0",
        json.dumps({"utc": "2026-11-04T00:30:00Z", "pool_cache": 180_000, "backlog_jumps": 0, "decoder_blobs_pinned": True}), "NRestarts=1",
        "ALERT pool_cache>=180000 -> QP re-rule HV4-T exposure"]).encode()
    recs = T.load_pool_cache(pc)
    assert [(r["pool_cache"], r["nrestarts"]) for r in recs] == [(100_000, 0), (180_000, 1)]
    rows, _ = run(pool_cache=pc, h0=H0, h1=H0 + 6 * HR)
    assert mid(rows)["pool_cache"] == 100_000 and mid(rows)["tripwire_pool_cache_180k"] is False and mid(rows)["excluded"] is False
    late = T.pool_cache_fields(recs, T.parse_hour("2026-11-04T05"))
    assert late["tripwire_pool_cache_180k"] is True and late["nrestarts_delta"] is None  # the day's record sits in hour 00
    day = T.pool_cache_fields(recs, T.parse_hour("2026-11-04T00"))
    assert day["tripwire_pool_cache_180k"] is True and day["nrestarts_delta"] == 1 and day["pool_cache_record"] == "2026-11-04T00:30:00Z"
    assert T.POOL_CACHE_ALERT == 180_000 and T.POOL_CACHE_CAP == 200_000


def test_follower_counter_reset_is_flagged_not_excluding():
    s = MID // 1000 + 1800
    rows, _ = run(samples=samples_blob(**{str(s): {"s_gaps": 0, "c_gaps": 0}}))  # a counter going down = follower restart
    r = mid(rows)
    assert r["follower_counter_resets"] >= 1 and r["tripwire_follower_restart"] is True


def test_transport_lag_is_reported():
    lag = "".join(json.dumps({"minute": T.iso(MID + k * 60_000)[:-4] + "Z", "lag_new_p50_ms": 10.0, "lag_new_p90_ms": 17.0 + k}) + "\n" for k in range(60)).encode()
    rows, _ = run(lag=lag)
    assert mid(rows)["transport_lag"] == {"minutes": 60, "lag_new_p50_ms_median": 10.0, "lag_new_p90_ms_max": 76.0}


def test_verified_json_and_events_jsonl_are_both_read():
    vj = json.dumps({"hours": {T.hour_key(H0): H0 + HR + 5000, "bad": 1}}).encode()
    assert T.load_verified(vj) == {H0: H0 + HR + 5000}
    ev = events_blob([H0]) + json.dumps({"h": T.hour_key(H0), "kind": "closed", "ms": H0 + HR + 999_999}).encode() + b"\n" + json.dumps({"h": T.hour_key(H0), "kind": "other", "ms": 1}).encode() + b"\n"
    assert T.load_verified(ev) == {H0: H0 + HR + 150_000}  # the first verification counts


# ------------------------------------------------------------------------------------------------------------------------------ output, sha, determinism
def test_render_sha_covers_hour_lines_and_is_deterministic():
    rows, summ = T.evaluate(counts=counts_blob(base_rows()), silence=None, samples=[samples_blob()], events=events_blob(), arrival=arrival_blob(), lag=None, pool_cache=None, thr=THR,
                            h0=H0, h1=H0 + 6 * HR, now_ms=NOW, programs=("pumpswap",), pins_spec="auto")
    a = T.render(rows, hashlib.sha256(THR_BYTES).hexdigest(), summ, ("pumpswap",), {})
    b = T.render(rows, hashlib.sha256(THR_BYTES).hexdigest(), summ, ("pumpswap",), {})
    assert a == b
    final = json.loads(a[-1])
    assert final["type"] == "sha256" and final["hours"] == len(rows) == len(a) - 1
    assert final["sha256"] == hashlib.sha256(("".join(ln + "\n" for ln in a[:-1])).encode()).hexdigest()
    assert final["thresholds_sha256"] == "5ee9a3361078d4f342ef7d608226c086ce9eed59f6d44838ffb5094e8469996d"


def test_output_holds_no_address_signature_or_row_field():
    rows, _ = run()
    blob = json.dumps(rows)
    for forbidden in ('"mint"', '"pool"', '"signature"', '"trader"', '"price"', '"reserve"', '"amount"'):
        assert forbidden not in blob


# ------------------------------------------------------------------------------------------------------------------------------ the pre-clock bar
def bar_rows(clock, gap_rows_by_hour=None, pins=None, skip=()):
    out = []
    for i in range(48):
        h = clock - (30 - i) * HR
        if h in skip:
            continue
        out.append(crow(h, pins=pins, gap_rows=(gap_rows_by_hour or {}).get(h)))
    return counts_blob(out)


CLOCK = T.parse_instant("2026-11-07T00:00:00Z")


@pytest.mark.parametrize("t_max_s,count,verdict,p", [
    (0.6 * 3600, 1, "PASS", 0.02469),   # QP-RULING-1101 section 8: one row passes at 0.6 h
    (1.0 * 3600, 1, "FAIL", 0.0408),    # HV7 (T_max 1 h): one row fails
    (1.0 * 3600, 0, "PASS", 0.0),
    (176_460.0, 0, "PASS", 0.0),        # r4-b-T: zero rows required
    (176_460.0, 1, "FAIL", None),
    (0.6076 * 3600, 1, "PASS", None),   # just under the break-even
    (0.6077 * 3600, 1, "FAIL", None),   # just over
])
def test_bar_formula_and_the_zero_row_rule(t_max_s, count, verdict, p):
    gr = {CLOCK - 5 * HR: {"tip_gap_backlog_jump|all": count}} if count else {}
    res = T.bar(bar_rows(CLOCK, gr), CLOCK, t_max_s, ("pumpswap",), "auto")
    assert res["verdict"] == verdict
    v = res["programs"]["pumpswap"]
    assert v["rows"] == count and math.isclose(v["p_loss"], 1 - math.exp(-count / 86400.0 * t_max_s))
    if p is not None:
        assert round(v["p_loss"], 4 if p else 0) == round(p, 4 if p else 0)
    assert res["zero_rows_required"] is (t_max_s / 3600.0 > T.T_ZERO_H)
    assert round(T.T_ZERO_H, 4) == 0.6076


def test_bar_counts_prog_all_for_both_programs_and_a_program_row_for_its_own_only():
    gr = {CLOCK - 3 * HR: {"tip_gap_backlog_jump|all": 1, "tip_gap_unresolved_dropped|pumpswap": 2, "tip_gap_bad_row_fields|pumpfun": 4}}
    res = T.bar(bar_rows(CLOCK, gr), CLOCK, 1800.0, ("pumpswap", "pumpfun"), "auto")
    assert res["programs"]["pumpswap"]["rows"] == 3 and res["programs"]["pumpfun"]["rows"] == 5
    assert res["programs"]["pumpswap"]["by_kind"] == {"tip_gap_backlog_jump|all": 1, "tip_gap_unresolved_dropped|pumpswap": 2}


def test_bar_window_is_the_24_closed_hours_before_the_clock():
    inside = {CLOCK - 24 * HR: {"tip_gap_backlog_jump|all": 1}}
    outside = {CLOCK - 25 * HR: {"tip_gap_backlog_jump|all": 7}, CLOCK: {"tip_gap_backlog_jump|all": 7}}
    res = T.bar(bar_rows(CLOCK, {**inside, **outside}), CLOCK, 600.0, ("pumpswap",), "auto")
    assert res["programs"]["pumpswap"]["rows"] == 1 and res["window_from"] == "2026-11-06T00:00:00Z" and res["window_to"] == "2026-11-07T00:00:00Z"


def test_bar_incomplete_when_an_hour_is_missing_or_pins_not_given_and_fail_on_pin_change():
    res = T.bar(bar_rows(CLOCK, skip={CLOCK - 7 * HR}), CLOCK, 3600.0, ("pumpswap",), "auto")
    assert res["verdict"] == "INCOMPLETE" and res["hours_missing"] == ["2026-11-06T17"]
    assert T.bar(bar_rows(CLOCK), CLOCK, 3600.0, ("pumpswap",), None)["verdict"] == "INCOMPLETE"
    rows = [crow(CLOCK - (30 - i) * HR, pins=PINS if i < 20 else {**PINS, "writer_sha256": "f" * 64}) for i in range(48)]
    res = T.bar(counts_blob(rows), CLOCK, 3600.0, ("pumpswap",), "auto")
    assert res["verdict"] == "FAIL" and res["hours_pins_differ_or_unchecked"]


def test_bar_hv7_window_example_and_clock_must_be_a_whole_hour():
    res = T.bar(bar_rows(CLOCK), CLOCK, 3600.0, ("pumpswap",), "auto")
    assert res["verdict"] == "PASS" and res["clock"] == "2026-11-07T00:00:00Z"
    with pytest.raises(ValueError):
        T.bar(bar_rows(CLOCK), CLOCK + 60_000, 3600.0, ("pumpswap",), "auto")


# ------------------------------------------------------------------------------------------------------------------------------ the guard and the CLI
@pytest.mark.parametrize("name", ["tape-2026-11-03T05.jsonl", "tape-2026-11-03T05.jsonl.zst", "trades-2026-11-03T05.jsonl", "creates-2026-11-03T05.jsonl", "migrations-2026-11-03T05.jsonl",
                                  "skipped-slots-2026-11-03T05.jsonl", "gaps.jsonl", "state.json", "ledger.jsonl", "bots.json", "runner-status.json", "decisions.jsonl"])
def test_guard_refuses_tip_tape_state_and_ledger_files(tmp_path, name):
    p = tmp_path / name
    p.write_text("x")
    with pytest.raises(T.Refused):
        T.guard(p)
    with pytest.raises(T.Refused):
        T.read_input(p)


@pytest.mark.parametrize("path", ["/var/lib/mal/sealed/fast-trades-tip/gaps.jsonl", "/data/mal/tip-tape-archive/x.jsonl", "/data2/mal/arena-tip-feed/hv7/.writer/state.json",
                                  "/data2/mal/arena-tip-feed/hv7/tape-2026-11-03T05.jsonl.zst", "/data/mal/meta-capture/a.json", "/data/mal/blocks/forward-1016/x.json"])
def test_guard_refuses_sealed_paths(path):
    with pytest.raises(T.Refused):
        T.guard(path)


def test_the_tool_never_lists_or_walks_a_directory():
    src = (Path(__file__).resolve().parent / "tip_hour_health.py").read_text()
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    body = code.split('"""', 2)[2]  # without the module docstring
    for banned in ("iterdir", "listdir", "scandir", "os.walk", "glob(", "rglob", "subprocess", "socket", "urllib", "requests"):
        assert banned not in body, banned


def test_read_inputs_allows_the_named_receiver_and_writer_files(tmp_path):
    for name in ("counts.jsonl", "silence.jsonl", "arrival.jsonl", "events.jsonl", "lag.jsonl", "writer_status.json", "status.json", "verified.json", "tip-health-1102.jsonl", "tip-pool-cache-daily.jsonl"):
        p = tmp_path / name
        p.write_text("{}\n")
        assert T.read_input(p) == b"{}\n"
    assert T.read_input(tmp_path / "absent.jsonl") is None


def write_inputs(tmp_path, counts=None, **kw):
    d = tmp_path / "in"
    d.mkdir(exist_ok=True)
    (d / "counts.jsonl").write_bytes(counts if counts is not None else counts_blob(base_rows()))
    (d / "samples.jsonl").write_bytes(samples_blob())
    (d / "events.jsonl").write_bytes(events_blob())
    (d / "arrival.jsonl").write_bytes(arrival_blob())
    (d / "thr.json").write_bytes(THR_BYTES)
    return d


def cli_args(d, sub, *more):
    return [sub, "--counts", str(d / "counts.jsonl"), "--samples", str(d / "samples.jsonl"), "--events", str(d / "events.jsonl"), "--arrival", str(d / "arrival.jsonl"),
            "--thresholds", str(d / "thr.json"), "--pins", "auto", "--now", T.iso(NOW), *more]


def test_cli_table_prints_hour_lines_then_the_sha_line(tmp_path, capsys):
    d = write_inputs(tmp_path)
    assert T.main(cli_args(d, "table", "--from", "2026-11-03T01", "--to", "2026-11-03T03")) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 4 and json.loads(lines[-1])["type"] == "sha256" and json.loads(lines[-1])["hours"] == 3
    assert json.loads(lines[-1])["inputs"]["counts"]["sha256"] == hashlib.sha256((d / "counts.jsonl").read_bytes()).hexdigest()


def test_cli_refuses_a_tape_file_with_exit_2(tmp_path, capsys):
    d = write_inputs(tmp_path)
    bad = tmp_path / "trades-2026-11-03T05.jsonl"
    bad.write_text("{}\n")
    args = cli_args(d, "table", "--from", "2026-11-03T01", "--to", "2026-11-03T03")
    args[args.index("--counts") + 1] = str(bad)
    assert T.main(args) == 2
    assert "refused" in capsys.readouterr().err


def test_cli_bar_exit_codes(tmp_path, capsys):
    f = tmp_path / "counts.jsonl"
    f.write_bytes(bar_rows(CLOCK))
    assert T.main(["bar", "--counts", str(f), "--clock", "2026-11-07T00:00:00Z", "--t-max-s", "3600", "--pins", "auto"]) == 0
    f.write_bytes(bar_rows(CLOCK, {CLOCK - HR: {"tip_gap_backlog_jump|all": 1}}))
    assert T.main(["bar", "--counts", str(f), "--clock", "2026-11-07T00:00:00Z", "--t-max-s", "3600", "--pins", "auto"]) == 1
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["programs"]["pumpswap"]["p_loss"] > 0.04
    f.write_bytes(bar_rows(CLOCK, skip={CLOCK - HR}))
    assert T.main(["bar", "--counts", str(f), "--clock", "2026-11-07T00:00:00Z", "--t-max-s", "3600", "--pins", "auto"]) == 3


# ------------------------------------------------------------------------------------------------------------------------------ the daily table writer
def test_daily_writes_atomically_with_sha_sidecar_and_is_idempotent(tmp_path, capsys):
    d = write_inputs(tmp_path)
    out = tmp_path / "out"
    args = cli_args(d, "daily", "--date", "2026-11-03", "--out-dir", str(out))
    assert T.main(args) == 0
    info = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    f = out / "tip-hour-table-2026-11-03.jsonl"
    assert info["status"] == "written" and f.is_file() and not list(out.glob(".*tmp*"))
    assert (out / (f.name + ".sha256")).read_text() == f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n" and info["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    first = f.read_bytes()
    assert T.main(args) == 0 and json.loads(capsys.readouterr().out.strip().splitlines()[-1])["status"] == "unchanged" and f.read_bytes() == first


def test_daily_never_redecides_a_tabled_hour(tmp_path, capsys):
    d = write_inputs(tmp_path)
    out = tmp_path / "out"
    args = cli_args(d, "daily", "--date", "2026-11-03", "--out-dir", str(out))
    assert T.main(args) == 0
    capsys.readouterr()
    # the same hours later get a gap span filed in a later row: the new table would differ for a tabled hour -> refused, file untouched
    rs = base_rows()
    rs[3] = crow(MID, spans=[("tip_gap_backlog_jump", "all", 1, 2, MID + 1000, MID + 300_000)])
    (d / "counts.jsonl").write_bytes(counts_blob(rs))
    before = (out / "tip-hour-table-2026-11-03.jsonl").read_bytes()
    assert T.main(args) == 4
    info = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert info["status"].startswith("REFUSED") and T.iso(MID) in info["changed_hours"]
    assert (out / "tip-hour-table-2026-11-03.jsonl").read_bytes() == before


def test_daily_extends_a_partial_table_when_more_hours_close(tmp_path, capsys):
    d = write_inputs(tmp_path)
    out = tmp_path / "out"
    early = cli_args(d, "daily", "--date", "2026-11-03", "--out-dir", str(out))
    early[early.index("--now") + 1] = T.iso(H0 + 5 * HR + 400_000)  # hours 0..3 tabled
    assert T.main(early) == 0
    n1 = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["hours_tabled"]
    assert T.main(cli_args(d, "daily", "--date", "2026-11-03", "--out-dir", str(out))) == 0
    info = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert info["status"] == "written" and info["hours_tabled"] > n1
