"""scripts/ops/hv6-p0-count.sh, hv6-smoke.sh, hv6-review-smoke.sh: argument and repo refusals only (the happy paths need the real final sha and the real hosts).
No API call, no key, no tape. The final sha is a REQUIRED environment value (HV6_FINAL_SHA); no script carries a default."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
P0 = ROOT / "scripts" / "ops" / "hv6-p0-count.sh"
SMOKE = ROOT / "scripts" / "ops" / "hv6-smoke.sh"
RSMOKE = ROOT / "scripts" / "ops" / "hv6-review-smoke.sh"
SHA = "b" * 40


def sh(script: Path, *args: str, sha: str | None = SHA):
    env = {k: v for k, v in os.environ.items() if k != "HV6_FINAL_SHA"}
    if sha is not None:
        env["HV6_FINAL_SHA"] = sha
    return subprocess.run(["sh", str(script), *args], capture_output=True, text=True, timeout=60, env=env)


def git_repo(path: Path) -> Path:
    path.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    (path / "f").write_text("1")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "x"], check=True, capture_output=True)
    return path


@pytest.mark.parametrize("script", [P0, SMOKE, RSMOKE])
def test_usage_refused(script):
    r = sh(script)
    assert r.returncode == 2 and "usage" in r.stdout


@pytest.mark.parametrize("script", [P0, SMOKE, RSMOKE])
def test_sha_is_required_and_must_be_40_hex(script):
    assert sh(script, sha=None).returncode == 2 and "HV6_FINAL_SHA_required" in sh(script, sha=None).stdout
    assert "40_hex" in sh(script, sha="abcdef1").stdout
    assert "40_hex" in sh(script, sha="b" * 41).stdout
    assert "[0-9a-f]" in script.read_text() and "5071e29a" not in script.read_text()


def test_p0_refuses_wrong_sha_missing_files_and_dirty(tmp_path):
    repo = git_repo(tmp_path / "r")
    ff = tmp_path / "ff.txt"
    ff.write_text("x")
    r = sh(P0, str(tmp_path / "nope.txt"), "2026-10-22,2026-10-23,2026-10-24", str(repo))
    assert r.returncode == 2 and "fixed_first_file_missing" in r.stdout
    r = sh(P0, str(ff), "2026-10-22,2026-10-23,2026-10-24", str(tmp_path / "norepo"))
    assert r.returncode == 2 and "repo_missing" in r.stdout
    r = sh(P0, str(ff), "2026-10-22,2026-10-23,2026-10-24", str(repo))
    assert r.returncode == 2 and "repo_not_at_final_sha" in r.stdout


def test_smoke_refuses_bad_tag_wrong_sha(tmp_path):
    repo = git_repo(tmp_path / "r")
    for script in (SMOKE, RSMOKE):
        assert "bad_tag" in sh(script, str(repo), "a/b").stdout
        assert "bad_tag" in sh(script, str(repo), "my-probe").stdout
        r = sh(script, str(repo), "ok-tag")
        assert r.returncode == 2 and "repo_not_at_final_sha" in r.stdout


def test_scripts_never_echo_the_key_file():
    for script in (SMOKE, RSMOKE):
        txt = script.read_text()
        assert "cat " not in txt and "echo \"$ANTHROPIC" not in txt and "set -x" not in txt
    assert "MAL_HV4_RPC=helius" in SMOKE.read_text() and "--cap-usd 0.50" in SMOKE.read_text()
