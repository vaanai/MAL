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
from tools import arena_stack as AS
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
    """A tmp world: synthetic decision root, twin dir, carry, read record, owner file, LIVE_OK, TIER; all fresh around `at`."""

    def __init__(self, tmp, synthetic=True, **over):
        self.tmp = tmp
        self.root = tmp / "rules" if synthetic else tmp / "r4b-dry" / CID / "declog"  # a real root sits under the change_id root
        self.root.mkdir(parents=True)
        if synthetic:
            (self.root / HV.SYNTHETIC_MARKER).write_text("x")
        self.at = ms(0) + 50
        self.d = {"root": str(self.root), "state_dir": str(tmp / "state"), "bot_id": T.BOT_ID, "frozen_sha": W, "change_id": CID, "end_ms": (BT0 + 400 * 86400) * 1000,
                  "slot_s": 0.4, "funded_lamports": 350_000_000, "twin_dir": str(tmp / "twin"), "a3_carry_path": str(tmp / "a3" / "carry.json"),
                  "read_record_path": str(tmp / "read.json"), "read_result_sha256": SHA_READ, "qp_comment_id": QP_ID,
                  "owner_file": str(tmp / "owner.md"), "live_ok_path": str(tmp / "LIVE_OK"), "tier_path": str(tmp / "TIER"), "health_path": str(tmp / "health" / "health.json"), **T.C0_CAPS}
        self.d.update(over)
        self.log = DecisionLog(str(self.root))
        self.fresh()

    def fresh(self, *, carry=None, run=None, rows=None):
        t = self.tmp
        run_utc = iso(self.at - 3_600_000)
        jwrite(t / "a3" / "carry.json", {"schema": T.CARRY_SCHEMA, "bot_id": T.BOT_ID, "change_id": CID, "run_utc": run_utc, "written_utc": iso(self.at - 60_000), "verdict": "NONE", "halt_at_ms": None,
                                         "halt": {"flags": {"pins_changed": {"halt": False, "evaluated": True}, "synthetic_share_high": {"halt": False, "evaluated": True}}},
                                         "warn": {"flags": {"program_changed": {"warn": False}}}, **(carry or {})})
        jwrite(t / "twin" / T.TWINS_NAME, rows or [], lines=True)
        sha = hashlib.sha256((t / "twin" / T.TWINS_NAME).read_bytes()).hexdigest()
        jwrite(t / "twin" / T.RUN_RECORD_NAME, {"schema": T.RUN_SCHEMA, "tool": "r4bt_c0_twin", "status": "ok", "run_ms": self.at - 1000, "heartbeat_ms": self.at - 1000, "w_sha": W,
                                                 "engine_sha256": T.ENGINE_SHA256, "twins_sha256": sha, **(run or {})})
        jwrite(t / "read.json", {"schema": T.READ_SCHEMA, "bot_id": T.BOT_ID, "change_id": CID, "status": "PASS", "read_result_sha256": SHA_READ, "qp_comment_id": QP_ID,
                                 "qp_verdict": "OK"})
        (t / "owner.md").write_text(f"OWNER_CANARY_CONFIRMED: bot_id={T.BOT_ID}\n")
        (t / "LIVE_OK").write_text(T.BOT_ID + "\n")
        (t / "TIER").write_text("T0\n")
        for f in ("owner.md", "LIVE_OK", "TIER"):
            os.chmod(t / f, 0o644)

    def cfg(self):
        return T.make_config(self.d)

    def bridge(self, clk=None, port=None, parity=True):
        clk = clk or Clock(self.at)
        cfg = self.cfg()
        port = port or B.DryPort(cfg.dry_balance_lamports)
        br = T.R4btBridge(cfg, port, now_ms=clk, root_checks=False)
        if parity:
            self.parity(cfg)
        return br, port, clk

    def parity(self, cfg=None, **over):
        """A fresh parity result for the CURRENT order ledger (the gate hashes it)."""
        cfg = cfg or self.cfg()
        sd = Path(cfg.state_dir)
        sha = lambda n: hashlib.sha256((sd / n).read_bytes()).hexdigest()  # noqa: E731
        jwrite(sd / T.PARITY_STATE_NAME, {"schema": T.PARITY_SCHEMA, "at_ms": self.at - 1000, "run_ms": self.at - 1000, "exit_code": 0, "verdict": "parity not shown",
                                          "stop_required": False, "ledger_sha256": sha(T.LEDGER_NAME), "attempts_sha256": sha(T.ATTEMPTS_NAME), "twins_sha256": None,
                                          "twin_run_ms": None, "counts": {}, "p_status": {}, "note": None, **over})

    def start(self, sha=W, change_id=CID):
        self.log.append(T.DECLOG_ARM, mark(0) * 1000, {"kind": "start", "book": "r4-b-T", "mode": "dry", "frozen_sha": sha, "change_id": change_id})

    def entry(self, i, mint, rank_, dt=1200):
        tm = mark(i)
        self.log.append(T.DECLOG_ARM, tm * 1000 + dt, {"kind": "entry", "order_id": f"{mint}:{tm}", "pool": "PDA_" + mint, "meta": {"mint": mint, "v0": V, "mbt": tm - 7200, "s0": 1},
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
    assert T.LIVE_BUILT is True and T.BOT_ID == "r4bt-a1"
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
    {"mode": "paper"}, {"mode": "live", "stop_file": "/x/STOP"}, {"bot_id": "r4b-exp027"}, {"bot_id": None}, {"frozen_sha": "ab" * 20}, {"frozen_sha": None}, {"change_id": "r4bt-a1-other"}, {"change_id": None},
    {"change_id": "x/y"}, {"stake_lamports": 50_000_001}, {"max_open": 3}, {"max_trades_per_day": 31}, {"daily_loss_lamports": 100_000_001},
    {"total_loss_lamports": 200_000_001}, {"stake_lamports": 0}, {"max_open": True}, {"funded_lamports": 349_999_999}, {"funded_lamports": 1_000_000_001},
    {"funded_lamports": None}, {"twin_dir": "rel/dir"}, {"a3_carry_path": ""}, {"read_record_path": "r.json"},
    {"read_result_sha256": "xyz"}, {"read_result_sha256": "CD" * 32}, {"qp_comment_id": "abc"}, {"qp_comment_id": ""}, {"sl_backstop": 0.3}, {"slot_s": 0}, {"end_ms": None}])
def test_make_config_refusals(tmp_path, bad):
    with pytest.raises(T.Refused):
        World(tmp_path, **bad).cfg()


@pytest.mark.parametrize("key", list(T.C0_CAPS) + ["funded_lamports", "twin_dir", "a3_carry_path", "read_record_path", "read_result_sha256", "qp_comment_id",
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
    w.log.append(T.DECLOG_ARM, mark(0) * 1000, {"kind": "start", "book": "r4-b-T", "mode": "dry", "frozen_sha": W})
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
    ({"run_ms": 1}, "twin_gate:stale"), ({"heartbeat_ms": None}, "twin_gate:heartbeat_missing"), ({"heartbeat_ms": "x"}, "twin_gate:heartbeat_missing"),
    ({"heartbeat_ms": 10 ** 15}, "twin_gate:heartbeat_future"), ({"run_ms": None}, "twin_gate:run_ms_missing"), ({"run_ms": 10 ** 15}, "twin_gate:run_ms_future"),
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


def twin_row(oid, **leg):
    return {"schema": T.TWIN_SCHEMA, "order_id": oid, "status": "open", "entry_leg": {"effective_slot": 1, "corrected_slot": 1000, "sps_entry": 2.5, "k_1p9": 1, "k_3p0": 2, **leg}}


def test_twin_gate_entry_row_needed_after_the_grace(tmp_path):
    w = World(tmp_path)
    cfg = w.cfg()
    orders = {"A:1": {"buy_landed_ms": w.at - T.TWIN_ROW_GRACE_MS - 1}, "B:1": {"buy_landed_ms": w.at - 5}, "C:1": {"status": "buy_sending"}}
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) == "twin_gate:row_missing"  # A:1 is past the grace with no row
    w.fresh(rows=[twin_row("A:1")])
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) is None  # B:1 is inside the grace; C:1 never landed
    w.fresh(rows=[{**twin_row("A:1"), "entry_leg": None}])
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) == "twin_gate:row_missing"  # a row without its entry stage is not enough
    w.fresh(rows=[{**twin_row("A:1"), "schema": "wrong"}])
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) == "twin_gate:row_missing"  # rows of another schema are not twins
    (Path(cfg.twin_dir) / T.TWINS_NAME).unlink()
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) == "twin_gate:twins_unreadable"
    assert T.twin_entry_legs(cfg.twin_dir, False) == {}


def test_twin_gate_checks_w_sha_against_the_pinned_w_not_the_run_record(tmp_path):
    w = World(tmp_path)
    cfg = w.cfg()
    w.fresh(run={"w_sha": W.upper()})
    assert T.twin_gate_problem(cfg.twin_dir, w.at, {}, cfg, False) == "twin_gate:pin_mismatch"
    w.fresh(run={"w_sha": None})
    assert T.twin_gate_problem(cfg.twin_dir, w.at, {}, cfg, False) == "twin_gate:pin_mismatch"


def test_twin_gate_heartbeat_stale_but_run_ms_fresh(tmp_path):
    w = World(tmp_path)
    w.fresh(run={"heartbeat_ms": w.at - T.TWIN_HEARTBEAT_DEFAULT_MS - 1})
    assert T.twin_gate_problem(w.cfg().twin_dir, w.at, {}, w.cfg(), False) == "twin_gate:heartbeat_stale"


def test_twin_entry_legs_read_the_per_trip_sps_entry(tmp_path):
    w = World(tmp_path)
    w.fresh(rows=[twin_row("A:1", sps_entry=2.5), twin_row("B:1", sps_entry=0), twin_row("C:1", corrected_slot="x")])
    assert T.twin_entry_legs(w.cfg().twin_dir, False) == {"A:1": {"corrected_slot": 1000, "sps_entry": 2.5}}


# ------------------------------------------------------------------------------------------------ F2: origin = the twin's corrected slot, ceil, own sps_entry
def test_latency_origin_is_the_twin_corrected_slot_with_sdT_beside():
    o = {"decision_slot": 100, "buy_landed_ms": 5, "buy_landed_slot": 1007}
    legs = {"a": {"corrected_slot": 1000, "sps_entry": 2.5}}  # sd(T) is 100, the twin's corrected slot (decision ms + d) is 1000
    r = T.landed_buy_latencies_t({"a": o}, legs)[0]
    assert r["slots"] == 7 and r["sdT_slots"] == 907 and r["sps_entry"] == 2.5
    assert (r["k19"], r["k30"]) == (1, 2) or (r["k19"], r["k30"]) == (AS.k_slots(1.9, 2.5, "ceil"), AS.k_slots(3.0, 2.5, "ceil"))
    assert (r["k19"], r["k30"]) == (1, 2)  # ceil(1.9 / 2.5) = 1, ceil(3.0 / 2.5) = 2
    assert r["x19"] == 6 and r["x30"] == 5
    s = T.latency_summary_t({"a": o}, legs)
    assert s["origin"].startswith("twin row corrected_slot") and s["diag_sdT"]["all_slots_p50"] == 907 and s["all_slots_p50"] == 7


def test_k_is_ceil_with_each_trips_own_sps_entry():
    legs = {"a": {"corrected_slot": 0, "sps_entry": 2.5}, "b": {"corrected_slot": 0, "sps_entry": 0.5}}
    orders = {k: {"buy_landed_ms": i, "buy_landed_slot": 10} for i, k in enumerate("ab")}
    got = {r["order_id"]: (r["k19"], r["k30"]) for r in T.landed_buy_latencies_t(orders, legs)}
    assert got == {"a": (1, 2), "b": (4, 6)}  # ceil(1.9 / 0.5) = 4 (3.8), ceil(3.0 / 0.5) = 6 exactly (the 1e-9 tolerance); round would give 4 and 6, 1.9/2.5 -> round 1
    assert AS.k_slots(1.9, 1.0, "ceil") == 2 and AS.k_slots(1.9, 1.0, "round") == 2 and AS.k_slots(2.1, 1.0, "ceil") == 3 != AS.k_slots(2.1, 1.0, "round")


def test_latency_decides_on_the_twin_origin_not_sdT():
    # 20 landed buys: 9 slots after the twin's corrected slot (k30 = 2 at sps 2.5, so excess 7 > 0: P5 soft halt); sd(T) would say -1 (passes)
    orders = {f"o{i}": {"buy_landed_ms": i, "buy_landed_slot": 1009 + 10 * i, "decision_slot": 1010 + 10 * i} for i in range(20)}
    legs = {f"o{i}": {"corrected_slot": 1000 + 10 * i, "sps_entry": 2.5} for i in range(20)}
    chk = X.latency_check(T.latency_summary_t(orders, legs))
    assert chk["enough"] and chk["p5_soft_halt"] and not chk["match_latency_ok"]
    assert not X.latency_check(X.latency_summary(orders, 0.4))["p5_soft_halt"]


def test_latency_unmeasured_without_a_twin_row_or_a_landed_slot():
    orders = {"a": {"buy_landed_ms": 1, "buy_landed_slot": 10}, "b": {"buy_landed_ms": 2}}
    legs = {"b": {"corrected_slot": 0, "sps_entry": 2.5}}
    assert [r["slots"] for r in T.landed_buy_latencies_t(orders, legs)] == [None, None]
    assert T.latency_summary_t(orders, legs)["measured"] == 0


def test_bridge_latency_report_reads_the_twin_rows(tmp_path):
    w = World(tmp_path)
    w.fresh(rows=[twin_row("a", corrected_slot=100, sps_entry=2.5)])
    br, _, _ = w.bridge()
    br.state.orders["a"] = {"decision_slot": 1, "buy_landed_ms": 3000, "buy_landed_slot": 103, "status": "open"}
    rep = br.latency_report()
    assert rep["summary"]["measured"] == 1 and rep["summary"]["all_slots_p50"] == 3 and rep["summary"]["diag_sdT"]["measured"] == 1


# ------------------------------------------------------------------------------------------------ A3: carry (Am.9 F4)
@pytest.mark.parametrize("carry,label", [
    ({"verdict": "HALT", "halt_at_ms": 1}, "a3_carry:HALT"), ({"verdict": "ERROR"}, "a3_carry:ERROR"), ({"verdict": "NEEDS_CHECK"}, "a3_carry:NEEDS_CHECK"),
    ({"verdict": None}, "a3_carry_verdict_unknown"), ({"verdict": "MAYBE"}, "a3_carry:MAYBE"),
    ({"bot_id": "r4b-exp027"}, "a3_carry_identity"), ({"change_id": "x"}, "a3_carry_identity"), ({"schema": "s"}, "a3_carry_identity"),
    ({"run_utc": "2026-08-12T00:00:00Z"}, "a3_carry_stale"), ({"run_utc": "2026-08-15T00:00:00Z"}, "a3_carry_future"), ({"run_utc": "garbage"}, "a3_carry_no_run_utc"),
    ({"halt": None}, "a3_record_no_halt_flags"), ({"halt": {"flags": {}}}, "a3_record_no_halt_flags"),
    ({"halt": {"flags": {"pins_changed": {"halt": True, "evaluated": True}}}}, "a3_flags:pins_changed"),
    ({"halt": {"flags": {"pins_changed": {"halt": False, "evaluated": False}}}}, "a3_flags:pins_changed"),
    ({"halt": {"flags": {"pins_changed": "yes"}}}, "a3_flags:pins_changed"),
    ({"halt": {"flags": {"pins_changed": {"halt": False}}}}, "a3_flags:pins_changed"),  # no `evaluated` key: not evaluated, fail closed
    ({"halt": {"flags": {"pins_changed": {"halt": False, "evaluated": None}}}}, "a3_flags:pins_changed"),
    ({"halt": {"flags": {"mystery_flag": {"halt": True, "evaluated": True}}}}, "a3_flags:mystery_flag"),
    ({"warn": {"flags": {"program_changed": {"warn": True}}}}, "a3_flags:program_changed")])
def test_a3_carry_latches(tmp_path, carry, label):
    br, port, w = run_one(tmp_path, carry=carry)
    assert sends(port) == [] and br.state.latches[T.LATCH_A3] == label


def test_a3_non_halting_flags_are_skipped_and_counted(tmp_path):
    flags = {"pins_changed": {"halt": False, "evaluated": True}, **{k: {"halt": True, "evaluated": True} for k in T.NOT_HALTING_FLAGS}}
    br, port, w = run_one(tmp_path, carry={"halt": {"flags": flags}})
    assert sends(port) == [("buy", f"MINT:{mark(0)}")] and not br.state.latches
    assert all(br.state.counts.get("a3_skipped_" + k) == 1 for k in T.NOT_HALTING_FLAGS)
    assert T.a3_problem(w.cfg(), w.at, False) == (None, sorted(T.NOT_HALTING_FLAGS))


def test_a3_record_only_and_none_clear(tmp_path):
    w = World(tmp_path)
    for v in ("NONE", "RECORD_ONLY"):
        w.fresh(carry={"verdict": v})
        assert T.a3_problem(w.cfg(), w.at, False) == (None, [])


def test_am3_pooled_guard_does_not_apply_and_no_applied_log_is_read(tmp_path):
    w = World(tmp_path)
    (tmp_path / "a3" / "a3-applied.jsonl").write_text(json.dumps({"run_utc": iso(w.at - 3_600_000), "verdict": "HALT"}) + "\n")
    assert T.a3_problem(w.cfg(), w.at, False) == (None, [])  # a guard HALT in a log next to the carry changes nothing (Am.9 F4)
    assert not hasattr(T.R4btConfig, "a3_applied_path") and "a3_applied_path" not in T.R4btConfig.__dataclass_fields__


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
    p.symlink_to(Path(cfg.twin_dir) / T.TWINS_NAME)
    assert T.a3_problem(cfg, w.at, False)[0] == "a3_carry_not_regular"
    p.unlink()
    p.write_bytes(b"x" * (T.FILE_MAX_BYTES + 1))
    os.chmod(p, 0o644)
    assert T.a3_problem(cfg, w.at, False)[0] == "a3_carry_swapped_or_large"


def test_a3_carry_older_than_36h_fails_closed(tmp_path):
    w = World(tmp_path, a3_carry_max_age_ms=36 * 3_600_000)  # the written_utc bound at its maximum, so the record run_utc bound is what is tested
    assert T.a3_problem(w.cfg(), w.at + 34 * 3_600_000, False)[0] is None  # 35 h old: not yet stale
    assert T.a3_problem(w.cfg(), w.at + 36 * 3_600_000 + 3_600_000 + 1, False)[0] == "a3_carry_stale"


# ------------------------------------------------------------------------------------------------ send fee (Am.9 section 8) and the order ledger
def test_default_send_fee_is_the_registered_505000(tmp_path):
    assert World(tmp_path).cfg().send_fee_lamports == 505_000 == T.REGISTERED_SEND_LAMPORTS == X.SEND_FEE_LAMPORTS


OVR = {"lamports": 255_000, "fill_fee": "FILL:fee 255000", "owner_line": "owner OK 2026-11-12", "qp_comment_id": "6700000009"}


@pytest.mark.parametrize("over", [
    {"send_fee_lamports": 255_000}, {"send_fee_lamports": 255_000, "fee_override": {**OVR, "lamports": 250_000}}, {"send_fee_lamports": 255_000, "fee_override": {**OVR, "fill_fee": ""}},
    {"send_fee_lamports": 255_000, "fee_override": {**OVR, "owner_line": None}}, {"send_fee_lamports": 255_000, "fee_override": {**OVR, "qp_comment_id": "x"}},
    {"send_fee_lamports": 255_000, "fee_override": "yes"}, {"send_fee_lamports": 505_001, "fee_override": {**OVR, "lamports": 505_001}},
    {"send_fee_lamports": 505_000, "fee_override": OVR}, {"send_fee_lamports": 5_000, "fee_override": {**OVR, "lamports": 5_000}}, {"send_fee_lamports": 0}, {"send_fee_lamports": True},
    {"send_fee_lamports": 1.5}, {"fee_override": OVR}])
def test_send_fee_below_or_off_the_registered_value_is_refused_without_a_reviewed_override(tmp_path, over):
    with pytest.raises(T.Refused):
        World(tmp_path, **over).cfg()


def test_a_reviewed_fee_override_is_accepted(tmp_path):
    cfg = World(tmp_path, send_fee_lamports=255_000, fee_override=OVR).cfg()
    assert cfg.send_fee_lamports == 255_000 and cfg.fee_override == OVR


def test_ledger_records_the_actual_entry_and_exit_fees_with_escalations(tmp_path):
    class FeePort(B.DryPort):
        def buy(self, order):
            ok, why = super().buy(order)
            self._events[-1].update(fees_lamports=505_000 + 650_000, landed_slot=2000)  # a buy that needed one escalated resend: both sends are paid
            return ok, why

        def sell(self, order, reason):
            ok, why = super().sell(order, reason)
            self._events[-1].update(fees_lamports=505_000, landed_slot=2100)
            return ok, why

    w = World(tmp_path, send_fee_lamports=255_000, fee_override=OVR)
    w.start()
    w.entry(0, "MINT", 0)
    clk = Clock(w.at)
    cfg = w.cfg()
    br = T.R4btBridge(cfg, FeePort(cfg.dry_balance_lamports), now_ms=clk, root_checks=False)
    w.parity(cfg)
    feed(br, clk)
    rows = [json.loads(ln) for ln in (Path(cfg.state_dir) / T.LEDGER_NAME).read_text().splitlines()]
    assert len(rows) == 1
    r = rows[0]
    assert r["order_id"] == f"MINT:{mark(0)}" and r["sent"] is True and r["entry_fee_lamports"] == 1_155_000 and r["exit_fee_lamports"] is None
    assert r["entry_ms"] == ms(0) and r["stake_sol"] == 0.05 and r["pool"] == "PDA_MINT" and r["landed_slot_entry"] == 2000 and r["decision_slot"] == SD0
    assert r["restart"] is False and r["gap_hold"] is False and set(r) >= {"rank", "t_mark", "exit_ms", "landed_slot_exit", "exit_reason"}
    br.handle_row({"kind": "exit", "order_id": f"MINT:{mark(0)}", "mint": "MINT", "reason": "rank", "t_mark": mark(0), "ms": ms(0) + 3_600_000, "seq": 99})
    br._poll_ports()
    r = json.loads((Path(cfg.state_dir) / T.LEDGER_NAME).read_text().splitlines()[0])
    assert r["exit_fee_lamports"] == 505_000 and r["landed_slot_exit"] == 2100 and r["exit_ms"] is not None and r["exit_reason"] == "rank"


def test_ledger_dry_default_fee_is_marked_and_refused_orders_are_not_listed(tmp_path):
    br, port, w = run_one(tmp_path)
    o = br.state.orders[f"MINT:{mark(0)}"]
    assert o["entry_fee_lamports"] == 505_000 and o["fee_source"] == "dry_default"
    assert [r["order_id"] for r in T.ledger_rows({"x": {**o, "status": "refused"}, "y": o})] == ["y"]


def test_non_dry_event_without_fees_latches():
    class P:
        pass

    # unit-level: the bridge latches r4bt_fee_unreported for a non-dry event carrying no fees_lamports
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        w = World(Path(td))
        w.start()
        w.entry(0, "MINT", 0)
        br, port, clk = w.bridge()
        feed(br, clk)
        br.apply_events([{"order_id": f"MINT:{mark(0)}", "event": "sell_landed", "pnl_lamports": 0, "landed_slot": 5}])
        assert T.LATCH_FEE in br.state.latches


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
    assert T.start_refusal(w.cfg(), live=True) == "live_flag_mode_mismatch"  # a DRY config never goes live by a flag
    assert T.start_refusal(w.cfg(), root_checks=False) is None
    cfgp = jwrite(tmp_path / "cfg.json", w.d)
    assert T.check(str(cfgp), root_checks=False)["ok"] is True
    assert T.check(str(cfgp), live=True, root_checks=False)["problem"] == "live_flag_mode_mismatch"
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
    (tmp_path / "TIER").write_text("T1\n")
    assert T.live_gate_problems(cfg, False) == ["tier_file_invalid"]  # the HV4 / H5 shared-file value T1 is not this bot's T0
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


def test_x_live_built_stays_false():
    assert X.LIVE_BUILT is False  # EXP-027's own module is untouched


# ------------------------------------------------------------------------------------------------ round 2 (reviewer / QP on 1a12de37)
def test_ledger_leaves_out_an_in_flight_order_so_the_twin_never_rejects_the_file(tmp_path):
    class SilentPort(B.DryPort):
        def buy(self, order):  # sent, but no buy event yet: in flight
            self.would_send.append({"side": "buy", "order_id": order["order_id"], "mint": order["mint"]})
            return True, None

    w = World(tmp_path)
    w.start()
    w.entry(0, "MINT", 0)
    clk = Clock(w.at)
    cfg = w.cfg()
    br = T.R4btBridge(cfg, SilentPort(cfg.dry_balance_lamports), now_ms=clk, root_checks=False)
    w.parity(cfg)
    feed(br, clk)
    assert f"MINT:{mark(0)}" in br.state.orders and br.state.orders[f"MINT:{mark(0)}"].get("entry_fee_lamports") is None
    assert (Path(cfg.state_dir) / T.LEDGER_NAME).read_text() == ""  # no pending row, so the twin's parse (int entry_ms and entry_fee_lamports) cannot fail
    br.apply_events([{"order_id": f"MINT:{mark(0)}", "event": "buy_landed", "pnl_lamports": 0, "landed_slot": 7, "fees_lamports": 505_000}])
    rows = [json.loads(ln) for ln in (Path(cfg.state_dir) / T.LEDGER_NAME).read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["entry_fee_lamports"] == 505_000 and isinstance(rows[0]["entry_ms"], int)


def test_every_ledger_row_satisfies_the_twins_required_ints(tmp_path):
    br, port, w = run_one(tmp_path)
    for r in T.ledger_rows(br.state.orders):
        assert r["sent"] is True and isinstance(r["entry_ms"], int) and isinstance(r["entry_fee_lamports"], int) and r["entry_fee_lamports"] >= 0
        assert r["exit_ms"] is None or r["exit_ms"] >= r["entry_ms"]


def test_ledger_row_check_matches_the_twins_parser(tmp_path):
    twin = pytest.importorskip("tools.r4bt_c0_twin")
    br, port, w = run_one(tmp_path)
    led = Path(br.cfg.state_dir) / T.LEDGER_NAME
    orders, skipped = twin.load_orders(led)  # the merged twin's own parser: it raises Untrusted on a sent row without int entry_ms / entry_fee_lamports
    assert len(orders) == 1 and skipped == 0


@pytest.mark.parametrize("bad", [{"twin_heartbeat_max_ms": 899_999}, {"twin_interval_s": 600}, {"twin_replay_s": 301}, {"twin_interval_s": 0}, {"twin_replay_s": None},
                                 {"twin_heartbeat_max_ms": True}, {"twin_interval_s": 300, "twin_replay_s": 300, "twin_heartbeat_max_ms": 100_000}])
def test_twin_heartbeat_bound_must_cover_two_intervals_plus_replay(tmp_path, bad):
    with pytest.raises(T.Refused):
        World(tmp_path, **bad).cfg()


def test_twin_heartbeat_bound_accepts_the_floor_and_a_record_aged_interval_plus_run_time(tmp_path):
    w = World(tmp_path, twin_interval_s=300, twin_replay_s=600, twin_heartbeat_max_ms=1_200_000)
    cfg = w.cfg()
    w.fresh(run={"heartbeat_ms": w.at - 300_000 - 600_000, "run_ms": w.at - 300_000 - 600_000})  # interval + run time old: must pass
    assert T.twin_gate_problem(cfg.twin_dir, w.at, {}, cfg, False) is None
    w.fresh(run={"heartbeat_ms": w.at - 1_200_001})
    assert T.twin_gate_problem(cfg.twin_dir, w.at, {}, cfg, False) == "twin_gate:heartbeat_stale"


def test_default_heartbeat_bound_is_the_floor(tmp_path):
    assert T.TWIN_HEARTBEAT_DEFAULT_MS == 900_000 == (2 * T.TWIN_INTERVAL_S_DEFAULT + T.TWIN_REPLAY_S_DEFAULT) * 1000
    assert World(tmp_path).cfg().twin_heartbeat_max_ms == 900_000


def test_a_refused_twin_row_is_no_twin_not_a_block(tmp_path):
    w = World(tmp_path)
    cfg = w.cfg()
    old = w.at - T.TWIN_ROW_GRACE_MS - 1
    orders = {"A:1": {"buy_landed_ms": old}, "B:1": {"buy_landed_ms": old}}
    refused = {"schema": T.TWIN_SCHEMA, "order_id": "A:1", "status": "refused", "entry_leg": None, "refused_reason": "no_pool_print"}
    w.fresh(rows=[refused, twin_row("B:1")])
    counts = {}
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False, counts) is None and counts == {"no_twin": 1}
    w.fresh(rows=[{**refused, "status": "excluded"}, twin_row("B:1")])
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) is None
    w.fresh(rows=[{**refused, "status": "pending_tape"}, twin_row("B:1")])  # pending_tape without its entry stage, past the grace: a block
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) == "twin_gate:row_missing"
    w.fresh(rows=[twin_row("B:1")])  # an absent row: a block
    assert T.twin_gate_problem(cfg.twin_dir, w.at, orders, cfg, False) == "twin_gate:row_missing"


def test_a_refused_twin_row_does_not_block_later_buys_in_the_bridge(tmp_path):
    w = World(tmp_path)
    w.start()
    w.entry(0, "MINT", 0)
    br, port, clk = w.bridge()
    br.state.orders["OLD:1"] = {"order_id": "OLD:1", "mint": "OLD", "status": "closed", "live": True, "stake_lamports": 1, "buy_landed_ms": w.at - T.TWIN_ROW_GRACE_MS - 1}
    w.fresh(rows=[{"schema": T.TWIN_SCHEMA, "order_id": "OLD:1", "status": "refused", "entry_leg": None}])
    feed(br, clk)
    assert sends(port) == [("buy", f"MINT:{mark(0)}")] and br.es["no_twin_seen"] == 1


def test_tier_is_a_per_bot_t0_file(tmp_path):
    w = World(tmp_path)
    assert T.TIER_REQUIRED == "T0" and "r4bt-a1" in T.TIER_DEFAULT and T.TIER_DEFAULT != "/etc/mal-arena-live/TIER"
    assert T.live_gate_problems(w.cfg(), False) == []
    (tmp_path / "TIER").write_text("T0 extra\n")
    assert T.live_gate_problems(w.cfg(), False) == ["tier_file_invalid"]


# ------------------------------------------------------------------------------------------------ declog arm, STOP file, fills, attempts (parity PR #1250)
def test_bridge_tails_arm_r4b_exp027_not_the_bot_id(tmp_path):
    w = World(tmp_path)
    w.start()
    w.entry(0, "MINT", 0)
    assert (w.root / "decisions" / "r4b-exp027").is_dir() and not (w.root / "decisions" / "r4bt-a1").exists()
    br, port, clk = w.bridge()
    feed(br, clk)
    assert sends(port) == [("buy", f"MINT:{mark(0)}")] and br.cfg.bot_id == "r4bt-a1" and T.DECLOG_ARM == "r4b-exp027"
    # rows under decisions/r4bt-a1 are not read
    other = DecisionLog(str(w.root))
    other.append("r4bt-a1", ms(1), {"kind": "start", "frozen_sha": "ee" * 20, "change_id": "zzz"})
    assert br.read_new_rows() == [] and not br.state.latches


def test_chain_is_verified_on_the_arm(tmp_path):
    w = World(tmp_path)
    w.start()
    w.entry(0, "MINT", 0)
    f = next((w.root / "decisions" / "r4b-exp027").glob("*.jsonl"))
    f.write_text(f.read_text().replace('"MINT"', '"MINX"', 1))
    br, port, clk = w.bridge()
    assert "chain_break" in br.state.latches and sends(port) == []


def test_real_root_must_be_under_the_change_id_root(tmp_path):
    (tmp_path / "w1").mkdir()
    assert World(tmp_path / "w1", synthetic=False).cfg().root.endswith(f"r4b-dry/{CID}/declog")
    (tmp_path / "w2").mkdir()
    bad = tmp_path / "r4b-dry" / "other" / "declog"
    bad.mkdir(parents=True)
    with pytest.raises(T.Refused, match="change_id root"):
        T.make_config({**World(tmp_path / "w2").d, "root": str(bad)})


def test_parity_stop_file_in_the_state_dir_stops_buys(tmp_path):
    w = World(tmp_path)
    w.start()
    w.entry(0, "MINT", 0)
    br, port, clk = w.bridge()
    Path(br.cfg.state_dir).mkdir(parents=True, exist_ok=True)
    (Path(br.cfg.state_dir) / T.STOP_NAME).write_text("{}")
    feed(br, clk)
    assert sends(port) == [] and br.state.counts.get("parity_stop_file_refusal", 0) >= 1 and br._files()[1] is True


def test_parity_stop_file_symlink_also_counts(tmp_path):
    br = World(tmp_path).bridge()[0]
    sd = Path(br.cfg.state_dir)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / T.STOP_NAME).symlink_to(tmp_path / "nowhere")
    assert br._files()[1] is True


def test_no_stop_file_no_stop(tmp_path):
    br = World(tmp_path).bridge()[0]
    assert br._files() == (False, False)


class FillPort(B.DryPort):
    """A non-DRY-looking event stream: events carry the live fill facts the parity tool needs."""

    def __init__(self, bal, fills=True, fail=False):
        super().__init__(bal)
        self.fills, self.fail = fills, fail

    def buy(self, order):
        ok, why = super().buy(order)
        ev = self._events[-1]
        ev.pop("dry")
        ev.update(fees_lamports=505_000, landed_slot=2000, landed_ms=9_000)
        if self.fills:
            ev.update(live_buy_price=1.25e-7)
        if self.fail:
            ev.update(event="buy_failed", pnl_lamports=-505_000, label="expired")
        return ok, why

    def sell(self, order, reason):
        ok, why = super().sell(order, reason)
        ev = self._events[-1]
        ev.pop("dry")
        ev.update(fees_lamports=505_000, landed_slot=2100, landed_ms=12_345)
        if self.fills:
            ev.update(live_sell_price=1.1e-7, live_net_lamports=-123_456, gap_through=True)
        return ok, why


def run_fill(tmp_path, **kw):
    w = World(tmp_path)
    w.start()
    w.entry(0, "MINT", 0)
    clk = Clock(w.at)
    cfg = w.cfg()
    br = T.R4btBridge(cfg, FillPort(cfg.dry_balance_lamports, **kw), now_ms=clk, root_checks=False)
    w.parity(cfg)
    feed(br, clk)
    return br, cfg, clk


def ledger(cfg, name=None):
    return [json.loads(ln) for ln in (Path(cfg.state_dir) / (name or T.LEDGER_NAME)).read_text().splitlines()]


def test_ledger_carries_the_live_fill_fields_from_the_events(tmp_path):
    br, cfg, clk = run_fill(tmp_path)
    r = ledger(cfg)[0]
    assert r["live_buy_price"] == 1.25e-7 and r["live_sell_price"] is None and r["fee_source"] is None
    br.handle_row({"kind": "exit", "order_id": f"MINT:{mark(0)}", "mint": "MINT", "reason": "rank", "t_mark": mark(0), "ms": ms(0) + 3_600_000, "seq": 99})
    br._poll_ports()
    r = ledger(cfg)[0]
    assert (r["live_sell_price"], r["live_net_lamports"], r["exit_landed_ms"], r["gap_through"]) == (1.1e-7, -123_456, 12_345, True)
    assert not br.state.latches


def test_dry_rows_leave_the_live_fields_null_and_are_marked(tmp_path):
    br, port, w = run_one(tmp_path)
    r = ledger(w.cfg())[0]
    assert r["fee_source"] == "dry_default" and r["live_buy_price"] is None and r["live_sell_price"] is None and r["live_net_lamports"] is None
    assert r["exit_landed_ms"] is None and r["gap_through"] is None and not br.state.latches


def test_non_dry_landed_events_without_fill_facts_latch(tmp_path):
    br, cfg, clk = run_fill(tmp_path, fills=False)
    assert T.LATCH_FILL in br.state.latches and "live_buy_price" in br.state.latches[T.LATCH_FILL]


def test_attempts_file_has_a_row_per_buy_including_failures(tmp_path):
    br, cfg, clk = run_fill(tmp_path)
    rows = ledger(cfg, T.ATTEMPTS_NAME)
    assert len(rows) == 1 and rows[0] == {"attempt_id": f"a:MINT:{mark(0)}", "trip_id": f"MINT:{mark(0)}", "decision_ms": ms(0), "landed": True, "landed_ms": 9_000,
                                          "failed_or_expired": False, "p_fail_press": 0.0}
    # a failed buy: a row with failed_or_expired true and no trip
    tmp2 = tmp_path / "f"
    tmp2.mkdir()
    br2, cfg2, _ = run_fill(tmp2, fail=True)
    rows = ledger(cfg2, T.ATTEMPTS_NAME)
    assert len(rows) == 1 and rows[0]["landed"] is False and rows[0]["failed_or_expired"] is True and rows[0]["trip_id"] is None and rows[0]["landed_ms"] is None


def test_attempts_file_round_trips_through_the_arena_parity_validator(tmp_path):
    AP = pytest.importorskip("tools.arena_live_parity")
    rows = []
    for i, sub in enumerate(("a", "b")):
        d = tmp_path / sub
        d.mkdir()
        rows += ledger(run_fill(d, fail=bool(i))[1], T.ATTEMPTS_NAME)
    rows = [{**r, "attempt_id": r["attempt_id"] + str(i)} for i, r in enumerate(rows)]
    AP.validate_inputs([], [], rows)  # the arena tool's own strict attempts schema (what #1250's --attempts feeds)


def test_ledger_and_attempts_round_trip_through_the_parity_tool_if_present(tmp_path):
    parity = pytest.importorskip("tools.r4bt_c0_parity")
    br, cfg, clk = run_fill(tmp_path)
    ldg = parity.load_ledger(Path(cfg.state_dir))
    assert ldg[0]["live_buy_price"] == 1.25e-7
    assert parity._jsonl((Path(cfg.state_dir) / T.ATTEMPTS_NAME).read_bytes(), "attempts")[0]["landed"] is True


# ------------------------------------------------------------------------------------------------ fresh-parity gate and carry written_utc bound
def gate_world(tmp_path):
    w = World(tmp_path)
    w.start()
    w.entry(0, "MINT", 0)
    return w


@pytest.mark.parametrize("over,label", [
    ({"exit_code": 10}, "parity_gate:exit_code"), ({"exit_code": 2}, "parity_gate:exit_code"), ({"exit_code": None}, "parity_gate:exit_code"), ({"exit_code": False}, "parity_gate:exit_code"),
    ({"at_ms": None}, "parity_gate:at_ms_missing"), ({"at_ms": "x"}, "parity_gate:at_ms_missing"), ({"at_ms": 1}, "parity_gate:stale"),
    ({"at_ms": 10 ** 15}, "parity_gate:at_ms_future"), ({"ledger_sha256": "00" * 32}, "parity_gate:ledger_sha_mismatch"), ({"ledger_sha256": None}, "parity_gate:ledger_sha_mismatch"),
    ({"attempts_sha256": "00" * 32}, "parity_gate:attempts_sha_mismatch"), ({"attempts_sha256": None}, "parity_gate:attempts_sha_mismatch"),
    ({"stop_required": True}, "parity_gate:stop_required"), ({"stop_required": None}, "parity_gate:stop_required"), ({"stop_required": 0}, "parity_gate:stop_required"),
    ({"schema": "other"}, "parity_gate:schema"), ({"schema": None}, "parity_gate:schema")])
def test_parity_gate_refuses_each_bad_state(tmp_path, over, label):
    w = gate_world(tmp_path)
    br, port, clk = w.bridge(parity=False)
    w.parity(**over)
    feed(br, clk)
    assert sends(port) == [] and br.state.counts.get("parity_gate_refusal") == 1
    assert T.parity_state_problem(br.cfg.state_dir, w.at) == label


def test_parity_gate_missing_malformed_and_unsafe_state(tmp_path):
    w = gate_world(tmp_path)
    br, port, clk = w.bridge(parity=False)
    f = Path(br.cfg.state_dir) / T.PARITY_STATE_NAME
    assert T.parity_state_problem(br.cfg.state_dir, w.at) == "parity_gate:parity_state_missing"
    f.write_text("{oops")
    assert T.parity_state_problem(br.cfg.state_dir, w.at) == "parity_gate:parity_state_malformed"
    w.parity()
    os.chmod(f, 0o666)
    assert T.parity_state_problem(br.cfg.state_dir, w.at) == "parity_gate:parity_state_owner_mode"
    f.unlink()
    f.symlink_to(tmp_path / "x")
    assert T.parity_state_problem(br.cfg.state_dir, w.at) == "parity_gate:parity_state_not_regular"
    feed(br, clk)
    assert sends(port) == []


def test_parity_gate_passes_with_a_fresh_matching_state_and_run_ms_at_the_bound(tmp_path):
    w = gate_world(tmp_path)
    br, port, clk = w.bridge(parity=False)
    w.parity(at_ms=w.at - T.PARITY_MAX_AGE_MS)
    assert T.parity_state_problem(br.cfg.state_dir, w.at) is None
    w.parity(at_ms=w.at - T.PARITY_MAX_AGE_MS - 1)
    assert T.parity_state_problem(br.cfg.state_dir, w.at) == "parity_gate:stale"
    w.parity()
    feed(br, clk)
    assert sends(port) == [("buy", f"MINT:{mark(0)}")]


def test_parity_gate_needs_a_new_run_after_the_ledger_changes(tmp_path):
    w = gate_world(tmp_path)
    w.entry(0, "OTHER", 1)
    br, port, clk = w.bridge()
    feed(br, clk)  # the first buy changes the ledger (a confirmed buy), so the second is refused until parity is rerun
    assert sends(port) == [("buy", f"MINT:{mark(0)}")] and br.state.counts.get("parity_gate_refusal") == 1
    w.parity(cfg=br.cfg)
    assert T.parity_state_problem(br.cfg.state_dir, w.at) is None


def test_parity_gate_refuses_when_the_ledger_file_is_missing(tmp_path):
    w = gate_world(tmp_path)
    br, port, clk = w.bridge()
    (Path(br.cfg.state_dir) / T.LEDGER_NAME).unlink()
    assert T.parity_state_problem(br.cfg.state_dir, w.at) == "parity_gate:ledger_missing"
    (Path(br.cfg.state_dir) / T.ATTEMPTS_NAME).unlink() if False else None


def test_ledger_exists_from_the_start(tmp_path):
    br = World(tmp_path).bridge(parity=False)[0]
    assert (Path(br.cfg.state_dir) / T.LEDGER_NAME).read_text() == ""


@pytest.mark.parametrize("carry,label", [
    ({"written_utc": None}, "a3_carry_no_written_utc"), ({"written_utc": "garbage"}, "a3_carry_no_written_utc"),
    ({"written_utc": "2026-08-14T00:00:00Z"}, "a3_carry_written_stale"), ({"written_utc": "2026-08-15T00:00:00Z"}, "a3_carry_written_future")])
def test_a3_carry_written_utc_bound_latches(tmp_path, carry, label):
    br, port, w = run_one(tmp_path, carry=carry)
    assert sends(port) == [] and br.state.latches[T.LATCH_A3] == label


def test_a3_carry_written_utc_bound_is_config_and_a_clean_but_stale_copy_is_refused(tmp_path):
    w = World(tmp_path)  # record run_utc is 1 h old and clean, written_utc 60 s old
    assert T.a3_problem(w.cfg(), w.at, False) == (None, [])
    assert T.a3_problem(w.cfg(), w.at + T.A3_CARRY_MAX_AGE_DEFAULT_MS - 62_000, False) == (None, [])  # just under 2 h after the write
    assert T.a3_problem(w.cfg(), w.at + T.A3_CARRY_MAX_AGE_DEFAULT_MS, False)[0] == "a3_carry_written_stale"  # the copy stalled; run_utc alone (36 h) would pass
    tight = World(tmp_path / "t", a3_carry_max_age_ms=30_000) if (tmp_path / "t").mkdir() is None else None
    assert T.a3_problem(tight.cfg(), tight.at, False)[0] == "a3_carry_written_stale"


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "2h", 36 * 3_600_000 + 1])
def test_a3_carry_max_age_config_is_validated(tmp_path, bad):
    with pytest.raises(T.Refused):
        World(tmp_path, a3_carry_max_age_ms=bad).cfg()


def test_parity_gate_round_trip_with_the_parity_tools_own_state_writer(tmp_path):
    """The state file written by tools/r4bt_c0_parity.write_state (PR #1250), read by this gate."""
    parity = pytest.importorskip("tools.r4bt_c0_parity")
    w = gate_world(tmp_path)
    br, port, clk = w.bridge(parity=False)
    sd = br.cfg.state_dir
    res = {"verdict": "parity not shown", "stop_required": False, "r4bt": {"hashes": {"ledger_sha256": hashlib.sha256((Path(sd) / T.LEDGER_NAME).read_bytes()).hexdigest(),
                                                                                    "attempts_sha256": hashlib.sha256((Path(sd) / T.ATTEMPTS_NAME).read_bytes()).hexdigest()},
                                                         "counts": {}, "twin_run_ms": 1, "p2_evaluated": True}, "halts": {}}
    assert parity.write_state(sd, w.at - 1000, 0, res) is None
    assert T.parity_state_problem(sd, w.at) is None
    feed(br, clk)
    assert sends(port) == [("buy", f"MINT:{mark(0)}")]
    # a failed run (exit 2, stop required) writes a state the gate refuses
    assert parity.write_state(sd, w.at - 1000, 2, None) is None
    assert T.parity_state_problem(sd, w.at) == "parity_gate:exit_code"
    assert T.PARITY_SCHEMA == parity.STATE_SCHEMA and T.PARITY_STATE_NAME == parity.STATE_NAME
