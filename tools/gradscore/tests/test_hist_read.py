"""Synthetic fixtures only; hist_read is never run on real data here."""
import json
import random

import pytest

from tools.gradscore import auc as au, hist_read as hr
from tools.gradscore import card as cd

BASE_T = 1_790_000_000


def write_block(d, b, n, signal, days=5, censor_every=0, seed=1):
    rng = random.Random(seed)
    with open(d / f"cards-{b}.jsonl", "w") as fc, open(d / f"labels-{b}.jsonl", "w") as fl, open(d / f"scores-{b}.jsonl", "w") as fs:
        for i in range(n):
            m = f"{b}-{i}"
            pos = rng.random() < 0.15
            t = BASE_T + (i % days) * 86400 + i
            card = {"mcap_usd": rng.randint(1, 10 ** 5), "liq_usd": rng.randint(1, 10 ** 5), "vol_5m_usd": rng.randint(1, 10 ** 5), "buys_5m": 3, "sells_5m": 2}
            fc.write(json.dumps({"mint": m, "first_sight_ts": t, "card_text": cd.card_text(card), "card_sha256": "x", "has_metadata": i % 2 == 0}) + "\n")
            cens = bool(censor_every and i % censor_every == 0)
            fl.write(json.dumps({"mint": m, "quote_mint": au.WSOL, "censored_24h": cens, "no_data": False, "max_mult_24h": 4.0 if pos else 1.2, "ret_4h": 0.1}) + "\n")
            sc = (70 if pos else 10) + rng.randint(0, 5) if signal else rng.randint(1, 90)
            for v in "AB":
                fs.write(json.dumps({"mint": m, "variant": v, "action": "SCORED", "runner_prob_pct": sc, "narrative": 1 + sc // 30,
                                     "rug_risk": 1 + (5 - sc // 30) % 5}) + "\n")


def test_refuses_without_allow_outcomes(capsys):
    assert hr.main(["--dir", "/nonexistent"]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_period_split_and_censored_exclusion(tmp_path):
    for b in hr.PERIODS["september_pooled"] + hr.PERIODS["explore-0814"]:
        write_block(tmp_path, b, 400, True, censor_every=10)
    ex, exc = hr.load_period(str(tmp_path), hr.PERIODS["explore-0814"])
    sp, exc2 = hr.load_period(str(tmp_path), hr.PERIODS["september_pooled"])
    assert len(ex["A"]) == 360 and exc["censored_24h"] == 40 * 2  # 40 per variant, counted for A and B
    assert len(sp["A"]) == 3 * 360 and all(r["mint"].startswith(("oracle", "fast")) for r in sp["A"])
    assert all(not r["mint"].startswith("explore") for r in sp["A"])


def test_both_period_pass_logic(tmp_path):
    for b in hr.PERIODS["september_pooled"] + hr.PERIODS["explore-0814"]:
        write_block(tmp_path, b, 400, True)
    per = {p: hr.load_period(str(tmp_path), bl)[0] for p, bl in hr.PERIODS.items()}
    res = hr.read_periods(per, draws=1000)
    assert res["periods"]["explore-0814"]["A"]["status"] == "READ"
    assert res["pass_both"]["A:runner_prob_pct"] is True  # separable synthetic signal passes in both periods
    # no signal in one period -> no pass
    for b in hr.PERIODS["september_pooled"]:
        write_block(tmp_path, b, 400, False, seed=7)
    per = {p: hr.load_period(str(tmp_path), bl)[0] for p, bl in hr.PERIODS.items()}
    res = hr.read_periods(per, draws=60)
    assert res["pass_both"]["A:runner_prob_pct"] is False
    assert set(res["strata"]["explore-0814"]) == {"has_metadata=True", "has_metadata=False"}


def test_small_period_is_not_decidable_and_table_has_disclosures(tmp_path):
    for b in hr.PERIODS["september_pooled"] + hr.PERIODS["explore-0814"]:
        write_block(tmp_path, b, 50, True)
    per = {p: hr.load_period(str(tmp_path), bl)[0] for p, bl in hr.PERIODS.items()}
    res = hr.read_periods(per, draws=20)
    assert res["periods"]["explore-0814"]["A"]["status"] == "NOT_DECIDABLE" and not any(res["pass_both"].values())
    txt = hr.table(res, {})
    assert "AUC is not P&L" in txt and "best of 6" in txt and "leakage" in txt
