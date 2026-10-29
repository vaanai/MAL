#!/usr/bin/env python3
"""Check that every cited blockquote in docs/rules/*.md is verbatim in its source.

Format checked: a blockquote (consecutive lines starting with '>'), followed
(after optional blank lines) by a citation line that holds `(<path>:<line>)`.
The quoted text, whitespace-normalised, must appear in <path> (whitespace
normalised) and must START on the cited line (the cited line must hold the
first character of the match). A blockquote with no citation is a failure.

Also: the quote must start and end on word boundaries (no mid-word or
mid-number substring), an indented blockquote ("^\\s+>") is a failure, and a
citation line with two different (path:line) cites is a failure.
--rev REV reads the cited files with `git show REV:path` (run inside --root)
instead of the working tree. The checker proves verbatim text only, not that a
clause is in force or complete.

usage: rules-quote-check.py [--root DIR] [--rev REV] [FILE.md ...]   (default: ROOT/docs/rules/*.md)
Prints failures. Exit 0 = none, 1 = at least one failure, 2 = usage error.
Stdlib only. Reads files from the working tree at --root (run it on a clean main checkout).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

CITE = re.compile(r"\(([^()\s:]+):(\d+)\)")


def normalise(text):
    """Return (normalised string, list mapping each normalised char to a source line number)."""
    out, lines = [], []
    line = 1
    pending_space = False
    for ch in text:
        if ch.isspace():
            pending_space = True
            if ch == "\n":
                line += 1
            continue
        if pending_space and out:
            out.append(" ")
            lines.append(line)
        pending_space = False
        out.append(ch)
        lines.append(line)
    return "".join(out), lines


def blockquotes(md_text):
    """Yield (first_line_no, quote_text, citation_match or None, cite_line_no)."""
    rows = md_text.split("\n")
    i = 0
    while i < len(rows):
        if re.match(r"^\s+>", rows[i]):
            yield i + 1, "", "INDENTED", None
            i += 1
            continue
        if rows[i].startswith(">"):
            start = i
            parts = []
            while i < len(rows) and rows[i].startswith(">"):
                parts.append(re.sub(r"^>\s?", "", rows[i]))
                i += 1
            j = i
            while j < len(rows) and rows[j].strip() == "":
                j += 1
            m = CITE.search(rows[j]) if j < len(rows) else None
            if m and len(set(CITE.findall(rows[j]))) > 1:
                m = "MULTI"
            yield start + 1, "\n".join(parts), m, (j + 1 if m else None)
            continue
        i += 1


def read_source(root, rel, rev):
    if rev:
        r = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{rel}"],
                           capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None
    src = root / rel
    return src.read_text(encoding="utf-8") if src.is_file() else None


def word_ok(sn, pos, qn):
    if qn[0].isalnum() and pos > 0 and (sn[pos - 1].isalnum() or sn[pos - 1] == "_"):
        return False
    end = pos + len(qn)
    if qn[-1].isalnum() and end < len(sn) and (sn[end].isalnum() or sn[end] == "_"):
        return False
    return True


def check_file(md_path, root, cache, rev=None):
    failures, n_cites = [], 0
    text = md_path.read_text(encoding="utf-8")
    for qline, quote, m, _cl in blockquotes(text):
        if m == "INDENTED":
            failures.append(f"{md_path}:{qline}: indented blockquote (not checked; fix it)")
            continue
        qn, _ = normalise(quote)
        if not qn:
            continue
        if m == "MULTI":
            failures.append(f"{md_path}:{qline}: citation line holds more than one (path:line)")
            continue
        if m is None:
            failures.append(f"{md_path}:{qline}: blockquote has no (path:line) citation")
            continue
        n_cites += 1
        rel, ln = m.group(1), int(m.group(2))
        if rel not in cache:
            body = read_source(root, rel, rev)
            cache[rel] = None if body is None else normalise(body)
        if cache[rel] is None:
            failures.append(f"{md_path}:{qline}: cited file missing: {rel}")
            continue
        sn, slines = cache[rel]
        pos, found = -1, False
        while True:
            pos = sn.find(qn, pos + 1)
            if pos < 0:
                break
            if not word_ok(sn, pos, qn):
                continue
            found = True
            if slines[pos] == ln:
                break
        if pos < 0 and not found:
            failures.append(f"{md_path}:{qline}: quote not verbatim in {rel}: {qn[:70]!r}")
        elif pos < 0:
            failures.append(f"{md_path}:{qline}: quote found in {rel} but does not start on line {ln}: {qn[:70]!r}")
    return failures, n_cites


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--rev", default=None)
    ap.add_argument("files", nargs="*")
    a = ap.parse_args(argv)
    root = Path(a.root)
    files = [Path(f) for f in a.files] or sorted((root / "docs" / "rules").glob("*.md"))
    cache, fails, total = {}, [], 0
    for f in files:
        fl, n = check_file(f, root, cache, a.rev)
        fails += fl
        total += n
    for line in fails:
        print("FAIL " + line)
    print(f"rules-quote-check: {total} citations checked, {len(fails)} failures")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
