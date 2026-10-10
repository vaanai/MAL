"""Launcher for the pinned C1-NF executor (DEC-026). Installed as <sha>/launcher.py. A copy of h5_exec_launcher.py (DEC-024).

The unit runs `python -I -B -u launcher.py ...`. -I (isolated) ignores PYTHONPATH, user site and the working directory, so
nothing outside the pinned install can shadow a module. This file puts ONLY its own resolved directory (the root-owned <sha>
dir, not the `current` symlink) on sys.path, checks that the `tools` package really came from there, then runs
tools.c1nf_executor exactly as `-m` would.

`--run-tool sell_and_close` (first argument only) runs C1-NF's own root rescue tool, tools.c1nf_sell_and_close, from the same pinned
tree instead of the executor. The unit's ExecStart never passes it. It is the only tool: H5's tools.h5_sell_and_close is never offered
here, because its guard checks only the H5 and probe units and it defaults to H5's key file (docs/runbooks/c1nf-executor.md).
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, ROOT)

import tools  # noqa: E402

if not os.path.realpath(tools.__file__).startswith(ROOT + os.sep):
    sys.exit("launcher: tools package is not from the pinned install; refusing")

TOOLS = {"sell_and_close": "tools.c1nf_sell_and_close"}
MODULE = "tools.c1nf_executor"
if sys.argv[1:2] == ["--run-tool"]:
    if len(sys.argv) < 3 or sys.argv[2] not in TOOLS:
        sys.exit("launcher: --run-tool needs one of: " + ", ".join(sorted(TOOLS)))
    MODULE = TOOLS[sys.argv[2]]
    sys.argv = [sys.argv[0], *sys.argv[3:]]
runpy.run_module(MODULE, run_name="__main__", alter_sys=True)
