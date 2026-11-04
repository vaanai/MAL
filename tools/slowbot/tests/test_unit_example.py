"""The example unit's python flags must let `-m tools.slowbot.live_executor` import from WorkingDirectory (HELM-REPLY-1104-1)."""
import os
import re
import shlex
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
UNIT = os.path.join(ROOT, "scripts", "mal-fast", "mal-slowbot-live.service.example")


def _execstarts():
    lines = []
    for raw in open(UNIT, encoding="utf-8").read().splitlines():
        line = raw.lstrip("# ").strip()
        if line.startswith("ExecStart=") and "live_executor" in line:
            lines.append(line[len("ExecStart="):])
    return lines


def _flags(cmd):
    argv = shlex.split(cmd)
    i = argv.index("-m")
    return argv[1:i], argv[i:]


def test_both_execstarts_found_and_no_isolated_flag():
    cmds = _execstarts()
    assert len(cmds) == 2  # dry unit + live drop-in
    for c in cmds:
        flags, _ = _flags(c)
        assert "-I" not in flags and "-P" not in flags
        assert flags == ["-E", "-s", "-B", "-u"]
    assert _execstarts()[1].endswith("--live")


def test_flags_import_module_from_working_directory():
    flags, _ = _flags(_execstarts()[0])
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = "/nonexistent"  # -E must ignore it; the import has to come from cwd
    code = subprocess.run(
        [sys.executable, *flags, "-c", "import tools.slowbot.live_executor as m; print(m.__file__)"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert code.returncode == 0, code.stderr[-2000:]
    assert os.path.realpath(code.stdout.strip()).startswith(os.path.realpath(ROOT))


def test_isolated_flag_would_fail_on_this_python():
    if sys.version_info < (3, 11):  # -P (implied by -I) exists from 3.11
        return
    code = subprocess.run([sys.executable, "-I", "-c", "import tools.slowbot.live_executor"],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert code.returncode != 0 and re.search(r"No module named .?tools", code.stderr)
