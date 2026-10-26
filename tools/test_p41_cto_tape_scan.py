import json, os, subprocess
import tools.p41_cto_tape_scan as S


def _mk(root, block, stream, hours, rows):
    d = root / block / stream
    d.mkdir(parents=True, exist_ok=True)
    for h in hours:
        p = d / f"{stream}-{h}.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        subprocess.run(["zstd", "-q", "--rm", str(p)], check=True)


def test_window_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "CV", str(tmp_path))
    blk = "oracle-insample-2026-09-22_25"
    _mk(tmp_path, blk, "trades", ["2026-09-21T23", "2026-09-22T00", "2026-09-25T06", "2026-09-25T07"], [])
    got = [h for _, h, _ in S.hour_files(blk, "trades")]
    assert got == ["2026-09-22T00", "2026-09-25T06"]  # T07 is the Oracle live tape row's; 09-21T23 is not this block's


def test_scan_keeps_only_listed_mints(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "CV", str(tmp_path / "cv"))
    monkeypatch.setattr(S, "SOURCES", [("C", "fast-pool-2026-09-18T23_2026-09-22T00")])
    blk = "fast-pool-2026-09-18T23_2026-09-22T00"
    rows = [{"mint": "M1", "venue": "pumpswap", "quote_reserve": 5, "block_time": 1, "x": "drop"},
            {"mint": "M2", "venue": "pumpswap", "quote_reserve": 6, "block_time": 2},
            {"mint": "M11", "venue": "pumpswap", "quote_reserve": 7, "block_time": 3}]  # substring match must not leak
    for st in ("trades", "creates", "migrations"):
        _mk(tmp_path / "cv", blk, st, ["2026-09-19T00"], rows)
    mf = tmp_path / "m.txt"; mf.write_text("M1\n")
    out = tmp_path / "out"
    S.main(["--mints", str(mf), "--out", str(out)])
    tr = [json.loads(l) for l in open(out / "trades.jsonl")]
    assert [r["mint"] for r in tr] == ["M1"] and "x" not in tr[0] and tr[0]["win"] == "C"
    man = json.load(open(out / "scan_manifest.json"))
    assert man["rows"] == {"creates": 1, "migrations": 1, "trades": 1} and man["hour_counts"] == {"C": 1}


def test_forbidden_paths_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "CV", str(tmp_path / "meta-capture"))
    try:
        list(S.hour_files("explore-0814/w1", "trades"))
    except AssertionError:
        return
    raise AssertionError("forbidden path not refused")
