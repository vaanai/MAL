"""tools/s1_ws2_probe.py: pure parts only (no network, no websockets import). Fixture key files and fixture `ss` text only."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import s1_ws2_probe as P

REPO = Path(__file__).resolve().parent.parent
SS = """0      0      10.0.0.5:40001      [::ffff:1.2.3.4]:443
\t cubic wscale:7,7 rto:204 bytes_sent:100 bytes_received:5000 segs_out:3
0      0      10.0.0.5:40002      1.2.3.5:443
\t cubic bytes_received:7000
0      0      10.0.0.5:40003      9.9.9.9:443
\t cubic bytes_received:123
0      0      [2001:db8::5]:40004      [2001:db8::1]:443
\t cubic
"""


def note(slot, sig, err=None):
    return {"jsonrpc": "2.0", "method": "logsNotification", "params": {"result": {"context": {"slot": slot}, "value": {"signature": sig, "err": err, "logs": ["x"]}}}}


class Pure(unittest.TestCase):
    def test_url_ok_refuses_keys_queries_userinfo(self):
        self.assertTrue(P.url_ok("wss://solana-rpc.publicnode.com"))
        self.assertTrue(P.url_ok("wss://solana.api.onfinality.io/public-ws"))
        for bad in ("ws://x.org", "wss://", "wss://x.org/?api-key=1", "wss://u:p@x.org", "wss://x.org/#f", "wss://x.org/apikey/abc", "https://x.org",
                    "wss://x.org/a b"):
            self.assertFalse(P.url_ok(bad), bad)

    def test_split_addr(self):
        self.assertEqual(P.split_addr("[::ffff:1.2.3.4]:443"), ("1.2.3.4", "443"))
        self.assertEqual(P.split_addr("1.2.3.5:443"), ("1.2.3.5", "443"))
        self.assertEqual(P.split_addr("[2001:db8::1]:443"), ("2001:db8::1", "443"))
        self.assertEqual(P.split_addr("[fe80::1%eth0]:443"), ("fe80::1", "443"))

    def test_parse_ss(self):
        d = P.parse_ss(SS)
        self.assertEqual(d[("10.0.0.5:40001", "[::ffff:1.2.3.4]:443")], 5000)
        self.assertEqual(d[("10.0.0.5:40002", "1.2.3.5:443")], 7000)
        self.assertIsNone(d[("[2001:db8::5]:40004", "[2001:db8::1]:443")])
        self.assertEqual(len(d), 4)

    def test_watcher_counts_new_ended_long_and_bytes(self):
        w = P.Watcher("unused.invalid", 5)
        w.ips = {"1.2.3.4", "1.2.3.5"}
        w.phase = "a"
        w.own_ports = {"40003"}
        w.update(P.parse_ss(SS), 5.0)                     # first sample: nothing is "new"
        s2 = SS.replace("bytes_received:5000", "bytes_received:6000")
        for _ in range(3):
            w.update(P.parse_ss(s2), 5.0)                 # 40001 +1000 once; 40002 flat
        w.phase = "b"
        only_one = "0 0 10.0.0.5:40009 1.2.3.4:443\n\t bytes_received:10\n"
        w.update(P.parse_ss(only_one), 5.0)               # 2 long-lived ended, 1 new
        a, b = w.stats["a"], w.stats["b"]
        self.assertEqual((a["samples"], a["new"], a["ended"], a["bytes"], a["open_sum"]), (4, 0, 0, 1000, 8))
        self.assertEqual((b["new"], b["ended"], b["ended_long"]), (1, 2, 2))
        self.assertEqual(w.own_bytes[("10.0.0.5:40003", "9.9.9.9:443")], 123)
        summ = w.summary()
        self.assertEqual(summ["a"]["mean_open"], 2.0)
        self.assertEqual(summ["b"]["ended_long"], 2)

    def test_stream_note_ack_error_slots_silence(self):
        st = P.Stream("c", keep_detail=True)
        st.note({"jsonrpc": "2.0", "id": 1, "result": 77}, 1000)
        self.assertTrue(st.sub_ok)
        st.note(note(100, "A" * 88), 2000)
        st.note(note(131, "B" * 88, err={"x": 1}), 2500)  # jump of 31 > 30
        st.note(note(140, "C" * 88), 23_600)              # 21.1 s after the last notice > 20 s
        st.note(note(120, "D" * 88), 23_700)              # older slot: no jump, last_slot stays 140
        self.assertEqual((st.notes, st.slot_jumps, st.max_slot_jump, st.silences, st.err_notes, st.last_slot), (4, 1, 31, 1, 1, 140))
        self.assertEqual(st.sigs["A" * P.SIG_KEEP], 2000)
        st2 = P.Stream("r", keep_detail=False)
        st2.note({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "secret-ish provider text"}}, 1)
        self.assertEqual(st2.sub_error, "-32601")
        st3 = P.Stream("c", keep_detail=True)
        st3.note({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}, 1)
        self.assertEqual(st3.sub_error, "-32601 Method not found")
        self.assertFalse(st3.sub_ok)

    def test_up_share(self):
        st = P.Stream("c", True)
        st.mark_up(1000)
        st.mark_down(4000)
        st.mark_down(5000)
        st.mark_up(6000)
        s = P.stream_summary(st, 10.0, 8000)
        self.assertEqual(s["up_share"], 0.5)

    def test_freeze_stops_counting_at_the_window_end(self):
        st = P.Stream("c", True)
        st.mark_up(0)
        st.freeze(1000)
        st.mark_up(2000)
        st.note(note(5, "E" * 88), 3000)
        self.assertEqual((st.up_ms, st.notes, st.sigs), (1000, 0, {}))
        self.assertEqual(P.stream_summary(st, 1.0, 5000, 0)["up_share"], 1.0)

    def test_short_window_trims_15_percent(self):
        ref, cand = P.Stream("r", False), P.Stream("c", True)
        ref.sigs = {"a": 1_000, "b": 5_000, "c": 9_500}   # window 0..10 s: trim 1.5 s -> only b is in the middle
        cand.sigs = {"b": 5_100}
        c = P.coverage(cand, ref, 0, 10_000)
        self.assertEqual((c["ref_sigs_mid"], c["coverage"], c["lead_ms_p50"]), (1, 1.0, 100))

    def test_coverage_and_rank(self):
        ref, cand = P.Stream("r", False), P.Stream("c", True)
        t0, t1 = 0, 100_000
        for i in range(10):
            ref.sigs[f"s{i}"] = 20_000 + i * 1000
        for i in range(8):
            cand.sigs[f"s{i}"] = 20_000 + i * 1000 + (50 if i % 2 else -10)
        cand.sigs["extra"] = 30_000
        cand.sigs["edge"] = 5_000                         # outside the trimmed middle: not counted
        c = P.coverage(cand, ref, t0, t1)
        self.assertEqual((c["ref_sigs_mid"], c["cand_has"], c["coverage"], c["cand_sigs_mid"]), (10, 8, 0.8, 9))
        self.assertEqual(c["cand_extra_share"], round(1 / 9, 4))
        self.assertEqual((c["lead_ms_p50"], c["cand_first_share"]), (50, 0.5))
        mk = lambda lab, ok, n, cov, rec: {"label": lab, "cand": {"sub_ok": ok, "notes": n, "reconnects": rec}, "coverage_vs_ref": {"coverage": cov}}
        order = [r["label"] for r in P.rank([mk("a", True, 100, 0.5, 0), mk("b", True, 50, 0.99, 0), mk("c", False, 999, 1.0, 0), mk("d", True, 0, 0.0, 0)])]
        self.assertEqual(order, ["b", "a"])

    def test_keyed_candidate_guards(self):
        self.assertTrue(P.keyed_ok("wss://fra.rpc.orbitflare.com", "ORBITFLARE_KEY", "api_key"))
        self.assertTrue(P.keyed_ok("wss://mainnet.helius-rpc.com/", "HELIUS_API_KEY", "api-key"))
        self.assertFalse(P.keyed_ok("wss://fra.rpc.orbitflare.com", "HELIUS_API_KEY", "api_key"))      # the Helius key never goes to another provider
        self.assertFalse(P.keyed_ok("wss://evil-helius-rpc.com", "HELIUS_API_KEY", "api-key"))
        self.assertFalse(P.keyed_ok("wss://rpc.shyft.to/?api_key=x", "SHYFT_KEY", "api_key"))
        self.assertFalse(P.keyed_ok("wss://rpc.shyft.to", "SHYFT KEY", "api_key"))
        self.assertFalse(P.keyed_ok("wss://rpc.shyft.to", "SHYFT_KEY", "api key"))
        self.assertEqual(P.with_key("wss://rpc.shyft.to", "api_key", "abcd1234"), "wss://rpc.shyft.to?api_key=abcd1234")
        with tempfile.TemporaryDirectory() as d:
            kf = Path(d) / "k"
            kf.write_text("export SHYFT_KEY='abcd-1234XY'\nOTHER=1\n")
            self.assertEqual(P.read_var(str(kf), "SHYFT_KEY"), "abcd-1234XY")
            self.assertIsNone(P.read_var(str(kf), "NOPE"))
            kf.write_text("SHYFT_KEY=has_underscore\n")
            self.assertIsNone(P.read_var(str(kf), "SHYFT_KEY"))       # same charset as the s1 launcher's KEY_RE

    def test_keyed_cli_refusals_never_print_the_key(self):
        exe = [sys.executable, "-I", str(REPO / "tools" / "s1_ws2_probe.py")]
        with tempfile.TemporaryDirectory() as d:
            kf = Path(d) / "k"
            kf.write_text("HELIUS_API_KEY=fixture0key-9\nOF_KEY=fixture1key-9\n")
            r = subprocess.run(exe + ["--keyed-url", "wss://fra.rpc.orbitflare.com", "--key-file", str(kf), "--key-var", "HELIUS_API_KEY"], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2)
            r2 = subprocess.run(exe + ["--keyed-url", "wss://fra.rpc.orbitflare.com", "--key-file", str(kf)], capture_output=True, text=True, timeout=30)
            self.assertEqual(r2.returncode, 2)
            r3 = subprocess.run(exe + ["--keyed-url", "wss://fra.rpc.orbitflare.com", "--key-file", str(kf), "--key-var", "MISSING"], capture_output=True, text=True, timeout=30)
            self.assertEqual(r3.returncode, 2)
            r4 = subprocess.run(exe + ["--keyed-url", "wss://fra.rpc.orbitflare.com", "--key-file", str(kf), "--key-var", "OF_KEY", "--top", "9"], capture_output=True, text=True, timeout=30)
            self.assertEqual(r4.returncode, 2)
            for x in (r, r2, r3, r4):
                self.assertNotIn("fixture0key", x.stdout + x.stderr)
                self.assertNotIn("fixture1key", x.stdout + x.stderr)

    def test_subscription_is_the_credit_probes(self):
        from tools import q50s1_ws_probe as Q
        self.assertIs(P.subscribe_request, Q.subscribe_request)  # tested equal to S*'s builder in tools/test_q50s1_ws_probe.py

    def test_cli_refusals_open_nothing_and_never_print_the_key(self):
        exe = [sys.executable, "-I", str(REPO / "tools" / "s1_ws2_probe.py")]
        r = subprocess.run(exe + ["--url", "wss://x.org/?api-key=abc"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("abc", r.stdout + r.stderr)
        r = subprocess.run(exe + ["--url", "wss://x.org", "--url", "wss://x.org"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run(exe + ["--url", "wss://x.org", "--top", "9"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        with tempfile.TemporaryDirectory() as d:
            kf = Path(d) / "k"
            kf.write_text("HELIUS_API_KEY=bad key!\n")
            r = subprocess.run(exe + ["--url", "wss://x.org", "--ref-key-file", str(kf)], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2)
            self.assertNotIn("bad key", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
