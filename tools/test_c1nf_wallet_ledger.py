"""Tests for tools/c1nf_wallet_ledger.py. Synthetic tapes only; no network, no real tape.

Run with a Python that has pytest, numpy and duckdb 1.5.6, for example
  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_c1nf_wallet_ledger.py

Without duckdb this file does not pass quietly as "1 skipped" (review 2): test_duckdb_pinned_is_installed fails with
the command above, and every other test is skipped. Set C1NF_LEDGER_ALLOW_NO_DUCKDB=1 to turn that failure into a skip,
for a run that knowingly has no duckdb.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import random
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import os

import numpy as np
import pytest

try:
    import duckdb
except ImportError:  # not importorskip: a whole-file skip reads as green (review 2)
    duckdb = None

from tools import c1nf_wallet_ledger as L  # noqa: E402

_NO_DUCKDB = (
    "duckdb is not importable, so the C1-NF ledger tests did not run. Use a Python with duckdb "
    f"{L.PINNED_DUCKDB}, e.g. PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages "
    "/data/mal/venv/bin/python -m pytest tools/test_c1nf_wallet_ledger.py "
    "(or set C1NF_LEDGER_ALLOW_NO_DUCKDB=1 to skip on purpose)"
)


@pytest.fixture(autouse=True)
def _require_duckdb(request):
    if duckdb is None and request.node.originalname != "test_duckdb_pinned_is_installed":
        pytest.skip("duckdb not importable (see test_duckdb_pinned_is_installed)")


def test_duckdb_pinned_is_installed():
    """Fails, not skips, when duckdb is missing; the wallet key needs the pinned version."""
    if duckdb is None:
        if os.environ.get("C1NF_LEDGER_ALLOW_NO_DUCKDB") == "1":
            pytest.skip("C1NF_LEDGER_ALLOW_NO_DUCKDB=1: duckdb missing, skipped on purpose")
        pytest.fail(_NO_DUCKDB, pytrace=False)
    assert duckdb.__version__ == L.PINNED_DUCKDB, (
        f"duckdb {duckdb.__version__} is not the pinned {L.PINNED_DUCKDB}; wallet hashes would differ"
    )

DAY = "2026-09-10"
NEXT = "2026-09-11"
MINTS = [f"MINT{i:02d}pump" for i in range(9)] + [L.WSOL_MINT]
TRADERS = [f"TRADER{i:03d}" + "x" * 30 for i in range(60)]


def make_rows(seed: int, n: int = 2500, null_trader: bool = False) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        venue = rng.choice(["pumpswap", "pumpswap", "pump_bonding", "pumpswap"])
        rows.append(
            {
                "venue": venue,
                "mint": rng.choice(MINTS),
                "trader": rng.choice(TRADERS),
                "side": rng.choice(["buy", "sell"]),
                "sol_lamports": rng.randrange(0, 5_000_000_000),
                "signature": f"sig{seed}-{i}",
                "event_index": rng.randrange(0, 2),
            }
        )
    # rows the ledger must ignore or treat specially
    rows.append({"venue": "other_venue", "mint": MINTS[0], "trader": TRADERS[0], "side": "buy", "sol_lamports": 999, "signature": f"o{seed}", "event_index": 0})
    rows.append({"venue": "other_venue", "mint": MINTS[0], "trader": None, "side": "buy", "sol_lamports": 9, "signature": f"on{seed}", "event_index": 0})
    if null_trader:  # a venue row with a NULL trader: refused by default (the pinned script would keep it)
        rows.append({"venue": "pumpswap", "mint": MINTS[1], "trader": None, "side": "buy", "sol_lamports": 777, "signature": f"n{seed}", "event_index": 0})
    rows.append({"venue": "pumpswap", "mint": MINTS[2], "trader": TRADERS[1], "side": None, "sol_lamports": 555, "signature": f"s{seed}", "event_index": 0})
    return rows


def th_of(traders) -> dict[str, int]:
    con = duckdb.connect()
    return {t: int(con.execute("SELECT hash(?)", [t]).fetchone()[0]) for t in traders}


def reference(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Pure-Python integer reference of the 01_wallet_daily.py semantics."""
    pm = defaultdict(lambda: [0, 0, 0, 0, 0, 0])  # n nbond nb ns buy sell
    for r in rows:
        if r["venue"] not in L.VENUES or r["trader"] is None:
            continue
        e = pm[(r["trader"], r["mint"])]
        e[0] += 1
        e[1] += r["venue"] == "pump_bonding"
        if r["side"] == "buy":
            e[2] += 1
            e[4] += r["sol_lamports"]
        elif r["side"] == "sell":
            e[3] += 1
            e[5] += r["sol_lamports"]
    out: dict[str, dict[str, int]] = {}
    for (t, _m), (n, nbond, nb, ns, buy, sell) in pm.items():
        o = out.setdefault(t, dict(n=0, nm=0, nbond=0, nwin=0, nrt=0, buy_lamports=0, sell_lamports=0, cash_lamports=0))
        o["n"] += n
        o["nm"] += 1
        o["nbond"] += nbond
        o["nwin"] += int(ns > 0 and nb > 0 and sell > buy)
        o["nrt"] += int(ns > 0 and nb > 0)
        o["buy_lamports"] += buy
        o["sell_lamports"] += sell
        o["cash_lamports"] += sell - buy
    return out


def write_tape(root: Path, day: str, rows: list[dict], files: int = 3, seed: int = 0) -> None:
    """Exploration-style tape: <root>/trades/<day>T<hh>.parquet."""
    (root / "trades").mkdir(parents=True, exist_ok=True)
    shuffled = rows[:]
    random.Random(seed).shuffle(shuffled)
    con = duckdb.connect()
    for k in range(files):
        part = shuffled[k::files]
        con.execute("CREATE OR REPLACE TABLE t (venue VARCHAR, mint VARCHAR, trader VARCHAR, side VARCHAR, sol_lamports BIGINT)")
        con.executemany(
            "INSERT INTO t VALUES (?, ?, ?, ?, ?)",
            [(r["venue"], r["mint"], r["trader"], r["side"], r["sol_lamports"]) for r in part],
        )
        con.execute(f"COPY t TO '{root}/trades/{day}T{12 + k:02d}.parquet' (FORMAT parquet)")


def tip_line(r: dict, extra: bool = True) -> str:
    d = dict(r)
    if extra:
        d.update({"v": 2, "feed": "helius_getblock_tip", "source": "tip", "token_raw": 5, "slot": 1, "t_recv_ms": 1, "quote_is_wsol": True})
    return json.dumps(d)


def write_tip_hour(d: Path, hour: str, rows: list[dict], zst: bool = False) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    text = "".join(tip_line(r) + "\n" for r in rows)
    if zst:
        p = d / f"trades-{hour}.jsonl.zst"
        subprocess.run(["zstd", "-q", "-o", str(p)], input=text.encode(), check=True)
    else:
        p = d / f"trades-{hour}.jsonl"
        p.write_text(text)
    return p


def write_tip_day(d: Path, day: str, rows: list[dict], hours: int = 24, zst: bool = False) -> None:
    """Spread `rows` over `hours` hour files; the other hours get one tiny row so they are non-empty."""
    filler = {"venue": "pumpswap", "mint": MINTS[0], "trader": "FILLER" + "z" * 38, "side": "buy", "sol_lamports": 1, "signature": "f", "event_index": 0}
    parts = [rows[k::3] for k in range(3)]
    for h in range(hours):
        write_tip_hour(d, f"{day}T{h:02d}", parts[h] if h < 3 else [filler], zst=zst)


def daily_to_ref(arrays: dict[str, np.ndarray], traders=TRADERS + ["FILLER" + "z" * 38]) -> dict[str, dict[str, int]]:
    ths = th_of(traders)
    by_th = {v: k for k, v in ths.items()}
    out = {}
    for i, th in enumerate(arrays["th"]):
        t = by_th[int(th)]
        out[t] = {c: int(arrays[c][i]) for c in L.DAILY_COLS}
    return out


def build_tape_day(tmp: Path, root_name: str, rows: list[dict], **kw) -> dict:
    tape = tmp / "tape"
    if not (tape / "trades").exists():
        write_tape(tape, DAY, rows, files=kw.pop("files", 3), seed=kw.pop("seed", 0))
    return L.build_day(DAY, "tape", tmp / root_name, tape_dir=tape, **kw)


# --------------------------------------------------------------------------------------------


def test_hash_pin_and_refusal(monkeypatch):
    con = L.connect(threads=1, mem_gb=1)
    assert L.check_hash_pin(con)
    monkeypatch.setitem(L.HASH_VECTORS, "abc", 1)
    with pytest.raises(L.Refused, match="hash"):
        L.check_hash_pin(con)


def test_tape_day_matches_integer_reference(tmp_path):
    rows = make_rows(1, null_trader=True)
    res = build_tape_day(tmp_path, "out", rows, allow_null_trader=True)
    assert res["status"] == "built" and res["hours"] == 3
    arrays = L.load_daily(L.daily_paths(tmp_path / "out", DAY)[0])
    assert daily_to_ref(arrays) == reference(rows)
    assert res["rows"]["rows_null_trader"] == 1  # with the flag the NULL trader row is dropped and counted
    man = json.loads(L.daily_paths(tmp_path / "out", DAY)[1].read_text())
    assert man["meta"]["rows"]["rows_null_trader"] == 1
    L.validate_daily(arrays)


def test_null_trader_is_refused_by_default(tmp_path, capsys):
    """Review 1, NULL-trader filter: the pinned script keeps NULL traders as hash(NULL); we refuse instead of drifting."""
    rows = make_rows(1, 300, null_trader=True)
    with pytest.raises(L.Refused, match="NULL trader"):
        build_tape_day(tmp_path, "out", rows)
    assert not (tmp_path / "out" / "daily").exists() or not list((tmp_path / "out" / "daily").iterdir())
    base = ["day", "--day", DAY, "--adapter", "tape", "--tape-dir", str(tmp_path / "tape"), "--out-root", str(tmp_path / "cli")]
    assert L.main(base) == 3
    assert "NULL trader" in capsys.readouterr().err
    assert L.main(base + ["--allow-null-trader"]) == 0
    # a NULL trader outside the two venues is ignored, as in the pinned script (no refusal, no count)
    ok = make_rows(2, 300)
    assert any(r["trader"] is None for r in ok)
    assert build_tape_day(tmp_path / "x", "out", ok)["rows"]["rows_null_trader"] == 0


def test_bit_identical_across_order_chunking_and_threads(tmp_path):
    rows = make_rows(2)
    a = build_tape_day(tmp_path, "a", rows, threads=1)
    # same rows, shuffled differently and split into 5 files instead of 3
    tape2 = tmp_path / "tape2"
    write_tape(tape2, DAY, rows, files=5, seed=99)
    b = L.build_day(DAY, "tape", tmp_path / "b", tape_dir=tape2, threads=4)
    assert a["content_sha256"] == b["content_sha256"]
    # the zip layer is deterministic too (fixed timestamps), though the hours list differs: compare arrays
    za = L.load_daily(L.daily_paths(tmp_path / "a", DAY)[0])
    zb = L.load_daily(L.daily_paths(tmp_path / "b", DAY)[0])
    assert all(np.array_equal(za[k], zb[k]) for k in za)
    # same source, rebuilt: identical file bytes
    c = L.build_day(DAY, "tape", tmp_path / "c", tape_dir=tmp_path / "tape", threads=2)
    assert a["npz_sha256"] == c["npz_sha256"]


def test_float_sum_order_flip_is_gone(tmp_path):
    # one wallet; per-mint cash +0.1, +0.2, -0.3 SOL: exactly 0 in lamports, +5.5e-17 as a float sum
    w = "W" * 44
    rows = [
        {"venue": "pumpswap", "mint": "A", "trader": w, "side": "sell", "sol_lamports": 100_000_000, "signature": "1", "event_index": 0},
        {"venue": "pumpswap", "mint": "B", "trader": w, "side": "sell", "sol_lamports": 200_000_000, "signature": "2", "event_index": 0},
        {"venue": "pumpswap", "mint": "C", "trader": w, "side": "buy", "sol_lamports": 300_000_000, "signature": "3", "event_index": 0},
    ]
    # the defect: a float sum of these per-mint cash values is not 0, so `skill = cash > 0` is decided by rounding
    assert sum([0.1, 0.2, -0.3]) > 0 and sum([-0.3, 0.2, 0.1]) > 0 and sum([0.3, -0.1, -0.2]) < 0
    for k, order in enumerate(itertools.permutations(rows)):
        tape = tmp_path / f"t{k}"
        write_tape(tape, DAY, list(order), files=3, seed=k)
        L.build_day(DAY, "tape", tmp_path / f"o{k}", tape_dir=tape)
        z = L.load_daily(L.daily_paths(tmp_path / f"o{k}", DAY)[0])
        assert int(z["cash_lamports"][0]) == 0 and int(z["nm"][0]) == 3
        assert not (L.to_wl_float(z)["cash"] > 0).any()  # skill = cash > 0 is False in every order


def test_tip_adapter_equals_tape_adapter_and_dedupes(tmp_path):
    rows = make_rows(3)
    tape_res = build_tape_day(tmp_path, "tape_out", rows)
    tip = tmp_path / "tip"
    parts = [rows[k::3] for k in range(3)]
    for h in range(24):
        part = parts[h] if h < 3 else []
        if h == 1:
            part = part + part[:25]  # 25 exact duplicate lines (a restart that replayed a block)
        if h >= 3:
            part = [{"venue": "pumpswap", "mint": "M", "trader": "T" * 40, "side": "buy", "sol_lamports": 0, "signature": f"z{h}", "event_index": 0}]
        write_tip_hour(tip, f"{DAY}T{h:02d}", part)
    write_tip_hour(tip, f"{NEXT}T00", parts[0][:3])
    res = L.build_day(DAY, "tip", tmp_path / "tip_out", tip_dirs=[tip])
    assert res["rows"]["rows_dedup_dropped"] == 25
    za = L.load_daily(L.daily_paths(tmp_path / "tape_out", DAY)[0])
    zb = L.load_daily(L.daily_paths(tmp_path / "tip_out", DAY)[0])
    # the tip day has 21 extra one-row filler wallets with 0 lamports: drop that wallet before comparing
    filler = th_of(["T" * 40])["T" * 40]
    keep = zb["th"] != np.uint64(filler)
    assert all(np.array_equal(za[k], zb[k][keep]) for k in za)
    assert zb["n"][~keep][0] == 21 and zb["nm"][~keep][0] == 1
    # without dedupe the 25 duplicates are summed twice
    res2 = L.build_day(DAY, "tip", tmp_path / "tip_out2", tip_dirs=[tip], dedupe=False)
    assert res2["rows"]["rows_dedup_dropped"] == 0
    assert int(L.load_daily(L.daily_paths(tmp_path / "tip_out2", DAY)[0])["n"].sum()) == int(zb["n"].sum()) + 25
    assert tape_res["wallets"] == len(za["th"])


def test_tip_key_conflict_is_refused(tmp_path):
    tip = tmp_path / "tip"
    base = {"venue": "pumpswap", "mint": "M", "trader": "T" * 40, "side": "buy", "sol_lamports": 5, "signature": "S", "event_index": 0}
    for h in range(24):
        rows = [base, dict(base, sol_lamports=6)] if h == 0 else [dict(base, signature=f"s{h}")]
        write_tip_hour(tip, f"{DAY}T{h:02d}", rows)
    write_tip_hour(tip, f"{NEXT}T00", [base])
    with pytest.raises(L.Refused, match="share"):
        L.build_day(DAY, "tip", tmp_path / "o", tip_dirs=[tip])
    assert not (tmp_path / "o" / "daily").exists() or not list((tmp_path / "o" / "daily").glob("*.npz"))


def test_tip_open_day_and_missing_hours_are_refused(tmp_path):
    rows = make_rows(4, 200)
    tip = tmp_path / "tip"
    write_tip_day(tip, DAY, rows)
    with pytest.raises(L.Refused, match="not closed"):
        L.build_day(DAY, "tip", tmp_path / "o", tip_dirs=[tip])
    # an empty D+1 T00 (the follower pre-creates it) does not close the day
    (tip / f"trades-{NEXT}T00.jsonl").write_text("")
    with pytest.raises(L.Refused, match="not closed"):
        L.build_day(DAY, "tip", tmp_path / "o", tip_dirs=[tip])
    assert L.build_day(DAY, "tip", tmp_path / "o", tip_dirs=[tip], allow_open_day=True)["status"] == "built"
    write_tip_hour(tip, f"{NEXT}T00", rows[:2])
    (tip / f"trades-{DAY}T05.jsonl").unlink()
    (tip / f"trades-{DAY}T06.jsonl").write_text("")  # an empty hour file counts as missing
    with pytest.raises(L.Refused, match="missing hour files"):
        L.build_day(DAY, "tip", tmp_path / "o2", tip_dirs=[tip])
    res = L.build_day(DAY, "tip", tmp_path / "o2", tip_dirs=[tip], allow_missing_hours=True)
    assert res["hours_missing"] == [f"{DAY}T05", f"{DAY}T06"]


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd CLI not installed")
def test_archive_zst_and_live_plain_give_identical_ledger_and_sha_check(tmp_path):
    rows = make_rows(5, 400)
    arch, live = tmp_path / "arch", tmp_path / "live"
    write_tip_day(arch, DAY, rows, zst=True)
    write_tip_hour(arch, f"{NEXT}T00", rows[:2], zst=True)
    write_tip_day(live, DAY, rows)
    write_tip_hour(live, f"{NEXT}T00", rows[:2])
    a = L.build_day(DAY, "tip", tmp_path / "a", tip_dirs=[arch])
    b = L.build_day(DAY, "tip", tmp_path / "b", tip_dirs=[live])
    c = L.build_day(DAY, "tip", tmp_path / "c", tip_dirs=[live, arch])  # first dir wins
    assert a["npz_sha256"] == b["npz_sha256"] == c["npz_sha256"]
    man = json.loads(L.daily_paths(tmp_path / "c", DAY)[1].read_text())
    assert {s["kind"] for s in man["sources"]} == {"jsonl"}
    # --verify-sha: the sidecar is the sha256 of the DECOMPRESSED jsonl
    import hashlib

    for p in arch.glob("trades-*.jsonl.zst"):
        raw = subprocess.run(["zstd", "-dc", str(p)], capture_output=True, check=True).stdout
        (arch / (p.name[: -len(".jsonl.zst")] + ".jsonl.sha256")).write_text(hashlib.sha256(raw).hexdigest() + "\n")
    assert L.build_day(DAY, "tip", tmp_path / "d", tip_dirs=[arch], verify_sha=True)["status"] == "built"
    (arch / f"trades-{DAY}T01.jsonl.sha256").write_text("0" * 64 + "\n")
    with pytest.raises(L.Refused, match="sha256 sidecar mismatch"):
        L.build_day(DAY, "tip", tmp_path / "e", tip_dirs=[arch], verify_sha=True)


def test_idempotent_exists_and_tamper_detected(tmp_path):
    rows = make_rows(6, 300)
    first = build_tape_day(tmp_path, "o", rows)
    again = L.build_day(DAY, "tape", tmp_path / "o", tape_dir=tmp_path / "tape")
    assert again["status"] == "exists" and again["content_sha256"] == first["content_sha256"]
    man_path = L.daily_paths(tmp_path / "o", DAY)[1]
    man = json.loads(man_path.read_text())
    man["content_sha256"] = "0" * 64
    man_path.write_text(json.dumps(man))
    with pytest.raises(L.Refused, match="content hash"):
        L.build_day(DAY, "tape", tmp_path / "o", tape_dir=tmp_path / "tape")


def test_npz_is_deterministic_and_loads_with_plain_numpy(tmp_path):
    arrays = {"th": np.array([1, 5, 9], np.uint64), "n": np.array([3, 2, 1], np.int64)}
    p1, p2 = tmp_path / "a.npz", tmp_path / "b.npz"
    L.write_npz_deterministic(p1, arrays)
    L.write_npz_deterministic(p2, arrays)
    assert p1.read_bytes() == p2.read_bytes()
    with np.load(p1, allow_pickle=False) as z:
        assert z["th"].dtype == np.uint64 and z["n"].tolist() == [3, 2, 1]


def _three_days(tmp_path: Path) -> list[str]:
    days = ["2026-09-08", "2026-09-09", "2026-09-10"]
    for i, d in enumerate(days):
        tape = tmp_path / f"tape-{d}"
        write_tape(tape, d, make_rows(10 + i, 700), files=2, seed=i)
        L.build_day(d, "tape", tmp_path / "root", tape_dir=tape, wl_parquet_dir=tmp_path / "wlparq")
    return days


def test_asof_fold_incremental_and_lookup_match_passa_sql(tmp_path):
    days = _three_days(tmp_path)
    root = tmp_path / "root"
    # as-of 09-10 (days 08, 09), then as-of 09-11 incrementally from it, and from scratch
    r1 = L.build_asof(root, "2026-09-10")
    assert r1["mode"] == "fold" and r1["days"] == 2
    r2 = L.build_asof(root, "2026-09-11")
    assert r2["mode"] == "incremental" and r2["days"] == 3
    scratch = L.build_asof(root, "2026-09-11", force=True, use_prev=False)
    assert scratch["mode"] == "fold" and scratch["content_sha256"] == r2["content_sha256"]
    assert L.build_asof(root, "2026-09-11")["status"] == "exists"

    led = L.AsofLedger.open(root / "asof" / "asof-2026-09-11", verify=True)
    assert isinstance(led.th, np.memmap) and all(isinstance(v, np.memmap) for v in led.cols.values())
    assert bool(np.all(led.th[1:] > led.th[:-1]))

    # brute force over the integer references
    expect: dict[str, dict[str, int]] = {}
    for i in range(3):
        for t, o in reference(make_rows(10 + i, 700)).items():
            e = expect.setdefault(t, dict.fromkeys(L.ASOF_COLS, 0))
            for c in L.DAILY_COLS:
                e[c] += o[c]
            e["buy_q"] += int(L.grid_units(np.array([o["buy_lamports"]]))[0])
            e["cash_q"] += int(L.grid_units(np.array([o["cash_lamports"]]))[0])
            e["ndays"] += 1
    ths = th_of(TRADERS)
    known, i = led.lookup_index(np.array([ths[t] for t in expect], np.uint64))
    assert known.all()
    for (t, e), ix in zip(expect.items(), i):
        assert {c: int(led.cols[c][ix]) for c in L.ASOF_COLS} == e

    # the 11_passA.py SQL over the prior pinned (grid) wl/*.parquet files gives the same numbers, bit for bit
    con = duckdb.connect()
    wl = "['" + "','".join(str(tmp_path / "wlparq" / f"{d}.parquet") for d in days) + "']"
    x = con.execute(
        f"SELECT th, sum(n) n, sum(nbond) nbond, sum(cash) cash, sum(nwin) nwin, sum(nrt) nrt, count(*) ndays, sum(buy) buy "
        f"FROM read_parquet({wl}) GROUP BY 1 ORDER BY 1"
    ).fetchnumpy()
    q = np.array(x["th"], dtype=np.uint64)
    known, m = led.passa_matrix(np.concatenate([q, np.array([12345], np.uint64)]))  # 12345: an unknown wallet
    assert known[:-1].all() and not known[-1] and np.isnan(m[-1]).all()
    for j, c in enumerate(L.PASSA_COLS):
        assert np.array_equal(m[:-1, j], np.asarray(x[c], dtype=np.float64)), c


def test_asof_refusals(tmp_path):
    root = tmp_path / "root"
    with pytest.raises(L.Refused, match="no daily"):
        L.build_asof(root, "2026-09-11")
    _three_days(tmp_path)
    L.build_asof(root, "2026-09-10")
    # a changed input behind an existing snapshot is reported, not silently reused
    man_path = L.daily_paths(root, "2026-09-09")[1]
    man = json.loads(man_path.read_text())
    man["content_sha256"] = "1" * 64
    man_path.write_text(json.dumps(man))
    with pytest.raises(L.OutputDiffers):
        L.build_asof(root, "2026-09-10")
    # a snapshot file edited after the build fails verify
    man["content_sha256"] = json.loads(L.daily_paths(root, "2026-09-09")[1].read_text())["content_sha256"]
    snap = root / "asof" / "asof-2026-09-10"
    with open(snap / "n.npy", "r+b") as fh:
        fh.seek(-1, 2)
        fh.write(b"\x7f")
    with pytest.raises(L.Refused, match="sha256 differs"):
        L.AsofLedger.open(snap, verify=True)


def test_cli_rollup_end_to_end_and_exit_codes(tmp_path, capsys):
    tip = tmp_path / "tip"
    rows = make_rows(7, 300)
    for d, seed in (("2026-10-06", 7), ("2026-10-07", 8)):
        write_tip_day(tip, d, make_rows(seed, 300))
    write_tip_hour(tip, "2026-10-08T00", rows[:2])
    root = tmp_path / "root"
    base = ["--out-root", str(root), "--tip-dir", str(tip), "--threads", "1", "--mem-gb", "1", "--tmp-dir", str(tmp_path / "duck")]
    assert L.main(["rollup", "--day", "2026-10-06", *base]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["day"]["status"] == "built" and out["asof"]["asof_day"] == "2026-10-07" and out["asof"]["mode"] == "fold"
    assert L.main(["rollup", "--day", "2026-10-07", *base]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["asof"]["mode"] == "incremental" and out["asof"]["days"] == 2
    assert L.main(["rollup", "--day", "2026-10-07", *base]) == 0  # rerun: nothing to do
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["day"]["status"] == "exists"
    # 10-08 is not closed (no 10-09 T00 file): refused, exit 3, nothing written for that day
    assert L.main(["rollup", "--day", "2026-10-08", *base]) == 3
    assert not L.daily_paths(root, "2026-10-08")[0].exists()
    assert L.main(["rollup", "--day", "not-a-day", *base]) == 3


def test_coverage_labels_the_known_hole(tmp_path):
    tape = tmp_path / "tape"
    (tape / "trades").mkdir(parents=True)
    for h in ("2026-09-25T05", "2026-09-25T06"):
        (tape / "trades" / f"{h}.parquet").write_bytes(b"x")
    tip = tmp_path / "tip"
    tip.mkdir()
    for h in ("2026-10-05T05", "2026-10-05T06", "2026-10-05T08"):
        (tip / f"trades-{h}.jsonl.zst").write_bytes(b"x" * 100)
    (tip / "trades-2026-10-05T07.jsonl.zst").write_bytes(b"")  # empty
    cov = L.coverage(tape, [tip])
    holes = {(h["start"], h["end"]): h["reason"] for h in cov["holes"]}
    assert ("2026-09-25T07", "2026-10-05T05") in holes
    assert "tip archive" in holes[("2026-09-25T07", "2026-10-05T05")]
    assert cov["days"]["2026-09-25"]["source"] == "tape" and cov["days"]["2026-10-05"]["tip_hours"] == 4
    assert "2026-10-05T07" in cov["tip_small_or_empty_hours"]
    assert "holes between" in L.format_coverage(cov)


def test_diff_wl_classifies_sign_flips_against_a_float_table():
    th = np.array([1, 2, 3, 4], np.uint64)
    exact = {
        "th": th,
        "n": np.array([3, 3, 3, 3]),
        "nm": np.array([1, 1, 1, 1]),
        "nbond": np.zeros(4, np.int64),
        "nwin": np.zeros(4, np.int64),
        "nrt": np.zeros(4, np.int64),
        "buy_lamports": np.array([1000, 10_000_000, 10_000_000, 1000], np.int64),
        "sell_lamports": np.array([1000, 20_000_000, 5_000_000, 1100], np.int64),
        "cash_lamports": np.array([0, 10_000_000, -5_000_000, 100], np.int64),
    }
    a = L.to_wl_float(exact)
    assert a["cash"][3] == 0.0  # +100 lamports is under half a grid step: 0 on the pinned grid
    b = {k: v.copy() for k, v in a.items()}
    b["cash"][0] = 5.5e-17  # float residue on an exact-zero wallet
    b["cash"][3] = 1e-7  # the original float table kept the 100 lamports
    d = L.diff_wl(a, b, exact)
    assert d["cash_positive_flips"] == 2 and d["sign_differs_with_nonzero_exact_cash"] == 1
    assert d["sign_differs_by_class"] == {"exact_zero": 1, "rounds_to_zero_on_grid": 1, "unexplained": 0}
    assert d["exact_zero_cash_wallets"] == 1 and d["th_only_a"] == d["th_only_b"] == 0


def test_pinned_copy_is_verbatim_and_tampering_is_refused(tmp_path, monkeypatch):
    assert L.file_sha256(L.PINNED_DET_PATH) == L.PINNED_DET_SHA256
    assert L.pinned().GRID == 2.0**20
    bad = tmp_path / "c1nf_wallet_det_pinned.py"
    bad.write_text(L.PINNED_DET_PATH.read_text() + "\n# edited\n")
    monkeypatch.setattr(L, "PINNED_DET_PATH", bad)
    monkeypatch.setattr(L, "_PINNED", None)
    with pytest.raises(L.Refused, match="pinned"):
        L.to_grid(np.array([1], np.int64))


def test_grid_units_and_sql_grid_equal_the_pinned_to_grid(tmp_path):
    rng = np.random.default_rng(5)
    lam = np.concatenate(
        [
            rng.integers(-3_000_000_000, 3_000_000_000, 5000),
            rng.integers(-10**15, 10**15, 2000),
            np.arange(-1500, 1500),  # around the half-step at 476.84 lamports
            np.array([0, 476, 477, -476, -477, 953, 954, 1_048_576, 10**9]),
        ]
    ).astype(np.int64)
    assert np.array_equal(L.grid_units(lam).astype(np.float64) / 2.0**20, L.to_grid(lam))
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE daily (th UBIGINT, n BIGINT, nm BIGINT, nbond BIGINT, buy_lamports BIGINT, sell_lamports BIGINT, nwin BIGINT, nrt BIGINT, cash_lamports BIGINT)")
    rows = [(i + 1, 1, 1, 0, int(max(x, 0)), int(max(-x, 0)), 0, 0, int(x)) for i, x in enumerate(lam)]
    con.executemany("INSERT INTO daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    arrays = {
        "th": np.arange(1, len(lam) + 1, dtype=np.uint64),
        "n": np.ones(len(lam), np.int64),
        "nm": np.ones(len(lam), np.int64),
        "nbond": np.zeros(len(lam), np.int64),
        "nwin": np.zeros(len(lam), np.int64),
        "nrt": np.zeros(len(lam), np.int64),
        "buy_lamports": np.maximum(lam, 0),
        "sell_lamports": np.maximum(-lam, 0),
        "cash_lamports": lam,
    }
    L.write_wl_parquet(con, arrays, tmp_path / "x.parquet")  # raises if the SQL grid differs from to_grid
    got = L.load_wl_parquet(tmp_path / "x.parquet")
    assert np.array_equal(got["cash"], L.to_grid(lam))


def test_wl_parquet_equals_the_pinned_script_output(tmp_path):
    """Our wl/ file for a tape day equals what ARTIFACTS/exp025/ledger/01_wallet_daily_det.py writes: same
    rows, same values, and the same file bytes."""
    pytest.importorskip("pandas")
    rows = [r for r in make_rows(21, 3000) if r["trader"] is not None]  # the pinned script keeps a NULL wallet; the tape has none
    tape = tmp_path / "tape"
    write_tape(tape, DAY, rows, files=4, seed=3)
    L.build_day(DAY, "tape", tmp_path / "ours", tape_dir=tape, wl_parquet_dir=tmp_path / "ours_wl", threads=2)
    pin = L.pinned()
    assert pin.main(["--tape", str(tape / "trades"), "--out", str(tmp_path / "pinned_wl"), "--tmp", str(tmp_path / "duck"), "--threads", "1", "--day", DAY]) == 0
    ours, theirs = tmp_path / "ours_wl" / f"{DAY}.parquet", tmp_path / "pinned_wl" / f"{DAY}.parquet"
    con = duckdb.connect()
    a, b = f"'{ours}'", f"'{theirs}'"
    assert con.execute(f"SELECT count(*) FROM (SELECT * FROM {a} EXCEPT ALL SELECT * FROM {b})").fetchone()[0] == 0
    assert con.execute(f"SELECT count(*) FROM (SELECT * FROM {b} EXCEPT ALL SELECT * FROM {a})").fetchone()[0] == 0
    assert con.execute(f"SELECT count(*) FROM {a}").fetchone()[0] == con.execute(f"SELECT count(*) FROM {b}").fetchone()[0] > 0
    assert L.file_sha256(ours) == L.file_sha256(theirs)


def test_tip_json_columns_exist_in_follower_rows():
    """The columns the tip adapter reads must be in the rows tools/fast_tip_follower writes."""
    try:
        from tools.pump_history_backfill import rows_from_block
        from tools.test_fast_tip_follower import _block
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"follower decoder not importable: {exc}")
    trades = rows_from_block(_block(100, n=3), {})["trades"]
    assert trades, "fixture block produced no trade rows"
    for key in ("venue", "mint", "trader", "side", "sol_lamports", "signature", "event_index"):
        assert key in trades[0], key
    assert trades[0]["venue"] in L.VENUES and trades[0]["side"] in ("buy", "sell")


def test_follower_rows_roundtrip_through_the_adapter(tmp_path):
    try:
        from tools.pump_history_backfill import rows_from_block
        from tools.test_fast_tip_follower import _block
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"follower decoder not importable: {exc}")
    trades = rows_from_block(_block(100, n=4), {})["trades"]
    for r in trades:
        r["source"] = "tip"
    tip = tmp_path / "tip"
    for h in range(24):
        write_tip_hour(tip, f"{DAY}T{h:02d}", trades if h == 0 else trades[:1])
    write_tip_hour(tip, f"{NEXT}T00", trades[:1])
    res = L.build_day(DAY, "tip", tmp_path / "o", tip_dirs=[tip])
    assert res["rows"]["rows_used"] == len(trades) + 23
    z = L.load_daily(L.daily_paths(tmp_path / "o", DAY)[0])
    assert int(z["n"].sum()) == len(trades) + 23


def test_rollup_require_prev_day(tmp_path, capsys):
    tip = tmp_path / "tip"
    for d, seed in (("2026-10-06", 31), ("2026-10-07", 32)):
        write_tip_day(tip, d, make_rows(seed, 100))
    write_tip_hour(tip, "2026-10-08T00", make_rows(33, 5))
    base = ["--tip-dir", str(tip), "--threads", "1", "--mem-gb", "1", "--tmp-dir", str(tmp_path / "duck")]
    root = str(tmp_path / "root")
    # a skipped night: 10-07 with no 10-06 ledger is refused, and nothing is written
    assert L.main(["rollup", "--day", "2026-10-07", "--require-prev-day", "--out-root", root, *base]) == 3
    assert not list((tmp_path / "root").glob("**/*.npz"))
    # bootstrap without the flag, then the guarded daily job passes
    assert L.main(["rollup", "--day", "2026-10-06", "--out-root", root, *base]) == 0
    assert L.main(["rollup", "--day", "2026-10-07", "--require-prev-day", "--out-root", root, *base]) == 0
    capsys.readouterr()


# --------------------------------------------------------------------------------------------
# round-1 review fixes (PR #502): bounded as-of memory, pruning, staleness, atomicity, checks
# --------------------------------------------------------------------------------------------


def _old_reduce_sorted(th, cols):
    """The pre-review in-memory fold (53da60f), kept here as the byte reference for the streamed build."""
    if len(th) == 0:
        return {"th": th.astype(np.uint64), **{c: np.zeros(0, np.int64) for c in cols}}
    order = np.argsort(th, kind="stable")
    ths = th[order]
    starts = np.concatenate(([0], np.flatnonzero(ths[1:] != ths[:-1]) + 1))
    out = {"th": ths[starts]}
    for c, v in cols.items():
        out[c] = np.add.reduceat(v[order], starts).astype(np.int64, copy=False)
    return out


def _old_merge_asof(prev, daily):
    d_cols = {c: daily[c] for c in L.DAILY_COLS}
    d_cols["buy_q"] = L.grid_units(daily["buy_lamports"])
    d_cols["cash_q"] = L.grid_units(daily["cash_lamports"])
    d_cols["ndays"] = np.ones(len(daily["th"]), np.int64)
    if prev is None:
        return _old_reduce_sorted(daily["th"], d_cols)
    th = np.concatenate([prev["th"], daily["th"]])
    return _old_reduce_sorted(th, {c: np.concatenate([prev[c], d_cols[c]]) for c in L.ASOF_COLS})


def _old_files_sha(cum, d: Path) -> dict[str, str]:
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in ("th",) + L.ASOF_COLS:
        np.save(d / f"{name}.npy", np.ascontiguousarray(cum[name]), allow_pickle=False)
        out[f"{name}.npy"] = L.file_sha256(d / f"{name}.npy")
    return out


def write_daily(root: Path, day: str, arrays: dict) -> None:
    """A daily file plus the manifest field the as-of step reads (no duckdb, any wallet count)."""
    npz, man = L.daily_paths(root, day)
    npz.parent.mkdir(parents=True, exist_ok=True)
    L.write_npz_deterministic(npz, arrays)
    man.write_text(json.dumps({"content_sha256": L.content_sha256(arrays)}))


def rand_daily(rng, universe: np.ndarray, k: int) -> dict:
    th = np.sort(rng.choice(universe, size=k, replace=False)).astype(np.uint64)
    buy = rng.integers(0, 10**12, k, dtype=np.int64)
    sell = rng.integers(0, 10**12, k, dtype=np.int64)
    nm = rng.integers(1, 9, k, dtype=np.int64)
    nrt = rng.integers(0, 9, k, dtype=np.int64) % (nm + 1)
    return {
        "th": th, "n": nm + rng.integers(0, 40, k, dtype=np.int64), "nm": nm, "nbond": rng.integers(0, 2, k, dtype=np.int64),
        "nwin": nrt // 2, "nrt": nrt, "buy_lamports": buy, "sell_lamports": sell, "cash_lamports": sell - buy,
    }


def _universe(rng, n: int) -> np.ndarray:
    u = rng.integers(0, 2**64 - 1, n, dtype=np.uint64, endpoint=True)
    return np.unique(np.concatenate([u, np.array([0, 1, 2**63, 2**64 - 1], np.uint64)]))


def test_merge_th_edges():
    a = np.array([5, 10, 20], np.uint64)
    for b, want in (
        ([], [5, 10, 20]),
        ([1, 2], [1, 2, 5, 10, 20]),
        ([25, 30], [5, 10, 20, 25, 30]),
        ([5, 10, 20], [5, 10, 20]),
        ([0, 5, 7, 11, 12, 20, 2**64 - 1], [0, 5, 7, 10, 11, 12, 20, 2**64 - 1]),
    ):
        u, is_new = L._merge_th(a, np.array(b, np.uint64))
        assert u.tolist() == want and u.dtype == np.uint64
        assert sorted(u[is_new].tolist()) == sorted(set(b) - set(a.tolist()))
    u, is_new = L._merge_th(np.zeros(0, np.uint64), np.array([3, 4], np.uint64))
    assert u.tolist() == [3, 4] and is_new.all()


def test_streamed_asof_equals_the_old_in_memory_fold_bytes(tmp_path):
    """Review 1, as-of memory: the column-streamed merge gives the bytes of the old fold, incrementally and from scratch."""
    rng = np.random.default_rng(11)
    uni = _universe(rng, 6000)
    root = tmp_path / "root"
    days = [f"2026-09-{d:02d}" for d in range(1, 7)]
    dailies = []
    for d, k in zip(days, (3000, 1, 2500, 0, 10, 3500)):  # a one-wallet day, an empty day, heavy overlap
        a = rand_daily(rng, uni, k)
        write_daily(root, d, a)
        dailies.append(a)
    cum = None
    for i in range(len(days)):
        nxt = (dt.date.fromisoformat(days[i]) + dt.timedelta(days=1)).isoformat()
        cum = _old_merge_asof(cum, dailies[i])
        want = _old_files_sha(cum, tmp_path / f"ref-{nxt}")
        inc = L.build_asof(root, nxt)
        assert inc["mode"] == ("fold" if i == 0 else "incremental")
        assert json.loads((L.asof_dir(root, nxt) / "MANIFEST.json").read_text())["files"] == want, nxt
    fold = L.build_asof(root, "2026-09-07", force=True, use_prev=False)
    assert fold["mode"] == "fold" and fold["content_sha256"] == inc["content_sha256"]
    assert json.loads((L.asof_dir(root, "2026-09-07") / "MANIFEST.json").read_text())["files"] == want
    led = L.AsofLedger.open(L.asof_dir(root, "2026-09-07"), verify=True)
    assert led.manifest["saturated"] == {"buy_lamports": 0, "sell_lamports": 0}


def test_streamed_asof_memory_is_bounded(tmp_path):
    """Review 1, as-of memory: a one-day incremental over a large snapshot allocates a few th-sized arrays, not the
    snapshot several times over (the old fold peaked at about 3.3x the snapshot on research-0)."""
    import tracemalloc

    rng = np.random.default_rng(12)
    n = 300_000
    uni = np.unique(rng.integers(0, 2**64 - 1, 2 * n, dtype=np.uint64))
    root = tmp_path / "root"
    write_daily(root, "2026-09-01", rand_daily(rng, uni, n))
    write_daily(root, "2026-09-02", rand_daily(rng, uni, 20_000))
    L.build_asof(root, "2026-09-02")
    snap_bytes = sum(p.stat().st_size for p in L.asof_dir(root, "2026-09-02").glob("*.npy"))
    tracemalloc.start()
    try:
        res = L.build_asof(root, "2026-09-03")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert res["mode"] == "incremental"
    assert peak < 0.35 * snap_bytes, (peak, snap_bytes)


def test_lamport_columns_saturate_and_other_columns_refuse_to_wrap(tmp_path):
    """Found while fixing review 1: research-0's cumulative buy_lamports was 0.728 of int64 max after 38 days; plain int64
    addition would wrap. buy/sell saturate (order-independent), signed columns refuse."""
    big = L.I64_MAX - 10
    th = np.array([7, 9], np.uint64)

    def day(buy, sell, cash=None):
        buy, sell = np.array(buy, np.int64), np.array(sell, np.int64)
        one = np.ones(2, np.int64)
        return {"th": th, "n": one, "nm": one, "nbond": 0 * one, "nwin": 0 * one, "nrt": 0 * one, "buy_lamports": buy,
                "sell_lamports": sell, "cash_lamports": sell - buy if cash is None else np.array(cash, np.int64)}

    shas = []
    for order, root in (((0, 1, 2), tmp_path / "a"), ((2, 1, 0), tmp_path / "b")):
        src = [day([big, 5], [0, 5], cash=[0, 0]), day([100, 5], [big, 5], cash=[0, 0]), day([3, 5], [100, 5], cash=[0, 0])]
        for k, i in enumerate(order):
            write_daily(root, f"2026-09-0{k + 1}", src[i])
        inc = [L.build_asof(root, f"2026-09-0{k}") for k in (2, 3, 4)][-1]
        fold = L.build_asof(root, "2026-09-04", force=True, use_prev=False)
        assert inc["mode"] == "incremental" and inc["content_sha256"] == fold["content_sha256"]
        led = L.AsofLedger.open(L.asof_dir(root, "2026-09-04"))
        assert led.cols["buy_lamports"].tolist() == [L.I64_MAX, 15]
        assert led.cols["sell_lamports"].tolist() == [L.I64_MAX, 15]
        assert led.manifest["saturated"] == {"buy_lamports": 1, "sell_lamports": 1}
        shas.append(led.manifest["content_sha256"])
    assert shas[0] == shas[1]
    # cash is signed: it is not saturated, a wrap is refused and nothing is written
    root = tmp_path / "c"
    write_daily(root, "2026-09-01", day([0, 0], [0, 0], cash=[big, 0]))
    write_daily(root, "2026-09-02", day([0, 0], [0, 0], cash=[100, 0]))
    with pytest.raises(L.Refused, match="cash_lamports overflows"):
        L.build_asof(root, "2026-09-03")
    assert not list((root / "asof").glob("asof-2026-09-03*"))


def test_keep_asof_prunes_old_snapshots_but_never_the_new_one_or_its_base(tmp_path):
    """Review 1, pruning: --keep-asof N after a successful build."""
    rng = np.random.default_rng(13)
    uni = _universe(rng, 500)
    root = tmp_path / "root"
    for d in range(1, 7):
        write_daily(root, f"2026-09-0{d}", rand_daily(rng, uni, 100))
    for d in range(2, 7):
        L.build_asof(root, f"2026-09-0{d}")
    assert L.list_asof_days(root) == [f"2026-09-0{d}" for d in range(2, 7)]
    assert L.prune_asof(root, 0) == [] and len(L.list_asof_days(root)) == 5  # 0 keeps all
    assert L.prune_asof(root, 2, protect=["2026-09-06", "2026-09-05"]) == ["2026-09-02", "2026-09-03", "2026-09-04"]
    assert L.list_asof_days(root) == ["2026-09-05", "2026-09-06"]
    # keep 1 still keeps the incremental base of the snapshot just built
    assert L.main(["asof", "--day", "2026-09-07", "--out-root", str(root), "--keep-asof", "1"]) == 0
    assert L.list_asof_days(root) == ["2026-09-06", "2026-09-07"]


def test_rollup_prunes_by_default_and_reports_memory(tmp_path, capsys):
    tip = tmp_path / "tip"
    days = ["2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07"]
    for i, d in enumerate(days):
        write_tip_day(tip, d, make_rows(40 + i, 60))
    write_tip_hour(tip, "2026-10-08T00", make_rows(50, 3))
    root = tmp_path / "root"
    base = ["--out-root", str(root), "--tip-dir", str(tip), "--threads", "1", "--mem-gb", "1", "--tmp-dir", str(tmp_path / "duck")]
    for d in days:
        assert L.main(["rollup", "--day", d, *base]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["peak_rss_mb"] > 0 and out["pruned_asof"] == ["2026-10-05"]
    assert L.list_asof_days(root) == ["2026-10-06", "2026-10-07", "2026-10-08"]  # default keep 3


def test_force_replace_swaps_atomically_and_cleans_crash_leftovers(tmp_path, monkeypatch):
    """Review 1, atomicity (c): a replaced snapshot is renamed aside first and removed only after the new one is in."""
    rng = np.random.default_rng(14)
    uni = _universe(rng, 300)
    root = tmp_path / "root"
    for d in (1, 2):
        write_daily(root, f"2026-09-0{d}", rand_daily(rng, uni, 80))
    L.build_asof(root, "2026-09-03")
    target = L.asof_dir(root, "2026-09-03")
    # leftovers of a crashed run are removed by the next build (it holds the lock)
    (root / "asof" / "asof-2026-09-03.tmp424242").mkdir()
    (root / "asof" / "asof-2026-09-02.old424242").mkdir()
    seen = []
    real_rmtree = L.shutil.rmtree

    def rmtree(p, *a, **k):
        seen.append((Path(p).name, (target / "MANIFEST.json").is_file()))
        return real_rmtree(p, *a, **k)

    monkeypatch.setattr(L.shutil, "rmtree", rmtree)
    assert L.build_asof(root, "2026-09-03", force=True)["status"] == "built"
    old = [s for s in seen if ".old" in s[0] and "424242" not in s[0]]
    assert old and all(live for _, live in old)  # the target held the new snapshot when the old one was removed
    assert sorted(p.name for p in (root / "asof").iterdir()) == ["asof-2026-09-03"]
    L.AsofLedger.open(target, verify=True)


def test_lock_refuses_a_second_writer(tmp_path):
    import fcntl

    rng = np.random.default_rng(15)
    root = tmp_path / "root"
    write_daily(root, "2026-09-01", rand_daily(rng, _universe(rng, 100), 50))
    with open(root / ".lock", "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # another process's build, as far as flock can tell
        with pytest.raises(L.Refused, match="held by another"):
            L.build_asof(root, "2026-09-02")
        assert L.main(["asof", "--day", "2026-09-02", "--out-root", str(root)]) == 3
    assert L.build_asof(root, "2026-09-02")["status"] == "built"  # released
    with L.root_lock(root):  # re-entrant inside one process
        assert L.build_asof(root, "2026-09-02")["status"] == "exists"


def test_consumer_staleness_guard(tmp_path):
    """Review 1, consumer contract: a decision on day D must use asof-D; before the nightly build it is a refusal."""
    rng = np.random.default_rng(16)
    root = tmp_path / "root"
    write_daily(root, "2026-10-06", rand_daily(rng, _universe(rng, 100), 50))
    L.build_asof(root, "2026-10-07")
    led = L.AsofLedger.open_for_day(root, "2026-10-07", verify=True)
    assert led.asof_day == "2026-10-07" and led.require_asof_day("2026-10-07") is led
    with pytest.raises(L.StaleLedger, match="newest: 2026-10-07"):
        L.AsofLedger.open_for_day(root, "2026-10-08", retries=2, wait_s=0.0)  # 10-08 between 00:00Z and the rollup
    with pytest.raises(L.StaleLedger, match="not for decision day 2026-10-08"):
        led.require_asof_day("2026-10-08")
    assert issubclass(L.StaleLedger, L.Refused)


def test_check_command(tmp_path, capsys):
    """Review 1, runbook alerting and seeding: `check` exits 3 on a missing snapshot, a bad daily file, or growth."""
    rng = np.random.default_rng(17)
    uni = _universe(rng, 400)
    root = tmp_path / "root"
    for d in (1, 2):
        write_daily(root, f"2026-09-0{d}", rand_daily(rng, uni, 100))
    L.build_asof(root, "2026-09-03")
    r = str(root)
    assert L.main(["check", "--out-root", r, "--asof-day", "2026-09-03", "--verify", "--daily"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["daily_verified"] == 2 and out["asof"]["asof_day"] == "2026-09-03" and out["asof"]["verified"]
    wallets = out["asof"]["wallets"]
    assert L.main(["check", "--out-root", r, "--asof-day", "2026-09-04"]) == 3  # the nightly did not run
    assert L.main(["check", "--out-root", r, "--max-wallets", str(wallets - 1)]) == 3
    assert L.main(["check", "--out-root", r, "--max-wallets", str(wallets)]) == 0
    # a copied-in daily file that does not match its manifest fails the seeding check
    L.daily_paths(root, "2026-09-02")[1].write_text(json.dumps({"content_sha256": "0" * 64}))
    capsys.readouterr()
    assert L.main(["check", "--out-root", r, "--daily"]) == 3
    assert "content hash" in capsys.readouterr().err


def test_wl_parquet_written_before_manifest_and_on_rerun(tmp_path, monkeypatch):
    """Review 1, idempotency (a): the manifest is the commit marker; a rerun writes a missing wl parquet."""
    tape = tmp_path / "tape"
    write_tape(tape, DAY, make_rows(18, 400))
    L.build_day(DAY, "tape", tmp_path / "ref", tape_dir=tape, wl_parquet_dir=tmp_path / "ref_wl")
    ref_sha = L.file_sha256(tmp_path / "ref_wl" / f"{DAY}.parquet")
    # built without the parquet, rerun with it: the parquet comes from the npz, same bytes as a fresh build
    L.build_day(DAY, "tape", tmp_path / "a", tape_dir=tape)
    res = L.build_day(DAY, "tape", tmp_path / "a", tape_dir=tape, wl_parquet_dir=tmp_path / "a_wl")
    assert res["status"] == "exists" and res["wl_parquet"].startswith("written")
    assert L.file_sha256(tmp_path / "a_wl" / f"{DAY}.parquet") == ref_sha
    # a refused parquet leaves the day unfinished (no manifest), so a plain rerun builds it
    real = L.write_wl_parquet

    def refuse(*a, **k):
        raise L.Refused("SQL grid columns differ (simulated)")

    monkeypatch.setattr(L, "write_wl_parquet", refuse)
    with pytest.raises(L.Refused, match="simulated"):
        L.build_day(DAY, "tape", tmp_path / "b", tape_dir=tape, wl_parquet_dir=tmp_path / "b_wl")
    assert not L.daily_paths(tmp_path / "b", DAY)[1].exists()
    monkeypatch.setattr(L, "write_wl_parquet", real)
    res = L.build_day(DAY, "tape", tmp_path / "b", tape_dir=tape, wl_parquet_dir=tmp_path / "b_wl")
    assert res["status"] == "built" and L.file_sha256(tmp_path / "b_wl" / f"{DAY}.parquet") == ref_sha


def test_recheck_and_force_on_a_changed_source(tmp_path, capsys):
    """Review 1, idempotency (b): exit 4 for a daily whose rebuild differs; --force replaces it and records the old hash."""
    rows = make_rows(19, 400)
    tape = tmp_path / "tape"
    write_tape(tape, DAY, rows)
    root = tmp_path / "o"
    first = L.build_day(DAY, "tape", root, tape_dir=tape)
    npz_bytes = L.daily_paths(root, DAY)[0].read_bytes()
    base = ["day", "--day", DAY, "--adapter", "tape", "--tape-dir", str(tape), "--out-root", str(root)]
    assert L.main(base + ["--recheck"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["status"] == "rechecked_same"
    shutil.rmtree(tape)
    write_tape(tape, DAY, rows[:-50])  # the source changed
    assert L.main(base) == 0  # a plain rerun does not rebuild
    assert json.loads(capsys.readouterr().out.strip())["status"] == "exists"
    assert L.main(base + ["--recheck"]) == 4
    assert "nothing written" in capsys.readouterr().err
    assert L.daily_paths(root, DAY)[0].read_bytes() == npz_bytes
    assert L.main(base + ["--force"]) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["status"] == "rebuilt" and out["replaced_content_sha256"] == first["content_sha256"]
    assert json.loads(L.daily_paths(root, DAY)[1].read_text())["replaced_content_sha256"] == first["content_sha256"]
    other = ["day", "--day", "2026-09-11", "--adapter", "tape", "--tape-dir", str(tape), "--out-root", str(root), "--recheck"]
    assert L.main(other) == 3  # nothing to recheck


def _runbook_sh_blocks() -> list[dict]:
    """Each ```sh block of docs/runbooks/c1nf-wallet-ledger.md as {cmd: parsed args} for its tool calls."""
    import shlex

    text = (Path(__file__).resolve().parents[1] / "docs" / "runbooks" / "c1nf-wallet-ledger.md").read_text()
    out = []
    for chunk in text.split("```sh")[1:]:
        block = chunk.split("```", 1)[0].replace("\\\n", " ")
        seen = {}
        for line in block.splitlines():
            for part in line.split("&&"):
                if "tools/c1nf_wallet_ledger.py" in part:
                    argv = shlex.split(part.split("tools/c1nf_wallet_ledger.py", 1)[1])
                    args = L.build_parser().parse_args(argv)
                    seen[args.cmd] = args
        out.append(seen)
    return out


def test_runbook_job_command_parses():
    """Review 1, runbook: the nightly command in docs/runbooks/c1nf-wallet-ledger.md uses flags this CLI accepts."""
    nightly = [s for s in _runbook_sh_blocks() if "rollup" in s]
    assert len(nightly) == 1
    seen = nightly[0]
    assert set(seen) == {"rollup", "check"}
    r = seen["rollup"]
    assert r.require_prev_day and r.keep_asof == 3 and r.max_temp_gb == 8 and r.tip_dir == ["/var/lib/mal/sealed/fast-trades-tip"]
    # review 2, D2: no allow flags in the standing command; those stay per-day manager calls
    assert not r.allow_missing_hours and not r.allow_null_trader and not r.verify_sha
    assert seen["check"].max_wallets == 30_000_000 and seen["check"].asof_day


def test_runbook_seed_1005_command_parses():
    """Review 2, D1 option B step 0: 10-05 is built on research-0 from the archive, sha-checked, hours 00-04 allowed."""
    seeds = [s["day"] for s in _runbook_sh_blocks() if "day" in s and s["day"].day == "2026-10-05"]
    assert len(seeds) == 1
    d = seeds[0]
    assert d.adapter == "tip" and d.verify_sha and d.allow_missing_hours and not d.allow_open_day
    assert d.tip_dir == ["/data/mal/tip-tape-archive/fast-trades-tip"] and not d.allow_null_trader


def test_spill_cap_is_an_option(tmp_path):
    """Review 1, pruning item: the duckdb spill cap is a CLI option with a small default (was 40 GB, hard-coded)."""
    con = L.connect(threads=1, mem_gb=1, tmp_dir=tmp_path / "duck", max_temp_gb=2)
    assert con.execute("SELECT current_setting('max_temp_directory_size')").fetchone()[0] == "1.8 GiB"  # 2e9 bytes
    p = L.build_parser().parse_args(["rollup", "--day", DAY, "--out-root", str(tmp_path)])
    assert p.max_temp_gb == L.DEFAULT_MAX_TEMP_GB == 8.0 and p.keep_asof == L.DEFAULT_KEEP_ASOF == 3


# --------------------------------------------------------------------------------------------
# event-V keys on tip rows (quant-proof ruling on the C1-NF live V source, section (c) item 5)
# --------------------------------------------------------------------------------------------
# Option A stamps observe.trade_decode.EVENT_V_KEYS on tip trade rows, and main's follower writes a negative
# single `virtual_quote_reserve` where the deployed one wrote the unsigned u64 (values >= 2**63 included; the
# route 1 fix keeps that reading, so archived and future tip hours carry them). The ledger reads tip rows only
# through TIP_JSON_COLUMNS. These tests build the same day with and without the new keys and require the ledger
# arrays (values and dtypes), the row counters, the content sha and the npz bytes to be equal.

# virtual_quote_reserves is i128 and may be negative (trade_decode.event_v_fields); creator_fee_unclaimed and
# buyback_fee are u64, so values above int64 max are legal. The ledger reads none of these keys.
_EV_VQR = (-123_456_789, -1, 0, 17_584_317_180, -(2**127), 2**127 - 1, -(2**70), 2**64 + 5)
_EV_U64 = (0, 777, 2**63, 2**64 - 1)
_EV_IX = ("buy", "buy_exact_quote_in", "sell", "", 'q"uo\\te', "ünï")
# the follower's single V: signed i64 (main), None, and the unsigned u64 reading in [2**63, 2**64) (quant-proof r4 item 5)
_SINGLE_V = (-17_584_317_180, None, 0, 554_841_812, 2**63, 2**64 - 1)
_FILLER = [
    {"venue": "pumpswap", "mint": MINTS[0], "trader": "FILLER" + "z" * 38, "side": "buy", "sol_lamports": 1,
     "signature": f"f{h}", "event_index": 0}
    for h in range(24)
]


def _event_v_extra(i: int, rng: random.Random) -> dict:
    """Keys an event-V tip row may carry, in the shapes the stamper can write: the full tail (shapes 0, 1), keys
    present with JSON nulls (2), the older sell tail that stops at V, so no creator_fee_unclaimed (3), and no
    tail at all, left unstamped as event_v_missing (4). Every row also has the follower's single V."""
    shape = i % 5
    out: dict = {"virtual_quote_reserve": _SINGLE_V[i % len(_SINGLE_V)]}
    if shape == 4:
        return out
    out.update(
        virtual_quote_reserves=_EV_VQR[i % len(_EV_VQR)],
        ix_name=rng.choice(_EV_IX),
        buyback_fee=rng.choice(_EV_U64),
        fee_recipient_zero=bool(i & 1),
    )
    if shape != 3:
        out["creator_fee_unclaimed"] = rng.choice(_EV_U64)
    if shape == 2:
        out.update(ix_name=None, buyback_fee=None, creator_fee_unclaimed=None, fee_recipient_zero=None)
    return out


def _with_event_v(rows: list[dict], seed: int, shift: int = 0) -> list[dict]:
    """The same rows plus event-V keys. Every other row puts the new keys before the legacy ones."""
    rng = random.Random(seed)
    out = []
    for i, r in enumerate(rows):
        extra = _event_v_extra(i + shift, rng)
        out.append({**extra, **r} if i % 2 else {**r, **extra})
    return out


def _write_tip_layout(d: Path, hour0: list[dict], dup: list[dict], filler: list[dict], zst: bool) -> None:
    """Hour 0 holds the rows and then the duplicate lines (dedupe works inside one file); hours 1-23 and the next
    day's T00 hold one filler row each, so the day is closed and no hour is missing."""
    write_tip_hour(d, f"{DAY}T00", hour0 + dup, zst=zst)
    for h in range(1, 24):
        write_tip_hour(d, f"{DAY}T{h:02d}", [filler[h]], zst=zst)
    write_tip_hour(d, f"{NEXT}T00", [filler[0]], zst=zst)


def _hour_lines(d: Path, hour: str, zst: bool) -> list[dict]:
    if zst:
        raw = subprocess.run(["zstd", "-dc", str(d / f"trades-{hour}.jsonl.zst")], capture_output=True, check=True).stdout
        text = raw.decode()
    else:
        text = (d / f"trades-{hour}.jsonl").read_text()
    return [json.loads(x) for x in text.splitlines()]


def _assert_same_ledger(res_a: dict, res_b: dict, root_a: Path, root_b: Path) -> dict[str, np.ndarray]:
    za = L.load_daily(L.daily_paths(root_a, DAY)[0])
    zb = L.load_daily(L.daily_paths(root_b, DAY)[0])
    assert set(za) == set(zb) == {"th", *L.DAILY_COLS}
    for k in za:
        assert za[k].dtype == zb[k].dtype, k
        assert np.array_equal(za[k], zb[k]), k
    assert res_a["rows"] == res_b["rows"]
    assert res_a["wallets"] == res_b["wallets"] == len(za["th"]) > 0
    assert res_a["content_sha256"] == res_b["content_sha256"] == L.content_sha256(za)
    assert res_a["npz_sha256"] == res_b["npz_sha256"]
    return za


def test_event_v_keys_are_not_ledger_columns():
    """The premise of the two tests below: the tip adapter selects none of the new keys."""
    from observe.trade_decode import EVENT_V_KEYS

    cols = set(re.findall(r"(\w+):'", L.TIP_JSON_COLUMNS))
    assert cols == {"venue", "mint", "trader", "side", "sol_lamports", "signature", "event_index"}
    assert not cols & {*EVENT_V_KEYS, "virtual_quote_reserve"}


@pytest.mark.parametrize("zst", [False, True], ids=["jsonl", "jsonl.zst"])
@pytest.mark.parametrize("dedupe", [True, False], ids=["dedupe", "no-dedupe"])
def test_event_v_keys_leave_the_tip_ledger_unchanged(tmp_path, zst, dedupe):
    """(c) item 5: rows with the event-V keys (negative and out-of-int64 virtual_quote_reserves, u64 fees above
    int64 max, nulls, a negative single virtual_quote_reserve and single ones in [2**63, 2**64)) give the same
    ledger as the rows without them."""
    if zst and shutil.which("zstd") is None:
        pytest.skip("zstd CLI not installed")
    rows = make_rows(11, 1500)
    ev_rows = _with_event_v(rows, 11)
    # A replayed block: the same (signature, event_index) again in one hour file. In the event-V tape the
    # copy's stamp differs from the first one; the ledger dedupes on the selected columns only.
    dup = rows[:40:2]
    ev_dup = _with_event_v(dup, 99, shift=3)
    assert any(a != b for a, b in zip(ev_dup, ev_rows[:40:2]))
    ev_filler = _with_event_v(_FILLER, 12)

    plain, ev = tmp_path / "tip_plain", tmp_path / "tip_ev"
    _write_tip_layout(plain, rows, dup, _FILLER, zst)
    _write_tip_layout(ev, ev_rows, ev_dup, ev_filler, zst)
    # the keys and edge values are really in the event-V file the ledger reads
    on_disk = _hour_lines(ev, f"{DAY}T00", zst)
    vqr = [r["virtual_quote_reserves"] for r in on_disk if r.get("virtual_quote_reserves") is not None]
    assert min(vqr) == -(2**127) and max(vqr) == 2**127 - 1 and -123_456_789 in vqr
    single_v = [r["virtual_quote_reserve"] for r in on_disk]
    assert {-17_584_317_180, None, 2**63, 2**64 - 1} <= set(single_v)
    assert max(v for v in single_v if v is not None) == 2**64 - 1
    assert any("virtual_quote_reserves" not in r for r in on_disk)  # event_v_missing rows
    assert any(r.get("creator_fee_unclaimed") == 2**64 - 1 for r in on_disk)
    assert not any("virtual_quote_reserves" in r for r in _hour_lines(plain, f"{DAY}T00", zst))

    res_p = L.build_day(DAY, "tip", tmp_path / "o_plain", tip_dirs=[plain], dedupe=dedupe)
    res_e = L.build_day(DAY, "tip", tmp_path / "o_ev", tip_dirs=[ev], dedupe=dedupe)
    z = _assert_same_ledger(res_p, res_e, tmp_path / "o_plain", tmp_path / "o_ev")
    assert res_e["rows"]["rows_dedup_dropped"] == (len(dup) if dedupe else 0)
    assert daily_to_ref(z) == reference(rows + _FILLER[1:] + ([] if dedupe else dup))


def _negative_v_sell_doc() -> dict:
    """sell_v2_kept.json with its SellEvent virtual_quote_reserves set to -123456789 (the patch of
    test_walk2_event_v.test_negative_v_is_signed), under its own signature so the row is not a duplicate."""
    import base64
    import copy

    from tools.test_walk2_event_v import _load, _program_data_line

    doc = copy.deepcopy(_load("sell_v2_kept.json"))
    lines, hits = [], 0
    for ln in doc["meta"]["logMessages"]:
        if "Program data: " in ln:
            head, b64 = ln.split("Program data: ", 1)
            raw = bytearray(base64.b64decode(b64.strip()))
            if raw[:8].hex() == "3e2f370aa503dc2a":
                raw[392:408] = (-123456789).to_bytes(16, "little", signed=True)
                ln = head + _program_data_line(bytes(raw))
                hits += 1
        lines.append(ln)
    assert hits == 1
    doc["meta"]["logMessages"] = lines
    doc["signature"] = doc["signature"][:-6] + "negVxx"
    return doc


def _decoded_trades(doc: dict, *, event_v: bool) -> list[dict]:
    """rows_from_block twice, the second pass with the pools the first left unresolved (as test_walk2_event_v)."""
    from tools.pump_history_backfill import rows_from_block
    from tools.test_walk2_event_v import _block

    kw = {"event_v": True} if event_v else {}
    block = _block(doc)
    first = rows_from_block(block, {}, "t", **kw)
    pools = {r["pool"]: ("MINT" + r["pool"][:6], L.WSOL_MINT) for r in first["unresolved"] if r.get("pool")}
    return rows_from_block(block, dict(pools), "t", **kw)["trades"]


def test_decoder_event_v_rows_leave_the_tip_ledger_unchanged(tmp_path):
    """(c) item 5 on decoder output: main's rows_from_block(event_v=True) on the walk-2 decoder fixtures plus a
    negative-V sell, with the new follower's negative single V, against the event_v=False rows with a None
    single V (the ledger reads neither). Decoder fixtures only; no price, return or P&L is read."""
    from observe.trade_decode import EVENT_V_KEYS
    from tools.test_walk2_event_v import FIX, _load

    docs = [_load(p.name) for p in sorted(FIX.glob("*.json"))] + [_negative_v_sell_doc()]
    ev_rows, plain_rows = [], []
    for doc in docs:
        ev_rows += _decoded_trades(doc, event_v=True)
        plain_rows += _decoded_trades(doc, event_v=False)
    assert ev_rows and len(ev_rows) == len(plain_rows)
    # the decoder's own promise: event_v adds keys and never changes a legacy one
    assert [{k: v for k, v in r.items() if k not in EVENT_V_KEYS} for r in ev_rows] == plain_rows
    vqr = [r["virtual_quote_reserves"] for r in ev_rows if "virtual_quote_reserves" in r]
    assert -123456789 in vqr and any(v > 0 for v in vqr)
    assert any(r["venue"] == "pumpswap" for r in ev_rows) and all(r["trader"] for r in ev_rows)
    for r in ev_rows:
        r.update(source="tip", virtual_quote_reserve=-17_584_317_180 if r["venue"] == "pumpswap" else None)
    for r in plain_rows:
        r.update(source="tip", virtual_quote_reserve=None)

    # a replayed negative-V print whose second copy came back unstamped (event_v_missing); it must dedupe
    neg = next(r for r in ev_rows if r.get("virtual_quote_reserves") == -123456789)
    ev_dup = [{k: v for k, v in neg.items() if k not in EVENT_V_KEYS}]
    plain_dup = [plain_rows[ev_rows.index(neg)]]
    plain, ev = tmp_path / "tip_plain", tmp_path / "tip_ev"
    _write_tip_layout(plain, plain_rows, plain_dup, _FILLER, zst=False)
    _write_tip_layout(ev, ev_rows, ev_dup, _FILLER, zst=False)
    res_p = L.build_day(DAY, "tip", tmp_path / "o_plain", tip_dirs=[plain])
    res_e = L.build_day(DAY, "tip", tmp_path / "o_ev", tip_dirs=[ev])
    z = _assert_same_ledger(res_p, res_e, tmp_path / "o_plain", tmp_path / "o_ev")
    assert res_e["rows"]["rows_used"] == len(ev_rows) + 23 and res_e["rows"]["rows_dedup_dropped"] == 1
    assert int(z["n"].sum()) == len(ev_rows) + 23
