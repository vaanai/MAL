import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import arena_am6_exclusions as m

H = m.HOUR_MS
T0 = 1_792_000_000_000 // H * H  # an hour boundary


def hs(i):
    from datetime import datetime, timezone
    return datetime.fromtimestamp((T0 + i * H) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def health(spec, tamper=None):
    """spec: {hour_index: None (healthy) | list of labels (excluded)}"""
    lines = [m.canon({"hour": hs(i), "excluded": v is not None, "labels_failing": sorted(v or [])}) for i, v in sorted(spec.items())]
    sha = hashlib.sha256("".join(ln + "\n" for ln in lines).encode()).hexdigest()
    fin = m.canon({"type": "sha256", "hours": len(lines), "sha256": tamper or sha})
    return "\n".join(lines + [fin]) + "\n"


def att(rows):
    return "".join(json.dumps(r) + "\n" for r in rows)


def A(aid, dh, eh, **kw):
    r = {"attempt_id": aid, "decided_ms": T0 + dh * H + 5, "exit_ms": None if eh is None else T0 + eh * H + 9}
    r.update(kw)
    return r


def out(h, a, r=""):
    lines = m.run(h, a, r)
    rows = [json.loads(x) for x in lines]
    return {x["attempt_id"]: x["status"] for x in rows if x["type"] == "attempt"}, next(x for x in rows if x["type"] == "totals"), rows[-1]


BASE = {i: None for i in range(10)}


def test_each_class():
    sp = dict(BASE); sp[1] = ["decision-input"]; sp[4] = ["price-path"]; sp[6] = ["price-path", "decision-input"]
    st, t, _ = out(health(sp), att([
        A("ok", 0, 0), A("dec_excl", 1, 1), A("span_pp", 3, 5), A("span_di_only", 0, 2), A("decided_pp", 4, 4),
        A("span_both_label", 5, 7)]))
    assert st == {"ok": "counted", "dec_excl": m.S_10, "span_pp": m.S_SPAN, "span_di_only": "counted",
                  "decided_pp": m.S_10, "span_both_label": m.S_SPAN}
    assert t["n_excl_10pct_class"] == 2 and t["n_excl_5pct_class"] == 2 and t["n_counted"] == 2


def test_never_both_caps():
    sp = dict(BASE); sp[1] = ["price-path"]
    # decided in excluded hour 1 AND span overlaps excluded hour 2: 10% class only
    sp[2] = ["price-path"]
    st, t, _ = out(health(sp), att([A("x", 1, 3)]))
    assert st["x"] == m.S_10 and t["n_excl_5pct_class"] == 0 and t["n_excl_10pct_class"] == 1
    # open at restart AND span overlap: 5% class once
    st, t, _ = out(health(sp), att([A("y", 0, 3)]), json.dumps([T0 + 1 * H + 100, T0 + 1 * H + 200]) + "\n")
    assert st["y"] == m.S_RESTART  # span also touches price-path hour 2, but it is one attempt in one class
    assert t["n_excl_10pct_class"] == 0 and t["n_excl_5pct_class"] == 1


def test_cap_boundary_10pct():
    sp = dict(BASE); sp[1] = ["decision-input"]
    rows = [A(f"e{i}", 1, 1) for i in range(1)] + [A(f"g{i}", 0, 0) for i in range(9)]
    _, t, _ = out(health(sp), att(rows))
    assert t["n_attempts"] == 10 and t["share_10pct"] == 0.1 and t["verdict_cap"] == "OK"
    rows = [A(f"e{i}", 1, 1) for i in range(2)] + [A(f"g{i}", 0, 0) for i in range(18)] + [A("h", 0, 0)]
    _, t, _ = out(health(sp), att(rows))  # 2/21 < 10%
    assert t["verdict_cap"] == "OK"
    rows = [A(f"e{i}", 1, 1) for i in range(2)] + [A(f"g{i}", 0, 0) for i in range(17)]
    _, t, _ = out(health(sp), att(rows))  # 2/19 > 10%
    assert t["verdict_cap"] == "NOT_DECIDABLE_10PCT"


def test_cap_boundary_5pct():
    sp = dict(BASE); sp[2] = ["price-path"]
    rows = [A("s", 1, 3)] + [A(f"g{i}", 0, 0) for i in range(19)]
    _, t, _ = out(health(sp), att(rows))
    assert t["n_excl_5pct_class"] == 1 and t["share_5pct"] == 0.05 and t["verdict_cap"] == "OK"
    rows = [A("s", 1, 3)] + [A(f"g{i}", 0, 0) for i in range(18)]
    _, t, _ = out(health(sp), att(rows))
    assert t["verdict_cap"] == "NOT_DECIDABLE_5PCT"


def test_5pct_denominator_excludes_10pct_class():
    sp = dict(BASE); sp[2] = ["price-path"]; sp[5] = ["decision-input"]
    # 1 span of 20 non-10 attempts = 5% OK; 2 excluded decided in hour 5 do not change the 5% denominator rule except to remove them
    rows = [A("s", 1, 3)] + [A(f"g{i}", 0, 0) for i in range(19)] + [A("d1", 5, 5), A("d2", 5, 5)]
    _, t, _ = out(health(sp), att(rows))
    assert t["share_5pct"] == 0.05 and t["verdict_cap"] == "OK"


def test_restart_gap_hours_and_open_positions():
    r = json.dumps([T0 + 3 * H + 1000, T0 + 4 * H + 2000]) + "\n"
    st, t, _ = out(health(BASE), att([
        A("in_gap_h3", 3, 3), A("in_gap_h4", 4, 4),          # decided in restart gap hours (rounded outward): 10% class
        A("open_closed_later", 2, 6), A("open_null2", 2, None),
        A("before_closed_before", 2, 2), A("after", 5, 5), A("flag", 0, 0, open_at_restart=True)]), r)
    assert st["in_gap_h3"] == st["in_gap_h4"] == m.S_10
    assert st["open_closed_later"] == st["open_null2"] == st["flag"] == m.S_RESTART
    assert st["before_closed_before"] == "counted" and st["after"] == "counted"
    assert t["n_excl_open_at_restart"] == 3


def test_open_without_restart_counts_and_is_reported():
    st, t, _ = out(health(BASE), att([A("o", 2, None)]))
    assert st["o"] == "counted" and t["n_open_unclosed_counted"] == 1


def test_gap_hour_in_span_is_not_pricepath():
    sp = dict(BASE); sp[4] = ["price-path"]
    r = json.dumps([T0 + 4 * H + 1, T0 + 4 * H + 2]) + "\n"
    st, _, _ = out(health(sp), att([A("a", 6, 7)]), r)  # after the restart; hour 4 not in span
    assert st["a"] == "counted"


def test_unmeasurable_hour_refused():
    with pytest.raises(m.Refused):
        out(health({0: None}), att([A("a", 0, 3)]))
    with pytest.raises(m.Refused):
        out(health({0: None}), att([A("a", 7, 7)]))


@pytest.mark.parametrize("key", ["pnl", "price_lamports", "sol_in", "fill_state", "amount", "net_pnl_bps", "SOL"])
def test_forbidden_keys_refused(key):
    with pytest.raises(m.Refused, match="forbidden"):
        out(health(BASE), att([A("a", 0, 0, **{key: 1})]))


def test_unknown_key_and_bad_rows_refused():
    for bad in ({"foo": 1}, {"decided_ms": "x"}):
        with pytest.raises(m.Refused):
            out(health(BASE), att([A("a", 0, 0, **bad)]))
    with pytest.raises(m.Refused):
        out(health(BASE), att([A("a", 0, 0), A("a", 1, 1)]))
    with pytest.raises(m.Refused):
        out(health(BASE), att([{"attempt_id": "a", "decided_ms": 5, "exit_ms": 1}]))


def test_health_sha_verified():
    with pytest.raises(m.Refused, match="sha256 mismatch"):
        out(health(BASE, tamper="0" * 64), att([A("a", 0, 0)]))
    good = health(BASE)
    lines = good.split("\n")
    lines[0] = lines[0].replace('"excluded":false', '"excluded":true')
    with pytest.raises(m.Refused, match="mismatch"):
        out("\n".join(lines), att([A("a", 0, 0)]))
    with pytest.raises(m.Refused):
        out("\n".join(good.split("\n")[:-2]) + "\n", att([A("a", 0, 0)]))


def test_deterministic_and_output_sha():
    h = health({**BASE, 2: ["price-path"]})
    a = att([A("b", 1, 3), A("a", 0, 0)])
    o1, o2 = m.run(h, a, ""), m.run(h, att([A("a", 0, 0), A("b", 1, 3)]), "")
    assert o1 == o2
    fin = json.loads(o1[-1])
    assert fin["sha256"] == hashlib.sha256("".join(x + "\n" for x in o1[:-1]).encode()).hexdigest()


def test_cli_exit_codes(tmp_path):
    (tmp_path / "h").write_text(health(BASE))
    (tmp_path / "a").write_text(att([A("a", 0, 0)]))
    (tmp_path / "bad").write_text(att([A("a", 0, 0, pnl=1)]))
    tool = str(Path(m.__file__))
    ok = subprocess.run([sys.executable, tool, "--health", str(tmp_path / "h"), "--attempts", str(tmp_path / "a")], capture_output=True, text=True)
    assert ok.returncode == 0 and json.loads(ok.stdout.strip().split("\n")[-1])["type"] == "sha256"
    bad = subprocess.run([sys.executable, tool, "--health", str(tmp_path / "h"), "--attempts", str(tmp_path / "bad")], capture_output=True, text=True)
    assert bad.returncode == 2 and bad.stdout == ""
