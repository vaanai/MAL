"""Tests for tools/kalshi_lip_f2.py (LIP-F2 cohort wrapper: F2-A side cap, F2-B I_max 25).  Synthetic stores only: no
network, no real recorder row, trade, program, registry or settled result is read.  The worlds come from the
simulator's own test helpers (tools/test_kalshi_lip_sim.py), which this file imports and does not modify.  Every
fixture is hand-built; none is copied or shaped from a post-2026-10-19T03:33:16Z book."""
import hashlib
import importlib
import json
import random
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root: ``tools`` is a package
import numpy as np
import pyarrow.parquet as pq
import pytest

from tools import kalshi_lip_capacity as cap
from tools import kalshi_lip_f2 as f2
from tools import kalshi_lip_sim as sim
from tools import kalshi_public as kp
from tools import test_kalshi_lip_sim as st

REPO = Path(__file__).resolve().parent.parent
REAL_TEXTS = Path("/data/mal/hunt-1008/push-1016/hunt18/kalshi-lip")
SIM_MD5 = "3dc4134aa575752927bea18db0e6a341"                       # DECLARE-FORWARD-PINS-1
SIM_TESTS_MD5 = "1f5db2f15043f4f8451c647a863f741a"
F2_FREEZE = "2026-10-19T11:00:00Z"                                  # a synthetic cohort freeze, after LIP-F1's
R = "2026-10-26T00:00:00Z"
T0 = st.T0


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def md5_dir(d):
    return {f.name: hashlib.md5(f.read_bytes()).hexdigest() for f in sorted(Path(d).iterdir())}


def sim_state():
    return {n: getattr(sim, n) for n in f2.PATCHED_CONSTANTS + f2.PATCHED_FUNCTIONS}


def sec(x):
    return int(round(x * sim.US))


@pytest.fixture(autouse=True)
def no_real_default_stores(monkeypatch):
    """The CLI defaults name real host stores; no test may reach them (every CLI test passes its own stores)."""
    for n in ("DEFAULT_PROGRAMS", "DEFAULT_META", "DEFAULT_RUNS", "DEFAULT_SERIES", "DEFAULT_LIP_TEXTS_DIR"):
        monkeypatch.setattr(f2, n, "/nonexistent-f2-test-default/" + n)
    monkeypatch.setattr(f2, "DEFAULT_BOOKS", ["/nonexistent-f2-test-default/obrec-1019c"])


@pytest.fixture
def covered(monkeypatch):
    monkeypatch.setattr(sim, "coverage", lambda *a, **k: {"not_decidable": False, "days": {}})


# ============================================================================================ fixtures
def tool_md5():
    return hashlib.md5(Path(f2.__file__).read_bytes()).hexdigest()


def decl_text(freeze=F2_FREEZE, md5=None):
    return (f"COHORT FREEZE INSTANT = {freeze} (output of `date -u +%Y-%m-%dT%H:%M:%SZ` run by the one stamping command)\n"
            f"F2 TOOL MD5 = {tool_md5() if md5 is None else md5}\n"
            "# DECLARE-F2 (synthetic test copy of the lines the parser reads)\n"
            "F2-A SIDE-CAP and F2-B INV25 are the two gated variants.\n")


@pytest.fixture
def texts(tmp_path, monkeypatch):
    """Six synthetic stand-ins for the inherited LIP-F1 texts, accepted by the tool for the duration of the test."""
    d = tmp_path / "lip-texts"
    d.mkdir()
    pins = []
    for key, fname, _want in f2.LIP_F1_TEXTS:
        body = f"synthetic {key}\n"
        if key == "verify1":
            body += "period_reward https://docs.kalshi.com/api-reference/incentive-programs/get-incentives.md VERIFIED\n"
        (d / fname).write_text(body)
        pins.append((key, fname, sha(d / fname)))
    monkeypatch.setattr(f2, "LIP_F1_TEXTS", tuple(pins))
    return d


@pytest.fixture
def decl(tmp_path):
    p = tmp_path / "decl" / "DECLARE-F2.md"
    p.parent.mkdir()
    p.write_text(decl_text())
    return p


_GATE_N = [0]


def lip_gate(tmp, **over):
    """A hand-written LIP-F1 gate.json (a Book A --read-instant run); ``over`` edits it, Ellipsis drops a key."""
    d = {"book": "A", "deciding": True, "freeze": "2026-10-19T03:33:16Z", "verdict": "FAIL",
         "read_instant": "2026-10-24T00:00:00Z"}
    d.update(over)
    d = {k: v for k, v in d.items() if v is not Ellipsis}
    _GATE_N[0] += 1
    p = Path(tmp) / "gates" / f"lip-f1-{_GATE_N[0]}.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(d))
    return p


BOOKC_GATE = {"book": "C", "report_only": True, "promotable": False, "verdict": "REPORT_ONLY_FAIL",
              "read_instant": "2026-10-24T00:00:00Z"}


def bookc_marker(tmp, content=None, name="gate.json"):
    """Book C's gate.json as the Book C wrapper writes it (book C, report_only, not promotable, at LIP-F1's R)."""
    p = Path(tmp) / "bookc-done" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(BOOKC_GATE) if content is None else content)
    return p


@pytest.fixture
def gates(tmp_path):
    return {"lip": str(lip_gate(tmp_path)), "bookc": str(bookc_marker(tmp_path))}


def f2_world(tmp, monkeypatch, **kw):
    """A LIP-F1 fixture world (two gas units, obrec-1019c) whose registry the tool's pin accepts."""
    w = st.basic_world(tmp, **kw)
    monkeypatch.setattr(f2, "REGISTRY_PINS", {"obrec-1019c": sha(Path(tmp) / "obrec-REGISTRY-1019c.md")})
    return w


def ctx_for(texts_dir, gates, freeze=F2_FREEZE, md5=None):
    """The run context ``prepare`` builds, with a synthetic DECLARE-F2.md (carrying this tool's md5 unless ``md5`` is given)."""
    d = Path(texts_dir).parent / "ctx-decl"
    d.mkdir(exist_ok=True)
    p = d / "DECLARE-F2.md"
    p.write_text(decl_text(freeze, md5))
    return {"freeze_iso": freeze, "declare_sha256": sha(p), "declare_path": str(p),
            "texts": {f.name: sha(f) for f in Path(texts_dir).iterdir()},
            "lip_f1_gate": {"path": gates["lip"]}, "bookc_done": {"path": gates["bookc"]}}


def f2_cfg(w, out, variant="A", **kw):
    base = dict(books=w.books, programs=w.lip, meta=getattr(w, "meta", None), trades=str(w.kal), markets=str(w.kal),
                series=str(w.kal), read_us=st.at(R), units_evidence=w.evidence(), out=str(out))
    base.update(kw)
    return f2.make_cfg(variant, freeze_us=st.at(F2_FREEZE), **base)


def cli_argv(w, tmp, texts_dir, decl_path, gates=None, variant="A", out="out", **kw):
    a = ["--f2-declare", str(decl_path), "--f2-declare-sha256", sha(decl_path), "--freeze", F2_FREEZE,
         "--lip-texts-dir", str(texts_dir), "--books", *w.books, "--programs", w.lip, "--meta", w.meta,
         "--trades", str(w.kal), "--markets", str(w.kal), "--series", str(w.kal)]
    if variant:
        a += ["--variant", variant]
    if gates is not None:
        a += ["--lip-f1-gate", gates["lip"], "--bookc-done", gates["bookc"]]
    if out:
        a += ["--out", str(Path(tmp) / out)]
    return a


def snap(ts_s, yes, no):
    """A snapshot with prices in basis points: yes / no = [(bp, size)] best first."""
    return sim.Snap(T0 + sec(ts_s), [(p, float(q)) for p, q in yes], [(p, float(q)) for p, q in no])


# ================================================================================ (1) pins and restoration
def test_the_simulator_and_its_tests_are_the_pinned_files_and_the_pin_is_checked_at_run_time(tmp_path, monkeypatch):
    assert f2.SIM_MD5_PINNED == SIM_MD5 and f2.SIM_TESTS_MD5_PINNED == SIM_TESTS_MD5
    assert hashlib.md5(Path(sim.__file__).read_bytes()).hexdigest() == SIM_MD5, \
        "tools/kalshi_lip_sim.py is frozen (DECLARE-FORWARD-PINS-1): this PR must not touch it"
    assert hashlib.md5(Path(st.__file__).read_bytes()).hexdigest() == SIM_TESTS_MD5, \
        "tools/test_kalshi_lip_sim.py is frozen (DECLARE-FORWARD-PINS-1): this PR must not touch it"
    assert f2.check_sim_pin() == SIM_MD5
    monkeypatch.setattr(f2, "SIM_MD5_PINNED", "0" * 32)
    with pytest.raises(f2.F2Refusal, match="not the pinned"):
        f2.check_sim_pin()
    with pytest.raises(f2.F2Refusal, match="not the pinned"):
        with f2.f2_cohort(F2_FREEZE, "A"):
            pytest.fail("the context started on an unpinned simulator")
    w = st.basic_world(tmp_path / "w")
    with pytest.raises(f2.F2Refusal, match="not the pinned"):
        f2.run_f2(f2_cfg(w, tmp_path / "out"), "A", {"freeze_iso": F2_FREEZE})
    assert not (tmp_path / "out").exists()


def test_inherited_pins_agree_with_the_simulator_pins1_and_pins2():
    assert dict((k, h) for k, _f, h in f2.LIP_F1_TEXTS) == {
        "declare": sim.DECLARE_SHA256, "conditions": sim.CONDITIONS_SHA256, "conditions2": sim.CONDITIONS2_SHA256,
        "verify1": sim.VERIFY1_SHA256,
        "process1": "4cc45c27317560ecf2a08d5d65b73c9f02afd32deb0488ad3ce63f9f62de78f8",
        "process2": "cf635192e8105632790a0cf5081f662aa8f346fb720391ed3fe9e8573419ddb0",
        "pins1": "f313b36d62388ca82bbbd4b1a8fe2a67417ad8d5fbd8b12a43dd59ed4c8659e8",
        "pins2": "f7f78198631b4d0e2925bbc1436be605f23b982f492f1b3208fc37fb54cf66ab"}
    assert f2.REGISTRY_PINS == {
        "obrec-1019c": "d6affcff6abcdd5dce07af8d786ad6e33828c0b4a762d7cd85627ab80371da3a",
        "obrec-1019d": "3984c1c68bf0d51f52d06226771fd08f8ae33a174dcd14a646cd9221e86e5236"}
    assert f2.ALLOWED_RECORDERS == ("obrec-1019c", "obrec-1019d")        # never Book C's obrec-1019e
    assert f2.F2_SERIES == sim.GAS_SERIES and len(f2.F2_SERIES) == 8
    f2.check_inherited()
    for n, want in f2.INHERITED.items():
        assert getattr(sim, n) == want, n


@pytest.mark.skipif(not (REAL_TEXTS / "DECLARE-FORWARD.md").exists(), reason="host texts not present")
def test_the_real_inherited_texts_match_pins1():
    got, verify1 = f2.verify_lip_texts(REAL_TEXTS)
    assert set(got) == {f for _k, f, _h in f2.LIP_F1_TEXTS} and verify1.endswith("DECLARE-FORWARD-VERIFY-1.md")
    assert sim.check_units_evidence(verify1)["ok"] is True


def test_the_text_pins_refuse_a_missing_or_altered_file(texts):
    got, verify1 = f2.verify_lip_texts(texts)
    assert len(got) == 8 and Path(verify1).name == "DECLARE-FORWARD-VERIFY-1.md"
    (texts / "DECLARE-FORWARD-PROCESS-2.md").write_text("altered\n")
    with pytest.raises(f2.F2Refusal, match="does not match the pinned"):
        f2.verify_lip_texts(texts)
    (texts / "DECLARE-FORWARD-PROCESS-2.md").unlink()
    with pytest.raises(f2.F2Refusal, match="not found"):
        f2.verify_lip_texts(texts)
    with pytest.raises(f2.F2Refusal, match="required"):
        f2.verify_lip_texts(None)


@pytest.mark.parametrize("variant", ["A", "B", None])
def test_the_context_rebinds_the_f2_attributes_and_restores_the_identical_objects(variant):
    before = sim_state()
    assert f2.sim_is_lip_f1() and len(before["SERIES_ADMITTED"]) == 15
    with f2.f2_cohort(F2_FREEZE, variant):
        assert sim.FREEZE_ISO == F2_FREEZE and sim.FREEZE_US == st.at(F2_FREEZE)
        assert sim.GAS_SERIES == sim.SERIES_ADMITTED == f2.F2_SERIES and sim.OTHER_SERIES == ()
        assert sim.READ_FROM_ISO == "2026-10-26T00:00:00Z" and sim.NOT_DECIDABLE_ISO == "2026-11-02T00:00:00Z"
        # the coverage run and its baseline are LIP-F1's: obrec-1019c at 222.7 s, polls at 900 s
        assert sim.PRIMARY_RUN == "obrec-1019c" and sim.BASELINE_GAP_S == {"obrec-1019c": 222.7, "lipsnap": 900.0}
        patched = {n for n in f2.PATCHED_FUNCTIONS if getattr(sim, n) is not f2.F1_FUNCS[n]}
        assert patched == {None: set(), "B": {"cluster_bootstrap", "evaluate_gate"},
                           "A": {"cluster_bootstrap", "evaluate_gate", "decide_quote"}}[variant]
        assert [r // sim.US for r in sim.read_candidates()][0] == st.at("2026-10-26T00:00:00Z") // sim.US
    after = sim_state()
    assert after == before and all(after[n] is before[n] for n in before)
    assert f2.sim_is_lip_f1()
    assert hashlib.md5(Path(sim.__file__).read_bytes()).hexdigest() == SIM_MD5
    assert sim.FREEZE_ISO == "2026-10-19T03:33:16Z" and len(sim.SERIES_ADMITTED) == 15
    assert sim.READ_FROM_ISO == "2026-10-24T00:00:00Z"


def test_the_patch_is_restored_in_finally_even_on_an_exception():
    before = sim_state()
    for variant in ("A", "B", None):
        with pytest.raises(RuntimeError, match="boom"):
            with f2.f2_cohort(F2_FREEZE, variant):
                assert sim.FREEZE_ISO == F2_FREEZE
                raise RuntimeError("boom")
        after = sim_state()
        assert all(after[n] is before[n] for n in before), variant
    # an error raised by the simulator inside run_f2 restores it too
    assert f2.sim_is_lip_f1()


def test_a_simulator_error_inside_the_run_still_restores_the_patch(tmp_path, monkeypatch, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    before = sim_state()
    seen = {}

    def boom(cfg):
        seen["inside"] = sim.FREEZE_ISO
        raise sim.InputError("synthetic")
    monkeypatch.setattr(sim, "run_sim", boom)
    with pytest.raises(sim.InputError):
        f2.run_f2(f2_cfg(w, tmp_path / "out"), "A", ctx_for(texts, gates))
    assert seen["inside"] == F2_FREEZE
    after = sim_state()
    assert all(after[n] is before[n] for n in before)


def test_the_context_refuses_to_nest_and_the_failed_nesting_does_not_restore_early():
    before = sim_state()
    with f2.f2_cohort(F2_FREEZE, "A"):
        for variant in ("A", "B", None):
            with pytest.raises(f2.F2Refusal, match="nest"):
                with f2.f2_cohort(F2_FREEZE, variant):
                    pytest.fail("nested")
        assert sim.FREEZE_ISO == F2_FREEZE and sim.decide_quote is not f2.F1_FUNCS["decide_quote"]
        with pytest.raises(cap.CapacityRefusal, match="nest"):          # Book C refuses inside F2
            with cap.book_c_universe():
                pass
    assert all(sim_state()[n] is before[n] for n in before)
    with cap.book_c_universe():                                          # and F2 refuses inside Book C
        with pytest.raises(f2.F2Refusal, match="nest"):
            with f2.f2_cohort(F2_FREEZE, "B"):
                pass
    assert all(sim_state()[n] is before[n] for n in before)


def test_the_context_refuses_on_a_simulator_that_is_not_exactly_lip_f1(monkeypatch):
    monkeypatch.setattr(sim, "decide_quote", lambda *a: (None, "x"))
    with pytest.raises(f2.F2Refusal, match="not exactly LIP-F1"):
        with f2.f2_cohort(F2_FREEZE, "A"):
            pass
    monkeypatch.undo()
    monkeypatch.setattr(sim, "CAP_US", 10 ** 15)
    with pytest.raises(f2.F2Refusal, match="inherited"):
        with f2.f2_cohort(F2_FREEZE, "A"):
            pass


def test_the_cohort_freeze_must_be_after_lip_f1s_freeze_and_the_variant_known():
    for bad in ("2026-10-19T03:33:16Z", "2026-10-19T03:00:00Z"):
        with pytest.raises(f2.F2Refusal, match="after LIP-F1"):
            with f2.f2_cohort(bad, "A"):
                pass
    with pytest.raises(f2.F2Refusal, match="unknown variant"):
        with f2.f2_cohort(F2_FREEZE, "C"):
            pass


def test_importing_and_reloading_the_module_does_not_move_a_simulator_attribute():
    code = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        from tools import kalshi_lip_sim as sim
        names = ("FREEZE_ISO", "FREEZE_US", "GAS_SERIES", "OTHER_SERIES", "SERIES_ADMITTED", "READ_FROM_ISO",
                 "NOT_DECIDABLE_ISO", "decide_quote", "cluster_bootstrap", "evaluate_gate", "PRIMARY_RUN", "BASELINE_GAP_S")
        before = {n: id(getattr(sim, n)) for n in names}
        from tools import kalshi_lip_f2 as f2
        import importlib
        importlib.reload(f2)
        after = {n: id(getattr(sim, n)) for n in names}
        assert before == after
        print("ok")
    """ % str(REPO))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr
    before = sim_state()
    importlib.reload(f2)
    assert all(sim_state()[n] is before[n] for n in before)


# ================================================== (2) LIP-F1 output is byte-identical with the patch absent
def test_a_lip_f1_run_is_byte_identical_before_and_after_f2_runs_in_the_same_process(tmp_path, monkeypatch, covered, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    sim.run_sim(w.cfg(out=str(tmp_path / "f1a")))
    f1_before = md5_dir(tmp_path / "f1a")
    for variant in ("A", "B"):
        f2.run_f2(f2_cfg(w, tmp_path / f"f2{variant}", variant), variant, ctx_for(texts, gates))
        with pytest.raises(f2.F2Refusal):                                # a failed start leaves nothing behind
            with f2.f2_cohort("2026-10-19T03:00:00Z", variant):
                pass
    sim.run_sim(w.cfg(out=str(tmp_path / "f1b")))
    assert md5_dir(tmp_path / "f1b") == f1_before
    gate = json.loads((tmp_path / "f1b" / "gate.json").read_text())
    assert gate["book"] == "A" and gate["freeze"] == "2026-10-19T03:33:16Z" and gate["deciding"] is True
    assert "p_adj" not in json.dumps(gate) and "f2_" not in json.dumps(gate)        # no F2 field leaks into LIP-F1
    assert "LIP-F1 formula screen | Book A" in (tmp_path / "f1b" / "table.txt").read_text()


def test_with_the_patch_absent_the_wrappers_are_not_in_the_simulator():
    assert sim.decide_quote is f2.F1_FUNCS["decide_quote"] and sim.cluster_bootstrap is f2.F1_FUNCS["cluster_bootstrap"]
    out = sim.cluster_bootstrap([1.0, 2.0, -0.5], [1.0, 1.0, 1.0])
    assert set(out) == {"p05", "p95", "mean", "clusters"}                  # LIP-F1's dict: no p_adj
    assert "p_adj" not in sim.evaluate_gate([], boot=False)["G4"]


# ======================================================================== (3) F2-A SIDE-CAP, the rule
def a_rule_checks(decide, frozen=f2.F1_FUNCS["decide_quote"]):
    """Raises AssertionError unless ``decide`` is the F2-A rule: zero a side whose best bid is ABOVE 7000 bp (exactly 7000
    is kept), applied with the Q5 sizes, Q4 re-checked with size 0."""
    u = st.mk_unit()                                                      # T = 1000, DF 0.5
    deep = 1000.0
    for by, want in ((6900, 25), (7000, 25), (7001, 0), (7100, 0), (8900, 0)):
        bn = max(1000, min(2800, 9800 - by))                              # b_y + b_n < 10000 and in the band
        q, why = decide(u, snap(1, [(by, deep)], [(bn, deep)]), 0.0, 0.0, 25, 50)
        assert q is not None and why == "ok" and (q.sy, q.sn) == (want, 25), (by, q and (q.sy, q.sn))
    for bn, want in ((7000, 25), (7001, 0), (7100, 0)):                    # the mirror: the NO side
        q, why = decide(u, snap(1, [(2800, deep)], [(bn, deep)]), 0.0, 0.0, 25, 50)
        assert q is not None and (q.sy, q.sn) == (25, want), bn
    # Q4 is re-checked with size 0: depth 990 passes Q4 with s = 25 (990 >= 975) but not with s = 0 (990 < 1000)
    near = snap(1, [(7100, 990.0)], [(2800, deep)])
    assert frozen(u, near, 0.0, 0.0, 25, 50)[0] is not None               # the frozen rule quotes it
    assert decide(u, near, 0.0, 0.0, 25, 50) == (None, "Q4_SIDECAP")
    assert decide(u, snap(1, [(7100, 1000.0)], [(2800, deep)]), 0.0, 0.0, 25, 50)[0].sy == 0   # exactly T: valid
    assert decide(u, snap(1, [(7000, 990.0)], [(2800, deep)]), 0.0, 0.0, 25, 50)[0].sy == 25   # 7000 kept: Q4 as frozen
    assert decide(u, snap(1, [(2800, deep)], [(7100, 990.0)]), 0.0, 0.0, 25, 50) == (None, "Q4_SIDECAP")
    # together with the inventory sizes: Q5 says 10, the cap says 0, the minimum is 0; below the cap Q5 stands
    capped = snap(1, [(7100, deep)], [(2800, deep)])
    assert decide(u, capped, 40.0, 0.0, 25, 50)[0].sy == 0                # Q5 gave 10
    assert decide(u, snap(1, [(6000, deep)], [(3900, deep)]), 40.0, 0.0, 25, 50)[0].sy == 10
    assert decide(u, capped, 50.0, 0.0, 25, 50)[0].sy == 0               # Q5 already 0: unchanged
    q, _ = decide(u, capped, -30.0, 0.0, 25, 50)                         # net short YES: the cap still zeroes the YES side
    assert (q.sy, q.sn) == (0, 25) or q.sy == 0
    # R2: the cap zeroes one side and Q5 already zeroed the other (|net| at I_max): nothing rests, no quote
    lop = snap(1, [(7200, deep)], [(2700, deep)])                         # YES 72c / NO 27c
    assert decide(u, lop, 0.0, 50.0, 25, 50) == (None, "Q_SIDECAP_EMPTY")  # N = 50: Q5 gives sn = 0, the cap gives sy = 0
    assert decide(u, snap(1, [(2700, deep)], [(7200, deep)]), 50.0, 0.0, 25, 50) == (None, "Q_SIDECAP_EMPTY")   # the mirror
    assert decide(u, lop, 0.0, 49.0, 25, 50)[0].sn == 1.0 and decide(u, lop, 0.0, 49.0, 25, 50)[0].sy == 0   # sn = 1: quoted
    q, _ = decide(u, snap(1, [(7000, deep)], [(2900, deep)]), 0.0, 50.0, 25, 50)             # 7000 is kept: Q5's zero is the NO side
    assert (q.sy, q.sn) == (25, 0)
    # the other tests of the frozen rule are unchanged: crossed / empty / out-of-band books
    assert decide(u, snap(1, [], [(2800, deep)]), 0.0, 0.0, 25, 50) == (None, "Q2")
    assert decide(u, snap(1, [(950, deep)], [(2800, deep)]), 0.0, 0.0, 25, 50) == (None, "Q3")
    assert decide(u, snap(1, [(7100, deep)], [(2900, deep)]), 0.0, 0.0, 25, 50) == (None, "Q2")   # 7100 + 2900 = 10000


def test_f2_a_zeroes_a_side_only_above_7000_bp_and_rechecks_q4_with_size_zero():
    assert f2.SIDE_CAP_BP == 7000
    a_rule_checks(f2._side_cap(f2.F1_FUNCS["decide_quote"]))
    with f2.f2_cohort(F2_FREEZE, "A"):
        a_rule_checks(sim.decide_quote)                                    # the patched simulator function itself
    with f2.f2_cohort(F2_FREEZE, "B"):                                     # F2-B has no side cap
        u = st.mk_unit()
        q, _ = sim.decide_quote(u, snap(1, [(7100, 1000.0)], [(2800, 1000.0)]), 0.0, 0.0, 25, 25)
        assert (q.sy, q.sn) == (25, 25)


def test_both_sides_cannot_exceed_7000_bp_at_once():
    u = st.mk_unit()
    for by in range(7001, 9000, 97):
        for bn in range(7001, 9000, 101):
            assert sim.decide_quote(u, snap(1, [(by, 1000.0)], [(bn, 1000.0)]), 0.0, 0.0, 25, 50) == (None, "Q2")


def test_f2_a_equals_the_frozen_rule_wherever_no_side_is_above_the_cap():
    rnd = random.Random(11)
    orig = f2.F1_FUNCS["decide_quote"]
    decide = f2._side_cap(orig)
    u = st.mk_unit()
    n_cap = 0
    for _ in range(3000):
        by, bn = rnd.randint(800, 9200), rnd.randint(800, 9200)
        s = snap(rnd.choice([1, 2, 99999]), [(by, rnd.choice([900.0, 975.0, 990.0, 1000.0, 1200.0]))],
                 [(bn, rnd.choice([900.0, 975.0, 1000.0]))])
        Y, N = rnd.choice([0.0, 10.0, 40.0, 50.0]), rnd.choice([0.0, 10.0, 40.0])
        a, b = decide(u, s, Y, N, 25, 50), orig(u, s, Y, N, 25, 50)
        if by <= 7000 and bn <= 7000:
            assert a[1] == b[1] and ((a[0] is None) == (b[0] is None)) and (a[0] is None or
                                                                         (a[0].sy, a[0].sn) == (b[0].sy, b[0].sn))
        else:
            n_cap += 1
            if a[0] is not None:
                assert b[0] is not None and (a[0].sy == 0 or by <= 7000) and (a[0].sn == 0 or bn <= 7000)
                assert (a[0].by, a[0].bn) == (b[0].by, b[0].bn)             # prices unchanged
    assert n_cap > 100


def test_the_reward_of_f2_a_comes_from_the_other_side_only_and_a_zeroed_side_takes_no_fill():
    deep = dict(yes=[(0.72, 1000)], no=[(0.27, 1000)])
    u = st.mk_unit(e=3000.0)
    snaps = [st.bsnap(0, **deep), st.bsnap(220, **deep)]
    trades = [st.trade(10, "y1", 0.72, 25, "no"),       # would hit our YES bid at 0.72 (taker no, yes_price <= 0.72)
              st.trade(20, "n1", 0.80, 10, "yes")]       # no_price 0.20 <= 0.27: hits our NO bid
    f1 = st.run(u, snaps, trades)
    with f2.f2_cohort(F2_FREEZE, "A"):
        a = st.run(u, snaps, trades)
    assert [(f[0], f[1]) for f in f1.fills] == [("y1", "yes"), ("n1", "no")]
    assert [(f[0], f[1]) for f in a.fills] == [("n1", "no")]                # no YES fill: that side is not posted
    assert a.decisions[0][2][2] == 0 and a.decisions[0][2][3] == 25
    # v_k = phi of the side we post: the YES side has size 0 (phi 0) but is valid with rivals alone (1000 >= T)
    vy, py, _ = sim.side_phi(snaps[0].yes, 1000.0, 0.5, 7200, 0.0)
    vn, pn, _ = sim.side_phi(snaps[0].no, 1000.0, 0.5, 2700, 25.0)
    assert vy and vn and py == 0.0 and pn > 0
    ok, v = sim.snapshot_value(snaps[0], 7200, 2700, 0.0, 25.0, 1000.0, 0.5)
    assert ok and v == pytest.approx(pn)
    ok1, v1 = sim.snapshot_value(snaps[0], 7200, 2700, 25.0, 25.0, 1000.0, 0.5)
    assert ok1 and v1 > v                                                   # F2-A earns less than the frozen rule here
    # the accumulated reward: the first interval is (2, 222] = 220 s at v (before the n1 fill at 20 s shrinks the NO size)
    r_ = sim.finalize_rewards(a, u)
    assert 0 < r_["c"] < sim.finalize_rewards(f1, u)["c"]

def test_whenever_f2_a_quotes_both_sides_are_valid_so_the_posted_side_always_earns():
    """Q4 with size 0 on the zeroed side (depth >= T) and with s on the posted side (depth + s >= T) is what makes
    'both sides must still be valid' hold at every quoted snapshot."""
    rnd = random.Random(5)
    u = st.mk_unit()
    decide = f2._side_cap(f2.F1_FUNCS["decide_quote"])
    quoted = zeroed = 0
    for _ in range(2000):
        by, bn = rnd.randint(1000, 8900), rnd.randint(1000, 2900)
        s = snap(1, [(by, rnd.choice([960.0, 975.0, 990.0, 1000.0, 1500.0]))], [(bn, rnd.choice([960.0, 975.0, 1000.0]))])
        q, _why = decide(u, s, 0.0, 0.0, 25, 50)
        if q is None:
            continue
        quoted += 1
        assert q.sy > 0 or q.sn > 0                                        # R2: never a quote that rests nothing
        zeroed += q.sy == 0 or q.sn == 0
        valid, v = sim.snapshot_value(s, q.by, q.bn, q.sy, q.sn, 1000.0, 0.5)
        assert valid and v > 0
    assert quoted > 100 and zeroed > 10


def test_r2_a_snapshot_that_would_rest_nothing_adds_no_quoted_time_and_cannot_make_a_full_unit():
    lop = dict(yes=[(0.72, 1000)], no=[(0.27, 1000)])
    u = st.mk_unit(e=10_000.0)
    snaps = [st.bsnap(220 * k, **lop) for k in range(40)]
    trades = [st.trade(10, "n1", 0.80, 25, "yes"), st.trade(230, "n2", 0.80, 25, "yes")]      # two NO fills: net NO = 50 = I_max
    f1 = st.run(u, snaps, trades)
    with f2.f2_cohort(F2_FREEZE, "A"):
        a = st.run(u, snaps, trades)
    assert sum(f[3] for f in a.fills) == 50.0 and a.reasons.get("Q_SIDECAP_EMPTY", 0) >= 30
    assert a.n_snaps_quoted == 2 and a.quoted_us < sec(3600)                  # nothing rests once the NO side is full
    assert f1.n_snaps_quoted == 40 and f1.quoted_us >= sec(3600)               # the frozen rule keeps quoting the YES side
    assert all(d[1] == "Q_SIDECAP_EMPTY" for d in a.decisions[2:])


def test_a_snapshot_with_one_zeroed_side_still_counts_as_quoted_time_for_the_full_unit_test():
    deep = dict(yes=[(0.72, 1000)], no=[(0.27, 1000)])
    u = st.mk_unit(e=3000.0)
    snaps = [st.bsnap(0, **deep), st.bsnap(220, **deep)]
    with f2.f2_cohort(F2_FREEZE, "A"):
        a = st.run(u, snaps, [])
    f1 = st.run(u, snaps, [])
    assert a.n_snaps_quoted == f1.n_snaps_quoted == 2 and a.quoted_us == f1.quoted_us > 0


# ======================================================================== (4) F2-B INV25, the rule
def test_f2_b_runs_with_i_max_25_and_f2_a_with_the_frozen_i_max_50():
    assert f2.IMAX == {"A": 50.0, "B": 25.0} and f2.SIZE == 25.0
    cb, ca = f2.make_cfg("B", freeze_us=st.at(F2_FREEZE)), f2.make_cfg("A", freeze_us=st.at(F2_FREEZE))
    assert (cb.size, cb.imax, cb.book) == (25.0, 25.0, "A") and (ca.size, ca.imax, ca.book) == (25.0, 50.0, "A")
    for bad in ({"size": 100.0}, {"imax": 40.0}, {"book": "B"}, {"force_source": "obrec-1019c"}, {"registry_pins": {}},
                {"stress": False}):
        with pytest.raises(f2.F2Refusal, match="fixed for F2"):
            f2.make_cfg("B", freeze_us=st.at(F2_FREEZE), **bad)
    with pytest.raises(TypeError):                                           # the freeze is a keyword given once
        f2.make_cfg("B", freeze_us=1, **{"freeze_us": 2})


def test_f2_b_stacking_i_max_25_stops_posting_on_the_side_that_adds_inventory():
    good = st.GOOD
    u = st.mk_unit(e=3000.0)
    snaps = [st.bsnap(0, **good), st.bsnap(220, **good), st.bsnap(440, **good), st.bsnap(660, **good)]
    # three same-direction prints, each hits our YES bid for 25
    trades = [st.trade(10, "y1", 0.48, 25, "no"), st.trade(230, "y2", 0.48, 25, "no"), st.trade(450, "y3", 0.48, 25, "no")]
    a = st.run(u, snaps, trades, S=25, imax=50)                             # LIP-F1 rule: stacks to +50
    b = st.run(u, snaps, trades, S=25, imax=25)                             # F2-B: stops at +25
    assert [f[0] for f in a.fills] == ["y1", "y2"] and sum(f[3] for f in a.fills) == 50.0
    assert [f[0] for f in b.fills] == ["y1"] and sum(f[3] for f in b.fills) == 25.0
    ydecisions = lambda r: [d[2][2] for d in r.decisions]
    nodecisions = lambda r: [d[2][3] for d in r.decisions]
    assert ydecisions(a) == [25, 25, 0, 0] and ydecisions(b) == [25, 0, 0, 0]       # YES size after the first fill
    assert nodecisions(a) == nodecisions(b) == [25, 25, 25, 25]                      # the NO side keeps posting 25
    # the opposite print then unwinds it: B posts the YES side again once net returns below I_max
    back = trades[:1] + [st.trade(230, "n1", 0.60, 25, "yes")]
    r = st.run(u, snaps, back, S=25, imax=25)
    assert [f[1] for f in r.fills] == ["yes", "no"] and ydecisions(r)[2] == 25


def test_f2_b_end_to_end_stacks_less_than_the_frozen_rule(tmp_path, monkeypatch, covered, texts, gates):
    rows = [("s1", st.GAS_A, "2026-10-19T12:10:00Z", 0.48, 25, "no"), ("s2", st.GAS_A, "2026-10-19T12:20:00Z", 0.48, 25, "no"),
            ("s3", st.GAS_A, "2026-10-19T12:30:00Z", 0.48, 25, "no")]
    w = f2_world(tmp_path / "w", monkeypatch)
    w.add_trades(rows, [st.GAS_A, st.GAS_B], name="trades2", since="2026-10-19T03:33:16Z", until="2026-10-20T00:00:00Z",
                 run_at="2026-10-20T04:41:00Z")
    got = {}
    for variant in ("A", "B"):
        f2.run_f2(f2_cfg(w, tmp_path / f"o{variant}", variant), variant, ctx_for(texts, gates))
        units = pq.read_table(tmp_path / f"o{variant}" / "units.parquet").to_pylist()
        got[variant] = next(u["fills_YES"] for u in units if u["unit_id"] == "pA" and u["cell"] == "U0")
    assert got["A"] > got["B"] and got["B"] <= 25.0 + 25.0                # I_max 25 can overshoot by one in-flight fill
    assert got["A"] >= 50.0


# =========================================== (5) mutation checks: SIM-SPEC 14 under the F2 context, and F2's own
@pytest.mark.parametrize("variant", ["A", "B"])
def test_the_simspec_14_mutation_checks_still_fail_as_they_should_under_the_f2_context(variant):
    with f2.f2_cohort(F2_FREEZE, variant):
        assert st._survivors(lambda mp: None) == []                        # the unmutated (patched) module passes every killer
        for name, mutant in sorted(st.MUTANTS.items()):
            assert st._survivors(mutant), f"mutant survived under F2-{variant}: {name}"
    assert f2.sim_is_lip_f1()


def _mut_boundary_ge(orig):
    def d(u, s, Y, N, S, imax):
        q, why = orig(u, s, Y, N, S, imax)
        if q is None:
            return q, why
        sy = 0.0 if q.by >= f2.SIDE_CAP_BP else q.sy
        sn = 0.0 if q.bn >= f2.SIDE_CAP_BP else q.sn
        return sim.Quote(q.by, q.bn, sy, sn, q.dy, q.dn), why
    return d


def _mut_no_q4_recheck(orig):
    def d(u, s, Y, N, S, imax):
        q, why = orig(u, s, Y, N, S, imax)
        if q is None:
            return q, why
        return sim.Quote(q.by, q.bn, 0.0 if q.by > 7000 else q.sy, 0.0 if q.bn > 7000 else q.sn, q.dy, q.dn), why
    return d


def _mut_yes_only(orig):
    def d(u, s, Y, N, S, imax):
        q, why = orig(u, s, Y, N, S, imax)
        if q is None or q.by <= 7000:
            return q, why
        T = u.pset0[1]
        if s.depth_y + sim.EPS < T:
            return None, "Q4_SIDECAP"
        return sim.Quote(q.by, q.bn, 0.0, q.sn, q.dy, q.dn), why
    return d


def _mut_cap_overrides_q5(orig):
    def d(u, s, Y, N, S, imax):
        q, why = orig(u, s, Y, N, S, imax)
        if q is None:
            return q, why
        sy = 0.0 if q.by > 7000 else S
        sn = 0.0 if q.bn > 7000 else S
        return sim.Quote(q.by, q.bn, sy, sn, q.dy, q.dn), why
    return d


def _mut_allow_empty(orig):
    def d(u, s, Y, N, S, imax):
        q, why = orig(u, s, Y, N, S, imax)
        if q is None:
            return q, why
        sy = 0.0 if q.by > 7000 else q.sy
        sn = 0.0 if q.bn > 7000 else q.sn
        T = u.pset0[1]
        if s.depth_y + sim.EPS < T - sy or s.depth_n + sim.EPS < T - sn:
            return None, "Q4_SIDECAP"
        return sim.Quote(q.by, q.bn, sy, sn, q.dy, q.dn), why
    return d


@pytest.mark.parametrize("mutant", [_mut_boundary_ge, _mut_no_q4_recheck, _mut_yes_only, _mut_cap_overrides_q5, _mut_allow_empty])
def test_each_mutation_of_the_side_cap_is_caught_by_a_rule_test(mutant):
    a_rule_checks(f2._side_cap(f2.F1_FUNCS["decide_quote"]))               # the real wrapper passes ...
    with pytest.raises(AssertionError):                                    # ... and the mutated one does not
        a_rule_checks(mutant(f2.F1_FUNCS["decide_quote"]))


@pytest.mark.parametrize("cap_bp", [6999, 7001])
def test_a_moved_cap_constant_is_caught(monkeypatch, cap_bp):
    monkeypatch.setattr(f2, "SIDE_CAP_BP", cap_bp)
    with pytest.raises(AssertionError):
        a_rule_checks(f2._side_cap(f2.F1_FUNCS["decide_quote"]))


# ======================================== (6) the deciding bound: the 1.667th percentile of the same draw matrix
def test_the_adjusted_percentile_is_5_over_m_with_m_3():
    assert f2.M_GATED == 3 and f2.ADJ_PCT == pytest.approx(5.0 / 3.0) and round(f2.ADJ_PCT, 3) == 1.667


@pytest.mark.parametrize("sums,ns", [([10.0] * 10 + [-20.0], [10.0] * 11), ([3.0, -1.0, 4.0, 1.5, 9.0, 2.6, 5.3], [4, 2, 5, 3, 6, 3, 4]),
                                     ([1.0, 2.0], [1, 1]), ([5.0], [3])])
def test_the_adjusted_bound_reproduces_p05_exactly_from_the_same_draw_matrix(sums, ns):
    idx = np.random.RandomState(1).randint(0, len(sums), size=(1000, len(sums)))       # the simulator's matrix, written here
    s, n = np.asarray(sums, float), np.asarray(ns, float)
    stat = s[idx].sum(axis=1) / n[idx].sum(axis=1)
    frozen = sim.cluster_bootstrap(sums, ns)                                           # LIP-F1's function, unpatched
    assert frozen["p05"] == float(np.percentile(stat, 5))                               # our matrix IS the simulator's
    with f2.f2_cohort(F2_FREEZE, "B"):
        got = sim.cluster_bootstrap(sums, ns)
    assert got["p05"] == frozen["p05"] and got["p95"] == frozen["p95"] and got["mean"] == frozen["mean"]
    assert got["p_adj"] == float(np.percentile(stat, 5.0 / 3.0)) and got["adj_pct"] == pytest.approx(5.0 / 3.0)
    assert got["p_adj"] <= got["p05"]
    assert f2.bootstrap_stat(sums, ns, 1000, 1).tolist() == stat.tolist()


def test_an_empty_cluster_set_has_no_adjusted_bound_and_a_changed_matrix_is_refused(monkeypatch):
    with f2.f2_cohort(F2_FREEZE, "A"):
        e = sim.cluster_bootstrap([], [])
        assert e["p05"] is None and e["p_adj"] is None and e["clusters"] == 0
    monkeypatch.setattr(f2, "bootstrap_stat", lambda *a: np.arange(1000.0))
    with f2.f2_cohort(F2_FREEZE, "A"):
        with pytest.raises(f2.F2Refusal, match="does not reproduce"):
            sim.cluster_bootstrap([1.0, 2.0, 3.0], [1, 1, 1])


def gate_rows(day_nets, per_day=10):
    """Population rows for evaluate_gate: ``per_day`` gas units a day, each its own event, day net split evenly."""
    rows = []
    for d, tot in enumerate(day_nets):
        day = f"2026-10-{20 + d:02d}"
        for k in range(per_day):
            net = tot / per_day
            rows.append({"unit_id": f"u{d}-{k}", "event": f"E{d}-{k}", "day": day, "day_e": day, "gas": True, "full": True,
                         "net": net, "net_lower": net, "worst_u": -0.5})
    return rows


def test_g4_and_g4b_decide_at_the_adjusted_bound_and_show_p05_beside_it():
    rows = gate_rows([10.0] * 10 + [-20.0])                                          # 110 full units, 11 gas days
    frozen = sim.evaluate_gate(rows)
    assert frozen["all_pass"] and frozen["G4b"]["pass"] and frozen["G4b"]["p05"] > 0   # LIP-F1's rule: a pass
    with f2.f2_cohort(F2_FREEZE, "B"):
        g = sim.evaluate_gate(rows)
    assert g["G4b"]["clusters"] == 11 and g["G4b"]["p05"] == frozen["G4b"]["p05"] > 0
    assert g["G4b"]["p_adj"] <= 0 < g["G4b"]["p05"]
    assert g["G4b"]["pass"] is False and g["G4b"]["pass_unadjusted"] is True       # a pass on p05 only
    assert g["G4"]["pass"] is True and g["G4"]["pass_unadjusted"] is True          # event-clustered: 110 clusters
    assert g["all_pass"] is False and g["all_pass_unadjusted"] is True
    for k in ("G1", "G2", "G3", "G5", "G6", "G7"):
        assert g[k] == frozen[k]                                                   # every other gate is the simulator's own
    ok = gate_rows([10.0] * 11)
    with f2.f2_cohort(F2_FREEZE, "A"):
        g2 = sim.evaluate_gate(ok)
    assert g2["all_pass"] and g2["G4b"]["p_adj"] > 0 and g2["G4b"]["pass"] and g2["all_pass_unadjusted"]
    # counts-only evaluation (no bootstrap) stays a non-pass
    with f2.f2_cohort(F2_FREEZE, "A"):
        g3 = sim.evaluate_gate(ok, boot=False)
    assert g3["G4"]["pass"] is False and g3["G4b"]["pass"] is False and g3["all_pass"] is False


def cells_gate(verdict, **unadj):
    return {"verdict": verdict, "cells": {c: {"gate": {"all_pass_unadjusted": unadj.get(c, True)}} for c in sim.CELLS}}


def test_the_verdict_wording_a_p05_only_pass_is_fail_multiplicity_adjusted():
    assert f2.final_verdict(cells_gate("FORMULA_SCREEN_PASSED")) == "FORMULA_SCREEN_PASSED (multiplicity-adjusted)"
    assert f2.final_verdict(cells_gate("FAIL")) == "FAIL (multiplicity-adjusted)"                # every cell passes on p05
    assert f2.final_verdict(cells_gate("FAIL", BQ=False)) == "FAIL"                              # one cell fails on p05 too
    for v in ("COUNTS_NOT_MET", "NOT_DECIDABLE", "DEAD_AS_FROZEN", "SCAN_REPORT_ONLY_WOULD_FAIL"):
        assert f2.final_verdict(cells_gate(v)) == v
    assert f2.final_verdict({"verdict": "NOT_DECIDABLE", "cells": {}}) == "NOT_DECIDABLE"
    assert f2.final_verdict({"verdict": "FAIL", "cells": {}}) == "FAIL"


# ============================================================ (7) refusal paths: the two process gates, R, the pins
def test_the_lip_f1_gate_must_be_a_final_deciding_book_a_read_instant_file(tmp_path):
    ok = lip_gate(tmp_path)
    info = f2.check_lip_f1_gate(str(ok))
    assert info == {"path": str(ok), "sha256": sha(ok), "read_instant": "2026-10-24T00:00:00Z"}
    for v in ("FORMULA_SCREEN_PASSED", "FAIL", "DEAD_AS_FROZEN"):
        assert f2.check_lip_f1_gate(str(lip_gate(tmp_path, verdict=v)))["sha256"]
    # a COUNTS_NOT_MET candidate is not final ... except the read at the NOT_DECIDABLE date
    with pytest.raises(f2.F2Refusal, match="LIP-F1 not final"):
        f2.check_lip_f1_gate(str(lip_gate(tmp_path, verdict="COUNTS_NOT_MET")))
    with pytest.raises(f2.F2Refusal, match="LIP-F1 not final"):
        f2.check_lip_f1_gate(str(lip_gate(tmp_path, verdict="COUNTS_NOT_MET", read_instant="2026-11-01T00:00:00Z")))
    assert f2.check_lip_f1_gate(str(lip_gate(tmp_path, verdict="COUNTS_NOT_MET", read_instant="2026-11-02T00:00:00Z")))
    for over in ({"book": "C"}, {"book": "F2-A"}, {"book": "B"}, {"deciding": False}, {"deciding": Ellipsis}, {"scan": {"x": 1}},
                 {"freeze": "2026-10-20T11:00:00Z"}, {"freeze": Ellipsis}):
        with pytest.raises(f2.F2Refusal, match="is not a LIP-F1 deciding gate.json"):
            f2.check_lip_f1_gate(str(lip_gate(tmp_path, **over)))
    for v in ("SCAN_REPORT_ONLY_WOULD_PASS_IF_READ_AT_R", "PENDING", "REPORT_ONLY_FAIL", None, Ellipsis):
        with pytest.raises(f2.F2Refusal, match="LIP-F1 not final"):
            f2.check_lip_f1_gate(str(lip_gate(tmp_path, verdict=v)))
    with pytest.raises(f2.F2Refusal, match="LIP-F1 not final"):
        f2.check_lip_f1_gate(None)
    with pytest.raises(f2.F2Refusal, match="LIP-F1 not final"):
        f2.check_lip_f1_gate(str(tmp_path / "nope.json"))
    with pytest.raises(f2.F2Refusal, match="LIP-F1 not final"):
        f2.check_lip_f1_gate(str(tmp_path))                                # a directory is not a file
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(f2.F2Refusal, match="not readable JSON"):
        f2.check_lip_f1_gate(str(bad))
    bad.write_text("[1, 2]")
    with pytest.raises(f2.F2Refusal, match="not a JSON object"):
        f2.check_lip_f1_gate(str(bad))


def nd_gate(tmp, reasons, counts=(False, False, False), r="2026-10-25T00:00:00Z", **over):
    cells = {c: {"gate": {"counts_met": m}} for c, m in zip(sim.CELLS, counts)}
    return lip_gate(tmp, verdict="NOT_DECIDABLE", nd_reasons=reasons, read_instant=r, cells=cells, **over)


def test_r1_r11_a_lip_f1_not_decidable_is_final_only_with_counts_met_or_the_nd_date(tmp_path):
    excl = "excluded units (no trades / no metadata) exceed 10% of the units that would otherwise be opened"
    cov = "recorder/poller coverage < 70% on two consecutive days"          # the simulator's own wording; no longer an exception
    assert not hasattr(f2, "COVERAGE_ND_REASON")
    # an exclusion-share OR coverage NOT_DECIDABLE at 10-25 with the counts not met is a candidate, not a deciding file
    for reasons in ([excl], [cov], [excl, cov]):
        for counts in ((False, False, False), (True, True, False), (True, False, True)):
            with pytest.raises(f2.F2Refusal, match="skipped: LIP-F1 not final"):
                f2.check_lip_f1_gate(str(nd_gate(tmp_path, reasons, counts)))
        with pytest.raises(f2.F2Refusal, match="skipped: LIP-F1 not final"):
            f2.check_lip_f1_gate(str(nd_gate(tmp_path, reasons, r="2026-11-01T00:00:00Z")))
    with pytest.raises(f2.F2Refusal, match="skipped: LIP-F1 not final"):            # no cells at all (units-evidence path)
        f2.check_lip_f1_gate(str(lip_gate(tmp_path, verdict="NOT_DECIDABLE", nd_reasons=["units not verified"], read_instant=Ellipsis)))
    # R11: the same coverage-only file is accepted once the counts are met in all three cells, or at the ND date
    assert f2.check_lip_f1_gate(str(nd_gate(tmp_path, [cov], (True, True, True))))["read_instant"] == "2026-10-25T00:00:00Z"
    assert f2.check_lip_f1_gate(str(nd_gate(tmp_path, [cov], r="2026-11-02T00:00:00Z")))
    assert f2.check_lip_f1_gate(str(nd_gate(tmp_path, [excl], (True, True, True))))
    assert f2.check_lip_f1_gate(str(nd_gate(tmp_path, [excl], r="2026-11-02T00:00:00Z")))
    # PASS / FAIL / DEAD handling is unchanged
    for v in ("FORMULA_SCREEN_PASSED", "FAIL", "DEAD_AS_FROZEN"):
        assert f2.check_lip_f1_gate(str(lip_gate(tmp_path, verdict=v)))


def test_r3_only_book_cs_gate_json_at_lip_f1s_r_counts_as_done(tmp_path):
    r = "2026-10-24T00:00:00Z"
    ok = bookc_marker(tmp_path)
    assert f2.check_bookc_done(str(ok), r) == {"path": str(ok), "sha256": sha(ok)}
    for arg in (None, str(tmp_path / "missing"), str(tmp_path)):
        with pytest.raises(f2.F2Refusal, match="Book C is not done"):
            f2.check_bookc_done(arg, r)
    start = tmp_path / "bookc-out" / f2.BOOKC_START_MARKER
    start.parent.mkdir()
    start.write_text(json.dumps(dict(BOOKC_GATE)))
    with pytest.raises(f2.F2Refusal, match="written when a Book C run starts"):
        f2.check_bookc_done(str(start), r)
    # any other file is refused: not JSON, not an object, no book key, or a different book / flags / scan
    arbitrary = tmp_path / "bookc-done" / "done.txt"
    arbitrary.write_text("done\n")
    with pytest.raises(f2.F2Refusal, match="not readable JSON"):
        f2.check_bookc_done(str(arbitrary), r)
    with pytest.raises(f2.F2Refusal, match="not a JSON object"):
        f2.check_bookc_done(str(bookc_marker(tmp_path, "[1]")), r)
    for body in ({}, {"read_instant": r}, dict(BOOKC_GATE, book="A"), dict(BOOKC_GATE, book="F2-A"), dict(BOOKC_GATE, report_only=False),
                 dict(BOOKC_GATE, promotable=True), {k: v for k, v in BOOKC_GATE.items() if k != "promotable"},
                 {k: v for k, v in BOOKC_GATE.items() if k != "report_only"}, dict(BOOKC_GATE, scan={"x": 1})):
        with pytest.raises(f2.F2Refusal, match="is not Book C's gate.json"):
            f2.check_bookc_done(str(bookc_marker(tmp_path, json.dumps(body))), r)
    # read at LIP-F1's R: another R, a missing R, or a LIP-F1 gate without an R are refused
    for body in (dict(BOOKC_GATE, read_instant="2026-10-25T00:00:00Z"), {k: v for k, v in BOOKC_GATE.items() if k != "read_instant"},
                 dict(BOOKC_GATE, read_instant="junk"), dict(BOOKC_GATE, read_instant=None)):
        with pytest.raises(f2.F2Refusal, match="was not read at LIP-F1's R"):
            f2.check_bookc_done(str(bookc_marker(tmp_path, json.dumps(body))), r)
    with pytest.raises(f2.F2Refusal, match="no read_instant"):
        f2.check_bookc_done(str(ok), None)


def test_r3_a_book_c_gate_read_at_another_r_blocks_the_cli(tmp_path, monkeypatch, capsys, texts, decl, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    monkeypatch.setattr(sim, "run_sim", lambda cfg: pytest.fail("the simulator ran"))
    other = bookc_marker(tmp_path, json.dumps(dict(BOOKC_GATE, read_instant="2026-10-25T00:00:00Z")), name="other-gate.json")
    argv = cli_argv(w, tmp_path, texts, decl, dict(gates, bookc=str(other))) + ["--read-instant", R]
    assert f2.main(argv) == 2 and "was not read at LIP-F1's R" in capsys.readouterr().err
    with pytest.raises(f2.F2Refusal, match="was not read at LIP-F1's R"):
        f2.run_f2(f2_cfg(w, tmp_path / "o"), "A", ctx_for(texts, dict(gates, bookc=str(other))))
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("flags", [["--read-instant", R], ["--scan"], ["--scan", "--asof", R]])
def test_outcome_computing_modes_are_refused_without_the_two_gates_and_the_simulator_never_runs(
        tmp_path, monkeypatch, capsys, texts, decl, gates, flags):
    w = f2_world(tmp_path / "w", monkeypatch)
    monkeypatch.setattr(sim, "run_sim", lambda cfg: pytest.fail("the simulator ran without the gates"))
    nongate = tmp_path / "x.json"
    nongate.write_text(json.dumps({"book": "A", "deciding": True, "freeze": "2026-10-19T03:33:16Z", "verdict": "COUNTS_NOT_MET"}))
    cases = [(None, "LIP-F1 not final"),                                               # neither gate
             ({"lip": None, "bookc": gates["bookc"]}, "LIP-F1 not final"),
             ({"lip": str(tmp_path / "nope.json"), "bookc": gates["bookc"]}, "LIP-F1 not final"),
             ({"lip": str(nongate), "bookc": gates["bookc"]}, "LIP-F1 not final"),     # a candidate, not the deciding file
             ({"lip": gates["lip"], "bookc": None}, "Book C is not done"),
             ({"lip": gates["lip"], "bookc": str(tmp_path / "nope")}, "Book C is not done")]
    for gs, msg in cases:
        argv = cli_argv(w, tmp_path, texts, decl, variant="A") + flags
        if gs is not None:
            argv += (["--lip-f1-gate", gs["lip"]] if gs["lip"] else []) + (["--bookc-done", gs["bookc"]] if gs["bookc"] else [])
        assert f2.main(argv) == 2
        err = capsys.readouterr().err
        assert err.startswith("refused:") and msg in err, (gs, err)
    assert not (tmp_path / "out").exists()
    # run_f2 re-checks both gates, so no caller can reach the simulator without them
    for bad_ctx in ({"freeze_iso": F2_FREEZE}, dict(ctx_for(texts, gates), lip_f1_gate={"path": None}),
                    dict(ctx_for(texts, gates), bookc_done={"path": None})):
        with pytest.raises(f2.F2Refusal):
            f2.run_f2(f2_cfg(w, tmp_path / "out2"), "A", bad_ctx)
    assert not (tmp_path / "out2").exists()


def test_a_read_instant_must_be_a_midnight_inside_the_f2_window(tmp_path, monkeypatch, capsys, texts, decl, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    monkeypatch.setattr(sim, "run_sim", lambda cfg: pytest.fail("the simulator ran"))
    for bad, msg in (("2026-10-24T00:00:00Z", "must lie in"), ("2026-10-25T00:00:00Z", "must lie in"),
                     ("2026-10-26T12:00:00Z", "00:00:00Z instant"), ("2026-10-26T00:00:01Z", "00:00:00Z instant"),
                     ("2026-11-03T00:00:00Z", "must lie in"), ("junk", "")):
        assert f2.main(cli_argv(w, tmp_path, texts, decl, gates) + ["--read-instant", bad]) == 2, bad
        assert msg in capsys.readouterr().err, bad
    # F2's window is [2026-10-26, 2026-11-02]; a read at the freeze or before is refused by the same check
    cfg = f2_cfg(w, tmp_path / "o", read_us=st.at("2026-10-19T00:00:00Z"))
    with pytest.raises(f2.F2Refusal, match="must lie in"):
        f2.check_cfg(cfg, "A", st.at(F2_FREEZE))
    for ok in ("2026-10-26T00:00:00Z", "2026-10-27T00:00:00Z", "2026-11-02T00:00:00Z"):
        f2.check_cfg(f2_cfg(w, tmp_path / "o", read_us=st.at(ok)), "A", st.at(F2_FREEZE))
    with pytest.raises(f2.F2Refusal, match="not both"):
        f2.check_cfg(f2_cfg(w, tmp_path / "o", scan=True), "A", st.at(F2_FREEZE))
    with pytest.raises(f2.F2Refusal, match="not both"):
        f2.check_cfg(f2_cfg(w, tmp_path / "o", read_us=None), "A", st.at(F2_FREEZE))


def test_argument_and_pin_refusals(tmp_path, monkeypatch, capsys, texts, decl, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    monkeypatch.setattr(sim, "run_sim", lambda cfg: pytest.fail("the simulator ran"))
    base = lambda **kw: cli_argv(w, tmp_path, texts, decl, gates, **kw) + ["--read-instant", R]
    # wrong declaration pin, wrong --freeze, missing variant, altered inherited text
    argv = base()
    argv[argv.index("--f2-declare-sha256") + 1] = "0" * 64
    assert f2.main(argv) == 2 and "does not match the given pin" in capsys.readouterr().err
    argv = base()
    argv[argv.index("--freeze") + 1] = "2026-10-19T12:00:00Z"
    assert f2.main(argv) == 2 and "--freeze must be the cohort freeze" in capsys.readouterr().err
    assert f2.main(base(variant=None)) == 2 and "--variant A|B is required" in capsys.readouterr().err
    (texts / "DECLARE-FORWARD-CONDITIONS.md").write_text("tampered\n")
    assert f2.main(base()) == 2 and "does not match the pinned" in capsys.readouterr().err
    (texts / "DECLARE-FORWARD-CONDITIONS.md").write_text("synthetic conditions\n")
    # recorders: only obrec-1019c (first) and obrec-1019d; never Book C's obrec-1019e
    for books in (["--books", str(tmp_path / "obrec-1019e")], ["--books", w.books[0], str(tmp_path / "obrec-1019e")],
                  ["--books", str(tmp_path / "obrec-1019d")]):
        argv = base()
        i = argv.index("--books")
        argv[i:i + 1 + len(w.books)] = books
        assert f2.main(argv) == 2 and "obrec-1019c" in capsys.readouterr().err, books
    # inputs that are not arguments at all: the F2 identity is fixed
    for flag in (["--force-source", "obrec-1019c"], ["--size", "100"], ["--imax", "40"], ["--registry-sha256", "obrec-1019c=ab"],
                 ["--no-stress"], ["--conditions", "x"], ["--declare", "x"]):
        with pytest.raises(SystemExit) as e:
            f2.main(base() + flag)
        assert e.value.code == 2, flag
        capsys.readouterr()
    with pytest.raises(SystemExit):
        f2.main(base() + ["--scan"])                                         # --read-instant and --scan are exclusive
    capsys.readouterr()
    assert f2.main(base() + ["--asof", R]) == 2 and "--asof is for --scan" in capsys.readouterr().err
    argv = cli_argv(w, tmp_path, texts, decl, gates, out=None) + ["--read-instant", R]
    assert f2.main(argv) == 2 and "--out is required" in capsys.readouterr().err


@pytest.mark.parametrize("text,msg", [
    ("no freeze line\nF2-A F2-B\n", "exactly one"),
    (decl_text() + decl_text(), "exactly one"),
    ("# title\n" + decl_text(), "first line"),
    (decl_text().replace("F2-B", "F2-X"), "does not name F2-B"),
    (decl_text("2026-10-19T03:33:16Z"), "after LIP-F1's freeze"),
    (decl_text("2026-10-18T03:33:16Z"), "after LIP-F1's freeze"),
    (decl_text().replace("F2 TOOL MD5", "F2 TOOL MD6"), "F2 TOOL MD5"),                      # no md5 line
    (decl_text() + "F2 TOOL MD5 = " + "0" * 32 + "\n", "exactly one 'F2 TOOL MD5"),            # two md5 lines
    (decl_text(md5="0" * 32), "pins F2 TOOL MD5"),                                            # a different tool
    (decl_text(md5=tool_md5().upper()), "F2 TOOL MD5"),                                       # not the lower-case 32-hex form
    (decl_text(md5=tool_md5()[:31]), "F2 TOOL MD5"),
])
def test_declaration_parse_and_pin(tmp_path, text, msg):
    p = tmp_path / "d.md"
    p.write_text(text)
    with pytest.raises(f2.F2Refusal, match=msg):
        f2.load_f2_declaration(str(p), sha(p))


def test_declaration_loading_and_the_first_unit_day(tmp_path):
    p = tmp_path / "d.md"
    p.write_text(decl_text("2026-10-20T10:59:59Z"))
    h, freeze = f2.load_f2_declaration(str(p), sha(p))
    assert (h, freeze) == (sha(p), "2026-10-20T10:59:59Z")
    for bad_pin in (None, "", "abc", "Z" * 64):
        with pytest.raises(f2.F2Refusal, match="required"):
            f2.load_f2_declaration(str(p), bad_pin)
    with pytest.raises(f2.F2Refusal, match="not a file"):
        f2.load_f2_declaration(str(tmp_path / "none"), "0" * 64)
    with pytest.raises(f2.F2Refusal, match="--f2-declare is required"):
        f2.load_f2_declaration(None, "0" * 64)
    # gas periods run 12:00Z -> 03:59Z next day: a freeze before 12:00Z on 10-20 gives unit-day 10-21
    assert f2.first_unit_day("2026-10-20T11:00:00Z") == "10-21" == f2.first_unit_day("2026-10-20T12:00:00Z")
    assert f2.first_unit_day("2026-10-20T12:00:01Z") == "10-22" and f2.first_unit_day("2026-10-19T23:00:00Z") == "10-21"


# =========================================================================================== --out guard
def test_out_must_be_its_own_directory_with_a_marker(tmp_path, monkeypatch, covered, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    ctx = ctx_for(texts, gates)
    # holding LIP-F1 (or Book C) simulator files: refused, nothing overwritten
    sim.run_sim(w.cfg(out=str(tmp_path / "f1out")))
    before = md5_dir(tmp_path / "f1out")
    with pytest.raises(f2.F2Refusal, match="already holds simulator files"):
        f2.run_f2(f2_cfg(w, tmp_path / "f1out"), "A", ctx)
    assert md5_dir(tmp_path / "f1out") == before and not (tmp_path / "f1out" / f2.MARKER).exists()
    # the other variant's directory is refused; the same variant may be re-run into its own
    f2.run_f2(f2_cfg(w, tmp_path / "oA", "A"), "A", ctx)
    assert (tmp_path / "oA" / f2.MARKER).is_file()
    with pytest.raises(f2.F2Refusal, match="another F2 variant"):
        f2.run_f2(f2_cfg(w, tmp_path / "oA", "B"), "B", ctx)
    run1 = md5_dir(tmp_path / "oA")
    f2.run_f2(f2_cfg(w, tmp_path / "oA", "A"), "A", ctx)
    assert md5_dir(tmp_path / "oA") == run1
    # an existing file, an output inside an input store
    (tmp_path / "afile").write_text("x")
    with pytest.raises(f2.F2Refusal, match="not a directory"):
        f2.check_out(tmp_path / "afile", "A")
    with pytest.raises(f2.F2Refusal, match="inside the input store"):
        f2.check_out(Path(w.lip) / "out", "A", [w.lip])
    with pytest.raises(f2.F2Refusal, match="inside the input store"):
        f2.check_out(Path(w.kal) / "deep" / "out", "B", [[str(w.kal) + "/lipdl/run-*"], str(w.kal)])
    f2.check_out(tmp_path / "fresh", "A", [w.lip, [str(w.kal)]])


def test_a_refused_or_failed_run_leaves_no_marker_only_directory(tmp_path, monkeypatch, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    monkeypatch.setattr(f2, "REGISTRY_PINS", {"obrec-1019c": "0" * 64})
    with pytest.raises(sim.InputError, match="registry check failed"):            # a wrong registry pin is refused by the sim
        f2.run_f2(f2_cfg(w, tmp_path / "out"), "A", ctx_for(texts, gates))
    assert not (tmp_path / "out").exists() and f2.sim_is_lip_f1()
    (tmp_path / "pre").mkdir()
    with pytest.raises(sim.InputError):
        f2.run_f2(f2_cfg(w, tmp_path / "pre", "B"), "B", ctx_for(texts, gates))
    assert (tmp_path / "pre").is_dir() and list((tmp_path / "pre").iterdir()) == []


# ======================================================================================= the dry run
ALLOWED_DRY = {"units_by_status", "excl_no_meta_sub", "units_by_source", "inconsistent_snapshots",
               "rows_before_freeze_ignored", "unsealed_parts_excluded", "meta_captures", "meta_markets", "stores", "mode",
               "note", "book", "f2_freeze", "f2_declare_sha256", "f2_universe", "f2_registry_pins", "f2_lip_texts_sha256",
               "f2_sim_md5_pinned", "f2_tool_md5"}
FORBIDDEN_DRY = ("quot", "reward", "fill", "pause", "share", "phi", "pnl", "p&l", "markout", "opened", "zero", "side_cap",
                 "sidecap", "snapshots_in_window", "carry_forward", "gaps_over_cap", "exclusion_share", "events", "unit_days",
                 "structurally", "g4", "kill")


def test_dry_run_prints_loader_counts_only_needs_no_gate_and_never_applies_the_f2_rule(tmp_path, monkeypatch, capsys, texts, decl):
    w = f2_world(tmp_path / "w", monkeypatch,
                 extra_prog=[st.prog("pRT", "KXRT-26OCT19-T3", st.S0, st.E_)],
                 extra_snaps={"KXRT-26OCT19-T3": st.ticks("2026-10-19T12:05:30Z", "2026-10-19T15:30:00Z", 60)})
    seen = []
    real_run_sim = sim.run_sim

    def spy(cfg):
        seen.append({n: getattr(sim, n) is f2.F1_FUNCS[n] for n in f2.PATCHED_FUNCTIONS})
        seen.append(sim.FREEZE_ISO)
        return real_run_sim(cfg)
    monkeypatch.setattr(sim, "run_sim", spy)
    monkeypatch.setattr(sim, "load_trades", lambda *a, **k: pytest.fail("the dry run opened the trades parquet"))
    monkeypatch.setattr(sim, "read_results", lambda *a, **k: pytest.fail("the dry run opened settled results"))
    monkeypatch.setattr(sim, "run_unit", lambda *a, **k: pytest.fail("the dry run ran the pass"))
    outs = []
    for variant in (None, "A", "B"):
        assert f2.main(cli_argv(w, tmp_path, texts, decl, None, variant=variant, out=None) + ["--dry-run"]) == 0
        outs.append(capsys.readouterr().out)
    assert outs[0] == outs[1] == outs[2]                                      # no variant rule is applied in a dry run
    assert seen[0] == {"decide_quote": True, "cluster_bootstrap": True, "evaluate_gate": True}   # the FROZEN functions
    assert seen[1] == F2_FREEZE
    rep = json.loads(outs[0])
    assert set(rep) <= ALLOWED_DRY, set(rep) - ALLOWED_DRY
    assert rep["mode"] == "dry_run" and rep["f2_freeze"] == F2_FREEZE and rep["f2_universe"] == list(sim.GAS_SERIES)
    assert rep["units_by_status"] == {"passed_exclusions": 2}                  # opened / not_opened are collapsed
    assert rep["units_by_source"] == {"obrec-1019c": 2} and rep["inconsistent_snapshots"] == {"obrec-1019c": 0}
    assert rep["unsealed_parts_excluded"] == {"obrec-1019c": 0} and rep["meta_markets"] == 1 and rep["meta_captures"] == 1
    assert rep["stores"]["trades"] and rep["stores"]["markets"]
    blob = json.dumps(rep).lower()
    for word in FORBIDDEN_DRY:
        assert word not in blob, word
    assert "pRT" not in json.dumps(rep) and f2.sim_is_lip_f1()
    # a dry run needs no gates and refuses the outcome-mode arguments
    for extra in (["--read-instant", R], ["--scan"], ["--asof", R], ["--out", str(tmp_path / "o")], ["--debug-snaps"]):
        assert f2.main(cli_argv(w, tmp_path, texts, decl, None, variant=None, out=None) + ["--dry-run"] + extra) == 2
        assert "takes no" in capsys.readouterr().err


def test_dry_run_still_checks_the_texts_the_declaration_and_the_registry_pins(tmp_path, monkeypatch, capsys, texts, decl):
    w = f2_world(tmp_path / "w", monkeypatch)
    argv = cli_argv(w, tmp_path, texts, decl, None, variant=None, out=None) + ["--dry-run"]
    assert f2.main(argv) == 0
    capsys.readouterr()
    (texts / "DECLARE-FORWARD.md").write_text("tampered\n")
    assert f2.main(argv) == 2 and "does not match the pinned" in capsys.readouterr().err
    (texts / "DECLARE-FORWARD.md").write_text("synthetic declare\n")
    monkeypatch.setattr(f2, "REGISTRY_PINS", {"obrec-1019c": "0" * 64})
    assert f2.main(argv) == 4 and "registry check failed" in capsys.readouterr().err
    assert f2.sim_is_lip_f1()


def test_the_dry_run_whitelist_drops_every_field_that_depends_on_the_quote_rule():
    rep = {"mode": "dry_run", "units_by_status": {"opened": 7, "not_opened": 3, "excl_not_daily": 4},
           "structurally_opened_units": 7, "events": 5, "unit_days": 2, "snapshots_in_window": 100,
           "snapshots_quotable_zero_inventory": 50, "carry_forward_gaps": {}, "exclusion_share": {"share": 0.1},
           "median_gap_s_by_source": {"obrec-1019c": 222.0}, "programs_admitted_series": 9, "units_by_source": {"obrec-1019c": 14},
           "excl_no_meta_sub": {}, "inconsistent_snapshots": {}, "meta_captures": 1, "meta_markets": 1, "stores": {}}
    out = f2.dry_report(rep, {"freeze_iso": F2_FREEZE, "declare_sha256": "a" * 64, "texts": {}})
    assert out["units_by_status"] == {"excl_not_daily": 4, "passed_exclusions": 10}
    for k in ("structurally_opened_units", "events", "unit_days", "snapshots_in_window", "snapshots_quotable_zero_inventory",
              "carry_forward_gaps", "exclusion_share", "median_gap_s_by_source", "programs_admitted_series"):
        assert k not in out


# ==================================================================== universe, first-seen rule, coverage
def test_the_universe_is_the_eight_gas_series_and_the_first_seen_rule_uses_the_f2_freeze(tmp_path, monkeypatch, covered, texts, gates):
    gas_c = "KXAAAGASDNY-26OCT19-T4.0"
    mid = st.prog("pMid", gas_c, st.S0, st.E_)
    w = f2_world(tmp_path / "w", monkeypatch,
                 extra_prog=[st.prog("pRT", "KXRT-26OCT19-T3", st.S0, st.E_), mid],
                 extra_snaps={"KXRT-26OCT19-T3": st.ticks("2026-10-19T12:05:30Z", "2026-10-19T15:30:00Z", 60),
                              gas_c: st.ticks("2026-10-19T12:05:30Z", "2026-10-19T15:30:00Z", 60)},
                 late_polls=[("2026-10-19T08:00:00Z", True, [mid])])         # listed at 08:00Z: after LIP-F1's freeze, before F2's
    g1 = sim.run_sim(w.cfg(out=str(tmp_path / "f1")))
    u1 = {u["unit_id"]: u for u in pq.read_table(tmp_path / "f1" / "units.parquet").to_pylist() if u["cell"] in ("", "U0")}
    assert {"pA", "pB", "pMid"} <= {k for k, u in u1.items() if u["status"] == "opened"} and g1["exclusions"] == {"excl_no_meta": 1}
    g2 = f2.run_f2(f2_cfg(w, tmp_path / "f2"), "A", ctx_for(texts, gates))
    u2 = {u["unit_id"]: u for u in pq.read_table(tmp_path / "f2" / "units.parquet").to_pylist() if u["cell"] in ("", "U0")}
    assert {k for k, u in u2.items() if u["status"] == "opened"} == {"pA", "pB"}
    assert u2["pMid"]["status"] == "excl_pre_freeze" and "pRT" not in u2         # pRT is not admitted at all
    assert g2["exclusions"] == {"excl_pre_freeze": 1}                           # and is not even counted
    assert g2["freeze"] == F2_FREEZE


def test_the_coverage_block_keeps_the_obrec_1019c_baseline_and_counts_from_the_f2_freeze(tmp_path, monkeypatch):
    w = f2_world(tmp_path / "w", monkeypatch)
    with f2.f2_cohort(F2_FREEZE, "A"):
        cfg = f2_cfg(w, tmp_path / "o")
        inp = sim.load_inputs(cfg)
        cov = sim.coverage(inp, cfg, st.at(R))
    assert cov["baselines_s"] == {"obrec-1019c": 222.7, "lipsnap": 900.0}
    assert sorted(cov["days"])[0] == "2026-10-19" and "2026-10-25" in cov["days"]
    assert cov["days"]["2026-10-19"]["recorder"] > 0                              # the 1019c rounds of the synthetic world
    assert f2.sim_is_lip_f1()


# ============================================================ outputs: every one relabelled, deterministic, own --out
def run_cli(w, tmp, texts_dir, decl_path, gates, variant, out, capsys, *extra):
    rc = f2.main(cli_argv(w, tmp, texts_dir, decl_path, gates, variant=variant, out=out) + list(extra))
    cap_ = capsys.readouterr()
    return rc, cap_.out, cap_.err


@pytest.mark.parametrize("variant", ["A", "B"])
def test_a_full_cli_run_relabels_every_output_and_writes_only_to_out(tmp_path, monkeypatch, capsys, covered, texts, decl, gates, variant):
    w = f2_world(tmp_path / "w", monkeypatch)
    label = f"F2-{variant}"
    rc, out, err = run_cli(w, tmp_path, texts, decl, gates, variant, "out", capsys, "--read-instant", R)
    assert rc == 0, err
    o = tmp_path / "out"
    assert sorted(f.name for f in o.iterdir()) == sorted(["units.parquet", "fills.parquet", "days.parquet", "gate.json", "table.txt", f2.MARKER])
    gate = json.loads((o / "gate.json").read_text())
    assert gate["book"] == label == gate["lip_f2_variant"] and gate["book_sim_internal"] == "A"
    assert gate["report_only"] is False and gate["gated"] is True and gate["promotable"] is False and gate["deciding"] is True
    assert gate["size"] == 25.0 and gate["i_max"] == {"A": 50.0, "B": 25.0}[variant]
    assert gate["freeze"] == F2_FREEZE == gate["f2_freeze"] and gate["f2_declare_sha256"] == sha(decl)
    assert gate["f2_universe"] == list(sim.GAS_SERIES) and gate["f2_m_gated"] == 3 and gate["verdict"] == "COUNTS_NOT_MET"
    assert gate["f2_rule"] == f2.RULE_TEXT[variant] and gate["f2_tool_md5"] == hashlib.md5(Path(f2.__file__).read_bytes()).hexdigest()
    assert gate["sim_md5"] == SIM_MD5 and gate["f2_sim_md5_pinned"] == SIM_MD5 and gate["f2_sim_tests_md5_matches"] is True
    assert gate["f2_lip_f1_gate"] == {"path": gates["lip"], "sha256": sha(gates["lip"]), "read_instant": "2026-10-24T00:00:00Z"}
    assert gate["f2_bookc_done"] == {"path": gates["bookc"], "sha256": sha(gates["bookc"])}
    assert set(gate["f2_registry_pins"]) == {"obrec-1019c"}
    assert gate["f2_lip_texts_sha256"] == {f.name: sha(f) for f in texts.iterdir()} and gate["f2_qp_review_sha256"].startswith("965a41bc")
    for c in sim.CELLS:
        g4b = gate["cells"][c]["gate"]["G4b"]
        assert "p05" in g4b and "p_adj" in g4b and g4b["adj_pct"] == pytest.approx(5 / 3) and "pass_unadjusted" in g4b
    table = (o / "table.txt").read_text()
    assert out == table                                                        # the CLI prints the file
    first = table.splitlines()[0]
    assert first.startswith(f"LIP-F2 formula screen | {label} {f2.NAMES[variant]} S=25 I_max=") and "verdict=COUNTS_NOT_MET" in first
    assert "LIP-F1 formula screen | Book" not in table
    assert "p05$" in table
    assert "G4 and G4b decide at the 1.6667th percentile" in table and "bound U0:" in table and "bound BQ:" in table
    # the L3 read statement is in the header, with LIP-F1's R, the F2 unit-days and the G4b cluster count
    stmt = gate["f2_statement_read"]
    assert stmt.startswith("Family declared before any F2 number: LIP-F1 Book A (gated, final at R = 2026-10-24T00:00:00Z, "
                           "not changed by this read); Book B and Book C (report-only); F2-A and F2-B (gated screens); "
                           "F1-rule control (declared, not run). m = 3 gated books.")
    assert "the same 8 gas series, unit-days 2026-10-19, G4b clusters = 1." in stmt
    assert stmt.endswith("An F2 pass does not rescue or condition LIP-F1.") and stmt in table
    assert "The deciding G4/G4b bounds are the 1.667th percentile; the 5th is shown." in stmt
    assert "formula screen passed (multiplicity-adjusted)" in stmt
    assert "F1-rule control (report-only)" not in table and not hasattr(f2, "STATEMENT_CONTROL")     # R5: declared, not run
    assert gate["f2_statement_cohort"] in table and "frozen 2026-10-19T11:00:00Z" in gate["f2_statement_cohort"]
    assert "gas days from 10-20" in gate["f2_statement_cohort"] and "No F2 number existed when this read ran." in table
    # the marker, the parquet metadata and the label on every output
    marker = json.loads((o / f2.MARKER).read_text())
    assert marker["lip_f2_variant"] == label and marker["files"] == list(f2.SIM_OUTPUT_FILES) and marker["promotable"] is False
    for f in ("units.parquet", "fills.parquet", "days.parquet"):
        md = pq.read_schema(o / f).metadata
        assert md[b"lip_f2_variant"] == label.encode() and md[b"promotable"] == b"false"
    assert len(pq.read_table(o / "units.parquet")) > 0 and f2.sim_is_lip_f1()
    assert "peak RSS" in err


@pytest.mark.parametrize("variant", ["A", "B"])
def test_f2_runs_are_byte_identical(tmp_path, monkeypatch, covered, texts, gates, variant):
    w = f2_world(tmp_path / "w", monkeypatch)
    for i in (1, 2):
        f2.run_f2(f2_cfg(w, tmp_path / f"o{i}", variant), variant, ctx_for(texts, gates))
    assert md5_dir(tmp_path / "o1") == md5_dir(tmp_path / "o2")


def test_a_scan_is_advisory_starts_at_the_f2_read_window_and_keeps_its_wording(tmp_path, monkeypatch, capsys, covered, texts, decl, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    rc, out, err = run_cli(w, tmp_path, texts, decl, gates, "B", "scan", capsys, "--scan", "--asof", "2026-10-28T00:00:00Z")
    assert rc == 0, err
    gate = json.loads((tmp_path / "scan" / "gate.json").read_text())
    assert gate["verdict"].startswith("SCAN_REPORT_ONLY_") and gate["deciding"] is False
    assert gate["scan"]["candidates"][0] == "2026-10-26T00:00:00Z" and gate["scan"]["candidates"][-1] == "2026-10-28T00:00:00Z"
    assert "multiplicity" not in gate["verdict"]


def test_a_units_evidence_failure_is_labelled_too(tmp_path, monkeypatch, capsys, covered, texts, decl, gates):
    (texts / "DECLARE-FORWARD-VERIFY-1.md").write_text("no url and no verdict word here\n")
    monkeypatch.setattr(f2, "LIP_F1_TEXTS", tuple((k, f, sha(texts / f)) for k, f, _h in f2.LIP_F1_TEXTS))
    w = f2_world(tmp_path / "w", monkeypatch)
    rc, out, err = run_cli(w, tmp_path, texts, decl, gates, "A", "ne", capsys, "--read-instant", R)
    assert rc == 0, err
    gate = json.loads((tmp_path / "ne" / "gate.json").read_text())
    assert gate["verdict"] == "NOT_DECIDABLE" and gate["book"] == "F2-A" and gate["cells"] == {}
    assert out.splitlines()[0].startswith("LIP-F2 formula screen | F2-A SIDE-CAP") and "LIP-F1 formula screen | Book" not in out
    assert "unit-days none, G4b clusters = 0" in gate["f2_statement_read"]


def test_f2_a_changes_the_fills_of_a_lopsided_book_end_to_end(tmp_path, monkeypatch, covered, texts, gates):
    lop = dict(yes=[(0.72, 1000)], no=[(0.27, 1000)])
    w = f2_world(tmp_path / "w", monkeypatch, extra_snaps={st.GAS_A: st.ticks("2026-10-19T12:05:30Z", "2026-10-19T15:30:00Z", 60, book=lop)})
    sim.run_sim(w.cfg(out=str(tmp_path / "f1")))
    f2.run_f2(f2_cfg(w, tmp_path / "a", "A"), "A", ctx_for(texts, gates))
    yes_fills = lambda d: next(u["fills_YES"] for u in pq.read_table(tmp_path / d / "units.parquet").to_pylist()
                               if u["unit_id"] == "pA" and u["cell"] == "U0")
    rew = lambda d: next(u["reward_central"] for u in pq.read_table(tmp_path / d / "units.parquet").to_pylist()
                         if u["unit_id"] == "pA" and u["cell"] == "U0")
    assert yes_fills("f1") == 25.0 and yes_fills("a") == 0.0                       # the 0.72 YES bid is not posted
    assert 0 < rew("a") < rew("f1")                                               # the reward comes from the NO side only


def test_r4_the_tool_refuses_to_run_unless_the_declaration_pins_its_own_md5(tmp_path, monkeypatch, capsys, texts, decl):
    w = f2_world(tmp_path / "w", monkeypatch)
    monkeypatch.setattr(sim, "run_sim", lambda cfg: pytest.fail("the simulator ran"))
    other = tmp_path / "decl" / "DECLARE-F2-other-tool.md"
    other.write_text(decl_text(md5="0" * 32))
    for flags in (["--dry-run"],):
        argv = cli_argv(w, tmp_path, texts, other, None, variant=None, out=None) + flags
        assert f2.main(argv) == 2 and "pins F2 TOOL MD5" in capsys.readouterr().err
    # the same tool file, edited: the md5 the declaration froze no longer matches
    assert f2.load_f2_declaration(str(decl), sha(decl))[1] == F2_FREEZE
    monkeypatch.setattr(f2, "__file__", str(tmp_path / "edited_tool.py"))
    (tmp_path / "edited_tool.py").write_text(Path(f2.sim.__file__).read_text()[:100])
    with pytest.raises(f2.F2Refusal, match="pins F2 TOOL MD5"):
        f2.load_f2_declaration(str(decl), sha(decl))


# ===================================== reviewer: the simulator writes to staging; --out only ever holds F2-labelled files
def _labels_in(d):
    """(file name, 'LIP-F1 formula screen | Book' present) for every file under d."""
    out = []
    for f in sorted(Path(d).rglob("*")):
        if f.is_file():
            out.append((f.relative_to(d).as_posix(), b"LIP-F1 formula screen | Book" in f.read_bytes()))
    return out


def test_the_simulator_writes_to_staging_and_out_only_ever_holds_relabelled_files(tmp_path, monkeypatch, covered, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    out = tmp_path / "out"
    real_table, seen = f2.f2_table, {}

    def spy_table(gate):
        seen["out_at_relabel"] = sorted(p.name for p in out.iterdir())
        seen["staged"] = sorted(p.name for p in (out / f2.STAGING).iterdir())
        seen["sim_label_in_staging"] = "LIP-F1 formula screen | Book A" in (out / f2.STAGING / "table.txt").read_text()
        return real_table(gate)
    monkeypatch.setattr(f2, "f2_table", spy_table)
    f2.run_f2(f2_cfg(w, out), "A", ctx_for(texts, gates))
    # at the moment of the relabel --out held the marker and the staging directory only
    assert seen["out_at_relabel"] == sorted([f2.MARKER, f2.STAGING])
    assert seen["sim_label_in_staging"] is True                                   # the simulator's own labels live in staging
    assert "units.parquet" in seen["staged"] and "gate.json" in seen["staged"]
    # afterwards: no staging directory, the five files and the marker, none with the simulator's header
    assert sorted(p.name for p in out.iterdir()) == sorted(["units.parquet", "fills.parquet", "days.parquet", "gate.json", "table.txt", f2.MARKER])
    assert not any(flag for _n, flag in _labels_in(out)) and json.loads((out / "gate.json").read_text())["book"] == "F2-A"


@pytest.mark.parametrize("where", ["table", "parquet", "verdict"])
def test_a_crash_while_relabelling_leaves_no_simulator_labelled_file_in_out(tmp_path, monkeypatch, covered, texts, gates, where):
    w = f2_world(tmp_path / "w", monkeypatch)
    boom = lambda *a, **k: (_ for _ in ()).throw(f2.F2Refusal("synthetic crash in the relabel"))
    # a good earlier run of the same variant, to show that a later crash leaves it untouched
    f2.run_f2(f2_cfg(w, tmp_path / "again"), "A", ctx_for(texts, gates))
    before = md5_dir(tmp_path / "again")
    target = {"table": "f2_table", "parquet": "_stamp_parquet", "verdict": "final_verdict"}[where]
    monkeypatch.setattr(f2, target, boom)
    with pytest.raises(f2.F2Refusal, match="synthetic crash"):
        f2.run_f2(f2_cfg(w, tmp_path / "fresh"), "A", ctx_for(texts, gates))
    assert not (tmp_path / "fresh").exists()                                     # nothing left at all
    (tmp_path / "pre").mkdir()
    with pytest.raises(f2.F2Refusal, match="synthetic crash"):
        f2.run_f2(f2_cfg(w, tmp_path / "pre"), "A", ctx_for(texts, gates))
    assert list((tmp_path / "pre").iterdir()) == [] and f2.sim_is_lip_f1()
    with pytest.raises(f2.F2Refusal, match="synthetic crash"):
        f2.run_f2(f2_cfg(w, tmp_path / "again"), "A", ctx_for(texts, gates))
    assert md5_dir(tmp_path / "again") == before and not any(f for _n, f in _labels_in(tmp_path / "again"))


def test_a_stale_staging_directory_from_a_killed_run_is_cleared_and_never_read(tmp_path, monkeypatch, covered, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    stale = tmp_path / "out" / f2.STAGING
    stale.mkdir(parents=True)
    (stale / "gate.json").write_text(json.dumps({"book": "A", "verdict": "FORMULA_SCREEN_PASSED"}))
    (stale / "junk.txt").write_text("x")
    f2.run_f2(f2_cfg(w, tmp_path / "out", "B"), "B", ctx_for(texts, gates))
    assert not stale.exists()
    assert json.loads((tmp_path / "out" / "gate.json").read_text())["verdict"] == "COUNTS_NOT_MET"
    assert not any(flag for _n, flag in _labels_in(tmp_path / "out"))


def test_the_cfg_out_is_restored_after_a_run_and_after_a_simulator_error(tmp_path, monkeypatch, covered, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    cfg = f2_cfg(w, tmp_path / "out")
    f2.run_f2(cfg, "A", ctx_for(texts, gates))
    assert cfg.out == str(tmp_path / "out")
    cfg2 = f2_cfg(w, tmp_path / "out2")
    monkeypatch.setattr(sim, "run_sim", lambda c: (_ for _ in ()).throw(sim.InputError("synthetic")))
    with pytest.raises(sim.InputError):
        f2.run_f2(cfg2, "A", ctx_for(texts, gates))
    assert cfg2.out == str(tmp_path / "out2") and not (tmp_path / "out2").exists()


# ===================================== R10: one new --out per variant per candidate R (and per --scan)
def test_r10_a_later_candidate_r_cannot_overwrite_an_earlier_candidates_gate_json(tmp_path, monkeypatch, covered, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    ctx = ctx_for(texts, gates)
    out = tmp_path / "out"
    r1, r2 = "2026-10-26T00:00:00Z", "2026-10-27T00:00:00Z"
    g1 = f2.run_f2(f2_cfg(w, out, read_us=st.at(r1)), "A", ctx)
    assert g1["f2_run"] == {"mode": "read_instant", "read_instant": r1, "asof": None}
    kept = md5_dir(out)
    assert json.loads((out / "gate.json").read_text())["f2_run"]["read_instant"] == r1
    # the 10-27 candidate into the same folder is refused, and the 10-26 record is untouched
    with pytest.raises(f2.F2Refusal, match="another candidate"):
        f2.run_f2(f2_cfg(w, out, read_us=st.at(r2)), "A", ctx)
    assert md5_dir(out) == kept and not (out / f2.STAGING).exists()
    # a new folder per candidate is fine, and the 10-26 candidate may be re-run into its own folder (same R, same mode)
    f2.run_f2(f2_cfg(w, tmp_path / "out2", read_us=st.at(r2)), "A", ctx)
    f2.run_f2(f2_cfg(w, out, read_us=st.at(r1)), "A", ctx)
    assert md5_dir(out) == kept
    # the other variant never shares a folder (existing rule), at any R
    with pytest.raises(f2.F2Refusal, match="another F2 variant"):
        f2.run_f2(f2_cfg(w, out, "B", read_us=st.at(r1)), "B", ctx)


def test_r10_a_scan_and_a_read_instant_run_never_share_a_folder_and_a_scan_keeps_its_asof(tmp_path, monkeypatch, covered, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    ctx = ctx_for(texts, gates)
    a28, a29 = st.at("2026-10-28T00:00:00Z"), st.at("2026-10-29T00:00:00Z")
    f2.run_f2(f2_cfg(w, tmp_path / "scan", "B", read_us=None, scan=True, asof_us=a28), "B", ctx)
    kept = md5_dir(tmp_path / "scan")
    for cfg in (f2_cfg(w, tmp_path / "scan", "B", read_us=st.at("2026-10-26T00:00:00Z")),          # a read-instant run
                f2_cfg(w, tmp_path / "scan", "B", read_us=None, scan=True, asof_us=a29)):         # another scan horizon
        with pytest.raises(f2.F2Refusal, match="another candidate"):
            f2.run_f2(cfg, "B", ctx)
    f2.run_f2(f2_cfg(w, tmp_path / "scan", "B", read_us=None, scan=True, asof_us=a28), "B", ctx)    # the same scan again
    assert md5_dir(tmp_path / "scan") == kept
    f2.run_f2(f2_cfg(w, tmp_path / "ri", "B", read_us=st.at("2026-10-26T00:00:00Z")), "B", ctx)
    with pytest.raises(f2.F2Refusal, match="another candidate"):
        f2.run_f2(f2_cfg(w, tmp_path / "ri", "B", read_us=None, scan=True, asof_us=a28), "B", ctx)


def test_r10_a_gate_json_without_a_candidate_identity_is_refused_and_check_out_reports_both_identities(tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    (out / f2.MARKER).write_text(json.dumps({"lip_f2_variant": "F2-A"}))
    (out / "gate.json").write_text(json.dumps({"lip_f2_variant": "F2-A", "book": "F2-A"}))
    (out / "units.parquet").write_text("x")
    run = {"mode": "read_instant", "read_instant": "2026-10-26T00:00:00Z", "asof": None}
    f2.check_out(out, "A")                                                         # no identity given: the file-level checks only
    with pytest.raises(f2.F2Refusal, match=r"another candidate \(None\)"):
        f2.check_out(out, "A", run=run)
    (out / "gate.json").write_text(json.dumps({"lip_f2_variant": "F2-A", "f2_run": run}))
    f2.check_out(out, "A", run=run)
    with pytest.raises(f2.F2Refusal, match="never reused for another R"):
        f2.check_out(out, "A", run=dict(run, read_instant="2026-10-27T00:00:00Z"))
    (out / "gate.json").write_text("{not json")
    with pytest.raises(f2.F2Refusal):
        f2.check_out(out, "A", run=run)


def test_r10_a_failed_rerun_restores_the_marker_it_rewrote(tmp_path, monkeypatch, covered, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    ctx = ctx_for(texts, gates)
    f2.run_f2(f2_cfg(w, tmp_path / "out"), "A", ctx)
    kept = md5_dir(tmp_path / "out")
    marker = (tmp_path / "out" / f2.MARKER).read_bytes()
    monkeypatch.setattr(f2, "f2_table", lambda g: (_ for _ in ()).throw(f2.F2Refusal("synthetic crash")))
    real_labels = f2.labels
    monkeypatch.setattr(f2, "labels", lambda v, c: dict(real_labels(v, c), f2_notes=["changed so the marker differs"]))
    with pytest.raises(f2.F2Refusal, match="synthetic crash"):
        f2.run_f2(f2_cfg(w, tmp_path / "out"), "A", ctx)
    assert (tmp_path / "out" / f2.MARKER).read_bytes() == marker and md5_dir(tmp_path / "out") == kept


# ============================== reviewer nits: run_f2 re-reads the declaration (R4 line), "eight" texts
def test_run_f2_itself_refuses_without_the_declaration_its_pin_its_freeze_and_its_tool_md5(tmp_path, monkeypatch, covered, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    good = ctx_for(texts, gates)
    monkeypatch.setattr(sim, "run_sim", lambda cfg: pytest.fail("the simulator ran"))
    bad = {"no declaration path": {k: v for k, v in good.items() if k != "declare_path"},
           "a wrong declaration pin": dict(good, declare_sha256="0" * 64),
           "another tool's md5": ctx_for(texts, gates, md5="0" * 32),
           "another freeze": dict(good, freeze_iso="2026-10-19T12:00:00Z")}
    for name, ctx in bad.items():
        with pytest.raises(f2.F2Refusal):
            f2.run_f2(f2_cfg(w, tmp_path / "out"), "A", ctx)
        dry = f2.make_cfg(None, freeze_us=st.at(F2_FREEZE), books=w.books, programs=w.lip, dry_run=True)
        with pytest.raises(f2.F2Refusal):
            f2.run_f2(dry, None, ctx)
        assert not (tmp_path / "out").exists(), name
    with pytest.raises(f2.F2Refusal, match="pins F2 TOOL MD5"):
        f2.run_f2(f2_cfg(w, tmp_path / "out"), "A", bad["another tool's md5"])


def test_the_help_and_docstring_say_eight_texts():
    helptext = " ".join(f2.build_parser().format_help().split())
    assert "eight inherited" in helptext and "six inherited" not in helptext
    assert "eight inherited" in f2.verify_lip_texts.__doc__ and len(f2.LIP_F1_TEXTS) == 8


# ======================== reviewer finding: gate.json moves first; the marker carries the identity if gate.json is missing
def test_gate_json_is_moved_first_and_a_kill_after_the_first_rename_cannot_let_another_r_in(tmp_path, monkeypatch, covered, texts, gates):
    import types
    w = f2_world(tmp_path / "w", monkeypatch)
    ctx = ctx_for(texts, gates)
    out = tmp_path / "out"
    r1, r2 = "2026-10-26T00:00:00Z", "2026-10-27T00:00:00Z"
    order, real_replace = [], f2.os.replace

    def kill_after_first(src, dst):
        order.append(Path(dst).name)
        if len(order) == 1:
            real_replace(src, dst)
            return
        raise OSError("synthetic kill between the renames")
    with monkeypatch.context() as mp:
        mp.setattr(f2, "os", types.SimpleNamespace(replace=kill_after_first))
        with pytest.raises(OSError, match="synthetic kill"):
            f2.run_f2(f2_cfg(w, out, read_us=st.at(r1)), "A", ctx)
    assert order[0] == "gate.json"                                              # gate.json is renamed first
    left = sorted(p.name for p in out.iterdir())
    assert "gate.json" in left and "units.parquet" not in left and f2.MARKER in left   # killed after the first rename
    gate = json.loads((out / "gate.json").read_text())
    assert gate["book"] == "F2-A" and gate["f2_run"]["read_instant"] == r1       # already relabelled and identified
    # another R is refused; the same R may finish the job
    with pytest.raises(f2.F2Refusal, match="another candidate"):
        f2.run_f2(f2_cfg(w, out, read_us=st.at(r2)), "A", ctx)
    f2.run_f2(f2_cfg(w, out, read_us=st.at(r1)), "A", ctx)
    assert "units.parquet" in {p.name for p in out.iterdir()}


def test_without_a_gate_json_the_markers_identity_keeps_another_r_out(tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    run = {"mode": "read_instant", "read_instant": "2026-10-26T00:00:00Z", "asof": None}
    (out / f2.MARKER).write_text(json.dumps({"lip_f2_variant": "F2-A", "f2_run": run}))
    (out / "units.parquet").write_text("x")                                      # a staged file already moved, gate.json missing
    f2.check_out(out, "A", run=run)
    with pytest.raises(f2.F2Refusal, match=r"already holds the LIP-F2-VARIANT.json of another candidate"):
        f2.check_out(out, "A", run=dict(run, read_instant="2026-10-27T00:00:00Z"))
    with pytest.raises(f2.F2Refusal, match="another candidate"):
        f2.check_out(out, "A", run=dict(run, mode="scan", read_instant=None, asof="2026-10-28T00:00:00Z"))
    (out / "units.parquet").unlink()                                             # a marker-only folder (killed during the sim)
    with pytest.raises(f2.F2Refusal, match="another candidate"):
        f2.check_out(out, "A", run=dict(run, read_instant="2026-10-27T00:00:00Z"))
    f2.check_out(out, "A", run=run)
    (out / f2.MARKER).write_text(json.dumps({"lip_f2_variant": "F2-A"}))          # a marker with no identity: refuse
    with pytest.raises(f2.F2Refusal, match="another candidate"):
        f2.check_out(out, "A", run=run)


# ======================== optional: a same-R re-run whose new gate.json would differ from the existing one is refused
def test_a_same_r_rerun_that_would_change_the_existing_gate_json_is_refused_and_leaves_the_record_untouched(
        tmp_path, monkeypatch, covered, texts, gates):
    w = f2_world(tmp_path / "w", monkeypatch)
    ctx = ctx_for(texts, gates)
    out = tmp_path / "out"
    f2.run_f2(f2_cfg(w, out), "A", ctx)
    kept = md5_dir(out)
    f2.run_f2(f2_cfg(w, out), "A", ctx)                                         # an identical re-run is allowed ...
    assert md5_dir(out) == kept
    # ... a re-run whose inputs differ (here: another Book C file, so another sha256 in gate.json) is refused
    other = bookc_marker(tmp_path, json.dumps(dict(BOOKC_GATE, note="a different file, same shape")), name="gate-2.json")
    with pytest.raises(f2.F2Refusal, match="would change the existing"):
        f2.run_f2(f2_cfg(w, out), "A", ctx_for(texts, dict(gates, bookc=str(other))))
    assert md5_dir(out) == kept and not (out / f2.STAGING).exists()
    # a re-run on changed data is refused the same way: add a trade the first run did not see
    w.add_trades([("tZ", st.GAS_A, "2026-10-19T12:40:00Z", 0.48, 25, "no")], [st.GAS_A], name="trades-late",
                 since="2026-10-19T03:33:16Z", until="2026-10-20T00:00:00Z", run_at="2026-10-20T04:41:00Z")
    with pytest.raises(f2.F2Refusal, match="would change the existing"):
        f2.run_f2(f2_cfg(w, out), "A", ctx)
    assert md5_dir(out) == kept
