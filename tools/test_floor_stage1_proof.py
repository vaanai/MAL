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
    rec = {"seq_pre": 5, "seq_post": 6, "buys_pre": 1, "buys_post": 1, "blocked_pools": set()}
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
    assert rc == 0 and "P0 PASS" in out and "files_md5_differ=0" in out and "checkpoint_equal=True" in out, out
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
