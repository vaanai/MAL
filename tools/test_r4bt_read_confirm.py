"""r4bt_read_confirm: the confirmation rule on top of the screen read wrapper. The wrapper's `read` is replaced by a canned result: no ledger, no tape, no decision log, no pricing."""
from __future__ import annotations

import hashlib
import json

import pytest

from tools import arena_h5_read as H5
from tools import r4bt_gate as G
from tools import r4bt_read as R
from tools import r4bt_read_confirm as CF

DAY = 86_400_000
CLOCK = 1_794_031_200_000
SCREEN_AT = 1_794_700_800_000  # a 00:00Z instant
BOUNDARY = SCREEN_AT + 7 * DAY


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rb(status="PASS", p=0.01):
    r = {"bot_id": G.BOT_ID, "status": status, "reasons": [], "read_boundary_ms": BOUNDARY, "boundary_source": "natural", "attempts": 120}
    if p is not None:
        r["r4bt"] = {"multiplicity": {"book_p": p}}
    return r


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Screen record (PASS), confirmation boundary file, registry stub, and a fake `read` that records its keyword arguments."""
    screen = tmp_path / "screen" / f"{G.BOT_ID}.json"
    screen.parent.mkdir()
    screen.write_text(json.dumps({"bot_id": G.BOT_ID, "status": "PASS", "read_boundary_ms": SCREEN_AT}))
    bnd = tmp_path / "boundary.json"
    bnd.write_text(json.dumps({"tool": "r4bt_attempts count", "arm": G.ARM, "clock_start_ms": CLOCK, "status": "boundary", "boundary_ms": BOUNDARY, "chain_verified": True,
                               "mode": "confirmation", "from_ms": SCREEN_AT, "attempts_before_boundary": 120}))
    monkeypatch.setattr(CF, "load_registry", lambda path: {G.BOT_ID: {"clock_start_ms": CLOCK}})
    calls = []
    answer = {"v": ([], rb())}

    def fake_read(**kw):
        calls.append(kw)
        return answer["v"]

    monkeypatch.setattr(R, "read", fake_read)
    kw = dict(screen_record=str(screen), screen_record_sha256=sha(screen), confirm_reads_dir=str(tmp_path / "confirm"), registry="reg.json", boundary_file=str(bnd),
              boundary_sha256=sha(bnd), i_am_the_read=True, c=1, ledger_root=str(tmp_path / "fresh-root"))
    return kw, calls, answer, tmp_path, screen, bnd


def test_pass_needs_the_gate_and_p_at_most_alpha_over_c_and_is_recorded_once(env):
    kw, calls, answer, tmp, *_ = env
    lines, res = CF.confirm(record=True, **kw)
    assert res["status"] == "PASS" and res["confirmation"]["book_p"] == 0.01 and res["confirmation"]["alpha_over_c"] == 0.05 and res["confirmation"]["c"] == 1
    assert res["confirmation"]["p_draws"] == H5.P_DRAWS == 10_000 and res["confirmation"]["p_seed"] == H5.P_SEED == 1
    rec = json.loads((tmp / "confirm" / f"{G.BOT_ID}.json").read_text())
    assert rec["status"] == "PASS" and rec["confirmation"]["screen_record_sha256"] == kw["screen_record_sha256"]
    assert any(ln.startswith("recorded: ") for ln in lines) and any("STATUS PASS" in ln for ln in lines)
    assert calls[0]["record"] is False and "reads_dir" not in calls[0]  # the screen's one read is never touched
    with pytest.raises(G.Refused, match="already recorded"):
        CF.confirm(record=True, **kw)


def test_the_bar_is_alpha_over_c_a_later_read_faces_a_smaller_one(env):
    kw, calls, answer, *_ = env
    answer["v"] = ([], rb(p=0.03))
    assert CF.confirm(**dict(kw, c=1))[1]["status"] == "PASS"
    r = CF.confirm(**dict(kw, c=2))[1]
    assert r["status"] == "FAIL" and r["confirmation"]["alpha_over_c"] == 0.025 and any("book p 0.03 > 0.05/c = 0.025 (c 2)" in x for x in r["reasons"])
    answer["v"] = ([], rb(p=0.05))
    assert CF.confirm(**dict(kw, c=1))[1]["status"] == "PASS"  # p == 0.05/c passes ("at most")


def test_gate_fail_missing_p_and_no_deciding_figure(env):
    kw, calls, answer, tmp, *_ = env
    answer["v"] = ([], rb("FAIL", p=0.0001))
    assert CF.confirm(**kw)[1]["status"] == "FAIL"  # a tiny p does not rescue a failed gate
    answer["v"] = ([], rb("PASS", p=None))
    r = CF.confirm(**kw)[1]
    assert r["status"] == "FAIL" and "no bootstrap p" in r["reasons"][0]
    answer["v"] = ([], None)  # the wrapper ended on an exclusions-tool cap
    r = CF.confirm(record=True, **kw)[1]
    assert r["status"] == "NOT_DECIDABLE" and r["confirmation"]["gate_status"] is None
    assert json.loads((tmp / "confirm" / f"{G.BOT_ID}.json").read_text())["status"] == "NOT_DECIDABLE"  # counted in c, final


def test_a_wrapper_not_decidable_stays_not_decidable(env):
    kw, calls, answer, *_ = env
    nd = {"bot_id": G.BOT_ID, "status": "NOT_DECIDABLE", "reasons": ["a3_halt_before_boundary"], "read_boundary_ms": BOUNDARY, "boundary_source": "natural", "attempts": None}
    answer["v"] = ([], nd)
    r = CF.confirm(**kw)[1]
    assert r["status"] == "NOT_DECIDABLE" and r["reasons"] == ["a3_halt_before_boundary"]


def test_only_a_pinned_screen_pass_of_this_bot_enters_confirmation(env):
    kw, calls, answer, tmp, screen, bnd = env
    with pytest.raises(G.Refused, match="differs from the pinned"):
        CF.confirm(**dict(kw, screen_record_sha256="0" * 64))
    for body, msg in (({"bot_id": G.BOT_ID, "status": "FAIL", "read_boundary_ms": SCREEN_AT}, "only a screen PASS"),
                      ({"bot_id": G.BOT_ID, "status": "NOT_DECIDABLE", "read_boundary_ms": SCREEN_AT}, "only a screen PASS"),
                      ({"bot_id": "other", "status": "PASS", "read_boundary_ms": SCREEN_AT}, "not r4bt-a1's"),
                      ({"bot_id": G.BOT_ID, "status": "PASS", "read_boundary_ms": SCREEN_AT + 5}, "no 00:00Z read_boundary_ms")):
        screen.write_text(json.dumps(body))
        with pytest.raises(G.Refused, match=msg):
            CF.confirm(**dict(kw, screen_record_sha256=sha(screen)))
    assert calls == []  # every refusal came before the read


def test_the_boundary_must_be_the_confirmation_count_from_the_screen_instant(env):
    kw, calls, answer, tmp, screen, bnd = env
    base = json.loads(bnd.read_text())
    for patch in ({"mode": "screen", "from_ms": None}, {"from_ms": SCREEN_AT + DAY}, {"from_ms": None}):
        bnd.write_text(json.dumps(dict(base, **patch)))
        with pytest.raises(G.Refused, match="not the confirmation count"):
            CF.confirm(**dict(kw, boundary_sha256=sha(bnd)))
    assert calls == []


def test_refusals_before_the_read_no_flag_no_c_same_directory(env):
    kw, calls, answer, tmp, screen, bnd = env
    with pytest.raises(G.Refused, match="i-am-the-read"):
        CF.confirm(**dict(kw, i_am_the_read=False))
    for bad in (None, 0, True):
        with pytest.raises(G.Refused, match="--c"):
            CF.confirm(**dict(kw, c=bad))
    with pytest.raises(G.Refused, match="screen read's directory"):
        CF.confirm(**dict(kw, confirm_reads_dir=str(screen.parent)))
    assert calls == []


def test_main_refuses_with_exit_2_and_prints_the_wrapper_lines(env, monkeypatch, capsys):
    kw, calls, answer, tmp, *_ = env
    h = tmp / "h.jsonl"
    h.write_text("")
    r = tmp / "r.txt"
    r.write_text("")
    argv = ["--i-am-the-read", "--ledger-root", kw["ledger_root"], "--registry", "reg.json", "--restarts", str(r), "--boundary-file", kw["boundary_file"],
            "--boundary-sha256", kw["boundary_sha256"], "--arena-paper-sha256", "a" * 64, "--arena-score-sha256", "b" * 64, "--health", str(h), "--health-sha", "c" * 64,
            "--as-of-ms", str(BOUNDARY), "--feed-delay-slots", "7", "--k-rounding", "ceil", "--n-books", "5", "--c", "1", "--screen-record", kw["screen_record"],
            "--screen-record-sha256", kw["screen_record_sha256"], "--confirm-reads-dir", kw["confirm_reads_dir"]]
    assert CF.main(argv, now_ms=BOUNDARY + 1) == 0
    out = capsys.readouterr().out
    assert "STATUS PASS" in out and calls[0]["n_books"] == 5 and calls[0]["c"] == 1 and calls[0]["now_ms"] == BOUNDARY + 1
    assert CF.main(argv[:-2] + ["--confirm-reads-dir", str(tmp / "screen")], now_ms=BOUNDARY + 1) == 2
    assert "REFUSED" in capsys.readouterr().err
