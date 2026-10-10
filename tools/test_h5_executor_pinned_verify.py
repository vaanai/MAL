"""The H5 installer's own hash verification (installed_name + verify_tree), run for real on a staged tree. The fake-install tests in
test_h5_executor_pinned.py stop at the first `install`, before this code, so it is exercised here by extracting the functions from
the script text and running them in bash against a tree laid out the way the installer stages it."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from tools.test_h5_executor_pinned import BASE_UNIT, CHECKER, EXP024, INSTALL, all_files, installer_var, src_bytes


def stage_like_the_installer(tmp_path: Path) -> tuple[Path, Path]:
    stage = tmp_path / "stage"
    for f in installer_var("MODULES").split():
        (stage / f).parent.mkdir(parents=True, exist_ok=True)
        (stage / f).write_bytes(src_bytes(f))
    for e in installer_var("EXTRA").split():
        src, dst = e.split(":")
        (stage / dst).parent.mkdir(parents=True, exist_ok=True)
        (stage / dst).write_bytes(src_bytes(src))
    (stage / "mal-h5-executor.service").write_bytes(src_bytes(BASE_UNIT))
    (stage / "check-h5-unit.py").write_bytes(src_bytes(CHECKER))
    manifest = tmp_path / "manifest"
    manifest.write_text("".join(f"{hashlib.sha256(src_bytes(f)).hexdigest()}  {f}\n" for f in all_files()))
    return stage, manifest


def verify(stage: Path, manifest: Path, mode: str) -> subprocess.CompletedProcess:
    t = INSTALL.read_text()
    funcs = t[t.index("# installed file for a repo path"):t.index('verify_tree "$STAGE" check ||')]
    script = "\n".join([
        "set -euo pipefail",
        f'MODULES="{installer_var("MODULES")}"', f'EXTRA="{installer_var("EXTRA")}"',
        f'BASE_UNIT_SRC="{installer_var("BASE_UNIT_SRC")}"', f'BASE_UNIT_CHECK="{installer_var("BASE_UNIT_CHECK")}"',
        f'MANIFEST="{manifest}"', f'PATHS="{" ".join(all_files())}"', funcs,
        f'verify_tree "{stage}" {mode} || exit 1'])  # called as the installer calls it: under `||`, where set -e is off
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_verify_tree_accepts_the_staged_layout_and_prints_manifest_format(tmp_path):
    stage, manifest = stage_like_the_installer(tmp_path)
    assert verify(stage, manifest, "check").returncode == 0
    r = verify(stage, manifest, "print")
    assert r.returncode == 0

    def key(line: str) -> str:
        return line.split("  ")[1]

    assert sorted(r.stdout.splitlines(), key=key) == sorted(manifest.read_text().splitlines(), key=key)
    for rel in (EXP024, "launcher.py", "mal-h5-executor.service", "check-h5-unit.py", "h5-executor-live.json", "tools/h5_sell_and_close.py"):
        assert (stage / rel).is_file(), rel


def test_verify_tree_refuses_a_changed_or_a_missing_file(tmp_path):
    muts = {
        "changed module": lambda s: (s / "tools/probe_live.py").write_text((s / "tools/probe_live.py").read_text() + "\n# x\n"),
        "changed renamed extra": lambda s: (s / "launcher.py").write_text("import os\n"),
        "changed drop-in copy": lambda s: (s / "mal-h5-executor-live-pinned.conf").write_text("[Service]\n"),
        "changed unit copy": lambda s: (s / "mal-h5-executor.service").write_text("[Service]\n"),
        "changed checker copy": lambda s: (s / "check-h5-unit.py").write_text("import sys\nsys.exit(0)\n"),
        "changed exp024": lambda s: (s / EXP024).write_text("x"),
        "missing file": lambda s: (s / "tools/h5_sell_and_close.py").unlink(),
    }
    for name, mutate in muts.items():
        stage, manifest = stage_like_the_installer(tmp_path / name.replace(" ", "_"))
        mutate(stage)
        r = verify(stage, manifest, "check")
        assert r.returncode == 1 and "differs from its manifest entry" in r.stderr, name
