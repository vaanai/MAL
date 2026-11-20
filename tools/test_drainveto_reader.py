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


def trade(mint, tr, side, tok, slot, ev, t, venue="pumpfun", tx=0):
    return {"venue": venue, "mint": mint, "trader": tr, "side": side, "token_raw": tok, "slot": slot, "event_index": ev, "tx_index": tx, "block_time": t,
            "sol_lamports": 123456789, "quote_reserve": 1, "price": 9.9, "creator": "CRE"}


def build(view, rows_by_hour, creates, pad=True, blk=BLK):
    b = view / blk
    rows_by_hour = {t: list(r) for t, r in rows_by_hour.items()}
    if pad and rows_by_hour:  # one complete row per venue so the block passes the 99% six-field rule
        t0 = min(rows_by_hour)
        rows_by_hour[t0] += [trade("PAD", "p", "buy", 1, 1, 0, t0, "pumpfun"), trade("PAD", "p", "buy", 1, 1, 0, t0, "pumpswap")]
    for t, rows in rows_by_hour.items():
        zst(b / "trades" / f"trades-{hname(t)}.jsonl.zst", rows)
    for t, rows in creates.items():
        zst(b / "creates" / f"creates-{hname(t)}.jsonl.zst", rows)


def trig(**kw):
    d = {"blk": BLK, "mint": "M1", "slot": 100, "tx_index": 0, "event_index": 0, "block_time": T0 + 3600 + 50, "Q": 1.0, "B": 100.0, "V": 10.0}
    d.update(kw)
    return d


def split_files(path, tmp):
    """Turn the merged fixture trigger lines (trig()) into a locator file + dstate file; returns (locator path, sha256, dstate path)."""
    from datetime import datetime, timezone
    loc, st = [], []
    for i, line in enumerate(open(path), 1):
        t = json.loads(line)
        hr = datetime.fromtimestamp(t["block_time"], tz=timezone.utc).strftime("%Y-%m-%dT%H")
        loc.append({"ordinal": i, "block": t["blk"], "blk": 0, "pool": "PX", "pool_crc32": 1, "mint": t["mint"], "hour": hr, "slot": t["slot"], "tx_index": t["tx_index"],
                    "event_index": t["event_index"], "locator_ok": t.get("locator_ok", True), "locator_problem": "none", "n_qual_in_slot": 1})
        st.append({"ordinal": i, "Q": t["Q"], "B": t["B"], "V": t["V"], "split": t.get("split")})
    lp, sp = tmp / "loc.jsonl", tmp / "dstate.jsonl"
    lp.write_text("".join(json.dumps(r) + "\n" for r in loc))
    sp.write_text("".join(json.dumps(r) + "\n" for r in st))
    return str(lp), R.sha256_file(str(lp)), str(sp)


def run_merged(path, out, schema_json=None):
    from pathlib import Path
    lp, sha, sp = split_files(path, Path(path).parent)
    return R.run(lp, out, schema_json, triggers_sha256=sha, dstate_path=sp)


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
             trade("M1", "b", "buy", 10_000, 100, 1, T0 + 3600 + 50, "pumpswap"),  # after the trigger print (same slot, same tx, later event)
             trade("M1", "b", "buy", 10_000, 100, 0, T0 + 3600 + 50, "pumpswap"),  # the trigger print itself: strictly-before excludes it
             trade("M1", "b", "buy", 10_000, 101, 0, T0 + 3600 + 51, "pumpswap")]  # after the cut (slot)
    rows2 = [trade("M1", "b", "buy", 77_777, 500, 0, T0 + 7200 + 5)]  # hour after decision: never read
    build(view, {T0: rows0, T0 + 3600: rows1, T0 + 7200: rows2}, {T0: [{"mint": "M1", "block_time": T0 + 1, "creator": "X", "name": "N"}]})
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trig(split="D")) + "\n")
    out = tmp_path / "out"
    run_merged(str(tp), str(out))
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
    agg = json.loads((out / "counts.json").read_text())["aggregate"]
    assert any(r["block"] == BLK and r["split"] == "D" and r["measured"] == 1 and r["flag_top1"] == 1 for r in agg)


def test_not_flagged_when_drain_too_large(view, tmp_path):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]})
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trig(block_time=T0 + 100, Q=10.0, B=100.0, V=1.0)) + "\n")  # D = 1000
    run_merged(str(tp), str(tmp_path / "o"))
    rec = json.loads((tmp_path / "o" / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec["top1_ge_D"] is False and rec["top3_ge_D"] is False


def test_pool_without_create_in_block_is_censored_and_counted(view, tmp_path):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {T0: [{"mint": "OTHER", "block_time": T0 + 1}]})
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trig(block_time=T0 + 100)) + "\n")
    run_merged(str(tp), str(tmp_path / "o"))
    rec = json.loads((tmp_path / "o" / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec == {"i": 0, "blk": BLK, "split": None, "status": "censored", "reason": "no_create_in_block"}
    agg = json.loads((tmp_path / "o" / "counts.json").read_text())["aggregate"]
    row = [r for r in agg if r["split"] == "all"][0]
    assert row["censored"] == 1 and row["censored_no_create_in_block"] == 1 and row["measured"] == 0 and row["flagged_share_top1"] is None


def test_create_hour_before_block_start_is_censored(view, tmp_path):
    # the create row sits in an hour before the first trades hour of the block
    build(view, {T0 + 3600: [trade("M1", "a", "buy", 5, 10, 0, T0 + 3601)]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]})
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trig(block_time=T0 + 3700)) + "\n")
    run_merged(str(tp), str(tmp_path / "o"))
    rec = json.loads((tmp_path / "o" / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec["reason"] == "no_create_in_block"


def test_block_without_curve_rows_not_measurable(view, tmp_path):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5, "pumpswap")]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]}, pad=False)
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trig(block_time=T0 + 100)) + "\n")
    run_merged(str(tp), str(tmp_path / "o"))
    rec = json.loads((tmp_path / "o" / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec["reason"] == "block_not_measurable"


def test_schema_count_names_and_counts_only(view):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5), trade("M1", "b", "sell", 5, 11, 0, T0 + 6, "pumpswap")]}, {T0: [{"mint": "M1", "block_time": T0}]}, pad=False)
    sc = R.schema_count(BLK)
    assert sc["trades_rows"] == 2 and sc["venues"]["pumpfun"]["rows"] == 1 and sc["venues"]["pumpswap"]["rows"] == 1
    assert sc["venues"]["pumpfun"]["has"]["trader"] == 1 and sc["venues"]["pumpswap"]["has"]["tx_index"] == 1 and sc["creates_rows"] == 1
    assert sc["venues"]["pumpfun"]["side_values"] == {"buy": 1} and "tx_index" in sc["venues"]["pumpswap"]["key_names"]
    assert "M1" not in json.dumps(sc) and R.measurable(sc)


def test_cli_schema_count(view, capsys):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {})
    assert R.main(["--schema-count", "--blocks", BLK]) == 0
    assert "M1" not in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------------------------------------ refusals
@pytest.mark.parametrize("blk", ["fresh-0808", "fresh-0802", "forward-1016", "explore-0814/w1/../../fresh-0808", "/data/mal/arena", "exp011-0909", "fresh-0903"])
def test_refuses_other_roots(blk):
    with pytest.raises(R.ReaderRefused):
        R.block_root(blk)


def test_refuses_extra_locator_keys_and_dstate_outcome_keys(tmp_path):
    lp = tmp_path / "t.jsonl"
    base = {"ordinal": 1, "block": BLK, "blk": 0, "pool": "P", "pool_crc32": 1, "mint": "M", "hour": "2026-09-22T00", "slot": 1, "tx_index": 0, "event_index": 0,
            "locator_ok": True, "locator_problem": "none", "n_qual_in_slot": 1}
    sp = tmp_path / "s.jsonl"
    sp.write_text(json.dumps({"ordinal": 1, "Q": 1, "B": 1, "V": 1, "split": None}) + "\n")
    for bad in ("Qx", "why", "dur_s", "wQ1", "pnl", "xs", "tdec"):
        lp.write_text(json.dumps({**base, bad: 1}) + "\n")
        with pytest.raises(R.ReaderRefused):
            R.load_triggers(str(lp), R.sha256_file(str(lp)), str(sp))
        lp.write_text(json.dumps(base) + "\n")
        sp.write_text(json.dumps({"ordinal": 1, "Q": 1, "B": 1, "V": 1, "split": None, bad: 1}) + "\n")
        with pytest.raises(R.ReaderRefused):
            R.load_triggers(str(lp), R.sha256_file(str(lp)), str(sp))
        sp.write_text(json.dumps({"ordinal": 1, "Q": 1, "B": 1, "V": 1, "split": None}) + "\n")


def test_refuses_trigger_file_with_wrong_sha(tmp_path):
    lp, sha, sp = _files(tmp_path, trig())
    with pytest.raises(R.ReaderRefused, match="sha256_mismatch"):
        R.load_triggers(lp, "0" * 64, sp)
    with pytest.raises(R.ReaderRefused, match="sha256_mismatch"):
        R.load_triggers(lp, "", sp)
    open(lp, "a").write("\n")  # any byte change after pinning
    with pytest.raises(R.ReaderRefused, match="sha256_mismatch"):
        R.load_triggers(lp, sha, sp)
    assert R.main(["--triggers", lp, "--triggers-sha256", "0" * 64, "--dstate", sp, "--out", str(tmp_path / "o")]) == 2


def _files(tmp_path, t):
    p = tmp_path / "m.jsonl"
    p.write_text(json.dumps(t) + "\n")
    return split_files(str(p), tmp_path)


def test_locator_incomplete_is_censored_and_counted(view, tmp_path):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]})
    rec, ret = _run1(view, tmp_path, trig(block_time=T0 + 100, locator_ok=False))
    assert rec == {"i": 0, "blk": BLK, "split": None, "status": "censored", "reason": "locator_incomplete"}
    row = [r for r in ret["aggregate"] if r["split"] == "all"][0]
    assert row["censored_locator_incomplete"] == 1 and row["measured"] == 0


def test_refuses_unknown_block_in_run(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(trig(blk="fresh-0808")) + "\n")
    with pytest.raises(R.ReaderRefused):
        run_merged(str(p), str(tmp_path / "o"))


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
        run_merged(str(p), str(view / "o"))


def test_main_returns_2_on_refusal(capsys):
    assert R.main(["--schema-count", "--blocks", "fresh-0808"]) == 2


def test_bad_trigger_row_is_censored(view, tmp_path):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]})
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(trig(V=0)) + "\n")
    run_merged(str(p), str(tmp_path / "o"))
    rec = json.loads((tmp_path / "o" / "per_pool.jsonl").read_text().splitlines()[0])
    assert rec["reason"] == "bad_trigger_row"



def _run1(view, tmp_path, trigger, schema_json=None):
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(trigger) + "\n")
    out = tmp_path / "o"
    ret = run_merged(str(tp), str(out), schema_json)
    return json.loads((out / "per_pool.jsonl").read_text().splitlines()[0]), ret


def test_same_slot_later_transaction_does_not_count(view, tmp_path):
    # trigger print: slot 100, tx 2, event 5. A later-tx buyer in the same slot with a LOWER event_index must not count; an earlier-tx row with a HIGHER event_index must count.
    rows = [trade("M1", "a", "buy", 10, 99, 0, T0 + 5),
            trade("M1", "late", "buy", 99_999, 100, 0, T0 + 6, tx=9),  # later tx, event 0 <= 5: the old (slot, event) cut let it through
            trade("M1", "early", "buy", 7, 100, 9, T0 + 6, tx=1),  # earlier tx, event 9 > 5: counts
            trade("M1", "same", "buy", 5_000, 100, 5, T0 + 6, tx=2),  # the trigger print itself: excluded
            trade("M1", "after", "buy", 5_000, 100, 6, T0 + 6, tx=2)]
    build(view, {T0: rows}, {T0: [{"mint": "M1", "block_time": T0 + 1}]})
    rec, ret = _run1(view, tmp_path, trig(slot=100, tx_index=2, event_index=5, block_time=T0 + 10))
    assert rec["status"] == "measured" and rec["n_curve_rows"] == 2 and rec["n_holders"] == 2
    assert rec["top1_share"] == pytest.approx(10 / 17)
    assert ret["skipped_rows_by_block"][BLK]["after_cut"] == 3


def test_same_slot_row_missing_tx_or_event_dropped_not_defaulted(view, tmp_path):
    bad1 = trade("M1", "x", "buy", 1000, 100, 0, T0 + 6)
    del bad1["tx_index"]
    bad2 = trade("M1", "y", "buy", 1000, 100, 0, T0 + 6)
    del bad2["event_index"]
    build(view, {T0: [trade("M1", "a", "buy", 10, 99, 0, T0 + 5), bad1, bad2]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]}, pad=True)
    sj = tmp_path / "schema.json"  # the two deliberately incomplete rows would fail the 99% rule; a filed schema says the block is measurable
    sj.write_text(json.dumps({"block": BLK, "venues": {v: {"rows": 100, "all_six": 100} for v in R.VENUES}}) + "\n")
    rec, ret = _run1(view, tmp_path, trig(slot=100, tx_index=2, event_index=5, block_time=T0 + 10), str(sj))
    assert rec["n_holders"] == 1 and ret["skipped_rows_by_block"][BLK]["same_slot_missing_key"] == 2


def test_no_usable_rows_is_censored_not_measured(view, tmp_path):
    bad = trade("M1", "a", "hold", 5, 10, 0, T0 + 5)  # side not buy/sell
    build(view, {T0: [bad]}, {T0: [{"mint": "M1", "block_time": T0 + 1}]})
    rec, ret = _run1(view, tmp_path, trig(block_time=T0 + 100))
    assert rec["status"] == "censored" and rec["reason"] == "no_usable_rows_before_cut"
    assert ret["skipped_rows_by_block"][BLK]["bad_side"] == 1


def test_oracle_live_proxy_reason(view, tmp_path):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {}, blk=R.ORACLE_LIVE)
    rec, _ = _run1(view, tmp_path, trig(blk=R.ORACLE_LIVE, block_time=T0 + 100))
    assert rec["reason"] == "oracle_live_proxy"


def test_creates_after_last_decision_hour_not_read(view, tmp_path, monkeypatch):
    build(view, {T0: [trade("M1", "a", "buy", 5, 10, 0, T0 + 5)]}, {T0: [{"mint": "M1", "block_time": T0 + 1}], T0 + 7200: [{"mint": "M1", "block_time": T0 + 7201}]})
    sj = tmp_path / "schema.json"
    sj.write_text(json.dumps(R.schema_count(BLK)) + "\n")  # the filed --schema-count output (its own licensed pass)
    opened = []
    orig = R.read_lines

    def spy(path):
        opened.append(path)
        return orig(path)
    monkeypatch.setattr(R, "read_lines", spy)
    rec, _ = _run1(view, tmp_path, trig(block_time=T0 + 100), str(sj))
    assert rec["status"] == "measured"
    assert not any("creates" in p and hname(T0 + 7200) in p for p in opened)


def test_measurability_threshold():
    ok = {"venues": {v: {"rows": 100, "all_six": 99} for v in R.VENUES}}
    low = {"venues": {"pumpfun": {"rows": 100, "all_six": 99}, "pumpswap": {"rows": 100, "all_six": 98}}}
    assert R.measurable(ok) and not R.measurable(low) and not R.measurable({"venues": {"pumpfun": {"rows": 5, "all_six": 5}}})


def test_trigger_requires_tx_index():
    t = trig()
    del t["tx_index"]
    assert not R.trigger_ok(t)
