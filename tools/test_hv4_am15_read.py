"""hv4_am15_read: FLOOR's gate read at the Amendment 12 instant B through the unchanged arena_am6_read / arena_score. Synthetic ledgers and hour tables only.
The fixture book has 100 opens + 5 data refusals on 5 dates (the unamended boundary, day 5) and 20 more opens on day 6 (the Am.15 boundary)."""
from __future__ import annotations

import collections
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from tools import arena_am6_exclusions as X
from tools import arena_am6_read as R
from tools import arena_score as S
from tools import hv4_am15_read as W
from tools import hv4_read_instant as F
from tools.arena_paper import TEST_ENV, Ledger
from tools.test_arena_score import DAY, DAY0, good_pnls, open_row, refused_row, register_bot, start_row, trade_row

os.environ[TEST_ENV] = "1"
H = 3_600_000
AS_OF = DAY0 + 30 * DAY
REPO = Path(__file__).resolve().parent.parent
B_OLD, B_NEW = DAY0 + 5 * DAY, DAY0 + 6 * DAY


@pytest.fixture(autouse=True)
def _disk(monkeypatch):
    monkeypatch.setattr(F, "CLOCK_MS", DAY0)  # the synthetic clock stands in for 1792886400000 (the real constants are asserted separately)
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def health(bad=None, days=10):
    bad = bad or {}
    lines = [X.canon({"hour": F.utc(DAY0 + i * H), "excluded": (DAY0 + i * H) in bad, "labels_failing": sorted(bad.get(DAY0 + i * H, []))}) for i in range(days * 24)]
    sha = hashlib.sha256("".join(ln + "\n" for ln in lines).encode()).hexdigest()
    return "\n".join(lines + [X.canon({"type": "sha256", "hours": len(lines), "sha256": sha})]) + "\n", sha


def shift(row, entry_ms, dur_ms=5000):
    d = dict(row["decision_ms"])
    d["entry"] = entry_ms
    d["exit"] = None if row["kind"] in ("open", "refused") else entry_ms + dur_ms
    return dict(row, decision_ms=d)


def book(n=126, per=20, refused_day1=5):
    rows = [start_row()]
    for i, p in enumerate(good_pnls(n)):
        pid, day = f"p{i:04d}", i // per
        ms = DAY0 + day * DAY + (1 + i % per) * H + 100 + i
        rows += [shift(open_row(pid, i, day), ms), shift(trade_row(pid, i, day, p), ms)]
    rows += [refused_row(i, 1) for i in range(refused_day1)]
    return [dict(r, bot_id="hv4-CON-F") for r in rows]


def setup(tmp_path, rows):
    reg = tmp_path / "bots.json"
    register_bot("hv4-CON-F", "abc1234", DAY0, reg, deciding_stake=0.25, model_ids=[], now_ms=0)
    led = Ledger(tmp_path / "ledger")
    for r in rows:
        led.append(r)
    return str(tmp_path / "ledger"), str(reg)


def instant(tmp_path, root, hl=None, restarts="", name="instant.json", as_of=AS_OF):
    h = hl or health()
    res = F.compute(root, "hv4-CON-F", DAY0, "screen", [h[0]], [h[1]], restarts, as_of)
    p = tmp_path / name
    F.write_once(p, res)
    return str(p), res


def wrun(tmp_path, root, reg, ip, hl=None, restarts="", **kw):
    h = hl or health()
    return W.run(ip, root, reg, "hv4-CON-F", [h[0]], [h[1]], AS_OF, restarts, **kw)


# ------------------------------------------------------------------------------------------------------------------------------
def test_unamended_path_reads_at_day5_and_am15_instant_is_day6(tmp_path):
    root, reg = setup(tmp_path, book())
    code, lines, res = R.run(root, reg, "hv4-CON-F", [health()[0]], AS_OF, "", [health()[1]])
    assert res["bots"]["hv4-CON-F"]["read_boundary_ms"] == B_OLD and res["bots"]["hv4-CON-F"]["attempts"] == 105
    _, ir = instant(tmp_path, root)
    assert ir["status"] == "READ_INSTANT" and ir["read_instant_ms"] == B_NEW and (ir["O"], ir["R"]) == (120, 5)


def test_wrapper_scores_at_b_and_matches_the_scorer_forced_to_b(tmp_path):
    root, reg = setup(tmp_path, book())
    ip, _ = instant(tmp_path, root)
    code, lines, res = wrun(tmp_path, root, reg, ip)
    assert code == 0
    mine = res["bots"]["hv4-CON-F"]
    assert mine["read_boundary_ms"] == B_NEW and mine["boundary_source"] == "natural" and mine["gate_read"] is True and mine["attempts"] == 125
    assert mine["am15"]["read_instant_ms"] == B_NEW and mine["am15"]["unamended_boundary_ms"] == B_OLD
    assert any(x.startswith("read_boundary_ms ") and x.endswith(str(B_NEW)) for x in lines)
    # independent check: the pinned scorer's own forced-boundary path at B (a comparator screen) gives the same books and the same status
    rows, _ = Ledger(root).scan_bot("hv4-CON-F")
    fake_recorded = {"bot_id": "hv4-CON-F", "status": "FAIL", "read_boundary_ms": B_OLD, "boundary_source": "natural"}
    forced = S.score_bot("hv4-CON-F", S.load_registry(reg), rows, AS_OF, None, S.SETTLE_MS, boundary_ms=B_NEW, comparator_read=fake_recorded)
    assert forced["read_boundary_ms"] == B_NEW and forced["books"] == mine["books"]
    assert forced["status"] == S.FORCED_PREFIX + mine["status"]


def test_patch_is_restored_and_unamended_behaviour_is_unchanged(tmp_path, monkeypatch):
    bri, rec = S.blind_read_instant, S.record_read
    root, reg = setup(tmp_path, book())
    ip, _ = instant(tmp_path, root)
    wrun(tmp_path, root, reg, ip)
    assert S.blind_read_instant is bri and S.record_read is rec
    seen = []

    def boom(*a, **k):
        seen.append((S.blind_read_instant is not bri, S.record_read is not rec, S.blind_read_instant([], DAY0), S.blind_read_instant([], DAY0 + DAY)))
        raise RuntimeError("inside the read")

    with monkeypatch.context() as m:
        m.setattr(R, "run", boom)
        with pytest.raises(RuntimeError):
            wrun(tmp_path, root, reg, ip)
    assert seen == [(True, True, B_NEW, None)]  # patched inside (other clocks get the original), restored after an error
    assert S.blind_read_instant is bri and S.record_read is rec
    _, _, res = R.run(root, reg, "hv4-CON-F", [health()[0]], AS_OF, "", [health()[1]])
    assert res["bots"]["hv4-CON-F"]["read_boundary_ms"] == B_OLD


def test_record_carries_the_am15_block_and_is_write_once(tmp_path):
    root, reg = setup(tmp_path, book())
    ip, _ = instant(tmp_path, root)
    rd = tmp_path / "reads"
    wrun(tmp_path, root, reg, ip, record=True, reads_dir=str(rd))
    rec = json.loads((rd / "hv4-CON-F.json").read_text())
    assert rec["read_boundary_ms"] == B_NEW and rec["boundary_source"] == "natural" and rec["am6"]["read_boundary_ms"] == B_NEW
    am15 = rec["am15"]
    assert am15["read_instant_ms"] == B_NEW and am15["unamended_boundary_ms"] == B_OLD
    assert am15["instant_file_sha256"] == hashlib.sha256(Path(ip).read_bytes()).hexdigest()
    assert am15["arena_am6_read_sha256"] == F.PINNED_AM6_READ_SHA256 and am15["arena_score_sha256"] in W.ARENA_SCORE_SHA256_KNOWN
    with pytest.raises(S.Refused, match="already recorded"):
        wrun(tmp_path, root, reg, ip, record=True, reads_dir=str(rd))


def test_excluded_hour_attempt_is_dropped_in_both_the_instant_and_the_read(tmp_path):
    rows = book()
    root, reg = setup(tmp_path, rows)
    bad = {DAY0 + 2 * DAY + 3 * H: ["decision-input"]}  # holds one open of day 2
    hl = health(bad)
    ip, ir = instant(tmp_path, root, hl=hl)
    assert ir["n_dropped_excluded_hour"] == 1 and ir["read_instant_ms"] == B_NEW and ir["O"] == 119
    code, lines, res = wrun(tmp_path, root, reg, ip, hl=hl)
    assert res["bots"]["hv4-CON-F"]["read_boundary_ms"] == B_NEW and res["bots"]["hv4-CON-F"]["attempts"] == 124
    tot = json.loads(next(x for x in lines if x.startswith("exclusions_totals ")).split(" ", 1)[1])
    assert tot["n_excl_10pct_class"] == 1


def test_refusals(tmp_path):
    root, reg = setup(tmp_path, book())
    ip, ir = instant(tmp_path, root)
    with pytest.raises(W.Refused, match="before the instant"):
        W.run(ip, root, reg, "hv4-CON-F", [health()[0]], [health()[1]], B_NEW - 1, "")
    # a file whose B does not follow from the inputs
    forged = dict(ir, read_instant_ms=B_NEW + DAY)
    fp = tmp_path / "forged.json"
    F.write_once(fp, forged)
    with pytest.raises(W.Refused, match="does not follow"):
        wrun(tmp_path, root, reg, str(fp))
    # the same file with an excluded hour the instant never saw
    with pytest.raises(W.Refused, match="does not follow"):
        wrun(tmp_path, root, reg, ip, hl=health({DAY0 + 5 * DAY + k * H: ["price-path"] for k in range(2, 18)}))  # 16 day-5 opens dropped
    # confirm-mode, other clock, other bot, not a record
    for k, v, m in (("mode", "confirm", "screen instant"), ("clock_ms", DAY0 + DAY, "registry has"), ("bot_id", "botB", "registry has"), ("schema", "x", "not a hv4_read_instant")):
        p = tmp_path / f"bad-{k}.json"
        F.write_once(p, dict(ir, **{k: v}))
        with pytest.raises(W.Refused, match=m):
            wrun(tmp_path, root, reg, str(p))


def test_pins_are_checked(tmp_path, monkeypatch):
    root, reg = setup(tmp_path, book())
    ip, _ = instant(tmp_path, root)
    monkeypatch.setattr(W, "ARENA_AM6_READ_SHA256", "0" * 64)
    with pytest.raises(W.Refused, match="arena_am6_read"):
        wrun(tmp_path, root, reg, ip)
    monkeypatch.setattr(W, "ARENA_AM6_READ_SHA256", F.PINNED_AM6_READ_SHA256)
    monkeypatch.setattr(W, "ARENA_SCORE_SHA256_KNOWN", ("0" * 64,))
    with pytest.raises(W.Refused, match="arena_score"):
        wrun(tmp_path, root, reg, ip)


def test_not_decidable_instant_never_calls_the_scorer(tmp_path, monkeypatch):
    root, reg = setup(tmp_path, book(n=60, per=12, refused_day1=0))
    ip, ir = instant(tmp_path, root)
    assert ir["status"] == "NOT_DECIDABLE"
    monkeypatch.setattr(S, "score_bot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("scorer called")))
    monkeypatch.setattr(R, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("am6 read called")))
    code, lines, res = wrun(tmp_path, root, reg, ip)
    assert code == 0 and res is None and lines[-1].startswith("NOT_DECIDABLE")


def test_cli_refuses_a_registry_clock_that_is_not_the_hv4_clock(tmp_path, monkeypatch):
    root, reg = setup(tmp_path, book())
    ip, _ = instant(tmp_path, root)
    hp, rp = tmp_path / "h", tmp_path / "r"
    hp.write_text(health()[0])
    rp.write_text("")
    args = ["--instant", ip, "--bot", "hv4-CON-F", "--ledger-root", root, "--registry", reg, "--health", str(hp), "--health-sha", health()[1], "--restarts", str(rp), "--as-of-ms", str(AS_OF)]
    monkeypatch.setattr(F, "CLOCK_MS", 1_792_886_400_000)  # the real HV4 clock: the synthetic registry line does not carry it
    assert W.main(args) == 2


def test_pinned_read_files_are_not_touched():
    diff = subprocess.run(["git", "diff", "--name-only", "origin/main", "--", "tools/arena_score.py", "tools/arena_am6_read.py", "tools/arena_am6_cut.py",
                           "tools/arena_am6_exclusions.py", "tools/arena_hour_health.py"], cwd=str(REPO), capture_output=True, text=True).stdout
    assert diff.strip() == ""
    assert hashlib.sha256((REPO / "tools" / "arena_am6_read.py").read_bytes()).hexdigest() == F.PINNED_AM6_READ_SHA256


def test_refuses_to_run_before_the_instant_file_exists(tmp_path, monkeypatch):
    root, reg = setup(tmp_path, book())
    monkeypatch.setattr(S, "score_bot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("scorer called")))
    monkeypatch.setattr(R, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("am6 read called")))
    with pytest.raises(W.Refused, match="unreadable"):
        wrun(tmp_path, root, reg, str(tmp_path / "no-such-instant.json"))
    hp, rp = tmp_path / "h", tmp_path / "r"
    hp.write_text(health()[0])
    rp.write_text("")
    assert W.main(["--instant", str(tmp_path / "no.json"), "--bot", "hv4-CON-F", "--ledger-root", root, "--registry", reg, "--health", str(hp), "--health-sha", health()[1],
                   "--restarts", str(rp), "--as-of-ms", str(AS_OF)]) == 2


def test_other_bot_and_real_constants_and_count_only_label(tmp_path):
    assert F.CLOCK_MS == 1_792_886_400_000 and F.CLOCK_MS + F.H_DAYS * DAY == 1_794_096_000_000 and len(F.BOT_IDS) == 9
    root, reg = setup(tmp_path, book())
    ip, _ = instant(tmp_path, root)
    with pytest.raises(W.Refused, match="nine HV4"):
        W.run(ip, root, reg, "botA", [health()[0]], [health()[1]], AS_OF, "")
    assert W.is_count_only({"fail_label": "FAIL (n < 100)"})
    assert W.is_count_only({"deciding": {"misses": {"a": ["n 99 < 100"], "b": ["days 4 < 5"]}}})
    assert not W.is_count_only({"deciding": {"misses": {"a": ["n 99 < 100", "CI90 lo -0.1 <= 0"]}}})
    assert not W.is_count_only({"deciding": {"misses": {"a": ["days positive 2/6 is not a majority"]}}})
    assert not W.is_count_only({"deciding": {"misses": {}}})


def test_hv4_read_book_is_not_a_gate_read_path(tmp_path, monkeypatch, capsys):
    from tools import hv4_read as HR
    monkeypatch.delenv("MAL_ARENA_TEST", raising=False)
    assert HR.__doc__.count("NOT A GATE-READ PATH") == 1
    called = []
    monkeypatch.setattr(HR, "read_book", lambda *a, **k: called.append(1))
    assert HR.main(["book", "--bot", "CON-L"]) == 3 and not called
    assert "not a gate-read path" in capsys.readouterr().err
