"""Correction adapter: add the pool's virtual quote reserve V to PumpSwap prints.

The tape's `quote_reserve` for a PumpSwap row is the pool's quote VAULT before that trade. The on-chain
swap math prices on vault + V. The frozen paper code (`tools/paper_curve_math.py`,
`tools/latency_curve.py`) prices on the tape quote alone. Nothing frozen is edited here: during a
scoring pass, `print_from_trade_row` (as imported into `tools.exploration_entry_model`) is wrapped so
that a PumpSwap print leaves it with `quote_reserve = vault + V` and `price_sol` / `market_cap_sol`
recomputed from that. Every quote, spot, tp/sl mark and exit downstream then sees vault + V.
Bonding-curve prints are never touched (the curve already carries its virtual reserves).

Fee tier: `mcap_mode="v"` computes the PumpSwap market cap on vault + V, `"vault"` on the vault alone
(see `tools/exp012_virtual_rescore.py fee-tier` for the on-tape evidence behind the default).

A PumpSwap row whose pool has no V is left unchanged and counted in `COUNTS["no_v"]`; it is never
treated as V = 0 silently. Callers must report the counts.

Spawned worker processes do not inherit parent-side monkeypatches, so the pool workers are replaced by
module-level wrappers (picklable by reference) that read their settings from the environment.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import tools.exploration_entry_model as eem
from tools.paper_price_path import TapePrint

ENV_MAP = "MAL_PSV_MAP"
ENV_MCAP = "MAL_PSV_MCAP"
ENV_COUNTS = "MAL_PSV_COUNTS_DIR"
ENV_FROZEN = "MAL_PSV_FROZEN"
ENV_CAPTURE = "MAL_PSV_CAPTURE"

COUNTS: dict[str, int] = {"pumpswap_prints": 0, "corrected": 0, "no_v": 0, "bonding_untouched": 0}
_NO_V_POOLS: set[str] = set()


def reset_counts() -> None:
    for k in COUNTS:
        COUNTS[k] = 0
    _NO_V_POOLS.clear()


def correct_print(pr: TapePrint, v: int | None, mcap_mode: str = "v") -> TapePrint:
    """Return `pr` with V added to a PumpSwap quote. Bonding prints and v=None return `pr` unchanged."""
    if pr.venue != "pumpswap" or v is None or v <= 0:
        return pr
    quote = pr.quote_reserve + int(v)
    price = quote / (pr.base_reserve * 1000)
    mcap = price * 1_000_000_000 if mcap_mode == "v" else pr.market_cap_sol
    return replace(pr, quote_reserve=quote, price_sol=price, market_cap_sol=mcap)


def make_wrapper(orig: Callable[[dict[str, Any]], Any], vmap: Mapping[str, int | None], mcap_mode: str = "v") -> Callable[[dict[str, Any]], Any]:
    def wrapped(row: dict[str, Any]) -> Any:
        is_swap = row.get("venue") == "pumpswap"
        v = None
        if is_swap:
            pool = row.get("pool")
            v = vmap.get(pool) if isinstance(pool, str) else None
            if v is not None and mcap_mode == "v":
                try:
                    q, b = int(row["quote_reserve"]), int(row["base_reserve"])
                    if q > 0 and b > 0:
                        # the pre-trade fee tier is read from price_sol: price it on vault + V
                        row = dict(row, price_sol=(q + int(v)) / (b * 1000))
                except (KeyError, TypeError, ValueError):
                    pass
        res = orig(row)
        if res is None:
            return res
        name, pr = res
        if pr.venue != "pumpswap":
            COUNTS["bonding_untouched"] += 1
            return res
        COUNTS["pumpswap_prints"] += 1
        if v is None:
            COUNTS["no_v"] += 1
            pool = row.get("pool")
            if isinstance(pool, str):
                _NO_V_POOLS.add(pool)
            return res
        COUNTS["corrected"] += 1
        return name, correct_print(pr, v, mcap_mode)

    return wrapped


@contextlib.contextmanager
def virtual_reserve_patch(vmap: Mapping[str, int | None], mcap_mode: str = "v") -> Iterator[None]:
    """Wrap `eem.print_from_trade_row`; restored on exit, including on error."""
    orig = eem.print_from_trade_row
    eem.print_from_trade_row = make_wrapper(orig, vmap, mcap_mode)
    try:
        yield
    finally:
        eem.print_from_trade_row = orig


def _flush_counts(tag: str) -> None:
    d = os.environ.get(ENV_COUNTS)
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        (Path(d) / f"counts-{os.getpid()}-{tag}.json").write_text(json.dumps({**COUNTS, "no_v_pools": sorted(_NO_V_POOLS)}) + "\n", encoding="utf-8")


def _env_vmap() -> tuple[dict[str, int | None], str]:
    from tools.pumpswap_virtual import load_map

    return load_map(Path(os.environ[ENV_MAP])), os.environ.get(ENV_MCAP, "v")


_VMAP_CACHE: dict[str, Any] = {}


def _cached_vmap() -> tuple[dict[str, int | None], str]:
    if not _VMAP_CACHE:
        _VMAP_CACHE["x"] = _env_vmap()
    return _VMAP_CACHE["x"]


@contextlib.contextmanager
def exit_capture() -> Iterator[None]:
    """Tag each scored row with `exit` by spec: "trigger" (a tp/sl print was hit, so `_delayed` ran inside that
    spec's `eval_spec`) or "cap" (time cap or no trigger); unfilled rows are "none". Restored on exit."""
    import tools.exploration_exits as ee

    flag = {"hit": False}
    hits: dict[str, bool] = {}
    orig_d, orig_s, orig_e = ee._delayed, eem.score_one, eem.eval_spec

    def delayed(*a: Any, **k: Any) -> Any:
        flag["hit"] = True
        return orig_d(*a, **k)

    def eval_spec(spec: Any, *a: Any, **k: Any) -> Any:
        flag["hit"] = False
        res = orig_e(spec, *a, **k)
        hits[spec["id"]] = flag["hit"]
        return res

    def score_one(*a: Any, **k: Any) -> Any:
        hits.clear()
        rows = orig_s(*a, **k)
        for r in rows:
            r["exit"] = ("trigger" if hits.get(r["spec"]) else "cap") if r.get("filled") else "none"
        return rows

    ee._delayed, eem.score_one, eem.eval_spec = delayed, score_one, eval_spec
    try:
        yield
    finally:
        ee._delayed, eem.score_one, eem.eval_spec = orig_d, orig_s, orig_e


def _run_patched(orig: Callable[..., Any], tag: str, *args: Any, **kw: Any) -> Any:
    vmap, mode = _cached_vmap()
    reset_counts()
    frozen = os.environ.get(ENV_FROZEN) == "1"  # frozen pricing: no V, only the exit tag
    with contextlib.ExitStack() as st:
        if not frozen:
            st.enter_context(virtual_reserve_patch(vmap, mode))
        if os.environ.get(ENV_CAPTURE) == "1":
            st.enter_context(exit_capture())
        try:
            return orig(*args, **kw)
        finally:
            _flush_counts(f"{tag}-{args[0] if args else 0}")


def worker_a(*args: Any, **kw: Any) -> Any:
    return _run_patched(_ORIG["a"], "a", *args, **kw)


def worker_b(*args: Any, **kw: Any) -> Any:
    return _run_patched(_ORIG["b"], "b", *args, **kw)


def worker_c(*args: Any, **kw: Any) -> Any:
    return _run_patched(_ORIG["c"], "c", *args, **kw)


def holdout_worker(*args: Any, **kw: Any) -> Any:
    """`tools.exp012_score._run_worker` under the patch (passed as `worker_fn` to `load_rows`)."""
    import tools.exp012_score as s12

    return _run_patched(s12._run_worker, "h", *args, **kw)


def _orig_workers() -> dict[str, Callable[..., Any]]:
    import tools.exploration_entry_model_b2 as b2
    import tools.exploration_entry_model_b3 as b3

    return {"a": eem.run_worker_a, "b": b2.run_worker_b, "c": b3.run_worker_c}


_ORIG = _orig_workers()


@contextlib.contextmanager
def patched_pool_workers() -> Iterator[None]:
    """Parent side: make `run_all_features_{a,b,c}` hand the wrappers above to their spawn pools.
    The original worker functions are restored on exit. Settings travel by environment variables."""
    import tools.exploration_entry_model_b2 as b2
    import tools.exploration_entry_model_b3 as b3

    saved = (eem.run_worker_a, b2.run_worker_b, b3.run_worker_c)
    eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = worker_a, worker_b, worker_c
    try:
        yield
    finally:
        eem.run_worker_a, b2.run_worker_b, b3.run_worker_c = saved
