"""Tests for tools/c1nf_wallet_ledger.py. Synthetic tapes only; no network, no real tape.

Run with a Python that has pytest, numpy and duckdb 1.5.6, for example
  PYTHONPATH=/data/mal/audit-1008/venv/lib/python3.12/site-packages /data/mal/venv/bin/python -m pytest tools/test_c1nf_wallet_ledger.py
"""

from __future__ import annotations

import itertools
import json
import random
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

duckdb = pytest.importorskip("duckdb")

from tools import c1nf_wallet_ledger as L  # noqa: E402

DAY = "2026-09-10"
NEXT = "2026-09-11"
MINTS = [f"MINT{i:02d}pump" for i in range(9)] + [L.WSOL_MINT]
TRADERS = [f"TRADER{i:03d}" + "x" * 30 for i in range(60)]


def make_rows(seed: int, n: int = 2500) -> list[dict]:
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
    rows = make_rows(1)
    res = build_tape_day(tmp_path, "out", rows)
    assert res["status"] == "built" and res["hours"] == 3
    arrays = L.load_daily(L.daily_paths(tmp_path / "out", DAY)[0])
    assert daily_to_ref(arrays) == reference(rows)
    assert res["rows"]["rows_null_trader"] == 1  # the NULL trader row is dropped and counted
    L.validate_daily(arrays)


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
    tip = Path(__file__).parent  # not used; write rows to a temp dir below
    del tip


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
