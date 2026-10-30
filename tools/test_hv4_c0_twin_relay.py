"""tools/hv4_c0_twin_relay.py: the carry of the HV4 C0 paper twin between research-0 (ledger) and fast-0 (bridge). SYNTHETIC fixtures in tmp dirs only. Nothing here reads an
HV4 ledger, decision row, bots.json or job output; no network, no key. `/data/mal/arena` appears only as a string in refusal tests (the wrapper's path checks stat it)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tools import arena_live_parity as alp
from tools import arena_twin_writer as W
from tools import decision_forwarder as df
from tools import hv4_c0_parity as c0p
from tools import hv4_c0_twin_relay as R
from tools import hv4_live_c0 as c0
from tools.h5_arena_gates import ParityRecorder, load_twin_ids, twin_pin_problem, twin_run_stamp
from tools.test_arena_twin_writer import DEC, ledger_row, trip, write_ledger

ARM = "hv4-CON-X"
PINS = c0.twin_pins()
REPO = Path(__file__).resolve().parent.parent
NOW = 1_790_000_500_000  # a fixed request instant for the in-process tests


def jl(rows) -> bytes:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode()


def synth_trip(mint="MINT1", **over):
    return trip(mint, buy_fee_lamports=60_000, sell_fee_lamports=60_000, **over)


FAKE_SRC = r'''
import hashlib, json, sys
def fake_twin_main(argv, pins=None):
    """Stands in for tools.hv4_c0_twin.main: writes a deterministic twins file and run record for the trips it is given, and is noisy on both streams."""
    from tools import hv4_live_c0 as c0
    o = dict(a.split("=", 1) for a in argv)
    trips = [json.loads(x) for x in open(o["--trips"]).read().splitlines() if x.strip()]
    rows = [{"trip_id": t["trip_id"], "bot_id": o["--bot-id"], "position_id": "p-" + t["trip_id"], "state": "filled", "pnl_lamports": 1.5, "pnl_live_basis_lamports": 1.5,
             "pnl_e1_live_basis_lamports": 1.5, "entry_price": 2.0, "exit_price": 3.0} for t in sorted(trips, key=lambda x: x["trip_id"]) if not t["trip_id"].startswith("NOTWIN")]
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    open(o["--out"], "w").write(body)
    cfg, code = pins or c0.twin_pins()
    rec = {"schema": "arena_twin_run_v1", "run_ms": int(o["--now-ms"]), "covered_trip_ids": sorted(t["trip_id"] for t in trips), "twin_rows": len(rows),
           "twins_sha256": hashlib.sha256(body.encode()).hexdigest(), "config_hash": cfg, "code_sha256": code}
    open(o["--run-record"], "w").write(json.dumps(rec, sort_keys=True) + "\n")
    print("NOISE-ON-STDOUT note: something about a ledger row")
    print("NOISE-ON-STDERR", file=sys.stderr)
    return 0
'''
_ns: dict = {}
exec(FAKE_SRC, _ns)
fake_twin_main = _ns["fake_twin_main"]

STUB = "import sys\nsys.path.insert(0, sys.argv[1])\nfrom tools import hv4_c0_twin_relay as R\n" + FAKE_SRC + "\nsys.exit(R.serve_main(sys.argv[2:], twin_main=fake_twin_main))\n"


class Tmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.tmp = Path(self._d.name)
        self.twins = str(self.tmp / "twins.jsonl")
        self.trips = self.tmp / "trips.jsonl"
        self.ledger = str(self.tmp / "ledger" / ARM)

    def serve(self, raw: bytes, request: str | None = None, twin_main=fake_twin_main, **kw) -> dict:
        args = dict(bot_id=ARM, ledger_dir=self.ledger, reads_dir=str(self.tmp / "reads"), tmp_root=str(self.tmp), clock_ms=lambda: NOW)
        args.update(kw)
        return R.serve_once(io.BytesIO(raw), request or f"twin {NOW}", twin_main=twin_main, **args)


# ---------------------------------------------------------------------------------------------------------------------------------- schema
class TripSchemaTests(unittest.TestCase):
    def test_accepts_what_the_real_recorder_writes(self):
        """Open, exited, escalated, gap, unreadable and manually closed trips as tools.h5_arena_gates.ParityRecorder writes them."""
        with tempfile.TemporaryDirectory() as d:
            rec = ParityRecorder(Path(d))
            buy = dict(landed=True, tokens_received=1000, spend_lamports=50_000_000, signature="sigA", decision_t_ms=DEC, confirm_seen_ms=DEC + 1500, fee_lamports=60_000,
                       expected_tokens=1000, tokens_source="tx")
            rec.on_buy("MintA", buy, DEC + 1500, DEC + 340_000)
            rec.on_buy("MintB", dict(buy, signature="sigB"), DEC + 1600, DEC + 340_000)
            rec.on_buy("MintC", dict(buy, signature="sigC", tokens_received=None), DEC + 1700, DEC + 340_000)  # unreadable
            rec.on_buy("MintD", dict(buy, signature="sigD"), DEC + 1800, DEC + 340_000)
            rec.on_sell("MintA", dict(landed=True, pnl_lamports=-1234, tokens=1000, sol_received_lamports=49_000_000, confirm_seen_ms=DEC + 336_000, exit_reason="timer",
                                      fee_lamports=155_000, failed_attempt_cost_lamports=60_000), DEC + 336_000)
            rec.note_gap(DEC + 5000)
            rec.on_manual_close("MintD", dict(tokens_sold=1000, sol_delta_lamports=48_000_000, fee_lamports=60_000, rent_refunded_lamports=2_039_280, pnl_lamports=-2_000_000,
                                              block_time=(DEC + 400_000) // 1000))
            raw = (Path(d) / "trips.jsonl").read_bytes()
            rows, why = R.validate_trips(raw)
            self.assertIsNone(why, why)
            self.assertEqual(len(rows), 4)
            self.assertTrue(any(r.get("unreadable") for r in rows) and any(r["exit_landed"] for r in rows) and any(not r["exit_landed"] for r in rows))
            self.assertTrue(set().union(*(r.keys() for r in rows)) <= set(R.TRIP_FIELDS))

    def test_empty_input_is_a_valid_no_trips_yet(self):
        self.assertEqual(R.validate_trips(b""), ([], None))

    def test_refuses_anything_else(self):
        good = synth_trip()
        line = lambda t: jl([t])  # noqa: E731
        cases = {
            "trips_no_final_newline": json.dumps(good).encode(),
            "trips_blank_line:line2": jl([good]) + b"\n",
            "trips_bad_json:line1": b"{not json}\n",
            "trips_bad_json:line1 nan": line({**good, "live_buy_price": 1.0}).replace(b"1.0", b"NaN"),
            "trips_bad_json:line1 dup": b'{"trip_id":"a","trip_id":"b"}\n',
            "trips_bad_json:line1 cr": json.dumps(good).encode() + b"\r\n",
            "trips_bad_json:line1 utf8": b"\xff\xfe\n",
            "trips_bad_field:not_object:line1": b"[1,2]\n",
            "trips_bad_field:unknown_key:line1": line({**good, "extra": 1}),
            "trips_bad_field:stake_lamports:line1": line({**good, "stake_lamports": True}),
            "trips_bad_field:stake_lamports:line1 zero": line({**good, "stake_lamports": 0}),
            "trips_bad_field:decision_ms:line1": line({**good, "decision_ms": "1790000000000"}),
            "trips_bad_field:trip_id:line1": line({**good, "trip_id": "a/b"}),
            "trips_bad_field:exit_landed:line1": line({**good, "exit_landed": 1}),
            "trips_bad_field:exit_reason:line1": line({**good, "exit_reason": "a b"}),
            "trips_bad_field:live_net_lamports:line1": line({k: v for k, v in good.items() if k != "live_net_lamports"}),
            "trips_bad_field:live_buy_price:line1 missing": line({k: v for k, v in good.items() if k != "live_buy_price"}),
            "trips_duplicate_id:line2": jl([good, good]),
        }
        for label, raw in cases.items():
            want = label.split(" ")[0]
            rows, why = R.validate_trips(raw)
            self.assertEqual((rows, why), ([], want), label)
        long_line = jl([{**good, "exit_reason": "x" * 9000}])
        self.assertEqual(R.validate_trips(long_line)[1], "trips_line_too_long:line1")
        self.assertEqual(R.validate_trips(b"x" * 101, max_bytes=100), ([], "trips_too_large"))


# ---------------------------------------------------------------------------------------------------------------------------------- serve
class ServeTests(Tmp):
    def test_forwarded_argv_is_exactly_the_flags_plus_the_private_tmp_files(self):
        seen = []

        def spy(argv):
            seen.append(list(argv))
            work = Path(dict(a.split("=", 1) for a in argv)["--trips"]).parent
            self.assertEqual(stat.S_IMODE(work.stat().st_mode), 0o700)  # private
            self.assertEqual(Path(work / "trips.jsonl").read_bytes(), jl([synth_trip()]))  # the stdin bytes, verbatim
            return fake_twin_main(argv)

        rep = self.serve(jl([synth_trip()]), twin_main=spy)
        self.assertTrue(rep["ok"], rep)
        argv = seen[0]
        work = Path(argv[0].split("=", 1)[1]).parent
        self.assertEqual(argv, [f"--trips={work}/trips.jsonl", f"--ledger-dir={self.ledger}", f"--bot-id={ARM}", f"--out={work}/twins.jsonl",
                                f"--run-record={work}/twins.jsonl.run.json", f"--now-ms={NOW}", f"--reads-dir={self.tmp}/reads"])
        self.assertTrue(work.parent == self.tmp and not work.exists(), "the private tmp dir is removed after the request")
        # no reads dir given: the flag is not forwarded at all
        seen.clear()
        self.serve(b"", twin_main=spy, reads_dir=None)
        self.assertFalse(any(a.startswith("--reads-dir") for a in seen[0]))

    def test_the_real_wrappers_parser_takes_exactly_these_args(self):
        from tools import hv4_c0_twin as tw
        argv = []
        self.serve(b"", twin_main=lambda a: argv.extend(a) or 0)
        ns = tw._parser().parse_args(argv)  # strict: unknown or repeated options would have exited
        self.assertEqual((ns.bot_id, ns.ledger_dir, ns.now_ms, ns.reads_dir), (ARM, self.ledger, NOW, str(self.tmp / "reads")))

    def test_ok_reply_is_one_line_and_carries_both_files_tagged(self):
        out = io.BytesIO()
        raw = jl([synth_trip("MINT1"), synth_trip("MINT2")])
        rc = R.serve_main(["--from-env", "--bot-id", ARM, "--ledger-dir", self.ledger, "--tmp-root", str(self.tmp)], stdin=io.BytesIO(raw), stdout=out,
                          environ={"SSH_ORIGINAL_COMMAND": f"twin {NOW}"}, twin_main=fake_twin_main, clock_ms=lambda: NOW)
        self.assertEqual(rc, 0)
        data = out.getvalue()
        self.assertEqual(data.count(b"\n"), 1, "the wrapper's chatter must not reach stdout")
        self.assertNotIn(b"NOISE", data)
        obj = R.parse_reply(data)
        twins, run = R.check_reply(obj, bot_id=ARM, req_ms=NOW, trips_raw=raw, trip_ids=["MINT1", "MINT2"], pins=PINS)
        self.assertEqual(hashlib.sha256(twins).hexdigest(), obj["twins"]["sha256"])
        self.assertEqual(hashlib.sha256(run).hexdigest(), obj["run"]["sha256"])
        self.assertEqual(len(twins.splitlines()), 2)

    def test_stdin_is_bounded(self):
        calls = []
        big = io.BytesIO(b"x" * 5000)
        rep = R.serve_once(big, f"twin {NOW}", bot_id=ARM, ledger_dir=self.ledger, reads_dir=None, max_trips_bytes=1000, twin_main=lambda a: calls.append(a) or 0, clock_ms=lambda: NOW)
        self.assertEqual(rep, R._refusal_reply("trips_too_large"))
        self.assertEqual(calls, [])
        self.assertLessEqual(big.tell(), 1001, "serve must not read more than the bound plus one byte")
        # the CLI refuses a bound above the compiled maximum
        out = io.BytesIO()
        rc = R.serve_main(["--now-ms", str(NOW), "--bot-id", ARM, "--ledger-dir", self.ledger, "--max-trips-bytes", str(R.MAX_TRIPS_BYTES + 1)], stdin=io.BytesIO(b""), stdout=out)
        self.assertEqual((rc, json.loads(out.getvalue())["refused"]), (3, "bad_arguments"))

    def test_a_malformed_trip_line_is_refused_and_the_twin_is_never_run(self):
        calls = []
        for raw in (b"garbage\n", jl([{**synth_trip(), "extra": 1}]), jl([synth_trip()]) + b"{}\n", jl([synth_trip()]).rstrip(b"\n")):
            rep = self.serve(raw, twin_main=lambda a: calls.append(a) or 0)
            self.assertFalse(rep["ok"])
            self.assertTrue(rep["refused"].startswith("trips_"), rep)
        self.assertEqual(calls, [])

    def test_request_forms(self):
        for req, code in (("ls /", "bad_request"), ("twin", "bad_request"), ("twin 123", "bad_request"), (f"twin {NOW} ; id", "bad_request"), (None, "bad_request"),
                          (f"twin {NOW + 16_000}", "request_clock_skew"), (f"twin {NOW - 16_000}", "request_clock_skew")):
            rep = R.serve_once(io.BytesIO(b""), req, bot_id=ARM, ledger_dir=self.ledger, reads_dir=None, twin_main=lambda a: self.fail("must not run"), clock_ms=lambda: NOW)
            self.assertEqual(rep["refused"], code, req)
        # both or neither source of the instant
        for argv in (["--from-env", "--now-ms", str(NOW)], []):
            out = io.BytesIO()
            R.serve_main([*argv, "--bot-id", ARM, "--ledger-dir", self.ledger], stdin=io.BytesIO(b""), stdout=out, environ={"SSH_ORIGINAL_COMMAND": f"twin {NOW}"})
            self.assertEqual(json.loads(out.getvalue())["refused"], "bad_arguments")
        # an arm that is not a canary candidate is refused before anything is read
        rep = R.serve_once(io.BytesIO(b""), f"twin {NOW}", bot_id="hv4-CON-L", ledger_dir=self.ledger, reads_dir=None, twin_main=lambda a: self.fail("must not run"), clock_ms=lambda: NOW)
        self.assertEqual(rep["refused"], "arm_not_canary_candidate")

    def test_wrapper_refusals_are_reduced_to_codes_and_leak_no_text(self):
        def refuse(text, rc=2):
            def f(argv):
                print(text, file=sys.stderr)
                return rc
            return f
        cases = [
            ("hv4_c0_twin: REFUSED ledger_dir_sealed:read_record_missing", "twin_refused:ledger_dir_sealed:read_record_missing"),
            ("hv4_c0_twin: REFUSED ledger:trades-2026-10-30.jsonl:file_is_symlink", "twin_refused:ledger:file_is_symlink"),
            ("hv4_c0_twin: REFUSED fee_fields_missing:SECRETMINT", "twin_refused:fee_fields_missing"),
            ("hv4_c0_twin: REFUSED write:/tmp/x/twins.jsonl:file_is_symlink", "twin_refused:write:file_is_symlink"),
            ("hv4_c0_twin: REFUSED ledger_dir_not_this_bot", "twin_refused:ledger_dir_not_this_bot"),
            ("hv4_c0_twin: REFUSED unusable arguments (--x given twice)", "twin_refused:bad_arguments"),
            ("arena_twin_writer: UNUSABLE INPUT, nothing written: ledger row 'p-SECRETMINT-s250' stack hash abc is not the pinned", "twin_unusable_input"),
            ("something else entirely", "twin_rc_2"),
        ]
        for text, code in cases:
            rep = self.serve(jl([synth_trip()]), twin_main=refuse(text))
            self.assertEqual(rep, R._refusal_reply(code), text)
            self.assertNotIn("SECRET", json.dumps(rep))
        self.assertEqual(self.serve(b"", twin_main=lambda a: (_ for _ in ()).throw(RuntimeError("boom SECRET")))["refused"], "twin_crashed")
        self.assertEqual(self.serve(b"", twin_main=lambda a: (_ for _ in ()).throw(ImportError("numpy")))["refused"], "twin_import_failed")
        self.assertEqual(self.serve(b"", twin_main=lambda a: sys.exit(2))["refused"], "twin_rc_2")

    def test_an_invalid_wrapper_output_is_not_forwarded(self):
        def wrong_ms(argv):
            o = dict(a.split("=", 1) for a in argv)
            fake_twin_main([a if not a.startswith("--now-ms") else f"--now-ms={int(o['--now-ms']) - 1}" for a in argv])
            return 0
        self.assertEqual(self.serve(jl([synth_trip()]), twin_main=wrong_ms)["refused"], "twin_output_invalid:run_ms_mismatch")
        self.assertEqual(self.serve(b"", twin_main=lambda a: 0)["refused"], "twin_output_missing")

    def test_deadline_turns_a_hang_into_a_refusal_and_cleans_up(self):
        out = io.BytesIO()
        self.addCleanup(signal.signal, signal.SIGALRM, signal.SIG_DFL)
        t0 = time.monotonic()
        rc = R.serve_main(["--now-ms", str(NOW), "--bot-id", ARM, "--ledger-dir", self.ledger, "--tmp-root", str(self.tmp), "--deadline-s", "1"], stdin=io.BytesIO(b""), stdout=out,
                          twin_main=lambda a: time.sleep(10) or 0, clock_ms=lambda: NOW, use_alarm=True)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual((rc, json.loads(out.getvalue())["refused"]), (3, "serve_timeout"))
        self.assertEqual(list(self.tmp.glob("hv4-c0-twin-relay-*")), [])


# ---------------------------------------------------------------------------------------------------------------------------------- the real wrapper (synthetic ledger outside the arena)
class RealWrapperTests(Tmp):
    def _ledger(self):
        """A synthetic ledger dir for ARM outside /data/mal/arena: one paper row for MINT1 whose stack block hashes to the registered C0 pin."""
        row = ledger_row()
        row["stack"] = dict(c0.HV4_STACK_PIN, sps_entry=0.4, sps_exit=0.4)
        row["bot_id"] = ARM
        d = Path(self.ledger)
        d.mkdir(parents=True)
        (d / f"trades-{W._day(DEC)}.jsonl").write_text(json.dumps(row) + "\n")
        return d

    def test_refusal_path_proves_the_trips_and_ledger_args_are_forwarded(self):
        self._ledger()
        # trips: only the file serve wrote can produce THIS refusal (a trip without the fees sent)
        rep = self.serve(jl([trip()]), twin_main=None)
        self.assertEqual(rep, R._refusal_reply("twin_refused:fee_fields_missing"))
        # ledger dir: only the forwarded --ledger-dir can produce THIS one (a symlink entry in it)
        (Path(self.ledger) / "trades-2026-10-31.jsonl").symlink_to(self.tmp / "elsewhere")
        rep = self.serve(jl([synth_trip()]), twin_main=None)
        self.assertEqual(rep, R._refusal_reply("twin_refused:ledger:file_is_symlink"))
        # bot id: the arm flag reaches the wrapper's own checks (this ledger holds ARM rows, a different F/X arm is not this dir's bot)
        rep = self.serve(jl([synth_trip()]), twin_main=None, bot_id="hv4-BIG-X", ledger_dir=self.ledger)
        self.assertFalse(rep["ok"])

    def test_sealed_ledger_is_not_opened_without_a_recorded_pass(self):
        sealed = f"{c0.SEALED_ROOT_PREFIX}/ledger/{ARM}"  # a string for the wrapper's path check; the refusals below happen before any open
        reads = self.tmp / "reads"
        reads.mkdir()
        for kw, code in (({"reads_dir": None}, "twin_refused:ledger_dir_sealed:read_record_dir_missing"),
                         ({"reads_dir": str(reads)}, "twin_refused:ledger_dir_sealed:read_record_dir_not_canonical"),
                         ({"reads_dir": str(reads), "ledger_dir": f"{c0.SEALED_ROOT_PREFIX}/ledger/hv4-BIG-X"}, "twin_refused:ledger_dir_not_this_bot")):
            rep = self.serve(jl([synth_trip()]), twin_main=None, **{"ledger_dir": sealed, **kw})
            self.assertEqual(rep, R._refusal_reply(code), kw)

    def test_end_to_end_with_the_real_writer_and_the_bridges_own_gate_functions(self):
        self._ledger()
        raw = jl([synth_trip("MINT1")])
        with mock.patch.object(R, "run_bounded", self._in_process(None)):
            relay = R.Relay(bot_id=ARM, trips_file=str(self._write_trips(raw)), twins_file=self.twins, cmd=["x", "{request}"], clock_ms=lambda: NOW)
            self.assertTrue(relay.cycle(), relay.last_code)
        self.assertIsNone(twin_pin_problem(self.twins, *PINS))
        self.assertEqual(twin_run_stamp(self.twins, ["MINT1"]), NOW)
        self.assertEqual(load_twin_ids(self.twins, priced_only=True), {"MINT1"})
        self.assertEqual(json.loads(Path(self.twins).read_text().splitlines()[0])["bot_id"], ARM)

    def _write_trips(self, raw):
        self.trips.write_bytes(raw)
        return self.trips

    def _in_process(self, twin_main):
        def fake_run(argv, data, timeout_s, max_out, stop=None):
            req = argv[-1]
            rep = R.serve_once(io.BytesIO(data), req, bot_id=ARM, ledger_dir=self.ledger, reads_dir=None, tmp_root=str(self.tmp), twin_main=twin_main, clock_ms=lambda: NOW)
            return (0 if rep.get("ok") else 3), json.dumps(rep).encode() + b"\n", b"", None
        return fake_run

    def test_forced_command_script_end_to_end(self):
        """The real file as a script, SSH_ORIGINAL_COMMAND as the only request channel, the real wrapper and writer, one line on stdout."""
        self._ledger()
        now = int(time.time() * 1000)
        env = {**os.environ, "PYTHONPATH": str(REPO), "SSH_ORIGINAL_COMMAND": f"twin {now}"}
        cmd = [sys.executable, str(REPO / "tools" / "hv4_c0_twin_relay.py"), "serve", "--from-env", "--bot-id", ARM, "--ledger-dir", self.ledger, "--tmp-root", str(self.tmp)]
        raw = jl([synth_trip("MINT1")])
        p = subprocess.run(cmd, input=raw, capture_output=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        self.assertEqual(p.stdout.count(b"\n"), 1)
        twins, run = R.check_reply(R.parse_reply(p.stdout), bot_id=ARM, req_ms=now, trips_raw=raw, trip_ids=["MINT1"], pins=PINS)
        self.assertEqual(json.loads(run)["twin_rows"], 1)
        self.assertEqual(json.loads(twins.splitlines()[0])["trip_id"], "MINT1")
        # a command the forced line was not made for
        p = subprocess.run(cmd, input=b"", capture_output=True, env={**env, "SSH_ORIGINAL_COMMAND": "cat /etc/hostname"}, timeout=60)
        self.assertEqual((p.returncode, json.loads(p.stdout)["refused"]), (3, "bad_request"))
        # and a malformed trip never reaches the writer
        p = subprocess.run(cmd, input=b"nope\n", capture_output=True, env=env, timeout=60)
        self.assertEqual(json.loads(p.stdout)["refused"], "trips_bad_json:line1")


# ---------------------------------------------------------------------------------------------------------------------------------- client
class ClientTests(Tmp):
    def relay(self, **kw) -> R.Relay:
        args = dict(bot_id=ARM, trips_file=str(self.trips), twins_file=self.twins, cmd=["server", "{request}"], clock_ms=lambda: NOW)
        args.update(kw)
        r = R.Relay(**args)
        self.addCleanup(r.close)
        return r

    def fake_server(self, mutate=None, twin_main=fake_twin_main):
        """A run_bounded stand-in: the real serve_once in-process, optionally corrupting the reply line."""
        def run(argv, data, timeout_s, max_out, stop=None):
            rep = R.serve_once(io.BytesIO(data), argv[-1], bot_id=ARM, ledger_dir=self.ledger, reads_dir=None, tmp_root=str(self.tmp), twin_main=twin_main, clock_ms=lambda: NOW)
            if mutate:
                rep = mutate(rep, data)
            line = rep if isinstance(rep, bytes) else json.dumps(rep, sort_keys=True).encode() + b"\n"
            return 0, line, b"", None
        return run

    def good_pair(self, trips=(synth_trip("MINT1"),)):
        self.trips.write_bytes(jl(trips))
        r = self.relay()
        with mock.patch.object(R, "run_bounded", self.fake_server()):
            self.assertTrue(r.cycle(), r.last_code)
        return r, Path(self.twins).read_bytes(), Path(self.twins + ".run.json").read_bytes()

    def assert_untouched(self, twins, run):
        self.assertEqual(Path(self.twins).read_bytes(), twins)
        self.assertEqual(Path(self.twins + ".run.json").read_bytes(), run)
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.endswith(".tmp") and p.name.startswith(".")], [])

    def test_a_verified_reply_installs_both_files_atomically_and_writes_status(self):
        r, twins, run = self.good_pair()
        for p in (self.twins, self.twins + ".run.json"):
            ls = os.lstat(p)
            self.assertTrue(stat.S_ISREG(ls.st_mode) and ls.st_nlink == 1)
        self.assertIsNone(twin_pin_problem(self.twins, *PINS))
        self.assertEqual(twin_run_stamp(self.twins, ["MINT1"]), NOW)
        st = json.loads(Path(self.twins + ".relay-status.json").read_text())
        self.assertEqual((st["schema"], st["state"], st["last_code"], st["last_run_ms"], st["twin_rows"], st["covered"]), (R.STATUS_SCHEMA, "ok", "ok", NOW, 1, 1))
        self.assertEqual(st["pid"], os.getpid())

    def test_old_twins_are_kept_on_every_kind_of_refusal_and_bad_reply(self):
        r, twins, run = self.good_pair()
        good = lambda rep, data: rep  # noqa: E731

        def with_(**over):
            return lambda rep, data: {**rep, **over}

        def blob(name, **over):
            return lambda rep, data: {**rep, name: {**rep[name], **over}}

        def retag(rep, data):  # tamper the twins bytes AND re-tag them: the run record's twins_sha256 still disagrees
            b = base64.b64encode(b'{"trip_id":"MINT1","pnl_lamports":999.0}\n')
            return {**rep, "twins": {"len": 41, "sha256": hashlib.sha256(base64.b64decode(b)).hexdigest(), "b64": b.decode()}}

        def edit_run(**over):
            def f(rep, data):
                rec = {**json.loads(base64.b64decode(rep["run"]["b64"])), **over}
                b = (json.dumps(rec, sort_keys=True) + "\n").encode()
                return {**rep, "run": {"len": len(b), "sha256": hashlib.sha256(b).hexdigest(), "b64": base64.b64encode(b).decode()}}
            return f

        def extra_row(rep, data):  # a twin row for a trip that was never sent, tagged consistently with a matching run record
            body = base64.b64decode(rep["twins"]["b64"]) + b'{"trip_id":"OTHER","pnl_lamports":1.0}\n'
            rec = {**json.loads(base64.b64decode(rep["run"]["b64"])), "twins_sha256": hashlib.sha256(body).hexdigest(), "twin_rows": 2}
            rb = (json.dumps(rec, sort_keys=True) + "\n").encode()
            return {**rep, "twins": {"len": len(body), "sha256": hashlib.sha256(body).hexdigest(), "b64": base64.b64encode(body).decode()},
                    "run": {"len": len(rb), "sha256": hashlib.sha256(rb).hexdigest(), "b64": base64.b64encode(rb).decode()}}

        flip = lambda rep, data: {**rep, "twins": {**rep["twins"], "b64": base64.b64encode(b"X" + base64.b64decode(rep["twins"]["b64"])[1:]).decode()}}  # noqa: E731
        cases = {
            "twins bytes flipped, tags kept": (flip, "reply_twins_sha"),
            "twins re-tagged, run record disagrees": (retag, "reply_twins_sha_mismatch"),
            "twin row for an unsent trip": (extra_row, "reply_twins_row_invalid"),
            "stale replay (run_ms of an older request)": (edit_run(run_ms=NOW - 10_000), "reply_run_ms_mismatch"),
            "covered ids differ": (edit_run(covered_trip_ids=["MINT1", "MINT2"]), "reply_covered_mismatch"),
            "wrong config pin": (edit_run(config_hash="0" * 64), "reply_pin_mismatch"),
            "wrong code pin": (edit_run(code_sha256="0" * 64), "reply_pin_mismatch"),
            "wrong run schema": (edit_run(schema="x"), "reply_run_record_schema"),
            "twin_rows lies": (edit_run(twin_rows=5), "reply_twin_rows_mismatch"),
            "other bot": (with_(bot_id="hv4-BIG-X"), "reply_bot_mismatch"),
            "other request instant": (with_(now_ms=NOW - 1), "reply_now_mismatch"),
            "other request bytes": (with_(req_sha256="0" * 64), "reply_request_mismatch"),
            "not base64": (blob("twins", b64="!!!!"), "reply_twins_base64"),
            "length lies": (blob("run", len=1), "reply_run_length"),
            "missing blob": (lambda rep, data: {k: v for k, v in rep.items() if k != "twins"}, "reply_twins_shape"),
            "wrong schema": (with_(schema="other"), "reply_schema"),
            "ok is not a bool": (with_(ok="yes"), "reply_schema"),
            "two lines": (lambda rep, data: json.dumps(rep).encode() + b"\n" + json.dumps(rep).encode() + b"\n", "reply_not_one_line"),
            "truncated": (lambda rep, data: json.dumps(rep).encode()[:-5], "reply_truncated"),
            "garbage": (lambda rep, data: b"Welcome to the host\n", "reply_unreadable"),
            "NaN constant": (lambda rep, data: json.dumps({**rep, "x": 1}).replace("1}", "NaN}").encode() + b"\n", "reply_unreadable"),
            "remote refusal": (lambda rep, data: {"schema": R.REPLY_SCHEMA, "ok": False, "refused": "twin_refused:ledger_dir_sealed:read_record_missing"},
                               "twin_refused:ledger_dir_sealed:read_record_missing"),
            "refusal with a free-text code": (lambda rep, data: {"schema": R.REPLY_SCHEMA, "ok": False, "refused": "Traceback (most recent call last)"}, "reply_bad_refusal"),
        }
        self.trips.write_bytes(jl([synth_trip("MINT1")]))
        for label, (mutate, code) in cases.items():
            with mock.patch.object(R, "run_bounded", self.fake_server(mutate=mutate)):
                self.assertFalse(r.cycle(), label)
            self.assertEqual(r.last_code, code, label)
            self.assert_untouched(twins, run)
        self.assertEqual(r.last_state, "refused")  # the last case was a (bad) refusal
        # transport failures, a bad local trips file
        for fake, code in ((lambda *a, **k: (None, b"", b"", "timeout"), "ssh_timeout"), (lambda *a, **k: (255, b"", b"Permission denied (publickey).\n", None), "ssh_rc_255"),
                           (lambda *a, **k: (0, b"", b"", None), "reply_empty"), (lambda *a, **k: (None, b"", b"", "reply_too_large"), "ssh_reply_too_large"),
                           (lambda *a, **k: (None, b"", b"", "spawn_failed"), "ssh_spawn_failed")):
            with mock.patch.object(R, "run_bounded", fake):
                self.assertFalse(r.cycle())
            self.assertEqual(r.last_code, code)
            self.assert_untouched(twins, run)
        with mock.patch.object(R, "run_bounded", lambda *a, **k: (255, b"", b"Permission denied (publickey).\n", None)):
            r.cycle()
        self.assertIn("Permission denied", json.loads(Path(self.twins + ".relay-status.json").read_text())["detail"])  # advisory text from the remote's stderr, printable ASCII only
        called = []
        with mock.patch.object(R, "run_bounded", lambda *a, **k: called.append(a)):
            for raw, code in ((b"garbage\n", "trips_bad_json:line1"), (b"x" * (R.MAX_TRIPS_BYTES + 1), "trips_too_large")):
                self.trips.write_bytes(raw)
                self.assertFalse(r.cycle())
                self.assertEqual(r.last_code, code)
            self.trips.unlink()
            self.assertFalse(r.cycle())
            self.assertEqual(r.last_code, "trips_missing")
        self.assertEqual(called, [], "an invalid or oversized local trips file is never sent")
        self.assert_untouched(twins, run)
        # and a good cycle after all that still works and replaces the files
        self.trips.write_bytes(jl([synth_trip("MINT1"), synth_trip("MINT2")]))
        with mock.patch.object(R, "run_bounded", self.fake_server()):
            self.assertTrue(r.cycle())
        self.assertEqual(r.failures, 0)
        self.assertEqual(json.loads(Path(self.twins + ".run.json").read_text())["covered_trip_ids"], ["MINT1", "MINT2"])

    def test_a_new_trip_is_uncovered_until_the_next_cycle_then_the_twin_stamp_is_good_again(self):
        """With the bridge's own recorder and parity state: a landed buy makes the stamp None (P7, no buy) until a cycle that carries the new trip lands."""
        rec = ParityRecorder(self.tmp / "parity")
        buy = dict(landed=True, tokens_received=1000, spend_lamports=50_000_000, signature="sigA", decision_t_ms=NOW - 20_000, confirm_seen_ms=NOW - 18_000, fee_lamports=60_000,
                   expected_tokens=1000, tokens_source="tx")
        rec.on_buy("MintA", buy, NOW - 18_000, NOW + 300_000)
        clock = {"now": NOW}
        r = self.relay(trips_file=str(rec.trips_path), clock_ms=lambda: clock["now"])

        def p7(now):
            st = c0p.parity_state(now, loss_ok=True, balance_ms=now, twins_file=self.twins, trip_ids=rec.trips, chain_ok=True, control_ok=True, hard_halts=())
            st["last_landed_ms"] = rec.last_landed_ms
            return [x for x in alp.state_problems(st, now) if x.startswith("twin")], st
        self.assertEqual(p7(NOW)[0], ["twin missing"], "no pair yet: no stamp")
        with mock.patch.object(R, "run_bounded", self.fake_server()):
            self.assertTrue(r.cycle())
            self.assertEqual(p7(NOW + 1000)[0], [])
            clock["now"] = NOW + 5000
            rec.on_buy("MintB", dict(buy, signature="sigB", confirm_seen_ms=NOW + 4000), NOW + 4000, NOW + 300_000)  # a second buy lands
            self.assertEqual(p7(NOW + 5001)[0], ["twin missing"], "the new trip is not covered: no stamp, so P7 and no buy")
            clock["now"] = NOW + 6000
            self.assertTrue(r.cycle())
            self.assertEqual(p7(NOW + 6500)[0], [])
            # an exit alone (no new trip): the stamp predates the landed tx until a cycle that started after it lands
            rec.on_sell("MintA", dict(landed=True, pnl_lamports=5, tokens=1000, sol_received_lamports=50_000_005, confirm_seen_ms=NOW + 7000, exit_reason="timer", fee_lamports=60_000), NOW + 7000)
            self.assertEqual(p7(NOW + 7001)[0], ["twin older than the last landed tx"])
            clock["now"] = NOW + 7500
            self.assertTrue(r.cycle())
            self.assertEqual(p7(NOW + 8000)[0], [])

    def test_install_refuses_links_and_irregular_targets_and_touches_nothing(self):
        self.trips.write_bytes(jl([synth_trip()]))
        other = self.tmp / "other"
        other.write_text("keep")
        r = self.relay()
        os.symlink(other, self.twins)
        with mock.patch.object(R, "run_bounded", self.fake_server()):
            self.assertFalse(r.cycle())
        self.assertEqual((r.last_code, other.read_text()), ("install_failed:target_not_regular", "keep"))
        os.unlink(self.twins)
        Path(self.twins).write_text("old")
        os.link(self.twins, self.tmp / "alias")
        with mock.patch.object(R, "run_bounded", self.fake_server()):
            self.assertFalse(r.cycle())
        self.assertEqual((r.last_code, Path(self.twins).read_text()), ("install_failed:target_hard_linked", "old"))
        os.unlink(self.tmp / "alias")
        os.unlink(self.twins)
        os.mkdir(self.twins + ".run.json")  # the record path is a directory
        with mock.patch.object(R, "run_bounded", self.fake_server()):
            self.assertFalse(r.cycle())
        self.assertEqual(r.last_code, "install_failed:target_not_regular")
        self.assertFalse(Path(self.twins).exists(), "the first file is not installed when the pair cannot be")

    def test_interval_and_timeout_bounds_and_the_stale_window(self):
        for kw in ({"interval_s": 20.5}, {"interval_s": 21}, {"interval_s": 0.5}, {"timeout_s": 25.5}, {"timeout_s": 0.5}):
            with self.assertRaises(ValueError, msg=str(kw)):
                self.relay(**kw)
        self.relay(interval_s=20, timeout_s=25).close()
        # the CLI says no too, before anything is touched
        base = ["client", "--bot-id", ARM, "--trips-file", str(self.trips), "--twins-file", self.twins, "--cmd-json", '["x","{request}"]']
        with mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                R.main([*base, "--interval-s", "21"])
        self.assertEqual(cm.exception.code, 2)
        # the bound is derived from the parity tool's own window, not a copy
        self.assertEqual(R.STALE_MS, alp.STALE_MS)
        self.assertLessEqual(R.INTERVAL_S_MAX + R.TIMEOUT_S_MAX + R.STALE_MARGIN_S, alp.STALE_MS / 1000)
        # a healthy loop at the loosest allowed settings never trips P7: the stamp (the request instant) is at most interval + timeout old when the next pair lands
        worst = int((R.INTERVAL_S_MAX + R.TIMEOUT_S_MAX) * 1000)
        self._write_twin_on_disk(run_ms=NOW)
        for age_ms, expect_problem in ((worst, False), (alp.STALE_MS, False), (alp.STALE_MS + 1, True)):
            st = c0p.parity_state(NOW + age_ms, loss_ok=True, balance_ms=NOW + age_ms, twins_file=self.twins, trip_ids=["MINT1"], chain_ok=True, control_ok=True, hard_halts=())
            problems = [p for p in alp.state_problems(st, NOW + age_ms) if p.startswith("twin")]
            self.assertEqual(bool(problems), expect_problem, (age_ms, problems))

    def _write_twin_on_disk(self, run_ms):
        argv = [f"--trips={self.trips}", f"--out={self.twins}", f"--run-record={self.twins}.run.json", f"--now-ms={run_ms}", f"--bot-id={ARM}"]
        self.trips.write_bytes(jl([synth_trip("MINT1")]))
        fake_twin_main(argv)

    def test_single_instance_flock(self):
        a = self.relay()
        with self.assertRaises(R.Locked):
            R.Relay(bot_id=ARM, trips_file=str(self.trips), twins_file=self.twins, cmd=["x", "{request}"])
        a.close()
        self.relay().close()  # free again
        # over the CLI: a held lock is exit 3, not a second writer
        holder = self.relay()
        with mock.patch("sys.stderr", io.StringIO()):
            rc = R.main(["client", "--bot-id", ARM, "--trips-file", str(self.trips), "--twins-file", self.twins, "--cmd-json", '["x","{request}"]', "--once"])
        self.assertEqual(rc, 3)
        holder.close()

    def test_loop_cadence_change_trigger_and_failure_backoff(self):
        clock = {"t": 0.0}
        starts = []
        r = self.relay(interval_s=10, min_gap_s=2, poll_s=1, mono=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
        self.trips.write_bytes(jl([synth_trip("MINT1")]))
        server = self.fake_server()

        def run(argv, data, timeout_s, max_out, stop=None):
            starts.append(round(clock["t"], 3))
            return server(argv, data, timeout_s, max_out, stop)

        def tick_sleep(s):
            clock["t"] += s
            if clock["t"] >= 23.5 and not getattr(tick_sleep, "done", False):
                tick_sleep.done = True
                self.trips.write_bytes(jl([synth_trip("MINT1"), synth_trip("MINT2")]))  # a trip lands at ~t=23.5
        r.sleep = tick_sleep
        with mock.patch.object(R, "run_bounded", run):
            r.run(max_s=45)
        self.assertEqual(starts[:3], [0.0, 10.0, 20.0])  # start-to-start every interval
        self.assertTrue(23.5 <= starts[3] <= 25.0, starts)  # the changed trips file is sent within a poll, not at t=30
        self.assertGreaterEqual(starts[3] - starts[2], 2.0)  # never inside min_gap of the previous start
        self.assertEqual(json.loads(Path(self.twins + ".relay-status.json").read_text())["state"], "stopped")
        # failure backoff: sooner than the interval, never later
        for failures, want in ((1, 2.0), (2, 4.0), (3, 8.0), (4, 10.0), (9, 10.0)):
            r.failures = failures
            self.assertEqual(r.delay_after(False), want, failures)
        self.assertEqual(r.delay_after(True), 10.0)

    def test_failed_cycles_retry_sooner_than_the_interval(self):
        clock = {"t": 0.0}
        starts = []
        r = self.relay(interval_s=10, mono=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s), poll_s=0)
        self.trips.write_bytes(jl([synth_trip()]))
        with mock.patch.object(R, "run_bounded", lambda *a, **k: starts.append(clock["t"]) or (255, b"", b"", None)):
            r.run(max_s=30)
        self.assertEqual([round(x, 1) for x in starts][:5], [0.0, 2.0, 6.0, 14.0, 24.0])


# ---------------------------------------------------------------------------------------------------------------------------------- real subprocesses
class SubprocessTests(Tmp):
    def test_run_bounded_timeout_kills_the_process_group(self):
        t0 = time.monotonic()
        rc, out, err, why = R.run_bounded(["sh", "-c", "sleep 30 & echo $! > " + str(self.tmp / "pid") + "; wait"], b"", 1.0, 1000)
        self.assertLess(time.monotonic() - t0, 6)
        self.assertEqual(why, "timeout")
        time.sleep(0.2)
        pid = int((self.tmp / "pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_run_bounded_caps_the_reply_and_feeds_stdin(self):
        rc, out, err, why = R.run_bounded([sys.executable, "-c", "import sys; sys.stdout.write('x' * 3_000_000)"], b"", 10, 100_000)
        self.assertEqual(why, "reply_too_large")
        self.assertLessEqual(len(out), 100_000 + 65536)
        rc, out, err, why = R.run_bounded([sys.executable, "-c", "import sys; d = sys.stdin.buffer.read(); sys.stdout.write(str(len(d))); sys.stderr.write('E' * 5000)"], b"y" * 700_000, 10, 1000)
        self.assertEqual((rc, out, why, len(err)), (0, b"700000", None, 2048))

    def test_cli_client_once_through_a_forced_command_shaped_stub(self):
        """The client process, the env-carried request, serve_main --from-env and the file install, as two real processes."""
        self.trips.write_bytes(jl([synth_trip("MINT1"), synth_trip("NOTWIN1")]))
        cmd = ["env", "SSH_ORIGINAL_COMMAND={request}", sys.executable, "-c", STUB, str(REPO), "--from-env", "--bot-id", ARM, "--ledger-dir", self.ledger, "--tmp-root", str(self.tmp)]
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        p = subprocess.run([sys.executable, str(REPO / "tools" / "hv4_c0_twin_relay.py"), "client", "--bot-id", ARM, "--trips-file", str(self.trips), "--twins-file", self.twins,
                            "--cmd-json", json.dumps(cmd), "--once", "--timeout-s", "20"], capture_output=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        now_stamp = twin_run_stamp(self.twins, ["MINT1", "NOTWIN1"])
        self.assertIsNotNone(now_stamp)
        self.assertLess(abs(now_stamp - time.time() * 1000), 60_000)
        self.assertEqual(load_twin_ids(self.twins), {"MINT1"})  # NOTWIN1 is covered but has no twin row: the gate's `past_exit_untwinned` / `open_untwinned_cap_1` case
        # a standing refusal leaves the old pair in place
        before = Path(self.twins).read_bytes()
        bad = ["env", "SSH_ORIGINAL_COMMAND=ls", "UNUSED={request}", sys.executable, "-c", STUB, str(REPO), "--from-env", "--bot-id", ARM, "--ledger-dir", self.ledger]
        p = subprocess.run([sys.executable, str(REPO / "tools" / "hv4_c0_twin_relay.py"), "client", "--bot-id", ARM, "--trips-file", str(self.trips), "--twins-file", self.twins,
                            "--cmd-json", json.dumps(bad), "--once"], capture_output=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(Path(self.twins).read_bytes(), before)
        st = json.loads(Path(self.twins + ".relay-status.json").read_text())
        self.assertEqual((st["state"], st["last_code"]), ("refused", "bad_request"))

    def test_sigterm_ends_the_loop_with_status_stopped(self):
        self.trips.write_bytes(jl([synth_trip("MINT1")]))
        cmd = ["env", "SSH_ORIGINAL_COMMAND={request}", sys.executable, "-c", STUB, str(REPO), "--from-env", "--bot-id", ARM, "--ledger-dir", self.ledger, "--tmp-root", str(self.tmp)]
        p = subprocess.Popen([sys.executable, str(REPO / "tools" / "hv4_c0_twin_relay.py"), "client", "--bot-id", ARM, "--trips-file", str(self.trips), "--twins-file", self.twins,
                              "--cmd-json", json.dumps(cmd)], env={**os.environ, "PYTHONPATH": str(REPO)}, stderr=subprocess.PIPE)
        st_path = Path(self.twins + ".relay-status.json")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if st_path.exists() and json.loads(st_path.read_text()).get("state") == "ok":
                break
            time.sleep(0.2)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=30), 0)
        self.assertEqual(json.loads(st_path.read_text())["state"], "stopped")
        self.assertIsNone(twin_pin_problem(self.twins, *PINS))


class PinTests(unittest.TestCase):
    def test_constants_that_mirror_other_modules(self):
        self.assertEqual(R.SSH_OPTS, df.SSH_OPTS)
        self.assertEqual(R.RUN_SCHEMA, W.RUN_SCHEMA)
        self.assertEqual(R.SEALED_ROOT_PREFIX, c0.SEALED_ROOT_PREFIX)
        self.assertEqual(R.STALE_MS, alp.STALE_MS)

    def test_the_client_module_needs_no_numpy_or_the_writer(self):
        """The client side runs on fast-0: importing the relay must not pull in the twin wrapper or numpy."""
        code = "import sys; from tools import hv4_c0_twin_relay as R; R._c0(); bad = [m for m in ('numpy', 'tools.hv4_c0_twin', 'tools.arena_twin_writer') if m in sys.modules]; print(bad)"
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, env={**os.environ, "PYTHONPATH": str(REPO)}, cwd=str(REPO), timeout=60)
        self.assertEqual(p.stdout.strip(), b"[]", p.stderr)


if __name__ == "__main__":
    unittest.main()
