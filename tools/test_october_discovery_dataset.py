"""tools/october_discovery_dataset.py on synthetic fixtures only. No /data/mal file, no network, no real tape.

The fixture is a tiny walker-layout root (trades/, creates/, migrations/, events/; one file per hour) for two
exploration-ledger hours, 2026-08-20T12 and 2026-08-20T13 (the repo ledger's explore-0814 row, host research), so the
real ledger guard is exercised. Four graduations:
  A  plain, V-band, migrate row present, 30 BOOST slices by the boost-vault PDA (and 30 boost_buy_and_burn events),
     a rotation tx (sell OTHER, buy A, same trader, one signature), a same-slot buy pair and one base-reserve break;
  B  synthetic (PostCompleteBuy in the completing tx, which also migrates), seed above the 420 SOL tier line;
  C  completes 10 minutes before the read end, so its window is censored;
  D  no migrate row; a V=0 pool prints first and the V-band pool second, so the first V-band pool is canonical; a
     PostCompleteBuy in a later, separate tx (class pcb_other_tx, so no PCB amount may be written).
The TestNoPostS0Price perturbation scrambles every post-s0 side, amount, reserve and V and the BOOST event amounts,
and requires byte-identical graduations and hour stats.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import tools.october_discovery_dataset as od
from observe.trade_decode import b58encode
from tools.pump_structure_monitor import boost_vault_authority

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "docs" / "HOLDOUT_LEDGER.md"
H1, H2, H3 = "2026-08-20T12", "2026-08-20T13", "2026-08-20T14"
T1 = od.hour_start_s(H1)
T2 = od.hour_start_s(H2)
WSOL = od.WSOL_MINT
V = 17_584_505_288
SEED_Q, SEED_B = 67_405_853_863, 206_900_000_000_000
HAVE_ZSTD = shutil.which("zstd") is not None


def key(label: str) -> str:
    return b58encode(hashlib.sha256(label.encode()).digest())


POOL_A, POOL_B, POOL_C, POOL_D1, POOL_D2, POOL_O = (key(f"pool-{x}") for x in ("a", "b", "c", "d1", "d2", "o"))
MINT_A, MINT_B, MINT_C, MINT_D, MINT_O = (key(f"mint-{x}") for x in ("a", "b", "c", "d", "o"))
AUTH_A = boost_vault_authority(POOL_A)


class Tape:
    """Builds trade rows with chained PRE-trade reserves per pool, in file order."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}
        self.state: dict[str, list[int]] = {}
        self.sig_n = 0

    def sig(self) -> str:
        self.sig_n += 1
        return f"SIG{self.sig_n:06d}"

    def trade(self, hour: str, *, pool: str, mint: str, side: str, sol: int, token: int, trader: str, slot: int, bt: int,
              sig: str | None = None, event_index: int = 0, tx_index: int = 0, v: int | None = V, quote_mint: str = WSOL,
              ix_name: str | None = "buy", base_override: int | None = None) -> dict:
        q, b = self.state.setdefault(pool, [SEED_Q, SEED_B])
        row = {
            "v": 2, "venue": "pumpswap", "mint": mint, "trader": trader, "side": side, "sol_lamports": sol, "token_raw": token,
            "quote_reserve": q, "base_reserve": b if base_override is None else base_override, "pool": pool, "slot": slot,
            "signature": sig or self.sig(), "event_index": event_index, "event_ts": bt, "quote_mint": quote_mint,
            "quote_is_wsol": quote_mint == WSOL, "source": "backfill", "block_time": bt, "tx_index": tx_index,
        }
        if v is not None:
            row.update({"virtual_quote_reserves": v, "ix_name": ix_name if side == "buy" else None, "buyback_fee": 0,
                        "creator_fee_unclaimed": 0, "fee_recipient_zero": False})
            if row["ix_name"] is None:
                del row["ix_name"]
        base_now = row["base_reserve"]
        if side == "buy":
            self.state[pool] = [q + sol, base_now - token]
        else:
            self.state[pool] = [max(q - sol, 1), base_now + token]
        self.rows.setdefault(hour, []).append(row)
        return row

    def bonding(self, hour: str, *, mint: str, side: str, sol: int, token: int, trader: str, slot: int, bt: int, sig: str,
                event_index: int, tx_index: int) -> dict:
        row = {"v": 1, "venue": "pump_bonding", "mint": mint, "trader": trader, "side": side, "sol_lamports": sol,
               "token_raw": token, "quote_reserve": 50_000_000_000, "base_reserve": 500_000_000_000_000, "pool": None,
               "slot": slot, "signature": sig, "event_index": event_index, "event_ts": bt, "quote_mint": WSOL,
               "quote_is_wsol": True, "source": "backfill", "block_time": bt, "tx_index": tx_index}
        self.rows.setdefault(hour, []).append(row)
        return row


def lifecycle(kind: str, *, mint: str, slot: int, bt: int, sig: str, tx_index: int = 3, **kw) -> dict:
    row = {"type": kind, "mint": mint, "trader": key("keeper"), "event_ts": bt, "quote_mint": WSOL, "v": 1, "source": "backfill",
           "venue": "pump_bonding", "slot": slot, "signature": sig, "event_index": 0, "block_time": bt, "tx_index": tx_index}
    row.update(kw)
    return row


def event(kind: str, *, slot: int, bt: int, sig: str, event_index: int = 0, tx_index: int = 4, **kw) -> dict:
    row = {"type": kind, "event_ts": bt, "v": 1, "source": "backfill", "slot": slot, "signature": sig, "event_index": event_index,
           "tx_index": tx_index, "block_time": bt, "event_source": "log"}
    row.update(kw)
    return row


def build_fixture(root: Path, *, events: bool = True, zst: bool = False) -> dict:
    """Writes the fixture walk under root; returns the expected values the tests check."""
    tape = Tape()
    creates = {H1: [], H2: []}
    migs = {H1: [], H2: []}
    evs = {H1: [], H2: []}

    # --- A: plain V-band graduation late in H1, so its window (and some BOOST slices) run into H2.
    # Slots advance 5 per second (200 ms) from s0, so slot order and time order agree.
    a_c_bt, a_s0_bt = T1 + 3400, T1 + 3402
    creates[H1].append(lifecycle("create", mint=MINT_A, slot=900, bt=T1 + 100, sig="SIG_CREATE_A", creator=key("creator-a"), is_mayhem_mode=False))
    migs[H1].append(lifecycle("complete", mint=MINT_A, slot=5000, bt=a_c_bt, sig="SIG_COMPLETE_A"))
    migs[H1].append(lifecycle("migration", mint=MINT_A, slot=5001, bt=a_c_bt + 1, sig="SIG_MIG_A", pool=POOL_A, sol_lamports=79_005_359_026,
                              token_raw=206_900_000_000_000, migration_fee=15_000_000, init_boost=True, event_source="log"))
    evs[H1].append(event("init_boost", slot=5001, bt=a_c_bt + 1, sig="SIG_MIG_A", pool=POOL_A, mint=MINT_A, virtual_quote_reserves=V))

    def hr(bt: int) -> str:
        return H1 if bt < T2 else H2

    def sl(dt: int) -> int:
        return 5005 + 5 * dt

    specs = [  # (seconds after s0, kind, kwargs)
        (0, "t", dict(side="buy", sol=1_000_000_000, token=2_000_000_000_000, trader=key("x"))),
        (5, "t", dict(side="buy", sol=500_000_000, token=1_000_000_000_000, trader=key("y"), tx_index=1)),  # same-slot pair
        (5, "t", dict(side="buy", sol=300_000_000, token=600_000_000_000, trader=key("z"), tx_index=2)),
        (20, "t", dict(side="sell", sol=700_000_000, token=1_500_000_000_000, trader=key("x"))),
        (30, "rot", {}),  # trader R sells OTHER on the curve and buys A on PumpSwap, one signature
        (400, "break", dict(side="sell", sol=100_000_000, token=200_000_000_000, trader=key("w"))),  # an LP move the tape lacks
        (405, "t", dict(side="buy", sol=100_000_000, token=150_000_000_000, trader=key("w2"))),
    ]
    specs += [(10 + 11 * i, "boost", {"i": i}) for i in range(30)]  # 30 slices, s0+10 .. s0+329
    specs.sort(key=lambda x: (x[0], x[1] != "t"))
    for dt, kind, kw in specs:
        bt = a_s0_bt + dt
        if kind == "t":
            tape.trade(hr(bt), pool=POOL_A, mint=MINT_A, slot=sl(dt), bt=bt, **kw)
        elif kind == "rot":
            tape.bonding(hr(bt), mint=MINT_O, side="sell", sol=400_000_000, token=9_000_000_000_000, trader=key("r"), slot=sl(dt), bt=bt,
                         sig="SIG_ROT", event_index=0, tx_index=7)
            tape.trade(hr(bt), pool=POOL_A, mint=MINT_A, side="buy", sol=390_000_000, token=700_000_000_000, trader=key("r"), slot=sl(dt),
                       bt=bt, sig="SIG_ROT", event_index=1, tx_index=7, ix_name="multi_hop_swap")
        elif kind == "break":
            tape.trade(hr(bt), pool=POOL_A, mint=MINT_A, slot=sl(dt), bt=bt, base_override=tape.state[POOL_A][1] + 5, **kw)
        else:
            i = kw["i"]
            tape.trade(hr(bt), pool=POOL_A, mint=MINT_A, side="buy", sol=586_166_666, token=900_000_000_000, trader=AUTH_A, slot=sl(dt),
                       bt=bt, tx_index=5)
            evs[hr(bt)].append(event("boost_buy_and_burn", slot=sl(dt), bt=bt, sig=f"SIG_BOOSTEV{i:02d}", pool=POOL_A, mint=MINT_A,
                                     authority=AUTH_A, quote_amount_in_requested=586_166_666, quote_amount_in_used=586_166_666,
                                     base_amount_burned=900_000_000_000, virtual_quote_reserves=V, real_quote_reserves_after=0,
                                     base_reserves_after=0, boost_vault_remaining=17_585_000_000 - 586_166_666 * (i + 1)))

    # --- B: synthetic graduation in H1; the completing tx carries PCB and the migration; seed above 420 SOL
    b_c_bt = T1 + 600
    migs[H1].append(lifecycle("complete", mint=MINT_B, slot=2000, bt=b_c_bt, sig="SIG_COMPLETE_B"))
    migs[H1].append(lifecycle("migration", mint=MINT_B, slot=2000, bt=b_c_bt, sig="SIG_COMPLETE_B", pool=POOL_B, sol_lamports=79_005_359_026,
                              token_raw=206_900_000_000_000, migration_fee=15_000_000, init_boost=False, event_source="inner_event"))
    evs[H1].append(event("post_complete_buy", slot=2000, bt=b_c_bt, sig="SIG_COMPLETE_B", mint=MINT_B, trader=key("pcb"), quote_mint=WSOL,
                         base_out=3_000_000_000_000, quote_in=500_000_000, fee=5_000_000, creator_fee=1_500_000, buyback_fee=0,
                         pool_base_reserves_before=SEED_B, pool_quote_reserves_before=SEED_Q, pool_base_reserves_after=SEED_B - 3_000_000_000_000,
                         pool_quote_reserves_after=SEED_Q + 495_000_000, bonding_curve=key("curve-b"), event_source="inner_event"))
    tape.state[POOL_B] = [SEED_Q + 2_000_000_000, SEED_B - 6_000_000_000_000]
    tape.trade(H1, pool=POOL_B, mint=MINT_B, side="sell", sol=200_000_000, token=500_000_000_000, trader=key("b1"), slot=2003, bt=b_c_bt + 1)
    tape.trade(H1, pool=POOL_B, mint=MINT_B, side="buy", sol=250_000_000, token=500_000_000_000, trader=key("b2"), slot=2010, bt=b_c_bt + 3)

    # --- C: completes 10 minutes before the read end (H3), so complete + 900 s passes it
    c_c_bt = od.hour_start_s(H3) - 600
    migs[H2].append(lifecycle("complete", mint=MINT_C, slot=9000, bt=c_c_bt, sig="SIG_COMPLETE_C"))
    migs[H2].append(lifecycle("migration", mint=MINT_C, slot=9001, bt=c_c_bt + 1, sig="SIG_MIG_C", pool=POOL_C, sol_lamports=79_005_359_026,
                              token_raw=206_900_000_000_000, migration_fee=15_000_000, init_boost=True, event_source="log"))
    tape.trade(H2, pool=POOL_C, mint=MINT_C, side="buy", sol=100_000_000, token=200_000_000_000, trader=key("c1"), slot=9003, bt=c_c_bt + 2)

    # --- D: no migrate row; a V=0 pool prints first, the V-band pool second
    d_c_bt = T1 + 1200
    migs[H1].append(lifecycle("complete", mint=MINT_D, slot=3000, bt=d_c_bt, sig="SIG_COMPLETE_D"))
    tape.state[POOL_D1] = [1_000_000_000, 10_000_000_000_000]
    tape.trade(H1, pool=POOL_D1, mint=MINT_D, side="buy", sol=10_000_000, token=1_000_000_000, trader=key("d0"), slot=3001, bt=d_c_bt + 1, v=0)
    tape.trade(H1, pool=POOL_D2, mint=MINT_D, side="buy", sol=20_000_000, token=1_000_000_000, trader=key("d1"), slot=3002, bt=d_c_bt + 2)
    evs[H1].append(event("post_complete_buy", slot=3020, bt=d_c_bt + 4, sig="SIG_PCB_D_LATER", mint=MINT_D, trader=key("pcb-d"), quote_mint=WSOL,
                         base_out=1_000_000_000, quote_in=30_000_000, fee=300_000, creator_fee=90_000, buyback_fee=0,
                         pool_base_reserves_before=10**15, pool_quote_reserves_before=10**11, pool_base_reserves_after=10**15 - 10**9,
                         pool_quote_reserves_after=10**11 + 3 * 10**7, bonding_curve=key("curve-d"), event_source="inner_event"))

    # an unrelated tx with an event_index gap (0, 2): one order violation
    tape.trade(H1, pool=POOL_O, mint=MINT_O, side="buy", sol=1_000_000, token=1_000_000, trader=key("o1"), slot=100, bt=T1 + 10, sig="SIG_GAP", event_index=0, v=None)
    tape.trade(H1, pool=POOL_O, mint=MINT_O, side="buy", sol=1_000_000, token=1_000_000, trader=key("o1"), slot=100, bt=T1 + 10, sig="SIG_GAP", event_index=2, v=None)
    # a WSOL-base leg plus a token sell by one trader: a SOL leg, not a rotation and not multi-mint
    tape.trade(H1, pool=key("pool-w"), mint=WSOL, side="buy", sol=2_000_000, token=2_000_000, trader=key("q"), slot=101, bt=T1 + 11, sig="SIG_WSOL",
               event_index=0, v=None)
    tape.trade(H1, pool=POOL_O, mint=MINT_O, side="sell", sol=1_000_000, token=1_000_000, trader=key("q"), slot=101, bt=T1 + 11, sig="SIG_WSOL",
               event_index=1, v=None)
    # event_index out of order inside one tx (1 then 0): a disorder
    tape.trade(H1, pool=POOL_O, mint=MINT_O, side="buy", sol=1_000_000, token=1_000_000, trader=key("o2"), slot=102, bt=T1 + 12, sig="SIG_DIS", event_index=1, v=None)
    tape.trade(H1, pool=POOL_O, mint=MINT_O, side="buy", sol=1_000_000, token=1_000_000, trader=key("o2"), slot=102, bt=T1 + 12, sig="SIG_DIS", event_index=0, v=None)

    streams = {"trades": tape.rows, "creates": creates, "migrations": migs}
    if events:
        streams["events"] = evs
    for stream, by_hour in streams.items():
        (root / stream).mkdir(parents=True, exist_ok=True)
        for hour in (H1, H2):
            data = b"".join(json.dumps(r, separators=(",", ":")).encode() + b"\n" for r in by_hour.get(hour, []))
            path = root / stream / f"{stream}-{hour}.jsonl"
            path.write_bytes(data)
            if zst:
                subprocess.run(["zstd", "-q", "-f", "--rm", str(path)], check=True)
    return {"a_s0_bt": a_s0_bt, "a_c_bt": a_c_bt}


def write_view(root: Path, *, corrupt: str | None = None) -> None:
    lines = []
    for p in sorted(root.glob("*/*.jsonl*")):
        rel = p.relative_to(root).as_posix()
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        if rel == corrupt:
            sha = "0" * 64
        lines.append(f"{sha}  ./{rel}")
    (root / "VIEW.sha256").write_text("\n".join(lines) + "\n")


def write_walk_meta(root: Path, *, unsealed: tuple[str, ...] = (), bad_trades_sha: tuple[str, ...] = (), bad_events_sha: tuple[str, ...] = ()) -> None:
    """checkpoint.json + verify.jsonl the way the forward walks write them (see tools/test_forward_v_join.py)."""
    cp = {"hours": {}}
    lines = []
    for h in (H1, H2):
        cp["hours"][h] = {"status": "partial" if h in unsealed else "sealed"}
        sha = {}
        for stream in od.STREAMS:
            p = od.stream_file(root, stream, h)
            if p is not None:
                sha[stream] = hashlib.sha256(p.read_bytes()).hexdigest()
        if h in bad_trades_sha:
            sha["trades"] = "0" * 64
        if h in bad_events_sha:
            sha["events"] = "0" * 64
        lines.append({"hour": h, "issues": [], "content": {"trades": {"duplicates": 0, "bad_lines": 0}}, "sha256": sha})
    (root / "checkpoint.json").write_text(json.dumps(cp))
    (root / "verify.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))


def read_out(out: Path) -> tuple[dict[str, dict], list[dict], dict]:
    grads = [json.loads(x) for x in (out / "graduations.jsonl").read_text().splitlines() if x.strip()]
    hours = [json.loads(x) for x in (out / "hour_stats.jsonl").read_text().splitlines() if x.strip()]
    man = json.loads((out / "manifest.json").read_text())
    return {g["mint"]: g for g in grads}, hours, man


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="oct-disc-"))
        self.root = self.tmp / "walk"
        self.out = self.tmp / "out"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_build(self, **kw):
        args = dict(ledger=LEDGER, ledger_host="research", out_dir=self.out, allow_unverified=True)
        args.update(kw)
        return od.build([self.root], H1, H3, **args)


class TestGuards(Base):
    def test_ledger_denies_every_october_hour_today(self):
        # No root exists: the ledger check must refuse before any file is looked at.
        for host in ("research", "fast", "oracle"):
            for start, end in (("2026-10-01T00", "2026-10-02T10"), ("2026-10-09T14", "2026-10-16T01"), ("2026-10-16T01", "2026-10-17T00"),
                               ("2026-11-06T01", "2026-11-07T00")):
                with self.assertRaises(od.Refused) as cm:
                    od.build([self.tmp / "nope"], start, end, ledger=LEDGER, ledger_host=host, out_dir=self.out)
                self.assertIn("ledger denies", str(cm.exception))
        self.assertFalse(self.out.exists())

    def test_ledger_denies_sealed_august_blocks(self):
        for start, end in (("2026-08-02T12", "2026-08-02T13"), ("2026-08-08T12", "2026-08-08T13"), ("2026-08-28T12", "2026-08-28T13")):
            with self.assertRaises(od.Refused):
                od.build([self.tmp / "nope"], start, end, ledger=LEDGER, ledger_host="research", out_dir=self.out)

    def test_never_read_paths(self):
        for bad in ("fresh-0802", "fresh-0808", "fresh-0828", "tip-tape-archive"):
            d = self.tmp / bad / "w1"
            d.mkdir(parents=True)
            with self.assertRaises(od.Refused) as cm:
                od.build([d], H1, H3, ledger=LEDGER, ledger_host="research", out_dir=self.out, allow_unverified=True)
            self.assertIn("never read", str(cm.exception))

    def test_forward_root_needs_final_marker(self):
        root = self.tmp / "forward-1002ev"
        build_fixture(root)
        with self.assertRaises(od.Refused) as cm:
            od.build([root], H1, H3, ledger=LEDGER, ledger_host="research", out_dir=self.out, allow_unverified=True,
                     final_ledger=self.tmp / "FINAL_READS.jsonl")
        self.assertIn("FINAL", str(cm.exception))
        self.assertFalse(self.out.exists())

    def test_unverified_root_refused_without_flag(self):
        build_fixture(self.root)
        with self.assertRaises(od.Refused) as cm:
            self.run_build(allow_unverified=False)
        self.assertIn("VIEW.sha256", str(cm.exception))

    def test_view_sha_checked(self):
        build_fixture(self.root)
        write_view(self.root, corrupt=f"trades/trades-{H2}.jsonl")
        with self.assertRaises(od.Refused) as cm:
            self.run_build(allow_unverified=False)
        self.assertIn("does not match", str(cm.exception))
        write_view(self.root)
        man = self.run_build(allow_unverified=False)
        self.assertEqual({h["integrity"] for h in man["hours"]}, {"view_ok"})

    def test_walk_layout_verified(self):
        build_fixture(self.root)
        for kw, msg in ((dict(unsealed=(H2,)), "not_sealed"), (dict(bad_trades_sha=(H1,)), "sha_mismatch"),
                        (dict(bad_events_sha=(H2,)), "verify.jsonl sha256")):
            write_walk_meta(self.root, **kw)
            with self.assertRaises(od.Refused) as cm:
                self.run_build(allow_unverified=False)
            self.assertIn(msg, str(cm.exception))
            self.assertFalse((self.out / "graduations.jsonl").exists())
        write_walk_meta(self.root)
        man = self.run_build(allow_unverified=False)
        self.assertEqual({h["integrity"] for h in man["hours"]}, {"walk_verified"})

    def test_missing_hour_refused_or_flagged(self):
        build_fixture(self.root)
        with self.assertRaises(od.Refused):
            od.build([self.root], H1, "2026-08-20T15", ledger=LEDGER, ledger_host="research", out_dir=self.out, allow_unverified=True)
        man = od.build([self.root], H1, "2026-08-20T15", ledger=LEDGER, ledger_host="research", out_dir=self.out, allow_unverified=True,
                       allow_missing=True)
        grads, _h, _m = read_out(self.out)
        self.assertTrue(grads[MINT_C]["censored_missing_hour"])
        self.assertFalse(grads[MINT_B]["censored_missing_hour"])
        self.assertEqual([h["integrity"] for h in man["hours"]], ["unverified", "unverified", "missing"])

    def test_never_overwrites(self):
        build_fixture(self.root)
        self.out.mkdir()
        (self.out / "x").write_text("keep")
        with self.assertRaises(od.Refused):
            self.run_build()
        self.assertEqual((self.out / "x").read_text(), "keep")

    def test_workers_capped(self):
        build_fixture(self.root)
        with self.assertRaises(od.Refused):
            self.run_build(workers=3)

    def test_cli_exit_3_on_refusal(self):
        rc = od.main(["--root", str(self.tmp / "nope"), "--start", "2026-10-10T00", "--end", "2026-10-10T01", "--ledger-host", "research",
                      "--out", str(self.out)])
        self.assertEqual(rc, 3)


class TestFixtureBuild(Base):
    def test_rows(self):
        exp = build_fixture(self.root)
        man = self.run_build()
        grads, hours, man2 = read_out(self.out)
        self.assertEqual(man["summary"], man2["summary"])
        self.assertEqual(set(grads), {MINT_A, MINT_B, MINT_C, MINT_D})
        for g in grads.values():
            od.assert_d0_graduation(g)
            self.assertLessEqual(set(g), set(od.D0_GRADUATION_COLUMNS))
            self.assertFalse(set(g) & set(TestD0Columns.REMOVED))
        for h in hours:
            od.assert_d0_hour(h)

        a = grads[MINT_A]
        self.assertEqual(a["synth_class"], "not_seen")
        self.assertEqual(a["pool"], POOL_A)
        self.assertEqual(a["pool_src"], "migration")
        self.assertEqual(a["s0_bt"], exp["a_s0_bt"])
        self.assertEqual(a["s0_minus_complete_s"], 2)
        self.assertEqual(a["s0_minus_migrate_s"], 1)
        self.assertEqual(a["s0_minus_migrate_slots"], 4)
        self.assertEqual(a["complete_to_migrate_slots"], 1)
        self.assertEqual((a["v0_lamports"], a["v0_src"], a["v_band"]), (V, "event", True))
        self.assertAlmostEqual(a["seed_mcap_sol"], (SEED_Q + V) * od.PUMP_SUPPLY_RAW / SEED_B / 1e9, places=5)
        self.assertEqual((a["seed_fee_ppm"], a["seed_creator_fee_ppm"], a["seed_tier_floor_sol"], a["seed_above_420"]), (12_500, 3_000, 0.0, False))
        self.assertTrue(a["migrate_init_boost"])
        self.assertEqual(a["init_boost_event_n"], 1)
        # BOOST: 30 slices by the PDA and the same 30 as events; counts and times only
        self.assertEqual(a["boost_src"], "pda")
        self.assertEqual(a["boost_pda_n"], 30)
        self.assertEqual(a["boost_ev_n"], 30)
        self.assertEqual(a["boost_pda_first_s_from_s0"], 10)
        self.assertEqual(a["boost_pda_last_s_from_s0"], 10 + 11 * 29)
        self.assertEqual(a["boost_pda_last_s_from_migrate"], 10 + 11 * 29 + 1)
        self.assertEqual(a["boost_pda_last_s_from_complete"], 10 + 11 * 29 + 2)
        self.assertEqual(a["boost_pda_median_gap_s"], 11)
        self.assertEqual(a["boost_ev_last_s_from_s0"], 10 + 11 * 29)
        # first minute: 5 organic prints by 4 traders (x twice) and 5 BOOST slices (s0+10, 21, 32, 43, 54); no side split
        self.assertEqual((a["fm_n_prints"], a["fm_n_traders"]), (10, 5))
        self.assertEqual((a["fm_n_prints_ex_boost"], a["fm_n_traders_ex_boost"], a["fm_boost_n"]), (5, 4, 5))
        # first 360 s: 5 organic prints and 30 slices; the s0+5 slot holds two prints
        self.assertEqual((a["ms_n_prints"], a["ms_n_traders"]), (35, 5))
        self.assertEqual(a["ms_same_slot_prints_max"], 2)
        self.assertEqual(a["ms_prints_per_active_slot_max"], 2)
        # multi-hop: one print in a multi-mint tx, flagged by structure and by ix_name
        self.assertEqual(a["mh_n_prints_multi_tx"], 1)
        self.assertEqual(a["mh_n_ix_multihop"], 1)
        # reserve chain: integrity counts in the manifest only. A holds the one break; A has 36 links and B one.
        chain = man["integrity"]["reserve_chain"]
        self.assertEqual(chain["chain_breaks"], 1)
        self.assertEqual(chain["chain_breaks_multi"], 0)
        self.assertEqual(chain["chain_breaks_cross_slot"], 1)
        self.assertGreaterEqual(chain["chain_links_multi"], 1)
        self.assertEqual(chain["chain_links"], 36 + 1)
        self.assertFalse([k for k in a if k.startswith("chain_")])
        self.assertFalse(a["censored_read_end"])
        self.assertEqual(a["creator"], key("creator-a"))
        self.assertIs(a["is_mayhem"], False)

        b = grads[MINT_B]
        self.assertEqual(b["synth_class"], "synthetic")
        self.assertTrue(b["pcb_same_tx_complete"])
        self.assertTrue(b["pcb_same_tx_migrate"])
        self.assertTrue(b["complete_migrate_same_tx"])
        self.assertEqual(b["pcb_delay_slots_from_complete"], 0)
        self.assertEqual(b["pcb_quote_in_lamports"], 500_000_000)
        self.assertEqual(b["pcb_pool_quote_after"], SEED_Q + 495_000_000)  # in the curve-completing tx: allowed in D0
        self.assertEqual(b["pcb_event_source"], "inner_event")
        self.assertTrue(b["seed_above_420"])
        self.assertEqual((b["seed_fee_ppm"], b["seed_creator_fee_ppm"], b["seed_tier_floor_sol"]), (12_000, 9_500, 420.0))
        self.assertIsNone(b["boost_src"])
        self.assertEqual(b["boost_pda_n"], 0)
        self.assertFalse(b["create_seen"])

        c = grads[MINT_C]
        self.assertTrue(c["censored_read_end"])
        self.assertEqual(c["synth_class"], "not_seen")

        d = grads[MINT_D]
        self.assertFalse(d["migrate_seen"])
        self.assertEqual(d["pool"], POOL_D2)
        self.assertEqual(d["pool_src"], "first_vband_pool")
        self.assertEqual(d["n_pumpswap_pools"], 2)
        # a PCB in a later tx: existence and time only, no amount or pool reserve
        self.assertEqual((d["synth_class"], d["pcb_n"], d["pcb_delay_s_from_complete"], d["pcb_same_tx_complete"]), ("pcb_other_tx", 1, 4, False))
        self.assertEqual([d[c] for c in od.PCB_QTY_SOURCE], [None] * len(od.PCB_QTY_SOURCE))

        hs = {h["hour"]: h for h in hours}
        self.assertEqual(hs[H1]["tx_multi_mint"], 1)
        self.assertNotIn("tx_rotation", hs[H1])
        self.assertEqual(hs[H1]["rows_by_venue"]["pump_bonding"], 1)
        self.assertEqual(hs[H1]["tx_event_index_gap"], 1)
        self.assertEqual(hs[H1]["tx_event_index_disorder"], 1)
        self.assertEqual(hs[H1]["tx_with_wsol_base_leg"], 1)
        self.assertEqual(hs[H1]["rows_ix_multihop"], 1)
        self.assertTrue(hs[H1]["has_event_stream"])
        self.assertEqual(hs[H1]["events_types"]["post_complete_buy"], 2)
        s = man["summary"]
        self.assertEqual(s["graduations"], 4)
        self.assertEqual(s["by_synth_class"], {"not_seen": 2, "pcb_other_tx": 1, "synthetic": 1})
        self.assertNotIn("chain_breaks", s)

    def test_no_event_stream(self):
        build_fixture(self.root, events=False)
        self.run_build()
        grads, hours, _m = read_out(self.out)
        self.assertEqual({g["synth_class"] for g in grads.values()}, {"no_event_stream"})
        self.assertEqual(grads[MINT_A]["boost_ev_n"], 0)
        self.assertEqual(grads[MINT_A]["boost_src"], "pda")
        self.assertFalse(any(h["has_event_stream"] for h in hours))

    def test_vmap_fallback_when_rows_carry_no_v(self):
        build_fixture(self.root)
        # strip event V from every trade row, as on a pre-October or non-ev walk
        for p in (self.root / "trades").glob("*.jsonl"):
            rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
            for r in rows:
                r.pop("virtual_quote_reserves", None)
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        vm = self.tmp / "vmap.json"
        vm.write_text(json.dumps({"v": {POOL_A: V, POOL_D1: 0, POOL_D2: V, POOL_B: 5 * 10**12}}))
        self.run_build(vmap_paths=[vm])
        grads, _h, _m = read_out(self.out)
        self.assertEqual((grads[MINT_A]["v0_src"], grads[MINT_A]["v0_lamports"]), ("map", V))
        self.assertEqual(grads[MINT_D]["pool"], POOL_D2)
        self.assertIsNone(grads[MINT_B]["v0_src"])  # map garbage (5e12) is dropped

    @unittest.skipUnless(HAVE_ZSTD, "zstd binary not installed")
    def test_zst_and_two_workers_match_one_worker(self):
        build_fixture(self.root, zst=True)
        self.run_build(workers=1)
        one = (self.out / "graduations.jsonl").read_text()
        out2 = self.tmp / "out2"
        od.build([self.root], H1, H3, ledger=LEDGER, ledger_host="research", out_dir=out2, allow_unverified=True, workers=2)
        self.assertEqual(one, (out2 / "graduations.jsonl").read_text())
        self.assertEqual((self.out / "hour_stats.jsonl").read_text(), (out2 / "hour_stats.jsonl").read_text())


class TestHelpers(unittest.TestCase):
    def test_chain_check_counts(self):
        rows = [{"side": "buy", "token_raw": 10, "base_reserve": 100, "slot": 1, "signature": "s1"},
                {"side": "sell", "token_raw": 5, "base_reserve": 90, "slot": 1, "signature": "s2"},
                {"side": "buy", "token_raw": 1, "base_reserve": 96, "_multi": True, "slot": 2, "signature": "s3"}]
        self.assertEqual(od.chain_check(rows), {"chain_links": 2, "chain_breaks": 1, "chain_links_multi": 1, "chain_breaks_multi": 1,
                                                 "chain_unknown": 0, "chain_breaks_same_tx": 0, "chain_breaks_same_slot": 0,
                                                 "chain_breaks_cross_slot": 1})

    def test_fee_tier_line(self):
        self.assertEqual(od.fee_tier(410.88), (12_500, 3_000, 0.0))
        self.assertEqual(od.fee_tier(420.0), (12_000, 9_500, 420.0))
        self.assertEqual(od.fee_tier(None), (None, None, None))

    def test_seed_mcap_matches_pinned_seed(self):
        # hunt-shared README: the pinned seed spot (67,405,853,863 + 17,584,505,288) / (206.9e12 * 1000) SOL per token
        self.assertAlmostEqual(od.seed_mcap_sol(SEED_Q, V, SEED_B), (SEED_Q + V) / (SEED_B * 1000) * 1e9, places=6)
        self.assertAlmostEqual(od.seed_mcap_sol(SEED_Q, V, SEED_B), 410.78, places=2)
        self.assertIsNone(od.seed_mcap_sol(SEED_Q, None, SEED_B))


def perturb_post_s0(root: Path, *, seed: int = 7) -> int:
    """Rewrites the fixture walk in place. On every PumpSwap row of a graduated mint: flips `side` and scrambles
    `sol_lamports` and `token_raw` (the s0 row included), and scrambles `quote_reserve`, `base_reserve` and
    `virtual_quote_reserves` on every row after its pool's first print (s0). On every boost_buy_and_burn event:
    scrambles the amounts, reserves and vault balance. Returns the number of rows changed."""
    rng = random.Random(seed)
    grad = {MINT_A, MINT_B, MINT_C, MINT_D}
    seen: set[str] = set()
    n = 0
    for hour in (H1, H2):
        p = root / "trades" / f"trades-{hour}.jsonl"
        rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
        for r in rows:
            if r.get("venue") != "pumpswap" or r.get("mint") not in grad:
                continue
            r["side"] = "sell" if r["side"] == "buy" else "buy"
            r["sol_lamports"] = rng.randrange(1, 10**12)
            r["token_raw"] = rng.randrange(1, 10**15)
            if r["pool"] in seen:
                r["quote_reserve"] = rng.randrange(1, 10**12)
                r["base_reserve"] = rng.randrange(1, 10**15)
                if "virtual_quote_reserves" in r:
                    r["virtual_quote_reserves"] = rng.randrange(1, 10**11)
            seen.add(r["pool"])
            n += 1
        p.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
        e = root / "events" / f"events-{hour}.jsonl"
        evs = [json.loads(x) for x in e.read_text().splitlines() if x.strip()]
        for r in evs:
            if r.get("type") == "boost_buy_and_burn":
                for k in ("quote_amount_in_requested", "quote_amount_in_used", "base_amount_burned", "virtual_quote_reserves",
                          "real_quote_reserves_after", "base_reserves_after", "boost_vault_remaining"):
                    r[k] = rng.randrange(1, 10**12)
                n += 1
        e.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in evs))
    return n


class TestNoPostS0Price(Base):
    """No post-s0 price can be reconstructed from the output: no post-s0 side, amount, reserve or V reaches it."""

    def test_post_s0_trade_columns_reach_no_output(self):
        build_fixture(self.root)
        m1 = self.run_build()
        g1, h1 = (self.out / "graduations.jsonl").read_bytes(), (self.out / "hour_stats.jsonl").read_bytes()
        self.assertGreater(perturb_post_s0(self.root), 40)
        out2 = self.tmp / "out2"
        m2 = od.build([self.root], H1, H3, ledger=LEDGER, ledger_host="research", out_dir=out2, allow_unverified=True)
        self.assertEqual(g1, (out2 / "graduations.jsonl").read_bytes())
        self.assertEqual(h1, (out2 / "hour_stats.jsonl").read_bytes())
        self.assertEqual(m1["summary"], m2["summary"])
        # the scrambled columns were present and were read, by the integrity counter only (manifest, counts)
        self.assertGreater(m2["integrity"]["reserve_chain"]["chain_breaks"], m1["integrity"]["reserve_chain"]["chain_breaks"])

    def test_seed_is_the_s0_row_only(self):
        build_fixture(self.root)
        p = self.root / "trades" / f"trades-{H1}.jsonl"
        rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
        first = next(r for r in rows if r.get("pool") == POOL_A)
        first["quote_reserve"] += 1_000_000_000  # the s0 row's PRE-trade quote reserve: the seed moves with it
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.run_build()
        grads, _h, _m = read_out(self.out)
        self.assertEqual(grads[MINT_A]["seed_quote_lamports"], SEED_Q + 1_000_000_000)

    def test_no_signed_flow_or_reserve_column_after_s0(self):
        build_fixture(self.root)
        self.run_build()
        grads, hours, man = read_out(self.out)
        for g in grads.values():
            for col in g:
                cat = od.D0_GRADUATION_COLUMNS[col][0]
                if od.QUANTITY_TOKENS & set(col.split("_")):
                    self.assertEqual(cat, od.SEED, col)  # a quantity column is a seed column, fixed at or before s0
        for h in hours:
            self.assertFalse(od.QUANTITY_TOKENS & {t for col in h for t in col.split("_")}, h)
        self.assertEqual(man["post_s0_masked_columns"], list(od.POST_S0_MASKED))


class TestD0Columns(unittest.TestCase):
    REMOVED = (
        "fm_buy_sol", "fm_sell_sol", "fm_max_buy_sol", "fm_max_sell_sol", "fm_flow_sol", "fm_n_buys", "fm_n_sells",
        "fm_n_buyers", "fm_n_sellers", "fm_first_sell_s_from_s0", "fm_boost_sol", "boost_pda_sol", "boost_ev_sol",
        "boost_heur_n", "boost_heur_sol", "boost_heur_is_pda", "boost_budget_spent_share", "boost_budget_complete",
        "boost_ev_vault_remaining_last", "mh_n_rot_in", "mh_n_rot_out", "mh_rot_in_sol", "mh_rot_out_sol", "mh_ix_names",
        "ms_same_slot_buys_mean", "ms_same_slot_buys_max", "s0_side", "s0_ix_name", "chain_links", "chain_breaks",
    )

    def test_removed_and_unknown_columns_refused(self):
        for col in self.REMOVED + ("pnl_sol", "seed_price", "ret_5m", "anything_new"):
            self.assertNotIn(col, od.D0_GRADUATION_COLUMNS)
            with self.assertRaises(od.D0ColumnError):
                od.assert_d0_graduation({"mint": "m", col: 1})
        for col in ("ix_name_top", "tx_rotation", "rows_pumpswap_buy", "sol_total"):
            with self.assertRaises(od.D0ColumnError):
                od.assert_d0_hour({"hour": H1, col: 1})

    def test_kinds_fail_closed(self):
        for bad in ({"fm_n_prints": 1.5}, {"fm_n_prints": -1}, {"fm_n_prints": True}, {"fm_n_prints": [1]}, {"mint": 5},
                    {"seed_mcap_sol": {"x": 1}}, {"v_band": 1}):
            with self.assertRaises(od.D0ColumnError):
                od.assert_d0_graduation(bad)
        for bad in ({"rows_by_venue": {"pumpswap": 1.5}}, {"events_types": {"x": -1}}, {"rows": "3"}):
            with self.assertRaises(od.D0ColumnError):
                od.assert_d0_hour(bad)
        od.assert_d0_graduation({"mint": "m", "fm_n_prints": 3, "seed_mcap_sol": 410.78, "v_band": None})
        od.assert_d0_hour({"hour": H1, "rows_by_venue": {"pumpswap": 3}, "tx_multi_mint": 0})

    def test_pcb_amounts_only_from_the_completing_tx(self):
        od.assert_d0_graduation({"pcb_same_tx_complete": True, "pcb_quote_in_lamports": 5, "pcb_pool_quote_after": 7})
        for flag in (False, None):
            with self.assertRaises(od.D0ColumnError):
                od.assert_d0_graduation({"pcb_same_tx_complete": flag, "pcb_pool_quote_after": 7})

    def test_allowlist_names_no_post_s0_quantity(self):
        for allow in (od.D0_GRADUATION_COLUMNS, od.D0_HOUR_COLUMNS):
            for col, (cat, kind) in allow.items():
                self.assertIn(cat, od.D0_CATEGORIES)
                self.assertIn(kind, od.KINDS)
                if od.QUANTITY_TOKENS & set(col.lower().split("_")):
                    self.assertEqual(cat, od.SEED, col)
        self.assertEqual(set(od.POST_S0_COLUMNS) & set(od.POST_S0_MASKED), set())
        self.assertLessEqual({"side", "sol_lamports", "token_raw", "quote_reserve", "base_reserve", "virtual_quote_reserves"},
                             set(od.POST_S0_MASKED))
        r = {"slot": 1, "trader": "t", "side": "buy", "sol_lamports": 5, "token_raw": 6, "quote_reserve": 7, "base_reserve": 8,
             "virtual_quote_reserves": 9}
        self.assertEqual(od.mask_post_s0(r), {"slot": 1, "trader": "t"})


if __name__ == "__main__":
    unittest.main()
