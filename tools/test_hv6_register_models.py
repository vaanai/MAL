"""QP #914 condition (after the rebase): the model ids hv6_register hands to register_bot equal hv6_agents.MODEL_ID for every arm whose ARMS[a].llm is true,
and [] for the rule book. The bot ids equal the runner's bot_id(arm). Test-only; synthetic; no registry is opened."""
from __future__ import annotations

from tools import hv6_agents as A
from tools import hv6_register as REG
from tools import hv6_runner as RN


def test_models_follow_hv6_agents_for_every_arm():
    assert sorted(REG.HV6_BOTS) == sorted(RN.bot_id(a) for a in RN.ARM_ORDER)
    for arm in RN.ARM_ORDER:
        bid = RN.bot_id(arm)
        want = [A.MODEL_ID] if A.ARMS[arm].llm else []
        assert REG.HV6_MODELS[bid] == want, bid
    assert REG.HV6_MODELS["hv6.0-X"] == [] and all(REG.HV6_MODELS[b] == [A.MODEL_ID] for b in REG.HV6_BOTS if b != "hv6.0-X")
