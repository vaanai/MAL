"""r4bt_c0_twin: synthetic fixtures only (no tape, no declog, no order ledger, no tip file of any real book). Tapes, counts, verified files and order ledgers are built in tmp dirs."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tools import r4bt_c0_twin as T
from tools import r4bt_gate as G
from tools.test_r4bt_price import CLOCK, HR, V, write_tape

NOW = CLOCK + 5 * HR  # the synthetic tape covers [CLOCK - 3 h, CLOCK + 5 h)
REPO = Path(__file__).resolve().parent.parent
HAVE_W = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", T.R4BT_W + "^{commit}"], capture_output=True).returncode == 0
pytestmark = pytest.mark.skipif(not HAVE_W, reason="W is not in this checkout's object store")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(T.TEST_ENV, "1")
    monkeypatch.setattr(T, "real_host", lambda: "not-fast")


def integrity(tape: Path, skip_verified=(), skip_counts=(), corrupt_sha=None, delete=(), keep_jsonl=()):
    """Turn write_tape's .jsonl hours into the writer's output: tape-<h>.jsonl.zst + .writer/counts.jsonl + .push/verified.json."""
    (tape / ".writer").mkdir(exist_ok=True)
    (tape / ".push").mkdir(exist_ok=True)
    counts, ver = [], {}
    for p in sorted(tape.glob("tape-*.jsonl")):
        name = p.name[5:18]
        data = p.read_bytes()
        counts_row = {"schema": "x", "hour": name, "bytes": len(data), "lines": data.count(b"\n"), "sha256": hashlib.sha256(data).hexdigest(), "rows": {"trade": 1}}
        if name == corrupt_sha:
            counts_row["sha256"] = "0" * 64
        if name not in keep_jsonl:
            subprocess.run(["zstd", "-q", "-f", str(p)], check=True)
            p.unlink()
        if name in delete:
            (tape / f"tape-{name}.jsonl.zst").unlink()
        if name not in skip_counts:
            counts.append(counts_row)
        if name not in skip_verified:
            ver[name] = 1
    (tape / ".writer" / "counts.jsonl").write_text("".join(json.dumps(c, sort_keys=True) + "\n" for c in counts))
    (tape / ".push" / "verified.json").write_text(json.dumps({"hours": ver}))


def order(oid="O1", pool="P1", entry=CLOCK + 1200, exit_=CLOCK + HR + 1200, stake=0.05, ef=505_000, xf=505_000, **kw):
    return {"order_id": oid, "sent": True, "pool": pool, "mint": "M" + pool, "rank": 0, "t_mark": CLOCK // 1000, "stake_sol": stake, "entry_ms": entry, "exit_ms": exit_,
            "entry_fee_lamports": ef, "exit_fee_lamports": xf, "decision_slot": 10 ** 9, **kw}


def world(tmp: Path, orders=None, tape_kw=None, integ=None, **over):
    write_tape(tmp / "tape", **(tape_kw or {}))
    integrity(tmp / "tape", **(integ or {}))
    orders = [order()] if orders is None else orders
    op = tmp / "orders.jsonl"
    op.write_text("".join(json.dumps(o) + "\n" for o in orders))
    kw = dict(tape_dir=tmp / "tape", orders_path=op, out_dir=tmp / "out", feed_delay_slots=0, w_sha=T.R4BT_W, now_ms=NOW)
    kw.update(over)
    return kw


def twins(tmp: Path):
    return [json.loads(x) for x in (tmp / "out" / "twins.jsonl").read_text().splitlines()]


def refuses(tmp, match, exc=G.Refused, integ=None, **over):
    tape_kw = over.pop("tape_kw", None)
    kw = world(tmp, integ=integ, tape_kw=tape_kw, **over)
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
        assert set(nf) == set(T.LEG_PNL_FIELDS) and "pnl_lamports" not in nf
        assert nf["state"] == rc["state"]
        if nf["filled"] and not nf["strand"]:
            assert nf["pnl_e1_excluded_lamports"] - rc["pnl_e1_excluded_lamports"] == pytest.approx(2_039_280)  # the rent-charged leg sits beside the rent-0 deciding leg
    s = row["stack"]
    assert s["rent"] == 0 and s["e1_floor_per_005"] == 384_022 and s["strand_rule"] == "both" and s["k_rounding"] == "ceil" and s["feed_delay_slots"] == 0
    assert row["entry_leg"]["landing_slot"] > 0 and row["exit_leg"]["Qx"] > 0
    assert rec["status"] == "ok" and rec["orders_sent"] == 1 and rec["counts_by_status"] == {"priced": 1}
    assert rec["engine_sha256"] == T.ENGINE_PIN and rec["twins_sha256"] == hashlib.sha256((tmp_path / "out" / "twins.jsonl").read_bytes()).hexdigest()
    assert rec["w_sha"] == T.R4BT_W and rec["module_blobs"] == dict(sorted(T.MODULE_BLOBS.items()))


def test_e1_excluded_is_the_deciding_figure_and_e1_charged_is_its_own_field(tmp_path, monkeypatch):
    """price_leg's pnl is AFTER the E1 floor. At the fixture's stake the haircut exceeds the floor (extra 0), so a stub forces a positive floor to pin the arithmetic."""
    real = T.price_leg
    monkeypatch.setattr(T, "price_leg", lambda *a, **k: dict(real(*a, **k), pnl=-1000.0, e1_extra=250.0))
    T.run(**world(tmp_path))
    (row,) = twins(tmp_path)
    for leg in ("nofail", "rent_charged"):
        for x in row[leg].values():
            assert x["pnl_e1_charged_lamports"] == -1000.0 and x["e1_floor_extra_lamports"] == 250.0 and x["pnl_e1_excluded_lamports"] == -750.0
    T.run(**world(tmp_path / "real"))
    for x in twins(tmp_path / "real")[0]["nofail"].values():
        assert x["pnl_e1_excluded_lamports"] == pytest.approx(x["pnl_e1_charged_lamports"] + x["e1_floor_extra_lamports"])


def test_actual_fees_decide_and_the_registered_fee_gap_is_report_only(tmp_path):
    T.run(**world(tmp_path / "a", orders=[order(ef=505_000, xf=505_000)]))
    T.run(**world(tmp_path / "b", orders=[order(ef=705_000, xf=905_000)]))
    a, b = twins(tmp_path / "a")[0], twins(tmp_path / "b")[0]
    assert (a["entry_fee_lamports"], a["exit_fee_lamports"], a["registered_send_lamports"], a["fee_gap_bps"]) == (505_000, 505_000, 505_000, 0.0)
    assert (b["entry_fee_lamports"], b["exit_fee_lamports"], b["send_lamports"]) == (705_000, 905_000, 805_000)
    assert b["fee_gap_bps"] == pytest.approx((2 * 505_000 - 1_610_000) / (0.05 * 1e9) * 10_000)
    for rule in ("total", "sc"):
        if a["nofail"][rule]["state"] == "filled":  # the trip pays 2 x send: the actual fees change the leg by exactly the extra fees
            assert a["nofail"][rule]["pnl_e1_excluded_lamports"] - b["nofail"][rule]["pnl_e1_excluded_lamports"] == pytest.approx(1_610_000 - 1_010_000)


def test_exit_fee_missing_is_flagged_and_gap_is_none(tmp_path):
    T.run(**world(tmp_path, orders=[order(xf=None)]))
    (r,) = twins(tmp_path)
    assert "exit_fee_missing" in r["flags"] and r["fee_gap_bps"] is None and r["send_lamports"] == 505_000


def test_stackconfig_pin_matches_item_24_byte_for_byte():
    cfg = T.pinned_config(feed_delay_slots=3, stake_sol=0.05, send_lamports=505_000)
    assert (cfg.rent, cfg.e1_floor_per_005, cfg.strand_rule, cfg.k_rounding, cfg.gap_slots, cfg.gap_silence_ms, cfg.feed_delay_slots) == (0, 384_022, "both", "ceil", 150, 60_000, 3)
    assert cfg.stakes_sol == (0.05,) and cfg.send == 505_000
    with pytest.raises(G.Refused):
        T.pinned_config(feed_delay_slots=None, stake_sol=0.05, send_lamports=1)  # d has no default


def test_entry_stage_fields_exist_at_open_and_equal_the_priced_row(tmp_path):
    T.run(**world(tmp_path / "open", orders=[order(exit_=None, xf=None)]))
    T.run(**world(tmp_path / "done"))
    (o,), (p,) = twins(tmp_path / "open"), twins(tmp_path / "done")
    assert o["status"] == "open" and p["status"] == "priced" and o["nofail"] is None
    keys = ("effective_slot", "corrected_slot", "sps_entry", "k_1p9", "k_3p0", "landing_slot")
    assert all(k in o["entry_leg"] for k in keys)
    assert {k: o["entry_leg"][k] for k in keys} == {k: p["entry_leg"][k] for k in keys}
    assert o["entry_leg"]["landing_slot"] == o["entry_leg"]["corrected_slot"] + o["entry_leg"]["k_1p9"]
    assert o["entry_leg"]["k_1p9"] == T.AST.k_slots(1.9, o["entry_leg"]["sps_entry"], "ceil") and o["entry_leg"]["k_3p0"] >= o["entry_leg"]["k_1p9"]


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
    assert r["status"] == "pending_tape" and r["nofail"] is None and r["entry_leg"] is None
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


def test_tape_data_loss_gap_is_counted_in_the_run_record(tmp_path):
    gap = {"type": "gap", "kind": "data_loss", "data_loss": True, "prog": "pumpswap", "t_recv_ms": CLOCK + HR // 2, "slot": 1_000_000 + (CLOCK + HR // 2 - (CLOCK - 3 * HR)) // 400,
           "silent_ms": 120_000}
    rec = T.run(**world(tmp_path, tape_kw={"extra": {T.P.hour_name(CLOCK): [gap]}}))
    (r,) = twins(tmp_path)
    assert r["status"] in ("excluded", "priced") and r["gap_or_restart"] == (r["exclusion_reason"] in ("engine_restart", "feed_gap"))
    assert rec["tape_data_loss_rows_by_prog_kind"] == {"pumpswap:data_loss": 1}


def test_v_band_audit_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(T.P, "V_LO", 1.0)
    monkeypatch.setattr(T.P, "V_HI", 2.0)  # every fixture print is now outside the band
    T.run(**world(tmp_path))
    (r,) = twins(tmp_path)
    assert r["v_band_audit"] is True and "v_band" in r["flags"]
    assert json.loads((tmp_path / "out" / "twins.run.json").read_text())["v_band_audit_n"] == 1


# ------------------------------------------------------------------------------------------------------------------------------ inputs: integrity and retention
def test_unsent_order_rows_are_skipped_and_only_named_fields_are_kept(tmp_path):
    secret = dict(order("OX"), sent=False)
    sent = dict(order("O1"), declog_line="SHOULD_NOT_BE_KEPT", stack_extra={"x": 1})
    kw = world(tmp_path, orders=[secret, sent])
    orders, skipped = T.load_orders(Path(kw["orders_path"]))
    assert skipped == 1 and [o["order_id"] for o in orders] == ["O1"] and "declog_line" not in orders[0] and "stack_extra" not in orders[0]
    T.run(**kw)
    assert [r["order_id"] for r in twins(tmp_path)] == ["O1"]


def test_integrity_sha_and_bytes_mismatch_is_untrusted_before_any_row(tmp_path):
    kw = world(tmp_path / "a", integ={"corrupt_sha": "2026-11-07T04"})
    with pytest.raises(T.Untrusted, match="counts row"):
        T.run(**kw)
    assert not (tmp_path / "a" / "out" / "twins.jsonl").exists()
    kw = world(tmp_path / "b")
    cp = tmp_path / "b" / "tape" / ".writer" / "counts.jsonl"
    rows = [json.loads(x) for x in cp.read_text().splitlines()]
    rows[1]["bytes"] += 1
    cp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(T.Untrusted, match="counts row"):
        T.run(**kw)


def test_hour_is_read_only_if_verified_and_counted_trailing_hours_are_pending(tmp_path):
    rec = T.run(**world(tmp_path / "a", integ={"skip_verified": ("2026-11-07T10",)}))
    assert rec["tape_files_checked"] == 7 and rec["tape_pending_hours"] == 1 and rec["tape_last_file"] == "tape-2026-11-07T09.jsonl.zst"
    rec = T.run(**world(tmp_path / "b", integ={"skip_counts": ("2026-11-07T10",)}))  # present but not counted: not read, pending
    assert rec["tape_files_checked"] == 7 and rec["tape_pending_hours"] == 1
    refuses(tmp_path / "c", "hole", integ={"skip_verified": ("2026-11-07T05",)})


def test_pending_hours_leave_orders_pending_tape(tmp_path):
    T.run(**world(tmp_path, orders=[order(entry=CLOCK + 4 * HR, exit_=CLOCK + 4 * HR + 2000)], integ={"skip_verified": ("2026-11-07T10", "2026-11-07T09")}))
    (r,) = twins(tmp_path)
    assert r["status"] == "pending_tape"


def test_retention_deleted_hour_refuses_with_a_named_reason(tmp_path):
    refuses(tmp_path / "a", "hour_deleted_by_retention", integ={"delete": ("2026-11-07T03",)})
    refuses(tmp_path / "b", "hour_deleted_by_retention", integ={"delete": ("2026-11-07T06",)})


def test_open_hour_jsonl_is_never_read(tmp_path):
    kw = world(tmp_path, integ={"skip_verified": ("2026-11-07T10",), "skip_counts": ("2026-11-07T10",), "keep_jsonl": ("2026-11-07T10",)})
    assert (tmp_path / "tape" / "tape-2026-11-07T10.jsonl").exists()
    rec = T.run(**kw)
    assert rec["tape_files_checked"] == 7 and rec["tape_pending_hours"] == 1


def test_check_mode_prints_names_only_and_refuses_when_oldest_is_later(tmp_path):
    kw = world(tmp_path)
    orders, _ = T.load_orders(Path(kw["orders_path"]))
    out = T.check_tape(Path(kw["tape_dir"]), orders)
    assert out == {"oldest_present_hour": "2026-11-07T03", "first_needed_hour": "2026-11-07T03", "present_hours": 8}
    (tmp_path / "tape" / "tape-2026-11-07T03.jsonl.zst").unlink()
    with pytest.raises(G.Refused, match="hour_deleted_by_retention"):
        T.check_tape(Path(kw["tape_dir"]), orders)


def test_only_the_two_meta_files_and_zst_hours_are_opened(tmp_path, monkeypatch):
    kw = world(tmp_path)
    (tmp_path / "tape" / ".writer" / "state.json").write_text("SEALED")
    opened = []
    real = Path.read_bytes

    def spy(self):
        opened.append(self.name)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    T.run(**kw)
    assert "state.json" not in opened and {"counts.jsonl", "verified.json"} <= set(opened)


def test_tape_loss_and_module_pin_fields_in_run_record(tmp_path):
    rec = T.run(**world(tmp_path))
    assert set(T.RUN_FIELDS) <= set(rec) and "manifest_sha256" not in rec


# ------------------------------------------------------------------------------------------------------------------------------ pins and refusals
def test_refuses_a_changed_engine_pin(tmp_path):
    kw = world(tmp_path)
    with pytest.raises(G.Refused, match="sha256"):
        T.run(**kw, engine_pin="1" * 64)
    assert not (tmp_path / "out" / "twins.run.json").exists()
    fake = tmp_path / "repo" / "tools"
    fake.mkdir(parents=True)
    (fake / "arena_paper.py").write_text("# changed\n")
    with pytest.raises(G.Refused, match="pinned"):
        T.check_engine_pin(tmp_path / "repo")
    assert T.check_engine_pin(REPO) == T.ENGINE_PIN


def test_module_blobs_match_the_loaded_files_and_one_changed_blob_refuses(tmp_path):
    assert T.check_modules(REPO) == dict(sorted(T.MODULE_BLOBS.items()))
    bad = dict(T.MODULE_BLOBS, **{"tools.r4bt_price": "0" * 40})
    kw = world(tmp_path)
    with pytest.raises(G.Refused, match="r4bt_price"):
        T.run(**kw, module_blobs=bad)
    assert not (tmp_path / "out" / "twins.run.json").exists()


def test_an_unlisted_loaded_tools_module_refuses(monkeypatch):
    m = types.ModuleType("tools.zz_unlisted")
    m.__file__ = str(REPO / "tools" / "arena_paper.py")
    monkeypatch.setitem(sys.modules, "tools.zz_unlisted", m)
    with pytest.raises(G.Refused, match="not in the pinned list"):
        T.check_modules(REPO)


def test_w_sha_must_be_the_registered_w_and_resolve_in_the_install_tree(tmp_path):
    refuses(tmp_path / "a", "40-hex", w_sha="zz")
    refuses(tmp_path / "b", "not the registered", w_sha="a" * 40)
    T.check_w(REPO, T.R4BT_W)
    with pytest.raises(G.Refused, match="not resolvable"):
        T.check_w(tmp_path, T.R4BT_W)
    with pytest.raises(G.Refused, match="not the pinned"):
        T.check_w(REPO, T.R4BT_W, dict(T.MODULE_BLOBS, **{"tools.arena_zst": "1" * 40}))


def test_refuses_when_r4b_book_round_trip_is_loaded(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "tools.r4b_book", object())
    refuses(tmp_path, "round_trip is refused")


def test_module_never_imports_r4b_book():
    src = (REPO / "tools" / "r4bt_c0_twin.py").read_text()
    assert "import r4b_book" not in src and "from tools.r4b_book" not in src
    out = subprocess.run([sys.executable, "-c", "import sys, tools.r4bt_c0_twin; print('tools.r4b_book' in sys.modules)"], cwd=REPO, capture_output=True, text=True)
    assert out.stdout.strip() == "False", out.stderr


def test_out_dir_may_not_overlap_the_tape_dir(tmp_path):
    kw = world(tmp_path)
    for bad in (tmp_path / "tape", tmp_path / "tape" / "sub", tmp_path):
        with pytest.raises(G.Refused, match="overlap"):
            T.run(**dict(kw, out_dir=bad))
    assert not any(p.name.startswith("twins") for p in (tmp_path / "tape").rglob("*"))


def test_tape_dir_must_be_the_derived_copy_and_not_a_sealed_or_closed_root(tmp_path, monkeypatch):
    monkeypatch.delenv(T.TEST_ENV)
    with pytest.raises(G.Refused, match="derived copy"):
        T.check_tape_dir(tmp_path)
    monkeypatch.setenv(T.TEST_ENV, "1")
    for bad in ("/var/lib/mal/sealed/fast-trades-tip", "/data2/mal/arena-tip-feed/hv7", "/data/mal/arena/tape", "/x/.writer/tape", "/x/declog/tape",
                "/home/claude/mal-data/arena-mirror/tape", "/x/.push/tape"):
        with pytest.raises(G.Refused):
            T.check_tape_dir(bad)
    with pytest.raises(G.Refused):
        T.check_other_path("/var/lib/mal-live/x/ledger.jsonl", "the order ledger")


def test_test_hook_is_closed_on_mal_fast_0(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "real_host", lambda: "mal-fast-0")
    with pytest.raises(G.Refused, match="closed in production"):
        T.check_tape_dir(tmp_path)


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


def test_run_record_has_input_shas(tmp_path):
    kw = world(tmp_path)
    rec = T.run(**kw)
    assert rec["orders_sha256"] == hashlib.sha256(Path(kw["orders_path"]).read_bytes()).hexdigest()
    assert rec["tape_last_hour_sha256"] and rec["run_ms"] == NOW


def cli_argv(kw):
    return ["--tape-dir", str(kw["tape_dir"]), "--orders", str(kw["orders_path"]), "--out-dir", str(kw["out_dir"]), "--feed-delay-slots", "0", "--w-sha", T.R4BT_W,
            "--now-ms", str(NOW)]


def test_cli_exit_codes_and_no_pnl_on_stdout(tmp_path):
    kw = world(tmp_path)
    env = dict(os.environ, **{T.TEST_ENV: "1"})
    ok = subprocess.run([sys.executable, "-I", "tools/r4bt_c0_twin.py", *cli_argv(kw)], cwd=REPO, env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert "pnl" not in ok.stdout and json.loads(ok.stdout)["orders_sent"] == 1
    bad = subprocess.run([sys.executable, "-I", "tools/r4bt_c0_twin.py", *cli_argv(kw)[:-4], "--w-sha", "x"], cwd=REPO, env=env, capture_output=True, text=True)
    assert bad.returncode == 2
    chk = subprocess.run([sys.executable, "-I", "tools/r4bt_c0_twin.py", "--check", "--tape-dir", str(kw["tape_dir"]), "--orders", str(kw["orders_path"])], cwd=REPO, env=env,
                         capture_output=True, text=True)
    assert chk.returncode == 0 and json.loads(chk.stdout)["oldest_present_hour"] == "2026-11-07T03"


# ------------------------------------------------------------------------------------------------------------------------------ wrapper
def wrapper_env(tmp: Path, **over):
    tape = tmp / "tape"
    tape.mkdir(exist_ok=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp), "R4BT_C0_TWIN_HOST_OVERRIDE": "mal-fast-0", "R4BT_C0_REPO": str(REPO), "R4BT_W_SHA": T.R4BT_W,
           "R4BT_C0_TWIN_SHA256": hashlib.sha256((REPO / "tools" / "r4bt_c0_twin.py").read_bytes()).hexdigest(), "R4BT_FEED_DELAY_SLOTS": "3",
           "R4BT_C0_ORDERS": str(tmp / "o.jsonl"), "R4BT_C0_STATE_DIR": str(tmp / "st"), "R4BT_C0_TAPE_DIR_TEST": str(tape), "R4BT_C0_PYTHON": sys.executable}
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


def test_wrapper_refuses_wrong_host_missing_env_wrong_pins_and_bad_interval(tmp_path):
    for over, match in (({"R4BT_C0_TWIN_HOST_OVERRIDE": "mal-core-0"}, "not mal-fast-0"), ({"R4BT_W_SHA": None}, "R4BT_W_SHA is not set"),
                        ({"R4BT_C0_PYTHON": None}, "R4BT_C0_PYTHON is not set"), ({"R4BT_C0_TWIN_SHA256": "0" * 64}, "installed pin"),
                        ({"R4BT_FEED_DELAY_SLOTS": "x"}, "whole number"), ({"R4BT_C0_INTERVAL": "abc"}, "positive whole number"),
                        ({"R4BT_C0_INTERVAL": "0"}, "positive whole number"), ({"R4BT_C0_INTERVAL": "-5"}, "positive whole number")):
        r = sh(wrapper_env(tmp_path, **over), "--loop")
        assert r.returncode == 2 and "REFUSED" in r.stderr and match in r.stderr, (over, r.stderr)
    fake = tmp_path / "repo" / "tools"
    fake.mkdir(parents=True)
    (fake / "arena_paper.py").write_text("# changed engine\n")
    r = sh(wrapper_env(tmp_path, R4BT_C0_REPO=str(tmp_path / "repo")))
    assert r.returncode == 2 and "is not the pin" in r.stderr
    assert sh(wrapper_env(tmp_path), "--bogus").returncode == 2


def test_wrapper_refuses_test_overrides_when_the_real_host_is_mal_fast_0(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "hostname").write_text("#!/bin/sh\necho mal-fast-0\n")
    (bindir / "hostname").chmod(0o755)
    env = wrapper_env(tmp_path, PATH=str(bindir) + os.pathsep + os.environ["PATH"])
    r = sh(env)
    assert r.returncode == 2 and "test override is set on mal-fast-0" in r.stderr
    env = wrapper_env(tmp_path, PATH=str(bindir) + os.pathsep + os.environ["PATH"], R4BT_C0_TWIN_HOST_OVERRIDE=None)
    r = sh(env)
    assert r.returncode == 2 and "test override is set on mal-fast-0" in r.stderr  # R4BT_C0_TAPE_DIR_TEST alone is refused too
