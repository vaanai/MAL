#!/usr/bin/env python3
"""hv4_c0_twin_relay: carries the HV4 C0 paper twin from mal-research-0 (where the HV4 paper ledger is) to mal-fast-0 (where the live bridge runs).
PAPER ONLY: no RPC, no key, no wallet. A build, not gate evidence. No book has cleared the promotion gate. Design: docs/research/p46-transport/TWINS.md.

Why: the bridge's twin gate (tools/h5_arena_gates.LiveGates.twin_refusal) reads `twins_file` and `twins_file + ".run.json"` on fast-0. The twin is made by
tools/hv4_c0_twin.py from the bridge's trips file (fast-0) AND the bot's paper ledger (research-0, readable only after the bot's recorded PASS). research-0 has no
credential to reach fast-0, so fast-0 PULLS over `ssh mal-research-0` (the P46 pattern of tools/decision_forwarder.py): trips go up, the twin comes down.

  serve   research-0. In production an authorized_keys forced command (`serve --from-env`; SSH_ORIGINAL_COMMAND is exactly `twin <now_ms>`). Reads ONE trips
          JSONL from stdin (at most --max-trips-bytes; every line is checked against the recorder's trip schema; anything else is refused), writes it to a private
          tmp dir, runs tools.hv4_c0_twin UNCHANGED with --ledger-dir / --reads-dir / --bot-id from the flags compiled into the forced command (never from stdin) and
          --now-ms = the request's instant, and prints exactly ONE line on stdout: a JSON reply that carries the twins file bytes and the run record bytes (base64, each
          with its length and sha256), or {"ok": false, "refused": CODE}. The wrapper's own read-record gate decides whether the sealed ledger may be opened (QP-RULING-1025
          R2.1: this tool adds no second path to the ledger and never reads one itself). Nothing but the twin rows and their run record leaves research-0: the wrapper's
          stdout and stderr are captured and reduced to a short code from a fixed vocabulary.
  client  fast-0. Single instance (flock). Every --interval-s (default 10, at most 20) and sooner when the trips file changed, sends the current trips file, verifies the
          whole reply, and only then replaces `twins_file` and its run record (tmp + fsync + rename; targets must be regular files with st_nlink 1). A refusal, a timeout or
          a bad reply leaves the old files untouched, so the gate goes stale and fails closed; it never fails open. A status file `<twins_file>.relay-status.json` sits beside it.

FRESHNESS. The twin stamp the bridge uses is the run record's `run_ms` (h5_arena_gates.twin_run_stamp); arena_live_parity.state_problems calls it stale after 60 s, in
the future after 5 s, and older than the last landed tx. The client picks `now_ms` BEFORE it reads the trips file, serve passes it as --now-ms, and the client accepts the
reply only if `run_ms == now_ms`. So the stamp is on the bridge host's own clock (no cross-host skew), is never later than the trips snapshot it covers, and an old reply
cannot be replayed as a new one. serve independently refuses a request whose now_ms is more than 15 s from research-0's clock. A healthy loop's stamp is at most
interval + one cycle old (interval <= 20 s and cycle <= timeout <= 25 s, so at most 45 s, inside the 60 s bound with a 15 s margin).
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import io
import json
import math
import os
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Sequence

REPLY_SCHEMA = "hv4_c0_twin_relay_reply_v1"
STATUS_SCHEMA = "hv4_c0_twin_relay_status_v1"
RUN_SCHEMA = "arena_twin_run_v1"  # tools/arena_twin_writer.RUN_SCHEMA (a string here: the client imports nothing from the writer; a test compares)
SEALED_ROOT_PREFIX = "/data/mal/arena"
REQUEST_RE = re.compile(r"^twin ([0-9]{13})$")
CODE_RE = re.compile(r"^[a-z0-9_]{1,64}(:[a-z0-9_]{1,64}){0,2}$")  # every code that crosses the wire or lands in a status file
TRIP_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")

MAX_TRIPS_BYTES = 1 << 20  # about 2,500 trips; the recorder never prunes, a C0 canary books a few per day
MAX_LINE_BYTES = 8192
MAX_TWINS_BYTES = 8 << 20
MAX_RUN_BYTES = 1 << 20
MAX_REPLY_BYTES = 16 << 20
MS_MAX = 4_102_444_800_000  # 2100-01-01

STALE_MS = 60_000  # tools/arena_live_parity.STALE_MS (a test compares): the bridge calls a twin stamp older than this stale
INTERVAL_S_DEFAULT, INTERVAL_S_MAX = 10.0, 20.0
TIMEOUT_S_DEFAULT, TIMEOUT_S_MAX = 20.0, 25.0
STALE_MARGIN_S = 15.0
assert INTERVAL_S_MAX + TIMEOUT_S_MAX + STALE_MARGIN_S <= STALE_MS / 1000  # a healthy loop (every cycle inside its timeout) never ages the stamp past the bound
POLL_S_DEFAULT, MIN_GAP_S_DEFAULT = 1.0, 2.0
FAIL_BACKOFF_S = (2.0, 4.0, 8.0)  # after a failed cycle the next try comes sooner than the interval, never later
MAX_REQ_SKEW_MS = 15_000  # serve refuses a request whose now_ms is further than this from research-0's clock
SERVE_DEADLINE_S = 30
SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3")  # as tools/decision_forwarder.SSH_OPTS (a test compares)
EXIT_OK, EXIT_BAD, EXIT_REFUSED = 0, 2, 3


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _c0():
    from tools import hv4_live_c0 as c0  # stdlib-only module; lazy so that this file also runs as a plain script
    return c0


def _under_sealed(path: str) -> bool:
    real = os.path.realpath(path)
    return real == SEALED_ROOT_PREFIX or real.startswith(SEALED_ROOT_PREFIX + "/")


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Bad(Exception):
    """A refusal with a short code (CODE_RE). `kind` is "refused" (the remote said no) or "error" (transport / reply / local problem)."""

    def __init__(self, code: str, kind: str = "error"):
        super().__init__(code)
        self.code, self.kind = code, kind


# ---------------------------------------------------------------------------------------------------------------------------------- strict JSON
def _reject_constant(name: str) -> Any:
    raise ValueError(f"constant {name}")


def _no_dups(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"duplicate key {k}")
        d[k] = v
    return d


def strict_loads(b: bytes | str) -> Any:
    """json.loads that refuses NaN / Infinity and duplicate keys."""
    return json.loads(b, parse_constant=_reject_constant, object_pairs_hook=_no_dups)


# ---------------------------------------------------------------------------------------------------------------------------------- trips schema
def _int(lo: int, hi: int) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _price(v: Any) -> bool:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        f = float(v)
    except OverflowError:
        return False
    return math.isfinite(f) and 0.0 <= f <= 1e30


_ms = _int(0, MS_MAX)
_bool: Callable[[Any], bool] = lambda v: isinstance(v, bool)  # noqa: E731
TRIP_FIELDS: dict[str, Callable[[Any], bool]] = {  # the recorder's trip rows (tools/h5_arena_gates.ParityRecorder) as arena_live_parity.validate_inputs reads them
    "trip_id": lambda v: isinstance(v, str) and bool(TRIP_ID_RE.match(v)),
    "stake_lamports": _int(1, 10**15), "decision_ms": _ms, "buy_landed_ms": _ms, "exit_bound_ms": _ms, "exit_landed_ms": _ms, "closed_ms": _ms,
    "live_buy_price": _price, "live_sell_price": _price, "live_net_lamports": _int(-(10**15), 10**15),
    "buy_fee_lamports": _int(0, 10**12), "sell_fee_lamports": _int(0, 10**12),
    "gap": _bool, "exit_landed": _bool, "unreadable": _bool, "gap_through": _bool,
    "exit_reason": lambda v: isinstance(v, str) and 0 < len(v) <= 64 and v.isprintable(),
}
TRIP_REQUIRED = ("trip_id", "stake_lamports", "decision_ms", "buy_landed_ms", "live_buy_price", "gap", "exit_landed", "exit_bound_ms")
TRIP_EXITED = ("live_net_lamports", "live_sell_price", "exit_landed_ms", "exit_reason")


def trip_problem(row: Any) -> str | None:
    """None, or the name of the first bad field (a name from TRIP_FIELDS, so it is safe to put in a code)."""
    if not isinstance(row, dict):
        return "not_object"
    for k in row:
        if k not in TRIP_FIELDS:
            return "unknown_key"
    for k in TRIP_REQUIRED:
        if k not in row:
            return k
    if row["exit_landed"] is True:
        for k in TRIP_EXITED:
            if k not in row:
                return k
    for k, v in row.items():
        if not TRIP_FIELDS[k](v):
            return k
    return None


def validate_trips(raw: bytes, max_bytes: int = MAX_TRIPS_BYTES) -> tuple[list[dict[str, Any]], str | None]:
    """(rows, None) for a trips JSONL the recorder could have written, else ([], code). Empty input is a valid 'no trips yet'. Newline-terminated lines only, no blank
    lines, each line one JSON object with only the recorder's keys and the right types, no duplicate keys, no duplicate trip ids."""
    if len(raw) > max_bytes:
        return [], "trips_too_large"
    if not raw:
        return [], None
    if not raw.endswith(b"\n"):
        return [], "trips_no_final_newline"
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n, line in enumerate(raw.split(b"\n")[:-1], 1):
        if not line.strip():
            return [], f"trips_blank_line:line{n}"
        if len(line) > MAX_LINE_BYTES:
            return [], f"trips_line_too_long:line{n}"
        if b"\r" in line:  # the wrapper and the parity tool read with str.splitlines(): a raw CR would cut the line in two
            return [], f"trips_bad_json:line{n}"
        try:
            row = strict_loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return [], f"trips_bad_json:line{n}"
        why = trip_problem(row)
        if why:
            return [], f"trips_bad_field:{why}:line{n}"
        if row["trip_id"] in seen:
            return [], f"trips_duplicate_id:line{n}"
        seen.add(row["trip_id"])
        rows.append(row)
    return rows, None


# ---------------------------------------------------------------------------------------------------------------------------------- the pair check (both ends)
def verify_pair(twins: bytes, run: bytes, *, bot_id: str, trip_ids: Sequence[str], now_ms_: int, pins: tuple[str, str]) -> str | None:
    """None when (twins, run) is a twin pair the bridge's gate will accept for exactly these trips; else a code. Used by serve on the wrapper's output AND by the client
    on the reply (the client trusts nothing it did not check): run record right schema, `run_ms` == the request instant, `covered_trip_ids` == exactly the trips sent,
    `twins_sha256` == sha256(twins), `twin_rows` == the row count, config_hash / code_sha256 == the pins (a wrong pin would latch the bridge for good); every twin row is a
    JSON object for a trip that was sent, once, and (when it names a bot) for this bot."""
    try:
        rec = strict_loads(run)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return "run_record_unreadable"
    if not isinstance(rec, dict) or rec.get("schema") != RUN_SCHEMA:
        return "run_record_schema"
    ms = rec.get("run_ms")
    if isinstance(ms, bool) or not isinstance(ms, int) or ms != now_ms_:
        return "run_ms_mismatch"
    ids = sorted(trip_ids)
    if rec.get("covered_trip_ids") != ids:
        return "covered_mismatch"
    if rec.get("twins_sha256") != sha256_hex(twins):
        return "twins_sha_mismatch"
    if (rec.get("config_hash"), rec.get("code_sha256")) != tuple(pins):
        return "pin_mismatch"
    if twins and not twins.endswith(b"\n"):
        return "twins_no_final_newline"
    want = set(ids)
    seen: set[str] = set()
    n = 0
    try:
        text = twins.decode("utf-8")
        lines = text.splitlines()
        if len(lines) != text.count("\n"):  # a separator str.splitlines() honours but "\n" is not: the gate would read other lines than we checked
            return "twins_row_invalid"
        for line in lines:
            row = strict_loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("trip_id"), str) or row["trip_id"] not in want or row["trip_id"] in seen:
                return "twins_row_invalid"
            if row.get("bot_id") not in (None, bot_id) or ("price_failed" in row and not isinstance(row["price_failed"], bool)):
                return "twins_row_invalid"
            seen.add(row["trip_id"])
            n += 1
    except (UnicodeDecodeError, ValueError, RecursionError):
        return "twins_unreadable"
    if rec.get("twin_rows") != n or isinstance(rec.get("twin_rows"), bool):
        return "twin_rows_mismatch"
    return None


# ---------------------------------------------------------------------------------------------------------------------------------- serve (research-0)
class _Deadline(BaseException):
    """Raised by the SIGALRM handler. A BaseException so the wrapper's own `except Exception` blocks cannot swallow it."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # a reply line must always be printed: no usage text, no exit 2 from argparse
        raise Bad("bad_arguments", "refused")


def _refusal_reply(code: str) -> dict[str, Any]:
    return {"schema": REPLY_SCHEMA, "ok": False, "refused": code if CODE_RE.match(code) else "refused_unparsed"}


def twin_refusal_code(rc: Any, err_text: str) -> str:
    """The wrapper's stderr reduced to a code from a fixed vocabulary. Never the text: a refusal message can name a ledger file or a trade id, and nothing but the twin
    rows may leave research-0."""
    for line in err_text.splitlines():
        if line.startswith("hv4_c0_twin:") and "REFUSED" in line:
            why = line.split("REFUSED", 1)[1].strip()
            if why.startswith("unusable arguments"):
                return "twin_refused:bad_arguments"
            parts = why.split(" ")[0].split(":")
            if parts[0] in ("ledger", "write") and len(parts) >= 3:
                head = f"{parts[0]}:{parts[-1]}"  # the file name in the middle is dropped, the reason kept
            elif parts[0] in ("trips", "ledger_dir_sealed") and len(parts) >= 2:
                head = ":".join(parts[:2])
            else:
                head = parts[0]
            code = f"twin_refused:{head}"
            return code if CODE_RE.match(code) else "twin_refused"
        if "UNUSABLE INPUT" in line:
            return "twin_unusable_input"
    return f"twin_rc_{rc}" if isinstance(rc, int) and not isinstance(rc, bool) else "twin_rc_unknown"


def default_twin_main(argv: list[str]) -> int:
    from tools import hv4_c0_twin  # numpy and the pinned writer load here, not at import of this module
    return hv4_c0_twin.main(argv)


def _read_regular(path: Path, cap: int) -> bytes:
    ls = os.lstat(path)
    if not stat.S_ISREG(ls.st_mode) or ls.st_nlink != 1 or ls.st_size > cap:
        raise OSError("not a plain file within the cap")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        b = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            b += chunk
            if len(b) > cap:
                raise OSError("over the cap")
        return b
    finally:
        os.close(fd)


def serve_once(stdin: BinaryIO, request: str | None, *, bot_id: str, ledger_dir: str, reads_dir: str | None, tmp_root: str | None = None,
               max_trips_bytes: int = MAX_TRIPS_BYTES, twin_main: Callable[[list[str]], int] | None = None, clock_ms: Callable[[], int] = now_ms) -> dict[str, Any]:
    """One request. Returns the reply dict (never raises, except _Deadline is turned into a refusal)."""
    work: str | None = None
    try:
        c0 = _c0()
        why = c0.bot_id_problem(bot_id) or c0.arm_refusal(bot_id)
        if why:
            return _refusal_reply(why)
        m = REQUEST_RE.match(request or "")
        if not m:
            return _refusal_reply("bad_request")
        req_ms = int(m.group(1))
        if abs(req_ms - clock_ms()) > MAX_REQ_SKEW_MS:
            return _refusal_reply("request_clock_skew")
        raw = stdin.read(max_trips_bytes + 1)
        trips, why = validate_trips(raw, max_trips_bytes)
        if why:
            return _refusal_reply(why)
        work = tempfile.mkdtemp(prefix="hv4-c0-twin-relay-", dir=tmp_root)  # 0700, private to this process's user
        trips_p, out_p, rec_p = os.path.join(work, "trips.jsonl"), os.path.join(work, "twins.jsonl"), os.path.join(work, "twins.jsonl.run.json")
        fd = os.open(trips_p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        argv = [f"--trips={trips_p}", f"--ledger-dir={ledger_dir}", f"--bot-id={bot_id}", f"--out={out_p}", f"--run-record={rec_p}", f"--now-ms={req_ms}"]
        if reads_dir:
            argv.append(f"--reads-dir={reads_dir}")
        out_buf, err_buf = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):  # the reply is the only thing on stdout
                rc: Any = (twin_main or default_twin_main)(argv)
        except ImportError:
            return _refusal_reply("twin_import_failed")
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
        except Exception:  # noqa: BLE001 - nothing may escape serve; the twin was not made
            return _refusal_reply("twin_crashed")
        if rc != 0:
            return _refusal_reply(twin_refusal_code(rc, err_buf.getvalue()))
        try:
            twins, run = _read_regular(Path(out_p), MAX_TWINS_BYTES), _read_regular(Path(rec_p), MAX_RUN_BYTES)
        except OSError:
            return _refusal_reply("twin_output_missing")
        bad = verify_pair(twins, run, bot_id=bot_id, trip_ids=[t["trip_id"] for t in trips], now_ms_=req_ms, pins=c0.twin_pins())
        if bad:
            return _refusal_reply(f"twin_output_invalid:{bad}")
        return {"schema": REPLY_SCHEMA, "ok": True, "bot_id": bot_id, "now_ms": req_ms, "req_sha256": sha256_hex(raw),
                "twins": {"len": len(twins), "sha256": sha256_hex(twins), "b64": base64.b64encode(twins).decode("ascii")},
                "run": {"len": len(run), "sha256": sha256_hex(run), "b64": base64.b64encode(run).decode("ascii")}}
    except _Deadline:
        return _refusal_reply("serve_timeout")
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


def _serve_parser() -> argparse.ArgumentParser:
    s = _Parser(prog="hv4_c0_twin_relay serve", allow_abbrev=False)
    s.add_argument("--bot-id", required=True)
    s.add_argument("--ledger-dir", required=True, help="compiled into the forced command: <arena>/ledger/<bot_id>; hv4_c0_twin refuses any other dir and opens a sealed one only past the read-record gate")
    s.add_argument("--reads-dir", default=None, help="compiled into the forced command: the recorded-read dir the wrapper's gate checks")
    s.add_argument("--from-env", action="store_true", help="forced-command use: the request is SSH_ORIGINAL_COMMAND, exactly `twin <now_ms>`")
    s.add_argument("--now-ms", type=int, default=None, help="tests and manual runs: the request instant (not with --from-env)")
    s.add_argument("--tmp-root", default=None)
    s.add_argument("--max-trips-bytes", type=int, default=MAX_TRIPS_BYTES)
    s.add_argument("--deadline-s", type=int, default=SERVE_DEADLINE_S)
    return s


def serve_main(argv: Sequence[str], *, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None, environ: Mapping[str, str] | None = None,
               twin_main: Callable[[list[str]], int] | None = None, clock_ms: Callable[[], int] = now_ms, use_alarm: bool = False) -> int:
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout.buffer
    env = os.environ if environ is None else environ
    try:
        a = _serve_parser().parse_args(list(argv))
        if a.from_env == (a.now_ms is not None):
            raise Bad("bad_arguments", "refused")  # exactly one source for the request instant
        if not 1 <= a.max_trips_bytes <= MAX_TRIPS_BYTES or not 1 <= a.deadline_s <= 300:
            raise Bad("bad_arguments", "refused")
        request = env.get("SSH_ORIGINAL_COMMAND") if a.from_env else f"twin {a.now_ms}"
    except Bad as exc:
        reply = _refusal_reply(exc.code)
    else:
        if use_alarm:
            def _alarm(*_: Any) -> None:
                raise _Deadline()
            signal.signal(signal.SIGALRM, _alarm)
            signal.alarm(a.deadline_s)
        try:
            reply = serve_once(stdin, request, bot_id=a.bot_id, ledger_dir=a.ledger_dir, reads_dir=a.reads_dir, tmp_root=a.tmp_root,
                               max_trips_bytes=a.max_trips_bytes, twin_main=twin_main, clock_ms=clock_ms)
        except _Deadline:
            reply = _refusal_reply("serve_timeout")
        finally:
            if use_alarm:
                signal.alarm(0)
    try:
        stdout.write(json.dumps(reply, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n")
        stdout.flush()
    except BrokenPipeError:  # the client is gone (its timeout); nothing to tell it
        return EXIT_REFUSED
    return EXIT_OK if reply.get("ok") else EXIT_REFUSED


# ---------------------------------------------------------------------------------------------------------------------------------- client (fast-0)
def run_bounded(argv: Sequence[str], data: bytes, timeout_s: float, max_out: int, stop: threading.Event | None = None) -> tuple[int | None, bytes, bytes, str | None]:
    """Runs argv with `data` on stdin. Returns (rc, stdout, stderr head, why). why is None, "spawn_failed", "timeout", "stopped" or "reply_too_large"; on any why the
    whole process group is killed. stdout is read with a hard cap, so a runaway remote cannot fill memory."""
    try:
        p = subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except OSError:
        return None, b"", b"", "spawn_failed"
    err = bytearray()

    def feed() -> None:
        try:
            assert p.stdin is not None
            p.stdin.write(data)
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError, ValueError):
                p.stdin.close()  # type: ignore[union-attr]

    def drain() -> None:
        try:
            assert p.stderr is not None
            while True:
                b = p.stderr.read(4096)
                if not b:
                    return
                if len(err) < 2048:
                    err.extend(b[: 2048 - len(err)])
        except (OSError, ValueError):
            return

    threads = [threading.Thread(target=feed, daemon=True), threading.Thread(target=drain, daemon=True)]
    for t in threads:
        t.start()
    out = bytearray()
    why: str | None = None
    deadline = time.monotonic() + timeout_s
    assert p.stdout is not None
    fd = p.stdout.fileno()
    while True:
        if stop is not None and stop.is_set():
            why = "stopped"
            break
        left = deadline - time.monotonic()
        if left <= 0:
            why = "timeout"
            break
        ready, _, _ = select.select([fd], [], [], min(left, 0.2))
        if ready:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            out += chunk
            if len(out) > max_out:
                why = "reply_too_large"
                break
    rc: int | None = None
    if why is None:
        try:
            rc = p.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            why = "timeout"
    if why is not None:
        with contextlib.suppress(OSError):
            os.killpg(p.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            p.wait(timeout=5)
    for t in threads:
        t.join(timeout=2)
    for f in (p.stdout, p.stderr):
        with contextlib.suppress(Exception):
            f.close()  # type: ignore[union-attr]
    return rc, bytes(out), bytes(err), why


def parse_reply(out: bytes) -> dict[str, Any]:
    """The one-line JSON reply, or Bad."""
    if not out.endswith(b"\n"):
        raise Bad("reply_truncated")
    lines = [x for x in out.split(b"\n") if x.strip()]
    if len(lines) != 1:
        raise Bad("reply_not_one_line")
    try:
        obj = strict_loads(lines[0].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Bad("reply_unreadable") from None
    if not isinstance(obj, dict) or obj.get("schema") != REPLY_SCHEMA or not isinstance(obj.get("ok"), bool):
        raise Bad("reply_schema")
    if not obj["ok"]:
        code = obj.get("refused")
        raise Bad(code if isinstance(code, str) and CODE_RE.match(code) else "reply_bad_refusal", "refused")
    return obj


def _blob(obj: Mapping[str, Any], key: str, cap: int) -> bytes:
    v = obj.get(key)
    if not isinstance(v, dict) or not isinstance(v.get("b64"), str) or not isinstance(v.get("sha256"), str) or isinstance(v.get("len"), bool) or not isinstance(v.get("len"), int):
        raise Bad(f"reply_{key}_shape")
    try:
        b = base64.b64decode(v["b64"], validate=True)
    except ValueError:
        raise Bad(f"reply_{key}_base64") from None
    if len(b) != v["len"] or len(b) > cap:
        raise Bad(f"reply_{key}_length")
    if sha256_hex(b) != v["sha256"]:
        raise Bad(f"reply_{key}_sha")
    return b


def check_reply(obj: Mapping[str, Any], *, bot_id: str, req_ms: int, trips_raw: bytes, trip_ids: Sequence[str], pins: tuple[str, str]) -> tuple[bytes, bytes]:
    """Everything the client checks before it touches a file. Returns (twins bytes, run record bytes) or raises Bad."""
    if obj.get("bot_id") != bot_id:
        raise Bad("reply_bot_mismatch")
    if obj.get("now_ms") != req_ms or isinstance(obj.get("now_ms"), bool):
        raise Bad("reply_now_mismatch")
    if obj.get("req_sha256") != sha256_hex(trips_raw):
        raise Bad("reply_request_mismatch")
    twins, run = _blob(obj, "twins", MAX_TWINS_BYTES), _blob(obj, "run", MAX_RUN_BYTES)
    why = verify_pair(twins, run, bot_id=bot_id, trip_ids=trip_ids, now_ms_=req_ms, pins=pins)
    if why:
        raise Bad(f"reply_{why}")
    return twins, run


def _fsync_dir(d: str) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def install_pair(twins_file: str, twins: bytes, run: bytes) -> None:
    """Replaces twins_file and twins_file + '.run.json': both temp files written and fsynced first, then two renames back to back (twins first), then a directory fsync.
    A target that exists must be a regular file with st_nlink 1 (never a symlink or a hard link); else nothing is touched. Raises Bad("install_failed:<why>")."""
    targets = [(twins_file, twins), (twins_file + ".run.json", run)]
    d = os.path.dirname(os.path.abspath(twins_file))
    try:
        if not stat.S_ISDIR(os.lstat(d).st_mode):
            raise Bad("install_failed:dir_not_directory")
        for path, _ in targets:
            try:
                ls = os.lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(ls.st_mode) or not stat.S_ISREG(ls.st_mode):
                raise Bad("install_failed:target_not_regular")
            if ls.st_nlink != 1:
                raise Bad("install_failed:target_hard_linked")
    except OSError:
        raise Bad("install_failed:target_unreadable") from None
    tmps: list[tuple[str, str]] = []
    try:
        for path, data in targets:
            tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            tmps.append((tmp, path))
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        while tmps:
            tmp, path = tmps[0]
            os.replace(tmp, path)
            tmps.pop(0)
        _fsync_dir(d)
    except OSError:
        raise Bad("install_failed:write") from None
    finally:
        for tmp, _ in tmps:
            with contextlib.suppress(OSError):
                os.unlink(tmp)


class Locked(RuntimeError):
    """Another relay client holds the lock for this twins file."""


def _ms_arg(x: float) -> int:
    return int(round(x * 1000))


class Relay:
    """The client. `cmd` is the argv template (a token `{request}` in any element is replaced by `twin <now_ms>`)."""

    def __init__(self, *, bot_id: str, trips_file: str, twins_file: str, cmd: Sequence[str], interval_s: float = INTERVAL_S_DEFAULT, timeout_s: float = TIMEOUT_S_DEFAULT,
                 poll_s: float = POLL_S_DEFAULT, min_gap_s: float = MIN_GAP_S_DEFAULT, max_trips_bytes: int = MAX_TRIPS_BYTES,
                 clock_ms: Callable[[], int] = now_ms, mono: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 pins: tuple[str, str] | None = None) -> None:
        c0 = _c0()
        why = c0.bot_id_problem(bot_id) or c0.arm_refusal(bot_id)
        if why:
            raise ValueError(why)
        if not 1.0 <= interval_s <= INTERVAL_S_MAX:
            raise ValueError(f"interval_s {interval_s} outside [1, {INTERVAL_S_MAX:g}]: the bridge calls a twin older than {STALE_MS // 1000} s stale (DEC-032 s3)")
        if not 1.0 <= timeout_s <= TIMEOUT_S_MAX:
            raise ValueError(f"timeout_s {timeout_s} outside [1, {TIMEOUT_S_MAX:g}]")
        if poll_s < 0 or min_gap_s < 0.5 or not 1 <= max_trips_bytes <= MAX_TRIPS_BYTES:
            raise ValueError("poll_s / min_gap_s / max_trips_bytes out of range")
        if not any("{request}" in x for x in cmd):
            raise ValueError("cmd has no {request} token")
        for p in (trips_file, twins_file):
            if not os.path.isabs(p) or _under_sealed(p):
                raise ValueError(f"path must be absolute and outside {SEALED_ROOT_PREFIX}: {p}")
        self.bot_id, self.trips_file, self.twins_file, self.cmd = bot_id, trips_file, twins_file, list(cmd)
        self.interval_s, self.timeout_s, self.poll_s, self.min_gap_s, self.max_trips_bytes = float(interval_s), float(timeout_s), float(poll_s), float(min_gap_s), int(max_trips_bytes)
        self.clock_ms, self.mono, self.sleep = clock_ms, mono, sleep
        self.pins = tuple(pins) if pins is not None else tuple(c0.twin_pins())
        self.status_path = twins_file + ".relay-status.json"
        self.stop = threading.Event()
        self.cycles = self.ok_cycles = self.failures = 0
        self.last_try_ms: int | None = None
        self.last_ok_ms: int | None = None
        self.last_run_ms: int | None = None
        self.last_code = "none"
        self.last_state = "starting"
        self._printed_code = ""
        self.twin_rows: int | None = None
        self.covered: int | None = None
        self.last_sent_sha: str | None = None
        self.detail = ""
        self._lock_fd: int | None = os.open(twins_file + ".relay.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # single instance for the client's whole life
        except BlockingIOError:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise Locked(f"locked:{twins_file}.relay.lock") from None

    def close(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

    # -- status
    def write_status(self, state: str | None = None) -> None:
        if state:
            self.last_state = state
        st = {"schema": STATUS_SCHEMA, "bot_id": self.bot_id, "state": self.last_state, "ms": self.clock_ms(), "pid": os.getpid(), "last_try_ms": self.last_try_ms,
              "last_ok_ms": self.last_ok_ms, "last_run_ms": self.last_run_ms, "last_code": self.last_code, "detail": self.detail, "consecutive_failures": self.failures,
              "cycles": self.cycles, "ok_cycles": self.ok_cycles, "twin_rows": self.twin_rows, "covered": self.covered, "interval_s": self.interval_s, "timeout_s": self.timeout_s}
        tmp = self.status_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(st, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.status_path)
        except OSError as exc:
            print(f"hv4_c0_twin_relay: status file not written ({type(exc).__name__})", file=sys.stderr, flush=True)

    # -- one cycle
    def _read_trips(self) -> bytes:
        try:
            ls = os.lstat(self.trips_file)
        except FileNotFoundError:
            raise Bad("trips_missing") from None
        except OSError:
            raise Bad("trips_unreadable") from None
        if stat.S_ISLNK(ls.st_mode) or not stat.S_ISREG(ls.st_mode):
            raise Bad("trips_not_regular")
        if ls.st_size > self.max_trips_bytes:
            raise Bad("trips_too_large")
        try:
            return _read_regular(Path(self.trips_file), self.max_trips_bytes)
        except OSError:
            raise Bad("trips_unreadable") from None

    def cycle(self) -> bool:
        """One send. True only when a verified pair replaced the files. Never raises."""
        self.cycles += 1
        req_ms = self.clock_ms()  # BEFORE the trips file is read: the stamp is never later than the snapshot it covers
        self.last_try_ms = req_ms
        self.detail = ""
        try:
            raw = self._read_trips()
            self.last_sent_sha = sha256_hex(raw)
            trips, why = validate_trips(raw, self.max_trips_bytes)
            if why:
                raise Bad(why)
            ids = [t["trip_id"] for t in trips]
            argv = [x.replace("{request}", f"twin {req_ms}") for x in self.cmd]
            rc, out, err, run_why = run_bounded(argv, raw, self.timeout_s, MAX_REPLY_BYTES, self.stop)
            self.detail = re.sub(r"[^ -~]", "?", err.decode("utf-8", "replace"))[:200].strip()
            if run_why:
                raise Bad(f"ssh_{run_why}")
            obj = parse_reply(out) if out.strip() else None
            if obj is None:
                raise Bad(f"ssh_rc_{rc}" if rc else "reply_empty")
            if rc != 0:
                raise Bad(f"ssh_rc_{rc}")
            twins, run = check_reply(obj, bot_id=self.bot_id, req_ms=req_ms, trips_raw=raw, trip_ids=ids, pins=self.pins)
            install_pair(self.twins_file, twins, run)
        except Bad as exc:
            return self._fail(exc.code, exc.kind)
        except Exception as exc:  # noqa: BLE001 - the loop must survive anything; the old files stay
            return self._fail(f"internal_error:{re.sub(r'[^a-z0-9_]', '_', type(exc).__name__.lower())[:40]}", "error")
        self.ok_cycles += 1
        if self.failures:
            print(f"hv4_c0_twin_relay: recovered after {self.failures} failed try(s)", file=sys.stderr, flush=True)
        self._printed_code = ""
        self.failures = 0
        self.last_ok_ms, self.last_run_ms, self.last_code = self.clock_ms(), req_ms, "ok"
        self.twin_rows, self.covered = len(twins.splitlines()), len(ids)
        self.write_status("ok")
        return True

    def _fail(self, code: str, kind: str) -> bool:
        self.failures += 1
        self.last_code = code
        self.write_status("refused" if kind == "refused" else "error")
        if code != self._printed_code or self.failures % 30 == 0:  # a standing refusal (e.g. before the PASS record exists) is logged once, then every 30th try
            self._printed_code = code
            print(f"hv4_c0_twin_relay: {self.last_state} {code} (consecutive {self.failures}){' ' + self.detail if self.detail else ''}", file=sys.stderr, flush=True)
        return False

    # -- the loop
    def trips_changed(self) -> bool:
        try:
            return sha256_hex(self._read_trips()) != self.last_sent_sha
        except Bad:
            return False

    def delay_after(self, ok: bool) -> float:
        """The wait from this cycle's start to the next one's: the interval after a success; after a failure 2, 4, 8 s and then the interval again (never longer)."""
        if ok or self.failures > len(FAIL_BACKOFF_S):
            return self.interval_s
        return min(self.interval_s, FAIL_BACKOFF_S[max(self.failures, 1) - 1])

    def run(self, max_s: float | None = None, max_cycles: int | None = None) -> int:
        """Start-to-start cadence: the next cycle starts `interval_s` after the last one started (sooner after a failure), or as soon as the trips file differs from the
        bytes last sent, but never within `min_gap_s` of the last start. Ends on stop, max_s or max_cycles; the status then reads `stopped`."""
        t0 = self.mono()
        next_at = t0
        last_start: float | None = None
        self.write_status("starting")
        try:
            while not self.stop.is_set():
                now = self.mono()
                if max_s is not None and now - t0 >= max_s:
                    break
                due = now >= next_at
                changed = (not due and self.poll_s > 0 and (last_start is None or now - last_start >= self.min_gap_s) and self.trips_changed())
                if due or changed:
                    last_start = now
                    ok = self.cycle()
                    next_at = last_start + self.delay_after(ok)
                    if max_cycles is not None and self.cycles >= max_cycles:
                        break
                    continue
                self.sleep(max(0.01, min(self.poll_s or 0.5, next_at - now)))
        finally:
            self.write_status("stopped")
        return EXIT_OK


def build_cmd(ssh_host: str | None, cmd_json: str | None, remote_template: str) -> list[str]:
    if bool(ssh_host) == bool(cmd_json):
        raise ValueError("give exactly one of --ssh-host (production) or --cmd-json (tests)")
    if cmd_json:
        cmd = json.loads(cmd_json)
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
            raise ValueError("--cmd-json must be a JSON list of strings")
        return cmd
    assert ssh_host is not None
    if not HOST_RE.match(ssh_host):
        raise ValueError("bad --ssh-host")
    return ["ssh", *SSH_OPTS, ssh_host, remote_template]


def _bounded_float(lo: float, hi: float) -> Callable[[str], float]:
    def conv(s: str) -> float:
        v = float(s)
        if not lo <= v <= hi:
            raise argparse.ArgumentTypeError(f"{s} outside [{lo:g}, {hi:g}]")
        return v
    return conv


def run_client(args: argparse.Namespace) -> int:
    try:
        cmd = build_cmd(args.ssh_host, args.cmd_json, args.remote_template)
        relay = Relay(bot_id=args.bot_id, trips_file=args.trips_file, twins_file=args.twins_file, cmd=cmd, interval_s=args.interval_s, timeout_s=args.timeout_s,
                      poll_s=args.poll_s, min_gap_s=args.min_gap_s, max_trips_bytes=args.max_trips_bytes)
    except Locked as exc:
        print(f"hv4_c0_twin_relay: refused {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (ValueError, OSError) as exc:
        print(f"hv4_c0_twin_relay: refused {exc}", file=sys.stderr)
        return EXIT_BAD
    signal.signal(signal.SIGTERM, lambda *_: relay.stop.set())
    signal.signal(signal.SIGINT, lambda *_: relay.stop.set())
    try:
        if args.once:
            ok = relay.cycle()
            return EXIT_OK if ok else 1
        return relay.run(max_s=args.max_s)
    finally:
        relay.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "serve":  # dispatched by hand: the forced command's options must reach serve's own parser untouched
        return serve_main(args[1:], use_alarm=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False,
                                 epilog="serve (research-0, forced command): run `serve --help` for its flags")
    sub = ap.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("client", help="fast-0: pull loop")
    c.add_argument("--bot-id", required=True)
    c.add_argument("--trips-file", required=True, help="the bridge's parity_dir/trips.jsonl")
    c.add_argument("--twins-file", required=True, help="the bridge's cfg.twins_file; its run record is <twins-file>.run.json")
    c.add_argument("--ssh-host", default=None, help="production: ssh <SSH_OPTS> HOST 'twin <now_ms>' (the forced command on HOST runs serve --from-env)")
    c.add_argument("--remote-template", default="{request}", help="with --ssh-host and no forced command (phase 1): the remote shell line, {request} replaced by 'twin <now_ms>'")
    c.add_argument("--cmd-json", default=None, help="tests: the command as a JSON list; a {request} token is replaced")
    c.add_argument("--interval-s", type=_bounded_float(1.0, INTERVAL_S_MAX), default=INTERVAL_S_DEFAULT)
    c.add_argument("--timeout-s", type=_bounded_float(1.0, TIMEOUT_S_MAX), default=TIMEOUT_S_DEFAULT)
    c.add_argument("--poll-s", type=_bounded_float(0.0, 10.0), default=POLL_S_DEFAULT, help="how often the trips file is checked for a change (0 = interval only)")
    c.add_argument("--min-gap-s", type=_bounded_float(0.5, 60.0), default=MIN_GAP_S_DEFAULT)
    c.add_argument("--max-trips-bytes", type=int, default=MAX_TRIPS_BYTES)
    c.add_argument("--max-s", type=float, default=None)
    c.add_argument("--once", action="store_true", help="one cycle, exit 0 only if a verified pair was installed")
    return run_client(ap.parse_args(args))


if __name__ == "__main__":
    sys.exit(main())
