"""Tests for scripts/ops/tip-hour-inputs-stage.sh (QP on #1191, S1 to S8): synthetic files, the destination is a local dir (RSH=env), no real tip data."""
import hashlib
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ops" / "tip-hour-inputs-stage.sh"
MARK = "SECRET-LOOKING-CONTENT-MARK"  # appears in the source files; the logs must never echo file content


def setup(tmp_path):
    out, ops, dest = tmp_path / "out", tmp_path / "ops", tmp_path / "dest"
    (out / ".writer").mkdir(parents=True)
    ops.mkdir()
    (out / ".writer" / "counts.jsonl").write_bytes(b'{"hour":"2026-11-03T00","m":"' + MARK.encode() + b'"}\n' * 1)
    (out / ".writer" / "state.json").write_bytes(b'{"pool":"addr"}')
    (out / "tape-2026-11-03T00.jsonl").write_bytes(b'{"mint":"x"}\n')
    (ops / "tip-health-1101.jsonl").write_bytes(b'{"t":1}\n{"t":2}\n')
    (ops / "tip-health-1102.jsonl").write_bytes(b'{"t":3}\n')
    (ops / "tip-pool-cache-daily.jsonl").write_bytes(b'{"utc":"2026-11-03T00:30:00Z","pool_cache":1}\nNRestarts=0\n')
    env = {**os.environ, "OUTDIR": str(out), "OPS": str(ops), "DEST": str(dest), "RSH": "env", "ONCE": "1", "SAMPLERS": "tip-health-1101.jsonl tip-health-1102.jsonl tip-health-1107.jsonl"}
    env.pop("END", None)
    return env, out, ops, dest


def run(env):
    return subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_script_is_posix_sh_with_a_period_cap():
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
    body = SCRIPT.read_bytes()
    assert body.splitlines()[0] == b"#!/bin/sh" and b"PIPESTATUS" not in body and b"[[" not in body
    assert b'[ "$PERIOD" -gt 300 ] && PERIOD=300' in body
    assert b"state.json" in body and b"MemoryMax" in body and b"nice -n 19 ionice -c3" in body


def test_first_pass_copies_exactly_the_named_files_as_bytes(tmp_path):
    env, out, ops, dest = setup(tmp_path)
    p = run(env)
    assert p.returncode == 0, p.stdout + p.stderr
    staged = sorted(f.name for f in dest.iterdir())
    assert staged == ["counts.jsonl", "silence.jsonl", "tip-health-1101.jsonl", "tip-health-1102.jsonl", "tip-pool-cache-daily.jsonl"]  # 1107 not written yet: skipped
    assert "SKIP_ABSENT tip-health-1107.jsonl" in p.stdout
    assert sha(dest / "counts.jsonl") == sha(out / ".writer" / "counts.jsonl") and (dest / "silence.jsonl").read_bytes() == b""
    assert sha(dest / "tip-health-1101.jsonl") == sha(ops / "tip-health-1101.jsonl")
    assert f"src_sha256={sha(out / '.writer' / 'counts.jsonl')} copy_sha256={sha(out / '.writer' / 'counts.jsonl')}" in p.stdout
    assert MARK not in p.stdout + p.stderr  # sizes, sha256s and instants only
    assert not [f for f in dest.iterdir() if f.name.startswith(".")]  # no tmp left
    assert "state.json" not in staged and not any(n.startswith("tape-") for n in staged)
    p2 = run(env)
    assert p2.returncode == 0 and "same counts.jsonl" in p2.stdout and "staged " not in p2.stdout


def test_an_appended_file_is_extended_and_stays_equal_to_the_source(tmp_path):
    env, out, ops, dest = setup(tmp_path)
    assert run(env).returncode == 0
    with open(out / ".writer" / "counts.jsonl", "ab") as fh:
        fh.write(b'{"hour":"2026-11-03T01"}\n')
    (ops / "tip-health-1107.jsonl").write_bytes(b'{"t":9}\n')  # a new segment appears
    p = run(env)
    assert p.returncode == 0 and "staged counts.jsonl" in p.stdout
    assert sha(dest / "counts.jsonl") == sha(out / ".writer" / "counts.jsonl") and (dest / "tip-health-1107.jsonl").read_bytes() == b'{"t":9}\n'


def test_a_non_prefix_change_alerts_and_stops_and_never_overwrites(tmp_path):
    env, out, ops, dest = setup(tmp_path)
    assert run(env).returncode == 0
    before = (dest / "counts.jsonl").read_bytes()
    (out / ".writer" / "counts.jsonl").write_bytes(b'{"hour":"REWRITTEN"}\n' + before)  # same length class, different bytes
    p = run(env)
    assert p.returncode == 3 and "ALERT not_a_byte_prefix_extension counts.jsonl" in p.stdout
    assert (dest / "counts.jsonl").read_bytes() == before
    (out / ".writer" / "counts.jsonl").write_bytes(b"")  # the source got shorter than the staged copy
    p = run(env)
    assert p.returncode == 3 and "staged_longer_than_source" in p.stdout and (dest / "counts.jsonl").read_bytes() == before


def test_refuses_state_tape_traversal_and_sealed_paths_in_code(tmp_path):
    env, out, ops, dest = setup(tmp_path)
    for bad in ("../state.json", "state.json", "tape-2026-11-03T00.jsonl", "gaps.jsonl", "tip-health-../x.jsonl", "counts.jsonl", "trades-2026-11-03T00.jsonl"):
        p = run({**env, "SAMPLERS": bad})
        assert p.returncode == 4 and "REFUSED" in p.stdout, (bad, p.stdout)
    assert not dest.exists()
    for key in ("OUTDIR", "OPS", "DEST"):
        p = run({**env, key: "/var/lib/mal/sealed/fast-trades-tip"})
        assert p.returncode == 4 and "REFUSED" in p.stdout
    assert not dest.exists()


def test_missing_counts_log_is_an_alert_and_stages_nothing_from_it(tmp_path):
    env, out, ops, dest = setup(tmp_path)
    (out / ".writer" / "counts.jsonl").unlink()
    p = run(env)
    assert p.returncode == 1 and "ALERT source_missing counts.jsonl" in p.stdout and not (dest / "counts.jsonl").exists()
