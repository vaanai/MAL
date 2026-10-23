"""Tests for tools/h5_replay_rpc_rewrite.py on synthetic fixtures."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import h5_replay_rpc_rewrite as rw

TRIG = '{"type": "trigger", "variant": "%s", "pool": "%s", "synthetic": false, "synthetic_src": "pre_event_binary", "note": "pre_event_binary", "x": 1.50}\n'


def build(root: Path) -> Path:
    d = root / "in"
    d.mkdir()
    lines = [
        '{"type":"start","start_ms":1}\n',
        TRIG % ("pv", "P1"), TRIG % ("fv", "P1"),
        '{"type":"trigger","variant":"pv","synthetic":false,"synthetic_src":"pre_event_binary"}\n',   # compact spacing
        '{"type": "trigger", "variant": "pv", "synthetic": true, "synthetic_src": "pre_event_binary"}\n',  # synthetic not false
        '{"type": "trigger", "variant": "pv", "synthetic": 0, "synthetic_src": "pre_event_binary"}\n',      # not the literal false
        '{"type": "trigger", "variant": "pv", "synthetic": false, "synthetic_src": "ws"}\n',
        '{"type": "excluded", "synthetic": false, "synthetic_src": "pre_event_binary"}\n',                  # not a trigger
        'not json pre_event_binary "synthetic_src"\n',
        '{"type": "trigger", "variant": "pv", "synthetic": false, "synthetic_src": "pre_event_binary"}',     # no trailing newline
    ]
    (d / "h5-shadow-2026-09-20T00.jsonl").write_bytes("".join(lines).encode())
    (d / "h5-shadow-2026-09-20T01.jsonl").write_bytes(b"\r\n".join([b'{"type":"start"}', b""]))
    (d / "other.txt").write_bytes(b"pre_event_binary\n")
    return d


class RewriteTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)

    def test_only_the_label_bytes_change(self):
        d = build(self.root)
        res = rw.rewrite_dir(d, self.root / "out")
        self.assertEqual(res["rewritten_records"], 4)
        raw = (d / "h5-shadow-2026-09-20T00.jsonl").read_bytes().split(b"\n")
        new = (self.root / "out" / "h5-shadow-2026-09-20T00.jsonl").read_bytes().split(b"\n")
        self.assertEqual(len(raw), len(new))
        changed = [i for i, (a, b) in enumerate(zip(raw, new)) if a != b]
        self.assertEqual(changed, [1, 2, 3, 9])
        for i in changed:
            self.assertEqual(raw[i].replace(b'"synthetic_src": "pre_event_binary"', b'"synthetic_src": "rpc"').replace(b'"synthetic_src":"pre_event_binary"', b'"synthetic_src":"rpc"'), new[i])
            a, b = json.loads(raw[i]), json.loads(new[i])
            self.assertEqual([k for k in a if a[k] != b[k]], ["synthetic_src"])
            self.assertEqual(list(a), list(b))
        self.assertIn(b'"note": "pre_event_binary"', new[1])  # a look-alike value elsewhere is untouched
        for name in ("h5-shadow-2026-09-20T01.jsonl", "other.txt"):
            self.assertEqual((d / name).read_bytes(), (self.root / "out" / name).read_bytes())

    def test_hashes_and_counts_recorded(self):
        import hashlib
        d = build(self.root)
        res = rw.rewrite_dir(d, self.root / "out")
        by = {f["file"]: f for f in res["files"]}
        self.assertEqual(set(by), {"h5-shadow-2026-09-20T00.jsonl", "h5-shadow-2026-09-20T01.jsonl", "other.txt"})
        for name, f in by.items():
            self.assertEqual(f["raw_sha256"], hashlib.sha256((d / name).read_bytes()).hexdigest())
            self.assertEqual(f["rewritten_sha256"], hashlib.sha256((self.root / "out" / name).read_bytes()).hexdigest())
        self.assertEqual(by["h5-shadow-2026-09-20T00.jsonl"]["records_rewritten"], 4)
        self.assertNotEqual(by["h5-shadow-2026-09-20T00.jsonl"]["raw_sha256"], by["h5-shadow-2026-09-20T00.jsonl"]["rewritten_sha256"])
        self.assertEqual(by["other.txt"]["raw_sha256"], by["other.txt"]["rewritten_sha256"])

    def test_refuses_existing_output_and_inside_input(self):
        d = build(self.root)
        (self.root / "exists").mkdir()
        with self.assertRaises(SystemExit):
            rw.rewrite_dir(d, self.root / "exists")
        with self.assertRaises(SystemExit):
            rw.rewrite_dir(d, d / "sub")
        rw.rewrite_dir(d, self.root / "out")
        with self.assertRaises(SystemExit):
            rw.rewrite_dir(d, self.root / "out")  # second run refuses
        self.assertEqual(len(list(d.iterdir())), 3)  # the input is untouched

    def test_refuses_when_the_label_is_ambiguous(self):
        d = self.root / "amb"
        d.mkdir()
        (d / "h5-shadow-2026-09-20T00.jsonl").write_text('{"type":"trigger","synthetic":false,"synthetic_src":"pre_event_binary","synthetic_src":"pre_event_binary"}\n')
        with self.assertRaises(SystemExit):
            rw.rewrite_dir(d, self.root / "o2")

    def test_manifest_is_create_exclusive(self):
        d = build(self.root)
        m = self.root / "m.json"
        self.assertEqual(rw.main(["--src", str(d), "--dst", str(self.root / "out"), "--manifest", str(m)]), 0)
        self.assertEqual(json.loads(m.read_text())["rewritten_records"], 4)


if __name__ == "__main__":
    unittest.main()
