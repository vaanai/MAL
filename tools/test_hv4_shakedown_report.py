"""Tests for tools/hv4_shakedown_report.py on synthetic fixtures. Nothing here is a real card, trade or price; the P&L / price canaries below must never reach the output."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from tools import hv4_shakedown_report as R

MIN = 60_000
HOUR = 3_600_000
START = R.parse_iso("2026-10-19T22:00:00Z")
CANARY = "CANARY-8675309"  # a value that sits in P&L / price / bots fields of the fixtures; it must never be printed
GENESIS = "0" * 64


class Log:
    def __init__(self, root: Path) -> None:
        self.root, self.head = root, {}

    def add(self, arm: str, ms: int, **body) -> None:
        prev, seq = self.head.get(arm, (GENESIS, -1))
        row = {"seq": seq + 1, "arm": arm, "ms": ms, "prev": prev, **body}
        row["sha"] = hashlib.sha256((prev + R.canon(row)).encode()).hexdigest()
        p = self.root / "decisions" / arm / f"{R.iso(ms)[:10]}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "ab") as f:
            f.write(R.canon(row).encode() + b"\n")
        self.head[arm] = (row["sha"], row["seq"])


def build(tmp: Path, *, hours: int = 25, restart_h: int = 12, scan_wall=lambda i: 900, exit_delay=lambda m: 200, refused=lambda i: [], holders_null=lambda i: False,
          trim_ms=lambda i: 150.0, calls_per_min=lambda m: 8, rss=lambda h: 500 + 20 * h, extra=None, restart_sha="a" * 40, v_ratio=0.3, n_bots=9, start2=True,
          skip_ticks=lambda m: False, tape_rows=None, cg_max=10240.0, oom=0, restart_binding_dirty=False, restart_min: int = 0) -> dict:
    base = tmp / "shake"
    root = base / "root"
    root.mkdir(parents=True)
    (base / "ledger").mkdir()
    (base / "ledger" / "BST-hidden.jsonl").write_text(CANARY)
    (base / "bots.json").write_text("".join(json.dumps({"bot_id": f"hv4-CON-{i}", "botnote": CANARY}) + "\n" for i in range(n_bots)))
    L = Log(root)
    end = START + hours * HOUR
    restart = START + restart_h * HOUR + restart_min * MIN
    L.add("SCAN", START, kind="start", binding={"dirty": False, "git_sha": "a" * 40}, frozen_sha="a" * 40, lane_h=False)
    cards, n_scan = [], 0
    for m in range(0, hours * 60):
        t = START + m * MIN
        if start2 and t == restart:  # rows are appended in time order, as the runner does
            L.add("SCAN", restart, kind="start", binding={"dirty": restart_binding_dirty, "git_sha": restart_sha}, frozen_sha=restart_sha, lane_h=False)
        if t // MIN % 5 == 0:
            T, i = t + 2000, n_scan
            n_scan += 1
            for r in refused(i):
                L.add("SCAN", T, kind="card_refused", pool=r[0], reasons=r[1])
            L.add("SCAN", T, kind="scan", as_of_slot=1, n_filter_pass=100, n_tags={"CON": 1, "BIG": 1}, n_board=1, n_cards=3, card_shas=["x", "y", "z"],
                  tagged=[[f"P{i}a", "CON"], [f"P{i}b", "BIG"]], eligible=[{"pool": f"P{i}a", "sha": "s", "token": {}}])
            L.add("SCAN", T, kind="scan_timing", scan_wall_ms=scan_wall(i))
            for j, (pool, pr) in enumerate([(f"P{i}a", "CON"), (f"P{i}b", "BIG"), (f"P{i}c", None)]):
                cards.append({"sha": f"{i}-{j}", "card": {"id": pool, "as_of_ms": T, "setup": {"type": "PULLBACK", "persona": pr, "z": 1.0} if pr else None,
                                                          "risk": {"holders_as_of_ms": None if holders_null(i) else T - 1000}, "f": {"entry_price": CANARY}}})
            L.add("CON-L", T, kind="llm_call", call="entry", ids=[])
            L.add("CON-X", T, kind="entry", order_id="o", pos={"entry_price": CANARY}, card={"est_pnl_sol": CANARY})
            L.add("CON-X", T + 1, kind="sell", pid="o", est_ret=CANARY, est_pnl_sol=CANARY)
        if not skip_ticks(m):
            L.add("SCAN", t + MIN - 1, kind="tick_timing", minute_ms=t, n_ticks=60, exit_check_delay_ms={"p50": 100, "p99": exit_delay(m), "max": exit_delay(m)}, n_scans=0, scan_wall_ms=None)
    cdir = root / "cards"
    cdir.mkdir()
    by_day: dict[str, list] = {}
    for c in cards:
        by_day.setdefault(R.iso(c["card"]["as_of_ms"])[:10], []).append(c)
    for day, cs in by_day.items():
        (cdir / f"{day}.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cs))
    lines = []
    for eid, (a, b) in ((("eng1", (START, restart)), ("eng2", (restart, end))) if start2 else (("eng1", (START, end)),)):
        k = 0
        for t in range(a, b, 5 * MIN):
            k += 1
            checked = 10 * k
            lines.append(R.canon({"schema": "hv4_engine_trim_v1", "tape_ms": t, "engine_id": eid, "trim_no": k, "dropped": 5, "trim_ms": trim_ms(len(lines)), "hist_trimmed_prints": 1000 * k,
                                  "rows_below_trim_floor": 0, "refused_history_trimmed": 0, "rows_dup": k, "filter_v_missing": 5 * k, "filter_v_band": k, "v_checked_pools": checked,
                                  "v_missing_pools": int(checked * v_ratio), "pools": 100 + k, "prints_held": 1000 + k, "hw_slot": 1}))
    (root / "engine-trim-counters.jsonl").write_text("\n".join(lines) + "\n")
    samples = []
    for t in range(START + 2 * MIN, end + 1, 10 * MIN):
        by: dict[str, int] = {}
        for q in range(START, t, MIN):
            by[R.iso(q)[:10]] = by.get(R.iso(q)[:10], 0) + calls_per_min((q - START) // MIN)
        started = START if t < restart else restart
        r = rss((t - started) / HOUR)
        samples.append({"schema": R.SAMPLE_SCHEMA, "t_ms": t, "chain_by_day": by, "spend": {"sum_last_hour": 400, "buckets": 60, "max_bucket": 20},
                        "procs": [{"pid": 111 if t < restart else 222, "rss_mb": r, "hwm_mb": r + 5, "cg_current_mb": r + 20, "cg_peak_mb": r + 30, "cg_max_mb": cg_max, "cg_oom_kill": oom}],
                        "feed": {"writing_paused": False, "lines_written": 1000 + (t - START) // 1000, "gaps": 3, "pauses": 0, "rows_dropped_total": 0}})
    sfile = tmp / "samples.jsonl"
    sfile.write_text("".join(json.dumps(s) + "\n" for s in samples))
    (root / "chain-credits.json").write_text(json.dumps({"by_day": samples[-1]["chain_by_day"], "credits_by_day": samples[-1]["chain_by_day"], "saved_ms": end, "schema": "hv4_chain.state.v1"}))
    (root / "chain-prefetch-spend.json").write_text(json.dumps({"schema": "hv4_prefetch.spend.v1", "spent": [[end - MIN, 8]]}))
    tape = tmp / "tape"
    tape.mkdir()
    h = START // HOUR * HOUR
    while h <= end:
        rows = [{"type": "hb", "t_recv_ms": h}] + [{"type": "trade", "pool": p, "t_recv_ms": t} for p, t in (tape_rows or {}).items() if h <= t < h + HOUR]
        (tape / ("tape-" + R.iso(h)[:13] + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in rows))
        h += HOUR
    (tape / "arena-feed-status.json").write_text(json.dumps({"writing_paused": False, "disk_reserve": {"pauses": 0, "rows_dropped_total": 0}, "gaps": 3, "updated_ms": end}))
    jl, jl2 = tmp / "job.log", tmp / "job2.log"
    jl.write_text('some line\n{"lane_h": false, "n_bots": 9}\nanother line\n')
    jl2.write_text("restart segment log\n")
    out = {"base": base, "root": root, "L": L, "samples": sfile, "tape": tape, "job": jl, "job2": jl2, "end": end, "restart": restart}
    if extra:
        extra(out)
    return out


def sub(tmp: Path, name: str) -> Path:
    d = tmp / name
    d.mkdir()
    return d


def late(fx: dict, n: int = 5) -> int:
    """An instant after every generated row, in the last day file, so an injected row keeps the chain in time order."""
    return fx["end"] + n


def run(fx: dict, *more: str, samples: bool = True, job: bool = True, read_hours: bool = True) -> dict:
    argv = ["--dir", str(fx["base"]), "--tape-dir", str(fx["tape"]), "--expect-end", R.iso(fx["end"]), "--checks-rev", "2"]  # these tests are the revision 2 evaluation; the default is revision 3
    if samples:
        argv += ["--samples", str(fx["samples"])]
    if job:
        argv += ["--job-log", str(fx["job"]), "--job-log", str(fx["job2"])]
    if read_hours:
        argv += ["--read-hours", "168"]
    return R.build_report(R.parse_args(argv + list(more)))


def by_id(rec: dict) -> dict:
    return {c["id"]: c for c in rec["checks"] + rec["registration_conditions"]}


def rewrite_samples(fx: dict, fn) -> None:
    rows = [json.loads(x) for x in fx["samples"].read_text().splitlines()]
    for r in rows:
        fn(r)
    fx["samples"].write_text("".join(json.dumps(r) + "\n" for r in rows))


def rewrite_trim(fx: dict, fn) -> None:
    p = fx["root"] / "engine-trim-counters.jsonl"
    rows = fn([json.loads(x) for x in p.read_text().splitlines()])
    p.write_text("".join(R.canon(r) + "\n" for r in rows))


# ------------------------------------------------------------------------------------------------------------------------------ healthy run and verdict
def test_healthy_run_passes_every_check_and_names_the_two_crossing_hours(tmp_path):
    rec = run(build(tmp_path, hours=34))  # 34 tape hours: more than the old 30-entry truncation
    c = by_id(rec)
    assert {k: v["status"] for k, v in c.items() if v["status"] != R.PASS} == {"QP-H1": R.UNM}, {k: (v["status"], v["notes"]) for k, v in c.items() if v["status"] != R.PASS}
    assert c["QP-H1"]["informational"] and rec["verdict"] == "PASS" and rec["complete"] and rec["blocks_registration"] == [] and rec["unmeasured_checks"] == []
    n5 = c["5"]["numbers"]
    assert 480 <= n5["rolling_hour_calls"]["max_upper_bound"] <= 1500
    assert len(n5["hour_crossing_00_00Z"]) == 2 and n5["hour_crossing_00_00Z"][0]["instant"] == "2026-10-20T00:00:00Z" and all(x["windows"] > 0 for x in n5["hour_crossing_00_00Z"])
    assert n5["hour_crossing_restart"][0]["instant"] == R.iso(START + 12 * HOUR)
    both = n5["both_processes_counted"]
    assert both["counter_decreases_in_samples"] == 0 and both["per_restart"][0]["reloaded_without_reset"] is True and both["per_restart"][0]["first_sample_after"] >= both["per_restart"][0]["last_sample_before"]
    assert n5["error_counters"]["spend_write_errors"].startswith("not measured") and n5["error_counters"]["job_logs_read"] == 2 and "partial days" in n5["utc_day_note"]
    pc = rec["per_persona_counts"]["per_persona"]["CON"]
    assert pc["attempts"] == 2 * 408 and pc["stub_llm_calls"] == 408 and pc["x_arm_entries"] == 408 and pc["x_arm_exits"] == 408
    m = c["QP-M1"]["numbers"]
    assert abs(m["slope_mb_per_h_conservative_max_of_segments"] - 20) < 0.5 and m["plan"]["memory_max_mb"] == 10240.0 and m["plan"]["memory_max_from"] == "sampled cgroup memory.max"
    assert m["plan"]["implied"].startswith("undetermined")  # no --host-cap-mb
    # no truncation of the per-hour series (QP item 6)
    tape_hours = {R.iso(r)[:13] for r in range(START, START + 34 * HOUR, 5 * MIN)}
    assert len(c["QP-T1"]["numbers"]["per_hour"]) == len(tape_hours) == 34
    assert len(c["QP-V1"]["numbers"]["share_of_newly_checked_pools_per_tape_hour"]) == 34
    assert len(m["hourly"]) >= 34


def test_a_partial_run_is_never_a_pass_and_unmeasured_checks_are_listed(tmp_path):
    fx = build(tmp_path)
    rec = run(fx, "--expect-end", "2026-10-30T00:00:00Z")
    assert rec["complete"] is False and rec["verdict"] == "INCOMPLETE"
    nojob = run(fx, job=False)
    assert by_id(nojob)["5"]["status"] == R.UNM and by_id(nojob)["10"]["status"] == R.UNM and nojob["verdict"] == "INCOMPLETE" and {"5", "10"} <= set(nojob["unmeasured_checks"])


# ------------------------------------------------------------------------------------------------------------------------------ checks 1 to 4
def test_check_1_as_of_refusal_fails(tmp_path):
    fx = build(tmp_path, extra=lambda fx: fx["L"].add("SCAN", late(fx), kind="as_of_refusal", pool="p", detail="LeakageError: chain"))
    c = by_id(run(fx))
    assert c["1"]["status"] == R.FAIL and c["1"]["numbers"]["as_of_refusal_rows"] == 1


def test_check_2_chain_stale_over_two_percent_fails_per_persona_and_a_board_pool_counts_for_both(tmp_path):
    ok = by_id(run(build(sub(tmp_path, "a"), refused=lambda i: [(f"P{i}a", ["chain_stale"])] if i < 7 else [])))
    assert ok["2"]["status"] == R.PASS and ok["2"]["numbers"]["CON"]["chain_stale"] == 7 and ok["2"]["numbers"]["BIG"]["chain_stale"] == 0
    bad = by_id(run(build(sub(tmp_path, "b"), refused=lambda i: [(f"P{i}a", ["chain_stale"])] if i < 13 else [])))
    assert bad["2"]["status"] == R.FAIL
    board = by_id(run(build(sub(tmp_path, "c"), refused=lambda i: [(f"B{i}", ["chain_stale"])] if i < 13 else [])))
    assert board["2"]["numbers"]["CON"]["chain_stale"] == 13 and board["2"]["numbers"]["BIG"]["chain_stale"] == 13 and board["2"]["status"] == R.FAIL
    assert board["2"]["numbers"]["CON"]["attempts"] == 2 * 300


def test_check_3_is_unmeasured_without_a_bound_pass_with_the_tape_bound_and_fail_on_a_visible_pool(tmp_path):
    new_pool = lambda i: [(f"N{i}", ["chain_unavailable"])] if i % 50 == 0 else []
    # no tape scan: a pool refused at its first appearance, 250 min into the process, has no bound under 10 min -> UNMEASURED (a first appearance proves nothing)
    unm = by_id(run(build(sub(tmp_path, "u"), refused=new_pool)))["3"]
    assert unm["status"] == R.UNM and unm["numbers"]["cards_whose_first_log_appearance_is_the_refusal"] == unm["numbers"]["chain_unavailable_cards"] == 6 and unm["numbers"]["cards_with_gap_bound_over_10min"] == 5
    # with the pools' first tape rows 3 min before their scans the bound is 3 min -> PASS
    rows = {f"N{i}": START + i * 5 * MIN + 2000 - 3 * MIN for i in range(0, 300, 50)}
    ok = by_id(run(build(sub(tmp_path, "t"), refused=new_pool, tape_rows=rows), "--tape-first-rows"))["3"]
    assert ok["status"] == R.PASS and ok["numbers"]["tape_first_row_scan"]["pools_found"] == 5 and ok["numbers"]["max_gap_bound_s"] <= 180
    # a tape row 30 min before the scan -> bound over 10 min -> UNMEASURED
    old = {k: v - 27 * MIN for k, v in rows.items()}
    assert by_id(run(build(sub(tmp_path, "o"), refused=new_pool, tape_rows=old), "--tape-first-rows"))["3"]["status"] == R.UNM
    # a pool the logs show at an earlier scan, 15 min before a chain_unavailable card -> FAIL
    fail = by_id(run(build(sub(tmp_path, "f"), refused=lambda i: [("Q", ["chain_unavailable"])] if i in (5, 8) else [])))["3"]
    assert fail["status"] == R.FAIL and fail["numbers"]["pool_visible_in_logs_more_than_10min_earlier"] == 1
    none = by_id(run(build(sub(tmp_path, "n"))))["3"]
    assert none["status"] == R.PASS and none["numbers"]["chain_unavailable_cards"] == 0


def test_check_4_null_holders_over_ten_percent_fails(tmp_path):
    c = by_id(run(build(tmp_path, holders_null=lambda i: i % 5 == 0)))["4"]  # 60 scans x (tagged + board) = 120 of 600 per persona
    assert c["status"] == R.FAIL and c["numbers"]["CON"]["holders_null"] == 120 and c["numbers"]["CON"]["cards"] == 600
    assert by_id(run(build(sub(tmp_path, "x"), holders_null=lambda i: i % 20 == 0)))["4"]["status"] == R.PASS


# ------------------------------------------------------------------------------------------------------------------------------ check 5
def test_check_5_rolling_hour_over_1500_fails_and_day_over_40000_fails(tmp_path):
    c = by_id(run(build(tmp_path, calls_per_min=lambda m: 40 if 100 <= m < 160 else 8)))["5"]  # 2400 calls in the hour 23:40Z-00:40Z
    assert c["status"] == R.FAIL and c["numbers"]["rolling_hour_calls"]["max_lower_bound"] > 1500 and c["numbers"]["hour_crossing_00_00Z"][0]["max_calls_lower_bound"] > 1500
    day = by_id(run(build(sub(tmp_path, "day"), calls_per_min=lambda m: 30)))["5"]
    assert day["status"] == R.FAIL and max(day["numbers"]["calls_by_utc_day"].values()) > 40_000


def test_check_5_needs_samples_and_a_job_log_per_start_and_names_spend_write_errors(tmp_path):
    fx = build(tmp_path)
    no_samples = by_id(run(fx, samples=False))["5"]
    assert no_samples["status"] == R.UNM and any("per-hour" in x for x in no_samples["notes"])
    one_log = by_id(R.build_report(R.parse_args(["--dir", str(fx["base"]), "--tape-dir", str(fx["tape"]), "--samples", str(fx["samples"]), "--job-log", str(fx["job"]), "--expect-end", R.iso(fx["end"]), "--checks-rev", "2"])))["5"]
    assert one_log["status"] == R.UNM and any("one --job-log per process start" in x for x in one_log["notes"]) and one_log["numbers"]["error_counters"]["spend_write_errors"] == "not measured (no log line, not persisted)"
    fx["job2"].write_text("hv4_prefetch spend state unreadable: treating the hour as fully spent\n")
    assert by_id(run(fx))["5"]["status"] == R.FAIL
    fx["job2"].write_text("hv4_chain state write failed: OSError\n")
    assert by_id(run(fx))["5"]["status"] == R.FAIL


def test_rolling_hours_uses_bounds_and_a_fresh_origin():
    mk = lambda t, c: {"t_ms": t, "chain_by_day": {"d": c}}
    rows, dec = R.rolling_hours([mk(50 * MIN, 900), mk(2 * HOUR, 1700), mk(2 * HOUR + 30 * MIN, 1800)], origin_ms=0)
    assert dec == 0 and (rows[0]["hi"], rows[0]["lo"]) == (900, 900) and (rows[1]["hi"], rows[1]["lo"]) == (800, 0) and (rows[2]["hi"], rows[2]["lo"]) == (900, 100)
    assert R.rolling_hours([mk(0, 10), mk(MIN, 5)], origin_ms=None)[1] == 1
    assert R.rolling_hours([{"t_ms": 0, "chain_by_day": None}, {"t_ms": 1, "chain_by_day": [1]}], origin_ms=0)[0] == []


# ------------------------------------------------------------------------------------------------------------------------------ check 6
def test_check_6_windows_follow_the_startup_rule_and_list_the_excluded_minutes_raw(tmp_path):
    ok = by_id(run(build(tmp_path, scan_wall=lambda i: 1900, exit_delay=lambda m: 207_000 if m < 8 else 300)))["6"]
    w = ok["numbers"]["windows"]["start_0"]
    assert ok["status"] == R.PASS and w["state"] == "closed" and w["length_s"] == 9 * 60  # to the end of the first good minute (minute 8)
    assert [x["minute"] for x in w["minutes"]][0] == R.iso(START) and len(w["minutes"]) == 9 and w["minutes"][0]["max"] == 207_000 and set(w["minutes"][0]) == {"minute", "n_ticks", "p50", "p99", "max"}
    assert w["scans_excluded"] == 2 and ok["numbers"]["windows"]["start_1"]["length_s"] == 60  # a clean restart ends after its first full minute
    assert ok["numbers"]["catchup_cap_s"] == 600


def test_check_6_a_catchup_past_the_cap_fails_and_a_window_cannot_be_widened(tmp_path):
    c = by_id(run(build(tmp_path, exit_delay=lambda m: 207_000 if m < 15 else 200)))["6"]
    assert c["status"] == R.FAIL and c["numbers"]["windows_over_cap"] == ["start_0"]
    with pytest.raises(SystemExit):
        R.parse_args(["--dir", "x", "--catchup-min", "30"])  # the cap is a constant: there is no flag


def test_check_6_scan_and_exit_limits(tmp_path):
    ds = [sub(tmp_path, n) for n in "1234"]
    assert by_id(run(build(ds[0], scan_wall=lambda i: 5500 if i == 100 else 900)))["6"]["status"] == R.FAIL
    assert by_id(run(build(ds[1], exit_delay=lambda m: 11_000 if m == 400 else 200)))["6"]["status"] == R.FAIL
    p99 = by_id(run(build(ds[2], scan_wall=lambda i: 2500 if 2 <= i < 8 else 900)))["6"]  # 6 of 299 scans over 2 s: nearest-rank p99 is 2,500
    assert p99["status"] == R.FAIL and p99["numbers"]["scan_wall_ms"]["p99"] == 2500 and p99["numbers"]["scan_wall_ms"]["scans_over_2000"] == 6
    two = by_id(run(build(ds[3], scan_wall=lambda i: 2500 if 2 <= i < 4 else 900)))["6"]  # 2 over 2 s out of 299: p99 stays 900
    assert two["status"] == R.PASS and two["numbers"]["scan_wall_ms"]["p99"] == 900


def test_check_6_a_listed_feed_gap_needs_a_source_and_excludes_its_minutes(tmp_path):
    spike = lambda m: 11_000 if m == 400 else 200
    gap = f"{R.iso(START + 399 * MIN)}/{R.iso(START + 402 * MIN)}"
    ok = by_id(run(build(sub(tmp_path, "g"), exit_delay=spike), "--feed-gap", gap + "@arena-feed-status gap row tape-2026-10-20T04"))["6"]
    assert ok["status"] == R.PASS and ok["numbers"]["windows"]["listed_feed_gap_0"]["source"].startswith("arena-feed-status")
    with pytest.raises(SystemExit):
        R.parse_args(["--dir", "x", "--feed-gap", gap])  # no source, not accepted


def test_check_6_p99_is_unmeasured_when_the_per_minute_rows_cannot_decide_and_missing_minutes_make_it_unmeasured(tmp_path):
    c = by_id(run(build(tmp_path, exit_delay=lambda m: 3000 if 100 <= m < 130 else 200)))["6"]
    assert c["numbers"]["exit_check_delay_ms"]["p99_status"] == R.UNM and c["status"] == R.UNM
    d = by_id(run(build(sub(tmp_path, "m"), skip_ticks=lambda m: 300 <= m < 305)))["6"]
    assert d["status"] == R.UNM and d["numbers"]["minutes_with_no_tick_timing_row_outside_windows"] == 5


# ------------------------------------------------------------------------------------------------------------------------------ checks 7 to 10
def test_check_7_sell_alert_and_already_has_a_sell_fail(tmp_path):
    c = by_id(run(build(tmp_path, extra=lambda fx: fx["L"].add("CON-X", late(fx), kind="sell_alert", pid="o", waited_ms=61_000, tries=6))))["7"]
    assert c["status"] == R.FAIL and c["numbers"]["sell_alert_rows"] == 1
    c2 = by_id(run(build(sub(tmp_path, "d"), extra=lambda fx: fx["L"].add("CON-X", late(fx), kind="order_refused", order_id="o", side="sell", reason="refused", detail="x: already has a sell"))))["7"]
    assert c2["status"] == R.FAIL and c2["numbers"]["sell_refused_drop_rows"] == {"already has a sell": 1}


def test_check_8_restart_conditions(tmp_path):
    ok = by_id(run(build(tmp_path)))["8"]
    assert ok["status"] == R.PASS and ok["numbers"]["process_starts"] == 2
    dirs = [sub(tmp_path, n) for n in ("sha", "third", "dup", "chain", "one", "reset", "dirty")]
    assert by_id(run(build(dirs[0], restart_sha="b" * 40)))["8"]["status"] == R.FAIL
    assert by_id(run(build(dirs[1], extra=lambda fx: fx["L"].add("SCAN", late(fx), kind="start", binding={"dirty": False, "git_sha": "a" * 40}, frozen_sha="a" * 40, lane_h=False))))["8"]["status"] == R.FAIL

    def dup(fx):
        for _ in range(2):
            fx["L"].add("SCAN", late(fx), kind="card_refused", pool="Z", reasons=["other"])

    d = by_id(run(build(dirs[2], extra=dup)))["8"]
    assert d["status"] == R.FAIL and d["numbers"]["duplicate_rows"]["SCAN"] == 1

    def tamper(fx):
        f = sorted((fx["root"] / "decisions" / "CON-L").glob("*.jsonl"))[0]
        f.write_text(f.read_text().replace('"call":"entry"', '"call":"recheck"', 1))

    t = by_id(run(build(dirs[3], extra=tamper)))["8"]
    assert t["status"] == R.FAIL and t["numbers"]["decision_log_chain_ok"] is False
    assert by_id(run(build(dirs[4], start2=False)))["8"]["status"] == R.UNM
    assert by_id(run(build(sub(tmp_path, "one-tamper"), start2=False, extra=tamper)))["8"]["status"] == R.FAIL  # a broken chain fails at any start count
    reset = lambda fx: rewrite_samples(fx, lambda r: r.update(chain_by_day={k: 0 for k in r["chain_by_day"]}) if r["t_ms"] > fx["restart"] + HOUR else None)
    assert by_id(run(build(dirs[5], extra=reset)))["8"]["status"] == R.FAIL

    assert by_id(run(build(dirs[6], restart_binding_dirty=True)))["8"]["status"] == R.FAIL  # a start row whose checkout was dirty


def test_a_missing_input_is_unmeasured_never_fail_error_or_pass(tmp_path):
    fx = build(tmp_path)
    # check 8: no samples, with the job log and a restart -> UNMEASURED (not the false FAIL); a missing samples file behaves the same
    assert by_id(run(fx, samples=False))["8"]["status"] == R.UNM
    missing = run(fx, "--samples", str(tmp_path / "nope.jsonl"), samples=False)
    assert by_id(missing)["8"]["status"] == R.UNM and by_id(missing)["5"]["status"] == R.UNM and "unreadable" in missing["sampler"]["note"]
    # check 9: no tape dir -> UNMEASURED
    assert by_id(R.build_report(R.parse_args(["--dir", str(fx["base"]), "--tape-dir", str(tmp_path / "no-tape"), "--samples", str(fx["samples"]), "--expect-end", R.iso(fx["end"]), "--checks-rev", "2"])))["9"]["status"] == R.UNM
    # a nonexistent job log does not abort the report
    rec = run(fx, "--job-log", str(tmp_path / "gone.log"))
    assert rec["verdict"] != "ERROR" and rec["job_logs"]["logs_missing"] == 1 and rec["job_logs"]["logs_read"] == 2
    # a null or list by_day in chain-credits.json does not abort the report
    for bad in (None, [1, 2], "x"):
        (fx["root"] / "chain-credits.json").write_text(json.dumps({"by_day": bad}))
        r = run(fx)
        assert by_id(r)["5"]["status"] == R.UNM and "ERROR" not in r["verdict"]
    # samples whose chain_by_day is null or a list are skipped, not counted as a counter reset
    rewrite_samples(fx, lambda r: r.update(chain_by_day=[1]) if r["t_ms"] % 20 == 0 else None)
    assert by_id(run(fx))["8"]["status"] != R.FAIL
    # no decision rows at all: checks 1, 3, 7, 8, 9 are UNMEASURED
    shutil.rmtree(fx["root"] / "decisions")
    e = by_id(run(fx))
    assert {k: e[k]["status"] for k in "13789"} == {k: R.UNM for k in "13789"} and e["2"]["status"] == R.UNM and "ERROR" not in run(fx)["verdict"]


def test_check_9_feed(tmp_path):
    assert by_id(run(build(tmp_path)))["9"]["status"] == R.PASS
    fx = build(sub(tmp_path, "m"))
    next(iter(sorted(fx["tape"].glob("tape-2026-10-20T05*")))).unlink()
    assert by_id(run(fx))["9"]["status"] == R.FAIL
    assert by_id(run(build(sub(tmp_path, "n")), samples=False))["9"]["status"] == R.UNM
    one = build(sub(tmp_path, "one"))
    rows = [json.loads(x) for x in one["samples"].read_text().splitlines()]
    one["samples"].write_text(json.dumps(rows[-1]) + "\n")
    assert by_id(run(one))["9"]["status"] == R.UNM  # one sample cannot show 'writes throughout'
    paused = build(sub(tmp_path, "p"), extra=lambda fx: rewrite_samples(fx, lambda r: r["feed"].update(writing_paused=True) if r["t_ms"] == START + 502 * MIN else None))
    assert by_id(run(paused))["9"]["status"] == R.FAIL
    reset = build(sub(tmp_path, "r"), extra=lambda fx: rewrite_samples(fx, lambda r: r["feed"].update(gaps=0, lines_written=5 + (r["t_ms"] - START - 600 * MIN) // 1000) if r["t_ms"] > START + 600 * MIN else None))
    assert by_id(run(reset))["9"]["status"] == R.UNM  # a feed restart resets its counters: the deltas are not comparable


def test_check_10_needs_the_register_print_from_the_job_log(tmp_path):
    fx = build(tmp_path)
    ok = by_id(run(fx))["10"]
    assert ok["status"] == R.PASS and ok["numbers"]["register_print_seen_in_job_log"] is True and ok["numbers"]["bots_json_lines"] == 9
    assert by_id(run(fx, job=False))["10"]["status"] == R.UNM  # bots.json alone is not the print
    fx["job"].write_text("a log without the print\n")
    assert by_id(run(fx))["10"]["status"] == R.UNM
    assert by_id(run(build(sub(tmp_path, "e"), n_bots=8)))["10"]["status"] == R.FAIL
    assert by_id(run(build(sub(tmp_path, "f2"), extra=lambda fx: fx["L"].add("BST-L", late(fx), kind="skip"))))["10"]["status"] == R.FAIL


# ------------------------------------------------------------------------------------------------------------------------------ trim, v_missing, memory
def test_trim_checks_block_registration(tmp_path):
    big = run(build(tmp_path, trim_ms=lambda i: 1500.0 if i == 40 else 150.0))
    assert by_id(big)["QP-T1"]["status"] == R.FAIL and any("trim_ms > 1000" in b for b in big["blocks_registration"]) and big["verdict"] == "FAIL"

    def floor(fx):
        def f(rows):
            rows[-1]["rows_below_trim_floor"] = 1
            return rows

        rewrite_trim(fx, f)

    f = run(build(sub(tmp_path, "f"), extra=floor))
    assert by_id(f)["QP-T2"]["status"] == R.FAIL and any("NOT_DECIDABLE" in b for b in f["blocks_registration"])
    v = run(build(sub(tmp_path, "v"), v_ratio=0.6))
    assert by_id(v)["QP-V1"]["status"] == R.FAIL and by_id(v)["QP-V1"]["numbers"]["blocks_registration"]
    n = by_id(run(build(sub(tmp_path, "ok"))))["QP-T3"]["numbers"]
    assert n["engine_ids"] == 2 and n["process_starts_in_scan_log"] == 2 and set(n["segments"]) == {"eng1", "eng2"} and n["largest_gap_between_lines_per_engine_id"]["eng1"]["max_gap_s"] == 300


def test_a_trim_line_gap_over_ten_tape_minutes_fails_unless_inside_a_listed_feed_gap(tmp_path):
    def hole(fx):
        rewrite_trim(fx, lambda rows: [r for i, r in enumerate(rows) if not 20 <= i < 23])  # three lines missing: a 20 min gap

    fx = build(tmp_path, extra=hole)
    t = by_id(run(fx))["QP-T3"]
    assert t["status"] == R.FAIL and t["numbers"]["gaps_over_10_tape_min"][0]["gap_s"] == 20 * 60
    gap = f"{R.iso(START + 95 * MIN)}/{R.iso(START + 125 * MIN)}@arena tape gap row"
    assert by_id(run(fx, "--feed-gap", gap))["QP-T3"]["status"] == R.PASS


def test_trim_counters_that_go_backwards_within_an_engine_id_fail(tmp_path):
    def back(fx):
        def f(rows):
            rows[10]["hist_trimmed_prints"] = 0
            return rows

        rewrite_trim(fx, f)

    assert by_id(run(build(tmp_path, extra=back)))["QP-T3"]["status"] == R.FAIL


def test_memory_plan_a_b_slope_window_memory_max_and_read_length(tmp_path):
    c = by_id(run(build(tmp_path, rss=lambda h: 1000 + 150 * h), "--host-cap-mb", "10240"))["QP-M1"]
    plan = c["numbers"]["plan"]
    assert abs(c["numbers"]["slope_mb_per_h_conservative_max_of_segments"] - 150) < 1
    assert plan["plan_A_required_memory_max_mb"] > 30_000 and plan["implied"] == "B" and plan["plan_B_restart_cadence_h"] == 24.0 and plan["read_hours"] == 168
    assert abs(plan["projected_hours_to_memory_max"] - (10_240 - c["numbers"]["rss_after_drain_mb"]) / 150) < 1
    a = by_id(run(build(sub(tmp_path, "a")), "--host-cap-mb", "10240"))["QP-M1"]["numbers"]["plan"]
    assert a["implied"] == "A"
    h336 = by_id(run(build(sub(tmp_path, "h")), "--read-hours", "336"))["QP-M1"]["numbers"]["plan"]
    assert h336["read_hours"] == 336 and h336["plan_A_required_memory_max_mb"] > a["plan_A_required_memory_max_mb"]  # H is a parameter
    assert by_id(run(build(sub(tmp_path, "nh")), read_hours=False))["QP-M1"]["status"] == R.UNM
    # the slope starts at stub hour 2: a steep first hour does not enter it
    steep = by_id(run(build(sub(tmp_path, "s"), rss=lambda h: 5000 if h < 1.5 else 500 + 20 * h)))["QP-M1"]["numbers"]
    assert abs(steep["slope_mb_per_h_conservative_max_of_segments"] - 20) < 1
    # MemoryMax comes from the sampled cgroup value; a differing --memory-max-mb is refused
    mm = by_id(run(build(sub(tmp_path, "mm"), cg_max=8192.0), "--memory-max-mb", "10240"))["QP-M1"]
    assert mm["status"] == R.UNM and any("refused" in x for x in mm["notes"])
    assert by_id(run(build(sub(tmp_path, "mm2"), cg_max=8192.0)))["QP-M1"]["numbers"]["plan"]["memory_max_mb"] == 8192.0


def test_memory_nonlinear_growth_oom_and_unplanned_start(tmp_path):
    curve = by_id(run(build(tmp_path, rss=lambda h: 500 + (5 * h if h < 6 else 5 * 6 + 80 * (h - 6))), "--host-cap-mb", "100000"))["QP-M1"]["numbers"]["plan"]
    assert curve["nonlinear_segments"] and curve["implied"].startswith("B at 24 h")
    assert by_id(run(build(sub(tmp_path, "o"), oom=1)))["QP-M1"]["status"] == R.FAIL
    cache = by_id(run(build(sub(tmp_path, "pc"), extra=lambda fx: rewrite_samples(fx, lambda r: r["procs"][0].update(cg_peak_mb=10240.0)))))["QP-M1"]  # a cgroup peak at MemoryMax made of page cache is not an OOM
    assert cache["status"] == R.PASS and cache["numbers"]["cgroup_peak_mb_incl_page_cache"] == 10240.0
    u = by_id(run(build(sub(tmp_path, "u"), extra=lambda fx: fx["L"].add("SCAN", late(fx), kind="start", binding={"dirty": False, "git_sha": "a" * 40}, frozen_sha="a" * 40, lane_h=False))))["QP-M1"]
    assert u["status"] == R.FAIL


def test_memory_without_samples_accepts_job_memory_points_and_says_unmeasured_by_the_runner(tmp_path):
    fx = build(tmp_path)
    none = by_id(run(fx, samples=False))["QP-M1"]
    assert none["status"] == R.UNM and "UNMEASURED by the runner" in json.dumps(none["numbers"])
    args = []
    for h in (1, 6, 12, 18, 24):
        args += ["--rss-point", f"{R.iso(START + h * HOUR)}={600 + 50 * h}"]
    c = by_id(run(fx, *args, samples=False))["QP-M1"]
    assert c["numbers"]["source"].startswith("figures supplied on the command line") and abs(c["numbers"]["slope_mb_per_h_conservative_max_of_segments"] - 50) < 1


# ------------------------------------------------------------------------------------------------------------------------------ policy, output, sampler
def test_the_record_never_carries_ledger_bots_card_or_pnl_content(tmp_path):
    rec = run(build(tmp_path))
    text = json.dumps(rec)
    assert CANARY not in text and "BST-hidden" not in text and '"botnote"' not in text and "est_pnl" not in text
    assert not any("ledger" in i["path"].lower() for i in rec["inputs"]) and all(len(i["sha256"]) == 64 for i in rec["inputs"])
    assert rec["tool"]["sha256"] and "git_head" in rec["tool"] and "git_dirty" in rec["tool"]
    R.forbid_scan(rec)
    with pytest.raises(R.Refused):
        R.forbid_scan({"a": [{"est_pnl_sol": 1}]})


def test_build_report_runs_the_forbidden_field_scan_on_the_record(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(R, "forbid_scan", lambda rec: calls.append(rec["schema"]))
    run(build(tmp_path))
    assert calls == [R.SCHEMA]

    def refuse(rec):
        raise R.Refused("forbidden output key")

    monkeypatch.setattr(R, "forbid_scan", refuse)
    with pytest.raises(R.Refused):
        run(build(sub(tmp_path, "b")))


def test_paths_naming_a_ledger_or_a_sealed_tree_are_refused_and_the_exit_code_is_zero(tmp_path, capsys):
    # fixed strings, not tmp_path: tmp_path embeds this test's name, which would trip the ledger word for every case
    for bad in ("/data/mal/ok/ledger/x", "/data/mal/tip-tape-archive/x", "/home/claude/data/Tip-Tape-Archive-2/x", "/data/mal/blocks/x", "/var/lib/mal/sealed/fast-trades-tip/x", "/data/mal/kalshi/z"):
        with pytest.raises(R.Refused):
            R.guard(bad)
    assert R.guard("/data/mal/arena/_shakedown-hv4-1020") == Path("/data/mal/arena/_shakedown-hv4-1020")
    assert R.main(["--dir", "/data/mal/x/ledger"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["error"] == "Refused"
    assert R.main(["--dir", "/data/mal/tip-tape-archive/y"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["error"] == "Refused"
    assert R.main(["--dir", str(tmp_path / "does-not-exist")]) == 0  # an empty dir is a report, not a crash
    assert "verdict" in capsys.readouterr().out


def test_main_prints_the_table_then_one_json_record_and_writes_the_out_dir(tmp_path, capsys):
    fx = build(tmp_path)
    od = tmp_path / "out"
    rc = R.main(["--dir", str(fx["base"]), "--tape-dir", str(fx["tape"]), "--samples", str(fx["samples"]), "--job-log", str(fx["job"]), "--expect-end", R.iso(fx["end"]), "--out-dir", str(od)])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["schema"] == R.SCHEMA and "| check | status | numbers |" in "\n".join(lines) and (od / "report.json").exists() and (od / "report.md").exists()


def test_sampler_takes_a_sample_appends_never_truncates_and_survives_a_bad_sample(tmp_path, monkeypatch):
    fx = build(tmp_path)
    feed = fx["tape"] / "arena-feed-status.json"
    s = R.take_sample(fx["base"], feed)
    assert s["schema"] == R.SAMPLE_SCHEMA and sum(s["chain_by_day"].values()) > 0 and s["spend"]["max_bucket"] == 8 and s["feed"]["writing_paused"] is False and s["procs"] == []
    me = R.proc_mem(os.getpid())
    assert me["rss_mb"] > 1 and me["hwm_mb"] >= me["rss_mb"] - 1
    out = tmp_path / "s.jsonl"
    a = R.parse_args(["sample", "--dir", str(fx["base"]), "--out", str(out), "--feed-status", str(feed), "--until", "2020-01-01T00:00:00Z"])
    assert R.sampler(a) == 0
    first = out.read_text().splitlines()
    assert len(first) == 1
    assert R.sampler(a) == 0  # a second run into the same file appends: the first line is preserved
    again = out.read_text().splitlines()
    assert len(again) == 2 and again[0] == first[0]
    # a sample that raises is an error line carrying the class only, and the loop goes on
    monkeypatch.setattr(R, "take_sample", lambda *a: (_ for _ in ()).throw(AttributeError("secret-ish detail")))
    assert R.sampler(a) == 0
    last = json.loads(out.read_text().splitlines()[-1])
    assert last["error"] == "AttributeError" and "detail" not in json.dumps(last) and len(out.read_text().splitlines()) == 3
    monkeypatch.undo()
    (fx["root"] / "chain-credits.json").write_text(json.dumps({"by_day": None}))
    assert R.take_sample(fx["base"], feed)["chain_by_day"] is None  # a misshaped counter file is a null section, not an exception
    assert R.sample_line(fx["base"], tmp_path / "no-feed.json").endswith(b"\n")
    with pytest.raises(R.Refused):
        R.sampler(R.parse_args(["sample", "--dir", str(fx["base"]), "--out", "/data/mal/x/ledger/s.jsonl"]))
    # error lines are counted, not used as samples
    rec = run(fx, "--samples", str(out), samples=False)
    assert rec["sampler"]["error_lines"] == 1


def test_runner_pid_match_needs_the_runner_script_and_the_exact_root(tmp_path):
    assert R.find_runner_pids(tmp_path / "root") == []  # this pytest process is not a runner


def test_canon_and_the_chain_match_the_runners_own_decision_log(tmp_path):
    pytest.importorskip("numpy")
    from tools import hv4_runner as H

    assert R.canon({"b": [1, 2.5, None], "a": "x"}) == H.canon({"b": [1, 2.5, None], "a": "x"})
    log = H.DecisionLog(tmp_path / "root")
    for i in range(5):
        log.append("SCAN", START + i * 1000, {"kind": "scan_timing", "scan_wall_ms": 10 + i})
    L = R.load_logs(tmp_path / "root", [])
    assert L.arms["SCAN"] == {"rows": 5, "chain_ok": True, "chain_error": None, "head_seq": 4} and len(L.scan_timing) == 5


# ------------------------------------------------------------------------------------------------------------------------------ QP-T3 tail and check 5 early hours (QP #764 comment 6279001927)
def test_a_trim_file_that_stops_before_the_end_of_its_process_fails_the_tail(tmp_path):
    ok = by_id(run(build(tmp_path)))["QP-T3"]["numbers"]["tail_to_process_end_per_engine_id"]
    assert ok["eng1"]["tail_s"] == 300 and ok["eng2"]["tail_s"] <= 300 and ok["eng1"]["process_end"] == R.iso(START + 12 * HOUR)  # an earlier process ends with its last tick minute

    def stop_last(fx):  # housekeeping dies for good 20 tape minutes before the run ends: no gap between lines, only a tail
        rewrite_trim(fx, lambda rows: [r for r in rows if r["engine_id"] == "eng1"] + [r for r in rows if r["engine_id"] == "eng2"][:-3])

    t = by_id(run(build(sub(tmp_path, "last"), extra=stop_last)))["QP-T3"]
    assert t["status"] == R.FAIL and t["numbers"]["gaps_over_10_tape_min"] == [] and [x["engine_id"] for x in t["numbers"]["tails_over_10_tape_min"]] == ["eng2"]
    assert t["numbers"]["tails_over_10_tape_min"][0]["tail_s"] >= 20 * 60 - 1

    def stop_first(fx):  # the same in the earlier process: compared with the last tick minute before the next start row
        rows = [json.loads(x) for x in (fx["root"] / "engine-trim-counters.jsonl").read_text().splitlines()]
        e1 = [r for r in rows if r["engine_id"] == "eng1"][:-3]
        rewrite_trim(fx, lambda _: e1 + [r for r in rows if r["engine_id"] == "eng2"])

    t1 = by_id(run(build(sub(tmp_path, "first"), extra=stop_first)))["QP-T3"]
    assert t1["status"] == R.FAIL and [x["engine_id"] for x in t1["numbers"]["tails_over_10_tape_min"]] == ["eng1"]
    gap = f"{R.iso(START + 11 * HOUR)}/{R.iso(START + 12 * HOUR)}@arena tape gap row"
    assert by_id(run(build(sub(tmp_path, "gap"), extra=stop_first), "--feed-gap", gap))["QP-T3"]["status"] == R.PASS  # a tail inside a listed feed gap is not a housekeeping failure


def sp_sample(t, c, s, mb):
    return {"t_ms": t, "chain_by_day": {"2026-10-19": c}, "spend": {"sum_last_hour": s, "buckets": 60, "max_bucket": mb}, "procs": [], "feed": None}


def test_early_spend_bound_uses_the_last_hour_spend_plus_the_previous_samples_largest_bucket():
    # QP's figures: the counter run total is 1,527 (over the limit) but the last-hour spend is 1,234 and the previous sample held a 260 bucket: 1,234 + 260 = 1,494
    ss = [sp_sample(START + 30 * MIN, 700, 700, 260), sp_sample(START + 31 * MIN, 720, 720, 260), sp_sample(START + 70 * MIN, 1527, 1234, 81)]
    bounds, info = R.early_spend_bounds(ss, START)
    assert bounds[START + 70 * MIN] == 1494 < 1500 and info["applies"] and info["first_hour_samples"] == 2 and info["first_divergence"] == R.iso(START + 70 * MIN)
    rh, _ = R.rolling_hours(ss, START)
    assert max(r["hi"] for r in rh) == 1527  # without the spend bound the window is bounded by the run total only
    # no evidence (the counter and the spend disagree from the first sample), or the first sample is more than an hour after the start: no early bound
    assert R.early_spend_bounds([sp_sample(START + 30 * MIN, 700, 400, 20), sp_sample(START + 31 * MIN, 720, 420, 20)], START)[0] == {}
    assert R.early_spend_bounds([sp_sample(START + 61 * MIN, 700, 700, 20), sp_sample(START + 62 * MIN, 720, 720, 20)], START)[0] == {}
    assert R.early_spend_bounds(ss, None)[0] == {}
    # a sample that exists before the window starts: the counter alone bounds it, not the spend
    late_ss = ss + [sp_sample(START + 95 * MIN, 1700, 1100, 81)]
    assert START + 95 * MIN not in R.early_spend_bounds(late_ss, START)[0]


def test_check_5_early_hours_pass_on_the_spend_bound_and_stay_unmeasured_without_it(tmp_path):
    # 260 calls in minute 3, then 19 a minute: the run total passes 1,500 at about minute 70 while any rolling hour holds about 1,140
    def at(k):
        t = START + k * MIN
        c = sum(260 if m == 3 else (19 if m >= 4 else 0) for m in range(k))
        file_b = {m: (260 if m == 3 else 19) for m in range(3, k) if START + m * MIN > t - HOUR - MIN}
        spend = sum(n for m, n in file_b.items() if START + m * MIN > t - HOUR)
        return sp_sample(t, c, spend, max(file_b.values(), default=0))

    ss = [at(k) for k in range(30, 111)]
    root = sub(tmp_path, "r")
    (root / "chain-credits.json").write_text(json.dumps({"by_day": {"2026-10-19": ss[-1]["chain_by_day"]["2026-10-19"]}}))
    starts = [{"ms": START, "seq": 0, "frozen_sha": "a" * 40, "lane_h": False}]
    job = {"logs_read": 1, "spend_state_reset": 0, "state_write_failed": 0}
    c = R.eval_check_5(root, ss, starts, [], job, True)
    assert c["status"] == R.PASS and c["numbers"]["early_hours_spend_bound"]["applies"] and c["numbers"]["rolling_hour_calls"]["max_upper_bound"] < 1400  # S plus the 260 bucket still in the previous sample: conservative, as QP states
    assert max(x["chain_by_day"]["2026-10-19"] for x in ss) > 1500  # the run total itself is over the hourly limit
    no_spend = [{**x, "spend": None} for x in ss]
    u = R.eval_check_5(root, no_spend, starts, [], job, True)
    assert u["status"] == R.UNM and u["numbers"]["early_hours_spend_bound"]["applies"] is False and u["numbers"]["rolling_hour_calls"]["max_upper_bound"] > 1500


# ------------------------------------------------------------------------------------------------------------------------------ safe(): a check that raises is UNMEASURED for that check only
@pytest.mark.parametrize("exc", [KeyError("k"), AttributeError("a"), ValueError("v"), OSError("o"), TypeError("t"), RuntimeError("r")])
def test_a_check_that_raises_is_unmeasured_for_that_check_only_and_the_report_is_not_aborted(tmp_path, monkeypatch, exc):
    base = run(build(tmp_path))

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(R, "eval_check_7", boom)
    monkeypatch.setattr(R, "eval_timing", boom)
    rec = run(build(sub(tmp_path, "again")))
    c = by_id(rec)
    for cid in ("6", "7"):
        assert c[cid]["status"] == R.UNM and c[cid]["numbers"] == {"input_error": type(exc).__name__} and "input error" in c[cid]["notes"][0]
    assert rec["verdict"] != "ERROR" and rec["schema"] == R.SCHEMA
    assert {k: v["status"] for k, v in c.items() if k not in ("6", "7")} == {k: v["status"] for k, v in by_id(base).items() if k not in ("6", "7")}  # every other check is untouched
    assert {"6", "7"} <= set(rec["unmeasured_checks"])
    # the trim and memory sections run under the same wrapper
    monkeypatch.setattr(R, "eval_trim", boom)
    monkeypatch.setattr(R, "eval_memory", boom)
    t = by_id(run(build(sub(tmp_path, "third"))))
    assert t["QP-T1"]["status"] == R.UNM and t["QP-M1"]["status"] == R.UNM and t["1"]["status"] == R.PASS


def test_safe_lets_a_policy_refusal_through_and_names_only_the_error_class():
    def refuse():
        raise R.Refused("path refused by policy")

    with pytest.raises(R.Refused):
        R.safe("7", "x", refuse)
    c = R.safe("7", "x", lambda: {}["missing key with detail"])
    assert c["status"] == R.UNM and c["numbers"] == {"input_error": "KeyError"} and "detail" not in json.dumps(c)


# ------------------------------------------------------------------------------------------------------------------------------ QP #764 comment 6279203212: a listed feed gap clears only its own length; the strict early-bound rule
def test_uncovered_ms_subtracts_merged_gaps_only():
    G = lambda a, b: {"start_ms": a, "end_ms": b}
    assert R.uncovered_ms(0, 120 * MIN, [G(10 * MIN, 11 * MIN)]) == 119 * MIN
    assert R.uncovered_ms(0, 120 * MIN, [G(10 * MIN, 16 * MIN), G(12 * MIN, 18 * MIN)]) == 112 * MIN  # overlapping gaps are merged: 8 min covered, not 12
    assert R.uncovered_ms(0, 120 * MIN, [G(10 * MIN, 16 * MIN), G(10 * MIN, 16 * MIN)]) == 114 * MIN
    assert R.uncovered_ms(0, 120 * MIN, [G(-5 * MIN, 200 * MIN)]) == 0 and R.uncovered_ms(0, 120 * MIN, [G(130 * MIN, 140 * MIN)]) == 120 * MIN


def test_a_listed_feed_gap_clears_only_its_own_length_between_trim_lines(tmp_path):
    def hole(fx):  # eng1 lines trim_no 20..43 missing: a 125 tape-minute hole between the lines at 90 and 215 min
        rewrite_trim(fx, lambda rows: [r for r in rows if not (r["engine_id"] == "eng1" and 20 <= r["trim_no"] < 44)])

    one_min = f"{R.iso(START + 100 * MIN)}/{R.iso(START + 101 * MIN)}@arena tape gap row"
    t = by_id(run(build(sub(tmp_path, "a"), extra=hole), "--feed-gap", one_min))["QP-T3"]
    assert t["status"] == R.FAIL and t["numbers"]["gaps_over_10_tape_min"][0]["gap_s"] == 125 * 60 and t["numbers"]["gaps_over_10_tape_min"][0]["uncovered_by_listed_feed_gaps_s"] == 124 * 60
    whole = f"{R.iso(START + 85 * MIN)}/{R.iso(START + 220 * MIN)}@arena tape gap row"
    assert by_id(run(build(sub(tmp_path, "b"), extra=hole), "--feed-gap", whole))["QP-T3"]["status"] == R.PASS  # a gap that covers the whole hole explains it
    part = f"{R.iso(START + 90 * MIN)}/{R.iso(START + 210 * MIN)}@arena tape gap row"  # covers 120 of the 125 min: 5 min remain, within 10
    assert by_id(run(build(sub(tmp_path, "c"), extra=hole), "--feed-gap", part))["QP-T3"]["status"] == R.PASS


def test_a_listed_feed_gap_clears_only_its_own_length_of_a_permanent_tail_failure(tmp_path):
    end = START + 25 * HOUR

    def stop_last(fx):  # housekeeping dies for good 20 tape minutes before the run ends
        rewrite_trim(fx, lambda rows: [r for r in rows if r["engine_id"] == "eng1"] + [r for r in rows if r["engine_id"] == "eng2"][:-3])

    g = lambda a, b: f"{R.iso(end - a * MIN)}/{R.iso(end - b * MIN)}@arena tape gap row"
    one = by_id(run(build(sub(tmp_path, "a"), extra=stop_last), "--feed-gap", g(19, 18)))["QP-T3"]
    assert one["status"] == R.FAIL and one["numbers"]["tails_over_10_tape_min"][0]["uncovered_by_listed_feed_gaps_s"] >= 19 * 60 - 1
    twice = by_id(run(build(sub(tmp_path, "b"), extra=stop_last), "--feed-gap", g(19, 13), "--feed-gap", g(19, 13)))["QP-T3"]  # the same 6 min listed twice counts once
    assert twice["status"] == R.FAIL
    whole = by_id(run(build(sub(tmp_path, "c"), extra=stop_last), "--feed-gap", g(21, 0)))["QP-T3"]
    assert whole["status"] == R.PASS


def test_early_bound_needs_agreement_at_every_first_hour_sample_and_adds_the_largest_difference():
    tail = sp_sample(START + 70 * MIN, 1527, 1234, 81)
    # a diff of 1 in the first hour is a read race: it is added to the bound (1,234 + 260 + 1)
    b, i = R.early_spend_bounds([sp_sample(START + 30 * MIN, 700, 700, 260), sp_sample(START + 31 * MIN, 721, 720, 260), tail], START)
    assert b[START + 70 * MIN] == 1495 and i["max_abs_diff_first_hour"] == 1 and i["first_hour_samples"] == 2
    # a divergence inside the first hour, even between two agreeing samples, disproves 'every call was a prefetch call': no early bound
    mid = [sp_sample(START + 30 * MIN, 700, 700, 260), sp_sample(START + 45 * MIN, 900, 860, 260), sp_sample(START + 59 * MIN, 1100, 1100, 260), tail]
    assert R.early_spend_bounds(mid, START)[0] == {} and R.early_spend_bounds(mid, START)[1]["max_abs_diff_first_hour"] == 40
    # a first-hour sample with no spend data is incomplete evidence
    gap = [sp_sample(START + 30 * MIN, 700, 700, 260), {**sp_sample(START + 31 * MIN, 720, 720, 260), "spend": None}, sp_sample(START + 32 * MIN, 740, 740, 260), tail]
    assert R.early_spend_bounds(gap, START)[0] == {}
    # a divergence after the first hour does not disturb it (the file no longer holds the whole run)
    assert START + 70 * MIN in R.early_spend_bounds([sp_sample(START + 30 * MIN, 700, 700, 260), sp_sample(START + 31 * MIN, 720, 720, 260), tail], START)[0]
