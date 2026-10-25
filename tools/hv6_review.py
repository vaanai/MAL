#!/usr/bin/env python3
"""hv6_review: the nightly reviewer and the intraday regime review of HV6 (build item 5 of docs/research/hv6/DESIGN.md; section 3 "Use", sections 4, 6).

PAPER only. Nothing here reads a key or opens a socket. The model path is a duck-typed `model.complete(request, now_ms)` (tools.hv6_agents.CallerModel
live, tools.hv6_agents.StubModel in tests). HV4 files are imported and never edited; where DESIGN says "as HV4" the HV4 function is reused when it does
not depend on HV4's persona table (tools.hv4_review: canon, shuffle_outcomes, parse_predicate, match_stats, paragraph rules, CardStore, apply_change) and a
GEN copy is written here when it does (knob bounds, trade loader, change validator, manifest).

What a nightly bundle holds (review day D, cutoff = D 00:00:00.000Z, frozen at 00:01Z)
  * trades  HV6's OWN closed trades of the arm with entry_ms >= T0 (G4: T0 is the genesis manifest's clock; nothing from the smoke, warm-up or any
            pre-T0 hour) and close_ms strictly before the cutoff, not shown in an earlier bundle. As HV4: hold_s, net_pct, exit_reason only; the true
            close_ms/entry_ms go to the audit file. Flat 15% leg at 1.9 s, 0.25 SOL stake; the pressure leg is never read.
  * skips   up to params.skip_count skipped cards of day D-1 with the v0 default plan's replayed outcome (a SkipPricer; none -> no skips).
  * rules text, knobs, local vetoes, active regime rules, open-position count, change log.
  * L and P only: `regime_evidence`, the hv6_regime.v1 block (tools/hv6_regime_block.py) as built by code. L-nr gets the same bundle without it.
  * nothing from any other arm and nothing from HV4/Q50. Records at or after the cutoff are dropped BEFORE anything is built or shuffled.

Placebo (P). The evidence set is permuted by tools.hv4_review.shuffle_outcomes (Fisher-Yates driven by sha256(arm|date), pre-registered, so a rerun of
a night gives the same bytes). P sees the REAL block; only outcomes are shuffled. The same shuffled set feeds the bundle and the validator.

Validators. <= params.max_changes (<= 3) changes in all per reply; playbook_edit/knob/local_veto need a predicate matching >= params.min_matches (8, 10
or 15) past trades or skips, a claim whose sign agrees with the mean difference, 1-20 evidence ids that exist; regime_rule goes through
tools.hv6_agents.validate_regime_rule (cites present RG- ids of a usable block with as_of_ms before the write instant; the match rule is waived for
TTL changes, DESIGN s3) and only for the arms that have a regime block (L, P). L-nr: a regime_rule is refused.

Manifest chain (<root>/manifests/<arm>.jsonl, one hash-chained line per version). Kinds: genesis, nightly, intraday.
  Rules (nightly): max_input_close_ms < cutoff; block_as_of_ms < written_ms; effective_from = max(cutoff + 30 min, written_ms).
  Intraday rules (DESIGN s3 R6, frozen): outcome inputs at or before the day's 00:01Z freeze (here: the line's max_input_close_ms < that day's
  cutoff, so inside the freeze); block as_of_ms strictly before written_ms; the version is hashed and logged before its first decision
  (effective_from_ms = written_ms + 30 min, the latency's last term). An intraday line never changes playbook, knobs or vetoes (only TTL'd regime rules),
  belongs to the day of the latest nightly line, at most 2 per arm per day. HV4's `max_input_close_ms` rule is replaced by these three.

Self-seal (G3). Reviewer replies, playbook versions, the change log, validator results and per-arm spend are written only under the HV6 root and
never printed. The CLI prints only the manifests' sha256 chain and the decision-log sha256.

A truncated reply (stop_reason max_tokens), a refusal, an error or an empty reply is an abstain: no change, recorded as review_error. The regime review
uses tools.hv6_agents.MAX_TOKENS["regime"] (1200), the nightly reviewer MAX_TOKENS["review"] (4000).
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import fcntl
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tools import hv4_prompts as pr
from tools import hv4_review as RV
from tools import hv6_agents as ag
from tools import hv6_cards as H
from tools import hv6_regime_block as rb
from tools.haiku_caller import MAX_INPUT_TOKENS, canonical_bytes, worst_case_input_tokens
from tools.hv4_budget import utc_day
from tools.hv4_review import (DAY_MS, EFFECTIVE_OFFSET_MS, EXIT_FILL_MS, GENESIS, MAX_VETOES, REQUEST_MAX_BYTES, STAKE_SOL, BundleTooLarge,
                              ManifestRefused, ReviewError, ReviewReplyError, canon, day_start_ms, sha256_hex)
from tools.hv4_validate import PredicateError, parse_watch_predicate

HV6_ROOT = str(ag.DEFAULT_ROOT)
TEMPLATE_DIR = Path(__file__).resolve().parent / "hv6_templates"
MODEL = ag.MODEL_ID
FREEZE_MS = 60_000                      # the day's 00:01Z freeze
MAX_INTRADAY_PER_DAY = 2                # DESIGN s3: cap 2/day
SCHEMA = "hv6-manifest-1"
KINDS = ("genesis", "nightly", "intraday")
NO_BLOCK_ARMS = ("L-nr",)

# ---------------------------------------------------------------------------------------------------------------------------------------- params
# DESIGN s7 item 3: the changeable registry. Anything else is a human-written PR with QP review.
REGISTRY = {"min_matches": (8, 10, 15), "skip_count": (20, 40, 60), "max_changes": (1, 2, 3), "ttl_h_max": (24, 48, 72), "evidence_days": (3, 5, 7)}
# READING: "evidence window" = the days of the change log shown to the reviewer (HV4's CHANGE_LOG_DAYS = 7); "bundle skip count" = the cap on
# skipped cards per bundle (HV4's 60); "TTL <= 72 h" = the cap on a regime rule's ttl_h, lowerable to 48 or 24.


@dataclass(frozen=True)
class Params:
    min_matches: int = 10
    skip_count: int = 60
    max_changes: int = 3
    ttl_h_max: int = 72
    evidence_days: int = 7

    def __post_init__(self):
        for k, allowed in REGISTRY.items():
            v = getattr(self, k)
            if isinstance(v, bool) or v not in allowed:
                raise ReviewError(f"params_{k}_not_in_registry")

    def sha256(self) -> str:
        return sha256_hex(canon(asdict(self)))


V60_PARAMS = Params()


# ---------------------------------------------------------------------------------------------------------------------------------------- state and knobs
def v0_state() -> RV.ArmState:
    return RV.ArmState(ag.PERSONA, 0, ag.load_playbook("v0"), ag.v0_knobs(), ())


def check_knob(name: str, value: Any) -> str | None:
    """None when value is a legal GEN setting of knob `name`, else a fixed code."""
    if name not in ag.knob_names():
        return "knob_unknown"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        return "knob_not_number"
    lo, hi = ag.GEN_SPEC.min_conviction_bounds if name == "min_conviction" else ag.GEN_SPEC.plan_bounds[name]
    if name in ("min_conviction", "time_min") and isinstance(value, float) and not value.is_integer():
        return "knob_not_integer"
    return None if lo <= value <= hi else "knob_out_of_bounds"


RELEASE_RE = re.compile(r"v6\.(0|[1-9][0-9]?)")
DEFAULT_RELEASE = "v6.0"
PERM_FILE, PERM_DIR = 0o600, 0o700
APPEND_MARGIN_MS = 60_000               # a nightly line is in force at least this long after it was written (R6: logged before its first decision)
NOW_SLACK_MS = 60_000                   # outside test mode --now-ms must be within this of the wall clock
MAX_BLOCK_BYTES = 2_000_000
PROBE_KEYS_IN_BLOCK = ("drill", "smoke", "stub", "synthetic")


def release_n(release: str) -> int:
    m = RELEASE_RE.fullmatch(release or "") if isinstance(release, str) else None
    if not m:
        raise ReviewError("bad_release_id")
    return int(m.group(1))


def bot_id(release: str, arm: str) -> str:
    """THE bot id of an arm in a release, from one function: hv6.<N>-<arm> (v6.0 -> hv6.0-L, the runner's BOT_PREFIX + arm). Nothing else builds one and
    no caller supplies a prefix."""
    if arm not in ag.ARMS:
        raise ReviewError("unknown_arm")
    return f"hv6.{release_n(release)}-{arm}"


def wall_ms() -> int:
    return time.time_ns() // 1_000_000


def _guard(path: str | os.PathLike, mode: str = "r") -> str:
    """Every path this module opens goes through tools.hv6_cards.check (allowlist, deny tokens, realpath, symlinks out of the allowlist)."""
    try:
        return H.check(path, mode)
    except H.PathRefused as e:
        raise ReviewError(f"path_refused:{e}") from None


def _deny_root(p: str | os.PathLike) -> None:     # kept name: read-check of a root
    _guard(p, "r")


def default_ledger_root(root: str | os.PathLike) -> str:
    """The runner's default: <HV6 root>/engine-ledger. A release root <root>/releases/vN shares its parent's engine ledger (bot ids carry the release)."""
    r = Path(root)
    base = r.parent.parent if r.parent.name == "releases" else r
    return str(base / "engine-ledger")


def _read_jsonl(path: Path) -> list[dict]:
    return RV._jsonl(Path(_guard(path, "r")))


def _checked_files(directory: Path, pattern: str) -> list[Path]:
    d = Path(_guard(directory, "r"))
    return [Path(_guard(f, "r")) for f in sorted(d.glob(pattern))] if d.is_dir() else []


def _write_once(path: Path, data: bytes) -> None:
    """Write-once, mode 0600 (dirs 0700), under the HV6 root only."""
    path = Path(_guard(path, "w"))
    if path.exists():
        if path.read_bytes() != data:
            raise ManifestRefused("file_exists_with_other_content")
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=PERM_DIR)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PERM_FILE)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def log_event(root: str | os.PathLike, event: Mapping) -> None:
    """One JSON line in <root>/logs/review-events.jsonl (0600, under the HV6 root, never printed)."""
    path = Path(_guard(Path(root) / "logs" / "review-events.jsonl", "w"))
    path.parent.mkdir(parents=True, exist_ok=True, mode=PERM_DIR)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, PERM_FILE)
    try:
        os.write(fd, canon(dict(event)).encode() + b"\n")
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------------------------------------------------------------------- records
def load_trade_records(arm: str, root: str | os.PathLike, ledger_root: str | os.PathLike, cutoff_ms: int, t0_ms: int,
                       release: str = DEFAULT_RELEASE) -> tuple[list[dict], int]:
    """(closed trades of THIS arm with t0_ms <= entry_ms and close_ms < cutoff_ms, positions open at the cutoff). Joins the arm's decision log with the
    ledger's priced rows of bot `bot_id(release, arm)`. As HV4's loader (flat leg 1.9 s, rule 'total', 0.25 SOL) but with the G4 entry >= T0 filter.
    Only that bot's directory under the ledger root is opened, every path through the guard. A sell row in the decision log with no ledger directory
    for the bot is a refusal (`ledger_dir_missing`), never an empty book."""
    _guard(root)
    _guard(ledger_root)
    dec = Path(root) / "decisions" / arm
    entries: dict[str, dict] = {}
    sells: dict[str, dict] = {}
    refused: set[str] = set()
    for f in _checked_files(dec, "*.jsonl"):
        for row in _read_jsonl(f):
            if row.get("kind") == "entry" and row.get("pos"):
                entries[row.get("order_id")] = row
            elif row.get("kind") == "sell":
                sells[row.get("pid")] = row
            elif row.get("kind") == "order_refused":
                if row.get("side") == "buy":
                    refused.add(row.get("order_id"))
                else:
                    sells.pop(row.get("order_id"), None)
    out: list[dict] = []
    led = Path(ledger_root) / bot_id(release, arm)
    if sells and not Path(_guard(led, "r")).is_dir():
        raise ReviewError("ledger_dir_missing")
    for f in _checked_files(led, "trades-*.jsonl"):
        for row in _read_jsonl(f):
            pid = row.get("position_id")
            ent = entries.get(pid)
            if row.get("kind") != "trade" or ent is None:
                continue
            stake = float(row.get("stake_sol", 0))
            probe = ent.get("tag") == "regime_probe"
            if abs(stake - STAKE_SOL) > 1e-9 and not probe:
                continue
            ms = row.get("decision_ms") or {}
            if not isinstance(ms.get("entry"), int) or not isinstance(ms.get("exit"), int) or ms["entry"] < t0_ms:
                continue
            close = ms["exit"] + EXIT_FILL_MS
            if close >= cutoff_ms:
                continue
            net = row["legs"][RV.FLAT_RULE][RV.FLAT_LEG]["net_lamports"]
            rec = {"id": pid, "kind": "trade", "entry_ms": ms["entry"], "close_ms": close, "card": ent.get("card") or {}, "decision": RV._decision_of(ent),
                   "exit_reason": str(row.get("exit_reason") or (sells.get(pid) or {}).get("reason") or ""),
                   "hold_s": int(round((ms["exit"] - ms["entry"]) / 1000.0)), "net_pct": RV._round(100.0 * float(net) / (stake * 1e9), 4)}
            if probe:
                rec["tag"] = "regime_probe"
            out.append(rec)
    done_exit = {pid: s for pid, s in sells.items() if s.get("ms", 0) + EXIT_FILL_MS < cutoff_ms}
    open_n = sum(1 for pid, e in entries.items() if pid not in refused and t0_ms <= e["ms"] < cutoff_ms and pid not in done_exit)
    return sorted(out, key=lambda r: (r["close_ms"], r["id"])), open_n


def load_skip_records(arm: str, root: str | os.PathLike, day: str, pricer: RV.SkipPricer | None, cutoff_ms: int, t0_ms: int) -> list[dict]:
    """HV4's skip loader (cards from the card store, v0 default plan replay); rows with entry_ms < T0 are dropped (G4)."""
    _guard(root)
    prev = utc_day(day_start_ms(day) - 1)
    for d in (day, prev):                                   # the files HV4's skip loader will open, checked first (realpath, symlinks)
        _guard(Path(root) / "decisions" / arm / f"{day}.jsonl")
        _guard(Path(root) / "cards" / f"{d}.jsonl")
    return [r for r in RV.load_skip_records(arm, root, day, ag.PERSONA, pricer, cutoff_ms) if r["entry_ms"] >= t0_ms]


# ---------------------------------------------------------------------------------------------------------------------------------------- validator
def _validate_one(c: Mapping, state: RV.ArmState, evidence: Sequence[Mapping], universe: Mapping[str, float], ids: set[str], params: Params) -> tuple[dict | None, str | None]:
    """GEN copy of tools.hv4_review._validate_one: GEN knob bounds and params.min_matches. Pure."""
    if set(c) != RV.CHANGE_KEYS:
        return None, "bad_keys"
    typ, target, new = c["type"], c["target"], c["new"]
    if typ not in RV.CHANGE_TYPES:
        return None, "bad_type"
    if c["claim"] not in RV.CLAIMS:
        return None, "bad_claim"
    if not isinstance(c["why"], str) or not RV.WHY_OK.fullmatch(c["why"]) or RV._words(c["why"]) > RV.MAX_WHY_WORDS:
        return None, "bad_why"
    ev = c["evidence_ids"]
    if not isinstance(ev, list) or not 1 <= len(ev) <= RV.MAX_EVIDENCE_IDS or not all(isinstance(i, str) and RV.ID_OK.fullmatch(i) for i in ev):
        return None, "bad_evidence_ids"
    if len(set(ev)) != len(ev) or not set(ev) <= ids:
        return None, "unknown_evidence_id"
    try:
        terms, norm = RV.parse_predicate(c["predicate"], universe)
    except ReviewError as e:
        return None, str(e)
    if not isinstance(target, str):
        return None, "bad_target"
    out = {"type": typ, "target": target, "predicate": norm, "claim": c["claim"], "evidence_ids": list(ev), "why": c["why"]}
    if typ == "knob":
        if RV.NEUTRAL_NAME.search(target):
            return None, "neutral_field"
        err = check_knob(target, new)
        if err:
            return None, err
        out["new"] = int(new) if target in ("min_conviction", "time_min") else new
    elif typ == "local_veto":
        if target != "veto":
            return None, "bad_target"
        if c["claim"] != "worse":
            return None, "veto_claim_must_be_worse"
        if not isinstance(new, str):
            return None, "bad_new"
        try:
            _t, vnorm = RV.parse_predicate(new, universe)
        except ReviewError as e:
            return None, str(e)
        if vnorm != norm:
            return None, "veto_differs_from_predicate"
        out["new"] = vnorm
    else:
        if not isinstance(new, str):
            return None, "bad_new"
        if RV.NEUTRAL_NAME.search(target) or RV.NEUTRAL_NAME.search(new):
            return None, "neutral_field"
        if not RV.TITLE_OK.fullmatch(target) or target in RV.FROZEN_TITLES:
            return None, "bad_or_frozen_target"
        if not RV.PARA_OK.fullmatch(new) or not new.startswith(target + ". ") or RV._words(new) > RV.MAX_PARAGRAPH_WORDS:
            return None, "bad_paragraph"
        out["new"] = new
    st = RV.match_stats(terms, evidence)
    if st["n_match"] < params.min_matches:
        return None, f"fewer_than_{params.min_matches}_matches"
    if st["n_rest"] < 1 or st["mean_match"] is None or st["mean_rest"] is None or st["mean_match"] == st["mean_rest"]:
        return None, "no_contrast"
    if (st["mean_match"] < st["mean_rest"]) != (c["claim"] == "worse"):
        return None, "effect_sign_mismatch"
    out["evidence"] = {"n_match": st["n_match"], "mean_match": RV._round(st["mean_match"]), "mean_rest": RV._round(st["mean_rest"])}
    return out, None


@dataclass(frozen=True)
class ChangeResult:
    accepted: tuple[dict, ...]               # playbook_edit / knob / local_veto, then regime_rule (with expires_ms), in reply order
    rejected: tuple[tuple[int, str], ...]
    state: RV.ArmState
    reply_error: str | None = None
    regime_rules: tuple[dict, ...] = ()      # the accepted regime_rule changes, as stored in the manifest (a subset of `accepted`)


def validate_reply(reply_text: Any, state: RV.ArmState, evidence: Sequence[Mapping], *, params: Params = V60_PARAMS, arm: str, block: Mapping | None = None,
                   now_ms: int = 0, active_rules: Sequence[Mapping] = (), base: pr.Playbook | None = None, only_regime: bool = False) -> ChangeResult:
    """Parse, test and apply the reply. More than params.max_changes items refuse the whole reply. `evidence` is every record of the arm closed before
    the cutoff (shuffled already for P). `only_regime` (the intraday review) refuses any other change type."""
    base = base or ag.load_playbook("v0")
    try:
        changes = RV.parse_changes(reply_text)
        if len(changes) > params.max_changes:
            raise ReviewReplyError("too_many_changes")
    except ReviewReplyError as e:
        return ChangeResult((), (), state, str(e))
    universe = RV.numeric_universe(evidence)
    ids = {r["id"] for r in evidence}
    has_block = ag.ARMS[arm].regime_review
    live = [dict(r) for r in active_rules if isinstance(r.get("expires_ms"), int) and r["expires_ms"] > now_ms]
    accepted: list[dict] = []
    rejected: list[tuple[int, str]] = []
    regime: list[dict] = []
    cur = state
    used: set[tuple[str, str]] = set()
    for i, c in enumerate(changes):
        if c.get("type") == ag.REGIME_TYPE:
            if not has_block:
                rejected.append((i, "regime_rule_not_available"))
                continue
            v = ag.validate_regime_rules([c], knobs=cur.knobs, block=block, now_ms=now_ms, universe=universe, vetoes=cur.vetoes,
                                         active_probes=[r for r in live if r.get("extra_entries_per_h")])[0]
            if not v.ok:
                rejected.append((i, v.reason or "regime_rule_refused"))
                continue
            if (v.change["ttl_h"]) > params.ttl_h_max:
                rejected.append((i, "ttl_over_release_cap"))
                continue
            key = ("regime_rule", v.change["target"] if v.change["target"] != "local_veto" else v.change["new"])
            if key in used or any((r["target"], r["new"]) == (v.change["target"], v.change["new"]) for r in live):
                rejected.append((i, "duplicate_target"))
                continue
            used.add(key)
            ch = {**v.change, "expires_ms": v.expires_ms, "mode": v.mode}
            if v.tag:
                ch["tag"] = v.tag
            accepted.append(ch)
            regime.append(ch)
            continue
        if only_regime:
            rejected.append((i, "not_a_regime_rule"))
            continue
        ch, err = _validate_one(c, cur, evidence, universe, ids, params)
        nxt = None
        if err is None:
            key = (ch["type"], ch["target"] if ch["type"] != "local_veto" else ch["new"])
            if key in used:
                err = "duplicate_target"
            else:
                nxt, err = RV.apply_change(cur, ch, base)
        if err is not None:
            rejected.append((i, err))
            continue
        used.add(key)
        accepted.append(ch)
        cur = nxt
    return ChangeResult(tuple(accepted), tuple(rejected), cur, None, tuple(regime))


# ---------------------------------------------------------------------------------------------------------------------------------------- the bundle
@dataclass(frozen=True)
class Bundle:
    obj: dict
    text: str
    sha256: str
    request: pr.Request
    request_bytes: int
    shown_ids: tuple[str, ...]
    max_input_close_ms: int
    dropped: dict
    evidence: tuple[dict, ...]
    audit_text: str = ""
    audit_sha256: str = ""
    block_sha256: str | None = None
    block_as_of_ms: int | None = None


def templates(kind: str) -> tuple[str, str, str]:
    """(system text, user template text, sha256 of both) for kind 'review' or 'regime'."""
    name = {"review": "review", "regime": "regime"}[kind]
    s = (TEMPLATE_DIR / f"{name}_system.txt").read_text(encoding="utf-8")
    u = (TEMPLATE_DIR / f"{name}_user.txt").read_text(encoding="utf-8")
    return s, u, sha256_hex(s + "\x00" + u)


def reviewer_sha256() -> str:
    return templates("review")[2]


def regime_template_sha256() -> str:
    return templates("regime")[2]


def review_nonce(arm: str, day: str, bundle_sha: str, kind: str = "review", seq: int = 0) -> str:
    return sha256_hex(f"{arm}|{kind}|{bundle_sha}|{day}" + (f"|{seq}" if kind == "regime" else ""))


def build_request(arm: str, kind: str, day: str, state: RV.ArmState, bundle_text: str, bundle_sha: str, seq: int = 0) -> pr.Request:
    """kind 'review' (nightly, max_tokens 4000) or 'regime' (intraday, MAX_TOKENS['regime'] = 1200). One call, no cache marker."""
    system, user_t, tsha = templates(kind)
    n = review_nonce(arm, day, bundle_sha, kind, seq)
    user = pr.render(pr.Template(kind, user_t, ""), {"BUNDLE": bundle_text, "NONCE": n[:pr.NONCE_CHARS]})
    req = pr.Request(arm=arm, kind=kind, persona=state.persona, system=({"type": "text", "text": system},), user_text=user, max_tokens=ag.MAX_TOKENS[kind],
                     ids=(), card_shas=(), batch_sha=bundle_sha, nonce=n, seq=seq, template_sha256=tsha, playbook_name=state.persona,
                     playbook_version=f"v{state.version}", playbook_sha256=state.playbook.sha256)
    req.body()
    return req


def _item_trade(r: Mapping, strip: bool) -> dict:
    it = RV._trade_item(r, strip)
    if r.get("tag") == "regime_probe":
        it["tag"] = "regime_probe"
    return it


def change_log(manifests: Sequence[Mapping], records: Sequence[Mapping], cutoff_ms: int, days: int) -> list[dict]:
    """HV4's change log over `days` days (params.evidence_days), also listing regime_rule changes (target, new, ttl_h; no predicate, so no measured
    effect). Measured effects use the same (shuffled, for P) records the reviewer sees."""
    rows = [m for m in manifests if m.get("seq", 0) >= 1]
    trades = [r for r in records if r["kind"] == "trade"]
    out = []
    for i, m in enumerate(rows):
        if m["cutoff_ms"] < cutoff_ms - days * DAY_MS:
            continue
        end = rows[i + 1]["effective_from_ms"] if i + 1 < len(rows) else cutoff_ms
        inforce = [r for r in trades if m["effective_from_ms"] <= r["entry_ms"] < end]
        chs = []
        for ch in m.get("changes", []):
            if ch.get("type") == ag.REGIME_TYPE:
                chs.append({"type": ch["type"], "target": RV._clean(ch["target"], 40), "new": RV._clean(ch["new"], 200), "ttl_h": ch.get("ttl_h")})
                continue
            eff: dict = {"n_in_force": len(inforce)}
            try:
                terms = parse_watch_predicate(ch["predicate"], RV.numeric_universe(inforce) or {})
                st = RV.match_stats(terms, inforce)
                eff.update(n_match=st["n_match"], mean_match=RV._round(st["mean_match"]), mean_rest=RV._round(st["mean_rest"]))
            except (PredicateError, KeyError):
                pass
            chs.append({"type": ch["type"], "target": RV._clean(ch["target"], 40), "new": RV._clean(ch["new"], 200), "predicate": ch["predicate"],
                        "claim": ch["claim"], "effect": eff})
        out.append({"day": m["review_day"], "version": m["version"], "kind": m["kind"], "effective_from_ms": m["effective_from_ms"], "changes": chs,
                    "version_effect": {"n": len(inforce), "mean_net_pct": RV._round(RV._mean([r["net_pct"] for r in inforce]))}})
    return out


def block_sha256(block: Mapping) -> str:
    return sha256_hex(canon(dict(block)))


def build_bundle(arm: str, day: str, state: RV.ArmState, records: Sequence[Mapping], *, t0_ms: int = 0, shown_before: Sequence[str] = (), open_positions: int = 0,
                 manifests: Sequence[Mapping] = (), block: Mapping | None = None, active_rules: Sequence[Mapping] = (), params: Params = V60_PARAMS,
                 limit: int = REQUEST_MAX_BYTES) -> Bundle:
    """The nightly bundle of `arm` for review day `day`. Only entry_ms >= t0_ms and close_ms < cutoff survive, before any shuffle (P shuffles
    after). `block` goes in only for arms with a regime block (L, P); for L-nr it is ignored."""
    spec = ag.ARMS[arm]
    if not spec.reviewer:
        raise ReviewError("no_reviewer_for_arm")
    cutoff = day_start_ms(day)
    ev = RV.before_cutoff([r for r in records if r.get("entry_ms", 0) >= t0_ms], cutoff)
    if spec.role.startswith("placebo"):
        ev = RV.shuffle_outcomes(ev, arm, day)
    shown = set(shown_before)
    trades = sorted((r for r in ev if r["kind"] == "trade" and r["id"] not in shown), key=lambda r: (r["entry_ms"], r["id"]))
    skips = RV.skip_order([r for r in ev if r["kind"] == "skip" and r["id"] not in shown and cutoff - DAY_MS <= r["entry_ms"] < cutoff])[:params.skip_count]
    log = change_log(manifests, ev, cutoff, params.evidence_days)
    for r in (*trades, *skips):
        pr.check_strings(r["card"])
    use_block = block if spec.regime_review and block is not None else None
    playbook = {"version": state.version, "sha256": state.playbook.sha256, "rules": state.playbook.rules}
    live = [dict(r) for r in active_rules]

    def make(nt: int, ns: int, strip: bool):
        obj = {"schema": "hv6-review-bundle-1", "arm": arm, "review_day": day, "cutoff_ms": cutoff, "min_matches": params.min_matches, "playbook": playbook,
               "knobs": dict(state.knobs), "vetoes": list(state.vetoes), "regime_rules": live, "open_positions": int(open_positions), "change_log": log,
               "trades": [_item_trade(r, strip) for r in trades[:nt]], "skips": [RV._skip_item(r, strip) for r in skips[:ns]],
               "dropped": {"trades": len(trades) - nt, "skips": len(skips) - ns, "candles_stripped": strip}}
        if use_block is not None:
            obj["regime_evidence"] = dict(use_block)
        text = canon(obj)
        sha = sha256_hex(text)
        req = build_request(arm, "review", day, state, text, sha)
        return obj, req, RV.request_size(req), text, sha

    def fits(nt, ns, strip):
        return make(nt, ns, strip)[2] <= limit

    nt, ns, strip = len(trades), len(skips), False
    if not fits(nt, ns, strip):
        strip = True
        if not fits(nt, ns, strip):
            k = RV._largest(0, ns, lambda k: fits(nt, k, True))
            if k is not None:
                ns = k
            else:
                ns = 0
                m = RV._largest(0, nt, lambda m: fits(m, 0, True))
                if m is None:
                    raise BundleTooLarge("bundle_too_large")
                nt = m
    obj, req, size, text, sha = make(nt, ns, strip)
    if size > limit or (limit <= REQUEST_MAX_BYTES and worst_case_input_tokens(req.body()) > MAX_INPUT_TOKENS):
        raise BundleTooLarge("bundle_too_large")
    close = max([r["close_ms"] for r in ev], default=0)
    audit = canon({"schema": "hv6-review-audit-1", "arm": arm, "review_day": day, "cutoff_ms": cutoff, "closes": {r["id"]: r["close_ms"] for r in ev}})
    return Bundle(obj, text, sha, req, size, tuple(r["id"] for r in trades[:nt]), close, dict(obj["dropped"]), tuple(ev), audit, sha256_hex(audit),
                  block_sha256(use_block) if use_block is not None else None, use_block.get("as_of_ms") if use_block is not None else None)


def build_regime_bundle(arm: str, day: str, n: int, state: RV.ArmState, records: Sequence[Mapping], block: Mapping, *, t0_ms: int = 0,
                        manifests: Sequence[Mapping] = (), active_rules: Sequence[Mapping] = (), params: Params = V60_PARAMS) -> Bundle:
    """The intraday regime-review input: ONLY the block, the current playbook/knobs/vetoes/active rules and the change log (no trades, no skips).
    Outcome inputs are the day's change-log records, all closed before the day's cutoff (inside the 00:01Z freeze)."""
    spec = ag.ARMS[arm]
    if not spec.regime_review:
        raise ReviewError("no_regime_review_for_arm")
    cutoff = day_start_ms(day)
    ev = RV.before_cutoff([r for r in records if r.get("entry_ms", 0) >= t0_ms], cutoff)
    if spec.role.startswith("placebo"):
        ev = RV.shuffle_outcomes(ev, arm, day)
    log = change_log(manifests, ev, cutoff, params.evidence_days)
    obj = {"schema": "hv6-regime-bundle-1", "arm": arm, "review_day": day, "intraday_n": n, "cutoff_ms": cutoff,
           "playbook": {"version": state.version, "sha256": state.playbook.sha256, "rules": state.playbook.rules}, "knobs": dict(state.knobs),
           "vetoes": list(state.vetoes), "regime_rules": [dict(r) for r in active_rules], "change_log": log, "regime_evidence": dict(block)}
    text = canon(obj)
    sha = sha256_hex(text)
    req = build_request(arm, "regime", day, state, text, sha, n)
    size = RV.request_size(req)
    if size > REQUEST_MAX_BYTES or worst_case_input_tokens(req.body()) > MAX_INPUT_TOKENS:
        raise BundleTooLarge("bundle_too_large")
    close = max([r["close_ms"] for r in ev], default=0)
    audit = canon({"schema": "hv6-review-audit-1", "arm": arm, "review_day": day, "cutoff_ms": cutoff, "closes": {r["id"]: r["close_ms"] for r in ev}})
    return Bundle(obj, text, sha, req, size, (), close, {}, tuple(ev), audit, sha256_hex(audit), block_sha256(block), block.get("as_of_ms"))


# ---------------------------------------------------------------------------------------------------------------------------------------- regime trigger
def direct_high_ids(block: Mapping | None) -> list[str]:
    """RG ids of HIGH events with relevance `direct` in a usable block (the intraday-review trigger set)."""
    if not isinstance(block, Mapping) or block.get("radar_status") not in ag.USABLE_STATUS:
        return []
    return sorted(e["id"] for e in block.get("events") or [] if isinstance(e, Mapping) and e.get("sev") == "HIGH" and e.get("relevance") == "direct"
                  and isinstance(e.get("id"), str))


def new_direct_high(block: Mapping | None, seen_ids: Sequence[str]) -> list[str]:
    seen = set(seen_ids)
    return [i for i in direct_high_ids(block) if i not in seen]


def read_block_file(path: str | os.PathLike, root: str | os.PathLike | None = None) -> tuple[dict, bytes]:
    """(block, the exact bytes read) of one saved block file, with the runner's checks: allowlisted real path; a regular file of at most MAX_BLOCK_BYTES;
    the file name is `block-<as_of_ms>.json` and carries the block's own as_of_ms; schema hv6_regime.v1; no drill/smoke/stub/synthetic key; and, when
    the block names a radar input sha256, the byte copy `radar-<as_of_ms>.jsonl` beside it hashes to it. With `root`, the file must be in <root>/regime/.
    Raises ReviewError(code)."""
    p = Path(_guard(path, "r"))
    if root is not None and Path(os.path.realpath(p)).parent != Path(os.path.realpath(Path(root) / "regime")):
        raise ReviewError("block_not_under_regime_dir")
    if os.path.islink(p) or not p.is_file() or p.stat().st_size > MAX_BLOCK_BYTES:
        raise ReviewError("block_file_unreadable")
    data = p.read_bytes()
    try:
        blk = json.loads(data)
    except ValueError:
        raise ReviewError("block_not_json") from None
    ms = blk.get("as_of_ms") if isinstance(blk, dict) else None
    if not isinstance(blk, dict) or blk.get("schema") != rb.BLOCK_SCHEMA or isinstance(ms, bool) or not isinstance(ms, int):
        raise ReviewError("block_schema")
    if any(k in blk for k in PROBE_KEYS_IN_BLOCK):
        raise ReviewError("block_is_drill_or_synthetic")
    if p.name != f"block-{ms}.json":
        raise ReviewError("block_filename")
    want = (blk.get("input") or {}).get("sha256") if isinstance(blk.get("input"), dict) else None
    if want is not None:
        cp = Path(_guard(p.parent / f"radar-{ms}.jsonl", "r"))
        if not cp.is_file() or sha256_hex(cp.read_bytes()) != want:
            raise ReviewError("radar_copy_mismatch")
    return blk, data


def latest_block_file(regime_dir: str | os.PathLike, before_ms: int) -> tuple[dict, bytes] | None:
    """The newest valid block-<as_of_ms>.json in the dir with as_of_ms strictly before before_ms (R6), with its bytes. An invalid file is skipped."""
    d = Path(_guard(regime_dir, "r"))
    cands = []
    for f in d.glob("block-*.json") if d.is_dir() else []:
        try:
            ms = int(f.stem.split("-", 1)[1])
        except ValueError:
            continue
        if ms < before_ms:
            cands.append((ms, f))
    for ms, f in sorted(cands, reverse=True):
        try:
            return read_block_file(f)
        except ReviewError:
            continue
    return None


def latest_block(regime_dir: str | os.PathLike, before_ms: int) -> dict | None:
    got = latest_block_file(regime_dir, before_ms)
    return got[0] if got else None


# ---------------------------------------------------------------------------------------------------------------------------------------- manifests
MANIFEST_FIELDS = ("schema", "kind", "seq", "arm", "version", "review_day", "intraday_n", "cutoff_ms", "written_ms", "effective_from_ms", "max_input_close_ms",
                   "commit", "model", "reviewer_sha256", "regime_sha256", "release", "params", "params_sha256", "playbook_sha256", "playbook_header_sha256",
                   "knobs", "knobs_sha256", "vetoes", "vetoes_sha256", "regime_rules", "block_sha256", "block_as_of_ms", "trigger_ids", "bundle_sha256", "audit_sha256",
                   "request_sha256", "response_sha256", "changes", "rejected", "reply_error", "review_error", "shown_ids", "truncation", "skip_pricer",
                   "prev_manifest_sha256", "sha256")
SHA_RE = RV.SHA_RE
COMMIT_RE = RV.COMMIT_RE


def manifest_hash(row: Mapping) -> str:
    return sha256_hex(canon({k: v for k, v in row.items() if k != "sha256"}))


def _paths(root: str | os.PathLike, arm: str) -> tuple[Path, Path, Path]:
    r = Path(root)
    return r / "manifests" / f"{arm}.jsonl", r / "playbooks" / arm, r / "bundles" / arm


def read_manifests(root: str | os.PathLike, arm: str) -> list[dict]:
    return _read_jsonl(_paths(root, arm)[0])


def check_clock(clock_ms: Any) -> None:
    """G4: T0 is a 00:00Z boundary after the epoch. A clock of 0 would let the smoke and warm-up rows into every bundle."""
    if isinstance(clock_ms, bool) or not isinstance(clock_ms, int) or clock_ms <= 0 or clock_ms % DAY_MS != 0:
        raise ReviewError("clock_not_a_utc_midnight")


def genesis_manifest(arm: str, clock_start_ms: int, commit: str, *, release: str = DEFAULT_RELEASE, params: Params = V60_PARAMS) -> tuple[dict, RV.ArmState]:
    if not ag.ARMS[arm].reviewer:
        raise ReviewError("no_reviewer_for_arm")
    check_clock(clock_start_ms)
    release_n(release)
    st = v0_state()
    row = {"schema": SCHEMA, "kind": "genesis", "seq": 0, "arm": arm, "version": 0, "review_day": None, "intraday_n": 0, "cutoff_ms": 0, "written_ms": clock_start_ms,
           "effective_from_ms": clock_start_ms, "max_input_close_ms": 0, "commit": commit, "model": ag.MODEL_ID, "reviewer_sha256": reviewer_sha256(),
           "regime_sha256": regime_template_sha256() if ag.ARMS[arm].regime_review else None, "release": release, "params": asdict(params),
           "params_sha256": params.sha256(), "playbook_sha256": st.playbook.sha256, "playbook_header_sha256": st.playbook.header_sha256, "knobs": dict(st.knobs),
           "knobs_sha256": st.knobs_sha256(), "vetoes": [], "vetoes_sha256": st.vetoes_sha256(), "regime_rules": [], "block_sha256": None, "block_as_of_ms": None,
           "trigger_ids": [], "bundle_sha256": None, "audit_sha256": None, "request_sha256": None, "response_sha256": None, "changes": [], "rejected": [], "reply_error": None,
           "review_error": None, "shown_ids": [], "truncation": None, "skip_pricer": None, "prev_manifest_sha256": GENESIS}
    row["sha256"] = manifest_hash(row)
    return row, st


def check_manifest(row: Mapping, history: Sequence[Mapping], arm: str, *, reviewer_sha: str | None = None, regime_sha: str | None = None,
                   frozen_sha: str | None = None, v0_playbook_sha256: str | None = None) -> None:
    """Raises ManifestRefused(code) unless the line obeys every rule. `history` is the chain before this line (empty for the genesis)."""
    def no(code: str):
        raise ManifestRefused(code)

    prev = history[-1] if history else None
    spec = ag.ARMS[arm]
    if not isinstance(row, Mapping) or set(row) != set(MANIFEST_FIELDS):
        no("manifest_fields")
    if row["sha256"] != manifest_hash(row):
        no("manifest_hash")
    if row["schema"] != SCHEMA or row["arm"] != arm or row["model"] != ag.MODEL_ID or row["kind"] not in KINDS:
        no("arm_or_model")
    if not spec.reviewer:
        no("no_reviewer_for_arm")
    if reviewer_sha is not None and row["reviewer_sha256"] != reviewer_sha:
        no("reviewer_instructions_changed")
    if spec.regime_review:
        if regime_sha is not None and row["regime_sha256"] != regime_sha:
            no("regime_instructions_changed")
    elif row["regime_sha256"] is not None or row["block_sha256"] is not None or row["regime_rules"] or row["kind"] == "intraday":
        no("regime_fields_on_no_block_arm")
    commit = row["commit"]
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        no("commit_format")
    if frozen_sha is not None:
        f = str(frozen_sha).lower()
        if not COMMIT_RE.fullmatch(f) or not (commit.startswith(f) or f.startswith(commit)):
            no("commit_not_frozen_sha")
    if row["prev_manifest_sha256"] != (GENESIS if prev is None else prev["sha256"]):
        no("chain_broken")
    if row["seq"] != (0 if prev is None else prev["seq"] + 1):
        no("seq_gap")
    for k in ("playbook_sha256", "playbook_header_sha256", "knobs_sha256", "vetoes_sha256", "params_sha256"):
        if not isinstance(row[k], str) or not SHA_RE.match(row[k]):
            no("bad_hash_field")
    try:
        params = Params(**row["params"])
    except (TypeError, ReviewError):
        no("params_invalid")
    if params.sha256() != row["params_sha256"] or (prev is not None and row["params_sha256"] != prev["params_sha256"]) or (prev is not None and row["release"] != prev["release"]):
        no("params_changed")
    if row["knobs_sha256"] != sha256_hex(canon(dict(row["knobs"]))) or row["vetoes_sha256"] != sha256_hex(canon(list(row["vetoes"]))):
        no("knobs_or_vetoes_hash")
    if set(row["knobs"]) != set(ag.knob_names()) or any(check_knob(k, v) for k, v in row["knobs"].items()):
        no("knob_out_of_bounds")
    if len(row["vetoes"]) > MAX_VETOES or (prev is not None and list(row["vetoes"][:len(prev["vetoes"])]) != list(prev["vetoes"])):
        no("vetoes_not_tightening")
    try:
        release_n(row["release"])
    except ReviewError:
        no("bad_release_id")
    if not isinstance(row["trigger_ids"], list) or not all(isinstance(i, str) and ag.RG_ID.fullmatch(i) for i in row["trigger_ids"]):
        no("trigger_ids")
    if prev is None:
        try:
            check_clock(row["effective_from_ms"])
        except ReviewError:
            no("clock_not_a_utc_midnight")
        if row["written_ms"] != row["effective_from_ms"] or row["trigger_ids"]:
            no("genesis_not_v0")
        if row["kind"] != "genesis" or row["version"] != 0 or row["max_input_close_ms"] != 0 or row["regime_rules"] or row["changes"] \
                or (v0_playbook_sha256 and row["playbook_sha256"] != v0_playbook_sha256):
            no("genesis_not_v0")
        return
    if row["kind"] == "genesis":
        no("second_genesis")
    day = row["review_day"]
    cutoff = day_start_ms(day)
    if row["cutoff_ms"] != cutoff:
        no("cutoff_not_review_day")
    if row["version"] != prev["version"] + 1:
        no("version_gap")
    if not isinstance(row["max_input_close_ms"], int) or row["max_input_close_ms"] >= cutoff or row["max_input_close_ms"] > cutoff + FREEZE_MS:
        no("input_after_cutoff")
    if not isinstance(row["written_ms"], int) or row["written_ms"] < cutoff:
        no("written_before_cutoff")
    if row["written_ms"] < prev["written_ms"]:
        no("written_backdated")                              # R6: written_ms never decreases along the chain
    if len(row["changes"]) > params.max_changes:
        no("too_many_changes")
    if row["block_sha256"] is not None:
        if not spec.regime_review or not isinstance(row["block_as_of_ms"], int) or row["block_as_of_ms"] >= row["written_ms"]:
            no("block_not_before_write")                      # R6: block as_of_ms strictly before the write instant
    elif row["block_as_of_ms"] is not None:
        no("block_fields")
    last_nightly = next((m for m in reversed(history) if m["kind"] == "nightly"), None)
    if row["kind"] == "nightly":
        if row["intraday_n"] != 0:
            no("intraday_n")
        if last_nightly is not None and day <= last_nightly["review_day"]:
            no("review_day_not_increasing")
        if row["effective_from_ms"] != max(cutoff + EFFECTIVE_OFFSET_MS, row["written_ms"] + APPEND_MARGIN_MS):
            no("effective_from")
        if row["trigger_ids"]:
            no("trigger_ids")
        if row["playbook_header_sha256"] != prev["playbook_header_sha256"]:
            no("frozen_header_changed")
    else:                                                     # intraday
        # a missed nightly line does not block the day's intraday review (the glue logs it); the day only may not go backwards
        if (last_nightly is not None and day < last_nightly["review_day"]) or (prev["review_day"] is not None and day < prev["review_day"]):
            no("intraday_day_backwards")
        used = {i for m in history for i in m["trigger_ids"]}
        if not row["trigger_ids"] or used & set(row["trigger_ids"]) or len(set(row["trigger_ids"])) != len(row["trigger_ids"]):
            no("intraday_trigger")                             # DESIGN s3: only a NEW direct HIGH event triggers one
        n_same = sum(1 for m in history if m["kind"] == "intraday" and m["review_day"] == day)
        if row["intraday_n"] != n_same + 1 or row["intraday_n"] > MAX_INTRADAY_PER_DAY:
            no("intraday_cap")
        if row["block_sha256"] is None:
            no("intraday_without_block")
        if row["effective_from_ms"] != row["written_ms"] + EFFECTIVE_OFFSET_MS:
            no("effective_from")
        if any(row[k] != prev[k] for k in ("playbook_sha256", "knobs", "vetoes")):
            no("intraday_changed_permanent_state")
        if any(c.get("type") != ag.REGIME_TYPE for c in row["changes"]):
            no("intraday_non_regime_change")
    # regime rules: all live at the write instant, all of the previous unexpired ones carried, none with a bad shape
    rules = row["regime_rules"]
    if not isinstance(rules, list) or any(not isinstance(r, Mapping) or not isinstance(r.get("expires_ms"), int) or r["expires_ms"] <= row["written_ms"] for r in rules):
        no("regime_rule_expired_or_malformed")
    if any(r.get("ttl_h", 0) > params.ttl_h_max for r in rules):
        no("regime_ttl_over_cap")
    carried = [r for r in prev["regime_rules"] if r["expires_ms"] > row["written_ms"]]
    if [r for r in rules if r in carried] != carried:
        no("regime_rules_dropped_early")
    new = [r for r in rules if r not in carried]
    if [c for c in row["changes"] if c.get("type") == ag.REGIME_TYPE] != new:
        no("regime_rules_not_in_changes")


def load_version(root: str | os.PathLike, arm: str, row: Mapping, *, verify_bundle: bool = True) -> RV.ArmState:
    """The ArmState a manifest line names; its files must hash to the line."""
    _, pdir, bdir = _paths(root, arm)
    base = ag.load_playbook("v0")
    if row["version"] == 0:
        pb = base
    else:
        try:
            text = Path(_guard(pdir / f"v{row['version']}.txt", "r")).read_text(encoding="utf-8")
        except OSError:
            raise ManifestRefused("playbook_file_missing") from None
        try:
            pb = pr.playbook_from_text(ag.PERSONA, f"v{row['version']}", text)
        except pr.PromptError:
            raise ManifestRefused("playbook_unparseable") from None
    if pb.sha256 != row["playbook_sha256"] or pb.header_sha256 != row["playbook_header_sha256"]:
        raise ManifestRefused("playbook_hash")
    if RV.check_playbook_paragraph_rules(pb, base):
        raise ManifestRefused("playbook_paragraph_rules")
    if verify_bundle and row["version"] > 0 and row["bundle_sha256"]:
        stem = row["review_day"] if row["kind"] == "nightly" else f"{row['review_day']}.i{row['intraday_n']}"
        try:
            data = Path(_guard(bdir / f"{stem}.json", "r")).read_bytes()
            audit = Path(_guard(bdir / f"{stem}.audit.json", "r")).read_bytes()
        except OSError:
            raise ManifestRefused("bundle_file_missing") from None
        if sha256_hex(data) != row["bundle_sha256"]:
            raise ManifestRefused("bundle_hash")
        if sha256_hex(audit) != row["audit_sha256"]:
            raise ManifestRefused("audit_hash")
        closes = json.loads(audit).get("closes") or {}
        if any(not isinstance(c, int) or c >= row["cutoff_ms"] for c in closes.values()) or max(closes.values(), default=0) != row["max_input_close_ms"]:
            raise ManifestRefused("input_after_cutoff")
    return RV.ArmState(ag.PERSONA, row["version"], pb, dict(row["knobs"]), tuple(row["vetoes"]))


def verify_chain(root: str | os.PathLike, arm: str, **kw) -> list[dict]:
    """Every line of the arm's manifest file, checked in order (and its files). ManifestRefused on the first bad line."""
    _deny_root(root)
    rows = read_manifests(root, arm)
    v0 = ag.load_playbook("v0").sha256
    for i, row in enumerate(rows):
        check_manifest(row, rows[:i], arm, v0_playbook_sha256=v0, **kw)
        load_version(root, arm, row)
    return rows


@dataclass(frozen=True)
class ActiveVersion:
    arm: str
    manifest: Mapping
    state: RV.ArmState
    regime_rules: tuple[dict, ...]


def active_version(root: str | os.PathLike, arm: str, now_ms: int, **kw) -> ActiveVersion | None:
    """The version in force at now_ms: the last line with effective_from_ms <= now_ms, after the whole chain verifies. Regime rules are those of that
    line that have not expired at now_ms."""
    rows = verify_chain(root, arm, **kw)
    if not rows:
        return None
    live = [r for r in rows if r["effective_from_ms"] <= now_ms]
    if not live:
        raise ManifestRefused("no_version_in_force")
    row = live[-1]
    rules = tuple(r for r in row["regime_rules"] if r["expires_ms"] > now_ms)
    return ActiveVersion(arm, row, load_version(root, arm, row, verify_bundle=False), rules)


def append_manifest(root: str | os.PathLike, arm: str, row: Mapping, playbook_text: str | None, bundle_bytes: bytes | None, audit_bytes: bytes | None = None,
                    *, wall: int | None = None, **kw) -> None:
    """Write the version's files, then append the line under flock, only if it extends the verified chain. Idempotent for the same line.
    R6, "hashed and logged before its first decision": the append instant (wall clock, `wall` in tests) must be strictly before the line's
    effective_from_ms, else ManifestRefused('appended_after_effective'). The genesis line is exempt."""
    _guard(root, "w")
    mpath, pdir, bdir = _paths(root, arm)
    rows = read_manifests(root, arm)
    if any(r["sha256"] == row["sha256"] for r in rows):
        return
    check_manifest(row, rows, arm, **kw)
    if row["kind"] != "genesis" and (wall if wall is not None else wall_ms()) >= row["effective_from_ms"]:
        raise ManifestRefused("appended_after_effective")
    stem = row["review_day"] if row["kind"] == "nightly" else f"{row['review_day']}.i{row['intraday_n']}"
    if playbook_text is not None and row["version"] > 0:
        _write_once(pdir / f"v{row['version']}.txt", playbook_text.encode("utf-8"))
    if bundle_bytes is not None:
        _write_once(bdir / f"{stem}.json", bundle_bytes)
    if audit_bytes is not None:
        _write_once(bdir / f"{stem}.audit.json", audit_bytes)
    _guard(mpath, "w")
    mpath.parent.mkdir(parents=True, exist_ok=True, mode=PERM_DIR)
    fd = os.open(str(mpath), os.O_WRONLY | os.O_APPEND | os.O_CREAT, PERM_FILE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if len(_read_jsonl(mpath)) != len(rows):
            raise ManifestRefused("chain_moved")
        os.write(fd, canon(row).encode() + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


def make_manifest(arm: str, kind: str, day: str, intraday_n: int, prev: Mapping, state: RV.ArmState, bundle: Bundle, result: ChangeResult, *, written_ms: int,
                  commit: str, response_sha256: str | None, review_error: str | None, rules: Sequence[Mapping], skip_pricer: str | None = None,
                  trigger_ids: Sequence[str] = ()) -> dict:
    cutoff = day_start_ms(day)
    eff = max(cutoff + EFFECTIVE_OFFSET_MS, written_ms + APPEND_MARGIN_MS) if kind == "nightly" else written_ms + EFFECTIVE_OFFSET_MS
    row = {"schema": SCHEMA, "kind": kind, "seq": prev["seq"] + 1, "arm": arm, "version": state.version, "review_day": day, "intraday_n": intraday_n, "cutoff_ms": cutoff,
           "written_ms": written_ms, "effective_from_ms": eff, "max_input_close_ms": bundle.max_input_close_ms, "commit": commit, "model": ag.MODEL_ID,
           "reviewer_sha256": prev["reviewer_sha256"], "regime_sha256": prev["regime_sha256"], "release": prev["release"], "params": dict(prev["params"]),
           "params_sha256": prev["params_sha256"], "playbook_sha256": state.playbook.sha256, "playbook_header_sha256": state.playbook.header_sha256,
           "knobs": dict(state.knobs), "knobs_sha256": state.knobs_sha256(), "vetoes": list(state.vetoes), "vetoes_sha256": state.vetoes_sha256(),
           "regime_rules": [dict(r) for r in rules], "block_sha256": bundle.block_sha256, "block_as_of_ms": bundle.block_as_of_ms, "trigger_ids": list(trigger_ids),
           "bundle_sha256": bundle.sha256,
           "audit_sha256": bundle.audit_sha256, "request_sha256": bundle.request.request_sha256(), "response_sha256": response_sha256,
           "changes": [dict(c) for c in result.accepted], "rejected": [[i, c] for i, c in result.rejected], "reply_error": result.reply_error,
           "review_error": review_error, "shown_ids": list(bundle.shown_ids), "truncation": {**bundle.dropped, "request_bytes": bundle.request_bytes},
           "skip_pricer": skip_pricer, "prev_manifest_sha256": prev["sha256"]}
    row["sha256"] = manifest_hash(row)
    return row


# ---------------------------------------------------------------------------------------------------------------------------------------- sealed side files
def _seal_write(root: str | os.PathLike, rel: str, data: str) -> None:
    """Replies, validator results: under the HV6 root only, write-once, never printed."""
    _write_once(Path(root) / rel, data.encode("utf-8"))


def _call(model: Any, req: pr.Request, now_ms: int) -> tuple[str, str | None, str | None]:
    """(reply text, response sha, error code). Error, refusal, truncation and empty text are abstains (text is then empty)."""
    reply = model.complete(req, now_ms)
    err = reply.error or ("refusal" if getattr(reply, "refused", False) else None)
    if err is None and reply.stop_reason == "max_tokens":
        err = "truncated"
    if err is None and not (reply.text or "").strip():
        err = "empty_reply"
    return ("" if err else reply.text), reply.response_sha256, err


def _next_state(prev_row: Mapping, result: ChangeResult, version: int) -> RV.ArmState:
    st = result.state
    return RV.ArmState(ag.PERSONA, version, pr.playbook_from_text(ag.PERSONA, f"v{version}", st.playbook.text), st.knobs, st.vetoes)


def _records(arm: str, day: str, *, root, ledger_root, t0_ms: int, pricer, params: Params, release: str) -> tuple[list[dict], int]:
    cutoff = day_start_ms(day)
    trades, open_n = load_trade_records(arm, root, ledger_root or default_ledger_root(root), cutoff, t0_ms, release)
    return trades + load_skip_records(arm, root, utc_day(cutoff - DAY_MS), pricer, cutoff, t0_ms), open_n


def review_night(arm: str, day: str, *, root: str | os.PathLike, ledger_root: str | os.PathLike | None, model: Any, now_ms: int, commit: str,
                 pricer: RV.SkipPricer | None = None, records: Sequence[Mapping] | None = None, open_positions: int | None = None,
                 block: Mapping | None = None) -> dict:
    """One arm's nightly review for `day`. Returns the line appended (or the existing nightly line of that day). Model, reply and validator failures
    leave the playbook unchanged and are recorded in the line. A broken chain or an oversize bundle stops the night. `block` (L, P only) must have
    as_of_ms before now_ms; if None, the newest saved block under <root>/regime before now_ms is used."""
    spec = ag.ARMS[arm]
    if not spec.reviewer:
        raise ReviewError("no_reviewer_for_arm")
    cutoff = day_start_ms(day)
    if not isinstance(now_ms, int) or now_ms < cutoff:
        raise ReviewError("review_before_cutoff")
    rows = verify_chain(root, arm, reviewer_sha=reviewer_sha256(), frozen_sha=commit)
    if not rows:
        raise ManifestRefused("no_genesis_manifest")
    for r in rows:
        if r["kind"] == "nightly" and r["review_day"] == day:
            return r
    head = rows[-1]
    if now_ms < head["written_ms"]:
        raise ReviewError("now_backdated")
    params = Params(**head["params"])
    t0 = rows[0]["effective_from_ms"]
    release = rows[0]["release"]
    state = load_version(root, arm, head, verify_bundle=False)
    if records is None:
        records, open_positions = _records(arm, day, root=root, ledger_root=ledger_root, t0_ms=t0, pricer=pricer, params=params, release=release)
    block_bytes = None
    if spec.regime_review and block is None:
        got = latest_block_file(Path(root) / "regime", now_ms)
        block, block_bytes = got if got else (None, None)
    if block is not None and not (isinstance(block.get("as_of_ms"), int) and block["as_of_ms"] < now_ms):
        block = None
    rules = [r for r in head["regime_rules"] if r["expires_ms"] > now_ms]
    shown = {i for m in rows for i in m["shown_ids"]}
    bundle = build_bundle(arm, day, state, records, t0_ms=t0, shown_before=shown, open_positions=open_positions or 0, manifests=rows, block=block,
                          active_rules=rules, params=params)
    text, rsha, err = _call(model, bundle.request, now_ms)
    if err:
        result = ChangeResult((), (), state, None)
    else:
        result = validate_reply(text, state, bundle.evidence, params=params, arm=arm, block=block, now_ms=now_ms, active_rules=rules)
    nv = head["version"] + 1
    nxt = _next_state(head, result, nv)
    carried = [r for r in rules]
    new_rules = [dict(c) for c in result.regime_rules]
    row = make_manifest(arm, "nightly", day, 0, head, nxt, bundle, result, written_ms=now_ms, commit=commit, response_sha256=rsha, review_error=err,
                        rules=carried + new_rules, skip_pricer=(getattr(pricer, "name", type(pricer).__name__) if pricer is not None else "none"))
    append_manifest(root, arm, row, nxt.playbook.text, bundle.text.encode("utf-8"), bundle.audit_text.encode("utf-8"))
    _seal_write(root, f"replies/{arm}/{day}.nightly.txt", text)
    _seal_write(root, f"validator/{arm}/{day}.nightly.json", canon({"accepted": [c for c in result.accepted], "rejected": [[i, c] for i, c in result.rejected],
                                                                      "reply_error": result.reply_error, "review_error": err}))
    if block_bytes is not None and bundle.block_sha256 is not None:
        _write_once(_paths(root, arm)[2] / f"{day}.block.json", block_bytes)       # the exact bytes read (byte copy)
    return row


def regime_review(arm: str, day: str, *, root: str | os.PathLike, ledger_root: str | os.PathLike | None, model: Any, now_ms: int, commit: str, block: Mapping,
                  records: Sequence[Mapping] | None = None, pricer: RV.SkipPricer | None = None, block_bytes: bytes | None = None) -> dict:
    """One intraday regime review (L or P). Allowed only on a block with a NEW direct HIGH event (DESIGN s3; a manual call would be a review timed by a
    person, R8): `no_new_direct_high` otherwise, and the trigger ids are stored in the line. Cap 2 per arm per day. A missed nightly line does not
    block it (the day's nightly may still run later). The call uses MAX_TOKENS['regime'] (1200); a truncated reply is an abstain (the line is still
    written, with review_error). Residual: the validator's evidence set holds this day's records, which the intraday reviewer is not shown."""
    spec = ag.ARMS[arm]
    if not spec.regime_review:
        raise ReviewError("no_regime_review_for_arm")
    cutoff = day_start_ms(day)
    if not isinstance(now_ms, int) or now_ms < cutoff:
        raise ReviewError("review_before_cutoff")
    if not (isinstance(block, Mapping) and isinstance(block.get("as_of_ms"), int) and block["as_of_ms"] < now_ms):
        raise ReviewError("block_not_before_write")
    rows = verify_chain(root, arm, reviewer_sha=reviewer_sha256(), regime_sha=regime_template_sha256(), frozen_sha=commit)
    head = rows[-1]
    if now_ms < head["written_ms"]:
        raise ReviewError("now_backdated")
    used = [i for r in rows for i in r["trigger_ids"]]
    trig = new_direct_high(block, used)
    if not trig:
        raise ReviewError("no_new_direct_high")
    if any(r["kind"] == "nightly" and r["review_day"] == day for r in rows) is False:
        log_event(root, {"kind": "nightly_missing_for_intraday", "arm": arm, "day": day, "ms": now_ms})
    n = sum(1 for r in rows if r["kind"] == "intraday" and r["review_day"] == day) + 1
    if n > MAX_INTRADAY_PER_DAY:
        raise ReviewError("intraday_cap")
    params = Params(**head["params"])
    t0 = rows[0]["effective_from_ms"]
    state = load_version(root, arm, head, verify_bundle=False)
    if records is None:
        records, _ = _records(arm, day, root=root, ledger_root=ledger_root, t0_ms=t0, pricer=pricer, params=params, release=rows[0]["release"])
    rules = [r for r in head["regime_rules"] if r["expires_ms"] > now_ms]
    bundle = build_regime_bundle(arm, day, n, state, records, block, t0_ms=t0, manifests=rows, active_rules=rules, params=params)
    text, rsha, err = _call(model, bundle.request, now_ms)
    if err:
        result = ChangeResult((), (), state, None)
    else:
        result = validate_reply(text, state, bundle.evidence, params=params, arm=arm, block=block, now_ms=now_ms, active_rules=rules, only_regime=True)
    nv = head["version"] + 1
    nxt = RV.ArmState(ag.PERSONA, nv, pr.playbook_from_text(ag.PERSONA, f"v{nv}", state.playbook.text), state.knobs, state.vetoes)
    row = make_manifest(arm, "intraday", day, n, head, nxt, bundle, result, written_ms=now_ms, commit=commit, response_sha256=rsha, review_error=err,
                        rules=rules + [dict(c) for c in result.regime_rules], trigger_ids=trig)
    append_manifest(root, arm, row, nxt.playbook.text, bundle.text.encode("utf-8"), bundle.audit_text.encode("utf-8"))
    _seal_write(root, f"replies/{arm}/{day}.intraday{n}.txt", text)
    _seal_write(root, f"validator/{arm}/{day}.intraday{n}.json", canon({"accepted": list(result.accepted), "rejected": [[i, c] for i, c in result.rejected],
                                                                          "reply_error": result.reply_error, "review_error": err}))
    if block_bytes is not None:
        _write_once(_paths(root, arm)[2] / f"{day}.i{n}.block.json", block_bytes)
    return row


def make_regime_review_fn(*, root: str | os.PathLike, ledger_root: str | os.PathLike | None = None, model: Any, commit: str,
                          pricer: RV.SkipPricer | None = None) -> Callable[[str, Mapping | None, int], Any]:
    """Glue for the runner's `regime_review_fn(arm, block, T)` (tools/hv6_runner.RegimeChannel hands over only the block and the tick instant T).
    The day is the UTC day of T. A missing nightly line for that day does NOT block the review: it is logged to <root>/logs/review-events.jsonl and the
    review goes on. A block of None (an arm without one) is logged and skipped. Any other refusal is logged with its fixed code and re-raised, so the
    runner's own error line (exception type) still fires."""
    def fn(arm: str, block: Mapping | None, T: int) -> Any:
        day = utc_day(T)
        if block is None:
            log_event(root, {"kind": "regime_review_skipped_no_block", "arm": arm, "day": day, "ms": T})
            return None
        try:
            return regime_review(arm, day, root=root, ledger_root=ledger_root, model=model, now_ms=T, commit=commit, block=block, pricer=pricer)
        except ReviewError as e:
            log_event(root, {"kind": "regime_review_refused", "arm": arm, "day": day, "ms": T, "code": str(e)})
            raise
    return fn


# ---------------------------------------------------------------------------------------------------------------------------------------- printable summary
def decision_log_sha256(root: str | os.PathLike, day: str) -> str:
    """READING: sha256 over, for every arm in sorted order, the sha256 of that arm's decisions/<arm>/<day>.jsonl (or 'absent'). The one decision-log
    figure the self-seal lets the nightly print."""
    parts = []
    for arm in sorted(ag.ARMS):
        f = Path(_guard(Path(root) / "decisions" / arm / f"{day}.jsonl", "r"))
        parts.append(f"{arm}:{sha256_hex(f.read_bytes()) if f.is_file() else 'absent'}")
    return sha256_hex("|".join(parts))


def printable(root: str | os.PathLike, day: str) -> dict:
    """The ONLY nightly output: per reviewer arm the manifests' sha256 chain, and the decision-log sha256. Nothing else is printed (G3)."""
    return {"manifest_chain": {a: [r["sha256"] for r in read_manifests(root, a)] for a in ag.reviewer_arms()}, "decision_log_sha256": decision_log_sha256(root, day)}


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _main(argv)
    except ReviewError as e:
        print(canon({"refused": str(e) or type(e).__name__}), file=sys.stderr)
        return 1


def _main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV6 nightly reviewer and regime review: paper only. Prints only the manifest sha256 chain and the decision-log sha256.")
    ap.add_argument("cmd", choices=("genesis", "run", "regime", "verify"))
    ap.add_argument("--root", default=HV6_ROOT)
    ap.add_argument("--ledger-root", default=None, help="engine ledger dir; default <root>/engine-ledger (the runner's default)")
    ap.add_argument("--arm", required=True, choices=ag.reviewer_arms())
    ap.add_argument("--day", default=None)
    ap.add_argument("--commit", default="")
    ap.add_argument("--clock-start-ms", type=int, default=None, help="genesis only, required: the registered T0 (a 00:00Z boundary); there is no default")
    ap.add_argument("--now-ms", type=int, default=None)
    ap.add_argument("--release", default=DEFAULT_RELEASE)
    ap.add_argument("--block", default=None, help="path of a saved block-<as_of_ms>.json (regime cmd; run uses the newest under <root>/regime)")
    ap.add_argument("--stub", action="store_true", help="StubModel: no API call, no key read, every reply is []")
    a = ap.parse_args(argv)
    wall = wall_ms()
    now = a.now_ms if a.now_ms is not None else wall
    if a.now_ms is not None and not a.stub and abs(a.now_ms - wall) > NOW_SLACK_MS:
        raise ReviewError("now_ms_not_wall_clock")          # outside test mode (--stub) the instant is the wall clock +-60 s: no backdating
    day = a.day or utc_day(now)
    if not COMMIT_RE.fullmatch(a.commit.lower()):
        ap.error("--commit must be the frozen git sha (7 to 40 hex chars)")
    _deny_root(a.root)
    if a.cmd == "genesis":
        if a.clock_start_ms is None:
            ap.error("--clock-start-ms is required for genesis")
        row, _ = genesis_manifest(a.arm, a.clock_start_ms, a.commit, release=a.release)
        append_manifest(a.root, a.arm, row, None, None)
    elif a.cmd == "verify":
        verify_chain(a.root, a.arm, reviewer_sha=reviewer_sha256(), frozen_sha=a.commit)
    else:
        guard = ag.BudgetGuard(a.root)
        model = ag.StubModel(lambda req: "[]", guard=guard) if a.stub else ag.CallerModel(guard)
        if a.cmd == "run":
            review_night(a.arm, day, root=a.root, ledger_root=a.ledger_root, model=model, now_ms=now, commit=a.commit)
        else:
            if not a.block:
                ap.error("--block is required for regime")
            block, raw = read_block_file(a.block, root=a.root)       # the runner's block-file checks, the file must be in <root>/regime/
            regime_review(a.arm, day, root=a.root, ledger_root=a.ledger_root, model=model, now_ms=now, commit=a.commit, block=block, block_bytes=raw)
    print(canon(printable(a.root, utc_day(day_start_ms(day) - DAY_MS))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
