"""Launcher for the s1 shadow (and its smoke) that hands S*'s frozen tools/h5_shadow.py its keyed second websocket URL WITHOUT the key in any OS-visible argv.

DEC-031 Amendment 22 item 10 (Helius websocket as s1's second provider) as bound by QP on #1107: the key never appears in any process's argv, a systemd-run unit description
or property, a job log or the journal. S* (5c2ab7c6) takes ws URLs only as --ws-url arguments (h5_shadow.py:3518) and forces TLS (observe/trade_source.py:243-250), so neither
an environment path nor a plain ws:// local relay exists. This launcher is OUTSIDE the frozen code and changes none of it:

  python -I scripts/ops/q50s1-ws-launch.py --src <S* worktree> [--key-file <KEY=VALUE file> --keyed-url <key-free wss URL> [--key-var NAME] [--key-param NAME]] -- <h5_shadow args>

Without --key-file/--keyed-url (an unkeyed second provider, or mainnet-beta alone) it only runs the module, with the same redaction. --key-var (default HELIUS_API_KEY) names
the variable in the key file; the key is always appended as ?api-key=<key>, the only parameter S* itself redacts (QP A2 on #1121). Every --ws-url value must be HOST-ONLY
(wss://<host>[:<port>][/]; QP A1: a credential in a URL path would reach argv, the registry, the start record and logs).

1. The process's argv (/proc/<pid>/cmdline, ps, systemctl status) is this line: the --ws-url arguments carry the key-free base only.
2. Inside the process it reads HELIUS_API_KEY from --key-file (never printed, never put in os.environ), replaces the ONE h5_shadow argument equal to --keyed-url by
   "<keyed-url>?api-key=<key>" in sys.argv only, puts the S* worktree first on sys.path, and runs the module exactly as `python -m tools.h5_shadow` does
   (runpy.run_module(..., run_name="__main__", alter_sys=True)). h5_shadow's start record and logged argv strip the query (redact_argv, _strip_url).
3. Belt and braces: sys.stdout and sys.stderr are wrapped so any text matching api-key=<value> is written as api-key=REDACTED (logging, tracebacks, prints).
Residual (disclosed): the key is in the process's memory and in the TLS handshake to the provider; /proc/<pid>/environ holds no key.
Exit: h5_shadow's own exit code; 2 usage or argv check; 11 the key file is unreadable, has no HELIUS_API_KEY, or the value has a character outside [A-Za-z0-9-].
--dry: every check, then prints the h5_shadow argv with the key shown as REDACTED and exits 0 without importing h5_shadow (used by the start script's --check).
"""
from __future__ import annotations

import io
import os
import re
import runpy
import sys

KEY_VAR = "HELIUS_API_KEY"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")
LEAK_RE = re.compile(r"(?i)(api[-_]?key=)[^&\s\"'<>]+")
KEYLIKE = re.compile(r"(?i)api[-_]?key|[?&](key|token|auth|apikey)=|://[^/\s]*@")
HOST_ONLY = re.compile(r"^wss://[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:[0-9]{1,5})?/?$")


def host_only_problem(args: list[str]) -> str | None:
    """Every --ws-url value must be wss://<host>[:<port>][/] (no path, query, fragment or userinfo)."""
    for i, a in enumerate(args):
        if a == "--ws-url" and (i + 1 >= len(args) or not HOST_ONLY.match(args[i + 1])):
            return "every --ws-url must be host-only wss://<host>[:<port>][/]"
        if a.startswith("--ws-url="):
            return "--ws-url=VALUE is not accepted; pass --ws-url VALUE"
    return None


def read_key(path: str, var: str = KEY_VAR) -> str | None:
    """The value of `var` (default HELIUS_API_KEY) in a KEY=VALUE file (optional `export `, optional quotes); None if unreadable, absent or malformed. Never logs."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    for ln in lines:
        s = ln.strip()
        if s.startswith("export "):
            s = s[len("export "):].lstrip()
        if s.startswith(var + "="):
            v = s[len(var) + 1:].strip().strip("'\"")
            return v if KEY_RE.match(v) else None
    return None


def keyed_argv(args: list[str], keyed_url: str, key: str) -> list[str]:
    """Replace the one argument equal to keyed_url (it must follow --ws-url) by keyed_url + ?api-key=key. Raises ValueError on any doubt."""
    if not HOST_ONLY.match(keyed_url) or KEYLIKE.search(keyed_url):
        raise ValueError("--keyed-url must be a host-only key-free wss:// URL")
    p = host_only_problem(args)
    if p:
        raise ValueError(p)
    for a in args:
        if KEYLIKE.search(a):
            raise ValueError("an h5_shadow argument already carries a key-like token")
    idx = [i for i, a in enumerate(args) if a == keyed_url]
    if len(idx) != 1 or idx[0] == 0 or args[idx[0] - 1] != "--ws-url":
        raise ValueError("--keyed-url must appear exactly once in the h5_shadow arguments, as a --ws-url value")
    out = list(args)
    out[idx[0]] = f"{keyed_url}?api-key={key}"
    return out


class Redacting(io.TextIOBase):
    """A text stream that writes through to `raw` with api-key=<value> replaced by api-key=REDACTED."""

    def __init__(self, raw):
        self.raw = raw

    def write(self, s):
        self.raw.write(LEAK_RE.sub(r"\1REDACTED", s))
        return len(s)

    def flush(self):
        self.raw.flush()

    def fileno(self):
        return self.raw.fileno()

    def isatty(self):
        return False

    @property
    def encoding(self):
        return getattr(self.raw, "encoding", "utf-8")


def parse(argv: list[str]) -> tuple[dict, list[str]]:
    if "--" not in argv:
        raise ValueError("usage: q50s1-ws-launch.py --src DIR [--key-file FILE --keyed-url URL [--key-var NAME]] [--dry] -- <h5_shadow args>")
    k = argv.index("--")
    own, rest = argv[:k], argv[k + 1:]
    opts: dict = {"dry": False}
    i = 0
    while i < len(own):
        a = own[i]
        if a == "--dry":
            opts["dry"] = True
            i += 1
        elif a in ("--src", "--key-file", "--keyed-url", "--key-var") and i + 1 < len(own):
            opts[a[2:].replace("-", "_")] = own[i + 1]
            i += 2
        else:
            raise ValueError(f"unknown launcher argument {a!r}")
    if "src" not in opts:
        raise ValueError("--src is required")
    if ("key_file" in opts) != ("keyed_url" in opts):
        raise ValueError("--key-file and --keyed-url go together (neither for an unkeyed feed)")
    if "key_var" in opts and not NAME_RE.match(opts["key_var"]):
        raise ValueError("--key-var must match [A-Za-z0-9_-]")
    return opts, rest


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        opts, rest = parse(argv)
    except ValueError as e:
        print(f"q50s1-ws-launch: {e}", file=sys.stderr)
        return 2
    src = os.path.realpath(opts["src"])
    if not os.path.isfile(os.path.join(src, "tools", "h5_shadow.py")):
        print(f"q50s1-ws-launch: {src} has no tools/h5_shadow.py", file=sys.stderr)
        return 2
    if "key_file" in opts:
        var = opts.get("key_var", KEY_VAR)
        key = read_key(opts["key_file"], var)
        if key is None:
            print(f"q50s1-ws-launch: no usable {var} in {opts['key_file']} (unreadable, absent or malformed); nothing started", file=sys.stderr)
            return 11
        try:
            args = keyed_argv(rest, opts["keyed_url"], key)
        except ValueError as e:
            print(f"q50s1-ws-launch: {e}", file=sys.stderr)
            return 2
    else:
        key = None
        args = list(rest)
        p = host_only_problem(args)
        if any(KEYLIKE.search(a) for a in args) or p:
            print(f"q50s1-ws-launch: {p or 'an h5_shadow argument carries a key-like token'}", file=sys.stderr)
            return 2
    if opts["dry"]:
        print("q50s1-ws-launch: (dry) would run tools.h5_shadow from " + src + " with " + " ".join(LEAK_RE.sub(r"\1REDACTED", a) for a in args))
        return 0
    del key
    sys.stdout = Redacting(sys.stdout)
    sys.stderr = Redacting(sys.stderr)
    here = os.path.dirname(os.path.realpath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.realpath(p or ".") != here]
    sys.path.insert(0, src)
    sys.argv = ["tools.h5_shadow", *args]
    try:
        runpy.run_module("tools.h5_shadow", run_name="__main__", alter_sys=True)
    except SystemExit as e:
        code = e.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
