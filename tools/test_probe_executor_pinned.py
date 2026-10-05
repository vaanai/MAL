"""Tests for the root-owned pinned LIVE executor install (installer, launcher, drop-in, module closure).

Nothing here runs as root or touches /usr/local. The installer's root-only steps are checked as text, and its
refusal paths are exercised with fake id/stat/find/systemctl/install on PATH against a throwaway git clone.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAST = ROOT / "scripts/mal-fast"
INSTALL = FAST / "install-probe-executor-pinned.sh"
CONF = FAST / "mal-probe-executor-live-pinned.conf"
LAUNCHER = FAST / "probe_exec_launcher.py"
REQ = FAST / "requirements-probe-exec.txt"
ENTRY = ("tools.probe_executor", "tools.probe_live")
THIRD_PARTY_OK = {"solders"}  # the only non-stdlib import the closure may make


def _tools_imports(mod: str) -> tuple[set[str], set[str]]:
    """(tools.* modules, non-stdlib non-tools top-level names) imported anywhere in the module,
    lazy function-level imports included."""
    tree = ast.parse((ROOT / (mod.replace(".", "/") + ".py")).read_text())
    local: set[str] = set()
    other: set[str] = set()

    def note(name: str) -> None:
        top = name.split(".")[0]
        if top == "tools":
            local.add(name)
        elif top not in sys.stdlib_module_names and top != "__future__":
            other.add(top)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                note(a.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "tools":
                for a in node.names:  # `from tools import x` is the module tools.x
                    local.add(f"tools.{a.name}")
            else:
                note(node.module)
    return local, other


def closure() -> tuple[set[str], set[str]]:
    seen: set[str] = set()
    third: set[str] = set()
    todo = list(ENTRY)
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        local, other = _tools_imports(m)
        third |= other
        todo.extend(local - seen)
    return seen, third


def closure_files() -> list[str]:
    mods, _ = closure()
    return sorted({"tools/__init__.py", *(m.replace(".", "/") + ".py" for m in mods)})


def installer_var(name: str) -> str:
    m = re.search(rf'^{name}="([^"]*)"', INSTALL.read_text(), re.M)
    assert m, name
    return m.group(1)


def test_installed_modules_are_exactly_the_import_closure():
    assert installer_var("MODULES").split() == closure_files()


def test_closure_is_tools_only_plus_solders():
    mods, third = closure()
    assert third <= THIRD_PARTY_OK, third  # an observe.* or other repo import would not be installed
    for m in mods:
        assert (ROOT / (m.replace(".", "/") + ".py")).is_file(), m
    # the lazy imports that used to pull in pump_history_backfill (and observe.*) are gone
    assert "tools.pump_history_backfill" not in mods
    assert not any(m.startswith("tools.test_") for m in mods)


def test_installer_extra_files_exist_in_repo():
    for e in installer_var("EXTRA").split():
        src, dst = e.split(":")
        assert (ROOT / src).is_file() and "/" not in dst
    assert {e.split(":")[1] for e in installer_var("EXTRA").split()} == {
        "launcher.py", "probe-executor-live.json", "requirements-probe-exec.txt"}


def test_requirements_pins_match_tools_requirements_and_are_hashed():
    def pins(p):
        t = p.read_text()
        return re.findall(r"^(\S+==\S+) \\\n\s+--hash=sha256:([0-9a-f]{64})$", t, re.M)

    exec_pins = pins(REQ)
    assert [n.split("==")[0] for n, _ in exec_pins] == ["solders", "jsonalias", "typing_extensions"]
    assert exec_pins == pins(FAST / "requirements-probe-tools.txt")
    assert REQ.read_text().count("--hash=sha256:") == 3


def test_installer_static_guards():
    t = INSTALL.read_text()
    assert os.access(INSTALL, os.X_OK) and subprocess.run(["bash", "-n", str(INSTALL)]).returncode == 0
    assert "core.attributesFile=/dev/null" in t and 'show "$COMMIT:$f"' in t and "archive" not in t
    assert "not root-owned" in t and "group/world-writable" in t and "sha256 mismatch" in t
    assert 'must run as root' in t and '"${#COMMIT}" -eq 40' in t and "clone HEAD is not" in t
    assert "--require-hashes --only-binary=:all:" in t and "--no-deps" in t
    assert 'VENV_NEW="$DEST/venv.$COMMIT.new"' in t and "DEST=/usr/local/lib/mal-probe-exec" in t
    assert "install -m 0644 -o root -g root" in t and "install -d -m 0755 -o root -g root" in t
    assert "already exists" in t and "is active; stop it first" in t
    assert 'mv -T "$DEST/.current.tmp" "$DEST/current"' in t  # atomic pointer swap
    assert "sha256sum" in t
    # the pointer moves only after the smoke import
    assert t.index("import tools.probe_executor, tools.probe_live") < t.index('mv -T "$DEST/.current.tmp"')
    # no group/world-writable result and no 0777-ish mode anywhere
    assert not re.search(r"install -m 0?[0-7]*[2367][0-7]\b", t.replace("0644", "").replace("0755", ""))


def test_dropin_content():
    t = CONF.read_text()
    lines = [ln.strip() for ln in t.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines[0] == "[Service]"
    assert "LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json" in lines
    assert "WorkingDirectory=/usr/local/lib/mal-probe-exec/current" in lines
    assert "ReadOnlyPaths=/usr/local/lib/mal-probe-exec" in lines
    assert "LimitCORE=0" in lines
    i = lines.index("ExecStart=")
    exe = lines[i + 1]
    assert exe == (
        "ExecStart=/usr/local/lib/mal-probe-exec/venv/bin/python -I -B -u "
        "/usr/local/lib/mal-probe-exec/current/launcher.py "
        "--config /usr/local/lib/mal-probe-exec/current/probe-executor-live.json --live")
    assert lines.count("ExecStart=") == 1 and sum(ln.startswith("ExecStart=") for ln in lines) == 2
    # nothing the ubuntu user or agents can write: no fast-forward paths, no PYTHONPATH, no home
    active = "\n".join(lines)
    for bad in ("/var/lib/mal/fast-forward", "ubuntu", "/home", "PYTHONPATH", "/tmp"):
        assert bad not in active, bad
    # -I ignores PYTHON* env, so the launcher must be what puts the pinned dir on sys.path
    assert " -I " in exe


def test_dropin_overrides_no_hardening_of_the_base_unit():
    base = (FAST / "mal-probe-executor.service").read_text()
    drop = {ln.split("=")[0] for ln in CONF.read_text().splitlines() if "=" in ln and not ln.startswith("#")}
    hardening = {"ProtectSystem", "ReadWritePaths", "TemporaryFileSystem", "BindReadOnlyPaths", "InaccessiblePaths",
                 "ProtectHome", "PrivateTmp", "NoNewPrivileges", "CapabilityBoundingSet", "SystemCallFilter",
                 "RestrictAddressFamilies", "PrivateDevices", "ProtectProc", "User", "MemoryMax", "LockPersonality"}
    assert not (drop & hardening)
    for h in hardening:
        assert re.search(rf"^{h}=", base, re.M), h  # still present in the untouched base unit
    assert "mal-probe-executor-live.conf" not in CONF.read_text().split("[Service]")[1]


def test_base_unit_and_current_live_conf_untouched_markers():
    # the currently deployed live.conf still points at the old path (this PR must not change it)
    assert "/var/lib/mal/fast-forward/venv/bin/python -m tools.probe_executor" in (
        FAST / "mal-probe-executor-live.conf").read_text()


def _stage(tmp_path: Path, files: list[str]) -> Path:
    for f in files:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, tmp_path / f)
    return tmp_path


def test_launcher_runs_only_from_its_own_directory(tmp_path):
    root = _stage(tmp_path, closure_files())
    shutil.copy(LAUNCHER, root / "launcher.py")
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT)}  # ignored under -I
    r = subprocess.run([sys.executable, "-I", "-B", "-u", str(root / "launcher.py"), "--help"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "usage" in r.stdout.lower(), r.stderr
    # a module missing from the pinned dir is NOT picked up from the repo (cwd and PYTHONPATH are both the repo)
    (root / "tools/paper_price_path.py").unlink()
    r = subprocess.run([sys.executable, "-I", "-B", "-u", str(root / "launcher.py"), "--help"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "ModuleNotFoundError" in r.stderr
    assert not list(root.rglob("__pycache__"))  # -B: nothing written into the pinned tree


# --- installer refusals, run for real as a non-root user against fakes -----------------------------------

def _run_installer(tmp_path, *args, fake_root=True, tamper=None, mode="755"):
    clone = tmp_path / "clone"
    (clone / "scripts/mal-fast").mkdir(parents=True)
    (clone / "tools").mkdir()
    files = closure_files() + [e.split(":")[0] for e in installer_var("EXTRA").split()]
    for f in files:
        shutil.copy(ROOT / f, clone / f)
    shutil.copy(INSTALL, clone / "scripts/mal-fast/install-probe-executor-pinned.sh")
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(clone)]
    subprocess.run([*g, "init", "-q"], check=True)
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-q", "-m", "x"], check=True)
    sha = subprocess.run([*g, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fakes = {
        "id": '[ "$1" = "-u" ] && echo 0 && exit 0\nexec /usr/bin/id "$@"',
        "stat": f'case "$2" in %a) echo {mode};; %u) echo 0;; *) exec /usr/bin/stat "$@";; esac',
        "find": "exit 0",
        "systemctl": "exit 3",  # not active
        "install": 'echo "FAKE-INSTALL $*"; exit 99',  # never touch the real filesystem
    }
    if not fake_root:
        del fakes["id"]
    for n, body in fakes.items():
        p = bindir / n
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o755)
    manifest = None
    if tamper is not None:
        lines = []
        for f in files:
            h = subprocess.run(["sha256sum", str(clone / f)], capture_output=True, text=True).stdout.split()[0]
            lines.append(f"{h}  {f}")
        manifest = tmp_path / "manifest"
        manifest.write_text("\n".join(tamper(lines)) + "\n")
    cmd = ["bash", str(clone / "scripts/mal-fast/install-probe-executor-pinned.sh"),
           *(a.replace("SHA", sha) for a in args)]
    if manifest:
        cmd.append(str(manifest))
    env = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def test_refuses_non_root(tmp_path):
    r = _run_installer(tmp_path, "SHA", fake_root=False)
    if os.geteuid() != 0:
        assert r.returncode != 0 and "must run as root" in r.stderr


def test_refuses_short_or_uppercase_sha(tmp_path):
    for bad in ("abc123", "A" * 40, "g" * 40):
        r = _run_installer(tmp_path / bad[:3], bad)
        assert r.returncode != 0 and "sha" in r.stderr


def test_refuses_head_mismatch(tmp_path):
    r = _run_installer(tmp_path, "0" * 40)
    assert r.returncode != 0 and "clone HEAD is not" in r.stderr


def test_refuses_group_writable_clone(tmp_path):
    r = _run_installer(tmp_path, "SHA", mode="775")
    assert r.returncode != 0 and "group/world-writable" in r.stderr


def test_manifest_mismatch_refused_before_anything_is_installed(tmp_path):
    def flip(lines):
        h, f = lines[3].split("  ")
        return [*lines[:3], f"{'0' * 64}  {f}", *lines[4:]]

    r = _run_installer(tmp_path, "SHA", tamper=flip)
    assert r.returncode != 0 and "sha256 mismatch" in r.stderr
    assert "FAKE-INSTALL" not in r.stdout and "manifest verified" not in r.stdout


def test_manifest_missing_entry_refused(tmp_path):
    r = _run_installer(tmp_path, "SHA", tamper=lambda lines: lines[:-1])
    assert r.returncode != 0 and "missing manifest entry" in r.stderr


def test_good_manifest_verifies_then_stops_at_the_faked_install(tmp_path):
    r = _run_installer(tmp_path, "SHA", tamper=lambda lines: lines)
    assert "manifest verified" in r.stdout
    assert "FAKE-INSTALL" in r.stdout and r.returncode == 99  # reached the first real install, which is faked


def test_refuses_while_executor_active(tmp_path):
    # same fakes, but systemctl says active: refusal comes after manifest, before any install
    clone_run = _run_installer(tmp_path, "SHA", tamper=lambda lines: lines)  # builds bin/ and clone
    assert "FAKE-INSTALL" in clone_run.stdout
    (tmp_path / "bin/systemctl").write_text("#!/bin/sh\nexit 0\n")
    sh = tmp_path / "clone/scripts/mal-fast/install-probe-executor-pinned.sh"
    sha = subprocess.run(["git", "-C", str(tmp_path / "clone"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    r = subprocess.run(["bash", str(sh), sha], env={"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"},
                       capture_output=True, text=True)
    assert r.returncode != 0 and "is active; stop it first" in r.stderr and "FAKE-INSTALL" not in r.stdout


def test_load_rpc_url_needs_no_observe_import():
    # the live path calls sim.load_rpc_url; it must work with observe.* absent
    code = ("import sys; sys.modules['observe']=None; sys.modules['tools.pump_history_backfill']=None;"
            "from tools import pumpswap_simulate as s; print(s.load_rpc_url('http://x', '/none'));"
            "import os; os.environ['HELIUS_API_KEY']='k'; print(s.load_rpc_url(None, '/none'))")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["http://x", "https://mainnet.helius-rpc.com/?api-key=k"]


def test_installer_clean_git_env_and_failure_safe_staging():
    t = INSTALL.read_text()
    # every git invocation goes through a clean environment
    assert "GITENV=(env -i PATH=/usr/bin:/bin)" in t
    for ln in t.splitlines():
        if re.search(r"(^|[\s(\"])git\s", ln) and not ln.lstrip().startswith(("#", "[", "echo")):
            assert "GITENV" in ln or ln.startswith("G=("), ln
    assert t.count('"${GITENV[@]}" git') == 2
    # EXIT trap removes TMP, the stage dir and the half-built venv
    assert "trap cleanup EXIT" in t and 'rm -rf "$STAGE"' in t and 'rm -rf "$VENV_NEW"' in t
    # new venv is built beside the old one and swapped only after pip and a smoke import
    assert 'VENV_NEW="$DEST/venv.$COMMIT.new"' in t and 'rm -rf "$DEST/venv"' not in t
    assert t.index("pip install") < t.index('mv -T "$VENV_NEW" "$DEST/venv"')
    assert t.index('"$VENV_NEW/bin/python" -I -B -c') < t.index('mv -T "$STAGE"')


def test_runbook_sudo_and_helius_env_note():
    t = (ROOT / "docs/runbooks/probe-executor.md").read_text()
    assert not re.search(r"(^|`)touch /var/lib/mal-live", t, re.M)
    assert "sudo touch /var/lib/mal-live/STOP" in t
    assert "helius.env` is root-owned, mode 0600" in t and "LD_PRELOAD" in t
    assert "install-fast-forward-paper.sh" in t and "live-pinned.conf" in t
