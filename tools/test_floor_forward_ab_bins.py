"""floor_forward_ab landing bins (land_vault_sol, entry_over_mark0): synthetic ledger rows only (no tape, no real ledger, no real pnl/ab file).
PAPER, hypothesis tracking; nothing here is a gate read."""
import hashlib, json, sys, tempfile, types
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "research"))
import floor_forward_ab as AB  # noqa: E402
import floor_nov_pnl as P  # noqa: E402
from tools.arena_stack import fee_ppm  # noqa: E402

V = 17_580_000_000  # meta.v (lamports), arbitrary
BE = 1_000_000_000_000.0
FAR = int(datetime(2026, 11, 25, tzinfo=timezone.utc).timestamp() * 1000)


def ms(d, h=12):
    return int(datetime(2026, 11, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def mark0_for(Qe, Be, ratio):
    net = 0.25e9 * (1 - float(fee_ppm(Qe, Be)) / 1e6)
    return (Qe + net) / Be * 1e-3 / ratio


def op(pid, t):
    return {"kind": "open", "position_id": pid, "pool": "P" + pid, "decision_ms": {"entry": t}}


def tr(pid, stake, net, t, vault=None, ratio=None, state="filled", reason="time_stop", fields=True):
    d = {"kind": "trade", "position_id": pid, "stake_sol": stake, "exit_reason": reason, "decision_ms": {"entry": t, "exit": t + 1000},
         "legs": {"total": {f"{m}_1p9": {"net_lamports": net, "state": state} for m in ("flat", "press")}}, "calc": {}, "meta": {}}
    if fields:
        Qe = int(round(vault * 1e9)) + V
        d["calc"] = {"1p9": {"Qe": Qe, "Be": BE}}
        d["meta"] = {"v": V, "mark0": mark0_for(Qe, BE, ratio)}
    return d


def pos_rows(pid, t, net10, net25, **kw):
    return [op(pid, t), tr(pid, 0.10, net10, t, **kw), tr(pid, 0.25, net25, t, **kw)]


def pnl(rows):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ledger" / "l1-floor-v2"
        p.mkdir(parents=True)
        (p / "trades-x.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        P.main([d + "/ledger", d + "/o.json", "--to-ms", str(FAR)])
        return json.load(open(d + "/o.json"))


def table(base_rows, var_rows, fwd="2026-11-12"):
    pb, pv = pnl(base_rows), pnl(var_rows)
    pb["header"], pv["header"] = {"variant": {}, "frozen_rule": True}, {"variant": {"Q_STAR": 1e9}, "frozen_rule": False}
    with tempfile.TemporaryDirectory() as d:
        for n, o in (("b", pb), ("v", pv)):
            json.dump(o, open(f"{d}/{n}.json", "w"))
        AB.table(types.SimpleNamespace(base=d + "/b.json", variant=d + "/v.json", out_json=d + "/ab.json", out_md=d + "/ab.md", end_hour="2026-11-19T23", forward_from=fwd))
        return json.load(open(d + "/ab.json")), open(d + "/ab.md").read()


def fixture(with_open=False):
    """11-10 in-sample; 11-12 and 11-13 forward. Stake-0.25 nets as given; stake-0.10 nets are 0.4x."""
    r = []
    n = [0]

    def add(pid, day, n25, **kw):
        n[0] += 1
        r.extend(pos_rows(pid, ms(day, n[0]), int(n25 * 0.4), n25, **kw))
    add("i1", 10, 1_000_000, vault=0.5, ratio=1.0)                       # exact edges: <=0.5 and <=1.00
    add("i2", 10, -2_000_000, vault=0.7, ratio=1.02)
    add("f1", 12, 5_000_000, vault=0.3, ratio=1.0, reason="tp_1.5x")
    add("f2", 12, -1_000_000, vault=0.3, ratio=1.02)
    add("f3", 12, 3_000_000, vault=0.3, ratio=1.1)
    add("f4", 12, -4_000_000, vault=0.3, ratio=1.3)
    add("f5", 12, -2_000_000, vault=0.3, ratio=2.0)
    add("f6", 12, 2_000_000, vault=3.0, ratio=1.3)
    add("f7", 13, -160_000, vault=5.0, ratio=2.3, state="guarded")      # guarded: counted apart, in no bin
    add("f8", 13, 700_000, fields=False)                                # priced, landing fields missing -> unknown
    r.extend([op("f10", ms(13, 23)), {"kind": "excluded", "position_id": "f10", "stake_sol": 0.10, "exclusion_reason": "feed_gap"},
              {"kind": "excluded", "position_id": "f10", "stake_sol": 0.25, "exclusion_reason": "feed_gap"}])  # unpriced
    if with_open:
        r.append(op("f9", ms(13, 22)))                                  # no trade, no exclusion: open at tape end -> 11-13 incomplete
    return r


def test_bin_edges():
    assert [AB.vault_bin(x) for x in (0.5, 0.5000001, 0.8, 0.8000001, 0.19, None)] == ["<=0.5", "0.5-0.8", "0.5-0.8", ">0.8", "<=0.5", "unknown"]
    assert [AB.ratio_bin(x) for x in (1.0, 1.0000001, 1.05, 1.15, 1.1500001, 1.5, 1.5000001, 0.9, None)] == [
        "<=1.00", "1.00-1.05", "1.00-1.05", "1.05-1.15", "1.15-1.5", "1.15-1.5", ">1.5", "<=1.00", "unknown"]


def test_pnl_carries_landing_fields_and_keeps_old_ones():
    p = pnl(pos_rows("a", ms(5, 1), 1_000_000, 2_000_000, vault=0.5, ratio=1.0, reason="tp_1.5x"))["positions"][0]
    assert p["landing"]["Qe"] == 500_000_000 + V and p["landing"]["v"] == V
    assert p["leg_state"] == {"0.1": "filled", "0.25": "filled"} and p["exit_reason"]["0.25"] == "tp_1.5x"
    assert set(p) >= {"pid", "pool", "t0", "day", "status", "reason", "net"}
    assert AB.landing_fields(p) == (0.5, 1.0)  # exactly on both edges
    q = pnl([op("m", ms(5, 1))] + [tr("m", s, 1, ms(5, 1), fields=False) for s in (0.10, 0.25)])["positions"][0]
    assert AB.landing_fields(q) == (None, None)


def test_bins_split_guarded_unknown_unpriced():
    rows = fixture()
    ab, md = table(rows, rows)
    lb = ab["landing_bins"]["bins"]["base"]["0.25"]
    ins, fwd = lb["IN-SAMPLE"], lb["FORWARD"]
    assert ins["n_entries"] == 2 and ins["vault"]["<=0.5"]["n"] == 1 and ins["vault"]["0.5-0.8"]["n"] == 1  # 0.5 exactly in <=0.5
    assert ins["ratio"]["<=1.00"]["n"] == 1 and ins["ratio"]["1.00-1.05"]["n"] == 1                          # 1.00 exactly in <=1.00
    assert fwd["n_entries"] == 9 and fwd["n_guarded"] == 1
    v = fwd["vault"]
    assert (v["<=0.5"]["n"], v["0.5-0.8"]["n"], v[">0.8"]["n"], v["unknown"]["n"], v["unknown"]["n_unpriced"]) == (5, 0, 1, 1, 1)
    assert sum(b["n"] + b["n_unpriced"] for b in v.values()) + fwd["n_guarded"] == fwd["n_entries"]
    assert [fwd["ratio"][b]["n"] for b in ("<=1.00", "1.00-1.05", "1.05-1.15", "1.15-1.5", ">1.5")] == [1, 1, 1, 2, 1]
    b0 = v["<=0.5"]  # f1..f5: 5, -1, 3, -4, -2 million lamports on 0.25 SOL
    assert abs(b0["total_sol"] - 1e-3) < 1e-12 and b0["tp"] == 1
    assert abs(b0["ex_top3_sol"] - (-6e-3)) < 1e-12 and abs(b0["mean_pct"] - (0.4 / 5)) < 1e-9 and abs(b0["median_pct"] - (-0.4)) < 1e-9
    assert v["0.5-0.8"]["mean_pct"] is None and v["0.5-0.8"]["ex_top3_sol"] is None
    s10 = ab["landing_bins"]["bins"]["vault1.0"]["0.1"]["FORWARD"]["vault"]["<=0.5"]
    assert s10["n"] == 5 and abs(s10["total_sol"] - 0.4e-3) < 1e-12
    assert "paper, hypothesis tracking only; no gate claim; n is small; decide nothing before at least 7 complete forward days" in md
    assert "## landing-state bins" in md and "0.25 stake" in md
    assert "MULTIPLE COMPARISONS" in md and "MULTIPLE COMPARISONS" in ab["landing_bins"]["caveat"]


def test_incomplete_forward_day_is_not_binned():
    ab, _ = table(fixture(True), fixture(True))
    f = ab["landing_bins"]["bins"]["base"]["0.25"]["FORWARD"]
    assert f["n_entries"] == 6 and f["n_guarded"] == 0  # 11-12 only; 11-13 holds an open position


def test_old_sections_unchanged():
    """digests of the output of the pre-change code (origin/main 54d0f588) for this fixed synthetic input"""
    rows = fixture()
    ab, md = table(rows, rows[:30])
    del ab["landing_bins"]
    old_md = md.split("\n## landing-state bins")[0]
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()
    assert h(json.dumps(ab, sort_keys=True)) == GOLDEN_JSON
    assert h(old_md) == GOLDEN_MD


GOLDEN_JSON = "acda7e2e8e8ac33f1301eadf1f35c8560aa62a745c3ddebef4705127782917a3"
GOLDEN_MD = "a208eb00c87c070ac7b613c5ae6fa0941dec16e686fa5d87063dd41abbf14cbe"
