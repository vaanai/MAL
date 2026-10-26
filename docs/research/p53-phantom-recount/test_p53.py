"""Tests for the P53 phantom recount harness (p53_mark.py, p53_run.py). Run: /data/mal/venv/bin/python -m pytest docs/research/p53-phantom-recount -q"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p53_mark  # noqa: E402
import p53_run  # noqa: E402

SIGNER = "BUpWiWy8Tqed4jy86dr7ZpoaE747DpQsrGnwmtpMwfBr"


def row(**k):
    base = {"type": "trade", "prog": "pumpfun", "trader": "T", "slot": 10, "t_recv_ms": 1_000, "signature": "s1"}
    base.update(k)
    return json.dumps(base) + "\n"


def tape(tmp_path):
    a = tmp_path / "tape-a.jsonl"
    a.write_text("".join([
        row(signature="s1", slot=10),                       # 0 keep
        row(signature="1" * 64),                            # 1 sig_ones
        row(signature="s2", trader=SIGNER),                 # 2 signer
        row(signature="s1", slot=10, prog="pumpswap"),     # 3 keep: same sig, same slot (multi-leg tx)
        "not json\n",                                       # 4 kept, bad_json
        json.dumps({"type": "hb", "prog": "pumpfun", "signature": None, "slot": 11, "t_recv_ms": 1_500}) + "\n",  # 5 keep
    ]))
    b = tmp_path / "tape-b.jsonl"
    b.write_text("".join([
        row(signature="s1", slot=99, t_recv_ms=2_000),     # 0 repeat_slot (seen in file a at slot 10)
        row(signature="s3", slot=99, t_recv_ms=2_000),     # 1 keep
        row(signature="s4", type="create", trader=SIGNER, t_recv_ms=2_000),  # 2 keep: signer rule is trade rows only
    ]))
    return [str(a), str(b)]


def test_mark_kinds_and_history_across_files(tmp_path):
    res = p53_mark.mark(tape(tmp_path), allow_any=True)
    assert res["drops"]["tape-a.jsonl"] == [[1, "sig_ones"], [2, "signer"]]
    assert res["drops"]["tape-b.jsonl"] == [[0, "repeat_slot"]]
    assert res["filter_counts"] == {"sig_ones": 1, "signer": 1, "repeat_slot": 1}
    assert res["hours"]["tape-a.jsonl"]["rows"]["bad_json"] == 1
    assert res["hours"]["tape-a.jsonl"]["lines"] == 6
    assert res["hours"]["tape-b.jsonl"]["drops"] == {"repeat_slot|trade|pumpfun": 1}


def test_mark_refuses_out_of_order_and_unplanned_paths(tmp_path):
    a, b = tape(tmp_path)
    with pytest.raises(SystemExit):
        p53_mark.mark([b, a], allow_any=True)
    with pytest.raises(SystemExit):
        list(p53_mark.lines_of("/data/mal/arena/tape/tape-2026-10-25T00.jsonl.zst"))
    with pytest.raises(SystemExit):
        list(p53_mark.lines_of("/data/mal/arena/tape/tape-2026-10-19T07.jsonl.zst"))
    assert p53_mark.PATH_RE.match("/data/mal/arena/tape/tape-2026-10-24T23.jsonl.zst")
    assert p53_mark.PATH_RE.match("/data/mal/arena/tape/tape-2026-10-19T08.jsonl.zst")


CONSUMER = '''
import json, subprocess, sys
out, files = sys.argv[1], sys.argv[2:]
n = []
for f in files:
    p = subprocess.Popen(["zstd", "-dc", f], stdout=subprocess.PIPE)
    n.append([l.decode() for l in p.stdout])
    assert p.wait() == 0
json.dump(n, open(out, "w"))
'''


def zst(tmp_path, plain):
    z = plain + ".zst"
    subprocess.run(["zstd", "-q", "-f", plain, "-o", z], check=True)
    return z


def test_run_skips_marked_lines_and_off_is_identity(tmp_path, monkeypatch):
    files = tape(tmp_path)
    res = p53_mark.mark(files, allow_any=True)
    zs = [zst(tmp_path, f) for f in files]
    res["drops"] = {os.path.basename(f) + ".zst": v for f, v in zip(files, res["drops"].values())}
    dj = tmp_path / "drops.json"
    dj.write_text(json.dumps(res))
    script = tmp_path / "consumer.py"
    script.write_text(CONSUMER)
    log = tmp_path / "skip.jsonl"
    monkeypatch.setenv("P53_SKIPLOG", str(log))
    off, on = tmp_path / "off.json", tmp_path / "on.json"
    p53_run.main(["p53_run.py", "--off", str(script), str(off)] + zs)
    p53_run.main(["p53_run.py", "--drops", str(dj), str(script), str(on)] + zs)
    raw = [open(f).readlines() for f in files]
    assert json.load(open(off)) == raw
    got = json.load(open(on))
    assert got[0] == [raw[0][i] for i in (0, 3, 4, 5)]
    assert got[1] == [raw[1][i] for i in (1, 2)]
    recs = [json.loads(x) for x in log.read_text().splitlines()]
    assert [r["skipped"] for r in recs] == [0, 0, 2, 1]
    assert all(r["skipped"] == r["expected"] for r in recs)
    assert subprocess.Popen is p53_run._REAL_POPEN  # restored


def test_run_sha_pin_refuses(tmp_path):
    script = tmp_path / "consumer.py"
    script.write_text(CONSUMER)
    with pytest.raises(SystemExit):
        p53_run.main(["p53_run.py", "--off", "--sha256", "0" * 64, str(script), str(tmp_path / "o.json")])


def test_wrapper_refuses_non_iteration_reads(tmp_path):
    p53_run.DROPS.clear()
    s = p53_run.SkipLines(iter([b"a\n"]), "x")
    with pytest.raises(RuntimeError):
        s.read()


MP_CONSUMER = '''
import json, multiprocessing as mp, subprocess, sys


def scan(f):
    p = subprocess.Popen(["zstd", "-dc", f], stdout=subprocess.PIPE)
    out = [l.decode() for l in p.stdout]
    assert p.wait() == 0
    return out


def main():
    out, files = sys.argv[1], sys.argv[2:]
    with mp.get_context("fork").Pool(2) as pool:
        json.dump(pool.map(scan, files), open(out, "w"))


if __name__ == "__main__":
    main()
'''


def test_run_wrapper_reaches_forked_pool_workers(tmp_path, monkeypatch):
    files = tape(tmp_path)
    res = p53_mark.mark(files, allow_any=True)
    zs = [zst(tmp_path, f) for f in files]
    res["drops"] = {os.path.basename(f) + ".zst": v for f, v in zip(files, res["drops"].values())}
    dj = tmp_path / "drops.json"
    dj.write_text(json.dumps(res))
    script = tmp_path / "mp_consumer.py"
    script.write_text(MP_CONSUMER)
    log = tmp_path / "skip.jsonl"
    monkeypatch.setenv("P53_SKIPLOG", str(log))
    on = tmp_path / "on.json"
    p53_run.main(["p53_run.py", "--drops", str(dj), str(script), str(on)] + zs)
    raw = [open(f).readlines() for f in files]
    assert json.load(open(on)) == [[raw[0][i] for i in (0, 3, 4, 5)], [raw[1][i] for i in (1, 2)]]
    recs = sorted((json.loads(x) for x in log.read_text().splitlines()), key=lambda r: r["file"])
    assert [(r["skipped"], r["expected"]) for r in recs] == [(2, 2), (1, 1)]
