"""hv4_read_instant (DEC-031 Am.12 items 1, 3, 6): the attempt-margin read instant of hv4-CON-F. Synthetic ledgers and hour tables only.
Covers the margin arithmetic and the 100-106 boundary cases with refusals, the O-only date rule, the Am.6 excluded-hour drop (table and restart gaps), the
horizon, the confirmation window, the write-once record, and parity with the pinned arena_score.load_decisions (a508ed79, read from git at 9ed4c5ff) and with
arena_am6_exclusions.classify's class S_10."""
from __future__ import annotations

import ast
import bisect
import hashlib
import json
import os
import random
import stat
import subprocess
import sys
from fractions import Fraction
from math import floor
from pathlib import Path
from typing import Sequence

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tools import arena_am6_exclusions as X  # noqa: E402
from tools import hv4_read_instant as F  # noqa: E402

DAY, HOUR = F.DAY_MS, F.HOUR_MS
CLOCK = F.CLOCK_MS
END_H = CLOCK + F.H_DAYS * DAY
BOT = F.BOT_IDS[1]


# ------------------------------------------------------------------------------------------------------------------------------ fixtures
def at(day: int, hour: int = 1, k: int = 0) -> int:
    """An instant on day `day` after the clock, hour `hour`, plus k seconds."""
    return CLOCK + day * DAY + hour * HOUR + k * 1000


def health_text(hours_from: int, hours_to: int, excluded: dict[int, list[str]] | None = None, skip: Sequence[int] = ()) -> tuple[str, str]:
    """An arena_hour_health-format table over [hours_from, hours_to) (epoch ms, whole hours); excluded: {hour_ms: labels}. Returns (text, sha256)."""
    excluded = excluded or {}
    lines = []
    h = hours_from
    while h < hours_to:
        if h not in skip:
            lb = excluded.get(h)
            lines.append(X.canon({"hour": F.utc(h), "excluded": lb is not None, "labels_failing": sorted(lb or [])}))
        h += HOUR
    sha = hashlib.sha256("".join(ln + "\n" for ln in lines).encode()).hexdigest()
    return "\n".join(lines + [X.canon({"type": "sha256", "hours": len(lines), "sha256": sha})]) + "\n", sha


FULL_HEALTH = health_text(CLOCK - DAY, END_H + DAY)


def row(kind: str, entry: int, i: int, **extra) -> str:
    """A ledger line with "kind" first, as the arena writes it (arena_score.load_decisions matches the line prefix)."""
    r = {"kind": kind, "bot_id": BOT, "position_id": f"p{i}", "decision_ms": {"entry": entry, "stamp": entry - 5}}
    r.update(extra)
    return json.dumps(r, separators=(",", ":"))


def write_ledger(root: Path, opens: Sequence[int], refused: Sequence[int] = (), noise: bool = True, bot: str = BOT) -> Path:
    d = root / bot
    d.mkdir(parents=True, exist_ok=True)
    by_file: dict[str, list[str]] = {}
    for i, e in enumerate(opens):
        by_file.setdefault(F.utc_date(e), []).append(row("open", e, i))
        if noise:  # a close of the same attempt, with outcome fields that must never be read
            by_file[F.utc_date(e)].append(row("trade", e, i, legs={"1p9": {"pnl_sol": 0.5}}, decision_ms={"entry": e, "exit": e + 3600_000}))
    for j, e in enumerate(refused):
        by_file.setdefault(F.utc_date(e), []).append(row("refused", e, 10_000 + j, reason="no_prints"))
    for day, lines in by_file.items():
        (d / f"trades-{day}.jsonl").write_text("\n".join(lines) + "\n")
    if noise:
        (d / "trades-zz-noise.jsonl").write_text('{"kind":"start","decision_ms":{"entry":1}}\n{"kind":"trade", TORN\n{"kind":"excluded"\n')
    return root


def spread(n: int, days: Sequence[int], hour: int = 1) -> list[int]:
    """n instants spread round-robin over the given days, distinct seconds."""
    return [at(days[i % len(days)], hour, i) for i in range(n)]


def run(root: Path, start: int = CLOCK, as_of: int = END_H, health=FULL_HEALTH, restarts: str = "", mode: str = "screen", screen_b: int | None = None) -> dict:
    return F.compute(str(root), BOT, CLOCK, mode, [health[0]], [health[1]], restarts, as_of, screen_b)


# ------------------------------------------------------------------------------------------------------------------------------ the pinned scorer's functions
def pinned_arena_score():
    """load_decisions, blind_read_instant and their constants, extracted from tools/arena_score.py at 9ed4c5ff (sha256 a508ed79) and executed alone."""
    try:
        blob = subprocess.run(["git", "-C", str(REPO), "show", "9ed4c5ff:tools/arena_score.py"], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git history with 9ed4c5ff not available")
    assert hashlib.sha256(blob).hexdigest() == F.PINNED_ARENA_SCORE_SHA256
    tree = ast.parse(blob)
    want_fn, want_const = {"load_decisions", "blind_read_instant"}, {"DAY_MS", "MIN_N", "MIN_DAYS"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in want_fn:
            body.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "Refused":
            body.append(node)
        elif isinstance(node, ast.Assign):
            names = {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)}
            if names & want_const:
                body.append(node)
    ns = {"json": json, "Path": Path, "bisect": bisect, "Sequence": Sequence, "__name__": "arena_score_a508ed79"}
    exec(compile(ast.Module(body=body, type_ignores=[]), "arena_score@a508ed79", "exec"), ns)
    assert ns["MIN_N"] == 100 and ns["MIN_DAYS"] == 5 and ns["DAY_MS"] == DAY
    return ns


# ------------------------------------------------------------------------------------------------------------------------------ margin
@pytest.mark.parametrize("o,r,ok", [
    (100, 0, False), (104, 0, False), (105, 0, True), (106, 0, True),
    (100, 6, False), (104, 1, False), (105, 1, True), (105, 14, True), (105, 15, False), (106, 15, True),
    (103, 3, False), (102, 4, False), (101, 5, False), (106, 14, True), (119, 400, False), (119, 260, True), (120, 0, True),
])
def test_margin_table(o, r, ok):
    assert (F.margin(o, r) >= 100) is ok


def test_margin_is_exact_floor():
    for o in range(0, 400):
        for r in range(0, 60):
            assert F.margin(o, r) == o - floor(Fraction(5, 100) * (o + r))


# ------------------------------------------------------------------------------------------------------------------------------ boundary cases 100-106
@pytest.mark.parametrize("o,r", [(100, 0), (101, 0), (104, 0), (105, 0), (106, 0), (100, 6), (104, 2), (105, 1), (105, 14), (105, 15), (106, 15)])
def test_boundary_cases_100_to_106(tmp_path, o, r):
    """Attempts on days 0..4; the first boundary is day 5 iff the margin holds there, else the next day's 6 opens move it to day 6."""
    opens = spread(o, [0, 1, 2, 3, 4])
    refused = spread(r, [0, 2, 4], hour=2)
    write_ledger(tmp_path, opens + spread(6, [5], hour=3), refused)
    ok = F.margin(o, r) >= 100
    res = run(tmp_path, as_of=CLOCK + 5 * DAY)
    assert res["status"] == ("READ_INSTANT" if ok else "NOT_READY")
    assert (res["O"], res["R"], res["o_dates"]) == (o, r, 5)
    if ok:
        assert res["read_instant_ms"] == CLOCK + 5 * DAY
    res6 = run(tmp_path, as_of=CLOCK + 6 * DAY)
    assert res6["status"] == "READ_INSTANT"
    assert res6["read_instant_ms"] == CLOCK + (5 if ok else 6) * DAY


def test_hundred_attempts_unamended_rule_would_read_here(tmp_path):
    """100 opens + 5 refused on 5 dates: the pinned rule reads at day 5 (105 attempts), the margin rule does not (100 - 5 = 95)."""
    opens, refused = spread(100, [0, 1, 2, 3, 4]), spread(5, [1], hour=2)
    write_ledger(tmp_path, opens, refused)
    P = pinned_arena_score()
    assert P["blind_read_instant"](P["load_decisions"](tmp_path, BOT), CLOCK) == CLOCK + 5 * DAY
    res = run(tmp_path)
    assert res["status"] == "NOT_DECIDABLE" and res["O"] == 100 and res["R"] == 5 and res["margin"] == 95


# ------------------------------------------------------------------------------------------------------------------------------ dates on O only
def test_dates_count_open_rows_only(tmp_path):
    opens = spread(130, [0, 1, 2, 3])
    refused = spread(5, [4], hour=2)
    write_ledger(tmp_path, opens, refused)
    res = run(tmp_path, as_of=CLOCK + 6 * DAY)
    assert res["status"] == "NOT_READY" and res["o_dates"] == 4 and res["margin"] >= 100
    write_ledger(tmp_path / "b", opens + [at(5, 7)], refused)
    res = run(tmp_path / "b", as_of=CLOCK + 6 * DAY)
    assert res["status"] == "READ_INSTANT" and res["read_instant_ms"] == CLOCK + 6 * DAY and res["o_dates"] == 5


def test_attempts_before_clock_ignored(tmp_path):
    write_ledger(tmp_path, spread(50, [-1]) + spread(104, [0, 1, 2, 3, 4]))
    res = run(tmp_path, as_of=CLOCK + 5 * DAY)
    assert res["status"] == "NOT_READY" and res["O"] == 104


# ------------------------------------------------------------------------------------------------------------------------------ Amendment 6 drop
def test_excluded_hour_drop(tmp_path):
    """105 opens; 1 in an excluded hour. Dropped: O = 104, margin 99, no B at day 5. Same hour healthy: B at day 5."""
    opens = spread(104, [0, 1, 2, 3, 4]) + [at(2, 9)]
    write_ledger(tmp_path, opens)
    h9 = at(2, 9) // HOUR * HOUR
    ex = health_text(CLOCK - DAY, END_H + DAY, {h9: ["decision-input"]})
    res = run(tmp_path, as_of=CLOCK + 5 * DAY, health=ex)
    assert res["status"] == "NOT_READY" and res["O"] == 104 and res["n_dropped_excluded_hour"] == 1
    res = run(tmp_path, as_of=CLOCK + 5 * DAY)
    assert res["status"] == "READ_INSTANT" and res["O"] == 105 and res["n_dropped_excluded_hour"] == 0


def test_dropped_refusals_leave_the_denominator(tmp_path):
    """105 opens + 20 refused (margin 99). The 20 refusals are all in a price-path excluded hour: dropped, so O 105, R 0, margin 100."""
    opens = spread(105, [0, 1, 2, 3, 4])
    refused = [at(3, 11, k) for k in range(20)]
    write_ledger(tmp_path, opens, refused)
    assert run(tmp_path, as_of=CLOCK + 5 * DAY)["status"] == "NOT_READY"
    ex = health_text(CLOCK - DAY, END_H + DAY, {at(3, 11) // HOUR * HOUR: ["price-path"]})
    res = run(tmp_path, as_of=CLOCK + 5 * DAY, health=ex)
    assert res["status"] == "READ_INSTANT" and (res["O"], res["R"], res["n_dropped_excluded_hour"]) == (105, 0, 20)


def test_restart_gap_hours_drop(tmp_path):
    """A restart gap [h:30, h+1:10] is rounded outward to hours h and h+1 (decision-input), as arena_am6_exclusions.classify does."""
    opens = spread(104, [0, 1, 2, 3, 4]) + [at(1, 5, 1), at(1, 6, 2)]
    write_ledger(tmp_path, opens)
    gap = json.dumps([at(1, 5) + 30 * 60_000, at(1, 6) + 10 * 60_000]) + "\n"
    res = run(tmp_path, as_of=CLOCK + 5 * DAY, restarts=gap)
    assert res["status"] == "NOT_READY" and res["O"] == 104 and res["n_dropped_excluded_hour"] == 2
    gap_end_on_boundary = json.dumps([at(1, 5) + 30 * 60_000, at(1, 6)]) + "\n"  # end exactly on 06:00 does not take hour 06
    res = run(tmp_path, as_of=CLOCK + 5 * DAY, restarts=gap_end_on_boundary)
    assert res["status"] == "READ_INSTANT" and res["O"] == 105


def test_unmeasurable_hour_refuses_only_when_needed(tmp_path):
    opens = spread(105, [0, 1, 2, 3, 4]) + [at(7, 3)]
    write_ledger(tmp_path, opens)
    hole = at(7, 3) // HOUR * HOUR
    res = run(tmp_path, as_of=END_H, health=health_text(CLOCK - DAY, END_H + DAY, skip=[hole]))
    assert res["status"] == "READ_INSTANT" and res["read_instant_ms"] == CLOCK + 5 * DAY  # the hole is after B: never looked at
    hole2 = at(1, 1) // HOUR * HOUR
    with pytest.raises(F.Refused, match="unmeasurable"):
        run(tmp_path, as_of=END_H, health=health_text(CLOCK - DAY, END_H + DAY, skip=[hole2]))


def test_drop_parity_with_am6_exclusions_classify():
    rng = random.Random(7)
    hours = [CLOCK + i * HOUR for i in range(24 * 12)]
    excl = {h: rng.choice([["price-path"], ["decision-input"], ["price-path", "decision-input"]]) for h in rng.sample(hours, 30)}
    text, _ = health_text(CLOCK, END_H, excl)
    table, _ = X.load_health(text)
    restarts = [(CLOCK + 50 * HOUR + 1234, CLOCK + 52 * HOUR), (CLOCK + 100 * HOUR + 59 * 60_000, CLOCK + 100 * HOUR + 59 * 60_000 + 5), (CLOCK + 200 * HOUR, CLOCK + 200 * HOUR)]
    rtext = "".join(json.dumps(list(r)) + "\n" for r in restarts)
    dropper = F.hour_dropper(table, X.load_restarts(rtext))
    atts = [{"attempt_id": f"a{i}", "decided_ms": rng.randrange(CLOCK, END_H - DAY), "exit_ms": None, "open_at_restart": False, "s14_excluded": False}
            for i in range(3000)]
    for a in atts:
        a["exit_ms"] = a["decided_ms"]  # refused-like: no span, so only class 1 can drop it; class 1 is checked first anyway
    out = X.classify(atts, table, X.load_restarts(rtext), END_H)
    by_id = {r["attempt_id"]: r["status"] for r in out}
    n10 = 0
    for a in atts:
        s10 = by_id[a["attempt_id"]] == X.S_10
        n10 += s10
        assert dropper(a["decided_ms"]) is s10
    assert 100 < n10 < 3000


# ------------------------------------------------------------------------------------------------------------------------------ horizon
def test_b_equal_to_end_h_is_a_read_instant(tmp_path):
    opens = spread(60, [0, 1, 2, 3, 4]) + spread(45, [13], hour=4)
    write_ledger(tmp_path, opens)
    assert run(tmp_path, as_of=END_H - 1)["status"] == "NOT_READY"
    res = run(tmp_path, as_of=END_H)
    assert res["status"] == "READ_INSTANT" and res["read_instant_ms"] == END_H


def test_attempts_at_or_after_end_h_do_not_count(tmp_path):
    opens = spread(60, [0, 1, 2, 3, 4]) + spread(44, [13], hour=4) + [END_H] + spread(40, [14, 15])
    write_ledger(tmp_path, opens)
    res = run(tmp_path, as_of=END_H + 3 * DAY, health=health_text(CLOCK - DAY, END_H + 5 * DAY))
    assert res["status"] == "NOT_DECIDABLE" and res["O"] == 104 and res["read_instant_ms"] is None
    assert run(tmp_path, as_of=END_H - DAY)["status"] == "NOT_READY"


# ------------------------------------------------------------------------------------------------------------------------------ confirmation
def test_confirmation_counts_only_after_the_screen_instant(tmp_path):
    screen = spread(105, [0, 1, 2, 3, 4])
    at_b1 = [CLOCK + 5 * DAY]  # decided exactly at B1: a confirmation attempt, not a screen one
    conf = spread(104, [5, 6, 7, 8, 9], hour=2)
    write_ledger(tmp_path, screen + at_b1 + conf)
    s = run(tmp_path, as_of=CLOCK + 5 * DAY)
    assert s["status"] == "READ_INSTANT" and s["read_instant_ms"] == CLOCK + 5 * DAY and s["O"] == 105
    b1 = s["read_instant_ms"]
    c = run(tmp_path, as_of=END_H, mode="confirm", screen_b=b1)
    assert c["status"] == "READ_INSTANT" and c["read_instant_ms"] == CLOCK + 10 * DAY and c["O"] == 105 and c["o_dates"] == 5
    assert min(c["kept_open_by_utc_date"]) == F.utc_date(b1)


def test_confirmation_needs_five_further_dates(tmp_path):
    write_ledger(tmp_path, spread(105, [0, 1, 2, 3, 4]) + spread(200, [5, 6, 7, 8], hour=2))
    c = run(tmp_path, as_of=END_H, mode="confirm", screen_b=CLOCK + 5 * DAY)
    assert c["status"] == "NOT_DECIDABLE" and c["o_dates"] == 4


def test_mode_arguments_refused():
    with pytest.raises(F.Refused):
        F.compute("/nonexistent", BOT, CLOCK, "confirm", [FULL_HEALTH[0]], [FULL_HEALTH[1]], "", END_H, None)
    with pytest.raises(F.Refused):
        F.compute("/nonexistent", BOT, CLOCK, "screen", [FULL_HEALTH[0]], [FULL_HEALTH[1]], "", END_H, CLOCK + DAY)
    with pytest.raises(F.Refused):
        F.compute("/nonexistent", BOT, CLOCK, "confirm", [FULL_HEALTH[0]], [FULL_HEALTH[1]], "", END_H, END_H)


# ------------------------------------------------------------------------------------------------------------------------------ pinned scorer parity
def test_loader_matches_pinned_load_decisions(tmp_path):
    P = pinned_arena_score()
    write_ledger(tmp_path, spread(37, [0, 1, 3]), spread(9, [2], hour=5))
    mine = F.load_decision_rows(tmp_path, BOT)
    assert [e for _, e in mine] == P["load_decisions"](tmp_path, BOT)
    assert sum(k == "open" for k, _ in mine) == 37 and sum(k == "refused" for k, _ in mine) == 9
    (tmp_path / BOT / "trades-zzz.jsonl").write_text('{"kind":"open","decision_ms":{"stamp":1}}\n')
    with pytest.raises(P["Refused"]):
        P["load_decisions"](tmp_path, BOT)
    with pytest.raises(F.Refused, match="torn"):
        F.load_decision_rows(tmp_path, BOT)
    assert F.load_decision_rows(tmp_path / "missing", BOT) == [] == P["load_decisions"](tmp_path / "missing", BOT)


def test_never_earlier_than_the_pinned_boundary():
    P = pinned_arena_score()
    rng = random.Random(11)
    never = lambda _e: False  # noqa: E731
    for _ in range(300):
        n_o, n_r = rng.randrange(80, 160), rng.randrange(0, 20)
        days = rng.randrange(3, 12)
        rows = [("open", rng.randrange(CLOCK, CLOCK + days * DAY)) for _ in range(n_o)] + [("refused", rng.randrange(CLOCK, CLOCK + days * DAY)) for _ in range(n_r)]
        mine = F.find_instant(rows, CLOCK, END_H, END_H, never)
        theirs = P["blind_read_instant"]([e for _, e in rows], CLOCK)
        if mine["read_instant_ms"] is not None:
            assert theirs is not None and theirs <= mine["read_instant_ms"]
            k = sum(1 for _, e in rows if e < mine["read_instant_ms"])
            assert mine["O"] + mine["R"] == k


def test_pins_recorded_in_tool():
    assert F.PINNED_AM6_READ_SHA256 == hashlib.sha256((REPO / "tools" / "arena_am6_read.py").read_bytes()).hexdigest()
    assert F.utc(F.CLOCK_MS) == "2026-10-25T00:00:00Z" and F.H_DAYS == 14 and F.BOT_IDS[1] == "hv4-CON-F" and len(F.BOT_IDS) == 9
    assert CLOCK + F.H_DAYS * DAY == 1_794_096_000_000 and F.CLOCK_MS == 1_792_886_400_000


def test_merge_health_matches_am6_read():
    R = pytest.importorskip("tools.arena_am6_read")
    a = health_text(CLOCK, CLOCK + DAY, {CLOCK + 3 * HOUR: ["price-path"]})
    b = health_text(CLOCK + DAY, CLOCK + 2 * DAY)
    _, msha, shas = F.merge_health([a[0], b[0]], [b[1], a[1]])
    _, rsha, rshas = R.merge_health([a[0], b[0]], [b[1], a[1]])
    assert (msha, shas) == (rsha, rshas)
    with pytest.raises(F.Refused, match="expected"):
        F.merge_health([a[0]], [b[1]])
    c = health_text(CLOCK, CLOCK + DAY)  # same hours as a, but hour 3 healthy
    with pytest.raises(F.Refused, match="differs"):
        F.merge_health([a[0], c[0]], [a[1], c[1]])


# ------------------------------------------------------------------------------------------------------------------------------ record and CLI
def test_write_once(tmp_path):
    p = tmp_path / "instant.json"
    sha = F.write_once(p, {"a": 1})
    assert hashlib.sha256(p.read_bytes()).hexdigest() == sha
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o444
    with pytest.raises(F.Refused, match="write-once"):
        F.write_once(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 1}


def _cli_files(tmp_path):
    h = tmp_path / "h.jsonl"
    h.write_text(FULL_HEALTH[0])
    r = tmp_path / "restarts.jsonl"
    r.write_text("")
    return h, r


def _argv(tmp_path, h, r, as_of, mode="screen", out=None, clock=CLOCK, sha=FULL_HEALTH[1], screen=None):
    v = ["--mode", mode, "--ledger-root", str(tmp_path / "L"), "--bot", BOT, "--clock-ms", str(clock), "--health", str(h), "--health-sha", sha, "--restarts", str(r),
         "--as-of-ms", str(as_of)]
    return v + (["--out", str(out)] if out else []) + (["--screen-record", str(screen)] if screen else [])


def test_cli_screen_confirm_and_refusals(tmp_path, capsys):
    write_ledger(tmp_path / "L", spread(105, [0, 1, 2, 3, 4]) + spread(110, [5, 6, 7, 8, 9], hour=2))
    h, r = _cli_files(tmp_path)
    now = END_H + DAY
    out = tmp_path / "screen.json"
    assert F.main(_argv(tmp_path, h, r, CLOCK + 4 * DAY, out=out), now_ms=now) == 0
    txt = capsys.readouterr().out
    assert not out.exists() and "NOT_READY" in txt
    for bad in ("O ", "R ", "margin", "attempts_sha256", "dates", "kept_"):  # QP-VERDICT-915 item 3: no running counts before B
        assert bad not in txt.replace("READ INSTANT", "").replace("NOT_READY", "").replace("END_H", "")
    assert F.main(_argv(tmp_path, h, r, CLOCK + 5 * DAY, out=out), now_ms=now) == 0
    rec = json.loads(out.read_text())
    assert rec["status"] == "READ_INSTANT" and rec["read_instant_ms"] == CLOCK + 5 * DAY and rec["mode"] == "screen" and rec["bot_id"] == BOT
    assert rec["tool_sha256"] == hashlib.sha256((REPO / "tools" / "hv4_read_instant.py").read_bytes()).hexdigest()
    text = capsys.readouterr().out
    assert "READ_INSTANT 2026-10-30T00:00:00Z" in text and "recorded" in text
    assert F.main(_argv(tmp_path, h, r, CLOCK + 5 * DAY, out=out), now_ms=now) == 2  # write-once
    cout = tmp_path / "confirm.json"
    assert F.main(_argv(tmp_path, h, r, END_H, mode="confirm", out=cout, screen=out), now_ms=now) == 0
    crec = json.loads(cout.read_text())
    assert crec["mode"] == "confirm" and crec["screen_instant_ms"] == CLOCK + 5 * DAY and crec["read_instant_ms"] == CLOCK + 10 * DAY
    # refusals
    assert F.main(_argv(tmp_path, h, r, END_H, mode="confirm", screen=cout), now_ms=now) == 2  # a confirm record is not a screen record
    assert F.main(_argv(tmp_path, h, r, END_H, mode="confirm"), now_ms=now) == 2  # no screen record
    assert F.main(_argv(tmp_path, h, r, END_H, clock=CLOCK + 2 * DAY), now_ms=now) == 2  # not a FLOOR clock
    assert F.main(_argv(tmp_path, h, r, now + 1), now_ms=now) == 2  # future as-of
    assert F.main(_argv(tmp_path, h, r, END_H, sha="0" * 64), now_ms=now) == 2  # health sha mismatch
    err = capsys.readouterr().err
    assert err.count("REFUSED") == 6


def test_cli_not_decidable_is_recorded(tmp_path):
    write_ledger(tmp_path / "L", spread(104, [0, 1, 2, 3, 4, 5, 6]))
    h, r = _cli_files(tmp_path)
    out = tmp_path / "nd.json"
    assert F.main(_argv(tmp_path, h, r, END_H, out=out), now_ms=END_H + DAY) == 0
    rec = json.loads(out.read_text())
    assert rec["status"] == "NOT_DECIDABLE" and rec["read_instant_ms"] is None and rec["O"] == 104


def test_other_clock_and_other_bot_refused(tmp_path):
    import pytest as _pt
    with _pt.raises(F.Refused):
        F.compute("/nonexistent", BOT, CLOCK + DAY, "screen", [FULL_HEALTH[0]], [FULL_HEALTH[1]], "", END_H, None)
    with _pt.raises(F.Refused):
        F.compute("/nonexistent", "l1-floor-v2", CLOCK, "screen", [FULL_HEALTH[0]], [FULL_HEALTH[1]], "", END_H, None)


def test_output_has_counts_only(tmp_path):
    write_ledger(tmp_path, spread(104, [0, 1, 2, 3, 4]) + spread(8, [1], hour=5), refused=[at(2, 2, k) for k in range(3)])
    res = run(tmp_path, as_of=CLOCK + 6 * DAY)
    text = "\n".join(F.render(dict(res, tool_sha256="x", am6_exclusions_sha256="y")))
    for bad in ("pnl", "price", "legs", "by_utc_date", "dropped"):
        assert bad not in text
    assert res["status"] == "READ_INSTANT" and f"O {res['O']}  R {res['R']}" in text


def test_not_ready_render_is_status_and_last_boundary_only(tmp_path):
    write_ledger(tmp_path, spread(104, [0, 1, 2, 3, 4]))
    res = run(tmp_path, as_of=CLOCK + 5 * DAY)
    assert res["status"] == "NOT_READY"
    lines = F.render(dict(res, tool_sha256="x", am6_exclusions_sha256="y"))
    assert len(lines) == 2 and "last tested" in lines[1]
    assert not any(k in "\n".join(lines) for k in ("margin", "attempts_sha256", "O 10", "kept_"))
    # READ_INSTANT keeps its counts
    write_ledger(tmp_path / "b", spread(110, [0, 1, 2, 3, 4]))
    ok = run(tmp_path / "b", as_of=CLOCK + 5 * DAY)
    assert ok["status"] == "READ_INSTANT" and any("margin" in x for x in F.render(dict(ok, tool_sha256="x", am6_exclusions_sha256="y")))


def test_not_ready_leaves_no_file_and_ready_record_carries_the_counts(tmp_path):
    import pytest as _pt
    write_ledger(tmp_path / "L", spread(104, [0, 1, 2, 3, 4]))
    h, r = _cli_files(tmp_path)
    out = tmp_path / "nr.json"
    assert F.main(_argv(tmp_path, h, r, CLOCK + 5 * DAY, out=out), now_ms=END_H + DAY) == 0
    assert not out.exists() and not list(tmp_path.glob("nr*"))  # NOT_READY: nothing on disk, so no running per-date counts
    nr = run(tmp_path / "L", as_of=CLOCK + 5 * DAY)
    assert nr["status"] == "NOT_READY"
    with _pt.raises(F.Refused, match="NOT_READY"):
        F.write_once(tmp_path / "direct.json", nr)
    assert not (tmp_path / "direct.json").exists()
    write_ledger(tmp_path / "M", spread(110, [0, 1, 2, 3, 4]))
    h2, r2 = _cli_files(tmp_path)
    out2 = tmp_path / "ready.json"
    assert F.main(["--mode", "screen", "--ledger-root", str(tmp_path / "M"), "--bot", BOT, "--health", str(h2), "--health-sha", FULL_HEALTH[1], "--restarts", str(r2),
                   "--as-of-ms", str(CLOCK + 5 * DAY), "--out", str(out2)], now_ms=END_H + DAY) == 0
    rec = json.loads(out2.read_text())
    assert rec["status"] == "READ_INSTANT" and sum(rec["kept_open_by_utc_date"].values()) == rec["O"] == 110  # per-date counts only in the READY record
