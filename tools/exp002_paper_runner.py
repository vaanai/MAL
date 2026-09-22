#!/usr/bin/env python3
"""EXP-002 / EXP-002b / EXP-002c evaluate->runner — rules-only filter + paper outcomes (local JSONL).

Offline CLI: sealed ingest JSONL only. No RPC, no live capital, no paid APIs.
Full detect book: every eligible bonding create gets evaluate label + outcome marks.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence, TextIO

from tools.exp001_mislabel import (
    LoadedRow,
    load_jsonl_files,
    t_ws_missing,
)
from observe.regime import canonical_tx_type
from tools.marks import is_as_of_tick, is_outcome_mark, parse_mark_tick

DEFAULT_SEED = 1
DEFAULT_OUTPUT_DIR = Path("data/observe")
DEFAULT_PREFIX_V0 = "_exp002"
DEFAULT_PREFIX_V1 = "_exp002b"
DEFAULT_PREFIX_V2 = "_exp002c"

RulesVersion = Literal["v0", "v1", "v2"]
MAX_RANDOM_SIGNATURE_SAMPLES = 20

# EXP-002b defaults — tuned for ~5-30% runners on sealed bonding creates (local calibration).
RULES_V1_DEFAULTS: dict[str, float | bool] = {
    "exclude_bonk_pool": True,
    "require_metadata": True,
    "reject_zero_creator_buy": True,
    "min_sol_amount": 0.5,
    "max_sol_amount": 2.5,
    "min_market_cap_sol": 28.0,
    "max_market_cap_sol": 34.0,
    "min_v_sol_in_bonding_curve": 29.0,
    "max_v_sol_in_bonding_curve": 33.5,
    "min_initial_buy": 1.0,
    "max_initial_buy": 900_000_000.0,
}

# EXP-002c — drop v1 sweet-spot bands; integrity floors + extreme-high cap only.
RULES_V2_DEFAULTS: dict[str, float | bool] = {
    "exclude_bonk_pool": True,
    "require_metadata": True,
    "reject_zero_creator_buy": True,
    "require_price_proxy": True,
    "min_sol_amount": 0.25,
    "min_market_cap_sol": 20.0,
    "extreme_max_market_cap_sol": 50.0,
    "min_initial_buy": 1.0,
}

EvaluateLabel = Literal["runner", "reject"]
GateResult = Literal["PASS", "FAIL", "INCOMPLETE"]

# Council lock — horizons from DEC-006 / EXP-002 (seconds from evaluate T = t_ws).
HORIZON_SECONDS: dict[str, int] = {
    "1s": 1,
    "5s": 5,
    "15s": 15,
    "30s": 30,
    "60s": 60,
    "+2s": 2,
    "+10s": 10,
    "+5m": 300,
}
PEAK_DRAWDOWN_WINDOW_S = 300

PRIMARY_HORIZON = "60s"
MIN_PRICED_FOR_KILL = 10
LIFT_EPSILON = 1e-9
PARITY_REL_TOLERANCE = 0.05


def _json_load_object(line: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def is_bonding_create(row: Mapping[str, Any]) -> bool:
    """Population v0: ingest_hot create with stage=bonding."""
    if row.get("type") != "ingest_hot":
        return False
    tx = row.get("txType")
    if tx in (None, "", "UNK"):
        payload = row.get("ws_payload")
        if isinstance(payload, dict):
            tx = payload.get("txType")
    if canonical_tx_type(tx) != "create":
        return False
    return row.get("stage") == "bonding"


def _parse_iso_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _sealed_field(row: Mapping[str, Any], key: str) -> Any:
    if key in row:
        return row.get(key)
    payload = row.get("ws_payload")
    if isinstance(payload, dict) and key in payload:
        return payload.get(key)
    return None


def _sealed_float(row: Mapping[str, Any], key: str) -> float | None:
    val = _sealed_field(row, key)
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _non_empty_str(val: Any) -> bool:
    return isinstance(val, str) and bool(val.strip())


def _price_proxy_from_row(row: Mapping[str, Any]) -> float | None:
    """Knowable price proxy from sealed row only (no synthesis)."""
    for key in ("marketCapSol", "vSolInBondingCurve"):
        val = row.get(key)
        if val is None and isinstance(row.get("ws_payload"), dict):
            val = row["ws_payload"].get(key)
        if val is None:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f) and f > 0:
            return f
    return None


@dataclass(frozen=True)
class PriceMark:
    t: datetime
    price: float
    source_path: str
    line_no: int


def build_mint_price_series(
    loaded: Sequence[LoadedRow],
) -> dict[str, list[PriceMark]]:
    """Price ticks from later ingest rows and/or EXP-003 outcome_mark side files.

    Outcome marks use ``t_mark`` (not create ``t_ws``). Create rows at T remain in
    the series for entry but are excluded from horizon join (see ``is_as_of_tick``).
    """
    by_mint: dict[str, list[PriceMark]] = {}
    for item in loaded:
        row = item.row
        if is_outcome_mark(row):
            parsed = parse_mark_tick(row)
            if parsed is None:
                continue
            mint, t, price = parsed
        else:
            mint = row.get("mint")
            if not isinstance(mint, str) or mint in ("", "UNK"):
                continue
            price = _price_proxy_from_row(row)
            if price is None:
                continue
            t = _parse_iso_ts(row.get("t_ws"))
            if t is None:
                continue
        by_mint.setdefault(mint, []).append(
            PriceMark(
                t=t,
                price=price,
                source_path=item.source_path,
                line_no=item.line_no,
            )
        )
    for marks in by_mint.values():
        marks.sort(key=lambda m: (m.t, m.line_no))
    return by_mint


def _last_mark_as_of(
    marks: Sequence[PriceMark],
    t0: datetime,
    horizon_s: float,
) -> PriceMark | None:
    """Last tick with T < t_mark <= T+H (EXP-003 honesty; no future leak)."""
    chosen: PriceMark | None = None
    for m in marks:
        if is_as_of_tick(t0, m.t, horizon_s):
            if chosen is None or m.t >= chosen.t:
                chosen = m
    return chosen


def _marks_in_window(
    marks: Sequence[PriceMark],
    t0: datetime,
    t_end: datetime,
) -> list[PriceMark]:
    return [m for m in marks if t0 <= m.t <= t_end]


def knowable_at_t_honest(row: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """v0 honesty: no verified flags without WS evidence on sealed packet."""
    reasons: list[str] = []
    kat = row.get("knowable_at_t")
    if not isinstance(kat, dict):
        return False, ["missing_knowable_at_t"]
    payload = row.get("ws_payload") if isinstance(row.get("ws_payload"), dict) else {}

    if kat.get("quote_verified") is True and not payload.get("quote_mint"):
        reasons.append("quote_verified_without_ws_quote_mint")
    instr = kat.get("instr")
    if instr not in (None, "", "pending_rpc", "UNK"):
        reasons.append("instr_not_pending_rpc_on_hot_create")
    if kat.get("venue_verified") is True:
        reasons.append("venue_verified_on_unverified_hot_row")
    if kat.get("creator_verified") is True and not payload.get("traderPublicKey"):
        reasons.append("creator_verified_without_traderPublicKey")
    if kat.get("fee") not in (None, "", "unverified", "UNK"):
        reasons.append("fee_not_unverified_on_hot_create")
    return (len(reasons) == 0, reasons)


def _bonk_pool_hint(row: Mapping[str, Any]) -> bool:
    """Soft bonk-pool exclude when toggle ON; documented heuristic on sealed fields."""
    pool = row.get("pool")
    if isinstance(pool, str) and "bonk" in pool.lower():
        return True
    payload = row.get("ws_payload")
    if isinstance(payload, dict):
        for key in ("pool", "platform", "launchpad", "raydiumPool"):
            val = payload.get(key)
            if isinstance(val, str) and "bonk" in val.lower():
                return True
        name = str(payload.get("name", "")) + str(payload.get("symbol", ""))
        if "bonk" in name.lower():
            return True
    rid = row.get("regime_id")
    if isinstance(rid, str) and "bonk" in rid.lower():
        return True
    return False


class EvaluateRulesProtocol(Protocol):
    version: RulesVersion

    def evaluate(self, row: Mapping[str, Any]) -> tuple[EvaluateLabel, list[str]]: ...

    def summary_spec(self) -> dict[str, Any]: ...


def _base_identity_rejects(row: Mapping[str, Any]) -> list[str]:
    reject_reasons: list[str] = []
    if row.get("stage") != "bonding":
        reject_reasons.append("stage_not_bonding")
    sig = row.get("signature")
    mint = row.get("mint")
    if not isinstance(sig, str) or sig in ("", "UNK"):
        reject_reasons.append("missing_signature")
    if not isinstance(mint, str) or mint in ("", "UNK"):
        reject_reasons.append("missing_mint")
    honest, honesty_failures = knowable_at_t_honest(row)
    if not honest:
        reject_reasons.extend(honesty_failures)
    return reject_reasons


@dataclass
class EvaluateRulesV0:
    """EXP-002 vacuous-tolerant rules (honesty + optional toggles)."""

    version: RulesVersion = "v0"
    exclude_bonk_pool: bool = False
    min_market_cap_sol: float = 0.0

    def evaluate(self, row: Mapping[str, Any]) -> tuple[EvaluateLabel, list[str]]:
        reject_reasons = _base_identity_rejects(row)
        if self.exclude_bonk_pool and _bonk_pool_hint(row):
            reject_reasons.append("bonk_pool_excluded")
        if self.min_market_cap_sol > 0:
            cap = _price_proxy_from_row(row)
            if cap is None or cap < self.min_market_cap_sol:
                reject_reasons.append("below_min_market_cap_sol")
        if reject_reasons:
            return "reject", reject_reasons
        return "runner", []

    def summary_spec(self) -> dict[str, Any]:
        return {
            "version": "v0",
            "stage": "bonding_only",
            "knowable_at_t_honesty": True,
            "exclude_bonk_pool": self.exclude_bonk_pool,
            "min_market_cap_sol": self.min_market_cap_sol,
        }


# Backward-compatible alias used in tests and v0 runs.
EvaluateRules = EvaluateRulesV0


@dataclass
class EvaluateRulesV1:
    """EXP-002b stricter knowable-at-T bands (default ON bonk exclude + metadata)."""

    version: RulesVersion = "v1"
    exclude_bonk_pool: bool = True
    require_metadata: bool = True
    reject_zero_creator_buy: bool = True
    min_sol_amount: float = float(RULES_V1_DEFAULTS["min_sol_amount"])
    max_sol_amount: float = float(RULES_V1_DEFAULTS["max_sol_amount"])
    min_market_cap_sol: float = float(RULES_V1_DEFAULTS["min_market_cap_sol"])
    max_market_cap_sol: float = float(RULES_V1_DEFAULTS["max_market_cap_sol"])
    min_v_sol_in_bonding_curve: float = float(RULES_V1_DEFAULTS["min_v_sol_in_bonding_curve"])
    max_v_sol_in_bonding_curve: float = float(RULES_V1_DEFAULTS["max_v_sol_in_bonding_curve"])
    min_initial_buy: float = float(RULES_V1_DEFAULTS["min_initial_buy"])
    max_initial_buy: float = float(RULES_V1_DEFAULTS["max_initial_buy"])

    def evaluate(self, row: Mapping[str, Any]) -> tuple[EvaluateLabel, list[str]]:
        reject_reasons = _base_identity_rejects(row)
        if self.exclude_bonk_pool and _bonk_pool_hint(row):
            reject_reasons.append("bonk_pool_excluded")
        if self.require_metadata:
            for key in ("name", "symbol", "uri"):
                if not _non_empty_str(_sealed_field(row, key)):
                    reject_reasons.append(f"missing_metadata_{key}")
        sol_amount = _sealed_float(row, "solAmount")
        initial_buy = _sealed_float(row, "initialBuy")
        if self.reject_zero_creator_buy:
            if sol_amount is not None and sol_amount <= 0:
                reject_reasons.append("zero_sol_amount")
            if initial_buy is not None and initial_buy <= 0:
                reject_reasons.append("zero_initial_buy")
        if sol_amount is not None:
            if sol_amount < self.min_sol_amount:
                reject_reasons.append("below_min_sol_amount")
            if sol_amount > self.max_sol_amount:
                reject_reasons.append("above_max_sol_amount")
        if initial_buy is not None:
            if initial_buy < self.min_initial_buy:
                reject_reasons.append("below_min_initial_buy")
            if initial_buy > self.max_initial_buy:
                reject_reasons.append("above_max_initial_buy")
        cap = _sealed_float(row, "marketCapSol")
        if cap is None:
            reject_reasons.append("missing_market_cap_sol")
        else:
            if cap < self.min_market_cap_sol:
                reject_reasons.append("below_min_market_cap_sol")
            if cap > self.max_market_cap_sol:
                reject_reasons.append("above_max_market_cap_sol")
        v_sol = _sealed_float(row, "vSolInBondingCurve")
        if v_sol is None:
            reject_reasons.append("missing_v_sol_in_bonding_curve")
        else:
            if v_sol < self.min_v_sol_in_bonding_curve:
                reject_reasons.append("below_min_v_sol_in_bonding_curve")
            if v_sol > self.max_v_sol_in_bonding_curve:
                reject_reasons.append("above_max_v_sol_in_bonding_curve")
        if reject_reasons:
            return "reject", reject_reasons
        return "runner", []

    def summary_spec(self) -> dict[str, Any]:
        return {
            "version": "v1",
            "stage": "bonding_only",
            "knowable_at_t_honesty": True,
            "exclude_bonk_pool": self.exclude_bonk_pool,
            "require_metadata": self.require_metadata,
            "reject_zero_creator_buy": self.reject_zero_creator_buy,
            "min_sol_amount": self.min_sol_amount,
            "max_sol_amount": self.max_sol_amount,
            "min_market_cap_sol": self.min_market_cap_sol,
            "max_market_cap_sol": self.max_market_cap_sol,
            "min_v_sol_in_bonding_curve": self.min_v_sol_in_bonding_curve,
            "max_v_sol_in_bonding_curve": self.max_v_sol_in_bonding_curve,
            "min_initial_buy": self.min_initial_buy,
            "max_initial_buy": self.max_initial_buy,
        }


@dataclass
class EvaluateRulesV2:
    """EXP-002c anti-adverse-selection: no narrow mcap/vSol/sol sweet-spot bands."""

    version: RulesVersion = "v2"
    exclude_bonk_pool: bool = True
    require_metadata: bool = True
    reject_zero_creator_buy: bool = True
    require_price_proxy: bool = True
    min_sol_amount: float = float(RULES_V2_DEFAULTS["min_sol_amount"])
    min_market_cap_sol: float = float(RULES_V2_DEFAULTS["min_market_cap_sol"])
    extreme_max_market_cap_sol: float = float(RULES_V2_DEFAULTS["extreme_max_market_cap_sol"])
    min_initial_buy: float = float(RULES_V2_DEFAULTS["min_initial_buy"])

    def evaluate(self, row: Mapping[str, Any]) -> tuple[EvaluateLabel, list[str]]:
        reject_reasons = _base_identity_rejects(row)
        if self.exclude_bonk_pool and _bonk_pool_hint(row):
            reject_reasons.append("bonk_pool_excluded")
        if self.require_metadata:
            for key in ("name", "symbol", "uri"):
                if not _non_empty_str(_sealed_field(row, key)):
                    reject_reasons.append(f"missing_metadata_{key}")
        sol_amount = _sealed_float(row, "solAmount")
        initial_buy = _sealed_float(row, "initialBuy")
        if self.reject_zero_creator_buy:
            if sol_amount is not None and sol_amount <= 0:
                reject_reasons.append("zero_sol_amount")
            if initial_buy is not None and initial_buy <= 0:
                reject_reasons.append("zero_initial_buy")
        if sol_amount is not None and sol_amount < self.min_sol_amount:
            reject_reasons.append("below_min_sol_amount")
        if initial_buy is not None and initial_buy < self.min_initial_buy:
            reject_reasons.append("below_min_initial_buy")
        cap = _sealed_float(row, "marketCapSol")
        v_sol = _sealed_float(row, "vSolInBondingCurve")
        if self.require_price_proxy and cap is None and v_sol is None:
            reject_reasons.append("missing_price_proxy")
        if cap is None:
            reject_reasons.append("missing_market_cap_sol")
        else:
            if cap < self.min_market_cap_sol:
                reject_reasons.append("below_min_market_cap_sol")
            if cap > self.extreme_max_market_cap_sol:
                reject_reasons.append("above_extreme_market_cap_sol")
        if reject_reasons:
            return "reject", reject_reasons
        return "runner", []

    def summary_spec(self) -> dict[str, Any]:
        return {
            "version": "v2",
            "stage": "bonding_only",
            "knowable_at_t_honesty": True,
            "exclude_bonk_pool": self.exclude_bonk_pool,
            "require_metadata": self.require_metadata,
            "reject_zero_creator_buy": self.reject_zero_creator_buy,
            "require_price_proxy": self.require_price_proxy,
            "min_sol_amount": self.min_sol_amount,
            "min_market_cap_sol": self.min_market_cap_sol,
            "extreme_max_market_cap_sol": self.extreme_max_market_cap_sol,
            "min_initial_buy": self.min_initial_buy,
            "dropped_v1_features": [
                "narrow_market_cap_band_28_34",
                "narrow_v_sol_band",
                "max_sol_amount",
                "max_initial_buy",
            ],
        }


def rules_from_cli(
    *,
    version: RulesVersion,
    exclude_bonk_pool: bool | None,
    include_bonk_pool: bool,
    min_market_cap_sol: float | None,
) -> EvaluateRulesProtocol:
    if version == "v0":
        bonk = exclude_bonk_pool if exclude_bonk_pool is not None else False
        cap_min = min_market_cap_sol if min_market_cap_sol is not None else 0.0
        return EvaluateRulesV0(exclude_bonk_pool=bonk, min_market_cap_sol=cap_min)
    if version == "v2":
        bonk = True
        if include_bonk_pool:
            bonk = False
        elif exclude_bonk_pool is not None:
            bonk = exclude_bonk_pool
        cap_min = (
            min_market_cap_sol
            if min_market_cap_sol is not None
            else float(RULES_V2_DEFAULTS["min_market_cap_sol"])
        )
        return EvaluateRulesV2(exclude_bonk_pool=bonk, min_market_cap_sol=cap_min)
    bonk = True
    if include_bonk_pool:
        bonk = False
    elif exclude_bonk_pool is not None:
        bonk = exclude_bonk_pool
    cap_min = (
        min_market_cap_sol
        if min_market_cap_sol is not None
        else float(RULES_V1_DEFAULTS["min_market_cap_sol"])
    )
    return EvaluateRulesV1(exclude_bonk_pool=bonk, min_market_cap_sol=cap_min)


@dataclass
class HorizonOutcome:
    horizon: str
    return_pct: float | None
    status: Literal["ok", "na"]
    mark_t: str | None = None
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "return_pct": self.return_pct,
            "status": self.status,
            "mark_t": self.mark_t,
            "notes": self.notes,
        }


def compute_outcomes(
    *,
    t0: datetime,
    p0: float | None,
    marks: Sequence[PriceMark],
) -> dict[str, Any]:
    horizons: dict[str, dict[str, Any]] = {}
    if p0 is None or p0 <= 0:
        for name in HORIZON_SECONDS:
            horizons[name] = HorizonOutcome(
                horizon=name, return_pct=None, status="na", notes="no_entry_price"
            ).as_dict()
        return {
            "horizons": horizons,
            "peak_return_pct": None,
            "max_drawdown_pct": None,
            "peak_drawdown_status": "na",
            "delta_exec": None,
            "delta_exec_status": "na",
            "delta_exec_notes": "fills_and_fees_not_in_sealed_jsonl",
        }

    for name, offset_s in HORIZON_SECONDS.items():
        mark = _last_mark_as_of(marks, t0, offset_s)
        if mark is None:
            horizons[name] = HorizonOutcome(
                horizon=name,
                return_pct=None,
                status="na",
                notes="no_post_create_mark",
            ).as_dict()
        else:
            ret = (mark.price / p0 - 1.0) * 100.0
            horizons[name] = HorizonOutcome(
                horizon=name,
                return_pct=ret,
                status="ok",
                mark_t=mark.t.isoformat(),
            ).as_dict()

    t_end = t0 + timedelta(seconds=PEAK_DRAWDOWN_WINDOW_S)
    window = _marks_in_window(marks, t0, t_end)
    peak_ret: float | None = None
    max_dd: float | None = None
    pd_status = "na"
    if window:
        peak_price = max(m.price for m in window)
        trough_after_peak = peak_price
        running_peak = window[0].price
        max_drawdown = 0.0
        for m in window:
            running_peak = max(running_peak, m.price)
            dd = (m.price / running_peak - 1.0) * 100.0
            max_drawdown = min(max_drawdown, dd)
        peak_ret = (peak_price / p0 - 1.0) * 100.0
        max_dd = max_drawdown
        pd_status = "ok"
        _ = trough_after_peak  # reserved for future path-sensitive DD

    return {
        "horizons": horizons,
        "peak_return_pct": peak_ret,
        "max_drawdown_pct": max_dd,
        "peak_drawdown_status": pd_status,
        "delta_exec": None,
        "delta_exec_status": "na",
        "delta_exec_notes": "fills_and_fees_not_in_sealed_jsonl",
    }


@dataclass
class BookRow:
    source_path: str
    line_no: int
    signature: Any
    mint: Any
    t_ws: Any
    evaluate_label: EvaluateLabel
    evaluate_reasons: list[str]
    entry_price: float | None
    outcomes: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "line_no": self.line_no,
            "signature": self.signature,
            "mint": self.mint,
            "t_ws": self.t_ws,
            "evaluate_label": self.evaluate_label,
            "evaluate_reasons": list(self.evaluate_reasons),
            "entry_price": self.entry_price,
            **self.outcomes,
        }


def _horizon_return(row: Mapping[str, Any], horizon: str) -> float | None:
    horizons = row.get("horizons")
    if not isinstance(horizons, dict):
        return None
    h = horizons.get(horizon)
    if not isinstance(h, dict) or h.get("status") != "ok":
        return None
    val = h.get("return_pct")
    return float(val) if isinstance(val, (int, float)) else None


def _mean_returns(rows: Sequence[Mapping[str, Any]], horizon: str) -> tuple[float | None, int]:
    vals = [_horizon_return(r, horizon) for r in rows]
    nums = [v for v in vals if v is not None]
    if not nums:
        return None, 0
    return sum(nums) / len(nums), len(nums)


def _gate_lift(
    runner_mean: float | None,
    random_mean: float | None,
    runner_n: int,
    random_n: int,
) -> GateResult:
    if runner_n < MIN_PRICED_FOR_KILL or random_n < MIN_PRICED_FOR_KILL:
        return "INCOMPLETE"
    if runner_mean is None or random_mean is None:
        return "INCOMPLETE"
    return "PASS" if runner_mean > random_mean + LIFT_EPSILON else "FAIL"


def _gate_parity(
    runner_mean: float | None,
    reject_mean: float | None,
    runner_n: int,
    reject_n: int,
) -> GateResult:
    if runner_n < MIN_PRICED_FOR_KILL or reject_n < MIN_PRICED_FOR_KILL:
        return "INCOMPLETE"
    if runner_mean is None or reject_mean is None:
        return "INCOMPLETE"
    if abs(runner_mean) < LIFT_EPSILON and abs(reject_mean) < LIFT_EPSILON:
        return "FAIL"
    denom = max(abs(runner_mean), abs(reject_mean), LIFT_EPSILON)
    rel_diff = abs(runner_mean - reject_mean) / denom
    return "FAIL" if rel_diff <= PARITY_REL_TOLERANCE else "PASS"


def _outcome_bucket(row: Mapping[str, Any], horizon: str) -> str:
    ret = _horizon_return(row, horizon)
    if ret is None:
        return "na"
    if ret > 0:
        return "positive_gross"
    return "non_positive_gross"


def build_confusion_matrix(
    book: Sequence[BookRow],
    horizon: str,
) -> dict[str, Any]:
    """Evaluate label vs gross return sign at horizon (Δ_exec N/A in v0)."""
    matrix: Counter[str] = Counter()
    for row in book:
        bucket = _outcome_bucket(row.as_dict(), horizon)
        key = f"evaluate={row.evaluate_label}|outcome={bucket}"
        matrix[key] += 1
    return {
        "horizon": horizon,
        "cells": dict(sorted(matrix.items())),
        "note": "Gross returns only; costs in delta_exec are N/A from JSONL alone.",
    }


def build_summary(
    *,
    paths: Sequence[Path],
    seed: int,
    book: Sequence[BookRow],
    rules: EvaluateRulesProtocol,
    random_baseline: Mapping[str, Any],
    primary_horizon: str,
) -> dict[str, Any]:
    runner_rows = [r.as_dict() for r in book if r.evaluate_label == "runner"]
    reject_rows = [r.as_dict() for r in book if r.evaluate_label == "reject"]
    runner_mean, runner_n = _mean_returns(runner_rows, primary_horizon)
    reject_mean, reject_n = _mean_returns(reject_rows, primary_horizon)
    rand_mean = random_baseline.get("mean_return_pct")
    rand_n = random_baseline.get("priced_n", 0)

    lift_gate = _gate_lift(runner_mean, rand_mean if isinstance(rand_mean, (int, float)) else None, runner_n, int(rand_n))
    parity_gate = _gate_parity(runner_mean, reject_mean, runner_n, reject_n)

    if lift_gate == "INCOMPLETE" or parity_gate == "INCOMPLETE":
        overall: str = "INCOMPLETE"
    elif lift_gate == "FAIL":
        overall = "FAIL_NO_LIFT_VS_RANDOM"
    elif parity_gate == "FAIL":
        overall = "FAIL_SELECTION_BIAS_PARITY"
    else:
        overall = "PASS"

    label_counts = Counter(r.evaluate_label for r in book)
    priced_entry = sum(1 for r in book if r.entry_price is not None)

    exp_ids = {"v0": "EXP-002", "v1": "EXP-002b", "v2": "EXP-002c"}
    exp_id = exp_ids.get(rules.version, "EXP-002")
    rules_key = f"rules_{rules.version}"
    return {
        "exp": exp_id,
        "rules_version": rules.version,
        "unit": "sealed_ingest_hot_bonding_create_full_book",
        "paths": [str(p) for p in paths],
        "seed": seed,
        "population_n": len(book),
        "void_excluded_n": 0,
        "evaluate_label_counts": dict(label_counts),
        rules_key: rules.summary_spec(),
        "primary_horizon": primary_horizon,
        "horizons_sec": HORIZON_SECONDS,
        "priced_entry_n": priced_entry,
        "runner_cohort": {
            "n": len(runner_rows),
            "mean_return_pct": runner_mean,
            "priced_n": runner_n,
        },
        "reject_cohort": {
            "n": len(reject_rows),
            "mean_return_pct": reject_mean,
            "priced_n": reject_n,
        },
        "random_baseline": dict(random_baseline),
        "confusion_matrix": build_confusion_matrix(book, primary_horizon),
        "gates": {
            "no_lift_vs_random": {
                "comparator": f"runner_mean_{primary_horizon} > random_mean_{primary_horizon}",
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": lift_gate,
            },
            "reject_runner_parity_after_costs": {
                "comparator": (
                    f"|runner_mean - reject_mean| / max(|means|) <= {PARITY_REL_TOLERANCE} "
                    "→ selection-bias kill (Δ_exec N/A in v0; gross returns only)"
                ),
                "min_priced_per_arm": MIN_PRICED_FOR_KILL,
                "result": parity_gate,
            },
        },
        "overall": overall,
        "limitations": [
            "Post-create marks from later ingest rows and/or EXP-003 outcome_mark files.",
            "Horizon join is last tick with T < t_mark <= T+H (no future leak).",
            "Δ_exec always N/A from JSONL alone in v0.",
        ],
    }


def draw_random_baseline(
    book: Sequence[BookRow],
    *,
    horizon: str,
    seed: int,
) -> dict[str, Any]:
    runner_n = sum(1 for r in book if r.evaluate_label == "runner")
    if runner_n == 0:
        return {
            "sample_n": 0,
            "priced_n": 0,
            "mean_return_pct": None,
            "allocation": "empty_runners",
        }
    rng = random.Random(seed)
    pool = list(book)
    if runner_n >= len(pool):
        sample = pool
    else:
        sample = rng.sample(pool, runner_n)
    rows = [r.as_dict() for r in sample]
    mean, priced_n = _mean_returns(rows, horizon)
    signatures = [r.signature for r in sample]
    capped = signatures[:MAX_RANDOM_SIGNATURE_SAMPLES]
    return {
        "sample_n": len(sample),
        "priced_n": priced_n,
        "mean_return_pct": mean,
        "allocation": "simple_random_same_n_as_runners",
        "sample_signatures": capped,
        "sample_signatures_total": len(signatures),
        "sample_signatures_truncated": len(signatures) > len(capped),
    }


def run_paper_book(
    paths: Sequence[Path],
    *,
    seed: int = DEFAULT_SEED,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = DEFAULT_PREFIX_V1,
    rules: EvaluateRulesProtocol | None = None,
    primary_horizon: str = PRIMARY_HORIZON,
    marks_paths: Sequence[Path] | None = None,
) -> dict[str, Any]:
    rules = rules or EvaluateRulesV2()
    loaded, malformed_n = load_jsonl_files(paths)
    if marks_paths:
        extra, extra_mal = load_jsonl_files(marks_paths)
        loaded.extend(extra)
        malformed_n += extra_mal
    price_series = build_mint_price_series(loaded)

    eligible: list[LoadedRow] = []
    void_n = 0
    for item in loaded:
        if not is_bonding_create(item.row):
            continue
        if t_ws_missing(item.row):
            void_n += 1
            continue
        eligible.append(item)

    book: list[BookRow] = []
    for item in eligible:
        label, reasons = rules.evaluate(item.row)
        t0 = _parse_iso_ts(item.row.get("t_ws"))
        mint = str(item.row.get("mint"))
        marks = price_series.get(mint, [])
        p0 = _price_proxy_from_row(item.row)
        if t0 is None:
            outcomes = compute_outcomes(t0=t0 or datetime.now(timezone.utc), p0=None, marks=marks)
        else:
            outcomes = compute_outcomes(t0=t0, p0=p0, marks=marks)
        book.append(
            BookRow(
                source_path=item.source_path,
                line_no=item.line_no,
                signature=item.row.get("signature"),
                mint=item.row.get("mint"),
                t_ws=item.row.get("t_ws"),
                evaluate_label=label,
                evaluate_reasons=reasons,
                entry_price=p0,
                outcomes=outcomes,
            )
        )

    random_baseline = draw_random_baseline(book, horizon=primary_horizon, seed=seed)
    summary = build_summary(
        paths=paths,
        seed=seed,
        book=book,
        rules=rules,
        random_baseline=random_baseline,
        primary_horizon=primary_horizon,
    )
    summary["malformed_n"] = malformed_n
    summary["void_n"] = void_n
    summary["eligible_population_n"] = len(eligible)
    summary["marks_paths"] = [str(p) for p in (marks_paths or [])]

    paths_out = write_outputs(
        output_dir=output_dir,
        prefix=prefix,
        book=book,
        summary=summary,
    )
    summary["output_paths"] = {k: str(v) for k, v in paths_out.items()}
    with paths_out["summary"].open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return summary


def write_outputs(
    *,
    output_dir: Path,
    prefix: str,
    book: Sequence[BookRow],
    summary: Mapping[str, Any],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{prefix}_book.jsonl"
    csv_path = output_dir / f"{prefix}_book.csv"
    summary_path = output_dir / f"{prefix}_summary.json"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for row in book:
            fh.write(json.dumps(row.as_dict(), ensure_ascii=False) + "\n")

    fieldnames = [
        "source_path",
        "line_no",
        "signature",
        "mint",
        "t_ws",
        "evaluate_label",
        "evaluate_reasons",
        "entry_price",
        "peak_return_pct",
        "max_drawdown_pct",
        "delta_exec_status",
    ]
    for h in HORIZON_SECONDS:
        fieldnames.append(f"return_{h}")

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in book:
            d = row.as_dict()
            flat = {
                "source_path": d["source_path"],
                "line_no": d["line_no"],
                "signature": d["signature"],
                "mint": d["mint"],
                "t_ws": d["t_ws"],
                "evaluate_label": d["evaluate_label"],
                "evaluate_reasons": ";".join(d["evaluate_reasons"]),
                "entry_price": d["entry_price"],
                "peak_return_pct": d.get("peak_return_pct"),
                "max_drawdown_pct": d.get("max_drawdown_pct"),
                "delta_exec_status": d.get("delta_exec_status"),
            }
            for h in HORIZON_SECONDS:
                ho = d.get("horizons", {}).get(h, {})
                flat[f"return_{h}"] = ho.get("return_pct") if ho.get("status") == "ok" else "N/A"
            writer.writerow(flat)

    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    return {"jsonl": jsonl_path, "csv": csv_path, "summary": summary_path}


def _fmt_pct(val: Any) -> str:
    if val is None or not isinstance(val, (int, float)):
        return "N/A"
    return f"{val:.4f}"


def _print_summary(summary: Mapping[str, Any], out: TextIO = sys.stdout) -> None:
    gates = summary["gates"]
    rc = summary["runner_cohort"]
    rj = summary["reject_cohort"]
    rb = summary["random_baseline"]
    exp_id = summary.get("exp", "EXP-002")
    rules_version = summary.get("rules_version", "v0")
    runners = summary["evaluate_label_counts"].get("runner", 0)
    rejects = summary["evaluate_label_counts"].get("reject", 0)
    ph = summary["primary_horizon"]
    lift = gates["no_lift_vs_random"]["result"]
    parity = gates["reject_runner_parity_after_costs"]["result"]
    overall = summary["overall"]
    lines = [
        f"{exp_id} evaluate->runner {rules_version} (paper)",
        "+------------------+-----------+-----------+",
        "| cohort           | priced_n  | mean_%    |",
        "+------------------+-----------+-----------+",
        f"| runner @{ph:<9} | {rc.get('priced_n', 0):>9} | {_fmt_pct(rc.get('mean_return_pct')):>9} |",
        f"| reject @{ph:<9} | {rj.get('priced_n', 0):>9} | {_fmt_pct(rj.get('mean_return_pct')):>9} |",
        f"| random same-n    | {rb.get('priced_n', 0):>9} | {_fmt_pct(rb.get('mean_return_pct')):>9} |",
        "+------------------+-----------+-----------+",
        f"population_n={summary['population_n']} runners={runners} rejects={rejects}",
        f"no_lift_vs_random={lift} parity_kill={parity} overall={overall}",
        f"outputs: {summary.get('output_paths')}",
    ]
    print("\n".join(lines), file=out)


def _exit_code(overall: str) -> int:
    if overall == "PASS":
        return 0
    if overall == "INCOMPLETE":
        return 3
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.exp002_paper_runner",
        description=(
            "EXP-002/002b/002c rules-only evaluate->paper runner on full bonding-create detect book "
            "(sealed JSONL; no live capital)."
        ),
    )
    p.add_argument(
        "jsonl",
        nargs="+",
        type=Path,
        help="Sealed observe JSONL path(s)",
    )
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed (default: 1)")
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory (default: data/observe)",
    )
    p.add_argument(
        "--rules",
        choices=("v0", "v1", "v2"),
        default="v2",
        help="Evaluate rules version (default: v2 / EXP-002c)",
    )
    p.add_argument(
        "--prefix",
        default=None,
        help="Output filename prefix (default: _exp002 / _exp002b / _exp002c by rules)",
    )
    p.add_argument(
        "--exclude-bonk-pool",
        action="store_true",
        default=None,
        help="Force bonk-pool soft exclude ON (v0: default OFF; v1: default ON)",
    )
    p.add_argument(
        "--include-bonk-pool",
        action="store_true",
        help="Disable bonk-pool exclude (v1 only)",
    )
    p.add_argument(
        "--min-market-cap-sol",
        type=float,
        default=None,
        help="Override min marketCapSol band (v0: optional floor; v1: default 28)",
    )
    p.add_argument(
        "--primary-horizon",
        default=PRIMARY_HORIZON,
        choices=sorted(HORIZON_SECONDS.keys()),
        help=f"Horizon for kill gates (default: {PRIMARY_HORIZON})",
    )
    p.add_argument(
        "--marks",
        nargs="*",
        default=[],
        type=Path,
        help="EXP-003 outcome_mark JSONL path(s); last-at-or-before join (default: none)",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    missing = [p for p in args.jsonl if not p.is_file()]
    if missing:
        print(f"error: JSONL not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 1
    marks_missing = [p for p in args.marks if not p.is_file()]
    if marks_missing:
        print(
            f"error: marks JSONL not found: {', '.join(str(p) for p in marks_missing)}",
            file=sys.stderr,
        )
        return 1
    rules_version: RulesVersion = args.rules
    prefix = args.prefix
    if prefix is None:
        prefix_map = {
            "v0": DEFAULT_PREFIX_V0,
            "v1": DEFAULT_PREFIX_V1,
            "v2": DEFAULT_PREFIX_V2,
        }
        prefix = prefix_map.get(rules_version, DEFAULT_PREFIX_V2)
    rules = rules_from_cli(
        version=rules_version,
        exclude_bonk_pool=args.exclude_bonk_pool,
        include_bonk_pool=args.include_bonk_pool,
        min_market_cap_sol=args.min_market_cap_sol,
    )
    summary = run_paper_book(
        args.jsonl,
        seed=args.seed,
        output_dir=args.output_dir,
        prefix=prefix,
        rules=rules,
        primary_horizon=args.primary_horizon,
        marks_paths=args.marks,
    )
    _print_summary(summary)
    return _exit_code(str(summary["overall"]))


if __name__ == "__main__":
    raise SystemExit(main())
