"""Tests for tools/r4b_layout.py on SYNTHETIC walker-layout hour files in tmp dirs. Nothing here reads /data/mal.

Needs duckdb (/data/mal/audit-1008/venv) and pytest (/data/mal/venv); run e.g.
  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_r4b_layout.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from tools import r4b_layout as rl  # noqa: E402

H3, H4, H5 = "2026-10-16T03", "2026-10-16T04", "2026-10-16T05"
BT3 = int(datetime(2026, 10, 16, 3, tzinfo=timezone.utc).timestamp())
S = 440_000_000  # base slot of hour 03
WSOL = rl.WSOL_MINT
V = 17_600_000_000


def pda_of(mint: str) -> str:
    return "PDA_" + mint


def trade(mint, slot, tx, ev, side="buy", sol=1_000_000_000, tok=5_000_000, pool=None, vq=V, bt=None, venue="pumpswap", trader="T1", sig=None):
    r = {"v": 2, "venue": venue, "mint": mint, "trader": trader, "side": side, "sol_lamports": sol, "token_raw": tok, "quote_reserve": 60_000_000_000,
         "base_reserve": 250_000_000_000_000, "pool": pool if pool is not None else pda_of(mint), "slot": slot,
         "signature": sig or f"sig-{mint}-{slot}-{tx}-{ev}", "event_index": ev, "tx_index": tx, "block_time": bt if bt is not None else BT3 + (slot - S) // 2,
         "quote_mint": WSOL}
    if venue == "pumpswap" and vq is not None:
        r["virtual_quote_reserves"] = vq
    if venue == "pump_bonding":
        r.pop("pool")
        r["pool"] = None
    return r


def complete(mint, slot, tx=1):
    return {"type": "complete", "mint": mint, "slot": slot, "block_time": BT3 + (slot - S) // 2, "tx_index": tx, "venue": "pump_bonding"}


def migration(mint, slot, pool, quote=WSOL, tx=2):
    return {"type": "migration", "mint": mint, "slot": slot, "block_time": BT3 + (slot - S) // 2, "tx_index": tx, "event_index": 0, "pool": pool, "quote_mint": quote,
            "event_source": "log"}


def create(mint, creator="C1", mayhem=False, slot=S - 500):
    return {"type": "create", "mint": mint, "creator": creator, "is_mayhem_mode": mayhem, "slot": slot, "block_time": BT3 - 250}


def zst(path: Path, lines: list[str]) -> None:
    raw = path.with_suffix("")  # .jsonl
    raw.write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    subprocess.run(["zstd", "-q", "-f", "--rm", str(raw), "-o", str(path)], check=True)


def make_walk(root: Path, hours: dict, *, unsealed=(), no_verify=(), rows_delta=None, raw_extra=None, drop_migrations=(), bad_verify_issue=()):
    """hours: {hour: {"trades": [...], "creates": [...], "migrations": [...]}} rows as dicts. Writes zst files, checkpoint.json and verify.jsonl."""
    root.mkdir(parents=True, exist_ok=True)
    cp, vlines = {"hours": {}}, []
    for h, d in hours.items():
        content, sha = {}, {}
        for sub in rl.SUBS:
            (root / sub).mkdir(exist_ok=True)
            if sub in drop_migrations and sub == "migrations":
                continue
            lines = [json.dumps(r, separators=(",", ":")) for r in d.get(sub, [])]
            n_good = len(lines)
            lines += (raw_extra or {}).get((h, sub), [])
            p = root / sub / f"{sub}-{h}.jsonl.zst"
            zst(p, lines)
            sha[sub] = hashlib.sha256(p.read_bytes()).hexdigest()
            rows = n_good + (rows_delta or {}).get((h, sub), 0)
            content[sub] = {"rows": rows, "unique": rows, "duplicates": 0, "bad_lines": 0}
        if h not in unsealed:
            cp["hours"][h] = {"status": "sealed"}
        if h not in no_verify:
            vlines.append(json.dumps({"hour": h, "issues": ["boom"] if h in bad_verify_issue else [], "content": content, "sha256": sha, "bad_lines": 0}))
    (root / "checkpoint.json").write_text(json.dumps(cp))
    (root / "verify.jsonl").write_text("\n".join(vlines) + "\n")
    return root


def scenario() -> dict:
    """One graduation hour (03) plus a second hour (04) of later prints."""
    t3, t4, mig, cr = [], [], [], []
    # A: good member (PDA match, WSOL, V0 in band). Prints out of order on purpose; a print before mslot and one on another pool.
    ms = S + 100
    mig += [complete("MA", ms), migration("MA", ms, pda_of("MA"))]
    cr.append(create("MA", "CA"))
    t3 += [trade("MA", ms + 30, 5, 1, side="sell"), trade("MA", ms + 30, 5, 0), trade("MA", ms + 30, 2, 3), trade("MA", ms + 10, 9, 0, vq=V + 7),
           trade("MA", ms - 1, 1, 0, vq=17_000_000_000), trade("MA", ms + 12, 1, 0, pool="OTHER_POOL", vq=17_650_000_000)]
    t4 += [trade("MA", S + 8000, 1, 0, bt=BT3 + 3700)]
    t3 += [trade("MA", ms - 50, 1, 0, venue="pump_bonding", trader="B1"), trade("MA", ms, 1, 0, venue="pump_bonding", trader="B2", side="sell"),
           trade("MA", ms + 1, 1, 0, venue="pump_bonding", trader="B3")]  # the last one is at slot mslot+1: dropped
    # B: pool in the migration event is not the PDA
    mig += [complete("MB", S + 200), migration("MB", S + 200, "OTHER_B")]
    cr.append(create("MB"))
    t3 += [trade("MB", S + 210, 1, 0)]
    # C: non-WSOL quote
    mig += [complete("MC", S + 300), migration("MC", S + 300, pda_of("MC"), quote="NOT_WSOL")]
    cr.append(create("MC"))
    t3 += [trade("MC", S + 310, 1, 0)]
    # D: V0 out of band (just above), E: at the band edges, X: just below
    for m, v in (("MD", 17_700_000_001), ("ME", 17_700_000_000), ("MF", 17_500_000_000), ("MX", 17_499_999_999)):
        mig += [complete(m, S + 400), migration(m, S + 400, pda_of(m))]
        cr.append(create(m))
        t3 += [trade(m, S + 410, 1, 0, vq=v)]
    # G: s0 window edge, last slot in the window (mslot + 7299) -> member (print lands in hour 04 by file, slot is what counts)
    mig += [complete("MG", S + 500), migration("MG", S + 500, pda_of("MG"))]
    cr.append(create("MG"))
    t4 += [trade("MG", S + 500 + 7299, 1, 0, bt=BT3 + 3650)]
    # H: first print at mslot + 7300 -> no s0
    mig += [complete("MH", S + 600), migration("MH", S + 600, pda_of("MH"))]
    cr.append(create("MH"))
    t4 += [trade("MH", S + 600 + 7300, 1, 0, bt=BT3 + 3700)]
    # I: mayhem
    mig += [complete("MI", S + 700), migration("MI", S + 700, pda_of("MI"))]
    cr.append(create("MI", mayhem=True))
    t3 += [trade("MI", S + 710, 1, 0)]
    # J: no migration event at all (event mode: not a match); K: same mbt as MA's neighbour for the mid tie-break
    mig += [complete("MJ", S + 800)]
    cr.append(create("MJ"))
    t3 += [trade("MJ", S + 810, 1, 0)]
    for m in ("MZ2", "MZ1"):  # same slot / block_time as each other: mid ties break by mint
        mig += [complete(m, S + 900), migration(m, S + 900, pda_of(m))]
        cr.append(create(m))
        t3 += [trade(m, S + 910, 1, 0)]
    # N: no create row at all -> kept (NaN create constants downstream)
    mig += [complete("MN", S + 1100), migration("MN", S + 1100, pda_of("MN"))]
    t3 += [trade("MN", S + 1110, 1, 0)]
    return {H3: {"trades": t3, "creates": cr, "migrations": mig}, H4: {"trades": t4, "creates": [], "migrations": []}}


@pytest.fixture()
def walk(tmp_path):
    return make_walk(tmp_path / "walk", scenario())


def build(walk, tmp_path, name="out", **kw):
    kw.setdefault("canonical_fn", pda_of)
    return rl.build(walk, H3, H5, tmp_path / name, **kw)


def q(path_or_sql, tmp_path=None):
    return duckdb.connect().sql(path_or_sql).fetchall()


def test_hour_range():
    assert rl.hour_range("2026-10-16T22", "2026-10-17T02") == ["2026-10-16T22", "2026-10-16T23", "2026-10-17T00", "2026-10-17T01"]
    with pytest.raises(rl.LayoutRefused):
        rl.hour_range("2026-10-16T03", "2026-10-16T03")


def test_membership_and_meta(walk, tmp_path):
    st = build(walk, tmp_path)
    out = tmp_path / "out"
    rows = q(f"select mid, mint, pool, mslot, mbt, s0, v, day, pda_match from '{out}/meta.parquet' order by mid")
    mints = [r[1] for r in rows]
    # members: A, E (V0 = 17.7e9), F (V0 = 17.5e9), G (window edge), N (no create), plus Z1/Z2
    assert set(mints) == {"MA", "ME", "MF", "MG", "MN", "MZ1", "MZ2"}
    # not members: B (pool mismatch), C (non-WSOL), D/X (band), H (s0 at mslot+7300), I (mayhem), J (no event), L (V0 missing)
    # mid = row_number over (mbt, mint): MA first, then by complete slot, ties (MZ1, MZ2) by mint
    assert mints == ["MA", "ME", "MF", "MG", "MZ1", "MZ2", "MN"]
    assert [r[0] for r in rows] == list(range(7))
    ma = rows[0]
    assert ma[2] == pda_of("MA") and ma[3] == S + 100 and ma[5] == S + 110 and ma[6] == V + 7 and ma[7] == "2026-10-16" and ma[8] is True
    mg = [r for r in rows if r[1] == "MG"][0]
    assert mg[5] == S + 500 + 7299
    assert st["members"] == 7 and st["members_per_day"] == {"2026-10-16": 7}


def test_stats_numbers(walk, tmp_path):
    st = build(walk, tmp_path)
    non_mayhem = {"MA", "MB", "MC", "MD", "ME", "MF", "MG", "MH", "MJ", "MX", "MZ1", "MZ2", "MN"}
    assert st["completes"] == 14 and st["mayhem"] == 1 and st["non_mayhem"] == len(non_mayhem) == 13
    assert st["no_migration_event"] == 1 and st["non_wsol_quote"] == 1 and st["no_create"] == 1
    matched = non_mayhem - {"MB", "MC", "MJ"}
    assert st["pda_match"] == len(matched)
    assert st["pda_match_share"] == pytest.approx(len(matched) / len(non_mayhem))
    with_s0 = matched - {"MH"}
    assert st["with_s0"] == len(with_s0)
    assert st["v0_missing"] == 0  # event mode refuses a null V instead
    in_band = with_s0 - {"MD", "MX"}
    assert st["v0_in_band"] == len(in_band)
    assert st["v0_band_share"] == pytest.approx(len(in_band) / len(with_s0))
    assert st["members"] == len(in_band)


def test_ut_filters_and_order(walk, tmp_path):
    build(walk, tmp_path)
    out = tmp_path / "out"
    rows = q(f"select mid, slot, txi, evi, isbuy, vq from '{out}/ut/{H3}.parquet' where mid = 0")
    ms = S + 100
    # kept: slot >= s0 (= ms + 10); the print at ms-1 and the OTHER_POOL print are gone
    assert [(r[1], r[2], r[3]) for r in rows] == [(ms + 10, 9, 0), (ms + 30, 2, 3), (ms + 30, 5, 0), (ms + 30, 5, 1)]
    assert rows[0][5] == V + 7 and rows[-1][4] is False  # event vq is the row's own; the sell is evi 1
    # the next hour's file holds the later print
    assert q(f"select slot from '{out}/ut/{H4}.parquet' where mid = 0") == [(S + 8000,)]
    # whole file ordered by (mid, slot, txi, evi)
    allrows = q(f"select mid, slot, txi, evi from '{out}/ut/{H3}.parquet'")
    assert allrows == sorted(allrows)
    cols = [r[0] for r in q(f"describe select * from '{out}/ut/{H3}.parquet'")]
    assert cols == ["mid", "slot", "txi", "evi", "bt", "isbuy", "sol", "tok", "q", "b", "th", "vq"]


def test_horizon_drops_late_prints(tmp_path):
    sc = scenario()
    late = trade("MA", S + 9000, 1, 0, bt=BT3 + 100 + 52 * 3600 + 1801)
    ok = trade("MA", S + 9001, 1, 0, bt=BT3 + 100 + 52 * 3600 + 1800 - 100)
    sc[H4]["trades"] += [late, ok]
    walk = make_walk(tmp_path / "w", sc)
    build(walk, tmp_path)
    slots = [r[0] for r in q(f"select slot from '{tmp_path}/out/ut/{H4}.parquet' where mid = 0")]
    # MA's mbt = BT3 + 50; limit = mbt + 52h + 1800
    assert S + 9001 in slots and S + 9000 not in slots


def test_bond_clock_mkt_creates(walk, tmp_path):
    build(walk, tmp_path)
    out = tmp_path / "out"
    b = q(f"select mid, slot, isbuy from '{out}/bond/{H3}.parquet'")
    ms = S + 100
    assert b == [(0, ms - 50, True), (0, ms, False)]  # slot < mslot + 1 only (slot == mslot is kept); mslot+1 is out
    # clock: min slot per block_time second over ALL trade rows (both venues, members or not)
    bt = BT3 + 25
    clk = dict((r[0], (r[1], r[2])) for r in q(f"select bt, smin, smax from '{out}/clock/{H3}.parquet'"))
    assert clk[BT3 + (S + 50 - S) // 2][0] == S + 50  # the bonding print at ms - 50 (nothing smaller in that second)
    mk = q(f"select mnt, n_ps, v_ps, n_bd, v_bd from '{out}/mkt/{H3}.parquet' order by mnt")
    assert all(r[0] % 60 == 0 for r in mk)
    assert sum(r[3] for r in mk) == 3 and sum(r[4] for r in mk) == 3_000_000_000
    n_ps = sum(1 for r in scenario()[H3]["trades"] if r["venue"] == "pumpswap")
    assert sum(r[1] for r in mk) == n_ps
    assert q(f"select sum(n_cr) from '{out}/mkt_creates.parquet'") == [(len(scenario()[H3]["creates"]),)]
    assert bt  # silence the unused name


def test_creators_use_duckdb_hash(walk, tmp_path):
    build(walk, tmp_path)
    out = tmp_path / "out"
    rows = q(f"select c.mid, c.ch, c.cbt, c.cslot, hash('CA') from '{out}/creators.parquet' c where c.mid = 0")
    assert rows[0][1] == rows[0][4] and rows[0][2] == BT3 - 250 and rows[0][3] == S - 500
    n = q(f"select ch is null from '{out}/creators.parquet' c join '{out}/meta.parquet' m using(mid) where m.mint = 'MN'")
    assert n == [(True,)]  # no create: kept, creator NULL


def test_s0_window_edge_excludes_mslot_plus_7300(walk, tmp_path):
    build(walk, tmp_path)
    assert q(f"select count(*) from '{tmp_path}/out/meta.parquet' where mint in ('MH')") == [(0,)]


def test_prep_work(walk, tmp_path):
    import numpy as np

    build(walk, tmp_path)
    info = rl.prep_work(tmp_path / "out")
    w = tmp_path / "out" / "work"
    ck, cs = np.load(w / "clock_bt.npy"), np.load(w / "clock_slot.npy")
    assert len(ck) == len(cs) == info["clock"] and list(ck) == sorted(set(ck))
    mk = np.load(w / "mkt.npz")
    assert set(mk.files) == {"mnt", "n_ps", "v_ps", "n_bd", "v_bd", "n_cr"}
    import pandas as pd

    mc = pd.read_pickle(w / "mintconst.pkl")
    assert len(mc) == 7 and mc.mid.tolist() == list(range(7))
    assert int(mc.nb[0]) == 2 and int(mc.ntr[0]) == 2


# ---- refusals

def refused(walk, tmp_path, match, **kw):
    with pytest.raises(rl.LayoutRefused, match=match):
        build(walk, tmp_path, **kw)
    assert not (tmp_path / "out" / "meta.parquet").exists()  # nothing written


def test_refuses_unsealed_hour(tmp_path):
    walk = make_walk(tmp_path / "w", scenario(), unsealed=(H4,))
    refused(walk, tmp_path, "not sealed")


def test_refuses_unverified_hour(tmp_path):
    walk = make_walk(tmp_path / "w", scenario(), no_verify=(H4,))
    refused(walk, tmp_path, "no OK line")


def test_refuses_verify_with_issues(tmp_path):
    walk = make_walk(tmp_path / "w", scenario(), bad_verify_issue=(H3,))
    refused(walk, tmp_path, "no OK line")


def test_refuses_missing_hour_in_range(walk, tmp_path):
    with pytest.raises(rl.LayoutRefused, match="2026-10-16T06"):
        rl.build(walk, H3, "2026-10-16T07", tmp_path / "out", canonical_fn=pda_of)


def test_refuses_sha_mismatch(walk, tmp_path):
    p = walk / "trades" / f"trades-{H4}.jsonl.zst"
    zst(p, [json.dumps(trade("MA", S + 1, 1, 0))])
    refused(walk, tmp_path, "do not match the sha256")


def test_refuses_row_count_mismatch(tmp_path):
    walk = make_walk(tmp_path / "w", scenario(), rows_delta={(H3, "trades"): 1})
    refused(walk, tmp_path, "rows=")


def test_refuses_row_count_mismatch_in_creates(tmp_path):
    walk = make_walk(tmp_path / "w", scenario(), rows_delta={(H3, "creates"): -1})
    refused(walk, tmp_path, "creates has")


def test_refuses_bad_line(tmp_path):
    walk = make_walk(tmp_path / "w", scenario(), raw_extra={(H3, "trades"): ["{not json"]}, rows_delta={(H3, "trades"): 1})
    # verify.jsonl is made to agree on the row count so the bad line alone has to refuse
    refused(walk, tmp_path, "bad line")


def test_refuses_nul_line(tmp_path):
    walk = make_walk(tmp_path / "w", scenario(), raw_extra={(H4, "trades"): ["\x00\x00\x00"]}, rows_delta={(H4, "trades"): 1})
    refused(walk, tmp_path, "bad line")


def test_refuses_missing_migrations_file(tmp_path):
    walk = make_walk(tmp_path / "w", scenario(), drop_migrations=("migrations",))
    refused(walk, tmp_path, "no migrations file")


def test_injected_verify_row_count(walk, tmp_path):
    table = ef_table(walk)
    table[H3]["content"]["trades"]["rows"] += 5
    with pytest.raises(rl.LayoutRefused, match="rows=") as ei:
        rl.build(walk, H3, H5, tmp_path / "o", canonical_fn=pda_of, problems_fn=lambda w, h: [], verify=table)
    assert any("trades has" in r for r in ei.value.reasons)


def test_injected_problems_fn_refuses(walk, tmp_path):
    with pytest.raises(rl.LayoutRefused, match="injected"):
        rl.build(walk, H3, H5, tmp_path / "o", canonical_fn=pda_of, problems_fn=lambda w, h: ["injected problem"])


def ef_table(walk):
    from tools import exp012_forward as ef

    return {h: json.loads(json.dumps(r)) for h, r in ef.verified_hours(walk, strict_bad_lines=True).items()}


def test_refuses_bad_v_source_and_mixing(walk, tmp_path):
    with pytest.raises(rl.LayoutRefused, match="v_source"):
        rl.build(walk, H3, H5, tmp_path / "o", v_source="nope", canonical_fn=pda_of)
    with pytest.raises(rl.LayoutRefused, match="needs the"):
        rl.build(walk, H3, H5, tmp_path / "o", v_source="static_map", canonical_fn=pda_of)
    with pytest.raises(rl.LayoutRefused, match="mix"):
        rl.build(walk, H3, H5, tmp_path / "o", v_source="event", static_map={}, canonical_fn=pda_of)


def test_refuses_out_dir_inside_dataset(walk, tmp_path):
    with pytest.raises(rl.LayoutRefused, match="inside"):
        rl.build(walk, H3, H5, walk / "out", canonical_fn=pda_of)


# ---- V source

def parquet_rows(path):
    return q(f"select * from '{path}' order by all")


def test_event_and_static_map_identical_when_vq_constant(tmp_path):
    sc = scenario()
    for h in sc.values():  # every pumpswap print carries the SAME V, in band
        for r in h["trades"]:
            if r["venue"] == "pumpswap":
                r["virtual_quote_reserves"] = V
    walk = make_walk(tmp_path / "w", sc)
    pools = {pda_of(m): V for m in ("MA", "MB", "MC", "MD", "ME", "MF", "MG", "MH", "MI", "MJ", "MX", "MZ1", "MZ2", "ML", "MN")}
    s_ev = build(walk, tmp_path, "ev")
    s_sm = build(walk, tmp_path, "sm", v_source="static_map", static_map=pools)
    # In this scenario the membership rule differs only by the event-only conditions (pda match / missing V0): compare on the members both agree on
    ev_mints = {r[0] for r in q(f"select mint from '{tmp_path}/ev/meta.parquet'")}
    sm_mints = {r[0] for r in q(f"select mint from '{tmp_path}/sm/meta.parquet'")}
    assert ev_mints <= sm_mints
    assert sm_mints - ev_mints == {"MB", "MC", "MJ"}  # static replay does not read the migration event
    assert s_ev["v_source"] == "event" and s_sm["v_source"] == "static_map"


def test_event_and_static_map_byte_identical_on_exploration_shaped_tape(tmp_path):
    """Exploration-shaped: every mint has a matching migration event and a constant vq -> every output file is equal."""
    sc = scenario()
    keep = {"MA", "ME", "MF", "MG", "MZ1", "MZ2", "MN", "MH", "MI"}
    for h in sc.values():
        for sub in ("trades", "creates", "migrations"):
            h[sub] = [r for r in h[sub] if r["mint"] in keep]
        for r in h["trades"]:
            if r["venue"] == "pumpswap":
                r["virtual_quote_reserves"] = V
    walk = make_walk(tmp_path / "w", sc)
    pools = {pda_of(m): V for m in keep}
    build(walk, tmp_path, "ev")
    build(walk, tmp_path, "sm", v_source="static_map", static_map=pools)
    names = ["meta.parquet", "creators.parquet", "mkt_creates.parquet"] + [f"{d}/{h}.parquet" for d in ("ut", "bond", "clock", "mkt") for h in (H3, H4)]
    for n in names:
        a, b = parquet_rows(tmp_path / "ev" / n), parquet_rows(tmp_path / "sm" / n)
        assert a == b, n
    # ordered ut must match in ORDER too, not only as sets
    for h in (H3, H4):
        assert q(f"select * from '{tmp_path}/ev/ut/{h}.parquet'") == q(f"select * from '{tmp_path}/sm/ut/{h}.parquet'")
    assert len(q(f"select * from '{tmp_path}/ev/meta.parquet'")) == 7  # A E F G Z1 Z2 N; H has no s0, I is mayhem


def test_static_map_sets_vq_and_membership(walk, tmp_path):
    pools = {pda_of("MA"): V, pda_of("ME"): 17_650_000_000, pda_of("MD"): 17_800_000_000, pda_of("MF"): None}
    build(walk, tmp_path, v_source="static_map", static_map=pools)
    out = tmp_path / "out"
    assert [r[0] for r in q(f"select mint from '{out}/meta.parquet' order by mid")] == ["MA", "ME"]
    assert set(r[0] for r in q(f"select distinct vq from '{out}/ut/{H3}.parquet' where mid = 0")) == {V}  # the map V, not the rows' (V + 7 on one row)
    assert q(f"select distinct vq from '{out}/ut/{H3}.parquet' where mid = 1") == [(17_650_000_000,)]


# ---- PDA with the real derivation

def test_real_pda_and_pda_mismatch(tmp_path):
    solders = pytest.importorskip("solders")
    from solders.pubkey import Pubkey

    from tools.exp016_screen import canonical_pool_str

    ma, mb = str(Pubkey.new_unique()), str(Pubkey.new_unique())
    ms = S + 100
    pool_a, pool_b = canonical_pool_str(ma), canonical_pool_str(mb)
    assert pool_a != pool_b
    sc = {H3: {"trades": [trade(ma, ms + 5, 1, 0, pool=pool_a), trade(mb, ms + 6, 1, 0, pool=pool_a)],
               "creates": [create(ma), create(mb)],
               "migrations": [complete(ma, ms), migration(ma, ms, pool_a), complete(mb, ms), migration(mb, ms, pool_a)]},  # mb names a's pool
          H4: {"trades": [], "creates": [], "migrations": []}}
    walk = make_walk(tmp_path / "w", sc)
    st = rl.build(walk, H3, H5, tmp_path / "out")  # default canonical_fn
    assert [r[0] for r in q(f"select mint from '{tmp_path}/out/meta.parquet'")] == [ma]
    assert st["pda_match"] == 1 and st["pda_match_share"] == 0.5
    assert q(f"select pool from '{tmp_path}/out/meta.parquet'") == [(pool_a,)]


def test_pda_error_is_not_a_match(walk, tmp_path):
    def bad(m):
        if m == "MA":
            raise ValueError("not a pubkey")
        return pda_of(m)

    st = rl.build(walk, H3, H5, tmp_path / "out", canonical_fn=bad)
    assert st["pda_errors"] == 1
    assert "MA" not in {r[0] for r in q(f"select mint from '{tmp_path}/out/meta.parquet'")}


def test_decoder_pin_problems_reports_mismatch(monkeypatch):
    from tools import forward_v_join as fvj

    monkeypatch.setattr(fvj, "pin_status", lambda: [{"file": "x.py", "pinned": "a", "actual": "b", "ok": False}, {"file": "y.py", "pinned": "c", "actual": "c", "ok": True}])
    assert rl.decoder_pin_problems() == ["decoder blob x.py: pinned a, actual b"]


def test_cli_refusal_exit_code(walk, tmp_path, capsys):
    rc = rl.main(["--walk-dir", str(walk), "--from-hour", H3, "--to-hour", "2026-10-16T09", "--out-dir", str(tmp_path / "o")])
    assert rc == 2 and "refused" in capsys.readouterr().err


def test_load_static_map_both_shapes(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"fetched_utc": "x", "v": {"P1": 17600000000, "P2": None}}))
    (tmp_path / "b.json").write_text(json.dumps({"P1": 17600000000, "P2": None}))
    assert rl.load_static_map(tmp_path / "a.json") == rl.load_static_map(tmp_path / "b.json") == {"P1": 17600000000, "P2": None}


def test_stats_null_txi_tie_groups_and_mixed_slots(tmp_path):
    sc = scenario()
    ms = S + 100
    # MA: three null-tx rows sharing (slot, evi) = one tie group of 3; a second slot mixing null and non-null tx; a third with distinct evi (no tie)
    sc[H3]["trades"] += [trade("MA", ms + 40, None, 0, sig="n1"), trade("MA", ms + 40, None, 0, sig="n2"), trade("MA", ms + 40, None, 0, sig="n3"),
                         trade("MA", ms + 50, None, 0, sig="m1"), trade("MA", ms + 50, 3, 0, sig="m2"), trade("MA", ms + 60, None, 0, sig="p1"),
                         trade("MA", ms + 60, None, 1, sig="p2")]
    walk = make_walk(tmp_path / "w", sc)
    st = build(walk, tmp_path)
    assert st["null_txi_tie_groups"] == 1 and st["mixed_null_txi_slots"] == 1  # only ms+50 mixes null and non-null; ms+40 / ms+60 are all null


def test_event_mode_refuses_null_vq_on_member_print(tmp_path):
    sc = scenario()
    sc[H3]["trades"] += [trade("MA", S + 120, 1, 0, vq=None)]  # MA is a member (s0 has V); a later print without event V
    walk = make_walk(tmp_path / "w", sc)
    refused(walk, tmp_path, "MEMBER")
    assert not (tmp_path / "out" / "creators.parquet").exists()


def test_null_vq_on_non_member_pools_is_not_refused_and_day_share_is_flagged(tmp_path):
    sc = scenario()
    ms = S + 1200
    sc[H3]["trades"] += [trade("MA", S + 120, 1, 0, pool="OTHER_POOL", vq=None),  # not the PDA pool
                         trade("MI", S + 711, 1, 0, vq=None)]  # mayhem pool, null V
    sc[H3]["migrations"] += [complete("MQ", ms), migration("MQ", ms, pda_of("MQ"))]  # s0 print without V: V0 missing -> non-member
    sc[H3]["creates"] += [create("MQ")]
    sc[H3]["trades"] += [trade("MQ", ms + 5, 1, 0, vq=None), trade("MQ", ms + 6, 1, 0, vq=None)]
    walk = make_walk(tmp_path / "w", sc)
    st = build(walk, tmp_path)
    assert st["members"] == 7 and st["v0_missing"] == 1
    assert "MQ" not in {r[0] for r in q(f"select mint from '{tmp_path}/out/meta.parquet'")}
    day = st["v0_missing_share_by_day"]["2026-10-16"]
    assert day["v0_missing"] == 1 and day["share"] == pytest.approx(1 / day["candidates"]) and day["share"] > rl.V0_MISSING_REFUSE_SHARE
    assert st["v0_missing_days_over"] == ["2026-10-16"] and rl.V0_MISSING_REFUSE_SHARE == 0.02


def test_no_missing_v0_day_is_not_flagged(walk, tmp_path):
    st = build(walk, tmp_path)
    assert st["v0_missing_days_over"] == []


def test_ordering_equals_r4b_features_forward_order(tmp_path):
    """Within-slot order: (slot, tx_index with null -> -1 first, event_index, frn), the same as tools.r4b_features.forward_order."""
    import numpy as np

    from tools.r4b_features import forward_order

    ms = S + 100
    rows = [(ms + 20, 4, 1), (ms + 20, None, 0), (ms + 20, None, 0), (ms + 20, 2, 5), (ms + 20, None, 0), (ms + 19, 9, 9), (ms + 20, 4, 0), (ms + 21, None, 3)]
    sc = scenario()
    sc[H3]["trades"] += [trade("MA", sl, tx, ev, sol=1000 + i) for i, (sl, tx, ev) in enumerate(rows)]  # sol = the file position marker
    walk = make_walk(tmp_path / "w", sc)
    build(walk, tmp_path)
    marker = {1000 + i for i in range(len(rows))}
    got = [r[0] - 1000 for r in q(f"select sol from '{tmp_path}/out/ut/{H3}.parquet' where mid = 0") if r[0] in marker]
    slot = np.array([r[0] for r in rows])
    tx = np.array([np.nan if r[1] is None else r[1] for r in rows])
    ev = np.array([r[2] for r in rows])
    want = list(forward_order(slot, tx, ev, np.arange(len(rows))))
    assert got == want and want.index(1) < want.index(2) < want.index(4)  # null-tx ties keep file order


def test_static_map_does_not_refuse_null_vq(tmp_path):
    sc = scenario()
    sc[H3]["trades"] += [trade("MA", S + 120, 1, 0, vq=None)]
    walk = make_walk(tmp_path / "w", sc)
    st = build(walk, tmp_path, v_source="static_map", static_map={pda_of("MA"): V})
    assert st["members"] == 1
