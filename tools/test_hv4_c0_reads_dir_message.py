"""The start check names the reads-dir provisioning step on the bridge host (/data/mal/arena/reads, root:root 0755, and the 0444 record copy) instead of a
bare label. Synthetic only: the canonical paths are patched to temp dirs; no real /data/mal/arena path is touched."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import TmpCase
from tools.test_hv4_live_c0 import ARM, Hv4Env


class ReadsDirMessageTests(TmpCase):
    def test_absent_dir_has_its_own_label_and_the_note_names_path_owner_mode_and_command(self):
        sealed = Path(self.tmp) / "arena"
        canon = sealed / "reads"
        with mock.patch.object(c0, "SEALED_ROOT_PREFIX", str(sealed)), mock.patch.object(c0, "READS_DIR_CANON", str(canon)):
            why, _ = c0.read_record_problem(str(canon), ARM)
            self.assertEqual(why, "read_record_dir_absent")
            canon.mkdir(parents=True)
            os.chmod(canon, 0o755)
            why, _ = c0.read_record_problem(str(canon), ARM)
            self.assertEqual(why, "read_record_missing")  # the dir exists, the record does not
        for label in ("read_record_dir_absent", "read_record_missing", "read_record_dir_owner_mode", "read_record_owner_mode"):
            t = c0.refusal_text(label)
            self.assertTrue(t.startswith(label + " ("), t)
            for must in ("/data/mal/arena/reads", "install -d -m 0755 -o root -g root /data/mal/arena /data/mal/arena/reads", "root:root 0755", "0444", "RECORD"):
                self.assertIn(must, t)
        self.assertEqual(c0.refusal_text("read_record_not_pass"), "read_record_not_pass")  # other labels are unchanged
        self.assertIn("MANUAL RECOVERY", c0.refusal_text("window_file_unsafe"))

    def test_main_prints_the_note_on_a_host_without_the_reads_dir(self):
        e = Hv4Env(self.tmp)
        sealed = Path(self.tmp) / "arena-absent"
        cfg = Path(self.tmp) / "cfg.json"
        raw = {**e.raw, "reads_dir": str(sealed / "reads")}
        cfg.write_text(json.dumps(raw))
        buf = io.StringIO()
        with mock.patch.object(c0, "SEALED_ROOT_PREFIX", str(sealed)), mock.patch.object(c0, "READS_DIR_CANON", str(sealed / "reads")), redirect_stdout(buf):
            rc = alb.main(["--config", str(cfg), "--once"])
        out = buf.getvalue()
        self.assertEqual(rc, 2)
        self.assertIn("startup_refused read_record_dir_absent (", out)
        self.assertIn("install -d -m 0755 -o root -g root /data/mal/arena /data/mal/arena/reads", out)
        self.assertFalse(os.path.exists(sealed))  # the bridge creates nothing
