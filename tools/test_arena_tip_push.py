"""Tests for tools/arena_tip_push.py (HV7 tip plan, Transport B): byte-exact push of the writer's hour files fast-0 -> research-0 on tape_mirror's frames, the sha256 / prefix verification of a
closed hour, the allowlists, the relayed writer heartbeat and the consumer's fail-closed check, a real sender/receiver pair over pipes (no ssh), and the isolation drift checks.
SYNTHETIC files in tmp / scratch dirs only: no real tip file, tape, mirror or network.

  /data/mal/venv/bin/python -m pytest tools/test_arena_tip_push.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tools import arena_tip_push as ap
from tools import arena_tip_tape_writer as aw
from tools import tape_mirror as tm
from tools.arena_zst import compact_file

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "arena_tip_push.py"
pytestmark = pytest.mark.skipif(shutil.which("zstd") is None, reason="the zstd CLI is required")
SCRATCH_OK = os.path.isdir(tm.SCRATCH_ROOT) and os.access(tm.SCRATCH_ROOT, os.W_OK)


PINS_LINE = json.dumps({"k": "pins", "sha256": ap.pins_sha()})


def hk(off_h: int) -> str:
    return tm.hour_key(tm.now_ms() + off_h * tm.H_MS)


def line(i: int, t: int) -> bytes:
    return (json.dumps({"v": 1, "type": "trade", "prog": "pumpswap", "slot": 1000 + i, "t_recv_ms": t, "signature": f"sig{i}", "pad": "x" * 200}, separators=(",", ":")) + "\n").encode()


def put_rows(d: Path, h: str, i0: int, n: int) -> int:
    t0 = tm.hour_ms(h) + 1000
    with open(d / tm.open_name(h), "ab") as fh:
        for i in range(i0, i0 + n):
            fh.write(line(i, t0 + i))
    return i0 + n


def close_hour(d: Path, h: str, *, sha: str | None = None) -> None:
    """What the writer does at a close: its counts row (bytes and sha256 of the uncompressed hour), then the compaction."""
    raw = (d / tm.open_name(h)).read_bytes()
    (d / aw.META_DIR).mkdir(exist_ok=True)
    with open(d / aw.META_DIR / "counts.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"hour": h, "bytes": len(raw), "sha256": sha or hashlib.sha256(raw).hexdigest()}) + "\n")
    compact_file(d / tm.open_name(h))


def writer_status(d: Path, now: int, **kw) -> None:
    (d / aw.META_DIR).mkdir(exist_ok=True)
    st = {"schema": aw.SCHEMA_STATUS, "ms": now, "state": "live", "problem": None, "caught_up": True, "decoder_pin": {"ok": True}, **kw}
    aw.write_json_atomic(d / aw.META_DIR / "status.json", st)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """src and dst under tmp_path for the in-process tests (the production roots do not contain pytest's tmp dir)."""
    base = tmp_path / "roots"
    base.mkdir()
    monkeypatch.setattr(ap, "DST_ROOTS", (str(base),))
    monkeypatch.setattr(ap.TipSender, "SRC_ROOTS", (str(base),))
    src, dst = base / "src", base / "dst"
    src.mkdir()
    return src, dst


def pair(src: Path, dst: Path, **kw):
    sink: list[bytes] = []
    since = hk(-6)
    rx = ap.TipReceiver(dst, backfill_hours=6, retain_hours=12, min_retain_hours=1, **kw)
    srv = ap.TipSender(src, since, {}, set(), write=sink.append)
    return srv, rx, sink


def deliver(srv: ap.TipSender, rx: ap.TipReceiver, sink: list[bytes], passes: int = 3) -> None:
    for _ in range(passes):
        srv.poll(force_hb=True)
        data = b"".join(sink)
        sink.clear()
        i = 0
        while i < len(data):
            j = data.index(b"\n", i)
            hdr = json.loads(data[i:j])
            i = j + 1
            n = hdr["n"] if hdr["k"] in ("d", "z") else 0
            rx.handle(hdr, data[i:i + n])
            i += n
        rx.state = "connected"  # what Puller._pump sets on the first frame
        rx.drain_verified(block_s=10 if rx.verifying else 0)


def tree(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir()) if p.is_file() and p.name.startswith("tape-")}


# ------------------------------------------------------------------------------------------------------------------------------------ byte-exact, in process
def test_open_and_closed_hours_arrive_byte_exact_and_the_open_hour_follows_its_compaction(roots):
    src, dst = roots
    closed_h, open_h = hk(-3), hk(0)
    put_rows(src, closed_h, 0, 500)
    close_hour(src, closed_h)
    n = put_rows(src, open_h, 0, 300)
    writer_status(src, tm.now_ms())
    srv, rx, sink = pair(src, dst)
    deliver(srv, rx, sink)
    assert tree(dst) == tree(src) and (dst / tm.closed_name(closed_h)).exists() and rx.closed_verified == 1
    put_rows(src, open_h, n, 200)  # the live tail grows
    deliver(srv, rx, sink)
    assert tree(dst) == tree(src)
    close_hour(src, open_h)  # the writer closes and compacts the hour the receiver is following
    deliver(srv, rx, sink, passes=4)
    assert tree(dst) == tree(src) and sorted(tree(dst)) == sorted([tm.closed_name(closed_h), tm.closed_name(open_h)]) and rx.closed_verified == 2
    rx.close()
    srv.close()


def test_the_closed_hours_sha256_is_checked_and_a_corrupt_chunk_is_never_installed(roots):
    src, dst = roots
    h = hk(-2)
    put_rows(src, h, 0, 400)
    close_hour(src, h)
    srv, rx, sink = pair(src, dst)
    srv.poll(force_hb=True)
    data = b"".join(sink)
    sink.clear()
    frames, i = [], 0
    while i < len(data):
        j = data.index(b"\n", i)
        hdr = json.loads(data[i:j])
        i = j + 1
        n = hdr["n"] if hdr["k"] in ("d", "z") else 0
        frames.append((hdr, bytearray(data[i:i + n])))
        i += n
    z = next(f for f in frames if f[0]["k"] == "z")
    z[1][len(z[1]) // 2] ^= 0xFF  # one byte flipped in transit
    with pytest.raises(tm.Reconnect, match="zst_sha"):
        for hdr, body in frames:
            rx.handle(hdr, bytes(body))
    assert not (dst / tm.closed_name(h)).exists() and not list(dst.glob("*.part"))
    rx.close()
    srv.close()


def test_a_closed_hour_that_does_not_extend_the_live_copy_stops_the_receiver(roots):
    src, dst = roots
    h = hk(-2)
    put_rows(src, h, 0, 100)
    srv, rx, sink = pair(src, dst)
    deliver(srv, rx, sink, passes=1)  # the receiver holds the live .jsonl
    (src / tm.open_name(h)).write_bytes(b"")  # the sender's file is replaced by different content, compacted: not an extension of what the receiver holds
    put_rows(src, h, 5000, 120)
    srv.close()
    close_hour(src, h)
    srv2 = ap.TipSender(src, hk(-6), {}, set(), write=sink.append)
    with pytest.raises(tm.Mismatch, match="closed_hour_diverged|offset|zst"):
        deliver(srv2, rx, sink, passes=4)
    rx.close()
    srv2.close()


def test_a_data_frame_at_the_wrong_offset_or_for_a_closed_hour_is_a_mismatch(roots):
    src, dst = roots
    h = hk(-1)
    put_rows(src, h, 0, 50)
    srv, rx, sink = pair(src, dst)
    deliver(srv, rx, sink, passes=1)
    with pytest.raises(tm.Mismatch, match="offset"):
        rx.handle({"k": "d", "f": tm.open_name(h), "o": 7, "n": 2}, b"x\n")
    with pytest.raises(tm.Mismatch, match="torn_frame"):
        rx.handle({"k": "d", "f": tm.open_name(h), "o": rx.sizes[h], "n": 3}, b"abc")
    rx.close()
    srv.close()


def test_the_writer_heartbeat_is_relayed_in_the_hb_and_the_consumer_check_fails_closed(roots):
    src, dst = roots
    h = hk(0)
    put_rows(src, h, 0, 50)
    now = tm.now_ms()
    writer_status(src, now)
    srv, rx, sink = pair(src, dst)
    deliver(srv, rx, sink)
    rx.write_status()
    assert ap.push_problem(dst) is None and ap.push_problem(dst, need_hours=1) is None
    relay = json.loads((dst / tm.META_DIR / ap.WRITER_RELAY).read_text())
    assert relay["w"]["state"] == "live" and relay["hb_ms"] == rx.writer_hb_ms
    # a writer that is catching up, unpinned, failed or stale
    for patch, code in (({"caught_up": False, "state": "catching_up"}, "not_caught_up"), ({"decoder_pin": {"ok": False}}, "decoder_pin"), ({"state": "failed"}, "state_failed"),
                        ({"problem": "tip:status_stale"}, "writer_problem"), ({"ms": now - 60_000}, "status_stale")):
        writer_status(src, now, **patch)
        deliver(srv, rx, sink, passes=2)
        rx.write_status()
        assert ap.push_problem(dst) == code, (patch, ap.push_problem(dst))
    assert ap.push_problem(dst, need_caught_up=False) in ("status_stale", "writer_problem", "decoder_pin", "state_failed")
    (src / aw.META_DIR / "status.json").unlink()
    deliver(srv, rx, sink, passes=2)
    rx.write_status()
    assert ap.push_problem(dst) == "writer_relay_empty"  # no heartbeat to relay: fail closed
    # a dead transport: the receiver's own status goes stale
    writer_status(src, tm.now_ms())
    deliver(srv, rx, sink, passes=2)
    rx.write_status()
    assert ap.push_problem(dst, now=tm.now_ms() + 60_000) in ("status_stale", "sync_stale")
    (dst / tm.META_DIR / ap.WRITER_RELAY).unlink()
    assert ap.push_problem(dst) == "writer_relay_missing"
    rx.close()
    srv.close()


# ------------------------------------------------------------------------------------------------------------------------------------ allowlists and isolation
def test_the_receivers_dst_is_confined_to_data2_and_never_the_public_tape_or_the_walker_disk(tmp_path):
    ok = "/data2/mal/arena-tip-feed/hv7"
    assert ap.check_dst(ok) == Path(ok)
    for bad in ("/data/mal/arena/tape", "/data/mal/arena/tape/x", "/data/mal/arena/haiku-v7/tip-feed", "/data/mal/arena-tip-feed", "/data2/mal/arena-tip-feed", "/data2/mal/other/x",
                "/data2/mal/arena-tip-feed/probe-feed", "/data2/mal/arena-tip-feed/sealed/x", "/tmp/x", "rel/path", "/data2/mal/arena-tip-feed/../x"):
        with pytest.raises(ValueError):
            ap.check_dst(bad)
    with pytest.raises(ValueError, match="dst_is_public_tape"):
        ap.check_dst("/data/mal/arena/tape", roots=["/data/mal/arena"])
    with pytest.raises(ValueError, match="dst_on_walker_fs"):
        ap.check_dst("/data/mal/arena-tip/x", roots=["/data/mal"])


def test_the_senders_src_is_the_writers_dir_only(tmp_path, monkeypatch):
    base = tmp_path / "b"
    (base / "ok").mkdir(parents=True)
    monkeypatch.setattr(ap.TipSender, "SRC_ROOTS", (str(base),))
    ap.TipSender(base / "ok", hk(-1), {}, set(), write=lambda b: None).close()
    for bad in (tmp_path, "/var/lib/mal/sealed/fast-trades-tip", "/data/mal/arena/tape", str(base / "probe-x")):
        with pytest.raises(tm.Bad):
            ap.TipSender(bad, hk(-1), {}, set(), write=lambda b: None)
    (base / "escape").symlink_to("/var/lib")
    with pytest.raises(tm.Bad):
        ap.TipSender(base / "escape", hk(-1), {}, set(), write=lambda b: None)


def test_no_tape_mirror_internal_drifted_under_the_subclasses(roots):
    """TipSender / TipReceiver replace the __init__ of tape_mirror.Server / Puller (their allowlists). If either base class gains or loses an attribute, this fails."""
    src, dst = roots
    srv, rx, _ = pair(src, dst)
    scratch = Path(tm.SCRATCH_ROOT) / "arena-tip-push-tests" / f"drift{os.getpid()}"
    if SCRATCH_OK:
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            base_srv = tm.Server(scratch, hk(-1), {}, set(), write=lambda b: None)
            assert set(vars(base_srv)) == set(vars(srv)), set(vars(base_srv)) ^ set(vars(srv))
            base_srv.close()
            base_pl = tm.Puller(scratch / "pl", ["true"])
            assert set(vars(base_pl)) - set(vars(rx)) == {"cmd"} or set(vars(base_pl)) <= set(vars(rx)), set(vars(base_pl)) ^ set(vars(rx))
            assert set(vars(rx)) - set(vars(base_pl)) <= {"_out", "_last_ack", "writer_hb_ms", "expect", "_arr", "arrival_path"}
            base_pl.close()
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    else:
        pytest.skip("no writable /data2/mal/scratch for the base classes' own allowlist")
    for name in ("scan", "_live", "_zst", "poll", "_current_hour", "hello", "flush", "close", "_gone"):
        assert getattr(ap.TipSender, name) is getattr(tm.Server, name)
    for name in ("_zchunk", "drain_verified", "hello_line", "prune", "_pump", "status", "write_status", "_startup", "handle", "_lag"):
        assert getattr(ap.TipReceiver, name) is getattr(tm.Puller, name)
    rx.close()
    srv.close()


# ------------------------------------------------------------------------------------------------------------------------------------ a real pair over pipes (no ssh)
needs_scratch = pytest.mark.skipif(not SCRATCH_OK, reason="the real roots need a dir under /data2/mal/scratch")


@pytest.fixture
def scratch():
    if not SCRATCH_OK:
        pytest.skip("no writable /data2/mal/scratch")
    d = Path(tm.SCRATCH_ROOT) / "arena-tip-push-tests" / f"e2e{os.getpid()}"
    shutil.rmtree(d, ignore_errors=True)
    (d / "src").mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def wait_for(pred, timeout: float = 40.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def start_sender(src: Path, dst: Path, **kw):
    cmd = [sys.executable, "-u", str(TOOL), "receive", "--dst", str(dst), "--backfill-hours", "6", "--retain-hours", "192"]
    sd = ap.Sender(src, cmd, **kw)
    stop = threading.Event()
    res: dict = {}
    th = threading.Thread(target=lambda: res.setdefault("rc", sd.run(stop=stop)), daemon=True)
    th.start()
    return sd, stop, th, res


def test_a_real_sender_and_receiver_converge_to_identical_bytes_and_survive_a_reconnect(scratch):
    src, dst = scratch / "src", scratch / "dst"
    closed_h, open_h = hk(-3), hk(0)
    put_rows(src, closed_h, 0, 800)
    close_hour(src, closed_h)
    n = put_rows(src, open_h, 0, 400)
    stop_ws = threading.Event()

    def beat():  # the writer's heartbeat
        while not stop_ws.is_set():
            writer_status(src, tm.now_ms())
            time.sleep(0.5)

    threading.Thread(target=beat, daemon=True).start()
    sd, stop, th, res = start_sender(src, dst)
    try:
        assert wait_for(lambda: dst.exists() and tree(dst) == tree(src) and ap.push_problem(dst) is None), (tree(dst), tree(src), ap.push_problem(dst))
        n = put_rows(src, open_h, n, 300)
        assert wait_for(lambda: tree(dst) == tree(src))
        sd.proc.kill()  # the ssh session dies: the sender reconnects and the receiver resumes from its local sizes
        n = put_rows(src, open_h, n, 200)
        assert wait_for(lambda: sd.connects >= 2 and tree(dst) == tree(src) and ap.push_problem(dst) is None, 60), (sd.connects, ap.push_problem(dst))
        close_hour(src, open_h)
        assert wait_for(lambda: tree(dst) == tree(src) and sorted(tree(dst)) == sorted([tm.closed_name(closed_h), tm.closed_name(open_h)]), 60)
        lag = json.loads((dst / tm.META_DIR / "status.json").read_text())["lag_60s"]
        assert lag["frames"] > 0 and lag["new_p90_ms"] is not None
    finally:
        stop_ws.set()
        stop.set()
        th.join(30)
    assert res.get("rc") == ap.EXIT_OK


def test_a_receiver_mismatch_stops_the_sender_for_good(scratch):
    src = scratch / "src"
    put_rows(src, hk(0), 0, 10)
    hello = json.dumps({"v": 1, "since": hk(-2), "have": {}, "closed": []})
    script = f"import sys,json; print({PINS_LINE!r}, flush=True); print({hello!r}, flush=True); sys.stdin.buffer.read(1); print(json.dumps({{'k':'end','why':'offset','rc':2}}), flush=True); sys.exit(2)"
    sd = ap.Sender(src, [sys.executable, "-u", "-c", script])
    assert sd.run(max_s=30) == ap.EXIT_MISMATCH and sd.state == "stopped_mismatch" and sd.connects == 1


def test_a_local_source_problem_stops_the_sender_fail_closed(scratch):
    src = scratch / "src"
    h = hk(0)
    put_rows(src, h, 0, 50)
    script = f"import sys,json,time; print({PINS_LINE!r}, flush=True); print(json.dumps({{'v':1,'since':{hk(-2)!r},'have':{{}},'closed':[]}}), flush=True); time.sleep(30)"
    sd = ap.Sender(src, [sys.executable, "-u", "-c", script], poll_ms=20)
    threading.Timer(1.0, lambda: (src / tm.open_name(h)).write_bytes(b"short\n")).start()  # the file SHRINKS under the sender
    assert sd.run(max_s=30) == ap.EXIT_MISMATCH and sd.state == "stopped_source_bad"


def test_a_silent_receiver_is_dropped_and_a_second_sender_on_the_dir_is_refused(scratch):
    src = scratch / "src"
    put_rows(src, hk(0), 0, 10)
    script = f"import json,time; print({PINS_LINE!r}, flush=True); print(json.dumps({{'v':1,'since':{hk(-2)!r},'have':{{}},'closed':[]}}), flush=True); time.sleep(60)"
    sd = ap.Sender(src, [sys.executable, "-u", "-c", script], ack_silence_ms=1500)
    with pytest.raises(tm.Locked):
        ap.Sender(src, ["true"])
    sd.run(max_s=6)
    assert sd.connects >= 2 and sd.events >= 1  # ack silence -> disconnect -> reconnect


# ------------------------------------------------------------------------------------------------------------------------------------ QP-RULING-1101-arena-tip-reader B2 / B5
def test_the_closed_hour_must_equal_the_writers_record_not_only_the_senders_file(roots):
    src, dst = roots
    h = hk(-2)
    put_rows(src, h, 0, 300)
    close_hour(src, h, sha="0" * 64)  # the writer's record says a different content than the .zst holds
    srv, rx, sink = pair(src, dst)
    with pytest.raises(tm.Mismatch, match="closed_hour_diverged"):
        deliver(srv, rx, sink, passes=4)
    assert not (dst / tm.closed_name(h)).exists()  # a failed check stalls: nothing is installed, nothing is skipped
    rx.close()
    srv.close()


def test_a_closed_hour_without_the_writers_record_is_not_sent_and_a_ze_without_one_is_refused(roots):
    src, dst = roots
    h = hk(-2)
    put_rows(src, h, 0, 100)
    compact_file(src / tm.open_name(h))  # no counts row
    srv, rx, sink = pair(src, dst)
    srv.poll(force_hb=True)
    data = b"".join(sink)
    assert b'"k":"z"' not in data and b'"k":"ze"' not in data
    with pytest.raises(tm.Mismatch, match="closed_hour_no_record"):
        rx.handle({"k": "ze", "f": tm.closed_name(h), "size": 1, "sha": "0" * 64}, b"")
    rx.close()
    srv.close()


def test_d_frames_carry_the_newest_pumpswap_t_and_the_receiver_logs_it_with_its_arrival(roots):
    src, dst = roots
    h = hk(0)
    t0 = tm.hour_ms(h) + 1000
    rows = [json.loads(line(i, t0 + i)) for i in range(5)]
    rows[4]["prog"] = "pumpfun"  # the newest row is a bonding row: the newest PumpSwap one is row 3
    rows.append({"v": 1, "type": "gap", "prog": "pumpswap", "t_recv_ms": t0 + 99, "kind": "x"})  # a gap row is not a data row
    (src / tm.open_name(h)).write_bytes(b"".join((json.dumps(r, separators=(",", ":")) + "\n").encode() for r in rows))
    srv, rx, sink = pair(src, dst)
    srv.poll(force_hb=True)
    hdrs = [o for o in (json.loads(ln) for ln in b"".join(sink).split(b"\n") if ln.strip()) if o.get("k") == "d"]
    assert hdrs and hdrs[0]["pt"] == t0 + 3 and hdrs[0]["mt"] is not None  # mt = the writer's write instant of the newest byte
    deliver(srv, rx, sink)
    rx.flush_arrival()
    arr = [json.loads(ln) for ln in (dst / tm.META_DIR / "arrival.jsonl").read_text().splitlines()]
    assert arr and arr[-1]["newest_pumpswap_t_recv_ms"] == t0 + 3 and arr[-1]["newest_pumpswap_arrival_ms"] >= arr[-1]["newest_source_write_ms"]
    assert ap.newest_pumpswap_t(b"") is None and ap.newest_pumpswap_t(b'{"prog":"pumpfun","type":"trade","t_recv_ms":5}\n') is None
    with pytest.raises(tm.Mismatch, match="bad_pt"):
        rx.handle({"k": "d", "f": tm.open_name(h), "o": rx.sizes[h], "n": 2, "pt": "x"}, b"x\n")
    rx.close()
    srv.close()


def test_acks_list_the_verified_hours_and_the_sender_keeps_their_first_seen_instant(scratch):
    src, dst = scratch / "src", scratch / "dst"
    closed_h, open_h = hk(-3), hk(0)
    put_rows(src, closed_h, 0, 300)
    close_hour(src, closed_h)
    put_rows(src, open_h, 0, 100)
    stop_ws = threading.Event()
    threading.Thread(target=lambda: [writer_status(src, tm.now_ms()) or time.sleep(0.5) for _ in iter(stop_ws.is_set, True)], daemon=True).start()
    sd, stop, th, res = start_sender(src, dst)
    try:
        assert wait_for(lambda: (src / ap.PUSH_DIR / "verified.json").exists() and closed_h in json.loads((src / ap.PUSH_DIR / "verified.json").read_text())["hours"])
        v = json.loads((src / ap.PUSH_DIR / "verified.json").read_text())["hours"]
        assert set(v) == {closed_h} and abs(v[closed_h] - tm.now_ms()) < 60_000
    finally:
        stop_ws.set()
        stop.set()
        th.join(30)
    sd2 = ap.Sender(src, ["true"])  # a restarted sender keeps the instants
    assert sd2.verified == v
    sd2.close()


# ------------------------------------------------------------------------------------------------------------------------------------ the job scripts
def run_sh(script: str, **env) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k not in ("OUT", "SRC", "REMOTE_DST", "REMOTE_REPO", "CATCH_UP_H", "DRY")}
    e.update(env)
    return subprocess.run(["sh", str(REPO / "scripts" / "ops" / script)], capture_output=True, text=True, env=e, timeout=60)


@pytest.mark.parametrize("script", ["arena-tip-writer-job.sh", "arena-tip-push-job.sh"])
def test_the_job_scripts_are_posix_sh(script):
    assert subprocess.run(["sh", "-n", str(REPO / "scripts" / "ops" / script)]).returncode == 0
    assert subprocess.run(["bash", "-n", str(REPO / "scripts" / "ops" / script)]).returncode == 0
    text = (REPO / "scripts" / "ops" / script).read_text()
    assert text.startswith("#!/bin/sh") and "PIPESTATUS" not in text and "[[" not in text and "exec " not in text.split("set -u", 1)[1]  # rc is kept: no exec of the job


def test_the_writer_job_dry_run_and_refusals():
    r = run_sh("arena-tip-writer-job.sh", DRY="1")
    assert r.returncode == 0 and "nice -n 19 ionice -c3" in r.stdout and "MemoryMax=1536M" in r.stdout and "--catch-up-hours 70" in r.stdout
    for env in ({"OUT": "/data/mal/x"}, {"OUT": "/var/lib/mal/sealed/x"}, {"OUT": "/home/claude/mal-data/tip-tape-a"}, {"OUT": "/tmp/x"}, {"CATCH_UP_H": "71"}, {"CATCH_UP_H": "x"}):
        r = run_sh("arena-tip-writer-job.sh", DRY="1", **env)
        assert r.returncode == 2 and r.stdout.startswith("REFUSED"), (env, r.stdout)


def test_the_push_job_dry_run_and_refusals():
    r = run_sh("arena-tip-push-job.sh", DRY="1", REMOTE_REPO="/data2/mal/wt/x")
    assert r.returncode == 0 and "/data2/mal/arena-tip-feed/hv7" in r.stdout and "nice -n 19 ionice -c3" in r.stdout
    for env in ({}, {"REMOTE_REPO": "/x", "REMOTE_DST": "/data/mal/arena-tip-feed/hv7"}, {"REMOTE_REPO": "/x", "REMOTE_DST": "/data2/mal/other/x"}, {"REMOTE_REPO": "/x/../y"},
                {"REMOTE_REPO": "/x", "SRC": "/data/mal/arena/tape"}):
        r = run_sh("arena-tip-push-job.sh", DRY="1", **env)
        assert r.returncode == 2 and r.stdout.startswith("REFUSED"), (env, r.stdout)


def test_the_job_scripts_keep_the_exit_status_and_forward_a_cancel(tmp_path):
    """The shared tail (background job, trap, wait, rc) run on a stand-in command: rc 7 comes back as 7, and a SIGTERM reaches the child."""
    tail = "PID=\nSTOP=0" + (REPO / "scripts" / "ops" / "arena-tip-push-job.sh").read_text().split("PID=\nSTOP=0", 1)[1].split("exit \"$RC\"", 1)[0]
    body = '#!/bin/sh\nset -- sh -c "$CHILD"\n' + tail + 'exit "$RC"\n'
    sc = tmp_path / "t.sh"
    sc.write_text(body)
    r = subprocess.run(["sh", str(sc)], env={**os.environ, "CHILD": "exit 7"}, capture_output=True, text=True, timeout=30)
    assert r.returncode == 7 and "rc=7" in r.stdout
    p = subprocess.Popen(["sh", str(sc)], env={**os.environ, "CHILD": "trap 'exit 3' TERM; sleep 30 & wait"}, stdout=subprocess.PIPE, text=True)
    time.sleep(1.0)
    p.terminate()
    out, _ = p.communicate(timeout=30)
    assert p.returncode == 3 and "rc=3" in out


def test_the_test_only_flags_cannot_reach_production(monkeypatch):
    monkeypatch.delenv("MAL_ARENA_TEST", raising=False)
    assert ap.main(["send", "--src", "/x", "--cmd-json", "[\"true\"]"]) == ap.EXIT_REFUSED
    assert aw.main(["run", "--no-compress", "--out-dir", str(Path(aw.OUT_ROOTS[0]) / "x" / "tape")]) == aw.EXIT_REFUSED


# ------------------------------------------------------------------------------------------------------------------------------------ QP on #1167: C3 (the book reads 7 days of history)
def test_c3_the_receiver_keeps_at_least_seven_days_plus_a_day_and_refuses_less(roots, tmp_path):
    src, dst = roots
    assert ap.RETAIN_HOURS_DEFAULT == 240 and ap.BACKFILL_HOURS_DEFAULT == 240 and ap.RETAIN_MIN_H == 192
    rx = ap.TipReceiver(dst)  # the defaults
    assert rx.retain_hours == 240 and rx.backfill_hours == 240
    rx.close()
    for bad in (191, 72, 1):
        with pytest.raises(ValueError, match="retain_hours_below_192"):
            ap.TipReceiver(dst.parent / "d2", retain_hours=bad, backfill_hours=1)
    ap.TipReceiver(dst.parent / "d3", retain_hours=192, backfill_hours=192).close()
    assert ap.main(["send", "--src", str(src), "--cmd-json", "[\"true\"]", "--retain-hours", "100"]) == ap.EXIT_REFUSED  # refused before dialing (the test flag is set by the module env)
    rx2 = ap.TipReceiver(dst.parent / "d4", retain_hours=240, backfill_hours=240)
    h_old, h_new = hk(-200), hk(-1)
    for h in (h_old, h_new):
        (dst.parent / "d4" / tm.open_name(h)).write_bytes(line(0, tm.hour_ms(h) + 1))
    rx2.prune(force=True)  # 200 h old is inside the 240 h retention: kept (it would have been deleted at 72 h)
    assert (dst.parent / "d4" / tm.open_name(h_old)).exists() and (dst.parent / "d4" / tm.open_name(h_new)).exists()
    rx2.close()


def test_c3_the_push_job_defaults_and_refuses_the_same_way():
    r = run_sh("arena-tip-push-job.sh", DRY="1", REMOTE_REPO="/x")
    assert "--retain-hours 240 --backfill-hours 240" in r.stdout and "MemoryMax=256M" in r.stdout and "MemorySwapMax=0" in r.stdout
    for env in ({"RETAIN_H": "191"}, {"RETAIN_H": "72"}, {"RETAIN_H": "240", "BACKFILL_H": "241"}):
        r = run_sh("arena-tip-push-job.sh", DRY="1", REMOTE_REPO="/x", **env)
        assert r.returncode == 2 and r.stdout.startswith("REFUSED"), (env, r.stdout)
    assert run_sh("arena-tip-push-job.sh", DRY="1", REMOTE_REPO="/x", RETAIN_H="192").returncode == 0


# ------------------------------------------------------------------------------------------------------------------------------------ reviewer on #1167: must-fix 1, 2; S5
def test_a_late_started_push_still_delivers_the_writers_first_hours(roots):
    src, dst = roots
    old = [hk(-100), hk(-99), hk(-5)]  # the writer's H0 hours; the push starts 100 h later
    for h in old:
        put_rows(src, h, 0, 200)
        close_hour(src, h)
    put_rows(src, hk(0), 0, 50)
    rx = ap.TipReceiver(dst)  # defaults: retain 240, backfill 240
    since, have, closed = tm.parse_hello(rx.hello_line())
    assert since <= old[0]
    sink: list[bytes] = []
    srv = ap.TipSender(src, since, have, closed, write=sink.append)
    deliver(srv, rx, sink, passes=6)
    assert {tm.closed_name(h) for h in old} <= set(tree(dst)) and rx.closed_verified == 3
    rx.close()
    srv.close()


def test_the_counts_record_lookup_is_incremental_and_held_hours_cost_no_lookup(roots, monkeypatch):
    src, dst = roots
    hours = [hk(-k) for k in range(10, 4, -1)]
    for h in hours:
        put_rows(src, h, 0, 20)
        close_hour(src, h)
    srv = ap.TipSender(src, hk(-12), {}, set(), write=lambda b: None)
    assert srv.record(hours[0])["bytes"] > 0 and srv._rec_off == (src / aw.META_DIR / "counts.jsonl").stat().st_size
    reads = []
    real_pread = os.pread
    monkeypatch.setattr(os, "pread", lambda fd, n, off: reads.append((n, off)) or real_pread(fd, n, off))
    assert srv.record(hours[-1]) is not None and reads == []  # nothing appended: no read at all
    h_new = hk(-3)
    put_rows(src, h_new, 0, 20)
    close_hour(src, h_new)
    assert srv.record(h_new) is not None and len(reads) == 1 and reads[0][1] > 0  # only the appended bytes
    srv.closed = set(hours)
    monkeypatch.setattr(srv, "record", lambda h: (_ for _ in ()).throw(AssertionError("looked up a held hour")))
    for h in hours:
        srv._queue(h)  # held by the receiver: Server.scan asks every second, and it must cost nothing
    srv.close()


def test_s5_the_receivers_code_is_compared_at_every_session_and_a_difference_stops_the_sender(scratch):
    src = scratch / "src"
    put_rows(src, hk(0), 0, 10)
    bad = dict(ap.pins_sha())
    bad["tools/arena_tip_tape_writer.py"] = "0" * 64
    script = f"import json,time; print({json.dumps({'k': 'pins', 'sha256': bad})!r}, flush=True); time.sleep(30)"
    sd = ap.Sender(src, [sys.executable, "-u", "-c", script])
    assert sd.run(max_s=30) == ap.EXIT_MISMATCH and sd.state == "stopped_pin_mismatch" and sd.connects == 1
    sd2 = ap.Sender(src, [sys.executable, "-u", "-c", "print('{}', flush=True)"])  # no pins line at all: not a hello either
    assert sd2.run(max_s=4) == ap.EXIT_OK and sd2.connects >= 1 and sd2.state in ("stopped", "disconnected")


def test_the_relay_without_a_writer_heartbeat_is_never_replaced_by_a_file_of_the_dst(roots):
    src, dst = roots
    put_rows(src, hk(0), 0, 20)  # no writer status in src: the hb carries w = null
    srv, rx, sink = pair(src, dst)
    deliver(srv, rx, sink)
    rx.write_status()
    (dst / aw.META_DIR).mkdir()
    writer_status(dst, tm.now_ms())  # a lookalike file in the dst must not be believed
    assert ap.push_problem(dst) == "writer_relay_empty"
    rx.close()
    srv.close()
