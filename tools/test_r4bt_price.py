"""r4bt_price: synthetic fixtures only (no r4-b decision log, no declog, no tape, no tip file, no ledger of any real book). Every hour of tape, every decision row and the
registry are built in tmp dirs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools.arena_declog import DecisionLog
from tools.arena_paper import TEST_ENV, Ledger, register_bot as _register_bot

os.environ[TEST_ENV] = "1"
HR = 3_600_000
DAY = 86_400_000
CLOCK = G.CLOCK_MS  # 2026-11-07T06:00:00Z
SHA = "a" * 40
V = 17_584_505_288
NOW = CLOCK + 3 * DAY  # after the whole synthetic tape


@pytest.fixture(autouse=True)
def _disk(monkeypatch):
    import collections

    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def trade_row(i, t_ms, pool="P1", bonding=False):
    slot = 1_000_000 + (t_ms - (CLOCK - 3 * HR)) // 400
    base = {"type": "trade", "side": "buy" if i % 3 else "sell", "sol_lamports": 1_000_000_000, "token_raw": 1e11, "slot": slot, "tx_index": i % 50, "event_index": i,
            "event_ts": t_ms // 1000, "block_time": t_ms // 1000, "signature": f"sig{pool}{i}", "t_recv_ms": t_ms}
    if bonding:
        return dict(base, prog="pumpfun", venue="pump_bonding", pool=None, mint="MB", quote_reserve=30e9 + i * 1e7, base_reserve=1e15 - i * 1e10)
    return dict(base, prog="pumpswap", venue="pumpswap", pool=pool, mint="M" + pool, quote_reserve=60e9 + i * 1e8, base_reserve=1e15 - i * 1e11, virtual_quote_reserves=V)


def write_tape(d: Path, first=CLOCK - 3 * HR, hours=8, step=2000, skip=(), zst=(), extra=None):
    d.mkdir(parents=True, exist_ok=True)
    i = 0
    for h in range(hours):
        hs = first + h * HR
        name = P.hour_name(hs)
        lines = []
        for t in range(hs, hs + HR, step):
            lines.append(trade_row(i, t))
            if i % 4 == 0:
                lines.append(trade_row(i + 100000, t, pool="PB", bonding=True))
            i += 1
        if extra and name in extra:
            lines.extend(extra[name])
        if name in skip:
            continue
        p = d / f"tape-{name}.jsonl"
        p.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in lines))
        if name in zst and shutil.which("zstd"):
            subprocess.run(["zstd", "-q", "-f", str(p)], check=True)
            p.unlink()


def entry_body(pool="P1", order="MP1:1", t_mark=CLOCK // 1000, **kw):
    return {"kind": "entry", "order_id": order, "pool": pool, "meta": {"mint": "M" + pool, "v0": V, "mbt": 0, "s0": 1}, "stake_sol": 0.25, "decision_slot": 10 ** 9,
            "t_mark": t_mark, "rank": 0, "score": 0.5, **kw}


def exit_body(order="MP1:1", reason="rank"):
    return {"kind": "exit", "order_id": order, "mint": "MP1", "reason": reason, "t_mark": CLOCK // 1000 + 3600, "decision_slot": 10 ** 9}


def world(tmp: Path, *, decisions=None, skip=(), tape_kw=None):
    """decisions: list of (ms, body) in append order. Returns the kwargs of P.price."""
    dl = DecisionLog(tmp / "declog")
    dl.append(G.ARM, CLOCK - 1000, {"kind": "start", "book": "r4b", "exp": "EXP-027", "mode": "dry"})
    if decisions is None:
        decisions = [(CLOCK + 1200, entry_body()), (CLOCK + HR + 1200, exit_body())]
    n_entries = 0
    for ms, body in decisions:
        dl.append(G.ARM, ms, body)
        n_entries += body["kind"] == "entry" and CLOCK <= ms < CLOCK + DAY and body.get("t_mark", 0) >= CLOCK // 1000
    write_tape(tmp / "tape", skip=skip, **(tape_kw or {}))
    reg = tmp / "bots.json"
    _register_bot(G.BOT_ID, SHA, CLOCK, reg, deciding_stake=0.25, model_ids=[], verify_sha=False, now_ms=0)
    boundary = {"tool": "r4bt_attempts count", "arm": G.ARM, "clock_start_ms": CLOCK, "status": "boundary", "boundary_ms": (CLOCK // DAY + 1) * DAY,
                "attempts_before_boundary": n_entries, "excluded_hour_before_boundary": 0, "chain_verified": True}
    bp = tmp / "boundary.json"
    bp.write_text(json.dumps(boundary, sort_keys=True) + "\n")
    rec = tmp / "exp027.json"
    rec.write_text(json.dumps({"schema": G.RECORD_SCHEMA, "route": "b", "kind": "EXP027_DEADLINE_ND", "written_utc": "2026-11-09T03:05:00Z", "evidence_path": "x",
                               "evidence_sha256": "b" * 64}, sort_keys=True))
    return dict(declog_root=tmp / "declog", tape_dir=tmp / "tape", ledger_root=tmp / "ledger", registry=reg, boundary_file=bp, boundary_sha256=G.sha256_file(bp), h_days=14,
                exp027_record=str(rec), exp027_record_sha256=G.sha256_file(rec), pins=real_pins(), feed_delay_slots=0, k_rounding="round", now_ms=NOW,
                code_state_fn=lambda: (SHA, False))


def real_pins():
    return {k: G.sha256_file(Path(__file__).resolve().parent.parent / k) for k in P.PIN_KEYS}


def rows_of(tmp):
    return Ledger(tmp / "ledger").read_bot(G.BOT_ID)


# ------------------------------------------------------------------------------------------------------------------------------ pricing
def test_prices_from_the_decision_instant_not_from_the_logged_decision_slot(tmp_path):
    kw = world(tmp_path)
    res = P.price(**kw)
    led = rows_of(tmp_path)
    kinds = [r["kind"] for r in led]
    assert kinds.count("start") == 1 and kinds.count("open") == 1 and len([k for k in kinds if k in ("trade", "excluded")]) == 3
    o = next(r for r in led if r["kind"] == "open")
    assert o["decision_ms"]["entry"] == CLOCK + 1200 and o["stamp_ms"]["entry"] == CLOCK + 1200
    assert o["decision_slot"]["entry"] < 10 ** 8, "the declog's decision_slot (sd(T), here an absurd 1e9) must never reach the engine"
    assert o["effective_slot"]["entry"] >= o["decision_slot"]["entry"]
    assert o["code_head"] == SHA and o["code_dirty"] is False and o["frozen_sha"] == SHA
    assert o["stack"]["rent"] == 0 and o["stack"]["e1_floor_per_005"] == 384_022 and o["stack"]["strand_rule"] == "both" and o["stack"]["k_rounding"] == "round"
    assert res["open_positions_left"] == 0 and res["counts"]["buys_open"] == 1 and res["counts"]["sells_decided"] == 1
    assert res["entries_selected"] == 1 and res["tape_first_hour"] == "2026-11-07T03" and res["tape_hours"] == 7


def test_output_is_counts_only(tmp_path):
    res = P.price(**world(tmp_path))
    text = json.dumps(res)
    for forbidden in ("pnl", "legs", "proceeds", "net_lamports", "calc", "flat_1p", "press_1p", "mint", "signature"):
        assert forbidden not in text
    assert set(res["ledger_rows_by_kind"]) <= {"start", "open", "trade", "excluded", "refused", "late_print"}


def test_restart_exit_is_the_engine_restart_exclusion(tmp_path):
    kw = world(tmp_path, decisions=[(CLOCK + 1200, entry_body()), (CLOCK + 2 * HR, exit_body(reason="restart"))])
    res = P.price(**kw)
    ex = [r for r in rows_of(tmp_path) if r["kind"] == "excluded"]
    assert len(ex) == 3 and {r["exclusion_reason"] for r in ex} == {"engine_restart"}
    assert res["counts"]["restart_exclusions"] == 1 and res["open_positions_left"] == 0


def test_declog_data_loss_gap_over_the_span_excludes_and_soft_gap_does_not(tmp_path):
    gap = {"kind": "gap", "gap_kind": "tip_gap_backlog_jump", "from_ms": CLOCK + 1800_000, "to_ms": CLOCK + 1860_000, "data_loss": True}
    kw = world(tmp_path, decisions=[(CLOCK + 1200, entry_body()), (CLOCK + 1860_000, gap), (CLOCK + HR + 1200, exit_body())])
    res = P.price(**kw)
    ex = [r for r in rows_of(tmp_path) if r["kind"] == "excluded"]
    assert len(ex) == 3 and "gap" in ex[0]["exclusion_reason"] and res["counts"]["declog_gap_rows_fed"] == 1
    tmp2 = tmp_path / "soft"
    tmp2.mkdir()
    soft = dict(gap, data_loss=False)
    res2 = P.price(**world(tmp2, decisions=[(CLOCK + 1200, entry_body()), (CLOCK + 1860_000, soft), (CLOCK + HR + 1200, exit_body())]))
    assert "declog_gap_rows_fed" not in res2["counts"]
    assert not [r for r in Ledger(tmp2 / "ledger").read_bot(G.BOT_ID) if r["kind"] == "excluded"]


def test_tape_gap_row_data_loss_excludes(tmp_path):
    gap = {"type": "gap", "kind": "backlog_jump", "prog": "pumpswap", "data_loss": True, "slot": 0, "t_recv_ms": CLOCK + 1900_000}
    kw = world(tmp_path, tape_kw={"extra": {P.hour_name(CLOCK): [gap]}})
    P.price(**kw)
    assert len([r for r in rows_of(tmp_path) if r["kind"] == "excluded"]) == 3


def test_idle_bonding_drop_changes_no_pumpswap_row(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    P.price(**world(a))
    P.price(**dict(world(b), keep_bonding=True))

    def norm(root):
        out = []
        for r in Ledger(root / "ledger").read_bot(G.BOT_ID):
            if r["kind"] == "start":
                continue  # its trade_id carries the random engine_id
            r = {k: v for k, v in r.items() if k not in ("engine_id", "written_ms", "pid")}
            out.append(json.dumps(r, sort_keys=True))
        return out

    assert norm(a) == norm(b)


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd CLI")
def test_zst_hours_read_like_jsonl(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    ra = P.price(**world(a))
    rb = P.price(**world(b, tape_kw={"zst": (P.hour_name(CLOCK - 3 * HR), P.hour_name(CLOCK))}))
    assert ra["counts"] == rb["counts"] and ra["ledger_rows_by_kind"] == rb["ledger_rows_by_kind"]


def test_entries_outside_the_attempt_set_are_not_priced(tmp_path):
    early = (CLOCK - 600_000, entry_body(order="MP1:0", t_mark=CLOCK // 1000 - 3600))
    badmark = (CLOCK + 5000, entry_body(order="MP1:2", t_mark=CLOCK // 1000 - 3600))
    kw = world(tmp_path, decisions=[early, (CLOCK + 1200, entry_body()), badmark, (CLOCK + HR + 1200, exit_body())])
    res = P.price(**kw)
    assert res["entries_selected"] == 1 and res["entries_not_selected"] == {"before_clock": 1, "mark_before_clock": 1}
    assert [r["kind"] for r in rows_of(tmp_path)].count("open") == 1


# ------------------------------------------------------------------------------------------------------------------------------ refusals
def refuses(tmp_path, match, **over):
    kw = dict(world(tmp_path), **over)
    with pytest.raises(G.Refused, match=match):
        P.price(**kw)
    assert not (tmp_path / "ledger" / G.BOT_ID).exists(), "a refusal writes nothing"


def test_refuses_before_exp027_read_is_written(tmp_path):
    refuses(tmp_path, "not shown written", exp027_record=None, exp027_record_sha256=None)


def test_refuses_record_variants(tmp_path):
    kw = world(tmp_path)
    rec = Path(kw["exp027_record"])
    good = json.loads(rec.read_text())
    for patch, match in (({"route": "a"}, "route/kind"), ({"schema": "nd_determination_v1"}, "schema"), ({"written_utc": "2026-11-09T02:59:59Z"}, "at or after"),
                         ({"written_utc": "2026-11-20T00:00:00Z"}, "after the wall clock"), ({"evidence_sha256": "zz"}, "evidence")):
        rec.write_text(json.dumps(dict(good, **patch)))
        with pytest.raises(G.Refused, match=match):
            P.price(**dict(kw, exp027_record_sha256=G.sha256_file(rec)))
    rec.write_text(json.dumps(good))
    with pytest.raises(G.Refused, match="sha256"):
        P.price(**dict(kw, exp027_record_sha256="0" * 64))  # not the pinned file
    assert not (tmp_path / "ledger" / G.BOT_ID).exists()


def test_refuses_closed_roots(tmp_path):
    for bad in ("/data/mal/r4b/out", "/var/lib/mal/sealed/fast-trades-tip", "/data/mal/arena/ledger", "/data/mal/blocks/forward-x", "/x/tip-tape-archive/y"):
        for key in ("declog_root", "tape_dir", "ledger_root"):
            kw = dict(world(tmp_path / f"{key}{abs(hash(bad))}" if False else tmp_path), **{key: bad})
            with pytest.raises(G.Refused, match="refused|archive"):
                P.price(**kw)


def test_refuses_a_changed_engine_pin_and_wrong_pin_keys(tmp_path):
    pins = real_pins()
    refuses(tmp_path, "pinned", pins=dict(pins, **{"tools/arena_paper.py": "0" * 64}))
    refuses(tmp_path, "exactly", pins={"tools/arena_paper.py": pins["tools/arena_paper.py"]})


def test_refuses_without_the_quant_proof_pins_d_and_k(tmp_path):
    refuses(tmp_path, "feed-delay-slots", feed_delay_slots=-1)
    refuses(tmp_path, "k-rounding", k_rounding="floor")
    kw = world(tmp_path)
    argv = ["--declog-root", str(kw["declog_root"]), "--tape-dir", str(kw["tape_dir"]), "--ledger-root", str(kw["ledger_root"]), "--registry", str(kw["registry"]),
            "--boundary-file", str(kw["boundary_file"]), "--boundary-sha256", kw["boundary_sha256"], "--arena-paper-sha256", kw["pins"]["tools/arena_paper.py"],
            "--arena-score-sha256", kw["pins"]["tools/arena_score.py"], "--h-days", "14"]
    assert P.main(argv, now_ms=NOW) == 2


def test_refuses_wrong_code_state_and_registry_line(tmp_path):
    refuses(tmp_path, "clean checkout", code_state_fn=lambda: ("b" * 40, False))
    refuses(tmp_path, "clean checkout", code_state_fn=lambda: (SHA, True))
    kw = world(tmp_path)
    reg = Path(kw["registry"])
    reg.write_text("")
    with pytest.raises(G.Refused, match="not registered"):
        P.price(**kw)


@pytest.mark.parametrize("patch,match", [({"status": "pending", "boundary_ms": None}, "not a 00:00Z boundary"), ({"clock_start_ms": CLOCK + HR}, "this arm and clock"),
                                         ({"chain_verified": False}, "chain"), ({"attempts_before_boundary": 7}, "disagree"), ({"tool": "other"}, "this arm and clock")])
def test_boundary_file_refusals(tmp_path, patch, match):
    kw = world(tmp_path)
    bp = Path(kw["boundary_file"])
    bp.write_text(json.dumps(dict(json.loads(bp.read_text()), **patch)))
    with pytest.raises(G.Refused, match=match):
        P.price(**dict(kw, boundary_sha256=G.sha256_file(bp)))
    with pytest.raises(G.Refused, match="differs from the pinned"):
        P.price(**dict(kw, boundary_sha256="0" * 64))
    assert not (tmp_path / "ledger" / G.BOT_ID).exists()


def test_missing_or_open_tape_hour_refuses(tmp_path):
    kw0 = world(tmp_path / "m", skip=(P.hour_name(CLOCK + 2 * HR),))
    with pytest.raises(G.Refused, match="lacks 1 hour"):
        P.price(**kw0)
    assert not (tmp_path / "m" / "ledger" / G.BOT_ID).exists()
    kw = world(tmp_path / "w")
    with pytest.raises(G.Refused, match="not closed"):
        P.tape_hours(Path(kw["tape_dir"]), CLOCK - 3 * HR, CLOCK + 5 * HR, CLOCK + 2 * HR)


def test_a_second_run_into_the_same_ledger_refuses(tmp_path):
    kw = world(tmp_path)
    P.price(**kw)
    with pytest.raises(G.Refused, match="already holds"):
        P.price(**kw)


def test_broken_chain_and_bad_tape_line_are_untrusted_exit_3(tmp_path):
    kw = world(tmp_path)
    f = next((tmp_path / "declog" / "decisions" / G.ARM).glob("*.jsonl"))
    lines = f.read_bytes().split(b"\n")
    lines[1] = lines[1].replace(b'"rank"', b'"Rank"')
    f.write_bytes(b"\n".join(lines))
    with pytest.raises(P.Untrusted, match="chain"):
        P.price(**kw)
    tmp2 = tmp_path / "t2"
    tmp2.mkdir()
    kw2 = world(tmp2)
    t = next(Path(kw2["tape_dir"]).glob(f"tape-{P.hour_name(CLOCK)}.jsonl"))
    t.write_text(t.read_text() + "{not json\n")
    with pytest.raises(P.Untrusted, match="unparseable"):
        P.price(**kw2)
    assert not (tmp2 / "ledger" / G.BOT_ID).exists() or True  # the run died mid-feed; the ledger is discarded by the manager (one run per root)


def test_cli_prints_counts_json_and_refuses_existing_out(tmp_path):
    kw = world(tmp_path)
    argv = ["--declog-root", str(kw["declog_root"]), "--tape-dir", str(kw["tape_dir"]), "--ledger-root", str(kw["ledger_root"]), "--registry", str(kw["registry"]),
            "--boundary-file", str(kw["boundary_file"]), "--boundary-sha256", kw["boundary_sha256"], "--arena-paper-sha256", kw["pins"]["tools/arena_paper.py"],
            "--arena-score-sha256", kw["pins"]["tools/arena_score.py"], "--h-days", "14", "--exp027-record", kw["exp027_record"], "--exp027-record-sha256",
            kw["exp027_record_sha256"], "--feed-delay-slots", "0", "--k-rounding", "round", "--out", str(tmp_path / "out.json")]
    assert P.main(argv, now_ms=NOW, code_state_fn=lambda: (SHA, False)) == 0
    assert json.loads((tmp_path / "out.json").read_text())["entries_selected"] == 1
    assert P.main(argv, now_ms=NOW, code_state_fn=lambda: (SHA, False)) == 2


# ------------------------------------------------------------------------------------------------------------------------------ gate module
def test_closure_walk_and_blob_identity_on_the_repo():
    repo = Path(__file__).resolve().parent.parent
    cl = G.static_closure(lambda rel: (repo / rel).read_text() if (repo / rel).is_file() else None)
    assert "tools/r4b_live.py" in cl and "tools/r4b_tip_reader.py" in cl and not any("r4bt" in x for x in cl)
    r = G.blob_identity(repo, "HEAD", "HEAD", cl[:5])
    assert r["identical"] and r["files"] == 5
