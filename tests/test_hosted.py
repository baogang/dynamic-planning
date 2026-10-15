from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentlab.benchmark import ROOT
from agentlab.http_api import handler_class, serve
from agentlab.remote import Remote
from agentlab.store import Store


class HostedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "hosted.sqlite3")
        self.token = "test-api-token-which-is-long-enough-123"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class(self.store, self.token))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.binding = Path(self.tmp.name) / "remote-session.json"
        self.remote = Remote(self.url, self.token, self.binding)

    def test_authentication_protects_sessions_messages_and_business(self):
        for path, body in (("/sessions", {}), ("/messages", {"role": "user", "text": "查询"}),
                           ("/tools/call", {"name": "ticket.list", "arguments": {}})):
            with self.subTest(path=path), self.assertRaises(ValueError) as raised:
                Remote(self.url, "wrong-token", self.binding).request(path, body)
            self.assertIn("HTTP 401", str(raised.exception))
        self.assertFalse(self.store.active_path.exists())
        with urlopen(self.url + "/health", timeout=3) as response:
            self.assertTrue(json.load(response)["ok"])
        with urlopen(self.url, timeout=3) as response:
            self.assertIn("Agent Planning Lab", response.read().decode())

    def test_remote_session_complete_round_trip_and_isolation(self):
        one = self.remote.create("one")["session_id"]
        self.assertEqual(self.remote.session_id(), one)
        two = self.remote.create("two")["session_id"]
        self.assertNotEqual(one, two)
        self.remote.request("/messages", {"role": "user", "text": "撤销工单 T-1002，重复提交"}, one)
        result = self.remote.request("/tools/call", {"name": "ticket.cancel", "arguments": {
            "ticket_id": "T-1002", "reason": "重复提交", "idempotency_key": "hosted-1"}}, one)
        self.assertTrue(result["ok"])
        self.remote.request("/messages", {"role": "assistant", "text": "T-1002 已撤销。"}, one)
        history = self.remote.request("/sessions/history", session_id=one)
        self.assertEqual([e["kind"] for e in history], ["user", "tool", "assistant"])
        self.assertEqual(self.store.export(two)["state"]["tickets"][1]["status"], "open")
        self.assertNotIn(self.token, self.binding.read_text())

    def test_remote_cli_preserves_existing_session_and_records_newlines(self):
        env = {**os.environ, "AGENTLAB_URL": self.url, "AGENTLAB_API_TOKEN": self.token,
               "AGENTLAB_REMOTE_SESSION_FILE": str(self.binding)}
        env.pop("AGENTLAB_SESSION_ID", None)
        def cli(*args, text=None):
            return subprocess.run([sys.executable, "-m", "agentlab", *args], input=text,
                                  env=env, cwd=ROOT, capture_output=True, text=True, timeout=15)
        first = cli("init")
        self.assertEqual(first.returncode, 0, first.stdout)
        sid = json.loads(first.stdout)["session_id"]
        self.assertEqual(json.loads(cli("init").stdout)["session_id"], sid)
        text = '查一下工单\n用户提供的 $(date) 是原文\n'
        self.assertEqual(cli("user", "--stdin", text=text).returncode, 0)
        result = cli("call", "ticket.list", '{"page_size":20}')
        self.assertEqual(json.loads(result.stdout)["data"]["total"], 5)
        self.assertEqual(cli("reply", "--stdin", text="已查到五张工单。\n").returncode, 0)
        self.assertEqual(self.store.conversation(sid)[0]["text"], text.removesuffix("\n"))
        self.assertEqual(cli("bench", "list").returncode, 2)

    def test_malformed_requests_and_turn_validation(self):
        sid = self.remote.create()["session_id"]
        for path, body in (("/sessions", {"label": "", "customer_id": "other"}),
                           ("/messages", {"role": "admin", "text": "reset"}),
                           ("/messages", {"role": "user", "text": []})):
            with self.subTest(path=path), self.assertRaises(ValueError) as raised:
                self.remote.request(path, body, sid)
            self.assertIn("HTTP 400", str(raised.exception))
        response = self.remote.request("/tools/call", {"name": "ticket.list", "arguments": {}}, sid)
        self.assertEqual(response["error"]["code"], "TURN_REQUIRED")
        with self.assertRaises(ValueError):
            self.remote.request("/sessions/current", session_id="unknown")

    def test_public_binding_requires_secret_before_opening_socket(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "AGENTLAB_API_TOKEN"):
                serve(self.store, 0, "0.0.0.0")
        with patch.dict(os.environ, {"AGENTLAB_API_TOKEN": "short"}, clear=True):
            with self.assertRaises(ValueError):
                serve(self.store, 0, "0.0.0.0")

    def test_client_refuses_insecure_hosts_credentials_and_wrong_binding(self):
        for url in ("http://public.example", "https://user:pass@example.com", "https://example.com?token=123"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Remote(url, self.token, self.binding)
        self.remote.create()
        with self.assertRaises(ValueError):
            Remote("https://another.example", self.token, self.binding).session_id()


if __name__ == "__main__":
    unittest.main()
