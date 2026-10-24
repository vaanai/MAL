"""DEC-032 Amendment 2 (stake ladder C0 -> C1 for h5v1q50-boostfloor-a1), item 9: the pure, keyless parts the executor uses. No RPC, no key, no send.

  LADDER            item 3's C0 and C1 columns as code constants (C2 is successor-only, item 12: it is NOT compiled here, so no ceiling can name it)
  ladder_guard_problem   a live start refuses if any constant is looser than item 3's verbatim table (AM2_TABLE)
  read_ceiling      /etc/mal-h5-arena/TIER (root:root 0644): "T1" = ceiling C0 (the fail-safe), "LADDER_C1" = ceiling C1. Anything else is a problem
  verify_unlock     /etc/mal-h5-arena/LADDER_UNLOCK (root:root 0644, written by Helm): the confirmation PASS (p <= 0.05/c), the 0.10 and 0.25 SOL cells under
                    BOTH fail models (CI90 lo > 0, ex-top-3 > 0, a majority of days positive), the QP comment id, the merged PR, and the confirmation report's
                    sha256, checked against a root-owned copy of the report under /etc/mal-h5-arena/ladder/ (reviewer nit: the sha inside the unlock is verified)
  LadderLog         the executor's hash-chained rung records (item 9.1): each record carries the sha256 of the one before; a break is a problem
  evaluate_step_up  item 5 C0 -> C1, ALL checks at one instant; an input that is not provided is a failed check ("input_not_built:<name>"), never a pass
  step_down_reason  item 5 "Step down to C0": soft halt, daily trigger, rung drawdown, trailing-30 d_i < -200 bps, deposits below the rung's need
  daily_loss_lamports / intraday_committed_lamports   item 3 (a) the daily trigger with marks (a mark older than 10 s counts the position at zero) and (b) the
                    pre-send intraday cap (positions at zero)
Only the executor steps up (item 5), and it never steps back up after a step down (the latch is the log's step_down record).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import h5_executor as h

SOL = 1_000_000_000
CEILING_PATH = Path("/etc/mal-h5-arena/TIER")
UNLOCK_PATH = Path("/etc/mal-h5-arena/LADDER_UNLOCK")
REPORT_DIR = Path("/etc/mal-h5-arena/ladder")
UNLOCK_SCHEMA = "ladder_unlock_v1"
LADDER_BOOK = "h5v1q50-boostfloor-a1"  # Am.2 item 1: the only book the ladder covers
MARK_MAX_AGE_MS = 10_000  # item 3 (a): a mark older than 10 s counts the position at zero
TRAILING_N = 30
STEP_UP_MIN_ELIGIBLE = 100
# QP #861 condition 1 and the reviewer: c and the confirmation report are PINNED IN CODE, never read from the unlock file. They are set by a reviewed PR
# only after the DEC-031 §2 step 4 confirmation read is merged with QP OK: c = the number of arena books that had entered a confirmation by that read,
# and the report's exact sha256. While they are None, no LADDER_UNLOCK verifies (C1 is unreachable: fail closed).
CONFIRMATION_C: int | None = None
CONFIRMATION_REPORT_SHA256: str | None = None


@dataclass(frozen=True)
class Rung:
    name: str
    stake_lamports: int
    max_open: int
    max_trades_per_day: int
    daily_trigger_lamports: int
    intraday_cap_lamports: int
    lifetime_cap_lamports: int
    slack_lamports: int
    deposits_min_lamports: int
    floor_below_deposits_lamports: int


# Am.2 item 3, verbatim (the guard compares LADDER with this; a test reads the DEC text)
AM2_TABLE = {
    "C0": {"stake_sol": 0.05, "max_open": 2, "attempts": 30, "daily_trigger_sol": 0.10, "intraday_cap_sol": 0.20, "lifetime_cap_sol": 0.20, "slack_sol": 0.05,
           "deposits_min_sol": 0.35, "floor_below_sol": 0.25},
    "C1": {"stake_sol": 0.15, "max_open": 3, "attempts": 40, "daily_trigger_sol": 0.30, "intraday_cap_sol": 0.55, "lifetime_cap_sol": 0.70, "slack_sol": 0.10,
           "deposits_min_sol": 1.00, "floor_below_sol": 0.80},
}
LADDER: dict[str, Rung] = {
    "C0": Rung("C0", 50_000_000, 2, 30, 100_000_000, 200_000_000, 200_000_000, 50_000_000, 350_000_000, 250_000_000),
    "C1": Rung("C1", 150_000_000, 3, 40, 300_000_000, 550_000_000, 700_000_000, 100_000_000, 1_000_000_000, 800_000_000),
}
CEILINGS = {"T1": "C0", "LADDER_C1": "C1"}  # the TIER file's content -> the highest rung it allows. C2 is not reachable from this book (item 12)
SOFT_HALTS = frozenset({"P1", "P2", "P3", "P5", "P7", "exit_gap_gt_200bps", "entry_gap_gt_200bps", "landing_fail_excess_gt_10pp", "sell_send_late_x2"})
STOP_HALTS = frozenset({"a3_pins_changed", "pins_changed", "program_changed", "feed_break", "twin_pin_mismatch", "close_blocked_strand", "two_stuck_sells",
                        "landed_buy_unreadable", "out_of_rule_entry", "P4"})


def ladder_guard_problem() -> str | None:
    """None when every compiled rung is at or below Am.2 item 3 (stake, open, attempts, trigger, caps, slack) and needs at least its deposits and floor."""
    for name, row in AM2_TABLE.items():
        r = LADDER.get(name)
        if r is None:
            return f"ladder_guard:{name}_missing"
        pairs = (("stake_lamports", r.stake_lamports, row["stake_sol"]), ("daily_trigger_lamports", r.daily_trigger_lamports, row["daily_trigger_sol"]),
                 ("intraday_cap_lamports", r.intraday_cap_lamports, row["intraday_cap_sol"]), ("lifetime_cap_lamports", r.lifetime_cap_lamports, row["lifetime_cap_sol"]),
                 ("slack_lamports", r.slack_lamports, row["slack_sol"]))
        for field, got, sol in pairs:
            if isinstance(got, bool) or not isinstance(got, int) or not 0 < got <= round(sol * SOL):
                return f"ladder_guard:{name}.{field}"
        if not 0 < r.max_open <= row["max_open"] or not 0 < r.max_trades_per_day <= row["attempts"]:
            return f"ladder_guard:{name}.counts"
        if r.deposits_min_lamports < round(row["deposits_min_sol"] * SOL) or r.floor_below_deposits_lamports > round(row["floor_below_sol"] * SOL):
            return f"ladder_guard:{name}.wallet"
        if r.intraday_cap_lamports > r.lifetime_cap_lamports and name != "C0":
            return f"ladder_guard:{name}.intraday_above_lifetime"
        if r.floor_below_deposits_lamports != r.lifetime_cap_lamports + r.slack_lamports:
            return f"ladder_guard:{name}.floor_not_worst_case"  # item 3: floor = deposits - worst case, worst case = lifetime cap + slack
    if set(LADDER) != {"C0", "C1"}:
        return "ladder_guard:rungs"  # C2 is successor-only (item 12)
    return None


def read_ceiling(path: Path | None = None, root_checks: bool = True) -> tuple[str, str | None]:
    """(highest rung allowed, problem). Missing, unsafe, unreadable or any other content is C0 with the reason (the fail-safe, as read_tier)."""
    p = path or CEILING_PATH
    if root_checks:
        why = h.root_file_problem(p)
        if why:
            return "C0", f"tier_file_{why}"
    try:
        text = p.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return "C0", "tier_file_missing"
    except (OSError, UnicodeDecodeError):
        return "C0", "tier_file_unreadable"
    return (CEILINGS[text], None) if text in CEILINGS else ("C0", "tier_file_invalid")


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def verify_unlock(bot_id: str, path: Path | None = None, report_dir: Path | None = None) -> tuple[bool, str | None]:
    """(True, None) only when LADDER_UNLOCK is a root-owned file naming this bot and every item 9 field holds. Any problem is (False, why); never a pass."""
    p = path or UNLOCK_PATH
    rd = (report_dir or REPORT_DIR)
    why = h.root_file_problem(p)
    if why:
        return False, f"unlock_{why}"
    try:
        u = json.loads(p.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return False, "unlock_unreadable"
    if not isinstance(u, dict) or u.get("schema") != UNLOCK_SCHEMA:
        return False, "unlock_schema"
    if u.get("bot_id") != bot_id or bot_id != LADDER_BOOK:
        return False, "unlock_other_book"
    if u.get("verdict") != "PASS":
        return False, "unlock_verdict_not_pass"
    pinned_c = CONFIRMATION_C
    if isinstance(pinned_c, bool) or not isinstance(pinned_c, int) or pinned_c < 1 or not isinstance(CONFIRMATION_REPORT_SHA256, str):
        return False, "unlock_confirmation_not_pinned_in_code"  # c and the report sha come from a reviewed code pin, never from this file
    c, p_val, alpha = u.get("c"), u.get("p"), u.get("alpha")
    if c != pinned_c or isinstance(c, bool) or not _num(alpha) or abs(alpha - 0.05 / pinned_c) > 1e-12:
        return False, "unlock_alpha_not_0.05_over_pinned_c"
    if not _num(p_val) or not 0 <= p_val <= alpha:
        return False, "unlock_p_above_alpha"
    cells = u.get("cells")
    for stake in ("0.10", "0.25"):
        for model in ("flat", "pressure"):
            cell = ((cells or {}).get(stake) or {}).get(model) if isinstance(cells, dict) else None
            if not isinstance(cell, dict):
                return False, f"unlock_cell_missing:{stake}:{model}"
            days, pos = cell.get("days"), cell.get("days_positive")
            if not (_num(cell.get("ci90_lo")) and cell["ci90_lo"] > 0):
                return False, f"unlock_cell_fails:{stake}:{model}:ci90_lo"
            if not (_num(cell.get("ex_top3_total")) and cell["ex_top3_total"] > 0):
                return False, f"unlock_cell_fails:{stake}:{model}:ex_top3_total"
            if isinstance(days, bool) or not isinstance(days, int) or days < 1 or isinstance(pos, bool) or not isinstance(pos, int) or not pos * 2 > days:
                return False, f"unlock_cell_fails:{stake}:{model}:days_positive"
    if not isinstance(u.get("qp_comment"), str) or not re.fullmatch(r"[0-9]{6,}", u["qp_comment"]):
        return False, "unlock_qp_comment"
    if isinstance(u.get("merged_pr"), bool) or not isinstance(u.get("merged_pr"), int) or u["merged_pr"] < 1:
        return False, "unlock_merged_pr"
    sha = u.get("report_sha256")
    rp = u.get("report_path")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha) or not isinstance(rp, str):
        return False, "unlock_report_fields"
    if sha != CONFIRMATION_REPORT_SHA256:
        return False, "unlock_report_sha_not_pinned"
    rpath = Path(rp)
    if not rpath.is_absolute() or ".." in rpath.parts or rpath.parent != rd:
        return False, "unlock_report_not_in_ladder_dir"
    why = h.root_file_problem(rpath)
    if why:
        return False, f"unlock_report_{why}"
    try:
        data = rpath.read_bytes()
    except OSError:
        return False, "unlock_report_unreadable"
    if hashlib.sha256(data).hexdigest() != sha:
        return False, "unlock_report_sha_mismatch"  # the root copy's exact bytes equal the pinned sha256 (no substring test)
    return True, None


class LadderLog:
    """Hash-chained JSON lines in the executor's own (mal-live owned) state dir. Each record: seq, ts_ms, kind, from, to, why, prev_sha256. `problem` is set when
    the file cannot be read or the chain is broken: the caller then holds C0 and refuses every step up (fail closed)."""

    GENESIS = "0" * 64

    def __init__(self, path: Path, *, owner_uid: int | None = None):
        """`owner_uid` (live: the executor's own euid) turns on the ownership check: the log dir and file must be owned by that uid and not group- or
        other-writable, and the dir must not be a symlink. The sha256 chain has no key, so it only catches naive edits; THIS check is the control that
        nobody but the executor's user (mal-live) can write the records (QP #861 condition 2). Helm's install verifies it too (HELM-SHEET)."""
        self.path = path
        self.records: list[dict[str, Any]] = []
        self.problem: str | None = None
        if owner_uid is not None:
            self.problem = self._ownership_problem(owner_uid)
            if self.problem:
                return
        try:
            lines = path.read_text().splitlines() if path.exists() else []
        except OSError:
            self.problem = "ladder_log_unreadable"
            return
        prev = self.GENESIS
        for i, line in enumerate(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                self.problem = f"ladder_log_bad_line:{i}"
                return
            if not isinstance(rec, dict) or rec.get("prev_sha256") != prev or rec.get("seq") != i:
                self.problem = f"ladder_log_chain_break:{i}"
                return
            prev = hashlib.sha256(line.encode()).hexdigest()
            self.records.append(rec)
        self._last = prev

    def _ownership_problem(self, uid: int) -> str | None:
        import stat as st
        d = self.path.parent
        try:
            d.mkdir(mode=0o750, parents=False, exist_ok=True)
            dl = os.lstat(d)
        except OSError:
            return "ladder_log_dir_unusable"
        if st.S_ISLNK(dl.st_mode) or not st.S_ISDIR(dl.st_mode) or dl.st_uid != uid or dl.st_mode & 0o022:
            return "ladder_log_dir_not_own"
        try:
            fl = os.lstat(self.path)
        except FileNotFoundError:
            return None
        except OSError:
            return "ladder_log_unreadable"
        if st.S_ISLNK(fl.st_mode) or not st.S_ISREG(fl.st_mode) or fl.st_uid != uid or fl.st_mode & 0o022:
            return "ladder_log_file_not_own"
        return None

    @property
    def rung(self) -> str:
        """The current rung: C0 unless the last step record says otherwise. A broken log is C0."""
        if self.problem:
            return "C0"
        for rec in reversed(self.records):
            if rec.get("kind") in ("step_up", "step_down"):
                return str(rec.get("to"))
        return "C0"

    @property
    def stepped_down(self) -> bool:
        """The 'no automatic step back up' latch (item 5): any step_down record, ever. A broken log counts as latched."""
        return bool(self.problem) or any(r.get("kind") == "step_down" for r in self.records)

    def append(self, kind: str, ts_ms: int, frm: str, to: str, why: str, **detail: Any) -> dict[str, Any]:
        if self.problem:
            raise OSError(self.problem)
        rec = {"seq": len(self.records), "ts_ms": ts_ms, "kind": kind, "from": frm, "to": to, "why": why, "prev_sha256": self._last, **detail}
        line = json.dumps(rec, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o640)
        try:
            os.fchmod(fd, 0o640)  # whatever the umask: owner-writable only (the ownership check refuses a group/other-writable log)
            os.write(fd, (line + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        self._last = hashlib.sha256(line.encode()).hexdigest()
        self.records.append(rec)
        return rec


def daily_loss_lamports(realized_today: int, positions: Sequence[Mapping[str, Any]], now_ms: int) -> int:
    """Item 3 (a): realized net loss since 00:00Z (floored at 0) plus each open position's cost minus its marked value. A mark (`mark_value_lamports` at
    `mark_ms`) older than MARK_MAX_AGE_MS, missing or not a number counts the position at zero (its whole cost is the loss)."""
    loss = max(0, -int(realized_today))
    for p in positions:
        cost = int(p.get("cost_lamports") or 0)
        mv, ms = p.get("mark_value_lamports"), p.get("mark_ms")
        fresh = _num(mv) and isinstance(ms, int) and not isinstance(ms, bool) and 0 <= now_ms - ms <= MARK_MAX_AGE_MS
        loss += max(0, cost - int(mv)) if fresh else cost
    return loss


def intraday_committed_lamports(realized_today: int, worst_cases_open: Sequence[int], this_buy_worst: int) -> int:
    """Item 3 (b): today's realized net loss (floored at 0) + every open/pending/in-flight position at zero (worst case) + this buy's worst case."""
    return max(0, -int(realized_today)) + sum(int(x) for x in worst_cases_open) + int(this_buy_worst)


STEP_UP_CHECKS = ("ceiling", "latch", "unlock", "fills_match", "trailing_30", "halts", "tieout", "live_total", "impact_cells", "deposits", "floor", "sell_pin",
                  "time_left")


def evaluate_step_up(inp: Mapping[str, Any]) -> tuple[bool, list[tuple[str, bool, str]]]:
    """Item 5, C0 -> C1, every check at one instant. `inp` keys (all from the executor's own state, the chain, or root-owned files; item 9.2):
    ceiling (str), stepped_down (bool), unlock ((ok, why)), parity (the arena_live_parity result dict), trailing ({mean_d_bps, mean_g_entry_bps, n}),
    exit_gap_mean_bps, halt_history ({stop: [...], soft: [(name, cleared_ms)], now_ms}), tieout ({days_signed, all_residual_zero, step_instant_residual}),
    chain_total_lamports, deposits_lamports, floor_ok_c1 (bool), sell_pin (problem or None), ms_to_end (int). A key that is absent is a FAILED check."""
    res: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, why: str = "") -> None:
        res.append((name, bool(ok), why))

    def miss(name: str) -> None:
        add(name, False, f"input_not_built:{name}")

    add("ceiling", inp.get("ceiling") == "C1", f"ceiling {inp.get('ceiling')!r}")
    add("latch", inp.get("stepped_down") is False, "stepped down earlier: no automatic step back up")
    u = inp.get("unlock")
    add("unlock", isinstance(u, tuple) and u[0] is True, str(u[1]) if isinstance(u, tuple) else "unlock not read")
    par = inp.get("parity")
    if isinstance(par, Mapping):
        st = par.get("stats") or {}
        ok = par.get("match") is True and isinstance(st.get("n_eligible"), int) and st["n_eligible"] >= STEP_UP_MIN_ELIGIBLE
        add("fills_match", ok, f"match={par.get('match')} n_eligible={st.get('n_eligible')}")
    else:
        miss("fills_match")
    tr = inp.get("trailing")
    if isinstance(tr, Mapping) and _num(tr.get("mean_d_bps")) and _num(tr.get("mean_g_entry_bps")) and _num(inp.get("exit_gap_mean_bps")):
        ok = tr.get("n") == TRAILING_N and tr["mean_d_bps"] >= -100 and tr["mean_g_entry_bps"] <= 100 and inp["exit_gap_mean_bps"] <= 100
        add("trailing_30", ok, f"n={tr.get('n')} d={tr['mean_d_bps']} g_entry={tr['mean_g_entry_bps']} exit_gap={inp['exit_gap_mean_bps']}")
    else:
        miss("trailing_30")
    hh = inp.get("halt_history")
    if isinstance(hh, Mapping) and isinstance(hh.get("stop"), list) and isinstance(hh.get("soft"), list) and isinstance(hh.get("now_ms"), int):
        soft = hh["soft"]
        recent = [s for s in soft if not isinstance(s[1], int) or hh["now_ms"] - s[1] < 48 * 3_600_000]
        add("halts", not hh["stop"] and len(soft) <= 1 and not recent, f"stop={hh['stop']} soft={len(soft)} recent={len(recent)}")
    else:
        miss("halts")
    to = inp.get("tieout")
    if isinstance(to, Mapping) and isinstance(to.get("days_signed"), int):
        add("tieout", to["days_signed"] >= 5 and to.get("all_residual_zero") is True and to.get("step_instant_residual") == 0,
            f"days_signed={to['days_signed']} residual_zero={to.get('all_residual_zero')} step_instant={to.get('step_instant_residual')}")
    else:
        miss("tieout")
    ct = inp.get("chain_total_lamports")
    if isinstance(ct, int) and not isinstance(ct, bool):
        add("live_total", ct >= -100_000_000, f"chain total {ct}")
    else:
        miss("live_total")
    add("impact_cells", isinstance(u, tuple) and u[0] is True, "carried by the verified LADDER_UNLOCK (0.10 and 0.25 SOL cells, both fail models)")
    dep = inp.get("deposits_lamports")
    if isinstance(dep, int) and not isinstance(dep, bool):
        add("deposits", dep >= LADDER["C1"].deposits_min_lamports, f"deposits {dep}")
    else:
        miss("deposits")
    fl = inp.get("floor_ok_c1")
    add("floor", fl is True, "floor check for one C1 buy with 3 open") if fl is not None else miss("floor")
    add("sell_pin", "sell_pin" in inp and inp["sell_pin"] is None, str(inp.get("sell_pin", "input_not_built:sell_pin")))
    ms = inp.get("ms_to_end")
    add("time_left", isinstance(ms, int) and not isinstance(ms, bool) and ms >= 86_400_000, f"ms_to_end {ms}") if ms is not None else miss("time_left")
    return all(ok for _n, ok, _w in res) and len(res) == len(STEP_UP_CHECKS), res


def step_down_reason(rung: str, *, soft_halts_latched: Sequence[str], daily_trigger_fired: bool, realized_since_rung_lamports: int,
                     trailing_mean_d_bps: float | None, deposits_lamports: int | None) -> str | None:
    """Item 5 'Step down to C0, from C1 or C2'. At C0 there is nothing to step down to (soft halts and the trigger PAUSE there): None.
    At C1 an unknown trailing d_i or unknown deposits is a step down (fail closed)."""
    if rung == "C0":
        return None
    r = LADDER[rung]
    soft = sorted(set(soft_halts_latched) & SOFT_HALTS)
    if soft:
        return f"soft_halt:{soft[0]}"
    if daily_trigger_fired:
        return "daily_trigger"
    if realized_since_rung_lamports <= -r.daily_trigger_lamports:
        return "rung_drawdown"
    if trailing_mean_d_bps is None or not _num(trailing_mean_d_bps):
        return "trailing_d_unknown"
    if trailing_mean_d_bps < -200:
        return "trailing_d_below_-200bps"
    if deposits_lamports is None or deposits_lamports < r.deposits_min_lamports:
        return "deposits_below_rung_need"
    return None
