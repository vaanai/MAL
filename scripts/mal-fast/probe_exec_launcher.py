"""Launcher for the pinned live probe executor (DEC-019). Installed as <sha>/launcher.py.

The unit runs `python -I -B -u launcher.py ...`. -I (isolated) ignores PYTHONPATH, user site and the
working directory, so nothing outside the pinned install can shadow a module. This file puts ONLY its own
resolved directory (the root-owned <sha> dir, not the `current` symlink) on sys.path, checks that the
`tools` package really came from there, then runs tools.probe_executor exactly as `-m` would.
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, ROOT)

import tools  # noqa: E402

if not os.path.realpath(tools.__file__).startswith(ROOT + os.sep):
    sys.exit("launcher: tools package is not from the pinned install; refusing")
runpy.run_module("tools.probe_executor", run_name="__main__", alter_sys=True)
