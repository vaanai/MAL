"""Forward-walk experiment specs and the gatekept secondary family (DEC-017).

`tools/exp012_forward.py` is the one scorer. This module holds what differs per
experiment, with no dependency on that module (no import cycle):

  - `ForwardSpec` and `load_spec`: the committed `ARTIFACTS/<exp>/forward_spec.json`
    (artifact dir, FROZEN.md5 pin, model and threshold file, exit spec id, optional
    selection band, optional reference experiment, clean clock, read end, role,
    registration order);
  - `load_registry`: `ARTIFACTS/forward-family/registry.json`, the fixed-k list of
    registered secondaries (DEC-017 point 2);
  - the statistics DEC-017 point 4 names: the paired per-mint increment, the
    one-sided bootstrap p-value (10,000 draws, seed 1, both fail models) and the
    Holm-Bonferroni step-down across the registered secondaries.

The bootstrap and Holm helpers are DEC-014's own (`tools.kill_review`), not copies.
Nothing here opens a tape or a P&L file; it only computes on rows it is handed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SPEC = "forward_spec_v1"
SCHEMA_REGISTRY = "forward_family_registry_v1"
PRIMARY_EXPERIMENT = "EXP-012"
REGISTRY_PATH = REPO_ROOT / "ARTIFACTS" / "forward-family" / "registry.json"
FAMILY_LEDGER_ROOT = Path("/data/mal/forward-family")
PRIMARY_LEDGER = Path("/data/mal/exp012-forward/FINAL_READS.jsonl")
DEC017_CLEAN_CLOCK = "2026-10-06T00:00:00Z"
DEC017_READ_END = "2026-10-16T00:00:00Z"
DEFAULT_EXIT_SPEC_ID = "tpsl_tp50_sl30"  # tools.exp011_freeze.TARGET_SPEC_ID, "tp50_sl30"
VARIANT_KINDS = ("exit", "band", "refit")
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 1
HOLM_ALPHA = 0.05
LAMPORTS = 1_000_000_000
EXP_RE = re.compile(r"^EXP-\d{3}$")


class SpecRefused(Exception):
    def __init__(self, reasons: Sequence[str]):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def exp_dir_name(experiment: str) -> str:
    """EXP-012 -> exp012."""
    return experiment.lower().replace("-", "")


def default_spec_path(experiment: str, repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / "ARTIFACTS" / exp_dir_name(experiment) / "forward_spec.json"


def default_ledger(experiment: str) -> Path:
    """EXP-012 keeps its own ledger; every secondary has one of its own."""
    if experiment == PRIMARY_EXPERIMENT:
        return PRIMARY_LEDGER
    return FAMILY_LEDGER_ROOT / experiment / "FINAL_READS.jsonl"


@dataclass(frozen=True)
class ForwardSpec:
    experiment: str
    role: str
    registration_order: int
    variant_kind: str | None
    artifact_dir: Path
    frozen_manifest_md5: str | None
    model_file: str
    threshold_file: str
    exit_spec_id: str
    band: tuple[float, float] | None
    reference_experiment: str | None
    clean_clock: str
    read_end: str
    freeze_commit: str | None
    source: Path | None = None

    @property
    def is_secondary(self) -> bool:
        return self.role == "secondary"


def _known_exit_ids() -> set[str]:
    from tools import exploration_exits as ex

    return {s["id"] for s in ex.build_specs()}


def parse_spec(doc: dict[str, Any], *, repo_root: Path = REPO_ROOT, source: Path | None = None, test_window: bool = False) -> ForwardSpec:
    bad: list[str] = []

    def need(key: str) -> Any:
        if key not in doc:
            bad.append(f"spec lacks {key!r}")
        return doc.get(key)

    if doc.get("schema") != SCHEMA_SPEC:
        bad.append(f"spec schema is {doc.get('schema')!r}, want {SCHEMA_SPEC!r}")
    exp, role = need("experiment"), need("role")
    order, kind = need("registration_order"), doc.get("variant_kind")
    art = need("artifact_dir")
    model_file, thr_file = doc.get("model_file", "model.txt"), doc.get("threshold_file", "threshold.json")
    exit_id = doc.get("exit_spec_id", DEFAULT_EXIT_SPEC_ID)
    band_doc, ref = doc.get("selection_band"), doc.get("reference_experiment")
    cc, re_ = need("clean_clock"), need("read_end")
    if bad:
        raise SpecRefused(bad)
    if not isinstance(exp, str) or not EXP_RE.match(exp):
        bad.append(f"experiment {exp!r} is not EXP-###")
    if role not in ("primary", "secondary"):
        bad.append(f"role {role!r} must be 'primary' or 'secondary'")
    if not isinstance(order, int) or isinstance(order, bool):
        bad.append("registration_order must be an integer")
    elif role == "primary" and (exp != PRIMARY_EXPERIMENT or order != 0):
        bad.append(f"the only primary is {PRIMARY_EXPERIMENT} at registration_order 0")
    elif role == "secondary" and order < 1:
        bad.append("a secondary's registration_order is >= 1 (EXP-012 is 0)")
    if (model_file, thr_file) != ("model.txt", "threshold.json"):
        bad.append(f"model_file/threshold_file must be model.txt/threshold.json (the frozen-spec loader's names), got {model_file!r}/{thr_file!r}")
    if exit_id not in _known_exit_ids():
        bad.append(f"exit_spec_id {exit_id!r} is not in tools/exploration_exits")
    band: tuple[float, float] | None = None
    if band_doc is not None:
        try:
            lo, hi = float(band_doc["t_low"]), float(band_doc["t_high"])
        except (KeyError, TypeError, ValueError):
            bad.append("selection_band must be {t_low, t_high} numbers")
        else:
            if not lo < hi:
                bad.append(f"selection_band needs t_low < t_high, got [{lo}, {hi})")
            band = (lo, hi)
    if ref is not None and (not isinstance(ref, str) or not EXP_RE.match(ref) or ref == exp):
        bad.append(f"reference_experiment {ref!r} must be another EXP-###")
    if role == "primary":
        if kind is not None or band is not None or ref is not None:
            bad.append("the primary has no variant_kind, selection_band or reference_experiment")
        if exit_id != DEFAULT_EXIT_SPEC_ID:
            bad.append(f"the primary's exit is {DEFAULT_EXIT_SPEC_ID}, spec has {exit_id!r}")
    elif role == "secondary":
        if kind not in VARIANT_KINDS:
            bad.append(f"a secondary needs variant_kind in {list(VARIANT_KINDS)}")
        if kind == "exit" and ref is None:
            bad.append("an exit variant needs reference_experiment (the paired increment)")
        if kind == "band" and band is None:
            bad.append("a band variant needs selection_band")
        if kind != "band" and band is not None:
            bad.append("selection_band is only for variant_kind 'band'")
        if kind == "refit" and ref is not None:
            bad.append("a refit is tested on the full gate only; it has no reference_experiment")
        if not test_window and (cc, re_) != (DEC017_CLEAN_CLOCK, DEC017_READ_END):
            bad.append(f"a secondary's window must be DEC-017's [{DEC017_CLEAN_CLOCK}, {DEC017_READ_END}), spec has [{cc}, {re_}); an override needs --test-window")
    md5 = doc.get("frozen_manifest_md5")
    if md5 is not None and not re.fullmatch(r"[0-9a-f]{32}", str(md5)):
        bad.append("frozen_manifest_md5 must be 32 hex chars or null")
    if bad:
        raise SpecRefused(bad)
    art_path = Path(art)
    return ForwardSpec(
        experiment=exp,
        role=role,
        registration_order=order,
        variant_kind=kind,
        artifact_dir=art_path if art_path.is_absolute() else repo_root / art_path,
        frozen_manifest_md5=md5,
        model_file=model_file,
        threshold_file=thr_file,
        exit_spec_id=exit_id,
        band=band,
        reference_experiment=ref,
        clean_clock=cc,
        read_end=re_,
        freeze_commit=doc.get("freeze_commit"),
        source=source,
    )


def load_spec(path: Path, *, repo_root: Path = REPO_ROOT, test_window: bool = False) -> ForwardSpec:
    if not path.is_file():
        raise SpecRefused([f"no forward spec at {path}"])
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecRefused([f"{path} is not valid JSON ({exc})"])
    return parse_spec(doc, repo_root=repo_root, source=path, test_window=test_window)


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    """The fixed-k registry. `secondaries` is a list of {experiment, registration_order}; k is its length."""
    if not path.is_file():
        raise SpecRefused([f"no family registry at {path}"])
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecRefused([f"{path} is not valid JSON ({exc})"])
    bad: list[str] = []
    if doc.get("schema") != SCHEMA_REGISTRY:
        bad.append(f"registry schema is {doc.get('schema')!r}, want {SCHEMA_REGISTRY!r}")
    secs = doc.get("secondaries")
    if not isinstance(secs, list):
        raise SpecRefused(bad + ["registry has no 'secondaries' list"])
    names = [s.get("experiment") for s in secs if isinstance(s, dict)]
    orders = [s.get("registration_order") for s in secs if isinstance(s, dict)]
    if len(names) != len(secs) or len(set(names)) != len(names):
        bad.append("registry secondaries need unique experiment ids")
    if orders != list(range(1, len(secs) + 1)):
        bad.append(f"registration orders must be 1..k in file order, got {orders}")
    if doc.get("k") != len(secs):
        bad.append(f"registry k={doc.get('k')!r} but lists {len(secs)} secondaries; k is fixed at the deadline")
    if PRIMARY_EXPERIMENT in names:
        bad.append("EXP-012 is the gate, not a member of family S")
    if bad:
        raise SpecRefused(bad)
    return doc


def require_registered(spec: ForwardSpec, registry: dict[str, Any]) -> None:
    hit = [s for s in registry["secondaries"] if s["experiment"] == spec.experiment]
    if not hit:
        raise SpecRefused([f"{spec.experiment} is not in the family registry (k={registry['k']}); DEC-017 point 2: none is added after the deadline"])
    if hit[0]["registration_order"] != spec.registration_order:
        raise SpecRefused([f"{spec.experiment}: spec registration_order {spec.registration_order} != registry {hit[0]['registration_order']}"])


# --- statistics (DEC-017 point 4, DEC-014 bootstrap and Holm) ---------------------------------


def _trades(pnls: Sequence[tuple[str, float]]) -> list[Any]:
    from tools.paper_attention_promote import BookTrade

    return [BookTrade(mint=m, t_ms=0, pnl=int(round(p))) for m, p in pnls]


def bootstrap_summary(pnls: Sequence[tuple[str, float]]) -> dict[str, Any]:
    """{n, mean_sol, ci90_lo_sol, p_le_zero}: mint-clustered bootstrap, 10,000 draws, seed 1.
    p_le_zero is the share of resampled means <= 0 (DEC-014); ci90_lo is the 5th percentile."""
    from tools.kill_review import _pct, bootstrap_means_lamports

    trades = _trades(pnls)
    if not trades:
        return {"n": 0, "mean_sol": None, "ci90_lo_sol": None, "p_le_zero": None}
    means = sorted(bootstrap_means_lamports(trades, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED))
    return {
        "n": len(trades),
        "mean_sol": sum(t.pnl for t in trades) / len(trades) / LAMPORTS,
        "ci90_lo_sol": _pct(means, 0.05) / LAMPORTS,
        "p_le_zero": sum(1 for m in means if m <= 0) / len(means),
    }


def _key(r: dict[str, Any]) -> tuple[str, int]:
    return (r["mint"], int(r["mig_ms"]))


def paired_increment(variant_rows: Sequence[dict[str, Any]], reference_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Per-mint (variant - reference) net on the mints entered in BOTH books, under both fail
    models. Mints entered in only one book are counted and left out of the pairing."""
    ref = {_key(r): r for r in reference_rows if r["entered"]}
    var = {_key(r): r for r in variant_rows if r["entered"]}
    shared = sorted(set(ref) & set(var))
    out: dict[str, Any] = {
        "n_pairs": len(shared),
        "n_variant_only": len(set(var) - set(ref)),
        "n_reference_only": len(set(ref) - set(var)),
    }
    for model in ("flat", "press"):
        out[model] = bootstrap_summary([(k[0], var[k][model] - ref[k][model]) for k in shared])
    out["clears"] = bool(out["n_pairs"]) and all((out[m]["ci90_lo_sol"] or 0.0) > 0 for m in ("flat", "press"))
    return out


def book_test(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The same bootstrap on a book's own nets (the band, or a refit's full book)."""
    return {m: bootstrap_summary([(r["mint"], r[m]) for r in rows]) for m in ("flat", "press")}


def p_pair(test: dict[str, Any]) -> dict[str, float | None]:
    return {m: test[m]["p_le_zero"] for m in ("flat", "press")}


def family_holm(p_by_exp: dict[str, dict[str, float | None]], *, alpha: float = HOLM_ALPHA) -> dict[str, dict[str, Any]]:
    """Holm-Bonferroni step-down per fail model over ALL registered secondaries (fixed k).
    A secondary passes only if it passes under both models. A missing p-value never passes."""
    from tools.kill_review import holm_step_down

    per_model = {m: holm_step_down({e: p[m] for e, p in p_by_exp.items()}, alpha=alpha) for m in ("flat", "press")}
    return {
        e: {"flat": per_model["flat"][e], "press": per_model["press"][e], "holm_pass": bool(per_model["flat"][e]["pass"] and per_model["press"][e]["pass"])}
        for e in p_by_exp
    }
