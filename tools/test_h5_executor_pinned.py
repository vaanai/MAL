"""Tests for the root-owned pinned H5 executor install (installer, launcher, units, checkers, manifest helper, module closure).

Same method as tools/test_probe_executor_pinned.py. Nothing here runs as root or touches /usr/local: the installer's root-only
steps are checked as text, and its refusal paths are exercised with fake id/stat/find/systemctl/install on PATH against a
throwaway git clone. NOT verified here (no host): the real install, systemd's reading of the units, the ProtectHome=tmpfs bind.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAST = ROOT / "scripts/mal-fast"
INSTALL = FAST / "install-h5-executor-pinned.sh"
MAKE_MANIFEST = FAST / "make-h5-manifest.sh"
CHECK_TREE = FAST / "check-h5-exec-tree.sh"
UNIT = FAST / "mal-h5-executor.service"
DROPIN = FAST / "mal-h5-executor-live-pinned.conf"
FEED = FAST / "mal-h5-executor-shadow-feed.conf"
LAUNCHER = FAST / "h5_exec_launcher.py"
REQ = FAST / "requirements-probe-exec.txt"
ENTRY = ("tools.h5_executor", "tools.h5_sell_and_close")
THIRD_PARTY_OK = {"solders"}
BASE_UNIT = "scripts/mal-fast/mal-h5-executor.service"
CHECKER = "scripts/mal-fast/check-h5-unit.py"
EXP024 = "EXP/EXP-024-h5-boostfloor-part1-prereg.md"
PINNED = "/usr/local/lib/mal-h5-exec"


def _tools_imports(mod: str) -> tuple[set[str], set[str]]:
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
                for a in node.names:
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


def extra_srcs() -> list[str]:
    return [e.split(":")[0] for e in installer_var("EXTRA").split()]


def all_files() -> list[str]:
    return closure_files() + extra_srcs() + [BASE_UNIT, CHECKER]


def src_bytes(f: str) -> bytes:
    p = ROOT / f
    if f == EXP024 and not p.exists():
        return b"# EXP-024 stand-in for this test (the real file lands from main)\n"
    return p.read_bytes()


def _checker():
    spec = importlib.util.spec_from_file_location("check_h5_unit", FAST / "check-h5-unit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- what is installed -----------------------------------------------------------------------------------------------------

def test_installed_modules_are_exactly_the_import_closure():
    assert installer_var("MODULES").split() == closure_files()


def test_closure_is_tools_only_plus_solders_and_has_both_entries():
    mods, third = closure()
    assert third <= THIRD_PARTY_OK, third
    assert {"tools.h5_executor", "tools.h5_sell_and_close", "tools.probe_live", "tools.probe_withdraw"} <= mods
    assert not any(m.startswith("tools.test_") or m == "tools.h5_reconcile" for m in mods)
    for m in mods:
        assert (ROOT / (m.replace(".", "/") + ".py")).is_file(), m


def test_extra_files_and_installed_names():
    pairs = [e.split(":") for e in installer_var("EXTRA").split()]
    for src, dst in pairs:
        assert src == EXP024 or (ROOT / src).is_file(), src  # EXP-024 lands from main; the installer refuses a sha without it
    assert {d for _, d in pairs} == {"launcher.py", "h5-executor-live.json", "h5-executor.json", "mal-h5-executor-live-pinned.conf",
                                     "mal-h5-executor-shadow-feed.conf", "requirements-probe-exec.txt", EXP024,
                                     "h5-watch.py", "h5-daily-check.py", "mal-h5-watch.service", "mal-h5-watch.timer"}
    # exp024_part1_present() looks for <repo_root>/EXP/EXP-024-...; repo_root() is the sha dir, so that exact relative path is installed
    from tools import h5_executor

    assert h5_executor.EXP024_PART1 == EXP024 and dict(pairs)[EXP024] == EXP024


def test_requirements_are_the_probe_hashed_pins():
    assert REQ.read_text().count("--hash=sha256:") == 3
    assert [n.split("==")[0] for n in re.findall(r"^(\S+==\S+) \\", REQ.read_text(), re.M)] == ["solders", "jsonalias", "typing_extensions"]


def test_dest_is_separate_from_the_probe_pin():
    t = INSTALL.read_text()
    assert "DEST=/usr/local/lib/mal-h5-exec" in t and "/usr/local/lib/mal-probe-exec" not in t


# --- units and drop-in -----------------------------------------------------------------------------------------------------

def test_key_arrives_only_through_the_live_dropin():
    base = "\n".join(l for l in UNIT.read_text().splitlines() if not l.lstrip().startswith("#"))
    assert "LoadCredential" not in base and "--live" not in base and "probe-wallet" not in base
    d =[ln.strip() for ln in DROPIN.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert d[0] == "[Service]" and "LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json" in d
    assert d.index("ExecStart=") + 1 == len(d) - 1
    assert d[-1] == (f"ExecStart={PINNED}/venv/bin/python -I -B -u {PINNED}/current/launcher.py "
                     f"--config {PINNED}/current/h5-executor-live.json --live")
    active = "\n".join(d)
    for bad in ("/var/lib/mal/fast-forward", "ubuntu", "/home", "PYTHONPATH", "/tmp", "-m tools"):
        assert bad not in active, bad


def test_dropin_overrides_no_hardening_of_the_base_unit():
    base = UNIT.read_text()
    drop = {ln.split("=")[0] for ln in DROPIN.read_text().splitlines() if "=" in ln and not ln.startswith("#")}
    assert drop == {"LoadCredential", "ExecStart"}
    for h in ("ProtectSystem", "ReadWritePaths", "TemporaryFileSystem", "ProtectHome", "PrivateTmp", "NoNewPrivileges", "CapabilityBoundingSet",
              "SystemCallFilter", "RestrictAddressFamilies", "PrivateDevices", "ProtectProc", "User", "MemoryMax", "LockPersonality", "LimitCORE"):
        assert re.search(rf"^{h}=", base, re.M), h


def test_base_unit_matches_the_probe_hardening_and_the_brief():
    t = UNIT.read_text()
    probe = (FAST / "mal-probe-executor.service").read_text()
    for key in ("NoNewPrivileges", "ProtectSystem", "PrivateTmp", "ProtectKernelTunables", "ProtectKernelModules", "ProtectControlGroups",
                "RestrictSUIDSGID", "LockPersonality", "RestrictAddressFamilies", "CapabilityBoundingSet", "PrivateDevices", "ProtectProc",
                "SystemCallFilter", "MemoryMax", "MemorySwapMax", "EnvironmentFile", "User"):
        a = re.search(rf"^{key}=(.*)$", t, re.M)
        b = re.search(rf"^{key}=(.*)$", probe, re.M)
        assert a and b and a.group(1) == b.group(1), key
    assert re.findall(r"^ReadWritePaths=(.*)$", t, re.M) == ["/var/lib/mal-live/h5"]  # nothing else writable
    assert "MemoryMax=1G" in t and "User=mal-live" in t and "EnvironmentFile=/etc/mal-probe-rpc/helius.env" in t
    assert "Conflicts=mal-probe-executor.service" in t  # two processes never share the key
    assert re.search(r"^ExecStartPre=\+/usr/bin/env -i /bin/sh -c 'test ", t, re.M)
    assert "fast-listener" not in "\n".join(l for l in t.splitlines() if not l.startswith("#"))  # the ubuntu-writable env file is never used


def test_pinned_configs_agree_with_the_unit_and_the_feed_template():
    live = json.loads((FAST / "h5-executor-live.json").read_text())
    dry = json.loads((FAST / "h5-executor.json").read_text())
    assert live["mode"] == "live" and dry["mode"] == "dryrun"
    assert live["state_dir"] == dry["state_dir"] == "/var/lib/mal-live/h5"
    assert live["intents_file"] == dry["intents_file"] == "/srv/mal-h5-shadow"
    assert FEED.read_text().rstrip().endswith(":/srv/mal-h5-shadow")
    assert "key_path" not in live and "pick_file" not in live and live["stake_lamports"] == 20_000_000


def test_unit_checker_accepts_the_shipped_files():
    c = _checker()
    assert c.problems(UNIT.read_bytes(), "base") == []
    assert c.problems(DROPIN.read_bytes(), "dropin") == []
    ok = FEED.read_text().replace("__SHADOW_DIR__", "/home/claude/data/h5-shadow")
    assert c.problems(ok, "shadow-feed") == []
    assert c.problems(FEED.read_text(), "shadow-feed")  # the unfilled template is refused on purpose
    for exe in ("/usr/bin/env", "/usr/bin/stat", "/bin/sh"):
        assert os.path.exists(exe)


def _base_mutations():
    unit = UNIT.read_text()
    pre = next(l for l in unit.splitlines() if l.startswith("ExecStartPre="))
    return {
        "later User=root": unit.replace("MemorySwapMax=0", "MemorySwapMax=0\nUser=root"),
        "empty User=": unit.replace("User=mal-live", "User="),
        "Environment LD_": unit.replace("Environment=LANG=C.UTF-8", "Environment=LANG=C.UTF-8\nEnvironment = LD_PRELOAD=/x.so"),
        "continuation": unit.replace("Environment=LANG=C.UTF-8", "Environment=LANG=C.UTF-8 \\\n LD_PRELOAD=/x.so"),
        "ExecStartPre extra": unit.replace(pre, pre + "\nExecStartPre=+/tmp/x"),
        "ExecStartPost": unit.replace("Restart=on-failure", "ExecStartPost=/tmp/x\nRestart=on-failure"),
        "ReadWritePaths=/": unit.replace("ReadWritePaths=/var/lib/mal-live/h5", "ReadWritePaths=/"),
        "ReadWritePaths probe dir": unit.replace("ReadWritePaths=/var/lib/mal-live/h5", "ReadWritePaths=/var/lib/mal-live"),
        "extra ReadWritePaths": unit.replace("ReadWritePaths=/var/lib/mal-live/h5", "ReadWritePaths=/var/lib/mal-live/h5\nReadWritePaths=/etc"),
        "LoadCredential in base": unit.replace("Nice=10", "Nice=10\nLoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json"),
        "--live in base": unit.replace("h5-executor.json", "h5-executor-live.json --live"),
        "ExecStart repo path": unit.replace(f"{PINNED}/venv/bin/python -I -B -u {PINNED}/current/launcher.py", "/var/lib/mal/fast-forward/venv/bin/python -m tools.h5_executor"),
        "ProtectHome true": unit.replace("ProtectHome=tmpfs", "ProtectHome=false"),
        "Conflicts removed": unit.replace("Conflicts=mal-probe-executor.service\n", ""),
        "CapabilityBoundingSet set": unit.replace("CapabilityBoundingSet=\n", "CapabilityBoundingSet=CAP_SYS_ADMIN\n"),
        "MemoryMax raised": unit.replace("MemoryMax=1G", "MemoryMax=infinity"),
        "ubuntu env file": unit.replace("/etc/mal-probe-rpc/helius.env", "/var/lib/mal/fast-listener/helius.env"),
        "dash env file": unit.replace("EnvironmentFile=/etc", "EnvironmentFile=-/etc"),
        "second EnvironmentFile": unit.replace("Nice=10", "Nice=10\nEnvironmentFile=/tmp/e"),
        "BindPaths": unit.replace("Nice=10", "Nice=10\nBindPaths=/etc"),
        "duplicate Service": unit.replace("[Install]", "[Service]\nUser=root\n[Install]"),
        "unknown section": unit.replace("[Install]", "[Socket]\nListenStream=1\n[Install]"),
        "line outside section": "User=root\n" + unit,
        "reordered": unit.replace("Type=simple\nUser=mal-live", "User=mal-live\nType=simple"),
        "key spaced": unit.replace("User=mal-live", "User =mal-live"),
    }


def test_checker_refuses_every_base_unit_bypass():
    c = _checker()
    good = UNIT.read_text()
    muts = _base_mutations()
    assert len(muts) >= 20
    for name, text in muts.items():
        assert text != good, name
        assert c.problems(text, "base"), f"checker accepted: {name}"


def test_checker_refuses_every_dropin_bypass():
    c = _checker()
    d = DROPIN.read_text()
    muts = {
        "second credential": d + "LoadCredential=other:/etc/shadow\n",
        "other credential path": d.replace("/etc/mal-probe/probe-wallet.json", "/etc/shadow"),
        "ImportCredential": d + "ImportCredential=x\n",
        "no --live": d.replace(" --live", ""),
        "dry config": d.replace("h5-executor-live.json", "h5-executor.json"),
        "dev copy": d.replace(f"{PINNED}/current/h5-executor-live.json", "/var/lib/mal/fast-forward/src/scripts/mal-fast/h5-executor-live.json"),
        "User": d + "User=root\n",
        "Environment": d + "Environment=LD_PRELOAD=/x.so\n",
        "ExecStartPre": d + "ExecStartPre=+/tmp/x\n",
        "no reset": d.replace("ExecStart=\n", ""),
        "ReadWritePaths": d + "ReadWritePaths=/etc\n",
        "no credential": d.replace("LoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json\n", ""),
        "nbsp": d.replace("[Service]", "[Service] "),
        "crlf": d.replace("\n", "\r\n"),
    }
    for name, text in muts.items():
        assert text != d, name
        assert c.problems(text, "dropin"), f"checker accepted: {name}"


def test_checker_shadow_feed_accepts_only_a_plain_home_h5_shadow_bind():
    c = _checker()

    def feed(line, head="[Service]\n"):
        return head + line + "\n"

    dst = ":/srv/mal-h5-shadow"
    assert c.problems(feed(f"BindReadOnlyPaths=-/home/claude/data/h5-shadow{dst}"), "shadow-feed") == []
    assert c.problems(feed(f"BindReadOnlyPaths=-/home/ubuntu/work/runs/h5-shadow-2{dst}"), "shadow-feed") == []
    bad = [
        f"BindReadOnlyPaths=-/etc{dst}", f"BindReadOnlyPaths=-/etc/mal-probe{dst}", f"BindReadOnlyPaths=-/var/lib/mal-live{dst}",
        f"BindReadOnlyPaths=-/home/claude/.ssh/h5-shadow{dst}", f"BindReadOnlyPaths=-/home/claude/../../etc/h5-shadow{dst}",
        f"BindReadOnlyPaths=-/home/claude{dst}", f"BindReadOnlyPaths=-/home/claude/data/h5-shadow:/srv/other",
        f"BindReadOnlyPaths=-/home/claude/data/h5-shadow{dst} /etc", f"BindReadOnlyPaths=/home/claude/data/h5-shadow{dst}",
        f"BindPaths=-/home/claude/data/h5-shadow{dst}", f"BindReadOnlyPaths=-/home/claude/data/h5-shadow:/srv/mal-h5-shadow:norbind",
        "BindReadOnlyPaths=-/home/claude/data/h5-shadow", f"BindReadOnlyPaths=-/home/claude/x y/h5-shadow{dst}",
        f"BindReadOnlyPaths=-/home/claude/data/h5-shadow{dst}\nBindReadOnlyPaths=-/etc:/srv/x",
        f"BindReadOnlyPaths=-/home/claude/data/h5-shadow{dst}\nUser=root", f"BindReadOnlyPaths=-/home/claude/data/h5-shadow{dst} \\\n -/etc",
    ]
    for line in bad:
        assert c.problems(feed(line), "shadow-feed"), line
    assert c.problems(feed(f"BindReadOnlyPaths=-/home/claude/data/h5-shadow{dst}", "[Unit]\n"), "shadow-feed")
    assert c.problems(b"", "shadow-feed")


def test_checker_cli_modes(tmp_path):
    def run(mode, f):
        return subprocess.run([sys.executable, "-I", str(FAST / "check-h5-unit.py"), mode, str(f)], capture_output=True, text=True)

    assert run("--base", UNIT).returncode == 0 and run("--dropin", DROPIN).returncode == 0
    assert run("--dropin", UNIT).returncode == 1 and run("--base", DROPIN).returncode == 1
    assert subprocess.run([sys.executable, "-I", str(FAST / "check-h5-unit.py")], capture_output=True).returncode == 2


# --- launcher --------------------------------------------------------------------------------------------------------------

def _stage(tmp_path: Path) -> Path:
    for f in closure_files():
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, tmp_path / f)
    shutil.copy(LAUNCHER, tmp_path / "launcher.py")
    return tmp_path


def _launch(root: Path, *args):
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT)}  # ignored under -I
    return subprocess.run([sys.executable, "-I", "-B", "-u", str(root / "launcher.py"), *args], cwd=ROOT, env=env, capture_output=True, text=True)


def test_launcher_runs_the_executor_only_from_its_own_directory(tmp_path):
    root = _stage(tmp_path)
    r = _launch(root, "--help")
    assert r.returncode == 0 and "H5-BOOSTFLOOR executor" in r.stdout, r.stderr
    (root / "tools/paper_price_path.py").unlink()  # a module missing from the pinned dir is NOT taken from the repo (cwd and PYTHONPATH are the repo)
    r = _launch(root, "--help")
    assert r.returncode != 0 and "ModuleNotFoundError" in r.stderr
    assert not list(root.rglob("__pycache__"))


def test_launcher_run_tool_is_a_whitelist_and_first_argument_only(tmp_path):
    root = _stage(tmp_path)
    r = _launch(root, "--run-tool", "sell_and_close", "--help")
    assert r.returncode == 0 and "--emergency" in r.stdout and "--send" in r.stdout, r.stderr
    for args in (("--run-tool", "probe_withdraw"), ("--run-tool", "../x"), ("--run-tool",), ("--run-tool", "h5_executor")):
        r = _launch(root, *args)
        assert r.returncode != 0 and "--run-tool needs one of: sell_and_close" in r.stderr, args
    r = _launch(root, "--help", "--run-tool", "sell_and_close")  # not first: ordinary executor args, which argparse rejects or serves; never the tool
    assert "H5-BOOSTFLOOR executor" in r.stdout


# --- installer: static guards ----------------------------------------------------------------------------------------------

def test_installer_static_guards():
    t = INSTALL.read_text()
    assert os.access(INSTALL, os.X_OK) and subprocess.run(["bash", "-n", str(INSTALL)]).returncode == 0
    assert "core.attributesFile=/dev/null" in t and 'show "$COMMIT:$f"' in t and "archive" not in t
    assert "not root-owned" in t and "group/world-writable" in t and "sha256 mismatch" in t
    assert "must run as root" in t and '"${#COMMIT}" -eq 40' in t and "clone HEAD is not" in t
    assert "--require-hashes --only-binary=:all:" in t and "--no-deps" in t
    assert 'VENV_NEW="$DEST/venv.$COMMIT.new"' in t
    assert "install -m 0644 -o root -g root" in t and "install -d -m 0755 -o root -g root" in t
    assert 'mv -T "$DEST/.current.tmp" "$DEST/current"' in t and "already exists" in t
    assert t.index("import tools.h5_executor, tools.h5_sell_and_close") < t.index('mv -T "$DEST/.current.tmp"')
    assert not re.search(r"install -m 0?[0-7]*[2367][0-7]\b", t.replace("0644", "").replace("0755", ""))
    # the live drop-in is never installed here: its directory is named once (LIVE_DROPIN, which is only tested for and echoed)
    assert "mal-h5-executor-live-pinned.conf" in t and t.count("/etc/systemd/system/mal-h5-executor.service.d") == 1
    assert all(re.search(r'-[eL] "\$LIVE_DROPIN"', l) or l.strip().startswith("echo ") for l in t.splitlines() if "$LIVE_DROPIN" in l and not l.lstrip().startswith("#"))
    assert "LoadCredential" not in t


def test_installer_uses_absolute_stat_and_clean_git_env():
    t = INSTALL.read_text()
    assert t.count("/usr/bin/stat -c") == 5 and not re.search(r"(?<![/\w])stat -c", t)  # check_dir x2, /etc/mal-probe-rpc x2, /etc/mal-h5
    assert "GITENV=(env -i PATH=/usr/bin:/bin)" in t
    for ln in t.splitlines():
        if re.search(r"(^|[\s(\"])git\s", ln) and not ln.lstrip().startswith(("#", "[", "echo")):
            assert "GITENV" in ln or ln.startswith("G=("), ln


def test_installer_checks_clone_state_then_manifest_then_units_then_preflight_then_moves():
    t = INSTALL.read_text()
    order = ["clone HEAD is not", "the clone is dirty", 'show "$COMMIT:$f"', "sha256 mismatch or missing manifest entry", "manifest verified",
             '--base "$TMP/$BASE_UNIT_SRC"', '--dropin "$TMP/$DROPIN_SRC"', '--watch-service "$TMP/$WATCH_SERVICE_SRC"', '--watch-timer "$TMP/$WATCH_TIMER_SRC"',
             'stat -c %u:%g:%a "$RPC_DIR"', 'unit_stopped "$UNIT"', 'unit_stopped "$PROBE_UNIT"', "is-enabled", '"$(/usr/bin/stat -c %u:%g:%a "$H5_ETC")" != "0:0:755"', '-e "$H5_ETC/LIVE_OK"', '-e "$LIVE_DROPIN"',
             'install -d -m 0755 -o root -g root "$H5_ETC"', 'mv -T "$STAGE" "$DEST/$COMMIT"']
    idx = [t.index(s) for s in order]
    assert idx == sorted(idx), dict(zip(order, idx))
    # LIVE_OK is Helm's, after the hash check: the installer only ever tests for it (and says so), never creates, touches or removes it
    assert "H5_ETC=/etc/mal-h5" in t
    code = [l.strip() for l in t.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    for l in code:
        if "LIVE_OK" in l and not l.startswith("echo "):
            assert not re.search(r"^(touch|rm|install|cp|mv|ln|tee|chown|chmod)\b|\s>\s*\S*LIVE_OK|:\s*>", l), l  # only tests (-e / -L) mention it
        if "$H5_ETC" in l:
            assert not re.match(r"(touch|rm|cp|mv|ln|tee)\b", l), l
    assert [l for l in code if l.startswith("install") and "$H5_ETC" in l] == ['install -d -m 0755 -o root -g root "$H5_ETC"']  # the directory, nothing in it
    assert "grep" not in t[t.index("Allowlist check"):t.index("unit_stopped()")]
    assert not any("is-active" in l for l in code)  # only `show -p ActiveState --value` is trusted (a comment may explain why)
    assert 'case "$(systemctl show -p ActiveState --value "$1" 2>/dev/null)" in inactive|failed) return 0 ;; *) return 1 ;; esac' in t
    assert "status --porcelain --untracked-files=all --ignored" in t


def test_installer_moves_all_guarded_by_rollback_and_hash_verified_before_pointer():
    t = INSTALL.read_text()
    tail = t[t.index('mv -T "$STAGE" "$DEST/$COMMIT"'):t.index("# Atomic switch")]
    n = 0
    for ln in tail.splitlines():
        if re.match(r"\s*(mv -T|chown)\b", ln):
            assert "|| rollback" in ln, ln
            n += 1
    assert n == 5
    assert tail.index("OLD_MOVED=1") < tail.index('mv -T "$DEST/venv" "$DEST/venv.old"')
    assert tail.index("NEW_PLACED=1") < tail.index('mv -T "$VENV_NEW" "$DEST/venv"')
    assert 'verify_tree "$DEST/$COMMIT" check || rollback' in tail
    assert t.index('verify_tree "$STAGE" check') < t.index('mv -T "$STAGE"')  # the staged tree is hashed before anything moves
    assert t.index('"$CHECK" "$STAGE" "$VENV_NEW"') < t.index('mv -T "$STAGE" "$DEST/$COMMIT"') < t.index('"$CHECK" "$DEST/$COMMIT" "$DEST/venv"')
    assert t.index("UNIT_PLACED=1") < t.index('mv -T "$BASE_UNIT_DEST.new" "$BASE_UNIT_DEST"') < t.index("systemctl daemon-reload || rollback") < t.index('mv -T "$DEST/.current.tmp"')
    arm = t.index("trap 'rollback \"interrupted by a signal\"' INT TERM HUP")
    assert arm < t.index('mv -T "$STAGE" "$DEST/$COMMIT"') and t.index('mv -T "$DEST/.current.tmp" "$DEST/current"') < t.index("trap - INT TERM HUP")
    assert "trap '' INT TERM HUP" in t and "Remove by hand" in t
    assert 'if [ "$NEW_PLACED" -eq 1 ] && [ ! -e "$DEST/venv.$COMMIT.new" ]; then NEW_VENV_MOVED=1; fi' in t


def test_installer_prints_a_manifest_format_table_and_the_end_ms_note():
    t = INSTALL.read_text()
    assert "BEGIN-MANIFEST" in t and "END-MANIFEST" in t and 'verify_tree "$DEST/$COMMIT" print | sort -k2' in t
    assert "has no end_ms" in t and "end_ms_missing" in t


# --- installer: refusals, run for real as a non-root user against fakes ---------------------------------------------------

def _git(clone, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(clone), *a], capture_output=True, text=True, check=True).stdout.strip()


def _manifest(clone: Path, files: list[str]) -> str:
    import hashlib

    return "\n".join(f"{hashlib.sha256((clone / f).read_bytes()).hexdigest()}  {f}" for f in files) + "\n"


def fake_systemctl(h5="inactive", probe="inactive", enabled=None) -> str:
    """`systemctl show -p ActiveState --value <unit>` answers h5/probe (an empty string is an empty answer); `is-enabled` prints `enabled`
    (exit 0) or nothing (exit 3); daemon-reload succeeds; anything else fails."""
    return ('if [ "$1" = show ]; then case "$5" in mal-h5-executor) echo "' + h5 + '";; mal-probe-executor) echo "' + probe + '";; esac; exit 0; fi\n'
            'if [ "$1" = is-enabled ] && [ "$2" = mal-probe-executor ]; then ' + (f'echo {enabled}; exit 0' if enabled else 'exit 3') + '; fi\n'
            '[ "$1" = daemon-reload ] && exit 0\nexit 3')


FAKE_SYSTEMCTL = fake_systemctl()  # both units inactive, probe unit not enabled


def installer_under_test(tmp_path: Path) -> str:
    """The installer text with the absolute paths a non-root test cannot fake redirected into tmp_path, and the fakes first on PATH.
    Each replaced line must exist in the script, so a rename cannot silently turn a test into a no-op."""
    t = INSTALL.read_text()
    for old, new in (("/usr/bin/stat", "stat"), ("H5_ETC=/etc/mal-h5", f"H5_ETC={tmp_path}/etc-mal-h5"),
                     ("LIVE_DROPIN=/etc/systemd/system/mal-h5-executor.service.d/live.conf", f"LIVE_DROPIN={tmp_path}/live.conf"),
                     ("export PATH=/usr/sbin:/usr/bin:/sbin:/bin", f'export PATH="{tmp_path}/bin:/usr/sbin:/usr/bin:/sbin:/bin"')):
        assert old in t, old
        t = t.replace(old, new)
    return t


def _build(tmp_path: Path, *, mode="755", systemctl=FAKE_SYSTEMCTL, edit=None, etc_mode="0:0:755"):
    """A throwaway clone, a PATH of fakes and a manifest. Returns (clone, env). /etc/mal-h5 is redirected to <tmp>/etc-mal-h5."""
    clone = tmp_path / "clone"
    (clone / "scripts/mal-fast").mkdir(parents=True)
    for f in all_files():
        (clone / f).parent.mkdir(parents=True, exist_ok=True)
        (clone / f).write_bytes(src_bytes(f))
    script = clone / "scripts/mal-fast/install-h5-executor-pinned.sh"
    script.write_text(installer_under_test(tmp_path))
    script.chmod(0o755)
    shutil.copy(CHECK_TREE, clone / "scripts/mal-fast/check-h5-exec-tree.sh")
    if edit:
        edit(clone)
    _git(clone, "init", "-q")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "x")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fakes = {
        "id": '[ "$1" = "-u" ] && echo 0 && exit 0\nexec /usr/bin/id "$@"',
        "stat": (f'case "$2" in %a) echo {mode};; %u) echo 0;; '
                 f'%u:%g:%a) case "$3" in */helius.env) echo 0:0:600;; */etc-mal-h5) echo {etc_mode};; *) echo 0:0:700;; esac;; '
                 '*) exec /usr/bin/stat "$@";; esac'),
        "find": "exit 0",
        "systemctl": systemctl,
        "install": 'echo "FAKE-INSTALL $*"; exit 99',
    }
    for n, body in fakes.items():
        (bindir / n).write_text("#!/bin/sh\n" + body + "\n")
        (bindir / n).chmod(0o755)
    (tmp_path / "manifest").write_text(_manifest(clone, [f for f in all_files() if (clone / f).exists()]))
    return clone, {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path)}


def _run(clone: Path, env: dict, sha: str | None = None, manifest="manifest", *extra):
    sha = sha or _git(clone, "rev-parse", "HEAD")
    cmd = ["bash", str(clone / "scripts/mal-fast/install-h5-executor-pinned.sh"), sha]
    if manifest:
        cmd.append(str(clone.parent / manifest))
    return subprocess.run([*cmd, *extra], env=env, capture_output=True, text=True)


def test_good_run_verifies_the_manifest_then_stops_at_the_faked_install(tmp_path):
    clone, env = _build(tmp_path)
    r = _run(clone, env)
    assert "manifest verified" in r.stdout and "FAKE-INSTALL" in r.stdout and r.returncode == 99, r.stderr


def test_refuses_non_root_short_sha_head_mismatch_and_group_writable_clone(tmp_path):
    clone, env = _build(tmp_path / "a")
    (tmp_path / "a/bin/id").unlink()
    if os.geteuid() != 0:
        r = _run(clone, env)
        assert r.returncode != 0 and "must run as root" in r.stderr
    for i, bad in enumerate(("abc123", "A" * 40, "g" * 40)):
        clone, env = _build(tmp_path / f"s{i}")
        r = _run(clone, env, sha=bad)
        assert r.returncode != 0 and "sha" in r.stderr
    clone, env = _build(tmp_path / "h")
    r = _run(clone, env, sha="0" * 40)
    assert r.returncode != 0 and "clone HEAD is not" in r.stderr
    clone, env = _build(tmp_path / "w", mode="775")
    r = _run(clone, env)
    assert r.returncode != 0 and "group/world-writable" in r.stderr


def test_refuses_a_dirty_clone(tmp_path):
    for i, dirty in enumerate((lambda c: (c / "untracked.txt").write_text("x"),
                               lambda c: (c / "tools/h5_executor.py").write_text((c / "tools/h5_executor.py").read_text() + "\n# edited\n"),
                               lambda c: (c / "scripts/mal-fast/check-h5-exec-tree.sh").write_text("#!/bin/sh\nexit 0\n"))):
        clone, env = _build(tmp_path / str(i))
        dirty(clone)
        r = _run(clone, env)
        assert r.returncode != 0 and "the clone is dirty" in r.stderr and "FAKE-INSTALL" not in r.stdout and "manifest verified" not in r.stdout, i


def test_manifest_mismatch_missing_entry_and_missing_manifest_refuse_before_any_install(tmp_path):
    clone, env = _build(tmp_path)
    lines = (tmp_path / "manifest").read_text().splitlines()
    (tmp_path / "bad").write_text("\n".join([f"{'0' * 64}  {lines[3].split('  ')[1]}", *lines[:3], *lines[4:]]) + "\n")
    (tmp_path / "short").write_text("\n".join(lines[:-1]) + "\n")
    r = _run(clone, env, manifest="bad")
    assert r.returncode != 0 and "sha256 mismatch" in r.stderr and "FAKE-INSTALL" not in r.stdout and "manifest verified" not in r.stdout
    r = _run(clone, env, manifest="short")
    assert r.returncode != 0 and "missing manifest entry" in r.stderr and "FAKE-INSTALL" not in r.stdout
    r = _run(clone, env, manifest="")
    assert r.returncode != 0 and "manifest argument is mandatory" in r.stderr and "FAKE-INSTALL" not in r.stdout
    r = _run(clone, env, manifest="nonexistent")
    assert r.returncode != 0 and "manifest argument is mandatory" in r.stderr


def test_every_installed_path_is_in_the_manifest_check(tmp_path):
    for f in all_files():
        clone, env = _build(tmp_path / f.replace("/", "_"))
        m = tmp_path / f.replace("/", "_") / "manifest"
        m.write_text("".join(l + "\n" for l in m.read_text().splitlines() if not l.endswith("  " + f)))
        r = _run(clone, env)
        assert r.returncode != 0 and f"missing manifest entry for {f}" in r.stderr, f


def test_refuses_a_sha_without_exp024(tmp_path):
    clone, env = _build(tmp_path, edit=lambda c: (c / EXP024).unlink())
    (tmp_path / "manifest").write_text(_manifest(clone, [f for f in all_files() if f != EXP024]))
    r = _run(clone, env)
    assert r.returncode != 0 and "FAKE-INSTALL" not in r.stdout and "manifest verified" not in r.stdout


def test_a_unit_counts_as_stopped_only_when_activestate_is_exactly_inactive_or_failed(tmp_path):
    """S1: `is-active` is false for activating (waiting out RestartSec), deactivating and reloading; those must refuse, as must an
    empty answer or a missing systemctl (fail closed). Both units are checked."""
    for unit, key in (("mal-h5-executor", "h5"), ("mal-probe-executor", "probe")):
        for state in ("active", "activating", "deactivating", "reloading", "maintenance", "refreshing", "unknown", ""):
            clone, env = _build(tmp_path / f"{key}-{state or 'empty'}", systemctl=fake_systemctl(**{key: state}))
            r = _run(clone, env)
            assert r.returncode != 0 and f"{unit} is not stopped" in r.stderr and "FAKE-INSTALL" not in r.stdout, (unit, state, r.stderr)
        for state in ("inactive", "failed"):
            clone, env = _build(tmp_path / f"{key}-ok-{state}", systemctl=fake_systemctl(**{key: state}))
            assert _run(clone, env).returncode == 99, (unit, state)
    clone, env = _build(tmp_path / "no-systemctl", systemctl="exit 127")  # systemctl missing or broken: no answer is not "stopped"
    r = _run(clone, env)
    assert r.returncode != 0 and "is not stopped" in r.stderr and "FAKE-INSTALL" not in r.stdout


def test_refuses_while_the_probe_unit_is_enabled_and_accepts_it_masked(tmp_path):
    clone, env = _build(tmp_path / "enabled", systemctl=fake_systemctl(enabled="enabled"))
    r = _run(clone, env)
    assert r.returncode != 0 and "mal-probe-executor is enabled" in r.stderr and "FAKE-INSTALL" not in r.stdout
    clone, env = _build(tmp_path / "masked", systemctl=fake_systemctl(enabled="masked"))
    assert _run(clone, env).returncode == 99  # masked and stopped is the desired state


def test_installer_runs_in_a_clean_environment_from_root_whatever_the_callers_cwd_path_and_python(tmp_path):
    t = INSTALL.read_text()
    body = t[t.index("set -euo pipefail"):]
    first = [l for l in body.splitlines() if l.strip() and not l.startswith("#")][:8]
    assert first[0] == "set -euo pipefail" and first[1] == "export PATH=/usr/sbin:/usr/bin:/sbin:/bin"
    assert first[2] == "unset PYTHONPATH PYTHONHOME PYTHONSTARTUP" and first[3].startswith('HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")"')
    assert first[4] == 'ORIG_PWD="$PWD"' and first[5] == "cd /"
    assert t.index("export PATH=") < t.index('id -u') and t.index("cd /\n") < t.index("git -C") and t.index("cd /\n") < t.index("mktemp -d")
    assert "/usr/bin/python3 -I -m venv" in t and "/usr/bin/python3 -m venv" not in t
    assert 'case "$MANIFEST" in "" | /*) ;; *) MANIFEST="$ORIG_PWD/$MANIFEST" ;; esac' in t
    # run for real from a hostile environment: a relative script path, a relative manifest, a bad PATH-less caller env, PYTHON* set
    clone, env = _build(tmp_path)
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "sha256sum").write_text("#!/bin/sh\necho 0000\n")
    (evil / "sha256sum").chmod(0o755)
    env2 = {**env, "PYTHONPATH": str(evil), "PYTHONHOME": "/nonexistent", "PYTHONSTARTUP": str(evil / "x.py"), "PATH": f"{evil}:{env['PATH']}"}
    sha = _git(clone, "rev-parse", "HEAD")
    r = subprocess.run(["bash", "./install-h5-executor-pinned.sh", sha, "../../../manifest"], cwd=clone / "scripts/mal-fast", env=env2, capture_output=True, text=True)
    assert "manifest verified" in r.stdout and r.returncode == 99, r.stderr  # the pinned PATH put the real sha256sum first, not the caller's


def test_refuses_an_existing_live_dropin_so_the_install_and_the_dry_run_are_keyless(tmp_path):
    clone, env = _build(tmp_path)
    (tmp_path / "live.conf").write_text("[Service]\nLoadCredential=probe-wallet:/etc/mal-probe/probe-wallet.json\n")
    r = _run(clone, env)
    assert r.returncode != 0 and "live.conf exists; move it away first" in r.stderr and "step 1b" in r.stderr and "FAKE-INSTALL" not in r.stdout
    (tmp_path / "live.conf").unlink()
    (tmp_path / "live.conf").symlink_to(tmp_path / "nowhere")  # even a dangling link
    assert _run(clone, env).returncode != 0
    (tmp_path / "live.conf").unlink()
    assert _run(clone, env).returncode == 99


def test_etc_mal_h5_gate_directory_and_live_ok_preflight(tmp_path):
    etc = lambda p: p / "etc-mal-h5"  # noqa: E731  the installer copy under test points H5_ETC here

    # absent: the installer would create it (the fake install stops the run at 99 right there)
    clone, env = _build(tmp_path / "absent")
    assert _run(clone, env).returncode == 99

    # present as a real root:root 0755 directory, no LIVE_OK: accepted
    clone, env = _build(tmp_path / "good")
    etc(tmp_path / "good").mkdir()
    assert _run(clone, env).returncode == 99

    # wrong mode or owner (the fake stat reports it): refused before anything is installed
    for name, mode in (("group-writable", "0:0:775"), ("not-root", "1000:1000:755"), ("tight", "0:0:700")):
        clone, env = _build(tmp_path / name, etc_mode=mode)
        etc(tmp_path / name).mkdir()
        r = _run(clone, env)
        assert r.returncode != 0 and "must be a real root:root 0755 directory" in r.stderr and "FAKE-INSTALL" not in r.stdout, name

    # the directory is a symlink: refused
    clone, env = _build(tmp_path / "link")
    (tmp_path / "link" / "elsewhere").mkdir()
    etc(tmp_path / "link").symlink_to(tmp_path / "link" / "elsewhere")
    r = _run(clone, env)
    assert r.returncode != 0 and "must be a real root:root 0755 directory" in r.stderr and "FAKE-INSTALL" not in r.stdout

    # LIVE_OK already there (a regular file, or a symlink, even dangling): a reinstall starts with the gate closed
    for name, make in (("file", lambda f: f.write_text("")), ("symlink", lambda f: f.symlink_to(f.parent / "nowhere"))):
        clone, env = _build(tmp_path / ("ok-" + name))
        etc(tmp_path / ("ok-" + name)).mkdir()
        make(etc(tmp_path / ("ok-" + name)) / "LIVE_OK")
        r = _run(clone, env)
        assert r.returncode != 0 and "LIVE_OK exists; remove it first" in r.stderr and "FAKE-INSTALL" not in r.stdout, name
        assert (etc(tmp_path / ("ok-" + name)) / "LIVE_OK").is_symlink() == (name == "symlink")  # and the installer did not touch it


def test_preflight_refuses_without_root_only_key_dir_and_file(tmp_path):
    clone, env = _build(tmp_path)
    bindir = tmp_path / "bin"
    for body, want in (('case "$3" in */helius.env) echo 0:0:600;; *) echo 1000:1000:755;; esac', "root:root 0700 directory"),
                       ('case "$3" in */helius.env) echo 1000:1000:644;; *) echo 0:0:700;; esac', "root:root 0600 file")):
        (bindir / "stat").write_text('#!/bin/sh\ncase "$2" in %a) echo 755;; %u) echo 0;; %u:%g:%a) ' + body + ';; *) exec /usr/bin/stat "$@";; esac\n')
        r = _run(clone, env)
        assert r.returncode != 0 and want in r.stderr and "FAKE-INSTALL" not in r.stdout, r.stderr


def test_installer_runs_the_checkers_and_refuses_a_bad_unit_or_dropin_before_any_install(tmp_path):
    cases = [
        (BASE_UNIT, "User=mal-live", "User=root", "mal-h5-executor.service failed"),
        (BASE_UNIT, "Environment=LC_ALL=C.UTF-8", "Environment = LD_PRELOAD=/x.so", "failed the allowlist check"),
        (BASE_UNIT, "ReadWritePaths=/var/lib/mal-live/h5", "ReadWritePaths=/var/lib/mal-live", "failed the allowlist check"),
        (BASE_UNIT, "TemporaryFileSystem=/var/lib/mal:ro", "TemporaryFileSystem=/tmp:ro", "failed the allowlist check"),
        ("scripts/mal-fast/mal-h5-executor-live-pinned.conf", "probe-wallet:/etc/mal-probe/probe-wallet.json", "probe-wallet:/etc/shadow", "failed the allowlist check"),
        ("scripts/mal-fast/mal-h5-executor-live-pinned.conf", "h5-executor-live.json --live", "h5-executor.json --live", "failed the allowlist check"),
    ]
    for i, (path, old, new, want) in enumerate(cases):
        def edit(c, path=path, old=old, new=new):
            (c / path).write_text((c / path).read_text().replace(old, new))
        clone, env = _build(tmp_path / str(i), edit=edit)  # the manifest is regenerated from the edited files: only the checker can refuse
        r = _run(clone, env)
        assert r.returncode != 0 and "failed the allowlist check" in r.stderr and "FAKE-INSTALL" not in r.stdout, (path, old, r.stderr)
        assert "manifest verified" in r.stdout


def test_tampered_checker_is_refused_by_the_manifest(tmp_path):
    clone, env = _build(tmp_path)
    m = tmp_path / "manifest"
    m.write_text("".join((f"{'0' * 64}  {CHECKER}\n" if l.endswith(CHECKER) else l + "\n") for l in m.read_text().splitlines()))
    r = _run(clone, env)
    assert r.returncode != 0 and "sha256 mismatch" in r.stderr and "FAKE-INSTALL" not in r.stdout


# --- manifest helper -------------------------------------------------------------------------------------------------------

def test_make_manifest_lists_exactly_the_installer_paths_with_the_blob_hashes(tmp_path):
    import hashlib

    clone, env = _build(tmp_path)
    sha = _git(clone, "rev-parse", "HEAD")
    r = subprocess.run(["bash", str(MAKE_MANIFEST), sha], cwd=clone, capture_output=True, text=True, env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    got = [tuple(l.split("  ")) for l in r.stdout.splitlines()]
    assert [p for _, p in got] == all_files()  # the installer's own order
    for h, p in got:
        assert h == hashlib.sha256((clone / p).read_bytes()).hexdigest()
    # and the installer accepts that output as its manifest
    (tmp_path / "from_helper").write_text(r.stdout)
    r2 = _run(clone, env, manifest="from_helper")
    assert "manifest verified" in r2.stdout and r2.returncode == 99
    bad = subprocess.run(["bash", str(MAKE_MANIFEST), "abc"], cwd=clone, capture_output=True, text=True)
    assert bad.returncode != 0 and "full 40-char sha" in bad.stderr


# --- tree/symlink check (a copy of the probe's, run for real against a real venv) -----------------------------------------

def _check(*dirs, uid=None):
    env = {"PATH": os.environ["PATH"], "MAL_TREE_CHECK_TEST_UID": str(os.getuid() if uid is None else uid)}
    return subprocess.run(["bash", str(CHECK_TREE), *map(str, dirs)], env=env, capture_output=True, text=True)


@pytest.fixture
def real_venv(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("check test mode is refused as root")
    dest = tmp_path / "dest"
    dest.mkdir()
    venv = dest / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    for root, dirs, files in os.walk(dest):
        for n in dirs + files:
            q = Path(root, n)
            if not q.is_symlink():
                q.chmod(q.stat().st_mode & ~0o022)
    venv.chmod(venv.stat().st_mode & ~0o022)
    return dest, venv


def test_tree_check_accepts_a_real_venv_and_refuses_the_bad_cases(real_venv, tmp_path):
    dest, venv = real_venv
    assert _check(venv).returncode == 0
    outside = tmp_path / "x"
    outside.write_text("evil")
    (venv / "bin" / "evil").symlink_to(outside)
    r = _check(venv)
    assert r.returncode != 0 and "points outside" in r.stderr
    (venv / "bin" / "evil").unlink()
    (venv / "dangling").symlink_to(venv / "nope")
    assert "does not resolve" in _check(venv).stderr
    (venv / "dangling").unlink()
    f = venv / "w.py"
    f.write_text("x")
    f.chmod(0o666)
    assert "group/world-writable" in _check(venv).stderr
    f.unlink()
    assert "not owned by uid" in _check(venv, uid=os.getuid() + 1).stderr


def test_tree_check_is_the_probes_script_apart_from_its_header():
    a = [l for l in CHECK_TREE.read_text().splitlines() if not l.startswith("#")]
    b = [l for l in (FAST / "check-probe-exec-tree.sh").read_text().splitlines() if not l.startswith("#")]
    assert a == b and "not allowed as root" in CHECK_TREE.read_text()


# --- runbook ---------------------------------------------------------------------------------------------------------------

RUNBOOK = ROOT / "docs/runbooks/h5-executor.md"


def test_runbook_has_the_ordered_steps_and_the_paths_the_code_uses():
    t = RUNBOOK.read_text()
    order = ["**Step 1. Stop and disable", "sudo systemctl stop mal-probe-executor", "sudo systemctl disable mal-probe-executor", "**Step 1b.", "**Step 2.",
             "install -d -m 0700 -o mal-live -g mal-live /var/lib/mal-live/h5", "install -d -m 0755 -o root -g root /etc/mal-h5", "**Step 3.",
             "**Step 3b.", "*3b-1.", "*3b-2.", "*3b-3.", "**Step 4. Install.", "install-h5-executor-pinned.sh <FULL_SHA>",
             "**Step 5. Hash check.", "**Step 6.", "**Step 7.", "**Step 8.", "**Step 9.", "**Step 10.", "**Step 11.", "## Going live (manager, then Helm)",
             "## Stop, halt, status", "## Daily check", "## Sell-and-close", "## Rollback", "## Auditd", "## Never", "## Not verified"]
    idx = [t.index(s) for s in order]
    assert idx == sorted(idx), [s for s, i in zip(order, idx) if i != sorted(idx)[idx.index(i)]]
    for p in ("/etc/mal-h5/LIVE_OK", "/var/lib/mal-live/h5/STOP", "/var/lib/mal-live/h5/HALT", "/var/lib/mal-live/h5/FINAL_WRITTEN", "-k malh5-liveok", "/usr/local/lib/mal-h5-exec/current/h5-executor-live.json",
              "/etc/systemd/system/mal-h5-executor.service.d/live.conf", "/etc/systemd/system/mal-h5-executor.service.d/10-shadow-feed.conf",
              "/srv/mal-h5-shadow", "/etc/mal-probe-rpc/helius.env", "make-h5-manifest.sh", "h5-daily-check.py", "-w /usr/local/lib/mal-h5-exec -p wa"):
        assert p in t, p
    from tools import h5_executor

    # the names the runbook relies on exist, and the executor pins LIVE_OK to the path the installer, the unit and the daily check use
    assert callable(h5_executor.live_ok_valid) and h5_executor.EXP024_PART1 and str(h5_executor.LIVE_OK_PATH) == "/etc/mal-h5/LIVE_OK"
    assert "live_ok_file" in h5_executor.PINNED_PATH_KEYS  # a config override is refused in live, so the pinned configs set none
    for cfgname in ("h5-executor-live.json", "h5-executor.json"):
        assert not set(h5_executor.PINNED_PATH_KEYS) & set(json.loads((FAST / cfgname).read_text())), cfgname
    assert json.loads((FAST / "h5-executor-live.json").read_text())["intents_file"] == "/srv/mal-h5-shadow"
    assert t.index("**Step 1.") < t.index("**Step 4. Install.")  # the old unit is stopped and disabled before anything is installed


def test_runbook_never_reads_a_key_or_env_file_and_flags_exist():
    t = RUNBOOK.read_text()
    assert not re.search(r"\b(cat|less|more|head|tail|strings|xxd|od|base64)\b[^\n]*(probe-wallet|helius\.env)", t)
    assert "Never print, copy, paste or commit" in t and "Never run `tools.h5_executor`" in t
    from tools import h5_sell_and_close as sc

    section = t[t.index("## Sell-and-close"):t.index("## Rollback")]
    help_src = Path(sc.__file__).read_text()
    for flag in (set(re.findall(r"`(--[a-z-]+)", section)) - {"--clear-halt"}) | {"--mint", "--send"}:  # --clear-halt is the executor's
        assert f'"{flag}"' in help_src, flag
    daily = (FAST / "h5-daily-check.py").read_text()
    assert '"--funded-sol"' in daily and '"--write-baseline"' in daily
    ex = (ROOT / "tools/h5_executor.py").read_text()
    for flag in ("--status", "--clear-halt", "--live", "--dry-run"):
        assert f'"{flag}"' in ex, flag


def test_runbook_names_the_sha_rule_and_the_live_ok_gate():
    t = RUNBOOK.read_text()
    assert "only the one the manager names in a comment on PR #499" in t and "after the executor branch" in t and "Never install any sha but the one the manager names" in t
    assert "origin/main" in t and "EXP-024" in t
    # LIVE_OK: root-owned gate in /etc/mal-h5, created by Helm after the hash check; the state dir is not the gate
    assert "Create `/etc/mal-h5/LIVE_OK`" in t and "(root:root 0644)" in t and "sudo install -m 0644 -o root -g root /dev/null /etc/mal-h5/LIVE_OK" in t
    assert t.index("**Step 5. Hash check.**") < t.index("sudo install -m 0644 -o root -g root /dev/null /etc/mal-h5/LIVE_OK")
    assert "sudo touch /var/lib/mal-live/h5/LIVE_OK" not in t and "sudo rm /var/lib/mal-live/h5/LIVE_OK" not in t
    assert "sudo rm /etc/mal-h5/LIVE_OK" in t and "Removing `LIVE_OK` stops new buys at once" in t
    # STOP: sudo touch, with what the executor accepts
    assert "sudo touch /var/lib/mal-live/h5/STOP" in t and "asks for no owner and no mode" in t and "Path.exists()" in t
    # the installer refuses an existing LIVE_OK, and the runbook says how to close the gate before a reinstall
    assert "**Step 1b." in t and "sudo rm -f /etc/mal-h5/LIVE_OK" in t


def test_runbook_preflight_checks_match_the_unit_and_have_expected_output():
    t = RUNBOOK.read_text()
    sec = t[t.index("**Step 3b."):t.index("**Step 4. Install.")]
    unit = {l for l in UNIT.read_text().splitlines() if "=" in l and not l.startswith("#")}
    # every sandbox property the transient unit carries is the real unit's (so the check tests the real combination)
    props = re.findall(r"-p \"?([A-Za-z]+=[^\s\")]*)", sec)
    assert props
    for p in props:
        k, v = p.split("=", 1)
        if k == "User":
            assert "User=mal-live" in unit
        elif k == "BindReadOnlyPaths":
            assert v == "-$SHADOW:/srv/mal-h5-shadow"
        else:
            assert p in unit, p
    for need in ("ProtectSystem=strict", "ProtectHome=tmpfs", "TemporaryFileSystem=/var/lib/mal:ro", "ReadWritePaths=/var/lib/mal-live/h5", "BindReadOnlyPaths="):
        assert any(need in p for p in props), need
    # the three checks and their expected output are written down
    assert "systemctl --version" in sec and "systemd-analyze verify /root/mal-h5-src/scripts/mal-fast/mal-h5-executor.service" in sec
    assert "sudo systemd-run \"${P[@]}\" /bin/sh -c" in sec
    for line in ("home:", "shadow-file-read: ok", "shadow-write: refused", "h5-dir-write: ok", "probe-dir-write: refused", "etc-mal-h5-write: refused",
                 "etc-mal-h5-read: ok", "var-lib-mal:", "rc=0", "Command /usr/local/lib/mal-h5-exec/venv/bin/python is not executable"):
        assert line in sec, line
    for bad in ("SUCCEEDED (BAD)", "FAILED"):
        assert bad in sec
    # verify is also run on the installed unit (steps 7 and 9), and the running unit's namespace is inspected (step 8)
    assert t.count("sudo systemd-analyze verify /etc/systemd/system/mal-h5-executor.service") == 2 and "nsenter -t \"$PID\" -m" in t
    assert "/proc/$PID/mountinfo" in t


def test_runbook_preflight_script_is_valid_sh_and_every_write_probe_is_a_refused_dot_preflight_file():
    t = RUNBOOK.read_text()
    sec = t[t.index("**Step 3b."):t.index("**Step 4. Install.")]
    m = re.search(r"/bin/sh -c '\n(.*?)\n'; echo \"rc=\$\?\"", sec, re.S)
    assert m
    body = m.group(1)
    assert "'" not in body
    r = subprocess.run(["sh", "-n", "-c", body], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    writes = re.findall(r": > (\S+)\)", body)
    assert writes and all(w.endswith("/.preflight") for w in writes)
    assert {w.rsplit("/", 1)[0] for w in writes} == {"/srv/mal-h5-shadow", "/var/lib/mal-live/h5", "/var/lib/mal-live", "/etc/mal-h5"}
    # the array assignment and the surrounding commands parse as bash once the placeholder is filled
    head = sec[sec.index("SHADOW="):sec.index("sudo systemd-run")].replace("<jobuser>", "x")
    assert subprocess.run(["bash", "-n", "-c", head], capture_output=True, text=True).returncode == 0
