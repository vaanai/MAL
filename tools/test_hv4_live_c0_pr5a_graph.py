"""Call-graph completeness of the sell pin (reviewer + QP on #969). Walks every pinned function with ast, resolves Name / Attribute calls inside tools.*, and fails on a
callee that is neither pinned nor on the reviewed allowlist, so a refactor that moves logic into a new helper cannot escape the pin. Synthetic; no HV4 data."""

from __future__ import annotations

import ast
import builtins
import inspect
import textwrap
import unittest

from tools import hv4_live_c0 as c0

# Reviewed: callees in tools.* that shape neither the decoded account data nor the quote nor the signed message (logging, rate limiter, state, I/O). Each needs a reason.
ALLOW = {
    "probe_executor:Executor._log": "writes a fill-log row; no value flows back into the quote",
    "probe_executor:Executor._batch_err": "pinned separately; rate-limited log only",
    "probe_live:LiveExecutor._log": "fill-log row only",
    "probe_executor:error_label": "turns an exception into a log label",
    "probe_executor:FillLog": "fill-log writer",
    "probe_executor:Executor.save": "persists state",
    "probe_executor:Executor._priority": "priority lamports for the send; the C0 priority is pinned by c0.PRIORITY_PIN and the signed-message decode tests",
    "probe_executor:Executor._close": "the paper / probe close path of pe.Executor.poll_positions; the bridge's BridgeExecutor.poll_positions (pinned) overrides it and sells via _start_sell",
    "probe_live:LiveExecutor._alert": "prints an alert line; no value flows back",
    "probe_live:LiveExecutor._sell_failed": "records a failed sell (state and alert); builds nothing that is signed",
    "probe_live:LiveExecutor._fa_send_blocked": "feearm spend-cap check of a BUY send (returns False when fa is None); never on the sell path",
    "probe_live:UnsafeTx": "exception type raised by validate_message (pinned); raising is the refusal",
    "probe_executor:Executor._exit_fields": "builds the logged exit_snapshot fields only",
}


def pinned_objects() -> dict[int, str]:
    out: dict[int, str] = {}
    items = c0.SELL_BUILDER_PIN
    from tools import probe_executor as pe
    from tools import probe_live as pl
    from tools import pumpswap_tx as ptx
    for n in c0.CALLEES_PL:
        out[id(getattr(pl, n))] = f"probe_live:{n}"
    for n in c0.CALLEES_PTX:
        out[id(getattr(ptx, n))] = f"pumpswap_tx:{n}"
    out[id(pl.LiveExecutor._start_sell)] = "probe_live:LiveExecutor._start_sell"
    out[id(pl.LiveExecutor._sign)] = "probe_live:LiveExecutor._sign"
    out[id(pe.exit_check)] = "probe_executor:exit_check"
    for n in c0.PE_FUNCS:
        out[id(getattr(pe, n))] = f"probe_executor:{n}"
    for cn, mn in c0.PE_METHODS:
        out[id(c0._fget(getattr(getattr(pe, cn), mn)))] = f"probe_executor:{cn}.{mn}"
    for spec in c0.PIN_EXTRA:
        out[id(c0._fget(c0.resolve_spec(spec)))] = spec
    assert items
    return out


def tools_module(obj) -> str | None:
    m = getattr(obj, "__module__", "") or ""
    return m[len("tools."):] if m.startswith("tools.") else None


def callees(src: str, glob: dict, owner_cls):
    """Yield (label, resolved object) for every call in `src` that resolves to an object defined in tools.*."""
    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        obj = None
        if isinstance(f, ast.Name):
            obj = glob.get(f.id)
            if obj is None and hasattr(builtins, f.id):
                continue
        elif isinstance(f, ast.Attribute):
            if isinstance(f.value, ast.Name) and f.value.id in ("self", "cls") and owner_cls is not None:
                obj = getattr(owner_cls, f.attr, None)
            elif isinstance(f.value, ast.Name):
                base = glob.get(f.value.id)
                if inspect.ismodule(base):
                    obj = getattr(base, f.attr, None)
        if obj is None:
            continue
        if isinstance(obj, property):
            obj = obj.fget
        if (inspect.isfunction(obj) or inspect.isclass(obj)) and tools_module(obj):
            yield obj


def label_of(obj) -> str:
    mod = tools_module(obj)
    return f"{mod}:{getattr(obj, '__qualname__', repr(obj))}"


class CallGraphTests(unittest.TestCase):
    def setUp(self):
        try:
            from tools import probe_live  # noqa: F401
        except ModuleNotFoundError as e:
            self.skipTest(str(e))

    def targets(self):
        """(label, source, globals, owner class) for every pinned function that has source."""
        from tools import arena_live_bridge as alb
        from tools import probe_live as pl
        pinned = pinned_objects()
        seen = {}
        from tools import probe_executor as pe
        from tools import pumpswap_tx as ptx
        objs = []
        for n in c0.CALLEES_PL:
            objs.append(getattr(pl, n))
        for n in c0.CALLEES_PTX:
            objs.append(getattr(ptx, n))
        objs += [pl.LiveExecutor._start_sell, pl.LiveExecutor._sign, pe.exit_check]
        objs += [getattr(pe, n) for n in c0.PE_FUNCS]
        objs += [c0._fget(getattr(getattr(pe, cn), mn)) for cn, mn in c0.PE_METHODS]
        objs += [c0._fget(c0.resolve_spec(s)) for s in c0.PIN_EXTRA]
        for o in objs:
            if inspect.isclass(o):  # a class pin covers its own body; calls in its methods are walked through the class's functions
                for _n, m in inspect.getmembers(o, inspect.isfunction):
                    if m.__qualname__.startswith(o.__qualname__):
                        try:
                            seen[id(m)] = (label_of(m), inspect.getsource(m), m.__globals__, o)
                        except OSError:  # generated dataclass methods have no source
                            pass
                continue
            owner = None
            q = getattr(o, "__qualname__", "")
            if "." in q:
                owner = pl.LiveExecutor if q.startswith(("Executor.", "LiveExecutor.")) else None
                if owner is None:
                    mod = inspect.getmodule(o)
                    owner = getattr(mod, q.split(".")[0], None)
            seen[id(o)] = (label_of(o), inspect.getsource(o), o.__globals__, owner)
        for n in c0.BRIDGE_METHODS:
            seen[("bridge", n)] = (f"bridge:{n}", c0.bridge_method_source(n), vars(alb), pl.LiveExecutor)
        return pinned, list(seen.values())

    maxDiff = None

    def test_every_tools_callee_of_a_pinned_function_is_pinned_or_reviewed(self):
        pinned, targets = self.targets()
        allowed_ids = set()
        for spec in ALLOW:
            try:
                allowed_ids.add(id(c0._fget(c0.resolve_spec(spec))))
            except (AttributeError, ImportError):
                self.fail(f"allowlist entry does not resolve: {spec}")
        missing: dict[str, set[str]] = {}
        for label, src, glob, owner in targets:
            for obj in callees(src, glob, owner):
                if id(obj) in pinned or id(obj) in allowed_ids:
                    continue
                missing.setdefault(label_of(obj), set()).add(label)
        self.assertEqual({}, {k: sorted(v) for k, v in missing.items()}, "unpinned callees: pin them (PIN_EXTRA) or add a reviewed ALLOW entry with a reason")

    def test_the_walker_catches_a_new_helper(self):
        """Sanity: a pinned function that starts calling an unpinned tools.* helper is reported."""
        from tools import probe_executor as pe
        src = "def f(x):\n    return pe.StaticCache(x)\n"
        found = list(callees(src, {"pe": pe}, None))
        self.assertEqual(label_of(found[0]), "probe_executor:StaticCache")
        self.assertNotIn(id(pe.StaticCache), pinned_objects())

    def test_allowlist_reasons_are_written(self):
        for k, v in ALLOW.items():
            self.assertTrue(len(v) > 10, k)


if __name__ == "__main__":
    unittest.main()
