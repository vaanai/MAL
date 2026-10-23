"""DEC-031 Amendment 8: the 160,000-lamport send stack (arena_rules.stack_config_v2) against the pinned 505,000 stack (stack_config). No network, no tape.

(a) the default / old configuration is unchanged (fields, as_dict, parity fixtures md5); (b) the 160k configuration differs by exactly 2 x 345,000 lamports per filled
round trip and 1 x 345,000 per guarded or stranded trip (one send), on the committed parity cases; (c) pins for the new configuration only."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools import arena_rules as R
from tools import arena_rules_v2 as RV2
from tools import arena_stack_v2 as V2
from tools import arena_report_legs as RL
from tools import arena_screen as S
from tools.arena_paper import StackConfig, price_leg
from tools.arena_rules import STACK_FIELDS, stack_config
from tools.arena_stack_v2 import STACK_FIELDS_V2, stack_config_for, stack_config_v2
from tools.test_arena_paper import BINDING, BINDING_MD5, CASES_SHA256, FIX, KEYS, LEGACY, LEGACY_MD5, _replay, _states
from tools.test_arena_rules_stack import PINNED_AS_DICT, PINNED_FIELDS

EXPECTED_DUST_FLIPS = {"total": 12, "sc": 0}  # 12 of the (case, stake, latency) legs on the committed set: sells worth 160,000..505,000 lamports (rugged pool ps048)
DELTA = 505_000 - 160_000  # 345,000
OLD, NEW = stack_config(), stack_config_v2()

PINNED_FIELDS_V2 = {**PINNED_FIELDS, "send": 160_000}
PINNED_AS_DICT_V2 = {**PINNED_AS_DICT, "send": 160_000}


def _cases():
    raw = (FIX / "parity_cases.jsonl").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CASES_SHA256
    return [json.loads(x) for x in raw.decode().splitlines()]


# ----------------------------------------------------------------------------------------------------------------------------------- (a) old stays
def test_default_stack_is_unchanged_505k():
    assert dataclasses.asdict(stack_config()) == PINNED_FIELDS
    assert stack_config(7).as_dict() == PINNED_AS_DICT
    assert STACK_FIELDS["send"] == 505_000
    assert stack_config_for(1, 7) == stack_config(7)
    assert S.screen_cfg() == stack_config(7) and S.screen_cfg(0, 505_000) == stack_config(7)


def test_old_config_reproduces_the_committed_parity_md5s_on_the_same_cases():
    cases = _cases()
    for cfg, rule, fname, md5 in ((LEGACY, "sc", "parity_expected.jsonl", LEGACY_MD5), (BINDING, "total", "parity_expected_binding.jsonl", BINDING_MD5)):
        assert cfg.send == 505_000
        got = _replay(cases, cfg, rule)
        lines = [json.dumps({"case_id": c["case_id"], **{k: got[c["case_id"]][k] for k in KEYS}}, sort_keys=True) for c in cases]
        exp = {json.loads(x)["case_id"]: json.loads(x) for x in (FIX / fname).read_text().splitlines()}
        for c in cases:
            for k in KEYS:
                assert round(got[c["case_id"]][k]) == round(exp[c["case_id"]][k])  # lamport-exact against the committed reference
        assert len(lines) == len(cases)


def test_stack_config_for_refuses_other_versions():
    assert stack_config_for(1, 7) == stack_config()
    for bad in (0, 3, -1, None, "2", True):
        with pytest.raises(ValueError):
            stack_config_for(bad)  # type: ignore[arg-type]


class _Stop(Exception):
    pass


def _run_main(monkeypatch, extra):
    seen = {}

    def fake_for(version, d):
        seen["version"] = version
        raise R.HostError("stop here")

    monkeypatch.setattr(RV2, "stack_config_for", fake_for)
    monkeypatch.setattr(RV2, "ArenaState", lambda *a, **k: None)
    rc = RV2.main(["--rules", "l1-floor-v2", "--mode", "replay", "--frozen-sha", "abc1234", "--clock-start-ms", "1", "--from-ms", "1", "--to-ms", "2",
                   "--root", "/tmp/x-root", "--ledger-root", "/tmp/x-ledger", "--registry", "/tmp/x-reg.json"] + extra)
    return rc, seen


def test_arena_rules_v2_cli_default_is_version_1(monkeypatch):
    assert _run_main(monkeypatch, []) == (2, {"version": 1})
    assert _run_main(monkeypatch, ["--stack-version", "2"]) == (2, {"version": 2})


def test_arena_rules_v2_cli_rejects_version_3(monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        _run_main(monkeypatch, ["--stack-version", "3"])
    assert e.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_arena_rules_py_is_not_given_a_stack_flag():
    assert not hasattr(R, "stack_config_v2") and not hasattr(R, "stack_config_for") and "--stack-version" not in Path(R.__file__).read_text()


# ----------------------------------------------------------------------------------------------------------------------------------- resume / read safety
def _ledger(tmp_path, bot, sends):
    d = tmp_path / bot
    d.mkdir(parents=True)
    (d / "trades-2026-10-24.jsonl").write_text("".join(json.dumps({"trade_id": str(i), "stack": {"send": x}}) + "\n" for i, x in enumerate(sends)))
    return tmp_path


def test_ledger_send_guard(tmp_path):
    root = _ledger(tmp_path, "b1", [160_000, 160_000])
    V2.check_ledger_send(root, ["b1", "no-rows-yet"], 160_000)
    with pytest.raises(R.HostError):
        V2.check_ledger_send(root, ["b1"], 505_000)
    with pytest.raises(R.HostError):
        V2.check_ledger_send(_ledger(tmp_path / "m", "b2", [160_000, 505_000]), ["b2"], 160_000)


def test_hostv2_resume_refuses_a_ledger_with_another_send(tmp_path):
    from types import SimpleNamespace
    root = _ledger(tmp_path, "b1", [505_000])
    h = object.__new__(RV2.HostV2)
    h.engine = SimpleNamespace(ledger=SimpleNamespace(root=root), cfg=stack_config_v2())
    h.rules = [SimpleNamespace(bot_id="b1")]
    with pytest.raises(R.HostError, match="refused"):
        h.resume()  # refused before verify_registered / the chain is touched


def test_report_refuses_rows_with_another_send_than_the_pin():
    rows = [{"stack": {"send": 160_000}}, {"stack": {"send": 160_000}}]
    assert RL.book_send(rows, 160_000) == 160_000 and RL.book_send(rows) == 160_000
    with pytest.raises(RL.Refused):
        RL.book_send(rows, 505_000)
    with pytest.raises(RL.Refused):
        RL.book_send(rows + [{"stack": {"send": 505_000}}], 160_000)
    with pytest.raises(RL.Refused):
        RL.book_send(rows + [{"stack": {"send": 505_000}}])


def test_v2_job_script_passes_the_flag_and_the_v1_script_is_unchanged():
    root = Path(__file__).resolve().parent.parent / "scripts" / "research"
    v2, v1 = (root / "arena-rules-v2-send-job.sh").read_text(), (root / "arena-rules-v2-job.sh").read_text()
    assert "--stack-version \"$STACK\"" in v2 and "ARENA_RULES_STACK_VERSION:?" in v2 and "tools/arena_rules_v2.py" in v2
    assert "stack-version" not in v1


# ----------------------------------------------------------------------------------------------------------------------------------- (c) new pins
def test_v2_fields_and_as_dict_are_pinned():
    assert dataclasses.asdict(stack_config_v2()) == PINNED_FIELDS_V2
    assert stack_config_v2(7).as_dict() == PINNED_AS_DICT_V2
    assert stack_config_for(2, 7) == stack_config_v2(7)


def test_v2_differs_from_v1_in_send_only():
    a, b = dataclasses.asdict(OLD), dataclasses.asdict(NEW)
    assert {k for k in a if a[k] != b[k]} == {"send"}
    assert set(STACK_FIELDS_V2) == set(STACK_FIELDS) and set(STACK_FIELDS_V2) | {"feed_delay_slots"} == {f.name for f in dataclasses.fields(StackConfig)}
    assert NEW.send == 160_000 == 155_000 + 5_000 and V2.SEND_V2 == 160_000


def test_v2_validates_the_feed_delay_like_v1_and_takes_no_keyword():
    assert stack_config_v2(9).feed_delay_slots == 9
    for bad in (-1, 1.5, True, None):
        with pytest.raises(ValueError):
            stack_config_v2(bad)
    with pytest.raises(TypeError):
        stack_config_v2(7, send=1)


def test_screen_cfg_with_160k_equals_the_v2_host_stack():
    assert S.screen_cfg(0, 160_000) == stack_config_v2(7)
    assert S.screen_cfg(S.RENT_CHARGED, 160_000).rent == 2_039_280 and S.screen_cfg(S.RENT_CHARGED, 160_000).send == 160_000


# ----------------------------------------------------------------------------------------------------------------------------------- (b) the difference
@pytest.mark.parametrize("rule", ["total", "sc"])
def test_160k_differs_by_exactly_the_send_difference_on_every_parity_case(rule):
    cfg_old = dataclasses.replace(OLD, feed_delay_slots=0, strand_rule=rule)
    cfg_new = dataclasses.replace(NEW, feed_delay_slots=0, strand_rule=rule)
    counts = {"filled": 0, "guarded": 0, "strand": 0, "other": 0}
    flips: list = []
    for c in _cases():
        st = _states(c)
        for prog in (c["prog"],):
            for stake in (0.25e9, 0.5e9, 1.0e9):
                for lat in (1.3, 1.9):
                    o, n = price_leg(prog, stake, st, lat, cfg_old, rule), price_leg(prog, stake, st, lat, cfg_new, rule)
                    if (o["state"], o["strand"]) == ("stranded", True) and n["state"] == "filled":
                        # the dust rule: a sell whose proceeds are below ONE SEND is a strand (rule "total"). A cheaper send moves the line from 505,000 to 160,000, so a sell
                        # worth 160,000..505,000 is a strand at 505k and a (tiny) fill at 160k. The ONLY state change the new send can cause.
                        flips.append((c["case_id"], stake, lat, o["pnl"], n["pnl"]))
                        assert rule == "total" and o["pnl"] == pytest.approx(-stake - 505_000 - 2_039_280)  # the old strand is the total-loss class
                        continue
                    assert (o["state"], o["filled"], o["strand"]) == (n["state"], n["filled"], n["strand"])  # same fate on the committed cases
                    if o["state"] == "filled":
                        counts["filled"] += 1
                        assert o["pnl"] - n["pnl"] == pytest.approx(-2 * DELTA, abs=1e-3)  # the cheaper send makes the trip DELTA*2 better
                        assert o["e1_extra"] == n["e1_extra"]  # the E1 floor does not depend on the send
                        # fail blend: flat = 0.85 pnl - 0.15 send ; press = (1-p) pnl - p send, p unchanged (a function of the fills only)
                        assert o["p"] == n["p"]
                        assert n["flat"] - o["flat"] == pytest.approx(0.85 * 2 * DELTA + 0.15 * DELTA, abs=1e-3)
                        assert n["press"] - o["press"] == pytest.approx((1 - o["p"]) * 2 * DELTA + o["p"] * DELTA, abs=1e-3)
                    elif o["state"] == "guarded":
                        counts["guarded"] += 1
                        assert o["pnl"] == -505_000.0 and n["pnl"] == -160_000.0  # a guarded buy pays one send
                    else:
                        counts["strand" if o["strand"] or "strand" in o["state"] else "other"] += 1
                        if rule == "total":
                            assert n["pnl"] - o["pnl"] == pytest.approx(DELTA, abs=1e-3)  # -stake - send - rent_stranded: one send
    assert counts["filled"] > 100  # the fixture is not vacuous
    assert len(flips) == EXPECTED_DUST_FLIPS[rule]


def test_hand_checked_filled_round_trip_and_strand_and_guard():
    st = {"ref": 80e9 / 8e14, "Qe": {1.3: 80e9, 1.9: 80e9}, "Be": {1.3: 8e14, 1.9: 8e14}, "ssb": {1.3: 0.0, 1.9: 0.0}, "nearby": {1.3: 0.0, 1.9: 0.0}, "Qx": 80e9, "Bx": 8e14}
    o, n = price_leg("pumpswap", 0.25e9, st, 1.9, OLD, "total"), price_leg("pumpswap", 0.25e9, st, 1.9, NEW, "total")
    assert o["state"] == n["state"] == "filled" and n["pnl"] - o["pnl"] == pytest.approx(2 * DELTA)
    bad = dict(st, Qx=1e9)
    o, n = price_leg("pumpswap", 0.25e9, bad, 1.9, OLD, "total"), price_leg("pumpswap", 0.25e9, bad, 1.9, NEW, "total")
    assert o["strand"] and n["strand"]
    assert o["pnl"] == pytest.approx(-0.25e9 - 505_000 - 2_039_280) and n["pnl"] == pytest.approx(-0.25e9 - 160_000 - 2_039_280)  # strand rent unchanged
    # stranded rent and rent=0 on filled trips are untouched by the send change
    assert OLD.rent == NEW.rent == 0


def test_every_other_arena_default_send_is_still_505k():
    """The pinned modules are not edited: arena_paper.StackConfig() and arena_stack still say 505,000 (their sha pins and the registered books' stacks)."""
    from tools import arena_h5_cost, arena_stack
    assert StackConfig().send == 505_000 and arena_stack.SEND_FEE_DECIDING == 505_000 and arena_h5_cost.SEND == 505_000
    assert np.isfinite(S.screen_cfg().send)
