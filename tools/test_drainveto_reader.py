"""drainveto_reader tests: fixtures only (tmp dirs, synthetic rows). No real tape, no network."""

from __future__ import annotations

import json
import subprocess
import zlib

import pytest

from tools import arena_screen as AS
from tools import drainveto_reader as R

BLK = "oracle-insample-2026-09-22_25"
T0 = 1_790_000_000 - 1_790_000_000 % 3600  # an hour start inside Sep 2026 (2026-09-21T..); only used for relative maths


def zst(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = "".join(json.dumps(r) + "\n" for r in rows).encode()
    subprocess.run(["zstd", "-q", "-f", "-o", str(path)], input=raw, check=True)


def hname(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H")


@pytest.fixture()
def view(tmp_path, monkeypatch):
    root = tmp_path / "clean-view"
    monkeypatch.setattr(AS, "CLEAN_VIEW", str(root))
    monkeypatch.setattr(AS, "BLOCK_ROOTS", tuple(f"{root}/{b}" for b in AS.CANONICAL_BLOCKS))
    return root


def trade(mint, tr, side, tok, slot, ev, t, venue="pumpfun"):
    return {"venue": venue, "mint": mint, "trader": tr, "side": side, "token_raw": tok, "slot": slot, "event_index": ev, "block_time": t,
            "sol_lamports": 123456789, "quote_reserve": 1, "price": 9.9, "creator": "CRE"}


def build(view, rows_by_hour, creates):
    b = view / BLK
    for t, rows in rows_by_hour.items():
        zst(b / "trades" / f"trades-{hname(t)}.jsonl.zst", rows)
    for t, rows in creates.items():
        zst(b / "creates" / f"creates-{hname(t)}.jsonl.zst", rows)


def trig(**kw):
    d = {"blk": BLK, "mint": "M1", "slot": 100, "event_index": 0, "block_time": T0 + 3600 + 50, "Q": 1.0, "B": 100.0, "V": 10.0}
    d.update(kw)
    return d


def test_drain_tokens_algebra():
    assert R.drain_tokens(1.0, 100.0, 10.0) == pytest.approx(10.0)  # Q*B/V
    with pytest.raises(R.ReaderRefused):
        R.drain_tokens(1, 1, 0)


def test_project_drops_forbidden_fields():
    r = trade("M1", "a", "buy", 5, 1, 0, T0)
    p = R.project(r, R.ALLOWED_ROW_FIELDS)
    for bad in ("sol_lamports", "quote_reserve", "price", "creator"):
        assert bad not in p
    assert set(p) <= set(R.ALLOWED_ROW_FIELDS)


def test_reader_balances_cut_and_flags(view, tmp_path):
    rows0 = [trade("M1", "a", "buy", 500, 10, 0, T0 + 5), trade("M1", "b", "buy", 30, 11, 0, T0 + 6),
             trade("M2", "z", "buy", 999, 12, 0, T0 + 7)]  # M2 is not a trigger
    rows1 = [trade("M1", "c", "buy", 20, 90, 0, T0 + 3600 + 1, "pumpswap"), trade("M1", "a", "sell", 100, 95, 0, T0 + 3600 + 2, "pumpswap"),
             trade("M1", "b", "buy", 10_000, 100, 1, T0 + 3600 + 50, "pumpswap"),  # after the cut (event_index 1 > 0)
             trade("M1", "b", "buy", 10_000, 101, 0, T0 + 3600 + 51, "pumpswap")]  # after the cut (slot)
    rows2 = [trade("M1", "b", "buy", 77_777, 500, 0, T0 + 7200 + 5)]  # hour after decision: never read
    build(view, {T0: rows0, T0 + 3600: rows1, T0 + 7200: rows2}, {T0: [{"mint": "M1", "block_time": T0 + 1, "creator": "X", "name": "N"}]})
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trig(split="D")) + "\n")
    out = tmp_path / "out"
    R.run(str(tp), str(out))
    rec = json.loads((out / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec["status"] == "measured"
    # balances: a=400, b=30, c=20 ; total 450 ; D = 10 -> top1 400 >= 10
    assert rec["top1_share"] == pytest.approx(400 / 450)
    assert rec["top3_share"] == pytest.approx(1.0)
    assert rec["n_curve_rows"] == 2 and rec["n_pumpswap_rows"] == 2
    assert rec["top1_ge_D"] is True
    text = (out / "per_pool.jsonl").read_text() + (out / "counts.json").read_text()
    for leak in ("M1", "M2", "sol_lamports", "creator", "price", "reason_pnl"):
        assert leak not in text
    agg = json.loads((out / "counts.json").read_text())
    assert any(r["block"] == BLK and r["split"] == "D" and r["measured"] == 1 and r["flag_top1"] == 1 for r in agg)


def test_not_flagged_when_drain_too_large(view, tmp_path):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]})
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trig(block_time=T0 + 100, Q=10.0, B=100.0, V=1.0)) + "\n")  # D = 1000
    R.run(str(tp), str(tmp_path / "o"))
    rec = json.loads((tmp_path / "o" / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec["top1_ge_D"] is False and rec["top3_ge_D"] is False


def test_pool_without_create_in_block_is_censored_and_counted(view, tmp_path):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {T0: [{"mint": "OTHER", "block_time": T0 + 1}]})
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trig(block_time=T0 + 100)) + "\n")
    R.run(str(tp), str(tmp_path / "o"))
    rec = json.loads((tmp_path / "o" / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec == {"i": 0, "blk": BLK, "split": None, "status": "censored", "reason": "no_create_in_block"}
    agg = json.loads((tmp_path / "o" / "counts.json").read_text())
    row = [r for r in agg if r["split"] == "all"][0]
    assert row["censored"] == 1 and row["censored_no_create_in_block"] == 1 and row["measured"] == 0 and row["flagged_share_top1"] is None


def test_create_hour_before_block_start_is_censored(view, tmp_path):
    # the create row sits in an hour before the first trades hour of the block
    build(view, {T0 + 3600: [trade("M1", "a", "buy", 5, 10, 0, T0 + 3601)]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]})
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trig(block_time=T0 + 3700)) + "\n")
    R.run(str(tp), str(tmp_path / "o"))
    rec = json.loads((tmp_path / "o" / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec["reason"] == "no_create_in_block"


def test_block_without_curve_trader_rows_not_measurable(view, tmp_path):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5, "pumpswap")]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]})
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trig(block_time=T0 + 100)) + "\n")
    R.run(str(tp), str(tmp_path / "o"))
    rec = json.loads((tmp_path / "o" / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec["reason"] == "block_not_measurable"


def test_schema_count_names_and_counts_only(view):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5), trade("M1", "b", "sell", 5, 11, 0, T0 + 6, "pumpswap")]}, {T0: [{"mint": "M1", "block_time": T0}]})
    sc = R.schema_count(BLK)
    assert sc["trades_rows"] == 2 and sc["rows_by_venue"] == {"pumpfun": 1, "pumpswap": 1}
    assert sc["trader_rows_by_venue"]["pumpfun"] == 1 and sc["creates_rows"] == 1
    assert "M1" not in json.dumps(sc) and "a" not in sc["key_names"]


def test_cli_schema_count(view, capsys):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {})
    assert R.main(["--schema-count", "--blocks", BLK]) == 0
    assert "M1" not in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------------------------------------ refusals
@pytest.mark.parametrize("blk", ["fresh-0808", "fresh-0802", "forward-1016", "explore-0814/w1/../../fresh-0808", "/data/mal/arena", "exp011-0909", "fresh-0903"])
def test_refuses_other_roots(blk):
    with pytest.raises(R.ReaderRefused):
        R.block_root(blk)


def test_refuses_extra_trigger_keys(tmp_path):
    p = tmp_path / "t.jsonl"
    for bad in ("Qx", "why", "dur_s", "wQ1", "pnl"):
        p.write_text(json.dumps(trig(**{bad: 1})) + "\n")
        with pytest.raises(R.ReaderRefused):
            R.load_triggers(str(p))


def test_refuses_unknown_block_in_run(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(trig(blk="fresh-0808")) + "\n")
    with pytest.raises(R.ReaderRefused):
        R.run(str(p), str(tmp_path / "o"))


def test_refuses_hour_at_or_after_october(view):
    from datetime import datetime, timezone
    zst(view / BLK / "trades" / "trades-2026-10-01T00.jsonl.zst", [])
    with pytest.raises(R.ReaderRefused):
        R.kind_files(str(view / BLK), "trades")


def test_refuses_sealed_hour(view):
    zst(view / BLK / "trades" / "trades-2026-10-05T00.jsonl.zst", [])
    with pytest.raises(R.ReaderRefused):
        R.kind_files(str(view / BLK), "trades")


def test_refuses_symlink_out_of_block(view, tmp_path):
    other = tmp_path / "elsewhere"
    zst(other / "trades-2026-09-22T00.jsonl.zst", [])
    (view / BLK / "trades").mkdir(parents=True)
    (view / BLK / "trades" / "trades-2026-09-22T00.jsonl.zst").symlink_to(other / "trades-2026-09-22T00.jsonl.zst")
    with pytest.raises(R.ReaderRefused):
        R.kind_files(str(view / BLK), "trades")


def test_refuses_out_dir_in_clean_view(view, tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(trig()) + "\n")
    with pytest.raises(AS.Refused):
        R.run(str(p), str(view / "o"))


def test_main_returns_2_on_refusal(capsys):
    assert R.main(["--schema-count", "--blocks", "fresh-0808"]) == 2


def test_bad_trigger_row_is_censored(view, tmp_path):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]})
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(trig(V=0)) + "\n")
    R.run(str(p), str(tmp_path / "o"))
    rec = json.loads((tmp_path / "o" / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec["reason"] == "bad_trigger_row"
