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
        if stat.S_ISLNK(os.lstat(d).st_mode) or not stat.S_ISDIR(os.lstat(d).st_mode):
            raise Refuse("arm dir is not a plain directory")
    except FileNotFoundError:
        return d, []
    names = []
    for n in sorted(os.listdir(d)):
        if not DAY_RE.match(n):
            continue  # anything else (out-of-pattern names) is never opened
        if not stat.S_ISREG(os.lstat(os.path.join(d, n)).st_mode):
            raise Refuse("day file is not a regular file: %s" % n)
        names.append(n)
    return d, names


def read_prefix(path):
    """O_RDONLY, no lock, no parse. Returns the bytes up to and including the last newline (b'' if none: a torn tail is never copied)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    i = data.rfind(b"\n")
    return data[: i + 1] if i >= 0 else b""


# ---------------------------------------------------------------------------------------------------------------- transport
def ssh_argv(host=SSH_HOST):
    return ["ssh", *SSH_OPTS, host]


def _remote_paths(cid, day):
    check_change_id(cid)
    check_day(day)
    d = "%s/%s/%s/%s" % (DEST_ROOT, cid, ARM_SUBDIR[0], ARM_SUBDIR[1])
    return d, "%s/%s" % (d, day), "%s/.%s.tmp.$$" % (d, day)


def remote_send_cmd(cid, day):
    """The fixed remote command (validated names only). $$ is the remote shell's pid, left unquoted on purpose."""
    d, final, tmp = _remote_paths(cid, day)
    tmp_q = shlex.quote(tmp[:-2]) + "$$"
    script = "umask 077; mkdir -p %s && cat > %s && mv %s %s" % (shlex.quote(d), tmp_q, tmp_q, shlex.quote(final))
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


def ssh_send(cid, day, data, host=SSH_HOST, timeout=900, run=subprocess.run):
    try:
        r = run(build_argv(remote_send_cmd(cid, day), host), input=data, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise TransportError("send: %s" % type(e).__name__)
    if r.returncode != 0:
        raise TransportError("send rc=%d" % r.returncode)


# ---------------------------------------------------------------------------------------------------------------- one pass
def one_pass(series, send_fn, remote_stat_fn, dry_run=False, out=print):
    """series: [(change_id, real declog root)] already validated. Returns (code, file records). A divergence ends nothing but the file; the code is the worst of the pass."""
    recs, code = [], EXIT_OK
    for cid, root in series:
        d, names = list_day_files(root)
        for day in names:
            try:
                data = read_prefix(os.path.join(d, day))
                if not data:
                    continue
                sha = hashlib.sha256(data).hexdigest()
                n = len(data)
                if dry_run:
                    state = "dry-run"
                else:
                    rem = remote_stat_fn(cid, day)
                    if rem is None:
                        state = "new"
                    else:
                        rlen, rsha = rem
                        if rlen > n or hashlib.sha256(data[:rlen]).hexdigest() != rsha:
                            out("R4B-COPY DIVERGED %s %s: copy and original diverge: stop and ask QP" % (cid, day))
                            code = max(code, EXIT_DIVERGED)
                            continue
                        state = "unchanged" if rlen == n else "extended"
                    if state != "unchanged":
                        send_fn(cid, day, data)
                        after = remote_stat_fn(cid, day)
                        if after != (n, sha):
                            out("R4B-COPY VERIFY-FAIL %s %s" % (cid, day))
                            code = EXIT_FAIL if code in (EXIT_OK, EXIT_FAIL) else code
                            continue
            except TransportError as e:
                out("R4B-COPY TRANSPORT-FAIL %s %s: %s" % (cid, day, e))
                code = EXIT_FAIL if code in (EXIT_OK, EXIT_FAIL) else code
                continue
            rec = {"change_id": cid, "file": day, "bytes": n, "sha256": sha, "state": state}
            recs.append(rec)
            out("R4B-COPY %s %s bytes=%d sha256=%s %s" % (cid, day, n, sha, state))
    return code, recs


def write_status(path, st):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, sort_keys=True)
    os.replace(tmp, path)


def default_status_path():
    return os.path.join(os.path.expanduser("~"), "mal-data", "r4b-preview-copier", "status.json")


def main(argv=None, send_fn=None, remote_stat_fn=None, allowed_parents=ALLOWED_PARENTS, status_path=None, sleep=time.sleep, max_passes=None, out=print):
    """send_fn, remote_stat_fn, allowed_parents, status_path, sleep, max_passes are injectable for tests; the CLI uses ssh to the research-0 alias."""
    ap = argparse.ArgumentParser(description="byte copier of r4bt decision-log prefixes to research-0 (parses no row)")
    ap.add_argument("--series", action="append", default=[], help="<change_id>=<declog root>, repeatable")
    ap.add_argument("--loop", type=int, default=900, help="seconds between passes; 0 = one pass")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        series = parse_series(a.series, allowed_parents)
    except Refuse as e:
        out("R4B-COPY REFUSE %s" % e)
        return EXIT_REFUSED
    send_fn = send_fn or ssh_send
    remote_stat_fn = remote_stat_fn or ssh_remote_stat
    spath = status_path or default_status_path()
    n = consec = 0
    while True:
        n += 1
        try:
            code, recs = one_pass(series, send_fn, remote_stat_fn, a.dry_run, out)
        except Refuse as e:
            out("R4B-COPY REFUSE %s" % e)
            return EXIT_REFUSED
        except OSError as e:
            out("R4B-COPY FAIL pass %d: %s" % (n, e.strerror or type(e).__name__))
            code, recs = EXIT_FAIL, []
        try:
            write_status(spath, {"pass_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "dry_run": a.dry_run, "exit": code, "files": recs})
        except OSError:
            out("R4B-COPY status file not written")
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
