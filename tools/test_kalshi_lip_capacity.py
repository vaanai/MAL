"""Tests for tools/kalshi_lip_capacity.py (LIP-F1 capacity Book C, report-only).  Synthetic stores only: no network,
no real recorder row, trade, program or settled result is read.  The worlds come from the simulator's own test
helpers (tools/test_kalshi_lip_sim.py), which this file imports and does not modify."""
import hashlib
import importlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root: ``tools`` is a package
import pyarrow.parquet as pq
import pytest

from tools import kalshi_lip_capacity as cap
from tools import kalshi_lip_sim as sim
from tools import kalshi_public as kp
from tools import test_kalshi_lip_sim as st

REPO = Path(__file__).resolve().parent.parent
REAL_DECLARATION = Path("/data/mal/hunt-1008/push-1016/hunt18/kalshi-lip/DECLARE-FORWARD-CAPACITY-1.md")
SIM_MD5 = "3dc4134aa575752927bea18db0e6a341"                       # DECLARE-FORWARD-PINS-1
R = "2026-10-24T00:00:00Z"

AK, AL, AR = (f"KXAAAGASD{s}-26OCT19-T3.5" for s in ("AK", "AL", "AR"))
AZ = "KXAAAGASDAZ-26OCT20-T3.5"                                      # a second date(E)
NY = "KXAAAGASDNY-26OCT19-T3.5"                                      # LIP-F1's series: never a Book C unit
BOOK_C_TICKERS = (AK, AL, AR, AZ)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def md5_dir(d):
    return {f.name: hashlib.md5(f.read_bytes()).hexdigest() for f in sorted(Path(d).iterdir())}


def sim_state():
    return {n: getattr(sim, n) for n in cap.PATCHED}


def declaration_text(series=None, fc=cap.FC_ISO):
    series = cap.BOOK_C_SERIES if series is None else series
    return ("# DECLARE-FORWARD-CAPACITY-1 (synthetic test copy of the two lines the parser reads)\n"
            "- Only two things differ:\n"
            "  1. Universe (replaces section 3.1): the 43 gas daily series below.\n"
            f"     {','.join(series)}\n"
            f"  2. Freeze instant (replaces FREEZE INSTANT in sections 3.3 and 12): FC = {fc}. The first-seen rule uses FC.\n"
            "Frozen at 2026-10-19T08:22:27Z (date -u), before any row of its recorder exists.\n")


ORIG_DEFAULTS = (cap.DEFAULT_BOOKS, cap.DEFAULT_PROGRAMS, cap.DEFAULT_META, cap.DEFAULT_RUNS)


@pytest.fixture(autouse=True)
def no_real_default_stores(monkeypatch):
    """The CLI defaults name real host stores; no test may reach them (every CLI test passes its own stores)."""
    for n in ("DEFAULT_BOOKS", "DEFAULT_PROGRAMS", "DEFAULT_META", "DEFAULT_RUNS"):
        monkeypatch.setattr(cap, n, "/nonexistent-bookc-test-default/" + n)


@pytest.fixture
def decl(tmp_path, monkeypatch):
    """A synthetic declaration whose sha256 the tool accepts for the duration of the test."""
    p = tmp_path / "decl" / "DECLARE-FORWARD-CAPACITY-1.md"
    p.parent.mkdir()
    p.write_text(declaration_text())
    monkeypatch.setattr(cap, "CAPACITY_DECLARE_SHA256", sha(p))
    return p


@pytest.fixture
def covered(monkeypatch):
    monkeypatch.setattr(sim, "coverage", lambda *a, **k: {"not_decidable": False, "days": {}})


def book_c_world(tmp, monkeypatch, with_f1_program=True):
    """obrec-1019e with four Book C gas units (three with E on 2026-10-19, one on 2026-10-20) and, in the program
    polls, one program of a LIP-F1 series that must never become a unit."""
    w = st.World(tmp)
    w.meta = str(Path(tmp) / "meta-empty")                 # daily series need no metadata capture
    Path(w.meta).mkdir(parents=True)
    snaps = {t: st.ticks("2026-10-19T12:05:30Z", "2026-10-19T15:30:00Z", 60) for t in (AK, AL, AR)}
    snaps[AZ] = st.ticks("2026-10-20T12:05:30Z", "2026-10-20T15:30:00Z", 60)
    w.add_run(cap.BOOK_C_RECORDER, snaps, sorted({kp.series_of(t) for t in snaps}))
    d1 = ("2026-10-19T12:00:00Z", "2026-10-19T14:00:00Z")
    d2 = ("2026-10-20T12:00:00Z", "2026-10-20T14:00:00Z")
    p1 = [st.prog("cAK", AK, *d1), st.prog("cAL", AL, *d1), st.prog("cAR", AR, *d1)]
    if with_f1_program:
        p1.append(st.prog("pNY", NY, *d1))
    w.add_polls([("2026-10-19T08:00:00Z", True, []), ("2026-10-19T12:05:00Z", True, p1),
                 ("2026-10-19T14:05:00Z", True, []), ("2026-10-20T12:05:00Z", True, [st.prog("cAZ", AZ, *d2)]),
                 ("2026-10-20T14:05:00Z", True, [])])
    w.add_series([(f"KXAAAGASD{s}", "daily", "quadratic") for s in ("AK", "AL", "AR", "AZ", "NY")])
    rows = []
    for t, day in ((AK, "19"), (AL, "19"), (AR, "19"), (AZ, "20")):
        rows += [(f"{t}-a", t, f"2026-10-{day}T12:10:00Z", 0.48, 25, "no"), (f"{t}-b", t, f"2026-10-{day}T12:30:00Z", 0.50, 10, "yes")]
    w.add_trades(rows, list(BOOK_C_TICKERS), since=cap.FC_ISO, until="2026-10-21T00:00:00Z",
                 run_at="2026-10-21T04:41:00Z", write_at="2026-10-21T04:30:00Z")
    w.add_markets([(AK, "KXAAAGASDAK-26OCT19", "yes", 1.0, "2026-10-19T15:00:00Z", "2026-10-20T04:41:00Z"),
                   (AL, "KXAAAGASDAL-26OCT19", "no", 0.0, "2026-10-19T15:00:00Z", "2026-10-20T04:41:00Z"),
                   (AR, "KXAAAGASDAR-26OCT19", "yes", 1.0, "2026-10-19T15:00:00Z", "2026-10-20T04:41:00Z"),
                   (AZ, "KXAAAGASDAZ-26OCT20", "no", 0.0, "2026-10-20T15:00:00Z", "2026-10-21T04:35:00Z")],
                  write_at="2026-10-21T04:35:00Z")
    reg = Path(tmp) / "obrec-REGISTRY-1019e.md"
    monkeypatch.setattr(cap, "REGISTRY_PINS", {cap.BOOK_C_RECORDER: sha(reg)})
    return w


def cap_cfg(w, out, **kw):
    base = dict(books=w.books, programs=w.lip, meta=w.meta, trades=str(w.kal), markets=str(w.kal), series=str(w.kal),
                read_us=st.at(R), units_evidence=w.evidence(), out=str(out))
    base.update(kw)
    return cap.make_cfg(**base)


def f1_run(w, out):
    """The simulator's own LIP-F1 path on the sim fixture world."""
    sim.run_sim(w.cfg(out=str(out)))
    return md5_dir(out)


# ================================================================================ (1) pins and restoration
def test_sim_file_is_the_pinned_one_and_the_pin_is_checked_at_run_time(tmp_path, monkeypatch):
    assert cap.SIM_MD5_PINNED == SIM_MD5
    assert hashlib.md5(Path(sim.__file__).read_bytes()).hexdigest() == SIM_MD5, \
        "tools/kalshi_lip_sim.py is frozen (DECLARE-FORWARD-PINS-1): this PR must not touch it"
    assert cap.check_sim_pin() == SIM_MD5
    w = book_c_world(tmp_path / "w", monkeypatch)
    monkeypatch.setattr(cap, "SIM_MD5_PINNED", "0" * 32)
    with pytest.raises(cap.CapacityRefusal, match="not the pinned"):
        cap.run_book_c(cap_cfg(w, tmp_path / "out"))
    assert not (tmp_path / "out").exists()


def test_a_capacity_run_restores_every_sim_constant_and_leaves_the_sim_file_alone(tmp_path, monkeypatch, covered):
    before, md5_before = sim_state(), hashlib.md5(Path(sim.__file__).read_bytes()).hexdigest()
    assert before == cap.F1_VALUES and len(before["SERIES_ADMITTED"]) == 15
    w = book_c_world(tmp_path / "w", monkeypatch)
    seen = {}
    real_run = sim.run_sim

    def spy(cfg):
        seen.update(sim_state())
        return real_run(cfg)
    monkeypatch.setattr(sim, "run_sim", spy)
    g = cap.run_book_c(cap_cfg(w, tmp_path / "out"))
    assert g["book"] == "C"
    # inside the run the sim saw Book C ...
    assert seen["FREEZE_ISO"] == cap.FC_ISO and seen["FREEZE_US"] == sim.iso_to_us(cap.FC_ISO)
    assert seen["GAS_SERIES"] == seen["SERIES_ADMITTED"] == cap.BOOK_C_SERIES and len(seen["GAS_SERIES"]) == 43
    assert seen["OTHER_SERIES"] == () and seen["PRIMARY_RUN"] == "obrec-1019e"
    # ... and afterwards it is LIP-F1's again, identical objects
    after = sim_state()
    assert after == before and all(after[n] is before[n] for n in before)
    assert hashlib.md5(Path(sim.__file__).read_bytes()).hexdigest() == md5_before == SIM_MD5
    assert sim.FREEZE_ISO == "2026-10-19T03:33:16Z" and len(sim.SERIES_ADMITTED) == 15


def test_constants_are_restored_after_an_error_and_the_override_does_not_nest():
    before = sim_state()
    with pytest.raises(RuntimeError):
        with cap.book_c_universe():
            assert sim.FREEZE_ISO == cap.FC_ISO
            raise RuntimeError("boom")
    assert sim_state() == before
    with cap.book_c_universe():
        with pytest.raises(cap.CapacityRefusal, match="nest"):
            with cap.book_c_universe():
                pass
        assert sim.FREEZE_ISO == cap.FC_ISO                       # the failed nesting did not restore early
    assert sim_state() == before


def test_a_simulator_error_inside_the_run_still_restores_the_constants(tmp_path, monkeypatch):
    w = book_c_world(tmp_path / "w", monkeypatch)
    before = sim_state()
    monkeypatch.setattr(sim, "run_sim", lambda cfg: (_ for _ in ()).throw(sim.InputError("synthetic")))
    with pytest.raises(sim.InputError):
        cap.run_book_c(cap_cfg(w, tmp_path / "out"))
    assert sim_state() == before


def test_the_coverage_block_reads_the_book_c_recorder_only_inside_the_override(tmp_path, monkeypatch):
    w = book_c_world(tmp_path / "w", monkeypatch)
    cfg = cap_cfg(w, tmp_path / "out")
    with cap.book_c_universe():
        inp = sim.load_inputs(cfg)
        inside = sim.coverage(inp, cfg, cfg.read_us)
    assert any(d["recorder"] > 0 for d in inside["days"].values())
    # without PRIMARY_RUN = obrec-1019e the simulator would filter out the only recorder and report 0.0
    outside = sim.coverage(inp, cfg, cfg.read_us)
    assert all(d["recorder"] == 0.0 for d in outside["days"].values())


# ============================================================ (2) capacity path == the sim with that universe
def test_capacity_path_equals_the_sim_run_with_the_same_universe_and_freeze(tmp_path, monkeypatch, covered):
    w = book_c_world(tmp_path / "w", monkeypatch)
    # reference: the simulator itself, constants patched by the test (not by the tool), no capacity code on the path
    with monkeypatch.context() as m:
        m.setattr(sim, "FREEZE_ISO", cap.FC_ISO)
        m.setattr(sim, "FREEZE_US", sim.iso_to_us(cap.FC_ISO))
        m.setattr(sim, "GAS_SERIES", tuple(cap.BOOK_C_SERIES))
        m.setattr(sim, "OTHER_SERIES", ())
        m.setattr(sim, "SERIES_ADMITTED", tuple(cap.BOOK_C_SERIES))
        m.setattr(sim, "PRIMARY_RUN", "obrec-1019e")
        ref_cfg = sim.Cfg(books=w.books, programs=w.lip, meta=w.meta, trades=str(w.kal), markets=str(w.kal), series=str(w.kal),
                          read_us=st.at(R), units_evidence=w.evidence(), out=str(tmp_path / "ref"), book="C",
                          freeze_us=sim.iso_to_us(cap.FC_ISO), registry_pins=dict(cap.REGISTRY_PINS))
        sim.run_sim(ref_cfg)
    res = cap.run_book_c(cap_cfg(w, tmp_path / "cap"))
    ref, got = md5_dir(tmp_path / "ref"), md5_dir(tmp_path / "cap")
    assert set(got) - set(ref) == {cap.MARKER} and set(ref) <= set(got)
    for f in ("units.parquet", "fills.parquet", "days.parquet"):
        assert got[f] == ref[f], f
    ref_gate = json.loads((tmp_path / "ref" / "gate.json").read_text())
    cap_gate = json.loads((tmp_path / "cap" / "gate.json").read_text())
    assert set(cap_gate) - set(ref_gate) == set(cap.LABEL_KEYS) - {"book"} and cap_gate["book"] == ref_gate["book"] == "C"
    stripped = {k: v for k, v in cap_gate.items() if k not in cap.LABEL_KEYS or k == "book"}
    assert stripped == ref_gate
    assert cap._dump(stripped) == (tmp_path / "ref" / "gate.json").read_text()           # byte-equal apart from labels
    assert (tmp_path / "cap" / "table.txt").read_text() == cap.banner() + "\n" + (tmp_path / "ref" / "table.txt").read_text()
    # the run was real
    assert ref_gate["cells"]["U0"]["gate"]["n_units"] == 4 and ref_gate["freeze"] == cap.FC_ISO
    units = pq.read_table(tmp_path / "cap" / "units.parquet").to_pylist()
    assert {u["unit_id"] for u in units} == {"cAK", "cAL", "cAR", "cAZ"}          # pNY (a LIP-F1 series) is not a unit
    assert any(u["fills_YES"] > 0 for u in units)
    # labels
    assert cap_gate["report_only"] is True and cap_gate["promotable"] is False and cap_gate["deciding"] is False
    assert cap_gate["verdict"].startswith("REPORT_ONLY_") and cap_gate["capacity_freeze"] == cap.FC_ISO
    assert res["report_only"] is True and res["promotable"] is False and res["book"] == "C"
    assert json.loads((tmp_path / "cap" / cap.MARKER).read_text())["promotable"] is False
    assert (tmp_path / "cap" / "table.txt").read_text().startswith("REPORT-ONLY CAPACITY BOOK C")


def test_under_lip_f1_constants_the_same_world_has_no_book_c_unit(tmp_path, monkeypatch, covered):
    """Guard for the test above: with LIP-F1's own universe the four Book C programs are not read at all."""
    w = book_c_world(tmp_path / "w", monkeypatch)
    cfg = sim.Cfg(books=w.books, programs=w.lip, meta=w.meta, trades=str(w.kal), markets=str(w.kal), series=str(w.kal),
                  read_us=st.at(R), units_evidence=w.evidence(), out=str(tmp_path / "f1"),
                  registry_pins=dict(cap.REGISTRY_PINS))
    g = sim.run_sim(cfg)
    ids = {u["unit_id"] for u in pq.read_table(tmp_path / "f1" / "units.parquet").to_pylist()}
    assert ids == {"pNY"} and g["exclusions"] == {"excl_no_book": 1} and g["cells"]["U0"]["gate"]["n_units"] == 0


# ================================================================================ (3) refusals
def test_an_f1_series_in_the_universe_is_refused():
    for bad in ("KXAAAGASDNY", "KXAAAGASDNC", "KXRT", "KXEOWEEK", "KXNEXTPRESSECPERM"):
        with pytest.raises(cap.CapacityRefusal, match="LIP-F1 series"):
            with cap.book_c_universe(cap.BOOK_C_SERIES + (bad,)):
                pytest.fail("the override started with an F1 series in the universe")
    assert set(cap.BOOK_C_SERIES).isdisjoint(sim.SERIES_ADMITTED) and len(cap.F1_SERIES) == 15
    with pytest.raises(cap.CapacityRefusal, match="differs from the declared"):
        with cap.book_c_universe(cap.BOOK_C_SERIES[:-1]):
            pass
    assert sim_state() == cap.F1_VALUES


def test_a_declared_universe_that_holds_an_f1_series_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cap, "BOOK_C_SERIES", cap.BOOK_C_SERIES + ("KXAAAGASDOH",))
    with pytest.raises(cap.CapacityRefusal, match="LIP-F1 series"):
        cap.check_universe(cap.BOOK_C_SERIES)
    with pytest.raises(cap.CapacityRefusal, match="LIP-F1 series"):
        with cap.book_c_universe():
            pass


def test_declaration_parse_sha_pin_and_hard_coded_constants_must_agree(tmp_path, monkeypatch, decl):
    assert cap.load_capacity_declaration(decl) == cap.CAPACITY_DECLARE_SHA256
    assert cap.parse_declaration(decl.read_text()) == (tuple(cap.BOOK_C_SERIES), cap.FC_ISO) and len(cap.BOOK_C_SERIES) == 43
    with pytest.raises(cap.CapacityRefusal, match="required"):
        cap.load_capacity_declaration(None)
    with pytest.raises(cap.CapacityRefusal, match="not a file"):
        cap.load_capacity_declaration(tmp_path / "missing.md")
    # a wrong sha is refused before anything is parsed
    other = tmp_path / "other.md"
    other.write_text(declaration_text() + "extra line\n")
    with pytest.raises(cap.CapacityRefusal, match="does not match the pinned"):
        cap.load_capacity_declaration(other)
    # a file whose sha is (made) accepted but whose content disagrees with the hard-coded constants is refused
    for name, text, msg in (("fc", declaration_text(fc="2026-10-19T03:33:16Z"), "FC"),
                            ("series", declaration_text(cap.BOOK_C_SERIES[:-1]), "series differ"),
                            ("f1", declaration_text(cap.BOOK_C_SERIES + ("KXAAAGASDNY",)), "series differ"),
                            ("none", "no series here\nFC = 2026-10-19T08:22:27Z\n", "series line"),
                            ("nofc", declaration_text().replace("FC = ", "F = "), "FC =")):
        p = tmp_path / f"{name}.md"
        p.write_text(text)
        monkeypatch.setattr(cap, "CAPACITY_DECLARE_SHA256", sha(p))
        with pytest.raises(cap.CapacityRefusal, match=msg):
            cap.load_capacity_declaration(p)


@pytest.mark.skipif(not REAL_DECLARATION.exists(), reason="the declaration is a host file")
def test_the_real_declaration_matches_the_pin_and_the_hard_coded_universe():
    assert sha(REAL_DECLARATION) == cap.CAPACITY_DECLARE_SHA256 == "a430e040ed1245fba0c0d2de11b8056cb9856ddecf750f7901878b8ac63fd34b"
    assert cap.load_capacity_declaration(REAL_DECLARATION) == cap.CAPACITY_DECLARE_SHA256


def cli(world, out, decl, *extra, covered_read=True):
    return ["--freeze", cap.FC_ISO, "--capacity-declare", str(decl), "--books", *world.books, "--programs", world.lip,
            "--meta", world.meta, "--trades", str(world.kal), "--markets", str(world.kal), "--series", str(world.kal), "--out", str(out),
            "--units-evidence", world.evidence(), *(["--read-instant", R] if covered_read else []), *extra]


@pytest.fixture
def sim_pins(tmp_path, monkeypatch):
    """The three LIP-F1 pin files of the simulator CLI, replaced by tiny synthetic files whose sha256 the sim accepts."""
    files = []
    for i, name in enumerate(("DECLARE", "CONDITIONS", "CONDITIONS2")):
        p = tmp_path / "pins" / f"{name}.md"
        p.parent.mkdir(exist_ok=True)
        p.write_text(f"synthetic {name}\n")
        monkeypatch.setattr(sim, name + "_SHA256", sha(p))
        files.append(str(p))
    return files


def test_cli_refuses_wrong_declare_sha_scan_wrong_freeze_and_a_missing_read_instant(tmp_path, monkeypatch, decl, sim_pins, capsys):
    w = book_c_world(tmp_path / "w", monkeypatch)
    pins = ["--declare", sim_pins[0], "--conditions", sim_pins[1], "--conditions2", sim_pins[2]]
    out = tmp_path / "out"
    bogus = tmp_path / "bogus.md"
    bogus.write_text("not the capacity declaration")
    assert cap.main(cli(w, out, bogus, *pins)) == 2 and "does not match the pinned" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:                       # --capacity-declare is a required argument
        cap.main(["--freeze", cap.FC_ISO, "--dry-run"])
    assert e.value.code == 2
    assert cap.main(cli(w, out, decl, *pins, "--scan", covered_read=False)) == 2 and "--scan" in capsys.readouterr().err
    assert cap.main(cli(w, out, decl, *pins, "--asof", R)) == 2
    assert cap.main(["--freeze", sim.FREEZE_ISO] + cli(w, out, decl, *pins)[2:]) == 2        # LIP-F1's freeze is not FC
    assert "must be 2026-10-19T08:22:27Z" in capsys.readouterr().err
    assert cap.main(cli(w, out, decl, *pins, covered_read=False)) == 2 and "--read-instant" in capsys.readouterr().err
    assert cap.main(cli(w, out, decl, "--declare", str(bogus), "--conditions", sim_pins[1], "--conditions2", sim_pins[2])) == 2
    assert cap.main(cli(w, out, decl, *pins, "--size", "100")) == 2                           # Book B size
    assert cap.main(cli(w, out, decl, *pins, "--imax", "75")) == 2
    assert cap.main(cli(w, out, decl, *pins, "--force-source", "obrec-1019c")) == 2
    assert cap.main(cli(w, out, decl, *pins, "--registry-sha256", "obrec-1019e=" + "0" * 64)) == 2
    assert not out.exists()                                    # every refusal happened before anything was written


def test_cli_refuses_the_f1_recorders_and_a_read_instant_before_the_freeze(tmp_path, monkeypatch, decl, sim_pins, capsys):
    w = book_c_world(tmp_path / "w", monkeypatch)
    pins = ["--declare", sim_pins[0], "--conditions", sim_pins[1], "--conditions2", sim_pins[2]]
    args = cli(w, tmp_path / "out", decl, *pins)
    i = args.index("--books")
    for name in ("obrec-1019c", "obrec-1019d"):
        bad = args[:i + 1] + [str(tmp_path / name)] + args[i + 2:]
        assert cap.main(bad) == 2 and "obrec-1019e only" in capsys.readouterr().err
    early = [("2026-10-19T03:00:00Z" if a == R else a) for a in args]
    assert cap.main(early) == 2 and "after the freeze" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_scan_is_refused_by_run_book_c_even_if_a_cfg_is_built_around_make_cfg(tmp_path, monkeypatch):
    w = book_c_world(tmp_path / "w", monkeypatch)
    for k, v in (("scan", True), ("asof_us", st.at(R)), ("book", "A"), ("freeze_us", sim.FREEZE_US), ("registry_pins", {})):
        with pytest.raises(cap.CapacityRefusal, match="fixed for Book C"):
            cap_cfg(w, tmp_path / "out", **{k: v})
    cfg = cap_cfg(w, tmp_path / "out")
    for attr, v in (("scan", True), ("book", "A"), ("freeze_us", sim.FREEZE_US), ("registry_pins", {}), ("size", 100.0)):
        bad = cap_cfg(w, tmp_path / "out")
        setattr(bad, attr, v)
        with pytest.raises(cap.CapacityRefusal):
            cap.run_book_c(bad)
    assert not (tmp_path / "out").exists() and cfg.book == "C"


def test_out_holding_lip_f1_files_is_refused_and_nothing_is_overwritten(tmp_path, monkeypatch, covered):
    f1 = st.basic_world(tmp_path / "f1w")
    f1_out = tmp_path / "f1_out"
    before = f1_run(f1, f1_out)
    assert json.loads((f1_out / "gate.json").read_text())["book"] == "A"
    w = book_c_world(tmp_path / "w", monkeypatch)
    with pytest.raises(cap.CapacityRefusal, match="already holds LIP-F1 simulator files"):
        cap.run_book_c(cap_cfg(w, f1_out))
    assert md5_dir(f1_out) == before                                     # untouched
    # any single simulator file without a Book C marker is enough
    for name in cap.SIM_OUTPUT_FILES:
        d = tmp_path / ("one_" + name)
        d.mkdir()
        (d / name).write_text("x")
        with pytest.raises(cap.CapacityRefusal, match="already holds"):
            cap.run_book_c(cap_cfg(w, d))
        assert sorted(p.name for p in d.iterdir()) == [name]
    # a marker alone is not enough when gate.json says it is not a Book C file
    forged = tmp_path / "forged"
    forged.mkdir()
    (forged / cap.MARKER).write_text(cap._dump(cap.labels()))
    (forged / "gate.json").write_text(json.dumps({"book": "A", "deciding": True}))
    with pytest.raises(cap.CapacityRefusal, match="already holds"):
        cap.run_book_c(cap_cfg(w, forged))
    # a directory with no simulator file, a new one, and a previous Book C output are accepted
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("x")
    cap.run_book_c(cap_cfg(w, other))
    g1 = md5_dir(other)
    cap.run_book_c(cap_cfg(w, other))                                    # rerun into a Book C output
    assert md5_dir(other) == g1
    f = tmp_path / "afile"
    f.write_text("x")
    with pytest.raises(cap.CapacityRefusal, match="not a directory"):
        cap.run_book_c(cap_cfg(w, f))


def test_out_inside_an_input_store_is_refused(tmp_path, monkeypatch):
    w = book_c_world(tmp_path / "w", monkeypatch)
    for inside in (Path(w.books[0]) / "x", Path(w.lip), w.kal / "capacity", w.kal):
        with pytest.raises(cap.CapacityRefusal, match="inside the input store"):
            cap.run_book_c(cap_cfg(w, inside))
    cfg = cap_cfg(w, tmp_path / "gl" / "out", trades=str(tmp_path / "gl" / "run-*"), markets=str(tmp_path / "m"),
                  series=str(tmp_path / "s"))
    with pytest.raises(cap.CapacityRefusal, match="inside the input store"):         # a glob store: its literal prefix
        cap.run_book_c(cfg)
    assert not (Path(w.books[0]) / "x").exists()


# ================================================================================ (4) G4b
def test_g4b_clusters_every_book_c_unit_by_date_e(tmp_path, monkeypatch, covered):
    w = book_c_world(tmp_path / "w", monkeypatch)
    g = cap.run_book_c(cap_cfg(w, tmp_path / "out"))
    units = [u for u in pq.read_table(tmp_path / "out" / "units.parquet").to_pylist() if u["cell"] == "U0"]
    assert len(units) == 4 and all(u["gas"] for u in units)
    assert sorted(u["day_E"] for u in units) == ["2026-10-19"] * 3 + ["2026-10-20"]
    for c in ("U0", "U15", "BQ"):
        gt = g["cells"][c]["gate"]
        assert gt["n_units"] == 4
        assert gt["G4"]["clusters"] == 4          # four events
        assert gt["G4b"]["clusters"] == 2         # ... but two dates of E: every unit is a gas unit
    assert sim.key_gas_day({"gas": True, "day_e": "2026-10-19", "event": "E1"}) == "GAS|2026-10-19"


def test_without_the_gas_override_the_same_units_would_be_clustered_per_event(tmp_path, monkeypatch, covered):
    """Contrast: SERIES_ADMITTED alone is not enough; GAS_SERIES is what makes G4b cluster by date(E)."""
    w = book_c_world(tmp_path / "w", monkeypatch)
    with monkeypatch.context() as m:
        m.setattr(sim, "SERIES_ADMITTED", tuple(cap.BOOK_C_SERIES))
        m.setattr(sim, "FREEZE_ISO", cap.FC_ISO)
        cfg = sim.Cfg(books=w.books, programs=w.lip, meta=w.meta, trades=str(w.kal), markets=str(w.kal), series=str(w.kal),
                      read_us=st.at(R), units_evidence=w.evidence(), out=str(tmp_path / "partial"), book="C",
                      freeze_us=sim.iso_to_us(cap.FC_ISO), registry_pins=dict(cap.REGISTRY_PINS))
        g = sim.run_sim(cfg)
    gt = g["cells"]["U0"]["gate"]
    assert gt["G4"]["clusters"] == gt["G4b"]["clusters"] == 4


# ===================================================================== dry run, full CLI run, structure
def test_dry_run_is_labelled_prints_structure_only_and_never_opens_trades_or_results(tmp_path, monkeypatch, decl, capsys):
    w = book_c_world(tmp_path / "w", monkeypatch)
    monkeypatch.setattr(sim, "load_trades", lambda *a, **k: pytest.fail("dry run opened the trades parquet"))
    monkeypatch.setattr(sim, "read_results", lambda *a, **k: pytest.fail("dry run opened settled results"))
    monkeypatch.setattr(sim, "run_unit", lambda *a, **k: pytest.fail("dry run ran the pass"))
    out = tmp_path / "out"
    rc = cap.main(["--freeze", cap.FC_ISO, "--capacity-declare", str(decl), "--dry-run", "--books", *w.books,
                   "--programs", w.lip, "--meta", w.meta, "--trades", str(w.kal), "--markets", str(w.kal),
                   "--series", str(w.kal), "--out", str(out)])
    rep = json.loads(capsys.readouterr().out)
    assert rc == 0 and rep["mode"] == "dry_run" and rep["book"] == "C" and rep["freeze"] == cap.FC_ISO
    assert rep["report_only"] is True and rep["promotable"] is False
    assert rep["structurally_opened_units"] == 4 and rep["programs_admitted_series"] == 4
    assert not out.exists()
    assert cap.main(["--freeze", cap.FC_ISO, "--capacity-declare", str(decl), "--dry-run", "--scan", "--books", *w.books,
                     "--programs", w.lip]) == 2


def test_full_cli_run_writes_labelled_files_only_in_out(tmp_path, monkeypatch, decl, sim_pins, covered, capsys):
    w = book_c_world(tmp_path / "w", monkeypatch)
    out = tmp_path / "out"
    rc = cap.main(cli(w, out, decl, "--declare", sim_pins[0], "--conditions", sim_pins[1], "--conditions2", sim_pins[2]))
    stdout = capsys.readouterr().out
    assert rc == 0 and stdout.startswith("REPORT-ONLY CAPACITY BOOK C") and "Book C S=25 I_max=50" in stdout
    assert {p.name for p in out.iterdir()} == {"units.parquet", "fills.parquet", "days.parquet", "gate.json", "table.txt", cap.MARKER}
    gate = json.loads((out / "gate.json").read_text())
    assert gate["book"] == "C" and gate["report_only"] is True and gate["promotable"] is False and gate["deciding"] is False
    assert gate["freeze"] == cap.FC_ISO and gate["read_instant"] == R and gate["sim_md5"] == SIM_MD5
    assert len(gate["capacity_universe"]) == 43 and gate["capacity_declare_sha256"] == cap.CAPACITY_DECLARE_SHA256
    assert gate["capacity_tool_md5"] == hashlib.md5(Path(cap.__file__).read_bytes()).hexdigest()
    assert any("no kill condition" in n for n in gate["capacity_notes"])
    # only the output dir and its parent fixtures changed: the stores are byte-for-byte what the world wrote
    assert sim_state() == cap.F1_VALUES


def test_units_evidence_missing_still_labels_the_not_decidable_file(tmp_path, monkeypatch):
    w = book_c_world(tmp_path / "w", monkeypatch)
    g = cap.run_book_c(cap_cfg(w, tmp_path / "out", units_evidence=None))
    gate = json.loads((tmp_path / "out" / "gate.json").read_text())
    assert gate["verdict"] == "NOT_DECIDABLE" and gate["report_only"] is True and gate["promotable"] is False
    assert gate["deciding"] is False and g["book"] == "C"
    assert (tmp_path / "out" / "table.txt").read_text().startswith("REPORT-ONLY CAPACITY BOOK C")


def test_capacity_runs_are_byte_identical(tmp_path, monkeypatch, covered):
    w = book_c_world(tmp_path / "w", monkeypatch)
    cap.run_book_c(cap_cfg(w, tmp_path / "o1"))
    cap.run_book_c(cap_cfg(w, tmp_path / "o2"))
    assert md5_dir(tmp_path / "o1") == md5_dir(tmp_path / "o2")


def test_a_wrong_registry_pin_makes_the_run_refuse_through_the_sim(tmp_path, monkeypatch, covered):
    w = book_c_world(tmp_path / "w", monkeypatch)
    monkeypatch.setattr(cap, "REGISTRY_PINS", {cap.BOOK_C_RECORDER: "0" * 64})
    with pytest.raises(sim.InputError, match="registry check failed"):
        cap.run_book_c(cap_cfg(w, tmp_path / "out"))
    assert sim_state() == cap.F1_VALUES


def test_the_pinned_registry_and_the_default_stores_are_the_declared_ones():
    assert cap.REGISTRY_1019E_SHA256 == "82f617517d7b3c19e910e7cc1efb94e8494060e3e5d0d815b7f367acacc02ed7"
    assert cap.REGISTRY_PINS == {"obrec-1019e": cap.REGISTRY_1019E_SHA256}
    assert ORIG_DEFAULTS == (
        "/data/mal/kalshi/obrec-1019e", "/data/mal/kalshi/lipsnap-1019", "/data/mal/kalshi/meta-1019e",
        "/data/mal/kalshi/lipdl-cap1/run-*")
    p = cap.build_parser()
    assert "2026-10-19T08:22:27Z" in p.format_help() and "3:33:16" not in p.format_help()


# ============================================================= (5) LIP-F1 byte-identical around the import
DRIVER = textwrap.dedent('''
    import json, sys
    cfg = json.loads(sys.argv[1])
    sys.path.insert(0, cfg["repo"])
    if cfg["import_capacity"]:
        from tools import kalshi_lip_capacity            # noqa: F401  (the only difference between the two runs)
    from tools import kalshi_lip_sim as sim
    for name, sha in cfg["pins"].items():
        setattr(sim, name, sha)
    sim.coverage = lambda *a, **k: {"not_decidable": False, "days": {}}          # the fixture is a few hours long
    sys.exit(sim.main(cfg["argv"]))
''')


def test_a_lip_f1_run_is_byte_identical_before_and_after_the_capacity_module_is_imported(tmp_path):
    w = st.basic_world(tmp_path / "w")
    pin_files = {}
    for name in ("DECLARE", "CONDITIONS", "CONDITIONS2"):
        p = tmp_path / f"{name}.md"
        p.write_text(f"synthetic {name}\n")
        pin_files[name] = p
    argv_base = ["--freeze", sim.FREEZE_ISO, "--books", *w.books, "--programs", w.lip, "--meta", w.meta,
                 "--trades", str(w.kal), "--markets", str(w.kal), "--series", str(w.kal), "--read-instant", R,
                 "--units-evidence", w.evidence(), "--declare", str(pin_files["DECLARE"]),
                 "--conditions", str(pin_files["CONDITIONS"]), "--conditions2", str(pin_files["CONDITIONS2"])]
    res = {}
    for label, imp in (("before", False), ("after", True)):
        out = tmp_path / f"f1_{label}"
        cfg = {"repo": str(REPO), "import_capacity": imp, "argv": argv_base + ["--out", str(out)],
               "pins": {n + "_SHA256": sha(p) for n, p in pin_files.items()}}
        r = subprocess.run([sys.executable, "-c", DRIVER, json.dumps(cfg)], capture_output=True, text=True, timeout=300,
                           cwd=str(tmp_path))
        assert r.returncode == 0, r.stderr
        res[label] = (md5_dir(out), r.stdout)
    assert res["before"][0] == res["after"][0] and res["before"][1] == res["after"][1]
    assert {"units.parquet", "fills.parquet", "days.parquet", "gate.json", "table.txt"} <= set(res["before"][0])
    gate = json.loads((tmp_path / "f1_after" / "gate.json").read_text())
    assert gate["book"] == "A" and gate["deciding"] is True and gate["freeze"] == "2026-10-19T03:33:16Z"
    assert "report_only" not in gate and gate["sim_md5"] == SIM_MD5


def test_reimporting_the_capacity_module_does_not_move_a_sim_constant():
    before = {n: v for n, v in vars(sim).items() if n.isupper()}
    importlib.reload(cap)
    after = {n: v for n, v in vars(sim).items() if n.isupper()}
    assert before == after and sim_state() == cap.F1_VALUES


def test_a_lip_f1_run_after_a_book_c_run_in_the_same_process_is_byte_identical(tmp_path, monkeypatch, covered):
    f1 = st.basic_world(tmp_path / "f1w")
    first = f1_run(f1, tmp_path / "f1_a")
    w = book_c_world(tmp_path / "w", monkeypatch)
    cap.run_book_c(cap_cfg(w, tmp_path / "c"))
    second = f1_run(f1, tmp_path / "f1_b")
    assert first == second
