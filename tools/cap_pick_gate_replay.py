"""Live EXP-012 gate replay on exploration pools (CAP-PICK judge item 1). Offline, zero credits.

Question: does the *online* gate (`tools/forward_exp012_gate.py`, the code the paper runner trades on)
reproduce the pick set the CAP-PICK audit book was scored on? The audit used OFFLINE scores
(`cache_table.csv` for P2-P4, `ARTIFACTS/exp012/oof_scores.json` for P1). This tool feeds exploration
tape through `Exp012Online` the way `ForwardEngine` does and records what the gate decides per mint.

EXPLORATION POOLS ONLY. Nothing here is gate evidence. `refuse_path` rejects fresh-0802/0808/0828, the
EXP-009 hours [2026-09-15T12, 2026-09-18T23), anything at or after 2026-10-02T10Z, and any
forward-walk / forward-paper / runner output (gate jsonl, arm-audit, heartbeat, positions).

How it mirrors the runner (reuse, not copy, wherever the runner exposes a function):
- rows: `flow_from_tape_row` (acceptance rule, wsol check), `_event_ts`, `TxOrder.stamp` (file order
  where `tx_index` is null), `_dedupe_key`, as `ForwardEngine.push_print` / `_add_print` do.
- order: heap key (t_recv_ms, creates first, then (mint_order, slot, tx_index, event_index, trader,
  side)) as `_apply_batch` sorts it; triggers fire after the whole time step (`_at_time`).
- migration: first `pumpswap` print with `t_recv_ms >= create.t_signal_ms` (`_consider_triggers`).
- decision: `Exp012Online.features_at` then `ForwardEngine._exp012_pass` (score, threshold,
  `gate_row`) on a real `ForwardEngine` holding the frozen gated book (`load_gate`, md5 checked).
- restart: `--daily-restart` (default) drops all state at 00:00Z and rebuilds creator history with
  `Exp012Online.preload` from the creates files, as `serve()` does at boot. A mint created before the
  restart is never gated (module note 7).
Places where this is a guess about the live wiring are listed in `GUESSES` and in the PR body.

Subcommands: `replay` (needs lightgbm: /data/mal/venv), `compare` (numpy; pyarrow only for --g-rows).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from tools.tape_lines import BadLinesError, scan_file, strict_lines

REPO = Path(__file__).resolve().parent.parent
MODEL = REPO / "ARTIFACTS" / "exp012" / "model.txt"
FEATURES = REPO / "ARTIFACTS" / "exp012" / "features.json"
FROZEN_MD5 = REPO / "ARTIFACTS" / "exp012" / "FROZEN.md5"
THRESHOLD = 0.8030766588450794
DROP_AFTER_CREATE_MS = 60 * 60 * 1000  # == forward_exp012_gate.DROP_AFTER_CREATE_MS (checked in the tests)
BOOK_ID = "cap_pick_replay"
SCHEMA = "cap_pick_gate_replay_v1"

GUESSES = (
    "t_recv_ms is null on every exploration row (getBlock backfill): imputed as block_time*1000 (zero receive lag). "
    "Live t_recv is later, so live decisions run seconds later and time_fallbacks stay at zero here.",
    "Creates enter as the live observe path does: note_create(chain=False, create_ms=t_signal) with the "
    "signature, fixed by the first matching bonding print (--create-time row uses the create row's chain time).",
    "t_signal_ms of a create = block_time*1000; first price = (quote_reserve/1e9)/(base_reserve/1e6) = v_sol/v_token_ui.",
    "Time steps are whole seconds (live steps are ms). The runner prunes when prints % 5000 == 0 at a step "
    "end; at second steps that rarely lands exactly, so prune runs when the print count crosses a multiple of "
    "--prune-every (default 5000); Exp012Online.prune still rate-limits itself to once per 60 s.",
    "Dedupe keys are stored as hashes and dropped 60 min after create (or at the decision); later prints of the mint "
    "are counted for the prune cadence but not deduped. TxOrder is pruned per hour with the runner's TX_ORDER_PRUNE_MS.",
    "Rows of mints created before the block start, or whose create is missing, get no decision (as live).",
    "Boot history: creates files only (fast-format creates-{hour}); no observe-{day} files exist for these pools.",
)

# ---- refusals -----------------------------------------------------------------------------------------
FORBIDDEN_NAMES = ("fresh-0802", "fresh-0808", "fresh-0828", "oracle-live", "forward-paper", "forward-walk", "forward_walk",
                   "exp012-gate", "arm-audit", "arm_audit", "runner-status", "heartbeat", "positions", "/var/lib/mal/paper")
VOID_LO = "2026-09-15T12"  # EXP-009 hours [2026-09-15T12, 2026-09-18T23)
VOID_HI = "2026-09-18T23"
CUTOFF = "2026-10-02T10"  # nothing at or after this hour
_HOUR_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2})")


class Refused(Exception):
    pass


class BadLineRefused(Refused):
    """A tape file held a NUL, a truncated or a non-JSON line (audit A8). Names the file, the first bad physical
    line (1-based, blank lines counted) and its kind: `nul`, `not_json` or `non_object`."""

    def __init__(self, label: str, line: int | None, kind: str | None, counts: dict[str, Any]) -> None:
        super().__init__(
            f"{label}: refused (bad tape line: kind {kind} at physical line {line}; {counts.get('bad_lines')} bad of "
            f"{counts.get('lines')} lines: nul={counts.get('nul')}, not_json={counts.get('not_json')}, "
            f"non_object={counts.get('non_object')}; a data hole, not a row)")
        self.label, self.line, self.kind, self.counts = label, line, kind, counts

    def __reduce__(self) -> tuple[Any, ...]:
        return (BadLineRefused, (self.label, self.line, self.kind, self.counts))


def _bad_lines_refusal(read_path: str | Path, label: str | Path, exc: BadLinesError) -> Refused:
    """Name the first bad line and its kind. `strict_lines` only carries counts, so this re-scans the file once,
    on the failure path only (`scan_file` keeps the first bad line's kind)."""
    try:
        c = scan_file(read_path)
    except (RuntimeError, OSError):
        c = None
    if c is not None and c.bad:
        return BadLineRefused(str(label), c.first_bad_line, c.first_bad_kind, {**c.as_dict(), "first_bad_kind": c.first_bad_kind})
    return BadLineRefused(str(label), exc.counts.get("first_bad_line"), None, exc.counts)


def _strict_text_lines(read_path: Path, label: str | Path | None = None) -> Iterator[str]:
    """Every line of `read_path` (plain, .gz or .zst), unchanged. After the last line, `Refused` if the file held a
    NUL, a truncated final line or any line that is not a JSON object; also `Refused` for a truncated or corrupt
    zstd stream or bytes that are not UTF-8. Strict is the only mode: there is no switch that skips bad lines."""
    from tools.paper_price_path import open_text

    name = str(label if label is not None else read_path)
    try:
        with open_text(read_path) as fh:
            yield from strict_lines(fh, name)
    except BadLinesError as exc:
        raise _bad_lines_refusal(read_path, name, exc) from None
    except UnicodeDecodeError as exc:
        raise Refused(f"{name}: refused (bad tape line: kind not_utf8; {exc.reason} at byte {exc.start})") from None
    except RuntimeError as exc:
        raise Refused(f"{name}: refused (truncated or corrupt compressed stream: {exc})") from None


def _json_rows(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:  # only a `lenient` line gets here (strict_lines already counted every bad one)
            continue
        if isinstance(row, dict):
            yield row


def _require_clean(copy: Path, label: str | Path) -> None:
    """Refuse unless the staged copy holds only rows. `Exp012Online.preload` swallows every error, so an unscanned
    bad creates file would silently shrink the creator history."""
    try:
        c = scan_file(copy)
    except RuntimeError as exc:
        raise Refused(f"{label}: refused (truncated or corrupt compressed stream: {exc})") from None
    if c.bad:
        raise BadLineRefused(str(label), c.first_bad_line, c.first_bad_kind, {**c.as_dict(), "first_bad_kind": c.first_bad_kind})


def refuse_name(path: str | Path) -> None:
    s = str(path)
    for bad in FORBIDDEN_NAMES:
        if bad in s:
            raise Refused(f"{s}: refused ({bad!r} is outside the exploration pools)")


def refuse_hour(hour: str, what: str = "") -> None:
    if VOID_LO <= hour < VOID_HI:
        raise Refused(f"{what or hour}: refused (EXP-009 hour {hour} in [{VOID_LO}, {VOID_HI}))")
    if hour >= CUTOFF:
        raise Refused(f"{what or hour}: refused (hour {hour} is at or after {CUTOFF}Z)")


def refuse_path(path: str | Path, allowed_roots: Sequence[str | Path] | None = None) -> None:
    """Raise Refused unless `path` is an hour file under an allowed root with an allowed hour."""
    p = Path(path)
    refuse_name(p)
    m = _HOUR_RE.search(p.name)
    if m is None:
        raise Refused(f"{p}: refused (not an hour file)")
    refuse_hour(m.group(1), str(p))
    if allowed_roots is not None:
        rp = os.path.realpath(p)
        if not any(rp.startswith(os.path.realpath(str(r)) + os.sep) for r in allowed_roots):
            raise Refused(f"{p}: refused (outside the allowed roots)")


# ---- blocks -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Block:
    name: str
    roots: tuple[str, ...]
    suffix: str
    null_tx_index: bool = False


def _roots(base: str, subs: Sequence[str]) -> tuple[str, ...]:
    return tuple(f"{base}/{s}" for s in subs)


BLOCKS: dict[str, Block] = {
    "explore-0814": Block("explore-0814", _roots("/data/mal/clean-view/explore-0814", [f"w{i}" for i in range(1, 8)]), ".jsonl.zst"),
    "fresh-0903": Block("fresh-0903", _roots("/data/mal/blocks-clean/fresh-0903", ["w1", "w2", "w3"]), ".deduped.jsonl.zst"),
    "exp011-0909": Block("exp011-0909", _roots("/data/mal/clean-view/exp011-0909", ["b", "c"]), ".jsonl.zst"),
    "fast-pool-0918": Block("fast-pool-0918", ("/data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00",), ".jsonl.zst"),
    "oracle-insample-0922": Block("oracle-insample-0922", ("/data/mal/clean-view/oracle-insample-2026-09-22_25",), ".jsonl.zst", True),
}


def hour_files(block: Block, roots: Sequence[str] | None = None) -> dict[str, dict[str, Path]]:
    """hour -> {"creates": path, "trades": path}, from directory listings only (no file is opened)."""
    out: dict[str, dict[str, Path]] = {}
    for root in roots if roots is not None else block.roots:
        for kind in ("creates", "trades"):
            d = Path(root) / kind
            if not d.is_dir():
                continue
            for name in sorted(os.listdir(d)):
                if not name.startswith(kind + "-") or not name.endswith(block.suffix):
                    continue
                m = _HOUR_RE.search(name)
                if m is None:
                    continue
                try:
                    refuse_hour(m.group(1))
                    refuse_name(d / name)
                except Refused:
                    continue
                out.setdefault(m.group(1), {})[kind] = d / name
    return out


def _calendar_ms(day: str, hour: int = 0) -> int:
    import calendar

    return calendar.timegm(time.strptime(f"{day}T{hour:02d}", "%Y-%m-%dT%H")) * 1000


def _day_of_ms(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def _hour_of_ms(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(ms / 1000.0))


def stage_creates(files: dict[str, dict[str, Path]], boot_ms: int, dest: Path, roots: Sequence[str | Path] | None) -> int:
    """Copy `creates-{hour}.jsonl[.zst]` for the history window before `boot_ms` into `dest`, so the runner's own
    `Exp012Online.preload` can read them under its fast-format file names. Copies, not symlinks: `zstd -dc` refuses
    a symlink (exit 1) and preload swallows the error, which would leave the creator history silently empty."""
    from tools.forward_exp012_gate import HIST_KEEP_MS

    n = 0
    lo = boot_ms - HIST_KEEP_MS - 3_600_000
    for t in range(lo - lo % 3_600_000, boot_ms, 3_600_000):
        hour = _hour_of_ms(t)
        src = files.get(hour, {}).get("creates")
        if src is None:
            continue
        refuse_path(src, roots)
        link = dest / (f"creates-{hour}.jsonl.zst" if src.name.endswith(".zst") else f"creates-{hour}.jsonl")
        if not link.exists():
            shutil.copyfile(src, link)
            _require_clean(link, src)  # the copy is what preload reads, so the copy is what is scanned
        n += 1
    return n


# ---- rows ---------------------------------------------------------------------------------------------
_MINT_KEY = '"mint":"'


def quick_mint(line: str) -> str | None:
    """The row's mint, by string search (no json parse). `"mint":"` does not match `quote_mint` or `mint_source`."""
    i = line.find(_MINT_KEY)
    off = 8
    if i < 0:  # tolerate `"mint": "` (non-compact json); the real files are compact
        i = line.find('"mint": "')
        off = 9
        if i < 0:
            return None
    j = line.find('"', i + off)
    return line[i + off:j] if j > 0 else None


def create_signal_from_row(row: dict[str, Any], *, imputed: list[int] | None = None):
    """A create row of the fast/backfill format as a CreateSignal. t_signal = t_recv_ms if the row has one,
    else block_time*1000 (zero lag)."""
    from tools.paper_price_path import CreateSignal

    if row.get("type") != "create":
        return None
    mint = row.get("mint")
    if not isinstance(mint, str) or not mint or mint == "UNK":
        return None
    block = row.get("block_time", row.get("event_ts"))
    if isinstance(block, bool) or not isinstance(block, int) or block < 1_000_000_000:
        return None
    t_recv = row.get("t_recv_ms")
    if isinstance(t_recv, int) and not isinstance(t_recv, bool):
        t_ms = t_recv
    else:
        t_ms = block * 1000
        if imputed is not None:
            imputed[0] += 1
    creator = row.get("creator") or row.get("trader")
    if not isinstance(creator, str) or creator == "UNK":
        creator = None
    sig = row.get("signature")
    if not isinstance(sig, str) or sig == "UNK":
        sig = None
    q, b = row.get("quote_reserve"), row.get("base_reserve")
    vs = q / 1e9 if isinstance(q, (int, float)) and not isinstance(q, bool) and q > 0 else None
    vt = b / 1e6 if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0 else None
    return CreateSignal(mint=mint, t_signal_ms=t_ms, creator=creator, signature=sig, v_sol=vs, v_token_ui=vt,
                        mcap_sol=None, initial_buy_ui=None, sol_amount=None)


def frozen_model_md5() -> str:
    for line in FROZEN_MD5.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "model.txt":
            return parts[0]
    raise SystemExit(f"{FROZEN_MD5}: no model.txt line")


def build_engine(model: Path = MODEL, md5: str | None = None, features: Path = FEATURES, threshold: float = THRESHOLD, kill_dir: Path | None = None):
    """A real ForwardEngine holding one frozen gated migrate book. It is used for `_exp012_pass`
    (score, threshold, gate_row), not for fills; nothing is ever pushed into its inbox."""
    from tools.forward_paper import BookSpec, ForwardEngine

    spec = BookSpec(BOOK_ID, "migrate", "tp50_sl30", creator_cooldown_ms=0, token_cooldown_ms=0, entry_model=str(model),
                    entry_model_md5=md5 or frozen_model_md5(), entry_threshold=threshold, entry_features=str(features))
    kill = (kill_dir or Path(tempfile.gettempdir())) / "KILL_cap_pick_replay_unused"
    return ForwardEngine([spec], kill_file=kill, retain_rows=True)


class _M:
    __slots__ = ("create", "order", "seen", "migrate_done", "chain_ms")

    def __init__(self, create: Any, order: int, chain_ms: int | None) -> None:
        self.create = create
        self.order = order
        self.seen: set | None = set()
        self.migrate_done = False
        self.chain_ms = chain_ms


class Replayer:
    """One engine process. `boot()` is the restart; `feed_hour()` is the tape."""

    def __init__(self, engine: Any, view: str, *, prune_every: int = 5000, create_time: str = "sig", null_tx_index: bool = False) -> None:
        if create_time not in ("sig", "row"):
            raise ValueError("create_time must be sig or row")
        self.engine = engine
        self.view = view
        self.prune_every = prune_every
        self.create_time = create_time
        self.null_tx_index = null_tx_index
        self.records: list[dict[str, Any]] = []
        self.decided: set[str] = set()
        self.imputed = [0]  # create rows whose t_recv_ms was imputed
        self.imputed_prints = 0
        self.rows_parsed = 0
        self.lib: dict[str, _M] = {}
        self._expiry: deque[tuple[int, str]] = deque()
        self.dead: dict[str, int] = {}
        self.dead_hits: dict[str, int] = {}
        self._order = 0
        self.prints = 0
        self._bucket = 0
        self.boots: list[dict[str, Any]] = []

    # --- restart ---------------------------------------------------------------------------------------
    def boot(self, boot_ms: int, creates_dir: Path | None) -> int:
        """A fresh process: empty engine state, creator history preloaded from disk (rows before boot)."""
        from tools.forward_exp012_gate import Exp012Online
        from tools.paper_price_path import TxOrder

        ex = Exp012Online()
        n = ex.preload(creates_dir, boot_ms) if creates_dir is not None else 0
        self.engine.exp012 = ex
        self.lib = {}
        self._expiry = deque()
        self.tx = TxOrder()
        self.prints = 0
        self._bucket = 0
        self.dead = {m: t for m, (_c, t) in ex.hist_mints.items() if m not in self.decided}
        self.boots.append({"boot_ms": boot_ms, "history_rows": n, "dead_mints": len(self.dead), **ex.preload_stats})
        return n

    # --- one hour --------------------------------------------------------------------------------------
    def feed_hour(self, create_rows: Iterable[dict[str, Any]], trade_lines: Iterable[str]) -> None:
        from tools.forward_exp012_gate import create_chain_ms
        from tools.forward_paper import TX_ORDER_PRUNE_MS, _event_ts, flow_from_tape_row

        events: list[tuple[tuple, Any]] = []
        new: dict[str, _M] = {}
        seq = 0
        for row in create_rows:
            c = create_signal_from_row(row, imputed=self.imputed)
            if c is None or c.mint in self.lib or c.mint in new:
                continue
            m = _M(c, self._order, create_chain_ms(row))
            self._order += 1
            new[c.mint] = m
            events.append(((c.t_signal_ms, -1, seq), ("create", m)))
            seq += 1
        for line in trade_lines:
            mint = quick_mint(line)
            if mint is None:
                continue
            known = self.lib.get(mint) or new.get(mint)
            if known is None:
                if mint not in self.dead or '"pumpswap"' not in line:
                    continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if not isinstance(row.get("t_recv_ms"), int) or isinstance(row.get("t_recv_ms"), bool):
                block = row.get("block_time", row.get("event_ts"))
                if isinstance(block, bool) or not isinstance(block, int):
                    continue
                row["t_recv_ms"] = block * 1000
                self.imputed_prints += 1
            parsed = flow_from_tape_row(row)
            self.rows_parsed += 1
            if parsed is None:
                continue
            mint, pr = parsed
            if known is None:  # a createless-in-this-process mint: only its first pumpswap print is noted
                if pr.venue == "pumpswap" and mint in self.dead:
                    ets = _event_ts(row)
                    self.dead_hits[mint] = (ets * 1000) if ets is not None else pr.t_recv_ms
                    del self.dead[mint]
                continue
            pr = self.tx.stamp(pr)
            events.append(((pr.t_recv_ms, 0, known.order, pr.slot, pr.tx_index, pr.event_index, pr.trader or "", pr.side, seq),
                           ("print", mint, pr, _event_ts(row))))
            seq += 1
        events.sort(key=lambda e: e[0])
        for m in new.values():
            self.lib[m.create.mint] = m
        queued: list[tuple[str, int]] = []
        cur: int | None = None
        for key, ev in events:
            t = key[0]
            if cur is not None and t != cur:
                self._end_step(cur, queued)
                queued = []
            cur = t
            if ev[0] == "create":
                self._register(ev[1])
            else:
                _tag, mint, pr, ets = ev
                self._add_print(mint, pr, ets, queued)
        if cur is not None:
            self._end_step(cur, queued)
            # the runner prunes TxOrder the same way (serve passes tx_order_prune_ms=TX_ORDER_PRUNE_MS)
            self.tx.prune_before_ms(cur - TX_ORDER_PRUNE_MS)

    def _register(self, m: _M) -> None:
        c = m.create
        ex = self.engine.exp012
        self._expiry.append((c.t_signal_ms, c.mint))
        first = c.v_sol / c.v_token_ui if c.v_sol and c.v_token_ui and c.v_sol > 0 and c.v_token_ui > 0 else None
        if self.create_time == "row" and m.chain_ms is not None:
            ex.note_create(c.mint, c.creator, m.chain_ms, first, chain=True, signature=c.signature)
        else:
            ex.note_create(c.mint, c.creator, c.t_signal_ms, first, chain=False, signature=c.signature)

    def _add_print(self, mint: str, pr: Any, event_ts: int | None, queued: list[tuple[str, int]]) -> None:
        m = self.lib[mint]
        if m.seen is not None:
            from tools.forward_paper import _dedupe_key

            key = hash(_dedupe_key(pr))  # the hash, not the tuple: bounds memory; a collision is ~n^2/2^64
            if key in m.seen:
                return
            m.seen.add(key)
        self.prints += 1
        ex = self.engine.exp012
        ex.note_print(mint, venue=pr.venue, t_recv_ms=pr.t_recv_ms, event_ts=event_ts, side=pr.side, trader=pr.trader,
                      sol_lamports=pr.sol_lamports, token_raw=pr.token_raw, price_sol=pr.price_sol, signature=pr.signature)
        if not m.migrate_done and pr.venue == "pumpswap" and pr.t_recv_ms >= m.create.t_signal_ms:
            m.migrate_done = True
            ex.mark_migrated(mint, event_ts, pr.t_recv_ms)
            queued.append((mint, pr.t_recv_ms))

    def _end_step(self, t_ms: int, queued: list[tuple[str, int]]) -> None:
        for mint, when in queued:
            self._on_signal(mint, when)
        if self.prune_every and self.prints // self.prune_every != self._bucket:
            self._bucket = self.prints // self.prune_every
            self.engine.exp012.prune(t_ms)
        # the gate drops a mint's accumulator 60 min after create; dedupe state past that cannot change a decision
        while self._expiry and self._expiry[0][0] + DROP_AFTER_CREATE_MS < t_ms:
            self.lib[self._expiry.popleft()[1]].seen = None

    def _on_signal(self, mint: str, t_ms: int) -> None:
        from tools.laya_v0 import MintBook

        eng = self.engine
        ex = eng.exp012
        m = self.lib[mint]
        a = ex.acc.get(mint)
        create_ms = a.feat.create_ms if a is not None else m.create.t_signal_ms
        gate_error: str | None = None
        try:
            feats, reason = ex.features_at(mint, t_ms)
        except Exception as exc:  # noqa: BLE001 - as the runner
            feats, reason, gate_error = None, "gate_error", type(exc).__name__
        else:
            if reason == "gate_error":
                gate_error = ex.errors.get(mint)
        n_rows = len(eng.exp012_rows)
        eng._exp012_pass(eng.books[0], MintBook(create=m.create, flow=[]), t_ms, "migrate", feats, reason, gate_error)
        row = eng.exp012_rows[n_rows]
        del eng.exp012_rows[n_rows:]
        eng.decisions.clear()
        ex.drop(mint)
        m.seen = None
        self.decided.add(mint)
        mig_ms = row["mig_ms"]
        if row["entered"]:
            label = "pick"
        elif row["reason"] == "below_threshold":
            label = "below"
        else:
            label = row["reason"] or "unknown"
        self.records.append({
            "schema": SCHEMA, "kind": "decision", "view": self.view, "mint": mint, "day": _day_of_ms(mig_ms), "mig_ms": mig_ms,
            "create_ms": create_ms, "ttm_s": (mig_ms - create_ms) / 1000.0, "score": row["score"], "decision": label,
            "entered": row["entered"], "error": row["error"], "time_fallbacks": row["time_fallbacks"],
            "tx_index_null": self.null_tx_index,
        })

    def dead_records(self) -> list[dict[str, Any]]:
        return [{"schema": SCHEMA, "kind": "dead", "view": self.view, "mint": m, "first_pumpswap_ms": t, "day": _day_of_ms(t),
                 "decision": "pre_restart", "tx_index_null": self.null_tx_index}
                for m, t in self.dead_hits.items() if m not in self.decided]


# ---- driver over files --------------------------------------------------------------------------------
def iter_json_rows(path: Path, roots: Sequence[str | Path] | None) -> Iterator[dict[str, Any]]:
    """The object rows of one hour file. Strict: a NUL, a truncated or a non-JSON line raises `Refused` after the
    last line (audit A8); it is never skipped."""
    refuse_path(path, roots)
    yield from _json_rows(_strict_text_lines(Path(path)))


def iter_lines(path: Path, roots: Sequence[str | Path] | None) -> Iterator[str]:
    """The raw lines of one hour file, with the same strict refusal as `iter_json_rows`."""
    refuse_path(path, roots)
    yield from _strict_text_lines(Path(path))


def days_between(a: str, b: str) -> list[str]:
    out, t = [], _calendar_ms(a)
    end = _calendar_ms(b)
    while t <= end:
        out.append(_day_of_ms(t))
        t += 86_400_000
    return out


def check_days(days: Sequence[str]) -> None:
    """A day is refused if none of its hours is allowed. Refused hours of a partly allowed day (09-15, 09-18, 10-02)
    are never listed, so never read."""
    for d in days:
        ok = 0
        for h in range(24):
            try:
                refuse_hour(f"{d}T{h:02d}")
                ok += 1
            except Refused:
                pass
        if not ok:
            refuse_hour(f"{d}T12", f"day {d}")


def replay_view(block: Block, days: Sequence[str], *, daily_restart: bool = True, prune_every: int = 5000, create_time: str = "sig",
                roots: Sequence[str] | None = None, engine: Any = None, log: Any = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replay `days` (UTC) of one block. With `daily_restart`, state is dropped at each 00:00Z and rebuilt by preload."""
    use_roots = list(roots if roots is not None else block.roots)
    check_days(days)
    files = hour_files(block, use_roots)
    rep = Replayer(engine or build_engine(), block.name, prune_every=prune_every, create_time=create_time, null_tx_index=block.null_tx_index)
    out: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="cap_pick_stage_") as td:
        for i, day in enumerate(days):
            if i == 0 or daily_restart:
                boot_ms = _calendar_ms(day)
                stage = Path(td) / f"boot-{day}"
                stage.mkdir()
                staged = stage_creates(files, boot_ms, stage, use_roots)
                n_hist = rep.boot(boot_ms, stage)
                rep.boots[-1]["staged_files"] = staged
                if staged and not n_hist and log is not None:
                    print(f"[{block.name}] WARNING boot {day}: {staged} creates files staged but 0 history rows read", file=log, flush=True)
            for h in range(24):
                hour = f"{day}T{h:02d}"
                f = files.get(hour)
                if not f or "trades" not in f:
                    continue
                t0 = time.monotonic()
                crows = iter_json_rows(f["creates"], use_roots) if "creates" in f else iter(())
                rep.feed_hour(crows, iter_lines(f["trades"], use_roots))
                if log is not None:
                    print(f"[{block.name}] {hour} decisions={len(rep.records)} lib={len(rep.lib)} prints={rep.prints} {time.monotonic() - t0:.1f}s", file=log, flush=True)
            if daily_restart:
                out.extend(rep.records)
                out.extend(rep.dead_records())
                rep.records = []
                rep.dead_hits = {}
        if not daily_restart:
            out.extend(rep.records)
            out.extend(rep.dead_records())
    meta = {"view": block.name, "days": list(days), "daily_restart": daily_restart, "prune_every": prune_every, "create_time": create_time,
            "boots": rep.boots, "create_rows_t_recv_imputed": rep.imputed[0], "trade_rows_t_recv_imputed": rep.imputed_prints,
            "tx_index_null_view": block.null_tx_index, "guesses": list(GUESSES)}
    return out, meta


# ---- comparison with the offline scores ----------------------------------------------------------------
BANDS = (("le32m", 0.0, 1920.0), ("32_60m", 1920.0, 3600.0), ("gt60m", 3600.0, float("inf")))
SOURCE_VIEW = {"P2": "explore-0814", "P3": "fresh-0903", "P4": "exp011-0909", "P1A": "fast-pool-0918", "P1C": "oracle-insample-0922"}
P2P4 = ("explore-0814", "fresh-0903", "exp011-0909")
P1_VIEWS = ("fast-pool-0918", "oracle-insample-0922")
CACHE_TABLE = "/data/mal/audit-1008/work/d08_model_selection/out/cache_table.csv"
OOF_SCORES = REPO / "ARTIFACTS" / "exp012" / "oof_scores.json"
G_ROWS = "/data/mal/audit-1008/work/g_reachable_cap_book_rescore/out/rows_P_primary.parquet"


def band_of(ttm_s: float | None) -> str:
    """le32m: ttm <= 1920 s; 32_60m: (1920, 3600]; gt60m: > 3600 s (the gate's no_features edge)."""
    if ttm_s is None:
        return "unknown"
    if ttm_s <= 1920.0:
        return "le32m"
    return "32_60m" if ttm_s <= 3600.0 else "gt60m"


def load_online(paths: Iterable[str | Path]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """(decisions by mint, dead records by mint, meta by view) from replay output files."""
    dec: dict[str, dict[str, Any]] = {}
    dead: dict[str, dict[str, Any]] = {}
    meta: dict[str, dict[str, Any]] = {}
    for p in paths:
        refuse_name(p)
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("schema") != SCHEMA:
                    raise Refused(f"{p}: not a {SCHEMA} file")
                k = r.get("kind")
                if k == "meta":
                    m = meta.setdefault(r["view"], {"days": []})
                    m["days"] = sorted(set(m["days"]) | set(r["days"]))
                elif k == "decision":
                    dec.setdefault(r["mint"], r)
                elif k == "dead":
                    dead.setdefault(r["mint"], r)
    return dec, dead, meta


def load_offline(cache_table: str | Path = CACHE_TABLE, oof_path: str | Path = OOF_SCORES) -> dict[str, dict[str, Any]]:
    """mint -> {view, date, score, ttm_s}. P2-P4: the d08 frozen-model score. P1 (fast-pool, oracle-insample): the
    leave-one-day-out OOF score, as the audit's strata.py does. Mints with no score are absent."""
    import csv

    for p in (cache_table, oof_path):
        refuse_name(p)
    oof = {r["mint"]: r["score"] for r in json.loads(Path(oof_path).read_text(encoding="utf-8"))["rows"]}
    out: dict[str, dict[str, Any]] = {}
    with open(cache_table, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            view = SOURCE_VIEW.get(r["source"])
            if view is None or r["mint"] in out:
                continue
            raw = oof.get(r["mint"]) if r["source"].startswith("P1") else (float(r["score"]) if r["score"] not in ("", None) else None)
            if raw is None:
                continue
            try:
                ttm = float(r["f0"])
            except (TypeError, ValueError):
                ttm = None
            out[r["mint"]] = {"view": view, "date": r["date"], "score": float(raw), "ttm_s": ttm}
    return out


def _pct(vals: Sequence[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return s[int(round(p * (len(s) - 1)))]


def compare(online: dict[str, dict[str, Any]], dead: dict[str, dict[str, Any]], offline: dict[str, dict[str, Any]],
            meta: dict[str, dict[str, Any]], threshold: float = THRESHOLD) -> dict[str, Any]:
    """Per view: n on each side, pick overlap, |score delta|, and decision labels by time-to-migrate band.
    Offline mints are restricted to the replayed days of the view."""
    views: dict[str, Any] = {}
    for view, m in meta.items():
        days = set(m["days"])
        on = {k: v for k, v in online.items() if v["view"] == view and v["day"] in days}
        off = {k: v for k, v in offline.items() if v["view"] == view and v["date"] in days}
        on_scored = {k: v for k, v in on.items() if v["score"] is not None}
        on_pick = {k for k, v in on.items() if v["decision"] == "pick"}
        off_pick = {k for k, v in off.items() if v["score"] >= threshold}
        both = on_pick & off_pick
        deltas = [abs(on_scored[k]["score"] - off[k]["score"]) for k in on_scored if k in off]
        labels: dict[str, dict[str, int]] = {}

        def bump(label: str, ttm: float | None) -> None:
            b = labels.setdefault(label, {})
            b[band_of(ttm)] = b.get(band_of(ttm), 0) + 1
            b["all"] = b.get("all", 0) + 1

        for k, v in on.items():
            bump("online:" + v["decision"], v["ttm_s"])
        for k, v in off.items():
            if k not in on:
                bump("offline_only_no_online:" + ("pre_restart" if k in dead else "no_record"), v["ttm_s"])
        by_band: dict[str, dict[str, int]] = {}
        for name in ("le32m", "32_60m", "gt60m"):
            ks = [k for k, v in off.items() if band_of(v["ttm_s"]) == name]
            by_band[name] = {"offline_scored": len(ks), "offline_pick": sum(1 for k in ks if k in off_pick),
                             "online_pick_of_those": sum(1 for k in ks if k in on_pick),
                             "both_pick": sum(1 for k in ks if k in both)}
        views[view] = {
            "days": sorted(days),
            "n_online_decisions": len(on), "n_online_scored": len(on_scored), "n_offline_scored": len(off),
            "online_scored_not_offline": sum(1 for k in on_scored if k not in off),
            "offline_not_online_scored": sum(1 for k in off if k not in on_scored),
            "picks": {"online": len(on_pick), "offline": len(off_pick), "both": len(both),
                      "online_only": len(on_pick - off_pick), "offline_only": len(off_pick - on_pick),
                      "online_only_absent_from_offline_table": sum(1 for k in on_pick - off_pick if k not in off),
                      "jaccard": (len(both) / len(on_pick | off_pick)) if (on_pick | off_pick) else None},
            "abs_score_delta": {"n": len(deltas), "max": max(deltas) if deltas else None, "p99": _pct(deltas, 0.99),
                                "p95": _pct(deltas, 0.95), "median": _pct(deltas, 0.5)},
            "labels_by_ttm_band": labels, "offline_picks_by_band": by_band,
            "tx_index_null": any(v.get("tx_index_null") for v in on.values()),
        }
    return {"threshold": threshold, "views": views}


# ---- book restatement (report only) --------------------------------------------------------------------
def legs(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Per-attempt P&L in lamports for the live / flat / pressure fail legs, as the audit's analyze.legs():
    live (1/62) and flat (0.15) fail on fills only; pressure p = sigmoid(c + 0.8 log1p(ssb) + 0.35 log1p(nearby SOL)),
    c bisected so the mean p over this selection's fills is 0.289. Guard-fail rows are already -fee."""
    import numpy as np

    fee = np.array([float(r["fee"]) for r in rows])
    pnl = np.array([float(r["pnl"]) for r in rows])
    fill = np.array([r["status"] == "fill" for r in rows], dtype=bool)
    out = {"nofail": pnl}
    for name, p in (("live", 1 / 62), ("flat", 0.15)):
        out[name] = np.where(fill, (1 - p) * pnl + p * (-fee), pnl)
    z0 = 0.8 * np.log1p(np.array([float(r["ssb"]) for r in rows])) + 0.35 * np.log1p(np.array([float(r["nearby"]) for r in rows]) / 1e9)
    lo, hi = -40.0, 40.0
    for _ in range(80):
        mid = (lo + hi) / 2
        m = (1 / (1 + np.exp(-(mid + z0[fill])))).mean() if fill.any() else 0
        if m > 0.289:
            hi = mid
        else:
            lo = mid
    pp = 1 / (1 + np.exp(-((lo + hi) / 2 + z0)))
    out["press"] = np.where(fill, (1 - pp) * pnl + pp * (-fee), pnl)
    return out


def leg_stats(x: Any, units: Sequence[str], size: float) -> dict[str, Any]:
    """Mean % of stake per attempt, date-cluster CI90 lower bound (1,000 resamples of day units, seed 1, 5th pct),
    days positive, total and ex-best-day SOL."""
    import numpy as np

    x = np.asarray(x, float) / 1e9
    n = len(x)
    if n == 0:
        return {"n": 0}
    ud, inv = np.unique(np.asarray(units), return_inverse=True)
    dsum = np.bincount(inv, weights=x)
    dn = np.bincount(inv)
    di = np.random.default_rng(1).integers(0, len(ud), size=(1000, len(ud)))
    lo = np.percentile(dsum[di].sum(1) / dn[di].sum(1), 5)
    sz = size / 1e9
    return {"n": n, "days": len(ud), "mean_pct": 100 * x.mean() / sz, "ci_date_lo_pct": 100 * lo / sz,
            "days_pos": f"{int((dsum > 0).sum())}/{len(ud)}", "total_sol": float(x.sum()), "ex_best_day_sol": float(x.sum() - dsum.max())}


def restate(rows: Sequence[dict[str, Any]], picks: set[str], groups: dict[str, Sequence[str]], days: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """Book on `picks` over G's attempt rows. The legs are built once on the whole pick book (all views in `groups`,
    replayed days only), so the pressure intercept is fitted on the pooled pick book's fills as the audit's
    pickbook.py does; each group is then a slice of that book."""
    every = {v for vs in groups.values() for v in vs}
    sel = [r for r in rows if r["blk"] in every and r["mint"] in picks and (days is None or r["day"] in days.get(r["blk"], set()))]
    out: dict[str, Any] = {g: {"n": 0} for g in groups}
    if not sel:
        return out
    sizes = {r["size"] for r in sel}
    if len(sizes) != 1:
        return {g: {"n": len(sel), "error": f"mixed stake sizes {sorted(sizes)}"} for g in groups}
    import numpy as np

    lg = legs(sel)
    units = np.array([r["day"] + "|" + r["blk"] for r in sel])
    blk = np.array([r["blk"] for r in sel])
    for g, vs in groups.items():
        m = np.isin(blk, list(vs))
        if m.any():
            out[g] = {leg: leg_stats(lg[leg][m], units[m], float(next(iter(sizes)))) for leg in ("live", "flat", "press")}
    return out


def load_g_rows(path: str | Path = G_ROWS) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    refuse_name(path)
    cols = ["day", "blk", "mint", "status", "pnl", "fee", "ssb", "nearby", "size"]
    t = pq.read_table(str(path), columns=cols).to_pydict()
    return [dict(zip(cols, vals)) for vals in zip(*(t[c] for c in cols))]


def restatement_report(online: dict[str, dict[str, Any]], offline: dict[str, dict[str, Any]], rows: Sequence[dict[str, Any]],
                       meta: dict[str, dict[str, Any]], threshold: float = THRESHOLD) -> dict[str, Any]:
    """Online pick set vs offline pick sets on the same G attempts. EXPLORATION ONLY, not gate evidence."""
    days = {v: set(m["days"]) for v, m in meta.items()}
    on_pick = {k for k, v in online.items() if v["decision"] == "pick" and v["day"] in days.get(v["view"], set())}
    off_all = {k for k, v in offline.items() if v["score"] >= threshold and v["date"] in days.get(v["view"], set())}
    off_le60 = {k for k in off_all if offline[k]["ttm_s"] is not None and offline[k]["ttm_s"] <= 3600.0}
    sets = {"online_pick": on_pick, "offline_all_picks": off_all, "offline_picks_le60min": off_le60}
    groups: dict[str, tuple[str, ...]] = {v: (v,) for v in meta}
    groups["P2-P4"] = tuple(v for v in P2P4 if v in meta)
    groups["P1"] = tuple(v for v in P1_VIEWS if v in meta)
    groups["ALL"] = tuple(meta)
    groups = {g: vs for g, vs in groups.items() if vs}
    out: dict[str, Any] = {"label": "exploration only; not gate evidence", "reference": {"offline_all_P2-P4_live": 3.819, "offline_le60_P2-P4_live": 3.492},
                           "pressure_intercept": "fitted on the pooled pick book of the replayed views (audit pickbook.py)"}
    g_mints = {r["mint"] for r in rows}
    out["coverage"] = {name: {"picks": len(s), "with_a_G_attempt_row": len(s & g_mints)} for name, s in sets.items()}
    per_set = {name: restate(rows, s, groups, days) for name, s in sets.items()}
    for g in groups:
        out[g] = {name: per_set[name][g] for name in sets}
    return out


def render(cmp: dict[str, Any], rest: dict[str, Any] | None) -> str:
    L = ["# CAP-PICK live-gate pick-set replay (exploration only, not gate evidence)", ""]
    for view, v in cmp["views"].items():
        p, d = v["picks"], v["abs_score_delta"]
        L += [f"## {view}  days {v['days'][0]}..{v['days'][-1]} ({len(v['days'])})" + ("  [tx_index null: file order]" if v["tx_index_null"] else ""),
              f"- migrating mints: online decisions {v['n_online_decisions']} (scored {v['n_online_scored']}), offline scored {v['n_offline_scored']}",
              f"- picks: both {p['both']}, online-only {p['online_only']} ({p['online_only_absent_from_offline_table']} of them absent from the offline table), offline-only {p['offline_only']} (jaccard {p['jaccard']})",
              f"- |score delta| on {d['n']} mints scored on both sides: max {d['max']}, p99 {d['p99']}, p95 {d['p95']}, median {d['median']}",
              "- decision labels by time-to-migrate band (le32m / 32_60m / gt60m / all):"]
        for lab, b in sorted(v["labels_by_ttm_band"].items()):
            L.append(f"  - {lab}: {b.get('le32m', 0)} / {b.get('32_60m', 0)} / {b.get('gt60m', 0)} / {b.get('all', 0)}")
        L.append("- offline picks by band: " + "; ".join(f"{k}: off {x['offline_pick']}, on {x['online_pick_of_those']}, both {x['both_pick']}" for k, x in v["offline_picks_by_band"].items()))
        L.append("")
    if rest:
        L += ["## Book restatement (mean % of stake per attempt [date-cluster CI90 lower] days positive, ex-best-day SOL)",
              f"_{rest['label']}_; reference offline P2-P4 live: +3.819 all picks, +3.492 <=60 min",
              "coverage (picks / picks with a G attempt row): " + "; ".join(f"{k} {v['picks']}/{v['with_a_G_attempt_row']}" for k, v in rest["coverage"].items()), ""]
        for g, sets in rest.items():
            if g in ("label", "reference", "pressure_intercept", "coverage"):
                continue
            for name, lg in sets.items():
                if not lg.get("live"):
                    L.append(f"- {g} / {name}: no attempts")
                    continue
                cell = lambda k: f"{lg[k]['mean_pct']:+.3f} [{lg[k]['ci_date_lo_pct']:+.2f}] {lg[k]['days_pos']} xbd {lg[k]['ex_best_day_sol']:+.2f}"
                L.append(f"- {g} / {name}: n={lg['live']['n']} live {cell('live')}; flat {cell('flat')}; press {cell('press')}")
    return "\n".join(L) + "\n"


def cmd_compare(args: argparse.Namespace) -> int:
    paths = [p for pat in args.online for p in sorted(glob.glob(pat))]
    if not paths:
        raise SystemExit("no online files matched")
    on, dead, meta = load_online(paths)
    off = load_offline(args.cache_table, args.oof)
    cmp = compare(on, dead, off, meta)
    rest = restatement_report(on, off, load_g_rows(args.g_rows), meta) if args.g_rows else None
    if args.out_json:
        refuse_name(args.out_json)
        Path(args.out_json).write_text(json.dumps({"compare": cmp, "restatement": rest, "guesses": list(GUESSES)}, indent=1) + "\n", encoding="utf-8")
    sys.stdout.write(render(cmp, rest))
    return 0


# ---- CLI -----------------------------------------------------------------------------------------------
def cmd_replay(args: argparse.Namespace) -> int:
    block = BLOCKS[args.view]
    days = days_between(args.from_day, args.to_day or args.from_day)
    t0 = time.monotonic()
    recs, meta = replay_view(block, days, daily_restart=args.daily_restart, prune_every=args.prune_every,
                             create_time=args.create_time, log=sys.stderr)
    meta["wall_s"] = round(time.monotonic() - t0, 1)
    import resource

    meta["maxrss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    out = Path(args.out)
    refuse_name(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"schema": SCHEMA, "kind": "meta", **meta}) + "\n")
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    print(json.dumps({k: meta[k] for k in ("view", "days", "wall_s", "maxrss_mb")} | {"rows": len(recs)}), file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay", help="run the live gate over one view, UTC days from..to")
    r.add_argument("--view", required=True, choices=sorted(BLOCKS))
    r.add_argument("--from-day", required=True)
    r.add_argument("--to-day")
    r.add_argument("--out", required=True)
    r.add_argument("--daily-restart", action=argparse.BooleanOptionalAction, default=True)
    r.add_argument("--prune-every", type=int, default=5000)
    r.add_argument("--create-time", choices=("sig", "row"), default="sig")
    r.set_defaults(fn=cmd_replay)
    c = sub.add_parser("compare", help="join replay output with the offline scores; optional book restatement")
    c.add_argument("--online", nargs="+", required=True, help="replay output files or globs")
    c.add_argument("--cache-table", default=CACHE_TABLE)
    c.add_argument("--oof", default=str(OOF_SCORES))
    c.add_argument("--g-rows", help="rows_P_primary.parquet (needs pyarrow: /data/mal/audit-1008/venv)")
    c.add_argument("--out-json")
    c.set_defaults(fn=cmd_compare)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3  # the walker's fatal code; argparse keeps 2 for usage errors


if __name__ == "__main__":
    raise SystemExit(main())
