#!/usr/bin/env python3
"""The MAL data catalog and read guard.

This is the enforcement point for docs/console-plan.md §2 rule 1 ("the
owner's and DeepSeek's tests run on exploration data only") and §9 item 3
("a data catalog ... the job runner refuses any read the catalog doesn't
allow for the job's role"). It has exactly one source of truth:
docs/HOLDOUT_LEDGER.md, the manager-edited ledger. This module never grants
a read the ledger doesn't already grant -- it only parses the ledger and
answers yes/no questions about it. If the ledger and this module ever
disagree, the ledger wins; fix the parser, not the answer.

Two independently useful halves:

  - `parse_ledger` / `allowed` / `check_read`: pure parsing and a pure
    access-policy function, plus the read guard that job runners should
    call before opening any sealed-hour file. Deny is the default: an
    unparseable row raises (never silently skipped), an hour not covered
    by any ledger block is denied ("not in ledger"), and an unrecognized
    role is denied.
  - `build_catalog`: a JSON snapshot of the parsed ledger (for the Console's
    Data screen) plus, optionally, each walker's checkpoint progress read
    read-only from its `checkpoint.json` (schema confirmed by inspecting a
    real file: top-level `credits_used` and a `hours` map of
    `"YYYY-MM-DDTHH" -> {"status": "sealed" | ...}`).

Precedence rule (mirrors the ledger's own "Fast EXP-009 exclusion" row):
an explicit single-hour row overrides the broader ranged block that
contains it. Two ranged (non-explicit) blocks may never overlap on the
same host -- that would mean the ledger gave one hour two owners, which
rule 5 in HOLDOUT_LEDGER.md forbids -- and `parse_ledger` raises if it
finds that.

Second owners. A ledger Status cell may carry one or more machine markers,

    SECOND-OWNER: EXP-022 [2026-10-09T00, 2026-10-16T01)

naming an experiment that the ledger discloses as a second reader of part of
a block that another EXP-### owns (ledger rule 3). The block's `owner` does
not change: it stays the owner of record. A marker only lets
role=confirmation-oneshot with exactly that exp_id read exactly those hours
of that block. It never widens `exploration`, `ops`, or any other exp_id, and
it never overrides an explicit single-hour row. The token `SECOND-OWNER`
(any case) is reserved for the marker: any occurrence that is not a
well-formed marker, a marker whose range leaves the block's own Hours, and a
marker on a block that no EXP-### owns, make `parse_ledger` raise. The guard
is by hour only; it does not model "sealed until X is written". The tool that
reads the hours enforces that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HOUR_FMT = "%Y-%m-%dT%H"
HOUR_TOKEN = r"\d{4}-\d{2}-\d{2}T\d{2}"
HOUR_TOKEN_RE = re.compile(rf"^{HOUR_TOKEN}$")

_BRACKET_RE = re.compile(rf"\[\s*({HOUR_TOKEN})\s*,\s*({HOUR_TOKEN})\s*\)")
_ARROW_RE = re.compile(rf"({HOUR_TOKEN})(?::(\d{{2}}))?Z?\s*(?:→|->)\s*({HOUR_TOKEN})(?::(\d{{2}}))?Z?")
_OLDER_THAN_RE = re.compile(rf"older than\s+({HOUR_TOKEN})", re.IGNORECASE)
_EXP_OWNER_RE = re.compile(r"EXP-(\d+)")
# Second-owner marker: strict, single spaces, half-open bracket range. The
# token regex is deliberately looser (any case) so a typo cannot be silently
# ignored: every token must be part of a strict match or the ledger is refused.
_SECOND_OWNER_TOKEN_RE = re.compile(r"SECOND-OWNER", re.IGNORECASE)
_SECOND_OWNER_RE = re.compile(
    rf"(?<![A-Za-z0-9_-])SECOND-OWNER: (EXP-\d{{3,}}) \[({HOUR_TOKEN}), ({HOUR_TOKEN})\)"
)

ROLES = ("exploration", "confirmation-oneshot", "ops")


def _parse_hour(s: str) -> datetime:
    return datetime.strptime(s, HOUR_FMT).replace(tzinfo=timezone.utc)


def _fmt_hour(dt: datetime) -> str:
    return dt.strftime(HOUR_FMT)


_NEG_INF = datetime.min.replace(tzinfo=timezone.utc)
_POS_INF = datetime.max.replace(tzinfo=timezone.utc)


@dataclass
class Block:
    """One row of docs/HOLDOUT_LEDGER.md's Table, parsed.

    Either `explicit_hours` is set (a disclosed single-hour exception row,
    e.g. the EXP-009 exclusion), or `start_hour`/`end_hour_exclusive` is
    set (a normal half-open range; `None` on either end means unbounded in
    that direction -- only the "Future fast backfill / Older than ..." row
    currently uses an open start).

    `second_owners` holds `(exp_id, start_hour, end_hour_exclusive)` triples
    parsed from `SECOND-OWNER:` markers in the Status cell. `owner` is never
    changed by them (see the module docstring).
    """

    name: str
    host: str
    owner: str
    status: str
    start_hour: str | None = None
    end_hour_exclusive: str | None = None
    explicit_hours: tuple[str, ...] | None = None
    second_owners: tuple[tuple[str, str, str], ...] = ()
    _explicit_set: frozenset[str] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._explicit_set = frozenset(self.explicit_hours) if self.explicit_hours else None

    def covers_hour(self, dt: datetime) -> bool:
        if self._explicit_set is not None:
            return _fmt_hour(dt) in self._explicit_set
        start = _parse_hour(self.start_hour) if self.start_hour else _NEG_INF
        end = _parse_hour(self.end_hour_exclusive) if self.end_hour_exclusive else _POS_INF
        return start <= dt < end


# --- Parsing ------------------------------------------------------------


def _table_rows(md_text: str) -> list[list[str]]:
    lines = md_text.splitlines()
    try:
        start_idx = next(i for i, line in enumerate(lines) if line.strip() == "## Table")
    except StopIteration as exc:
        raise ValueError("ledger markdown is missing a '## Table' heading") from exc

    rows: list[list[str]] = []
    header_seen = False
    for line in lines[start_idx + 1 :]:
        stripped = line.strip()
        if not stripped or not stripped.startswith("|"):
            if header_seen:
                break
            continue
        inner = stripped.strip("|")
        if not header_seen:
            header_seen = True
            continue
        if set(inner.replace("|", "").strip()) <= set("-: "):
            continue  # the "| --- | --- | ... |" separator row
        cells = [c.strip() for c in inner.split("|")]
        if len(cells) != 5:
            raise ValueError(f"ledger table row does not have 5 cells (Block/Hours/Host/Owner/Status): {line!r}")
        rows.append(cells)
    if not rows:
        raise ValueError("no data rows found under the ledger's '## Table' heading")
    return rows


def _parse_hours_cell(cell: str, *, row_name: str) -> tuple[str | None, str | None, tuple[str, ...] | None]:
    text = cell.replace("`", "").strip()

    m = _BRACKET_RE.search(text)
    if m:
        return m.group(1), m.group(2), None

    m = _ARROW_RE.search(text)
    if m:
        start, start_min, end, end_min = m.groups()
        if start_min not in (None, "00") or end_min not in (None, "00"):
            raise ValueError(f"row {row_name!r}: non-hour-aligned timestamp in Hours cell {cell!r}")
        return start, end, None

    m = _OLDER_THAN_RE.search(text)
    if m:
        return None, m.group(1), None

    parts = [p.strip() for p in text.split(",")] if text else []
    if parts and all(HOUR_TOKEN_RE.match(p) for p in parts):
        return None, None, tuple(parts)

    raise ValueError(f"row {row_name!r}: could not parse Hours cell {cell!r} (no bracket range, arrow range, 'older than', or explicit hour list found)")


def _normalize_owner(cell: str, *, row_name: str) -> str:
    text = cell.replace("**", "").replace("`", "").strip()
    lowered = text.lower()
    # A reserved block has no owner yet. Its text may mention an EXP id ("the
    # test after EXP-012"); that must never be read as the owner. Reserved is
    # denied for every role by allowed().
    if lowered.startswith("reserved"):
        return "reserved"
    m = _EXP_OWNER_RE.search(text)
    if m:
        return f"EXP-{m.group(1)}"
    if "exploration pool" in lowered:
        return "exploration-pool"
    if "kill review" in lowered:
        return "kill-review"
    if "unassigned" in lowered:
        return "unassigned"
    raise ValueError(f"row {row_name!r}: unrecognized Owner cell {cell!r}")


def _normalize_host(cell: str, *, row_name: str) -> str:
    text = cell.replace("`", "").strip()
    lowered = text.lower()
    if "forward-paper runner" in lowered:
        return "oracle-forward"
    m_host = re.match(r"^mal-([a-z]+)-(\d+)\b", lowered)
    if m_host:
        known = {"research-0": "research", "fast-0": "fast", "core-0": "oracle"}
        key = f"{m_host.group(1)}-{m_host.group(2)}"
        if key not in known:
            raise ValueError(f"row {row_name!r}: unknown mal-* host in Host cell {cell!r}")
        return known[key]
    m = re.match(r"^([A-Za-z][A-Za-z0-9_]*)", text)
    if not m:
        raise ValueError(f"row {row_name!r}: could not determine a host from Host cell {cell!r}")
    return m.group(1).lower()


def _parse_second_owners(
    status_cell: str,
    *,
    row_name: str,
    owner: str,
    start: str | None,
    end: str | None,
    explicit: tuple[str, ...] | None,
) -> tuple[tuple[str, str, str], ...]:
    """Read the `SECOND-OWNER:` markers out of a Status cell. Raises
    `ValueError` for a malformed marker, an empty or reversed range, a range
    outside the block's own Hours, a block that is not owned by an EXP-###,
    or a second owner that is the owner itself."""
    text = status_cell.replace("`", "")
    n_tokens = len(_SECOND_OWNER_TOKEN_RE.findall(text))
    if n_tokens == 0:
        return ()
    matches = list(_SECOND_OWNER_RE.finditer(text))
    if len(matches) != n_tokens:
        raise ValueError(
            f"row {row_name!r}: malformed SECOND-OWNER marker in Status cell ({n_tokens} token(s), {len(matches)} well-formed); "
            "the form is 'SECOND-OWNER: EXP-### [YYYY-MM-DDTHH, YYYY-MM-DDTHH)', and the token SECOND-OWNER is reserved for it"
        )
    if not owner.startswith("EXP-"):
        raise ValueError(f"row {row_name!r}: SECOND-OWNER marker on a block whose owner is {owner!r}; only an EXP-### owned block can have a second owner")

    found: list[tuple[str, str, str]] = []
    for m in matches:
        exp_id, s_hour, e_hour = m.groups()
        label = f"row {row_name!r}: SECOND-OWNER {exp_id} [{s_hour}, {e_hour})"
        try:
            s_dt, e_dt = _parse_hour(s_hour), _parse_hour(e_hour)
        except ValueError as exc:
            raise ValueError(f"{label}: not a valid UTC hour ({exc})") from exc
        if exp_id == owner:
            raise ValueError(f"{label}: the second owner is the block's own owner")
        if e_dt <= s_dt:
            raise ValueError(f"{label}: the range is empty or reversed")
        if explicit is None:
            block_start = _parse_hour(start) if start else _NEG_INF
            block_end = _parse_hour(end) if end else _POS_INF
            if s_dt < block_start or e_dt > block_end:
                raise ValueError(f"{label}: the range is outside the block's own Hours [{start}, {end})")
        else:
            n_hours = (e_dt - s_dt) // timedelta(hours=1)
            hours = {_fmt_hour(s_dt + timedelta(hours=i)) for i in range(n_hours)} if n_hours <= len(explicit) else None
            if hours is None or not hours <= set(explicit):
                raise ValueError(f"{label}: the range is outside the block's explicit hours {list(explicit)}")
        found.append((exp_id, s_hour, e_hour))
    return tuple(found)


def _ranges_overlap(a: Block, b: Block) -> bool:
    a_start = _parse_hour(a.start_hour) if a.start_hour else _NEG_INF
    a_end = _parse_hour(a.end_hour_exclusive) if a.end_hour_exclusive else _POS_INF
    b_start = _parse_hour(b.start_hour) if b.start_hour else _NEG_INF
    b_end = _parse_hour(b.end_hour_exclusive) if b.end_hour_exclusive else _POS_INF
    return a_start < b_end and b_start < a_end


def _validate_no_bad_overlaps(blocks: list[Block]) -> None:
    by_host: dict[str, list[Block]] = {}
    for b in blocks:
        by_host.setdefault(b.host, []).append(b)
    for host, host_blocks in by_host.items():
        ranged = [b for b in host_blocks if b._explicit_set is None]
        explicit = [b for b in host_blocks if b._explicit_set is not None]

        for i in range(len(ranged)):
            for j in range(i + 1, len(ranged)):
                if _ranges_overlap(ranged[i], ranged[j]):
                    raise ValueError(
                        f"host {host!r}: overlapping blocks {ranged[i].name!r} and {ranged[j].name!r} both claim at least one hour -- "
                        "the ledger forbids two owners for one hour (rule 5)"
                    )

        seen: dict[str, str] = {}
        for b in explicit:
            for h in b.explicit_hours or ():
                if h in seen:
                    raise ValueError(f"host {host!r}: hour {h} is claimed by two explicit-hour rows, {seen[h]!r} and {b.name!r}")
                seen[h] = b.name


def parse_ledger(md_text: str) -> list[Block]:
    """Parse docs/HOLDOUT_LEDGER.md's '## Table' into `Block`s.

    Raises `ValueError` on anything it cannot parse or on a forbidden
    overlap -- it never silently drops a row.
    """
    blocks: list[Block] = []
    for name, hours_cell, host_cell, owner_cell, status_cell in _table_rows(md_text):
        start, end, explicit = _parse_hours_cell(hours_cell, row_name=name)
        owner = _normalize_owner(owner_cell, row_name=name)
        host = _normalize_host(host_cell, row_name=name)
        second_owners = _parse_second_owners(status_cell, row_name=name, owner=owner, start=start, end=end, explicit=explicit)
        blocks.append(
            Block(
                name=name,
                host=host,
                owner=owner,
                status=status_cell,
                start_hour=start,
                end_hour_exclusive=end,
                explicit_hours=explicit,
                second_owners=second_owners,
            )
        )
    _validate_no_bad_overlaps(blocks)
    return blocks


# --- Access policy (pure) -------------------------------------------------

_EXP_ID_RE = re.compile(r"EXP-\d{3,}")


def allowed(role: str, owner: str, exp_id: str | None = None) -> bool:
    """The read-guard policy. Deny by default -- an unrecognized role or
    owner is a deny, not an allow."""
    if role == "exploration":
        return owner == "exploration-pool"
    if role == "confirmation-oneshot":
        # Only a real EXP-### owner can be unlocked, and only by that exact id.
        # Pool, kill-review and unassigned tags are never valid exp_ids.
        return (
            exp_id is not None
            and _EXP_ID_RE.fullmatch(exp_id) is not None
            and owner == exp_id
        )
    if role == "ops":
        return False
    return False


def second_owner_allows(block: Block, role: str, exp_id: str | None, dt: datetime) -> bool:
    """True only for role=confirmation-oneshot, with an exp_id that `block`
    lists as a second owner of a range containing `dt`. `block` must be the
    block that covers `dt` (see `_block_for_hour`). Deny by default."""
    if role != "confirmation-oneshot":
        return False
    if exp_id is None or _EXP_ID_RE.fullmatch(exp_id) is None:
        return False
    return any(
        sid == exp_id and _parse_hour(s) <= dt < _parse_hour(e)
        for sid, s, e in block.second_owners
    )


def _block_for_hour(blocks: list[Block], host: str, dt: datetime) -> Block | None:
    host_blocks = [b for b in blocks if b.host == host]
    for b in host_blocks:
        if b._explicit_set is not None and _fmt_hour(dt) in b._explicit_set:
            return b  # explicit single-hour rows override broader blocks
    for b in host_blocks:
        if b._explicit_set is None and b.covers_hour(dt):
            return b
    return None


def _owner_for_hour(blocks: list[Block], host: str, dt: datetime) -> str | None:
    b = _block_for_hour(blocks, host, dt)
    return b.owner if b is not None else None


def _is_next_hour(a: str, b: str) -> bool:
    return _parse_hour(b) - _parse_hour(a) == timedelta(hours=1)


def _group_reasons(denied: list[tuple[str, str]]) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(denied)
    while i < n:
        j = i
        while j + 1 < n and denied[j + 1][1] == denied[i][1] and _is_next_hour(denied[j][0], denied[j + 1][0]):
            j += 1
        if i == j:
            out.append(f"{denied[i][0]}: {denied[i][1]}")
        else:
            out.append(f"{denied[i][0]}..{denied[j][0]} ({j - i + 1} hours): {denied[i][1]}")
        i = j + 1
    return out


def check_read(
    blocks: list[Block],
    role: str,
    host: str,
    start_hour: str,
    end_hour_exclusive: str,
    exp_id: str | None = None,
) -> tuple[bool, list[str]]:
    """Every hour in `[start_hour, end_hour_exclusive)` on `host` must be
    covered by a ledger block AND allowed for `role` (see `allowed`).
    Returns `(True, [])` on ALLOW, or `(False, reasons)` listing which
    hours (grouped into contiguous spans) were denied and why.

    An hour that `allowed` denies is still allowed for role=confirmation-oneshot
    when the block covering that hour lists `exp_id` as a second owner of a
    range containing the hour (`second_owner_allows`). Nothing else changes."""
    start_dt = _parse_hour(start_hour)
    end_dt = _parse_hour(end_hour_exclusive)
    if end_dt <= start_dt:
        raise ValueError(f"end_hour_exclusive {end_hour_exclusive!r} must be after start_hour {start_hour!r}")

    denied: list[tuple[str, str]] = []
    cur = start_dt
    while cur < end_dt:
        hstr = _fmt_hour(cur)
        block = _block_for_hour(blocks, host, cur)
        if block is None:
            denied.append((hstr, "not in ledger"))
        elif not allowed(role, block.owner, exp_id) and not second_owner_allows(block, role, exp_id, cur):
            suffix = f" exp_id={exp_id}" if exp_id is not None else ""
            denied.append((hstr, f"owner={block.owner} not allowed for role={role}{suffix}"))
        cur += timedelta(hours=1)

    if not denied:
        return True, []
    return False, _group_reasons(denied)


# --- Catalog (data/catalog.json) -----------------------------------------


def _block_to_doc(b: Block) -> dict[str, Any]:
    if b._explicit_set is not None:
        hours: dict[str, Any] = {"explicit": list(b.explicit_hours or ())}
    else:
        hours = {"start": b.start_hour, "end_exclusive": b.end_hour_exclusive}
    doc: dict[str, Any] = {
        "name": b.name,
        "host": b.host,
        "owner": b.owner,
        "hours": hours,
        "status": b.status,
        "access": {
            "exploration": allowed("exploration", b.owner),
            "confirmation_oneshot_exp": b.owner if b.owner.startswith("EXP-") else None,
        },
    }
    if b.second_owners:  # additive; absent for every block without a marker, so existing output is unchanged
        doc["second_owners"] = [{"exp_id": e, "start": s, "end_exclusive": x} for e, s, x in b.second_owners]
    return doc


def _walker_doc(name: str, dir_or_file: str) -> dict[str, Any]:
    path = Path(dir_or_file)
    cp_path = path / "checkpoint.json" if path.is_dir() else path
    doc: dict[str, Any] = {"name": name, "path": str(cp_path)}
    try:
        raw = cp_path.read_text(encoding="utf-8")
    except OSError as exc:
        doc["error"] = f"checkpoint unreadable: {exc}"
        return doc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        doc["error"] = f"checkpoint is not valid JSON: {exc}"
        return doc
    hours_map = data.get("hours") if isinstance(data, dict) else None
    if not isinstance(hours_map, dict):
        doc["error"] = "checkpoint has no 'hours' map"
        return doc
    total = len(hours_map)
    sealed = sum(1 for v in hours_map.values() if isinstance(v, dict) and v.get("status") == "sealed")
    doc.update(
        {
            "sealed": sealed,
            "total": total,
            "sealed_all": bool(total) and sealed == total,
            "credits_used": data.get("credits_used"),
        }
    )
    return doc


def build_catalog(ledger_path: str | Path, checkpoint_dirs: dict[str, str] | None = None) -> dict[str, Any]:
    """Build the catalog.v1 document. Deterministic given the same ledger
    bytes and checkpoint files, except `generated_utc`."""
    ledger_path = Path(ledger_path)
    raw = ledger_path.read_bytes()
    blocks = parse_ledger(raw.decode("utf-8"))

    doc: dict[str, Any] = {
        "schema_version": "catalog.v1",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ledger_sha256": hashlib.sha256(raw).hexdigest(),
        "blocks": [_block_to_doc(b) for b in blocks],
        "walkers": [],
    }
    if checkpoint_dirs:
        doc["walkers"] = [_walker_doc(name, checkpoint_dirs[name]) for name in sorted(checkpoint_dirs)]
    return doc


# --- CLI -------------------------------------------------------------------


def _parse_checkpoint_args(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--checkpoint expects name=path, got {item!r}")
        name, path = item.split("=", 1)
        out[name] = path
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mal_catalog", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="build data/catalog.json from the ledger")
    p_build.add_argument("--ledger", default="docs/HOLDOUT_LEDGER.md")
    p_build.add_argument("--out", default="data/catalog.json")
    p_build.add_argument("--checkpoint", action="append", default=[], metavar="NAME=PATH", help="repeatable; a walker checkpoint dir or file")

    p_check = sub.add_parser("check", help="ask the read guard ALLOW/DENY for one range")
    p_check.add_argument("--role", required=True, choices=list(ROLES))
    p_check.add_argument("--host", required=True)
    p_check.add_argument("--start", required=True, help="YYYY-MM-DDTHH, inclusive")
    p_check.add_argument("--end", required=True, help="YYYY-MM-DDTHH, exclusive")
    p_check.add_argument("--exp", default=None, help="EXP-### id, for role=confirmation-oneshot")
    p_check.add_argument("--ledger", default="docs/HOLDOUT_LEDGER.md")

    args = ap.parse_args(argv)

    if args.cmd == "build":
        checkpoint_dirs = _parse_checkpoint_args(args.checkpoint)
        catalog = build_catalog(args.ledger, checkpoint_dirs or None)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(catalog, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        print(f"wrote {out_path} ({len(catalog['blocks'])} blocks, {len(catalog['walkers'])} walkers)")
        return 0

    if args.cmd == "check":
        blocks = parse_ledger(Path(args.ledger).read_text(encoding="utf-8"))
        ok, reasons = check_read(blocks, args.role, args.host, args.start, args.end, exp_id=args.exp)
        print("ALLOW" if ok else "DENY")
        for reason in reasons:
            print(f"  - {reason}")
        return 0 if ok else 2

    return 2


if __name__ == "__main__":
    sys.exit(main())
