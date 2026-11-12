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


def crow(hour, dc, dp, cc=None, pc=None):
    """rows{} carry the hour's own counts (dc, dp); adapter_cumulative only when cc/pc are given (None = key absent, like the writer's first row)."""
    ad = {"other": 9}
    if cc is not None:
        ad["rows_complete"] = cc
    if pc is not None:
        ad["rows_pool_create"] = pc
    return {"hour": hour, "adapter_cumulative": ad, "rows": {"pumpfun|complete": dc, "pumpswap|pool_create": dp}}


def hrs(day):
    return ["%sT%02d" % (day, h) for h in range(24)]


def write_counts(p, rows):
    Path(p).write_text("".join(json.dumps(r) + "\n" for r in rows))


def serve(tmp, rows):
    p, out = tmp / "c.jsonl", tmp / "o.json"
    write_counts(p, rows)
    rc = run(["serve", "--allow-test-path", "--counts", str(p), "--out", str(out)])
    return rc, (json.loads(out.read_text()) if out.exists() else None)


def full_day(day, dc, dp, cum=True, start=(1000, 100), reset_at=None):
    rows, cc, pc = [], start[0], start[1]
    for i, h in enumerate(hrs(day)):
        if i == reset_at:
            cc, pc = 0, 0
        cc, pc = cc + dc, pc + dp
        rows.append(crow(h, dc, dp, cc if cum else None, pc if cum else None))
    return rows


def test_serve_rows_drive_attribution_day_boundary_and_reset(tmp):
    rows = [crow("2026-11-07T23", 5, 5, 1000, 100)] + full_day("2026-11-08", 10, 9, reset_at=10, start=(1000, 100))
    rows.append(crow("2026-11-09T00", 10, 9, 10, 9))
    rc, r = serve(tmp, rows)
    assert rc == 0
    d7, d8, d9 = r["days"]
    assert (d7["hours"], d7["counted"], d7["cross_check_incomplete"]) == (1, False, True)  # baseline row only
    # rows{} of 11-08 hours: 24 x (10, 9); the cumulative cross-check sees the restart at hour 10 (delta = new value) and agrees
    assert (d8["date"], d8["hours"], d8["full"], d8["counted"]) == ("2026-11-08", 24, True, True)
    assert (d8["complete"], d8["pool_create"], d8["ratio"]) == (240, 216, 216 / 240)
    assert (d8["cross_check_complete"], d8["cross_check_pool_create"], d8["methods_disagree"]) == (240, 216, False)
    assert (d9["hours"], d9["partial"], d9["counted"]) == (1, True, False)
    assert r["serve_verdict"] == "PASS" and "no retune" in r["rule"]


def test_serve_first_row_without_cumulative_key_does_not_crash(tmp):
    rows = [crow("2026-11-07T22", 3, 0, 5, None)] + [crow("2026-11-07T23", 3, 0, 8, 1)] + full_day("2026-11-08", 10, 10, start=(8, 1))
    rc, r = serve(tmp, rows)
    assert rc == 0 and r["serve_verdict"] == "PASS"
    d7 = r["days"][0]
    assert d7["cross_check_incomplete"] and d7["cross_check_ratio"] is None  # never 0 inside a ratio
    d8 = r["days"][1]
    assert d8["cross_check_incomplete"] is False and d8["cross_check_ratio"] == 1.0
    # a log with NO cumulative keys at all still works: rows{} alone drives, every day flagged
    rc, r = serve(tmp, full_day("2026-11-08", 10, 10, cum=False))
    assert rc == 0 and r["serve_verdict"] == "PASS" and r["days"][0]["cross_check_incomplete"]


def test_serve_methods_disagree_gives_disclose(tmp):
    rows = [crow("2026-11-07T23", 1, 1, 1000, 100)] + full_day("2026-11-08", 10, 9)  # rows{} 0.9 -> pass (inclusive)
    for i, r in enumerate(rows[1:]):  # cumulative counters tell a different story: pool_create grows by only 8 per hour
        r["adapter_cumulative"]["rows_pool_create"] = 100 + 8 * (i + 1)
    rc, r = serve(tmp, rows)
    d = r["days"][1]
    assert rc == 0 and d["ratio"] == 0.9 and d["cross_check_ratio"] < 0.9 and d["methods_disagree"] and not d["day_pass"]
    assert r["serve_verdict"] == "DISCLOSE"


def test_serve_thresholds_inclusive_and_rows_drive(tmp):
    rc, r = serve(tmp, full_day("2026-11-08", 10, 9, cum=False))
    assert r["days"][0]["ratio"] == 0.9 and r["serve_verdict"] == "PASS"
    rows = full_day("2026-11-08", 100, 89, cum=False)
    rc, r = serve(tmp, rows)
    assert r["serve_verdict"] == "DISCLOSE"
    rows = full_day("2026-11-08", 10, 9, cum=False)
    rows[3]["rows"]["pumpswap|pool_create"] = 8  # one hour lower -> 215/240 < 0.90
    assert serve(tmp, rows)[1]["serve_verdict"] == "DISCLOSE"


def test_serve_predeploy_low_ratio_and_none_counted(tmp):
    rows = full_day("2026-11-06", 10, 0, cum=False) + full_day("2026-11-08", 10, 8, cum=False)
    rc, r = serve(tmp, rows)
    assert [(x["date"], x["hours"], x["counted"]) for x in r["days"]] == [("2026-11-06", 24, False), ("2026-11-08", 24, True)]
    assert r["days"][1]["ratio"] == 0.8 and r["serve_verdict"] == "DISCLOSE"
    rc, r = serve(tmp, rows[:5])
    assert r["serve_verdict"] == "DISCLOSE"  # no full post-deploy day


def test_serve_refusals(tmp):
    out = tmp / "o.json"
    p = tmp / "c.jsonl"
    write_counts(p, [crow("2026-11-08T00", 1, 1), crow("2026-11-08T00", 2, 2)])
    assert run(["serve", "--allow-test-path", "--counts", str(p), "--out", str(out)]) == 2  # duplicate hour
    write_counts(p, [{"hour": "2026-11-08T00"}])
    assert run(["serve", "--allow-test-path", "--counts", str(p), "--out", str(out)]) == 2  # no rows{}
    p.write_text("not json\n")
    assert run(["serve", "--allow-test-path", "--counts", str(p), "--out", str(out)]) == 2  # parse error is exit 2, not a traceback
    assert run(["serve", "--counts", str(p), "--out", str(out)]) == 2  # off-allowlist without the flag
    assert run(["serve", "--allow-test-path", "--counts", str(tmp / "fresh-0808.jsonl"), "--out", str(out)]) == 2
    assert not out.exists()


def test_train_threshold_095_inclusive(tmp):
    root = tmp / "blk"
    write_hour(root, 0, [comp("A%d" % i, B0 + i) for i in range(20)])
    write_hour(root, 1, [mig("A%d" % i, B0 + 3600) for i in range(19)] + [mig("A19", B0 + 3600, pool="WRONG")])
    write_hour(root, 6, [])
    out = tmp / "o.json"
    assert run(["train", "--allow-test-path", "--out", str(out), str(root)]) == 0
    r = json.loads(out.read_text())
    assert r["blocks"]["blk"]["coverage"] == 0.95 and r["train_verdict"] == "PASS"


def test_train_migration_without_block_time_counted_and_still_covers(tmp):
    root = tmp / "blk"
    nb = mig("N1", 0)
    del nb["block_time"]
    write_hour(root, 0, [comp("N1", B0 + 1), nb])
    write_hour(root, 6, [])
    out = tmp / "o.json"
    assert run(["train", "--allow-test-path", "--out", str(out), str(root)]) == 0
    b = json.loads(out.read_text())["blocks"]["blk"]
    assert b["migration_rows_no_block_time"] == 1 and b["covered_mints"] == 1 and b["coverage"] == 1.0


def test_train_earliest_row_rule_and_quote_split(tmp):
    root = tmp / "blk"
    e = mig("E1", B0 + 5, pool="WRONG")
    e["slot"] = 1
    late = mig("E1", B0 + 6)
    late["slot"] = 2  # a later matching row does not rescue a wrong earliest row (engine rule); the any-row count does
    c2 = comp("E2", B0 + 7)
    c2["quote_mint"] = "OTHERQ"  # known non-WSOL complete: outside the verdict denominator, still counted
    write_hour(root, 0, [comp("E1", B0 + 4), c2, late, e, mig("E2", B0 + 8)])
    write_hour(root, 6, [])
    out = tmp / "o.json"
    assert run(["train", "--allow-test-path", "--out", str(out), str(root)]) == 0
    b = json.loads(out.read_text())["blocks"]["blk"]
    assert (b["covered_mints"], b["covered_mints_any_matching_row"], b["verdict_denominator_mints"]) == (0, 1, 1)
    assert (b["complete_mints_quote_other"], b["complete_mints_quote_absent"], b["mints_with_multiple_migration_rows"]) == (1, 1, 1)


def test_train_refuses_symlink_below_root_and_bad_row(tmp):
    root = tmp / "blk"
    block(root)
    real = tmp / "elsewhere"
    block(real)
    shutil.rmtree(root / "migrations")
    (root / "migrations").symlink_to(real / "migrations")
    out = tmp / "o.json"
    assert run(["train", "--allow-test-path", "--out", str(out), str(root)]) == 2
    bad = tmp / "bad"
    write_hour(bad, 0, [comp("M1", B0)])
    (bad / "migrations" / "migrations-2026-08-14T12.jsonl").write_text("{broken\n")
    assert run(["train", "--allow-test-path", "--out", str(out), str(bad)]) == 2 and not out.exists()


def test_blob_id_matches_git():
    git = subprocess.run(["git", "hash-object", m.__file__], capture_output=True, text=True)
    if git.returncode == 0:
        assert git.stdout.strip() == m.blob_id()
