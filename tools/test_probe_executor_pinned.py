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


BASE_UNIT = "scripts/mal-fast/mal-probe-executor.service"
CHECKER = "scripts/mal-fast/check-probe-base-unit.py"


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
        "launcher.py", "probe-executor-live.json", "probe-executor-live-dec020.json", "requirements-probe-exec.txt"}


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
    files = closure_files() + [e.split(":")[0] for e in installer_var("EXTRA").split()] + [BASE_UNIT, CHECKER]
    for f in files:
        shutil.copy(ROOT / f, clone / f)
    # The shipped script calls /usr/bin/stat by absolute path (PATH-proof). A non-root test cannot fake that, so the
    # copy under test uses plain `stat` (the PATH fake below); that token is the only difference.
    script = clone / "scripts/mal-fast/install-probe-executor-pinned.sh"
    script.write_text(INSTALL.read_text().replace("/usr/bin/stat", "stat"))
    shutil.copy(CHECK, clone / "scripts/mal-fast/check-probe-exec-tree.sh")
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(clone)]
    subprocess.run([*g, "init", "-q"], check=True)
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-q", "-m", "x"], check=True)
    sha = subprocess.run([*g, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fakes = {
        "id": '[ "$1" = "-u" ] && echo 0 && exit 0\nexec /usr/bin/id "$@"',
        "stat": (f'case "$2" in %a) echo {mode};; %u) echo 0;; '
                 '%u:%g:%a) case "$3" in */helius.env) echo 0:0:600;; *) echo 0:0:700;; esac;; '
                 '*) exec /usr/bin/stat "$@";; esac'),
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
    if tamper is None:
        tamper = lambda lines: lines  # noqa: E731  the manifest is mandatory
    if True:
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
    r = subprocess.run(["bash", str(sh), sha, str(tmp_path / "manifest")], env={"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"},
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
    assert 'VENV_NEW="$DEST/venv.$COMMIT.new"' in t and not re.search(r'^\s*rm -rf "\$DEST/venv"$', t, re.M)
    assert t.index("pip install") < t.index('mv -T "$VENV_NEW" "$DEST/venv"')
    assert t.index('"$VENV_NEW/bin/python" -I -B -c') < t.index('mv -T "$STAGE"')


def test_runbook_sudo_and_helius_env_note():
    t = (ROOT / "docs/runbooks/probe-executor.md").read_text()
    assert not re.search(r"(^|`)touch /var/lib/mal-live", t, re.M)
    assert "sudo touch /var/lib/mal-live/STOP" in t
    assert "holding `helius.env` (`root:root`, mode 0600" in t and "LD_PRELOAD" in t and "/etc/mal-probe-rpc" in t
    assert "install-fast-forward-paper.sh" in t and "live-pinned.conf" in t


# --- permission/symlink check, run for real against a REAL venv (no network, no pip) ----------------------

CHECK = ROOT / "scripts/mal-fast/check-probe-exec-tree.sh"


def _check(*dirs, uid=None):
    env = {"PATH": os.environ["PATH"], "MAL_TREE_CHECK_TEST_UID": str(os.getuid() if uid is None else uid)}
    return subprocess.run(["bash", str(CHECK), *map(str, dirs)], env=env, capture_output=True, text=True)


@pytest.fixture
def real_venv(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("check test mode is refused as root")
    dest = tmp_path / "dest"
    dest.mkdir()
    venv = dest / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    for root, dirs, files in os.walk(dest):  # normalise to 0755/0644-style regardless of the umask
        for n in dirs + files:
            q = Path(root, n)
            if not q.is_symlink():
                q.chmod(q.stat().st_mode & ~0o022)
    venv.chmod(venv.stat().st_mode & ~0o022)
    return dest, venv


def test_check_accepts_real_venv_with_its_symlinks(real_venv):
    dest, venv = real_venv
    links = [p for p in venv.rglob("*") if p.is_symlink()]
    assert links, "a real venv has symlinks (bin/python, lib64, ...)"
    # the old check flagged these (lstat mode 0777); the new one must not
    old = subprocess.run(["find", str(dest), "-perm", "/022"], capture_output=True, text=True).stdout
    assert str(links[0]) in old or any(str(p) in old for p in links)
    r = _check(venv)
    assert r.returncode == 0, r.stderr


def test_check_refuses_symlink_pointing_outside(real_venv, tmp_path):
    dest, venv = real_venv
    outside = tmp_path / "x"
    outside.write_text("evil")
    (venv / "bin" / "evil").symlink_to(outside)
    r = _check(venv)
    assert r.returncode != 0 and "points outside" in r.stderr and "evil" in r.stderr


def test_check_refuses_dangling_symlink_and_writable_file(real_venv):
    dest, venv = real_venv
    (venv / "dangling").symlink_to(venv / "nope")
    r = _check(venv)
    assert r.returncode != 0 and "does not resolve" in r.stderr
    (venv / "dangling").unlink()
    f = venv / "w.py"
    f.write_text("x")
    f.chmod(0o666)
    r = _check(venv)
    assert r.returncode != 0 and "group/world-writable" in r.stderr


def test_check_refuses_wrong_owner(real_venv):
    dest, venv = real_venv
    r = _check(venv, uid=os.getuid() + 1)
    assert r.returncode != 0 and "not owned by uid" in r.stderr


def test_check_fails_closed_when_find_fails(real_venv, tmp_path):
    dest, venv = real_venv
    fb = tmp_path / "fakebin"
    fb.mkdir()
    (fb / "find").write_text("#!/bin/sh\nexit 1\n")
    (fb / "find").chmod(0o755)
    env = {"PATH": f"{fb}:{os.environ['PATH']}", "MAL_TREE_CHECK_TEST_UID": str(os.getuid())}
    r = subprocess.run(["bash", str(CHECK), str(venv)], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "find failed" in r.stderr


def test_installer_moves_all_guarded_by_rollback():
    t = INSTALL.read_text()
    tail = t[t.index('mv -T "$STAGE" "$DEST/$COMMIT"'):t.index("# Atomic switch")]
    n = 0
    for ln in tail.splitlines():
        if re.match(r"\s*(mv -T|chown)\b", ln):
            assert "|| rollback" in ln, ln
            n += 1
    assert n == 5
    # flags are set BEFORE their mv (a signal in the gap must roll back) ...
    assert tail.index("OLD_MOVED=1") < tail.index('mv -T "$DEST/venv" "$DEST/venv.old"')
    assert tail.index("NEW_PLACED=1") < tail.index('mv -T "$VENV_NEW" "$DEST/venv"')
    # ... so rollback tests existence first: the new venv counts as moved only if venv.$COMMIT.new is gone, and the
    # previous venv is restored only if venv.old exists and venv does not
    assert 'if [ "$NEW_PLACED" -eq 1 ] && [ ! -e "$DEST/venv.$COMMIT.new" ]; then NEW_VENV_MOVED=1; fi' in t
    assert 'if [ "$NEW_VENV_MOVED" -eq 1 ] && [ -e "$DEST/venv" ]; then rm -rf "$DEST/venv"' in t
    assert 'if [ "$OLD_MOVED" -eq 1 ] && [ -e "$DEST/venv.old" ] && [ ! -e "$DEST/venv" ]' in t
    assert t.index("NEW_VENV_MOVED=0") < t.index('rm -rf "$DEST/$COMMIT" "$DEST/venv.$COMMIT"')


def test_installer_uses_absolute_stat():
    t = INSTALL.read_text()
    assert t.count("/usr/bin/stat -c") == 4 and not re.search(r"(?<![/\w])stat -c", t)


def test_check_test_mode_refused_as_root():
    t = CHECK.read_text()
    assert 'id -u)" -ne 0' in t and "not allowed as root" in t


def test_installer_checks_before_moves_and_rolls_back():
    t = INSTALL.read_text()
    assert t.index('"$CHECK" "$STAGE" "$VENV_NEW"') < t.index('mv -T "$STAGE" "$DEST/$COMMIT"')
    assert t.index('"$CHECK" "$DEST/$COMMIT" "$DEST/venv"') > t.index('mv -T "$STAGE" "$DEST/$COMMIT"')
    assert t.index('"$CHECK" "$DEST/$COMMIT"') < t.index('mv -T "$DEST/.current.tmp"')
    assert "rollback()" in t and "Remove by hand" in t
    assert 'find "$DEST" ! -user root' not in t


def test_base_unit_and_checker_are_manifest_checked_and_unit_installed_before_pointer_switch():
    t = INSTALL.read_text()
    assert 'BASE_UNIT_SRC="scripts/mal-fast/mal-probe-executor.service"' in t
    assert 'BASE_UNIT_CHECK="scripts/mal-fast/check-probe-base-unit.py"' in t
    assert (ROOT / BASE_UNIT).is_file() and (ROOT / CHECKER).is_file()
    # same git-show + manifest loop as MODULES/EXTRA: both are appended to PATHS before it
    assert t.index('PATHS="$PATHS $BASE_UNIT_SRC $BASE_UNIT_CHECK"') < t.index('"${G[@]}" show "$COMMIT:$f"') < t.index('sha256 mismatch or missing manifest entry')
    # the checker copy from the commit (in $TMP) runs under -I before anything is installed or moved
    run_check = t.index('/usr/bin/python3 -I "$TMP/$BASE_UNIT_CHECK" "$TMP/$BASE_UNIT_SRC"')
    assert run_check < t.index("is-active --quiet")
    # staged as .new, renamed into place and reloaded BEFORE the pointer switch; rollback restores the previous unit
    stage = t.index('install -m 0644 -o root -g root "$TMP/$BASE_UNIT_SRC" "$BASE_UNIT_DEST.new"')
    place = t.index('mv -T "$BASE_UNIT_DEST.new" "$BASE_UNIT_DEST"')
    reload_ = t.index('systemctl daemon-reload || rollback')
    pointer = t.index('mv -T "$DEST/.current.tmp" "$DEST/current"')
    assert stage < place < reload_ < pointer
    assert 'mv -T "$BASE_UNIT_DEST.old" "$BASE_UNIT_DEST"' in t and "UNIT_PLACED" in t
    assert "grep" not in t[t.index("Allowlist check"):t.index("is-active --quiet")]


def test_manifest_argument_is_mandatory(tmp_path):
    _run_installer(tmp_path, "SHA")  # builds the clone and bin/
    clone = tmp_path / "clone"
    sha = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    env = {"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", str(clone / "scripts/mal-fast/install-probe-executor-pinned.sh"), sha], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "manifest argument is mandatory" in r.stderr and "FAKE-INSTALL" not in r.stdout


def _checker():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_probe_base_unit", ROOT / CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_checker_accepts_the_shipped_unit_and_has_the_intended_hardening():
    c = _checker()
    unit = (ROOT / BASE_UNIT).read_text()
    assert c.problems(unit) == []
    assert "EnvironmentFile=/etc/mal-probe-rpc/helius.env" in unit.splitlines()
    assert any(l.startswith("ExecStartPre=+/usr/bin/env -i /bin/sh -c 'test ") for l in unit.splitlines())
    assert "Slice=" not in unit
    assert "fast-listener" not in "\n".join(l for l in unit.splitlines() if not l.startswith("#"))
    assert "ExecStartPre=" + c.PRE in unit.splitlines()
    # EnvironmentFile applies to the "+" command too: env -i and absolute paths keep PATH=/LD_PRELOAD= out of it
    assert c.PRE.startswith("+/usr/bin/env -i /bin/sh -c ") and "/usr/bin/stat -c" in c.PRE and " stat -c" not in c.PRE
    for exe in ("/usr/bin/env", "/usr/bin/stat", "/bin/sh"):
        assert os.path.exists(exe), exe


def _mutations():
    unit = (ROOT / BASE_UNIT).read_text()
    pre = next(l for l in unit.splitlines() if l.startswith("ExecStartPre="))
    return {
        "later User=root": unit.replace("[Install]", "User=root\n[Install]"),
        "later User=root in Service": unit.replace("MemorySwapMax=0", "MemorySwapMax=0\nUser=root"),
        "empty User=": unit.replace("User=mal-live", "User="),
        "Environment spaced LD_": unit.replace("Environment=LANG=C.UTF-8", "Environment=LANG=C.UTF-8\nEnvironment = LD_PRELOAD=/x.so"),
        "continuation": unit.replace("Environment=LANG=C.UTF-8", "Environment=LANG=C.UTF-8 \\\n LD_PRELOAD=/x.so"),
        "continuation of User": unit.replace("User=mal-live", "User=mal-live \\\nroot"),
        "ExecStartPre +/tmp/x": unit.replace(pre, pre + "\nExecStartPre=+/tmp/x"),
        "ExecStartPre replaced": unit.replace(pre, "ExecStartPre=+/tmp/x"),
        "ExecStartPost": unit.replace("Restart=on-failure", "ExecStartPost=/tmp/x\nRestart=on-failure"),
        "ReadWritePaths=/": unit.replace("ReadWritePaths=/var/lib/mal-live", "ReadWritePaths=/"),
        "extra ReadWritePaths": unit.replace("ReadWritePaths=/var/lib/mal-live", "ReadWritePaths=/var/lib/mal-live\nReadWritePaths=/etc"),
        "CapabilityBoundingSet set": unit.replace("CapabilityBoundingSet=\n", "CapabilityBoundingSet=CAP_SYS_ADMIN\n"),
        "CapabilityBoundingSet removed": unit.replace("CapabilityBoundingSet=\n", ""),
        "duplicate Service": unit.replace("[Install]", "[Service]\nUser=root\n[Install]"),
        "unknown section": unit.replace("[Install]", "[Socket]\nListenStream=1\n[Install]"),
        "PassEnvironment": unit.replace("Nice=10", "Nice=10\nPassEnvironment=LD_PRELOAD"),
        "UnsetEnvironment": unit.replace("Nice=10", "Nice=10\nUnsetEnvironment=HOME"),
        "BindPaths": unit.replace("Nice=10", "Nice=10\nBindPaths=/etc"),
        "old ubuntu env file": unit.replace("/etc/mal-probe-rpc/helius.env", "/var/lib/mal/fast-listener/helius.env"),
        "dash env file": unit.replace("EnvironmentFile=/etc", "EnvironmentFile=-/etc"),
        "second EnvironmentFile": unit.replace("Nice=10", "Nice=10\nEnvironmentFile=/tmp/e"),
        "Slice back": unit.replace("Nice=10", "Nice=10\nSlice=mal-forward.slice"),
        "ExecStart changed": unit.replace("-m tools.probe_executor", "-m tools.evil"),
        "line outside section": "User=root\n" + unit,
        "no equals": unit.replace("Nice=10", "Nice=10\ngarbage"),
        "reordered": unit.replace("Type=simple\nUser=mal-live", "User=mal-live\nType=simple"),
    }


def test_checker_refuses_every_bypass():
    c = _checker()
    muts = _mutations()
    assert len(muts) >= 25
    good = (ROOT / BASE_UNIT).read_text()
    for name, text in muts.items():
        assert text != good, name
        assert c.problems(text), f"checker accepted: {name}"


def test_checker_normalises_only_whitespace_around_equals():
    c = _checker()
    unit = (ROOT / BASE_UNIT).read_text()
    assert c.problems(unit.replace("User=mal-live", "  User=mal-live  ")) == []
    assert c.problems(unit.replace("User=mal-live", "User= mal-live")) == []  # systemd strips leading value whitespace
    assert c.problems(unit.replace("User=mal-live", "User =mal-live"))  # systemd: unknown key "User "
    assert c.problems(unit.replace("NoNewPrivileges=true", "NoNewPrivileges =true"))
    assert c.problems(unit.replace("User=mal-live", "User=mal-live2"))


def test_checker_refuses_unicode_whitespace_and_control_bytes():
    c = _checker()
    unit = (ROOT / BASE_UNIT).read_text()
    assert unit.isascii() and "\r" not in unit
    cases = {
        "nbsp in key": unit.replace("User=mal-live", "User\u00a0=mal-live"),
        "nbsp in value": unit.replace("User=mal-live", "User=mal-live\u00a0"),
        "nbsp leading": unit.replace("\nUser=mal-live", "\n\u00a0User=mal-live"),
        "ideographic space": unit.replace("Nice=10", "\u3000Nice=10"),
        "lone CR hides a line": unit.replace("Nice=10", "#x\rNoNewPrivileges=true\nNice=10"),
        "lone CR": unit.replace("Nice=10\n", "Nice=10\r"),
        "CRLF": unit.replace("\n", "\r\n"),
        "VT": unit.replace("Nice=10", "\x0bNice=10"),
        "FF": unit.replace("Nice=10", "Nice=10\x0c"),
        "FS 0x1c": unit.replace("Nice=10", "Nice=10\x1c"),
        "GS 0x1d": unit.replace("Nice=10", "Nice=10\x1d"),
        "RS 0x1e": unit.replace("Nice=10", "Nice=10\x1e"),
        "NEL u0085": unit.replace("Nice=10", "Nice=10\u0085"),
        "NUL": unit.replace("Nice=10", "Nice=10\x00"),
        "BOM": "\ufeff" + unit,
        "U+2028": unit.replace("Nice=10", "Nice=10\u2028User=root"),
    }
    for name, text in cases.items():
        assert text != unit, name
        assert c.problems(text), f"checker accepted: {name}"
    assert c.problems(unit.encode("ascii")) == []
    assert c.problems(unit.encode("ascii").replace(b"Nice=10", b"Nice=10\xc2\xa0"))  # raw bytes input


def _hash_manifest(clone, files):
    import hashlib

    return "\n".join(f"{hashlib.sha256((clone / f).read_bytes()).hexdigest()}  {f}" for f in files) + "\n"


def _bad_unit_run(tmp_path, old, new):
    # build the clone via the normal harness, edit the unit, recommit, regenerate the manifest (so only the checker can refuse)
    r = _run_installer(tmp_path, "SHA", tamper=lambda lines: lines)
    assert "FAKE-INSTALL" in r.stdout
    clone = tmp_path / "clone"
    u = clone / BASE_UNIT
    assert old in u.read_text()
    u.write_text(u.read_text().replace(old, new))
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(clone)]
    subprocess.run([*g, "commit", "-q", "-am", "bad"], check=True)
    sha = subprocess.run([*g, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    files = closure_files() + [e.split(":")[0] for e in installer_var("EXTRA").split()] + [BASE_UNIT, CHECKER]
    (tmp_path / "manifest").write_text(_hash_manifest(clone, files))
    return subprocess.run(["bash", str(clone / "scripts/mal-fast/install-probe-executor-pinned.sh"), sha, str(tmp_path / "manifest")],
                          env={"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}, capture_output=True, text=True)


def test_preflight_refuses_without_root_only_key_dir_and_file(tmp_path):
    _run_installer(tmp_path, "SHA")
    clone = tmp_path / "clone"
    sha = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    env = {"PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}
    for body, want in (('case "$3" in */helius.env) echo 0:0:600;; *) echo 1000:1000:755;; esac', "root:root 0700 directory"),
                       ('case "$3" in */helius.env) echo 1000:1000:644;; *) echo 0:0:700;; esac', "root:root 0600 file")):
        (tmp_path / "bin/stat").write_text('#!/bin/sh\ncase "$2" in %a) echo 755;; %u) echo 0;; %u:%g:%a) ' + body + ';; *) exec /usr/bin/stat "$@";; esac\n')
        r = subprocess.run(["bash", str(clone / "scripts/mal-fast/install-probe-executor-pinned.sh"), sha, str(tmp_path / "manifest")],
                           env=env, capture_output=True, text=True)
        assert r.returncode != 0 and want in r.stderr and "FAKE-INSTALL" not in r.stdout, r.stderr


def test_installer_traps_signals_for_rollback_and_clears_after_pointer_switch():
    t = INSTALL.read_text()
    arm = t.index("trap 'rollback \"interrupted by a signal\"' INT TERM HUP")
    assert arm < t.index('mv -T "$STAGE" "$DEST/$COMMIT"')
    assert t.index('mv -T "$DEST/.current.tmp" "$DEST/current"') < t.index("trap - INT TERM HUP")
    assert "trap '' INT TERM HUP" in t  # rollback itself is not interruptible
    assert t.index("stat -c %u:%g:%a") < t.index("is-active --quiet")  # pre-flight before anything is moved


def test_installer_runs_checker_and_refuses_bad_unit_before_any_install(tmp_path):
    cases = [
        ("User=mal-live", "User=root"),
        ("Environment=LC_ALL=C.UTF-8", "Environment = LD_PRELOAD=/x.so"),
        ("User=mal-live", "User =mal-live"),
        ("EnvironmentFile=/etc/mal-probe-rpc/helius.env", "EnvironmentFile=/var/lib/mal/fast-listener/helius.env"),
    ]
    for i, (old, new) in enumerate(cases):
        r = _bad_unit_run(tmp_path / str(i), old, new)
        assert r.returncode != 0 and "failed the allowlist check" in r.stderr and "FAKE-INSTALL" not in r.stdout, (old, r.stderr)
        assert "manifest verified" in r.stdout


def test_tampered_checker_refused_by_manifest(tmp_path):
    r = _run_installer(tmp_path, "SHA", tamper=lambda lines: [f"{'0' * 64}  {l.split('  ')[1]}" if l.endswith(CHECKER) else l for l in lines])
    assert r.returncode != 0 and "sha256 mismatch" in r.stderr and "FAKE-INSTALL" not in r.stdout


def test_withdraw_default_rpc_env_is_the_root_only_file():
    from tools import probe_withdraw

    assert probe_withdraw.DEFAULT_RPC_ENV == "/etc/mal-probe-rpc/helius.env"


def test_dec020_pinned_set_and_drop_in():
    """DEC-020 re-pin: the manifest grows from 13 to 14 lines (one added file); the dec020 drop-in differs from the
    current pinned drop-in only in the --config path (and its header comment)."""
    extra = [e.split(":")[0] for e in installer_var("EXTRA").split()]
    assert "scripts/mal-fast/probe-executor-live-dec020.json" in extra
    assert "scripts/mal-fast/probe-executor-live.json" in extra  # the current config is still pinned unchanged
    assert len(closure_files()) + len(extra) + 2 == 14  # + BASE_UNIT and CHECKER
    d20 = FAST / "mal-probe-executor-live-pinned-dec020.conf"
    a = [l for l in CONF.read_text().splitlines() if not l.startswith("#")]
    b = [l for l in d20.read_text().splitlines() if not l.startswith("#")]
    assert len(a) == len(b)
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    assert len(diff) == 1 and diff[0][0].startswith("ExecStart=/usr/local/lib/mal-probe-exec/venv/bin/python")
    assert diff[0][1] == diff[0][0].replace("current/probe-executor-live.json", "current/probe-executor-live-dec020.json")
    assert d20.read_text().count("--live") >= 1 and "probe-executor-live-dec020.json --live" in b[-2]
