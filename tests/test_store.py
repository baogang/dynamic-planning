from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from agentlab.store import Store
from tests.test_domain import creation


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "lab.sqlite3")
        self.sid = self.store.create()["session_id"]

    def test_api_requires_open_turn_and_records_errors(self):
        result = self.store.call(self.sid, "ticket.list", {})
        self.assertEqual(result["error"]["code"], "TURN_REQUIRED")
        self.store.user(self.sid, "查看工单")
        self.assertTrue(self.store.call(self.sid, "ticket.list", {})["ok"])
        self.store.say(self.sid, "assistant", "已查到工单")
        result = self.store.call(self.sid, "ticket.create", creation())
        self.assertEqual(result["error"]["code"], "TURN_REQUIRED")
        events = self.store.export(self.sid)["events"]
        self.assertEqual([e["kind"] for e in events], ["tool", "user", "tool", "assistant", "tool"])
        with self.assertRaises(ValueError):
            self.store.say(self.sid, "assistant", "重复回复")

    def test_session_isolation_and_no_implicit_reset(self):
        other = self.store.create("other")["session_id"]
        self.store.user(self.sid, "创建工单")
        self.store.call(self.sid, "ticket.create", creation())
        self.assertEqual(len(self.store.export(self.sid)["state"]["tickets"]), 7)
        self.assertEqual(len(self.store.export(other)["state"]["tickets"]), 6)
        reopened = Store(self.store.path)
        self.assertEqual(len(reopened.export(self.sid)["state"]["tickets"]), 7)

    def test_atomic_idempotency_under_concurrent_requests(self):
        self.store.user(self.sid, "创建工单")
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: self.store.call(self.sid, "ticket.create", creation()), range(16)))
        self.assertTrue(all(response["ok"] for response in responses))
        self.assertEqual({r["data"]["id"] for r in responses}, {"T-1006"})
        snapshot = self.store.export(self.sid)
        self.assertEqual(len(snapshot["state"]["tickets"]), 7)
        self.assertEqual(sum(e.get("mutated", False) for e in snapshot["events"]), 1)

    def test_budget_and_user_deduplication(self):
        sid = self.store.create(config={"max_tools": 1})["session_id"]
        self.store.user(sid, "查询工单")
        repeated = self.store.user(sid, "查询工单")
        self.assertTrue(repeated["already_recorded"])
        with self.assertRaises(ValueError):
            self.store.user(sid, "另一个问题")
        self.assertTrue(self.store.call(sid, "ticket.list", {})["ok"])
        result = self.store.call(sid, "ticket.list", {})
        self.assertEqual(result["error"]["code"], "TOOL_BUDGET_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
