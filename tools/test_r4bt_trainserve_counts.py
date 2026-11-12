"""Tests for tools/r4bt_trainserve_counts.py on SYNTHETIC fixtures in tmp dirs (fake ids). Reads no tape, no clean-view, no ledger.

  /data/mal/venv/bin/python -m pytest tools/test_r4bt_trainserve_counts.py
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tools import r4bt_trainserve_counts as m

B0 = int(dt.datetime(2026, 8, 14, 12, tzinfo=dt.timezone.utc).timestamp())
fake_pda = lambda mint: "P_" + mint  # noqa: E731


@pytest.fixture()
def tmp():
    d = tempfile.mkdtemp(prefix="r4t")
    yield Path(d)
    shutil.rmtree(d)


def write_hour(root, hour_i, rows, sub="migrations", zst=False):
    t = dt.datetime(2026, 8, 14, 12, tzinfo=dt.timezone.utc) + dt.timedelta(hours=hour_i)
    p = Path(root) / sub
    p.mkdir(parents=True, exist_ok=True)
    f = p / ("migrations-%s.jsonl" % t.strftime("%Y-%m-%dT%H"))
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    if zst:
        subprocess.run(["zstd", "-q", "--rm", str(f)], check=True)


def comp(mint, bt):
    return {"type": "complete", "mint": mint, "block_time": bt, "slot": 1}


def mig(mint, bt, pool=None, quote=m.WSOL):
    return {"type": "migration", "mint": mint, "block_time": bt, "pool": pool or "P_" + mint, "quote_mint": quote}


def run(args):
    return m.main(args, pda_fn=fake_pda)


def block(root, zst=False):
    # hour files 0,1,2,4 -> block_end = hour 5; censor boundary = hour 2: M4 (hour 4) is censored
    write_hour(root, 0, [comp("M1", B0 + 10), comp("M2", B0 + 20), comp("M3", B0 + 30), {"type": "x", "mint": "Z"}], zst=zst)
    write_hour(root, 1, [mig("M1", B0 + 3600), mig("M2", B0 + 3700, pool="WRONG"), comp("M5", B0 + 3800)], zst=zst)
    write_hour(root, 2, [mig("M3", B0 + 7300, quote="OTHER"), mig("M5", B0 + 7300)], zst=zst)
    write_hour(root, 4, [comp("M4", B0 + 4 * 3600 + 5)], zst=zst)


def test_train_counts_and_no_ids(tmp):
    root = tmp / "blk"
    block(root)
    out = tmp / "o.json"
    assert run(["train", "--allow-test-path", "--out", str(out), str(root)]) == 0
    b = json.loads(out.read_text())["blocks"]["blk"]
    assert (b["complete_rows"], b["migration_rows"], b["other_types"]) == (5, 4, 1)
    assert (b["complete_mints_uncensored"], b["complete_mints_censored"], b["covered_mints"]) == (4, 1, 2)
    assert b["coverage"] == 0.5 and b["row_ratio"] == 0.8
    txt = out.read_text()
    for banned in ("M1", "M2", "M5", "P_M", "WRONG", str(B0)):
        assert banned not in txt
    assert json.loads(txt)["train_verdict"] == "DISCLOSE" and m.RULE in json.loads(txt)["rule"]


def test_train_pass_partial_flag_and_subblocks(tmp):
    root = tmp / "blk"
    write_hour(root, 0, [comp("A%d" % i, B0 + i) for i in range(20)], sub="w1/migrations")
    write_hour(root, 1, [mig("A%d" % i, B0 + 3600) for i in range(20)], sub="w2/migrations")
    write_hour(root, 6, [], sub="w2/migrations")
    out = tmp / "o.json"
    assert run(["train", "--allow-test-path", "--out", str(out), str(root)]) == 0
    r = json.loads(out.read_text())
    assert r["train_verdict"] == "PASS" and r["blocks"]["blk"]["coverage"] == 1.0
    assert all(d["partial"] for d in r["blocks"]["blk"]["per_date"])  # a 7 h block holds no whole UTC date


@pytest.mark.skipif(shutil.which("zstd") is None, reason="no zstd")
def test_train_reads_zst(tmp):
    root = tmp / "blk"
    block(root, zst=True)
    out = tmp / "o.json"
    assert run(["train", "--allow-test-path", "--out", str(out), str(root)]) == 0
    assert json.loads(out.read_text())["blocks"]["blk"]["covered_mints"] == 2


@pytest.mark.parametrize("name", ["fresh-0808", "fresh-0903", "exp011-0909", "forward-1016", "walk2", "sealed", "tape-x", "fresh-0828"])
def test_train_refuses_names_even_in_test_mode(tmp, name):
    root = tmp / name
    block(root)
    out = tmp / "o.json"
    assert run(["train", "--allow-test-path", "--out", str(out), str(root)]) == 2
    assert not out.exists()


def test_train_refuses_off_allowlist_without_flag(tmp):
    root = tmp / "blk"
    block(root)
    out = tmp / "o.json"
    assert run(["train", "--out", str(out), str(root)]) == 2 and not out.exists()
    assert run(["train", "--out", str(out), "/data/mal/clean-view/fresh-0903"]) == 2 and not out.exists()
    assert run(["train", "--out", str(out), "/data/mal/blocks/forward-1016"]) == 2 and not out.exists()


def test_train_refuses_symlink_escape_and_missing_layout(tmp):
    real = tmp / "fresh-0808"
    block(real)
    link = tmp / "innocent"
    link.symlink_to(real)
    out = tmp / "o.json"
    assert run(["train", "--allow-test-path", "--out", str(out), str(link)]) == 2
    empty = tmp / "empty"
    empty.mkdir()
    assert run(["train", "--allow-test-path", "--out", str(out), str(empty)]) == 2 and not out.exists()


def test_test_flag_refused_in_miscusi_job(tmp, monkeypatch):
    root = tmp / "blk"
    block(root)
    monkeypatch.setenv("MISCUSI_JOB_ID", "j1")
    assert run(["train", "--allow-test-path", "--out", str(tmp / "o.json"), str(root)]) == 2
    assert run(["serve", "--allow-test-path", "--counts", str(tmp / "c.jsonl"), "--out", str(tmp / "o.json")]) == 2


def crow(hour, cc, pc, rows=None):
    return {"hour": hour, "adapter_cumulative": {"rows_complete": cc, "rows_pool_create": pc, "other": 9}, "rows": rows or {}}


def hrs(day):
    return ["%sT%02d" % (day, h) for h in range(24)]


def write_counts(p, rows):
    Path(p).write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_serve_attribution_day_boundary_and_reset(tmp):
    rows, cc, pc = [crow("2026-11-07T23", 1000, 100)], 1000, 100
    for i, h in enumerate(hrs("2026-11-08")):
        if i == 10:
            cc, pc = 0, 0  # writer restart: counters drop, that hour's delta is the new value itself
        cc += 10
        pc += 9
        rows.append(crow(h, cc, pc))
    rows.append(crow("2026-11-09T00", cc + 10, pc + 9))  # next day: one hour only -> partial
    p, out = tmp / "c.jsonl", tmp / "o.json"
    write_counts(p, rows)
    assert run(["serve", "--allow-test-path", "--counts", str(p), "--out", str(out)]) == 0
    r = json.loads(out.read_text())
    d8, d9 = r["days"]  # 11-07 has only the baseline row: no delta, so no day entry
    # 11-08T00's delta (vs 11-07T23) belongs to 11-08, the date of the LATER row
    assert (d8["date"], d8["hours"], d8["full"], d8["counted"]) == ("2026-11-08", 24, True, True)
    assert (d8["complete"], d8["pool_create"]) == (240, 216)  # reset hour contributes 10 / 9, never negative
    assert d8["ratio"] == 216 / 240
    assert (d9["hours"], d9["partial"], d9["counted"]) == (1, True, False)
    assert r["serve_verdict"] == "PASS" and "no retune" in r["rule"]


def test_serve_predeploy_low_ratio_and_none_counted(tmp):
    rows, c, pc = [], 0, 0
    for h in hrs("2026-11-06") + hrs("2026-11-08"):
        c, pc = c + 10, pc + (0 if h.startswith("2026-11-06") else 8)
        rows.append(crow(h, c, pc))
    p, out = tmp / "c.jsonl", tmp / "o.json"
    write_counts(p, rows)
    assert run(["serve", "--allow-test-path", "--counts", str(p), "--out", str(out)]) == 0
    r = json.loads(out.read_text())
    assert [(x["date"], x["hours"], x["counted"]) for x in r["days"]] == [("2026-11-06", 23, False), ("2026-11-08", 24, True)]
    assert r["days"][1]["ratio"] == 0.8 and r["serve_verdict"] == "DISCLOSE"
    write_counts(p, rows[:5])
    assert run(["serve", "--allow-test-path", "--counts", str(p), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["serve_verdict"] == "DISCLOSE"  # no full post-deploy day


def test_serve_refusals(tmp):
    out = tmp / "o.json"
    p = tmp / "c.jsonl"
    write_counts(p, [crow("2026-11-08T00", 1, 1), crow("2026-11-08T00", 2, 2)])
    assert run(["serve", "--allow-test-path", "--counts", str(p), "--out", str(out)]) == 2  # duplicate hour
    write_counts(p, [{"hour": "2026-11-08T00", "rows": {}}])
    assert run(["serve", "--allow-test-path", "--counts", str(p), "--out", str(out)]) == 2  # no adapter_cumulative
    assert run(["serve", "--counts", str(p), "--out", str(out)]) == 2  # off-allowlist without the flag
    assert run(["serve", "--allow-test-path", "--counts", str(tmp / "fresh-0808.jsonl"), "--out", str(out)]) == 2
    assert not out.exists()


def test_blob_id_matches_git():
    git = subprocess.run(["git", "hash-object", m.__file__], capture_output=True, text=True)
    if git.returncode == 0:
        assert git.stdout.strip() == m.blob_id()
