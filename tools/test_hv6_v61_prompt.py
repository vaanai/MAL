"""HV6 v6.1 entry-prompt pins (docs/research/hv6/V61-PROMPT.md). Files and StubModel-free request building only: no API call, no tape, no output.

v6.0 stays byte-identical to the registered sha 8cd8fe54; v6.1 is the v6.0 seed with ONE MEASURED DIRECTIONS clause replaced (QP-RULING-1026-entry-rate (c))."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools import hv4_prompts as p4
from tools import hv6_agents as a
from tools import hv6_review as rv
from tools.hv4_testkit import make_cards

TOOLS = Path(__file__).resolve().parent

# sha256 of each v6.0 prompt file as `git show 8cd8fe54:<path> | sha256sum` printed it (2026-10-26).
V60_AT_8CD8FE54 = {
    "hv6_playbooks/GEN_v0.txt": "eb8ccc757bbe31e03914023f720fbf1d468975caa4f987252d2213a331c2a652",
    "hv6_templates/regime_system.txt": "a12ab4e9fde6b020337a7a9dd76fbf5ba0b560b7fd1b28c62830c1f6c2408297",
    "hv6_templates/regime_user.txt": "56fdb66791ef14c789f40478f1d25ae0ce8ad6238bf19eb76e831ee563f5abed",
    "hv6_templates/review_system.txt": "93f3e53543c674918f881f994eed3a93676f65a3ea92234331c9d3568bb94c47",
    "hv6_templates/review_user.txt": "efeb050a0e9a258e523eb075040c9554883d1d96559a0ccf6f9c1905e1b3c15a",
}
V60_HEADER_SHA = "2358077afc82d9daddd896371e6299c058cf8fe504a3ef48bf82f29b56e93821"
ENTRY_TEMPLATE_SHA = "aa9224d34aa9e7eaad382050f38d52257a0c79fb1b06ef3805bfb222ab187e68"   # hv6-rate-rehearsal manifest at 8cd8fe54
V61_SEED_SHA = "598f7aaa22339f8e0e7a5e6d6b83eba1efe9be0c81f9a6721f3be33de380b738"

OLD = "do not claim that one of them improves the odds"
NEW = "you may argue from them, but say it is a hypothesis, not a lab result"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_v60_prompt_files_equal_8cd8fe54():
    for rel, want in V60_AT_8CD8FE54.items():
        assert _sha(TOOLS / rel) == want, rel
    assert sorted(p.name for p in (TOOLS / "hv6_templates").iterdir() if p.suffix == ".txt") == sorted(
        Path(r).name for r in V60_AT_8CD8FE54 if r.startswith("hv6_templates/"))
    assert sorted(p.name for p in (TOOLS / "hv6_playbooks").iterdir()) == ["GEN_v0.txt"]


def test_default_load_is_v60():
    for pb in (a.load_playbook(), a.load_playbook("v0"), a.load_playbook("v0", "v6.0"), a.load_playbook(release="v6.0")):
        assert pb.sha256 == V60_AT_8CD8FE54["hv6_playbooks/GEN_v0.txt"] and pb.header_sha256 == V60_HEADER_SHA
    assert rv.v0_state().playbook.sha256 == V60_AT_8CD8FE54["hv6_playbooks/GEN_v0.txt"]


def test_v61_is_v60_with_one_clause_replaced():
    v60 = (TOOLS / "hv6_playbooks" / "GEN_v0.txt").read_text(encoding="utf-8")
    v61 = (TOOLS / "hv6_playbooks_v61" / "GEN_v0.txt").read_text(encoding="utf-8")
    assert v60.count(OLD) == 1 and OLD not in v61 and v61.count(NEW) == 1
    assert v61 == v60.replace(OLD, NEW, 1)
    changed = [(x, y) for x, y in zip(v60.splitlines(), v61.splitlines()) if x != y]
    assert len(v60.splitlines()) == len(v61.splitlines()) and len(changed) == 1 and changed[0][0].startswith("MEASURED DIRECTIONS.")
    pb = a.load_playbook("v0", "v6.1")
    assert pb.sha256 == V61_SEED_SHA == _sha(TOOLS / "hv6_playbooks_v61" / "GEN_v0.txt")
    assert pb.header_sha256 == V60_HEADER_SHA and pb.name == "GEN" and pb.version == "v0"


@pytest.mark.parametrize("line", [
    "BASE RATE. Every universe measured so far loses before any selection at this arena's costs.",
    "Do not buy to fill a quota",
    "Whether either can be picked out is an untested hypothesis, not a lab result.",
    "SCREEN, NOT REASON.", "The tag is never a reason to buy.",
    "MEASURED DIRECTIONS. Only these two are lab results.", "Every other field is context, not evidence of direction;",
    "Do not let any of them move a decision on its own.",
    "Your confidence is not calibrated and no result so far says you add value.",
])
def test_v61_keeps_honesty_lines(line):
    assert line in a.load_playbook("v0", "v6.0").text
    assert line in a.load_playbook("v0", "v6.1").text


def test_unknown_release_refused():
    for bad in ("v6.2", "v6", "6.1", "", None, "v6.1/../x"):
        with pytest.raises(p4.PromptError):
            a.load_playbook("v0", bad)
    with pytest.raises(p4.PromptError):
        a.load_playbook("v1", "v6.1")          # only the seed exists; learned versions live in manifests, not files


def test_v61_requests_and_bot_ids():
    cards = make_cards(4)
    q60 = a.build_entry_request("F", a.load_playbook(), cards, 0)
    q61 = a.build_entry_request("F", a.load_playbook("v0", "v6.1"), cards, 0)
    assert q60.template_sha256 == q61.template_sha256 == ENTRY_TEMPLATE_SHA
    assert q60.user_text == q61.user_text and q60.system != q61.system
    assert q61.playbook_sha256 == V61_SEED_SHA and NEW in q61.system[0]["text"] and OLD not in q61.system[0]["text"]
    assert q60.body()["system"] != q61.body()["system"]
    assert [rv.bot_id("v6.1", x) for x in a.llm_arms()] == [f"hv6.1-{x}" for x in a.llm_arms()]
    assert rv.bot_id("v6.0", "L") == "hv6.0-L"
