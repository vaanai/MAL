"""Tests of tools/r4bt_c0_executor.py (the r4-b-T profile of the DRY executor). Synthetic fixtures only: no tape, no declog, no key, no network.

  /data/mal/venv/bin/python -m pytest tools/test_r4bt_c0_executor.py
"""

from __future__ import annotations

import ast
import collections
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import arena_live_bridge as B
from tools import hv4_live_c0 as HV
from tools import r4b_arena_executor as X
from tools import r4bt_c0_executor as T
from tools.arena_declog import DecisionLog

W = T.REGISTERED_W
CID = T.REGISTERED_CHANGE_IDS[0]
BT0 = int(datetime(2026, 8, 14, tzinfo=timezone.utc).timestamp())
TM0 = BT0 + 2 * 3600
V = 17_600_000_000
SD0 = 438_000_000
SHA_READ = "cd" * 32
QP_ID = "6700000001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


class Clock:
    def __init__(self, t=0):
        self.t = t

    def __call__(self):
        return self.t


def mark(i):
    return TM0 + 3600 * i


def ms(i, dt=1200):
    return mark(i) * 1000 + dt


def iso(t_ms):
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jwrite(p, obj, lines=False):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(("\n".join(json.dumps(o) for o in obj) + "\n") if lines else json.dumps(obj))
    os.chmod(p, 0o644)
    return p


class World:
    """A tmp world: synthetic decision root, twin dir, carry, applied log, read record, owner file, LIVE_OK, TIER; all fresh around `at`."""

    def __init__(self, tmp, synthetic=True, **over):
        self.tmp = tmp
        self.root = tmp / "rules"
        self.root.mkdir()
        if synthetic:
            (self.root / HV.SYNTHETIC_MARKER).write_text("x")
        self.at = ms(0) + 50
        self.d = {"root": str(self.root), "state_dir": str(tmp / "state"), "bot_id": T.BOT_ID, "frozen_sha": W, "change_id": CID, "end_ms": (BT0 + 400 * 86400) * 1000,
                  "slot_s": 0.4, "funded_lamports": 350_000_000, "twin_dir": str(tmp / "twin"), "a3_carry_path": str(tmp / "a3" / "carry.json"),
                  "a3_applied_path": str(tmp / "a3" / "a3-applied.jsonl"), "read_record_path": str(tmp / "read.json"), "read_result_sha256": SHA_READ, "qp_comment_id": QP_ID,
                  "owner_file": str(tmp / "owner.md"), "live_ok_path": str(tmp / "LIVE_OK"), "tier_path": str(tmp / "TIER"), **T.C0_CAPS}
        self.d.update(over)
        self.log = DecisionLog(str(self.root))
        self.fresh()

    def fresh(self, *, carry=None, applied=None, run=None, rows=None):
        t = self.tmp
        run_utc = iso(self.at - 3_600_000)
        jwrite(t / "a3" / "carry.json", {"schema": T.CARRY_SCHEMA, "bot_id": T.BOT_ID, "change_id": CID, "run_utc": run_utc, "verdict": "NONE", "halt_at_ms": None, "deadline_ms": None,
                                         **(carry or {})})
        jwrite(t / "a3" / "a3-applied.jsonl", [{"run_utc": run_utc, "verdict": "PASS", **(applied or {})}], lines=True)
        jwrite(t / "twin" / T.TWINS_NAME, rows or [], lines=True)
        sha = hashlib.sha256((t / "twin" / T.TWINS_NAME).read_bytes()).hexdigest()
        jwrite(t / "twin" / T.RUN_RECORD_NAME, {"schema": T.RUN_SCHEMA, "tool": "r4bt_c0_twin", "status": "ok", "run_ms": self.at - 1000, "heartbeat_ms": self.at - 1000, "w_sha": W,
                                                 "engine_sha256": T.ENGINE_SHA256, "twins_sha256": sha, **(run or {})})
        jwrite(t / "read.json", {"schema": T.READ_SCHEMA, "bot_id": T.BOT_ID, "change_id": CID, "status": "PASS", "read_result_sha256": SHA_READ, "qp_comment_id": QP_ID,
                                 "qp_verdict": "OK"})
        (t / "owner.md").write_text(f"OWNER_CANARY_CONFIRMED: bot_id={T.BOT_ID}\n")
        (t / "LIVE_OK").write_text(T.BOT_ID + "\n")
        (t / "TIER").write_text("T1\n")
        for f in ("owner.md", "LIVE_OK", "TIER"):
            os.chmod(t / f, 0o644)

    def cfg(self):
        return T.make_config(self.d)

    def bridge(self, clk=None, port=None):
        clk = clk or Clock(self.at)
        cfg = self.cfg()
        port = port or B.DryPort(cfg.dry_balance_lamports)
        return T.R4btBridge(cfg, port, now_ms=clk, root_checks=False), port, clk

    def start(self, sha=W, change_id=CID):
        self.log.append(T.BOT_ID, mark(0) * 1000, {"kind": "start", "book": "r4-b-T", "mode": "dry", "frozen_sha": sha, "change_id": change_id})

    def entry(self, i, mint, rank_, dt=1200):
        tm = mark(i)
        self.log.append(T.BOT_ID, tm * 1000 + dt, {"kind": "entry", "order_id": f"{mint}:{tm}", "pool": "PDA_" + mint, "meta": {"mint": mint, "v0": V, "mbt": tm - 7200, "s0": 1},
                                                  "stake_sol": 0.25, "decision_slot": SD0 + 9000 * i, "t_mark": tm, "rank": rank_, "score": 0.5})


def feed(br, clk, at=None):
    rows = br.read_new_rows()
    for r in rows:
        clk.t = r["ms"] + 50 if at is None else at
        br.handle_row(r)
        br._poll_ports()
    br._save()
    return rows


def sends(port):
    return [(w["side"], w["order_id"]) for w in port.would_send]


def run_one(tmp_path, **mut):
    """One start row + one entry, the bridge fed once; returns (bridge, port, world)."""
    w = World(tmp_path)
    if mut:
        w.fresh(**mut)
    w.start()
    w.entry(0, "MINT", 0)
    br, port, clk = w.bridge()
    feed(br, clk)
    return br, port, w


# ------------------------------------------------------------------------------------------------ constants and config
def test_pins_and_caps_follow_the_dec_and_the_other_modules():
    assert T.LIVE_BUILT is False and T.BOT_ID == "r4bt-a1"
    assert T.C0_CAPS == X.C0 == HV.C0_LIMITS == {"stake_lamports": 50_000_000, "max_open": 2, "max_trades_per_day": 30, "daily_loss_lamports": 100_000_000,
                                                 "total_loss_lamports": 200_000_000}
    assert (T.FUNDED_MIN_LAMPORTS, T.FUNDED_MAX_LAMPORTS) == (350_000_000, 1_000_000_000)
    assert T.A3_MAX_AGE_MS == 36 * 3_600_000 and HV.A3_GUARD_VERDICTS == ("NONE", "PASS", "HALT", "FAIL", "ERROR")
    assert T.ENGINE_SHA256 == "9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514" and len(W) == 40


def test_good_config_and_exp027_path_unchanged(tmp_path):
    cfg = World(tmp_path).cfg()
    assert isinstance(cfg, T.R4btConfig) and cfg.bot_id == "r4bt-a1" and cfg.frozen_sha == W and cfg.change_id == CID and cfg.mode == "dry"
    assert (cfg.stake_lamports, cfg.max_open, cfg.max_trades_per_day, cfg.daily_loss_lamports, cfg.total_loss_lamports) == tuple(T.C0_CAPS.values())
    assert X.BOT_ID == "r4b-exp027" and X.LIVE_BUILT is False
    x = X.make_config({"root": str(tmp_path / "r"), "state_dir": str(tmp_path / "s"), "frozen_sha": "ab" * 20, "end_ms": 10 ** 13, "slot_s": 0.4})
    assert x.bot_id == "r4b-exp027" and type(x) is X.R4bConfig
    with pytest.raises(ValueError):  # EXP-027's own path refuses r4bt-a1
        X.make_config({"root": str(tmp_path / "r"), "state_dir": str(tmp_path / "s"), "frozen_sha": W, "end_ms": 10 ** 13, "slot_s": 0.4, "bot_id": T.BOT_ID})


@pytest.mark.parametrize("bad", [
    {"mode": "live"}, {"bot_id": "r4b-exp027"}, {"bot_id": None}, {"frozen_sha": "ab" * 20}, {"frozen_sha": None}, {"change_id": "r4bt-a1-other"}, {"change_id": None},
    {"change_id": "x/y"}, {"stake_lamports": 50_000_001}, {"max_open": 3}, {"max_trades_per_day": 31}, {"daily_loss_lamports": 100_000_001},
    {"total_loss_lamports": 200_000_001}, {"stake_lamports": 0}, {"max_open": True}, {"funded_lamports": 349_999_999}, {"funded_lamports": 1_000_000_001},
    {"funded_lamports": None}, {"twin_dir": "rel/dir"}, {"a3_carry_path": ""}, {"a3_applied_path": None}, {"read_record_path": "r.json"},
    {"read_result_sha256": "xyz"}, {"read_result_sha256": "CD" * 32}, {"qp_comment_id": "abc"}, {"qp_comment_id": ""}, {"sl_backstop": 0.3}, {"slot_s": 0}, {"end_ms": None}])
def test_make_config_refusals(tmp_path, bad):
    with pytest.raises(T.Refused):
        World(tmp_path, **bad).cfg()


@pytest.mark.parametrize("key", list(T.C0_CAPS) + ["funded_lamports", "twin_dir", "a3_carry_path", "a3_applied_path", "read_record_path", "read_result_sha256", "qp_comment_id",
                                                    "change_id"])
def test_make_config_requires_every_profile_key(tmp_path, key):
    w = World(tmp_path)
    w.d.pop(key)
    with pytest.raises(T.Refused):
        w.cfg()


def test_lower_caps_are_kept(tmp_path):
    assert World(tmp_path, stake_lamports=10_000_000).cfg().stake_lamports == 10_000_000


# ------------------------------------------------------------------------------------------------ the happy path and the pins
def test_happy_path_sends_a_dry_buy(tmp_path):
    br, port, _ = run_one(tmp_path)
    assert sends(port) == [("buy", f"MINT:{mark(0)}")] and not br.state.latches


def test_bridge_refuses_a_foreign_config_and_live(tmp_path):
    w = World(tmp_path)
    exp = X.make_config({"root": str(tmp_path / "r"), "state_dir": str(tmp_path / "s"), "frozen_sha": W, "end_ms": 10 ** 13, "slot_s": 0.4})
    with pytest.raises(SystemExit):
        T.R4btBridge(exp, B.DryPort(1), now_ms=Clock(1), root_checks=False)
    with pytest.raises(SystemExit):
        T.R4btBridge(w.cfg(), B.DryPort(1), now_ms=Clock(1), root_checks=False, dry_replay_caps=True)
    port = B.DryPort(1)
    port.live = True
    with pytest.raises(SystemExit):
        T.R4btBridge(w.cfg(), port, now_ms=Clock(1), root_checks=False)


def test_start_row_with_another_change_id_latches_and_no_buy(tmp_path):
    w = World(tmp_path)
    w.start(change_id="r4bt-a1-zzz")
    w.entry(0, "MINT", 0)
    br, port, clk = w.bridge()
    feed(br, clk)
    assert T.LATCH_PIN in br.state.latches and sends(port) == []


def test_start_row_with_another_w_latches_and_no_buy(tmp_path):
    w = World(tmp_path)
    w.start(sha="ee" * 20)
    w.entry(0, "MINT", 0)
    br, port, clk = w.bridge()
    feed(br, clk)
    assert "frozen_sha_mismatch" in br.state.latches and sends(port) == []


def test_start_row_without_change_id_latches(tmp_path):
    w = World(tmp_path)
    w.log.append(T.BOT_ID, mark(0) * 1000, {"kind": "start", "book": "r4-b-T", "mode": "dry", "frozen_sha": W})
    w.entry(0, "MINT", 0)
    br, port, clk = w.bridge()
    feed(br, clk)
    assert T.LATCH_PIN in br.state.latches and sends(port) == []


# ------------------------------------------------------------------------------------------------ F1: no round_trip, twins from the writer only
def test_twin_round_trip_is_refused(tmp_path):
    with pytest.raises(T.RouteRefused):
        T.twin_round_trip(1.0, 2.0, 3.0, 4.0, 5.0, stake_lamports=1)
    br, _, _ = World(tmp_path).bridge()
    with pytest.raises(T.RouteRefused):
        br.twin_round_trip(1.0)
    assert br.twin_round_trip is T.twin_round_trip  # not EXP-027's r4b_book.round_trip route


def test_module_never_imports_or_calls_r4b_book_round_trip():
    tree = ast.parse(Path(T.__file__).read_text())
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            assert all(a.name != "tools.r4b_book" for a in n.names)
        if isinstance(n, ast.ImportFrom):
            assert n.module != "tools.r4b_book" and not (n.module == "tools" and any(a.name == "r4b_book" for a in n.names))
        if isinstance(n, ast.Call):
            f = n.func
            assert not (isinstance(f, ast.Attribute) and f.attr == "round_trip") and not (isinstance(f, ast.Name) and f.id == "round_trip")


@pytest.mark.parametrize("run,label", [
    ({"run_ms": 1}, "twin_gate:stale"), ({"run_ms": None}, "twin_gate:heartbeat_missing"), ({"run_ms": 10 ** 15}, "twin_gate:heartbeat_future"),
    ({"w_sha": "ee" * 20}, "twin_gate:pin_mismatch"), ({"engine_sha256": "00" * 32}, "twin_gate:pin_mismatch"), ({"status": "error"}, "twin_gate:status_not_ok"),
    ({"twins_sha256": "00" * 32}, "twin_gate:twins_sha_mismatch"), ({"schema": "other"}, "twin_gate:run_record_schema")])
def test_twin_gate_refuses_a_bad_run_record(tmp_path, run, label):
    br, port, w = run_one(tmp_path, run=run)
    assert sends(port) == [] and br.state.counts.get("twin_gate_refusal") == 1
    assert T.twin_gate_problem(w.cfg().twin_dir, w.at, {}, w.cfg(), False) == label
    assert (T.LATCH_TWIN_PIN in br.state.latches) == (label == "twin_gate:pin_mismatch")


def test_twin_gate_missing_malformed_and_unsafe_run_record(tmp_path):
    w = World(tmp_path)
    cfg = w.cfg()
    rec = Path(cfg.twin_dir) / T.RUN_RECORD_NAME
    assert T.twin_gate_problem(cfg.twin_dir, w.at, {}, cfg, False) is None
    rec.unlink()
    assert T.twin_gate_problem(cfg.twin_dir, w.at, {}, cfg, False) == "twin_gate:twin_run_record_missing"
    w.fresh()
    rec.write_text("{not json")
    assert T.twin_gate_problem(cfg.twin_dir, w.at, {}, cfg, False) == "twin_gate:twin_run_record_malformed"
    w.fresh()
    os.chmod(rec, 0o666)
    assert T.twin_gate_problem(cfg.twin_dir, w.at, {}, cfg, False) == "twin_gate:twin_run_record_owner_mode"
    w.fresh()
    assert T.twin_gate_problem(cfg.twin_dir, w.at, {}, cfg, True) == "twin_gate:twin_run_record_owner_mode"  # root_checks: a user-owned file is not root's
    link = Path(cfg.twin_dir) / "link.json"
    link.symlink_to(rec)
    assert T._read_small(link, False)[1] == "not_regular"


def test_twin_gate_row_missing_after_grace_and_present(tmp_path):
    w = World(tmp_path)
    cfg = w.cfg()
    orders = {"A:1": {"status": "closed", "sell_landed_ms": w.at - T.TWIN_GRACE_MS - 1}, "B:1": {"status": "closed", "sell_landed_ms": w.at - 5}, "C:1": {"status": "open"}}
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) == "twin_gate:row_missing"  # A:1 is past the grace with no row
    w.fresh(rows=[{"schema": T.TWIN_SCHEMA, "order_id": "A:1"}])
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) is None  # B:1 is inside the grace; C:1 is open
    w.fresh(rows=[{"schema": "wrong", "order_id": "A:1"}, {"order_id": 5}])
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) == "twin_gate:row_missing"  # rows of another schema are not twins
    (Path(cfg.twin_dir) / T.TWINS_NAME).unlink()
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) == "twin_gate:twins_unreadable"


# ------------------------------------------------------------------------------------------------ F2: the latency origin is the decision row's ms
def test_latency_origin_is_the_decision_row_instant_with_sdT_beside():
    o = {"decision_ms": 10_000, "buy_landed_ms": 12_000, "decision_slot": 100, "buy_landed_slot": 103}  # 2.0 s after the row; sd(T) says 3 slots
    r = T.landed_buy_latencies_t({"a": o}, 0.4)[0]
    assert r["lat_s"] == 2.0 and r["slots"] == pytest.approx(5.0) and r["sdT_slots"] == 3
    assert (r["k19"], r["k30"]) == (X.k_slots(1.9, 0.4), X.k_slots(3.0, 0.4))
    assert r["x19"] == pytest.approx(r["slots"] - r["k19"]) and r["x30"] == pytest.approx(r["slots"] - r["k30"])
    s = T.latency_summary_t({"a": o}, 0.4)
    assert s["origin"].startswith("decision row ms") and s["diag_sdT"]["all_slots_p50"] == 3 and s["all_slots_p50"] == pytest.approx(5.0)
    base = X.landed_buy_latencies({"a": {**o, "buy_landed_ms": 12_000}}, 0.4)[0]
    assert base["slots"] == 3 and base["slots"] != r["slots"]  # EXP-027's sd(T) origin would decide on another number


def test_latency_decides_on_the_decision_ms_not_sdT():
    # 20 landed buys: sd(T) latency 1 slot (passes), decision-ms latency 4.0 s (above 3.0 s): the P5 soft halt only under the r4-b-T origin
    orders = {f"o{i}": {"decision_ms": 1000 * i, "buy_landed_ms": 1000 * i + 4000, "decision_slot": 10 * i, "buy_landed_slot": 10 * i + 1} for i in range(20)}
    chk = X.latency_check(T.latency_summary_t(orders, 0.4))
    assert chk["enough"] and chk["p5_soft_halt"] and not chk["match_latency_ok"]
    assert not X.latency_check(X.latency_summary(orders, 0.4))["p5_soft_halt"]


def test_latency_unmeasured_when_landing_precedes_decision():
    r = T.landed_buy_latencies_t({"a": {"decision_ms": 5000, "buy_landed_ms": 4000}, "b": {"buy_landed_ms": 4000}}, 0.4)
    assert [x["slots"] for x in r] == [None, None]
    assert T.latency_summary_t({"a": {"decision_ms": 5000, "buy_landed_ms": 4000}}, 0.4)["measured"] == 0


def test_bridge_latency_report_uses_the_r4bt_origin(tmp_path):
    br, _, _ = World(tmp_path).bridge()
    br.state.orders["a"] = {"decision_ms": 1000, "buy_landed_ms": 3000, "decision_slot": 1, "buy_landed_slot": 2, "status": "open"}
    rep = br.latency_report()
    assert rep["summary"]["origin"].startswith("decision row ms") and rep["summary"]["diag_sdT"]["measured"] == 1


# ------------------------------------------------------------------------------------------------ A3: carry (item 12) and the Am.3 guard
@pytest.mark.parametrize("carry,label", [
    ({"verdict": "HALT", "halt_at_ms": 1}, "a3_carry:HALT"), ({"verdict": "ERROR"}, "a3_carry:ERROR"), ({"verdict": "MAYBE"}, "a3_carry_verdict_unknown"),
    ({"bot_id": "r4b-exp027"}, "a3_carry_identity"), ({"change_id": "x"}, "a3_carry_identity"), ({"schema": "s"}, "a3_carry_identity"),
    ({"run_utc": "2026-08-12T00:00:00Z"}, "a3_carry_stale"), ({"run_utc": "2026-08-15T00:00:00Z"}, "a3_carry_future"), ({"run_utc": "garbage"}, "a3_carry_no_run_utc"),
    ({"verdict": "NEEDS_CHECK", "deadline_ms": None}, "a3_needs_check_deadline"), ({"verdict": "NEEDS_CHECK", "deadline_ms": 1}, "a3_needs_check_deadline")])
def test_a3_carry_latches(tmp_path, carry, label):
    br, port, w = run_one(tmp_path, carry=carry)
    assert sends(port) == [] and br.state.latches[T.LATCH_A3] == label


def test_a3_needs_check_inside_deadline_refuses_without_a_latch(tmp_path):
    w = World(tmp_path)
    w.fresh(carry={"verdict": "NEEDS_CHECK", "deadline_ms": w.at + 3_600_000})
    assert T.a3_problem(w.cfg(), w.at, False) == (None, "a3_needs_check")
    w.start()
    w.entry(0, "MINT", 0)
    br, port, clk = w.bridge()
    feed(br, clk)
    assert sends(port) == [] and not br.state.latches and br.state.counts.get("a3_needs_check_refusal") == 1


def test_a3_record_only_and_none_clear(tmp_path):
    w = World(tmp_path)
    for v in ("NONE", "RECORD_ONLY"):
        w.fresh(carry={"verdict": v})
        assert T.a3_problem(w.cfg(), w.at, False) == (None, None)


def test_a3_carry_unreadable_missing_malformed_or_not_root(tmp_path):
    w = World(tmp_path)
    cfg = w.cfg()
    p = Path(cfg.a3_carry_path)
    assert T.a3_problem(cfg, w.at, True)[0] == "a3_carry_owner_mode"  # root-owned is required with root_checks: the test file is the running user's
    os.chmod(p, 0o664)
    assert T.a3_problem(cfg, w.at, False)[0] == "a3_carry_owner_mode"
    os.chmod(p, 0o644)
    p.write_text("[1]")
    assert T.a3_problem(cfg, w.at, False)[0] == "a3_carry_malformed"
    p.write_text("")
    assert T.a3_problem(cfg, w.at, False)[0] == "a3_carry_malformed"
    p.unlink()
    assert T.a3_problem(cfg, w.at, False)[0] == "a3_carry_missing"
    p.symlink_to(Path(cfg.a3_applied_path))
    assert T.a3_problem(cfg, w.at, False)[0] == "a3_carry_not_regular"
    p.unlink()
    p.write_bytes(b"x" * (T.FILE_MAX_BYTES + 1))
    os.chmod(p, 0o644)
    assert T.a3_problem(cfg, w.at, False)[0] == "a3_carry_swapped_or_large"


@pytest.mark.parametrize("verdict", ["HALT", "FAIL", "ERROR"])
def test_a3_guard_halting_verdicts_latch(tmp_path, verdict):
    w = World(tmp_path)
    w.fresh(applied={"verdict": verdict})
    assert T.a3_problem(w.cfg(), w.at, False) == (f"a3_guard:{verdict}", None)


def test_a3_guard_clear_verdicts(tmp_path):
    w = World(tmp_path)
    for v in ("NONE", "PASS"):
        w.fresh(applied={"verdict": v})
        assert T.a3_problem(w.cfg(), w.at, False) == (None, None)


def test_a3_guard_pending_ahead_malformed_unreadable(tmp_path):
    w = World(tmp_path)
    cfg = w.cfg()
    run_ms = w.at - 3_600_000
    w.fresh(applied={"run_utc": iso(run_ms - 3_600_000)})  # behind the carry's run; the carry file was just written: pending (a refusal, no latch)
    assert T.a3_problem(cfg, w.at, False) == (None, "a3_guard_pending")
    w.fresh(applied={"run_utc": iso(run_ms + 3_600_000)})
    assert T.a3_problem(cfg, w.at, False) == ("a3_guard_ahead", None)
    w.fresh(applied={"verdict": "MAYBE"})
    assert T.a3_problem(cfg, w.at, False) == ("a3_guard_malformed", None)
    w.fresh(applied={"run_utc": "nope"})
    assert T.a3_problem(cfg, w.at, False) == ("a3_guard_malformed", None)
    Path(cfg.a3_applied_path).unlink()
    assert T.a3_problem(cfg, w.at, False) == ("a3_guard_unreadable", None)
    Path(cfg.a3_applied_path).write_text("{oops")
    assert T.a3_problem(cfg, w.at, False) == ("a3_guard_unreadable", None)


def test_a3_guard_behind_longer_than_the_lag_latches(tmp_path):
    w = World(tmp_path)
    cfg = w.cfg()
    w.fresh(applied={"run_utc": iso(w.at - 2 * 3_600_000)})
    os.utime(cfg.a3_carry_path, (1, 1))  # the carry was written long ago
    assert T.a3_problem(cfg, w.at + 3 * 3_600_000, False) == ("a3_guard_missing", None)


def test_a3_guard_halt_reaches_the_bridge(tmp_path):
    br, port, _ = run_one(tmp_path, applied={"verdict": "HALT"})
    assert sends(port) == [] and br.state.latches[T.LATCH_A3] == "a3_guard:HALT"


# ------------------------------------------------------------------------------------------------ read-record gate and the start gate
@pytest.mark.parametrize("rec,label", [
    ({"read_result_sha256": "ee" * 32}, "read_record_sha_mismatch"), ({"qp_comment_id": "6700000002"}, "read_record_qp_mismatch"), ({"status": "FAIL"}, "read_record_not_pass_ok"),
    ({"qp_verdict": "NOT OK"}, "read_record_not_pass_ok"), ({"bot_id": "r4b-exp027"}, "read_record_identity"), ({"change_id": "x"}, "read_record_identity"),
    ({"schema": "s"}, "read_record_identity")])
def test_read_record_gate_needs_both_to_match(tmp_path, rec, label):
    w = World(tmp_path)
    jwrite(tmp_path / "read.json", {"schema": T.READ_SCHEMA, "bot_id": T.BOT_ID, "change_id": CID, "status": "PASS", "read_result_sha256": SHA_READ, "qp_comment_id": QP_ID,
                                    "qp_verdict": "OK", **rec})
    assert T.read_record_problem(w.cfg(), False) == label


def test_read_record_missing_and_good(tmp_path):
    w = World(tmp_path)
    assert T.read_record_problem(w.cfg(), False) is None
    (tmp_path / "read.json").unlink()
    assert T.read_record_problem(w.cfg(), False) == "read_record_missing"


def test_start_refused_on_a_real_root_without_the_read_record(tmp_path):
    w = World(tmp_path, synthetic=False)
    (tmp_path / "read.json").unlink()
    with pytest.raises(SystemExit, match="read_record_missing"):
        T.R4btBridge(w.cfg(), B.DryPort(1), now_ms=Clock(1), root_checks=False)
    w.fresh()
    br, _, _ = w.bridge()  # with the record the same root starts
    assert br is not None


def test_a_real_root_reads_no_row_when_the_record_goes_bad(tmp_path):
    w = World(tmp_path, synthetic=False)
    w.start()
    w.entry(0, "MINT", 0)
    br, port, clk = w.bridge()
    jwrite(tmp_path / "read.json", {"schema": T.READ_SCHEMA, "bot_id": T.BOT_ID, "change_id": CID, "status": "PASS", "read_result_sha256": "ee" * 32, "qp_comment_id": QP_ID,
                                    "qp_verdict": "OK"})
    assert br.read_new_rows() == [] and T.LATCH_READ in br.state.latches and sends(port) == []
    assert br.read_new_rows() == []  # the latch stays


def test_start_refusal_live_and_check_cli(tmp_path, capsys):
    w = World(tmp_path)
    assert T.start_refusal(w.cfg(), live=True) == "live_not_built"
    assert T.start_refusal(w.cfg(), root_checks=False) is None
    cfgp = jwrite(tmp_path / "cfg.json", w.d)
    assert T.check(str(cfgp), root_checks=False)["ok"] is True
    assert T.check(str(cfgp), live=True, root_checks=False)["problem"] == "live_not_built"
    assert T.main(["--config", str(cfgp), "--live"]) == 2
    bad = jwrite(tmp_path / "bad.json", {**w.d, "stake_lamports": 10 ** 9})
    assert T.check(str(bad))["ok"] is False and "above the DEC-032" in T.check(str(bad))["problem"]
    assert T.check(str(tmp_path / "nope.json"))["ok"] is False
    capsys.readouterr()


# ------------------------------------------------------------------------------------------------ live gates: owner, LIVE_OK, TIER
def test_live_gates_all_met_with_root_checks_off(tmp_path):
    assert T.live_gate_problems(World(tmp_path).cfg(), False) == []


def test_live_gates_each_missing_or_wrong(tmp_path):
    w = World(tmp_path)
    cfg = w.cfg()
    (tmp_path / "owner.md").write_text("OWNER_CANARY_CONFIRMED: bot_id=hv4-x\n")
    assert T.live_gate_problems(cfg, False) == ["owner_line_missing"]
    (tmp_path / "owner.md").unlink()
    assert T.live_gate_problems(cfg, False) == ["owner_dec_missing"]
    w.fresh()
    (tmp_path / "LIVE_OK").write_text("some-other-bot\n")
    assert T.live_gate_problems(cfg, False) == ["live_ok_other_book"]
    (tmp_path / "LIVE_OK").unlink()
    assert T.live_gate_problems(cfg, False) == ["live_ok_missing"]
    w.fresh()
    (tmp_path / "TIER").write_text("T0\n")
    assert T.live_gate_problems(cfg, False) == ["tier_file_invalid"]
    (tmp_path / "TIER").unlink()
    assert T.live_gate_problems(cfg, False) == ["tier_file_missing"]


def test_live_gates_need_root_ownership_when_root_checks_are_on(tmp_path):
    got = T.live_gate_problems(World(tmp_path).cfg(), True)
    assert len(got) == 3 and got[0].startswith("owner_dec_") and got[1] == "live_ok_owner_mode" and got[2].startswith("tier_file_")


def test_bridge_start_records_which_live_gates_are_open(tmp_path):
    w = World(tmp_path)
    (tmp_path / "LIVE_OK").unlink()
    br, _, _ = w.bridge()
    rows = [json.loads(ln) for ln in br.log_path.read_text().splitlines()]
    assert [r for r in rows if r["kind"] == "r4bt_start"][0]["live_gates_open"] == ["live_ok_missing"]


# ------------------------------------------------------------------------------------------------ caps inside the bridge
def test_bridge_limits_are_the_c0_table(tmp_path):
    br, _, _ = World(tmp_path, stake_lamports=20_000_000, max_open=1).bridge()
    tier, lim, prob = br.tier_limits()
    assert prob is None and lim["stake_lamports"] == 20_000_000 and lim["max_open"] == 1 and lim["total_loss_lamports"] == 200_000_000


def test_max_open_cap_refuses_the_next_buy(tmp_path):
    w = World(tmp_path, max_open=1)
    w.start()
    w.entry(0, "AAA", 0)
    w.entry(0, "BBB", 1)
    br, port, clk = w.bridge()
    feed(br, clk)
    assert sends(port) == [("buy", f"AAA:{mark(0)}")]


def test_live_built_stays_false():
    assert T.LIVE_BUILT is False and X.LIVE_BUILT is False
