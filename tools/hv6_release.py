#!/usr/bin/env python3
"""hv6_release: release tooling of HV6 (build item 5 of docs/research/hv6/DESIGN.md; section 7, T10). PAPER only; no network, no model call.

A release = one frozen bundle (code sha, reviewer prompt sha, validator thresholds = hv6_review.Params, bundle spec, regime-block spec, playbook seed),
forward-registered under its own bot_ids with its own clock. This module builds, checks and writes the release record and refuses what DESIGN s7 forbids.

Rules enforced here (each one a refusal code, never a warning):
  1. No v6.(N+1) clock starts before v6.N's comparison window ends (clock >= prev clock + D days, D = 12 by default).          window_not_over
  2. (V2-3) v6.(N+1) is registered only if v6.0's F and X keep running through its window; the caller states when they run until.   fx_not_continuing
     No F or X copy is ever registered by a release (a second frozen copy is forbidden, DEC-031 Am.3 item 5).                         no_new_fx_copies
     v6.0 itself registers all five arms; it is not blocked by this rule.
  3. A release registers ONLY the arms whose procedure changed (L, L-nr, P as applicable): reviewer prompt sha changes touch all three, a regime
     prompt / block-spec change touches L and P, a params change touches all three. An empty set or a wrong arm list is refused.      arms_not_changed_set
  4. Release inputs are non-outcome metrics only (invalid-reply rate, schema/bounds rejects, token cost, latency, block coverage). The whitelist is
     closed: P&L, any arm gap, "effect of claimed sign not met" rejects and the change-acceptance rate are refused by name, and so is any unlisted key.
                                                                                                                                        outcome_metric_refused
     The changeable registry is hv6_review.REGISTRY (min-match in {8, 10, 15}, skip count, max changes/night, TTL <= 72 h, evidence window). Anything
     else is a human-written PR with QP review: a params value outside the registry is refused.                                         params_not_in_registry
  5. A data-only release (same code sha) needs a manifest pin (64 hex); a code change needs the md5 decision-equivalence replay proof (32 hex).
Self-seal: metric values and per-arm results are written only in the release record under the HV6 root; the CLI prints only the record's sha256.
"""
from __future__ import annotations

if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import argparse
import json
import math
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import hv6_agents as ag
from tools import hv6_regime_block as rb
from tools import hv6_review as rv
from tools.hv4_review import DAY_MS, GENESIS, ReviewError, canon, sha256_hex

SCHEMA = "hv6-release-1"
WINDOW_DAYS = 12                                   # DESIGN s4/s7: D = 12
CHANGEABLE_ARMS = ("L", "L-nr", "P")
ALL_ARMS = tuple(ag.ARMS)
ALLOWED_METRICS = ("invalid_reply_rate", "schema_rejects", "bounds_rejects", "token_cost_usd", "latency_ms_p50", "latency_ms_p95", "block_coverage")
# reject codes that exist independent of any outcome (fixed grammar and bounds checks). Everything else (the 10-match rule, no_contrast, effect_sign_mismatch,
# duplicate_target against past changes) depends on outcomes and never enters a release input.
SCHEMA_CODES = frozenset({"bad_keys", "bad_type", "bad_claim", "bad_why", "bad_evidence_ids", "unknown_evidence_id", "bad_target", "bad_new", "bad_paragraph",
                          "bad_or_frozen_target", "neutral_field", "not_json", "not_an_array", "item_not_object", "empty_reply", "reply_too_long",
                          "too_many_changes", "veto_claim_must_be_worse", "veto_differs_from_predicate", "bad_ttl", "bad_keys"})
BOUNDS_CODES = frozenset({"knob_out_of_bounds", "knob_unknown", "knob_not_number", "knob_not_integer", "probe_stake_not_0.25", "probe_extra_entries",
                          "probe_more_than_one_step", "ttl_over_release_cap", "veto_cap", "block_unusable", "block_not_before_write"})
OUTCOME_NAME = re.compile(r"pnl|p_l|profit|net|sol|gap|effect|accept|sign|win|loss|ret|edge|diff|outcome", re.I)
RELEASE_RE = re.compile(r"v6\.(0|[1-9][0-9]?)")
SHA64 = re.compile(r"[0-9a-f]{64}")
MD5 = re.compile(r"[0-9a-f]{32}")


class ReleaseRefused(ReviewError):
    """A release this tool will not write. The message is a fixed code."""


def _no(code: str):
    raise ReleaseRefused(code)


def release_number(release: str) -> int:
    m = RELEASE_RE.fullmatch(release or "")
    if not m:
        _no("bad_release_id")
    return int(m.group(1))


def bot_ids(release: str, arms: Sequence[str]) -> dict[str, str]:
    """v6.0 -> hv6-<arm> (the ledger name the reviewer reads); v6.N -> hv6r<N>-<arm>."""
    n = release_number(release)
    return {a: (f"hv6-{a}" if n == 0 else f"hv6r{n}-{a}") for a in arms}


def release_root(root: str | Path, release: str) -> Path:
    return Path(root) if release_number(release) == 0 else Path(root) / "releases" / release


def bundle_spec_sha256() -> str:
    """The bundle spec as code: schema names, size and count limits. A change to any is a new sha."""
    return sha256_hex(canon({"review": "hv6-review-bundle-1", "regime": "hv6-regime-bundle-1", "request_max_bytes": rv.REQUEST_MAX_BYTES,
                             "freeze_ms": rv.FREEZE_MS, "max_intraday": rv.MAX_INTRADAY_PER_DAY, "effective_offset_ms": rv.EFFECTIVE_OFFSET_MS,
                             "max_tokens": {"review": ag.MAX_TOKENS["review"], "regime": ag.MAX_TOKENS["regime"]}}))


def regime_spec_sha256() -> str:
    """The regime-block spec: the block schema plus the sha256 of the regime-review instructions."""
    return sha256_hex(canon({"schema": rb.BLOCK_SCHEMA, "days_schema": rb.DAYS_SCHEMA, "regime_review": rv.regime_template_sha256()}))


def procedure(params: rv.Params) -> dict:
    """What an arm's procedure is, per arm: the hash inputs that decide whether the arm 'changed'."""
    base = {"reviewer": rv.reviewer_sha256(), "params": params.sha256(), "bundle": bundle_spec_sha256(), "playbook_seed": ag.load_playbook("v0").sha256}
    return {"L": {**base, "regime": regime_spec_sha256()}, "P": {**base, "regime": regime_spec_sha256()}, "L-nr": dict(base)}


def check_metrics(metrics: Any) -> dict:
    """The closed whitelist of release inputs: finite non-negative numbers only. Outcome-looking or unlisted names are refused."""
    if not isinstance(metrics, Mapping):
        _no("metrics_not_object")
    out = {}
    for k, v in metrics.items():
        if not isinstance(k, str) or k not in ALLOWED_METRICS:
            _no("outcome_metric_refused" if isinstance(k, str) and OUTCOME_NAME.search(k) else "metric_not_whitelisted")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            _no("metric_not_a_number")
        out[k] = v
    return out


def metrics_from_manifests(rows: Sequence[Mapping]) -> dict:
    """Non-outcome metrics from one reviewer arm's manifest lines: invalid-reply rate (reply_error or review_error), counts of rejects that are schema or
    bounds codes (outcome-dependent codes are skipped by construction), and block coverage (share of nightly/intraday lines that carry a block)."""
    lines = [r for r in rows if r.get("kind") in ("nightly", "intraday")]
    n = len(lines)
    bad = sum(1 for r in lines if r.get("reply_error") or r.get("review_error"))
    codes = [c for r in lines for _i, c in r.get("rejected", [])]
    out = {"invalid_reply_rate": (bad / n) if n else 0.0, "schema_rejects": sum(1 for c in codes if c in SCHEMA_CODES),
           "bounds_rejects": sum(1 for c in codes if c in BOUNDS_CODES)}
    if any(r.get("regime_sha256") for r in rows):
        out["block_coverage"] = (sum(1 for r in lines if r.get("block_sha256")) / n) if n else 0.0
    return out


def changed_arms(prev: Mapping | None, params: rv.Params) -> tuple[str, ...]:
    """The arms whose procedure differs from the previous release's (all three on a first release after v6.0 only if something changed)."""
    cur = procedure(params)
    if prev is None:
        return CHANGEABLE_ARMS
    old = prev["procedure"]
    return tuple(a for a in CHANGEABLE_ARMS if cur[a] != old.get(a))


def record_hash(rec: Mapping) -> str:
    return sha256_hex(canon({k: v for k, v in rec.items() if k != "sha256"}))


def build_release(release: str, *, commit: str, clock_ms: int, written_ms: int, params: rv.Params = rv.V60_PARAMS, prev: Mapping | None = None,
                  arms: Sequence[str] | None = None, metrics: Mapping | None = None, fx_run_until_ms: int | None = None, window_days: int = WINDOW_DAYS,
                  manifest_pin: str | None = None, replay_md5: str | None = None) -> dict:
    """The release record, or ReleaseRefused(code). `prev` is the previous release's record (None for v6.0)."""
    n = release_number(release)
    if not rv.COMMIT_RE.fullmatch(str(commit).lower()):
        _no("commit_format")
    if not (isinstance(clock_ms, int) and isinstance(written_ms, int)) or written_ms >= clock_ms:
        _no("record_not_before_clock")
    metrics = check_metrics(metrics or {})
    if n == 0:
        if prev is not None:
            _no("v60_has_no_prev")
        reg = ALL_ARMS
        if arms is not None and tuple(arms) != reg:
            _no("v60_registers_all_five")
    else:
        if prev is None or release_number(prev["release"]) != n - 1:
            _no("prev_release_missing")
        if prev["sha256"] != record_hash(prev):
            _no("prev_hash")
        window_end = prev["clock_ms"] + prev["window_days"] * DAY_MS
        if clock_ms < window_end:
            _no("window_not_over")
        # V2-3: v6.0's F and X must keep running through THIS release's window (clock + D)
        if not isinstance(fx_run_until_ms, int) or isinstance(fx_run_until_ms, bool) or fx_run_until_ms < clock_ms + window_days * DAY_MS:
            _no("fx_not_continuing")
        if arms is not None and any(a in ("F", "X") for a in arms):
            _no("no_new_fx_copies")
        reg = changed_arms(prev, params)
        if not reg:
            _no("arms_not_changed_set")
        if arms is not None and tuple(arms) != reg:
            _no("arms_not_changed_set")
        data_only = str(commit).lower() == str(prev["commit"]).lower()
        if data_only and not (isinstance(manifest_pin, str) and SHA64.fullmatch(manifest_pin)):
            _no("data_only_needs_manifest_pin")
        if not data_only and not (isinstance(replay_md5, str) and MD5.fullmatch(replay_md5)):
            _no("code_change_needs_md5_replay")
    rec = {"schema": SCHEMA, "release": release, "seq": n, "commit": str(commit).lower(), "clock_ms": clock_ms, "written_ms": written_ms,
           "window_days": window_days, "params": asdict(params), "params_sha256": params.sha256(), "reviewer_sha256": rv.reviewer_sha256(),
           "regime_spec_sha256": regime_spec_sha256(), "bundle_spec_sha256": bundle_spec_sha256(), "playbook_seed_sha256": ag.load_playbook("v0").sha256,
           "procedure": procedure(params), "arms": list(reg), "bot_ids": bot_ids(release, reg), "metrics": metrics,
           "fx_run_until_ms": fx_run_until_ms if n else None, "manifest_pin": manifest_pin if n else None, "replay_md5": replay_md5 if n else None,
           "prev_release_sha256": GENESIS if prev is None else prev["sha256"]}
    rec["sha256"] = record_hash(rec)
    return rec


def read_release(root: str | Path, release: str) -> dict | None:
    f = Path(root) / "release-records" / f"{release}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def write_release(root: str | Path, rec: Mapping) -> Path:
    """Write-once under the HV6 root, then create the genesis manifest of every reviewer arm the release registers (F and X have none)."""
    rv._deny_root(root)
    if rec.get("sha256") != record_hash(rec):
        _no("record_hash")
    path = Path(root) / "release-records" / f"{rec['release']}.json"
    rv.RV._write_once(path, canon(rec).encode("utf-8"))
    params = rv.Params(**rec["params"])
    for arm in rec["arms"]:
        if ag.ARMS[arm].reviewer:
            row, _ = rv.genesis_manifest(arm, rec["clock_ms"], rec["commit"], release=rec["release"], params=params)
            rv.append_manifest(release_root(root, rec["release"]), arm, row, None, None)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _main(argv)
    except ReviewError as e:
        print(canon({"refused": str(e) or type(e).__name__}), file=sys.stderr)
        return 1


def _main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HV6 release record: paper only. Prints only the record sha256.")
    ap.add_argument("--root", default=rv.HV6_ROOT)
    ap.add_argument("--release", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--clock-ms", type=int, required=True)
    ap.add_argument("--written-ms", type=int, required=True)
    ap.add_argument("--params-json", default="{}", help="registry values to change, e.g. '{\"min_matches\": 15}'")
    ap.add_argument("--metrics-json", default="{}")
    ap.add_argument("--fx-run-until-ms", type=int, default=None)
    ap.add_argument("--manifest-pin", default=None)
    ap.add_argument("--replay-md5", default=None)
    a = ap.parse_args(argv)
    n = release_number(a.release)
    prev = read_release(a.root, f"v6.{n - 1}") if n else None
    params = rv.Params(**{**asdict(rv.V60_PARAMS), **json.loads(a.params_json)})
    rec = build_release(a.release, commit=a.commit, clock_ms=a.clock_ms, written_ms=a.written_ms, params=params, prev=prev, metrics=json.loads(a.metrics_json),
                        fx_run_until_ms=a.fx_run_until_ms, manifest_pin=a.manifest_pin, replay_md5=a.replay_md5)
    write_release(a.root, rec)
    print(canon({"release": rec["release"], "sha256": rec["sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
