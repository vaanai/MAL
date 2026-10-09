"""Launcher for the pinned H5 executor (DEC-024). Installed as <sha>/launcher.py. Same design as probe_exec_launcher.py.

The unit runs `python -I -B -u launcher.py ...`. -I (isolated) ignores PYTHONPATH, user site and the working directory, so
nothing outside the pinned install can shadow a module. This file puts ONLY its own resolved directory (the root-owned <sha>
dir, not the `current` symlink) on sys.path, checks that the `tools` package really came from there, then runs the module
exactly as `-m` would.

`--run-tool NAME` (first argument only) runs a root-run tool from the same pinned tree instead of the executor. The unit's
ExecStart never passes it. Only the names in TOOLS are accepted.
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, ROOT)

import tools  # noqa: E402

if not os.path.realpath(tools.__file__).startswith(ROOT + os.sep):
    sys.exit("launcher: tools package is not from the pinned install; refusing")

TOOLS = {"sell_and_close": "tools.h5_sell_and_close"}
module = "tools.h5_executor"
if sys.argv[1:2] == ["--run-tool"]:
    if len(sys.argv) < 3 or sys.argv[2] not in TOOLS:
        sys.exit("launcher: --run-tool needs one of: " + ", ".join(sorted(TOOLS)))
    module = TOOLS[sys.argv[2]]
    sys.argv = [sys.argv[0], *sys.argv[3:]]
runpy.run_module(module, run_name="__main__", alter_sys=True)
