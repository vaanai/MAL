"""r4bt_read: synthetic fixtures only (no real declog, tape, ledger, health table or outcome). The ledger is made by the pricer from a synthetic tape in a tmp dir."""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path

import pytest

from tools import arena_am6_cut as C
from tools import arena_score as S
from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools import r4bt_read as R
from tools.arena_paper import Ledger, stop_bot
from tools.test_r4bt_price import CLOCK, DAY, HR, NOW, SHA, entry_body, exit_body, real_pins, world  # noqa: F401


@pytest.fixture(autouse=True)
def _disk(monkeypatch):
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))
    monkeypatch.setattr(S, "MIN_N", 3)  # the synthetic book has 3 attempts, not 100: the SAME constants feed the boundary and the cell verdict
    monkeypatch.setattr(S, "MIN_DAYS", 1)


def three_trades():
    d = []
    for i in range(3):
        d.append((CLOCK + i * HR + 1200, entry_body(order=f"MP1:{i + 1}")))
        if i:
            d.append((CLOCK + i * HR + 1200, exit_body(order=f"MP1:{i}")))
    d.append((CLOCK + 3 * HR + 1200, exit_body(order="MP1:3")))
    return d


def health_text():
    h0 = CLOCK - 6 * HR
    lines = [C.canon({"hour": G.iso(h0 + i * HR), "excluded": False, "labels_failing": []}) for i in range(0, 60)]
    sha = hashlib.sha256("".join(ln + "\n" for ln in lines).encode()).hexdigest()
    return "\n".join(lines + [C.canon({"type": "sha256", "hours": len(lines), "sha256": sha})]) + "\n", sha


def priced(tmp: Path, k="ceil", audit=False, monkeypatch=None):
    kw = world(tmp, decisions=three_trades(), tape_kw={"hours": 10})
    kw["k_rounding"] = k
    if audit:
        monkeypatch.setattr(P, "V_HI", 17.0e9)  # the synthetic V is then outside the band: the pricer flags every trade
    ids = tmp / "ids.json"
    res = P.price(**dict(kw, v_audit_ids_out=ids))
    rep = tmp / "report.json"
    rep.write_text(json.dumps(res, sort_keys=True))
    kw.update(report=rep, ids=ids)
    return kw


def rkw(tmp: Path, kw: dict, **over):
    text, sha = health_text()
    base = dict(ledger_root=str(kw["ledger_root"]), registry=str(kw["registry"]), health_texts=[text], health_shas=[sha], restarts_text="", as_of_ms=NOW - DAY,
                boundary_file=str(kw["boundary_file"]), boundary_sha256=kw["boundary_sha256"], exp027_record=kw["exp027_record"], exp027_record_sha256=kw["exp027_record_sha256"],
                pins=real_pins(), feed_delay_slots=0, k_rounding="ceil", n_books=5, c=2, i_am_the_read=True, now_ms=NOW, ancestor=lambda sha: True,
                pricer_report=str(kw["report"]), pricer_report_sha256=G.sha256_file(kw["report"]), v_audit_ids=str(kw["ids"]), v_audit_ids_sha256=G.sha256_file(kw["ids"]))
    return dict(base, **over)


def test_end_to_end_prints_the_rent_charged_leg_gaps_audit_and_multiplicity(tmp_path):
    kw = priced(tmp_path)
    lines, rb = R.read(**rkw(tmp_path, kw))
    text = "\n".join(lines)
    assert rb is not None and rb["bot_id"] == G.BOT_ID and rb["read_boundary_ms"] == (CLOCK // DAY + 1) * DAY
    assert "A PASS is a SCREEN" in text and "DISCLOSURES" in text and "GAPS, report-only" in text and "V AUDIT" in text and "partial" in text
    assert rb["status"] in ("PASS", "FAIL"), rb.get("reasons")
    assert "RENT-CHARGED LEG" in text and "MULTIPLICITY" in text and set(rb["r4bt"]["rent_charged"]) == {f"{m}_1p9_{r}" for m in ("flat", "press") for r in ("total", "sc")}
    assert rb["r4bt"]["v_audit"] == {"n_reported": 0, "n_in_deciding_trades": 0}


def test_rent_charged_transform_is_the_exact_item_7_formula(tmp_path):
    kw = priced(tmp_path)
    lines, rb = R.read(**rkw(tmp_path, kw))
    rows, _ = Ledger(kw["ledger_root"]).scan_bot(G.BOT_ID)
    trades = R.deciding_trades(rows, CLOCK, (CLOCK // DAY + 1) * DAY, 0.25)
    cells = R.deciding_cells(trades, rb["books"]["0.25"]["rules"])
    for rule in rb["books"]["0.25"]["rules"]:
        n_filled = sum(t["calc"]["1p9"]["by_rule"][rule]["state"] == "filled" for t in trades)
        c = cells[f"flat_1p9_{rule}"]
        want = R.RENT_FLAT_SHARE * R.RENT * n_filled / len(trades) / 1e9
        assert c["rent0"]["mean_sol"] - c["rent_charged"]["mean_sol"] == pytest.approx(want, abs=1e-12)
        assert c["deciding"]["mean_sol"] == c["rent0"]["mean_sol"]  # no audit trades: the deciding figure is the rent-0 one
        p = cells[f"press_1p9_{rule}"]
        assert p["rent_charged"]["mean_sol"] <= p["rent0"]["mean_sol"] + 1e-15


def test_v_audit_trades_decide_at_the_rent_charged_leg(tmp_path, monkeypatch):
    kw = priced(tmp_path, audit=True, monkeypatch=monkeypatch)
    lines, rb = R.read(**rkw(tmp_path, kw))
    assert rb["r4bt"]["v_audit"]["n_reported"] == 3 and rb["r4bt"]["v_audit"]["n_in_deciding_trades"] == 3
    rows, _ = Ledger(kw["ledger_root"]).scan_bot(G.BOT_ID)
    trades = R.deciding_trades(rows, CLOCK, (CLOCK // DAY + 1) * DAY, 0.25)
    ids = frozenset(json.loads(Path(kw["ids"]).read_text())["ids"])
    cells = R.deciding_cells(trades, rb["books"]["0.25"]["rules"], ids)
    assert all(v["deciding"]["mean_sol"] == v["rent_charged"]["mean_sol"] for v in cells.values())
    assert rb["status"] in ("PASS", "FAIL")


def test_consistency_refusals_run_before_any_scoring_call(tmp_path, monkeypatch):
    kw = priced(tmp_path)
    monkeypatch.setattr(S, "score_bot", lambda *a, **k: pytest.fail("the scorer was called before the counts-only refusals"))
    bp = Path(kw["boundary_file"])
    good = json.loads(bp.read_text())
    for patch, match in (({"boundary_ms": good["boundary_ms"] + DAY}, "disagree"), ({"attempts_before_boundary": 2}, "disagree"), ({"status": "pending"}, "00:00Z boundary")):
        bp.write_text(json.dumps(dict(good, **patch)))
        rep = Path(kw["report"])
        rep0 = rep.read_text()
        if "boundary_ms" in patch:  # keep the pricer report consistent with the moved boundary: only the ledger-vs-counter refusal is under test
            rep.write_text(json.dumps(dict(json.loads(rep0), boundary_ms=patch["boundary_ms"])))
        with pytest.raises(G.Refused, match=match):
            R.read(**rkw(tmp_path, kw, boundary_sha256=G.sha256_file(bp), pricer_report_sha256=G.sha256_file(rep)))
        rep.write_text(rep0)
    bp.write_text(json.dumps(good))


def test_refusals_before_any_score(tmp_path):
    kw = priced(tmp_path)
    with pytest.raises(G.Refused, match="i-am-the-read"):
        R.read(**rkw(tmp_path, kw, i_am_the_read=False))
    with pytest.raises(G.Refused, match="not shown written"):
        R.read(**rkw(tmp_path, kw, exp027_record=None, exp027_record_sha256=None))
    with pytest.raises(G.Refused, match="not an ancestor"):
        R.read(**rkw(tmp_path, kw, ancestor=lambda sha: False))
    with pytest.raises(G.Refused, match="pinned"):
        R.read(**rkw(tmp_path, kw, pins=dict(real_pins(), **{"tools/arena_score.py": "0" * 64})))
    with pytest.raises(G.Refused, match="future"):
        R.read(**rkw(tmp_path, kw, as_of_ms=NOW + 1))
    with pytest.raises(G.Refused, match="before the boundary"):
        R.read(**rkw(tmp_path, kw, as_of_ms=CLOCK))
    with pytest.raises(G.Refused, match="integers"):
        R.read(**rkw(tmp_path, kw, n_books=0))
    with pytest.raises(G.Refused, match="closed|refused"):
        R.read(**rkw(tmp_path, kw, ledger_root="/data/mal/arena/ledger"))
    with pytest.raises(G.Refused, match="refused"):
        R.read(**rkw(tmp_path, kw, registry="/data/mal/arena/bots.json"))  # r4-b-T's own registry only
    with pytest.raises(G.Refused, match="both required"):
        R.read(**rkw(tmp_path, kw, pricer_report=None))


def test_pricer_report_and_audit_sidecar_are_tied_to_this_book(tmp_path):
    kw = priced(tmp_path)
    rep = Path(kw["report"])
    good = json.loads(rep.read_text())
    for patch, match in (({"boundary_ms": good["boundary_ms"] + DAY}, "not this book's report"), ({"k_rounding": "round"}, "not this book's report"), ({"open_positions_left": 1}, "open positions"),
                         ({"v_audit_n": "x"}, "lacks")):
        rep.write_text(json.dumps(dict(good, **patch)))
        with pytest.raises(G.Refused, match=match):
            R.read(**rkw(tmp_path, kw, pricer_report_sha256=G.sha256_file(rep)))
    rep.write_text(json.dumps(good))
    ids = Path(kw["ids"])
    ids.write_text(json.dumps({"ids": ["x"]}))
    with pytest.raises(G.Refused, match="matching"):
        R.read(**rkw(tmp_path, kw, v_audit_ids_sha256=G.sha256_file(ids)))
    with pytest.raises(G.Refused, match="differs from the pinned"):
        R.read(**rkw(tmp_path, kw, v_audit_ids_sha256="0" * 64))


def test_config_pin_mismatch_is_final_not_decidable_scores_nothing_and_is_recorded(tmp_path, monkeypatch):
    kw = priced(tmp_path, k="round")  # priced at round, read with the quant-proof pin "ceil"
    monkeypatch.setattr(S, "score_bot", lambda *a, **k: pytest.fail("the scorer must not run on a config mismatch"))
    kw2 = dict(kw)
    kw2["report"] = tmp_path / "report_round.json"
    kw2["report"].write_text(json.dumps(dict(json.loads(Path(kw["report"]).read_text()), k_rounding="ceil")))  # the report check is by the pin the manager passes
    reads = tmp_path / "reads"
    lines, rb = R.read(**rkw(tmp_path, kw2, record=True, reads_dir=str(reads)))
    assert rb["status"] == "NOT_DECIDABLE" and rb["reasons"][0].startswith("config_pin_mismatch:k_rounding")
    assert any(ln.startswith("NOT_DECIDABLE") for ln in lines) and not any("RENT-CHARGED" in ln for ln in lines)
    rec = json.loads((reads / f"{G.BOT_ID}.json").read_text())
    assert rec["status"] == "NOT_DECIDABLE" and rec["read_boundary_ms"] == (CLOCK // DAY + 1) * DAY
    with pytest.raises(S.Refused, match="already recorded"):
        R.read(**rkw(tmp_path, kw2, record=True, reads_dir=str(reads)))


def test_a_registry_stop_before_the_boundary_is_a_recorded_not_decidable(tmp_path):
    kw = priced(tmp_path)
    bd = (CLOCK // DAY + 1) * DAY
    stop_bot(G.BOT_ID, bd - HR, kw["registry"])
    reads = tmp_path / "reads"
    lines, rb = R.read(**rkw(tmp_path, kw, record=True, reads_dir=str(reads)))
    assert rb["status"] == "NOT_DECIDABLE" and rb["reasons"] == ["a3_halt_before_boundary"] and (reads / f"{G.BOT_ID}.json").exists()


def test_check_config_unit():
    pin = {"rent": 0, "k_rounding": "ceil"}
    assert R.check_config([{"kind": "open", "stack": {"rent": 0, "k_rounding": "ceil"}}, {"kind": "trade", "stack": {"rent": 5}}], pin) == []
    assert R.check_config([{"kind": "start", "stack": {"rent": 2_039_280, "k_rounding": "ceil"}}], pin)[0] == "rent"


def test_cli_refuses_without_the_pins_and_parameters(tmp_path):
    assert R.main(["--i-am-the-read", "--ledger-root", str(tmp_path), "--registry", str(tmp_path / "r"), "--restarts", str(tmp_path / "x"), "--boundary-file", "b",
                   "--boundary-sha256", "s", "--arena-paper-sha256", "a", "--arena-score-sha256", "b", "--health", "h", "--health-sha", "s", "--as-of-ms", "1"]) == 2
