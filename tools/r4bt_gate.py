"""r4bt_gate: the shared gates of the r4-b-T pricer and read tool (DEC-031 Amendment 25 DRAFT, PR #1187; items 2, 4, 13, 24, 25, 27). PAPER ONLY. No key, no network.

It holds what `tools/r4bt_price.py` and `tools/r4bt_read.py` must both refuse on, so the two cannot drift:

  * G8, "EXP-027's read is written" (item 4). Neither tool opens `/data/mal/r4b` to find out. The manager writes ONE record file and pins it as path@sha256:
        {"schema": "exp027_read_written_v1", "route": "a" | "b", "kind": "completed" | "EXP027_DEADLINE_ND", "written_utc": "YYYY-MM-DDTHH:MM:SSZ",
         "evidence_path": <string>, "evidence_sha256": <64 hex>}
    route "a": `tools/r4b_read.py` wrote its `completed` line (kind "completed"). route "b": `EXP027_DEADLINE_ND` was recorded at the deadline (kind
    "EXP027_DEADLINE_ND", written_utc at or after 2026-11-09T03:00:00Z). The ND determination record (#1181) is NOT the read and is not accepted here: it has
    no route and no kind of these two. The record's sha256 must equal the pinned one, and the wall clock must be at or after written_utc.
  * Closed roots: nothing under `/data/mal/r4b` (EXP-027's outputs), the sealed tip tape `/var/lib/mal/sealed`, a tip-tape archive, `/data/mal/meta-capture`,
    `/data/mal/blocks`, or the shared arena ledger root `/data/mal/arena` may be a declog, tape, ledger or record path of these tools (item 25: "It never reads the
    tip dir, the archive, walk 2, `/data/mal/r4b/`, or any other book's output").
  * Pins: the sha256 of `tools/arena_paper.py` (the engine) and `tools/arena_score.py` (the scorer) must equal the values the manager pastes from the registration
    record (Amendment 25 items 24 and 27: `9d2fdb04...3514` and `c4859323...f27f3` at main 2fa2e4bc). No default: a changed engine (for example #1182) needs its
    own QP OK and a new pin in the registration record.
  * `static_closure` / `blob_identity`: the W route's closure comparison (item 13). A static import walk from the host's entry files, and a `git rev-parse` blob
    comparison of the walk's files between two shas. The walk is a check of QP's 88-file list, not a replacement for it.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

BOT_ID = "r4bt-a1"
ARM = "r4b-exp027"  # tools/r4b_live.BOT_ID at bc65c088: the frozen code's arm name (not imported: no tools/r4b_* import in the pricer)
FROZEN_SHA_M_PRIME = "bc65c088793753954c86b82d0e5eff74aa038e0b"
CLOCK_MS = 1_794_031_200_000  # 2026-11-07T06:00:00Z (Amendment 25 item 14)
DEADLINE_ND_MS = 1_794_193_200_000  # 2026-11-09T03:00:00Z, EXP-027's deadline (route b)
CLOSED_ROOTS = ("/data/mal/r4b", "/var/lib/mal/sealed", "/data/mal/meta-capture", "/data/mal/blocks", "/data/mal/arena")
CLOSED_MARKS = ("tip-tape-archive",)
HOST_ENTRIES = ("tools/r4b_host_glue.py", "tools/r4b_live.py", "tools/r4b_live_host.py", "tools/r4b_tip_reader.py", "tools/r4b_feed_adapter.py",
                "tools/r4b_feed_lag_probe.py", "tools/r4b_feed_merge.py")
RECORD_SCHEMA = "exp027_read_written_v1"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
ROUTE_B_COMMITS = ("a2_c4_window_commit", "a2_c5_ledger_end_commit")  # QP-RULING-1102-r4bt V6: both must be ancestors of origin/main
REPO = Path(__file__).resolve().parent.parent
ARENA_BOTS = "/data/mal/arena/bots.json"  # the shared registry: only the A3 stop line may be written there (Q2 item 7); the tools read r4-b-T's OWN registry
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


class Refused(RuntimeError):
    """A refusal: nothing is priced, read or printed."""


def utc_ms(text: str) -> int:
    if not isinstance(text, str) or not UTC_RE.match(text):
        raise Refused(f"not a UTC instant YYYY-MM-DDTHH:MM:SSZ: {text!r}")
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_path(path: str | Path, what: str, closed: Sequence[str] = CLOSED_ROOTS) -> Path:
    """Refuse a path (resolved, so a symlink does not hide it) under a closed root or carrying a closed mark. Returns the resolved path."""
    p = Path(path).resolve()
    s = str(p)
    for bad in closed:
        if s == bad or s.startswith(bad + "/"):
            raise Refused(f"{what} {s} is under {bad}: refused (DEC-031 Amendment 25 items 2 and 25; SEALS-BRIEF)")
    for mark in CLOSED_MARKS:
        if mark in s:
            raise Refused(f"{what} {s} is inside a tip-tape archive: refused")
    return p


def git_is_ancestor(repo: str | Path, sha: str, ref: str = "origin/main", fetch: bool = True) -> bool:
    """`merge-base --is-ancestor <sha> origin/main` after a quiet fetch. No git output is echoed. A failed fetch refuses (fail closed)."""
    if fetch:
        r = subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], capture_output=True)
        if r.returncode != 0:
            raise Refused("the quiet fetch of origin failed: the ancestor check fails closed")
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, ref], capture_output=True).returncode == 0


def check_registry(path: str | Path, what: str = "the registry", allow_arena_bots: bool = False) -> Path:
    """r4-b-T's own registry sits OUTSIDE /data/mal/arena (QP-RULING-1102-r4bt V10): the pricer, the read and the classifier never open the shared bots.json (other
    books' lines). Only `apply` may name it, to append the A3 stop line (allow_arena_bots)."""
    if allow_arena_bots and str(Path(path).resolve()) == ARENA_BOTS:
        return Path(path).resolve()
    return check_path(path, what)


def exp027_read_written(record_path: str | Path | None, record_sha256: str | None, now_ms: int, *, ancestor: Callable[[str], bool] | None = None) -> dict:
    """G8. Returns the validated record (plus its sha256) or raises Refused. Fail closed on every doubt. Route b also names the A2 C4 Window-line commit and the
    A2 C5 ledger-end-record merge commit (40 hex each; Addendum 2 C5 is what the pricer and the read wait for); `ancestor(sha)` (default: git_is_ancestor on this
    checkout against origin/main with a quiet fetch) must be true for both."""
    if not record_path or not record_sha256:
        raise Refused("EXP-027's read is not shown written: --exp027-record and --exp027-record-sha256 are both required (Amendment 25 item 4)")
    if not SHA_RE.match(record_sha256):
        raise Refused("--exp027-record-sha256 is not a lowercase sha256")
    p = check_path(record_path, "the EXP-027 record")
    if not p.is_file():
        raise Refused(f"the EXP-027 record {p} is not a file")
    got = sha256_file(p)
    if got != record_sha256:
        raise Refused(f"the EXP-027 record sha256 {got} != the pinned {record_sha256}")
    try:
        rec = json.loads(p.read_bytes())
    except ValueError:
        raise Refused("the EXP-027 record is not JSON") from None
    if not isinstance(rec, dict) or rec.get("schema") != RECORD_SCHEMA:
        raise Refused(f"the EXP-027 record needs schema {RECORD_SCHEMA!r}")
    route, kind = rec.get("route"), rec.get("kind")
    if (route, kind) not in (("a", "completed"), ("b", "EXP027_DEADLINE_ND")):
        raise Refused(f"the EXP-027 record route/kind {route!r}/{kind!r} is not (a, completed) or (b, EXP027_DEADLINE_ND): the ND determination alone is not the read")
    written = utc_ms(rec.get("written_utc"))
    if route == "b" and written < DEADLINE_ND_MS:
        raise Refused("route b: EXP027_DEADLINE_ND is written at or after 2026-11-09T03:00:00Z, not earlier")
    if written > now_ms:
        raise Refused(f"the EXP-027 record is dated {rec['written_utc']}, after the wall clock {iso(now_ms)}")
    if not isinstance(rec.get("evidence_path"), str) or not rec["evidence_path"] or not SHA_RE.match(str(rec.get("evidence_sha256"))):
        raise Refused("the EXP-027 record needs evidence_path and a 64-hex evidence_sha256")
    if route == "b":
        check = ancestor or (lambda sha: git_is_ancestor(REPO, sha))
        for k in ROUTE_B_COMMITS:
            sha = rec.get(k)
            if not isinstance(sha, str) or not SHA40_RE.match(sha):
                raise Refused(f"route b: the record needs {k} as a 40-hex commit")
            if not check(sha):
                raise Refused(f"route b: {k} {sha} is not an ancestor of origin/main: the A2 C4/C5 records must be merged before the pricer or the read runs")
    return dict(rec, record_sha256=got)


def check_pins(repo: str | Path, expected: Mapping[str, str]) -> dict[str, str]:
    """expected: {'tools/arena_paper.py': sha256, 'tools/arena_score.py': sha256}. Returns the computed hashes; any difference is a refusal."""
    out = {}
    for rel, want in expected.items():
        if not SHA_RE.match(str(want)):
            raise Refused(f"pin for {rel} is not a lowercase sha256")
        got = sha256_file(Path(repo) / rel)
        if got != want:
            raise Refused(f"{rel} sha256 {got} != the pinned {want}: a changed engine or scorer needs a new pin in the registration record")
        out[rel] = got
    return out


# ------------------------------------------------------------------------------------------------------------------------------ W route: closure and blob identity
def _imports(src: str) -> set[str]:
    mods: set[str] = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names if a.name.split(".")[0] == "tools")
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0 and n.module.split(".")[0] == "tools":
            if n.module == "tools":
                mods.update(f"tools.{a.name}" for a in n.names)
            else:
                mods.add(n.module)
                mods.update(f"{n.module}.{a.name}" for a in n.names)
    return mods


def static_closure(read: Callable[[str], str | None], entries: Iterable[str] = HOST_ENTRIES) -> list[str]:
    """Repo files reached by a static `tools.*` import walk from `entries`. `read(rel)` returns a file's text or None when it does not exist (a name that is
    an attribute, not a module, is skipped)."""
    seen: set[str] = set()
    todo = list(entries)
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        text = read(rel)
        if text is None:
            continue
        seen.add(rel)
        for m in _imports(text):
            cand = m.replace(".", "/") + ".py"
            if cand not in seen:
                todo.append(cand)
    return sorted(seen)


def git_show(repo: str | Path, sha: str, rel: str) -> str | None:
    r = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def blob_id(repo: str | Path, sha: str, rel: str) -> str | None:
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{sha}:{rel}"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def blob_identity(repo: str | Path, sha_a: str, sha_b: str, files: Sequence[str]) -> dict:
    """{'files': n, 'identical': bool, 'differ': [...], 'missing': [...]} for `git rev-parse <sha>:<file>` at both shas."""
    differ, missing = [], []
    for f in files:
        a, b = blob_id(repo, sha_a, f), blob_id(repo, sha_b, f)
        if a is None or b is None:
            missing.append(f)
        elif a != b:
            differ.append(f)
    return {"files": len(files), "identical": not differ and not missing, "differ": differ, "missing": missing}


def models_listing(repo: str | Path, sha: str, rel: str = "models/r4b") -> list[str]:
    r = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", sha, rel], capture_output=True, text=True)
    return sorted(x for x in r.stdout.splitlines() if x)
