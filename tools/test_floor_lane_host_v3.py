"""l1-floor-v3 plumbing: v2 decisions md5-identical to origin/main, v3@0.5 == v2 except the rule id, v3@1.0 admits a pool v2 refuses, config refusals. Synthetic fixtures only."""

from __future__ import annotations

import json

import pytest

from tools import arena_rule_l1 as L
from tools import arena_rule_l1_v3 as V3
from tools import floor_lane_host as H
from tools.arena_declog import DecisionLog
from tools.arena_paper import canon
from tools.arena_rules import HostError
from tools.arena_rules_testkit import FakeEngine, write_tape
from tools.arena_rules import register_rules
from tools.test_arena_rule_l1 import H0, HOUR, SOLX, pool_rows, row
from tools.test_floor_arena_executor import _host_log_scenarios
from tools.test_floor_lane_host import BINDING, SHA40, _env, lane_host  # noqa: F401  (_env: the autouse fixture)

# md5 of the v2 lane-host decision output on the three-pool fixture, recorded on origin/main bb0322ba BEFORE this change.
V2_DECISION_DIGEST_MAIN = "fea992d89367720188d67792ea8043cd"  # H.decision_digest: canonical entry / exit rows
V2_LOG_MD5_MAIN = "6b6437eba0173668e9ce7ebe8ff8f162"  # DecisionLog.md5: the whole file, start row and chain included

V3_ID = "l1-floor-v3"


def run_rule(tmp, rows, rule_id=H.RULE_V2, q=None, hours=60):
    tape = tmp / "tape"
    if not tape.exists():
        write_tape(tape, rows)
    reg = tmp / "bots.json"
    rule = H._load_rule(rule_id, q)
    rule.table_mode = "first_print"
    if not reg.exists():
        register_rules([rule], str(reg), SHA40, H0, now_ms=H0 - 1, verify_sha=False)
    h = H.make_lane_host(root=str(tmp / "rules"), ledger_root=str(tmp / "ledger"), registry=str(reg), tape_dir=str(tape), clock_start_ms=H0, live=False, binding=BINDING,
                         start_ms=0, table_mode="first_print", engine=FakeEngine(tmp / "rules"), rule_id=rule_id, q_star_lamports=q)
    h.resume()
    h.run(H0, H0 + hours * HOUR)
    return h


def norm(rows, bot):
    """Rows with the chain fields and order_id dropped (order_id is a hash that includes the rule id) and the rule id mapped to v2's, as canonical strings."""
    def one(r):
        r = {k: v for k, v in r.items() if k not in ("prev", "sha", "order_id")}
        if "meta" in r:
            r["meta"] = {k: v for k, v in r["meta"].items() if k != "q_star_lamports"}  # v3's one added meta key
        return canon(r).replace(bot, "l1-floor-v2")
    return [one(r) for r in rows]


def mid_vault_rows():
    """Pool P's drained-pool sell leaves a real vault of ~0.697 SOL after the print: above v2's 0.5 SOL cap, below 1.0."""
    rows = [r for r in pool_rows(with_drop=False)]
    rows.append(row(H0 + 30 * HOUR + 900_000, "P", "sell", q=900_000_000, sol=200_000_000))
    return sorted(rows, key=lambda r: r["t_recv_ms"])


# ------------------------------------------------------------------------------------------------ v2 untouched
def test_v2_decisions_are_md5_identical_to_origin_main(tmp_path):
    h = lane_host(tmp_path, _host_log_scenarios(tmp_path))
    h.resume()
    h.run(H0, H0 + 60 * HOUR)
    assert H.decision_digest(tmp_path / "rules") == V2_DECISION_DIGEST_MAIN
    assert DecisionLog(str(tmp_path / "rules")).md5() == V2_LOG_MD5_MAIN


def test_v2_pins_unchanged_and_v3_module_pinned_only_for_v3():
    assert "tools/arena_rule_l1_v3.py" not in H.pins_for(H.RULE_V2) and H.pins_for(H.RULE_V2) == H.PINS
    assert "tools/arena_rule_l1_v3.py" in H.pins_for(V3_ID)
    H.verify_pins(pins=H.pins_for(V3_ID))  # the pinned sha256 of the v3 module holds on this checkout


def test_the_frozen_rule_module_is_not_edited_and_q_star_is_restored():
    assert L.Q_STAR == 0.5e9
    r = V3.make_rule(1_000_000_000)
    assert r.on_row.__func__ is not L.L1Rule.on_row
    assert L.Q_STAR == 0.5e9 and r.bot_id == V3_ID and L.BOT_ID == "l1-floor-v2"


def cooldown_rows(tmp):
    """The three-pool fixture plus two drained-pool sells on A after its take profit, 30 s and 70 s later: the first is inside the 60 s cooldown, the second is not."""
    rows = _host_log_scenarios(tmp)
    t = max(r["t_recv_ms"] for r in rows if r["pool"] == "A" and r["quote_reserve"] == 60 * SOLX)
    rows.append(row(t + 30_000, "A", "sell", q=600_000_000, sol=200_000_000, n=1))
    rows.append(row(t + 70_000, "A", "sell", q=600_000_000, sol=200_000_000, n=2))
    return sorted(rows, key=lambda r: r["t_recv_ms"])


# recorded with the v2 path as it is before the q_star commit (the v2 path is byte-identical to origin/main by the pin above); COOLDOWN_MS 20000 or 0 gives ddca8067adf5fb03e934a3a790f2c413
V2_COOLDOWN_DIGEST = "ec7befe8cdfe360b1e622a5872eb797d"


def test_v2_cooldown_fixture_pins_cooldown_ms(tmp_path):
    h = lane_host(tmp_path, cooldown_rows(tmp_path))
    h.resume()
    h.run(H0, H0 + 60 * HOUR)
    ent = [r for r in DecisionLog(str(tmp_path / "rules")).rows(L.BOT_ID) if r["kind"] == "entry"]
    assert len(ent) == 4 and all(r["stake_sol"] == L.STAKE_SOL for r in ent)  # A re-enters once, only at the 70 s print
    assert H.decision_digest(tmp_path / "rules") == V2_COOLDOWN_DIGEST


# ------------------------------------------------------------------------------------------------ v3 == v2 at 0.5
def test_v3_at_half_sol_equals_v2_except_the_rule_id(tmp_path):
    rows = _host_log_scenarios(tmp_path)
    a, b = tmp_path / "v2", tmp_path / "v3"
    run_rule(a, rows)
    run_rule(b, rows, V3_ID, 500_000_000)
    la = [r for r in DecisionLog(str(a / "rules")).rows(L.BOT_ID) if r["kind"] in ("entry", "exit")]
    lb = [r for r in DecisionLog(str(b / "rules")).rows(V3_ID) if r["kind"] in ("entry", "exit")]
    assert len([r for r in lb if r["kind"] == "entry"]) == 3 and len([r for r in lb if r["kind"] == "exit"]) == 3  # non-vacuous
    assert all(r["meta"]["rule"] == V3_ID for r in lb if r["kind"] == "entry") and all(r["meta"]["rule"] == L.BOT_ID for r in la if r["kind"] == "entry")
    assert norm(la, L.BOT_ID) == norm(lb, V3_ID)
    ida, idb = [r["order_id"] for r in la], [r["order_id"] for r in lb]  # the ids differ (they hash the rule id) but pair entry to exit identically
    assert all(x != y for x, y in zip(ida, idb)) and [ida.index(x) for x in ida] == [idb.index(y) for y in idb]


# ------------------------------------------------------------------------------------------------ v3 at 1.0 admits what v2 refuses
def test_v3_at_one_sol_admits_a_pool_v2_refuses(tmp_path):
    rows = mid_vault_rows()
    run_rule(tmp_path / "v2", rows, hours=40)
    run_rule(tmp_path / "v3a", rows, V3_ID, 500_000_000, hours=40)
    run_rule(tmp_path / "v3b", rows, V3_ID, 750_000_000, hours=40)
    run_rule(tmp_path / "v3c", rows, V3_ID, 1_000_000_000, hours=40)

    def entries(d, bot):
        return [r for r in DecisionLog(str(tmp_path / d / "rules")).rows(bot) if r["kind"] == "entry"]
    assert entries("v2", L.BOT_ID) == [] and entries("v3a", V3_ID) == []  # real vault ~0.697 SOL > 0.5
    assert len(entries("v3b", V3_ID)) == 1 and len(entries("v3c", V3_ID)) == 1
    e = entries("v3c", V3_ID)[0]
    assert e["pool"] == "P" and e["meta"]["rule"] == V3_ID and e["stake_sol"] == 0.25


# ------------------------------------------------------------------------------------------------ refusals
@pytest.mark.parametrize("bad", [None, 0, 1, 400_000_000, 600_000_000, 2_000_000_000, 0.5e9, 1e9, "1000000000", True, -500_000_000])
def test_v3_refuses_unpinned_q_star(bad):
    with pytest.raises(HostError):
        V3.make_rule(bad)
    with pytest.raises(HostError):
        H.build_rules("first_print", H.LOOKBACK_MIN_S, [None], V3_ID, bad)
    with pytest.raises(HostError):
        H.check_rule_cfg(V3_ID, bad)


@pytest.mark.parametrize("ok", [500_000_000, 750_000_000, 1_000_000_000])
def test_v3_accepts_each_pinned_value(ok):
    assert H.check_rule_cfg(V3_ID, ok) == (V3_ID, ok)
    assert H.build_rules("first_print", H.LOOKBACK_MIN_S, [None], V3_ID, ok)[0].bot_id == V3_ID


def test_v2_refuses_a_q_star_key_and_unknown_rule_ids_refuse():
    for q in (500_000_000, 1_000_000_000, 0):
        with pytest.raises(HostError):
            H.check_rule_cfg(H.RULE_V2, q)
    with pytest.raises(HostError):
        H.build_rules("first_print", H.LOOKBACK_MIN_S, [None], H.RULE_V2, 1_000_000_000)
    for rid in ("l1-floor-v1", "l1-floor-v4", "", None):
        with pytest.raises(HostError):
            H.check_rule_cfg(rid, None)
    assert H.check_rule_cfg(H.RULE_V2, None) == (H.RULE_V2, None)


def test_config_default_is_v2_and_v3_without_q_star_refuses(tmp_path):
    base = {"root": str(tmp_path / "r"), "tape_dir": "x", "clock_start_ms": 1}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(base))
    assert (lambda d: (d["rule_id"], d["q_star_lamports"]))(H._load_cfg(str(p))) == (H.RULE_V2, None)
    p.write_text(json.dumps({**base, "rule_id": V3_ID}))
    with pytest.raises(HostError):
        H._load_cfg(str(p))
    p.write_text(json.dumps({**base, "rule_id": V3_ID, "q_star_lamports": 1_000_000_000}))
    assert H._load_cfg(str(p))["q_star_lamports"] == 1_000_000_000
    p.write_text(json.dumps({**base, "q_star_lamports": 1_000_000_000}))
    with pytest.raises(HostError):
        H._load_cfg(str(p))


def test_main_refuses_v3_with_a_bad_q_star(tmp_path, capsys):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"root": str(tmp_path / "r"), "tape_dir": "x", "clock_start_ms": 1, "rule_id": V3_ID, "q_star_lamports": 999}))
    assert H.main(["verify", "--config", str(p)]) == 2
    assert "q_star_lamports" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ q_star is recorded and checked on resume (reviewer B1)
def test_v3_entry_meta_and_start_row_carry_q_star(tmp_path):
    run_rule(tmp_path, mid_vault_rows(), V3_ID, 1_000_000_000, hours=34)
    log = DecisionLog(str(tmp_path / "rules")).rows(V3_ID)
    ent = [r for r in log if r["kind"] == "entry"]
    start = [r for r in log if r["kind"] == "start"]
    assert len(ent) == 1 and ent[0]["meta"]["q_star_lamports"] == 1_000_000_000
    assert start and all(r["binding"]["q_star_lamports"] == 1_000_000_000 for r in start)


def test_resume_with_a_different_q_star_refuses_and_the_same_continues(tmp_path):
    run_rule(tmp_path, mid_vault_rows(), V3_ID, 750_000_000, hours=34)
    root = tmp_path / "rules"
    before = DecisionLog(str(root)).rows(V3_ID)
    with pytest.raises(HostError, match="q_star_lamports"):
        run_rule(tmp_path, mid_vault_rows(), V3_ID, 1_000_000_000, hours=1)
    assert DecisionLog(str(root)).rows(V3_ID) == before  # a start refusal: no decision row was written
    run_rule(tmp_path, mid_vault_rows(), V3_ID, 750_000_000, hours=1)  # the same q_star continues
    after = DecisionLog(str(root)).rows(V3_ID)
    assert len(after) > len(before) and after[:len(before)] == before


def test_v2_rows_carry_no_q_star(tmp_path):
    run_rule(tmp_path, mid_vault_rows(), H.RULE_V2, hours=34)
    log = DecisionLog(str(tmp_path / "rules")).rows(L.BOT_ID)
    assert log and all("q_star_lamports" not in (r.get("meta") or {}) and "q_star_lamports" not in (r.get("binding") or {}) for r in log)


def test_q_star_is_restored_when_on_row_raises():
    r = V3.make_rule(1_000_000_000)

    class Ctx:
        positions: list = []

        @property
        def state(self):
            raise RuntimeError("boom")
    row_ = {"type": "trade", "prog": "pumpswap", "venue": "pumpswap", "quote_mint": L.WSOL, "pool": "P", "side": "sell"}
    with pytest.raises(RuntimeError):
        r.on_row(Ctx(), row_)
    assert L.Q_STAR == 0.5e9


def test_v2_config_refuses_a_null_q_star_key_too(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"root": str(tmp_path / "r"), "tape_dir": "x", "clock_start_ms": 1, "q_star_lamports": None}))
    with pytest.raises(HostError):
        H._load_cfg(str(p))
    assert H.RULE_V3 == V3.BOT_ID
