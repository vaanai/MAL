#!/usr/bin/env python3
"""r4b_declog_copy: byte copier of the r4bt hosts' decision logs, mal-fast-0 -> mal-research-0 (DEC-031 Am.31 preview; QP-RULING-1113-open-sims Addendum 1, B5 rule 2). PAPER ONLY.

Per --series <change_id>=<declog root> it copies the day files `<root>/decisions/r4b-exp027/YYYY-MM-DD.jsonl` to research-0
`/data2/mal/r4b-preview/declog-copy/<change_id>/decisions/r4b-exp027/<day>.jsonl` (dirs 0700, files 0600), as prefixes cut at the last newline.

What it never does: parse a row (no json.loads, no line count, no content in any output), take a lock (a lock could block the host's append), open anything but the allow-listed day files
(O_RDONLY|O_NOFOLLOW), or touch `out/`, `host.log`, `m1-start.txt`, `sidecar.jsonl`. Output per file: change_id, file name, byte length, sha256, unchanged|extended|new.

Allow-list (refusal = exit 2, before anything is read): change_id is exactly r4bt-a2-dry-1113a, or r4bt-a2-NNNNx, or r4bt-a3-NNNNx / r4bt-a3-dry-NNNNx (dry-long-1101a and r4bt-a1-* are refused);
the root's realpath ends in /declog and is <allowed parent>/<change_id>/declog; no path component is `out`, `host.log`, `m1-start.txt` or `sidecar.jsonl`; only files matching
^\\d{4}-\\d\\d-\\d\\d\\.jsonl$ directly inside decisions/r4b-exp027/, regular and not symlinks.

Append-only check: before sending, the remote file's byte length and sha256 are fetched; the local prefix of that length must hash to it (and the remote must not be longer), else the file is
refused loudly (exit 4, nothing sent for it). After a send the remote sha256 and length must equal the local ones (else exit 1).

Log rule (QP B3, DEC-031 Am.31 item 4): before P0_COUNT_INSTANT_S (2026-11-19T00:10:00Z) stdout carries only `R4B-COPY pass <n> start|end exit <code>` lines and failure lines naming the change_id and a
failure kind; no day name, byte length, sha or state. The per-file records go only to the 0600 status file (dir 0700). After the instant the per-file lines print. `--quiet-until <ISO>` moves the
instant later (the wrapper sets it for r4bt-a3 series, which need it: a series of an r4bt-a3 host is refused without it); a value earlier than the a2 constant is refused for any a2 series.

Memory: nothing is held whole. The cut point is found by scanning back from the end for the last newline, the prefix is hashed by streaming, and the send streams the prefix from the file;
the remote side checks length and sha256 of the temp file BEFORE the rename, so a dropped connection can never replace a good file with a truncated one.

Exit codes: 0 pass done; 1 transport or verify failure (retried next pass; 5 consecutive passes = exit 1); 2 refusal; 4 divergence.  --loop SECONDS (default 900; 0 = one pass); --dry-run reads and hashes,
sends nothing and asks the remote nothing.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone

DEST_ROOT = "/data2/mal/r4b-preview/declog-copy"
SSH_HOST = "mal-research-0"  # the alias tools/arena_tip_push.py and scripts/ops/arena-tip-push-job.sh dial (no new key, no new config)
SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3")  # == tools.decision_forwarder.SSH_OPTS (copied: this tool stays dependency-free)
ARM_SUBDIR = ("decisions", "r4b-exp027")
# The r4bt-a2 dry host's root. The decision host's path is pinned later (add it here with a PR); a second parent is accepted by being listed.
ALLOWED_PARENTS = ("/home/claude/mal-data/r4b-dry",)
CHANGE_ID_RES = (re.compile(r"^r4bt-a2-dry-1113a\Z"), re.compile(r"^r4bt-a2-\d{4}[a-z]\Z"), re.compile(r"^r4bt-a3(-dry)?-\d{4}[a-z]\Z"))
DAY_RE = re.compile(r"^\d{4}-\d\d-\d\d\.jsonl\Z")
DENY_COMPONENTS = frozenset({"out", "host.log", "m1-start.txt", "sidecar.jsonl"})
MAX_CONSEC = 5
CHUNK = 1 << 20
P0_COUNT_INSTANT_S = int(datetime(2026, 11, 19, 0, 10, 0, tzinfo=timezone.utc).timestamp())  # Am.31 item 4: no entry-count proxy in any log before this instant
SHA_RE = re.compile(r"^[0-9a-f]{64}\Z")
EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_DIVERGED = 0, 1, 2, 4


class Refuse(Exception):
    pass


class Diverged(Exception):
    pass


class TransportError(Exception):
    pass


def check_change_id(cid):
    if not isinstance(cid, str) or not any(r.match(cid) for r in CHANGE_ID_RES):
        raise Refuse("change_id not allow-listed: %s" % re.sub(r"[^A-Za-z0-9._-]", "?", str(cid))[:60])
    return cid


def check_day(name):
    if not isinstance(name, str) or not DAY_RE.match(name):
        raise Refuse("day file name not allowed")
    return name


def check_root(cid, root, allowed_parents):
    check_change_id(cid)
    real = os.path.realpath(root)
    parts = real.split("/")
    if any(p in DENY_COMPONENTS for p in parts):
        raise Refuse("denied path component in declog root of %s" % cid)
    if os.path.basename(real) != "declog":
        raise Refuse("declog root of %s does not end in /declog" % cid)
    parent = os.path.dirname(real)
    if os.path.basename(parent) != cid or os.path.dirname(parent) not in {os.path.realpath(p) for p in allowed_parents}:
        raise Refuse("declog root of %s is not <allowed parent>/%s/declog" % (cid, cid))
    return real


def parse_series(items, allowed_parents=ALLOWED_PARENTS):
    out, seen = [], set()
    for it in items:
        if "=" not in it:
            raise Refuse("--series needs <change_id>=<declog root>")
        cid, root = it.split("=", 1)
        if cid in seen:
            raise Refuse("duplicate change_id")
        seen.add(cid)
        out.append((cid, check_root(cid, root, allowed_parents)))
    if not out:
        raise Refuse("no --series given")
    return out


def list_day_files(real_root):
    """Names only (a directory listing). Raises Refuse for a symlink or non-regular day file."""
    d = os.path.join(real_root, *ARM_SUBDIR)
    try:
        if stat.S_ISLNK(os.lstat(d).st_mode) or not stat.S_ISDIR(os.lstat(d).st_mode) or os.path.realpath(d) != d:
            raise Refuse("arm dir is not a plain directory under the declog root")
    except FileNotFoundError:
        return d, []
    names = []
    for n in sorted(os.listdir(d)):
        if not DAY_RE.match(n):
            continue  # anything else (out-of-pattern names) is never opened
        if not stat.S_ISREG(os.lstat(os.path.join(d, n)).st_mode):
            raise Refuse("a day file is not a regular file")
        names.append(n)
    return d, names


def _open_ro(path):
    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW)  # no lock: a lock could block the host's append


def _cut_point(fd):
    """Length of the prefix ending at the last newline, found by scanning back from the end of the file as it is now (0 if there is none: a torn tail is never copied)."""
    pos = os.fstat(fd).st_size
    while pos > 0:
        start = max(0, pos - CHUNK)
        b = os.pread(fd, pos - start, start)
        i = b.rfind(b"\n")
        if i >= 0:
            return start + i + 1
        pos = start
    return 0


def _hash_range(fd, n):
    h, off = hashlib.sha256(), 0
    while off < n:
        b = os.pread(fd, min(CHUNK, n - off), off)
        if not b:
            break
        h.update(b)
        off += len(b)
    return h.hexdigest()


def prefix_info(path):
    """(byte length, sha256) of the file's prefix up to its last newline. O_RDONLY, no lock, no parse, never the whole file in memory."""
    fd = _open_ro(path)
    try:
        n = _cut_point(fd)
        return n, _hash_range(fd, n)
    finally:
        os.close(fd)


def prefix_sha(path, n):
    fd = _open_ro(path)
    try:
        return _hash_range(fd, n)
    finally:
        os.close(fd)


def iter_prefix(path, n):
    fd = _open_ro(path)
    try:
        off = 0
        while off < n:
            b = os.pread(fd, min(CHUNK, n - off), off)
            if not b:
                break
            yield b
            off += len(b)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------------------------------------------- transport
def ssh_argv(host=SSH_HOST):
    return ["ssh", *SSH_OPTS, host]


def _remote_paths(cid, day):
    check_change_id(cid)
    check_day(day)
    d = "%s/%s/%s/%s" % (DEST_ROOT, cid, ARM_SUBDIR[0], ARM_SUBDIR[1])
    return d, "%s/%s" % (d, day), "%s/.%s.tmp.$$" % (d, day)


def remote_send_cmd(cid, day, n, sha):
    """The fixed remote command (validated names only). $$ is the remote shell's pid, left unquoted on purpose. The temp file is checked for byte length AND sha256 before the rename; on any
    failure it is removed and the shell exits 1, so a cut connection leaves the previous good file untouched."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 0 or not isinstance(sha, str) or not SHA_RE.match(sha):
        raise Refuse("length or sha256 not valid")
    d, final, tmp = _remote_paths(cid, day)
    t = shlex.quote(tmp[:-2]) + "$$"
    script = ("umask 077; mkdir -p %s && cat > %s && [ \"$(wc -c < %s | tr -d ' ')\" = %d ] && [ \"$(sha256sum < %s | cut -d' ' -f1)\" = %s ] && mv %s %s || { rm -f %s; exit 1; }"
              % (shlex.quote(d), t, t, n, t, sha, t, shlex.quote(final), t))
    return "sh -c " + shlex.quote(script)


def remote_stat_cmd(cid, day):
    """Prints `<bytes> <sha256>` of the remote file, or nothing when it does not exist."""
    _, final, _ = _remote_paths(cid, day)
    f = shlex.quote(final)
    script = "if [ -f %s ]; then echo \"$(wc -c < %s | tr -d ' ') $(sha256sum < %s | cut -d' ' -f1)\"; fi" % (f, f, f)
    return "sh -c " + shlex.quote(script)


def build_argv(remote_cmd, host=SSH_HOST):
    return ssh_argv(host) + [remote_cmd]


def ssh_remote_stat(cid, day, host=SSH_HOST, timeout=120, run=subprocess.run):
    try:
        r = run(build_argv(remote_stat_cmd(cid, day), host), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise TransportError("remote stat: %s" % type(e).__name__)
    if r.returncode != 0:
        raise TransportError("remote stat rc=%d" % r.returncode)
    text = r.stdout.decode("ascii", "replace").strip()
    if not text:
        return None
    m = re.match(r"^(\d+) ([0-9a-f]{64})\Z", text)
    if not m:
        raise TransportError("remote stat: unreadable answer")
    return int(m.group(1)), m.group(2)


def ssh_send(cid, day, path, n, sha, host=SSH_HOST, timeout=900, popen=subprocess.Popen):
    """Streams the first n bytes of path to the remote command's stdin (never the whole file in memory); the remote verifies length and sha256 before the rename."""
    argv = build_argv(remote_send_cmd(cid, day, n, sha), host)
    try:
        p = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise TransportError("send: %s" % type(e).__name__)
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    try:
        try:
            for chunk in iter_prefix(path, n):
                p.stdin.write(chunk)
            p.stdin.close()
        except OSError as e:
            p.kill()
            p.wait()
            raise TransportError("send: %s" % type(e).__name__)
        rc = p.wait()
    finally:
        timer.cancel()
    if rc != 0:
        raise TransportError("send rc=%s" % rc)


# ---------------------------------------------------------------------------------------------------------------- one pass
def one_pass(series, send_fn, remote_stat_fn, dry_run=False, out=print, quiet=True):
    """series: [(change_id, real declog root)] already validated. Returns (code, file records, failures). The per-file records are for the status file; `out` gets per-file lines only when
    not quiet. Failures are (change_id, kind) pairs, which are safe to print at any time. A divergence ends nothing but the file; the code is the worst of the pass."""
    recs, fails, code = [], [], EXIT_OK

    def fail(cid, kind, c, detail=None):
        nonlocal code
        fails.append((cid, kind))
        code = EXIT_DIVERGED if EXIT_DIVERGED in (c, code) else EXIT_FAIL
        if not quiet:
            out("R4B-COPY %s %s %s" % (kind.upper(), cid, detail or ""))

    for cid, root in series:
        d, names = list_day_files(root)
        for day in names:
            try:
                path = os.path.join(d, day)
                n, sha = prefix_info(path)
                if n == 0:
                    continue
                if dry_run:
                    state = "dry-run"
                else:
                    rem = remote_stat_fn(cid, day)
                    if rem is None:
                        state = "new"
                    else:
                        rlen, rsha = rem
                        if rlen > n or prefix_sha(path, rlen) != rsha:
                            fail(cid, "diverged", EXIT_DIVERGED, "%s: copy and original diverge: stop and ask QP" % day)
                            continue
                        state = "unchanged" if rlen == n else "extended"
                    if state != "unchanged":
                        send_fn(cid, day, path, n, sha)
                        if remote_stat_fn(cid, day) != (n, sha):
                            fail(cid, "verify-fail", EXIT_FAIL, day)
                            continue
            except TransportError as e:
                fail(cid, "transport-fail", EXIT_FAIL, "%s: %s" % (day, e))
                continue
            recs.append({"change_id": cid, "file": day, "bytes": n, "sha256": sha, "state": state})
            if not quiet:
                out("R4B-COPY %s %s bytes=%d sha256=%s %s" % (cid, day, n, sha, state))
    return code, recs, fails


def write_status(path, st):
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(st, f, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def default_status_path():
    return os.path.join(os.path.expanduser("~"), "mal-data", "r4b-preview-copier", "status.json")


def parse_quiet_until(text, series):
    if text is None:
        if any(re.match(r"^r4bt-a3", cid) for cid, _ in series):
            raise Refuse("an r4bt-a3 series needs --quiet-until (its own P0 count instant)")
        return P0_COUNT_INSTANT_S
    try:
        t = int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        raise Refuse("--quiet-until must be YYYY-MM-DDTHH:MM:SSZ")
    if t < P0_COUNT_INSTANT_S and any(re.match(r"^r4bt-a2", cid) for cid, _ in series):
        raise Refuse("--quiet-until earlier than the r4bt-a2 P0 count instant")
    return t


def main(argv=None, send_fn=None, remote_stat_fn=None, allowed_parents=ALLOWED_PARENTS, status_path=None, sleep=time.sleep, max_passes=None, out=print, now_fn=time.time):
    """send_fn, remote_stat_fn, allowed_parents, status_path, sleep, max_passes, now_fn are injectable for tests; the CLI uses ssh to the research-0 alias."""
    ap = argparse.ArgumentParser(description="byte copier of r4bt decision-log prefixes to research-0 (parses no row)")
    ap.add_argument("--series", action="append", default=[], help="<change_id>=<declog root>, repeatable")
    ap.add_argument("--loop", type=int, default=900, help="seconds between passes; 0 = one pass")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet-until", default=None, help="YYYY-MM-DDTHH:MM:SSZ; per-file lines stay out of the log before it (default: the r4bt-a2 P0 count instant)")
    a = ap.parse_args(argv)
    try:
        series = parse_series(a.series, allowed_parents)
        quiet_until = parse_quiet_until(a.quiet_until, series)
    except Refuse as e:
        out("R4B-COPY REFUSE %s" % e)
        return EXIT_REFUSED
    send_fn = send_fn or ssh_send
    remote_stat_fn = remote_stat_fn or ssh_remote_stat
    spath = status_path or default_status_path()
    n = consec = 0
    while True:
        n += 1
        quiet = now_fn() < quiet_until
        out("R4B-COPY pass %d start" % n)
        try:
            code, recs, fails = one_pass(series, send_fn, remote_stat_fn, a.dry_run, out, quiet)
        except Refuse as e:
            out("R4B-COPY REFUSE %s" % e)
            return EXIT_REFUSED
        except OSError as e:
            out("R4B-COPY FAIL pass %d: %s" % (n, e.strerror or type(e).__name__))
            code, recs, fails = EXIT_FAIL, [], []
        if quiet:
            for cid, kind in sorted(set(fails)):
                out("R4B-COPY %s %s%s" % (kind.upper(), cid, ": copy and original diverge: stop and ask QP" if kind == "diverged" else ""))
        try:
            write_status(spath, {"pass_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "dry_run": a.dry_run, "exit": code, "files": recs})
        except OSError:
            out("R4B-COPY status file not written")
        out("R4B-COPY pass %d end exit %d" % (n, code))
        if code == EXIT_DIVERGED:
            return EXIT_DIVERGED
        consec = consec + 1 if code == EXIT_FAIL else 0
        if consec >= MAX_CONSEC or (code == EXIT_FAIL and a.loop <= 0):
            return EXIT_FAIL
        if a.loop <= 0 or (max_passes is not None and n >= max_passes):
            return code
        sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
