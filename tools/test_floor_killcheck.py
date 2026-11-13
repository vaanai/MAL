"""Tests of scripts/ops/floor_killcheck_extract.py (step A) and scripts/ops/floor_killcheck.py (step B), the FLOOR kill-line check. SYNTHETIC tapes and decision logs only; nothing real is opened."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from tools.arena_rules_testkit import write_tape
from tools.test_arena_rule_l1 import H0, HOUR
from tools.test_floor_lane_host import _env  # noqa: F401 (autouse fixture)
from tools.test_floor_stage1_proof import renamed_world, PQ, PZ

REPO = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


X = load("floor_killcheck_extract", "scripts/ops/floor_killcheck_extract.py")
import sys  # noqa: E402
sys.path.insert(0, str(REPO / "scripts" / "ops"))
K = load("floor_killcheck", "scripts/ops/floor_killcheck.py")

SECRET_POOL = "SECRETPOOL1234567890abcdef"


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"))


def row(seq, ms, kind, **kw):
    return canon({"seq": seq, "arm": X.BOT_ID, "ms": ms, "prev": "0" * 64, "kind": kind, "sha": "f" * 64, **kw})


def entry(seq, ms, pool, trig=None, oid=None):
    return row(seq, ms, "entry", order_id=oid or "oid%d" % seq, pool=pool, meta={"signal_t_recv_ms": trig if trig is not None else ms - 400, "mark0": 1.23, "vol24": 99}, decision_slot=7, stake_sol=0.1, attempt_key=None)


def restart_row(seq, ms, complete=True):
    return row(seq, ms, "lane_restart_shadow", cheap_restart=True, shadow_complete=complete, fallback=None if complete else "unclean_exit", prev_end="signal", down_from_ms=ms - 600_000, down_to_ms=ms - 5_000,
               shadow_pools=[SECRET_POOL], n_shadow_buys=1)


DAY = "2026-11-12"
D0 = 1_794_441_600_000  # 2026-11-12T00:00:00Z


# ------------------------------------------------------------------------------------------------ step A
@pytest.fixture
def xenv(tmp_path, monkeypatch):
    monkeypatch.setenv("MAL_KILLCHECK_TEST", "1")
    root = tmp_path / "lane" / "decisions-root"
    d = root / "decisions" / X.BOT_ID
    d.mkdir(parents=True)
    cfg = tmp_path / "host.json"
    cfg.write_text(json.dumps({"root": str(root)}))
    lines = [row(0, D0 + 1000, "start"), entry(1, D0 + 3_600_000, SECRET_POOL), row(2, D0 + 3_700_000, "exit", order_id="oid1", pool=SECRET_POOL, reason="tp", price=3.3),
             row(3, D0 + 3_800_000, "entry_refused_gap", pool=SECRET_POOL, trigger_ms=1, reason="feed_gap"), restart_row(4, D0 + 4_000_000),
             restart_row(5, D0 + 4_100_000, complete=False), row(6, D0 + 4_200_000, "lane_restart_shadow_failed", why="x"),
             entry(7, D0 + 50_400_000, "POOL2", trig=D0 + 50_399_000), entry(8, D0 + 80_000_000, "POOL3")]
    (d / (DAY + ".jsonl")).write_bytes(("\n".join(lines) + "\n").encode())
    return tmp_path, root, cfg


def xargs(tmp, root, cfg, name="e.jsonl", frm=D0 + 3_000_000, to=D0 + 70_000_000, *extra):
    return ["--decision-root", str(root), "--config", str(cfg), "--out-base", str(tmp / "kc"), "--out", str(tmp / "kc" / name), "--from-ms", str(frm), "--to-ms", str(to), "--test-mode", *extra]


def test_extract_keeps_only_entry_rows_in_the_window_and_the_restart_rows(xenv, capsys):
    tmp, root, cfg = xenv
    assert X.main(xargs(tmp, root, cfg)) == 0
    out = capsys.readouterr().out
    assert SECRET_POOL not in out and "POOL2" not in out and "oid" not in out
    assert "n_entries=2" in out and "n_lane_restart_rows=2" in out and "n_cheap_restart_rows=1" in out
    body = (tmp / "kc" / "e.jsonl").read_bytes()
    assert hashlib.sha256(body).hexdigest() in out
    lines = [json.loads(x) for x in body.decode().splitlines()]
    h, ents = lines[0], lines[1:]
    assert h["n_entries"] == 2 and len(ents) == 2
    for e in ents:
        assert set(e) <= set(X.ENTRY_KEYS) and ("order_id" in e or "seq" in e)
    assert {e["pool"] for e in ents} == {SECRET_POOL, "POOL2"}  # POOL3 is after the window
    assert [r["shadow_complete"] for r in h["lane_starts"]] == [True, False]
    assert all(set(r) == set(X.RESTART_KEYS) for r in h["lane_starts"])
    assert h["day_files_sha256"][DAY + ".jsonl"] == hashlib.sha256((Path(root) / "decisions" / X.BOT_ID / (DAY + ".jsonl")).read_bytes()).hexdigest()
    text = body.decode()
    assert not any(w in text for w in ("price", "stake", "mark0", "vol24", "reason", "shadow_pools", "feed_gap"))


def test_extract_window_edges_and_torn_tail(xenv):
    tmp, root, cfg = xenv
    p = Path(root) / "decisions" / X.BOT_ID / (DAY + ".jsonl")
    p.write_bytes(p.read_bytes() + entry(9, D0 + 60_000_000, "POOL4").encode()[:-30])  # an unterminated last line of a kind we want
    assert X.main(xargs(tmp, root, cfg, "e2.jsonl", D0 + 50_400_000, D0 + 60_000_001)) == 0
    h = json.loads((tmp / "kc" / "e2.jsonl").read_text().splitlines()[0])
    assert h["n_entries"] == 1 and h["torn_tail_lines_dropped"] == 1  # [from, to): the entry at exactly `from` is in


def test_extract_refusals(xenv, tmp_path):
    tmp, root, cfg = xenv
    other = tmp / "other"
    other.mkdir()
    with pytest.raises(SystemExit):
        X.main(xargs(tmp, other, cfg))  # not the configured root
    assert X.main(xargs(tmp, root, cfg, "ok.jsonl")) == 0
    with pytest.raises(SystemExit):
        X.main(xargs(tmp, root, cfg, "ok.jsonl"))  # never overwrite
    with pytest.raises(SystemExit):
        X.main(["--decision-root", str(root) + "/../lane/decisions-root", "--config", str(cfg), "--out-base", str(tmp / "kc"), "--from-ms", "1", "--to-ms", "2", "--test-mode"])  # `..`
    link = tmp / "rootlink"
    os.symlink(root, link)
    cfg2 = tmp / "host2.json"
    cfg2.write_text(json.dumps({"root": str(link)}))
    with pytest.raises(SystemExit):
        X.main(xargs(tmp, link, cfg2, "l.jsonl"))  # symlinked root
    os.symlink(tmp / "kc", tmp / "kclink")
    with pytest.raises(SystemExit):
        X.main(["--decision-root", str(root), "--config", str(cfg), "--out-base", str(tmp / "kclink"), "--from-ms", "1", "--to-ms", "2", "--test-mode"])  # symlinked out dir
    with pytest.raises(SystemExit):
        X.main(xargs(tmp, root, cfg, "../x.jsonl"))  # `..` in --out
    with pytest.raises(SystemExit):
        X.main(xargs(tmp, root, cfg, "w.jsonl", 5, 5))  # empty window
    with pytest.raises(SystemExit):
        X.main(xargs(tmp, root, cfg, "w.jsonl", 1, 9 * 86_400_000))  # window over 7 days


def test_extract_symlinked_day_file_is_refused(xenv):
    tmp, root, cfg = xenv
    d = Path(root) / "decisions" / X.BOT_ID
    os.symlink(d / (DAY + ".jsonl"), d / "2026-11-13.jsonl")
    with pytest.raises(SystemExit):
        X.main(xargs(tmp, root, cfg, "s.jsonl", D0 + 80_000_000, D0 + 90_000_000))


def test_outside_test_mode_only_the_live_root_and_the_real_config(xenv, monkeypatch):
    tmp, root, cfg = xenv
    monkeypatch.delenv("MAL_KILLCHECK_TEST")
    with pytest.raises(SystemExit):
        X.main(xargs(tmp, root, cfg))  # --test-mode without the env
    with pytest.raises(SystemExit):
        X.main(["--decision-root", str(root), "--config", str(cfg), "--from-ms", "1", "--to-ms", "2"])  # --config is a test-mode option
    with pytest.raises(SystemExit):
        X.check_root(str(root), str(cfg), False)  # in the config but not the live root


def test_bot_id_and_block_ms_are_the_hosts():
    sys.path.insert(0, str(REPO))
    from tools import floor_lane_host as H
    assert X.BOT_ID == H.BOT_ID and K.S.BOT == H.BOT_ID and X.CONFIG == "/etc/mal-floor-lane/host.json" and X.LIVE_ROOT == "/var/lib/mal-live/floor-lane/decisions-root"


# ------------------------------------------------------------------------------------------------ step B, pure comparison
def ref_e(pool, ms, trig, oid):
    return {"kind": "entry", "pool": pool, "ms": ms, "order_id": oid, "meta": {"signal_t_recv_ms": trig}}


def ref_x(oid, ms):
    return {"kind": "exit", "order_id": oid, "ms": ms}


def live(pool, t, oid, trig=None):
    e = {"t_ms": t, "pool": pool, "order_id": oid}
    if trig is not None:
        e["trig_ms"] = trig
    return e


BLOCK = 100_000


def test_compare_match_pass_held_fail_and_info_counts():
    ref = [ref_e("a", 1000, 900, "ra"), ref_x("ra", 5000), ref_e("b", 2000, 1900, "rb")]
    rs = [{"t_ms": 500}]
    ok = K.compare(ref, [live("a", 1100, "l1", 900), live("b", 2100, "l2", 1900)], rs, BLOCK, 10_000)
    assert ok["n_live_entry_on_ref_held_pool_MUST0"] == 0 and ok["n_live_entries_after_restart"] == 2 and ok["n_live_entry_not_in_reference_INFO"] == 0
    held = K.compare(ref, [live("a", 3000, "bad", 2900)], rs, BLOCK, 10_000)  # a different trigger on a, inside the reference's position on a
    assert held["n_live_entry_on_ref_held_pool_MUST0"] == 1 and held["restarts"][0]["offender_ids"] == ["bad"] and held["n_live_entry_not_in_reference_INFO"] == 1
    free = K.compare(ref, [live("a", 6000, "after-exit", 5900)], rs, BLOCK, 10_000)  # after the reference exit: not held; information only
    assert free["n_live_entry_on_ref_held_pool_MUST0"] == 0 and free["n_live_entry_not_in_reference_INFO"] == 1
    never = K.compare(ref, [live("zz", 3000, "n1", 2900)], rs, BLOCK, 10_000)  # a pool the reference never entered: information, not the kill line
    assert never["n_live_entry_on_ref_held_pool_MUST0"] == 0 and never["n_live_entry_not_in_reference_INFO"] == 1
    open_ref = K.compare([ref_e("a", 1000, 900, "ra")], [live("a", 9000, "o", 8900)], rs, BLOCK, 10_000)  # no exit: held to the end
    assert open_ref["n_live_entry_on_ref_held_pool_MUST0"] == 1
    out = K.compare(ref, [live("a", 400, "before", 300), live("b", 2100, "late", 2000), live("a", 700_000, "far", 1)], rs, BLOCK, 2000)
    assert out["n_live_entries_after_restart"] == 0 and out["restarts"][0]["n_live_entries_beyond_reference_window_INFO"] == 1  # before the restart / beyond BLOCK_MS are not counted
    seq_only = K.compare([ref_e("a", 1000, 900, "ra")], [{"t_ms": 3000, "pool": "a", "seq": 41, "trig_ms": 2900}], rs, BLOCK, 10_000)
    assert seq_only["restarts"][0]["offender_ids"] == ["seq:41"]


def test_compare_without_a_trigger_uses_a_two_second_tolerance():
    ref = [ref_e("a", 1000, 900, "ra"), ref_x("ra", 5000)]
    same = K.compare(ref, [live("a", 2500, "l")], [{"t_ms": 500}], BLOCK, 10_000)  # 1.5 s after the reference entry: the same entry, not held
    assert same["n_live_entry_on_ref_held_pool_MUST0"] == 0
    other = K.compare(ref, [live("a", 4000, "l")], [{"t_ms": 500}], BLOCK, 10_000)
    assert other["n_live_entry_on_ref_held_pool_MUST0"] == 1


# ------------------------------------------------------------------------------------------------ step B, end to end on a synthetic tape
def hn(ms):
    return K.S.hour_name(ms)


@pytest.fixture
def kenv(tmp_path, monkeypatch):
    monkeypatch.setenv("MAL_ARENA_TEST", "1")
    monkeypatch.setattr(K, "OUT_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setattr(K, "IN_ROOT", str(tmp_path / "scratch" / "in"))
    monkeypatch.setattr(K.S.TP, "ALLOWED_ROOTS", (str(tmp_path / "tape"),))
    (tmp_path / "scratch" / "in").mkdir(parents=True)
    write_tape(tmp_path / "tape", renamed_world())
    return tmp_path


def put_extract(tmp, entries, restarts, name="x.jsonl", to_ms=H0 + 48 * HOUR):
    h = {"kind": "header", "schema": K.SCHEMA, "bot_id": X.BOT_ID, "from_ms": H0 + 30 * HOUR, "to_ms": to_ms, "n_entries": len(entries), "lane_starts": restarts, "day_files_sha256": {}}
    body = "".join(canon(x) + "\n" for x in [h] + entries).encode()
    p = tmp / "scratch" / "in" / name
    p.write_bytes(body)
    return p, hashlib.sha256(body).hexdigest()


def kargs(tmp, p, sha, out="r1", *extra):
    return ["--out", str(tmp / "scratch" / out), "--extract", str(p), "--extract-sha256", sha, "--seed-from-hour", "none", "--tape-src", str(tmp / "tape"), "--test-mode", "--now-ms", str(H0 + 400 * HOUR),
            "--step-ms", "60000", *extra]


def reference(tmp):
    """The reference's own entries on the synthetic tape, learned by running the same function the check runs (same window as main derives for RESTART)."""
    frm = K.floor_h(min(RESTART["t_ms"], RESTART["down_from_ms"]) - 2 * K_BLOCK())
    (tmp / "scratch" / "learn").mkdir(exist_ok=True)
    rows = K.run_reference(str(tmp / "scratch" / "learn"), [str(tmp / "tape")], None, frm, H0 + 48 * HOUR, 60_000, True)
    return [r for r in rows if r.get("kind") == "entry"], rows


def K_BLOCK():
    sys.path.insert(0, str(REPO))
    from tools import floor_lane_host as H
    return int(H.BLOCK_MS)


RESTART = {"t_ms": H0 + 31 * HOUR, "prev_end": "signal", "fallback": None, "shadow_complete": True, "down_from_ms": H0 + 30 * HOUR + 3_000_000, "down_to_ms": H0 + 31 * HOUR - 5000}


def live_from_ref(r, k=0, suffix=""):
    return {"t_ms": int(r["ms"]), "pool": r["pool"], "order_id": "L" + str(r["order_id"]) + suffix, "trig_ms": (r.get("meta") or {}).get("signal_t_recv_ms")}


def test_e2e_live_entries_equal_to_reference_entries_pass(kenv, capsys):
    ents, _ = reference(kenv)
    after = [r for r in ents if r["ms"] >= RESTART["t_ms"]]
    assert after, "the synthetic world must hold a reference entry after the restart"
    p, sha = put_extract(kenv, [live_from_ref(r) for r in after], [RESTART])
    rc = K.main(kargs(kenv, p, sha))
    out = capsys.readouterr().out
    assert rc == 0 and "KILLCHECK PASS" in out and "n_live_entry_on_ref_held_pool_MUST0=0" in out and "KILLCHECK_SMOKE" not in out
    assert PQ not in out and PZ not in out
    res = json.loads((kenv / "scratch" / "r1" / "killcheck-KILLCHECK.json").read_text())
    assert res["n_cheap_restarts_checked"] == 1 and res["n_live_entries_after_restart"] == len(after) and res["n_live_entry_not_in_reference_INFO"] == 0
    assert not (kenv / "scratch" / "r1" / "ref-work").exists()  # the reference's decision log (pool ids) is deleted


def test_e2e_planted_entry_on_a_pool_the_reference_holds_fails_with_order_id_and_no_pool(kenv, capsys):
    ents, _ = reference(kenv)
    after = [r for r in ents if r["ms"] >= RESTART["t_ms"]]
    target = after[0]
    planted = {"t_ms": int(target["ms"]) + 120_000, "pool": target["pool"], "order_id": "PLANTED-OID", "trig_ms": int(target["ms"]) + 119_000}
    p, sha = put_extract(kenv, [live_from_ref(target), planted], [RESTART])
    rc = K.main(kargs(kenv, p, sha))
    out = capsys.readouterr().out
    assert rc == 1 and "KILLCHECK FAIL" in out and "PLANTED-OID" in out and "n_live_entry_on_ref_held_pool_MUST0=1" in out
    assert target["pool"] not in out and PQ not in out


def test_e2e_vacuous_without_a_cheap_restart_or_without_an_entry_after_one(kenv, capsys):
    ents, _ = reference(kenv)
    e = [live_from_ref(r) for r in ents if r["ms"] >= RESTART["t_ms"]]
    p, sha = put_extract(kenv, e, [dict(RESTART, shadow_complete=False, fallback="unclean_exit")], "a.jsonl")
    assert K.main(kargs(kenv, p, sha, "r2")) == 3
    assert "no cheap restart" in capsys.readouterr().err
    p, sha = put_extract(kenv, [], [RESTART], "b.jsonl")
    assert K.main(kargs(kenv, p, sha, "r3")) == 3
    assert "VACUOUS" in capsys.readouterr().err


def test_extract_guards_in_step_b(kenv):
    ents, _ = reference(kenv)
    p, sha = put_extract(kenv, [live_from_ref(ents[0])], [RESTART])
    with pytest.raises(SystemExit):
        K.main(kargs(kenv, p, "0" * 64, "r4"))  # sha256 mismatch
    elsewhere = kenv / "x.jsonl"
    elsewhere.write_bytes(p.read_bytes())
    with pytest.raises(SystemExit):
        K.main(kargs(kenv, elsewhere, sha, "r5"))  # not under the in dir
    link = kenv / "scratch" / "in" / "l.jsonl"
    os.symlink(p, link)
    with pytest.raises(SystemExit):
        K.main(kargs(kenv, link, sha, "r6"))  # symlink
    with pytest.raises(SystemExit):
        K.main(kargs(kenv, str(kenv / "scratch" / "in" / ".." / "in" / "x.jsonl"), sha, "r7"))  # `..`
    body = p.read_bytes().decode().splitlines()
    fat = json.loads(body[1])
    fat["price"] = 3.3
    bad = ("\n".join([body[0], canon(fat)]) + "\n").encode()
    q = kenv / "scratch" / "in" / "fat.jsonl"
    q.write_bytes(bad)
    with pytest.raises(SystemExit):
        K.main(kargs(kenv, q, hashlib.sha256(bad).hexdigest(), "r8"))  # a key outside the allowed set
    with pytest.raises(SystemExit):
        K.main(kargs(kenv, p, sha, "r1x", "--tape-src", "/etc"))  # a tape source outside the allowed roots
    (kenv / "scratch" / "r9").mkdir()
    with pytest.raises(SystemExit):
        K.main(kargs(kenv, p, sha, "r9"))  # the out dir exists: never overwrite


def test_smoke_label_and_restart_override_needs_smoke(kenv, capsys):
    ents, _ = reference(kenv)
    first = [r for r in ents if r["ms"] >= RESTART["t_ms"]][0]
    p, sha = put_extract(kenv, [live_from_ref(first)], [], "s.jsonl")  # the extract has no restart row at all
    with pytest.raises(SystemExit):
        K.main(kargs(kenv, p, sha, "s1", "--restart-ms", str(RESTART["t_ms"])))  # override without --smoke
    rc = K.main(kargs(kenv, p, sha, "s2", "--restart-ms", str(RESTART["t_ms"]), "--smoke"))
    out = capsys.readouterr().out
    assert rc == 0 and "KILLCHECK_SMOKE PASS" in out and "KILLCHECK PASS" not in out and "SMOKE-INPUT" in out and "restart_source=override" in out
    assert "n_live_entries_after_restart=1" in out and "n_live_entry_on_ref_held_pool_MUST0=0" in out
    assert (kenv / "scratch" / "s2" / "killcheck-SMOKE.json").exists() and not (kenv / "scratch" / "s2" / "killcheck-KILLCHECK.json").exists()


def test_count_only_runs_no_reference(kenv, capsys):
    ents, _ = reference(kenv)
    p, sha = put_extract(kenv, [live_from_ref(ents[0])], [RESTART])
    assert K.main(kargs(kenv, p, sha, "c1", "--count-only")) == 0
    out = capsys.readouterr().out
    assert "KILLCHECK-INPUT" in out and "KILLCHECK-TAPE" in out and not (kenv / "scratch" / "c1").exists()


# ------------------------------------------------------------------------------------------------ changes since 079a8959 (QP B1 and the smaller items)
def test_b1_a_reference_that_starts_one_block_before_the_restart_misses_a_hold_the_two_block_start_sees(kenv, capsys, monkeypatch):
    """The synthetic reference enters pool Q at 32 h and holds it to 56 h. With BLOCK_MS cut to 2 h and a restart at 35 h: a start at 33 h (the old 1 x BLOCK start) has an empty book, never
    sees that entry, and a live entry on Q at 35.5 h (another trigger) passes; the 2 x BLOCK start (31 h) sees the hold and the same live entry FAILS."""
    sys.path.insert(0, str(REPO))
    from tools import floor_lane_host as H
    monkeypatch.setattr(H, "BLOCK_MS", 2 * HOUR)
    r35 = H0 + 35 * HOUR
    restart = {"t_ms": r35, "prev_end": "signal", "fallback": None, "shadow_complete": True, "down_from_ms": None, "down_to_ms": None}
    planted = {"t_ms": r35 + 1_800_000, "pool": PQ, "order_id": "B1-PLANTED", "trig_ms": r35 + 1_799_000}
    (kenv / "scratch" / "l1").mkdir()
    old = K.run_reference(str(kenv / "scratch" / "l1"), [str(kenv / "tape")], None, H0 + 33 * HOUR, H0 + 37 * HOUR, 60_000, True)
    assert K.compare(old, [planted], [restart], 2 * HOUR, H0 + 37 * HOUR)["n_live_entry_on_ref_held_pool_MUST0"] == 0  # the old start would have PASSED
    p, sha = put_extract(kenv, [planted], [restart], "b1.jsonl", to_ms=H0 + 37 * HOUR)
    rc = K.main(kargs(kenv, p, sha, "b1"))
    out = capsys.readouterr().out
    assert rc == 1 and "KILLCHECK FAIL" in out and "B1-PLANTED" in out
    assert json.loads((kenv / "scratch" / "b1" / "killcheck-KILLCHECK.json").read_text())["tape_info"]["ref_from"] == hn(H0 + 31 * HOUR)
    with pytest.raises(SystemExit):  # a later start is refused outside smoke
        K.main(kargs(kenv, p, sha, "b2", "--ref-from", hn(H0 + 33 * HOUR)))
    assert K.main(kargs(kenv, p, sha, "b3", "--ref-from", hn(H0 + 33 * HOUR), "--smoke")) != 1  # smoke may start later (a plumbing test only)


def test_every_restart_row_is_printed_with_checked_and_the_not_shown_line(kenv, capsys):
    ents, _ = reference(kenv)
    e = [live_from_ref(r) for r in ents if r["ms"] >= RESTART["t_ms"]]
    fb = dict(RESTART, t_ms=H0 + 20 * HOUR, shadow_complete=False, fallback="unclean_exit", prev_end="exception")
    p, sha = put_extract(kenv, e, [fb, RESTART])
    assert K.main(kargs(kenv, p, sha)) == 0
    out = capsys.readouterr().out
    assert "KILLCHECK-ROW row=1" in out and "fallback=unclean_exit" in out and "checked=no" in out and "checked=yes" in out
    assert "KILLCHECK-NOT-SHOWN" in out and "backstop, not a proof" in out and "re-entry" in out and "live staleness" in out and "after the reference window end" in out


def test_smoke_flags_a_live_entry_the_reference_does_not_reproduce(kenv, capsys):
    ents, _ = reference(kenv)
    first = [r for r in ents if r["ms"] >= RESTART["t_ms"]][0]
    stray = {"t_ms": int(first["ms"]) + 7_000, "pool": "POOLNEVER", "order_id": "STRAY", "trig_ms": int(first["ms"]) + 6_000}
    p, sha = put_extract(kenv, [live_from_ref(first), stray], [], "n.jsonl")
    rc = K.main(kargs(kenv, p, sha, "n1", "--restart-ms", str(RESTART["t_ms"]), "--smoke", "--ref-from", hn(H0 + 6 * HOUR)))
    out = capsys.readouterr().out
    assert rc == 4 and "REFERENCE DOES NOT REPRODUCE LIVE DECISIONS: ask QP" in out and "KILLCHECK_SMOKE PASS" not in out and "POOLNEVER" not in out


def test_delete_extract_after_removes_only_the_research_copy_after_the_result(kenv, capsys):
    ents, _ = reference(kenv)
    p, sha = put_extract(kenv, [live_from_ref(r) for r in ents if r["ms"] >= RESTART["t_ms"]], [RESTART])
    assert K.main(kargs(kenv, p, sha, "d1", "--delete-extract-after")) == 0
    assert not p.exists() and (kenv / "scratch" / "d1" / "killcheck-KILLCHECK.json").exists()
    q, sq = put_extract(kenv, [], [RESTART], "d2.jsonl")
    assert K.main(kargs(kenv, q, sq, "d2", "--delete-extract-after")) == 3 and not q.exists()  # a vacuous result is a recorded result


def test_extract_counts_failed_restart_rows_writes_0600_and_refuses_symlinked_subdirs(xenv, capsys):
    tmp, root, cfg = xenv
    assert X.main(xargs(tmp, root, cfg, "m.jsonl")) == 0
    out = capsys.readouterr().out
    assert "n_restart_shadow_failed=1" in out
    assert (tmp / "kc" / "m.jsonl").stat().st_mode & 0o777 == 0o600
    h = json.loads((tmp / "kc" / "m.jsonl").read_text().splitlines()[0])
    assert h["n_restart_shadow_failed"] == 1 and "why" not in canon(h)
    d = Path(root) / "decisions"
    real = d.with_name("decisions-real")
    d.rename(real)
    os.symlink(real, d)
    with pytest.raises(SystemExit):
        X.main(xargs(tmp, root, cfg, "m2.jsonl"))


def test_wrappers_hardcode_the_push_target_and_set_modes():
    sh = (REPO / "scripts" / "ops" / "floor_killcheck_extract.sh").read_text()
    assert "SSH_HOST=mal-research-0" in sh and "F0640" in sh and "chmod 0750" in sh and "SSH_HOST:-" not in sh
