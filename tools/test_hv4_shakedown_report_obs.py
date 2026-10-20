"""Tests for revision 3 (SHAKEDOWN-CHECKS-3, sha256 55fbba05...) of tools/hv4_shakedown_report.py: rule M and checks 1, 3, 5, 8, 11 from hv4-obs.jsonl.

The obs files are written with the real writer (tools.hv4_obs.ObsLog; a subclass sets the pid and the wall clock, which a real run takes from the process), so the row
schema (`hv4_obs_v1`, the canonical JSON line, the `schema` / `wall_ms` envelope) is the real one. The decision logs, cards and the rest of the shakedown root come from the
revision 2 fixture builder. Nothing here is a real pool, trade or price; the pool ids below are synthetic and must never reach the output.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools import hv4_obs as O
from tools import hv4_shakedown_report as R
from tools.test_hv4_shakedown_report import HOUR, MIN, START, build, by_id, sub

A, B = 111, 222  # the pids of the two processes
RESTART_MIN = 5  # the restart is 12 h 5 min after the first start row (the checks text: cancel between HH:02Z and HH:10Z)
W_END = START + 24 * HOUR
DAY = 86_400_000
SHA = "a" * 40
POOL_IDS = ("UNAVPOOL-x7", "TAGPOOL-q3", "STALEPOOL-z9", "OTHERPOOL-k2")  # synthetic ids that appear in the obs file and the decision logs; none may reach the output


class FakeObs(O.ObsLog):
    """The real ObsLog with a pid and a wall clock set by the test (a real run takes both from the process)."""

    def __init__(self, root: Path, pid: int | None) -> None:
        self.clock = 0
        self.pid = pid
        super().__init__(root, enabled=True, wall_ms=lambda: self.clock)

    def write_many(self, rows):
        return super().write_many([{"pid": self.pid, **r} for r in rows])  # a row's own pid wins: {"pid": None} writes a row with no usable pid


def floor_min(ms: int) -> int:
    return ms - ms % MIN


def obs_rows(fx: dict, calls=lambda m: 8, *, sha: str = SHA, w_end: int = W_END) -> list[dict]:
    """The rows of a healthy two-process run: pid A until the restart, pid B after it. `calls(m)` = chain calls in minute m since START (all processes, one counter state)."""
    r0 = fx["restart"]
    rmin = floor_min(r0)
    cm = lambda t: calls((t - START) // MIN)  # noqa: E731
    a_minutes = {t: cm(t) for t in range(START, rmin, MIN) if cm(t) > 0}
    own = lambda pid, t: cm(t) if ((pid == A) == (t < rmin)) else 0  # noqa: E731
    own_cum = lambda pid, upto: sum(own(pid, t) for t in range(START, upto, MIN))  # noqa: E731
    day_calls = lambda upto, day_ms: sum(cm(t) for t in range(max(START, day_ms - day_ms % DAY), upto, MIN))  # noqa: E731
    rows: list[dict] = []

    def row(wall: int, pid: int, kind: str, **f) -> None:
        rows.append({"wall_ms": wall, "pid": pid, "kind": kind, **f})

    zero = dict(spend_state_reset=0, counter_calls_at_start=0, prior_minutes_error=None)
    row(START - 2000, A, "runner_start", frozen_sha=sha, binding={}, live=True)
    row(START - 1900, A, "prefetch_start", process_start_ms=START - 1900, policy="v2", hourly_budget=1500, spent_at_load=0, counter_day_calls_at_start=0, prior_minutes=[], prior_calls_total=0, **zero)
    lmax = max(a_minutes)  # the counter persists its last 61 buckets, ending at its last call's minute; a minute with no call is not in the list
    prior = [[t, c, c] for t, c in sorted(a_minutes.items()) if t >= lmax - 60 * MIN]
    row(r0 - 2000, B, "runner_start", frozen_sha=sha, binding={}, live=True)
    row(r0 - 1900, B, "prefetch_start", process_start_ms=r0 - 1900, policy="v2", hourly_budget=1500, spent_at_load=sum(c for t, c in a_minutes.items() if t >= rmin - 59 * MIN),
        counter_day_calls_at_start=day_calls(rmin, r0), prior_minutes=prior, prior_calls_total=sum(a_minutes.values()), **zero)
    k, h = 0, floor_min(START) // HOUR * HOUR + HOUR
    while h <= w_end:
        k += 1
        pid, hs = (A if h <= r0 else B), h - HOUR
        pm = [own(pid, hs + j * MIN) for j in range(60)]
        n = own_cum(pid, h)
        row(h + 5000, pid, "chain_hour", hour_start_ms=hs, hour_end_ms=h, process_start_ms=START if pid == A else r0, partial_since_ms=None, policy="v2", calls=sum(pm), credits=sum(pm),
            per_minute_calls=pm, counter_calls_total=n, counter_credits_total=n, counter_errors=0, counter_day_calls=day_calls(h, hs), counter_state_write_errors=0, spend_write_errors=0,
            spend_state_reset=0, budget_deferred=0, deferred_mint=0, deferred_hold=0, hourly_budget=1500, prefetch_stats={"calls": n})
        row(h + 1000, pid, "runner_hour", hour_start_ms=hs, hour_end_ms=h, hook_errors={}, n_hook_errors=0, n_housekeep_errors=0, counters={}, rss_mb=500.0 + k, obs_write_errors=0,
            obs_build_errors=0, obs_rows=100 * k)
        h += HOUR
    return rows


def put(fx: dict, rows: list[dict], name: str = O.OBS_FILE) -> Path:
    path = fx["root"] / name
    if path.exists():
        path.unlink()
    objs: dict = {}
    for r in sorted(rows, key=lambda r: r["wall_ms"]):
        o = objs.setdefault(r["pid"], FakeObs(fx["root"], r["pid"]))
        o.path = path
        o.clock = r["wall_ms"]
        o.write(r["kind"], **{k: v for k, v in r.items() if k not in ("wall_ms", "kind")})
    return path


def rows_of(fx: dict, kind: str | None = None) -> list[dict]:
    return [r for r in O.read_rows(fx["root"] / O.OBS_FILE) if kind is None or r["kind"] == kind]


def rewrite(fx: dict, fn) -> None:
    rows = O.read_rows(fx["root"] / O.OBS_FILE)
    rows = fn(rows) or rows
    (fx["root"] / O.OBS_FILE).write_text("".join(O.canon(r) + "\n" for r in rows))


def sel(rows: list[dict], kind: str, pid: int | None = None, hour_end: int | None = None) -> list[dict]:
    return [r for r in rows if r["kind"] == kind and (pid is None or r["pid"] == pid) and (hour_end is None or r.get("hour_end_ms") == hour_end)]


def fixture(tmp: Path, *, calls=lambda m: 8, mutate=None, obs: bool = True, start_pids=None, **kw) -> dict:
    kw.setdefault("restart_min", RESTART_MIN)
    fx = build(tmp, start_pids=start_pids, **kw)
    if obs:
        rows = obs_rows(fx, calls)
        if mutate:
            rows = mutate(rows, fx) or rows
        put(fx, rows)
    return fx


def rev3(fx: dict, *more: str, samples: bool = True, job: bool = True) -> dict:
    argv = ["--dir", str(fx["base"]), "--tape-dir", str(fx["tape"]), "--checks-rev", "3"]
    if samples:
        argv += ["--samples", str(fx["samples"])]
    if job:
        argv += ["--job-log", str(fx["job"]), "--job-log", str(fx["job2"])]
    argv += ["--read-hours", "168"]
    return R.build_report(R.parse_args(argv + list(more)))


def scan_t(i: int) -> int:
    return START + i * 5 * MIN + 2000


def pid_at(fx: dict, t: int) -> int:
    return A if t < fx["restart"] else B


def first_seen(pid: int, pool: str, scan_ms: int, entry: str = "first", **f) -> dict:
    return {"wall_ms": scan_ms + 100, "pid": pid, "kind": "first_seen_by_scanner", "pool": pool, "scan_ms": scan_ms, "now_ms": scan_ms + 100, "in_filter_pass": True, "n_filter_pass": 5,
            "entry": entry, "since_last_seen_ms": None if entry == "first" else 2_000_000, "hot": True, **f}


# two scans (30, before the restart; 200, after it) with two chain_unavailable cards each: a tagged pool (the fixture's tagged ids are P{i}a / P{i}b) and an untagged board pool
UNAV_TAGGED = lambda i: ([(f"P{i}a", ["chain_unavailable"]), (f"UNAVPOOL-x7-{i}", ["chain_unavailable"])] if i in (30, 200) else [])  # noqa: E731


def check3_rows(fx: dict, extra=None, gap_ms: int = 120_000) -> list[dict]:
    """first_seen rows for the four chain_unavailable cards of UNAV_TAGGED (two scans, a tagged and an untagged pool each)."""
    rows = obs_rows(fx)
    for i in (30, 200):
        t = scan_t(i)
        rows.append(first_seen(pid_at(fx, t), f"P{i}a", t - gap_ms, "first"))
        rows.append(first_seen(pid_at(fx, t), f"UNAVPOOL-x7-{i}", t - gap_ms, "re_entry"))
    if extra:
        rows = extra(rows) or rows
    return rows


def c3(tmp: Path, name: str, extra=None, gap_ms: int = 120_000) -> dict:
    fx = build(sub(tmp, name), restart_min=RESTART_MIN, refused=UNAV_TAGGED)
    put(fx, check3_rows(fx, extra, gap_ms))
    return by_id(rev3(fx))["3"]


# ------------------------------------------------------------------------------------------------------------------------------ the record, the pin, a healthy run
def test_a_healthy_two_process_run_passes_and_the_record_carries_the_pin_the_window_and_the_processes(tmp_path):
    fx = fixture(tmp_path, start_pids=(A, B))
    rec = rev3(fx)
    c = by_id(rec)
    assert {k: v["status"] for k, v in c.items() if v["status"] != R.PASS and not v["informational"]} == {}, {k: (v["status"], v["notes"]) for k, v in c.items() if v["status"] != R.PASS}
    assert rec["verdict"] == "PASS" and rec["complete"] and rec["checks_rev"] == 3 and rec["rule_m"]["fired"] is False and rec["verdict_reason"] is None
    assert rec["checks_pin"] == {"name": "SHAKEDOWN-CHECKS-3.md", "sha256": "55fbba0559327c1e18728596506de11fdf0408ae9bedcee49a21966d9314a3af",
                                 "supersedes_sha256": "af5052d5bae4efb683d745ed0fbb9aa6c680bc44a79ddfaf3cef7c3110831b18"}
    assert R.CHECKS3_SHA256 == rec["checks_pin"]["sha256"] and len(R.CHECKS3_SHA256) == 64
    assert rec["window"]["w_start"] == R.iso(START) and rec["window"]["w_end"] == R.iso(W_END) and rec["window"]["w_hours"] == 24
    assert [p["pid"] for p in rec["processes"]] == [A, B] and [p["join"] for p in rec["processes"]] == ["pid", "pid"] and rec["rule_m"]["join_used"] == {"pid": 2}
    assert rec["obs"]["present"] and rec["obs"]["path"].endswith("hv4-obs.jsonl") and any(i["path"].endswith("hv4-obs.jsonl") and len(i["sha256"]) == 64 for i in rec["inputs"])
    assert c["5S"]["informational"] and c["11"]["status"] == R.PASS  # the sampler-based check 5 is kept as an informational cross-check
    assert "5S" not in {x["id"] for x in rec["checks"]}
    text = R.markdown(rec)
    assert "checks revision 3" in text and "rule M: no condition fired" in text


def test_the_default_revision_is_3_and_revision_2_has_no_revision_3_keys(tmp_path):
    fx = fixture(tmp_path)
    a = R.parse_args(["--dir", str(fx["base"])])
    assert a.checks_rev == 3 and a.obs is None
    rec2 = R.build_report(R.parse_args(["--dir", str(fx["base"]), "--tape-dir", str(fx["tape"]), "--samples", str(fx["samples"]), "--expect-end", R.iso(fx["end"]), "--checks-rev", "2",
                                        "--obs", str(fx["root"] / O.OBS_FILE)]))
    assert set(rec2) == {"schema", "generated", "dir", "verdict", "complete", "window", "blocks_registration", "unmeasured_checks", "checks", "registration_conditions", "per_persona_counts",
                         "startup_and_feed_gap_windows", "statuses", "inputs", "torn_tail_files", "unreadable_decision_files", "skipped_dirs", "sampler", "job_logs", "tool", "scope"}
    assert set(rec2["window"]) == {"first_start", "last_decision_row", "expect_end", "hours_covered"}
    assert [c["id"] for c in rec2["checks"]] == [str(i) for i in range(1, 11)] and "5S" not in {c["id"] for c in rec2["registration_conditions"]}
    assert not any(i["path"].endswith("hv4-obs.jsonl") for i in rec2["inputs"])  # revision 2 never opens the obs file
    with pytest.raises(SystemExit):
        R.parse_args(["--dir", "x", "--checks-rev", "4"])


def test_the_obs_path_goes_through_the_path_policy_and_an_explicit_path_is_read(tmp_path, capsys):
    fx = fixture(tmp_path, obs=False)
    elsewhere = put(fx, obs_rows(fx), name="elsewhere.jsonl")
    rec = rev3(fx, "--obs", str(elsewhere))
    assert rec["obs"]["present"] and rec["obs"]["path"] == str(elsewhere) and rec["rule_m"]["fired"] is False
    with pytest.raises(R.Refused):
        rev3(fx, "--obs", "/data/mal/x/ledger/hv4-obs.jsonl")
    assert R.main(["--dir", str(fx["base"]), "--obs", "/data/mal/blocks/hv4-obs.jsonl"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["error"] == "Refused"


def test_no_pool_id_reaches_the_output_and_the_json_and_markdown_are_free_of_price_or_pnl_keys(tmp_path):
    def more(rows, fx):
        t = scan_t(40)
        rows.append({"wall_ms": t + 50, "pid": A, "kind": "chain_stale_served", "pool": "STALEPOOL-z9", "scan_ms": t, "age_ms": 400_000, "hot": True, "n_mints": 1, "fails_mint": 0,
                     "next_try_in_ms": 0, "pool_age_in_store_ms": 1})
        rows.append(first_seen(A, "OTHERPOOL-k2", t - 1000))
        rows.extend(r for r in check3_rows(fx) if r["kind"] == "first_seen_by_scanner")

    fx = fixture(tmp_path, mutate=more, refused=lambda i: ([("STALEPOOL-z9", ["chain_stale"])] if i == 40 else []) + UNAV_TAGGED(i))
    rec = rev3(fx)
    assert by_id(rec)["3"]["status"] == R.PASS and by_id(rec)["3"]["numbers"]["chain_unavailable_cards"] == 4
    blob = json.dumps(rec) + R.markdown(rec)
    for pool in POOL_IDS + ("P30a", "P200a", "UNAVPOOL", "TAGPOOL"):
        assert pool not in blob, pool
    R.forbid_scan(rec)
    assert rec["also_reported_from_obs"]["chain_stale_served"]["hot"]["n"] == 1


# ------------------------------------------------------------------------------------------------------------------------------ rule M
def rule_m(tmp: Path, name: str, mutate, **kw) -> tuple[dict, dict]:
    fx = fixture(sub(tmp, name), mutate=mutate, **kw)
    rec = rev3(fx)
    return rec, by_id(rec)


def assert_m(rec: dict, c: dict, label: str) -> None:
    assert rec["rule_m"]["fired"] and label in rec["rule_m"]["fired_conditions"], rec["rule_m"]["fired_conditions"]
    for cid in ("1", "3", "5", "8"):
        assert c[cid]["status"] == R.UNM and label in c[cid]["numbers"]["rule_m_fired"] and "rule M" in c[cid]["notes"][0]
    assert rec["rule_m"]["checks_made_unmeasured"] == ["1", "3", "5", "8"]
    assert c["2"]["status"] == R.PASS  # a check that is not measured from the obs file is untouched
    assert rec["complete"] and rec["verdict"] == "FAIL" and label in rec["verdict_reason"] and "1, 3, 5, 8" in rec["verdict_reason"]  # UNMEASURED fails


def test_rule_m_condition_1_a_runner_hour_row_with_obs_write_errors(tmp_path):
    def m(rows, fx):
        sel(rows, "runner_hour", B)[3]["obs_write_errors"] = 1

    rec, c = rule_m(tmp_path, "m1", m)
    assert_m(rec, c, "M1_runner_hour_obs_write_errors")
    assert rec["rule_m"]["conditions"]["M1_runner_hour_obs_write_errors"]["rows"] == 1
    # a row from outside W is not in the rule: the hour that closes after W
    def outside(rows, fx):
        rows.append({"wall_ms": W_END + HOUR + 1000, "pid": B, "kind": "runner_hour", "hour_start_ms": W_END, "hour_end_ms": W_END + HOUR, "hook_errors": {}, "n_housekeep_errors": 0,
                     "rss_mb": 1.0, "obs_write_errors": 5, "obs_build_errors": 0, "obs_rows": 1})

    rec2, c2 = rule_m(tmp_path, "m1b", outside)
    assert rec2["rule_m"]["fired"] is False and rec2["verdict"] == "PASS"


@pytest.mark.parametrize("kind,pid,which", [("chain_hour", A, -1), ("runner_hour", B, 5), ("chain_hour", B, -1), ("runner_hour", A, 0)])
def test_rule_m_condition_2_an_hour_that_closed_in_a_processs_life_has_no_row_from_it(tmp_path, kind, pid, which):
    def m(rows, fx):
        victim = sel(rows, kind, pid)[which]
        rows.remove(victim)

    rec, c = rule_m(tmp_path, f"m2{kind}{pid}", m)
    assert_m(rec, c, "M2_hour_without_chain_hour_or_runner_hour_row")
    cond = rec["rule_m"]["conditions"]["M2_hour_without_chain_hour_or_runner_hour_row"]
    assert (cond["chain_hour_missing"], cond["runner_hour_missing"]) == ((1, 0) if kind == "chain_hour" else (0, 1)) and len(cond["missing_hours"]) == 1


def test_rule_m_condition_2_counts_the_last_hour_of_w_for_the_last_process_and_the_hour_of_the_restart_for_the_first(tmp_path):
    # the end rule: the first hour that closes >= 24 h after the first start row needs both rows from the process that was alive then
    def drop_end(rows, fx):
        for r in sel(rows, "runner_hour", B, W_END):
            rows.remove(r)

    rec, c = rule_m(tmp_path, "end", drop_end)
    assert_m(rec, c, "M2_hour_without_chain_hour_or_runner_hour_row")
    assert rec["rule_m"]["conditions"]["M2_hour_without_chain_hour_or_runner_hour_row"]["missing_hours"] == [R.iso(W_END)]
    # the first process's last row is the hour that ends at 10:00, before the restart at 10:05
    assert sel(rows_of(fixture(sub(tmp_path, "rs"))), "chain_hour", A)[-1]["hour_end_ms"] == START + 12 * HOUR


@pytest.mark.parametrize("what", ["no_runner_start", "no_prefetch_start", "ambiguous_runner_start", "two_prefetch_start"])
def test_rule_m_condition_3_a_start_row_needs_runner_start_and_prefetch_start_from_the_same_pid(tmp_path, what):
    def m(rows, fx):
        if what == "no_runner_start":
            rows.remove(sel(rows, "runner_start", B)[0])
        elif what == "no_prefetch_start":
            rows.remove(sel(rows, "prefetch_start", B)[0])
        elif what == "ambiguous_runner_start":  # a second process wrote a runner_start between the two start rows and never a start row of its own
            rows.append({"wall_ms": fx["restart"] - 5000, "pid": 333, "kind": "runner_start", "frozen_sha": SHA, "binding": {}, "live": True})
        else:
            rows.append({**sel(rows, "prefetch_start", B)[0], "wall_ms": fx["restart"] - 1800})

    rec, c = rule_m(tmp_path, what, m)
    assert_m(rec, c, "M3_start_row_without_runner_start_or_prefetch_start_from_the_same_pid")


def test_rule_m_condition_3_the_prefetch_start_must_have_the_pid_of_the_runner_start(tmp_path):
    def m(rows, fx):
        sel(rows, "prefetch_start", B)[0]["pid"] = 999  # a prefetch_start from another pid does not count for pid B

    rec, c = rule_m(tmp_path, "pf", m)
    assert_m(rec, c, "M3_start_row_without_runner_start_or_prefetch_start_from_the_same_pid")
    assert rec["rule_m"]["conditions"]["M3_start_row_without_runner_start_or_prefetch_start_from_the_same_pid"]["no_prefetch_start"] == [1]


def test_rule_m_condition_4_counter_state_write_errors_in_the_hour_before_the_restart(tmp_path):
    def m(rows, fx):
        sel(rows, "chain_hour", A)[-1]["counter_state_write_errors"] = 1

    rec, c = rule_m(tmp_path, "m4", m)
    assert_m(rec, c, "M4_counter_state_write_errors_before_a_restart")
    # the same error in the second process is not rule M 4 (it is not before a restart); check 5 reports it as an expected-0 counter
    rec2, c2 = rule_m(sub(tmp_path, "m4b"), "x", lambda rows, fx: sel(rows, "chain_hour", B)[-1].update(counter_state_write_errors=1))
    assert "M4_counter_state_write_errors_before_a_restart" not in rec2["rule_m"]["fired_conditions"] and c2["5"]["status"] == R.FAIL


def test_a_missing_obs_file_is_rule_m_and_a_completed_window_fails_while_an_unfinished_one_is_incomplete(tmp_path):
    fx = fixture(sub(tmp_path, "none"), obs=False)
    rec = rev3(fx)
    c = by_id(rec)
    assert rec["obs"]["present"] is False and rec["rule_m"]["fired"] and rec["verdict"] == "FAIL" and all(c[k]["status"] == R.UNM for k in ("1", "3", "5", "8"))
    short = fixture(sub(tmp_path, "short"), obs=False, hours=13)  # the stub has not reached the end of W: nothing is decided
    rs = rev3(short)
    assert rs["complete"] is False and rs["rule_m"]["fired"] and rs["verdict"] == "INCOMPLETE" and rs["verdict_reason"] is None


def test_extra_guards_a_pid_in_two_runner_start_rows_and_an_orphan_runner_start_are_unmeasured(tmp_path):
    rec, c = rule_m(tmp_path, "g", lambda rows, fx: rows.append({"wall_ms": START - 1000, "pid": A, "kind": "runner_start", "frozen_sha": SHA, "binding": {}, "live": True}))
    assert rec["rule_m"]["fired"] and rec["rule_m"]["conditions"]["guards_not_in_the_checks_text"]["fired"] and c["5"]["status"] == R.UNM
    rec2, _ = rule_m(sub(tmp_path, "g2"), "o", lambda rows, fx: rows.append({"wall_ms": W_END + MIN, "pid": 444, "kind": "runner_start", "frozen_sha": SHA, "binding": {}, "live": True}))
    assert rec2["rule_m"]["conditions"]["guards_not_in_the_checks_text"]["runner_start_rows_without_a_start_row"] == 1


# ------------------------------------------------------------------------------------------------------------------------------ the join of start rows to pids
def test_start_rows_with_a_pid_are_joined_on_it_and_the_join_used_is_named(tmp_path):
    fx = fixture(sub(tmp_path, "p"), start_pids=(A, B))
    rec = rev3(fx)
    assert rec["rule_m"]["join_used"] == {"pid": 2} and rec["verdict"] == "PASS"
    # a start row whose pid has no runner_start row: rule M 3, whatever the times say
    bad = fixture(sub(tmp_path, "q"), start_pids=(A, 999))
    r2 = rev3(bad)
    assert r2["rule_m"]["conditions"]["M3_start_row_without_runner_start_or_prefetch_start_from_the_same_pid"]["no_runner_start"] == [1] and r2["verdict"] == "FAIL"
    # start rows without a pid (a replay, an old log): the time-order join, named as such
    old = rev3(fixture(sub(tmp_path, "t")))
    assert old["rule_m"]["join_used"] == {"time_order": 2} and [p["join"] for p in old["processes"]] == ["time_order", "time_order"] and old["verdict"] == "PASS"
    # one pid behind two start rows is ambiguous
    dup = rev3(fixture(sub(tmp_path, "d"), start_pids=(A, A)))
    assert dup["rule_m"]["fired"] and dup["verdict"] == "FAIL"


def test_a_time_order_join_takes_the_runner_start_nearest_before_the_start_row_and_after_the_previous_one(tmp_path):
    fx = fixture(sub(tmp_path, "a"))
    L = R.load_logs(fx["root"], [])
    Ob = R.load_obs(fx["root"] / O.OBS_FILE, [])
    procs, extra = R.join_processes(L, Ob)
    assert [p["pid"] for p in procs] == [A, B] and extra["join_used"] == {"time_order": 2} and all(p["n_runner_start_candidates"] == 1 for p in procs)
    # a runner_start that precedes the previous start row belongs to that one, not to this: the second process has none when the only B row is moved before START
    rewrite(fx, lambda rows: [dict(r, wall_ms=START - 500) if (r["kind"] == "runner_start" and r["pid"] == B) else r for r in rows])
    Ob2 = R.load_obs(fx["root"] / O.OBS_FILE, [])
    procs2, _ = R.join_processes(L, Ob2)
    assert procs2[0]["n_runner_start_candidates"] == 2 and procs2[1]["pid"] is None


# ------------------------------------------------------------------------------------------------------------------------------ check 1 and check 11
def test_check_1_reads_leakage_error_from_hook_errors_cumulative_per_pid_and_reports_the_uncovered_minutes(tmp_path):
    ok = by_id(rev3(fixture(sub(tmp_path, "ok"))))["1"]
    n = ok["numbers"]
    assert ok["status"] == R.PASS and n["hook_errors_LeakageError_max_over_runner_hour_rows"] == 0
    un = {u["process"]: u for u in n["uncovered_minutes_after_the_last_runner_hour_row"]}
    assert un[0]["pid"] == A and un[0]["uncovered_minutes"] == RESTART_MIN and un[0]["last_runner_hour_row_hour_end"] == R.iso(START + 12 * HOUR)  # 10:00 to the 10:05 restart
    assert un[1]["pid"] == B and un[1]["uncovered_minutes"] >= 1 and un[1]["last_runner_hour_row_hour_end"] == R.iso(W_END)

    def leak(rows, fx):
        for r in sel(rows, "runner_hour", B)[10:]:  # cumulative per pid: once counted it stays in every later row
            r["hook_errors"] = {"LeakageError": 1}

    bad = by_id(rev3(fixture(sub(tmp_path, "bad"), mutate=leak)))
    assert bad["1"]["status"] == R.FAIL and bad["1"]["numbers"]["hook_errors_LeakageError_max_over_runner_hour_rows"] == 1 and bad["1"]["numbers"]["hook_errors_LeakageError_last_row_per_pid"] == {str(A): 0, str(B): 1}
    assert bad["11"]["status"] == R.PASS  # LeakageError is check 1's, not check 11's
    ar = by_id(rev3(fixture(sub(tmp_path, "ar"), extra=lambda fx: fx["L"].add("SCAN", fx["end"] + 5, kind="as_of_refusal", pool="p", detail="LeakageError: chain"))))["1"]
    assert ar["status"] == R.FAIL and ar["numbers"]["as_of_refusal_rows"] == 1 and ar["numbers"]["leakage_error_mentions_in_decision_logs"] == 1
    # a runner_hour row whose hook_errors cannot be read leaves the check unmeasured
    unread = by_id(rev3(fixture(sub(tmp_path, "un"), mutate=lambda rows, fx: sel(rows, "runner_hour", A)[2].update(hook_errors="boom"))))
    assert unread["1"]["status"] == R.UNM and unread["11"]["status"] == R.UNM


def test_check_11_other_hook_errors_fail_and_housekeep_errors_are_reported_per_hour_differenced(tmp_path):
    def m(rows, fx):
        for i, r in enumerate(sel(rows, "runner_hour", B)):
            if i >= 4:
                r["hook_errors"] = {"housekeep:OSError": 2, "KeyError": 1} if i < 8 else {"housekeep:OSError": 3, "KeyError": 1}
                r["n_housekeep_errors"] = 2 if i < 8 else 3
                r["n_hook_errors"] = 3 if i < 8 else 4

    rec = rev3(fixture(tmp_path, mutate=m))
    c = by_id(rec)["11"]
    assert c["status"] == R.FAIL and c["numbers"]["max_other_hook_errors_in_a_row"] == 4 and c["numbers"]["other_hook_errors_by_type_last_row_per_pid_summed"] == {"housekeep:OSError": 3, "KeyError": 1}
    hourly = rec["also_reported_from_obs"]["runner_hour_per_hour"]
    b_rows = [h for h in hourly if h["pid"] == B]
    assert b_rows[4]["housekeep_errors_delta"] == 2 and b_rows[4]["hook_errors_delta_by_type"] == {"housekeep:OSError": 2, "KeyError": 1}
    assert b_rows[5]["housekeep_errors_delta"] == 0 and b_rows[5]["hook_errors_delta_by_type"] == {} and b_rows[8]["housekeep_errors_delta"] == 1
    assert [h["rss_mb"] for h in b_rows][:2] == [513.0, 514.0]  # rss_mb per hour (QP-M1)
    assert rec["also_reported_from_obs"]["obs_rows_in_file"]["by_kind"]["runner_hour"] == 24 and rec["verdict"] == "FAIL"


# ------------------------------------------------------------------------------------------------------------------------------ check 3
def test_check_3_pass_counts_entries_gaps_and_setup_tags(tmp_path):
    c = c3(tmp_path, "pass")
    n = c["numbers"]
    assert c["status"] == R.PASS and n["chain_unavailable_cards"] == 4 and n["cards_carrying_a_setup_tag"] == 2
    assert n["latest_first_seen_row_entry_counts"] == {"first": 2, "re_entry": 2, "other": 0}  # a re-entry counts
    assert n["t_minus_scan_ms_p50"] == 120_000 and n["t_minus_scan_ms_max"] == 120_000 and n["cards_with_no_first_seen_row_for_the_pool_and_pid"] == 0 and n["cards_by_persona"]["CON"] >= 2


def test_check_3_the_limit_is_600000_ms_inclusive(tmp_path):
    at = c3(tmp_path, "at", gap_ms=600_000)
    assert at["status"] == R.PASS and at["numbers"]["t_minus_scan_ms_max"] == 600_000
    over = c3(tmp_path, "over", gap_ms=600_001)
    assert over["status"] == R.FAIL and over["numbers"]["cards_with_t_minus_scan_ms_over_600000"] == 4


def test_check_3_a_card_without_a_first_seen_row_fails(tmp_path):
    def drop(rows):
        rows.remove(next(r for r in rows if r["kind"] == "first_seen_by_scanner" and r["pool"] == "UNAVPOOL-x7-200"))

    c = c3(tmp_path, "norow", drop)
    assert c["status"] == R.FAIL and c["numbers"]["cards_with_no_first_seen_row_for_the_pool_and_pid"] == 1 and c["numbers"]["latest_first_seen_row_entry_counts"]["first"] == 2


def test_check_3_a_row_from_another_pid_does_not_count(tmp_path):
    def other(rows):
        for r in rows:
            if r["kind"] == "first_seen_by_scanner" and r["pool"] == "P200a":
                r["pid"] = A if r["pid"] == B else B

    c = c3(tmp_path, "pid", other)
    assert c["status"] == R.FAIL and c["numbers"]["cards_with_no_first_seen_row_for_the_pool_and_pid"] == 1
    # the first process's row for a pool does not carry over a restart: pool seen by A at scan 30, refused again by B at scan 200 without a row of B's own
    def carry(rows):
        rows[:] = [r for r in rows if not (r["kind"] == "first_seen_by_scanner" and r["pool"] == "UNAVPOOL-x7-200")]
        rows.append(first_seen(A, "UNAVPOOL-x7-200", scan_t(200) - 1000))

    assert c3(tmp_path, "carry", carry)["status"] == R.FAIL


def test_check_3_a_row_after_the_scan_does_not_count_and_the_latest_row_at_or_before_it_does(tmp_path):
    def after(rows):
        rows[:] = [r for r in rows if not (r["kind"] == "first_seen_by_scanner" and r["pool"] == "P30a")]
        rows.append(first_seen(A, "P30a", scan_t(30) + 1000, "first"))

    c = c3(tmp_path, "after", after)
    assert c["status"] == R.FAIL and c["numbers"]["cards_with_no_first_seen_row_for_the_pool_and_pid"] == 1

    def two(rows):  # an old row, then a re-entry just before the scan: the latest one decides
        rows.append(first_seen(A, "P30a", scan_t(30) - 3 * HOUR, "first"))

    ok = c3(tmp_path, "two", two)
    assert ok["status"] == R.PASS and ok["numbers"]["t_minus_scan_ms_max"] == 120_000

    def same_scan(rows):  # a pool that enters the watch set at the scan itself: T - scan_ms = 0
        for r in rows:
            if r["kind"] == "first_seen_by_scanner" and r["pool"] == "P30a":
                r["scan_ms"] = scan_t(30)

    z = c3(tmp_path, "same", same_scan)
    assert z["status"] == R.PASS and z["numbers"]["t_minus_scan_ms_p50"] in (0, 120_000)


def test_check_3_a_row_without_a_pid_is_malformed_counted_and_the_check_is_unmeasured(tmp_path):
    def nopid(rows):
        next(r for r in rows if r["kind"] == "first_seen_by_scanner")["pid"] = None

    c = c3(tmp_path, "nopid", nopid)
    assert c["status"] == R.UNM and c["numbers"]["first_seen_rows_without_pid"] == 1
    # the counts by kind are in the obs section of the record
    fx = build(sub(tmp_path, "cnt"), restart_min=RESTART_MIN, refused=UNAV_TAGGED)
    put(fx, check3_rows(fx, nopid))
    rec = rev3(fx)
    assert rec["also_reported_from_obs"]["obs_rows_in_file"]["rows_without_pid_by_kind"] == {"first_seen_by_scanner": 1} and by_id(rec)["3"]["status"] == R.UNM
    # the file really has no pid on that row (not the writer's envelope)
    assert sum(1 for r in rows_of(fx, "first_seen_by_scanner") if not isinstance(r.get("pid"), int)) == 1


def test_check_3_with_no_chain_unavailable_card_passes_when_the_rows_are_sound(tmp_path):
    c = by_id(rev3(fixture(tmp_path)))["3"]
    assert c["status"] == R.PASS and c["numbers"]["chain_unavailable_cards"] == 0 and c["numbers"]["t_minus_scan_ms_p50"] is None


# ------------------------------------------------------------------------------------------------------------------------------ check 5
def crowded(m: int) -> int:
    """100 calls a minute from 10:00 to 10:04 (the first process, after its last chain_hour row) and 20 a minute for the rest of that hour (the second process): 500 + 1,100."""
    base = 12 * 60  # minute index of 10:00
    if base <= m < base + RESTART_MIN:
        return 100
    if base + RESTART_MIN <= m < base + 60:
        return 20
    return 8


def test_check_5_a_rolling_hour_over_1500_is_visible_only_when_both_processes_are_summed_across_the_restart(tmp_path):
    rec = rev3(fixture(tmp_path, calls=crowded))
    c = by_id(rec)["5"]
    n = c["numbers"]
    assert c["status"] == R.FAIL and n["rolling_hour_calls"]["max"] == 1600 and n["rolling_hour_calls"]["max_window_starts"] == R.iso(START + 12 * HOUR) and n["rolling_hour_calls"]["windows_over_limit"] >= 1
    x = n["hour_crossing_restart"][0]
    assert x["instant"] == R.iso(START + 12 * HOUR + (RESTART_MIN - 1) * MIN) and x["max_calls_both_processes_counted"] == 1600  # the minute the second process's runner_start was written in and x["within_limit"] is False
    assert x["max_calls_earlier_processes_only"] <= 1500 and x["max_calls_later_processes_only"] <= 1500  # neither process alone is over: the sum is
    assert n["restart_coverage"][0]["gap_minutes"] == 0 and n["restart_coverage"][0]["overlap_disagreements"] == 0 and n["restart_coverage"][0]["overlap_minutes_compared"] > 0
    assert rec["verdict"] == "FAIL"
    # without the second process's prior_minutes the first process's minutes after its last chain_hour row are lost: UNMEASURED (a gap), never a pass
    def lost(rows, fx):
        sel(rows, "prefetch_start", B)[0].update(prior_minutes=[], prior_calls_total=4000)

    g = by_id(rev3(fixture(sub(tmp_path, "lost"), calls=crowded, mutate=lost)))["5"]
    assert g["status"] == R.UNM and g["numbers"]["unmeasurable"]["gap_minutes_outside_every_record"] > 0


def test_check_5_the_second_process_alone_cannot_hide_the_first_processs_tail(tmp_path):
    c = by_id(rev3(fixture(tmp_path, calls=lambda m: 8)))["5"]
    n = c["numbers"]
    assert c["status"] == R.PASS and n["rolling_hour_calls"]["max"] == 480 and n["minutes_in_W_covered"] == 24 * 60
    r0 = n["hour_crossing_restart"][0]
    assert r0["windows"] == 60 and r0["max_calls_both_processes_counted"] == 480 and r0["within_limit"] is True
    assert r0["max_calls_earlier_processes_only"] <= 480 and r0["max_calls_later_processes_only"] <= 480


def test_check_5_prior_minutes_that_agree_with_the_first_processs_chain_hour_row_pass_and_a_disagreement_fails(tmp_path):
    ok = by_id(rev3(fixture(sub(tmp_path, "agree"))))["5"]
    cov = ok["numbers"]["restart_coverage"][0]
    assert ok["status"] == R.PASS and cov["overlap_minutes_compared"] == 56 and cov["overlap_disagreements"] == 0  # 09:04 (the start of the persisted window) to 09:59 are in both records
    assert cov["persisted_window"] == [R.iso(START + 12 * HOUR + (RESTART_MIN - 1) * MIN - 60 * MIN), R.iso(START + 12 * HOUR + (RESTART_MIN - 1) * MIN)]

    def differ(rows, fx):
        pm = sel(rows, "prefetch_start", B)[0]["prior_minutes"]
        next(x for x in pm if x[0] == START + 11 * HOUR + 30 * MIN)[1] = 9  # the first process's chain_hour row says 8 for 09:30

    bad = by_id(rev3(fixture(sub(tmp_path, "differ"), mutate=differ)))["5"]
    cb = bad["numbers"]["restart_coverage"][0]
    assert bad["status"] == R.FAIL and cb["overlap_disagreements"] == 1 and cb["overlap_disagreement_minutes"] == [{"minute": R.iso(START + 11 * HOUR + 30 * MIN), "prior_minutes": 9, "chain_hour_rows": 8}]

    def absent(rows, fx):  # an absent minute inside the persisted window counts as 0: the first process's row says 8 there
        pm = sel(rows, "prefetch_start", B)[0]["prior_minutes"]
        pm[:] = [x for x in pm if x[0] != START + 11 * HOUR + 40 * MIN]

    ab = by_id(rev3(fixture(sub(tmp_path, "absent"), mutate=absent)))["5"]
    assert ab["status"] == R.FAIL and ab["numbers"]["restart_coverage"][0]["overlap_disagreements"] == 1


def test_check_5_a_minute_without_calls_is_absent_from_prior_minutes_and_that_agrees_with_a_zero_in_the_row(tmp_path):
    quiet = lambda m: 0 if (m in (12 * 60 - 20, 12 * 60 + 2)) else 8  # noqa: E731 - 09:40 (in the overlap) and 10:02 (a tail minute) have no calls
    fx = fixture(tmp_path, calls=quiet)
    pm = sel(rows_of(fx), "prefetch_start", B)[0]["prior_minutes"]
    assert START + 12 * HOUR - 20 * MIN not in [x[0] for x in pm] and START + 12 * HOUR + 2 * MIN not in [x[0] for x in pm]
    c = by_id(rev3(fx))["5"]
    assert c["status"] == R.PASS and c["numbers"]["restart_coverage"][0]["overlap_disagreements"] == 0


def test_check_5_an_absent_minute_outside_the_window_between_the_last_row_and_the_window_is_a_gap(tmp_path):
    def far(rows, fx):  # a window that starts after the first process's last chain_hour row: the minutes between are in no record
        sel(rows, "prefetch_start", B)[0]["prior_minutes"] = [[START + 12 * HOUR + 4 * MIN, 8, 8], [START + 13 * HOUR + 3 * MIN, 8, 8]]

    c = by_id(rev3(fixture(tmp_path, mutate=far)))["5"]
    cov = c["numbers"]["restart_coverage"][0]
    assert c["status"] == R.UNM and cov["gap_minutes"] == 3 and c["numbers"]["unmeasurable"]["gap_minutes_outside_every_record"] == cov["gap_minutes"]
    for name, upd in (("err", {"prior_minutes_error": "minutes_malformed", "prior_minutes": []}), ("none", {"prior_minutes": None})):
        g = by_id(rev3(fixture(sub(tmp_path, name), mutate=lambda rows, fx, upd=upd: sel(rows, "prefetch_start", B)[0].update(upd))))["5"]
        assert g["status"] == R.UNM and g["numbers"]["restart_coverage"][0]["gap_minutes"] > 0


def test_check_5_the_hour_that_crosses_00_00Z_is_named_and_each_must_stay_within_1500(tmp_path):
    at_limit = lambda m: 25 if 100 <= m < 160 else 8  # noqa: E731 - 60 minutes of 25 = 1,500 exactly, across 00:00Z (minute 120)
    ok = by_id(rev3(fixture(sub(tmp_path, "ok"), calls=at_limit)))["5"]
    x = ok["numbers"]["hour_crossing_00_00Z"]
    assert ok["status"] == R.PASS and len(x) == 1 and x[0]["instant"] == "2026-10-20T00:00:00Z" and x[0]["windows"] == 59 and x[0]["max_calls_both_processes_counted"] == 1500 and x[0]["within_limit"] is True
    over = lambda m: 26 if 100 <= m < 160 else 8  # noqa: E731
    bad = by_id(rev3(fixture(sub(tmp_path, "bad"), calls=over)))["5"]
    assert bad["status"] == R.FAIL and bad["numbers"]["hour_crossing_00_00Z"][0]["max_calls_both_processes_counted"] == 1560 and bad["numbers"]["hour_crossing_00_00Z"][0]["within_limit"] is False


def test_check_5_utc_days_come_from_counter_day_calls_for_a_closed_day_and_the_minute_sum_for_an_open_one(tmp_path):
    c = by_id(rev3(fixture(tmp_path)))["5"]
    d = c["numbers"]["calls_by_utc_day"]
    assert d["2026-10-19"]["basis"].startswith("counter_day_calls") and d["2026-10-19"]["calls"] == 2 * 60 * 8 == d["2026-10-19"]["minute_sum_in_W"]
    assert d["2026-10-20"]["basis"].startswith("open day") and d["2026-10-20"]["calls"] == 22 * 60 * 8 and d["2026-10-20"]["limit"] == 40_000
    assert c["numbers"]["helius_credits_by_utc_day"] == {"2026-10-19": 960, "2026-10-20": 22 * 60 * 8}

    def big(rows, fx):  # the counter's own day count for the closed day is 40,001
        next(r for r in sel(rows, "chain_hour", A) if r["hour_end_ms"] == START + 2 * HOUR)["counter_day_calls"] = 40_001

    f = by_id(rev3(fixture(sub(tmp_path, "big"), mutate=big)))["5"]
    assert f["status"] == R.FAIL and f["numbers"]["failing"]["utc_days_over_40000"] == ["2026-10-19"] and f["numbers"]["calls_by_utc_day"]["2026-10-19"]["calls"] == 40_001
    # a closed day with no counter_day_calls is UNMEASURED, not computed from the minutes
    nd = by_id(rev3(fixture(sub(tmp_path, "nd"), mutate=lambda rows, fx: next(r for r in sel(rows, "chain_hour", A) if r["hour_end_ms"] == START + 2 * HOUR).update(counter_day_calls=None))))["5"]
    assert nd["status"] == R.UNM and nd["numbers"]["unmeasurable"]["closed_days_without_counter_day_calls"] == ["2026-10-19"]
    # an open day over 40,000 is the per-minute sum: 25 calls a minute (1,500 an hour) for the 22 h of 10-20 is 33,000, so the limit is only reachable with an hour over 1,500 as well
    hot = by_id(rev3(fixture(sub(tmp_path, "hot"), calls=lambda m: 40)))["5"]
    assert hot["status"] == R.FAIL and hot["numbers"]["failing"]["utc_days_over_40000"] == ["2026-10-20"] and hot["numbers"]["calls_by_utc_day"]["2026-10-20"]["calls"] == 22 * 60 * 40


def test_check_5_the_expected_zero_counters_and_the_no_limit_reports(tmp_path):
    c = by_id(rev3(fixture(tmp_path)))["5"]
    n = c["numbers"]
    z = n["expected_zero_counters"]
    assert z["spend_state_reset"]["prefetch_start_rows"] == [0, 0] and z["spend_state_reset"]["last_chain_hour_row_per_pid"] == {str(A): 0, str(B): 0}
    assert z["spend_write_errors_last_chain_hour_row_per_pid"] == {str(A): 0, str(B): 0} and z["counter_state_write_errors_last_chain_hour_row_per_pid"] == {str(A): 0, str(B): 0}
    assert n["largest_per_minute_count"] == {"calls": 8, "minute": R.iso(START), "token_bucket_allows": 120}
    bd = n["budget_deferred_by_class_per_hour"]
    assert len(bd) == 24 and bd[0]["total"] == 0 and bd[0]["hot_mint_distinct_pools"] == "not reported" and bd[0]["cold_mint_distinct_pools"] == "not reported"  # fields the row lacks are not reported, not failures
    bk = n["counter_calls_next_to_the_prefetchers_booked_calls_per_hour"]
    assert len(bk) == 24 and bk[0]["counter_calls"] == 480 and bk[0]["prefetcher_booked_calls_hour"] == "not reported" and bk[0]["differ"] is None and bk[1]["prefetcher_calls_stat_delta"] == 480
    for name, mut in (("reset_pf", lambda rows, fx: sel(rows, "prefetch_start", B)[0].update(spend_state_reset=1)), ("reset_ch", lambda rows, fx: sel(rows, "chain_hour", A)[-1].update(spend_state_reset=1)),
                      ("spend_w", lambda rows, fx: sel(rows, "chain_hour", B)[-1].update(spend_write_errors=2)), ("state_w", lambda rows, fx: sel(rows, "chain_hour", B)[-1].update(counter_state_write_errors=1))):
        f = by_id(rev3(fixture(sub(tmp_path, name), mutate=mut)))["5"]
        assert f["status"] == R.FAIL and f["numbers"]["failing"]["expected_zero_counters_not_zero"] == 1, name


def test_check_5_reports_the_new_chain_hour_fields_when_present(tmp_path):
    def m(rows, fx):
        for r in sel(rows, "chain_hour", B):
            r.update(deferred_hot_mint=3, deferred_cold_mint=5, booked_calls_hour=r["calls"], deferred_mint=7, budget_deferred=9)

    n = by_id(rev3(fixture(tmp_path, mutate=m)))["5"]["numbers"]
    bd = [x for x in n["budget_deferred_by_class_per_hour"] if x["pid"] == B]
    assert bd[0]["hot_mint_distinct_pools"] == 3 and bd[0]["cold_mint_distinct_pools"] == 5 and bd[0]["total"] == 9 and bd[1]["total"] == 0
    bk = [x for x in n["counter_calls_next_to_the_prefetchers_booked_calls_per_hour"] if x["pid"] == B]
    assert bk[0]["prefetcher_booked_calls_hour"] == bk[0]["counter_calls"] and bk[0]["differ"] is False


def test_check_5_unusable_chain_hour_rows_are_unmeasured(tmp_path):
    c = by_id(rev3(fixture(tmp_path, mutate=lambda rows, fx: sel(rows, "chain_hour", B)[4].update(per_minute_calls=[1, 2, 3]))))["5"]
    assert c["status"] == R.UNM and len(c["numbers"]["chain_hour_rows_unusable"]) == 1


# ------------------------------------------------------------------------------------------------------------------------------ check 8
def c8(tmp: Path, name: str, mutate=None, **kw) -> dict:
    return by_id(rev3(fixture(sub(tmp, name), mutate=mutate, **kw)))["8"]


def test_check_8_passes_when_all_four_obs_conditions_hold(tmp_path):
    c = c8(tmp_path, "ok")
    o = c["numbers"]["obs_conditions"]
    assert c["status"] == R.PASS and o["runner_start_rows_same_frozen_sha"] is True and o["prefetch_start_rows_policy_v2"] == [True, True] and o["prefetch_start_rows_spend_state_reset_0"] == [True, True]
    assert o["first_process_calls_in_the_60_min_before_the_cancel"] > 0 and o["second_prefetch_start_spent_at_load"] > 0 and o["spent_at_load_positive_if_the_first_process_booked_calls"] is True
    assert o["counter_day_calls_compare"] == "ok" and o["second_prefetch_start_counter_day_calls_at_start"] >= o["first_process_last_chain_hour_counter_day_calls"]
    assert c["numbers"]["restart_protocol_informational"] == {"restart_start_minute_of_hour": RESTART_MIN, "restart_hour_starts_at_00_00Z": False}


def test_check_8_condition_a_both_runner_start_rows_show_the_same_frozen_sha(tmp_path):
    c = c8(tmp_path, "a", lambda rows, fx: sel(rows, "runner_start", B)[0].update(frozen_sha="b" * 40))
    assert c["status"] == R.FAIL and c["numbers"]["obs_conditions"]["runner_start_rows_same_frozen_sha"] is False


def test_check_8_condition_b_policy_v2_and_spend_state_reset_0_in_both_prefetch_start_rows(tmp_path):
    p = c8(tmp_path, "p", lambda rows, fx: sel(rows, "prefetch_start", A)[0].update(policy="v1"))
    assert p["status"] == R.FAIL and p["numbers"]["obs_conditions"]["prefetch_start_rows_policy_v2"] == [False, True]
    r = c8(tmp_path, "r", lambda rows, fx: sel(rows, "prefetch_start", B)[0].update(spend_state_reset=1))
    assert r["status"] == R.FAIL and r["numbers"]["obs_conditions"]["prefetch_start_rows_spend_state_reset_0"] == [True, False]
    m = c8(tmp_path, "m", lambda rows, fx: sel(rows, "prefetch_start", B)[0].pop("spend_state_reset"))
    assert m["status"] == R.UNM  # a field that is missing is not a pass


def test_check_8_condition_c_spent_at_load_is_positive_if_the_first_process_booked_calls_before_the_cancel(tmp_path):
    z = c8(tmp_path, "z", lambda rows, fx: sel(rows, "prefetch_start", B)[0].update(spent_at_load=0))
    assert z["status"] == R.FAIL and z["numbers"]["obs_conditions"]["spent_at_load_positive_if_the_first_process_booked_calls"] is False
    # the first process booked nothing in the 60 minutes before the cancel (it stopped calling an hour and a half earlier): 0 is right
    quiet = lambda m: 0 if m >= 12 * 60 - 90 else 8  # noqa: E731
    q = c8(tmp_path, "q", lambda rows, fx: sel(rows, "prefetch_start", B)[0].update(spent_at_load=0), calls=quiet)
    assert q["status"] == R.PASS and q["numbers"]["obs_conditions"]["first_process_calls_in_the_60_min_before_the_cancel"] == 0 and q["numbers"]["obs_conditions"]["second_prefetch_start_spent_at_load"] == 0


def test_check_8_condition_d_counter_day_calls_at_start_is_not_smaller_than_the_first_processs_last_row(tmp_path):
    def smaller(rows, fx):
        last = sel(rows, "chain_hour", A)[-1]["counter_day_calls"]
        sel(rows, "prefetch_start", B)[0]["counter_day_calls_at_start"] = last - 1

    d = c8(tmp_path, "d", smaller)
    assert d["status"] == R.FAIL and d["numbers"]["obs_conditions"]["counter_day_calls_compare"].startswith("smaller")
    eq = c8(tmp_path, "eq", lambda rows, fx: sel(rows, "prefetch_start", B)[0].update(counter_day_calls_at_start=sel(rows, "chain_hour", A)[-1]["counter_day_calls"]))
    assert eq["status"] == R.PASS  # equal is allowed: >=
    # different UTC days cannot be compared: UNMEASURED
    day = c8(tmp_path, "day", lambda rows, fx: sel(rows, "prefetch_start", B)[0].update(process_start_ms=fx["restart"] + DAY))
    assert day["status"] == R.UNM and day["numbers"]["obs_conditions"]["counter_day_calls_compare"] == "different_utc_days"
    none = c8(tmp_path, "none", lambda rows, fx: sel(rows, "prefetch_start", B)[0].update(counter_day_calls_at_start=None))
    assert none["status"] == R.UNM


def test_check_8_keeps_the_decision_log_conditions_and_a_third_start_is_unplanned(tmp_path):
    third = fixture(sub(tmp_path, "third"), extra=lambda fx: fx["L"].add("SCAN", fx["end"] + 5, kind="start", binding={"dirty": False, "git_sha": "a" * 40}, frozen_sha="a" * 40, lane_h=False))
    c = by_id(rev3(third))["8"]
    assert c["status"] in (R.FAIL, R.UNM)  # the third start has no runner_start (rule M) or is an unplanned restart (FAIL): never a pass
    sha = by_id(rev3(fixture(sub(tmp_path, "sha"), restart_sha="b" * 40)))["8"]
    assert sha["status"] == R.FAIL and sha["numbers"]["start_rows_lane_h_false_and_same_frozen_sha"] is False
    one = by_id(rev3(fixture(sub(tmp_path, "one"), start2=False, obs=False)))["8"]
    assert one["status"] == R.UNM


# ------------------------------------------------------------------------------------------------------------------------------ the obs file itself
def test_a_torn_last_line_is_dropped_and_counted_and_other_schemas_are_skipped(tmp_path):
    fx = fixture(tmp_path)
    p = fx["root"] / O.OBS_FILE
    with open(p, "ab") as f:
        f.write(b'{"schema":"hv4_obs_v1","kind":"runner_hour","pid":222,"hour_s')  # a live file's partial last line
    rec = rev3(fx)
    assert rec["obs"]["torn_tail_lines_dropped"] == 1 and rec["rule_m"]["fired"] is False and rec["verdict"] == "PASS"
    with open(p, "ab") as f:
        f.write(b'\n{"schema":"other_v9","kind":"runner_hour","pid":1}\n')
    rec2 = rev3(fx)
    assert rec2["also_reported_from_obs"]["obs_rows_in_file"]["rows_with_another_schema"] == 1 and rec2["obs"]["torn_tail_lines_dropped"] >= 0


def test_the_rows_come_from_the_real_writer_and_the_reader_keeps_only_the_fields_it_uses(tmp_path):
    fx = fixture(tmp_path)
    line = (fx["root"] / O.OBS_FILE).read_text().splitlines()[0]
    assert json.loads(line)["schema"] == O.OBS_SCHEMA == R.OBS_SCHEMA and R.OBS_FILE == O.OBS_FILE and line == O.canon(json.loads(line))
    Ob = R.load_obs(fx["root"] / O.OBS_FILE, [])
    assert Ob.present and Ob.rows == sum(Ob.by_kind.values()) and not Ob.no_pid and not Ob.malformed
    assert set(Ob.chain_hour[0]) >= {"pid", "hour_end_ms", "per_minute_calls", "counter_day_calls", "booked_calls_hour"} and "prefetch_stats" not in Ob.chain_hour[0]


def test_the_status_of_a_check_that_raises_inside_the_obs_evaluation_is_unmeasured_for_that_check_only(tmp_path, monkeypatch):
    fx = fixture(tmp_path)

    def boom(*a, **k):
        raise KeyError("detail")

    monkeypatch.setattr(R, "eval_check_5_obs", boom)
    c = by_id(rev3(fx))
    assert c["5"]["status"] == R.UNM and c["5"]["numbers"] == {"input_error": "KeyError"} and c["1"]["status"] == R.PASS and c["8"]["status"] == R.PASS


# ------------------------------------------------------------------------------------------------------------------------------ check 2 diagnosis, and the real counter
def test_chain_stale_served_rows_are_split_hot_cold_and_hot_stale_cards_without_a_served_row_are_counted(tmp_path):
    def rows(rows_, fx):
        for i, age in ((0, 400_000), (1, 700_000)):  # served rows for the stale cards of scans 50 and 51
            t = scan_t(50 + i)
            rows_.append({"wall_ms": t + 10, "pid": A, "kind": "chain_stale_served", "pool": f"STALEPOOL-z9-{i}", "scan_ms": t, "age_ms": age, "hot": True, "n_mints": 1, "fails_mint": 0,
                          "next_try_in_ms": 0, "pool_age_in_store_ms": 9})
        # scan 52: a served row for ANOTHER pool at that scan, cold: the stale card of scan 52 has no served row at its own pool and scan_ms
        rows_.append({"wall_ms": scan_t(52) + 10, "pid": A, "kind": "chain_stale_served", "pool": "OTHERPOOL-k2", "scan_ms": scan_t(52), "age_ms": 500_000, "hot": False, "n_mints": 1, "fails_mint": 0,
                      "next_try_in_ms": 0, "pool_age_in_store_ms": 9})

    fx = fixture(tmp_path, mutate=rows, refused=lambda i: [(f"STALEPOOL-z9-{i - 50}", ["chain_stale"])] if i in (50, 51, 52) else [])
    s = rev3(fx)["also_reported_from_obs"]["chain_stale_served"]
    assert s["hot"] == {"n": 2, "age_ms_p50": 400_000, "age_ms_max": 700_000} and s["cold"] == {"n": 1, "age_ms_p50": 500_000, "age_ms_max": 500_000}
    assert s["hot_chain_stale_cards"] == 3 and s["hot_chain_stale_cards_without_a_served_row_at_the_same_pool_and_scan_ms"] == 1


def test_the_prior_minutes_window_matches_how_credit_counter_writes_the_tail_on_this_branch(tmp_path, monkeypatch):
    """The real tools.hv4_chain.CreditCounter drives a fake clock; its state file's tail is what the restarted process reports as prior_minutes. The report's series, built from the
    first process's chain_hour-style rows plus that tail, must equal the calls the counter really recorded, with every minute counted once."""
    C = pytest.importorskip("tools.hv4_chain")
    monkeypatch.setattr(os, "fsync", lambda fd: None)  # the counter fsyncs its state file after every call; the test makes thousands
    assert R.PRIOR_WINDOW_MIN == C.PRIOR_MINUTES == 61
    state = tmp_path / "chain-credits.json"
    clk = {"ms": START}
    rmin_a_end = START + 3 * HOUR + 5 * MIN  # the first process stops 3 h 5 min in; the second starts a minute later
    truth: dict[int, int] = {}
    ca = C.CreditCounter("helius", now_ms=lambda: clk["ms"], state_path=state)
    for t in range(START, rmin_a_end, MIN):
        n = 0 if (t // MIN) % 7 == 3 else 1 + (t // MIN) % 3  # some minutes make no call at all
        for _ in range(n):
            clk["ms"] = t + 500
            ca.record_call("getAccountInfo")
        truth[t] = n
    hour_rows = []
    for hs in range(START, START + 3 * HOUR, HOUR):  # the first process's closed hours (the last one closes at 01:00 + 2 h... three rows)
        hour_rows.append({"hs": hs, "pm": [m[1] for m in ca.minute_counts(hs, hs + HOUR)]})
    clk["ms"] = rmin_a_end + MIN
    cb = C.CreditCounter("helius", now_ms=lambda: clk["ms"], state_path=state)  # the restart: the state file is read back
    assert [m[0] for m in cb.prior_minutes] == sorted(t for t, n in truth.items() if n and t >= max(k for k, v in truth.items() if v) - 60 * MIN)  # sparse: only non-empty minutes of the last 61
    assert cb.prior_calls_total == ca.calls == sum(truth.values())
    # the observability rows as the real writers would produce them from these counters
    fx = {"root": tmp_path}
    rows = [{"wall_ms": START - 1000, "pid": A, "kind": "runner_start", "frozen_sha": SHA}, {"wall_ms": START - 900, "pid": A, "kind": "prefetch_start", "policy": "v2", "prior_minutes": [], "prior_calls_total": 0},
            {"wall_ms": rmin_a_end - 1000, "pid": B, "kind": "runner_start", "frozen_sha": SHA},
            {"wall_ms": rmin_a_end - 900, "pid": B, "kind": "prefetch_start", "policy": "v2", "prior_minutes": [list(m) for m in cb.prior_minutes], "prior_calls_total": cb.prior_calls_total,
             "spent_at_load": 1, "spend_state_reset": 0, "counter_day_calls_at_start": cb.calls_today()}]
    for r in hour_rows:
        rows.append({"wall_ms": r["hs"] + HOUR + 5000, "pid": A, "kind": "chain_hour", "hour_start_ms": r["hs"], "hour_end_ms": r["hs"] + HOUR, "per_minute_calls": r["pm"], "calls": sum(r["pm"]), "credits": sum(r["pm"])})
    # the second process makes its own calls from the minute after the first stopped
    for t in range(rmin_a_end + MIN, START + 5 * HOUR, MIN):
        for _ in range(2):
            clk["ms"] = t + 500
            cb.record_call("getAccountInfo")
        truth[t] = truth.get(t, 0) + 2
    for hs in range(START + 3 * HOUR, START + 5 * HOUR, HOUR):
        rows.append({"wall_ms": hs + HOUR + 5000, "pid": B, "kind": "chain_hour", "hour_start_ms": hs, "hour_end_ms": hs + HOUR, "per_minute_calls": [m[1] for m in cb.minute_counts(hs, hs + HOUR)],
                     "calls": 0, "credits": 0})
    # the hour the restart is in (03:00 to 04:00) has the second process's row with zeros before it started; the first process's minutes 03:00 to 03:04 are only in prior_minutes
    put(fx, rows)
    Ob = R.load_obs(tmp_path / O.OBS_FILE, [])
    procs = [{"index": 0, "pid": A, "start_ms": START, "end_ms": rmin_a_end, "runner_start": Ob.runner_start[0], "prefetch": Ob.prefetch_start[0], "n_runner_start_candidates": 1, "n_prefetch_start": 1, "join": "pid"},
             {"index": 1, "pid": B, "start_ms": rmin_a_end, "end_ms": START + 5 * HOUR, "runner_start": Ob.runner_start[1], "prefetch": Ob.prefetch_start[1], "n_runner_start_candidates": 1, "n_prefetch_start": 1,
              "join": "pid"}]
    S = R.build_series(Ob, procs, START, START + 5 * HOUR)
    cov = S["restarts"][0]
    assert cov["gap_minutes"] == 0 and cov["overlap_disagreements"] == 0 and cov["overlap_minutes_compared"] > 0
    got = {m: sum(S["own"][p].get(m, 0) for p in S["own"]) + (S["tail_eff"][m][1] if m in S["tail_eff"] else 0) for m in S["mins"]}
    assert {m: n for m, n in got.items() if n} == {m: n for m, n in truth.items() if n}  # every minute counted once, the tail included
    assert sum(got.values()) == ca.calls + cb.calls
