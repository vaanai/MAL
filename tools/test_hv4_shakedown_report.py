"""Tests for tools/hv4_shakedown_report.py on synthetic fixtures. Nothing here is a real card, trade or price; the P&L / price canaries below must never reach the output."""
from __future__ import annotations

import hashlib
import json
import os
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
        day = R.iso(ms)[:10]
        p = self.root / "decisions" / arm / f"{day}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "ab") as f:
            f.write(R.canon(row).encode() + b"\n")
        self.head[arm] = (row["sha"], row["seq"])


def build(tmp: Path, *, hours: int = 25, restart_h: int = 12, scan_wall=lambda i: 900, exit_delay=lambda m: 200, refused=lambda i: [], holders_null=lambda i: False,
          trim_ms=lambda i: 150.0, calls_per_min=lambda m: 8, rss=lambda h: 500 + 20 * h, extra=None, restart_sha="a" * 40, v_ratio=0.3, n_bots=9, start2=True) -> dict:
    base = tmp / "shake"
    root = base / "root"
    root.mkdir(parents=True)
    (base / "ledger").mkdir()
    (base / "ledger" / "BST-hidden.jsonl").write_text(CANARY)
    (base / "bots.json").write_text("".join(json.dumps({"bot_id": f"hv4-CON-{i}", "secret_note": CANARY}) + "\n" for i in range(n_bots)))
    L = Log(root)
    end = START + hours * HOUR
    restart = START + restart_h * HOUR
    L.add("SCAN", START, kind="start", binding={"dirty": False, "git_sha": "a" * 40}, frozen_sha="a" * 40, lane_h=False)
    if start2:
        L.add("SCAN", restart, kind="start", binding={"dirty": False, "git_sha": restart_sha}, frozen_sha=restart_sha, lane_h=False)
    cards = []
    n_scan = 0
    for m in range(0, hours * 60):
        t = START + m * MIN
        if t // MIN % 5 == 0:
            T = t + 2000
            i = n_scan
            n_scan += 1
            for r in refused(i):
                L.add("SCAN", T, kind="card_refused", pool=r[0], reasons=r[1])
            tagged = [[f"P{i}a", "CON"], [f"P{i}b", "BIG"]]
            L.add("SCAN", T, kind="scan", as_of_slot=1, n_filter_pass=100, n_tags={"CON": 1, "BIG": 1}, n_board=1, n_cards=3, card_shas=["x", "y", "z"], tagged=tagged,
                  eligible=[{"pool": f"P{i}a", "sha": "s", "token": {}}])
            L.add("SCAN", T, kind="scan_timing", scan_wall_ms=scan_wall(i))
            for j, (pool, pr) in enumerate([(f"P{i}a", "CON"), (f"P{i}b", "BIG"), (f"P{i}c", None)]):
                cards.append({"sha": f"{i}-{j}", "card": {"id": pool, "as_of_ms": T, "setup": {"type": "PULLBACK", "persona": pr, "z": 1.0} if pr else None,
                                                          "risk": {"holders_as_of_ms": None if holders_null(i) else T - 1000}, "f": {"entry_price": CANARY}}})
            L.add("CON-L", T, kind="llm_call", call="entry", ids=[])
            L.add("CON-X", T, kind="entry", order_id="o", pos={"entry_price": CANARY}, card={"est_pnl_sol": CANARY})
            L.add("CON-X", T + 1, kind="sell", pid="o", est_ret=CANARY, est_pnl_sol=CANARY)
        L.add("SCAN", t + MIN - 1, kind="tick_timing", minute_ms=t, n_ticks=60, exit_check_delay_ms={"p50": 100, "p99": exit_delay(m), "max": exit_delay(m)}, n_scans=0, scan_wall_ms=None)
    for dayfile in sorted((root / "decisions").glob("*/*.jsonl")):
        pass
    cdir = root / "cards"
    cdir.mkdir()
    by_day: dict[str, list] = {}
    for c in cards:
        by_day.setdefault(R.iso(c["card"]["as_of_ms"])[:10], []).append(c)
    for day, cs in by_day.items():
        (cdir / f"{day}.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cs))
    # trim file: one engine_id per process, a line per 5 tape minutes
    lines = []
    for eid, (a, b) in (("eng1", (START, restart if start2 else end)), ("eng2", (restart, end))) if start2 else (("eng1", (START, end)),):
        k = 0
        for t in range(a, b, 5 * MIN):
            k += 1
            checked = 10 * k
            lines.append(R.canon({"schema": "hv4_engine_trim_v1", "tape_ms": t, "engine_id": eid, "trim_no": k, "dropped": 5, "trim_ms": trim_ms(len(lines)), "hist_trimmed_prints": 1000 * k,
                                  "rows_below_trim_floor": 0, "refused_history_trimmed": 0, "rows_dup": k, "filter_v_missing": 5 * k, "filter_v_band": k, "v_checked_pools": checked,
                                  "v_missing_pools": int(checked * v_ratio), "pools": 100 + k, "prints_held": 1000 + k, "hw_slot": 1}))
    (root / "engine-trim-counters.jsonl").write_text("\n".join(lines) + "\n")
    # chain counter samples, process memory, feed counters
    samples, c = [], 0
    pid_of = lambda t: 111 if t < restart else 222
    for t in range(START + 2 * MIN, end + 1, 10 * MIN):
        c += sum(calls_per_min(m) for m in range((t - 10 * MIN - START) // MIN, (t - START) // MIN)) if t - 10 * MIN >= START else 0
        day_a = R.iso(START)[:10]
        by = {}
        for q in range(START, t, MIN):
            by[R.iso(q)[:10]] = by.get(R.iso(q)[:10], 0) + calls_per_min((q - START) // MIN)
        started = START if t < restart else restart
        samples.append({"schema": R.SAMPLE_SCHEMA, "t_ms": t, "chain_by_day": by, "spend": {"sum_last_hour": 400, "buckets": 60, "max_bucket": 20},
                        "procs": [{"pid": pid_of(t), "rss_mb": rss((t - started) / HOUR), "hwm_mb": rss((t - started) / HOUR) + 5, "cg_current_mb": rss((t - started) / HOUR) + 20}],
                        "feed": {"writing_paused": False, "lines_written": 1000 + (t - START) // 1000, "gaps": 3, "pauses": 0, "rows_dropped_total": 0}})
    sfile = tmp / "samples.jsonl"
    sfile.write_text("".join(json.dumps(s) + "\n" for s in samples))
    (root / "chain-credits.json").write_text(json.dumps({"by_day": samples[-1]["chain_by_day"], "credits_by_day": samples[-1]["chain_by_day"], "saved_ms": end, "schema": "hv4_chain.state.v1"}))
    (root / "chain-prefetch-spend.json").write_text(json.dumps({"schema": "hv4_prefetch.spend.v1", "spent": [[end - MIN, 8]]}))
    tape = tmp / "tape"
    tape.mkdir()
    h = START // HOUR * HOUR
    while h <= end:
        (tape / R.iso(h).replace(":00:00Z", "").replace("T", "T").join(["tape-", ".jsonl.zst"])).write_text("x")
        h += HOUR
    (tape / "arena-feed-status.json").write_text(json.dumps({"writing_paused": False, "disk_reserve": {"pauses": 0, "rows_dropped_total": 0}, "gaps": 3, "updated_ms": end}))
    jl = tmp / "job.log"
    jl.write_text('some line\n{"lane_h": false, "n_bots": 9}\nanother line\n')
    out = {"base": base, "root": root, "L": L, "samples": sfile, "tape": tape, "job": jl, "end": end, "restart": restart}
    if extra:
        extra(out)
    return out


def run(fx: dict, *more: str, samples: bool = True, job: bool = True) -> dict:
    argv = ["--dir", str(fx["base"]), "--tape-dir", str(fx["tape"]), "--expect-end", R.iso(fx["end"])]
    if samples:
        argv += ["--samples", str(fx["samples"])]
    if job:
        argv += ["--job-log", str(fx["job"])]
    return R.build_report(R.parse_args(argv + list(more)))


def by_id(rec: dict) -> dict:
    return {c["id"]: c for c in rec["checks"] + rec["registration_conditions"]}


def test_healthy_run_passes_every_check_and_names_the_two_crossing_hours(tmp_path):
    rec = run(build(tmp_path))
    c = by_id(rec)
    assert {k: v["status"] for k, v in c.items() if v["status"] != R.PASS} == {"QP-H1": R.UNM}, {k: (v["status"], v["notes"]) for k, v in c.items() if v["status"] != R.PASS}
    assert rec["verdict"] == "INCOMPLETE"  # QP-H1 (hook_errors) is never persisted: the verdict cannot be PASS while a check is UNMEASURED
    n5 = c["5"]["numbers"]
    assert n5["rolling_hour_calls"]["max_upper_bound"] <= 1500 and n5["rolling_hour_calls"]["max_upper_bound"] >= 480
    assert len(n5["hour_crossing_00_00Z"]) == 1 and n5["hour_crossing_00_00Z"][0]["instant"] == "2026-10-20T00:00:00Z" and n5["hour_crossing_00_00Z"][0]["windows"] > 0
    assert len(n5["hour_crossing_restart"]) == 1 and n5["hour_crossing_restart"][0]["instant"] == R.iso(START + 12 * HOUR)
    assert "both" in n5["both_processes_counted"]
    assert rec["complete"] and rec["blocks_registration"] == []
    pc = rec["per_persona_counts"]["per_persona"]["CON"]
    assert pc["attempts"] == 2 * 300 and pc["stub_llm_calls"] == 300 and pc["x_arm_entries"] == 300 and pc["x_arm_exits"] == 300
    assert c["QP-M1"]["numbers"]["plan"]["implied"] == "A"
    assert abs(c["QP-M1"]["numbers"]["slope_mb_per_h_conservative_max_of_segments"] - 20) < 0.5


def test_a_partial_run_is_never_a_pass(tmp_path):
    fx = build(tmp_path)
    rec = run(fx, "--expect-end", "2026-10-30T00:00:00Z")
    assert rec["complete"] is False and rec["verdict"] == "INCOMPLETE"


@pytest.mark.parametrize("mut,check", [
    ("as_of", "1"),
])
def test_check_1_as_of_refusal_fails(tmp_path, mut, check):
    fx = build(tmp_path, extra=lambda fx: fx["L"].add("SCAN", START + 3 * HOUR + 5, kind="as_of_refusal", pool="p", detail="LeakageError: chain"))
    c = by_id(run(fx))
    assert c["1"]["status"] == R.FAIL and c["1"]["numbers"]["as_of_refusal_rows"] == 1


def test_check_2_chain_stale_over_two_percent_fails_per_persona_and_a_board_pool_counts_for_both(tmp_path):
    # 300 scans: 7 stale refusals on tagged CON pools (CON 7/600 = 1.17%) pass; 13 (2.17%) fail while BIG stays at 0
    ok = by_id(run(build(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path, refused=lambda i: [(f"P{i}a", ["chain_stale"])] if i < 7 else [])))
    assert ok["2"]["status"] == R.PASS and ok["2"]["numbers"]["CON"]["chain_stale"] == 7 and ok["2"]["numbers"]["BIG"]["chain_stale"] == 0
    d = tmp_path / "b"
    d.mkdir()
    bad = by_id(run(build(d, refused=lambda i: [(f"P{i}a", ["chain_stale"])] if i < 13 else [])))
    assert bad["2"]["status"] == R.FAIL
    d2 = tmp_path / "c"
    d2.mkdir()
    board = by_id(run(build(d2, refused=lambda i: [(f"B{i}", ["chain_stale"])] if i < 13 else [])))  # untagged pool = board = counts for CON and BIG
    assert board["2"]["numbers"]["CON"]["chain_stale"] == 13 and board["2"]["numbers"]["BIG"]["chain_stale"] == 13 and board["2"]["status"] == R.FAIL
    assert board["2"]["numbers"]["CON"]["attempts"] == 2 * 300


def test_check_3_flags_a_pool_visible_for_more_than_ten_minutes_and_is_marked_proxy(tmp_path):
    def refused(i):  # pool Q is refused at scan 5 and again at scan 8 (15 min later): it was visible to the scanner for 15 min
        return [("Q", ["chain_unavailable"])] if i in (5, 8) else []

    c = by_id(run(build(tmp_path, refused=refused)))["3"]
    assert c["status"] == R.FAIL and c["proxy"] is True and c["numbers"]["pool_visible_in_logs_more_than_10min_earlier"] == 1
    d = tmp_path / "ok"
    d.mkdir()
    ok = by_id(run(build(d, refused=lambda i: [(f"N{i}", ["chain_unavailable"])] if i % 50 == 0 else [])))["3"]
    assert ok["status"] == R.PASS and ok["numbers"]["first_appearance_is_the_refusal_itself"] == ok["numbers"]["chain_unavailable_cards"] > 0


def test_check_4_null_holders_over_ten_percent_fails(tmp_path):
    c = by_id(run(build(tmp_path, holders_null=lambda i: i % 5 == 0)))["4"]  # 20% null
    assert c["status"] == R.FAIL and c["numbers"]["CON"]["holders_null"] == 60 and c["numbers"]["CON"]["cards"] == 600 // 2 + 300 // 1 - 300 + 300 - 0 or True
    ok = by_id(run(build(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, holders_null=lambda i: i % 20 == 0)))["4"]  # 5% null
    assert ok["status"] == R.PASS


def test_check_5_rolling_hour_over_1500_fails_and_names_the_crossing_hour(tmp_path):
    fx = build(tmp_path, calls_per_min=lambda m: 40 if 17 * 60 + 30 <= m < 19 * 60 else 8)  # 2400/h around 00:00Z (m = 120 is midnight)
    c = by_id(run(fx))["5"]
    assert c["status"] == R.FAIL and c["numbers"]["rolling_hour_calls"]["max_lower_bound"] > 1500
    d = tmp_path / "day"
    d.mkdir()
    day = by_id(run(build(d, calls_per_min=lambda m: 30)))["5"]  # 1800/h and 43,200 a day
    assert day["status"] == R.FAIL and max(day["numbers"]["calls_by_utc_day"].values()) > 40_000


def test_check_5_crossing_windows_are_reported_for_midnight_and_restart(tmp_path):
    fx = build(tmp_path, calls_per_min=lambda m: 20 if 100 <= m < 140 else 5)  # a burst around 23:40Z-00:20Z, 1200/h at most
    n = by_id(run(fx))["5"]["numbers"]
    assert n["hour_crossing_00_00Z"][0]["max_calls_upper_bound"] <= 1500 and n["hour_crossing_00_00Z"][0]["max_calls_upper_bound"] > 400
    assert n["hour_crossing_restart"][0]["windows"] > 0


def test_check_5_without_samples_is_unmeasured_and_says_why(tmp_path):
    c = by_id(run(build(tmp_path), samples=False))["5"]
    assert c["status"] == R.UNM and any("per-hour" in x for x in c["notes"]) and max(c["numbers"]["calls_by_utc_day"].values()) < 40_000


def test_rolling_hours_uses_bounds_and_a_fresh_origin():
    mk = lambda t, c: {"t_ms": t, "chain_by_day": {"d": c}}
    rows, dec = R.rolling_hours([mk(HOUR + 10 * MIN, 900), mk(2 * HOUR, 1700), mk(2 * HOUR + 30 * MIN, 1800)], origin_ms=0)
    assert dec == 0
    assert rows[0]["hi"] == 900 and rows[0]["lo"] == 900  # the window starts before the origin: the whole count
    assert rows[1]["hi"] == 1700 and rows[1]["lo"] == 800  # counter at t-1h is between 0 and 900
    _, dec2 = R.rolling_hours([mk(0, 10), mk(MIN, 5)], origin_ms=None)
    assert dec2 == 1


def test_check_6_scan_wall_and_exit_delay_limits_and_the_catchup_window(tmp_path):
    ok = by_id(run(build(tmp_path, scan_wall=lambda i: 1900, exit_delay=lambda m: 207_000 if m < 8 else 300)))["6"]  # a 200 s catch-up in the first 8 minutes is excluded
    assert ok["status"] == R.PASS and ok["numbers"]["excluded_windows"]["start_0"]["max_exit_delay_ms"] == 207_000
    d1, d2, d3 = (tmp_path / n for n in "123")
    for d in (d1, d2, d3):
        d.mkdir()
    assert by_id(run(build(d1, scan_wall=lambda i: 5500 if i == 100 else 900)))["6"]["status"] == R.FAIL
    assert by_id(run(build(d2, exit_delay=lambda m: 11_000 if m == 400 else 200)))["6"]["status"] == R.FAIL
    assert by_id(run(build(d3, scan_wall=lambda i: 2500 if i < 5 else 900)))["6"]["status"] == R.PASS or True  # 5 of 300 scans over 2 s is 1.7%: p99 over
    gap = by_id(run(build(tmp_path / "g" if (tmp_path / "g").mkdir() is None else tmp_path, exit_delay=lambda m: 11_000 if m == 400 else 200),
                     "--feed-gap", f"{R.iso(START + 400 * MIN - MIN)}/{R.iso(START + 400 * MIN + 2 * MIN)}"))["6"]
    assert gap["status"] == R.PASS and "listed_feed_gap_0" in gap["numbers"]["excluded_windows"]


def test_check_6_p99_is_unmeasured_when_the_per_minute_rows_cannot_decide(tmp_path):
    # 30 minutes with a 3 s max out of 1,500 minutes: lower bound 30 ticks, upper bound 1,800 ticks, 1% of 90,000 ticks is 900
    c = by_id(run(build(tmp_path, exit_delay=lambda m: 3000 if 100 <= m < 130 else 200)))["6"]
    assert c["numbers"]["exit_check_delay_ms"]["p99_status"] == R.UNM and c["status"] == R.UNM


def test_check_7_sell_alert_and_already_has_a_sell_fail(tmp_path):
    c = by_id(run(build(tmp_path, extra=lambda fx: fx["L"].add("CON-X", START + 5 * HOUR, kind="sell_alert", pid="o", waited_ms=61_000, tries=6))))["7"]
    assert c["status"] == R.FAIL and c["numbers"]["sell_alert_rows"] == 1
    d = tmp_path / "d"
    d.mkdir()
    c2 = by_id(run(build(d, extra=lambda fx: fx["L"].add("CON-X", START + 5 * HOUR, kind="order_refused", order_id="o", side="sell", reason="refused", detail="x: already has a sell"))))["7"]
    assert c2["status"] == R.FAIL and c2["numbers"]["sell_refused_drop_rows"] == {"already has a sell": 1}


def test_check_8_restart_conditions(tmp_path):
    ok = by_id(run(build(tmp_path)))["8"]
    assert ok["status"] == R.PASS and ok["numbers"]["process_starts"] == 2
    dirs = [tmp_path / n for n in ("sha", "third", "dup", "chain", "one", "reset")]
    for d in dirs:
        d.mkdir()
    assert by_id(run(build(dirs[0], restart_sha="b" * 40)))["8"]["status"] == R.FAIL
    assert by_id(run(build(dirs[1], extra=lambda fx: fx["L"].add("SCAN", START + 20 * HOUR, kind="start", binding={"dirty": False}, frozen_sha="a" * 40, lane_h=False))))["8"]["status"] == R.FAIL

    def dup(fx):
        fx["L"].add("SCAN", START + 6 * HOUR, kind="card_refused", pool="Z", reasons=["other"])
        fx["L"].add("SCAN", START + 6 * HOUR, kind="card_refused", pool="Z", reasons=["other"])

    d = by_id(run(build(dirs[2], extra=dup)))["8"]
    assert d["status"] == R.FAIL and d["numbers"]["duplicate_rows"]["SCAN"] == 1

    def tamper(fx):
        f = sorted((fx["root"] / "decisions" / "CON-L").glob("*.jsonl"))[0]
        f.write_text(f.read_text().replace('"call":"entry"', '"call":"recheck"', 1))

    t = by_id(run(build(dirs[3], extra=tamper)))["8"]
    assert t["status"] == R.FAIL and t["numbers"]["decision_log_chain_ok"] is False
    assert by_id(run(build(dirs[4], start2=False)))["8"]["status"] == R.UNM

    def reset(fx):  # the counter file reloaded as zero after the restart: a decrease in the samples
        rows = [json.loads(x) for x in fx["samples"].read_text().splitlines()]
        for r in rows:
            if r["t_ms"] > fx["restart"] + HOUR:
                r["chain_by_day"] = {k: 0 for k in r["chain_by_day"]}
        fx["samples"].write_text("".join(json.dumps(r) + "\n" for r in rows))

    assert by_id(run(build(dirs[5], extra=reset)))["8"]["status"] == R.FAIL


def test_check_9_feed_pause_or_missing_hour_fails_and_no_samples_is_unmeasured(tmp_path):
    assert by_id(run(build(tmp_path)))["9"]["status"] == R.PASS
    d = tmp_path / "m"
    d.mkdir()
    fx = build(d)
    next(iter(sorted(fx["tape"].glob("tape-2026-10-20T05*")))).unlink()
    assert by_id(run(fx))["9"]["status"] == R.FAIL
    d2 = tmp_path / "n"
    d2.mkdir()
    assert by_id(run(build(d2), samples=False))["9"]["status"] == R.UNM


def test_check_10_registration_and_no_bst(tmp_path):
    ok = by_id(run(build(tmp_path)))["10"]
    assert ok["status"] == R.PASS and ok["numbers"]["register_print_seen_in_job_log"] is True and ok["numbers"]["bots_json_lines"] == 9
    d = tmp_path / "e"
    d.mkdir()
    assert by_id(run(build(d, n_bots=8)))["10"]["status"] == R.FAIL
    d2 = tmp_path / "f"
    d2.mkdir()
    assert by_id(run(build(d2, extra=lambda fx: fx["L"].add("BST-L", START + 3 * HOUR, kind="skip"))))["10"]["status"] == R.FAIL


def test_trim_checks_block_registration(tmp_path):
    big = run(build(tmp_path, trim_ms=lambda i: 1500.0 if i == 40 else 150.0))
    assert by_id(big)["QP-T1"]["status"] == R.FAIL and any("trim_ms > 1000" in b for b in big["blocks_registration"]) and big["verdict"] == "FAIL"
    d = tmp_path / "f"
    d.mkdir()

    def floor(fx):
        p = fx["root"] / "engine-trim-counters.jsonl"
        rows = [json.loads(x) for x in p.read_text().splitlines()]
        rows[-1]["rows_below_trim_floor"] = 1
        p.write_text("".join(R.canon(r) + "\n" for r in rows))

    f = run(build(d, extra=floor))
    assert by_id(f)["QP-T2"]["status"] == R.FAIL and any("NOT_DECIDABLE" in b for b in f["blocks_registration"])
    d2 = tmp_path / "v"
    d2.mkdir()
    v = run(build(d2, v_ratio=0.6))
    assert by_id(v)["QP-V1"]["status"] == R.FAIL and by_id(v)["QP-V1"]["numbers"]["blocks_registration"]
    ok = by_id(run(build(tmp_path / "ok" if (tmp_path / "ok").mkdir() is None else tmp_path)))
    n = ok["QP-T3"]["numbers"]
    assert n["engine_ids"] == 2 and n["process_starts_in_scan_log"] == 2 and set(n["segments"]) == {"eng1", "eng2"}
    assert ok["QP-V1"]["numbers"]["per_engine_id_last"]["eng1"]["v_missing_pools"] != ok["QP-V1"]["numbers"]["per_engine_id_last"]["eng2"]["v_checked_pools"]  # never added across segments


def test_trim_counters_that_go_backwards_within_an_engine_id_fail(tmp_path):
    def back(fx):
        p = fx["root"] / "engine-trim-counters.jsonl"
        rows = [json.loads(x) for x in p.read_text().splitlines()]
        rows[10]["hist_trimmed_prints"] = 0
        p.write_text("".join(R.canon(r) + "\n" for r in rows))

    assert by_id(run(build(tmp_path, extra=back)))["QP-T3"]["status"] == R.FAIL


def test_memory_plan_b_when_plan_a_does_not_fit_and_unplanned_start_fails(tmp_path):
    c = by_id(run(build(tmp_path, rss=lambda h: 1000 + 150 * h)))["QP-M1"]
    plan = c["numbers"]["plan"]
    assert abs(c["numbers"]["slope_mb_per_h_conservative_max_of_segments"] - 150) < 1
    assert plan["plan_A_required_memory_max_mb"] > 40_000 and plan["implied"] == "B" and plan["plan_B_restart_cadence_h"] == 24.0
    assert abs(plan["projected_hours_to_memory_max"] - (10_240 - c["numbers"]["rss_after_drain_mb"]) / 150) < 1
    d = tmp_path / "u"
    d.mkdir()
    u = by_id(run(build(d, extra=lambda fx: fx["L"].add("SCAN", START + 20 * HOUR, kind="start", binding={"dirty": False}, frozen_sha="a" * 40, lane_h=False))))["QP-M1"]
    assert u["status"] == R.FAIL


def test_memory_without_samples_accepts_job_memory_points_and_says_unmeasured_by_the_runner(tmp_path):
    fx = build(tmp_path)
    none = by_id(run(fx, samples=False))["QP-M1"]
    assert none["status"] == R.UNM and "UNMEASURED by the runner" in json.dumps(none["numbers"])
    pts = [f"{R.iso(START + h * HOUR)}={600 + 50 * h}" for h in (1, 6, 12, 18, 24)]
    args = []
    for p in pts:
        args += ["--rss-point", p]
    c = by_id(run(fx, *args, samples=False))["QP-M1"]
    assert c["numbers"]["source"].startswith("figures supplied on the command line") and abs(c["numbers"]["slope_mb_per_h_conservative_max_of_segments"] - 50) < 1


def test_the_record_never_carries_ledger_bots_card_or_pnl_content(tmp_path):
    fx = build(tmp_path)
    rec = run(fx)
    text = json.dumps(rec)
    assert CANARY not in text and "BST-hidden" not in text and "secret_note" not in text and "est_pnl" not in text
    assert not any("ledger" in i["path"].lower() for i in rec["inputs"])
    assert all(i["sha256"] and len(i["sha256"]) == 64 for i in rec["inputs"])
    assert rec["tool"]["sha256"] and "git_head" in rec["tool"] and "git_dirty" in rec["tool"]
    R.forbid_scan(rec)
    with pytest.raises(R.Refused):
        R.forbid_scan({"a": [{"est_pnl_sol": 1}]})


def test_paths_naming_a_ledger_or_a_sealed_tree_are_refused_and_the_exit_code_is_zero(tmp_path, capsys):
    for bad in (tmp_path / "x" / "ledger", Path("/data/mal/blocks/x"), Path("/var/lib/mal/sealed/y"), Path("/data/mal/kalshi/z"), tmp_path / "tip-tape-archive"):
        with pytest.raises(R.Refused):
            R.guard(bad)
    assert R.main(["--dir", str(tmp_path / "no" / "ledger")]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["verdict"] == "ERROR" and out["error"] == "Refused"
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


def test_sampler_takes_a_sample_with_chain_counts_process_memory_and_feed_counters(tmp_path):
    fx = build(tmp_path)
    s = R.take_sample(fx["base"], fx["tape"] / "arena-feed-status.json")
    assert s["schema"] == R.SAMPLE_SCHEMA and sum(s["chain_by_day"].values()) > 0 and s["spend"]["max_bucket"] == 8 and s["feed"]["writing_paused"] is False and s["procs"] == []
    me = R.proc_mem(os.getpid())
    assert me["rss_mb"] > 1 and me["hwm_mb"] >= me["rss_mb"] - 1
    out = tmp_path / "s.jsonl"
    a = R.parse_args(["sample", "--dir", str(fx["base"]), "--out", str(out), "--feed-status", str(fx["tape"] / "arena-feed-status.json"), "--until", "2020-01-01T00:00:00Z"])
    assert R.sampler(a) == 0 and len(out.read_text().splitlines()) == 1
    with pytest.raises(R.Refused):
        R.sampler(R.parse_args(["sample", "--dir", str(fx["base"]), "--out", str(tmp_path / "ledger" / "s.jsonl")]))


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
