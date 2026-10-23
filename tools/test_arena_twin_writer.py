"""arena_twin_writer: re-prices a book's paper position at the canary stake, nofail leg, 1.9 s, per-send fees, E1 excluded. Fixtures only (the parity-case pool states)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import arena_live_parity as alp
from tools import arena_twin_writer as W
from tools.arena_paper import StackConfig, price_leg

FIX = Path(__file__).resolve().parent / "fixtures" / "arena_paper" / "parity_cases.jsonl"
DEC = 1_790_000_000_000
STAKE = 50_000_000
BOT = "h5_test_book"
HASH = W.stack_hash(dict(StackConfig(feed_delay_slots=0, strand_rule="both").as_dict()))


def case():
    for line in FIX.read_text().splitlines():
        c = json.loads(line)
        if c["prog"] == "pumpswap" and c["stake_sol"] == 0.25:
            return c
    raise AssertionError


def ledger_row(kind="trade", prog="pumpswap", mint="MINT1", entry_ms=DEC + 400, **over):
    c = case()
    cfg = StackConfig(feed_delay_slots=0, strand_rule="both")
    calc = {"1p9": {"landing_slot": 1, "Qe": c["Qe19"], "Be": c["Be19"], "spot": c["spot"], "ssb": c["ssb19"], "nearby_lamports": c["near19"], "Qx": c["Qx"], "Bx": c["Bx"]}}
    row = {"schema": "arena_trade_v1", "kind": kind, "trade_id": f"p-{mint}-s250", "position_id": f"p-{mint}", "bot_id": BOT, "decision_ms": {"entry": entry_ms, "exit": entry_ms + 335_000},
           "mint": mint, "prog": prog, "stake_sol": 0.25, "stack": dict(cfg.as_dict(), sps_entry=0.4, sps_exit=0.4), "calc": calc if kind == "trade" else None}
    if kind == "excluded":
        row["exclusion_reason"] = "gap"
    row.update(over)
    return row


def trip(mint="MINT1", **over):
    t = {"trip_id": mint, "stake_lamports": STAKE, "decision_ms": DEC, "buy_landed_ms": DEC + 1500, "gap": False, "exit_landed": True, "live_buy_price": 1.0, "live_sell_price": 1.0,
         "live_net_lamports": 0, "exit_landed_ms": DEC + 336_000, "exit_bound_ms": DEC + 340_000, "exit_reason": "timer"}
    t.update(over)
    return t


def write_ledger(tmp, rows):
    d = tmp / "ledger"
    d.mkdir(exist_ok=True)
    (d / f"trades-{W._day(DEC)}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def test_twin_is_the_engines_nofail_leg_at_the_canary_stake_and_send_fees(tmp_path):
    d = write_ledger(tmp_path, [ledger_row()])
    (row,), notes = W.build([trip()], d, BOT, 60_000, 60_000, 10_000, HASH)
    c = case()
    st = {"ref": c["spot"], "Qe": {1.9: c["Qe19"]}, "Be": {1.9: c["Be19"]}, "ssb": {1.9: c["ssb19"]}, "nearby": {1.9: c["near19"]}, "Qx": c["Qx"], "Bx": c["Bx"]}
    cfg = StackConfig(send=60_000, rent=0, feed_delay_slots=0, strand_rule="both", e1_floor_per_005=0)
    direct = price_leg("pumpswap", float(STAKE), st, 1.9, cfg, "total")
    assert direct["state"] == "filled" and row["state"] == "filled"
    assert row["pnl_lamports"] == pytest.approx(direct["pnl"])  # no E1, 0.05 SOL, send 60,000, rent 0: not the book's 0.25 SOL / 505,000 stack
    assert row["pnl_e1_lamports"] < row["pnl_lamports"]  # the E1 floor is reported beside, and it is a cost
    # the stored prices reproduce the pnl by hand (proceeds - stake - 2 sends), so the unit is lamports per raw token
    tokh = STAKE / row["entry_price"] / (1 + cfg.h_in)
    assert row["exit_price"] * tokh - STAKE - 2 * 60_000 == pytest.approx(row["pnl_lamports"], abs=1.0)
    assert set(row["pnl_by_rule"]) == {"total", "sc"}


def test_the_fees_are_the_executors_as_sent(tmp_path):
    d = write_ledger(tmp_path, [ledger_row()])
    base = W.build([trip()], d, BOT, 60_000, 60_000, 10_000, HASH)[0][0]
    esc = W.build([trip(buy_fee_lamports=60_000, sell_fee_lamports=210_000)], d, BOT, 1, 1, 10_000, HASH)[0][0]  # trip fees win over the defaults
    assert esc["pnl_lamports"] == pytest.approx(base["pnl_lamports"] - 150_000)
    assert esc["sell_send_lamports"] == 210_000
    buy = W.build([trip(buy_fee_lamports=160_000)], d, BOT, 60_000, 60_000, 10_000, HASH)[0][0]
    assert buy["pnl_lamports"] == pytest.approx(base["pnl_lamports"] - 100_000, abs=1.0)


def test_no_position_means_no_row_and_excluded_or_foreign_positions_are_handled(tmp_path):
    d = write_ledger(tmp_path, [ledger_row(mint="OTHER"), ledger_row(mint="GAPPED", kind="excluded"), ledger_row(mint="BONDING", prog="pump_bonding"),
                                ledger_row(mint="LATE", entry_ms=DEC + 60_000), ledger_row(mint="OTHERBOT", bot_id="someone_else")])
    rows, notes = W.build([trip("NONE"), trip("GAPPED"), trip("BONDING"), trip("LATE"), trip("OTHERBOT")], d, BOT, 60_000, 60_000, 10_000, HASH)
    assert [r["trip_id"] for r in rows] == ["BONDING", "GAPPED"]
    assert all(r["price_failed"] for r in rows) and "paper_excluded:gap" in rows[1]["reason"]
    assert len(notes) == 3  # NONE, LATE (outside the join tolerance), OTHERBOT


def test_cli_writes_a_file_the_parity_tool_validates_and_finds_eligible(tmp_path):
    d = write_ledger(tmp_path, [ledger_row()])
    t = trip()
    (tmp_path / "trips.jsonl").write_text(json.dumps(t) + "\n")
    out = tmp_path / "twins.jsonl"
    assert W.main(["--trips", str(tmp_path / "trips.jsonl"), "--ledger-dir", str(d), "--bot-id", BOT, "--out", str(out), "--config-hash", HASH]) == 0
    twins = alp.load_jsonl(out)
    alp.validate_inputs([t], twins, [])
    m = alp.classify_trip(t, twins[0])
    assert m["eligible"] and "d" in m and "g_entry" in m and "g_exit" in m
    again = out.read_bytes()
    W.main(["--trips", str(tmp_path / "trips.jsonl"), "--ledger-dir", str(d), "--bot-id", BOT, "--out", str(out), "--config-hash", HASH])
    assert out.read_bytes() == again  # deterministic


def test_no_matching_position_writes_an_empty_file_which_the_parity_tool_fails_closed_on(tmp_path):
    d = write_ledger(tmp_path, [])
    t = trip()
    (tmp_path / "trips.jsonl").write_text(json.dumps(t) + "\n")
    out = tmp_path / "twins.jsonl"
    assert W.main(["--trips", str(tmp_path / "trips.jsonl"), "--ledger-dir", str(d), "--bot-id", BOT, "--out", str(out), "--config-hash", HASH]) == 0
    assert out.read_text() == ""
    now = DEC + 400_000
    st = {"at_ms": {n: now for n in alp.STATE_NAMES}, "unresolved_hard_halts": []}
    assert "P7" in alp.evaluate([t], [], [], st, now)["halts"]


def test_bad_input_writes_nothing(tmp_path):
    out = tmp_path / "twins.jsonl"
    (tmp_path / "trips.jsonl").write_text("{not json\n")
    assert W.main(["--trips", str(tmp_path / "trips.jsonl"), "--ledger-dir", str(tmp_path), "--bot-id", BOT, "--out", str(out), "--config-hash", HASH]) == 2
    assert not out.exists()
    row = ledger_row()
    del row["stack"]["h_in"]
    d = write_ledger(tmp_path, [row])
    (tmp_path / "trips.jsonl").write_text(json.dumps(trip()) + "\n")
    assert W.main(["--trips", str(tmp_path / "trips.jsonl"), "--ledger-dir", str(d), "--bot-id", BOT, "--out", str(out), "--config-hash", HASH]) == 2
    assert not out.exists()


def test_the_recorder_carries_the_fees_as_sent(tmp_path):
    from tools import h5_arena_gates as gt
    rec = gt.ParityRecorder(tmp_path / "r")
    rec.on_buy("M", {"landed": True, "tokens_received": 10, "spend_lamports": 50, "signature": "b", "decision_t_ms": 5, "fee_lamports": 60_000}, 100, None)
    rec.on_sell("M", {"landed": True, "pnl_lamports": 5, "tokens": 10, "sol_received_lamports": 60, "fee_lamports": 160_000, "failed_attempt_cost_lamports": 50_000, "exit_reason": "timer"}, 200)
    t = rec.trips["M"]
    assert (t["buy_fee_lamports"], t["sell_fee_lamports"]) == (60_000, 210_000)


# ---------------------------------------------------------------------------------------------------------------------- QP #826 conditions 1, 2, 5
def run(tmp_path, d, trips, **extra):
    (tmp_path / "trips.jsonl").write_text("".join(json.dumps(t) + "\n" for t in trips))
    args = ["--trips", str(tmp_path / "trips.jsonl"), "--ledger-dir", str(d), "--bot-id", BOT, "--out", str(tmp_path / "twins.jsonl"), "--config-hash", extra.pop("config_hash", HASH)]
    for k, v in extra.items():
        args += [f"--{k.replace('_', '-')}", str(v)]
    return W.main(args)


def test_a_ledger_row_with_a_different_config_refuses_the_run_and_writes_nothing(tmp_path):
    row = ledger_row()
    row["stack"]["h_out"] = 0.0  # a changed haircut
    d = write_ledger(tmp_path, [row])
    assert run(tmp_path, d, [trip()]) == 2
    assert not (tmp_path / "twins.jsonl").exists() and not (tmp_path / "twins.jsonl.run.json").exists()
    d = write_ledger(tmp_path, [ledger_row()])
    assert run(tmp_path, d, [trip()], config_hash="0" * 64) == 2  # and a wrong pin refuses the correct row
    assert run(tmp_path, d, [trip()]) == 0


def test_the_hash_ignores_only_the_per_position_slot_rates():
    s = dict(StackConfig(feed_delay_slots=0).as_dict())
    assert W.stack_hash({**s, "sps_entry": 0.4}) == W.stack_hash({**s, "sps_entry": 0.5, "sps_exit": 1}) == W.stack_hash(s)
    for k in ("send", "rent", "h_in", "h_out", "strand_rule", "e1_floor_per_005", "feed_delay_slots", "press_intercept_provisional"):
        assert W.stack_hash({**s, k: "changed"}) != W.stack_hash(s), k


def test_the_run_record_is_the_writers_own_and_covers_every_trip_read(tmp_path):
    d = write_ledger(tmp_path, [ledger_row()])
    assert run(tmp_path, d, [trip(), trip("NOPOS")], now_ms=1234) == 0
    rec = json.loads((tmp_path / "twins.jsonl.run.json").read_text())
    import hashlib
    assert rec["schema"] == W.RUN_SCHEMA and rec["run_ms"] == 1234 and rec["covered_trip_ids"] == ["MINT1", "NOPOS"] and rec["twin_rows"] == 1
    assert rec["twins_sha256"] == hashlib.sha256((tmp_path / "twins.jsonl").read_bytes()).hexdigest() and rec["config_hash"] == HASH
    assert run(tmp_path, d, [trip()], run_record=tmp_path / "elsewhere.json") == 0 and (tmp_path / "elsewhere.json").exists()


def test_a_failed_run_leaves_the_old_record_untouched_to_go_stale(tmp_path):
    d = write_ledger(tmp_path, [ledger_row()])
    run(tmp_path, d, [trip()], now_ms=1000)
    before = (tmp_path / "twins.jsonl.run.json").read_text()
    assert run(tmp_path, d, [trip()], now_ms=999_000, config_hash="f" * 64) == 2
    assert (tmp_path / "twins.jsonl.run.json").read_text() == before


def test_the_fee_basis_excludes_failed_earlier_buy_sends_and_the_live_side_does_too(tmp_path):
    d = write_ledger(tmp_path, [ledger_row()])
    (row,), _ = W.build([trip()], d, BOT, 60_000, 60_000, 10_000, HASH)
    assert "failed_earlier_buy_sends_excluded" in row["fee_basis"]
    from tools import h5_arena_gates as gt
    rec = gt.ParityRecorder(tmp_path / "r")
    rec.on_buy("M", {"landed": True, "tokens_received": 10, "spend_lamports": 50, "signature": "b", "decision_t_ms": 5, "fee_lamports": 60_000}, 100, None)
    assert rec.trips["M"]["buy_fee_lamports"] == 60_000  # the landed buy only; an earlier failed buy is a separate attempt row with no trip fee
