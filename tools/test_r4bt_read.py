"""r4bt_read: synthetic fixtures only (no real declog, tape, ledger, health table or outcome). The ledger is made by the pricer from a synthetic tape in a tmp dir."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import arena_am6_cut as C
from tools import arena_score as S
from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools import r4bt_read as R
from tools.test_r4bt_price import CLOCK, DAY, HR, NOW, SHA, entry_body, exit_body, real_pins, world  # noqa: F401  (the autouse disk fixture is re-declared below)

import collections


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
    lines = [C.canon({"hour": P.G.iso(h0 + i * HR), "excluded": False, "labels_failing": []}) for i in range(0, 60)]
    sha = __import__("hashlib").sha256("".join(ln + "\n" for ln in lines).encode()).hexdigest()
    return "\n".join(lines + [C.canon({"type": "sha256", "hours": len(lines), "sha256": sha})]) + "\n", sha


def priced(tmp: Path, k="round"):
    kw = world(tmp, decisions=three_trades(), tape_kw={"hours": 10})
    kw["k_rounding"] = k
    P.price(**kw)
    return kw


def rkw(tmp: Path, kw: dict, **over):
    text, sha = health_text()
    base = dict(ledger_root=str(kw["ledger_root"]), registry=str(kw["registry"]), health_texts=[text], health_shas=[sha], restarts_text="", as_of_ms=NOW - DAY,
                boundary_file=str(kw["boundary_file"]), boundary_sha256=kw["boundary_sha256"], exp027_record=kw["exp027_record"], exp027_record_sha256=kw["exp027_record_sha256"],
                pins=real_pins(), feed_delay_slots=0, k_rounding="round", n_books=5, c=2, i_am_the_read=True, now_ms=NOW)
    return dict(base, **over)


def test_end_to_end_prints_the_rent_charged_leg_and_multiplicity(tmp_path):
    kw = priced(tmp_path)
    lines, rb = R.read(**rkw(tmp_path, kw))
    text = "\n".join(lines)
    assert rb is not None and rb["bot_id"] == G.BOT_ID and rb["read_boundary_ms"] == (CLOCK // DAY + 1) * DAY
    assert "A PASS is a SCREEN" in text and "DISCLOSURES" in text
    if rb["status"] in ("PASS", "FAIL"):
        assert "RENT-CHARGED LEG" in text and "MULTIPLICITY" in text and set(rb["r4bt"]["rent_charged"]) == {f"{m}_1p9_{r}" for m in ("flat", "press") for r in ("total", "sc")}
    else:
        pytest.fail(f"synthetic book should reach a PASS/FAIL, got {rb['status']}: {rb.get('reasons')}")


def test_rent_charged_transform_is_the_exact_item_7_formula(tmp_path):
    kw = priced(tmp_path)
    lines, rb = R.read(**rkw(tmp_path, kw))
    rows, _ = R.Ledger(kw["ledger_root"]).scan_bot(G.BOT_ID)
    trades = R.deciding_trades(rows, CLOCK, (CLOCK // DAY + 1) * DAY, 0.25)
    cells = R.deciding_cells(trades, rb["books"]["0.25"]["rules"])
    for rule in rb["books"]["0.25"]["rules"]:
        n_filled = sum(t["calc"]["1p9"]["by_rule"][rule]["state"] == "filled" for t in trades)
        c = cells[f"flat_1p9_{rule}"]
        want = R.RENT_FLAT_SHARE * R.RENT * n_filled / len(trades) / 1e9
        assert c["rent0"]["mean_sol"] - c["rent_charged"]["mean_sol"] == pytest.approx(want, abs=1e-12)
        assert c["rent_charged"]["mean_sol"] <= c["rent0"]["mean_sol"] + 1e-15
        p = cells[f"press_1p9_{rule}"]
        assert p["rent_charged"]["mean_sol"] <= p["rent0"]["mean_sol"] + 1e-15


def test_boundary_and_count_disagreements_refuse(tmp_path):
    kw = priced(tmp_path)
    bp = Path(kw["boundary_file"])
    good = json.loads(bp.read_text())
    for patch, match in (({"boundary_ms": good["boundary_ms"] + DAY}, "disagree"), ({"attempts_before_boundary": 2}, "disagree"), ({"status": "pending"}, "00:00Z boundary")):
        bp.write_text(json.dumps(dict(good, **patch)))
        with pytest.raises(G.Refused, match=match):
            R.read(**rkw(tmp_path, kw, boundary_sha256=G.sha256_file(bp)))
    bp.write_text(json.dumps(good))


def test_refusals_before_any_score(tmp_path):
    kw = priced(tmp_path)
    with pytest.raises(G.Refused, match="i-am-the-read"):
        R.read(**rkw(tmp_path, kw, i_am_the_read=False))
    with pytest.raises(G.Refused, match="not shown written"):
        R.read(**rkw(tmp_path, kw, exp027_record=None, exp027_record_sha256=None))
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


def test_config_pin_mismatch_is_final_not_decidable_and_scores_nothing(tmp_path):
    kw = priced(tmp_path, k="ceil")  # priced at ceil, read with the quant-proof pin "round"
    lines, rb = R.read(**rkw(tmp_path, kw))
    assert rb["status"] == "NOT_DECIDABLE" and rb["reasons"][0].startswith("config_pin_mismatch:k_rounding")
    assert any(ln.startswith("NOT_DECIDABLE") for ln in lines) and not any("RENT-CHARGED" in ln for ln in lines)


def test_check_config_unit():
    pin = {"rent": 0, "k_rounding": "round"}
    assert R.check_config([{"kind": "open", "stack": {"rent": 0, "k_rounding": "round"}}, {"kind": "trade", "stack": {"rent": 5}}], pin) == []
    assert R.check_config([{"kind": "start", "stack": {"rent": 2_039_280, "k_rounding": "round"}}], pin)[0] == "rent"


def test_cli_refuses_without_the_pins_and_parameters(tmp_path):
    assert R.main(["--i-am-the-read", "--ledger-root", str(tmp_path), "--registry", str(tmp_path / "r"), "--restarts", str(tmp_path / "x"), "--boundary-file", "b",
                   "--boundary-sha256", "s", "--arena-paper-sha256", "a", "--arena-score-sha256", "b", "--health", "h", "--health-sha", "s", "--as-of-ms", "1"]) == 2
