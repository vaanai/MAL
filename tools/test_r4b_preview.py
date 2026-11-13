"""r4b_preview: synthetic fixtures only (no declog, declog copy, tape, keeper dir, ledger, out/ or sidecar of any real book; every file is built in tmp dirs). Nothing here
says r4-b or r4bt-a2 makes money."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from pathlib import Path

import pytest

from tools import arena_paper as AP
from tools import r4b_preview as V
from tools import r4bt_gate as G
from tools import r4bt_price as P
from tools import r4bt_read as RD
from tools import test_r4bt_price as T
from tools.arena_declog import DecisionLog
from tools.arena_paper import TEST_ENV, Ledger, register_bot

os.environ[TEST_ENV] = "1"
HR = 3_600_000
DAY = 86_400_000
CUT = V.CUTOFF_MS
SHA = "a" * 40  # the decision host's frozen_sha (start rows)
PIN = "c" * 40  # the preview's pin_commit
POOL = "ZQXPOOL9"
MINT = "MZQXPOOL9"
DRY = "r4bt-a2-dry-1113a"
DEC = "r4bt-a2-1120a"
P0 = ("2026-11-16", "2026-11-17", "2026-11-18")
REPO = Path(__file__).resolve().parent.parent
DEC_TEXT = REPO / "DEC" / "DEC-031-forward-paper-arena.md"


REAL_ROOTS = ("/data2/mal/r4b-preview", "/data2/mal/scratch/r4b-price-keep")


@pytest.fixture(autouse=True)
def _never_touch_the_real_roots(monkeypatch):
    """Any write, mkdir, chmod, link, rename or delete under the real roots raises during these tests (reads are not blocked)."""
    def guard(fn, pathpos=0):
        def inner(*a, **k):
            for x in a[pathpos:pathpos + 2] if fn.__name__ in ("link", "rename", "replace") else a[pathpos:pathpos + 1]:
                if isinstance(x, (str, bytes, os.PathLike)):
                    q = os.path.abspath(os.fsdecode(x))
                    if any(q == r or q.startswith(r + "/") for r in REAL_ROOTS):
                        raise AssertionError(f"a test touched the real root {q}")
            return fn(*a, **k)
        return inner

    for name in ("mkdir", "chmod", "unlink", "rmdir", "link", "rename", "replace", "remove"):
        monkeypatch.setattr(os, name, guard(getattr(os, name)))
    real_open = os.open

    def safe_open(path, flags, *a, **k):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            q = os.path.abspath(os.fsdecode(path))
            if any(q == r or q.startswith(r + "/") for r in REAL_ROOTS):
                raise AssertionError(f"a test opened the real root {q} for writing")
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", safe_open)


@pytest.fixture(autouse=True)
def _disk(monkeypatch):
    import collections

    monkeypatch.setattr("tools.arena_paper.shutil.disk_usage", lambda path: collections.namedtuple("Usage", "total used free")(1000, 500, 500))


# ------------------------------------------------------------------------------------------------------------------------------ fixtures
def keeper_row(d: Path, name: str):
    z = d / name
    st = z.stat()
    row = {"h": name[5:18], "name": name, "zst_bytes": st.st_size, "zst_sha": hashlib.sha256(z.read_bytes()).hexdigest(), "dev": st.st_dev, "inode": st.st_ino,
           "linked_utc": "2026-11-16T00:00:00Z"}
    with open(d / V.KEEPER_HOURS, "a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def mk_tape(d: Path, first: int, hours: int, pools=(POOL,), step=3000, skip=(), extra=None, pre_extra=None, zst=True):
    """Hourly tape files from `first` (ms), `.jsonl.zst` with a keeper row each (as the keeper writes them). `pre_extra`: {hour_name: [rows]} appended (rows of pools no
    entry selects)."""
    d.mkdir(parents=True, exist_ok=True)
    i = 0
    cnt = {p: 0 for p in pools}  # one running index per pool: each pool's reserves follow its own trades
    for h in range(hours):
        hs = first + h * HR
        name = P.hour_name(hs)
        lines = []
        for t in range(hs, hs + HR, step):
            for pool in pools:
                lines.append(T.trade_row(cnt[pool], t, pool=pool))
                cnt[pool] += 1
            i += 1
            if (t // step) % 4 == 0:
                lines.append(T.trade_row(i + 100000, t, pool="PB", bonding=True))
        for src in (extra, pre_extra):
            if src and name in src:
                lines.extend(src[name])
        if name in skip:
            continue
        lines.sort(key=lambda r: r.get("t_recv_ms", 0))  # receive order (stable)
        pth = d / f"tape-{name}.jsonl"
        pth.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in lines))
        if zst:
            import subprocess

            subprocess.run(["zstd", "-q", "-f", "--rm", str(pth)], check=True)
            keeper_row(d, f"tape-{name}.jsonl.zst")


def entry_body(T0, pool=POOL, rank=0, mint=MINT, **kw):
    return {"kind": "entry", "order_id": f"{mint}:{T0}", "pool": pool, "meta": {"mint": mint, "v0": T.V, "mbt": 0, "s0": 1}, "stake_sol": 0.25, "decision_slot": 10 ** 9,
            "t_mark": T0, "rank": rank, "score": 0.5, **kw}


def exit_body(T0, T1, reason="rank", mint=MINT):
    return {"kind": "exit", "order_id": f"{mint}:{T0}", "mint": mint, "reason": reason, "t_mark": T1, "decision_slot": 10 ** 9}


def mk_declog(root: Path, cid: str, rows):
    dl = DecisionLog(root / cid)
    dl.append(V.ARM, CUT - 1000, {"kind": "start", "book": "r4b", "exp": "EXP-027", "mode": "dry", "frozen_sha": SHA, "change_id": cid})
    for ms, body in rows:
        dl.append(V.ARM, ms, body)
    return root / cid / "decisions" / V.ARM


def mk_manifest(tmp: Path, *, dry_stop=None, decision=False, first_live=None, d=0, name="manifest.json"):
    series = [{"change_id": DRY, "role": "dry", "frozen_sha": SHA, "dry_stop_ms": dry_stop}]
    if decision:
        series.append({"change_id": DEC, "role": "decision", "frozen_sha": SHA, "first_live_mark_s": first_live})
    m = {"pin_commit": PIN, "declared_instant": "2026-11-17T01:00:00Z", "d": d, "d_measurement_sha256": "d" * 64, "d_window": "[2026-11-15T00:00Z, 2026-11-16T00:00Z)",
         "series": series, "p0_days": list(P0)}
    p = tmp / name
    p.write_text(json.dumps(m, sort_keys=True))
    return p, G.sha256_file(p)


def world(tmp: Path, dry_rows=None, *, dec_rows=None, hours=9, dry_stop=None, first_live=None, pre_extra=None, tape_first=V.TAPE_FLOOR_MS, pools=(POOL,), skip=()):
    """A preview world: declog copy, keeper dir, manifest. Default decisions: one entry at mark CUTOFF exited one hour later."""
    if dry_rows is None:
        dry_rows = [(CUT + 1200, entry_body(V.CUTOFF_S)), (CUT + HR + 1200, exit_body(V.CUTOFF_S, V.CUTOFF_S + 3600))]
    copy, keep, out, work = tmp / "copy", tmp / "keep", tmp / "out", tmp / "work"
    mk_declog(copy, DRY, dry_rows)
    if dec_rows is not None:
        mk_declog(copy, DEC, dec_rows)
    mk_tape(keep, tape_first, hours, pools=pools, pre_extra=pre_extra, skip=skip)
    mp, msha = mk_manifest(tmp, dry_stop=dry_stop, decision=dec_rows is not None, first_live=first_live)
    as_of = CUT + 2 * HR  # 03:00Z
    return dict(roots=(copy, keep, out, work), manifest=str(mp), manifest_sha256=msha, as_of=G.iso(as_of), as_of_ms=as_of, now_ms=as_of + DAY, code_state_fn=lambda: (PIN, False))


def run_world(w, **over):
    kw = dict(roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"])
    kw.update(over)
    return V.run(w["manifest"], w["manifest_sha256"], w["as_of"], **kw)


def out_dir(w):
    return Path(w["roots"][2]) / "as-of-20261116T03Z"


def outputs(w) -> dict:
    d = out_dir(w)
    return {n: (d / n).read_bytes() for n in ("preview.json", "preview.md", "console.txt")}


# ------------------------------------------------------------------------------------------------------------------------------ constants and label
def test_label_equals_the_dec_text_of_amendment_31_item_1():
    text = DEC_TEXT.read_text()
    i = text.index("Label on every preview file and Console line")
    m = re.search(r"`(FORWARD PAPER PREVIEW[^`]*)`", text[i:])
    assert m and m.group(1).replace("<instant>", "{declared}") == V.LABEL
    m0 = re.search(r"P0-day rows add: `([^`]*)`", text[i:])
    assert m0 and m0.group(1) == V.P0_LABEL


def test_hard_coded_constants():
    assert V.STAKES == (0.10, 0.25, 0.5, 1.0) and V.K == 10 and V.CUTOFF_S == 1794790800 and V.TAPE_FLOOR == 1794780000 and V.E1_FIXED == 1_920_110
    assert G.iso(V.CUTOFF_S * 1000) == "2026-11-16T01:00:00Z" and G.iso(V.TAPE_FLOOR * 1000) == "2026-11-15T22:00:00Z" and V.ARM == "r4b-exp027"
    assert V.CUTOFF_S - V.TAPE_FLOOR == 3 * 3600
    assert V.DECLOG_COPY_ROOT == "/data2/mal/r4b-preview/declog-copy" and V.TAPE_DIR == "/data2/mal/scratch/r4b-price-keep" and V.OUT_ROOT == "/data2/mal/r4b-preview/out"
    assert V.E1_LABEL == "E1 is a live-minus-sim gap estimate scaled from 0.05 SOL trips; its growth beyond proportional at 0.5 and 1.0 SOL is unmeasured."


def test_config_is_the_registered_pin_with_only_the_stakes_replaced(tmp_path):
    w = world(tmp_path)
    cap = {}
    orig = V.replay

    def spy(*a, **k):
        r = orig(*a, **k)
        cap["cfg"] = r["cfg"]
        return r

    V.replay, saved = spy, V.replay
    try:
        assert run_world(w) == 0
    finally:
        V.replay = saved
    reg = P.pinned_config(feed_delay_slots=0, k_rounding="ceil")
    assert cap["cfg"].as_dict() == reg.as_dict() and cap["cfg"].stakes_sol == V.STAKES and reg.stakes_sol == AP.STAKES_SOL
    assert {k for k in vars(reg) if getattr(reg, k) != getattr(cap["cfg"], k)} == {"stakes_sol"}


@pytest.mark.parametrize("cid,ok", [("r4bt-a2-dry-1113a", True), ("r4bt-a2-1120a", True), ("r4bt-a3-1204b", True), ("r4bt-a3-dry-1203a", True), ("dry-long-1101a", False),
                                    ("r4bt-a1-1107a", False), ("r4bt-a2-dry-1113b", False), ("../x", False), (None, False), ("r4bt-a2-11200", False)])
def test_change_id_allowlist(cid, ok):
    if ok:
        assert V.check_change_id(cid) == cid
    else:
        with pytest.raises(G.Refused):
            V.check_change_id(cid)


# ------------------------------------------------------------------------------------------------------------------------------ guards
def test_roots_guards(tmp_path):
    ok = (tmp_path / "c", tmp_path / "k", tmp_path / "o", tmp_path / "w")
    env = V.make_env(ok)
    for bad in (tmp_path / "x", Path("/data/mal/arena/ledger"), Path("/var/lib/mal/x"), tmp_path / "k" / "sub", tmp_path / "c"):
        with pytest.raises(G.Refused):
            env.check_write(bad)
    for out in ("/data/mal/arena/x", "/var/lib/mal-live/x", "/home/claude/mal-data/r4b-dry/x", "/data2/mal/scratch/r4b-price-keep/o", "/data2/mal/r4b-preview/declog-copy/o",
                str(tmp_path / "c" / "o"), str(tmp_path / "k")):
        with pytest.raises(G.Refused):
            V.make_env((tmp_path / "c", tmp_path / "k", out, tmp_path / "w"))
    for droot in (tmp_path / "x" / "out", tmp_path / "sidecar.jsonl", tmp_path / "host.log", tmp_path / "m1-start.txt"):
        with pytest.raises(G.Refused):
            V.make_env((droot, tmp_path / "k", tmp_path / "o", tmp_path / "w"))
    with pytest.raises(G.Refused):
        V.make_env(("/data/mal/arena/x", tmp_path / "k", tmp_path / "o", tmp_path / "w"))
    d = tmp_path / "c" / "r4bt-a2-dry-1113a" / "out"
    d.mkdir(parents=True)
    with pytest.raises(G.Refused):
        env.check_read_declog(d / "a.jsonl")
    with pytest.raises(G.Refused):
        env.check_read_declog(tmp_path / "c" / "r4bt-a2-dry-1113a" / "sidecar.jsonl")
    with pytest.raises(G.Refused):
        env.check_read_declog(tmp_path / "elsewhere" / "x.jsonl")


def test_the_constants_default_roots_are_what_the_ruling_names():
    assert (V.DECLOG_COPY_ROOT, V.TAPE_DIR, V.OUT_ROOT, V.WORK_ROOT) == ("/data2/mal/r4b-preview/declog-copy", "/data2/mal/scratch/r4b-price-keep",
                                                                           "/data2/mal/r4b-preview/out", "/data2/mal/r4b-preview/work")
    with pytest.raises(G.Refused, match="never used under pytest"):
        V.make_env()


def test_tape_hour_before_the_floor_is_never_opened_and_is_refused(tmp_path, monkeypatch):
    sentinel = V.TAPE_FLOOR_MS - HR  # 2026-11-15T21
    w = world(tmp_path)
    (Path(w["roots"][1]) / f"tape-{P.hour_name(sentinel)}.jsonl").write_text("{not json\n")
    opened = []
    real = P.iter_lines

    def rec(p):
        opened.append(Path(p).name)
        return real(p)

    monkeypatch.setattr(P, "iter_lines", rec)
    assert run_world(w) == 0
    assert opened and not any(P.hour_name(sentinel) in n for n in opened) and any(P.hour_name(V.TAPE_FLOOR_MS) in n for n in opened)
    env = V.make_env(w["roots"])
    with pytest.raises(G.Refused, match="before TAPE_FLOOR"):
        env.check_read_tape(Path(w["roots"][1]) / f"tape-{P.hour_name(sentinel)}.jsonl")
    with pytest.raises(G.Refused):
        V.replay([], [], [Path(w["roots"][1]) / f"tape-{P.hour_name(sentinel)}.jsonl"], 0, tmp_path / "wk", pin_commit=PIN, code_state_fn=w["code_state_fn"], env=env)


# ------------------------------------------------------------------------------------------------------------------------------ declog copy reader
def test_read_chain_prefix_stops_at_as_of_and_never_locks(tmp_path, monkeypatch):
    w = world(tmp_path)
    env = V.make_env(w["roots"])

    def boom(*a, **k):
        raise AssertionError("flock must never be called")

    monkeypatch.setattr(fcntl, "flock", boom)
    rows, shas = V.read_chain_prefix(env, DRY, CUT + HR)
    assert [r["kind"] for r in rows] == ["start", "entry"] and list(shas) == ["2026-11-16"]
    rows2, shas2 = V.read_chain_prefix(env, DRY, CUT + 3 * HR)
    assert [r["kind"] for r in rows2] == ["start", "entry", "exit"] and shas2 != shas
    f = Path(w["roots"][0]) / DRY / "decisions" / V.ARM / "2026-11-16.jsonl"
    assert shas2["2026-11-16"] == __import__("hashlib").sha256(f.read_bytes()).hexdigest()


def test_torn_tail_is_ignored_and_every_chain_break_is_untrusted(tmp_path):
    w = world(tmp_path)
    env = V.make_env(w["roots"])
    f = Path(w["roots"][0]) / DRY / "decisions" / V.ARM / "2026-11-16.jsonl"
    good = f.read_bytes()
    f.write_bytes(good + b'{"seq": 3, "arm": "r4b-exp027", "ms": 1, "pr')  # a torn last line (no newline): cut, never an error
    rows, _ = V.read_chain_prefix(env, DRY, CUT + 3 * HR)
    assert len(rows) == 3
    lines = good.split(b"\n")[:-1]
    f.write_bytes(b"\n".join(lines[:-1]) + b"\n" + lines[-1].replace(b'"rank"', b'"Rank"') + b"\n")  # the very last complete line is inconsistent: a real break
    with pytest.raises(V.Untrusted):
        V.read_chain_prefix(env, DRY, CUT + 3 * HR)
    f.write_bytes(lines[0] + b"\n" + lines[1].replace(b'"rank"', b'"Rank"') + b"\n" + lines[2] + b"\n")  # a break with a line after it
    with pytest.raises(V.Untrusted):
        V.read_chain_prefix(env, DRY, CUT + 3 * HR)
    assert V.run(w["manifest"], w["manifest_sha256"], w["as_of"], roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"]) == 3
    assert not out_dir(w).exists()


# ------------------------------------------------------------------------------------------------------------------------------ select and merge
def test_select_drops_pre_cutoff_rows_without_counting_and_keeps_exits_of_selected_only(tmp_path):
    rows = [{"kind": "entry", "ms": CUT - 5000, "t_mark": V.CUTOFF_S - 3600, "order_id": f"{MINT}:{V.CUTOFF_S - 3600}", "pool": POOL},
            {"kind": "exit", "ms": CUT + 2 * HR - 1, "order_id": f"{MINT}:{V.CUTOFF_S - 3600}", "t_mark": V.CUTOFF_S},
            {"kind": "entry", "ms": CUT + 1200, "t_mark": V.CUTOFF_S, "order_id": f"{MINT}:{V.CUTOFF_S}", "pool": POOL},
            {"kind": "exit", "ms": CUT + HR, "order_id": f"{MINT}:{V.CUTOFF_S}", "t_mark": V.CUTOFF_S + 3600},
            {"kind": "entry", "ms": CUT + 9 * HR, "t_mark": V.CUTOFF_S + 3600, "order_id": f"{MINT}:{V.CUTOFF_S + 3600}", "pool": POOL}]
    e, x = V.select(rows, DRY, CUT + 2 * HR)
    assert [r["t_mark"] for r in e] == [V.CUTOFF_S] and [r["order_id"] for r in x] == [f"{MINT}:{V.CUTOFF_S}"]
    with pytest.raises(V.Untrusted):
        V.select([dict(rows[2], order_id=f"{MINT}:{V.CUTOFF_S + 1}")], DRY, CUT + HR)
    with pytest.raises(V.Untrusted):
        V.select([dict(rows[2], pool=None)], DRY, CUT + HR)


def test_pre_cutoff_entry_never_reaches_buy_and_its_exit_is_invisible(tmp_path, monkeypatch):
    early = (CUT - 5000, entry_body(V.CUTOFF_S - 3600))
    early_exit = (CUT + 30 * 60_000, exit_body(V.CUTOFF_S - 3600, V.CUTOFF_S + 1800))
    calls = []
    real = V.ArenaEngine.buy
    monkeypatch.setattr(V.ArenaEngine, "buy", lambda self, *a, **k: (calls.append(a), real(self, *a, **k))[1])
    w = world(tmp_path / "a", dry_rows=[early, early_exit])
    assert run_world(w) == 0 and calls == []
    a = outputs(w)
    ja = json.loads(a["preview.json"])
    assert ja["trades"] == [] and ja["counts"]["entries_selected_counted"] == 0
    w2 = world(tmp_path / "b", dry_rows=[])
    assert run_world(w2) == 0
    b = outputs(w2)
    assert V_strip(a["preview.json"]) == V_strip(b["preview.json"]) and a["preview.md"] == b["preview.md"] and a["console.txt"] == b["console.txt"]


def V_strip(js: bytes) -> dict:
    j = json.loads(js)
    j.pop("inputs")  # the prefix shas cover the chain bytes, pre-cut-off rows included
    return j


def test_merge_series_once_per_mark_and_catch_up_never_counted():
    m = {"series": [{"change_id": DRY, "role": "dry", "dry_stop_ms": (V.CUTOFF_S + 7200) * 1000}, {"change_id": DEC, "role": "decision", "first_live_mark_s": V.CUTOFF_S + 10800}]}

    def e(cid, T0, pool="p"):
        return {"_cid": cid, "t_mark": T0, "pool": pool + str(T0), "ms": T0 * 1000 + cid.__len__()}

    ent = {DRY: [e(DRY, V.CUTOFF_S), e(DRY, V.CUTOFF_S + 3600), e(DRY, V.CUTOFF_S + 7200)],
           DEC: [e(DEC, V.CUTOFF_S + 3600), e(DEC, V.CUTOFF_S + 10800), e(DEC, V.CUTOFF_S + 14400)]}
    out = V.merge_series(ent, m)
    lab = {(x["_cid"], x["t_mark"] - V.CUTOFF_S): (x["_label"], x["_counted"]) for x in out}
    assert lab[(DRY, 0)] == ("dry", True) and lab[(DRY, 3600)] == ("dry", True) and lab[(DRY, 7200)] == ("dry after stop", False)
    assert lab[(DEC, 3600)] == ("catch-up replay", False) and lab[(DEC, 10800)] == ("decision", True) and lab[(DEC, 14400)] == ("decision", True)
    both = {DRY: [e(DRY, V.CUTOFF_S)], DEC: [e(DEC, V.CUTOFF_S)]}  # the same counted mark in both series
    with pytest.raises(V.Untrusted):
        V.merge_series(both, {"series": [{"change_id": DRY, "role": "dry", "dry_stop_ms": None}, {"change_id": DEC, "role": "decision", "first_live_mark_s": V.CUTOFF_S}]})


def test_overlap_between_hosts_is_exit_3_and_catch_up_is_absent_from_totals(tmp_path):
    T0, T1, T2 = V.CUTOFF_S, V.CUTOFF_S + 3600, V.CUTOFF_S + 1800
    dry = [(CUT + 1200, entry_body(T0)), (CUT + HR + 1200, exit_body(T0, T1))]
    # the decision host: a catch-up mark (T2 < first_live) and the same mark T0 as the dry host's
    dec = [(CUT + 600_000, entry_body(T2, mint="MX")), (CUT + HR + 600_000, exit_body(T2, T2 + 1800, mint="MX"))]
    w = world(tmp_path / "ok", dry_rows=dry, dec_rows=dec, first_live=V.CUTOFF_S + 7200)
    assert run_world(w) == 0
    j = json.loads(outputs(w)["preview.json"])
    roles = {t["role"]: t for t in j["trades"]}
    assert set(roles) == {"dry", "catch-up replay"} and roles["catch-up replay"]["counted"] is False and roles["dry"]["counted"] is True
    assert j["counts"]["entries_selected_counted"] == 1 and j["counts"]["entries_outside_totals"] == 1
    for stake, modes in j["cells"].items():
        for mode, cs in modes.items():
            assert all(c["rent0"]["n"] == 1 for c in cs.values()), "the catch-up series is in no total or statistic"
    dec2 = [(CUT + 600_000, entry_body(T0, mint="MX")), (CUT + HR + 600_000, exit_body(T0, T1, mint="MX"))]
    w2 = world(tmp_path / "bad", dry_rows=dry, dec_rows=dec2, first_live=V.CUTOFF_S)
    assert run_world(w2) == 3 and not out_dir(w2).exists()


# ------------------------------------------------------------------------------------------------------------------------------ end to end output
def test_end_to_end_output_labels_p0_no_read_words_and_no_pool_ids(tmp_path):
    w = world(tmp_path)
    assert run_world(w) == 0
    o = outputs(w)
    label = V.label_for("2026-11-17T01:00:00Z")
    j = json.loads(o["preview.json"])
    assert j["label"] == label and j["e1_label"] == V.E1_LABEL and j["d_slots"] == 0 and j["d_measurement_sha256"] == "d" * 64 and j["d_window"].startswith("[2026-11-15")
    for name in ("preview.md", "console.txt"):
        lines = o[name].decode().splitlines()
        assert lines and all(ln.startswith(label) for ln in lines), name
    md = o["preview.md"].decode()
    assert V.E1_LABEL in md and "e1 fixed" in md and "e1 proportional" in md
    assert any(V.P0_LABEL in ln for ln in o["console.txt"].decode().splitlines()) and V.P0_LABEL in md
    t = j["trades"][0]
    assert t["p0"] is True and t["p0_label"] == V.P0_LABEL and t["status"] == "priced" and t["rank"] == 0 and t["series"] == DRY and t["key"] == f"{DRY}-00001"
    assert t["entry_mark_utc"] == "2026-11-16T01:00:00Z" and t["exit_mark_utc"] == "2026-11-16T02:00:00Z" and t["hold_hours"] == 1.0 and t["exit_reason"] == "rank"
    assert set(t["rungs"]) == {"0.1", "0.25", "0.5", "1"}
    # no pool or mint string in any byte; no forbidden key name anywhere
    blob = b"".join(o.values())
    for s in (POOL, MINT, "sigZQX", "ZQXPOOL"):
        assert s.encode() not in blob

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                assert k not in V.OPEN_FIELD_DENY, k
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(j)
    # no read wording outside the label itself
    text = b"".join(o.values()).decode().replace(label, "")
    low = text.lower()
    for word in ("pass", "fail", "verdict", "ci90", "gate"):
        assert word not in low, word
    assert "ci90_lo_sol" not in text and "boot" not in low
    # no wall clock in the deterministic files, nothing random
    assert "engine_id" not in text


def test_four_rungs_both_e1_columns_both_fail_models_rent_charged_beside(tmp_path):
    w = world(tmp_path)
    assert run_world(w) == 0
    j = json.loads(outputs(w)["preview.json"])
    assert set(j["cells"]) == {"0.1", "0.25", "0.5", "1"}
    want = {f"{m}_{lat}_{rule}" for m in ("flat", "press") for lat in ("1p3", "1p9") for rule in ("total", "sc")}
    for s, modes in j["cells"].items():
        assert set(modes) == {"proportional", "fixed"}
        for mode, cs in modes.items():
            assert set(cs) == want
            for k, c in cs.items():
                assert set(c["rent0"]) >= {"n", "mean_sol", "total_sol", "days", "days_pos", "daily", "ex_top3_sol", "ex_best_day_sol"} and "rent_charged" in c
                assert "ci90_lo_sol" not in c["rent0"]
    assert j["e1_headline"] == "proportional" and j["e1_fixed_lamports"] == 1_920_110
    for s in ("0.1", "0.25", "0.5", "1"):
        assert set(j["cost_drag_pct_of_stake"][s]) == {f"{lat}_{rule}" for lat in ("1p3", "1p9") for rule in ("total", "sc")}
        assert j["peak_open"][s]["nominal_k_x_stake_sol"] == 10 * float(s) and j["peak_open"][s]["observed_max_concurrent"] == 1
    rent = [c for c in j["cells"]["0.25"]["proportional"].values() if c["rent0"]["n"]]
    assert rent and all(c["rent_charged"]["n"] == c["rent0"]["n"] for c in rent)


def test_idempotent_rerun_verify_and_first_output_o_excl(tmp_path, capsys):
    w = world(tmp_path)
    assert run_world(w) == 0
    first = outputs(w)
    fo = Path(w["roots"][2]) / "first-output.json"
    fo_bytes = fo.read_bytes()
    assert json.loads(fo_bytes)["as_of"] == w["as_of"] and json.loads(fo_bytes)["pin_commit"] == PIN
    time.sleep(1.1)
    capsys.readouterr()
    assert run_world(w) == 0
    assert outputs(w) == first and fo.read_bytes() == fo_bytes
    assert run_world(w, verify=True) == 0 and capsys.readouterr().out.strip().endswith("EQUAL")
    (out_dir(w) / "preview.json").unlink()
    (out_dir(w) / "preview.json").write_bytes(b"{}")
    assert run_world(w, verify=True) == 4 and capsys.readouterr().out.strip().endswith("DIFF")
    assert run_world(w) == 2, "an output with other bytes is never overwritten"
    assert not _left(w), "the temp ledger is deleted"


def test_every_refusal_writes_nothing(tmp_path):
    w = world(tmp_path)
    out = Path(w["roots"][2])
    assert run_world(w, code_state_fn=lambda: ("b" * 40, False)) == 2
    assert run_world(w, code_state_fn=lambda: (PIN, True)) == 2
    assert V.run(w["manifest"], "0" * 64, w["as_of"], roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"]) == 2
    assert V.run(w["manifest"], w["manifest_sha256"], "2026-11-16T00:59:59Z", roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"]) == 2
    assert V.run(w["manifest"], w["manifest_sha256"], w["as_of"], roots=w["roots"], now_ms=w["as_of_ms"] - 1, code_state_fn=w["code_state_fn"]) == 2
    assert V.run(w["manifest"], w["manifest_sha256"], w["as_of"], roots=w["roots"], now_ms=w["as_of_ms"] + HR, code_state_fn=w["code_state_fn"]) == 2  # last hour not closed
    assert not out.exists() or not any(p.is_file() for p in out.rglob("*"))
    w2 = world(tmp_path / "m", skip=(P.hour_name(V.TAPE_FLOOR_MS + 3 * HR),))
    assert run_world(w2) == 2


def test_manifest_requires_d_and_pinned_fields(tmp_path):
    mp, sha = mk_manifest(tmp_path)
    m = json.loads(mp.read_text())
    for patch in ({"d": None}, {"d": True}, {"d": -1}, {"d_measurement_sha256": "zz"}, {"d_window": ""}, {"pin_commit": "short"}, {"p0_days": ["2026-11-19"]},
                  {"series": [{"change_id": "dry-long-1101a", "role": "dry", "frozen_sha": SHA}]}):
        bad = dict(m, **patch)
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(bad))
        with pytest.raises(G.Refused):
            V.load_manifest(p, G.sha256_file(p))
    nod = {k: v for k, v in m.items() if k != "d"}
    p = tmp_path / "nod.json"
    p.write_text(json.dumps(nod))
    with pytest.raises(G.Refused, match="no default"):
        V.load_manifest(p, G.sha256_file(p))


def test_no_exit_row_and_open_at_as_of_are_listed_counted_and_never_priced(tmp_path):
    T0 = V.CUTOFF_S
    rows = [(CUT + 1200, entry_body(T0)), (CUT + HR + 1200, exit_body(T0, T0 + 3600)),
            (CUT + 1800_000, entry_body(T0 + 1800, mint="MA")), (CUT + 2400_000, entry_body(T0 + 2400, mint="MB"))]
    w = world(tmp_path, dry_rows=rows, dry_stop=(T0 + 2400) * 1000 - 1)  # the dry host stopped before its last mark: its entries have no exit row, final
    # the stop is before as_of: both unexited entries are "no exit row" (the second is after the stop but still an entry row; it is outside the dry totals)
    assert run_world(w) == 0
    j = json.loads(outputs(w)["preview.json"])
    st = {t["entry_mark_utc"]: t["status"] for t in j["trades"]}
    assert j["counts"]["no_exit_row"] + j["counts"]["open_at_as_of"] == 2 and j["counts"]["no_exit_row"] == 2
    assert sorted(st.values()) == ["no_exit_row", "no_exit_row", "priced"]
    assert all(not t["rungs"] for t in j["trades"] if t["status"] != "priced")
    w2 = world(tmp_path / "run", dry_rows=rows, dry_stop=None)
    assert run_world(w2) == 0
    j2 = json.loads(outputs(w2)["preview.json"])
    assert j2["counts"]["open_at_as_of"] == 2 and j2["counts"]["no_exit_row"] == 0
    for modes in j2["cells"].values():
        assert all(c["rent0"]["n"] <= 1 for cs in modes.values() for c in cs.values())


def test_pre_roll_hours_that_touch_no_selected_pool_change_no_byte(tmp_path):
    noise = {P.hour_name(V.TAPE_FLOOR_MS + k * HR): [dict(T.trade_row(900_000 + k, V.TAPE_FLOOR_MS + k * HR + 3000 * (j + 1), pool="OTHER"), signature=f"o{k}{j}") for j in range(5)]
             for k in range(3)}
    a = world(tmp_path / "a")
    b = world(tmp_path / "b", pre_extra=noise)
    assert run_world(a) == 0 and run_world(b) == 0
    oa, ob = outputs(a), outputs(b)
    assert oa == ob


def test_snapshot_monotonicity_trades_priced_at_a_are_identical_at_b(tmp_path):
    T0 = V.CUTOFF_S
    rows = [(CUT + 1200, entry_body(T0)), (CUT + HR + 1200, exit_body(T0, T0 + 3600)),
            (CUT + 2 * HR + 1200, entry_body(T0 + 7200, mint="MB")), (CUT + 3 * HR + 1200, exit_body(T0 + 7200, T0 + 10800, mint="MB"))]
    w = world(tmp_path, dry_rows=rows, hours=10)
    assert run_world(w) == 0
    ja = json.loads(outputs(w)["preview.json"])
    w2 = dict(w, as_of=G.iso(CUT + 4 * HR), now_ms=CUT + 4 * HR + DAY)
    assert V.run(w2["manifest"], w2["manifest_sha256"], w2["as_of"], roots=w2["roots"], now_ms=w2["now_ms"], code_state_fn=w2["code_state_fn"]) == 0
    jb = json.loads((Path(w["roots"][2]) / "as-of-20261116T05Z" / "preview.json").read_bytes())
    pa = {t["key"]: t for t in ja["trades"] if t["status"] == "priced"}
    pb = {t["key"]: t for t in jb["trades"] if t["status"] == "priced"}
    assert pa and set(pa) < set(pb) and all(pb[k] == pa[k] for k in pa)
    assert len(pb) == 2 and len(pa) == 1


# ------------------------------------------------------------------------------------------------------------------------------ pricing arithmetic
def priced_rows(tmp_path, rows=None):
    w = world(tmp_path, dry_rows=rows)
    env = V.make_env(w["roots"])
    m = V.load_manifest(w["manifest"], w["manifest_sha256"])
    box = {}
    orig = V.build_report

    def spy(**kw):
        box["res"] = kw["res"]
        return orig(**kw)

    V.build_report, saved = spy, V.build_report
    try:
        V.compute(m, w["as_of_ms"], env, now_ms=w["now_ms"], code_state_fn=w["code_state_fn"])
    finally:
        V.build_report = saved
    return box["res"]


def test_reconstruct_state_reproduces_every_stored_pnl_nofail_and_e1_identities(tmp_path):
    res = priced_rows(tmp_path)
    cfg = res["cfg"]
    trades = [r for r in res["rows"] if r["kind"] == "trade"]
    assert len(trades) == 4
    n_filled = 0
    for r in trades:
        st = V.reconstruct_state(r["calc"])
        stake = float(round(r["stake_sol"] * 1e9))
        for lat in ("1p3", "1p9"):
            for rule in V.RULES:
                got = AP.price_leg(r["prog"], stake, st, V.LAT_F[lat], cfg, rule)
                assert got["pnl"] == r["calc"][lat]["by_rule"][rule]["pnl_nofail"] and got["state"] == r["calc"][lat]["by_rule"][rule]["state"]
                fx = V.e1_fixed_leg(r["prog"], stake, st, V.LAT_F[lat], cfg, rule)
                if got["state"] == "filled":
                    n_filled += 1
                    if r["stake_sol"] == 0.25:
                        assert fx["pnl"] == got["pnl"], "E1 fixed equals proportional at 0.25 SOL (5 x 384,022 = 1,920,110)"
                    else:
                        want = max(0.0, V.E1_FIXED - fx["haircut"])
                        assert fx["e1_extra"] == want and (fx["pnl"] != got["pnl"] or got["e1_extra"] == want)
                else:
                    assert fx["pnl"] == got["pnl"]
    assert n_filled > 0
    assert 384_022 * 5 == V.E1_FIXED
    # differ at 0.10 and 1.0 when the floor binds
    by = {r["stake_sol"]: r for r in trades}
    for s in (0.10, 1.0):
        r = by[s]
        st = V.reconstruct_state(r["calc"])
        a = AP.price_leg(r["prog"], float(round(s * 1e9)), st, 1.9, cfg, "sc")
        b = V.e1_fixed_leg(r["prog"], float(round(s * 1e9)), st, 1.9, cfg, "sc")
        if a["state"] == "filled" and a["e1_extra"] > 0:
            assert a["pnl"] != b["pnl"]


def test_telescoping_identity_of_the_cost_drag(tmp_path):
    res = priced_rows(tmp_path)
    cfg = res["cfg"]
    n = 0
    for r in [r for r in res["rows"] if r["kind"] == "trade"]:
        stake = float(round(r["stake_sol"] * 1e9))
        st = V.reconstruct_state(r["calc"])
        for lat in ("1p3", "1p9"):
            for rule in V.RULES:
                if r["calc"][lat]["by_rule"][rule]["state"] != "filled":
                    continue
                dc = V.decompose_trip(r["prog"], stake, st, V.LAT_F[lat], cfg, rule)
                if dc is None:
                    continue
                n += 1
                s = dc["impact"] + dc["haircut"] + dc["sends"] + dc["e1_proportional"]
                assert abs(s - dc["total_proportional"]) < 1e-6 * max(1.0, abs(s))
                assert abs(dc["impact"] + dc["haircut"] + dc["sends"] + dc["e1_fixed"] - dc["total_fixed"]) < 1e-6 * max(1.0, abs(s))
                assert dc["e1_proportional"] == r["calc"][lat]["by_rule"][rule]["e1_extra"]
                assert abs(dc["sends"] - 2 * cfg.send) < 1e-3
    assert n > 0


# ------------------------------------------------------------------------------------------------------------------------------ the equality test
CLOCK2 = 1_795_132_800_000  # r4bt-a2's clock, 2026-11-20T00:00:00Z


def _a2_world(tmp: Path, monkeypatch, decisions):
    monkeypatch.setattr(T, "CLOCK", CLOCK2)
    monkeypatch.setattr(T, "NOW", CLOCK2 + 3 * DAY)
    kw = T.world(tmp, decisions=decisions)
    register_bot("r4bt-a2", T.SHA, CLOCK2, kw["registry"], deciding_stake=0.25, model_ids=[], verify_sha=False, now_ms=0)
    return kw


def shaped(pool, t_from, t_to, mult, start_i, step=3000):
    """Tape rows of one pool whose price level is `mult(t)` x the base level: {hour name: [rows]}."""
    out: dict = {}
    for k, t in enumerate(range(t_from, t_to, step)):
        r = T.trade_row(k, t, pool=pool)
        r["quote_reserve"] = r["quote_reserve"] * mult(t)
        r["signature"] = f"sig{pool}{start_i + k}"
        out.setdefault(P.hour_name(t), []).append(r)
    return out


def _merge(*ds):
    out: dict = {}
    for d in ds:
        for k, v in d.items():
            out.setdefault(k, []).extend(v)
    return out


def test_quarter_rung_equals_registered_pricer_rows_and_cells(tmp_path, monkeypatch):
    """Trip kinds in the fixture, each asserted present: filled, guarded (the price jumps 1.6x between the decision spot and the landing print), a strand-class trip under rule total (the price collapses before the exit; its state is exit_invalid, which
    STRAND_STATES counts as a strand; the literal state `stranded`, proceeds below one send, is not built in this fixture), derived_last (the pool stops printing; its exit is priced past the grace slots), a late print, a restart exit and a feed-gap exclusion.
    Rows are compared on the same keys for the whole set at once, and the 0.25 rung's per-trade vectors are compared with r4bt_read.deciding_cells (built by the same function
    on the registered side), with the V-audit set empty and then full (the V band is narrowed so every trade is audited, which makes the rent-charged leg decide)."""
    monkeypatch.setattr(T, "CLOCK", CLOCK2)  # trade_row's slot base: before any row is built
    t0 = CLOCK2 // 1000
    gap = {"type": "gap", "kind": "backlog_jump", "prog": "pumpswap", "data_loss": True, "slot": 0, "t_recv_ms": CLOCK2 + 3 * HR + 900_000}
    x6 = CLOCK2 + HR + 300_000 - 1000  # a decision 1 s before a tape row: the jump lands between the guard spot and the landing print
    x7, e7 = CLOCK2 + HR + 1_200_000 - 1000, CLOCK2 + HR + 3_600_000
    x8, e8 = CLOCK2 + 30 * 60_000 - 1000, CLOCK2 + 2 * HR + 5 * 60_000
    end = CLOCK2 + 7 * HR
    start = CLOCK2 - 3 * HR
    late_slot = T.trade_row(0, CLOCK2 + HR + 1200, pool="P1")["slot"] - 40
    late = dict(T.trade_row(777_777, CLOCK2 + HR + 600_000, pool="P1"), slot=late_slot, signature="late-p1")
    parts = {"p6": shaped("P6", start, end, lambda t: 1.6 if t >= x6 + 1000 else 1.0, 1_000_000),
             "p7": shaped("P7", start, end, lambda t: 1e-4 if t >= e7 - 30_000 else 1.0, 2_000_000),
             "p8": shaped("P8", start, x8 + 20_000, lambda t: 1.0, 3_000_000), "gap": {P.hour_name(CLOCK2 + 3 * HR): [gap]}, "late": {P.hour_name(CLOCK2 + HR): [late]}}
    extra = _merge(*parts.values())
    decisions = [(CLOCK2 + 1200, T.entry_body(order="MP1:%d" % t0, t_mark=t0)), (CLOCK2 + HR + 1200, T.exit_body(order="MP1:%d" % t0)),
                 (CLOCK2 + 2 * HR + 1300, T.entry_body(pool="P2", order="MP2:%d" % (t0 + 7200), t_mark=t0 + 7200)),
                 (CLOCK2 + 3 * HR + 1300, T.exit_body(order="MP2:%d" % (t0 + 7200), reason="restart")),
                 (CLOCK2 + 3 * HR + 600_000, T.entry_body(pool="P3", order="MP3:%d" % (t0 + 10800), t_mark=t0 + 10800)),
                 (CLOCK2 + 4 * HR + 600_000, T.exit_body(order="MP3:%d" % (t0 + 10800))),
                 (CLOCK2 + 3 * HR + 950_000, T.entry_body(pool="P4", order="MP4:%d" % (t0 + 10800), t_mark=t0 + 10800)),
                 (CLOCK2 + 4 * HR + 700_000, T.exit_body(order="MP4:%d" % (t0 + 10800))),
                 (CLOCK2 + 2 * HR + 100_000, T.entry_body(pool="P5", order="MP5:%d" % (t0 + 7300), t_mark=t0 + 7300)),
                 (CLOCK2 + 2 * HR + 900_000, T.exit_body(order="MP5:%d" % (t0 + 7300))),
                 (x6, T.entry_body(pool="P6", order="MP6:%d" % (t0 + 4000), t_mark=t0 + 4000)), (x6 + 20 * 60_000, T.exit_body(order="MP6:%d" % (t0 + 4000))),
                 (x7, T.entry_body(pool="P7", order="MP7:%d" % (t0 + 5000), t_mark=t0 + 5000)), (e7, T.exit_body(order="MP7:%d" % (t0 + 5000))),
                 (x8, T.entry_body(pool="P8", order="MP8:%d" % (t0 + 1800), t_mark=t0 + 1800)), (e8, T.exit_body(order="MP8:%d" % (t0 + 1800)))]
    kw = _a2_world(tmp_path, monkeypatch, sorted(decisions, key=lambda x: x[0]))
    import shutil

    shutil.rmtree(tmp_path / "tape")
    mk_tape(tmp_path / "tape", start, 10, pools=("P1", "P2", "P3", "P4", "P5"), extra=extra)
    res_a = P.price(**dict(kw, bot_id="r4bt-a2"))
    A = Ledger(tmp_path / "ledger").read_bot("r4bt-a2")
    dl = DecisionLog(tmp_path / "declog")
    rows = [r for r in dl.rows(G.ARM)]
    ents = [dict(r, _cid=DRY) for r in rows if r["kind"] == "entry"]
    exs = [dict(r, _cid=DRY) for r in rows if r["kind"] == "exit"]
    last = max(r["ms"] for r in ents + exs)
    files = P.tape_hours(tmp_path / "tape", start, last + P.EXIT_POST_MS, CLOCK2 + 3 * DAY)
    rb = V.replay(ents, exs, files, 0, tmp_path / "work", pin_commit=PIN, code_state_fn=lambda: (PIN, False))
    B = rb["rows"]
    keyf = ("calc", "legs", "v_src", "state_src", "decision_ms", "corrected_slot", "exit_reason", "exclusion_reason", "kind", "prog", "effective_slot", "stamp_ms", "call_ms")

    def pick(rows_, bot, stake, kinds=("trade", "excluded")):
        out = [r for r in rows_ if r["kind"] in kinds and r["bot_id"] == bot and r["stake_sol"] == stake]
        return sorted(out, key=lambda r: (r["decision_ms"]["entry"], r["pool"]))

    qa, qb = pick(A, "r4bt-a2", 0.25), pick(B, V.PREVIEW_BOT, 0.25)
    assert len(qa) == len(qb) == 8
    assert [{k: r.get(k) for k in keyf} for r in qa] == [{k: r.get(k) for k in keyf} for r in qb], "the whole 0.25 row set is equal on every compared key"
    la = sorted(r["gap_overlap"] for r in A if r["kind"] == "late_print")
    lb = sorted(r["gap_overlap"] for r in B if r["kind"] == "late_print")
    assert len(la) == len(lb) >= 1 and la == lb
    states = {r["calc"][lat]["by_rule"]["total"]["state"] for r in qa if r["kind"] == "trade" for lat in ("1p3", "1p9")}
    assert {"filled", "guarded"} <= states, states
    assert any(r["calc"][lat]["by_rule"]["total"]["strand"] for r in qa if r["kind"] == "trade" for lat in ("1p3", "1p9")), "the strand class under rule total"
    assert "derived_last" in {r.get("state_src") for r in qa}
    assert "engine_restart" in {r.get("exclusion_reason") for r in qa} and "feed_gap" in {r.get("exclusion_reason") for r in qa}
    assert res_a["counts"]["engine_restart_exclusions"] == 1
    assert {r["stake_sol"] for r in B if r["kind"] in ("trade", "excluded")} == set(V.STAKES)
    bd = json.loads(Path(kw["boundary_file"]).read_text())["boundary_ms"]
    dec_a = RD.deciding_trades(A, CLOCK2, bd, 0.25)
    dec_b = RD.deciding_trades(B, CUT, 1 << 62, 0.25)
    assert len(dec_a) == len(dec_b) >= 4
    for narrow in (False, True):
        if narrow:
            monkeypatch.setattr(P, "V_HI", 17.0e9)  # every trade is now a V-audit trade: the rent-charged leg decides
        aud_a = frozenset(P.v_audit(dec_a, rb["prints"]))
        aud_b = frozenset(P.v_audit(dec_b, rb["prints"]))
        assert len(aud_a) == len(aud_b) and (len(aud_a) == len(dec_a)) == narrow and (not aud_a) == (not narrow)  # (position ids differ by bot id)
        want = RD.deciding_cells(dec_a, V.RULES, aud_a)
        cells, vec = V.cells_of(dec_b, rb["cfg"], P0, aud_b)
        for k in want:
            assert vec["proportional"][k] == want[k]["x0_sol"], (narrow, k)
            assert vec["fixed"][k] == want[k]["x0_sol"], "E1 fixed equals proportional at the 0.25 rung"
            for leg in ("rent0", "rent_charged"):
                for stat in ("total_sol", "mean_sol", "n", "days", "days_pos", "ex_top3_sol", "ex_best_day_sol"):
                    assert cells["proportional"][k][leg][stat] == want[k][leg][stat], (k, leg, stat)
            assert cells["proportional"][k]["deciding_like"]["total_sol"] == pytest.approx(sum(want[k]["x0_sol"]), abs=1e-12)


# ------------------------------------------------------------------------------------------------------------------------------ static
def test_no_registered_tool_mentions_the_preview_and_the_preview_has_no_read_imports():
    for p in (REPO / "tools").glob("r4bt_*"):
        if p.name.startswith("test_"):
            continue
        assert "r4b-preview" not in p.read_text() and "r4b_preview" not in p.read_text(), p.name
    for p in (REPO / "scripts").rglob("*r4bt*"):
        if p.is_file():
            assert "r4b-preview" not in p.read_text() and "r4b_preview" not in p.read_text(), p.name
    src = (REPO / "tools" / "r4b_preview.py").read_text()
    for banned in ("import r4b_book", "r4b_read", "r4b_insample_ladder", "tools import r4b_book"):
        assert banned not in src, banned
    i = src.index("def read_chain_prefix")
    assert "flock" not in src[i:src.index("def check_start_rows")], "no lock on the declog copy"
    assert not re.search(r"^from tools import .*r4b_(book|read|live)", src, re.M)


def test_wrapper_is_posix_sh_with_the_research_0_rules():
    sh = (REPO / "scripts" / "research" / "r4b-preview.sh").read_text()
    assert sh.startswith("#!/bin/sh")
    for need in ("systemd-run --user --scope -q -p MemoryMax=8G -p MemorySwapMax=0 nice -n 19", "/data/mal/venv/bin/python -I tools/r4b_preview.py run", "--manifest", "--manifest-sha256", "--as-of"):
        assert need in sh, need
    assert "[[" not in sh and "PIPESTATUS" not in sh and "oom_score_adj" not in sh and "user-1002" not in sh


def test_cli_main_runs_and_the_script_runs_isolated(tmp_path):
    w = world(tmp_path)
    assert V.main(["run", "--manifest", w["manifest"], "--manifest-sha256", w["manifest_sha256"], "--as-of", w["as_of"]], roots=w["roots"], now_ms=w["now_ms"],
                  code_state_fn=w["code_state_fn"]) == 0
    assert (out_dir(w) / "preview.json").exists()
    import subprocess
    import sys

    argv = [sys.executable, "-I", str(REPO / "tools" / "r4b_preview.py"), "run", "--manifest", w["manifest"], "--manifest-sha256", "0" * 64, "--as-of", w["as_of"]]
    r = subprocess.run(argv, capture_output=True, text=True, cwd=str(tmp_path))  # PYTEST_CURRENT_TEST is inherited: the real roots are refused
    assert r.returncode == 2 and "never used under pytest" in r.stderr
    before = os.stat("/data2/mal/r4b-preview/work").st_mtime_ns if os.path.exists("/data2/mal/r4b-preview/work") else None
    env2 = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    r2 = subprocess.run(argv, capture_output=True, text=True, cwd=str(tmp_path), env=env2)  # a wrong manifest sha is refused before any directory is made or swept
    assert r2.returncode == 2 and "REFUSED" in r2.stderr
    after = os.stat("/data2/mal/r4b-preview/work").st_mtime_ns if os.path.exists("/data2/mal/r4b-preview/work") else None
    assert before == after


# ------------------------------------------------------------------------------------------------------------------------------ review round: work root, blobs, keeper
def _left(w):
    return [p for p in Path(w["roots"][3]).iterdir() if p.name != V.RUN_LOCK]


def _scan(root, needles) -> list:
    hits = []
    if not Path(root).exists():
        return hits
    for p in Path(root).rglob("*"):
        if p.is_file():
            b = p.read_bytes()
            hits += [(p.name, n) for n in needles if n.encode() in b]
    return hits


def _no_files(root) -> bool:
    return not Path(root).exists() or not any(p.is_file() for p in Path(root).rglob("*"))


def test_work_root_overlap_is_refused_and_stale_leftovers_are_swept(tmp_path):
    c, k, o, w = (tmp_path / x for x in ("c", "k", "o", "w"))
    for bad in ((c, k, o, o / "w"), (c, k, o, c / "w"), (c, k, o, k), (c, k, o, tmp_path), (c, k, o, Path("/data/mal/arena/w")), (c, k, o, Path("/var/lib/w")),
                (c, k, o, Path("/data2/mal/r4b-preview/out/w"))):
        with pytest.raises(G.Refused):
            V.make_env(bad)
    env = V.make_env((c, k, o, w))
    with pytest.raises(G.Refused):
        env.check_work(o / "x")
    wd = world(tmp_path / "s")
    stale = Path(wd["roots"][3]) / "work-1-999"
    stale.mkdir(parents=True)
    (stale / "ledger.jsonl").write_text(POOL)
    (Path(wd["roots"][3]) / "loose.txt").write_text(POOL)
    assert run_world(wd) == 0
    assert not _left(wd)


def test_no_pool_string_exists_under_out_root_during_or_after_a_run(tmp_path, monkeypatch):
    w = world(tmp_path)
    seen = {}
    real = V.ArenaEngine.sell

    def spy(self, *a, **k):
        seen["work"] = [p for p in Path(w["roots"][3]).rglob("*") if p.is_file()]
        seen["out_hits"] = _scan(w["roots"][2], (POOL, MINT))
        return real(self, *a, **k)

    monkeypatch.setattr(V.ArenaEngine, "sell", spy)
    assert run_world(w) == 0
    assert seen["work"] and seen["out_hits"] == [], "the engine ledger exists only under WORK_ROOT while the replay runs"
    assert _scan(w["roots"][2], (POOL, MINT, "ZQX")) == [] and not _left(w)


def test_a_crash_mid_replay_leaves_nothing_under_out_root_and_work_is_emptied(tmp_path, monkeypatch):
    w = world(tmp_path)

    def boom(self, *a, **k):
        raise RuntimeError("killed")

    monkeypatch.setattr(V.ArenaEngine, "sell", boom)
    with pytest.raises(RuntimeError):
        run_world(w)
    assert not _left(w), "removed at exit on every path"
    assert _scan(w["roots"][2], (POOL,)) == []


def test_registered_blob_table_matches_the_files_and_one_byte_refuses(tmp_path):
    assert set(V.REGISTERED_BLOBS) == {"tools/r4bt_price.py", "tools/arena_paper.py", "tools/arena_score.py", "tools/r4bt_read.py", "tools/r4bt_gate.py", "tools/arena_declog.py",
                                       "tools/r4bt_bots.py"}
    V.check_registered_modules()  # the table equals the files at this commit
    for rel in V.REGISTERED_BLOBS:
        d = tmp_path / "copy" / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_bytes((REPO / rel).read_bytes())
    V.check_registered_modules(tmp_path / "copy")
    f = tmp_path / "copy" / "tools/arena_score.py"
    f.write_bytes(f.read_bytes() + b" ")
    with pytest.raises(G.Refused, match="arena_score.py"):
        V.check_registered_modules(tmp_path / "copy")
    w = world(tmp_path / "w")
    saved = dict(V.REGISTERED_BLOBS)
    V.REGISTERED_BLOBS["tools/r4bt_bots.py"] = "0" * 40
    try:
        assert run_world(w) == 2
    finally:
        V.REGISTERED_BLOBS.update(saved)
    assert _no_files(w["roots"][2])


def _hour_zst(w, k):
    return Path(w["roots"][1]) / f"tape-{P.hour_name(V.TAPE_FLOOR_MS + k * HR)}.jsonl.zst"


def test_keeper_hours_good_size_sha_missing_row_and_torn_last_line(tmp_path, capsys):
    w = world(tmp_path / "g")
    assert run_world(w) == 0
    w1 = world(tmp_path / "s")  # size mismatch on a pre-roll hour
    f = _hour_zst(w1, 1)
    f.write_bytes(f.read_bytes() + b"\0")
    assert run_world(w1) == 3 and _no_files(w1["roots"][2])
    w2 = world(tmp_path / "h")  # sha mismatch, same size
    f = _hour_zst(w2, 4)
    b = bytearray(f.read_bytes())
    b[-1] ^= 1
    f.write_bytes(bytes(b))
    capsys.readouterr()
    assert run_world(w2) == 3 and P.hour_name(V.TAPE_FLOOR_MS + 4 * HR) in capsys.readouterr().err
    w3 = world(tmp_path / "m")  # no row for the first pre-roll hour
    kp = Path(w3["roots"][1]) / V.KEEPER_HOURS
    kp.write_text("\n".join(kp.read_text().splitlines()[1:]) + "\n")
    assert run_world(w3) == 3
    w4 = world(tmp_path / "t")  # a torn extra line is skipped with a count; a torn needed row is a missing row
    kp = Path(w4["roots"][1]) / V.KEEPER_HOURS
    txt = kp.read_text()
    kp.write_text(txt + '{"h": "2026-11-16T09", "name": "tape-2026-11-16T09.jsonl.z')
    capsys.readouterr()
    assert run_world(w4) == 0 and "1 unreadable line" in capsys.readouterr().err
    ls = txt.splitlines()
    kp.write_text("\n".join(ls[:5]) + '\n{"h": "2026-11-16T0')  # the rows of hours 5.. are gone and the last line is torn
    assert run_world(w4) == 3


def test_chain_break_at_the_tail_is_exit_3_and_a_missing_middle_day_is_untrusted(tmp_path):
    w = world(tmp_path)
    f = Path(w["roots"][0]) / DRY / "decisions" / V.ARM / "2026-11-16.jsonl"
    lines = f.read_bytes().split(b"\n")[:-1]
    f.write_bytes(b"\n".join(lines[:-1]) + b"\n" + lines[-1].replace(b'"rank"', b'"Rank"') + b"\n")
    assert run_world(w) == 3
    w2 = world(tmp_path / "d")
    dl = DecisionLog(Path(w2["roots"][0]) / DRY)
    dl.append(V.ARM, CUT + 3 * HR + 5, {"kind": "gap", "gap_kind": "x"})
    dl.append(V.ARM, CUT + DAY + 5, {"kind": "gap", "gap_kind": "y"})
    dl.append(V.ARM, CUT + 2 * DAY + 5, {"kind": "gap", "gap_kind": "z"})
    (Path(w2["roots"][0]) / DRY / "decisions" / V.ARM / "2026-11-17.jsonl").unlink()
    with pytest.raises(V.Untrusted):
        V.read_chain_prefix(V.make_env(w2["roots"]), DRY, CUT + 3 * DAY)


def test_console_lines_carry_both_e1_columns_and_both_counts(tmp_path):
    T0 = V.CUTOFF_S
    rows = [(CUT + 1200, entry_body(T0)), (CUT + HR + 1200, exit_body(T0, T0 + 3600)), (CUT + 1800_000, entry_body(T0 + 1800, mint="MA"))]
    w = world(tmp_path, dry_rows=rows)
    assert run_world(w) == 0
    con = outputs(w)["console.txt"].decode().splitlines()
    assert con and all("e1 proportional flat" in ln and "e1 fixed flat" in ln and "press" in ln for ln in con)
    assert all(ln.endswith("| no exit row 0, open at as_of 1") for ln in con)
    for s in ("0.1", "0.25", "0.5", "1"):
        assert any(f"{s} SOL" in ln for ln in con)


def test_non_counted_entries_change_no_counted_row_and_peak_open_counts_priced_only(tmp_path):
    T0, T2 = V.CUTOFF_S, V.CUTOFF_S + 1800
    dry = [(CUT + 1200, entry_body(T0)), (CUT + HR + 1200, exit_body(T0, T0 + 3600))]
    dec = [(CUT + 600_000, entry_body(T2, mint="MX")), (CUT + HR + 600_000, exit_body(T2, T2 + 1800, mint="MX"))]
    a = world(tmp_path / "a", dry_rows=dry)
    b = world(tmp_path / "b", dry_rows=dry, dec_rows=dec, first_live=V.CUTOFF_S + 7200)
    assert run_world(a) == 0 and run_world(b) == 0
    ja, jb = json.loads(outputs(a)["preview.json"]), json.loads(outputs(b)["preview.json"])
    assert ja["cells"] == jb["cells"] and ja["peak_open"] == jb["peak_open"]
    assert ja["trades"] == [t for t in jb["trades"] if t["counted"]]


def test_verify_diff_exit_code_is_not_the_crash_code_and_as_of_must_be_on_the_hour(tmp_path):
    w = world(tmp_path)
    assert V.run(w["manifest"], w["manifest_sha256"], "2026-11-16T03:30:00Z", roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"]) == 2
    assert V.run(str(tmp_path / "nope.json"), "0" * 64, w["as_of"], roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"]) == 3
    assert run_world(w) == 0
    (out_dir(w) / "preview.json").unlink()
    (out_dir(w) / "preview.json").write_bytes(b"{}")
    assert run_world(w, verify=True) == 4


def test_first_output_is_written_before_the_outputs(tmp_path, monkeypatch):
    w = world(tmp_path)
    order = []
    real = V.os.link
    monkeypatch.setattr(V.os, "link", lambda a, b: (order.append((Path(w["roots"][2]) / "first-output.json").exists()), real(a, b))[1])
    assert run_world(w) == 0
    assert order and all(order), "first-output.json exists before the first link"


# ------------------------------------------------------------------------------------------------------------------------------ run lock and ordering
def test_real_roots_under_pytest_exit_2_and_never_sweep(tmp_path, monkeypatch):
    w = world(tmp_path)

    def boom(env):
        raise AssertionError("sweep_work must not be called")

    monkeypatch.setattr(V, "sweep_work", boom)
    assert V.run(w["manifest"], w["manifest_sha256"], w["as_of"], roots=None, now_ms=w["now_ms"], code_state_fn=w["code_state_fn"]) == 2


def test_a_held_run_lock_exits_2_and_sweeps_nothing(tmp_path):
    import fcntl as fc

    w = world(tmp_path)
    wk = Path(w["roots"][3])
    wk.mkdir(parents=True)
    (wk / "work-x").mkdir()
    fd = os.open(str(wk / V.RUN_LOCK), os.O_RDWR | os.O_CREAT, 0o600)
    fc.flock(fd, fc.LOCK_EX | fc.LOCK_NB)
    try:
        assert run_world(w) == 2
        assert (wk / "work-x").exists()
    finally:
        os.close(fd)
    assert run_world(w) == 0 and not (wk / "work-x").exists() and (wk / V.RUN_LOCK).exists()


def test_a_wrong_manifest_sha_or_code_state_leaves_the_work_dir_alone(tmp_path):
    w = world(tmp_path)
    wk = Path(w["roots"][3])
    (wk / "work-x").mkdir(parents=True)
    assert V.run(w["manifest"], "0" * 64, w["as_of"], roots=w["roots"], now_ms=w["now_ms"], code_state_fn=w["code_state_fn"]) == 2
    assert run_world(w, code_state_fn=lambda: ("b" * 40, False)) == 2
    assert (wk / "work-x").exists() and not (wk / V.RUN_LOCK).exists()


def test_two_keeper_rows_for_one_hour_that_differ_are_untrusted(tmp_path):
    w = world(tmp_path)
    kp = Path(w["roots"][1]) / V.KEEPER_HOURS
    first = json.loads(kp.read_text().splitlines()[0])
    kp.write_text(kp.read_text() + json.dumps(dict(first, zst_sha="e" * 64)) + "\n")
    assert run_world(w) == 3
    kp.write_text(kp.read_text().rsplit("\n", 2)[0] + "\n" + json.dumps(first) + "\n")  # an identical duplicate is harmless
    assert run_world(w) == 0
