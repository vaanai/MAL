"""r4bt_gate: the git ancestor gate on a throwaway repo, the registry check, the closure walk. Synthetic only."""
from __future__ import annotations

import subprocess

import pytest

from tools import r4bt_gate as G


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def make_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.org")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a")
    git(tmp_path, "add", "a.txt")
    git(tmp_path, "commit", "-q", "-m", "first")
    first = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", first)
    (tmp_path / "b.txt").write_text("b")
    git(tmp_path, "add", "b.txt")
    git(tmp_path, "commit", "-q", "-m", "second (not on origin/main)")
    return first, git(tmp_path, "rev-parse", "HEAD")


def test_git_is_ancestor_true_false_and_quiet(tmp_path, capfd):
    first, second = make_repo(tmp_path)
    assert G.git_is_ancestor(tmp_path, first, fetch=False) is True
    assert G.git_is_ancestor(tmp_path, second, fetch=False) is False
    assert G.git_is_ancestor(tmp_path, "f" * 40, fetch=False) is False  # unknown commit: not an ancestor
    out = capfd.readouterr()
    assert out.out == "" and out.err == ""  # no git output is echoed


def test_a_failed_fetch_fails_closed(tmp_path):
    first, _ = make_repo(tmp_path)  # no remote named origin: the quiet fetch fails
    with pytest.raises(G.Refused, match="fetch"):
        G.git_is_ancestor(tmp_path, first, fetch=True)


def test_check_registry_allows_only_the_exact_shared_file_and_only_on_request():
    with pytest.raises(G.Refused, match="refused"):
        G.check_registry("/data/mal/arena/bots.json")
    with pytest.raises(G.Refused, match="refused"):
        G.check_registry("/data/mal/arena/other.json", allow_arena_bots=True)
    assert str(G.check_registry("/data/mal/arena/bots.json", allow_arena_bots=True)) == "/data/mal/arena/bots.json"
    assert G.check_registry("/tmp/r4bt-own-registry.json").name == "r4bt-own-registry.json"


def test_closure_blob_identity_and_name_status_on_this_checkout():
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    cl = G.static_closure(lambda rel: (repo / rel).read_text() if (repo / rel).is_file() else None)
    assert "tools/r4b_live.py" in cl and "tools/arena_paper.py" in cl and not any("r4bt" in x for x in cl)
    r = G.blob_identity(repo, "HEAD", "HEAD", cl)  # the comparison against bc65c088 is a registration-time record: main may move closure files later
    assert r == {"files": len(cl), "identical": True, "differ": [], "missing": []}
    assert G.blob_identity(repo, "HEAD", "HEAD", ["no/such/file.py"])["missing"] == ["no/such/file.py"]
