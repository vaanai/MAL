"""Class-substituted copy of a shadow replay dir (QP 10-23 md5 ruling, item 12 option (c), Run B).

h5_shadow at the frozen sha, run in replay mode, labels every plain trigger `"synthetic": false, "synthetic_src": "pre_event_binary"`; the executor accepts only "rpc".
This tool copies SHADOW_DIR to a NEW dir and changes ONLY that label, on `trigger` records whose `synthetic` is literally false and whose `synthetic_src` is exactly
"pre_event_binary": the value bytes `"pre_event_binary"` become `"rpc"` at the one place the field appears; every other byte of every file is copied unchanged.
It records the sha256 of every raw and rewritten file and the count of rewritten records, and refuses if the output exists. It prints no price, fill, exit or outcome.

The substitution proves decide_row equivalence GIVEN a plain class, not the class source (the DEC copy must say so).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

OLD, NEW = "pre_event_binary", "rpc"
_FIELD_RE = re.compile(rb'("synthetic_src"\s*:\s*)"pre_event_binary"')


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rewrite_line(line: bytes) -> tuple[bytes, bool]:
    """(line, changed). Only a trigger record with synthetic literally false and synthetic_src == "pre_event_binary" changes, in one place."""
    if b'"synthetic_src"' not in line or b'"trigger"' not in line:
        return line, False
    try:
        rec = json.loads(line)
    except ValueError:
        return line, False
    if not isinstance(rec, dict) or rec.get("type") != "trigger" or rec.get("synthetic") is not False or rec.get("synthetic_src") != OLD:
        return line, False
    matches = _FIELD_RE.findall(line)
    if len(matches) != 1:
        raise SystemExit(f"refused: synthetic_src label found {len(matches)} times in one trigger record (need exactly 1)")
    new = _FIELD_RE.sub(lambda m: m.group(1) + b'"' + NEW.encode() + b'"', line, count=1)
    want = dict(rec, synthetic_src=NEW)
    if json.loads(new) != want:  # exactly one field differs, and it is the one intended
        raise SystemExit("refused: the rewritten record differs from the raw one in more than synthetic_src")
    return new, True


def rewrite_dir(src: Path, dst: Path) -> dict:
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        raise SystemExit(f"refused: input {src} is not a directory")
    if os.path.lexists(dst):
        raise SystemExit(f"refused: output {dst} exists")
    s_res, d_res = src.resolve(), dst.parent.resolve() / dst.name
    if d_res == s_res or s_res in d_res.parents:
        raise SystemExit("refused: output is inside the input")
    os.mkdir(dst)  # create-exclusive
    files, total = [], 0
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_symlink():
            raise SystemExit(f"refused: symlink in input: {rel}")
        if p.is_dir():
            (dst / rel).mkdir(exist_ok=True)
            continue
        out = dst / rel
        n = 0
        if p.suffix == ".jsonl":
            with open(p, "rb") as fi, open(out, "xb") as fo:
                for line in fi:
                    new, ch = rewrite_line(line)
                    n += ch
                    fo.write(new)
            shutil.copymode(p, out)
        else:
            shutil.copyfile(p, out)
        total += n
        files.append({"file": str(rel), "raw_sha256": sha256_file(p), "rewritten_sha256": sha256_file(out), "records_rewritten": n})
    return {"input": str(src), "output": str(dst), "rewritten_records": total, "files": files}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="copy a shadow replay dir, rewriting only trigger synthetic_src pre_event_binary -> rpc")
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--dst", required=True, type=Path)
    ap.add_argument("--manifest", type=Path, help="also write the JSON here (create-exclusive)")
    a = ap.parse_args(argv)
    res = rewrite_dir(a.src, a.dst)
    text = json.dumps(res, sort_keys=True)
    if a.manifest:
        with open(a.manifest, "x") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
