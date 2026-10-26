"""PR 4 of the HV4 live adapter: read-record gate, start-row pins, S on the live port, owner line, F-arm model id, per-trade loss latch, 14-day window, main()
path checks. Synthetic fixtures only: no HV4 ledger, decision row, bots.json, read record or job log is read; no network, no key (throwaway Keypair)."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_bridge as alb
from tools import hv4_live_c0 as c0
from tools.test_arena_live_bridge import TmpCase
from tools.test_hv4_live_c0 import ARM, FUNDED, Hv4Env, live_main_env, owner_fixture, read_record, write_read_record
from tools.test_hv4_live_c0_pr2 import LivePortScheduleTests, decode_tx

def rrp(*a, **k):
    """read_record_problem with the synthetic exception on (dirs other than the canonical one are allowed): the sealed-prefix, alias and content checks still run.
    Provenance tests call c0.read_record_problem directly."""
    k.setdefault("synthetic_ok", True)
    return c0.read_record_problem(*a, **k)


DAY = 86_400_000
FARM = "hv4-CON-F"


class ScratchArena:
    """A scratch stand-in for the sealed arena (SEALED_ROOT_PREFIX and READS_DIR_CANON patched). No real arena path is touched."""

    def __init__(self, tmp):
        self.root = os.path.realpath(os.path.join(tmp, "arena"))
        os.makedirs(os.path.join(self.root, "reads"), exist_ok=True)
        self.patches = [mock.patch.object(c0, "SEALED_ROOT_PREFIX", self.root), mock.patch.object(c0, "READS_DIR_CANON", os.path.join(self.root, "reads"))]

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()


class ReadRecordPureTests(TmpCase):
    def _dir(self, name="reads"):
        d = os.path.realpath(os.path.join(self.tmp, name))
        os.makedirs(d, exist_ok=True)
        return d

    def test_good_record_passes_and_returns_identity(self):
        d = self._dir()
        write_read_record(d, ARM)
        why, ident = rrp(d, ARM)
        self.assertIsNone(why)
        self.assertEqual(set(ident), {"path", "dev", "ino", "sha256"})
        self.assertEqual(ident["path"], os.path.join(d, ARM + ".json"))

    def test_applies_to_any_hv4_bot_id_and_refuses_the_rest(self):
        d = self._dir()
        for bot in ("hv4-CON-F", "hv4-BST-X", "hv4-BIG-L"):
            self.assertEqual(rrp(d, bot)[0], "read_record_missing", bot)
        for bad in ("rule1", "", None, "hv4-../x", ".hv4-x", 5):
            self.assertEqual(rrp(d, bad)[0], "read_record_bot_id", bad)
        for bad in (None, "", "relative/reads", 3):
            self.assertEqual(rrp(bad, ARM)[0], "read_record_dir_missing", bad)

    def test_content_must_be_a_recorded_pass_for_this_bot(self):
        d = self._dir()
        cases = {
            "read_record_not_pass": [dict(status="FAIL"), dict(status="REPORT_ONLY"), dict(status="PENDING"), dict(status="PASS", fail_label="FAIL (n < 100)"),
                                     dict(boundary_source="forced"), dict(pending=2), dict(am15=None), dict(read_boundary_ms=True), dict(read_boundary_ms="1")],
            "read_record_other_bot": [dict(bot_id="hv4-BIG-X")],
        }
        for label, overs in cases.items():
            for over in overs:
                os.path.exists(os.path.join(d, ARM + ".json")) and os.unlink(os.path.join(d, ARM + ".json"))
                write_read_record(d, ARM, **over)
                self.assertEqual(rrp(d, ARM)[0], label, over)
        p = Path(d) / (ARM + ".json")
        for junk in ("not json", "[]", json.dumps({"bot_id": ARM}), ""):
            p.write_text(junk)
            self.assertEqual(rrp(d, ARM)[0], "read_record_unreadable", junk)

    def test_symlink_and_hardlink_tricks(self):
        d = self._dir()
        other = self._dir("other")
        write_read_record(other, ARM)
        os.symlink(os.path.join(other, ARM + ".json"), os.path.join(d, ARM + ".json"))  # record file is a symlink
        self.assertEqual(rrp(d, ARM)[0], "read_record_not_regular")
        os.unlink(os.path.join(d, ARM + ".json"))
        os.link(os.path.join(other, ARM + ".json"), os.path.join(d, ARM + ".json"))  # record file is a hard link (nlink 2)
        self.assertEqual(rrp(d, ARM)[0], "read_record_hardlink")
        os.unlink(os.path.join(d, ARM + ".json"))
        d2 = os.path.join(self.tmp, "symdir")  # a directory symlink to a clean dir resolves by realpath and is judged where it lands
        os.symlink(other, d2)
        self.assertIsNone(rrp(d2, ARM)[0])

    def test_sealed_prefix_dir_refused_except_the_canonical_reads_dir(self):
        with ScratchArena(self.tmp) as a:
            write_read_record(os.path.join(a.root, "reads"), ARM)
            self.assertIsNone(rrp(os.path.join(a.root, "reads"), ARM)[0])  # exactly the canonical dir
            os.makedirs(os.path.join(a.root, "decisions"))
            write_read_record(os.path.join(a.root, "decisions"), ARM)
            self.assertEqual(rrp(os.path.join(a.root, "decisions"), ARM)[0], "read_record_dir_sealed")
            self.assertEqual(rrp(a.root, ARM)[0], "read_record_dir_sealed")
            link = os.path.join(self.tmp, "alias-of-decisions")  # a symlink that lands under the prefix
            os.symlink(os.path.join(a.root, "decisions"), link)
            self.assertEqual(rrp(link, ARM)[0], "read_record_dir_sealed")
            self.assertEqual(rrp(os.path.join(a.root, "reads", "..", "decisions"), ARM)[0], "read_record_dir_sealed")

    def test_bind_alias_of_the_sealed_prefix_is_caught_by_st_dev_st_ino(self):
        with ScratchArena(self.tmp) as a:
            alias = self._dir("data2-alias")  # a different name; a bind mount would show the SAME (st_dev, st_ino) as the prefix
            write_read_record(alias, ARM)
            self.assertIsNone(rrp(alias, ARM)[0])
            real_stat = os.stat

            def fake(path, *args, **kw):
                st = real_stat(a.root if os.fspath(path) == alias else path, *args, **kw)
                return st

            with mock.patch("os.stat", fake):
                self.assertEqual(rrp(alias, ARM)[0], "read_record_dir_sealed_alias")
            real_reads = os.path.join(a.root, "reads")

            def fake2(path, *args, **kw):
                return real_stat(real_reads if os.fspath(path) == alias else path, *args, **kw)

            with mock.patch("os.stat", fake2):  # an alias of the canonical reads dir under another name is refused as well
                self.assertEqual(rrp(alias, ARM)[0], "read_record_dir_sealed_alias")

    def test_identity_pin_catches_rewrite_swap_and_unlink(self):
        d = self._dir()
        write_read_record(d, ARM)
        why, ident = rrp(d, ARM)
        self.assertIsNone(why)
        self.assertIsNone(rrp(d, ARM, ident)[0])  # unchanged
        p = Path(d) / (ARM + ".json")
        p.write_text(p.read_text().replace("120", "121"))  # same inode, other bytes
        self.assertEqual(rrp(d, ARM, ident)[0], "read_record_changed")
        write_read_record(d, ARM)
        why, ident = rrp(d, ARM)
        raw = p.read_bytes()
        fresh = Path(d) / "fresh.tmp"
        fresh.write_bytes(raw)  # created while the old file still exists: a different inode
        os.replace(fresh, p)  # same bytes, new inode
        self.assertNotEqual(os.stat(p).st_ino, ident["ino"])
        self.assertEqual(rrp(d, ARM, ident)[0], "read_record_changed")
        p.unlink()
        self.assertEqual(rrp(d, ARM, ident)[0], "read_record_missing")


class ModelIdTests(TmpCase):
    def test_f_arm_needs_exactly_claude_haiku_5_5(self):
        self.assertEqual(c0.MODEL_ID, "claude-haiku-5-5")
        self.assertIsNone(c0.model_problem(FARM, ["claude-haiku-5-5"]))
        for bad in ([], ["claude-haiku-4-5"], ["claude-haiku-5-5", "claude-haiku-4-5"], ["claude-haiku-5-5", "claude-haiku-5-5"], "claude-haiku-5-5", None, [5]):
            self.assertNotEqual(c0.model_problem(FARM, bad), None, bad)
        self.assertEqual(c0.model_problem(FARM, ["claude-haiku-4-5"]), "model_id_not_pinned")
        self.assertEqual(c0.model_problem(FARM, None), "model_ids_unreadable")

    def test_x_arm_has_no_model_or_the_same_one(self):
        self.assertIsNone(c0.model_problem(ARM, []))
        self.assertIsNone(c0.model_problem(ARM, ["claude-haiku-5-5"]))
        self.assertEqual(c0.model_problem(ARM, ["other"]), "model_id_not_pinned")

    def test_record_with_wrong_model_fails_the_gate(self):
        d = os.path.realpath(self.tmp)
        write_read_record(d, FARM)
        self.assertIsNone(rrp(d, FARM)[0])
        write_read_record(d, "hv4-BIG-F", model_ids=["claude-haiku-4-5"])
        self.assertEqual(rrp(d, "hv4-BIG-F")[0], "model_id_not_pinned")


class GuardAliasTests(TmpCase):
    def test_decision_file_and_dir_inode_alias_of_the_sealed_prefix(self):
        with ScratchArena(self.tmp) as a:
            root = os.path.realpath(os.path.join(self.tmp, "data2-root"))
            d = os.path.join(root, "decisions", ARM)
            os.makedirs(d)
            mine = os.path.join(d, "2026-10-26.jsonl")
            Path(mine).write_text("x\n")
            self.assertEqual(c0.guarded_read(root, ARM, "2026-10-26.jsonl"), b"x\n")  # a clean root reads
            real_stat = os.stat
            sealed_file = os.path.join(a.root, "decisions", ARM, "2026-10-26.jsonl")

            def file_alias(path, *args, **kw):  # the sealed counterpart of this name has OUR file's (st_dev, st_ino): a bind or alias of the arena day file
                return real_stat(mine if os.fspath(path) == sealed_file else path, *args, **kw)

            with mock.patch("os.stat", file_alias):
                with self.assertRaises(c0.SealedPath) as cm:
                    c0.guarded_read(root, ARM, "2026-10-26.jsonl")
                self.assertEqual(str(cm.exception), "inode_is_arena_file")
            os.makedirs(os.path.join(a.root, "decisions"))
            alias_dec = os.path.join(root, "decisions")

            def dir_alias(path, *args, **kw):  # our decisions dir is the sealed decisions dir's inode under another name
                return real_stat(os.path.join(a.root, "decisions") if os.fspath(path) == alias_dec else path, *args, **kw)

            with mock.patch("os.stat", dir_alias):
                with self.assertRaises(c0.SealedPath):
                    c0.guard_dir(root, ARM)
                with self.assertRaises(c0.SealedPath):
                    c0.guarded_read(root, ARM, "2026-10-26.jsonl")
            self.assertEqual(c0.guard_dir(root, ARM), d)  # patches undone: clean again


class BridgeReadGateTests(TmpCase):
    def _spy(self):
        return mock.patch.object(c0, "guarded_read", wraps=c0.guarded_read), mock.patch.object(c0, "guarded_listdir", wraps=c0.guarded_listdir)

    def test_valid_record_reads_and_buys(self):
        e = Hv4Env(self.tmp)
        e.start()
        e.entry_sz("o1")
        b = e.bridge()
        b.tick()
        self.assertEqual(b.state.latches, {})
        self.assertEqual(len([c for c in e.port.calls if c[0] == "buy"]), 1)

    def test_no_record_means_no_decision_row_is_listed_or_read(self):
        e = Hv4Env(self.tmp)
        e.start()
        e.entry_sz("o1")
        os.unlink(os.path.join(e.raw["reads_dir"], ARM + ".json"))
        rd, ld = self._spy()
        with rd as r, ld as l:
            b = e.bridge()
            b.tick()
            b.tick()
            self.assertEqual((r.call_count, l.call_count), (0, 0))
        self.assertEqual(b.state.latches.get("read_record"), "read_record_missing")
        self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])
        self.assertEqual(b.state.seq, -1)

    def test_config_without_reads_dir_never_reads(self):
        raw = {k: v for k, v in Hv4Env(self.tmp).raw.items() if k != "reads_dir"}
        e = Hv4Env(self.tmp)
        e.raw = raw
        e.start()
        e.entry_sz("o1")
        rd, ld = self._spy()
        with rd as r, ld as l:
            b = e.bridge()
            b.tick()
            self.assertEqual((r.call_count, l.call_count), (0, 0))
        self.assertEqual(b.state.latches.get("read_record"), "read_record_dir_missing")

    def test_each_bad_record_latches_before_any_read(self):
        for i, over in enumerate((dict(status="FAIL"), dict(bot_id="hv4-BIG-X"), dict(boundary_source="forced"))):
            sub = Path(self.tmp) / f"s{i}"
            sub.mkdir()
            e = Hv4Env(str(sub))
            (Path(e.raw["reads_dir"]) / (ARM + ".json")).unlink()
            write_read_record(e.raw["reads_dir"], ARM, **over)
            e.start()
            e.entry_sz("o1")
            rd, ld = self._spy()
            with rd as r, ld as l:
                b = e.bridge()
                b.tick()
                self.assertEqual((r.call_count, l.call_count), (0, 0), over)
            self.assertIn("read_record", b.state.latches)
            self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])

    def test_record_swapped_after_start_latches_and_reads_nothing_more(self):
        e = Hv4Env(self.tmp)
        e.start()
        e.entry_sz("o1", pool="P1")
        b = e.bridge()
        b.tick()
        self.assertEqual(len([c for c in e.port.calls if c[0] == "buy"]), 1)
        p = Path(e.raw["reads_dir"]) / (ARM + ".json")
        write_read_record(self.tmp + "/other", ARM)
        p.unlink()
        os.link(self.tmp + "/other/" + ARM + ".json", p)  # swapped for a hard link
        e.entry_sz("o2", pool="P2")
        rd, ld = self._spy()
        with rd as r, ld as l:
            b.tick()
            self.assertEqual((r.call_count, l.call_count), (0, 0))
        self.assertIn("read_record", b.state.latches)
        self.assertEqual(len([c for c in e.port.calls if c[0] == "buy"]), 1)

    def test_record_rewritten_after_start_latches(self):
        e = Hv4Env(self.tmp)
        e.start()
        b = e.bridge()
        b.tick()
        self.assertEqual(b.state.latches, {})
        p = Path(e.raw["reads_dir"]) / (ARM + ".json")
        p.write_text(p.read_text().replace("120", "999"))
        e.entry_sz("o1")
        b.tick()
        self.assertEqual(b.state.latches.get("read_record"), "read_record_changed")
        self.assertEqual([c for c in e.port.calls if c[0] == "buy"], [])

    def test_plain_profile_does_not_use_the_gate(self):
        from tools.test_arena_live_bridge import Env
        e = Env(self.tmp)
        e.start()
        e.entry("o1")
        b = e.bridge()
        b.tick()
        self.assertEqual(len(e.port.calls), 1)
        self.assertNotIn("read_record", b.state.latches)

    def test_reads_dir_belongs_to_the_profile(self):
        from tools.test_arena_live_bridge import Env
        with self.assertRaises(ValueError):
            Env(self.tmp, reads_dir="/x").bridge()


class StartRowTests(TmpCase):
    def test_bridge_start_row_carries_the_pins(self):
        e = Hv4Env(self.tmp)
        e.bridge()
        rows = e.log_rows("bridge_start")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["frozen_sha"], c0.FROZEN_SHA)
        self.assertEqual(r["config_sha256"], c0.CONFIG_PIN_SHA256)
        self.assertTrue(r["config_sha256"].startswith("1e611a5a"))
        self.assertEqual(r["ladder_sha256"], c0.ladder_pin_sha256())
        self.assertEqual(r["twin_writer_sha256"], c0.TWIN_WRITER_SHA256)
        self.assertEqual(len(r["sell_pin_sha256"]), 64)
        self.assertEqual(r["priority_pin"], c0.PRIORITY_PIN)
        self.assertEqual(r["model_id"], "claude-haiku-5-5")

    def test_key_name_frozen_sha_is_the_runner_start_rows_key(self):
        src = (Path(__file__).parent / "hv4_runner.py").read_text()
        self.assertIn('{"kind": "start", "binding": self.binding, "frozen_sha": self.cfg.frozen_sha', src)

    def test_sell_pin_sha_changes_when_the_pin_changes(self):
        a = c0.start_row_fields()["sell_pin_sha256"]
        with mock.patch.object(c0, "SELL_BUILDER_PIN", {**c0.SELL_BUILDER_PIN, "symbol_sha256": "0" * 64}):
            self.assertNotEqual(c0.start_row_fields()["sell_pin_sha256"], a)

    def test_plain_profile_writes_no_bridge_start(self):
        from tools.test_arena_live_bridge import Env
        e = Env(self.tmp)
        e.bridge()
        self.assertEqual(e.log_rows("bridge_start"), [])


class OwnerLineTests(TmpCase):
    def _fx(self, text=None, bot=ARM):
        p, sha = owner_fixture(self.tmp, bot)
        if text is not None:
            p.write_text(text)
            import hashlib
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
        return p, sha

    def test_exact_line_with_pinned_blob(self):
        p, sha = self._fx()
        self.assertIsNone(c0.owner_line_problem(p, ARM, sha))

    def test_not_a_match(self):
        want = c0.OWNER_LINE_FMT.format(bot_id=ARM)
        for text in ("x\n", want + "2\n", f"  {want}\n", f"{want} \n", want.replace("CONFIRMED", "confirmed") + "\n", f"# {want}\n", f"{want}\n{want}\n",
                     c0.OWNER_LINE_FMT.format(bot_id="hv4-CON-F") + "\n", f"OWNER_CANARY_CONFIRMED: bot_id={ARM}x\n"):
            p, sha = self._fx(text)
            self.assertEqual(c0.owner_line_problem(p, ARM, sha), "owner_line_missing", text)

    def test_blob_must_equal_the_pin(self):
        p, sha = self._fx()
        self.assertEqual(c0.owner_line_problem(p, ARM, "0" * 64), "owner_dec_sha_mismatch")
        p.write_text(p.read_text() + "edited after the pin\n")
        self.assertEqual(c0.owner_line_problem(p, ARM, sha), "owner_dec_sha_mismatch")

    def test_unset_or_malformed_pin_means_no_live(self):
        p, _ = self._fx()
        for bad in ("", "abc", "G" * 64, "A" * 64):
            self.assertEqual(c0.owner_line_problem(p, ARM, bad), "owner_dec_pin_unset", bad)
        self.assertEqual(c0.CANARY_DEC_SHA256, "")  # no pin is committed in this PR: the line stays closed
        self.assertEqual(c0.owner_line_problem(p, ARM), "owner_dec_pin_unset")

    def test_missing_symlink_and_root_check(self):
        p, sha = self._fx()
        self.assertEqual(c0.owner_line_problem(Path(self.tmp) / "none.md", ARM, sha), "owner_dec_missing")
        link = Path(self.tmp) / "link.md"
        os.symlink(p, link)
        self.assertEqual(c0.owner_line_problem(link, ARM, sha), "owner_dec_unreadable")
        self.assertEqual(c0.owner_line_problem(p, ARM, sha, lambda _p: "not_root_owned"), "owner_dec_not_root_owned")
        self.assertIsNone(c0.owner_line_problem(p, ARM, sha, lambda _p: None))

    def test_the_path_is_outside_the_installed_tree(self):
        repo = Path(__file__).resolve().parent.parent
        self.assertTrue(c0.OWNER_DEC_PATH.startswith("/etc/"))
        self.assertNotIn(str(repo), c0.OWNER_DEC_PATH)
        self.assertNotIn("/DEC/", c0.OWNER_DEC_PATH)

    def test_bridge_routes_to_dry_without_the_line_and_live_with_it(self):
        e = Hv4Env(self.tmp, live=True)
        e.live_ok.write_text(ARM + "\n")
        e.tier.write_text("T1\n")
        b = e.bridge()
        self.assertEqual(b.effective_live(), (False, "live_ok_owner_dec_pin_unset"))
        p, sha = self._fx()
        b.owner_dec_path, b.owner_pin = p, sha
        self.assertEqual(b.effective_live(), (True, None))
        other, sha2 = self._fx(c0.OWNER_LINE_FMT.format(bot_id="hv4-BIG-X") + "\n")
        b.owner_dec_path, b.owner_pin = other, sha2
        self.assertEqual(b.effective_live(), (False, "live_ok_owner_line_missing"))

    def test_main_live_refuses_without_the_owner_line_before_the_incomplete_refusal(self):
        e = Hv4Env(self.tmp)
        full = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")}
        paths, _st = live_main_env(self.tmp, ARM)
        with mock.patch.object(c0, "CANARY_DEC_SHA256", "0" * 64), mock.patch.object(c0, "OWNER_DEC_PATH", str(owner_fixture(self.tmp, ARM)[0])), \
                mock.patch.object(alb, "root_ok_problem", lambda _p: None):
            cp = Path(self.tmp) / "cfg.json"
            cp.write_text(json.dumps({**full, **paths, "mode": "live"}))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = alb.main(["--config", str(cp), "--live"])
        self.assertEqual(rc, 2)
        self.assertIn("owner_dec_sha_mismatch", buf.getvalue())


class SOnTheLivePortTests(TmpCase):
    """Uses the PR2 port builder (throwaway key, fake RPC) without inheriting its tests."""

    _port = LivePortScheduleTests._port

    def _transfers(self, b64):
        msg = decode_tx(b64)
        keys = list(msg.account_keys)
        out = []
        for ix in msg.instructions:
            d = bytes(ix.data)
            if str(keys[ix.program_id_index]) == "11111111111111111111111111111111" and len(d) == 12 and d[:4] == b"\x02\x00\x00\x00":
                out.append(int.from_bytes(d[4:], "little"))
        return out

    def test_s_buy_sends_the_orders_stake_not_the_config_stake(self):
        from tools.test_probe_executor import MINT
        port, rpc, clock, cfg = self._port(True)
        s = 30_000_000
        self.assertLess(s, cfg.stake_lamports)
        ok, why = port.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": s})
        self.assertTrue(ok, why)
        self.assertEqual(port.ex.limits.size_lamports, s)
        self.assertIn(s, self._transfers(rpc.sent[-1][1]))
        self.assertNotIn(cfg.stake_lamports, self._transfers(rpc.sent[-1][1]))

    def test_s_stake_above_table_or_malformed_is_refused_before_any_send(self):
        from tools.test_probe_executor import MINT
        port, rpc, clock, cfg = self._port(True)
        n = len(rpc.sent)
        for bad in (cfg.stake_lamports + 1, 0, -5, None, True, "5", 50_000_001):
            ok, why = port.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": bad})
            self.assertEqual((ok, why), (False, "stake_not_s"), bad)
        self.assertEqual(len(rpc.sent), n)

    def test_s_full_stake_still_sends_the_table_stake(self):
        from tools.test_probe_executor import MINT
        port, rpc, clock, cfg = self._port(True)
        ok, why = port.buy({"order_id": "o1", "mint": MINT, "decision_ms": clock(), "stake_lamports": cfg.stake_lamports})
        self.assertTrue(ok, why)
        self.assertIn(50_000_000, self._transfers(rpc.sent[-1][1]))

    def test_s_bridge_stake_mismatch_latch_still_fires(self):
        from tools import hv4_c0_parity as c0p
        self.assertIsNone(c0p.stake_problem({"landed": True, "spend_lamports": 30_000_000}, 30_000_000))
        self.assertTrue(c0p.stake_problem({"landed": True, "spend_lamports": 50_000_000}, 30_000_000).startswith("stake_mismatch"))


class PerTradeLossTests(TmpCase):
    def test_thirty_percent_of_s(self):
        self.assertEqual(c0.PER_TRADE_LOSS_PCT, 30)
        self.assertEqual(c0.per_trade_loss_lamports(50_000_000), 15_000_000)
        self.assertEqual(c0.per_trade_loss_lamports(20_000_000), 6_000_000)
        self.assertEqual(c0.per_trade_loss_lamports(7), 2)
        self.assertEqual(c0.per_trade_loss_lamports(50_000_000, 10_000_000), 10_000_000)  # config tightens
        self.assertEqual(c0.per_trade_loss_lamports(50_000_000, 40_000_000), 15_000_000)  # config never loosens

    def _book(self, stake, pnl, **over):
        sub = tempfile.mkdtemp(dir=self.tmp)
        e = Hv4Env(sub, **over)
        b = e.bridge()
        b._book_pnl({"order_id": "o", "stake_lamports": stake}, pnl, "t")
        return b

    def test_latch_at_exactly_30_percent_of_the_orders_own_s(self):
        self.assertNotIn("per_trade_loss_cap", self._book(20_000_000, -5_999_999).state.latches)
        self.assertIn("per_trade_loss_cap", self._book(20_000_000, -6_000_000).state.latches)
        self.assertNotIn("per_trade_loss_cap", self._book(50_000_000, -14_999_999).state.latches)
        self.assertIn("per_trade_loss_cap", self._book(50_000_000, -15_000_000).state.latches)
        self.assertNotIn("per_trade_loss_cap", self._book(20_000_000, 3_000_000).state.latches)

    def test_config_can_only_tighten(self):
        self.assertIn("per_trade_loss_cap", self._book(50_000_000, -5_000_000, per_trade_loss_lamports=5_000_000).state.latches)
        self.assertNotIn("per_trade_loss_cap", self._book(20_000_000, -5_999_999, per_trade_loss_lamports=50_000_000).state.latches)

    def test_plain_profile_keeps_its_15m_limit(self):
        from tools.test_arena_live_bridge import Env
        e = Env(self.tmp)
        b = e.bridge()
        b._book_pnl({"order_id": "o", "stake_lamports": 20_000_000}, -6_000_000, "t")
        self.assertNotIn("per_trade_loss_cap", b.state.latches)


class WindowCapTests(TmpCase):
    def test_pure_edges(self):
        self.assertEqual(c0.MAX_WINDOW_MS, 14 * DAY)
        self.assertIsNone(c0.end_cap_problem(1000 + 14 * DAY, 1000))
        self.assertEqual(c0.end_cap_problem(1001 + 14 * DAY, 1000), "end_ms_over_14d")

    def test_bridge_refuses_an_end_past_14_days_and_persists_the_first_start(self):
        e = Hv4Env(self.tmp, end_ms=0)
        t = e.clock.t
        for end, ok in ((t + 14 * DAY, True), (t + 14 * DAY + 1, False)):
            sub = tempfile.mkdtemp(dir=self.tmp)
            e = Hv4Env(sub, end_ms=end)
            if ok:
                self.assertEqual(e.bridge().state.first_start_ms, e.clock.t)
            else:
                with self.assertRaises(SystemExit) as cm:
                    e.bridge()
                self.assertIn("end_ms_over_14d", str(cm.exception))

    def test_restart_does_not_move_the_window(self):
        e = Hv4Env(self.tmp, end_ms=0)
        t0 = e.clock.t
        e = Hv4Env(tempfile.mkdtemp(dir=self.tmp), end_ms=t0 + 13 * DAY)
        b = e.bridge()
        self.assertEqual(b.state.first_start_ms, t0)
        e.clock.t = t0 + 10 * DAY  # a restart ten days in: a window that would reach t0 + 24 d from now is NOT allowed
        e.raw["end_ms"] = t0 + 14 * DAY
        e.bridge()
        e.raw["end_ms"] = t0 + 14 * DAY + 1
        with self.assertRaises(SystemExit):
            e.bridge()

    def test_main_checks_the_cap_against_the_saved_state(self):
        e = Hv4Env(self.tmp)
        full = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")}
        full["end_ms"] = int(__import__("time").time() * 1000) + 15 * DAY
        cp = Path(self.tmp) / "cfg.json"
        cp.write_text(json.dumps({**full, "mode": "dry"}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = alb.main(["--config", str(cp), "--once"])
        self.assertEqual(rc, 2)
        self.assertIn("end_ms_over_14d", buf.getvalue())


class MainPathChecksTests(TmpCase):
    def _run(self, raw_over, live=True, drop=(), rm_record=False):
        e = Hv4Env(self.tmp)
        if rm_record:
            os.unlink(os.path.join(self.tmp, "reads", ARM + ".json"))
        full = {k: v for k, v in e.raw.items() if k not in ("stop_file", "halt_file")}
        paths, st = live_main_env(self.tmp, ARM)
        full.update(paths)
        full.update(raw_over)
        for k in drop:
            full.pop(k, None)
        Path(full["root"]).mkdir(parents=True, exist_ok=True)
        (Path(full["root"]) / c0.SYNTHETIC_MARKER).write_text("")
        cp = Path(self.tmp) / "cfg.json"
        cp.write_text(json.dumps({**full, "mode": "live" if live else "dry"}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), st:
            rc = alb.main(["--config", str(cp), "--once"] + (["--live"] if live else []))
        return rc, buf.getvalue()

    def test_live_needs_all_three_parity_paths_absolute(self):
        rc, out = self._run({}, drop=("parity_dir", "twins_file", "tieout_dir"))
        self.assertEqual(rc, 2)
        self.assertIn("parity_path_missing:parity_dir", out)
        rc, out = self._run({}, drop=("tieout_dir",))  # one or two of them: Config refuses
        self.assertEqual(rc, 2)
        self.assertIn("set together", out)
        rc, out = self._run({"twins_file": "relative.jsonl"})
        self.assertEqual(rc, 2)
        self.assertIn("parity_path_missing:twins_file", out)

    def test_with_the_paths_a_live_start_reaches_the_incomplete_refusal(self):
        rc, out = self._run({})
        self.assertEqual(rc, 2)
        self.assertIn(c0.LIVE_REFUSAL, out)  # still refused: PR5 removes this

    def test_dry_start_on_a_synthetic_root_does_not_need_the_paths(self):
        rc, out = self._run({}, live=False, drop=("parity_dir", "twins_file", "tieout_dir"))
        self.assertEqual(rc, 0, out)

    def test_no_read_record_refuses_a_start_dry_or_live(self):
        for live in (False, True):
            rc, out = self._run({}, live=live, rm_record=True)
            self.assertEqual(rc, 2)
            self.assertIn("read_record_missing", out)
            self.assertNotIn(c0.LIVE_REFUSAL, out)
            os.unlink(os.path.join(self.tmp, "cfg.json"))


if __name__ == "__main__":
    unittest.main()
