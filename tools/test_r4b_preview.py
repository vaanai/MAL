"""r4b_preview: synthetic fixtures only (no declog, declog copy, tape, keeper dir, ledger, out/ or sidecar of any real book; every file is built in tmp dirs). Nothing here
says r4-b or r4bt-a2 makes money."""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from pathlib import Path

import numpy as np
import pytest

from tools import arena_paper as AP
from tools import r4b_preview as V
from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools import r4bt_read as RD
from tools import test_r4bt_price as T
from tools.arena_declog import DecisionLog
from tools.arena_paper import TEST_ENV, Ledger, register_bot

os.environ[TEST_ENV] = "1"
HR = 3_600_000
DAY = 86_400_000
CUT = V.CUTOFF_MS
SHA = "a" * 40  # the decision host's frozen_sha (start rows)
PIN = "c" * 40  # the preview's pin_commit
POOL = "ZQXPOOL9"
MINT = "MZQXPOOL9"
DRY = "r4bt-a2-dry-1113a"
DEC = "r4bt-a2-1120a"
P0 = ("2026-11-16", "2026-11-17", "2026-11-18")
REPO = Path(__file__).resolve().parent.parent
DEC_TEXT = REPO / "DEC" / "DEC-031-forward-paper-arena.md"


@pytest.fixture(autouse=True)
def _disk(monkeypatch):
    import collections

    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


# ------------------------------------------------------------------------------------------------------------------------------ fixtures
def mk_tape(d: Path, first: int, hours: int, pools=(POOL,), step=3000, skip=(), extra=None, pre_extra=None):
    """Hourly tape files from `first` (ms). `pre_extra`: {hour_name: [rows]} appended (rows of pools no entry selects)."""
    d.mkdir(parents=True, exist_ok=True)
    i = 0
    for h in range(hours):
        hs = first + h * HR
        name = P.hour_name(hs)
        lines = []
        for t in range(hs, hs + HR, step):
            for pool in pools:
                lines.append(T.trade_row(i, t, pool=pool))
                i += 1
            if (t // step) % 4 == 0:
                lines.append(T.trade_row(i + 100000, t, pool="PB", bonding=True))
        for src in (extra, pre_extra):
            if src and name in src:
                lines.extend(src[name])
        if name in skip:
            continue
        (d / f"tape-{name}.jsonl").write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in lines))


def entry_body(T0, pool=POOL, rank=0, mint=MINT, **kw):
    return {"kind": "entry", "order_id": f"{mint}:{T0}", "pool": pool, "meta": {"mint": mint, "v0": T.V, "mbt": 0, "s0": 1}, "stake_sol": 0.25, "decision_slot": 10 ** 9,
            "t_mark": T0, "rank": rank, "score": 0.5, **kw}


def exit_body(T0, T1, reason="rank", mint=MINT):
    return {"kind": "exit", "order_id": f"{mint}:{T0}", "mint": mint, "reason": reason, "t_mark": T1, "decision_slot": 10 ** 9}


def mk_declog(root: Path, cid: str, rows):
    dl = DecisionLog(root / cid)
    dl.append(V.ARM, CUT - 1000, {"kind": "start", "book": "r4b", "exp": "EXP-027", "mode": "dry", "frozen_sha": SHA, "change_id": cid})
    for ms, body in rows:
        dl.append(V.ARM, ms, body)
    return root / cid / "decisions" / V.ARM


def mk_manifest(tmp: Path, *, dry_stop=None, decision=False, first_live=None, d=0, name="manifest.json"):
    series = [{"change_id": DRY, "role": "dry", "frozen_sha": SHA, "dry_stop_ms": dry_stop}]
    if decision:
        series.append({"change_id": DEC, "role": "decision", "frozen_sha": SHA, "first_live_mark_s": first_live})
    m = {"pin_commit": PIN, "declared_instant": "2026-11-17T01:00:00Z", "d": d, "d_measurement_sha256": "d" * 64, "d_window": "[2026-11-15T00:00Z, 2026-11-16T00:00Z)",
         "series": series, "p0_days": list(P0)}
    p = tmp / name
    p.write_text(json.dumps(m, sort_keys=True))
    return p, G.sha256_file(p)


def world(tmp: Path, dry_rows=None, *, dec_rows=None, hours=9, dry_stop=None, first_live=None, pre_extra=None, tape_first=V.TAPE_FLOOR_MS, pools=(POOL,), skip=()):
    """A preview world: declog copy, keeper dir, manifest. Default decisions: one entry at mark CUTOFF exited one hour later."""
    if dry_rows is None:
        dry_rows = [(CUT + 1200, entry_body(V.CUTOFF_S)), (CUT + HR + 1200, exit_body(V.CUTOFF_S, V.CUTOFF_S + 3600))]
    copy, keep, out = tmp / "copy", tmp / "keep", tmp / "out"
    mk_declog(copy, DRY, dry_rows)
    if dec_rows is not None:
        mk_declog(copy, DEC, dec_rows)
    mk_tape(keep, tape_first, hours, pools=pools, pre_extra=pre_extra, skip=skip)
    mp, msha = mk_manifest(tmp, dry_stop=dry_stop, decision=dec_rows is not None, first_live=first_live)
    as_of = CUT + 2 * HR  # 03:00Z
    return dict(roots=(copy, keep, out), manifest=str(mp), manifest_sha256=msha, as_of=G.iso(as_of), as_of_ms=as_of, now_ms=as_of + DAY, code_state_fn=lambda: (PIN, False))


def run_world(w, **over):
    kw = dict(roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"])
    kw.update(over)
    return V.run(w["manifest"], w["manifest_sha256"], w["as_of"], **kw)


def out_dir(w):
    return Path(w["roots"][2]) / "as-of-20261116T03Z"


def outputs(w) -> dict:
    d = out_dir(w)
    return {n: (d / n).read_bytes() for n in ("preview.json", "preview.md", "console.txt")}


# ------------------------------------------------------------------------------------------------------------------------------ constants and label
def test_label_equals_the_dec_text_of_amendment_31_item_1():
    text = DEC_TEXT.read_text()
    i = text.index("Label on every preview file and Console line")
    m = re.search(r"`(FORWARD PAPER PREVIEW[^`]*)`", text[i:])
    assert m and m.group(1).replace("<instant>", "{declared}") == V.LABEL
    m0 = re.search(r"P0-day rows add: `([^`]*)`", text[i:])
    assert m0 and m0.group(1) == V.P0_LABEL


def test_hard_coded_constants():
    assert V.STAKES == (0.10, 0.25, 0.5, 1.0) and V.K == 10 and V.CUTOFF_S == 1794790800 and V.TAPE_FLOOR == 1794780000 and V.E1_FIXED == 1_920_110
    assert G.iso(V.CUTOFF_S * 1000) == "2026-11-16T01:00:00Z" and G.iso(V.TAPE_FLOOR * 1000) == "2026-11-15T22:00:00Z" and V.ARM == "r4b-exp027"
    assert V.CUTOFF_S - V.TAPE_FLOOR == 3 * 3600
    assert V.DECLOG_COPY_ROOT == "/data2/mal/r4b-preview/declog-copy" and V.TAPE_DIR == "/data2/mal/scratch/r4b-price-keep" and V.OUT_ROOT == "/data2/mal/r4b-preview/out"
    assert V.E1_LABEL == "E1 is a live-minus-sim gap estimate scaled from 0.05 SOL trips; its growth beyond proportional at 0.5 and 1.0 SOL is unmeasured."


def test_config_is_the_registered_pin_with_only_the_stakes_replaced(tmp_path):
    w = world(tmp_path)
    cap = {}
    orig = V.replay

    def spy(*a, **k):
        r = orig(*a, **k)
        cap["cfg"] = r["cfg"]
        return r

    V.replay, saved = spy, V.replay
    try:
        assert run_world(w) == 0
    finally:
        V.replay = saved
    reg = P.pinned_config(feed_delay_slots=0, k_rounding="ceil")
    assert cap["cfg"].as_dict() == reg.as_dict() and cap["cfg"].stakes_sol == V.STAKES and reg.stakes_sol == AP.STAKES_SOL
    assert {k for k in vars(reg) if getattr(reg, k) != getattr(cap["cfg"], k)} == {"stakes_sol"}


@pytest.mark.parametrize("cid,ok", [("r4bt-a2-dry-1113a", True), ("r4bt-a2-1120a", True), ("r4bt-a3-1204b", True), ("r4bt-a3-dry-1203a", True), ("dry-long-1101a", False),
                                    ("r4bt-a1-1107a", False), ("r4bt-a2-dry-1113b", False), ("../x", False), (None, False), ("r4bt-a2-11200", False)])
def test_change_id_allowlist(cid, ok):
    if ok:
        assert V.check_change_id(cid) == cid
    else:
        with pytest.raises(G.Refused):
            V.check_change_id(cid)


# ------------------------------------------------------------------------------------------------------------------------------ guards
def test_roots_guards(tmp_path):
    ok = (tmp_path / "c", tmp_path / "k", tmp_path / "o")
    env = V.make_env(ok)
    for bad in (tmp_path / "x", Path("/data/mal/arena/ledger"), Path("/var/lib/mal/x"), tmp_path / "k" / "sub", tmp_path / "c"):
        with pytest.raises(G.Refused):
            env.check_write(bad)
    for out in ("/data/mal/arena/x", "/var/lib/mal-live/x", "/home/claude/mal-data/r4b-dry/x", "/data2/mal/scratch/r4b-price-keep/o", "/data2/mal/r4b-preview/declog-copy/o",
                str(tmp_path / "c" / "o"), str(tmp_path / "k")):
        with pytest.raises(G.Refused):
            V.make_env((tmp_path / "c", tmp_path / "k", out))
    for droot in (tmp_path / "x" / "out", tmp_path / "sidecar.jsonl", tmp_path / "host.log", tmp_path / "m1-start.txt"):
        with pytest.raises(G.Refused):
            V.make_env((droot, tmp_path / "k", tmp_path / "o"))
    with pytest.raises(G.Refused):
        V.make_env(("/data/mal/arena/x", tmp_path / "k", tmp_path / "o"))
    d = tmp_path / "c" / "r4bt-a2-dry-1113a" / "out"
    d.mkdir(parents=True)
    with pytest.raises(G.Refused):
        env.check_read_declog(d / "a.jsonl")
    with pytest.raises(G.Refused):
        env.check_read_declog(tmp_path / "c" / "r4bt-a2-dry-1113a" / "sidecar.jsonl")
    with pytest.raises(G.Refused):
        env.check_read_declog(tmp_path / "elsewhere" / "x.jsonl")


def test_the_constants_default_roots_are_what_the_ruling_names():
    env = V.make_env()
    assert (str(env.declog_root), str(env.tape_dir), str(env.out_root)) == (V.DECLOG_COPY_ROOT, V.TAPE_DIR, V.OUT_ROOT)


def test_tape_hour_before_the_floor_is_never_opened_and_is_refused(tmp_path, monkeypatch):
    sentinel = V.TAPE_FLOOR_MS - HR  # 2026-11-15T21
    w = world(tmp_path)
    (Path(w["roots"][1]) / f"tape-{P.hour_name(sentinel)}.jsonl").write_text("{not json\n")
    opened = []
    real = P.iter_lines

    def rec(p):
        opened.append(Path(p).name)
        return real(p)

    monkeypatch.setattr(P, "iter_lines", rec)
    assert run_world(w) == 0
    assert opened and not any(P.hour_name(sentinel) in n for n in opened) and any(P.hour_name(V.TAPE_FLOOR_MS) in n for n in opened)
    env = V.make_env(w["roots"])
    with pytest.raises(G.Refused, match="before TAPE_FLOOR"):
        env.check_read_tape(Path(w["roots"][1]) / f"tape-{P.hour_name(sentinel)}.jsonl")
    with pytest.raises(G.Refused):
        V.replay([], [], [Path(w["roots"][1]) / f"tape-{P.hour_name(sentinel)}.jsonl"], 0, tmp_path / "wk", pin_commit=PIN, code_state_fn=w["code_state_fn"], env=env)


# ------------------------------------------------------------------------------------------------------------------------------ declog copy reader
def test_read_chain_prefix_stops_at_as_of_and_never_locks(tmp_path, monkeypatch):
    w = world(tmp_path)
    env = V.make_env(w["roots"])

    def boom(*a, **k):
        raise AssertionError("flock must never be called")

    monkeypatch.setattr(fcntl, "flock", boom)
    rows, shas = V.read_chain_prefix(env, DRY, CUT + HR)
    assert [r["kind"] for r in rows] == ["start", "entry"] and list(shas) == ["2026-11-16"]
    rows2, shas2 = V.read_chain_prefix(env, DRY, CUT + 3 * HR)
    assert [r["kind"] for r in rows2] == ["start", "entry", "exit"] and shas2 != shas
    f = Path(w["roots"][0]) / DRY / "decisions" / V.ARM / "2026-11-16.jsonl"
    assert shas2["2026-11-16"] == __import__("hashlib").sha256(f.read_bytes()).hexdigest()


def test_torn_tail_and_tail_only_inconsistency_are_tolerated_but_a_mid_break_is_untrusted(tmp_path):
    w = world(tmp_path)
    env = V.make_env(w["roots"])
    f = Path(w["roots"][0]) / DRY / "decisions" / V.ARM / "2026-11-16.jsonl"
    good = f.read_bytes()
    f.write_bytes(good + b'{"seq": 3, "arm": "r4b-exp027", "ms": 1, "pr')  # a torn last line (no newline): cut, never an error
    rows, _ = V.read_chain_prefix(env, DRY, CUT + 3 * HR)
    assert len(rows) == 3
    lines = good.split(b"\n")[:-1]
    f.write_bytes(b"\n".join(lines[:-1]) + b"\n" + lines[-1].replace(b'"rank"', b'"Rank"') + b"\n")  # the very last complete line is inconsistent
    rows, _ = V.read_chain_prefix(env, DRY, CUT + 3 * HR)
    assert len(rows) == 2
    f.write_bytes(lines[0] + b"\n" + lines[1].replace(b'"rank"', b'"Rank"') + b"\n" + lines[2] + b"\n")  # a break with a line after it
    with pytest.raises(V.Untrusted):
        V.read_chain_prefix(env, DRY, CUT + 3 * HR)
    assert V.run(w["manifest"], w["manifest_sha256"], w["as_of"], roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"]) == 3
    assert not out_dir(w).exists()


# ------------------------------------------------------------------------------------------------------------------------------ select and merge
def test_select_drops_pre_cutoff_rows_without_counting_and_keeps_exits_of_selected_only(tmp_path):
    rows = [{"kind": "entry", "ms": CUT - 5000, "t_mark": V.CUTOFF_S - 3600, "order_id": f"{MINT}:{V.CUTOFF_S - 3600}", "pool": POOL},
            {"kind": "exit", "ms": CUT + 2 * HR - 1, "order_id": f"{MINT}:{V.CUTOFF_S - 3600}", "t_mark": V.CUTOFF_S},
            {"kind": "entry", "ms": CUT + 1200, "t_mark": V.CUTOFF_S, "order_id": f"{MINT}:{V.CUTOFF_S}", "pool": POOL},
            {"kind": "exit", "ms": CUT + HR, "order_id": f"{MINT}:{V.CUTOFF_S}", "t_mark": V.CUTOFF_S + 3600},
            {"kind": "entry", "ms": CUT + 9 * HR, "t_mark": V.CUTOFF_S + 3600, "order_id": f"{MINT}:{V.CUTOFF_S + 3600}", "pool": POOL}]
    e, x = V.select(rows, DRY, CUT + 2 * HR)
    assert [r["t_mark"] for r in e] == [V.CUTOFF_S] and [r["order_id"] for r in x] == [f"{MINT}:{V.CUTOFF_S}"]
    with pytest.raises(V.Untrusted):
        V.select([dict(rows[2], order_id=f"{MINT}:{V.CUTOFF_S + 1}")], DRY, CUT + HR)
    with pytest.raises(V.Untrusted):
        V.select([dict(rows[2], pool=None)], DRY, CUT + HR)


def test_pre_cutoff_entry_never_reaches_buy_and_its_exit_is_invisible(tmp_path, monkeypatch):
    early = (CUT - 5000, entry_body(V.CUTOFF_S - 3600))
    early_exit = (CUT + 30 * 60_000, exit_body(V.CUTOFF_S - 3600, V.CUTOFF_S + 1800))
    calls = []
    real = V.ArenaEngine.buy
    monkeypatch.setattr(V.ArenaEngine, "buy", lambda self, *a, **k: (calls.append(a), real(self, *a, **k))[1])
    w = world(tmp_path / "a", dry_rows=[early, early_exit])
    assert run_world(w) == 0 and calls == []
    a = outputs(w)
    ja = json.loads(a["preview.json"])
    assert ja["trades"] == [] and ja["counts"]["entries_selected_counted"] == 0
    w2 = world(tmp_path / "b", dry_rows=[])
    assert run_world(w2) == 0
    b = outputs(w2)
    assert V_strip(a["preview.json"]) == V_strip(b["preview.json"]) and a["preview.md"] == b["preview.md"] and a["console.txt"] == b["console.txt"]


def V_strip(js: bytes) -> dict:
    j = json.loads(js)
    j.pop("inputs")  # the prefix shas cover the chain bytes, pre-cut-off rows included
    return j


def test_merge_series_once_per_mark_and_catch_up_never_counted():
    m = {"series": [{"change_id": DRY, "role": "dry", "dry_stop_ms": (V.CUTOFF_S + 7200) * 1000}, {"change_id": DEC, "role": "decision", "first_live_mark_s": V.CUTOFF_S + 10800}]}

    def e(cid, T0, pool="p"):
        return {"_cid": cid, "t_mark": T0, "pool": pool + str(T0), "ms": T0 * 1000 + cid.__len__()}

    ent = {DRY: [e(DRY, V.CUTOFF_S), e(DRY, V.CUTOFF_S + 3600), e(DRY, V.CUTOFF_S + 7200)],
           DEC: [e(DEC, V.CUTOFF_S + 3600), e(DEC, V.CUTOFF_S + 10800), e(DEC, V.CUTOFF_S + 14400)]}
    out = V.merge_series(ent, m)
    lab = {(x["_cid"], x["t_mark"] - V.CUTOFF_S): (x["_label"], x["_counted"]) for x in out}
    assert lab[(DRY, 0)] == ("dry", True) and lab[(DRY, 3600)] == ("dry", True) and lab[(DRY, 7200)] == ("dry after stop", False)
    assert lab[(DEC, 3600)] == ("catch-up replay", False) and lab[(DEC, 10800)] == ("decision", True) and lab[(DEC, 14400)] == ("decision", True)
    both = {DRY: [e(DRY, V.CUTOFF_S)], DEC: [e(DEC, V.CUTOFF_S)]}  # the same counted mark in both series
    with pytest.raises(V.Untrusted):
        V.merge_series(both, {"series": [{"change_id": DRY, "role": "dry", "dry_stop_ms": None}, {"change_id": DEC, "role": "decision", "first_live_mark_s": V.CUTOFF_S}]})


def test_overlap_between_hosts_is_exit_3_and_catch_up_is_absent_from_totals(tmp_path):
    T0, T1, T2 = V.CUTOFF_S, V.CUTOFF_S + 3600, V.CUTOFF_S + 1800
    dry = [(CUT + 1200, entry_body(T0)), (CUT + HR + 1200, exit_body(T0, T1))]
    # the decision host: a catch-up mark (T2 < first_live) and the same mark T0 as the dry host's
    dec = [(CUT + 600_000, entry_body(T2, mint="MX")), (CUT + HR + 600_000, exit_body(T2, T2 + 1800, mint="MX"))]
    w = world(tmp_path / "ok", dry_rows=dry, dec_rows=dec, first_live=V.CUTOFF_S + 7200)
    assert run_world(w) == 0
    j = json.loads(outputs(w)["preview.json"])
    roles = {t["role"]: t for t in j["trades"]}
    assert set(roles) == {"dry", "catch-up replay"} and roles["catch-up replay"]["counted"] is False and roles["dry"]["counted"] is True
    assert j["counts"]["entries_selected_counted"] == 1 and j["counts"]["entries_outside_totals"] == 1
    for stake, modes in j["cells"].items():
        for mode, cs in modes.items():
            assert all(c["rent0"]["n"] == 1 for c in cs.values()), "the catch-up series is in no total or statistic"
    dec2 = [(CUT + 600_000, entry_body(T0, mint="MX")), (CUT + HR + 600_000, exit_body(T0, T1, mint="MX"))]
    w2 = world(tmp_path / "bad", dry_rows=dry, dec_rows=dec2, first_live=V.CUTOFF_S)
    assert run_world(w2) == 3 and not out_dir(w2).exists()


# ------------------------------------------------------------------------------------------------------------------------------ end to end output
def test_end_to_end_output_labels_p0_no_read_words_and_no_pool_ids(tmp_path):
    w = world(tmp_path)
    assert run_world(w) == 0
    o = outputs(w)
    label = V.label_for("2026-11-17T01:00:00Z")
    j = json.loads(o["preview.json"])
    assert j["label"] == label and j["e1_label"] == V.E1_LABEL and j["d_slots"] == 0 and j["d_measurement_sha256"] == "d" * 64 and j["d_window"].startswith("[2026-11-15")
    for name in ("preview.md", "console.txt"):
        lines = o[name].decode().splitlines()
        assert lines and all(ln.startswith(label) for ln in lines), name
    md = o["preview.md"].decode()
    assert V.E1_LABEL in md and "e1 fixed" in md and "e1 proportional" in md
    assert any(V.P0_LABEL in ln for ln in o["console.txt"].decode().splitlines()) and V.P0_LABEL in md
    t = j["trades"][0]
    assert t["p0"] is True and t["p0_label"] == V.P0_LABEL and t["status"] == "priced" and t["rank"] == 0 and t["series"] == DRY and t["key"] == f"{DRY}-00001"
    assert t["entry_mark_utc"] == "2026-11-16T01:00:00Z" and t["exit_mark_utc"] == "2026-11-16T02:00:00Z" and t["hold_hours"] == 1.0 and t["exit_reason"] == "rank"
    assert set(t["rungs"]) == {"0.1", "0.25", "0.5", "1"}
    # no pool or mint string in any byte; no forbidden key name anywhere
    blob = b"".join(o.values())
    for s in (POOL, MINT, "sigZQX", "ZQXPOOL"):
        assert s.encode() not in blob

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                assert k not in V.OPEN_FIELD_DENY, k
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(j)
    # no read wording outside the label itself
    text = b"".join(o.values()).decode().replace(label, "")
    low = text.lower()
    for word in ("pass", "fail", "verdict", "ci90", "gate"):
        assert word not in low, word
    assert "ci90_lo_sol" not in text and "boot" not in low
    # no wall clock in the deterministic files, nothing random
    assert "engine_id" not in text


def test_four_rungs_both_e1_columns_both_fail_models_rent_charged_beside(tmp_path):
    w = world(tmp_path)
    assert run_world(w) == 0
    j = json.loads(outputs(w)["preview.json"])
    assert set(j["cells"]) == {"0.1", "0.25", "0.5", "1"}
    want = {f"{m}_{lat}_{rule}" for m in ("flat", "press") for lat in ("1p3", "1p9") for rule in ("total", "sc")}
    for s, modes in j["cells"].items():
        assert set(modes) == {"proportional", "fixed"}
        for mode, cs in modes.items():
            assert set(cs) == want
            for k, c in cs.items():
                assert set(c["rent0"]) >= {"n", "mean_sol", "total_sol", "days", "days_pos", "daily", "ex_top3_sol", "ex_best_day_sol"} and "rent_charged" in c
                assert "ci90_lo_sol" not in c["rent0"]
    assert j["e1_headline"] == "proportional" and j["e1_fixed_lamports"] == 1_920_110
    for s in ("0.1", "0.25", "0.5", "1"):
        assert set(j["cost_drag_pct_of_stake"][s]) == {f"{lat}_{rule}" for lat in ("1p3", "1p9") for rule in ("total", "sc")}
        assert j["peak_open"][s]["nominal_k_x_stake_sol"] == 10 * float(s) and j["peak_open"][s]["observed_max_concurrent"] == 1
    rent = [c for c in j["cells"]["0.25"]["proportional"].values() if c["rent0"]["n"]]
    assert rent and all(c["rent_charged"]["n"] == c["rent0"]["n"] for c in rent)


def test_idempotent_rerun_verify_and_first_output_o_excl(tmp_path, capsys):
    w = world(tmp_path)
    assert run_world(w) == 0
    first = outputs(w)
    fo = Path(w["roots"][2]) / "first-output.json"
    fo_bytes = fo.read_bytes()
    assert json.loads(fo_bytes)["as_of"] == w["as_of"] and json.loads(fo_bytes)["pin_commit"] == PIN
    time.sleep(1.1)
    capsys.readouterr()
    assert run_world(w) == 0
    assert outputs(w) == first and fo.read_bytes() == fo_bytes
    assert run_world(w, verify=True) == 0 and capsys.readouterr().out.strip().endswith("EQUAL")
    (out_dir(w) / "preview.json").unlink()
    (out_dir(w) / "preview.json").write_bytes(b"{}")
    assert run_world(w, verify=True) == 1 and capsys.readouterr().out.strip().endswith("DIFF")
    assert run_world(w) == 2, "an output with other bytes is never overwritten"
    assert not [p for p in Path(w["roots"][2]).rglob(".work-*")], "the temp ledger is deleted"


def test_every_refusal_writes_nothing(tmp_path):
    w = world(tmp_path)
    out = Path(w["roots"][2])
    assert run_world(w, code_state_fn=lambda: ("b" * 40, False)) == 2
    assert run_world(w, code_state_fn=lambda: (PIN, True)) == 2
    assert V.run(w["manifest"], "0" * 64, w["as_of"], roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"]) == 2
    assert V.run(w["manifest"], w["manifest_sha256"], "2026-11-16T00:59:59Z", roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"]) == 2
    assert V.run(w["manifest"], w["manifest_sha256"], w["as_of"], roots=w["roots"], now_ms=w["as_of_ms"] - 1, code_state_fn=w["code_state_fn"]) == 2
    assert V.run(w["manifest"], w["manifest_sha256"], w["as_of"], roots=w["roots"], now_ms=w["as_of_ms"] + HR, code_state_fn=w["code_state_fn"]) == 2  # last hour not closed
    assert not out.exists() or not any(p.is_file() for p in out.rglob("*"))
    w2 = world(tmp_path / "m", skip=(P.hour_name(V.TAPE_FLOOR_MS + 3 * HR),))
    assert run_world(w2) == 2


def test_manifest_requires_d_and_pinned_fields(tmp_path):
    mp, sha = mk_manifest(tmp_path)
    m = json.loads(mp.read_text())
    for patch in ({"d": None}, {"d": True}, {"d": -1}, {"d_measurement_sha256": "zz"}, {"d_window": ""}, {"pin_commit": "short"}, {"p0_days": ["2026-11-19"]},
                  {"series": [{"change_id": "dry-long-1101a", "role": "dry", "frozen_sha": SHA}]}):
        bad = dict(m, **patch)
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(bad))
        with pytest.raises(G.Refused):
            V.load_manifest(p, G.sha256_file(p))
    nod = {k: v for k, v in m.items() if k != "d"}
    p = tmp_path / "nod.json"
    p.write_text(json.dumps(nod))
    with pytest.raises(G.Refused, match="no default"):
        V.load_manifest(p, G.sha256_file(p))


def test_no_exit_row_and_open_at_as_of_are_listed_counted_and_never_priced(tmp_path):
    T0 = V.CUTOFF_S
    rows = [(CUT + 1200, entry_body(T0)), (CUT + HR + 1200, exit_body(T0, T0 + 3600)),
            (CUT + 1800_000, entry_body(T0 + 1800, mint="MA")), (CUT + 2400_000, entry_body(T0 + 2400, mint="MB"))]
    w = world(tmp_path, dry_rows=rows, dry_stop=(T0 + 2400) * 1000 - 1)  # the dry host stopped before its last mark: its entries have no exit row, final
    # the stop is before as_of: both unexited entries are "no exit row" (the second is after the stop but still an entry row; it is outside the dry totals)
    assert run_world(w) == 0
    j = json.loads(outputs(w)["preview.json"])
    st = {t["entry_mark_utc"]: t["status"] for t in j["trades"]}
    assert j["counts"]["no_exit_row"] + j["counts"]["open_at_as_of"] == 2 and j["counts"]["no_exit_row"] == 2
    assert sorted(st.values()) == ["no_exit_row", "no_exit_row", "priced"]
    assert all(not t["rungs"] for t in j["trades"] if t["status"] != "priced")
    w2 = world(tmp_path / "run", dry_rows=rows, dry_stop=None)
    assert run_world(w2) == 0
    j2 = json.loads(outputs(w2)["preview.json"])
    assert j2["counts"]["open_at_as_of"] == 2 and j2["counts"]["no_exit_row"] == 0
    for modes in j2["cells"].values():
        assert all(c["rent0"]["n"] <= 1 for cs in modes.values() for c in cs.values())


def test_pre_roll_hours_that_touch_no_selected_pool_change_no_byte(tmp_path):
    noise = {P.hour_name(V.TAPE_FLOOR_MS + k * HR): [dict(T.trade_row(900_000 + k, V.TAPE_FLOOR_MS + k * HR + 3000 * (j + 1), pool="OTHER"), signature=f"o{k}{j}") for j in range(5)]
             for k in range(3)}
    a = world(tmp_path / "a")
    b = world(tmp_path / "b", pre_extra=noise)
    assert run_world(a) == 0 and run_world(b) == 0
    oa, ob = outputs(a), outputs(b)
    assert oa == ob


def test_snapshot_monotonicity_trades_priced_at_a_are_identical_at_b(tmp_path):
    T0 = V.CUTOFF_S
    rows = [(CUT + 1200, entry_body(T0)), (CUT + HR + 1200, exit_body(T0, T0 + 3600)),
            (CUT + 2 * HR + 1200, entry_body(T0 + 7200, mint="MB")), (CUT + 3 * HR + 1200, exit_body(T0 + 7200, T0 + 10800, mint="MB"))]
    w = world(tmp_path, dry_rows=rows, hours=10)
    assert run_world(w) == 0
    ja = json.loads(outputs(w)["preview.json"])
    w2 = dict(w, as_of=G.iso(CUT + 4 * HR), now_ms=CUT + 4 * HR + DAY)
    assert V.run(w2["manifest"], w2["manifest_sha256"], w2["as_of"], roots=w2["roots"], now_ms=w2["now_ms"], code_state_fn=w2["code_state_fn"]) == 0
    jb = json.loads((Path(w["roots"][2]) / "as-of-20261116T05Z" / "preview.json").read_bytes())
    pa = {t["key"]: t for t in ja["trades"] if t["status"] == "priced"}
    pb = {t["key"]: t for t in jb["trades"] if t["status"] == "priced"}
    assert pa and set(pa) < set(pb) and all(pb[k] == pa[k] for k in pa)
    assert len(pb) == 2 and len(pa) == 1


# ------------------------------------------------------------------------------------------------------------------------------ pricing arithmetic
def priced_rows(tmp_path, rows=None):
    w = world(tmp_path, dry_rows=rows)
    env = V.make_env(w["roots"])
    m = V.load_manifest(w["manifest"], w["manifest_sha256"])
    box = {}
    orig = V.build_report

    def spy(**kw):
        box["res"] = kw["res"]
        return orig(**kw)

    V.build_report, saved = spy, V.build_report
    try:
        V.compute(m, w["as_of_ms"], env, now_ms=w["now_ms"], code_state_fn=w["code_state_fn"])
    finally:
        V.build_report = saved
    return box["res"]


def test_reconstruct_state_reproduces_every_stored_pnl_nofail_and_e1_identities(tmp_path):
    res = priced_rows(tmp_path)
    cfg = res["cfg"]
    trades = [r for r in res["rows"] if r["kind"] == "trade"]
    assert len(trades) == 4
    n_filled = 0
    for r in trades:
        st = V.reconstruct_state(r["calc"])
        stake = float(round(r["stake_sol"] * 1e9))
        for lat in ("1p3", "1p9"):
            for rule in V.RULES:
                got = AP.price_leg(r["prog"], stake, st, V.LAT_F[lat], cfg, rule)
                assert got["pnl"] == r["calc"][lat]["by_rule"][rule]["pnl_nofail"] and got["state"] == r["calc"][lat]["by_rule"][rule]["state"]
                fx = V.e1_fixed_leg(r["prog"], stake, st, V.LAT_F[lat], cfg, rule)
                if got["state"] == "filled":
                    n_filled += 1
                    if r["stake_sol"] == 0.25:
                        assert fx["pnl"] == got["pnl"], "E1 fixed equals proportional at 0.25 SOL (5 x 384,022 = 1,920,110)"
                    else:
                        want = max(0.0, V.E1_FIXED - fx["haircut"])
                        assert fx["e1_extra"] == want and (fx["pnl"] != got["pnl"] or got["e1_extra"] == want)
                else:
                    assert fx["pnl"] == got["pnl"]
    assert n_filled > 0
    assert 384_022 * 5 == V.E1_FIXED
    # differ at 0.10 and 1.0 when the floor binds
    by = {r["stake_sol"]: r for r in trades}
    for s in (0.10, 1.0):
        r = by[s]
        st = V.reconstruct_state(r["calc"])
        a = AP.price_leg(r["prog"], float(round(s * 1e9)), st, 1.9, cfg, "sc")
        b = V.e1_fixed_leg(r["prog"], float(round(s * 1e9)), st, 1.9, cfg, "sc")
        if a["state"] == "filled" and a["e1_extra"] > 0:
            assert a["pnl"] != b["pnl"]


def test_telescoping_identity_of_the_cost_drag(tmp_path):
    res = priced_rows(tmp_path)
    cfg = res["cfg"]
    n = 0
    for r in [r for r in res["rows"] if r["kind"] == "trade"]:
        stake = float(round(r["stake_sol"] * 1e9))
        st = V.reconstruct_state(r["calc"])
        for lat in ("1p3", "1p9"):
            for rule in V.RULES:
                if r["calc"][lat]["by_rule"][rule]["state"] != "filled":
                    continue
                dc = V.decompose_trip(r["prog"], stake, st, V.LAT_F[lat], cfg, rule)
                if dc is None:
                    continue
                n += 1
                s = dc["impact"] + dc["haircut"] + dc["sends"] + dc["e1_proportional"]
                assert abs(s - dc["total_proportional"]) < 1e-6 * max(1.0, abs(s))
                assert abs(dc["impact"] + dc["haircut"] + dc["sends"] + dc["e1_fixed"] - dc["total_fixed"]) < 1e-6 * max(1.0, abs(s))
                assert dc["e1_proportional"] == r["calc"][lat]["by_rule"][rule]["e1_extra"]
                assert abs(dc["sends"] - 2 * cfg.send) < 1e-3
    assert n > 0


# ------------------------------------------------------------------------------------------------------------------------------ the equality test
CLOCK2 = 1_795_132_800_000  # r4bt-a2's clock, 2026-11-20T00:00:00Z


def _a2_world(tmp: Path, monkeypatch, decisions):
    monkeypatch.setattr(T, "CLOCK", CLOCK2)
    monkeypatch.setattr(T, "NOW", CLOCK2 + 3 * DAY)
    kw = T.world(tmp, decisions=decisions)
    register_bot("r4bt-a2", T.SHA, CLOCK2, kw["registry"], deciding_stake=0.25, model_ids=[], verify_sha=False, now_ms=0)
    return kw


def test_quarter_rung_equals_registered_pricer_rows_and_cells(tmp_path, monkeypatch):
    t0 = CLOCK2 // 1000
    gap = {"type": "gap", "kind": "backlog_jump", "prog": "pumpswap", "data_loss": True, "slot": 0, "t_recv_ms": CLOCK2 + 3 * HR + 900_000}
    decisions = [(CLOCK2 + 1200, T.entry_body(order="MP1:%d" % t0, t_mark=t0)), (CLOCK2 + HR + 1200, T.exit_body(order="MP1:%d" % t0)),
                 (CLOCK2 + 2 * HR + 1300, T.entry_body(pool="P2", order="MP2:%d" % (t0 + 7200), t_mark=t0 + 7200)),
                 (CLOCK2 + 3 * HR + 1300, T.exit_body(order="MP2:%d" % (t0 + 7200), reason="restart")),
                 (CLOCK2 + 3 * HR + 600_000, T.entry_body(pool="P3", order="MP3:%d" % (t0 + 10800), t_mark=t0 + 10800)),
                 (CLOCK2 + 4 * HR + 600_000, T.exit_body(order="MP3:%d" % (t0 + 10800))),
                 (CLOCK2 + 3 * HR + 950_000, T.entry_body(pool="P4", order="MP4:%d" % (t0 + 10800), t_mark=t0 + 10800)),
                 (CLOCK2 + 4 * HR + 700_000, T.exit_body(order="MP4:%d" % (t0 + 10800))),
                 (CLOCK2 + 2 * HR + 100_000, T.entry_body(pool="P5", order="MP5:%d" % (t0 + 7300), t_mark=t0 + 7300)),
                 (CLOCK2 + 2 * HR + 900_000, T.exit_body(order="MP5:%d" % (t0 + 7300)))]
    kw = _a2_world(tmp_path, monkeypatch, decisions)
    import shutil

    shutil.rmtree(tmp_path / "tape")  # the world's own tape is replaced by one that carries pools P1..P4
    mk_tape(tmp_path / "tape", CLOCK2 - 3 * HR, 10, pools=("P1", "P2", "P3", "P4", "P5"), extra={P.hour_name(CLOCK2 + 3 * HR): [gap]})
    res_a = P.price(**dict(kw, bot_id="r4bt-a2"))
    A = Ledger(tmp_path / "ledger").read_bot("r4bt-a2")
    # the preview side, on the same decisions and the same tape files
    dl = DecisionLog(tmp_path / "declog")
    rows = [r for r in dl.rows(G.ARM)]
    ents = [dict(r, _cid=DRY) for r in rows if r["kind"] == "entry"]
    exs = [dict(r, _cid=DRY) for r in rows if r["kind"] == "exit"]
    last = max(r["ms"] for r in ents + exs)
    files = P.tape_hours(tmp_path / "tape", CLOCK2 - 3 * HR, last + P.EXIT_POST_MS, CLOCK2 + 3 * DAY)
    rb = V.replay(ents, exs, files, 0, tmp_path / "work", pin_commit=PIN, code_state_fn=lambda: (PIN, False))
    B = rb["rows"]
    keyf = ("calc", "legs", "v_src", "state_src", "decision_ms", "corrected_slot", "exit_reason", "exclusion_reason", "kind", "prog", "effective_slot", "stamp_ms", "call_ms")

    def pick(rows_, bot, stake):
        out = [r for r in rows_ if r["kind"] in ("trade", "excluded") and r["bot_id"] == bot and r["stake_sol"] == stake]
        return sorted(out, key=lambda r: (r["decision_ms"]["entry"], r["pool"]))

    qa, qb = pick(A, "r4bt-a2", 0.25), pick(B, V.PREVIEW_BOT, 0.25)
    assert len(qa) == len(qb) == 5
    for ra, rb_ in zip(qa, qb):
        for k in keyf:
            assert ra.get(k) == rb_.get(k), k
    assert {r["kind"] for r in qa} == {"trade", "excluded"} and "engine_restart" in {r.get("exclusion_reason") for r in qa}
    assert res_a["counts"]["engine_restart_exclusions"] == 1
    # the four stakes are all priced
    assert {r["stake_sol"] for r in B if r["kind"] in ("trade", "excluded")} == set(V.STAKES)
    # the deciding cells: the 0.25 rung's rent-0 vectors equal r4bt_read.deciding_cells
    bd = json.loads(Path(kw["boundary_file"]).read_text())["boundary_ms"]
    dec_a = RD.deciding_trades(A, CLOCK2, bd, 0.25)
    dec_b = RD.deciding_trades(B, CUT, 1 << 62, 0.25)
    assert len(dec_a) == len(dec_b) >= 2
    want = RD.deciding_cells(dec_a, V.RULES)
    cells, vec = V.cells_of(dec_b, rb["cfg"], P0)
    for k in want:
        assert vec["proportional"][k] == want[k]["x0_sol"], k
        assert cells["proportional"][k]["rent0"]["total_sol"] == pytest.approx(want[k]["rent0"]["total_sol"], abs=0)
        assert cells["proportional"][k]["rent0"]["mean_sol"] == want[k]["rent0"]["mean_sol"]
        assert cells["proportional"][k]["rent_charged"]["total_sol"] == pytest.approx(want[k]["rent_charged"]["total_sol"], abs=0)
        assert vec["fixed"][k] == want[k]["x0_sol"], "E1 fixed equals proportional at the 0.25 rung"
    assert V.trade_digest(B) == V.trade_digest(B)


# ------------------------------------------------------------------------------------------------------------------------------ static
def test_no_registered_tool_mentions_the_preview_and_the_preview_has_no_read_imports():
    for p in (REPO / "tools").glob("r4bt_*"):
        if p.name.startswith("test_"):
            continue
        assert "r4b-preview" not in p.read_text() and "r4b_preview" not in p.read_text(), p.name
    for p in (REPO / "scripts").rglob("*r4bt*"):
        if p.is_file():
            assert "r4b-preview" not in p.read_text() and "r4b_preview" not in p.read_text(), p.name
    src = (REPO / "tools" / "r4b_preview.py").read_text()
    for banned in ("import r4b_book", "r4b_read", "r4b_insample_ladder", "tools import r4b_book", "fcntl.flock", "flock("):
        assert banned not in src.replace("never calls flock", ""), banned
    assert not re.search(r"^from tools import .*r4b_(book|read|live)", src, re.M)


def test_wrapper_is_posix_sh_with_the_research_0_rules():
    sh = (REPO / "scripts" / "research" / "r4b-preview.sh").read_text()
    assert sh.startswith("#!/bin/sh")
    for need in ("systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 nice -n 19", "/data/mal/venv/bin/python -I tools/r4b_preview.py run", "--manifest", "--manifest-sha256", "--as-of"):
        assert need in sh, need
    assert "[[" not in sh and "PIPESTATUS" not in sh and "oom_score_adj" not in sh and "user-1002" not in sh


def test_cli_main_runs_and_the_script_runs_isolated(tmp_path):
    w = world(tmp_path)
    assert V.main(["run", "--manifest", w["manifest"], "--manifest-sha256", w["manifest_sha256"], "--as-of", w["as_of"]], roots=w["roots"], now_ms=w["now_ms"],
                  code_state_fn=w["code_state_fn"]) == 0
    assert (out_dir(w) / "preview.json").exists()
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-I", str(REPO / "tools" / "r4b_preview.py"), "run", "--manifest", w["manifest"], "--manifest-sha256", "0" * 64, "--as-of", w["as_of"]],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 2 and "REFUSED" in r.stderr, "the real roots do not exist here and the manifest sha is wrong: refused, nothing opened"
