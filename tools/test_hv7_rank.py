"""HV7 C1 tests: tools/hv7_rank.py, RANK_v0.txt, rank.txt. Synthetic cards only; no tape, card file or job output is read."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

import tools.hv4_prompts as P
import tools.hv6_cards as hc
import tools.hv7_rank as R
from tools.haiku_caller import PRICES

ROOT = Path(__file__).resolve().parent
GEN = ROOT / "hv6_playbooks" / "GEN_v0.txt"
RANK = ROOT / "hv7_playbooks" / "RANK_v0.txt"
TD = "2026-11-01T03:02:00.000Z"
TD_MS = R.ms_from_iso(TD)


def card(i, as_of=TD_MS, **extra):
    c = {"id": f"pool{i}", "as_of_ms": as_of, "as_of_slot": 1000 + i, "x": None,
         "f": {"ret_60m": 0.01 * i, "vol_60m": 100.0 - i, "age_s": 8000, "quote_eff_sol": 100.0 + i},
         "risk": {"holder_count": 50 + i}, "setup": {"type": "PULLBACK"}}
    c.update(extra)
    return c


def cards(n=8, **kw):
    return [card(i, **kw) for i in range(n)]


def reply(order, why="f.ret_60m is positive"):
    return json.dumps([{"id": i, "why": why} for i in order])


# ---------------------------------------------------------------- playbook and template

def paras(text):
    return [p.strip("\n") for p in text.replace("=== RULES ===\n", "\n\n").split("\n\n")]


QP_CLAUSE = "Every other field is context, not evidence of direction; you may argue from them, but say it is a hypothesis, not a lab result."


def _one(text, prefix):
    m = [p for p in paras(text) if p.startswith(prefix)]
    assert len(m) == 1
    return m[0]


@pytest.mark.parametrize("prefix", ["UNTRUSTED TEXT", "CARDS\n", "HONESTY", "SCREEN, NOT REASON."])
def test_header_paragraphs_byte_identical_to_gen(prefix):
    assert _one(GEN.read_text(encoding="utf-8"), prefix).encode() == _one(RANK.read_text(encoding="utf-8"), prefix).encode()


def test_base_rate_truth_lines_byte_equal_and_no_skip_wording():
    g, r = _one(GEN.read_text(encoding="utf-8"), "BASE RATE."), _one(RANK.read_text(encoding="utf-8"), "BASE RATE.")
    cut = g.index(" Do not buy to fill a quota")
    assert cut > 0 and r.encode().startswith(g[:cut].encode())      # every measured number and the "earlier Haiku" truth, unchanged
    assert "SKIP" not in r and "skip" not in r and "quota" not in r
    assert r.endswith("Judge each pool on its fields alone.")


def test_measured_directions_uses_qp_clause():
    g, r = _one(GEN.read_text(encoding="utf-8"), "MEASURED DIRECTIONS."), _one(RANK.read_text(encoding="utf-8"), "MEASURED DIRECTIONS.")
    cut = g.index(" Every other field is context")
    assert r.encode().startswith(g[:cut].encode())
    assert r.endswith(" " + QP_CLAUSE) and r.count(QP_CLAUSE) == 1
    assert "do not claim" not in r and "Conviction" not in r and "Size 0.25" not in r and "improves the odds" not in r


def test_playbook_parses_and_has_ranking_task():
    pb = R.load_rank_playbook()
    assert pb.name == "RANK" and "RANKING TASK. You are shown N pools" in pb.rules
    assert "You cannot skip." in pb.rules and "The order shown is random and carries no information." in pb.rules
    assert "BUY, WATCH or SKIP" not in pb.text   # GEN's per-card answer sentence must not be carried over


def test_template_placeholders():
    t = R.load_rank_template()
    assert sorted(set(__import__("re").findall(r"\{\{([A-Z]+)\}\}", t.text))) == ["CARDS", "N", "NONCE", "TD"]


# ---------------------------------------------------------------- ordering validator

IDS = [f"pool{i}" for i in range(8)]


def test_valid_permutation():
    order = [IDS[i] for i in (3, 1, 7, 0, 2, 6, 5, 4)]
    v = R.validate_rank(reply(order), IDS)
    assert v.ok and v.order == tuple(order) and v.reason is None
    assert R.pick_top_k(v, 1) == (order[0],) and R.pick_top_k(v, 3) == tuple(order[:3])


def _bad(text, reason, ids=IDS):
    v = R.validate_rank(text, ids)
    assert not v.ok and v.reason == reason and v.order == ()
    assert R.pick_top_k(v, 1) == ()


def test_validator_rejections():
    _bad(reply(IDS[:7]), "missing_id")                                   # missing
    _bad(reply(IDS + ["pool8"]), "unknown_id")                           # extra / invented id
    _bad(reply(IDS[:7] + ["pool0"]), "duplicate_id")                     # duplicate
    _bad(reply(IDS[:7] + ["other"]), "unknown_id")                       # wrong id
    _bad(reply(IDS[:4]), "missing_id")                                   # wrong N
    _bad(reply(IDS[:7] + [IDS[0]]), "duplicate_id")
    _bad(json.dumps([{"id": i, "action": "SKIP", "why": "no"} for i in IDS]), "bad_item")   # SKIP text
    _bad(json.dumps([{"id": i, "action": "WATCH"} for i in IDS]), "bad_item")
    _bad("I cannot rank these.", "not_json")                             # refusal prose
    _bad("Here is the order:\n```json\n" + reply(IDS) + "\n```", "not_json")   # prose outside a code fence (one bare fence is removed: test_fence_rule)
    _bad(None, "refusal")                                                # timeout / budget stop / refusal
    _bad("", "empty")
    _bad("   \n", "empty")
    _bad("[]", "missing_id")
    _bad('{"ranking": []}', "not_list")
    _bad(json.dumps(IDS), "bad_item")                                    # bare id strings
    _bad(json.dumps([{"id": 5, "why": "x"}] * 8), "bad_item")


def test_bad_why_on_full_permutation_is_accepted_and_logged():
    v = R.validate_rank(json.dumps([{"id": i, "why": 3} for i in IDS]), IDS)
    assert v.ok and v.why_invalid == 8 and v.why_defect == 8 and v.order == tuple(IDS)
    mixed = [{"id": IDS[0], "why": None}, {"id": IDS[1]}] + [{"id": i, "why": "f.age_s is high"} for i in IDS[2:]]
    v = R.validate_rank(json.dumps(mixed), IDS)
    assert v.ok and v.why_invalid == 2 and v.why_without_field == 0 and v.why_defect == 2


def test_why_quality_is_flag_not_fallback():
    long = " ".join(["word"] * 25)
    v = R.validate_rank(reply(IDS, why=long), IDS)
    assert v.ok and v.why_words_over_20 == 8 and v.why_without_field == 8 and v.why_defect == 8
    v = R.validate_rank(json.dumps([{"id": i} for i in IDS]), IDS)
    assert v.ok and v.why_invalid == 8


def test_fallback_reasons_are_all_known():
    for r in ("empty", "refusal", "not_json", "not_list", "bad_item", "unknown_id", "duplicate_id", "missing_id"):
        assert r in R.FALLBACK_REASONS


# ---------------------------------------------------------------- determinism and uniformity

def shas(n=8, seed=0):
    return [hashlib.sha256(f"{seed}|{i}".encode()).hexdigest() for i in range(n)]


def test_display_perm_deterministic_and_a_permutation():
    s = shas()
    a, b = R.display_perm("L", TD, s), R.display_perm("L", TD, s)
    assert a == b and sorted(a) == list(range(8))
    assert R.display_perm("A", TD, s) != R.display_perm("B", TD, s)
    assert R.display_perm("L", TD, s) != R.display_perm("L", "2026-11-01T04:02:00.000Z", s)
    with pytest.raises(R.RankError):
        R.display_perm("L|x", TD, s)
    with pytest.raises(R.RankError):
        R.display_perm("L", "2026-11-01 03:02", s)


def test_display_perm_slot_uniformity():
    # the screen's #1 candidate lands in each of 8 slots about equally over 8,000 periods
    counts = [0] * 8
    for k in range(8000):
        t = R.iso_from_ms(TD_MS + k * 3_600_000)
        perm = R.display_perm("L", t, shas(8, k))
        counts[perm.index(0)] += 1
    chi = sum((c - 1000) ** 2 / 1000 for c in counts)
    assert chi < 24.32, (chi, counts)    # chi-square df 7, p = 0.001


def test_r_pick_determinism_and_order_independence():
    s = shas()
    ids = IDS
    assert R.r_pick(TD, ids, s) == R.r_pick(TD, ids, s)
    assert R.r_pick_index(TD, s) == R.r_pick_index(TD, list(reversed(s)))     # hash uses the sorted shas
    assert R.fallback_pick(TD, ids, s) == R.r_pick(TD, ids, s)
    exp = int(hashlib.sha256(("hv7-R|" + TD + "|" + ",".join(sorted(s))).encode()).hexdigest(), 16) % 8
    assert R.r_pick_index(TD, s) == exp
    with pytest.raises(R.RankError):
        R.r_pick(TD, ids[:7], s)


def test_r_pick_uniform_over_10000_sets():
    counts = [0] * 8
    for k in range(10_000):
        t = R.iso_from_ms(TD_MS + k * 3_600_000)
        counts[R.r_pick_index(t, shas(8, k))] += 1
    exp = 10_000 / 8
    chi = sum((c - exp) ** 2 / exp for c in counts)
    assert chi < 24.32, (chi, counts)    # chi-square df 7, p = 0.001


# ---------------------------------------------------------------- screen, s_pick, u_set, min-set rule

def test_screen_order_and_tie_by_pool_id():
    vol = {"b": 5.0, "a": 5.0, "c": 9.0, "d": 1.0}
    sc = R.screen_set(["a", "b", "c", "d"], vol, n=8, n_min=3)
    assert sc.ids == ("c", "a", "b", "d") and not sc.no_trade
    assert R.s_pick(sc) == "c" and R.u_set(sc) == sc.ids
    tie = R.screen_set(["z", "m", "q"], {"z": 2.0, "m": 2.0, "q": 2.0})
    assert tie.ids == ("m", "q", "z") and R.s_pick(tie) == "m"


def test_screen_takes_first_n_and_vetoed_replaced_by_next():
    vol = {f"p{i:02d}": float(100 - i) for i in range(12)}
    sc = R.screen_set(vol, vol)
    assert sc.ids == tuple(f"p{i:02d}" for i in range(8))
    sc2 = R.screen_set(vol, vol, vetoed={"p00", "p03"})
    assert sc2.ids == tuple(f"p{i:02d}" for i in (1, 2, 4, 5, 6, 7, 8, 9))


def test_min_set_rule_no_trade_below_three():
    for k in (0, 1, 2):
        vol = {f"p{i}": 1.0 + i for i in range(k)}
        sc = R.screen_set(vol, vol)
        assert sc.no_trade and R.s_pick(sc) is None and R.u_set(sc) == ()
    sc = R.screen_set({"a": 1.0, "b": 2.0, "c": 3.0}, {"a": 1.0, "b": 2.0, "c": 3.0})
    assert not sc.no_trade and len(sc.ids) == 3
    # a veto or a missing / non-finite volume that drops the set below 3 is a no-trade
    assert R.screen_set(["a", "b", "c"], {"a": 1.0, "b": 2.0, "c": 3.0}, vetoed={"a"}).no_trade
    assert R.screen_set(["a", "b", "c"], {"a": 1.0, "b": 2.0, "c": float("nan")}).no_trade
    assert R.screen_set(["a", "b", "c"], {"a": 1.0, "b": 2.0}).no_trade
    assert R.screen_set(["a", "b", "c"], {"a": 1.0, "b": 2.0, "c": True}).no_trade


def test_screen_argument_checks():
    with pytest.raises(R.RankError):
        R.screen_set([], {}, n=9)
    with pytest.raises(R.RankError):
        R.screen_set([], {}, n=4, n_min=5)
    for bad_min in (0, 1, 2):
        with pytest.raises(R.RankError):
            R.screen_set([], {}, n=8, n_min=bad_min)
    with pytest.raises(R.RankError):
        R.screen_set([], {}, n=2, n_min=2)


# ---------------------------------------------------------------- fallback to R

def test_fallback_to_r():
    s = shas()
    good = R.validate_rank(reply(list(reversed(IDS))), IDS)
    c = R.l_choice(good, TD, IDS, s)
    assert c == R.LChoice("pool7", False, None)
    for text, reason in ((None, "refusal"), ("nope", "not_json"), (reply(IDS[:7]), "missing_id")):
        c = R.l_choice(R.validate_rank(text, IDS), TD, IDS, s)
        assert c.fallback and c.reason == reason and c.pool_id == R.r_pick(TD, IDS, s)


# ---------------------------------------------------------------- request

def test_request_bytes_identical_across_builds_and_marker():
    a, b = R.build_rank_request(TD, cards()), R.build_rank_request(TD, cards())
    assert a.request == b.request and a.request.request_sha256() == b.request.request_sha256()
    assert a.request.user_text == b.request.user_text
    sysb = a.request.body()["system"]
    assert sum(1 for blk in sysb if "cache_control" in blk) == 1 and sysb[0]["cache_control"] == P.CACHE_MARKER
    assert a.request.max_tokens == R.MAX_TOKENS == 2000 and a.request.body()["max_tokens"] == 2000 and "temperature" not in a.request.body()
    assert sorted(a.shown_ids) == sorted(IDS) and a.shown_ids == tuple(a.screen_ids[i] for i in a.perm)
    assert a.request.ids == a.shown_ids and "Call " in a.request.user_text
    # a reply in the shown order validates against exactly these ids
    assert R.validate_rank(reply(a.shown_ids), a.request.ids).ok


@pytest.mark.parametrize("n", [3, 4, 5, 6, 7, 8])
def test_any_n_from_3_to_8(n):
    rr = R.build_rank_request(TD, cards(n))
    assert len(rr.shown_ids) == n and sorted(rr.shown_ids) == sorted(f"pool{i}" for i in range(n))
    assert f"{n} cards" in rr.request.user_text and f"exactly {n} objects" in rr.request.user_text
    assert R.validate_rank(reply(rr.shown_ids), rr.request.ids).ok
    assert not R.validate_rank(reply(rr.shown_ids[:-1]), rr.request.ids).ok


def test_cards_must_be_in_screen_order():
    cs = cards()
    cs[0], cs[1] = cs[1], cs[0]
    with pytest.raises(R.RankError):
        R.build_rank_request(TD, cs)
    cs = cards()
    cs[2]["f"] = dict(cs[2]["f"], vol_60m=None)
    with pytest.raises(R.RankError):
        R.build_rank_request(TD, cs)


def test_r_index_points_into_screen_order_not_display_order():
    differs = 0
    for k in range(50):
        t = R.iso_from_ms(TD_MS + k * 3_600_000)
        cs = cards(8, as_of=R.ms_from_iso(t))
        rr = R.build_rank_request(t, cs)
        idx = R.r_pick_index(t, rr.screen_card_shas)
        expected = f"pool{idx}"                                     # card i has vol 100 - i, so screen index i is pool i
        assert rr.screen_ids[idx] == expected
        c = R.l_choice(R.validate_rank("nope", rr.request.ids), t, rr.screen_ids, rr.screen_card_shas)
        assert c.fallback and c.pool_id == expected
        differs += rr.shown_ids[idx] != expected                    # the display order is a different order
    assert differs > 20


def test_request_changes_with_set_seed_and_tag():
    base = R.build_rank_request(TD, cards())
    assert R.build_rank_request(TD, cards(7)).request.user_text != base.request.user_text
    assert R.build_rank_request(TD, cards(), perm_tag="A").perm != R.build_rank_request(TD, cards(), perm_tag="B").perm
    assert R.build_rank_request(TD, cards(), seq=1).request.nonce != base.request.nonce


def test_request_checks():
    with pytest.raises(R.RankError):
        R.build_rank_request(TD, cards(2))                       # below the minimum set
    with pytest.raises(P.PromptError):
        R.build_rank_request(TD, cards(9))                       # above the cap
    with pytest.raises(R.RankError):
        R.build_rank_request(TD, cards(as_of=TD_MS + 1))         # card built after t_d
    with pytest.raises(R.RankError):
        R.build_rank_request(TD, cards(as_of=TD_MS - 1))


@pytest.mark.parametrize("key", ["outcome", "pnl_sol", "fwd_ret_60m", "future_ret", "realized_ret", "fill_price", "label", "exit_price",
                                 "prior_trade", "arm", "pick", "rank", "decision"])
def test_no_outcome_field_enters_a_request(key):
    cs = cards()
    cs[3] = dict(cs[3], **{key: 0.1})
    with pytest.raises(R.RankError):
        R.build_rank_request(TD, cs)
    cs = cards()
    cs[3]["f"] = dict(cs[3]["f"], **{key: 0.1})                  # nested too
    with pytest.raises(R.RankError):
        R.build_rank_request(TD, cs)


def test_normal_card_keys_pass_guard():
    assert R.forbidden_keys(cards()) == []
    assert R.forbidden_keys([{"f": {"ret_1m": 0, "dd_ath": 0, "alarm": 1, "quote_dd_peak": 0}}]) == []


def test_request_text_has_no_outcome_vocabulary():
    r = R.build_rank_request(TD, cards()).request
    low = (R.load_rank_template().text + r.user_text.split("\n")[0]).lower()
    for word in ("pnl", "p&l", "outcome", "your previous", "other arm"):
        assert word not in low


# ---------------------------------------------------------------- purity

def test_module_is_pure():
    src = (ROOT / "hv7_rank.py").read_text(encoding="utf-8")
    assert hc.forbidden_imports(src, hc.HV4_CODE_ONLY) == []
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module)
    assert mods <= {"__future__", "hashlib", "json", "math", "re", "dataclasses", "datetime", "pathlib", "tools", "tools.haiku_caller"}
    for banned in ("socket", "requests", "urllib", "subprocess", "os", "time", "random", "anthropic"):
        assert banned not in mods
    assert "datetime.now" not in src and "time.time" not in src and "open(" not in src.replace("def open", "")


# ---------------------------------------------------------------- cost arithmetic (DESIGN section 5)

def test_cost_arithmetic():
    p = PRICES["claude-haiku-5-5"]
    assert (p["input"], p["cache_write"], p["cache_read"], p["output"]) == (0.10, 0.125, 0.01, 0.50)
    row1 = R.cost_usd(2874, 9056, 400)
    assert row1 == pytest.approx((2874 * 0.125 + 9056 * 0.10 + 400 * 0.50) / 1e6)
    assert row1 == pytest.approx(0.00146485)
    assert 24 * row1 == pytest.approx(0.0351564)
    worst = R.cost_usd(2874, 9056, 600)
    assert worst == pytest.approx(0.00156485) and 24 * worst == pytest.approx(0.03755640)
    n12 = R.cost_usd(2874, 13384, 400)
    assert n12 == pytest.approx(0.00189765) and 24 * n12 == pytest.approx(0.0455436)
    assert 48 * row1 == pytest.approx(0.0703128)
    mem = R.cost_usd(0, 11056, 400, cache_read_tokens=2874)
    assert mem == pytest.approx(0.00133434) and 24 * mem == pytest.approx(0.03202416)
    assert R.cost_usd(0, 60000, 4000) == pytest.approx(0.008)
    # Arithmetic on the cap table, not enforcement: nothing here limits spend. HV6 caps 4 x 0.50 + 0.15 + 0.05, v6.1 parked at 0.00.
    hv6_caps = (0.50, 0.50, 0.50, 0.50, 0.15, 0.05)
    hv6, v61_parked, hv7 = sum(hv6_caps), 0.00, 0.30
    assert hv6 == pytest.approx(2.20)
    assert hv6 + v61_parked + hv7 == pytest.approx(2.50) and hv6 + v61_parked + hv7 <= 3.00
    assert 10 * hv7 == pytest.approx(3.00)
    assert R.ASSUMED_OUTPUT_TOKENS == 400 and R.MAX_TOKENS == 2000
    worst2000 = R.cost_usd(2874, 9056, 2000)          # DESIGN section 5 worst row after the 2026-10-30 max_tokens change
    assert worst2000 == pytest.approx(0.00226485) and 24 * worst2000 == pytest.approx(0.0543564) and 24 * worst2000 < hv7


# ---------------------------------------------------------------- C2 session: C1 nits

def test_duplicate_id_key_in_one_reply_object_is_bad_item():
    ids = ["a", "b", "c"]
    dup = '[{"id": "a", "id": "b", "why": "f.vol_60m"}, {"id": "b", "why": "f.vol_60m"}, {"id": "c", "why": "f.vol_60m"}]'
    v = R.validate_rank(dup, ids)
    assert not v.ok and v.reason == "bad_item"        # plain json.loads would keep the last "id" and read this as [b, b, c]
    dup2 = '[{"id": "a", "why": "f.x", "why": "f.y"}, {"id": "b", "why": "f.x"}, {"id": "c", "why": "f.x"}]'
    assert R.validate_rank(dup2, ids).reason == "bad_item"
    ok = '[{"id": "c", "why": "f.vol_60m"}, {"id": "a", "why": "f.vol_60m"}, {"id": "b", "why": "f.vol_60m"}]'
    assert R.validate_rank(ok, ids).ok


def test_wrong_length_reason_is_gone_and_every_length_fault_has_a_reason():
    assert "wrong_length" not in R.FALLBACK_REASONS
    ids = ["a", "b", "c"]
    one = lambda i: {"id": i, "why": "f.vol_60m"}
    assert R.validate_rank(json.dumps([one("a"), one("b")]), ids).reason == "missing_id"
    assert R.validate_rank(json.dumps([one("a"), one("b"), one("c"), one("d")]), ids).reason == "unknown_id"
    assert R.validate_rank(json.dumps([one("a"), one("b"), one("c"), one("c")]), ids).reason == "duplicate_id"
    assert R.validate_rank(json.dumps([]), ids).reason == "missing_id"


def test_display_slot_of_matches_the_shown_order():
    t = R.iso_from_ms(TD_MS)
    cs = [card(i) for i in range(6)]
    rr = R.build_rank_request(t, cs)
    for pid in rr.screen_ids:
        assert R.display_slot_of(rr.perm, rr.screen_ids, pid) == list(rr.shown_ids).index(pid)
    assert sorted(R.display_slot_of(rr.perm, rr.screen_ids, p) for p in rr.screen_ids) == list(range(6))
    with pytest.raises(ValueError):
        R.display_slot_of(rr.perm, rr.screen_ids, "nope")


@pytest.mark.parametrize("n", range(3, 9))
def test_r_pick_uniform_for_every_realised_n(n):
    counts = [0] * n
    for k in range(6000):
        t = R.iso_from_ms(TD_MS + k * 3_600_000)
        counts[R.r_pick_index(t, shas(n, k))] += 1
    exp = 6000 / n
    chi = sum((c - exp) ** 2 / exp for c in counts)
    crit = {2: 13.82, 3: 16.27, 4: 18.47, 5: 20.52, 6: 22.46, 7: 24.32}[n - 1]     # chi-square p = 0.001 at df n-1
    assert chi < crit, (n, chi, counts)


def test_screen_set_argument_names_and_message():
    sc = R.screen_set(["a", "b", "c"], vol_60m={"a": 3.0, "b": 2.0, "c": 1.0})
    assert sc.ids == ("a", "b", "c")
    with pytest.raises(R.RankError, match=r"3 <= n_min <= n <= 8 \(got n=9, n_min=3\)"):
        R.screen_set(["a"], {"a": 1.0}, n=9)
    with pytest.raises(R.RankError, match=r"got n=8, n_min=2"):
        R.screen_set(["a"], {"a": 1.0}, n_min=2)


# ---------------------------------------------------------------- reply format (2026-10-30, after real-model smoke #1779)
# The smoke's 31 cached replies were read for STRUCTURE only (stop_reason, output_tokens, length, first character, fence, JSON validity, item and
# id counts; never an id, a why text or an order): 30 stopped at max_tokens 600 with 6 or 7 of 8 items written (28 bare, 2 inside an unclosed ```json
# fence), 1 ended normally after 4 items with one id twice (duplicate_id). The fixtures below rebuild those shapes from synthetic ids of the
# real length (44 base58 characters) and why texts of the real length range; no reply byte is copied.

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58_id(i):
    h = hashlib.sha256(f"synthetic-{i}".encode()).digest()
    return "".join(B58[b % 58] for b in (h + h)[:44])


IDS44 = [b58_id(i) for i in range(8)]
WHY_SHORT = "f.vol_60m highest"
WHY_LONG = "f.quote_eff_sol deep and f.impact_rt_025 low, f.ret_60m positive; hypothesis that f.net_flow_15m buying continues near term"


def compact(items):
    return json.dumps(items, separators=(",", ":"))


def full_reply(why=WHY_LONG, order=None):
    return compact([{"id": i, "why": why} for i in (order or IDS44)])


def test_fence_rule_one_bare_fence_is_removed():
    body = full_reply()
    for wrapped in ("```json\n" + body + "\n```", "```\n" + body + "\n```", "```JSON\n" + body + "\n```\n", "  ```json\n" + body + "```  \n",
                    "```json\r\n" + body + "\r\n```"):
        v = R.validate_rank(wrapped, IDS44)
        assert v.ok and v.order == tuple(IDS44) and v.reason is None, wrapped[:12]
    assert R.validate_rank(body, IDS44).order == R.validate_rank("```json\n" + body + "\n```", IDS44).order   # the fence never changes an order


def test_fence_rule_keeps_every_other_wrapper_a_fallback():
    body = full_reply()
    for bad in ("Ranking:\n```json\n" + body + "\n```",            # prose before
                "```json\n" + body + "\n```\nTop pick is the first.",   # prose after
                "```json\n" + body + "\n```\n```json\n" + body + "\n```",   # two fences
                "```python\n" + body + "\n```",                      # another language tag
                "```json " + body + "```",                            # no newline after the info string
                "```json\n" + body,                                   # opened, never closed
                "Ranking: " + body,                                   # bare prose prefix
                body + " Done."):
        assert R.validate_rank(bad, IDS44).reason == "not_json", bad[:20]
    v = R.validate_rank("```json\n" + full_reply(order=IDS44[:7]) + "\n```", IDS44)
    assert not v.ok and v.reason == "missing_id"                       # inside the fence every permutation check still applies


def _cut(k):
    """A reply cut mid-item the way max_tokens cuts it: the first k full items, then part of the next one."""
    items = [compact([{"id": i, "why": WHY_LONG}])[1:-1] for i in IDS44]
    head = "[" + ",".join(items[:k]) + ("," if k else "")
    return head + items[k][: len(items[k]) // 2]


@pytest.mark.parametrize("k", [6, 7])
def test_smoke_1779_shapes_cut_replies_stay_not_json(k):
    assert R.validate_rank(_cut(k), IDS44).reason == "not_json"                    # 28 of the 30 cut replies: bare array, cut mid-item
    assert R.validate_rank("```json\n" + _cut(k), IDS44).reason == "not_json"     # 2 of 30: inside a ```json fence that never closed


def test_smoke_1779_shape_short_reply_with_a_repeated_id_stays_duplicate_id():
    order = [IDS44[0], IDS44[1], IDS44[2], IDS44[1]]                  # ended normally after 4 items, one id twice, 3 distinct of 8
    assert R.validate_rank(compact([{"id": i, "why": WHY_SHORT} for i in order]), IDS44).reason == "duplicate_id"


def test_max_tokens_covers_a_full_reply():
    """At the smoke's measured worst structure (the longest item, the fewest chars per token) a full N_MAX reply plus a fence fits in MAX_TOKENS
    with at least a 1.5x margin. At 600 it did not: 30 of 31 replies were cut."""
    fence = len("```json\n") + len("\n```")
    worst_chars = 2 + R.N_MAX * R.MEASURED_MAX_ITEM_CHARS + fence
    worst_tokens = worst_chars / R.MEASURED_MIN_CHARS_PER_TOKEN
    assert worst_tokens * 1.5 <= R.MAX_TOKENS
    assert 600 < worst_tokens                                           # the old cap is below the measured worst full reply
    assert len(full_reply()) <= 2 + R.N_MAX * R.MEASURED_MAX_ITEM_CHARS   # the synthetic long fixture is inside the measured envelope
