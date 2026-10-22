"""arena_bridge_dry_proof: a self-contained DRY proof of tools/arena_live_bridge.py on a SYNTHETIC decision log in the arena format.

No network, no key, no wallet, no host path, no real book ledger. The log is written with the repo's own `DecisionLog` into a temp dir. The
balance comes from a FAKE source (a DryPort subclass with a scripted number). Every check returns (name, ok, detail); `run_proof()` returns the
list and `main()` prints it as JSON and exits 1 on any failure. It proves bridge logic; it is not gate evidence and not a profit claim.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from tools import arena_live_bridge as alb
from tools.arena_declog import DecisionLog, LogChainError

BOT = "proof-bot"
SHA = "b" * 64
T0 = 1_791_300_000_000
HOLD = 600_000


class FakeBalancePort(alb.DryPort):
    """DryPort whose balance is scripted (the 'fake balance source'). Still no RPC, no key."""

    def set_balance(self, lamports: int) -> None:
        self._balance = lamports


class Rig:
    def __init__(self, tmp: Path, balance: int = 600_000_000, **over: Any) -> None:
        self.tmp = tmp
        self.t = T0 + 1_000
        self.declog = DecisionLog(str(tmp / "arena"))
        self.raw = {"bot_id": BOT, "frozen_sha": SHA, "root": str(tmp / "arena"), "state_dir": str(tmp / "state"), "end_ms": T0 + 86_400_000,
                    "max_hold_ms": HOLD, "stop_file": str(tmp / "STOP"), "halt_file": str(tmp / "HALT"), "mode": "dry", **over}
        self.port = FakeBalancePort(balance)

    def bridge(self, port: alb.DryPort | None = None) -> alb.Bridge:
        return alb.Bridge(alb.Config.from_dict(self.raw), port or self.port, now_ms=lambda: self.t, root_checks=False)

    def row(self, body: dict, ms: int | None = None) -> dict:
        return self.declog.append(BOT, self.t - 100 if ms is None else ms, body)

    def start(self, sha: str = SHA) -> None:
        self.row({"kind": "start", "binding": "proof", "frozen_sha": sha, "rules": [BOT]})

    def entry(self, oid: str, pool: str, ms: int | None = None) -> None:
        self.row({"kind": "entry", "order_id": oid, "pool": pool, "meta": {}, "decision_slot": 1000, "stake_sol": 0.05, "attempt_key": None}, ms)

    def exit(self, oid: str, pool: str) -> None:
        self.row({"kind": "exit", "order_id": oid, "pool": pool, "reason": "tp", "decision_slot": 1010, "attempt": 1})

    def log(self, kind: str) -> list[dict]:
        p = self.tmp / "state" / "bridge-log-dry.jsonl"
        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        return [r for r in rows if r["kind"] == kind]


Check = tuple[str, bool, str]


def check_chain_and_replay() -> list[Check]:
    out: list[Check] = []
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.start()
        r.entry("o1", "P1")
        r.entry("o2", "P2")
        r.exit("o1", "P1")
        head, n = r.declog.verify(BOT)
        b = r.bridge()
        b.tick()
        sent = [(w["side"], w["order_id"]) for w in r.port.would_send]
        out.append(("chain_verifies_clean", bool(head) and n >= 3, f"rows={n}"))
        out.append(("dry_sends_would_send_only", sent == [("buy", "o1"), ("buy", "o2"), ("sell", "o1")], str(sent)))
        # restart: new Bridge + new port over the same state dir; nothing may be replayed
        port2 = FakeBalancePort(600_000_000)
        b2 = r.bridge(port2)
        b2.tick()
        out.append(("restart_replays_nothing", port2.would_send == [], f"second_run_orders={port2.would_send}"))
        r.entry("o3", "P3")
        for _ in range(3):
            b2.tick()
        out.append(("new_row_after_restart_once", [w["order_id"] for w in port2.would_send] == ["o3"], str(port2.would_send)))
        # crash window: the cursor is lost but order ids are recorded -> order_id idempotency, no second buy
        body = json.loads(b2.state_path.read_text())
        body["cursor"], body["prev"], body["seq"] = {}, alb.GENESIS, -1
        b2.state_path.write_text(json.dumps(body, separators=(",", ":"), sort_keys=True))
        port3 = FakeBalancePort(600_000_000)
        r.bridge(port3).tick()
        out.append(("cursor_loss_no_double_buy", not [w for w in port3.would_send if w["side"] == "buy"], str(port3.would_send)))
    return out


def check_chain_break() -> list[Check]:
    out: list[Check] = []
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.start()
        r.entry("o1", "P1")
        f = r.declog.files(BOT)[0]
        lines = f.read_text().splitlines()
        row = json.loads(lines[-1])
        row["pool"] = "TAMPERED"
        lines[-1] = json.dumps(row, separators=(",", ":"), sort_keys=True)
        f.write_text("\n".join(lines) + "\n")
        try:
            r.declog.verify(BOT)
            verify_raises = False
        except LogChainError:
            verify_raises = True
        b = r.bridge()
        b.tick()
        out.append(("tamper_detected_by_declog_verify", verify_raises, ""))
        out.append(("tamper_latches_and_no_buy", "chain_break" in b.state.latches and r.port.would_send == [], str(b.state.latches)))
    return out


def _gate(label: str, setup, expect_buy: bool, out: list[Check], **rig_kw: Any) -> None:
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), **rig_kw)
        r.start()
        setup(r)
        b = r.bridge()
        b.tick()
        bought = any(w["side"] == "buy" for w in r.port.would_send)
        out.append((label, bought == expect_buy, f"buy={bought} counts={b.state.counts}"))


def check_gates() -> list[Check]:
    out: list[Check] = []
    _gate("baseline_buys", lambda r: r.entry("o1", "P1"), True, out)
    _gate("stop_file_blocks_buy", lambda r: (Path(r.raw["stop_file"]).write_text("x"), r.entry("o1", "P1")), False, out)
    _gate("halt_file_blocks_buy", lambda r: (Path(r.raw["halt_file"]).write_text("x"), r.entry("o1", "P1")), False, out)
    _gate("age_4s_ok", lambda r: r.entry("o1", "P1", ms=r.t - 4_000), True, out)
    _gate("age_6s_refused", lambda r: r.entry("o1", "P1", ms=r.t - 6_000), False, out)
    _gate("fake_balance_above_floor_ok", lambda r: r.entry("o1", "P1"), True, out, balance=600_000_000)
    _gate("fake_balance_below_floor_refused", lambda r: r.entry("o1", "P1"), False, out, balance=420_000_000)
    _gate("frozen_sha_mismatch_refused", lambda r: r.entry("o1", "P1"), False, out, frozen_sha="c" * 64)

    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d), max_open=2)
        r.start()
        for i in range(4):
            r.entry(f"o{i}", f"P{i}")
        r.bridge().tick()
        n = sum(1 for w in r.port.would_send if w["side"] == "buy")
        out.append(("max_open_cap", n == 2, f"buys={n}"))
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.start()
        r.entry("o1", "P1")
        r.t = T0 + 86_400_000 + 10
        r.bridge().tick()
        out.append(("past_end_ms_refused", r.port.would_send == [], ""))
    return out


def check_no_network_no_key() -> list[Check]:
    with tempfile.TemporaryDirectory() as d:
        r = Rig(Path(d))
        r.start()
        r.entry("o1", "P1")
        b = r.bridge()
        b.tick()
        sent = r.log("buy_sent")
        return [("port_is_not_live", not b.port.live and b.cfg.mode == alb.DRY, ""),
                ("buy_logged_as_would_send", bool(sent) and all(x.get("would_send") for x in sent), str(sent[:1])),
                ("no_live_state_file", not (Path(d) / "state" / "bridge-state-live.json").exists(), "")]


def run_proof() -> list[Check]:
    return check_chain_and_replay() + check_chain_break() + check_gates() + check_no_network_no_key()


def main() -> int:
    res = run_proof()
    print(json.dumps({"schema": "arena_bridge_dry_proof_v1", "all_ok": all(ok for _, ok, _ in res),
                      "checks": [{"name": n, "ok": ok, "detail": det} for n, ok, det in res]}, indent=1))
    return 0 if all(ok for _, ok, _ in res) else 1


if __name__ == "__main__":
    sys.exit(main())
