"""Tests of scripts/ops/floor_stage1_proof.py (P0-P2 replay proof of the FLOOR lane host stage 1). SYNTHETIC tapes and decision logs only; nothing real is opened."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
from pathlib import Path

import pytest

from tools.arena_rules_testkit import write_tape
from tools.test_arena_rule_l1 import H0, HOUR
from tools.test_floor_lane_host import _env, gap_row  # noqa: F401 (_env: autouse fixture)
from tools.test_floor_lane_host_stage1 import status_row, world

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("floor_stage1_proof", REPO / "scripts" / "ops" / "floor_stage1_proof.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

PQ, PZ = "POOLQQQQ1234567890abcdef", "POOLZZZZ1234567890abcdef"


def hn(ms):
    return P.hour_name(ms)


def renamed_world(extra=()):
    rows = []
    for r in world() + list(extra):
        r = dict(r)
        if r.get("pool") == "Q":
            r["pool"] = PQ
        elif r.get("pool") == "Z":
            r["pool"] = PZ
        rows.append(r)
    return sorted(rows, key=lambda r: r["t_recv_ms"])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setattr(P.TP, "ALLOWED_ROOTS", (str(tmp_path / "tape"),))
    (tmp_path / "scratch").mkdir()
    return tmp_path


def args(tmp, cmd, frm, to, out="o1", *extra):
    return [cmd, "--out", str(tmp / "scratch" / out), "--from", hn(frm), "--to", hn(to), "--seed-from-hour", "none", "--tape-src", str(tmp / "tape"), "--test-mode",
            "--v1-tree", str(tmp / "v1tree"), "--v2-tree", str(REPO), "--now-ms", str(H0 + 400 * HOUR), *extra]


def v1tree(tmp):
    shutil.copytree(REPO / "tools", tmp / "v1tree" / "tools", ignore=shutil.ignore_patterns("__pycache__", "test_*"))
    shutil.copytree(REPO / "observe", tmp / "v1tree" / "observe", ignore=shutil.ignore_patterns("__pycache__"))
    return tmp / "v1tree"


def no_secrets(text):
    assert PQ not in text and PZ not in text
    assert not re.search(r"mark0|price|pnl|exit_reason|vol24|\"pool\"", text)


# ------------------------------------------------------------------------------------------------ pure comparisons
def ent(pool, trig, ms=None, slot=1, meta_extra=None):
    meta = {"signal_t_recv_ms": trig, "signal_slot": slot, "mark0": 1.0, **(meta_extra or {})}
    return {"kind": "entry", "order_id": "id-%s-%d" % (pool, trig), "pool": pool, "ms": ms or trig, "decision_slot": slot, "meta": meta}


def ex(pool, trig, ms, reason="tp"):
    return {"kind": "exit", "order_id": "id-%s-%d" % (pool, trig), "pool": pool, "ms": ms, "reason": reason, "decision_slot": 2, "attempt": 1}


def ref(pool, trig, reason="feed_gap", kind="tip_gap_status_unhealthy"):
    r = {"kind": "entry_refused_gap", "pool": pool, "trigger_ms": trig, "trigger_slot": 1, "reason": reason}
    if reason == "feed_gap":
        r["gap"] = {"kind": kind}
    return r


def test_p1_planted_refusal_turned_entry_passes_with_count_one():
    v1 = [ent("a", 100), ex("a", 100, 900), ref("b", 500)]
    v2 = [ent("a", 100), ex("a", 100, 900), ent("b", 500), ex("b", 500, 1500)]
    d = P.p1_compare(v1, v2)
    assert d["v2_only_entries"] == 1 and d["v2_only_at_v1_refusal_rows"] == 1 and d["pass"] is True and d["nonvacuous"]
    assert d["refusals_v1_by_reason_gapkind"] == {"feed_gap/tip_gap_status_unhealthy": 1}
    assert all(d[k] == 0 for k in d if k.endswith("MUST0"))


def test_p1_changed_exit_fails():
    v1 = [ent("a", 100), ex("a", 100, 900), ref("b", 500)]
    v2 = [ent("a", 100), ex("a", 100, 901), ent("b", 500)]
    d = P.p1_compare(v1, v2)
    assert d["exits_changed_MUST0"] == 1 and d["pass"] is False


def test_p1_vacuous_missing_entry_and_unseen_trigger_fail():
    same = [ent("a", 100), ex("a", 100, 900)]
    assert P.p1_compare(same, same)["nonvacuous"] is False and P.p1_compare(same, same)["pass"] is False
    d = P.p1_compare([ent("a", 100), ref("b", 500)], [ref("b", 500)])
    assert d["v1_entries_missing_in_v2_MUST0"] == 1 and d["pass"] is False
    d = P.p1_compare([ref("b", 500)], [ent("c", 700)])  # a v2 entry on a trigger v1 never saw
    assert d["entries_on_triggers_v1_never_saw_MUST0"] == 1 and d["v2_only_not_at_v1_refusal_MUST0"] == 1 and d["pass"] is False
    d = P.p1_compare([ref("b", 500)], [ent("b", 500), ref("c", 900)])  # a refusal in v2 that v1 does not have
    assert d["v2_refusals_not_in_v1_MUST0"] == 1 and d["pass"] is False


def test_p1_later_v1_refusal_on_a_pool_v2_holds_is_explained_not_unexplained():
    v1 = [ref("b", 500), ref("b", 900, "pool_blocked_after_gap")]
    v2 = [ent("b", 500)]  # v2 holds b after entering: no row at 900
    d = P.p1_compare(v1, v2)
    assert d["v1_refusals_dropped_unexplained_MUST0"] == 0 and d["pass"] is True
    d = P.p1_compare(v1 + [ref("z", 950)], v2)  # z vanished with no v2 entry on z
    assert d["v1_refusals_dropped_unexplained_MUST0"] == 1 and d["pass"] is False


def write_decisions(root, files):
    d = Path(root) / "decisions" / P.BOT
    d.mkdir(parents=True)
    for n, b in files.items():
        (d / n).write_bytes(b)
    (Path(root) / "lane").mkdir()
    (Path(root) / "lane" / "checkpoint.json").write_bytes(b'{"hwm_ms":1}')


def test_p0_equal_passes_and_one_flipped_byte_fails(tmp_path):
    body = b'{"seq":1,"kind":"start"}\n{"seq":2,"kind":"entry"}\n'
    write_decisions(tmp_path / "a", {"2026-11-10.jsonl": body})
    write_decisions(tmp_path / "b", {"2026-11-10.jsonl": body})
    c = P.p0_compare(str(tmp_path / "a"), str(tmp_path / "b"))
    assert P.p0_pass(c, 0) and c["files_md5_differ"] == 0 and c["checkpoint_equal"]
    assert not P.p0_pass(c, 1)  # a status span in W1: not strict
    flipped = bytearray(body)
    flipped[10] ^= 1
    write_decisions(tmp_path / "c", {"2026-11-10.jsonl": bytes(flipped)})
    c = P.p0_compare(str(tmp_path / "a"), str(tmp_path / "c"))
    assert not P.p0_pass(c, 0) and c["files_md5_differ"] == 1
    write_decisions(tmp_path / "d", {"2026-11-10.jsonl": body, "2026-11-11.jsonl": b"{\"seq\":9}\n"})  # an extra day file
    assert not P.p0_pass(P.p0_compare(str(tmp_path / "a"), str(tmp_path / "d")), 0)


def test_p2_case_planted_mismatch_fails_and_match_passes():
    refrows = [ent("a", 1_000_000), ex("a", 1_000_000, 9_000_000), ent("b", 1_200_000)]
    rec = {"seq_pre": 5, "seq_post": 6, "buys_pre": 1, "buys_post": 1, "blocked_pools": {"a": 9_000_000, "b": 2 ** 62}, "lane_open_pools": set()}
    shadow = {"kind": "lane_restart_shadow", "shadow_complete": True, "fallback": None, "prev_end": "signal", "shadow_pools": ["b"]}
    rows = [ent("a", 1_000_000), shadow]  # the lane itself holds a (entered before T)
    m = P.p2_case_metrics("a_5min", refrows, 1_100_000, 300_000, None, rows, rec, None, "none", 1_400_000)
    assert m["pass"] is True and m["n_pools_blocked_by_shadow"] == 1 and m["n_ref_buys_in_downtime"] == 1 and m["blocked_equals_ref_buys"]
    bad = dict(shadow, shadow_pools=[])  # the shadow missed the reference's Buy on b
    m = P.p2_case_metrics("a_5min", refrows, 1_100_000, 300_000, None, [ent("a", 1_000_000), bad], rec, None, "none", 1_400_000)
    assert m["n_ref_pools_bought_in_downtime_missed_MUST0"] == 1 and m["pass"] is False
    extra = [ent("a", 1_000_000), shadow, ent("zz", 1_450_000, ms=1_450_000)]  # a post-restart entry the reference never made
    m = P.p2_case_metrics("a_5min", refrows, 1_100_000, 300_000, None, extra, rec, None, "none", 1_400_000)
    assert m["n_post_restart_entries_not_in_reference_MUST0"] == 1 and m["pass"] is False
    rec2 = dict(rec, seq_post=8)  # the shadow wrote rows
    m = P.p2_case_metrics("a_5min", refrows, 1_100_000, 300_000, None, rows, rec2, None, "none", 1_400_000)
    assert m["n_rows_written_by_shadow_MUST0"] == 2 and m["pass"] is False
    fb = {"kind": "lane_restart_shadow", "shadow_complete": False, "fallback": "unclean_exit", "prev_end": "exception", "shadow_pools": []}
    assert P.p2_case_metrics("c", refrows, 1_100_000, 300_000, None, [ent("a", 1_000_000), fb], rec, None, "unclean_exit", 1_400_000)["pass"] is True
    assert P.p2_case_metrics("c", refrows, 1_100_000, 300_000, None, [ent("a", 1_000_000), fb], rec, None, "none", 1_400_000)["pass"] is False  # wrong fallback


def test_p2_b2_a_block_that_ends_before_the_reference_exit_fails_and_closed_pool_history_does_not_cover():
    refrows = [ent("a", 1_000_000), ex("a", 1_000_000, 9_000_000), ent("b", 1_200_000), ex("b", 1_200_000, 1_300_000)]
    shadow = {"kind": "lane_restart_shadow", "shadow_complete": True, "fallback": None, "prev_end": "signal", "shadow_pools": []}
    base = {"seq_pre": 5, "seq_post": 6, "buys_pre": 1, "buys_post": 1, "lane_open_pools": set()}
    # the old restart block on a ends at 2_000_000, before the reference exit (capped at restart + horizon = 5_000_000): NOT covered, and the lane's old entry on a is no cover
    m = P.p2_case_metrics("a_5min", refrows, 1_100_000, 300_000, None, [ent("a", 1_000_000), shadow], dict(base, blocked_pools={"a": 2_000_000}), None, "none", 1_400_000)
    assert m["n_ref_positions_open_in_downtime"] == 2 and m["n_ref_positions_open_in_downtime_uncovered_MUST0"] == 2 and m["pass"] is False
    ok = P.p2_case_metrics("a_5min", refrows, 1_100_000, 300_000, None, [ent("a", 1_000_000), shadow], dict(base, blocked_pools={"a": 9_000_000, "b": 5_000_000}), None, "none", 1_400_000)
    assert ok["n_ref_positions_open_in_downtime_uncovered_MUST0"] == 0
    ok2 = P.p2_case_metrics("a_5min", refrows, 1_100_000, 300_000, None, [shadow], dict(base, blocked_pools={"b": 5_000_000}, lane_open_pools={"a"}), None, "none", 1_400_000)
    assert ok2["n_ref_positions_open_in_downtime_uncovered_MUST0"] == 0
    # QP B3: a block that outlives restart + a 4 h horizon but ends before the reference exit still FAILS (no horizon cap)
    far = [ent("a", 1_000_000), ex("a", 1_000_000, 90_000_000), ent("b", 1_200_000)]
    hold = 86_400_000
    m = P.p2_case_metrics("a_5min", far, 1_100_000, 300_000, None, [ent("a", 1_000_000), shadow], dict(base, blocked_pools={"a": 1_400_000 + 5 * 3_600_000, "b": 2 ** 62}), None, "none", 1_400_000,
                          hold_ms=hold)
    assert m["n_ref_positions_open_in_downtime_uncovered_MUST0"] == 1 and m["pass"] is False
    m = P.p2_case_metrics("a_5min", far, 1_100_000, 300_000, None, [ent("a", 1_000_000), shadow], dict(base, blocked_pools={"a": 1_000_000 + hold + 1_000, "b": 2 ** 62}), None, "none", 1_400_000,
                          hold_ms=hold)
    assert m["n_ref_positions_open_in_downtime_uncovered_MUST0"] == 0


def test_p0_three_way_and_non_vacuity(tmp_path):
    body = b'{"seq":1,"kind":"entry"}\n'
    for n in "abc":
        write_decisions(tmp_path / n, {"2026-11-10.jsonl": body})
    (tmp_path / "c" / "lane" / "checkpoint.json").write_bytes(b'{"hwm_ms":1,"guard":{"cheap_restart":true},"ended":"end"}')
    c = P.p0_compare(str(tmp_path / "a"), str(tmp_path / "b"), str(tmp_path / "c"))
    assert c["checkpoint_equal_on_minus_guard_ended"] and c["files_md5_differ_on"] == 0 and P.p0_pass(c, 0) and P.p0_nonvacuous(c) and c["n_entry"] == 1
    write_decisions(tmp_path / "d", {"2026-11-10.jsonl": body.replace(b"entry", b"entrz")})
    (tmp_path / "d" / "lane" / "checkpoint.json").write_bytes(b'{"hwm_ms":1}')
    c = P.p0_compare(str(tmp_path / "a"), str(tmp_path / "b"), str(tmp_path / "d"))
    assert c["files_md5_differ_on"] == 1 and not P.p0_pass(c, 0)
    (tmp_path / "c" / "lane" / "checkpoint.json").write_bytes(b'{"hwm_ms":2,"guard":{},"ended":"end"}')
    assert not P.p0_pass(P.p0_compare(str(tmp_path / "a"), str(tmp_path / "b"), str(tmp_path / "c")), 0)
    write_decisions(tmp_path / "e", {"2026-11-10.jsonl": b'{"seq":1,"kind":"start"}\n'})
    assert not P.p0_nonvacuous(P.p0_compare(str(tmp_path / "e"), str(tmp_path / "e")))


def test_v2_pin_disk_guard_and_tape_source(env, monkeypatch):
    assert P.TAPE_SRC == (P.HV7,)
    with pytest.raises(SystemExit):  # outside test mode only hv7 is accepted
        P.check_srcs(["/data2/mal/scratch/floor-nov-funnel/tapecopy"])
    with pytest.raises(SystemExit):
        P.check_srcs([P.HV7 + "/../hv7x"])

    class A:
        test_mode = False
        merge_sha = "m"
        host_blob = P.MERGE_HOST_BLOB
        v1_tree = v2_tree = None

    monkeypatch.setattr(P, "git", lambda *a: "0" * 40 if "floor_lane_host.py" in a[-1] else "deadbeef")
    with pytest.raises(SystemExit):  # the host blob is not the merge's
        P.prepare_trees(A, str(env / "scratch" / "x"))
    (env / "src").mkdir()
    (env / "src" / "f").write_bytes(b"x" * 1000)
    with pytest.raises(SystemExit):
        P.disk_guard(str(env / "src"), 10, str(env), usage=lambda w: type("U", (), {"free": 5000, "total": 10000}))
    P.disk_guard(str(env / "src"), 2, str(env), usage=lambda w: type("U", (), {"free": 9000, "total": 10000}))


def test_pick_t_needs_a_buy_in_the_downtime_and_an_open_position():
    rows = [ent("a", 1_000_000), ent("b", 4_000_000), ex("a", 1_000_000, 9_000_000)]
    T = P.pick_t(rows, 0, 10 ** 9, 25_200_000, 0)
    assert T is not None and T <= 4_000_000 - 1 and 4_000_000 - T <= 300_000
    assert P.pick_t([ent("a", 1_000_000)], 0, 10 ** 9, 25_200_000, 0) is None  # no other position open
    assert P.pick_t(rows, 0, 5_000_000, 25_200_000, 0) is None  # window too short for D = 7 h


# ------------------------------------------------------------------------------------------------ guards
def test_out_dir_refusals(env):
    for bad in (str(env / "elsewhere"), str(env / "scratch"), str(env / "scratch" / ".." / "x"), "/tmp/x"):
        with pytest.raises(SystemExit):
            P.check_out(bad)
    (env / "scratch" / "exists").mkdir()
    with pytest.raises(SystemExit):
        P.check_out(str(env / "scratch" / "exists"))  # never overwrite
    assert P.check_out(str(env / "scratch" / "fresh")).endswith("fresh")


def test_tape_path_guard_and_window_reachability(env):
    for bad in ("/data/mal/arena/tape", "/var/lib/mal/sealed/x", "/home/claude/mal-data/arena-mirror/tape", "/data/mal/blocks/forward-1016", str(env / "other")):
        with pytest.raises(SystemExit):
            P.check_srcs([bad])
    seed = P.parse_hour("2026-11-02T01")
    now = P.parse_hour("2026-11-13T05")
    with pytest.raises(SystemExit):  # 2026-11-10T00Z is not reachable from a 11-02T01 seed
        P.check_window(P.parse_hour("2026-11-10T00"), P.parse_hour("2026-11-11T00"), seed, now)
    P.check_window(P.parse_hour("2026-11-10T10"), P.parse_hour("2026-11-11T00"), seed, now)  # the earliest valid start
    with pytest.raises(SystemExit):  # the current hour is not closed
        P.check_window(P.parse_hour("2026-11-12T00"), P.parse_hour("2026-11-13T06"), seed, now)


def test_symlink_resolving_outside_the_root_is_refused(env, tmp_path):
    (env / "tape").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tape-2026-11-12T00.jsonl.zst").write_bytes(b"")
    os.symlink(outside / "tape-2026-11-12T00.jsonl.zst", env / "tape" / "tape-2026-11-12T00.jsonl.zst")
    with pytest.raises(SystemExit):
        P.make_tapelink(str(env / "scratch" / "o"), [str(env / "tape")], P.parse_hour("2026-11-12T00"), P.parse_hour("2026-11-12T01"))


def test_count_only_prints_counts_and_opens_nothing(env, capsys, monkeypatch):
    write_tape(env / "tape", renamed_world())
    monkeypatch.setattr(P, "git", lambda *a: "deadbeef")
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": b"x", "returncode": 0})())
    seed = H0 - 10 * HOUR
    rc = P.main(["p1", "--count-only", "--out", str(env / "scratch" / "c"), "--from", hn(H0 + 30 * HOUR), "--to", hn(H0 + 33 * HOUR), "--seed-from-hour", hn(seed) if False else "none",
                 "--tape-src", str(env / "tape"), "--test-mode", "--now-ms", str(H0 + 400 * HOUR)])
    out = capsys.readouterr().out
    assert out.startswith("COUNT-ONLY") and "hours_expected=" in out and "hours_missing=" in out
    no_secrets(out)
    assert not (env / "scratch" / "c").exists()  # count-only creates nothing
    assert rc in (0, 2)


def test_scan_gaps_counts_only_the_writer_status_span(env):
    rows = renamed_world([status_row(H0 + 30 * HOUR - 763, H0 + 30 * HOUR), status_row(H0 + 30 * HOUR + 5, H0 + 30 * HOUR + 9, missed_slots=1, from_slot=5, to_slot=6),
                          status_row(H0 + 31 * HOUR - 500_000, H0 + 31 * HOUR, src="tip"), gap_row(H0 + 29 * HOUR, H0 + 29 * HOUR + 30_000, prog="all", kind="tip_gap_backlog_jump")])
    write_tape(env / "tape", rows)
    sc = P.scan_gaps(str(env / "tape"), H0 + 24 * HOUR, H0 + 33 * HOUR)
    assert sc["status_spans"] == 1 and sc["status_kind_any_src"] == 3 and sc["status_durations_ms"] == [763] and sc["by_kind"]["tip_gap_backlog_jump"] == 1
    assert P.scan_gaps(str(env / "tape"), H0, H0 + HOUR)["status_spans"] == 0


# ------------------------------------------------------------------------------------------------ end to end on a synthetic tape (test-mode child: first_print table, in-memory engine)
def test_e2e_p0_equal_trees_pass_and_a_status_span_in_w1_refuses(env, capsys):
    v1tree(env)
    write_tape(env / "tape", renamed_world())
    rc = P.main(args(env, "p0", H0 + 6 * HOUR, H0 + 34 * HOUR))
    out = capsys.readouterr().out
    assert rc == 0 and "P0 PASS" in out and "files_md5_differ=0" in out and "test_mode=True" in out and "seed_hour=none" in out and "files_md5_differ_on=0" in out and "checkpoint_equal_on_minus_guard_ended=True" in out and "checkpoint_equal=True" in out, out
    no_secrets(out)
    res = json.loads((env / "scratch" / "o1" / "p0.json").read_text())
    assert res["files_v1"] >= 1 and res["rows_v1"] == res["rows_v2"] and res["rows_v1"] >= 1  # the Q entry
    # a W1 with a writer status span is not strict: refuse before any replay
    shutil.rmtree(env / "tape")
    write_tape(env / "tape", renamed_world([status_row(H0 + 31 * HOUR + 3_000_000 - 763, H0 + 31 * HOUR + 3_000_000)]))
    with pytest.raises(SystemExit) as ei:
        P.main(args(env, "p0", H0 + 6 * HOUR, H0 + 34 * HOUR, "o2"))
    assert ei.value.code == P.EXIT_FAIL and not (env / "scratch" / "o2" / "v1").exists()


def test_e2e_p1_status_span_turns_a_v1_refusal_into_an_entry(env, capsys):
    v1tree(env)
    span = status_row(H0 + 31 * HOUR + 1_800_000 - 763, H0 + 31 * HOUR + 1_800_000)  # 30 min before the 32 h trigger on Q
    write_tape(env / "tape", renamed_world([span]))
    rc = P.main(args(env, "p1", H0 + 6 * HOUR, H0 + 34 * HOUR))
    out = capsys.readouterr().out
    assert rc == 0 and "P1 PASS" in out
    no_secrets(out)
    d = json.loads((env / "scratch" / "o1" / "p1.json").read_text())
    assert d["v2_only_entries"] == 1 and d["v2_only_at_v1_refusal_rows"] == 1 and d["entries_v1"] == 0 and d["entries_v2"] == 1 and d["status_spans_in_window"] == 1
    assert d["span_ignored_rows_v2"] == 1 and all(d[k] == 0 for k in d if k.endswith("MUST0"))


def test_e2e_p1_without_a_status_span_is_vacuous(env, capsys):
    v1tree(env)
    write_tape(env / "tape", renamed_world())
    with pytest.raises(SystemExit) as ei:
        P.main(args(env, "p1", H0 + 6 * HOUR, H0 + 34 * HOUR))
    assert ei.value.code == P.EXIT_VACUOUS


def test_e2e_p2_every_restart_case(env, capsys):
    write_tape(env / "tape", renamed_world())
    rc = P.main(args(env, "p2", H0, H0 + 48 * HOUR, "o1", "--step-ms", "60000", "--horizon-s", "5400"))
    out = capsys.readouterr().out
    no_secrets(out)
    res = json.loads((env / "scratch" / "o1" / "p2.json").read_text())
    by = {c["case"]: c for c in res["cases"]}
    assert set(by) == {"a_5min", "a_2h", "a_warm-5min", "a_warm+5min", "a_7h", "c_exception", "d_in_doubt", "e_unknown_v1_checkpoint", "e_unknown_status_flag_only"}
    assert by["a_5min"]["shadow_complete"] and by["a_5min"]["prev_end"] == "signal" and by["a_5min"]["n_ref_buys_in_downtime"] >= 1 and by["a_5min"]["n_pools_blocked_by_shadow"] == 1
    assert by["a_warm-5min"]["shadow_complete"] and by["a_warm+5min"]["fallback"] == "downtime_exceeds_warmup" and by["a_7h"]["fallback"] == "downtime_exceeds_warmup"
    assert by["c_exception"]["fallback"] == "unclean_exit" and by["d_in_doubt"]["fallback"] == "unclean_exit"
    assert by["e_unknown_v1_checkpoint"]["fallback"] == "unknown_prev_end" and by["e_unknown_status_flag_only"]["fallback"] == "unknown_prev_end"
    for c in res["cases"]:
        assert c["n_post_restart_entries_not_in_reference_MUST0"] == 0 and c["n_dispatched_during_shadow_MUST0"] == 0 and c["n_rows_written_by_shadow_MUST0"] == 0, c["case"]
    assert rc == 0 and res["pass"] and "P2 PASS" in out
    assert "t_ms" not in out  # the instant stays in the JSON


def test_e2e_p2_no_reference_buy_is_vacuous(env, capsys):
    write_tape(env / "tape", [r for r in renamed_world() if r["t_recv_ms"] < H0 + 31 * HOUR])  # no drained-pool sell: the reference never buys
    rc = P.main(args(env, "p2", H0, H0 + 48 * HOUR, "o1", "--step-ms", "60000", "--horizon-s", "600"))
    assert rc == P.EXIT_VACUOUS


# ------------------------------------------------------------------------------------------------ frozen hard-link copy, discovered seed, windows (precount #2549)
HOURS = ["2026-11-03T01", "2026-11-03T02", "2026-11-03T03"]


def frozen_world(tmp, monkeypatch, drop_from_hv7=(), sizes=None):
    hv7, fp = tmp / "hv7", tmp / "fp"
    hv7.mkdir()
    fp.mkdir()
    monkeypatch.setattr(P, "HV7", str(hv7))
    monkeypatch.setattr(P, "FROZEN_PARENT", str(fp))
    monkeypatch.setattr(P, "_FROZEN_OK", [])
    monkeypatch.setattr(P.TP, "ALLOWED_ROOTS", (str(tmp / "tape"), str(hv7)))
    cp = fp / "tapecopy-1113"
    cp.mkdir()
    lines = []
    for i, h in enumerate(HOURS):
        n = "tape-%s.jsonl.zst" % h
        (hv7 / n).write_bytes(b"x" * (10 + i))
        os.link(hv7 / n, cp / n)
        lines.append("%s %d 2" % (n, (sizes or {}).get(h, 10 + i)))
    (fp / "tapecopy-1113.list").write_text("\n".join(lines) + "\n")
    for h in drop_from_hv7:
        (hv7 / ("tape-%s.jsonl.zst" % h)).unlink()
    return str(cp), str(fp / "tapecopy-1113.list")


def needed():
    return P.parse_hour(HOURS[0]), P.parse_hour(HOURS[-1]) + HOUR


def test_seed_is_the_first_hour_present_and_earliest_from_follows(env, monkeypatch):
    cp, _ = frozen_world(env, monkeypatch)
    seed = P.discover_seed([cp])
    assert hn(seed) == "2026-11-03T01" and hn(seed + P.SEED_MIN_H * HOUR) == "2026-11-11T10"
    with pytest.raises(SystemExit):  # one hour before the earliest valid start
        P.check_window(P.parse_hour("2026-11-11T09"), P.parse_hour("2026-11-12T00"), seed, P.parse_hour("2026-11-14T00"))
    P.check_window(P.parse_hour("2026-11-11T10"), P.parse_hour("2026-11-12T00"), seed, P.parse_hour("2026-11-14T00"))
    (env / "empty").mkdir()
    with pytest.raises(SystemExit):
        P.discover_seed([str(env / "empty")])


def test_frozen_copy_verified_with_counts_and_list_sha(env, monkeypatch):
    cp, lp = frozen_world(env, monkeypatch, drop_from_hv7=[HOURS[0]])
    P.check_srcs([cp])
    frm, to = needed()
    r = P.verify_frozen([cp], frm, to, lp)
    assert r["hours_checked"] == 3 and r["hours_same_inode"] == 2 and r["hours_no_longer_in_hv7"] == 1 and r["size_mismatch_MUST0"] == 0
    import hashlib
    assert r["list_sha256"] == hashlib.sha256(Path(lp).read_bytes()).hexdigest()


def test_frozen_copy_refuses_planted_mismatches(env, monkeypatch):
    frm, to = needed()
    cp, lp = frozen_world(env, monkeypatch, sizes={HOURS[1]: 999})  # listed size differs from the file
    with pytest.raises(SystemExit):
        P.verify_frozen([cp], frm, to, lp)
    Path(lp).write_text(Path(lp).read_text().replace("999", "11"))
    P.verify_frozen([cp], frm, to, lp)  # the repaired list passes
    last = env / "fp" / "tapecopy-1113" / ("tape-%s.jsonl.zst" % HOURS[2])
    os.remove(last)  # a missing hour
    with pytest.raises(SystemExit):
        P.verify_frozen([cp], frm, to, lp)
    os.link(env / "hv7" / ("tape-%s.jsonl.zst" % HOURS[2]), last)
    hp = env / "hv7" / ("tape-%s.jsonl.zst" % HOURS[0])  # same size, another inode than hv7's (a copy, not a hard link)
    data = hp.read_bytes()
    hp.unlink()
    hp.write_bytes(data)
    with pytest.raises(SystemExit):
        P.verify_frozen([cp], frm, to, lp)
    hp.write_bytes(data + b"y")  # a different size in hv7
    with pytest.raises(SystemExit):
        P.verify_frozen([cp], frm, to, lp)
    os.remove(lp)  # the list file is required
    with pytest.raises(SystemExit):
        P.verify_frozen([cp], frm, to, lp)


def test_exactly_two_source_roots_outside_test_mode(env, monkeypatch):
    cp, _ = frozen_world(env, monkeypatch)
    assert P.check_srcs([P.HV7]) == [P.HV7] and P.check_srcs([cp]) == [cp]
    for bad in (str(env / "fp" / "tapecopy-12"), str(env / "fp" / "other"), "/data2/mal/scratch/floor-nov-funnel/tapecopy", str(env / "tape")):
        with pytest.raises(SystemExit):
            P.check_srcs([bad])
    os.symlink(cp, env / "fp" / "tapecopy-9999")  # a symlinked copy is refused (abspath != realpath)
    with pytest.raises(SystemExit):
        P.check_srcs([str(env / "fp" / "tapecopy-9999")])
    with pytest.raises(SystemExit):  # a frozen copy is the only source of a run
        P.verify_frozen([cp, P.HV7], *needed())


def test_pre_declared_windows():
    assert P.W1 == ("2026-11-12T00", "2026-11-12T15") and P.W2 == ("2026-11-12T00", "2026-11-13T00") and P.FALLBACK_W2 == ("2026-11-11T10", "2026-11-13T00")
    assert "2026-11-11T10" in P.__doc__ and "asks QP" in P.__doc__


def test_test_mode_field_and_seed_hour_on_every_output(env, capsys, monkeypatch):
    write_tape(env / "tape", renamed_world())
    monkeypatch.setattr(P, "git", lambda *a: "deadbeef")
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": b"x", "returncode": 0})())
    P.main(["p0", "--count-only", "--out", str(env / "scratch" / "c"), "--from", hn(H0 + 30 * HOUR), "--to", hn(H0 + 33 * HOUR), "--seed-from-hour", "none", "--tape-src", str(env / "tape"),
            "--test-mode", "--now-ms", str(H0 + 400 * HOUR)])
    out = capsys.readouterr().out
    assert "test_mode=True" in out and "seed_hour=none" in out and "list_sha256=none" in out


# ------------------------------------------------------------------------------------------------ P2-only --seed-relaxed (QP-RULING-1113-floor-stage1-p2-vacuous)
def test_seed_relaxed_boundary_is_96_h_and_default_stays_201():
    seed = P.parse_hour("2026-11-03T01")
    now = P.parse_hour("2026-11-14T00")
    frm, to = seed + 96 * HOUR, seed + 120 * HOUR
    P.check_window(frm, to, seed, now, min_h=P.SEED_RELAXED_MIN_H)  # exactly 96 h: accepted
    with pytest.raises(SystemExit):
        P.check_window(frm - HOUR, to, seed, now, min_h=P.SEED_RELAXED_MIN_H)  # 95 h: refused
    with pytest.raises(SystemExit):
        P.check_window(frm, to, seed, now)  # without the relaxation 96 h is refused
    assert P.SEED_MIN_H == 201 and P.SEED_RELAXED_MIN_H == 96
    # the pre-declared windows: 119 h and 143 h from the frozen copy's seed
    for w, h in ((("2026-11-08T00", "2026-11-10T00"), 119), (("2026-11-09T00", "2026-11-11T00"), 143)):
        P.check_window(P.parse_hour(w[0]), P.parse_hour(w[1]), seed, now, min_h=P.SEED_RELAXED_MIN_H)
        assert (P.parse_hour(w[0]) - seed) // HOUR == h


def test_seed_relaxed_is_refused_by_p0_and_p1(env, capsys):
    write_tape(env / "tape", renamed_world())
    for cmd in ("p0", "p1"):
        with pytest.raises(SystemExit) as e:
            P.main(args(env, cmd, H0 + 30 * HOUR, H0 + 33 * HOUR, "o_" + cmd, "--seed-from-hour", hn(H0 - 300 * HOUR), "--seed-relaxed", "--count-only"))
        assert e.value.code == 2
        assert "p2 only" in capsys.readouterr().err
        assert not P.RELAXED


def test_seed_relaxed_needs_a_seed(env, capsys):
    write_tape(env / "tape", renamed_world())
    with pytest.raises(SystemExit):
        P.main(args(env, "p2", H0, H0 + 48 * HOUR, "o_ns", "--seed-relaxed", "--count-only"))
    assert "needs a seed" in capsys.readouterr().err


def _p2_relaxed_args(env, seed_h_before, out, *extra):
    return args(env, "p2", H0, H0 + 48 * HOUR, out, "--seed-from-hour", hn(H0 - seed_h_before * HOUR), *extra)


def test_seed_relaxed_95h_refused_96h_accepted_through_main(env, capsys, monkeypatch):
    write_tape(env / "tape", renamed_world())
    monkeypatch.setattr(P, "git", lambda *a: "deadbeef")
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": b"x", "returncode": 0})())
    with pytest.raises(SystemExit):
        P.main(_p2_relaxed_args(env, 95, "o95", "--seed-relaxed", "--count-only"))
    err = capsys.readouterr().err
    assert "less than 96 h" in err and "SEED_RELAXED" in err and "seed_h_before_from=95" in err
    P.main(_p2_relaxed_args(env, 96, "o96", "--seed-relaxed", "--count-only"))
    out = capsys.readouterr().out
    assert "COUNT-ONLY" in out and "SEED_RELAXED" in out and "seed_h_before_from=96" in out and "earliest_valid_from=%s" % hn(H0) in out
    with pytest.raises(SystemExit):  # without the flag, 96 h is far short of 201 h
        P.main(_p2_relaxed_args(env, 96, "o96b", "--count-only"))
    err = capsys.readouterr().err
    assert "less than 201 h" in err and "SEED_RELAXED" not in err
    assert not P.RELAXED


def test_e2e_p2_seed_relaxed_tags_every_line_and_default_has_no_tag(env, capsys):
    write_tape(env / "tape", renamed_world())
    rc = P.main(_p2_relaxed_args(env, 100, "orel", "--seed-relaxed", "--step-ms", "60000", "--horizon-s", "5400"))
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert rc == 0 and lines and lines[-1].startswith("P2 PASS")
    assert sum(ln.startswith("P2-CASE") for ln in lines) == 9
    for ln in lines:  # every output line of the run
        assert "SEED_RELAXED" in ln and "seed_h_before_from=100" in ln, ln
    # the same run without the flag (seed none, as the default tests): no tag anywhere
    rc = P.main(args(env, "p2", H0, H0 + 48 * HOUR, "odef", "--step-ms", "60000", "--horizon-s", "5400"))
    out = capsys.readouterr().out
    assert rc == 0 and "SEED_RELAXED" not in out and "seed_h_before_from" not in out
    assert not P.RELAXED


def test_pre_declared_text_of_the_ruling_is_in_the_script_header_and_runbook():
    for w in ("--seed-relaxed", "[2026-11-08T00Z, 2026-11-10T00Z)", "[2026-11-09T00Z, 2026-11-11T00Z)", "tapecopy-1113", "2026-11-03T01", "STOP and ask QP", "MUST NOT",
              "triggers of pools older than the seed", "AGE_DROP"):
        assert w in P.__doc__, w
    doc = (REPO / "docs" / "runbooks" / "floor-stage1-proof.md").read_text()
    for w in ("--seed-relaxed", "2026-11-08T00Z", "2026-11-09T00Z", "tapecopy-1113", "at the merged commit of this PR", "SEED_RELAXED", "seed_h_before_from", "Must not"):
        assert w in doc, w
