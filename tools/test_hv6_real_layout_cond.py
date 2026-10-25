"""QP #924 (6402594913) conditions for the real-layout replay, on a SYNTHETIC hour-file layout. No real tape, no API, no RPC."""

from __future__ import annotations

import builtins
import json
import os
import stat
from pathlib import Path

import pytest

from tools import drill_hv6_item6 as D
from tools import drill_hv6_real_layout as RL
from tools.test_hv6_real_layout import HOUR, MIN, _sandbox, shifted_rows, write_layout  # noqa: F401  (_sandbox is autouse)

DAY = "2026-10-21"
FORBIDDEN_KEYS = {"reason", "exit_reason", "exit", "pid", "pool", "mint", "price", "pnl", "est_pnl_sol", "est_ret", "signature", "trader"}


def drill_base(tmp_path, name="t1"):
    base = tmp_path / "drill" / name
    base.mkdir(parents=True)
    (base / "SYNTHETIC_FIXTURE_ROOT").write_text("")
    return base


def window():
    t0 = RL.day_ms(DAY) + 6 * HOUR
    return t0 + 9 * MIN + 2000, t0 + 70 * MIN


def walk_keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk_keys(v)


def test_orders_go_only_to_pumpswap_pools(tmp_path, _sandbox):
    rows = shifted_rows(DAY)
    other = []
    for r in rows:  # the same market on a bonding-curve program, other ids: must never be traded
        c = dict(r)
        c["prog"] = "pump_bonding"
        c["venue"] = "pump_bonding"
        for k in ("pool", "mint"):
            if k in c:
                c[k] = "BC" + c[k][2:]
        c["signature"] = "bc" + str(c["signature"])
        other.append(c)
    write_layout(_sandbox, sorted(rows + other, key=lambda r: (r["t_recv_ms"], r["signature"])))
    pump_pools = {r["pool"] for r in rows if "pool" in r}
    rd = RL.RealDrill(drill_base(tmp_path), _sandbox, "a" * 40, [DAY])
    run = rd.new_run("pump")
    bought = []
    orig = run.engine.buy

    def buy(bot, pool, *a, **k):
        bought.append(pool)
        return orig(bot, pool, *a, **k)

    run.engine.buy = buy
    s, e = window()
    run.run(s, e)
    assert bought and set(bought) <= pump_pools
    entry_pools = {r["pool"] for arm in D.R.ARM_ORDER for r in D.rows(run, arm, "entry")}
    assert entry_pools <= pump_pools
    counts = RL.count_programs(_sandbox, [DAY])[DAY]
    assert set(counts) == {"pumpswap", "pump_bonding"} and counts["pumpswap"] == counts["pump_bonding"] > 0


def test_no_file_outside_the_chosen_days_is_opened(tmp_path, _sandbox, monkeypatch):
    write_layout(_sandbox, shifted_rows(DAY))
    for junk in ("tape-2026-10-19T23.jsonl", "tape-2026-10-22T00.jsonl", "tape-2026-10-25T00.jsonl", "tape-2026-10-25T01.jsonl.zst", "tape-2026-10-20T05.jsonl.zst"):
        (_sandbox / junk).write_text("{}\n")
    opened: list[str] = []
    real_open = builtins.open
    tape = str(_sandbox)

    def spy_open(f, *a, **k):
        if isinstance(f, (str, os.PathLike)) and os.fspath(f).startswith(tape):
            opened.append(os.path.basename(os.fspath(f)))
        return real_open(f, *a, **k)

    real_path_open = Path.open

    def spy_path_open(self, *a, **k):
        if str(self).startswith(tape):
            opened.append(self.name)
        return real_path_open(self, *a, **k)

    real_zst = RL.open_zst

    def spy_zst(path, *a, **k):
        opened.append(os.path.basename(path))
        return real_zst(path, *a, **k)

    import tools.arena_state as AS

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(Path, "open", spy_path_open)
    monkeypatch.setattr(RL, "open_zst", spy_zst)
    monkeypatch.setattr(AS, "open_zst", spy_zst)
    s, e = window()
    res = RL.run_real([DAY], "t2", "a" * 40, base=drill_base(tmp_path, "t2"), _window=(s, e))
    monkeypatch.undo()
    assert res["result"] in ("PASS", "FAIL") and opened
    assert all(n.startswith(f"tape-{DAY}T") for n in opened), sorted({n for n in opened if not n.startswith(f"tape-{DAY}T")})
    for n in ("tape-2026-10-19T23.jsonl", "tape-2026-10-22T00.jsonl", "tape-2026-10-25T00.jsonl", "tape-2026-10-25T01.jsonl.zst", "tape-2026-10-20T05.jsonl.zst"):
        assert n not in opened


def test_output_pass_rule_seal_and_no_per_position_fields(tmp_path, _sandbox, capsys):
    write_layout(_sandbox, shifted_rows(DAY))
    base = drill_base(tmp_path, "t3")
    s, e = window()
    res = RL.run_real([DAY], "t3", "a" * 40, base=base, _window=(s, e))
    out = capsys.readouterr().out
    lines = [json.loads(x) for x in out.splitlines()]
    start, end, sealed = lines[0], lines[-2], lines[-1]
    assert start["rows_by_program"][DAY]["pumpswap"] > 0 and start["orders_only_on"] == "pumpswap" and "scan_ms_p95 (max of both runs) < 1000" in start["pass_rule"]
    assert end["result"] == "PASS" and end["md5_equal"] and end["rss_ok"] and end["scan_p95_ok"]
    assert not (set(walk_keys(lines)) & FORBIDDEN_KEYS)
    # the root is read-only and the printed sha256 is the result file's
    assert sealed["root_read_only"] and not (os.stat(base).st_mode & stat.S_IWUSR)
    rf = base / sealed["result_file"]
    import hashlib
    assert hashlib.sha256(rf.read_bytes()).hexdigest() == sealed["result_sha256"] and not (os.stat(rf).st_mode & stat.S_IWUSR)
    RL_unseal = [os.path.join(dp, n) for dp, dns, fns in os.walk(base) for n in (*dns, *fns)] + [str(base)]
    for q in RL_unseal:
        os.chmod(q, os.stat(q).st_mode | stat.S_IWUSR)
    assert res["result"] == "PASS"


def test_rss_cap_and_p95_make_a_fail(tmp_path, _sandbox, capsys):
    write_layout(_sandbox, shifted_rows(DAY))
    base = drill_base(tmp_path, "t4")
    s, e = window()
    res = RL.run_real([DAY], "t4", "a" * 40, base=base, _window=(s, e), rss_cap_mb=1.0)
    capsys.readouterr()
    assert res["md5_equal"] and not res["rss_ok"] and res["result"] == "FAIL"
    for dp, dns, fns in os.walk(base):
        for n in (*dns, *fns):
            q = os.path.join(dp, n)
            os.chmod(q, os.stat(q).st_mode | stat.S_IWUSR)
    os.chmod(base, os.stat(base).st_mode | stat.S_IWUSR)


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "../x", ".hidden", "a b", "x" * 200])
def test_drill_id_refusals(tmp_path, _sandbox, bad):
    write_layout(_sandbox, shifted_rows(DAY))
    with pytest.raises(RL.Refused):
        RL.run_real([DAY], bad, "a" * 40, base=drill_base(tmp_path, "t5"))


def test_main_prints_only_the_class_name_on_any_exception(tmp_path, _sandbox, capsys, monkeypatch):
    def boom(*a, **k):
        raise ValueError("ROW VALUE POOLXYZ price 1.234")

    monkeypatch.setattr(RL, "run_real", boom)
    rc = RL.main(["--days", DAY, "--drill-id", "t6", "--sha", "a" * 40])
    cap = capsys.readouterr()
    assert rc == 3 and json.loads(cap.err) == {"real_layout": "error", "class": "ValueError"}
    assert "POOLXYZ" not in cap.out + cap.err and "Traceback" not in cap.out + cap.err


def test_wrapper_uses_the_venv_python():
    sh = (Path(__file__).resolve().parent.parent / "scripts" / "ops" / "hv6-real-layout-replay.sh").read_text()
    assert "exec /data/mal/venv/bin/python -m tools.drill_hv6_real_layout" in sh and "python3" not in sh
