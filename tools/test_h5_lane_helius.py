"""tools.h5_lane_helius: the api-key stays out of argv, logs and the start record's endpoints."""
from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from observe.trade_source import _safe_err
except ImportError:  # needs certifi + websockets (the listener venv)
    _safe_err = None
from tools import h5_lane_helius as lh
from tools import h5_shadow as h5

KEY = "k3y-SECRET-0123456789abcdef"


class HeliusLaunchTests(unittest.TestCase):
    def _envfile(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / "helius.env"
        p.write_text(text)
        os.chmod(p, 0o600)
        return str(p)

    def test_read_key_variants(self):
        for t in (f"HELIUS_API_KEY={KEY}\n", f"export HELIUS_API_KEY='{KEY}'\n", f"# c\nX=1\nHELIUS_API_KEY=\"{KEY}\"\n"):
            self.assertEqual(lh.read_key(self._envfile(t)), KEY)

    def test_read_key_error_does_not_echo(self):
        with self.assertRaises(ValueError) as cm:
            lh.read_key(self._envfile("OTHER=abc\n"))
        self.assertNotIn("abc", str(cm.exception))

    def test_urls_two_helius_one_public(self):
        u = lh.build_ws_urls(KEY)
        self.assertEqual([x.startswith("wss://mainnet.helius-rpc.com/") for x in u], [True, False, True])
        self.assertEqual(u[1], lh.PUBLIC_WS)

    def test_key_not_in_process_argv_and_ws_url_refused(self):
        seen = {}
        with mock.patch.object(lh.ls, "main", lambda a: seen.setdefault("a", list(a)) and 0), \
                mock.patch.dict(os.environ, {"H5_HELIUS_ENV_FILE": self._envfile(f"HELIUS_API_KEY={KEY}\n")}), \
                mock.patch.object(sys, "argv", ["x", "--out-dir", "o"]), \
                mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(sys, "stderr", io.StringIO()), \
                mock.patch.object(logging, "setLogRecordFactory"):
            self.assertEqual(lh.main(["--out-dir", "o"]), 0)
            self.assertFalse(any(KEY in a for a in sys.argv))  # what ps shows
            self.assertTrue(any(KEY in a for a in seen["a"]))  # only the in-process list carries it
            self.assertEqual(lh.main(["--ws-url", "wss://x"]), 2)

    def test_missing_env_file_exit3_no_key_output(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"H5_HELIUS_ENV_FILE": "/nonexistent/h.env"}), mock.patch.object(sys, "stderr", err):
            self.assertEqual(lh.main(["--out-dir", "o"]), 3)

    def test_redaction_of_streams_and_logs(self):
        buf = io.StringIO()
        rs = lh.RedactingStream(buf, KEY)
        rs.write(f"boom wss://h/?api-key={KEY}")
        self.assertNotIn(KEY, buf.getvalue())
        old = logging.getLogRecordFactory()
        self.addCleanup(logging.setLogRecordFactory, old)
        lh.install_redaction(KEY)
        self.addCleanup(lambda: (setattr(sys, "stdout", sys.__stdout__), setattr(sys, "stderr", sys.__stderr__)))
        rec = logging.getLogger("t").makeRecord("t", logging.WARNING, "f", 1, "bad %s", (f"wss://h/?api-key={KEY}",), None)
        self.assertNotIn(KEY, rec.getMessage())

    def test_pinned_start_record_and_errors_redact(self):
        url = lh.HELIUS_WS + KEY
        self.assertNotIn(KEY, h5._strip_url(url))
        self.assertNotIn(KEY, " ".join(h5.redact_argv(["--ws-url", url, f"--ws-url={url}"])))
        if _safe_err is not None:
            self.assertNotIn(KEY, _safe_err(RuntimeError(f"connect {url} failed")))


if __name__ == "__main__":
    unittest.main()
