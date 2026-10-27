"""HV8 C3 tests: tools/hv8_labels.py. SYNTHETIC tape in a temp root only: no real hour, no API call, no key. Nothing here says HV8 makes money."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from tools import hv8_calib as CAL
from tools import hv8_dump as D
from tools import hv8_labels as L
from tools.test_hv4_runner import write_tape
from tools.test_hv7_runner import HOUR, T0, _env, tape_rows  # noqa: F401 - _env is an autouse fixture

WIN = D.Window(block_start_ms=T0 - 3 * HOUR, first_h_ms=T0 + HOUR, last_h_excl_ms=T0 + 4 * HOUR, end_ms=T0 + 5 * HOUR)


def trade_row(ret_lam, exit_ms, stake=0.25, pool="P", t_e=1, kind="trade", **kw):
    legs = {"total": {"flat_1p9": {"state": "filled", "net_lamports": ret_lam}, "press_1p9": {"state": "filled", "net_lamports": ret_lam * 2}}}
    return {"kind": kind, "stake_sol": stake, "pool": pool, "stamp_ms": {"entry": t_e}, "decision_ms": {"entry": t_e, "exit": exit_ms}, "legs": legs, **kw}


# ---------------------------------------------------------------- pure arithmetic
def test_classify_return_and_stake_filter():
    r = L.classify([trade_row(25_000_000, 1000, stake=0.5), trade_row(-50_000_000, 1000)], end_ms=10**9)
    assert r == {"ret": -0.2, "ret_press": -0.4, "censored": None}
    assert L.classify([trade_row(1, 1, stake=1.0)], 10**9)["censored"] == "no_row"
    assert L.classify([], 10**9)["censored"] == "no_row"


def test_exit_at_or_after_the_tape_end_is_censored_and_before_it_is_not():
    end = 1_000_000
    assert L.classify([trade_row(1, end - 1900 - 1)], end)["censored"] is None
    assert L.classify([trade_row(1, end - 1900)], end)["censored"] == "exit_at_or_after_tape_end"
    assert L.classify([trade_row(1, end)], end)["censored"] == "exit_at_or_after_tape_end"
    assert L.classify([trade_row(1, None)], end)["censored"] == "exit_at_or_after_tape_end"


def test_excluded_and_refused_rows_are_censored_with_a_reason():
    assert L.classify([trade_row(1, 1, kind="excluded", exclusion_reason="gap")], 10**9)["censored"] == "excluded:gap"
    assert L.classify([trade_row(1, 1, kind="refused", reason="no_prints")], 10**9)["censored"] == "refused:no_prints"


def test_median_split_per_hour_ties_and_censored_dropped_and_counted():
    items = [("h1", "a", 0.1), ("h1", "b", 0.2), ("h1", "c", 0.3), ("h1", "d", None),
             ("h2", "e", -0.1), ("h2", "f", -0.1), ("h2", "g", 0.4), ("h2", "h", 0.4)]
    m = L.median_labels(items)
    assert [m[k]["y"] for k in "abc"] == [0, None, 1] and m["b"]["status"] == "tied" and m["d"]["status"] == "censored"
    assert [m[k]["y"] for k in "efgh"] == [0, 0, 1, 1]                 # median 0.15: no ties
    # the hour's own level cancels: shifting a whole hour changes no label
    shifted = L.median_labels([(t, s, None if v is None else v + 5.0) for t, s, v in items if t == "h1"])
    assert [shifted[k]["y"] for k in "abc"] == [0, None, 1]


def test_median_split_agrees_with_the_calibrator_label_rows():
    rows = [{"card_sha": c, "t_d": "2026-08-21T12:02:00Z", "ratings": [1, 1, 1]} for c in "abcd"] + \
           [{"card_sha": c, "t_d": "2026-08-21T13:02:00Z", "ratings": [1, 1, 1]} for c in "efg"]
    rets = {"a": 0.1, "b": 0.3, "c": 0.2, "d": None, "e": 0.0, "f": 0.0, "g": 0.5}
    _r, _rets, ys, cnt = CAL.label_rows(rows, rets)
    mine = L.median_labels([(r["t_d"], r["card_sha"], rets[r["card_sha"]]) for r in rows])
    assert ys == [mine[r["card_sha"]]["y"] for r in rows]
    assert cnt["censored"] == sum(v["status"] == "censored" for v in mine.values())


# ---------------------------------------------------------------- the pin gate
def fixture(tmp_path):
    """A synthetic dump dir, ratings dir (with manifest) and pin file."""
    tape = tmp_path / "tape"
    write_tape(tape, tape_rows())
    res = D.collect(tape, tmp_path / "scratch-dump", WIN)
    dump = tmp_path / "dump"
    D.write_dump(res, dump, head="a" * 40, win=WIN, out_root=None)
    rd = tmp_path / "ratings"
    rd.mkdir()
    (rd / "manifest.json").write_text(json.dumps({"dump_manifest_sha256": hashlib.sha256((dump / "manifest.json").read_bytes()).hexdigest()}))
    sha = hashlib.sha256((rd / "manifest.json").read_bytes()).hexdigest()
    pin = tmp_path / "RATINGS-PIN.md"
    pin.write_text(f"manifest_sha256: {sha}\n")
    return tape, dump, rd, sha, pin


def lock(rd):
    for p in rd.iterdir():
        p.chmod(0o444)
    rd.chmod(0o555)


def unlock(rd):
    rd.chmod(0o755)


def test_no_pin_file_means_no_labels_and_the_tape_is_never_opened(tmp_path):
    tape, dump, rd, sha, pin = fixture(tmp_path)
    lock(rd)
    try:
        pin.unlink()
        with pytest.raises(CAL.CalibError, match="does not exist"):
            L.run_labels(dump, rd, sha, tmp_path / "no-such-tape", tmp_path / "sc", tmp_path / "out", WIN, pin_path=pin)
    finally:
        unlock(rd)
    assert not (tmp_path / "out").exists() and not (tmp_path / "sc").exists()


def test_wrong_pin_wrong_argument_writable_dir_and_wrong_dump_are_refused(tmp_path):
    tape, dump, rd, sha, pin = fixture(tmp_path)
    args = (dump, rd, sha, tmp_path / "no-such-tape", tmp_path / "sc", tmp_path / "out", WIN)
    with pytest.raises(L.Refused, match="not_read_only"):
        L.run_labels(*args, pin_path=pin)                                 # still writable
    lock(rd)
    try:
        with pytest.raises(L.Refused, match="argument_differs"):
            L.run_labels(dump, rd, "0" * 64, *args[3:], pin_path=pin)
        pin.write_text("manifest_sha256: " + "1" * 64 + "\n")
        with pytest.raises(L.Refused, match="argument_differs"):
            L.run_labels(*args, pin_path=pin)
        pin.write_text("manifest_sha256: " + "1" * 64 + "\n")
        with pytest.raises(L.Refused, match="differs_from_the_pin"):
            L.run_labels(dump, rd, "1" * 64, *args[3:], pin_path=pin)     # arg == pin but the manifest does not hash to it
        pin.write_text(f"manifest_sha256: {sha}\n")
        (dump / "manifest.json").write_text("{}")
        with pytest.raises(L.Refused, match="dump_manifest_differs"):
            L.run_labels(*args, pin_path=pin)
        pin.write_text("junk")
        with pytest.raises(CAL.CalibError):
            L.run_labels(*args, pin_path=pin)
    finally:
        unlock(rd)


def test_main_returns_2_without_a_pin(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(CAL, "PIN_PATH", tmp_path / "missing-pin.md")
    assert L.main(["--dump", str(tmp_path), "--ratings", str(tmp_path), "--manifest-sha", "0" * 64, "--tape", str(tmp_path), "--scratch", str(tmp_path / "s"),
                   "--out", str(tmp_path / "o")]) == 2


# ---------------------------------------------------------------- full pricing on synthetic tape
def test_pipeline_prices_every_dumped_card_and_counts_the_censored(tmp_path):
    tape, dump, rd, sha, pin = fixture(tmp_path)
    lock(rd)
    try:
        res = L.run_labels(dump, rd, sha, tape, tmp_path / "sc", tmp_path / "out", WIN, pin_path=pin)
    finally:
        unlock(rd)
    man = json.loads((tmp_path / "out" / "labels-manifest.json").read_text())
    n = json.loads((dump / "manifest.json").read_text())["cards"]
    assert res["cards"] == n == man["cards"] and man["kept"] + man["tied"] + man["censored"] == n
    assert man["api_calls"] == 0 and man["tape_end_excl"] == D.iso_s(WIN.end_ms)
    rows = [json.loads(x) for x in (tmp_path / "out" / "labels.jsonl").read_text().splitlines()]
    assert len(rows) == n and sum(1 for r in rows if r["censored"]) == man["censored"]
    assert sum(man["censored_reasons"].values()) == man["censored"]
    assert man["kept"] > 0 and all(r["y"] in (0, 1) for r in rows if r["ret"] is not None and r["y"] is not None)
    # the labels file feeds the calibrator's own loader
    labels = CAL.load_labels(tmp_path / "out" / "labels.jsonl", rd / "manifest.json", pin)
    assert len(labels) == n


def test_labels_do_not_look_past_the_exit_horizon(tmp_path):
    """No-lookahead for outcomes: the first set's labels from the full tape equal those from a tape cut 70 min after its entry."""
    tape, dump, rd, sha, pin = fixture(tmp_path)
    first_td = WIN.grid()[0]
    cut_ms = first_td + 45_000 + 70 * 60_000
    cut = tmp_path / "tape-cut"
    write_tape(cut, [r for r in tape_rows() if r["t_recv_ms"] < cut_ms])
    cards = [json.loads(x) for x in (dump / "cards.jsonl").read_text().splitlines()]
    one = [c for c in cards if c["t_d"] == D.iso_s(first_td)]
    outs = []
    for tag, tp in (("full", tape), ("cut", cut)):
        win = WIN if tag == "full" else D.Window(WIN.block_start_ms, WIN.first_h_ms, WIN.first_h_ms + HOUR, cut_ms)
        r = L.price(tp, tmp_path / f"sc-{tag}", [c for c in one], win, t_ds=[first_td])
        outs.append({k: v for k, v in r["returns"].items()})
    assert outs[0] == outs[1] and any(v["ret"] is not None for v in outs[0].values())


def test_replay_that_differs_from_the_dump_is_refused(tmp_path):
    tape, dump, rd, sha, pin = fixture(tmp_path)
    cards = [json.loads(x) for x in (dump / "cards.jsonl").read_text().splitlines()]
    cards[0]["card_sha"] = "f" * 64
    with pytest.raises(L.Refused, match="differ_from_the_dump"):
        L.price(tape, tmp_path / "sc", cards, WIN)
