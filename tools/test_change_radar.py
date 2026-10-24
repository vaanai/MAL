"""Tests for tools/change_radar.py. No network.

Recorded fixtures (tools/fixtures/change_radar/, public mainnet RPC getMultipleAccounts, 2026-10-24 ~17:50Z): the 14 program accounts of
tools/change_radar_watch.json and their programdata slices (45 bytes), both stored in watch-file order. Everything else is synthetic and built here.
"""
from __future__ import annotations

import base64
import copy
import json
import shutil
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import change_radar as cr
from tools.pump_structure_monitor import EVENT_CPI_TAG, b58decode, b58encode

FIX = Path(__file__).with_name("fixtures") / "change_radar"
WATCH = json.loads(cr.DEFAULT_WATCH.read_text())
SEEN_2026_10_24 = {  # deploy slots read on 2026-10-24 (DESIGN section 1 table)
    "pump": 458921440, "pumpswap": 458921517, "pump_fees": 456132171, "meteora_dbc": 457502600, "meteora_damm_v2": 457080014,
    "raydium_launchlab": 446221802, "raydium_cpmm": 452376460, "meteora_damm_v1": 457247107, "raydium_amm_v4": 445763204,
    "raydium_clmm": 459932378, "moonshot": 350198871, "boop": 362618777, "heaven": 410580266, "jupiter_v6": 459479624,
}
NOW = datetime(2026, 10, 25, 7, 30, tzinfo=timezone.utc).timestamp()


def b64(raw: bytes) -> list:
    return [base64.b64encode(raw).decode(), "base64"]


def programdata_slice(slot: int, authority: bytes | None) -> bytes:
    return struct.pack("<IQ", 3, slot) + (b"\x01" + authority if authority else b"\x00" + b"\x00" * 32)


class FakeClient:
    """Routes calls by method. `pd` overrides programdata slices by programdata address; `accounts` by address; `txs` by signature."""

    def __init__(self, *, pd: dict | None = None, accounts: dict | None = None, sigs: dict | None = None, txs: dict | None = None,
                 fail: set | None = None) -> None:
        self.prog = json.loads((FIX / "program_accounts_20261024.json").read_text())
        slices = json.loads((FIX / "programdata_slices_20261024.json").read_text())["value"]
        pdas = [b58encode(base64.b64decode(v["data"][0])[4:36]) for v in self.prog["value"]]
        self.slices = dict(zip(pdas, slices))
        self.pd = pd or {}
        self.accounts = accounts or {}
        self.sigs = sigs or {}
        self.txs = txs or {}
        self.fail = fail or set()
        self.calls: list[tuple[str, list]] = []
        self.total_calls = 0

    def call(self, method: str, params):
        self.calls.append((method, list(params)))
        self.total_calls += 1
        if method in self.fail:
            raise cr.RpcUnreachable(f"{method} down")
        if method == "getMultipleAccounts":
            keys, opts = params
            if keys == [s["id"] for s in WATCH["programs"].values()]:
                return copy.deepcopy(self.prog)
            if "dataSlice" in opts:
                vals = []
                for k in keys:
                    vals.append({"data": b64(self.pd[k]), "executable": False, "owner": cr.LOADER_UPGRADEABLE} if k in self.pd else self.slices[k])
                return {"context": {"slot": 1}, "value": vals}
            return {"context": {"slot": 1}, "value": [({"data": b64(self.accounts[k])} if self.accounts.get(k) is not None else None) for k in keys]}
        if method == "getBlockTime":
            return 1_792_000_000
        if method == "getSignaturesForAddress":
            return self.sigs.get(params[0], [])
        if method == "getTransaction":
            return self.txs.get(params[0])
        raise AssertionError(method)


def pd_addr(name: str) -> str:
    prog = json.loads((FIX / "program_accounts_20261024.json").read_text())
    i = list(WATCH["programs"]).index(name)
    raw = base64.b64decode(prog["value"][i]["data"][0])
    return b58encode(raw[4:36])


def account_bytes() -> dict:
    return {a["address"]: bytes([i]) * 40 for i, a in enumerate(WATCH["accounts"].values())}


# ===================================================================================================== URL / path refusals
@pytest.mark.parametrize("url", ["https://mainnet.helius-rpc.com", "https://rpc.example.com/?api-key=x", "https://u:p@rpc.example.com",
                                 "https://rpc.example.com/abc", "ws://rpc.example.com", "nonsense"])
def test_rpc_url_refused(url):
    with pytest.raises(cr.RadarError):
        cr.check_rpc_url(url)


def test_rpc_url_public_ok():
    assert cr.check_rpc_url("https://api.mainnet-beta.solana.com") == "api.mainnet-beta.solana.com"
    assert cr.check_rpc_url("https://api.mainnet-beta.solana.com/") == "api.mainnet-beta.solana.com"


@pytest.mark.parametrize("p", ["/data/mal/holdout/x", "/data/fwdv1/tape", "/data/mal/h5-shadow", "/x/probe", "/data2/mal/venue-tape-backfill",
                               "/data/sealed", "/x/HOLDOUT_LEDGER"])
def test_tape_dir_refused(p):
    with pytest.raises(cr.RadarError):
        cr.check_tape_dir(p)


def test_symlink_into_forbidden_dir_refused(tmp_path):
    real = tmp_path / "fwdv1-data"
    real.mkdir()
    link = tmp_path / "innocent"
    link.symlink_to(real)
    with pytest.raises(cr.RadarError):
        cr.check_tape_dir(str(link))


# ===================================================================================================== W1/W2 programs
def test_programs_recorded_layout_baseline():
    errors: list[str] = []
    obs, ch = cr.stage_programs(FakeClient(), WATCH, {}, errors)
    assert ch == [] and errors == []
    assert {n: o["deploy_slot"] for n, o in obs.items()} == SEEN_2026_10_24
    assert all(o["status"] == "ok" and o["first_seen"] and o["authority"] for o in obs.values())


def _state_from(obs):
    return {n: {k: o.get(k) for k in ("id", "programdata", "deploy_slot", "authority", "deploy_utc")} for n, o in obs.items()}


def test_program_upgrade_by_tier_and_writes():
    base, _ = cr.stage_programs(FakeClient(), WATCH, {}, [])
    prev = _state_from(base)
    auth = b58decode(base["meteora_dbc"]["authority"])
    pd = {pd_addr("meteora_dbc"): programdata_slice(SEEN_2026_10_24["meteora_dbc"] + 1000, auth),
          pd_addr("boop"): programdata_slice(SEEN_2026_10_24["boop"] + 5, b58decode(base["boop"]["authority"])),
          pd_addr("jupiter_v6"): programdata_slice(SEEN_2026_10_24["jupiter_v6"] + 5, b58decode(base["jupiter_v6"]["authority"]))}
    sigs = {pd_addr("meteora_dbc"): [{"slot": SEEN_2026_10_24["meteora_dbc"] + 1000, "err": None},
                                     {"slot": SEEN_2026_10_24["meteora_dbc"] + 400, "err": None},
                                     {"slot": SEEN_2026_10_24["meteora_dbc"] + 300, "err": {"x": 1}},
                                     {"slot": SEEN_2026_10_24["meteora_dbc"], "err": None}]}
    obs, ch = cr.stage_programs(FakeClient(pd=pd, sigs=sigs), WATCH, prev, [])
    by = {c["subject"]: c for c in ch}
    assert by["meteora_dbc"]["sev"] == "high" and by["meteora_dbc"]["kind"] == "program_upgrade"
    assert "writes_since=2" in by["meteora_dbc"]["detail"] and by["meteora_dbc"]["at_utc"].startswith("2026-")
    assert by["boop"]["sev"] == "med" and by["jupiter_v6"]["sev"] == "low"
    assert len(ch) == 3


def test_writes_since_unread_when_page_does_not_reach_prev():
    sigs = [{"slot": 1000 + i, "err": None} for i in range(100)]
    assert cr._writes_since(FakeClient(sigs={"PD": sigs}), "PD", 10, []) is None


def test_authority_frozen_and_program_gone():
    base, _ = cr.stage_programs(FakeClient(), WATCH, {}, [])
    prev = _state_from(base)
    pd = {pd_addr("pump"): programdata_slice(SEEN_2026_10_24["pump"], None)}
    client = FakeClient(pd=pd)
    client.prog["value"][list(WATCH["programs"]).index("heaven")] = None
    obs, ch = cr.stage_programs(client, WATCH, prev, [])
    kinds = {(c["kind"], c["subject"], c["sev"]) for c in ch}
    assert ("authority_change", "pump", "high") in kinds and ("program_gone", "heaven", "high") in kinds
    assert obs["heaven"]["status"] == "missing"


# ===================================================================================================== W3 accounts
def test_accounts_change_and_close():
    acc = account_bytes()
    obs, ch = cr.stage_accounts(FakeClient(accounts=acc), WATCH, {})
    assert ch == [] and all(o["first_seen"] for o in obs.values())
    prev = {n: {k: o[k] for k in ("address", "exists", "len", "sha256")} for n, o in obs.items()}
    acc2 = dict(acc)
    addrs = [a["address"] for a in WATCH["accounts"].values()]
    acc2[addrs[1]] = b"\xff" * 40
    acc2[addrs[2]] = None
    _, ch2 = cr.stage_accounts(FakeClient(accounts=acc2), WATCH, prev)
    assert sorted((c["kind"], c["sev"]) for c in ch2) == [("account_closed", "high"), ("config_change", "high")]


# ===================================================================================================== W4 vocabulary
PID = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
OTHER = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"


def make_tx(ix_name: str, evt_disc: bytes, data_disc: bytes = b"\x01" * 8, outer_disc: bytes = b"\x02" * 8) -> dict:
    logs = [f"Program {OTHER} invoke [1]", "Program log: Instruction: Route",
            f"Program {PID} invoke [2]", f"Program log: Instruction: {ix_name}",
            "Program data: " + base64.b64encode(data_disc + b"rest").decode(),
            f"Program {PID} invoke [3]", f"Program {PID} success",
            f"Program {PID} success", f"Program {OTHER} success"]
    keys = ["Signer1111111111111111111111111111111111111", OTHER, PID]
    return {"meta": {"logMessages": logs, "loadedAddresses": {"writable": [], "readonly": []},
                     "innerInstructions": [{"index": 0, "instructions": [
                         {"programIdIndex": 2, "data": b58encode(outer_disc + b"args")},
                         {"programIdIndex": 2, "data": b58encode(EVENT_CPI_TAG + evt_disc + b"body")}]}]},
            "transaction": {"message": {"accountKeys": keys, "instructions": [{"programIdIndex": 1, "data": b58encode(b"\x09" * 8)}]}}}


def test_vocab_from_tx_frames_and_discriminators():
    keys = cr.vocab_from_tx(make_tx("Swap2", b"\xaa" * 8), PID)
    assert keys == {"ix:Swap2", "data:" + "01" * 8, "ixd:" + "02" * 8, "evt:" + "aa" * 8}
    assert "ix:Route" not in keys  # the router's own instruction line is not the program's


def _vocab_watch():
    return {"programs": {"meteora_dbc": {"id": PID, "tier": "core", "vocab": True}}}


def _vocab_client(evt: bytes, n: int = 3):
    sigs = {PID: [{"signature": f"s{i}", "err": None} for i in range(n)] + [{"signature": "bad", "err": {"x": 1}}]}
    return FakeClient(sigs=sigs, txs={f"s{i}": make_tx("Swap2", evt) for i in range(n)})


def test_vocab_warmup_then_new_event_alert():
    vocab, runs = {}, {}
    for _ in range(cr.VOCAB_WARMUP_RUNS):
        obs, ch = cr.stage_vocab(_vocab_client(b"\xaa" * 8), _vocab_watch(), vocab, {}, 20, [], runs)
        assert ch == [] and obs["meteora_dbc"]["warmup"]
        vocab, runs = cr.merge_vocab(vocab, runs, obs)
    obs, ch = cr.stage_vocab(_vocab_client(b"\xaa" * 8), _vocab_watch(), vocab, {}, 20, [], runs)
    assert ch == []
    obs, ch = cr.stage_vocab(_vocab_client(b"\xbb" * 8), _vocab_watch(), vocab, {}, 20, [], runs)
    assert len(ch) == 1 and ch[0]["kind"] == "new_vocab" and ch[0]["sev"] == "med" and ch[0]["keys"] == ["evt:" + "bb" * 8]
    _, ch = cr.stage_vocab(_vocab_client(b"\xbb" * 8), _vocab_watch(), vocab, {"meteora_dbc": {"upgraded": True}}, 20, [], runs)
    assert ch[0]["sev"] == "high"


def test_vocab_rpc_down_is_unread_not_change():
    client = _vocab_client(b"\xaa" * 8)
    client.fail = {"getSignaturesForAddress"}
    errors: list[str] = []
    obs, ch = cr.stage_vocab(client, _vocab_watch(), {"meteora_dbc": ["ix:Swap2"]}, {}, 20, errors, {"meteora_dbc": 5})
    assert ch == [] and obs["meteora_dbc"]["status"] == "unread" and errors


# ===================================================================================================== W5 tapes
SENTINELS = ("SENTINELMINT111", "SENTINELWALLET22", "987654.321")


def arena_rows(launches: int, trades: int, extra: list | None = None) -> list[str]:
    rows = [json.dumps({"v": 1, "type": "hb", "prog": "pumpfun", "slot": 1}, separators=(",", ":"))]
    rows += [json.dumps({"v": 1, "type": "create", "prog": "pumpfun", "slot": 1, "mint": SENTINELS[0]}, separators=(",", ":"))] * launches
    rows += [json.dumps({"v": 1, "type": "trade", "prog": "pumpswap", "slot": 1, "user": SENTINELS[1], "price_sol": float(SENTINELS[2])},
                        separators=(",", ":"))] * trades
    rows += extra or []
    return rows


def dbc_rows(swap_name: str, n: int) -> list[str]:
    base = {"v": 1, "venue": "meteora_dbc", "slot": 1, "t_recv_ms": 1, "signature": SENTINELS[1]}
    rows = [json.dumps({**base, "type": "ix", "event_index": 0, "ix": [swap_name]})] * n
    rows += [json.dumps({**base, "type": "ix", "event_index": 0, "ix": ["InitializeVirtualPoolWithSplToken"]})] * 2
    rows += [json.dumps({**base, "type": "ix", "failed": True, "ix": ["Swap9"]})]
    return rows


def write_day(root: Path, day: str, hours: int, rows: list[str], zst_hours: int = 0) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for h in range(hours):
        p = root / f"tape-{day}T{h:02d}.jsonl"
        p.write_text("\n".join(rows) + "\n")
        if h < zst_hours:
            subprocess.run(["zstd", "-q", "--rm", str(p)], check=True)


def test_count_arena_fast_path_and_fallback(tmp_path):
    p = tmp_path / "tape-2026-10-20T00.jsonl"
    p.write_text("\n".join(arena_rows(2, 3, ['{"type": "complete", "v": 1, "prog": "pumpfun"}', "not json"])) + "\n")
    c = cr.count_arena_file(p)
    assert c == {"pumpfun/hb": 1, "pumpfun/create": 2, "pumpswap/trade": 3, "pumpfun/complete": 1, "bad_line": 1}


def test_count_venue_file(tmp_path):
    p = tmp_path / "tape-2026-10-20T00.jsonl"
    p.write_text("\n".join(dbc_rows("Swap2", 4) + [json.dumps({"type": "event_undecoded", "disc": "abcd", "venue": "x"})]) + "\n")
    c = cr.count_venue_file(p)
    assert c["ix:Swap2"] == 4 and c["failed"] == 1 and "ix:Swap9" not in c and c["undecoded:abcd"] == 1 and c["ix"] == 7


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd CLI missing")
def test_zst_hours_and_zst_preferred(tmp_path):
    root = tmp_path / "arena"
    write_day(root, "2026-10-20", 3, arena_rows(1, 1), zst_hours=2)
    (root / "tape-2026-10-20T00.jsonl").write_text("garbage\n")  # stale source next to its .zst: the .zst wins
    days = cr.tape_days(root)
    assert sorted(p.name for p in days["2026-10-20"].values()) == ["tape-2026-10-20T00.jsonl.zst", "tape-2026-10-20T01.jsonl.zst", "tape-2026-10-20T02.jsonl"]
    total = sum((cr.count_arena_file(p) for p in days["2026-10-20"].values()), start=cr.Counter())
    assert total["pumpfun/create"] == 3 and "bad_line" not in total


def test_closed_days_and_lookback(tmp_path):
    root = tmp_path / "arena"
    for d in ("2026-10-10", "2026-10-23", "2026-10-24", "2026-10-25"):
        write_day(root, d, 1, arena_rows(1, 1))
    recs = cr.stage_tapes({"arena": (root, cr.count_arena_file)}, set(), NOW, 8, [], cr.load_seals_dict(NO_SEALS))
    assert [r["day"] for r in recs] == ["2026-10-23", "2026-10-24"]  # 10-10 outside lookback, 10-25 still open
    recs2 = cr.stage_tapes({"arena": (root, cr.count_arena_file)}, {("arena", "2026-10-23")}, NOW, 8, [], cr.load_seals_dict(NO_SEALS))
    assert [r["day"] for r in recs2] == ["2026-10-24"]


def _rec(src, day, counts, hours=24):
    return {"source": src, "day": day, "hours": hours, "coverage_short": hours < cr.MIN_HOURS, "counts": counts}


def test_new_and_gone_key_swap3_case():
    hist = [_rec("venue:meteora_dbc", f"2026-10-1{i}", {"ix:Swap2": 50000, "ix": 50000, "hb": 1440}) for i in range(1, 5)]
    new = _rec("venue:meteora_dbc", "2026-10-16", {"ix:Swap3": 48000, "ix": 48000, "hb": 1440})
    ch = cr.tape_changes([new], hist + [new])
    kinds = {(c["kind"], c["subject"]) for c in ch}
    assert ("new_key", "venue:meteora_dbc ix:Swap3") in kinds and ("gone_key", "venue:meteora_dbc ix:Swap2") in kinds
    assert all(c["sev"] == "high" for c in ch)


def test_shift_thresholds_coverage_and_baseline_short():
    hist = [_rec("arena", f"2026-10-1{i}", {"pumpfun/create": 30000, "pumpfun/gap": 10}) for i in range(1, 5)]
    assert cr.tape_changes([_rec("arena", "2026-10-16", {"pumpfun/create": 40000, "pumpfun/gap": 900})], hist) == []  # x1.33; gap is internal
    ch = cr.tape_changes([_rec("arena", "2026-10-16", {"pumpfun/create": 50000})], hist)
    assert [(c["kind"], c["sev"]) for c in ch] == [("count_shift", "med")]
    ch = cr.tape_changes([_rec("arena", "2026-10-16", {"pumpfun/create": 9000})], hist)
    assert [(c["kind"], c["sev"]) for c in ch] == [("count_shift", "high")]
    # 12 h day scaled to 24 h: 20000 rows in 12 h = 40000/day -> no alert; but 12 h < 20 h is coverage_short anyway
    assert cr.tape_changes([_rec("arena", "2026-10-16", {"pumpfun/create": 1}, hours=12)], hist) == []
    assert cr.tape_changes([_rec("arena", "2026-10-16", {"pumpfun/create": 1})], hist[:2]) == []  # baseline_short
    small = cr.tape_changes([_rec("arena", "2026-10-16", {"pumpfun/create": 30000, "x/y": 4})], hist)
    assert small == []


def test_share_shift():
    hist = []
    for i in range(1, 5):
        hist += [_rec("arena", f"2026-10-1{i}", {"pumpfun/create": 30000, "pumpfun/trade": 1000}),
                 _rec("venue:meteora_dbc", f"2026-10-1{i}", {"ix:InitializeVirtualPoolWithSplToken": 3000, "ix:Swap2": 1000})]
    new = [_rec("arena", "2026-10-16", {"pumpfun/create": 30000, "pumpfun/trade": 1000}),
           _rec("venue:meteora_dbc", "2026-10-16", {"ix:InitializeVirtualPoolWithSplToken": 15000, "ix:Swap2": 1000})]
    ch = cr.share_changes(new, hist + new)
    subj = {c["subject"] for c in ch}
    assert subj == {"pumpfun launches", "meteora_dbc launches"}
    assert all(c["sev"] == "med" for c in ch)


# ===================================================================================================== W7 monitor
def test_monitor_passthrough_once(tmp_path):
    p = tmp_path / "daily.jsonl"
    rec = {"run_utc": "2026-10-20T07:11:27Z", "halt": {"flags": {"pins_changed": {"halt": True, "reason": "program_pump deploy_slot a -> b"},
                                                              "boost_disabled": {"halt": False}}},
           "warn": {"flags": {"ms_per_slot_moved": {"warn": True, "reason": "267 -> 217"}}}}
    p.write_text("not json\n" + json.dumps(rec) + "\n")
    info, ch = cr.stage_monitor(p, None)
    assert info["run_utc"] == rec["run_utc"]
    assert [(c["kind"], c["sev"], c["subject"]) for c in ch] == [("monitor_halt", "high", "pins_changed"), ("monitor_warn", "low", "ms_per_slot_moved")]
    info2, ch2 = cr.stage_monitor(p, rec["run_utc"])
    assert ch2 == [] and info2["already_reported"]
    assert cr.stage_monitor(tmp_path / "absent", None) == ({"status": "absent"}, [])


# ===================================================================================================== end to end
NO_SEALS = {"schema": "change_radar_seals.v1", "seals": [], "disabled_until": {}}


def _args(tmp_path, arena, venue, monitor, extra=(), seals=NO_SEALS):
    sp = tmp_path / "seals.json"
    if seals is not None:
        sp.write_text(json.dumps(seals))
    else:
        sp = cr.DEFAULT_SEALS
    return ["--out-dir", str(tmp_path / "out"), "--arena-tape", str(arena), "--venue-tape", str(venue), "--monitor", str(monitor), "--seals", str(sp), *extra]


def test_main_end_to_end(tmp_path, capsys):
    arena, venue = tmp_path / "arena", tmp_path / "venue"
    for i in range(18, 24):
        write_day(arena, f"2026-10-{i}", 24, arena_rows(3, 5))
        write_day(venue / "meteora_dbc", f"2026-10-{i}", 24, dbc_rows("Swap2", 3 if i < 23 else 0) + (dbc_rows("Swap3", 3) if i == 23 else []))
    write_day(arena, "2026-10-17", 10, arena_rows(3, 5))  # coverage short
    monitor = tmp_path / "daily.jsonl"
    monitor.write_text(json.dumps({"run_utc": "2026-10-24T07:11:14Z", "halt": {"flags": {}}, "warn": {"flags": {}}}) + "\n")
    acc = account_bytes()
    rc = cr.main(_args(tmp_path, arena, venue, monitor), client=FakeClient(accounts=acc), now=NOW)
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0].startswith("CHANGES 2026-10-25 high=") and "programs 14/14 read, accounts 4/4" in out[0] and "baseline run" in out[0]
    # 3 rows/hour = 72/day: Swap3 new (n >= 5), Swap2 gone needs baseline >= 100 -> not gone; Swap3 new_key is HIGH
    assert any("new_key venue:meteora_dbc ix:Swap3" in ln for ln in out)
    o = tmp_path / "out"
    files = sorted(x.name for x in o.iterdir())
    assert files == ["console-events-2026-10-25.json", "daily-counts.jsonl", "digest-2026-10-25.json", "digest-2026-10-25.md", "radar.jsonl", "state.json"]
    counts = cr.load_jsonl(o / "daily-counts.jsonl")
    assert {(r["source"], r["day"]) for r in counts} == ({("arena", f"2026-10-{i}") for i in range(17, 24)}
                                                         | {("venue:meteora_dbc", f"2026-10-{i}") for i in range(18, 24)})
    ev = json.loads((o / "console-events-2026-10-25.json").read_text())
    assert ev and all(e["kind"] == "finding" and e["id"].startswith("radar-2026-10-25-") and "promotion gate" in e["detail"] for e in ev)
    # outputs carry counts only: no mint, wallet or price value from the rows
    blob = "".join(x.read_text() for x in o.iterdir())
    for s in SENTINELS:
        assert s not in blob
    # second run: nothing new to count, programs unchanged -> no tape or program change
    rc = cr.main(_args(tmp_path, arena, venue, monitor), client=FakeClient(accounts=acc), now=NOW + 3600)
    out2 = capsys.readouterr().out.strip().splitlines()
    assert rc == 0 and out2[-1] == "CHANGES none" and "baseline run" not in out2[0]
    assert len(cr.load_jsonl(o / "radar.jsonl")) == 2


def test_main_no_rpc_and_refusals(tmp_path, capsys):
    arena, venue = tmp_path / "arena", tmp_path / "venue"
    arena.mkdir()
    rc = cr.main(_args(tmp_path, arena, venue, tmp_path / "none", ["--no-rpc"]), client=None, now=NOW)
    assert rc == 0 and capsys.readouterr().out.strip().endswith("CHANGES none")
    assert cr.main(_args(tmp_path, tmp_path / "holdout", venue, "", ["--no-rpc"]), now=NOW) == 2
    assert cr.main(_args(tmp_path, arena, venue, "", ["--rpc", "https://mainnet.helius-rpc.com"]), now=NOW) == 2


def test_rpc_down_is_partial_not_change(tmp_path, capsys):
    arena = tmp_path / "arena"
    arena.mkdir()
    client = FakeClient(fail={"getMultipleAccounts", "getSignaturesForAddress"})
    rc = cr.main(_args(tmp_path, arena, tmp_path / "v", ""), client=client, now=NOW)
    out = capsys.readouterr().out
    assert rc == 0 and "status partial" in out and "CHANGES none" in out


def test_watch_file_ids_valid():
    for n, spec in WATCH["programs"].items():
        assert len(b58decode(spec["id"])) == 32, n
        assert spec["tier"] in cr.TIER_SEVERITY
    for n, spec in WATCH["accounts"].items():
        assert len(b58decode(spec["address"])) == 32, n


# ===================================================================================================== sealed windows (QP verdict on #889)
def _utc(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def test_pinned_seals_file_floor_window():
    seals = cr.load_seals(cr.DEFAULT_SEALS)
    assert seals["seals"] == [{"source": "arena", "lo": _utc("2026-10-22T21:00:00Z"), "hi": _utc("2026-10-26T21:00:00Z")},
                              {"source": "venue:meteora_dbc", "lo": _utc("2026-10-25T00:00:00Z"), "hi": _utc("2026-11-01T00:10:00Z")},
                              {"source": "venue:launchlab", "lo": _utc("2026-10-17T00:00:00Z"), "hi": _utc("2026-10-24T00:00:00Z")}]
    assert cr.source_disabled(seals, "arena", _utc("2026-10-26T20:59:59Z"))
    assert not cr.source_disabled(seals, "arena", _utc("2026-10-26T21:00:00Z"))
    assert not cr.source_disabled(seals, "venue:meteora_dbc", NOW)
    assert [d for d in ("2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27") if cr.day_sealed(seals, "arena", d)] == \
        ["2026-10-22", "2026-10-23", "2026-10-26"]
    assert cr.day_sealed(seals, "arena", "2026-10-24") and cr.day_sealed(seals, "arena", "2026-10-25")
    assert not cr.day_sealed(seals, "venue:meteora_dbc", "2026-10-23")
    dbc = [f"2026-10-{i}" for i in range(24, 32)] + ["2026-11-01", "2026-11-02"]
    assert [d for d in dbc if cr.day_sealed(seals, "venue:meteora_dbc", d)] == dbc[1:-1]  # 10-25 .. 11-01 (11-01 overlaps 00:00-00:10)
    ll = [f"2026-10-{i}" for i in range(16, 26)]
    assert [d for d in ll if cr.day_sealed(seals, "venue:launchlab", d)] == [f"2026-10-{i}" for i in range(17, 24)]


def test_sealed_days_never_opened_and_never_recorded(tmp_path):
    root = tmp_path / "arena"
    for i in range(20, 28):
        write_day(root, f"2026-10-{i}", 24, arena_rows(1, 1))
    opened = []

    def counter(p):
        opened.append(p.name[5:15])
        return cr.count_arena_file(p)

    skipped: list[str] = []
    now = _utc("2026-10-28T01:00:00Z")
    recs = cr.stage_tapes({"arena": (root, counter)}, set(), now, 8, [], cr.load_seals(cr.DEFAULT_SEALS), skipped)
    assert [r["day"] for r in recs] == ["2026-10-20", "2026-10-21", "2026-10-27"]
    assert set(opened) == {"2026-10-20", "2026-10-21", "2026-10-27"}
    assert skipped == [f"arena 2026-10-{i} sealed" for i in range(22, 27)]


def test_venue_sealed_days_never_opened(tmp_path):
    venue = tmp_path / "venue"
    for i in range(16, 32):
        write_day(venue / "launchlab", f"2026-10-{i}", 24, ['{"type": "create"}'])
        write_day(venue / "meteora_dbc", f"2026-10-{i}", 24, dbc_rows("Swap2", 1))
    write_day(venue / "meteora_dbc", "2026-11-01", 24, dbc_rows("Swap2", 1))
    write_day(venue / "meteora_dbc", "2026-11-02", 24, dbc_rows("Swap2", 1))
    opened = []

    def counter(p):
        opened.append((p.parent.name, p.name[5:15]))
        return cr.count_venue_file(p)

    srcs = {k: (root, counter) for k, (root, _) in cr.tape_sources(None, venue).items()}
    skipped: list[str] = []
    recs = cr.stage_tapes(srcs, set(), _utc("2026-11-03T01:00:00Z"), 40, [], cr.load_seals(cr.DEFAULT_SEALS), skipped)
    got = {(r["source"], r["day"]) for r in recs}
    ll_sealed = {f"2026-10-{i}" for i in range(17, 24)}
    dbc_sealed = {f"2026-10-{i}" for i in range(25, 32)} | {"2026-11-01"}
    assert not any(v == "launchlab" and d in ll_sealed for v, d in opened)
    assert not any(v == "meteora_dbc" and d in dbc_sealed for v, d in opened)
    assert ("venue:launchlab", "2026-10-16") in got and ("venue:launchlab", "2026-10-24") in got
    assert ("venue:meteora_dbc", "2026-10-24") in got and ("venue:meteora_dbc", "2026-11-02") in got
    assert not any(d in ll_sealed for s, d in got if s == "venue:launchlab") and not any(d in dbc_sealed for s, d in got if s == "venue:meteora_dbc")
    assert len(skipped) == len(ll_sealed) + len(dbc_sealed)


def test_arena_disabled_before_seal_end_not_even_listed(tmp_path, monkeypatch, capsys):
    arena, venue = tmp_path / "arena", tmp_path / "venue"
    for i in range(18, 24):
        write_day(arena, f"2026-10-{i}", 24, arena_rows(3, 5))
    write_day(venue / "launchlab", "2026-10-24", 24, ['{"type": "create"}'])
    listed = []
    real = cr.tape_days
    monkeypatch.setattr(cr, "tape_days", lambda root: listed.append(Path(root).name) or real(root))
    rc = cr.main(_args(tmp_path, arena, venue, "", ["--no-rpc"], seals=None), now=NOW)
    out = capsys.readouterr().out
    assert rc == 0 and "arena" not in listed and listed == ["launchlab"]
    counts = cr.load_jsonl(tmp_path / "out" / "daily-counts.jsonl")
    assert [r["source"] for r in counts] == ["venue:launchlab"] and "tape skipped 1" in out


@pytest.mark.parametrize("body", [None, "not json", json.dumps({"seals": [{"source": "arena", "start": "2026-10-26T21:00:00Z", "end": "2026-10-22T21:00:00Z"}]}),
                                  json.dumps({"seals": [{"source": "arena", "start": "bad", "end": "2026-10-22T21:00:00Z"}]}), json.dumps({"x": 1})])
def test_seals_file_fail_closed(tmp_path, body):
    sp = tmp_path / "seals.json"
    if body is not None:
        sp.write_text(body)
    arena = tmp_path / "arena"
    arena.mkdir()
    args = ["--out-dir", str(tmp_path / "out"), "--arena-tape", str(arena), "--venue-tape", "", "--monitor", "", "--seals", str(sp), "--no-rpc"]
    assert cr.main(args, now=NOW) == 2
    assert not (tmp_path / "out" / "daily-counts.jsonl").exists()


def test_stage_tapes_requires_seals(tmp_path):
    with pytest.raises(cr.RadarError):
        cr.stage_tapes({}, set(), NOW, 8, [])
