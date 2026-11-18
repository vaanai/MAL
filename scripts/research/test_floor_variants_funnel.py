"""Tests for floor_variants_funnel.py: default (WSOL) output is byte-identical, and the --universe other-quote mode (counts only). Synthetic rows only."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

WSOL = "So11111111111111111111111111111111111111112"
H0, H1 = "2026-11-10T00", "2026-11-11T08"
M = int(datetime(2026, 11, 10, 0, tzinfo=timezone.utc).timestamp())
HR = 3600


def _row(pool, mint, side, ts, q, sol, tok, b, quote=WSOL, v=17_580_000_000, slot=1, idx=0):
    return {"type": "trade", "prog": "pumpswap", "pool": pool, "mint": mint, "side": side, "quote_mint": quote, "virtual_quote_reserves": v, "slot": slot, "event_ts": ts,
            "t_recv_ms": ts * 1000 + 5, "sol_lamports": sol, "token_raw": tok, "quote_reserve": q, "base_reserve": b, "signature": f"sig{ts}{idx}", "event_index": idx}


def wsol_fixture_rows():
    return [{"type": "create", "mint": "MINT1", "t_recv_ms": M * 1000}, {"type": "complete", "mint": "MINT1", "event_ts": M, "t_recv_ms": M * 1000 + 1},
            _row("POOL1", "MINT1", "buy", M, 5_000_000_000, 100_000_000, 1_000_000, 10**15),
            _row("POOL1", "MINT1", "sell", M + 20 * HR, 2_000_000_000, 500_000_000, 1_000_000, 10**15),
            _row("POOL1", "MINT1", "sell", M + 25 * HR, 2_000_000_000, 500_000_000, 1_000_000, 10**15),
            _row("POOL1", "MINT1", "sell", M + 27 * HR, 800_000_000, 400_000_000, 1_000_000, 3 * 10**15)]


def write_tape(tape, rows, h0=H0, h1=H1):
    from floor_nov_funnel import hours
    os.makedirs(tape, exist_ok=True)
    by_hour = {}
    for r in rows:
        by_hour.setdefault(datetime.fromtimestamp(r["t_recv_ms"] / 1000, timezone.utc).strftime("%Y-%m-%dT%H"), []).append(r)
    for h in hours(h0, h1):
        data = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in by_hour.get(h, []))
        p = os.path.join(tape, f"tape-{h}.jsonl")
        open(p, "w").write(data)
        subprocess.run(["zstd", "-q", "-f", "--rm", p, "-o", p + ".zst"], check=True)


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd CLI missing")

# md5 of the default-mode outputs on the synthetic WSOL fixture, recorded from origin/main f74fd027 BEFORE the other-quote change
GOLDEN = {"stdout": "678e5e019a79526d0fcca5e29f6e5f78", "variants": "0e90ce00bb32578597f05f5ad0fbe8ae", "triggers": "95d5d52edd7f622f59a8da78a68c046b"}


@needs_zstd
def test_default_output_byte_identical(tmp_path):
    tape, out = str(tmp_path / "tape"), str(tmp_path / "out")
    write_tape(tape, wsol_fixture_rows())
    p = subprocess.run([sys.executable, str(HERE / "floor_variants_funnel.py"), tape, out, H0, H1], capture_output=True, text=True, cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr
    got = {"stdout": hashlib.md5(p.stdout.encode()).hexdigest(), "variants": md5(out + "/variants-narrow.json"), "triggers": md5(out + "/triggers-narrow.jsonl")}
    res = json.load(open(out + "/variants-narrow.json"))
    assert res["base"]["rows"], "fixture must produce a base trigger"
    assert got == GOLDEN


# ---------------------------------------------------------------- --universe other-quote (counts only)
import floor_other_quote_funnel as OQ  # noqa: E402

PRICE = 10.0   # lamports of SOL per raw quote unit (synthetic)
ALLOWED_TOP = {"universe", "first_hour", "last_hour", "stages", "quote_price_file_sha256", "days"}
ALLOWED_CELL = {"trigger_pools", "trigger_rows", "funnel", "quote_mints_seen", "quote_mints_trigger"}
ALLOWED_FUNNEL = {"stage", "pools_reaching", "pools_dropped"}
V0 = 1_758_000_000


def oq_rows(quote="QUOTE1", pool="OPOOL", first=0, v_last=V0, last_q=80_000_000, last_b=3 * 10**15, last_sol=40_000_000, mid=True, small_vol=False):
    """Quote-unit twin of the WSOL trigger fixture: amounts divided by PRICE, V = 1.758e9 raw (the pool's own V_ref)."""
    sm = 5_000_000 if small_vol else 50_000_000
    rows = [_row(pool, "M" + pool, "buy", M + first * HR, 500_000_000, 10_000_000, 1_000_000, 10**15, quote=quote, v=V0),
            _row(pool, "M" + pool, "sell", M + 20 * HR, 200_000_000, sm, 1_000_000, 10**15, quote=quote, v=V0)]
    if mid:
        # the history print has an out-of-band V so that, as a sell row, it dies at v_scaled_band and never masks the stage where the LAST row is dropped
        rows.append(_row(pool, "M" + pool, "sell", M + 25 * HR, 200_000_000, sm, 1_000_000, 10**15, quote=quote, v=int(V0 * 1.5)))
    rows.append(_row(pool, "M" + pool, "sell", M + 27 * HR, last_q, last_sol, 1_000_000, last_b, quote=quote, v=v_last))
    return rows


def feed(rows, prices=None):
    c = OQ.Counter({"QUOTE1": PRICE} if prices is None else prices)
    for r in sorted(rows, key=lambda x: x["t_recv_ms"]):
        c.row(r)
    return c.counts(H0, H1, "x")


def cell(o, cls, day="2026-11-11"):
    return o["days"][day][cls]


def reach(c):
    return {f["stage"]: f["pools_reaching"] for f in c["funnel"]}


def test_trigger_counted_and_funnel_monotone():
    o = feed(oq_rows(quote=OQ.USDC), {OQ.USDC: PRICE})
    c = cell(o, "usdc")
    assert (c["trigger_pools"], c["trigger_rows"], c["quote_mints_seen"], c["quote_mints_trigger"]) == (1, 1, 1, 1)
    assert [f["pools_dropped"] for f in c["funnel"]] == [0] * len(OQ.STAGES)
    assert list(reach(c)) == OQ.STAGES


# (row mutation, prices, stage where the pool must be dropped = the first stage with 0 pools reaching)
@pytest.mark.parametrize("kw,prices,dropped_at", [
    ({}, {}, "quote_price_known"),                                    # quote not in the declared price file
    ({"v_last": V0 * 1.02}, None, "v_scaled_band"),                   # V / V_ref outside the scaled band
    ({"v_last": V0 * 0.99}, None, "v_scaled_band"),
    ({"first": 10}, None, "age_24_200"),                              # first print 10 h in: age 17 h
    ({"last_q": 2_000_000_000}, None, "vault_le_0.5_soleq"),          # vault above 0.5 SOL-equiv
    ({"mid": False}, None, "prev_le_6h"),                             # previous print 7 h back
    ({"small_vol": True, "last_sol": 4_000_000, "last_q": 30_000_000}, None, "vol24_ge_1_soleq"),
    ({"last_b": 10**15}, None, "r6_lt_0.80(trigger)"),                # spot ratio ~ 1
])
def test_each_filter_drops_at_its_stage(kw, prices, dropped_at):
    o = feed(oq_rows(**kw), prices)
    c = cell(o, "other")
    assert c["trigger_pools"] == 0 and c["trigger_rows"] == 0
    r = reach(c)
    first_zero = next(s for s in OQ.STAGES if r[s] == 0)
    assert first_zero == dropped_at, r
    assert next(f for f in c["funnel"] if f["stage"] == dropped_at)["pools_dropped"] == 1


def test_prev_print_not_visible_drops():
    # a single print visible (earlier prints are outside the 26 h window): FLOOR needs a visible previous print
    rows = [_row("OPOOL", "MO", "buy", M, 500_000_000, 10_000_000, 1_000_000, 10**15, quote="QUOTE1", v=V0),
            _row("OPOOL", "MO", "sell", M + 27 * HR, 80_000_000, 40_000_000, 1_000_000, 3 * 10**15, quote="QUOTE1", v=V0)]
    c = cell(feed(rows), "other")
    assert reach(c)["vault_le_0.5_soleq"] == 1 and reach(c)["prev_print_visible"] == 0


def test_conversion_uses_the_quote_price():
    rows = oq_rows()
    assert cell(feed(rows, {"QUOTE1": 10.0}), "other")["trigger_pools"] == 1
    # same raw amounts, 10x the SOL price: vault 3.9e7 raw * 100 = 3.9e9 lamports > 0.5 SOL
    c = cell(feed(rows, {"QUOTE1": 100.0}), "other")
    assert c["trigger_pools"] == 0 and reach(c)["vault_le_0.5_soleq"] == 0
    # 10x lower price: vault passes, 24 h volume (1.4e8 raw * 1 = 0.14 SOL-equiv) fails
    c = cell(feed(rows, {"QUOTE1": 1.0}), "other")
    assert c["trigger_pools"] == 0 and reach(c)["vol24_ge_1_soleq"] == 0 and reach(c)["prev_le_6h"] == 1


def test_scaled_v_uses_each_pools_own_first_row():
    # a pool whose V is 1000x the WSOL scale still passes: only V / V_ref matters
    big = oq_rows(pool="BIG")
    for r in big:
        r["virtual_quote_reserves"] = int(r["virtual_quote_reserves"] * 1000)
    assert cell(feed(big), "other")["trigger_pools"] == 1


def test_quote_classes_and_distinct_quote_mints():
    pump_mint = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU" + "pump"
    rows = (oq_rows(quote=OQ.USDC, pool="P1") + oq_rows(quote=pump_mint, pool="P2") + oq_rows(quote="QUOTE1", pool="P3") + oq_rows(quote="QUOTE2", pool="P4"))
    o = feed(rows, {OQ.USDC: PRICE, pump_mint: PRICE, "QUOTE1": PRICE, "QUOTE2": PRICE})
    d = o["days"]["2026-11-11"]
    assert set(d) == {"usdc", "pump", "other"}
    assert [d[k]["trigger_pools"] for k in ("usdc", "pump")] == [1, 1]
    assert d["other"]["trigger_pools"] == 2 and d["other"]["quote_mints_seen"] == 2 and d["other"]["quote_mints_trigger"] == 2
    assert OQ.quote_class(OQ.USDC) == "usdc" and OQ.quote_class(pump_mint) == "pump" and OQ.quote_class("QUOTE1") == "other"


def test_wsol_and_buy_rows_are_not_counted():
    rows = oq_rows() + [dict(r, pool="WP", quote_mint=WSOL) for r in oq_rows()]
    o = feed(rows)
    assert cell(o, "other")["trigger_pools"] == 1 and set(o["days"]["2026-11-11"]) == {"other"}
    buys_only = [r for r in oq_rows() if r["side"] == "buy"]
    assert feed(buys_only)["days"] == {}


def test_output_keys_are_exactly_the_allowed_set():
    o = OQ.check_keys(feed(oq_rows()))
    assert set(o) == ALLOWED_TOP == OQ.ALLOWED_TOP and OQ.ALLOWED_CELL == ALLOWED_CELL and OQ.ALLOWED_FUNNEL == ALLOWED_FUNNEL
    for cl in o["days"].values():
        for c in cl.values():
            assert set(c) == ALLOWED_CELL
            assert all(set(f) == ALLOWED_FUNNEL for f in c["funnel"])
            assert all(isinstance(c[k], int) for k in ("trigger_pools", "trigger_rows", "quote_mints_seen", "quote_mints_trigger"))
    # the serialized output carries no pool, mint, price or time value from the rows
    s = json.dumps(o)
    for needle in ("OPOOL", "MOPOOL", "QUOTE1", "sig", "t_recv"):
        assert needle not in s
    for bad in ({**o, "mark": 1}, {k: v for k, v in o.items() if k != "stages"}):
        with pytest.raises(ValueError):
            OQ.check_keys(bad)
    bad = json.loads(json.dumps(o))
    next(iter(next(iter(bad["days"].values())).values()))["pnl"] = 0
    with pytest.raises(ValueError):
        OQ.check_keys(bad)
    bad = json.loads(json.dumps(o))
    next(iter(next(iter(bad["days"].values())).values()))["funnel"][0]["price"] = 0
    with pytest.raises(ValueError):
        OQ.check_keys(bad)


def test_price_file_validation(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"A": 1.5}))
    d, sha = OQ.load_prices(str(p))
    assert d == {"A": 1.5} and len(sha) == 64
    for bad in ({"A": 0}, {"A": "x"}, [1]):
        p.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            OQ.load_prices(str(p))


@needs_zstd
def test_cli_other_quote_end_to_end(tmp_path):
    tape, out = str(tmp_path / "tape"), str(tmp_path / "out")
    write_tape(tape, oq_rows(quote=OQ.USDC) + wsol_fixture_rows())
    pf = tmp_path / "prices.json"
    pf.write_text(json.dumps({OQ.USDC: PRICE}))
    p = subprocess.run([sys.executable, str(HERE / "floor_variants_funnel.py"), "--universe", "other-quote", "--quote-sol-prices", str(pf), tape, out, H0, H1],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr
    assert sorted(os.listdir(out)) == ["counts-other-quote.json", "progress-other-quote.txt"]   # no triggers jsonl with prices
    o = json.load(open(out + "/counts-other-quote.json"))
    assert set(o) == ALLOWED_TOP and o["days"]["2026-11-11"]["usdc"]["trigger_pools"] == 1 and set(o["days"]["2026-11-11"]) == {"usdc"}
    # missing price file flag, unknown universe
    for argv in (["--universe", "other-quote", tape, out, H0, H1], ["--universe", "wsol", tape, out, H0, H1]):
        q = subprocess.run([sys.executable, str(HERE / "floor_variants_funnel.py")] + argv, capture_output=True, text=True, cwd=str(tmp_path))
        assert q.returncode != 0
