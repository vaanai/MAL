"""scripts/ops/hv6-p0-count.sh and scripts/ops/hv6-smoke.sh: argument and repo refusals only (the happy paths need the real final sha and the real hosts).
No API call, no key, no tape."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
P0 = ROOT / "scripts" / "ops" / "hv6-p0-count.sh"
SMOKE = ROOT / "scripts" / "ops" / "hv6-smoke.sh"
FINAL = "5071e29afe473b6c9ff1f753bf045345e722b0f6"


def sh(script: Path, *args: str, env: dict | None = None):
    return subprocess.run(["sh", str(script), *args], capture_output=True, text=True, timeout=60, env=env)


def git_repo(path: Path) -> Path:
    for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    (path / "f").write_text("1")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "x"], check=True, capture_output=True)
    return path


@pytest.mark.parametrize("script", [P0, SMOKE])
def test_usage_refused(script):
    r = sh(script)
    assert r.returncode == 2 and "usage" in r.stdout


def test_p0_refuses_wrong_sha_missing_files_and_dirty(tmp_path):
    repo = git_repo(tmp_path / "r") if (tmp_path / "r").mkdir() is None else None
    ff = tmp_path / "ff.txt"
    ff.write_text("x")
    r = sh(P0, str(tmp_path / "nope.txt"), "2026-10-22,2026-10-23,2026-10-24", str(repo))
    assert r.returncode == 2 and "fixed_first_file_missing" in r.stdout
    r = sh(P0, str(ff), "2026-10-22,2026-10-23,2026-10-24", str(tmp_path / "norepo"))
    assert r.returncode == 2 and "repo_missing" in r.stdout
    r = sh(P0, str(ff), "2026-10-22,2026-10-23,2026-10-24", str(repo))
    assert r.returncode == 2 and "repo_not_at_final_sha" in r.stdout
    assert FINAL in P0.read_text()


def test_smoke_refuses_bad_tag_wrong_sha(tmp_path):
    (tmp_path / "r").mkdir()
    repo = git_repo(tmp_path / "r")
    assert "bad_tag" in sh(SMOKE, str(repo), "a/b").stdout
    assert "bad_tag" in sh(SMOKE, str(repo), "my-probe").stdout
    r = sh(SMOKE, str(repo), "ok-tag")
    assert r.returncode == 2 and "repo_not_at_final_sha" in r.stdout
    assert FINAL in SMOKE.read_text()


def test_smoke_script_never_echoes_the_key_file():
    txt = SMOKE.read_text()
    assert "cat " not in txt and "echo \"$ANTHROPIC" not in txt and "set -x" not in txt
    assert "MAL_HV4_RPC=helius" in txt and "--cap-usd 0.50" in txt
