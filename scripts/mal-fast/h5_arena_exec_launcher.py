"""Launcher for the pinned H5 ARENA canary executor (DEC-032). Installed as <sha>/launcher.py under /usr/local/lib/mal-h5-arena-exec. Same design as
h5_exec_launcher.py, with ONE module: tools.h5_arena_executor (no --run-tool: the root sell-and-close tool stays the H5 install's).

The unit runs `python -I -B -u launcher.py ...`. -I (isolated) ignores PYTHONPATH, user site and the working directory, so nothing outside the pinned
install can shadow a module. This file puts ONLY its own resolved directory (the root-owned <sha> dir, not the `current` symlink) on sys.path, checks
that the `tools` and `observe` packages really came from there, then runs the module exactly as `-m` would.
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, ROOT)

import observe  # noqa: E402  (tools.arena_paper -> tools.h5_shadow imports observe.trade_decode at module level)
import tools  # noqa: E402

for pkg in (tools, observe):
    if not os.path.realpath(pkg.__file__).startswith(ROOT + os.sep):
        sys.exit(f"launcher: {pkg.__name__} package is not from the pinned install; refusing")

MODULE = "tools.h5_arena_executor"
if sys.argv[1:2] == ["--run-tool"]:
    sys.exit("launcher: --run-tool is not available in the arena install (sell-and-close is the H5 install's root tool)")
runpy.run_module(MODULE, run_name="__main__", alter_sys=True)
