"""scripts/ops/slowbot-intents-push.sh with a local shim standing in for ssh (no network)."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ops" / "slowbot-intents-push.sh"


def shim(tmp_path):
    p = tmp_path / "fakessh"
    p.write_text('#!/bin/sh\nshift\nexec sh -c "$1"\n')  # drop the host argument, run the remote command locally
    p.chmod(0o755)
    return str(p)


def push(tmp_path, src, dest, ssh=None):
    env = dict(os.environ, SLOWBOT_INTENTS_SRC=str(src), SLOWBOT_INTENTS_DEST=str(dest), SLOWBOT_SSH=ssh or shim(tmp_path), TMPDIR=str(tmp_path))
    r = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def test_push_is_idempotent_and_appends_only_new_rows(tmp_path):
    src, dest = tmp_path / "src.jsonl", tmp_path / "remote" / "d.jsonl"
    src.write_text('{"a":1}\n{"a":2}\n')
    assert push(tmp_path, src, dest)[:2] == (0, "pushed=16 remote_bytes=16")
    assert dest.read_text() == src.read_text()
    assert push(tmp_path, src, dest)[:2] == (0, "pushed=0 remote_bytes=16")  # second run sends nothing
    with open(src, "a") as f:
        f.write('{"a":3}\n')
    assert push(tmp_path, src, dest)[1].startswith("pushed=8")
    assert dest.read_text() == src.read_text() == '{"a":1}\n{"a":2}\n{"a":3}\n'


def test_torn_local_tail_is_not_sent_until_complete(tmp_path):
    src, dest = tmp_path / "src.jsonl", tmp_path / "remote" / "d.jsonl"
    src.write_text('{"a":1}\n{"a":')
    push(tmp_path, src, dest)
    assert dest.read_text() == '{"a":1}\n'
    src.write_text('{"a":1}\n{"a":2}\n')
    push(tmp_path, src, dest)
    assert dest.read_text() == src.read_text()


def test_remote_not_a_prefix_refuses(tmp_path):
    src, dest = tmp_path / "src.jsonl", tmp_path / "remote" / "d.jsonl"
    src.write_text('{"a":1}\n{"a":2}\n')
    dest.parent.mkdir()
    dest.write_text('{"z":9}\n')
    rc, out, err = push(tmp_path, src, dest)
    assert rc == 5 and dest.read_text() == '{"z":9}\n'


def test_remote_ahead_refuses_and_missing_source_is_a_noop(tmp_path):
    src, dest = tmp_path / "src.jsonl", tmp_path / "remote" / "d.jsonl"
    assert push(tmp_path, src, dest)[:2] == (0, "pushed=0 reason=no_source")
    src.write_text('{"a":1}\n')
    dest.parent.mkdir()
    dest.write_text('{"a":1}\n{"a":2}\n')
    assert push(tmp_path, src, dest)[0] == 4


def test_unreachable_remote_exits_3(tmp_path):
    bad = tmp_path / "badssh"
    bad.write_text("#!/bin/sh\nexit 255\n")
    bad.chmod(0o755)
    src = tmp_path / "src.jsonl"
    src.write_text('{"a":1}\n')
    assert push(tmp_path, src, tmp_path / "r" / "d", ssh=str(bad))[0] == 3
