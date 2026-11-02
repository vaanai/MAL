"""r4bt_read_confirm: the CONFIRMATION read of the arena book r4-b-T (`r4bt-a1`; DEC-031 section 2 step 4 and Amendment 3 item 2, the r4-b-T amendment item 27; QP-RULING-1102-r4bt Q7/V4).
PAPER ONLY. READ-ONLY on the ledger. No key, no network. New file (r4bt_*): it edits no existing file.

The confirmation is the same `bot_id`, unchanged, on attempts decided AT OR AFTER the screen read's instant, read once at the outcome-blind instant: at least 100 attempts on at
least 5 further UTC dates (that is the boundary file's job: `python -m tools.r4bt_attempts count --from-ms <screen read instant>`), clearing the SAME gate on its deciding cell, AND the
one-sided bootstrap p of the book (10,000 draws, seed 1, `tools.arena_h5_read.boot_p`, the LARGER p of the two fail models = the max over the deciding sub-cells) at most 0.05/c.
c is the running count of confirmation tests by that read (failed and NOT_DECIDABLE ones included), a parameter of the manager (`--c`, no default): a later read faces a smaller 0.05/c.
Live fills must also match paper (DEC-031 section 2 step 4): NOT checked here; parity is required in addition and never replaces this.

This module adds NO scoring of its own. It calls `tools.r4bt_read.read` (imported; the book's screen read wrapper: the same refusals, the same hour table / excluded-hour class / 5%
cap, the same config check, the same scorer and drift guards, the same rent-charged leg and V audit) with `record=False`, on a FRESH ledger root priced by `tools/r4bt_price.py` from
the confirmation boundary file (the pricer selects entries decided in [from_ms, boundary)), and then applies the confirmation rule on top. Before that read it refuses (exit 2):
  1. no `--i-am-the-read`; no recorded SCREEN read with status PASS (`--screen-record`, pinned by `--screen-record-sha256`; bot_id r4bt-a1; a screen FAIL or NOT_DECIDABLE is final
     and has no confirmation); the confirmation record directory is the screen's directory (a second record of one bot_id in one directory is refused by the recorder anyway);
  2. a boundary file that is not a CONFIRMATION count (`mode` "confirmation") whose `from_ms` equals the screen read's `read_boundary_ms` (the screen read's instant);
  3. an existing confirmation record for the bot (one read).
The result: status PASS only if the gate status is PASS AND book p <= 0.05/c; FAIL if the gate fails, the p misses, or no p exists; NOT_DECIDABLE if the read wrapper reached no
deciding figure (A3 halt before the boundary, config-pin mismatch, an exclusions-tool cap). The record goes to `--confirm-reads-dir` (never the screen's) through
`arena_score.record_read`, with a `confirmation` block (from_ms, c, 0.05/c, book p, gate status, screen read sha256). A PASS here is the confirmation of ONE screen pass, best of N,
in-sample for selection, and walk-2-hour information (DEC-030 section 2 R1). No book has cleared the promotion gate.

    python -m tools.r4bt_read_confirm --screen-record S.json --screen-record-sha256 H --confirm-reads-dir D [--record] <the tools/r4bt_read.py flags: --i-am-the-read --ledger-root (the FRESH
        confirmation root) --registry --health --health-sha --restarts --as-of-ms --boundary-file (the confirmation count) --boundary-sha256 --pricer-report ... --n-books --c ...>
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import json
import sys
from pathlib import Path
from typing import Any, Sequence

from tools import arena_score as S
from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools import r4bt_read as R
from tools.arena_h5_read import P_DRAWS, P_SEED
from tools.arena_paper import load_registry


def load_screen(path: str, sha256: str, bot: str) -> dict[str, Any]:
    """The recorded screen read, pinned by sha256: a PASS of this bot_id with a read instant."""
    p = G.check_path(path, "the screen read record")
    if not G.SHA_RE.match(sha256 or ""):
        raise G.Refused("--screen-record-sha256 is not a lowercase sha256")
    if G.sha256_file(p) != sha256:
        raise G.Refused("the screen read record sha256 differs from the pinned one")
    try:
        rec = json.loads(p.read_bytes())
    except ValueError:
        raise G.Refused("the screen read record is not JSON") from None
    if rec.get("bot_id") != bot:
        raise G.Refused(f"the screen read record is not {bot}'s")
    if rec.get("status") != "PASS":
        raise G.Refused(f"the screen read is {rec.get('status')!r}: only a screen PASS enters confirmation (a FAIL or NOT_DECIDABLE is final for the bot_id)")
    b = rec.get("read_boundary_ms")
    if not (isinstance(b, int) and not isinstance(b, bool) and b % P.DAY_MS == 0):
        raise G.Refused("the screen read record has no 00:00Z read_boundary_ms")
    return rec


def check_confirmation_boundary(path: str, sha256: str, clock_ms: int, screen_instant_ms: int) -> dict[str, Any]:
    bnd = P.load_boundary(path, sha256, clock_ms, G.ARM)
    if bnd.get("mode") != "confirmation" or bnd.get("from_ms") != screen_instant_ms:
        raise G.Refused(f"the boundary file is not the confirmation count from the screen read's instant {screen_instant_ms} (mode {bnd.get('mode')!r}, from_ms {bnd.get('from_ms')!r})")
    return bnd


def decide(rb: dict[str, Any] | None, c: int, boundary_ms: int, bot: str = G.BOT_ID) -> dict[str, Any]:
    """Pure. rb = the read wrapper's result dict (None when it ended on an exclusions-tool cap). Returns the confirmation result."""
    alpha = 0.05 / c
    if rb is None or rb.get("status") not in ("PASS", "FAIL"):
        base = dict(rb or {"bot_id": bot, "reasons": ["exclusions_verdict_cap"], "read_boundary_ms": boundary_ms, "boundary_source": "natural", "attempts": None})
        return dict(base, status="NOT_DECIDABLE", confirmation={"gate_status": (rb or {}).get("status"), "book_p": None, "alpha_over_c": alpha, "c": c, "p_draws": P_DRAWS, "p_seed": P_SEED})
    p = (((rb.get("r4bt") or {}).get("multiplicity")) or {}).get("book_p")
    reasons = list(rb.get("reasons") or [])
    if rb["status"] != "PASS":
        status = "FAIL"
    elif p is None:
        status, reasons = "FAIL", reasons + ["no bootstrap p (no deciding sub-cell)"]
    elif p > alpha:
        status, reasons = "FAIL", reasons + [f"book p {p} > 0.05/c = {alpha} (c {c})"]
    else:
        status = "PASS"
    return dict(rb, status=status, reasons=reasons,
                confirmation={"gate_status": rb["status"], "book_p": p, "alpha_over_c": alpha, "c": c, "p_draws": P_DRAWS, "p_seed": P_SEED})


def confirm(*, screen_record: str, screen_record_sha256: str, confirm_reads_dir: str, record: bool = False, **kw: Any) -> tuple[list[str], dict[str, Any]]:
    """kw: the keyword arguments of `tools.r4bt_read.read` (without record / reads_dir)."""
    bot = kw.get("bot", G.BOT_ID)
    if not kw.get("i_am_the_read"):
        raise G.Refused("--i-am-the-read is required: this is the book's one confirmation read")
    c = kw.get("c")
    if not (isinstance(c, int) and not isinstance(c, bool) and c >= 1):
        raise G.Refused("--c is the manager's running count of confirmation tests (integer >= 1, no default)")
    scr = load_screen(screen_record, screen_record_sha256, bot)
    if Path(confirm_reads_dir).resolve() == Path(screen_record).resolve().parent:
        raise G.Refused("the confirmation record directory is the screen read's directory: use a separate --confirm-reads-dir")
    if (Path(confirm_reads_dir) / f"{bot}.json").exists():
        raise G.Refused(f"{bot}: its one confirmation read is already recorded")
    clock = int(load_registry(kw["registry"])[bot]["clock_start_ms"])
    bnd = check_confirmation_boundary(kw["boundary_file"], kw["boundary_sha256"], clock, scr["read_boundary_ms"])
    lines, rb = R.read(record=False, **kw)
    res = decide(rb, c, int(bnd["boundary_ms"]), bot)
    cf = res["confirmation"]
    lines = list(lines) + [
        f"CONFIRMATION (DEC-031 section 2 step 4): same bot_id, attempts decided at or after the screen read's instant {scr['read_boundary_ms']} (from_ms {bnd['from_ms']}); "
        f"the gate on the deciding cell: {cf['gate_status']}; book p {cf['book_p']} (larger p of the two fail models, {P_DRAWS} draws, seed {P_SEED}) vs 0.05/c = {cf['alpha_over_c']} "
        f"(c {cf['c']}, the running count: a later read faces a smaller 0.05/c); STATUS {res['status']}",
        "Live fills must also match paper (not checked here). A PASS confirms ONE screen pass: best of N, in-sample for selection, walk-2-hour information. No book has cleared the promotion gate."]
    if record:
        res["confirmation"]["screen_record_sha256"] = screen_record_sha256
        lines.append("recorded: " + str(S.record_read(confirm_reads_dir, res)))
    return lines, res


def main(argv: Sequence[str] | None = None, *, now_ms: int | None = None, ancestor: Any = None) -> int:
    ap = R.build_parser()
    ap.add_argument("--screen-record", required=True)
    ap.add_argument("--screen-record-sha256", required=True)
    ap.add_argument("--confirm-reads-dir", required=True)
    a = ap.parse_args(argv)
    try:
        if None in (a.feed_delay_slots, a.k_rounding, a.n_books, a.c):
            raise G.Refused("--feed-delay-slots, --k-rounding, --n-books and --c are required (no defaults)")
        lines, res = confirm(
            screen_record=a.screen_record, screen_record_sha256=a.screen_record_sha256, confirm_reads_dir=a.confirm_reads_dir, record=a.record,
            ledger_root=a.ledger_root, registry=a.registry, health_texts=[Path(h).read_text() for h in a.health], health_shas=a.health_sha, restarts_text=Path(a.restarts).read_text(),
            as_of_ms=a.as_of_ms, boundary_file=a.boundary_file, boundary_sha256=a.boundary_sha256, exp027_record=a.exp027_record, exp027_record_sha256=a.exp027_record_sha256,
            pins={"tools/arena_paper.py": a.arena_paper_sha256, "tools/arena_score.py": a.arena_score_sha256}, feed_delay_slots=a.feed_delay_slots, k_rounding=a.k_rounding,
            n_books=a.n_books, c=a.c, i_am_the_read=a.i_am_the_read, pricer_report=a.pricer_report, pricer_report_sha256=a.pricer_report_sha256, v_audit_ids=a.v_audit_ids,
            v_audit_ids_sha256=a.v_audit_ids_sha256, now_ms=now_ms, ancestor=ancestor)
    except Exception as e:  # noqa: BLE001 - every failure is a refusal (exit 2), never a traceback
        print(f"r4bt_read_confirm: REFUSED: {e}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
