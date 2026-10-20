"""Tests for revision 4 of tools/hv4_shakedown_report.py (QP design ruling on #768, comment 6281516193, Note N4 with cap 2,000): check 5 at 2,000 per rolling hour and 48,000 per UTC day,
check 12 (holders_stale refusals = 0), and the holders report lines (holders_age_s, blocks older than 5 and 12 min, null-holders cards by cause and pool state, warm holders reads per hour).
Revision 3 keeps 1,500 / 40,000 and its record. The obs files come from the revision 3 test module's real-ObsLog fixtures; the `holders_null_served` rows and the chain_hour warm keys are written
by hand until the #768 builder's commits land (their field names are the ones in the ruling: pool, scan_ms, hot, promoted_at_scan, warm, newest_holders_age_ms; chain_hour `deferred_warm_hold`).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from tools import hv4_shakedown_report as R
from tools.test_hv4_shakedown_report import HOUR, MIN, START, by_id, sub
from tools.test_hv4_shakedown_report_obs import A, B, DAY, fixture, put, scan_t, sel


UNPINNED = "PLACEHOLDER-NOT-PINNED-test"  # what the tool carries until the manager gives the sha256 of SHAKEDOWN-CHECKS-4.md


@pytest.fixture(autouse=True)
def _checks4_pinned(monkeypatch):
    """A revision 4 PASS needs the CHECKS-4 pin. Tests that are not about the pin run with a valid sha256 in place (when the tool still holds the placeholder); the pin tests set their own."""
    if not R.checks4_pinned():
        monkeypatch.setattr(R, "CHECKS4_SHA256", "ab" * 32)


def rev(fx: dict, n: int, *more: str) -> dict:
    argv = ["--dir", str(fx["base"]), "--tape-dir", str(fx["tape"]), "--checks-rev", str(n), "--samples", str(fx["samples"]), "--job-log", str(fx["job"]), "--job-log", str(fx["job2"]),
            "--read-hours", "168"]
    return R.build_report(R.parse_args(argv + list(more)))


def rev4(fx: dict, *more: str) -> dict:
    return rev(fx, 4, *more)


def block_hour(total: int, at: int = 300):
    """Calls per minute: the hour that starts at minute index `at` holds exactly `total` calls (spread evenly); 8 a minute everywhere else, so no other 60-minute run reaches `total`."""
    base, rem = divmod(total, 60)
    return lambda m: (base + (1 if m - at < rem else 0)) if at <= m < at + 60 else 8


# ------------------------------------------------------------------------------------------------------------------------------ the revision, the pins, rev 3 unchanged
def test_revision_4_is_the_default_and_a_healthy_pinned_run_passes_with_both_pins_in_the_record(tmp_path):
    fx = fixture(tmp_path, start_pids=(A, B))
    rec = rev4(fx)
    c = by_id(rec)
    assert rec["checks_rev"] == 4 and rec["verdict"] == "PASS" and rec["verdict_reason"] is None and {k: v["status"] for k, v in c.items() if v["status"] != R.PASS and not v["informational"]} == {}
    assert c["12"]["status"] == R.PASS and c["12"]["numbers"]["holders_stale_refusals_total"] == 0 and c["4"]["status"] == R.PASS
    assert rec["checks_pin"]["sha256"] == "55fbba0559327c1e18728596506de11fdf0408ae9bedcee49a21966d9314a3af" and rec["checks_notes_pin"]["sha256"] == R.CHECKS3_NOTES1_SHA256
    assert rec["checks4_pin"] == {"name": "SHAKEDOWN-CHECKS-4.md", "sha256": R.CHECKS4_SHA256, "pinned": True} and R.checks4_pinned()
    assert rec["check_5_limits"] == {"rolling_hour": 2000, "utc_day": 48000} and "2,000" in c["5"]["name"] and "48,000" in c["5"]["name"]
    text = R.markdown(rec)
    assert "checks revision 4" in text and "PLACEHOLDER" not in text and "2,000 per rolling hour and 48,000 per UTC day" in text and "| 12 holders_stale" in text
    assert R.parse_args(["--dir", "x"]).checks_rev == 4


def test_a_revision_4_pass_needs_the_checks_4_pin_otherwise_the_verdict_is_not_passed(tmp_path, monkeypatch):
    fx = fixture(tmp_path)
    for bad in (UNPINNED, "AB" * 32, "ab" * 31, "g" * 64, ""):  # the placeholder, upper case, short, not hex, empty: all "missing or mismatched"
        monkeypatch.setattr(R, "CHECKS4_SHA256", bad)
        rec = rev4(fx)
        assert rec["complete"] and rec["verdict"] == "NOT_PASSED" and rec["verdict_reason"] == "CHECKS-4 pin missing or mismatched" and rec["checks4_pin"]["pinned"] is False
        assert all(v["status"] == R.PASS for v in by_id(rec).values() if not v["informational"])  # every check passed: only the pin stands between the run and a PASS
    monkeypatch.setattr(R, "CHECKS4_SHA256", UNPINNED)
    assert "PLACEHOLDER: NOT PINNED YET" in R.markdown(rev4(fx)) and "verdict reason: CHECKS-4 pin missing or mismatched" in R.markdown(rev4(fx))
    monkeypatch.setattr(R, "CHECKS4_SHA256", "ab" * 32)
    assert rev4(fx)["verdict"] == "PASS"
    # the pin never turns a worse verdict into a better one or an unfinished window into a finished one
    monkeypatch.setattr(R, "CHECKS4_SHA256", UNPINNED)
    bad_run = rev4(fixture(sub(tmp_path, "fail"), refused=lambda i: [(f"P{i}a", ["holders_stale"])] if i == 40 else []))
    assert bad_run["verdict"] == "FAIL" and bad_run["verdict_reason"].startswith("FAILED: 12")
    assert rev4(fixture(sub(tmp_path, "wip"), hours=13))["verdict"] == "INCOMPLETE"
    # revision 3 never reads the revision 4 pin
    assert rev(fixture(sub(tmp_path, "r3"), start_pids=(A, B)), 3)["verdict"] == "PASS"


def test_the_revision_4_pin_constant_is_the_only_thing_to_set_when_the_manager_gives_the_sha(tmp_path, monkeypatch):
    fx = fixture(tmp_path)
    monkeypatch.setattr(R, "CHECKS4_SHA256", "ab" * 32)
    rec = rev4(fx)
    assert rec["checks4_pin"] == {"name": "SHAKEDOWN-CHECKS-4.md", "sha256": "ab" * 32, "pinned": True} and "PLACEHOLDER" not in R.markdown(rec)
    for bad in ("AB" * 32, "ab" * 31, "g" * 64, "PLACEHOLDER"):
        monkeypatch.setattr(R, "CHECKS4_SHA256", bad)
        assert not R.checks4_pinned()


def test_revision_3_output_is_unchanged_no_check_12_no_holders_report_and_the_old_limits(tmp_path):
    fx = fixture(tmp_path, start_pids=(A, B))
    rec = rev(fx, 3)
    ids = {c["id"] for c in rec["checks"] + rec["registration_conditions"]}
    assert rec["checks_rev"] == 3 and "12" not in ids and "holders_report" not in rec and "checks4_pin" not in rec and "check_5_limits" not in rec
    assert set(rec) == {"schema", "generated", "dir", "verdict", "complete", "window", "blocks_registration", "unmeasured_checks", "checks", "registration_conditions", "per_persona_counts",
                        "startup_and_feed_gap_windows", "statuses", "inputs", "torn_tail_files", "unreadable_decision_files", "skipped_dirs", "sampler", "job_logs", "tool", "scope", "checks_rev",
                        "checks_pin", "checks_notes_pin", "not_measured", "rule_m", "processes", "obs", "also_reported_from_obs", "verdict_reason", "tape_first_rows"}
    c5 = by_id(rec)["5"]
    assert "1,500" in c5["name"] and "40,000" in c5["name"] and "rolling_hours_over_1500" in c5["numbers"]["failing"] and "utc_days_over_40000" in c5["numbers"]["failing"]
    assert rec["tape_first_rows"].startswith("not used in revision 3") and "holders_age_s" not in json.dumps(rec) and "PLACEHOLDER" not in R.markdown(rec)


# ------------------------------------------------------------------------------------------------------------------------------ check 5 at 2,000 and 48,000
@pytest.mark.parametrize("total,r3,r4", [(1999, R.FAIL, R.PASS), (2000, R.FAIL, R.PASS), (2001, R.FAIL, R.FAIL), (1500, R.PASS, R.PASS), (1501, R.FAIL, R.PASS)])
def test_check_5_rolling_hour_limit_is_2000_in_revision_4_and_1500_in_revision_3(tmp_path, total, r3, r4):
    c4 = by_id(rev4(fixture(sub(tmp_path, "r4"), calls=block_hour(total))))["5"]
    assert c4["status"] == r4 and c4["numbers"]["rolling_hour_calls"]["max"] == total and c4["numbers"]["rolling_hour_calls"]["limit"] == 2000
    assert (c4["numbers"]["failing"]["rolling_hours_over_2000"] > 0) == (r4 == R.FAIL)
    c3 = by_id(rev(fixture(sub(tmp_path, "r3"), calls=block_hour(total)), 3))["5"]
    assert c3["status"] == r3 and c3["numbers"]["rolling_hour_calls"]["max"] == total and c3["numbers"]["rolling_hour_calls"]["limit"] == 1500


def test_check_5_day_limit_is_48000_in_revision_4_and_40000_in_revision_3(tmp_path):
    def day(n):
        return lambda rows, fx: next(r for r in sel(rows, "chain_hour", A) if r["hour_end_ms"] == START + 2 * HOUR).update(counter_day_calls=n)

    ok = by_id(rev4(fixture(sub(tmp_path, "ok"), mutate=day(48_000))))["5"]
    assert ok["status"] == R.PASS and ok["numbers"]["calls_by_utc_day"]["2026-10-19"]["limit"] == 48_000
    bad = by_id(rev4(fixture(sub(tmp_path, "bad"), mutate=day(48_001))))["5"]
    assert bad["status"] == R.FAIL and bad["numbers"]["failing"]["utc_days_over_48000"] == ["2026-10-19"]
    old = by_id(rev(fixture(sub(tmp_path, "old"), mutate=day(48_000)), 3))["5"]
    assert old["status"] == R.FAIL and old["numbers"]["failing"]["utc_days_over_40000"] == ["2026-10-19"] and old["numbers"]["calls_by_utc_day"]["2026-10-19"]["limit"] == 40_000
    assert by_id(rev(fixture(sub(tmp_path, "old_ok"), mutate=day(40_000)), 3))["5"]["status"] == R.PASS


def test_the_revision_specific_numbers_in_the_text_and_the_sampler_note(tmp_path):
    doc = " ".join(R.__doc__.split())
    assert "revision 3 (24 x 1,500 = 36,000 is below its 40,000)" in doc and "revision 4 (24 x 2,000 = 48,000 is its day limit" in doc
    fx = fixture(tmp_path)
    (fx["root"] / "chain-prefetch-spend.json").write_text(json.dumps({"schema": "hv4_prefetch.spend.v1", "spent": [[START + 2 * HOUR, 2000], [START + 2 * HOUR + MIN, 1500]]}))
    note = lambda rec, n: [x for x in by_id(rec)["5S"]["notes"] if "exactly" in x]  # noqa: E731
    r4 = note(rev4(fx), 4)
    assert len(r4) == 1 and "exactly 2,000" in r4[0] and "1,500" not in r4[0]
    r3 = note(rev(fx, 3), 3)
    assert len(r3) == 1 and "exactly 1,500" in r3[0]
    r2 = [x for x in by_id(rev(fx, 2))["5"]["notes"] if "exactly" in x]
    assert len(r2) == 1 and "exactly 1,500" in r2[0]


def test_the_informational_sampler_cross_check_uses_the_revision_limits_and_the_verdict_never_reads_it(tmp_path):
    rec = rev4(fixture(tmp_path, calls=block_hour(1999)))
    cross = by_id(rec)["5S"]
    assert cross["informational"] and rec["verdict"] != "ERROR" and by_id(rec)["5"]["status"] == R.PASS


# ------------------------------------------------------------------------------------------------------------------------------ check 12
def test_check_12_passes_without_holders_stale_refusals_and_fails_on_any_per_persona(tmp_path):
    tag = lambda i: [(f"P{i}a", ["holders_stale"])] if i == 40 else []  # noqa: E731 - a tagged CON pool
    c = by_id(rev4(fixture(sub(tmp_path, "con"), refused=tag)))["12"]
    assert c["status"] == R.FAIL and c["numbers"]["holders_stale_refusals_total"] == 1 and c["numbers"]["per_persona"] == {"CON": 1, "BIG": 0} and "constant mismatch" in c["notes"][0]
    big = lambda i: [(f"P{i}b", ["holders_stale"])] if i in (3, 4) else []  # noqa: E731
    cb = by_id(rev4(fixture(sub(tmp_path, "big"), refused=big)))["12"]
    assert cb["status"] == R.FAIL and cb["numbers"]["per_persona"] == {"CON": 0, "BIG": 2}
    board = lambda i: [(f"B{i}", ["holders_stale", "chain_stale"])] if i == 7 else []  # noqa: E731 - an untagged board pool counts for both
    cd = by_id(rev4(fixture(sub(tmp_path, "board"), refused=board)))["12"]
    assert cd["status"] == R.FAIL and cd["numbers"]["per_persona"] == {"CON": 1, "BIG": 1} and cd["numbers"]["holders_stale_refusals_total"] == 1
    other = lambda i: [(f"P{i}a", ["chain_stale"])] if i < 3 else []  # noqa: E731 - other refusals do not count
    assert by_id(rev4(fixture(sub(tmp_path, "other"), refused=other)))["12"]["status"] == R.PASS


def test_check_12_gates_on_the_total_of_every_card_refused_row_with_the_reason_in_any_arm_and_reports_the_persona_split(tmp_path):
    t = START + 3 * HOUR + 17 * MIN
    rec = rev4(fixture(tmp_path, extra=lambda fx: fx["L"].add("CON-L", t, kind="card_refused", pool="OTHERARMPOOL-z1", reasons=["holders_stale"])))
    c = by_id(rec)["12"]
    assert c["status"] == R.FAIL and c["numbers"]["holders_stale_refusals_total"] == 1 and c["numbers"]["per_persona"] == {"CON": 0, "BIG": 0} and c["numbers"]["refusals_with_no_scan_row_at_their_ms"] == 0
    assert "in total over every card_refused row" in c["name"] and "OTHERARMPOOL" not in json.dumps(rec) + R.markdown(rec)


def test_check_12_counts_a_refused_row_that_has_no_scan_row_and_fails_the_verdict(tmp_path):
    t = START + 12 * HOUR + 4 * MIN + 30_000
    rec = rev4(fixture(tmp_path, extra=lambda fx: fx["L"].add("SCAN", t, kind="card_refused", pool="ORPHANPOOL-q1", reasons=["holders_stale"])))
    c = by_id(rec)["12"]
    assert c["status"] == R.FAIL and c["numbers"]["holders_stale_refusals_total"] == 1 and c["numbers"]["refusals_with_no_scan_row_at_their_ms"] == 1 and c["numbers"]["per_persona"] == {"CON": 0, "BIG": 0}
    assert rec["verdict"] == "FAIL" and "12" in rec["verdict_reason"] and "ORPHANPOOL" not in json.dumps(rec) + R.markdown(rec)


def test_check_12_is_unmeasured_without_decision_rows_and_absent_in_revision_3(tmp_path):
    fx = fixture(tmp_path)
    import shutil

    shutil.rmtree(fx["root"] / "decisions")
    assert by_id(rev4(fx))["12"]["status"] == R.UNM
    assert "12" not in by_id(rev(fixture(sub(tmp_path, "r3")), 3))


def test_rule_m_is_unchanged_under_revision_4(tmp_path):
    rec = rev4(fixture(tmp_path, mutate=lambda rows, fx: sel(rows, "runner_hour", B)[3].update(obs_write_errors=1)))
    c = by_id(rec)
    assert rec["verdict"] == "FAIL" and rec["rule_m"]["fired"] and all(c[k]["status"] == R.UNM for k in ("1", "3", "5", "8")) and c["12"]["status"] == R.PASS and c["4"]["status"] == R.PASS


# ------------------------------------------------------------------------------------------------------------------------------ the holders report lines
def set_ages(fx: dict, age) -> None:
    """Write risk.holders_age_s into the card sink: age(card) -> seconds or None."""
    for f in sorted((fx["root"] / "cards").glob("*.jsonl")):
        out = []
        for ln in f.read_text().splitlines():
            row = json.loads(ln)
            card = row["card"]
            card["risk"]["holders_age_s"] = age(card)
            out.append(json.dumps(row))
        f.write_text("".join(x + "\n" for x in out))


def nearest_rank(vals, q):
    v = sorted(vals)
    return v[max(0, math.ceil(q * len(v)) - 1)] if v else None


def scan_index(card: dict) -> int:
    return round((card["as_of_ms"] - 2000 - START) / (5 * MIN))


def test_the_holders_report_gives_the_age_distribution_and_the_cards_older_than_5_and_12_minutes_per_persona(tmp_path):
    null = lambda i: i % 5 == 0  # noqa: E731
    age = lambda c: None if scan_index(c) % 5 == 0 else float(60 * (scan_index(c) % 20 + 1))  # noqa: E731 - 60 s to 1,200 s
    fx = fixture(tmp_path, holders_null=null)
    set_ages(fx, age)
    rec = rev4(fx)
    hr = rec["holders_report"]
    for p in R.PERSONAS:
        mine = [i for i in range(300) if i % 5 != 0]
        ages = [60 * (i % 20 + 1) for i in mine for _ in range(2)]  # the persona's tagged card and the board card of each scan
        h = hr[p]
        assert h["cards_with_a_holders_block"] == len(ages) == 480 and h["holders_age_s"] == {"n": 480, "p50": nearest_rank(ages, 0.5), "p90": nearest_rank(ages, 0.9), "max": 1200}
        assert h["cards_with_a_block_older_than_5_min"] == sum(1 for a in ages if a > 300) and h["cards_with_a_block_older_than_12_min"] == sum(1 for a in ages if a > 720) > 0
        assert h["null_holders_cards"] == 120 and h["cards_with_a_block_and_no_holders_age_s"] == 0
        assert h["null_holders_cards_by_cause"] == {"no_holders_null_served_row": 120}  # no holders_null_served rows were written
    assert by_id(rec)["4"]["status"] == R.FAIL  # check 4 itself is unchanged: 120 of 600 cards per persona are null
    assert by_id(rec)["4"]["numbers"]["CON"] == {"cards": 600, "holders_null": 120, "frac": 0.2}
    text = R.markdown(rec)
    assert "- holders CON: age_s p50" in text and "- holders BIG: age_s p50" in text


def served_rows(fx: dict, flags_for) -> list[dict]:
    rows = []
    for i in range(0, 300, 5):
        for suffix in "abc":
            promoted, hot, warm, age = flags_for(i)
            rows.append({"wall_ms": scan_t(i) + 20, "pid": A if scan_t(i) < fx["restart"] else B, "kind": "holders_null_served", "pool": f"P{i}{suffix}", "scan_ms": scan_t(i), "hot": hot,
                         "promoted_at_scan": promoted, "warm": warm, "newest_holders_age_ms": age})
    return rows


def test_null_holders_cards_are_counted_by_cause_and_by_the_pools_state_at_the_scan(tmp_path):
    def flags(i):  # (promoted_at_scan, hot, warm, newest_holders_age_ms)
        return {0: (True, True, False, None), 5: (False, True, False, 1_000_000), 10: (False, False, True, None), 15: (False, False, False, 2_000_000)}[i % 20]

    fx = fixture(tmp_path, holders_null=lambda i: i % 5 == 0, mutate=lambda rows, fx: rows.extend(served_rows(fx, flags)))
    set_ages(fx, lambda c: None if scan_index(c) % 5 == 0 else 100.0)
    rec = rev4(fx)
    for p in R.PERSONAS:
        h = rec["holders_report"][p]
        assert h["null_holders_cards"] == 120
        assert h["null_holders_cards_by_cause"] == {"no_holders_read_before_T": 60, "newest_read_older_than_15_min": 60}
        assert h["null_holders_cards_by_pool_state_at_the_scan"] == {"promoted_to_hot_at_this_scan": 30, "hot_before": 30, "warm": 30, "other": 30}
    w = rec["holders_report"]["holders_null_served_rows_in_W"]
    # 180 rows in the file; the 6 of scans 290 and 295 are after the end of W (24 h after the first start row), so 174 are in W
    assert w["n"] == 174 and w["by_cause"] == {"no_holders_read_before_T": 87, "newest_read_older_than_15_min": 87} and w["rows_in_file"] == 180
    assert w["by_pool_state_at_the_scan"] == {"promoted_to_hot_at_this_scan": 45, "hot_before": 45, "warm": 42, "other": 42}


def test_a_null_card_without_a_served_row_a_missing_flag_and_a_young_null_read_are_named_not_failed(tmp_path):
    def flags(i):
        return (None, True, False, None) if i == 0 else ((False, False, False, 600_000) if i == 5 else (False, False, True, None))

    def drop_some(rows, fx):
        rows.extend(r for r in served_rows(fx, flags) if r["scan_ms"] != scan_t(10))  # no served rows at scan 10

    fx = fixture(tmp_path, holders_null=lambda i: i % 5 == 0 and i < 15, mutate=drop_some)
    set_ages(fx, lambda c: None if (scan_index(c) % 5 == 0 and scan_index(c) < 15) else 100.0)
    h = rev4(fx)["holders_report"]["CON"]
    assert h["null_holders_cards"] == 6
    assert h["null_holders_cards_by_cause"] == {"no_holders_read_before_T": 2, "newest_read_within_15_min_but_not_served": 2, "no_holders_null_served_row": 2}
    assert h["null_holders_cards_by_pool_state_at_the_scan"] == {"unknown": 2, "other": 2, "no_holders_null_served_row": 2}


def test_warm_holders_reads_per_hour_are_reported_and_a_missing_key_is_not_reported_never_a_failure(tmp_path, monkeypatch):
    def warm(rows, fx):
        for k, r in enumerate(sel(rows, "chain_hour", B)):  # the keys of the builder's chain_hour row (hv4_prefetch.py on claude/hv4-v2-fresh-obs-v2): the first process's rows lack them
            r.update(deferred_warm_hold=k, warm_holders_reads_hour=10 + k, warm_holders_calls_hour=12 + k)

    fx = fixture(tmp_path, mutate=warm)
    rec = rev4(fx)
    hourly = rec["holders_report"]["warm_holders_per_hour"]
    b = [x for x in hourly if x["pid"] == B]
    assert len(hourly) == 24 and [x["warm_holders_pools_deferred"] for x in b] == list(range(12))
    assert [x["warm_holders_reads_booked"] for x in b] == [10 + k for k in range(12)] and [x["warm_holders_calls_booked"] for x in b] == [12 + k for k in range(12)]
    a = [x for x in hourly if x["pid"] == A]
    assert all(set(x.values()) - {A, x["hour_end"]} == {"not reported"} for x in a) and rec["verdict"] == "PASS"  # absent keys are 'not reported'
    assert any("warm_holders_reads_hour" in n for n in rec["holders_report"]["notes"])
    monkeypatch.setattr(R, "CHAIN_HOUR_WARM_BOOKED_KEY", "renamed_key")  # a key the rows do not have
    assert {x["warm_holders_reads_booked"] for x in rev4(fx)["holders_report"]["warm_holders_per_hour"]} == {"not reported"}


def test_no_pool_id_reaches_the_revision_4_output(tmp_path):
    def flags(i):
        return (False, True, False, None)

    fx = fixture(tmp_path, holders_null=lambda i: i % 5 == 0, refused=lambda i: [(f"P{i}a", ["holders_stale"])] if i == 7 else [], mutate=lambda rows, fx: rows.extend(served_rows(fx, flags)))
    set_ages(fx, lambda c: 200.0)
    rec = rev4(fx)
    blob = json.dumps(rec) + R.markdown(rec)
    assert not re.search(r"\bP\d+[abc]\b", blob) and "ORPHAN" not in blob
    R.forbid_scan(rec)
    assert rec["holders_report"]["holders_null_served_rows_in_W"]["n"] == 174 and by_id(rec)["12"]["status"] == R.FAIL


def test_the_report_lines_do_not_gate_the_verdict(tmp_path):
    fx = fixture(tmp_path)
    set_ages(fx, lambda c: 1100.0)  # every block is older than 12 min: reported, no limit
    rec = rev4(fx)
    assert rec["holders_report"]["CON"]["cards_with_a_block_older_than_12_min"] == 600 and rec["verdict"] == "PASS"


# ------------------------------------------------------------------------------------------------------------------------------ the real writer at d0d4ca8 (PR #771)
def test_the_real_prefetcher_writes_the_holders_null_served_fields_and_the_warm_chain_hour_keys_the_report_reads(tmp_path):
    """Field-name contract against the real ChainPrefetcher (the N4 writer): a pool promoted from warm to hot at the scan, whose newest holders read is 19 min old, is served a null block;
    the report reads the row (cause, pool state) and the chain_hour row keys (warm reads, warm calls, distinct pools deferred) and joins them to a null-holders card."""
    pytest.importorskip("numpy")
    from tools import hv4_obs as O
    from tools import hv4_prefetch as P
    from tools import hv4_runner as HR
    from tools.test_hv4_prefetch_v2 import SLOT, T0, Clock, CountingChain, pools

    clock = Clock(T0)
    obs = O.ObsLog(tmp_path, wall_ms=lambda: clock.ms)
    pf = P.ChainPrefetcher(CountingChain(clock), HR.HV4ChainSource.compose, clock=clock, policy=P.POLICY_V2, hourly_budget=100)
    pf.set_obs(obs)
    pf.run_once(T0)
    a = pools(2)
    clock.ms = T0 + MIN
    pf.watch(a, [], clock.ms, vols={a[0][0]: 3.0})
    pf.run_once(clock.ms)
    clock.ms = T0 + 20 * MIN
    obs.scan_ms = clock.ms
    pf.watch(a, [a[1][0]], clock.ms)  # a1 promoted at this scan from warm; its holders read at +1 min are 19 min old: null
    assert "holders_as_of_ms" not in pf.part_at(a[1][0], a[1][1], clock.ms, SLOT + 10)
    scan_ms = clock.ms
    clock.ms = T0 + HOUR + MIN
    pf.run_once(clock.ms)
    Ob = R.load_obs(tmp_path / O.OBS_FILE, [])
    assert not Ob.no_pid and not Ob.malformed and len(Ob.holders_null_served) == 1
    s = Ob.holders_null_served[0]
    assert s["pool"] == a[1][0] and s["scan_ms"] == scan_ms and s["promoted_at_scan"] is True and s["warm"] is True and s["hot"] is False and s["age_key_present"] is True
    assert 18 * MIN < s["newest_holders_age_ms"] < 20 * MIN and R.holders_cause(s) == "newest_read_older_than_15_min" and R.holders_state(s) == "promoted_to_hot_at_this_scan"
    ch = Ob.chain_hour[0]
    assert ch["deferred_warm_hold"] is not None and ch["warm_holders_booked"] >= 1 and ch["warm_holders_calls_booked"] >= 1  # warm_holders_reads_hour / warm_holders_calls_hour of the real row
    card = {"persona": "CON", "holders_null": True, "pool": a[1][0], "as_of_ms": scan_ms, "holders_age_s": None, "type": None}
    rep = R.holders_report([card], Ob, R.Logs(), (T0, T0 + 2 * HOUR))
    con = rep["CON"]
    assert con["null_holders_cards_by_cause"] == {"newest_read_older_than_15_min": 1} and con["null_holders_cards_by_pool_state_at_the_scan"] == {"promoted_to_hot_at_this_scan": 1}
    assert rep["holders_null_served_rows_in_W"]["n"] == 1 and rep["warm_holders_per_hour"][0]["warm_holders_reads_booked"] >= 1 and rep["warm_holders_per_hour"][0]["warm_holders_pools_deferred"] != "not reported"


def test_the_pinned_checks_4_sha_is_the_constant_and_matches_the_file_on_the_managers_host(tmp_path):
    assert R.CHECKS4_SHA256 == "a9b019ca9c8db38e4172a04ceb338c6edc03ef17333bc0ae3d462079bc7dd583" and R.checks4_pinned()
    f = Path("/data/mal/hunt-1008/push-1016/hunt20/haiku-v4") / R.CHECKS4_NAME
    if f.is_file():  # on the manager's host the constant is the file's own hash
        import hashlib

        assert hashlib.sha256(f.read_bytes()).hexdigest() == R.CHECKS4_SHA256
    rec = rev4(fixture(tmp_path, start_pids=(A, B)))
    assert rec["checks4_pin"] == {"name": "SHAKEDOWN-CHECKS-4.md", "sha256": R.CHECKS4_SHA256, "pinned": True} and rec["verdict"] == "PASS"
