"""Tests for tools/r4b_count.py on SYNTHETIC walker-layout hours in tmp dirs (fixtures from test_r4b_layout). Nothing here reads /data/mal sealed data.

  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages:. /data/mal/venv/bin/python -m pytest tools/test_r4b_count.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from tools import exp012_forward as ef  # noqa: E402
from tools import r4b_count as rc  # noqa: E402
from tools import r4b_layout as rl  # noqa: E402
from tools.test_r4b_layout import BT3, S, complete, create, make_walk, migration, pda_of, scenario, trade, zst  # noqa: E402,F401

HOUR0 = datetime(2026, 10, 16, 3, tzinfo=timezone.utc)


def hkey(i: int) -> str:
    return ef.hour_key(HOUR0 + timedelta(hours=i))


def s_of(i: int) -> int:
    return S + 7200 * i  # 2 slots per second


def b_of(i: int) -> int:
    return BT3 + 3600 * i


def stream(n_hours: int, *, extra=None) -> dict:
    """Hour i: token Mi graduates at s_i+100 (bt b_i+50), prints at +110..130 and +7000..7020 (bt b_i+3500..); M0 also prints at +7000..7002 in every hour,
    so at the mark b_i: live = {M(i-1), M0}: 0, 1, 2, 2, ..."""
    hours = {}
    for i in range(n_hours):
        si, mi = s_of(i), f"M{i}"
        t = [trade(mi, si + 110 + 10 * x, 1, 0, bt=b_of(i) + 55 + 5 * x) for x in range(3)]
        t += [trade(mi, si + 7000 + 10 * x, 1, 0, bt=b_of(i) + 3500 + 5 * x) for x in range(3)]
        if i >= 1:
            t += [trade("M0", si + 7000 + x, 1, 0, bt=b_of(i) + 3500 + x // 2) for x in range(3)]
        mig = [complete(mi, si + 100), migration(mi, si + 100, pda_of(mi))]
        mig[0]["block_time"] = mig[1]["block_time"] = b_of(i) + 50
        cr = [create(mi, "C" + mi, slot=si - 500)]
        hours[hkey(i)] = {"trades": t, "creates": cr, "migrations": mig}
    for h, d in (extra or {}).items():
        for k, v in d.items():
            hours[h][k] += v
    return hours


def cfg_for(tmp_path, **kw):
    kw.setdefault("w0", hkey(0))
    kw.setdefault("n_blocks", 1)
    kw.setdefault("a_attempts", 1)
    return rc.Config(tmp_path / "walk", tmp_path / "counts", **kw)


def sph_ok(path):
    return 10000.0


@pytest.fixture()
def loose(monkeypatch):
    monkeypatch.setattr(rc, "MIN_LIVE_TOKENS", 2)
    monkeypatch.setattr(rc, "MIN_MARKS_WITH_LIVE", 5)


def run(cfg, through=None, **kw):
    kw.setdefault("sph_fn", sph_ok)
    kw.setdefault("canonical_fn", pda_of)
    return rc.run(cfg, through, **kw)


def lines(cfg):
    return rc.read_counts(Path(cfg.out_dir))


# ---- calendar stop instant, cap, allowlist ------------------------------------------------------------------------------------------------
def test_calendar_stop_and_cap(tmp_path):
    c = rc.Config(tmp_path, tmp_path / "o", "2026-10-18T00")
    assert rc.STOP_MODE == "calendar" and not hasattr(c, "stop_mode")
    assert ef.hour_key(c.calendar_stop_dt) == "2026-11-07T00"  # W0 + 20 d
    assert ef.hour_key(c.cap_dt) == "2026-11-08T00"  # W0 + 21 d is earlier than 11-16T01 - 1h
    assert ef.hour_key(c.allow_end()) == "2026-11-07T01"
    al = c.allowlist()
    assert al[0] == "2026-10-16T03" and al[-1] == "2026-11-07T00"
    c.validate()


def test_cap_is_walk_end_minus_horizon(tmp_path):
    c = rc.Config(tmp_path, tmp_path / "o", "2026-11-01T00", n_blocks=10)
    assert ef.hour_key(c.cap_dt) == "2026-11-16T00"  # 11-16T01 - 1h
    c.validate()
    with pytest.raises(rc.CountRefused, match="after the cap"):
        rc.Config(tmp_path, tmp_path / "o", "2026-11-01T00").validate()  # N=20 -> 11-21 > cap


def test_w0_before_h0_refused(tmp_path):
    with pytest.raises(rc.CountRefused, match="before H0"):
        rc.Config(tmp_path, tmp_path / "o", "2026-10-16T02").validate()


def test_through_outside_allowlist_refused(tmp_path):
    cfg = cfg_for(tmp_path)  # H0..E = 03 + 24 h
    make_walk(tmp_path / "walk", stream(3))
    with pytest.raises(rc.CountRefused, match="outside the allowlist"):
        run(cfg, hkey(26))
    refused = [json.loads(x) for x in (tmp_path / "counts" / rc.REFUSED).read_text().splitlines()]
    assert refused and "outside the allowlist" in refused[0]["reasons"][0]
    assert not (tmp_path / "counts" / rc.COUNTS).exists()


# ---- counts ---------------------------------------------------------------------------------------------------------------------------------
def test_counts_lines(tmp_path, loose):
    cfg = cfg_for(tmp_path)
    make_walk(tmp_path / "walk", stream(5))
    st = run(cfg, hkey(4))
    ls = lines(cfg)
    assert [ln["hour"] for ln in ls] == [hkey(i) for i in range(5)]
    assert [ln["live_tokens"] for ln in ls] == [0, 1, 2, 2, 2]
    assert [ln["decision_rows"] for ln in ls] == [0, 1, 2, 2, 2]
    t2 = b_of(2)
    assert ls[2]["mark_t"] == t2
    assert ls[2]["live_md5"] == rc.keys_md5([("M0", t2), ("M1", t2)])
    assert ls[0]["live_md5"] == rc.keys_md5([])
    assert ls[2]["grad_hour"] == hkey(1) and ls[2]["grad_members"] == 1 and ls[2]["grad_pda_match_share"] == 1.0 and ls[2]["grad_v0_band_share"] == 1.0
    assert ls[0]["grad_hour"] is None
    assert ls[1]["sph"] == 10000.0 and ls[1]["k_1.3"] == 4 and ls[1]["k_1.9"] == 5  # s = 0.36 s: 1.3/0.36 = 3.6 -> 4, 1.9/0.36 = 5.28 -> 5
    assert set(ls[0]["sha256"]) == set(rl.SUBS) and set(ls[0]["rows"]) == set(rl.SUBS) and ls[0]["rows"]["trades"] == 6
    assert ls[2]["null_txi_rows"] == 0 and ls[2]["mixed_null_txi_slots"] == 0
    assert st["status"] == "RUNNING" and st["appended"] == [hkey(i) for i in range(5)]
    day = st["per_day"]["2026-10-16"]
    assert day["hours"] == 5 and day["sph_mean"] == 10000.0 and day["k_1.3"] == [4]
    pin = json.loads((tmp_path / "counts" / rc.CONFIG).read_text())
    assert pin["w0"] == hkey(0) and pin["stop_mode"] == "calendar" and set(pin["code_sha256"]) == {"r4b_count.py", "r4b_layout.py"}


def test_k_rounding_tie_is_np_round(tmp_path):
    # 200 ms slots: 18000 sph, 1.3 s = 6.5 slots -> 6 (np.round / half to even); 18010 -> 7
    assert rc.k_pair(18000.0)[0] == 6
    assert rc.k_pair(18010.0)[0] == 7
    assert rc.k_pair(18000.0)[1] in (9, 10)


def test_idempotent_append(tmp_path, loose):
    cfg = cfg_for(tmp_path)
    make_walk(tmp_path / "walk", stream(6))
    run(cfg, hkey(2))
    before = (tmp_path / "counts" / rc.COUNTS).read_text()
    st = run(cfg, hkey(2))  # same hours again
    assert st["appended"] == [] and (tmp_path / "counts" / rc.COUNTS).read_text() == before
    st = run(cfg, hkey(5))  # extends; earlier lines were rebuilt identically (no drift) and not duplicated
    assert st["appended"] == [hkey(3), hkey(4), hkey(5)]
    text = (tmp_path / "counts" / rc.COUNTS).read_text()
    assert text.startswith(before) and len(text.splitlines()) == 6
    assert len({json.loads(x)["hour"] for x in text.splitlines()}) == 6


def test_drift_is_refused_not_overwritten(tmp_path, loose):
    cfg = cfg_for(tmp_path)
    make_walk(tmp_path / "walk", stream(4))
    run(cfg, hkey(1))
    p = tmp_path / "counts" / rc.COUNTS
    ls = [json.loads(x) for x in p.read_text().splitlines()]
    ls[1]["live_tokens"] = 99
    p.write_text("".join(rc.stable_json(x) + "\n" for x in ls))
    with pytest.raises(rc.CountRefused, match="drift"):
        run(cfg, hkey(3))
    assert json.loads(p.read_text().splitlines()[1])["live_tokens"] == 99 and len(p.read_text().splitlines()) == 2


def test_config_pin_mismatch(tmp_path, loose):
    make_walk(tmp_path / "walk", stream(3))
    run(cfg_for(tmp_path), hkey(1))
    with pytest.raises(rc.CountRefused, match="config.json differs"):
        run(cfg_for(tmp_path, a_attempts=2), hkey(2))


def test_calendar_stop_reached_and_aggregate(tmp_path, loose):
    cfg = cfg_for(tmp_path)  # E = W0 + 24 h = hour index 24
    make_walk(tmp_path / "walk", stream(26))
    st = run(cfg, hkey(23))
    assert st["status"] == "RUNNING" and st["stop"] == hkey(24) and st["attempts_total"] is None
    st = run(cfg, hkey(24))
    assert st["status"] == "STOP_REACHED" and st["aggregate_ok"] is True
    blocks = st["blocks"]
    assert list(blocks) == ["0"] and blocks["0"]["marks"] == 24 and blocks["0"]["marks_live10"] == 22  # live >= 10 per the patched MIN_LIVE_TOKENS... see below
    with pytest.raises(rc.CountRefused, match="outside the allowlist"):
        run(cfg, hkey(25))


def test_min_marks_with_live_refusal(tmp_path):
    cfg = cfg_for(tmp_path)
    make_walk(tmp_path / "walk", stream(26))
    with pytest.raises(rc.CountRefused, match="counted marks with >= 10 live tokens"):
        run(cfg, hkey(24))  # real constants: no mark has 10 live tokens
    assert not (tmp_path / "counts" / rc.COUNTS).exists()  # nothing partial


def test_pda_match_below_95_refused(tmp_path, loose):
    sc = stream(4)
    # 3 graduations in hour 0 whose migration event names another pool: 3 of 4+3 completes (hours 0..2 reported) mismatch
    extra_mig = []
    for x in range(3):
        m = f"B{x}"
        extra_mig += [complete(m, s_of(0) + 200 + x), migration(m, s_of(0) + 200 + x, "OTHER_" + m)]
    sc[hkey(0)]["migrations"] += extra_mig
    sc[hkey(0)]["creates"] += [create(f"B{x}") for x in range(3)]
    cfg = cfg_for(tmp_path)
    make_walk(tmp_path / "walk", sc)
    with pytest.raises(rc.CountRefused, match="PDA match"):
        run(cfg, hkey(2), final=True)
    assert not (tmp_path / "counts" / rc.COUNTS).exists()
    st = run(cfg, hkey(2))  # not final: the share is reported on the line, not refused
    assert lines(cfg)[1]["grad_pda_match_share"] == pytest.approx(1 / 4) and st["status"] == "RUNNING"


def test_unmeasurable_sph_refused(tmp_path, loose):
    make_walk(tmp_path / "walk", stream(3))
    cfg = cfg_for(tmp_path)
    bad = lambda p: None if hkey(1) in p.name else 10000.0  # noqa: E731
    with pytest.raises(rc.CountRefused, match=f"hour {hkey(1)}: slots-per-hour is unmeasurable"):
        run(cfg, hkey(2), sph_fn=bad)
    assert not (tmp_path / "counts" / rc.COUNTS).exists()


def test_mixed_null_txi_refused(tmp_path, loose):
    extra = {hkey(1): {"trades": [trade("M1", s_of(1) + 112, None, 0, bt=b_of(1) + 56), trade("M1", s_of(1) + 112, 3, 1, bt=b_of(1) + 56)]}}
    make_walk(tmp_path / "walk", stream(3, extra=extra))
    cfg = cfg_for(tmp_path)
    with pytest.raises(rc.CountRefused, match="mix null and non-null tx_index"):
        run(cfg, hkey(2))
    assert not (tmp_path / "counts" / rc.COUNTS).exists()
    # null tx_index alone (no mixing) is fine and is counted
    extra = {hkey(1): {"trades": [trade("M1", s_of(1) + 112, None, 0, bt=b_of(1) + 56), trade("M1", s_of(1) + 113, 3, 1, bt=b_of(1) + 56)]}}
    make_walk(tmp_path / "walk2", stream(3, extra=extra))
    cfg2 = rc.Config(tmp_path / "walk2", tmp_path / "counts2", hkey(0), n_blocks=1, a_attempts=1)
    run(cfg2, hkey(2))
    assert rc.read_counts(tmp_path / "counts2")[1]["null_txi_rows"] == 1


def test_null_v_refused(tmp_path, loose):
    extra = {hkey(1): {"trades": [trade("M1", s_of(1) + 150, 1, 0, vq=None, bt=b_of(1) + 75)]}}
    make_walk(tmp_path / "walk", stream(3, extra=extra))
    with pytest.raises(rc.CountRefused, match="null/missing virtual_quote_reserves"):
        run(cfg_for(tmp_path), hkey(2))


def test_unsealed_unverified_and_sha_hours_refused(tmp_path, loose):
    cfg = cfg_for(tmp_path)
    make_walk(tmp_path / "w1", stream(3), unsealed=(hkey(1),))
    with pytest.raises(rc.CountRefused, match=hkey(1)):
        run(rc.Config(tmp_path / "w1", tmp_path / "c1", hkey(0), n_blocks=1), hkey(2))
    make_walk(tmp_path / "w2", stream(3), no_verify=(hkey(2),))
    with pytest.raises(rc.CountRefused, match=hkey(2)):
        run(rc.Config(tmp_path / "w2", tmp_path / "c2", hkey(0), n_blocks=1), hkey(2))
    make_walk(tmp_path / "w3", stream(3), rows_delta={(hkey(1), "trades"): 1})
    with pytest.raises(rc.CountRefused, match="rows"):
        run(rc.Config(tmp_path / "w3", tmp_path / "c3", hkey(0), n_blocks=1), hkey(2))
    make_walk(tmp_path / "w4", stream(3))  # missing hour: only 3 hours exist, ask for 4
    with pytest.raises(rc.CountRefused):
        run(rc.Config(tmp_path / "w4", tmp_path / "c4", hkey(0), n_blocks=1), hkey(3))
    for c in ("c1", "c2", "c3", "c4"):
        assert not (tmp_path / c / rc.COUNTS).exists() and (tmp_path / c / rc.REFUSED).exists()


def test_sha_mismatch_refused(tmp_path, loose):
    make_walk(tmp_path / "walk", stream(3))
    f = tmp_path / "walk" / "trades" / f"trades-{hkey(1)}.jsonl.zst"
    zst(f, [json.dumps(trade("M1", s_of(1) + 111, 1, 0, bt=b_of(1) + 56))] + [x for x in []])  # different bytes, same name
    with pytest.raises(rc.CountRefused):
        run(cfg_for(tmp_path), hkey(2))


def test_date_without_decisions_refused():
    cfg = rc.Config(Path("/x"), Path("/y"), "2026-10-17T00", n_blocks=1)
    w0 = cfg.w0_epoch

    def ln(h, dec, gm):
        return {"hour": h, "mark_t": int(ef.hour_dt(h).timestamp()), "decision_rows": dec, "live_tokens": dec, "grad_hour": h, "grad_members": gm}

    hs = [ef.hour_key(ef.hour_dt("2026-10-17T00") + timedelta(hours=i)) for i in range(26)]
    ls = [ln(h, 0, 1 if h < "2026-10-18" else 0) for h in hs]
    assert rc.date_problems(ls, cfg, None) == ["UTC date 2026-10-17: 24 graduations (members) but 0 decision rows"]
    ls[5]["decision_rows"] = 1
    assert rc.date_problems(ls, cfg, None) == []
    # an incomplete date (no line for the next day's hour 00) is not judged
    assert rc.date_problems(ls[:24], cfg, None) == [] and w0 == ls[0]["mark_t"]


# ---- which columns are read ------------------------------------------------------------------------------------------------------------------
def test_structure_columns_exclude_outcome_columns():
    forbidden = {"side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "trader"}
    assert not forbidden & set(rl.TRADE_COLS_STRUCT)
    assert set(rl.TRADE_COLS_STRUCT) == {"venue", "mint", "pool", "slot", "signature", "event_index", "tx_index", "block_time", "virtual_quote_reserves"}


def test_garbage_in_forbidden_columns_does_not_matter(tmp_path, loose):
    """The structure run never parses side / sol / token / reserves / trader: corrupt those fields in every trade row and the counts do not change."""
    ref = stream(4)
    make_walk(tmp_path / "walk", ref)
    run(cfg_for(tmp_path), hkey(3))
    corrupt = stream(4)
    for d in corrupt.values():
        for r in d["trades"]:
            r.update({"side": "???", "sol_lamports": "not-a-number", "token_raw": [1], "quote_reserve": "x", "base_reserve": {"a": 1}, "trader": None})
    make_walk(tmp_path / "walk_c", corrupt)
    cfgc = rc.Config(tmp_path / "walk_c", tmp_path / "counts_c", hkey(0), n_blocks=1, a_attempts=1)
    run(cfgc, hkey(3))
    a = [{k: v for k, v in ln.items() if k not in ("sha256",)} for ln in lines(cfg_for(tmp_path))]
    b = [{k: v for k, v in ln.items() if k not in ("sha256",)} for ln in rc.read_counts(tmp_path / "counts_c")]
    assert a == b


def test_scratch_ut_columns_are_slim(tmp_path, loose):
    make_walk(tmp_path / "walk", stream(3))
    cfg = cfg_for(tmp_path)
    run(cfg, hkey(2), keep_scratch=True)
    cols = [r[0] for r in duckdb.connect().sql(f"describe select * from '{tmp_path}/counts/scratch/ut/{hkey(1)}.parquet'").fetchall()]
    assert cols == ["mid", "slot", "txi", "evi", "bt"]
    assert not (tmp_path / "counts" / "scratch" / "bond").exists() and not (tmp_path / "counts" / "scratch" / "mkt").exists()


def test_structure_membership_equals_full_layout(tmp_path):
    walk = make_walk(tmp_path / "w", scenario())
    full = rl.build(walk, "2026-10-16T03", "2026-10-16T05", tmp_path / "full", canonical_fn=pda_of)
    slim = rl.build(walk, "2026-10-16T03", "2026-10-16T05", tmp_path / "slim", canonical_fn=pda_of, structure_only=True)
    q = lambda d: duckdb.connect().sql(f"select * from '{d}/meta.parquet' order by mid").fetchall()  # noqa: E731
    assert q(tmp_path / "full") == q(tmp_path / "slim")
    for k in ("members", "pda_match", "v0_in_band", "null_txi_tie_groups", "mixed_null_txi_slots", "pda_match_share"):
        assert full[k] == slim[k]
    m0 = duckdb.connect().sql(f"select count(*) from '{tmp_path}/slim/m0.parquet'").fetchone()[0]
    assert m0 == full["completes"]


def test_cli_refusal_exit_code(tmp_path, capsys):
    rc_ = rc.main(["--walk-dir", str(tmp_path / "nowalk"), "--out-dir", str(tmp_path / "o"), "--w0", "2026-10-18T00"])
    assert rc_ == 2
    assert "REFUSED" in capsys.readouterr().err
    assert (tmp_path / "o" / rc.REFUSED).exists()




# ---- columns requested (QP fourth pass: no side / sol / token / reserves / trader; V null-ness only) --------------------------------------------
FORBIDDEN = {"side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "trader"}


def test_requested_columns_never_include_forbidden(tmp_path, loose, monkeypatch):
    """Capture every column list that r4b_layout hands to DuckDB's read_json while the count job runs: none may name a forbidden column."""
    seen = []
    orig = rl._read_json_sql

    def spy(path, cols):
        seen.append((Path(path).parent.name, set(cols)))
        return orig(path, cols)

    monkeypatch.setattr(rl, "_read_json_sql", spy)
    make_walk(tmp_path / "walk", stream(3))
    run(cfg_for(tmp_path), hkey(2))
    assert seen and {sub for sub, _ in seen} == {"trades", "creates", "migrations"}
    for sub, cols in seen:
        assert not cols & FORBIDDEN, (sub, cols & FORBIDDEN)
    assert {c for sub, cols in seen if sub == "trades" for c in cols} == set(rl.TRADE_COLS_STRUCT)


def test_count_module_has_no_attempts_mode_or_scoring():
    src = Path(rc.__file__).read_text(encoding="utf-8")
    code = [x for x in src.splitlines() if x.lstrip().startswith(("import ", "from ", "def ")) or " import " in x]
    for word in ("lightgbm", "r4b_score", "r4b_book", "r4b_features", "xs_book", "attempts", "score"):
        assert not [x for x in code if word in x and "measure_hour_sph" not in x and "cap_pick_score" not in x], word
    assert "TRADE_COLS," not in src and "rl.TRADE_COLS" not in src
    assert rc.STOP_MODE == "calendar" and not hasattr(rc, "STOP_MODES")


def test_v_null_ness_only_for_member_prints_and_v0_for_s0(tmp_path):
    """Structure mode: the only V value that survives is s0's (meta.v); the ut scratch has no V column and candidate V is dropped."""
    walk = make_walk(tmp_path / "w", scenario())
    rl.build(walk, "2026-10-16T03", "2026-10-16T05", tmp_path / "slim", canonical_fn=pda_of, structure_only=True)
    cols = [r[0] for r in duckdb.connect().sql(f"describe select * from '{tmp_path}/slim/ut/2026-10-16T03.parquet'").fetchall()]
    assert cols == ["mid", "slot", "txi", "evi", "bt"]
    v = duckdb.connect().sql(f"select v from '{tmp_path}/slim/meta.parquet' where mint = 'MA'").fetchone()[0]
    assert v == 17_600_000_007  # the s0 print's V0 (the earliest print, not the first row of the file)
    # a null V on a member print at/after s0 refuses, from the IS NULL boolean alone
    sc = scenario()
    sc["2026-10-16T03"]["trades"].append(trade("MA", S + 100 + 40, 3, 0, vq=None))
    walk2 = make_walk(tmp_path / "w2", sc)
    with pytest.raises(rl.LayoutRefused, match="null/missing virtual_quote_reserves"):
        rl.build(walk2, "2026-10-16T03", "2026-10-16T05", tmp_path / "slim2", canonical_fn=pda_of, structure_only=True)


# ---- owed on #674 (QP comment 6223913760): pending tail, V0-missing days, mixed null tx, create-row share ---------------------------------------
@pytest.fixture(autouse=True)
def _no_pending_unless_asked(request, monkeypatch):
    if "pending" not in request.node.name:
        monkeypatch.setattr(rc, "EXTRA_HOURS", 0)


def test_pending_tail_not_written_until_later_hours_loaded(tmp_path, loose):
    assert rc.EXTRA_HOURS == 2
    cfg = cfg_for(tmp_path)
    make_walk(tmp_path / "walk", stream(8), unsealed=(hkey(5), hkey(6), hkey(7)))
    st = run(cfg, hkey(4))  # hours 5.. are not sealed: no lookahead, the last two hours are pending
    assert [ln["hour"] for ln in lines(cfg)] == [hkey(0), hkey(1), hkey(2)] and st["pending_hours"] == [hkey(3), hkey(4)]
    assert all(ln["extra_hours_loaded"] == 2 for ln in lines(cfg))
    make_walk(tmp_path / "walk", stream(8), unsealed=(hkey(7),))  # hours 5 and 6 get sealed: they are the lookahead of hour 4
    st = run(cfg, hkey(4))
    assert [ln["hour"] for ln in lines(cfg)] == [hkey(i) for i in range(5)] and st["pending_hours"] == []
    # a one-shot run gives the same lines
    make_walk(tmp_path / "walk_b", stream(8), unsealed=(hkey(7),))
    cfgb = rc.Config(tmp_path / "walk_b", tmp_path / "counts_b", hkey(0), n_blocks=1, a_attempts=1)
    run(cfgb, hkey(4))
    assert (tmp_path / "counts_b" / rc.COUNTS).read_text() == (tmp_path / "counts" / rc.COUNTS).read_text()


def test_pending_final_allowlisted_hours_are_written_with_fewer_extras(tmp_path, loose):
    cfg = cfg_for(tmp_path)
    make_walk(tmp_path / "walk", stream(26))
    run(cfg, hkey(22))
    st = run(cfg, hkey(24))  # E = hour 24 is the last allowlisted hour: nothing later exists
    assert st["pending_hours"] == [] and st["status"] == "STOP_REACHED"
    ls = {ln["hour"]: ln for ln in lines(cfg)}
    assert ls[hkey(24)]["extra_hours_loaded"] == 0 and ls[hkey(23)]["extra_hours_loaded"] == 1 and ls[hkey(22)]["extra_hours_loaded"] == 2
    assert len(ls) == 25


def test_pending_unsealed_later_hour_is_not_a_refusal_of_earlier_hours(tmp_path, loose):
    make_walk(tmp_path / "walk", stream(5), unsealed=(hkey(4),))
    cfg = cfg_for(tmp_path)
    run(cfg, hkey(3))  # hours 0..3 sealed: 0..1 written, 2..3 pending
    assert [ln["hour"] for ln in lines(cfg)] == [hkey(0), hkey(1)]
    with pytest.raises(rc.CountRefused, match=hkey(4)):
        run(cfg, hkey(4))  # asking for the unsealed hour itself refuses


def test_v0_missing_days_over_refused(tmp_path, loose):
    sc = stream(3)
    for r in sc[hkey(1)]["trades"]:
        if r["mint"] == "M1":
            r.pop("virtual_quote_reserves", None)  # M1's s0 print has no V: V0 missing (non-member)
    make_walk(tmp_path / "walk", sc)
    with pytest.raises(rc.CountRefused, match="missing V0"):
        run(cfg_for(tmp_path), hkey(2))
    assert not (tmp_path / "counts" / rc.COUNTS).exists()


def test_no_create_share_constant_and_line_field(tmp_path, loose):
    assert rc.NO_CREATE_MAX_SHARE == 0.06  # QP ruling 1 (exploration days reach 0.0513)
    sc = stream(4)
    sc[hkey(1)]["creates"] = []  # M1 has no create row
    make_walk(tmp_path / "walk", sc)
    cfg = cfg_for(tmp_path)
    run(cfg, hkey(3))
    ls = lines(cfg)
    assert ls[2]["grad_hour"] == hkey(1) and ls[2]["grad_members"] == 1 and ls[2]["grad_members_no_create"] == 1
    assert ls[1]["grad_members_no_create"] == 0


def test_no_create_share_refused_per_complete_date():
    cfg = rc.Config(Path("/x"), Path("/y"), "2026-10-17T00", n_blocks=1)
    hs = [ef.hour_key(ef.hour_dt("2026-10-17T00") + timedelta(hours=i)) for i in range(25)]

    def ln(h, nocre):
        return {"hour": h, "mark_t": int(ef.hour_dt(h).timestamp()), "decision_rows": 3, "live_tokens": 3, "grad_hour": h, "grad_members": 100, "grad_members_no_create": nocre}

    ok = [ln(h, 0) for h in hs]
    ok[3]["grad_members_no_create"] = 144  # 144 / 2400 = 6.0%: not above
    assert rc.date_problems(ok, cfg, None) == []
    ok[4]["grad_members_no_create"] = 1  # 145 / 2400 > 6%
    probs = rc.date_problems(ok, cfg, None)
    assert len(probs) == 1 and "no create row" in probs[0] and "2026-10-17" in probs[0]


def test_mixed_null_txi_slots_over_run_refused_via_stats(tmp_path, loose):
    extra = {hkey(1): {"trades": [trade("M1", s_of(1) + 112, None, 0, bt=b_of(1) + 56), trade("M1", s_of(1) + 112, 3, 1, bt=b_of(1) + 56)]}}
    make_walk(tmp_path / "walk", stream(3, extra=extra))
    with pytest.raises(rc.CountRefused) as ei:
        run(cfg_for(tmp_path), hkey(2))
    assert any("over the read hours" in r for r in ei.value.reasons)


def test_incremental_run_to_e_equals_one_shot_run_to_e(tmp_path, loose):
    """Reviewer note 5: several incremental runs ending at E give a byte-identical counts.jsonl to one run to E."""
    make_walk(tmp_path / "walk", stream(26))
    inc = cfg_for(tmp_path)
    for through in (3, 9, 10, 17, 24):
        run(inc, hkey(through))
    make_walk(tmp_path / "walk_b", stream(26))
    one = rc.Config(tmp_path / "walk_b", tmp_path / "counts_b", hkey(0), n_blocks=1, a_attempts=1)
    run(one, hkey(24))
    a = (tmp_path / "counts" / rc.COUNTS).read_text()
    assert a == (tmp_path / "counts_b" / rc.COUNTS).read_text() and len(a.splitlines()) == 25


def test_no_table_other_than_s0t_m0_m_holds_v(tmp_path):
    """QP change A: V is masked at load. Every non-boolean V-like column of every table in the structure run is in s0t, m0 or m only; the hour table has vq_null."""
    walk = make_walk(tmp_path / "w", scenario())
    st = rl.build(walk, "2026-10-16T03", "2026-10-16T05", tmp_path / "slim", canonical_fn=pda_of, structure_only=True)
    tables = {c.split(".")[0] for c in st["v_columns"]}
    assert tables == {"s0t", "m0", "m"}, st["v_columns"]
    assert sorted(st["v_columns"]) == ["m.v", "m0.v0", "s0t.vq"]
    # the hour table `t` is gone with the connection; rebuild its load by hand to check its schema
    con = duckdb.connect()
    rl._load_trades(con, walk / "trades" / "trades-2026-10-16T03.jsonl.zst", 0, "x", rl.TRADE_COLS_STRUCT, True)
    cols = [r[0] for r in con.sql("describe t").fetchall()]
    assert "vq_null" in cols and "virtual_quote_reserves" not in cols
    assert con.sql("select count(*) from t where vq_null and venue = 'pumpswap'").fetchone()[0] == 0
    # full mode keeps V (the read tool needs it)
    full = rl.build(walk, "2026-10-16T03", "2026-10-16T05", tmp_path / "full", canonical_fn=pda_of)
    assert "v_columns" not in full


def test_pending_lookahead_is_bounded_at_the_allowlist_end(tmp_path, loose, monkeypatch):
    """QP change D: with hours E+1 and E+2 sealed, build must not load them; extra_hours_loaded comes from what was loaded."""
    make_walk(tmp_path / "walk", stream(28))  # hours 0..27 sealed; E = hour 24
    seen = {}
    orig = rl.build

    def spy(*a, **k):
        st = orig(*a, **k)
        seen["extras"] = st["trade_extra_hours"]
        seen["trade_end"] = k.get("trade_end")
        return st

    monkeypatch.setattr(rc.rl, "build", spy)
    cfg = cfg_for(tmp_path)
    run(cfg, hkey(24), keep_scratch=True)
    assert seen["extras"] == [] and seen["trade_end"] == hkey(25)
    ls = {ln["hour"]: ln for ln in lines(cfg)}
    assert ls[hkey(24)]["extra_hours_loaded"] == 0 and ls[hkey(23)]["extra_hours_loaded"] == 1 and ls[hkey(22)]["extra_hours_loaded"] == 2
    assert not (tmp_path / "counts" / "scratch" / "clock" / f"{hkey(25)}.parquet").exists()
    # a mid-run call: the lookahead past the requested hour is loaded (2 sealed hours), so the requested hour is written with 2
    cfg2 = rc.Config(tmp_path / "walk", tmp_path / "counts2", hkey(0), n_blocks=1, a_attempts=1)
    run(cfg2, hkey(10))
    assert seen["extras"] == [hkey(11), hkey(12)]
    assert rc.read_counts(tmp_path / "counts2")[-1]["hour"] == hkey(10)


def test_build_trade_end_bounds_extras(tmp_path):
    walk = make_walk(tmp_path / "w", scenario())
    st = rl.build(walk, "2026-10-16T03", "2026-10-16T04", tmp_path / "o1", canonical_fn=pda_of)
    assert st["trade_extra_hours"] == ["2026-10-16T04"]
    st = rl.build(walk, "2026-10-16T03", "2026-10-16T04", tmp_path / "o2", canonical_fn=pda_of, trade_end="2026-10-16T04")
    assert st["trade_extra_hours"] == []


def s0_extra_walk(root):
    """QP's repro: MX completes 2 s before hour 05 and its FIRST PDA print (s0) is in hour 05, a lookahead hour of the build range [03, 05)."""
    H = ["2026-10-16T03", "2026-10-16T04", "2026-10-16T05", "2026-10-16T06"]
    hours = {h: {"trades": [], "creates": [], "migrations": []} for h in H}
    hours[H[0]]["migrations"] += [complete("MA", s_of(0) + 100), migration("MA", s_of(0) + 100, pda_of("MA"))]
    hours[H[0]]["creates"].append(create("MA", "CA"))
    hours[H[0]]["trades"] += [trade("MA", s_of(0) + 110 + x, 1, 0, vq=17_600_000_001) for x in range(3)]
    ms = s_of(2) - 4
    hours[H[1]]["migrations"] += [complete("MX", ms), migration("MX", ms, pda_of("MX"))]
    hours[H[1]]["creates"].append(create("MX", "CX"))
    hours[H[2]]["trades"] += [trade("MX", s_of(2) + 10 + x, 1, 0, vq=17_600_000_009) for x in range(3)]
    for i, h in enumerate(H):
        if not hours[h]["trades"]:
            hours[h]["trades"].append(trade("FILL", s_of(i) + 5, 1, 0, pool="OTHER"))
    return make_walk(root, hours), H


def test_s0_in_lookahead_hour_structure_equals_full(tmp_path):
    """QP change C: V0 of a first print that falls in a lookahead hour is found by the structure re-read (all loaded trade hours)."""
    walk, H = s0_extra_walk(tmp_path / "w")
    out = {}
    for mode in (False, True):
        d = tmp_path / ("slim" if mode else "full")
        st = rl.build(walk, H[0], H[2], d, canonical_fn=pda_of, structure_only=mode)
        assert st["trade_extra_hours"] == [H[2], H[3]]
        out[mode] = (duckdb.connect().sql(f"select mint, s0, v0 from '{d}/m0.parquet' order by mint").fetchall(), st["members"], st["v0_missing"])
    assert out[True] == out[False]
    mx = [r for r in out[True][0] if r[0] == "MX"][0]
    assert mx[2] == 17_600_000_009 and out[True][2] == 0


def test_s0_reread_without_match_refuses_frn_order(tmp_path, monkeypatch):
    walk, H = s0_extra_walk(tmp_path / "w")
    orig = rl._read_json_sql

    def empty_for_reread(path, cols):
        if set(cols) == {"slot", "virtual_quote_reserves"}:
            return "(select 0::bigint as slot, 0::bigint as virtual_quote_reserves where false)"
        return orig(path, cols)

    monkeypatch.setattr(rl, "_read_json_sql", empty_for_reread)
    with pytest.raises(rl.LayoutRefused, match="frn_order"):
        rl.build(walk, H[0], H[2], tmp_path / "slim", canonical_fn=pda_of, structure_only=True)


def test_check_frn_extracts_by_regex_only():
    line = '{"v":2,"venue":"pumpswap","mint":"M","slot":123,"signature":"abc","event_index":4,"tx_index":null,"x":{"slot":9}}'
    assert rl._slot_sig_evi(line) == (123, "abc", 4)
    assert rl._slot_sig_evi('{"slot":5,"signature":"s","event_index":null}') == (5, "s", None)
    # a line that is not valid JSON anywhere else still yields the three fields: nothing else is parsed
    assert rl._slot_sig_evi('{"side":"???" "sol_lamports":not-json, "slot":7,"signature":"q","event_index":1}') == (7, "q", 1)
