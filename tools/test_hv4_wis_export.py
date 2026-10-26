"""hv4_wis_export (DEC-031 Am.15 item 6, QP-VERDICT-915 item 6 conditions a to d). SYNTHETIC fixtures only: a fake ledger and hour tables from test_hv4_am15_read, a read
recorded through the real hv4_am15_read wrapper, and a fake `arena_worst_gaps` command (its real argv contract is checked against the real tool's refusal). Nothing real is opened."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from tools import arena_score as S_DISK
from tools import hv4_wis_export as X
from tools import hv4_am15_read as W
from tools.arena_paper import TEST_ENV
from tools.test_hv4_am15_read import AS_OF, B_NEW, book, health, instant, setup, wrun  # noqa: F401  (also applies the autouse monkeypatches below)
from tools.test_hv4_am15_read import _disk  # noqa: F401
from tools.test_arena_score import DAY0, good_pnls

os.environ[TEST_ENV] = "1"
BOT = "hv4-CON-F"

FAKE_GAPS = '''import json, sys
a = sys.argv[1:]
d = dict(zip(a[0::2], a[1::2]))
mode = open(d["--trades"] + ".mode").read().strip() if __import__("os").path.exists(d["--trades"] + ".mode") else "ok"
ids = [json.loads(l)["trade_id"] for l in open(d["--trades"]) if l.strip()]
gap = {t: (500_000.0 if i % 2 == 0 else 0.0) for i, t in enumerate(ids)}
if mode == "partial":
    gap.pop(ids[0])
det = {"n": len(ids), "measured": len(gap), "left_out": {ids[0]: "x"} if mode in ("partial", "rc4") else {}, "nonzero": sum(1 for v in gap.values() if v > 0), "clipped_negative": 0}
open(d["--out"], "w").write(json.dumps(gap, sort_keys=True))
open(d["--detail"], "w").write(json.dumps(det))
sys.exit(4 if mode == "rc4" else 0)
'''


class World:
    def __init__(self, tmp: Path, rows=None):
        self.tmp = tmp
        self.root, self.reg = setup(tmp, rows or book())
        self.ip, _ = instant(tmp, self.root)
        self.rd, self.od = tmp / "reads", tmp / "out"
        self.hl = health()
        wrun(tmp, self.root, self.reg, self.ip, record=True, reads_dir=str(self.rd))
        self.script = tmp / "fake_gaps.py"
        self.script.write_text(FAKE_GAPS)
        self.cmd = (sys.executable, str(self.script))
        self.tape = tmp / "tape"
        self.tape.mkdir()
        self.rec_bytes = (self.rd / f"{BOT}.json").read_bytes()

    def build(self, **kw):
        a = dict(bot=BOT, reads_dir=str(self.rd), instant_path=self.ip, ledger_root=self.root, registry=self.reg, health_texts=[self.hl[0]], health_shas=[self.hl[1]],
                 as_of_ms=AS_OF, restarts_text="", tape_dir=str(self.tape), out_dir=str(self.od), gaps_cmd=self.cmd)
        a.update(kw)
        return X.build(**a)


@pytest.fixture()
def w(tmp_path):
    return World(tmp_path)


def test_the_fixture_read_is_a_recorded_pass(w):
    rec = json.loads(w.rec_bytes)
    assert rec["status"] == "PASS" and rec["read_boundary_ms"] == B_NEW


def test_export_reproduces_b_n_and_subcells_and_writes_wis_once(w):
    rec = json.loads(w.rec_bytes)
    out = w.build()
    assert out["read_boundary_ms"] == B_NEW == rec["read_boundary_ms"]
    book_ = rec["books"]["0.25"]
    assert out["n"] == book_["n_trades"] == 120
    assert sorted(out["subcells"]) == sorted(rec["deciding"]["subcells"]) and len(out["subcells"]) == 4
    for k, v in out["subcells"].items():
        assert v == {f: book_["cells"][k][f] for f in ("n", "mean_sol", "total_sol")} and v["n"] == 120
    assert X.reproduced(out["reproduction"]) and out["reproduction"]["B_equal"]
    wis = w.od / f"{BOT}.wis.json"
    on_disk = json.loads(wis.read_text())
    assert on_disk["schema"] == X.SCHEMA and on_disk["bot_id"] == BOT and on_disk["report_only"] and on_disk["never_changes_the_verdict"]
    assert on_disk["worst_in_slot"]["measured"] is True and on_disk["gaps"]["arena_worst_gaps_rc"] == 0 and on_disk["credits"] == {"helius": 0, "rpc": 0, "note": on_disk["credits"]["note"]}
    assert stat.S_IMODE(wis.stat().st_mode) == 0o444 and out["wis_sha256"] == X._sha_file(wis)
    # the recorded read was not touched
    assert (w.rd / f"{BOT}.json").read_bytes() == w.rec_bytes and sorted(p.name for p in w.rd.iterdir()) == [f"{BOT}.json"]
    # write-once: a second run refuses before any work
    with pytest.raises(X.Refused, match="written once"):
        w.build()


def test_trade_export_is_exactly_the_scored_set_and_the_gaps_tool_got_it(w):
    out = w.build()
    tp = w.od / f"{BOT}.wis-trades.jsonl"
    rows = [json.loads(x) for x in tp.read_text().splitlines()]
    assert len(rows) == 120 and all(r["kind"] == "trade" and r["stake_sol"] == 0.25 for r in rows) and len({r["trade_id"] for r in rows}) == 120
    assert out["trade_export"]["sha256"] == X._sha_file(tp) and stat.S_IMODE(tp.stat().st_mode) == 0o444
    gaps = json.loads((w.od / f"{BOT}.wis-gaps.json").read_text())
    assert set(gaps) == {r["trade_id"] for r in rows}


def test_figure_is_worst_in_slot_cells_over_the_same_trades_and_gaps(w):
    out = w.build()
    rows = [json.loads(x) for x in (w.od / f"{BOT}.wis-trades.jsonl").read_text().splitlines()]
    gaps = S_DISK.load_worst_gaps(w.od / f"{BOT}.wis-gaps.json")
    again = S_DISK.worst_in_slot_cells(rows, gaps, ["total", "sc"])
    assert again == json.loads(json.dumps(out["worst_in_slot"]))
    assert out["item6"]["canary_proposal_blocked_by_item6"] == (not again["mean_sol"] > 0)


@pytest.mark.parametrize("status", ["FAIL", "NOT_DECIDABLE", "REPORT_ONLY", "PENDING"])
def test_only_a_recorded_pass_runs(w, status):
    p = w.rd / f"{BOT}.json"
    rec = json.loads(p.read_text())
    p.chmod(0o644)
    p.write_text(json.dumps(dict(rec, status=status)))
    with pytest.raises(X.Refused, match="not PASS"):
        w.build()
    assert not w.od.exists()  # nothing computed, nothing written


def test_no_recorded_read_refuses(tmp_path):
    w = World(tmp_path)
    (w.rd / f"{BOT}.json").unlink()
    with pytest.raises(X.Refused, match="no readable recorded read"):
        w.build()
    assert not w.od.exists()


def test_a_trade_set_that_does_not_reproduce_is_refused_and_writes_nothing(w):
    # the recorded read says n = 120; tamper the recorded n, mean and total in turn
    p = w.rd / f"{BOT}.json"
    base = json.loads(p.read_text())
    k = base["deciding"]["subcells"][0]
    for field, bad in (("mean_sol", base["books"]["0.25"]["cells"][k]["mean_sol"] + 1e-9), ("total_sol", 0.0), ("n", 125)):
        rec = json.loads(json.dumps(base))
        rec["books"]["0.25"]["cells"][k][field] = bad
        p.chmod(0o644)
        p.write_text(json.dumps(rec))
        with pytest.raises(X.Refused, match="does not reproduce"):
            w.build()
        assert not w.od.exists() or not (w.od / f"{BOT}.wis.json").exists()
    rec = json.loads(json.dumps(base))
    rec["books"]["0.25"]["n_trades"] = 121
    p.write_text(json.dumps(rec))
    with pytest.raises(X.Refused, match="does not reproduce"):
        w.build()
    rec = json.loads(json.dumps(base))
    rec["am6"]["cut_sha256"] = "0" * 64
    p.write_text(json.dumps(rec))
    with pytest.raises(X.Refused, match="cut_sha_equal"):
        w.build()
    assert not (w.od / f"{BOT}.wis-trades.jsonl").exists()


def test_b_must_agree_across_the_record_the_instant_file_and_the_read(w):
    p = w.rd / f"{BOT}.json"
    rec = json.loads(p.read_text())
    rec["am15"]["read_instant_ms"] = B_NEW + 86_400_000
    p.chmod(0o644)
    p.write_text(json.dumps(rec))
    with pytest.raises(X.Refused, match="B mismatch"):
        w.build()


def test_a_different_ledger_or_health_table_does_not_reproduce(w, tmp_path):
    other = health({DAY0 + 2 * 86_400_000 + 3 * 3_600_000: ["decision-input"]})
    with pytest.raises(X.Refused):
        w.build(health_texts=[other[0]], health_shas=[other[1]])
    assert not w.od.exists()


def test_gaps_tool_rc_must_be_zero_and_every_trade_needs_a_gap(w):
    tp = w.od / f"{BOT}.wis-trades.jsonl"
    w.od.mkdir()
    # the tool's own mode file sits beside the trade file it will read
    Path(str(tp) + ".mode").write_text("rc4")
    with pytest.raises(X.Refused, match="rc 4"):
        w.build()
    assert not (w.od / f"{BOT}.wis.json").exists()
    Path(str(tp) + ".mode").write_text("partial")
    with pytest.raises(X.Refused, match="not measured"):
        w.build()
    assert not (w.od / f"{BOT}.wis.json").exists()
    # a fixed rerun is allowed: the trade export is accepted only if byte-identical, and it is
    Path(str(tp) + ".mode").write_text("ok")
    out = w.build()
    assert out["gaps"]["left_out"] == 0 and (w.od / f"{BOT}.wis.json").exists()


def test_existing_trade_export_with_other_bytes_is_refused(w):
    w.od.mkdir()
    (w.od / f"{BOT}.wis-trades.jsonl").write_text("{}\n")
    with pytest.raises(X.Refused, match="different bytes"):
        w.build()


def test_the_real_gaps_tool_refuses_here_with_a_nonzero_rc(w):
    # argv contract: the real arena_worst_gaps parses these flags; on an empty trades file it exits 3 ("no trade rows"), which the export refuses
    w.od.mkdir()
    tp = w.od / "t.jsonl"
    tp.write_text("")
    with pytest.raises(X.Refused, match="rc 3"):
        X.run_gaps(tp, str(w.tape), w.od, BOT)


def test_disk_scorer_must_be_the_known_copy(w, monkeypatch):
    monkeypatch.setattr(X, "ARENA_SCORE_DISK_SHA256", "0" * 64)
    with pytest.raises(X.Refused, match="c4859323"):
        w.build()


def test_pinned_scorer_patches_are_restored(w):
    S = W.pinned_score()
    bc, rec = S.book_cells, S.record_read
    w.build()
    assert S.book_cells is bc and S.record_read is rec
    with pytest.raises(X.Refused):
        w.build(health_texts=["bad"], health_shas=["0" * 64])
    assert S.book_cells is bc and S.record_read is rec


def test_cli_exit_codes_and_output(w, capsys, tmp_path):
    hp = tmp_path / "h.jsonl"
    hp.write_text(w.hl[0])
    rp = tmp_path / "r.jsonl"
    rp.write_text("")
    argv = ["--bot", BOT, "--instant", w.ip, "--reads-dir", str(w.rd), "--tape-dir", str(w.tape), "--out-dir", str(w.od), "--ledger-root", w.root, "--registry", w.reg,
            "--health", str(hp), "--health-sha", w.hl[1], "--restarts", str(rp), "--as-of-ms", str(AS_OF)]
    orig = X.GAPS_CMD
    X.build.__defaults__ = X.build.__defaults__[:-1] + (w.cmd,)
    try:
        assert X.main(argv) == 0
        out = capsys.readouterr().out
        assert "reproduced True" in out and "credits helius 0 rpc 0" in out
        assert X.main(argv) == 2  # write-once
        assert "written once" in capsys.readouterr().err
    finally:
        X.build.__defaults__ = X.build.__defaults__[:-1] + (orig,)


# ------------------------------------------------------------------------------------------------------------------------------ the host job script
import subprocess  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ops" / "hv4-wis-job.sh"
REPO = Path(__file__).resolve().parent.parent


def job(w, tmp_path, **env):
    hp = tmp_path / "h.jsonl"
    hp.write_text(w.hl[0])
    hl = tmp_path / "health-list.txt"
    hl.write_text(f"{hp} {w.hl[1]}\n")
    rp = tmp_path / "r.jsonl"
    rp.write_text("")
    e = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), TEST_ENV: "1", "BOT": BOT, "TAPE_DIR": str(w.tape), "HEALTH_LIST": str(hl), "RESTARTS": str(rp), "AS_OF_MS": str(AS_OF),
         "READS_DIR": str(w.rd), "OUT_DIR": str(w.od), "INSTANT": w.ip, "LEDGER": w.root, "REGISTRY": w.reg, "PY": sys.executable}
    e.update(env)
    return subprocess.run(["sh", str(SCRIPT)], cwd=REPO, env=e, capture_output=True, text=True, timeout=300)


def test_job_dry_prints_the_plan_and_writes_nothing(w, tmp_path):
    p = job(w, tmp_path, DRY="1")
    assert p.returncode == 0 and "tools.hv4_wis_export --bot hv4-CON-F" in p.stdout and "--as-of-ms" in p.stdout and not w.od.exists()


def test_job_refuses_without_a_recorded_pass(w, tmp_path):
    rec = w.rd / f"{BOT}.json"
    for status in ("FAIL", "NOT_DECIDABLE"):
        rec.chmod(0o644)
        rec.write_text(json.dumps({"bot_id": BOT, "status": status}))
        p = job(w, tmp_path)
        assert p.returncode == 2 and f"status {status}, not PASS" in p.stdout and not w.od.exists()
    rec.unlink()
    p = job(w, tmp_path)
    assert p.returncode == 2 and "no recorded read" in p.stdout and not w.od.exists()


def test_job_refuses_bad_inputs(w, tmp_path):
    assert job(w, tmp_path, BOT="botA").returncode == 2
    assert job(w, tmp_path, TAPE_DIR="").returncode == 2
    assert job(w, tmp_path, HEALTH_LIST="").returncode == 2
    assert not w.od.exists()


def test_job_exits_zero_when_the_wis_record_exists(w, tmp_path):
    w.od.mkdir()
    (w.od / f"{BOT}.wis.json").write_text("{}")
    p = job(w, tmp_path)
    assert p.returncode == 0 and "write-once" in p.stdout and (w.od / f"{BOT}.wis.json").read_text() == "{}"


def test_job_passes_a_tool_refusal_through_as_exit_2_and_writes_nothing(w, tmp_path):
    # in a subprocess the synthetic clock patch is absent, so the real tool refuses the synthetic registry (the HV4 clock is a pinned constant): exit 2, no wis.json
    p = job(w, tmp_path)
    assert p.returncode == 2 and "is not the HV4 clock" in p.stderr and not (w.od / f"{BOT}.wis.json").exists()
