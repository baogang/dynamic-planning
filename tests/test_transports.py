from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentlab.benchmark import ROOT, load_cases, run_suite
from agentlab.http_api import handler_class
from agentlab.store import Store


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "lab.sqlite3")
        self.sid = self.store.create()["session_id"]
        self.env = {**os.environ, "AGENTLAB_DB": str(self.store.path), "AGENTLAB_SESSION_ID": self.sid}

    def cli(self, *args, text=None):
        return subprocess.run([sys.executable, "-m", "agentlab", *args], input=text,
                              capture_output=True, text=True, env=self.env, cwd=ROOT, timeout=15)

    def test_cli_stdin_preserves_quotes_newlines_and_shell_characters(self):
        text = '查询工单\n引号 " 和 $(echo sensitive) 与 `date` 只是文本'
        self.assertEqual(self.cli("user", "--stdin", text=text).returncode, 0)
        result = self.cli("call", "ticket.list", '{"page_size":20}')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["data"]["total"], 5)
        self.assertEqual(self.cli("reply", "--stdin", text="查询完成").returncode, 0)
        self.assertEqual(self.store.conversation(self.sid)[0]["text"], text)

    def test_cli_failure_exit_codes_and_init_preserves_session(self):
        before = self.store.metadata(self.sid)
        result = self.cli("init")
        self.assertEqual(json.loads(result.stdout)["session_id"], before["session_id"])
        self.cli("user", "查询工单")
        invalid = self.cli("call", "ticket.list", '{"page":0}')
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(json.loads(invalid.stdout)["error"]["code"], "VALIDATION_ERROR")
        invalid = self.cli("call", "ticket.list", "not-json")
        self.assertEqual(invalid.returncode, 2)
        report = self.cli("grade")
        self.assertIsNone(json.loads(report.stdout)["passed"])

    def test_http_and_cli_share_business_state_and_audit(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class(self.store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/health", timeout=3) as response:
            self.assertTrue(json.load(response)["ok"])
        with urlopen(base + "/openapi.json", timeout=3) as response:
            self.assertEqual(json.load(response)["openapi"], "3.1.0")
        with urlopen(base + "/tools", timeout=3) as response:
            self.assertEqual(len(json.load(response)["tools"]), 11)
        self.store.user(self.sid, "查询工单")
        def request(body, sid=None):
            return Request(base + "/tools/call", data=body.encode(), method="POST",
                           headers={"Content-Type": "application/json", **({"X-Session-Id": sid} if sid else {})})
        with urlopen(request('{"name":"ticket.list","arguments":{}}', self.sid), timeout=3) as response:
            self.assertEqual(json.load(response)["data"]["total"], 5)
        self.assertEqual(self.store.conversation(self.sid)[-1]["name"], "ticket.list")
        with urlopen(request('{"name":"ticket.get","arguments":{"ticket_id":"T-9001"}}', self.sid), timeout=3) as response:
            self.assertEqual(json.load(response)["error"]["code"], "NOT_FOUND")
        for body in ('{"name":"ticket.list","arguments":{}}', 'not-json', '[]'):
            with self.subTest(body=body), self.assertRaises(HTTPError) as raised:
                urlopen(request(body), timeout=3)
            self.assertEqual(raised.exception.code, 400)

    def test_native_process_runner_uses_real_cli_transport_over_multiple_turns(self):
        case = next(c for c in load_cases() if c["id"] == "create.missing")
        report = run_suite(self.store, [case], [sys.executable, "-m", "tests.native_fixture", "create.missing"],
                           label="canned-transport-fixture-NOT-agent", output=Path(self.tmp.name) / "report.json", repeat=2, timeout=15)
        self.assertTrue(report["complete"])
        self.assertEqual(report["success_rate"], 1)
        self.assertEqual(len(report["results"]), 2)
        self.assertEqual(self.store.current_id(), self.sid)  # Batch mode doesn't replace the live session.
        for result in report["results"]:
            self.assertEqual(result["metrics"]["user_turns"], 2)
            self.assertTrue(Path(result["trace"]).exists())

    def test_native_runner_marks_timeout_failure_and_unrecorded_reply(self):
        case = next(c for c in load_cases() if c["id"] == "unsupported.delete")
        for mode in ("timeout", "fail", "no-reply"):
            with self.subTest(mode=mode):
                report = run_suite(self.store, [case], [sys.executable, "-m", "tests.native_fixture", mode],
                                   label="failure-fixture", output=Path(self.tmp.name) / f"{mode}.json", timeout=0.2 if mode == "timeout" else 15)
                self.assertEqual(report["success_rate"], 0)
                self.assertEqual(report["results"][0]["status"], "execution_error")
                self.assertTrue(report["results"][0]["errors"])

    def test_native_runner_rejects_shell_command_strings(self):
        with self.assertRaises(ValueError):
            run_suite(self.store, load_cases()[:1], "codex exec -", label="invalid", output="unused.json")


if __name__ == "__main__":
    unittest.main()
