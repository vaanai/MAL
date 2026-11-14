"""osb_live_extract: synthetic fixtures in tmp dirs only (no live path, no host, no tape). Paper; nothing here says any book makes money."""
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "ops"))
import osb_live_extract as X  # noqa: E402

POOL, MINT, SIG = "POOLFIX1", "MINTFIX1", "SIGFIX1"
SINCE = "2026-11-14T12"
T0 = 1_794_000_000_000  # an instant after SINCE is not needed: since is passed explicitly below
DAY = "2026-11-14"
S_MS = X.ms_of_hour(SINCE)
AS_OF = S_MS + 6 * 3_600_000


def jl(rows):
    return "".join(json.dumps(r) + "\n" for r in rows)


def host_rows():
    t = S_MS + 60_000
    return [
        {"kind": "start", "ms": S_MS + 1},
        {"kind": "entry", "ms": t, "order_id": "o1", "pool": POOL, "stake_sol": 0.1, "decision_slot": 500, "meta": {"mint": MINT, "mark0": 1e-8, "signal_slot": 495, "signal_t_recv_ms": t - 800}},
        {"kind": "entry", "ms": t + 10_000, "order_id": "o2", "pool": POOL + "b", "stake_sol": 0.1, "meta": {"mint": MINT + "b", "signal_slot": 600, "signal_t_recv_ms": t + 9_000}},
        {"kind": "entry", "ms": t + 20_000, "order_id": "o3", "pool": POOL + "c", "stake_sol": 0.1, "meta": {"mint": MINT + "c", "signal_slot": 700, "signal_t_recv_ms": t + 19_000}},
        {"kind": "exit", "ms": t + 7_200_000, "order_id": "o1", "pool": POOL, "reason": "tp_1.5x"},
        {"kind": "entry_refused_gap", "ms": t + 30_000, "pool": POOL + "d", "reason": "feed_gap", "trigger_slot": 800, "trigger_ms": t + 29_000, "gap": {"kind": "host_down", "from_ms": 1, "to_ms": 2}},
        {"kind": "entry_refused_gap", "ms": t + 31_000, "pool": POOL + "e", "reason": "pool_blocked_after_restart", "trigger_slot": 810, "trigger_ms": t + 30_500, "blocked_until_ms": t + 99},
        {"kind": "entry_gap_span_ignored", "ms": t + 32_000, "pool": POOL + "f", "trigger_slot": 820, "trigger_ms": t + 31_500, "then": "allow"},
        {"kind": "lane_restart_shadow", "ms": t + 40_000, "down_from_ms": t - 5_000, "down_to_ms": t + 1_000, "shadow_complete": True, "fallback": None},
        {"kind": "lane_restart_shadow_failed", "ms": t + 41_000, "why": "x"},
        {"kind": "entry", "ms": S_MS - 5, "order_id": "old", "pool": "OLDPOOL", "meta": {}},  # before since
    ]


def bridge_rows():
    t = S_MS + 60_000
    return [
        {"kind": "buy_sent", "ts_ms": t + 100, "mode": "live", "order_id": "o1", "mint": MINT, "stake_lamports": 100_000_000, "decision_ms": t, "send_ms": t + 100, "decision_slot": 500},
        {"kind": "buy_landed", "ts_ms": t + 1_900, "mode": "live", "order_id": "o1", "landed_slot": 506, "decision_slot": 500},
        {"kind": "sell_sent", "ts_ms": t + 7_200_100, "mode": "live", "order_id": "o1", "reason": "tp_1.5x", "send_ms": t + 7_200_100},
        {"kind": "sell_landed", "ts_ms": t + 7_201_900, "mode": "live", "order_id": "o1", "pnl_lamports": 50_000_000, "reason": "tp_1.5x", "landed_slot": 9},
        {"kind": "entry_refused", "ts_ms": t + 10_100, "mode": "live", "order_id": "o2", "why": "lane_full", "decision_ms": t + 10_000},
        {"kind": "buy_sent", "ts_ms": t + 20_100, "mode": "live", "order_id": "o3", "mint": MINT + "c", "stake_lamports": 30_000_000, "decision_ms": t + 20_000, "send_ms": t + 20_100},
        {"kind": "buy_sent", "ts_ms": t + 20_100, "mode": "dry", "order_id": "dry1"},
        {"kind": "feed_gap_hold", "ts_ms": t + 50_000, "first_ms": t, "newest_ms": t + 1, "hold_until_ms": t + 600_000},
    ]


def ledger_rows():
    t = S_MS + 60_000
    b = "floor-lane-1"
    return [
        {"ev": "send_intent", "bot_id": b, "mint": MINT, "ts_ms": t + 100, "signature": "intent:o1", "stake_lamports": 100_000_000, "row_id": "r1"},
        {"ev": "fill", "bot_id": b, "mint": MINT, "ts_ms": t + 1_900, "signature": SIG, "ok": True, "sol_out_lamports": 100_000_000, "fee_network_lamports": 5000, "fee_priority_lamports": 70_000, "fee_tip_lamports": 85_000, "row_id": "r2"},
        {"ev": "exit", "bot_id": b, "mint": MINT, "ts_ms": t + 7_201_900, "reason": "tp_1.5x", "sol_in_lamports": 148_000_000, "rent_unreclaimed_lamports": 0, "row_id": "r3"},
        {"ev": "send_intent", "bot_id": b, "mint": MINT + "c", "ts_ms": t + 20_100, "signature": "intent:o3", "stake_lamports": 30_000_000, "row_id": "r4"},
        {"ev": "writeoff", "bot_id": b, "mint": MINT + "c", "ts_ms": t + 700_000, "reason": "buy_expired", "row_id": "r5"},
        {"ev": "send_intent", "bot_id": "other-lane-bot", "mint": "OTHERMINT", "ts_ms": t, "signature": "intent:zz", "stake_lamports": 1},
    ]


def fills_rows():
    t = S_MS + 60_000
    return [{"kind": "buy", "landed": True, "mint": MINT, "signature": SIG, "ts_ms": t + 1_900, "spend_lamports": 100_000_000, "tokens_received": 9_000_000_000, "price_vs_quote_bps": 55, "tokens_source": "meta"}]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("OSB_EXTRACT_TEST", "1")
    d = tmp_path / "root" / "decisions" / "l1-floor-v2"
    d.mkdir(parents=True)
    (d / (DAY + ".jsonl")).write_text(jl(host_rows()))
    led = tmp_path / "ledger"
    led.mkdir()
    (led / "lane-ledger.jsonl").write_text(jl(ledger_rows()))
    br = tmp_path / "bridge.jsonl"
    br.write_text(jl(bridge_rows()))
    fl = tmp_path / "fills.jsonl"
    fl.write_text(jl(fills_rows()))
    out = tmp_path / "near"
    argv = ["extract", "--test-mode", "--decision-root", str(tmp_path / "root"), "--bridge-log", str(br), "--ledger-root", str(led), "--fills-log", str(fl), "--out-dir", str(out),
            "--since-hour", SINCE, "--as-of-ms", str(AS_OF)]
    return tmp_path, argv, out


def load(out):
    rows = [json.loads(x) for x in (out / X.EXTRACT_NAME).read_text().splitlines()]
    return rows, json.loads((out / X.META_NAME).read_text())


def test_extract_orders_and_rows(world, capsys):
    _, argv, out = world
    assert X.main(argv) == 0
    rows, meta = load(out)
    assert rows[0]["kind"] == "header" and rows[0]["since_ms"] == S_MS
    orders = {r["order_id"]: r for r in rows if r["kind"] == "order"}
    assert set(orders) == {"o1", "o2", "o3"}  # the pre-since entry and the other lane's intent are not there
    o1 = orders["o1"]
    assert (o1["pool"], o1["trig_slot"], o1["sent"], o1["landed_ms"], o1["exit_reason"], o1["has_host_exit"]) == (POOL, 495, True, S_MS + 61_900, "tp_1.5x", True)
    assert o1["trig_ms"] == S_MS + 60_000 - 800 and o1["decision_ms"] == S_MS + 60_000
    assert (o1["fill_spend_lamports"], o1["fill_tokens_received"], o1["fill_price_vs_quote_bps"]) == (100_000_000, 9_000_000_000, 55)
    assert o1["led_net_lamports"] == 48_000_000 - 0 and o1["led_fees_lamports"] == 160_000 and o1["led_ok_buy"] is True and o1["sell_landed_ms"] == S_MS + 60_000 + 7_201_900
    assert orders["o2"]["sent"] is False and orders["o2"]["refused_why"] == "lane_full"
    assert orders["o3"]["sent"] is True and orders["o3"]["led_close_reason"] == "buy_expired" and orders["o3"]["led_ok_buy"] is False
    assert "dry1" not in orders
    kinds = meta["counts_by_kind"]
    assert kinds == {"feed_gap_hold": 1, "gap_refusal": 2, "order": 3, "restart": 2, "span_ignored": 1, "start": 1}
    gr = [r for r in rows if r["kind"] == "gap_refusal"]
    assert {r["gap_kind"] for r in gr} == {"host_down", None} and any(r["blocked_until_ms"] for r in gr)
    assert [r for r in rows if r["kind"] == "restart" and r["failed"]]
    assert meta["extract_sha256"] == hashlib.sha256((out / X.EXTRACT_NAME).read_bytes()).hexdigest()
    assert meta["source_sizes"]["bridge_log"] > 0 and meta["as_of_ms"] == AS_OF


def test_start_row_carries_prev_last_ms(world):
    _, argv, out = world
    X.main(argv)
    rows, _ = load(out)
    st = [r for r in rows if r["kind"] == "start"]
    assert len(st) == 1 and set(st[0]) == {"kind", "t_ms", "start_kind", "prev_last_ms"}
    assert st[0]["prev_last_ms"] == S_MS - 5  # the last row of ANY kind before it: here the pre-window entry row of the day file


def test_prev_last_ms_counts_a_row_of_any_kind(world):
    tmp, argv, out = world
    day = tmp / "root" / "decisions" / "l1-floor-v2" / (DAY + ".jsonl")
    day.write_text(jl(host_rows() + [{"kind": "heartbeat", "ms": S_MS + 5_000_000}, {"kind": "start", "ms": S_MS + 5_999_000, "x": {"ms": 1}}]))
    X.main(argv)
    rows, _ = load(out)
    st = sorted((r for r in rows if r["kind"] == "start"), key=lambda r: r["t_ms"])
    assert [r["prev_last_ms"] for r in st][-1] == S_MS + 5_000_000


def test_prev_last_ms_is_last_host_row_before_start():
    host = [{"kind": "entry", "ms": 1000, "order_id": "a", "pool": "P", "meta": {}}, {"kind": "exit", "ms": 2000, "order_id": "a"}, {"kind": "start", "ms": 9000},
            {"kind": "lane_start", "ms": 9500}]
    rows = [r for r in X.build_rows(host, [], [], [], 0) if r["kind"] == "start"]
    assert [(r["t_ms"], r["prev_last_ms"]) for r in rows] == [(9000, 2000), (9500, 9000)]


def test_no_mint_signature_in_extract_and_stdout_is_counts_only(world, capsys):
    tmp, argv, out = world
    X.main(argv)
    so = capsys.readouterr()
    text = so.out + so.err
    for needle in (POOL, MINT, SIG, "o1", "lane_full"):
        assert needle not in text
    body = (out / X.EXTRACT_NAME).read_text() + (out / X.META_NAME).read_text()
    assert MINT not in body and SIG not in body and "OTHERMINT" not in body
    assert POOL in body  # the pool id is a join key and stays inside the extract
    assert "OUT_SHA256=" in text


def test_permissions_and_atomic(world):
    _, argv, out = world
    X.main(argv)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o700
    for n in (X.EXTRACT_NAME, X.META_NAME):
        assert stat.S_IMODE(os.stat(out / n).st_mode) == 0o600
    assert sorted(os.listdir(out)) == sorted([X.EXTRACT_NAME, X.META_NAME])  # no tmp file is left
    X.main(argv)  # a rerun replaces atomically, same bytes
    assert sorted(os.listdir(out)) == sorted([X.EXTRACT_NAME, X.META_NAME])


def test_torn_last_line_counted(world):
    tmp, argv, out = world
    with open(tmp / "bridge.jsonl", "a") as f:
        f.write('{"kind":"buy_landed","ts_ms":')
    X.main(argv)
    _, meta = load(out)
    assert meta["stats"]["torn_tail"] == 1 and meta["stats"]["bad_lines"] == 0


def test_read_only_on_live_side(world):
    tmp, argv, out = world
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in tmp.rglob("*") if p.is_file() and "near" not in p.parts}
    X.main(argv)
    after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in tmp.rglob("*") if p.is_file() and "near" not in p.parts}
    assert before == after
    assert not list(tmp.rglob("*.lock"))


def test_backlog_rows_included_and_deduped(world):
    tmp, argv, out = world
    (tmp / "ledger" / "lane-ledger.jsonl.backlog.floor-lane-1").write_text(jl([ledger_rows()[2]]))  # a duplicate of r3: must not double the net
    X.main(argv)
    rows, meta = load(out)
    o1 = next(r for r in rows if r["kind"] == "order" and r["order_id"] == "o1")
    assert o1["led_net_lamports"] == 48_000_000 and meta["stats"]["ledger_backlog_nonempty"] == 1


def test_refusals(world, monkeypatch, tmp_path):
    tmp, argv, out = world
    monkeypatch.delenv("OSB_EXTRACT_TEST")
    with pytest.raises(SystemExit) as e:
        X.main(argv)
    assert e.value.code == 2  # --test-mode without the env var
    monkeypatch.setenv("OSB_EXTRACT_TEST", "1")
    with pytest.raises(SystemExit):
        X.main(["extract", "--decision-root", str(tmp / "root")])  # custom path without --test-mode
    with pytest.raises(SystemExit):
        X.main(["extract", "--since-hour", "garbage"])
    with pytest.raises(SystemExit):  # a real root under pytest
        X.main(["extract", "--test-mode", "--decision-root", "/var/lib/mal-live/floor-lane/decisions-root", "--out-dir", str(tmp / "n2")])
    link = tmp / "linkroot"
    os.symlink(tmp / "root", link)
    a2 = list(argv)
    a2[a2.index("--decision-root") + 1] = str(link)
    with pytest.raises(SystemExit):
        X.main(a2)  # a symlink in the decision root path


def test_symlinked_day_dir_refused(world):
    tmp, argv, out = world
    os.rename(tmp / "root" / "decisions" / "l1-floor-v2", tmp / "real-arm")
    os.symlink(tmp / "real-arm", tmp / "root" / "decisions" / "l1-floor-v2")
    with pytest.raises(SystemExit):
        X.main(argv)


def test_push_order_modes_and_acl(world, capsys):
    tmp, argv, out = world
    X.main(argv)
    sent = []
    assert X.main(["push", "--test-mode", "--out-dir", str(out)], send_fn=lambda n, b, s: sent.append((n, b, s))) == 0
    assert [n for n, _, _ in sent] == [X.EXTRACT_NAME, X.META_NAME]
    assert all(hashlib.sha256(b).hexdigest() == s for _, b, s in sent)
    sc = X.remote_script(X.EXTRACT_NAME, 10, "a" * 64)
    assert sc.index("setfacl -b -k") < sc.index("cat >>") and sc.index("chmod 700") < sc.index("cat >>")
    assert "/data2/mal/scratch/floor-open-sim/live-extract" in sc and "umask 077" in sc and "chmod 600" in sc and "sha256sum" in sc
    out_text = capsys.readouterr().out
    assert POOL not in out_text and "EXTRACT_PUSHED" in out_text


def test_push_refuses_loose_mode_and_sha_mismatch(world):
    tmp, argv, out = world
    X.main(argv)
    os.chmod(out / X.EXTRACT_NAME, 0o644)
    with pytest.raises(SystemExit):
        X.main(["push", "--test-mode", "--out-dir", str(out)], send_fn=lambda *a: None)
    os.chmod(out / X.EXTRACT_NAME, 0o600)
    with open(out / X.EXTRACT_NAME, "ab") as f:
        f.write(b"x\n")
    with pytest.raises(SystemExit):
        X.main(["push", "--test-mode", "--out-dir", str(out)], send_fn=lambda *a: None)


def test_push_failure_returns_1(world, capsys):
    tmp, argv, out = world
    X.main(argv)

    def boom(n, b, s):
        raise RuntimeError("send rc=255")

    assert X.main(["push", "--test-mode", "--out-dir", str(out)], send_fn=boom) == 1
    assert POOL not in capsys.readouterr().out


def test_structure_check_prints_names_and_counts_only(world, capsys):
    tmp, argv, out = world
    a = ["structure-check", "--test-mode"] + argv[2:-2]
    a = [x for x in a if x not in ("--out-dir", str(out))]
    assert X.main(a) == 0
    txt = capsys.readouterr().out
    assert "meta.signal_slot present 3/4" in txt and "alias spend_lamports: spend_lamports present 1/1" in txt
    for needle in (POOL, MINT, SIG):
        assert needle not in txt
    assert not out.exists()  # nothing written


# ------------------------------------------------------------------------------------------------------------------------------ review round 2
def test_missing_sources_refuse(world):
    tmp, argv, out = world
    for victim in ("bridge.jsonl", "fills.jsonl"):
        os.rename(tmp / victim, tmp / (victim + ".gone"))
        with pytest.raises(SystemExit):
            X.main(argv)
        os.rename(tmp / (victim + ".gone"), tmp / victim)
    os.rename(tmp / "ledger" / "lane-ledger.jsonl", tmp / "ledger" / "x")
    with pytest.raises(SystemExit):
        X.main(argv)
    os.rename(tmp / "ledger" / "x", tmp / "ledger" / "lane-ledger.jsonl")
    os.remove(tmp / "root" / "decisions" / "l1-floor-v2" / (DAY + ".jsonl"))
    with pytest.raises(SystemExit):
        X.main(argv)  # no decision day file read
    assert not out.exists()


def test_torn_and_bad_lines_counted_per_source(world):
    tmp, argv, out = world
    with open(tmp / "ledger" / "lane-ledger.jsonl", "a") as f:
        f.write('{"ev":"x","bot_id":"floor-lane-1"\n')  # a bad line with a newline
        f.write('{"ev":"exit","bot_id":"floor-lane-1","ts_')  # a torn last line
    X.main(argv)
    _, meta = load(out)
    st = meta["stats"]
    assert st["bad_lines_ledger"] == 1 and st["torn_tail_ledger"] == 1


def test_bridge_only_orders_are_dropped_and_counted(world):
    tmp, argv, out = world
    with open(tmp / "bridge.jsonl", "a") as f:
        f.write(json.dumps({"kind": "buy_sent", "ts_ms": S_MS + 100_000, "mode": "live", "order_id": "foreign", "stake_lamports": 1}) + "\n")
    X.main(argv)
    rows, meta = load(out)
    assert "foreign" not in {r.get("order_id") for r in rows} and meta["stats"]["dropped_bridge_only"] == 1


def test_writeoff_kind_and_sell_failed_carried(world):
    tmp, argv, out = world
    with open(tmp / "bridge.jsonl", "a") as f:
        f.write(json.dumps({"kind": "sell_failed", "ts_ms": S_MS + 61_000 + 7_300_000, "mode": "live", "order_id": "o1", "label": "x"}) + "\n")
    X.main(argv)
    rows, _ = load(out)
    orders = {r["order_id"]: r for r in rows if r["kind"] == "order"}
    assert orders["o3"]["led_writeoff"] == "buy_expired" and orders["o1"]["led_writeoff"] is None and orders["o1"]["sell_failed"] is True


def test_symlinked_near_dir_refused(world):
    tmp, argv, out = world
    target = tmp / "victim"
    target.mkdir(mode=0o755)
    os.symlink(target, out)
    with pytest.raises(SystemExit):
        X.main(argv)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o755  # the target of the link was not chmod-ed


def test_remote_script_runs_and_fails_closed(tmp_path, monkeypatch):
    import shutil
    import subprocess
    if not (shutil.which("setfacl") and shutil.which("getfacl")):
        pytest.skip("acl tools missing")
    far = tmp_path / "parent" / "live-extract"
    monkeypatch.setattr(X, "FAR_DIR", str(far))
    (tmp_path / "parent").mkdir()
    data = b"hello\n" * 100
    sha = hashlib.sha256(data).hexdigest()
    script = X.remote_script("f.jsonl", len(data), sha)
    r = subprocess.run(["sh", "-c", script], input=data, capture_output=True)
    assert r.returncode == 0
    assert stat.S_IMODE(os.stat(far).st_mode) == 0o700 and stat.S_IMODE(os.stat(far / "f.jsonl").st_mode) == 0o600 and (far / "f.jsonl").read_bytes() == data
    # a truncated stream: rc 1, no new final file, no tmp left
    r2 = subprocess.run(["sh", "-c", X.remote_script("g.jsonl", len(data), sha)], input=data[:10], capture_output=True)
    assert r2.returncode == 1 and not (far / "g.jsonl").exists() and not [p for p in os.listdir(far) if ".tmp" in p]
    # setfacl missing: fail closed (no final file)
    r3 = subprocess.run(["/bin/sh", "-c", X.remote_script("h.jsonl", len(data), sha)], input=data, capture_output=True, env={"PATH": "/nonexistent"})
    assert r3.returncode != 0 and not (far / "h.jsonl").exists()


# ------------------------------------------------------------------------------------------------------------------------------ follow-up to #1378
def _host_with(tmp, mutate):
    rows = host_rows()
    mutate(rows)
    (tmp / "root" / "decisions" / "l1-floor-v2" / (DAY + ".jsonl")).write_text(jl(rows))


@pytest.mark.parametrize("field,meta_add", [("trig_slot", {"trigger_slot": 495}), ("trig_ms", {"trigger_ms": 5})])
def test_two_alias_names_on_one_row_refuse_naming_the_field_only(world, capsys, field, meta_add):
    tmp, argv, out = world
    _host_with(tmp, lambda rows: rows[1]["meta"].update(meta_add))
    with pytest.raises(SystemExit) as e:
        X.main(argv)
    assert e.value.code == 2
    so = capsys.readouterr()
    text = so.out + so.err
    assert "field " + field in text
    for needle in (POOL, MINT, SIG, "o1"):
        assert needle not in text
    assert not out.exists()


def test_alias_names_split_over_rows_of_one_kind_do_not_refuse(world):
    tmp, argv, out = world

    def mut(rows):  # o1 uses signal_slot, o2 uses trigger_slot: each row has ONE name, so no row conflicts
        del rows[2]["meta"]["signal_slot"]
        rows[2]["meta"]["trigger_slot"] = 600
    _host_with(tmp, mut)
    assert X.main(argv) == 0


def test_entry_refused_why_and_buy_port_refused_label_in_one_window_extract_fine(world):
    tmp, argv, out = world
    with open(tmp / "bridge.jsonl", "a") as f:
        f.write(json.dumps({"kind": "buy_port_refused", "ts_ms": S_MS + 70_000, "mode": "live", "order_id": "o3", "label": "port_x"}) + "\n")
    assert X.main(argv) == 0
    rows, _ = load(out)
    orders = {r["order_id"]: r for r in rows if r["kind"] == "order"}
    assert orders["o2"]["refused_why"] == "lane_full" and orders["o3"]["refused_why"] == "port_x"


def test_one_refusal_row_with_why_and_label_refuses(world, capsys):
    tmp, argv, out = world
    with open(tmp / "bridge.jsonl", "a") as f:
        f.write(json.dumps({"kind": "entry_refused", "ts_ms": S_MS + 70_000, "mode": "live", "order_id": "o3", "why": "a", "label": "b"}) + "\n")
    with pytest.raises(SystemExit):
        X.main(argv)
    assert "field refusal_why" in capsys.readouterr().err


def test_fill_alias_conflict_refuses(world, capsys):
    tmp, argv, out = world
    rows = fills_rows()
    rows[0]["tokens"] = 1
    (tmp / "fills.jsonl").write_text(jl(rows))
    with pytest.raises(SystemExit) as e:
        X.main(argv)
    err = capsys.readouterr().err
    assert e.value.code == 2 and "field tokens_received" in err and MINT not in err and SIG not in err


def test_single_alias_per_field_still_extracts(world):
    tmp, argv, out = world

    def mut(rows):  # every entry on the second name: fine
        for r in rows:
            if r["kind"] == "entry" and "signal_slot" in r.get("meta", {}):
                r["meta"]["trigger_slot"] = r["meta"].pop("signal_slot")
    _host_with(tmp, mut)
    assert X.main(argv) == 0


def test_structure_check_counts_order_id_shapes_never_the_ids(world, capsys):
    tmp, argv, out = world
    rows = host_rows()
    rows.append({"kind": "exit", "ms": S_MS + 90_000, "order_id": "BAD-ID-WITH-A-VERY-LONG-TAIL-0123456789", "reason": "x"})
    (tmp / "root" / "decisions" / "l1-floor-v2" / (DAY + ".jsonl")).write_text(jl(rows))
    a = [x for x in ["structure-check", "--test-mode"] + argv[2:-2] if x not in ("--out-dir", str(out))]
    assert X.main(a) == 0
    txt = capsys.readouterr().out
    assert "not matching 1" in txt and "BAD-ID" not in txt
    for needle in (POOL, MINT, SIG):
        assert needle not in txt


def test_oid_rule_equals_the_recons():
    sys.path.insert(0, str(ROOT / "scripts" / "research"))
    import osb_recon as R
    assert X.OID_OK.pattern == R.OID_OK.pattern
