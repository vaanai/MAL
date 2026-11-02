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
    return dict(base, prog="pumpswap", venue="pumpswap", pool=pool, mint="M" + pool, quote_reserve=60e9 + i * 1e8, base_reserve=1e15 - i * 1e11, virtual_quote_reserves=V,
                quote_mint=P.WSOL)


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
    dl.append(G.ARM, CLOCK - 1000, {"kind": "start", "book": "r4b", "exp": "EXP-027", "mode": "dry", "frozen_sha": SHA})
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
                               "evidence_sha256": "b" * 64, "a2_c4_window_commit": "c" * 40, "a2_c5_ledger_end_commit": "d" * 40}, sort_keys=True))
    ds = tmp / "daysha.json"
    ds.write_text(json.dumps({"day_sha256": {f.stem: G.sha256_file(f) for f in dl.files(G.ARM)}}, sort_keys=True))
    return dict(declog_root=tmp / "declog", tape_dir=tmp / "tape", ledger_root=tmp / "ledger", registry=reg, boundary_file=bp, boundary_sha256=G.sha256_file(bp), h_days=14,
                day_sha_file=ds, day_sha_file_sha256=G.sha256_file(ds), exp027_record=str(rec), exp027_record_sha256=G.sha256_file(rec), pins=real_pins(),
                feed_delay_slots=0, k_rounding="ceil", now_ms=NOW, code_state_fn=lambda: (SHA, False), ancestor=lambda sha: True)


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
    assert o["stack"]["rent"] == 0 and o["stack"]["e1_floor_per_005"] == 384_022 and o["stack"]["strand_rule"] == "both" and o["stack"]["k_rounding"] == "ceil"
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
    assert res["counts"]["engine_restart_exclusions"] == 1 and res["open_positions_left"] == 0


def test_declog_gap_rows_are_counted_by_kind_but_never_fed_to_the_engine(tmp_path):
    gap = {"kind": "gap", "gap_kind": "tip_gap_backlog_jump", "from_ms": CLOCK + 1800_000, "to_ms": CLOCK + 1860_000, "data_loss": True}
    ii = {"kind": "gap", "gap_kind": "incomplete_inputs", "from_ms": None, "to_ms": None, "data_loss": None}
    kw = world(tmp_path, decisions=[(CLOCK + 1200, entry_body()), (CLOCK + 1860_000, gap), (CLOCK + 1900_000, ii), (CLOCK + HR + 1200, exit_body())])
    res = P.price(**kw)
    assert not [r for r in rows_of(tmp_path) if r["kind"] == "excluded"], "Q1-a: a declog gap row is not a price-path gap"
    assert res["declog_gaps"] == {"by_gap_kind": {"incomplete_inputs": 1, "tip_gap_backlog_jump": 1}, "incomplete_inputs_rows": 1, "positions_open_across_incomplete_inputs": 1}
    assert "to_ms" not in json.dumps(res) and "declog_gap_rows_fed" not in res["counts"]


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
                         ({"written_utc": "2026-11-20T00:00:00Z"}, "after the wall clock"), ({"evidence_sha256": "zz"}, "evidence"),
                         ({"a2_c4_window_commit": "short"}, "40-hex"), ({"a2_c5_ledger_end_commit": None}, "40-hex")):
        rec.write_text(json.dumps(dict(good, **patch)))
        with pytest.raises(G.Refused, match=match):
            P.price(**dict(kw, exp027_record_sha256=G.sha256_file(rec)))
    rec.write_text(json.dumps(good))
    with pytest.raises(G.Refused, match="sha256"):
        P.price(**dict(kw, exp027_record_sha256="0" * 64))  # not the pinned file
    with pytest.raises(G.Refused, match="not an ancestor"):  # V6: the A2 C4/C5 commits must be merged
        P.price(**dict(kw, ancestor=lambda sha: sha != "d" * 40))
    assert not (tmp_path / "ledger" / G.BOT_ID).exists()


def test_refuses_closed_roots(tmp_path):
    for bad in ("/data/mal/r4b/out", "/var/lib/mal/sealed/fast-trades-tip", "/data/mal/arena/ledger", "/data/mal/blocks/forward-x", "/x/tip-tape-archive/y"):
        for key in ("declog_root", "tape_dir", "ledger_root", "day_sha_file", "boundary_file"):
            kw = dict(world(tmp_path), **{key: bad})
            with pytest.raises(G.Refused, match="refused|archive"):
                P.price(**kw)
    with pytest.raises(G.Refused, match="refused"):  # r4-b-T's own registry sits outside /data/mal/arena (QP V10): the shared bots.json is never opened
        P.price(**dict(world(tmp_path / "r"), registry="/data/mal/arena/bots.json"))


def test_refuses_a_changed_engine_pin_and_wrong_pin_keys(tmp_path):
    pins = real_pins()
    refuses(tmp_path, "pinned", pins=dict(pins, **{"tools/arena_paper.py": "0" * 64}))
    refuses(tmp_path, "exactly", pins={"tools/arena_paper.py": pins["tools/arena_paper.py"]})


def test_refuses_without_the_quant_proof_pins_d_and_k(tmp_path):
    refuses(tmp_path, "feed-delay-slots", feed_delay_slots=-1)
    refuses(tmp_path, "k-rounding", k_rounding="floor")
    assert P.main(cli_argv(world(tmp_path / "c"), drop=("--feed-delay-slots", "--k-rounding")), now_ms=NOW) == 2


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
    ds = Path(kw["day_sha_file"])
    ds.write_text(json.dumps({"day_sha256": {f.stem: G.sha256_file(f)}}))  # the pinned list matches the tampered file: only the chain can catch it
    with pytest.raises(P.Untrusted, match="chain"):
        P.price(**dict(kw, day_sha_file_sha256=G.sha256_file(ds)))
    tmp2 = tmp_path / "t2"
    tmp2.mkdir()
    kw2 = world(tmp2)
    t = next(Path(kw2["tape_dir"]).glob(f"tape-{P.hour_name(CLOCK)}.jsonl"))
    t.write_text(t.read_text() + "{not json\n")
    with pytest.raises(P.Untrusted, match="unparseable"):
        P.price(**kw2)


def cli_argv(kw, drop=()):
    argv = ["--declog-root", str(kw["declog_root"]), "--tape-dir", str(kw["tape_dir"]), "--ledger-root", str(kw["ledger_root"]), "--registry", str(kw["registry"]),
            "--boundary-file", str(kw["boundary_file"]), "--boundary-sha256", kw["boundary_sha256"], "--day-sha-file", str(kw["day_sha_file"]),
            "--day-sha-file-sha256", kw["day_sha_file_sha256"], "--arena-paper-sha256", kw["pins"]["tools/arena_paper.py"],
            "--arena-score-sha256", kw["pins"]["tools/arena_score.py"], "--h-days", "14", "--exp027-record", kw["exp027_record"], "--exp027-record-sha256",
            kw["exp027_record_sha256"], "--feed-delay-slots", "0", "--k-rounding", "ceil"]
    out = []
    i = 0
    while i < len(argv):
        if argv[i] in drop:
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def test_cli_prints_counts_json_and_refuses_existing_out(tmp_path):
    kw = world(tmp_path)
    argv = cli_argv(kw) + ["--out", str(tmp_path / "out.json")]
    assert P.main(argv, now_ms=NOW, code_state_fn=lambda: (SHA, False), ancestor=lambda sha: True) == 0
    assert json.loads((tmp_path / "out.json").read_text())["entries_selected"] == 1
    assert P.main(argv, now_ms=NOW, code_state_fn=lambda: (SHA, False), ancestor=lambda sha: True) == 2


# ------------------------------------------------------------------------------------------------------------------------------ gate module
def test_closure_walk_and_blob_identity_on_the_repo():
    repo = Path(__file__).resolve().parent.parent
    cl = G.static_closure(lambda rel: (repo / rel).read_text() if (repo / rel).is_file() else None)
    assert "tools/r4b_live.py" in cl and "tools/r4b_tip_reader.py" in cl and not any("r4bt" in x for x in cl)
    r = G.blob_identity(repo, "HEAD", "HEAD", cl[:5])
    assert r["identical"] and r["files"] == 5


def test_a_stop_before_the_boundary_refuses_and_a_stop_after_it_does_not(tmp_path):
    from tools.arena_paper import stop_bot

    kw = world(tmp_path / "a")
    bd = json.loads(Path(kw["boundary_file"]).read_text())["boundary_ms"]
    stop_bot(G.BOT_ID, bd - HR, kw["registry"])
    with pytest.raises(G.Refused, match="stopped at"):
        P.price(**kw)
    assert not (tmp_path / "a" / "ledger" / G.BOT_ID).exists()
    kw2 = world(tmp_path / "b")
    stop_bot(G.BOT_ID, bd + HR, kw2["registry"])
    assert P.price(**kw2)["entries_selected"] == 1



# ------------------------------------------------------------------------------------------------------------------------------ QP-RULING-1102-r4bt V2, V3, V4, V7, V8 and the reviewer's items
def test_day_sha_list_is_verified_before_pricing(tmp_path):
    kw = world(tmp_path)
    f = next((tmp_path / "declog" / "decisions" / G.ARM).glob("*.jsonl"))
    ds = Path(kw["day_sha_file"])
    good = json.loads(ds.read_text())
    for patch, match in (({f.stem: "0" * 64}, "sha256 differs"), ({f.stem: G.sha256_file(f), "2026-11-06": "1" * 64}, "has no file"), ({}, "non-empty")):
        ds.write_text(json.dumps({"day_sha256": patch}))
        with pytest.raises(G.Refused, match=match):
            P.price(**dict(kw, day_sha_file_sha256=G.sha256_file(ds)))
    ds.write_text(json.dumps({"day_sha256": {"2026-11-09": G.sha256_file(f)}}))  # a closed day on disk that is not pinned
    with pytest.raises(G.Refused, match="not in the pinned"):
        P.price(**dict(kw, day_sha_file_sha256=G.sha256_file(ds)))
    with pytest.raises(G.Refused, match="differs from the pinned"):
        P.price(**dict(kw, day_sha_file_sha256="0" * 64))
    assert not (tmp_path / "ledger" / G.BOT_ID).exists()


def test_an_entry_without_a_string_pool_is_untrusted_no_meta_mint_fallback(tmp_path):
    body = dict(entry_body(), pool=None)
    kw = world(tmp_path, decisions=[(CLOCK + 1200, body), (CLOCK + HR + 1200, exit_body())])
    with pytest.raises(P.Untrusted, match="string pool"):
        P.price(**kw)
    assert P.main(cli_argv(kw), now_ms=NOW, code_state_fn=lambda: (SHA, False), ancestor=lambda sha: True) == 3


def test_refuses_while_a_counted_entry_has_no_exit(tmp_path):
    kw = world(tmp_path, decisions=[(CLOCK + 1200, entry_body())])
    with pytest.raises(G.Refused, match="no exit row"):
        P.price(**kw)
    assert not (tmp_path / "ledger" / G.BOT_ID).exists()


def test_start_rows_must_carry_the_registered_frozen_sha(tmp_path):
    kw = world(tmp_path)
    f = next((tmp_path / "declog" / "decisions" / G.ARM).glob("*.jsonl"))
    rows = [json.loads(x) for x in f.read_bytes().splitlines()]
    assert rows[0]["kind"] == "start"
    other = tmp_path / "o"
    other.mkdir()
    dl = DecisionLog(other / "declog")
    dl.append(G.ARM, CLOCK - 1000, {"kind": "start", "book": "r4b", "frozen_sha": "e" * 40})
    for r in rows[1:]:
        dl.append(G.ARM, r["ms"], {k: v for k, v in r.items() if k not in ("seq", "arm", "ms", "prev", "sha")})
    nf = next((other / "declog" / "decisions" / G.ARM).glob("*.jsonl"))
    ds = Path(kw["day_sha_file"])
    ds.write_text(json.dumps({"day_sha256": {nf.stem: G.sha256_file(nf)}}))
    with pytest.raises(G.Refused, match="host ran other code"):
        P.price(**dict(kw, declog_root=other / "declog", day_sha_file_sha256=G.sha256_file(ds)))


def test_a3_stop_instant_inside_the_span_is_an_a3_halt_exclusion(tmp_path):
    from tools.arena_paper import stop_bot

    exit_ms = CLOCK + 19 * HR + 1800_000
    kw = world(tmp_path, decisions=[(CLOCK + 1200, entry_body()), (exit_ms, exit_body())], tape_kw={"hours": 26})
    bd = json.loads(Path(kw["boundary_file"]).read_text())["boundary_ms"]
    stop_bot(G.BOT_ID, bd + HR, kw["registry"])
    res = P.price(**kw)
    ex = [r for r in rows_of(tmp_path) if r["kind"] == "excluded"]
    assert len(ex) == 3 and {r["exclusion_reason"] for r in ex} == {"a3_halt"} and res["counts"]["a3_halt_exclusions"] == 1 and "sells_decided" not in res["counts"]


def test_v_audit_counts_trades_whose_landing_print_leaves_the_band_or_is_not_wsol(tmp_path, monkeypatch):
    ids = tmp_path / "ids.json"
    res = P.price(**dict(world(tmp_path / "a"), v_audit_ids_out=ids))
    assert res["v_audit_n"] == 0 and json.loads(ids.read_text())["ids"] == []
    monkeypatch.setattr(P, "V_HI", 17.0e9)  # the synthetic V (17.58e9) is now outside the band
    ids2 = tmp_path / "ids2.json"
    res2 = P.price(**dict(world(tmp_path / "b"), v_audit_ids_out=ids2))
    got = json.loads(ids2.read_text())["ids"]
    assert res2["v_audit_n"] == 1 and len(got) == 1 and got[0] in {r.get("position_id") for r in Ledger(tmp_path / "b" / "ledger").read_bot(G.BOT_ID)}
    assert "ids" not in json.dumps(res2)
    monkeypatch.setattr(P, "V_HI", 17.7e9)
    monkeypatch.setattr(P, "WSOL", "not-wsol")
    assert P.price(**world(tmp_path / "c"))["v_audit_n"] == 1


def test_confirmation_boundary_file_selects_from_ms_and_needs_a_fresh_root(tmp_path):
    second = (CLOCK + HR + 1200, entry_body(order="MP1:2"))
    kw = world(tmp_path, decisions=[(CLOCK + 1200, entry_body()), second, (CLOCK + HR + 1200, exit_body()), (CLOCK + 2 * HR + 1200, exit_body(order="MP1:2"))])
    bp = Path(kw["boundary_file"])
    b = json.loads(bp.read_text())
    bp.write_text(json.dumps(dict(b, from_ms=CLOCK + HR, attempts_before_boundary=1)))
    res = P.price(**dict(kw, boundary_sha256=G.sha256_file(bp)))
    assert res["from_ms"] == CLOCK + HR and res["entries_selected"] == 1 and res["entries_not_selected"] == {"before_from_ms": 1}
    with pytest.raises(G.Refused, match="already holds"):
        P.price(**dict(kw, boundary_sha256=G.sha256_file(bp)))
