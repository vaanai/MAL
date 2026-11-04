"""Offline tests for tools.gradscore.hist (fixtures only; no network, no API, no real block)."""
import builtins
import json
import os
from types import SimpleNamespace

import pytest

from tools.haiku_caller import BudgetExceeded
from tools.gradscore import card as cd, hist as hs

H = 1_790_000_400 // 3600 * 3600  # an hour boundary
M = H + 600                        # migration instant; first sight t0 = M + 300
T0 = M + 300
WSOL = hs.WSOL
VM = hs.VMap([{"POOLA": 0, "POOLB": 0}])  # V = 0 so spot = q / b * 1e-3
B = 10 ** 12


def tr(ts, q, pool="POOLA", side="buy", sol=10 ** 9, mint="MINTX", b=B):
    return {"v": 2, "venue": "pumpswap", "mint": mint, "pool": pool, "side": side, "sol_lamports": sol, "token_raw": 10 ** 9,
            "quote_reserve": q, "base_reserve": b, "quote_mint": WSOL, "quote_is_wsol": True, "block_time": ts, "event_ts": ts,
            "slot": ts, "event_index": 0}


def mk_block(root, rows, hours=3, migs=((("MINTX", M)),), creates=None, name="blk"):
    d = root / name
    for sub in ("creates", "migrations", "trades"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    for i in range(hours):
        h = H + 3600 * i
        iso = hs.datetime.fromtimestamp(h, hs.timezone.utc).strftime("%Y-%m-%dT%H")
        with open(d / "trades" / f"trades-{iso}.jsonl", "w") as f:
            for r in rows:
                if h <= r["block_time"] < h + 3600:
                    f.write(json.dumps(r) + "\n")
    iso0 = hs.datetime.fromtimestamp(H, hs.timezone.utc).strftime("%Y-%m-%dT%H")
    with open(d / "migrations" / f"migrations-{iso0}.jsonl", "w") as f:
        for m, t in migs:
            f.write(json.dumps({"type": "complete", "mint": m, "event_ts": t}) + "\n")
    with open(d / "creates" / f"creates-{iso0}.jsonl", "w") as f:
        for m, c in (creates or {"MINTX": {"name": "Frog", "symbol": "FROG", "creator": "CRE", "event_ts": M - 99}}).items():
            f.write(json.dumps(dict({"type": "create", "mint": m}, **c)) + "\n")
    return root / name


@pytest.fixture
def allow(monkeypatch, tmp_path):
    monkeypatch.setattr(hs, "ALLOWED_BLOCKS", (str(tmp_path / "blk"),))
    return tmp_path


BASE = [tr(M + 10, 10 ** 12), tr(M + 100, 10 ** 12), tr(T0 + 100, 2 * 10 ** 12), tr(T0 + 200, 6 * 10 ** 12, side="sell", sol=1)]


def run_cards(tmp_path, rows, out="out"):
    blk = mk_block(tmp_path, rows)
    hs.cmd_cards(str(blk), str(tmp_path / out), fetch=False, workers=1, vmap=VM)
    return [json.loads(l) for l in open(tmp_path / out / "cards-blk.jsonl")]


def test_cards_use_only_pre_first_sight_trades(allow):
    a = run_cards(allow, BASE, "o1")
    post_changed = BASE[:2] + [tr(T0 + 100, 9 * 10 ** 12, side="sell", sol=5 * 10 ** 9), tr(T0 + 200, 1, side="buy", sol=77)]
    (allow / "blk").rename(allow / "blk_old")
    import shutil
    shutil.rmtree(allow / "blk_old")
    b = run_cards(allow, post_changed, "o2")
    assert len(a) == len(b) == 1
    assert a[0]["card_text"] == b[0]["card_text"]
    c = json.loads(a[0]["card_text"])
    assert c["buys_5m"] == 2 and c["sells_5m"] == 0 and c["age_min"] == 5 and "x_context" not in c
    assert c["name"] == "Frog" and a[0]["has_metadata"] is False and a[0]["has_x_context"] is False
    assert set(c) == set(cd.build_card({}, {}, {}, None, None))  # same field names as card.py


def test_cards_pool_is_most_volume_before_first_sight(allow):
    rows = [tr(M + 10, 10 ** 12, pool="POOLA", sol=10), tr(M + 20, 3 * 10 ** 12, pool="POOLB", sol=10 ** 10)]
    c = run_cards(allow, rows)[0]
    assert c["pool"] == "POOLB"


def test_skip_without_coverage_or_pool(allow):
    blk = mk_block(allow, [tr(M + 10, 10 ** 12)], migs=(("MINTX", M), ("NOPOOL", M), ("EARLY", H - 5), ("MINTX", M + 50)),
                   creates={"MINTX": {"name": "a", "symbol": "b", "creator": "c", "event_ts": 1}})
    meta = hs.cmd_cards(str(blk), str(allow / "o"), fetch=False, workers=1, vmap=VM)
    assert meta["counts"]["cards"] == 1 and meta["counts"]["duplicate_migrations"] == 1
    assert meta["skips"] == {"no_pool_at_first_sight": 1, "no_trade_coverage_start": 1}


def labels(tmp_path, rows, **kw):
    blk = mk_block(tmp_path, rows, **kw)
    meta = hs.cmd_labels(str(blk), str(tmp_path / "lo"), workers=1, vmap=VM)
    return meta, [json.loads(l) for l in open(tmp_path / "lo" / "labels-blk.jsonl")]


def test_labels_entry_max_and_horizons(allow):
    meta, (r,) = labels(allow, BASE)
    # entry = state at t0 = pre-trade spot of the first print after t0 (2e12/1e12*1e-3)
    assert r["p0"] == pytest.approx(2e-3) and r["no_data"] is False and r["quote_mint"] == WSOL
    # max over trades strictly after t0: the first post print's price is the NEXT print's pre spot (6e-3); the last is derived
    assert r["max_mult_24h"] == pytest.approx(3.0, rel=0.01)
    assert r["price_ts_1h"] == T0 + 200  # silent token keeps its last price, with the trade's timestamp
    assert r["censored_1h"] is False and r["censored_24h"] is True  # fixture block covers 3 h
    assert meta["counts"]["censored_24h"] == 1 and meta["counts"]["no_data"] == 0


def test_labels_ignore_pre_first_sight_prices(allow):
    _, (a,) = labels(allow, BASE)
    spiky = [tr(M + 10, 10 ** 12), tr(M + 50, 9 * 10 ** 13), tr(M + 100, 10 ** 12)] + BASE[2:]
    (allow / "lo").mkdir(exist_ok=True)
    import shutil
    shutil.rmtree(allow / "blk")
    _, (b,) = labels(allow, spiky)
    for k in ("p0", "max_mult_24h", "mult_1h", "mult_4h", "ret_4h"):
        assert a[k] == b[k]


def test_labels_no_data_only_without_any_trade_after_t0(allow):
    meta, (r,) = labels(allow, BASE[:2])
    assert r["no_data"] is True and r["max_mult_24h"] is None and meta["counts"]["no_data"] == 1


def test_late_trade_after_silence_sets_horizon_price(allow):
    rows = BASE[:3] + [tr(T0 + 4000, 4 * 10 ** 12, side="sell", sol=1)]
    _, (r,) = labels(allow, rows)
    assert r["price_ts_1h"] == T0 + 100 and r["price_ts_4h"] == T0 + 4000  # +1h = T0+3600 < T0+4000


def test_refuses_blocks_off_the_allow_list(tmp_path):
    with pytest.raises(hs.Refused):
        hs.check_block(str(tmp_path))
    with pytest.raises(hs.Refused):
        hs.check_block("/data/mal/clean-view/fresh-0828")
    with pytest.raises(hs.Refused):
        hs.check_block("/data/mal/clean-view/exp011-0909")
    assert hs.check_block("/data/mal/clean-view/explore-0814")


# ---------------------------------------------------------------- score

GOOD = json.dumps({"runner_prob_pct": 4, "narrative": 2, "rug_risk": 3, "reason": "plain"})


class FakeCaller:
    def __init__(self, cap_after=None):
        self.n, self.cap_after = 0, cap_after

    def call(self, **kw):
        if self.cap_after is not None and self.n >= self.cap_after:
            raise BudgetExceeded("cap")
        self.n += 1
        return SimpleNamespace(text=GOOD, usage={"input_tokens": 1}, cost_usd=0.001, request_sha256="r")


def write_cards(path, n=3):
    with open(path, "w") as f:
        for i in range(n):
            c = {"name": f"n{i}"}
            f.write(json.dumps({"mint": f"M{i}", "window": "all", "card_text": cd.card_text(c), "card_sha256": cd.card_sha256(c),
                                "first_sight_ts": i}) + "\n")


def test_score_never_touches_labels_resumes_and_caps(tmp_path, monkeypatch):
    cards = tmp_path / "cards-blk.jsonl"
    write_cards(cards)
    out = tmp_path / "o"
    out.mkdir()
    (out / "labels-blk.jsonl").write_text("SECRET")
    real_open = builtins.open

    def guard(p, *a, **k):
        assert "labels-" not in str(p), "score opened a labels file"
        return real_open(p, *a, **k)

    monkeypatch.setattr(builtins, "open", guard)
    fc = FakeCaller(cap_after=4)
    m = hs.cmd_score(str(cards), str(out), 1.0, workers=1, caller=fc)
    assert m["this_run"]["scored"] == 4 and m["this_run"]["cap_hit"] is True and fc.n == 4
    fc2 = FakeCaller()
    m2 = hs.cmd_score(str(cards), str(out), 1.0, workers=1, caller=fc2)
    assert fc2.n == 2 and m2["this_run"]["todo"] == 2  # resumed: only the missing (mint, variant) pairs
    fc3 = FakeCaller()
    hs.cmd_score(str(cards), str(out), 1.0, workers=1, caller=fc3)
    assert fc3.n == 0
    rows = [json.loads(l) for l in real_open(out / "scores-blk.jsonl")]
    assert len(rows) == 6 and {(r["mint"], r["variant"]) for r in rows} == {(f"M{i}", v) for i in range(3) for v in "AB"}
    assert m["prefix_sha256"].keys() == {"A", "B"} and len(m["prompt_py_sha256"]) == 64


def test_score_module_does_not_import_labels_reader():
    import inspect
    src = inspect.getsource(hs.cmd_score)
    assert "labels" not in src.replace("labels-", "").replace("never opens a labels file", "") or "label_row" not in src
    assert "cmd_labels" not in src and "load_labels" not in src


def test_frozen_prompt_hash():
    assert hs._check_frozen()


# ---------------------------------------------------------------- metadata

def test_meta_fetch_order_cache_and_negative(tmp_path):
    calls = []

    def opener(url, timeout):
        calls.append(url)
        if url.startswith("http://127.0.0.1"):
            raise OSError("kubo down")
        return json.dumps({"description": "d", "twitter": "https://x.com/foo"})

    f = hs.MetaFetcher(str(tmp_path / "c"), opener=opener, sleep=lambda s: None)
    uri = "https://ipfs.io/ipfs/bafkreiabc"
    assert f.get(uri)["description"] == "d"
    assert calls == ["http://127.0.0.1:8080/ipfs/bafkreiabc", "https://ipfs.io/ipfs/bafkreiabc"]
    assert f.get(uri)["description"] == "d" and len(calls) == 2 and f.stats["cached"] == 1
    bad = hs.MetaFetcher(str(tmp_path / "c2"), opener=lambda u, t: (_ for _ in ()).throw(OSError()), sleep=lambda s: None)
    assert bad.get("https://example.org/m.json") is None
    assert bad.get("https://example.org/m.json") is None and bad.stats["failed"] == 1 and bad.stats["cached"] == 1
