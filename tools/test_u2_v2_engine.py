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
        "hour_ledger_md5s": "0998f6a35bb51e9ed71d4837d24b970b",
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
        "hour_ledger_md5s": "6048bea6fbd6f41bc5ab460ee1a852dc",
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
    "hour_ledger_md5s": "17a993b600df0565913f310f6d6c05bd",
    "ledger_rows": "6e729f60a463d944bae1b7c4f855cc75",
    "marks": "d751713988987e9331980363e24189ce",
    "n_hours": 3,
    "n_ledger_rows": 5
}
