"""Smoke tests for observe TLS context (no network)."""

import os
import ssl
import unittest
from unittest import mock

from observe.client import observe_ssl_context


class ObserveSslContextTests(unittest.TestCase):
    def test_default_verifies_with_certifi(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAL_OBSERVE_SSL_INSECURE", None)
            ctx = observe_ssl_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_insecure_env_disables_verify(self) -> None:
        with mock.patch.dict(os.environ, {"MAL_OBSERVE_SSL_INSECURE": "1"}):
            ctx = observe_ssl_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)
        self.assertFalse(ctx.check_hostname)


if __name__ == "__main__":
    unittest.main()
