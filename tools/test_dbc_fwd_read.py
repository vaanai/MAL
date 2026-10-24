"""Tests for tools/dbc_fwd_read.py (registered forward read). Run: python3 -m tools.test_dbc_fwd_read"""
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from tools import dbc_fwd_read as R
from tools import dbc_price as L


def _root(n_missing=0, age_s=3600, content=None):
    d = Path(tempfile.mkdtemp())
    names = R.expected_names()
    assert len(names) == 168 and names[0] == "tape-2026-10-25T00.jsonl.zst" and names[-1] == "tape-2026-10-31T23.jsonl.zst"
    for n in names[n_missing:]:
        p = d / n
        if content is None:
            p.write_bytes(b"")
        else:
            h = n[5:18]
            raw = (json.dumps({"v": 1, "venue": "meteora_dbc", "type": "backfill_hour_end", "hour": h}) + "\n").encode()
            subprocess.run(["zstd", "-q", "-o", str(p)], input=raw, check=True)
    t = time.time() - age_s
    for p in list(d.iterdir()) + [d]:
        os.utime(p, (t, t))
    return d


def _reset():
    L.FWD_ACTIVE = None
    L.SWAP3_IS_HOOK, L.FOB_EVIDENCE = True, False
    L.CFG_THR.clear(); L.CFG_XFER.clear(); L.CFG_UNMAPPED.clear()


LATE = datetime(2026, 11, 1, 0, 11, tzinfo=timezone.utc)


def test_time_gate_refuses_before_0010_and_does_not_claim():
    out = Path(tempfile.mkdtemp()) / "o"
    m = str(Path(tempfile.mkdtemp()) / "mk")
    for now in (datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc), datetime(2026, 11, 1, 0, 9, 59, tzinfo=timezone.utc), datetime(2026, 10, 25, 1, tzinfo=timezone.utc)):
        assert R.run(out, root=str(_root()), now=now, marker=m) == 6
    assert not Path(m).exists() and not out.exists() and L.FWD_ACTIVE is None
    assert R.time_gate(datetime(2026, 11, 1, 0, 10, tzinfo=timezone.utc))[0]


def test_preflight_and_marker():
    out = Path(tempfile.mkdtemp()) / "o"
    m = str(Path(tempfile.mkdtemp()) / "mk")
    assert R.run(out, root=str(_root(n_missing=3)), now=LATE, marker=m) == 5 and not Path(m).exists()
    assert R.run(out, root=str(_root(age_s=60)), now=LATE, marker=m) == 5 and not Path(m).exists()
    assert R.claim_marker(m) is True and R.claim_marker(m) is False
    assert R.run(out, root=str(_root()), now=LATE, marker=m) == 4 and L.FWD_ACTIVE is None


def test_guard_accepts_only_window_hours():
    root = str(_root())
    try:
        L.enable_forward_window(root)
        assert L.check_hour_path(f"{root}/tape-2026-10-25T00.jsonl.zst", root) == L.FWD_START
        assert L.check_hour_path(f"{root}/tape-2026-10-31T23.jsonl.zst", root)
        for bad in ("tape-2026-10-24T23", "tape-2026-11-01T00", "tape-2026-10-16T23", "tape-2026-10-17T00"):
            try:
                L.check_hour_path(f"{root}/{bad}.jsonl.zst", root)
                raise AssertionError("not refused: " + bad)
            except L.Refused:
                pass
        try:
            L.check_hour_path("/data2/mal/venue-tape-backfill/meteora_dbc/tape-2026-10-25T00.jsonl.zst")
            raise AssertionError("other root accepted")
        except L.Refused:
            pass
    finally:
        _reset()
    assert L.window()[1] == L.EXPLORE_END  # default window unchanged


def test_empty_window_is_not_decidable_end_to_end():
    out = Path(tempfile.mkdtemp()) / "o"
    m = str(Path(tempfile.mkdtemp()) / "mk")
    base = Path(tempfile.mkdtemp()) / "base.json"
    base.write_text("{}")
    try:
        rc = R.run(out, root=str(_root(content=True)), now=LATE, marker=m, fetch=lambda ks: {}, base_map=str(base))
    finally:
        _reset()
    assert rc == 0 and Path(m).exists()
    res = json.loads((out / "fwd-m10.json").read_text())
    assert res["verdict"] == "NOT_DECIDABLE" and (out / "cfg_map_final.json").exists()


def test_resolve_configs_fetches_missing_and_counts_unmapped():
    import base64
    from tools import dbc_config as C
    fx = json.loads((Path(__file__).parent / "data" / "dbc_swap3_fixture.json").read_text())
    good_k, good_v = next(iter(fx["configs"].items()))
    counts = {"A" * 40: 10, good_k: 60, "B" * 40: 30}
    seen = []

    def fetch(keys):
        seen.append(list(keys))
        return {good_k: {"data_b64": good_v}} if good_k in keys else {}

    final, rep = R.resolve_configs(counts, {"A" * 40: {"thr": 1, "transfer_fee_bps": 0}}, fetch)
    assert seen == [sorted([good_k, "B" * 40])] and good_k in final and rep["unmapped"] == ["B" * 40]
    assert abs(rep["unmapped_row_share"] - 0.30) < 1e-12 and rep["fetched_new"] == 1


def test_verdict_r11_and_2p5():
    def side(mean=0.01, days=5, pos=3, ci=0.001, ex3=1.0, exday=1.0):
        return {"n": 120, "mean": mean, "days": days, "days_pos": pos, "ci90_lo_trade": ci, "ex_top3": ex3, "ex_best_day": exday}

    good = {"filled": 120, "flat": side(), "press": side()}
    g25 = {"flat": side(), "press": side()}
    assert R.verdict(good, g25)["verdict"] == "PASS"
    assert R.verdict(good, None)["verdict"] == "FAIL"
    assert R.verdict(good, {"flat": side(), "press": side(mean=0.0)})["verdict"] == "FAIL"
    assert R.verdict({**good, "filled": 99}, g25)["verdict"] == "FAIL"
    assert R.verdict({**good, "flat": side(ci=0.0)}, g25)["verdict"] == "FAIL"
    assert R.verdict({**good, "press": side(days=6, pos=3)}, g25)["verdict"] == "FAIL"
    assert R.verdict({**good, "flat": side(exday=-0.1)}, g25)["verdict"] == "FAIL"
    assert R.verdict({**good, "flat": side(ex3=0.0)}, g25)["verdict"] == "FAIL"


def test_out_exists_refuses():
    out = Path(tempfile.mkdtemp())
    (out / "x").write_text("1")
    assert R.run(out, root=str(_root()), now=LATE, marker=str(out.parent / "mk2")) == 3


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
