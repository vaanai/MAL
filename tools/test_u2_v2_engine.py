"""U2 v2 engine fix (QP-RULING-1118-u2-v2, sha256 d79c319d...): the runner keys `engine_warmup_h` and `engine_prune` of scripts/research/c1nf_forward_sim.py.

  * a pool quiet 5 h 30 min before a mark is priced under v2 and refused no_prints under v1 (the prune is the cause);
  * a pick at the first mark S2 is priced under v2, with no clock_not_ready, and the warm-up engine hours write no ledger row, buy, sell or placebo draw;
  * the U2 decider's decisions, counts and marks are md5-equal v1 vs v2 on the same fixture;
  * keys absent: decision md5, per-hour ledger md5 and ledger rows of the U4, U2-v1 and C1-NF paths equal the GOLDEN values produced by the pre-change runner (origin/main 77f62e32);
  * the manifest refuses unknown / invalid values; memory accounting (engine pools, RSS, engine counters) is written only when a key is present.

Synthetic fixtures only; no real tape, ledger, fill or P&L is opened. Nothing here says any book makes money."""
import collections
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "research"))
import c1nf_forward_sim as S  # noqa: E402
import u4_u2_deciders as D  # noqa: E402
from tools import test_c1nf_forward_sim as T1  # noqa: E402
from tools import test_u4_u2_forward_sim as T2  # noqa: E402

T0, START_MS, H = T2.T0, T2.START_MS, T2.H
EMPTY_MD5 = hashlib.md5(b"").hexdigest()
RULING_SHA256_PREFIX = "d79c319d"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(S, "MIN_WARMUP_H", 1)
    monkeypatch.setattr(S, "WARMUP_MIN_BY_DECIDER", {})
    monkeypatch.setattr(S, "code_sha256", lambda extra=(): "c" * 64)  # the code hash is in every ledger row (code_head, frozen_sha): pinned, so per-hour ledger md5s compare across the code change
    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


def mf(tmp, decider, **kw):
    return T2.mf(tmp, decider, **kw)


# ---------------------------------------------------------------------------------------------------------------------------------- keys absent: md5 equivalence with the pre-change runner
def digest(out_root, run_ledger_rows):
    """what 'unchanged' means: per-hour decision + ledger md5, the counts, the marks, decisions.md5, book counts, and the ledger rows minus the code-version fields"""
    out = Path(out_root)
    hours = [json.loads(ln) for ln in (out / "md5-by-hour.jsonl").read_text().splitlines()]
    counts = [json.loads(ln) for ln in (out / "counts.jsonl").read_text().splitlines()]
    marks = [json.loads(ln) for ln in (out / "marks.jsonl").read_text().splitlines()] if (out / "marks.jsonl").exists() else []
    rows = [{k: v for k, v in r.items() if k not in ("code_head", "engine_id", "frozen_sha")} for r in run_ledger_rows if r["kind"] != "start"]
    st = json.loads((out / "status.json").read_text())
    return {
        "hour_decision_md5s": hashlib.md5("".join(h["decision_md5"] for h in hours).encode()).hexdigest(),
        "hour_ledger_md5s": hashlib.md5("".join(h["ledger_md5"] for h in hours).encode()).hexdigest(),
        "counts": hashlib.md5(S.canon(counts).encode()).hexdigest(),
        "marks": hashlib.md5(S.canon(marks).encode()).hexdigest(),
        "decisions": (out / "decisions.md5").read_text().strip(),
        "book_counts": hashlib.md5(S.canon(st["book_counts"]).encode()).hexdigest(),
        "ledger_rows": hashlib.md5(S.canon(sorted(rows, key=lambda r: (r.get("trade_id") or "", r["kind"]))).encode()).hexdigest(),
        "n_hours": len(hours), "n_ledger_rows": len(rows),
    }


def run_long_book(tmp, decider, seed, **mkw):
    """the runner path of test_u4_u2_forward_sim (96 warm-up hours, 48 forward hours of the long synthetic stream) with the default (bounded) decider"""
    rows = T2.long_rows(days_before=4, days_after=2, per_hour=5, seed=seed)
    by_hour = collections.defaultdict(list)
    for r in rows:
        by_hour[(r["block_time"] - T0) // 3600].append(r)
    keep = tmp / "keep"
    keep.mkdir(parents=True)
    for h in range(-96, 49):
        hr = sorted(by_hour.get(h, []), key=lambda r: (r["block_time"], r["slot"]))
        (keep / ("tape-%s.jsonl" % S.hour_key(START_MS + h * S.HOUR_MS))).write_text("".join(json.dumps(r) + "\n" for r in hr))
    m = mf(tmp, decider, warmup_hours=96, exit_s=14_400 if decider == "u4" else 43_200, **mkw)
    dec = (D.U4Decider if decider == "u4" else D.U2Decider)(START_MS, hr_pda_fn=T2.hr_pda)
    assert S.follow(m, fixture=True, once=True, _roots=T1.roots(tmp), decider_factory=lambda mm: dec, ledger_builder=lambda *a: None, engine_kw=T1.SPS) == 0
    return m, T1.ledger_rows(m["out_root"])


# produced by the PRE-CHANGE runner (origin/main 77f62e32, scripts/research/c1nf_forward_sim.py unchanged) on exactly these fixtures
GOLD_LONG = {
    "u4": {
        "book_counts": "d582282a6249ebf9159f0d9b7e5060a9",
        "counts": "f08da550d8e22d23718b74d509516229",
        "decisions": "0b6078cbb64779c0747f3b9c3cc75d6b",
        "hour_decision_md5s": "8e4207f33490fd450fce61766a39fd2e",
        "hour_ledger_md5s": "489b516bfadf745b28ea48503662f0d0",
        "ledger_rows": "b9131f9b3151c102ea69701f82eb153e",
        "marks": "d751713988987e9331980363e24189ce",
        "n_hours": 144,
        "n_ledger_rows": 75
    },
    "u2": {
        "book_counts": "87ad12f7a1799488c34e944d035f299b",
        "counts": "1ae9ed262af534b2c5676e6a55baae8d",
        "decisions": "96d59dc8eeec9ea57c2a9982f39f11f2",
        "hour_decision_md5s": "8d1b999bac78bcbfedec75b2b365c213",
        "hour_ledger_md5s": "7f9bf82e5cb9389c5517799d07e1daef",
        "ledger_rows": "a17c77ba58b3ef0ecd10000fe6b37c2f",
        "marks": "d097efd7f46b1c4b62026e5061329959",
        "n_hours": 144,
        "n_ledger_rows": 116
    },
}


@pytest.mark.parametrize("decider,seed", [("u4", 3), ("u2", 5)])
def test_keys_absent_u4_and_u2_v1_equal_the_pre_change_runner(tmp_path, decider, seed):
    m, rows = run_long_book(tmp_path, decider, seed)
    d = digest(m["out_root"], rows)
    print("GOLD", decider, json.dumps(d, sort_keys=True))
    assert d["n_hours"] == 144 and d["n_ledger_rows"] > 0
    assert d == GOLD_LONG[decider]
    assert not (Path(m["out_root"]) / "engine-mem.jsonl").exists()   # memory accounting is only for the books that carry a key
    assert "engine_pools" not in json.loads((Path(m["out_root"]) / "status.json").read_text())


def test_keys_absent_c1nf_equals_the_pre_change_runner(tmp_path):
    T1.write_keep(tmp_path, 3)
    m = T1.manifest(tmp_path)
    assert T1.run(tmp_path, m, T1.StubDecider()) == 0
    d = digest(m["out_root"], T1.ledger_rows(m["out_root"]))
    print("GOLD c1nf", json.dumps(d, sort_keys=True))
    assert d == GOLD_C1NF
    assert not (Path(m["out_root"]) / "engine-mem.jsonl").exists()


GOLD_C1NF = {
    "book_counts": "2d339d02c4bab1297d5c550322d1876e",
    "counts": "38c144556ce6914723acccd1c10342ad",
    "decisions": "c7174b4aa2cface8569618b48862954d",
    "hour_decision_md5s": "ab7120fcb9a85c1f28873c25c5a5740d",
    "hour_ledger_md5s": "d1076263fc224bb64e3b5b9a1436f8c3",
    "ledger_rows": "6e729f60a463d944bae1b7c4f855cc75",
    "marks": "d751713988987e9331980363e24189ce",
    "n_hours": 3,
    "n_ledger_rows": 5
}


# ---------------------------------------------------------------------------------------------------------------------------------- the U2 v2 fixture
# Two cohorts of the T2 shape (11 pools each: buckets of 6 and 5, ages 30 h and 50 h, the residual leaders are the first pool of bucket 1, then buckets 0 and 1):
#   cohort "P" is decided at the first mark S (T0): every pool's last print is at S - 50 min;
#   cohort "Q" is decided at the second mark S + 6 h, and its leader Q10 is quiet: its last print is at S + 30 min, i.e. 5 h 30 min before the mark (about 49,500 slots, more than HIST_KEEP_SLOTS = 20,000).
# A background pool prints every 20 s from S - 56 h to S + 8 h (it has no pool_create row, so the decider never sees it): a ready slot clock for the engine (8 points in its 300 s window). The engine here runs WITHOUT the sps_fn seam.
QUIET_POOL, QUIET_LAST_S = "Q10", 30 * 60
W_H, FWD_H = 56, 8


def cohort(prefix, mark_s, quiet=None):
    rows = []
    for bi, rs in enumerate(T2.COHORT):
        for k, r in enumerate(rs):
            pool, mint = "%s%d%d" % (prefix, bi, k), "M%s%d%d" % (prefix, bi, k)
            ct = mark_s - (30, 50)[bi] * H   # both create rows lie inside W
            last = QUIET_LAST_S if pool == quiet else mark_s - 3000
            rows += [T2.create(mint, ct - 100), T2.pool_create(pool, mint, ct), T2.u2_print(pool, mint, mark_s - 24 * H - 100, 1.0), T2.u2_print(pool, mint, last, 1.0 + r)]
    return rows


def fixture_rows():
    rows = cohort("P", 0) + cohort("Q", 6 * H, quiet=QUIET_POOL)
    rows += [T2.trade("BGM", "BG", t, "bg", "buy", 0, so=0) for t in range(-W_H * H, FWD_H * H, 20)]
    return rows


def write_keep(tmp, rows):
    keep = tmp / "keep"
    keep.mkdir(parents=True)
    by_hour = collections.defaultdict(list)
    for r in rows:
        by_hour[(r["block_time"] - T0) // 3600].append(r)
    for h in range(-W_H, FWD_H + 1):
        hr = sorted(by_hour.get(h, []), key=lambda r: (r["block_time"], r["slot"]))
        (keep / ("tape-%s.jsonl" % S.hour_key(START_MS + h * S.HOUR_MS))).write_text("".join(json.dumps(r) + "\n" for r in hr))


def run_u2(tmp, max_hours=None, book_id="t-book", **mkw):
    """-> (manifest, ledger rows of the book, out root); a fresh tape dir per call. engine_kw is empty: the engine estimates its own slot clock from the rows (no sps seam)."""
    write_keep(tmp, fixture_rows())
    m = mf(tmp, "u2", book_id=book_id, warmup_hours=W_H, exit_s=43_200, **mkw)
    dec = D.U2Decider(START_MS, hr_pda_fn=T2.hr_pda)
    assert S.follow(m, fixture=True, once=True, max_hours=max_hours, _roots=T1.roots(tmp), decider_factory=lambda mm: dec, ledger_builder=lambda *a: None, engine_kw={}) == 0
    return m, T1.ledger_rows(m["out_root"]), Path(m["out_root"])


def main_rows(rows, kinds):
    return [r for r in rows if r["kind"] in kinds and not r["bot_id"].endswith("-plc")]


def pools_of(rows, kind):
    return sorted({r["pool"] for r in main_rows(rows, (kind,))})


V2 = dict(engine_warmup_h=7, engine_prune=False)


def test_a_pool_quiet_5h30_is_priced_under_v2_and_refused_no_prints_under_v1(tmp_path):
    _, rows1, out1 = run_u2(tmp_path / "v1")
    _, rows2, out2 = run_u2(tmp_path / "v2", **V2)
    # the fixture's picks at the second mark: Q10, Q00, Q11 (residual leaders)
    assert QUIET_POOL in pools_of(rows1, "refused") and [r["reason"] for r in main_rows(rows1, ("refused",)) if r["pool"] == QUIET_POOL] == ["no_prints"]   # v1: the engine pruned it
    assert QUIET_POOL not in pools_of(rows1, "open")
    assert QUIET_POOL in pools_of(rows2, "open") and QUIET_POOL not in pools_of(rows2, "refused")                                                          # v2: priced
    assert {"Q00", "Q11"} <= set(pools_of(rows1, "open"))     # the non-quiet picks of the same mark are priced by both: the quiet pool is the difference
    assert {"Q00", "Q11"} <= set(pools_of(rows2, "open"))
    assert pools_of(rows2, "refused") == []                    # v2: not one refusal at either mark
    b2 = json.loads((out2 / "status.json").read_text())["book_counts"]
    assert not [k for k in b2 if k.startswith("buy_refused")] and b2["buys"] == 6 and b2["buys_placebo"] == 6


def test_engine_prune_false_alone_prices_the_quiet_pool_and_the_warmup_key_alone_does_not(tmp_path):
    _, rows_p, _ = run_u2(tmp_path / "p", engine_prune=False)
    assert QUIET_POOL in pools_of(rows_p, "open")                                          # the prune was the cause of the second-mark refusal
    assert set(pools_of(rows_p, "refused")) == {"P00", "P10", "P11"}                       # without the warm-up feed the first mark's picks are still refused (the engine starts empty at S)
    _, rows_w, _ = run_u2(tmp_path / "w", engine_warmup_h=7)
    assert QUIET_POOL in pools_of(rows_w, "refused") and not {"P00", "P10", "P11"} & set(pools_of(rows_w, "refused"))


def test_the_pick_at_the_first_mark_is_priced_with_no_clock_not_ready_and_v1_refuses_it(tmp_path):
    _, rows1, out1 = run_u2(tmp_path / "v1")
    _, rows2, out2 = run_u2(tmp_path / "v2", **V2)
    first = {"P00", "P10", "P11"}
    assert first <= set(pools_of(rows2, "open")) and not first & set(pools_of(rows2, "refused"))
    assert not any(r.get("reason") == "clock_not_ready" for r in rows2)
    assert first <= set(pools_of(rows1, "refused")) and set(r["reason"] for r in main_rows(rows1, ("refused",)) if r["pool"] in first) <= {"no_prints", "clock_not_ready"}
    opens = [r for r in main_rows(rows2, ("open",)) if r["pool"] in first]
    assert opens and all(r["decision_ms"]["entry"] >= START_MS for r in opens)
    assert json.loads((out2 / "status.json").read_text())["engine_counters"].get("refused_clock_not_ready", 0) == 0


def test_the_engine_warmup_hours_write_no_ledger_row_buy_sell_or_draw(tmp_path):
    _, rows, out = run_u2(tmp_path, max_hours=W_H, **V2)       # only the W hours
    hours = [json.loads(ln) for ln in (out / "md5-by-hour.jsonl").read_text().splitlines()]
    assert len(hours) == W_H and all(h["warmup"] and h["ledger_md5"] == EMPTY_MD5 for h in hours)   # the ledger tree md5 after the E hours equals the empty md5
    assert [r for r in rows if r["kind"] != "start"] == []
    counts = [json.loads(ln) for ln in (out / "counts.jsonl").read_text().splitlines()]
    assert all(c["entries"] == 0 and c["book"] == {} and not c["decider"].get("placebo_entries") and not c["decider"].get("picks") for c in counts)
    assert json.loads((out / "status.json").read_text())["book_counts"] == {}
    mem = [json.loads(ln) for ln in (out / "engine-mem.jsonl").read_text().splitlines()]
    assert [x["engine_pools"] for x in mem[: W_H - 7]] == [0] * (W_H - 7) and all(x["engine_pools"] > 0 for x in mem[W_H - 7:])   # the engine sees exactly E = [S - 7 h, S)
    assert not (out / "marks.jsonl").exists() or all(json.loads(ln)["mark_s"] >= T0 for ln in (out / "marks.jsonl").read_text().splitlines())


def test_the_u2_deciders_own_decisions_are_md5_equal_v1_vs_v2(tmp_path):
    res = {}
    for name, kw in (("v1", {}), ("v2", V2)):
        _, _, out = run_u2(tmp_path / name, **kw)
        hours = [json.loads(ln) for ln in (out / "md5-by-hour.jsonl").read_text().splitlines()]
        counts = [json.loads(ln) for ln in (out / "counts.jsonl").read_text().splitlines()]
        marks = [json.loads(ln) for ln in (out / "marks.jsonl").read_text().splitlines()]
        res[name] = ([h["decision_md5"] for h in hours], [(c["hour"], c["entries"], c["decider"]) for c in counts], marks)
    assert res["v1"] == res["v2"] and len(res["v1"][0]) == W_H + FWD_H
    assert sum(e for _, e, _ in res["v1"][1]) == 12 and [m["picks"] for m in res["v1"][2] if m["picks"]] == [3, 3]    # 3 picks and 3 placebo draws at each of the two marks


def test_engine_mem_accounting_is_written_only_for_a_book_with_a_key_and_the_pool_table_does_not_shrink_without_prune(tmp_path):
    _, _, out2 = run_u2(tmp_path / "v2", **V2)
    mem = [json.loads(ln) for ln in (out2 / "engine-mem.jsonl").read_text().splitlines()]
    assert len(mem) == W_H + FWD_H and all(x["rss_kb"] and x["peak_rss_kb"] >= x["rss_kb"] and isinstance(x["engine_counters"], dict) for x in mem)
    pools = [x["engine_pools"] for x in mem]
    assert pools == sorted(pools) and pools[-1] >= 23                            # 22 cohort pools + the background pool, never dropped
    ec = mem[-1]["engine_counters"]
    assert ec.get("rows_below_trim_floor", 0) == 0 and ec.get("refused_history_trimmed", 0) == 0 and ec.get("refused_no_prints", 0) == 0
    assert json.loads((out2 / "status.json").read_text())["engine_pools"] == pools[-1]
    _, _, out1 = run_u2(tmp_path / "v1")
    assert not (out1 / "engine-mem.jsonl").exists() and "engine_pools" not in json.loads((out1 / "status.json").read_text())


# ---------------------------------------------------------------------------------------------------------------------------------- manifest: refusals and the book id
@pytest.mark.parametrize("kw", [{"engine_warmup_h": v} for v in (0, -1, 25, 7.5, True, False, "7", None, [7])] + [{"engine_prune": v} for v in ("false", 0, 1, None, "no")])
def test_invalid_values_of_the_two_keys_are_refused(tmp_path, kw):
    with pytest.raises(S.Refused):
        S.check_manifest(mf(tmp_path, "u2", exit_s=43_200, **kw))


def test_valid_values_pass_and_unset_keys_stay_unset(tmp_path):
    m = S.check_manifest(mf(tmp_path, "u2", exit_s=43_200, **V2))
    assert m["engine_warmup_h"] == 7 and m["engine_prune"] is False
    m = S.check_manifest(mf(tmp_path, "u2", exit_s=43_200, engine_prune=True))
    assert m["engine_prune"] is True and "engine_warmup_h" not in m
    m = S.check_manifest(mf(tmp_path, "u2", exit_s=43_200))
    assert "engine_warmup_h" not in m and "engine_prune" not in m                    # no default is written into a v1 manifest: its canonical sha256 does not move
    assert S.manifest_sha256(m) != S.manifest_sha256(dict(m, **V2))


@pytest.mark.parametrize("decider", ["c1nf", "u1"])
def test_the_keys_are_refused_for_the_other_deciders(tmp_path, decider):
    kw = {"u1": {"table_a": "x", "table_a_sha256": "0" * 64, "hr_exclusion": True}} if decider == "u1" else {}
    S.check_manifest(mf(tmp_path, decider, **kw))   # valid without the key
    for k, v in (("engine_warmup_h", 7), ("engine_prune", False)):
        with pytest.raises(S.Refused, match="only for the u4 / u2"):
            S.check_manifest(mf(tmp_path, decider, **dict(kw, **{k: v})))
    S.check_manifest(mf(tmp_path, "u4", exit_s=14_400, **V2))   # u4 may carry them (its own ruling decides whether it does)


def test_engine_warmup_longer_than_the_warmup_window_is_refused_at_start(tmp_path):
    m = mf(tmp_path, "u2", warmup_hours=5, exit_s=43_200, engine_warmup_h=7)
    S.check_manifest(m)
    with pytest.raises(S.Refused, match="longer than the warm-up window"):
        S.hours_needed(m)
    write_keep(tmp_path, fixture_rows())
    assert S.follow(m, fixture=True, once=True, _roots=T1.roots(tmp_path), decider_factory=lambda mm: D.U2Decider(START_MS, hr_pda_fn=T2.hr_pda), ledger_builder=lambda *a: None, engine_kw={}) == S.EXIT_REFUSED
    assert S.hours_needed(dict(m, warmup_hours=7))[1] == START_MS


def test_book_id_osb_v2_u2_and_its_twins_do_not_collide_with_osb_u2(tmp_path):
    ids = ["osb-u2", "osb-u2-lat3010", "osb-u2-lat5590", "osb-v2-u2", "osb-v2-u2-lat3010", "osb-v2-u2-lat5590"]
    bots = []
    for b in ids:
        m = S.check_manifest(mf(tmp_path, "u2", book_id=b, exit_s=43_200))
        bots.append("c1nf-fwd-" + S.re.sub(r"[^A-Za-z0-9_.-]", "-", m["book_id"])[:40])
    assert len(set(bots)) == 6
    v1, v2 = bots[:3], bots[3:]
    assert not any(b.startswith(a) for a in v1 for b in v2) and not any(b.startswith(a) for a in v2 for b in v1)   # v1's engine bot ids are not a prefix of any v2 id (QP-RULING-1118-u2-v2 section 4)
    m1, m2 = mf(tmp_path, "u2", book_id="osb-u2", exit_s=43_200), mf(tmp_path, "u2", book_id="osb-v2-u2", exit_s=43_200, **V2)
    assert S.manifest_sha256(S.check_manifest(m1)) != S.manifest_sha256(S.check_manifest(m2))


def test_the_ruling_is_cited_in_the_tool():
    assert "QP-RULING-1118-u2-v2" in Path(S.__file__).read_text()
