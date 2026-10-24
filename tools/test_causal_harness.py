"""Tests for tools/causal_harness.py on synthetic rows (no tape, no price, no outcome). Each leaky screen below reproduces one lookahead class seen in the lab."""
from __future__ import annotations

import json
import random

import pytest

from tools import causal_harness as C

SOL = 10**9


def make_rows(n_pools: int = 6, per_pool: int = 40, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for p in range(n_pools):
        t = 1_000_000 + p * 5_000
        price = 1000
        for i in range(per_pool):
            t += rng.choice((200, 400, 1_000, 3_000, 9_000))
            price = max(1, price + rng.randint(-60, 80))
            rows.append({"t_ms": t, "pool": f"P{p}", "side": rng.choice(("buy", "buy", "sell")), "sol": rng.randint(1, 50) * SOL // 10, "px": price, "slot": t // 400})
    rng.shuffle(rows)  # the harness orders them
    return rows


# ---- screens (batch) and their stream twins


def causal_screen(rows):
    """Enter a pool once, at the row where it has >= 4 buys in the trailing 30 s and its price is above its first seen price. Uses only rows at or before the row."""
    out, first, buys, done = [], {}, {}, set()
    for r in sorted(rows, key=lambda r: r["t_ms"]):
        p = r["pool"]
        first.setdefault(p, r["px"])
        q = buys.setdefault(p, [])
        if r["side"] == "buy":
            q.append(r["t_ms"])
        while q and q[0] < r["t_ms"] - 30_000:
            q.pop(0)
        if p not in done and len(q) >= 4 and r["px"] > first[p]:
            done.add(p)
            out.append({"decision_ms": r["t_ms"], "pool": p, "key": f"{p}:entry"})
    return out


def _fires(mine, i):
    r = mine[i]
    nb = sum(1 for x in mine[: i + 1] if x["side"] == "buy" and x["t_ms"] >= r["t_ms"] - 30_000)
    return nb >= 4 and r["px"] > mine[0]["px"]


def causal_decide(view, row):
    """The stream twin of causal_screen, pure: it re-derives 'already entered' from the rows it was shown (O(n^2), test sizes only)."""
    mine = view.where(lambda r: r["pool"] == row["pool"])
    if any(_fires(mine, i) for i in range(len(mine) - 1)):
        return None
    if _fires(mine, len(mine) - 1):
        return {"pool": row["pool"], "key": f"{row['pool']}:entry"}
    return None


def quantile_screen(rows):
    """Lookahead: the size threshold is the 90th percentile over ALL rows (an end-of-window aggregate)."""
    sizes = sorted(r["sol"] for r in rows)
    thr = sizes[int(0.9 * (len(sizes) - 1))]
    return [{"decision_ms": r["t_ms"], "pool": r["pool"], "key": f"{r['pool']}:{r['t_ms']}"} for r in rows if r["side"] == "buy" and r["sol"] >= thr]


def next_row_screen(rows):
    """Lookahead: enter on a buy if the pool's NEXT row has a higher price (shift(-1))."""
    by = {}
    for r in sorted(rows, key=lambda r: r["t_ms"]):
        by.setdefault(r["pool"], []).append(r)
    out = []
    for p, rs in by.items():
        for a, b in zip(rs, rs[1:]):
            if a["side"] == "buy" and b["px"] > a["px"]:
                out.append({"decision_ms": a["t_ms"], "pool": p, "key": f"{p}:{a['t_ms']}"})
    return out


def backfill_screen(rows):
    """Lookahead (DBC L1 analogue): a row's reference price is forward-filled from 'isolated' rows; rows before the first isolated row are BACK-filled from it."""
    by = {}
    for r in sorted(rows, key=lambda r: r["t_ms"]):
        by.setdefault(r["pool"], []).append(r)
    out = []
    for p, rs in by.items():
        ref = None
        first_iso = next((r["px"] for r in rs if r["sol"] < SOL), None)
        for r in rs:
            if r["sol"] < SOL:
                ref = r["px"]
            use = ref if ref is not None else first_iso
            if use is not None and r["side"] == "buy" and r["px"] < use:
                out.append({"decision_ms": r["t_ms"], "pool": p, "key": f"{p}:{r['t_ms']}"})
    return out


def misstamped_screen(rows):
    """Lookahead: looks at the first 60 s of a pool but stamps the decision at the pool's first row."""
    by = {}
    for r in sorted(rows, key=lambda r: r["t_ms"]):
        by.setdefault(r["pool"], []).append(r)
    out = []
    for p, rs in by.items():
        t0 = rs[0]["t_ms"]
        w = [r for r in rs if r["t_ms"] <= t0 + 60_000]
        if sum(r["side"] == "buy" for r in w) >= 0.6 * len(w):
            out.append({"decision_ms": t0, "pool": p, "key": f"{p}:early"})
    return out


def exit_refusal_screen(rows):
    """Lookahead (DBC L3 analogue): drop an entry whose exit row (20 s later) is 'bad' (a sell above 3 SOL)."""
    out = []
    for e in causal_screen(rows):
        later = [r for r in rows if r["pool"] == e["pool"] and r["t_ms"] >= e["decision_ms"] + 20_000]
        if later and later[0]["side"] == "sell" and later[0]["sol"] > 3 * SOL:
            continue
        out.append(e)
    return out


# ---- tests


def test_causal_screen_passes():
    res = C.lookahead_check(causal_screen, make_rows(), keep_fields=("slot",))
    assert res.n_decisions > 0
    assert res.verdict == "CAUSAL_OK", res.as_dict()
    assert all(p.ok for p in res.probes) and {p.mode for p in res.probes} == {"truncate", "perturb"}


@pytest.mark.parametrize("screen", [quantile_screen, next_row_screen, backfill_screen, misstamped_screen])
def test_leaky_screens_are_caught(screen):
    res = C.lookahead_check(screen, make_rows(), keep_fields=("slot",))
    assert res.verdict == "LOOKAHEAD", screen.__name__
    rep = res.as_dict()
    assert rep["first_failure"]["n_only_full"] + rep["first_failure"]["n_only_probe"] > 0


def test_exit_refusal_caught_with_rows_that_trigger_it():
    rows = make_rows(n_pools=12, per_pool=60, seed=3)
    # make sure some exit rows are bad sells, so the refusal fires
    for r in rows:
        if r["side"] == "sell":
            r["sol"] = 5 * SOL
    assert len(exit_refusal_screen(rows)) < len(causal_screen(rows))
    assert C.lookahead_check(exit_refusal_screen, rows, keep_fields=("slot",)).verdict == "LOOKAHEAD"


def test_report_carries_no_price_fields():
    rep = C.lookahead_check(next_row_screen, make_rows()).as_dict()
    blob = json.dumps(rep["first_failure"])
    assert '"px"' not in blob and '"sol"' not in blob


def test_stream_view_never_shows_a_later_row():
    rows = make_rows()
    seen_max = []

    def decide(view, row):
        assert all(r["t_ms"] <= row["t_ms"] for r in view.rows())
        assert view.now_ms == row["t_ms"]
        with pytest.raises(C.LookaheadError):
            view.at(row["t_ms"] + 1)
        seen_max.append(len(view))
        return None

    res = C.run_stream(decide, rows)
    assert seen_max == list(range(1, len(rows) + 1)) and res.rows_seen == len(rows)


def test_stream_projects_allowed_fields_only():
    rows = make_rows()

    def decide(view, row):
        assert set(row) == {"t_ms", "pool", "side"}
        assert all(set(r) == {"t_ms", "pool", "side"} for r in view.last(3))
        return None

    C.run_stream(decide, rows, allowed_fields=("pool", "side"))


def test_stream_refuses_backdated_decision():
    with pytest.raises(C.LookaheadError):
        C.run_stream(lambda v, r: {"decision_ms": r["t_ms"] - 1}, make_rows())


def test_outcome_fields_refused_unless_allowed():
    rows = make_rows()
    rows[0]["fwd_ret_60s"] = 0.1
    with pytest.raises(C.FieldRefused):
        C.run_stream(lambda v, r: None, rows)
    C.check_fields(rows, allowed_fields=["fwd_ret_60s", "t_ms", "pool", "side", "sol", "px", "slot"])


def test_stream_batch_equivalence_md5():
    rows = make_rows()
    rep = C.stream_batch_equivalence(causal_decide, causal_screen, rows)
    assert rep["equivalent"], rep
    assert rep["md5_stream"] == C.md5_decisions(causal_screen(rows))
    rep2 = C.stream_batch_equivalence(causal_decide, quantile_screen, rows)
    assert not rep2["equivalent"]


def test_canon_is_order_free_and_md5_stable():
    a = [{"decision_ms": 2, "k": 1}, {"decision_ms": 1, "k": 2}]
    assert C.md5_decisions(a) == C.md5_decisions(list(reversed(a)))
    with pytest.raises(ValueError):
        C.canon([{"k": 1}])


def test_order_rows_requires_int_ms():
    with pytest.raises(ValueError):
        C.order_rows([{"t_ms": 1.5}])
    with pytest.raises(ValueError):
        C.order_rows([{"x": 1}])


def test_cutoffs_include_just_before_each_decision():
    rows = [{"t_ms": t} for t in (10, 20, 30)]
    cs = C.choose_cutoffs([{"decision_ms": 20}], rows, 8, 1)
    assert cs == [19, 20]


def test_cli_check_and_equiv(tmp_path):
    rows = make_rows()
    rp = tmp_path / "rows.jsonl"
    rp.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = tmp_path / "rep.json"
    assert C.main(["check", "--screen", "tools.test_causal_harness:causal_screen", "--rows", str(rp), "--out", str(out), "--keep-field", "slot"]) == 0
    assert json.loads(out.read_text())["verdict"] == "CAUSAL_OK"
    assert C.main(["check", "--screen", "tools.test_causal_harness:next_row_screen", "--rows", str(rp), "--out", str(out)]) == 3
    assert json.loads(out.read_text())["verdict"] == "LOOKAHEAD"
    assert C.main(["equiv", "--screen", "tools.test_causal_harness:causal_screen", "--decide", "tools.test_causal_harness:causal_decide", "--rows", str(rp), "--out", str(out)]) == 0
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"t_ms": 1, "pnl_sol": 1.0}) + "\n")
    assert C.main(["check", "--screen", "tools.test_causal_harness:causal_screen", "--rows", str(bad), "--out", str(out)]) == 2
