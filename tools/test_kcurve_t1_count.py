"""Tests for tools/kcurve_t1_count.py (kcurve-boost T1). Synthetic fixtures in tmp dirs only: no forward, walk or
October data file is opened; the real ledger text is parsed for the hour-owner gate, as tools/test_mal_catalog_october.py does."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import kcurve_t1_count as kt
from tools import mal_catalog_october as oc

NOW = datetime(2026, 10, 16, 5, 0, tzinfo=timezone.utc)
TS0 = int(datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc).timestamp())
DAY = 86400


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    oc._reset_latch_for_tests()
    marker = tmp_path / "FINAL_READS.jsonl"
    marker.write_text(json.dumps({"final": True}) + "\n")
    monkeypatch.setattr(oc, "DEFAULT_FINAL_LEDGER", marker)
    monkeypatch.setattr(oc, "_now", lambda: NOW)
    monkeypatch.setattr(oc, "BLOCKS_ROOT", tmp_path / "blocks")
    prereg = tmp_path / "PREREG.md"
    prereg.write_text("frozen prereg text\n")
    monkeypatch.setattr(kt, "PREREG", prereg)
    yield
    oc._reset_latch_for_tests()


def _sha(tmp_path):
    return hashlib.sha256((tmp_path / "PREREG.md").read_bytes()).hexdigest()


def _hour(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H")


def _write(tmp_path, dataset, kind, ts, rows):
    p = tmp_path / "blocks" / dataset / kind / f"{kind}-{_hour(ts)}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _graduation(tmp_path, i, ts, trader, *, synthetic=True, trade_row=True, create_age=120, v0=17_585_000_000):
    m, c, mig = f"MINT{i}", f"CSIG{i}", f"MSIG{i}"
    slot = kt.REDEPLOY_SLOT + 10_000 + i * 10
    _write(tmp_path, "forward-1002ev", "migrations", ts, [
        {"type": "complete", "mint": m, "signature": c, "slot": slot, "event_index": 0, "event_ts": ts, "trader": trader},
        {"type": "migration", "mint": m, "signature": mig, "slot": slot + 2, "event_index": 0, "event_ts": ts + 1,
         "virtual_quote_reserves": v0, "quote_reserve": 1, "base_reserve": 2}])
    if trade_row:
        _write(tmp_path, "forward-1002ev", "trades", ts, [
            {"type": "trade", "venue": "pump_bonding", "mint": m, "trader": trader, "signature": c, "slot": slot,
             "event_index": 1, "event_ts": ts, "side": "buy", "sol_lamports": 123, "quote_mint": "So11111111111111111111111111111111111111112"}])
    if synthetic:
        _write(tmp_path, "forward-1002ev", "events", ts, [
            {"type": "post_complete_buy", "mint": m, "signature": c, "slot": slot, "event_index": 3, "event_ts": ts,
             "quote_in": 999, "base_out": 888}])
    _write(tmp_path, "forward-1002ev", "creates", ts - create_age, [
        {"type": "create", "mint": m, "signature": f"CR{i}", "slot": slot - 300, "event_index": 0, "event_ts": ts - create_age}])


def _run(tmp_path, out=None, notebook="n_test"):
    logs = []
    rc = kt.run(out or tmp_path / "out", _sha(tmp_path), notebook, log=logs.append)
    doc = json.loads(((out or tmp_path / "out") / "t1_result.json").read_text())
    return rc, doc, logs


def test_held_seat_kills(tmp_path):
    for i in range(40):
        _graduation(tmp_path, i, TS0 + i * 60, f"BOT{i % 5}")
    rc, doc, logs = _run(tmp_path)
    assert rc == 0 and doc["verdict"] == "KILL" and doc["top5_share"] == 1.0 and doc["synthetic_completions"] == 40
    assert json.loads(logs[1])["precheck"] == "forward-1002" and json.loads(logs[2])["precheck"] == "forward-1002ev"
    assert json.loads(logs[2])["post_complete_buy_rows_total"] == 40


def test_spread_seat_passes_and_outputs_are_aggregates_only(tmp_path):
    for i in range(40):
        _graduation(tmp_path, i, TS0 + (i % 2) * DAY + i * 60, f"BOT{i}")
    rc, doc, _ = _run(tmp_path)
    assert rc == 0 and doc["verdict"] == "PASS" and doc["top5_share"] == pytest.approx(5 / 40)
    assert doc["distinct_completers"] == 40 and len(doc["decidable_days"]) == 2
    text = (tmp_path / "out" / "t1_result.json").read_text()
    for leak in ("MINT", "CSIG", "MSIG", "BOT1", "999", "888"):  # no per-mint table, no completer id, no amount
        assert leak not in text
    assert doc["report_only"]["quote_in"] == "not measured under the D0 mask"
    assert doc["report_only"]["synthetic_share_over_organic_v0_band"] == 1.0


def test_too_few_synthetic_is_not_decidable(tmp_path):
    for i in range(40):
        _graduation(tmp_path, i, TS0 + i * 60, f"BOT{i % 5}", synthetic=i < 29)
    rc, doc, _ = _run(tmp_path)
    assert rc == 3 and doc["verdict"] == "NOT_DECIDABLE" and doc["synthetic_completions"] == 29


def test_no_post_complete_buy_rows_is_not_decidable(tmp_path):
    for i in range(40):
        _graduation(tmp_path, i, TS0 + i * 60, "BOT", synthetic=False)
    rc, doc, _ = _run(tmp_path)
    assert rc == 3 and "precheck" in doc["why"]


def test_day_over_one_percent_unclassified_leaves_the_pool(tmp_path):
    for i in range(40):
        _graduation(tmp_path, i, TS0 + i * 60, f"BOT{i}")
    for i in range(40, 80):  # day 2: every completion lacks its trade row -> unclassified -> day not decidable
        _graduation(tmp_path, i, TS0 + DAY + i * 60, "BOT0", trade_row=False)
    rc, doc, _ = _run(tmp_path)
    assert doc["not_decidable_days"] == ["2026-10-11"] and doc["synthetic_completions"] == 40 and doc["verdict"] == "PASS"
    assert doc["per_day"]["2026-10-11"]["unclassified"] == 40


def test_events_refused_by_the_old_guard_means_not_decidable(tmp_path, monkeypatch):
    monkeypatch.setitem(oc.ROLE_KINDS, oc.ROLE_OCT_D0, ("creates", "migrations", "trades"))
    for i in range(40):
        _graduation(tmp_path, i, TS0 + i * 60, f"BOT{i % 5}")
    rc, doc, logs = _run(tmp_path)
    assert rc == 3 and doc["verdict"] == "NOT_DECIDABLE"
    assert any(line.startswith("SKIP_REFUSED events/") for line in logs)


def test_walk2_hour_files_are_never_built(tmp_path):
    ts = int(datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc).timestamp())
    _write(tmp_path, "forward-1002ev", "events", ts, [{"type": "post_complete_buy", "mint": "X", "signature": "S", "slot": 1}])
    assert kt.hour_paths("forward-1002ev", "events", kt.START, "2026-10-16T03") == []
    assert kt.hours(kt.START, kt.END)[-1] == "2026-10-16T00"


def test_refusals_before_any_read(tmp_path):
    with pytest.raises(kt.Refused, match="declare-notebook"):
        kt.run(tmp_path / "out", _sha(tmp_path), " ", log=lambda s: None)
    with pytest.raises(kt.Refused, match="sha256"):
        kt.run(tmp_path / "out", "0" * 64, "n_test", log=lambda s: None)
    with pytest.raises(kt.Refused, match="forbidden"):
        kt.run(tmp_path / "probe-out", _sha(tmp_path), "n_test", log=lambda s: None)
    assert oc._LATCH == []  # nothing was granted, so nothing could be opened
    assert kt.main(["--out", str(tmp_path / "o"), "--prereg-sha256", "0" * 64, "--declare-notebook", "n"]) == 2


def test_columns_are_on_the_d0_allowlist():
    assert oc.check_columns(oc.ROLE_OCT_D0, kt.COLUMNS) == []
