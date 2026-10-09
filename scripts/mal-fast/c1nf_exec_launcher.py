"""Launcher for the pinned C1-NF executor (DEC-026). Installed as <sha>/launcher.py. A copy of h5_exec_launcher.py (DEC-024).

The unit runs `python -I -B -u launcher.py ...`. -I (isolated) ignores PYTHONPATH, user site and the working directory, so
nothing outside the pinned install can shadow a module. This file puts ONLY its own resolved directory (the root-owned <sha>
dir, not the `current` symlink) on sys.path, checks that the `tools` package really came from there, then runs
tools.c1nf_executor exactly as `-m` would.

No `--run-tool`: H5's sell_and_close checks only the H5 and probe units and defaults to H5's key file, so it is not offered for
the second wallet. A C1-NF sell-and-close is an open item (docs/runbooks/c1nf-executor.md).
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, ROOT)

import tools  # noqa: E402

if not os.path.realpath(tools.__file__).startswith(ROOT + os.sep):
    sys.exit("launcher: tools package is not from the pinned install; refusing")

MODULE = "tools.c1nf_executor"
if sys.argv[1:2] == ["--run-tool"]:
    sys.exit("launcher: the C1-NF launcher has no --run-tool")
runpy.run_module(MODULE, run_name="__main__", alter_sys=True)
