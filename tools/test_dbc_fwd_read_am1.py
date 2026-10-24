"""Tests for tools/dbc_fwd_read_am1.py (Amendment 1 read). SYNTHETIC fixtures only: a 168-hour synthetic window tape in a temp dir (the synthetic DBC pools of
tools/test_dbc_fwd.py shifted to 2026-10-25). No real tape, no fwdv1 dir, no network. Run: python3 -m tools.test_dbc_fwd_read_am1"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from tools import dbc_fwd as W
from tools import dbc_fwd_read as R
from tools import dbc_fwd_read_am1 as A
from tools import dbc_price as L
from tools import dbc_screen as S
from tools import test_dbc_fwd as T

LATE = datetime(2026, 11, 1, 0, 11, tzinfo=timezone.utc)
SHIFT_S = int((L.FWD_START - T.START).total_seconds())  # 11 days
SHIFT_SLOTS = int(SHIFT_S / 0.4)


def _rows():
    out = []
    for r in T.synth_rows():
        r = dict(r)
        r["slot"] += SHIFT_SLOTS
        r["t_recv_ms"] += SHIFT_S * 1000
        if "block_time" in r:
            r["block_time"] += SHIFT_S
        out.append(r)
    return out


def _root(age_s=3600, n_missing=0, rows=None):
    d = Path(tempfile.mkdtemp()) / "meteora_dbc"
    d.mkdir()
    rows = _rows() if rows is None else rows
    t0 = int(L.FWD_START.timestamp())
    for k, n in enumerate(R.expected_names()[n_missing:], start=n_missing):
        lo, hi = (t0 + 3600 * k) * 1000, (t0 + 3600 * (k + 1)) * 1000
        lines = [json.dumps({"v": 1, "venue": "meteora_dbc", "type": "backfill_hour", "hour": n[5:18]}, separators=(",", ":"))]
        lines += [json.dumps(r, separators=(",", ":")) for r in rows if lo <= r["t_recv_ms"] < hi]
        lines.append(json.dumps({"v": 1, "venue": "meteora_dbc", "type": "backfill_hour_end"}, separators=(",", ":")))
        subprocess.run(["zstd", "-q", "-o", str(d / n)], input=("\n".join(lines) + "\n").encode(), check=True)
    t = time.time() - age_s
    for p in list(d.iterdir()) + [d]:
        os.utime(p, (t, t))
    return str(d)


def _reset():
    L.FWD_ACTIVE = None
    L.SWAP3_IS_HOOK, L.FOB_EVIDENCE = True, False
    L.CFG_THR.clear(); L.CFG_XFER.clear(); L.CFG_UNMAPPED.clear()


def _base():
    p = Path(tempfile.mkdtemp()) / "base.json"
    p.write_text(json.dumps({"CfgOut1": {"thr": 85 * 10**9, "transfer_fee_bps": 0}, "CfgIn1": {"thr": 85 * 10**9, "transfer_fee_bps": 0}}))
    return str(p)


_ROOT: dict = {}


def root():
    if "r" not in _ROOT:
        _ROOT["r"] = _root()
    return _ROOT["r"]


def test_same_marker_and_window_as_the_registered_tool():
    assert A.MARKER == R.MARKER == "/data/mal/hunt-1008/push-1016/hunt29/dbc/HOLDOUT_READ_DONE_v3"
    assert (L.FWD_START, L.FWD_END) == (datetime(2026, 10, 25, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert L.FWD_ROOT == "/data2/mal/venue-tape-backfill/fwdv1/meteora_dbc" and R.MIN_READ_UTC == datetime(2026, 11, 1, 0, 10, tzinfo=timezone.utc)
    assert A.FIRST_OK == datetime(2026, 10, 25, 1, tzinfo=timezone.utc) and A.DEFAULT_OUT != R.DEFAULT_OUT


def test_refusals_before_the_claim_do_not_claim():
    out = Path(tempfile.mkdtemp()) / "o"
    m = str(Path(tempfile.mkdtemp()) / "mk")
    for now in (datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc), datetime(2026, 11, 1, 0, 9, 59, tzinfo=timezone.utc), datetime(2026, 10, 25, 1, tzinfo=timezone.utc)):
        assert A.run(out, root=root(), now=now, marker=m) == 6
    assert A.run(out, root=_root(n_missing=2), now=LATE, marker=m) == 5
    assert A.run(out, root=_root(age_s=30), now=LATE, marker=m) == 5

    def boom():
        raise ValueError("env file unreadable")
    assert A.run(out, root=root(), now=LATE, marker=m, make_fetch=boom) == 7
    full = Path(tempfile.mkdtemp())
    (full / "x").write_text("1")
    assert A.run(full, root=root(), now=LATE, marker=m) == 3
    assert not Path(m).exists() and not out.exists() and L.FWD_ACTIVE is None
    assert A.main(["--out", str(out)]) == 2


def test_marker_shared_both_ways():
    m = str(Path(tempfile.mkdtemp()) / "mk")
    assert R.claim_marker(m) is True
    assert A.run(Path(tempfile.mkdtemp()) / "o", root=root(), now=LATE, marker=m, fetch=lambda ks: {}) == 4 and L.FWD_ACTIVE is None


def test_end_to_end_synthetic_window():
    out = Path(tempfile.mkdtemp()) / "o"
    m = str(Path(tempfile.mkdtemp()) / "mk")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = A.run(out, root=root(), now=LATE, marker=m, fetch=lambda ks: {}, base_map=_base())
        o = buf.getvalue()
        assert rc == 0 and Path(m).exists()
        assert o.index("LOOK CONSUMED") < o.index("FORWARD WINDOW ENABLED")
        res = json.loads((out / A.OUT_JSON).read_text())
        assert (out / A.OUT_MD).exists() and (out / "cfg_map_final.json").exists()
        assert res["hours_present"] == 168 and res["bad_hours"] == [] and res["first_ok_bt_utc"] == "2026-10-25T01:00:00Z"
        dec = res["deciding"]
        # the deciding set is the causal engine's live-policy decisions on the same tape (independent run here)
        live: dict = {}
        eng = W.Engine(A.FIRST_OK.timestamp(), ss_live=lambda hk: S.slot_s_for(live, hk))
        W.stream_hours(eng, L.FWD_START, L.FWD_END, root(), live, None)
        assert dec["md5_keys"] == W.md5_keys(x["key"] for x in eng.decisions_live) and dec["n_decisions"] == len(eng.decisions_live) >= 3
        c = dec["primary_1p9s"]["counts"]
        assert c["completed_before_entry"] >= 1 and c["key_not_found_full_loss"] == 0 and dec["not_found_share"] == 0
        assert set(c) >= {"attempts", "bad_state_zeroed_priced", "bad_state_still_refused_full_loss", "no_state", "block_edge_excluded", "bad_hour_excluded"}
        assert dec["primary_1p9s"]["fixed_items"] == c["completed_before_entry"] + c["no_state"] + c["bad_state_still_refused_full_loss"] + c["other_refusal"]
        # AM3: R11 + R9 evaluated on the deciding set, both models; < 100 fills -> NOT_DECIDABLE (R1)
        assert set(res["checks"]) == {"filled_ge_100", "flat", "press"} and "entry_2p5s_mean_gt_0" in res["checks"]["flat"]
        assert res["verdict"] == "NOT_DECIDABLE" and "R1" in res["reason"]
        # AM4: report-only screen set with overlap counts
        sr = res["screen_report_only"]
        ov = sr["overlap"]
        assert sr["REPORT_ONLY"] is True and ov["both"] + ov["screen_only"] == ov["screen"] and ov["both"] + ov["causal_only"] == ov["deciding"]
        assert ov["causal_only"] >= 1 and sr["causal_only_means"]["flat"]["n"] >= 1
        # the second tool cannot read after this one
        assert R.run(Path(tempfile.mkdtemp()) / "o2", root=root(), now=LATE, marker=m, fetch=lambda ks: {}) == 4
    finally:
        _reset()


def test_screen_set_never_decides():
    """decide() with the screen's attempts replaced by nothing gives the same verdict, checks and deciding block (AM4: report-only)."""
    try:
        L.enable_swap3_pricing()
        L.enable_forward_window(root())
        D = S.load_exploration(root())
        a = A.decide({}, D, root())
        orig = S.cell_attempts
        S.cell_attempts = lambda cell, D, *x: ([], {k: 0 for k in a["screen_report_only"]["refused"]}, {"pools": 0, "trigger_prints": 0})
        try:
            b = A.decide({}, D, root())
        finally:
            S.cell_attempts = orig
        assert a["screen_report_only"]["attempts"] > 0 and b["screen_report_only"]["attempts"] == 0
        assert (a["verdict"], a.get("checks"), a["deciding"]) == (b["verdict"], b.get("checks"), b["deciding"])
    finally:
        _reset()


def test_not_found_over_1pct_is_not_decidable():
    try:
        L.enable_swap3_pricing()
        L.enable_forward_window(root())
        D = S.load_exploration(root())
        orig = A.deciding_set
        A.deciding_set = lambda r: ([("NoSuchPool|1|1.0", L.FWD_START.timestamp() + 7200)] + orig(r)[0], {})
        try:
            res = A.decide({}, D, root())
        finally:
            A.deciding_set = orig
        assert res["verdict"] == "NOT_DECIDABLE" and "AM2" in res["reason"] and res["deciding"]["primary_1p9s"]["counts"]["key_not_found_full_loss"] == 1
    finally:
        _reset()


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
