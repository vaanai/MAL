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
    "probe_executor:RpcError": "exception type raised on an RPC failure inside _start_sell; the raise is a refusal, nothing signed flows from it",
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


def local_names(tree) -> set[str]:
    """Parameters and every name bound inside the source (assignments, loops, with/except/import aliases, nested defs, comprehensions)."""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
    return out


def analyze(src: str, glob: dict, owner_cls):
    """(resolved tools.* objects, unresolved plain-name callees). Walks Call nodes AND decorator expressions. A plain-name callee that is a local / parameter, a builtin,
    or resolves in `glob` is fine; one that is none of these is reported as unresolved (reviewer + QP: a walker that cannot see a call must say so)."""
    tree = ast.parse(textwrap.dedent(src))
    local = local_names(tree)
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            targets.append(node.func)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            targets += [d.func if isinstance(d, ast.Call) else d for d in node.decorator_list]
    found, unresolved = [], []
    for f in targets:
        obj = None
        if isinstance(f, ast.Name):
            if f.id in glob:
                obj = glob[f.id]
            elif f.id in local or hasattr(builtins, f.id):
                continue
            else:
                unresolved.append(f.id)
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
            found.append(obj)
    return found, unresolved


def callees(src: str, glob: dict, owner_cls):
    yield from analyze(src, glob, owner_cls)[0]


# Plain-name callees the walker cannot resolve, each reviewed: {name: reason}. Empty means none exist at this head.
UNRESOLVED_ALLOW: dict[str, str] = {}


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
                            seen[id(m)] = (label_of(m), inspect.getsource(m), inspect.unwrap(m).__globals__, o)
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
            seen[id(o)] = (label_of(o), inspect.getsource(o), inspect.unwrap(o).__globals__, owner)
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
        unresolved: dict[str, set[str]] = {}
        for label, src, glob, owner in targets:
            found, unres = analyze(src, glob, owner)
            for name in unres:
                if name not in UNRESOLVED_ALLOW:
                    unresolved.setdefault(name, set()).add(label)
            for obj in found:
                if id(obj) in pinned or id(obj) in allowed_ids:
                    continue
                missing.setdefault(label_of(obj), set()).add(label)
        self.assertEqual({}, {k: sorted(v) for k, v in unresolved.items()}, "plain-name callees the walker cannot resolve: fix the resolution or add a reasoned UNRESOLVED_ALLOW entry")
        self.assertEqual({}, {k: sorted(v) for k, v in missing.items()}, "unpinned callees: pin them (PIN_EXTRA) or add a reviewed ALLOW entry with a reason")

    def test_the_walker_catches_a_new_helper(self):
        """Sanity: a pinned function that starts calling an unpinned tools.* helper is reported."""
        from tools import probe_executor as pe
        src = "def f(x):\n    return pe.StaticCache(x)\n"
        found = list(callees(src, {"pe": pe}, None))
        self.assertEqual(label_of(found[0]), "probe_executor:StaticCache")
        self.assertNotIn(id(pe.StaticCache), pinned_objects())

    def test_decorated_start_sell_resolves_names_in_probe_live_not_the_wrapper_module(self):
        """R2: _start_sell's __globals__ is probe_executor's (the @pe.critical wrapper); the unwrapped function's is probe_live's."""
        from tools import probe_live as pl
        _p, targets = self.targets()
        glob = next(g for label, _s, g, _o in targets if label == "probe_live:LiveExecutor._start_sell")
        self.assertIs(glob, pl.__dict__)
        self.assertIsNot(pl.LiveExecutor._start_sell.__globals__, pl.__dict__)  # the trap

    def test_a_new_probe_live_helper_is_reported_and_decorators_are_walked(self):
        from tools import probe_live as pl
        helper = next(o for n, o in vars(pl).items() if inspect.isfunction(o) and o.__module__ == pl.__name__ and id(o) not in pinned_objects() and not n.startswith("__"))
        src = f"@pe.critical\ndef f(self, x):\n    return {helper.__name__}(x)\n"
        found, unres = analyze(src, {**vars(pl)}, None)
        self.assertIn(helper, found)
        self.assertEqual(unres, [])
        self.assertIn(__import__("tools.probe_executor", fromlist=["critical"]).critical, analyze("@pe.critical\ndef g(self):\n    pass\n", {"pe": __import__("tools.probe_executor", fromlist=["x"])}, None)[0])

    def test_an_unresolvable_plain_name_call_is_reported(self):
        found, unres = analyze("def f(self):\n    return mystery_helper(1)\n", {}, None)
        self.assertEqual(unres, ["mystery_helper"])
        self.assertEqual(analyze("def f(self, rpc):\n    g = rpc\n    return rpc(1) + g(2) + len([])\n", {}, None)[1], [])

    def test_allowlist_reasons_are_written(self):
        for k, v in ALLOW.items():
            self.assertTrue(len(v) > 10, k)


if __name__ == "__main__":
    unittest.main()
