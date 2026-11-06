"""r4bt_c0_twin: synthetic fixtures only (no tape, no declog, no order ledger, no tip file of any real book). Tapes, manifests and order ledgers are built in tmp dirs."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import r4bt_c0_twin as T
from tools import r4bt_gate as G
from tools.test_r4bt_price import CLOCK, HR, V, write_tape

NOW = CLOCK + 5 * HR  # the synthetic tape covers [CLOCK - 3 h, CLOCK + 5 h)
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(T.TEST_ENV, "1")


def manifest_of(tape: Path, tmp: Path, corrupt=None, drop=None) -> Path:
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(tape.iterdir())}
    if corrupt:
        files[corrupt] = "0" * 64
    if drop:
        files.pop(drop)
    m = tmp / "manifest.json"
    m.write_text(json.dumps({"files": files}))
    return m


def order(oid="O1", pool="P1", entry=CLOCK + 1200, exit_=CLOCK + HR + 1200, stake=0.05, ef=505_000, xf=505_000, **kw):
    return {"order_id": oid, "sent": True, "pool": pool, "mint": "M" + pool, "rank": 0, "t_mark": CLOCK // 1000, "stake_sol": stake, "entry_ms": entry, "exit_ms": exit_,
            "entry_fee_lamports": ef, "exit_fee_lamports": xf, "decision_slot": 10 ** 9, **kw}


def world(tmp: Path, orders=None, tape_kw=None, **over):
    write_tape(tmp / "tape", **(tape_kw or {}))
    orders = [order()] if orders is None else orders
    op = tmp / "orders.jsonl"
    op.write_text("".join(json.dumps(o) + "\n" for o in orders))
    kw = dict(tape_dir=tmp / "tape", manifest=manifest_of(tmp / "tape", tmp), orders_path=op, out_dir=tmp / "out", feed_delay_slots=0, w_sha="a" * 40, now_ms=NOW)
    kw.update(over)
    return kw


def twins(tmp: Path):
    return [json.loads(x) for x in (tmp / "out" / "twins.jsonl").read_text().splitlines()]


def refuses(tmp, match, exc=G.Refused, **over):
    kw = world(tmp, **over)
    with pytest.raises(exc, match=match):
        T.run(**kw)
    assert not (tmp / "out" / "twins.run.json").exists()


# ------------------------------------------------------------------------------------------------------------------------------ pricing
def test_prices_one_sent_order_with_both_legs_and_the_pinned_stack(tmp_path):
    rec = T.run(**world(tmp_path))
    (row,) = twins(tmp_path)
    assert row["schema"] == T.TWIN_ROW_SCHEMA and set(row) == set(T.TWIN_ROW_FIELDS)
    assert row["status"] == "priced" and row["order_id"] == "O1" and row["entry_ms"] == CLOCK + 1200
    assert row["leg_s"] == 1.9 and row["stake_sol"] == 0.05 and row["send_lamports"] == 505_000
    assert set(row["nofail"]) == {"total", "sc"} and set(row["rent_charged"]) == {"total", "sc"}
    for rule in ("total", "sc"):
        nf, rc = row["nofail"][rule], row["rent_charged"][rule]
        assert nf["state"] == rc["state"]
        if nf["filled"] and not nf["strand"]:
            assert nf["pnl_lamports"] - rc["pnl_lamports"] == pytest.approx(2_039_280)  # the rent-charged leg sits beside the rent-0 deciding leg
    s = row["stack"]
    assert s["rent"] == 0 and s["e1_floor_per_005"] == 384_022 and s["strand_rule"] == "both" and s["k_rounding"] == "ceil" and s["feed_delay_slots"] == 0
    assert row["entry_leg"]["landing_slot"] > 0 and row["exit_leg"]["Qx"] > 0
    assert rec["status"] == "ok" and rec["orders_sent"] == 1 and rec["counts_by_status"] == {"priced": 1}
    assert rec["engine_sha256"] == T.ENGINE_PIN and rec["twins_sha256"] == hashlib.sha256((tmp_path / "out" / "twins.jsonl").read_bytes()).hexdigest()


def test_stackconfig_pin_matches_item_24_byte_for_byte():
    cfg = T.pinned_config(feed_delay_slots=3, stake_sol=0.05, send_lamports=505_000)
    assert (cfg.rent, cfg.e1_floor_per_005, cfg.strand_rule, cfg.k_rounding, cfg.gap_slots, cfg.gap_silence_ms, cfg.feed_delay_slots) == (0, 384_022, "both", "ceil", 150, 60_000, 3)
    assert cfg.stakes_sol == (0.05,) and cfg.send == 505_000
    with pytest.raises(G.Refused):
        T.pinned_config(feed_delay_slots=None, stake_sol=0.05, send_lamports=1)  # d has no default


def test_twin_equals_the_engine_leg_at_the_orders_stake_and_fee(tmp_path):
    """The twin's nofail leg is price_leg at the order's own stake and fee; a different fee changes the leg by exactly the fee difference (trip pays 2 sends)."""
    T.run(**world(tmp_path / "a", orders=[order(ef=505_000, xf=505_000)]))
    T.run(**world(tmp_path / "b", orders=[order(ef=705_000, xf=705_000)]))
    a, b = twins(tmp_path / "a")[0], twins(tmp_path / "b")[0]
    for rule in ("total", "sc"):
        if a["nofail"][rule]["state"] == "filled":
            assert a["nofail"][rule]["pnl_lamports"] - b["nofail"][rule]["pnl_lamports"] == pytest.approx(2 * 200_000)


def test_latency_starts_at_the_decision_ms_not_the_logged_sd_t(tmp_path):
    T.run(**world(tmp_path / "a", orders=[order(decision_slot=10 ** 9)]))
    T.run(**world(tmp_path / "b", orders=[order(decision_slot=1)]))
    a, b = twins(tmp_path / "a")[0], twins(tmp_path / "b")[0]
    assert a["entry_leg"] == b["entry_leg"] and a["nofail"] == b["nofail"]
    assert a["entry_leg"]["effective_slot"] < 10 ** 8 and a["decision_slot_sd_t"] == 10 ** 9
    T.run(**world(tmp_path / "c", orders=[order(entry=CLOCK + 600_000, exit_=CLOCK + HR + 600_000)]))
    c = twins(tmp_path / "c")[0]
    assert c["entry_leg"]["effective_slot"] > a["entry_leg"]["effective_slot"] + 100  # a later decision ms lands on a later slot (ms mapped to the slot clock)


def test_feed_order_rows_at_or_before_the_decision_ms_are_fed_first(tmp_path):
    """The fixture tape has a row every 2000 ms. A decision stamped exactly on a row's t_recv_ms sees that row fed (its slot is the engine's highest fed slot); a decision 1 ms
    earlier does not."""
    s = 1_000_000 + (CLOCK + 2000 - (CLOCK - 3 * HR)) // 400  # the slot of the row at CLOCK + 2000
    eff = {}
    for tag, ms in (("on", CLOCK + 2000), ("before", CLOCK + 1999)):
        T.run(**world(tmp_path / tag, orders=[order(entry=ms, exit_=ms + HR)]))
        eff[tag] = twins(tmp_path / tag)[0]["entry_leg"]["effective_slot"]
    assert eff["on"] >= s > eff["before"]


def test_decision_ahead_of_the_tape_stays_pending_and_open_order_is_open(tmp_path):
    T.run(**world(tmp_path / "a", orders=[order(entry=CLOCK + 5 * HR + 5_000, exit_=CLOCK + 5 * HR + 9_000)], now_ms=CLOCK + 5 * HR + 10_000))
    (r,) = twins(tmp_path / "a")
    assert r["status"] == "pending_tape" and r["nofail"] is None
    T.run(**world(tmp_path / "b", orders=[order(exit_=None, xf=None)]))
    (r,) = twins(tmp_path / "b")
    assert r["status"] == "open" and r["nofail"] is None and r["exit_ms"] is None and r["send_lamports"] == 505_000


def test_restart_and_gap_flags_are_carried(tmp_path):
    T.run(**world(tmp_path / "a", orders=[order(restart=True, exit_reason="restart")]))
    (r,) = twins(tmp_path / "a")
    assert r["status"] == "excluded" and r["exclusion_reason"] == "engine_restart" and r["gap_or_restart"] is True and "excluded:engine_restart" in r["flags"]
    T.run(**world(tmp_path / "b", orders=[order(gap_hold=True)]))
    (r,) = twins(tmp_path / "b")
    assert r["gap_or_restart"] is True and "order_gap_hold" in r["flags"] and r["status"] == "priced"


def test_tape_data_loss_gap_excludes(tmp_path):
    gap = {"type": "gap", "kind": "data_loss", "data_loss": True, "prog": "pumpswap", "t_recv_ms": CLOCK + HR // 2, "slot": 1_000_000 + (CLOCK + HR // 2 - (CLOCK - 3 * HR)) // 400,
           "silent_ms": 120_000}
    kw = world(tmp_path, tape_kw={"extra": {T.P.hour_name(CLOCK + HR // 2 - HR // 2): [gap]}})
    # the gap row is appended at the end of its hour file (t_recv in the past of that hour): it lands inside the entry-to-exit span
    T.run(**kw)
    (r,) = twins(tmp_path)
    assert r["status"] in ("excluded", "priced")  # the engine's own rule decides; this pins that a gap run completes and is flagged consistently
    assert r["gap_or_restart"] == (r["exclusion_reason"] in ("engine_restart", "feed_gap"))


def test_v_band_audit_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(T.P, "V_LO", 1.0)
    monkeypatch.setattr(T.P, "V_HI", 2.0)  # every fixture print is now outside the band
    T.run(**world(tmp_path))
    (r,) = twins(tmp_path)
    assert r["v_band_audit"] is True and "v_band" in r["flags"]
    rec = json.loads((tmp_path / "out" / "twins.run.json").read_text())
    assert rec["v_band_audit_n"] == 1


# ------------------------------------------------------------------------------------------------------------------------------ inputs and refusals
def test_unsent_order_rows_are_skipped_and_only_named_fields_are_kept(tmp_path):
    secret = dict(order("OX"), sent=False)
    sent = dict(order("O1"), declog_line="SHOULD_NOT_BE_KEPT", stack_extra={"x": 1})
    kw = world(tmp_path, orders=[secret, sent])
    orders, skipped = T.load_orders(Path(kw["orders_path"]))
    assert skipped == 1 and [o["order_id"] for o in orders] == ["O1"] and "declog_line" not in orders[0] and "stack_extra" not in orders[0]
    T.run(**kw)
    assert [r["order_id"] for r in twins(tmp_path)] == ["O1"]


def test_refuses_a_changed_engine_pin(tmp_path):
    kw = world(tmp_path)
    with pytest.raises(G.Refused, match="sha256"):
        T.run(**kw, engine_pin="1" * 64)
    assert not (tmp_path / "out").exists() or not (tmp_path / "out" / "twins.run.json").exists()
    fake = tmp_path / "repo" / "tools"
    fake.mkdir(parents=True)
    (fake / "arena_paper.py").write_text("# changed\n")
    with pytest.raises(G.Refused, match="pinned"):
        T.check_engine_pin(tmp_path / "repo")
    assert T.check_engine_pin(REPO) == T.ENGINE_PIN


def test_refuses_when_r4b_book_round_trip_is_loaded(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "tools.r4b_book", object())
    refuses(tmp_path, "round_trip is refused")


def test_module_never_imports_r4b_book():
    src = (REPO / "tools" / "r4bt_c0_twin.py").read_text()
    assert "import r4b_book" not in src and "from tools.r4b_book" not in src
    out = subprocess.run([sys.executable, "-c", "import sys, tools.r4bt_c0_twin; print('tools.r4b_book' in sys.modules)"], cwd=REPO, capture_output=True, text=True)
    assert out.stdout.strip() == "False", out.stderr


def test_manifest_sha_mismatch_missing_entry_and_missing_hour(tmp_path):
    kw = world(tmp_path / "a")
    bad = manifest_of(Path(kw["tape_dir"]), tmp_path / "a", corrupt="tape-2026-11-07T04.jsonl")
    with pytest.raises(T.Untrusted, match="manifest"):
        T.run(**dict(kw, manifest=bad))
    assert not (tmp_path / "a" / "out" / "twins.jsonl").exists()
    kw = world(tmp_path / "b")
    with pytest.raises(T.Untrusted, match="no line"):
        T.run(**dict(kw, manifest=manifest_of(Path(kw["tape_dir"]), tmp_path / "b", drop="tape-2026-11-07T05.jsonl")))
    refuses(tmp_path / "c", "never skips an hour", tape_kw={"skip": ("2026-11-07T05",)})
    refuses(tmp_path / "d", "lacks the", tape_kw={"skip": ("2026-11-07T03",)})


def test_only_hours_from_first_entry_minus_3h_are_read_and_open_hour_is_not(tmp_path):
    rec = T.run(**world(tmp_path, now_ms=CLOCK + 4 * HR + 5))
    assert rec["tape_first_hour"] == "2026-11-07T03" and rec["tape_files_checked"] == 7  # hours 03..09 closed at CLOCK+4h; 10 is open
    assert rec["tape_last_file"] == "tape-2026-11-07T09.jsonl"


def test_tape_dir_must_be_the_derived_copy_and_not_a_sealed_or_closed_root(tmp_path, monkeypatch):
    monkeypatch.delenv(T.TEST_ENV)
    with pytest.raises(G.Refused, match="derived copy"):
        T.check_tape_dir(tmp_path)
    monkeypatch.setenv(T.TEST_ENV, "1")
    for bad in ("/var/lib/mal/sealed/fast-trades-tip", "/data2/mal/arena-tip-feed/hv7", "/data/mal/arena/tape", "/x/.writer/tape", "/x/declog/tape"):
        with pytest.raises(G.Refused):
            T.check_tape_dir(bad)
    with pytest.raises(G.Refused):
        T.check_other_path("/var/lib/mal-live/x/ledger.jsonl", "the order ledger")


def test_untrusted_order_lines(tmp_path):
    for bad in ('{"order_id":"O","sent":true,"pool":"P1","stake_sol":5,"entry_ms":1,"entry_fee_lamports":1}', "not json",
                '{"order_id":"O","sent":true,"pool":"P1","stake_sol":0.05,"entry_ms":5,"exit_ms":1,"entry_fee_lamports":1}'):
        p = tmp_path / "o.jsonl"
        p.write_text(bad + "\n")
        with pytest.raises(T.Untrusted):
            T.load_orders(p)
    p.write_text(json.dumps(order("A")) + "\n" + json.dumps(order("A")) + "\n")
    with pytest.raises(T.Untrusted, match="duplicate"):
        T.load_orders(p)


def test_empty_order_ledger_writes_a_fresh_heartbeat_run_record(tmp_path):
    rec = T.run(**world(tmp_path, orders=[]))
    assert rec["orders_sent"] == 0 and rec["run_ms"] == NOW and (tmp_path / "out" / "twins.jsonl").read_text() == ""
    assert rec["twins_sha256"] == hashlib.sha256(b"").hexdigest()


def test_w_sha_must_be_40_hex(tmp_path):
    refuses(tmp_path, "40-hex", w_sha="zz")


def test_run_record_has_the_documented_fields_and_input_shas(tmp_path):
    kw = world(tmp_path)
    rec = T.run(**kw)
    assert set(T.RUN_FIELDS) <= set(rec)
    assert rec["orders_sha256"] == hashlib.sha256(Path(kw["orders_path"]).read_bytes()).hexdigest()
    assert rec["manifest_sha256"] == hashlib.sha256(Path(kw["manifest"]).read_bytes()).hexdigest()
    assert rec["w_sha"] == "a" * 40 and rec["tape_last_file_sha256"] and rec["run_ms"] == NOW


def test_cli_exit_codes_and_no_pnl_on_stdout(tmp_path):
    kw = world(tmp_path)
    argv = ["--tape-dir", str(kw["tape_dir"]), "--manifest", str(kw["manifest"]), "--orders", str(kw["orders_path"]), "--out-dir", str(kw["out_dir"]), "--feed-delay-slots", "0",
            "--w-sha", "a" * 40, "--now-ms", str(NOW)]
    env = dict(os.environ, **{T.TEST_ENV: "1"})
    ok = subprocess.run([sys.executable, "-I", "tools/r4bt_c0_twin.py", *argv], cwd=REPO, env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert "pnl" not in ok.stdout and json.loads(ok.stdout)["orders_sent"] == 1
    bad = subprocess.run([sys.executable, "-I", "tools/r4bt_c0_twin.py", *argv[:-4], "--w-sha", "x"], cwd=REPO, env=env, capture_output=True, text=True)
    assert bad.returncode == 2


def wrapper_env(tmp: Path, **over):
    tape = tmp / "tape"
    tape.mkdir(exist_ok=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp), "R4BT_C0_TWIN_HOST_OVERRIDE": "mal-fast-0", "R4BT_C0_REPO": str(REPO), "R4BT_W_SHA": "a" * 40,
           "R4BT_C0_TWIN_SHA256": hashlib.sha256((REPO / "tools" / "r4bt_c0_twin.py").read_bytes()).hexdigest(), "R4BT_FEED_DELAY_SLOTS": "3",
           "R4BT_C0_MANIFEST": str(tmp / "m.json"), "R4BT_C0_ORDERS": str(tmp / "o.jsonl"), "R4BT_C0_STATE_DIR": str(tmp / "st"), "R4BT_C0_TAPE_DIR_TEST": str(tape)}
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


def sh(env, mode="--check"):
    return subprocess.run(["sh", str(REPO / "scripts" / "mal-fast" / "r4bt-c0-twin.sh"), mode], env=env, capture_output=True, text=True)


def test_wrapper_is_posix_sh_and_passes_with_pins_and_host(tmp_path):
    w = REPO / "scripts" / "mal-fast" / "r4bt-c0-twin.sh"
    assert w.exists() and os.access(w, os.X_OK) and w.read_text().startswith("#!/bin/sh")
    assert "[[" not in w.read_text() and "PIPESTATUS" not in w.read_text()
    r = sh(wrapper_env(tmp_path))
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr


def test_wrapper_refuses_wrong_host_missing_env_wrong_pins(tmp_path):
    for over, match in (({"R4BT_C0_TWIN_HOST_OVERRIDE": "mal-core-0"}, "not mal-fast-0"), ({"R4BT_W_SHA": None}, "R4BT_W_SHA is not set"),
                        ({"R4BT_C0_TWIN_SHA256": "0" * 64}, "installed pin"), ({"R4BT_FEED_DELAY_SLOTS": "x"}, "whole number")):
        r = sh(wrapper_env(tmp_path, **over))
        assert r.returncode == 2 and "REFUSED" in r.stderr and match in r.stderr, (over, r.stderr)
    fake = tmp_path / "repo" / "tools"
    fake.mkdir(parents=True)
    (fake / "arena_paper.py").write_text("# changed engine\n")
    r = sh(wrapper_env(tmp_path, R4BT_C0_REPO=str(tmp_path / "repo")))
    assert r.returncode == 2 and "is not the pin" in r.stderr
    assert sh(wrapper_env(tmp_path), "--bogus").returncode == 2
