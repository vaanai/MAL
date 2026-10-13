"""Tests for tools/boost_slice_watch.py. No network: a constructed chain sits behind the real RpcClient.

Everything here is constructed from the layouts the monitor and observe/trade_decode.py decode (migrate event, InitBoost,
BoostBuyAndBurn event, WSOL token balances). Only the PumpSwap GlobalConfig account is the recorded public fixture of
tools/fixtures/pump_structure_monitor/ (it supplies the keeper key). No recorded per-pool responses are used.
"""
from __future__ import annotations

import base64
import json
import struct
from collections import Counter
from pathlib import Path

import pytest

from tools import boost_slice_watch as w
from tools import pump_structure_monitor as m

FX = Path(__file__).parent / "fixtures" / "pump_structure_monitor"
KEEPER = "HTVZVEQMBsNanubDPTs3CxDAEGNFQHJY8c1441iy2S5r"
STRANGER = m.b58encode(bytes([9, 9]) + bytes(30))
NOW = 1_790_100_000
BT0 = 1_790_000_000
BBB_DISC = bytes.fromhex("3f451c16305cc2b9")


def k(i: int, j: int = 0) -> bytes:
    return bytes([i, j]) + bytes(30)


def data_line(blob: bytes) -> str:
    return "Program data: " + base64.b64encode(blob).decode()


def migration_blob(mint, curve, pool, quote) -> bytes:
    return m.DISC_MIGRATE + bytes(32) + mint + struct.pack("<QQQ", 1, 2, 3) + curve + struct.pack("<q", BT0) + pool + quote


def bbb_blob(used: int) -> bytes:
    blob = BBB_DISC + struct.pack("<q", BT0) + bytes(128) + struct.pack("<QQQ", used, used, 5) + bytes(16) + struct.pack("<QQQ", 0, 0, 0)
    assert len(blob) == 208
    return blob


def tx_of(slot, bt, logs, keys, signers=1, token_pre=None, token_post=None) -> dict:
    return {
        "slot": slot, "blockTime": bt,
        "transaction": {"message": {"accountKeys": keys, "header": {"numRequiredSignatures": signers}}},
        "meta": {"logMessages": logs, "preBalances": [0] * len(keys), "postBalances": [0] * len(keys), "innerInstructions": [],
                 "preTokenBalances": token_pre or [], "postTokenBalances": token_post or []},
    }


def tb(idx, amount):
    return {"accountIndex": idx, "mint": m.WSOL_MINT, "uiTokenAmount": {"amount": str(amount)}}


class Chain:
    """Fake node transport: signature lists per address, transactions per signature, account entries."""

    def __init__(self):
        self.lists: dict[str, list[dict]] = {}
        self.txs: dict[str, dict] = {}
        self.accounts: dict[str, dict] = {}
        self.methods: Counter = Counter()
        self.tx_fetches: Counter = Counter()
        gc = json.loads((FX / "config_accounts.json").read_text())
        self.config = gc["value"][2]
        self.n_sig = 0
        self.fee_list: list[dict] = []

    def sig(self) -> str:
        self.n_sig += 1
        return f"sig{self.n_sig:06d}"

    def add(self, address, slot, bt, tx=None, err=None, sig=None) -> str:
        sig = sig or self.sig()
        self.lists.setdefault(address, []).append({"signature": sig, "slot": slot, "blockTime": bt, "err": err})
        if tx is not None:
            self.txs[sig] = tx
        return sig

    def __call__(self, url, body, timeout):
        req = json.loads(body)
        meth, params = req["method"], req["params"]
        self.methods[meth] += 1
        if meth == "getAccountInfo":
            result = {"context": {"slot": 1}, "value": self.config}
        elif meth == "getSignaturesForAddress":
            lst = sorted(self.lists.get(params[0], []), key=lambda x: (x["slot"], x["signature"]), reverse=True)
            opts = params[1]
            if opts.get("before"):
                idx = next(i for i, x in enumerate(lst) if x["signature"] == opts["before"])
                lst = lst[idx + 1:]
            result = lst[: opts.get("limit", 1000)]
        elif meth == "getTransaction":
            self.tx_fetches[params[0]] += 1
            result = self.txs.get(params[0])
        elif meth == "getMultipleAccounts":
            result = {"context": {"slot": 1}, "value": [self.accounts.get(a) for a in params[0]]}
        else:
            raise AssertionError(f"unexpected {meth}")
        return 200, {}, json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


def client_for(chain, **kw) -> m.RpcClient:
    return m.RpcClient("https://rpc.test/", min_interval=0, sleep=lambda s: None, transport=chain, **kw)


def pool_acct(mayhem: bool) -> dict:
    data = bytearray(300)
    data[m.POOL_MAYHEM_OFFSET] = 1 if mayhem else 0
    return {"data": [base64.b64encode(bytes(data)).decode(), "base64"], "lamports": 1, "owner": m.PUMPSWAP_PROGRAM}


def add_pool(chain: Chain, i: int, slot: int, *, offsets=None, n_slices=None, mayhem=False, wsol=True, boost=True, non_keeper=(),
             list_migrate=True, not_keeper_signed=(), leaky=()) -> str:
    """One graduation plus its vault-authority signature list. Slice j sits `offsets[j]` seconds after the migrate."""
    mint, curve, pool_b = k(i, 2), k(i, 3), k(i, 1)
    pool = m.b58encode(pool_b)
    if offsets is None:
        offsets = [10 + 10 * j for j in range(n_slices if n_slices is not None else 29)]
    quote = bytes(32) if wsol else k(77, 7)
    logs = [f"Program {m.PUMP_PROGRAM} invoke [1]", "Program log: Instruction: MigrateV2", data_line(migration_blob(mint, curve, pool_b, quote))]
    if boost:
        logs += [f"Program {m.PUMPSWAP_PROGRAM} invoke [2]", "Program log: Instruction: InitBoost", f"Program {m.PUMPSWAP_PROGRAM} success"]
    logs.append(f"Program {m.PUMP_PROGRAM} success")
    mig_tx = tx_of(slot, BT0, logs, [STRANGER])
    mig_sig = chain.add(m.MIGRATION_FEE_ACCOUNT, slot, BT0, mig_tx)
    chain.accounts[pool] = pool_acct(mayhem)
    auth = m.boost_vault_authority(pool)
    vault = m.boost_vault(pool)
    if list_migrate:
        chain.add(auth, slot, BT0, None, sig=mig_sig)
    for j, off in enumerate(offsets):
        signer = STRANGER if j in not_keeper_signed else KEEPER
        used = 1000
        leak = 1 if j in leaky else 0
        slice_logs = [f"Program {m.PUMPSWAP_PROGRAM} invoke [1]", "Program log: Instruction: BoostBuyAndBurn", data_line(bbb_blob(used)), f"Program {m.PUMPSWAP_PROGRAM} success"]
        keys = [signer, vault, m.b58encode(k(i, 50 + j % 40)), m.b58encode(k(i, 99))]
        pre = [tb(1, 5000), tb(2, 0)] + ([tb(3, 0)] if leak else [])
        post = [tb(1, 5000 - used), tb(2, used - leak)] + ([tb(3, leak)] if leak else [])
        chain.add(auth, slot + off, BT0 + off, tx_of(slot + off, BT0 + off, slice_logs, keys, token_pre=pre, token_post=post))
    for off in non_keeper:
        chain.add(auth, slot + off, BT0 + off, tx_of(slot + off, BT0 + off, [f"Program {m.PUMPSWAP_PROGRAM} invoke [1]", "Program log: Instruction: Sell", f"Program {m.PUMPSWAP_PROGRAM} success"], [STRANGER, vault]))
    return pool


def run(chain, ranges, *, exact=True, **kw):
    return w.run(client_for(chain, **kw), ranges, exact=exact, now=NOW, record_run_utc="2026-10-13T05:00:00Z")


# ---------------------------------------------------------------------------------------------
def test_slot_range_is_inclusive_at_both_ends():
    ch = Chain()
    for i, slot in enumerate([999, 1000, 1500, 2000, 2001]):
        add_pool(ch, i + 1, slot, n_slices=3)
    rec = run(ch, [(1000, 2000)], exact=False)
    assert rec["status"] == "done" and rec["n_migrate_found"] == 3 and rec["n_profiled"] == 3
    assert sorted(p["migrate_slot"] for p in rec["pools"]) == [1000, 1500, 2000]


def test_eligibility_excludes_mayhem_non_wsol_and_no_initboost():
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=3)
    add_pool(ch, 2, 1001, n_slices=3, mayhem=True)
    add_pool(ch, 3, 1002, n_slices=3, wsol=False)
    add_pool(ch, 4, 1003, n_slices=3, boost=False)
    rec = run(ch, [(1000, 1003)], exact=False)
    assert rec["n_migrate_found"] == 4 and rec["n_profiled"] == 1
    assert (rec["n_mayhem"], rec["n_not_wsol"], rec["n_no_init_boost"]) == (1, 1, 1)


def test_rough_tallies_with_a_pool_at_28():
    ch = Chain()
    for i, n in enumerate([28, 29, 30]):
        add_pool(ch, i + 1, 1000 + i, n_slices=n)
    r = run(ch, [(1000, 1002)], exact=False)["rough"]
    assert r == {"n": 3, "n_le_28": 1, "share_le_28": 0.3333, "min": 28, "median": 29, "max": 30}


def test_gap_of_303_s_and_late_last_slice_are_counted():
    ch = Chain()
    add_pool(ch, 1, 1000, offsets=[10, 20, 30, 333, 450])  # largest gap 303 s, last slice 450 s
    add_pool(ch, 2, 1100, offsets=[10, 20, 30, 40, 330])   # gap 290 s too: also >= 60, last 330 s
    add_pool(ch, 3, 1200, offsets=[10, 20, 30, 40, 50])    # no pause, last 50 s
    rec = run(ch, [(1000, 1200)], exact=False)
    g = rec["gaps"]
    assert g["n"] == 3 and g["n_gap_ge_60"] == 2 and g["n_last_gt_400"] == 1 and g["n_last_verified"] == 3
    gaps = {x["pool"]: x["largest_gap_s"] for x in g["pools_gap_ge_60"]}
    assert sorted(gaps.values()) == [290, 303]
    assert rec["pools"][0]["last_slice_s"] in (450, 330, 50)


def test_exact_count_drops_a_non_keeper_transaction_from_29_to_28():
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=28, non_keeper=[500])  # list holds 29, one is not a keeper slice
    rec = run(ch, [(1000, 1000)])
    p = rec["pools"][0]
    assert p["sig_count"] == 29 and p["exact_keeper_slices"] == 28 and p["non_keeper_tx"] == 1 and p["fully_fetched"]
    assert rec["rough"]["n_le_28"] == 0 and rec["exact"]["n_under_29"] == 1 and rec["exact"]["share_under_29"] == 1.0


def test_pool_with_unidentified_migrate_is_not_fully_fetched_and_not_tallied():
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=29)
    add_pool(ch, 2, 1100, n_slices=27, list_migrate=False)
    rec = run(ch, [(1000, 1100)])
    e = rec["exact"]
    bad = next(p for p in rec["pools"] if p["migrate_slot"] == 1100)
    assert not bad["fully_fetched"] and bad["exact_keeper_slices"] == 27 and not bad["migrate_identified"]
    assert (e["n_fully_fetched"], e["n_not_fully_fetched"], e["n_under_29"], e["min"], e["max"], e["n_slices"]) == (1, 1, 0, 29, 29, 29)


def test_bbb_not_keeper_signed_is_counted_and_not_a_slice():
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=29, not_keeper_signed=[3])
    rec = run(ch, [(1000, 1000)])
    p, e = rec["pools"][0], rec["exact"]
    assert p["exact_keeper_slices"] == 28 and p["bbb_not_keeper_signed"] == 1 and p["non_keeper_tx"] == 1
    assert e["n_bbb_not_keeper_signed"] == 1 and e["n_keeper_signed"] == 28 and e["n_zero_fee"] == 28 and e["n_under_29"] == 1


def test_zero_fee_requires_the_whole_amount_to_reach_one_account():
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=30, leaky=[0, 1])
    e = run(ch, [(1000, 1000)])["exact"]
    assert e["n_keeper_signed"] == 30 and e["n_zero_fee"] == 28 and e["n_over_30"] == 0


def test_over_30_and_tx_fetched_once():
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=31)
    e = run(ch, [(1000, 1000)])["exact"]
    assert e["n_over_30"] == 1
    assert max(ch.tx_fetches.values()) == 1  # the last-slice check and the exact pass share one fetch


def test_truncated_list_is_not_fully_fetched():
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=105)
    p = run(ch, [(1000, 1000)])["pools"][0]
    assert p["truncated"] and not p["fully_fetched"] and p["sig_count"] == 100


def test_no_exact_makes_no_exact_fields_and_fewer_calls():
    ch1, ch2 = Chain(), Chain()
    for ch in (ch1, ch2):
        add_pool(ch, 1, 1000, n_slices=29)
    a = run(ch1, [(1000, 1000)], exact=False)
    b = run(ch2, [(1000, 1000)], exact=True)
    assert "exact" not in a and "exact_keeper_slices" not in a["pools"][0] and b["calls"] > a["calls"]


def test_multiple_ranges_each_graduation_once():
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=3)
    add_pool(ch, 2, 3000, n_slices=3)
    rec = run(ch, [(1000, 1000), (1000, 3000)], exact=False)
    assert rec["n_profiled"] == 2 and rec["slot_ranges"] == [[1000, 1000], [1000, 3000]]


def test_record_and_metadata_have_no_price_like_keys():
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=29, non_keeper=[500])
    rec = run(ch, [(1000, 1000)])
    assert rec["schema"] == "boost_slice_watch.v1" and "verdict" not in rec and "flag" not in rec

    def keys(o):
        if isinstance(o, dict):
            for key, v in o.items():
                yield key
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    bad = [x for x in keys(rec) if any(s in x.lower() for s in ("price", "quote", "pnl", "fill"))]
    assert bad == []


# ---------------------------------------------------------------------------------------------
def write_record(path: Path, lines: list[dict]) -> None:
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def test_main_with_record_reads_the_last_line_and_appends(tmp_path, capsys):
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=29)
    add_pool(ch, 2, 5000, n_slices=29)
    rf, out = tmp_path / "daily.jsonl", tmp_path / "out" / "watch.jsonl"
    write_record(rf, [{"run_utc": "2026-10-12T05:00:00Z", "graduations": {"slot_min": 5000, "slot_max": 5000}},
                      {"run_utc": "2026-10-13T05:00:00Z", "graduations": {"slot_min": 1000, "slot_max": 1000}}])
    rc = w.main(["--record", str(rf), "--out", str(out), "--no-exact"], client=client_for(ch), now=NOW)
    assert rc == 0
    lines = out.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["slot_ranges"] == [[1000, 1000]] and rec["run_utc_of_record"] == "2026-10-13T05:00:00Z" and rec["tool_run_utc"].startswith("2026-")
    assert rec["n_profiled"] == 1 and rec["exact_mode"] is False
    assert "boost_slice_watch status=done" in capsys.readouterr().out
    w.main(["--record", str(rf), "--out", str(out), "--no-exact"], client=client_for(ch), now=NOW)
    assert len(out.read_text().splitlines()) == 2


def test_call_cap_reports_stopped_and_exits_3(tmp_path):
    ch = Chain()
    for i in range(3):
        add_pool(ch, i + 1, 1000 + i, n_slices=29)
    out = tmp_path / "w.jsonl"
    rc = w.main(["--slot-range", "1000-1002", "--out", str(out)], client=client_for(ch, max_calls=12), now=NOW)
    assert rc == 3
    rec = json.loads(out.read_text())
    assert rec["status"] == "stopped" and "CallBudgetExceeded" in rec["error"] and rec["calls"] <= 12
    assert rec["n_profiled"] < 3


def test_exit_0_on_done(tmp_path):
    ch = Chain()
    add_pool(ch, 1, 1000, n_slices=29)
    assert w.main(["--slot-range", "1000-1000", "--out", str(tmp_path / "o.jsonl")], client=client_for(ch), now=NOW) == 0


@pytest.mark.parametrize("url", [
    "https://mainnet.helius-rpc.com/?api-key=abc",
    "https://example.com/rpc?api-key=abc",
    "https://user:pw@example.com/rpc",
    "https://example.com/rpc?token=abc",
])
def test_keyed_and_helius_urls_are_refused(url, tmp_path, capsys):
    rc = w.main(["--slot-range", "1-2", "--out", str(tmp_path / "o.jsonl"), "--rpc-url", url])
    assert rc == 2 and not (tmp_path / "o.jsonl").exists()
    assert "abc" not in capsys.readouterr().err and w.refuse_reason(m.DEFAULT_RPC) is None


def test_usage_errors_exit_2(tmp_path):
    out = str(tmp_path / "o.jsonl")
    rf = tmp_path / "r.jsonl"
    write_record(rf, [{"run_utc": "x", "graduations": {"slot_min": None, "slot_max": None}}])
    empty = tmp_path / "e.jsonl"
    empty.write_text("")
    assert w.main(["--out", out]) == 2
    assert w.main(["--out", out, "--slot-range", "1-2", "--record", str(rf)]) == 2
    assert w.main(["--out", out, "--slot-range", "5-2"]) == 2
    assert w.main(["--out", out, "--slot-range", "abc"]) == 2
    assert w.main(["--out", out, "--record", str(rf)]) == 2  # null slots
    assert w.main(["--out", out, "--record", str(empty)]) == 2
    assert w.main(["--out", out, "--record", str(tmp_path / "missing.jsonl")]) == 2
    assert not (tmp_path / "o.jsonl").exists()
