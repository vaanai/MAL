"""Tests for DEC-026 item 14(c), route B: tools/c1nf_item14c.py (driver, stub chain) and tools/c1nf_item14c_reference.py (independent reference).

Synthetic pick files only. Nothing here opens x594h-0920, a run-* output, a P&L, a label or an outcome record, and no real key or credential file.
The closure tests need a detached checkout of f873aac: they clone this repository (local, shared objects) into a temp dir and skip when that commit
is not in it. Set C1NF_ITEM14C_CLOSURE to an existing detached f873aac checkout to reuse one. The executor runs in subprocesses (`prove`), because the
closure's `tools` package cannot share a process with this repository's.

Run: /data/mal/venv/bin/python -m pytest tools/test_c1nf_item14c.py   (the venv with solders)
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tools import c1nf_item14c as drv
from tools import c1nf_item14c_reference as ref

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "tools" / "c1nf_item14c.py"
PY = sys.executable
# fixture digests of the SYNTHETIC files and of their runs (a change in the generator, the stub or the executor's answers shows up here)
SYNTH_SHA = {"realistic": "863397bd63595165938a127a78cee7e5e73d1721ac67ab073f68f99fdb267611", "limits": "6a06a446a13b528e90fa6cbecb06d29dbb5e4ee74514198a260fb6ac3d62de52"}
RUN_MD5 = {"realistic": "3784363dfe0584214fe7610f873e1110", "limits": "6bcf3aa4847b1979beb65ec28a24d338"}


def run(cmd: list[str], env: dict | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=env)


def make_closure(dest: Path) -> Path:
    """A detached checkout of the install sha in `dest` (a shared local clone: no network)."""
    r = run(["git", "clone", "--quiet", "--no-checkout", "--shared", str(REPO), str(dest)])
    assert r.returncode == 0, r.stderr
    r = run(["git", "-C", str(dest), "checkout", "--quiet", "--detach", drv.INSTALL_SHA])
    if r.returncode != 0:
        pytest.skip(f"{drv.INSTALL_SHA} is not in this repository")
    return dest


@pytest.fixture(scope="session")
def closure(tmp_path_factory) -> Path:
    env = os.environ.get("C1NF_ITEM14C_CLOSURE")
    if env:
        return Path(env)
    return make_closure(tmp_path_factory.mktemp("closure") / "f873aac")


@pytest.fixture(scope="session")
def synth_files(closure, tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("synth")
    out = {}
    for kind in ("realistic", "limits"):
        r = run([PY, "-I", str(DRIVER), "synth", "--closure-dir", str(closure), "--kind", kind, "--out", str(d / f"{kind}.jsonl")])
        assert r.returncode == 0, r.stderr
        out[kind] = d / f"{kind}.jsonl"
    return out


@pytest.fixture(scope="session")
def proofs(closure, synth_files, tmp_path_factory) -> dict[str, tuple[Path, dict]]:
    """One `prove` per synthetic file (executor twice in two processes, the reference once). Hostile env names are set to show they are removed."""
    base = tmp_path_factory.mktemp("proofs")
    env = {**os.environ, "CREDENTIALS_DIRECTORY": "/nonexistent-14c-test", "HELIUS_API_KEY": "not-a-key", "RPC_URL": "http://127.0.0.1:1"}
    out = {}
    cases = {"realistic": ["--require-reasons", "total_loss_stop,max_open"],
             "limits": ["--profile", "synthetic-limits-v1", "--require-reasons", "max_trades_day,end_instant,already_held,cooldown,max_open,daily_loss_stop"]}
    for kind, extra in cases.items():
        w = base / kind
        r = run([PY, "-I", str(DRIVER), "prove", "--closure-dir", str(closure), "--picks", str(synth_files[kind]), "--work-dir", str(w), *extra], env=env)
        assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
        out[kind] = (w, json.loads((w / "proof.json").read_text()))
    return out


# --- condition 5: md5(executor) == md5(reference), a byte-equal rerun, one line per pick ------------------------------------------------------------
@pytest.mark.parametrize("kind", ["realistic", "limits"])
def test_executor_matches_reference_and_rerun_is_byte_equal(proofs, kind):
    w, proof = proofs[kind]
    assert proof["pass"] is True
    assert proof["md5_equal"] and proof["rerun_byte_equal"] and proof["line_count_equals_pick_count"]
    assert proof["executor_md5"] == proof["reference_md5"] == proof["executor_rerun_md5"] == RUN_MD5[kind]
    lines = (w / "exec1" / "lines.csv").read_text().splitlines()
    assert lines == sorted(lines) and len(lines) == proof["n_picks"]
    assert (w / "exec1" / "lines.csv").read_bytes() == (w / "exec2" / "lines.csv").read_bytes() == (w / "reference.csv").read_bytes()
    for ln in lines:
        mint, t, verdict, reason = ln.split(",")
        assert verdict in ("accepted", "skipped") and int(t) > 0 and 32 <= len(mint) <= 44 and reason
    assert proof["picks_sha256"] == SYNTH_SHA[kind]


# --- condition 6: coverage ---------------------------------------------------------------------------------------------------------------------------
def test_realistic_list_fires_the_total_stop_and_two_open(proofs):
    _w, proof = proofs["realistic"]
    assert proof["required_reasons_missing"] == []
    assert proof["skipped_reasons"]["total_loss_stop"] >= 1 and proof["skipped_reasons"]["max_open"] >= 1
    assert proof["accepted"] >= 3


def test_synthetic_limits_file_fires_30_a_day_end_ms_and_already_held(proofs):
    w, proof = proofs["limits"]
    r = proof["skipped_reasons"]
    assert proof["required_reasons_missing"] == []
    assert r["max_trades_day"] == 2 and r["end_instant"] >= 1 and r["already_held"] == 1 and r["cooldown"] == 1 and r["max_open"] == 1 and r["daily_loss_stop"] >= 1
    assert proof["end_ms"] == drv.SYNTH_LIMITS_END_MS
    lines = (w / "exec1" / "lines.csv").read_text().splitlines()
    day1 = [ln for ln in lines if drv.DAY0 <= int(ln.split(",")[1]) < drv.DAY0 + drv.DAY_MS]
    assert sum(1 for ln in day1 if ",accepted," in ln) == 30  # 30 attempts on the first UTC day, then max_trades_day


def test_reasons_come_from_the_executors_enumerated_sets(proofs):
    for kind in proofs:
        meta = json.loads((proofs[kind][0] / "exec1" / "meta.json").read_text())
        assert meta["reasons"]["outside_both_sets"] == [] and meta["reasons"]["parse_refusals"] == []
        assert meta["problems"] == []


# --- condition 1: the closure --------------------------------------------------------------------------------------------------------------------------
def test_meta_records_the_install_sha_and_the_two_blobs(proofs):
    meta = json.loads((proofs["limits"][0] / "exec1" / "meta.json").read_text())
    assert meta["closure"]["head"] == drv.INSTALL_SHA == "f873aac3a07b52ba03c0c13db675051a88e21b38"
    assert meta["closure"]["detached"] and meta["closure"]["clean"]
    assert meta["closure"]["blobs"] == {"tools/c1nf_executor.py": "679f4da2c48d103afa3eb84e613cedfb9b94dec2", "tools/h5_executor.py": "b189dbd192a929e161f5c29d95b2ebe008a67a32"}


def test_blob_sha1_is_gits(tmp_path):
    assert drv.blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_closure_check_refuses_a_branch_a_wrong_head_a_dirty_tree_and_a_wrong_blob(closure, tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="is not"):
        drv.check_closure(REPO)  # this checkout: another head
    ok = drv.check_closure(closure)
    assert ok["head"] == drv.INSTALL_SHA
    on_branch = make_closure(tmp_path / "branch")
    assert run(["git", "-C", str(on_branch), "checkout", "--quiet", "-b", "scratch", drv.INSTALL_SHA]).returncode == 0
    with pytest.raises(SystemExit, match="not detached"):
        drv.check_closure(on_branch)
    dirty = make_closure(tmp_path / "dirty")
    with (dirty / "tools" / "c1nf_executor.py").open("a") as fh:
        fh.write("\n# edited\n")
    with pytest.raises(SystemExit, match="local changes"):
        drv.check_closure(dirty)
    monkeypatch.setitem(drv.BLOBS, "tools/h5_executor.py", "0" * 40)
    with pytest.raises(SystemExit, match="not 0000"):
        drv.check_closure(closure)


def test_run_refuses_a_closure_that_is_not_the_install_sha(synth_files, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(drv.canon(drv.build_stub_spec(drv.read_pick_rows(synth_files["realistic"]), drv.PROFILE_LOSS_FIRST)))
    r = run([PY, "-I", str(DRIVER), "run", "--closure-dir", str(REPO), "--picks", str(synth_files["realistic"]), "--spec", str(spec), "--work-dir", str(tmp_path / "w")])
    assert r.returncode != 0 and "closure check refused" in r.stderr


# --- condition 2: the key ------------------------------------------------------------------------------------------------------------------------------
def test_ephemeral_key_is_not_a_pinned_wallet_and_is_not_in_the_outputs(proofs):
    for kind in proofs:
        w = proofs[kind][0]
        for n in (1, 2):
            meta = json.loads((w / f"exec{n}" / "meta.json").read_text())
            key = meta["key"]
            assert key["unpatched"] == {"C1NF_WALLET_PUBKEY": "CKAc6ZiAC7dWBs4XScujGauaqzgFMoCMPwvxF7cuKGw5", "H5_WALLET_PUBKEY": "5n95HyhZqjZNkjdp44QGJoAqk4ZFjDgMKuUzWcQqSugk"}
            assert key["ephemeral_pubkey"] not in key["unpatched"].values() and key["differs_from_both_before_patching"]
            assert key["credentials_directory_set"] is False
            assert key["secret_scan"]["hits"] == 0 and key["secret_scan"]["files_scanned"] >= 4 and key["secret_scan"]["encodings"] == 9
        a, b = (json.loads((w / f"exec{n}" / "meta.json").read_text())["key"]["ephemeral_pubkey"] for n in (1, 2))
        assert a != b  # a fresh key per run, and the outputs are still byte-equal (the key does not reach them)


def test_executor_is_built_directly_with_no_credential_path():
    tree = ast.parse(DRIVER.read_text())
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not ({"credential_path", "load_probe_key", "load_keypair", "from_json", "from_base58_string", "read_keypair_file"} & called)
    attr_called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "main" not in attr_called  # C1NFExecutor(...) is built directly; the executor's main() (which loads the credential) is never called
    # the key (`kp`) and its bytes (`secret`) are never an argument of anything that prints, logs or writes
    sinks = {"print", "write", "write_text", "write_bytes", "dumps", "format", "warning", "info", "debug", "error", "_log", "_alert"}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")) in sinks:
            names = {x.id for a in [*n.args, *[k.value for k in n.keywords]] for x in ast.walk(a) if isinstance(x, ast.Name)}
            assert not ({"kp", "secret"} & names), ast.dump(n)[:200]


def test_secret_scan_finds_every_encoding_and_a_clean_dir_has_none(tmp_path):
    secret = os.urandom(64)  # a random stand-in, not a key
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "a.txt").write_text("nothing here\n")
    assert drv.scan_for_secret(clean, secret)["hits"] == 0
    for i, needle in enumerate(drv.secret_encodings(secret)):
        d = tmp_path / f"d{i}"
        d.mkdir()
        (d / "f.bin").write_bytes(b"xx" + needle + b"yy")
        assert drv.scan_for_secret(d, secret)["hits"] >= 1, i
    assert len(drv.secret_encodings(secret)) == 9


# --- condition 3: no network -----------------------------------------------------------------------------------------------------------------------------
def test_net_guard_blocks_counts_and_restores():
    saved = (socket.create_connection, socket.getaddrinfo, socket.socket.connect)
    with drv.NetGuard() as g:
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", 9), timeout=0.1)
        with pytest.raises(OSError):
            socket.getaddrinfo("example.invalid", 80)
        with pytest.raises(OSError):
            socket.socket().connect(("127.0.0.1", 9))
        assert g.calls == 3
    assert (socket.create_connection, socket.getaddrinfo, socket.socket.connect) == saved


def test_runs_made_no_socket_call_and_removed_the_hostile_env_names(proofs):
    for kind in proofs:
        meta = json.loads((proofs[kind][0] / "exec1" / "meta.json").read_text())
        assert meta["network"]["socket_calls"] == 0
        assert meta["network"]["env_removed_names"] == ["CREDENTIALS_DIRECTORY", "HELIUS_API_KEY", "RPC_URL"]
        assert set(meta["stub"]["rpc_calls"]) <= {"getAccountInfo", "getBalance", "getBlockHeight", "getLatestBlockhash", "getMultipleAccounts", "getSignatureStatuses",
                                                   "getSlot", "getTransaction", "sendTransaction", "getTokenAccountBalance"}


# --- condition 4: named module-attribute patches only ---------------------------------------------------------------------------------------------------------
def test_only_the_named_patches_were_made(proofs):
    expected = {("tools.c1nf_executor", "LIVE_STATE_DIR"), ("tools.c1nf_executor", "LIVE_OK_PATH"), ("tools.c1nf_executor", "TIER_FILE_PATH"),
                ("tools.c1nf_executor", "C1NF_WALLET_PUBKEY"), ("tools.c1nf_executor", "PICK_WINDOW_START_MS"), ("tools.h5_executor", "LIVE_OK_UID"),
                ("tools.h5_executor", "LIVE_OK_GID"), ("tools.probe_executor", "LIVE_DIR")}
    for kind in proofs:
        meta = json.loads((proofs[kind][0] / "exec1" / "meta.json").read_text())
        assert {(p["module"], p["attr"]) for p in meta["patches"]} == expected and len(meta["patches"]) == len(expected)
        got = {p["attr"]: p["value"] for p in meta["patches"]}
        assert got["C1NF_WALLET_PUBKEY"] == meta["key"]["ephemeral_pubkey"] and got["PICK_WINDOW_START_MS"] == "0"
        assert (proofs[kind][0] / "exec1" / "etc" / "TIER").read_text().strip() == "T1" and meta["tier"] == "T1"


def test_driver_source_patches_nothing_else():
    src = DRIVER.read_text()
    tree = ast.parse(src)
    forbidden = {"C1NF_TIERS", "C1NF_DEFAULT", "C1NF_MAX", "H5_DEFAULT", "H5_MAX", "TIERS", "_pick_refusal", "_budget_stop", "_balance_refusal", "_seal_reason",
                 "STOP_REASONS", "BOOK_REASONS", "SEAL_REASONS", "_total_stop_lamports", "_refuse", "handle_pick_refusal"}
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AugAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Attribute):
                    assert t.attr not in forbidden, t.attr
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("setattr", "delattr"):
            assert not (len(n.args) > 1 and isinstance(n.args[1], ast.Constant) and n.args[1].value in forbidden)
    assert len(__import__("re").findall(r"mock\.patch\.object\(", src)) == 1  # the one loop over the named list
    for name in forbidden - {"handle_pick_refusal"}:
        assert f"mock.patch.object(c, \"{name}\"" not in src and f"(c, \"{name}\"" not in src and f"(h5, \"{name}\"" not in src


def test_effective_limits_are_the_dec_026_canary_limits(proofs):
    meta = json.loads((proofs["realistic"][0] / "exec1" / "meta.json").read_text())
    lim = meta["limits"]
    assert (lim["stake_lamports"], lim["max_open"], lim["max_trades_per_day"], lim["daily_loss_lamports"], lim["total_loss_lamports"]) == (50_000_000, 2, 30, 200_000_000, 300_000_000)
    assert meta["total_stop_lamports"] == 175_000_000 and meta["halts"] == []
    assert meta["stub"]["spec_sha256"] == proofs["realistic"][1]["spec_sha256"]


# --- the gate has teeth: the reference and the executor disagree when a limit differs ---------------------------------------------------------------------
def test_md5_gate_detects_a_changed_limit(proofs, synth_files):
    w, proof = proofs["limits"]
    spec = json.loads((w / "spec.json").read_text())
    picks = [(r["mint"], int(r["decision_T_ms"])) for r in drv.read_pick_rows(synth_files["limits"])]
    exec_text = (w / "exec1" / "lines.csv").read_text()
    assert ref.render(ref.decide(picks, spec, drv.SYNTH_LIMITS_END_MS)) == exec_text
    assert ref.render(ref.decide(picks, spec, ref.END_CEILING_MS)) != exec_text  # no end_ms: the 21 end_instant lines become accepted or others
    spec2 = json.loads(json.dumps(spec))
    spec2["constants"]["wallet0_lamports"] = 100_000_000  # a smaller wallet: the 35% cap drops the total stop to 0.035 SOL
    assert ref.render(ref.decide(picks, spec2, drv.SYNTH_LIMITS_END_MS)) != exec_text


def test_ambiguous_timing_is_a_problem_not_a_pass(closure, tmp_path):
    # a pick off the minute grid, handled within the tolerance of the first pick's close: the comparison would turn on scheduling
    base = tmp_path / "base.jsonl"
    r = run([PY, "-I", str(DRIVER), "synth", "--closure-dir", str(closure), "--kind", "realistic", "--out", str(base)])
    assert r.returncode == 0
    picks = [json.loads(x) for x in base.read_text().splitlines()][:2]
    picks[1] = {**picks[1], "decision_T_ms": picks[0]["decision_T_ms"] + 301_000}
    f = tmp_path / "amb.jsonl"
    f.write_text("".join(json.dumps(p, sort_keys=True) + "\n" for p in picks))
    spec = tmp_path / "spec.json"
    spec.write_text(drv.canon(drv.build_stub_spec(drv.read_pick_rows(f), drv.PROFILE_LOSS_FIRST)))
    r = run([PY, "-I", str(DRIVER), "run", "--closure-dir", str(closure), "--picks", str(f), "--spec", str(spec), "--work-dir", str(tmp_path / "w")])
    assert r.returncode == 1
    meta = json.loads((tmp_path / "w" / "meta.json").read_text())
    assert any("ambiguous timing" in p for p in meta["problems"])


# --- the reference: independent, and right on hand-built cases ------------------------------------------------------------------------------------------------
def test_reference_imports_nothing_from_the_executor_or_tools():
    tree = ast.parse((REPO / "tools" / "c1nf_item14c_reference.py").read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert mods <= {"__future__", "argparse", "hashlib", "json", "sys", "pathlib", "typing"}, mods


def _spec(rows: list[tuple[str, int, int]], wallet0: int = 500_000_000) -> tuple[list[tuple[str, int]], dict]:
    c = {"stake_lamports": 50_000_000, "wallet0_lamports": wallet0, "fee_buy_lamports": 510_000, "fee_sell_lamports": 510_000, "ata_rent_lamports": 2_039_280}
    picks = [{"idx": i, "mint": m, "decision_T_ms": t, "receipt_ms": t + 600, "ret_bps": r, "close_ms": t + 600 + 301_600} for i, (m, t, r) in enumerate(rows)]
    return [(m, t) for m, t, _r in rows], {"constants": c, "picks": picks}


def test_reference_each_limit_on_hand_built_picks():
    M = 60_000
    d = drv.DAY0
    # already_held, cooldown, max_open, accepted again after the cooldown
    pk, sp = _spec([("a", d + 600 * M, 0), ("b", d + 600 * M, 0), ("c", d + 600 * M, 0), ("a", d + 601 * M, 0), ("a", d + 606 * M, 0), ("a", d + 607 * M, 0)])
    got = [(x[2], x[3]) for x in ref.decide(pk, sp, ref.END_CEILING_MS)]
    assert got == [("accepted", "filled"), ("accepted", "filled"), ("skipped", "max_open"), ("skipped", "already_held"), ("skipped", "cooldown"), ("accepted", "filled")]
    # total stop: three closed losers (-0.138), the next buy would put -0.188 at risk against 0.175
    pk, sp = _spec([(f"m{i}", d + (700 + 6 * i) * M, -9000) for i in range(6)])
    assert [x[3] for x in ref.decide(pk, sp, ref.END_CEILING_MS)] == ["filled", "filled", "filled", "total_loss_stop", "total_loss_stop", "total_loss_stop"]
    # the 35% cap: a 0.5 SOL wallet gives 0.175, a 0.9 SOL wallet gives the owner's 0.30 ceiling (0.315 capped)
    big = _spec([(f"m{i}", d + (700 + 6 * i) * M, -9000) for i in range(6)], wallet0=900_000_000)[1]
    assert [x[3] for x in ref.decide(pk, big, ref.END_CEILING_MS)][3] == "filled"
    # 30 a day, then the 31st; and end_ms
    pk, sp = _spec([(f"n{i}", d + (60 + 6 * i) * M, 0) for i in range(32)])
    out = ref.decide(pk, sp, ref.END_CEILING_MS)
    assert sum(1 for x in out if x[2] == "accepted") == 30 and out[30][3] == "max_trades_day" and out[31][3] == "max_trades_day"
    out = ref.decide(pk, sp, d + 90 * M)
    assert out[4][3] == "filled" and out[5][3] == "end_instant" and out[-1][3] == "end_instant"
    # daily stop with the tier total still fine: a profitable first day, then losers on day 2
    rows = [(f"p{i}", d + (60 + 6 * i) * M, 3000) for i in range(10)] + [(f"q{i}", d + drv.DAY_MS + (60 + 6 * i) * M, -9000) for i in range(6)]
    pk, sp = _spec(rows)
    why = [x[3] for x in ref.decide(pk, sp, ref.END_CEILING_MS)]
    assert why[10:] == ["filled", "filled", "filled", "filled", "daily_loss_stop", "daily_loss_stop"]


def test_reference_refuses_a_spec_that_is_not_its_pick_list():
    pk, sp = _spec([("a", drv.DAY0, 0)])
    with pytest.raises(SystemExit):
        ref.decide(pk + [("b", drv.DAY0)], sp, ref.END_CEILING_MS)
    sp["constants"]["stake_lamports"] = 20_000_000
    with pytest.raises(SystemExit):
        ref.decide(pk, sp, ref.END_CEILING_MS)


# --- the stub spec, the synthetic files, extract --------------------------------------------------------------------------------------------------------------
def test_stub_spec_is_a_fixed_function_of_mint_and_index(synth_files):
    rows = drv.read_pick_rows(synth_files["realistic"])
    s1, s2 = drv.build_stub_spec(rows, drv.PROFILE_LOSS_FIRST), drv.build_stub_spec(rows, drv.PROFILE_LOSS_FIRST)
    assert drv.canon(s1) == drv.canon(s2)
    assert drv.ret_loss_first("m", 9) == drv.ret_loss_first("m", 9) and len({drv.ret_loss_first(f"m{k}", 9) for k in range(20)}) > 1  # a function of the mint too
    assert all(p["ret_bps"] <= -8600 for p in s1["picks"][:8]) and any(p["ret_bps"] > 0 for p in s1["picks"][8:])
    with pytest.raises(SystemExit):
        drv.build_stub_spec(rows, "nope")
    with pytest.raises(SystemExit):
        drv.build_stub_spec(rows, drv.PROFILE_SYNTH)  # the synthetic-limits profile only describes the synthetic-limits file


def test_synthetic_files_are_pinned_sorted_and_valid(synth_files):
    for kind, f in synth_files.items():
        assert hashlib.sha256(f.read_bytes()).hexdigest() == SYNTH_SHA[kind]
        rows = [json.loads(x) for x in f.read_text().splitlines()]
        assert rows == sorted(rows, key=lambda r: (r["decision_T_ms"], r["mint"]))
        assert len({(r["mint"], r["decision_T_ms"]) for r in rows}) == len(rows)
        assert all(r["decision_T_ms"] % 60_000 == 0 and r["model_sha"] == drv.CANARY_MODEL_SHA and r["stage1"] is True for r in rows)
    assert len(synth_files["realistic"].read_text().splitlines()) == 28


def test_extract_writes_only_the_days_whitelisted_pick_fields(tmp_path):
    d = tmp_path / "picks"
    d.mkdir()
    day = drv.DAY0
    row = lambda m, t, **kw: {"type": "c1nf_pick", "mint": m, "pool": "p", "decision_T_ms": t, "SD_slot": 1, "pred": 0.1, "h_top1": 0.2, "stage1": True, "feature_hash": "abcdefgh",  # noqa: E731
                              "model_sha": "a" * 64, "q_lamports": 3, "base_reserve": 4, "v_lamports": 1, "state_slot": 0, **kw}
    (d / "c1nf-prewindow-picks-2026-09-20T01.jsonl").write_text("".join(json.dumps(x) + "\n" for x in [
        row("m2", day + 120_000, pnl_decoy=5, label="x"), row("m1", day + 120_000), row("m0", day - 60_000), row("m3", day + drv.DAY_MS), {"type": "c1nf_heartbeat", "t": 1}]))
    out = tmp_path / "0920.jsonl"
    r = run([PY, "-I", str(DRIVER), "extract", "--picks-dir", str(d), "--day", "2026-09-20", "--out", str(out)])
    assert r.returncode == 0, r.stderr
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert [x["mint"] for x in rows] == ["m1", "m2"] and not any("pnl_decoy" in x or "label" in x for x in rows)
    side = (tmp_path / "0920.jsonl.sha256").read_text().split()
    assert side[0] == hashlib.sha256(out.read_bytes()).hexdigest() and json.loads(r.stdout)["n"] == 2
    assert run([PY, "-I", str(DRIVER), "extract", "--picks-dir", str(d), "--day", "2026-09-20", "--out", str(out)]).returncode != 0  # no overwrite
