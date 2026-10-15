import copy
import unittest

from agentlab.domain import BusinessError, TOOLS, invoke, seed_state


def creation(**changes):
    return {"title": "打印机无法打印", "description": "打印机一直卡纸无法打印", "category": "hardware",
            "contact": "13800138000", "idempotency_key": "request-1", **changes}


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.state = seed_state()

    def error(self, name, args, code):
        with self.assertRaises(BusinessError) as raised:
            invoke(self.state, name, args)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_required_unknown_and_wrong_type_fields(self):
        for args in ({}, creation(customer_id="customer-other"), creation(contact="123"),
                     creation(title=" "), creation(description=None), creation(category="delete")):
            with self.subTest(args=args):
                original = copy.deepcopy(self.state)
                self.error("ticket.create", args, "VALIDATION_ERROR")
                self.assertEqual(self.state, original)
        self.error("ticket.list", {"page": True}, "VALIDATION_ERROR")
        self.error("ticket.list", {"page_size": 100}, "VALIDATION_ERROR")
        self.error("ticket.list", [], "VALIDATION_ERROR")
        self.error("ticket.delete", {}, "UNKNOWN_TOOL")

    def test_schema_is_strict_for_every_tool(self):
        for name in TOOLS:
            with self.subTest(name=name):
                self.error(name, {"customer_id": "customer-other"}, "VALIDATION_ERROR")

    def test_owner_scoping_for_every_ticket_operation(self):
        args_by_tool = {
            "ticket.get": {}, "ticket.update": {"priority": "high"}, "ticket.cancel": {"reason": "重复提交"},
            "ticket.reopen": {"reason": "问题还没有得到解决"}, "comment.add": {"content": "测试留言"},
            "comment.list": {}, "complaint.create": {"reason": "delay", "description": "等待了很久还没处理"},
            "complaint.list": {},
        }
        for name, extra in args_by_tool.items():
            for ticket_id in ("T-9001", "T-9999"):
                with self.subTest(name=name, ticket_id=ticket_id):
                    args = {"ticket_id": ticket_id, **extra}
                    if TOOLS[name]["write"]:
                        args["idempotency_key"] = name + ticket_id
                    self.error(name, args, "NOT_FOUND")

    def test_pagination_and_filter_combine(self):
        first = invoke(self.state, "ticket.list", {})["data"]
        second = invoke(self.state, "ticket.list", {"page": 2})["data"]
        self.assertEqual(first["total"], 5)
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertEqual([t["id"] for t in first["items"] + second["items"]],
                         ["T-1001", "T-1002", "T-1003", "T-1004", "T-1005"])
        filtered = invoke(self.state, "ticket.list", {"category": "billing", "status": "open", "keyword": "扣费"})
        self.assertEqual([t["id"] for t in filtered["data"]["items"]], ["T-1002"])
        self.assertEqual(invoke(self.state, "ticket.list", {"keyword": "不存在的词"})["data"]["total"], 0)

    def test_create_defaults_and_idempotent_replay(self):
        first = invoke(self.state, "ticket.create", creation())
        second = invoke(self.state, "ticket.create", creation())
        self.assertEqual(first, second)
        self.assertEqual(len(self.state["tickets"]), 7)
        self.assertEqual(first["data"]["id"], "T-1006")
        self.assertEqual(first["data"]["priority"], "normal")
        self.error("ticket.create", creation(title="其他问题"), "IDEMPOTENCY_CONFLICT")
        self.error("comment.add", {"ticket_id": "T-1001", "content": "留言", "idempotency_key": "request-1"}, "IDEMPOTENCY_CONFLICT")

    def test_rejected_state_changes_leave_state_intact(self):
        for name, args in [
            ("ticket.cancel", {"ticket_id": "T-1004", "reason": "重复提交"}),
            ("ticket.update", {"ticket_id": "T-1003", "priority": "high"}),
            ("ticket.reopen", {"ticket_id": "T-1001", "reason": "问题还没有得到解决"}),
            ("comment.add", {"ticket_id": "T-1005", "content": "再次检查"}),
            ("complaint.create", {"ticket_id": "T-1005", "reason": "delay", "description": "等待很久依旧没人处理"}),
        ]:
            with self.subTest(name=name):
                original = copy.deepcopy(self.state)
                self.error(name, {**args, "idempotency_key": name}, "INVALID_STATE")
                self.assertEqual(original, self.state)

    def test_mutations_and_duplicate_complaint(self):
        updated = invoke(self.state, "ticket.update", {"ticket_id": "T-1001", "priority": "high", "idempotency_key": "u"})
        self.assertEqual(updated["data"]["priority"], "high")
        self.error("ticket.update", {"ticket_id": "T-1001", "idempotency_key": "empty"}, "VALIDATION_ERROR")
        reopened = invoke(self.state, "ticket.reopen", {"ticket_id": "T-1003", "reason": "问题还没有得到解决", "idempotency_key": "r"})
        self.assertEqual(reopened["data"]["status"], "open")
        invoke(self.state, "comment.add", {"ticket_id": "T-1003", "content": "请再次检查", "idempotency_key": "m"})
        self.assertEqual(len(invoke(self.state, "comment.list", {"ticket_id": "T-1003"})["data"]["items"]), 1)
        args = {"ticket_id": "T-1001", "reason": "delay", "description": "等待很久依旧没人处理", "idempotency_key": "c1"}
        invoke(self.state, "complaint.create", args)
        duplicate = self.error("complaint.create", {**args, "idempotency_key": "c2"}, "DUPLICATE_COMPLAINT")
        self.assertEqual(duplicate.details["complaint_id"], "C-1")
        self.assertEqual(len(invoke(self.state, "complaint.list", {})["data"]["items"]), 1)

    def test_transient_failure_before_commit_can_retry(self):
        self.state["faults"] = [{"tool": "ticket.create", "remaining": 1, "mode": "before", "code": "TEMPORARY_UNAVAILABLE"}]
        error = self.error("ticket.create", creation(), "TEMPORARY_UNAVAILABLE")
        self.assertTrue(error.retryable)
        self.assertEqual(len(self.state["tickets"]), 6)
        self.assertTrue(invoke(self.state, "ticket.create", creation())["ok"])

    def test_lost_response_commits_exactly_once(self):
        self.state["faults"] = [{"tool": "ticket.create", "remaining": 1, "mode": "after", "code": "TIMEOUT"}]
        self.error("ticket.create", creation(), "TIMEOUT")
        self.assertEqual(len(self.state["tickets"]), 7)
        replay = invoke(self.state, "ticket.create", creation())
        self.assertEqual(replay["data"]["id"], "T-1006")
        self.assertEqual(len(self.state["tickets"]), 7)


if __name__ == "__main__":
    unittest.main()
