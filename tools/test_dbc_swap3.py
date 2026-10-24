"""P17 tests on REAL Swap3 rows (tools/data/dbc_swap3_fixture.json: 110 trades, 2026-10-19T12-13, pools of 16 real configs incl. transfer-hook configs)."""
import json
from pathlib import Path

from tools import dbc_config as C
from tools import dbc_exact as X
from tools import dbc_price as L

FX = json.loads((Path(__file__).parent / "data" / "dbc_swap3_fixture.json").read_text())
CFG = {k: C.decode_b64(v) for k, v in FX["configs"].items()}


def test_fixture_size():
    assert len(FX["rows"]) >= 100
    assert sum(1 for r in FX["rows"] if CFG[r["config"]]["kind"] == "ConfigWithTransferHook") >= 25


def test_swap_math_atom_exact():
    n = 0
    for r in FX["rows"]:
        res = X.check_row(int(r["prev_next_sqrt_price"]), r, C.curve_points(CFG[r["config"]]["config"]))
        if res is None:
            continue
        n += 1
        assert all(res.values()), (r["signature"], res)
    assert n >= 100


def test_vault_deltas_equal_event_amounts():
    for r in FX["rows"]:
        if r["quote_vault_pre"] is None or r["base_vault_pre"] is None:
            continue
        dq, db = r["quote_vault_post"] - r["quote_vault_pre"], r["base_vault_post"] - r["base_vault_pre"]
        if r["side"] == "buy":
            assert dq == r["amount_in"] and db == -r["amount_out"]
        else:
            assert db == r["amount_in"] and dq == -r["amount_out"]


def test_fee_side_matches_config_collect_fee_mode():
    for r in FX["rows"]:
        c = CFG[r["config"]]["config"]
        if r["side"] == "buy":
            assert bool(r["fee_on_base_token"]) == (c["collect_fee_mode"] == 1)
        else:
            assert r["fee_on_base_token"] is False


def test_no_transfer_fee_on_fixture_configs():
    for k, a in CFG.items():
        assert a["config"]["transfer_fee_basis_points"] == 0
    for r in FX["rows"]:
        assert r["amount_in"] == r["amount_in_excl_fee"] and r["amount_out"] == r["amount_out_incl_transfer_fee"]


def test_hook_config_has_program_and_token2022():
    hooks = [a for a in CFG.values() if a["kind"] == "ConfigWithTransferHook"]
    assert hooks and all(len(a["transfer_hook_program"]) >= 32 and a["config"]["token_type"] == 1 for a in hooks)


def test_hook_rule_legacy_and_true():
    swap3 = {"event": "EvtSwap3", "ix": "Swap2"}
    hook = {"event": "EvtSwap3WithTransferHook", "ix": "Swap2WithTransferHook"}
    assert L.hook_row(swap3) and L.hook_row(hook)  # legacy default (P14)
    L.enable_swap3_pricing()
    try:
        assert not L.hook_row(swap3) and L.hook_row(hook)
        assert L.hook_row({"event": "EvtSwap2", "ix": "Swap2WithTransferHook"})
    finally:
        L.SWAP3_IS_HOOK, L.FOB_EVIDENCE = True, False


def test_spent_switch_refuses_without_marker_and_default_window_unchanged():
    old = L.SPENT_MARKER
    L.SPENT_MARKER = "/nonexistent/marker"
    try:
        try:
            L.enable_spent_exploration("/tmp/x")
            raise AssertionError("no refusal")
        except L.Refused:
            pass
    finally:
        L.SPENT_MARKER = old
    assert L.SPENT_ROOT is None and L.window()[1] == L.EXPLORE_END


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
