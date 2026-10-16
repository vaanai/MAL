#!/usr/bin/env python3
"""Idea library: validate docs/library/ideas.jsonl and render docs/library/INDEX.md.

  python3 tools/library_index.py            # validate, then rewrite INDEX.md
  python3 tools/library_index.py --check    # validate, exit 1 if INDEX.md is not what the rows render to

One JSON object per line in ideas.jsonl. Required keys: id, name, family, mechanism, status, evidence, why_stopped,
revive_trigger, next_step, source_date, section, check. Optional: status_source, duplicate_of, duplicate_note, manager_note.
The page is generated: edit the rows (or META.json), never INDEX.md. Standard library only. No network, no market data.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "docs" / "library"

REQUIRED = ("id", "name", "family", "mechanism", "status", "evidence", "why_stopped", "revive_trigger", "next_step",
            "source_date", "section", "check")
OPTIONAL = ("status_source", "duplicate_of", "duplicate_note", "manager_note")
# status -> plain words for the owner
STATUS = collections.OrderedDict([
    ("live-canary", "trading real money at the smallest stake, as a measurement"),
    ("forward-test", "a registered forward read is running or waiting; nobody has looked at its outcomes"),
    ("explored-survivor-unconfirmed", "positive on exploration data, not confirmed on data it has never seen"),
    ("parked", "set aside by a rule or a decision; a named condition reopens it"),
    ("shelved", "stopped by the owner or by a finding; kept with its revive trigger"),
    ("untested", "proposed, never run"),
    ("desk-read", "read from documents only; no data run"),
    ("explored-no-survivor", "run on exploration data; nothing survived"),
    ("dead-as-frozen", "the frozen version failed its test; a new version would be a new idea"),
])
SECTIONS = ("library", "inbox-2026-10-13", "owner-ideas", "push-1016")
H5_LABEL = "execution measurement; EXP-024 ended NOT_DECIDABLE on 2026-10-13 with no look read; not a book"
GATE_SENTENCE = "H5 has not cleared the promotion gate"


class LibraryError(ValueError):
    pass


def load_rows(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as e:
            raise LibraryError(f"{path.name} line {n}: not JSON ({e})") from None
        if not isinstance(row, dict):
            raise LibraryError(f"{path.name} line {n}: not an object")
        rows.append(row)
    return rows


def validate(rows: list[dict]) -> None:
    seen: dict[str, dict] = {}
    for n, r in enumerate(rows, 1):
        rid = r.get("id")
        where = f"row {n} ({rid!r})"
        for k in REQUIRED:
            if not isinstance(r.get(k), str) or not r[k].strip():
                raise LibraryError(f"{where}: key {k!r} missing or empty")
        for k in r:
            if k not in REQUIRED and k not in OPTIONAL:
                raise LibraryError(f"{where}: unknown key {k!r}")
            if not isinstance(r[k], str):
                raise LibraryError(f"{where}: key {k!r} is not a string")
        if r["status"] not in STATUS:
            raise LibraryError(f"{where}: status {r['status']!r} is not one of {list(STATUS)}")
        if r["section"] not in SECTIONS:
            raise LibraryError(f"{where}: section {r['section']!r} is not one of {list(SECTIONS)}")
        if rid in seen:
            raise LibraryError(f"{where}: duplicate id")
        seen[rid] = r
    for r in rows:
        d = r.get("duplicate_of")
        if d is None:
            continue
        if d == r["id"] or d not in seen:
            raise LibraryError(f"row {r['id']!r}: duplicate_of {d!r} is not another row")
        if "duplicate_of" in seen[d]:
            raise LibraryError(f"row {r['id']!r}: duplicate_of {d!r} is itself a duplicate")
    for r in rows:
        if r["status"] == "live-canary" and "duplicate_of" not in r:
            shown = r.get("manager_note") or r["why_stopped"]  # the text the page prints for this row
            if H5_LABEL not in shown or GATE_SENTENCE not in shown:
                raise LibraryError(f"row {r['id']!r}: a live-canary row must carry the canary label and the gate sentence in the text the page shows")


def cell(text: str, limit: int = 0) -> str:
    """One table cell: single line, pipes escaped, cut at a word boundary with an ellipsis when over the limit."""
    s = " ".join(str(text).split()).replace("|", "\\|")
    if limit and len(s) > limit:
        cut = s[:limit].rsplit(" ", 1)[0].rstrip(" ,;:(")
        if cut.endswith("\\"):
            cut = cut[:-1]
        s = cut + " …"
    return s


def table(header: tuple[str, ...], body: list[tuple[str, ...]]) -> list[str]:
    if not body:
        return ["None.", ""]
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(row) + " |" for row in body]
    return out + [""]


def stands(r: dict, limit: int) -> str:
    return cell(r.get("manager_note") or r["why_stopped"], limit)


def with_note(text: str, r: dict, limit: int) -> str:
    """A cell that never hides a manager's note: the note comes first, then the row's own text."""
    note = r.get("manager_note")
    return cell(f"Note: {note} {text}" if note else text, limit)


def render(rows: list[dict], meta: dict) -> str:
    main = [r for r in rows if r["section"] == "library" and "duplicate_of" not in r]
    dups = [r for r in rows if "duplicate_of" in r]
    inbox = [r for r in rows if r["section"].startswith("inbox") and "duplicate_of" not in r]
    owner = [r for r in rows if r["section"] == "owner-ideas"]
    by = lambda rs, *st: [r for r in rs if r["status"] in st]
    L: list[str] = []
    L += ["# MAL idea library", "",
          f"As of {meta['as_of']}. One row per idea in [`ideas.jsonl`](ideas.jsonl). This page is generated by `tools/library_index.py`; "
          "change the rows, not this page.", ""]
    L += ["## Read this first", "",
          "- **Nothing on this page is a claim that a strategy makes money.** A book goes live only through the promotion gate and the owner's approval.",
          f"- **H5 is the only thing trading.** It is labelled \"{H5_LABEL}\". H5 has not cleared the promotion gate.",
          "- **The library rows are a conversion, not a verified record.** A judge sampled 20 of the 170 library rows and found 2 errors, rated one source file "
          "not usable as it was, and listed rows with a stale status; the corrections were made by Haiku agents and were not judged again. "
          "Each row's `check` field says who wrote it and how far it was checked. Verify any number at the row's `evidence` path before quoting.",
          "- Exploration results (statuses \"explored\") are on data the lab has already looked at. They are not out-of-sample evidence.", ""]
    L += ["## What the statuses mean", ""]
    L += table(("Status", "Meaning"), [(f"`{s}`", cell(m)) for s, m in STATUS.items()])
    L += ["## Counts", ""]
    cnt = lambda rs, s: str(sum(1 for r in rs if r["status"] == s))
    body = [(f"`{s}`", cnt(main, s), cnt(inbox, s), cnt(owner, s)) for s in STATUS]
    body.append(("**all**", str(len(main)), str(len(inbox)), str(len(owner))))
    L += table(("Status", "Library", "Inbox of 2026-10-13", "Owner ideas"), body)
    L += [f"The file holds {len(rows)} rows: the {len(main) + len(inbox) + len(owner)} above and {len(dups)} rows kept only as duplicates of another row (listed at the end).", ""]
    L += ["## Live and in forward test", ""]
    L += table(("Id", "Idea", "Status", "Where it stands", "Next step"),
               [(f"`{r['id']}`", cell(r["name"], 90), f"`{r['status']}`", stands(r, 420), cell(r["next_step"], 200))
                for r in by(main, "live-canary", "forward-test")])
    L += ["## Survivors on exploration data, not confirmed", "",
          "Each of these was positive on data the lab had already seen and has no clean forward read. Several are small or fragile; read the note.", ""]
    L += table(("Id", "Idea", "Where it stands", "Next step"),
               [(f"`{r['id']}`", cell(r["name"], 90), stands(r, 420), cell(r["next_step"], 200))
                for r in by(main, "explored-survivor-unconfirmed")])
    L += ["## Parked and shelved, with what would reopen them", ""]
    L += table(("Id", "Idea", "Status", "Why it stopped", "What would reopen it"),
               [(f"`{r['id']}`", cell(r["name"], 90), f"`{r['status']}`", stands(r, 320), cell(r["revive_trigger"], 220))
                for r in by(main, "parked", "shelved")])
    L += ["## Never tested", ""]
    L += table(("Id", "Idea", "Family", "Next step"),
               [(f"`{r['id']}`", cell(r["name"], 90), cell(r["family"], 50), with_note(r["next_step"], r, 560))
                for r in by(main, "untested", "desk-read")])
    L += ["## Owner ideas", ""]
    L += table(("Id", "Idea", "Status", "Where it stands", "Next step"),
               [(f"`{r['id']}`", cell(r["name"], 110), f"`{r['status']}`", stands(r, 420), cell(r["next_step"], 260)) for r in owner])
    L += ["## Inbox of 2026-10-13: new leads and same-day results", "",
          "Rows written by research agents on 2026-10-13. A judge read each web agent's whole report and checked a list of claims "
          "(judge-web/JUDGE.md, section B); it named what it did not check (section E). A row's own figures are checked only if they are on that list. "
          "A lead here is something to count, not something to trade.", ""]
    groups: "collections.OrderedDict[str, list[dict]]" = collections.OrderedDict()
    for r in inbox:
        groups.setdefault(r["id"].rsplit("-", 1)[0], []).append(r)
    for g, rs in groups.items():
        L += [f"### {g} ({len(rs)})", ""]
        L += table(("Id", "Idea", "Status", "Note"),
                   [(f"`{r['id']}`", cell(r["name"], 100), f"`{r['status']}`", cell(r["why_stopped"], 320)) for r in rs])
    L += ["## Dead as frozen, and explored with no survivor", "",
          "\"Dead\" means that frozen version failed. A changed version is a new row with its own test.", ""]
    L += table(("Id", "Idea", "Status", "Why"),
               [(f"`{r['id']}`", cell(r["name"], 90), f"`{r['status']}`", with_note(r["why_stopped"], r, 380))
                for r in by(main, "explored-no-survivor", "dead-as-frozen")])
    L += ["## Duplicate rows", "", "Kept for traceability to the source files. Each points at the row to read instead.", ""]
    L += table(("Id", "Idea", "Read instead"),
               [(f"`{r['id']}`", cell(r["name"], 90), f"`{r['duplicate_of']}`") for r in dups])
    L += ["## Where the rows came from", ""]
    L += [f"- {cell(s)}" for s in meta.get("sources", [])] + [""]
    L += ["## Rulings made when the library was assembled", ""]
    L += [f"- {cell(s)}" for s in meta.get("rulings", [])] + [""]
    L += ["## How to add or change an idea", "",
          "1. Append one line to `docs/library/ideas.jsonl` (or edit the idea's line) with the twelve required keys. "
          "`section` is `library`, `owner-ideas` or an inbox name listed in `tools/library_index.py`; `check` says who wrote the row and what was verified.",
          "2. A new idea starts as `untested`. Change the status only with the source of the verdict in `status_source`.",
          "3. Run `python3 tools/library_index.py` to rebuild this page, and `python3 -m pytest tools/test_library_index.py`.",
          "4. Counts and copied figures only. Never write that an idea makes money; the promotion gate decides that.", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, default=LIB, help="library directory (default docs/library)")
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if INDEX.md differs from the render")
    a = ap.parse_args(argv)
    try:
        rows = load_rows(a.dir / "ideas.jsonl")
        validate(rows)
        meta = json.loads((a.dir / "META.json").read_text(encoding="utf-8"))
        if not isinstance(meta.get("as_of"), str):
            raise LibraryError("META.json: as_of missing")
    except (LibraryError, OSError, ValueError) as e:
        print(f"library_index: {e}", file=sys.stderr)
        return 2
    text = render(rows, meta)
    index = a.dir / "INDEX.md"
    if a.check:
        if not index.exists() or index.read_text(encoding="utf-8") != text:
            print("library_index: INDEX.md is not what the rows render to; run tools/library_index.py", file=sys.stderr)
            return 1
        print(f"library_index: ok, {len(rows)} rows")
        return 0
    index.write_text(text, encoding="utf-8")
    print(f"library_index: wrote {index} from {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
